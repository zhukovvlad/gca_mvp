"""Unit-тесты для bulk_delete_invoices (DELETE /api/invoices/bulk)."""
from unittest.mock import MagicMock, call

from models import Document, Invoice
from routers.invoices import BulkDeleteRequest, bulk_delete_invoices


def _make_invoice(id_: int, verified: bool, document_id: int | None = None) -> MagicMock:
    """СФ-мок; document_id по умолчанию = id_ (не важно для теста, лишь бы был int)."""
    inv = MagicMock()
    inv.id = id_
    inv.verified = verified
    inv.document_id = document_id if document_id is not None else id_
    return inv


def _make_db(invoices: list) -> MagicMock:
    """Мок Session с 3 разными query()-путями, которые появились с блокировкой
    документов (S0-8): document_id-lookup, FOR UPDATE документа (всегда не
    processing в этих тестах — сама блокировка проверяется интеграционными
    тестами на реальной БД), и re-fetch СФ под блокировкой перед удалением."""
    db = MagicMock()

    def query_side_effect(entity):
        """Возвращает отдельную цепочку query().filter()... в зависимости от entity."""
        chain = MagicMock()
        if entity is Invoice.document_id:
            rows = [MagicMock(document_id=inv.document_id) for inv in invoices]
            chain.filter.return_value.all.return_value = rows
        elif entity is Document:
            doc = MagicMock(status="parsed")
            chain.filter.return_value.with_for_update.return_value.first.return_value = doc
        else:  # entity is Invoice — финальный re-fetch под блокировкой
            chain.filter.return_value.all.return_value = invoices
        return chain

    db.query.side_effect = query_side_effect
    return db


class TestBulkDeleteInvoices:
    def test_empty_ids_returns_zero(self):
        db = _make_db([])
        result = bulk_delete_invoices(BulkDeleteRequest(ids=[]), db)
        assert result == {"deleted": 0, "skipped": []}
        db.delete.assert_not_called()
        db.commit.assert_not_called()

    def test_deletes_unverified(self):
        inv = _make_invoice(1, verified=False)
        db = _make_db([inv])
        result = bulk_delete_invoices(BulkDeleteRequest(ids=[1]), db)
        assert result == {"deleted": 1, "skipped": []}
        db.delete.assert_called_once_with(inv)
        db.commit.assert_called_once()

    def test_skips_verified(self):
        inv = _make_invoice(2, verified=True)
        db = _make_db([inv])
        result = bulk_delete_invoices(BulkDeleteRequest(ids=[2]), db)
        assert result == {"deleted": 0, "skipped": [2]}
        db.delete.assert_not_called()
        db.commit.assert_called_once()

    def test_mixed_deletes_unverified_skips_verified(self):
        inv1 = _make_invoice(1, verified=False)
        inv2 = _make_invoice(2, verified=True)
        inv3 = _make_invoice(3, verified=False)
        db = _make_db([inv1, inv2, inv3])
        result = bulk_delete_invoices(BulkDeleteRequest(ids=[1, 2, 3]), db)
        assert result["deleted"] == 2
        assert result["skipped"] == [2]
        assert db.delete.call_count == 2
        db.delete.assert_has_calls([call(inv1), call(inv3)], any_order=False)

    def test_all_verified_deletes_nothing(self):
        invoices = [_make_invoice(i, verified=True) for i in range(1, 4)]
        db = _make_db(invoices)
        result = bulk_delete_invoices(BulkDeleteRequest(ids=[1, 2, 3]), db)
        assert result["deleted"] == 0
        assert set(result["skipped"]) == {1, 2, 3}
        db.delete.assert_not_called()
