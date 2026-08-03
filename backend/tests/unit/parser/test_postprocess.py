"""Тесты постобработки структуры.

Перенос `app/tests/excel_parser/test_postprocess.py` из
`parser_tender_xlsx@0e178c0` без содержательных изменений — логика при
адаптации не менялась.

Здесь же проверяется ветка, которая и есть «адаптация без baseline»
(AGENTS.md §5.2): в смете ГП предложения «Расчетная стоимость» нет.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from parser import postprocess as postprocess_module
from parser.constants import (
    JSON_KEY_BASELINE_PROPOSAL,
    JSON_KEY_CHAPTER_NUMBER,
    JSON_KEY_CHAPTER_REF,
    JSON_KEY_CONTRACTOR_ITEMS,
    JSON_KEY_CONTRACTOR_POSITIONS,
    JSON_KEY_CONTRACTOR_SUMMARY,
    JSON_KEY_CONTRACTOR_TITLE,
    JSON_KEY_DEVIATION_FROM_CALCULATED_COST,
    JSON_KEY_IS_CHAPTER,
    JSON_KEY_LOTS,
    JSON_KEY_PROPOSALS,
    JSON_KEY_TOTAL_COST,
    TABLE_PARSE_BASELINE_COST,
)
from parser.postprocess import (
    BASELINE_MISSING_TITLE,
    DataIntegrityError,
    _clean_deviation_fields,
    _is_value_zero,
    annotate_structure_fields,
    normalize_lots_json_structure,
    replace_excel_errors_with_null,
    stringify_temporal_values,
)


@contextmanager
def _captured_warnings(logger: logging.Logger) -> Iterator[list[str]]:
    """Собирает WARNING-сообщения конкретного логгера, минуя root-хендлеры."""
    messages: list[str] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    handler = _Collector(level=logging.WARNING)
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        yield messages
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


# =================================================================
# 1. replace_excel_errors_with_null
# =================================================================


def test_replace_excel_errors_replaces_div0_variants():
    """Набор исходника: обе записи деления на ноль и русский вариант."""
    input_data = {
        "err1": "#DIV/0!",
        "err2": "  div/0  ",
        "err3": "деление на 0",
        "items": [{"value": "#DIV/0!"}],
    }
    expected = {"err1": None, "err2": None, "err3": None, "items": [{"value": None}]}

    assert replace_excel_errors_with_null(input_data) == expected


def test_replace_excel_errors_replaces_every_excel_error_literal():
    """Отступление от исходника: `#N/A` в денежном поле — такое же «нет значения».

    После перевода денег в строки (§2.6 отчёта) фаза 4 не отличила бы `#REF!`
    от суммы по типу — узнала бы только на `Decimal(value)`.
    """
    literals = ["#N/A", "#NAME?", "#NULL!", "#NUM!", "#REF!", "#VALUE!", "#SPILL!", "#CALC!"]

    assert replace_excel_errors_with_null({"errs": literals}) == {"errs": [None] * len(literals)}
    # регистр и крайние пробелы не мешают
    assert replace_excel_errors_with_null(" #n/a ") is None


def test_replace_excel_errors_does_not_change_valid_data():
    input_data = {"value": "Some string", "cost": 100.5, "items": [1, 2]}

    assert replace_excel_errors_with_null(input_data) == input_data


# =================================================================
# 1a. stringify_temporal_values
# =================================================================


def test_stringify_temporal_values_converts_all_temporal_types():
    """openpyxl отдаёт date-форматированные ячейки объектами — jsonb их не примет."""
    import datetime as dt

    input_data = {
        "a": dt.datetime(2025, 2, 1, 12, 30),
        "b": dt.date(2025, 2, 1),
        "items": [dt.time(12, 30), dt.timedelta(hours=26)],
        "text": "как есть",
        "num": 1.5,
    }

    result = stringify_temporal_values(input_data)

    assert result == {
        "a": "2025-02-01T12:30:00",
        "b": "2025-02-01",
        "items": ["12:30:00", "1 day, 2:00:00"],
        "text": "как есть",
        "num": 1.5,
    }


def test_stringify_temporal_values_makes_data_json_serializable():
    import datetime as dt
    import json

    data = {"nested": {"deep": [dt.datetime(2025, 2, 1)]}}

    json.dumps(stringify_temporal_values(data))  # TypeError здесь — провал теста


# =================================================================
# 2. _is_value_zero
# =================================================================


@pytest.mark.parametrize("value", [None, 0, "0", "0.0", "", "0,0", "  NONE  "])
def test_is_value_zero_returns_true_for_zero_values(value):
    assert _is_value_zero(value) is True


@pytest.mark.parametrize("value", [1, -1, "100", "some text", 0.1])
def test_is_value_zero_returns_false_for_non_zero_values(value):
    assert _is_value_zero(value) is False


# =================================================================
# 3. annotate_structure_fields
# =================================================================


def test_annotate_structure_fields_happy_path():
    """Разделы, подразделы и обычные позиции."""
    positions = {
        "1": {JSON_KEY_CHAPTER_NUMBER: "1"},
        "2": {"description": "Work in section 1"},
        "3": {JSON_KEY_CHAPTER_NUMBER: "1.1"},
        "4": {"description": "Work in subsection 1.1"},
    }

    annotated = annotate_structure_fields(positions)

    assert annotated["1"][JSON_KEY_IS_CHAPTER] is True
    assert annotated["1"][JSON_KEY_CHAPTER_REF] is None  # верхний уровень
    assert annotated["2"][JSON_KEY_IS_CHAPTER] is False
    assert annotated["2"][JSON_KEY_CHAPTER_REF] == "1"
    assert annotated["3"][JSON_KEY_IS_CHAPTER] is True
    assert annotated["3"][JSON_KEY_CHAPTER_REF] == "1"  # ссылка на родителя
    assert annotated["4"][JSON_KEY_IS_CHAPTER] is False
    assert annotated["4"][JSON_KEY_CHAPTER_REF] == "1.1"


def test_annotate_structure_fields_empty_input():
    assert annotate_structure_fields({}) == {}


def test_annotate_structure_fields_with_non_integer_keys():
    """Нечисловые ключи не роняют функцию, но пишут предупреждение.

    Лог ловится собственным хендлером, а не фикстурой `caplog`: приложение при
    инициализации логгирования делает `root.handlers.clear()` (logging_config.py),
    и в общем прогоне это сносит хендлер caplog вместе с остальными. Тест на
    caplog здесь проходил бы в одиночку и падал в полном suite.
    """
    positions = {"1": {"description": "Work 1"}, "abc": {"description": "Work with non-int key"}}

    with _captured_warnings(postprocess_module.log) as messages:
        annotated = annotate_structure_fields(positions)

    assert sorted(annotated) == ["1", "abc"]
    assert any("Не удалось отсортировать позиции" in m for m in messages)


def test_annotate_structure_fields_handles_non_dict_input():
    assert annotate_structure_fields(None) == {}
    assert annotate_structure_fields("не словарь") == {}


def test_annotate_structure_fields_does_not_mutate_input():
    positions = {"1": {JSON_KEY_CHAPTER_NUMBER: "1"}}

    annotate_structure_fields(positions)

    assert JSON_KEY_IS_CHAPTER not in positions["1"]


# =================================================================
# 4. normalize_lots_json_structure
# =================================================================


@pytest.fixture
def sample_tender_data():
    """Тендерная таблица: подрядчик плюс валидная «Расчетная стоимость»."""
    return {
        JSON_KEY_LOTS: {
            "lot_1": {
                JSON_KEY_PROPOSALS: {
                    "proposal_1": {
                        JSON_KEY_CONTRACTOR_TITLE: "Подрядчик 1",
                        JSON_KEY_CONTRACTOR_ITEMS: {
                            JSON_KEY_CONTRACTOR_POSITIONS: {
                                "1": {
                                    "description": "Работа 1",
                                    JSON_KEY_DEVIATION_FROM_CALCULATED_COST: 10,
                                }
                            },
                            JSON_KEY_CONTRACTOR_SUMMARY: {
                                JSON_KEY_DEVIATION_FROM_CALCULATED_COST: {"total": 10}
                            },
                        },
                    },
                    "proposal_2": {
                        JSON_KEY_CONTRACTOR_TITLE: TABLE_PARSE_BASELINE_COST,
                        JSON_KEY_CONTRACTOR_ITEMS: {
                            JSON_KEY_CONTRACTOR_SUMMARY: {
                                "some_total": {JSON_KEY_TOTAL_COST: {"value": 1000}}
                            }
                        },
                    },
                }
            }
        }
    }


def test_normalize_with_valid_baseline(sample_tender_data):
    """Расчётная стоимость есть и значима — отклонения сохраняются."""
    result = normalize_lots_json_structure(sample_tender_data)
    lot_1 = result[JSON_KEY_LOTS]["lot_1"]

    assert lot_1[JSON_KEY_BASELINE_PROPOSAL][JSON_KEY_CONTRACTOR_TITLE] == TABLE_PARSE_BASELINE_COST

    assert list(lot_1[JSON_KEY_PROPOSALS]) == ["contractor_1"]
    contractor_1 = lot_1[JSON_KEY_PROPOSALS]["contractor_1"]
    assert contractor_1[JSON_KEY_CONTRACTOR_TITLE] == "Подрядчик 1"

    pos_1 = contractor_1[JSON_KEY_CONTRACTOR_ITEMS][JSON_KEY_CONTRACTOR_POSITIONS]["1"]
    assert JSON_KEY_DEVIATION_FROM_CALCULATED_COST in pos_1


def test_normalize_with_invalid_baseline(sample_tender_data):
    """Расчётная стоимость есть, но нулевая — отклонения вычищаются."""
    baseline = sample_tender_data[JSON_KEY_LOTS]["lot_1"][JSON_KEY_PROPOSALS]["proposal_2"]
    baseline[JSON_KEY_CONTRACTOR_ITEMS][JSON_KEY_CONTRACTOR_SUMMARY]["some_total"][JSON_KEY_TOTAL_COST][
        "value"
    ] = "0.0"

    result = normalize_lots_json_structure(sample_tender_data)
    lot_1 = result[JSON_KEY_LOTS]["lot_1"]

    assert lot_1[JSON_KEY_BASELINE_PROPOSAL][JSON_KEY_CONTRACTOR_TITLE] == BASELINE_MISSING_TITLE

    contractor_1 = lot_1[JSON_KEY_PROPOSALS]["contractor_1"]
    pos_1 = contractor_1[JSON_KEY_CONTRACTOR_ITEMS][JSON_KEY_CONTRACTOR_POSITIONS]["1"]
    summary = contractor_1[JSON_KEY_CONTRACTOR_ITEMS][JSON_KEY_CONTRACTOR_SUMMARY]

    assert JSON_KEY_DEVIATION_FROM_CALCULATED_COST not in pos_1
    assert JSON_KEY_DEVIATION_FROM_CALCULATED_COST not in summary


def test_normalize_without_baseline(sample_tender_data):
    """Расчётной стоимости нет вовсе — это и есть случай сметы ГП."""
    del sample_tender_data[JSON_KEY_LOTS]["lot_1"][JSON_KEY_PROPOSALS]["proposal_2"]

    result = normalize_lots_json_structure(sample_tender_data)
    lot_1 = result[JSON_KEY_LOTS]["lot_1"]

    assert lot_1[JSON_KEY_BASELINE_PROPOSAL][JSON_KEY_CONTRACTOR_TITLE] == BASELINE_MISSING_TITLE

    contractor_1 = lot_1[JSON_KEY_PROPOSALS]["contractor_1"]
    pos_1 = contractor_1[JSON_KEY_CONTRACTOR_ITEMS][JSON_KEY_CONTRACTOR_POSITIONS]["1"]
    assert JSON_KEY_DEVIATION_FROM_CALCULATED_COST not in pos_1


def test_normalize_does_not_mutate_input(sample_tender_data):
    """Функция работает на копии — исходный словарь остаётся нетронутым."""
    del sample_tender_data[JSON_KEY_LOTS]["lot_1"][JSON_KEY_PROPOSALS]["proposal_2"]

    normalize_lots_json_structure(sample_tender_data)

    assert list(sample_tender_data[JSON_KEY_LOTS]["lot_1"][JSON_KEY_PROPOSALS]) == ["proposal_1"]
    assert JSON_KEY_BASELINE_PROPOSAL not in sample_tender_data[JSON_KEY_LOTS]["lot_1"]


def test_normalize_raises_error_on_malformed_position(sample_tender_data):
    positions = sample_tender_data["lots"]["lot_1"]["proposals"]["proposal_1"]["contractor_items"]["positions"]
    positions["2"] = None

    with pytest.raises(DataIntegrityError, match="Ожидался словарь, но получен тип NoneType"):
        normalize_lots_json_structure(sample_tender_data)


def test_normalize_handles_missing_contractor_items(sample_tender_data):
    """Отсутствие contractor_items не роняет чистку отклонений."""
    baseline = sample_tender_data["lots"]["lot_1"]["proposals"]["proposal_2"]
    baseline["contractor_items"]["summary"]["some_total"]["total_cost"]["value"] = 0

    del sample_tender_data["lots"]["lot_1"]["proposals"]["proposal_1"]["contractor_items"]

    result = normalize_lots_json_structure(sample_tender_data)

    assert "contractor_items" not in result["lots"]["lot_1"]["proposals"]["contractor_1"]


def test_clean_deviation_fields_handles_malformed_items():
    """contractor_items не словарь — функция не падает."""
    proposals = {"Подрядчик 1": {"title": "Подрядчик 1", "contractor_items": None}}

    cleaned = _clean_deviation_fields(proposals)

    assert cleaned["Подрядчик 1"]["contractor_items"] is None
