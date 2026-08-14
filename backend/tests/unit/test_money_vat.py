from decimal import Decimal, getcontext

import pytest

from money.vat import (
    NET_RECONCILIATION_TOLERANCE,
    AmountStatus,
    NetStatus,
    ProposalNetCheck,
    check_proposal_net,
    effective_display_rate,
    fold_net_reconciliation,
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
    assert result.amount == gross
    assert result.amount.as_tuple().exponent == gross.as_tuple().exponent


def test_restate_is_identity_when_override_equals_declared_rate():
    """Ветка тождества срабатывает всегда при равенстве цели и базы.

    Измерение «перекрыта база или нет» на этом слое НЕ наблюдаемо вовсе —
    restate_gross получает уже эффективную базу, и различие проверяется выше,
    на слое API. Тест закрепляет ветку тождества, не проверку перекрытия.
    """
    gross = Decimal("100.50")
    result = restate_gross(gross, Decimal("20"), Decimal("20"))
    assert result.status is AmountStatus.ORIGINAL
    assert result.amount == gross
    assert result.amount.as_tuple().exponent == gross.as_tuple().exponent


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
    if gross.is_nan():
        assert result.amount.is_nan()
    else:
        assert result.amount == gross
        assert result.amount.is_infinite()


def test_restate_not_finite_when_target_equals_base():
    """NOT_FINITE перевешивает ORIGINAL: не-конечная сумма негодна при любой ставке."""
    gross = Decimal("Infinity")
    result = restate_gross(gross, Decimal("20"), Decimal("20"))
    assert result.status is AmountStatus.NOT_FINITE
    assert result.amount == gross
    assert result.amount.is_infinite()


def test_restate_not_finite_when_base_unknown():
    """NOT_FINITE перевешивает UNKNOWN_BASE: не-конечная сумма негодна без базы."""
    gross = Decimal("NaN")
    result = restate_gross(gross, None, Decimal("16"))
    assert result.status is AmountStatus.NOT_FINITE
    assert result.amount.is_nan()


def test_restate_keeps_none_value_when_restating_different_rates():
    """При None сумме и различных ставках статус ORIGINAL: нет арифметики."""
    result = restate_gross(None, Decimal("20"), Decimal("16"))
    assert result.status is AmountStatus.ORIGINAL
    assert result.amount is None


def test_quantize_money_rounds_half_up_to_kopecks():
    assert quantize_money(Decimal("116.005")) == Decimal("116.01")
    assert quantize_money(None) is None


def test_quantize_money_returns_infinity_unchanged():
    """quantize_money не округляет и не роняет не-конечные значения."""
    inf = Decimal("Infinity")
    result = quantize_money(inf)
    assert result == inf
    assert result.is_infinite()

    neg_inf = Decimal("-Infinity")
    result = quantize_money(neg_inf)
    assert result == neg_inf
    assert result.is_infinite()


def test_quantize_money_returns_nan_unchanged():
    """quantize_money не округляет и не роняет NaN."""
    nan = Decimal("NaN")
    result = quantize_money(nan)
    assert result.is_nan()


def test_global_decimal_context_is_not_touched():
    before_ctx = getcontext()
    before_prec = before_ctx.prec
    before_traps = before_ctx.traps.copy()
    before_rounding = before_ctx.rounding

    gross_to_net(Decimal("120"), Decimal("20"))

    after_ctx = getcontext()
    assert after_ctx.prec == before_prec
    assert after_ctx.traps == before_traps
    assert after_ctx.rounding == before_rounding


def _check(pid, status, delta=None):
    return ProposalNetCheck(proposal_id=pid, status=status, delta=delta)


def test_check_agrees_within_tolerance():
    result = check_proposal_net(1, Decimal("120.00"), Decimal("100.00"), Decimal("20"))
    assert result.status is NetStatus.OK
    assert result.delta == Decimal("0")


def test_check_reports_mismatch_beyond_tolerance():
    result = check_proposal_net(1, Decimal("120.00"), Decimal("90.00"), Decimal("20"))
    assert result.status is NetStatus.MISMATCH
    assert result.delta == Decimal("10")


def test_check_without_base_is_unknown_base():
    result = check_proposal_net(1, Decimal("120.00"), Decimal("100.00"), None)
    assert result.status is NetStatus.UNKNOWN_BASE
    assert result.delta is None


@pytest.mark.parametrize(
    ("gross", "file_net"),
    [(None, Decimal("100")), (Decimal("120"), None), (Decimal("NaN"), Decimal("100"))],
)
def test_check_without_both_operands_is_not_applicable(gross, file_net):
    result = check_proposal_net(1, gross, file_net, Decimal("20"))
    assert result.status is NetStatus.NOT_APPLICABLE
    assert result.delta is None


def test_fold_prefers_mismatch_over_unknown_base():
    """Приоритет идёт от противоречия к незнанию: расхождение — факт,
    незнание — его отсутствие."""
    folded = fold_net_reconciliation(
        [_check(1, NetStatus.UNKNOWN_BASE), _check(2, NetStatus.MISMATCH, Decimal("10"))]
    )
    assert folded.status is NetStatus.MISMATCH
    assert folded.mismatched_proposal_ids == [2]


def test_fold_reports_unknown_base_when_no_mismatch():
    folded = fold_net_reconciliation(
        [_check(1, NetStatus.UNKNOWN_BASE), _check(2, NetStatus.OK, Decimal("0"))]
    )
    assert folded.status is NetStatus.UNKNOWN_BASE
    assert folded.mismatched_proposal_ids == []


def test_fold_is_not_applicable_without_any_comparable():
    folded = fold_net_reconciliation([_check(1, NetStatus.NOT_APPLICABLE)])
    assert folded.status is NetStatus.NOT_APPLICABLE
    assert folded.delta is None


def test_fold_delta_ignores_non_comparable_proposals():
    """Добавление непроверяемого предложения не имеет права двигать сумму."""
    base = [_check(1, NetStatus.OK, Decimal("0.30"))]
    with_extra = base + [_check(2, NetStatus.UNKNOWN_BASE), _check(3, NetStatus.NOT_APPLICABLE)]
    assert fold_net_reconciliation(base).delta == fold_net_reconciliation(with_extra).delta


def test_fold_delta_is_null_without_comparable_proposals():
    assert fold_net_reconciliation([_check(1, NetStatus.UNKNOWN_BASE)]).delta is None


# ---------------------------------------------------------------------------
#  effective_display_rate (задача 8, спека §5.1)
# ---------------------------------------------------------------------------

def test_effective_rate_prefers_target_over_everything_else():
    """Цель побеждает даже когда база и заявленные ставки говорят другое."""
    rate = effective_display_rate(Decimal("16"), Decimal("20"), [Decimal("10"), Decimal("10")])
    assert rate == Decimal("16")


def test_effective_rate_falls_back_to_base_override_without_target():
    rate = effective_display_rate(None, Decimal("20"), [Decimal("10"), Decimal("12")])
    assert rate == Decimal("20")


def test_effective_rate_falls_back_to_unanimous_declared_rate():
    """Ни цели, ни перекрытой базы нет — единогласная заявленная ставка предложений."""
    rate = effective_display_rate(None, None, [Decimal("20"), Decimal("20")])
    assert rate == Decimal("20")


def test_effective_rate_is_null_on_disagreement():
    """Разногласие заявленных ставок — оговорённая граница §5.1: показать
    «в какой-то из» ставок нельзя."""
    rate = effective_display_rate(None, None, [Decimal("20"), Decimal("12")])
    assert rate is None


def test_effective_rate_is_null_when_any_declared_rate_is_unknown():
    rate = effective_display_rate(None, None, [Decimal("20"), None])
    assert rate is None


def test_effective_rate_is_null_without_any_proposal():
    rate = effective_display_rate(None, None, [])
    assert rate is None


# ---------------------------------------------------------------------------
#  Три отложенных теста задачи 2 (приложение оркестратора п.7, задача 8 доводит)
# ---------------------------------------------------------------------------

def test_check_agrees_at_the_exact_tolerance_boundary():
    """|delta| РОВНО NET_RECONCILIATION_TOLERANCE (3.5) — сравнение нестрогое,
    статус OK. Парный тест ниже проверяет, что чуть выше границы уже MISMATCH —
    та же конвенция, что у `parser/vat_rate.py` (`VAT_RATE_TOLERANCE`, пара
    «ровно на границе» / «чуть за границей»), которой у этого допуска не было.

    Ставка явная и ненулевая (20 %), а не фон задачи по умолчанию.
    """
    result = check_proposal_net(1, Decimal("1200.00"), Decimal("996.50"), Decimal("20"))
    assert result.status is NetStatus.OK
    assert result.delta == NET_RECONCILIATION_TOLERANCE


def test_check_mismatches_just_past_the_tolerance_boundary():
    result = check_proposal_net(1, Decimal("1200.00"), Decimal("996.49"), Decimal("20"))
    assert result.status is NetStatus.MISMATCH
    assert result.delta == Decimal("3.51")


def test_fold_delta_sums_signed_deltas_of_different_signs():
    """Знак дельты при нескольких сравнимых: mismatch +10 и ok −3 дают +7, а
    не |10|+|-3|=13 и не среднее. Обе ставки явные и ненулевые (20 %)."""
    mismatch = check_proposal_net(1, Decimal("1200.00"), Decimal("990.00"), Decimal("20"))
    ok = check_proposal_net(2, Decimal("600.00"), Decimal("503.00"), Decimal("20"))
    assert mismatch.status is NetStatus.MISMATCH
    assert mismatch.delta == Decimal("10")
    assert ok.status is NetStatus.OK
    assert ok.delta == Decimal("-3")

    folded = fold_net_reconciliation([mismatch, ok])
    assert folded.delta == Decimal("7")


def test_fold_empty_input_is_not_applicable():
    folded = fold_net_reconciliation([])
    assert folded.status is NetStatus.NOT_APPLICABLE
    assert folded.delta is None
    assert folded.mismatched_proposal_ids == []
