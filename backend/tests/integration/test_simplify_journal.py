"""«Журнал ИИ» на сервере (спека упрощения каталога §2.6, §2.7): чтение списка и записи,
отмена склейки и приравнивания, «вернуть автоматику».

Сцены строятся настоящим путём записи (`apply_values`: ответ `several` даёт составное,
ответ `new` с формой слова существующего значения даёт приравнивание), поэтому записи
журнала в тестах такие же, как в работе. Эталоны — независимые литералы: тексты, нормы
и порядок записаны руками. Значения сцены заводятся по возрастанию `id` в порядке
перечисления.
"""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager

import pytest
import sqlalchemy as sa

import crud.ai_journal as journal
from crud.common import DomainError
from models import (
    CatalogAutoAction,
    CatalogContext,
    CatalogPosition,
    CompositeVeto,
    CompositeVetoPart,
    ContextParameterValue,
    FamilyParameterSchema,
    FamilyParameterValue,
    FamilyValuePart,
    ValueNotDuplicate,
    ValueOrigin,
    WorkVariant,
)
from services.ai_journal import record_action
from services.composite_values import find_or_create_composite
from services.human_decisions import add_veto, is_not_duplicate
from services.variant_answer import ValueItem, ValuesAnswer
from services.work_variants import (
    apply_values,
    freeze_schema,
    get_or_create_value,
    merge_parameter_values,
    rebuild_schema,
)
from tests.integration.test_simplify_composite import _journal, _scene
from tests.integration.test_work_variants_core import _parameter_in, _schema_answer
from tests.integration.test_work_variants_material import _settings
from tests.integration.test_work_variants_schema_life import (
    _claim,
    _jobs,
    _paths_hash,
    _second_context,
)

pytestmark = pytest.mark.integration

BASE = "/api/v1/semantic/journal"


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _apply(db, scene, item):
    return apply_values(
        db, context_id=scene.world.context_id, schema_id=scene.schema.id,
        answer=ValuesAnswer(items=(item,)), paths_hash=_paths_hash(db, scene.world.context_id),
        guard=None, settings=_settings(),
    )


def _new(text):
    return ValueItem(1, "new", text, "name")


def _several(combine, *values):
    return ValueItem(1, "several", None, "name", values=tuple(values), combine=combine)


def _composite_scene(db, factories):
    """Составное «плиты + балки» на контексте сцены; запись журнала с исходным текстом
    «балки + плиты» (модель назвала части в другом порядке)."""
    scene = _scene(db, factories, ("плиты", "балки"))
    _apply(db, scene, _several("all", "балки", "плиты"))
    [entry] = _journal(db, scene.family.id)
    assert entry.kind == "composite"
    return scene, entry


def _equated_scene(db, factories):
    """Текст «плиты перекрытия» приравнен к значению «плита перекрытий»."""
    scene = _scene(db, factories, ("плита перекрытий", "балки"))
    _apply(db, scene, _new("плиты перекрытия"))
    [entry] = _journal(db, scene.family.id)
    assert entry.kind == "equated"
    return scene, entry


def _refused(call) -> tuple[int, str]:
    with pytest.raises(DomainError) as raised:
        call()
    return raised.value.status_code, raised.value.code


def _fresh(db, entry) -> CatalogAutoAction:
    db.expire_all()
    return db.get(CatalogAutoAction, entry.id)


def _value(db, value_id) -> FamilyParameterValue:
    db.expire_all()
    return db.get(FamilyParameterValue, value_id)


def _context_value(db, scene, context_id=None) -> int | None:
    db.expire_all()
    return db.execute(
        sa.select(ContextParameterValue.value_id).where(
            ContextParameterValue.context_id == (context_id or scene.world.context_id),
            ContextParameterValue.parameter_id == scene.parameter_id,
        )
    ).scalar_one_or_none()


def _revision(db, schema_id) -> int:
    db.expire_all()
    return db.execute(
        sa.select(FamilyParameterSchema.revision).where(FamilyParameterSchema.id == schema_id)
    ).scalar_one()


def _vetoes(db) -> list[tuple[str, str, list[str]]]:
    db.expire_all()
    return [
        (veto.kind, veto.value_norm, sorted(
            db.execute(
                sa.select(CompositeVetoPart.part_norm).where(CompositeVetoPart.veto_id == veto.id)
            ).scalars()
        ))
        for veto in db.execute(sa.select(CompositeVeto).order_by(CompositeVeto.id)).scalars()
    ]


def _not_duplicates(db) -> list[tuple[str, str]]:
    db.expire_all()
    return [
        tuple(row)
        for row in db.execute(
            sa.select(ValueNotDuplicate.text_norm, ValueNotDuplicate.value_norm).order_by(
                ValueNotDuplicate.id
            )
        ).all()
    ]


def _parts(db, value_id) -> list[int]:
    db.expire_all()
    return sorted(
        db.execute(
            sa.select(FamilyValuePart.part_value_id).where(FamilyValuePart.value_id == value_id)
        ).scalars()
    )


def _variant_key(db, context_id) -> str:
    db.expire_all()
    variant_id = db.get(CatalogContext, context_id).work_variant_id
    return db.get(WorkVariant, variant_id).values_key


def _context_title(db, scene) -> str:
    db.expire_all()
    return db.get(CatalogPosition, scene.world.catalog_id).standard_job_title


def _record(db, scene, kind, payload, *, created=None, value_id=None, parameter_id=None):
    action_id = record_action(
        db, kind=kind, family_id=scene.family.id, payload=payload,
        context_id=scene.world.context_id, parameter_id=parameter_id, value_id=value_id,
    )
    if created is not None:
        db.execute(
            sa.update(CatalogAutoAction)
            .where(CatalogAutoAction.id == action_id)
            .values(created_at=created)
        )
        db.flush()
    return action_id


def _added(text):
    return {"text": text, "origin_context_id": None, "from_composite_id": None}


def _at(hour) -> dt.datetime:
    return dt.datetime(2026, 11, 1, hour, 0, tzinfo=dt.UTC)


def _ids(page) -> list[int]:
    return [item["id"] for item in page["items"]]


def _list(db, **overrides):
    arguments = dict(kind=None, family_id=None, state=None, q=None, limit=50, offset=0)
    arguments.update(overrides)
    return journal.list_journal(db, **arguments)


@contextmanager
def _statements(db):
    seen: list[str] = []

    def _tick(_conn, _cursor, statement, *_args, **_kwargs):
        if not statement.startswith(("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")):
            seen.append(statement)

    engine = db.get_bind()
    sa.event.listen(engine, "before_cursor_execute", _tick)
    try:
        yield seen
    finally:
        sa.event.remove(engine, "before_cursor_execute", _tick)


def _writes(seen) -> list[str]:
    return [s for s in seen if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]


# ---------------------------------------------------------------------------
#  Список
# ---------------------------------------------------------------------------

class TestList:
    def test_the_order_is_newest_first_and_the_id_breaks_a_tie(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        oldest = _record(db_session, scene, "value_added", _added("а"), created=_at(10))
        tied_first = _record(db_session, scene, "value_added", _added("б"), created=_at(12))
        tied_last = _record(db_session, scene, "value_added", _added("в"), created=_at(12))

        page = _list(db_session)

        assert _ids(page) == [tied_last, tied_first, oldest]

    def test_a_page_has_the_total_of_the_whole_selection(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        ids = [
            _record(db_session, scene, "value_added", _added(str(n)), created=_at(10 + n))
            for n in range(3)
        ]

        page = _list(db_session, limit=2, offset=1)

        assert _ids(page) == [ids[1], ids[0]]
        assert page["total"] == 3

    def test_the_kind_family_and_state_narrow_the_selection(self, db_session, factories):
        first = _scene(db_session, factories, ("плиты", "балки"))
        second = _scene(db_session, factories, ("плиты", "балки"))
        added = _record(db_session, first, "value_added", _added("а"))
        composite = _record(db_session, first, "composite", {"text": "б"})
        other_family = _record(db_session, second, "composite", {"text": "в"})
        db_session.execute(
            sa.update(CatalogAutoAction)
            .where(CatalogAutoAction.id == composite)
            .values(undone_at=_at(9), undone_by=first.actor)
        )
        db_session.flush()

        assert set(_ids(_list(db_session, kind="composite"))) == {composite, other_family}
        assert _ids(_list(db_session, family_id=first.family.id, kind="composite")) == [composite]
        assert _ids(_list(db_session, family_id=first.family.id, state="active")) == [added]
        assert _ids(_list(db_session, family_id=first.family.id, state="undone")) == [composite]

    def test_the_locked_state_selects_nothing_before_the_split_exists(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        _record(db_session, scene, "value_added", _added("а"))

        assert _list(db_session, state="locked")["total"] == 0

    def test_the_search_finds_the_text_of_the_record_the_family_and_the_value(
        self, db_session, factories
    ):
        by_text = _scene(db_session, factories, ("плиты", "балки"))
        by_family = _scene(db_session, factories, ("плиты", "балки"))
        by_value = _scene(db_session, factories, ("плиты", "балки"))
        by_family.family.title = "Кровля скатная"
        db_session.flush()
        text_id = _record(db_session, by_text, "value_added", _added("Фасадная плитка"))
        family_id = _record(db_session, by_family, "value_added", _added("zzz"))
        value_id = _record(
            db_session, by_value, "value_added", _added("yyy"),
            value_id=by_value.ids["балки"], parameter_id=by_value.parameter_id,
        )

        assert _ids(_list(db_session, q="ФАСАДНАЯ")) == [text_id]
        assert _ids(_list(db_session, q="кровл")) == [family_id]
        assert _ids(_list(db_session, q="БАЛКИ")) == [value_id]
        assert _list(db_session, q="нет такого текста")["total"] == 0

    def test_a_percent_sign_in_the_search_is_a_letter_not_a_pattern(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        plain = _record(db_session, scene, "value_added", _added("раствор 40 процентов"))
        percent = _record(db_session, scene, "value_added", _added("раствор 40% марки"))

        assert _ids(_list(db_session, q="40%")) == [percent]
        assert plain not in _ids(_list(db_session, q="%"))

    def test_the_counts_ignore_the_kind_and_obey_every_other_filter(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        _record(db_session, scene, "value_added", _added("а"))
        _record(db_session, scene, "value_added", _added("б"))
        undone = _record(db_session, scene, "composite", {"text": "в"})
        _record(db_session, scene, "equated", {"text": "г"})
        db_session.execute(
            sa.update(CatalogAutoAction)
            .where(CatalogAutoAction.id == undone)
            .values(undone_at=_at(9), undone_by=scene.actor)
        )
        db_session.flush()

        everything = _list(db_session, kind="composite")
        active = _list(db_session, state="active")

        assert everything["total"] == 1
        assert everything["counts"] == {"split": 0, "value_added": 2, "composite": 1, "equated": 1}
        assert active["counts"] == {"split": 0, "value_added": 2, "composite": 0, "equated": 1}


class TestItem:
    def test_a_composite_shows_its_parts_text_and_subjects(self, db_session, factories):
        scene, entry = _composite_scene(db_session, factories)

        item = journal.get_journal_item(db_session, entry.id)

        assert item["id"] == entry.id
        assert item["kind"] == "composite"
        assert item["created_at"] == entry.created_at.isoformat()
        assert item["family"] == {"id": scene.family.id, "title": scene.family.title}
        assert item["context"] == {
            "id": scene.world.context_id, "title": _context_title(db_session, scene),
        }
        assert item["parameter"] == {"id": scene.parameter_id, "name": "Тип конструкции"}
        assert item["text"] == "балки + плиты"
        assert item["summary_values"] == ["плиты", "балки"]
        assert item["state"] == "active"
        assert item["undo"] == {"available": True, "locked_by": None}
        assert item["undone"] is None
        assert item["restored_at"] is None
        assert item["details"] == {
            "contexts": [{
                "id": scene.world.context_id, "title": _context_title(db_session, scene),
                "parameter_value": "плиты + балки",
            }],
            "undo_text": "балки + плиты",
        }

    def test_an_equated_record_shows_the_text_and_the_value_it_was_given(
        self, db_session, factories
    ):
        scene, entry = _equated_scene(db_session, factories)

        item = journal.get_journal_item(db_session, entry.id)

        assert item["kind"] == "equated"
        assert item["text"] == "плиты перекрытия"
        assert item["summary_values"] == ["плиты перекрытия", "плита перекрытий"]
        assert item["undo"] == {"available": True, "locked_by": None}
        assert item["details"]["contexts"][0]["parameter_value"] == "плита перекрытий"
        assert item["details"]["undo_text"] == "плиты перекрытия"

    def test_a_value_added_record_cannot_be_undone(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        action_id = _record(
            db_session, scene, "value_added", _added("колонны"),
            value_id=scene.ids["балки"], parameter_id=scene.parameter_id,
        )

        item = journal.get_journal_item(db_session, action_id)

        assert item["summary_values"] == ["колонны"]
        assert item["undo"] == {"available": False, "locked_by": None}
        assert "details" not in item

    def test_a_split_shows_the_titles_of_its_groups(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        action_id = _record(
            db_session, scene, "split",
            {"level": 1, "rule_ids": [], "groups": [
                {"context_id": 1, "title": "Колонны", "value_ids": [], "members": 2},
                {"context_id": 2, "title": "Стены", "value_ids": [], "members": 3},
            ]},
        )

        item = journal.get_journal_item(db_session, action_id)

        assert item["text"] is None
        assert item["summary_values"] == ["Колонны", "Стены"]

    def test_a_subject_that_is_gone_leaves_empty_references(self, db_session, factories):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        action_id = record_action(
            db_session, kind="value_added", family_id=scene.family.id, payload=_added("а"),
        )

        item = journal.get_journal_item(db_session, action_id)

        assert (item["context"], item["parameter"]) == (None, None)
        assert item["summary_values"] == ["а"]

    def test_an_undone_record_names_who_and_when(self, db_session, factories):
        scene, entry = _composite_scene(db_session, factories)
        admin = factories.UserFactory.create()

        journal.undo_action(db_session, entry.id, user_id=admin.id)
        item = journal.get_journal_item(db_session, entry.id)

        assert item["state"] == "undone"
        assert item["undo"] == {"available": False, "locked_by": None}
        assert item["undone"] == {
            "at": _fresh(db_session, entry).undone_at.isoformat(), "by_email": admin.email,
        }

    def test_a_missing_record_is_not_found(self, db_session):
        assert _refused(lambda: journal.get_journal_item(db_session, 987654)) == (
            404, journal.REFUSE_JOURNAL_NOT_FOUND,
        )


# ---------------------------------------------------------------------------
#  Отмена склейки
# ---------------------------------------------------------------------------

class TestUndoComposite:
    def test_the_value_becomes_simple_with_the_source_text(self, db_session, factories):
        scene, entry = _composite_scene(db_session, factories)
        composite_id = entry.value_id
        parts = scene.ids["плиты"], scene.ids["балки"]

        journal.undo_action(db_session, entry.id, user_id=scene.actor)

        row = _value(db_session, composite_id)
        assert (row.composite_kind, row.composite_key) == (None, None)
        assert (row.value, row.value_norm) == ("балки + плиты", "балки + плиты")
        assert row.origin == "extension"
        assert _parts(db_session, composite_id) == []
        for part_id in parts:  # части остались простыми
            assert _value(db_session, part_id).composite_kind is None

    def test_the_contexts_stay_on_the_same_value_and_variant(self, db_session, factories):
        scene, entry = _composite_scene(db_session, factories)
        composite_id = entry.value_id
        key_before = _variant_key(db_session, scene.world.context_id)

        journal.undo_action(db_session, entry.id, user_id=scene.actor)

        assert _context_value(db_session, scene) == composite_id
        assert _variant_key(db_session, scene.world.context_id) == key_before == f"1={composite_id}"

    def test_the_veto_is_written_by_the_part_norms(self, db_session, factories):
        scene, entry = _composite_scene(db_session, factories)

        journal.undo_action(db_session, entry.id, user_id=scene.actor)

        assert _vetoes(db_session) == [("all", "балки + плиты", ["балки", "плиты"])]
        db_session.expire_all()
        veto = db_session.execute(sa.select(CompositeVeto)).scalar_one()
        assert veto.set_by == scene.actor
        row = _fresh(db_session, entry)
        assert row.payload["undo_decision"] == {"veto_id": veto.id}
        assert row.payload["text"] == "балки + плиты"

    def test_the_record_gets_who_and_when_together(self, db_session, factories):
        scene, entry = _composite_scene(db_session, factories)

        journal.undo_action(db_session, entry.id, user_id=scene.actor)

        row = _fresh(db_session, entry)
        assert row.undone_by == scene.actor
        assert row.undone_at is not None
        assert row.restored_at is None

    def test_the_same_parts_asked_again_give_that_value(self, db_session, factories):
        scene, entry = _composite_scene(db_session, factories)
        journal.undo_action(db_session, entry.id, user_id=scene.actor)
        other = _scene(db_session, factories, ("плиты", "балки"))

        _apply(db_session, scene, _several("all", "плиты", "балки"))

        assert _context_value(db_session, scene) == entry.value_id
        assert [row.kind for row in _journal(db_session, scene.family.id)] == ["composite"]
        # вето записано для семьи и параметра отменённой склейки, чужая семья не затронута
        _apply(db_session, other, _several("all", "плиты", "балки"))
        [other_entry] = _journal(db_session, other.family.id)
        assert _context_value(db_session, other) == other_entry.value_id

    def test_the_schema_revision_grows_by_one(self, db_session, factories):
        scene, entry = _composite_scene(db_session, factories)
        before = _revision(db_session, scene.schema.id)

        journal.undo_action(db_session, entry.id, user_id=scene.actor)

        assert _revision(db_session, scene.schema.id) == before + 1

    def test_a_norm_taken_by_another_value_refuses_and_changes_nothing(
        self, db_session, factories
    ):
        scene, entry = _composite_scene(db_session, factories)
        get_or_create_value(
            db_session, parameter_id=scene.parameter_id, text="Балки + плиты",
            origin=ValueOrigin.extension,
        )
        before = _revision(db_session, scene.schema.id)

        assert _refused(lambda: journal.undo_action(db_session, entry.id, user_id=scene.actor)) == (
            409, journal.REFUSE_JOURNAL_UNDO_CONFLICT,
        )

        row = _value(db_session, entry.value_id)
        assert row.composite_kind == "all"
        assert _parts(db_session, entry.value_id) == sorted(
            [scene.ids["плиты"], scene.ids["балки"]]
        )
        assert _vetoes(db_session) == []
        assert _fresh(db_session, entry).undone_at is None
        assert _revision(db_session, scene.schema.id) == before

    def test_a_second_undo_is_refused(self, db_session, factories):
        scene, entry = _composite_scene(db_session, factories)
        journal.undo_action(db_session, entry.id, user_id=scene.actor)

        assert _refused(lambda: journal.undo_action(db_session, entry.id, user_id=scene.actor)) == (
            409, journal.REFUSE_JOURNAL_UNDONE,
        )
        assert len(_vetoes(db_session)) == 1

    def test_a_composite_merged_into_another_is_not_applicable(self, db_session, factories):
        scene, entry = _composite_scene(db_session, factories)
        # `merge_parameter_values` без каскада отказывает составному в слиянии с простым,
        # поэтому цель — другое составное того же вида
        get_or_create_value(
            db_session, parameter_id=scene.parameter_id, text="колонны",
            origin=ValueOrigin.extension,
        )
        columns = db_session.execute(
            sa.select(FamilyParameterValue.id).where(
                FamilyParameterValue.parameter_id == scene.parameter_id,
                FamilyParameterValue.value_norm == "колонны",
            )
        ).scalar_one()
        target = find_or_create_composite(
            db_session, parameter_id=scene.parameter_id, kind="all",
            part_ids=[scene.ids["плиты"], columns], context_id=None, source_text=None,
        )
        merge_parameter_values(
            db_session, parameter_id=scene.parameter_id, source_value_id=entry.value_id,
            target_value_id=target, actor_id=scene.actor,
        )

        assert _refused(lambda: journal.undo_action(db_session, entry.id, user_id=scene.actor)) == (
            409, journal.REFUSE_JOURNAL_UNDO_NOT_APPLICABLE,
        )
        assert _vetoes(db_session) == []

    def test_a_retired_composite_is_not_applicable(self, db_session, factories):
        scene, entry = _composite_scene(db_session, factories)
        db_session.execute(
            sa.update(FamilyParameterValue)
            .where(FamilyParameterValue.id == entry.value_id)
            .values(retired_at=_at(8))
        )
        db_session.flush()

        assert _refused(lambda: journal.undo_action(db_session, entry.id, user_id=scene.actor)) == (
            409, journal.REFUSE_JOURNAL_UNDO_NOT_APPLICABLE,
        )
        assert _vetoes(db_session) == []

    def test_a_record_that_lost_its_value_is_not_applicable(self, db_session, factories):
        scene, entry = _composite_scene(db_session, factories)
        db_session.execute(
            sa.update(CatalogAutoAction).where(CatalogAutoAction.id == entry.id).values(value_id=None)
        )
        db_session.flush()

        assert journal.get_journal_item(db_session, entry.id)["undo"]["available"] is False
        assert _refused(lambda: journal.undo_action(db_session, entry.id, user_id=scene.actor)) == (
            409, journal.REFUSE_JOURNAL_UNDO_NOT_APPLICABLE,
        )

    def test_a_record_that_lost_its_parameter_is_not_applicable(self, db_session, factories):
        scene, entry = _composite_scene(db_session, factories)
        db_session.execute(
            sa.update(CatalogAutoAction).where(CatalogAutoAction.id == entry.id).values(parameter_id=None)
        )
        db_session.flush()

        assert journal.get_journal_item(db_session, entry.id)["undo"]["available"] is False
        assert _refused(lambda: journal.undo_action(db_session, entry.id, user_id=scene.actor)) == (
            409, journal.REFUSE_JOURNAL_UNDO_NOT_APPLICABLE,
        )

    def test_a_rebuilt_schema_makes_the_record_not_applicable(self, db_session, factories):
        scene, entry = _composite_scene(db_session, factories)
        new = rebuild_schema(db_session, family_id=scene.family.id, actor_id=scene.actor)
        [job] = _jobs(db_session, "family_schema")
        outcome = freeze_schema(
            db_session, schema_id=new.id,
            answer=_schema_answer(_parameter_in(1, "Другой параметр", "а")),
            guard=_claim(db_session, job), settings=_settings(),
        )
        assert outcome.applied

        assert _refused(lambda: journal.undo_action(db_session, entry.id, user_id=scene.actor)) == (
            409, journal.REFUSE_JOURNAL_UNDO_NOT_APPLICABLE,
        )
        assert _fresh(db_session, entry).undone_at is None

    def test_a_missing_record_is_not_found(self, db_session, factories):
        assert _refused(
            lambda: journal.undo_action(db_session, 987654, user_id=_scene_user(factories))
        ) == (404, journal.REFUSE_JOURNAL_NOT_FOUND)

    @pytest.mark.parametrize("kind", ["value_added"])
    def test_the_kinds_without_an_undo_here_are_refused(self, db_session, factories, kind):
        scene = _scene(db_session, factories, ("плиты", "балки"))
        action_id = _record(db_session, scene, kind, _added("а"))

        assert _refused(
            lambda: journal.undo_action(db_session, action_id, user_id=scene.actor)
        ) == (409, journal.REFUSE_JOURNAL_UNDO_NOT_APPLICABLE)
        db_session.expire_all()
        assert db_session.get(CatalogAutoAction, action_id).undone_at is None


def _scene_user(factories) -> int:
    return factories.UserFactory.create().id


# ---------------------------------------------------------------------------
#  Отмена приравнивания
# ---------------------------------------------------------------------------

class TestUndoEquated:
    def test_a_simple_value_is_created_from_the_text(self, db_session, factories):
        scene, entry = _equated_scene(db_session, factories)
        target = entry.value_id

        journal.undo_action(db_session, entry.id, user_id=scene.actor)

        db_session.expire_all()
        created = db_session.execute(
            sa.select(FamilyParameterValue).where(
                FamilyParameterValue.parameter_id == scene.parameter_id,
                FamilyParameterValue.value_norm == "плиты перекрытия",
            )
        ).scalar_one()
        assert (created.value, created.origin, created.composite_kind) == (
            "плиты перекрытия", "extension", None,
        )
        assert created.id != target
        assert _value(db_session, target).value == "плита перекрытий"

    def test_the_contexts_of_the_record_move_and_the_variant_follows(self, db_session, factories):
        scene, entry = _equated_scene(db_session, factories)
        target = entry.value_id
        assert _variant_key(db_session, scene.world.context_id) == f"1={target}"

        journal.undo_action(db_session, entry.id, user_id=scene.actor)

        moved = _context_value(db_session, scene)
        assert moved not in (None, target)
        assert _value(db_session, moved).value == "плиты перекрытия"
        assert _variant_key(db_session, scene.world.context_id) == f"1={moved}"
        source = db_session.execute(
            sa.select(ContextParameterValue.source).where(
                ContextParameterValue.context_id == scene.world.context_id,
                ContextParameterValue.parameter_id == scene.parameter_id,
            )
        ).scalar_one()
        assert source == "manual"

    def test_a_context_outside_the_record_stays_on_the_value(self, db_session, factories):
        scene, entry = _equated_scene(db_session, factories)
        bystander = _second_context(db_session, factories, scene.world)
        db_session.add(
            ContextParameterValue(
                context_id=bystander, schema_id=scene.schema.id,
                parameter_id=scene.parameter_id, value_id=entry.value_id, source="name",
            )
        )
        db_session.flush()

        journal.undo_action(db_session, entry.id, user_id=scene.actor)

        assert _context_value(db_session, scene, bystander) == entry.value_id

    def test_a_context_that_left_the_value_is_not_pulled_back(self, db_session, factories):
        scene, entry = _equated_scene(db_session, factories)
        elsewhere = scene.ids["балки"]
        db_session.execute(
            sa.update(ContextParameterValue)
            .where(ContextParameterValue.context_id == scene.world.context_id)
            .values(value_id=elsewhere)
        )
        db_session.flush()

        journal.undo_action(db_session, entry.id, user_id=scene.actor)

        assert _context_value(db_session, scene) == elsewhere

    def test_the_pair_is_marked_not_a_duplicate_and_the_filter_reads_it(
        self, db_session, factories
    ):
        scene, entry = _equated_scene(db_session, factories)

        journal.undo_action(db_session, entry.id, user_id=scene.actor)

        assert _not_duplicates(db_session) == [("плиты перекрытия", "плита перекрытий")]
        db_session.expire_all()
        decision = db_session.execute(sa.select(ValueNotDuplicate)).scalar_one()
        assert _fresh(db_session, entry).payload["undo_decision"] == {
            "not_duplicate_id": decision.id
        }
        assert is_not_duplicate(
            db_session, parameter_id=scene.parameter_id, text_norm="плиты перекрытия",
            value_id=entry.value_id,
        )

    def test_the_same_text_asked_again_is_not_equated(self, db_session, factories):
        scene, entry = _equated_scene(db_session, factories)
        journal.undo_action(db_session, entry.id, user_id=scene.actor)
        other = _scene(db_session, factories, ("плита перекрытий", "балки"))

        _apply(db_session, scene, _new("плиты перекрытия"))

        db_session.expire_all()
        assert _value(db_session, _context_value(db_session, scene)).value == "плиты перекрытия"
        assert [row.kind for row in _journal(db_session, scene.family.id)] == ["equated"]
        # другая семья по-прежнему приравнивает: решение принадлежит семье
        _apply(db_session, other, _new("плиты перекрытия"))
        assert [row.kind for row in _journal(db_session, other.family.id)] == ["equated"]

    def test_a_listed_simple_value_with_that_norm_is_taken_not_duplicated(
        self, db_session, factories
    ):
        scene, entry = _equated_scene(db_session, factories)
        existing, _ = get_or_create_value(
            db_session, parameter_id=scene.parameter_id, text="Плиты перекрытия",
            origin=ValueOrigin.manual,
        )
        before = _value_count(db_session, scene)

        journal.undo_action(db_session, entry.id, user_id=scene.actor)

        assert _value_count(db_session, scene) == before
        assert _context_value(db_session, scene) == existing

    def test_a_retired_value_with_that_norm_returns_to_the_list(self, db_session, factories):
        scene, entry = _equated_scene(db_session, factories)
        existing, _ = get_or_create_value(
            db_session, parameter_id=scene.parameter_id, text="плиты перекрытия",
            origin=ValueOrigin.manual,
        )
        db_session.execute(
            sa.update(FamilyParameterValue)
            .where(FamilyParameterValue.id == existing)
            .values(retired_at=_at(8))
        )
        db_session.flush()

        journal.undo_action(db_session, entry.id, user_id=scene.actor)

        assert _value(db_session, existing).retired_at is None
        assert _context_value(db_session, scene) == existing

    def test_a_composite_with_that_norm_refuses_and_changes_nothing(self, db_session, factories):
        scene, entry = _equated_scene(db_session, factories)
        occupant, _ = get_or_create_value(
            db_session, parameter_id=scene.parameter_id, text="плиты перекрытия",
            origin=ValueOrigin.manual,
        )
        db_session.execute(
            sa.update(FamilyParameterValue)
            .where(FamilyParameterValue.id == occupant)
            .values(composite_kind="all", composite_key="all:1,2")
        )
        db_session.flush()

        assert _refused(lambda: journal.undo_action(db_session, entry.id, user_id=scene.actor)) == (
            409, journal.REFUSE_JOURNAL_UNDO_CONFLICT,
        )

        assert _context_value(db_session, scene) == entry.value_id
        assert _not_duplicates(db_session) == []
        assert _fresh(db_session, entry).undone_at is None

    def test_a_text_already_merged_into_the_value_refuses(self, db_session, factories):
        scene, entry = _equated_scene(db_session, factories)
        synonym, _ = get_or_create_value(
            db_session, parameter_id=scene.parameter_id, text="плиты перекрытия",
            origin=ValueOrigin.manual,
        )
        merge_parameter_values(
            db_session, parameter_id=scene.parameter_id, source_value_id=synonym,
            target_value_id=entry.value_id, actor_id=scene.actor,
        )

        assert _refused(lambda: journal.undo_action(db_session, entry.id, user_id=scene.actor)) == (
            409, journal.REFUSE_JOURNAL_UNDO_CONFLICT,
        )
        assert _not_duplicates(db_session) == []

    def test_the_schema_revision_grows_and_a_second_undo_is_refused(self, db_session, factories):
        scene, entry = _equated_scene(db_session, factories)
        before = _revision(db_session, scene.schema.id)

        journal.undo_action(db_session, entry.id, user_id=scene.actor)

        assert _revision(db_session, scene.schema.id) > before
        assert _refused(lambda: journal.undo_action(db_session, entry.id, user_id=scene.actor)) == (
            409, journal.REFUSE_JOURNAL_UNDONE,
        )
        assert len(_not_duplicates(db_session)) == 1

    def test_a_rebuilt_schema_makes_the_record_not_applicable(self, db_session, factories):
        scene, entry = _equated_scene(db_session, factories)
        new = rebuild_schema(db_session, family_id=scene.family.id, actor_id=scene.actor)
        [job] = _jobs(db_session, "family_schema")
        freeze_schema(
            db_session, schema_id=new.id,
            answer=_schema_answer(_parameter_in(1, "Другой параметр", "а")),
            guard=_claim(db_session, job), settings=_settings(),
        )

        assert _refused(lambda: journal.undo_action(db_session, entry.id, user_id=scene.actor)) == (
            409, journal.REFUSE_JOURNAL_UNDO_NOT_APPLICABLE,
        )
        assert _not_duplicates(db_session) == []


def _value_count(db, scene) -> int:
    db.expire_all()
    return db.execute(
        sa.select(sa.func.count()).select_from(FamilyParameterValue).where(
            FamilyParameterValue.parameter_id == scene.parameter_id
        )
    ).scalar_one()


# ---------------------------------------------------------------------------
#  «Вернуть автоматику»
# ---------------------------------------------------------------------------

class TestRestoreAuto:
    def test_the_veto_and_its_parts_are_removed_and_the_value_stays_simple(
        self, db_session, factories
    ):
        scene, entry = _composite_scene(db_session, factories)
        journal.undo_action(db_session, entry.id, user_id=scene.actor)

        journal.restore_auto(db_session, entry.id, user_id=scene.actor)

        assert _vetoes(db_session) == []
        db_session.expire_all()
        assert db_session.execute(sa.select(sa.func.count()).select_from(CompositeVetoPart)).scalar_one() == 0
        row = _fresh(db_session, entry)
        assert row.restored_at is not None
        assert row.undone_at is not None
        assert _value(db_session, entry.value_id).composite_kind is None

    def test_after_the_restore_the_same_parts_make_a_composite_again(self, db_session, factories):
        scene, entry = _composite_scene(db_session, factories)
        journal.undo_action(db_session, entry.id, user_id=scene.actor)
        journal.restore_auto(db_session, entry.id, user_id=scene.actor)

        _apply(db_session, scene, _several("all", "плиты", "балки"))

        again = _context_value(db_session, scene)
        assert again != entry.value_id
        assert _value(db_session, again).composite_kind == "all"

    def test_only_the_decision_of_this_record_is_removed(self, db_session, factories):
        scene, entry = _composite_scene(db_session, factories)
        journal.undo_action(db_session, entry.id, user_id=scene.actor)
        get_or_create_value(
            db_session, parameter_id=scene.parameter_id, text="колонны",
            origin=ValueOrigin.manual,
        )
        kept = add_veto(
            db_session, parameter_id=scene.parameter_id, kind="any",
            part_ids=[scene.ids["плиты"], scene.ids["балки"]], value_id=entry.value_id,
            user_id=scene.actor,
        )

        journal.restore_auto(db_session, entry.id, user_id=scene.actor)

        assert _vetoes(db_session) == [("any", "балки + плиты", ["балки", "плиты"])]
        db_session.expire_all()
        assert db_session.get(CompositeVeto, kept) is not None

    def test_the_not_duplicate_mark_is_removed(self, db_session, factories):
        scene, entry = _equated_scene(db_session, factories)
        journal.undo_action(db_session, entry.id, user_id=scene.actor)

        journal.restore_auto(db_session, entry.id, user_id=scene.actor)

        assert _not_duplicates(db_session) == []
        assert not is_not_duplicate(
            db_session, parameter_id=scene.parameter_id, text_norm="плиты перекрытия",
            value_id=entry.value_id,
        )
        assert _fresh(db_session, entry).restored_at is not None

    def test_the_restore_does_not_return_the_contexts(self, db_session, factories):
        scene, entry = _equated_scene(db_session, factories)
        journal.undo_action(db_session, entry.id, user_id=scene.actor)
        moved = _context_value(db_session, scene)

        journal.restore_auto(db_session, entry.id, user_id=scene.actor)

        assert _context_value(db_session, scene) == moved != entry.value_id

    def test_a_record_that_was_not_undone_has_nothing_to_restore(self, db_session, factories):
        scene, entry = _composite_scene(db_session, factories)

        assert _refused(
            lambda: journal.restore_auto(db_session, entry.id, user_id=scene.actor)
        ) == (409, journal.REFUSE_JOURNAL_NOT_UNDONE)
        assert _fresh(db_session, entry).restored_at is None

    def test_a_second_restore_is_refused(self, db_session, factories):
        scene, entry = _composite_scene(db_session, factories)
        journal.undo_action(db_session, entry.id, user_id=scene.actor)
        journal.restore_auto(db_session, entry.id, user_id=scene.actor)
        stamp = _fresh(db_session, entry).restored_at

        assert _refused(
            lambda: journal.restore_auto(db_session, entry.id, user_id=scene.actor)
        ) == (409, journal.REFUSE_JOURNAL_ALREADY_RESTORED)
        assert _fresh(db_session, entry).restored_at == stamp

    def test_a_missing_record_is_not_found(self, db_session, factories):
        assert _refused(
            lambda: journal.restore_auto(db_session, 987654, user_id=_scene_user(factories))
        ) == (404, journal.REFUSE_JOURNAL_NOT_FOUND)

    def test_a_restored_record_is_reported_as_restored(self, db_session, factories):
        scene, entry = _composite_scene(db_session, factories)
        journal.undo_action(db_session, entry.id, user_id=scene.actor)

        item = journal.restore_auto(db_session, entry.id, user_id=scene.actor)

        assert item["state"] == "undone"
        assert item["restored_at"] == _fresh(db_session, entry).restored_at.isoformat()


# ---------------------------------------------------------------------------
#  HTTP
# ---------------------------------------------------------------------------

class TestRoutes:
    def test_the_list_answers_with_items_total_and_counts(self, admin_client, db_session, factories):
        scene, entry = _composite_scene(db_session, factories)

        response = admin_client.get(BASE, params={"family_id": scene.family.id})

        assert response.status_code == 200, response.text
        body = response.json()
        assert [item["id"] for item in body["items"]] == [entry.id]
        assert body["total"] == 1
        assert body["counts"] == {"split": 0, "value_added": 0, "composite": 1, "equated": 0}

    @pytest.mark.parametrize(
        "params",
        [{"limit": 101}, {"limit": 0}, {"offset": -1}, {"kind": "other"}, {"state": "other"}],
    )
    def test_a_bad_query_is_rejected(self, admin_client, params):
        assert admin_client.get(BASE, params=params).status_code == 422

    def test_the_item_answers_and_a_missing_one_is_404_with_its_code(
        self, admin_client, db_session, factories
    ):
        scene, entry = _composite_scene(db_session, factories)

        found = admin_client.get(f"{BASE}/{entry.id}")
        missing = admin_client.get(f"{BASE}/987654")

        assert found.status_code == 200 and found.json()["id"] == entry.id
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "journal_not_found"

    def test_the_undo_changes_the_data_and_answers_with_the_undone_item(
        self, admin_client, db_session, factories
    ):
        scene, entry = _composite_scene(db_session, factories)

        response = admin_client.post(f"{BASE}/{entry.id}/undo")

        assert response.status_code == 200, response.text
        assert response.json()["state"] == "undone"
        assert response.json()["undone"]["by_email"] == admin_client.user.email
        assert _value(db_session, entry.value_id).composite_kind is None
        assert len(_vetoes(db_session)) == 1

    def test_the_refusals_keep_their_status_and_code(self, admin_client, db_session, factories):
        scene, entry = _composite_scene(db_session, factories)
        added = _record(db_session, scene, "value_added", _added("а"))
        admin_client.post(f"{BASE}/{entry.id}/undo")

        again = admin_client.post(f"{BASE}/{entry.id}/undo")
        not_applicable = admin_client.post(f"{BASE}/{added}/undo")
        not_undone = admin_client.post(f"{BASE}/{added}/restore-auto")

        assert (again.status_code, again.json()["detail"]["code"]) == (409, "journal_already_undone")
        assert (not_applicable.status_code, not_applicable.json()["detail"]["code"]) == (
            409, "journal_undo_not_applicable",
        )
        assert (not_undone.status_code, not_undone.json()["detail"]["code"]) == (
            409, "journal_not_undone",
        )

    def test_the_conflict_is_409_and_nothing_is_kept(self, admin_client, db_session, factories):
        scene, entry = _composite_scene(db_session, factories)
        get_or_create_value(
            db_session, parameter_id=scene.parameter_id, text="балки + плиты",
            origin=ValueOrigin.extension,
        )
        composite_id = entry.value_id
        db_session.commit()  # отказ откатывает транзакцию: сцена к запросу уже закоммичена

        response = admin_client.post(f"{BASE}/{entry.id}/undo")

        assert (response.status_code, response.json()["detail"]["code"]) == (
            409, "journal_undo_conflict",
        )
        assert _vetoes(db_session) == []
        assert _value(db_session, composite_id).composite_kind == "all"

    def test_the_restore_removes_the_decision(self, admin_client, db_session, factories):
        scene, entry = _equated_scene(db_session, factories)
        admin_client.post(f"{BASE}/{entry.id}/undo")

        response = admin_client.post(f"{BASE}/{entry.id}/restore-auto")
        second = admin_client.post(f"{BASE}/{entry.id}/restore-auto")

        assert response.status_code == 200, response.text
        assert response.json()["restored_at"] is not None
        assert _not_duplicates(db_session) == []
        assert (second.status_code, second.json()["detail"]["code"]) == (
            409, "journal_already_restored",
        )

    def test_the_reads_write_nothing(self, admin_client, db_session, factories):
        scene, entry = _composite_scene(db_session, factories)
        equated_scene, equated = _equated_scene(db_session, factories)
        db_session.commit()

        with _statements(db_session) as seen:
            listing = admin_client.get(BASE, params={"q": "плиты"})
            item = admin_client.get(f"{BASE}/{entry.id}")
            second = admin_client.get(f"{BASE}/{equated.id}")
            missing = admin_client.get(f"{BASE}/987654")

        assert (listing.status_code, item.status_code, second.status_code) == (200, 200, 200)
        assert missing.status_code == 404
        assert seen and _writes(seen) == []

    @pytest.mark.parametrize(
        ("method", "suffix"),
        [("get", ""), ("get", "/1"), ("post", "/1/undo"), ("post", "/1/restore-auto")],
    )
    def test_a_member_is_refused_on_every_route(self, member_client, method, suffix):
        response = getattr(member_client, method)(f"{BASE}{suffix}")

        assert response.status_code == 403
