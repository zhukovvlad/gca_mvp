"""Удаления и замена этапа под отметкой победителя (спека Б2 §2.4, §2.7):
`delete_round`, `delete_participant` и его предпросмотр, `delete_tender`,
загрузка этапа с заменой (роутер) и `import_round` в сессии B.

Тесты идут на сессиях с настоящими commit-ами: отказ через ключ откатывает
сессию, и корпус на общей транзакции пропал бы вместе с ней. Состояние «после
отказа» читается свежей сессией. Отказ проверяется кодом, статусом и неизменённой
базой, а не подстрокой текста.
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from crud import tender_awards as crud_awards
from crud import tenders as crud_tenders
from crud.common import DomainError
from models import (
    Contract,
    Contractor,
    Estimate,
    ImportJob,
    ImportJobStatus,
    ObjectModel,
    Offer,
    OfferPackage,
    Tender,
    TenderAward,
    TenderRound,
    UserRole,
)
from services import import_pipeline
from services.category_resolution import CategoryResolver
from services.estimate_import import EstimateImportError
from services.round_import import import_round
from services.unit_resolution import UnitResolver
from tests.integration.test_estimates_api import xlsx_bytes
from tests.integration.test_import_pipeline import fake_parse
from tests.payloads import position, proposal, round_payload

pytestmark = pytest.mark.integration

TENDERS = "/api/v1/tenders"

# Независимые литералы текстов §2.7 (с подстановками команды).
STAGE_REPLACE_TEXT_2 = (
    "Нельзя заменить файл этапа 2: по его КП в тендере записан победитель, решение принималось по "
    "этому файлу. Для переторжки добавьте новый этап."
)
STAGE_DELETE_TEXT_2 = (
    "Нельзя удалить этап 2: на его КП есть отметка победителя — действующая или в истории тендера. "
    "Действующую можно снять; этап с историей победы удаляется только вместе с тендером."
)
PARTICIPANT_TEXT_A = (
    "Нельзя удалить участника «ООО А»: на его КП есть отметка победителя — действующая или в истории "
    "тендера. Действующую можно снять; участник с историей победы удаляется только вместе с тендером."
)
STAGE_DELETE_KEY_TEXT = (
    "Нельзя удалить этап: на его КП есть отметка победителя — действующая или в истории тендера. "
    "Действующую можно снять; этап с историей победы удаляется только вместе с тендером."
)
PARTICIPANT_KEY_TEXT = (
    "Нельзя удалить участника: на его КП есть отметка победителя — действующая или в истории "
    "тендера. Действующую можно снять; участник с историей победы удаляется только вместе с тендером."
)
TENDER_TEXT_777 = (
    "Нельзя удалить тендер: по отметке победителя заключён договор № ГП-777. Сначала удалите договор "
    "или отвяжите его от тендера."
)
TENDER_TEXT_888 = (
    "Нельзя удалить тендер: по отметке победителя заключён договор № ГП-888. Сначала удалите договор "
    "или отвяжите его от тендера."
)
TENDER_KEY_TEXT = (
    "Нельзя удалить тендер: по отметке победителя заключён договор. Сначала удалите договор или "
    "отвяжите его от тендера."
)


def _participant(inn: str, title: str, cost: str = "10"):
    return proposal(
        [position(job_title="Работа", unit="м2", unit_cost_total=cost, total_cost_total=cost)],
        title=title, inn=inn,
    )


@pytest.fixture
def scene(committing_db, committing_factories, committing_session_factory, tmp_storage):
    """Тендер: этап 1 (участники А, Б, В) и этап 2 (А, Б) — настоящим `import_round`."""
    f = committing_factories
    db = committing_db
    tender = f.TenderFactory.create()
    r1 = f.TenderRoundFactory.create(tender=tender, stage_no=1)
    r2 = f.TenderRoundFactory.create(tender=tender, stage_no=2)
    db.flush()

    def load(rnd, participants):
        import_round(
            db, tender_round=rnd, data=round_payload(participants), parser_version="4.0.0",
            import_job_id=None, replace=False, unit_resolver=UnitResolver(db),
            category_resolver=CategoryResolver.from_db(db),
        )

    load(r1, [_participant("7700000001", "ООО А"), _participant("7700000002", "ООО Б"),
              _participant("7700000003", "ООО В")])
    load(r2, [_participant("7700000001", "ООО А"), _participant("7700000002", "ООО Б")])
    user = f.UserFactory.create(role=UserRole.admin)
    db.commit()

    def offer_id(round_id, inn):
        return db.execute(
            sa.select(Offer.id).join(OfferPackage, OfferPackage.id == Offer.package_id)
            .join(Contractor, Contractor.id == OfferPackage.contractor_id)
            .where(Offer.round_id == round_id, Contractor.inn == inn)
        ).scalar_one()

    def package_id(inn):
        return db.execute(
            sa.select(OfferPackage.id).join(Contractor, Contractor.id == OfferPackage.contractor_id)
            .where(OfferPackage.tender_id == tender.id, Contractor.inn == inn)
        ).scalar_one()

    return SimpleNamespace(
        sf=committing_session_factory, db=db, f=f, storage=tmp_storage,
        tender_id=tender.id, object_id=tender.object_id, r1_id=r1.id, r2_id=r2.id, user_id=user.id,
        offer_a=offer_id(r2.id, "7700000001"), offer_b=offer_id(r2.id, "7700000002"),
        package_a=package_id("7700000001"), package_b=package_id("7700000002"),
        package_v=package_id("7700000003"),
        contractor_a=db.execute(sa.select(Contractor.id).where(Contractor.inn == "7700000001")).scalar_one(),
    )


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _award(s, offer_id):
    with s.sf() as db:
        return crud_awards.award_winner(db, s.tender_id, offer_id=offer_id, user_id=s.user_id)


def _close(s, award_id):
    with s.sf() as db:
        return crud_awards.mark_not_concluded(
            db, s.tender_id, award_id, not_concluded_on=dt.date(2026, 2, 1), note=None, user_id=s.user_id,
        )


def _award_ids(s):
    with s.sf() as db:
        return list(db.execute(sa.select(TenderAward.id).order_by(TenderAward.id)).scalars())


def _scalar(s, stmt):
    with s.sf() as db:
        return db.execute(stmt).scalar_one()


def _estimate_ids(s, round_id):
    with s.sf() as db:
        offers = sa.select(Offer.id).where(Offer.round_id == round_id)
        return sorted(db.execute(
            sa.select(Estimate.id).where(sa.or_(Estimate.round_id == round_id, Estimate.offer_id.in_(offers)))
        ).scalars())


def _refused(fn) -> DomainError:
    with pytest.raises(DomainError) as info:
        fn()
    return info.value


def _tender_state(s):
    """Снимок того, что удаление не должно трогать: этапы, оферты, сметы, отметки."""
    with s.sf() as db:
        return (
            sorted(db.execute(sa.select(TenderRound.id).where(TenderRound.tender_id == s.tender_id)).scalars()),
            sorted(db.execute(sa.select(Offer.id).where(Offer.tender_id == s.tender_id)).scalars()),
            sorted(db.execute(sa.select(Estimate.id)).scalars()),
            sorted(db.execute(sa.select(TenderAward.id)).scalars()),
        )


def _delete_round(s, round_id):
    with s.sf() as db:
        return crud_tenders.delete_round(db, s.tender_id, round_id)


def _delete_participant(s, package_id, token=None):
    with s.sf() as db:
        return crud_tenders.delete_participant(db, s.tender_id, package_id, confirmation_token=token)


def _preview(s, package_id):
    with s.sf() as db:
        return crud_tenders.participant_deletion_preview(db, s.tender_id, package_id)


def _delete_tender(s):
    with s.sf() as db:
        return crud_tenders.delete_tender(db, s.tender_id)


def _contract_on(s, award_id, number="ГП-777"):
    """Договор по отметке — прямой записью: так он есть и у закрытой отметки."""
    contract = s.f.ContractFactory.create(
        object=s.db.get(ObjectModel, s.object_id),
        contractor=s.db.get(Contractor, s.contractor_a), contract_number=number,
    )
    s.db.commit()
    with s.sf() as db:
        db.execute(sa.update(Contract).where(Contract.id == contract.id).values(tender_award_id=award_id))
        db.commit()
    return contract.id


# ---------------------------------------------------------------------------
#  delete_round
# ---------------------------------------------------------------------------

class TestDeleteRound:
    def test_a_round_with_an_active_award_is_refused(self, scene):
        _award(scene, scene.offer_a)
        before = _tender_state(scene)

        err = _refused(lambda: _delete_round(scene, scene.r2_id))

        assert (err.status_code, err.code) == (409, "stage_has_award")
        assert err.detail == STAGE_DELETE_TEXT_2
        assert _tender_state(scene) == before

    def test_a_round_with_only_a_not_concluded_award_is_refused(self, scene):
        _award(scene, scene.offer_a)
        _close(scene, _award_ids(scene)[0])
        before = _tender_state(scene)

        err = _refused(lambda: _delete_round(scene, scene.r2_id))

        assert (err.status_code, err.code) == (409, "stage_has_award")
        assert err.detail == STAGE_DELETE_TEXT_2
        assert _tender_state(scene) == before

    def test_a_round_without_awards_is_deleted_while_another_round_holds_the_award(self, scene):
        _award(scene, scene.offer_a)
        r1_estimates = _estimate_ids(scene, scene.r1_id)
        assert r1_estimates

        _delete_round(scene, scene.r1_id)

        assert _scalar(scene, sa.select(sa.func.count()).select_from(TenderRound).where(
            TenderRound.id == scene.r1_id)) == 0
        assert _estimate_ids(scene, scene.r1_id) == []
        assert len(_award_ids(scene)) == 1

    def test_the_key_translates_a_violation_past_the_check_with_the_same_code(self, scene, monkeypatch):
        _award(scene, scene.offer_a)
        before = _tender_state(scene)
        monkeypatch.setattr(crud_awards, "refuse_if_round_has_award", lambda *a, **k: None)

        err = _refused(lambda: _delete_round(scene, scene.r2_id))

        assert (err.status_code, err.code, err.detail) == (409, "stage_has_award", STAGE_DELETE_KEY_TEXT)
        assert _tender_state(scene) == before


# ---------------------------------------------------------------------------
#  delete_participant и предпросмотр
# ---------------------------------------------------------------------------

class TestDeleteParticipant:
    def test_a_participant_with_an_active_award_is_refused_by_the_command_and_the_preview(self, scene):
        _award(scene, scene.offer_a)
        before = _tender_state(scene)

        deleting = _refused(lambda: _delete_participant(scene, scene.package_a))
        previewing = _refused(lambda: _preview(scene, scene.package_a))

        for err in (deleting, previewing):
            assert (err.status_code, err.code) == (409, "participant_has_award")
            assert err.detail == PARTICIPANT_TEXT_A
        assert _tender_state(scene) == before

    def test_a_participant_with_only_a_not_concluded_award_is_refused(self, scene):
        _award(scene, scene.offer_a)
        _close(scene, _award_ids(scene)[0])
        before = _tender_state(scene)

        deleting = _refused(lambda: _delete_participant(scene, scene.package_a))
        previewing = _refused(lambda: _preview(scene, scene.package_a))

        assert deleting.code == previewing.code == "participant_has_award"
        assert deleting.detail == previewing.detail == PARTICIPANT_TEXT_A
        assert _tender_state(scene) == before

    def test_the_refusal_comes_before_the_confirmation_demand(self, scene):
        _award(scene, scene.offer_a)

        err = _refused(lambda: _delete_participant(scene, scene.package_a, token="wrong"))

        assert err.code == "participant_has_award"

    def test_a_participant_without_awards_keeps_the_old_flow(self, scene):
        _award(scene, scene.offer_a)
        preview = _preview(scene, scene.package_b)
        assert preview["rounds_count"] == 2 and preview["estimates_count"] == 2
        demand = _refused(lambda: _delete_participant(scene, scene.package_b))
        assert demand.code == "confirmation_required"

        _delete_participant(scene, scene.package_b, token=preview["confirmation_token"])

        assert _scalar(scene, sa.select(sa.func.count()).select_from(OfferPackage).where(
            OfferPackage.id == scene.package_b)) == 0
        assert len(_award_ids(scene)) == 1

    def test_the_key_translates_a_violation_past_the_check_with_the_same_code(self, scene, monkeypatch):
        _award(scene, scene.offer_a)
        token = _preview_token_past_check(scene, monkeypatch)
        before = _tender_state(scene)

        err = _refused(lambda: _delete_participant(scene, scene.package_a, token=token))

        assert (err.status_code, err.code, err.detail) == (409, "participant_has_award", PARTICIPANT_KEY_TEXT)
        assert _tender_state(scene) == before


def _preview_token_past_check(s, monkeypatch):
    """Токен состава участника А при выключенной проверке; проверка остаётся выключенной."""
    monkeypatch.setattr(crud_awards, "refuse_if_package_has_award", lambda *a, **k: None)
    return _preview(s, s.package_a)["confirmation_token"]


# ---------------------------------------------------------------------------
#  delete_tender
# ---------------------------------------------------------------------------

class TestDeleteTender:
    def test_a_tender_with_award_history_and_no_contract_is_deleted_with_its_awards(self, scene):
        _award(scene, scene.offer_a)
        _close(scene, _award_ids(scene)[0])
        _award(scene, scene.offer_b)
        assert len(_award_ids(scene)) == 2

        keys = _delete_tender(scene)

        assert isinstance(keys, list)
        assert _scalar(scene, sa.select(sa.func.count()).select_from(Tender)) == 0
        assert _award_ids(scene) == []

    def test_a_tender_with_a_contract_on_the_active_award_is_refused_with_its_number(self, scene):
        _award(scene, scene.offer_a)
        _contract_on(scene, _award_ids(scene)[0], number="ГП-777")
        before = _tender_state(scene)

        err = _refused(lambda: _delete_tender(scene))

        assert (err.status_code, err.code) == (409, "tender_has_contract")
        assert err.detail == TENDER_TEXT_777
        assert _tender_state(scene) == before

    def test_a_contract_on_a_not_concluded_award_also_refuses(self, scene):
        _award(scene, scene.offer_a)
        closed = _award_ids(scene)[0]
        _close(scene, closed)
        _award(scene, scene.offer_b)
        _contract_on(scene, closed, number="ГП-888")
        before = _tender_state(scene)

        err = _refused(lambda: _delete_tender(scene))

        assert (err.status_code, err.code) == (409, "tender_has_contract")
        assert err.detail == TENDER_TEXT_888
        assert _tender_state(scene) == before

    def test_a_tender_without_awards_is_deleted_as_before(self, scene):
        _delete_tender(scene)

        assert _scalar(scene, sa.select(sa.func.count()).select_from(Tender)) == 0

    def test_the_key_translates_a_violation_past_the_check_with_the_same_code(self, scene, monkeypatch):
        _award(scene, scene.offer_a)
        _contract_on(scene, _award_ids(scene)[0])
        before = _tender_state(scene)
        monkeypatch.setattr(crud_awards, "awards_have_contract", lambda *a, **k: None)

        err = _refused(lambda: _delete_tender(scene))

        assert (err.status_code, err.code, err.detail) == (409, "tender_has_contract", TENDER_KEY_TEXT)
        assert _tender_state(scene) == before


# ---------------------------------------------------------------------------
#  Замена этапа: роутер
# ---------------------------------------------------------------------------

def _storage_files(s) -> int:
    return sum(1 for p in s.storage.root.rglob("*") if p.is_file()) if s.storage.root.exists() else 0


def _jobs_count(s) -> int:
    return _scalar(s, sa.select(sa.func.count()).select_from(ImportJob))


def _replace_upload(client, s, round_id, content):
    return client.post(
        f"{TENDERS}/{s.tender_id}/rounds/{round_id}/upload",
        files={"file": ("сводная.xlsx", content, "application/octet-stream")}, data={"replace": "true"},
    )


class TestReplaceThroughTheRouter:
    @pytest.mark.parametrize("close_first", [False, True], ids=["active", "not_concluded"])
    def test_a_replacement_over_an_award_is_refused_before_the_file_is_saved(
        self, committing_client, scene, close_first,
    ):
        _award(scene, scene.offer_a)
        if close_first:
            _close(scene, _award_ids(scene)[0])
        files, jobs, estimates = _storage_files(scene), _jobs_count(scene), _estimate_ids(scene, scene.r2_id)

        response = _replace_upload(committing_client, scene, scene.r2_id, xlsx_bytes("новый файл"))

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == "stage_has_award"
        assert detail["message"] == STAGE_REPLACE_TEXT_2
        assert (_storage_files(scene), _jobs_count(scene)) == (files, jobs)
        assert _estimate_ids(scene, scene.r2_id) == estimates

    def test_a_round_without_awards_is_replaced_as_before(self, committing_client, scene, monkeypatch):
        _award(scene, scene.offer_a)
        monkeypatch.setattr(
            import_pipeline, "parse_estimate",
            fake_parse(round_payload([_participant("7700000001", "ООО А", "11"),
                                      _participant("7700000002", "ООО Б", "12")])),
        )
        before = _estimate_ids(scene, scene.r1_id)

        response = _replace_upload(committing_client, scene, scene.r1_id, xlsx_bytes("новый файл 1"))

        assert response.status_code == 202
        job = _scalar(scene, sa.select(ImportJob).where(ImportJob.id == response.json()["id"]))
        assert job.status == ImportJobStatus.done.value
        assert set(_estimate_ids(scene, scene.r1_id)).isdisjoint(before)


# ---------------------------------------------------------------------------
#  Замена этапа: import_round в сессии B
# ---------------------------------------------------------------------------

def _round_job(s, round_id):
    key = s.storage.save(b"PK\x03\x04not-a-real-xlsx")
    job = s.f.ImportJobFactory.create(contract=None, round_id=round_id, file_key=key,
                                      status=ImportJobStatus.pending.value)
    s.db.commit()
    return job.id


def _run_replace(s, job_id, payload):
    import_pipeline.run_import_job(
        job_id, session_factory=s.sf, storage=s.storage, replace=True, parse=fake_parse(payload),
    )
    with s.sf() as db:
        return db.get(ImportJob, job_id)


class TestReplaceInsideTheImport:
    def _payload(self):
        return round_payload([_participant("7700000001", "ООО А", "99"), _participant("7700000002", "ООО Б", "98")])

    @pytest.mark.parametrize("close_first", [False, True], ids=["active", "not_concluded"])
    def test_a_replacement_job_started_after_the_award_fails_with_the_award_text(self, scene, close_first):
        # Окно между проверкой роутера и созданием задания: отметка уже закоммичена,
        # активного задания у этапа ещё нет, задание замены создаётся после.
        _award(scene, scene.offer_a)
        if close_first:
            _close(scene, _award_ids(scene)[0])
        before = _estimate_ids(scene, scene.r2_id)
        job_id = _round_job(scene, scene.r2_id)

        job = _run_replace(scene, job_id, self._payload())

        assert job.status == ImportJobStatus.error.value
        assert job.error_text == STAGE_REPLACE_TEXT_2
        assert _estimate_ids(scene, scene.r2_id) == before
        assert len(_award_ids(scene)) == 1

    def test_a_replacement_of_a_round_without_awards_still_succeeds(self, scene):
        _award(scene, scene.offer_a)
        before = _estimate_ids(scene, scene.r1_id)
        job_id = _round_job(scene, scene.r1_id)

        job = _run_replace(scene, job_id, self._payload())

        assert job.status == ImportJobStatus.done.value
        assert set(_estimate_ids(scene, scene.r1_id)).isdisjoint(before)

    def test_without_replace_the_award_check_does_not_fire(self, scene):
        """Без `replace` проверка отметок не зовётся: загрузка этапа С отметкой без
        замены получает прежний отказ «Раунд уже загружен», а не `stage_has_award`;
        первая загрузка нового этапа проходит при действующей отметке тендера.

        Первый вход — единственный, на котором выдала бы себя проверка, вынесенная
        из-под `if replace` (у нового этапа отметок нет, и проверка молчит)."""
        _award(scene, scene.offer_a)
        with scene.sf() as db:
            with pytest.raises(EstimateImportError) as info:
                import_round(
                    db, tender_round=db.get(TenderRound, scene.r2_id), data=self._payload(), parser_version="4.0.0",
                    import_job_id=None, replace=False, unit_resolver=UnitResolver(db),
                    category_resolver=CategoryResolver.from_db(db),
                )
            db.rollback()
        assert str(info.value) != STAGE_REPLACE_TEXT_2
        assert str(info.value).startswith("Раунд уже загружен (смет: ")

        r3 = scene.f.TenderRoundFactory.create(tender=scene.db.get(Tender, scene.tender_id), stage_no=3)
        scene.db.commit()
        with scene.sf() as db:
            import_round(
                db, tender_round=db.get(TenderRound, r3.id), data=self._payload(), parser_version="4.0.0",
                import_job_id=None, replace=False, unit_resolver=UnitResolver(db),
                category_resolver=CategoryResolver.from_db(db),
            )
            db.commit()
        assert _estimate_ids(scene, r3.id)
