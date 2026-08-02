"""Integration: compute_calculations with normalized units + dimension guard."""
from datetime import date
from decimal import Decimal

from crud.calculations import compute_calculations
from crud.units import load_alias_map, normalize_item
from models import InvoiceItem, UnitOfMeasure


def _add_item(db, invoice, material_class, raw_unit, quantity, unit_price):
    aliases = load_alias_map(db)
    q = Decimal(str(quantity))
    p = Decimal(str(unit_price))
    norm = normalize_item(raw_unit, q, p, aliases)
    item = InvoiceItem(
        invoice_id=invoice.id, raw_name="x", item_type="material",
        material_class_id=material_class.id, quantity=q, raw_unit=raw_unit,
        unit_price=p, amount=q * p, vat_amount=q * p * Decimal("0.2"),
        normalized_unit_id=norm.normalized_unit_id if norm else None,
        normalized_quantity=norm.normalized_quantity if norm else None,
        normalized_unit_price=norm.normalized_unit_price if norm else None,
    )
    db.add(item)
    db.commit()
    return item


class TestCalculationsWithUnits:
    def test_kg_aggregated_as_tons(self, db_session, factories):
        project = factories.ProjectFactory.create()
        mc = factories.MaterialClassFactory.create(material_type_code="rebar", name="d12")
        ton = db_session.query(UnitOfMeasure).filter_by(code="TON").one()
        factories.ReferencePriceFactory.create(
            project=project, material_class=mc, unit_id=ton.id, price=60000,
            period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
        )
        doc = factories.DocumentFactory.create(project=project)
        inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 10))
        # 2000 kg rebar @ 60 ₽/kg → 2 t @ 60000 ₽/t
        _add_item(db_session, inv, mc, raw_unit="кг", quantity=2000, unit_price=60)

        rows = compute_calculations(db_session, project.id, date(2026, 3, 1), date(2026, 3, 31))
        row = next(r for r in rows if r["material_class_id"] == mc.id)
        assert row["total_qty"] == Decimal("2.000")          # tons, not 2000
        assert row["dimension_mismatch"] is False
        assert row["unit_symbol"] == "т"

    def test_dimension_mismatch_blocks_deviation(self, db_session, factories):
        project = factories.ProjectFactory.create()
        mc = factories.MaterialClassFactory.create(material_type_code="rebar", name="d10")
        # ref price in TON (mass) but item normalized to M (length, "пог.м")
        ton = db_session.query(UnitOfMeasure).filter_by(code="TON").one()
        factories.ReferencePriceFactory.create(
            project=project, material_class=mc, unit_id=ton.id, price=60000,
            period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
        )
        doc = factories.DocumentFactory.create(project=project)
        inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 10))
        _add_item(db_session, inv, mc, raw_unit="пог.м", quantity=100, unit_price=500)

        rows = compute_calculations(db_session, project.id, date(2026, 3, 1), date(2026, 3, 31))
        row = next(r for r in rows if r["material_class_id"] == mc.id)
        assert row["dimension_mismatch"] is True
        assert row["deviation_pct"] is None
        assert row["compensation_amount"] is None

    def test_unnormalized_rows_excluded(self, db_session, factories):
        project = factories.ProjectFactory.create()
        mc = factories.MaterialClassFactory.create(material_type_code="concrete", name="В25")
        doc = factories.DocumentFactory.create(project=project)
        inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 10))
        _add_item(db_session, inv, mc, raw_unit="бухта", quantity=5, unit_price=1000)  # unknown → NULL

        rows = compute_calculations(db_session, project.id, date(2026, 3, 1), date(2026, 3, 31))
        assert all(r["material_class_id"] != mc.id for r in rows)

    def test_intra_class_dimension_mix_flagged(self, db_session, factories):
        # One class with two normalized dimensions in the same invoice (т + пог.м) → flagged,
        # not silently summed (mass + length).
        project = factories.ProjectFactory.create()
        mc = factories.MaterialClassFactory.create(material_type_code="rebar", name="d12")
        ton = db_session.query(UnitOfMeasure).filter_by(code="TON").one()
        factories.ReferencePriceFactory.create(
            project=project, material_class=mc, unit_id=ton.id, price=60000,
            period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
        )
        doc = factories.DocumentFactory.create(project=project)
        inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 10))
        _add_item(db_session, inv, mc, raw_unit="т", quantity=2, unit_price=60000)
        _add_item(db_session, inv, mc, raw_unit="пог.м", quantity=100, unit_price=500)

        rows = compute_calculations(db_session, project.id, date(2026, 3, 1), date(2026, 3, 31))
        row = next(r for r in rows if r["material_class_id"] == mc.id)
        assert row["dimension_mismatch"] is True
        assert row["deviation_pct"] is None
        assert row["compensation_amount"] is None
