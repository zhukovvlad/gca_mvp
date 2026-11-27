"""Загрузка для правила «строка похожа на комплект» — `crud/lump_sum.py`, таблица
`lump_sum_overrides` (миграция 0029), второй кэш (спека строк-сумм §2, §3 п. 1).

Сцены строятся фабриками; названия и числа выдуманы. Пул обычной ставки: из ключа выпадает
каждая строка по одному условию — четвёртая строка с экстремальной ценой при этом не двигает
ставку трёх хороших.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import crud.lump_sum as crud_lump_sum
from crud.lump_sum import load_lump_overrides, load_typical_rates, typical_rates
from models import (
    CK_LUMP_SUM_VERDICT,
    CatalogContext,
    CatalogPosition,
    LumpSumOverride,
    NotAnalogMark,
    PositionItem,
    PriceErrorMark,
    User,
)
from money.vat import gross_to_net
from services.context_routing import route_position
from services.work_families import assign_family
from tests.integration.test_catalog_discovery_schema import rejected_by
from tests.integration.test_semantic_queue_api import _active_family, _unit_id
from tests.integration.test_work_variants_schema import _scratch_alembic

pytestmark = pytest.mark.integration

D = Decimal
BACKEND_ROOT = Path(__file__).resolve().parents[2]
NOW = dt.datetime(2026, 11, 22, 12, 0, tzinfo=dt.UTC)
EXTREME = D("100000")


class Scene:
    def __init__(self, db, factories, admin):
        self.db = db
        self.f = factories
        self.admin = admin
        self._n = 0
        self._families: dict[tuple[str, str], object] = {}

    def family(self, title="Семья А", unit="M2"):
        key = (title, unit)
        if key not in self._families:
            self._families[key] = _active_family(
                self.db, title=title, unit_name=unit, actor_id=self.admin.id
            )
        return self._families[key]

    def contract_estimate(self, *, amendment_no=None, override=None, vat_rate=D("0")):
        contract = self.f.ContractFactory.create()
        estimate = self.f.EstimateFactory.create(
            contract=contract, amendment_no=amendment_no, vat_rate_base_override=override
        )
        lot = self.f.LotFactory.create(estimate=estimate)
        proposal = self.f.ProposalFactory.create(
            lot=lot, contractor=contract.contractor, vat_rate=vat_rate
        )
        return estimate, proposal

    def offer_estimate(self, *, vat_rate=D("0")):
        rnd = self.f.TenderRoundFactory.create()
        offer = self.f.OfferFactory.create(round=rnd)
        estimate = self.f.EstimateFactory.create(contract=None, offer=offer)
        lot = self.f.LotFactory.create(estimate=estimate)
        proposal = self.f.ProposalFactory.create(lot=lot, vat_rate=vat_rate)
        return estimate, proposal

    def round_estimate(self, *, vat_rate=D("0")):
        rnd = self.f.TenderRoundFactory.create()
        estimate = self.f.EstimateFactory.create(contract=None, round=rnd)
        lot = self.f.LotFactory.create(estimate=estimate)
        proposal = self.f.ProposalFactory.create(
            lot=lot, contractor=None, is_baseline=True, vat_rate=vat_rate
        )
        return estimate, proposal

    def line(
        self, proposal, *, price=D("100"), weight=D("10"), family="default", unit="M2",
        kind=None, archived=False, no_unit=False,
    ):
        """Строка с каталожной позицией, контекстом и (по умолчанию) семьёй; `family=None` —
        без семьи."""
        self._n += 1
        title = f"Работа {self._n}"
        cp = self.f.CatalogPositionFactory.create(
            unit_id=_unit_id(self.db, unit), standard_job_title=title
        )
        item = self.f.PositionItemFactory.create(
            proposal=proposal, is_chapter=False, job_title_in_proposal=title,
            catalog_position_id=cp.id, unit_cost_total=price, total_cost_total=price,
            suggested_quantity=weight, quantity=weight,
        )
        context_id = route_position(self.db, position_item_id=item.id).context_id
        if family == "default":
            family = self.family(unit=unit if unit in ("M2", "PCS") else "M2")
        if family is not None:
            assign_family(self.db, context_id=context_id, family_id=family.id, actor_id=self.admin.id)
        if no_unit:  # единица исчезает после назначения семьи: семья без единицы не назначается
            self.db.execute(
                sa.update(CatalogPosition).where(CatalogPosition.id == cp.id).values(unit_id=None)
            )
        values = {}
        if kind is not None:
            values["semantic_kind"] = kind
        if archived:
            values["archived_at"] = NOW
        if values:
            self.db.execute(
                sa.update(CatalogContext).where(CatalogContext.id == context_id).values(**values)
            )
        self.db.flush()
        return item

    def good_pool(self, proposal, prices=(D("100"), D("200"), D("300")), **kwargs):
        return [self.line(proposal, price=price, **kwargs) for price in prices]

    def override(self, item, verdict="lump"):
        self.db.add(LumpSumOverride(position_item_id=item.id, verdict=verdict, set_by=self.admin.id))
        self.db.flush()


@pytest.fixture
def scene(db_session, factories, admin_user):
    return Scene(db_session, factories, admin_user)


def _rate(scene, family=None, unit="M2"):
    family = family or scene.family()
    rates = load_typical_rates(scene.db)
    return rates.rates.get((family.id, _unit_id(scene.db, unit)))


# ---------------------------------------------------------------------------
#  Пул обычной ставки
# ---------------------------------------------------------------------------


class TestPoolSize:
    def test_two_lines_have_no_rate(self, scene):
        _, proposal = scene.contract_estimate()
        scene.good_pool(proposal, prices=(D("100"), D("200")))
        assert _rate(scene) is None

    def test_three_lines_have_a_rate_of_the_median(self, scene):
        _, proposal = scene.contract_estimate()
        scene.good_pool(proposal)
        rate = _rate(scene)
        assert rate.net == D("200") and rate.lines == 3

    def test_four_lines_give_the_mean_of_the_two_middle_ones_exactly(self, scene):
        _, proposal = scene.contract_estimate()
        scene.good_pool(proposal, prices=(D("100.10"), D("200.20"), D("300.30"), D("400.41")))
        rate = _rate(scene)
        assert rate.net == D("250.25") and rate.lines == 4
        assert isinstance(rate.net, Decimal)

    def test_contract_and_offer_estimates_both_count_including_amendments(self, scene):
        _, first = scene.contract_estimate()
        _, amendment = scene.contract_estimate(amendment_no=1)
        _, offer = scene.offer_estimate()
        scene.line(first, price=D("100"))
        scene.line(amendment, price=D("200"))
        scene.line(offer, price=D("300"))
        rate = _rate(scene)
        assert rate.net == D("200") and rate.lines == 3

    def test_key_is_family_and_catalog_unit(self, scene):
        _, proposal = scene.contract_estimate()
        scene.good_pool(proposal)
        scene.good_pool(proposal, prices=(D("1000"), D("2000"), D("3000")), unit="PCS")
        rates = load_typical_rates(scene.db)
        assert rates.rates[(scene.family().id, _unit_id(scene.db, "M2"))].net == D("200")
        assert rates.rates[(scene.family(unit="PCS").id, _unit_id(scene.db, "PCS"))].net == D("2000")

    def test_unit_codes_and_labels_cover_the_units(self, scene):
        rates = load_typical_rates(scene.db)
        m2, sht = _unit_id(scene.db, "M2"), _unit_id(scene.db, "PCS")
        assert rates.unit_codes[m2] == "M2" and rates.unit_codes[sht] == "PCS"
        assert rates.unit_labels[m2] and rates.unit_labels[sht]


class TestPoolDropsOneAtATime:
    """Три хорошие строки (ставка 200) и четвёртая с ценой 100 000 и ровно одним изъяном."""

    @pytest.mark.parametrize(
        "reason",
        ["weight_one", "net_below_one", "vat_base_null", "round_estimate", "archived_context",
         "system_context", "chapter", "override_lump", "no_catalog_unit", "no_family",
         "zero_price", "nan_weight"],
    )
    def test_defective_line_is_left_out(self, scene, reason):
        _, proposal = scene.contract_estimate()
        scene.good_pool(proposal)
        # трижды: одна строка с чужим ключом сама ставки не образует и осталась бы незамеченной
        for _ in range(3):
            if reason == "weight_one":
                scene.line(proposal, price=EXTREME, weight=D("1.000"))
            elif reason == "net_below_one":
                scene.line(proposal, price=D("0.99"))
            elif reason == "vat_base_null":
                _, no_base = scene.contract_estimate(vat_rate=None)
                scene.line(no_base, price=EXTREME)
            elif reason == "round_estimate":
                _, baseline = scene.round_estimate()
                scene.line(baseline, price=EXTREME)
            elif reason == "archived_context":
                scene.line(proposal, price=EXTREME, archived=True)
            elif reason == "system_context":
                scene.line(proposal, price=EXTREME, kind="SYSTEM")
            elif reason == "chapter":
                item = scene.line(proposal, price=EXTREME)
                scene.db.execute(
                    sa.update(PositionItem).where(PositionItem.id == item.id).values(is_chapter=True)
                )
            elif reason == "override_lump":
                scene.override(scene.line(proposal, price=EXTREME), "lump")
            elif reason == "no_catalog_unit":
                scene.line(proposal, price=EXTREME, no_unit=True)
            elif reason == "no_family":
                scene.line(proposal, price=EXTREME, family=None)
            elif reason == "zero_price":
                scene.line(proposal, price=D("0"))
            elif reason == "nan_weight":
                scene.line(proposal, price=EXTREME, weight=D("NaN"))
        scene.db.flush()
        rates = load_typical_rates(scene.db)
        rate = rates.rates[(scene.family().id, _unit_id(scene.db, "M2"))]
        assert rate.net == D("200") and rate.lines == 3
        assert all(family is not None and unit is not None for family, unit in rates.rates)

    def test_the_extreme_line_counts_when_nothing_is_wrong_with_it(self, scene):
        """Парный положительный случай: изъяна нет — строка входит и сдвигает медиану."""
        _, proposal = scene.contract_estimate()
        scene.good_pool(proposal)
        scene.line(proposal, price=EXTREME)
        rate = _rate(scene)
        assert rate.lines == 4 and rate.net == D("250")

    def test_rate_override_does_not_drop_the_line(self, scene):
        _, proposal = scene.contract_estimate()
        scene.good_pool(proposal)
        scene.override(scene.line(proposal, price=D("400")), "rate")
        assert _rate(scene).lines == 4

    def test_net_exactly_one_is_in_the_pool(self, scene):
        _, proposal = scene.contract_estimate()
        scene.good_pool(proposal, prices=(D("1"), D("2"), D("3")))
        assert _rate(scene).lines == 3

    def test_overridden_lump_line_leaves_a_pool_of_three_without_a_rate(self, scene):
        """Review Focus 2: из трёх строк одна «комплект» — ставки нет."""
        _, proposal = scene.contract_estimate()
        items = scene.good_pool(proposal)
        scene.override(items[2], "lump")
        assert _rate(scene) is None


class TestPoolVatBase:
    def test_gross_prices_at_twenty_percent_give_the_same_rate(self, scene):
        _, proposal = scene.contract_estimate(vat_rate=D("20"))
        scene.good_pool(proposal, prices=(D("120"), D("240"), D("360")))
        assert _rate(scene).net == D("200")

    def test_prices_at_zero_percent_give_the_same_rate(self, scene):
        _, proposal = scene.contract_estimate(vat_rate=D("0"))
        scene.good_pool(proposal)
        assert _rate(scene).net == D("200")

    def test_both_bases_in_one_pool(self, scene):
        _, twenty = scene.contract_estimate(vat_rate=D("20"))
        _, zero = scene.contract_estimate(vat_rate=D("0"))
        scene.good_pool(twenty, prices=(D("120"), D("240"), D("360")))
        scene.good_pool(zero)
        rate = _rate(scene)
        assert rate.net == D("200") and rate.lines == 6

    def test_estimate_override_beats_the_proposal_rate(self, scene):
        _, proposal = scene.contract_estimate(vat_rate=D("0"), override=D("20"))
        scene.good_pool(proposal, prices=(D("120"), D("240"), D("360")))
        assert _rate(scene).net == D("200")

    def test_net_is_gross_to_net(self, scene):
        _, proposal = scene.contract_estimate(vat_rate=D("20"))
        scene.good_pool(proposal, prices=(D("100"), D("101"), D("103")))
        assert _rate(scene).net == gross_to_net(D("101"), D("20"))


# ---------------------------------------------------------------------------
#  Поправки
# ---------------------------------------------------------------------------


class TestLoadOverrides:
    def test_returns_verdict_author_email_and_time_only_for_rows_with_override(self, scene):
        _, proposal = scene.contract_estimate()
        with_lump, with_rate, without = (scene.line(proposal) for _ in range(3))
        scene.override(with_lump, "lump")
        scene.override(with_rate, "rate")
        got = load_lump_overrides(scene.db, [with_lump.id, with_rate.id, without.id])
        assert set(got) == {with_lump.id, with_rate.id}
        assert got[with_lump.id].verdict == "lump" and got[with_rate.id].verdict == "rate"
        assert got[with_lump.id].set_by_id == scene.admin.id
        assert got[with_lump.id].set_by_email == scene.admin.email
        assert got[with_lump.id].set_at is not None

    def test_only_requested_ids(self, scene):
        _, proposal = scene.contract_estimate()
        first, second = scene.line(proposal), scene.line(proposal)
        scene.override(first)
        scene.override(second)
        assert set(load_lump_overrides(scene.db, [first.id])) == {first.id}

    def test_empty_set_makes_no_query(self, scene, monkeypatch):
        def boom(*args, **kwargs):
            raise AssertionError("запрос при пустом наборе")

        monkeypatch.setattr(scene.db, "execute", boom)
        assert load_lump_overrides(scene.db, []) == {}
        assert load_lump_overrides(scene.db, set()) == {}


# ---------------------------------------------------------------------------
#  Кэш
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 9000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def cache_on(monkeypatch):
    from config import settings
    from services.status_cache import typical_rates_cache

    clock = _Clock()
    monkeypatch.setattr(settings, "TYPICAL_RATES_CACHE_TTL_S", 300)
    monkeypatch.setattr(typical_rates_cache, "clock", clock)
    return clock


@pytest.fixture
def loads(monkeypatch):
    calls: list[int] = []
    real = crud_lump_sum.load_typical_rates

    def counted(db):
        calls.append(1)
        return real(db)

    monkeypatch.setattr(crud_lump_sum, "load_typical_rates", counted)
    return calls


def _settle(scene) -> None:
    """Закрепить записи фикстур: в «грязной» транзакции (записано вне закрытого списка кэша сцен)
    `typical_rates` кэш обходит (спека стадийного кэша §5), а тесты кэша проверяют попадание."""
    Session.commit(scene.db)


class TestCache:
    def test_two_calls_within_the_ttl_load_once(self, scene, cache_on, loads):
        _settle(scene)
        first = typical_rates(scene.db)
        second = typical_rates(scene.db)
        assert len(loads) == 1 and first is second

    def test_call_after_the_ttl_loads_again(self, scene, cache_on, loads):
        typical_rates(scene.db)
        cache_on.now += 300
        typical_rates(scene.db)
        assert len(loads) == 2

    def test_zero_ttl_loads_every_time(self, scene, loads):
        typical_rates(scene.db)
        typical_rates(scene.db)
        assert len(loads) == 2

    @pytest.mark.parametrize("method", ["put", "post", "patch", "delete"])
    def test_any_write_request_resets(self, admin_client, scene, cache_on, loads, method):
        typical_rates(scene.db)
        getattr(admin_client, method)("/api/v1/no-such-route-for-the-cache-test")
        typical_rates(scene.db)
        assert len(loads) == 2

    def test_get_request_does_not_reset(self, admin_client, scene, cache_on, loads):
        _settle(scene)
        typical_rates(scene.db)
        admin_client.get("/api/v1/no-such-route-for-the-cache-test")
        typical_rates(scene.db)
        assert len(loads) == 1

    @pytest.mark.parametrize("fail", [False, True])
    def test_run_import_job_resets_even_on_exception(self, scene, cache_on, loads, monkeypatch, fail):
        import services.import_pipeline as pipeline

        def fake(*args, **kwargs):
            if fail:
                raise RuntimeError("импорт упал")

        monkeypatch.setattr(pipeline, "_run_import_job", fake)
        typical_rates(scene.db)
        assert len(loads) == 1
        if fail:
            with pytest.raises(RuntimeError):
                pipeline.run_import_job(1, session_factory=None, storage=None)
        else:
            pipeline.run_import_job(1, session_factory=None, storage=None)
        typical_rates(scene.db)
        assert len(loads) == 2


# ---------------------------------------------------------------------------
#  Схема (миграция 0029)
# ---------------------------------------------------------------------------


def _migration_0029():
    path = next(BACKEND_ROOT.glob("alembic/versions/*0029-lump_sum_overrides.py"))
    spec = importlib.util.spec_from_file_location("_migration_0029_lump_sum_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestSchema:
    def test_table_and_columns(self, db_session):
        columns = {
            row[0]: (row[1], row[2])
            for row in db_session.execute(sa.text(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'lump_sum_overrides'"))
        }
        assert columns == {
            "position_item_id": ("bigint", "NO"),
            "verdict": ("text", "NO"),
            "set_by": ("integer", "NO"),
            "set_at": ("timestamp with time zone", "NO"),
        }

    @pytest.mark.parametrize("verdict", ["lump", "rate"])
    def test_both_verdicts_are_accepted(self, scene, verdict):
        _, proposal = scene.contract_estimate()
        scene.override(scene.line(proposal), verdict)

    @pytest.mark.parametrize("verdict", ["comp", "", "LUMP"])
    def test_other_verdict_is_rejected_by_name(self, scene, verdict):
        _, proposal = scene.contract_estimate()
        item = scene.line(proposal)
        with rejected_by(scene.db, "ck_lump_sum_overrides_verdict"):
            scene.db.add(
                LumpSumOverride(position_item_id=item.id, verdict=verdict, set_by=scene.admin.id)
            )

    def test_deleting_the_row_cascades(self, scene):
        _, proposal = scene.contract_estimate()
        item = scene.line(proposal)
        scene.override(item)
        scene.db.execute(sa.delete(PositionItem).where(PositionItem.id == item.id))
        left = scene.db.execute(
            sa.select(sa.func.count()).select_from(LumpSumOverride)
        ).scalar_one()
        assert left == 0

    def test_deleting_the_author_is_restricted(self, scene, factories):
        author = factories.UserFactory.create()
        _, proposal = scene.contract_estimate()
        item = scene.line(proposal)
        scene.db.add(LumpSumOverride(position_item_id=item.id, verdict="lump", set_by=author.id))
        scene.db.flush()
        with pytest.raises(IntegrityError) as exc, scene.db.begin_nested():
            scene.db.execute(sa.delete(User).where(User.id == author.id))
        assert exc.value.orig.diag.constraint_name == "fk_lump_sum_overrides_set_by"

    def test_one_override_per_row(self, scene):
        _, proposal = scene.contract_estimate()
        item = scene.line(proposal)
        scene.override(item)
        with pytest.raises(IntegrityError) as exc, scene.db.begin_nested():
            scene.db.add(LumpSumOverride(position_item_id=item.id, verdict="rate", set_by=scene.admin.id))
            scene.db.flush()
        assert exc.value.orig.diag.constraint_name == "pk_lump_sum_overrides"


class TestParityWithMigration:
    def test_model_expression_equals_migration_and_independent_literal(self):
        assert CK_LUMP_SUM_VERDICT == _migration_0029().CK_LUMP_SUM_VERDICT
        assert CK_LUMP_SUM_VERDICT == "verdict IN ('lump', 'rate')"

    def test_constraint_names_match_the_model(self):
        names = {c.name for c in LumpSumOverride.__table__.constraints}
        assert "ck_lump_sum_overrides_verdict" in names
        assert _migration_0029().CK_NAME == "ck_lump_sum_overrides_verdict"

    def test_revision_chain(self):
        module = _migration_0029()
        assert (module.revision, module.down_revision) == ("0029", "0028")


@pytest.mark.migration_roundtrip
class TestMigrationRoundtrip:
    def test_upgrade_creates_and_downgrade_drops_the_table(self):
        with _scratch_alembic("0029 roundtrip") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0028")
            engine = sa.create_engine(scratch_url)
            try:
                def table_exists():
                    with engine.connect() as conn:
                        return conn.execute(
                            sa.text("SELECT to_regclass('public.lump_sum_overrides') IS NOT NULL")
                        ).scalar_one()

                assert table_exists() is False
                command.upgrade(cfg, "0029")
                assert table_exists() is True
                with engine.connect() as conn:
                    names = {
                        row[0]
                        for row in conn.execute(sa.text(
                            "SELECT conname FROM pg_constraint "
                            "WHERE conrelid = 'lump_sum_overrides'::regclass"))
                    }
                assert {"pk_lump_sum_overrides", "ck_lump_sum_overrides_verdict",
                        "fk_lump_sum_overrides_position_item",
                        "fk_lump_sum_overrides_set_by"} <= names
                command.downgrade(cfg, "0028")
                assert table_exists() is False
            finally:
                engine.dispose()


class TestPriceErrorLeavesTheTypicalRatePool:
    """Спека «Не аналог» §5 п. 4: строка с «ошибкой в цене» ставку не образует; тендерная отметка не трогает."""

    def _mark(self, scene, item):
        scene.db.add(PriceErrorMark(position_item_id=item.id, reason="опечатка", set_by=scene.admin.id))
        scene.db.flush()

    def test_a_pool_of_three_with_one_marked_line_has_no_rate(self, scene):
        _, proposal = scene.contract_estimate()
        items = scene.good_pool(proposal)
        self._mark(scene, items[0])
        assert _rate(scene) is None

    def test_a_marked_extreme_line_does_not_move_the_rate_of_four(self, scene):
        _, proposal = scene.contract_estimate()
        scene.good_pool(proposal)
        extreme = scene.line(proposal, price=EXTREME)
        assert _rate(scene).lines == 4
        self._mark(scene, extreme)
        rate = _rate(scene)
        assert rate.net == D("200") and rate.lines == 3

    def test_unmarking_returns_the_line_to_the_pool(self, scene):
        _, proposal = scene.contract_estimate()
        items = scene.good_pool(proposal)
        self._mark(scene, items[0])
        assert _rate(scene) is None
        scene.db.execute(sa.delete(PriceErrorMark))
        assert _rate(scene).lines == 3

    def test_a_tender_mark_leaves_the_pool_as_is(self, scene):
        _, proposal = scene.contract_estimate()
        items = scene.good_pool(proposal)
        tender = scene.f.TenderFactory.create()
        scene.db.add(NotAnalogMark(
            tender_id=tender.id, form_title="работа 1", form_unit_id=None,
            position_item_id=items[0].id, reason="другая работа", set_by=scene.admin.id,
        ))
        scene.db.flush()
        rate = _rate(scene)
        assert rate.net == D("200") and rate.lines == 3
