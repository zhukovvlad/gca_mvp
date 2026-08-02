from datetime import date


def test_summary_empty(client, factories):
    """Пустой объект: нули в агрегатах и None в датах/компенсации за период."""
    project = factories.ProjectFactory.create()
    response = client.get(f"/api/dashboard/summary?project_id={project.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["doc_count"] == 0
    assert body["invoice_count"] == 0
    assert body["total_amount"] == 0
    assert body["total_qty"] == 0
    assert body["first_invoice_date"] is None
    assert body["last_invoice_date"] is None
    assert body["full_compensation_amount"] is None


def test_summary_aggregates_materials(client, factories):
    project = factories.ProjectFactory.create()
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc)
    factories.InvoiceItemFactory.create(invoice=inv, item_type="material", quantity=5, amount=40000)
    factories.InvoiceItemFactory.create(invoice=inv, item_type="delivery", quantity=1, amount=2000)

    response = client.get(f"/api/dashboard/summary?project_id={project.id}")
    body = response.json()
    # total_amount = all item types with VAT (material: 40000+8000=48000, delivery: 2000+400=2400)
    assert body["total_amount"] == 50400.0
    assert body["total_qty"] == 5.0
    assert body["invoice_count"] == 1


def test_summary_with_corridor_computes_compensation(client, factories):
    """full_compensation_amount считается, когда у класса включён коридор компенсации."""
    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create()
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 15))
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=mc, item_type="material",
        quantity=10, unit_price=9000, amount=90000,
    )
    # avg_price includes VAT: (90000+18000)/10=10800; ref=8000.
    factories.ReferencePriceFactory.create(
        project=project, material_class=mc,
        price=8000.0,
        period_start=date(2026, 1, 1), period_end=date(2026, 12, 31),
    )
    # corridor_pct=0 → любое отклонение компенсируется: comp=(10800-8000)*10=28000
    client.put(
        f"/api/projects/{project.id}/corridors/class/{mc.id}",
        json={"is_compensable": True, "corridor_pct": 0},
    )

    response = client.get(f"/api/dashboard/summary?project_id={project.id}")
    body = response.json()
    assert body["first_invoice_date"] == "2026-03-15"
    assert body["last_invoice_date"] == "2026-03-15"
    assert body["full_compensation_amount"] == 28000.0


def test_calculations_endpoint_returns_live_data(client, factories):
    """GET /calculations возвращает live-данные без предварительного POST."""
    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create()
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 1))
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=mc, item_type="material",
        quantity=10, unit_price=8000, amount=80000,
    )

    response = client.get(f"/api/dashboard/calculations?project_id={project.id}")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["total_qty"] == 10.0
    assert rows[0]["avg_price"] == 9600.0  # (80000 + 16000 vat) / 10
    assert "unit_symbol" in rows[0]
    assert "dimension_mismatch" in rows[0]
    assert rows[0]["period_start"] == "2026-03-01"


def test_calculations_endpoint_period_filter(client, factories):
    """GET /calculations с period_start/period_end возвращает только нужные месяцы."""
    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create()
    doc = factories.DocumentFactory.create(project=project)

    inv_jan = factories.InvoiceFactory.create(document=doc, date=date(2026, 1, 15))
    factories.InvoiceItemFactory.create(
        invoice=inv_jan, material_class=mc, item_type="material",
        quantity=5, unit_price=9000, amount=45000,
    )
    inv_mar = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 15))
    factories.InvoiceItemFactory.create(
        invoice=inv_mar, material_class=mc, item_type="material",
        quantity=10, unit_price=8000, amount=80000,
    )

    response = client.get(
        f"/api/dashboard/calculations?project_id={project.id}"
        f"&period_start=2026-03-01&period_end=2026-03-31"
    )
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["total_qty"] == 10.0


def test_dashboard_invoices_includes_verified_fields(client, factories):
    project = factories.ProjectFactory.create()
    doc = factories.DocumentFactory.create(project=project)
    factories.InvoiceFactory.create(document=doc)

    response = client.get(f"/api/dashboard/invoices?project_id={project.id}")
    assert response.status_code == 200
    inv = response.json()[0]
    assert "verified" in inv
    assert "verified_at" in inv
    assert "supplier_inn" in inv
    assert inv["verified"] is False
    assert inv["verified_at"] is None
    assert inv["supplier_inn"] == "0000000000"


def test_dashboard_invoices_reflects_verification(client, factories):
    project = factories.ProjectFactory.create()
    doc = factories.DocumentFactory.create(project=project)
    invoice = factories.InvoiceFactory.create(document=doc)

    client.post(f"/api/invoices/{invoice.id}/verify")

    response = client.get(f"/api/dashboard/invoices?project_id={project.id}")
    assert response.status_code == 200
    inv = response.json()[0]
    assert inv["verified"] is True
    assert inv["verified_at"] is not None


def test_dashboard_invoices_reflects_unverification(client, factories):
    project = factories.ProjectFactory.create()
    doc = factories.DocumentFactory.create(project=project)
    invoice = factories.InvoiceFactory.create(document=doc)

    verify_response = client.post(f"/api/invoices/{invoice.id}/verify")
    assert verify_response.status_code == 200

    response = client.get(f"/api/dashboard/invoices?project_id={project.id}")
    assert response.status_code == 200
    inv = response.json()[0]
    assert inv["verified"] is True
    assert inv["verified_at"] is not None

    unverify_response = client.post(f"/api/invoices/{invoice.id}/unverify")
    assert unverify_response.status_code == 200

    response = client.get(f"/api/dashboard/invoices?project_id={project.id}")
    assert response.status_code == 200
    inv = response.json()[0]
    assert inv["verified"] is False
    assert inv["verified_at"] is None


# ── /monthly-summary ─────────────────────────────────────────────────────────

def test_monthly_summary_empty(client, factories):
    project = factories.ProjectFactory.create()
    response = client.get(f"/api/dashboard/monthly-summary?project_id={project.id}")
    assert response.status_code == 200
    assert response.json() == []


def test_monthly_summary_aggregates_by_month(client, factories):
    project = factories.ProjectFactory.create()
    doc = factories.DocumentFactory.create(project=project)

    inv_jan = factories.InvoiceFactory.create(document=doc, date=date(2026, 1, 10))
    factories.InvoiceItemFactory.create(
        invoice=inv_jan, item_type="material", quantity=5.0, amount=40000.0,
    )
    # delivery входит и в оборот, и в total_qty (агрегаты monthly-summary без фильтра по типу)
    factories.InvoiceItemFactory.create(
        invoice=inv_jan, item_type="delivery", quantity=1.0, amount=3000.0,
    )

    inv_mar = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 5))
    factories.InvoiceItemFactory.create(
        invoice=inv_mar, item_type="material", quantity=10.0, amount=80000.0,
    )

    response = client.get(f"/api/dashboard/monthly-summary?project_id={project.id}")
    assert response.status_code == 200

    rows = {(r["year"], r["month"]): r for r in response.json()}
    # Должно быть ровно два месяца (февраль восстанавливает фронт, не бэк)
    assert set(rows.keys()) == {(2026, 1), (2026, 3)}

    jan = rows[(2026, 1)]
    # оборот = все позиции с НДС: material (40000+8000) + delivery (3000+600) = 51600
    assert jan["total_amount"] == 51600.0
    # total_qty суммирует quantity по всем item_type (material 5 + delivery 1 = 6)
    assert jan["total_qty"] == 6.0
    assert jan["invoice_count"] == 1

    mar = rows[(2026, 3)]
    assert mar["total_amount"] == 96000.0  # 80000 + 20% VAT
    assert mar["total_qty"] == 10.0
    assert mar["invoice_count"] == 1


def test_monthly_summary_counts_invoices_not_items(client, factories):
    """Несколько позиций в одном счёте — invoice_count = 1, а не кол-во позиций."""
    project = factories.ProjectFactory.create()
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 2, 1))
    factories.InvoiceItemFactory.create(invoice=inv, item_type="material", quantity=3.0, amount=24000.0)
    factories.InvoiceItemFactory.create(invoice=inv, item_type="material", quantity=2.0, amount=16000.0)

    response = client.get(f"/api/dashboard/monthly-summary?project_id={project.id}")
    assert response.status_code == 200
    row = response.json()[0]
    assert row["invoice_count"] == 1
    assert row["total_qty"] == 5.0
    assert row["total_amount"] == 48000.0  # (24000+16000) + 20% VAT


def test_monthly_summary_ordered_chronologically(client, factories):
    project = factories.ProjectFactory.create()
    doc = factories.DocumentFactory.create(project=project)

    for m in [3, 1, 2]:
        inv = factories.InvoiceFactory.create(document=doc, date=date(2026, m, 1))
        factories.InvoiceItemFactory.create(invoice=inv, item_type="material", quantity=1.0, amount=1000.0)

    response = client.get(f"/api/dashboard/monthly-summary?project_id={project.id}")
    months = [(r["year"], r["month"]) for r in response.json()]
    assert months == sorted(months)


def test_monthly_summary_isolated_between_projects(client, factories):
    p1 = factories.ProjectFactory.create()
    p2 = factories.ProjectFactory.create()

    doc1 = factories.DocumentFactory.create(project=p1)
    inv1 = factories.InvoiceFactory.create(document=doc1, date=date(2026, 1, 1))
    factories.InvoiceItemFactory.create(invoice=inv1, item_type="material", quantity=1.0, amount=1000.0)

    response = client.get(f"/api/dashboard/monthly-summary?project_id={p2.id}")
    assert response.status_code == 200
    assert response.json() == []


# ── Supplier exclusion filtering ──────────────────────────────────────────────

def test_summary_excludes_supplier_invoices(client, factories):
    """Исключённый поставщик не попадает в invoice_count и total_amount."""
    project = factories.ProjectFactory.create()
    included_supplier = factories.SupplierFactory.create()
    excluded_supplier = factories.SupplierFactory.create()

    doc = factories.DocumentFactory.create(project=project)
    inv_included = factories.InvoiceFactory.create(document=doc, supplier_id=included_supplier.id)
    factories.InvoiceItemFactory.create(invoice=inv_included, item_type="material", quantity=5.0, amount=40000.0)

    inv_excluded = factories.InvoiceFactory.create(document=doc, supplier_id=excluded_supplier.id)
    factories.InvoiceItemFactory.create(invoice=inv_excluded, item_type="material", quantity=3.0, amount=24000.0)

    # Без исключений — оба инвойса в сводке
    resp = client.get(f"/api/dashboard/summary?project_id={project.id}")
    assert resp.json()["invoice_count"] == 2

    # Исключаем второго поставщика
    resp_excl = client.post(f"/api/projects/{project.id}/supplier-exclusions/{excluded_supplier.id}")
    assert resp_excl.status_code == 204

    resp = client.get(f"/api/dashboard/summary?project_id={project.id}")
    body = resp.json()
    assert body["invoice_count"] == 1
    # total_amount: только included invoice: 40000 + 20% VAT = 48000
    assert body["total_amount"] == 48000.0
    assert body["total_qty"] == 5.0


def test_calculations_excludes_supplier_invoices(client, factories):
    """Расчёт avg_price не учитывает инвойсы исключённого поставщика."""
    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create(calc_role="base")
    included_supplier = factories.SupplierFactory.create()
    excluded_supplier = factories.SupplierFactory.create()

    doc = factories.DocumentFactory.create(project=project)

    # included: qty=10, amount=80000 → avg_price with VAT = 96000/10 = 9600
    inv_inc = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 1), supplier_id=included_supplier.id)
    factories.InvoiceItemFactory.create(invoice=inv_inc, material_class=mc, item_type="material", quantity=10.0, unit_price=8000.0, amount=80000.0)

    # excluded: qty=20, amount=200000 → would pull avg_price down if included
    inv_exc = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 5), supplier_id=excluded_supplier.id)
    factories.InvoiceItemFactory.create(invoice=inv_exc, material_class=mc, item_type="material", quantity=20.0, unit_price=10000.0, amount=200000.0)

    # Без исключений — агрегат по обоим инвойсам
    resp = client.get(f"/api/dashboard/calculations?project_id={project.id}")
    rows_all = resp.json()
    assert len(rows_all) == 1
    assert rows_all[0]["total_qty"] == 30.0

    # Исключаем второго поставщика
    client.post(f"/api/projects/{project.id}/supplier-exclusions/{excluded_supplier.id}")

    resp = client.get(f"/api/dashboard/calculations?project_id={project.id}")
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["total_qty"] == 10.0
    assert row["avg_price"] == 9600.0  # (80000 + 16000) / 10


def test_monthly_summary_excludes_supplier_invoices(client, factories):
    """Исключённый поставщик не учитывается в monthly-summary."""
    project = factories.ProjectFactory.create()
    included_supplier = factories.SupplierFactory.create()
    excluded_supplier = factories.SupplierFactory.create()

    doc = factories.DocumentFactory.create(project=project)
    inv_inc = factories.InvoiceFactory.create(document=doc, date=date(2026, 1, 10), supplier_id=included_supplier.id)
    factories.InvoiceItemFactory.create(invoice=inv_inc, item_type="material", quantity=5.0, amount=40000.0)

    inv_exc = factories.InvoiceFactory.create(document=doc, date=date(2026, 1, 20), supplier_id=excluded_supplier.id)
    factories.InvoiceItemFactory.create(invoice=inv_exc, item_type="material", quantity=3.0, amount=24000.0)

    # Исключаем
    client.post(f"/api/projects/{project.id}/supplier-exclusions/{excluded_supplier.id}")

    resp = client.get(f"/api/dashboard/monthly-summary?project_id={project.id}")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    jan = rows[0]
    assert jan["invoice_count"] == 1
    assert jan["total_qty"] == 5.0
    assert jan["total_amount"] == 48000.0  # 40000 + 20% VAT
