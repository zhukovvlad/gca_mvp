"""Запрос заданий `family_schema` и `context_values`: чистые функции
`services/variant_request.py` (спека `2026-10-02-catalog-variants-design.md`
§2.3, §2.6) и константы формата ответа `services/variant_answer.py`.

Загрузка материала из базы и `render_request_for` — в
`tests/integration/test_work_variants_material.py`.

Эталоны (литералы хэша фичи 2, формы ответа, текст заглушек) записаны вручную и
не вычисляются тем же кодом, что проверяется.
"""
from __future__ import annotations

import copy
import dataclasses
import itertools
import json

import pytest

from config import Settings
from services import variant_request
from services.semantic_privacy import (
    PrivacyDictionary,
    PrivacyEntry,
    find_privacy_matches,
)
from services.semantic_request import (
    CandidateFamily,
    ContextRequestMaterial,
    RenderedRequest,
    render_context_request,
)
from services.variant_answer import (
    SCHEMA_RESPONSE_FORMAT,
    VALUES_RESPONSE_FORMAT,
    values_response_format_for,
)
from services.variant_request import (
    MAX_PATHS,
    SCHEMA_PROMPT,
    SCHEMA_PROMPT_VERSION,
    VALUES_PROMPT,
    VALUES_PROMPT_VERSION,
    SchemaParameterIn,
    SchemaRequestMaterial,
    ValuesRequestMaterial,
    paths_hash_of,
    render_schema_request,
    render_values_request,
    top_paths,
)

# ---------------------------------------------------------------------------
#  Заготовки
# ---------------------------------------------------------------------------


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, SECRET_KEY="x" * 32, **overrides)


def _schema_material(**overrides) -> SchemaRequestMaterial:
    defaults = dict(
        family_id=7,
        schema_id=70,
        title="Устройство пола",
        unit_code="M2",
        definition="Пол по грунту",
        names=("Стяжка пола 50 мм", "Стяжка пола 80 мм", "Устройство бетонного пола"),
    )
    defaults.update(overrides)
    return SchemaRequestMaterial(**defaults)


def _paths(n: int = 10) -> tuple[str, ...]:
    return tuple(f"Раздел {i} / Подраздел {i}" for i in range(1, n + 1))


def _values_material(**overrides) -> ValuesRequestMaterial:
    defaults = dict(
        context_id=5,
        schema_id=70,
        title="Стяжка пола 50 мм",
        unit_code="M2",
        article="12.03 Полы",
        paths=_paths(10),
        parameters=(
            SchemaParameterIn(ordinal=1, name="Толщина", values=("50 мм", "80 мм")),
            SchemaParameterIn(ordinal=2, name="Материал", values=("бетон", "цементный раствор")),
        ),
    )
    defaults.update(overrides)
    return ValuesRequestMaterial(**defaults)


def _hash(rendered: RenderedRequest) -> str:
    return rendered.request_hash


def _dictionary(*pairs: tuple[str, str]) -> PrivacyDictionary:
    entries = tuple(PrivacyEntry(kind=k, text=t) for k, t in sorted(pairs))
    return PrivacyDictionary(entries=entries, digest="test-digest")


# ---------------------------------------------------------------------------
#  Снимок хэша фичи 2: тело `family_suggestion` не меняется ни байтом
# ---------------------------------------------------------------------------


def test_family_suggestion_request_hash_snapshot_is_unchanged():
    material = ContextRequestMaterial(
        context_id=1, unit_id=5, unit_code="m2", title="Ustroystvo pola betonnogo",
        article="12 Poly", path_counts=(("A / B", 3), ("A / C", 3), ("Z", 1)),
        member_count=7, archived=False, semantic_state="PENDING", semantic_kind="POSITION",
        work_family_id=None,
        candidates=(
            CandidateFamily(id=11, title="Poly", unit_code="m2", definition="def poly"),
            CandidateFamily(id=4, title="Steny", unit_code="m2", definition="def steny"),
        ),
    )
    settings = _settings(SEMANTIC_MODEL="test/model-x", SEMANTIC_MAX_TOKENS=1234)
    assert (
        render_context_request(material, settings=settings).request_hash
        == "ddbd8808c7d5d69b9310617489c294ad93b585b24ff4e1cb64db9e2db4e7a2d4"
    )


# ---------------------------------------------------------------------------
#  Формы ответа
# ---------------------------------------------------------------------------


def test_schema_response_format_is_the_documented_literal():
    assert SCHEMA_RESPONSE_FORMAT == {
        "type": "json_schema",
        "json_schema": {
            "name": "family_schema",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "parameters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "ordinal": {"type": "integer", "enum": [1, 2, 3]},
                                "name": {"type": "string"},
                                "values": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["ordinal", "name", "values"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["parameters"],
                "additionalProperties": False,
            },
        },
    }


def test_values_response_format_is_the_documented_literal():
    assert VALUES_RESPONSE_FORMAT == {
        "type": "json_schema",
        "json_schema": {
            "name": "context_values",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "values": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "ordinal": {"type": "integer", "enum": [1, 2, 3]},
                                "kind": {
                                    "type": "string",
                                    "enum": ["value", "new", "conflict", "none"],
                                },
                                "value": {"type": ["string", "null"]},
                                "source": {
                                    "anyOf": [
                                        {"type": "string", "enum": ["name", "path"]},
                                        {"type": "null"},
                                    ],
                                },
                            },
                            "required": ["ordinal", "kind", "value", "source"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["values"],
                "additionalProperties": False,
            },
        },
    }


# ---------------------------------------------------------------------------
#  Промпты
# ---------------------------------------------------------------------------


class TestPrompts:
    def test_prompt_versions(self):
        assert SCHEMA_PROMPT_VERSION == 3
        assert VALUES_PROMPT_VERSION == 1

    def test_schema_prompt_states_thirty_value_limit(self):
        assert "от 1 до 30" in SCHEMA_PROMPT
        assert "от 1 до 8" not in SCHEMA_PROMPT

    def test_schema_prompt_asks_for_parameters_only(self):
        assert '"parameters"' in SCHEMA_PROMPT
        assert '"ordinal"' in SCHEMA_PROMPT
        assert '"rows"' not in SCHEMA_PROMPT
        assert '"key"' not in SCHEMA_PROMPT

    def test_values_prompt_uses_tagged_answer_without_string_markers(self):
        for kind in ("value", "new", "conflict", "none"):
            assert f'"{kind}"' in VALUES_PROMPT, kind
        assert '"values"' in VALUES_PROMPT
        assert '"source"' in VALUES_PROMPT
        assert "новое:" not in VALUES_PROMPT
        assert "разное" not in VALUES_PROMPT
        assert '"rows"' not in VALUES_PROMPT


# ---------------------------------------------------------------------------
#  top_paths, paths_hash_of
# ---------------------------------------------------------------------------


class TestTopPaths:
    def test_orders_by_descending_count(self):
        assert top_paths((("а", 1), ("б", 5), ("в", 2))) == ("б", "в", "а")

    def test_equal_counts_lexicographic(self):
        assert top_paths((("яблоко", 3), ("апельсин", 3), ("груша", 3))) == (
            "апельсин", "груша", "яблоко",
        )

    def test_count_beats_lexicographic_order(self):
        assert top_paths((("а", 1), ("я", 9))) == ("я", "а")

    def test_at_most_ten_by_default(self):
        counts = tuple((f"путь {i:02d}", 100 - i) for i in range(15))
        result = top_paths(counts)
        assert MAX_PATHS == 10
        assert result == tuple(f"путь {i:02d}" for i in range(10))

    def test_limit_is_a_parameter(self):
        assert top_paths((("а", 3), ("б", 2), ("в", 1)), limit=2) == ("а", "б")

    def test_fewer_than_limit_returns_all(self):
        assert top_paths((("а", 3), ("б", 2))) == ("а", "б")

    def test_empty_input(self):
        assert top_paths(()) == ()

    @pytest.mark.parametrize("order", list(itertools.permutations(range(4))))
    def test_independent_of_input_order(self, order):
        base = (("в", 2), ("а", 2), ("б", 7), ("г", 1))
        shuffled = tuple(base[i] for i in order)
        assert top_paths(shuffled) == ("б", "а", "в", "г")


class TestPathsHash:
    def test_depends_on_order(self):
        assert paths_hash_of(("а", "б")) != paths_hash_of(("б", "а"))

    def test_equal_lists_equal_hashes(self):
        assert paths_hash_of(["а", "б"]) == paths_hash_of(("а", "б"))

    def test_is_sha256_hex_of_canonical_json_list(self):
        import hashlib

        expected = hashlib.sha256(
            json.dumps(["а", "б"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        assert paths_hash_of(("а", "б")) == expected

    def test_empty_list_has_a_hash(self):
        assert len(paths_hash_of(())) == 64

    def test_frequency_swap_changes_body_and_paths_hash(self):
        before = top_paths((("Раздел А", 5), ("Раздел Б", 3)))
        after = top_paths((("Раздел А", 3), ("Раздел Б", 5)))
        assert before != after
        assert paths_hash_of(before) != paths_hash_of(after)
        settings = _settings()
        rendered_before = render_values_request(_values_material(paths=before), settings=settings)
        rendered_after = render_values_request(_values_material(paths=after), settings=settings)
        assert rendered_before.body != rendered_after.body
        assert rendered_before.request_hash != rendered_after.request_hash


# ---------------------------------------------------------------------------
#  Раскладка тела
# ---------------------------------------------------------------------------


class TestSchemaBodyLayout:
    def test_keys_and_profile(self):
        settings = _settings(
            SEMANTIC_SCHEMA_MODEL="vendor/schema-model", SEMANTIC_SCHEMA_MAX_TOKENS=4321,
            SEMANTIC_VARIANTS_REASONING_EFFORT="medium",
        )
        body = render_schema_request(_schema_material(), settings=settings).body
        assert set(body) == {
            "model", "temperature", "max_tokens", "reasoning", "usage", "response_format",
            "messages",
        }
        assert body["model"] == "vendor/schema-model"
        assert body["temperature"] == 0
        assert body["max_tokens"] == 4321
        assert body["reasoning"] == {"effort": "medium"}
        assert body["usage"] == {"include": True}
        assert body["response_format"] == SCHEMA_RESPONSE_FORMAT

    def test_system_is_one_cached_prompt_block(self):
        body = render_schema_request(_schema_material(), settings=_settings()).body
        system, user = body["messages"]
        assert system["role"] == "system"
        assert system["content"] == [
            {"type": "text", "text": SCHEMA_PROMPT, "cache_control": {"type": "ephemeral"}}
        ]
        assert user["role"] == "user"
        assert isinstance(user["content"], str)

    def test_user_text_has_family_and_numbered_names(self):
        user = render_schema_request(_schema_material(), settings=_settings()).body["messages"][1][
            "content"
        ]
        assert user == (
            "СЕМЬЯ: Устройство пола\nЕДИНИЦА: M2\nОПРЕДЕЛЕНИЕ: Пол по грунту\n\nСТРОКИ:\n"
            "1. Стяжка пола 50 мм\n2. Стяжка пола 80 мм\n3. Устройство бетонного пола"
        )

    def test_missing_unit_is_shown_as_stub(self):
        user = render_schema_request(
            _schema_material(unit_code=None), settings=_settings()
        ).body["messages"][1]["content"]
        assert "ЕДИНИЦА: без единицы\n" in user

    def test_fingerprints(self):
        import hashlib

        rendered = render_schema_request(_schema_material(), settings=_settings())
        names = ["Стяжка пола 50 мм", "Стяжка пола 80 мм", "Устройство бетонного пола"]
        assert rendered.candidates_hash == hashlib.sha256(
            json.dumps(names, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        assert len(rendered.request_hash) == 64
        assert len(rendered.prefix_hash) == 64
        assert len(rendered.input_hash) == 64
        assert rendered.prefix_bytes == len(SCHEMA_PROMPT.encode("utf-8"))
        assert rendered.user_bytes == len(
            rendered.body["messages"][1]["content"].encode("utf-8")
        )

    def test_render_is_deterministic(self):
        settings = _settings()
        first = render_schema_request(_schema_material(), settings=settings)
        second = render_schema_request(_schema_material(), settings=settings)
        assert first == second

    def test_names_are_sorted_inside_render(self):
        settings = _settings()
        shuffled = _schema_material(names=("Устройство бетонного пола", "Стяжка пола 50 мм",
                                           "Стяжка пола 80 мм"))
        assert render_schema_request(shuffled, settings=settings) == render_schema_request(
            _schema_material(), settings=settings
        )


def _values_ordinal_enum(body: dict) -> list:
    return body["response_format"]["json_schema"]["schema"]["properties"]["values"]["items"][
        "properties"
    ]["ordinal"]["enum"]


class TestValuesResponseFormatPerSchema:
    @pytest.mark.parametrize("ordinals", [(1, 2), (1, 3), (1, 2, 3), (2,), (1,)])
    def test_rendered_enum_is_exactly_the_schema_ordinals(self, ordinals):
        parameters = tuple(
            SchemaParameterIn(ordinal=o, name=f"П{o}", values=("а", "б")) for o in ordinals
        )
        material = _values_material(parameters=parameters)
        body = render_values_request(material, settings=_settings()).body
        assert _values_ordinal_enum(body) == list(ordinals)

    def test_enum_is_sorted_whatever_the_material_order(self):
        parameters = (
            SchemaParameterIn(ordinal=3, name="Класс", values=("B15",)),
            SchemaParameterIn(ordinal=1, name="Толщина", values=("50 мм",)),
        )
        body = render_values_request(
            _values_material(parameters=parameters), settings=_settings()
        ).body
        assert _values_ordinal_enum(body) == [1, 3]

    def test_only_the_ordinal_enum_differs_from_the_base_format(self):
        body = render_values_request(_values_material(), settings=_settings()).body
        expected = copy.deepcopy(VALUES_RESPONSE_FORMAT)
        expected["json_schema"]["schema"]["properties"]["values"]["items"]["properties"][
            "ordinal"
        ]["enum"] = [1, 2]
        assert body["response_format"] == expected

    def test_helper_does_not_touch_the_base_constant(self):
        pristine = copy.deepcopy(VALUES_RESPONSE_FORMAT)
        built = values_response_format_for([1, 3])
        built["json_schema"]["schema"]["required"].append("mutated")
        assert pristine == VALUES_RESPONSE_FORMAT

    def test_different_ordinal_sets_give_different_request_hashes(self):
        two = _values_material()
        gap = dataclasses.replace(
            two,
            parameters=(
                two.parameters[0],
                SchemaParameterIn(ordinal=3, name="Материал", values=("бетон",)),
            ),
        )
        assert (
            render_values_request(two, settings=_settings()).request_hash
            != render_values_request(gap, settings=_settings()).request_hash
        )


class TestValuesBodyLayout:
    def test_keys_and_profile(self):
        settings = _settings(
            SEMANTIC_VALUES_MODEL="vendor/values-model", SEMANTIC_VALUES_MAX_TOKENS=777,
            SEMANTIC_VARIANTS_REASONING_EFFORT="high",
        )
        body = render_values_request(_values_material(), settings=settings).body
        assert set(body) == {
            "model", "temperature", "max_tokens", "reasoning", "usage", "response_format",
            "messages",
        }
        assert body["model"] == "vendor/values-model"
        assert body["temperature"] == 0
        assert body["max_tokens"] == 777
        assert body["reasoning"] == {"effort": "high"}
        assert body["usage"] == {"include": True}
        # Материал — параметры 1 и 2: формат значений несёт ровно их порядковые.
        expected = copy.deepcopy(VALUES_RESPONSE_FORMAT)
        expected["json_schema"]["schema"]["properties"]["values"]["items"]["properties"][
            "ordinal"
        ]["enum"] = [1, 2]
        assert body["response_format"] == expected

    def test_system_has_prompt_and_schema_blocks_cache_on_schema(self):
        body = render_values_request(_values_material(), settings=_settings()).body
        system = body["messages"][0]["content"]
        assert len(system) == 2
        assert system[0] == {"type": "text", "text": VALUES_PROMPT}
        assert system[1]["cache_control"] == {"type": "ephemeral"}
        assert set(system[1]) == {"type", "text", "cache_control"}
        schema_text = system[1]["text"]
        for fragment in ("Толщина", "50 мм", "80 мм", "Материал", "бетон", "цементный раствор"):
            assert fragment in schema_text, fragment

    def test_zero_parameter_schema_block_says_there_are_none(self):
        # Схема из нуля параметров — законная заморозка (спека §2.2): модель
        # обязана увидеть явное «параметров нет», а не голый заголовок.
        material = _values_material(parameters=())
        text = render_values_request(material, settings=_settings()).body["messages"][0][
            "content"
        ][1]["text"]
        assert text == "СХЕМА СЕМЬИ:\n(параметров нет)"

    def test_schema_block_lists_parameters_by_ordinal(self):
        material = _values_material(
            parameters=(
                SchemaParameterIn(ordinal=2, name="Материал", values=("бетон",)),
                SchemaParameterIn(ordinal=1, name="Толщина", values=("50 мм",)),
            )
        )
        text = render_values_request(material, settings=_settings()).body["messages"][0][
            "content"
        ][1]["text"]
        assert text.index("Толщина") < text.index("Материал")

    def test_user_text_with_paths(self):
        material = _values_material(paths=("Секция 1 / Полы", "Секция 2 / Стяжки"))
        user = render_values_request(material, settings=_settings()).body["messages"][1]["content"]
        assert user == (
            "СТРОКА\nСтатья: 12.03 Полы\nНаименование: Стяжка пола 50 мм\nЕдиница: M2\n"
            "Пути разделов (от самого частого):\n1. Секция 1 / Полы\n2. Секция 2 / Стяжки"
        )

    def test_stubs_for_missing_paths_article_and_unit(self):
        material = _values_material(paths=(), article=None, unit_code=None)
        user = render_values_request(material, settings=_settings()).body["messages"][1]["content"]
        assert user == (
            "СТРОКА\nСтатья: (не определена)\nНаименование: Стяжка пола 50 мм\n"
            "Единица: без единицы\nПути разделов: (разделы не указаны)"
        )

    def test_fingerprints(self):
        import hashlib

        material = _values_material()
        rendered = render_values_request(material, settings=_settings())
        # candidates_hash — sha256 схемы: параметры по ordinal, значения списком.
        schema_literal = [
            {"ordinal": 1, "name": "Толщина", "values": ["50 мм", "80 мм"]},
            {"ordinal": 2, "name": "Материал", "values": ["бетон", "цементный раствор"]},
        ]
        assert rendered.candidates_hash == hashlib.sha256(
            json.dumps(schema_literal, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        system = rendered.body["messages"][0]["content"]
        assert rendered.prefix_bytes == sum(len(b["text"].encode("utf-8")) for b in system)
        assert rendered.user_bytes == len(rendered.body["messages"][1]["content"].encode("utf-8"))

    def test_prefix_is_shared_by_contexts_of_one_schema(self):
        settings = _settings()
        first = render_values_request(_values_material(title="Строка А"), settings=settings)
        second = render_values_request(
            _values_material(title="Строка Б", context_id=99, paths=("Другой путь",)),
            settings=settings,
        )
        assert first.prefix_hash == second.prefix_hash
        assert first.candidates_hash == second.candidates_hash
        assert first.request_hash != second.request_hash
        assert first.input_hash != second.input_hash

    def test_render_is_deterministic(self):
        settings = _settings()
        assert render_values_request(_values_material(), settings=settings) == (
            render_values_request(_values_material(), settings=settings)
        )


# ---------------------------------------------------------------------------
#  Оси request_hash: смена каждой меняет хэш, служебное не меняет
# ---------------------------------------------------------------------------

_SCHEMA_BASE = _schema_material()
_SCHEMA_AXES = {
    "family_title": dict(title="Устройство основания пола"),
    "family_definition": dict(definition="Пол по плите"),
    "family_unit": dict(unit_code="M3"),
    "family_unit_removed": dict(unit_code=None),
    "names_added": dict(names=_SCHEMA_BASE.names + ("Укладка линолеума",)),
    "names_removed": dict(names=_SCHEMA_BASE.names[:-1]),
    "names_one_changed": dict(
        names=(_SCHEMA_BASE.names[0], _SCHEMA_BASE.names[1], "Устройство бетонного пола М300")
    ),
}
_SCHEMA_SETTINGS_AXES = {
    "schema_model": dict(SEMANTIC_SCHEMA_MODEL="vendor/other-model"),
    "schema_max_tokens": dict(SEMANTIC_SCHEMA_MAX_TOKENS=19999),
    "reasoning_effort": dict(SEMANTIC_VARIANTS_REASONING_EFFORT="high"),
}


class TestSchemaHashAxes:
    @pytest.mark.parametrize("axis", sorted(_SCHEMA_AXES))
    def test_material_axis_changes_request_hash(self, axis):
        settings = _settings()
        base = render_schema_request(_SCHEMA_BASE, settings=settings)
        changed = render_schema_request(
            dataclasses.replace(_SCHEMA_BASE, **_SCHEMA_AXES[axis]), settings=settings
        )
        assert changed.request_hash != base.request_hash

    @pytest.mark.parametrize("axis", sorted(_SCHEMA_SETTINGS_AXES))
    def test_profile_axis_changes_request_hash(self, axis):
        base = render_schema_request(_SCHEMA_BASE, settings=_settings())
        changed = render_schema_request(
            _SCHEMA_BASE, settings=_settings(**_SCHEMA_SETTINGS_AXES[axis])
        )
        assert changed.request_hash != base.request_hash

    def test_model_change_changes_prefix_hash_too(self):
        base = render_schema_request(_SCHEMA_BASE, settings=_settings())
        changed = render_schema_request(
            _SCHEMA_BASE, settings=_settings(SEMANTIC_SCHEMA_MODEL="vendor/other-model")
        )
        assert changed.prefix_hash != base.prefix_hash

    def test_values_profile_does_not_touch_the_schema_request(self):
        base = render_schema_request(_SCHEMA_BASE, settings=_settings())
        same = render_schema_request(
            _SCHEMA_BASE,
            settings=_settings(SEMANTIC_VALUES_MODEL="vendor/x", SEMANTIC_VALUES_MAX_TOKENS=5),
        )
        assert same.request_hash == base.request_hash

    def test_response_format_change_changes_request_hash(self, monkeypatch):
        base = render_schema_request(_SCHEMA_BASE, settings=_settings())
        changed_format = copy.deepcopy(SCHEMA_RESPONSE_FORMAT)
        changed_format["json_schema"]["strict"] = False
        monkeypatch.setattr(variant_request, "SCHEMA_RESPONSE_FORMAT", changed_format)
        changed = render_schema_request(_SCHEMA_BASE, settings=_settings())
        assert changed.request_hash != base.request_hash

    @pytest.mark.parametrize(
        ("render", "material", "constant"),
        [
            (render_schema_request, _SCHEMA_BASE, "SCHEMA_RESPONSE_FORMAT"),
            (render_values_request, _values_material(), "VALUES_RESPONSE_FORMAT"),
        ],
        ids=["family_schema", "context_values"],
    )
    def test_body_response_format_is_a_copy_not_the_module_constant(
        self, render, material, constant, monkeypatch
    ):
        # Тело уходит потребителям (исполнитель, сверка); правка вложенного
        # словаря в одном теле не должна менять константу и хэши следующих тел.
        # Рендер читает копию, подставленную monkeypatch-ем: упавший тест не
        # портит настоящую константу соседним тестам.
        pristine = copy.deepcopy(getattr(variant_request, constant))
        monkeypatch.setattr(variant_request, constant, copy.deepcopy(pristine))
        body = render(material, settings=_settings()).body
        body["response_format"]["json_schema"]["schema"]["required"].append("mutated")
        body["response_format"]["json_schema"]["strict"] = False
        assert getattr(variant_request, constant) == pristine

    @pytest.mark.parametrize(
        "ids", [dict(family_id=8), dict(schema_id=71), dict(family_id=1, schema_id=2)]
    )
    def test_identifiers_do_not_change_request_hash(self, ids):
        settings = _settings()
        base = render_schema_request(_SCHEMA_BASE, settings=settings)
        other = render_schema_request(dataclasses.replace(_SCHEMA_BASE, **ids), settings=settings)
        assert other == base

    def test_prompt_version_is_not_part_of_the_hash(self, monkeypatch):
        base = render_schema_request(_SCHEMA_BASE, settings=_settings())
        monkeypatch.setattr(variant_request, "SCHEMA_PROMPT_VERSION", 99)
        assert render_schema_request(_SCHEMA_BASE, settings=_settings()) == base


_VALUES_BASE = _values_material()
_VALUES_AXES = {
    "title": dict(title="Стяжка пола 80 мм"),
    "unit": dict(unit_code="M3"),
    "article": dict(article="12.04 Основания"),
    "article_removed": dict(article=None),
    "parameter_name": dict(
        parameters=(
            SchemaParameterIn(ordinal=1, name="Толщина слоя", values=("50 мм", "80 мм")),
            _VALUES_BASE.parameters[1],
        )
    ),
    "parameter_value_changed": dict(
        parameters=(
            SchemaParameterIn(ordinal=1, name="Толщина", values=("50 мм", "90 мм")),
            _VALUES_BASE.parameters[1],
        )
    ),
    "parameter_value_added": dict(
        parameters=(
            SchemaParameterIn(ordinal=1, name="Толщина", values=("50 мм", "80 мм", "100 мм")),
            _VALUES_BASE.parameters[1],
        )
    ),
    "parameter_value_order": dict(
        parameters=(
            SchemaParameterIn(ordinal=1, name="Толщина", values=("80 мм", "50 мм")),
            _VALUES_BASE.parameters[1],
        )
    ),
    "parameter_removed": dict(parameters=(_VALUES_BASE.parameters[0],)),
    "parameter_ordinal": dict(
        parameters=(
            SchemaParameterIn(ordinal=3, name="Толщина", values=("50 мм", "80 мм")),
            _VALUES_BASE.parameters[1],
        )
    ),
}
_VALUES_SETTINGS_AXES = {
    "values_model": dict(SEMANTIC_VALUES_MODEL="vendor/other-model"),
    "values_max_tokens": dict(SEMANTIC_VALUES_MAX_TOKENS=599),
    "reasoning_effort": dict(SEMANTIC_VARIANTS_REASONING_EFFORT="high"),
}


class TestValuesHashAxes:
    @pytest.mark.parametrize("axis", sorted(_VALUES_AXES))
    def test_material_axis_changes_request_hash(self, axis):
        settings = _settings()
        base = render_values_request(_VALUES_BASE, settings=settings)
        changed = render_values_request(
            dataclasses.replace(_VALUES_BASE, **_VALUES_AXES[axis]), settings=settings
        )
        assert changed.request_hash != base.request_hash

    @pytest.mark.parametrize("position", range(10))
    def test_each_of_ten_paths_changes_request_hash(self, position):
        settings = _settings()
        changed_paths = list(_VALUES_BASE.paths)
        changed_paths[position] = "Совсем другой раздел"
        base = render_values_request(_VALUES_BASE, settings=settings)
        changed = render_values_request(
            dataclasses.replace(_VALUES_BASE, paths=tuple(changed_paths)), settings=settings
        )
        assert changed.request_hash != base.request_hash

    def test_eleventh_path_does_not_change_request_hash(self):
        settings = _settings()
        base = render_values_request(_VALUES_BASE, settings=settings)
        with_eleventh = render_values_request(
            dataclasses.replace(_VALUES_BASE, paths=_paths(11)), settings=settings
        )
        assert with_eleventh == base

    @pytest.mark.parametrize("axis", sorted(_VALUES_SETTINGS_AXES))
    def test_profile_axis_changes_request_hash(self, axis):
        base = render_values_request(_VALUES_BASE, settings=_settings())
        changed = render_values_request(
            _VALUES_BASE, settings=_settings(**_VALUES_SETTINGS_AXES[axis])
        )
        assert changed.request_hash != base.request_hash

    def test_schema_profile_does_not_touch_the_values_request(self):
        base = render_values_request(_VALUES_BASE, settings=_settings())
        same = render_values_request(
            _VALUES_BASE,
            settings=_settings(SEMANTIC_SCHEMA_MODEL="vendor/x", SEMANTIC_SCHEMA_MAX_TOKENS=5),
        )
        assert same.request_hash == base.request_hash

    def test_response_format_change_changes_request_hash(self, monkeypatch):
        base = render_values_request(_VALUES_BASE, settings=_settings())
        changed_format = copy.deepcopy(VALUES_RESPONSE_FORMAT)
        changed_format["json_schema"]["strict"] = False
        monkeypatch.setattr(variant_request, "VALUES_RESPONSE_FORMAT", changed_format)
        changed = render_values_request(_VALUES_BASE, settings=_settings())
        assert changed.request_hash != base.request_hash

    @pytest.mark.parametrize(
        "ids", [dict(context_id=6), dict(schema_id=71), dict(context_id=1, schema_id=2)]
    )
    def test_identifiers_do_not_change_request_hash(self, ids):
        settings = _settings()
        base = render_values_request(_VALUES_BASE, settings=settings)
        other = render_values_request(dataclasses.replace(_VALUES_BASE, **ids), settings=settings)
        assert other == base

    def test_prompt_version_is_not_part_of_the_hash(self, monkeypatch):
        base = render_values_request(_VALUES_BASE, settings=_settings())
        monkeypatch.setattr(variant_request, "VALUES_PROMPT_VERSION", 99)
        assert render_values_request(_VALUES_BASE, settings=_settings()) == base


# ---------------------------------------------------------------------------
#  Приватность: `find_privacy_matches` на теле каждого нового вида
# ---------------------------------------------------------------------------

_OBJECT = ("object", "жк северное сияние")


class TestPrivacyOnNewBodies:
    def test_schema_body_without_matches_is_clean(self):
        rendered = render_schema_request(_schema_material(), settings=_settings())
        assert find_privacy_matches(_dictionary(_OBJECT), rendered) == ()

    def test_values_body_without_matches_is_clean(self):
        rendered = render_values_request(_values_material(), settings=_settings())
        assert find_privacy_matches(_dictionary(_OBJECT), rendered) == ()

    def test_object_name_in_a_row_name_is_found_in_schema_body(self):
        material = _schema_material(
            names=("Стяжка пола ЖК Северное сияние", "Стяжка пола 80 мм")
        )
        rendered = render_schema_request(material, settings=_settings())
        matches = find_privacy_matches(_dictionary(_OBJECT), rendered)
        assert [(m.text, m.kind, m.where) for m in matches] == [
            (_OBJECT[1], "object", "context")
        ]

    def test_object_name_in_a_path_is_found_in_values_body(self):
        material = _values_material(paths=("ЖК Северное сияние / Секция 1", "Раздел 2"))
        rendered = render_values_request(material, settings=_settings())
        matches = find_privacy_matches(_dictionary(_OBJECT), rendered)
        assert [(m.text, m.kind, m.where) for m in matches] == [
            (_OBJECT[1], "object", "context")
        ]

    def test_object_name_in_a_parameter_value_is_found_in_the_prompt_part(self):
        material = _values_material(
            parameters=(SchemaParameterIn(ordinal=1, name="Корпус", values=("ЖК Северное сияние",)),)
        )
        rendered = render_values_request(material, settings=_settings())
        matches = find_privacy_matches(_dictionary(_OBJECT), rendered)
        assert [(m.text, m.where) for m in matches] == [(_OBJECT[1], "prompt")]
