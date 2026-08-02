class TestUnitsApi:
    def test_list_units(self, client):
        resp = client.get("/api/units")
        assert resp.status_code == 200
        codes = {u["code"] for u in resp.json()}
        assert {"TON", "KG", "M3", "L", "M2", "M", "PCS", "SET", "MON"} <= codes
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

    def test_m2_aliases_present(self, client):
        """Алиасы «м2»/«кв.м»/«м²» обязательны (AGENTS.md §4)."""
        units = client.get("/api/units").json()
        m2_id = next(u["id"] for u in units if u["code"] == "M2")
        resp = client.get(f"/api/units/{m2_id}/aliases")
        assert resp.status_code == 200
        raw = {a["raw_text"] for a in resp.json()}
        # м² NFKC-нормализуется в м2 — обе формы ведут к одному ключу
        assert "м2" in raw and "кв.м" in raw

    def test_unknown_unit_404(self, client):
        assert client.get("/api/units/999999/aliases").status_code == 404
