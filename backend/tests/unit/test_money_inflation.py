"""Ядро приведения к ценовому уровню: `money/inflation.py` (спека
`2026-08-18-inflation-adjustment-design.md` §2.4, §2.5; план, задача 2).

Числа здесь — не примеры, а замеры гейта 3, воспроизведённые до последнего знака
при `k(2024)=1.075`, `k(2025)=1.083`, `k(2026)=1.060` и `prec=34`. Ожидания
записаны литералами, а не пересчётом теми же функциями: тест, считающий ожидание
той же формулой, доказывает лишь то, что функция равна себе.

Три свойства, которые именно здесь и стерегутся, потому что дальше по конвейеру
они уже не наблюдаемы:

1. **год, сокращающийся тождественно, не требуется** — правило «сплошной
   диапазон min…max» математически неверно (§2.5);
2. **каноническая форма одна** — годы по возрастанию, одна `**` на год, затем
   последовательное умножение; вторая форма расходится в последнем знаке при
   `prec=34`, и экран с листом разъехались бы, считая одно и то же двумя путями;
3. **зона «текущего месяца» читается**, а не подменяется часами процесса (§2.7).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from money import inflation as inf
from money.vat import quantize_money

#: Ряд замеров гейта 3. Прогноз/факт здесь не важны — ядро формулы о них не знает.
SERIES = {
    2024: Decimal("1.075"),
    2025: Decimal("1.083"),
    2026: Decimal("1.060"),
}


def ym(year: int, month: int) -> inf.YearMonth:
    return inf.YearMonth(year, month)


def factor(source: inf.YearMonth, target: inf.YearMonth, values=None) -> Decimal:
    """Коэффициент пары дат по канонической форме — сквозь ОБЕ функции ядра."""
    return inf.coefficient(inf.year_exponents(source, target), SERIES if values is None else values)


# ---------------------------------------------------------------------------
#  Показатели степени: какие годы вообще требуются (§2.5)
# ---------------------------------------------------------------------------

def test_december_to_december_requires_only_the_later_year():
    """дек 2024 → дек 2025 требует ТОЛЬКО `k(2025)`: 2024-й сокращается (DoD 4)."""
    assert inf.year_exponents(ym(2024, 12), ym(2025, 12)) == {2025: Decimal(1)}
    assert inf.required_years(ym(2024, 12), ym(2025, 12)) == [2025]


def test_coefficient_of_december_step_is_the_series_value_itself():
    """Тот же переход даёт РОВНО значение ряда — семантика «декабрь к декабрю» (DoD 3)."""
    assert factor(ym(2024, 12), ym(2025, 12)) == Decimal("1.083")


def test_year_missing_from_the_series_is_not_needed_when_it_cancels():
    """Приведение считается при ОТСУТСТВИИ строки за 2024 год (DoD 4).

    Утверждение не про равенство числа, а про то, что ряд без 2024-го подходит:
    правило «сплошной диапазон» потребовало бы его напрасно и отказало бы здесь.
    """
    without_2024 = {2025: SERIES[2025], 2026: SERIES[2026]}
    assert factor(ym(2024, 12), ym(2025, 12), without_2024) == Decimal("1.083")


def test_same_month_needs_no_series_rows_at_all():
    """Месяц → тот же месяц: показателей нет, коэффициент ровно 1 (DoD 5).

    Ряд передаётся ПУСТЫМ намеренно: «ни одной строки не требуется» — часть
    утверждения, а не оговорка. С непустым рядом тест не отличил бы «не
    понадобилось» от «понадобилось и нашлось».
    """
    assert inf.year_exponents(ym(2026, 3), ym(2026, 3)) == {}
    assert inf.required_years(ym(2026, 3), ym(2026, 3)) == []
    assert inf.coefficient({}, {}) == Decimal(1)


def test_exponents_of_a_two_year_span_are_partial_at_both_ends():
    """май 2024 → авг 2026: `7/12`, `1`, `2/3` — и ни одного года ниже 2024-го.

    Дроби записаны литералами `prec=34`: показатель считается делением внутри
    того же контекста, и его округление входит в результат наравне со степенью.
    """
    assert inf.year_exponents(ym(2024, 5), ym(2026, 8)) == {
        2024: Decimal("0.5833333333333333333333333333333333"),
        2025: Decimal("1"),
        2026: Decimal("0.6666666666666666666666666666666667"),
    }
    assert inf.required_years(ym(2024, 5), ym(2026, 8)) == [2024, 2025, 2026]


def test_backward_adjustment_uses_negative_exponents():
    """Обратное направление законно и отдельной ветки не требует (§2.5)."""
    exponents = inf.year_exponents(ym(2026, 7), ym(2024, 5))
    assert sorted(exponents) == [2024, 2025, 2026]
    assert all(value < 0 for value in exponents.values())


# ---------------------------------------------------------------------------
#  Коэффициенты: замеры гейта 3 до последнего знака
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("source", "target", "expected"),
    [
        ((2024, 5), (2026, 8), "1.174412425779300303760548277958037"),
        ((2026, 7), (2026, 8), "1.004867550565343037541198945587506"),
        ((2025, 2), (2026, 8), "1.111034702376096271381243623987202"),
        ((2026, 7), (2024, 5), "0.8556342972091315960133676609758070"),
        ((2025, 1), (2025, 12), "1.075827773742335083053899972842088"),
    ],
)
def test_coefficient_matches_measured_value(source, target, expected):
    assert factor(ym(*source), ym(*target)) == Decimal(expected)


def test_level_ratio_of_neighbouring_decembers_is_the_series_value():
    """`L(дек y) / L(дек y−1) = k(y)` — семантика показателя (DoD 3).

    Уровень берётся от общей базы (дек 2023): именно так публикуемый цепной
    коэффициент и определён, и если бы показатель означал «январь к январю»
    (`(m−1)/12`, отвергнутая конвенция §2.4), это отношение перестало бы
    совпадать с числом ряда.
    """
    base = ym(2023, 12)
    level_2024 = factor(base, ym(2024, 12))
    level_2025 = factor(base, ym(2025, 12))
    assert level_2025 / level_2024 == SERIES[2025]
    assert level_2024 == SERIES[2024]


def test_missing_required_year_raises_instead_of_substituting_one():
    """Отсутствующий год — отказ, а не подстановка: экстраполяции нет (§2.9).

    Молчаливая единица дала бы «приведение», в котором один год не приведён
    вовсе, и отличить его от честного было бы нечем.
    """
    exponents = inf.year_exponents(ym(2024, 5), ym(2026, 8))
    with pytest.raises(KeyError):
        inf.coefficient(exponents, {2025: SERIES[2025], 2026: SERIES[2026]})


# ---------------------------------------------------------------------------
#  Телескопирование (DoD 6)
# ---------------------------------------------------------------------------

#: Максимальная реальная величина договора ГП, а не пример: DoD 6 требует замера
#: на них, потому что расхождение `1E-33` заметно только на больших суммах.
LARGE_AMOUNT = Decimal("4823456789.17")


def test_telescoping_coefficients_are_exactly_equal():
    """Приведение через промежуточный месяц даёт ТОТ ЖЕ коэффициент."""
    first = factor(ym(2024, 5), ym(2025, 12))
    second = factor(ym(2025, 12), ym(2026, 8))
    direct = factor(ym(2024, 5), ym(2026, 8))
    assert inf.adjust_amount(first, second) == direct


def test_telescoping_amounts_differ_in_the_last_digits_and_agree_after_rounding():
    """На сумме расхождение есть, и оно снимается `quantize_money` (DoD 6).

    Точного равенства неокруглённых `Decimal` НЕ требуется — замерено `1E-33`
    при `prec=34`. Утверждается и то и другое: без первой половины тест
    обещал бы точность, которой нет, без второй — не сказал бы, что деньги
    сходятся там, где их видит человек.
    """
    via = inf.adjust_amount(
        inf.adjust_amount(LARGE_AMOUNT, factor(ym(2024, 5), ym(2025, 12))),
        factor(ym(2025, 12), ym(2026, 8)),
    )
    direct = inf.adjust_amount(LARGE_AMOUNT, factor(ym(2024, 5), ym(2026, 8)))

    assert via != direct
    assert str(via).endswith("318244")
    assert str(direct).endswith("318246")
    assert quantize_money(via) == quantize_money(direct)


# ---------------------------------------------------------------------------
#  «Текущий» месяц и разбор цели
# ---------------------------------------------------------------------------

def test_current_period_reads_the_named_zone_and_not_the_process_clock(monkeypatch):
    """Две зоны у ОДНОГО момента дают разные `YearMonth` (DoD 18, §2.7).

    Момент фиксируется подменой `_now_in`, потому что зоны расходятся МЕСЯЦЕМ
    только у границы месяца: на произвольном «сейчас» тест был бы зелен, даже
    если зона игнорируется вовсе. Подмена честная — она переводит один и тот же
    абсолютный момент в ту зону, которую ей передали, поэтому равные ответы
    означали бы ровно то, что зона не читается.
    """
    moment = dt.datetime(2026, 8, 31, 20, 0, tzinfo=dt.UTC)
    monkeypatch.setattr(inf, "_now_in", lambda zone: moment.astimezone(zone))

    # UTC+14 и UTC−11: у этого момента в первой уже сентябрь, во второй ещё август.
    assert inf.current_period("Pacific/Kiritimati") == inf.YearMonth(2026, 9)
    assert inf.current_period("Pacific/Niue") == inf.YearMonth(2026, 8)


def test_business_timezone_is_a_named_constant_and_resolvable():
    """Константа зоны существует и разрешается в системе (§2.7).

    `tzdata` приходит транзитивно жёсткой зависимостью `psycopg` на win32 —
    отдельной зависимости в `pyproject.toml` не заводится, но проверить, что зона
    действительно разрешима, обязательно: без базы зон вызов упал бы уже в
    рантайме.
    """
    assert inf.BUSINESS_TIMEZONE == "Europe/Moscow"
    assert ZoneInfo(inf.BUSINESS_TIMEZONE) is not None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2026-08", (2026, 8)), ("2024-01", (2024, 1)), ("2025-12", (2025, 12))],
)
def test_parse_target_month_accepts_year_month(raw, expected):
    assert inf.parse_target_month(raw) == inf.YearMonth(*expected)


@pytest.mark.parametrize(
    "raw",
    [
        "2026-08-15",  # день не передаётся вовсе: он игнорируется по §2.4
        "2026-8",
        "2026-13",
        "2026-00",
        "08-2026",
        "2026",
        "",
        "abcd-ef",
    ],
)
def test_parse_target_month_rejects_everything_else(raw):
    """Формат строго `YYYY-MM`; HTTP-код из `ValueError` делает роутер."""
    with pytest.raises(ValueError):
        inf.parse_target_month(raw)


def test_year_month_orders_by_year_then_month():
    """Порядок нужен задаче 7: «цель раньше самой поздней сметы» — сравнение пары."""
    assert inf.YearMonth(2024, 12) < inf.YearMonth(2025, 1)
    assert inf.YearMonth(2026, 8) > inf.YearMonth(2026, 7)
