"""Открытие порциями (спека 3б §2.3.1): нарезка имён, тело порции, разбор
ответа порции, сборка ответа и оценка.

Чистые функции, без базы: охват задан вручную, тело строит
`build_discovery_request`. Прохождение собранного ответа через `apply_discovery`
в базе — в интеграционном `test_catalog_discovery_chunks.py`."""
from __future__ import annotations

import copy
import dataclasses
import json
from decimal import Decimal

import pytest

from config import Settings
from services.family_discovery import (
    DRAFT_BLOCK_HEADER,
    DiscoveryCategory,
    DiscoveryName,
    DiscoveryScope,
    DraftRef,
    ScopeCounts,
    build_discovery_request,
    chunk_bounds,
    chunk_sent_of,
    draft_line_max_bytes,
    draft_refs_of,
    estimate_of,
    expected_of,
    render_discovery_chunk,
    reserve_of,
)
from services.semantic_answer import AnswerSchemaError
from services.semantic_request import CandidateFamily
from services.variant_answer import (
    ChunkAnswer,
    ChunkGroup,
    ChunkSent,
    DiscoveryAnswer,
    DiscoveryGroup,
    merge_chunks,
    parse_discovery_chunk,
)

_KEY = "k" * 32


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, SECRET_KEY=_KEY, **kw)


S = _settings()


# ---------------------------------------------------------------------------
#  Материал
# ---------------------------------------------------------------------------

FAMILIES = (
    CandidateFamily(id=10, title="Кладка", unit_code="м2", definition="Возведение стен"),
    CandidateFamily(id=11, title="Отделка", unit_code="м2", definition="Отделочные работы"),
)
CATEGORIES = (
    DiscoveryCategory(id=1, title="Работа", definition="Виды строительных работ"),
    DiscoveryCategory(id=2, title="Материал", definition="Закупка материалов"),
)


def _scope(names_count: int, *, unit_code="м2", uncategorized=(11,)) -> DiscoveryScope:
    names = tuple(
        DiscoveryName(
            index=i, title=f"Имя {i}", context_ids=(1000 + i,),
            articles=("01.01 Стены",), path="Раздел",
        )
        for i in range(1, names_count + 1)
    )
    return DiscoveryScope(
        unit_id=1,
        unit_code=unit_code,
        names=names,
        context_ids=frozenset(1000 + i for i in range(1, names_count + 1)),
        active_family_ids=(10, 11),
        uncategorized_family_ids=tuple(uncategorized),
        category_ids=(1, 2),
        counts=ScopeCounts(
            systems=0, new_family=0, bare=names_count, names=names_count,
            uncategorized_families=len(uncategorized),
        ),
    )


def _canonical(scope: DiscoveryScope, settings: Settings = S):
    return build_discovery_request(
        unit_code=scope.unit_code,
        names=scope.names,
        families=FAMILIES,
        categories=CATEGORIES,
        uncategorized_family_ids=scope.uncategorized_family_ids,
        settings=settings,
    )


def _chunk_body(scope, *, bounds, drafts=(), first=None, settings=S):
    return render_discovery_chunk(
        _canonical(scope, settings), scope, settings=settings, bounds=bounds,
        drafts=drafts, first=(bounds[0] == 1) if first is None else first,
    )


def _user(body: dict) -> str:
    return body["messages"][1]["content"]


# ---------------------------------------------------------------------------
#  Нарезка
# ---------------------------------------------------------------------------

class TestChunkBounds:
    def test_zero_names_is_one_chunk_without_names(self):
        assert chunk_bounds(0, 100) == [(1, 0)]

    def test_one_name_is_one_chunk(self):
        assert chunk_bounds(1, 100) == [(1, 1)]

    def test_exactly_one_chunk_has_both_edges_at_the_limit(self):
        assert chunk_bounds(100, 100) == [(1, 100)]

    def test_one_name_over_the_limit_opens_a_second_chunk_with_that_name(self):
        assert chunk_bounds(101, 100) == [(1, 100), (101, 101)]

    def test_1048_names_are_eleven_chunks_in_order_without_gaps(self):
        bounds = chunk_bounds(1048, 100)

        assert len(bounds) == 11
        assert bounds[0] == (1, 100)
        assert bounds[1] == (101, 200)
        assert bounds[-1] == (1001, 1048)
        assert all(prev[1] + 1 == nxt[0] for prev, nxt in zip(bounds, bounds[1:], strict=False))

    def test_the_size_is_a_parameter(self):
        assert chunk_bounds(7, 3) == [(1, 3), (4, 6), (7, 7)]


# ---------------------------------------------------------------------------
#  Тело порции
# ---------------------------------------------------------------------------

class TestChunkBody:
    def test_first_chunk_user_text_exact_layout(self):
        scope = _scope(3)

        body = _chunk_body(scope, bounds=(1, 3))

        assert _user(body) == (
            "ЕДИНИЦА: м2\n"
            "СЕМЬИ БЕЗ КАТЕГОРИИ: 11\n"
            "ИМЕНА 1–3 ИЗ 3:\n"
            "1. Имя 1 | статьи: 01.01 Стены | разделы: Раздел\n"
            "2. Имя 2 | статьи: 01.01 Стены | разделы: Раздел\n"
            "3. Имя 3 | статьи: 01.01 Стены | разделы: Раздел"
        )

    def test_later_chunk_has_no_uncategorized_list_and_global_numbers(self):
        scope = _scope(5)
        s = _settings(SEMANTIC_DISCOVERY_CHUNK_NAMES=2)

        body = _chunk_body(scope, bounds=(3, 4), settings=s)

        assert _user(body) == (
            "ЕДИНИЦА: м2\n"
            "ИМЕНА 3–4 ИЗ 5:\n"
            "3. Имя 3 | статьи: 01.01 Стены | разделы: Раздел\n"
            "4. Имя 4 | статьи: 01.01 Стены | разделы: Раздел"
        )

    def test_draft_block_lists_exactly_the_given_drafts_in_number_order(self):
        scope = _scope(4)
        drafts = (
            DraftRef(2, "Окраска", "Покраска стен", 1),
            DraftRef(1, "Кладка стен", "Возведение стен", 2),
        )

        body = _chunk_body(scope, bounds=(3, 4), drafts=drafts)

        assert _user(body) == (
            "ЕДИНИЦА: м2\n"
            "ЧЕРНОВИКИ ЭТОГО ОТКРЫТИЯ:\n"
            "Ч1. Кладка стен — Возведение стен [категория 2]\n"
            "Ч2. Окраска — Покраска стен [категория 1]\n"
            "ИМЕНА 3–4 ИЗ 4:\n"
            "3. Имя 3 | статьи: 01.01 Стены | разделы: Раздел\n"
            "4. Имя 4 | статьи: 01.01 Стены | разделы: Раздел"
        )

    def test_no_drafts_no_block_header(self):
        assert DRAFT_BLOCK_HEADER not in _user(_chunk_body(_scope(3), bounds=(1, 3)))

    def test_whitespace_in_a_draft_title_cannot_split_its_line(self):
        scope = _scope(2)
        drafts = (DraftRef(1, "Кладка\nстен", "Возведение\n  стен", 1),)

        text = _user(_chunk_body(scope, bounds=(2, 2), drafts=drafts))

        assert "Ч1. Кладка стен — Возведение стен [категория 1]\n" in text

    def test_zero_names_is_one_call_without_names(self):
        scope = _scope(0, uncategorized=(10, 11))

        body = _chunk_body(scope, bounds=(1, 0))

        assert _user(body) == "ЕДИНИЦА: м2\nСЕМЬИ БЕЗ КАТЕГОРИИ: 10, 11\nИМЕНА:\n"

    def test_unit_none_is_a_legal_input(self):
        scope = _scope(1, unit_code=None)

        assert _user(_chunk_body(scope, bounds=(1, 1))).startswith("ЕДИНИЦА: без единицы\n")

    def test_system_is_byte_identical_in_every_chunk_and_to_the_canonical_body(self):
        scope = _scope(250)
        canonical = _canonical(scope)
        drafts = (DraftRef(1, "Кладка", "Возведение", 1),)

        bodies = [
            render_discovery_chunk(
                canonical, scope, settings=S, bounds=bounds, drafts=drafts if k else (),
                first=k == 0,
            )
            for k, bounds in enumerate(chunk_bounds(250, 100))
        ]

        canonical_system = json.dumps(canonical.body["messages"][0], ensure_ascii=False)
        assert [json.dumps(b["messages"][0], ensure_ascii=False) for b in bodies] == [
            canonical_system
        ] * 3
        assert bodies[0]["messages"][0]["content"][2]["cache_control"] == {"type": "ephemeral"}

    def test_profile_and_format_come_from_the_canonical_body(self):
        scope = _scope(3)
        s = _settings(SEMANTIC_DISCOVERY_REASONING_EFFORT="medium", SEMANTIC_DISCOVERY_MAX_TOKENS=1234)
        canonical = _canonical(scope, s)

        body = render_discovery_chunk(
            canonical, scope, settings=s, bounds=(1, 3), drafts=(), first=True
        )

        assert set(body) == {
            "model", "temperature", "max_tokens", "reasoning", "usage", "response_format",
            "messages",
        }
        assert body["model"] == "anthropic/claude-sonnet-5.5"
        assert body["temperature"] == 0
        assert body["max_tokens"] == 1234
        assert body["reasoning"] == {"effort": "medium"}
        assert body["usage"] == {"include": True}
        assert body["response_format"] == canonical.body["response_format"]

    def test_chunk_body_shares_no_mutable_state_with_the_canonical_body(self):
        scope = _scope(3)
        canonical = _canonical(scope)
        before = copy.deepcopy(canonical.body)

        body = render_discovery_chunk(
            canonical, scope, settings=S, bounds=(1, 3), drafts=(), first=True
        )
        body["messages"][0]["content"][0]["text"] = "изменено"
        body["response_format"]["json_schema"]["strict"] = False

        assert canonical.body == before

    def test_every_name_of_the_scope_is_sent_once_across_chunks(self):
        scope = _scope(250)
        canonical = _canonical(scope)

        sent_numbers = []
        for bounds in chunk_bounds(250, 100):
            text = _user(
                render_discovery_chunk(
                    canonical, scope, settings=S, bounds=bounds, drafts=(), first=bounds[0] == 1
                )
            )
            names_part = text.split(":\n", 1)[1] if text.count(":\n") == 1 else text.split("ИМЕНА")[1]
            sent_numbers += [
                int(line.split(".", 1)[0]) for line in names_part.splitlines() if "| статьи:" in line
            ]

        assert sent_numbers == list(range(1, 251))

    def test_body_carries_no_prices_volumes_contractors_or_objects(self):
        scope = _scope(3)

        body = _chunk_body(scope, bounds=(1, 3))

        # Состав тела — тот же, что у канонического: единица, перечень, имена со
        # статьями и разделами; ничего другого в `user` нет.
        for line in _user(body).splitlines():
            assert line.startswith(("ЕДИНИЦА:", "СЕМЬИ БЕЗ КАТЕГОРИИ:", "ИМЕНА ", "1. ", "2. ", "3. "))


class TestDraftRefsOfAnAnswer:
    def test_new_groups_get_consecutive_numbers_existing_groups_do_not_count(self):
        answer = DiscoveryAnswer(
            groups=(
                DiscoveryGroup(10, None, None, None, None, (1,)),
                DiscoveryGroup(None, "Кладка", "Опр 1", 2, None, (2,)),
                DiscoveryGroup(11, None, None, None, None, (3,)),
                DiscoveryGroup(None, "Окраска", "Опр 2", 1, 10, (4,)),
            ),
            not_work=(), family_categories=(), unassigned=(),
        )

        assert draft_refs_of(answer) == (
            DraftRef(1, "Кладка", "Опр 1", 2),
            DraftRef(2, "Окраска", "Опр 2", 1),
        )


class TestChunkSentOfAScope:
    def test_uncategorized_families_only_in_the_first_chunk(self):
        scope = _scope(250)
        drafts = (DraftRef(1, "А", "Б", 1), DraftRef(2, "В", "Г", 2))

        first = chunk_sent_of(scope, bounds=(1, 100), drafts=(), first=True)
        later = chunk_sent_of(scope, bounds=(101, 200), drafts=drafts, first=False)

        assert first == ChunkSent(
            bounds=(1, 100), draft_numbers=frozenset(),
            active_family_ids=frozenset({10, 11}),
            uncategorized_family_ids=frozenset({11}), category_ids=frozenset({1, 2}),
        )
        assert later == ChunkSent(
            bounds=(101, 200), draft_numbers=frozenset({1, 2}),
            active_family_ids=frozenset({10, 11}),
            uncategorized_family_ids=frozenset(), category_ids=frozenset({1, 2}),
        )


# ---------------------------------------------------------------------------
#  Разбор ответа порции
# ---------------------------------------------------------------------------

#: Порция 1: имена 1–5, активные семьи 10, 11, 12 (11, 12 без категории),
#: справочник 1 и 2, черновиков нет.
SENT1 = ChunkSent(
    bounds=(1, 5), draft_numbers=frozenset(),
    active_family_ids=frozenset({10, 11, 12}),
    uncategorized_family_ids=frozenset({11, 12}), category_ids=frozenset({1, 2}),
)
#: Порция 2: имена 6–10, в блоке черновики 1 и 2, перечня «без категории» нет.
SENT2 = ChunkSent(
    bounds=(6, 10), draft_numbers=frozenset({1, 2}),
    active_family_ids=frozenset({10, 11, 12}),
    uncategorized_family_ids=frozenset(), category_ids=frozenset({1, 2}),
)


def _new(names, *, title="Новая семья", definition="Определение", category_id=1, similar=None,
         family_id=None, draft_ref=None):
    return {
        "family_id": family_id, "draft_ref": draft_ref, "title": title,
        "definition": definition, "category_id": category_id,
        "similar_family_id": similar, "names": list(names),
    }


def _existing(family_id, names, **overrides):
    group = {
        "family_id": family_id, "draft_ref": None, "title": None, "definition": None,
        "category_id": None, "similar_family_id": None, "names": list(names),
    }
    group.update(overrides)
    return group


def _ref(draft_ref, names, **overrides):
    group = {
        "family_id": None, "draft_ref": draft_ref, "title": None, "definition": None,
        "category_id": None, "similar_family_id": None, "names": list(names),
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


def _parse(raw, sent=SENT1) -> ChunkAnswer:
    return parse_discovery_chunk(raw, sent)


def _placed(answer: ChunkAnswer) -> list[int]:
    return sorted([n for g in answer.groups for n in g.names] + list(answer.not_work))


def _expect(code, raw, sent=SENT1):
    with pytest.raises(AnswerSchemaError) as raised:
        parse_discovery_chunk(raw, sent)
    assert raised.value.code == code, raised.value.detail


def _group_of(**kw) -> ChunkGroup:
    base = dict(family_id=None, draft_ref=None, title=None, definition=None,
                category_id=None, similar_family_id=None)
    base.update(kw)
    return ChunkGroup(**base)


class TestChunkAnswerIsAccepted:
    def test_full_answer_is_parsed_into_typed_groups(self):
        raw = _raw(
            [_new([2, 1], similar=10), _existing(10, [3])],
            not_work=[5, 4],
            family_categories=[(11, 2), (12, 1)],
        )

        answer = _parse(raw)

        assert answer == ChunkAnswer(
            groups=(
                _group_of(title="Новая семья", definition="Определение", category_id=1,
                          similar_family_id=10, names=(1, 2)),
                _group_of(family_id=10, names=(3,)),
            ),
            not_work=(4, 5),
            family_categories=((11, 2), (12, 1)),
        )

    def test_title_and_definition_are_stripped(self):
        answer = _parse(_raw([_new([1], title="  Пол  ", definition=" Опр ")]))

        assert (answer.groups[0].title, answer.groups[0].definition) == ("Пол", "Опр")

    def test_skipped_numbers_are_not_an_error(self):
        answer = _parse(_raw([_new([4])], not_work=[2]))

        assert _placed(answer) == [2, 4]

    def test_fenced_and_bare_answers_parse_identically(self):
        bare = _raw([_new([1])], not_work=[2])

        assert _parse(f"```json\n{bare}\n```") == _parse(bare)

    def test_chunk_two_draft_ref_group_is_accepted(self):
        answer = _parse(_raw([_ref(2, [6, 7])]), SENT2)

        assert answer.groups == (_group_of(draft_ref=2, names=(6, 7)),)

    def test_empty_chunk_answer_is_legal(self):
        answer = _parse(_raw([]))

        assert answer == ChunkAnswer(groups=(), not_work=(), family_categories=())


class TestChunkFormIsStrict:
    def test_not_json(self):
        _expect("not_json", "это не json")

    def test_text_around_json(self):
        bare = _raw([_new([1])])

        _expect("extra_text", f"{bare}\nГотово.")
        _expect("not_json", f"Вот ответ: {bare}")

    def test_missing_top_level_key(self):
        _expect("missing_field", json.dumps({"groups": [], "not_work": []}))

    def test_extra_top_level_key_is_bad_type(self):
        _expect("bad_type", json.dumps(
            {"groups": [], "not_work": [], "family_categories": [], "extra": 1}
        ))

    def test_group_without_draft_ref_key_is_missing_field(self):
        group = _new([1])
        del group["draft_ref"]

        _expect("missing_field", _raw([group]))

    def test_group_with_extra_key_is_bad_type(self):
        group = _new([1])
        group["comment"] = "x"

        _expect("bad_type", _raw([group]))

    def test_groups_not_a_list_is_bad_type(self):
        _expect("bad_type", json.dumps(
            {"groups": {}, "not_work": [], "family_categories": []}
        ))

    def test_group_not_an_object_is_bad_type(self):
        _expect("bad_type", json.dumps(
            {"groups": [5], "not_work": [], "family_categories": []}
        ))

    def test_names_not_a_list_is_bad_type(self):
        group = _new([1])
        group["names"] = "1"

        _expect("bad_type", _raw([group]))

    def test_name_number_not_an_integer_is_bad_type(self):
        group = _new([1])
        group["names"] = ["1"]

        _expect("bad_type", _raw([group]))

    def test_boolean_number_is_bad_type(self):
        group = _new([1])
        group["names"] = [True]

        _expect("bad_type", _raw([group]))

    def test_draft_ref_string_is_bad_type(self):
        _expect("bad_type", _raw([_ref("1", [1])]))

    def test_family_id_string_is_bad_type(self):
        _expect("bad_type", _raw([_existing("10", [1])]))

    def test_title_number_is_bad_type(self):
        _expect("bad_type", _raw([_new([1], title=5)]))

    def test_not_work_item_not_an_integer_is_bad_type(self):
        _expect("bad_type", json.dumps(
            {"groups": [], "not_work": ["1"], "family_categories": []}
        ))

    def test_family_category_pair_without_key_is_missing_field(self):
        _expect("missing_field", json.dumps(
            {"groups": [], "not_work": [], "family_categories": [{"family_id": 11}]}
        ))

    def test_family_category_pair_with_string_is_bad_type(self):
        _expect("bad_type", json.dumps(
            {"groups": [], "not_work": [],
             "family_categories": [{"family_id": "11", "category_id": 1}]}
        ))

    # Ревью задачи 8: формы, которые проверял удалённый разбор одним вызовом, —
    # каждая своим входом и для разбора порции.
    def test_family_category_pair_with_extra_key_is_bad_type(self):
        _expect("bad_type", json.dumps(
            {"groups": [], "not_work": [],
             "family_categories": [{"family_id": 11, "category_id": 1, "note": "x"}]}
        ))

    def test_definition_number_is_bad_type(self):
        _expect("bad_type", _raw([_new([1], definition=5)]))

    def test_category_id_string_is_bad_type(self):
        _expect("bad_type", _raw([_new([1], category_id="1")]))

    def test_similar_family_id_string_is_bad_type(self):
        _expect("bad_type", _raw([_new([1], similar="10")]))

    def test_not_work_not_a_list_is_bad_type(self):
        _expect("bad_type", json.dumps(
            {"groups": [], "not_work": 3, "family_categories": []}
        ))

    def test_family_categories_not_a_list_is_bad_type(self):
        _expect("bad_type", json.dumps(
            {"groups": [], "not_work": [], "family_categories": {}}
        ))

    def test_a_form_error_in_a_later_group_fails_the_whole_answer(self):
        bad = _new([2])
        del bad["definition"]

        _expect("missing_field", _raw([_new([1]), bad]))


class TestChunkContentErrorsGoToTheRest:
    """Каждое правило содержания — отдельным входом: имена его группы не попадают
    никуда, соседняя верная группа цела."""

    GOOD = staticmethod(lambda: _new([5], title="Верная"))

    def _only_good_survives(self, bad_group, bad_names, sent=SENT1, good_names=(5,)):
        good = _new(list(good_names), title="Верная")
        answer = _parse(_raw([bad_group, good]), sent)

        assert [g.title for g in answer.groups] == ["Верная"]
        assert _placed(answer) == sorted(good_names)
        for number in bad_names:
            assert number not in _placed(answer)

    def test_family_not_from_the_sent_list(self):
        self._only_good_survives(_existing(99, [1, 2]), (1, 2))

    def test_family_from_the_sent_list_is_kept(self):
        answer = _parse(_raw([_existing(12, [1, 2])]))

        assert answer.groups == (_group_of(family_id=12, names=(1, 2)),)

    def test_draft_not_from_the_block(self):
        self._only_good_survives(_ref(3, [6, 7]), (6, 7), SENT2, good_names=(8,))

    def test_draft_from_the_block_is_kept(self):
        answer = _parse(_raw([_ref(1, [6, 7])]), SENT2)

        assert [g.draft_ref for g in answer.groups] == [1]

    def test_draft_ref_in_the_first_chunk_has_no_block_to_point_to(self):
        self._only_good_survives(_ref(1, [1]), (1,))

    def test_category_not_from_the_catalog(self):
        self._only_good_survives(_new([1, 2], category_id=77), (1, 2))

    def test_category_missing_on_a_new_group(self):
        self._only_good_survives(_new([1], category_id=None), (1,))

    def test_similar_family_not_from_the_sent_list(self):
        self._only_good_survives(_new([1, 2], similar=99), (1, 2))

    def test_similar_family_from_the_sent_list_is_kept(self):
        answer = _parse(_raw([_new([1], similar=12)]))

        assert answer.groups[0].similar_family_id == 12

    def test_family_id_and_draft_ref_together(self):
        self._only_good_survives(_existing(10, [6, 7], draft_ref=1), (6, 7), SENT2, (8,))

    def test_new_group_without_title(self):
        self._only_good_survives(_new([1], title=None), (1,))

    def test_new_group_with_blank_title(self):
        self._only_good_survives(_new([1], title="  \t "), (1,))

    def test_new_group_without_definition(self):
        self._only_good_survives(_new([1], definition=None), (1,))

    def test_new_group_with_blank_definition(self):
        self._only_good_survives(_new([1], definition="   "), (1,))

    def test_number_above_the_chunk_is_dropped_the_rest_of_the_group_stays(self):
        answer = _parse(_raw([_new([4, 6, 99])]))

        assert answer.groups == (
            _group_of(title="Новая семья", definition="Определение", category_id=1, names=(4,)),
        )

    def test_number_below_the_chunk_is_dropped(self):
        answer = _parse(_raw([_new([6, 7, 9])]), SENT2)

        assert answer.groups[0].names == (6, 7, 9)
        answer = _parse(_raw([_new([5, 6])]), SENT2)
        assert answer.groups[0].names == (6,)

    def test_zero_and_negative_numbers_are_outside_the_chunk(self):
        answer = _parse(_raw([_new([0, -1, 2])]))

        assert answer.groups[0].names == (2,)

    def test_group_that_loses_all_numbers_is_dropped_not_kept_empty(self):
        answer = _parse(_raw([_new([9, 10]), _new([1], title="Остаётся")]))

        assert [g.title for g in answer.groups] == ["Остаётся"]

    def test_number_in_two_groups_drops_both_occurrences(self):
        answer = _parse(_raw([_new([1, 2], title="А"), _new([2, 3], title="Б")]))

        assert [(g.title, g.names) for g in answer.groups] == [("А", (1,)), ("Б", (3,))]

    def test_number_twice_in_one_group_is_dropped(self):
        answer = _parse(_raw([_new([1, 2, 2, 3])]))

        assert answer.groups[0].names == (1, 3)

    def test_number_in_a_group_and_in_not_work_is_dropped_from_both(self):
        answer = _parse(_raw([_new([1, 2])], not_work=[2, 3]))

        assert answer.groups[0].names == (1,)
        assert answer.not_work == (3,)

    def test_number_twice_in_not_work_is_dropped(self):
        answer = _parse(_raw([], not_work=[3, 3, 4]))

        assert answer.not_work == (4,)

    def test_a_number_in_an_invalid_group_still_counts_as_an_occurrence(self):
        answer = _parse(_raw([_existing(99, [1, 2]), _new([2, 3])]))

        assert _placed(answer) == [3]

    def test_not_work_numbers_outside_the_chunk_are_dropped(self):
        answer = _parse(_raw([], not_work=[5, 6, 10]), SENT2)

        assert answer.not_work == (6, 10)

    def test_zero_name_chunk_drops_every_number(self):
        sent = ChunkSent(
            bounds=(1, 0), draft_numbers=frozenset(), active_family_ids=frozenset({10}),
            uncategorized_family_ids=frozenset({10}), category_ids=frozenset({1}),
        )

        answer = _parse(_raw([_new([1])], not_work=[1], family_categories=[(10, 1)]), sent)

        assert answer.groups == () and answer.not_work == ()
        assert answer.family_categories == ((10, 1),)

    def test_draft_ref_group_ignores_its_other_fields(self):
        group = _ref(1, [6], title="Другое", definition="Другое", category_id=99)
        group["similar_family_id"] = 99

        answer = _parse(_raw([group]), SENT2)

        assert answer.groups == (_group_of(draft_ref=1, names=(6,)),)

    def test_existing_family_group_ignores_its_other_fields(self):
        group = _existing(10, [1], title="Лишнее", definition="Лишнее", category_id=77)
        group["similar_family_id"] = 99

        answer = _parse(_raw([group]))

        assert answer.groups == (_group_of(family_id=10, names=(1,)),)

    @pytest.mark.parametrize(
        "field,value",
        [("title", "Лишнее"), ("definition", "Лишнее"), ("category_id", 77),
         ("similar_family_id", 99)],
    )
    def test_each_extra_field_of_an_existing_family_group_is_ignored_on_its_own(
        self, field, value
    ):
        group = _existing(10, [1])
        group[field] = value

        answer = _parse(_raw([group]))

        assert answer.groups == (_group_of(family_id=10, names=(1,)),)

    @pytest.mark.parametrize(
        "make",
        [lambda: _new([]), lambda: _existing(10, []), lambda: _ref(1, [])],
    )
    def test_a_group_with_an_empty_names_list_disappears_without_an_error(self, make):
        sent = SENT2
        group = make()
        if group["draft_ref"] is None:
            sent = SENT1
        answer = _parse(_raw([group, _new([6 if sent is SENT2 else 1], title="Живая")]), sent)

        assert [g.title for g in answer.groups] == ["Живая"]

    def test_two_groups_of_one_family_are_both_kept_for_the_merge(self):
        answer = _parse(_raw([_existing(10, [1]), _existing(10, [2])]))

        assert [(g.family_id, g.names) for g in answer.groups] == [(10, (1,)), (10, (2,))]

    def test_group_order_and_name_order(self):
        answer = _parse(_raw([_new([3, 1], title="Вторая"), _existing(10, [2])]))

        assert [(g.title, g.names) for g in answer.groups] == [("Вторая", (1, 3)), (None, (2,))]


class TestChunkFamilyCategories:
    def test_valid_pairs_are_kept_in_order(self):
        answer = _parse(_raw([], family_categories=[(12, 2), (11, 1)]))

        assert answer.family_categories == ((12, 2), (11, 1))

    def test_family_not_from_the_uncategorized_list_is_skipped(self):
        answer = _parse(_raw([], family_categories=[(10, 1), (11, 1)]))

        assert answer.family_categories == ((11, 1),)

    def test_unknown_family_is_skipped(self):
        answer = _parse(_raw([], family_categories=[(99, 1), (11, 1)]))

        assert answer.family_categories == ((11, 1),)

    def test_category_not_from_the_catalog_is_skipped(self):
        answer = _parse(_raw([], family_categories=[(11, 77), (12, 1)]))

        assert answer.family_categories == ((12, 1),)

    def test_two_pairs_of_one_family_are_both_dropped(self):
        answer = _parse(_raw([], family_categories=[(11, 1), (11, 2), (12, 1)]))

        assert answer.family_categories == ((12, 1),)

    def test_two_pairs_of_one_family_are_dropped_even_when_one_is_invalid(self):
        answer = _parse(_raw([], family_categories=[(11, 1), (11, 77), (12, 2)]))

        assert answer.family_categories == ((12, 2),)

    def test_the_same_pair_twice_is_also_dropped(self):
        answer = _parse(_raw([], family_categories=[(11, 1), (11, 1)]))

        assert answer.family_categories == ()

    def test_a_non_empty_list_outside_the_first_chunk_is_ignored(self):
        answer = _parse(_raw([_new([6])], family_categories=[(11, 1), (12, 2)]), SENT2)

        assert answer.family_categories == ()
        assert [g.names for g in answer.groups] == [(6,)]

    def test_a_non_empty_list_outside_the_first_chunk_is_still_checked_for_form(self):
        _expect("missing_field", json.dumps(
            {"groups": [], "not_work": [], "family_categories": [{"family_id": 11}]}
        ), SENT2)


# ---------------------------------------------------------------------------
#  Сборка
# ---------------------------------------------------------------------------

def _chunk(groups=(), not_work=(), pairs=()) -> ChunkAnswer:
    return ChunkAnswer(groups=tuple(groups), not_work=tuple(not_work), family_categories=tuple(pairs))


def _newg(names, title="Кладка", definition="Опр", category_id=1, similar=None) -> ChunkGroup:
    return _group_of(title=title, definition=definition, category_id=category_id,
                     similar_family_id=similar, names=tuple(names))


def _merge(chunks, n=300, max_drafts=150) -> DiscoveryAnswer:
    return merge_chunks(chunks, n, max_drafts=max_drafts)


class TestMergeChunks:
    def test_draft_ref_appends_names_to_its_draft(self):
        answer = _merge([
            _chunk([_newg([1, 2], title="Кладка")]),
            _chunk([_group_of(draft_ref=1, names=(101, 102))]),
            _chunk([_group_of(draft_ref=1, names=(201,))]),
        ])

        assert answer.groups == (
            DiscoveryGroup(None, "Кладка", "Опр", 1, None, (1, 2, 101, 102, 201)),
        )

    def test_draft_ref_group_keeps_the_fields_of_the_first_group_of_the_draft(self):
        answer = _merge([
            _chunk([_newg([1], title="Кладка", definition="Первое", category_id=2, similar=10)]),
            _chunk([_group_of(draft_ref=1, names=(101,))]),
        ])

        assert (answer.groups[0].definition, answer.groups[0].category_id,
                answer.groups[0].similar_family_id) == ("Первое", 2, 10)

    def test_new_group_with_the_title_of_an_existing_draft_is_appended_to_it(self):
        answer = _merge([
            _chunk([_newg([1], title="Кладка", definition="Первое")]),
            _chunk([_newg([101], title="Кладка", definition="Второе", category_id=2)]),
        ])

        assert answer.groups == (DiscoveryGroup(None, "Кладка", "Первое", 1, None, (1, 101)),)

    def test_title_match_ignores_case_and_surrounding_and_inner_whitespace(self):
        answer = _merge([
            _chunk([_newg([1], title="Кладка стен")]),
            _chunk([_newg([101], title="  кладка   СТЕН ")]),
        ])

        assert len(answer.groups) == 1 and answer.groups[0].names == (1, 101)

    def test_different_titles_stay_different_drafts(self):
        answer = _merge([_chunk([_newg([1], title="Кладка"), _newg([2], title="Кладка стен")])])

        assert [g.title for g in answer.groups] == ["Кладка", "Кладка стен"]

    def test_same_title_inside_one_chunk_is_one_draft(self):
        answer = _merge([_chunk([_newg([1], title="Кладка"), _newg([2], title="кладка")])])

        assert [(g.title, g.names) for g in answer.groups] == [("Кладка", (1, 2))]

    def test_title_of_101_characters_is_cut_to_100_with_an_ellipsis(self):
        answer = _merge([_chunk([_newg([1], title="А" * 101)])])

        assert answer.groups[0].title == "А" * 99 + "…"

    def test_title_of_exactly_100_characters_is_not_cut(self):
        answer = _merge([_chunk([_newg([1], title="А" * 100)])])

        assert answer.groups[0].title == "А" * 100

    def test_definition_of_301_characters_is_cut_to_300_with_an_ellipsis(self):
        answer = _merge([_chunk([_newg([1], definition="Б" * 301)])])

        assert answer.groups[0].definition == "Б" * 299 + "…"

    def test_definition_of_exactly_300_characters_is_not_cut(self):
        answer = _merge([_chunk([_newg([1], definition="Б" * 300)])])

        assert answer.groups[0].definition == "Б" * 300

    def test_a_long_title_is_matched_by_its_cut_form(self):
        answer = _merge([
            _chunk([_newg([1], title="А" * 150)]),
            _chunk([_newg([101], title="А" * 99 + "…")]),
        ])

        assert len(answer.groups) == 1 and answer.groups[0].names == (1, 101)

    def test_two_long_titles_with_a_common_prefix_are_two_drafts_in_one_chunk(self):
        first = "Щ" * 110 + "А"
        second = "Щ" * 110 + "Б"

        answer = _merge([_chunk([
            _newg([1], title=first, definition="Первое", category_id=1),
            _newg([2], title=second, definition="Второе", category_id=2, similar=10),
        ])], n=2)

        assert [(g.title, g.definition, g.category_id, g.similar_family_id, g.names)
                for g in answer.groups] == [
            ("Щ" * 99 + "…", "Первое", 1, None, (1,)),
            ("Щ" * 99 + "…", "Второе", 2, 10, (2,)),
        ]

    def test_two_long_titles_with_a_common_prefix_are_two_drafts_across_chunks(self):
        first = "Щ" * 110 + "А"
        second = "Щ" * 110 + "Б"

        answer = _merge([
            _chunk([_newg([1], title=first, definition="Первое", category_id=1)]),
            _chunk([_newg([101], title=second, definition="Второе", category_id=2)]),
        ], n=101)

        assert [(g.definition, g.category_id, g.names) for g in answer.groups] == [
            ("Первое", 1, (1,)), ("Второе", 2, (101,)),
        ]

    def test_the_same_long_title_twice_is_one_draft(self):
        long_title = "Щ" * 110 + "А"

        answer = _merge([
            _chunk([_newg([1], title=long_title)]),
            _chunk([_newg([101], title="  " + long_title.lower() + " ")]),
        ], n=101)

        assert len(answer.groups) == 1 and answer.groups[0].names == (1, 101)

    def test_an_echo_of_the_cut_form_of_exactly_one_draft_is_appended_to_it(self):
        answer = _merge([
            _chunk([_newg([1], title="Щ" * 110 + "А"), _newg([2], title="Другое")]),
            _chunk([_newg([101], title="Щ" * 99 + "…", definition="Эхо", category_id=2)]),
        ], n=101)

        assert [(g.title, g.names) for g in answer.groups] == [
            ("Щ" * 99 + "…", (1, 101)), ("Другое", (2,)),
        ]

    def test_an_echo_of_a_cut_form_shared_by_two_drafts_goes_to_the_rest(self):
        answer = _merge([
            _chunk([_newg([1], title="Щ" * 110 + "А"), _newg([2], title="Щ" * 110 + "Б")]),
            _chunk([_newg([101], title="Щ" * 99 + "…")]),
        ], n=101)

        assert [g.names for g in answer.groups] == [(1,), (2,)]
        assert 101 in answer.unassigned

    def test_a_long_title_whose_cut_form_equals_a_short_drafts_title_is_a_new_draft(self):
        short = "Щ" * 99 + "…"

        answer = _merge([_chunk([
            _newg([1], title=short, definition="Короткое"),
            _newg([2], title="Щ" * 110 + "А", definition="Длинное"),
        ])], n=2)

        assert [(g.title, g.definition, g.names) for g in answer.groups] == [
            (short, "Короткое", (1,)), (short, "Длинное", (2,)),
        ]

    def test_a_full_title_match_wins_over_an_ambiguous_cut_form(self):
        # Ревью круга Codex: имя, равное ПОЛНОМУ имени короткого черновика, идёт в
        # него, хотя та же строка — обрезанная форма двух черновиков (правило (а)
        # раньше (б)).
        short = "Щ" * 99 + "…"

        answer = _merge([
            _chunk([_newg([1], title=short, definition="Короткое"),
                    _newg([2], title="Щ" * 110 + "А", definition="Длинное")]),
            _chunk([_newg([101], title=short, definition="Третье")]),
        ], n=101)

        assert [(g.definition, g.names) for g in answer.groups] == [
            ("Короткое", (1, 101)), ("Длинное", (2,)),
        ]

    def test_an_echo_of_the_cut_form_ignores_case_and_whitespace(self):
        # Обрезанная форма — «Щ»×49, пробел, «Щ»×49, «…»; повтор — другим регистром
        # первой половины и тремя пробелами внутри.
        answer = _merge([
            _chunk([_newg([1], title="Щ" * 49 + " " + "Щ" * 60 + "А")]),
            _chunk([_newg([101], title="щ" * 49 + "   " + "Щ" * 49 + "…")]),
        ], n=101)

        assert len(answer.groups) == 1 and answer.groups[0].names == (1, 101)

    def test_new_groups_over_the_draft_limit_send_their_names_to_the_rest(self):
        answer = _merge([_chunk([_newg([1], title="А"), _newg([2], title="Б"), _newg([3], title="В")])],
                        n=3, max_drafts=2)

        assert [g.title for g in answer.groups] == ["А", "Б"]
        assert answer.unassigned == (3,)

    def test_exactly_the_limit_is_allowed(self):
        answer = _merge([_chunk([_newg([1], title="А"), _newg([2], title="Б")])], n=2, max_drafts=2)

        assert len(answer.groups) == 2 and answer.unassigned == ()

    def test_draft_ref_and_same_title_still_work_at_the_limit(self):
        answer = _merge([
            _chunk([_newg([1], title="А"), _newg([2], title="Б")]),
            _chunk([_group_of(draft_ref=2, names=(101,)), _newg([102], title="а"),
                    _newg([103], title="Новая")]),
        ], n=103, max_drafts=2)

        assert [(g.title, g.names) for g in answer.groups] == [("А", (1, 102)), ("Б", (2, 101))]
        assert answer.unassigned == tuple(range(3, 101)) + (103,)

    def test_existing_family_groups_of_one_chunk_are_one_group(self):
        answer = _merge([_chunk([_group_of(family_id=10, names=(1,)),
                                 _group_of(family_id=10, names=(3,))])])

        assert answer.groups == (DiscoveryGroup(10, None, None, None, None, (1, 3)),)

    def test_existing_family_groups_of_different_chunks_are_one_group(self):
        answer = _merge([
            _chunk([_group_of(family_id=10, names=(1,))]),
            _chunk([_newg([101], title="Кладка"), _group_of(family_id=11, names=(102,))]),
            _chunk([_group_of(family_id=10, names=(201,))]),
        ])

        assert [(g.family_id, g.title, g.names) for g in answer.groups] == [
            (10, None, (1, 201)), (None, "Кладка", (101,)), (11, None, (102,)),
        ]

    def test_names_are_sorted_and_unique_in_every_group(self):
        answer = _merge([
            _chunk([_group_of(family_id=10, names=(3, 1))]),
            _chunk([_group_of(family_id=10, names=(101,))]),
        ])

        assert answer.groups[0].names == (1, 3, 101)

    def test_not_work_is_the_sorted_union_of_the_chunks(self):
        answer = _merge([_chunk(not_work=(5, 2)), _chunk(not_work=(101,)), _chunk(not_work=(250,))])

        assert answer.not_work == (2, 5, 101, 250)

    def test_unassigned_are_the_numbers_in_no_place(self):
        answer = _merge([_chunk([_newg([1, 3])], not_work=(2,))], n=5)

        assert answer.unassigned == (4, 5)

    def test_only_categories_has_no_names_and_no_unassigned(self):
        answer = _merge([_chunk(pairs=[(11, 1)])], n=0)

        assert answer == DiscoveryAnswer(
            groups=(), not_work=(), family_categories=((11, 1),), unassigned=(),
        )

    def test_a_pair_family_met_twice_across_chunks_is_dropped_in_every_place(self):
        answer = _merge([
            _chunk(pairs=[(11, 1), (12, 2)]),
            _chunk(pairs=[(11, 2)]),
        ])

        assert answer.family_categories == ((12, 2),)

    def test_draft_ref_to_a_draft_that_does_not_exist_is_the_rest(self):
        answer = _merge([_chunk([_group_of(draft_ref=1, names=(1,))])], n=1)

        assert answer.groups == () and answer.unassigned == (1,)

    def test_draft_numbers_are_stable_when_chunks_are_added(self):
        first = _chunk([_newg([1], title="А"), _group_of(family_id=10, names=(2,)),
                        _newg([3], title="Б")])
        second = _chunk([_newg([101], title="В"), _group_of(draft_ref=1, names=(102,))])

        before = draft_refs_of(_merge([first]))
        after = draft_refs_of(_merge([first, second]))

        assert before == (DraftRef(1, "А", "Опр", 1), DraftRef(2, "Б", "Опр", 1))
        assert after[:2] == before
        assert after[2] == DraftRef(3, "В", "Опр", 1)

    def test_the_result_satisfies_the_contract_of_the_answer(self):
        """Номер — не больше чем в одном месте, активная семья — не больше чем в
        одной группе, новая группа непуста и с именем, определением и категорией,
        пара категории одна на семью."""
        answer = _merge([
            _chunk(
                [_newg([1, 2], title="А"), _group_of(family_id=10, names=(3,)),
                 _newg([4], title="Б", similar=10)],
                not_work=(5,), pairs=[(11, 1), (12, 2)],
            ),
            _chunk([
                _group_of(draft_ref=1, names=(101,)), _group_of(family_id=10, names=(102,)),
                _newg([103], title="а"), _newg([104], title="В"),
            ], not_work=(105,)),
        ], n=110)

        numbers = [n for g in answer.groups for n in g.names] + list(answer.not_work)
        assert len(numbers) == len(set(numbers))
        assert set(answer.unassigned).isdisjoint(numbers)
        assert sorted(numbers + list(answer.unassigned)) == list(range(1, 111))
        families = [g.family_id for g in answer.groups if g.family_id is not None]
        assert len(families) == len(set(families))
        for g in answer.groups:
            assert g.names
            if g.family_id is None:
                assert g.title and g.definition and g.category_id is not None
            else:
                assert (g.title, g.definition, g.category_id, g.similar_family_id) == (
                    None, None, None, None)
        pair_families = [f for f, _c in answer.family_categories]
        assert len(pair_families) == len(set(pair_families))


class TestParseThenMerge:
    def test_an_emptied_group_takes_no_draft_number_and_is_not_in_the_next_block(self):
        first = _parse(_raw([_new([9, 10], title="Пустая"), _new([1], title="Живая")]))
        second_sent = ChunkSent(
            bounds=(6, 10), draft_numbers=frozenset({1}),
            active_family_ids=frozenset({10, 11, 12}), uncategorized_family_ids=frozenset(),
            category_ids=frozenset({1, 2}),
        )
        merged = _merge([first], n=10)

        refs = draft_refs_of(merged)

        assert refs == (DraftRef(1, "Живая", "Определение", 1),)
        second = _parse(_raw([_ref(1, [6, 7]), _ref(2, [8])]), second_sent)
        full = _merge([first, second], n=10)
        assert [(g.title, g.names) for g in full.groups] == [("Живая", (1, 6, 7))]
        assert full.unassigned == (2, 3, 4, 5, 8, 9, 10)

    def test_a_group_of_dropped_names_does_not_reach_the_draft_block(self):
        first = _parse(_raw([_new([1], title="Остаётся"), _new([2, 2], title="Дубль")]))

        assert [d.title for d in draft_refs_of(_merge([first], n=5))] == ["Остаётся"]


# ---------------------------------------------------------------------------
#  Резерв и ожидаемая цена
# ---------------------------------------------------------------------------

_PER_M = Decimal(1_000_000)


def _prices(**kw) -> Settings:
    base = dict(
        SEMANTIC_DISCOVERY_PRICE_INPUT_PER_M=Decimal("1"),
        SEMANTIC_DISCOVERY_PRICE_CACHE_WRITE_PER_M=Decimal("3"),
        SEMANTIC_DISCOVERY_PRICE_CACHE_READ_PER_M=Decimal("0.5"),
        SEMANTIC_DISCOVERY_PRICE_OUTPUT_PER_M=Decimal("10"),
        SEMANTIC_DISCOVERY_MAX_TOKENS=100,
        SEMANTIC_DISCOVERY_CHUNK_NAMES=2,
        SEMANTIC_DISCOVERY_MAX_DRAFTS=2,
    )
    base.update(kw)
    return _settings(**base)


def _user_bytes(scope, settings, bounds, first):
    return len(
        _user(_chunk_body(scope, bounds=bounds, first=first, settings=settings)).encode("utf-8")
    )


class TestDraftLineBound:
    def test_longest_line_with_a_one_digit_number_is_1634_bytes(self):
        # «Ч2. » 5 + имя 100×4 + « — » 5 + определение 300×4 + « [категория » 21
        # + «2» + «]» + перевод строки = 5+400+5+1200+21+1+1+1.
        assert draft_line_max_bytes(2, 2) == 1634

    def test_a_three_digit_number_and_category_cost_two_more_bytes_each(self):
        assert draft_line_max_bytes(150, 2) == 1636
        assert draft_line_max_bytes(150, 12) == 1637

    def test_the_header_of_the_block_is_48_bytes(self):
        assert len(DRAFT_BLOCK_HEADER.encode("utf-8")) == 48

    def test_the_bound_covers_a_real_line_of_the_longest_allowed_text(self):
        from services.family_discovery import _draft_line

        draft = DraftRef(150, "Я" * 99 + "…", "Я" * 299 + "…", 2)

        assert len((_draft_line(draft) + "\n").encode("utf-8")) <= draft_line_max_bytes(150, 2)

    def test_a_draft_of_four_byte_characters_at_the_limits_fits_the_bound(self):
        from services.family_discovery import _draft_line

        emoji = "\U0001F600"
        draft = DraftRef(150, emoji * 100, emoji * 300, 2)

        line = (_draft_line(draft) + "\n").encode("utf-8")

        assert len(line) == 1636 == draft_line_max_bytes(150, 2)

    def test_a_long_four_byte_draft_cut_by_the_assembly_fits_the_bound(self):
        from services.family_discovery import _draft_line

        emoji = "\U0001F600"
        answer = _merge([_chunk([_newg([1], title=emoji * 150, definition=emoji * 400)])], n=1)
        (draft,) = draft_refs_of(answer)

        assert len(draft.title) == 100 and len(draft.definition) == 300
        line = (_draft_line(draft) + "\n").encode("utf-8")
        assert len(line) <= draft_line_max_bytes(150, 1)


class TestReserve:
    def test_reserve_is_the_sum_of_the_chunk_upper_bounds(self):
        s = _prices()
        scope = _scope(5)
        rendered = _canonical(scope, s)
        prefix = sum(len(b["text"].encode("utf-8")) for b in rendered.body["messages"][0]["content"])
        assert rendered.prefix_bytes == prefix
        # Три порции (1–2, 3–4, 5): блок черновиков у второй и третьей — на
        # пределе в два черновика.
        block = 48 + 2 * 1634
        users = [
            _user_bytes(scope, s, (1, 2), True),
            _user_bytes(scope, s, (3, 4), False) + block,
            _user_bytes(scope, s, (5, 5), False) + block,
        ]
        expected = sum(
            (Decimal(prefix) * 3 + Decimal(u) * 1 + Decimal(100) * 10) for u in users
        ) / _PER_M

        assert reserve_of(scope, rendered, s) == expected

    def test_the_last_chunk_draft_block_is_at_the_limit_for_a_big_unit(self):
        s = _settings(SEMANTIC_DISCOVERY_CHUNK_NAMES=100)
        scope = _scope(400)
        rendered = _canonical(scope, s)
        line, header = 1636, 48
        blocks = [0, header + 100 * line, header + 150 * line, header + 150 * line]
        bounds = chunk_bounds(400, 100)
        users = [
            _user_bytes(scope, s, b, b[0] == 1) + blocks[k] for k, b in enumerate(bounds)
        ]
        expected = sum(
            Decimal(rendered.prefix_bytes) * Decimal("2.5") + Decimal(u) * 2 + Decimal(8000) * 10
            for u in users
        ) / _PER_M

        assert reserve_of(scope, rendered, s) == expected

    def test_a_draft_number_of_four_digits_costs_one_byte_more_per_draft_line(self):
        # 400 имён по 100: блоки на 100, 200 и 300 черновиков (предел 1000 их не
        # режет) — 600 строк; номер 1000 — на знак длиннее номера 500.
        scope = _scope(400)
        rendered = _canonical(scope)
        three_digits = _settings(SEMANTIC_DISCOVERY_MAX_DRAFTS=500)
        four_digits = _settings(SEMANTIC_DISCOVERY_MAX_DRAFTS=1000)

        difference = reserve_of(scope, rendered, four_digits) - reserve_of(
            scope, rendered, three_digits
        )

        assert difference == Decimal(600) * 1 * Decimal(2) / _PER_M

    def test_a_category_id_of_five_digits_costs_four_bytes_more_per_draft_line(self):
        # Предел 150: блоки на 100, 150 и 150 черновиков — 400 строк.
        scope = _scope(400)
        big = dataclasses.replace(scope, category_ids=(1, 12345))
        rendered = _canonical(scope)

        difference = reserve_of(big, rendered, S) - reserve_of(scope, rendered, S)

        assert difference == Decimal(400) * 4 * Decimal(2) / _PER_M

    def test_the_longest_line_uses_the_largest_category_id_of_the_scope(self):
        assert draft_line_max_bytes(150, 12345) - draft_line_max_bytes(150, 2) == 4
        assert draft_line_max_bytes(1000, 2) - draft_line_max_bytes(999, 2) == 1

    def test_a_cache_write_dearer_than_input_prices_the_prefix_of_every_chunk_by_the_write(self):
        scope = _scope(5)
        cheap = _prices(SEMANTIC_DISCOVERY_PRICE_CACHE_WRITE_PER_M=Decimal("1"))
        dear = _prices(SEMANTIC_DISCOVERY_PRICE_CACHE_WRITE_PER_M=Decimal("5"))
        rendered = _canonical(scope, cheap)

        difference = reserve_of(scope, rendered, dear) - reserve_of(scope, rendered, cheap)

        assert difference == Decimal(3) * rendered.prefix_bytes * 4 / _PER_M

    def test_the_prefix_is_priced_by_the_dearest_of_the_three_tariffs(self):
        scope = _scope(5)
        read_dearest = _prices(
            SEMANTIC_DISCOVERY_PRICE_CACHE_READ_PER_M=Decimal("7"),
        )
        base = _prices(SEMANTIC_DISCOVERY_PRICE_CACHE_READ_PER_M=Decimal("0.5"))
        rendered = _canonical(scope, base)

        difference = reserve_of(scope, rendered, read_dearest) - reserve_of(scope, rendered, base)

        assert difference == Decimal(3) * rendered.prefix_bytes * 4 / _PER_M

    def test_a_cache_miss_in_the_second_chunk_does_not_change_the_reserve(self):
        # Резерв не опирается на кэш: чтение кэша дешевле записи, но при тарифе
        # чтения, сниженном до нуля, резерв прежний.
        scope = _scope(5)
        with_read = _prices(SEMANTIC_DISCOVERY_PRICE_CACHE_READ_PER_M=Decimal("0.5"))
        free_read = _prices(SEMANTIC_DISCOVERY_PRICE_CACHE_READ_PER_M=Decimal("0"))
        rendered = _canonical(scope, with_read)

        assert reserve_of(scope, rendered, with_read) == reserve_of(scope, rendered, free_read)

    def test_reserve_is_not_below_the_expected_cost(self):
        scope = _scope(250)
        rendered = _canonical(scope)

        reserve, expected = estimate_of(scope, rendered, S)

        assert reserve > expected > 0

    def test_zero_names_is_one_call(self):
        s = _prices()
        scope = _scope(0, uncategorized=(10, 11))
        rendered = _canonical(scope, s)
        user = _user_bytes(scope, s, (1, 0), True)

        assert reserve_of(scope, rendered, s) == (
            Decimal(rendered.prefix_bytes) * 3 + Decimal(user) + Decimal(1000)
        ) / _PER_M

    def test_reserve_depends_on_the_chunk_size(self):
        scope = _scope(250)
        rendered = _canonical(scope)

        assert reserve_of(scope, rendered, _settings(SEMANTIC_DISCOVERY_CHUNK_NAMES=50)) != (
            reserve_of(scope, rendered, S)
        )


class TestExpected:
    def test_the_first_chunk_writes_the_prefix_later_chunks_read_it(self):
        s = _prices()
        scope = _scope(5)
        rendered = _canonical(scope, s)
        block = 48 + 2 * 1634
        users = [
            (_user_bytes(scope, s, (1, 2), True), 0),
            (_user_bytes(scope, s, (3, 4), False), block),
            (_user_bytes(scope, s, (5, 5), False), block),
        ]
        rates = [Decimal(3), Decimal("0.5"), Decimal("0.5")]
        expected = sum(
            Decimal(rendered.prefix_bytes) * rate + (Decimal(u) + Decimal(b) / 2) * 1 + 1000
            for rate, (u, b) in zip(rates, users, strict=True)
        ) / _PER_M

        assert expected_of(scope, rendered, s) == expected

    def test_a_single_chunk_has_no_cached_prefix(self):
        s = _prices(SEMANTIC_DISCOVERY_CHUNK_NAMES=100)
        scope = _scope(3)
        rendered = _canonical(scope, s)
        user = _user_bytes(scope, s, (1, 3), True)

        assert expected_of(scope, rendered, s) == (
            Decimal(rendered.prefix_bytes) * 3 + Decimal(user) + Decimal(1000)
        ) / _PER_M

    def test_estimate_of_returns_the_reserve_and_the_expected_cost(self):
        scope = _scope(250)
        rendered = _canonical(scope)

        assert estimate_of(scope, rendered, S) == (
            reserve_of(scope, rendered, S), expected_of(scope, rendered, S)
        )
