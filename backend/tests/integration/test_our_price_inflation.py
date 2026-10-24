"""ИПЦ «нашей цены»: целевой месяц этапа и план приведения истории
(спека 4а §2.10).

Числа — замеры спеки приведения при ряде `{2024: 1.075, 2025: 1.083, 2026: 1.060}`
и цели `2026-08`; те же литералы стоят в тестах приведения сравнения.
"""

from __future__ import annotations

import contextlib
import datetime as dt
from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud import comparison as cmp
from crud.common import DomainError
from crud.our_price_inflation import (
    CODE_TARGET_MISSING,
    plan_history_inflation,
    round_target_month,
)
from money.inflation import YearMonth
from tests import comparison_fixtures as fx

pytestmark = pytest.mark.integration

SERIES_NAME = "Росстат, ИПЦ, декабрь к декабрю"
FULL_YEARS = {2024: "1.075", 2025: "1.083", 2026: "1.060"}
TARGET = YearMonth(2026, 8)

FACTOR_MAY_2024 = Decimal("1.174412425779300303760548277958037")
FACTOR_FEB_2025 = Decimal("1.111034702376096271381243623987202")
FACTOR_JUL_2026 = Decimal("1.004867550565343037541198945587506")

TARGET_MISSING_TEXT = "У этапа нет даты проведения, и у КП нет даты подготовки — приведение по ИПЦ не к чему привязать."


def _series(db, years=None, *, forecast=(), note=None) -> int:
    return fx.series_with_years(
        db,
        SERIES_NAME,
        FULL_YEARS if years is None else years,
        forecast=forecast,
        note=note,
    )


def _estimate(db, factories, *, signed, prepared=None, amendment_no=None, number=None):
    """Договор с ОДНОЙ сметой; возвращает `(contract, estimate)`."""
    contract = factories.ContractFactory.create(
        signed_date=signed,
        **({"contract_number": number} if number else {}),
    )
    estimate = factories.EstimateFactory.create(
        contract=contract, amendment_no=amendment_no, data_prepared_on_date=prepared
    )
    db.flush()
    return contract, estimate


def _round_estimate(db, factories, rnd, prepared):
    offer = factories.OfferFactory.create(round=rnd)
    estimate = factories.EstimateFactory.create(contract=None, offer=offer, data_prepared_on_date=prepared)
    db.flush()
    return estimate


def _refusal(call) -> DomainError:
    with pytest.raises(DomainError) as exc:
        call()
    return exc.value


@contextlib.contextmanager
def _count_queries(session):
    box = {"total": 0}
    connection = session.connection()

    def on_execute(conn, cursor, statement, parameters, context, executemany):
        box["total"] += 1

    sa.event.listen(connection, "after_cursor_execute", on_execute)
    try:
        yield box
    finally:
        sa.event.remove(connection, "after_cursor_execute", on_execute)


# ---------------------------------------------------------------------------
#  round_target_month
# ---------------------------------------------------------------------------


def test_held_on_wins_over_later_estimate_dates(db_session, factories):
    rnd = factories.TenderRoundFactory.create(held_on=dt.date(2026, 3, 31))
    _round_estimate(db_session, factories, rnd, dt.date(2026, 9, 10))

    assert round_target_month(db_session, rnd.id) == YearMonth(2026, 3)


def test_without_held_on_the_latest_prepared_date_wins(db_session, factories):
    """Ни первая, ни последняя по id смета не самая поздняя: берётся максимум
    дат, а не смета по порядку; смета без даты не мешает."""
    rnd = factories.TenderRoundFactory.create(held_on=None)
    _round_estimate(db_session, factories, rnd, dt.date(2026, 5, 2))
    _round_estimate(db_session, factories, rnd, dt.date(2026, 9, 10))
    _round_estimate(db_session, factories, rnd, None)
    _round_estimate(db_session, factories, rnd, dt.date(2026, 7, 15))

    assert round_target_month(db_session, rnd.id) == YearMonth(2026, 9)


def test_estimates_of_another_round_are_not_counted(db_session, factories):
    rnd = factories.TenderRoundFactory.create(held_on=None)
    other = factories.TenderRoundFactory.create(held_on=None)
    _round_estimate(db_session, factories, rnd, dt.date(2026, 5, 2))
    _round_estimate(db_session, factories, other, dt.date(2026, 11, 20))

    assert round_target_month(db_session, rnd.id) == YearMonth(2026, 5)


def test_no_held_on_and_no_prepared_dates_gives_none(db_session, factories):
    rnd = factories.TenderRoundFactory.create(held_on=None)
    _round_estimate(db_session, factories, rnd, None)
    empty = factories.TenderRoundFactory.create(held_on=None)

    assert round_target_month(db_session, rnd.id) is None
    assert round_target_month(db_session, empty.id) is None


def test_unknown_round_gives_none_without_raising(db_session):
    """Существование этапа проверяет вызывающий; здесь — ни даты, ни отказа."""
    assert round_target_month(db_session, 987654321) is None


def test_baseline_estimate_of_the_round_is_not_an_offer_estimate(db_session, factories):
    """Смета-baseline владеет раундом напрямую и КП этапа не является."""
    rnd = factories.TenderRoundFactory.create(held_on=None)
    factories.EstimateFactory.create(contract=None, round=rnd, data_prepared_on_date=dt.date(2026, 11, 1))
    db_session.flush()

    assert round_target_month(db_session, rnd.id) is None


# ---------------------------------------------------------------------------
#  Отказы и их порядок
# ---------------------------------------------------------------------------


def test_missing_series_is_404(db_session, factories):
    _contract, estimate = _estimate(db_session, factories, signed=dt.date(2025, 2, 20))

    exc = _refusal(lambda: plan_history_inflation(db_session, [estimate.id], series_id=987654321, target_month=TARGET))

    assert exc.status_code == 404
    assert exc.code is None


def test_target_missing_refuses_with_code_and_text(db_session, factories):
    series_id = _series(db_session)
    _contract, estimate = _estimate(db_session, factories, signed=dt.date(2025, 2, 20))

    exc = _refusal(lambda: plan_history_inflation(db_session, [estimate.id], series_id=series_id, target_month=None))

    assert CODE_TARGET_MISSING == "inflation_target_missing"
    assert exc.status_code == 422
    assert exc.code == CODE_TARGET_MISSING
    assert exc.detail == TARGET_MISSING_TEXT
    assert exc.context == {}


def test_missing_series_beats_missing_target(db_session, factories):
    _contract, estimate = _estimate(db_session, factories, signed=dt.date(2025, 2, 20))

    exc = _refusal(lambda: plan_history_inflation(db_session, [estimate.id], series_id=987654321, target_month=None))

    assert exc.status_code == 404


def test_missing_target_beats_undated_amendment(db_session, factories):
    series_id = _series(db_session)
    _contract, amendment = _estimate(db_session, factories, signed=dt.date(2025, 2, 20), amendment_no=1)

    exc = _refusal(lambda: plan_history_inflation(db_session, [amendment.id], series_id=series_id, target_month=None))

    assert exc.code == CODE_TARGET_MISSING


def test_undated_amendment_refuses_even_when_target_is_present(db_session, factories):
    """Сосед предыдущего: цель есть и ряд полон — отказывает дата ДС."""
    series_id = _series(db_session)
    _contract, amendment = _estimate(db_session, factories, signed=dt.date(2025, 2, 20), amendment_no=1)

    exc = _refusal(lambda: plan_history_inflation(db_session, [amendment.id], series_id=series_id, target_month=TARGET))

    assert exc.status_code == 422
    assert exc.code == "amendment_date_missing"


def test_undated_amendment_beats_a_gap_in_the_series(db_session, factories):
    """Ряд пуст (дыра есть) и ДС без даты: верхний код — про дату ДС."""
    series_id = _series(db_session, {})
    _c1, base = _estimate(db_session, factories, signed=dt.date(2024, 5, 28))
    _c2, amendment = _estimate(db_session, factories, signed=dt.date(2025, 2, 20), amendment_no=1)

    exc = _refusal(
        lambda: plan_history_inflation(db_session, [base.id, amendment.id], series_id=series_id, target_month=TARGET)
    )

    assert exc.code == "amendment_date_missing"


def test_gap_in_the_series_refuses_with_sorted_years(db_session, factories):
    """Годы нужны объединением по ВСЕМ сметам: смета с самым широким набором
    лет стоит в середине порядка чтения (по номеру договора), а не первой и не
    последней. Недостающие 2023 и 2026 — по возрастанию, хотя в множестве
    целых 2026 обходится раньше 2023."""
    series_id = _series(db_session, {2024: "1.075", 2025: "1.083"})
    _c1, late = _estimate(db_session, factories, signed=dt.date(2026, 7, 6), number="ГП-А")
    _c2, early = _estimate(db_session, factories, signed=dt.date(2023, 11, 15), number="ГП-Б")
    _c3, middle = _estimate(db_session, factories, signed=dt.date(2025, 2, 20), number="ГП-В")

    exc = _refusal(
        lambda: plan_history_inflation(
            db_session, [middle.id, late.id, early.id], series_id=series_id, target_month=TARGET
        )
    )

    assert exc.status_code == 422
    assert exc.code == "missing_inflation_years"
    assert exc.context == {"missing_years": [2023, 2026]}


def test_full_series_does_not_refuse(db_session, factories):
    """Парный вход к дыре: ряд полон — отказа нет."""
    series_id = _series(db_session)
    _c1, early = _estimate(db_session, factories, signed=dt.date(2024, 5, 28))

    plan = plan_history_inflation(db_session, [early.id], series_id=series_id, target_month=TARGET)

    assert plan.factor_by_estimate == {early.id: FACTOR_MAY_2024}


def test_undated_amendments_are_listed_ascending_by_estimate_id(db_session, factories):
    """Порядок номеров договоров обратен порядку id: сортировка — по id."""
    series_id = _series(db_session)
    _c1, first = _estimate(db_session, factories, signed=dt.date(2025, 2, 20), amendment_no=1, number="ГП-Я")
    _c2, second = _estimate(db_session, factories, signed=dt.date(2025, 2, 20), amendment_no=2, number="ГП-А")

    exc = _refusal(
        lambda: plan_history_inflation(db_session, [second.id, first.id], series_id=series_id, target_month=TARGET)
    )

    assert first.id < second.id
    assert exc.context == {"estimate_ids": [first.id, second.id]}
    assert "ГП-Я ДС №1" in exc.detail
    assert "ГП-А ДС №2" in exc.detail


def test_amendment_not_in_history_without_a_date_does_not_refuse(db_session, factories):
    """ДС не последняя: в историю идёт только поздняя смета, ДС без даты не читается."""
    series_id = _series(db_session)
    contract = factories.ContractFactory.create(signed_date=dt.date(2025, 2, 20))
    factories.EstimateFactory.create(contract=contract, amendment_no=None, data_prepared_on_date=dt.date(2025, 2, 20))
    undated = factories.EstimateFactory.create(contract=contract, amendment_no=1, data_prepared_on_date=None)
    latest = factories.EstimateFactory.create(
        contract=contract, amendment_no=2, data_prepared_on_date=dt.date(2026, 7, 6)
    )
    db_session.flush()

    plan = plan_history_inflation(db_session, [latest.id], series_id=series_id, target_month=TARGET)

    assert plan.factor_by_estimate == {latest.id: FACTOR_JUL_2026}

    exc = _refusal(
        lambda: plan_history_inflation(db_session, [latest.id, undated.id], series_id=series_id, target_month=TARGET)
    )
    assert exc.code == "amendment_date_missing"
    assert exc.context == {"estimate_ids": [undated.id]}


def test_undated_contract_date_is_not_substituted_for_amendment(db_session, factories):
    """Договор не выкидывается и дата договора не подставляется: отказ."""
    series_id = _series(db_session)
    _contract, amendment = _estimate(db_session, factories, signed=dt.date(2026, 7, 6), amendment_no=1)
    _c2, base = _estimate(db_session, factories, signed=dt.date(2024, 5, 28))

    exc = _refusal(
        lambda: plan_history_inflation(db_session, [base.id, amendment.id], series_id=series_id, target_month=TARGET)
    )

    assert exc.code == "amendment_date_missing"
    assert exc.context == {"estimate_ids": [amendment.id]}


# ---------------------------------------------------------------------------
#  Коэффициенты и метаданные
# ---------------------------------------------------------------------------


def test_factor_per_estimate_period(db_session, factories):
    series_id = _series(db_session)
    _c1, base = _estimate(db_session, factories, signed=dt.date(2024, 5, 28), prepared=dt.date(2024, 5, 28))
    _c2, amendment = _estimate(
        db_session,
        factories,
        signed=dt.date(2024, 5, 28),
        amendment_no=1,
        prepared=dt.date(2026, 7, 6),
    )

    plan = plan_history_inflation(db_session, [base.id, amendment.id], series_id=series_id, target_month=TARGET)

    assert plan.factor_by_estimate == {
        base.id: FACTOR_MAY_2024,
        amendment.id: FACTOR_JUL_2026,
    }
    assert plan.target_month == "2026-08"
    assert [item["year"] for item in plan.used_years] == [2024, 2025, 2026]


def test_base_estimate_without_prepared_date_uses_signed_date(db_session, factories):
    series_id = _series(db_session)
    _c1, undated_base = _estimate(db_session, factories, signed=dt.date(2025, 2, 20))
    _c2, dated_base = _estimate(db_session, factories, signed=dt.date(2024, 1, 10), prepared=dt.date(2025, 2, 3))

    plan = plan_history_inflation(
        db_session,
        [undated_base.id, dated_base.id],
        series_id=series_id,
        target_month=TARGET,
    )

    assert plan.factor_by_estimate == {
        undated_base.id: FACTOR_FEB_2025,
        dated_base.id: FACTOR_FEB_2025,
    }


def test_empty_history_gives_an_empty_plan(db_session, factories):
    series_id = _series(db_session, {}, note="примечание")

    plan = plan_history_inflation(db_session, [], series_id=series_id, target_month=TARGET)

    assert plan.factor_by_estimate == {}
    assert plan.used_years == []
    assert plan.has_forecast is False
    assert plan.series_name == SERIES_NAME
    assert plan.series_note == "примечание"


def test_forecast_flag_follows_used_years_only(db_session, factories):
    series_id = _series(db_session, forecast=(2026,))
    _c1, early = _estimate(db_session, factories, signed=dt.date(2024, 12, 5))

    # Цель 2025-01 требует 2025; прогнозный 2026 не нужен.
    unused = plan_history_inflation(db_session, [early.id], series_id=series_id, target_month=YearMonth(2025, 1))
    used = plan_history_inflation(db_session, [early.id], series_id=series_id, target_month=TARGET)

    assert unused.has_forecast is False
    assert used.has_forecast is True


def test_plan_equals_comparison_plan_on_the_same_contracts(db_session, factories):
    """2023 в выборке: в множестве целых он обходится после 2026, так что
    порядок `used_years` проверяется, а не совпадает случайно."""
    series_id = _series(db_session, {2023: "1.074", **FULL_YEARS}, forecast=(2026,), note="заметка")
    c1, e1 = _estimate(db_session, factories, signed=dt.date(2024, 5, 28))
    c2, e2 = _estimate(db_session, factories, signed=dt.date(2025, 2, 20), prepared=dt.date(2026, 7, 6))
    c3, e3 = _estimate(db_session, factories, signed=dt.date(2023, 11, 15))

    ours = plan_history_inflation(db_session, [e1.id, e2.id, e3.id], series_id=series_id, target_month=TARGET)
    theirs = cmp.resolve_inflation(db_session, [c1.id, c2.id, c3.id], series_id=series_id, target_month=TARGET)

    assert ours == theirs
    assert ours.series_name == SERIES_NAME
    assert ours.has_forecast is True
    assert [item["year"] for item in ours.used_years] == [2023, 2024, 2025, 2026]


def test_amendment_refusal_matches_comparison_refusal(db_session, factories):
    """Два ДС, порядок номеров договоров обратен порядку id: текст перечисляет
    их в том же порядке, что у сравнения."""
    series_id = _series(db_session)
    c1, a1 = _estimate(db_session, factories, signed=dt.date(2025, 2, 20), amendment_no=1, number="ГП-Т9")
    c2, a2 = _estimate(db_session, factories, signed=dt.date(2025, 2, 20), amendment_no=1, number="ГП-Т1")

    ours = _refusal(
        lambda: plan_history_inflation(db_session, [a1.id, a2.id], series_id=series_id, target_month=TARGET)
    )
    theirs = _refusal(
        lambda: cmp.resolve_inflation(db_session, [c1.id, c2.id], series_id=series_id, target_month=TARGET)
    )

    assert (ours.status_code, ours.code, ours.detail, ours.context) == (
        theirs.status_code,
        theirs.code,
        theirs.detail,
        theirs.context,
    )
    assert ours.context == {"estimate_ids": [a1.id, a2.id]}
    assert ours.detail.index("ГП-Т1 ДС №1") < ours.detail.index("ГП-Т9 ДС №1")


def test_gap_refusal_matches_comparison_refusal(db_session, factories):
    series_id = _series(db_session, {2025: "1.083"})
    contract, estimate = _estimate(db_session, factories, signed=dt.date(2024, 5, 28))

    ours = _refusal(lambda: plan_history_inflation(db_session, [estimate.id], series_id=series_id, target_month=TARGET))
    theirs = _refusal(
        lambda: cmp.resolve_inflation(db_session, [contract.id], series_id=series_id, target_month=TARGET)
    )

    assert (ours.status_code, ours.code, ours.detail, ours.context) == (
        theirs.status_code,
        theirs.code,
        theirs.detail,
        theirs.context,
    )
    assert ours.context == {"missing_years": [2024, 2026]}


def test_missing_series_refusal_matches_comparison_refusal(db_session, factories):
    contract, estimate = _estimate(db_session, factories, signed=dt.date(2025, 2, 20))

    ours = _refusal(lambda: plan_history_inflation(db_session, [estimate.id], series_id=987654321, target_month=TARGET))
    theirs = _refusal(
        lambda: cmp.resolve_inflation(db_session, [contract.id], series_id=987654321, target_month=TARGET)
    )

    assert (ours.status_code, ours.code, ours.detail) == (theirs.status_code, theirs.code, theirs.detail)


# ---------------------------------------------------------------------------
#  Бюджет запросов
# ---------------------------------------------------------------------------


def test_query_count_is_two_for_two_and_for_twenty_estimates(db_session, factories):
    series_id = _series(db_session)
    ids = [_estimate(db_session, factories, signed=dt.date(2025, 2, 20))[1].id for _ in range(20)]
    db_session.expire_all()

    with _count_queries(db_session) as few:
        plan_history_inflation(db_session, ids[:2], series_id=series_id, target_month=TARGET)
    with _count_queries(db_session) as many:
        plan = plan_history_inflation(db_session, ids, series_id=series_id, target_month=TARGET)

    assert len(plan.factor_by_estimate) == 20
    assert few["total"] == 2
    assert many["total"] == 2
