"""Ядро разбора блока итогов: распознавание меток и назначение ключей.

Ни `Worksheet`, ни файла — блок итогов это три строки и метка в колонке A,
синтетический вход моделирует его точно (спека §2.10).
"""
from __future__ import annotations

import pytest

from parser.summary_block import SummaryRow, assign_summary_keys

INCLUDING = "total_cost_including_vat"
VAT = "vat_amount"
EXCLUDING = "total_cost_excluding_vat"


def _row(row: int, label, values=None) -> SummaryRow:
    return SummaryRow(row=row, label=label, values=values or {})


class TestRecognition:
    def test_three_tax_labels_get_three_distinct_keys(self):
        """Ровно тот случай, ради которого заведена фича."""
        rows = [
            _row(10, "ИТОГО, руб. с учетом НДС"),
            _row(11, "В том числе НДС"),
            _row(12, "ИТОГО, руб. без учета НДС"),
        ]
        assignment = assign_summary_keys(rows)
        assert assignment.keys == [INCLUDING, VAT, EXCLUDING]
        assert assignment.unrecognized == []
        assert assignment.duplicated == []

    @pytest.mark.parametrize(
        "label",
        [
            "  ИТОГО, руб. с учетом НДС  ",
            "ИТОГО,  руб.  с  учетом  НДС",
            "ИТОГО,\xa0руб.\xa0с учетом НДС",
            "итого, руб. с учетом ндс",
            "ИТОГО, РУБ. С УЧЕТОМ НДС",
        ],
    )
    def test_normalization_before_exact_match(self, label):
        """Края, внутренние пробелы (включая неразрывный) и регистр не мешают."""
        assert assign_summary_keys([_row(10, label)]).keys == [INCLUDING]

    def test_substring_of_a_known_label_is_not_a_match(self):
        """Точное равенство, а не вхождение: «итого»+«ндс» и есть дефект."""
        assignment = assign_summary_keys([_row(10, "ИТОГО с учетом НДС по лоту")])
        assert assignment.keys == ["merged_10"]
        assert assignment.unrecognized == [(10, "ИТОГО с учетом НДС по лоту")]

    def test_tender_labels_are_still_matched_by_substring(self):
        """Их константы — маркеры подстроки, полного текста входа у нас нет."""
        rows = [
            _row(10, "Отклонение от расчетной стоимости, руб."),
            _row(11, "Первоначальная стоимость (справочно)"),
        ]
        assert assign_summary_keys(rows).keys == [
            "deviation_from_baseline_cost",
            "initial_cost",
        ]

    def test_unknown_label_falls_back_to_row_key(self):
        assignment = assign_summary_keys([_row(42, "Что-то незнакомое")])
        assert assignment.keys == ["merged_42"]
        assert assignment.unrecognized == [(42, "Что-то незнакомое")]

    def test_empty_label_is_unrecognized_not_a_crash(self):
        assignment = assign_summary_keys([_row(42, None)])
        assert assignment.keys == ["merged_42"]
        assert assignment.unrecognized == [(42, "")]


class TestInjectivity:
    def test_repeated_label_does_not_overwrite_the_first(self):
        """Точность метки коллизию НЕ закрывает — закрывает инъективность."""
        rows = [
            _row(10, "ИТОГО, руб. с учетом НДС"),
            _row(11, "ИТОГО, руб. с учетом НДС"),
        ]
        assignment = assign_summary_keys(rows)
        assert assignment.keys == [INCLUDING, "merged_11"]
        assert assignment.duplicated == [(11, "ИТОГО, руб. с учетом НДС", 10)]

    def test_repeated_tender_label_is_covered_too(self):
        rows = [
            _row(10, "Первоначальная стоимость"),
            _row(11, "Первоначальная стоимость, руб."),
        ]
        assignment = assign_summary_keys(rows)
        assert assignment.keys == ["initial_cost", "merged_11"]
        assert len(assignment.duplicated) == 1

    def test_duplicate_physical_row_number_is_rejected(self):
        """Совпадение номера строки — ошибка вызывающего кода, а не файла.

        Без явной проверки обе строки получили бы один и тот же фолбэк-ключ
        `merged_5`, коллизия не попала бы в `duplicated`, и модуль тихо
        воспроизвёл бы тот самый дефект, ради устранения которого он заведён.
        """
        rows = [
            _row(5, "Что-то незнакомое"),
            _row(5, "Другое незнакомое"),
        ]
        with pytest.raises(ValueError, match="5"):
            assign_summary_keys(rows)

    @pytest.mark.parametrize(
        "labels",
        [
            ["ИТОГО, руб. с учетом НДС", "В том числе НДС", "ИТОГО, руб. без учета НДС"],
            ["ИТОГО, руб. с учетом НДС", "В том числе НДС"],
            ["ИТОГО, руб. с учетом НДС", "ИТОГО, руб. с учетом НДС", "Незнакомая"],
            [None, None],
        ],
    )
    def test_keys_are_unique_and_one_per_row(self, labels):
        """Инвариант §2.3 на каждом входе: строк = ключей, и все ключи разные."""
        rows = [_row(100 + i, label) for i, label in enumerate(labels)]
        keys = assign_summary_keys(rows).keys
        assert len(keys) == len(rows)
        assert len(set(keys)) == len(rows)


from decimal import Decimal

from parser.summary_block import check_arithmetic, to_decimal

MONEY_COLUMNS = ("materials", "works", "indirect_costs", "total")


def _line(**amounts) -> dict:
    """Строка итогов в форме, в которой её отдаёт parse_contractor_row."""
    return {"total_cost": {name: amounts.get(name) for name in MONEY_COLUMNS}}


def _triple(gross, vat, net) -> dict:
    """Блок из трёх налоговых строк; одно и то же значение во все колонки."""
    return {
        INCLUDING: _line(**{name: gross for name in MONEY_COLUMNS}),
        VAT: _line(**{name: vat for name in MONEY_COLUMNS}),
        EXCLUDING: _line(**{name: net for name in MONEY_COLUMNS}),
    }


class TestToDecimal:
    def test_decimal_string_is_converted(self):
        assert to_decimal("120.50") == (Decimal("120.50"), None)

    def test_int_and_float_are_converted_through_str(self):
        assert to_decimal(7) == (Decimal("7"), None)
        assert to_decimal(0.1) == (Decimal("0.1"), None)

    def test_none_is_blank(self):
        assert to_decimal(None) == (None, None)

    @pytest.mark.parametrize("value", ["", "   ", "\xa0", "\n"])
    def test_blank_string_is_blank_not_unusable(self, value):
        """`money_to_json` пропускает '' как есть; косметически пустая ячейка
        не должна выглядеть негодным значением (спека §2.6)."""
        assert to_decimal(value) == (None, None)

    @pytest.mark.parametrize("value", ["n/a", "1 234,56", "#REF!"])
    def test_arbitrary_text_is_unusable_and_keeps_the_raw_value(self, value):
        assert to_decimal(value) == (None, value)

    @pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity"])
    def test_non_finite_is_unusable(self, value):
        """is_finite() — единственный фильтр, закрывающий все три случая."""
        assert to_decimal(value) == (None, value)


class TestArithmetic:
    def test_exact_identity_is_silent(self):
        report = check_arithmetic(_triple("120", "20", "100"))
        assert report.broken == []
        assert report.unverified == []

    def test_broken_identity_names_the_column_and_all_three_numbers(self):
        # Числа подобраны так, чтобы ни одно не было подстрокой другого:
        # с «120/20/99» утверждение прошло бы вакуозно — «20» лежит внутри «120».
        report = check_arithmetic(_triple("500", "77", "400"))
        assert len(report.broken) == len(MONEY_COLUMNS)
        assert "materials" in report.broken[0]
        for number in ("500", "77", "400"):
            assert number in report.broken[0]
        assert report.unverified == []

    def test_all_three_blank_in_a_column_is_silent(self):
        report = check_arithmetic(_triple(None, None, None))
        assert report.broken == []
        assert report.unverified == []

    def test_partial_triple_is_unverified(self):
        lines = _triple("120", None, "100")
        report = check_arithmetic(lines)
        assert len(report.unverified) == len(MONEY_COLUMNS)
        assert report.broken == []

    @pytest.mark.parametrize("bad", ["n/a", "#REF!", "NaN", "sNaN"])
    def test_unusable_value_is_unverified_and_named(self, bad):
        report = check_arithmetic(_triple("120", bad, "100"))
        assert report.broken == []
        assert len(report.unverified) == len(MONEY_COLUMNS)
        assert bad in report.unverified[0]

    def test_infinity_does_not_pass_as_agreement(self):
        """Главная дыра: Infinity == Infinity + 100 истинно, и без фильтра
        противоречивый файл выглядел бы сошедшимся."""
        report = check_arithmetic(_triple("Infinity", "100", "Infinity"))
        assert report.broken == []
        assert len(report.unverified) == len(MONEY_COLUMNS)

    def test_opposite_infinities_do_not_raise(self):
        """`-Infinity + Infinity` бросает InvalidOperation на СЛОЖЕНИИ —
        фильтр обязан отработать раньше."""
        report = check_arithmetic(_triple("100", "Infinity", "-Infinity"))
        assert report.broken == []
        assert len(report.unverified) == len(MONEY_COLUMNS)

    def test_missing_tax_line_means_no_check_at_all(self):
        """Сверка идёт, только если присутствуют все три налоговые строки."""
        lines = _triple("120", "20", "100")
        del lines[EXCLUDING]
        report = check_arithmetic(lines)
        assert report.broken == []
        assert report.unverified == []


from parser.summary_block import SummaryBlock, build_summary_block


def _summary_row(row: int, label, **amounts) -> SummaryRow:
    return SummaryRow(row=row, label=label, values=_line(**amounts))


def _full_triple_rows(gross="120", vat="20", net="100") -> list[SummaryRow]:
    return [
        _summary_row(10, "ИТОГО, руб. с учетом НДС", **{c: gross for c in MONEY_COLUMNS}),
        _summary_row(11, "В том числе НДС", **{c: vat for c in MONEY_COLUMNS}),
        _summary_row(12, "ИТОГО, руб. без учета НДС", **{c: net for c in MONEY_COLUMNS}),
    ]


def _warned(block: SummaryBlock, fragment: str) -> list[str]:
    return [text for text in block.warnings if fragment in text]


class TestBuildSummaryBlock:
    def test_three_rows_give_three_keys_and_no_warnings(self):
        block = build_summary_block(_full_triple_rows(), search_start_row=5)
        assert sorted(block.lines) == sorted([INCLUDING, VAT, EXCLUDING])
        assert block.warnings == []

    def test_job_title_keeps_the_raw_cell_value(self):
        block = build_summary_block(_full_triple_rows(), search_start_row=5)
        assert block.lines[INCLUDING]["job_title"] == "ИТОГО, руб. с учетом НДС"

    def test_values_are_carried_through_untouched(self):
        block = build_summary_block(_full_triple_rows(), search_start_row=5)
        assert block.lines[VAT]["total_cost"]["total"] == "20"

    @pytest.mark.parametrize(
        "rows",
        [
            _full_triple_rows(),
            [_summary_row(10, "ИТОГО, руб. с учетом НДС"), _summary_row(11, "В том числе НДС")],
            [_summary_row(10, "Незнакомая"), _summary_row(11, "Незнакомая")],
        ],
    )
    def test_invariant_rows_equal_keys(self, rows):
        """Инвариант §2.3 — на каждом входе."""
        block = build_summary_block(rows, search_start_row=5)
        assert len(block.lines) == len(rows)


class TestWarnings:
    def test_block_not_found_names_the_search_start(self):
        block = build_summary_block([], search_start_row=11)
        assert block.lines == {}
        assert _warned(block, "Блок итогов не найден")
        assert "11" in _warned(block, "Блок итогов не найден")[0]

    def test_unrecognized_labels_are_one_aggregated_warning_with_the_actual_text(self):
        rows = [_summary_row(10, "Первое чужое"), _summary_row(11, "Второе чужое")]
        block = build_summary_block(rows, search_start_row=5)
        found = _warned(block, "не распознан")
        assert len(found) == 1
        assert "Первое чужое" in found[0] and "Второе чужое" in found[0]

    def test_examples_are_truncated_with_a_tail(self):
        rows = [_summary_row(10 + i, f"Чужая метка {i}") for i in range(7)]
        block = build_summary_block(rows, search_start_row=5)
        found = _warned(block, "не распознан")
        assert len(found) == 1
        assert "…и ещё 2" in found[0]

    def test_duplicate_label_is_its_own_warning(self):
        rows = [
            _summary_row(10, "ИТОГО, руб. с учетом НДС"),
            _summary_row(11, "ИТОГО, руб. с учетом НДС"),
        ]
        block = build_summary_block(rows, search_start_row=5)
        found = _warned(block, "встретилась дважды")
        assert len(found) == 1
        assert "merged_11" in found[0]

    def test_empty_vat_row_warns_without_claiming_anything_about_the_header(self):
        rows = [
            _summary_row(10, "ИТОГО, руб. с учетом НДС", **{c: "120" for c in MONEY_COLUMNS}),
            _summary_row(11, "В том числе НДС"),
        ]
        block = build_summary_block(rows, search_start_row=5)
        found = _warned(block, "суммы не указаны")
        assert len(found) == 1
        assert "не заявлен" not in found[0], "предупреждение не имеет права судить о шапке файла"

    def test_missing_gross_row_warns_and_nothing_is_reconstructed(self):
        rows = [
            _summary_row(10, "В том числе НДС", **{c: "20" for c in MONEY_COLUMNS}),
            _summary_row(11, "ИТОГО, руб. без учета НДС", **{c: "100" for c in MONEY_COLUMNS}),
        ]
        block = build_summary_block(rows, search_start_row=5)
        assert INCLUDING not in block.lines
        assert _warned(block, "Валовое ИТОГО отсутствует")

    def test_present_labels_are_truncated_in_the_gross_missing_warning(self):
        """Усечение перечня — требование ко ВСЕМ агрегированным предупреждениям.

        Это единственное место, где перечень растёт по числу строк блока, а не
        по числу отклонений, поэтому без усечения текст ничем не ограничен.
        """
        rows = [_summary_row(10 + index, f"Чужая метка {index}") for index in range(7)]
        block = build_summary_block(rows, search_start_row=5)
        found = _warned(block, "Валовое ИТОГО отсутствует")
        assert len(found) == 1
        assert "…и ещё 2" in found[0]

    def test_broken_arithmetic_warns(self):
        block = build_summary_block(_full_triple_rows(net="99"), search_start_row=5)
        assert _warned(block, "не сходится")

    def test_unverified_arithmetic_is_a_different_warning(self):
        block = build_summary_block(_full_triple_rows(vat="NaN"), search_start_row=5)
        assert _warned(block, "не проверена")
        assert not _warned(block, "не сходится")

    def test_broken_and_unverified_are_two_separate_warnings(self):
        """Спека §2.6: это ДВА предупреждения, а не одно с переменным текстом.

        Ни один другой тест этого не требует: каждый из них включает лишь одну
        ветку, и реализация, склеивающая оба смысла в одну строку с условными
        фрагментами, прошла бы их все. Здесь обе ветки срабатывают на одном
        блоке — «materials» противоречит себе, «works» сверить нечем, — и
        сообщений обязано быть два, причём ни одно не содержит маркер другого.
        """
        lines = {
            INCLUDING: _line(materials="120", works="120", indirect_costs="120", total="120"),
            VAT: _line(materials="20", works=None, indirect_costs="20", total="20"),
            EXCLUDING: _line(materials="99", works="100", indirect_costs="100", total="100"),
        }
        rows = [
            _summary_row(10, "ИТОГО, руб. с учетом НДС", **lines[INCLUDING]["total_cost"]),
            _summary_row(11, "В том числе НДС", **lines[VAT]["total_cost"]),
            _summary_row(12, "ИТОГО, руб. без учета НДС", **lines[EXCLUDING]["total_cost"]),
        ]
        block = build_summary_block(rows, search_start_row=5)

        broken = _warned(block, "не сходится")
        unverified = _warned(block, "не проверена")
        assert len(broken) == 1
        assert len(unverified) == 1
        assert broken[0] is not unverified[0]
        assert "не проверена" not in broken[0]
        assert "не сходится" not in unverified[0]

    def test_full_correct_block_is_completely_silent(self):
        """Форма fixture после Task 1: ни одного предупреждения."""
        assert build_summary_block(_full_triple_rows(), search_start_row=5).warnings == []
