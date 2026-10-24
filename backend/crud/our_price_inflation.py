"""ИПЦ для «нашей цены»: целевой месяц этапа и план приведения истории
(спека 4а §2.10, `AGENTS.md` §10).

Только чтение. Приводятся ПОСЛЕДНИЕ сметы договоров истории на месяц этапа.
Планировщик — копия `crud.comparison.resolve_inflation` под историю на том же
ядре `money/inflation.py` и с теми же отказами; добавлен один: у этапа нет
целевого месяца.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.common import DomainError, iso
from crud.comparison import InflationPlan, _amendment_label, _estimate_period
from models import (
    Contract,
    Estimate,
    InflationIndexValue,
    InflationSeries,
    Offer,
    TenderRound,
)
from money.inflation import YearMonth, coefficient, required_years, year_exponents

CODE_TARGET_MISSING = "inflation_target_missing"


def round_target_month(db: Session, round_id: int) -> YearMonth | None:
    """Месяц этапа: `held_on`; без него — месяц самой поздней даты подготовки
    среди смет КП этого этапа; без обеих — `None`."""
    held_on, latest_prepared = db.execute(
        sa.select(
            TenderRound.held_on,
            sa.select(sa.func.max(Estimate.data_prepared_on_date))
            .join(Offer, Offer.id == Estimate.offer_id)
            .where(Offer.round_id == TenderRound.id)
            .scalar_subquery(),
        ).where(TenderRound.id == round_id)
    ).one_or_none() or (None, None)
    chosen = held_on or latest_prepared
    return None if chosen is None else YearMonth(chosen.year, chosen.month)


def plan_history_inflation(
    db: Session,
    history_estimate_ids: Sequence[int],
    *,
    series_id: int,
    target_month: YearMonth | None,
) -> InflationPlan:
    """Периоды смет истории, покрытие и коэффициент на смету.

    Ряд читается ОДНИМ оператором вместе со своими годами (`LEFT JOIN`): в
    `READ COMMITTED` два `SELECT` могли бы увидеть разные редакции ряда, и имя с
    примечанием легли бы рядом с чужими коэффициентами. Сметы — вторым
    оператором; число операторов не зависит от числа смет. `db.get` не
    используется: из карты идентичности он отдаёт объект без запроса, и бюджет
    зависел бы от прогретости сессии.

    Читаются только сметы, названные на входе: исходная смета договора с ДС,
    не попавшая в историю, не читается и отказа не даёт.

    Raises:
        DomainError 404: ряда нет (архивный ряд по явному `id` читается).
        DomainError 422 `inflation_target_missing`: цели приведения нет.
        DomainError 422 `amendment_date_missing`: у ДС нет собственной даты.
        DomainError 422 `missing_inflation_years`: в ряду нет нужных лет.
    """
    series_rows = db.execute(
        sa.select(
            InflationSeries.name,
            InflationSeries.note,
            InflationSeries.updated_at,
            InflationIndexValue.year,
            InflationIndexValue.coefficient,
            InflationIndexValue.source,
            InflationIndexValue.is_forecast,
            InflationIndexValue.updated_at.label("year_updated_at"),
        )
        .join(
            InflationIndexValue,
            InflationIndexValue.series_id == InflationSeries.id,
            isouter=True,
        )
        .where(InflationSeries.id == series_id)
    ).all()
    if not series_rows:
        raise DomainError(404, f"Ряд индексов {series_id} не найден.")

    if target_month is None:
        raise DomainError(
            422,
            "У этапа нет даты проведения, и у КП нет даты подготовки — приведение по ИПЦ не к чему привязать.",
            code=CODE_TARGET_MISSING,
            context={},
        )

    series_name = series_rows[0].name
    series_note = series_rows[0].note
    series_updated_at = series_rows[0].updated_at
    values = {row.year: row for row in series_rows}

    rows = db.execute(
        sa.select(
            Estimate.id,
            Estimate.amendment_no,
            Estimate.data_prepared_on_date,
            Contract.signed_date,
            Contract.contract_number,
        )
        .join(Contract, Contract.id == Estimate.contract_id)
        .where(Estimate.id.in_(list(history_estimate_ids)))
        .order_by(Contract.contract_number, Estimate.amendment_no.nulls_first(), Estimate.id)
    ).all()

    period_by_estimate: dict[int, YearMonth] = {}
    undated: list[tuple[int, str]] = []
    for estimate_id, amendment_no, prepared_on, signed_date, contract_number in rows:
        period = _estimate_period(amendment_no, prepared_on, signed_date)
        if period is None:
            undated.append((estimate_id, _amendment_label(contract_number, amendment_no)))
            continue
        period_by_estimate[estimate_id] = period

    if undated:
        listed = ", ".join(label for _estimate_id, label in undated)
        raise DomainError(
            422,
            f"У допсоглашений нет собственной даты подготовки: {listed}. "
            "Дата договора для допсоглашения не подходит — это дата базовой сметы.",
            code="amendment_date_missing",
            context={"estimate_ids": sorted(estimate_id for estimate_id, _label in undated)},
        )

    required: set[int] = set()
    for period in period_by_estimate.values():
        required.update(required_years(period, target_month))

    missing = sorted(year for year in required if year not in values)
    if missing:
        listed = ", ".join(str(year) for year in missing)
        raise DomainError(
            422,
            f"Не заданы коэффициенты за годы: {listed}.",
            code="missing_inflation_years",
            context={"missing_years": missing},
        )

    coefficients = {year: values[year].coefficient for year in required}
    factor_by_estimate = {
        estimate_id: coefficient(year_exponents(period, target_month), coefficients)
        for estimate_id, period in period_by_estimate.items()
    }

    used_years = [
        {
            "year": year,
            "coefficient": values[year].coefficient,
            "source": values[year].source,
            "is_forecast": values[year].is_forecast,
            "updated_at": iso(values[year].year_updated_at),
        }
        for year in sorted(required)
    ]

    return InflationPlan(
        series_id=series_id,
        series_name=series_name,
        series_note=series_note,
        series_updated_at=iso(series_updated_at),
        target_month=f"{target_month.year:04d}-{target_month.month:02d}",
        has_forecast=any(values[year].is_forecast for year in required),
        used_years=used_years,
        factor_by_estimate=factor_by_estimate,
    )
