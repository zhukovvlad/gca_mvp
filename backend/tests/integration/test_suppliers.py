"""Интеграционные тесты роутера /api/suppliers."""
from crud.suppliers import get_or_create_supplier

# --- GET /api/suppliers ---

def test_list_suppliers_empty(client):
    response = client.get("/api/suppliers")
    assert response.status_code == 200
    assert response.json() == []


def test_list_suppliers_returns_created(client, factories):
    factories.SupplierFactory.create(name="ООО Альфа", inn="1111111111")
    factories.SupplierFactory.create(name="ООО Бета", inn="2222222222")

    response = client.get("/api/suppliers")
    assert response.status_code == 200
    names = [s["name"] for s in response.json()]
    assert "ООО Альфа" in names
    assert "ООО Бета" in names


def test_list_suppliers_includes_invoice_count(client, factories):
    supplier = factories.SupplierFactory.create(name="ООО Поставщик", inn="3333333333")
    doc = factories.DocumentFactory.create()
    factories.InvoiceFactory.create(document=doc, supplier_id=supplier.id, supplier_inn="3333333333")
    factories.InvoiceFactory.create(document=doc, supplier_id=supplier.id, supplier_inn="3333333333")

    response = client.get("/api/suppliers")
    match = next(s for s in response.json() if s["id"] == supplier.id)
    assert match["invoice_count"] == 2


# --- GET /api/suppliers/{id} ---

def test_get_supplier_returns_data(client, factories):
    supplier = factories.SupplierFactory.create(name="ООО Гамма", inn="4444444444")
    doc = factories.DocumentFactory.create()
    factories.InvoiceFactory.create(document=doc, supplier_id=supplier.id)

    response = client.get(f"/api/suppliers/{supplier.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "ООО Гамма"
    assert body["inn"] == "4444444444"
    assert body["invoice_count"] == 1


def test_get_supplier_404(client):
    response = client.get("/api/suppliers/99999")
    assert response.status_code == 404


# --- POST /api/suppliers/{id}/merge ---

def test_merge_suppliers_moves_invoices(client, factories, db_session):
    source = factories.SupplierFactory.create(name="ООО Источник", inn="5555555555")
    target = factories.SupplierFactory.create(name="ООО Цель", inn="6666666666")
    doc = factories.DocumentFactory.create()
    inv = factories.InvoiceFactory.create(document=doc, supplier_id=source.id)

    response = client.post(
        f"/api/suppliers/{target.id}/merge",
        json={"source_id": source.id},
    )
    assert response.status_code == 200
    assert response.json()["id"] == target.id

    # Инвойс теперь принадлежит target
    db_session.expire_all()
    from models import Invoice
    updated_inv = db_session.query(Invoice).filter(Invoice.id == inv.id).first()
    assert updated_inv.supplier_id == target.id
    # supplier_name/inn в инвойсе должны обновиться до канонических значений target
    assert updated_inv.supplier_name == target.name
    assert updated_inv.supplier_inn == target.inn

    # source удалён
    from models import Supplier
    assert db_session.query(Supplier).filter(Supplier.id == source.id).first() is None


def test_merge_suppliers_target_not_found(client, factories):
    source = factories.SupplierFactory.create(name="ООО Источник 2", inn="7777777777")
    response = client.post(
        "/api/suppliers/99999/merge",
        json={"source_id": source.id},
    )
    assert response.status_code == 404


def test_merge_suppliers_source_not_found(client, factories):
    target = factories.SupplierFactory.create(name="ООО Цель 2", inn="8888888888")
    response = client.post(
        f"/api/suppliers/{target.id}/merge",
        json={"source_id": 99999},
    )
    assert response.status_code == 404


def test_merge_same_id_rejected(client, factories):
    supplier = factories.SupplierFactory.create(name="ООО Один", inn="9999999999")
    response = client.post(
        f"/api/suppliers/{supplier.id}/merge",
        json={"source_id": supplier.id},
    )
    assert response.status_code == 422


# --- GET /api/suppliers/duplicates ---

def test_duplicates_empty(client):
    response = client.get("/api/suppliers/duplicates")
    assert response.status_code == 200
    assert response.json() == []


def test_duplicates_finds_similar_names(client, factories):
    # Одна и та же компания — слова переставлены местами (типичный случай для РФ)
    factories.SupplierFactory.create(name="ООО СтройБетон", inn=None)
    factories.SupplierFactory.create(name="СтройБетон ООО", inn=None)

    response = client.get("/api/suppliers/duplicates")
    assert response.status_code == 200
    pairs = response.json()
    assert len(pairs) == 1
    names = {pairs[0]["supplier_a"]["name"], pairs[0]["supplier_b"]["name"]}
    assert "ООО СтройБетон" in names
    assert "СтройБетон ООО" in names
    assert pairs[0]["score"] >= 85.0


def test_duplicates_ignores_suppliers_with_inn(client, factories):
    # Поставщики с ИНН — не участвуют в поиске дубликатов
    factories.SupplierFactory.create(name="ООО СтройБетон", inn="1234567890")
    factories.SupplierFactory.create(name="ООО Строй Бетон", inn="0987654321")

    response = client.get("/api/suppliers/duplicates")
    assert response.status_code == 200
    assert response.json() == []


def test_duplicates_invalid_threshold(client):
    response = client.get("/api/suppliers/duplicates?threshold=0")
    assert response.status_code == 422


# --- CRUD: get_or_create_supplier ---

def test_get_or_create_supplier_by_inn(db_session):
    s1 = get_or_create_supplier(db_session, name="ООО Бетон", inn="1112223334")
    s2 = get_or_create_supplier(db_session, name="ООО Бетон Другое", inn="1112223334")
    # Тот же ИНН → тот же объект
    assert s1.id == s2.id
    assert s1.name == "ООО Бетон"


def test_get_or_create_supplier_by_name_no_inn(db_session):
    s1 = get_or_create_supplier(db_session, name="ООО Ромашка", inn=None)
    s2 = get_or_create_supplier(db_session, name="ООО Ромашка", inn=None)
    assert s1.id == s2.id


def test_get_or_create_supplier_different_names_no_inn(db_session):
    s1 = get_or_create_supplier(db_session, name="ООО Ромашка", inn=None)
    s2 = get_or_create_supplier(db_session, name="ООО Лютик", inn=None)
    assert s1.id != s2.id


# --- POST /api/suppliers ---

def test_create_supplier(client):
    response = client.post("/api/suppliers", json={"name": "ООО Новый", "inn": "1230001230"})
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "ООО Новый"
    assert body["inn"] == "1230001230"
    assert isinstance(body["id"], int)
    # Проверяем что запись действительно сохранилась в БД (не только flush без commit)
    get_response = client.get(f"/api/suppliers/{body['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["name"] == "ООО Новый"


def test_create_supplier_no_inn(client):
    response = client.post("/api/suppliers", json={"name": "ООО БезИНН"})
    assert response.status_code == 200
    assert response.json()["inn"] is None


def test_create_supplier_empty_name_rejected(client):
    response = client.post("/api/suppliers", json={"name": "  ", "inn": None})
    assert response.status_code == 422


def test_create_supplier_duplicate_inn_returns_existing(client, factories):
    """POST с существующим ИНН возвращает имеющегося поставщика, не создаёт дубль."""
    existing = factories.SupplierFactory.create(name="ООО Альфа", inn="5550005550")
    response = client.post("/api/suppliers", json={"name": "ООО Другой", "inn": "5550005550"})
    assert response.status_code == 200
    # возвращается существующая запись, не новая
    assert response.json()["id"] == existing.id
    assert response.json()["name"] == existing.name


def test_create_supplier_duplicate_name_no_inn_returns_existing(client, factories):
    """POST с тем же именем (без ИНН) возвращает имеющегося поставщика."""
    existing = factories.SupplierFactory.create(name="ООО Без ИНН", inn=None)
    response = client.post("/api/suppliers", json={"name": "ООО Без ИНН"})
    assert response.status_code == 200
    assert response.json()["id"] == existing.id


# --- PUT /api/suppliers/{id} ---

def test_update_supplier_name(client, factories):
    supplier = factories.SupplierFactory.create(name="ООО Старый", inn="6660006660")
    response = client.put(
        f"/api/suppliers/{supplier.id}",
        json={"name": "ООО Новое имя", "inn": "6660006660"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "ООО Новое имя"


def test_update_supplier_syncs_invoices(client, factories, db_session):
    """PUT /suppliers/{id} обновляет supplier_name/inn в связанных инвойсах."""
    from models import Invoice

    supplier = factories.SupplierFactory.create(name="ООО Старый", inn="7770007770")
    doc = factories.DocumentFactory.create()
    inv = factories.InvoiceFactory.create(document=doc, supplier_id=supplier.id, supplier_name="ООО Старый")

    resp = client.put(
        f"/api/suppliers/{supplier.id}",
        json={"name": "ООО Новое имя", "inn": "7770007770"},
    )
    assert resp.status_code == 200

    db_session.expire_all()
    updated = db_session.query(Invoice).filter(Invoice.id == inv.id).first()
    assert updated.supplier_name == "ООО Новое имя"


def test_update_supplier_404(client):
    response = client.put("/api/suppliers/99999", json={"name": "X", "inn": None})
    assert response.status_code == 404


def test_update_supplier_duplicate_inn_rejected(client, factories):
    factories.SupplierFactory.create(name="ООО Занятый", inn="8880008880")
    target = factories.SupplierFactory.create(name="ООО Цель", inn="9990009990")
    response = client.put(
        f"/api/suppliers/{target.id}",
        json={"name": "ООО Цель", "inn": "8880008880"},
    )
    assert response.status_code == 409


# --- DELETE /api/suppliers/{id} ---

def test_delete_supplier_no_invoices(client, factories):
    supplier = factories.SupplierFactory.create(name="ООО Удаляемый", inn=None)
    response = client.delete(f"/api/suppliers/{supplier.id}")
    assert response.status_code == 200

    check = client.get(f"/api/suppliers/{supplier.id}")
    assert check.status_code == 404


def test_delete_supplier_with_invoices_rejected(client, factories):
    supplier = factories.SupplierFactory.create(name="ООО Занятый Инвойсами", inn=None)
    doc = factories.DocumentFactory.create()
    factories.InvoiceFactory.create(document=doc, supplier_id=supplier.id)

    response = client.delete(f"/api/suppliers/{supplier.id}")
    assert response.status_code == 409


def test_delete_supplier_404(client):
    response = client.delete("/api/suppliers/99999")
    assert response.status_code == 404
