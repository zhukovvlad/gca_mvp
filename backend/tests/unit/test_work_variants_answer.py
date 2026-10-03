"""Строгий разбор ответов моделей заданий `family_schema` и `context_values`
(спека `2026-10-02-catalog-variants-design.md` §2.3, §2.6, шаги (1а) и (2))."""
from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from services.semantic_answer import AnswerSchemaError
from services.variant_answer import (
    SCHEMA_RESPONSE_FORMAT,
    VALUES_RESPONSE_FORMAT,
    SchemaAnswer,
    ValueItem,
    ValuesAnswer,
    parse_schema_answer,
    parse_values_answer,
)
from services.variant_request import SchemaParameterIn


def _param(ordinal: int, name: str, values: list[str]) -> dict:
    return {"ordinal": ordinal, "name": name, "values": values}


def _schema_raw(parameters: list) -> str:
    return json.dumps({"parameters": parameters}, ensure_ascii=False)


def _expect(code: str, fn, *args) -> AnswerSchemaError:
    with pytest.raises(AnswerSchemaError) as info:
        fn(*args)
    assert info.value.code == code, info.value.detail
    return info.value


# ---------------------------------------------------------------------------
# Соответствие формату (без jsonschema: проверка по полям констант)
# ---------------------------------------------------------------------------

def _conforms(node: dict, value: object) -> bool:
    """Минимальная проверка по подмножеству JSON Schema, которое используют
    константы формата: type, enum, properties, required, additionalProperties,
    items."""
    kind = node.get("type")
    kinds = kind if isinstance(kind, list) else [kind]

    def has_type(t: str) -> bool:
        if t == "null":
            return value is None
        if t == "string":
            return isinstance(value, str)
        if t == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if t == "object":
            return isinstance(value, dict)
        if t == "array":
            return isinstance(value, list)
        raise AssertionError(f"неподдержанный тип {t}")

    if not any(has_type(t) for t in kinds):
        return False
    if "enum" in node and value not in node["enum"]:
        return False
    if isinstance(value, dict):
        props = node.get("properties", {})
        if any(key not in value for key in node.get("required", [])):
            return False
        if node.get("additionalProperties") is False and set(value) - set(props):
            return False
        return all(_conforms(props[k], v) for k, v in value.items() if k in props)
    if isinstance(value, list):
        return all(_conforms(node["items"], item) for item in value)
    return True


def _schema_of(fmt: dict) -> dict:
    return fmt["json_schema"]["schema"]


class TestResponseFormats:
    def test_both_formats_are_strict(self):
        assert SCHEMA_RESPONSE_FORMAT["json_schema"]["strict"] is True
        assert VALUES_RESPONSE_FORMAT["json_schema"]["strict"] is True

    def test_checker_rejects_extra_key_and_wrong_type(self):
        # Предусловие: сам проверяльщик способен отвергнуть нарушение формата.
        schema = _schema_of(SCHEMA_RESPONSE_FORMAT)
        assert not _conforms(schema, {"parameters": [], "x": 1})
        assert not _conforms(schema, {"parameters": [_param(4, "a", ["b"])]})
        assert not _conforms(schema, {"parameters": "a"})

    def test_checker_rejects_values_format_violations(self):
        # Предусловие для формата значений: перечисления `kind`/`source`,
        # тип-объединение `value` и обязательные ключи проверяльщик видит.
        schema = _schema_of(VALUES_RESPONSE_FORMAT)
        ok = {"ordinal": 1, "kind": "none", "value": None, "source": None}
        assert _conforms(schema, {"values": [ok]})
        for bad in (
            {**ok, "kind": "bogus"},
            {**ok, "source": "title"},
            {**ok, "value": 5},
            {**ok, "ordinal": 4},
            {k: v for k, v in ok.items() if k != "source"},
        ):
            assert not _conforms(schema, {"values": [bad]}), bad

    def test_schema_answer_valid_by_format_but_rule_violating_is_rejected(self):
        # Имя из пробелов, 9 значений, дубль ordinal, 4 параметра: формат всё
        # это пропускает, разбор отвергает.
        schema = _schema_of(SCHEMA_RESPONSE_FORMAT)
        cases = {
            "empty_value": {"parameters": [_param(1, "   ", ["a"])]},
            "bad_count": {"parameters": [_param(1, "n", [str(i) for i in range(9)])]},
            "bad_ordinal": {"parameters": [_param(1, "a", ["x"]), _param(1, "b", ["y"])]},
        }
        for code, payload in cases.items():
            assert _conforms(schema, payload), code
            _expect(code, parse_schema_answer, json.dumps(payload))
        four = {"parameters": [_param(1, "a", ["x"]), _param(2, "b", ["x"]),
                               _param(3, "c", ["x"]), _param(3, "d", ["x"])]}
        assert _conforms(schema, four)
        _expect("bad_count", parse_schema_answer, json.dumps(four))

    def test_values_answer_valid_by_format_but_rule_violating_is_rejected(self):
        schema = _schema_of(VALUES_RESPONSE_FORMAT)
        params = [SchemaParameterIn(1, "p", ("a", "b"))]
        cases = {
            "bad_kind": [{"ordinal": 1, "kind": "none", "value": "a", "source": None}],
            "value_not_in_list": [{"ordinal": 1, "kind": "value", "value": "z", "source": "name"}],
            "bad_ordinal": [{"ordinal": 2, "kind": "none", "value": None, "source": None}],
            "empty_value": [{"ordinal": 1, "kind": "new", "value": " ", "source": "path"}],
        }
        for code, items in cases.items():
            payload = {"values": items}
            assert _conforms(schema, payload), code
            _expect(code, parse_values_answer, json.dumps(payload), params)


# ---------------------------------------------------------------------------
# parse_schema_answer
# ---------------------------------------------------------------------------

class TestParseSchemaAnswer:
    def test_zero_parameters_is_legal(self):
        answer = parse_schema_answer('{"parameters": []}')
        assert answer == SchemaAnswer(parameters=())

    def test_three_parameters_and_value_bounds(self):
        raw = _schema_raw([
            _param(1, "Материал", ["бетон"]),
            _param(2, "Толщина", [str(i) for i in range(8)]),
            _param(3, "Класс", ["B15", "B25"]),
        ])
        answer = parse_schema_answer(raw)
        assert [p.ordinal for p in answer.parameters] == [1, 2, 3]
        assert answer.parameters[0] == SchemaParameterIn(1, "Материал", ("бетон",))
        assert len(answer.parameters[1].values) == 8
        assert isinstance(answer.parameters, tuple)
        assert isinstance(answer.parameters[0].values, tuple)

    def test_parameters_are_returned_ordered_by_ordinal(self):
        raw = _schema_raw([_param(2, "b", ["x"]), _param(1, "a", ["y"])])
        assert [p.name for p in parse_schema_answer(raw).parameters] == ["a", "b"]

    def test_four_parameters_is_bad_count(self):
        raw = _schema_raw([_param(i, f"p{i}", ["x"]) for i in (1, 2, 3, 1)])
        _expect("bad_count", parse_schema_answer, raw)

    def test_zero_values_is_bad_count(self):
        _expect("bad_count", parse_schema_answer, _schema_raw([_param(1, "a", [])]))

    def test_nine_values_is_bad_count(self):
        raw = _schema_raw([_param(1, "a", [str(i) for i in range(9)])])
        _expect("bad_count", parse_schema_answer, raw)

    def test_blank_name_is_empty_value(self):
        _expect("empty_value", parse_schema_answer, _schema_raw([_param(1, "", ["x"])]))
        _expect("empty_value", parse_schema_answer, _schema_raw([_param(1, " \t ", ["x"])]))

    def test_blank_value_is_empty_value(self):
        _expect("empty_value", parse_schema_answer, _schema_raw([_param(1, "a", ["x", ""])]))
        _expect("empty_value", parse_schema_answer, _schema_raw([_param(1, "a", ["  "])]))

    def test_duplicate_ordinal_is_bad_ordinal(self):
        raw = _schema_raw([_param(1, "a", ["x"]), _param(1, "b", ["y"])])
        _expect("bad_ordinal", parse_schema_answer, raw)

    @pytest.mark.parametrize("ordinal", [0, 4, -1])
    def test_ordinal_out_of_range_is_bad_ordinal(self, ordinal):
        _expect("bad_ordinal", parse_schema_answer, _schema_raw([_param(ordinal, "a", ["x"])]))

    def test_out_of_range_ordinal_is_reported_before_the_rest_of_the_item(self):
        # Вердикт «вне 1-3» без проверки диапазона дал бы и сверка 1..N в
        # конце; проверка диапазона на месте решает КОД отказа, когда у того же
        # элемента есть и другой дефект: диагностика называет номер, а не имя.
        _expect("bad_ordinal", parse_schema_answer, _schema_raw([_param(4, " ", ["x"])]))

    def test_ordinal_gap_is_bad_ordinal(self):
        raw = _schema_raw([_param(1, "a", ["x"]), _param(3, "b", ["y"])])
        _expect("bad_ordinal", parse_schema_answer, raw)
        _expect("bad_ordinal", parse_schema_answer, _schema_raw([_param(2, "a", ["x"])]))

    def test_duplicate_json_key_is_not_json(self):
        raw = '{"parameters": [], "parameters": []}'
        _expect("not_json", parse_schema_answer, raw)

    def test_duplicate_key_inside_item_is_not_json(self):
        raw = '{"parameters": [{"ordinal": 1, "ordinal": 1, "name": "a", "values": ["x"]}]}'
        _expect("not_json", parse_schema_answer, raw)

    def test_text_after_object_is_extra_text(self):
        _expect("extra_text", parse_schema_answer, '{"parameters": []} спасибо')

    def test_text_before_object_is_rejected_as_not_json(self):
        # Объект из текста не извлекается: разбор идёт с позиции 0 (как у фичи 2).
        _expect("not_json", parse_schema_answer, 'Вот ответ: {"parameters": []}')

    def test_json_fence_is_accepted_other_fence_is_extra_text(self):
        assert parse_schema_answer('```json\n{"parameters": []}\n```').parameters == ()
        _expect("extra_text", parse_schema_answer, '```yaml\n{"parameters": []}\n```')

    def test_garbage_is_not_json(self):
        _expect("not_json", parse_schema_answer, "не json")

    def test_non_object_is_bad_type(self):
        _expect("bad_type", parse_schema_answer, "[]")

    def test_missing_parameters_key(self):
        _expect("missing_field", parse_schema_answer, "{}")

    def test_missing_item_field(self):
        _expect("missing_field", parse_schema_answer,
                '{"parameters": [{"ordinal": 1, "name": "a"}]}')

    def test_extra_top_level_key_is_bad_type(self):
        _expect("bad_type", parse_schema_answer, '{"parameters": [], "note": "x"}')

    def test_extra_key_inside_item_is_bad_type(self):
        raw = '{"parameters": [{"ordinal": 1, "name": "a", "values": ["x"], "extra": 1}]}'
        _expect("bad_type", parse_schema_answer, raw)

    @pytest.mark.parametrize("raw", [
        '{"parameters": "a"}',
        '{"parameters": [1]}',
        '{"parameters": [{"ordinal": "1", "name": "a", "values": ["x"]}]}',
        '{"parameters": [{"ordinal": true, "name": "a", "values": ["x"]}]}',
        '{"parameters": [{"ordinal": 1.0, "name": "a", "values": ["x"]}]}',
        '{"parameters": [{"ordinal": 1, "name": 5, "values": ["x"]}]}',
        '{"parameters": [{"ordinal": 1, "name": "a", "values": "x"}]}',
        '{"parameters": [{"ordinal": 1, "name": "a", "values": [5]}]}',
        '{"parameters": [{"ordinal": 1, "name": "a", "values": [null]}]}',
    ], ids=[
        "parameters_str", "item_int", "ordinal_str", "ordinal_bool", "ordinal_float",
        "name_int", "values_str", "value_int", "value_null",
    ])
    def test_wrong_types_are_bad_type(self, raw):
        _expect("bad_type", parse_schema_answer, raw)

    def test_object_instead_of_parameters_list_is_bad_type(self):
        # Строка `"a"` выше отвергается и без проверки массива — на первом
        # символе как «не объект»; пустой объект без неё прошёл бы как законная
        # схема из нуля параметров.
        _expect("bad_type", parse_schema_answer, '{"parameters": {}}')

    def test_result_is_frozen(self):
        answer = parse_schema_answer('{"parameters": []}')
        with pytest.raises(FrozenInstanceError):
            answer.parameters = ()  # type: ignore[misc]


# ---------------------------------------------------------------------------
# parse_values_answer
# ---------------------------------------------------------------------------

def _params() -> list[SchemaParameterIn]:
    return [
        SchemaParameterIn(1, "Материал", ("бетон", "кирпич")),
        SchemaParameterIn(2, "Толщина", ("100", "200")),
    ]


def _item(ordinal: int, kind: str, value: str | None, source: str | None) -> dict:
    return {"ordinal": ordinal, "kind": kind, "value": value, "source": source}


def _values_raw(items: list) -> str:
    return json.dumps({"values": items}, ensure_ascii=False)


def _one(item: dict, values: tuple[str, ...] = ("бетон", "кирпич")) -> ValuesAnswer:
    return parse_values_answer(_values_raw([item]), [SchemaParameterIn(1, "p", values)])


class TestParseValuesAnswerSet:
    def test_full_set_in_any_order_is_returned_by_ordinal(self):
        raw = _values_raw([_item(2, "none", None, None), _item(1, "value", "бетон", "name")])
        answer = parse_values_answer(raw, _params())
        assert [i.ordinal for i in answer.items] == [1, 2]
        assert answer.items[0] == ValueItem(1, "value", "бетон", "name")
        assert answer.items[1] == ValueItem(2, "none", None, None)
        assert isinstance(answer.items, tuple)

    def test_empty_schema_and_empty_answer_is_legal(self):
        assert parse_values_answer('{"values": []}', []) == ValuesAnswer(items=())

    def test_nonempty_answer_for_empty_schema_is_bad_ordinal(self):
        raw = _values_raw([_item(1, "none", None, None)])
        _expect("bad_ordinal", parse_values_answer, raw, [])

    def test_omitted_ordinal_is_bad_ordinal(self):
        raw = _values_raw([_item(1, "none", None, None)])
        _expect("bad_ordinal", parse_values_answer, raw, _params())

    def test_empty_answer_for_nonempty_schema_is_bad_ordinal(self):
        _expect("bad_ordinal", parse_values_answer, '{"values": []}', _params())

    def test_duplicate_ordinal_is_bad_ordinal(self):
        raw = _values_raw([_item(1, "none", None, None), _item(1, "none", None, None),
                           _item(2, "none", None, None)])
        _expect("bad_ordinal", parse_values_answer, raw, _params())

    def test_duplicate_ordinal_with_full_set_otherwise(self):
        # Число объектов равно числу параметров, но множество не совпадает.
        raw = _values_raw([_item(1, "none", None, None), _item(1, "none", None, None)])
        _expect("bad_ordinal", parse_values_answer, raw, _params())

    def test_foreign_ordinal_is_bad_ordinal(self):
        raw = _values_raw([_item(1, "none", None, None), _item(3, "none", None, None)])
        _expect("bad_ordinal", parse_values_answer, raw, _params())

    def test_extra_ordinal_beyond_schema_is_bad_ordinal(self):
        raw = _values_raw([_item(1, "none", None, None), _item(2, "none", None, None),
                           _item(3, "none", None, None)])
        _expect("bad_ordinal", parse_values_answer, raw, _params())

    @pytest.mark.parametrize("ordinal", [0, 4])
    def test_ordinal_out_of_range_is_bad_ordinal(self, ordinal):
        raw = _values_raw([_item(ordinal, "none", None, None)])
        _expect("bad_ordinal", parse_values_answer, raw, [SchemaParameterIn(1, "p", ("a",))])


class TestParseValuesAnswerKinds:
    def test_kind_value_in_list(self):
        assert _one(_item(1, "value", "кирпич", "path")).items == (
            ValueItem(1, "value", "кирпич", "path"),
        )

    def test_kind_new_with_value_and_source(self):
        assert _one(_item(1, "new", "гранит", "name")).items == (
            ValueItem(1, "new", "гранит", "name"),
        )

    def test_kind_new_equal_to_list_value_is_not_a_parse_error(self):
        # Канонизация совпавшего значения — дело записи результата, не разбора.
        assert _one(_item(1, "new", "бетон", "name")).items[0].kind == "new"

    def test_kind_conflict(self):
        assert _one(_item(1, "conflict", None, None)).items == (
            ValueItem(1, "conflict", None, None),
        )

    def test_kind_none(self):
        assert _one(_item(1, "none", None, None)).items == (ValueItem(1, "none", None, None),)

    @pytest.mark.parametrize("kind", ["Value", "unknown", "", "NONE"])
    def test_kind_outside_list_is_bad_kind(self, kind):
        _expect("bad_kind", _one, _item(1, kind, None, None))

    @pytest.mark.parametrize("kind", ["conflict", "none"])
    def test_source_forbidden_for_conflict_and_none(self, kind):
        _expect("bad_kind", _one, _item(1, kind, None, "name"))
        _expect("bad_kind", _one, _item(1, kind, None, "path"))

    @pytest.mark.parametrize("kind", ["conflict", "none"])
    def test_value_forbidden_for_conflict_and_none(self, kind):
        _expect("bad_kind", _one, _item(1, kind, "бетон", None))
        _expect("bad_kind", _one, _item(1, kind, "", None))

    @pytest.mark.parametrize("kind", ["value", "new"])
    def test_source_required_for_value_and_new(self, kind):
        _expect("bad_kind", _one, _item(1, kind, "бетон", None))

    def test_source_outside_name_path_is_bad_kind(self):
        _expect("bad_kind", _one, _item(1, "value", "бетон", "title"))

    @pytest.mark.parametrize("kind", ["value", "new"])
    def test_value_required_for_value_and_new(self, kind):
        _expect("empty_value", _one, _item(1, kind, None, "name"))
        _expect("empty_value", _one, _item(1, kind, "", "name"))
        _expect("empty_value", _one, _item(1, kind, "   ", "path"))

    def test_value_outside_list_is_value_not_in_list(self):
        _expect("value_not_in_list", _one, _item(1, "value", "гранит", "name"))

    def test_value_comparison_is_exact(self):
        _expect("value_not_in_list", _one, _item(1, "value", "Бетон", "name"))
        _expect("value_not_in_list", _one, _item(1, "value", " бетон", "name"))

    def test_value_compared_against_its_own_parameter_list(self):
        raw = _values_raw([_item(1, "value", "100", "name"), _item(2, "none", None, None)])
        _expect("value_not_in_list", parse_values_answer, raw, _params())

    def test_list_value_equal_to_path_conflict_is_legal_with_kind_value(self):
        item = _item(1, "value", "path_conflict", "name")
        assert _one(item, ("path_conflict", "x")).items[0].value == "path_conflict"

    def test_list_value_starting_with_new_prefix_is_legal_with_kind_value(self):
        item = _item(1, "value", "new:abc", "path")
        assert _one(item, ("new:abc",)).items[0].value == "new:abc"

    def test_path_conflict_not_in_list_is_a_plain_unknown_value(self):
        _expect("value_not_in_list", _one, _item(1, "value", "path_conflict", "name"))


class TestParseValuesAnswerEnvelope:
    def test_duplicate_json_key_is_not_json(self):
        _expect("not_json", parse_values_answer, '{"values": [], "values": []}', [])

    def test_duplicate_key_inside_item_is_not_json(self):
        raw = ('{"values": [{"ordinal": 1, "kind": "none", "kind": "none", '
               '"value": null, "source": null}]}')
        _expect("not_json", parse_values_answer, raw, [SchemaParameterIn(1, "p", ("a",))])

    def test_text_after_object_is_extra_text(self):
        _expect("extra_text", parse_values_answer, '{"values": []}\nготово', [])

    def test_text_before_object_is_rejected_as_not_json(self):
        _expect("not_json", parse_values_answer, 'ответ {"values": []}', [])

    def test_json_fence_is_accepted(self):
        assert parse_values_answer('```json\n{"values": []}\n```', []).items == ()

    def test_garbage_is_not_json(self):
        _expect("not_json", parse_values_answer, "ok", [])

    def test_non_object_is_bad_type(self):
        _expect("bad_type", parse_values_answer, '"x"', [])

    def test_missing_values_key(self):
        _expect("missing_field", parse_values_answer, "{}", [])

    def test_missing_item_field(self):
        raw = '{"values": [{"ordinal": 1, "kind": "none", "value": null}]}'
        _expect("missing_field", parse_values_answer, raw, [SchemaParameterIn(1, "p", ("a",))])

    def test_extra_top_level_key_is_bad_type(self):
        _expect("bad_type", parse_values_answer, '{"values": [], "note": 1}', [])

    def test_extra_key_inside_item_is_bad_type(self):
        raw = '{"values": [{"ordinal": 1, "kind": "none", "value": null, "source": null, "x": 1}]}'
        _expect("bad_type", parse_values_answer, raw, [SchemaParameterIn(1, "p", ("a",))])

    @pytest.mark.parametrize("raw", [
        '{"values": "a"}',
        '{"values": [1]}',
        '{"values": [{"ordinal": "1", "kind": "none", "value": null, "source": null}]}',
        '{"values": [{"ordinal": true, "kind": "none", "value": null, "source": null}]}',
        '{"values": [{"ordinal": 1, "kind": 5, "value": null, "source": null}]}',
        '{"values": [{"ordinal": 1, "kind": "new", "value": 5, "source": "name"}]}',
        '{"values": [{"ordinal": 1, "kind": "new", "value": "a", "source": 5}]}',
    ], ids=[
        "values_str", "item_int", "ordinal_str", "ordinal_bool", "kind_int",
        "value_int", "source_int",
    ])
    def test_wrong_types_are_bad_type(self, raw):
        _expect("bad_type", parse_values_answer, raw, [SchemaParameterIn(1, "p", ("a",))])

    def test_object_instead_of_values_list_is_bad_type(self):
        # При нулевой схеме пустой объект без проверки массива прошёл бы как
        # законный пустой ответ.
        _expect("bad_type", parse_values_answer, '{"values": {}}', [])


class TestResultsAreFrozen:
    def test_value_item_is_frozen(self):
        item = _one(_item(1, "none", None, None)).items[0]
        with pytest.raises(FrozenInstanceError):
            item.kind = "value"  # type: ignore[misc]

    def test_values_answer_is_frozen(self):
        answer = parse_values_answer('{"values": []}', [])
        with pytest.raises(FrozenInstanceError):
            answer.items = ()  # type: ignore[misc]
