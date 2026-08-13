from decimal import Decimal, getcontext

import pytest

from money.vat import (
    AmountStatus,
    gross_to_net,
    net_to_gross,
    quantize_money,
    restate_gross,
    vat_from_net,
)


def test_gross_to_net_removes_declared_vat():
    assert gross_to_net(Decimal("120"), Decimal("20")) == Decimal("100")


def test_net_to_gross_adds_target_vat():
    assert net_to_gross(Decimal("100"), Decimal("16")) == Decimal("116")


def test_vat_from_net_is_zero_at_zero_target():
    """Тест против универсального множителя: (100+0)/(100+20) дало бы не ноль."""
    assert vat_from_net(Decimal("100"), Decimal("0")) == Decimal("0")


def test_restate_keeps_value_and_exponent_when_target_equals_base():
    gross = Decimal("100.50")
    result = restate_gross(gross, Decimal("20"), Decimal("20"))
    assert result.status is AmountStatus.ORIGINAL
    assert result.amount == gross
    assert result.amount.as_tuple().exponent == gross.as_tuple().exponent


def test_restate_keeps_value_when_target_not_set():
    gross = Decimal("100.50")
    result = restate_gross(gross, Decimal("20"), None)
    assert result.status is AmountStatus.ORIGINAL
    assert result.amount.as_tuple().exponent == gross.as_tuple().exponent


def test_restate_is_identity_when_override_equals_declared_rate():
    """Ветка тождества не зависит от того, перекрыта ли база."""
    gross = Decimal("100.50")
    result = restate_gross(gross, Decimal("20"), Decimal("20"))
    assert result.status is AmountStatus.ORIGINAL


def test_restate_without_base_reports_unknown_and_returns_source():
    gross = Decimal("100.50")
    result = restate_gross(gross, None, Decimal("16"))
    assert result.status is AmountStatus.UNKNOWN_BASE
    assert result.amount == gross
    assert result.amount.as_tuple().exponent == gross.as_tuple().exponent


def test_restate_recalculates_when_target_differs():
    result = restate_gross(Decimal("120"), Decimal("20"), Decimal("16"))
    assert result.status is AmountStatus.RESTATED
    assert result.amount == Decimal("116")


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity"])
def test_restate_does_not_fail_on_non_finite(raw):
    gross = Decimal(raw)
    result = restate_gross(gross, Decimal("20"), Decimal("16"))
    assert result.status is AmountStatus.NOT_FINITE


def test_quantize_money_rounds_half_up_to_kopecks():
    assert quantize_money(Decimal("116.005")) == Decimal("116.01")
    assert quantize_money(None) is None


def test_global_decimal_context_is_not_touched():
    before = getcontext().prec
    gross_to_net(Decimal("120"), Decimal("20"))
    assert getcontext().prec == before
