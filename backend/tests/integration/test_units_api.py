class TestUnitsApi:
    def test_list_units(self, client):
        resp = client.get("/api/units")
        assert resp.status_code == 200
        codes = {u["code"] for u in resp.json()}
        assert {"TON", "KG", "M3", "L", "M", "PCS"} <= codes
        ton = next(u for u in resp.json() if u["code"] == "TON")
        assert ton["dimension"] == "mass"
        assert ton["symbol"] == "т"

    def test_list_aliases_for_ton(self, client):
        units = client.get("/api/units").json()
        ton_id = next(u["id"] for u in units if u["code"] == "TON")
        resp = client.get(f"/api/units/{ton_id}/aliases")
        assert resp.status_code == 200
        raw = {a["raw_text"] for a in resp.json()}
        assert "т" in raw and "тонн" in raw

    def test_list_material_types(self, client):
        resp = client.get("/api/material-types")
        assert resp.status_code == 200
        by_code = {m["code"]: m for m in resp.json()}
        assert set(by_code) == {"concrete", "rebar", "other"}
        assert by_code["concrete"]["default_unit"]["code"] == "M3"
        assert by_code["other"]["default_unit"] is None
