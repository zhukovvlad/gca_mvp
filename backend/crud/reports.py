"""Данные для выгрузок §7.6: свод по договору и отчёт «для банка».

Макет отчёта «для банка» **согласован с пользователем** 2026-08-04 (§6.1 брифинга,
`AGENTS.md` §7.6 требует именно согласования, а не выдумывания):

| Что | Решение |
|---|---|
| Разрез | класс → работа |
| Колонки | работа, единица, объём, ставка, норматив, отклонение %, отклонение в деньгах |
| Итоги | по каждому классу **и** общий |
| Шапка | реквизиты выборки + блок подписей |

Два отчёта — **два файла** (решение §6.6): у них разный охват и разные параметры,
свод берёт один договор, а «для банка» — выборку из многих.

**Отклонение в итогах — средневзвешенное по объёму, не среднее арифметическое
процентов.** Разница не косметическая: работа на 12 млн с отклонением +5 % и работа
на 40 тыс. с +80 % дают среднее арифметическое +42,5 %, тогда как фактическая
переплата — около +5 %. Банку показывают вторую цифру. Поэтому итог считается как
`(факт − норматив) / норматив` по суммам, а не усреднением процентов.

**Строки отчёта «для банка» — только работы, у которых норматив есть.** Так прямо
попросил пользователь: «позиции без норматива в расчёт отклонения не входят и
показываются отдельным счётчиком». Иначе они разбавили бы средневзвешенное
отклонение вниз, и отчёт занизил бы переплату — то есть соврал бы в пользу
подрядчика. Счётчик исключённых печатается в итогах каждого класса и в общем итоге.

**Итоги считаются в Python, а не в SQL** — в отличие от матрицы (решение §6.3). Это
не противоречие: там SQL выигрывал потому, что Python пришлось бы перекачивать
140 тысяч строк ради 1200 ячеек, а здесь строки и есть содержимое файла, они
транспортируются в любом случае. Суммировать уже полученное дешевле, чем добавлять
второй запрос.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.analytics import (
    DEVIATIONS,
    column_scope_filters,
    get_latest_estimate,
    latest_estimates,
    scope_filters,
)
from crud.common import DomainError, iso
from models import (
    CatalogPosition,
    Contract,
    Contractor,
    ObjectModel,
    RateClass,
    UnitOfMeasure,
)

#: Ноль как Decimal — чтобы суммирование не начиналось с int и не давало float.
ZERO = Decimal(0)


def _weighted(amount: Decimal | None, volume: Decimal | None) -> Decimal | None:
    """Средневзвешенная величина; `None`, если веса нет (§6: ячейка пустая)."""
    if amount is None or volume is None or volume == 0:
        return None
    return amount / volume


def _deviation_pct(fact: Decimal | None, standard: Decimal | None) -> Decimal | None:
    """Отклонение по СУММАМ, а не усреднение процентов (см. модульную документацию)."""
    if fact is None or standard is None or standard == 0:
        return None
    return (fact / standard - 1) * 100


# ---------------------------------------------------------------------------
#  (а) Свод расценок по договору с отклонениями
# ---------------------------------------------------------------------------

def contract_summary(db: Session, contract_id: int) -> dict:
    """Свод по договору: все расценённые работы последней сметы с отклонениями.

    В отличие от отчёта «для банка», работы **без** норматива здесь остаются: это
    свод по договору, а не сравнение с нормативами, и человек должен видеть весь
    предмет торга. У таких строк отклонение — `None`, что на листе печатается как
    «нет норматива» (§10 требует отличать это от нуля).
    """
    contract_row = db.execute(
        sa.select(Contract, ObjectModel.title, Contractor.title, RateClass.title)
        .join(ObjectModel, ObjectModel.id == Contract.object_id)
        .join(Contractor, Contractor.id == Contract.contractor_id)
        .join(RateClass, RateClass.id == Contract.rate_class_id)
        .where(Contract.id == contract_id)
    ).first()
    if contract_row is None:
        raise DomainError(404, f"Договор {contract_id} не найден.")
    contract, object_title, contractor_title, rate_class_title = contract_row

    estimate = get_latest_estimate(db, contract_id)
    header = {
        "contract_number": contract.contract_number,
        "contract_title": contract.title,
        "object_title": object_title,
        "contractor_title": contractor_title,
        "rate_class_title": rate_class_title,
        "signer": contract.signer,
        "signed_date": iso(contract.signed_date),
        "total_amount": contract.total_amount,
        "estimate_amendment_no": estimate.amendment_no if estimate else None,
        "estimate_date": iso(estimate.data_prepared_on_date) if estimate else None,
    }
    if estimate is None:
        return {"header": header, "rows": [], "totals": _empty_report_totals()}

    grouped = db.execute(
        _work_aggregate_select()
        .where(DEVIATIONS.c.estimate_id == estimate.id, DEVIATIONS.c.weight > 0)
        .group_by(
            DEVIATIONS.c.catalog_position_id,
            CatalogPosition.standard_job_title,
            UnitOfMeasure.code,
        )
        .order_by(sa.desc("fact_amount_total"))
    ).all()

    rows = [_summary_row(r) for r in grouped]
    return {"header": header, "rows": rows, "totals": _totals_of(rows)}


def _work_aggregate_select():
    """Агрегаты по работе: факт, объём, норматив — раздельно для сравнимых строк.

    `FILTER (WHERE rate_standard_id IS NOT NULL)` отделяет сравнимые строки от
    остальных **внутри одного проходa**: иначе понадобился бы второй запрос, а его
    результат пришлось бы сшивать с первым по ключу.

    `fact_amount_total` (по всем строкам) нужен своду по договору — он показывает
    весь предмет торга; `fact_amount` (только по сравнимым) нужен отклонению, чтобы
    оно считалось от той же совокупности, что норматив.
    """
    comparable = DEVIATIONS.c.rate_standard_id.isnot(None)
    weighted_fact = DEVIATIONS.c.unit_cost_total * DEVIATIONS.c.weight
    weighted_standard = DEVIATIONS.c.standard_unit_rate * DEVIATIONS.c.weight
    return (
        sa.select(
            DEVIATIONS.c.catalog_position_id.label("catalog_position_id"),
            CatalogPosition.standard_job_title.label("job_title"),
            UnitOfMeasure.code.label("unit_code"),
            sa.func.sum(weighted_fact).label("fact_amount_total"),
            sa.func.sum(DEVIATIONS.c.weight).label("volume_total"),
            sa.func.sum(weighted_fact).filter(comparable).label("fact_amount"),
            sa.func.sum(DEVIATIONS.c.weight).filter(comparable).label("volume"),
            sa.func.sum(weighted_standard).filter(comparable).label("standard_amount"),
            sa.func.count().label("positions"),
            sa.func.count().filter(~comparable).label("positions_without_standard"),
        )
        .select_from(DEVIATIONS)
        .join(CatalogPosition, CatalogPosition.id == DEVIATIONS.c.catalog_position_id)
        .outerjoin(UnitOfMeasure, UnitOfMeasure.id == DEVIATIONS.c.unit_id)
    )


def _summary_row(row) -> dict:
    """Строка свода: ставка по всем строкам работы, отклонение — по сравнимым."""
    rate = _weighted(row.fact_amount_total, row.volume_total)
    standard = _weighted(row.standard_amount, row.volume)
    deviation_money = (
        None
        if row.fact_amount is None or row.standard_amount is None
        else row.fact_amount - row.standard_amount
    )
    return {
        "catalog_position_id": row.catalog_position_id,
        "job_title": row.job_title,
        "unit_code": row.unit_code,
        "volume": row.volume_total,
        "rate": rate,
        "standard_unit_rate": standard,
        "amount": row.fact_amount_total,
        "deviation_pct": _deviation_pct(row.fact_amount, row.standard_amount),
        "deviation_money": deviation_money,
        # Сравнимая часть — по ней считаются итоги, и она может быть меньше строки.
        "comparable_amount": row.fact_amount,
        "comparable_standard_amount": row.standard_amount,
        "positions": row.positions,
        "positions_without_standard": row.positions_without_standard,
    }


# ---------------------------------------------------------------------------
#  (б) Сравнение с нормативами «для банка»: класс → работа
# ---------------------------------------------------------------------------

def bank_comparison(
    db: Session,
    *,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    rate_class_id: int | None = None,
) -> dict:
    """Отчёт «для банка» по согласованному макету: секции по классам, внутри — работы.

    Выборка та же, что у матрицы (последние сметы договоров плюс фильтры класса и
    периода), поэтому отчёт и экран показывают одно и то же — иначе расхождение
    цифр между экраном и файлом пришлось бы объяснять банку.
    """
    latest = latest_estimates()
    scope = scope_filters(
        rate_class_id=rate_class_id, date_from=date_from, date_to=date_to
    )

    grouped = db.execute(
        _work_aggregate_select()
        .join(latest, latest.c.estimate_id == DEVIATIONS.c.estimate_id)
        .join(RateClass, RateClass.id == DEVIATIONS.c.rate_class_id)
        .add_columns(
            DEVIATIONS.c.rate_class_id.label("rate_class_id"),
            RateClass.title.label("rate_class_title"),
        )
        .where(DEVIATIONS.c.weight > 0, *scope)
        .group_by(
            DEVIATIONS.c.rate_class_id,
            RateClass.title,
            DEVIATIONS.c.catalog_position_id,
            CatalogPosition.standard_job_title,
            UnitOfMeasure.code,
        )
        .order_by(RateClass.title.asc(), sa.desc("fact_amount"))
    ).all()

    sections: list[dict] = []
    by_class: dict[int, dict] = {}
    for row in grouped:
        section = by_class.get(row.rate_class_id)
        if section is None:
            section = {
                "rate_class_id": row.rate_class_id,
                "rate_class_title": row.rate_class_title,
                "rows": [],
            }
            by_class[row.rate_class_id] = section
            sections.append(section)
        section["rows"].append(_bank_row(row))

    for section in sections:
        # Строки без норматива в отчёт не попадают, но их счётчик обязан дожить до
        # итогов: этого требует согласованный макет (§6.1).
        excluded = sum(r["positions_without_standard"] for r in section["rows"])
        section["rows"] = [r for r in section["rows"] if r["volume"] is not None]
        section["totals"] = _totals_of(section["rows"])
        section["totals"]["positions_without_standard"] = excluded

    return {
        "header": _bank_header(db, latest, date_from, date_to, rate_class_id),
        "sections": sections,
        "totals": _totals_of([r for s in sections for r in s["rows"]])
        | {
            "positions_without_standard": sum(
                s["totals"]["positions_without_standard"] for s in sections
            )
        },
    }


def _bank_row(row) -> dict:
    """Строка отчёта «для банка»: только сравнимая часть работы.

    Объём, ставка и стоимость берутся по строкам **с нормативом**, а не по всем: иначе
    «отклонение в деньгах» не совпало бы с разницей показанных сумм, и читатель не
    смог бы сойтись с отчётом на калькуляторе.
    """
    return {
        "catalog_position_id": row.catalog_position_id,
        "job_title": row.job_title,
        "unit_code": row.unit_code,
        "volume": row.volume,
        "rate": _weighted(row.fact_amount, row.volume),
        "standard_unit_rate": _weighted(row.standard_amount, row.volume),
        "amount": row.fact_amount,
        "standard_amount": row.standard_amount,
        "deviation_pct": _deviation_pct(row.fact_amount, row.standard_amount),
        "deviation_money": (
            None
            if row.fact_amount is None or row.standard_amount is None
            else row.fact_amount - row.standard_amount
        ),
        "comparable_amount": row.fact_amount,
        "comparable_standard_amount": row.standard_amount,
        "positions": row.positions,
        "positions_without_standard": row.positions_without_standard,
    }


def _bank_header(
    db: Session,
    latest,
    date_from: dt.date | None,
    date_to: dt.date | None,
    rate_class_id: int | None,
) -> dict:
    """Состав выборки для шапки: сколько договоров, объектов и классов в отчёте."""
    row = db.execute(
        sa.select(
            sa.func.count(sa.distinct(Contract.id)).label("contracts"),
            sa.func.count(sa.distinct(Contract.object_id)).label("objects"),
            sa.func.count(sa.distinct(Contract.rate_class_id)).label("classes"),
        )
        .select_from(sa.join(latest, Contract, Contract.id == latest.c.contract_id))
        .where(
            *column_scope_filters(
                rate_class_id=rate_class_id,
                date_from=date_from,
                date_to=date_to,
                latest=latest,
            )
        )
    ).one()
    return {
        "date_from": iso(date_from),
        "date_to": iso(date_to),
        "contracts": row.contracts,
        "objects": row.objects,
        "classes": row.classes,
    }


# ---------------------------------------------------------------------------
#  Итоги
# ---------------------------------------------------------------------------

def _empty_report_totals() -> dict:
    """Итоги, когда сравнивать нечего.

    **Деньги здесь `None`, а не ноль** — найдено прогоном стенда. У класса, где ни у
    одной работы нет норматива, ноль в колонке «Отклонение, ₽» читается как «сошлось
    с нормативом», то есть утверждает прямо противоположное действительности. Это та
    же ошибка, которую §10 запрещает на уровне отдельной позиции, только в итоге —
    и в отчёте, который уходит в банк, она опаснее всего.

    `deviation_money` и `deviation_pct` обязаны быть пустыми **вместе**: разошлись бы
    они, и строка утверждала бы «отклонение 0 ₽ при неизвестном проценте».
    """
    return {
        "volume": None,
        "amount": None,
        "standard_amount": None,
        "deviation_money": None,
        "deviation_pct": None,
        "works": 0,
        "positions": 0,
        "positions_without_standard": 0,
    }


def _totals_of(rows: list[dict]) -> dict:
    """Итог по набору строк: деньги суммой, отклонение — по суммам.

    Отклонение НЕ усредняется из процентов строк (см. модульную документацию): здесь
    оно считается как `(факт − норматив) / норматив` по сравнимым суммам, то есть
    оказывается взвешенным по объёму автоматически.
    """
    if not rows:
        return _empty_report_totals()

    amount = sum((r["amount"] for r in rows if r["amount"] is not None), ZERO)
    comparable = sum((r["comparable_amount"] for r in rows if r["comparable_amount"] is not None), ZERO)
    standard = sum(
        (r["comparable_standard_amount"] for r in rows if r["comparable_standard_amount"] is not None),
        ZERO,
    )
    # Сравнимой базы нет — значит нет ни процента, ни рублей отклонения. Ноль в
    # рублях при пустом проценте означал бы «сошлось» (см. `_empty_report_totals`).
    comparable_exists = standard != 0
    return {
        "volume": None,  # объёмы работ в разных единицах — суммировать их нельзя
        "amount": amount,
        "standard_amount": standard if comparable_exists else None,
        "deviation_money": (comparable - standard) if comparable_exists else None,
        "deviation_pct": _deviation_pct(comparable, standard),
        "works": len(rows),
        "positions": sum(r["positions"] for r in rows),
        "positions_without_standard": sum(r["positions_without_standard"] for r in rows),
    }
