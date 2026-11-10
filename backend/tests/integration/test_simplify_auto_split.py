"""Автоделение контекстов «к делению» и его отмена (спека упрощения каталога §2.5, §2.7).

Сцена строится настоящим путём: семья с параметром «Тип конструкции», контекст с членами под
разными разделами, ответ `conflict` через `apply_values` (он и ставит задание). Ответы модели
подставляются параметром `ask`. Эталоны — независимые литералы: заголовки, значения, виды
правил, коды отказа записаны руками, а не взяты из кода.

Устаревший ответ проверяется снимком строк домена и журнала до и после вызова, а не числом.
"""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

import crud.ai_journal as journal
import services.semantic_worker as worker
from crud.common import DomainError
from models import (
    CatalogAutoAction,
    CatalogContext,
    ContextMember,
    ContextParameterValue,
    ContextRoutingRule,
    FamilyParameterValue,
    PositionItem,
    Proposal,
    SemanticEvent,
    SemanticJob,
    SemanticJobAttempt,
    SemanticJobKind,
    WorkVariant,
)
from services.auto_split import (
    AutoSplitOutcome,
    auto_split_context,
    auto_split_fingerprint,
    current_fingerprint,
    plan_for_context,
    run_auto_split_job,
)
from services.context_operations import move_members
from services.context_routing import route_position
from services.semantic_cost import reserve_for, tariffs_from
from services.variant_answer import ValueItem, ValuesAnswer
from services.work_variants import (
    apply_values,
    bump_revision,
    freeze_schema,
    rebuild_schema,
    values_key_of,
)
from tests.integration.test_work_variants_core import _parameter_in, _schema_answer, _table_rows, _world
from tests.integration.test_work_variants_schema_life import _claim, _jobs, _paths_hash
from tests.integration.test_work_variants_worker import (
    NOW,
    S,
    _response,
)

pytestmark = pytest.mark.integration

BASE = "/api/v1/semantic/journal"

_TWO_HEADINGS = [(("Смета", "Корпус 2", "Стены"), 2), (("Смета", "Корпус 2", "Колонны"), 2)]
_TABLE = {"Стены": "стены", "Колонны": "колонны", "Балки": "колонны"}


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _conflict():
    return ValuesAnswer(items=(ValueItem(1, "conflict", None, None),))


def _apply(db, scene, answer, *, context_id=None, **extra):
    context_id = context_id or scene.ctx
    return apply_values(
        db, context_id=context_id, schema_id=scene.schema.id, answer=answer,
        paths_hash=_paths_hash(db, context_id), guard=None, settings=S, **extra,
    )


def _scene(db, factories, specs=_TWO_HEADINGS, values=("стены", "колонны", "балки")):
    """Контекст с членами под разделами `specs`, у которого ответ модели был `conflict`:
    подсказка «к делению» стоит, задание `auto_split` поставлено."""
    world = _world(
        db, factories, params=((1, "Тип конструкции", list(values)),), path_specs=specs,
    )
    world.family.status = "active"
    db.flush()
    scene = SimpleNamespace(
        world=world, family=world.family, schema=world.schema, ctx=world.context_id,
        parameter_id=world.parameters[1], actor=factories.UserFactory.create().id,
        factories=factories,
    )
    assert _apply(db, scene, _conflict()).applied
    # Сцена проверяет только очередь автоделения: прочие задания контекста не мешают захвату.
    _only_auto_split(db)
    return scene


def _only_auto_split(db):
    """Прочие открытые задания не мешают захвату: проверяется очередь автоделения."""
    db.execute(
        sa.update(SemanticJob)
        .where(SemanticJob.kind != "auto_split", SemanticJob.status == "pending")
        .values(status="cancelled", cancel_reason="not_applicable")
    )
    db.flush()


def _ask(table=_TABLE, calls=None):
    def ask(context_id, group):
        if calls is not None:
            calls.append((context_id, group.title))
        return {1: ValueItem(1, "value", table[group.title], "name")}

    return ask


def _members(db, ctx):
    """`{заголовок ближайшего раздела: [id позиций]}` членов контекста."""
    db.expire_all()
    chapter = sa.orm.aliased(PositionItem)
    rows = db.execute(
        sa.select(ContextMember.position_item_id, chapter.job_title_in_proposal)
        .join(PositionItem, PositionItem.id == ContextMember.position_item_id)
        .join(chapter, chapter.id == PositionItem.chapter_item_id)
        .where(ContextMember.context_id == ctx)
        .order_by(ContextMember.position_item_id)
    ).all()
    result: dict[str, list[int]] = {}
    for position_id, title in rows:
        result.setdefault(title, []).append(position_id)
    return result


def _member_ids(db, ctx) -> list[int]:
    return sorted(i for ids in _members(db, ctx).values() for i in ids)


def _context(db, context_id) -> CatalogContext:
    db.expire_all()
    return db.get(CatalogContext, context_id)


def _all_contexts(db, scene) -> list[int]:
    db.expire_all()
    bucket = db.get(CatalogContext, scene.ctx).bucket_id
    return list(
        db.execute(
            sa.select(CatalogContext.id)
            .where(CatalogContext.bucket_id == bucket)
            .order_by(CatalogContext.id)
        ).scalars()
    )


def _values(db, context_id):
    """`[(значение, источник)]` строк контекста по параметру."""
    db.expire_all()
    return db.execute(
        sa.select(FamilyParameterValue.value, ContextParameterValue.source)
        .select_from(ContextParameterValue)
        .outerjoin(FamilyParameterValue, FamilyParameterValue.id == ContextParameterValue.value_id)
        .where(ContextParameterValue.context_id == context_id)
    ).all()


def _rules(db, scene) -> list[ContextRoutingRule]:
    db.expire_all()
    bucket = db.get(CatalogContext, scene.ctx).bucket_id
    return list(
        db.execute(
            sa.select(ContextRoutingRule)
            .where(ContextRoutingRule.bucket_id == bucket)
            .order_by(ContextRoutingRule.ordinal)
        ).scalars()
    )


def _journal_rows(db) -> list[CatalogAutoAction]:
    db.expire_all()
    return list(db.execute(sa.select(CatalogAutoAction).order_by(CatalogAutoAction.id)).scalars())


def _events(db, event_type):
    db.expire_all()
    return list(
        db.execute(
            sa.select(SemanticEvent)
            .where(SemanticEvent.event_type == event_type)
            .order_by(SemanticEvent.id)
        ).scalars()
    )


def _state(db):
    """Всё, что автоделение пишет, одним сравнимым значением (журнал и домен)."""
    return {
        model.__tablename__: _table_rows(db, model)
        for model in (
            ContextMember, CatalogContext, ContextRoutingRule, ContextParameterValue,
            SemanticEvent, CatalogAutoAction, WorkVariant,
        )
    }


def _job(db, scene, **where):
    [job] = _jobs(db, "auto_split", context_id=scene.ctx, **where)
    return job


def _prepared(db, scene, table=_TABLE, level=None):
    """Захват задания, план и ответы модели: то, что есть у писателя к моменту записи."""
    job = _jobs(db, "auto_split", context_id=scene.ctx)[-1]
    guard = _claim(db, job)
    plan = plan_for_context(db, scene.ctx, level=level)
    ask = _ask(table)
    answers = {index: ask(scene.ctx, group) for index, group in enumerate(plan.groups)}
    return guard, plan, answers


def _write(db, scene, prepared) -> AutoSplitOutcome:
    guard, plan, answers = prepared
    return auto_split_context(
        db, context_id=scene.ctx, answers=answers, plan=plan, guard=guard, settings=S
    )


def _split(db, scene, table=_TABLE, level=None) -> AutoSplitOutcome:
    return _write(db, scene, _prepared(db, scene, table, level))


def _refused(call) -> DomainError:
    with pytest.raises(DomainError) as raised:
        call()
    return raised.value


def _split_scene(db, factories, specs=_TWO_HEADINGS, table=_TABLE):
    scene = _scene(db, factories, specs)
    outcome = _split(db, scene, table)
    assert outcome.status == "split", outcome
    [entry] = _journal_rows(db)
    scene.entry = entry
    scene.new_ctx = [c for c in _all_contexts(db, scene) if c != scene.ctx and
                     _context(db, c).archived_at is None and _member_ids(db, c)][0]
    return scene


def _new_member(db, factories, scene, heading) -> int:
    """Позиция под тем же разделом, что у члена `heading`, пришедшая после деления."""
    members = {h: i for ctx in _all_contexts(db, scene) for h, i in _members(db, ctx).items()}
    template = db.get(PositionItem, members[heading][0])
    proposal = db.get(Proposal, template.proposal_id)
    position = factories.PositionItemFactory.create(
        proposal=proposal, is_chapter=False, job_title_in_proposal="Новая позиция",
        chapter_item_id=template.chapter_item_id, catalog_position_id=template.catalog_position_id,
    )
    return route_position(db, position_item_id=position.id).context_id, position.id


# ---------------------------------------------------------------------------
#  Постановка задания в apply_values
# ---------------------------------------------------------------------------

class TestQueueing:
    def test_a_conflict_answer_queues_one_auto_split_job_with_the_current_fingerprint(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)

        job = _job(db_session, scene)

        assert (job.kind, job.status, job.family_id, job.schema_id, job.split_level) == (
            "auto_split", "pending", None, None, None,
        )
        plan = plan_for_context(db_session, scene.ctx)
        assert job.request_hash == current_fingerprint(db_session, scene.ctx, plan, settings=S)

    def test_the_second_conflict_answer_does_not_queue_a_second_job(self, db_session, factories):
        scene = _scene(db_session, factories)

        assert _apply(db_session, scene, _conflict()).applied

        assert len(_jobs(db_session, "auto_split", context_id=scene.ctx)) == 1

    def test_an_ordinary_answer_queues_nothing(self, db_session, factories):
        scene = _scene(db_session, factories)
        for job in _jobs(db_session, "auto_split"):
            db_session.delete(job)
        db_session.flush()

        assert _apply(db_session, scene, ValuesAnswer(items=(ValueItem(1, "value", "стены", "path"),))).applied

        assert _jobs(db_session, "auto_split") == []

    def test_a_conflict_with_identical_paths_has_nothing_to_split_and_queues_nothing(
        self, db_session, factories
    ):
        _scene(db_session, factories, [(("Смета", "Корпус 2", "Стены"), 3)])

        assert _jobs(db_session, "auto_split") == []

    def test_a_blocked_context_gets_no_job_and_the_standing_one_is_cancelled(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        context = _context(db_session, scene.ctx)
        context.auto_split_blocked_at = dt.datetime.now(dt.UTC)
        context.auto_split_blocked_by = scene.actor
        db_session.flush()

        assert _apply(db_session, scene, _conflict()).applied

        job = _job(db_session, scene)
        assert (job.status, job.cancel_reason) == ("cancelled", "not_applicable")

    def test_a_blocked_context_gets_no_new_job(self, db_session, factories):
        scene = _scene(db_session, factories)
        for job in _jobs(db_session, "auto_split"):
            db_session.delete(job)
        context = _context(db_session, scene.ctx)
        context.auto_split_blocked_at = dt.datetime.now(dt.UTC)
        context.auto_split_blocked_by = scene.actor
        db_session.flush()

        assert _apply(db_session, scene, _conflict()).applied

        assert _jobs(db_session, "auto_split") == []


# ---------------------------------------------------------------------------
#  Деление
# ---------------------------------------------------------------------------

class TestSplit:
    def test_two_groups_split_the_context_and_each_part_gets_its_values(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        before = _members(db_session, scene.ctx)

        outcome = _split(db_session, scene)

        assert (outcome.status, outcome.reason) == ("split", None)
        new_ctx = [c for c in _all_contexts(db_session, scene) if c != scene.ctx][-1]
        assert _members(db_session, scene.ctx) == {"Стены": before["Стены"]}
        assert _members(db_session, new_ctx) == {"Колонны": before["Колонны"]}
        assert _values(db_session, scene.ctx) == [("стены", "path")]
        assert _values(db_session, new_ctx) == [("колонны", "path")]
        assert _context(db_session, new_ctx).work_family_id == scene.family.id
        assert _context(db_session, new_ctx).work_variant_id != _context(
            db_session, scene.ctx
        ).work_variant_id

    def test_the_hint_is_cleared_on_every_part_and_the_job_is_done(self, db_session, factories):
        scene = _scene(db_session, factories)
        assert _context(db_session, scene.ctx).variant_split_hint == "path_conflict"

        _split(db_session, scene)

        for ctx in _all_contexts(db_session, scene):
            assert _context(db_session, ctx).variant_split_hint is None
        job = _job(db_session, scene)
        assert (job.status, job.claim_token, job.cancel_reason) == ("done", None, None)

    def test_the_rule_is_written_by_the_system_and_is_not_a_level_rule(self, db_session, factories):
        scene = _scene(db_session, factories)

        _split(db_session, scene)

        [rule] = _rules(db_session, scene)
        new_ctx = [c for c in _all_contexts(db_session, scene) if c != scene.ctx][-1]
        assert rule.predicate == {"kind": "nearest_chapter_equals", "value": "Колонны"}
        assert (rule.created_by, rule.context_id, rule.ordinal) == (None, new_ctx, 1)
        moved = db_session.execute(
            sa.select(ContextMember.routed_by, ContextMember.routing_rule_id).where(
                ContextMember.context_id == new_ctx
            )
        ).all()
        assert moved == [("rule", rule.id)] * 2

    def test_the_events_of_the_system_have_no_actor(self, db_session, factories):
        scene = _scene(db_session, factories)
        before = len(_events(db_session, "context_split"))

        _split(db_session, scene)

        [split] = _events(db_session, "context_split")[before:]
        assert split.actor_id is None
        assert split.payload["moved_members"] == 2
        created = _events(db_session, "context_created")
        assert created[-1].actor_id is None

    def test_one_journal_record_carries_the_groups_the_rules_and_the_level(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        before = _members(db_session, scene.ctx)

        _split(db_session, scene)

        [entry] = _journal_rows(db_session)
        [rule] = _rules(db_session, scene)
        new_ctx = [c for c in _all_contexts(db_session, scene) if c != scene.ctx][-1]
        values = {
            text: value_id
            for text, value_id in db_session.execute(
                sa.select(FamilyParameterValue.value, FamilyParameterValue.id).where(
                    FamilyParameterValue.parameter_id == scene.parameter_id
                )
            )
        }
        assert (entry.kind, entry.family_id, entry.context_id) == (
            "split", scene.family.id, scene.ctx,
        )
        assert entry.payload["level"] == 2
        assert entry.payload["rule_ids"] == [rule.id]
        assert entry.payload["groups"] == [
            {
                "context_id": scene.ctx, "title": "Стены", "value_ids": [values["стены"]],
                "members": before["Стены"],
            },
            {
                "context_id": new_ctx, "title": "Колонны", "value_ids": [values["колонны"]],
                "members": before["Колонны"],
            },
        ]
        assert (entry.undone_at, entry.undone_by) == (None, None)

    def test_the_parts_are_filled_without_asking_the_model_again(self, db_session, factories):
        scene = _scene(db_session, factories)

        _split(db_session, scene)

        new_ctx = [c for c in _all_contexts(db_session, scene) if c != scene.ctx][-1]
        assert _jobs(db_session, "context_values", context_id=new_ctx) == []

    def test_groups_with_the_same_main_values_are_merged_and_get_a_rule_per_heading(
        self, db_session, factories
    ):
        specs = [
            (("Смета", "Корпус 2", "Стены"), 2),
            (("Смета", "Корпус 2", "Колонны"), 1),
            (("Смета", "Корпус 2", "Балки"), 1),
        ]
        scene = _scene(db_session, factories, specs)
        before = {h: i for h, i in _members(db_session, scene.ctx).items()}

        outcome = _split(db_session, scene)

        assert outcome.status == "split"
        parts = [c for c in _all_contexts(db_session, scene) if c != scene.ctx]
        assert len(parts) == 1
        assert _members(db_session, parts[0]) == {
            "Колонны": before["Колонны"], "Балки": before["Балки"],
        }
        rules = _rules(db_session, scene)
        assert sorted(r.predicate["value"] for r in rules) == ["Балки", "Колонны"]
        assert {r.context_id for r in rules} == {parts[0]}
        [entry] = _journal_rows(db_session)
        assert len(entry.payload["groups"]) == 2
        assert sorted(entry.payload["rule_ids"]) == sorted(r.id for r in rules)
        assert _values(db_session, parts[0]) == [("колонны", "path")]

    def test_a_context_split_by_rules_keeps_its_parts_after_a_rerouting_of_the_estimate(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        before = _members(db_session, scene.ctx)
        _split(db_session, scene)
        new_ctx = [c for c in _all_contexts(db_session, scene) if c != scene.ctx][-1]

        ids = before["Колонны"] + before["Стены"]
        for position_id in ids:
            route_position(db_session, position_item_id=position_id)

        assert _members(db_session, scene.ctx) == {"Стены": before["Стены"]}
        assert _members(db_session, new_ctx) == {"Колонны": before["Колонны"]}


class TestResolved:
    def test_one_group_after_merging_sets_the_values_and_clears_the_hint_without_a_journal(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        before = _state(db_session)
        contexts = _all_contexts(db_session, scene)

        outcome = _split(db_session, scene, {"Стены": "стены", "Колонны": "стены"})

        assert (outcome.status, outcome.action_id) == ("resolved", None)
        assert _all_contexts(db_session, scene) == contexts
        assert _journal_rows(db_session) == []
        assert _rules(db_session, scene) == []
        assert _values(db_session, scene.ctx) == [("стены", "path")]
        assert _context(db_session, scene.ctx).variant_split_hint is None
        assert before["context_members"] == _state(db_session)["context_members"]
        assert _job(db_session, scene).status == "done"


class TestNotSplit:
    def test_a_heading_found_in_the_chain_of_another_group_leaves_the_context_as_it_is(
        self, db_session, factories
    ):
        specs = [
            (("Смета", "Корпус", "Надземная часть", "Колонны"), 2),
            (("Смета", "Корпус", "Колонны"), 2),
        ]
        scene = _scene(db_session, factories, specs)
        table = {"Надземная часть": "стены", "Колонны": "колонны"}
        prepared = _prepared(db_session, scene, table)
        before = _state(db_session)

        outcome = _write(db_session, scene, prepared)

        assert (outcome.status, outcome.action_id, outcome.reason) == ("not_split", None, "no_rule")
        assert _state(db_session) == before
        assert _context(db_session, scene.ctx).variant_split_hint == "path_conflict"
        assert _job(db_session, scene).status == "done"

    def test_chains_of_different_depth_split_by_the_chain_rule(self, db_session, factories):
        specs = [
            (("Смета", "Корпус", "Колонны"), 2),
            (("Смета", "Корпус", "Надземная часть", "Колонны"), 2),
        ]
        scene = _scene(db_session, factories, specs)
        before = _members(db_session, scene.ctx)

        outcome = _split(db_session, scene, {"Колонны": "колонны", "Надземная часть": "стены"})

        assert outcome.status == "split"
        [rule] = _rules(db_session, scene)
        assert rule.predicate == {"kind": "chapter_chain_contains", "value": "Надземная часть"}
        new_ctx = rule.context_id
        assert len(_member_ids(db_session, new_ctx)) == 2
        assert len(_member_ids(db_session, scene.ctx)) == 2
        assert sorted(_member_ids(db_session, new_ctx) + _member_ids(db_session, scene.ctx)) == (
            sorted(i for ids in before.values() for i in ids)
        )

    def test_an_earlier_rule_of_the_bucket_that_takes_the_members_leaves_the_context(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        bucket = _context(db_session, scene.ctx).bucket_id
        db_session.add(
            ContextRoutingRule(
                bucket_id=bucket, ordinal=1, context_id=scene.ctx, created_by=scene.actor,
                predicate={"kind": "chapter_chain_contains", "value": "Корпус 2"},
            )
        )
        db_session.flush()
        prepared = _prepared(db_session, scene)
        before = _state(db_session)

        outcome = _write(db_session, scene, prepared)

        assert (outcome.status, outcome.reason) == ("not_split", "no_rule")
        assert _state(db_session) == before

    def test_a_manually_placed_member_stops_the_split(self, db_session, factories):
        scene = _scene(db_session, factories)
        member_id = _members(db_session, scene.ctx)["Стены"][0]
        db_session.execute(
            sa.update(ContextMember)
            .where(ContextMember.position_item_id == member_id)
            .values(routed_by="manual")
        )
        db_session.flush()

        assert _job_is_not_applicable(db_session, scene)


def _job_is_not_applicable(db, scene) -> bool:
    """Захват отменяет задание: предмет неприменим."""
    claim = worker.claim_next(db, settings=S, now=NOW)
    job = _job(db, scene)
    return claim is None and (job.status, job.cancel_reason) == ("cancelled", "not_applicable")


# ---------------------------------------------------------------------------
#  Отпечаток: устаревший ответ не пишет ничего
# ---------------------------------------------------------------------------

def _chapter_of(db, scene, heading) -> int:
    position_id = _members(db, scene.ctx)[heading][0]
    return db.get(PositionItem, position_id).chapter_item_id


def _rename_chapter(db, scene, heading):
    """(а) Те же члены, у одного изменилась цепочка разделов."""
    db.get(PositionItem, _chapter_of(db, scene, heading)).job_title_in_proposal = "Перегородки"
    db.flush()


def _set_value_by_hand(db, scene, heading):
    """(б) Человек поставил значение параметра контекста вручную."""
    value_id = db.execute(
        sa.select(FamilyParameterValue.id).where(
            FamilyParameterValue.parameter_id == scene.parameter_id,
            FamilyParameterValue.value == "стены",
        )
    ).scalar_one()
    db.execute(
        sa.update(ContextParameterValue)
        .where(ContextParameterValue.context_id == scene.ctx)
        .values(value_id=value_id, source="manual")
    )
    db.flush()


def _freeze_new_schema(db, scene, heading):
    """(в) У семьи заморожена новая версия схемы."""
    new = rebuild_schema(db, family_id=scene.family.id, actor_id=scene.actor)
    [schema_job] = _jobs(db, "family_schema")
    outcome = freeze_schema(
        db, schema_id=new.id,
        answer=_schema_answer(_parameter_in(1, "Тип конструкции", "стены", "колонны", "балки")),
        guard=_claim(db, schema_job), settings=S,
    )
    assert outcome.applied


def _move_by_hand(db, scene, heading):
    """(г) Член перенесён вручную."""
    member_id = _members(db, scene.ctx)[heading][0]
    db.execute(
        sa.update(ContextMember)
        .where(ContextMember.position_item_id == member_id)
        .values(routed_by="manual", routing_rule_id=None)
    )
    db.flush()


def _block(db, scene, heading):
    """(д) Контексту поставлен `auto_split_blocked_at`."""
    context = _context(db, scene.ctx)
    context.auto_split_blocked_at = dt.datetime.now(dt.UTC)
    context.auto_split_blocked_by = scene.actor
    db.flush()


def _new_arrival(db, scene, heading):
    """Новый член в контексте после захвата."""
    ctx, _ = _new_member(db, scene.factories, scene, heading)
    assert ctx == scene.ctx


_CHANGES = {
    "chain_changed": _rename_chapter,
    "value_set_by_hand": _set_value_by_hand,
    "new_schema_version": _freeze_new_schema,
    "member_moved_by_hand": _move_by_hand,
    "split_blocked": _block,
    "member_arrived": _new_arrival,
}


class TestStaleAnswer:
    def test_without_a_change_the_same_call_applies(self, db_session, factories):
        scene = _scene(db_session, factories)

        assert _split(db_session, scene).status == "split"

    @pytest.mark.parametrize("change", list(_CHANGES))
    def test_a_change_between_the_model_call_and_the_write_writes_nothing(
        self, db_session, factories, change
    ):
        scene = _scene(db_session, factories)
        prepared = _prepared(db_session, scene)
        _CHANGES[change](db_session, scene, "Колонны")
        before = _state(db_session)

        outcome = _write(db_session, scene, prepared)

        assert (outcome.status, outcome.action_id) == ("input_changed", None)
        assert _state(db_session) == before
        job = _job(db_session, scene)
        assert (job.status, job.cancel_reason, job.claim_token) == (
            "cancelled", "input_changed", None,
        )

    def test_a_pending_family_of_the_context_stops_the_write(self, db_session, factories):
        from tests.integration.test_work_variants_core import _set_pending
        from tests.integration.test_work_variants_schema import _family

        scene = _scene(db_session, factories)
        prepared = _prepared(db_session, scene)
        _set_pending(
            db_session, factories, scene.ctx, _family(db_session, status="active", definition="Другая")
        )
        before = _state(db_session)

        outcome = _write(db_session, scene, prepared)

        assert (outcome.status, outcome.action_id) == ("input_changed", None)
        assert _state(db_session) == before

    def test_a_context_that_became_not_a_work_is_not_split(self, db_session, factories):
        scene = _scene(db_session, factories)
        prepared = _prepared(db_session, scene)
        _context(db_session, scene.ctx).semantic_state = "NOT_APPLICABLE"
        db_session.flush()
        before = _state(db_session)

        outcome = _write(db_session, scene, prepared)

        assert (outcome.status, outcome.reason) == ("input_changed", "not_applicable")
        assert _state(db_session) == before
        assert _job(db_session, scene).cancel_reason == "not_applicable"

    def test_an_answer_without_an_active_parameter_is_refused_before_any_write(
        self, db_session, factories
    ):
        from services.semantic_answer import AnswerSchemaError

        scene = _scene(db_session, factories)
        guard, plan, answers = _prepared(db_session, scene)
        answers[1] = {}
        before = _state(db_session)

        with pytest.raises(AnswerSchemaError) as raised:
            _write(db_session, scene, (guard, plan, answers))

        assert raised.value.code == "bad_ordinal"
        assert _state(db_session) == before

    def test_a_job_taken_over_by_another_attempt_writes_nothing_and_is_not_touched(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        prepared = _prepared(db_session, scene)
        taken = uuid.uuid4()
        _job(db_session, scene).claim_token = taken
        db_session.flush()
        before = _state(db_session)

        outcome = _write(db_session, scene, prepared)

        assert (outcome.status, outcome.action_id) == ("lost_claim", None)
        assert _state(db_session) == before
        job = _job(db_session, scene)
        assert (job.status, job.claim_token) == ("running", taken)

    def test_a_cancelled_job_writes_nothing(self, db_session, factories):
        scene = _scene(db_session, factories)
        prepared = _prepared(db_session, scene)
        job = _job(db_session, scene)
        job.status = "cancelled"
        job.cancel_reason = "not_applicable"
        job.claim_token = None
        db_session.flush()
        before = _state(db_session)

        outcome = _write(db_session, scene, prepared)

        assert outcome.status == "lost_claim"
        assert _state(db_session) == before


class TestFingerprintInputs:
    """Каждая составляющая отпечатка меняет его сама по себе (одна на вход)."""

    @staticmethod
    def _fingerprint(db, scene, hashes=("h1", "h2")):
        return auto_split_fingerprint(db, scene.ctx, group_request_hashes=list(hashes))

    def test_the_same_state_gives_the_same_fingerprint(self, db_session, factories):
        scene = _scene(db_session, factories)

        assert self._fingerprint(db_session, scene) == self._fingerprint(db_session, scene)

    def test_the_group_request_hashes_are_part_of_it_in_order(self, db_session, factories):
        scene = _scene(db_session, factories)

        base = self._fingerprint(db_session, scene)

        assert self._fingerprint(db_session, scene, ("h1", "h3")) != base
        assert self._fingerprint(db_session, scene, ("h2", "h1")) != base

    @pytest.mark.parametrize("change", list(_CHANGES))
    def test_the_state_changes_of_the_stale_cases_change_it(self, db_session, factories, change):
        scene = _scene(db_session, factories)
        base = self._fingerprint(db_session, scene)

        _CHANGES[change](db_session, scene, "Колонны")

        assert self._fingerprint(db_session, scene) != base

    def test_the_routing_rule_of_a_member_changes_it(self, db_session, factories):
        scene = _scene(db_session, factories)
        base = self._fingerprint(db_session, scene)
        bucket = _context(db_session, scene.ctx).bucket_id
        rule = ContextRoutingRule(
            bucket_id=bucket, ordinal=1, context_id=scene.ctx, created_by=scene.actor,
            predicate={"kind": "nearest_chapter_equals", "value": "Стены"},
        )
        db_session.add(rule)
        db_session.flush()
        member_id = _members(db_session, scene.ctx)["Стены"][0]
        db_session.execute(
            sa.update(ContextMember)
            .where(ContextMember.position_item_id == member_id)
            .values(routed_by="rule", routing_rule_id=rule.id)
        )
        db_session.flush()

        assert self._fingerprint(db_session, scene) != base

    def test_the_variant_of_the_context_changes_it(self, db_session, factories):
        scene = _scene(db_session, factories)
        base = self._fingerprint(db_session, scene)
        other = WorkVariant(
            family_id=scene.family.id, schema_id=scene.schema.id, values_key="1=999",
            status="active",
        )
        db_session.add(other)
        db_session.flush()
        _context(db_session, scene.ctx).work_variant_id = other.id
        db_session.flush()

        assert self._fingerprint(db_session, scene) != base

    def test_the_hint_of_the_context_changes_it(self, db_session, factories):
        scene = _scene(db_session, factories)
        base = self._fingerprint(db_session, scene)

        _context(db_session, scene.ctx).variant_split_hint = None
        db_session.flush()

        assert self._fingerprint(db_session, scene) != base

    def test_the_archiving_of_the_context_changes_it(self, db_session, factories):
        scene = _scene(db_session, factories)
        base = self._fingerprint(db_session, scene)

        _context(db_session, scene.ctx).archived_at = dt.datetime.now(dt.UTC)
        db_session.flush()

        assert self._fingerprint(db_session, scene) != base

    def test_the_revision_of_the_schema_changes_it(self, db_session, factories):
        scene = _scene(db_session, factories)
        base = self._fingerprint(db_session, scene)

        bump_revision(db_session, scene.schema.id)

        assert self._fingerprint(db_session, scene) != base

    def test_the_off_list_text_of_a_row_changes_it(self, db_session, factories):
        scene = _scene(db_session, factories)
        base = self._fingerprint(db_session, scene)
        db_session.execute(
            sa.update(ContextParameterValue)
            .where(ContextParameterValue.context_id == scene.ctx)
            .values(source="off_list", off_list_text="что-то своё")
        )
        db_session.flush()

        assert self._fingerprint(db_session, scene) != base

    def test_the_source_of_a_row_changes_it(self, db_session, factories):
        scene = _scene(db_session, factories)
        base = self._fingerprint(db_session, scene)
        db_session.execute(
            sa.update(ContextParameterValue)
            .where(ContextParameterValue.context_id == scene.ctx)
            .values(source="none")
        )
        db_session.flush()

        assert self._fingerprint(db_session, scene) != base

    def test_the_family_of_the_context_changes_it(self, db_session, factories):
        scene = _scene(db_session, factories)
        base = self._fingerprint(db_session, scene)
        from tests.integration.test_work_variants_schema import _family

        other = _family(db_session, status="active", definition="Другая семья")
        context = _context(db_session, scene.ctx)
        context.work_family_id = other.id
        db_session.flush()

        assert self._fingerprint(db_session, scene) != base


# ---------------------------------------------------------------------------
#  Ветка воркера
# ---------------------------------------------------------------------------

class TestWorkerBranch:
    def test_the_claim_renders_the_job_with_the_values_tariffs_and_the_fingerprint(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        job = _job(db_session, scene)

        claim = worker.claim_next(db_session, settings=S, now=NOW)

        assert claim is not None and claim.kind == SemanticJobKind.auto_split
        assert claim.rendered.request_hash == job.request_hash
        attempt = db_session.get(SemanticJobAttempt, claim.attempt_id)
        expected = reserve_for(
            db_session, claim.rendered, tariffs_from(S, SemanticJobKind.context_values),
            claim.rendered.body["max_tokens"],
        )
        suggestion = reserve_for(
            db_session, claim.rendered, tariffs_from(S, SemanticJobKind.family_suggestion),
            claim.rendered.body["max_tokens"],
        )
        assert attempt.reserve_usd == expected
        assert expected != suggestion
        assert tariffs_from(S, SemanticJobKind.auto_split) == tariffs_from(
            S, SemanticJobKind.context_values
        )

    def test_the_request_covers_every_group_for_the_privacy_check_and_the_reserve(
        self, db_session, factories
    ):
        _scene(db_session, factories)

        claim = worker.claim_next(db_session, settings=S, now=NOW)

        user_text = claim.rendered.body["messages"][1]["content"]
        assert "Смета / Корпус 2 / Стены" in user_text
        assert "Смета / Корпус 2 / Колонны" in user_text

    def test_the_job_of_a_blocked_context_is_cancelled_at_the_claim(self, db_session, factories):
        scene = _scene(db_session, factories)
        _block(db_session, scene, "Колонны")

        assert worker.claim_next(db_session, settings=S, now=NOW) is None

        job = _job(db_session, scene)
        assert (job.status, job.cancel_reason) == ("cancelled", "not_applicable")

    def test_the_job_whose_input_changed_is_cancelled_at_the_claim(self, db_session, factories):
        scene = _scene(db_session, factories)
        _rename_chapter(db_session, scene, "Колонны")

        assert worker.claim_next(db_session, settings=S, now=NOW) is None

        job = _job(db_session, scene)
        assert (job.status, job.cancel_reason) == ("cancelled", "input_changed")

    def test_the_run_asks_every_group_once_and_splits(self, db_session, factories):
        scene = _scene(db_session, factories)
        claim = worker.claim_next(db_session, settings=S, now=NOW)
        calls: list = []

        outcome = run_auto_split_job(
            db_session, claim=claim, ask=_ask(calls=calls), settings=S
        )

        assert outcome.status == "split"
        assert calls == [(scene.ctx, "Стены"), (scene.ctx, "Колонны")]
        assert len(_all_contexts(db_session, scene)) == 2
        assert _job(db_session, scene).status == "done"

    def test_the_finish_hook_runs_in_the_same_transaction_before_the_commit(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        claim = worker.claim_next(db_session, settings=S, now=NOW)
        seen: list = []

        run_auto_split_job(
            db_session, claim=claim, ask=_ask(), settings=S,
            finish=lambda db, outcome: seen.append((outcome.status, len(_all_contexts(db, scene)))),
        )

        assert seen == [("split", 2)]

    def test_a_split_that_the_model_answers_the_same_for_every_group_resolves(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        claim = worker.claim_next(db_session, settings=S, now=NOW)

        outcome = run_auto_split_job(
            db_session, claim=claim, ask=_ask({"Стены": "балки", "Колонны": "балки"}), settings=S
        )

        assert outcome.status == "resolved"
        assert _values(db_session, scene.ctx) == [("балки", "path")]

    def test_the_run_of_a_lost_claim_changes_nothing(self, db_session, factories):
        scene = _scene(db_session, factories)
        claim = worker.claim_next(db_session, settings=S, now=NOW)
        _job(db_session, scene).claim_token = uuid.uuid4()
        db_session.flush()
        before = _state(db_session)

        outcome = run_auto_split_job(db_session, claim=claim, ask=_ask(), settings=S)

        assert outcome.status == "lost_claim"
        assert _state(db_session) == before


class TestWorkerEndToEnd:
    def test_the_worker_calls_the_model_per_group_closes_the_attempt_and_splits(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _scene(committing_db, committing_factories)
        committing_db.commit()
        bodies: list[dict] = []

        class _Client:
            def complete(self, body, *, timeout_s):
                bodies.append(body)
                heading = "колонны" if "Колонны" in body["messages"][1]["content"] else "стены"
                content = (
                    '{"values": [{"ordinal": 1, "kind": "value", "value": "'
                    + heading
                    + '", "source": "path"}]}'
                )
                return _response(content, cost=Decimal("0.01"))

        assert worker.process_one(
            committing_session_factory, _Client(), settings=S, clock=lambda: NOW
        ) is True

        assert len(bodies) == 2
        assert len(_all_contexts(committing_db, scene)) == 2
        job = _job(committing_db, scene)
        assert (job.status, job.claim_token) == ("done", None)
        committing_db.expire_all()
        [attempt] = committing_db.execute(
            sa.select(SemanticJobAttempt).where(SemanticJobAttempt.job_id == job.id)
        ).scalars()
        assert (attempt.outcome, attempt.cost_usd) == ("ok", Decimal("0.02"))
        assert [row.kind for row in _journal_rows(committing_db)] == ["split"]

    def test_a_failed_call_retries_and_writes_nothing(
        self, committing_session_factory, committing_db, committing_factories
    ):
        from services.semantic_client import TransientModelError

        scene = _scene(committing_db, committing_factories)
        committing_db.commit()

        class _Failing:
            def complete(self, body, *, timeout_s):
                raise TransientModelError("сбой", error_class="Timeout")

        assert worker.process_one(
            committing_session_factory, _Failing(), settings=S, clock=lambda: NOW
        ) is True

        job = _job(committing_db, scene)
        assert (job.status, job.claim_token) == ("pending", None)
        assert len(_all_contexts(committing_db, scene)) == 1
        assert _journal_rows(committing_db) == []


# ---------------------------------------------------------------------------
#  Отмена
# ---------------------------------------------------------------------------

def _undo(db, scene, **kwargs):
    return journal.undo_action(db, scene.entry.id, user_id=scene.actor, **kwargs)


class TestUndo:
    def test_the_current_members_come_back_including_the_rows_that_came_by_the_rule(
        self, db_session, factories
    ):
        scene = _split_scene(db_session, factories)
        before = {**_members(db_session, scene.ctx), **_members(db_session, scene.new_ctx)}
        arrived_ctx, arrived = _new_member(db_session, factories, scene, "Колонны")
        assert arrived_ctx == scene.new_ctx

        item = _undo(db_session, scene)

        assert item["state"] == "undone"
        assert _members(db_session, scene.ctx) == {
            "Стены": before["Стены"],
            "Колонны": before["Колонны"] + [arrived],
        }
        assert _member_ids(db_session, scene.new_ctx) == []
        assert _context(db_session, scene.new_ctx).archived_at is not None

    def test_only_the_rules_of_the_record_are_removed_and_their_members_are_unpinned(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        bucket = _context(db_session, scene.ctx).bucket_id
        db_session.add(
            ContextRoutingRule(
                bucket_id=bucket, ordinal=1, context_id=scene.ctx, created_by=scene.actor,
                predicate={"kind": "nearest_chapter_equals", "value": "Фасады"},
            )
        )
        db_session.flush()
        assert _split(db_session, scene).status == "split"
        [entry] = _journal_rows(db_session)
        scene.entry = entry
        assert len(_rules(db_session, scene)) == 2

        _undo(db_session, scene)

        [kept] = _rules(db_session, scene)
        assert kept.predicate == {"kind": "nearest_chapter_equals", "value": "Фасады"}
        rows = db_session.execute(
            sa.select(ContextMember.routed_by, ContextMember.routing_rule_id).where(
                ContextMember.context_id == scene.ctx
            )
        ).all()
        assert rows == [("default", None)] * 4

    def test_the_context_goes_back_to_the_path_conflict_with_the_block_mark(
        self, db_session, factories
    ):
        scene = _split_scene(db_session, factories)

        _undo(db_session, scene)

        context = _context(db_session, scene.ctx)
        assert context.variant_split_hint == "path_conflict"
        assert context.auto_split_blocked_by == scene.actor
        assert context.auto_split_blocked_at is not None
        assert _values(db_session, scene.ctx) == [(None, "path_conflict")]
        variant = db_session.get(WorkVariant, context.work_variant_id)
        assert variant.values_key == values_key_of({1: None})

    def test_the_undone_record_carries_the_author_and_no_job_is_queued(self, db_session, factories):
        scene = _split_scene(db_session, factories)

        item = _undo(db_session, scene)

        assert item["undone"]["by_email"] is not None
        row = _journal_rows(db_session)[0]
        assert (row.undone_by, row.undone_at is not None) == (scene.actor, True)
        assert [j.status for j in _jobs(db_session, "auto_split", context_id=scene.ctx)] == ["done"]

    def test_a_standing_job_is_cancelled_by_the_undo(self, db_session, factories):
        scene = _split_scene(db_session, factories)
        standing = SemanticJob(
            kind="auto_split", context_id=scene.ctx, request_hash="standing", status="pending",
            prompt_version="1", model_requested="m", place_dictionary_version=1,
            candidates_hash="c", prefix_hash="p", input_hash="i",
            response_schema_version="1", serialization_version="1",
        )
        db_session.add(standing)
        db_session.flush()

        _undo(db_session, scene)

        db_session.refresh(standing)
        assert (standing.status, standing.cancel_reason) == ("cancelled", "not_applicable")

    def test_the_second_undo_is_refused(self, db_session, factories):
        scene = _split_scene(db_session, factories)
        _undo(db_session, scene)

        refused = _refused(lambda: _undo(db_session, scene))

        assert (refused.status_code, refused.code) == (409, "journal_already_undone")

    def test_a_manual_move_into_a_part_locks_the_undo_and_names_who_did_it(
        self, db_session, factories
    ):
        scene = _split_scene(db_session, factories)
        mover = factories.UserFactory.create()
        member = _members(db_session, scene.ctx)["Стены"][0]
        move_members(
            db_session, position_item_ids=[member], target_context_id=scene.new_ctx,
            actor_id=mover.id, reason="manual",
        )
        before = _state(db_session)

        refused = _refused(lambda: _undo(db_session, scene))

        assert (refused.status_code, refused.code) == (409, "journal_undo_locked")
        locked_by = refused.context["locked_by"]
        assert (locked_by["event_type"], locked_by["actor_email"]) == ("members_moved", mover.email)
        assert locked_by["at"] is not None
        assert _state(db_session) == before

    @pytest.mark.parametrize(
        "event_type", ["context_split", "context_merged", "context_archived",
                       "context_variant_assigned", "context_family_assigned"],
    )
    def test_each_listed_human_event_after_the_record_locks_the_undo(
        self, db_session, factories, event_type
    ):
        scene = _split_scene(db_session, factories)
        db_session.add(
            SemanticEvent(
                context_id=scene.new_ctx, event_type=event_type, payload={"x": 1},
                actor_id=scene.actor,
            )
        )
        db_session.flush()

        refused = _refused(lambda: _undo(db_session, scene))

        assert (refused.status_code, refused.code) == (409, "journal_undo_locked")
        assert refused.context["locked_by"]["event_type"] == event_type

    def test_an_event_of_the_system_does_not_lock(self, db_session, factories):
        scene = _split_scene(db_session, factories)
        db_session.add(
            SemanticEvent(
                context_id=scene.new_ctx, event_type="members_moved", payload={"x": 1},
                actor_id=None,
            )
        )
        db_session.flush()

        assert _undo(db_session, scene)["state"] == "undone"

    def test_an_event_of_a_human_of_another_kind_does_not_lock(self, db_session, factories):
        scene = _split_scene(db_session, factories)
        db_session.add(
            SemanticEvent(
                context_id=scene.new_ctx, event_type="kind_set", payload={"x": 1},
                actor_id=scene.actor,
            )
        )
        db_session.flush()

        assert _undo(db_session, scene)["state"] == "undone"

    def test_an_event_of_a_human_before_the_record_does_not_lock(self, db_session, factories):
        scene = _split_scene(db_session, factories)
        db_session.add(
            SemanticEvent(
                context_id=scene.new_ctx, event_type="members_moved", payload={"x": 1},
                actor_id=scene.actor, created_at=scene.entry.created_at - dt.timedelta(hours=1),
            )
        )
        db_session.flush()

        assert _undo(db_session, scene)["state"] == "undone"

    def test_an_archived_part_locks_the_undo(self, db_session, factories):
        scene = _split_scene(db_session, factories)
        _context(db_session, scene.new_ctx).archived_at = dt.datetime.now(dt.UTC)
        db_session.flush()

        refused = _refused(lambda: _undo(db_session, scene))

        assert (refused.status_code, refused.code) == (409, "journal_undo_locked")
        assert refused.context["locked_by"] == {
            "event_type": None, "actor_email": None, "at": None,
        }

    def test_a_manual_member_in_a_part_locks_the_undo(self, db_session, factories):
        scene = _split_scene(db_session, factories)
        member = _members(db_session, scene.new_ctx)["Колонны"][0]
        db_session.execute(
            sa.update(ContextMember)
            .where(ContextMember.position_item_id == member)
            .values(routed_by="manual", routing_rule_id=None)
        )
        db_session.flush()

        refused = _refused(lambda: _undo(db_session, scene))

        assert (refused.status_code, refused.code) == (409, "journal_undo_locked")

    def test_the_item_of_a_locked_record_says_so_and_the_list_filters_it(
        self, db_session, factories
    ):
        scene = _split_scene(db_session, factories)
        mover = factories.UserFactory.create()
        member = _members(db_session, scene.ctx)["Стены"][0]
        move_members(
            db_session, position_item_ids=[member], target_context_id=scene.new_ctx,
            actor_id=mover.id, reason="manual",
        )

        item = journal.get_journal_item(db_session, scene.entry.id)
        locked = journal.list_journal(
            db_session, kind=None, family_id=None, state="locked", q=None, limit=50, offset=0
        )
        active = journal.list_journal(
            db_session, kind=None, family_id=None, state="active", q=None, limit=50, offset=0
        )

        assert item["state"] == "locked"
        assert item["undo"]["available"] is False
        assert item["undo"]["locked_by"]["actor_email"] == mover.email
        assert [i["id"] for i in locked["items"]] == [scene.entry.id]
        assert [i["id"] for i in active["items"]] == []
        assert locked["items"][0]["state"] == "locked"

    def test_an_unlocked_record_is_active_and_undoable(self, db_session, factories):
        scene = _split_scene(db_session, factories)

        item = journal.get_journal_item(db_session, scene.entry.id)
        listed = journal.list_journal(
            db_session, kind=None, family_id=None, state="active", q=None, limit=50, offset=0
        )

        assert item["state"] == "active"
        assert item["undo"] == {"available": True, "locked_by": None}
        assert [i["id"] for i in listed["items"]] == [scene.entry.id]
        assert item["summary_values"] == ["Стены", "Колонны"]

    def test_the_item_gives_the_parts_with_the_current_numbers(self, db_session, factories):
        scene = _split_scene(db_session, factories)
        _new_member(db_session, factories, scene, "Колонны")

        item = journal.get_journal_item(db_session, scene.entry.id)

        assert item["details"]["parts"] == [
            {"context_id": scene.ctx, "title": "Стены", "members_now": 2, "members_after": 5},
            {"context_id": scene.new_ctx, "title": "Колонны", "members_now": 3,
             "members_after": 0},
        ]

    def test_the_restore_removes_the_block_mark_of_the_undo(self, db_session, factories):
        scene = _split_scene(db_session, factories)
        _undo(db_session, scene)

        item = journal.restore_auto(db_session, scene.entry.id, user_id=scene.actor)

        context = _context(db_session, scene.ctx)
        assert (context.auto_split_blocked_at, context.auto_split_blocked_by) == (None, None)
        assert item["restored_at"] is not None
        again = _refused(lambda: journal.restore_auto(db_session, scene.entry.id, user_id=scene.actor))
        assert again.code == "journal_already_restored"

    def test_the_restore_leaves_the_mark_of_a_resplit_alone(self, db_session, factories):
        scene = _split_scene(db_session, factories)
        journal.resplit_action(db_session, scene.entry.id, user_id=scene.actor, level=2)
        other = _context(db_session, scene.ctx)
        other.auto_split_blocked_at = dt.datetime.now(dt.UTC)
        other.auto_split_blocked_by = scene.actor
        db_session.flush()

        journal.restore_auto(db_session, scene.entry.id, user_id=scene.actor)

        context = _context(db_session, scene.ctx)
        assert context.auto_split_blocked_by == scene.actor


# ---------------------------------------------------------------------------
#  «Поправить»
# ---------------------------------------------------------------------------

_DEEP = [
    (("Смета", "Корпус", "Стены", "Бетон"), 1),
    (("Смета", "Корпус", "Стены", "Кирпич"), 1),
    (("Смета", "Корпус", "Колонны", "Бетон"), 1),
]
_DEEP_FIRST = {"Бетон": "стены", "Кирпич": "колонны", "Стены": "стены", "Колонны": "колонны"}


class TestResplit:
    def _scene(self, db, factories):
        scene = _split_scene(db, factories, _DEEP, _DEEP_FIRST)
        return scene

    def test_the_resplit_undoes_without_the_mark_and_queues_a_job_with_the_level(
        self, db_session, factories
    ):
        scene = self._scene(db_session, factories)

        item = journal.resplit_action(db_session, scene.entry.id, user_id=scene.actor, level=3)

        assert item["state"] == "undone"
        context = _context(db_session, scene.ctx)
        assert (context.auto_split_blocked_at, context.auto_split_blocked_by) == (None, None)
        assert context.variant_split_hint == "path_conflict"
        assert len(_member_ids(db_session, scene.ctx)) == 3
        job = _jobs(db_session, "auto_split", context_id=scene.ctx)[-1]
        assert (job.status, job.split_level) == ("pending", 3)
        assert [j.split_level for j in _jobs(db_session, "auto_split", context_id=scene.ctx)] == [
            None, 3,
        ]

    def test_the_new_job_splits_at_the_given_level_and_writes_a_new_record(
        self, db_session, factories
    ):
        scene = self._scene(db_session, factories)
        journal.resplit_action(db_session, scene.entry.id, user_id=scene.actor, level=3)
        _only_auto_split(db_session)

        claim = worker.claim_next(db_session, settings=S, now=NOW)
        outcome = run_auto_split_job(db_session, claim=claim, ask=_ask(_DEEP_FIRST), settings=S)

        assert outcome.status == "split", outcome
        rows = _journal_rows(db_session)
        assert [row.kind for row in rows] == ["split", "split"]
        assert rows[0].undone_at is not None and rows[1].undone_at is None
        assert rows[1].payload["level"] == 3
        assert [g["title"] for g in rows[1].payload["groups"]] == ["Бетон", "Кирпич"]
        assert rows[1].id == outcome.action_id

    def test_a_locked_record_is_not_resplit_and_nothing_is_queued(self, db_session, factories):
        scene = self._scene(db_session, factories)
        member = _members(db_session, scene.ctx)["Бетон"][0]
        move_members(
            db_session, position_item_ids=[member], target_context_id=scene.new_ctx,
            actor_id=scene.actor, reason="manual",
        )
        before = _state(db_session)
        jobs_before = len(_jobs(db_session, "auto_split"))

        refused = _refused(
            lambda: journal.resplit_action(db_session, scene.entry.id, user_id=scene.actor, level=3)
        )

        assert (refused.status_code, refused.code) == (409, "journal_undo_locked")
        assert _state(db_session) == before
        assert len(_jobs(db_session, "auto_split")) == jobs_before

    def test_a_level_that_gives_no_groups_is_refused_before_any_write(self, db_session, factories):
        scene = self._scene(db_session, factories)
        before = _state(db_session)

        refused = _refused(
            lambda: journal.resplit_action(db_session, scene.entry.id, user_id=scene.actor, level=9)
        )

        assert (refused.status_code, refused.code) == (409, "journal_resplit_no_groups")
        assert _state(db_session) == before

    def test_an_undone_record_is_not_resplit(self, db_session, factories):
        scene = self._scene(db_session, factories)
        _undo(db_session, scene)

        refused = _refused(
            lambda: journal.resplit_action(db_session, scene.entry.id, user_id=scene.actor, level=3)
        )

        assert (refused.status_code, refused.code) == (409, "journal_already_undone")

    def test_a_record_of_another_kind_is_not_resplit(self, db_session, factories):
        scene = _scene(db_session, factories)
        from services.ai_journal import record_action

        action_id = record_action(
            db_session, kind="value_added", family_id=scene.family.id,
            payload={"text": "а", "origin_context_id": None, "from_composite_id": None},
        )

        refused = _refused(
            lambda: journal.resplit_action(db_session, action_id, user_id=scene.actor, level=1)
        )

        assert (refused.status_code, refused.code) == (409, "journal_undo_not_applicable")

    def test_a_resplit_at_the_level_of_a_finished_job_reuses_the_job(self, db_session, factories):
        scene = self._scene(db_session, factories)

        journal.resplit_action(db_session, scene.entry.id, user_id=scene.actor, level=2)

        jobs = _jobs(db_session, "auto_split", context_id=scene.ctx)
        assert len(jobs) == 1
        assert (jobs[0].status, jobs[0].split_level) == ("pending", 2)


# ---------------------------------------------------------------------------
#  HTTP
# ---------------------------------------------------------------------------

class TestRoutes:
    def test_the_undo_route_returns_the_undone_item(self, admin_client, db_session, factories):
        scene = _split_scene(db_session, factories)
        db_session.commit()

        response = admin_client.post(f"{BASE}/{scene.entry.id}/undo")

        assert response.status_code == 200, response.text
        assert response.json()["state"] == "undone"
        assert _context(db_session, scene.ctx).auto_split_blocked_by == admin_client.user.id

    def test_the_locked_refusal_carries_the_code_and_who_locked(
        self, admin_client, db_session, factories
    ):
        scene = _split_scene(db_session, factories)
        mover = factories.UserFactory.create()
        member = _members(db_session, scene.ctx)["Стены"][0]
        move_members(
            db_session, position_item_ids=[member], target_context_id=scene.new_ctx,
            actor_id=mover.id, reason="manual",
        )
        db_session.commit()

        response = admin_client.post(f"{BASE}/{scene.entry.id}/undo")

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == "journal_undo_locked"
        assert detail["locked_by"]["event_type"] == "members_moved"
        assert detail["locked_by"]["actor_email"] == mover.email

    def test_the_resplit_route_takes_a_level(self, admin_client, db_session, factories):
        scene = _split_scene(db_session, factories, _DEEP, _DEEP_FIRST)
        db_session.commit()

        response = admin_client.post(f"{BASE}/{scene.entry.id}/resplit", json={"level": 3})

        assert response.status_code == 200, response.text
        assert response.json()["state"] == "undone"
        assert _jobs(db_session, "auto_split", context_id=scene.ctx)[-1].split_level == 3

    @pytest.mark.parametrize("body", [{"level": -1}, {}, {"level": "x"}, {"level": 1, "extra": 1}])
    def test_a_bad_resplit_body_is_422(self, admin_client, db_session, factories, body):
        scene = _split_scene(db_session, factories)
        db_session.commit()

        assert admin_client.post(f"{BASE}/{scene.entry.id}/resplit", json=body).status_code == 422

    def test_the_resplit_refusal_keeps_its_code(self, admin_client, db_session, factories):
        scene = _split_scene(db_session, factories)
        db_session.commit()

        response = admin_client.post(f"{BASE}/{scene.entry.id}/resplit", json={"level": 9})

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "journal_resplit_no_groups"

    def test_the_restore_route_clears_the_mark(self, admin_client, db_session, factories):
        scene = _split_scene(db_session, factories)
        admin_client.post(f"{BASE}/{scene.entry.id}/undo")

        response = admin_client.post(f"{BASE}/{scene.entry.id}/restore-auto")

        assert response.status_code == 200, response.text
        assert _context(db_session, scene.ctx).auto_split_blocked_at is None

    def test_the_item_and_list_reads_write_nothing(self, admin_client, db_session, factories):
        scene = _split_scene(db_session, factories)
        db_session.commit()
        seen: list[str] = []

        def _tick(_conn, _cursor, statement, *_args, **_kwargs):
            if not statement.startswith(("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")):
                seen.append(statement)

        engine = db_session.get_bind()
        sa.event.listen(engine, "before_cursor_execute", _tick)
        try:
            item = admin_client.get(f"{BASE}/{scene.entry.id}")
            listing = admin_client.get(BASE, params={"state": "locked"})
            active = admin_client.get(BASE, params={"state": "active"})
        finally:
            sa.event.remove(engine, "before_cursor_execute", _tick)

        assert (item.status_code, listing.status_code, active.status_code) == (200, 200, 200)
        assert item.json()["details"]["parts"][0]["members_now"] == 2
        assert seen and [
            s for s in seen if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        ] == []

    @pytest.mark.parametrize("suffix", ["/1/undo", "/1/resplit", "/1/restore-auto"])
    def test_a_member_is_refused_on_every_action(self, member_client, suffix):
        response = member_client.post(f"{BASE}{suffix}", json={"level": 1})

        assert response.status_code == 403

