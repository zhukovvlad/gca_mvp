"""`import_estimate` под владельцем (спека контура §2.4).

Договорной владелец обязан дать ТО ЖЕ, что прежний вызов с `contract=`:
проверяется не «тест зелёный», а конкретные факты — владелец сметы, подрядчик
предложения, сверка шапки, замена пары.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from models import Estimate, Lot, PositionItem, Proposal
from parser.constants import (
    JSON_KEY_CONTRACTOR_ACCREDITATION,
    JSON_KEY_CONTRACTOR_ADDRESS,
    JSON_KEY_DEVIATION_FROM_CALCULATED_COST,
)
from services.category_resolution import CategoryResolver
from services.estimate_import import compare_header, import_estimate
from services.import_owners import (
    HeaderTruth,
    baseline_estimate_owner,
    contract_estimate_owner,
    offer_estimate_owner,
)
from services.unit_resolution import UnitResolver
from tests.payloads import baseline_proposal_block, estimate_payload, payload_for, position

pytestmark = pytest.mark.integration


def _import(db, owner, data, *, replace=False):
    return import_estimate(
        db,
        owner=owner,
        data=data,
        parser_version="4.0.0",
        import_job_id=None,
        replace=replace,
        unit_resolver=UnitResolver(db),
        category_resolver=CategoryResolver.from_db(db),
    )


def _positions_of(db, estimate_id):
    return db.execute(
        sa.select(PositionItem).join(Proposal).join(Lot).where(Lot.estimate_id == estimate_id)
    ).scalars().all()


class TestContractOwner:
    def test_estimate_belongs_to_the_contract_and_proposal_to_its_contractor(self, db_session, factories):
        contract = factories.ContractFactory.create()
        db_session.flush()
        owner = contract_estimate_owner(contract, None)

        outcome = _import(db_session, owner, payload_for(contract))

        estimate = db_session.get(Estimate, outcome.estimate_id)
        assert (estimate.contract_id, estimate.offer_id, estimate.round_id) == (contract.id, None, None)
        assert estimate.amendment_no is None
        proposal = db_session.execute(
            sa.select(Proposal).join(Lot).where(Lot.estimate_id == estimate.id)
        ).scalar_one()
        assert proposal.contractor_id == contract.contractor_id
        assert proposal.is_baseline is False

    def test_truth_is_the_contract_card(self, factories, db_session):
        contract = factories.ContractFactory.create()
        db_session.flush()
        truth = contract_estimate_owner(contract, None).truth
        assert truth == HeaderTruth(
            object_title=contract.object.title,
            object_address=contract.object.address,
            contractor_title=contract.contractor.title,
            contractor_inn=contract.contractor.inn,
            # Договорная сверка адрес и аккредитацию не проверяет — как сегодня
            # (спека §2.4, ревизия гейта 3).
            contractor_address=None,
            contractor_accreditation=None,
        )

    def test_header_mismatch_names_the_card(self, factories, db_session):
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(contract, tender_object="Другой объект")
        warnings = compare_header(data, contract_estimate_owner(contract, None).truth,
                                  data["lots"]["lot_1"]["proposals"]["contractor_1"])
        assert any("Объект" in w and "Другой объект" in w for w in warnings)

    def test_replace_replaces_the_same_pair(self, db_session, factories):
        contract = factories.ContractFactory.create()
        db_session.flush()
        owner = contract_estimate_owner(contract, None)
        first = _import(db_session, owner, payload_for(contract))
        second = _import(db_session, owner, payload_for(contract, [position(job_title="Другая", unit="м2", unit_cost_total="5")]), replace=True)
        assert second.replaced_estimate_id == first.estimate_id
        assert db_session.get(Estimate, first.estimate_id) is None


class TestOfferOwner:
    def test_estimate_belongs_to_offer_and_proposal_to_package_contractor(self, db_session, factories):
        offer = factories.OfferFactory.create()
        db_session.flush()
        owner = offer_estimate_owner(offer, offer.round.tender, offer.package.contractor)
        data = estimate_payload(
            [position(job_title="Работа", unit="м2", unit_cost_total="10", total_cost_total="10")],
            title=offer.package.contractor.title, inn=offer.package.contractor.inn,
            tender_object=offer.round.tender.object.title,
        )

        outcome = _import(db_session, owner, data)

        estimate = db_session.get(Estimate, outcome.estimate_id)
        assert (estimate.contract_id, estimate.offer_id, estimate.round_id) == (None, offer.id, None)
        proposal_row = db_session.execute(sa.select(Proposal).join(Lot).where(Lot.estimate_id == estimate.id)).scalar_one()
        assert proposal_row.contractor_id == offer.package.contractor_id
        assert proposal_row.is_baseline is False

    def test_deviation_is_read_from_offer_positions(self, db_session, factories):
        offer = factories.OfferFactory.create()
        db_session.flush()
        owner = offer_estimate_owner(offer, offer.round.tender, offer.package.contractor)
        pos = position(job_title="Работа", unit="м2", unit_cost_total="10", total_cost_total="10")
        pos[JSON_KEY_DEVIATION_FROM_CALCULATED_COST] = "-0.05"

        outcome = _import(db_session, owner, estimate_payload([pos]))

        [item] = _positions_of(db_session, outcome.estimate_id)
        assert item.deviation_from_baseline_cost == Decimal("-0.05")

    def test_missing_deviation_key_stays_null(self, db_session, factories):
        offer = factories.OfferFactory.create()
        db_session.flush()
        owner = offer_estimate_owner(offer, offer.round.tender, offer.package.contractor)
        outcome = _import(db_session, owner, estimate_payload(
            [position(job_title="Работа", unit="м2", unit_cost_total="10", total_cost_total="10")]
        ))
        [item] = _positions_of(db_session, outcome.estimate_id)
        assert item.deviation_from_baseline_cost is None

    def test_replace_is_refused_for_round_owners(self, db_session, factories):
        offer = factories.OfferFactory.create()
        db_session.flush()
        owner = offer_estimate_owner(offer, offer.round.tender, offer.package.contractor)
        with pytest.raises(ValueError, match="замена раунда"):
            _import(db_session, owner, estimate_payload(), replace=True)

    @pytest.mark.parametrize(
        ("key", "label"),
        [
            (JSON_KEY_CONTRACTOR_ADDRESS, "Адрес подрядчика"),
            (JSON_KEY_CONTRACTOR_ACCREDITATION, "Аккредитация подрядчика"),
        ],
    )
    def test_offer_truth_checks_address_and_accreditation(self, db_session, factories, key, label):
        """Все четыре поля подрядчика (спека §2.4). У договора эти два поля в
        истину не входят — см. test_truth_is_the_contract_card."""
        offer = factories.OfferFactory.create()
        db_session.flush()
        owner = offer_estimate_owner(offer, offer.round.tender, offer.package.contractor)
        data = estimate_payload(title=offer.package.contractor.title, inn=offer.package.contractor.inn)
        data["lots"]["lot_1"]["proposals"]["contractor_1"][key] = "СОВСЕМ ДРУГОЕ"

        outcome = _import(db_session, owner, data)

        assert any(label in w and "СОВСЕМ ДРУГОЕ" in w for w in outcome.warnings)

    def test_contract_truth_ignores_address_and_accreditation(self, db_session, factories):
        """Контроль: тот же вход у договора предупреждения НЕ даёт — иначе
        DoD 13 нарушен новыми warnings у смет договора."""
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(contract)
        data["lots"]["lot_1"]["proposals"]["contractor_1"][JSON_KEY_CONTRACTOR_ADDRESS] = "СОВСЕМ ДРУГОЕ"

        outcome = _import(db_session, contract_estimate_owner(contract, None), data)

        assert not any("Адрес подрядчика" in w for w in outcome.warnings)


class TestBaselineOwner:
    def _baseline_data(self):
        pos = position(job_title="Работа", unit="м2", unit_cost_total="10", total_cost_total="10")
        pos[JSON_KEY_DEVIATION_FROM_CALCULATED_COST] = "0.10"  # странная раскладка — у базы это NULL
        block = baseline_proposal_block([pos])
        return estimate_payload(lots={
            "lot_1": {
                "lot_title": "Лот №1",
                "proposals": {"contractor_1": block},
                "baseline_proposal": {"title": "Расчетная стоимость отсутствует"},
            }
        })

    def test_estimate_belongs_to_round_and_proposal_has_no_contractor(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        outcome = _import(db_session, baseline_estimate_owner(rnd, rnd.tender), self._baseline_data())

        estimate = db_session.get(Estimate, outcome.estimate_id)
        assert (estimate.contract_id, estimate.offer_id, estimate.round_id) == (None, None, rnd.id)
        proposal_row = db_session.execute(sa.select(Proposal).join(Lot).where(Lot.estimate_id == estimate.id)).scalar_one()
        assert proposal_row.contractor_id is None
        assert proposal_row.is_baseline is True

    def test_baseline_never_writes_deviation(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        outcome = _import(db_session, baseline_estimate_owner(rnd, rnd.tender), self._baseline_data())
        [item] = _positions_of(db_session, outcome.estimate_id)
        assert item.deviation_from_baseline_cost is None

    def test_baseline_skips_the_additional_works_contour(self, db_session, factories, monkeypatch):
        """Контур допработ выключен целиком (спека §2.9): шпион на обоих звеньях.
        Без гейта `decide_owner` упал бы KeyError на вырезанном additional_info."""
        import services.estimate_import as module

        calls: list[str] = []
        monkeypatch.setattr(module, "decide_owner", lambda data: calls.append("decide_owner") or None)
        real_works = module._import_additional_works
        monkeypatch.setattr(module, "_import_additional_works",
                            lambda *a, **kw: calls.append("_import_additional_works") or real_works(*a, **kw))
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()

        _import(db_session, baseline_estimate_owner(rnd, rnd.tender), self._baseline_data())

        assert calls == []

    def test_contract_owner_still_runs_the_contour(self, db_session, factories, monkeypatch):
        """Контроль наблюдаемости: тот же шпион у договора ЛОВИТ вызовы — значит
        пустой список выше означает «не вызвано», а не «нечем смотреть»."""
        import services.estimate_import as module

        calls: list[str] = []
        real_decide = module.decide_owner
        monkeypatch.setattr(module, "decide_owner", lambda data: calls.append("decide_owner") or real_decide(data))
        contract = factories.ContractFactory.create()
        db_session.flush()

        _import(db_session, contract_estimate_owner(contract, None), payload_for(contract))

        assert calls == ["decide_owner"]
