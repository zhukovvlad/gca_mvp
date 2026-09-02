"""HTTP-контракт тендерного контура (спека §2.12, §2.13)."""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from models import Estimate, ImportJob, ImportJobStatus, UserRole
from tests.integration.test_estimates_api import finished_job, xlsx_bytes
from tests.payloads import position, proposal, round_payload

pytestmark = pytest.mark.integration

TENDERS = "/api/v1/tenders"


@pytest.fixture
def tender(committing_client, committing_factories, committing_db):
    obj = committing_factories.ObjectFactory.create()
    rc = committing_factories.RateClassFactory.create()
    committing_db.commit()
    created = committing_client.post(TENDERS, json={
        "object_id": obj.id, "title": "Генподряд", "tender_number": "Т-0001", "rate_class_id": rc.id,
    })
    assert created.status_code == 201, created.text
    card = created.json()
    rnd = committing_client.post(f"{TENDERS}/{card['id']}/rounds", json={"stage_no": 1})
    assert rnd.status_code == 201
    return {"id": card["id"], "round_id": rnd.json()["rounds"][0]["id"]}


@pytest.fixture
def round_stub(monkeypatch):
    from parser import ParseResult
    from services import import_pipeline

    holder = {"payload": None}

    def fake(_handle):
        return ParseResult(data=holder["payload"], parser_version="4.0.0", warnings=[])

    monkeypatch.setattr(import_pipeline, "parse_estimate", fake)

    def configure(payload):
        holder["payload"] = payload
    return configure


def upload_round(client, tender, *, content, replace=None, filename="сводная.xlsx"):
    data = {} if replace is None else {"replace": str(replace).lower()}
    return client.post(f"{TENDERS}/{tender['id']}/rounds/{tender['round_id']}/upload",
                       files={"file": (filename, content, "application/octet-stream")}, data=data)


def P_A():
    return proposal([position(job_title="Работа", unit="м2", unit_cost_total="10", total_cost_total="10")],
                    title="ООО А", inn="7700000001")


def P_B():
    return proposal([position(job_title="Работа", unit="м2", unit_cost_total="12", total_cost_total="12")],
                    title="ООО Б", inn="7700000002")


class TestRoundUpload:
    def test_upload_creates_round_job_and_all_estimates(self, committing_client, committing_db, tender, round_stub):
        round_stub(round_payload([P_A(), P_B()]))
        response = upload_round(committing_client, tender, content=xlsx_bytes())
        assert response.status_code == 202
        body = response.json()
        assert body["owner_type"] == "round" and body["round_id"] == tender["round_id"]

        job = finished_job(committing_client, response)
        assert job["status"] == ImportJobStatus.done.value
        assert job["estimates_created"] == 2
        assert len(job["estimate_ids"]) == 2
        assert "estimate_id" not in job

    def test_same_file_twice_is_idempotent_200(self, committing_client, tender, round_stub):
        round_stub(round_payload([P_A()]))
        first = upload_round(committing_client, tender, content=xlsx_bytes("same"))
        first_job = finished_job(committing_client, first)
        assert first_job["status"] == ImportJobStatus.done.value
        assert first_job["estimates_created"] == 1
        second = upload_round(committing_client, tender, content=xlsx_bytes("same"))
        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]

    def test_different_file_without_replace_is_409(self, committing_client, tender, round_stub):
        round_stub(round_payload([P_A()]))
        first_job = finished_job(committing_client, upload_round(committing_client, tender, content=xlsx_bytes("a")))
        assert first_job["status"] == ImportJobStatus.done.value
        assert first_job["estimates_created"] == 1
        assert upload_round(committing_client, tender, content=xlsx_bytes("b")).status_code == 409

    def test_already_loaded_conflict_has_replace_required_code(self, committing_client, tender, round_stub):
        """Находка внешнего ревью PR #33 (finding 2): развилка «нужна
        замена» несёт СВОЙ код `replace_required` — до фикса три разных
        причины 409 (идёт другой импорт / уже загружено / гонка на индексе)
        были неотличимым голым `HTTPException(409, ...)`, и фронт не мог
        решить, когда предлагать диалог замены."""
        round_stub(round_payload([P_A()]))
        first_job = finished_job(committing_client, upload_round(committing_client, tender, content=xlsx_bytes("a")))
        assert first_job["status"] == ImportJobStatus.done.value

        response = upload_round(committing_client, tender, content=xlsx_bytes("b"))

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "replace_required"

    def test_running_import_conflict_has_active_import_code(
        self, committing_client, committing_db, committing_factories, tender, round_stub
    ):
        """Находка внешнего ревью PR #33 (finding 2): второй запрос видит
        задание ДРУГОГО запроса как ЗАПУЩЕННЫЙ импорт — это не то же самое,
        что «раунд уже загружен» выше, и код обязан быть ДРУГИМ
        (`active_import`), иначе клиент предложил бы опасную замену там, где
        нужно просто подождать завершения чужого импорта."""
        committing_factories.ImportJobFactory.create(
            contract=None, round_id=tender["round_id"], status=ImportJobStatus.parsing.value, file_key="k-running",
        )
        committing_db.commit()

        response = upload_round(committing_client, tender, content=xlsx_bytes())

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "active_import"

    def test_active_round_index_race_maps_to_active_import_code(
        self, committing_client, committing_db, committing_factories, tender, round_stub, monkeypatch
    ):
        """Находка внешнего ревью PR #33 (finding 2): третья причина 409 —
        гонка на partial unique index `uq_import_jobs_active_round`, вторая
        линия защиты ПОСЛЕ `active_round_job()`. Проверяем СНЯТИЕМ первой
        защиты (мок возвращает `None`, как будто активного job нет), а не
        притворной параллельностью — тот же приём, что у любого теста на
        гонку: доказывать защиту её снятием, а не совпадением по времени.
        С первой защитой снятой INSERT реально упирается в partial unique
        index, и именно ветка `except IntegrityError` обязана поймать отказ
        и отдать тот же код `active_import`, что и штатная проверка выше —
        причина отказа для клиента одна и та же."""
        committing_factories.ImportJobFactory.create(
            contract=None, round_id=tender["round_id"], status=ImportJobStatus.parsing.value, file_key="k-race",
        )
        committing_db.commit()
        from routers import tenders as tenders_router
        monkeypatch.setattr(tenders_router.crud_tenders, "active_round_job", lambda db, round_id: None)

        response = upload_round(committing_client, tender, content=xlsx_bytes())

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "active_import"

    def test_replace_requires_admin(self, committing_client, tender, round_stub):
        committing_client.auth_state["role"] = UserRole.member
        response = upload_round(committing_client, tender, content=xlsx_bytes(), replace=True)
        assert response.status_code == 403

    def test_member_can_upload(self, committing_client, tender, round_stub):
        round_stub(round_payload([P_A()]))
        committing_client.auth_state["role"] = UserRole.member
        assert upload_round(committing_client, tender, content=xlsx_bytes()).status_code == 202

    def test_round_of_another_tender_in_url_is_404(self, committing_client, committing_factories, committing_db, tender, round_stub):
        other = committing_factories.TenderFactory.create()
        committing_db.commit()
        response = committing_client.post(
            f"{TENDERS}/{other.id}/rounds/{tender['round_id']}/upload",
            files={"file": ("x.xlsx", xlsx_bytes(), "application/octet-stream")},
        )
        assert response.status_code == 404

    def test_same_sha_after_participant_deleted_is_409_not_200(self, committing_client, committing_db, tender, round_stub):
        round_stub(round_payload([P_A(), P_B()]))
        first = upload_round(committing_client, tender, content=xlsx_bytes("same"))
        first_job = finished_job(committing_client, first)
        assert first_job["status"] == ImportJobStatus.done.value
        assert first_job["estimates_created"] == 2
        card = committing_client.get(f"{TENDERS}/{tender['id']}").json()
        package_b = next(p for p in card["participants"] if p["inn"] == "7700000002")["package_id"]
        preview = committing_client.delete(f"{TENDERS}/{tender['id']}/participants/{package_b}")
        assert preview.status_code == 409 and preview.json()["detail"]["code"] == "confirmation_required"
        token = preview.json()["detail"]["confirmation_token"]
        assert committing_client.delete(f"{TENDERS}/{tender['id']}/participants/{package_b}",
                                        params={"confirmation_token": token}).status_code == 204

        second = upload_round(committing_client, tender, content=xlsx_bytes("same"))
        assert second.status_code == 409


class TestParticipantDeletionProtocol:
    def test_stale_token_returns_fresh_preview_and_deletes_nothing(self, committing_client, committing_db, tender, round_stub):
        round_stub(round_payload([P_A()]))
        job = finished_job(committing_client, upload_round(committing_client, tender, content=xlsx_bytes()))
        assert job["status"] == ImportJobStatus.done.value
        assert job["estimates_created"] == 1
        card = committing_client.get(f"{TENDERS}/{tender['id']}").json()
        package = card["participants"][0]["package_id"]
        response = committing_client.delete(f"{TENDERS}/{tender['id']}/participants/{package}",
                                            params={"confirmation_token": "stale"})
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "confirmation_required"
        assert response.json()["detail"]["estimates_count"] == 1
        committing_db.expire_all()
        assert committing_db.execute(sa.select(sa.func.count()).select_from(Estimate)).scalar_one() == 1

    def test_member_cannot_delete_participant(self, committing_client, tender):
        committing_client.auth_state["role"] = UserRole.member
        assert committing_client.delete(f"{TENDERS}/{tender['id']}/participants/1").status_code == 403


class TestDeletionPurgesFiles:
    """Файлы удаляются ПОСЛЕ коммита, best-effort, по одному ключу (спека §2.11,
    DoD 9). Проверяется физически — через `tmp_storage.exists`, а не по списку
    возвращённых ключей."""

    def _two_jobs(self, committing_client, tender, round_stub):
        round_stub(round_payload([P_A()]))
        first = upload_round(committing_client, tender, content=xlsx_bytes("a"))
        first_job = finished_job(committing_client, first)
        assert first_job["status"] == ImportJobStatus.done.value
        assert first_job["estimates_created"] == 1
        second = upload_round(committing_client, tender, content=xlsx_bytes("b"), replace=True)
        second_job = finished_job(committing_client, second)
        assert second_job["status"] == ImportJobStatus.done.value
        assert second_job["estimates_created"] == 1
        return first.json()["id"], second.json()["id"]

    def _file_keys(self, committing_db, job_ids):
        committing_db.expire_all()
        return [committing_db.get(ImportJob, j).file_key for j in job_ids]

    def test_delete_round_purges_all_history_files(self, committing_client, committing_db, tmp_storage, tender, round_stub):
        job_ids = self._two_jobs(committing_client, tender, round_stub)
        keys = self._file_keys(committing_db, job_ids)
        assert all(tmp_storage.exists(k) for k in keys)

        response = committing_client.delete(f"{TENDERS}/{tender['id']}/rounds/{tender['round_id']}")

        assert response.status_code == 204
        assert not any(tmp_storage.exists(k) for k in keys)

    def test_delete_tender_purges_files_of_all_rounds(self, committing_client, committing_db, tmp_storage, tender, round_stub):
        job_ids = self._two_jobs(committing_client, tender, round_stub)
        r2 = committing_client.post(f"{TENDERS}/{tender['id']}/rounds", json={"stage_no": 2}).json()
        r2_id = next(r["id"] for r in r2["rounds"] if r["stage_no"] == 2)
        third = committing_client.post(f"{TENDERS}/{tender['id']}/rounds/{r2_id}/upload",
                                       files={"file": ("c.xlsx", xlsx_bytes("c"), "application/octet-stream")})
        third_job = finished_job(committing_client, third)
        assert third_job["status"] == ImportJobStatus.done.value
        assert third_job["estimates_created"] == 1
        keys = self._file_keys(committing_db, [*job_ids, third.json()["id"]])

        assert committing_client.delete(f"{TENDERS}/{tender['id']}").status_code == 204
        assert not any(tmp_storage.exists(k) for k in keys)

    def test_one_broken_key_does_not_stop_the_rest(self, committing_client, committing_db, tmp_storage, tender, round_stub):
        """Испорченный file_key поднимает StorageKeyError; purge изолирует
        ошибку по ключу — остальные удалены, ответ 204."""
        first_id, second_id = self._two_jobs(committing_client, tender, round_stub)
        good_key = self._file_keys(committing_db, [second_id])[0]
        committing_db.execute(sa.update(ImportJob).where(ImportJob.id == first_id).values(file_key="not-a-valid-key"))
        committing_db.commit()

        assert committing_client.delete(f"{TENDERS}/{tender['id']}/rounds/{tender['round_id']}").status_code == 204
        assert not tmp_storage.exists(good_key)

    def test_domain_refusal_deletes_no_files(self, committing_client, committing_db, committing_factories, tmp_storage, tender, round_stub):
        job_ids = self._two_jobs(committing_client, tender, round_stub)
        keys = self._file_keys(committing_db, job_ids)
        committing_factories.ImportJobFactory.create(contract=None, round_id=tender["round_id"],
                                                     status=ImportJobStatus.parsing.value, file_key="k-active")
        committing_db.commit()

        response = committing_client.delete(f"{TENDERS}/{tender['id']}/rounds/{tender['round_id']}")

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "active_import"
        assert all(tmp_storage.exists(k) for k in keys)


class TestRights:
    @pytest.mark.parametrize(("method", "path", "payload"), [
        ("post", TENDERS, {"object_id": 1, "title": "x", "tender_number": "y"}),
        ("patch", f"{TENDERS}/1", {"title": "x"}),
        ("delete", f"{TENDERS}/1", None),
        ("post", f"{TENDERS}/1/rounds", {"stage_no": 1}),
        ("patch", f"{TENDERS}/1/rounds/1", {"label": "x"}),
        ("delete", f"{TENDERS}/1/rounds/1", None),
    ])
    def test_member_cannot_change(self, committing_client, method, path, payload):
        committing_client.auth_state["role"] = UserRole.member
        response = getattr(committing_client, method)(path, json=payload) if payload is not None else getattr(committing_client, method)(path)
        assert response.status_code == 403

    def test_member_can_read(self, committing_client, tender):
        committing_client.auth_state["role"] = UserRole.member
        assert committing_client.get(TENDERS).status_code == 200
        assert committing_client.get(f"{TENDERS}/{tender['id']}").status_code == 200


class TestCardShape:
    def test_cells_are_the_rectangle_even_for_a_late_participant(self, committing_client, tender, round_stub):
        """Участник Б появляется только во втором раунде — ячейка (раунд 1, Б)
        обязана быть с offer_id = null (§2.13)."""
        round_stub(round_payload([P_A()]))
        r1_job = finished_job(committing_client, upload_round(committing_client, tender, content=xlsx_bytes("r1")))
        assert r1_job["status"] == ImportJobStatus.done.value
        assert r1_job["estimates_created"] == 1
        r2 = committing_client.post(f"{TENDERS}/{tender['id']}/rounds", json={"stage_no": 2}).json()
        r2_id = next(r["id"] for r in r2["rounds"] if r["stage_no"] == 2)
        round_stub(round_payload([P_A(), P_B()]))
        r2_job = finished_job(committing_client, committing_client.post(
            f"{TENDERS}/{tender['id']}/rounds/{r2_id}/upload",
            files={"file": ("r2.xlsx", xlsx_bytes("r2"), "application/octet-stream")}))
        assert r2_job["status"] == ImportJobStatus.done.value
        assert r2_job["estimates_created"] == 2

        card = committing_client.get(f"{TENDERS}/{tender['id']}").json()
        assert len(card["cells"]) == 4
        b = next(p for p in card["participants"] if p["inn"] == "7700000002")["package_id"]
        cell = next(c for c in card["cells"] if c["round_id"] == tender["round_id"] and c["package_id"] == b)
        assert cell["offer_id"] is None and cell["estimate_id"] is None
        # Спека этапного разноса §2.6, читаная НАД ПРОВОДОМ (ни один прежний
        # тест не вытягивал `unallocated_pending_sections` из настоящего HTTP-
        # ответа карточки, только из вызова crud напрямую): у обоих раундов
        # ЕСТЬ offer-сметы, а `P_A`/`P_B` несут плоскую ведомость без единого
        # раздела — счётчик обязан быть 0, не `null`.
        assert [r["unallocated_pending_sections"] for r in card["rounds"]] == [0, 0]


class TestContractJobResponseUnchanged:
    def test_contract_job_keeps_estimate_id_and_gains_owner_type(
        self, committing_client, committing_db, committing_factories, monkeypatch
    ):
        from parser import ParseResult
        from services import import_pipeline
        from tests.integration.test_estimates_api import upload
        from tests.payloads import payload_for

        contract = committing_factories.ContractFactory.create()
        committing_db.commit()
        # monkeypatch, не присваивание модулю: подмена обязана откатиться после
        # теста, иначе утекла бы в соседние файлы прогона.
        monkeypatch.setattr(
            import_pipeline, "parse_estimate",
            lambda _h: ParseResult(data=payload_for(contract), parser_version="4.0.0", warnings=[]),
        )
        response = upload(committing_client, content=xlsx_bytes(), contract_id=contract.id)
        job = finished_job(committing_client, response)
        assert job["owner_type"] == "contract"
        assert job["estimate_id"] is not None
        assert job["estimates_created"] == 1
