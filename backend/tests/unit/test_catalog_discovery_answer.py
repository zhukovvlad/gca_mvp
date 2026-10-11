"""Разбор ответа открытия семей (спека 3б §2.3, «Ответ» и «Разбор»).

Каждое правило разбора — схемная ошибка отдельным входом; рядом с каждой
ошибкой стоит соседний вход, который правило пропускает."""
from __future__ import annotations

import json

import pytest

from services.semantic_answer import AnswerSchemaError
from services.variant_answer import (
    DiscoveryAnswer,
    DiscoveryGroup,
    DiscoverySent,
    parse_discovery_answer,
)

#: Отправлено: пять имён, активные семьи 10, 11, 12 (11 и 12 без категории),
#: справочник из категорий 1 и 2.
SENT = DiscoverySent(
    names_count=5,
    active_family_ids=frozenset({10, 11, 12}),
    uncategorized_family_ids=frozenset({11, 12}),
    category_ids=frozenset({1, 2}),
)


def _new(names, *, title="Новая семья", definition="Определение", category_id=1, similar=None):
    return {
        "family_id": None,
        "title": title,
        "definition": definition,
        "category_id": category_id,
        "similar_family_id": similar,
        "names": names,
    }


def _existing(family_id, names, **overrides):
    group = {
        "family_id": family_id,
        "title": None,
        "definition": None,
        "category_id": None,
        "similar_family_id": None,
        "names": names,
    }
    group.update(overrides)
    return group


def _raw(groups, not_work=(), family_categories=()):
    return json.dumps(
        {
            "groups": groups,
            "not_work": list(not_work),
            "family_categories": [
                {"family_id": f, "category_id": c} for f, c in family_categories
            ],
        },
        ensure_ascii=False,
    )


def _expect(code, raw, sent=SENT):
    with pytest.raises(AnswerSchemaError) as raised:
        parse_discovery_answer(raw, sent)
    assert raised.value.code == code, raised.value.detail


# ---------------------------------------------------------------------------
#  Разбор принимает верный ответ
# ---------------------------------------------------------------------------

class TestAccepted:
    def test_full_answer_is_parsed_into_typed_groups(self):
        raw = _raw(
            [_new([2, 1], similar=10), _existing(10, [3])],
            not_work=[5, 4],
            family_categories=[(11, 2), (12, 1)],
        )

        answer = parse_discovery_answer(raw, SENT)

        assert answer == DiscoveryAnswer(
            groups=(
                DiscoveryGroup(
                    family_id=None, title="Новая семья", definition="Определение",
                    category_id=1, similar_family_id=10, names=(1, 2),
                ),
                DiscoveryGroup(
                    family_id=10, title=None, definition=None, category_id=None,
                    similar_family_id=None, names=(3,),
                ),
            ),
            not_work=(4, 5),
            family_categories=((11, 2), (12, 1)),
            unassigned=(),
        )

    def test_skipped_numbers_are_unassigned_ascending_not_an_error(self):
        raw = _raw([_new([4])], not_work=[2])

        answer = parse_discovery_answer(raw, SENT)

        assert answer.unassigned == (1, 3, 5)

    def test_skipped_uncategorized_family_is_not_an_error(self):
        answer = parse_discovery_answer(_raw([_new([1, 2, 3, 4, 5])]), SENT)

        assert answer.family_categories == ()
        assert answer.unassigned == ()

    def test_answer_without_groups_is_allowed_when_everything_is_not_work(self):
        answer = parse_discovery_answer(_raw([], not_work=[1, 2, 3, 4, 5]), SENT)

        assert answer.groups == ()
        assert answer.not_work == (1, 2, 3, 4, 5)

    def test_fenced_and_bare_answers_parse_identically(self):
        bare = _raw([_new([1])], not_work=[2])
        fenced = f"```json\n{bare}\n```"

        assert parse_discovery_answer(fenced, SENT) == parse_discovery_answer(bare, SENT)

    def test_new_group_may_have_no_similar_family(self):
        answer = parse_discovery_answer(_raw([_new([1], similar=None)]), SENT)

        assert answer.groups[0].similar_family_id is None

    def test_title_is_returned_as_it_came(self):
        answer = parse_discovery_answer(_raw([_new([1], title="  Пол  ")]), SENT)

        assert answer.groups[0].title == "  Пол  "


# ---------------------------------------------------------------------------
#  Обрамление
# ---------------------------------------------------------------------------

class TestFraming:
    def test_text_around_json_is_schema_error(self):
        bare = _raw([_new([1])])

        with pytest.raises(AnswerSchemaError):
            parse_discovery_answer(f"Вот ответ: {bare}", SENT)
        with pytest.raises(AnswerSchemaError):
            parse_discovery_answer(f"{bare}\nГотово.", SENT)

    def test_not_json_is_schema_error(self):
        _expect("not_json", "это не json")


# ---------------------------------------------------------------------------
#  Ключи и типы
# ---------------------------------------------------------------------------

class TestKeysAndTypes:
    def test_extra_top_level_key_is_bad_type(self):
        obj = json.loads(_raw([_new([1])]))
        obj["comment"] = "лишнее"

        _expect("bad_type", json.dumps(obj, ensure_ascii=False))

    def test_extra_group_key_is_bad_type(self):
        group = _new([1])
        group["note"] = "лишнее"

        _expect("bad_type", _raw([group]))

    def test_extra_family_categories_key_is_bad_type(self):
        obj = json.loads(_raw([_new([1])]))
        obj["family_categories"] = [{"family_id": 11, "category_id": 1, "x": 1}]

        _expect("bad_type", json.dumps(obj))

    def test_missing_top_level_key_is_missing_field(self):
        obj = json.loads(_raw([_new([1])]))
        del obj["not_work"]

        _expect("missing_field", json.dumps(obj, ensure_ascii=False))

    def test_missing_group_key_is_missing_field(self):
        group = _new([1])
        del group["similar_family_id"]

        _expect("missing_field", _raw([group]))

    def test_groups_not_a_list_is_bad_type(self):
        obj = json.loads(_raw([_new([1])]))
        obj["groups"] = {}

        _expect("bad_type", json.dumps(obj, ensure_ascii=False))

    def test_group_not_an_object_is_bad_type(self):
        obj = json.loads(_raw([_new([1])]))
        obj["groups"] = [7]

        _expect("bad_type", json.dumps(obj, ensure_ascii=False))

    @pytest.mark.parametrize("bad", ["1", 1.5, True, None])
    def test_name_number_not_an_integer_is_bad_type(self, bad):
        _expect("bad_type", _raw([_new([bad])]))

    @pytest.mark.parametrize("bad", ["1", 1.5, True, None])
    def test_not_work_number_not_an_integer_is_bad_type(self, bad):
        _expect("bad_type", _raw([_new([1])], not_work=[bad]))

    def test_title_not_a_string_is_bad_type(self):
        _expect("bad_type", _raw([_new([1], title=7)]))

    def test_definition_not_a_string_is_bad_type(self):
        _expect("bad_type", _raw([_new([1], definition=7)]))

    def test_family_id_not_an_integer_is_bad_type(self):
        _expect("bad_type", _raw([_existing("10", [1])]))

    def test_family_categories_family_id_not_an_integer_is_bad_type(self):
        obj = json.loads(_raw([_new([1])]))
        obj["family_categories"] = [{"family_id": "11", "category_id": 1}]

        _expect("bad_type", json.dumps(obj))


# ---------------------------------------------------------------------------
#  Новый черновик (family_id IS NULL)
# ---------------------------------------------------------------------------

class TestNewDraft:
    @pytest.mark.parametrize("title", [None, "", "   ", "\t\n"])
    def test_without_title_is_empty_value(self, title):
        _expect("empty_value", _raw([_new([1], title=title)]))

    @pytest.mark.parametrize("definition", [None, "", "   "])
    def test_without_definition_is_empty_value(self, definition):
        _expect("empty_value", _raw([_new([1], definition=definition)]))

    @pytest.mark.parametrize("category", [3, 0, None])
    def test_category_outside_the_reference_is_unknown_category(self, category):
        _expect("unknown_category", _raw([_new([1], category_id=category)]))

    def test_each_sent_category_is_accepted(self):
        for category in (1, 2):
            parse_discovery_answer(_raw([_new([1], category_id=category)]), SENT)

    @pytest.mark.parametrize("similar", [99, 0])
    def test_similar_family_outside_active_is_unknown_family(self, similar):
        _expect("unknown_family", _raw([_new([1], similar=similar)]))

    def test_similar_family_from_active_is_accepted(self):
        for similar in (10, 11, 12):
            parse_discovery_answer(_raw([_new([1], similar=similar)]), SENT)


# ---------------------------------------------------------------------------
#  Группа активной семьи (family_id IS NOT NULL)
# ---------------------------------------------------------------------------

class TestExistingFamilyGroup:
    @pytest.mark.parametrize(
        "field, value",
        [
            ("title", "Имя"),
            ("definition", "Определение"),
            ("category_id", 1),
            ("similar_family_id", 11),
        ],
    )
    def test_filled_extra_field_is_unexpected_value(self, field, value):
        _expect("unexpected_value", _raw([_existing(10, [1], **{field: value})]))

    def test_family_outside_the_sent_ones_is_unknown_family(self):
        _expect("unknown_family", _raw([_existing(99, [1])]))

    def test_one_family_in_two_groups_is_duplicate_family(self):
        _expect("duplicate_family", _raw([_existing(10, [1]), _existing(10, [2])]))

    def test_two_different_families_are_accepted(self):
        parse_discovery_answer(_raw([_existing(10, [1]), _existing(11, [2])]), SENT)

    def test_empty_blank_title_is_still_unexpected_value(self):
        # Пустая строка не `null`: группа активной семьи несёт четыре `null`.
        _expect("unexpected_value", _raw([_existing(10, [1], title="")]))


# ---------------------------------------------------------------------------
#  Номера имён
# ---------------------------------------------------------------------------

class TestNameNumbers:
    @pytest.mark.parametrize("number", [0, 6, -1])
    def test_number_outside_range_in_a_group_is_bad_index(self, number):
        _expect("bad_index", _raw([_new([number])]))

    @pytest.mark.parametrize("number", [0, 6])
    def test_number_outside_range_in_not_work_is_bad_index(self, number):
        _expect("bad_index", _raw([_new([1])], not_work=[number]))

    def test_boundaries_one_and_n_are_accepted(self):
        parse_discovery_answer(_raw([_new([1, 5])]), SENT)

    def test_number_in_two_groups_is_duplicate_index(self):
        _expect("duplicate_index", _raw([_new([1, 2]), _new([2, 3])]))

    def test_number_in_group_and_not_work_is_duplicate_index(self):
        _expect("duplicate_index", _raw([_new([1, 2])], not_work=[2]))

    def test_number_twice_inside_one_group_is_duplicate_index(self):
        _expect("duplicate_index", _raw([_new([1, 1])]))

    def test_number_twice_inside_not_work_is_duplicate_index(self):
        _expect("duplicate_index", _raw([_new([1])], not_work=[3, 3]))

    def test_empty_names_is_bad_count(self):
        _expect("bad_count", _raw([_new([])]))

    def test_empty_names_of_an_existing_family_group_is_bad_count(self):
        _expect("bad_count", _raw([_existing(10, [])]))


# ---------------------------------------------------------------------------
#  family_categories
# ---------------------------------------------------------------------------

class TestFamilyCategories:
    def test_family_already_categorized_is_unknown_family(self):
        _expect("unknown_family", _raw([_new([1])], family_categories=[(10, 1)]))

    def test_family_outside_active_is_unknown_family(self):
        _expect("unknown_family", _raw([_new([1])], family_categories=[(99, 1)]))

    def test_repeated_family_is_duplicate_family(self):
        _expect(
            "duplicate_family",
            _raw([_new([1])], family_categories=[(11, 1), (11, 2)]),
        )

    @pytest.mark.parametrize("category", [3, 0])
    def test_category_outside_the_reference_is_unknown_category(self, category):
        _expect("unknown_category", _raw([_new([1])], family_categories=[(11, category)]))

    def test_uncategorized_family_with_each_category_is_accepted(self):
        for family_id, category in ((11, 1), (12, 2)):
            answer = parse_discovery_answer(
                _raw([_new([1])], family_categories=[(family_id, category)]), SENT
            )
            assert answer.family_categories == ((family_id, category),)


class TestDegenerateSent:
    def test_unit_without_uncategorized_families_rejects_any_family_category(self):
        sent = DiscoverySent(
            names_count=1,
            active_family_ids=frozenset({10}),
            uncategorized_family_ids=frozenset(),
            category_ids=frozenset({1}),
        )

        _expect("unknown_family", _raw([_new([1])], family_categories=[(10, 1)]), sent)

    def test_only_categories_launch_answers_with_no_names(self):
        sent = DiscoverySent(
            names_count=0,
            active_family_ids=frozenset({10}),
            uncategorized_family_ids=frozenset({10}),
            category_ids=frozenset({1}),
        )

        answer = parse_discovery_answer(_raw([], family_categories=[(10, 1)]), sent)

        assert answer.family_categories == ((10, 1),)
        assert answer.unassigned == ()
