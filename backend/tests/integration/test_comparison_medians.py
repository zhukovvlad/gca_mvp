"""Медиана в ставке показа — `totals_medians[].shown_per_sqm` (план, задача 5).

`_median_dict` (используется и для `rows[].medians`, и для `totals_medians`)
не трогается — она отдаёт `value`/`comparable_count`/`contract_ids` и НЕ несёт
режима показа. Правило спеки §2.10 требует поля `shown_per_sqm` только на
`totals_medians`, и только когда режим НДС допускает единую ось показа:

* `net`/`single` — ключ ЕСТЬ ВСЕГДА (значение может быть `None`, если
  сопоставимых меньше трёх — то же правило, что у `value`);
* `own` — ключа НЕТ вовсе (у каждого договора своя ставка, единой оси нет).

`rows[].medians` этот ключ не несёт НИКОГДА, ни в одном режиме, ни в одной
корзине — им продолжает пользоваться голый `_median_dict`.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from crud import comparison as cmp
from tests import comparison_fixtures as fx

pytestmark = pytest.mark.integration

_BUCKETS = (cmp.BUCKET_BASE, cmp.BUCKET_AMENDMENTS, cmp.BUCKET_TOTAL)


def _totals_median(agg: dict, *, bucket: str) -> dict:
    return agg["totals_medians"][bucket]


def _all_row_medians(agg: dict) -> list[dict]:
    """Медианы ВСЕХ строк, ВСЕХ корзин — плоский список словарей."""
    return [
        row["medians"][bucket]
        for row in agg["rows"]
        for bucket in _BUCKETS
    ]


# ---------------------------------------------------------------------------
#  Фикстура с ненулевыми, различными медианами в ВСЕХ ТРЁХ корзинах.
# ---------------------------------------------------------------------------
#
# Три договора, у каждого ДГП и ОДНО ДС, ставка НДС 0 % (нетто = валовое —
# арифметика ставки показа тестом не проверяется здесь, только присутствие и
# происхождение значения). Площадь каждого объекта — 100 м² (50 + 50), суммы
# подобраны так, чтобы медиана в КАЖДОЙ из трёх корзин была отдельным круглым
# числом:
#
#   ДГП:  100 / 200 / 300 -> net_per_sqm 1 / 2 / 3   -> медиана 2
#   ДС:   400 / 500 / 600 -> net_per_sqm 4 / 5 / 6   -> медиана 5
#   Итого: 500 / 700 / 900 -> net_per_sqm 5 / 7 / 9  -> медиана 7
def _three_bucket_contracts(db, factories) -> list[int]:
    x = fx.contract_with_amendment(
        db, factories, base_rate=Decimal("0"), amd_rate=Decimal("0"),
        base="100.00", amd="400.00", area_aboveground="50", area_underground="50",
    )
    y = fx.contract_with_amendment(
        db, factories, base_rate=Decimal("0"), amd_rate=Decimal("0"),
        base="200.00", amd="500.00", area_aboveground="50", area_underground="50",
    )
    z = fx.contract_with_amendment(
        db, factories, base_rate=Decimal("0"), amd_rate=Decimal("0"),
        base="300.00", amd="600.00", area_aboveground="50", area_underground="50",
    )
    db.flush()
    return [x.id, y.id, z.id]


# ---------------------------------------------------------------------------
#  1. Режим "net": shown_per_sqm == value, в ВСЕХ ТРЁХ корзинах.
# ---------------------------------------------------------------------------

def test_net_mode_shown_per_sqm_equals_value_in_all_three_buckets(db_session, factories):
    ids = _three_bucket_contracts(db_session, factories)

    agg = cmp.build_comparison(db_session, ids, vat_mode=cmp.VAT_MODE_NET)

    expected = {
        cmp.BUCKET_BASE: Decimal("2"),
        cmp.BUCKET_AMENDMENTS: Decimal("5"),
        cmp.BUCKET_TOTAL: Decimal("7"),
    }
    for bucket in _BUCKETS:
        median = _totals_median(agg, bucket=bucket)
        assert median["value"] == expected[bucket], (
            f"предпосылка: медиана корзины {bucket!r} обязана быть известным круглым числом"
        )
        assert "shown_per_sqm" in median
        assert median["shown_per_sqm"] == median["value"], (
            f"режим net: shown_per_sqm обязан ДУБЛИРОВАТЬ value в корзине {bucket!r}"
        )
        assert isinstance(median["shown_per_sqm"], Decimal), (
            "сравнение обязано быть Decimal с Decimal, не строк и не float"
        )


# ---------------------------------------------------------------------------
#  2. Режим "single": shown_per_sqm == net_to_gross(value, rate), ЧИСЛОМ.
# ---------------------------------------------------------------------------

def test_single_mode_shown_per_sqm_is_net_to_gross_of_value(db_session, factories):
    """Медиана нетто, выведенная из фикстуры: 200 (см. ниже). Ставка показа —
    20 %, произведение — 240 (200 * 1.2). Оба числа — следствие фикстуры, не
    переписаны из брифа."""
    a = fx.contract_with_area(
        db_session, factories, {"1": ["100000.00"]}, vat_rate=Decimal("0"),
        area_aboveground="500", area_underground="500",
    )
    b = fx.contract_with_area(
        db_session, factories, {"1": ["200000.00"]}, vat_rate=Decimal("0"),
        area_aboveground="500", area_underground="500",
    )
    c = fx.contract_with_area(
        db_session, factories, {"1": ["300000.00"]}, vat_rate=Decimal("0"),
        area_aboveground="500", area_underground="500",
    )
    ids = [a, b, c]

    agg = cmp.build_comparison(
        db_session, ids, vat_mode=cmp.VAT_MODE_SINGLE, single_rate=Decimal("20")
    )
    median = _totals_median(agg, bucket=cmp.BUCKET_TOTAL)

    assert median["value"] == Decimal("200"), (
        "предпосылка: медиана нетто выборки равна известному круглому числу 200 "
        "(net_per_sqm 100/200/300 при площади 1000 м², средний по трём — 200)"
    )
    assert median["shown_per_sqm"] == cmp.net_to_gross(median["value"], Decimal("20")), (
        "shown_per_sqm обязан быть выведен через net_to_gross ИЗ value"
    )
    assert median["shown_per_sqm"] == Decimal("240"), (
        "конкретное число: 200 нетто при ставке 20% -> 240 валовых (ловит случай, "
        "когда обе стороны сравнения ошиблись одинаково)"
    )


# ---------------------------------------------------------------------------
#  3. Режим "own": ключа НЕТ вовсе, в ВСЕХ ТРЁХ корзинах.
# ---------------------------------------------------------------------------

def test_own_mode_has_no_shown_per_sqm_key_in_any_bucket(db_session, factories):
    ids = _three_bucket_contracts(db_session, factories)

    agg = cmp.build_comparison(db_session, ids, vat_mode=cmp.VAT_MODE_OWN)

    for bucket in _BUCKETS:
        median = _totals_median(agg, bucket=bucket)
        assert median["value"] is not None, (
            f"предпосылка: медиана корзины {bucket!r} вычислима — иначе отсутствие "
            "ключа не отличить от отсутствия медианы"
        )
        assert "shown_per_sqm" not in median, (
            f"режим own: у каждого договора своя ставка, единой оси показа НЕТ — "
            f"ключ обязан отсутствовать в корзине {bucket!r}"
        )


# ---------------------------------------------------------------------------
#  4. rows[].medians НИКОГДА не несут shown_per_sqm — ни в одной строке,
#     ни в одной корзине, независимо от режима показа.
# ---------------------------------------------------------------------------

def test_row_medians_never_carry_shown_per_sqm(db_session, factories):
    ids = [
        fx.contract_with_area(
            db_session, factories,
            {"1": ["1200.00"], "2": ["600.00"]}, vat_rate=Decimal("20"),
        )
        for _ in range(3)
    ]
    db_session.flush()

    for mode, rate in (
        (cmp.VAT_MODE_NET, None),
        (cmp.VAT_MODE_SINGLE, Decimal("18")),
        (cmp.VAT_MODE_OWN, None),
    ):
        agg = cmp.build_comparison(db_session, ids, vat_mode=mode, single_rate=rate)
        assert agg["rows"], "предпосылка: строки есть — иначе проверка пуста"
        for median in _all_row_medians(agg):
            assert "shown_per_sqm" not in median, (
                f"режим {mode!r}: rows[].medians не имеет права нести shown_per_sqm — "
                "это поле только у totals_medians"
            )


# ---------------------------------------------------------------------------
#  5. Меньше трёх сопоставимых: ключ ПРИСУТСТВУЕТ и равен None (net и single).
# ---------------------------------------------------------------------------

def test_fewer_than_three_comparable_keeps_key_present_but_none_net(db_session, factories):
    a = fx.contract_with_area(db_session, factories, {"1": ["1200.00"]})
    b = fx.contract_with_area(db_session, factories, {"1": ["2400.00"]})

    agg = cmp.build_comparison(db_session, [a, b], vat_mode=cmp.VAT_MODE_NET)
    median = _totals_median(agg, bucket=cmp.BUCKET_TOTAL)

    assert median["comparable_count"] == 2, "предпосылка: меньше трёх сопоставимых"
    assert median["value"] is None
    assert "shown_per_sqm" in median, "режим net: ключ обязан присутствовать всегда"
    assert median["shown_per_sqm"] is None, "медианы нет -> и shown_per_sqm None"


def test_fewer_than_three_comparable_keeps_key_present_but_none_single(db_session, factories):
    a = fx.contract_with_area(db_session, factories, {"1": ["1200.00"]})
    b = fx.contract_with_area(db_session, factories, {"1": ["2400.00"]})

    agg = cmp.build_comparison(
        db_session, [a, b], vat_mode=cmp.VAT_MODE_SINGLE, single_rate=Decimal("20")
    )
    median = _totals_median(agg, bucket=cmp.BUCKET_TOTAL)

    assert median["comparable_count"] == 2, "предпосылка: меньше трёх сопоставимых"
    assert median["value"] is None
    assert "shown_per_sqm" in median, "режим single: ключ обязан присутствовать всегда"
    assert median["shown_per_sqm"] is None, "медианы нет -> и shown_per_sqm None"


# ---------------------------------------------------------------------------
#  6. "single" БЕЗ явной ставки — обязан взять EFFECTIVE_single_rate
#     (предвыбор), а не остаться None (главная ловушка брифа §5.2).
# ---------------------------------------------------------------------------

def test_single_mode_without_explicit_rate_uses_effective_rate_for_median(db_session, factories):
    a = fx.contract_with_area(db_session, factories, {"1": ["100.00"]}, vat_rate=Decimal("16"))
    b = fx.contract_with_area(db_session, factories, {"1": ["100.00"]}, vat_rate=Decimal("20"))
    c = fx.contract_with_area(db_session, factories, {"1": ["100.00"]}, vat_rate=Decimal("22"))
    ids = [a, b, c]

    agg = cmp.build_comparison(db_session, ids, vat_mode=cmp.VAT_MODE_SINGLE, single_rate=None)
    median = _totals_median(agg, bucket=cmp.BUCKET_TOTAL)

    assert agg["single_rate"] == Decimal("22"), (
        "предпосылка: три ставки, каждая по разу -> ничья -> предвыбор — бо́льшая "
        "(то же правило, что test_rate_options_and_preselection)"
    )
    assert median["value"] is not None, "предпосылка: медиана вычислима — три сопоставимых"
    assert median["shown_per_sqm"] is not None, (
        "БЕЗ подстановки предвыбора линия медианы исчезла бы там, где столбцы уже "
        "с числами — объяснить это было бы нечем (§5.2 брифа)"
    )
    assert median["shown_per_sqm"] == cmp.net_to_gross(median["value"], agg["single_rate"]), (
        "shown_per_sqm обязан быть согласован с ОТДАННЫМ single_rate (предвыбором), "
        "а не с исходным (None) параметром запроса"
    )
