"""Команды отметки победителя тендера (спека Б2 §2.4, §2.6, §2.7): отметить,
снять, «договор не заключён», кандидаты, привязать, фрагмент карточки тендера
и запрет нового этапа при действующей отметке.

Тесты идут на сессиях с настоящими commit-ами: отказ через ключ откатывает
сессию (`translating_integrity`), и корпус на общей транзакции пропал бы вместе
с ней. Состояние «после отказа» читается свежей сессией. Отказ проверяется
кодом, статусом и неизменённой базой, а не подстрокой текста.
"""
from __future__ import annotations

import copy
import datetime as dt
import threading
import time
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
    EstimateRawData,
    ImportJob,
    ImportJobStatus,
    ObjectModel,
    Offer,
    OfferPackage,
    ProposalSummaryLine,
    TenderAward,
    TenderRound,
    UserRole,
)
from parser.constants import JSON_KEY_CONTRACTOR_INN, JSON_KEY_LOTS, JSON_KEY_PROPOSALS
from services.category_resolution import CategoryResolver
from services.round_import import import_round
from services.unit_resolution import UnitResolver
from tests.payloads import position, proposal, round_payload

pytestmark = pytest.mark.integration

# Независимые литералы: эталон не вычисляется кодом, который проверяется.
CARD_KEYS_BEFORE_AWARDS = {
    "id", "tender_number", "title", "notes", "object_id", "object_title", "object_address",
    "rate_class_id", "rate_class_title", "created_at", "rounds", "participants", "cells",
}
AWARD_KEYS = {
    "id", "offer_id", "package_id", "contractor_id", "contractor_title", "contractor_inn", "round_id",
    "stage_no", "estimate_id", "total_including_vat", "awarded_at", "awarded_by_email", "contract",
}
CANDIDATE_KEYS = {
    "id", "contract_number", "signed_date", "object_title", "contractor_title", "base_total_including_vat",
}


def _participant(inn: str, title: str):
    return proposal(
        [position(job_title="Работа", unit="м2", unit_cost_total="10", total_cost_total="10")],
        title=title, inn=inn,
    )


@pytest.fixture
def scene(committing_db, committing_factories, committing_session_factory):
    """Тендер: этап 1 (участники А, Б, В) и этап 2 (А, Б) — настоящим
    `import_round`; участник Г — оферта финального этапа без КП. Администраторы
    `user` и `other_user`. Второй тендер `foreign` с одним этапом."""
    f = committing_factories
    db = committing_db
    tender = f.TenderFactory.create()
    r1 = f.TenderRoundFactory.create(tender=tender, stage_no=1)
    r2 = f.TenderRoundFactory.create(tender=tender, stage_no=2)
    foreign = f.TenderFactory.create()
    foreign_round = f.TenderRoundFactory.create(tender=foreign, stage_no=1)
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
    load(foreign_round, [_participant("7700000009", "ООО Чужой")])
    package_d = f.OfferPackageFactory.create(tender=tender)
    offer_d = f.OfferFactory.create(round=r2, package=package_d)
    user = f.UserFactory.create(role=UserRole.admin)
    other_user = f.UserFactory.create(role=UserRole.admin)
    db.commit()

    def offer_id(round_id, inn):
        return db.execute(
            sa.select(Offer.id).join(OfferPackage, OfferPackage.id == Offer.package_id)
            .join(Contractor, Contractor.id == OfferPackage.contractor_id)
            .where(Offer.round_id == round_id, Contractor.inn == inn)
        ).scalar_one()

    def contractor_id(inn):
        return db.execute(sa.select(Contractor.id).where(Contractor.inn == inn)).scalar_one()

    return SimpleNamespace(
        sf=committing_session_factory, db=db, f=f,
        tender_id=tender.id, object_id=tender.object_id, tender_number=tender.tender_number,
        r1_id=r1.id, r2_id=r2.id, foreign_id=foreign.id,
        offer_a=offer_id(r2.id, "7700000001"), offer_b=offer_id(r2.id, "7700000002"),
        offer_stage1=offer_id(r1.id, "7700000003"), offer_d=offer_d.id,
        foreign_offer=offer_id(foreign_round.id, "7700000009"),
        contractor_a=contractor_id("7700000001"), contractor_b=contractor_id("7700000002"),
        user_id=user.id, other_user_id=other_user.id, user_email=user.email, other_email=other_user.email,
    )


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _award(s, offer_id, *, user_id=None):
    with s.sf() as db:
        return crud_awards.award_winner(
            db, s.tender_id, offer_id=offer_id, user_id=user_id or s.user_id,
        )


def _awards(s):
    with s.sf() as db:
        return db.execute(sa.select(TenderAward).order_by(TenderAward.id)).scalars().all()


def _call(s, fn, *args, **kwargs):
    with s.sf() as db:
        return fn(db, *args, **kwargs)


def _refused(fn) -> DomainError:
    with pytest.raises(DomainError) as info:
        fn()
    return info.value


def _contract(s, *, contractor_id=None, object_id=None, number=None, signed=dt.date(2026, 1, 10)):
    f = s.f
    kwargs = {"signed_date": signed}
    contractor = contractor_id if contractor_id is not None else s.contractor_a
    obj = object_id if object_id is not None else s.object_id
    if number:
        kwargs["contract_number"] = number
    contract = f.ContractFactory.create(
        object=s.db.get(ObjectModel, obj), contractor=s.db.get(Contractor, contractor), **kwargs,
    )
    s.db.commit()
    return contract.id


def _contract_award_id(s, contract_id):
    with s.sf() as db:
        return db.execute(sa.select(Contract.tender_award_id).where(Contract.id == contract_id)).scalar_one()


def _contract_estimate(s, contract_id, *, amendment_no, total):
    f = s.f
    estimate = f.EstimateFactory.create(contract=s.db.get(Contract, contract_id), amendment_no=amendment_no)
    lot = f.LotFactory.create(estimate=estimate)
    prop = f.ProposalFactory.create(lot=lot)
    s.db.flush()
    s.db.add(ProposalSummaryLine(
        proposal_id=prop.id, summary_key="total_cost_including_vat", job_title="ИТОГО", total_cost=total,
    ))
    s.db.commit()
    return estimate.id


def _rounds_count(s):
    with s.sf() as db:
        return db.execute(
            sa.select(sa.func.count()).select_from(TenderRound).where(TenderRound.tender_id == s.tender_id)
        ).scalar_one()


def _only_award_id(s):
    rows = _awards(s)
    assert len(rows) == 1
    return rows[0].id


def _second_tender_award(s):
    """Второй тендер на том же объекте с тем же участником А и действующей
    отметкой на нём: (id тендера, id отметки)."""
    # `object=`, а не `object_id=`: SubFactory фабрики иначе заводит тендеру
    # чужой объект (ревью задачи 3 — привязка тогда нарушала заодно и объект).
    other_tender = s.f.TenderFactory.create(object=s.db.get(ObjectModel, s.object_id))
    other_round = s.f.TenderRoundFactory.create(tender=other_tender, stage_no=1)
    s.db.flush()
    assert other_tender.object_id == s.object_id
    import_round(
        s.db, tender_round=other_round, data=round_payload([_participant("7700000001", "ООО А")]),
        parser_version="4.0.0", import_job_id=None, replace=False, unit_resolver=UnitResolver(s.db),
        category_resolver=CategoryResolver.from_db(s.db),
    )
    s.db.commit()
    other_offer = s.db.execute(sa.select(Offer.id).where(Offer.round_id == other_round.id)).scalar_one()
    with s.sf() as db:
        crud_awards.award_winner(db, other_tender.id, offer_id=other_offer, user_id=s.user_id)
    with s.sf() as db:
        other_award = db.execute(
            sa.select(TenderAward.id).where(TenderAward.tender_id == other_tender.id)
        ).scalar_one()
    return other_tender.id, other_award


# ---------------------------------------------------------------------------
#  kp_inn_of на реальном разборе
# ---------------------------------------------------------------------------

class TestAwardWinner:
    def test_award_creates_an_active_award_from_the_offer_kp(self, scene):
        card = _award(scene, scene.offer_a)

        rows = _awards(scene)
        assert len(rows) == 1
        row = rows[0]
        with scene.sf() as db:
            kp_id = db.execute(sa.select(Estimate.id).where(Estimate.offer_id == scene.offer_a)).scalar_one()
        assert row.estimate_id == kp_id
        assert row.kp_inn == "7700000001"
        assert row.offer_id == scene.offer_a
        assert row.object_id == scene.object_id
        assert row.tender_id == scene.tender_id
        assert row.contractor_id == scene.contractor_a
        assert row.awarded_by == scene.user_id
        assert row.awarded_at is not None
        assert row.not_concluded_on is None
        assert card["award"]["id"] == row.id

    def test_the_offer_of_the_first_stage_is_refused_as_not_final(self, scene):
        err = _refused(lambda: _award(scene, scene.offer_stage1))
        assert (err.status_code, err.code) == (409, "award_not_final_stage")
        assert _awards(scene) == []

    def test_an_offer_without_kp_is_refused(self, scene):
        err = _refused(lambda: _award(scene, scene.offer_d))
        assert (err.status_code, err.code) == (409, "award_no_kp")
        assert _awards(scene) == []

    def test_an_active_round_job_blocks_the_award(self, scene):
        scene.f.ImportJobFactory.create(
            contract=None, round_id=scene.r2_id, status=ImportJobStatus.parsing.value,
        )
        scene.db.commit()
        err = _refused(lambda: _award(scene, scene.offer_a))
        assert (err.status_code, err.code) == (409, "active_import")
        assert _awards(scene) == []

    def test_a_finished_round_job_does_not_block_the_award(self, scene):
        scene.f.ImportJobFactory.create(
            contract=None, round_id=scene.r2_id, status=ImportJobStatus.done.value,
        )
        scene.db.commit()
        _award(scene, scene.offer_a)
        assert len(_awards(scene)) == 1

    def test_an_active_job_of_another_round_does_not_block_the_award(self, scene):
        scene.f.ImportJobFactory.create(
            contract=None, round_id=scene.r1_id, status=ImportJobStatus.parsing.value,
        )
        scene.db.commit()
        _award(scene, scene.offer_a)
        assert len(_awards(scene)) == 1

    def test_a_second_award_is_refused_and_names_the_current_winner(self, scene):
        _award(scene, scene.offer_a)
        err = _refused(lambda: _award(scene, scene.offer_b))
        assert (err.status_code, err.code) == (409, "award_exists")
        assert "ООО А" in err.detail
        rows = _awards(scene)
        assert [r.offer_id for r in rows] == [scene.offer_a]

    def test_an_offer_of_another_tender_is_not_found(self, scene):
        err = _refused(lambda: _award(scene, scene.foreign_offer))
        assert err.status_code == 404
        assert _awards(scene) == []

    def test_an_unknown_offer_is_not_found(self, scene):
        err = _refused(lambda: _award(scene, 987654))
        assert err.status_code == 404

    def _set_raw(self, s, offer_id, mutate):
        with s.sf() as db:
            raw = db.execute(
                sa.select(EstimateRawData).join(Estimate, Estimate.id == EstimateRawData.estimate_id)
                .where(Estimate.offer_id == offer_id)
            ).scalar_one()
            data = copy.deepcopy(raw.raw_data)
            mutate(data)
            raw.raw_data = data
            db.commit()

    def test_a_kp_without_inn_is_unidentified(self, scene):
        def blank(data):
            for lot in data[JSON_KEY_LOTS].values():
                for block in lot[JSON_KEY_PROPOSALS].values():
                    block[JSON_KEY_CONTRACTOR_INN] = ""

        self._set_raw(scene, scene.offer_a, blank)
        err = _refused(lambda: _award(scene, scene.offer_a))
        assert (err.status_code, err.code) == (409, "award_kp_unidentified")
        assert _awards(scene) == []

    def test_a_kp_with_two_different_inns_is_unidentified(self, scene):
        def split(data):
            lots = data[JSON_KEY_LOTS]
            second = copy.deepcopy(next(iter(lots.values())))
            for block in second[JSON_KEY_PROPOSALS].values():
                block[JSON_KEY_CONTRACTOR_INN] = "7700000099"
            lots["lot_extra"] = second

        self._set_raw(scene, scene.offer_a, split)
        err = _refused(lambda: _award(scene, scene.offer_a))
        assert (err.status_code, err.code) == (409, "award_kp_unidentified")
        assert _awards(scene) == []

    def test_one_inn_written_with_spaces_in_the_file_is_taken_canonical(self, scene):
        def spaced(data):
            for lot in data[JSON_KEY_LOTS].values():
                for block in lot[JSON_KEY_PROPOSALS].values():
                    block[JSON_KEY_CONTRACTOR_INN] = "77 0000 0001"

        self._set_raw(scene, scene.offer_a, spaced)
        _award(scene, scene.offer_a)
        assert _awards(scene)[0].kp_inn == "7700000001"

    def test_kp_inn_is_the_file_inn_not_the_edited_card_inn(self, scene):
        with scene.sf() as db:
            contractor = db.get(Contractor, scene.contractor_a)
            contractor.inn = "7799999999"
            db.commit()
        _award(scene, scene.offer_a)
        row = _awards(scene)[0]
        assert row.kp_inn == "7700000001"
        assert row.contractor_id == scene.contractor_a

    def test_kp_inn_is_the_file_inn_when_cards_inns_are_swapped(self, scene):
        with scene.sf() as db:
            db.get(Contractor, scene.contractor_a).inn = "7700000777"
            db.commit()
            db.get(Contractor, scene.contractor_b).inn = "7700000001"
            db.commit()
            db.get(Contractor, scene.contractor_a).inn = "7700000002"
            db.commit()
        _award(scene, scene.offer_a)
        row = _awards(scene)[0]
        assert row.kp_inn == "7700000001"
        assert row.contractor_id == scene.contractor_a


class TestRemoveAward:
    def test_remove_deletes_the_active_award_without_a_trace(self, scene):
        _award(scene, scene.offer_a)
        award_id = _only_award_id(scene)
        with scene.sf() as db:
            card = crud_awards.remove_award(db, scene.tender_id, award_id)
        assert card["award"] is None
        assert card["award_history"] == []
        assert _awards(scene) == []

    def test_a_closed_award_cannot_be_removed(self, scene):
        _award(scene, scene.offer_a)
        award_id = _only_award_id(scene)
        with scene.sf() as db:
            crud_awards.mark_not_concluded(
                db, scene.tender_id, award_id, not_concluded_on=dt.date(2026, 2, 1), note=None,
                user_id=scene.user_id,
            )
        err = _refused(lambda: _call(scene, crud_awards.remove_award, scene.tender_id, award_id))
        assert (err.status_code, err.code) == (409, "award_not_active")
        assert len(_awards(scene)) == 1

    def test_an_award_with_a_contract_cannot_be_removed(self, scene):
        _award(scene, scene.offer_a)
        award_id = _only_award_id(scene)
        contract_id = _contract(scene, number="ГП-777")
        with scene.sf() as db:
            crud_awards.link_contract(db, scene.tender_id, award_id, contract_id=contract_id)
        err = _refused(lambda: _call(scene, crud_awards.remove_award, scene.tender_id, award_id))
        assert (err.status_code, err.code) == (409, "award_has_contract")
        assert "ГП-777" in err.detail
        assert len(_awards(scene)) == 1
        assert _contract_award_id(scene, contract_id) == award_id

    def test_the_award_of_another_tender_is_not_found(self, scene):
        _award(scene, scene.offer_a)
        award_id = _only_award_id(scene)
        err = _refused(lambda: _call(scene, crud_awards.remove_award, scene.foreign_id, award_id))
        assert err.status_code == 404
        assert len(_awards(scene)) == 1

    def test_a_removed_award_is_not_found_on_a_second_removal(self, scene):
        _award(scene, scene.offer_a)
        award_id = _only_award_id(scene)
        with scene.sf() as db:
            crud_awards.remove_award(db, scene.tender_id, award_id)
        err = _refused(lambda: _call(scene, crud_awards.remove_award, scene.tender_id, award_id))
        assert err.status_code == 404


class TestNotConcluded:
    def _close(self, s, award_id, *, note="Отказались от подписания", user_id=None, on=dt.date(2026, 2, 1)):
        with s.sf() as db:
            return crud_awards.mark_not_concluded(
                db, s.tender_id, award_id, not_concluded_on=on, note=note, user_id=user_id or s.user_id,
            )

    def test_the_four_fields_are_written(self, scene):
        _award(scene, scene.offer_a)
        award_id = _only_award_id(scene)
        self._close(scene, award_id, user_id=scene.other_user_id)
        row = _awards(scene)[0]
        assert row.not_concluded_on == dt.date(2026, 2, 1)
        assert row.not_concluded_note == "Отказались от подписания"
        assert row.not_concluded_by == scene.other_user_id
        assert row.not_concluded_at is not None
        assert row.awarded_by == scene.user_id

    @pytest.mark.parametrize("note", [None, "", "   \t "])
    def test_an_empty_or_blank_note_is_stored_as_null(self, scene, note):
        _award(scene, scene.offer_a)
        self._close(scene, _only_award_id(scene), note=note)
        assert _awards(scene)[0].not_concluded_note is None

    def test_a_closed_award_cannot_be_closed_again(self, scene):
        _award(scene, scene.offer_a)
        award_id = _only_award_id(scene)
        self._close(scene, award_id)
        err = _refused(lambda: self._close(scene, award_id, note="другая", on=dt.date(2026, 3, 1)))
        assert (err.status_code, err.code) == (409, "award_not_active")
        row = _awards(scene)[0]
        assert row.not_concluded_note == "Отказались от подписания"
        assert row.not_concluded_on == dt.date(2026, 2, 1)

    def test_an_award_with_a_contract_cannot_be_closed(self, scene):
        _award(scene, scene.offer_a)
        award_id = _only_award_id(scene)
        contract_id = _contract(scene, number="ГП-778")
        with scene.sf() as db:
            crud_awards.link_contract(db, scene.tender_id, award_id, contract_id=contract_id)
        err = _refused(lambda: self._close(scene, award_id))
        assert (err.status_code, err.code) == (409, "award_has_contract")
        assert "ГП-778" in err.detail
        row = _awards(scene)[0]
        assert row.not_concluded_on is None
        assert row.not_concluded_by is None

    def test_another_participant_can_be_awarded_after_it(self, scene):
        _award(scene, scene.offer_a)
        self._close(scene, _only_award_id(scene))
        _award(scene, scene.offer_b)
        rows = _awards(scene)
        assert [r.offer_id for r in rows] == [scene.offer_a, scene.offer_b]
        assert [r.not_concluded_on is None for r in rows] == [False, True]

    def test_the_same_participant_can_be_awarded_again_and_history_shows_both(self, scene):
        _award(scene, scene.offer_a)
        self._close(scene, _only_award_id(scene))
        card = _award(scene, scene.offer_a)
        rows = _awards(scene)
        assert [r.offer_id for r in rows] == [scene.offer_a, scene.offer_a]
        kinds = [(e["award_id"], e["kind"]) for e in card["award_history"]]
        assert kinds == [(rows[0].id, "awarded"), (rows[0].id, "not_concluded"), (rows[1].id, "awarded")]


class TestAwardCardFragment:
    def test_a_tender_without_awards_has_null_award_and_empty_history(self, scene):
        with scene.sf() as db:
            card = crud_tenders.get_tender_card(db, scene.tender_id)
        assert card["award"] is None
        assert card["award_history"] == []

    def test_the_card_gets_two_keys_and_loses_none(self, scene):
        with scene.sf() as db:
            card = crud_tenders.get_tender_card(db, scene.tender_id)
        assert set(card) == CARD_KEYS_BEFORE_AWARDS | {"award", "award_history"}

    def test_the_active_award_has_the_documented_shape(self, scene):
        card = _award(scene, scene.offer_a)
        award = card["award"]
        assert set(award) == AWARD_KEYS
        assert award["offer_id"] == scene.offer_a
        assert award["contractor_id"] == scene.contractor_a
        assert award["contractor_title"] == "ООО А"
        assert award["contractor_inn"] == "7700000001"
        assert award["round_id"] == scene.r2_id
        assert award["stage_no"] == 2
        assert award["total_including_vat"] == "1200.00"
        assert award["awarded_by_email"] == scene.user_email
        assert award["contract"] is None
        assert isinstance(award["awarded_at"], str)

    def test_the_total_is_null_when_the_kp_total_is_unavailable(self, scene):
        _award(scene, scene.offer_a)
        with scene.sf() as db:
            db.execute(sa.delete(ProposalSummaryLine))
            db.commit()
            card = crud_tenders.get_tender_card(db, scene.tender_id)
        assert card["award"]["total_including_vat"] is None

    def test_a_closed_award_is_not_in_award_but_in_history(self, scene):
        _award(scene, scene.offer_a)
        with scene.sf() as db:
            card = crud_awards.mark_not_concluded(
                db, scene.tender_id, _only_award_id(scene), not_concluded_on=dt.date(2026, 2, 1),
                note="Причина", user_id=scene.other_user_id,
            )
        assert card["award"] is None
        awarded, closed = card["award_history"]
        assert awarded["kind"] == "awarded" and closed["kind"] == "not_concluded"
        assert awarded["by_email"] == scene.user_email
        assert closed["by_email"] == scene.other_email
        assert closed["not_concluded_on"] == "2026-02-01"
        assert closed["note"] == "Причина"
        event_keys = {
            "award_id", "kind", "package_id", "contractor_title", "awarded_at", "not_concluded_on",
            "note", "by_email", "is_active",
        }
        assert set(awarded) == event_keys and set(closed) == event_keys
        assert isinstance(awarded["awarded_at"], str)
        assert awarded["not_concluded_on"] is None and awarded["note"] is None
        assert closed["awarded_at"] is None
        assert awarded["contractor_title"] == closed["contractor_title"] == "ООО А"
        assert awarded["package_id"] == closed["package_id"]

    def test_history_is_ordered_by_award_id_when_the_closing_date_precedes_the_record_date(self, scene):
        _award(scene, scene.offer_a)
        with scene.sf() as db:
            crud_awards.mark_not_concluded(
                db, scene.tender_id, _only_award_id(scene), not_concluded_on=dt.date(2000, 1, 1),
                note=None, user_id=scene.user_id,
            )
        card = _award(scene, scene.offer_b)
        rows = _awards(scene)
        assert rows[0].not_concluded_on < rows[0].awarded_at.date()
        assert [(e["award_id"], e["kind"]) for e in card["award_history"]] == [
            (rows[0].id, "awarded"), (rows[0].id, "not_concluded"), (rows[1].id, "awarded"),
        ]

    def test_history_is_ordered_by_award_id_when_awarded_at_and_storage_order_disagree(self, scene):
        """Ревью задачи 3: на входе соседнего теста порядок по `id` совпадает и с
        порядком по `awarded_at`, и с физическим порядком строк — ни снятие
        `ORDER BY`, ни сортировка по `awarded_at` его не красят. Здесь у первой
        отметки `awarded_at` позже второй, таблица и её индексы переписаны в
        порядке «вторая, первая», а первая отметка — на участнике Б, вторая — на А: порядок
        участников и оферт тоже против `id` (без `ORDER BY` соединение отдаёт
        строки в порядке одной из соединяемых таблиц)."""
        _award(scene, scene.offer_b)
        first = _only_award_id(scene)
        with scene.sf() as db:
            crud_awards.mark_not_concluded(
                db, scene.tender_id, first, not_concluded_on=dt.date(2026, 2, 1), note=None,
                user_id=scene.user_id,
            )
        _award(scene, scene.offer_a)
        second = _awards(scene)[1].id
        with scene.sf() as db:
            later = db.get(TenderAward, second).awarded_at + dt.timedelta(days=1)
            db.execute(sa.update(TenderAward).where(TenderAward.id == first).values(awarded_at=later))
            db.commit()
            # Правка `awarded_at` — HOT: индекс по `tender_id` по-прежнему
            # отдаёт строки в порядке вставки. CLUSTER по оферте переписывает
            # и таблицу, и индексы в порядке «вторая, первая» (оферта А раньше Б).
            db.execute(sa.text("CLUSTER tender_awards USING ix_tender_awards_offer_id"))
            db.commit()
            storage_order = list(db.execute(sa.text("SELECT id FROM tender_awards")).scalars())
            card = crud_tenders.get_tender_card(db, scene.tender_id)
        rows = _awards(scene)
        assert rows[0].awarded_at > rows[1].awarded_at  # предусловие: даты против id
        assert storage_order == [second, first]  # предусловие: хранение против id
        assert [(e["award_id"], e["kind"]) for e in card["award_history"]] == [
            (first, "awarded"), (first, "not_concluded"), (second, "awarded"),
        ]

    def test_only_the_awarded_event_of_the_active_award_is_active(self, scene):
        _award(scene, scene.offer_a)
        with scene.sf() as db:
            crud_awards.mark_not_concluded(
                db, scene.tender_id, _only_award_id(scene), not_concluded_on=dt.date(2026, 2, 1),
                note=None, user_id=scene.user_id,
            )
        card = _award(scene, scene.offer_b)
        flags = [(e["kind"], e["is_active"]) for e in card["award_history"]]
        assert flags == [("awarded", False), ("not_concluded", False), ("awarded", True)]

    def test_the_contract_of_the_award_is_in_the_card(self, scene):
        _award(scene, scene.offer_a)
        contract_id = _contract(scene, number="ГП-901", signed=dt.date(2026, 4, 5))
        with scene.sf() as db:
            card = crud_awards.link_contract(db, scene.tender_id, _only_award_id(scene), contract_id=contract_id)
        assert card["award"]["contract"] == {
            "id": contract_id, "contract_number": "ГП-901", "signed_date": "2026-04-05",
        }


class TestContractCandidates:
    def test_returns_only_unlinked_contracts_of_the_same_object_and_contractor_newest_first(self, scene):
        _award(scene, scene.offer_a)
        award_id = _only_award_id(scene)
        older = _contract(scene, signed=dt.date(2025, 1, 1), number="ГП-СТАРЫЙ")
        newer = _contract(scene, signed=dt.date(2026, 5, 1), number="ГП-НОВЫЙ")
        _contract(scene, contractor_id=scene.contractor_b, number="ГП-ДРУГОЙ-ПОДРЯДЧИК")
        other_object = scene.f.ObjectFactory.create()
        scene.db.commit()
        _contract(scene, object_id=other_object.id, number="ГП-ДРУГОЙ-ОБЪЕКТ")
        linked = _contract(scene, signed=dt.date(2026, 6, 1), number="ГП-СВЯЗАННЫЙ")
        with scene.sf() as db:
            crud_awards.link_contract(db, scene.tender_id, award_id, contract_id=linked)
            candidates = crud_awards.contract_candidates(db, scene.tender_id, award_id)
        assert [c["id"] for c in candidates] == [newer, older]
        assert set(candidates[0]) == CANDIDATE_KEYS

    def test_the_order_is_by_signed_date_and_not_by_id(self, scene):
        """Ревью задачи 3: в соседнем тесте старший договор заведён первым, и
        порядок по `id` убыванием совпадает с порядком по дате. Здесь ни `id`
        по возрастанию, ни по убыванию не дают порядка дат."""
        _award(scene, scene.offer_a)
        middle = _contract(scene, signed=dt.date(2025, 6, 1), number="ГП-СЕРЕДИНА")
        newest = _contract(scene, signed=dt.date(2026, 5, 1), number="ГП-НОВЫЙ")
        oldest = _contract(scene, signed=dt.date(2024, 1, 1), number="ГП-СТАРЫЙ")
        with scene.sf() as db:
            candidates = crud_awards.contract_candidates(db, scene.tender_id, _only_award_id(scene))
        assert [c["id"] for c in candidates] == [newest, middle, oldest]

    def test_an_award_without_contract_lists_the_unlinked_ones(self, scene):
        _award(scene, scene.offer_a)
        cid = _contract(scene, number="ГП-1")
        with scene.sf() as db:
            candidates = crud_awards.contract_candidates(db, scene.tender_id, _only_award_id(scene))
        assert [c["id"] for c in candidates] == [cid]
        assert candidates[0]["contract_number"] == "ГП-1"
        assert candidates[0]["signed_date"] == "2026-01-10"
        assert candidates[0]["contractor_title"] == "ООО А"
        assert candidates[0]["base_total_including_vat"] is None

    def test_the_base_total_counts_only_the_main_estimate(self, scene):
        _award(scene, scene.offer_a)
        with_estimate = _contract(scene, number="ГП-С", signed=dt.date(2026, 3, 1))
        amendment_only = _contract(scene, number="ГП-Д", signed=dt.date(2026, 2, 1))
        _contract_estimate(scene, with_estimate, amendment_no=None, total=500)
        _contract_estimate(scene, with_estimate, amendment_no=1, total=700)
        _contract_estimate(scene, amendment_only, amendment_no=1, total=900)
        with scene.sf() as db:
            candidates = crud_awards.contract_candidates(db, scene.tender_id, _only_award_id(scene))
        totals = {c["id"]: c["base_total_including_vat"] for c in candidates}
        assert totals[with_estimate] == "500"
        assert totals[amendment_only] is None

    def test_the_award_of_another_tender_is_not_found(self, scene):
        _award(scene, scene.offer_a)
        err = _refused(lambda: _call(scene, crud_awards.contract_candidates, scene.foreign_id, _only_award_id(scene)))
        assert err.status_code == 404


class TestLinkContract:
    def _link(self, s, award_id, contract_id):
        with s.sf() as db:
            return crud_awards.link_contract(db, s.tender_id, award_id, contract_id=contract_id)

    def test_link_sets_the_basis_and_leaves_the_estimate_alone(self, scene):
        _award(scene, scene.offer_a)
        award_id = _only_award_id(scene)
        contract_id = _contract(scene)
        estimate_id = _contract_estimate(scene, contract_id, amendment_no=None, total=500)
        card = self._link(scene, award_id, contract_id)
        assert _contract_award_id(scene, contract_id) == award_id
        assert card["award"]["contract"]["id"] == contract_id
        with scene.sf() as db:
            estimate = db.get(Estimate, estimate_id)
            assert estimate.source_award_id is None
            assert estimate.contract_id == contract_id

    def test_a_closed_award_refuses_the_link(self, scene):
        _award(scene, scene.offer_a)
        award_id = _only_award_id(scene)
        with scene.sf() as db:
            crud_awards.mark_not_concluded(
                db, scene.tender_id, award_id, not_concluded_on=dt.date(2026, 2, 1), note=None,
                user_id=scene.user_id,
            )
        contract_id = _contract(scene)
        err = _refused(lambda: self._link(scene, award_id, contract_id))
        assert (err.status_code, err.code) == (409, "award_not_active")
        assert _contract_award_id(scene, contract_id) is None

    def test_an_award_that_already_has_a_contract_refuses_a_second_one(self, scene):
        _award(scene, scene.offer_a)
        award_id = _only_award_id(scene)
        first = _contract(scene, number="ГП-1")
        second = _contract(scene, number="ГП-2")
        self._link(scene, award_id, first)
        err = _refused(lambda: self._link(scene, award_id, second))
        assert (err.status_code, err.code) == (409, "award_has_contract")
        # Номер — признак синхронной проверки: отказ ключом пришёл бы без него.
        assert "ГП-1" in err.detail
        assert _contract_award_id(scene, second) is None
        assert _contract_award_id(scene, first) == award_id

    def test_a_contract_with_a_basis_is_refused_as_already_linked(self, scene):
        _award(scene, scene.offer_a)
        first_award = _only_award_id(scene)
        contract_id = _contract(scene, number="ГП-1")
        self._link(scene, first_award, contract_id)
        # Договор привязан к отметке этого тендера: снять её нельзя, значит ссылка
        # на «уже привязан» проверяется на договоре другой отметки — того же
        # подрядчика во втором тендере ТОГО ЖЕ объекта (ревью задачи 3: прежде
        # тендер получал чужой объект, вход нарушал заодно и объект, и снятие
        # проверки краснело соседним `contract_object_mismatch`).
        other_tender_id, other_award = _second_tender_award(scene)
        err = _refused(lambda: _call(scene, crud_awards.link_contract, other_tender_id, other_award, contract_id=contract_id,
        ))
        assert (err.status_code, err.code) == (409, "contract_already_linked")
        assert scene.tender_number in err.detail
        assert _contract_award_id(scene, contract_id) == first_award

    def test_a_contract_of_another_object_is_refused(self, scene):
        _award(scene, scene.offer_a)
        other_object = scene.f.ObjectFactory.create()
        scene.db.commit()
        contract_id = _contract(scene, object_id=other_object.id, number="ГП-ОБЪЕКТ")
        err = _refused(lambda: self._link(scene, _only_award_id(scene), contract_id))
        assert (err.status_code, err.code) == (409, "contract_object_mismatch")
        # Номер — признак синхронной проверки: ключ `fk_contracts_tender_award`
        # отказал бы тем же кодом, но без номера.
        assert "ГП-ОБЪЕКТ" in err.detail
        assert _contract_award_id(scene, contract_id) is None

    def test_a_contract_of_another_contractor_is_refused(self, scene):
        _award(scene, scene.offer_a)
        contract_id = _contract(scene, contractor_id=scene.contractor_b, number="ГП-ПОДРЯДЧИК")
        err = _refused(lambda: self._link(scene, _only_award_id(scene), contract_id))
        assert (err.status_code, err.code) == (409, "contract_contractor_mismatch")
        assert "ГП-ПОДРЯДЧИК" in err.detail
        assert _contract_award_id(scene, contract_id) is None

    def test_an_unknown_contract_is_not_found(self, scene):
        _award(scene, scene.offer_a)
        err = _refused(lambda: self._link(scene, _only_award_id(scene), 987654))
        assert err.status_code == 404

    def test_the_award_of_another_tender_is_not_found(self, scene):
        _award(scene, scene.offer_a)
        contract_id = _contract(scene)
        err = _refused(lambda: _call(scene, crud_awards.link_contract, scene.foreign_id, _only_award_id(scene), contract_id=contract_id,
        ))
        assert err.status_code == 404
        assert _contract_award_id(scene, contract_id) is None


class TestCreateRound:
    def _create(self, s, stage_no):
        with s.sf() as db:
            return crud_tenders.create_round(db, s.tender_id, stage_no=stage_no, label=None, held_on=None)

    def test_an_active_award_blocks_a_new_round(self, scene):
        _award(scene, scene.offer_a)
        before = _rounds_count(scene)
        err = _refused(lambda: self._create(scene, 3))
        assert (err.status_code, err.code) == (409, "round_blocked_by_award")
        assert _rounds_count(scene) == before

    def test_a_closed_award_does_not_block_a_new_round(self, scene):
        _award(scene, scene.offer_a)
        with scene.sf() as db:
            crud_awards.mark_not_concluded(
                db, scene.tender_id, _only_award_id(scene), not_concluded_on=dt.date(2026, 2, 1),
                note=None, user_id=scene.user_id,
            )
        before = _rounds_count(scene)
        self._create(scene, 3)
        assert _rounds_count(scene) == before + 1

    def test_without_awards_a_round_is_created(self, scene):
        before = _rounds_count(scene)
        self._create(scene, 3)
        assert _rounds_count(scene) == before + 1

    def test_an_award_of_another_tender_does_not_block(self, scene):
        with scene.sf() as db:
            crud_awards.award_winner(db, scene.foreign_id, offer_id=scene.foreign_offer, user_id=scene.user_id)
        before = _rounds_count(scene)
        self._create(scene, 3)
        assert _rounds_count(scene) == before + 1

    def test_an_unknown_tender_is_not_found(self, scene):
        with scene.sf() as db:
            err = _refused(lambda: crud_tenders.create_round(db, 987654, stage_no=1, label=None, held_on=None))
        assert err.status_code == 404


class TestKeysBackTheChecks:
    """Нарушение ключа мимо проверки команды (проверка выключена подменой) даёт
    тот же код и статус, что синхронный отказ (спека §2.7, решение 14), и текст
    §2.7 без подстановок (спека §5: «тот же код и текст») — эталоны ниже
    литералами, а не вызовом текстовых функций модуля."""

    KEY_TEXTS = {
        "award_exists": (
            "В тендере уже отмечен победитель. Чтобы отметить другого, снимите отметку или "
            "отметьте, что договор не заключён."
        ),
        "award_has_contract": (
            "Нельзя: по этой отметке заключён договор. Сначала удалите договор или отвяжите его от тендера."
        ),
        "award_no_kp": "У участника нет КП в этом этапе — отметить его нельзя.",
        "contract_object_mismatch": "Договор заключён на другом объекте — привязать его к этому тендеру нельзя.",
        "contract_contractor_mismatch": (
            "Договор заключён с другим подрядчиком — привязать его к этому тендеру нельзя."
        ),
        "award_kp_unidentified": (
            "КП участника не опознаётся в разборе этапа: в нём нет ИНН участника либо их несколько. "
            "Отметить нельзя — загрузите файл этапа заново."
        ),
    }

    def test_unique_active_award_key_gives_the_code_of_the_sync_refusal(self, scene, monkeypatch):
        _award(scene, scene.offer_a)
        sync = _refused(lambda: _award(scene, scene.offer_b))
        monkeypatch.setattr(crud_awards, "_refuse_if_award_exists", lambda db, tender_id: None)
        via_key = _refused(lambda: _award(scene, scene.offer_b))
        assert sync.code == via_key.code == "award_exists"
        assert sync.status_code == via_key.status_code == 409
        assert via_key.detail == self.KEY_TEXTS["award_exists"]
        assert [r.offer_id for r in _awards(scene)] == [scene.offer_a]

    def test_contract_key_on_removal_gives_the_code_of_the_sync_refusal(self, scene, monkeypatch):
        _award(scene, scene.offer_a)
        award_id = _only_award_id(scene)
        contract_id = _contract(scene)
        with scene.sf() as db:
            crud_awards.link_contract(db, scene.tender_id, award_id, contract_id=contract_id)
        sync = _refused(lambda: _call(scene, crud_awards.remove_award, scene.tender_id, award_id))
        monkeypatch.setattr(crud_awards, "_refuse_if_has_contract", lambda db, award: None)
        via_key = _refused(lambda: _call(scene, crud_awards.remove_award, scene.tender_id, award_id))
        assert sync.code == via_key.code == "award_has_contract"
        assert via_key.detail == self.KEY_TEXTS["award_has_contract"]
        assert len(_awards(scene)) == 1
        assert _contract_award_id(scene, contract_id) == award_id

    @pytest.mark.parametrize(
        ("kind", "code"),
        [("object", "contract_object_mismatch"), ("contractor", "contract_contractor_mismatch")],
    )
    def test_contract_key_on_link_gives_the_code_of_the_sync_refusal(self, scene, monkeypatch, kind, code):
        _award(scene, scene.offer_a)
        award_id = _only_award_id(scene)
        if kind == "object":
            other = scene.f.ObjectFactory.create()
            scene.db.commit()
            contract_id = _contract(scene, object_id=other.id)
        else:
            contract_id = _contract(scene, contractor_id=scene.contractor_b)
        sync = _refused(lambda: _call(scene, crud_awards.link_contract, scene.tender_id, award_id, contract_id=contract_id,
        ))
        monkeypatch.setattr(crud_awards, "_refuse_if_parties_differ", lambda contract, award: None)
        via_key = _refused(lambda: _call(scene, crud_awards.link_contract, scene.tender_id, award_id, contract_id=contract_id,
        ))
        assert sync.code == via_key.code == code
        assert sync.status_code == via_key.status_code == 409
        assert via_key.detail == self.KEY_TEXTS[code]
        assert _contract_award_id(scene, contract_id) is None

    def test_unique_contract_key_on_link_gives_the_code_of_the_sync_refusal(self, scene, monkeypatch):
        _award(scene, scene.offer_a)
        award_id = _only_award_id(scene)
        first = _contract(scene, number="ГП-1")
        second = _contract(scene, number="ГП-2")
        with scene.sf() as db:
            crud_awards.link_contract(db, scene.tender_id, award_id, contract_id=first)
        sync = _refused(lambda: _call(scene, crud_awards.link_contract, scene.tender_id, award_id, contract_id=second,
        ))
        monkeypatch.setattr(crud_awards, "_refuse_if_has_contract", lambda db, award: None)
        via_key = _refused(lambda: _call(scene, crud_awards.link_contract, scene.tender_id, award_id, contract_id=second,
        ))
        assert sync.code == via_key.code == "award_has_contract"
        assert via_key.detail == self.KEY_TEXTS["award_has_contract"]
        assert _contract_award_id(scene, second) is None
        assert _contract_award_id(scene, first) == award_id

    def test_canonical_inn_key_gives_the_code_of_the_sync_refusal(self, scene, monkeypatch):
        """Ревью задачи 3: ключ-страховка `award_kp_unidentified` по §2.7 —
        `ck_tender_awards_kp_inn_canonical`. Мимо проверки — ИНН не в
        канонической форме, подставленный вместо снятого с разбора."""
        monkeypatch.setattr(crud_awards, "kp_inn_of", lambda raw: "77-01")
        via_key = _refused(lambda: _award(scene, scene.offer_a))
        assert (via_key.status_code, via_key.code) == (409, "award_kp_unidentified")
        assert via_key.detail == self.KEY_TEXTS["award_kp_unidentified"]
        assert _awards(scene) == []

    def test_kp_estimate_key_gives_the_code_of_the_sync_refusal(self, scene, monkeypatch):
        """Ревью задачи 3: ключ-страховка `award_no_kp` по §2.7 —
        `fk_tender_awards_kp_estimate`. Мимо проверки — КП исчезла другим
        соединением между её чтением командой и вставкой отметки."""
        real = crud_awards.kp_inn_of

        def vanish(raw):
            with scene.sf() as other:
                other.execute(sa.delete(Estimate).where(Estimate.offer_id == scene.offer_a))
                other.commit()
            return real(raw)

        monkeypatch.setattr(crud_awards, "kp_inn_of", vanish)
        via_key = _refused(lambda: _award(scene, scene.offer_a))
        assert (via_key.status_code, via_key.code) == (409, "award_no_kp")
        assert via_key.detail == self.KEY_TEXTS["award_no_kp"]
        assert _awards(scene) == []


# ---------------------------------------------------------------------------
#  Гонка «новый этап против отметки»
# ---------------------------------------------------------------------------

_JOIN_TIMEOUT = 20.0


def _wait_for_a_blocked_backend(session_factory, *, timeout: float = 6.0) -> bool:
    deadline = time.monotonic() + timeout
    with session_factory() as probe:
        while time.monotonic() < deadline:
            blocked = probe.execute(sa.text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )).scalar_one()
            probe.rollback()
            if blocked:
                return True
            time.sleep(0.05)
    return False


@pytest.mark.parametrize("first", ["award", "round"])
def test_new_round_and_award_race_exactly_one_passes(scene, first):
    """Две команды на одном тендере (два соединения). Первая берёт замок тендера
    и держит его (пауза ПОСЛЕ чтения `SELECT … FOR UPDATE`, когда результат уже
    у клиента); вторая стартует, обязана встать на замок, и только потом первая
    завершается. Проходит ровно одна: отметка раньше — этап отказывает
    `round_blocked_by_award`; этап раньше — отметка отказывает
    `award_not_final_stage`. Снятие `FOR UPDATE` тендера в `create_round` красит
    тест: этап вставляется поверх отметки, ещё не закоммиченной первой командой."""
    held, release = threading.Event(), threading.Event()
    outcome: dict[str, str] = {}

    def pause_after_tender_lock(session):
        @sa.event.listens_for(session.connection(), "after_cursor_execute")
        def _pause(conn, cursor, statement, parameters, context, executemany):
            if "FOR UPDATE" in statement.upper() and "FROM tenders" in statement and not held.is_set():
                held.set()
                release.wait(timeout=_JOIN_TIMEOUT)

    def run(name, call, *, pauses):
        session = scene.sf()
        try:
            if pauses:
                pause_after_tender_lock(session)
            call(session)
            outcome[name] = "ok"
        except DomainError as exc:
            outcome[name] = exc.code or str(exc.status_code)
        except Exception as exc:  # noqa: BLE001 - любая иная ошибка проваливает тест ниже
            outcome[name] = f"{type(exc).__name__}: {exc}"
        finally:
            session.close()

    def award(session):
        crud_awards.award_winner(session, scene.tender_id, offer_id=scene.offer_a, user_id=scene.user_id)

    def new_round(session):
        crud_tenders.create_round(session, scene.tender_id, stage_no=3, label=None, held_on=None)

    commands = {"award": award, "round": new_round}
    second = "round" if first == "award" else "award"
    t_first = threading.Thread(target=run, args=(first, commands[first]), kwargs={"pauses": True}, daemon=True)
    t_second = threading.Thread(target=run, args=(second, commands[second]), kwargs={"pauses": False}, daemon=True)
    t_first.start()
    try:
        assert held.wait(timeout=10), "первая команда не дошла до замка тендера"
        t_second.start()
        reached_lock = _wait_for_a_blocked_backend(scene.sf)
        assert reached_lock, "вторая команда не встала на замок тендера: сериализации нет"
        assert t_second.is_alive(), "вторая команда завершилась, пока первая держит замок"
    finally:
        release.set()
        t_first.join(timeout=_JOIN_TIMEOUT)
        if t_second.ident is not None:
            t_second.join(timeout=_JOIN_TIMEOUT)
    assert not t_first.is_alive()
    assert not t_second.is_alive()

    refusal = "round_blocked_by_award" if first == "award" else "award_not_final_stage"
    assert outcome[first] == "ok"
    assert outcome[second] == refusal
    rows = _awards(scene)
    assert len(rows) == (1 if first == "award" else 0)
    assert _rounds_count(scene) == (2 if first == "award" else 3)


# ---------------------------------------------------------------------------
#  Гонки, добавленные ревью задачи 3: замки, которые держат правило без ключа
# ---------------------------------------------------------------------------

def _outcome_into(scene, outcome, name, call, *, pause=None, held=None, release=None):
    session = scene.sf()
    try:
        if pause is not None:
            @sa.event.listens_for(session.connection(), "after_cursor_execute")
            def _pause(conn, cursor, statement, parameters, context, executemany):
                if pause(statement) and not held.is_set():
                    held.set()
                    release.wait(timeout=_JOIN_TIMEOUT)
        call(session)
        outcome[name] = "ok"
    except DomainError as exc:
        outcome[name] = exc.code or str(exc.status_code)
    except Exception as exc:  # noqa: BLE001 - любая иная ошибка проваливает тест ниже
        outcome[name] = f"{type(exc).__name__}: {exc}"
    finally:
        session.close()


def _blocked_or_done(session_factory, thread, *, timeout: float = 6.0) -> bool:
    """True — поток встал на замок; False — поток завершился (или срок вышел),
    не вставая. Без защиты второй поток не ждёт, и тест краснеет исходом
    команд, а не этим предусловием."""
    deadline = time.monotonic() + timeout
    with session_factory() as probe:
        while time.monotonic() < deadline:
            if not thread.is_alive():
                return False
            blocked = probe.execute(sa.text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )).scalar_one()
            probe.rollback()
            if blocked:
                return True
            time.sleep(0.05)
    return False


def _race(scene, first, second, *, pause):
    """Первая команда встаёт на паузу ПОСЛЕ чтения, которое `pause` узнаёт по
    тексту запроса (замки к этому моменту взяты); вторая стартует, и первая
    отпускается, когда вторая встала на замок или завершилась."""
    held, release = threading.Event(), threading.Event()
    outcome: dict[str, str] = {}
    t_first = threading.Thread(
        target=_outcome_into, args=(scene, outcome, "first", first),
        kwargs={"pause": pause, "held": held, "release": release}, daemon=True,
    )
    t_second = threading.Thread(target=_outcome_into, args=(scene, outcome, "second", second), daemon=True)
    blocked = False
    t_first.start()
    try:
        assert held.wait(timeout=10), "первая команда не дошла до паузы"
        t_second.start()
        blocked = _blocked_or_done(scene.sf, t_second)
    finally:
        release.set()
        t_first.join(timeout=_JOIN_TIMEOUT)
        if t_second.ident is not None:
            t_second.join(timeout=_JOIN_TIMEOUT)
    assert not t_first.is_alive()
    assert not t_second.is_alive()
    return outcome, blocked


@pytest.mark.parametrize("first", ["not_concluded", "link"])
def test_not_concluded_and_link_race_exactly_one_passes(scene, first):
    """Спека §2.3, правило 4: «договор не заключён» при договоре ключом не
    выражается — его держат замки команд (тендер и отметка `FOR UPDATE` у
    обеих). Первая команда стоит ПОСЛЕ своих проверок; без замков вторая
    проходит мимо, и отметка оказывается «не заключён» с договором."""
    _award(scene, scene.offer_a)
    award_id = _only_award_id(scene)
    contract_id = _contract(scene, number="ГП-ГОНКА")

    def close(db):
        crud_awards.mark_not_concluded(
            db, scene.tender_id, award_id, not_concluded_on=dt.date(2026, 2, 1), note=None,
            user_id=scene.user_id,
        )

    def link(db):
        crud_awards.link_contract(db, scene.tender_id, award_id, contract_id=contract_id)

    calls = {"not_concluded": close, "link": link}
    after_checks = {
        "not_concluded": lambda s: "WHERE contracts.tender_award_id =" in s,
        "link": lambda s: "WHERE contracts.id =" in s,
    }
    second = "link" if first == "not_concluded" else "not_concluded"
    outcome, blocked = _race(scene, calls[first], calls[second], pause=after_checks[first])

    refusal = "award_not_active" if first == "not_concluded" else "award_has_contract"
    assert (outcome["first"], outcome["second"]) == ("ok", refusal)
    award = _awards(scene)[0]
    linked = _contract_award_id(scene, contract_id) == award_id
    assert (award.not_concluded_on is None) == linked
    assert blocked, "вторая команда не встала на замок"


def test_one_contract_linked_to_two_tenders_at_once_only_one_passes(scene):
    """Спека §2.7: у `contract_already_linked` ключа нет — `uq_contracts_tender_award`
    уникален по отметке, а не по договору; гонку держит `FOR UPDATE` договора.
    Две привязки одного договора к отметкам двух тендеров: без замка вторая
    перезаписывает основание первой."""
    _award(scene, scene.offer_a)
    first_award = _only_award_id(scene)
    other_tender_id, other_award = _second_tender_award(scene)
    contract_id = _contract(scene, number="ГП-ДВА-ТЕНДЕРА")

    outcome, blocked = _race(
        scene,
        lambda db: crud_awards.link_contract(db, scene.tender_id, first_award, contract_id=contract_id),
        lambda db: crud_awards.link_contract(db, other_tender_id, other_award, contract_id=contract_id),
        pause=lambda s: "WHERE contracts.id =" in s,
    )
    assert (outcome["first"], outcome["second"]) == ("ok", "contract_already_linked")
    assert _contract_award_id(scene, contract_id) == first_award
    assert blocked, "вторая привязка не встала на замок договора"


def test_an_uncommitted_round_job_holds_the_award_until_it_commits(scene):
    """Загрузка этапа вставила задание (FOR KEY SHARE раунда по ключу) и ещё не
    закоммитила; загрузка тендер не блокирует. Отметка обязана дождаться её —
    это делает `FOR UPDATE` раундов — и отказать `active_import`; без замка
    раундов отметка не видит незакоммиченное задание и проходит."""
    outcome: dict[str, str] = {}
    holder = scene.sf()
    thread = threading.Thread(
        target=_outcome_into,
        args=(scene, outcome, "award",
              lambda db: crud_awards.award_winner(db, scene.tender_id, offer_id=scene.offer_a, user_id=scene.user_id)),
        daemon=True,
    )
    try:
        holder.add(ImportJob(round_id=scene.r2_id, filename="race.xlsx", file_key="race-award",
                             file_sha256="1" * 64, status=ImportJobStatus.pending.value))
        holder.flush()
        thread.start()
        blocked = _blocked_or_done(scene.sf, thread)
        holder.commit()
    finally:
        holder.close()
        if thread.ident is not None:
            thread.join(timeout=_JOIN_TIMEOUT)
    assert not thread.is_alive()
    assert outcome["award"] == "active_import"
    assert _awards(scene) == []
    assert blocked, "отметка не встала на замок раунда"
