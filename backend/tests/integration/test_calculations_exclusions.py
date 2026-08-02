"""Интеграционные тесты: excluded_supplier_ids фильтрует инвойсы из расчётов."""
from datetime import date


def test_excluded_supplier_removed_from_calculations(db_session, factories):
    """Поставщик A исключён — его счета не участвуют в avg_price."""
    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create(calc_role="base")

    supplier_a = factories.SupplierFactory.create()
    supplier_b = factories.SupplierFactory.create()

    doc_a = factories.DocumentFactory.create(project=project)
    inv_a = factories.InvoiceFactory.create(
        document=doc_a, supplier_id=supplier_a.id, date=date(2026, 3, 10), vat_rate=20.0
    )
    factories.InvoiceItemFactory.create(
        invoice=inv_a,
        material_class=mc,
        item_type="material",
        quantity=10.0,
        unit_price=9000.0,
        amount=90000.0,
        vat_amount=18000.0,
    )

    doc_b = factories.DocumentFactory.create(project=project)
    inv_b = factories.InvoiceFactory.create(
        document=doc_b, supplier_id=supplier_b.id, date=date(2026, 3, 20), vat_rate=20.0
    )
    factories.InvoiceItemFactory.create(
        invoice=inv_b,
        material_class=mc,
        item_type="material",
        quantity=10.0,
        unit_price=7000.0,
        amount=70000.0,
        vat_amount=14000.0,
    )

    from crud.calculations import compute_calculations

    # Без исключений: avg = (90000+18000 + 70000+14000) / 20 = 9600
    rows_all = compute_calculations(db_session, project.id)
    assert len(rows_all) == 1
    assert rows_all[0]["avg_price"] == 9600.0

    # Исключаем supplier_a: avg = (70000+14000) / 10 = 8400
    rows_excl = compute_calculations(
        db_session, project.id, excluded_supplier_ids={supplier_a.id}
    )
    assert len(rows_excl) == 1
    assert rows_excl[0]["avg_price"] == 8400.0


def test_null_supplier_id_not_affected_by_exclusion(db_session, factories):
    """Инвойс без supplier_id всегда участвует в расчётах, даже если передан excluded_supplier_ids."""
    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create(calc_role="base")

    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(
        document=doc, supplier_id=None, date=date(2026, 3, 15), vat_rate=20.0
    )
    factories.InvoiceItemFactory.create(
        invoice=inv,
        material_class=mc,
        item_type="material",
        quantity=5.0,
        unit_price=8000.0,
        amount=40000.0,
        vat_amount=8000.0,
    )

    from crud.calculations import compute_calculations

    # excluded_supplier_ids={999} не должен убрать инвойс без supplier_id
    rows = compute_calculations(db_session, project.id, excluded_supplier_ids={999})
    assert len(rows) == 1
    assert rows[0]["total_qty"] == 5.0
