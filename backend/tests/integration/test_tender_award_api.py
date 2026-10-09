"""HTTP-контракт отметки победителя и договора из КП (спека Б2 §2.6, §2.7).

Тесты идут через настоящие commit-ы: команды отказывают ключом с откатом
сессии, а фоновая задача импорта работает на собственных сессиях. Клиент
подставляет настоящих пользователей (`awarded_by` — внешний ключ на `users`),
фабрику сессий и хранилище. Отказ проверяется статусом, кодом, текстом и
неизменённой базой, а не подстрокой.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.orm.attributes import flag_modified

from crud import tender_awards as crud_awards
from models import (
    Contract,
    Contractor,
    Estimate,
    EstimateRawData,
    ImportJob,
    ImportJobStatus,
    ObjectModel,
    Offer,
    OfferPackage,
    TenderAward,
    UserRole,
)
from parser.constants import (
    JSON_KEY_CONTRACTOR_INN,
    JSON_KEY_LOTS,
    JSON_KEY_PROPOSALS,
    JSON_KEY_TOTAL_COST_INCLUDING_VAT,
)
from services import import_pipeline
from services.category_resolution import CategoryResolver
from services.round_import import import_round
from services.unit_resolution import UnitResolver
from tests.integration.test_estimates_api import xlsx_bytes
from tests.integration.test_import_pipeline import fake_parse
from tests.payloads import position, proposal, round_payload, summary_line

pytestmark = pytest.mark.integration

TENDERS = "/api/v1/tenders"
CONTRACTS = "/api/v1/contracts"

INN_A, INN_B, INN_V = "7700000001", "7700000002", "7700000003"
STAGE_BYTES = xlsx_bytes("stage-two")
STAGE_FILENAME = "Этап 2 сводная.xlsx"

# Независимые литералы: эталон не вычисляется кодом, который проверяется.
TENDER_LIST_KEYS = {
    "id", "tender_number", "title", "object_id", "object_title", "rate_class_id", "rate_class_title",
    "rounds_count", "participants_count", "created_at",
}
TENDER_CARD_KEYS_BEFORE = {
    "id", "tender_number", "title", "notes", "object_id", "object_title", "object_address",
    "rate_class_id", "rate_class_title", "created_at", "rounds", "participants", "cells",
}
CONTRACT_CARD_KEYS_BEFORE = {
    "id", "contract_number", "title", "object_id", "object_title", "contractor_id", "contractor_title",
    "rate_class_id", "rate_class_title", "signer", "signed_date", "total_amount", "estimates_count",
    "created_at", "updated_at", "notes", "advance_pct", "advance_note", "bank_guarantee_pct",
    "bank_guarantee_note", "retention_pct", "retention_note", "estimates",
}
CONTRACT_LIST_KEYS = {
    "id", "contract_number", "title", "object_id", "object_title", "contractor_id", "contractor_title",
    "rate_class_id", "rate_class_title", "signer", "signed_date", "total_amount", "estimates_count",
    "created_at", "updated_at",
}
AWARD_KEYS = {
    "id", "offer_id", "package_id", "contractor_id", "contractor_title", "contractor_inn", "round_id",
    "stage_no", "estimate_id", "total_including_vat", "awarded_at", "awarded_by_email", "contract",
}
HISTORY_KEYS = {
    "award_id", "kind", "package_id", "contractor_title", "awarded_at", "not_concluded_on", "note",
    "by_email", "is_active",
}
CANDIDATE_KEYS = {
    "id", "contract_number", "signed_date", "object_title", "contractor_title", "base_total_including_vat",
}
TENDER_BASIS_KEYS = {
    "award_id", "tender_id", "tender_number", "tender_title", "round_id", "stage_no", "offer_id",
}

NOT_ACTIVE_TEXT = "Отметка уже не действующая — обновите карточку тендера."
HAS_CONTRACT_TEXT = (
    "Нельзя: по этой отметке заключён договор № {}. Сначала удалите договор или отвяжите его от тендера."
)
NOT_FINAL_TEXT = "Отметить победителя можно только на КП финального этапа — этап 1 не последний."
NOT_LINKED_TEXT = "Договор не привязан к тендеру."
ESTIMATE_IS_COPY_TEXT = (
    "Нельзя: смета договора — копия КП победителя этого тендера. Если победитель не тот, удалите "
    "договор вместе со сметой; если загрузили подписанную смету вместо копии, отвязка станет доступна."
)
ROUND_BLOCKED_TEXT = (
    "Нельзя добавить этап: в тендере отмечен победитель. Снимите отметку или отметьте, что договор не "
    "заключён."
)
NUMBER_TAKEN_TEXT = "Договор с таким номером уже есть."


def _summary(total: str) -> dict:
    return {JSON_KEY_TOTAL_COST_INCLUDING_VAT: summary_line("ИТОГО, руб. с учетом НДС", total)}


def _participant(inn: str, title: str, price: str = "10"):
    return proposal(
        [position(
            job_title="Работа", unit="м2", quantity=1, suggested_quantity=1,
            unit_cost_total=price, total_cost_total=price,
        )],
        title=title, inn=inn, summary=_summary(price + ".00"),
    )


@pytest.fixture
def scene(committing_db, committing_factories, committing_session_factory, tmp_storage):
    """Тендер: этап 1 (А, Б, В) и финальный этап 2 (А, Б) настоящим `import_round`;
    участник Г — оферта финального этапа без КП. Задание этапа 2 `done` с
    настоящим файлом в хранилище. Чужой тендер `foreign`. Пользователи: `admin`
    и `member`. Отметка не ставится: её ставят тесты."""
    f = committing_factories
    db = committing_db
    tender = f.TenderFactory.create()
    r1 = f.TenderRoundFactory.create(tender=tender, stage_no=1)
    r2 = f.TenderRoundFactory.create(tender=tender, stage_no=2)
    foreign = f.TenderFactory.create()
    foreign_round = f.TenderRoundFactory.create(tender=foreign, stage_no=1)
    object_class = f.RateClassFactory.create()
    other_object = f.ObjectFactory.create()
    db.flush()
    db.get(ObjectModel, tender.object_id).rate_class_id = object_class.id

    payload2 = round_payload([_participant(INN_A, "ООО А", "11"), _participant(INN_B, "ООО Б", "20")])
    key = tmp_storage.save(STAGE_BYTES)
    stage_job = ImportJob(
        round_id=r2.id, filename=STAGE_FILENAME, file_key=key,
        file_sha256=hashlib.sha256(STAGE_BYTES).hexdigest(), status=ImportJobStatus.done.value,
        estimates_created=2,
    )
    db.add(stage_job)
    db.flush()

    def load(rnd, data, job_id=None):
        import_round(
            db, tender_round=rnd, data=data, parser_version="4.0.0", import_job_id=job_id,
            replace=False, unit_resolver=UnitResolver(db), category_resolver=CategoryResolver.from_db(db),
        )

    load(r1, round_payload([
        _participant(INN_A, "ООО А"), _participant(INN_B, "ООО Б"), _participant(INN_V, "ООО В"),
    ]))
    load(r2, payload2, stage_job.id)
    load(foreign_round, round_payload([_participant("7700000009", "ООО Чужой")]))
    package_d = f.OfferPackageFactory.create(tender=tender)
    offer_d = f.OfferFactory.create(round=r2, package=package_d)
    admin = f.UserFactory.create(role=UserRole.admin)
    member = f.UserFactory.create(role=UserRole.member)
    db.commit()

    def offer_id(round_id, inn):
        return db.execute(
            sa.select(Offer.id).join(OfferPackage, OfferPackage.id == Offer.package_id)
            .join(Contractor, Contractor.id == OfferPackage.contractor_id)
            .where(Offer.round_id == round_id, Contractor.inn == inn)
        ).scalar_one()

    def contractor_id(inn):
        return db.execute(sa.select(Contractor.id).where(Contractor.inn == inn)).scalar_one()

    def package_id(inn):
        return db.execute(
            sa.select(OfferPackage.id).join(Contractor, Contractor.id == OfferPackage.contractor_id)
            .where(OfferPackage.tender_id == tender.id, Contractor.inn == inn)
        ).scalar_one()

    return SimpleNamespace(
        sf=committing_session_factory, db=db, f=f, storage=tmp_storage, payload=payload2,
        tender_id=tender.id, tender_number=tender.tender_number, object_id=tender.object_id,
        r1_id=r1.id, r2_id=r2.id, foreign_id=foreign.id, other_object_id=other_object.id,
        stage_key=key, stage_job_id=stage_job.id,
        offer_a=offer_id(r2.id, INN_A), offer_b=offer_id(r2.id, INN_B), offer_v1=offer_id(r1.id, INN_V),
        offer_a1=offer_id(r1.id, INN_A), offer_d=offer_d.id, foreign_offer=offer_id(foreign_round.id, "7700000009"),
        contractor_a=contractor_id(INN_A), contractor_b=contractor_id(INN_B),
        package_a=package_id(INN_A), package_d=package_d.id,
        package_d_title=db.execute(
            sa.select(Contractor.title).join(OfferPackage, OfferPackage.contractor_id == Contractor.id)
            .where(OfferPackage.id == package_d.id)
        ).scalar_one(),
        admin=SimpleNamespace(id=admin.id, role=UserRole.admin, is_active=True, email=admin.email),
        member=SimpleNamespace(id=member.id, role=UserRole.member, is_active=True, email=member.email),
    )


@pytest.fixture
def api(scene, committing_session_factory, tmp_storage, monkeypatch):
    """Клиент, у которого запросы РЕАЛЬНО коммитят; пользователь — `scene.admin`
    или `scene.member` (`api.as_member()` / `api.as_admin()`); парсер файла этапа
    подменён разбором финального этапа."""
    from fastapi.testclient import TestClient

    from auth import get_current_user
    from database import get_db, get_session_factory
    from main import app
    from storage import get_storage

    holder = {"user": scene.admin}

    def override_get_db():
        db = committing_session_factory()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(import_pipeline, "parse_estimate", fake_parse(scene.payload))
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: holder["user"]
    app.dependency_overrides[get_session_factory] = lambda: committing_session_factory
    app.dependency_overrides[get_storage] = lambda: tmp_storage
    token = "test-csrf-token"
    with TestClient(app, headers={"X-CSRF-Token": token}, raise_server_exceptions=True) as client:
        client.cookies.set("csrf_token", token)
        client.as_member = lambda: holder.__setitem__("user", scene.member)
        client.as_admin = lambda: holder.__setitem__("user", scene.admin)
        yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _t(s) -> str:
    return f"{TENDERS}/{s.tender_id}"


def _award(s, offer=None) -> int:
    """Действующая отметка прямой командой (по умолчанию на КП участника А этапа 2)."""
    with s.sf() as db:
        crud_awards.award_winner(db, s.tender_id, offer_id=offer or s.offer_a, user_id=s.admin.id)
    return _award_ids(s)[-1]


def _award_ids(s) -> list[int]:
    with s.sf() as db:
        return list(db.execute(sa.select(TenderAward.id).where(TenderAward.tender_id == s.tender_id)
                               .order_by(TenderAward.id)).scalars())


def _close(s, award_id) -> None:
    with s.sf() as db:
        crud_awards.mark_not_concluded(
            db, s.tender_id, award_id, not_concluded_on=dt.date(2026, 2, 1), note=None, user_id=s.admin.id,
        )


def _contract(s, *, number="ГП-1", object_id=None, contractor_id=None) -> int:
    contract = s.f.ContractFactory.create(
        object=s.db.get(ObjectModel, object_id if object_id is not None else s.object_id),
        contractor=s.db.get(Contractor, contractor_id if contractor_id is not None else s.contractor_a),
        contract_number=number,
    )
    s.db.commit()
    return contract.id


def _link(s, contract_id, award_id) -> None:
    with s.sf() as db:
        crud_awards.link_contract(db, s.tender_id, award_id, contract_id=contract_id)


def _snapshot(s) -> tuple:
    """Всё, что маршруты отметки меняют: отметки, договоры с их основанием, задания, сметы, файлы."""
    with s.sf() as db:
        awards = tuple(db.execute(
            sa.select(TenderAward.id, TenderAward.not_concluded_on, TenderAward.not_concluded_note)
            .order_by(TenderAward.id)
        ).all())
        contracts = tuple(db.execute(
            sa.select(Contract.id, Contract.tender_award_id, Contract.object_id, Contract.contract_number)
            .order_by(Contract.id)
        ).all())
        jobs = tuple(db.execute(sa.select(ImportJob.id, ImportJob.status).order_by(ImportJob.id)).all())
        estimates = tuple(db.execute(sa.select(Estimate.id, Estimate.source_award_id).order_by(Estimate.id)).all())
    files = tuple(sorted(p.name for p in s.storage.root.iterdir())) if s.storage.root.exists() else ()
    return awards, contracts, jobs, estimates, files


def _refused(response, status, code, message=None):
    """Отказ команды: статус, `detail` объектом `{code, message}` и (если задан) текст."""
    assert response.status_code == status, response.text
    detail = response.json()["detail"]
    assert set(detail) == {"code", "message"}
    assert detail["code"] == code
    if message is not None:
        assert detail["message"] == message
    return detail


def _from_kp_body(number="ГП-КП-1", **overrides) -> dict:
    body = {
        "contract_number": number, "signed_date": "2026-10-01", "title": "Договор из КП",
        "signer": "Петров П.П.", "total_amount": "1234.50", "notes": "примечание", "advance_pct": "30",
        "advance_note": "аванс", "retention_pct": "5",
    }
    body.update(overrides)
    return body


def _create_from_kp(api, s, award_id, **overrides):
    return api.post(f"{_t(s)}/awards/{award_id}/contract", json=_from_kp_body(**overrides))


def _job_status(s, job_id) -> str:
    with s.sf() as db:
        return db.execute(sa.select(ImportJob.status).where(ImportJob.id == job_id)).scalar_one()


# ---------------------------------------------------------------------------
#  POST /awards
# ---------------------------------------------------------------------------

class TestAwardCreate:
    def test_marks_the_winner_and_answers_the_tender_card(self, api, scene):
        response = api.post(f"{_t(scene)}/awards", json={"offer_id": scene.offer_a})

        assert response.status_code == 201, response.text
        card = response.json()
        assert card["id"] == scene.tender_id
        award = card["award"]
        assert set(award) == AWARD_KEYS
        assert award["id"] == _award_ids(scene)[0]
        assert award["offer_id"] == scene.offer_a
        assert award["contractor_id"] == scene.contractor_a
        assert award["stage_no"] == 2
        assert award["awarded_by_email"] == scene.admin.email
        assert award["contract"] is None
        assert award["total_including_vat"] == "11.00"
        assert [(e["kind"], e["is_active"]) for e in card["award_history"]] == [("awarded", True)]
        assert set(card["award_history"][0]) == HISTORY_KEYS

    def test_member_is_forbidden_and_nothing_changes(self, api, scene):
        before = _snapshot(scene)
        api.as_member()
        response = api.post(f"{_t(scene)}/awards", json={"offer_id": scene.offer_a})
        assert response.status_code == 403
        assert _snapshot(scene) == before

    def test_an_offer_of_another_tender_is_not_found(self, api, scene):
        before = _snapshot(scene)
        response = api.post(f"{_t(scene)}/awards", json={"offer_id": scene.foreign_offer})
        assert response.status_code == 404
        assert _snapshot(scene) == before

    def test_an_unknown_tender_is_not_found(self, api, scene):
        response = api.post(f"{TENDERS}/987654/awards", json={"offer_id": scene.offer_a})
        assert response.status_code == 404

    @pytest.mark.parametrize("body", [{}, {"offer_id": "abc"}, {"offer_id": None}])
    def test_a_body_without_a_valid_offer_is_422(self, api, scene, body):
        before = _snapshot(scene)
        assert api.post(f"{_t(scene)}/awards", json=body).status_code == 422
        assert _snapshot(scene) == before

    def test_a_second_winner_is_refused_with_award_exists(self, api, scene):
        _award(scene)
        before = _snapshot(scene)
        response = api.post(f"{_t(scene)}/awards", json={"offer_id": scene.offer_b})
        _refused(
            response, 409, "award_exists",
            "В тендере уже отмечен победитель — ООО А. Чтобы отметить другого, снимите отметку или "
            "отметьте, что договор не заключён.",
        )
        assert _snapshot(scene) == before

    def test_an_offer_of_a_non_final_stage_is_refused(self, api, scene):
        before = _snapshot(scene)
        response = api.post(f"{_t(scene)}/awards", json={"offer_id": scene.offer_v1})
        _refused(response, 409, "award_not_final_stage", NOT_FINAL_TEXT)
        assert _snapshot(scene) == before

    def test_an_offer_without_kp_is_refused(self, api, scene):
        before = _snapshot(scene)
        response = api.post(f"{_t(scene)}/awards", json={"offer_id": scene.offer_d})
        _refused(
            response, 409, "award_no_kp",
            f"У участника «{scene.package_d_title}» нет КП в этом этапе — отметить его нельзя.",
        )
        assert _snapshot(scene) == before

    def test_a_kp_without_inn_is_refused_as_unidentified(self, api, scene):
        with scene.sf() as db:
            raw = db.execute(
                sa.select(EstimateRawData).join(Estimate, Estimate.id == EstimateRawData.estimate_id)
                .where(Estimate.offer_id == scene.offer_a)
            ).scalar_one()
            data = raw.raw_data
            for lot in data[JSON_KEY_LOTS].values():
                for block in lot[JSON_KEY_PROPOSALS].values():
                    block[JSON_KEY_CONTRACTOR_INN] = ""
            raw.raw_data = data
            flag_modified(raw, "raw_data")
            db.commit()
        before = _snapshot(scene)
        response = api.post(f"{_t(scene)}/awards", json={"offer_id": scene.offer_a})
        _refused(
            response, 409, "award_kp_unidentified",
            "КП участника «ООО А» не опознаётся в разборе этапа 2: в нём нет ИНН участника либо их "
            "несколько. Отметить нельзя — загрузите файл этапа заново.",
        )
        assert _snapshot(scene) == before

    def test_an_active_import_of_the_round_is_refused(self, api, scene):
        job = scene.f.ImportJobFactory.create(
            contract=None, round_id=scene.r2_id, status=ImportJobStatus.parsing.value,
        )
        scene.db.commit()
        before = _snapshot(scene)
        response = api.post(f"{_t(scene)}/awards", json={"offer_id": scene.offer_a})
        assert response.status_code == 409
        # Код, текст контура и его контекст `job_id` — целиком, а не один `code`.
        assert response.json()["detail"] == {
            "code": "active_import",
            "message": (
                f"Импорт раунда выполняется (задание {job.id}, статус «parsing»). "
                "Дождитесь завершения и повторите."
            ),
            "job_id": job.id,
        }
        assert _snapshot(scene) == before


# ---------------------------------------------------------------------------
#  DELETE /awards/{aid}
# ---------------------------------------------------------------------------

class TestAwardRemove:
    def test_removes_the_active_award_without_a_trace(self, api, scene):
        award_id = _award(scene)
        response = api.delete(f"{_t(scene)}/awards/{award_id}")
        assert response.status_code == 200, response.text
        card = response.json()
        assert card["id"] == scene.tender_id
        assert card["award"] is None
        assert card["award_history"] == []
        assert _award_ids(scene) == []

    def test_member_is_forbidden_and_the_award_stays(self, api, scene):
        award_id = _award(scene)
        before = _snapshot(scene)
        api.as_member()
        assert api.delete(f"{_t(scene)}/awards/{award_id}").status_code == 403
        assert _snapshot(scene) == before

    def test_an_award_of_another_tender_in_the_path_is_not_found(self, api, scene):
        award_id = _award(scene)
        before = _snapshot(scene)
        response = api.delete(f"{TENDERS}/{scene.foreign_id}/awards/{award_id}")
        assert response.status_code == 404
        assert _snapshot(scene) == before

    def test_a_gone_award_is_not_found(self, api, scene):
        assert api.delete(f"{_t(scene)}/awards/987654").status_code == 404

    def test_an_award_with_a_contract_is_refused_with_its_number(self, api, scene):
        award_id = _award(scene)
        _link(scene, _contract(scene, number="ГП-777"), award_id)
        before = _snapshot(scene)
        response = api.delete(f"{_t(scene)}/awards/{award_id}")
        _refused(response, 409, "award_has_contract", HAS_CONTRACT_TEXT.format("ГП-777"))
        assert _snapshot(scene) == before

    def test_a_closed_award_is_refused_as_not_active(self, api, scene):
        award_id = _award(scene)
        _close(scene, award_id)
        before = _snapshot(scene)
        _refused(api.delete(f"{_t(scene)}/awards/{award_id}"), 409, "award_not_active", NOT_ACTIVE_TEXT)
        assert _snapshot(scene) == before


# ---------------------------------------------------------------------------
#  POST /awards/{aid}/not-concluded
# ---------------------------------------------------------------------------

class TestNotConcluded:
    def test_closes_the_award_and_keeps_it_in_the_history(self, api, scene):
        award_id = _award(scene)
        response = api.post(
            f"{_t(scene)}/awards/{award_id}/not-concluded",
            json={"not_concluded_on": "2026-03-05", "note": "отказался"},
        )
        assert response.status_code == 200, response.text
        card = response.json()
        assert card["award"] is None
        events = card["award_history"]
        assert [(e["kind"], e["is_active"]) for e in events] == [("awarded", False), ("not_concluded", False)]
        closing = events[1]
        assert (closing["not_concluded_on"], closing["note"], closing["by_email"]) == (
            "2026-03-05", "отказался", scene.admin.email,
        )
        assert set(closing) == HISTORY_KEYS

    def test_the_note_is_optional(self, api, scene):
        award_id = _award(scene)
        response = api.post(f"{_t(scene)}/awards/{award_id}/not-concluded", json={"not_concluded_on": "2026-03-05"})
        assert response.status_code == 200, response.text
        assert response.json()["award_history"][1]["note"] is None

    @pytest.mark.parametrize("body", [{}, {"note": "без даты"}, {"not_concluded_on": None}, {"not_concluded_on": "завтра"}])
    def test_a_body_without_a_valid_date_is_422(self, api, scene, body):
        award_id = _award(scene)
        before = _snapshot(scene)
        assert api.post(f"{_t(scene)}/awards/{award_id}/not-concluded", json=body).status_code == 422
        assert _snapshot(scene) == before

    def test_member_is_forbidden_and_the_award_stays_active(self, api, scene):
        award_id = _award(scene)
        before = _snapshot(scene)
        api.as_member()
        response = api.post(f"{_t(scene)}/awards/{award_id}/not-concluded", json={"not_concluded_on": "2026-03-05"})
        assert response.status_code == 403
        assert _snapshot(scene) == before

    def test_an_award_of_another_tender_in_the_path_is_not_found(self, api, scene):
        award_id = _award(scene)
        before = _snapshot(scene)
        response = api.post(
            f"{TENDERS}/{scene.foreign_id}/awards/{award_id}/not-concluded", json={"not_concluded_on": "2026-03-05"},
        )
        assert response.status_code == 404
        assert _snapshot(scene) == before

    def test_a_closed_award_is_refused_as_not_active(self, api, scene):
        award_id = _award(scene)
        _close(scene, award_id)
        before = _snapshot(scene)
        response = api.post(f"{_t(scene)}/awards/{award_id}/not-concluded", json={"not_concluded_on": "2026-03-05"})
        _refused(response, 409, "award_not_active", NOT_ACTIVE_TEXT)
        assert _snapshot(scene) == before

    def test_an_award_with_a_contract_is_refused(self, api, scene):
        award_id = _award(scene)
        _link(scene, _contract(scene, number="ГП-888"), award_id)
        before = _snapshot(scene)
        response = api.post(f"{_t(scene)}/awards/{award_id}/not-concluded", json={"not_concluded_on": "2026-03-05"})
        _refused(response, 409, "award_has_contract", HAS_CONTRACT_TEXT.format("ГП-888"))
        assert _snapshot(scene) == before

    def test_a_date_earlier_than_the_award_is_accepted_and_the_history_stays_in_order(self, api, scene):
        award_id = _award(scene)
        response = api.post(f"{_t(scene)}/awards/{award_id}/not-concluded", json={"not_concluded_on": "2000-01-01"})
        assert response.status_code == 200
        assert [e["kind"] for e in response.json()["award_history"]] == ["awarded", "not_concluded"]


# ---------------------------------------------------------------------------
#  GET /awards/{aid}/contract-candidates
# ---------------------------------------------------------------------------

class TestCandidates:
    def test_lists_unlinked_contracts_of_the_same_object_and_contractor(self, api, scene):
        award_id = _award(scene)
        fits = _contract(scene, number="ГП-1")
        _contract(scene, number="ГП-ДРУГОЙ-ОБЪЕКТ", object_id=scene.other_object_id)
        _contract(scene, number="ГП-ДРУГОЙ-ПОДРЯДЧИК", contractor_id=scene.contractor_b)
        linked = _contract(scene, number="ГП-СВЯЗАН")
        _link(scene, linked, award_id)
        response = api.get(f"{_t(scene)}/awards/{award_id}/contract-candidates")
        assert response.status_code == 200, response.text
        rows = response.json()
        assert [r["id"] for r in rows] == [fits]
        assert set(rows[0]) == CANDIDATE_KEYS
        assert rows[0]["base_total_including_vat"] is None

    def test_member_may_read(self, api, scene):
        award_id = _award(scene)
        _contract(scene)
        api.as_member()
        response = api.get(f"{_t(scene)}/awards/{award_id}/contract-candidates")
        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_an_award_of_another_tender_in_the_path_is_not_found(self, api, scene):
        award_id = _award(scene)
        assert api.get(f"{TENDERS}/{scene.foreign_id}/awards/{award_id}/contract-candidates").status_code == 404

    def test_a_gone_award_is_not_found(self, api, scene):
        assert api.get(f"{_t(scene)}/awards/987654/contract-candidates").status_code == 404


# ---------------------------------------------------------------------------
#  POST /awards/{aid}/link
# ---------------------------------------------------------------------------

class TestLink:
    def test_links_the_contract_and_answers_the_tender_card(self, api, scene):
        award_id = _award(scene)
        contract_id = _contract(scene, number="ГП-1")
        response = api.post(f"{_t(scene)}/awards/{award_id}/link", json={"contract_id": contract_id})
        assert response.status_code == 200, response.text
        contract = response.json()["award"]["contract"]
        assert contract["id"] == contract_id and contract["contract_number"] == "ГП-1"
        with scene.sf() as db:
            assert db.get(Contract, contract_id).tender_award_id == award_id

    def test_member_is_forbidden_and_nothing_is_linked(self, api, scene):
        award_id = _award(scene)
        contract_id = _contract(scene)
        before = _snapshot(scene)
        api.as_member()
        assert api.post(f"{_t(scene)}/awards/{award_id}/link", json={"contract_id": contract_id}).status_code == 403
        assert _snapshot(scene) == before

    @pytest.mark.parametrize("body", [{}, {"contract_id": "x"}, {"contract_id": None}])
    def test_a_body_without_a_valid_contract_is_422(self, api, scene, body):
        award_id = _award(scene)
        assert api.post(f"{_t(scene)}/awards/{award_id}/link", json=body).status_code == 422

    def test_an_unknown_contract_is_not_found(self, api, scene):
        award_id = _award(scene)
        before = _snapshot(scene)
        assert api.post(f"{_t(scene)}/awards/{award_id}/link", json={"contract_id": 987654}).status_code == 404
        assert _snapshot(scene) == before

    def test_an_award_of_another_tender_in_the_path_is_not_found(self, api, scene):
        award_id = _award(scene)
        contract_id = _contract(scene)
        response = api.post(f"{TENDERS}/{scene.foreign_id}/awards/{award_id}/link", json={"contract_id": contract_id})
        assert response.status_code == 404

    def test_a_contract_of_another_object_is_refused(self, api, scene):
        award_id = _award(scene)
        contract_id = _contract(scene, number="ГП-О", object_id=scene.other_object_id)
        before = _snapshot(scene)
        response = api.post(f"{_t(scene)}/awards/{award_id}/link", json={"contract_id": contract_id})
        _refused(
            response, 409, "contract_object_mismatch",
            "Договор № ГП-О заключён на другом объекте — привязать его к этому тендеру нельзя.",
        )
        assert _snapshot(scene) == before

    def test_a_contract_of_another_contractor_is_refused(self, api, scene):
        award_id = _award(scene)
        contract_id = _contract(scene, number="ГП-П", contractor_id=scene.contractor_b)
        before = _snapshot(scene)
        response = api.post(f"{_t(scene)}/awards/{award_id}/link", json={"contract_id": contract_id})
        _refused(
            response, 409, "contract_contractor_mismatch",
            "Договор № ГП-П заключён с другим подрядчиком — привязать его к этому тендеру нельзя.",
        )
        assert _snapshot(scene) == before

    def test_an_award_that_already_has_a_contract_is_refused(self, api, scene):
        award_id = _award(scene)
        _link(scene, _contract(scene, number="ГП-1"), award_id)
        second = _contract(scene, number="ГП-2")
        before = _snapshot(scene)
        response = api.post(f"{_t(scene)}/awards/{award_id}/link", json={"contract_id": second})
        _refused(response, 409, "award_has_contract", HAS_CONTRACT_TEXT.format("ГП-1"))
        assert _snapshot(scene) == before

    def test_a_contract_that_has_a_basis_is_refused_as_already_linked(self, api, scene):
        first_award = _award(scene)
        contract_id = _contract(scene, number="ГП-1")
        _link(scene, contract_id, first_award)
        other_award = _second_tender_award(scene)
        before = _snapshot(scene)
        response = api.post(
            f"{TENDERS}/{other_award['tender_id']}/awards/{other_award['award_id']}/link",
            json={"contract_id": contract_id},
        )
        _refused(
            response, 409, "contract_already_linked",
            f"Договор № ГП-1 уже привязан к тендеру № {scene.tender_number}.",
        )
        assert _snapshot(scene) == before

    def test_a_closed_award_is_refused_as_not_active(self, api, scene):
        award_id = _award(scene)
        _close(scene, award_id)
        contract_id = _contract(scene)
        before = _snapshot(scene)
        response = api.post(f"{_t(scene)}/awards/{award_id}/link", json={"contract_id": contract_id})
        _refused(response, 409, "award_not_active", NOT_ACTIVE_TEXT)
        assert _snapshot(scene) == before


def _second_tender_award(s) -> dict:
    """Второй тендер того же объекта с участником А и действующей отметкой на нём."""
    other = s.f.TenderFactory.create(object=s.db.get(ObjectModel, s.object_id))
    other_round = s.f.TenderRoundFactory.create(tender=other, stage_no=1)
    s.db.flush()
    import_round(
        s.db, tender_round=other_round, data=round_payload([_participant(INN_A, "ООО А")]),
        parser_version="4.0.0", import_job_id=None, replace=False, unit_resolver=UnitResolver(s.db),
        category_resolver=CategoryResolver.from_db(s.db),
    )
    s.db.commit()
    offer_id = s.db.execute(sa.select(Offer.id).where(Offer.round_id == other_round.id)).scalar_one()
    with s.sf() as db:
        crud_awards.award_winner(db, other.id, offer_id=offer_id, user_id=s.admin.id)
    with s.sf() as db:
        award_id = db.execute(sa.select(TenderAward.id).where(TenderAward.tender_id == other.id)).scalar_one()
    return {"tender_id": other.id, "award_id": award_id}


# ---------------------------------------------------------------------------
#  POST /awards/{aid}/contract
# ---------------------------------------------------------------------------

class TestContractFromAward:
    def test_creates_the_contract_and_the_import_reaches_done(self, api, scene):
        award_id = _award(scene)
        response = _create_from_kp(api, scene, award_id)

        assert response.status_code == 202, response.text
        body = response.json()
        assert set(body) == {"contract", "job"}
        contract, job = body["contract"], body["job"]
        assert contract["contract_number"] == "ГП-КП-1"
        assert contract["object_id"] == scene.object_id and contract["contractor_id"] == scene.contractor_a
        assert contract["total_amount"] == "1234.50"
        assert contract["advance_pct"] == "30"
        assert set(contract["tender_basis"]) == TENDER_BASIS_KEYS
        assert contract["tender_basis"]["award_id"] == award_id
        assert contract["estimate_origin"] == "no_estimate"
        assert job["owner_type"] == "contract" and job["contract_id"] == contract["id"]
        assert job["filename"] == STAGE_FILENAME
        # Фоновая задача поставлена и в тестовом прогоне доходит до `done`.
        assert _job_status(scene, job["id"]) == ImportJobStatus.done.value
        card = api.get(f"{CONTRACTS}/{contract['id']}").json()
        assert card["estimate_origin"] == "from_offer"
        with scene.sf() as db:
            row = db.get(ImportJob, job["id"])
            assert row.source_award_id == award_id and row.estimates_created == 1
        tender = api.get(_t(scene)).json()
        assert tender["award"]["contract"]["id"] == contract["id"]

    def test_member_is_forbidden_and_nothing_is_created(self, api, scene):
        award_id = _award(scene)
        before = _snapshot(scene)
        api.as_member()
        assert _create_from_kp(api, scene, award_id).status_code == 403
        assert _snapshot(scene) == before

    @pytest.mark.parametrize("field", ["total_amount", "advance_pct", "bank_guarantee_pct", "retention_pct"])
    def test_a_float_in_money_or_percent_is_422(self, api, scene, field):
        award_id = _award(scene)
        before = _snapshot(scene)
        response = _create_from_kp(api, scene, award_id, **{field: 12.5})
        assert response.status_code == 422
        assert _snapshot(scene) == before

    @pytest.mark.parametrize("number", ["", "   "])
    def test_an_empty_number_is_422(self, api, scene, number):
        award_id = _award(scene)
        before = _snapshot(scene)
        response = _create_from_kp(api, scene, award_id, number=number)
        assert response.status_code == 422
        assert response.json()["detail"] == "Поле «Номер договора» не может быть пустым."
        assert _snapshot(scene) == before

    @pytest.mark.parametrize("body,field", [
        ({"signed_date": "2026-10-01"}, "contract_number"), ({"contract_number": "ГП-КП-1"}, "signed_date"),
        (_from_kp_body(signed_date="не дата"), "signed_date"),
    ])
    def test_required_fields_are_enforced(self, api, scene, body, field):
        award_id = _award(scene)
        before = _snapshot(scene)
        response = api.post(f"{_t(scene)}/awards/{award_id}/contract", json=body)
        assert response.status_code == 422
        # Отказ схемы тела именно по этому полю: 422 команды (`require_text` на
        # отсутствующем номере) дал бы тот же статус строкой и спрятал бы
        # необязательное поле модели.
        assert [err["loc"] for err in response.json()["detail"]] == [["body", field]]
        assert _snapshot(scene) == before

    def test_an_award_of_another_tender_in_the_path_is_not_found(self, api, scene):
        award_id = _award(scene)
        before = _snapshot(scene)
        response = api.post(f"{TENDERS}/{scene.foreign_id}/awards/{award_id}/contract", json=_from_kp_body())
        assert response.status_code == 404
        assert _snapshot(scene) == before

    def test_a_taken_number_is_refused_with_the_existing_text(self, api, scene):
        award_id = _award(scene)
        _contract(scene, number="ГП-ЗАНЯТ", object_id=scene.other_object_id)
        before = _snapshot(scene)
        response = _create_from_kp(api, scene, award_id, number="ГП-ЗАНЯТ")
        assert response.status_code == 409
        assert response.json()["detail"] == NUMBER_TAKEN_TEXT
        assert _snapshot(scene) == before

    def test_a_missing_stage_file_is_refused_and_nothing_is_created(self, api, scene):
        award_id = _award(scene)
        scene.storage.delete(scene.stage_key)
        before = _snapshot(scene)
        response = _create_from_kp(api, scene, award_id)
        _refused(
            response, 409, "stage_file_missing",
            "Файл этапа 2 недоступен в хранилище — договор из КП создать нельзя. Заведите договор "
            "обычной формой и загрузите смету.",
        )
        assert _snapshot(scene) == before

    def test_a_closed_award_is_refused_as_not_active(self, api, scene):
        award_id = _award(scene)
        _close(scene, award_id)
        before = _snapshot(scene)
        _refused(_create_from_kp(api, scene, award_id), 409, "award_not_active", NOT_ACTIVE_TEXT)
        assert _snapshot(scene) == before

    def test_a_second_contract_on_the_award_is_refused(self, api, scene):
        award_id = _award(scene)
        assert _create_from_kp(api, scene, award_id).status_code == 202
        before = _snapshot(scene)
        response = _create_from_kp(api, scene, award_id, number="ГП-КП-2")
        _refused(response, 409, "award_has_contract", HAS_CONTRACT_TEXT.format("ГП-КП-1"))
        assert _snapshot(scene) == before


# ---------------------------------------------------------------------------
#  DELETE /contracts/{cid}/tender-award
# ---------------------------------------------------------------------------

class TestUnlink:
    def test_unlinks_and_answers_the_contract_card(self, api, scene):
        award_id = _award(scene)
        contract_id = _contract(scene)
        _link(scene, contract_id, award_id)
        response = api.delete(f"{CONTRACTS}/{contract_id}/tender-award")
        assert response.status_code == 200, response.text
        card = response.json()
        assert card["id"] == contract_id
        assert card["tender_basis"] is None and card["estimate_origin"] is None
        with scene.sf() as db:
            assert db.get(Contract, contract_id).tender_award_id is None

    def test_member_is_forbidden_and_the_link_stays(self, api, scene):
        award_id = _award(scene)
        contract_id = _contract(scene)
        _link(scene, contract_id, award_id)
        before = _snapshot(scene)
        api.as_member()
        assert api.delete(f"{CONTRACTS}/{contract_id}/tender-award").status_code == 403
        assert _snapshot(scene) == before

    def test_an_unknown_contract_is_not_found(self, api, scene):
        assert api.delete(f"{CONTRACTS}/987654/tender-award").status_code == 404

    def test_an_unlinked_contract_is_refused(self, api, scene):
        contract_id = _contract(scene)
        before = _snapshot(scene)
        _refused(api.delete(f"{CONTRACTS}/{contract_id}/tender-award"), 409, "contract_not_linked", NOT_LINKED_TEXT)
        assert _snapshot(scene) == before

    def test_a_contract_with_the_kp_copy_is_refused(self, api, scene):
        award_id = _award(scene)
        contract_id = _create_from_kp(api, scene, award_id).json()["contract"]["id"]
        before = _snapshot(scene)
        response = api.delete(f"{CONTRACTS}/{contract_id}/tender-award")
        _refused(response, 409, "contract_estimate_is_copy", ESTIMATE_IS_COPY_TEXT)
        assert _snapshot(scene) == before

    def test_an_active_import_is_refused_with_its_number(self, api, scene):
        award_id = _award(scene)
        contract_id = _contract(scene)
        _link(scene, contract_id, award_id)
        job = scene.f.ImportJobFactory.create(
            contract=scene.db.get(Contract, contract_id), amendment_no=None,
            status=ImportJobStatus.pending.value,
        )
        scene.db.commit()
        before = _snapshot(scene)
        response = api.delete(f"{CONTRACTS}/{contract_id}/tender-award")
        _refused(
            response, 409, "contract_import_active",
            f"Импорт сметы этого договора выполняется (задание {job.id}). Дождитесь завершения и повторите.",
        )
        assert _snapshot(scene) == before


# ---------------------------------------------------------------------------
#  Коды договорного роутера и роутера этапов, поднятые через настоящие маршруты
# ---------------------------------------------------------------------------

class TestCodesOfTheNeighbouringRoutes:
    def test_patch_of_a_linked_contract_with_another_object_carries_its_code(self, api, scene):
        award_id = _award(scene)
        contract_id = _contract(scene)
        _link(scene, contract_id, award_id)
        before = _snapshot(scene)
        response = api.patch(f"{CONTRACTS}/{contract_id}", json={"object_id": scene.other_object_id})
        _refused(
            response, 409, "contract_parties_locked",
            f"Нельзя: договор заключён по тендеру № {scene.tender_number}, объект и подрядчик берутся из "
            "тендера. Чтобы их изменить, сначала отвяжите договор от тендера (договор, созданный из КП, не "
            "отвязывается — если победитель не тот, его удаляют).",
        )
        assert _snapshot(scene) == before

    def test_patch_of_a_linked_contract_with_unchanged_parties_passes(self, api, scene):
        award_id = _award(scene)
        contract_id = _contract(scene)
        _link(scene, contract_id, award_id)
        response = api.patch(
            f"{CONTRACTS}/{contract_id}",
            json={"object_id": scene.object_id, "contractor_id": scene.contractor_a, "title": "Новое имя"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["title"] == "Новое имя"

    def test_existing_refusals_of_the_contract_router_keep_a_plain_detail(self, api, scene):
        first = _contract(scene, number="ГП-1")
        second = _contract(scene, number="ГП-2", object_id=scene.other_object_id)
        taken = api.patch(f"{CONTRACTS}/{second}", json={"contract_number": "ГП-1"})
        assert taken.status_code == 409
        assert taken.json()["detail"] == NUMBER_TAKEN_TEXT
        missing = api.patch(f"{CONTRACTS}/987654", json={"title": "x"})
        assert missing.status_code == 404
        assert missing.json()["detail"] == "Договор 987654 не найден."
        assert first != second

    def test_a_new_stage_over_an_active_award_is_refused(self, api, scene):
        _award(scene)
        before = _snapshot(scene)
        response = api.post(f"{_t(scene)}/rounds", json={"stage_no": 3})
        _refused(response, 409, "round_blocked_by_award", ROUND_BLOCKED_TEXT)
        assert _snapshot(scene) == before

    def test_deleting_the_stage_of_the_award_is_refused(self, api, scene):
        _award(scene)
        before = _snapshot(scene)
        response = api.delete(f"{_t(scene)}/rounds/{scene.r2_id}")
        _refused(
            response, 409, "stage_has_award",
            "Нельзя удалить этап 2: на его КП есть отметка победителя — действующая или в истории "
            "тендера. Действующую можно снять; этап с историей победы удаляется только вместе с тендером.",
        )
        assert _snapshot(scene) == before

    def test_replacing_the_stage_file_of_the_award_is_refused(self, api, scene):
        _award(scene)
        before = _snapshot(scene)
        response = api.post(
            f"{_t(scene)}/rounds/{scene.r2_id}/upload", data={"replace": "true"},
            files={"file": ("сводная.xlsx", xlsx_bytes("другой файл"), "application/octet-stream")},
        )
        _refused(
            response, 409, "stage_has_award",
            "Нельзя заменить файл этапа 2: по его КП в тендере записан победитель, решение принималось "
            "по этому файлу. Для переторжки добавьте новый этап.",
        )
        assert _snapshot(scene) == before

    def test_deleting_the_participant_of_the_award_is_refused(self, api, scene):
        _award(scene)
        before = _snapshot(scene)
        response = api.delete(f"{_t(scene)}/participants/{scene.package_a}")
        _refused(
            response, 409, "participant_has_award",
            "Нельзя удалить участника «ООО А»: на его КП есть отметка победителя — действующая или в "
            "истории тендера. Действующую можно снять; участник с историей победы удаляется только вместе "
            "с тендером.",
        )
        assert _snapshot(scene) == before

    def test_deleting_the_tender_with_a_contract_is_refused(self, api, scene):
        award_id = _award(scene)
        _link(scene, _contract(scene, number="ГП-555"), award_id)
        before = _snapshot(scene)
        response = api.delete(_t(scene))
        _refused(
            response, 409, "tender_has_contract",
            "Нельзя удалить тендер: по отметке победителя заключён договор № ГП-555. Сначала удалите "
            "договор или отвяжите его от тендера.",
        )
        assert _snapshot(scene) == before


# ---------------------------------------------------------------------------
#  Аноним
# ---------------------------------------------------------------------------

class TestAnonymous:
    """Аноним с действующей CSRF-парой получает именно 401, а не 403 CSRF-прослойки:
    сторож `test_auth_coverage.py` принимает оба статуса и потому 401 на изменяющих
    маршрутах не доказывает."""

    @pytest.mark.parametrize("method,path", [
        ("POST", "/api/v1/tenders/1/awards"),
        ("DELETE", "/api/v1/tenders/1/awards/1"),
        ("POST", "/api/v1/tenders/1/awards/1/not-concluded"),
        ("GET", "/api/v1/tenders/1/awards/1/contract-candidates"),
        ("POST", "/api/v1/tenders/1/awards/1/link"),
        ("POST", "/api/v1/tenders/1/awards/1/contract"),
        ("DELETE", "/api/v1/contracts/1/tender-award"),
    ])
    def test_anonymous_is_401(self, method, path):
        from fastapi.testclient import TestClient

        from main import app

        assert not app.dependency_overrides
        token = "test-csrf-token"
        with TestClient(app, headers={"X-CSRF-Token": token}) as client:
            client.cookies.set("csrf_token", token)
            response = client.request(method, path, json={})
        assert response.status_code == 401, response.text


# ---------------------------------------------------------------------------
#  Ключи ответов
# ---------------------------------------------------------------------------

class TestResponseKeys:
    def test_tender_list_keys_are_unchanged(self, api, scene):
        _award(scene)
        items = api.get(TENDERS).json()["items"]
        assert items and all(set(item) == TENDER_LIST_KEYS for item in items)

    def test_tender_card_got_exactly_two_keys(self, api, scene):
        card = api.get(_t(scene)).json()
        assert set(card) == TENDER_CARD_KEYS_BEFORE | {"award", "award_history"}
        assert card["award"] is None and card["award_history"] == []

    def test_contract_card_got_exactly_two_keys(self, api, scene):
        contract_id = _contract(scene)
        card = api.get(f"{CONTRACTS}/{contract_id}").json()
        assert set(card) == CONTRACT_CARD_KEYS_BEFORE | {"tender_basis", "estimate_origin"}
        assert card["tender_basis"] is None and card["estimate_origin"] is None

    def test_contract_list_keys_are_unchanged(self, api, scene):
        _contract(scene)
        items = api.get(CONTRACTS).json()["items"]
        assert items and all(set(item) == CONTRACT_LIST_KEYS for item in items)

    def test_member_reads_the_tender_card_with_the_award(self, api, scene):
        _award(scene)
        api.as_member()
        response = api.get(_t(scene))
        assert response.status_code == 200
        assert response.json()["award"]["offer_id"] == scene.offer_a
