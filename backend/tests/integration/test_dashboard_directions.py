from datetime import date
from decimal import Decimal

import pytest


def _rebar_class(factories, name="А500С Ø12"):
    """Класс арматуры (material_type_code=rebar) для тестовых счетов."""
    return factories.MaterialClassFactory.create(material_type_code="rebar", name=name)


def _rebar_item(factories, invoice, mc, qty, unit_price):
    """Позиция арматуры в тоннах — normalized_* задаём явно (см. факторку)."""
    from tests.factories import _unit_id
    return factories.InvoiceItemFactory.create(
        invoice=invoice, material_class=mc, item_type="material",
        quantity=qty, raw_unit="т", unit_price=unit_price,
        normalized_unit_id=_unit_id("TON"), normalized_quantity=Decimal(str(qty)),
    )


def test_additive_scoped_to_own_type_in_mixed_invoice(client, factories):
    """§5.4: additive типа concrete в смешанном счёте входит только бетону;
    у арматуры — только доля доставки."""
    project = factories.ProjectFactory.create()
    concrete = factories.MaterialClassFactory.create(name="В25")
    rebar = _rebar_class(factories)
    plasticizer = factories.MaterialClassFactory.create(name="Пластификатор", calc_role="additive")
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 10))
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=concrete, item_type="material",
        quantity=10, unit_price=8000, amount=80000)               # бетон 80000 (+20% НДС)
    _rebar_item(factories, inv, rebar, qty=2, unit_price=10000)   # арматура 20000 (+НДС)
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=plasticizer, item_type="material",
        quantity=1, unit_price=1000, amount=1000)                 # additive concrete 1000 (+НДС 200)
    factories.InvoiceItemFactory.create(
        invoice=inv, item_type="delivery", material_class=None,
        quantity=1, unit_price=5000, amount=5000)                 # доставка 5000 (+НДС 1000)

    rows = client.get(f"/api/dashboard/calculations?project_id={project.id}").json()
    by_name = {r["material_class_name"]: r for r in rows}
    # mixed dimensions → delivery по amount (80000 vs 20000 без НДС): 0.8 / 0.2 от 6000 с НДС
    # additive (1200 с НДС) — только бетону
    assert by_name["В25"]["delivery_total"] == 4800.0 + 1200.0
    assert by_name["А500С Ø12"]["delivery_total"] == 1200.0


def test_additive_without_own_type_base_not_allocated(client, factories):
    """Edge §5.4: additive concrete в счёте только с base rebar — никому."""
    project = factories.ProjectFactory.create()
    rebar = _rebar_class(factories)
    plasticizer = factories.MaterialClassFactory.create(name="Пластификатор", calc_role="additive")
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 10))
    _rebar_item(factories, inv, rebar, qty=2, unit_price=10000)
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=plasticizer, item_type="material",
        quantity=1, unit_price=1000, amount=1000)

    rows = client.get(f"/api/dashboard/calculations?project_id={project.id}").json()
    assert len(rows) == 1
    assert rows[0]["delivery_total"] == 0.0  # additive не разнесён


def test_supplier_project_deviation_additive_scoped(client, factories):
    """§5.4: та же разноска в _compute_supplier_project_deviation (карточка поставщика).
    Additive concrete не должен удорожать арматуру в отклонении поставщика."""
    project = factories.ProjectFactory.create()
    concrete = factories.MaterialClassFactory.create(name="В25")
    rebar = _rebar_class(factories)
    plasticizer = factories.MaterialClassFactory.create(name="Пластификатор", calc_role="additive")
    supplier = factories.SupplierFactory.create()
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, supplier_id=supplier.id, date=date(2026, 3, 10))
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=concrete, item_type="material",
        quantity=10, unit_price=8000, amount=80000)
    _rebar_item(factories, inv, rebar, qty=2, unit_price=10000)
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=plasticizer, item_type="material",
        quantity=1, unit_price=1000, amount=1000)
    # Базовая цена ровно по факту арматуры: 12000 ₽/т с НДС → отклонение арматуры = 0,
    # если additive (1200 с НДС) НЕ протёк в её avg_price.
    from tests.factories import _unit_id
    factories.ReferencePriceFactory.create(
        project=project, material_class=rebar, price=12000.0, unit_id=_unit_id("TON"))

    rows = client.get(f"/api/suppliers/{supplier.id}/projects").json()
    stats = next(r for r in rows if r["project_id"] == project.id)
    assert stats["deviation_amount"] == 0.0


def test_calculations_direction_field_and_filter(client, factories):
    """Поле `direction` в строках calculations + фильтр ?direction= по направлению."""
    project = factories.ProjectFactory.create()
    concrete = factories.MaterialClassFactory.create(name="В25")
    rebar = _rebar_class(factories)
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 10))
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=concrete, item_type="material",
        quantity=10, unit_price=8000, amount=80000)
    _rebar_item(factories, inv, rebar, qty=2, unit_price=10000)

    all_rows = client.get(f"/api/dashboard/calculations?project_id={project.id}").json()
    assert {r["direction"] for r in all_rows} == {"concrete", "rebar"}

    resp = client.get(f"/api/dashboard/calculations?project_id={project.id}&direction=rebar")
    rows = resp.json()
    assert [r["material_class_name"] for r in rows] == ["А500С Ø12"]


def test_calculations_direction_filter_does_not_change_class_rows(client, factories):
    """Тест-страж ADR #2: фильтр на выходе — поклассовые цифры идентичны."""
    project = factories.ProjectFactory.create()
    concrete = factories.MaterialClassFactory.create(name="В25")
    rebar = _rebar_class(factories)
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 10))
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=concrete, item_type="material",
        quantity=10, unit_price=8000, amount=80000)
    _rebar_item(factories, inv, rebar, qty=2, unit_price=10000)
    factories.InvoiceItemFactory.create(
        invoice=inv, item_type="delivery", material_class=None,
        quantity=1, unit_price=5000, amount=5000)
    factories.ReferencePriceFactory.create(project=project, material_class=concrete, price=8000.0)

    full = client.get(f"/api/dashboard/calculations?project_id={project.id}").json()
    scoped = client.get(f"/api/dashboard/calculations?project_id={project.id}&direction=concrete").json()
    full_concrete = [r for r in full if r["direction"] == "concrete"]
    assert scoped == full_concrete  # включая avg_price/deviation/compensation


def test_calculations_unknown_direction_422(client, factories):
    """Неизвестное направление в calculations → 422."""
    project = factories.ProjectFactory.create()
    resp = client.get(f"/api/dashboard/calculations?project_id={project.id}&direction=bricks")
    assert resp.status_code == 422


def _mixed_project(factories):
    """Объект: смешанный счёт (бетон+арматура+доставка+прочее) + чисто бетонный счёт
    + счёт без direction-позиций (только доставка)."""
    project = factories.ProjectFactory.create()
    concrete = factories.MaterialClassFactory.create(name="В25")
    rebar = _rebar_class(factories)
    doc = factories.DocumentFactory.create(project=project)

    inv_mixed = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 10))
    factories.InvoiceItemFactory.create(
        invoice=inv_mixed, material_class=concrete, item_type="material",
        quantity=10, unit_price=8000, amount=80000)               # 96000 с НДС
    _rebar_item(factories, inv_mixed, rebar, qty=2, unit_price=10000)  # 24000 с НДС
    factories.InvoiceItemFactory.create(
        invoice=inv_mixed, item_type="delivery", material_class=None,
        quantity=1, unit_price=5000, amount=5000)                 # 6000 с НДС
    factories.InvoiceItemFactory.create(
        invoice=inv_mixed, item_type="other", material_class=None,
        quantity=1, unit_price=2000, amount=2000)                 # 2400 с НДС

    inv_concrete = factories.InvoiceFactory.create(document=doc, date=date(2026, 4, 5))
    factories.InvoiceItemFactory.create(
        invoice=inv_concrete, material_class=concrete, item_type="material",
        quantity=5, unit_price=8200, amount=41000)                # 49200 с НДС

    inv_delivery_only = factories.InvoiceFactory.create(document=doc, date=date(2026, 4, 7))
    factories.InvoiceItemFactory.create(
        invoice=inv_delivery_only, item_type="delivery", material_class=None,
        quantity=1, unit_price=1000, amount=1000)                 # 1200 с НДС

    return project, concrete, rebar


def test_summary_directions_and_invariant(client, factories):
    """Summary: разбивка по направлениям (оборот/объём/счета/смешанность) + инвариант §5.1."""
    project, *_ = _mixed_project(factories)
    body = client.get(f"/api/dashboard/summary?project_id={project.id}").json()

    codes = [d["code"] for d in body["directions"]]
    assert codes == ["concrete", "rebar"]          # порядок — по id типа; other отсутствует
    by_code = {d["code"]: d for d in body["directions"]}
    assert by_code["concrete"]["turnover"] == 96000.0 + 49200.0
    assert by_code["rebar"]["turnover"] == 24000.0
    assert by_code["concrete"]["volume"] == 15.0
    assert by_code["concrete"]["volume_unit"] == "м³"
    assert by_code["rebar"]["volume"] == 2.0
    assert by_code["rebar"]["volume_unit"] == "т"
    assert by_code["concrete"]["invoice_count"] == 2
    assert by_code["rebar"]["invoice_count"] == 1
    assert by_code["concrete"]["mixed_invoice_count"] == 1
    assert body["mixed_invoice_count"] == 1
    assert body["other_invoice_count"] == 1        # счёт из одной доставки
    assert body["delivery_total"] == 6000.0 + 1200.0
    assert body["other_total"] == 2400.0
    # ИНВАРИАНТ §5.1 — на сериализованных значениях. Каждое слагаемое округлено
    # до копеек НЕЗАВИСИМО, поэтому сумма округлённых может разойтись с округлённой
    # суммой на ±копейки — approx, не точное равенство. Точная (Decimal, до round)
    # партиция проверяется отдельным тестом test_summary_material_partition_exact.
    assert body["total_amount"] == pytest.approx(
        sum(d["turnover"] for d in body["directions"])
        + body["delivery_total"] + body["other_total"],
        abs=0.05,
    )


def test_summary_material_partition_exact(client, factories, db_session):
    """Точный Decimal-инвариант §5.1 ДО сериализации: группировка material-позиций
    по типу (направления / other / NULL-класс) разбивает их без пересечений и
    пропусков — суммы сходятся точно. Значит, расхождение сериализованного
    инварианта может дать только независимое округление, не потеря позиций."""
    project, *_ = _mixed_project(factories)
    from sqlalchemy import func, literal

    from models import Document, Invoice, InvoiceItem, MaterialClass, MaterialType

    vat = func.coalesce(
        InvoiceItem.vat_amount,
        InvoiceItem.amount * func.coalesce(Invoice.vat_rate, literal(Decimal("20.0"))) / 100,
    )
    grouped = (
        db_session.query(MaterialType.code, func.sum(InvoiceItem.amount + vat))
        .select_from(InvoiceItem)
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .join(Document, Invoice.document_id == Document.id)
        .outerjoin(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
        .outerjoin(MaterialType, MaterialClass.material_type_id == MaterialType.id)
        .filter(Document.project_id == project.id, InvoiceItem.item_type == "material")
        .group_by(MaterialType.code)
        .all()
    )
    total_material = (
        db_session.query(func.sum(InvoiceItem.amount + vat))
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .join(Document, Invoice.document_id == Document.id)
        .filter(Document.project_id == project.id, InvoiceItem.item_type == "material")
        .scalar()
    )
    assert sum((row[1] for row in grouped), Decimal("0")) == total_material  # точно, без round


def test_summary_other_type_class_goes_to_other_total(client, factories):
    """ADR #9: классы типа other — в other_total, направления не образуют."""
    project = factories.ProjectFactory.create()
    misc = factories.MaterialClassFactory.create(material_type_code="other", name="Крепёж")
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc)
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=misc, item_type="material",
        quantity=1, unit_price=3000, amount=3000)

    body = client.get(f"/api/dashboard/summary?project_id={project.id}").json()
    assert body["directions"] == []
    assert body["other_total"] == 3600.0
    assert body["other_invoice_count"] == 1
    assert body["mixed_invoice_count"] == 0


def test_summary_concrete_only_matches_legacy_fields(client, factories):
    """Критерий приёмки #1: моно-бетонный объект — directions согласован со старыми полями."""
    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create()
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc)
    factories.InvoiceItemFactory.create(invoice=inv, material_class=mc, item_type="material",
                                        quantity=5, amount=40000)
    body = client.get(f"/api/dashboard/summary?project_id={project.id}").json()
    assert [d["code"] for d in body["directions"]] == ["concrete"]
    d = body["directions"][0]
    assert d["turnover"] == body["material_amount"]
    assert d["volume"] == 5.0
    assert d["invoice_count"] == body["invoice_count"]
    assert body["mixed_invoice_count"] == 0 and body["other_invoice_count"] == 0


def test_summary_compensation_per_direction_sums_to_full(client, factories):
    """Компенсация по направлениям суммируется в полную компенсацию объекта (approx)."""
    project, concrete, rebar = _mixed_project(factories)
    factories.ReferencePriceFactory.create(project=project, material_class=concrete, price=8000.0)
    from tests.factories import _unit_id
    factories.ReferencePriceFactory.create(
        project=project, material_class=rebar, price=10000.0, unit_id=_unit_id("TON"))
    # corridor_pct=0 → любое отклонение компенсируется (whitelist по типам).
    for code in ("concrete", "rebar"):
        client.put(
            f"/api/projects/{project.id}/corridors/type/{code}",
            json={"is_compensable": True, "corridor_pct": 0},
        )

    body = client.get(f"/api/dashboard/summary?project_id={project.id}").json()
    by_code = {d["code"]: d for d in body["directions"]}
    overpayments = [d["overpayment"] for d in body["directions"] if d["overpayment"] is not None]
    # Слагаемые округлены независимо при сериализации → approx (та же причина,
    # что у инварианта оборота; источник один — calc_rows, потерь быть не может).
    assert sum(overpayments) == pytest.approx(body["full_compensation_amount"], abs=0.05)
    assert by_code["concrete"]["overpayment"] is not None


def test_summary_volume_excluded_count(client, factories):
    """§5.2: арматура в пог.м (length) не входит в объём «т» и попадает в счётчик."""
    project = factories.ProjectFactory.create()
    rebar = _rebar_class(factories)
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc)
    _rebar_item(factories, inv, rebar, qty=2, unit_price=10000)   # 2 т — входит
    from tests.factories import _unit_id
    factories.InvoiceItemFactory.create(                          # 100 пог.м — НЕ входит
        invoice=inv, material_class=rebar, item_type="material",
        quantity=100, raw_unit="м", unit_price=50,
        normalized_unit_id=_unit_id("M"), normalized_quantity=Decimal("100"))

    body = client.get(f"/api/dashboard/summary?project_id={project.id}").json()
    d = body["directions"][0]
    assert d["volume"] == 2.0
    assert d["volume_excluded_count"] == 1


def test_invoices_direction_filter_mixed_visible_in_both(client, factories):
    """Критерий #2: смешанный счёт виден в обоих направлениях, целиком."""
    project, *_ = _mixed_project(factories)
    base = f"/api/dashboard/invoices?project_id={project.id}"
    all_ids = {i["id"] for i in client.get(base).json()}
    concrete_ids = {i["id"] for i in client.get(base + "&direction=concrete").json()}
    rebar_ids = {i["id"] for i in client.get(base + "&direction=rebar").json()}
    assert len(all_ids) == 3
    assert len(concrete_ids) == 2          # смешанный + чисто бетонный
    assert len(rebar_ids) == 1             # только смешанный
    assert rebar_ids < concrete_ids | rebar_ids
    mixed_id = next(iter(rebar_ids))
    mixed_inv = next(i for i in client.get(base + "&direction=rebar").json() if i["id"] == mixed_id)
    assert len(mixed_inv["items"]) == 4    # документ целиком, со всеми позициями


def test_monthly_summary_direction_scoped(client, factories):
    """monthly-summary с ?direction= — только позиции направления, в его родной единице."""
    project, *_ = _mixed_project(factories)
    base = f"/api/dashboard/monthly-summary?project_id={project.id}"
    rows = client.get(base + "&direction=concrete").json()
    by_month = {(r["year"], r["month"]): r for r in rows}
    assert by_month[(2026, 3)]["total_amount"] == 96000.0   # только бетонные позиции
    assert by_month[(2026, 3)]["total_qty"] == 10.0
    assert by_month[(2026, 3)]["invoice_count"] == 1
    assert by_month[(2026, 3)]["volume_unit"] == "м³"
    assert by_month[(2026, 4)]["total_amount"] == 49200.0
    assert (2026, 4) in by_month and by_month[(2026, 4)]["invoice_count"] == 1
    # счёт «только доставка» (апрель) в направлении не существует


def test_monthly_summary_without_direction_unchanged(client, factories):
    """Без параметра — прежнее поведение + volume_unit: None."""
    project, *_ = _mixed_project(factories)
    rows = client.get(f"/api/dashboard/monthly-summary?project_id={project.id}").json()
    by_month = {(r["year"], r["month"]): r for r in rows}
    assert by_month[(2026, 3)]["total_amount"] == 96000.0 + 24000.0 + 6000.0 + 2400.0
    assert by_month[(2026, 4)]["total_amount"] == 49200.0 + 1200.0
    assert all(r["volume_unit"] is None for r in rows)


def test_invoices_unknown_direction_422(client, factories):
    """Неизвестное направление в invoices и monthly-summary → 422."""
    project = factories.ProjectFactory.create()
    assert client.get(f"/api/dashboard/invoices?project_id={project.id}&direction=bricks").status_code == 422
    assert client.get(f"/api/dashboard/monthly-summary?project_id={project.id}&direction=bricks").status_code == 422


def test_project_suppliers_direction_scoped(client, factories):
    """Поставщики проекта фильтруются по ?direction= — только касавшиеся направления."""
    project = factories.ProjectFactory.create()
    concrete = factories.MaterialClassFactory.create(name="В25")
    rebar = _rebar_class(factories)
    sup_a = factories.SupplierFactory.create(name="БетонТорг")
    sup_b = factories.SupplierFactory.create(name="МеталлБаза")
    doc = factories.DocumentFactory.create(project=project)
    inv_a = factories.InvoiceFactory.create(document=doc, supplier_id=sup_a.id)
    factories.InvoiceItemFactory.create(invoice=inv_a, material_class=concrete,
                                        item_type="material", quantity=5, amount=40000)
    inv_b = factories.InvoiceFactory.create(document=doc, supplier_id=sup_b.id)
    _rebar_item(factories, inv_b, rebar, qty=2, unit_price=10000)

    url = f"/api/projects/{project.id}/suppliers"
    assert {s["name"] for s in client.get(url).json()} == {"БетонТорг", "МеталлБаза"}
    concrete_rows = client.get(url + "?direction=concrete").json()
    assert [s["name"] for s in concrete_rows] == ["БетонТорг"]
    assert concrete_rows[0]["invoice_count"] == 1


def test_project_suppliers_unknown_direction_422(client, factories):
    """Неизвестное направление в списке поставщиков проекта → 422."""
    project = factories.ProjectFactory.create()
    assert client.get(f"/api/projects/{project.id}/suppliers?direction=bricks").status_code == 422


def test_reference_prices_direction_filter(client, factories):
    """reference-prices фильтруются по ?direction=; неизвестное направление → 422."""
    project = factories.ProjectFactory.create()
    concrete = factories.MaterialClassFactory.create(name="В25")
    rebar = _rebar_class(factories)
    factories.ReferencePriceFactory.create(project=project, material_class=concrete)
    from tests.factories import _unit_id
    factories.ReferencePriceFactory.create(
        project=project, material_class=rebar, unit_id=_unit_id("TON"), price=10000.0)

    rows = client.get(f"/api/reference-prices?project_id={project.id}&direction=rebar").json()
    assert [r["material_type"] for r in rows] == ["rebar"]
    assert client.get(f"/api/reference-prices?project_id={project.id}&direction=bricks").status_code == 422


def test_reference_price_for_other_type_class_rejected(client, factories):
    """§5.3: классам типа other базовая цена не назначается → 422."""
    project = factories.ProjectFactory.create()
    misc = factories.MaterialClassFactory.create(material_type_code="other", name="Крепёж")
    from tests.factories import _unit_id
    resp = client.post("/api/reference-prices", json={
        "project_id": project.id, "material_class_id": misc.id,
        "unit_id": _unit_id("PCS"), "price": 100,
        "period_start": "2026-01-01", "period_end": "2026-12-31",
    })
    assert resp.status_code == 422
    assert "Прочее" in resp.json()["detail"]


def test_direction_other_is_not_a_direction_422(client, factories):
    """ADR #9: тип other направлением не является — ?direction=other → 422."""
    project = factories.ProjectFactory.create()
    base = f"/api/dashboard/calculations?project_id={project.id}"
    resp = client.get(base + "&direction=other")
    assert resp.status_code == 422
    assert "Прочее" in resp.json()["detail"]


def test_reference_price_patch_for_other_type_class_rejected(client, factories):
    """§5.3 defense-in-depth: PATCH legacy-строки типа other → 422 (POST уже заблокирован)."""
    project = factories.ProjectFactory.create()
    misc = factories.MaterialClassFactory.create(material_type_code="other", name="Крепёж")
    from tests.factories import _unit_id
    rp = factories.ReferencePriceFactory.create(
        project=project, material_class=misc, unit_id=_unit_id("PCS"), price=100.0)
    resp = client.patch(f"/api/reference-prices/{rp.id}", json={"price": 200})
    assert resp.status_code == 422
    assert "Прочее" in resp.json()["detail"]


def test_dashboard_invoices_directions_field(client, factories):
    """Каждый счёт несёт directions: коды направлений; смешанный — оба, прочий — []."""
    project = factories.ProjectFactory.create()
    concrete = factories.MaterialClassFactory.create(name="В25")          # concrete
    rebar = _rebar_class(factories)                                        # rebar
    doc = factories.DocumentFactory.create(project=project)

    # смешанный счёт: бетон + арматура
    mixed = factories.InvoiceFactory.create(document=doc, number="MIX", date=date(2026, 3, 10))
    factories.InvoiceItemFactory.create(
        invoice=mixed, material_class=concrete, item_type="material",
        quantity=10, unit_price=8000, amount=80000)
    _rebar_item(factories, mixed, rebar, qty=2, unit_price=10000)

    # счёт-сирота: материал без класса (material_class=None)
    orphan = factories.InvoiceFactory.create(document=doc, number="ORPHAN", date=date(2026, 3, 11))
    factories.InvoiceItemFactory.create(
        invoice=orphan, material_class=None, item_type="material",
        quantity=1, unit_price=5000, amount=5000)

    rows = client.get(f"/api/dashboard/invoices?project_id={project.id}").json()
    by_number = {r["number"]: r for r in rows}
    assert sorted(by_number["MIX"]["directions"]) == ["concrete", "rebar"]
    assert by_number["ORPHAN"]["directions"] == []
