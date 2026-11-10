"""Составное значение главного параметра (спека упрощения каталога §2.3): инварианты,
пять мест существующего кода и перенос в новую версию схемы.

Эталоны — независимые литералы: тексты составных, ключи и нормы записаны руками.
Значения сцены заводятся по возрастанию `id` в порядке перечисления, поэтому порядок
частей в тексте и в ключе виден из самого теста.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from models import (
    CatalogAutoAction,
    ContextParameterValue,
    FamilyParameter,
    FamilyParameterSchema,
    FamilyParameterValue,
    FamilyValuePart,
    SemanticEvent,
    WorkVariant,
)
from services.composite_values import (
    COMPOSITE_JOIN,
    REFUSE_MERGE_COMPOSITE_MISMATCH,
    REFUSE_SCHEMA_COMPOSITE_TEXT,
    composite_key,
    composite_parts,
    find_or_create_composite,
)
from services.releasable import load_releasable_material
from services.schema_edit import (
    _Move,
    _ParameterPlan,
    _Rebuild,
    _ValueRow,
    _write_rebuild,
)
from services.variant_request import load_human_parameters, load_schema_parameters
from services.work_families import WorkFamilyError
from services.work_variants import (
    freeze_schema,
    get_or_create_variant,
    merge_parameter_values,
    rebuild_schema,
)
from tests.integration.parameters_edit_support import draft
from tests.integration.test_work_variants_api import BASE, _detail
from tests.integration.test_work_variants_core import (
    _attach_variant,
    _parameter_in,
    _schema_answer,
)
from tests.integration.test_work_variants_material import _settings
from tests.integration.test_work_variants_schema import _param, _value
from tests.integration.test_work_variants_schema_life import (
    _active_world,
    _actor,
    _claim,
    _jobs,
    _second_context,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _scene(db, factories, names=("плиты покрытий", "перекрытия", "балки"), *, human=False):
    """Активная семья с одним главным параметром «Тип конструкции» и простыми
    значениями `names`; `ids` — `{имя: id}`."""
    world = _active_world(db, factories, ((1, "Тип конструкции", list(names)),))
    if human:
        db.execute(
            sa.update(FamilyParameter)
            .where(FamilyParameter.id == world.parameters[1])
            .values(origin="human")
        )
        db.flush()
    return SimpleNamespace(
        world=world, family=world.family, schema=world.schema, parameter_id=world.parameters[1],
        ids={name: world.values[(1, name)] for name in names}, actor=_actor(factories),
    )


def _make(db, scene, *names, kind="all", context_id=None, source_text=None, new_part_ids=()):
    return find_or_create_composite(
        db, parameter_id=scene.parameter_id, kind=kind,
        part_ids=[scene.ids[name] for name in names], context_id=context_id,
        source_text=source_text, new_part_ids=new_part_ids,
    )


def _row(db, value_id) -> FamilyParameterValue:
    db.expire_all()
    return db.get(FamilyParameterValue, value_id)


def _parts_of(db, value_id):
    db.expire_all()
    return sorted(
        db.execute(
            sa.select(FamilyValuePart.part_value_id).where(FamilyValuePart.value_id == value_id)
        ).scalars()
    )


def _journal(db, family_id):
    db.expire_all()
    return list(
        db.execute(
            sa.select(CatalogAutoAction)
            .where(CatalogAutoAction.family_id == family_id)
            .order_by(CatalogAutoAction.id)
        ).scalars()
    )


def _revision(db, schema_id) -> int:
    db.expire_all()
    return db.execute(
        sa.select(FamilyParameterSchema.revision).where(FamilyParameterSchema.id == schema_id)
    ).scalar_one()


def _event_count(db, family_id) -> int:
    db.expire_all()
    return db.execute(
        sa.select(sa.func.count()).select_from(SemanticEvent).where(
            SemanticEvent.event_type == "family_schema_value_added",
            SemanticEvent.family_id == family_id,
        )
    ).scalar_one()


def _put_row(db, scene, context_id, value_id, source="name"):
    """Строка значения контекста по главному параметру сцены."""
    db.add(
        ContextParameterValue(
            context_id=context_id, schema_id=scene.schema.id,
            parameter_id=scene.parameter_id, value_id=value_id, source=source,
        )
    )
    db.flush()


def _context_value(db, scene, context_id):
    db.expire_all()
    return db.execute(
        sa.select(ContextParameterValue.value_id).where(
            ContextParameterValue.context_id == context_id,
            ContextParameterValue.parameter_id == scene.parameter_id,
        )
    ).scalar_one()


def _variant_for(db, scene, context_id, value_id):
    variant, _ = get_or_create_variant(
        db, family_id=scene.family.id, schema_id=scene.schema.id,
        value_ids_by_ordinal={1: value_id},
    )
    _attach_variant(db, context_id, variant)
    return variant.id


def _refusal(call) -> str:
    with pytest.raises(WorkFamilyError) as raised:
        call()
    return raised.value.code


# ---------------------------------------------------------------------------
#  Инварианты §2.3
# ---------------------------------------------------------------------------

class TestInvariants:
    def test_the_text_of_all_is_the_parts_joined_by_plus_in_id_order(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки"))

        # части названы в обратном порядке id — текст и ключ идут по возрастанию id
        composite_id = _make(db_session, scene, "балки", "плиты")

        row = _row(db_session, composite_id)
        assert row.value == "плиты + балки"
        assert row.value_norm == "плиты + балки"
        assert row.composite_kind == "all"
        assert row.composite_key == f"all:{scene.ids['плиты']},{scene.ids['балки']}"
        assert row.origin == "extension"
        assert _parts_of(db_session, composite_id) == sorted(scene.ids.values())

    def test_the_text_of_any_is_the_parts_joined_by_or(self, db_session, factories):
        scene = _scene(db_session, factories, ("Knauf П113", "Gyproc П-1М-1ГСП"))

        composite_id = _make(db_session, scene, "Knauf П113", "Gyproc П-1М-1ГСП", kind="any")

        row = _row(db_session, composite_id)
        assert row.value == "Knauf П113 или Gyproc П-1М-1ГСП"
        assert row.value_norm == "knauf п113 или gyproc п-1м-1гсп"
        assert row.composite_key.startswith("any:")

    def test_the_join_strings_are_the_ones_of_the_spec(self):
        assert COMPOSITE_JOIN == {"all": " + ", "any": " или "}

    def test_all_and_any_over_the_same_parts_are_two_values(self, db_session, factories):
        scene = _scene(db_session, factories, ("Knauf", "Gyproc"))

        together = _make(db_session, scene, "Knauf", "Gyproc", kind="all")
        either = _make(db_session, scene, "Knauf", "Gyproc", kind="any")

        assert together != either
        assert (_row(db_session, together).value, _row(db_session, either).value) == (
            "Knauf + Gyproc", "Knauf или Gyproc",
        )

    def test_the_same_parts_in_another_order_give_the_same_id_and_one_journal_record(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты", "балки"))

        first = _make(db_session, scene, "плиты", "балки")
        second = _make(db_session, scene, "балки", "плиты")

        assert first == second
        assert [action.kind for action in _journal(db_session, scene.family.id)] == ["composite"]
        composites = db_session.execute(
            sa.select(sa.func.count()).select_from(FamilyParameterValue).where(
                FamilyParameterValue.composite_kind.is_not(None)
            )
        ).scalar_one()
        assert composites == 1

    def test_the_key_is_the_kind_and_the_sorted_ids(self):
        assert composite_key("all", [9, 3, 3, 5]) == "all:3,5,9"
        assert composite_key("any", [4, 2]) == "any:2,4"

    def test_a_composite_is_a_context_value_and_a_variant_key_like_any_value(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        composite_id = _make(db_session, scene, "плиты", "балки")

        _put_row(db_session, scene, scene.world.context_id, composite_id)
        variant_id = _variant_for(db_session, scene, scene.world.context_id, composite_id)

        assert _context_value(db_session, scene, scene.world.context_id) == composite_id
        db_session.expire_all()
        assert db_session.get(WorkVariant, variant_id).values_key == f"1={composite_id}"

    def test_a_composite_cannot_be_a_part(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки", "колонны"))
        composite_id = _make(db_session, scene, "плиты", "балки")

        with pytest.raises(ValueError):
            find_or_create_composite(
                db_session, parameter_id=scene.parameter_id, kind="all",
                part_ids=[composite_id, scene.ids["колонны"]], context_id=None, source_text=None,
            )

    def test_a_retired_value_cannot_be_a_part(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        db_session.execute(
            sa.update(FamilyParameterValue)
            .where(FamilyParameterValue.id == scene.ids["балки"])
            .values(retired_at=sa.func.now())
        )

        with pytest.raises(ValueError):
            _make(db_session, scene, "плиты", "балки")

    def test_a_value_of_another_parameter_cannot_be_a_part(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        other = _param(db_session, scene.schema, 2, "Материал")
        foreign = _value(db_session, other, "бетон")

        with pytest.raises(ValueError):
            find_or_create_composite(
                db_session, parameter_id=scene.parameter_id, kind="all",
                part_ids=[scene.ids["плиты"], foreign.id], context_id=None, source_text=None,
            )

    def test_one_part_named_twice_is_not_a_composite(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки"))

        with pytest.raises(ValueError):
            _make(db_session, scene, "плиты", "плиты")

    def test_two_names_of_one_value_are_not_a_composite(self, db_session, factories):
        scene = _scene(db_session, factories, ("плита", "плиты", "балки"))
        merge_parameter_values(
            db_session, parameter_id=scene.parameter_id, source_value_id=scene.ids["плита"],
            target_value_id=scene.ids["плиты"], actor_id=scene.actor,
        )

        with pytest.raises(ValueError):
            _make(db_session, scene, "плита", "плиты")

    def test_a_merged_part_is_replaced_by_its_canonical_value(self, db_session, factories):
        scene = _scene(db_session, factories, ("плита", "плиты", "балки"))
        merge_parameter_values(
            db_session, parameter_id=scene.parameter_id, source_value_id=scene.ids["плита"],
            target_value_id=scene.ids["плиты"], actor_id=scene.actor,
        )

        composite_id = _make(db_session, scene, "плита", "балки")

        assert _row(db_session, composite_id).composite_key == (
            f"all:{scene.ids['плиты']},{scene.ids['балки']}"
        )

    def test_an_unknown_kind_is_refused(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки"))

        with pytest.raises(ValueError):
            _make(db_session, scene, "плиты", "балки", kind="both")

    def test_a_text_taken_by_an_active_simple_value_returns_that_value(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты", "балки", "плиты + балки"))

        value_id = _make(db_session, scene, "плиты", "балки")

        assert value_id == scene.ids["плиты + балки"]
        assert _journal(db_session, scene.family.id) == []

    def test_a_text_taken_by_a_retired_value_is_refused(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки", "плиты + балки"))
        db_session.execute(
            sa.update(FamilyParameterValue)
            .where(FamilyParameterValue.id == scene.ids["плиты + балки"])
            .values(retired_at=sa.func.now())
        )

        with pytest.raises(ValueError):
            _make(db_session, scene, "плиты", "балки")


class TestCreationWrites:
    def test_creation_writes_one_journal_record_with_the_payload(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        context_id = scene.world.context_id

        composite_id = _make(
            db_session, scene, "плиты", "балки", context_id=context_id,
            source_text="плиты и балки", new_part_ids=[scene.ids["балки"]],
        )

        [action] = _journal(db_session, scene.family.id)
        assert (action.kind, action.family_id, action.context_id) == (
            "composite", scene.family.id, context_id,
        )
        assert (action.parameter_id, action.value_id) == (scene.parameter_id, composite_id)
        assert action.payload == {
            "text": "плиты и балки", "kind": "all",
            "part_ids": [scene.ids["плиты"], scene.ids["балки"]],
            "new_part_ids": [scene.ids["балки"]], "context_ids": [context_id],
        }
        assert (action.undone_at, action.undone_by, action.restored_at) == (None, None, None)

    def test_without_a_context_the_record_has_no_context_and_an_empty_list(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты", "балки"))

        _make(db_session, scene, "плиты", "балки")

        [action] = _journal(db_session, scene.family.id)
        assert action.context_id is None
        assert action.payload["context_ids"] == []
        assert action.payload["text"] == "плиты + балки"
        assert action.payload["new_part_ids"] == []

    def test_creation_extends_the_list_a_found_composite_does_not(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        revision = _revision(db_session, scene.schema.id)
        events = _event_count(db_session, scene.family.id)

        _make(db_session, scene, "плиты", "балки")

        assert _revision(db_session, scene.schema.id) == revision + 1
        assert _event_count(db_session, scene.family.id) == events + 1
        _make(db_session, scene, "балки", "плиты")
        assert _revision(db_session, scene.schema.id) == revision + 1
        assert _event_count(db_session, scene.family.id) == events + 1

    def test_composite_parts_lists_only_composites_with_ascending_parts(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты", "перекрытия", "балки"))
        composite_id = _make(db_session, scene, "балки", "плиты", "перекрытия", kind="any")

        parts = composite_parts(db_session, [composite_id, scene.ids["плиты"]])

        assert parts == {
            composite_id: ("any", (scene.ids["плиты"], scene.ids["перекрытия"], scene.ids["балки"]))
        }
        assert composite_parts(db_session, []) == {}


# ---------------------------------------------------------------------------
#  Место 1: модель составных не видит
# ---------------------------------------------------------------------------

class TestTheModelNeverSeesAComposite:
    def test_the_schema_block_carries_the_parts_and_not_the_composite(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        _make(db_session, scene, "плиты", "балки")

        [parameter] = load_schema_parameters(db_session, [scene.schema.id])[scene.schema.id]

        assert parameter.values == ("плиты", "балки")

    def test_the_human_parameters_carry_the_parts_and_not_the_composite(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты", "балки"), human=True)
        _make(db_session, scene, "плиты", "балки")

        assert load_human_parameters(db_session, scene.family.id) == (
            ("Тип конструкции", ("плиты", "балки")),
        )

    def test_the_releasable_request_carries_the_parts_and_not_the_composite(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        _make(db_session, scene, "плиты", "балки")

        material = load_releasable_material(db_session, scene.family.id, scene.schema.id)

        assert material is not None
        assert [parameter.values for parameter in material.parameters] == [("плиты", "балки")]


# ---------------------------------------------------------------------------
#  Место 2: слияние части
# ---------------------------------------------------------------------------

class TestMergeOfAPart:
    def test_the_part_is_replaced_and_the_text_and_the_key_follow(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты покрытий", "перекрытия", "балки"))
        composite_id = _make(db_session, scene, "плиты покрытий", "балки")

        merge_parameter_values(
            db_session, parameter_id=scene.parameter_id,
            source_value_id=scene.ids["плиты покрытий"],
            target_value_id=scene.ids["перекрытия"], actor_id=scene.actor,
        )

        row = _row(db_session, composite_id)
        assert row.value == "перекрытия + балки"
        assert row.value_norm == "перекрытия + балки"
        assert row.composite_key == f"all:{scene.ids['перекрытия']},{scene.ids['балки']}"
        assert (row.merged_into_id, row.retired_at) == (None, None)
        assert _parts_of(db_session, composite_id) == sorted(
            [scene.ids["перекрытия"], scene.ids["балки"]]
        )

    def test_a_composite_without_the_merged_part_is_not_touched(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты покрытий", "перекрытия", "балки", "колонны"))
        untouched = _make(db_session, scene, "балки", "колонны")
        before = _row(db_session, untouched).composite_key

        merge_parameter_values(
            db_session, parameter_id=scene.parameter_id,
            source_value_id=scene.ids["плиты покрытий"],
            target_value_id=scene.ids["перекрытия"], actor_id=scene.actor,
        )

        assert _row(db_session, untouched).composite_key == before
        assert _row(db_session, untouched).value == "балки + колонны"

    def test_a_three_part_composite_stays_a_composite_of_the_two_left(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты", "перекрытия", "балки"))
        composite_id = _make(db_session, scene, "плиты", "перекрытия", "балки")

        merge_parameter_values(
            db_session, parameter_id=scene.parameter_id, source_value_id=scene.ids["плиты"],
            target_value_id=scene.ids["перекрытия"], actor_id=scene.actor,
        )

        row = _row(db_session, composite_id)
        assert row.merged_into_id is None
        assert row.composite_key == f"all:{scene.ids['перекрытия']},{scene.ids['балки']}"
        assert row.value == "перекрытия + балки"

    def test_an_existing_composite_of_the_new_state_takes_the_old_one_in(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты покрытий", "перекрытия", "балки"))
        old = _make(db_session, scene, "плиты покрытий", "балки")
        taken = _make(db_session, scene, "перекрытия", "балки")
        second = _second_context(db_session, factories, scene.world)
        first = scene.world.context_id
        _put_row(db_session, scene, first, old)
        _put_row(db_session, scene, second, taken)
        old_variant = _variant_for(db_session, scene, first, old)
        taken_variant = _variant_for(db_session, scene, second, taken)

        merge_parameter_values(
            db_session, parameter_id=scene.parameter_id,
            source_value_id=scene.ids["плиты покрытий"],
            target_value_id=scene.ids["перекрытия"], actor_id=scene.actor,
        )

        assert _row(db_session, old).merged_into_id == taken
        assert _row(db_session, taken).merged_into_id is None
        assert _context_value(db_session, scene, first) == taken
        assert _context_value(db_session, scene, second) == taken
        db_session.expire_all()
        merged_variant = db_session.get(WorkVariant, old_variant)
        assert (merged_variant.status, merged_variant.merged_into_id) == ("archived", taken_variant)
        assert db_session.get(WorkVariant, taken_variant).status == "active"
        assert _row(db_session, taken).composite_key == (
            f"all:{scene.ids['перекрытия']},{scene.ids['балки']}"
        )

    @pytest.mark.parametrize(("source", "target"), [("плиты", "балки"), ("балки", "плиты")])
    def test_a_part_merged_into_another_part_turns_the_composite_into_that_simple_value(
        self, db_session, factories, source, target
    ):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        composite_id = _make(db_session, scene, "плиты", "балки")
        first = scene.world.context_id
        _put_row(db_session, scene, first, composite_id)
        composite_variant = _variant_for(db_session, scene, first, composite_id)
        simple_variant = _variant_for(
            db_session, scene, _second_context(db_session, factories, scene.world),
            scene.ids[target],
        )

        merge_parameter_values(
            db_session, parameter_id=scene.parameter_id, source_value_id=scene.ids[source],
            target_value_id=scene.ids[target], actor_id=scene.actor,
        )

        assert _row(db_session, composite_id).merged_into_id == scene.ids[target]
        assert _context_value(db_session, scene, first) == scene.ids[target]
        db_session.expire_all()
        assert db_session.get(WorkVariant, composite_variant).merged_into_id == simple_variant

    @pytest.mark.parametrize("composite_is_source", [True, False])
    def test_the_merge_through_the_api_refuses_a_composite_and_a_simple_value(
        self, admin_client, db_session, factories, composite_is_source
    ):
        scene = _scene(db_session, factories, ("плиты", "балки", "колонны"))
        composite_id = _make(db_session, scene, "плиты", "балки")
        simple_id = scene.ids["колонны"]
        source, target = (
            (composite_id, simple_id) if composite_is_source else (simple_id, composite_id)
        )

        response = admin_client.post(
            f"{BASE}/families/{scene.family.id}/schema/values/merge",
            json={
                "parameter_id": scene.parameter_id, "source_value_id": source,
                "target_value_id": target,
            },
        )

        assert response.status_code == 422, response.text
        assert _detail(response)["code"] == REFUSE_MERGE_COMPOSITE_MISMATCH

    def test_the_merge_refuses_composites_of_different_kinds(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        together = _make(db_session, scene, "плиты", "балки", kind="all")
        either = _make(db_session, scene, "плиты", "балки", kind="any")

        code = _refusal(
            lambda: merge_parameter_values(
                db_session, parameter_id=scene.parameter_id, source_value_id=together,
                target_value_id=either, actor_id=scene.actor,
            )
        )

        assert code == REFUSE_MERGE_COMPOSITE_MISMATCH
        assert _row(db_session, together).merged_into_id is None

    def test_two_composites_of_one_kind_merge_through_the_api(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты", "перекрытия", "балки"))
        first = _make(db_session, scene, "плиты", "балки")
        second = _make(db_session, scene, "перекрытия", "балки")

        response = admin_client.post(
            f"{BASE}/families/{scene.family.id}/schema/values/merge",
            json={
                "parameter_id": scene.parameter_id, "source_value_id": first,
                "target_value_id": second,
            },
        )

        assert response.status_code == 200, response.text
        assert _row(db_session, first).merged_into_id == second

    def test_two_simple_values_still_merge_through_the_api(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты", "перекрытия"))

        response = admin_client.post(
            f"{BASE}/families/{scene.family.id}/schema/values/merge",
            json={
                "parameter_id": scene.parameter_id, "source_value_id": scene.ids["плиты"],
                "target_value_id": scene.ids["перекрытия"],
            },
        )

        assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
#  Место 3: вывод части
# ---------------------------------------------------------------------------

class TestRetirementOfAPart:
    def _plan(self, scene, *retired_names):
        value = lambda name: _ValueRow(  # noqa: E731
            id=scene.ids[name], parameter_id=scene.parameter_id, value=name, value_norm=name,
            merged=False, retired=False,
        )
        return _ParameterPlan(
            ordinal=1, parameter_id=scene.parameter_id, draft=draft(1, "Тип конструкции"),
            existing=None,
            rebuild=_Rebuild(
                moves=[_Move(source=value(name), target=None, rows=[]) for name in retired_names],
                bounds=[],
            ),
        )

    def test_a_composite_with_a_retired_part_is_retired_at_the_same_moment(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты", "балки", "колонны"))
        composite_id = _make(db_session, scene, "плиты", "балки")
        untouched = _make(db_session, scene, "балки", "колонны")
        now = db_session.execute(sa.select(sa.func.now())).scalar_one()

        _write_rebuild(db_session, self._plan(scene, "плиты"), {}, now)

        assert _row(db_session, scene.ids["плиты"]).retired_at == now
        assert _row(db_session, composite_id).retired_at == now
        assert _row(db_session, untouched).retired_at is None

    def test_the_rows_of_contexts_on_the_retired_composite_become_refreshing(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        composite_id = _make(db_session, scene, "плиты", "балки")
        _put_row(db_session, scene, scene.world.context_id, composite_id)
        now = db_session.execute(sa.select(sa.func.now())).scalar_one()

        _write_rebuild(db_session, self._plan(scene, "плиты"), {}, now)

        db_session.expire_all()
        marked = db_session.execute(
            sa.select(ContextParameterValue.refresh_requested_at).where(
                ContextParameterValue.context_id == scene.world.context_id
            )
        ).scalar_one()
        assert marked is not None

    def test_a_rebuild_to_ranges_retires_the_parts_and_the_composite_together(
        self, db_session, factories
    ):
        from tests.integration.parameters_edit_support import edit

        scene = _scene(db_session, factories, ("10", "20", "30"))
        composite_id = _make(db_session, scene, "10", "20")

        edit(
            db_session, scene.family.id,
            [draft(1, "Тип конструкции", kind="range", rng=(10, 30, 10))], scene.actor,
        )

        db_session.expire_all()
        stamps = {
            _row(db_session, scene.ids["10"]).retired_at,
            _row(db_session, scene.ids["20"]).retired_at,
            _row(db_session, composite_id).retired_at,
        }
        assert len(stamps) == 1 and None not in stamps


# ---------------------------------------------------------------------------
#  Место 4: редактор схемы
# ---------------------------------------------------------------------------

class TestSchemaEditor:
    def _body(self, scene, texts):
        return {
            "parameters": [
                {
                    "ordinal": 1, "name": "Тип конструкции", "role": "main",
                    "value_kind": "exact", "closed": False, "values": texts,
                }
            ]
        }

    def test_the_schema_response_gives_the_composition_of_a_composite(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        composite_id = _make(db_session, scene, "плиты", "балки", kind="any")

        body = admin_client.get(f"{BASE}/families/{scene.family.id}/schema").json()

        values = {v["id"]: v for v in body["parameters"][0]["values"]}
        assert values[composite_id]["composite"] == {
            "kind": "any", "part_ids": [scene.ids["плиты"], scene.ids["балки"]],
        }
        assert values[scene.ids["плиты"]]["composite"] is None

    @pytest.mark.parametrize("through_patch", [False, True])
    def test_a_respelled_text_of_a_composite_is_refused_by_the_preview_and_the_patch(
        self, admin_client, db_session, factories, through_patch
    ):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        _make(db_session, scene, "плиты", "балки")
        url = f"{BASE}/families/{scene.family.id}/schema"
        body = self._body(scene, ["плиты", "балки", "Плиты + Балки"])

        response = (
            admin_client.patch(url, json={**body, "preview_hash": "x"})
            if through_patch
            else admin_client.post(f"{url}/edit-preview", json=body)
        )

        assert response.status_code == 422, response.text
        assert _detail(response)["code"] == REFUSE_SCHEMA_COMPOSITE_TEXT

    def test_the_same_text_of_a_composite_passes_and_changes_nothing(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        composite_id = _make(db_session, scene, "плиты", "балки")
        url = f"{BASE}/families/{scene.family.id}/schema"
        body = self._body(scene, ["плиты", "балки", "плиты + балки"])

        preview = admin_client.post(f"{url}/edit-preview", json=body)

        assert preview.status_code == 200, preview.text
        assert _row(db_session, composite_id).value == "плиты + балки"

    def test_the_spelling_of_a_simple_value_is_still_editable(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        _make(db_session, scene, "плиты", "балки")
        url = f"{BASE}/families/{scene.family.id}/schema"
        body = self._body(scene, ["Плиты", "балки", "плиты + балки"])

        preview = admin_client.post(f"{url}/edit-preview", json=body)

        assert preview.status_code == 200, preview.text


# ---------------------------------------------------------------------------
#  Место 5: новая версия схемы переносит составные
# ---------------------------------------------------------------------------

class TestNewSchemaVersion:
    def _new_version(self, db, factories, scene):
        new = rebuild_schema(db, family_id=scene.family.id, actor_id=scene.actor)
        [job] = _jobs(db, "family_schema")
        outcome = freeze_schema(
            db, schema_id=new.id,
            answer=_schema_answer(_parameter_in(1, "Другой параметр", "а")),
            guard=_claim(db, job), settings=_settings(),
        )
        assert outcome.applied
        return new

    def _carried(self, db, schema_id):
        db.expire_all()
        parameter = db.execute(
            sa.select(FamilyParameter).where(
                FamilyParameter.schema_id == schema_id, FamilyParameter.ordinal == 1
            )
        ).scalar_one()
        values = {
            row.value: row
            for row in db.execute(
                sa.select(FamilyParameterValue).where(
                    FamilyParameterValue.parameter_id == parameter.id
                )
            ).scalars()
        }
        return parameter, values

    def test_a_composite_of_a_human_parameter_is_carried_with_its_parts_and_key(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты", "перекрытия", "балки"), human=True)
        _make(db_session, scene, "плиты", "балки", kind="any")

        new = self._new_version(db_session, factories, scene)

        _parameter, values = self._carried(db_session, new.id)
        carried = values["плиты или балки"]
        new_parts = sorted((values["плиты"].id, values["балки"].id))
        assert carried.composite_kind == "any"
        assert carried.composite_key == f"any:{new_parts[0]},{new_parts[1]}"
        assert carried.origin == "extension"
        assert _parts_of(db_session, carried.id) == new_parts
        assert all(part not in scene.ids.values() for part in new_parts)

    def test_a_merged_part_does_not_travel_and_the_composite_follows_its_canonical_part(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плита", "плиты", "балки"), human=True)
        _make(db_session, scene, "плита", "балки")
        merge_parameter_values(
            db_session, parameter_id=scene.parameter_id, source_value_id=scene.ids["плита"],
            target_value_id=scene.ids["плиты"], actor_id=scene.actor,
        )

        new = self._new_version(db_session, factories, scene)

        _parameter, values = self._carried(db_session, new.id)
        assert set(values) == {"плиты", "балки", "плиты + балки"}
        assert _parts_of(db_session, values["плиты + балки"].id) == sorted(
            (values["плиты"].id, values["балки"].id)
        )

    def test_the_rows_of_a_carried_composite_point_at_the_carried_composite(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты", "балки"), human=True)
        composite_id = _make(db_session, scene, "плиты", "балки")
        _put_row(db_session, scene, scene.world.context_id, composite_id)

        new = self._new_version(db_session, factories, scene)

        parameter, values = self._carried(db_session, new.id)
        carried_row = db_session.execute(
            sa.select(ContextParameterValue).where(
                ContextParameterValue.context_id == scene.world.context_id,
                ContextParameterValue.parameter_id == parameter.id,
            )
        ).scalar_one()
        assert carried_row.value_id == values["плиты + балки"].id

    def test_a_composite_that_lost_a_part_is_not_carried_and_its_rows_become_none(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты", "балки"), human=True)
        composite_id = _make(db_session, scene, "плиты", "балки")
        _put_row(db_session, scene, scene.world.context_id, composite_id)
        # часть выведена без вывода составного: в новую версию она не едет
        db_session.execute(
            sa.update(FamilyParameterValue)
            .where(FamilyParameterValue.id == scene.ids["балки"])
            .values(retired_at=sa.func.now())
        )

        new = self._new_version(db_session, factories, scene)

        parameter, values = self._carried(db_session, new.id)
        assert set(values) == {"плиты"}
        carried_row = db_session.execute(
            sa.select(ContextParameterValue).where(
                ContextParameterValue.context_id == scene.world.context_id,
                ContextParameterValue.parameter_id == parameter.id,
            )
        ).scalar_one()
        assert (carried_row.value_id, carried_row.source, carried_row.off_list_text) == (
            None, "none", None,
        )

    def test_a_model_parameter_does_not_travel_with_its_composite(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки"), human=False)
        _make(db_session, scene, "плиты", "балки")

        new = self._new_version(db_session, factories, scene)

        _parameter, values = self._carried(db_session, new.id)
        assert set(values) == {"а"}


# ---------------------------------------------------------------------------
#  Значение-замена вето расширяет список
# ---------------------------------------------------------------------------

class TestVetoValueExtendsTheList:
    def _vetoed_scene(self, db, factories, *, drop_value):
        from services.human_decisions import add_veto

        scene = _scene(db, factories, ("плиты", "балки", "плиты и балки"))
        add_veto(
            db, parameter_id=scene.parameter_id, kind="all",
            part_ids=[scene.ids["плиты"], scene.ids["балки"]],
            value_id=scene.ids["плиты и балки"], user_id=factories.UserFactory.create().id,
        )
        if drop_value:
            db.execute(
                sa.delete(FamilyParameterValue).where(
                    FamilyParameterValue.id == scene.ids["плиты и балки"]
                )
            )
        return scene

    def test_a_value_created_through_the_veto_bumps_the_revision_writes_one_event_and_is_listed(
        self, db_session, factories
    ):
        scene = self._vetoed_scene(db_session, factories, drop_value=True)
        revision = _revision(db_session, scene.schema.id)
        events = _event_count(db_session, scene.family.id)
        added: list[int] = []

        created = find_or_create_composite(
            db_session, parameter_id=scene.parameter_id, kind="all",
            part_ids=[scene.ids["плиты"], scene.ids["балки"]], context_id=None,
            source_text="плиты и балки", added=added,
        )

        assert added == [created]
        assert _revision(db_session, scene.schema.id) == revision + 1
        assert _event_count(db_session, scene.family.id) == events + 1
        assert _journal(db_session, scene.family.id) == []

    def test_a_normal_composite_is_not_listed(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        added: list[int] = []

        find_or_create_composite(
            db_session, parameter_id=scene.parameter_id, kind="all",
            part_ids=list(scene.ids.values()), context_id=None, source_text=None, added=added,
        )

        assert added == []

    def test_an_existing_value_of_the_veto_is_not_listed_and_does_not_bump(
        self, db_session, factories
    ):
        scene = self._vetoed_scene(db_session, factories, drop_value=False)
        revision = _revision(db_session, scene.schema.id)
        events = _event_count(db_session, scene.family.id)
        added: list[int] = []

        value_id = find_or_create_composite(
            db_session, parameter_id=scene.parameter_id, kind="all",
            part_ids=[scene.ids["плиты"], scene.ids["балки"]], context_id=None,
            source_text=None, added=added,
        )

        assert value_id == scene.ids["плиты и балки"]
        assert added == []
        assert _revision(db_session, scene.schema.id) == revision
        assert _event_count(db_session, scene.family.id) == events
