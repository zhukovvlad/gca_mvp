"""CRUD тендерного контура (спека §2.11–§2.13): итог единогласием, карточка с
решёткой, три команды удаления на ПОЛНОЙ решётке, третий потребитель
подрядчика.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud import tenders as crud_tenders
from crud.common import DomainError
from crud.estimate_totals import estimate_total_including_vat
from models import Estimate, ImportJob, ImportJobStatus, Offer, OfferPackage, ProposalSummaryLine
from services.category_resolution import CategoryResolver
from services.round_import import import_round
from services.unit_resolution import UnitResolver
from tests.payloads import baseline_proposal_block, position, proposal, round_payload

pytestmark = pytest.mark.integration


def _summary_line(db, proposal_id, total):
    db.add(ProposalSummaryLine(proposal_id=proposal_id, summary_key="total_cost_including_vat",
                               job_title="ИТОГО", total_cost=total))
    db.flush()


class TestEstimateTotalIncludingVat:
    def _estimate_with_lots(self, db, factories, totals):
        estimate = factories.EstimateFactory.create()
        for total in totals:
            lot = factories.LotFactory.create(estimate=estimate)
            prop = factories.ProposalFactory.create(lot=lot)
            db.flush()
            if total is not ...:
                _summary_line(db, prop.id, total)
        return estimate

    def test_unanimous_value_is_returned(self, db_session, factories):
        e = self._estimate_with_lots(db_session, factories, [Decimal("1200.00"), Decimal("1200.00")])
        assert estimate_total_including_vat(db_session, e.id) == Decimal("1200.00")

    def test_disagreement_is_null(self, db_session, factories):
        e = self._estimate_with_lots(db_session, factories, [Decimal("1200.00"), Decimal("1300.00")])
        assert estimate_total_including_vat(db_session, e.id) is None

    def test_missing_line_on_one_proposal_is_null(self, db_session, factories):
        e = self._estimate_with_lots(db_session, factories, [Decimal("1200.00"), ...])
        assert estimate_total_including_vat(db_session, e.id) is None

    def test_nan_on_one_proposal_is_null(self, db_session, factories):
        e = self._estimate_with_lots(db_session, factories, [Decimal("1200.00"), Decimal("NaN")])
        assert estimate_total_including_vat(db_session, e.id) is None

    def test_no_proposals_is_null(self, db_session, factories):
        e = factories.EstimateFactory.create()
        db_session.flush()
        assert estimate_total_including_vat(db_session, e.id) is None


def P_A():
    return proposal([position(job_title="Работа", unit="м2", unit_cost_total="10", total_cost_total="10")],
                    title="ООО А", inn="7700000001")


def P_B():
    return proposal([position(job_title="Работа", unit="м2", unit_cost_total="12", total_cost_total="12")],
                    title="ООО Б", inn="7700000002")


def _grid(db_session, factories, *, round_participants: dict[int, list], baseline_in_round_1: bool):
    """Тендер с двумя раундами. Jobs проводятся так, как их оставил бы пайплайн:
    done, estimates_created, parsed_data и parser_version заполнены — иначе
    правило текущего job (§2.12) и проверка «аудит сохранён» тестам не видны."""
    tender = factories.TenderFactory.create()
    r1 = factories.TenderRoundFactory.create(tender=tender, stage_no=1)
    r2 = factories.TenderRoundFactory.create(tender=tender, stage_no=2)
    db_session.flush()
    base = baseline_proposal_block([position(job_title="База", unit="м2", unit_cost_total="9", total_cost_total="9")])
    jobs = {}
    for rnd, key in ((r1, "k-r1"), (r2, "k-r2")):
        participants = round_participants[rnd.stage_no]
        data = round_payload(participants, baseline=base if (rnd.stage_no == 1 and baseline_in_round_1) else None)
        job = factories.ImportJobFactory.create(contract=None, round_id=rnd.id, status=ImportJobStatus.pending.value,
                                                file_key=key, file_sha256=f"{rnd.stage_no:064x}")
        db_session.flush()
        outcome = import_round(db_session, tender_round=rnd, data=data, parser_version="4.0.0", import_job_id=job.id,
                               replace=False, unit_resolver=UnitResolver(db_session),
                               category_resolver=CategoryResolver.from_db(db_session))
        job.status = ImportJobStatus.done.value
        job.estimates_created = outcome.estimates_created
        job.parsed_data = data
        job.parser_version = "4.0.0"
        jobs[rnd.stage_no] = job
    db_session.flush()

    class Grid:
        pass

    g = Grid()
    g.tender, g.r1, g.r2, g.j1, g.j2 = tender, r1, r2, jobs[1], jobs[2]

    def package_of(inn):
        return db_session.execute(
            sa.select(OfferPackage).join(OfferPackage.contractor)
            .where(OfferPackage.tender_id == tender.id, OfferPackage.contractor.has(inn=inn))
        ).scalar_one()

    g.package_a = package_of("7700000001")
    g.package_b = package_of("7700000002")
    return g


@pytest.fixture
def rectangular_grid(db_session, factories):
    """Участник Б появляется ТОЛЬКО во втором раунде: у решётки есть ячейка без
    Offer. Корпус для формы карточки (§2.13), не для удалений."""
    return _grid(db_session, factories, round_participants={1: [P_A()], 2: [P_A(), P_B()]}, baseline_in_round_1=True)


@pytest.fixture
def full_grid(db_session, factories):
    """2 раунда × 2 участника, смета в КАЖДОЙ ячейке плюс baseline в первом
    раунде — корпус, которого спека требует для удалений (§2.11, §6)."""
    return _grid(db_session, factories, round_participants={1: [P_A(), P_B()], 2: [P_A(), P_B()]}, baseline_in_round_1=True)


class TestTenderCard:
    def test_cells_are_the_full_rectangle_with_three_states(self, db_session, rectangular_grid):
        g = rectangular_grid
        card = crud_tenders.get_tender_card(db_session, g.tender.id)
        assert [r["stage_no"] for r in card["rounds"]] == [1, 2]
        assert {p["inn"] for p in card["participants"]} == {"7700000001", "7700000002"}
        cells = {(c["round_id"], c["package_id"]): c for c in card["cells"]}
        assert len(cells) == 4
        # участник Б в раунде 1 не участвовал: ни Offer, ни сметы
        none_cell = cells[(g.r1.id, g.package_b.id)]
        assert none_cell["offer_id"] is None and none_cell["estimate_id"] is None
        # участник А в раунде 1: и Offer, и смета, и «Итого с НДС»
        full_cell = cells[(g.r1.id, g.package_a.id)]
        assert full_cell["offer_id"] is not None and full_cell["estimate_id"] is not None
        assert full_cell["total_including_vat"] == "1200.00"

    def test_round_carries_baseline_and_current_job(self, db_session, rectangular_grid):
        g = rectangular_grid
        card = crud_tenders.get_tender_card(db_session, g.tender.id)
        r1 = next(r for r in card["rounds"] if r["stage_no"] == 1)
        assert r1["baseline_estimate_id"] is not None
        assert r1["baseline_total_including_vat"] == "1200.00"
        assert r1["current_job_id"] == g.j1.id
        r2 = next(r for r in card["rounds"] if r["stage_no"] == 2)
        assert r2["baseline_estimate_id"] is None


class TestCurrentRoundJob:
    def test_done_job_with_full_set_is_current(self, db_session, full_grid):
        assert crud_tenders.current_round_job(db_session, full_grid.r2.id).id == full_grid.j2.id

    def test_job_is_not_current_after_a_participant_is_removed(self, db_session, full_grid):
        crud_tenders.delete_participant(
            db_session, full_grid.tender.id, full_grid.package_b.id,
            confirmation_token=crud_tenders.participant_deletion_preview(
                db_session, full_grid.tender.id, full_grid.package_b.id)["confirmation_token"],
        )
        assert crud_tenders.current_round_job(db_session, full_grid.r2.id) is None

    def test_non_done_job_is_never_current(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        factories.ImportJobFactory.create(contract=None, round_id=rnd.id, status=ImportJobStatus.error.value,
                                          estimates_created=None, file_key="k-e")
        db_session.flush()
        assert crud_tenders.current_round_job(db_session, rnd.id) is None


class TestDeleteParticipant:
    def test_without_token_returns_preview_and_deletes_nothing(self, db_session, full_grid):
        with pytest.raises(DomainError) as err:
            crud_tenders.delete_participant(db_session, full_grid.tender.id, full_grid.package_b.id, confirmation_token=None)
        assert err.value.code == "confirmation_required"
        assert err.value.context["rounds_count"] == 2
        assert err.value.context["estimates_count"] == 2
        assert err.value.context["confirmation_token"]
        assert db_session.get(OfferPackage, full_grid.package_b.id) is not None

    def test_stale_token_returns_fresh_preview_and_deletes_nothing(self, db_session, full_grid):
        with pytest.raises(DomainError) as err:
            crud_tenders.delete_participant(db_session, full_grid.tender.id, full_grid.package_b.id,
                                            confirmation_token="stale")
        assert err.value.code == "confirmation_required"
        assert db_session.get(OfferPackage, full_grid.package_b.id) is not None

    def test_valid_token_deletes_offers_estimates_and_package_but_not_jobs(self, db_session, full_grid):
        preview = crud_tenders.participant_deletion_preview(db_session, full_grid.tender.id, full_grid.package_b.id)
        before_jobs = db_session.execute(sa.select(sa.func.count()).select_from(ImportJob)).scalar_one()

        crud_tenders.delete_participant(db_session, full_grid.tender.id, full_grid.package_b.id,
                                        confirmation_token=preview["confirmation_token"])

        assert db_session.get(OfferPackage, full_grid.package_b.id) is None
        assert db_session.execute(sa.select(sa.func.count()).select_from(Offer).where(Offer.package_id == full_grid.package_b.id)).scalar_one() == 0
        # полная решётка: 4 offer-сметы + baseline = 5; ушли две сметы Б, остались
        # две сметы А и baseline
        assert db_session.execute(sa.select(sa.func.count()).select_from(Estimate)).scalar_one() == 3
        assert db_session.execute(sa.select(sa.func.count()).select_from(ImportJob)).scalar_one() == before_jobs
        assert db_session.get(ImportJob, full_grid.j2.id).parsed_data is not None

    def test_active_import_refuses_with_its_own_code(self, db_session, full_grid, factories):
        factories.ImportJobFactory.create(contract=None, round_id=full_grid.r2.id,
                                          status=ImportJobStatus.parsing.value, file_key="k-active")
        db_session.flush()
        with pytest.raises(DomainError) as err:
            crud_tenders.delete_participant(db_session, full_grid.tender.id, full_grid.package_b.id, confirmation_token="x")
        assert err.value.code == "active_import"


class TestDeleteRoundAndTender:
    def test_delete_round_returns_all_history_files(self, db_session, full_grid, factories):
        factories.ImportJobFactory.create(contract=None, round_id=full_grid.r1.id,
                                          status=ImportJobStatus.error.value, file_key="k-r1-old")
        db_session.flush()
        keys = crud_tenders.delete_round(db_session, full_grid.tender.id, full_grid.r1.id)
        assert sorted(keys) == ["k-r1", "k-r1-old"]
        assert db_session.execute(sa.select(sa.func.count()).select_from(Estimate).where(
            sa.or_(Estimate.round_id == full_grid.r1.id))).scalar_one() == 0
        # раунд 2 не тронут
        assert crud_tenders.current_round_job(db_session, full_grid.r2.id) is not None

    def test_delete_round_of_another_tender_is_404(self, db_session, full_grid, factories):
        other = factories.TenderFactory.create()
        db_session.flush()
        with pytest.raises(DomainError) as err:
            crud_tenders.delete_round(db_session, other.id, full_grid.r1.id)
        assert err.value.status_code == 404

    def test_delete_tender_on_full_grid_removes_everything(self, db_session, full_grid):
        keys = crud_tenders.delete_tender(db_session, full_grid.tender.id)
        assert sorted(keys) == ["k-r1", "k-r2"]
        for model in (Estimate, Offer, OfferPackage, ImportJob):
            assert db_session.execute(sa.select(sa.func.count()).select_from(model)).scalar_one() == 0

    def test_delete_tender_refuses_during_active_import(self, db_session, full_grid, factories):
        factories.ImportJobFactory.create(contract=None, round_id=full_grid.r1.id,
                                          status=ImportJobStatus.importing.value, file_key="k-act")
        db_session.flush()
        with pytest.raises(DomainError) as err:
            crud_tenders.delete_tender(db_session, full_grid.tender.id)
        assert err.value.code == "active_import"
