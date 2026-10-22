"""Значения не из списка и формы ответов API схемы (спека 3в §2.7, §2.10).

Здесь: «Слить с…» (`resolve_off_list` и маршрут `off-list/resolve`), расширенная
форма `SchemaOut` (параметры с ролью, видом, границами, агрегатом «не из списка»,
числа на значение), варианты только из главных, карточка контекста с ролью,
«уточняется» и текстом не из списка, слияние значений архивного параметра.

Числа в ожиданиях — независимые литералы, выведенные из сцены (членства и
значения считаются руками, а не кодом, который проверяется).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import sqlalchemy as sa

import services.schema_edit as schema_edit_module
from crud.work_variants import context_variant_out, schema_out, variants_out
from models import (
    CatalogContext,
    ContextMember,
    ContextParameterValue,
    FamilyParameterSchema,
    FamilyParameterValue,
    SemanticJob,
    WorkVariant,
)
from services.semantic_events import EVENT_REQUIRED_KEYS
from services.semantic_reconcile import reconcile_or_defer
from services.work_families import WorkFamilyError
from services.work_variants import merge_parameter_values, rebuild_schema
from tests.integration.parameters_edit_support import draft, edit
from tests.integration.test_catalog_parameters_edit import (
    _apply_code,
    _context_row,
    _events,
    _is_revision_read,
    _live_values_jobs,
    _parameter,
    _revision,
    _set_row,
    _update_parameter,
    _values,
)
from tests.integration.test_work_variants_api import (
    BASE,
    _building,
    _detail,
    _family_with_params,
    _schemaless_family,
)
from tests.integration.test_work_variants_concurrency import _Scene
from tests.integration.test_work_variants_core import (
    _answer,
    _lock_sequence,
    _named,
    _table_rows,
)
from tests.integration.test_work_variants_material import (
    _bind,
    _capturing_sql,
    _chain_context,
    _uid,
)
from tests.integration.test_work_variants_schema_life import (
    _active_world,
    _actor,
    _all_state,
    _settle_context,
    _variant_of,
    _variant_row,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
#  Сцена: значения не из списка
# ---------------------------------------------------------------------------

def _context_with_members(db, factories, world, members: int) -> int:
    """Контекст семьи мира с заданным числом членств (строк каталога)."""
    context_id, _ = _chain_context(
        db, factories, title=f"Строка {_uid()}", path_specs=[((), members)]
    )
    _bind(db, factories, context_id, family=world.family)
    return context_id


def _member_count(db, context_id) -> int:
    return db.execute(
        sa.select(sa.func.count()).select_from(ContextMember).where(
            ContextMember.context_id == context_id
        )
    ).scalar_one()


def _off_list_scene(db, factories):
    """Закрытый «Материал»; контексты (членств в скобках):
    базовый — 50 мм, бетон (2); c0 — 50 мм, «Газобетон» (1); c1 — 100 мм,
    «ГАЗОБЕТОН» (1); c2 — 50 мм, «Керамзит» (3); c3 — 50 мм, «Газобетон» (1).
    Три написания одной нормы «газобетон» и одно «керамзит»."""
    world = _active_world(db, factories)
    material = world.parameters[2]
    _update_parameter(db, material, closed=True)
    sizes = (1, 1, 3, 1)
    contexts = [_context_with_members(db, factories, world, n) for n in sizes]
    base = _settle_context(
        db, world, world.context_id,
        _answer(_named(1, "50 мм"), _named(2, "бетон", "path")),
    )
    thickness = ("50 мм", "100 мм", "50 мм", "50 мм")
    texts = ("Газобетон", "ГАЗОБЕТОН", "Керамзит", "Газобетон")
    for context_id, mm, text in zip(contexts, thickness, texts, strict=True):
        _settle_context(db, world, context_id, _answer(_named(1, mm), (2, "new", text, "path")))
    for context_id, size in zip(contexts, sizes, strict=True):
        assert _member_count(db, context_id) == size
    assert _member_count(db, world.context_id) == 2
    return SimpleNamespace(
        world=world, contexts=contexts, material=material, thickness=world.parameters[1],
        base_variant=base, family_id=world.family.id, schema_id=world.schema.id,
    )


def _parameter_of(body, ordinal):
    return next(p for p in body["parameters"] if p["ordinal"] == ordinal)


def _value_of(body, ordinal, text):
    return next(v for v in _parameter_of(body, ordinal)["values"] if v["value"] == text)


def _resolve(db, scene, text_norm="газобетон", target="кирпич", *, actor_id, parameter=None):
    return schema_edit_module.resolve_off_list(
        db, family_id=scene.family_id, parameter_id=parameter or scene.material,
        text_norm=text_norm,
        target_value_id=_values(db, scene.material)[target].id if isinstance(target, str) else target,
        actor_id=actor_id,
    )


# ---------------------------------------------------------------------------
#  Формы SchemaOut
# ---------------------------------------------------------------------------

class TestSchemaForm:
    def test_the_keys_are_the_old_ones_plus_the_new_ones(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)

        body = schema_out(db_session, scene.family_id)

        assert set(body) == {
            "family_id", "status", "version", "ready_to_build", "building",
            "values_jobs_live", "parameters", "revision",
        }
        assert set(body["parameters"][0]) == {
            "id", "ordinal", "name", "values", "role", "value_kind", "closed", "range",
            "origin", "off_list", "refreshing",
        }
        assert set(body["parameters"][0]["values"][0]) == {
            "id", "value", "origin", "merged_into_id", "range_lo", "range_hi", "retired",
            "contexts", "rows",
        }
        [off_list] = _parameter_of(body, 2)["off_list"][:1]
        assert set(off_list) == {"text_norm", "text", "contexts", "rows"}

    def test_a_family_without_a_current_version_has_revision_zero(self, db_session):
        family = _schemaless_family(db_session)

        body = schema_out(db_session, family.id)

        assert (body["status"], body["parameters"], body["revision"]) == (None, [], 0)

    def test_the_parameter_fields_come_from_the_row(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)

        body = schema_out(db_session, scene.family_id)

        thickness, material = _parameter_of(body, 1), _parameter_of(body, 2)
        assert (thickness["role"], thickness["value_kind"], thickness["closed"]) == (
            "main", "exact", False,
        )
        assert (thickness["range"], thickness["origin"]) == (None, "model")
        assert material["closed"] is True

    def test_a_human_secondary_parameter_is_given_with_its_role_and_origin(
        self, db_session, factories
    ):
        scene = _off_list_scene(db_session, factories)
        edit(
            db_session, scene.family_id,
            [*_same_with_material_closed(), draft(None, "Марка", "М100", role="secondary")],
            _actor(factories),
        )

        body = schema_out(db_session, scene.family_id)

        mark = _parameter_of(body, 3)
        assert (mark["role"], mark["origin"], mark["name"]) == ("secondary", "human", "Марка")

    def test_a_range_parameter_is_given_with_its_bounds_and_the_bounds_of_its_values(
        self, db_session, factories
    ):
        scene = _off_list_scene(db_session, factories)
        edit(
            db_session, scene.family_id,
            [*_same_with_material_closed(), draft(None, "Диаметр", kind="range", rng=(120, 200, 40))],
            _actor(factories),
        )

        body = schema_out(db_session, scene.family_id)

        diameter = _parameter_of(body, 3)
        assert (diameter["value_kind"], diameter["closed"]) == ("range", True)
        assert diameter["range"] == {"start": "120", "stop": "200", "step": "40"}
        assert {(v["range_lo"], v["range_hi"]) for v in diameter["values"]} == {
            (None, "120"), ("120", "160"), ("160", "200"), ("200", None),
        }
        exact = _value_of(body, 1, "50 мм")
        assert (exact["range_lo"], exact["range_hi"]) == (None, None)

    def test_the_counts_of_contexts_and_rows_per_value(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)

        body = schema_out(db_session, scene.family_id)

        counts = {
            (ordinal, v["value"]): (v["contexts"], v["rows"])
            for ordinal in (1, 2)
            for v in _parameter_of(body, ordinal)["values"]
        }
        assert counts == {
            (1, "50 мм"): (4, 7),     # базовый (2), c0 (1), c2 (3), c3 (1)
            (1, "100 мм"): (1, 1),    # c1
            (2, "бетон"): (1, 2),     # базовый
            (2, "кирпич"): (0, 0),
        }

    def test_an_archived_context_is_not_counted(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        db_session.execute(
            sa.update(CatalogContext)
            .where(CatalogContext.id == scene.contexts[1])
            .values(archived_at=sa.func.now())
        )
        db_session.flush()

        body = schema_out(db_session, scene.family_id)

        assert (_value_of(body, 1, "100 мм")["contexts"], _value_of(body, 1, "100 мм")["rows"]) == (
            0, 0,
        )
        assert _value_of(body, 1, "50 мм")["contexts"] == 4
        gas = next(e for e in _parameter_of(body, 2)["off_list"] if e["text_norm"] == "газобетон")
        assert (gas["contexts"], gas["rows"]) == (2, 2)

    def test_the_off_list_texts_are_aggregated_by_norm(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)

        body = schema_out(db_session, scene.family_id)

        assert _parameter_of(body, 1)["off_list"] == []
        assert _parameter_of(body, 2)["off_list"] == [
            {"text_norm": "газобетон", "text": "Газобетон", "contexts": 3, "rows": 3},
            {"text_norm": "керамзит", "text": "Керамзит", "contexts": 1, "rows": 3},
        ]

    def test_the_shown_text_is_the_alphabetically_first_spelling_on_a_tie(
        self, db_session, factories
    ):
        scene = _off_list_scene(db_session, factories)
        db_session.execute(
            sa.update(CatalogContext)
            .where(CatalogContext.id == scene.contexts[3])
            .values(archived_at=sa.func.now())
        )
        db_session.flush()

        body = schema_out(db_session, scene.family_id)

        # остались «Газобетон» (c0) и «ГАЗОБЕТОН» (c1): по одному контексту
        gas = next(e for e in _parameter_of(body, 2)["off_list"] if e["text_norm"] == "газобетон")
        assert (gas["text"], gas["contexts"]) == ("ГАЗОБЕТОН", 2)

    def test_refreshing_counts_the_marked_contexts_of_the_parameter(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        for context_id in (scene.contexts[0], scene.contexts[2]):
            _set_row(
                db_session, context_id, scene.material, refresh_requested_at=sa.func.now()
            )
        _set_row(
            db_session, scene.contexts[3], scene.thickness, refresh_requested_at=sa.func.now()
        )
        archived = scene.contexts[1]
        _set_row(db_session, archived, scene.material, refresh_requested_at=sa.func.now())
        db_session.execute(
            sa.update(CatalogContext).where(CatalogContext.id == archived)
            .values(archived_at=sa.func.now())
        )
        db_session.flush()

        body = schema_out(db_session, scene.family_id)

        assert (_parameter_of(body, 1)["refreshing"], _parameter_of(body, 2)["refreshing"]) == (
            1, 2,
        )

    def test_retired_values_and_archived_parameters_are_not_given(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        db_session.execute(
            sa.update(FamilyParameterValue)
            .where(FamilyParameterValue.id == _values(db_session, scene.material)["кирпич"].id)
            .values(retired_at=sa.func.now())
        )
        _update_parameter(db_session, scene.thickness, archived_at=sa.func.now())

        body = schema_out(db_session, scene.family_id)

        assert [p["ordinal"] for p in body["parameters"]] == [2]
        assert [v["value"] for v in body["parameters"][0]["values"]] == ["бетон"]
        assert [v["retired"] for v in body["parameters"][0]["values"]] == [False]

    def test_a_merged_value_stays_in_the_list_with_its_target(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        values = _values(db_session, scene.material)
        merge_parameter_values(
            db_session, parameter_id=scene.material, source_value_id=values["кирпич"].id,
            target_value_id=values["бетон"].id, actor_id=_actor(factories),
        )

        body = schema_out(db_session, scene.family_id)

        merged = _value_of(body, 2, "кирпич")
        assert merged["merged_into_id"] == values["бетон"].id
        assert (merged["contexts"], merged["rows"]) == (0, 0)

    def test_the_revision_is_the_one_of_the_current_version(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        assert schema_out(db_session, scene.family_id)["revision"] == 0

        _resolve(db_session, scene, actor_id=_actor(factories))

        assert schema_out(db_session, scene.family_id)["revision"] == 1


def _same_with_material_closed():
    return [
        draft(1, "Толщина", "50 мм", "100 мм"),
        draft(2, "Материал", "бетон", "кирпич", closed=True),
    ]


# ---------------------------------------------------------------------------
#  Число запросов не зависит от числа значений
# ---------------------------------------------------------------------------

def _queries_of_schema_out(db, factories, *, values: int, off_list_texts: int):
    params = (
        (1, "Толщина", [f"т{i}" for i in range(values)]),
        (2, "Материал", [f"м{i}" for i in range(values)]),
    )
    world = _active_world(db, factories, params=params)
    _update_parameter(db, world.parameters[2], closed=True)
    for index in range(off_list_texts):
        context_id = _context_with_members(db, factories, world, 1 + index)
        _settle_context(
            db, world, context_id,
            _answer(_named(1, f"т{index}"), (2, "new", f"вне списка {index}", "path")),
        )
    with _capturing_sql(db) as statements:
        body = schema_out(db, world.family.id)
    return len(statements), body


class TestQueryCount:
    def test_the_count_is_the_same_for_five_and_for_thirty_values(self, db_session, factories):
        few, body_few = _queries_of_schema_out(db_session, factories, values=5, off_list_texts=1)
        many, body_many = _queries_of_schema_out(
            db_session, factories, values=30, off_list_texts=3
        )

        assert [len(p["values"]) for p in body_few["parameters"]] == [5, 5]
        assert [len(p["values"]) for p in body_many["parameters"]] == [30, 30]
        assert len(_parameter_of(body_few, 2)["off_list"]) == 1
        assert len(_parameter_of(body_many, 2)["off_list"]) == 3
        assert few == many


# ---------------------------------------------------------------------------
#  Варианты — только из главных; карточка контекста
# ---------------------------------------------------------------------------

class TestVariantsAndCard:
    def test_variants_list_only_the_main_parameters(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        assert all(len(v["values"]) == 2 for v in variants_out(db_session, scene.family_id))

        edit(
            db_session, scene.family_id,
            [
                draft(1, "Толщина", "50 мм", "100 мм"),
                draft(2, "Материал", "бетон", "кирпич", closed=True, role="secondary"),
            ],
            _actor(factories),
        )

        variants = variants_out(db_session, scene.family_id)
        assert variants and all(len(v["values"]) == 1 for v in variants)
        assert sorted(v["values"][0] for v in variants if v["status"] == "active") == [
            "100 мм", "50 мм",
        ]

    def test_the_card_gives_role_refreshing_and_the_off_list_text(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        edit(
            db_session, scene.family_id,
            [
                draft(1, "Толщина", "50 мм", "100 мм"),
                draft(2, "Материал", "бетон", "кирпич", closed=True, role="secondary"),
            ],
            _actor(factories),
        )
        _set_row(
            db_session, scene.contexts[2], scene.thickness, refresh_requested_at=sa.func.now()
        )
        context = db_session.get(CatalogContext, scene.contexts[2])

        card = context_variant_out(db_session, context)

        by_ordinal = {v["ordinal"]: v for v in card["values"]}
        assert set(by_ordinal[1]) == {
            "parameter_id", "ordinal", "name", "value_id", "value", "source", "role",
            "refreshing", "off_list_text",
        }
        assert (by_ordinal[1]["role"], by_ordinal[1]["refreshing"], by_ordinal[1]["off_list_text"]) == (
            "main", True, None,
        )
        assert (
            by_ordinal[2]["role"], by_ordinal[2]["refreshing"], by_ordinal[2]["off_list_text"],
            by_ordinal[2]["value"], by_ordinal[2]["source"],
        ) == ("secondary", False, "Керамзит", None, "off_list")

    def test_the_card_lists_the_main_parameters_before_the_secondary(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        edit(
            db_session, scene.family_id,
            [
                draft(1, "Толщина", "50 мм", "100 мм", role="secondary"),
                draft(2, "Материал", "бетон", "кирпич", closed=True),
            ],
            _actor(factories),
        )
        context = db_session.get(CatalogContext, scene.contexts[0])

        card = context_variant_out(db_session, context)

        assert [(v["ordinal"], v["role"]) for v in card["values"]] == [
            (2, "main"), (1, "secondary"),
        ]

    def test_an_archived_parameter_is_not_on_the_card(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        edit(
            db_session, scene.family_id,
            [draft(2, "Материал", "бетон", "кирпич", closed=True)], _actor(factories),
        )
        context = db_session.get(CatalogContext, scene.contexts[0])

        card = context_variant_out(db_session, context)

        assert [v["ordinal"] for v in card["values"]] == [2]


# ---------------------------------------------------------------------------
#  Слияние значений архивного параметра
# ---------------------------------------------------------------------------

class TestMergeOfAnArchivedParameter:
    def _archived_scene(self, db, factories):
        scene = _off_list_scene(db, factories)
        values = _values(db, scene.thickness)
        edit(
            db, scene.family_id,
            [draft(2, "Материал", "бетон", "кирпич", closed=True)], _actor(factories),
        )
        return scene, values

    def test_the_merge_is_refused_and_nothing_changes(self, db_session, factories):
        scene, values = self._archived_scene(db_session, factories)
        before = _all_state(db_session)

        with pytest.raises(WorkFamilyError) as raised:
            merge_parameter_values(
                db_session, parameter_id=scene.thickness,
                source_value_id=values["100 мм"].id, target_value_id=values["50 мм"].id,
                actor_id=_actor(factories),
            )

        assert raised.value.code == "parameter_not_found"
        assert _all_state(db_session) == before

    def test_the_route_answers_404(self, admin_client, db_session, factories):
        scene, values = self._archived_scene(db_session, factories)
        db_session.commit()

        response = admin_client.post(
            f"{BASE}/families/{scene.family_id}/schema/values/merge",
            json={
                "parameter_id": scene.thickness, "source_value_id": values["100 мм"].id,
                "target_value_id": values["50 мм"].id,
            },
        )

        assert response.status_code == 404, response.text
        assert _detail(response)["code"] == "parameter_not_found"

    def test_an_active_parameter_of_the_same_family_still_merges(self, db_session, factories):
        scene, _values_of_thickness = self._archived_scene(db_session, factories)
        values = _values(db_session, scene.material)

        merge_parameter_values(
            db_session, parameter_id=scene.material, source_value_id=values["кирпич"].id,
            target_value_id=values["бетон"].id, actor_id=_actor(factories),
        )

        assert _values(db_session, scene.material)["кирпич"].merged_into_id == values["бетон"].id


# ---------------------------------------------------------------------------
#  resolve_off_list: «Слить с…»
# ---------------------------------------------------------------------------

def _states(db, scene):
    """Всё, что операция может записать, и число событий правки."""
    return _all_state(db), len(_events(db, "family_schema_edited", scene.family_id))


class TestResolveOffList:
    def test_the_rows_of_the_norm_get_the_value_and_the_others_stay(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        target = _values(db_session, scene.material)["кирпич"].id
        other_before = _context_row(db_session, scene.contexts[2], scene.material)
        base_before = _table_rows(
            db_session, ContextParameterValue,
            ContextParameterValue.context_id == scene.world.context_id,
        )

        _resolve(db_session, scene, actor_id=_actor(factories))

        for index in (0, 1, 3):
            row = _context_row(db_session, scene.contexts[index], scene.material)
            assert (row.value_id, row.source, row.off_list_text) == (target, "manual", None)
            assert row.refresh_requested_at is None
        other = _context_row(db_session, scene.contexts[2], scene.material)
        assert (other.value_id, other.source, other.off_list_text) == (
            None, "off_list", "Керамзит",
        )
        assert other.off_list_text == other_before.off_list_text
        assert _table_rows(
            db_session, ContextParameterValue,
            ContextParameterValue.context_id == scene.world.context_id,
        ) == base_before

    def test_the_list_of_values_does_not_change(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        before = _table_rows(
            db_session, FamilyParameterValue,
            FamilyParameterValue.parameter_id.in_([scene.thickness, scene.material]),
        )

        _resolve(db_session, scene, actor_id=_actor(factories))

        assert _table_rows(
            db_session, FamilyParameterValue,
            FamilyParameterValue.parameter_id.in_([scene.thickness, scene.material]),
        ) == before

    def test_a_main_parameter_moves_the_contexts_to_the_variant_of_the_value(
        self, db_session, factories
    ):
        scene = _off_list_scene(db_session, factories)
        c0, c1, c2, c3 = scene.contexts
        keep = _variant_of(db_session, c2)
        assert {_variant_of(db_session, c) for c in (c0, c3)} == {keep}
        thickness = _values(db_session, scene.thickness)
        brick = _values(db_session, scene.material)["кирпич"].id
        old_c1 = _variant_of(db_session, c1)

        _resolve(db_session, scene, actor_id=_actor(factories))

        moved = _variant_of(db_session, c0)
        assert moved not in (keep, None) and _variant_of(db_session, c3) == moved
        assert _variant_row(db_session, moved).values_key == (
            f"1={thickness['50 мм'].id}|2={brick}"
        )
        assert _variant_row(db_session, _variant_of(db_session, c1)).values_key == (
            f"1={thickness['100 мм'].id}|2={brick}"
        )
        assert _variant_of(db_session, c2) == keep
        assert _variant_row(db_session, old_c1).status == "archived"
        assert _variant_row(db_session, keep).status == "active"

    def test_a_secondary_parameter_leaves_the_variants_alone(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        edit(
            db_session, scene.family_id,
            [
                draft(1, "Толщина", "50 мм", "100 мм"),
                draft(2, "Материал", "бетон", "кирпич", closed=True, role="secondary"),
            ],
            _actor(factories),
        )
        before = {c: _variant_of(db_session, c) for c in scene.contexts}
        variants = _table_rows(db_session, WorkVariant)

        _resolve(db_session, scene, actor_id=_actor(factories))

        assert {c: _variant_of(db_session, c) for c in scene.contexts} == before
        assert _table_rows(db_session, WorkVariant) == variants
        row = _context_row(db_session, scene.contexts[0], scene.material)
        assert (row.source, row.off_list_text) == ("manual", None)

    def test_the_revision_grows_by_one_and_the_event_names_the_change(
        self, db_session, factories
    ):
        scene = _off_list_scene(db_session, factories)
        brick = _values(db_session, scene.material)["кирпич"].id

        _resolve(db_session, scene, actor_id=_actor(factories))

        assert _revision(db_session, scene.schema_id) == 1
        [event] = _events(db_session, "family_schema_edited", scene.family_id)
        assert set(event.payload) == EVENT_REQUIRED_KEYS["family_schema_edited"]
        assert (event.payload["schema_id"], event.payload["revision"]) == (scene.schema_id, 1)
        assert event.payload["changes"] == [
            {
                "ordinal": 2, "name": "Материал",
                "off_list_resolved": {
                    "text_norm": "газобетон", "value_id": brick, "value": "кирпич",
                    "contexts": 3,
                },
            }
        ]
        assert (event.payload["variants_before"], event.payload["variants_after"]) == (3, 4)
        assert event.payload["jobs_enqueued"] == 0

    def test_an_archived_context_keeps_its_row(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        db_session.execute(
            sa.update(CatalogContext).where(CatalogContext.id == scene.contexts[1])
            .values(archived_at=sa.func.now())
        )
        db_session.flush()

        _resolve(db_session, scene, actor_id=_actor(factories))

        kept = _context_row(db_session, scene.contexts[1], scene.material)
        assert (kept.source, kept.off_list_text) == ("off_list", "ГАЗОБЕТОН")
        resolved = _context_row(db_session, scene.contexts[0], scene.material)
        assert resolved.source == "manual"
        [event] = _events(db_session, "family_schema_edited", scene.family_id)
        assert event.payload["changes"][0]["off_list_resolved"]["contexts"] == 2

    def test_a_marked_row_is_unmarked_and_its_pending_job_is_replaced(
        self, db_session, factories
    ):
        scene = _off_list_scene(db_session, factories)
        marked = scene.contexts[0]
        _set_row(db_session, marked, scene.material, refresh_requested_at=sa.func.now())
        reconcile_or_defer(db_session, [marked])
        before = _live_values_jobs(db_session, context_id=marked)
        assert [job.requested_ordinals for job in before] == [[2]]

        _resolve(db_session, scene, actor_id=_actor(factories))

        row = _context_row(db_session, marked, scene.material)
        assert (row.source, row.refresh_requested_at) == ("manual", None)
        assert _live_values_jobs(db_session, context_id=marked) == []
        assert {job.id for job in before}.isdisjoint(
            {job.id for job in _live_values_jobs(db_session, context_id=marked)}
        )

    def test_without_marked_rows_no_job_is_touched(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        jobs = _table_rows(db_session, SemanticJob)

        _resolve(db_session, scene, actor_id=_actor(factories))

        assert _table_rows(db_session, SemanticJob) == jobs

    def test_the_locks_follow_the_module_order(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        actor = _actor(factories)

        with _capturing_sql(db_session) as statements:
            _resolve(db_session, scene, actor_id=actor)

        first_seen: list[tuple[str, str]] = []
        for lock in _lock_sequence(statements):
            if lock not in first_seen:
                first_seen.append(lock)
        order = ["work_families", "family_parameter_schemas", "family_parameter_values",
                 "catalog_contexts", "work_variants", "semantic_jobs"]
        tables = [table for table, _mode in first_seen]
        assert [mode for _table, mode in first_seen] == ["UPDATE"] * len(first_seen)
        assert tables == [table for table in order if table in tables]
        assert {"work_families", "family_parameter_schemas", "family_parameter_values",
                "catalog_contexts"} <= set(tables)
        assert tables[:2] == ["work_families", "family_parameter_schemas"]


class TestResolveOffListRefusals:
    """Каждый отказ — своим входом, с неизменной базой, `revision` и журналом."""

    def _refuse(self, db, scene, code, call):
        before = _states(db, scene)
        with pytest.raises(WorkFamilyError) as raised:
            call()
        assert raised.value.code == code
        assert _states(db, scene) == before

    def test_a_text_without_rows_is_off_list_not_found(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        self._refuse(
            db_session, scene, "off_list_not_found",
            lambda: _resolve(db_session, scene, "пеноблок", actor_id=_actor(factories)),
        )

    def test_the_rows_belong_to_a_parameter_the_text_of_another_is_not_found(
        self, db_session, factories
    ):
        scene = _off_list_scene(db_session, factories)
        thickness_value = _values(db_session, scene.thickness)["50 мм"].id
        self._refuse(
            db_session, scene, "off_list_not_found",
            lambda: _resolve(
                db_session, scene, "газобетон", thickness_value, actor_id=_actor(factories),
                parameter=scene.thickness,
            ),
        )

    def test_resolved_rows_are_not_found_again(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        actor = _actor(factories)
        _resolve(db_session, scene, actor_id=actor)

        self._refuse(
            db_session, scene, "off_list_not_found",
            lambda: _resolve(db_session, scene, actor_id=actor),
        )

    def test_a_value_of_another_parameter_is_an_invalid_target(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        foreign = _values(db_session, scene.thickness)["50 мм"].id
        self._refuse(
            db_session, scene, "off_list_target_invalid",
            lambda: _resolve(db_session, scene, "газобетон", foreign, actor_id=_actor(factories)),
        )

    def test_a_merged_value_is_an_invalid_target(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        values = _values(db_session, scene.material)
        merge_parameter_values(
            db_session, parameter_id=scene.material, source_value_id=values["кирпич"].id,
            target_value_id=values["бетон"].id, actor_id=_actor(factories),
        )
        self._refuse(
            db_session, scene, "off_list_target_invalid",
            lambda: _resolve(
                db_session, scene, "газобетон", values["кирпич"].id, actor_id=_actor(factories)
            ),
        )

    def test_a_retired_value_is_an_invalid_target(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        brick = _values(db_session, scene.material)["кирпич"].id
        db_session.execute(
            sa.update(FamilyParameterValue).where(FamilyParameterValue.id == brick)
            .values(retired_at=sa.func.now())
        )
        db_session.flush()
        self._refuse(
            db_session, scene, "off_list_target_invalid",
            lambda: _resolve(db_session, scene, "газобетон", brick, actor_id=_actor(factories)),
        )

    def test_a_missing_value_is_value_not_found(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        self._refuse(
            db_session, scene, "value_not_found",
            lambda: _resolve(
                db_session, scene, "газобетон", 999999999, actor_id=_actor(factories)
            ),
        )

    def test_an_archived_parameter_is_parameter_not_found(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        brick = _values(db_session, scene.material)["кирпич"].id
        _update_parameter(db_session, scene.material, archived_at=sa.func.now())
        self._refuse(
            db_session, scene, "parameter_not_found",
            lambda: _resolve(db_session, scene, "газобетон", brick, actor_id=_actor(factories)),
        )

    def test_a_parameter_of_another_family_is_parameter_not_found(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        other = _off_list_scene(db_session, factories)
        brick = _values(db_session, scene.material)["кирпич"].id
        self._refuse(
            db_session, scene, "parameter_not_found",
            lambda: _resolve(
                db_session, scene, "газобетон", brick, actor_id=_actor(factories),
                parameter=other.material,
            ),
        )

    def test_a_missing_parameter_is_parameter_not_found(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        brick = _values(db_session, scene.material)["кирпич"].id
        self._refuse(
            db_session, scene, "parameter_not_found",
            lambda: _resolve(
                db_session, scene, "газобетон", brick, actor_id=_actor(factories),
                parameter=999999999,
            ),
        )

    def test_a_family_with_a_building_version_is_refused(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        _building(db_session, scene.world.family)
        self._refuse(
            db_session, scene, "schema_building",
            lambda: _resolve(db_session, scene, actor_id=_actor(factories)),
        )

    def test_a_missing_family_is_family_not_found(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        scene.family_id = 999999999
        with pytest.raises(WorkFamilyError) as raised:
            _resolve(db_session, scene, target=1, actor_id=_actor(factories))
        assert raised.value.code == "family_not_found"

    def test_a_family_without_a_current_version_is_refused(self, db_session, factories):
        scene = _off_list_scene(db_session, factories)
        family = _schemaless_family(db_session)
        scene.family_id = family.id
        with pytest.raises(WorkFamilyError) as raised:
            _resolve(db_session, scene, target=1, actor_id=_actor(factories))
        assert raised.value.code == "schema_no_current"


# ---------------------------------------------------------------------------
#  Маршрут off-list/resolve и одна форма SchemaOut
# ---------------------------------------------------------------------------

def _resolve_url(family_id: int) -> str:
    return f"{BASE}/families/{family_id}/schema/off-list/resolve"


def _resolve_body(scene, db, text_norm="газобетон", target="кирпич", parameter=None):
    return {
        "parameter_id": parameter or scene.material, "text_norm": text_norm,
        "target_value_id": _values(db, scene.material)[target].id
        if isinstance(target, str) else target,
    }


class TestResolveRoute:
    def test_the_route_resolves_and_answers_with_the_schema(
        self, admin_client, db_session, factories
    ):
        scene = _off_list_scene(db_session, factories)
        db_session.commit()

        response = admin_client.post(
            _resolve_url(scene.family_id), json=_resolve_body(scene, db_session)
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["revision"] == 1
        assert [e["text_norm"] for e in _parameter_of(body, 2)["off_list"]] == ["керамзит"]
        assert _context_row(db_session, scene.contexts[0], scene.material).source == "manual"

    def test_every_route_answers_with_the_same_form_as_the_read(
        self, admin_client, db_session, factories
    ):
        scene = _off_list_scene(db_session, factories)
        db_session.commit()
        url = f"{BASE}/families/{scene.family_id}/schema"

        def read():
            return admin_client.get(url).json()

        resolved = admin_client.post(
            _resolve_url(scene.family_id), json=_resolve_body(scene, db_session)
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json() == read()

        parameters = [
            {"ordinal": 1, "name": "Толщина", "role": "main", "value_kind": "exact",
             "closed": False, "values": ["50 мм", "100 мм"]},
            {"ordinal": 2, "name": "Материал", "role": "main", "value_kind": "exact",
             "closed": True, "values": ["бетон", "кирпич"]},
            {"ordinal": None, "name": "Марка", "role": "secondary", "value_kind": "exact",
             "closed": False, "values": ["М100"]},
        ]
        preview = admin_client.post(f"{url}/edit-preview", json={"parameters": parameters})
        assert preview.status_code == 200, preview.text
        patched = admin_client.patch(
            url, json={"parameters": parameters, "preview_hash": preview.json()["preview_hash"]}
        )
        assert patched.status_code == 200, patched.text
        assert patched.json() == read()

        _building(db_session, scene.world.family)
        db_session.commit()
        cancelled = admin_client.post(f"{url}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json() == read()

        assert {tuple(sorted(r)) for r in (resolved.json(), patched.json(), cancelled.json())} == {
            tuple(sorted(read()))
        }

    def test_a_member_is_rejected(self, member_client, db_session, factories):
        scene = _off_list_scene(db_session, factories)

        response = member_client.post(
            _resolve_url(scene.family_id), json=_resolve_body(scene, db_session)
        )

        assert response.status_code == 403

    @pytest.mark.parametrize(
        ("case", "status", "code"),
        [
            ("no_rows", 404, "off_list_not_found"),
            ("foreign_target", 422, "off_list_target_invalid"),
            ("missing_value", 404, "value_not_found"),
        ],
    )
    def test_a_refusal_answers_with_its_status_and_changes_nothing(
        self, admin_client, db_session, factories, case, status, code
    ):
        scene = _off_list_scene(db_session, factories)
        body = _resolve_body(scene, db_session)
        if case == "no_rows":
            body["text_norm"] = "пеноблок"
        elif case == "foreign_target":
            body["target_value_id"] = _values(db_session, scene.thickness)["50 мм"].id
        else:
            body["target_value_id"] = 999999999
        db_session.commit()
        before = _states(db_session, scene)

        response = admin_client.post(_resolve_url(scene.family_id), json=body)

        assert response.status_code == status, response.text
        assert _detail(response)["code"] == code
        assert _states(db_session, scene) == before

    def test_a_body_without_a_field_is_a_validation_error(
        self, admin_client, db_session, factories
    ):
        scene = _off_list_scene(db_session, factories)
        body = _resolve_body(scene, db_session)
        del body["text_norm"]

        response = admin_client.post(_resolve_url(scene.family_id), json=body)

        assert response.status_code == 422

    def test_an_unknown_family_is_404(self, admin_client):
        response = admin_client.post(
            _resolve_url(999999999),
            json={"parameter_id": 1, "text_norm": "а", "target_value_id": 1},
        )

        assert response.status_code == 404
        assert _detail(response)["code"] == "family_not_found"

    def test_a_family_without_a_version_is_409(self, admin_client, db_session):
        family = _schemaless_family(db_session)

        response = admin_client.post(
            _resolve_url(family.id),
            json={"parameter_id": 1, "text_norm": "а", "target_value_id": 1},
        )

        assert response.status_code == 409
        assert _detail(response)["code"] == "schema_no_current"


class TestSchemaRoutesShape:
    def test_get_gives_the_extended_form(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)

        body = admin_client.get(f"{BASE}/families/{scene.family.id}/schema").json()

        assert body["revision"] == 0
        assert [p["role"] for p in body["parameters"]] == ["main", "main"]
        assert all(p["off_list"] == [] and p["refreshing"] == 0 for p in body["parameters"])
        assert (
            sum(v["contexts"] for p in body["parameters"] for v in p["values"]) == 0
        ), "у значений без строк контекстов нет"

    def test_the_schema_revision_is_visible_in_the_read(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)
        db_session.execute(
            sa.update(FamilyParameterSchema)
            .where(FamilyParameterSchema.id == scene.schema.id)
            .values(revision=7)
        )
        db_session.commit()

        body = admin_client.get(f"{BASE}/families/{scene.family.id}/schema").json()

        assert body["revision"] == 7


# ---------------------------------------------------------------------------
#  Гонки: правка, «Слить с…» и слияние значений сериализует замок семьи
# ---------------------------------------------------------------------------

def _is_contexts_lock(statement: str) -> bool:
    """Замок контекстов под замками семьи и версии: ответ уже у клиента."""
    text = " ".join(statement.split())
    return text.startswith("SELECT catalog_contexts.id FROM catalog_contexts") and text.endswith(
        "FOR UPDATE"
    )


def _is_version_lock(statement: str) -> bool:
    text = " ".join(statement.split())
    return "FROM family_parameter_schemas" in text and text.endswith("FOR UPDATE")


def _committed_off_list_scene(db, factories):
    scene = _off_list_scene(db, factories)
    actor = _actor(factories)
    db.commit()
    return scene, actor


def _resolve_work(scene, target_id, actor):
    family_id, material = scene.family_id, scene.material

    def work(session):
        schema_edit_module.resolve_off_list(
            session, family_id=family_id, parameter_id=material, text_norm="газобетон",
            target_value_id=target_id, actor_id=actor,
        )
        return "ok"

    return work


def _merge_work(scene, source_id, target_id, actor):
    material = scene.material

    def work(session):
        merge_parameter_values(
            session, parameter_id=material, source_value_id=source_id,
            target_value_id=target_id, actor_id=actor,
        )
        return "ok"

    return work


def _cosmetic_edit(db, scene, actor):
    """Косметическое переименование «Толщины» с хэшем предпросмотра, снятым до гонки."""
    drafts = [
        draft(1, "ТОЛЩИНА", "50 мм", "100 мм"),
        draft(2, "Материал", "бетон", "кирпич", closed=True),
    ]
    shared = schema_edit_module.preview_schema_edit(
        db, family_id=scene.family_id, drafts=drafts
    ).preview_hash
    db.commit()
    return _apply_code(scene.family_id, drafts, shared, actor)


class TestOffListResolveVersusEdit:
    def test_the_resolve_waits_for_the_edit_and_both_are_kept(
        self, committing_db, committing_factories, committing_session_factory
    ):
        db = committing_db
        scene, actor = _committed_off_list_scene(db, committing_factories)
        target = _values(db, scene.material)["кирпич"].id
        edit_work = _cosmetic_edit(db, scene, actor)
        sessions = _Scene(committing_session_factory)

        try:
            sessions.spawn("a", edit_work, pause_on=_is_revision_read)
            assert sessions.wait_paused("a"), "правка не дошла до чтения revision"
            sessions.spawn("b", _resolve_work(scene, target, actor))
            settled = sessions.wait_settled("b", contains="FOR UPDATE")
            sessions.release["a"].set()
        finally:
            sessions.finish()

        sessions.assert_clean()
        assert settled == "blocked", f"«Слить с…» не встало на замке правки: {settled}"
        assert sessions.results == {"a": "ok", "b": "ok"}
        with committing_session_factory() as probe:
            assert _revision(probe, scene.schema_id) == 2
            assert _parameter(probe, scene.schema_id, 1).name == "ТОЛЩИНА"
            row = _context_row(probe, scene.contexts[0], scene.material)
            assert (row.value_id, row.source) == (target, "manual")

    def test_the_edit_waits_for_the_resolve_and_is_refused(
        self, committing_db, committing_factories, committing_session_factory
    ):
        db = committing_db
        scene, actor = _committed_off_list_scene(db, committing_factories)
        target = _values(db, scene.material)["кирпич"].id
        edit_work = _cosmetic_edit(db, scene, actor)
        sessions = _Scene(committing_session_factory)

        try:
            sessions.spawn("a", _resolve_work(scene, target, actor), pause_on=_is_contexts_lock)
            assert sessions.wait_paused("a"), "«Слить с…» не дошло до замка контекстов"
            sessions.spawn("b", edit_work)
            settled = sessions.wait_settled("b", contains="FOR UPDATE")
            sessions.release["a"].set()
        finally:
            sessions.finish()

        sessions.assert_clean()
        assert settled == "blocked", f"правка не встала на замке «Слить с…»: {settled}"
        assert sessions.results == {"a": "ok", "b": "preview_changed"}
        with committing_session_factory() as probe:
            assert _revision(probe, scene.schema_id) == 1
            assert _parameter(probe, scene.schema_id, 1).name == "Толщина"


class TestMergeOfValuesVersusEdit:
    def test_the_merge_waits_for_the_edit_and_both_are_kept(
        self, committing_db, committing_factories, committing_session_factory
    ):
        db = committing_db
        scene, actor = _committed_off_list_scene(db, committing_factories)
        values = _values(db, scene.material)
        edit_work = _cosmetic_edit(db, scene, actor)
        sessions = _Scene(committing_session_factory)

        try:
            sessions.spawn("a", edit_work, pause_on=_is_revision_read)
            assert sessions.wait_paused("a"), "правка не дошла до чтения revision"
            sessions.spawn("b", _merge_work(scene, values["кирпич"].id, values["бетон"].id, actor))
            settled = sessions.wait_settled("b", contains="FOR UPDATE")
            sessions.release["a"].set()
        finally:
            sessions.finish()

        sessions.assert_clean()
        assert settled == "blocked", f"слияние не встало на замке правки: {settled}"
        assert sessions.results == {"a": "ok", "b": "ok"}
        with committing_session_factory() as probe:
            assert _revision(probe, scene.schema_id) == 2
            assert _values(probe, scene.material)["кирпич"].merged_into_id == values["бетон"].id

    def test_the_edit_waits_for_the_merge_and_is_refused(
        self, committing_db, committing_factories, committing_session_factory
    ):
        db = committing_db
        scene, actor = _committed_off_list_scene(db, committing_factories)
        values = _values(db, scene.material)
        edit_work = _cosmetic_edit(db, scene, actor)
        sessions = _Scene(committing_session_factory)

        try:
            sessions.spawn(
                "a", _merge_work(scene, values["кирпич"].id, values["бетон"].id, actor),
                pause_on=_is_version_lock,
            )
            assert sessions.wait_paused("a"), "слияние не дошло до замка версии"
            sessions.spawn("b", edit_work)
            settled = sessions.wait_settled("b", contains="FOR UPDATE")
            sessions.release["a"].set()
        finally:
            sessions.finish()

        sessions.assert_clean()
        assert settled == "blocked", f"правка не встала на замке слияния: {settled}"
        assert sessions.results == {"a": "ok", "b": "preview_changed"}
        with committing_session_factory() as probe:
            assert _revision(probe, scene.schema_id) == 1
            assert _parameter(probe, scene.schema_id, 1).name == "Толщина"


# ---------------------------------------------------------------------------
#  Дополнения ревью задачи 6
# ---------------------------------------------------------------------------

class TestReviewCountsAndJobs:
    def test_a_marked_row_still_counts_for_its_value(self, db_session, factories):
        """Строка «уточняется» со значением остаётся строкой этого значения: числа
        значения складываются по группам с отметкой и без неё."""
        scene = _off_list_scene(db_session, factories)
        _set_row(
            db_session, scene.contexts[3], scene.thickness, refresh_requested_at=sa.func.now()
        )

        body = schema_out(db_session, scene.family_id)

        fifty = _value_of(body, 1, "50 мм")
        assert (fifty["contexts"], fifty["rows"]) == (4, 7)
        assert _parameter_of(body, 1)["refreshing"] == 1

    def test_the_event_counts_the_job_left_for_another_marked_parameter(
        self, db_session, factories
    ):
        """У контекста помечены оба параметра; «Слить с…» снимает отметку материала,
        задание заменяется заданием на толщину — событие называет его."""
        scene = _off_list_scene(db_session, factories)
        marked = scene.contexts[0]
        for parameter in (scene.thickness, scene.material):
            _set_row(db_session, marked, parameter, refresh_requested_at=sa.func.now())
        reconcile_or_defer(db_session, [marked])
        before = _live_values_jobs(db_session, context_id=marked)
        assert [job.requested_ordinals for job in before] == [None]  # все параметры

        _resolve(db_session, scene, actor_id=_actor(factories))

        after = _live_values_jobs(db_session, context_id=marked)
        assert [job.requested_ordinals for job in after] == [[1]]
        assert {job.id for job in before}.isdisjoint({job.id for job in after})
        [event] = _events(db_session, "family_schema_edited", scene.family_id)
        assert event.payload["jobs_enqueued"] == 1


    def test_the_bounds_are_given_in_the_canonical_form(self, db_session, factories):
        """Границы, записанные с лишними нулями («1.0», «0.50»), отдаются канонической
        записью правки («1», «0.5»), как их ввёл бы экран."""
        scene = _off_list_scene(db_session, factories)
        edit(
            db_session, scene.family_id,
            [*_same_with_material_closed(), draft(None, "Шаг", kind="range", rng=("1.0", "2.0", "0.50"))],
            _actor(factories),
        )

        body = schema_out(db_session, scene.family_id)

        step = _parameter_of(body, 3)
        assert step["range"] == {"start": "1", "stop": "2", "step": "0.5"}
        assert {(v["range_lo"], v["range_hi"]) for v in step["values"]} == {
            (None, "1"), ("1", "1.5"), ("1.5", "2"), ("2", None),
        }

    def test_a_secondary_parameter_locks_the_contexts_and_not_the_variants(
        self, db_session, factories
    ):
        """Порядок блокировок «Слить с…» у второстепенного: контексты берутся (своим
        замком, без пересчёта), варианты не берутся вовсе — пересчёта нет."""
        scene = _off_list_scene(db_session, factories)
        edit(
            db_session, scene.family_id,
            [
                draft(1, "Толщина", "50 мм", "100 мм"),
                draft(2, "Материал", "бетон", "кирпич", closed=True, role="secondary"),
            ],
            _actor(factories),
        )
        actor = _actor(factories)

        with _capturing_sql(db_session) as statements:
            _resolve(db_session, scene, actor_id=actor)

        tables = [table for table, _mode in _lock_sequence(statements)]
        assert "catalog_contexts" in tables
        assert "work_variants" not in tables


def _is_building_insert(statement: str) -> bool:
    return statement.lstrip().startswith("INSERT INTO family_parameter_schemas")


class TestOffListResolveVersusRebuild:
    def test_the_resolve_waits_for_the_rebuild_and_is_refused(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """Пересборка держит только семью (версия `building` ещё не видна): второй
        рубеж — `FOR UPDATE` действующей версии — её не встречает, и «Слить с…»
        сериализует с пересборкой один замок семьи."""
        db = committing_db
        scene, actor = _committed_off_list_scene(db, committing_factories)
        target = _values(db, scene.material)["кирпич"].id
        family_id, material = scene.family_id, scene.material

        def rebuild(session):
            rebuild_schema(session, family_id=family_id, actor_id=actor)
            return "ok"

        def resolve(session):
            try:
                schema_edit_module.resolve_off_list(
                    session, family_id=family_id, parameter_id=material, text_norm="газобетон",
                    target_value_id=target, actor_id=actor,
                )
            except WorkFamilyError as exc:
                session.rollback()
                return exc.code
            return "ok"

        sessions = _Scene(committing_session_factory)
        try:
            sessions.spawn("a", rebuild, pause_on=_is_building_insert)
            assert sessions.wait_paused("a"), "пересборка не дошла до вставки версии"
            sessions.spawn("b", resolve)
            settled = sessions.wait_settled("b", contains="FOR UPDATE")
            sessions.release["a"].set()
        finally:
            sessions.finish()

        sessions.assert_clean()
        assert settled == "blocked", f"«Слить с…» не встало на замке семьи: {settled}"
        assert sessions.results == {"a": "ok", "b": "schema_building"}
        with committing_session_factory() as probe:
            assert _revision(probe, scene.schema_id) == 0
            row = _context_row(probe, scene.contexts[0], scene.material)
            assert (row.source, row.off_list_text) == ("off_list", "Газобетон")


class TestMergeOfValuesVersusArchivingEdit:
    def test_the_merge_waits_and_finds_the_parameter_archived(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """Правка архивирует «Толщину» и держит замки; слияние значений толщины ждёт
        и перечитывает признак архива под замками — `parameter_not_found`."""
        db = committing_db
        scene, actor = _committed_off_list_scene(db, committing_factories)
        thickness = _values(db, scene.thickness)
        drafts = [draft(2, "Материал", "бетон", "кирпич", closed=True)]
        shared = schema_edit_module.preview_schema_edit(
            db, family_id=scene.family_id, drafts=drafts
        ).preview_hash
        db.commit()
        parameter_id = scene.thickness

        def merge(session):
            try:
                merge_parameter_values(
                    session, parameter_id=parameter_id,
                    source_value_id=thickness["100 мм"].id,
                    target_value_id=thickness["50 мм"].id, actor_id=actor,
                )
            except WorkFamilyError as exc:
                session.rollback()
                return exc.code
            return "ok"

        sessions = _Scene(committing_session_factory)
        try:
            sessions.spawn(
                "a", _apply_code(scene.family_id, drafts, shared, actor),
                pause_on=_is_revision_read,
            )
            assert sessions.wait_paused("a"), "правка не дошла до чтения revision"
            sessions.spawn("b", merge)
            settled = sessions.wait_settled("b", contains="FOR UPDATE")
            sessions.release["a"].set()
        finally:
            sessions.finish()

        sessions.assert_clean()
        assert settled == "blocked", f"слияние не встало на замке правки: {settled}"
        assert sessions.results == {"a": "ok", "b": "parameter_not_found"}
        with committing_session_factory() as probe:
            assert _values(probe, scene.thickness)["100 мм"].merged_into_id is None


class TestMergeSeesTheRoleSetWhileItWaited:
    def test_a_role_flipped_to_main_by_the_edit_is_seen_by_the_merge(
        self, committing_db, committing_factories, committing_session_factory
    ):
        db = committing_db
        scene, actor = _committed_off_list_scene(db, committing_factories)
        _resolve(db, scene, actor_id=actor)
        db.commit()
        edit(
            db, scene.family_id,
            [
                draft(1, "Толщина", "50 мм", "100 мм"),
                draft(2, "Материал", "бетон", "кирпич", closed=True, role="secondary"),
            ],
            actor,
        )
        db.commit()
        flip = [
            draft(1, "Толщина", "50 мм", "100 мм"),
            draft(2, "Материал", "бетон", "кирпич", closed=True),
        ]
        shared = schema_edit_module.preview_schema_edit(
            db, family_id=scene.family_id, drafts=flip
        ).preview_hash
        db.commit()
        values = _values(db, scene.material)
        thickness = _values(db, scene.thickness)
        sessions = _Scene(committing_session_factory)

        try:
            sessions.spawn(
                "a", _apply_code(scene.family_id, flip, shared, actor), pause_on=_is_revision_read
            )
            assert sessions.wait_paused("a"), "правка не дошла до чтения revision"
            sessions.spawn(
                "b", _merge_work(scene, values["кирпич"].id, values["бетон"].id, actor)
            )
            settled = sessions.wait_settled("b", contains="FOR UPDATE")
            sessions.release["a"].set()
        finally:
            sessions.finish()

        sessions.assert_clean()
        assert settled == "blocked", f"слияние не встало на замке правки: {settled}"
        assert sessions.results == {"a": "ok", "b": "ok"}
        with committing_session_factory() as probe:
            first = scene.contexts[0]
            assert _context_row(probe, first, scene.material).value_id == values["бетон"].id
            key = _variant_row(probe, _variant_of(probe, first)).values_key
            assert key == f"1={thickness['50 мм'].id}|2={values['бетон'].id}"
