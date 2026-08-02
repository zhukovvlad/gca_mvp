def test_create_reference_price(client, factories, db_session):
    from models import UnitOfMeasure
    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create()
    m3_id = db_session.query(UnitOfMeasure).filter_by(code="M3").one().id

    response = client.post(
        "/api/reference-prices",
        json={
            "project_id": project.id,
            "material_class_id": mc.id,
            "unit_id": m3_id,
            "price": 8500.0,
            "period_start": "2026-01-01",
            "period_end": "2026-12-31",
            "source": "контракт",
        },
    )
    assert response.status_code == 200


def test_list_reference_prices_includes_relations(client, factories):
    factories.ReferencePriceFactory.create()

    response = client.get("/api/reference-prices")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert "project_name" in body[0]
    assert "material_class_name" in body[0]
    # material_type must be the bare code string, not the nested MaterialType object
    assert body[0]["material_type"] == "concrete"


def test_filter_by_project(client, factories):
    p1 = factories.ProjectFactory.create()
    p2 = factories.ProjectFactory.create()
    factories.ReferencePriceFactory.create(project=p1)
    factories.ReferencePriceFactory.create(project=p2)

    response = client.get(f"/api/reference-prices?project_id={p1.id}")
    assert response.status_code == 200
    assert all(rp["project_id"] == p1.id for rp in response.json())


def test_delete_reference_price(client, factories):
    rp = factories.ReferencePriceFactory.create()
    response = client.delete(f"/api/reference-prices/{rp.id}")
    assert response.status_code == 200


def test_update_reference_price(client, factories):
    rp = factories.ReferencePriceFactory.create(price=8000.0, source="старый")

    response = client.patch(
        f"/api/reference-prices/{rp.id}",
        json={
            "price": 9500.0,
            "period_start": "2026-03-01",
            "period_end": "2026-09-30",
            "source": "новый контракт",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["price"] == 9500.0
    assert body["period_start"] == "2026-03-01"
    assert body["period_end"] == "2026-09-30"
    assert body["source"] == "новый контракт"


def test_update_reference_price_partial(client, factories):
    rp = factories.ReferencePriceFactory.create(
        price=8000.0,
        period_start="2026-01-01",
        period_end="2026-12-31",
    )

    response = client.patch(
        f"/api/reference-prices/{rp.id}",
        json={"price": 7000.0},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["price"] == 7000.0
    assert body["period_start"] == "2026-01-01"
    assert body["period_end"] == "2026-12-31"


def test_update_reference_price_not_found(client):
    response = client.patch(
        "/api/reference-prices/99999",
        json={"price": 1000.0},
    )
    assert response.status_code == 404


def test_update_reference_price_clears_source(client, factories):
    rp = factories.ReferencePriceFactory.create(source="договор")

    response = client.patch(
        f"/api/reference-prices/{rp.id}",
        json={"source": None},
    )
    assert response.status_code == 200
    assert response.json()["source"] is None


def test_update_reference_price_rejects_null_price(client, factories):
    rp = factories.ReferencePriceFactory.create()

    response = client.patch(
        f"/api/reference-prices/{rp.id}",
        json={"price": None},
    )
    assert response.status_code == 422


def test_update_reference_price_rejects_null_period(client, factories):
    rp = factories.ReferencePriceFactory.create()

    response = client.patch(
        f"/api/reference-prices/{rp.id}",
        json={"period_start": None},
    )
    assert response.status_code == 422


def test_update_reference_price_response_includes_relations(client, factories):
    rp = factories.ReferencePriceFactory.create(price=5000.0)

    response = client.patch(
        f"/api/reference-prices/{rp.id}",
        json={"price": 5500.0},
    )
    assert response.status_code == 200
    body = response.json()
    assert "project_name" in body
    assert "material_class_name" in body
    # material_type must be the bare code string, not the nested MaterialType object
    assert body["material_type"] == "concrete"

