"""Суммы статей, ₽/м², пары нетто/показано, ставка колонки истории (спека 4а §2.8, §2.9)."""

from decimal import Decimal

import pytest

from services.our_price_money import (
    UNALLOCATED,
    CategoryRow,
    article_net,
    estimate_net_total,
    history_shown_rate,
    money_pair,
    net_by_root,
    pct_to_median,
    per_sqm,
)

D = Decimal
ROOT_OF = {10: 10, 11: 10, 12: 10, 20: 20}


def _row(estimate_id, category, base, amount):
    return CategoryRow(
        estimate_id=estimate_id,
        work_category_id=category,
        vat_base=None if base is None else D(base),
        amount=None if amount is None else D(amount),
    )


# --- article_net -----------------------------------------------------------


@pytest.mark.parametrize("zero", ["0", "0.00", "-0", "1E-100", "-1E-98", "0.0049", "-0.0049"])
def test_our_price_money_article_net_zero_to_the_cent_means_no_article(zero):
    assert article_net(D(zero)) is None


@pytest.mark.parametrize("value", ["0.005", "-0.005", "0.01", "-100", "80"])
def test_our_price_money_article_net_keeps_nonzero_values_including_negative(value):
    assert article_net(D(value)) == D(value)


def test_our_price_money_article_net_passes_unknown_net_through():
    assert article_net(None) is None


# --- net_by_root -----------------------------------------------------------


def test_our_price_money_net_by_root_converts_amount_by_own_base():
    rows = [_row(1, 10, "20", "120"), _row(1, 20, "22", "122")]
    assert net_by_root(rows, ROOT_OF) == {1: {10: D("100"), 20: D("100")}}


def test_our_price_money_net_by_root_accumulates_rows_of_one_estimate_and_root():
    # смета из двух лотов: две строки VIEW одной статьи, 120/1.2 + 60/1.2 = 150
    rows = [_row(1, 10, "20", "120"), _row(1, 10, "20", "60")]
    assert net_by_root(rows, ROOT_OF)[1][10] == D("150")


def test_our_price_money_net_by_root_accumulates_without_intermediate_rounding():
    # 1/1.22 + 1/1.22 = 200/122 = 1.6393442622950819672...; по копейкам на строку вышло бы 0.82 + 0.82 = 1.64
    rows = [_row(1, 10, "22", "1"), _row(1, 10, "22", "1")]
    total = net_by_root(rows, ROOT_OF)[1][10]
    assert total is not None
    assert str(total).startswith("1.63934426229508196721311475409836")


def test_our_price_money_net_by_root_sums_positive_and_negative_positions():
    rows = [_row(1, 10, "0", "100"), _row(1, 10, "0", "-20")]
    assert net_by_root(rows, ROOT_OF)[1][10] == D("80")


def test_our_price_money_net_by_root_keeps_zero_total_as_zero_not_none():
    rows = [_row(1, 10, "0", "100"), _row(1, 10, "0", "-100")]
    assert net_by_root(rows, ROOT_OF)[1][10] == D("0")


def test_our_price_money_net_by_root_child_category_goes_to_its_root():
    rows = [_row(1, 11, "0", "30"), _row(1, 12, "0", "50"), _row(1, 10, "0", "20")]
    assert net_by_root(rows, ROOT_OF) == {1: {10: D("100")}}


def test_our_price_money_net_by_root_null_category_goes_to_unallocated():
    rows = [_row(1, None, "0", "70"), _row(1, 10, "0", "30")]
    result = net_by_root(rows, ROOT_OF)
    assert UNALLOCATED is None
    assert result == {1: {None: D("70"), 10: D("30")}}


def test_our_price_money_net_by_root_unknown_category_is_caller_error():
    with pytest.raises(KeyError):
        net_by_root([_row(1, 99, "0", "10")], ROOT_OF)


def test_our_price_money_net_by_root_unknown_base_poisons_the_root():
    rows = [_row(1, 10, "0", "40"), _row(1, 10, None, "10")]
    assert net_by_root(rows, ROOT_OF)[1][10] is None


def test_our_price_money_net_by_root_later_rows_do_not_heal_poisoned_root():
    rows = [_row(1, 10, None, "10"), _row(1, 10, "0", "40"), _row(1, 11, "20", "120")]
    assert net_by_root(rows, ROOT_OF)[1][10] is None


def test_our_price_money_net_by_root_poison_stays_in_its_root_and_estimate():
    rows = [
        _row(1, 10, None, "10"),
        _row(1, 20, "0", "25"),
        _row(2, 10, "0", "40"),
    ]
    assert net_by_root(rows, ROOT_OF) == {1: {10: None, 20: D("25")}, 2: {10: D("40")}}


def test_our_price_money_net_by_root_row_without_amount_adds_nothing_and_does_not_poison():
    # у строки без суммы база может быть любой, в том числе неизвестной
    rows = [_row(1, 10, None, None), _row(1, 10, "20", None), _row(1, 10, "0", "40")]
    assert net_by_root(rows, ROOT_OF)[1][10] == D("40")


def test_our_price_money_net_by_root_only_empty_amounts_give_no_root_value():
    assert net_by_root([_row(1, 10, "20", None)], ROOT_OF) == {1: {}}


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_our_price_money_net_by_root_non_finite_amount_is_treated_as_empty(bad):
    rows = [_row(1, 10, None, bad), _row(1, 10, "0", bad), _row(1, 10, "0", "40")]
    assert net_by_root(rows, ROOT_OF)[1][10] == D("40")


# --- estimate_net_total ----------------------------------------------------


def test_our_price_money_estimate_net_total_sums_roots_with_negative_root():
    assert estimate_net_total({10: D("100"), 20: D("-30"), None: D("5")}) == D("75")


def test_our_price_money_estimate_net_total_any_unknown_root_gives_none():
    assert estimate_net_total({10: D("100"), 20: None}) is None


def test_our_price_money_estimate_net_total_is_not_rounded():
    assert estimate_net_total({10: D("0.004"), 20: D("0.004")}) == D("0.008")


def test_our_price_money_estimate_net_total_empty_is_zero_not_none():
    total = estimate_net_total({})
    assert total is not None
    assert total == D("0")


# --- per_sqm ---------------------------------------------------------------


def test_our_price_money_per_sqm_divides_without_rounding():
    assert per_sqm(D("1000"), D("8")) == D("125")
    assert per_sqm(D("100"), D("8")) == D("12.5")
    third = per_sqm(D("100"), D("3"))
    assert third is not None
    assert str(third).startswith("33." + "3" * 28)


def test_our_price_money_per_sqm_negative_and_zero_net_are_legal():
    assert per_sqm(D("-100"), D("4")) == D("-25")
    assert per_sqm(D("0"), D("4")) == D("0")


def test_our_price_money_per_sqm_no_net_is_none():
    assert per_sqm(None, D("8")) is None


@pytest.mark.parametrize("area", [None, D("0"), D("NaN"), D("-5"), D("Infinity")])
def test_our_price_money_per_sqm_area_failing_price_predicate_is_none(area):
    assert per_sqm(D("1000"), area) is None


# --- money_pair ------------------------------------------------------------


def test_our_price_money_pair_shown_is_net_raised_by_rate():
    assert money_pair(D("100"), D("22"), unit=False) == {"net": D("100"), "shown": D("122")}


def test_our_price_money_pair_contract_with_own_base_20_shown_at_22_from_net():
    net = net_by_root([_row(1, 10, "20", "120")], ROOT_OF)[1][10]
    pair = money_pair(net, D("22"), unit=False)
    assert pair == {"net": D("100.00"), "shown": D("122.00")}


def test_our_price_money_pair_no_rate_gives_null_shown():
    assert money_pair(D("100"), None, unit=False) == {"net": D("100"), "shown": None}
    assert money_pair(D("100"), None, unit=True) == {"net": D("100"), "shown": None}


def test_our_price_money_pair_no_net_is_none_not_dict():
    assert money_pair(None, D("22"), unit=False) is None
    assert money_pair(None, D("22"), unit=True) is None
    assert money_pair(None, None, unit=False) is None


def test_our_price_money_pair_sum_rounds_both_fields_to_kopecks():
    pair = money_pair(D("100.004"), D("20"), unit=False)
    assert pair is not None
    assert pair["net"] == D("100.00")
    assert pair["net"].as_tuple().exponent == -2
    assert pair["shown"] == D("120.00")  # 120.0048 -> 120.00
    assert pair["shown"].as_tuple().exponent == -2


def test_our_price_money_pair_sum_rounds_up_on_the_other_side_of_half_kopeck():
    pair = money_pair(D("100.006"), D("0"), unit=False)
    assert pair == {"net": D("100.01"), "shown": D("100.01")}


def test_our_price_money_pair_shown_is_raised_from_unrounded_net():
    # 100.0049 * 1.22 = 122.005978 -> 122.01; из округлённого нетто 100.00 вышло бы 122.00
    pair = money_pair(D("100.0049"), D("22"), unit=False)
    assert pair == {"net": D("100.00"), "shown": D("122.01")}


def test_our_price_money_pair_unit_rate_is_not_rounded():
    pair = money_pair(D("1.23456"), D("20"), unit=True)
    assert pair == {"net": D("1.23456"), "shown": D("1.481472")}


# --- history_shown_rate ----------------------------------------------------


def test_our_price_money_history_rate_single_common_rate_wins():
    assert history_shown_rate([D("22"), D("22"), D("22")]) == D("22")


def test_our_price_money_history_rate_compares_values_not_spelling():
    assert history_shown_rate([D("22"), D("22.00")]) == D("22")


def test_our_price_money_history_rate_two_distinct_rates_give_none():
    assert history_shown_rate([D("0"), D("22")]) is None
    assert history_shown_rate([D("22"), D("20"), D("22")]) is None


def test_our_price_money_history_rate_no_known_rate_gives_none():
    assert history_shown_rate([]) is None
    assert history_shown_rate([None, None]) is None


def test_our_price_money_history_rate_unknown_base_participants_do_not_vote():
    assert history_shown_rate([None, D("20"), None, D("20")]) == D("20")


def test_our_price_money_history_rate_zero_percent_is_a_rate():
    rate = history_shown_rate([None, D("0")])
    assert rate is not None
    assert rate == D("0")


def test_our_price_money_history_rate_accepts_a_generator():
    assert history_shown_rate(r for r in [D("22"), None]) == D("22")


# --- pct_to_median ---------------------------------------------------------


def test_our_price_money_pct_to_median_above_median_is_positive():
    assert pct_to_median(D("150"), [D("100")]) == D("50")


def test_our_price_money_pct_to_median_below_median_is_negative():
    assert pct_to_median(D("90"), [D("100")]) == D("-10")


def test_our_price_money_pct_to_median_even_pool_uses_mean_of_two_middle():
    # медиана (100 + 200) / 2 = 150; 165 / 150 - 1 = 10 %
    assert pct_to_median(D("165"), [D("200"), D("100")]) == D("10")


def test_our_price_money_pct_to_median_odd_pool_uses_middle_value():
    assert pct_to_median(D("110"), [D("300"), D("100"), D("5")]) == D("10")


def test_our_price_money_pct_to_median_is_not_rounded():
    pct = pct_to_median(D("1"), [D("3")])
    assert pct is not None
    assert str(pct).startswith("-66." + "6" * 28)


def test_our_price_money_pct_to_median_no_value_or_empty_pool_is_none():
    assert pct_to_median(None, [D("100")]) is None
    assert pct_to_median(D("100"), []) is None


@pytest.mark.parametrize(
    ("value", "pool"),
    [
        (D("5"), [D("0"), D("0")]),
        (D("0"), [D("0")]),
        (D("5"), [D("0")]),
        # чётный пул с нулевой медианой: (-50 + 50) / 2
        (D("5"), [D("-50"), D("50")]),
    ],
    ids=["two_zeros", "single_zero_value_zero", "single_zero_value_positive", "even_pool_zero_mean"],
)
def test_our_price_money_pct_to_median_zero_median_is_none(value, pool):
    assert pct_to_median(value, pool) is None


@pytest.mark.parametrize(
    ("value", "pool"),
    [
        (D("-50"), [D("-100")]),
        (D("50"), [D("-100")]),
        # чётный пул с отрицательной медианой: (-30 + 10) / 2 = -10
        (D("10"), [D("-30"), D("10")]),
    ],
    ids=["negative_value", "positive_value", "even_pool_negative_mean"],
)
def test_our_price_money_pct_to_median_negative_median_is_none(value, pool):
    assert pct_to_median(value, pool) is None


def test_our_price_money_pct_to_median_smallest_positive_median_still_counts():
    assert pct_to_median(D("0.02"), [D("0.01")]) == D("100")
