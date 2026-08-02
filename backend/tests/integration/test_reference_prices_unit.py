from models import UnitOfMeasure


def _unit_id(db, code):
    return db.query(UnitOfMeasure).filter_by(code=code).one().id


class TestReferencePriceUnit:
    def test_create_with_base_unit_ok(self, client, factories, db_session):
        project = factories.ProjectFactory.create()
        mc = factories.MaterialClassFactory.create(material_type_code="concrete", name="В25")
        resp = client.post("/api/reference-prices", json={
            "project_id": project.id, "material_class_id": mc.id,
            "unit_id": _unit_id(db_session, "M3"),
            "price": 8000, "period_start": "2026-01-01", "period_end": "2026-12-31",
        })
        assert resp.status_code == 200

    def test_derived_unit_rejected(self, client, factories, db_session):
        project = factories.ProjectFactory.create()
        mc = factories.MaterialClassFactory.create(material_type_code="rebar", name="d12")
        resp = client.post("/api/reference-prices", json={
            "project_id": project.id, "material_class_id": mc.id,
            "unit_id": _unit_id(db_session, "KG"),  # derived, not base
            "price": 60, "period_start": "2026-01-01", "period_end": "2026-12-31",
        })
        assert resp.status_code == 422

    def test_wrong_dimension_rejected(self, client, factories, db_session):
        project = factories.ProjectFactory.create()
        mc = factories.MaterialClassFactory.create(material_type_code="concrete", name="В30")
        resp = client.post("/api/reference-prices", json={
            "project_id": project.id, "material_class_id": mc.id,
            "unit_id": _unit_id(db_session, "TON"),  # mass for a concrete (volume) class
            "price": 8000, "period_start": "2026-01-01", "period_end": "2026-12-31",
        })
        assert resp.status_code == 422

    def test_other_type_class_rejected(self, client, factories, db_session):
        """§5.3: классам типа other базовая цена не назначается (направления не образует)."""
        project = factories.ProjectFactory.create()
        mc = factories.MaterialClassFactory.create(material_type_code="other", name="Песок")
        resp = client.post("/api/reference-prices", json={
            "project_id": project.id, "material_class_id": mc.id,
            "unit_id": _unit_id(db_session, "TON"),
            "price": 500, "period_start": "2026-01-01", "period_end": "2026-12-31",
        })
        assert resp.status_code == 422
        assert "Прочее" in resp.json()["detail"]
