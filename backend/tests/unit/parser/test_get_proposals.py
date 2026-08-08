"""Тесты сборки предложений подрядчиков.

Перенос `app/tests/excel_parser/test_get_proposals.py` из
`parser_tender_xlsx@0e178c0`.

Изменение при переносе: `get_summary` теперь получает третий аргумент —
начало блока позиций (`search_start_row`), см. `parser/get_summary.py`.
Тесты, проверявшие «в get_summary передаются ровно два аргумента»,
переписаны под новую сигнатуру.

Добавлен тест на смету ГП с одним подрядчиком (AGENTS.md §4).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from openpyxl import Workbook

from parser.constants import (
    JSON_KEY_CONTRACTOR_ACCREDITATION,
    JSON_KEY_CONTRACTOR_ADDITIONAL_INFO,
    JSON_KEY_CONTRACTOR_ADDRESS,
    JSON_KEY_CONTRACTOR_COORDINATE,
    JSON_KEY_CONTRACTOR_HEIGHT,
    JSON_KEY_CONTRACTOR_INN,
    JSON_KEY_CONTRACTOR_ITEMS,
    JSON_KEY_CONTRACTOR_POSITIONS,
    JSON_KEY_CONTRACTOR_SUMMARY,
    JSON_KEY_CONTRACTOR_TITLE,
    JSON_KEY_CONTRACTOR_WIDTH,
    JSON_KEY_VAT_RATE,
)
from parser.get_lot_positions import LotRows
from parser.get_proposals import get_proposals
from parser.summary_block import SummaryBlock

MODULE = "parser.get_proposals"

#: Номер строки шапки колонок, который `parse_worksheet` проводит вниз.
#: На синтетических листах этих тестов шапка не заполнена, поэтому значение
#: служит только для сигнатуры; 9 — та строка, где шапка стоит во всех четырёх
#: известных файлах.
COLUMN_HEADER_ROW = 9


@pytest.fixture
def empty_worksheet():
    return Workbook().active


@pytest.fixture
def sample_contractors_data():
    """Ответ read_contractors: ячейка-маркер и два подрядчика.

    Ширина блока подрядчика — 11 (единственная поддерживаемая ширина сметы
    ГП): `money_group_offsets` в `get_proposals` принимает только
    `SUPPORTED_CONTRACTOR_COLSPANS`, и синтетическая ширина 1 больше не
    годится (спека Ф4б §2.3). `column_start` у двух подрядчиков — 2 и 4, их
    11-колоночные блоки при этом перекрываются; для чтения ячеек шапки это
    безразлично — лист пустой, обе метки дают `None`.
    """
    return [
        {
            "value": "HEADER_ROW",
            "row_start": 1,
            "column_start": 1,
            "coordinate": "A1",
            "merged_shape": {"rowspan": 1, "colspan": 1},
        },
        {
            "value": "ООО Строитель",
            "row_start": 2,
            "column_start": 2,
            "coordinate": "B2",
            "merged_shape": {"rowspan": 1, "colspan": 11},
        },
        {
            "value": "АО Подрядчик",
            "row_start": 2,
            "column_start": 4,
            "coordinate": "D2",
            "merged_shape": {"rowspan": 1, "colspan": 11},
        },
    ]


@pytest.fixture
def sample_positions_data():
    return {"1": {"job_title": "Позиция 1"}, "2": {"job_title": "Позиция 2"}}


@pytest.fixture
def sample_summary_data():
    return {"total_amount": 15000, "vat": 2700}


@pytest.fixture
def sample_additional_info():
    return {"comment": "Дополнительная информация", "deadline": "30 дней"}


def _patch_collaborators(positions=None, summary=None, additional=None, contractors=None):
    """Контекст с подменёнными соседями get_proposals."""
    summary_block = SummaryBlock(lines={} if summary is None else summary, warnings=[])
    return (
        patch(f"{MODULE}.read_contractors", return_value=contractors),
        patch(f"{MODULE}.get_lot_positions", return_value=LotRows(positions={} if positions is None else positions)),
        patch(f"{MODULE}.get_summary", return_value=summary_block),
        patch(f"{MODULE}.get_additional_info", return_value={} if additional is None else additional),
    )


class TestGetProposalsBasicBehavior:
    """Базовое поведение."""

    def test_returns_empty_dict_when_no_contractors(self, empty_worksheet):
        with patch(f"{MODULE}.read_contractors", return_value=None):
            assert get_proposals(empty_worksheet, 10, 20, header_row=COLUMN_HEADER_ROW).proposals == {}

    def test_returns_empty_dict_when_empty_contractors_list(self, empty_worksheet):
        with patch(f"{MODULE}.read_contractors", return_value=[]):
            assert get_proposals(empty_worksheet, 10, 20, header_row=COLUMN_HEADER_ROW).proposals == {}

    def test_skips_first_contractor_entry(self, empty_worksheet, sample_contractors_data):
        """Нулевой элемент — ячейка-маркер, а не подрядчик."""
        p1, p2, p3, p4 = _patch_collaborators(contractors=sample_contractors_data)
        with p1, p2, p3, p4:
            result = get_proposals(empty_worksheet, 10, 20, header_row=COLUMN_HEADER_ROW).proposals

        assert sorted(result) == ["contractor_1", "contractor_2"]


class TestGetProposalsDataExtraction:
    """Извлечение реквизитов подрядчика из ячеек."""

    def test_extracts_basic_contractor_info(self, empty_worksheet, sample_contractors_data):
        empty_worksheet.cell(row=3, column=2, value="1234567890")
        empty_worksheet.cell(row=4, column=2, value="г. Москва, ул. Ленина, д.1")
        empty_worksheet.cell(row=5, column=2, value="Аккредитация есть")

        p1, p2, p3, p4 = _patch_collaborators(contractors=sample_contractors_data)
        with p1, p2, p3, p4:
            result = get_proposals(empty_worksheet, 10, 20, header_row=COLUMN_HEADER_ROW).proposals

        contractor_1 = result["contractor_1"]
        assert contractor_1[JSON_KEY_CONTRACTOR_TITLE] == "ООО Строитель"
        assert contractor_1[JSON_KEY_CONTRACTOR_INN] == "1234567890"
        assert contractor_1[JSON_KEY_CONTRACTOR_ADDRESS] == "г. Москва, ул. Ленина, д.1"
        assert contractor_1[JSON_KEY_CONTRACTOR_ACCREDITATION] == "Аккредитация есть"

    def test_extracts_coordinate_and_dimensions(self, empty_worksheet, sample_contractors_data):
        p1, p2, p3, p4 = _patch_collaborators(contractors=sample_contractors_data)
        with p1, p2, p3, p4:
            result = get_proposals(empty_worksheet, 10, 20, header_row=COLUMN_HEADER_ROW).proposals

        contractor_1 = result["contractor_1"]
        assert contractor_1[JSON_KEY_CONTRACTOR_COORDINATE] == "B2"
        assert contractor_1[JSON_KEY_CONTRACTOR_WIDTH] == 11
        assert contractor_1[JSON_KEY_CONTRACTOR_HEIGHT] == 1

    def test_handles_merged_cells(self, empty_worksheet):
        """При rowspan > 1 реквизиты под заголовком не читаются.

        Ширина блока — 10, а не 3: `money_group_offsets` принимает только
        `SUPPORTED_CONTRACTOR_COLSPANS` (спека Ф4б §2.3). `rowspan` = 2 —
        предмет самого теста — не трогается.
        """
        contractors_with_merged = [
            {
                "value": "HEADER",
                "row_start": 1,
                "column_start": 1,
                "coordinate": "A1",
                "merged_shape": {"rowspan": 1, "colspan": 1},
            },
            {
                "value": "ООО Большая компания",
                "row_start": 2,
                "column_start": 2,
                "coordinate": "B2",
                "merged_shape": {"rowspan": 2, "colspan": 10},
            },
        ]

        p1, p2, p3, p4 = _patch_collaborators(contractors=contractors_with_merged)
        with p1, p2, p3, p4:
            result = get_proposals(empty_worksheet, 10, 20, header_row=COLUMN_HEADER_ROW).proposals

        contractor_1 = result["contractor_1"]
        assert contractor_1[JSON_KEY_CONTRACTOR_WIDTH] == 10
        assert contractor_1[JSON_KEY_CONTRACTOR_HEIGHT] == 2
        assert contractor_1[JSON_KEY_CONTRACTOR_INN] is None
        assert contractor_1[JSON_KEY_CONTRACTOR_ADDRESS] is None
        assert contractor_1[JSON_KEY_CONTRACTOR_ACCREDITATION] is None


class TestGetProposalsModuleIntegration:
    """Взаимодействие с соседними модулями."""

    def test_calls_get_lot_positions_with_correct_parameters(self, empty_worksheet, sample_contractors_data):
        with (
            patch(f"{MODULE}.read_contractors", return_value=sample_contractors_data),
            patch(f"{MODULE}.get_lot_positions", return_value=LotRows(positions={})) as mock_positions,
            patch(f"{MODULE}.get_summary", return_value=SummaryBlock(lines={}, warnings=[])),
            patch(f"{MODULE}.get_additional_info", return_value={}),
        ):
            get_proposals(empty_worksheet, 15, 25, header_row=COLUMN_HEADER_ROW)

        assert mock_positions.call_count == 2

        call_args = mock_positions.call_args_list[0]
        assert call_args[0][0] is empty_worksheet
        assert call_args[0][1] == sample_contractors_data[1]
        assert call_args[1]["lot_start_row"] == 15
        assert call_args[1]["lot_end_row"] == 25

    def test_calls_get_summary_for_each_contractor(self, empty_worksheet, sample_contractors_data):
        with (
            patch(f"{MODULE}.read_contractors", return_value=sample_contractors_data),
            patch(f"{MODULE}.get_lot_positions", return_value=LotRows(positions={})),
            patch(f"{MODULE}.get_summary", return_value=SummaryBlock(lines={}, warnings=[])) as mock_summary,
            patch(f"{MODULE}.get_additional_info", return_value={}),
        ):
            get_proposals(empty_worksheet, 10, 20, header_row=COLUMN_HEADER_ROW)

        assert mock_summary.call_count == 2

        for i, call_args in enumerate(mock_summary.call_args_list):
            assert call_args[0][0] is empty_worksheet
            assert call_args[0][1] == sample_contractors_data[i + 1]

    def test_calls_get_additional_info_for_each_contractor(self, empty_worksheet, sample_contractors_data):
        with (
            patch(f"{MODULE}.read_contractors", return_value=sample_contractors_data),
            patch(f"{MODULE}.get_lot_positions", return_value=LotRows(positions={})),
            patch(f"{MODULE}.get_summary", return_value=SummaryBlock(lines={}, warnings=[])),
            patch(f"{MODULE}.get_additional_info", return_value={}) as mock_additional,
        ):
            get_proposals(empty_worksheet, 10, 20, header_row=COLUMN_HEADER_ROW)

        assert mock_additional.call_count == 2


class TestGetProposalsDataAggregation:
    """Сборка итоговой структуры."""

    def test_combines_positions_and_summary_in_items(
        self, empty_worksheet, sample_contractors_data, sample_positions_data, sample_summary_data
    ):
        p1, p2, p3, p4 = _patch_collaborators(
            contractors=sample_contractors_data,
            positions=sample_positions_data,
            summary=sample_summary_data,
        )
        with p1, p2, p3, p4:
            result = get_proposals(empty_worksheet, 10, 20, header_row=COLUMN_HEADER_ROW).proposals

        items = result["contractor_1"][JSON_KEY_CONTRACTOR_ITEMS]
        assert items[JSON_KEY_CONTRACTOR_POSITIONS] == sample_positions_data
        assert items[JSON_KEY_CONTRACTOR_SUMMARY] == sample_summary_data

    def test_includes_additional_info_in_proposal(
        self, empty_worksheet, sample_contractors_data, sample_additional_info
    ):
        p1, p2, p3, p4 = _patch_collaborators(
            contractors=sample_contractors_data, additional=sample_additional_info
        )
        with p1, p2, p3, p4:
            result = get_proposals(empty_worksheet, 10, 20, header_row=COLUMN_HEADER_ROW).proposals

        assert result["contractor_1"][JSON_KEY_CONTRACTOR_ADDITIONAL_INFO] == sample_additional_info

    def test_creates_complete_proposal_structure(
        self,
        empty_worksheet,
        sample_contractors_data,
        sample_positions_data,
        sample_summary_data,
        sample_additional_info,
    ):
        empty_worksheet.cell(row=3, column=2, value="1234567890")
        empty_worksheet.cell(row=4, column=2, value="г. Москва")
        empty_worksheet.cell(row=5, column=2, value="Есть")

        p1, p2, p3, p4 = _patch_collaborators(
            contractors=sample_contractors_data,
            positions=sample_positions_data,
            summary=sample_summary_data,
            additional=sample_additional_info,
        )
        with p1, p2, p3, p4:
            result = get_proposals(empty_worksheet, 10, 20, header_row=COLUMN_HEADER_ROW).proposals

        contractor_1 = result["contractor_1"]
        for field in (
            JSON_KEY_CONTRACTOR_TITLE,
            JSON_KEY_CONTRACTOR_INN,
            JSON_KEY_CONTRACTOR_ADDRESS,
            JSON_KEY_CONTRACTOR_ACCREDITATION,
            JSON_KEY_CONTRACTOR_COORDINATE,
            JSON_KEY_CONTRACTOR_WIDTH,
            JSON_KEY_CONTRACTOR_HEIGHT,
            JSON_KEY_CONTRACTOR_ITEMS,
            JSON_KEY_CONTRACTOR_ADDITIONAL_INFO,
        ):
            assert field in contractor_1, field

        items = contractor_1[JSON_KEY_CONTRACTOR_ITEMS]
        assert JSON_KEY_CONTRACTOR_POSITIONS in items
        assert JSON_KEY_CONTRACTOR_SUMMARY in items


class TestGetProposalsMultipleContractors:
    """Несколько подрядчиков и нумерация ключей."""

    def test_processes_multiple_contractors(self, empty_worksheet, sample_contractors_data):
        p1, p2, p3, p4 = _patch_collaborators(contractors=sample_contractors_data)
        with p1, p2, p3, p4:
            result = get_proposals(empty_worksheet, 10, 20, header_row=COLUMN_HEADER_ROW).proposals

        assert result["contractor_1"][JSON_KEY_CONTRACTOR_TITLE] == "ООО Строитель"
        assert result["contractor_2"][JSON_KEY_CONTRACTOR_TITLE] == "АО Подрядчик"

    def test_contractor_keys_increment_correctly(self, empty_worksheet):
        """Ширина блока каждого подрядчика — 11: единственная поддерживаемая
        `money_group_offsets` (спека Ф4б §2.3); ключ-маркер HEADER эту функцию
        не проходит (индекс 0 в обходе `get_proposals` пропускается), поэтому
        его ширина не имеет значения."""
        contractors_data = [
            {
                "value": name,
                "row_start": 2,
                "column_start": col,
                "coordinate": f"{chr(64 + col)}2",
                "merged_shape": {"rowspan": 1, "colspan": 1 if name == "HEADER" else 11},
            }
            for name, col in [("HEADER", 1), ("Подрядчик 1", 2), ("Подрядчик 2", 4), ("Подрядчик 3", 6)]
        ]

        p1, p2, p3, p4 = _patch_collaborators(contractors=contractors_data)
        with p1, p2, p3, p4:
            result = get_proposals(empty_worksheet, 10, 20, header_row=COLUMN_HEADER_ROW).proposals

        assert sorted(result) == ["contractor_1", "contractor_2", "contractor_3"]

    def test_gp_estimate_yields_exactly_one_proposal(self, empty_worksheet):
        """Смета ГП: один подрядчик — ровно одно предложение (AGENTS.md §4).

        Отдельной ветки «один подрядчик» в коде нет и не нужно: слой proposals
        сохранён как задел на возврат тендеров, а число предложений определяется
        числом заголовков в строке контрагентов.
        """
        contractors_data = [
            {
                "value": "Наименование контрагента",
                "row_start": 6,
                "column_start": 7,
                "coordinate": "G6",
                "merged_shape": {"rowspan": 1, "colspan": 2},
            },
            {
                "value": 'ООО "ТЕСТПОДРЯД"',
                "row_start": 6,
                "column_start": 10,
                "coordinate": "J6",
                "merged_shape": {"rowspan": 1, "colspan": 11},
            },
        ]

        p1, p2, p3, p4 = _patch_collaborators(contractors=contractors_data)
        with p1, p2, p3, p4:
            result = get_proposals(empty_worksheet, 11, 2600, header_row=COLUMN_HEADER_ROW).proposals

        assert list(result) == ["contractor_1"]
        assert result["contractor_1"][JSON_KEY_CONTRACTOR_WIDTH] == 11


class TestGetProposalsVatRate:
    """Ключ vat_rate (спека Ф4б §2.1): контракт `get_proposals`, не полный путь."""

    def test_vat_rate_key_is_always_present_even_as_none(self, empty_worksheet, sample_contractors_data):
        """Синтетический лист пуст: обе метки шапки молчат, ставка не получена —
        но ключ обязан лечь в предложение, а не отсутствовать (спека §2.1)."""
        p1, p2, p3, p4 = _patch_collaborators(contractors=sample_contractors_data)
        with p1, p2, p3, p4:
            result = get_proposals(empty_worksheet, 10, 20, header_row=COLUMN_HEADER_ROW).proposals

        contractor_1 = result["contractor_1"]
        assert JSON_KEY_VAT_RATE in contractor_1
        assert contractor_1[JSON_KEY_VAT_RATE] is None

    def test_vat_rate_sits_next_to_width_not_inside_items(self, empty_worksheet, sample_contractors_data):
        """Ставка — свойство блока подрядчика, как contractor_width, а не
        содержимое contractor_items (спека §2.1)."""
        p1, p2, p3, p4 = _patch_collaborators(contractors=sample_contractors_data)
        with p1, p2, p3, p4:
            result = get_proposals(empty_worksheet, 10, 20, header_row=COLUMN_HEADER_ROW).proposals

        contractor_1 = result["contractor_1"]
        assert JSON_KEY_VAT_RATE not in contractor_1[JSON_KEY_CONTRACTOR_ITEMS]


class TestGetProposalsEdgeCases:
    """Граничные случаи."""

    def test_handles_missing_contractor_fields(self, empty_worksheet):
        incomplete_contractors = [
            {
                "value": "HEADER",
                "row_start": 1,
                "column_start": 1,
                "coordinate": "A1",
                "merged_shape": {"rowspan": 1, "colspan": 1},
            },
            {"value": "Неполные данные", "coordinate": "B2"},
        ]

        p1, p2, p3, p4 = _patch_collaborators(contractors=incomplete_contractors)
        with p1, p2, p3, p4:
            result = get_proposals(empty_worksheet, 10, 20, header_row=COLUMN_HEADER_ROW).proposals

        contractor_1 = result["contractor_1"]
        assert contractor_1[JSON_KEY_CONTRACTOR_TITLE] == "Неполные данные"
        assert contractor_1[JSON_KEY_CONTRACTOR_COORDINATE] == "B2"
        assert contractor_1[JSON_KEY_CONTRACTOR_WIDTH] == 1
        assert contractor_1[JSON_KEY_CONTRACTOR_HEIGHT] == 1

    def test_handles_none_values_in_cells(self, empty_worksheet, sample_contractors_data):
        p1, p2, p3, p4 = _patch_collaborators(contractors=sample_contractors_data)
        with p1, p2, p3, p4:
            result = get_proposals(empty_worksheet, 10, 20, header_row=COLUMN_HEADER_ROW).proposals

        contractor_1 = result["contractor_1"]
        assert contractor_1[JSON_KEY_CONTRACTOR_INN] is None
        assert contractor_1[JSON_KEY_CONTRACTOR_ADDRESS] is None
        assert contractor_1[JSON_KEY_CONTRACTOR_ACCREDITATION] is None

    def test_propagates_exceptions_from_collaborators(self, empty_worksheet, sample_contractors_data):
        """Ошибка разбора не глотается: импорт фазы 4 должен упасть в error."""
        with (
            patch(f"{MODULE}.read_contractors", return_value=sample_contractors_data),
            patch(f"{MODULE}.get_lot_positions", side_effect=RuntimeError("Ошибка позиций")),
            patch(f"{MODULE}.get_summary", return_value=SummaryBlock(lines={}, warnings=[])),
            patch(f"{MODULE}.get_additional_info", return_value={}),
            pytest.raises(RuntimeError, match="Ошибка позиций"),
        ):
            get_proposals(empty_worksheet, 10, 20, header_row=COLUMN_HEADER_ROW)


class TestGetProposalsLotBoundaries:
    """Передача границ лота вниз по стеку."""

    def test_passes_lot_boundaries_to_positions_function(self, empty_worksheet, sample_contractors_data):
        with (
            patch(f"{MODULE}.read_contractors", return_value=sample_contractors_data),
            patch(f"{MODULE}.get_lot_positions", return_value=LotRows(positions={})) as mock_positions,
            patch(f"{MODULE}.get_summary", return_value=SummaryBlock(lines={}, warnings=[])),
            patch(f"{MODULE}.get_additional_info", return_value={}),
        ):
            get_proposals(empty_worksheet, 100, 200, header_row=COLUMN_HEADER_ROW)

        for call_args in mock_positions.call_args_list:
            assert call_args[1]["lot_start_row"] == 100
            assert call_args[1]["lot_end_row"] == 200

    def test_summary_gets_start_row_but_not_end_row(self, empty_worksheet, sample_contractors_data):
        """Итоги ищутся от начала позиций и не ограничены концом лота.

        Начало нужно, чтобы поиск не наткнулся на объединённую ячейку шапки
        таблицы; конец не нужен — блок итогов лежит ниже всех позиций.
        """
        with (
            patch(f"{MODULE}.read_contractors", return_value=sample_contractors_data),
            patch(f"{MODULE}.get_lot_positions", return_value=LotRows(positions={})),
            patch(f"{MODULE}.get_summary", return_value=SummaryBlock(lines={}, warnings=[])) as mock_summary,
            patch(f"{MODULE}.get_additional_info", return_value={}),
        ):
            get_proposals(empty_worksheet, 100, 200, header_row=COLUMN_HEADER_ROW)

        for call_args in mock_summary.call_args_list:
            assert call_args.kwargs == {"search_start_row": 100}

    def test_additional_info_does_not_depend_on_lot_boundaries(self, empty_worksheet, sample_contractors_data):
        with (
            patch(f"{MODULE}.read_contractors", return_value=sample_contractors_data),
            patch(f"{MODULE}.get_lot_positions", return_value=LotRows(positions={})),
            patch(f"{MODULE}.get_summary", return_value=SummaryBlock(lines={}, warnings=[])),
            patch(f"{MODULE}.get_additional_info", return_value={}) as mock_additional,
        ):
            get_proposals(empty_worksheet, 100, 200, header_row=COLUMN_HEADER_ROW)

        for call_args in mock_additional.call_args_list:
            assert len(call_args.args) == 2
            assert call_args.kwargs == {}
