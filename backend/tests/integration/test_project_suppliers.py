"""Интеграционные тесты эндпоинтов поставщиков и исключений проекта."""


def test_get_project_suppliers_empty(client, factories):
    project = factories.ProjectFactory.create()
    response = client.get(f"/api/projects/{project.id}/suppliers")
    assert response.status_code == 200
    assert response.json() == []


def test_get_project_suppliers_returns_suppliers_with_invoice_count(client, factories):
    project = factories.ProjectFactory.create()
    supplier = factories.SupplierFactory.create()

    doc = factories.DocumentFactory.create(project=project)
    factories.InvoiceFactory.create(document=doc, supplier_id=supplier.id)
    factories.InvoiceFactory.create(document=doc, supplier_id=supplier.id)

    response = client.get(f"/api/projects/{project.id}/suppliers")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == supplier.id
    assert body[0]["name"] == supplier.name
    assert body[0]["invoice_count"] == 2


def test_get_project_suppliers_excludes_null_supplier_invoices(client, factories):
    """Инвойсы без supplier_id не должны попадать в список поставщиков."""
    project = factories.ProjectFactory.create()
    doc = factories.DocumentFactory.create(project=project)
    factories.InvoiceFactory.create(document=doc, supplier_id=None)

    response = client.get(f"/api/projects/{project.id}/suppliers")
    assert response.status_code == 200
    assert response.json() == []


def test_get_supplier_exclusions_empty(client, factories):
    project = factories.ProjectFactory.create()
    response = client.get(f"/api/projects/{project.id}/supplier-exclusions")
    assert response.status_code == 200
    assert response.json() == []


def test_add_and_remove_supplier_exclusion(client, factories):
    project = factories.ProjectFactory.create()
    supplier = factories.SupplierFactory.create()

    # Добавить исключение
    response = client.post(
        f"/api/projects/{project.id}/supplier-exclusions/{supplier.id}",
        json={"reason": "Аварийная закупка"},
    )
    assert response.status_code == 204

    # Проверить список
    response = client.get(f"/api/projects/{project.id}/supplier-exclusions")
    assert response.json() == [supplier.id]

    # Снять исключение
    response = client.delete(
        f"/api/projects/{project.id}/supplier-exclusions/{supplier.id}"
    )
    assert response.status_code == 204

    # Список пустой
    response = client.get(f"/api/projects/{project.id}/supplier-exclusions")
    assert response.json() == []


def test_add_exclusion_idempotent(client, factories):
    """POST дважды — второй вызов не возвращает ошибку."""
    project = factories.ProjectFactory.create()
    supplier = factories.SupplierFactory.create()

    client.post(
        f"/api/projects/{project.id}/supplier-exclusions/{supplier.id}",
        json={},
    )
    response = client.post(
        f"/api/projects/{project.id}/supplier-exclusions/{supplier.id}",
        json={},
    )
    assert response.status_code == 204


def test_add_exclusion_unknown_supplier_returns_404(client, factories):
    project = factories.ProjectFactory.create()
    response = client.post(
        f"/api/projects/{project.id}/supplier-exclusions/99999",
        json={},
    )
    assert response.status_code == 404
