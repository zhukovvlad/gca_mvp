"""Integration tests for corridor fallback hierarchy (Spec 2)."""
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from crud.calculations import compute_calculations
from crud.compensation_corridors import (
    delete_class_corridor,
    delete_type_corridor,
    get_corridor_map,
    resolve_corridor,
    set_class_corridor,
    set_type_corridor,
)
from models import MaterialType
from tests.factories import (
    DocumentFactory,
    InvoiceFactory,
    InvoiceItemFactory,
    MaterialClassFactory,
    ProjectFactory,
    ReferencePriceFactory,
)

D = Decimal


def _mt_id(db: Session, code: str) -> int:
    """Resolve material_type code → id (test helper)."""
    return db.query(MaterialType).filter(MaterialType.code == code).one().id


# --- CRUD tests ---

class TestTypeCorridorCrud:
    def test_set_type_creates_row(self, db_session, factories):
        project = ProjectFactory.create()
        mt_id = _mt_id(db_session, "concrete")
        set_type_corridor(db_session, project.id, mt_id, True, D("5.00"))
        by_class, by_type = get_corridor_map(db_session, project.id)
        assert mt_id in by_type
        assert by_type[mt_id].corridor_pct == D("5.00")
        assert by_type[mt_id].is_compensable is True

    def test_set_type_upsert_overwrites(self, db_session, factories):
        project = ProjectFactory.create()
        mt_id = _mt_id(db_session, "concrete")
        set_type_corridor(db_session, project.id, mt_id, True, D("5.00"))
        set_type_corridor(db_session, project.id, mt_id, True, D("7.00"))
        _, by_type = get_corridor_map(db_session, project.id)
        assert by_type[mt_id].corridor_pct == D("7.00")

    def test_set_type_not_compensable(self, db_session, factories):
        project = ProjectFactory.create()
        mt_id = _mt_id(db_session, "rebar")
        set_type_corridor(db_session, project.id, mt_id, False, None)
        _, by_type = get_corridor_map(db_session, project.id)
        assert by_type[mt_id].is_compensable is False
        assert by_type[mt_id].corridor_pct is None

    def test_delete_type_idempotent(self, db_session, factories):
        project = ProjectFactory.create()
        mt_id = _mt_id(db_session, "concrete")
        set_type_corridor(db_session, project.id, mt_id, True, D("5.00"))
        assert delete_type_corridor(db_session, project.id, mt_id) is True
        assert delete_type_corridor(db_session, project.id, mt_id) is False


class TestClassCorridorCrud:
    def test_set_class_creates_row(self, db_session, factories):
        project = ProjectFactory.create()
        mc = MaterialClassFactory.create(material_type_code="concrete", name="В25")
        set_class_corridor(db_session, project.id, mc.id, True, D("7.00"))
        by_class, _ = get_corridor_map(db_session, project.id)
        assert mc.id in by_class
        assert by_class[mc.id].corridor_pct == D("7.00")

    def test_delete_class_idempotent(self, db_session, factories):
        project = ProjectFactory.create()
        mc = MaterialClassFactory.create(material_type_code="concrete", name="В25")
        set_class_corridor(db_session, project.id, mc.id, True, D("7.00"))
        assert delete_class_corridor(db_session, project.id, mc.id) is True
        assert delete_class_corridor(db_session, project.id, mc.id) is False


# --- Fallback resolution with real DB ---

class TestFallbackResolution:
    def test_class_override_wins(self, db_session, factories):
        project = ProjectFactory.create()
        mc = MaterialClassFactory.create(material_type_code="concrete", name="В40")
        mt_id = _mt_id(db_session, "concrete")
        set_type_corridor(db_session, project.id, mt_id, True, D("5.00"))
        set_class_corridor(db_session, project.id, mc.id, True, D("7.00"))
        by_class, by_type = get_corridor_map(db_session, project.id)
        compensable, pct = resolve_corridor(by_class, by_type, mc.id, mc.material_type_id)
        assert compensable is True
        assert pct == D("7.00")

    def test_class_disables_over_type_enabled(self, db_session, factories):
        project = ProjectFactory.create()
        mc = MaterialClassFactory.create(material_type_code="concrete", name="В50")
        mt_id = _mt_id(db_session, "concrete")
        set_type_corridor(db_session, project.id, mt_id, True, D("5.00"))
        set_class_corridor(db_session, project.id, mc.id, False, None)
        by_class, by_type = get_corridor_map(db_session, project.id)
        compensable, _ = resolve_corridor(by_class, by_type, mc.id, mc.material_type_id)
        assert compensable is False

    def test_class_enables_over_type_disabled(self, db_session, factories):
        project = ProjectFactory.create()
        mc = MaterialClassFactory.create(material_type_code="rebar", name="d12")
        mt_id = _mt_id(db_session, "rebar")
        set_type_corridor(db_session, project.id, mt_id, False, None)
        set_class_corridor(db_session, project.id, mc.id, True, D("3.00"))
        by_class, by_type = get_corridor_map(db_session, project.id)
        compensable, pct = resolve_corridor(by_class, by_type, mc.id, mc.material_type_id)
        assert compensable is True
        assert pct == D("3.00")

    def test_no_rows_means_not_compensable(self, db_session, factories):
        project = ProjectFactory.create()
        mc = MaterialClassFactory.create(material_type_code="other", name="Песок")
        by_class, by_type = get_corridor_map(db_session, project.id)
        compensable, pct = resolve_corridor(by_class, by_type, mc.id, mc.material_type_id)
        assert compensable is None
        assert pct is None


# --- Calculation integration ---

def _make_invoice_with_item(db_session, project, mc, *, qty, unit_price, inv_date):
    doc = DocumentFactory.create(project=project)
    inv = InvoiceFactory.create(document=doc, date=inv_date, vat_rate=D("0"))
    InvoiceItemFactory.create(
        invoice=inv, material_class=mc, item_type="material",
        quantity=qty, unit_price=unit_price, amount=qty * unit_price, vat_amount=D("0"),
    )
    return inv


class TestCalculationIntegration:
    def test_type_level_corridor_applies_to_class(self, db_session, factories):
        """Type-level corridor 5% → class inherits → compensation calculated."""
        project = ProjectFactory.create()
        mc = MaterialClassFactory.create(material_type_code="concrete", name="В25")
        ReferencePriceFactory.create(
            project=project, material_class=mc, price=D("100"),
            period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
        )
        mt_id = _mt_id(db_session, "concrete")
        set_type_corridor(db_session, project.id, mt_id, True, D("5.00"))
        _make_invoice_with_item(db_session, project, mc, qty=D("2"), unit_price=D("110"), inv_date=date(2026, 3, 15))

        rows = compute_calculations(db_session, project.id, date(2026, 3, 1), date(2026, 3, 31))
        assert len(rows) == 1
        assert rows[0]["compensation_per_unit"] == D("5.00")
        assert rows[0]["compensation_amount"] == D("10.00")

    def test_not_compensable_returns_none(self, db_session, factories):
        """No corridor row → class not compensable → compensation fields are None."""
        project = ProjectFactory.create()
        mc = MaterialClassFactory.create(material_type_code="concrete", name="В25")
        ReferencePriceFactory.create(
            project=project, material_class=mc, price=D("100"),
            period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
        )
        _make_invoice_with_item(db_session, project, mc, qty=D("2"), unit_price=D("110"), inv_date=date(2026, 3, 15))

        rows = compute_calculations(db_session, project.id, date(2026, 3, 1), date(2026, 3, 31))
        assert len(rows) == 1
        assert rows[0]["compensation_per_unit"] is None
        assert rows[0]["compensation_amount"] is None
        assert rows[0]["corridor_pct"] is None

    def test_class_override_disables_compensation(self, db_session, factories):
        """Type enabled, class override disables → no compensation for that class."""
        project = ProjectFactory.create()
        mc = MaterialClassFactory.create(material_type_code="concrete", name="В40")
        ReferencePriceFactory.create(
            project=project, material_class=mc, price=D("100"),
            period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
        )
        mt_id = _mt_id(db_session, "concrete")
        set_type_corridor(db_session, project.id, mt_id, True, D("5.00"))
        set_class_corridor(db_session, project.id, mc.id, False, None)
        _make_invoice_with_item(db_session, project, mc, qty=D("2"), unit_price=D("110"), inv_date=date(2026, 3, 15))

        rows = compute_calculations(db_session, project.id, date(2026, 3, 1), date(2026, 3, 31))
        assert len(rows) == 1
        assert rows[0]["compensation_per_unit"] is None


# --- API endpoint tests ---

class TestCorridorApi:
    def test_get_corridors_empty(self, client: TestClient, db_session, factories):
        project = ProjectFactory.create()
        resp = client.get(f"/api/projects/{project.id}/corridors")
        assert resp.status_code == 200
        data = resp.json()
        assert "types" in data
        assert "classes" in data

    def test_put_type_corridor(self, client: TestClient, db_session, factories):
        project = ProjectFactory.create()
        resp = client.put(
            f"/api/projects/{project.id}/corridors/type/concrete",
            json={"is_compensable": True, "corridor_pct": 5.0},
        )
        assert resp.status_code == 200
        assert resp.json()["is_compensable"] is True

    def test_put_type_not_compensable(self, client: TestClient, db_session, factories):
        project = ProjectFactory.create()
        resp = client.put(
            f"/api/projects/{project.id}/corridors/type/rebar",
            json={"is_compensable": False},
        )
        assert resp.status_code == 200
        assert resp.json()["is_compensable"] is False

    def test_put_compensable_without_pct_returns_422(self, client: TestClient, db_session, factories):
        project = ProjectFactory.create()
        resp = client.put(
            f"/api/projects/{project.id}/corridors/type/concrete",
            json={"is_compensable": True},
        )
        assert resp.status_code == 422

    def test_delete_type_corridor(self, client: TestClient, db_session, factories):
        project = ProjectFactory.create()
        client.put(
            f"/api/projects/{project.id}/corridors/type/concrete",
            json={"is_compensable": True, "corridor_pct": 5.0},
        )
        resp = client.delete(f"/api/projects/{project.id}/corridors/type/concrete")
        assert resp.status_code == 204

    def test_put_class_corridor(self, client: TestClient, db_session, factories):
        project = ProjectFactory.create()
        mc = MaterialClassFactory.create(material_type_code="concrete", name="В40")
        resp = client.put(
            f"/api/projects/{project.id}/corridors/class/{mc.id}",
            json={"is_compensable": True, "corridor_pct": 7.0},
        )
        assert resp.status_code == 200

    def test_delete_class_corridor(self, client: TestClient, db_session, factories):
        project = ProjectFactory.create()
        mc = MaterialClassFactory.create(material_type_code="concrete", name="В40")
        client.put(
            f"/api/projects/{project.id}/corridors/class/{mc.id}",
            json={"is_compensable": True, "corridor_pct": 7.0},
        )
        resp = client.delete(f"/api/projects/{project.id}/corridors/class/{mc.id}")
        assert resp.status_code == 204

    def test_resolved_matrix_shows_inheritance(self, client: TestClient, db_session, factories):
        project = ProjectFactory.create()
        mc1 = MaterialClassFactory.create(material_type_code="concrete", name="В25")
        mc2 = MaterialClassFactory.create(material_type_code="concrete", name="В40")
        client.put(
            f"/api/projects/{project.id}/corridors/type/concrete",
            json={"is_compensable": True, "corridor_pct": 5.0},
        )
        client.put(
            f"/api/projects/{project.id}/corridors/class/{mc2.id}",
            json={"is_compensable": True, "corridor_pct": 7.0},
        )
        resp = client.get(f"/api/projects/{project.id}/corridors")
        data = resp.json()
        classes = {c["material_class_id"]: c for c in data["classes"]}
        # В25 inherits from type
        assert classes[mc1.id]["level"] == "type"
        assert classes[mc1.id]["corridor_pct"] == 5.0
        assert classes[mc1.id]["has_override"] is False
        # В40 has own override
        assert classes[mc2.id]["level"] == "class"
        assert classes[mc2.id]["corridor_pct"] == 7.0
        assert classes[mc2.id]["has_override"] is True
