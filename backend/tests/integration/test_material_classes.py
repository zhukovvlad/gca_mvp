def test_create_material_class(client):
    response = client.post(
        "/api/material-classes",
        json={"name": "В30", "material_type": "concrete"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "В30"
    assert body["material_type"] == "concrete"


def test_create_is_idempotent(client):
    """get_or_create — повторный вызов не плодит дубликаты."""
    r1 = client.post("/api/material-classes", json={"name": "В25", "material_type": "concrete"})
    r2 = client.post("/api/material-classes", json={"name": "В25", "material_type": "concrete"})
    assert r1.json()["id"] == r2.json()["id"]


def test_list_filtered_by_material_type(client, factories):
    factories.MaterialClassFactory.create(name="В25", material_type_code="concrete")
    factories.MaterialClassFactory.create(name="d12", material_type_code="rebar")

    response = client.get("/api/material-classes?material_type=concrete")
    assert response.status_code == 200
    assert all(c["material_type"] == "concrete" for c in response.json())


def test_delete_material_class(client, factories):
    mc = factories.MaterialClassFactory.create()
    response = client.delete(f"/api/material-classes/{mc.id}")
    assert response.status_code == 200


def test_delete_404(client):
    response = client.delete("/api/material-classes/9999")
    assert response.status_code == 404


class TestMaterialTypeResolution:
    def test_create_resolves_material_type_code(self, client):
        resp = client.post("/api/material-classes", json={"name": "В30", "material_type": "concrete"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "В30"
        assert body["material_type"] == "concrete"

    def test_list_emits_material_type_code(self, client, factories):
        factories.MaterialClassFactory.create(material_type_code="rebar", name="d10")
        resp = client.get("/api/material-classes")
        assert resp.status_code == 200
        rebars = [c for c in resp.json() if c["material_type"] == "rebar"]
        assert any(c["name"] == "d10" for c in rebars)

    def test_create_unknown_type_returns_422(self, client):
        resp = client.post("/api/material-classes", json={"name": "X", "material_type": "wood"})
        assert resp.status_code == 422
