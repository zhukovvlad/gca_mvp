"""Схема тендерного контура (спека §2.1): каждый CHECK и частичный индекс —
пробоем, по одному нарушенному ограничению на вход.

Составные FK проверяются в режиме MATCH SIMPLE, и `NOT NULL` на их колонках —
условие, при котором проверка вообще выполняется; тест на `round_id = NULL` —
прямой пробой этого свойства.
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from models import Estimate, ImportJob, ImportJobStatus, Offer, Proposal
from tests.integration.test_schema_constraints import rejected

pytestmark = pytest.mark.integration


class TestTenderTables:
    def test_tender_number_unique(self, db_session, factories):
        factories.TenderFactory.create(tender_number="T-1")
        with rejected(db_session, contains="uq_tenders_tender_number"):
            factories.TenderFactory.create(tender_number="T-1")

    @pytest.mark.parametrize("n", [0, 1, 2])
    def test_factory_minted_tender_number_never_collides_with_a_hand_written_one(
        self, db_session, factories, n
    ):
        """`TenderFactory.tender_number` — `factory.Sequence`, а его счётчик
        глобален на весь процесс pytest (общий на все файлы и, под xdist, на
        воркер): где именно он окажется к моменту этого теста, зависит от
        состава прогона, а не от этого теста.

        Раньше фабрика минтила голое `f"Т-{n:04d}"` — то же пространство
        значений, что и рукописные литералы вроде "Т-0001"
        (`tests/integration/test_tenders_api.py::tender`). Когда счётчик
        доходил до совпадающего `n`, INSERT падал `duplicate key value
        violates unique constraint "uq_tenders_tender_number"` — плавающий
        по составу тестов и раскладке xdist (симптом закрыт этим тестом,
        а не конкретное число: см. докстринг `TenderFactory` в
        `tests/factories.py`).

        Кладём в базу РУКОПИСНЫЙ литерал СТАРОЙ формы прямо на значении
        счётчика, которое сейчас заставим отдать фабрике — если бы фабрика
        всё ещё минтила в то же пространство, тест упал бы тут же.
        """
        literal = f"Т-{n:04d}"
        factories.TenderFactory.create(tender_number=literal)
        db_session.flush()

        factories.TenderFactory.reset_sequence(n, force=True)
        minted = factories.TenderFactory.create()
        db_session.flush()

        assert minted.tender_number != literal

    @pytest.mark.parametrize("field", ["title", "tender_number"])
    def test_blank_text_rejected(self, db_session, factories, field):
        with rejected(db_session, contains=f"ck_tenders_{'title' if field == 'title' else 'number'}_not_blank"):
            factories.TenderFactory.create(**{field: "   "})

    def test_stage_no_positive(self, db_session, factories):
        with rejected(db_session, contains="ck_tender_rounds_stage_no"):
            factories.TenderRoundFactory.create(stage_no=0)

    def test_stage_no_unique_within_tender(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create(stage_no=1)
        with rejected(db_session, contains="uq_tender_rounds_tender_stage"):
            factories.TenderRoundFactory.create(tender=rnd.tender, stage_no=1)

    def test_one_package_per_contractor_per_tender(self, db_session, factories):
        pkg = factories.OfferPackageFactory.create()
        with rejected(db_session, contains="uq_offer_packages_tender_contractor"):
            factories.OfferPackageFactory.create(tender=pkg.tender, contractor=pkg.contractor)


class TestOfferGridIntegrity:
    def test_offer_unique_per_round_and_package(self, db_session, factories):
        offer = factories.OfferFactory.create()
        with rejected(db_session, contains="uq_offers_round_package"):
            factories.OfferFactory.create(round=offer.round, package=offer.package)

    def test_round_and_package_must_belong_to_the_same_tender(self, db_session, factories):
        """Скрещивание тендеров: раунд тендера A с участником тендера B."""
        round_a = factories.TenderRoundFactory.create()
        package_b = factories.OfferPackageFactory.create()
        assert round_a.tender_id != package_b.tender_id
        with rejected(db_session):
            db_session.add(Offer(tender_id=round_a.tender_id, round_id=round_a.id, package_id=package_b.id))
            db_session.flush()

    def test_offer_without_round_is_impossible(self, db_session, factories):
        """MATCH SIMPLE: при NULL в round_id составной FK НЕ проверяется — только
        NOT NULL делает ячейку без раунда непредставимой."""
        package = factories.OfferPackageFactory.create()
        with rejected(db_session, contains="round_id"):
            db_session.add(Offer(tender_id=package.tender_id, round_id=None, package_id=package.id))
            db_session.flush()

    def test_offer_without_package_is_impossible(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        with rejected(db_session, contains="package_id"):
            db_session.add(Offer(tender_id=rnd.tender_id, round_id=rnd.id, package_id=None))
            db_session.flush()


class TestEstimateOwners:
    def test_exactly_one_owner_required(self, db_session, factories):
        offer = factories.OfferFactory.create()
        contract = factories.ContractFactory.create()
        with rejected(db_session, contains="ck_estimates_owner"):
            db_session.add(Estimate(contract_id=contract.id, offer_id=offer.id))
            db_session.flush()
        with rejected(db_session, contains="ck_estimates_owner"):
            db_session.add(Estimate())
            db_session.flush()

    def test_offer_estimate_accepted(self, db_session, factories):
        offer = factories.OfferFactory.create()
        db_session.add(Estimate(offer_id=offer.id))
        db_session.flush()

    def test_baseline_estimate_accepted_once_per_round(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.add(Estimate(round_id=rnd.id))
        db_session.flush()
        with rejected(db_session, contains="uq_estimates_round"):
            db_session.add(Estimate(round_id=rnd.id))
            db_session.flush()

    def test_one_estimate_per_offer(self, db_session, factories):
        offer = factories.OfferFactory.create()
        db_session.add(Estimate(offer_id=offer.id))
        db_session.flush()
        with rejected(db_session, contains="uq_estimates_offer"):
            db_session.add(Estimate(offer_id=offer.id))
            db_session.flush()

    def test_amendment_no_only_with_contract(self, db_session, factories):
        offer = factories.OfferFactory.create()
        with rejected(db_session, contains="ck_estimates_amendment_owner"):
            db_session.add(Estimate(offer_id=offer.id, amendment_no=1))
            db_session.flush()

    def test_contract_pair_uniqueness_survives_partial_index(self, db_session, factories):
        """Частичный индекс держит прежний инвариант договоров (§2.1)."""
        contract = factories.ContractFactory.create()
        factories.EstimateFactory.create(contract=contract, amendment_no=None)
        with rejected(db_session, contains="uq_estimates_contract_amendment"):
            factories.EstimateFactory.create(contract=contract, amendment_no=None)


class TestProposalBaseline:
    def test_baseline_proposal_has_no_contractor(self, db_session, factories):
        lot = factories.LotFactory.create()
        db_session.add(Proposal(lot_id=lot.id, contractor_id=None, is_baseline=True))
        db_session.flush()

    def test_baseline_with_contractor_rejected(self, db_session, factories):
        lot = factories.LotFactory.create()
        contractor = factories.ContractorFactory.create()
        with rejected(db_session, contains="ck_proposals_baseline_contractor"):
            db_session.add(Proposal(lot_id=lot.id, contractor_id=contractor.id, is_baseline=True))
            db_session.flush()

    def test_participant_proposal_without_contractor_rejected(self, db_session, factories):
        lot = factories.LotFactory.create()
        with rejected(db_session, contains="ck_proposals_baseline_contractor"):
            db_session.add(Proposal(lot_id=lot.id, contractor_id=None, is_baseline=False))
            db_session.flush()


class TestImportJobOwners:
    def _job(self, **kw):
        return ImportJob(
            filename="f.xlsx", file_key=kw.pop("file_key", "k-1"), file_sha256="0" * 64,
            status=kw.pop("status", ImportJobStatus.pending.value), **kw,
        )

    def test_exactly_one_owner(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        contract = factories.ContractFactory.create()
        with rejected(db_session, contains="ck_import_jobs_owner"):
            db_session.add(self._job(contract_id=contract.id, round_id=rnd.id))
            db_session.flush()

    def test_amendment_no_only_with_contract(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        with rejected(db_session, contains="ck_import_jobs_amendment_owner"):
            db_session.add(self._job(round_id=rnd.id, amendment_no=1))
            db_session.flush()

    def test_active_round_lock(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.add(self._job(round_id=rnd.id, file_key="k-a"))
        db_session.flush()
        with rejected(db_session, contains="uq_import_jobs_active_round"):
            db_session.add(self._job(round_id=rnd.id, file_key="k-b"))
            db_session.flush()

    @pytest.mark.parametrize("terminal", [ImportJobStatus.done, ImportJobStatus.error])
    def test_terminal_round_job_releases_lock(self, db_session, factories, terminal):
        rnd = factories.TenderRoundFactory.create()
        db_session.add(self._job(round_id=rnd.id, file_key="k-a", status=terminal.value))
        db_session.add(self._job(round_id=rnd.id, file_key="k-b"))
        db_session.flush()

    def test_parsed_data_and_version_come_together(self, db_session, factories):
        contract = factories.ContractFactory.create()
        with rejected(db_session, contains="ck_import_jobs_parsed_pair"):
            db_session.add(self._job(contract_id=contract.id, parsed_data={"lots": {}}))
            db_session.flush()
        with rejected(db_session, contains="ck_import_jobs_parsed_pair"):
            db_session.add(self._job(contract_id=contract.id, parser_version="4.0.0"))
            db_session.flush()

    def test_estimates_created_positive_or_null(self, db_session, factories):
        contract = factories.ContractFactory.create()
        with rejected(db_session, contains="ck_import_jobs_estimates_created"):
            db_session.add(self._job(contract_id=contract.id, estimates_created=0))
            db_session.flush()
        db_session.add(self._job(contract_id=contract.id, estimates_created=None, file_key="k-null"))
        db_session.flush()


class TestContractorInn:
    def test_non_canonical_inn_rejected_by_schema(self, db_session, factories):
        """Правка мимо приложения — тоже под каноном (спека §2.7)."""
        with rejected(db_session, contains="ck_contractors_inn_canonical"):
            factories.ContractorFactory.create(inn="77 00 123")

    def test_empty_inn_rejected_by_schema(self, db_session, factories):
        with rejected(db_session, contains="ck_contractors_inn_canonical"):
            factories.ContractorFactory.create(inn="")


import importlib.util
from pathlib import Path

from models import (
    CONTRACTOR_INN_CANONICAL,
    ESTIMATE_AMENDMENT_ONLY_WITH_CONTRACT,
    ESTIMATE_OWNER_EXACTLY_ONE,
    IMPORT_JOB_AMENDMENT_ONLY_WITH_CONTRACT,
    IMPORT_JOB_ESTIMATES_CREATED_POSITIVE,
    IMPORT_JOB_OWNER_EXACTLY_ONE,
    IMPORT_JOB_PARSED_PAIR,
    PROPOSAL_BASELINE_CONTRACTOR,
    TENDER_NUMBER_NOT_BLANK,
    TENDER_ROUND_STAGE_NO_POSITIVE,
    TENDER_TITLE_NOT_BLANK,
)


def _migration_0015():
    path = next(Path(__file__).resolve().parents[2].glob("alembic/versions/*0015-tenders_contour.py"))
    spec = importlib.util.spec_from_file_location("_migration_0015", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestParityWithMigration:
    """`alembic check` выражений CHECK не сравнивает — сравниваем сами."""

    @pytest.mark.parametrize(
        ("model_expr", "migration_name"),
        [
            (TENDER_TITLE_NOT_BLANK, "CK_TENDERS_TITLE"),
            (TENDER_NUMBER_NOT_BLANK, "CK_TENDERS_NUMBER"),
            (TENDER_ROUND_STAGE_NO_POSITIVE, "CK_ROUNDS_STAGE_NO"),
            (ESTIMATE_OWNER_EXACTLY_ONE, "CK_ESTIMATES_OWNER"),
            (ESTIMATE_AMENDMENT_ONLY_WITH_CONTRACT, "CK_ESTIMATES_AMENDMENT_OWNER"),
            (PROPOSAL_BASELINE_CONTRACTOR, "CK_PROPOSALS_BASELINE"),
            (IMPORT_JOB_OWNER_EXACTLY_ONE, "CK_JOBS_OWNER"),
            (IMPORT_JOB_AMENDMENT_ONLY_WITH_CONTRACT, "CK_JOBS_AMENDMENT_OWNER"),
            (IMPORT_JOB_PARSED_PAIR, "CK_JOBS_PARSED_PAIR"),
            (IMPORT_JOB_ESTIMATES_CREATED_POSITIVE, "CK_JOBS_ESTIMATES_CREATED"),
            (CONTRACTOR_INN_CANONICAL, "CK_CONTRACTORS_INN"),
        ],
    )
    def test_check_expressions_match(self, model_expr, migration_name):
        assert model_expr == getattr(_migration_0015(), migration_name)


class TestInnDataMigration:
    """`_canonicalize_existing_inns` — на живом соединении со savepoint."""

    def test_two_contractors_collapsing_to_one_canon_refuse(self, db_session, factories):
        a = factories.ContractorFactory.create(inn="7700000001")
        db_session.flush()
        # Второго с неканоническим ИНН схема после 0015 не пустит — обходим
        # CHECK внутри теста, чтобы воспроизвести дофичевое состояние данных.
        db_session.execute(sa.text("ALTER TABLE contractors DROP CONSTRAINT ck_contractors_inn_canonical"))
        b = factories.ContractorFactory.create(inn="77 0000 0001")
        db_session.flush()
        with pytest.raises(RuntimeError, match=f"id={a.id} и id={b.id}"):
            _migration_0015()._canonicalize_existing_inns(db_session.connection())

    def test_formatted_inn_is_rewritten(self, db_session, factories):
        db_session.execute(sa.text("ALTER TABLE contractors DROP CONSTRAINT ck_contractors_inn_canonical"))
        c = factories.ContractorFactory.create(inn="77-00-000-002")
        db_session.flush()
        _migration_0015()._canonicalize_existing_inns(db_session.connection())
        db_session.expire_all()
        assert db_session.get(type(c), c.id).inn == "7700000002"


class TestDowngradeBlockers:
    """Каждая из трёх диагностик отката — отдельно, на живом соединении."""

    def test_clean_schema_does_not_block(self, db_session):
        m = _migration_0015()
        assert m._downgrade_refusal(m._downgrade_blockers(db_session.connection())) is None

    def test_tender_alone_blocks_and_is_named(self, db_session, factories):
        factories.TenderFactory.create()
        db_session.flush()
        m = _migration_0015()
        blockers = m._downgrade_blockers(db_session.connection())
        assert blockers == {"tenders": 1, "round_jobs": 0, "ownerless_estimates": 0}
        assert "тендеров — 1" in m._downgrade_refusal(blockers)

    def test_round_job_is_counted_separately(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        db_session.add(ImportJob(round_id=rnd.id, filename="f.xlsx", file_key="k-dg", file_sha256="0" * 64,
                                 status=ImportJobStatus.error.value))
        db_session.flush()
        m = _migration_0015()
        blockers = m._downgrade_blockers(db_session.connection())
        assert blockers["round_jobs"] == 1
        assert "заданий импорта раундов — 1" in m._downgrade_refusal(blockers)

    def test_ownerless_estimate_is_counted_separately(self, db_session, factories):
        offer = factories.OfferFactory.create()
        db_session.add(Estimate(offer_id=offer.id))
        db_session.flush()
        m = _migration_0015()
        blockers = m._downgrade_blockers(db_session.connection())
        assert blockers["ownerless_estimates"] == 1
        assert "смет предложений и baseline — 1" in m._downgrade_refusal(blockers)
