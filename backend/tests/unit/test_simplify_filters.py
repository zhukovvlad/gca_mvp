"""Склейка значений и ответ `several` (спека упрощения каталога §2.4): чистое деление
текста на части, лемматизированная норма, разбор вида `several`, формат ответа и
промпт.

Эталоны записаны руками: части, виды и коды отказа — литералами.
"""
from __future__ import annotations

import json

import pytest

from services import variant_request
from services.semantic_answer import AnswerSchemaError
from services.value_filters import SplitCandidate, lemma_norm, split_candidate
from services.variant_answer import (
    VALUES_RESPONSE_FORMAT,
    ValueItem,
    parse_values_answer,
)
from services.variant_request import (
    VALUES_PROMPT,
    VALUES_PROMPT_VERSION,
    SchemaParameterIn,
    paths_hash_of,
)

# ---------------------------------------------------------------------------
#  split_candidate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind", "parts"),
    [
        (
            "балки, капители, трансферные конструкции", "all",
            ("балки", "капители", "трансферные конструкции"),
        ),
        ("Knauf П113 или Gyproc П-1М-1ГСП", "any", ("Knauf П113", "Gyproc П-1М-1ГСП")),
        ("плиты и балки", "all", ("плиты", "балки")),
        ("балки; капители", "all", ("балки", "капители")),
        ("балки / капители", "all", ("балки", "капители")),
        ("плиты/балки", "all", ("плиты", "балки")),
        ("А или Б или В", "any", ("А", "Б", "В")),
        ("А ИЛИ Б", "any", ("А", "Б")),
        ("ГО и ЧС", "all", ("ГО", "ЧС")),
        ("3,6 м и 4,2 м", "all", ("3,6 м", "4,2 м")),
        ("балки, , капители", "all", ("балки", "капители")),
    ],
)
def test_split_candidate_cuts_the_text_into_parts(text, kind, parts):
    assert split_candidate(text) == SplitCandidate(kind=kind, parts=parts)


@pytest.mark.parametrize(
    "text",
    [
        "3,6 м",
        "В12,5",
        '1/2"',
        "10/20",
        "ГО",
        "ориентир",
        "балки,",
        "краска и",
        "А или Б, В",
        "А, Б или В",
        "",
        "   ",
    ],
)
def test_split_candidate_keeps_a_whole_text_whole(text):
    assert split_candidate(text) is None


# ---------------------------------------------------------------------------
#  lemma_norm
# ---------------------------------------------------------------------------


def test_lemma_norm_equates_word_forms_of_one_construction():
    assert lemma_norm("плиты перекрытия") == lemma_norm("плита перекрытий") == "плита перекрытие"


def test_lemma_norm_keeps_different_constructions_apart():
    assert lemma_norm("плиты покрытий") == "плита покрытие"
    assert lemma_norm("плиты покрытий") != lemma_norm("перекрытия")


# ---------------------------------------------------------------------------
#  Разбор вида several
# ---------------------------------------------------------------------------

_PARAMS = [SchemaParameterIn(1, "Тип", ("балки", "плиты"))]


def _raw(**item):
    base = {"ordinal": 1, "kind": "several", "value": None, "source": "name",
            "values": ["балки", "плиты"], "combine": "all"}
    base.update(item)
    return json.dumps({"values": [base]})


def _fails(raw, code):
    with pytest.raises(AnswerSchemaError) as caught:
        parse_values_answer(raw, _PARAMS)
    assert caught.value.code == code


def test_a_several_item_is_parsed_with_its_values_and_combine():
    answer = parse_values_answer(_raw(combine="any", source="path"), _PARAMS)

    assert answer.items == (
        ValueItem(
            ordinal=1, kind="several", value=None, source="path",
            values=("балки", "плиты"), combine="any",
        ),
    )


def test_a_several_value_may_be_new():
    answer = parse_values_answer(_raw(values=["балки", "капители"]), _PARAMS)

    assert answer.items[0].values == ("балки", "капители")


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"combine": None}, "bad_kind"),
        ({"combine": "both"}, "bad_kind"),
        ({"values": ["балки"]}, "bad_count"),
        ({"values": []}, "bad_count"),
        ({"values": ["балки", "балки"]}, "bad_count"),
        ({"values": ["балки", " Балки "]}, "bad_count"),
        ({"values": ["балки", "   "]}, "empty_value"),
        ({"values": ["балки", 5]}, "bad_type"),
        ({"values": None}, "bad_type"),
        ({"value": "балки"}, "bad_kind"),
        ({"source": None}, "bad_kind"),
    ],
)
def test_a_malformed_several_item_is_a_schema_error(overrides, code):
    _fails(_raw(**overrides), code)


def test_several_without_the_combine_key_is_a_schema_error():
    raw = json.dumps({"values": [{
        "ordinal": 1, "kind": "several", "value": None, "source": "name",
        "values": ["балки", "плиты"],
    }]})

    _fails(raw, "bad_kind")


@pytest.mark.parametrize("overrides", [{"values": ["балки", "плиты"]}, {"combine": "all"}])
def test_values_and_combine_belong_to_several_only(overrides):
    raw = json.dumps({"values": [{
        "ordinal": 1, "kind": "none", "value": None, "source": None,
        "values": None, "combine": None, **overrides,
    }]})

    _fails(raw, "bad_kind")


def test_an_answer_of_the_old_shape_without_values_and_combine_still_parses():
    raw = json.dumps({"values": [
        {"ordinal": 1, "kind": "value", "value": "балки", "source": "name"},
    ]})

    [item] = parse_values_answer(raw, _PARAMS).items

    assert (item.kind, item.value, item.values, item.combine) == ("value", "балки", None, None)


def test_a_wire_answer_with_null_values_and_combine_parses_for_ordinary_kinds():
    raw = json.dumps({"values": [
        {"ordinal": 1, "kind": "new", "value": "капители", "source": "path",
         "values": None, "combine": None},
    ]})

    [item] = parse_values_answer(raw, _PARAMS).items

    assert (item.kind, item.value, item.values, item.combine) == ("new", "капители", None, None)


# ---------------------------------------------------------------------------
#  Формат ответа и промпт
# ---------------------------------------------------------------------------


def _item_schema():
    return VALUES_RESPONSE_FORMAT["json_schema"]["schema"]["properties"]["values"]["items"]


def test_the_format_names_several_and_lists_every_key_as_required():
    item = _item_schema()

    assert item["properties"]["kind"]["enum"] == ["value", "new", "conflict", "none", "several"]
    assert item["required"] == ["ordinal", "kind", "value", "source", "values", "combine"]
    assert item["additionalProperties"] is False


def test_the_format_spells_values_and_combine_through_any_of_for_null():
    properties = _item_schema()["properties"]

    assert properties["values"] == {
        "anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]
    }
    assert properties["combine"] == {
        "anyOf": [{"type": "string", "enum": ["all", "any"]}, {"type": "null"}]
    }


def test_the_values_prompt_is_version_two():
    assert VALUES_PROMPT_VERSION == 2


def test_the_values_prompt_states_the_several_and_conflict_rule():
    assert '"kind": "several"' in VALUES_PROMPT
    assert '"combine": "all"' in VALUES_PROMPT
    assert '"combine": "any"' in VALUES_PROMPT
    assert "только когда разные строки" in VALUES_PROMPT


def test_the_paths_hash_of_a_context_does_not_depend_on_the_prompt_or_the_format(monkeypatch):
    # Контексты не перезапрашиваются из-за смены промпта: отпечаток путей зависит
    # только от путей.
    paths = ()
    before = paths_hash_of(paths)

    monkeypatch.setattr(variant_request, "VALUES_PROMPT_VERSION", 99)
    monkeypatch.setattr(variant_request, "VALUES_PROMPT", "другой текст")
    monkeypatch.setattr(variant_request, "VALUES_RESPONSE_FORMAT", {"type": "other"})

    assert paths_hash_of(paths) == before
