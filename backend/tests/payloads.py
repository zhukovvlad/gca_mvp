"""Конструктор синтетических `ParseResult.data` для тестов импорта и матчинга.

Полный конвейер на `fixtures/gp_estimate_fixture.xlsx` стоит ~20 с (лемматизация
1070 уникальных наименований, `docs/phase3-parser.md` §3), поэтому большинство
тестов подаёт готовую структуру в сервис импорта мимо парсера
(`docs/phase4-start.md` §8). Форма структуры повторяет вывод парсера буква в
букву: деньги — десятичные строки либо None, количества — числа, ключи позиций —
строки-номера.
"""
from __future__ import annotations

from typing import Any

from parser.constants import (
    JSON_KEY_ADDITIONAL_WORKS_SOURCE_ROW,
    JSON_KEY_ARTICLE_SMR,
    JSON_KEY_BASELINE_PROPOSAL,
    JSON_KEY_CHAPTER_NUMBER,
    JSON_KEY_CHAPTER_REF,
    JSON_KEY_COMMENT_CONTRACTOR,
    JSON_KEY_COMMENT_ORGANIZER,
    JSON_KEY_CONTRACTOR_ACCREDITATION,
    JSON_KEY_CONTRACTOR_ADDITIONAL_INFO,
    JSON_KEY_CONTRACTOR_ADDITIONAL_WORKS,
    JSON_KEY_CONTRACTOR_ADDRESS,
    JSON_KEY_CONTRACTOR_COORDINATE,
    JSON_KEY_CONTRACTOR_HEIGHT,
    JSON_KEY_CONTRACTOR_INN,
    JSON_KEY_CONTRACTOR_ITEMS,
    JSON_KEY_CONTRACTOR_POSITIONS,
    JSON_KEY_CONTRACTOR_SUMMARY,
    JSON_KEY_CONTRACTOR_TITLE,
    JSON_KEY_CONTRACTOR_WIDTH,
    JSON_KEY_EXECUTOR,
    JSON_KEY_EXECUTOR_DATE,
    JSON_KEY_EXECUTOR_NAME,
    JSON_KEY_EXECUTOR_PHONE,
    JSON_KEY_INDIRECT_COSTS,
    JSON_KEY_IS_CHAPTER,
    JSON_KEY_JOB_TITLE,
    JSON_KEY_JOB_TITLE_NORMALIZED,
    JSON_KEY_LOT_TITLE,
    JSON_KEY_LOTS,
    JSON_KEY_MATERIALS,
    JSON_KEY_NUMBER,
    JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST,
    JSON_KEY_PROPOSALS,
    JSON_KEY_QUANTITY,
    JSON_KEY_SUGGESTED_QUANTITY,
    JSON_KEY_TENDER_ADDRESS,
    JSON_KEY_TENDER_ID,
    JSON_KEY_TENDER_OBJECT,
    JSON_KEY_TENDER_TITLE,
    JSON_KEY_TOTAL,
    JSON_KEY_TOTAL_COST,
    JSON_KEY_TOTAL_COST_VAT,
    JSON_KEY_UNIT,
    JSON_KEY_UNIT_COST,
    JSON_KEY_VAT,
    JSON_KEY_WORKS,
)
from parser.postprocess import BASELINE_MISSING_TITLE
from services.additional_works import SVEDENIYA_KEY

DEFAULT_OBJECT = "Объект 0"
DEFAULT_ADDRESS = "ул. Тестовая, 0"
DEFAULT_CONTRACTOR = "Подрядчик 0"
DEFAULT_INN = "7700000000"


def _cost(materials=None, works=None, indirect=None, total=None) -> dict[str, Any]:
    return {
        JSON_KEY_MATERIALS: materials,
        JSON_KEY_WORKS: works,
        JSON_KEY_INDIRECT_COSTS: indirect,
        JSON_KEY_TOTAL: total,
    }


def position(
    *,
    job_title: str | None,
    unit: str | None = None,
    quantity: int | float | None = None,
    suggested_quantity: int | float | None = None,
    unit_cost_total: str | None = None,
    total_cost_total: str | None = None,
    unit_cost: dict[str, Any] | None = None,
    total_cost: dict[str, Any] | None = None,
    is_chapter: bool = False,
    chapter_number: str | None = None,
    chapter_ref: str | None = None,
    number: str = "1",
    comment_organizer: str | None = None,
    comment_contractor: str | None = None,
    organizer_total: str | None = None,
    article_smr: str | None = None,
    job_title_normalized: str | None = None,
) -> dict[str, Any]:
    """Одна позиция в форме, которую отдаёт парсер (colspan 11)."""
    return {
        JSON_KEY_NUMBER: number,
        JSON_KEY_CHAPTER_NUMBER: chapter_number,
        JSON_KEY_ARTICLE_SMR: article_smr,
        JSON_KEY_JOB_TITLE: job_title,
        JSON_KEY_COMMENT_ORGANIZER: comment_organizer,
        JSON_KEY_UNIT: unit,
        JSON_KEY_QUANTITY: quantity,
        JSON_KEY_SUGGESTED_QUANTITY: suggested_quantity,
        JSON_KEY_UNIT_COST: unit_cost or _cost(total=unit_cost_total),
        JSON_KEY_TOTAL_COST: total_cost or _cost(total=total_cost_total),
        JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST: organizer_total,
        JSON_KEY_COMMENT_CONTRACTOR: comment_contractor,
        JSON_KEY_JOB_TITLE_NORMALIZED: job_title_normalized,
        JSON_KEY_IS_CHAPTER: is_chapter,
        JSON_KEY_CHAPTER_REF: chapter_ref,
    }


def summary_line(job_title: str, total: str | None = None) -> dict[str, Any]:
    return {
        JSON_KEY_JOB_TITLE: job_title,
        JSON_KEY_SUGGESTED_QUANTITY: None,
        JSON_KEY_UNIT_COST: _cost(),
        JSON_KEY_TOTAL_COST: _cost(total=total),
        JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST: None,
        JSON_KEY_COMMENT_CONTRACTOR: None,
    }


def additional_works_row(
    *,
    total: str | None = None,
    job_title: str = "Дополнительные работы",
    source_row: int = 999,
    suggested_quantity: int | float | None = None,
    unit_cost: dict[str, Any] | None = None,
    total_cost: dict[str, Any] | None = None,
    organizer_total: str | None = None,
    comment_contractor: str | None = None,
) -> dict[str, Any]:
    """Агрегатная строка допработ в форме, которую строит парсер (Ф4, спека
    §2.1): `job_title` и номер строки листа, плюс денежный блок подрядчика —
    те же пять ключей, что отдаёт `parse_contractor_row` для `colspan=11`
    (`get_lot_positions.py:153-157`) и что использует `summary_line`. `total`
    ложится в `total_cost.total` — контрольная сумма расшивки (спека §2.4,
    §2.6). Гейт формы 1.1.0 (спека §2.7) сравнивает ИМЕННО эти пять ключей
    строки-позиции с этими же ключами здесь, поэтому форма обязана быть
    побайтово той же, что у `position()`.
    """
    return {
        JSON_KEY_JOB_TITLE: job_title,
        JSON_KEY_ADDITIONAL_WORKS_SOURCE_ROW: source_row,
        JSON_KEY_SUGGESTED_QUANTITY: suggested_quantity,
        JSON_KEY_UNIT_COST: unit_cost or _cost(),
        JSON_KEY_TOTAL_COST: total_cost or _cost(total=total),
        JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST: organizer_total,
        JSON_KEY_COMMENT_CONTRACTOR: comment_contractor,
    }


def svedeniya_info(*lines: str) -> dict[str, str]:
    """`additional_info` с ключом «Сведения по дополнительным работам» (спека
    §2.4): значение — переданные строки, склеенные `\\n`, как их отдаёт
    `get_additional_info`. Ключ берётся из `services.additional_works.SVEDENIYA_KEY`,
    чтобы тестовый ключ не мог разойтись с продакшен-константой.
    """
    return {SVEDENIYA_KEY: "\n".join(lines)}


def proposal(
    positions: list[dict[str, Any]],
    *,
    title: str = DEFAULT_CONTRACTOR,
    inn: str = DEFAULT_INN,
    summary: dict[str, Any] | None = None,
    additional_info: dict[str, str] | None = None,
    additional_works: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        JSON_KEY_CONTRACTOR_TITLE: title,
        JSON_KEY_CONTRACTOR_INN: inn,
        JSON_KEY_CONTRACTOR_ADDRESS: "г. Тест, ул. Подрядная, 1",
        JSON_KEY_CONTRACTOR_ACCREDITATION: None,
        JSON_KEY_CONTRACTOR_COORDINATE: "J6",
        JSON_KEY_CONTRACTOR_WIDTH: 11,
        JSON_KEY_CONTRACTOR_HEIGHT: 1,
        JSON_KEY_CONTRACTOR_ITEMS: {
            JSON_KEY_CONTRACTOR_POSITIONS: {
                str(i): pos for i, pos in enumerate(positions, start=1)
            },
            JSON_KEY_CONTRACTOR_SUMMARY: summary
            if summary is not None
            else {
                JSON_KEY_TOTAL_COST_VAT: summary_line("Итого, руб. с учётом НДС", "1200.00"),
                JSON_KEY_VAT: summary_line("В том числе НДС", "200.00"),
            },
            # Ключ создаётся ВСЕГДА, как это делает парсер (`get_proposals.py:92`):
            # `contractor_items` у него всегда несёт `additional_works`, значение —
            # `None`, если агрегатной строки в файле нет (спека §1.5 факт 7).
            JSON_KEY_CONTRACTOR_ADDITIONAL_WORKS: additional_works,
        },
        JSON_KEY_CONTRACTOR_ADDITIONAL_INFO: additional_info
        if additional_info is not None
        else {"Условия оплаты": "Аванс 30%", "Гарантия": ""},
    }


def _default_position() -> dict[str, Any]:
    """Позиция по умолчанию — РАСЦЕНЁННАЯ.

    Иначе каждая смета по умолчанию срабатывала бы на эвристику «формулы без
    кэша» (все денежные поля NULL), и предупреждение шумело бы в тестах, которые
    проверяют совсем другое.
    """
    return position(
        job_title="Работа 1",
        unit="м2",
        quantity=1,
        suggested_quantity=10,
        unit_cost_total="100.00",
        total_cost_total="1000.00",
    )


def estimate_payload(
    positions: list[dict[str, Any]] | None = None,
    *,
    tender_id: str = "001-ГП",
    tender_title: str = "Смета к договору генподряда",
    tender_object: str = DEFAULT_OBJECT,
    tender_address: str = DEFAULT_ADDRESS,
    executor_date: str | None = None,
    proposals: dict[str, dict[str, Any]] | None = None,
    lots: dict[str, dict[str, Any]] | None = None,
    baseline_title: str = BASELINE_MISSING_TITLE,
    **proposal_kwargs: Any,
) -> dict[str, Any]:
    """Полная структура `ParseResult.data` с одним лотом и одним подрядчиком."""
    if lots is None:
        if proposals is None:
            proposals = {
                "contractor_1": proposal(
                    positions if positions is not None else [_default_position()],
                    **proposal_kwargs,
                )
            }
        lots = {
            "lot_1": {
                JSON_KEY_LOT_TITLE: "Лот №1 - Тестовый",
                JSON_KEY_PROPOSALS: proposals,
                JSON_KEY_BASELINE_PROPOSAL: {JSON_KEY_CONTRACTOR_TITLE: baseline_title},
            }
        }

    payload = {
        JSON_KEY_TENDER_ID: tender_id,
        JSON_KEY_TENDER_TITLE: tender_title,
        JSON_KEY_TENDER_OBJECT: tender_object,
        JSON_KEY_TENDER_ADDRESS: tender_address,
        JSON_KEY_EXECUTOR: {
            JSON_KEY_EXECUTOR_NAME: None,
            JSON_KEY_EXECUTOR_PHONE: None,
            JSON_KEY_EXECUTOR_DATE: executor_date,
        },
        JSON_KEY_LOTS: lots,
    }
    return payload


def payload_for(contract, positions: list[dict[str, Any]] | None = None, **kwargs: Any) -> dict[str, Any]:
    """Структура, реквизиты которой СОВПАДАЮТ с карточкой договора.

    Иначе сверка шапки (§3) вернула бы предупреждения в каждом тесте импорта, и
    тесты, проверяющие именно расхождения, потеряли бы смысл.
    """
    kwargs.setdefault("tender_object", contract.object.title)
    kwargs.setdefault("tender_address", contract.object.address)
    kwargs.setdefault("title", contract.contractor.title)
    kwargs.setdefault("inn", contract.contractor.inn)
    return estimate_payload(positions, **kwargs)
