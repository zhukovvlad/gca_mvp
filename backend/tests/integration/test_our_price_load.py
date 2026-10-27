"""Загрузчики экрана «КП против нашей цены» — `crud/our_price_load.py`
(спека 4а §2.3, §2.4, §2.8, §2.17, §2.18).

Сцены строятся фабриками, таблицы схемы параметров и варианты пишутся
литералами; все названия и числа выдуманы. Каждое правило имеет обе стороны:
вход, где оно срабатывает, и соседний вход, нарушающий ровно одно условие.
"""

from __future__ import annotations

import datetime as dt
import threading
from contextlib import contextmanager
from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud import our_price_load as load
from crud.our_price_load import (
    ArticleMeta,
    category_roots,
    category_rows,
    kp_vat_bases,
    load_history,
    load_kp_lines,
)
from crud.stage_summary import _rates_by_estimate
from models import (
    CatalogContext,
    ContextMember,
    ContextParameterValue,
    FamilyParameter,
    FamilyParameterSchema,
    PositionItem,
    UnitOfMeasure,
    ValueOrigin,
    WorkCategory,
)
from money.price import is_price, is_weight
from services.context_routing import route_position
from services.our_price import HistoryContract, LineKey
from services.our_price_money import CategoryRow
from services.work_families import assign_family
from services.work_variants import get_or_create_value, get_or_create_variant
from tests.integration.test_semantic_queue_api import _active_family, _unit_id

pytestmark = pytest.mark.integration

#: Оракул правил «конечно и больше нуля» — свой литерал, по восьми входам.
TRUTH_TABLE = (
    ("пусто", None, False),
    ("ноль", Decimal("0"), False),
    ("отрицательное", Decimal("-1"), False),
    ("NaN", Decimal("NaN"), False),
    ("+Infinity", Decimal("Infinity"), False),
    ("-Infinity", Decimal("-Infinity"), False),
    ("копеечное", Decimal("0.0000001"), True),
    ("целое", Decimal("100"), True),
)


# ---------------------------------------------------------------------------
#  Предикаты цены и веса против независимого оракула
# ---------------------------------------------------------------------------


def _item_with(factories, *, unit_cost_total=Decimal("1"), suggested_quantity=Decimal("1")):
    estimate = factories.EstimateFactory.create()
    lot = factories.LotFactory.create(estimate=estimate)
    proposal = factories.ProposalFactory.create(lot=lot)
    return factories.PositionItemFactory.create(
        proposal=proposal,
        unit_cost_total=unit_cost_total,
        quantity=None,
        suggested_quantity=suggested_quantity,
    )


def _predicate_rows(db, ids, column, predicate):
    return {
        row.id: (row.value, row.ok)
        for row in db.execute(
            sa.select(PositionItem.id, column.label("value"), predicate(column).label("ok")).where(
                PositionItem.id.in_(ids)
            )
        ).all()
    }


def _same(stored, expected):
    if expected is None or stored is None:
        return stored is expected
    if expected.is_nan():
        return stored.is_nan()
    return stored == expected


class TestPricePredicatesAgreeWithTheOracle:
    def test_price_rule_matches_the_oracle_on_every_input(self, db_session, factories):
        by_id = {
            _item_with(factories, unit_cost_total=value).id: (name, value, expected)
            for name, value, expected in TRUTH_TABLE
        }
        db_session.flush()

        rows = _predicate_rows(db_session, list(by_id), PositionItem.unit_cost_total, load._price_ok)

        assert len(rows) == len(TRUTH_TABLE) == 8
        for position_id, (stored, ok) in rows.items():
            name, value, expected = by_id[position_id]
            assert _same(stored, value), f"доставка входа «{name}»"
            assert ok in (True, False), f"вход «{name}»: ответ не булев ({ok!r})"
            assert ok is expected, f"SQL, вход «{name}»"
            assert is_price(value) is expected, f"Python, вход «{name}»"

    def test_weight_predicate_matches_the_oracle_on_every_input(self, db_session, factories):
        by_id = {
            _item_with(factories, suggested_quantity=value).id: (name, value, expected)
            for name, value, expected in TRUTH_TABLE
        }
        db_session.flush()

        rows = _predicate_rows(db_session, list(by_id), PositionItem.suggested_quantity, load._weight_ok)

        assert len(rows) == 8
        for position_id, (stored, ok) in rows.items():
            name, value, expected = by_id[position_id]
            assert _same(stored, value), f"доставка входа «{name}»"
            assert ok in (True, False), f"вход «{name}»: ответ не булев ({ok!r})"
            assert ok is expected, f"SQL, вход «{name}»"
            assert is_weight(value) is expected, f"Python, вход «{name}»"

    def test_naive_greater_than_zero_lets_nan_through(self, db_session, factories):
        """Свидетель, зачем предикат не равен голому `> 0`: `'NaN'` ему проходит."""
        item = _item_with(factories, unit_cost_total=Decimal("NaN"))
        db_session.flush()

        naive = db_session.execute(
            sa.select(PositionItem.unit_cost_total > 0).where(PositionItem.id == item.id)
        ).scalar_one()
        ours = db_session.execute(
            sa.select(load._price_ok(PositionItem.unit_cost_total)).where(PositionItem.id == item.id)
        ).scalar_one()

        assert naive is True
        assert ours is False


# ---------------------------------------------------------------------------
#  Сцена: семьи, схемы, варианты, строки, договоры
# ---------------------------------------------------------------------------

D = Decimal
NOW = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


class World:
    """Помощники сцены: всё через ORM-литералы, названия и числа выдуманы."""

    def __init__(self, db, factories, admin):
        self.db = db
        self.f = factories
        self.admin = admin
        self.unit_id = _unit_id(db, "M2")
        self._n = 0

    # -- семьи и схемы ------------------------------------------------------

    def family(self, title="Семья А"):
        family = _active_family(self.db, title=title, unit_name="M2", actor_id=self.admin.id)
        schema = self.db.execute(
            sa.select(FamilyParameterSchema).where(FamilyParameterSchema.family_id == family.id)
        ).scalar_one()
        return family, schema

    def param(self, schema, ordinal, name, *, role="main", archived=False):
        row = FamilyParameter(
            schema_id=schema.id,
            ordinal=ordinal,
            name=name,
            name_norm=name.lower(),
            role=role,
            archived_at=NOW if archived else None,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def value(self, parameter, text):
        value_id, _ = get_or_create_value(self.db, parameter_id=parameter.id, text=text, origin=ValueOrigin.schema)
        return value_id

    def variant(self, family, schema, by_ordinal):
        variant, _ = get_or_create_variant(
            self.db, family_id=family.id, schema_id=schema.id, value_ids_by_ordinal=by_ordinal
        )
        return variant

    def supersede(self, family, schema):
        """Схема уходит в `superseded`, на её место встаёт новая `frozen` версия."""
        self.db.execute(
            sa.update(FamilyParameterSchema)
            .where(FamilyParameterSchema.id == schema.id)
            .values(status="superseded", superseded_at=NOW)
        )
        new = FamilyParameterSchema(
            family_id=family.id,
            version=schema.version + 1,
            status="frozen",
            origin="model",
            frozen_at=NOW,
        )
        self.db.add(new)
        self.db.flush()
        return new

    # -- договоры и сметы ---------------------------------------------------

    def contract(self, *, object_=None, area=D("1000"), year=2025, rate_class=None, contractor=None):
        self._n += 1
        obj = object_ or self.f.ObjectFactory.create(
            area_aboveground_sp=area, area_underground_sp=D("0") if area is not None else None
        )
        return self.f.ContractFactory.create(
            object=obj,
            signed_date=dt.date(year, 6, 1),
            **({"rate_class": rate_class} if rate_class else {}),
            **({"contractor": contractor} if contractor else {}),
        )

    def estimate(self, contract, *, amendment_no=None, override=None, vat_rate=D("20")):
        estimate = self.f.EstimateFactory.create(
            contract=contract, amendment_no=amendment_no, vat_rate_base_override=override
        )
        lot = self.f.LotFactory.create(estimate=estimate)
        proposal = self.f.ProposalFactory.create(lot=lot, contractor=contract.contractor, vat_rate=vat_rate)
        return estimate, proposal

    def offer_estimate(self, *, vat_rate=D("20")):
        """Смета оферты тендера: владелец — предложение, `contract_id IS NULL`."""
        rnd = self.f.TenderRoundFactory.create()
        offer = self.f.OfferFactory.create(round=rnd)
        estimate = self.f.EstimateFactory.create(contract=None, offer=offer)
        lot = self.f.LotFactory.create(estimate=estimate)
        proposal = self.f.ProposalFactory.create(lot=lot, vat_rate=vat_rate)
        return estimate, proposal

    # -- строки -------------------------------------------------------------

    def line(
        self,
        proposal,
        *,
        title=None,
        family=None,
        variant=None,
        kind=None,
        archived_context=False,
        unit_cost=D("100"),
        total=D("1000"),
        suggested=D("10"),
        quantity=D("10"),
        chapter=None,
        number=None,
    ):
        """Строка с каталожной строкой и членством; семья и вариант — по желанию."""
        self._n += 1
        title = title or f"Работа {self._n}"
        cp = self.f.CatalogPositionFactory.create(unit_id=self.unit_id, standard_job_title=title)
        item = self.f.PositionItemFactory.create(
            proposal=proposal,
            is_chapter=False,
            job_title_in_proposal=title,
            catalog_position_id=cp.id,
            unit_id=self.unit_id,
            unit_cost_total=unit_cost,
            total_cost_total=total,
            suggested_quantity=suggested,
            quantity=quantity,
            chapter_item_id=chapter.id if chapter is not None else None,
            item_number_in_proposal=number,
        )
        context_id = route_position(self.db, position_item_id=item.id).context_id
        if family is not None:
            assign_family(self.db, context_id=context_id, family_id=family.id, actor_id=self.admin.id)
        values = {}
        if variant is not None:
            values.update(work_variant_id=variant.id, variant_at=NOW, variant_paths_hash="h")
        if kind is not None:
            values["semantic_kind"] = kind
        if archived_context:
            values["archived_at"] = NOW
        if values:
            self.db.execute(sa.update(CatalogContext).where(CatalogContext.id == context_id).values(**values))
        self.db.flush()
        item.context_id = context_id
        return item

    def chapter(self, proposal, category_id, *, title="Раздел"):
        self._n += 1
        return self.f.PositionItemFactory.create(
            proposal=proposal,
            is_chapter=True,
            job_title_in_proposal=f"{title} {self._n}",
            unit_cost_total=None,
            total_cost_total=None,
            quantity=None,
            suggested_quantity=None,
            work_category_id=category_id,
            category_source="manual" if category_id is not None else None,
        )


@pytest.fixture
def world(db_session, factories, admin_user):
    return World(db_session, factories, admin_user)


@contextmanager
def count_statements(db):
    """Число SQL-операторов сессии внутри блока (`before_cursor_execute`); точки
    сохранения тестовой обёртки не считаются: это не чтение данных."""
    box = [0]

    def _count(_conn, _cursor, statement, *_args, **_kwargs):
        if not statement.startswith(("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")):
            box[0] += 1

    engine = db.get_bind()
    sa.event.listen(engine, "before_cursor_execute", _count)
    try:
        yield box
    finally:
        sa.event.remove(engine, "before_cursor_execute", _count)


def _simple_variant(world, family, schema, *, text="50 мм"):
    """Схема с одним главным параметром и вариантом по тексту значения."""
    param = world.param(schema, 1, "Толщина")
    return param, world.variant(family, schema, {1: world.value(param, text)})


# ---------------------------------------------------------------------------
#  История
# ---------------------------------------------------------------------------


def _history_of(db, contract):
    contracts, lines = load_history(db, exclude_object_id=-1)
    return (
        [c for c in contracts if c.contract_id == contract.id],
        [ln for ln in lines if ln.contract_id == contract.id],
    )


class TestHistoryComposition:
    def test_contract_header_carries_the_contract_class_object_area_and_year(self, world, db_session):
        rate_class = world.f.RateClassFactory.create(title="Класс Х")
        # площадь объекта — полная (надземная + подземная), а не одно слагаемое
        obj = world.f.ObjectFactory.create(area_aboveground_sp=D("2000"), area_underground_sp=D("500"))
        contract = world.contract(object_=obj, year=2024, rate_class=rate_class)
        estimate, _ = world.estimate(contract)
        db_session.flush()

        contracts, _ = _history_of(db_session, contract)

        assert contracts == [
            HistoryContract(
                contract_id=contract.id,
                estimate_id=estimate.id,
                number=contract.contract_number,
                object_title=contract.object.title,
                year=2024,
                rate_class_id=rate_class.id,
                rate_class_title="Класс Х",
                contractor_id=contract.contractor_id,
                contractor_title=contract.contractor.title,
                area=D("2500"),
            )
        ]

    def test_object_without_area_gives_none(self, world, db_session):
        contract = world.contract(area=None)
        world.estimate(contract)
        db_session.flush()

        contracts, _ = _history_of(db_session, contract)

        assert [c.area for c in contracts] == [None]

    def test_contract_class_is_the_contract_snapshot_not_the_object_class(self, world, db_session):
        object_class = world.f.RateClassFactory.create(title="Класс объекта")
        snapshot = world.f.RateClassFactory.create(title="Класс договора")
        obj = world.f.ObjectFactory.create(rate_class_id=object_class.id)
        contract = world.contract(object_=obj, rate_class=snapshot)
        world.estimate(contract)
        db_session.flush()

        contracts, _ = _history_of(db_session, contract)

        assert [(c.rate_class_id, c.rate_class_title) for c in contracts] == [(snapshot.id, "Класс договора")]

    def test_only_the_latest_estimate_of_a_contract_is_read(self, world, db_session):
        """Допсоглашение: строки исходной сметы в ставку не входят."""
        contract = world.contract()
        _, original_proposal = world.estimate(contract, amendment_no=None)
        amended, amended_proposal = world.estimate(contract, amendment_no=1)
        family, schema = world.family()
        _, variant = _simple_variant(world, family, schema)
        world.line(original_proposal, family=family, variant=variant, unit_cost=D("111"))
        world.line(amended_proposal, family=family, variant=variant, unit_cost=D("222"))
        db_session.flush()

        contracts, lines = _history_of(db_session, contract)

        assert [c.estimate_id for c in contracts] == [amended.id]
        assert [ln.unit_price for ln in lines] == [D("222")]

    def test_the_higher_amendment_wins_over_a_lower_one(self, world, db_session):
        contract = world.contract()
        world.estimate(contract, amendment_no=1)
        top, _ = world.estimate(contract, amendment_no=2)
        db_session.flush()

        contracts, _ = _history_of(db_session, contract)

        assert [c.estimate_id for c in contracts] == [top.id]

    def test_a_new_amendment_between_the_two_reads_does_not_mix_versions(self, world, db_session):
        """Договор и его строки читаются двумя запросами: допсоглашение, вставшее между
        ними, не должно дать строки новой сметы под метаданными старой. Допсоглашение
        «появляется» между запросами сменой номера — на том же соединении, в момент
        второго запроса (до его исполнения)."""
        contract = world.contract()
        current, current_proposal = world.estimate(contract, amendment_no=2, vat_rate=D("0"))
        coming, coming_proposal = world.estimate(contract, amendment_no=1, vat_rate=D("20"))
        family, schema = world.family()
        _, variant = _simple_variant(world, family, schema)
        world.line(current_proposal, family=family, variant=variant, unit_cost=D("111"))
        world.line(coming_proposal, family=family, variant=variant, unit_cost=D("222"))
        db_session.flush()
        appeared = [False]

        def _amend(conn, _cursor, statement, _params, _context, _executemany):
            if appeared[0] or "position_items" not in statement:
                return
            appeared[0] = True
            conn.exec_driver_sql("UPDATE estimates SET amendment_no = 3 WHERE id = %s", (coming.id,))

        engine = db_session.get_bind()
        sa.event.listen(engine, "before_cursor_execute", _amend)
        try:
            contracts, lines = _history_of(db_session, contract)
        finally:
            sa.event.remove(engine, "before_cursor_execute", _amend)

        assert appeared[0]
        assert [c.estimate_id for c in contracts] == [current.id]
        assert [(ln.unit_price, ln.vat_base) for ln in lines] == [(D("111"), D("0"))]

    def test_offer_estimate_without_a_contract_is_not_history(self, world, db_session):
        estimate, proposal = world.offer_estimate()
        family, schema = world.family()
        _, variant = _simple_variant(world, family, schema)
        world.line(proposal, family=family, variant=variant)
        db_session.flush()

        contracts, lines = load_history(db_session, exclude_object_id=-1)

        assert estimate.id not in {c.estimate_id for c in contracts}
        assert lines == []

    def test_contract_of_the_tender_object_is_excluded_and_a_neighbour_is_not(self, world, db_session):
        own = world.contract()
        other = world.contract()
        world.estimate(own)
        world.estimate(other)
        db_session.flush()

        contracts, _ = load_history(db_session, exclude_object_id=own.object_id)

        ids = {c.contract_id for c in contracts}
        assert own.id not in ids
        assert other.id in ids

    def test_lines_of_the_tender_object_contract_are_excluded_too(self, world, db_session):
        own = world.contract()
        _, proposal = world.estimate(own)
        family, schema = world.family()
        _, variant = _simple_variant(world, family, schema)
        world.line(proposal, family=family, variant=variant)
        db_session.flush()

        _, lines = load_history(db_session, exclude_object_id=own.object_id)
        _, lines_other_object = load_history(db_session, exclude_object_id=-1)

        assert lines == []
        assert len(lines_other_object) == 1

    def test_signing_date_does_not_limit_the_history(self, world, db_session):
        future = world.contract(year=2090)
        world.estimate(future)
        db_session.flush()

        contracts, _ = _history_of(db_session, future)

        assert [c.year for c in contracts] == [2090]


class TestHistoryLineMembership:
    def _contract_with_family(self, world, db_session):
        contract = world.contract()
        _, proposal = world.estimate(contract)
        family, schema = world.family()
        _, variant = _simple_variant(world, family, schema)
        return contract, proposal, family, variant

    def test_line_with_a_variant_in_a_live_context_goes_in(self, world, db_session):
        contract, proposal, family, variant = self._contract_with_family(world, db_session)
        world.line(proposal, family=family, variant=variant)
        db_session.flush()

        _, lines = _history_of(db_session, contract)

        assert [ln.key.variant_id for ln in lines] == [variant.id]
        assert [ln.key.family_id for ln in lines] == [family.id]

    def test_line_whose_context_has_no_variant_stays_out(self, world, db_session):
        contract, proposal, family, variant = self._contract_with_family(world, db_session)
        world.line(proposal, family=family, variant=None)
        db_session.flush()

        _, lines = _history_of(db_session, contract)

        assert lines == []

    def test_line_of_an_archived_context_stays_out(self, world, db_session):
        contract, proposal, family, variant = self._contract_with_family(world, db_session)
        world.line(proposal, family=family, variant=variant, archived_context=True)
        db_session.flush()

        _, lines = _history_of(db_session, contract)

        assert lines == []

    def test_line_without_membership_stays_out(self, world, db_session):
        contract, proposal, family, variant = self._contract_with_family(world, db_session)
        world.f.PositionItemFactory.create(proposal=proposal, is_chapter=False)
        db_session.flush()

        _, lines = _history_of(db_session, contract)

        assert lines == []

    def test_chapter_rows_never_enter(self, world, db_session):
        contract, proposal, family, variant = self._contract_with_family(world, db_session)
        chapter = world.chapter(proposal, None)
        # раздел с пригодными ценой, суммой и весом: отсечь его может только
        # признак раздела, а не предикаты цены и веса
        chapter.unit_cost_total = D("100")
        chapter.total_cost_total = D("1000")
        chapter.suggested_quantity = D("10")
        # раздел с членством в контексте, несущем вариант, — всё равно не строка
        item = world.line(proposal, family=family, variant=variant)
        member = db_session.get(ContextMember, item.id)
        db_session.add(
            ContextMember(
                position_item_id=chapter.id,
                context_id=member.context_id,
                bucket_id=member.bucket_id,
                membership_state="CURRENT",
                routed_by="default",
            )
        )
        db_session.flush()

        _, lines = _history_of(db_session, contract)

        assert len(lines) == 1, "в истории только обычная строка, не раздел"

    @pytest.mark.parametrize(("name", "value", "expected"), TRUTH_TABLE)
    def test_work_line_price_passes_the_predicate_in_sql(self, world, db_session, name, value, expected):
        contract, proposal, family, variant = self._contract_with_family(world, db_session)
        world.line(proposal, family=family, variant=variant, unit_cost=value)
        db_session.flush()

        _, lines = _history_of(db_session, contract)

        assert (len(lines) == 1) is expected, name

    @pytest.mark.parametrize(("name", "value", "expected"), TRUTH_TABLE)
    def test_work_line_weight_passes_the_predicate_in_sql(self, world, db_session, name, value, expected):
        contract, proposal, family, variant = self._contract_with_family(world, db_session)
        world.line(proposal, family=family, variant=variant, suggested=value, quantity=None)
        db_session.flush()

        _, lines = _history_of(db_session, contract)

        assert (len(lines) == 1) is expected, name

    def test_empty_suggested_quantity_falls_back_to_the_customer_quantity(self, world, db_session):
        contract, proposal, family, variant = self._contract_with_family(world, db_session)
        world.line(proposal, family=family, variant=variant, suggested=None, quantity=D("3"))
        db_session.flush()

        _, lines = _history_of(db_session, contract)

        assert [ln.weight for ln in lines] == [D("3")]

    def test_suggested_quantity_wins_over_the_customer_quantity(self, world, db_session):
        contract, proposal, family, variant = self._contract_with_family(world, db_session)
        world.line(proposal, family=family, variant=variant, suggested=D("5"), quantity=D("3"))
        db_session.flush()

        _, lines = _history_of(db_session, contract)

        assert [ln.weight for ln in lines] == [D("5")]

    @pytest.mark.parametrize(("name", "value", "expected"), TRUTH_TABLE)
    def test_system_line_is_judged_by_its_total_only(self, world, db_session, name, value, expected):
        """У системы предикат на сумме строки; цена и вес ей не нужны."""
        contract, proposal, family, variant = self._contract_with_family(world, db_session)
        world.line(
            proposal,
            family=family,
            variant=variant,
            kind="SYSTEM",
            unit_cost=None,
            suggested=None,
            quantity=None,
            total=value,
        )
        db_session.flush()

        _, lines = _history_of(db_session, contract)

        assert (len(lines) == 1) is expected, name

    def test_work_line_is_not_judged_by_its_total(self, world, db_session):
        contract, proposal, family, variant = self._contract_with_family(world, db_session)
        world.line(proposal, family=family, variant=variant, total=None)
        db_session.flush()

        _, lines = _history_of(db_session, contract)

        assert len(lines) == 1

    @pytest.mark.parametrize(("kind", "is_system"), [("SYSTEM", True), ("WORK", False), ("UNKNOWN", False)])
    def test_only_the_system_kind_marks_a_system_line(self, world, db_session, kind, is_system):
        contract, proposal, family, variant = self._contract_with_family(world, db_session)
        world.line(proposal, family=family, variant=variant, kind=kind)
        db_session.flush()

        _, lines = _history_of(db_session, contract)

        assert [ln.is_system for ln in lines] == [is_system]

    def test_line_carries_the_gross_numbers_of_the_row(self, world, db_session):
        contract, proposal, family, variant = self._contract_with_family(world, db_session)
        world.line(
            proposal,
            family=family,
            variant=variant,
            unit_cost=D("12.5"),
            total=D("125"),
            suggested=D("10"),
        )
        db_session.flush()

        _, lines = _history_of(db_session, contract)

        assert [(ln.unit_price, ln.total, ln.weight) for ln in lines] == [(D("12.5"), D("125"), D("10"))]


class TestHistoryVatBase:
    def _setup(self, world, *, override, vat_rate):
        contract = world.contract()
        _, proposal = world.estimate(contract, override=override, vat_rate=vat_rate)
        family, schema = world.family()
        _, variant = _simple_variant(world, family, schema)
        world.line(proposal, family=family, variant=variant)
        world.db.flush()
        return contract

    def test_override_wins_over_the_proposal_rate(self, world, db_session):
        contract = self._setup(world, override=D("12"), vat_rate=D("20"))

        _, lines = _history_of(db_session, contract)

        assert [ln.vat_base for ln in lines] == [D("12")]

    def test_zero_override_is_a_base_not_an_absence(self, world, db_session):
        contract = self._setup(world, override=D("0"), vat_rate=D("20"))

        _, lines = _history_of(db_session, contract)

        assert [ln.vat_base for ln in lines] == [D("0")]

    def test_without_override_the_proposal_rate_is_the_base(self, world, db_session):
        contract = self._setup(world, override=None, vat_rate=D("22"))

        _, lines = _history_of(db_session, contract)

        assert [ln.vat_base for ln in lines] == [D("22")]

    def test_unknown_base_keeps_the_line_with_none(self, world, db_session):
        contract = self._setup(world, override=None, vat_rate=None)

        _, lines = _history_of(db_session, contract)

        assert [ln.vat_base for ln in lines] == [None]


class TestLineKeyFirstMain:
    def _two_mains(self, world):
        family, schema = world.family()
        first = world.param(schema, 1, "Толщина")
        second = world.param(schema, 2, "Материал")
        return family, schema, first, second

    def _first_main(self, world, db_session, variant, family):
        contract = world.contract()
        _, proposal = world.estimate(contract)
        world.line(proposal, family=family, variant=variant)
        db_session.flush()
        _, lines = _history_of(db_session, contract)
        assert len(lines) == 1
        return lines[0].key

    def test_first_main_is_the_value_of_the_lowest_ordinal_main_parameter(self, world, db_session):
        family, schema, first, second = self._two_mains(world)
        thick = world.value(first, "50 мм")
        variant = world.variant(family, schema, {1: thick, 2: world.value(second, "бетон")})

        key = self._first_main(world, db_session, variant, family)

        assert key == LineKey(family.id, variant.id, (first.id, thick))

    def test_unspecified_first_value_is_a_pair_with_none(self, world, db_session):
        family, schema, first, second = self._two_mains(world)
        variant = world.variant(family, schema, {1: None, 2: world.value(second, "бетон")})

        key = self._first_main(world, db_session, variant, family)

        assert key.first_main == (first.id, None)

    def test_a_single_main_parameter_gives_no_first_main(self, world, db_session):
        family, schema = world.family()
        only = world.param(schema, 1, "Толщина")
        variant = world.variant(family, schema, {1: world.value(only, "50 мм")})

        key = self._first_main(world, db_session, variant, family)

        assert key.first_main is None

    def test_secondary_and_archived_parameters_do_not_count_as_main(self, world, db_session):
        family, schema = world.family()
        main = world.param(schema, 1, "Толщина")
        world.param(schema, 2, "Цвет", role="secondary")
        world.param(schema, 3, "Марка", archived=True)
        variant = world.variant(family, schema, {1: world.value(main, "50 мм")})

        key = self._first_main(world, db_session, variant, family)

        assert key.first_main is None

    def test_the_lowest_ordinal_wins_over_the_creation_order(self, world, db_session):
        family, schema = world.family()
        late = world.param(schema, 7, "Материал")
        early = world.param(schema, 3, "Толщина")
        world.param(schema, 1, "Старый", archived=True)
        early_value = world.value(early, "50 мм")
        variant = world.variant(family, schema, {3: early_value, 7: world.value(late, "бетон")})

        key = self._first_main(world, db_session, variant, family)

        assert key.first_main == (early.id, early_value)

    def test_variant_on_a_superseded_schema_has_no_first_main(self, world, db_session):
        family, schema, first, second = self._two_mains(world)
        variant = world.variant(family, schema, {1: world.value(first, "50 мм"), 2: world.value(second, "бетон")})
        world.supersede(family, schema)

        key = self._first_main(world, db_session, variant, family)

        assert key == LineKey(family.id, variant.id, None)

    def test_the_same_variant_has_a_first_main_while_its_schema_is_current(self, world, db_session):
        """Парный вход к замещённой версии: тот же вариант на действующей версии."""
        family, schema, first, second = self._two_mains(world)
        variant = world.variant(family, schema, {1: world.value(first, "50 мм"), 2: world.value(second, "бетон")})

        key = self._first_main(world, db_session, variant, family)

        assert key.first_main is not None

    def test_a_building_next_version_does_not_displace_the_frozen_one(self, world, db_session):
        """Действующая версия — `frozen`, а не самая новая: пересборка схемы
        (`building`, версия выше) не отнимает уровень `p` у вариантов текущей."""
        family, schema, first, second = self._two_mains(world)
        thick = world.value(first, "50 мм")
        variant = world.variant(family, schema, {1: thick, 2: world.value(second, "бетон")})
        db_session.add(
            FamilyParameterSchema(family_id=family.id, version=schema.version + 1, status="building", origin="model")
        )
        db_session.flush()

        key = self._first_main(world, db_session, variant, family)

        assert key == LineKey(family.id, variant.id, (first.id, thick))


# ---------------------------------------------------------------------------
#  Строки КП с семантикой
# ---------------------------------------------------------------------------


def _categories(db):
    """Свои статьи: корень R, его потомок C и внук G; отдельный корень R2.
    Коды и порядок взяты выше всего, что уже есть в справочнике."""
    top = db.execute(sa.select(sa.func.max(WorkCategory.sort_order))).scalar_one() or 0
    made = {}
    for offset, (name, code, parent) in enumerate(
        (("R", "8801", None), ("C", "8801.1", "R"), ("G", "8801.1.1", "C"), ("R2", "8802", None)),
        start=1,
    ):
        row = WorkCategory(
            code=code,
            title=f"Статья {name}",
            parent_id=made[parent].id if parent else None,
            sort_order=top + offset,
        )
        db.add(row)
        db.flush()
        made[name] = row
    return made


def _kp(db, estimate):
    return load_kp_lines(db, [estimate.id])[estimate.id]


def _kp_scene(world, *, vat_rate=D("20"), family_title="Семья А"):
    """Смета КП (договор здесь только носитель сметы) и семья со схемой из
    двух главных параметров."""
    contract = world.contract()
    estimate, proposal = world.estimate(contract, vat_rate=vat_rate)
    family, schema = world.family(family_title)
    first = world.param(schema, 1, "Толщина")
    second = world.param(schema, 2, "Материал")
    return estimate, proposal, family, schema, first, second


class TestKpLines:
    def test_every_row_comes_back_even_with_an_unfit_price(self, world, db_session):
        """Предикаты пригодности ядру: загрузчик строки КП не отбрасывает."""
        estimate, proposal, *_ = _kp_scene(world)
        world.line(proposal, unit_cost=D("NaN"), total=D("NaN"))
        world.line(proposal, unit_cost=None, total=None, suggested=None, quantity=None)
        world.line(proposal, unit_cost=D("0"), total=D("0"), suggested=D("0"))
        db_session.flush()

        rows = _kp(db_session, estimate)

        assert len(rows) == 3

    def test_chapter_rows_are_not_lines(self, world, db_session):
        estimate, proposal, *_ = _kp_scene(world)
        world.chapter(proposal, None)
        world.line(proposal)
        db_session.flush()

        assert len(_kp(db_session, estimate)) == 1

    def test_estimates_without_lines_still_get_a_key(self, world, db_session):
        estimate, proposal, *_ = _kp_scene(world)
        empty, _ = world.estimate(world.contract())
        world.line(proposal)
        db_session.flush()

        result = load_kp_lines(db_session, [estimate.id, empty.id])

        assert len(result[estimate.id]) == 1
        assert result[empty.id] == []

    def test_no_estimates_means_no_lines(self, db_session):
        assert load_kp_lines(db_session, []) == {}

    def test_lines_are_grouped_by_their_own_estimate(self, world, db_session):
        first, first_proposal, *_ = _kp_scene(world)
        second, second_proposal, *_ = _kp_scene(world, family_title="Семья Б")
        a = world.line(first_proposal)
        b = world.line(second_proposal)
        db_session.flush()

        result = load_kp_lines(db_session, [first.id, second.id])

        assert [r.line.position_item_id for r in result[first.id]] == [a.id]
        assert [r.line.position_item_id for r in result[second.id]] == [b.id]

    def test_row_carries_title_unit_quantity_and_gross_numbers(self, world, db_session):
        estimate, proposal, *_ = _kp_scene(world)
        item = world.line(
            proposal,
            title="Устройство Х",
            unit_cost=D("12.5"),
            total=D("125"),
            suggested=D("10"),
            quantity=D("8"),
            number="3.2",
        )
        db_session.flush()

        (row,) = _kp(db_session, estimate)

        assert row.title == "Устройство Х"
        assert row.unit == db_session.get(UnitOfMeasure, world.unit_id).symbol
        # объём строки — вес сравнения, а не заглушка файла
        assert row.quantity == D("10")
        assert row.section_ref == "3.2"
        assert (row.line.position_item_id, row.line.unit_price, row.line.total) == (
            item.id,
            D("12.5"),
            D("125"),
        )
        assert row.line.weight == D("10")

    def test_quantity_is_the_comparison_weight_not_the_file_placeholder(self, world, db_session):
        estimate, proposal, *_ = _kp_scene(world)
        world.line(proposal, suggested=D("2600"), quantity=D("1"))
        db_session.flush()

        (row,) = _kp(db_session, estimate)

        assert row.quantity == D("2600")
        assert row.line.weight == D("2600")

    def test_quantity_without_a_suggested_quantity_is_the_customer_quantity(self, world, db_session):
        estimate, proposal, *_ = _kp_scene(world)
        world.line(proposal, suggested=None, quantity=D("3"))
        db_session.flush()

        (row,) = _kp(db_session, estimate)

        assert row.quantity == D("3")

    def test_quantity_is_none_when_neither_quantity_is_known(self, world, db_session):
        estimate, proposal, *_ = _kp_scene(world)
        world.line(proposal, suggested=None, quantity=None)
        db_session.flush()

        (row,) = _kp(db_session, estimate)

        assert row.quantity is None

    def test_weight_falls_back_to_the_quantity(self, world, db_session):
        estimate, proposal, *_ = _kp_scene(world)
        world.line(proposal, suggested=None, quantity=D("3"))
        db_session.flush()

        (row,) = _kp(db_session, estimate)

        assert row.line.weight == D("3")

    def test_row_without_a_unit_has_none(self, world, db_session):
        estimate, proposal, *_ = _kp_scene(world)
        item = world.line(proposal)
        item.unit_id = None
        db_session.flush()

        (row,) = _kp(db_session, estimate)

        assert row.unit is None

    def test_section_ref_falls_back_to_the_position_key(self, world, db_session):
        estimate, proposal, *_ = _kp_scene(world)
        item = world.line(proposal, number=None)
        db_session.flush()

        (row,) = _kp(db_session, estimate)

        assert row.section_ref == item.position_key_in_proposal


class TestKpSemantics:
    def test_row_without_membership_has_no_key_label_family_or_flags(self, world, db_session):
        estimate, proposal, *_ = _kp_scene(world)
        world.f.PositionItemFactory.create(proposal=proposal, is_chapter=False)
        db_session.flush()

        (row,) = _kp(db_session, estimate)

        assert row.line.key is None
        assert row.variant_label is None
        assert row.family_title is None
        assert row.secondary == ()
        assert row.line.is_system is False
        assert (row.line.in_progress, row.line.waiting_admin) == (False, False)

    def test_family_without_a_variant_names_the_family_but_has_no_key(self, world, db_session):
        estimate, proposal, family, *_ = _kp_scene(world)
        world.line(proposal, family=family, variant=None)
        db_session.flush()

        (row,) = _kp(db_session, estimate)

        assert row.family_title == "Семья А"
        assert row.line.key is None
        assert row.variant_label is None

    def test_row_with_a_variant_has_key_family_and_label(self, world, db_session):
        estimate, proposal, family, schema, first, second = _kp_scene(world)
        thick = world.value(first, "50 мм")
        variant = world.variant(family, schema, {1: thick, 2: world.value(second, "бетон")})
        world.line(proposal, family=family, variant=variant)
        db_session.flush()

        (row,) = _kp(db_session, estimate)

        assert row.line.key == LineKey(family.id, variant.id, (first.id, thick))
        assert row.family_title == "Семья А"
        assert row.variant_label == "Семья А · 50 мм · бетон"

    def test_unspecified_value_reads_as_not_specified(self, world, db_session):
        estimate, proposal, family, schema, first, second = _kp_scene(world)
        variant = world.variant(family, schema, {1: None, 2: world.value(second, "бетон")})
        world.line(proposal, family=family, variant=variant)
        db_session.flush()

        (row,) = _kp(db_session, estimate)

        assert row.variant_label == "Семья А · не уточнено · бетон"
        assert row.line.key.first_main == (first.id, None)

    def test_label_lists_only_active_main_parameters_in_ordinal_order(self, world, db_session):
        family, schema = world.family()
        late = world.param(schema, 5, "Материал")
        early = world.param(schema, 2, "Толщина")
        world.param(schema, 3, "Цвет", role="secondary")
        world.param(schema, 4, "Марка", archived=True)
        variant = world.variant(family, schema, {2: world.value(early, "50 мм"), 5: world.value(late, "бетон")})
        estimate, proposal = world.estimate(world.contract())
        world.line(proposal, family=family, variant=variant)
        db_session.flush()

        (row,) = _kp(db_session, estimate)

        assert row.variant_label == "Семья А · 50 мм · бетон"

    def test_variant_on_a_superseded_schema_has_its_own_label_and_no_first_main(self, world, db_session):
        estimate, proposal, family, schema, first, second = _kp_scene(world)
        variant = world.variant(family, schema, {1: world.value(first, "50 мм"), 2: world.value(second, "бетон")})
        world.supersede(family, schema)
        world.line(proposal, family=family, variant=variant)
        db_session.flush()

        (row,) = _kp(db_session, estimate)

        assert row.variant_label == "Семья А · 50 мм · бетон"
        assert row.line.key == LineKey(family.id, variant.id, None)

    @pytest.mark.parametrize(("kind", "is_system"), [("SYSTEM", True), ("WORK", False), ("UNKNOWN", False)])
    def test_only_the_system_kind_marks_a_system_row(self, world, db_session, kind, is_system):
        estimate, proposal, family, schema, first, second = _kp_scene(world)
        variant = world.variant(family, schema, {1: world.value(first, "50 мм"), 2: world.value(second, "бетон")})
        world.line(proposal, family=family, variant=variant, kind=kind)
        db_session.flush()

        (row,) = _kp(db_session, estimate)

        assert row.line.is_system is is_system

    def test_archived_context_still_gives_the_key_of_its_variant(self, world, db_session):
        """У строки КП архивный контекст не отсекается (в отличие от истории):
        «без варианта» — только нет членства или нет варианта у контекста."""
        estimate, proposal, family, schema, first, second = _kp_scene(world)
        thick = world.value(first, "50 мм")
        variant = world.variant(family, schema, {1: thick, 2: world.value(second, "бетон")})
        world.line(proposal, family=family, variant=variant, archived_context=True)
        db_session.flush()

        (row,) = _kp(db_session, estimate)

        assert row.line.key == LineKey(family.id, variant.id, (first.id, thick))
        assert row.variant_label == "Семья А · 50 мм · бетон"

    def test_system_flag_is_read_even_without_a_variant(self, world, db_session):
        estimate, proposal, family, *_ = _kp_scene(world)
        world.line(proposal, family=family, kind="SYSTEM")
        db_session.flush()

        (row,) = _kp(db_session, estimate)

        assert row.line.is_system is True
        assert row.line.key is None

    def test_secondary_parameters_come_from_the_context_in_ordinal_order(self, world, db_session):
        estimate, proposal, family, schema, first, second = _kp_scene(world)
        variant = world.variant(family, schema, {1: world.value(first, "50 мм"), 2: world.value(second, "бетон")})
        color = world.param(schema, 4, "Цвет", role="secondary")
        mark = world.param(schema, 3, "Марка", role="secondary")
        archived = world.param(schema, 6, "Старое", role="secondary", archived=True)
        item = world.line(proposal, family=family, variant=variant)
        for parameter, text in ((color, "серый"), (mark, "М300"), (archived, "x")):
            db_session.add(
                ContextParameterValue(
                    context_id=item.context_id,
                    schema_id=schema.id,
                    parameter_id=parameter.id,
                    value_id=world.value(parameter, text),
                    source="manual",
                )
            )
        # главный параметр в строках контекста во вторичные не попадает
        db_session.add(
            ContextParameterValue(
                context_id=item.context_id,
                schema_id=schema.id,
                parameter_id=first.id,
                value_id=world.value(first, "50 мм"),
                source="manual",
            )
        )
        db_session.flush()

        (row,) = _kp(db_session, estimate)

        assert row.secondary == (("Марка", "М300"), ("Цвет", "серый"))

    def test_secondary_value_not_specified_and_off_list_text(self, world, db_session):
        estimate, proposal, family, schema, first, second = _kp_scene(world)
        unspecified = world.param(schema, 3, "Марка", role="secondary")
        off_list = world.param(schema, 4, "Цвет", role="secondary")
        item = world.line(proposal, family=family)
        db_session.add_all(
            [
                ContextParameterValue(
                    context_id=item.context_id,
                    schema_id=schema.id,
                    parameter_id=unspecified.id,
                    value_id=None,
                    source="none",
                ),
                ContextParameterValue(
                    context_id=item.context_id,
                    schema_id=schema.id,
                    parameter_id=off_list.id,
                    value_id=None,
                    source="off_list",
                    off_list_text="зелёный",
                ),
            ]
        )
        db_session.flush()

        (row,) = _kp(db_session, estimate)

        assert row.secondary == (("Марка", "не уточнено"), ("Цвет", "не из списка: «зелёный»"))


class TestKpArticleRoot:
    def test_article_root_climbs_from_the_chapter_category_to_the_root(self, world, db_session):
        estimate, proposal, *_ = _kp_scene(world)
        cats = _categories(db_session)
        deep = world.chapter(proposal, cats["G"].id)
        world.line(proposal, chapter=deep)
        db_session.flush()

        (row,) = _kp(db_session, estimate)

        assert row.article_root_id == cats["R"].id

    def test_a_root_category_is_its_own_root(self, world, db_session):
        estimate, proposal, *_ = _kp_scene(world)
        cats = _categories(db_session)
        world.line(proposal, chapter=world.chapter(proposal, cats["R2"].id))
        db_session.flush()

        (row,) = _kp(db_session, estimate)

        assert row.article_root_id == cats["R2"].id

    def test_row_without_a_chapter_has_no_article(self, world, db_session):
        estimate, proposal, *_ = _kp_scene(world)
        world.line(proposal)
        db_session.flush()

        (row,) = _kp(db_session, estimate)

        assert row.article_root_id is None

    def test_chapter_without_a_category_gives_no_article(self, world, db_session):
        estimate, proposal, *_ = _kp_scene(world)
        world.line(proposal, chapter=world.chapter(proposal, None))
        db_session.flush()

        (row,) = _kp(db_session, estimate)

        assert row.article_root_id is None

    def test_article_comes_from_the_nearest_chapter_only(self, world, db_session):
        estimate, proposal, *_ = _kp_scene(world)
        cats = _categories(db_session)
        outer = world.chapter(proposal, cats["R2"].id)
        inner = world.chapter(proposal, None)
        inner.chapter_item_id = outer.id
        db_session.flush()
        world.line(proposal, chapter=inner)
        db_session.flush()

        (row,) = _kp(db_session, estimate)

        assert row.article_root_id is None


class TestKpCatalogSignal:
    def test_flags_come_from_the_catalog_signal(self, world, db_session):
        estimate, proposal, *_ = _kp_scene(world)
        idle = world.f.PositionItemFactory.create(proposal=proposal, is_chapter=False)
        busy = world.line(proposal)
        stuck = world.line(proposal)
        from tests.integration.test_our_price_signal import _job

        _job(db_session, "family_suggestion", "pending", context_id=busy.context_id)
        _job(db_session, "family_suggestion", "error", context_id=stuck.context_id)
        db_session.flush()

        rows = {r.line.position_item_id: r.line for r in _kp(db_session, estimate)}

        assert (rows[idle.id].in_progress, rows[idle.id].waiting_admin) == (False, False)
        assert (rows[busy.id].in_progress, rows[busy.id].waiting_admin) == (True, False)
        assert (rows[stuck.id].in_progress, rows[stuck.id].waiting_admin) == (False, True)


# ---------------------------------------------------------------------------
#  Базы НДС, суммы статей, дерево статей
# ---------------------------------------------------------------------------


def _estimate_with(world, rates, *, override=None):
    from tests.factories import estimate_with_proposals

    estimate = estimate_with_proposals(rates)
    estimate.vat_rate_base_override = override
    world.db.flush()
    return estimate


class TestKpVatBases:
    @pytest.mark.parametrize(
        ("name", "rates", "override", "expected"),
        [
            ("одна ставка", [D("20")], None, D("20")),
            ("две лота одной ставки", [D("22"), D("22")], None, D("22")),
            ("нулевая ставка — база", [D("0"), D("0")], None, D("0")),
            ("разногласие ставок", [D("20"), D("22")], None, None),
            ("часть ставок неизвестна", [D("20"), None], None, None),
            ("ставка неизвестна", [None], None, None),
            ("override поверх единой ставки", [D("20")], D("12"), D("12")),
            ("override поверх разногласия", [D("20"), D("22")], D("12"), D("12")),
            ("нулевой override — база", [D("20")], D("0"), D("0")),
            ("override поверх неизвестной ставки", [None], D("10"), D("10")),
        ],
    )
    def test_base_follows_override_then_unanimous_proposal_rates(
        self, world, db_session, name, rates, override, expected
    ):
        estimate = _estimate_with(world, rates, override=override)

        result = kp_vat_bases(db_session, [estimate.id])

        assert result == {estimate.id: expected}, name
        assert result == _rates_by_estimate(db_session, [estimate.id]), name

    def test_estimate_without_proposals_has_no_base(self, world, db_session):
        estimate = world.f.EstimateFactory.create()
        db_session.flush()

        assert kp_vat_bases(db_session, [estimate.id]) == {estimate.id: None}
        assert _rates_by_estimate(db_session, [estimate.id]) == {estimate.id: None}

    def test_many_estimates_agree_with_the_stage_summary_rule(self, world, db_session):
        estimates = [
            _estimate_with(world, [D("20")]),
            _estimate_with(world, [D("20"), D("22")]),
            _estimate_with(world, [D("20"), D("22")], override=D("5")),
            _estimate_with(world, [None]),
        ]
        ids = [e.id for e in estimates]

        assert kp_vat_bases(db_session, ids) == _rates_by_estimate(db_session, ids)
        assert kp_vat_bases(db_session, ids) == {
            ids[0]: D("20"),
            ids[1]: None,
            ids[2]: D("5"),
            ids[3]: None,
        }

    def test_no_estimates_means_no_bases(self, db_session):
        assert kp_vat_bases(db_session, []) == {}


class TestCategoryRows:
    def test_each_proposal_of_a_multi_lot_estimate_gives_its_own_row(self, world, db_session):
        """Накопление — дело ядра (`net_by_root`): загрузчик отдаёт строки VIEW
        по одной на предложение, ничего не складывая."""
        cats = _categories(db_session)
        estimate, first_proposal = world.estimate(world.contract())
        second_lot = world.f.LotFactory.create(estimate=estimate)
        second_proposal = world.f.ProposalFactory.create(
            lot=second_lot, contractor=first_proposal.contractor, vat_rate=D("20")
        )
        for proposal, total in ((first_proposal, D("1000")), (second_proposal, D("500"))):
            chapter = world.chapter(proposal, cats["C"].id)
            world.line(proposal, chapter=chapter, total=total)
        db_session.flush()

        rows = category_rows(db_session, [estimate.id])

        assert sorted((r.work_category_id, r.amount) for r in rows) == [
            (cats["C"].id, D("500")),
            (cats["C"].id, D("1000")),
        ]
        assert all(isinstance(r, CategoryRow) for r in rows)
        assert {r.estimate_id for r in rows} == {estimate.id}

    def test_negative_and_unallocated_amounts_come_through_unchanged(self, world, db_session):
        cats = _categories(db_session)
        estimate, proposal = world.estimate(world.contract())
        chapter = world.chapter(proposal, cats["R"].id)
        world.line(proposal, chapter=chapter, total=D("100"))
        world.line(proposal, chapter=chapter, total=D("-20"))
        world.line(proposal, total=D("7"))
        db_session.flush()

        rows = category_rows(db_session, [estimate.id])

        assert sorted(((r.work_category_id is None), r.amount) for r in rows) == [(False, D("80")), (True, D("7"))]

    def test_vat_base_is_the_override_else_the_proposal_rate(self, world, db_session):
        cats = _categories(db_session)
        plain, plain_proposal = world.estimate(world.contract(), vat_rate=D("22"))
        forced, forced_proposal = world.estimate(world.contract(), override=D("12"), vat_rate=D("22"))
        unknown, unknown_proposal = world.estimate(world.contract(), vat_rate=None)
        for proposal in (plain_proposal, forced_proposal, unknown_proposal):
            world.line(proposal, chapter=world.chapter(proposal, cats["R"].id))
        db_session.flush()

        rows = category_rows(db_session, [plain.id, forced.id, unknown.id])

        assert {r.estimate_id: r.vat_base for r in rows} == {
            plain.id: D("22"),
            forced.id: D("12"),
            unknown.id: None,
        }

    def test_only_the_asked_estimates_are_read(self, world, db_session):
        cats = _categories(db_session)
        asked, asked_proposal = world.estimate(world.contract())
        other, other_proposal = world.estimate(world.contract())
        for proposal in (asked_proposal, other_proposal):
            world.line(proposal, chapter=world.chapter(proposal, cats["R"].id))
        db_session.flush()

        rows = category_rows(db_session, [asked.id])

        assert {r.estimate_id for r in rows} == {asked.id}

    def test_no_estimates_means_no_rows(self, db_session):
        assert category_rows(db_session, []) == []


class TestCategoryRoots:
    def test_every_category_maps_to_its_root_and_a_root_to_itself(self, db_session):
        cats = _categories(db_session)

        root_of, _ = category_roots(db_session)

        assert root_of[cats["G"].id] == cats["R"].id
        assert root_of[cats["C"].id] == cats["R"].id
        assert root_of[cats["R"].id] == cats["R"].id
        assert root_of[cats["R2"].id] == cats["R2"].id

    def test_meta_describes_roots_only(self, db_session):
        cats = _categories(db_session)

        root_of, meta = category_roots(db_session)

        assert set(meta) == set(root_of.values())
        assert cats["C"].id not in meta
        assert meta[cats["R"].id] == ArticleMeta(
            id=cats["R"].id, code="8801", title="Статья R", sort_order=cats["R"].sort_order
        )

    def test_roots_order_by_their_sort_order(self, db_session):
        cats = _categories(db_session)

        _, meta = category_roots(db_session)

        assert meta[cats["R"].id].sort_order < meta[cats["R2"].id].sort_order

    def test_map_covers_the_whole_seeded_tree(self, db_session):
        root_of, meta = category_roots(db_session)
        parents = dict(db_session.execute(sa.select(WorkCategory.id, WorkCategory.parent_id)).all())

        assert set(root_of) == set(parents)
        assert all(parents[root] is None for root in meta)
        assert {root for root in meta} == {i for i, p in parents.items() if p is None}

    def test_a_cycle_in_the_tree_is_refused_instead_of_looping(self, db_session):
        """База запрещает только петлю на себя; цикл из двух статей обход обязан
        отвергнуть, а не крутиться без конца (обход — в потоке с таймаутом)."""
        top = db_session.execute(sa.select(sa.func.max(WorkCategory.sort_order))).scalar_one() or 0
        first = WorkCategory(code="8803", title="Статья цикла 1", parent_id=None, sort_order=top + 1)
        db_session.add(first)
        db_session.flush()
        second = WorkCategory(code="8803.1", title="Статья цикла 2", parent_id=first.id, sort_order=top + 2)
        db_session.add(second)
        db_session.flush()
        first.parent_id = second.id
        db_session.flush()
        outcome = {}

        def walk():
            try:
                category_roots(db_session)
            except ValueError as exc:
                outcome["refused"] = exc
            else:
                outcome["returned"] = True

        thread = threading.Thread(target=walk, daemon=True)
        thread.start()
        thread.join(timeout=30)

        assert not thread.is_alive(), "обход дерева статей зациклился"
        assert "refused" in outcome


# ---------------------------------------------------------------------------
#  Число операторов не растёт с числом строк
# ---------------------------------------------------------------------------


def _history_rows(world, family, schema, parameter, count, *, start):
    """`count` новых договоров с одной строкой на свой вариант, значением и
    второстепенным значением."""
    for index in range(start, start + count):
        contract = world.contract()
        _, proposal = world.estimate(contract)
        variant = world.variant(family, schema, {1: world.value(parameter, f"{index} мм")})
        world.line(proposal, family=family, variant=variant)
    world.db.flush()


class TestStatementCountDoesNotGrowWithRows:
    def test_load_history_runs_the_same_statements_for_2_and_20_lines(self, world, db_session):
        family, schema = world.family()
        parameter = world.param(schema, 1, "Толщина")
        _history_rows(world, family, schema, parameter, 2, start=0)
        with count_statements(db_session) as few:
            _, lines_few = load_history(db_session, exclude_object_id=-1)
        _history_rows(world, family, schema, parameter, 18, start=2)
        with count_statements(db_session) as many:
            _, lines_many = load_history(db_session, exclude_object_id=-1)

        assert (len(lines_few), len(lines_many)) == (2, 20)
        assert few[0] > 0
        assert many[0] == few[0]

    def _kp_rows(self, world, proposal, family, schema, parameter, secondary, chapter, count, start):
        for index in range(start, start + count):
            variant = world.variant(family, schema, {1: world.value(parameter, f"{index} мм")})
            item = world.line(proposal, family=family, variant=variant, chapter=chapter)
            db_session_add = world.db.add
            db_session_add(
                ContextParameterValue(
                    context_id=item.context_id,
                    schema_id=schema.id,
                    parameter_id=secondary.id,
                    value_id=world.value(secondary, f"цвет {index}"),
                    source="manual",
                )
            )
        world.db.flush()

    def test_load_kp_lines_runs_the_same_statements_for_2_and_20_lines(self, world, db_session):
        estimate, proposal = world.estimate(world.contract())
        family, schema = world.family()
        parameter = world.param(schema, 1, "Толщина")
        secondary = world.param(schema, 2, "Цвет", role="secondary")
        cats = _categories(db_session)
        chapter = world.chapter(proposal, cats["G"].id)
        self._kp_rows(world, proposal, family, schema, parameter, secondary, chapter, 2, 0)
        with count_statements(db_session) as few:
            rows_few = load_kp_lines(db_session, [estimate.id])[estimate.id]
        self._kp_rows(world, proposal, family, schema, parameter, secondary, chapter, 18, 2)
        with count_statements(db_session) as many:
            rows_many = load_kp_lines(db_session, [estimate.id])[estimate.id]

        assert (len(rows_few), len(rows_many)) == (2, 20)
        assert all(r.secondary for r in rows_many)
        assert few[0] > 0
        assert many[0] == few[0]

    def test_kp_vat_bases_runs_the_same_statements_for_2_and_20_estimates(self, world, db_session):
        few_ids = [_estimate_with(world, [D("20")]).id for _ in range(2)]
        with count_statements(db_session) as few:
            kp_vat_bases(db_session, few_ids)
        many_ids = few_ids + [_estimate_with(world, [D("20"), D("22")]).id for _ in range(18)]
        with count_statements(db_session) as many:
            result = kp_vat_bases(db_session, many_ids)

        assert len(result) == 20
        assert few[0] > 0
        assert many[0] == few[0]

    def test_category_rows_runs_the_same_statements_for_2_and_20_estimates(self, world, db_session):
        cats = _categories(db_session)

        def estimates(count):
            made = []
            for _ in range(count):
                estimate, proposal = world.estimate(world.contract())
                world.line(proposal, chapter=world.chapter(proposal, cats["C"].id))
                made.append(estimate.id)
            db_session.flush()
            return made

        few_ids = estimates(2)
        with count_statements(db_session) as few:
            category_rows(db_session, few_ids)
        many_ids = few_ids + estimates(18)
        with count_statements(db_session) as many:
            rows = category_rows(db_session, many_ids)

        assert len(rows) == 20
        assert few[0] > 0
        assert many[0] == few[0]

    def test_load_kp_lines_runs_no_statements_without_estimates(self, db_session):
        with count_statements(db_session) as box:
            assert load_kp_lines(db_session, []) == {}

        assert box[0] == 0

    def test_category_rows_runs_no_statements_without_estimates(self, db_session):
        with count_statements(db_session) as box:
            assert category_rows(db_session, []) == []

        assert box[0] == 0

    def test_category_roots_is_one_statement(self, db_session):
        with count_statements(db_session) as once:
            category_roots(db_session)

        assert once[0] == 1
