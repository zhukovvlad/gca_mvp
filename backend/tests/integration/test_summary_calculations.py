"""Интеграционные тесты: /summary отдаёт calc-rows и они совпадают с /calculations."""
from datetime import date


def _sort(rows: list[dict]) -> list[dict]:
    """Сортировка обеих сторон — порядок compute_calculations не детерминирован."""
    return sorted(rows, key=lambda r: (r["period_start"], r["material_class_id"]))


def test_summary_includes_calculations(client, factories):
    """Проект с данными: summary['calculations'] непуст и совпадает с /calculations."""
    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create(calc_role="base", name="В25")
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 10), vat_rate=20.0)
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=mc, item_type="material",
        quantity=10.0, unit_price=9000.0, amount=90000.0, vat_amount=18000.0,
    )

    summary = client.get(f"/api/dashboard/summary?project_id={project.id}").json()
    calc = client.get(f"/api/dashboard/calculations?project_id={project.id}").json()

    assert len(summary["calculations"]) > 0
    assert _sort(summary["calculations"]) == _sort(calc)


def test_summary_calculations_empty_for_project_without_invoices(client, factories):
    """Пустой проект: calculations == []."""
    project = factories.ProjectFactory.create()
    summary = client.get(f"/api/dashboard/summary?project_id={project.id}").json()
    assert summary["calculations"] == []


def test_summary_calculations_equals_endpoint_with_excluded_edge_supplier(client, db_session, factories):
    """Исключённый поставщик держит самую раннюю дату → границы периода summary
    (нефильтрованные) и /calculations (с исключениями) различаются, но выход идентичен
    (пустые месяцы пропускаются через continue)."""
    from models import ProjectSupplierExclusion

    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create(calc_role="base", name="В25")

    excluded = factories.SupplierFactory.create()
    kept = factories.SupplierFactory.create()

    # Исключённый поставщик — самый ранний счёт (край диапазона).
    doc_e = factories.DocumentFactory.create(project=project)
    inv_e = factories.InvoiceFactory.create(
        document=doc_e, supplier_id=excluded.id, date=date(2026, 1, 5), vat_rate=20.0
    )
    factories.InvoiceItemFactory.create(
        invoice=inv_e, material_class=mc, item_type="material",
        quantity=10.0, unit_price=5000.0, amount=50000.0, vat_amount=10000.0,
    )
    # Оставленный поставщик — позже.
    doc_k = factories.DocumentFactory.create(project=project)
    inv_k = factories.InvoiceFactory.create(
        document=doc_k, supplier_id=kept.id, date=date(2026, 3, 10), vat_rate=20.0
    )
    factories.InvoiceItemFactory.create(
        invoice=inv_k, material_class=mc, item_type="material",
        quantity=10.0, unit_price=9000.0, amount=90000.0, vat_amount=18000.0,
    )

    db_session.add(ProjectSupplierExclusion(project_id=project.id, supplier_id=excluded.id))
    db_session.commit()

    summary = client.get(f"/api/dashboard/summary?project_id={project.id}").json()
    calc = client.get(f"/api/dashboard/calculations?project_id={project.id}").json()

    assert _sort(summary["calculations"]) == _sort(calc)
    # Санити: остался только оставленный поставщик (январь исключён и пропущен).
    assert all(r["period_start"] >= "2026-03-01" for r in summary["calculations"])
