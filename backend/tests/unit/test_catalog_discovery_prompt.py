"""Промпт предложения семьи для систем и снимок хэшей работы (спека 3б §2.6,
решение 1, DoD 5).

Снимок хэшей работы снят на коде до появления промпта систем: тело запроса
для контекста не-системы не меняется ни байтом, поэтому литералы ниже
переживают любую правку модуля, не трогающую это тело.
"""
from __future__ import annotations

import hashlib

from config import Settings
from services import semantic_request
from services.semantic_request import (
    SYSTEM_PROMPT_VERSION,
    SYSTEM_SEMANTIC_PROMPT,
    CandidateFamily,
    ContextRequestMaterial,
    RequestHasher,
    is_applicable,
    prompt_for,
    render_context_request,
)


def _candidate(id_=1, title="Устройство пола", unit_code="M2", definition="Пол по грунту"):
    return CandidateFamily(id=id_, title=title, unit_code=unit_code, definition=definition)


def _material(**overrides) -> ContextRequestMaterial:
    defaults = dict(
        context_id=1,
        unit_id=10,
        unit_code="M2",
        title="Устройство стяжки пола",
        article="12.03 Полы",
        path_counts=(("Секция 1 / Этаж 2", 5), ("Секция 2 / Этаж 1", 3)),
        member_count=8,
        archived=False,
        semantic_state="SUGGESTED",
        semantic_kind="WORK",
        work_family_id=None,
        candidates=(_candidate(),),
    )
    defaults.update(overrides)
    return ContextRequestMaterial(**defaults)


def _settings(**overrides) -> Settings:
    base = dict(SECRET_KEY="x" * 32)
    base.update(overrides)
    return Settings(_env_file=None, **base)


class TestWorkRequestHashSnapshot:
    """Хэши запроса работы — литералы, снятые на коде до промпта систем."""

    def test_single_family_work_hashes_equal_the_snapshot(self):
        rendered = render_context_request(_material(), settings=_settings())
        assert rendered.request_hash == (
            "874d27aa6549f173f1eadf673bcecfb40c41f883b0d45b2112f4d1847d69b4ab"
        )
        assert rendered.prefix_hash == (
            "824103ce5c9abecde7c1cd75218f2d96e9d4d5ba4089300b0d5f04ba56190ac8"
        )

    def test_two_family_work_hashes_equal_the_snapshot(self):
        material = _material(
            candidates=(
                _candidate(id_=1),
                _candidate(id_=2, title="Другая семья", unit_code=None, definition="Опр"),
            )
        )
        rendered = render_context_request(material, settings=_settings())
        assert rendered.request_hash == (
            "c174ed9b44bcbbc3e99ad07271076f67eefb2b315c4f1a69bb90d65008230f7a"
        )
        assert rendered.prefix_hash == (
            "e44c1819706522c6da5eb18bb99dd2d0d85122b038bdfe88cb1a6e17cbc5834c"
        )


def _system(**overrides) -> ContextRequestMaterial:
    return _material(semantic_kind="SYSTEM", **overrides)


class TestPromptFor:
    def test_system_gets_its_prompt_and_version(self):
        text, version = prompt_for("SYSTEM")
        assert text is SYSTEM_SEMANTIC_PROMPT
        assert version == "system:1"

    def test_work_gets_the_old_prompt_and_old_version(self):
        text, version = prompt_for("WORK")
        assert text is semantic_request.SEMANTIC_PROMPT
        assert version == "1"

    def test_every_non_system_kind_gets_the_work_prompt(self):
        for kind in ("WORK", "MATERIAL", "OTHER_KIND"):
            assert prompt_for(kind) == (semantic_request.SEMANTIC_PROMPT, "1")

    def test_system_version_constant_is_an_int_and_feeds_the_label(self, monkeypatch):
        assert SYSTEM_PROMPT_VERSION == 1
        monkeypatch.setattr(semantic_request, "SYSTEM_PROMPT_VERSION", 7)
        assert prompt_for("SYSTEM")[1] == "system:7"


class TestSystemPromptText:
    def test_has_no_system_rule(self):
        """Правило «СИСТЕМА» уводит систему из семей в «Новую»: в тексте
        для систем его быть не должно."""
        assert "СИСТЕМА" not in SYSTEM_SEMANTIC_PROMPT

    def test_keeps_the_answer_form_of_the_work_prompt(self):
        for part in ('"family_id"', '"new_family_name"', '"confidence"', '"reason"'):
            assert part in SYSTEM_SEMANTIC_PROMPT
            assert part in semantic_request.SEMANTIC_PROMPT

    def test_says_what_a_system_line_is(self):
        assert "комплект" in SYSTEM_SEMANTIC_PROMPT
        assert "тип системы" in SYSTEM_SEMANTIC_PROMPT

    def test_text_is_pinned_to_its_version(self):
        """Правка текста без новой версии — красный тест; правка с новой
        версией дописывает пару, а не заменяет старую."""
        pinned = {1: "c80c68b4c073dcd28f6a3be553ca1c14d82089acf74dedbc3653b586ab6c552b"}
        digest = hashlib.sha256(SYSTEM_SEMANTIC_PROMPT.encode("utf-8")).hexdigest()
        assert pinned.get(SYSTEM_PROMPT_VERSION) == digest


class TestBodyByKind:
    def test_system_body_carries_the_system_prompt_in_the_first_block(self):
        body = render_context_request(_system(), settings=_settings()).body
        assert body["messages"][0]["content"][0]["text"] == SYSTEM_SEMANTIC_PROMPT

    def test_work_body_carries_the_work_prompt_in_the_first_block(self):
        body = render_context_request(_material(), settings=_settings()).body
        assert body["messages"][0]["content"][0]["text"] == semantic_request.SEMANTIC_PROMPT

    def test_family_block_and_user_text_do_not_depend_on_kind(self):
        work = render_context_request(_material(), settings=_settings()).body["messages"]
        system = render_context_request(_system(), settings=_settings()).body["messages"]
        assert work[0]["content"][1] == system[0]["content"][1]
        assert work[1] == system[1]

    def test_prefix_hash_differs_between_system_and_work_with_equal_fields(self):
        work = render_context_request(_material(), settings=_settings())
        system = render_context_request(_system(), settings=_settings())
        assert work.prefix_hash != system.prefix_hash
        assert work.request_hash != system.request_hash

    def test_input_and_candidates_hash_do_not_depend_on_kind(self):
        work = render_context_request(_material(), settings=_settings())
        system = render_context_request(_system(), settings=_settings())
        assert work.input_hash == system.input_hash
        assert work.candidates_hash == system.candidates_hash

    def test_system_hashes_are_stable_between_renders(self):
        a = render_context_request(_system(), settings=_settings())
        b = render_context_request(_system(), settings=_settings())
        assert (a.request_hash, a.prefix_hash) == (b.request_hash, b.prefix_hash)

    def test_system_prompt_text_moves_the_system_hash_only(self, monkeypatch):
        work = render_context_request(_material(), settings=_settings())
        system = render_context_request(_system(), settings=_settings())
        monkeypatch.setattr(semantic_request, "SYSTEM_SEMANTIC_PROMPT", "Другой промпт систем")
        assert render_context_request(_material(), settings=_settings()).request_hash == (
            work.request_hash
        )
        assert render_context_request(_system(), settings=_settings()).request_hash != (
            system.request_hash
        )


class TestRequestHasherByKind:
    def test_hasher_matches_the_full_render_for_both_kinds(self):
        hasher = RequestHasher(_material(), settings=_settings())
        for material in (_material(), _system(), _material(title="Другое"), _system(title="Иное")):
            assert hasher.request_hash(material) == render_context_request(
                material, settings=_settings()
            ).request_hash

    def test_hasher_built_from_a_system_template_still_hashes_work_as_work(self):
        hasher = RequestHasher(_system(), settings=_settings())
        work = _material()
        assert hasher.request_hash(work) == render_context_request(
            work, settings=_settings()
        ).request_hash


class TestIsApplicableForSystem:
    def test_system_is_applicable_at_baseline(self):
        assert is_applicable(_system()) is True

    def test_system_follows_the_work_conditions_one_by_one(self):
        for overrides in (
            dict(archived=True),
            dict(member_count=0),
            dict(semantic_state="NOT_APPLICABLE"),
            dict(path_broken=True, path_counts=()),
            dict(candidates=()),
        ):
            assert is_applicable(_system(**overrides)) is False, overrides
            assert is_applicable(_material(**overrides)) is False, overrides
