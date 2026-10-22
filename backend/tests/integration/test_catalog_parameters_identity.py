"""Идентичность варианта из активных главных параметров, пересчёт вариантов,
счётчик `revision` у писателей версии и слияние значений (спека 3в §2.2, §2.3).

Роль параметра в тестах выставляется прямо в БД: сервис правки схемы появляется
позже, а пересчёт и ключ от него не зависят.
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from models import (
    CatalogContext,
    ContextParameterValue,
    FamilyParameter,
    FamilyParameterSchema,
    FamilyParameterValue,
    WorkVariant,
    WorkVariantValue,
)
from services.work_families import WorkFamilyError
from services.work_variants import (
    RecomputeResult,
    active_main_ordinals,
    bump_revision,
    freeze_schema,
    get_or_create_variant,
    merge_parameter_values,
    rebuild_schema,
    recompute_variants,
)
from tests.integration.parameters_edit_support import draft, edit, same_drafts
from tests.integration.test_work_variants_api import BASE, _detail, _family_with_params
from tests.integration.test_work_variants_core import (
    _answer,
    _named,
    _parameter_in,
    _schema_answer,
    _table_rows,
)
from tests.integration.test_work_variants_material import _settings
from tests.integration.test_work_variants_schema import _param, _value
from tests.integration.test_work_variants_schema_life import (
    _active_world,
    _actor,
    _all_state,
    _Merge,
    _second_context,
    _settle_context,
    _variant_of,
    _variant_row,
    _variant_value_rows,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _set_role(db, parameter_id, role):
    db.execute(
        sa.update(FamilyParameter).where(FamilyParameter.id == parameter_id).values(role=role)
    )
    db.flush()
    db.expire_all()


def _archive_parameter(db, parameter_id):
    db.execute(
        sa.update(FamilyParameter)
        .where(FamilyParameter.id == parameter_id)
        .values(archived_at=sa.func.now())
    )
    db.flush()
    db.expire_all()


def _settle(db, world, context_id, thickness, material):
    """Контекст получает вариант и значения настоящим обработчиком ответа."""
    return _settle_context(
        db, world, context_id,
        _answer(_named(1, thickness), _named(2, material, "path")),
    )


def _recompute(db, world, **kwargs):
    return recompute_variants(
        db, family_id=world.family.id, schema_id=world.schema.id, **kwargs
    )


def _revision(db, schema_id) -> int:
    db.expire_all()
    return db.get(FamilyParameterSchema, schema_id).revision


def _two_contexts(db, factories):
    """Два контекста одной семьи: `(50 мм, бетон)` и `(50 мм, кирпич)`, у каждого
    свой вариант; оба параметра главные."""
    world = _active_world(db, factories)
    second = _second_context(db, factories, world)
    first_variant = _settle(db, world, world.context_id, "50 мм", "бетон")
    second_variant = _settle(db, world, second, "50 мм", "кирпич")
    return world, second, first_variant, second_variant


def _context_row(db, context_id, parameter_id) -> ContextParameterValue | None:
    db.expire_all()
    return db.execute(
        sa.select(ContextParameterValue).where(
            ContextParameterValue.context_id == context_id,
            ContextParameterValue.parameter_id == parameter_id,
        )
    ).scalar_one_or_none()


def _variant_value_pairs(db, variant_id) -> set[tuple[int, int | None]]:
    db.expire_all()
    return {
        (row.parameter_id, row.value_id)
        for row in db.execute(
            sa.select(WorkVariantValue.parameter_id, WorkVariantValue.value_id).where(
                WorkVariantValue.variant_id == variant_id
            )
        ).all()
    }


# ---------------------------------------------------------------------------
#  active_main_ordinals
# ---------------------------------------------------------------------------

class TestActiveMainOrdinals:
    def test_every_parameter_main_and_active_is_listed(self, db_session, factories):
        world = _active_world(db_session, factories)

        assert active_main_ordinals(db_session, world.schema.id) == {
            1: world.parameters[1], 2: world.parameters[2],
        }

    def test_a_secondary_parameter_is_not_listed(self, db_session, factories):
        world = _active_world(db_session, factories)
        _set_role(db_session, world.parameters[2], "secondary")

        assert active_main_ordinals(db_session, world.schema.id) == {1: world.parameters[1]}

    def test_an_archived_parameter_is_not_listed(self, db_session, factories):
        world = _active_world(db_session, factories)
        _archive_parameter(db_session, world.parameters[1])

        assert active_main_ordinals(db_session, world.schema.id) == {2: world.parameters[2]}

    def test_parameters_of_another_version_are_not_listed(self, db_session, factories):
        world = _active_world(db_session, factories)
        other = _active_world(db_session, factories)

        assert set(active_main_ordinals(db_session, world.schema.id).values()) == {
            world.parameters[1], world.parameters[2],
        }
        assert set(active_main_ordinals(db_session, other.schema.id).values()) == {
            other.parameters[1], other.parameters[2],
        }


# ---------------------------------------------------------------------------
#  Ключ и состав варианта
# ---------------------------------------------------------------------------

class TestVariantIdentity:
    def test_the_key_of_an_all_main_schema_keeps_its_format(self, db_session, factories):
        world = _active_world(db_session, factories)

        variant_id = _settle(db_session, world, world.context_id, "50 мм", "бетон")

        v50, concrete = world.values[(1, "50 мм")], world.values[(2, "бетон")]
        assert _variant_row(db_session, variant_id).values_key == f"1={v50}|2={concrete}"

    def test_rows_differing_only_by_a_secondary_value_are_one_variant(
        self, db_session, factories
    ):
        world = _active_world(db_session, factories)
        second = _second_context(db_session, factories, world)
        _set_role(db_session, world.parameters[2], "secondary")

        first_variant = _settle(db_session, world, world.context_id, "50 мм", "бетон")
        second_variant = _settle(db_session, world, second, "50 мм", "кирпич")

        assert first_variant == second_variant
        assert _variant_row(db_session, first_variant).values_key == (
            f"1={world.values[(1, '50 мм')]}"
        )

    def test_rows_differing_by_a_main_value_stay_two_variants(self, db_session, factories):
        world = _active_world(db_session, factories)
        second = _second_context(db_session, factories, world)
        _set_role(db_session, world.parameters[2], "secondary")

        first_variant = _settle(db_session, world, world.context_id, "50 мм", "бетон")
        second_variant = _settle(db_session, world, second, "100 мм", "бетон")

        assert first_variant != second_variant

    def test_variant_rows_are_written_for_the_main_parameter_only(self, db_session, factories):
        world = _active_world(db_session, factories)
        _set_role(db_session, world.parameters[2], "secondary")

        variant_id = _settle(db_session, world, world.context_id, "50 мм", "бетон")

        assert _variant_value_pairs(db_session, variant_id) == {
            (world.parameters[1], world.values[(1, "50 мм")])
        }

    def test_the_secondary_answer_is_still_written_to_the_context(self, db_session, factories):
        world = _active_world(db_session, factories)
        _set_role(db_session, world.parameters[2], "secondary")

        _settle(db_session, world, world.context_id, "50 мм", "бетон")

        row = _context_row(db_session, world.context_id, world.parameters[2])
        assert row is not None and row.value_id == world.values[(2, "бетон")]

    def test_a_set_with_a_secondary_ordinal_is_refused(self, db_session, factories):
        world = _active_world(db_session, factories)
        _set_role(db_session, world.parameters[2], "secondary")

        with pytest.raises(ValueError, match="активными главными"):
            get_or_create_variant(
                db_session, family_id=world.family.id, schema_id=world.schema.id,
                value_ids_by_ordinal={
                    1: world.values[(1, "50 мм")], 2: world.values[(2, "бетон")],
                },
            )

    def test_a_set_missing_a_main_ordinal_is_refused(self, db_session, factories):
        world = _active_world(db_session, factories)

        with pytest.raises(ValueError, match="активными главными"):
            get_or_create_variant(
                db_session, family_id=world.family.id, schema_id=world.schema.id,
                value_ids_by_ordinal={1: world.values[(1, "50 мм")]},
            )

    def test_an_archived_parameter_and_a_new_one_with_max_plus_one_give_different_keys(
        self, db_session, factories
    ):
        # Номер архивного параметра не используется повторно: новый получает
        # `max + 1`, и «не уточнено» у обоих не сводит ключи.
        world = _active_world(db_session, factories)
        v50 = world.values[(1, "50 мм")]
        old, _ = get_or_create_variant(
            db_session, family_id=world.family.id, schema_id=world.schema.id,
            value_ids_by_ordinal={1: v50, 2: None},
        )
        _archive_parameter(db_session, world.parameters[2])
        new_parameter = _param(db_session, world.schema, 3, "Марка")
        _value(db_session, new_parameter, "М100")

        new, created = get_or_create_variant(
            db_session, family_id=world.family.id, schema_id=world.schema.id,
            value_ids_by_ordinal={1: v50, 3: None},
        )

        assert created and new.id != old.id
        assert (old.values_key, new.values_key) == (f"1={v50}|2=?", f"1={v50}|3=?")
        assert _variant_value_pairs(db_session, new.id) == {
            (world.parameters[1], v50), (new_parameter.id, None),
        }
        assert _variant_value_pairs(db_session, old.id) == {
            (world.parameters[1], v50), (world.parameters[2], None),
        }


# ---------------------------------------------------------------------------
#  recompute_variants
# ---------------------------------------------------------------------------

class TestRecomputeVariants:
    def test_a_role_change_joins_variants_and_archives_the_emptied_ones_without_a_link(
        self, db_session, factories
    ):
        world, second, first_variant, second_variant = _two_contexts(db_session, factories)
        _set_role(db_session, world.parameters[2], "secondary")

        result = _recompute(db_session, world)

        assert result == RecomputeResult(
            variants_before=2, variants_after=1, moved_contexts=2, waiting_contexts=0
        )
        joined = _variant_of(db_session, world.context_id)
        assert joined == _variant_of(db_session, second)
        assert joined not in (first_variant, second_variant)
        assert _variant_row(db_session, joined).values_key == f"1={world.values[(1, '50 мм')]}"
        for old in (first_variant, second_variant):
            row = _variant_row(db_session, old)
            assert (row.status, row.merged_into_id) == ("archived", None)
            assert row.archived_at is not None

    def test_nothing_changes_when_the_main_set_is_the_same(self, db_session, factories):
        world, second, first_variant, second_variant = _two_contexts(db_session, factories)
        before = _all_state(db_session)

        result = _recompute(db_session, world)

        assert result == RecomputeResult(
            variants_before=2, variants_after=2, moved_contexts=0, waiting_contexts=0
        )
        assert _all_state(db_session) == before

    def test_a_row_waiting_for_a_refresh_keeps_its_context_in_the_old_variant(
        self, db_session, factories
    ):
        world, second, first_variant, second_variant = _two_contexts(db_session, factories)
        _set_role(db_session, world.parameters[2], "secondary")
        db_session.execute(
            sa.update(ContextParameterValue)
            .where(
                ContextParameterValue.context_id == second,
                ContextParameterValue.parameter_id == world.parameters[1],
            )
            .values(refresh_requested_at=sa.func.now())
        )
        db_session.flush()

        result = _recompute(db_session, world)

        assert result == RecomputeResult(
            variants_before=2, variants_after=2, moved_contexts=1, waiting_contexts=1
        )
        assert _variant_of(db_session, second) == second_variant
        assert _variant_row(db_session, second_variant).status == "active"
        assert _variant_of(db_session, world.context_id) not in (first_variant, second_variant)
        assert _variant_row(db_session, first_variant).status == "archived"

    def test_a_context_without_a_row_for_a_main_parameter_waits(self, db_session, factories):
        world, second, first_variant, second_variant = _two_contexts(db_session, factories)
        _set_role(db_session, world.parameters[2], "secondary")
        db_session.execute(
            sa.delete(ContextParameterValue).where(
                ContextParameterValue.context_id == second,
                ContextParameterValue.parameter_id == world.parameters[1],
            )
        )
        db_session.flush()

        result = _recompute(db_session, world)

        assert (result.moved_contexts, result.waiting_contexts) == (1, 1)
        assert _variant_of(db_session, second) == second_variant

    def test_a_row_without_a_value_counts_as_an_answer_and_gives_an_unknown_key(
        self, db_session, factories
    ):
        world, second, first_variant, second_variant = _two_contexts(db_session, factories)
        _set_role(db_session, world.parameters[2], "secondary")
        db_session.execute(
            sa.update(ContextParameterValue)
            .where(
                ContextParameterValue.context_id == second,
                ContextParameterValue.parameter_id == world.parameters[1],
            )
            .values(value_id=None, source="none")
        )
        db_session.flush()

        result = _recompute(db_session, world)

        assert (result.moved_contexts, result.waiting_contexts) == (2, 0)
        assert _variant_row(db_session, _variant_of(db_session, second)).values_key == "1=?"

    def test_context_ids_narrow_the_recompute(self, db_session, factories):
        world, second, first_variant, second_variant = _two_contexts(db_session, factories)
        _set_role(db_session, world.parameters[2], "secondary")

        result = _recompute(db_session, world, context_ids=[world.context_id])

        assert (result.moved_contexts, result.waiting_contexts) == (1, 0)
        assert _variant_of(db_session, world.context_id) not in (first_variant, second_variant)
        assert _variant_of(db_session, second) == second_variant
        assert _variant_row(db_session, second_variant).status == "active"
        assert _variant_row(db_session, first_variant).status == "archived"

    def test_an_archived_context_and_a_context_without_a_variant_are_not_touched(
        self, db_session, factories
    ):
        world, second, first_variant, second_variant = _two_contexts(db_session, factories)
        third = _second_context(db_session, factories, world)
        db_session.execute(
            sa.update(CatalogContext)
            .where(CatalogContext.id == second)
            .values(archived_at=sa.func.now())
        )
        db_session.flush()
        _set_role(db_session, world.parameters[2], "secondary")

        result = _recompute(db_session, world)

        assert (result.moved_contexts, result.waiting_contexts) == (1, 0)
        assert _variant_of(db_session, second) == second_variant
        assert _variant_of(db_session, third) is None
        assert _variant_row(db_session, second_variant).status == "active"

    def test_the_role_back_and_forth_returns_the_same_variants(self, db_session, factories):
        world, second, first_variant, second_variant = _two_contexts(db_session, factories)
        history = {
            variant_id: (
                _variant_row(db_session, variant_id).values_key,
                _variant_value_rows(db_session, variant_id),
            )
            for variant_id in (first_variant, second_variant)
        }

        _set_role(db_session, world.parameters[2], "secondary")
        _recompute(db_session, world)
        _set_role(db_session, world.parameters[2], "main")
        result = _recompute(db_session, world)

        assert result.moved_contexts == 2
        assert _variant_of(db_session, world.context_id) == first_variant
        assert _variant_of(db_session, second) == second_variant
        for variant_id, (key, rows) in history.items():
            row = _variant_row(db_session, variant_id)
            assert (row.status, row.merged_into_id, row.archived_at) == ("active", None, None)
            assert (row.values_key, _variant_value_rows(db_session, variant_id)) == (key, rows)

    def test_the_call_does_not_commit(self, db_session, factories, monkeypatch):
        # `in_transaction()` здесь не свидетель: сессия теста вложена в
        # точку сохранения и остаётся в транзакции и после `commit()`.
        world, *_ = _two_contexts(db_session, factories)
        _set_role(db_session, world.parameters[2], "secondary")
        commits: list[int] = []
        monkeypatch.setattr(db_session, "commit", lambda: commits.append(1))

        result = _recompute(db_session, world)

        assert result.moved_contexts == 2
        assert commits == []


# ---------------------------------------------------------------------------
#  Слияние значений второстепенного параметра
# ---------------------------------------------------------------------------

class TestMergeOfASecondaryParameter:
    def _scene(self, db, factories):
        world, second, first_variant, second_variant = _two_contexts(db, factories)
        _set_role(db, world.parameters[2], "secondary")
        _recompute(db, world)
        return world, second

    def _merge_materials(self, db, world, factories):
        return merge_parameter_values(
            db, parameter_id=world.parameters[2],
            source_value_id=world.values[(2, "кирпич")],
            target_value_id=world.values[(2, "бетон")], actor_id=_actor(factories),
        )

    def test_no_variant_changes_and_only_the_context_rows_are_rewritten(
        self, db_session, factories
    ):
        world, second = self._scene(db_session, factories)
        variants = _table_rows(db_session, WorkVariant)
        variant_values = _table_rows(db_session, WorkVariantValue)
        placement = {
            context_id: _variant_of(db_session, context_id)
            for context_id in (world.context_id, second)
        }
        # Архивные варианты прежних ролей хранят строки с этим параметром.
        assert len(variants) == 3

        merged = self._merge_materials(db_session, world, factories)

        assert merged == {}
        assert _table_rows(db_session, WorkVariant) == variants
        assert _table_rows(db_session, WorkVariantValue) == variant_values
        assert {c: _variant_of(db_session, c) for c in placement} == placement
        row = _context_row(db_session, second, world.parameters[2])
        assert row.value_id == world.values[(2, "бетон")]
        source = db_session.get(FamilyParameterValue, world.values[(2, "кирпич")])
        assert source.merged_into_id == world.values[(2, "бетон")]

    def test_the_same_merge_of_a_main_parameter_does_rewrite_variants(
        self, db_session, factories
    ):
        world, second, first_variant, second_variant = _two_contexts(db_session, factories)
        variants = _table_rows(db_session, WorkVariant)

        merged = self._merge_materials(db_session, world, factories)

        assert merged == {second_variant: first_variant}
        assert _table_rows(db_session, WorkVariant) != variants


# ---------------------------------------------------------------------------
#  revision
# ---------------------------------------------------------------------------

class TestRevision:
    def test_bump_adds_one_and_returns_the_new_value(self, db_session, factories):
        world = _active_world(db_session, factories)
        assert _revision(db_session, world.schema.id) == 0

        assert bump_revision(db_session, world.schema.id) == 1
        assert bump_revision(db_session, world.schema.id) == 2

        assert _revision(db_session, world.schema.id) == 2

    def test_bump_touches_only_its_own_version(self, db_session, factories):
        world = _active_world(db_session, factories)
        other = _active_world(db_session, factories)

        bump_revision(db_session, world.schema.id)

        assert _revision(db_session, other.schema.id) == 0

    def test_a_merge_adds_one(self, db_session, factories):
        scene = _Merge(db_session, factories)

        scene.merge()

        assert _revision(db_session, scene.world.schema.id) == 1

    def test_a_refused_merge_adds_nothing(self, db_session, factories):
        scene = _Merge(db_session, factories)

        with pytest.raises(WorkFamilyError) as raised:
            scene.merge(source=scene.target, target=scene.target)

        assert raised.value.code == "merge_value_cycle"
        assert _revision(db_session, scene.world.schema.id) == 0

    def test_a_new_value_from_a_model_answer_adds_one(self, db_session, factories):
        world = _active_world(db_session, factories)

        _settle_context(
            db_session, world, world.context_id,
            _answer((1, "new", "75 мм", "name"), _named(2, "бетон", "path")),
        )

        assert _revision(db_session, world.schema.id) == 1

    def test_two_new_values_in_one_answer_add_two(self, db_session, factories):
        world = _active_world(db_session, factories)

        _settle_context(
            db_session, world, world.context_id,
            _answer((1, "new", "75 мм", "name"), (2, "new", "газобетон", "path")),
        )

        assert _revision(db_session, world.schema.id) == 2

    def test_a_listed_value_adds_nothing(self, db_session, factories):
        world = _active_world(db_session, factories)

        _settle_context(
            db_session, world, world.context_id,
            _answer(_named(1, "50 мм"), _named(2, "бетон", "path")),
        )

        assert _revision(db_session, world.schema.id) == 0

    def test_a_new_value_that_is_already_in_the_list_adds_nothing(self, db_session, factories):
        world = _active_world(db_session, factories)

        _settle_context(
            db_session, world, world.context_id,
            _answer((1, "new", "50 мм", "name"), _named(2, "бетон", "path")),
        )

        assert _revision(db_session, world.schema.id) == 0

    def test_a_cosmetic_rename_adds_one(self, db_session, factories):
        world = _active_world(db_session, factories)
        drafts = same_drafts()
        drafts[0] = draft(1, "ТОЛЩИНА", *drafts[0].values)

        edit(db_session, world.family.id, drafts, _actor(factories))

        assert _revision(db_session, world.schema.id) == 1

    def test_an_edit_without_changes_adds_nothing(self, db_session, factories):
        world = _active_world(db_session, factories)

        edit(db_session, world.family.id, same_drafts(), _actor(factories))

        assert _revision(db_session, world.schema.id) == 0


# ---------------------------------------------------------------------------
#  Слияние с выведенным значением
# ---------------------------------------------------------------------------

def _retire(db, value_id):
    db.execute(
        sa.update(FamilyParameterValue)
        .where(FamilyParameterValue.id == value_id)
        .values(retired_at=sa.func.now())
    )
    db.flush()
    db.expire_all()


class TestMergeWithARetiredValue:
    def test_a_retired_source_is_refused_and_nothing_changes(self, db_session, factories):
        scene = _Merge(db_session, factories)
        scene.settle(scene.world.context_id, "50 мм")
        _retire(db_session, scene.source)
        before = _all_state(db_session)

        with pytest.raises(WorkFamilyError) as raised:
            scene.merge()

        assert raised.value.code == "merge_value_retired"
        assert _all_state(db_session) == before

    def test_a_retired_target_is_refused_and_nothing_changes(self, db_session, factories):
        scene = _Merge(db_session, factories)
        scene.settle(scene.world.context_id, "50 мм")
        _retire(db_session, scene.target)
        before = _all_state(db_session)

        with pytest.raises(WorkFamilyError) as raised:
            scene.merge()

        assert raised.value.code == "merge_value_retired"
        assert _all_state(db_session) == before

    def test_a_target_whose_canonical_value_is_retired_is_refused(self, db_session, factories):
        scene = _Merge(db_session, factories)
        synonym = _value(
            db_session, db_session.get(FamilyParameter, scene.parameter_id), "75 мм",
            merged_into_id=scene.target,
        )
        _retire(db_session, scene.target)
        before = _all_state(db_session)

        with pytest.raises(WorkFamilyError) as raised:
            scene.merge(target=synonym.id)

        assert raised.value.code == "merge_value_retired"
        assert _all_state(db_session) == before

    def test_a_retired_value_of_another_parameter_does_not_block_the_merge(
        self, db_session, factories
    ):
        scene = _Merge(db_session, factories)
        _retire(db_session, scene.world.values[(2, "кирпич")])

        scene.merge()

        assert scene.value(scene.source).merged_into_id == scene.target

    def test_the_route_answers_409_with_the_code_and_changes_nothing(
        self, admin_client, db_session, factories
    ):
        scene = _family_with_params(db_session, factories)
        parameter_id = db_session.execute(
            sa.select(FamilyParameter.id).where(
                FamilyParameter.schema_id == scene.schema.id, FamilyParameter.ordinal == 1
            )
        ).scalar_one()
        first, second = db_session.execute(
            sa.select(FamilyParameterValue.id)
            .where(FamilyParameterValue.parameter_id == parameter_id)
            .order_by(FamilyParameterValue.id)
        ).scalars().all()
        _retire(db_session, second)
        db_session.commit()  # отказ маршрута откатывает транзакцию сессии

        for source, target in ((second, first), (first, second)):
            response = admin_client.post(
                f"{BASE}/families/{scene.family.id}/schema/values/merge",
                json={
                    "parameter_id": parameter_id, "source_value_id": source,
                    "target_value_id": target,
                },
            )

            assert response.status_code == 409, response.text
            assert _detail(response)["code"] == "merge_value_retired"
        db_session.expire_all()
        merged = db_session.execute(
            sa.select(FamilyParameterValue.merged_into_id).where(
                FamilyParameterValue.parameter_id == parameter_id
            )
        ).scalars().all()
        assert merged == [None, None]

# ---------------------------------------------------------------------------
#  Ревью задачи 2: границы выбора пересчёта и его блокировки
# ---------------------------------------------------------------------------

class TestRecomputeScope:
    """Пересчёт трогает только контексты в вариантах переданной версии своей
    семьи (спека 3в §2.3: «у которого вариант действующей версии»)."""

    def _two_versions(self, db, factories):
        # Оба контекста получили варианты версии 1; затем пересборка заводит
        # версию 2, и только первый контекст получает вариант по ней. Второй
        # остаётся в варианте версии 1 до ответа модели.
        from types import SimpleNamespace

        world, second, first_v1, second_v1 = _two_contexts(db, factories)
        building = rebuild_schema(db, family_id=world.family.id, actor_id=_actor(factories))
        freeze_schema(
            db, schema_id=building.id, guard=None, settings=_settings(),
            answer=_schema_answer(
                _parameter_in(1, "Толщина", "50 мм", "100 мм", "75 мм"),
                _parameter_in(2, "Материал", "бетон", "кирпич"),
            ),
        )
        current = db.get(FamilyParameterSchema, building.id)
        assert current.status == "frozen" and current.id != world.schema.id
        new_world = SimpleNamespace(**{**vars(world), "schema": current})
        new_variant = _settle(db, new_world, world.context_id, "50 мм", "бетон")
        parameters = {
            row.ordinal: row.id
            for row in db.execute(
                sa.select(FamilyParameter.ordinal, FamilyParameter.id).where(
                    FamilyParameter.schema_id == current.id
                )
            ).all()
        }
        return world, new_world, second, second_v1, new_variant, parameters

    def test_a_context_in_a_variant_of_another_version_is_neither_moved_nor_waiting(
        self, db_session, factories
    ):
        world, new_world, second, second_v1, new_variant, parameters = self._two_versions(
            db_session, factories
        )
        _set_role(db_session, parameters[2], "secondary")

        result = recompute_variants(
            db_session, family_id=world.family.id, schema_id=new_world.schema.id
        )

        assert result == RecomputeResult(
            variants_before=1, variants_after=1, moved_contexts=1, waiting_contexts=0
        )
        assert _variant_of(db_session, second) == second_v1
        assert _variant_row(db_session, second_v1).status == "active"
        assert _variant_row(db_session, new_variant).status == "archived"

    def test_a_foreign_family_id_touches_nothing(self, db_session, factories):
        world, second, first_variant, second_variant = _two_contexts(db_session, factories)
        other = _active_world(db_session, factories)
        _set_role(db_session, world.parameters[2], "secondary")
        before = _all_state(db_session)

        result = recompute_variants(
            db_session, family_id=other.family.id, schema_id=world.schema.id
        )

        assert (result.moved_contexts, result.waiting_contexts) == (0, 0)
        assert _all_state(db_session) == before


class TestRecomputeLocks:
    def test_the_contexts_are_locked_for_update_in_ascending_id_order(
        self, db_session, factories
    ):
        from tests.integration.test_work_variants_core import _capturing_sql, _lock_sequence

        world, second, first_variant, second_variant = _two_contexts(db_session, factories)
        _set_role(db_session, world.parameters[2], "secondary")

        with _capturing_sql(db_session) as statements:
            _recompute(db_session, world)

        assert ("catalog_contexts", "UPDATE") in _lock_sequence(statements)
        [locking] = [
            text for text in statements
            if "FROM catalog_contexts" in text and text.strip().endswith("FOR UPDATE")
        ]
        assert "ORDER BY catalog_contexts.id" in locking
