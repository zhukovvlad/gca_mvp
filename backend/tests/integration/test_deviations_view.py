"""Семантика VIEW v_position_deviations (AGENTS.md §4).

Проверяем ровно то, что зафиксировано брифом: дата сравнения и её фолбэк,
выбор норматива по классу ДОГОВОРА, «нет норматива» ≠ 0%, и состав строк
(разделы, строки без цены и не-POSITION в отклонения не попадают).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
import sqlalchemy as sa

from models import CatalogKind

pytestmark = pytest.mark.integration


def _deviation_rows(session, item_id: int) -> list[sa.RowMapping]:
    return list(
        session.execute(
            sa.text("SELECT * FROM v_position_deviations WHERE position_item_id = :id"),
            {"id": item_id},
        ).mappings()
    )


def _priced_item(factories, *, rate_class=None, estimate_date=dt.date(2025, 4, 1), **item_kwargs):
    """Полная цепочка договор → смета → лот → предложение → позиция."""
    contract_kwargs = {"rate_class": rate_class} if rate_class is not None else {}
    contract = factories.ContractFactory.create(**contract_kwargs)
    estimate = factories.EstimateFactory.create(
        contract=contract, data_prepared_on_date=estimate_date
    )
    lot = factories.LotFactory.create(estimate=estimate)
    proposal = factories.ProposalFactory.create(lot=lot, contractor=contract.contractor)
    item_kwargs.setdefault("catalog_position", factories.CatalogPositionFactory.create())
    return factories.PositionItemFactory.create(proposal=proposal, **item_kwargs)


class TestDeviationValue:
    def test_deviation_against_matching_standard(self, db_session, factories):
        item = _priced_item(factories, unit_cost_total=Decimal("120.00"))
        contract = item.proposal.lot.estimate.contract
        factories.RateStandardFactory.create(
            catalog_position=item.catalog_position,
            rate_class=contract.rate_class,
            standard_unit_rate=Decimal("100.00"),
            valid_from=dt.date(2025, 1, 1),
        )
        db_session.flush()

        (row,) = _deviation_rows(db_session, item.id)
        assert row["standard_unit_rate"] == Decimal("100.00")
        assert row["deviation_pct"] == Decimal("20")
        assert row["comparison_date"] == dt.date(2025, 4, 1)

    def test_missing_standard_gives_null_not_zero(self, db_session, factories):
        """«Нет норматива» должно быть отличимо от «0%» (§10)."""
        item = _priced_item(factories, unit_cost_total=Decimal("120.00"))
        db_session.flush()

        (row,) = _deviation_rows(db_session, item.id)
        assert row["rate_standard_id"] is None
        assert row["deviation_pct"] is None

    def test_standard_of_another_class_is_not_applied(self, db_session, factories):
        item = _priced_item(factories, unit_cost_total=Decimal("120.00"))
        factories.RateStandardFactory.create(
            catalog_position=item.catalog_position,
            rate_class=factories.RateClassFactory.create(),
            standard_unit_rate=Decimal("100.00"),
        )
        db_session.flush()

        (row,) = _deviation_rows(db_session, item.id)
        assert row["deviation_pct"] is None

    def test_weight_prefers_suggested_quantity(self, db_session, factories):
        """w = COALESCE(suggested_quantity, quantity) — §6, подтверждено фазой 0."""
        item = _priced_item(
            factories, quantity=Decimal("1"), suggested_quantity=Decimal("12.5")
        )
        db_session.flush()
        (row,) = _deviation_rows(db_session, item.id)
        assert row["weight"] == Decimal("12.5")

        fallback = _priced_item(factories, quantity=Decimal("7"), suggested_quantity=None)
        db_session.flush()
        (row,) = _deviation_rows(db_session, fallback.id)
        assert row["weight"] == Decimal("7")


class TestComparisonDate:
    def test_falls_back_to_contract_signed_date(self, db_session, factories):
        item = _priced_item(factories, estimate_date=None, unit_cost_total=Decimal("110.00"))
        contract = item.proposal.lot.estimate.contract
        factories.RateStandardFactory.create(
            catalog_position=item.catalog_position,
            rate_class=contract.rate_class,
            standard_unit_rate=Decimal("100.00"),
            valid_from=dt.date(2025, 1, 1),
        )
        db_session.flush()

        (row,) = _deviation_rows(db_session, item.id)
        assert row["comparison_date"] == contract.signed_date
        assert row["deviation_pct"] == Decimal("10")

    def test_standard_outside_the_period_is_not_applied(self, db_session, factories):
        item = _priced_item(
            factories, estimate_date=dt.date(2025, 4, 1), unit_cost_total=Decimal("120.00")
        )
        contract = item.proposal.lot.estimate.contract
        factories.RateStandardFactory.create(
            catalog_position=item.catalog_position,
            rate_class=contract.rate_class,
            valid_from=dt.date(2025, 5, 1),   # начинается позже даты сметы
            valid_to=None,
        )
        db_session.flush()

        (row,) = _deviation_rows(db_session, item.id)
        assert row["deviation_pct"] is None

    def test_reapproval_does_not_change_old_estimates(self, db_session, factories):
        """Переутверждение норматива не меняет отклонения прошлых смет (§10)."""
        rate_class = factories.RateClassFactory.create()
        position = factories.CatalogPositionFactory.create()
        old_item = _priced_item(
            factories,
            rate_class=rate_class,
            estimate_date=dt.date(2025, 4, 1),
            catalog_position=position,
            unit_cost_total=Decimal("120.00"),
        )
        new_item = _priced_item(
            factories,
            rate_class=rate_class,
            estimate_date=dt.date(2026, 4, 1),
            catalog_position=position,
            unit_cost_total=Decimal("120.00"),
        )
        factories.RateStandardFactory.create(
            catalog_position=position,
            rate_class=rate_class,
            standard_unit_rate=Decimal("100.00"),
            valid_from=dt.date(2025, 1, 1),
            valid_to=dt.date(2026, 1, 1),
        )
        factories.RateStandardFactory.create(
            catalog_position=position,
            rate_class=rate_class,
            standard_unit_rate=Decimal("150.00"),   # старая × индекс инфляции
            valid_from=dt.date(2026, 1, 1),
            valid_to=None,
            inflation_index=Decimal("1.5"),
        )
        db_session.flush()

        (old_row,) = _deviation_rows(db_session, old_item.id)
        (new_row,) = _deviation_rows(db_session, new_item.id)
        assert old_row["deviation_pct"] == Decimal("20")
        assert new_row["deviation_pct"] == Decimal("-20")


class TestExcludedRows:
    def test_chapter_rows_excluded(self, db_session, factories):
        item = _priced_item(factories, is_chapter=True)
        db_session.flush()
        assert _deviation_rows(db_session, item.id) == []

    def test_rows_without_price_excluded(self, db_session, factories):
        """Пустая стоимость — NULL, не 0 (§3); сравнивать нечего."""
        item = _priced_item(factories, unit_cost_total=None)
        db_session.flush()
        assert _deviation_rows(db_session, item.id) == []

    def test_unmatched_rows_excluded(self, db_session, factories):
        item = _priced_item(factories, catalog_position=None)
        db_session.flush()
        assert _deviation_rows(db_session, item.id) == []

    @pytest.mark.parametrize(
        "kind", [CatalogKind.HEADER, CatalogKind.LOT_HEADER, CatalogKind.TRASH, CatalogKind.TO_REVIEW]
    )
    def test_non_position_catalog_rows_excluded(self, db_session, factories, kind):
        item = _priced_item(
            factories,
            catalog_position=factories.CatalogPositionFactory.create(kind=kind.value),
        )
        db_session.flush()
        assert _deviation_rows(db_session, item.id) == []
