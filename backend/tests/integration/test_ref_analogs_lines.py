"""Справочные строки и значения параметров связей — `crud/reference_lines.py`
(спека справочных аналогов §2.3, §2.7 п. 1).

Сцены строятся помощниками `World` из тестов загрузчиков «КП против нашей цены»;
названия и числа выдуманы. У каждого условия §2.3 свой вход, нарушающий ровно его,
и соседний вход, где строка проходит.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from crud.lump_sum import typical_rates
from crud.our_price_load import load_history
from crud.reference_lines import (
    ParamText,
    ReferenceLine,
    load_link_values,
    load_reference_lines,
    reference_lines_query,
)
from models import ContextMember, ContextParameterValue, FamilyParameterValue
from tests.integration.test_our_price_load import D, World, count_statements

pytestmark = pytest.mark.integration


@pytest.fixture
def world(db_session, factories, admin_user):
    return World(db_session, factories, admin_user)


def _history(db):
    contracts, _ = load_history(db, exclude_object_id=-1)
    return contracts


def _scene(world, **line_kwargs):
    """Договор с одной сметой и одной строкой; возвращает (договор, смета, строка)."""
    contract = world.contract()
    estimate, proposal = world.estimate(contract)
    item = world.line(proposal, **line_kwargs)
    world.db.flush()
    return contract, estimate, item


def _loaded(world, item, *, factors=None, contracts=None):
    contracts = _history(world.db) if contracts is None else contracts
    return load_reference_lines(world.db, contracts, [item.id], factors=factors)


# ---------------------------------------------------------------------------
#  Шесть условий §2.3
# ---------------------------------------------------------------------------


class TestSixConditions:
    def test_an_ordinary_line_is_reference_and_carries_its_facts(self, world):
        family, schema = world.family()
        chain = world.chain(("Корпус", "Полы"), "aboveground")
        contract, estimate, item = _scene(world, family=family, chain=chain, unit_cost=D("120"))

        found = _loaded(world, item)

        line = found[item.id]
        assert isinstance(line, ReferenceLine)
        assert line.position_item_id == item.id
        assert line.contract_id == contract.id
        assert line.estimate_id == estimate.id
        assert line.family_id == family.id
        assert line.context_id == item.context_id
        assert line.zone == "aboveground"
        assert line.chain_titles == frozenset({"корпус", "полы"})
        assert line.unit_id == world.unit_id
        assert line.unit_symbol == "м²"
        assert line.chapter_item_id is None
        # 120 с НДС 20 % -> 100 нетто
        assert line.nominal == D("100")
        assert line.net == D("100")

    def test_a_chapter_is_not_reference(self, world):
        contract = world.contract()
        _, proposal = world.estimate(contract)
        chapter = world.chapter(proposal, None)
        # глава с ценой и весом, чтобы отсекало именно «глава»
        chapter.unit_cost_total = D("100")
        chapter.suggested_quantity = D("5")
        world.db.flush()

        assert _loaded(world, chapter) == {}

    def test_a_line_without_context_is_reference(self, world):
        contract = world.contract()
        _, proposal = world.estimate(contract)
        item = world.line(proposal)
        world.db.execute(sa.delete(ContextMember).where(ContextMember.position_item_id == item.id))
        world.db.flush()

        line = _loaded(world, item)[item.id]

        assert (line.context_id, line.family_id) == (None, None)

    def test_a_system_context_line_is_not_reference_but_its_neighbour_is(self, world):
        contract = world.contract()
        _, proposal = world.estimate(contract)
        system = world.line(proposal, kind="SYSTEM")
        plain = world.line(proposal)
        world.db.flush()

        found = load_reference_lines(world.db, _history(world.db), [system.id, plain.id], factors=None)

        assert set(found) == {plain.id}

    @pytest.mark.parametrize(
        "price", [D("NaN"), D("0"), D("-5"), D("Infinity"), None], ids=["nan", "zero", "negative", "inf", "empty"]
    )
    def test_an_unfit_price_is_not_reference(self, world, price):
        _, _, item = _scene(world, unit_cost=price)

        assert _loaded(world, item) == {}

    def test_zero_weight_is_not_reference_while_a_positive_one_is(self, world):
        contract = world.contract()
        _, proposal = world.estimate(contract)
        zero = world.line(proposal, suggested=D("0"), quantity=D("0"))
        weighty = world.line(proposal, suggested=D("0.5"))
        world.db.flush()

        found = load_reference_lines(world.db, _history(world.db), [zero.id, weighty.id], factors=None)

        assert set(found) == {weighty.id}

    def test_weight_falls_back_to_the_customer_quantity(self, world):
        contract = world.contract()
        _, proposal = world.estimate(contract)
        item = world.line(proposal, suggested=None, quantity=D("4"))
        world.db.flush()

        assert set(_loaded(world, item)) == {item.id}

    def test_unknown_vat_base_is_not_reference(self, world):
        contract = world.contract()
        _, proposal = world.estimate(contract, vat_rate=None)
        item = world.line(proposal)
        world.db.flush()

        assert _loaded(world, item) == {}

    def test_override_of_the_vat_base_rescues_the_unknown_rate(self, world):
        contract = world.contract()
        _, proposal = world.estimate(contract, vat_rate=None, override=D("20"))
        item = world.line(proposal, unit_cost=D("120"))
        world.db.flush()

        assert _loaded(world, item)[item.id].nominal == D("100")

    def test_nominal_below_one_ruble_is_not_reference_and_exactly_one_is(self, world):
        contract = world.contract()
        _, proposal = world.estimate(contract, vat_rate=D("0"))
        below = world.line(proposal, unit_cost=D("0.99"))
        exact = world.line(proposal, unit_cost=D("1.00"))
        world.db.flush()

        found = load_reference_lines(world.db, _history(world.db), [below.id, exact.id], factors=None)

        assert set(found) == {exact.id}
        assert found[exact.id].nominal == D("1.00")

    def test_the_minimum_applies_to_the_net_not_to_the_gross(self, world):
        """1,10 с НДС 20 % — нетто 0,9166…: брутто выше рубля, номинал ниже."""
        contract = world.contract()
        _, proposal = world.estimate(contract, vat_rate=D("20"))
        item = world.line(proposal, unit_cost=D("1.10"))
        world.db.flush()

        assert _loaded(world, item) == {}

    def test_a_line_without_zone_and_family_is_reference(self, world):
        _, _, item = _scene(world, chain=None)

        line = _loaded(world, item)[item.id]

        assert line.zone is None
        assert line.family_id is None
        assert line.chain_titles == frozenset()

    def test_a_chapter_item_is_carried(self, world):
        contract = world.contract()
        _, proposal = world.estimate(contract)
        chapter = world.chapter(proposal, None)
        item = world.line(proposal, chapter=chapter)
        world.db.flush()

        assert _loaded(world, item)[item.id].chapter_item_id == chapter.id


# ---------------------------------------------------------------------------
#  Состав истории
# ---------------------------------------------------------------------------


class TestHistoryScope:
    def test_a_row_of_a_superseded_estimate_is_not_reference(self, world):
        contract = world.contract()
        _, old_proposal = world.estimate(contract, amendment_no=None)
        _, new_proposal = world.estimate(contract, amendment_no=1)
        old = world.line(old_proposal)
        new = world.line(new_proposal)
        world.db.flush()

        found = load_reference_lines(world.db, _history(world.db), [old.id, new.id], factors=None)

        assert set(found) == {new.id}

    def test_a_row_of_a_contract_outside_the_given_contracts_is_not_reference(self, world):
        kept = world.contract()
        other = world.contract()
        _, kept_proposal = world.estimate(kept)
        _, other_proposal = world.estimate(other)
        mine = world.line(kept_proposal)
        foreign = world.line(other_proposal)
        world.db.flush()
        contracts = [c for c in _history(world.db) if c.contract_id == kept.id]

        found = load_reference_lines(world.db, contracts, [mine.id, foreign.id], factors=None)

        assert set(found) == {mine.id}

    def test_a_row_of_an_offer_estimate_is_not_reference(self, world):
        _, proposal = world.offer_estimate()
        item = world.line(proposal)
        world.db.flush()

        assert _loaded(world, item) == {}

    def test_unrequested_ids_are_not_returned(self, world):
        contract = world.contract()
        _, proposal = world.estimate(contract)
        asked = world.line(proposal)
        world.line(proposal)
        world.db.flush()

        assert set(_loaded(world, asked)) == {asked.id}

    def test_empty_inputs_give_empty_results_without_a_query(self, world):
        contracts = _history(world.db)
        with count_statements(world.db) as box:
            assert load_reference_lines(world.db, [], [1], factors=None) == {}
            assert load_reference_lines(world.db, contracts, [], factors=None) == {}
            assert load_link_values(world.db, []) == {}

        assert box[0] == 0


# ---------------------------------------------------------------------------
#  Цена: номинал и ряд ИПЦ
# ---------------------------------------------------------------------------


class TestNetPrice:
    def test_with_a_series_net_is_the_nominal_times_the_estimate_factor(self, world):
        _, estimate, item = _scene(world, unit_cost=D("120"))

        line = _loaded(world, item, factors={estimate.id: D("1.1")})[item.id]

        assert line.nominal == D("100")
        assert line.net == D("110.0")

    def test_the_factor_is_taken_per_estimate(self, world):
        first = world.contract()
        second = world.contract()
        first_estimate, first_proposal = world.estimate(first)
        second_estimate, second_proposal = world.estimate(second)
        a = world.line(first_proposal, unit_cost=D("120"))
        b = world.line(second_proposal, unit_cost=D("120"))
        world.db.flush()

        found = load_reference_lines(
            world.db,
            _history(world.db),
            [a.id, b.id],
            factors={first_estimate.id: D("2"), second_estimate.id: D("3")},
        )

        assert (found[a.id].net, found[b.id].net) == (D("200"), D("300"))

    def test_without_a_series_net_equals_nominal(self, world):
        _, _, item = _scene(world, unit_cost=D("120"))

        line = _loaded(world, item, factors=None)[item.id]

        assert line.net == line.nominal == D("100")

    def test_a_missing_factor_is_an_error_as_in_the_ladder(self, world):
        _, _, item = _scene(world)

        with pytest.raises(KeyError):
            _loaded(world, item, factors={})


# ---------------------------------------------------------------------------
#  Запрос
# ---------------------------------------------------------------------------


class TestQueryShape:
    def test_the_query_is_a_select_that_filters_conditions_one_to_five(self, world):
        contract = world.contract()
        _, proposal = world.estimate(contract)
        good = world.line(proposal)
        # условие 6 запросом не проверяется: номинал ниже рубля он отдаёт, отсекает загрузчик
        cheap_estimate_contract = world.contract()
        _, cheap_proposal = world.estimate(cheap_estimate_contract, vat_rate=D("0"))
        cheap = world.line(cheap_proposal, unit_cost=D("0.5"))
        world.db.flush()

        query = reference_lines_query(_history(world.db))

        assert isinstance(query, sa.Select)
        ids = {row.position_item_id for row in world.db.execute(query).all()}
        assert {good.id, cheap.id} <= ids

    def test_each_loader_runs_one_query(self, world, monkeypatch):
        from config import settings

        monkeypatch.setattr(settings, "TYPICAL_RATES_CACHE_TTL_S", 300)
        contract = world.contract()
        _, proposal = world.estimate(contract)
        items = [world.line(proposal) for _ in range(3)]
        world.db.flush()
        contracts = _history(world.db)

        ids = [i.id for i in items]
        typical_rates(world.db)  # ставки — кэш: холодный набор читает их отдельно (пул и единицы)
        with count_statements(world.db) as box:
            found = load_reference_lines(world.db, contracts, ids, factors=None)

        assert len(found) == 3
        assert box[0] == 1  # строки одним запросом; поправки — ещё один, только если есть строки с семьёй и вариантом

    def test_the_lump_check_adds_a_fixed_number_of_queries_not_one_per_line(self, world):
        from tests.integration.test_our_price_load import _simple_variant

        family, schema = world.family()
        _, variant = _simple_variant(world, family, schema)
        contract = world.contract()
        _, proposal = world.estimate(contract)
        bare = [world.line(proposal) for _ in range(3)]
        applicable = [world.line(proposal, family=family, variant=variant) for _ in range(3)]
        world.db.flush()
        contracts = _history(world.db)
        bare_ids = [i.id for i in bare]
        applicable_ids = [i.id for i in applicable]

        with count_statements(world.db) as box:
            load_reference_lines(world.db, contracts, bare_ids, factors=None)
        with count_statements(world.db) as applicable_box:
            found = load_reference_lines(world.db, contracts, applicable_ids, factors=None)

        assert all(found[pid].lump is not None for pid in applicable_ids)
        assert box[0] == 3  # строки + пул обычных ставок + единицы (холодный кэш); поправок не у кого читать
        # поправки трёх строк с семьёй и вариантом — один запрос на набор, а не по запросу на строку
        assert applicable_box[0] == 4


# ---------------------------------------------------------------------------
#  Значения параметров связи
# ---------------------------------------------------------------------------


def _valued(world):
    """Строка с контекстом семьи и двумя параметрами (главный и второстепенный)."""
    family, schema = world.family()
    main = world.param(schema, 1, "Толщина, мм")
    second = world.param(schema, 2, "Цвет", role="secondary")
    _, _, item = _scene(world, family=family)
    return schema, main, second, item


def _cpv(world, item, schema, parameter, *, value_id=None, source="manual", off_list=None):
    world.db.add(
        ContextParameterValue(
            context_id=item.context_id,
            schema_id=schema.id,
            parameter_id=parameter.id,
            value_id=value_id,
            source=source,
            off_list_text=off_list,
        )
    )
    world.db.flush()


class TestLinkValues:
    def test_main_and_secondary_values_come_with_text_name_and_source(self, world):
        schema, main, second, item = _valued(world)
        _cpv(world, item, schema, main, value_id=world.value(main, "100 мм"), source="name")
        _cpv(world, item, schema, second, value_id=world.value(second, "серый"), source="manual")

        found = load_link_values(world.db, [(item.id, main.id), (item.id, second.id)])

        assert found == {
            (item.id, main.id): ParamText("100 мм", "Толщина, мм", "name", False, False),
            (item.id, second.id): ParamText("серый", "Цвет", "manual", False, False),
        }

    def test_off_list_value_carries_its_text_and_the_mark(self, world):
        schema, main, _, item = _valued(world)
        _cpv(world, item, schema, main, source="off_list", off_list="около 100 мм")

        found = load_link_values(world.db, [(item.id, main.id)])

        assert found[(item.id, main.id)] == ParamText("около 100 мм", "Толщина, мм", "off_list", True, False)

    def test_path_conflict_is_marked_by_its_source(self, world):
        schema, main, _, item = _valued(world)
        _cpv(world, item, schema, main, source="path_conflict")

        found = load_link_values(world.db, [(item.id, main.id)])

        assert found[(item.id, main.id)] == ParamText(None, "Толщина, мм", "path_conflict", False, False)

    def test_unspecified_value_is_absent(self, world):
        schema, main, second, item = _valued(world)
        _cpv(world, item, schema, main, source="none")
        _cpv(world, item, schema, second, value_id=world.value(second, "серый"))

        found = load_link_values(world.db, [(item.id, main.id), (item.id, second.id)])

        assert set(found) == {(item.id, second.id)}

    def test_composite_value_is_marked(self, world):
        schema, main, _, item = _valued(world)
        part = world.value(main, "100 мм")
        composite = FamilyParameterValue(
            parameter_id=main.id,
            value="100 мм + 150 мм",
            value_norm="100 мм + 150 мм",
            origin="extension",
            composite_kind="all",
            composite_key=f"all:{part},{part + 1}",
        )
        world.db.add(composite)
        world.db.flush()
        _cpv(world, item, schema, main, value_id=composite.id)

        found = load_link_values(world.db, [(item.id, main.id)])

        assert found[(item.id, main.id)] == ParamText("100 мм + 150 мм", "Толщина, мм", "manual", False, True)

    def test_a_pair_without_a_value_row_is_absent(self, world):
        schema, main, second, item = _valued(world)
        _cpv(world, item, schema, main, value_id=world.value(main, "100 мм"))

        found = load_link_values(world.db, [(item.id, main.id), (item.id, second.id)])

        assert set(found) == {(item.id, main.id)}

    def test_only_the_asked_pairs_are_returned(self, world):
        schema, main, second, item = _valued(world)
        _cpv(world, item, schema, main, value_id=world.value(main, "100 мм"))
        _cpv(world, item, schema, second, value_id=world.value(second, "серый"))

        found = load_link_values(world.db, [(item.id, second.id)])

        assert set(found) == {(item.id, second.id)}

    def test_the_value_of_another_row_with_the_same_parameter_is_not_mixed_in(self, world):
        family, schema = world.family()
        main = world.param(schema, 1, "Толщина, мм")
        contract = world.contract()
        _, proposal = world.estimate(contract)
        first = world.line(proposal, family=family)
        second = world.line(proposal, family=family, title="Другая работа")
        world.db.flush()
        _cpv(world, first, schema, main, value_id=world.value(main, "100 мм"))
        _cpv(world, second, schema, main, value_id=world.value(main, "150 мм"))

        found = load_link_values(world.db, [(first.id, main.id), (second.id, main.id)])

        assert first.context_id != second.context_id
        assert found[(first.id, main.id)].text == "100 мм"
        assert found[(second.id, main.id)].text == "150 мм"

    def test_one_query_for_any_number_of_pairs(self, world):
        schema, main, second, item = _valued(world)
        _cpv(world, item, schema, main, value_id=world.value(main, "100 мм"))
        _cpv(world, item, schema, second, value_id=world.value(second, "серый"))

        pairs = [(item.id, main.id), (item.id, second.id)]
        with count_statements(world.db) as box:
            found = load_link_values(world.db, pairs)

        assert len(found) == 2
        assert box[0] == 1


# ---------------------------------------------------------------------------
#  Решение «комплект или ставка» справочной строки (спека строк-сумм §6 п. 1)
# ---------------------------------------------------------------------------


def _lump_scene(world):
    """Ключ (семья, м²): три ставки по 100 при весе 10 — обычная ставка 100; договор-носитель пула отдельный."""
    from tests.integration.test_our_price_load import _simple_variant

    family, schema = world.family()
    _, variant = _simple_variant(world, family, schema)
    carrier = world.contract()
    _, proposal = world.estimate(carrier, vat_rate=D("0"))
    for _ in range(3):
        world.line(proposal, family=family, variant=variant, unit_cost=D("100"), total=D("1000"))
    contract = world.contract()
    _, own = world.estimate(contract, vat_rate=D("0"))
    world.db.flush()
    return family, variant, own


class TestLumpOfReferenceLines:
    def test_a_weight_one_line_at_the_threshold_is_a_lump_and_one_step_below_is_a_rate(self, world):
        family, variant, proposal = _lump_scene(world)
        at = world.line(proposal, family=family, variant=variant, unit_cost=D("500"), total=D("500"), suggested=D("1"), quantity=D("1"))
        below = world.line(proposal, family=family, variant=variant, unit_cost=D("499.99"), total=D("499.99"), suggested=D("1"), quantity=D("1"))
        world.db.flush()

        found = load_reference_lines(world.db, _history(world.db), [at.id, below.id], factors=None)

        assert (found[at.id].weight, found[below.id].weight) == (D("1"), D("1"))
        assert found[at.id].lump.is_lump is True and found[at.id].lump.rule is True
        assert found[at.id].lump.net == D("500") and found[at.id].lump.typical.net == D("100")
        assert found[below.id].lump.is_lump is False
        assert found[at.id].family_title == family.title

    def test_a_heavy_line_is_a_rate_at_any_price(self, world):
        family, variant, proposal = _lump_scene(world)
        heavy = world.line(proposal, family=family, variant=variant, unit_cost=D("900"), total=D("9000"))
        world.db.flush()

        found = load_reference_lines(world.db, _history(world.db), [heavy.id], factors=None)

        assert found[heavy.id].weight == D("10")
        assert found[heavy.id].lump.rule is False and found[heavy.id].lump.is_lump is False

    def test_the_override_beats_the_rule_both_ways(self, world, admin_user):
        from models import LumpSumOverride

        family, variant, proposal = _lump_scene(world)
        big = world.line(proposal, family=family, variant=variant, unit_cost=D("900"), total=D("900"), suggested=D("1"), quantity=D("1"))
        small = world.line(proposal, family=family, variant=variant, unit_cost=D("100"), total=D("1000"))
        world.db.add_all(
            [
                LumpSumOverride(position_item_id=big.id, verdict="rate", set_by=admin_user.id),
                LumpSumOverride(position_item_id=small.id, verdict="lump", set_by=admin_user.id),
            ]
        )
        world.db.flush()

        found = load_reference_lines(world.db, _history(world.db), [big.id, small.id], factors=None)

        assert found[big.id].lump.rule is True and found[big.id].lump.is_lump is False
        assert found[big.id].lump.override.verdict == "rate"
        assert found[small.id].lump.rule is False and found[small.id].lump.is_lump is True
        assert found[small.id].lump.override.set_by_email == admin_user.email

    def test_a_line_without_a_family_or_a_variant_has_no_lump_check(self, world):
        family, variant, proposal = _lump_scene(world)
        bare = world.line(proposal, unit_cost=D("900"), total=D("900"), suggested=D("1"), quantity=D("1"))
        no_variant = world.line(
            proposal, family=family, unit_cost=D("900"), total=D("900"), suggested=D("1"), quantity=D("1")
        )
        world.db.flush()

        found = load_reference_lines(world.db, _history(world.db), [bare.id, no_variant.id], factors=None)

        assert found[bare.id].lump is None and found[bare.id].family_title is None
        assert found[no_variant.id].lump is None

    def test_a_line_in_an_archived_context_has_no_lump_check(self, world):
        family, variant, proposal = _lump_scene(world)
        archived = world.line(
            proposal, family=family, variant=variant, archived_context=True, unit_cost=D("900"), total=D("900"),
            suggested=D("1"), quantity=D("1"),
        )
        world.db.flush()

        found = load_reference_lines(world.db, _history(world.db), [archived.id], factors=None)

        assert found[archived.id].lump is None
