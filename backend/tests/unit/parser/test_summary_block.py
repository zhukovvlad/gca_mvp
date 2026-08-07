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
