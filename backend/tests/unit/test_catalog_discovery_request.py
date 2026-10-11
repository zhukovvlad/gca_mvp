"""Тело запроса открытия семей и профиль его модели (спека 3б §2.3, «Тело» и
«Профиль»; DoD 2).

Тело строит чистая функция `build_discovery_request`: на заранее собранных
именах, семьях и справочнике, без базы. Загрузку материала из базы и охват
проверяют интеграционные тесты `test_catalog_discovery_scope.py`."""
from __future__ import annotations

import copy
import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

import services.family_discovery as fd
from config import Settings
from models import SemanticJobKind
from services.family_discovery import (
    DISCOVERY_PROMPT,
    DISCOVERY_PROMPT_VERSION,
    DiscoveryCategory,
    DiscoveryName,
    build_discovery_request,
)
from services.semantic_cost import Tariffs, tariffs_from
from services.semantic_privacy import PrivacyDictionary, PrivacyEntry, find_privacy_matches
from services.semantic_request import (
    FAMILY_BLOCK_HEADER,
    SERIALIZATION_VERSION,
    CandidateFamily,
    _canonical_bytes,
    _sha256_hex,
    family_line,
)
from services.variant_answer import DISCOVERY_RESPONSE_FORMAT, DISCOVERY_RESPONSE_SCHEMA_VERSION

_KEY = "k" * 32


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, SECRET_KEY=_KEY, **kw)


S = _settings()


def _name(index, title, *, articles=("01.01 Стены",), path="Раздел 1 / Подраздел", ids=None):
    return DiscoveryName(
        index=index, title=title, context_ids=tuple(ids or (index,)),
        articles=tuple(articles), path=path,
    )


NAMES = (_name(1, "Кладка стен"), _name(2, "Штукатурка"))
FAMILIES = (
    CandidateFamily(id=10, title="Кладка", unit_code="м2", definition="Возведение стен"),
    CandidateFamily(id=11, title="Отделка", unit_code="м2", definition="Отделочные работы"),
)
CATEGORIES = (
    DiscoveryCategory(id=1, title="Работа", definition="Виды строительных работ"),
    DiscoveryCategory(id=2, title="Материал", definition="Закупка материалов"),
)


def _build(**overrides):
    args = dict(
        unit_code="м2", names=NAMES, families=FAMILIES, categories=CATEGORIES,
        uncategorized_family_ids=(11,), settings=S,
    )
    args.update(overrides)
    return build_discovery_request(**args)


def _user(rendered) -> str:
    return rendered.body["messages"][1]["content"]


def _system(rendered) -> list[dict]:
    return rendered.body["messages"][0]["content"]


# ---------------------------------------------------------------------------
#  Профиль и тарифы
# ---------------------------------------------------------------------------

class TestProfileSettings:
    def test_defaults_are_the_documented_values(self):
        s = _settings()
        assert s.SEMANTIC_DISCOVERY_MODEL == "anthropic/claude-sonnet-5.5"
        assert s.SEMANTIC_DISCOVERY_REASONING_EFFORT == "low"
        assert s.SEMANTIC_DISCOVERY_MAX_TOKENS == 32000
        assert s.SEMANTIC_DISCOVERY_MAX_NAMES == 1500
        assert s.SEMANTIC_DISCOVERY_NAME_MAX_CHARS == 300
        prices = [
            getattr(s, f"SEMANTIC_DISCOVERY_PRICE_{suffix}_PER_M")
            for suffix in ("INPUT", "CACHE_WRITE", "CACHE_READ", "OUTPUT")
        ]
        assert prices == [Decimal("2"), Decimal("2.5"), Decimal("0.2"), Decimal("10")]
        assert all(isinstance(price, Decimal) for price in prices)

    @pytest.mark.parametrize(
        "name",
        [
            "SEMANTIC_DISCOVERY_MAX_TOKENS",
            "SEMANTIC_DISCOVERY_MAX_NAMES",
            "SEMANTIC_DISCOVERY_NAME_MAX_CHARS",
        ],
    )
    def test_integer_settings_below_one_rejected_and_one_accepted(self, name):
        for bad in (0, -1):
            with pytest.raises(ValidationError) as excinfo:
                _settings(**{name: bad})
            assert [err["loc"] for err in excinfo.value.errors()] == [(name,)]
        assert getattr(_settings(**{name: 1}), name) == 1

    @pytest.mark.parametrize("suffix", ["INPUT", "CACHE_WRITE", "CACHE_READ", "OUTPUT"])
    def test_negative_price_rejected_and_zero_accepted(self, suffix):
        name = f"SEMANTIC_DISCOVERY_PRICE_{suffix}_PER_M"
        with pytest.raises(ValidationError) as excinfo:
            _settings(**{name: Decimal("-0.01")})
        assert [err["loc"] for err in excinfo.value.errors()] == [(name,)]
        assert getattr(_settings(**{name: Decimal("0")}), name) == Decimal("0")

    @pytest.mark.parametrize("effort", ["low", "medium", "high"])
    def test_reasoning_effort_in_list_accepted(self, effort):
        s = _settings(SEMANTIC_DISCOVERY_REASONING_EFFORT=effort)
        assert effort == s.SEMANTIC_DISCOVERY_REASONING_EFFORT

    @pytest.mark.parametrize("bad", ["", "none", "minimal", "LOW", "xhigh"])
    def test_reasoning_effort_outside_list_rejected(self, bad):
        with pytest.raises(ValidationError) as excinfo:
            _settings(SEMANTIC_DISCOVERY_REASONING_EFFORT=bad)
        assert [err["loc"] for err in excinfo.value.errors()] == [
            ("SEMANTIC_DISCOVERY_REASONING_EFFORT",)
        ]

    def test_read_from_environment(self, monkeypatch):
        monkeypatch.setenv("SEMANTIC_DISCOVERY_MODEL", "vendor/discovery")
        monkeypatch.setenv("SEMANTIC_DISCOVERY_MAX_NAMES", "77")
        monkeypatch.setenv("SEMANTIC_DISCOVERY_PRICE_OUTPUT_PER_M", "3.25")
        s = _settings()
        assert s.SEMANTIC_DISCOVERY_MODEL == "vendor/discovery"
        assert s.SEMANTIC_DISCOVERY_MAX_NAMES == 77
        assert Decimal("3.25") == s.SEMANTIC_DISCOVERY_PRICE_OUTPUT_PER_M
        assert type(s.SEMANTIC_DISCOVERY_PRICE_OUTPUT_PER_M) is Decimal


class TestTariffs:
    def test_discovery_kind_takes_its_own_four_fields(self):
        s = _settings(
            SEMANTIC_DISCOVERY_PRICE_INPUT_PER_M=Decimal("4.1"),
            SEMANTIC_DISCOVERY_PRICE_CACHE_WRITE_PER_M=Decimal("4.2"),
            SEMANTIC_DISCOVERY_PRICE_CACHE_READ_PER_M=Decimal("4.3"),
            SEMANTIC_DISCOVERY_PRICE_OUTPUT_PER_M=Decimal("4.4"),
        )

        assert tariffs_from(s, SemanticJobKind.family_discovery) == Tariffs(
            Decimal("4.1"), Decimal("4.2"), Decimal("4.3"), Decimal("4.4")
        )

    def test_discovery_tariffs_differ_from_the_other_kinds_when_prices_differ(self):
        s = _settings(
            SEMANTIC_PRICE_INPUT_PER_M=Decimal("1.1"),
            SEMANTIC_SCHEMA_PRICE_INPUT_PER_M=Decimal("2.1"),
            SEMANTIC_VALUES_PRICE_INPUT_PER_M=Decimal("3.1"),
            SEMANTIC_DISCOVERY_PRICE_INPUT_PER_M=Decimal("4.1"),
        )
        got = [tariffs_from(s, kind).input_per_m for kind in SemanticJobKind]

        assert sorted(got) == [Decimal("1.1"), Decimal("2.1"), Decimal("3.1"), Decimal("4.1")]


# ---------------------------------------------------------------------------
#  Форма тела
# ---------------------------------------------------------------------------

class TestBodyShape:
    def test_top_level_keys_and_profile_values(self):
        body = _build().body

        assert set(body) == {
            "model", "temperature", "max_tokens", "reasoning", "usage", "response_format",
            "messages",
        }
        assert body["model"] == "anthropic/claude-sonnet-5.5"
        assert body["temperature"] == 0
        assert body["max_tokens"] == 32000
        assert body["usage"] == {"include": True}
        assert body["response_format"] == DISCOVERY_RESPONSE_FORMAT

    def test_reasoning_is_an_effort_never_enabled_false(self):
        body = _build(settings=_settings(SEMANTIC_DISCOVERY_REASONING_EFFORT="high")).body

        assert body["reasoning"] == {"effort": "high"}
        assert "enabled" not in body["reasoning"]

    def test_system_blocks_are_prompt_then_categories_then_families_with_cache_mark(self):
        blocks = _system(_build())

        assert [b["type"] for b in blocks] == ["text", "text", "text"]
        assert blocks[0]["text"] == DISCOVERY_PROMPT
        assert blocks[1]["text"].startswith("КАТЕГОРИИ СЕМЕЙ:\n")
        assert blocks[2]["text"].startswith(FAMILY_BLOCK_HEADER)
        assert "cache_control" not in blocks[0] and "cache_control" not in blocks[1]
        assert blocks[2]["cache_control"] == {"type": "ephemeral"}

    def test_categories_block_lists_id_title_definition_by_id(self):
        shuffled = (CATEGORIES[1], CATEGORIES[0])

        text = _system(_build(categories=shuffled))[1]["text"]

        assert text == (
            "КАТЕГОРИИ СЕМЕЙ:\n1. Работа — Виды строительных работ\n"
            "2. Материал — Закупка материалов"
        )

    def test_families_block_uses_family_line_sorted_by_id(self):
        shuffled = (FAMILIES[1], FAMILIES[0])

        text = _system(_build(families=shuffled))[2]["text"]

        assert text == FAMILY_BLOCK_HEADER + "\n".join(family_line(f) for f in FAMILIES)

    def test_user_text_exact_layout(self):
        names = (
            _name(1, "Кладка стен", articles=("01.01 Стены", "02.01 Полы"), path="А / Б"),
            _name(2, "Штукатурка", articles=(), path=""),
        )

        assert _user(_build(names=names)) == (
            "ЕДИНИЦА: м2\n"
            "СЕМЬИ БЕЗ КАТЕГОРИИ: 11\n"
            "ИМЕНА:\n"
            "1. Кладка стен | статьи: 01.01 Стены; 02.01 Полы | разделы: А / Б\n"
            "2. Штукатурка | статьи: (не определены) | разделы: (не указаны)"
        )

    def test_unit_none_is_a_legal_input(self):
        rendered = _build(unit_code=None)

        assert _user(rendered).startswith("ЕДИНИЦА: без единицы\n")

    def test_no_uncategorized_families_is_stated_explicitly(self):
        rendered = _build(uncategorized_family_ids=())

        assert "СЕМЬИ БЕЗ КАТЕГОРИИ: нет\n" in _user(rendered)

    def test_uncategorized_ids_are_sorted_ascending(self):
        rendered = _build(uncategorized_family_ids=(12, 10, 11))

        assert "СЕМЬИ БЕЗ КАТЕГОРИИ: 10, 11, 12\n" in _user(rendered)

    def test_names_listing_is_empty_but_valid_for_categories_only_launch(self):
        rendered = _build(names=())

        assert _user(rendered).endswith("ИМЕНА:\n")

    def test_whitespace_in_a_name_is_collapsed_to_one_line(self):
        rendered = _build(names=(_name(1, "Кладка\n  стен\tтолстая"),))

        assert "1. Кладка стен толстая | " in _user(rendered)

    def test_long_name_is_cut_with_an_ellipsis_to_the_limit(self):
        s = _settings(SEMANTIC_DISCOVERY_NAME_MAX_CHARS=10)

        rendered = _build(names=(_name(1, "А" * 11),), settings=s)

        assert f"1. {'А' * 9}… | " in _user(rendered)

    def test_name_exactly_at_the_limit_is_not_cut(self):
        s = _settings(SEMANTIC_DISCOVERY_NAME_MAX_CHARS=10)

        rendered = _build(names=(_name(1, "А" * 10),), settings=s)

        assert f"1. {'А' * 10} | " in _user(rendered)

    def test_long_path_is_cut_to_two_hundred_characters(self):
        long_path = "П" * 201

        rendered = _build(names=(_name(1, "Имя", path=long_path),))

        assert f"разделы: {'П' * 199}…" in _user(rendered)
        assert f"{'П' * 200}" not in _user(rendered)

    def test_path_of_exactly_two_hundred_characters_is_kept(self):
        rendered = _build(names=(_name(1, "Имя", path="П" * 200),))

        assert f"разделы: {'П' * 200}" in _user(rendered)
        assert "…" not in _user(rendered)

    def test_at_most_three_articles_reach_the_line(self):
        names = (_name(1, "Имя", articles=("А", "Б", "В", "Г")),)

        assert "статьи: А; Б; В |" in _user(_build(names=names))

    def test_prefix_bytes_and_user_bytes_count_the_blocks(self):
        rendered = _build()

        assert rendered.prefix_bytes == sum(len(b["text"].encode()) for b in _system(rendered))
        assert rendered.user_bytes == len(_user(rendered).encode())


# ---------------------------------------------------------------------------
#  Хэши
# ---------------------------------------------------------------------------

class TestHashes:
    def test_same_state_same_hashes(self):
        assert _build() == _build()

    def test_request_hash_is_sha256_of_version_and_body(self):
        rendered = _build()

        assert rendered.request_hash == _sha256_hex(
            {"serialization_version": SERIALIZATION_VERSION, "body": rendered.body}
        )

    def test_prefix_hash_is_model_and_system_only(self):
        rendered = _build()

        assert rendered.prefix_hash == _sha256_hex(
            {
                "serialization_version": SERIALIZATION_VERSION,
                "model": S.SEMANTIC_DISCOVERY_MODEL,
                "system": _system(rendered),
            }
        )

    def test_input_hash_is_the_user_text_only(self):
        rendered = _build()

        assert rendered.input_hash == _sha256_hex(
            {"serialization_version": SERIALIZATION_VERSION, "user": _user(rendered)}
        )

    def test_candidates_hash_is_the_active_families_and_the_categories(self):
        rendered = _build()

        assert rendered.candidates_hash == _sha256_hex(
            {
                "families": [
                    {"id": f.id, "title": f.title, "unit_code": f.unit_code,
                     "definition": f.definition}
                    for f in FAMILIES
                ],
                "categories": [
                    {"id": c.id, "title": c.title, "definition": c.definition}
                    for c in CATEGORIES
                ],
            }
        )

    def test_family_input_order_does_not_change_the_hashes(self):
        assert _build(families=tuple(reversed(FAMILIES))) == _build()

    def test_category_input_order_does_not_change_the_hashes(self):
        assert _build(categories=tuple(reversed(CATEGORIES))) == _build()

    def test_uncategorized_input_order_does_not_change_the_hashes(self):
        assert _build(uncategorized_family_ids=(12, 11)) == _build(
            uncategorized_family_ids=(11, 12)
        )


def _axis_cases():
    other_names = NAMES + (_name(3, "Окраска"),)
    return {
        "name_in_scope": dict(names=other_names),
        "name_text": dict(names=(_name(1, "Кладка стен!"), NAMES[1])),
        "article": dict(names=(_name(1, "Кладка стен", articles=("01.02 Иное",)), NAMES[1])),
        "path": dict(names=(_name(1, "Кладка стен", path="Другой раздел"), NAMES[1])),
        "family_title": dict(
            families=(CandidateFamily(10, "Кладка!", "м2", "Возведение стен"), FAMILIES[1])
        ),
        "family_definition": dict(
            families=(CandidateFamily(10, "Кладка", "м2", "Возведение стен!"), FAMILIES[1])
        ),
        "active_family_added": dict(
            families=FAMILIES + (CandidateFamily(12, "Новая", "м2", "Определение"),)
        ),
        "category_title": dict(
            categories=(DiscoveryCategory(1, "Работа!", "Виды строительных работ"), CATEGORIES[1])
        ),
        "category_definition": dict(
            categories=(DiscoveryCategory(1, "Работа", "Виды строительных работ!"), CATEGORIES[1])
        ),
        "category_added": dict(
            categories=CATEGORIES + (DiscoveryCategory(3, "Услуга", "Услуги"),)
        ),
        "uncategorized_family": dict(uncategorized_family_ids=(10, 11)),
        "uncategorized_family_none": dict(uncategorized_family_ids=()),
        "unit": dict(unit_code="м3"),
        "unit_none": dict(unit_code=None),
        "model": dict(settings=_settings(SEMANTIC_DISCOVERY_MODEL="vendor/other")),
        "max_tokens": dict(settings=_settings(SEMANTIC_DISCOVERY_MAX_TOKENS=31999)),
        "reasoning_effort": dict(
            settings=_settings(SEMANTIC_DISCOVERY_REASONING_EFFORT="medium")
        ),
        "name_max_chars": dict(settings=_settings(SEMANTIC_DISCOVERY_NAME_MAX_CHARS=5)),
    }


class TestEveryAxisChangesTheRequestHash:
    @pytest.mark.parametrize("axis", sorted(_axis_cases()))
    def test_axis(self, axis):
        base = _build()
        changed = _build(**_axis_cases()[axis])

        assert changed.request_hash != base.request_hash, axis

    def test_response_format_axis(self, monkeypatch):
        base = _build()
        changed_format = copy.deepcopy(DISCOVERY_RESPONSE_FORMAT)
        changed_format["json_schema"]["name"] = "family_discovery_changed"
        monkeypatch.setattr(fd, "DISCOVERY_RESPONSE_FORMAT", changed_format)

        assert _build().request_hash != base.request_hash

    def test_prompt_text_axis(self, monkeypatch):
        base = _build()
        monkeypatch.setattr(fd, "DISCOVERY_PROMPT", DISCOVERY_PROMPT + " ")

        assert _build().request_hash != base.request_hash

    def test_prompt_version_is_not_part_of_the_hash(self, monkeypatch):
        base = _build()
        monkeypatch.setattr(fd, "DISCOVERY_PROMPT_VERSION", 99)

        assert _build() == base

    def test_name_index_and_context_ids_are_not_part_of_the_hash_beyond_the_line(self):
        # Номера контекстов в тело не входят: один и тот же текст — один хэш.
        base = _build()
        same_text_other_contexts = tuple(
            DiscoveryName(
                index=n.index, title=n.title, context_ids=(900 + n.index, 901 + n.index),
                articles=n.articles, path=n.path,
            )
            for n in NAMES
        )

        assert _build(names=same_text_other_contexts) == base


class TestResponseFormatIsACopy:
    def test_body_response_format_is_a_copy_not_the_module_constant(self):
        rendered = _build()

        rendered.body["response_format"]["json_schema"]["schema"]["required"].append("mutated")
        rendered.body["response_format"]["json_schema"]["strict"] = False

        assert "mutated" not in DISCOVERY_RESPONSE_FORMAT["json_schema"]["schema"]["required"]
        assert DISCOVERY_RESPONSE_FORMAT["json_schema"]["strict"] is True


# ---------------------------------------------------------------------------
#  Ответная схема
# ---------------------------------------------------------------------------

class TestResponseFormatLiteral:
    def test_schema_is_strict_with_all_keys_required_and_closed(self):
        fmt = DISCOVERY_RESPONSE_FORMAT
        schema = fmt["json_schema"]["schema"]
        group = schema["properties"]["groups"]["items"]
        pair = schema["properties"]["family_categories"]["items"]

        assert fmt["type"] == "json_schema"
        assert fmt["json_schema"]["strict"] is True
        assert fmt["json_schema"]["name"] == "family_discovery"
        assert schema["required"] == ["groups", "not_work", "family_categories"]
        assert schema["additionalProperties"] is False
        assert group["required"] == [
            "family_id", "title", "definition", "category_id", "similar_family_id", "names",
        ]
        assert set(group["properties"]) == set(group["required"])
        assert group["additionalProperties"] is False
        assert group["properties"]["names"] == {
            "type": "array", "items": {"type": "integer"}, "minItems": 1,
        }
        for nullable in ("family_id", "category_id", "similar_family_id"):
            assert group["properties"][nullable] == {"type": ["integer", "null"]}
        for nullable in ("title", "definition"):
            assert group["properties"][nullable] == {"type": ["string", "null"]}
        assert schema["properties"]["not_work"] == {"type": "array", "items": {"type": "integer"}}
        assert pair["required"] == ["family_id", "category_id"]
        assert pair["additionalProperties"] is False

    def test_schema_version_is_a_nonempty_string_distinct_from_suggestion_versions(self):
        assert isinstance(DISCOVERY_RESPONSE_SCHEMA_VERSION, str)
        assert DISCOVERY_RESPONSE_SCHEMA_VERSION.strip()
        assert DISCOVERY_RESPONSE_SCHEMA_VERSION != "1"

    def test_schema_serializes_to_json(self):
        assert json.loads(json.dumps(DISCOVERY_RESPONSE_FORMAT)) == DISCOVERY_RESPONSE_FORMAT


class TestTopArticles:
    def test_more_contexts_first_then_title(self):
        counts = {"Б": 1, "В": 3, "А": 1, "Г": 2}

        assert fd._top_articles(counts) == ("В", "Г", "А")

    def test_fewer_than_three_are_kept_whole(self):
        assert fd._top_articles({"Б": 1, "А": 1}) == ("А", "Б")
        assert fd._top_articles({}) == ()


class TestPromptContent:
    def test_prompt_version_is_one(self):
        assert DISCOVERY_PROMPT_VERSION == 1

    def test_prompt_states_the_rules_of_the_specification(self):
        text = DISCOVERY_PROMPT

        assert "тип работы" in text
        assert "категори" in text
        assert "не работа" in text.lower()
        assert "дубл" in text


# ---------------------------------------------------------------------------
#  Приватность: тело проходит словарь целиком
# ---------------------------------------------------------------------------

def _dictionary(*pairs) -> PrivacyDictionary:
    entries = tuple(PrivacyEntry(kind=k, text=t) for k, t in sorted(pairs))
    return PrivacyDictionary(entries=entries, digest="test-digest")


class TestPrivacyOverTheBody:
    def test_privacy_search_does_not_raise_on_the_whole_body(self):
        found = find_privacy_matches(_dictionary(("contractor", "посторонний")), _build())

        assert found == ()

    def test_name_match_is_attributed_to_context(self):
        names = (_name(1, "Кладка у подрядчика ромашка"),)

        found = find_privacy_matches(_dictionary(("contractor", "ромашка")), _build(names=names))

        assert [(m.text, m.where) for m in found] == [("ромашка", "context")]

    def test_family_match_is_attributed_to_the_family_id(self):
        families = (CandidateFamily(10, "Кладка ромашка", "м2", "Определение"), FAMILIES[1])

        found = find_privacy_matches(_dictionary(("contractor", "ромашка")), _build(families=families))

        assert [(m.text, m.where) for m in found] == [("ромашка", "family:10")]

    def test_category_match_is_attributed_to_the_prompt_not_to_a_family(self):
        categories = (DiscoveryCategory(1, "Работа ромашка", "Определение"), CATEGORIES[1])

        found = find_privacy_matches(
            _dictionary(("contractor", "ромашка")), _build(categories=categories)
        )

        assert [(m.text, m.where) for m in found] == [("ромашка", "prompt")]

    def test_category_ids_never_collide_with_family_ids(self):
        # Номера категорий и семей пересекаются (1, 2): строка категории не
        # должна перетирать строку семьи в разборе блока.
        families = (CandidateFamily(1, "Семья ромашка", "м2", "Определение"),)
        categories = (DiscoveryCategory(1, "Категория", "Определение"),)

        found = find_privacy_matches(
            _dictionary(("contractor", "ромашка")), _build(families=families, categories=categories)
        )

        assert [(m.text, m.where) for m in found] == [("ромашка", "family:1")]


def test_canonical_bytes_of_the_body_are_plain_json():
    body = _build().body

    assert json.loads(_canonical_bytes(body)) == body
