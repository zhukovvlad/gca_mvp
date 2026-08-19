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

import contextlib
import datetime as dt
from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud import comparison as cmp
from crud.common import DomainError
from money.inflation import YearMonth, adjust_amount
from money.vat import quantize_money
from tests import comparison_fixtures as fx

pytestmark = pytest.mark.integration

SERIES_NAME = "Росстат, ИПЦ, декабрь к декабрю"
OTHER_NAME = "Внутренняя оценка ПЭО"
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


class _QueryCounter:
    def __init__(self):
        self.total = 0


@contextlib.contextmanager
def _count_queries(session):
    """Считает запросы соединения сессии. Listener СНИМАЕТСЯ в finally — тот же
    приём, что в `test_comparison_buckets.py` и `test_comparison_rollup.py`."""
    counter = _QueryCounter()
    connection = session.connection()

    def on_execute(conn, cursor, statement, parameters, context, executemany):
        counter.total += 1

    sa.event.listen(connection, "after_cursor_execute", on_execute)
    try:
        yield counter
    finally:
        sa.event.remove(connection, "after_cursor_execute", on_execute)


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


# ---------------------------------------------------------------------------
#  Приведение чисел, метаданные и подпись оси (план, задача 8)
# ---------------------------------------------------------------------------

#: Нетто в каждом из трёх мест — ровно 1 000 000 при ставке 20 %.
GROSS_PER_PLACE = "1200000.00"
NET_PER_PLACE = Decimal("1000000")
AREA = "100000"

#: Медиана трёх колонок по ₽/м² до приведения: 1 000 000 / 100 000 м².
NET_PER_SQM = Decimal("10")


def _selection_of_three(db, factories) -> list[int]:
    """Три договора датами стенда, у каждого деньги В ТРЁХ МЕСТАХ сразу.

    Три места обязательны: единственная точка умножения проверялась бы одной
    ветвью данных, а приведённое дерево и НЕприведённый остаток молча сложились бы
    в «Итого по договору» (comparison.py складывает их вместе).
    """
    return [
        fx.contract_with_money_everywhere(
            db, factories, signed_date=signed_date,
            gross=GROSS_PER_PLACE, area_aboveground="50000", area_underground="50000",
        )
        for signed_date in (
            dt.date(2024, 5, 28), dt.date(2025, 2, 20), dt.date(2026, 7, 6)
        )
    ]


def _row(data: dict, code: str) -> dict:
    for row in data["rows"]:
        if row["code"] == code:
            return row
    raise AssertionError(f"строки {code!r} нет в ответе; есть {[r['code'] for r in data['rows']]}")


def _cell(data: dict, code: str, contract_id: int, bucket: str = None) -> dict:
    bucket = bucket or cmp.BUCKET_TOTAL
    for cell in _row(data, code)["cells"]:
        if cell["contract_id"] == contract_id:
            return cell[bucket]
    raise AssertionError(f"колонки {contract_id} нет в строке {code!r}")


def test_column_coefficients_are_the_measured_ones(db_session, factories):
    """Множители колонок — замеры §2.5 спеки (DoD 2, 33)."""
    series_id = _series(db_session)
    ids = _selection_of_three(db_session, factories)

    data = cmp.build_comparison(
        db_session, ids, vat_mode=cmp.VAT_MODE_OWN,
        inflation_series_id=series_id, target_month=TARGET_AUG_2026,
    )

    by_number = {
        column["contract_id"]: column["inflation_coefficient"] for column in data["columns"]
    }
    assert sorted(by_number.values()) == sorted(
        [FACTOR_MAY_2024, FACTOR_FEB_2025, FACTOR_JUL_2026]
    )
    # Ни у одной колонки коэффициенты смет не расходятся — разбивки быть не должно.
    assert all("inflation_factors" not in column for column in data["columns"])


def test_median_and_deviation_are_computed_from_adjusted_values(db_session, factories):
    """Медиана и отклонения посчитаны ОТ ПРИВЕДЁННЫХ значений (DoD 12).

    Сдвиг отклонения — цель фичи, а не побочный эффект: до приведения все три
    колонки лежат на 10 ₽/м² и отклонение нулевое, после — самая ранняя уходит на
    +5,7 %, потому что её рубли 2024 года приведены к августу 2026.
    """
    series_id = _series(db_session)
    ids = _selection_of_three(db_session, factories)
    earliest = ids[0]

    nominal = cmp.build_comparison(db_session, ids, vat_mode=cmp.VAT_MODE_OWN)
    assert _row(nominal, "1.1")["medians"][cmp.BUCKET_TOTAL]["value"] == NET_PER_SQM
    assert _cell(nominal, "1.1", earliest)["deviation_pct"] == 0

    adjusted = cmp.build_comparison(
        db_session, ids, vat_mode=cmp.VAT_MODE_OWN,
        inflation_series_id=series_id, target_month=TARGET_AUG_2026,
    )

    # Медиана — средняя из ТРЁХ приведённых ₽/м², то есть множитель февраля-2025.
    # Сравнение по копейкам: величина прошла умножение и деление на площадь, и
    # внутренняя точность путей агрегата разная (см. тест трёх мест).
    assert quantize_money(
        _row(adjusted, "1.1")["medians"][cmp.BUCKET_TOTAL]["value"]
    ) == quantize_money(adjust_amount(NET_PER_SQM, FACTOR_FEB_2025))
    deviation = _cell(adjusted, "1.1", earliest)["deviation_pct"]
    assert deviation > 5
    assert deviation < 6
    assert str(deviation).startswith("5.70")


def test_every_place_of_money_is_adjusted_by_the_same_factor(db_session, factories):
    """Поддерево, «Без подстатьи» и «Нераспределённое» приведены ОДИНАКОВО (DoD 2).

    «Итого по договору» обязано равняться сумме приведённых частей: если остаток
    останется номинальным, он молча сложится с приведённым деревом, и итог соврёт
    ровно на разницу.

    **Сравнение — по деньгам (`quantize_money`), а не по неокруглённым `Decimal`.**
    Замер 2026-08-19: пути агрегата несут РАЗНУЮ внутреннюю точность —
    `build_tree` складывает поддерево под амбиентным контекстом (28 значащих
    цифр), а `own_net` и остаток отдают произведение как есть (34). Разница лежит
    в 22-м знаке после запятой, то есть заведомо ниже копейки, и §2.5 спеки
    прямо разрешает её: округление — только на слое показа, а внутреннее
    приближение бизнес-округлением не является. Требовать здесь равенства
    неокруглённых значило бы закреплять тестом внутреннюю точность, которой
    агрегат не обещает (тот же довод, которым DoD 6 не требует его от
    телескопирования).

    Ожидание считается `adjust_amount`, а НЕ выражением `FACTOR * NET`: второе
    посчиталось бы амбиентным контекстом самого теста и совпало бы с одним путём
    агрегата случайно, а с другим — разошлось. Предпосылка теста не имеет права
    зависеть от контекста, в котором тест запущен.
    """
    series_id = _series(db_session)
    ids = _selection_of_three(db_session, factories)
    earliest = ids[0]

    data = cmp.build_comparison(
        db_session, ids, vat_mode=cmp.VAT_MODE_OWN,
        inflation_series_id=series_id, target_month=TARGET_AUG_2026,
    )
    expected = quantize_money(adjust_amount(NET_PER_PLACE, FACTOR_MAY_2024))

    for code in ("1.1", "1::own", "::unallocated"):
        assert quantize_money(_cell(data, code, earliest)["net"]) == expected, code

    # Суммы приводятся ЦЕЛИКОМ, а не умножением округлённой части: `Σ round(x) ≠
    # round(Σ x)` — то самое неравенство, из-за которого `quantize_money` живёт на
    # границе ответа, а не внутри агрегации (`money/vat.py`). Здесь оно наблюдаемо
    # прямо: 1 174 412,43 × 2 = 2 348 824,86, а округление суммы даёт 2 348 824,85.
    assert quantize_money(_cell(data, "1", earliest)["net"]) == quantize_money(
        adjust_amount(NET_PER_PLACE * 2, FACTOR_MAY_2024)
    )

    totals = next(item for item in data["totals"] if item["contract_id"] == earliest)
    assert quantize_money(totals[cmp.BUCKET_TOTAL]["net"]) == quantize_money(
        adjust_amount(NET_PER_PLACE * 3, FACTOR_MAY_2024)
    )


def test_buckets_are_adjusted_separately_not_as_one_sum(db_session, factories):
    """ДГП дек-2024 и ДС дек-2025 при цели дек-2025: корзины приведены ПОРОЗНЬ.

    «Итого» — сумма раздельно приведённых, а НЕ (ДГП + ДС) × общий множитель. Это
    и есть «коэффициент на смету, а не на договор» (§2.2), и на стенде случай не
    воспроизводится — допсоглашений там ноль.
    """
    series_id = _series(db_session)
    contract, _base, _amd = fx.contract_with_amendment_dates(
        db_session, factories,
        signed_date=dt.date(2024, 12, 10),
        base_prepared=dt.date(2024, 12, 10),
        amd_prepared=dt.date(2025, 12, 20),
        base="1200000.00", amd="600000.00",
    )

    data = cmp.build_comparison(
        db_session, [contract.id], vat_mode=cmp.VAT_MODE_OWN,
        inflation_series_id=series_id, target_month=YearMonth(2025, 12),
    )

    base_net = _cell(data, "1", contract.id, cmp.BUCKET_BASE)["net"]
    amd_net = _cell(data, "1", contract.id, cmp.BUCKET_AMENDMENTS)["net"]
    total_net = _cell(data, "1", contract.id, cmp.BUCKET_TOTAL)["net"]

    assert base_net == Decimal("1000000") * Decimal("1.083")
    assert amd_net == Decimal("500000")
    assert total_net == base_net + amd_net
    # Приведение «на договор» дало бы 1 500 000 × 1.083 — это НЕ то же число.
    assert total_net != Decimal("1500000") * Decimal("1.083")


def test_column_with_estimates_of_different_years_shows_the_breakdown(db_session, factories):
    """Коэффициенты смет расходятся -> чип «разные» плюс разбивка (DoD 33).

    Разбивка обязана уезжать в ответе: чип без неё отправлял бы читателя смотреть
    корзины, а корзины множителей не показывают, — арифметика колонки перестала бы
    быть проверяемой.
    """
    series_id = _series(db_session)
    contract, _base, _amd = fx.contract_with_amendment_dates(
        db_session, factories,
        signed_date=dt.date(2024, 12, 10),
        base_prepared=dt.date(2024, 12, 10),
        amd_prepared=dt.date(2025, 12, 20),
    )

    data = cmp.build_comparison(
        db_session, [contract.id], vat_mode=cmp.VAT_MODE_OWN,
        inflation_series_id=series_id, target_month=YearMonth(2025, 12),
    )

    column = data["columns"][0]
    assert column["inflation_coefficient"] is None
    assert column["inflation_factors"] == [
        {"label": "ДГП", "coefficient": Decimal("1.083")},
        {"label": "ДС №1", "coefficient": Decimal(1)},
    ]


def test_inflation_metadata_travels_in_this_response(db_session, factories):
    """`series_note` и `series_updated_at` приходят В ЭТОМ ответе (DoD 38, 14, 13)."""
    series_id = _series(
        db_session, note="официальная публикация, по РФ", forecast=(2026,)
    )
    ids = _selection_of_three(db_session, factories)

    data = cmp.build_comparison(
        db_session, ids, vat_mode=cmp.VAT_MODE_OWN,
        inflation_series_id=series_id, target_month=TARGET_AUG_2026,
    )

    block = data["inflation"]
    assert block["series_id"] == series_id
    assert block["series_name"] == SERIES_NAME
    assert block["series_note"] == "официальная публикация, по РФ"
    assert block["series_updated_at"] is not None
    assert block["target_month"] == "2026-08"
    assert block["has_forecast"] is True
    assert [item["year"] for item in block["used_years"]] == [2024, 2025, 2026]
    assert block["used_years"][0]["source"] == "бюллетень 01.2026"


def test_archived_series_metadata_still_travels(db_session, factories):
    """Тот же состав для АРХИВНОГО ряда по явному id (DoD 38).

    Второй запрос к списку выбора его бы не нашёл — архивных там нет, — и полоса
    уровней осталась бы без примечания и без даты правки.
    """
    series_id = _series(db_session, note="ряд в архиве")
    fx.archive_series(db_session, series_id)
    ids = _selection_of_three(db_session, factories)

    data = cmp.build_comparison(
        db_session, ids, vat_mode=cmp.VAT_MODE_OWN,
        inflation_series_id=series_id, target_month=TARGET_AUG_2026,
    )

    assert data["inflation"]["series_note"] == "ряд в архиве"
    assert data["inflation"]["series_updated_at"] is not None


def test_same_month_target_keeps_the_inflation_block_with_no_used_years(
    db_session, factories
):
    """Цель = месяц сметы: `used_years` пуст, а блок `inflation` В ОТВЕТЕ ЕСТЬ (DoD 5, 13).

    Пустой список — не то же, что отсутствие приведения: подпись оси обязана
    сказать, что цены приведены, даже когда приводить было нечего.
    """
    series_id = _series(db_session, {})
    contract_id = fx.contract_with_money_everywhere(
        db_session, factories, signed_date=dt.date(2026, 8, 15), gross=GROSS_PER_PLACE
    )

    data = cmp.build_comparison(
        db_session, [contract_id], vat_mode=cmp.VAT_MODE_OWN,
        inflation_series_id=series_id, target_month=TARGET_AUG_2026,
    )

    assert data["inflation"]["used_years"] == []
    assert data["inflation"]["has_forecast"] is False
    assert data["columns"][0]["inflation_coefficient"] == Decimal(1)


def test_caption_names_the_selected_series_and_the_month(db_session, factories):
    """Подпись оси называет ВЫБРАННЫЙ ряд и месяц (DoD 31), прогноз — отдельно."""
    series_id = _series(db_session, forecast=(2026,))
    ids = _selection_of_three(db_session, factories)

    data = cmp.build_comparison(
        db_session, ids, vat_mode=cmp.VAT_MODE_OWN,
        inflation_series_id=series_id, target_month=TARGET_AUG_2026,
    )

    caption = data["caption"]
    # Прежняя половина подписи не исчезла: налоговый состав объявлен по-прежнему.
    assert "своей действующей ставке НДС" in caption
    assert "августу 2026" in caption
    assert SERIES_NAME in caption
    assert "прогноз" in caption


def test_two_series_give_two_captions_and_different_numbers(db_session, factories):
    """Смена ряда меняет И подпись, И числа (DoD 31).

    Оба утверждения в одном тесте намеренно: подпись, следующая за выбором при
    одинаковых числах, означала бы, что селектор меняет надпись, не меняя расчёта,
    — та же ложь, только незаметнее (дефект макета, §7 спеки).
    """
    official = _series(db_session)
    internal = _series(
        db_session, {2024: "1.120", 2025: "1.150", 2026: "1.090"}, name=OTHER_NAME
    )
    ids = _selection_of_three(db_session, factories)

    first = cmp.build_comparison(
        db_session, ids, vat_mode=cmp.VAT_MODE_OWN,
        inflation_series_id=official, target_month=TARGET_AUG_2026,
    )
    second = cmp.build_comparison(
        db_session, ids, vat_mode=cmp.VAT_MODE_OWN,
        inflation_series_id=internal, target_month=TARGET_AUG_2026,
    )

    assert SERIES_NAME in first["caption"]
    assert OTHER_NAME in second["caption"]
    assert SERIES_NAME not in second["caption"]

    earliest = ids[0]
    assert _cell(first, "1.1", earliest)["net"] != _cell(second, "1.1", earliest)["net"]
    assert (
        _row(first, "1.1")["medians"][cmp.BUCKET_TOTAL]["value"]
        != _row(second, "1.1")["medians"][cmp.BUCKET_TOTAL]["value"]
    )


def test_nominal_response_carries_no_inflation_keys(db_session, factories):
    """Без выбранного ряда ответ не несёт НИ ОДНОГО инфляционного ключа (DoD 1).

    Дубль golden-снимка задачи 1 намеренный: снимок скажет «ответ изменился», а
    этот тест — ЧЕМ именно, и назовёт ключи по именам.
    """
    ids = _selection_of_three(db_session, factories)

    data = cmp.build_comparison(db_session, ids, vat_mode=cmp.VAT_MODE_OWN)

    assert "inflation" not in data
    for column in data["columns"]:
        assert "inflation_coefficient" not in column
        assert "inflation_factors" not in column


def test_target_month_without_a_series_is_a_400(db_session, factories):
    """Умолчательного ряда не существует: справочник пустой и рядов может быть
    несколько (§2.12). Проверка живёт в агрегате, а не в роутере, потому что
    маршрутов ДВА и они обязаны отвечать одинаково."""
    ids = _selection_of_three(db_session, factories)

    with pytest.raises(DomainError) as exc:
        cmp.build_comparison(
            db_session, ids, vat_mode=cmp.VAT_MODE_OWN, target_month=TARGET_AUG_2026
        )
    assert exc.value.status_code == 400


def test_series_without_month_resolves_the_current_period(db_session, factories, monkeypatch):
    """Ряд без месяца: сервер разрешает ТЕКУЩИЙ месяц в названной зоне и
    ВОЗВРАЩАЕТ его (§2.7, DoD 19).

    Момент фиксируется подменой шва `_now_in`: иначе тест зависел бы от дня
    прогона, а «текущий месяц» — подвижная предпосылка.
    """
    import money.inflation as inflation_module

    moment = dt.datetime(2026, 8, 19, 12, 0, tzinfo=dt.UTC)
    monkeypatch.setattr(inflation_module, "_now_in", lambda zone: moment.astimezone(zone))

    series_id = _series(db_session)
    ids = _selection_of_three(db_session, factories)

    data = cmp.build_comparison(
        db_session, ids, vat_mode=cmp.VAT_MODE_OWN, inflation_series_id=series_id
    )

    assert data["inflation"]["target_month"] == "2026-08"


def test_estimate_without_a_factor_fails_loudly(db_session, factories):
    """Смета без коэффициента при непустом приведении — громкий `RuntimeError`.

    `resolve_inflation` и `load_rollups` читают сметы СВОИМИ запросами, а в
    `READ COMMITTED` каждый оператор берёт свой снимок даже внутри одной
    транзакции: набор смет выборки между двумя чтениями в принципе может
    разойтись. Тихое последствие — смета, которой нет в плане, осталась бы
    НЕПРИВЕДЁННОЙ и смешалась с приведёнными. Проверяется искусственно урезанным
    `adjustment`, потому что гонку в тесте не воспроизвести.
    """
    ids = _selection_of_three(db_session, factories)

    with pytest.raises(RuntimeError):
        cmp.load_rollups(db_session, ids, adjustment={-1: Decimal("1.1")})


def test_query_budget_is_five_without_adjustment_and_eight_with_it(db_session, factories):
    """Абсолютный бюджет запросов, а не «не растёт с выборкой» (решение плана №4).

    Пять без приведения: четыре в `load_rollups` плюс `_load_columns`. Восемь с
    ним: те же пять плюс три у `resolve_inflation` — ряд по id, значения ряда,
    даты смет. Число утверждается точным: «не растёт» пропустило бы лишний
    запрос, добавленный один раз на всю выборку.
    """
    series_id = _series(db_session)
    ids = _selection_of_three(db_session, factories)

    with _count_queries(db_session) as nominal:
        cmp.build_comparison(db_session, ids, vat_mode=cmp.VAT_MODE_OWN)
    assert nominal.total == 5

    with _count_queries(db_session) as adjusted:
        cmp.build_comparison(
            db_session, ids, vat_mode=cmp.VAT_MODE_OWN,
            inflation_series_id=series_id, target_month=TARGET_AUG_2026,
        )
    assert adjusted.total == 8
