"""Приведение сравнения к ценовому уровню: разрешение дат, покрытие и отказы
(спека `2026-08-18-inflation-adjustment-design.md` §2.2, §2.5, §2.9; план, задача 7).

Числа — замеры гейта 3 при ряде `{2024: 1.075, 2025: 1.083, 2026: 1.060}` и цели
`2026-08`. Они же стоят в §2.5 спеки, и воспроизводятся здесь до последнего знака.

Что стерегут именно эти тесты, потому что дальше по конвейеру оно уже не наблюдаемо:

* **покрытие есть ОБЪЕДИНЕНИЕ требуемых годов**, а не диапазон `min…max`: диапазон
  потребовал бы год, который сокращается тождественно, и запретил бы полностью
  вычислимое приведение;
* **дата договора для ДС ЗАПРЕЩЕНА** — это дата базы, и коэффициент вышел бы молча
  неверным. Снятие этой защиты обязано ронять тест (DoD 8);
* **при дыре в покрытии номинальные числа не выдаются как приведённые** — отказ, а
  не подстановка.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from crud import comparison as cmp
from crud.common import DomainError
from money.inflation import YearMonth
from tests import comparison_fixtures as fx

pytestmark = pytest.mark.integration

SERIES_NAME = "Росстат, ИПЦ, декабрь к декабрю"
FULL_YEARS = {2024: "1.075", 2025: "1.083", 2026: "1.060"}
TARGET_AUG_2026 = YearMonth(2026, 8)

#: Коэффициенты замера §2.5 спеки — по одному на каждую дату стенда.
FACTOR_MAY_2024 = Decimal("1.174412425779300303760548277958037")
FACTOR_FEB_2025 = Decimal("1.111034702376096271381243623987202")
FACTOR_JUL_2026 = Decimal("1.004867550565343037541198945587506")

MONEY = {"1": ["1200000.00"]}


def _series(db, years=None, *, name=SERIES_NAME, note=None, forecast=()) -> int:
    return fx.series_with_years(
        db, name, years or FULL_YEARS, note=note, forecast=forecast
    )


# ---------------------------------------------------------------------------
#  Покрытие: объединение требуемых годов, а не диапазон
# ---------------------------------------------------------------------------

def test_coverage_is_the_union_of_required_years_not_a_range(db_session, factories):
    """Май 2024 и июль 2026 при цели авг 2026 требуют 2024, 2025 и 2026.

    Оба договора нужны в одной выборке: покрытие — свойство ВЫБОРКИ, и на одном
    договоре объединение не отличить от диапазона.
    """
    series_id = _series(db_session)
    early = fx.contract_with_dates(
        db_session, factories, MONEY, signed_date=dt.date(2024, 5, 28)
    )
    late = fx.contract_with_dates(
        db_session, factories, MONEY, signed_date=dt.date(2026, 7, 6)
    )

    plan = cmp.resolve_inflation(
        db_session, [early, late], series_id=series_id, target_month=TARGET_AUG_2026
    )

    assert [item["year"] for item in plan.used_years] == [2024, 2025, 2026]
    assert set(plan.factor_by_estimate.values()) == {FACTOR_MAY_2024, FACTOR_JUL_2026}


def test_december_target_needs_only_the_year_of_the_estimate(db_session, factories):
    """Тот же договор мая-2024 при цели дек-2024 требует ТОЛЬКО 2024 (DoD 4).

    Ряд отдаётся БЕЗ 2025 и 2026: если бы покрытие считалось диапазоном до цели
    или сплошняком, приведение отказало бы там, где оно вычислимо.
    """
    series_id = _series(db_session, {2024: "1.075"})
    contract_id = fx.contract_with_dates(
        db_session, factories, MONEY, signed_date=dt.date(2024, 5, 28)
    )

    plan = cmp.resolve_inflation(
        db_session, [contract_id], series_id=series_id, target_month=YearMonth(2024, 12)
    )

    assert [item["year"] for item in plan.used_years] == [2024]


def test_same_month_needs_no_series_rows_and_gives_exactly_one(db_session, factories):
    """Цель = месяц сметы: коэффициент ровно 1, использованных годов нет (DoD 5)."""
    series_id = _series(db_session, {})
    contract_id = fx.contract_with_dates(
        db_session, factories, MONEY, signed_date=dt.date(2026, 8, 15)
    )

    plan = cmp.resolve_inflation(
        db_session, [contract_id], series_id=series_id, target_month=TARGET_AUG_2026
    )

    assert plan.used_years == []
    assert set(plan.factor_by_estimate.values()) == {Decimal(1)}


def test_missing_year_refuses_with_the_list_and_yields_no_numbers(db_session, factories):
    """Дыра в покрытии — доменный отказ с перечнем, а НЕ номинальные числа (DoD 7).

    «Номинальные числа не выданы» проверяется тем, что функция БРОСАЕТ: план с
    коэффициентами-единицами выглядел бы успешным приведением, в котором один год
    не приведён вовсе, и отличить его от честного было бы нечем.
    """
    series_id = _series(db_session, {2025: "1.083"})
    early = fx.contract_with_dates(
        db_session, factories, MONEY, signed_date=dt.date(2024, 5, 28)
    )
    late = fx.contract_with_dates(
        db_session, factories, MONEY, signed_date=dt.date(2026, 7, 6)
    )

    with pytest.raises(DomainError) as exc:
        cmp.resolve_inflation(
            db_session, [early, late], series_id=series_id, target_month=TARGET_AUG_2026
        )

    assert exc.value.status_code == 422
    assert exc.value.code == "missing_inflation_years"
    assert exc.value.context == {"missing_years": [2024, 2026]}
    assert "2024" in exc.value.detail and "2026" in exc.value.detail


def test_unknown_series_is_404(db_session, factories):
    contract_id = fx.contract_with_dates(
        db_session, factories, MONEY, signed_date=dt.date(2026, 7, 6)
    )
    with pytest.raises(DomainError) as exc:
        cmp.resolve_inflation(
            db_session, [contract_id], series_id=10**9, target_month=TARGET_AUG_2026
        )
    assert exc.value.status_code == 404


def test_archived_series_by_explicit_id_still_computes(db_session, factories):
    """Архивный ряд по явному id считается: старая ссылка обязана работать (DoD 20)."""
    series_id = _series(db_session)
    fx.archive_series(db_session, series_id)
    contract_id = fx.contract_with_dates(
        db_session, factories, MONEY, signed_date=dt.date(2026, 7, 6)
    )

    plan = cmp.resolve_inflation(
        db_session, [contract_id], series_id=series_id, target_month=TARGET_AUG_2026
    )

    assert set(plan.factor_by_estimate.values()) == {FACTOR_JUL_2026}


# ---------------------------------------------------------------------------
#  Период сметы: дата ДС собственная, дата договора для него ЗАПРЕЩЕНА (DoD 8)
# ---------------------------------------------------------------------------

def test_base_estimate_falls_back_to_the_contract_date(db_session, factories):
    """У ДГП фолбэк на `signed_date` ВЕРЕН: дата исходной сметы и есть дата
    подписания договора (§2.2)."""
    series_id = _series(db_session)
    contract_id = fx.contract_with_dates(
        db_session, factories, MONEY,
        signed_date=dt.date(2025, 2, 20), prepared_on=None,
    )

    plan = cmp.resolve_inflation(
        db_session, [contract_id], series_id=series_id, target_month=TARGET_AUG_2026
    )

    assert set(plan.factor_by_estimate.values()) == {FACTOR_FEB_2025}


def test_base_estimate_prefers_its_own_date_over_the_contract_date(db_session, factories):
    """Своя дата сметы приоритетнее: `COALESCE` именно в этом порядке."""
    series_id = _series(db_session)
    contract_id = fx.contract_with_dates(
        db_session, factories, MONEY,
        signed_date=dt.date(2024, 5, 28), prepared_on=dt.date(2025, 2, 20),
    )

    plan = cmp.resolve_inflation(
        db_session, [contract_id], series_id=series_id, target_month=TARGET_AUG_2026
    )

    assert set(plan.factor_by_estimate.values()) == {FACTOR_FEB_2025}


def test_amendment_without_its_own_date_refuses(db_session, factories):
    """ДС без собственной даты — отказ, хотя год договора покрыт рядом ПОЛНОСТЬЮ.

    Год специально покрыт: иначе отказ мог бы прийти по нехватке коэффициента, и
    тест не отличил бы одну причину от другой.

    `estimate_ids` — машинный контекст; человеку он не показывается, и в `message`
    его нет. Человеческую формулировку собирает СЕРВЕР, потому что «ДС №1» без
    номера договора не опознаёт смету: он есть у каждого второго договора выборки.
    """
    series_id = _series(db_session)
    contract, _base, amendment = fx.contract_with_amendment_dates(
        db_session, factories,
        signed_date=dt.date(2025, 2, 20),
        base_prepared=dt.date(2025, 2, 20),
        amd_prepared=None,
        contract_number="ГП-ДС-1",
    )

    with pytest.raises(DomainError) as exc:
        cmp.resolve_inflation(
            db_session, [contract.id], series_id=series_id, target_month=TARGET_AUG_2026
        )

    assert exc.value.status_code == 422
    assert exc.value.code == "amendment_date_missing"
    assert exc.value.context == {"estimate_ids": [amendment.id]}
    assert "ГП-ДС-1" in exc.value.detail
    assert "1" in exc.value.detail
    assert str(amendment.id) not in exc.value.detail


def test_amendment_with_its_own_date_uses_it(db_session, factories):
    """Две сметы одного договора разных лет — РАЗНЫЕ множители (DoD 2)."""
    series_id = _series(db_session)
    contract, base, amendment = fx.contract_with_amendment_dates(
        db_session, factories,
        signed_date=dt.date(2024, 5, 28),
        base_prepared=dt.date(2024, 5, 28),
        amd_prepared=dt.date(2026, 7, 6),
    )

    plan = cmp.resolve_inflation(
        db_session, [contract.id], series_id=series_id, target_month=TARGET_AUG_2026
    )

    assert plan.factor_by_estimate[base.id] == FACTOR_MAY_2024
    assert plan.factor_by_estimate[amendment.id] == FACTOR_JUL_2026
    assert plan.factor_by_estimate[base.id] != plan.factor_by_estimate[amendment.id]


# ---------------------------------------------------------------------------
#  Обратное направление и метаданные
# ---------------------------------------------------------------------------

def test_target_earlier_than_the_estimate_gives_a_factor_below_one(db_session, factories):
    """Цель раньше сметы — коэффициент меньше единицы, отказа НЕТ (DoD 10)."""
    series_id = _series(db_session)
    contract_id = fx.contract_with_dates(
        db_session, factories, MONEY, signed_date=dt.date(2026, 7, 6)
    )

    plan = cmp.resolve_inflation(
        db_session, [contract_id], series_id=series_id, target_month=YearMonth(2024, 5)
    )

    factor = next(iter(plan.factor_by_estimate.values()))
    assert factor < 1
    assert factor == Decimal("0.8556342972091315960133676609758070")


def test_plan_carries_series_metadata_and_forecast_flag(db_session, factories):
    """`series_note` и `series_updated_at` приходят в ЭТОМ ответе (DoD 38, 14)."""
    series_id = _series(
        db_session, note="официальная публикация, по РФ", forecast=(2026,)
    )
    contract_id = fx.contract_with_dates(
        db_session, factories, MONEY, signed_date=dt.date(2026, 7, 6)
    )

    plan = cmp.resolve_inflation(
        db_session, [contract_id], series_id=series_id, target_month=TARGET_AUG_2026
    )

    assert plan.series_id == series_id
    assert plan.series_name == SERIES_NAME
    assert plan.series_note == "официальная публикация, по РФ"
    assert plan.series_updated_at is not None
    assert plan.target_month == "2026-08"
    # Использован только 2026-й, и он прогнозный.
    assert [item["year"] for item in plan.used_years] == [2026]
    assert plan.has_forecast is True
    assert plan.used_years[0]["is_forecast"] is True
    assert plan.used_years[0]["source"] == "бюллетень 01.2026"


def test_has_forecast_is_false_when_no_used_year_is_a_forecast(db_session, factories):
    """Прогнозный год, который НЕ используется, флага не поднимает.

    Иначе поверхность называла бы прогнозным приведение, посчитанное по фактам, —
    и подпись соврала бы в ту же сторону, что захардкоженное название ряда.
    """
    series_id = _series(db_session, forecast=(2024,))
    contract_id = fx.contract_with_dates(
        db_session, factories, MONEY, signed_date=dt.date(2026, 7, 6)
    )

    plan = cmp.resolve_inflation(
        db_session, [contract_id], series_id=series_id, target_month=TARGET_AUG_2026
    )

    assert [item["year"] for item in plan.used_years] == [2026]
    assert plan.has_forecast is False
