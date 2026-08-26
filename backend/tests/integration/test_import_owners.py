"""`import_estimate` под владельцем (спека контура §2.4).

Договорной владелец обязан дать ТО ЖЕ, что прежний вызов с `contract=`:
проверяется не «тест зелёный», а конкретные факты — владелец сметы, подрядчик
предложения, сверка шапки, замена пары.
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from models import Estimate, Lot, Proposal
from services.category_resolution import CategoryResolver
from services.estimate_import import compare_header, import_estimate
from services.import_owners import HeaderTruth, contract_estimate_owner
from services.unit_resolution import UnitResolver
from tests.payloads import payload_for, position

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
