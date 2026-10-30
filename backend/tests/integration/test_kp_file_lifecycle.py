"""Жизненный цикл решений по строкам КП и отправленных файлов: замена раунда и
удаления раунда, тендера и участника (спека 4б §2.10, §2.11, §2.13).

Решения по строкам КП живут на позициях сметы и уходят с ней; отправленный файл
принадлежит офферу и замену раунда переживает. Удаления раунда, тендера и участника
убирают файлы отправленных с носителя после коммита, а токен подтверждения удаления
участника меняется от числа решений и от числа отправленных файлов. Названия и числа
выдуманы; эталоны — числа и строки, посчитанные руками.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud import kp_decisions as kd
from crud import tenders as crud_tenders
from crud.common import DomainError
from models import (
    Contractor,
    Estimate,
    ImportJobStatus,
    KpLineAgreement,
    KpLineComment,
    KpLineRung,
    KpSentFile,
    Lot,
    Offer,
    OfferPackage,
    PositionItem,
    Proposal,
)
from tests.integration.test_estimates_api import finished_job, xlsx_bytes
from tests.integration.test_kp_file_scene import _agree, _comment, _family_rung, _line_rung, _user
from tests.integration.test_our_price_load import World
from tests.integration.test_our_price_round_api import Stage, count_statements
from tests.integration.test_tenders_api import P_A, P_B, TENDERS, upload_round
from tests.payloads import round_payload

pytestmark = pytest.mark.integration

D = Decimal

REPLACE_REQUIRED_TEXT = "Раунд уже загружен; для замены всех его смет повторите запрос с replace=true."
LOST_WARNING = (
    "Заменой раунда удалены решения по строкам КП: договорённостей — {a}, комментариев — {c}, ручных ступеней — {r}."
)


@pytest.fixture
def world(db_session, factories, admin_user):
    return World(db_session, factories, admin_user)


@pytest.fixture
def stage(world, db_session, factories):
    return Stage(world, db_session, factories)


@pytest.fixture
def tender(committing_client, committing_factories, committing_db):
    obj = committing_factories.ObjectFactory.create()
    rc = committing_factories.RateClassFactory.create()
    committing_db.commit()
    created = committing_client.post(
        TENDERS, json={"object_id": obj.id, "title": "Тендер Т", "tender_number": "Т-0001", "rate_class_id": rc.id}
    )
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


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------


def _sent(db, storage, offer_id, user, *, data=b"file"):
    """Отправленный файл оффера: байты на носителе и запись; возвращает ключ."""
    key = storage.save(data)
    db.add(
        KpSentFile(
            offer_id=offer_id,
            file_key=key,
            filename="КП.xlsx",
            file_sha256="0" * 64,
            size_bytes=len(data),
            vat_rate=D("20"),
            level_label="Уровень цен КП",
            created_by=user.id,
        )
    )
    db.flush()
    return key


def _stage_two_rounds(stage, db_session):
    """Участники «А» и «Б»; «А» участвует в двух этапах. Решения на каждой смете."""
    rnd1 = stage.round()
    rnd2 = stage.round()
    a1 = stage.participant(rnd1, "Подрядчик А")
    b1 = stage.participant(rnd1, "Подрядчик Б")
    a2 = stage.participant(rnd2, "Подрядчик А", package=a1.offer.package)
    items = {name: stage.kp(part, D("10")) for name, part in (("a1", a1), ("b1", b1), ("a2", a2))}
    db_session.flush()
    return rnd1, rnd2, a1, b1, a2, items


def _loaded(client, tender, round_stub, db, factories):
    """Раунд из двух участников через настоящий импорт: ИНН, позиции, офферы, пользователь."""
    round_stub(round_payload([P_A(), P_B()]))
    job = finished_job(client, upload_round(client, tender, content=xlsx_bytes("a")))
    assert job["status"] == ImportJobStatus.done.value
    assert job["estimates_created"] == 2
    user = factories.UserFactory.create()
    db.commit()
    return user


def _item(db, inn):
    return db.execute(
        sa.select(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .join(Estimate, Estimate.id == Lot.estimate_id)
        .join(Offer, Offer.id == Estimate.offer_id)
        .join(OfferPackage, OfferPackage.id == Offer.package_id)
        .join(Contractor, Contractor.id == OfferPackage.contractor_id)
        .where(Contractor.inn == inn)
    ).scalar_one()


def _offer(db, inn):
    return db.execute(
        sa.select(Offer)
        .join(OfferPackage, OfferPackage.id == Offer.package_id)
        .join(Contractor, Contractor.id == OfferPackage.contractor_id)
        .where(Contractor.inn == inn)
    ).scalar_one()


def _decide(db, user):
    """Два «Договорились», один комментарий и одна ручная ступень строки на двух сметах."""
    item_a, item_b = _item(db, "7700000001"), _item(db, "7700000002")
    _agree(db, item_a, D("9"), user)
    _agree(db, item_b, D("11"), user)
    _comment(db, item_a, "Свой текст", user)
    _line_rung(db, item_b, "v", "cls", {"level": "v", "variant_id": 1}, user)
    db.commit()


def _decision_rows(db):
    return [
        db.execute(sa.select(sa.func.count()).select_from(model)).scalar_one()
        for model in (KpLineAgreement, KpLineComment, KpLineRung)
    ]


# ---------------------------------------------------------------------------
#  Счёт решений и ключи отправленных
# ---------------------------------------------------------------------------


class TestCounts:
    def test_round_counts_four_kinds_over_all_its_estimates(self, stage, db_session):
        rnd1, _, a1, b1, _, items = _stage_two_rounds(stage, db_session)
        user = _user(stage.f)
        _agree(db_session, items["a1"], D("9"), user)
        _agree(db_session, items["b1"], D("8"), user)
        _comment(db_session, items["a1"], "Т", user)
        _line_rung(db_session, items["b1"], "v", "cls", {"level": "v", "variant_id": 1}, user)
        _family_rung(db_session, a1.estimate, stage.family, "f", "cls", user)
        _family_rung(db_session, b1.estimate, stage.family, "f", "cls", user)

        counts = kd.count_round_decisions(db_session, rnd1.id)

        # две ступени семьи из разных смет и одна ступень строки — три ступени
        assert counts == kd.DecisionCounts(agreements=2, comments=1, rungs=3)

    def test_round_counts_nothing_of_another_round(self, stage, db_session):
        rnd1, rnd2, _, _, _, items = _stage_two_rounds(stage, db_session)
        user = _user(stage.f)
        _agree(db_session, items["a2"], D("9"), user)
        _comment(db_session, items["a2"], "Т", user)

        assert kd.count_round_decisions(db_session, rnd2.id) == kd.DecisionCounts(1, 1, 0)
        # на первом этапе решений нет, второй им не мешает
        assert kd.count_round_decisions(db_session, rnd1.id) == kd.DecisionCounts(0, 0, 0)

    def test_empty_round_counts_zero(self, stage, db_session):
        rnd = stage.round()

        assert kd.count_round_decisions(db_session, rnd.id) == kd.DecisionCounts(0, 0, 0)

    def test_package_counts_every_round_of_the_participant_and_no_other_package(self, stage, db_session):
        _, _, a1, b1, _, items = _stage_two_rounds(stage, db_session)
        user = _user(stage.f)
        _agree(db_session, items["a1"], D("9"), user)
        _agree(db_session, items["a2"], D("9"), user)
        _comment(db_session, items["a2"], "Т", user)
        _line_rung(db_session, items["a1"], "v", "all", {"level": "v", "variant_id": 1}, user)
        _family_rung(db_session, a1.estimate, stage.family, "f", "cls", user)
        _agree(db_session, items["b1"], D("9"), user)
        _comment(db_session, items["b1"], "Т", user)

        assert kd.count_package_decisions(db_session, a1.offer.package_id) == kd.DecisionCounts(2, 1, 2)
        assert kd.count_package_decisions(db_session, b1.offer.package_id) == kd.DecisionCounts(1, 1, 0)

    def test_count_is_one_statement_however_many_estimates(self, stage, db_session):
        rnd1, _, a1, _, _, items = _stage_two_rounds(stage, db_session)
        user = _user(stage.f)
        _agree(db_session, items["a1"], D("9"), user)
        round_id, package_id = rnd1.id, a1.offer.package_id
        with count_statements(db_session) as few:
            kd.count_round_decisions(db_session, round_id)
            kd.count_package_decisions(db_session, package_id)
        for name in ("Подрядчик В", "Подрядчик Г", "Подрядчик Д"):
            _agree(db_session, stage.kp(stage.participant(rnd1, name), D("10")), D("9"), user)
        with count_statements(db_session) as many:
            kd.count_round_decisions(db_session, round_id)
            kd.count_package_decisions(db_session, package_id)

        assert len(few) == len(many) == 2


class TestSentFileKeys:
    def _scene(self, stage, db_session, tmp_storage):
        rnd1, rnd2, a1, b1, a2, _ = _stage_two_rounds(stage, db_session)
        user = _user(stage.f)
        keys = {
            "a1": _sent(db_session, tmp_storage, a1.offer.id, user),
            "b1": _sent(db_session, tmp_storage, b1.offer.id, user),
            "a2": _sent(db_session, tmp_storage, a2.offer.id, user),
        }
        return rnd1, a1, keys

    def test_by_round(self, stage, db_session, tmp_storage):
        rnd1, _, keys = self._scene(stage, db_session, tmp_storage)

        assert sorted(kd.sent_file_keys(db_session, round_id=rnd1.id)) == sorted([keys["a1"], keys["b1"]])

    def test_by_tender(self, stage, db_session, tmp_storage):
        _, _, keys = self._scene(stage, db_session, tmp_storage)

        assert sorted(kd.sent_file_keys(db_session, tender_id=stage.tender.id)) == sorted(keys.values())

    def test_by_package_covers_every_round_of_the_participant(self, stage, db_session, tmp_storage):
        _, a1, keys = self._scene(stage, db_session, tmp_storage)

        assert sorted(kd.sent_file_keys(db_session, package_id=a1.offer.package_id)) == sorted([keys["a1"], keys["a2"]])

    def test_nothing_sent_gives_empty_list(self, stage, db_session):
        rnd = stage.round()
        stage.participant(rnd, "Подрядчик А")

        assert kd.sent_file_keys(db_session, round_id=rnd.id) == []

    @pytest.mark.parametrize(
        "kwargs",
        [{}, {"round_id": 1, "tender_id": 1}, {"round_id": 1, "package_id": 1}, {"tender_id": 1, "package_id": 1}],
    )
    def test_exactly_one_argument(self, db_session, kwargs):
        with pytest.raises(ValueError):
            kd.sent_file_keys(db_session, **kwargs)


# ---------------------------------------------------------------------------
#  Замена раунда
# ---------------------------------------------------------------------------


class TestReplaceRequired:
    def test_with_decisions_the_refusal_carries_counts(
        self, committing_client, committing_db, committing_factories, tender, round_stub
    ):
        user = _loaded(committing_client, tender, round_stub, committing_db, committing_factories)
        _decide(committing_db, user)

        response = upload_round(committing_client, tender, content=xlsx_bytes("b"))

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == "replace_required"
        assert detail["agreements"] == 2
        assert detail["comments"] == 1
        assert detail["rungs"] == 1
        assert "договорённостей — 2" in detail["message"]
        assert "комментариев — 1" in detail["message"]
        assert "ручных ступеней — 1" in detail["message"]

    def test_without_decisions_the_text_is_the_old_one_and_no_context(
        self, committing_client, committing_db, committing_factories, tender, round_stub
    ):
        _loaded(committing_client, tender, round_stub, committing_db, committing_factories)

        response = upload_round(committing_client, tender, content=xlsx_bytes("b"))

        assert response.status_code == 409
        assert response.json()["detail"] == {"code": "replace_required", "message": REPLACE_REQUIRED_TEXT}

    def test_only_a_sent_file_does_not_count_as_a_decision(
        self, committing_client, committing_db, committing_factories, tmp_storage, tender, round_stub
    ):
        user = _loaded(committing_client, tender, round_stub, committing_db, committing_factories)
        _sent(committing_db, tmp_storage, _offer(committing_db, "7700000001").id, user)
        committing_db.commit()

        response = upload_round(committing_client, tender, content=xlsx_bytes("b"))

        assert response.json()["detail"] == {"code": "replace_required", "message": REPLACE_REQUIRED_TEXT}


class TestReplaceWarning:
    def test_replace_warns_about_lost_decisions_and_keeps_sent_files(
        self, committing_client, committing_db, committing_factories, tmp_storage, tender, round_stub
    ):
        user = _loaded(committing_client, tender, round_stub, committing_db, committing_factories)
        _decide(committing_db, user)
        key = _sent(committing_db, tmp_storage, _offer(committing_db, "7700000001").id, user)
        committing_db.commit()

        job = finished_job(
            committing_client, upload_round(committing_client, tender, content=xlsx_bytes("b"), replace=True)
        )

        assert job["status"] == ImportJobStatus.done.value
        assert LOST_WARNING.format(a=2, c=1, r=1) in job["warnings"]
        committing_db.expire_all()
        assert _decision_rows(committing_db) == [0, 0, 0]
        assert committing_db.execute(sa.select(sa.func.count()).select_from(KpSentFile)).scalar_one() == 1
        assert tmp_storage.exists(key)

    def test_replace_without_decisions_has_no_such_warning(
        self, committing_client, committing_db, committing_factories, tender, round_stub
    ):
        _loaded(committing_client, tender, round_stub, committing_db, committing_factories)

        job = finished_job(
            committing_client, upload_round(committing_client, tender, content=xlsx_bytes("b"), replace=True)
        )

        assert job["status"] == ImportJobStatus.done.value
        assert not any("решения по строкам КП" in w for w in job["warnings"])


# ---------------------------------------------------------------------------
#  Удаления
# ---------------------------------------------------------------------------


class TestDeletionsPurgeSentFiles:
    def _scene(self, client, db, factories, storage, tender, round_stub):
        user = _loaded(client, tender, round_stub, db, factories)
        keys = [_sent(db, storage, _offer(db, inn).id, user, data=inn.encode()) for inn in ("7700000001", "7700000002")]
        db.commit()
        assert all(storage.exists(k) for k in keys)
        return keys

    def test_delete_round_removes_sent_files(
        self, committing_client, committing_db, committing_factories, tmp_storage, tender, round_stub
    ):
        keys = self._scene(committing_client, committing_db, committing_factories, tmp_storage, tender, round_stub)

        response = committing_client.delete(f"{TENDERS}/{tender['id']}/rounds/{tender['round_id']}")

        assert response.status_code == 204
        assert not any(tmp_storage.exists(k) for k in keys)

    def test_delete_tender_removes_sent_files(
        self, committing_client, committing_db, committing_factories, tmp_storage, tender, round_stub
    ):
        keys = self._scene(committing_client, committing_db, committing_factories, tmp_storage, tender, round_stub)

        response = committing_client.delete(f"{TENDERS}/{tender['id']}")

        assert response.status_code == 204
        assert not any(tmp_storage.exists(k) for k in keys)

    def test_refused_round_deletion_keeps_sent_files(
        self, committing_client, committing_db, committing_factories, tmp_storage, tender, round_stub
    ):
        keys = self._scene(committing_client, committing_db, committing_factories, tmp_storage, tender, round_stub)
        committing_factories.ImportJobFactory.create(
            contract=None, round_id=tender["round_id"], status=ImportJobStatus.parsing.value, file_key="k-active"
        )
        committing_db.commit()

        response = committing_client.delete(f"{TENDERS}/{tender['id']}/rounds/{tender['round_id']}")

        assert response.status_code == 409
        assert all(tmp_storage.exists(k) for k in keys)

    def test_delete_participant_removes_only_its_files(
        self, committing_client, committing_db, committing_factories, tmp_storage, tender, round_stub
    ):
        key_a, key_b = self._scene(
            committing_client, committing_db, committing_factories, tmp_storage, tender, round_stub
        )
        package = _offer(committing_db, "7700000001").package_id
        url = f"{TENDERS}/{tender['id']}/participants/{package}"
        token = committing_client.delete(url).json()["detail"]["confirmation_token"]

        response = committing_client.delete(url, params={"confirmation_token": token})

        assert response.status_code == 204
        assert not tmp_storage.exists(key_a)
        assert tmp_storage.exists(key_b)

    def test_stale_token_keeps_participant_files(
        self, committing_client, committing_db, committing_factories, tmp_storage, tender, round_stub
    ):
        key_a, _ = self._scene(committing_client, committing_db, committing_factories, tmp_storage, tender, round_stub)
        package = _offer(committing_db, "7700000001").package_id

        response = committing_client.delete(
            f"{TENDERS}/{tender['id']}/participants/{package}", params={"confirmation_token": "stale"}
        )

        assert response.status_code == 409
        assert tmp_storage.exists(key_a)

    @pytest.mark.parametrize("target", ["round", "tender", "participant"])
    def test_a_rollback_after_the_keys_are_collected_keeps_files_and_records(
        self,
        committing_client,
        committing_db,
        committing_factories,
        tmp_storage,
        tender,
        round_stub,
        monkeypatch,
        target,
    ):
        """Сбой внутри доменной транзакции — уже после сбора ключей и удаления строк,
        до коммита: транзакция откатывается, и файлы с носителя трогать нельзя."""
        keys = self._scene(committing_client, committing_db, committing_factories, tmp_storage, tender, round_stub)
        params = {}
        if target == "round":
            url = f"{TENDERS}/{tender['id']}/rounds/{tender['round_id']}"
        elif target == "tender":
            url = f"{TENDERS}/{tender['id']}"
        else:
            url = f"{TENDERS}/{tender['id']}/participants/{_offer(committing_db, '7700000001').package_id}"
            params = {"confirmation_token": committing_client.delete(url).json()["detail"]["confirmation_token"]}

        def failing(*_args, **_kwargs):
            raise DomainError(409, "Сбой перед коммитом")

        monkeypatch.setattr(crud_tenders, "_reconcile_after_delete", failing)

        response = committing_client.delete(url, params=params)

        assert response.status_code == 409
        assert all(tmp_storage.exists(k) for k in keys)
        committing_db.expire_all()
        assert committing_db.execute(sa.select(sa.func.count()).select_from(KpSentFile)).scalar_one() == 2


class TestParticipantWithoutEstimate:
    def test_files_of_an_offer_without_estimate_are_removed(self, stage, db_session, tmp_storage):
        """Смета ушла заменой раунда, оффер остался и несёт отправленный файл: ключ
        собирается по офферу, а не по смете."""
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", estimate=False)
        other = stage.participant(rnd, "Подрядчик Б", estimate=False)
        user = _user(stage.f)
        key = _sent(db_session, tmp_storage, part.offer.id, user)
        other_key = _sent(db_session, tmp_storage, other.offer.id, user)
        preview = crud_tenders.participant_deletion_preview(db_session, stage.tender.id, part.offer.package_id)
        assert preview["estimates_count"] == 0

        crud_tenders.delete_participant(
            db_session,
            stage.tender.id,
            part.offer.package_id,
            confirmation_token=preview["confirmation_token"],
            storage=tmp_storage,
        )

        assert not tmp_storage.exists(key)
        assert tmp_storage.exists(other_key)


class TestConfirmationToken:
    def _preview(self, db, stage, part):
        return crud_tenders.participant_deletion_preview(db, stage.tender.id, part.offer.package_id)

    def test_composition_carries_both_counts(self, stage, db_session, tmp_storage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А")
        item = stage.kp(part, D("10"))
        user = _user(stage.f)
        _agree(db_session, item, D("9"), user)
        _comment(db_session, item, "Т", user)
        _line_rung(db_session, item, "v", "cls", {"level": "v", "variant_id": 1}, user)
        _sent(db_session, tmp_storage, part.offer.id, user)
        _sent(db_session, tmp_storage, part.offer.id, user)

        preview = self._preview(db_session, stage, part)

        assert preview["decisions_count"] == 3
        assert preview["sent_files_count"] == 2

    def test_a_decision_changes_the_token(self, stage, db_session):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А")
        item = stage.kp(part, D("10"))
        before = self._preview(db_session, stage, part)["confirmation_token"]

        _agree(db_session, item, D("9"), _user(stage.f))

        assert self._preview(db_session, stage, part)["confirmation_token"] != before

    def test_a_sent_file_changes_the_token(self, stage, db_session, tmp_storage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А")
        stage.kp(part, D("10"))
        before = self._preview(db_session, stage, part)["confirmation_token"]

        _sent(db_session, tmp_storage, part.offer.id, _user(stage.f))

        assert self._preview(db_session, stage, part)["confirmation_token"] != before

    def test_a_second_decision_of_another_kind_changes_the_token(self, stage, db_session):
        """Токен держит число решений, а не факт «решения есть»: вторая — другого рода."""
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А")
        item = stage.kp(part, D("10"))
        user = _user(stage.f)
        _agree(db_session, item, D("9"), user)
        before = self._preview(db_session, stage, part)["confirmation_token"]

        _comment(db_session, item, "Т", user)

        assert self._preview(db_session, stage, part)["confirmation_token"] != before

    def test_a_second_sent_file_changes_the_token(self, stage, db_session, tmp_storage):
        """Токен держит число отправленных файлов, а не факт «файл есть»."""
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А")
        stage.kp(part, D("10"))
        user = _user(stage.f)
        _sent(db_session, tmp_storage, part.offer.id, user)
        before = self._preview(db_session, stage, part)["confirmation_token"]

        _sent(db_session, tmp_storage, part.offer.id, user, data=b"second")

        assert self._preview(db_session, stage, part)["confirmation_token"] != before

    def test_another_participants_decision_does_not_change_the_token(self, stage, db_session):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А")
        stage.kp(part, D("10"))
        other = stage.participant(rnd, "Подрядчик Б")
        other_item = stage.kp(other, D("10"))
        before = self._preview(db_session, stage, part)["confirmation_token"]

        _agree(db_session, other_item, D("9"), _user(stage.f))

        assert self._preview(db_session, stage, part)["confirmation_token"] == before

    def test_stale_token_after_a_new_decision_is_refused_with_counts(self, stage, db_session, tmp_storage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А")
        item = stage.kp(part, D("10"))
        user = _user(stage.f)
        old = self._preview(db_session, stage, part)["confirmation_token"]
        _agree(db_session, item, D("9"), user)
        key = _sent(db_session, tmp_storage, part.offer.id, user)

        with pytest.raises(DomainError) as exc:
            crud_tenders.delete_participant(
                db_session, stage.tender.id, part.offer.package_id, confirmation_token=old, storage=tmp_storage
            )

        assert exc.value.status_code == 409
        assert exc.value.code == "confirmation_required"
        assert exc.value.context["decisions_count"] == 1
        assert exc.value.context["sent_files_count"] == 1
        assert "решений по строкам КП — 1" in exc.value.detail
        assert "отправленных файлов — 1" in exc.value.detail
        assert tmp_storage.exists(key)


class TestExportRacesWithOwnerDeletion:
    """Выгрузка и удаление этапа или тендера на двух соединениях: файл-сирота не остаётся.

    Барьер стоит после вставки записи и до коммита выгрузки: удаление, пришедшее в этот
    промежуток, собрало бы ключи без новой записи и каскадом унесло бы её. Замок выгрузки на
    тендер и этап заставляет удаление ждать и собрать ключ новой записи."""

    @pytest.fixture
    def scene(self, committing_db, committing_factories, committing_session_factory):
        tender = committing_factories.TenderFactory.create()
        rnd = committing_factories.TenderRoundFactory.create(tender=tender, stage_no=1)
        committing_db.flush()
        offer = committing_factories.OfferFactory.create(round=rnd)
        user = committing_factories.UserFactory.create()
        committing_db.commit()
        # Токен — тот, что пользователь увидел в предпросмотре удаления до выгрузки.
        token = crud_tenders.participant_deletion_preview(committing_db, tender.id, offer.package_id)["confirmation_token"]
        committing_db.commit()

        class Scene:
            tender_id = tender.id
            round_id = rnd.id
            offer_id = offer.id
            package_id = offer.package_id
            confirmation_token = token
            user_id = user.id
            session_factory = committing_session_factory

        return Scene()

    @staticmethod
    def _stub_the_book(monkeypatch, tmp_storage, *, check_offer: bool = False) -> list[str]:
        """Сборка книги заглушкой, `storage.save` записывает ключи; возвращает их список.

        `check_offer`: заглушка проверяет оффер этапа так же, как настоящая сборка
        (`_offer_context`: 404 «Оффер … не найден в этапе …»), — без этого выгрузка после
        удаления участника не узнала бы, что оффера больше нет."""
        from types import SimpleNamespace

        from crud import kp_sent_files

        header = SimpleNamespace(filename="КП.xlsx", level_label="Уровень цен КП")

        def assemble(db, tender_id, round_id, offer_id, **_):
            if check_offer:
                kp_sent_files._check_offer(db, tender_id, round_id, offer_id)
            return header, [], D("20")

        monkeypatch.setattr(kp_sent_files, "_assemble", assemble)
        monkeypatch.setattr(kp_sent_files, "build_kp_file", lambda *a, **k: b"book")
        saved: list[str] = []
        real_save = tmp_storage.save

        def recording_save(data):
            key = real_save(data)
            saved.append(key)
            return key

        monkeypatch.setattr(tmp_storage, "save", recording_save)
        return saved

    @staticmethod
    def _wait_until_blocked(session_factory, pid: int, *, timeout: float = 15.0) -> bool:
        """Ждёт, пока backend `pid` встанет на чужой замок (`pg_blocking_pids`), а не любой."""
        import time

        deadline = time.monotonic() + timeout
        with session_factory() as probe:
            while time.monotonic() < deadline:
                blocked = probe.execute(
                    sa.text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}
                ).scalar_one()
                probe.rollback()
                if blocked:
                    return True
                time.sleep(0.05)
        return False

    def test_participant_deletion_after_an_uncommitted_export_is_refused_by_its_stale_token(
        self, scene, tmp_storage, monkeypatch
    ):
        """Удаление участника приходит, когда выгрузка уже вставила запись, но не закоммитила.
        Замок выгрузки заставляет удаление ждать; дождавшись, оно пересчитывает состав, видит
        новый отправленный файл и отказывает устаревшему токену — файл остаётся при записи.
        Без замка удаление посчитало бы состав без новой записи, токен совпал бы, каскад унёс
        бы запись, а файл остался бы в хранилище без владельца."""
        import threading
        import time

        from crud import kp_sent_files

        saved = self._stub_the_book(monkeypatch, tmp_storage)
        inserted, may_commit = threading.Event(), threading.Event()
        outcome: dict = {}

        def exporter():
            session = scene.session_factory()
            try:
                session.execute(sa.text("SET lock_timeout = '20s'"))

                @sa.event.listens_for(session.connection(), "after_cursor_execute")
                def barrier_after_insert(conn, cursor, statement, parameters, context, executemany):
                    if "INSERT INTO kp_sent_files" in statement and not inserted.is_set():
                        inserted.set()
                        may_commit.wait(timeout=15)

                kp_sent_files.export_file(
                    session, tmp_storage, scene.tender_id, scene.round_id, scene.offer_id,
                    inflation_series_id=None, user_id=scene.user_id,
                )
                outcome["export"] = "done"
            except Exception as e:  # noqa: BLE001 — исход потока проверяется ниже
                outcome["export"] = getattr(e, "status_code", type(e).__name__)
            finally:
                session.close()

        def deleter():
            session = scene.session_factory()
            try:
                session.execute(sa.text("SET lock_timeout = '20s'"))
                outcome["delete_pid"] = session.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                crud_tenders.delete_participant(
                    session, scene.tender_id, scene.package_id,
                    confirmation_token=scene.confirmation_token, storage=tmp_storage,
                )
                outcome["delete"] = "done"
            except Exception as e:  # noqa: BLE001
                outcome["delete"] = getattr(e, "status_code", type(e).__name__)
                outcome["delete_code"] = getattr(e, "code", None)
            finally:
                session.close()

        exporting = threading.Thread(target=exporter, daemon=True)
        deleting = threading.Thread(target=deleter, daemon=True)
        exporting.start()
        try:
            assert inserted.wait(timeout=15), "выгрузка не дошла до вставки записи"
            deleting.start()
            for _ in range(300):
                if "delete_pid" in outcome or not deleting.is_alive():
                    break
                time.sleep(0.05)
            assert self._wait_until_blocked(scene.session_factory, outcome["delete_pid"]), "удаление не встало на замок"
            assert deleting.is_alive(), "удаление прошло мимо незавершённой выгрузки"
        finally:
            may_commit.set()
        exporting.join(timeout=30)
        deleting.join(timeout=30)
        assert not exporting.is_alive() and not deleting.is_alive(), "потоки зависли"

        assert outcome["export"] == "done"
        assert len(saved) == 1
        with scene.session_factory() as check:
            recorded = set(check.execute(sa.select(KpSentFile.file_key)).scalars())
        assert [key for key in saved if tmp_storage.exists(key) and key not in recorded] == [], (
            "в хранилище остался файл без владельца"
        )
        assert (outcome["delete"], outcome.get("delete_code")) == (409, "confirmation_required")
        assert recorded == set(saved) and tmp_storage.exists(saved[0])

    @pytest.mark.parametrize("command", ["round", "tender", "participant"])
    def test_an_export_that_waited_for_a_deletion_gets_404_and_saves_nothing(
        self, scene, tmp_storage, monkeypatch, command
    ):
        """Обратный порядок: удаление взяло замки и стоит перед коммитом, выгрузка приходит.
        Выгрузка обязана ждать, а дождавшись — получить 404 до `storage.save`: ни файла, ни
        записи. Без замка выгрузка прошла бы мимо незакоммиченного удаления, сохранила бы
        файл и упала бы только на внешнем ключе вставки."""
        import threading
        import time

        from crud import kp_sent_files
        from services.maintenance import purge_files_best_effort

        saved = self._stub_the_book(monkeypatch, tmp_storage, check_offer=True)
        locked, may_commit = threading.Event(), threading.Event()
        outcome: dict = {}

        def deleter():
            session = scene.session_factory()
            try:
                session.execute(sa.text("SET lock_timeout = '20s'"))

                @sa.event.listens_for(session, "before_commit")
                def barrier_before_commit(_session):
                    if not locked.is_set():
                        locked.set()
                        may_commit.wait(timeout=15)

                if command == "round":
                    purge_files_best_effort(
                        tmp_storage, crud_tenders.delete_round(session, scene.tender_id, scene.round_id), context="тест"
                    )
                elif command == "tender":
                    purge_files_best_effort(tmp_storage, crud_tenders.delete_tender(session, scene.tender_id), context="тест")
                else:
                    crud_tenders.delete_participant(
                        session, scene.tender_id, scene.package_id,
                        confirmation_token=scene.confirmation_token, storage=tmp_storage,
                    )
                outcome["delete"] = "done"
            except Exception as e:  # noqa: BLE001
                outcome["delete"] = getattr(e, "status_code", type(e).__name__)
            finally:
                session.close()

        def exporter():
            session = scene.session_factory()
            try:
                session.execute(sa.text("SET lock_timeout = '20s'"))
                outcome["export_pid"] = session.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                kp_sent_files.export_file(
                    session, tmp_storage, scene.tender_id, scene.round_id, scene.offer_id,
                    inflation_series_id=None, user_id=scene.user_id,
                )
                outcome["export"] = "done"
            except Exception as e:  # noqa: BLE001
                outcome["export"] = getattr(e, "status_code", type(e).__name__)
            finally:
                session.close()

        deleting = threading.Thread(target=deleter, daemon=True)
        exporting = threading.Thread(target=exporter, daemon=True)
        deleting.start()
        try:
            assert locked.wait(timeout=15), "удаление не дошло до коммита"
            exporting.start()
            for _ in range(300):
                if "export_pid" in outcome or not exporting.is_alive():
                    break
                time.sleep(0.05)
            assert self._wait_until_blocked(scene.session_factory, outcome["export_pid"]), "выгрузка не встала на замок"
            assert exporting.is_alive(), "выгрузка прошла мимо незакоммиченного удаления"
        finally:
            may_commit.set()
        deleting.join(timeout=30)
        exporting.join(timeout=30)
        assert not exporting.is_alive() and not deleting.is_alive(), "потоки зависли"

        assert outcome["delete"] == "done"
        assert outcome["export"] == 404
        assert saved == [], "выгрузка сохранила файл, хотя владельца уже нет"
        with scene.session_factory() as check:
            assert check.execute(sa.select(sa.func.count()).select_from(KpSentFile)).scalar_one() == 0

    @pytest.mark.parametrize("command", ["round", "tender"])
    def test_no_orphan_file_stays_in_the_storage(self, scene, tmp_storage, monkeypatch, command):
        import threading
        from types import SimpleNamespace

        from crud import kp_sent_files
        from services.maintenance import purge_files_best_effort
        from tests.integration.test_tenders_concurrency import _wait_until_a_backend_blocks

        header = SimpleNamespace(filename="КП.xlsx", level_label="Уровень цен КП")
        monkeypatch.setattr(kp_sent_files, "_assemble", lambda *a, **k: (header, [], D("20")))
        monkeypatch.setattr(kp_sent_files, "build_kp_file", lambda *a, **k: b"book")
        saved: list[str] = []
        real_save = tmp_storage.save

        def recording_save(data):
            key = real_save(data)
            saved.append(key)
            return key

        monkeypatch.setattr(tmp_storage, "save", recording_save)

        inserted, may_commit = threading.Event(), threading.Event()
        outcome: dict = {}

        def exporter():
            session = scene.session_factory()
            try:
                session.execute(sa.text("SET lock_timeout = '20s'"))

                @sa.event.listens_for(session.connection(), "after_cursor_execute")
                def barrier_after_insert(conn, cursor, statement, parameters, context, executemany):
                    if "INSERT INTO kp_sent_files" in statement and not inserted.is_set():
                        inserted.set()
                        may_commit.wait(timeout=15)

                kp_sent_files.export_file(
                    session, tmp_storage, scene.tender_id, scene.round_id, scene.offer_id,
                    inflation_series_id=None, user_id=scene.user_id,
                )
                outcome["export"] = "done"
            except DomainError as e:
                outcome["export"] = e.status_code
            finally:
                session.close()

        def deleter():
            session = scene.session_factory()
            try:
                session.execute(sa.text("SET lock_timeout = '20s'"))
                if command == "round":
                    keys = crud_tenders.delete_round(session, scene.tender_id, scene.round_id)
                else:
                    keys = crud_tenders.delete_tender(session, scene.tender_id)
                purge_files_best_effort(tmp_storage, keys, context="тест гонки")
                outcome["delete"] = "done"
            finally:
                session.close()

        exporting = threading.Thread(target=exporter, daemon=True)
        deleting = threading.Thread(target=deleter, daemon=True)
        exporting.start()
        try:
            assert inserted.wait(timeout=15), "выгрузка не дошла до вставки записи"
            deleting.start()
            assert _wait_until_a_backend_blocks(scene.session_factory), "удаление не встало на замок"
            assert deleting.is_alive(), "удаление прошло мимо незавершённой выгрузки"
        finally:
            may_commit.set()
        exporting.join(timeout=30)
        deleting.join(timeout=30)
        assert not exporting.is_alive() and not deleting.is_alive(), "потоки зависли"

        assert outcome["delete"] == "done"
        assert saved, "выгрузка не сохраняла файл: гонка не воспроизведена"
        assert [key for key in saved if tmp_storage.exists(key)] == [], "в хранилище остался файл без владельца"
