"""Unit tests for dimension-aware delivery allocation — no DB."""
from decimal import Decimal
from types import SimpleNamespace

from crud.calculations import _aggregate_by_class, compute_shared_shares

D = Decimal


def _row(class_id, dimension, qty, amount):
    return SimpleNamespace(
        material_class_id=class_id, dimension=dimension,
        qty=D(qty), mat_total=D(amount),
    )


class TestComputeSharedShares:
    def test_mono_dimension_splits_by_quantity(self):
        rows = [_row(1, "volume", "30", "300"), _row(2, "volume", "10", "900")]
        shares = compute_shared_shares(rows)
        assert shares[1] == D("30") / D("40")
        assert shares[2] == D("10") / D("40")

    def test_mixed_dimension_splits_by_amount(self):
        rows = [_row(1, "volume", "50", "1000"), _row(2, "mass", "2", "3000")]
        shares = compute_shared_shares(rows)
        assert shares[1] == D("1000") / D("4000")
        assert shares[2] == D("3000") / D("4000")

    def test_mixed_dimension_zero_amount_no_split(self):
        rows = [_row(1, "volume", "50", "0"), _row(2, "mass", "2", "0")]
        shares = compute_shared_shares(rows)
        assert shares[1] == D("0")
        assert shares[2] == D("0")

    def test_partial_zero_amount_in_mixed(self):
        rows = [_row(1, "volume", "50", "0"), _row(2, "mass", "2", "4000")]
        shares = compute_shared_shares(rows)
        assert shares[1] == D("0")
        assert shares[2] == D("1")

    def test_mono_zero_qty_no_split(self):
        rows = [_row(1, "volume", "0", "0")]
        shares = compute_shared_shares(rows)
        assert shares[1] == D("0")

    def test_duplicate_class_id_accumulates_not_last_wins(self):
        # Same class appears twice (e.g. two normalized-unit rows) — basis must sum,
        # not drop one. Mixed dims here → amount basis; 1000 + 3000 share vs 4000 total.
        rows = [
            _row(1, "volume", "50", "1000"),
            _row(1, "mass", "2", "3000"),
            _row(2, "mass", "1", "4000"),
        ]
        shares = compute_shared_shares(rows)
        assert shares[1] == D("4000") / D("8000")  # 1000+3000 summed, not last-wins 3000
        assert shares[2] == D("4000") / D("8000")


def _agg_row(invoice_id, class_id, dimension, qty, mat_total, mat_vat, symbol="т", type_id=0):
    return SimpleNamespace(
        invoice_id=invoice_id, material_class_id=class_id, dimension=dimension,
        qty=D(qty), mat_total=D(mat_total), mat_vat=D(mat_vat), symbol=symbol,
        type_id=type_id,
    )


class TestAggregateByClassSharedOnce:
    def test_multi_dim_class_shared_accrued_once(self):
        # One class, two dimension rows in one invoice, delivery=500.
        # The only class's share is 1.0 → shared must accrue exactly 500, not 1000.
        rows = [
            _agg_row(1, 1, "mass", "2", "1000", "200"),
            _agg_row(1, 1, "length", "100", "3000", "600"),
        ]
        contrib = _aggregate_by_class(rows, {1: D("500")}, {})
        assert contrib[1]["shared_with_vat"] == D("500")     # once, not double
        assert contrib[1]["mat_with_vat"] == D("4800")       # (1000+200)+(3000+600), per-row sum
        assert contrib[1]["qty"] == D("102")                 # 2 + 100, per-row sum
        assert len(contrib[1]["dimensions"]) == 2            # flagged downstream

    def test_two_classes_shared_split_sums_to_total(self):
        # Mixed dims across classes → amount basis; full delivery distributed, no overflow.
        rows = [
            _agg_row(1, 1, "volume", "50", "1000", "200"),
            _agg_row(1, 2, "mass", "2", "3000", "600"),
        ]
        contrib = _aggregate_by_class(rows, {1: D("800")}, {})
        total_shared = contrib[1]["shared_with_vat"] + contrib[2]["shared_with_vat"]
        assert total_shared == D("800")
