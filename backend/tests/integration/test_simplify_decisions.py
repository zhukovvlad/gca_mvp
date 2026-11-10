"""Решения человека (спека упрощения каталога §2.6): вето состава и «не дубль», запись
журнала ИИ и перенос решений при слиянии семей.

Решение записано нормами текста, а не `id`: оно обязано пережить слияние значения и новую
версию схемы. Эталоны — независимые литералы: нормы записаны руками.
"""
from __future__ import annotations

import logging

import pytest
import sqlalchemy as sa

from models import (
    CatalogAutoAction,
    CompositeVeto,
    CompositeVetoPart,
    FamilyParameter,
    FamilyParameterValue,
    ValueNotDuplicate,
)
from services.ai_journal import record_action
from services.composite_values import find_or_create_composite
from services.human_decisions import (
    add_not_duplicate,
    add_veto,
    is_not_duplicate,
    resolve_veto,
)
from services.work_families import merge_families
from services.work_variants import freeze_schema, merge_parameter_values, rebuild_schema
from tests.integration.test_simplify_composite import (
    _journal,
    _make,
    _revision,
    _row,
    _scene,
)
from tests.integration.test_work_variants_core import _parameter_in, _schema_answer
from tests.integration.test_work_variants_material import _settings
from tests.integration.test_work_variants_schema import _param, _value
from tests.integration.test_work_variants_schema_life import _claim, _jobs

pytestmark = pytest.mark.integration

NAMES = ("плиты", "балки", "перекрытия", "колонны", "плиты и балки")


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _user(factories) -> int:
    return factories.UserFactory.create().id


def _vetoed(db, factories, scene, *names, kind="all", value="плиты и балки"):
    """Вето «состав `names` — значение `value`»; возвращает `id` вето."""
    return add_veto(
        db, parameter_id=scene.parameter_id, kind=kind,
        part_ids=[scene.ids[name] for name in names], value_id=scene.ids[value],
        user_id=_user(factories),
    )


def _merge(db, scene, source, target):
    merge_parameter_values(
        db, parameter_id=scene.parameter_id, source_value_id=scene.ids[source],
        target_value_id=scene.ids[target], actor_id=scene.actor,
    )


def _part_norms(db, veto_id) -> list[str]:
    db.expire_all()
    return sorted(
        db.execute(
            sa.select(CompositeVetoPart.part_norm).where(CompositeVetoPart.veto_id == veto_id)
        ).scalars()
    )


def _composites(db) -> int:
    db.expire_all()
    return db.execute(
        sa.select(sa.func.count()).select_from(FamilyParameterValue).where(
            FamilyParameterValue.composite_kind.is_not(None)
        )
    ).scalar_one()


def _new_version(db, scene):
    """Новая версия схемы: параметр человека переезжает, параметр модели — новый."""
    new = rebuild_schema(db, family_id=scene.family.id, actor_id=scene.actor)
    [job] = _jobs(db, "family_schema")
    outcome = freeze_schema(
        db, schema_id=new.id, answer=_schema_answer(_parameter_in(1, "Другой параметр", "а")),
        guard=_claim(db, job), settings=_settings(),
    )
    assert outcome.applied
    return new


def _carried(db, schema_id):
    """`(id параметра, {текст значения: id})` перенесённого параметра человека."""
    db.expire_all()
    parameter = db.execute(
        sa.select(FamilyParameter).where(
            FamilyParameter.schema_id == schema_id, FamilyParameter.ordinal == 1
        )
    ).scalar_one()
    values = {
        row.value: row.id
        for row in db.execute(
            sa.select(FamilyParameterValue).where(FamilyParameterValue.parameter_id == parameter.id)
        ).scalars()
    }
    return parameter.id, values


def _ask(db, parameter_id, ids, *, kind="all", source_text=None):
    return find_or_create_composite(
        db, parameter_id=parameter_id, kind=kind, part_ids=ids, context_id=None,
        source_text=source_text,
    )


# ---------------------------------------------------------------------------
#  Вето состава
# ---------------------------------------------------------------------------

class TestVetoWins:
    def test_the_veto_gives_its_value_and_no_composite_and_no_journal_record(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, NAMES)
        _vetoed(db_session, factories, scene, "плиты", "балки")
        revision = _revision(db_session, scene.schema.id)

        value_id = _make(db_session, scene, "балки", "плиты")

        assert value_id == scene.ids["плиты и балки"]
        assert _composites(db_session) == 0
        assert _journal(db_session, scene.family.id) == []
        assert _revision(db_session, scene.schema.id) == revision

    def test_the_veto_is_for_its_kind_only(self, db_session, factories):
        scene = _scene(db_session, factories, NAMES)
        _vetoed(db_session, factories, scene, "плиты", "балки", kind="all")

        value_id = _make(db_session, scene, "плиты", "балки", kind="any")

        assert _row(db_session, value_id).composite_kind == "any"

    def test_the_veto_is_for_its_exact_composition_only(self, db_session, factories):
        scene = _scene(db_session, factories, NAMES)
        _vetoed(db_session, factories, scene, "плиты", "балки")

        other = _make(db_session, scene, "плиты", "колонны")
        wider = _make(db_session, scene, "плиты", "балки", "колонны")

        assert _row(db_session, other).composite_kind == "all"
        assert _row(db_session, wider).composite_kind == "all"

    def test_the_veto_is_for_its_family_only(self, db_session, factories):
        scene = _scene(db_session, factories, NAMES)
        stranger = _scene(db_session, factories, NAMES)
        _vetoed(db_session, factories, scene, "плиты", "балки")

        value_id = _make(db_session, stranger, "плиты", "балки")

        assert _row(db_session, value_id).composite_kind == "all"

    def test_the_veto_is_for_its_parameter_name_only(self, db_session, factories):
        scene = _scene(db_session, factories, NAMES)
        other = _param(db_session, scene.schema, 2, "Материал")
        ids = {name: _value(db_session, other, name).id for name in ("плиты", "балки")}
        _vetoed(db_session, factories, scene, "плиты", "балки")

        value_id = _ask(db_session, other.id, [ids["плиты"], ids["балки"]])

        assert _row(db_session, value_id).composite_kind == "all"

    def test_the_veto_is_resolved_through_a_merged_name_of_a_part(self, db_session, factories):
        scene = _scene(db_session, factories, NAMES)
        _vetoed(db_session, factories, scene, "плиты", "балки")
        _merge(db_session, scene, "плиты", "перекрытия")

        through_new = _make(db_session, scene, "перекрытия", "балки")
        through_old = _make(db_session, scene, "плиты", "балки")

        assert through_new == through_old == scene.ids["плиты и балки"]
        assert _composites(db_session) == 0

    def test_the_merge_rewrites_the_norms_of_the_veto(self, db_session, factories):
        scene = _scene(db_session, factories, NAMES)
        veto_id = _vetoed(db_session, factories, scene, "плиты", "балки")

        _merge(db_session, scene, "плиты", "перекрытия")

        assert _part_norms(db_session, veto_id) == ["балки", "перекрытия"]

    def test_the_merge_of_the_value_of_the_veto_rewrites_its_value_norm(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, (*NAMES, "плиты вместе с балками"))
        veto_id = _vetoed(db_session, factories, scene, "плиты", "балки")

        _merge(db_session, scene, "плиты и балки", "плиты вместе с балками")

        db_session.expire_all()
        assert db_session.get(CompositeVeto, veto_id).value_norm == "плиты вместе с балками"
        assert _make(db_session, scene, "плиты", "балки") == scene.ids["плиты вместе с балками"]

    def test_the_veto_survives_a_new_version_of_the_schema(self, db_session, factories):
        scene = _scene(db_session, factories, NAMES, human=True)
        _vetoed(db_session, factories, scene, "плиты", "балки")

        new = _new_version(db_session, scene)

        parameter_id, values = _carried(db_session, new.id)
        value_id = _ask(db_session, parameter_id, [values["балки"], values["плиты"]])
        assert value_id == values["плиты и балки"]
        assert _composites(db_session) == 0

    def test_the_veto_survives_the_merge_and_then_a_new_version(self, db_session, factories):
        scene = _scene(db_session, factories, NAMES, human=True)
        _vetoed(db_session, factories, scene, "плиты", "балки")
        _merge(db_session, scene, "плиты", "перекрытия")

        new = _new_version(db_session, scene)

        parameter_id, values = _carried(db_session, new.id)
        assert "плиты" not in values
        value_id = _ask(db_session, parameter_id, [values["перекрытия"], values["балки"]])
        assert value_id == values["плиты и балки"]
        assert _composites(db_session) == 0

    def test_a_vetoed_composition_that_has_a_composite_already_still_gives_the_value(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, NAMES)
        _make(db_session, scene, "плиты", "балки")
        _vetoed(db_session, factories, scene, "плиты", "балки")

        assert _make(db_session, scene, "плиты", "балки") == scene.ids["плиты и балки"]


class TestResolutionThroughMergedRows:
    """Страховка разрешения (спека §2.6): норма решения, указывающая на слитую строку
    текущей версии, разрешается через `canonical_value_id`. Решения заводятся прямо
    ПОСЛЕ слияния, поэтому переписывание норм (`rewrite_after_merge`) их не касается —
    держит только канонизация при разрешении (ревью задачи 2)."""

    def _raw_veto(self, db, factories, scene, parts, value_norm):
        veto = CompositeVeto(
            family_id=scene.family.id, parameter_name_norm="тип конструкции", kind="all",
            value_norm=value_norm, set_by=_user(factories),
        )
        db.add(veto)
        db.flush()
        db.add_all(CompositeVetoPart(veto_id=veto.id, part_norm=norm) for norm in parts)
        db.flush()

    def test_a_part_norm_of_a_merged_row_resolves_to_its_canonical_value(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, NAMES)
        _merge(db_session, scene, "плиты", "перекрытия")
        self._raw_veto(db_session, factories, scene, ("плиты", "балки"), "плиты и балки")

        value_id = _make(db_session, scene, "перекрытия", "балки")

        assert value_id == scene.ids["плиты и балки"]
        assert _composites(db_session) == 0

    def test_a_value_norm_of_a_merged_row_gives_its_canonical_value(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, (*NAMES, "плиты вместе с балками"))
        _merge(db_session, scene, "плиты и балки", "плиты вместе с балками")
        self._raw_veto(db_session, factories, scene, ("плиты", "балки"), "плиты и балки")

        value_id = _make(db_session, scene, "плиты", "балки")

        assert value_id == scene.ids["плиты вместе с балками"]

    def test_a_not_duplicate_norm_of_a_merged_row_resolves_to_its_canonical_value(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, NAMES)
        _merge(db_session, scene, "плиты", "перекрытия")
        db_session.add(
            ValueNotDuplicate(
                family_id=scene.family.id, parameter_name_norm="тип конструкции",
                text_norm="плита", value_norm="плиты", set_by=_user(factories),
            )
        )
        db_session.flush()

        assert is_not_duplicate(
            db_session, parameter_id=scene.parameter_id, text_norm="плита",
            value_id=scene.ids["перекрытия"],
        ) is True


class TestVetoCollisions:
    def test_the_later_veto_wins_by_the_time_of_the_decision_not_by_the_id(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, (*NAMES, "плиты с балками"))
        first = _vetoed(db_session, factories, scene, "плиты", "балки", value="плиты и балки")
        second = _vetoed(
            db_session, factories, scene, "плиты", "балки", value="плиты с балками"
        )
        # у первого вето id меньше, а решение позднее
        db_session.execute(
            sa.update(CompositeVeto).where(CompositeVeto.id == first).values(
                set_at=sa.text("now() + interval '1 hour'")
            )
        )
        db_session.execute(
            sa.update(CompositeVeto).where(CompositeVeto.id == second).values(
                set_at=sa.text("now() - interval '1 hour'")
            )
        )

        assert _make(db_session, scene, "плиты", "балки") == scene.ids["плиты и балки"]

    def test_with_equal_times_the_bigger_id_wins(self, db_session, factories):
        scene = _scene(db_session, factories, (*NAMES, "плиты с балками"))
        _vetoed(db_session, factories, scene, "плиты", "балки", value="плиты и балки")
        _vetoed(db_session, factories, scene, "плиты", "балки", value="плиты с балками")
        db_session.execute(sa.update(CompositeVeto).values(set_at=sa.text("now()")))

        assert _make(db_session, scene, "плиты", "балки") == scene.ids["плиты с балками"]

    def test_a_collision_is_logged_and_both_vetoes_stay(
        self, db_session, factories, caplog
    ):
        scene = _scene(db_session, factories, (*NAMES, "плиты с балками"))
        _vetoed(db_session, factories, scene, "плиты", "балки", value="плиты и балки")
        _vetoed(db_session, factories, scene, "плиты", "балки", value="плиты с балками")

        with caplog.at_level(logging.WARNING, logger="services.human_decisions"):
            _make(db_session, scene, "плиты", "балки")

        assert any("collision" in record.getMessage() for record in caplog.records)
        assert db_session.execute(
            sa.select(sa.func.count()).select_from(CompositeVeto)
        ).scalar_one() == 2

    def test_vetoes_to_one_value_are_not_a_collision(self, db_session, factories, caplog):
        scene = _scene(db_session, factories, NAMES)
        _vetoed(db_session, factories, scene, "плиты", "балки")
        _vetoed(db_session, factories, scene, "плиты", "балки")

        with caplog.at_level(logging.WARNING, logger="services.human_decisions"):
            _make(db_session, scene, "плиты", "балки")

        assert not [r for r in caplog.records if "collision" in r.getMessage()]


class TestVetoStaysSilent:
    def test_parts_collapsed_into_one_make_the_veto_silent_and_one_row_is_left(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, NAMES)
        veto_id = _vetoed(db_session, factories, scene, "плиты", "балки")

        _merge(db_session, scene, "плиты", "балки")

        assert _part_norms(db_session, veto_id) == ["балки"]
        assert (
            resolve_veto(
                db_session, parameter_id=scene.parameter_id, kind="all",
                part_ids=frozenset({scene.ids["плиты"], scene.ids["балки"]}),
            )
            is None
        )
        other = _make(db_session, scene, "балки", "колонны")
        assert _row(db_session, other).composite_kind == "all"

    def test_a_part_that_is_not_in_the_parameter_makes_the_veto_silent(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, NAMES)
        veto = CompositeVeto(
            family_id=scene.family.id, parameter_name_norm="тип конструкции", kind="all",
            value_norm="плиты и балки", set_by=_user(factories),
        )
        db_session.add(veto)
        db_session.flush()
        db_session.add_all(
            CompositeVetoPart(veto_id=veto.id, part_norm=norm)
            for norm in ("плиты", "такой части нет")
        )
        db_session.flush()

        value_id = _make(db_session, scene, "плиты", "балки")

        assert _row(db_session, value_id).composite_kind == "all"

    def test_a_retired_value_of_the_veto_makes_it_silent(self, db_session, factories):
        scene = _scene(db_session, factories, NAMES)
        _vetoed(db_session, factories, scene, "плиты", "балки")
        db_session.execute(
            sa.update(FamilyParameterValue)
            .where(FamilyParameterValue.id == scene.ids["плиты и балки"])
            .values(retired_at=sa.func.now())
        )

        value_id = _make(db_session, scene, "плиты", "балки")

        assert _row(db_session, value_id).composite_kind == "all"

    def test_the_value_of_the_veto_that_is_absent_is_created_with_the_source_text(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, NAMES)
        _vetoed(db_session, factories, scene, "плиты", "балки")
        db_session.execute(
            sa.delete(FamilyParameterValue).where(
                FamilyParameterValue.id == scene.ids["плиты и балки"]
            )
        )

        created = _ask(
            db_session, scene.parameter_id, [scene.ids["плиты"], scene.ids["балки"]],
            source_text="  Плиты и Балки ",
        )

        row = _row(db_session, created)
        assert (row.value, row.value_norm, row.origin) == ("Плиты и Балки", "плиты и балки", "manual")
        assert row.composite_kind is None
        assert _composites(db_session) == 0
        assert _make(db_session, scene, "плиты", "балки") == created

    def test_the_absent_value_of_the_veto_takes_its_norm_when_the_text_is_another(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, NAMES)
        _vetoed(db_session, factories, scene, "плиты", "балки")
        db_session.execute(
            sa.delete(FamilyParameterValue).where(
                FamilyParameterValue.id == scene.ids["плиты и балки"]
            )
        )

        created = _ask(
            db_session, scene.parameter_id, [scene.ids["плиты"], scene.ids["балки"]],
            source_text="плиты вместе с балками",
        )

        row = _row(db_session, created)
        assert (row.value, row.value_norm) == ("плиты и балки", "плиты и балки")


class TestAddVeto:
    def test_the_veto_is_stored_by_text(self, db_session, factories):
        scene = _scene(db_session, factories, NAMES)
        user_id = _user(factories)

        veto_id = add_veto(
            db_session, parameter_id=scene.parameter_id, kind="any",
            part_ids=[scene.ids["балки"], scene.ids["плиты"]], value_id=scene.ids["плиты и балки"],
            user_id=user_id,
        )

        db_session.expire_all()
        veto = db_session.get(CompositeVeto, veto_id)
        assert (veto.family_id, veto.parameter_name_norm, veto.kind) == (
            scene.family.id, "тип конструкции", "any",
        )
        assert (veto.value_norm, veto.set_by) == ("плиты и балки", user_id)
        assert _part_norms(db_session, veto_id) == ["балки", "плиты"]

    @pytest.mark.parametrize(
        "case", ["composite_value", "composite_part", "one_part", "same_part_twice", "bad_kind"]
    )
    def test_a_bad_veto_is_refused(self, db_session, factories, case):
        scene = _scene(db_session, factories, NAMES)
        composite_id = _make(db_session, scene, "плиты", "колонны")
        arguments = dict(
            parameter_id=scene.parameter_id, kind="all",
            part_ids=[scene.ids["плиты"], scene.ids["балки"]], value_id=scene.ids["плиты и балки"],
            user_id=_user(factories),
        )
        if case == "composite_value":
            arguments["value_id"] = composite_id
        elif case == "composite_part":
            arguments["part_ids"] = [composite_id, scene.ids["балки"]]
        elif case == "one_part":
            arguments["part_ids"] = [scene.ids["балки"]]
        elif case == "same_part_twice":
            arguments["part_ids"] = [scene.ids["балки"], scene.ids["балки"]]
        else:
            arguments["kind"] = "both"

        with pytest.raises(ValueError):
            add_veto(db_session, **arguments)

    def test_a_value_of_another_parameter_is_refused(self, db_session, factories):
        scene = _scene(db_session, factories, NAMES)
        other = _param(db_session, scene.schema, 2, "Материал")
        foreign = _value(db_session, other, "бетон")

        with pytest.raises(ValueError):
            add_veto(
                db_session, parameter_id=scene.parameter_id, kind="all",
                part_ids=[scene.ids["плиты"], scene.ids["балки"]], value_id=foreign.id,
                user_id=_user(factories),
            )


# ---------------------------------------------------------------------------
#  «Не дубль»
# ---------------------------------------------------------------------------

class TestNotDuplicate:
    def _decide(self, db, factories, scene, text_norm="плита", value="плиты"):
        return add_not_duplicate(
            db, parameter_id=scene.parameter_id, text_norm=text_norm,
            value_id=scene.ids[value], user_id=_user(factories),
        )

    def _asks(self, db, scene, text_norm="плита", value="плиты"):
        return is_not_duplicate(
            db, parameter_id=scene.parameter_id, text_norm=text_norm, value_id=scene.ids[value]
        )

    def test_the_decision_forbids_exactly_its_pair(self, db_session, factories):
        scene = _scene(db_session, factories, NAMES)
        self._decide(db_session, factories, scene)

        assert self._asks(db_session, scene) is True
        assert self._asks(db_session, scene, text_norm="плитка") is False
        assert self._asks(db_session, scene, value="балки") is False

    def test_the_decision_is_stored_by_text(self, db_session, factories):
        scene = _scene(db_session, factories, NAMES)
        user_id = _user(factories)

        add_not_duplicate(
            db_session, parameter_id=scene.parameter_id, text_norm="плита",
            value_id=scene.ids["плиты"], user_id=user_id,
        )

        row = db_session.execute(sa.select(ValueNotDuplicate)).scalar_one()
        assert (row.family_id, row.parameter_name_norm, row.text_norm, row.value_norm) == (
            scene.family.id, "тип конструкции", "плита", "плиты",
        )
        assert row.set_by == user_id

    def test_the_same_decision_twice_is_one_row(self, db_session, factories):
        scene = _scene(db_session, factories, NAMES)

        first = self._decide(db_session, factories, scene)
        second = self._decide(db_session, factories, scene)

        assert first == second
        assert db_session.execute(
            sa.select(sa.func.count()).select_from(ValueNotDuplicate)
        ).scalar_one() == 1

    def test_the_decision_is_for_its_family_only(self, db_session, factories):
        scene = _scene(db_session, factories, NAMES)
        stranger = _scene(db_session, factories, NAMES)
        self._decide(db_session, factories, scene)

        assert self._asks(db_session, stranger) is False

    def test_the_decision_survives_the_merge_of_the_target(self, db_session, factories):
        scene = _scene(db_session, factories, NAMES)
        self._decide(db_session, factories, scene)

        _merge(db_session, scene, "плиты", "перекрытия")

        assert self._asks(db_session, scene, value="перекрытия") is True
        assert self._asks(db_session, scene, value="плиты") is True
        db_session.expire_all()
        row = db_session.execute(sa.select(ValueNotDuplicate)).scalar_one()
        assert row.value_norm == "перекрытия"

    def test_two_decisions_that_became_one_pair_collapse_into_one_row(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, NAMES)
        self._decide(db_session, factories, scene, value="плиты")
        self._decide(db_session, factories, scene, value="перекрытия")

        _merge(db_session, scene, "плиты", "перекрытия")

        db_session.expire_all()
        rows = db_session.execute(sa.select(ValueNotDuplicate)).scalars().all()
        assert [(r.text_norm, r.value_norm) for r in rows] == [("плита", "перекрытия")]

    def test_the_decision_survives_a_new_version_after_the_merge(self, db_session, factories):
        scene = _scene(db_session, factories, NAMES, human=True)
        self._decide(db_session, factories, scene)
        _merge(db_session, scene, "плиты", "перекрытия")

        new = _new_version(db_session, scene)

        parameter_id, values = _carried(db_session, new.id)
        assert is_not_duplicate(
            db_session, parameter_id=parameter_id, text_norm="плита",
            value_id=values["перекрытия"],
        ) is True
        assert is_not_duplicate(
            db_session, parameter_id=parameter_id, text_norm="плита", value_id=values["балки"]
        ) is False


# ---------------------------------------------------------------------------
#  Слияние значения и слияние семей
# ---------------------------------------------------------------------------

class TestMergeRewrite:
    def test_the_parts_that_became_equal_are_one_row(self, db_session, factories):
        scene = _scene(db_session, factories, NAMES)
        veto_id = _vetoed(db_session, factories, scene, "плиты", "балки", "перекрытия")

        _merge(db_session, scene, "плиты", "перекрытия")

        assert _part_norms(db_session, veto_id) == ["балки", "перекрытия"]

    def test_the_merge_of_a_value_nobody_decided_about_changes_no_decision(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, NAMES)
        veto_id = _vetoed(db_session, factories, scene, "плиты", "балки")

        _merge(db_session, scene, "колонны", "перекрытия")

        assert _part_norms(db_session, veto_id) == ["балки", "плиты"]

    def test_the_decisions_of_another_family_with_the_same_norms_are_not_rewritten(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, NAMES)
        stranger = _scene(db_session, factories, NAMES)
        veto_id = _vetoed(db_session, factories, stranger, "плиты", "балки")

        _merge(db_session, scene, "плиты", "перекрытия")

        assert _part_norms(db_session, veto_id) == ["балки", "плиты"]

    def test_the_decisions_of_another_parameter_of_the_family_are_not_rewritten(
        self, db_session, factories
    ):
        # те же нормы у параметра «Материал» той же семьи: переписка держится за
        # `name_norm` параметра слияния (ревью задачи 2)
        scene = _scene(db_session, factories, NAMES)
        other = _param(db_session, scene.schema, 2, "Материал")
        ids = {name: _value(db_session, other, name).id for name in ("плиты", "балки", "плиты и балки")}
        veto_id = add_veto(
            db_session, parameter_id=other.id, kind="all",
            part_ids=[ids["плиты"], ids["балки"]], value_id=ids["плиты и балки"],
            user_id=_user(factories),
        )
        add_not_duplicate(
            db_session, parameter_id=other.id, text_norm="плита", value_id=ids["плиты"],
            user_id=_user(factories),
        )

        _merge(db_session, scene, "плиты", "перекрытия")

        assert _part_norms(db_session, veto_id) == ["балки", "плиты"]
        db_session.expire_all()
        assert db_session.execute(sa.select(ValueNotDuplicate.value_norm)).scalar_one() == "плиты"


class TestFamilyMerge:
    def _two(self, db, factories):
        source = _scene(db, factories, NAMES)
        target = _scene(db, factories, NAMES)
        return source, target

    def test_the_decisions_of_the_source_family_move_to_the_target(self, db_session, factories):
        source, target = self._two(db_session, factories)
        veto_id = _vetoed(db_session, factories, source, "плиты", "балки")
        add_not_duplicate(
            db_session, parameter_id=source.parameter_id, text_norm="плита",
            value_id=source.ids["плиты"], user_id=_user(factories),
        )

        merge_families(
            db_session, source_family_id=source.family.id, target_family_id=target.family.id,
            actor_id=source.actor,
        )

        db_session.expire_all()
        assert db_session.get(CompositeVeto, veto_id).family_id == target.family.id
        assert db_session.execute(sa.select(ValueNotDuplicate.family_id)).scalar_one() == (
            target.family.id
        )

    def test_the_decisions_of_the_target_family_stay_and_a_twin_is_not_doubled(
        self, db_session, factories
    ):
        source, target = self._two(db_session, factories)
        for scene in (source, target):
            add_not_duplicate(
                db_session, parameter_id=scene.parameter_id, text_norm="плита",
                value_id=scene.ids["плиты"], user_id=_user(factories),
            )
        add_not_duplicate(
            db_session, parameter_id=source.parameter_id, text_norm="плитка",
            value_id=source.ids["плиты"], user_id=_user(factories),
        )

        merge_families(
            db_session, source_family_id=source.family.id, target_family_id=target.family.id,
            actor_id=source.actor,
        )

        db_session.expire_all()
        rows = db_session.execute(
            sa.select(ValueNotDuplicate.family_id, ValueNotDuplicate.text_norm).order_by(
                ValueNotDuplicate.text_norm
            )
        ).all()
        assert [tuple(r) for r in rows] == [
            (target.family.id, "плита"), (target.family.id, "плитка"),
        ]


# ---------------------------------------------------------------------------
#  Журнал ИИ
# ---------------------------------------------------------------------------

class TestRecordAction:
    def test_the_record_is_stored_with_its_subjects(self, db_session, factories):
        scene = _scene(db_session, factories, NAMES)

        action_id = record_action(
            db_session, kind="value_added", family_id=scene.family.id,
            payload={"text": "плиты", "origin_context_id": None, "from_composite_id": None},
            context_id=scene.world.context_id, parameter_id=scene.parameter_id,
            value_id=scene.ids["плиты"],
        )

        db_session.expire_all()
        row = db_session.get(CatalogAutoAction, action_id)
        assert (row.kind, row.family_id, row.context_id) == (
            "value_added", scene.family.id, scene.world.context_id,
        )
        assert (row.parameter_id, row.value_id) == (scene.parameter_id, scene.ids["плиты"])
        assert row.payload == {"text": "плиты", "origin_context_id": None, "from_composite_id": None}
        assert (row.undone_at, row.undone_by, row.restored_at) == (None, None, None)

    def test_the_subjects_are_optional(self, db_session, factories):
        scene = _scene(db_session, factories, NAMES)

        action_id = record_action(db_session, kind="split", family_id=scene.family.id, payload={})

        db_session.expire_all()
        row = db_session.get(CatalogAutoAction, action_id)
        assert (row.context_id, row.parameter_id, row.value_id, row.payload) == (
            None, None, None, {},
        )
