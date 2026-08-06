"""Разбор строк «Сведений» и правило единогласия — без БД и без xlsx (спека Ф4 §4.2).

Суммы всюду синтетические и круглые (политика `samples/`, спека §6): ни одна
цифра здесь не взята из реального файла.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from services.additional_works import (
    LINE_RE,
    SVEDENIYA_KEY,
    categories_by_chapter_number,
    parse_lines,
    resolve_ref,
    svedeniya_text,
)
from services.category_resolution import CategoryRef, CategoryResolver
from tests.payloads import position

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
