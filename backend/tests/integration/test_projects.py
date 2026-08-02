def test_list_projects_empty(client):
    response = client.get("/api/projects")
    assert response.status_code == 200
    assert response.json() == []


def test_create_project(client):
    response = client.post(
        "/api/projects",
        json={"name": "Новый объект", "contract_number": "Д-007"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Новый объект"
    assert body["contract_number"] == "Д-007"
    assert isinstance(body["id"], int)


def test_list_projects_returns_created(client, factories):
    factories.ProjectFactory.create(name="ЖК Радуга")
    factories.ProjectFactory.create(name="ЖК Звезда")

    response = client.get("/api/projects")
    assert response.status_code == 200
    names = [p["name"] for p in response.json()]
    assert names == ["ЖК Звезда", "ЖК Радуга"]


def test_update_project(client, factories):
    project = factories.ProjectFactory.create(name="Старое имя")
    response = client.put(
        f"/api/projects/{project.id}",
        json={"name": "Новое имя", "contract_number": "Д-999"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Новое имя"


def test_update_project_404(client):
    response = client.put(
        "/api/projects/9999",
        json={"name": "X"},
    )
    assert response.status_code == 404


def test_delete_project(client, factories):
    project = factories.ProjectFactory.create()
    response = client.delete(f"/api/projects/{project.id}")
    assert response.status_code == 200

    # Проверяем, что список пустой
    list_response = client.get("/api/projects")
    assert list_response.json() == []


def test_delete_project_404(client):
    response = client.delete("/api/projects/9999")
    assert response.status_code == 404
