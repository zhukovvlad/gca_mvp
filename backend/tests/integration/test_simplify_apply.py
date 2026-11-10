"""Ответ `several`, два фильтра перед записью нового значения и заморозка схемы со
склейкой (спека упрощения каталога §2.3 место 5, §2.4).

Сцена — активная семья с одним главным параметром «Тип конструкции»; значения
заводятся по возрастанию `id` в порядке перечисления. Эталоны — независимые литералы:
тексты составных, нормы и числа записаны руками.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

import services.value_filters as value_filters
import services.variant_request as variant_request
from models import (
    CatalogAutoAction,
    CatalogContext,
    ContextParameterValue,
    FamilyParameter,
    FamilyParameterValue,
    FamilyValuePart,
)
from services.composite_values import composite_parts
from services.human_decisions import add_not_duplicate, add_veto
from services.semantic_reconcile import NO_CAP, reconcile_context_values
from services.variant_answer import ValueItem, ValuesAnswer
from services.work_variants import (
    apply_values,
    freeze_schema,
    merge_parameter_values,
    rebuild_schema,
)
from tests.integration.test_catalog_parameters_values import _RANGE_VALUES, _make_range
from tests.integration.test_work_variants_core import _parameter_in, _schema_answer
from tests.integration.test_work_variants_material import _settings
from tests.integration.test_work_variants_schema_life import (
    _active_world,
    _actor,
    _claim,
    _jobs,
    _paths_hash,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _scene(db, factories, names, *, closed=False):
    world = _active_world(db, factories, ((1, "Тип конструкции", list(names)),))
    if closed:
        db.execute(
            sa.update(FamilyParameter)
            .where(FamilyParameter.id == world.parameters[1])
            .values(closed=True)
        )
        db.flush()
    return SimpleNamespace(
        world=world, family=world.family, parameter_id=world.parameters[1],
        context_id=world.context_id, actor=_actor(factories),
        ids={name: world.values[(1, name)] for name in names},
    )


def _new(text, source="name"):
    return ValueItem(1, "new", text, source)


def _several(combine, *values, source="name"):
    return ValueItem(
        1, "several", None, source, values=tuple(values), combine=combine
    )


def _run(db, scene, item):
    return apply_values(
        db, context_id=scene.context_id, schema_id=scene.world.schema.id,
        answer=ValuesAnswer(items=(item,)), paths_hash=_paths_hash(db, scene.context_id),
        guard=None, settings=_settings(),
    )


def _row_of(db, scene):
    """Строка контекста по главному параметру: `(value_id, source, off_list_text)`."""
    db.expire_all()
    row = db.execute(
        sa.select(ContextParameterValue).where(
            ContextParameterValue.context_id == scene.context_id,
            ContextParameterValue.parameter_id == scene.parameter_id,
        )
    ).scalar_one()
    return row.value_id, row.source, row.off_list_text


def _value(db, value_id) -> FamilyParameterValue:
    db.expire_all()
    return db.get(FamilyParameterValue, value_id)


def _values(db, scene):
    db.expire_all()
    return list(
        db.execute(
            sa.select(FamilyParameterValue)
            .where(FamilyParameterValue.parameter_id == scene.parameter_id)
            .order_by(FamilyParameterValue.id)
        ).scalars()
    )


def _parts_of(db, value_id):
    db.expire_all()
    return sorted(
        db.execute(
            sa.select(FamilyValuePart.part_value_id).where(FamilyValuePart.value_id == value_id)
        ).scalars()
    )


def _journal(db, scene, kind=None):
    db.expire_all()
    query = sa.select(CatalogAutoAction).where(CatalogAutoAction.family_id == scene.family.id)
    if kind is not None:
        query = query.where(CatalogAutoAction.kind == kind)
    return list(db.execute(query.order_by(CatalogAutoAction.id)).scalars())


def _composites(db, scene):
    return [row for row in _values(db, scene) if row.composite_kind is not None]


def _ids(scene, *names):
    return sorted(scene.ids[name] for name in names)


def _merge(db, scene, source, target):
    merge_parameter_values(
        db, parameter_id=scene.parameter_id, source_value_id=scene.ids[source],
        target_value_id=scene.ids[target], actor_id=scene.actor,
    )
    db.flush()


# ---------------------------------------------------------------------------
#  Фильтр 1: склейка -> составное
# ---------------------------------------------------------------------------

class TestFilterOneGlue:
    NAMES = ("колонны", "пилоны", "трансферные конструкции", "балки")

    def test_a_new_glue_with_every_part_listed_becomes_a_composite(self, db_session, factories):
        scene = _scene(db_session, factories, self.NAMES)

        outcome = _run(db_session, scene, _new("колонны, пилоны, трансферные конструкции"))

        value_id, source, off_list = _row_of(db_session, scene)
        composite = _value(db_session, value_id)
        assert (composite.value, composite.composite_kind, composite.origin) == (
            "колонны + пилоны + трансферные конструкции", "all", "extension",
        )
        assert _parts_of(db_session, value_id) == _ids(
            scene, "колонны", "пилоны", "трансферные конструкции"
        )
        assert (source, off_list) == ("name", None)
        assert outcome.values_added == ()
        [entry] = _journal(db_session, scene)
        assert entry.kind == "composite"
        assert entry.context_id == scene.context_id
        assert entry.value_id == value_id
        assert entry.payload["text"] == "колонны, пилоны, трансферные конструкции"
        assert entry.payload["kind"] == "all"
        assert entry.payload["new_part_ids"] == []
        assert entry.payload["context_ids"] == [scene.context_id]

    @pytest.mark.parametrize("via", ["new", "several"])
    def test_a_value_of_the_veto_created_on_the_spot_counts_as_added(
        self, db_session, factories, via
    ):
        scene = _scene(db_session, factories, ("плиты", "балки", "плиты и балки"))
        add_veto(
            db_session, parameter_id=scene.parameter_id, kind="all",
            part_ids=[scene.ids["плиты"], scene.ids["балки"]],
            value_id=scene.ids["плиты и балки"], user_id=scene.actor,
        )
        db_session.execute(
            sa.delete(FamilyParameterValue).where(
                FamilyParameterValue.id == scene.ids["плиты и балки"]
            )
        )
        db_session.flush()
        item = _new("балки и плиты") if via == "new" else _several("all", "плиты", "балки")

        outcome = _run(db_session, scene, item)

        value_id, _source, _off_list = _row_of(db_session, scene)
        assert _value(db_session, value_id).value_norm == "плиты и балки"
        assert _value(db_session, value_id).composite_kind is None
        assert outcome.values_added == (value_id,)

    def test_the_word_or_makes_the_composite_of_kind_any(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки"))

        _run(db_session, scene, _new("плиты или балки"))

        value_id, _source, _off_list = _row_of(db_session, scene)
        composite = _value(db_session, value_id)
        assert (composite.value, composite.composite_kind) == ("плиты или балки", "any")

    @pytest.mark.parametrize(
        "text", ["ГО и ЧС", "трубопровод с изоляцией и арматурой", "балки и плиты и колонны"]
    )
    def test_a_glue_with_a_part_outside_the_list_stays_whole(self, db_session, factories, text):
        scene = _scene(db_session, factories, ("балки", "колонны"))
        before = len(_values(db_session, scene))

        outcome = _run(db_session, scene, _new(text))

        value_id, source, _off_list = _row_of(db_session, scene)
        row = _value(db_session, value_id)
        assert (row.value, row.composite_kind, row.origin) == (text, None, "extension")
        assert len(_values(db_session, scene)) == before + 1
        assert outcome.values_added == (value_id,)
        assert source == "name"
        [entry] = _journal(db_session, scene)
        assert entry.kind == "value_added"

    def test_a_partial_part_list_is_not_a_reason_to_add_the_known_parts_again(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("балки",))

        _run(db_session, scene, _new("балки и плиты"))

        assert [row.value for row in _values(db_session, scene)] == ["балки", "балки и плиты"]

    def test_the_parts_may_be_known_by_the_lemma_of_their_norm(self, db_session, factories):
        scene = _scene(db_session, factories, ("плита перекрытий", "балка"))

        _run(db_session, scene, _new("плиты перекрытия, балки"))

        value_id, _source, _off_list = _row_of(db_session, scene)
        assert _value(db_session, value_id).composite_kind == "all"
        assert _parts_of(db_session, value_id) == _ids(scene, "плита перекрытий", "балка")

    def test_parts_that_collapse_to_one_value_give_the_simple_value(self, db_session, factories):
        scene = _scene(db_session, factories, ("плита перекрытий", "балки"))

        _run(db_session, scene, _new("плита перекрытий, плиты перекрытия"))

        value_id, _source, _off_list = _row_of(db_session, scene)
        assert value_id == scene.ids["плита перекрытий"]
        assert _composites(db_session, scene) == []

    def test_a_vetoed_glue_is_replaced_by_the_value_of_the_veto(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки", "плиты и балки"))
        add_veto(
            db_session, parameter_id=scene.parameter_id, kind="all",
            part_ids=[scene.ids["плиты"], scene.ids["балки"]],
            value_id=scene.ids["плиты и балки"], user_id=scene.actor,
        )

        _run(db_session, scene, _new("балки и плиты"))

        value_id, _source, _off_list = _row_of(db_session, scene)
        assert value_id == scene.ids["плиты и балки"]
        assert _composites(db_session, scene) == []
        assert _journal(db_session, scene) == []


# ---------------------------------------------------------------------------
#  Порядок для new: точное совпадение
# ---------------------------------------------------------------------------

class TestExactMatchFirst:
    def test_an_exact_norm_is_set_and_the_filters_are_not_applied(self, db_session, factories):
        # «балки, капители» уже простое значение (например, после отмены разбора):
        # фильтр 1 дал бы составное из первых двух, но точное совпадение сильнее.
        scene = _scene(db_session, factories, ("балки", "капители", "балки, капители"))

        outcome = _run(db_session, scene, _new("Балки,  Капители"))

        value_id, _source, _off_list = _row_of(db_session, scene)
        assert value_id == scene.ids["балки, капители"]
        assert _composites(db_session, scene) == []
        assert _journal(db_session, scene) == []
        assert outcome.values_added == ()

    def test_the_exact_text_of_a_vetoed_value_is_set_even_when_the_veto_exists(
        self, db_session, factories, monkeypatch
    ):
        scene = _scene(db_session, factories, ("плиты", "балки", "плиты и балки"))
        add_veto(
            db_session, parameter_id=scene.parameter_id, kind="all",
            part_ids=[scene.ids["плиты"], scene.ids["балки"]],
            value_id=scene.ids["плиты и балки"], user_id=scene.actor,
        )

        # Фильтр 1 с вето дал бы то же V, поэтому исход сам по себе точное
        # совпадение не отличает: путь через разрез запрещён явно.
        def _no_filter_one(*args, **kwargs):
            raise AssertionError("точное совпадение обязано идти раньше фильтра 1")

        monkeypatch.setattr(value_filters, "split_candidate", _no_filter_one)
        monkeypatch.setattr(value_filters, "find_or_create_composite", _no_filter_one)

        _run(db_session, scene, _new("плиты и балки"))

        assert _row_of(db_session, scene)[0] == scene.ids["плиты и балки"]
        assert _journal(db_session, scene) == []

    def test_a_listed_text_in_new_is_the_listed_value(self, db_session, factories):
        scene = _scene(db_session, factories, ("колонны", "пилоны"))

        outcome = _run(db_session, scene, _new("Колонны"))

        assert _row_of(db_session, scene)[0] == scene.ids["колонны"]
        assert outcome.values_added == ()
        assert _journal(db_session, scene) == []


# ---------------------------------------------------------------------------
#  Фильтр 2: дубль -> существующее
# ---------------------------------------------------------------------------

class TestFilterTwoDuplicate:
    def test_a_word_form_of_a_listed_value_is_that_value(self, db_session, factories):
        scene = _scene(db_session, factories, ("плита перекрытий", "балки"))

        outcome = _run(db_session, scene, _new("плиты перекрытия"))

        assert _row_of(db_session, scene)[0] == scene.ids["плита перекрытий"]
        assert len(_values(db_session, scene)) == 2
        assert outcome.values_added == ()
        [entry] = _journal(db_session, scene)
        assert entry.kind == "equated"
        assert entry.family_id == scene.family.id
        assert entry.context_id == scene.context_id
        assert entry.parameter_id == scene.parameter_id
        assert entry.value_id == scene.ids["плита перекрытий"]
        assert entry.payload == {
            "text": "плиты перекрытия", "target_value_id": scene.ids["плита перекрытий"],
            "context_ids": [scene.context_id],
        }

    def test_two_matches_by_the_lemma_make_a_new_value(self, db_session, factories):
        scene = _scene(db_session, factories, ("плита перекрытий", "плиты перекрытия"))

        outcome = _run(db_session, scene, _new("плита перекрытие"))

        value_id, _source, _off_list = _row_of(db_session, scene)
        assert value_id not in scene.ids.values()
        assert _value(db_session, value_id).value == "плита перекрытие"
        assert outcome.values_added == (value_id,)
        assert [entry.kind for entry in _journal(db_session, scene)] == ["value_added"]

    def test_a_pair_marked_not_a_duplicate_is_not_equated(self, db_session, factories):
        scene = _scene(db_session, factories, ("плита перекрытий", "балки"))
        add_not_duplicate(
            db_session, parameter_id=scene.parameter_id, text_norm="плиты перекрытия",
            value_id=scene.ids["плита перекрытий"], user_id=scene.actor,
        )

        _run(db_session, scene, _new("плиты перекрытия"))

        value_id, _source, _off_list = _row_of(db_session, scene)
        assert value_id not in scene.ids.values()
        assert _value(db_session, value_id).value == "плиты перекрытия"
        assert [entry.kind for entry in _journal(db_session, scene)] == ["value_added"]

    def test_a_marked_pair_does_not_block_another_text_for_the_same_value(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плита перекрытий", "балки"))
        add_not_duplicate(
            db_session, parameter_id=scene.parameter_id, text_norm="плиты перекрытия",
            value_id=scene.ids["плита перекрытий"], user_id=scene.actor,
        )

        _run(db_session, scene, _new("плита перекрытие"))

        assert _row_of(db_session, scene)[0] == scene.ids["плита перекрытий"]

    def test_different_constructions_are_not_equated(self, db_session, factories):
        scene = _scene(db_session, factories, ("перекрытия", "балки"))

        _run(db_session, scene, _new("плиты покрытий"))

        value_id, _source, _off_list = _row_of(db_session, scene)
        assert value_id not in scene.ids.values()
        assert _value(db_session, value_id).value == "плиты покрытий"


# ---------------------------------------------------------------------------
#  Закрытый параметр
# ---------------------------------------------------------------------------

class TestClosedParameter:
    def test_a_listed_text_is_the_listed_value(self, db_session, factories):
        scene = _scene(db_session, factories, ("бетон", "кирпич"), closed=True)

        _run(db_session, scene, _new("Бетон "))

        assert _row_of(db_session, scene) == (scene.ids["бетон"], "name", None)

    @pytest.mark.parametrize("text", ["гранит", "бетон и кирпич", "бетоны"])
    def test_anything_else_is_off_list_and_the_filters_do_not_run(
        self, db_session, factories, text
    ):
        scene = _scene(db_session, factories, ("бетон", "кирпич"), closed=True)

        _run(db_session, scene, _new(text))

        assert _row_of(db_session, scene) == (None, "off_list", text)
        assert len(_values(db_session, scene)) == 2
        assert _journal(db_session, scene) == []


# ---------------------------------------------------------------------------
#  several
# ---------------------------------------------------------------------------

class TestSeveral:
    def test_listed_values_make_a_composite_of_the_asked_kind(self, db_session, factories):
        scene = _scene(db_session, factories, ("балки", "плиты", "колонны"))

        _run(db_session, scene, _several("all", "плиты", "балки", source="path"))

        value_id, source, off_list = _row_of(db_session, scene)
        composite = _value(db_session, value_id)
        assert (composite.value, composite.composite_kind) == ("балки + плиты", "all")
        assert _parts_of(db_session, value_id) == _ids(scene, "балки", "плиты")
        assert (source, off_list) == ("path", None)

    def test_the_kind_any_gives_the_word_or(self, db_session, factories):
        scene = _scene(db_session, factories, ("балки", "плиты"))

        _run(db_session, scene, _several("any", "балки", "плиты"))

        composite = _value(db_session, _row_of(db_session, scene)[0])
        assert (composite.value, composite.composite_kind) == ("балки или плиты", "any")

    def test_the_same_parts_of_the_other_kind_are_another_value(self, db_session, factories):
        scene = _scene(db_session, factories, ("балки", "плиты"))
        _run(db_session, scene, _several("all", "балки", "плиты"))
        first = _row_of(db_session, scene)[0]

        _run(db_session, scene, _several("any", "балки", "плиты"))

        assert _row_of(db_session, scene)[0] != first
        assert len(_composites(db_session, scene)) == 2

    def test_the_second_answer_with_the_same_parts_reuses_the_composite(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("балки", "плиты"))
        _run(db_session, scene, _several("all", "балки", "плиты"))
        first = _row_of(db_session, scene)[0]

        _run(db_session, scene, _several("all", "плиты", "балки"))

        assert _row_of(db_session, scene)[0] == first
        assert len(_composites(db_session, scene)) == 1
        assert len(_journal(db_session, scene, "composite")) == 1

    def test_new_parts_are_added_one_by_one_and_named_in_the_composite_entry(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("балки", "плиты"))

        outcome = _run(db_session, scene, _several("all", "балки", "капители"))

        value_id, _source, _off_list = _row_of(db_session, scene)
        rows = {row.value: row for row in _values(db_session, scene)}
        new_part = rows["капители"]
        assert (new_part.origin, new_part.composite_kind) == ("extension", None)
        assert _parts_of(db_session, value_id) == sorted((scene.ids["балки"], new_part.id))
        assert outcome.values_added == (new_part.id,)
        [added] = _journal(db_session, scene, "value_added")
        assert added.value_id == new_part.id
        assert added.payload == {
            "text": "капители", "origin_context_id": scene.context_id,
            "from_composite_id": value_id,
        }
        [composite] = _journal(db_session, scene, "composite")
        assert composite.payload["new_part_ids"] == [new_part.id]
        assert composite.payload["part_ids"] == sorted((scene.ids["балки"], new_part.id))

    def test_a_vetoed_state_writes_neither_a_composite_nor_a_journal_entry(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты", "балки", "плиты и балки"))
        add_veto(
            db_session, parameter_id=scene.parameter_id, kind="all",
            part_ids=[scene.ids["плиты"], scene.ids["балки"]],
            value_id=scene.ids["плиты и балки"], user_id=scene.actor,
        )

        _run(db_session, scene, _several("all", "плиты", "балки"))

        assert _row_of(db_session, scene)[0] == scene.ids["плиты и балки"]
        assert _composites(db_session, scene) == []
        assert _journal(db_session, scene) == []

    def test_a_vetoed_state_with_a_new_part_is_a_different_state(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки", "плиты и балки"))
        add_veto(
            db_session, parameter_id=scene.parameter_id, kind="all",
            part_ids=[scene.ids["плиты"], scene.ids["балки"]],
            value_id=scene.ids["плиты и балки"], user_id=scene.actor,
        )

        _run(db_session, scene, _several("all", "плиты", "балки", "колонны"))

        value_id, _source, _off_list = _row_of(db_session, scene)
        assert _value(db_session, value_id).composite_kind == "all"
        assert len(_parts_of(db_session, value_id)) == 3

    def test_elements_equated_to_one_value_give_a_simple_value(self, db_session, factories):
        scene = _scene(db_session, factories, ("плита перекрытий", "балки"))

        _run(db_session, scene, _several("all", "плита перекрытий", "плиты перекрытия"))

        assert _row_of(db_session, scene)[0] == scene.ids["плита перекрытий"]
        assert _composites(db_session, scene) == []
        assert [entry.kind for entry in _journal(db_session, scene)] == ["equated"]

    def test_a_composite_of_the_same_kind_is_expanded_into_its_parts(self, db_session, factories):
        scene = _scene(db_session, factories, ("балки", "плиты", "колонны"))
        _run(db_session, scene, _several("all", "балки", "плиты"))

        _run(db_session, scene, _several("all", "балки + плиты", "колонны"))

        value_id, _source, _off_list = _row_of(db_session, scene)
        assert _parts_of(db_session, value_id) == _ids(scene, "балки", "плиты", "колонны")
        assert _value(db_session, value_id).value == "балки + плиты + колонны"
        assert len(_composites(db_session, scene)) == 2

    def test_a_composite_of_another_kind_makes_the_answer_none(
        self, db_session, factories, caplog
    ):
        scene = _scene(db_session, factories, ("балки", "плиты", "колонны"))
        _run(db_session, scene, _several("all", "балки", "плиты"))
        composites_before = len(_composites(db_session, scene))

        with caplog.at_level(logging.INFO, logger="services.value_filters"):
            _run(db_session, scene, _several("any", "балки + плиты", "колонны"))

        assert _row_of(db_session, scene) == (None, "none", None)
        assert len(_composites(db_session, scene)) == composites_before
        assert [r.name for r in caplog.records if r.name == "services.value_filters"] == [
            "services.value_filters"
        ]

    def test_a_closed_parameter_with_every_part_listed_gets_the_composite(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("бетон", "кирпич"), closed=True)

        _run(db_session, scene, _several("all", "бетон", "кирпич"))

        value_id, source, _off_list = _row_of(db_session, scene)
        assert _value(db_session, value_id).composite_kind == "all"
        assert source == "name"

    @pytest.mark.parametrize(
        ("combine", "text"), [("all", "130 + 170"), ("any", "130 или 170")]
    )
    def test_several_on_a_range_parameter_is_off_list_with_the_assembled_text(
        self, db_session, factories, combine, text
    ):
        # Решение исполнителя (спека о диапазоне молчит): составного из диапазонов
        # не бывает, `several` у параметра-диапазона — значение не из списка.
        scene = _scene(db_session, factories, ("балки",))
        ranges = _make_range(db_session, scene.world, 1, _RANGE_VALUES)
        before = len(_values(db_session, scene))

        _run(db_session, scene, _several(combine, "130", "170"))

        assert _row_of(db_session, scene) == (None, "off_list", text)
        assert len(_values(db_session, scene)) == before == len(ranges)
        assert _journal(db_session, scene) == []

    @pytest.mark.parametrize(
        ("combine", "text"), [("all", "бетон + гранит"), ("any", "бетон или гранит")]
    )
    def test_a_closed_parameter_with_a_part_outside_the_list_is_off_list(
        self, db_session, factories, combine, text
    ):
        scene = _scene(db_session, factories, ("бетон", "кирпич"), closed=True)

        _run(db_session, scene, _several(combine, "бетон", "гранит"))

        assert _row_of(db_session, scene) == (None, "off_list", text)
        assert len(_values(db_session, scene)) == 2
        assert _journal(db_session, scene) == []


# ---------------------------------------------------------------------------
#  Слитый синоним в ответе (Review Focus плана, п. 1)
# ---------------------------------------------------------------------------

class TestMergedSynonym:
    """Элемент ответа пришёл текстом слитого синонима. «панели» слиты в «плиты»:
    леммы у них разные, поэтому совпасть они могут только через слияние, а не через
    фильтр 2."""

    NAMES = ("плиты", "балки", "панели")

    def _merged(self, db, factories, *, closed=False):
        scene = _scene(db, factories, self.NAMES, closed=closed)
        _merge(db, scene, "панели", "плиты")
        return scene

    def test_a_merged_synonym_and_its_target_collapse_to_the_simple_value(
        self, db_session, factories
    ):
        scene = self._merged(db_session, factories)

        outcome = _run(db_session, scene, _several("all", "плиты", "Панели"))

        assert _row_of(db_session, scene)[0] == scene.ids["плиты"]
        assert _composites(db_session, scene) == []
        assert outcome.values_added == ()
        assert _journal(db_session, scene) == []

    def test_a_merged_synonym_is_a_part_of_the_composite_by_its_target(
        self, db_session, factories
    ):
        scene = self._merged(db_session, factories)

        _run(db_session, scene, _several("all", "панели", "балки"))

        value_id, _source, _off_list = _row_of(db_session, scene)
        assert _parts_of(db_session, value_id) == _ids(scene, "плиты", "балки")
        assert _value(db_session, value_id).value == "плиты + балки"

    def test_filter_one_knows_a_part_by_a_merged_synonym(self, db_session, factories):
        scene = self._merged(db_session, factories)

        _run(db_session, scene, _new("панели, балки"))

        value_id, _source, _off_list = _row_of(db_session, scene)
        assert _value(db_session, value_id).composite_kind == "all"
        assert _parts_of(db_session, value_id) == _ids(scene, "плиты", "балки")

    def test_a_closed_parameter_reads_a_merged_synonym_as_listed(self, db_session, factories):
        scene = self._merged(db_session, factories, closed=True)

        _run(db_session, scene, _several("any", "панели", "балки"))

        value_id, source, off_list = _row_of(db_session, scene)
        assert (source, off_list) == ("name", None)
        assert _parts_of(db_session, value_id) == _ids(scene, "плиты", "балки")

    def test_a_closed_parameter_sets_the_target_of_a_merged_new_text(
        self, db_session, factories
    ):
        scene = self._merged(db_session, factories, closed=True)

        _run(db_session, scene, _new("панели"))

        assert _row_of(db_session, scene) == (scene.ids["плиты"], "name", None)


# ---------------------------------------------------------------------------
#  value_added
# ---------------------------------------------------------------------------

class TestValueAdded:
    def test_a_new_simple_value_is_journaled_with_its_context(self, db_session, factories):
        scene = _scene(db_session, factories, ("балки",))

        outcome = _run(db_session, scene, _new("капители"))

        [entry] = _journal(db_session, scene)
        value_id = _row_of(db_session, scene)[0]
        assert outcome.values_added == (value_id,)
        assert (entry.kind, entry.value_id, entry.parameter_id, entry.context_id) == (
            "value_added", value_id, scene.parameter_id, scene.context_id,
        )
        assert entry.payload == {
            "text": "капители", "origin_context_id": scene.context_id,
            "from_composite_id": None,
        }

    def test_a_listed_value_is_not_journaled(self, db_session, factories):
        scene = _scene(db_session, factories, ("балки",))

        _run(db_session, scene, ValueItem(1, "value", "балки", "name"))

        assert _journal(db_session, scene) == []


# ---------------------------------------------------------------------------
#  Контексты не перезапрашиваются
# ---------------------------------------------------------------------------

def test_an_applied_context_is_not_requested_again(db_session, factories, monkeypatch):
    # Контекст записан при прежнем тексте промпта; сверка при новом его не
    # перезапрашивает: нужда сравнивает `variant_paths_hash` с путями, а промпт и
    # формат в этот хэш не входят.
    scene = _scene(db_session, factories, ("балки", "плиты"))
    monkeypatch.setattr(variant_request, "VALUES_PROMPT", "прежний текст промпта")
    _run(db_session, scene, _several("all", "балки", "плиты"))
    monkeypatch.undo()

    reconcile_context_values(db_session, [scene.context_id], cap=NO_CAP, source="operation")

    assert _jobs(db_session, "context_values") == []
    # Положительный контроль: та же сверка ставит задание, когда хэш путей разошёлся.
    db_session.execute(
        sa.update(CatalogContext)
        .where(CatalogContext.id == scene.context_id)
        .values(variant_paths_hash="другой")
    )
    db_session.flush()
    reconcile_context_values(db_session, [scene.context_id], cap=NO_CAP, source="operation")
    assert len(_jobs(db_session, "context_values")) == 1


# ---------------------------------------------------------------------------
#  Место 5: заморозка схемы
# ---------------------------------------------------------------------------

class TestFreezeSchema:
    def _freeze(self, db, factories, scene, *values, name="Тип конструкции"):
        rebuild_schema(db, family_id=scene.family.id, actor_id=scene.actor)
        [job] = _jobs(db, "family_schema")
        outcome = freeze_schema(
            db, schema_id=job.schema_id,
            answer=_schema_answer(_parameter_in(1, name, *values)),
            guard=_claim(db, job), settings=_settings(),
        )
        assert outcome.applied
        db.expire_all()
        parameter = db.execute(
            sa.select(FamilyParameter).where(
                FamilyParameter.schema_id == job.schema_id, FamilyParameter.ordinal == 1
            )
        ).scalar_one()
        rows = {
            row.value: row
            for row in db.execute(
                sa.select(FamilyParameterValue).where(
                    FamilyParameterValue.parameter_id == parameter.id
                )
            ).scalars()
        }
        return SimpleNamespace(parameter=parameter, rows=rows)

    def _scene(self, db, factories):
        return _scene(db, factories, ("старое",))

    def test_a_value_glued_from_named_values_is_frozen_as_a_composite(
        self, db_session, factories
    ):
        scene = self._scene(db_session, factories)

        frozen = self._freeze(
            db_session, factories, scene, "балки", "капители", "балки, капители"
        )

        assert set(frozen.rows) == {"балки", "капители", "балки + капители"}
        composite = frozen.rows["балки + капители"]
        assert (composite.composite_kind, composite.origin) == ("all", "extension")
        assert _parts_of(db_session, composite.id) == sorted(
            (frozen.rows["балки"].id, frozen.rows["капители"].id)
        )
        assert (frozen.rows["балки"].origin, frozen.rows["капители"].origin) == (
            "schema", "schema",
        )
        [entry] = _journal(db_session, scene, "composite")
        assert entry.payload["text"] == "балки, капители"
        assert entry.value_id == composite.id
        assert entry.context_id is None

    def test_the_word_or_freezes_a_composite_of_kind_any(self, db_session, factories):
        scene = self._scene(db_session, factories)

        frozen = self._freeze(db_session, factories, scene, "Knauf", "Gyproc", "Knauf или Gyproc")

        assert frozen.rows["Knauf или Gyproc"].composite_kind == "any"

    @pytest.mark.parametrize(
        "values",
        [
            ("балки", "балки, капители"),
            ("ГО", "ГО и ЧС"),
            ("трубопровод с изоляцией и арматурой", "арматура"),
        ],
    )
    def test_a_glue_with_a_part_the_answer_does_not_name_stays_a_simple_value(
        self, db_session, factories, values
    ):
        scene = self._scene(db_session, factories)

        frozen = self._freeze(db_session, factories, scene, *values)

        assert set(frozen.rows) == set(values)
        assert {row.composite_kind for row in frozen.rows.values()} == {None}
        assert {row.origin for row in frozen.rows.values()} == {"schema"}
        assert _journal(db_session, scene, "composite") == []

    def test_a_glue_that_names_one_value_twice_stays_simple(self, db_session, factories):
        scene = self._scene(db_session, factories)

        frozen = self._freeze(db_session, factories, scene, "балки", "балки, балки")

        assert {row.composite_kind for row in frozen.rows.values()} == {None}

    def test_a_vetoed_composition_is_frozen_as_the_value_of_the_veto(self, db_session, factories):
        scene = _scene(db_session, factories, ("балки", "капители", "балки и капители"))
        add_veto(
            db_session, parameter_id=scene.parameter_id, kind="all",
            part_ids=[scene.ids["балки"], scene.ids["капители"]],
            value_id=scene.ids["балки и капители"], user_id=scene.actor,
        )

        frozen = self._freeze(
            db_session, factories, scene, "балки", "капители", "балки и капители"
        )

        assert set(frozen.rows) == {"балки", "капители", "балки и капители"}
        assert {row.composite_kind for row in frozen.rows.values()} == {None}
        assert composite_parts(db_session, [row.id for row in frozen.rows.values()]) == {}
        assert _journal(db_session, scene, "composite") == []
