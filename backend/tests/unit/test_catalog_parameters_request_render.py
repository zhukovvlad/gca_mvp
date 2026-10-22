"""Рендер запроса значений по подмножеству параметров и разбор ответа по
запрошенным (спека 3в §2.4): блок схемы и перечисление номеров формата ответа
несут только запрошенные параметры по возрастанию, строка правила есть только у
параметра с диапазонами, а полное подмножество без диапазонов даёт тот же хэш,
что до фичи.
"""
from __future__ import annotations

import json

import pytest

from config import Settings
from services.parameter_ranges import RANGE_RULE_LINE
from services.semantic_answer import AnswerSchemaError
from services.variant_answer import parse_values_answer
from services.variant_request import (
    SchemaParameterIn,
    ValuesRequestMaterial,
    render_values_request,
)

#: Тот же литерал, что в `test_catalog_parameters_request_snapshot.py`.
FULL_EXACT_HASH = "2b2de6c7d26b53314e5582540a0a4bd0a6f7403917ab4195e73e69e5c4f527b8"


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        SECRET_KEY="x" * 32,
        SEMANTIC_VALUES_MODEL="vendor/values-model",
        SEMANTIC_VALUES_MAX_TOKENS=900,
        SEMANTIC_VARIANTS_REASONING_EFFORT="low",
    )


def _material(**overrides) -> ValuesRequestMaterial:
    defaults = dict(
        context_id=5,
        schema_id=70,
        title="Стяжка пола 50 мм",
        unit_code="M2",
        article="12.03 Полы",
        paths=("Секция 1 / Полы", "Секция 2 / Полы"),
        parameters=(
            SchemaParameterIn(ordinal=1, name="Толщина", values=("50 мм", "80 мм")),
            SchemaParameterIn(ordinal=3, name="Материал", values=("бетон", "цементный раствор")),
        ),
    )
    defaults.update(overrides)
    return ValuesRequestMaterial(**defaults)


def _three_parameters() -> tuple[SchemaParameterIn, ...]:
    return (
        SchemaParameterIn(ordinal=1, name="Толщина", values=("50 мм", "80 мм")),
        SchemaParameterIn(ordinal=2, name="Класс", values=("В15", "В25")),
        SchemaParameterIn(ordinal=3, name="Материал", values=("бетон", "кирпич")),
    )


def _schema_block(rendered) -> str:
    return rendered.body["messages"][0]["content"][1]["text"]


def _enum(rendered) -> list[int]:
    schema = rendered.body["response_format"]["json_schema"]["schema"]
    return schema["properties"]["values"]["items"]["properties"]["ordinal"]["enum"]


class TestSubsetRender:
    def test_schema_block_and_format_enumeration_carry_only_requested_parameters(self):
        material = _material(parameters=_three_parameters(), requested_ordinals=(3, 1))

        rendered = render_values_request(material, settings=_settings())

        block = _schema_block(rendered)
        assert "Параметр 1: Толщина" in block and "Параметр 3: Материал" in block
        assert "Параметр 2" not in block and "Класс" not in block and "В25" not in block
        assert _enum(rendered) == [1, 3]

    def test_requested_parameters_go_in_ascending_order_whatever_the_input_order(self):
        material = _material(parameters=_three_parameters(), requested_ordinals=(3, 1))

        block = _schema_block(render_values_request(material, settings=_settings()))

        assert block.index("Параметр 1") < block.index("Параметр 3")

    def test_parameters_given_out_of_order_are_rendered_by_ordinal(self):
        """Порядок параметров в материале не задаёт порядок блока: и полный, и
        частичный запрос идут по возрастанию номера (как до фичи)."""
        shuffled = tuple(reversed(_three_parameters()))
        for requested in (None, (3, 1)):
            rendered = render_values_request(
                _material(parameters=shuffled, requested_ordinals=requested),
                settings=_settings(),
            )
            block = _schema_block(rendered)
            expected = [1, 2, 3] if requested is None else [1, 3]
            assert _enum(rendered) == expected
            positions = [block.index(f"Параметр {o}:") for o in expected]
            assert positions == sorted(positions)

    def test_single_requested_parameter(self):
        material = _material(parameters=_three_parameters(), requested_ordinals=(2,))

        rendered = render_values_request(material, settings=_settings())

        assert _enum(rendered) == [2]
        assert "Параметр 2: Класс" in _schema_block(rendered)
        assert "Параметр 1" not in _schema_block(rendered)

    def test_subset_changes_the_request_hash_against_the_full_set(self):
        full = render_values_request(_material(parameters=_three_parameters()), settings=_settings())
        subset = render_values_request(
            _material(parameters=_three_parameters(), requested_ordinals=(1, 2)),
            settings=_settings(),
        )
        assert full.request_hash != subset.request_hash

    def test_two_different_subsets_of_one_material_have_different_hashes(self):
        a = render_values_request(
            _material(parameters=_three_parameters(), requested_ordinals=(1,)),
            settings=_settings(),
        )
        b = render_values_request(
            _material(parameters=_three_parameters(), requested_ordinals=(3,)),
            settings=_settings(),
        )
        assert a.request_hash != b.request_hash

    def test_full_subset_without_ranges_keeps_the_pre_feature_hash(self):
        implicit = render_values_request(_material(), settings=_settings())
        explicit = render_values_request(_material(requested_ordinals=(1, 3)), settings=_settings())
        assert implicit.request_hash == explicit.request_hash == FULL_EXACT_HASH

    def test_a_requested_ordinal_that_is_not_a_parameter_is_not_rendered(self):
        material = _material(requested_ordinals=(1, 2, 3))

        rendered = render_values_request(material, settings=_settings())

        assert _enum(rendered) == [1, 3]


class TestRangeRuleLine:
    def _range_parameter(self, ordinal=2):
        return SchemaParameterIn(
            ordinal=ordinal, name="Толщина", values=("до 120", "120–140", "140 и более"),
            value_kind="range",
        )

    def test_the_rule_is_in_the_section_of_the_range_parameter_only(self):
        material = _material(
            parameters=(
                SchemaParameterIn(ordinal=1, name="Материал", values=("бетон", "кирпич")),
                self._range_parameter(2),
                SchemaParameterIn(ordinal=3, name="Класс", values=("В15", "В25")),
            )
        )

        block = _schema_block(render_values_request(material, settings=_settings()))

        assert block.count(RANGE_RULE_LINE) == 1
        sections = block.split("\n\n")
        with_rule = [s for s in sections if RANGE_RULE_LINE in s]
        assert len(with_rule) == 1 and with_rule[0].startswith("Параметр 2: Толщина")

    def test_the_rule_follows_the_value_list_of_its_section(self):
        material = _material(parameters=(self._range_parameter(1),))

        block = _schema_block(render_values_request(material, settings=_settings()))

        assert block.endswith(f"- 140 и более\n{RANGE_RULE_LINE}")

    def test_a_range_parameter_outside_the_subset_brings_no_rule(self):
        material = _material(
            parameters=(
                SchemaParameterIn(ordinal=1, name="Материал", values=("бетон", "кирпич")),
                self._range_parameter(2),
            ),
            requested_ordinals=(1,),
        )

        block = _schema_block(render_values_request(material, settings=_settings()))

        assert RANGE_RULE_LINE not in block

    def test_exact_parameters_only_never_carry_the_rule(self):
        block = _schema_block(render_values_request(_material(), settings=_settings()))
        assert RANGE_RULE_LINE not in block

    def test_the_range_rule_moves_the_hash_of_an_otherwise_equal_request(self):
        exact = _material(parameters=(SchemaParameterIn(ordinal=1, name="Т", values=("а", "б")),))
        ranged = _material(
            parameters=(SchemaParameterIn(ordinal=1, name="Т", values=("а", "б"), value_kind="range"),)
        )
        assert (
            render_values_request(exact, settings=_settings()).request_hash
            != render_values_request(ranged, settings=_settings()).request_hash
        )


class TestRoleStaysOutOfTheRequest:
    def test_no_role_word_reaches_the_body(self):
        rendered = render_values_request(_material(parameters=_three_parameters()), settings=_settings())
        text = json.dumps(rendered.body, ensure_ascii=False)
        for word in ("secondary", "главн", "второстеп", "роль параметра"):
            assert word not in text.lower()


def _answer(*objects) -> str:
    return json.dumps({"values": list(objects)}, ensure_ascii=False)


def _value(ordinal, value="50 мм"):
    return {"ordinal": ordinal, "kind": "value", "value": value, "source": "name"}


class TestParseByRequestedParameters:
    PARAMETERS = _three_parameters()

    def test_answer_for_exactly_the_requested_parameters_is_accepted(self):
        requested = (self.PARAMETERS[0], self.PARAMETERS[2])
        answer = parse_values_answer(
            _answer(_value(3, "бетон"), _value(1, "50 мм")), requested
        )
        assert [item.ordinal for item in answer.items] == [1, 3]

    def test_answer_that_covers_the_whole_schema_but_asks_for_a_subset_is_refused(self):
        requested = (self.PARAMETERS[0],)
        with pytest.raises(AnswerSchemaError) as raised:
            parse_values_answer(_answer(_value(1), _value(2, "В15")), requested)
        assert raised.value.code == "bad_ordinal"

    def test_answer_that_misses_a_requested_parameter_is_refused(self):
        requested = (self.PARAMETERS[0], self.PARAMETERS[2])
        with pytest.raises(AnswerSchemaError) as raised:
            parse_values_answer(_answer(_value(1)), requested)
        assert raised.value.code == "bad_ordinal"

    def test_value_is_checked_against_the_list_of_its_requested_parameter(self):
        requested = (self.PARAMETERS[2],)
        with pytest.raises(AnswerSchemaError) as raised:
            parse_values_answer(_answer(_value(3, "50 мм")), requested)
        assert raised.value.code == "value_not_in_list"
