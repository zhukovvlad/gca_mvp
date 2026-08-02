"""Unit-тесты для _doc_has_issues и _avg_confidence (routers/invoices.py)."""
from dataclasses import dataclass, field
from decimal import Decimal

from routers.invoices import _avg_confidence, _doc_has_issues


@dataclass
class _FakeItem:
    quantity: float
    raw_name: str | None = ""
    item_type: str = "material"
    normalized_unit_id: int | None = 1  # non-None → no "unnormalized unit" flag
    # invariant_holds uses Decimal arithmetic — supply Decimal-compatible values
    # so that the check passes for a valid item (qty * unit_price ≈ amount).
    unit_price: Decimal = Decimal("100")
    amount: Decimal = Decimal("500")  # consistent with quantity=5, unit_price=100


@dataclass
class _FakeInvoice:
    items: list[_FakeItem] = field(default_factory=list)
    ai_confidence: float | None = None


@dataclass
class _FakeDoc:
    invoices: list[_FakeInvoice] = field(default_factory=list)


class TestDocHasIssues:
    def test_no_issues_when_items_valid(self):
        doc = _FakeDoc(invoices=[
            _FakeInvoice(items=[_FakeItem(quantity=5, raw_name="Бетон")]),
        ])
        assert _doc_has_issues(doc) is False

    def test_issues_when_invoice_has_no_items(self):
        doc = _FakeDoc(invoices=[_FakeInvoice(items=[])])
        assert _doc_has_issues(doc) is True

    def test_issues_when_quantity_zero(self):
        doc = _FakeDoc(invoices=[
            _FakeInvoice(items=[_FakeItem(quantity=0, raw_name="X")]),
        ])
        assert _doc_has_issues(doc) is True

    def test_issues_when_raw_name_blank(self):
        doc = _FakeDoc(invoices=[
            _FakeInvoice(items=[_FakeItem(quantity=5, raw_name="   ")]),
        ])
        assert _doc_has_issues(doc) is True

    def test_issues_when_raw_name_none(self):
        doc = _FakeDoc(invoices=[
            _FakeInvoice(items=[_FakeItem(quantity=5, raw_name=None)]),
        ])
        assert _doc_has_issues(doc) is True


class TestAvgConfidence:
    def test_returns_none_when_no_confidence_set(self):
        doc = _FakeDoc(invoices=[
            _FakeInvoice(ai_confidence=None),
            _FakeInvoice(ai_confidence=None),
        ])
        assert _avg_confidence(doc) is None

    def test_average_of_multiple(self):
        doc = _FakeDoc(invoices=[
            _FakeInvoice(ai_confidence=0.9),
            _FakeInvoice(ai_confidence=0.7),
        ])
        assert _avg_confidence(doc) == 0.8

    def test_skips_none_invoices(self):
        doc = _FakeDoc(invoices=[
            _FakeInvoice(ai_confidence=0.9),
            _FakeInvoice(ai_confidence=None),
            _FakeInvoice(ai_confidence=0.5),
        ])
        assert _avg_confidence(doc) == 0.7

    def test_rounds_to_two_decimals(self):
        doc = _FakeDoc(invoices=[
            _FakeInvoice(ai_confidence=0.333),
            _FakeInvoice(ai_confidence=0.666),
        ])
        assert _avg_confidence(doc) == 0.5
