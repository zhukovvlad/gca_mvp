"""Unit-тесты для crud/supplier_exclusions.py (без БД — чистые функции через mock)."""
from unittest.mock import MagicMock


def _make_db(rows=None):
    """Минимальный мок SQLAlchemy Session."""
    db = MagicMock()
    q = MagicMock()
    db.query.return_value = q
    q.filter.return_value = q
    q.all.return_value = rows or []
    return db


class TestGetExcludedSupplierIds:
    def test_returns_empty_set_when_no_exclusions(self):
        from crud.supplier_exclusions import get_excluded_supplier_ids

        db = _make_db(rows=[])
        result = get_excluded_supplier_ids(db, project_id=1)
        assert result == set()

    def test_returns_set_of_supplier_ids(self):
        from crud.supplier_exclusions import get_excluded_supplier_ids

        row1 = MagicMock()
        row1.supplier_id = 5
        row2 = MagicMock()
        row2.supplier_id = 12
        db = _make_db(rows=[row1, row2])
        result = get_excluded_supplier_ids(db, project_id=1)
        assert result == {5, 12}


class TestSetSupplierExcluded:
    def test_adds_exclusion_when_excluded_true(self):
        from crud.supplier_exclusions import set_supplier_excluded

        db = MagicMock()

        set_supplier_excluded(db, project_id=1, supplier_id=7, excluded=True, reason="Аварийная закупка")

        db.execute.assert_called_once()
        db.commit.assert_called_once()

    def test_noop_when_adding_already_existing_exclusion(self):
        """ON CONFLICT DO NOTHING: always executes+commits; idempotency is DB-level."""
        from crud.supplier_exclusions import set_supplier_excluded

        db = MagicMock()

        set_supplier_excluded(db, project_id=1, supplier_id=7, excluded=True)
        set_supplier_excluded(db, project_id=1, supplier_id=7, excluded=True)

        assert db.execute.call_count == 2
        assert db.commit.call_count == 2

    def test_deletes_exclusion_when_excluded_false(self):
        from crud.supplier_exclusions import set_supplier_excluded

        db = MagicMock()
        filter_q = MagicMock()
        db.query.return_value.filter.return_value = filter_q

        set_supplier_excluded(db, project_id=1, supplier_id=7, excluded=False)

        filter_q.delete.assert_called_once()
        db.commit.assert_called_once()

    def test_noop_when_removing_nonexistent_exclusion(self):
        """Bulk DELETE with 0 rows affected is idempotent; commit is still called."""
        from crud.supplier_exclusions import set_supplier_excluded

        db = MagicMock()
        filter_q = MagicMock()
        filter_q.delete.return_value = 0  # 0 rows deleted
        db.query.return_value.filter.return_value = filter_q

        set_supplier_excluded(db, project_id=1, supplier_id=7, excluded=False)

        filter_q.delete.assert_called_once()
        db.commit.assert_called_once()
