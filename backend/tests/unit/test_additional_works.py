"""Разбор строк «Сведений», правило единогласия, владелец и матрица состояний —
без БД и без xlsx (спека Ф4 §4.2).

Суммы всюду синтетические и круглые (политика `samples/`, спека §6): ни одна
цифра здесь не взята из реального файла.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from parser.constants import (
    JSON_KEY_ADDITIONAL_WORKS_SOURCE_ROW,
    JSON_KEY_CONTRACTOR_ADDITIONAL_WORKS,
    JSON_KEY_CONTRACTOR_ITEMS,
    JSON_KEY_JOB_TITLE,
    JSON_KEY_LOTS,
    JSON_KEY_PROPOSALS,
    JSON_KEY_TOTAL,
    JSON_KEY_TOTAL_COST,
)
from services.additional_works import (
    LINE_RE,
    SVEDENIYA_KEY,
    build_rows,
    categories_by_chapter_number,
    decide_owner,
    parse_lines,
    resolve_ref,
    svedeniya_text,
)
from services.category_resolution import CategoryRef, CategoryResolver
from tests.payloads import position, proposal

#: Карта справочника: только коды, нужные тестам этого файла (образец —
#: `test_category_resolution.py`). «1» повторяет реальный случай корпуса
#: (спека §1.3, §2.5): номер раздела «1» дублируется строкой лота без статьи
#: и первым настоящим разделом со статьёй.
CATALOG = {
    "1": CategoryRef(id=101, title="Подготовительные работы"),
    "5.1": CategoryRef(id=151, title="Кровля"),
    "5.2": CategoryRef(id=152, title="Фасад"),
}


@pytest.fixture
def resolver() -> CategoryResolver:
    return CategoryResolver(CATALOG)


def rows(*positions):
    """Позиции в форме парсера с каноническими ключами «1..N» (как в Ф3)."""
    return {str(i): pos for i, pos in enumerate(positions, start=1)}


def chapter(number, *, article=None, title="Раздел", key_number="1"):
    return position(
        job_title=title, number=key_number, chapter_number=number,
        article_smr=article, is_chapter=True,
    )


def work(title="Работа", *, number="1", article=None, **extra):
    return position(job_title=title, number=number, article_smr=article, is_chapter=False, **extra)


def aggregate_row(total: str | None, *, job_title: str = "Дополнительные работы") -> dict[str, Any]:
    """Агрегатная строка допработ в форме парсера (`get_lot_positions.py:153-157`):
    только то, что читают `decide_owner`/`build_rows` — `job_title`, служебный
    номер строки листа и денежный блок с `total_cost.total`. Остальные поля
    денежного блока (материалы/СМР/косвенные) модулю не нужны и здесь не
    строятся — это не полная форма `parse_contractor_row`, а её проекция на то,
    что читает эта фича."""
    return {
        JSON_KEY_JOB_TITLE: job_title,
        JSON_KEY_ADDITIONAL_WORKS_SOURCE_ROW: 999,
        JSON_KEY_TOTAL_COST: {JSON_KEY_TOTAL: total},
    }


def proposal_with_row(additional_works: dict[str, Any] | None, **kwargs: Any) -> dict[str, Any]:
    """`payloads.proposal()` ключа `additional_works` не создаёт вовсе (замер §1.5
    факт 7 спеки Ф4: хелпер Task 5 добавит его в конструктор). До Task 5 ключ
    проставляется вручную, ровно в форме `get_proposals.py:92`, где он лежит
    внутри `contractor_items`, рядом с `positions` и `summary`."""
    data = proposal([], **kwargs)
    data[JSON_KEY_CONTRACTOR_ITEMS][JSON_KEY_CONTRACTOR_ADDITIONAL_WORKS] = additional_works
    return data


def lot(proposal_data: dict[str, Any]) -> dict[str, Any]:
    """Лот с ровно одним предложением (инвариант `AGENTS.md` §4) — только то, что
    читает `decide_owner`: ключ `proposals` с единственной записью. `lot_title` и
    `baseline_proposal` опущены — `decide_owner` их не читает, а строить полную
    форму лота ради них было бы шумом, не проверяющим ничего."""
    return {JSON_KEY_PROPOSALS: {"contractor_1": proposal_data}}


def estimate_data(**lots: dict[str, Any]) -> dict[str, Any]:
    """`data` в форме, которую потребляет `decide_owner`: только `JSON_KEY_LOTS`."""
    return {JSON_KEY_LOTS: lots}


class TestParseLines:
    """Каждый вход НАЗВАН и пройден руками до утверждения (инсайт
    replaying-new-rules.md, слой 1)."""

    def test_nbsp_and_decimal_comma_in_the_amount_give_one_decimal(self):
        """«Прочие - 1<NBSP>234,56 руб.»: между «1» и «234» — НЕРАЗРЫВНЫЙ
        пробел (не обычный пробел), дробная часть — через запятую. Регулярное
        выражение допускает и пробел, и NBSP внутри группы `amount`; чистка
        снимает оба вида пробела и меняет запятую на точку — одна `Decimal`,
        `float` нигде не участвует."""
        line = "Прочие - 1\u00a0234,56 руб."
        parsed, unreadable = parse_lines(line)
        assert unreadable == []
        assert len(parsed) == 1
        assert parsed[0].amount == Decimal("1234.56")

    def test_zero_amount_is_parsed(self):
        line = "Прочие - 0 руб."
        parsed, unreadable = parse_lines(line)
        assert unreadable == []
        assert len(parsed) == 1
        assert parsed[0].amount == Decimal("0")

    def test_line_without_a_leading_number_has_no_ref(self):
        line = "Прочие работы - 100 руб."
        parsed, unreadable = parse_lines(line)
        assert unreadable == []
        assert len(parsed) == 1
        assert parsed[0].ref is None

    def test_trailing_dot_in_the_ref_is_stripped(self):
        line = "3.2.2. Прочие работы - 100 руб."
        parsed, unreadable = parse_lines(line)
        assert unreadable == []
        assert len(parsed) == 1
        assert parsed[0].ref == "3.2.2"

    def test_line_without_rub_is_unreadable(self):
        """«Прочие работы - 100» — слова «руб» нет вовсе, `LINE_RE` не
        совпадает: проверено — `LINE_RE.match(line) is None`."""
        line = "Прочие работы - 100"
        assert LINE_RE.match(line) is None
        parsed, unreadable = parse_lines(line)
        assert parsed == []
        assert unreadable == [line]

    def test_line_without_an_amount_is_unreadable(self):
        """«Прочие работы -руб.» — между тире и «руб» нет НИ ОДНОГО символа из
        допустимого набора (даже пробела), поэтому `LINE_RE` не совпадает
        вовсе: проверено — `LINE_RE.match(line) is None`. Это отличает случай
        от `test_input_on_which_decimal_raises_invalid_operation_is_unreadable`
        ниже, где regex СОВПАДАЕТ, но сумма после чистки пуста."""
        line = "Прочие работы -руб."
        assert LINE_RE.match(line) is None
        parsed, unreadable = parse_lines(line)
        assert parsed == []
        assert unreadable == [line]

    def test_blank_title_after_trim_is_unreadable(self):
        """«   - 100 руб.» — три пробела перед тире; `LINE_RE` совпадает (title
        забирает себе один пробел, остальные — ведущий `\\s*`), но после
        обрезки название пусто: проверено — `match.group("title").strip() ==
        ""`."""
        line = "   - 100 руб."
        match = LINE_RE.match(line)
        assert match is not None
        assert match.group("title").strip() == ""
        parsed, unreadable = parse_lines(line)
        assert parsed == []
        assert unreadable == [line]

    def test_input_on_which_decimal_raises_invalid_operation_is_unreadable(self):
        """«Прочие -  руб.» (тире, ДВА пробела, «руб.») — регулярное выражение
        `[\\d\\s\\u00a0]+` не требует ни одной ЦИФРЫ, только один-или-больше
        символ из набора «цифра/пробел/NBSP», поэтому `LINE_RE` СОВПАДАЕТ и
        группа `amount` забирает себе один из двух пробелов (второй достаётся
        завершающему `\\s*` перед «руб»). После чистки (снятие пробелов) сумма
        — пустая строка, а `Decimal("")` бросает `InvalidOperation`. Проверено
        руками и отдельным прогоном интерпретатора (см. отчёт задачи):
        `LINE_RE.match(line) is not None`, а `match.group("amount").strip() ==
        ""`."""
        line = "Прочие -  руб."
        match = LINE_RE.match(line)
        assert match is not None
        assert match.group("amount").strip() == ""
        parsed, unreadable = parse_lines(line)
        assert parsed == []
        assert unreadable == [line]

    def test_blank_text_lines_are_skipped_not_counted_as_unreadable(self):
        text = "Раздел 1 - 100 руб.\n\n   \nРаздел 2 - 200 руб."
        parsed, unreadable = parse_lines(text)
        assert unreadable == []
        assert len(parsed) == 2
        assert [p.ordinal for p in parsed] == [1, 2]


class TestSvedeniyaText:
    """Выбор ключа блока «Дополнительная информация» (спека §2.4, §2.9)."""

    def test_exact_key_is_found(self):
        text, warning = svedeniya_text({SVEDENIYA_KEY: "запись о допработах"})
        assert text == "запись о допработах"
        assert warning is None

    def test_key_with_extra_spaces_and_different_case_is_also_found(self):
        info = {"  СВЕДЕНИЯ   по  Дополнительным   Работам  ": "запись о допработах"}
        text, warning = svedeniya_text(info)
        assert text == "запись о допработах"
        assert warning is None

    @pytest.mark.parametrize(
        "additional_info",
        [
            {SVEDENIYA_KEY: ""},
            {"Другой ключ": "значение"},
        ],
        ids=["empty_value", "key_absent"],
    )
    def test_empty_value_and_absent_key_give_none_identically(self, additional_info):
        """Спека §1.1: «ключа нет» и «ключ есть, значение пусто» — ОДНО
        состояние, одна ветка кода, один и тот же результат."""
        assert svedeniya_text(additional_info) == (None, None)

    def test_similar_but_wrong_key_gives_no_text_but_warns_with_the_actual_key(self):
        """«Сведения О дополнительных работах» (не «ПО») точно не совпадает,
        но соответствует нестрогому шаблону — текст не берётся, предупреждение
        называет фактический ключ."""
        actual_key = "Сведения о дополнительных работах"
        text, warning = svedeniya_text({actual_key: "запись о допработах"})
        assert text is None
        assert warning is not None
        assert actual_key in warning


class TestResolveRef:
    """Правило единогласия (спека §2.5): все восемь строк таблицы."""

    def test_single_candidate_with_a_category_is_attached(self, resolver):
        positions = rows(chapter("5.1", article="5.1. Кровля"), work())
        resolution = resolver.resolve_proposal(positions)
        by_number = categories_by_chapter_number(positions, resolution)
        result_id, reason = resolve_ref("5.1", by_number)
        assert result_id == 151
        assert reason is None

    def test_several_candidates_with_one_category_are_attached(self, resolver):
        """Два раздела с номером «5.1», у обоих — одна и та же статья."""
        positions = rows(
            chapter("5.1", article="5.1. Кровля"),
            chapter("5.1", article="5.1. Кровля"),
        )
        resolution = resolver.resolve_proposal(positions)
        by_number = categories_by_chapter_number(positions, resolution)
        result_id, reason = resolve_ref("5.1", by_number)
        assert result_id == 151
        assert reason is None

    def test_candidates_with_different_categories_stay_unassigned(self, resolver):
        """Два раздела с номером «5», статьи — разные («5.1» и «5.2»)."""
        positions = rows(
            chapter("5", article="5.1. Кровля"),
            chapter("5", article="5.2. Фасад"),
        )
        resolution = resolver.resolve_proposal(positions)
        by_number = categories_by_chapter_number(positions, resolution)
        result_id, reason = resolve_ref("5", by_number)
        assert result_id is None
        assert reason == "статьи различаются"

    def test_candidate_without_a_category_blocks_attachment(self, resolver):
        """Номер «1» дублируется строкой лота (без статьи) и настоящим
        разделом (со статьёй) — реальный случай корпуса (спека §1.3)."""
        positions = rows(
            chapter("1", title="Лот №1 - Тестовый"),
            chapter("1", article="1. Подготовительные работы"),
        )
        resolution = resolver.resolve_proposal(positions)
        by_number = categories_by_chapter_number(positions, resolution)
        result_id, reason = resolve_ref("1", by_number)
        assert result_id is None
        assert reason == "кандидат без статьи"

    def test_no_candidates_stay_unassigned(self, resolver):
        positions = rows(chapter("5.1", article="5.1. Кровля"))
        resolution = resolver.resolve_proposal(positions)
        by_number = categories_by_chapter_number(positions, resolution)
        result_id, reason = resolve_ref("9.9", by_number)
        assert result_id is None
        assert reason == "нет кандидатов"

    def test_ref_absent_means_no_resolution_attempt(self, resolver):
        positions = rows(chapter("5.1", article="5.1. Кровля"))
        resolution = resolver.resolve_proposal(positions)
        by_number = categories_by_chapter_number(positions, resolution)
        result_id, reason = resolve_ref(None, by_number)
        assert result_id is None
        assert reason is None

    def test_positions_of_another_kind_are_not_candidates(self, resolver):
        """Позиция (не раздел) со своим собственным `number == "1"` не должна
        стать кандидатом по НОМЕРУ РАЗДЕЛА: `categories_by_chapter_number`
        читает `chapter_number` только у строк с `kind is RowKind.CHAPTER`.
        Если бы реализация (ошибочно) учла ещё и позиции, `by_number["1"]`
        получил бы лишний `None` от строки-позиции (у позиции
        `work_category_id` всегда `None`), и «1» стало бы неоднозначным —
        ровно тот класс дефекта, которого эта проверка не допускает."""
        positions = rows(
            chapter("1", article="1. Подготовительные работы"),
            work(number="1"),
        )
        resolution = resolver.resolve_proposal(positions)
        by_number = categories_by_chapter_number(positions, resolution)
        assert by_number["1"] == {101}
        result_id, reason = resolve_ref("1", by_number)
        assert result_id == 101
        assert reason is None

    def test_disabled_structure_leaves_every_ref_unassigned(self, resolver):
        """Деградация D (спека Ф3 §2.4): номер раздела «прим.» не разбирается
        в глубину, вся структура предложения гасится, и КАЖДАЯ статья — `None`
        (проверено чтением `_disabled` в `category_resolution.py`: там
        `work_category_id` не задаётся вовсе). Поэтому «1» законно уходит в
        «кандидат без статьи» без единой строки специального кода."""
        positions = rows(
            chapter("1", article="1. Подготовительные работы"),
            chapter("прим.", title="Примечание"),
        )
        resolution = resolver.resolve_proposal(positions)
        assert resolution.structure_disabled is True
        by_number = categories_by_chapter_number(positions, resolution)
        result_id, reason = resolve_ref("1", by_number)
        assert result_id is None
        assert reason == "кандидат без статьи"


class TestBuildRowsMatrix:
    """Матрица состояний одного предложения, семь строк (спека §2.6), владелец
    уже известен — все тесты этого класса зовут `build_rows` с `is_owner=True`
    (правило владельца проверяется отдельно, в `TestDecideOwner`).

    В КАЖДОМ тесте, помимо состава записей, проверяется ИНВАРИАНТ «сумма записей
    предложения равна T, либо записей нет вовсе» — ради него написана вся
    функция (спека §2.6): он делает осмысленной сверку с независимым ИТОГО файла
    (Task 6), а здесь проверяется на каждом входе матрицы отдельно.
    """

    def test_no_row_and_empty_svedeniya_gives_nothing(self, resolver):
        """Строки нет, «Сведения» пусты (или ключа нет — то же состояние,
        спека §1.1): ноль записей, ноль предупреждений."""
        result = build_rows(
            additional_works=None,
            svedeniya=None,
            resolution=resolver.resolve_proposal({}),
            positions={},
            is_owner=True,
        )
        assert result.rows == ()
        assert result.warnings == ()

    def test_no_row_and_non_empty_svedeniya_gives_nothing_here(self, resolver):
        """Строки нет, «Сведения» непусты: ноль записей И ноль предупреждений
        НА ЭТОМ УРОВНЕ — предупреждение «текст называет деньги, которых нет в
        таблице» принадлежит смете (`decide_owner`), не предложению (спека §2.2,
        §2.6: «sheet-level, not here»). Проверено отдельно в `TestDecideOwner`."""
        result = build_rows(
            additional_works=None,
            svedeniya="5.1 Кровля - 100 руб.",
            resolution=resolver.resolve_proposal({}),
            positions={},
            is_owner=True,
        )
        assert result.rows == ()
        assert result.warnings == ()

    def test_row_present_and_empty_svedeniya_gives_one_unallocated_record(self, resolver):
        """Строка есть, «Сведения» пусты: 1 нераспределённая запись на весь T,
        `chapter_ref_raw` и `raw_line` — NULL, заголовок — `job_title` строки,
        `ordinal = 1`; предупреждение «допработы не расшиты»."""
        result = build_rows(
            additional_works=aggregate_row("200"),
            svedeniya=None,
            resolution=resolver.resolve_proposal({}),
            positions={},
            is_owner=True,
        )
        assert len(result.rows) == 1
        row = result.rows[0]
        assert row.ordinal == 1
        assert row.chapter_ref_raw is None
        assert row.raw_line is None
        assert row.title == "Дополнительные работы"
        assert row.total_amount == Decimal("200")
        assert row.work_category_id is None
        assert len(result.warnings) == 1
        assert "200" in result.warnings[0]
        assert sum(r.total_amount for r in result.rows) == Decimal("200")

    def test_row_present_and_parsed_sum_equals_total_gives_one_record_per_line(self, resolver):
        """`P = T`: по одной записи на разобранную строку, нераспределённой
        нет; предупреждение — ТОЛЬКО по резолву ссылки (вторая строка ссылается
        на номер «9.9», которого нет в справочнике этого предложения)."""
        positions = rows(chapter("5.1", article="5.1. Кровля"), work())
        resolution = resolver.resolve_proposal(positions)
        text = "5.1 Кровля - 100 руб.\n9.9 Прочее - 50 руб."
        result = build_rows(
            additional_works=aggregate_row("150"),
            svedeniya=text,
            resolution=resolution,
            positions=positions,
            is_owner=True,
        )
        assert len(result.rows) == 2
        first, second = result.rows
        assert first.ordinal == 1
        assert first.chapter_ref_raw == "5.1"
        assert first.total_amount == Decimal("100")
        assert first.work_category_id == 151
        assert second.ordinal == 2
        assert second.chapter_ref_raw == "9.9"
        assert second.total_amount == Decimal("50")
        assert second.work_category_id is None
        assert len(result.warnings) == 1
        assert "9.9" in result.warnings[0]
        assert "нет кандидатов" in result.warnings[0]
        assert sum(r.total_amount for r in result.rows) == Decimal("150")

    def test_row_present_and_parsed_sum_below_total_gives_remainder_record(self, resolver):
        """`P < T`: разобранная строка + 1 нераспределённая на остаток `T − P`
        с ПОСЛЕДНИМ ordinal; предупреждения — остаток (оба слагаемых) и
        нечитаемая строка (с сырьём)."""
        positions = rows(chapter("5.1", article="5.1. Кровля"), work())
        resolution = resolver.resolve_proposal(positions)
        text = "5.1 Кровля - 100 руб.\nПрочие -  руб."
        result = build_rows(
            additional_works=aggregate_row("130"),
            svedeniya=text,
            resolution=resolution,
            positions=positions,
            is_owner=True,
        )
        assert len(result.rows) == 2
        parsed_row, unallocated = result.rows
        assert parsed_row.ordinal == 1
        assert parsed_row.total_amount == Decimal("100")
        assert unallocated.ordinal == 2
        assert unallocated.chapter_ref_raw is None
        assert unallocated.raw_line is None
        assert unallocated.title == "Дополнительные работы"
        assert unallocated.total_amount == Decimal("30")
        assert unallocated.work_category_id is None
        assert len(result.warnings) == 2
        joined = " ".join(result.warnings)
        assert "30" in joined and "130" in joined and "100" in joined
        assert "Прочие -  руб." in joined
        assert sum(r.total_amount for r in result.rows) == Decimal("130")

    def test_row_present_and_parsed_sum_above_total_discards_the_breakdown(self, resolver):
        """`P > T`: РОВНО 1 нераспределённая запись на `T`, разобранные строки
        НЕ сохраняются; предупреждение называет ОБА числа."""
        positions = rows(chapter("5.1", article="5.1. Кровля"), work())
        resolution = resolver.resolve_proposal(positions)
        text = "5.1 Кровля - 100 руб.\nПрочее - 80 руб."
        result = build_rows(
            additional_works=aggregate_row("150"),
            svedeniya=text,
            resolution=resolution,
            positions=positions,
            is_owner=True,
        )
        assert len(result.rows) == 1
        row = result.rows[0]
        assert row.ordinal == 1
        assert row.chapter_ref_raw is None
        assert row.raw_line is None
        assert row.total_amount == Decimal("150")
        assert len(result.warnings) == 1
        assert "180" in result.warnings[0]
        assert "150" in result.warnings[0]
        assert sum(r.total_amount for r in result.rows) == Decimal("150")

    def test_row_present_and_total_is_empty_gives_nothing(self, resolver):
        """Строка есть, денег в ней нет (`T` пусто): ноль записей, одно
        предупреждение «агрегатная строка без суммы»."""
        result = build_rows(
            additional_works=aggregate_row(None),
            svedeniya="неважно что здесь написано",
            resolution=resolver.resolve_proposal({}),
            positions={},
            is_owner=True,
        )
        assert result.rows == ()
        assert len(result.warnings) == 1
        assert "Дополнительные работы" in result.warnings[0]


class TestDecideOwner:
    """Правило владельца «Сведений» — по смете, четыре строки таблицы (спека §2.2)."""

    def test_exactly_one_proposal_with_the_row_is_the_owner(self):
        data = estimate_data(
            lot_1=lot(proposal_with_row(None)),
            lot_2=lot(proposal_with_row(aggregate_row("500"))),
        )
        result = decide_owner(data)
        assert result.owner_lot_key == "lot_2"
        assert result.lot_keys_with_row == ("lot_2",)
        assert result.warnings == ()

    def test_no_owner_with_non_empty_svedeniya_gives_one_warning_per_estimate(self):
        info = {SVEDENIYA_KEY: "5.1 Кровля - 100 руб."}
        data = estimate_data(
            lot_1=lot(proposal_with_row(None, additional_info=info)),
            lot_2=lot(proposal_with_row(None, additional_info=info)),
        )
        result = decide_owner(data)
        assert result.owner_lot_key is None
        assert result.lot_keys_with_row == ()
        assert len(result.warnings) == 1

    def test_no_owner_with_empty_svedeniya_gives_zero_warnings(self):
        """Редакционная точность гейта 2 спеки: прежнее валидное состояние
        («Сведения» пусты, строки нет ни у кого) обязано остаться МОЛЧАЛИВЫМ."""
        info = {SVEDENIYA_KEY: ""}
        data = estimate_data(
            lot_1=lot(proposal_with_row(None, additional_info=info)),
            lot_2=lot(proposal_with_row(None, additional_info=info)),
        )
        result = decide_owner(data)
        assert result.owner_lot_key is None
        assert result.lot_keys_with_row == ()
        assert result.warnings == ()

    def test_two_or_more_owners_give_one_warning_naming_keys_and_totals(self):
        data = estimate_data(
            lot_1=lot(proposal_with_row(aggregate_row("500"))),
            lot_2=lot(proposal_with_row(aggregate_row(None))),
        )
        result = decide_owner(data)
        assert result.owner_lot_key is None
        assert result.lot_keys_with_row == ("lot_1", "lot_2")
        assert len(result.warnings) == 1
        warning = result.warnings[0]
        assert "2" in warning
        assert "lot_1" in warning
        assert "lot_2" in warning
        assert "500" in warning


class TestNoStateInModule:
    """Спека §4.2 / Task 4 Step 3, по образцу `TestInstanceIsStateless` Ф3
    (`test_category_resolution.py`): модуль — не класс, но опасность та же.
    Если бы предупреждения или записи копились где-то между вызовами (модульная
    переменная, мутируемое значение по умолчанию), второй вызов на другом
    предложении/смете унаследовал бы чужие данные, и найти это можно только
    позвав функции ДВАЖДЫ с разными входами и сверив, что второй результат не
    видит первого."""

    def test_one_instance_serves_two_proposals_without_leaking(self, resolver):
        first = build_rows(
            additional_works=aggregate_row("200"),
            svedeniya=None,
            resolution=resolver.resolve_proposal({}),
            positions={},
            is_owner=True,
        )
        second = build_rows(
            additional_works=None,
            svedeniya=None,
            resolution=resolver.resolve_proposal({}),
            positions={},
            is_owner=True,
        )
        assert len(first.warnings) == 1
        assert first.rows != ()
        assert second.warnings == ()
        assert second.rows == ()

        owner_a = decide_owner(estimate_data(lot_1=lot(proposal_with_row(aggregate_row("500")))))
        owner_b = decide_owner(
            estimate_data(
                lot_1=lot(proposal_with_row(None)),
                lot_2=lot(proposal_with_row(aggregate_row("300"))),
            )
        )
        assert owner_a.owner_lot_key == "lot_1"
        assert owner_a.warnings == ()
        assert owner_b.owner_lot_key == "lot_2"
        assert owner_b.warnings == ()
