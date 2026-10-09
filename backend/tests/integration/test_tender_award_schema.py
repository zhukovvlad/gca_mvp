"""Схема отметки победителя тендера (миграция 0020; спека Б2 §2.2, §1.3): таблица
`tender_awards`, три новые колонки и составные ключи.

Сценарии §1.3 воспроизведены на НАСТОЯЩЕЙ схеме после `alembic upgrade`, по
одному входу на ограничение; имя нарушенного ограничения берётся из
`diag.constraint_name`, а не из подстроки текста. Сценарии, которые по таблице
проходят, проверяются исходом (что удалено, что осталось), а не отсутствием
исключения. `upgrade`/`downgrade` на данных — на отдельной scratch-базе.

Помощник `violates` сбрасывает сессию (`begin_nested` делает `flush`), поэтому
строка, которую ждём отвергнутой, создаётся внутри блока.
"""
from __future__ import annotations

import contextlib
import datetime as dt
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from models import (
    ESTIMATE_SOURCE_AWARD_ORIGINAL,
    IMPORT_JOB_SOURCE_AWARD_ORIGINAL,
    TENDER_AWARD_KP_INN_CANONICAL,
    TENDER_AWARD_NOT_CONCLUDED,
    TENDER_AWARD_NOTE_NOT_BLANK,
    Base,
    ImportJob,
    TenderAward,
    UserRole,
)
from tests.integration.test_work_variants_schema import _load_migration, _scratch_alembic

pytestmark = pytest.mark.integration

# Независимые литералы: эталон не вычисляется тем кодом, что проверяется.
EXPECTED_NOT_CONCLUDED = (
    "(not_concluded_on IS NULL AND not_concluded_by IS NULL "
    "AND not_concluded_at IS NULL AND not_concluded_note IS NULL) "
    "OR (not_concluded_on IS NOT NULL AND not_concluded_by IS NOT NULL "
    "AND not_concluded_at IS NOT NULL)"
)
EXPECTED_NOTE_NOT_BLANK = "not_concluded_note IS NULL OR btrim(not_concluded_note) <> ''"
EXPECTED_KP_INN = "kp_inn ~ '^[0-9]+$'"
EXPECTED_SOURCE_AWARD = "source_award_id IS NULL OR (contract_id IS NOT NULL AND amendment_no IS NULL)"

_NOW = dt.datetime(2026, 10, 8, 12, 0, tzinfo=dt.UTC)


@contextlib.contextmanager
def violates(session, constraint: str):
    """Ожидаем отказ БД именно этого ограничения (по `diag.constraint_name`)."""
    with pytest.raises(IntegrityError) as exc, session.begin_nested():
        yield
        session.flush()
    assert exc.value.orig.diag.constraint_name == constraint


def _sql(session, statement: str, **params):
    return session.execute(sa.text(statement), params)


def _count(session, statement: str, **params) -> int:
    return _sql(session, statement, **params).scalar_one()


# ---------------------------------------------------------------------------
#  Корпус: тендер, два этапа, два участника, КП на каждую оферту
# ---------------------------------------------------------------------------

def _award(session, c, side: str, *, round_no: int = 2, **overrides) -> TenderAward:
    """Отметка победителя `side` ('a'/'b') по оферте этапа `round_no`."""
    offer = c.offers[(round_no, side)]
    package = c.packages[side]
    values = dict(
        tender_id=c.tender.id,
        object_id=c.tender.object_id,
        offer_id=offer.id,
        package_id=package.id,
        contractor_id=package.contractor_id,
        estimate_id=c.kp[(round_no, side)].id,
        kp_inn="7701234567",
        awarded_by=c.user.id,
    )
    values.update(overrides)
    award = TenderAward(**values)
    session.add(award)
    session.flush()
    return award


def _corpus(session, factories) -> SimpleNamespace:
    """Тендер 1, этапы 1 и 2, участники A и B. Отметки: A на втором этапе с
    «договор не заключён» (история) и B на втором этапе действующая."""
    tender = factories.TenderFactory.create()
    rounds = {
        n: factories.TenderRoundFactory.create(tender=tender, stage_no=n) for n in (1, 2)
    }
    packages = {s: factories.OfferPackageFactory.create(tender=tender) for s in ("a", "b")}
    offers = {
        (n, s): factories.OfferFactory.create(round=rounds[n], package=packages[s])
        for n in (1, 2)
        for s in ("a", "b")
    }
    kp = {
        key: factories.EstimateFactory.create(contract=None, offer=offer)
        for key, offer in offers.items()
    }
    baseline = factories.EstimateFactory.create(contract=None, round=rounds[2])
    user = factories.UserFactory.create(role=UserRole.admin)
    c = SimpleNamespace(
        tender=tender, rounds=rounds, packages=packages, offers=offers, kp=kp,
        baseline=baseline, user=user,
    )
    c.history = _award(
        session, c, "a",
        not_concluded_on=dt.date(2026, 1, 15), not_concluded_by=user.id, not_concluded_at=_NOW,
    )
    c.active = _award(session, c, "b")
    return c


def _mini(session, factories) -> SimpleNamespace:
    """Тендер с одним этапом, одним участником, одной офертой и КП — без отметок."""
    tender = factories.TenderFactory.create()
    rnd = factories.TenderRoundFactory.create(tender=tender, stage_no=1)
    package = factories.OfferPackageFactory.create(tender=tender)
    offer = factories.OfferFactory.create(round=rnd, package=package)
    kp = factories.EstimateFactory.create(contract=None, offer=offer)
    user = factories.UserFactory.create(role=UserRole.admin)
    return SimpleNamespace(
        tender=tender, rounds={1: rnd}, packages={"a": package}, offers={(1, "a"): offer},
        kp={(1, "a"): kp}, user=user,
    )


def _linked_contract(factories, c, *, award=None):
    """Договор, привязанный к отметке (по умолчанию к действующей, участник B)."""
    award = award or c.active
    return factories.ContractFactory.create(
        object=c.tender.object, contractor=c.packages["b"].contractor, tender_award_id=award.id,
    )


def _tenders_left(session, c) -> int:
    return _count(session, "SELECT count(*) FROM tenders WHERE id = :t", t=c.tender.id)


def _awards_left(session, c) -> int:
    return _count(session, "SELECT count(*) FROM tender_awards WHERE tender_id = :t", t=c.tender.id)


# ---------------------------------------------------------------------------
#  Сценарии §1.3: порядок каскадов и составные ключи
# ---------------------------------------------------------------------------

class TestCascadeScenarios:
    def test_s1_plain_tender_delete_takes_awards_without_contract(self, db_session, factories):
        c = _corpus(db_session, factories)
        assert _awards_left(db_session, c) == 2
        _sql(db_session, "DELETE FROM tenders WHERE id = :t", t=c.tender.id)
        assert _tenders_left(db_session, c) == 0
        assert _awards_left(db_session, c) == 0

    def test_s2_delete_offers_before_tender_is_refused_by_offer_key(self, db_session, factories):
        c = _corpus(db_session, factories)
        with violates(db_session, "fk_tender_awards_offer"):
            _sql(db_session, "DELETE FROM offers WHERE tender_id = :t", t=c.tender.id)

    def test_s2b_awards_then_offers_then_tender_passes(self, db_session, factories):
        c = _corpus(db_session, factories)
        _sql(db_session, "DELETE FROM tender_awards WHERE tender_id = :t", t=c.tender.id)
        _sql(db_session, "DELETE FROM offers WHERE tender_id = :t", t=c.tender.id)
        _sql(db_session, "DELETE FROM tenders WHERE id = :t", t=c.tender.id)
        assert _tenders_left(db_session, c) == 0
        assert _awards_left(db_session, c) == 0

    def test_s3_delete_final_round_with_awards_is_refused(self, db_session, factories):
        c = _corpus(db_session, factories)
        with violates(db_session, "fk_tender_awards_offer"):
            _sql(db_session, "DELETE FROM tender_rounds WHERE id = :r", r=c.rounds[2].id)

    def test_s3b_delete_round_without_awards_passes_and_keeps_awards(self, db_session, factories):
        c = _corpus(db_session, factories)
        _sql(db_session, "DELETE FROM tender_rounds WHERE id = :r", r=c.rounds[1].id)
        assert _count(db_session, "SELECT count(*) FROM tender_rounds WHERE id = :r", r=c.rounds[1].id) == 0
        assert _count(
            db_session, "SELECT count(*) FROM offers WHERE round_id = :r", r=c.rounds[1].id
        ) == 0
        assert _awards_left(db_session, c) == 2

    def test_s4_delete_offers_of_participant_with_history_award_is_refused(
        self, db_session, factories
    ):
        c = _corpus(db_session, factories)
        with violates(db_session, "fk_tender_awards_offer"):
            _sql(db_session, "DELETE FROM offers WHERE package_id = :p", p=c.packages["a"].id)

    def test_s5_delete_final_round_estimates_is_refused_by_kp_key(self, db_session, factories):
        c = _corpus(db_session, factories)
        with violates(db_session, "fk_tender_awards_kp_estimate"):
            _sql(
                db_session,
                "DELETE FROM estimates WHERE offer_id IN (:a, :b) OR round_id = :r",
                a=c.offers[(2, "a")].id, b=c.offers[(2, "b")].id, r=c.rounds[2].id,
            )

    def test_s5b_delete_estimates_of_round_without_awards_passes(self, db_session, factories):
        c = _corpus(db_session, factories)
        _sql(
            db_session,
            "DELETE FROM estimates WHERE offer_id IN (:a, :b)",
            a=c.offers[(1, "a")].id, b=c.offers[(1, "b")].id,
        )
        assert _count(
            db_session, "SELECT count(*) FROM estimates WHERE offer_id IN (:a, :b)",
            a=c.offers[(1, "a")].id, b=c.offers[(1, "b")].id,
        ) == 0
        assert _count(
            db_session, "SELECT count(*) FROM estimates WHERE offer_id IN (:a, :b)",
            a=c.offers[(2, "a")].id, b=c.offers[(2, "b")].id,
        ) == 2


class TestContractLink:
    def test_s6_tender_delete_with_linked_contract_is_refused(self, db_session, factories):
        c = _corpus(db_session, factories)
        _linked_contract(factories, c)
        with violates(db_session, "fk_contracts_tender_award"):
            _sql(db_session, "DELETE FROM tenders WHERE id = :t", t=c.tender.id)

    def test_s7_removing_award_with_contract_is_refused(self, db_session, factories):
        c = _corpus(db_session, factories)
        _linked_contract(factories, c)
        with violates(db_session, "fk_contracts_tender_award"):
            _sql(db_session, "DELETE FROM tender_awards WHERE id = :a", a=c.active.id)

    def test_s8a_changing_object_of_linked_contract_is_refused(self, db_session, factories):
        c = _corpus(db_session, factories)
        contract = _linked_contract(factories, c)
        other = factories.ObjectFactory.create()
        with violates(db_session, "fk_contracts_tender_award"):
            _sql(
                db_session, "UPDATE contracts SET object_id = :o WHERE id = :c",
                o=other.id, c=contract.id,
            )

    def test_s8b_changing_contractor_of_linked_contract_is_refused(self, db_session, factories):
        c = _corpus(db_session, factories)
        contract = _linked_contract(factories, c)
        with violates(db_session, "fk_contracts_tender_award"):
            _sql(
                db_session, "UPDATE contracts SET contractor_id = :k WHERE id = :c",
                k=c.packages["a"].contractor_id, c=contract.id,
            )

    def test_s8c_after_unlink_object_can_change(self, db_session, factories):
        c = _corpus(db_session, factories)
        contract = _linked_contract(factories, c)
        other = factories.ObjectFactory.create()
        _sql(db_session, "UPDATE contracts SET tender_award_id = NULL WHERE id = :c", c=contract.id)
        _sql(
            db_session, "UPDATE contracts SET object_id = :o WHERE id = :c",
            o=other.id, c=contract.id,
        )
        row = _sql(
            db_session, "SELECT tender_award_id, object_id FROM contracts WHERE id = :c",
            c=contract.id,
        ).one()
        assert tuple(row) == (None, other.id)

    def test_s9_linking_contract_of_another_object_is_refused(self, db_session, factories):
        c = _corpus(db_session, factories)
        stranger = factories.ContractFactory.create(
            object=factories.ObjectFactory.create(), contractor=c.packages["b"].contractor,
        )
        with violates(db_session, "fk_contracts_tender_award"):
            _sql(
                db_session, "UPDATE contracts SET tender_award_id = :a WHERE id = :c",
                a=c.active.id, c=stranger.id,
            )

    def test_s9b_linking_contract_of_another_contractor_is_refused(self, db_session, factories):
        c = _corpus(db_session, factories)
        stranger = factories.ContractFactory.create(
            object=c.tender.object, contractor=c.packages["a"].contractor,
        )
        with violates(db_session, "fk_contracts_tender_award"):
            _sql(
                db_session, "UPDATE contracts SET tender_award_id = :a WHERE id = :c",
                a=c.active.id, c=stranger.id,
            )

    def test_one_contract_per_award(self, db_session, factories):
        c = _corpus(db_session, factories)
        _linked_contract(factories, c)
        with violates(db_session, "uq_contracts_tender_award"):
            _linked_contract(factories, c)

    def test_contract_without_basis_is_not_checked_by_the_key(self, db_session, factories):
        """`MATCH SIMPLE`: при `tender_award_id IS NULL` ключ не проверяется —
        обычный договор живёт с любым объектом и подрядчиком."""
        contract = factories.ContractFactory.create()
        assert contract.tender_award_id is None
        assert _count(db_session, "SELECT count(*) FROM contracts WHERE id = :c", c=contract.id) == 1


class TestSourceAward:
    def test_s10_unlink_contract_with_copy_estimate_is_refused(self, db_session, factories):
        c = _corpus(db_session, factories)
        contract = _linked_contract(factories, c)
        factories.EstimateFactory.create(contract=contract, source_award_id=c.active.id)
        with violates(db_session, "fk_estimates_source_award"):
            _sql(
                db_session, "UPDATE contracts SET tender_award_id = NULL WHERE id = :c",
                c=contract.id,
            )

    def test_s10b_unlink_after_copy_is_replaced_passes(self, db_session, factories):
        c = _corpus(db_session, factories)
        contract = _linked_contract(factories, c)
        copy = factories.EstimateFactory.create(contract=contract, source_award_id=c.active.id)
        _sql(db_session, "DELETE FROM estimates WHERE id = :e", e=copy.id)
        factories.EstimateFactory.create(contract=contract)
        _sql(
            db_session, "UPDATE contracts SET tender_award_id = NULL WHERE id = :c", c=contract.id
        )
        assert _count(
            db_session, "SELECT count(*) FROM contracts WHERE id = :c AND tender_award_id IS NULL",
            c=contract.id,
        ) == 1
        assert _count(
            db_session, "SELECT count(*) FROM estimates WHERE contract_id = :c AND source_award_id IS NULL",
            c=contract.id,
        ) == 1

    def test_s11_delete_contract_then_award_then_tender_passes(self, db_session, factories):
        c = _corpus(db_session, factories)
        contract = _linked_contract(factories, c)
        factories.EstimateFactory.create(contract=contract, source_award_id=c.active.id)
        _sql(db_session, "DELETE FROM estimates WHERE contract_id = :c", c=contract.id)
        _sql(db_session, "DELETE FROM contracts WHERE id = :c", c=contract.id)
        _sql(db_session, "DELETE FROM tender_awards WHERE id = :a", a=c.active.id)
        _sql(db_session, "DELETE FROM tender_awards WHERE tender_id = :t", t=c.tender.id)
        _sql(db_session, "DELETE FROM offers WHERE tender_id = :t", t=c.tender.id)
        _sql(db_session, "DELETE FROM tenders WHERE id = :t", t=c.tender.id)
        assert _tenders_left(db_session, c) == 0
        assert _awards_left(db_session, c) == 0
        assert _count(db_session, "SELECT count(*) FROM contracts WHERE id = :c", c=contract.id) == 0

    def test_s12_copy_pointing_at_award_that_is_not_the_basis_is_refused(
        self, db_session, factories
    ):
        c = _corpus(db_session, factories)
        contract = _linked_contract(factories, c)
        with violates(db_session, "fk_estimates_source_award"):
            factories.EstimateFactory.create(contract=contract, source_award_id=c.history.id)

    def test_s12b_copy_on_contract_without_basis_is_refused(self, db_session, factories):
        c = _corpus(db_session, factories)
        plain = factories.ContractFactory.create(
            object=c.tender.object, contractor=c.packages["b"].contractor,
        )
        with violates(db_session, "fk_estimates_source_award"):
            factories.EstimateFactory.create(contract=plain, source_award_id=c.active.id)

    def test_s13_copy_on_amendment_is_refused_by_check(self, db_session, factories):
        c = _corpus(db_session, factories)
        contract = _linked_contract(factories, c)
        with violates(db_session, "ck_estimates_source_award"):
            factories.EstimateFactory.create(
                contract=contract, amendment_no=1, source_award_id=c.active.id,
            )

    def test_copy_on_offer_estimate_is_refused_by_check(self, db_session, factories):
        """Без договора `MATCH SIMPLE` ключ молчит — отказывает только CHECK."""
        c = _corpus(db_session, factories)
        with violates(db_session, "ck_estimates_source_award"):
            factories.EstimateFactory.create(
                contract=None, offer=c.offers[(1, "a")], source_award_id=c.active.id,
            )

    def test_copy_on_matching_contract_original_estimate_passes(self, db_session, factories):
        c = _corpus(db_session, factories)
        contract = _linked_contract(factories, c)
        copy = factories.EstimateFactory.create(
            contract=contract, amendment_no=None, source_award_id=c.active.id,
        )
        assert copy.source_award_id == c.active.id


class TestAwardKeys:
    def test_s14_second_active_award_is_refused(self, db_session, factories):
        c = _corpus(db_session, factories)
        with violates(db_session, "uq_tender_awards_active"):
            _award(db_session, c, "a")

    def test_second_active_award_after_the_first_is_not_concluded_passes(
        self, db_session, factories
    ):
        c = _corpus(db_session, factories)
        c.active.not_concluded_on = dt.date(2026, 2, 1)
        c.active.not_concluded_by = c.user.id
        c.active.not_concluded_at = _NOW
        db_session.flush()
        _award(db_session, c, "a")
        assert _count(
            db_session,
            "SELECT count(*) FROM tender_awards WHERE tender_id = :t AND not_concluded_on IS NULL",
            t=c.tender.id,
        ) == 1

    def test_s14b_award_with_kp_of_another_offer_is_refused(self, db_session, factories):
        c = _corpus(db_session, factories)
        c.active.not_concluded_on = dt.date(2026, 2, 1)
        c.active.not_concluded_by = c.user.id
        c.active.not_concluded_at = _NOW
        db_session.flush()
        with violates(db_session, "fk_tender_awards_kp_estimate"):
            _award(db_session, c, "a", estimate_id=c.kp[(1, "a")].id)

    def test_s14c_award_with_contractor_not_of_its_package_is_refused(self, db_session, factories):
        c = _corpus(db_session, factories)
        c.active.not_concluded_on = dt.date(2026, 2, 1)
        c.active.not_concluded_by = c.user.id
        c.active.not_concluded_at = _NOW
        db_session.flush()
        with violates(db_session, "fk_tender_awards_package"):
            _award(db_session, c, "a", contractor_id=c.packages["b"].contractor_id)

    def test_award_with_object_of_another_tender_is_refused(self, db_session, factories):
        c = _mini(db_session, factories)
        other = factories.ObjectFactory.create()
        with violates(db_session, "fk_tender_awards_tender"):
            _award(db_session, c, "a", round_no=1, object_id=other.id)

    def test_award_with_offer_of_another_package_is_refused(self, db_session, factories):
        c = _corpus(db_session, factories)
        c.active.not_concluded_on = dt.date(2026, 2, 1)
        c.active.not_concluded_by = c.user.id
        c.active.not_concluded_at = _NOW
        db_session.flush()
        with violates(db_session, "fk_tender_awards_offer"):
            _award(
                db_session, c, "a", package_id=c.packages["b"].id,
                contractor_id=c.packages["b"].contractor_id,
            )


# ---------------------------------------------------------------------------
#  CHECK-и самой отметки
# ---------------------------------------------------------------------------

class TestAwardChecks:
    @pytest.mark.parametrize(
        "partial",
        [
            pytest.param({"not_concluded_on": dt.date(2026, 1, 15)}, id="only_date"),
            pytest.param({"not_concluded_by": "user"}, id="only_author"),
            pytest.param({"not_concluded_at": _NOW}, id="only_moment"),
            pytest.param({"not_concluded_note": "причина"}, id="note_without_the_rest"),
        ],
    )
    def test_not_concluded_partial_fill_is_refused(self, db_session, factories, partial):
        c = _mini(db_session, factories)
        values = {k: (c.user.id if v == "user" else v) for k, v in partial.items()}
        with violates(db_session, "ck_tender_awards_not_concluded"):
            _award(db_session, c, "a", round_no=1, **values)

    def test_not_concluded_note_with_two_of_three_is_refused(self, db_session, factories):
        c = _mini(db_session, factories)
        with violates(db_session, "ck_tender_awards_not_concluded"):
            _award(
                db_session, c, "a", round_no=1,
                not_concluded_on=dt.date(2026, 1, 15), not_concluded_by=c.user.id,
            )

    @pytest.mark.parametrize(
        "missing",
        ["not_concluded_on", "not_concluded_by", "not_concluded_at"],
        ids=["missing_date", "missing_author", "missing_moment"],
    )
    def test_not_concluded_two_of_three_is_refused(self, db_session, factories, missing):
        """Каждое поле второй ветви — своим входом: два из трёх заданы, одного
        нет. Одиночные поля этого не различают — их отвергает и ветвь без
        снятого члена."""
        c = _mini(db_session, factories)
        values = {
            "not_concluded_on": dt.date(2026, 1, 15),
            "not_concluded_by": c.user.id,
            "not_concluded_at": _NOW,
        }
        del values[missing]
        with violates(db_session, "ck_tender_awards_not_concluded"):
            _award(db_session, c, "a", round_no=1, **values)

    def test_award_without_any_not_concluded_field_passes(self, db_session, factories):
        c = _mini(db_session, factories)
        award = _award(db_session, c, "a", round_no=1)
        assert award.not_concluded_on is None and award.not_concluded_note is None

    def test_not_concluded_with_three_fields_and_no_note_passes(self, db_session, factories):
        c = _mini(db_session, factories)
        award = _award(
            db_session, c, "a", round_no=1,
            not_concluded_on=dt.date(2026, 1, 15), not_concluded_by=c.user.id,
            not_concluded_at=_NOW,
        )
        assert award.not_concluded_note is None

    def test_not_concluded_with_all_four_fields_passes(self, db_session, factories):
        c = _mini(db_session, factories)
        award = _award(
            db_session, c, "a", round_no=1,
            not_concluded_on=dt.date(2026, 1, 15), not_concluded_by=c.user.id,
            not_concluded_at=_NOW, not_concluded_note="отказался подписывать",
        )
        assert award.not_concluded_note == "отказался подписывать"

    @pytest.mark.parametrize("note", ["", "   "], ids=["empty", "spaces"])
    def test_blank_note_is_refused(self, db_session, factories, note):
        c = _mini(db_session, factories)
        with violates(db_session, "ck_tender_awards_note_not_blank"):
            _award(
                db_session, c, "a", round_no=1,
                not_concluded_on=dt.date(2026, 1, 15), not_concluded_by=c.user.id,
                not_concluded_at=_NOW, not_concluded_note=note,
            )

    @pytest.mark.parametrize("inn", ["77 01234567", "77O1234567", "", "7701234567 "])
    def test_non_canonical_kp_inn_is_refused(self, db_session, factories, inn):
        c = _mini(db_session, factories)
        with violates(db_session, "ck_tender_awards_kp_inn_canonical"):
            _award(db_session, c, "a", round_no=1, kp_inn=inn)

    def test_digits_only_kp_inn_passes(self, db_session, factories):
        c = _mini(db_session, factories)
        assert _award(db_session, c, "a", round_no=1, kp_inn="0123456789").kp_inn == "0123456789"

    def test_author_must_exist(self, db_session, factories):
        c = _mini(db_session, factories)
        with violates(db_session, "fk_tender_awards_awarded_by"):
            _award(db_session, c, "a", round_no=1, awarded_by=2_000_000_000)

    def test_not_concluded_author_must_exist(self, db_session, factories):
        c = _mini(db_session, factories)
        with violates(db_session, "fk_tender_awards_not_concluded_by"):
            _award(
                db_session, c, "a", round_no=1,
                not_concluded_on=dt.date(2026, 1, 15), not_concluded_by=2_000_000_000,
                not_concluded_at=_NOW,
            )

    def test_awarded_at_defaults_to_now(self, db_session, factories):
        c = _mini(db_session, factories)
        award = _award(db_session, c, "a", round_no=1)
        db_session.refresh(award)
        assert award.awarded_at is not None


# ---------------------------------------------------------------------------
#  Задания импорта
# ---------------------------------------------------------------------------

def _job(factories, **overrides):
    return factories.ImportJobFactory.create(**overrides)


class TestImportJobSourceAward:
    def test_round_job_with_source_award_is_refused(self, db_session, factories):
        c = _corpus(db_session, factories)
        with violates(db_session, "ck_import_jobs_source_award"):
            _job(factories, contract=None, round=c.rounds[2], source_award_id=c.active.id)

    def test_amendment_job_with_source_award_is_refused(self, db_session, factories):
        c = _corpus(db_session, factories)
        contract = _linked_contract(factories, c)
        with violates(db_session, "ck_import_jobs_source_award"):
            _job(factories, contract=contract, amendment_no=1, source_award_id=c.active.id)

    def test_original_contract_job_with_source_award_passes(self, db_session, factories):
        c = _corpus(db_session, factories)
        contract = _linked_contract(factories, c)
        job = _job(factories, contract=contract, amendment_no=None, source_award_id=c.active.id)
        assert job.source_award_id == c.active.id

    def test_source_award_must_exist(self, db_session, factories):
        with violates(db_session, "fk_import_jobs_source_award"):
            _job(factories, source_award_id=987_654_321)

    def test_removing_award_nulls_job_reference_instead_of_refusing(self, db_session, factories):
        c = _corpus(db_session, factories)
        contract = factories.ContractFactory.create(
            object=c.tender.object, contractor=c.packages["a"].contractor,
        )
        job = _job(factories, contract=contract, amendment_no=None, source_award_id=c.history.id)
        _sql(db_session, "DELETE FROM tender_awards WHERE id = :a", a=c.history.id)
        assert _count(
            db_session, "SELECT count(*) FROM tender_awards WHERE id = :a", a=c.history.id
        ) == 0
        remaining = _sql(
            db_session, "SELECT source_award_id FROM import_jobs WHERE id = :j", j=job.id
        ).scalar_one()
        assert remaining is None


# ---------------------------------------------------------------------------
#  Состав схемы: имена, виды ключей, индексы
# ---------------------------------------------------------------------------

class TestSchemaShape:
    @pytest.mark.parametrize(
        ("table", "name"),
        [
            ("tenders", "uq_tenders_id_object"),
            ("offer_packages", "uq_offer_packages_id_contractor"),
            ("offers", "uq_offers_id_tender_package"),
            ("estimates", "uq_estimates_id_offer"),
            ("contracts", "uq_contracts_tender_award"),
            ("contracts", "uq_contracts_id_tender_award"),
            ("tender_awards", "uq_tender_awards_id_object_contractor"),
        ],
    )
    def test_unique_constraint_exists(self, db_session, table, name):
        assert _count(
            db_session,
            "SELECT count(*) FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
            "WHERE c.contype = 'u' AND c.conname = :n AND t.relname = :t",
            n=name, t=table,
        ) == 1

    @pytest.mark.parametrize(
        ("name", "action"),
        [
            ("fk_tender_awards_tender", "c"),        # CASCADE
            ("fk_tender_awards_offer", "a"),         # NO ACTION
            ("fk_tender_awards_package", "a"),
            ("fk_tender_awards_kp_estimate", "a"),
            ("fk_tender_awards_awarded_by", "r"),    # RESTRICT
            ("fk_tender_awards_not_concluded_by", "r"),
            ("fk_contracts_tender_award", "a"),
            ("fk_estimates_source_award", "a"),
            ("fk_import_jobs_source_award", "n"),    # SET NULL
        ],
    )
    def test_foreign_key_delete_action(self, db_session, name, action):
        assert _sql(
            db_session,
            "SELECT confdeltype FROM pg_constraint WHERE contype = 'f' AND conname = :n",
            n=name,
        ).scalar_one() == action

    def test_active_award_index_is_partial_unique(self, db_session):
        definition = _sql(
            db_session,
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_tender_awards_active'",
        ).scalar_one()
        assert definition.startswith("CREATE UNIQUE INDEX")
        assert "(tender_id)" in definition
        assert "WHERE (not_concluded_on IS NULL)" in definition

    @pytest.mark.parametrize(
        "name",
        [
            "ix_tender_awards_tender_id",
            "ix_tender_awards_offer_id",
            "ix_tender_awards_package_id",
            "ix_tender_awards_estimate_id",
        ],
    )
    def test_lookup_index_exists(self, db_session, name):
        assert _count(
            db_session, "SELECT count(*) FROM pg_indexes WHERE indexname = :n", n=name
        ) == 1

    def test_composite_keys_have_the_declared_column_lists(self, db_session):
        expected = {
            "fk_tender_awards_tender": ("tender_id, object_id", "tenders", "id, object_id"),
            "fk_tender_awards_offer": (
                "offer_id, tender_id, package_id", "offers", "id, tender_id, package_id",
            ),
            "fk_tender_awards_package": (
                "package_id, contractor_id", "offer_packages", "id, contractor_id",
            ),
            "fk_tender_awards_kp_estimate": ("estimate_id, offer_id", "estimates", "id, offer_id"),
            "fk_contracts_tender_award": (
                "tender_award_id, object_id, contractor_id", "tender_awards",
                "id, object_id, contractor_id",
            ),
            "fk_estimates_source_award": (
                "contract_id, source_award_id", "contracts", "id, tender_award_id",
            ),
        }
        for name, (local, target, remote) in expected.items():
            definition = _sql(
                db_session,
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :n",
                n=name,
            ).scalar_one()
            assert f"FOREIGN KEY ({local}) REFERENCES {target}({remote})" in definition, name

    def test_tender_awards_is_listed_among_domain_tables(self):
        from tests.conftest import _DOMAIN_TABLES

        assert "tender_awards" in _DOMAIN_TABLES

    def test_new_columns_are_nullable(self, db_session):
        rows = _sql(
            db_session,
            "SELECT table_name, is_nullable FROM information_schema.columns WHERE "
            "(table_name, column_name) IN (('contracts', 'tender_award_id'), "
            "('estimates', 'source_award_id'), ('import_jobs', 'source_award_id'))",
        ).all()
        assert sorted(tuple(r) for r in rows) == [
            ("contracts", "YES"), ("estimates", "YES"), ("import_jobs", "YES"),
        ]


# ---------------------------------------------------------------------------
#  Выражения CHECK: модель и миграция равны независимому литералу
# ---------------------------------------------------------------------------

def _migration_0020():
    return _load_migration("*0020-tender_awards.py", "_migration_0020")


_CONSTANT_CASES = [
    ("TENDER_AWARD_NOT_CONCLUDED", EXPECTED_NOT_CONCLUDED),
    ("TENDER_AWARD_NOTE_NOT_BLANK", EXPECTED_NOTE_NOT_BLANK),
    ("TENDER_AWARD_KP_INN_CANONICAL", EXPECTED_KP_INN),
    ("ESTIMATE_SOURCE_AWARD_ORIGINAL", EXPECTED_SOURCE_AWARD),
    ("IMPORT_JOB_SOURCE_AWARD_ORIGINAL", EXPECTED_SOURCE_AWARD),
]


class TestParityWithMigration:
    @pytest.mark.parametrize(("name", "literal"), _CONSTANT_CASES, ids=[n for n, _ in _CONSTANT_CASES])
    def test_model_constant_equals_independent_literal(self, name, literal):
        import models

        assert getattr(models, name) == literal

    @pytest.mark.parametrize(("name", "literal"), _CONSTANT_CASES, ids=[n for n, _ in _CONSTANT_CASES])
    def test_migration_constant_equals_independent_literal(self, name, literal):
        assert getattr(_migration_0020(), name) == literal

    def test_constants_are_the_ones_the_model_constraints_carry(self):
        carried = {
            ("tender_awards", "ck_tender_awards_not_concluded"): TENDER_AWARD_NOT_CONCLUDED,
            ("tender_awards", "ck_tender_awards_note_not_blank"): TENDER_AWARD_NOTE_NOT_BLANK,
            ("tender_awards", "ck_tender_awards_kp_inn_canonical"): TENDER_AWARD_KP_INN_CANONICAL,
            ("estimates", "ck_estimates_source_award"): ESTIMATE_SOURCE_AWARD_ORIGINAL,
            ("import_jobs", "ck_import_jobs_source_award"): IMPORT_JOB_SOURCE_AWARD_ORIGINAL,
        }
        for (table, name), expression in carried.items():
            constraint = next(
                k for k in Base.metadata.tables[table].constraints if k.name == name
            )
            assert str(constraint.sqltext) == expression, name

    def test_model_active_index_is_the_partial_unique_of_the_migration(self):
        """`alembic check` видит наличие индекса, но не сравнивает его условие
        (замерено на ревью задачи 1): условие модели сверяется здесь."""
        index = next(
            i for i in Base.metadata.tables["tender_awards"].indexes
            if i.name == "uq_tender_awards_active"
        )
        assert index.unique is True
        assert [c.name for c in index.columns] == ["tender_id"]
        assert str(index.dialect_options["postgresql"]["where"]) == "not_concluded_on IS NULL"

    def test_migration_revision_chain(self):
        module = _migration_0020()
        assert (module.revision, module.down_revision) == ("0020", "0019")

    def test_import_job_model_has_the_column(self):
        assert "source_award_id" in ImportJob.__table__.columns


# ---------------------------------------------------------------------------
#  upgrade / downgrade на scratch-базе
# ---------------------------------------------------------------------------

_SEED_BEFORE_0020 = """
INSERT INTO rate_classes (id, title) VALUES (901, 'scratch class');
INSERT INTO objects (id, title, address) VALUES (901, 'scratch object', 'addr');
INSERT INTO contractors (id, title, inn, address, accreditation)
    VALUES (901, 'scratch A', '7700000901', 'addr', 'yes'), (902, 'scratch B', '7700000902', 'addr', 'yes');
INSERT INTO contracts (id, object_id, contractor_id, rate_class_id, contract_number, signed_date)
    VALUES (901, 901, 901, 901, 'SCR-1', '2026-01-01');
INSERT INTO estimates (id, contract_id, title) VALUES (901, 901, 'contract estimate');
INSERT INTO import_jobs (id, contract_id, filename, file_key, file_sha256, status)
    VALUES (901, 901, 'f.xlsx', 'scratch-key-1', 'sha', 'done');
INSERT INTO tenders (id, object_id, title, tender_number, rate_class_id)
    VALUES (901, 901, 'scratch tender', 'SCR-T-1', 901);
INSERT INTO tender_rounds (id, tender_id, stage_no) VALUES (901, 901, 1);
INSERT INTO offer_packages (id, tender_id, contractor_id) VALUES (901, 901, 901), (902, 901, 902);
INSERT INTO offers (id, tender_id, round_id, package_id) VALUES (901, 901, 901, 901), (902, 901, 901, 902);
INSERT INTO estimates (id, offer_id, title) VALUES (902, 901, 'kp a'), (903, 902, 'kp b');
INSERT INTO estimates (id, round_id, title) VALUES (904, 901, 'baseline');
"""

_ROW_COUNTS = (
    "SELECT (SELECT count(*) FROM contracts), (SELECT count(*) FROM estimates), "
    "(SELECT count(*) FROM import_jobs), (SELECT count(*) FROM tenders), "
    "(SELECT count(*) FROM offers)"
)

_NEW_STRUCTURE = (
    "SELECT "
    "(SELECT count(*) FROM information_schema.tables WHERE table_name = 'tender_awards'), "
    "(SELECT count(*) FROM information_schema.columns WHERE "
    " (table_name, column_name) IN (('contracts', 'tender_award_id'), "
    " ('estimates', 'source_award_id'), ('import_jobs', 'source_award_id'))), "
    "(SELECT count(*) FROM pg_constraint WHERE conname IN ("
    " 'uq_tenders_id_object', 'uq_offer_packages_id_contractor', 'uq_offers_id_tender_package', "
    " 'uq_estimates_id_offer', 'uq_contracts_tender_award', 'uq_contracts_id_tender_award', "
    " 'fk_contracts_tender_award', 'fk_estimates_source_award', 'fk_import_jobs_source_award', "
    " 'ck_estimates_source_award', 'ck_import_jobs_source_award'))"
)


@pytest.mark.migration_roundtrip
class TestUpgradeAndDowngradeOnData:
    def test_upgrade_keeps_rows_and_leaves_new_columns_null_then_round_trips(self):
        with _scratch_alembic("tender award data") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0019")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.begin() as conn:
                    for statement in filter(None, (s.strip() for s in _SEED_BEFORE_0020.split(";\n"))):
                        conn.execute(sa.text(statement))
                    before = tuple(conn.execute(sa.text(_ROW_COUNTS)).one())
                assert before == (1, 4, 1, 1, 2)

                command.upgrade(cfg, "0020")
                with engine.connect() as conn:
                    assert tuple(conn.execute(sa.text(_ROW_COUNTS)).one()) == before
                    assert tuple(conn.execute(sa.text(_NEW_STRUCTURE)).one()) == (1, 3, 11)
                    nulls = conn.execute(
                        sa.text(
                            "SELECT "
                            "(SELECT count(*) FROM contracts WHERE tender_award_id IS NOT NULL), "
                            "(SELECT count(*) FROM estimates WHERE source_award_id IS NOT NULL), "
                            "(SELECT count(*) FROM import_jobs WHERE source_award_id IS NOT NULL), "
                            "(SELECT count(*) FROM tender_awards)"
                        )
                    ).one()
                assert tuple(nulls) == (0, 0, 0, 0)

                command.downgrade(cfg, "0019")
                with engine.connect() as conn:
                    assert tuple(conn.execute(sa.text(_ROW_COUNTS)).one()) == before
                    assert tuple(conn.execute(sa.text(_NEW_STRUCTURE)).one()) == (0, 0, 0)

                command.upgrade(cfg, "0020")
                with engine.connect() as conn:
                    assert tuple(conn.execute(sa.text(_ROW_COUNTS)).one()) == before
                    assert tuple(conn.execute(sa.text(_NEW_STRUCTURE)).one()) == (1, 3, 11)
            finally:
                engine.dispose()

    def test_downgrade_removes_awards_and_keeps_contracts_and_estimates(self):
        with _scratch_alembic("tender award downgrade") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0019")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.begin() as conn:
                    for statement in filter(None, (s.strip() for s in _SEED_BEFORE_0020.split(";\n"))):
                        conn.execute(sa.text(statement))
                command.upgrade(cfg, "0020")
                with engine.begin() as conn:
                    conn.execute(
                        sa.text(
                            "INSERT INTO users (id, email, password_hash, role) "
                            "VALUES (901, 'scratch@example.com', 'x', 'admin')"
                        )
                    )
                    conn.execute(
                        sa.text(
                            "INSERT INTO tender_awards (id, tender_id, object_id, offer_id, "
                            "package_id, contractor_id, estimate_id, kp_inn, awarded_by) "
                            "VALUES (901, 901, 901, 902, 902, 902, 903, '7700000902', 901)"
                        )
                    )
                    conn.execute(
                        sa.text(
                            "INSERT INTO contracts (id, object_id, contractor_id, rate_class_id, "
                            "contract_number, signed_date, tender_award_id) "
                            "VALUES (902, 901, 902, 901, 'SCR-2', '2026-02-01', 901)"
                        )
                    )
                    conn.execute(
                        sa.text(
                            "INSERT INTO estimates (id, contract_id, title, source_award_id) "
                            "VALUES (905, 902, 'copy', 901)"
                        )
                    )
                    conn.execute(
                        sa.text(
                            "INSERT INTO import_jobs (id, contract_id, filename, file_key, "
                            "file_sha256, status, source_award_id) "
                            "VALUES (902, 902, 'f.xlsx', 'scratch-key-2', 'sha', 'done', 901)"
                        )
                    )

                command.downgrade(cfg, "0019")
                with engine.connect() as conn:
                    kept = tuple(
                        conn.execute(
                            sa.text(
                                "SELECT (SELECT count(*) FROM contracts WHERE id = 902), "
                                "(SELECT count(*) FROM estimates WHERE id = 905), "
                                "(SELECT count(*) FROM import_jobs WHERE id = 902)"
                            )
                        ).one()
                    )
                    assert kept == (1, 1, 1)
                    assert tuple(conn.execute(sa.text(_NEW_STRUCTURE)).one()) == (0, 0, 0)
            finally:
                engine.dispose()

    def test_round_trip_on_empty_database(self):
        with _scratch_alembic("tender award empty") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0020")
            command.downgrade(cfg, "-1")
            command.upgrade(cfg, "0020")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.connect() as conn:
                    assert tuple(conn.execute(sa.text(_NEW_STRUCTURE)).one()) == (1, 3, 11)
            finally:
                engine.dispose()
