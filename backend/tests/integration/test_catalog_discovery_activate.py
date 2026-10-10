"""Активация черновиков открытия (спека 3б §2.5; DoD 7, 10, 14) и перепроверка
снимка черновиков потребителями после `commit` обработки ответа.

Часть на транзакционной сессии строит открытие напрямую в базе; часть на
настоящих сессиях гоняет обработку ответа и активацию одной единицы. Отказ
проверяется кодом (`exc.code`) и неизменённым состоянием базы."""
# ruff: noqa: F811 — `world`, `scene` — фикстуры, импортированные из соседних наборов
from __future__ import annotations

import datetime as dt
import re
import threading
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy import event

import services.discovery_drafts as dd
import services.work_families as work_families
from crud.discovery import discovery_drafts
from models import (
    CatalogContext,
    FamilyCategory,
    FamilyCategoryProposal,
    FamilyDraft,
    SemanticEvent,
    SemanticJob,
    WorkFamily,
)
from services.context_operations import archive_context, move_members
from services.discovery_drafts import (
    ActivationOutcome,
    activate_discovery,
    discard_draft,
    merge_draft,
    restore_draft,
)
from services.discovery_result import apply_discovery
from services.family_categories import create_category, delete_category
from services.variant_answer import parse_discovery_answer
from services.work_families import (
    WorkFamilyError,
    activate_family,
    archive_family,
    assign_family,
    create_family,
    update_family,
)
from tests.factories import seed_category_id
from tests.integration.test_catalog_discovery_drafts import (
    _draft,
    _job,
    _make,
    _make_system,
    _refusal,
    _set,
    _snap,
    _sys,
    _uncategorize,
    world,  # noqa: F401 — фикстура
)
from tests.integration.test_catalog_discovery_races import (
    Scene,
    _Apply,
    _drafts_written,
    _members_of_drafts,
    _terminate,
    _waits_for_lock,
    scene,  # noqa: F401 — фикстура
)
from tests.integration.test_catalog_discovery_worker import S, _answer, _group
from tests.integration.test_semantic_queue_api import (
    _active_family,
    _make_job,
    _proposal,
    _unit_id,
)

pytestmark = pytest.mark.integration

_T = 20.0


# ---------------------------------------------------------------------------
#  Сцена на транзакционной сессии
# ---------------------------------------------------------------------------

class Act:
    """Выполненное открытие единицы M2: новые черновики D1, D2 (с категорией) и D3
    (без), группа «Не работа» из трёх контекстов, предложения категорий двум
    активным семьям без категории."""


@pytest.fixture
def act(world):
    a = Act()
    a.w, a.db, a.admin, a.unit = world, world.db, world.admin.id, world.family_unit
    a.work = seed_category_id(world.db)
    a.job = _job(world.db, a.unit)
    s1, s2, s3 = (_sys(world, name) for name in ("Имя 1", "Имя 2", "Имя 3"))
    a.s = (s1, s2, s3)
    a.d1 = _draft(world.db, a.job, 1, title="Новая семья один", category_id=a.work, members=[s1])
    a.d2 = _draft(world.db, a.job, 2, title="Новая семья два", category_id=a.work, members=[s2])
    a.d3 = _draft(world.db, a.job, 3, title="Без категории", category_id=None, members=[s3])
    a.n1, a.n2, a.n3 = (_sys(world, name) for name in ("Заметка 1", "Заметка 2", "Заметка 3"))
    a.nw = _draft(world.db, a.job, 4, grp="not_work", members=[a.n1, a.n2, a.n3])
    a.fa = _active_family(world.db, title="Семья А", unit_name="M2", actor_id=a.admin)
    a.fb = _active_family(world.db, title="Семья Б", unit_name="M2", actor_id=a.admin)
    _uncategorize(world.db, a.fa.id)
    _uncategorize(world.db, a.fb.id)
    for family in (a.fa, a.fb):
        world.db.add(
            FamilyCategoryProposal(
                job_id=a.job.id, family_id=family.id, family_category_id=a.work, status="open"
            )
        )
    world.db.flush()
    return a


def _call(a, *, drafts=(), not_work=(), pairs=(), job=None):
    return activate_discovery(
        a.db, job_id=(job or a.job).id, draft_ids=list(drafts),
        not_work_context_ids=list(not_work), family_categories=list(pairs), actor_id=a.admin,
    )


def _active(db, title) -> list[WorkFamily]:
    db.expire_all()
    return list(
        db.execute(
            sa.select(WorkFamily).where(WorkFamily.title == title, WorkFamily.status == "active")
        ).scalars()
    )


def _all_families(db) -> int:
    return db.execute(sa.select(sa.func.count(WorkFamily.id))).scalar_one()


def _events(db, event_type, **subject):
    stmt = sa.select(SemanticEvent).where(SemanticEvent.event_type == event_type)
    for key, value in subject.items():
        stmt = stmt.where(getattr(SemanticEvent, key) == value)
    return list(db.execute(stmt.order_by(SemanticEvent.id)).scalars())


def _state(db, context_id) -> str:
    db.expire_all()
    return db.get(CatalogContext, context_id).semantic_state


# ---------------------------------------------------------------------------
#  Новые семьи
# ---------------------------------------------------------------------------

class TestNewFamilies:
    def test_marked_drafts_become_active_families_with_categories(self, act):
        outcome = _call(act, drafts=[act.d2.id, act.d1.id])

        assert isinstance(outcome, ActivationOutcome)
        families = [_active(act.db, t)[0] for t in ("Новая семья один", "Новая семья два")]
        assert outcome.created_family_ids == tuple(f.id for f in families)  # по ordinal
        for family in families:
            assert (family.unit_id, family.family_category_id) == (act.unit, act.work)
            assert family.definition == "Определение" and family.status == "active"
        assert outcome.reask_unit_id == act.unit

    def test_families_are_created_in_the_order_of_the_draft_numbers(self, act):
        # Номер черновика и порядок `id` расходятся: девятый заведён раньше восьмого.
        ninth = _draft(act.db, act.job, 9, title="Девятый", category_id=act.work,
                       members=[_sys(act.w, "Имя 9")])
        eighth = _draft(act.db, act.job, 8, title="Восьмой", category_id=act.work,
                        members=[_sys(act.w, "Имя 8")])
        assert ninth.id < eighth.id, "вход теста: номера и id идут в разные стороны"

        outcome = _call(act, drafts=[ninth.id, eighth.id])

        by_number = tuple(_active(act.db, t)[0].id for t in ("Восьмой", "Девятый"))
        assert outcome.created_family_ids == by_number
        assert by_number[0] < by_number[1], "восьмой заведён первым"

    @pytest.mark.parametrize("how", ["discarded", "merged_into_draft", "merged_into_family"])
    def test_restored_draft_can_be_activated(self, act, how):
        """DoD 9: «Вернуть» обратим до активации — возвращённый черновик активируется."""
        if how == "discarded":
            discard_draft(act.db, draft_id=act.d1.id, actor_id=act.admin)
        elif how == "merged_into_draft":
            merge_draft(act.db, draft_id=act.d1.id, target_draft_id=act.d2.id, actor_id=act.admin)
        else:
            merge_draft(act.db, draft_id=act.d1.id, target_family_id=act.fa.id, actor_id=act.admin)
        _refusal("draft_not_open", lambda: _call(act, drafts=[act.d1.id]))
        restore_draft(act.db, draft_id=act.d1.id, actor_id=act.admin)

        outcome = _call(act, drafts=[act.d1.id])

        (family,) = _active(act.db, "Новая семья один")
        assert outcome.created_family_ids == (family.id,)
        assert _snap(act.db, act.d1.id)[0]["status"] == "activated"

    def test_drafts_become_activated_with_the_family_and_the_author(self, act):
        outcome = _call(act, drafts=[act.d1.id])

        (snap,) = _snap(act.db, act.d1.id)
        assert snap["status"] == "activated"
        assert snap["activated_family_id"] == outcome.created_family_ids[0]
        assert snap["decided_by"] == act.admin and snap["decided_at"] is not None

    def test_events_family_created_from_discovery_and_family_activated(self, act):
        outcome = _call(act, drafts=[act.d1.id])
        family_id = outcome.created_family_ids[0]

        (created,) = _events(act.db, "family_created", family_id=family_id)
        (activated,) = _events(act.db, "family_activated", family_id=family_id)
        assert created.payload["origin"] == "discovery"
        assert created.actor_id == act.admin and activated.actor_id == act.admin

    def test_unmarked_drafts_stay_open(self, act):
        _call(act, drafts=[act.d1.id])

        statuses = {s["id"]: s["status"] for s in _snap(act.db, act.d2.id, act.d3.id)}
        assert statuses == {act.d2.id: "open", act.d3.id: "open"}

    def test_nothing_marked_is_a_quiet_no_op(self, act):
        before = _all_families(act.db)

        outcome = _call(act)

        assert outcome == ActivationOutcome((), (), (), (), (), act.unit)
        assert _all_families(act.db) == before

    def test_unit_without_a_unit_gives_a_family_without_a_unit_and_null_reask(self, world):
        job = _job(world.db, None)
        context_id, _ = _make(world.db, world.factories, world.proposal, None, "Без единицы")
        draft = _draft(
            world.db, job, 1, title="Семья без единицы", category_id=seed_category_id(world.db),
            members=[context_id],
        )

        outcome = activate_discovery(
            world.db, job_id=job.id, draft_ids=[draft.id], not_work_context_ids=[],
            family_categories=[], actor_id=world.admin.id,
        )

        (family,) = _active(world.db, "Семья без единицы")
        assert family.unit_id is None and outcome.reask_unit_id is None


class TestNewFamilyRefusals:
    def test_duplicate_of_an_active_family_refuses_the_whole_activation(self, act):
        dup = _draft(
            act.db, act.job, 5, title="  семья пола ", category_id=act.work,
            members=[_sys(act.w, "Имя 5")],
        )
        before = _all_families(act.db)

        with pytest.raises(WorkFamilyError) as exc:
            _call(act, drafts=[act.d1.id, dup.id])

        assert exc.value.code == "duplicate_active_family"
        assert exc.value.ordinal == 5 and exc.value.draft_id == dup.id
        assert _all_families(act.db) == before, "ни одной семьи не создано"
        assert {s["status"] for s in _snap(act.db, act.d1.id, dup.id)} == {"open"}

    def test_duplicate_between_two_marked_drafts_refuses_the_second(self, act):
        twin = _draft(
            act.db, act.job, 5, title="НОВАЯ СЕМЬЯ ОДИН", category_id=act.work,
            members=[_sys(act.w, "Имя 6")],
        )
        before = _all_families(act.db)

        with pytest.raises(WorkFamilyError) as exc:
            _call(act, drafts=[act.d1.id, twin.id])

        assert exc.value.code == "duplicate_active_family" and exc.value.draft_id == twin.id
        assert _all_families(act.db) == before
        assert _active(act.db, "Новая семья один") == []

    def test_second_line_through_the_key_refuses_with_the_draft_number(self, act, monkeypatch):
        """Проверка дубля до записи обойдена (гонка двух активаций): имя ловит ключ
        `uq_work_families_active_name_unit`, и отказ тот же, без семей."""
        dup = _draft(
            act.db, act.job, 5, title="Семья пола", category_id=act.work,
            members=[_sys(act.w, "Имя ключа")],
        )
        before = _all_families(act.db)
        monkeypatch.setattr(work_families, "_duplicate_active_family_id", lambda *a, **k: None)

        with pytest.raises(WorkFamilyError) as exc:
            _call(act, drafts=[act.d1.id, dup.id])

        assert exc.value.code == "duplicate_active_family"
        assert exc.value.draft_id == dup.id and exc.value.ordinal == 5
        assert _all_families(act.db) == before, "ни одной семьи не создано"
        assert {s["status"] for s in _snap(act.db, act.d1.id, dup.id)} == {"open"}

    def test_same_name_in_another_unit_is_not_a_duplicate(self, act):
        _active_family(act.db, title="Новая семья один", unit_name="M3", actor_id=act.admin)

        outcome = _call(act, drafts=[act.d1.id])

        assert len(outcome.created_family_ids) == 1

    def test_duplicate_between_two_marked_drafts_names_the_later_number(self, act):
        # Девятый заведён раньше восьмого; отказ называет тот, что позже ПО НОМЕРУ.
        ninth = _draft(act.db, act.job, 9, title="Двойник", category_id=act.work,
                       members=[_sys(act.w, "Имя 9")])
        eighth = _draft(act.db, act.job, 8, title="ДВОЙНИК", category_id=act.work,
                        members=[_sys(act.w, "Имя 8")])
        assert ninth.id < eighth.id, "вход теста: номера и id идут в разные стороны"

        with pytest.raises(WorkFamilyError) as exc:
            _call(act, drafts=[ninth.id, eighth.id])

        assert exc.value.code == "duplicate_active_family"
        assert (exc.value.draft_id, exc.value.ordinal) == (ninth.id, 9)

    def test_job_of_another_kind_is_a_lookup_error(self, act):
        foreign = _make_job(act.db, act.s[0], status="pending", unit_id=act.unit)
        assert foreign.kind != "family_discovery"

        with pytest.raises(LookupError):
            activate_discovery(
                act.db, job_id=foreign.id, draft_ids=[], not_work_context_ids=[],
                family_categories=[], actor_id=act.admin,
            )

    def test_draft_without_a_category_is_refused_with_its_number(self, act):
        before = _all_families(act.db)

        with pytest.raises(WorkFamilyError) as exc:
            _call(act, drafts=[act.d1.id, act.d3.id])

        assert exc.value.code == "draft_without_category" and exc.value.draft_id == act.d3.id
        assert _all_families(act.db) == before
        assert _snap(act.db, act.d1.id)[0]["status"] == "open"

    def test_draft_of_another_run_is_not_open(self, act):
        other = _draft(act.db, _job(act.db, act.w.bare_unit), 1, category_id=act.work)
        _refusal("draft_not_open", lambda: _call(act, drafts=[act.d1.id, other.id]))
        assert _snap(act.db, act.d1.id)[0]["status"] == "open"

    def test_discarded_draft_is_not_open(self, act):
        act.db.execute(
            sa.update(FamilyDraft).where(FamilyDraft.id == act.d1.id).values(
                status="discarded", decided_by=act.admin, decided_at=dt.datetime.now(dt.UTC)
            )
        )
        _refusal("draft_not_open", lambda: _call(act, drafts=[act.d1.id]))

    @pytest.mark.parametrize("which", ["not_work_group", "missing"])
    def test_group_that_is_not_new_and_missing_ids_are_not_open(self, act, which):
        draft_id = act.nw.id if which == "not_work_group" else 987654
        _refusal("draft_not_open", lambda: _call(act, drafts=[draft_id]))

    def test_run_that_is_not_the_latest_is_superseded(self, act):
        _job(act.db, act.unit)  # новое выполненное открытие той же единицы
        before = _all_families(act.db)

        _refusal("discovery_run_superseded", lambda: _call(act, drafts=[act.d1.id]))

        assert _all_families(act.db) == before

    def test_unknown_job_is_a_lookup_error(self, act):
        with pytest.raises(LookupError):
            activate_discovery(
                act.db, job_id=987654, draft_ids=[], not_work_context_ids=[],
                family_categories=[], actor_id=act.admin,
            )


# ---------------------------------------------------------------------------
#  Категории активным семьям
# ---------------------------------------------------------------------------

class TestCategories:
    def test_pair_sets_the_category_and_the_proposal_becomes_applied(self, act):
        outcome = _call(act, pairs=[(act.fa.id, act.work)])

        act.db.expire_all()
        assert act.db.get(WorkFamily, act.fa.id).family_category_id == act.work
        assert outcome.categories_applied == (act.fa.id,) and outcome.categories_skipped == ()
        proposal = act.db.get(FamilyCategoryProposal, (act.job.id, act.fa.id))
        assert (proposal.status, proposal.decided_by) == ("applied", act.admin)
        assert proposal.decided_at is not None
        other = act.db.get(FamilyCategoryProposal, (act.job.id, act.fb.id))
        assert (other.status, other.decided_by) == ("open", None)
        (updated,) = _events(act.db, "family_updated", family_id=act.fa.id)
        assert updated.actor_id == act.admin

    def test_human_may_choose_another_category_than_the_proposed_one(self, act):
        other = create_category(act.db, title="Другая", definition="Д", actor_id=act.admin)

        _call(act, pairs=[(act.fa.id, other.id)])

        act.db.expire_all()
        assert act.db.get(WorkFamily, act.fa.id).family_category_id == other.id

    def test_family_that_got_a_category_after_the_answer_is_skipped_and_named(self, act):
        other = create_category(act.db, title="Чужая", definition="Д", actor_id=act.admin)
        update_family(
            act.db, family_id=act.fa.id, family_category_id=other.id, actor_id=act.admin
        )

        outcome = _call(act, pairs=[(act.fa.id, act.work), (act.fb.id, act.work)])

        act.db.expire_all()
        assert act.db.get(WorkFamily, act.fa.id).family_category_id == other.id
        assert act.db.get(WorkFamily, act.fb.id).family_category_id == act.work
        assert outcome.categories_skipped == (act.fa.id,)
        assert outcome.categories_applied == (act.fb.id,)

    def test_family_archived_after_the_answer_is_skipped_and_named(self, act):
        archive_family(act.db, family_id=act.fa.id, actor_id=act.admin)

        outcome = _call(act, pairs=[(act.fa.id, act.work)])

        act.db.expire_all()
        assert act.db.get(WorkFamily, act.fa.id).family_category_id is None
        assert outcome.categories_skipped == (act.fa.id,) and outcome.categories_applied == ()
        assert act.db.get(FamilyCategoryProposal, (act.job.id, act.fa.id)).status == "open"

    def test_pair_not_from_the_proposals_of_the_run_is_refused(self, act):
        foreign = _active_family(act.db, title="Семья без предложения", unit_name="M2",
                                 actor_id=act.admin)
        _uncategorize(act.db, foreign.id)

        _refusal(
            "category_not_proposed",
            lambda: _call(act, pairs=[(act.fa.id, act.work), (foreign.id, act.work)]),
        )

        act.db.expire_all()
        assert act.db.get(WorkFamily, act.fa.id).family_category_id is None
        assert act.db.get(WorkFamily, foreign.id).family_category_id is None

    def test_proposal_of_another_run_does_not_count(self, act):
        other_job = _job(act.db, act.w.bare_unit)
        foreign = _active_family(act.db, title="Чужая единица", unit_name="M3", actor_id=act.admin)
        _uncategorize(act.db, foreign.id)
        act.db.add(
            FamilyCategoryProposal(
                job_id=other_job.id, family_id=foreign.id, family_category_id=act.work,
                status="open",
            )
        )
        act.db.flush()

        _refusal("category_not_proposed", lambda: _call(act, pairs=[(foreign.id, act.work)]))

        act.db.expire_all()
        assert act.db.get(WorkFamily, foreign.id).family_category_id is None

    def test_pair_of_an_already_applied_proposal_is_not_proposed_any_more(self, act):
        """Предложение, уже применённое активацией, из этого открытия больше не
        предлагается: смена категории — на карточке семьи, а не повторной парой."""
        other = create_category(act.db, title="Другая", definition="Д", actor_id=act.admin)
        _call(act, pairs=[(act.fa.id, act.work)])

        _refusal("category_not_proposed", lambda: _call(act, pairs=[(act.fa.id, other.id)]))

        act.db.expire_all()
        assert act.db.get(WorkFamily, act.fa.id).family_category_id == act.work

    def test_deleted_category_in_a_pair_of_a_skipped_family_is_still_refused(self, act):
        """Пара проверяется до записи: удалённая категория — отказ и тогда, когда
        семья пары сама была бы пропущена (категория у неё уже есть)."""
        update_family(act.db, family_id=act.fa.id, family_category_id=act.work, actor_id=act.admin)
        gone = create_category(act.db, title="Исчезнет", definition="Д", actor_id=act.admin)
        delete_category(act.db, category_id=gone.id, actor_id=act.admin)

        _refusal(
            "category_not_found",
            lambda: _call(act, drafts=[act.d1.id], pairs=[(act.fa.id, gone.id)]),
        )

        assert _snap(act.db, act.d1.id)[0]["status"] == "open"

    def test_deleted_category_in_a_pair_is_category_not_found(self, act):
        gone = create_category(act.db, title="Исчезнет", definition="Д", actor_id=act.admin)
        delete_category(act.db, category_id=gone.id, actor_id=act.admin)

        _refusal("category_not_found", lambda: _call(act, pairs=[(act.fa.id, gone.id)]))

        act.db.expire_all()
        assert act.db.get(WorkFamily, act.fa.id).family_category_id is None


# ---------------------------------------------------------------------------
#  «Не работа»
# ---------------------------------------------------------------------------

class TestNotWork:
    def test_members_go_off_work_with_the_manual_event(self, act):
        outcome = _call(act, not_work=[act.n1, act.n2])

        assert set(outcome.not_work_applied) == {act.n1, act.n2}
        assert outcome.not_work_skipped == ()
        assert [_state(act.db, c) for c in (act.n1, act.n2, act.n3)] == [
            "NOT_APPLICABLE", "NOT_APPLICABLE", "SUGGESTED",
        ]
        (not_work,) = _events(act.db, "context_not_work", context_id=act.n1)
        assert not_work.payload["reason"] == "manual" and not_work.actor_id == act.admin

    def test_jobs_of_the_contexts_are_cancelled(self, act):
        job = _make_job(act.db, act.n1)
        assert job.status == "pending"

        _call(act, not_work=[act.n1])

        act.db.expire_all()
        refreshed = act.db.get(SemanticJob, job.id)
        assert (refreshed.status, refreshed.cancel_reason) == ("cancelled", "not_applicable")

    def test_one_name_in_two_articles_takes_both_contexts(self, act):
        first, second = (
            act.db.execute(sa.text("SELECT id FROM work_categories ORDER BY id LIMIT 2"))
            .scalars()
            .all()
        )
        ctx_1, cp = _make(
            act.db, act.w.factories, act.w.proposal, act.unit, "Одно имя", article_id=first
        )
        ctx_2, _ = _make(
            act.db, act.w.factories, act.w.proposal, act.unit, "Одно имя", cp=cp, article_id=second
        )
        for context_id in (ctx_1, ctx_2):
            _make_system(act.db, context_id, act.admin)
        act.db.execute(
            sa.text(
                "INSERT INTO family_draft_members (draft_id, job_id, context_id, name_index) "
                "VALUES (:d, :j, :c, 7)"
            ),
            [{"d": act.nw.id, "j": act.job.id, "c": c} for c in (ctx_1, ctx_2)],
        )

        outcome = _call(act, not_work=[ctx_1, ctx_2])

        assert set(outcome.not_work_applied) == {ctx_1, ctx_2}
        assert {_state(act.db, ctx_1), _state(act.db, ctx_2)} == {"NOT_APPLICABLE"}

    def test_context_of_another_group_is_refused_and_nothing_is_applied(self, act):
        _refusal(
            "context_not_in_group", lambda: _call(act, not_work=[act.n1, act.s[0]])
        )
        assert _state(act.db, act.n1) != "NOT_APPLICABLE"

    def test_context_that_is_not_a_member_is_refused(self, act):
        stranger = _sys(act.w, "Посторонний")
        _refusal("context_not_in_group", lambda: _call(act, not_work=[stranger]))

    def test_run_without_a_not_work_group_refuses_any_context(self, act):
        act.db.execute(sa.delete(FamilyDraft).where(FamilyDraft.id == act.nw.id))
        _refusal("context_not_in_group", lambda: _call(act, not_work=[act.n1]))

    def test_member_that_got_a_family_is_skipped_and_named(self, act):
        assign_family(
            act.db, context_id=act.n1, family_id=act.w.family.id, actor_id=act.admin
        )

        outcome = _call(act, not_work=[act.n1, act.n2])

        assert outcome.not_work_skipped == (act.n1,) and outcome.not_work_applied == (act.n2,)
        assert _state(act.db, act.n1) != "NOT_APPLICABLE"
        assert act.db.get(CatalogContext, act.n1).work_family_id == act.w.family.id

    def test_archived_member_is_skipped_and_named(self, act):
        _set(act.db, act.n1, archived_at=dt.datetime.now(dt.UTC))

        outcome = _call(act, not_work=[act.n1, act.n2])

        assert outcome.not_work_skipped == (act.n1,) and outcome.not_work_applied == (act.n2,)

    def test_member_already_off_work_is_skipped_and_named(self, act):
        _set(act.db, act.n1, semantic_state="NOT_APPLICABLE")

        outcome = _call(act, not_work=[act.n1])

        assert outcome.not_work_skipped == (act.n1,) and outcome.not_work_applied == ()
        assert _events(act.db, "context_not_work", context_id=act.n1) == []

    def test_activation_of_new_families_does_not_skip_the_members(self, act):
        """Активные семьи единицы, заведённые этой же активацией, не выводят
        контексты из охвата «не работы»: у единицы без семей это было бы так."""
        unit = act.w.bare_unit
        job = _job(act.db, unit)
        note, _ = _make(act.db, act.w.factories, act.w.proposal, unit, "Голая заметка")
        name, _ = _make(act.db, act.w.factories, act.w.proposal, unit, "Голое имя")
        draft = _draft(
            act.db, job, 1, title="Семья голой единицы", category_id=act.work, members=[name]
        )
        _draft(act.db, job, 2, grp="not_work", members=[note])

        outcome = activate_discovery(
            act.db, job_id=job.id, draft_ids=[draft.id], not_work_context_ids=[note],
            family_categories=[], actor_id=act.admin,
        )

        assert outcome.not_work_applied == (note,) and outcome.not_work_skipped == ()
        assert _state(act.db, note) == "NOT_APPLICABLE"


# ---------------------------------------------------------------------------
#  Целиком: один вызов, транзакционность, порядок блокировок
# ---------------------------------------------------------------------------

class TestWholeActivation:
    def test_all_three_kinds_in_one_call(self, act):
        outcome = _call(
            act, drafts=[act.d1.id], not_work=[act.n1], pairs=[(act.fa.id, act.work)]
        )

        assert len(outcome.created_family_ids) == 1
        assert outcome.categories_applied == (act.fa.id,)
        assert outcome.not_work_applied == (act.n1,)
        assert outcome.reask_unit_id == act.unit

    def test_failure_at_the_last_step_leaves_the_database_as_before_the_call(
        self, act, monkeypatch
    ):
        act.db.commit()
        families = _all_families(act.db)
        drafts = _snap(act.db, act.d1.id, act.nw.id)
        states = [_state(act.db, c) for c in (act.n1, act.n2)]
        events = act.db.execute(sa.select(sa.func.count(SemanticEvent.id))).scalar_one()
        proposal = act.db.get(FamilyCategoryProposal, (act.job.id, act.fa.id)).status

        def boom(*args, **kwargs):
            raise RuntimeError("сбой на последнем шаге")

        monkeypatch.setattr(dd, "reconcile_or_defer", boom)
        with pytest.raises(RuntimeError):
            _call(act, drafts=[act.d1.id], not_work=[act.n1], pairs=[(act.fa.id, act.work)])

        act.db.expire_all()
        assert _all_families(act.db) == families
        assert _snap(act.db, act.d1.id, act.nw.id) == drafts
        assert [_state(act.db, c) for c in (act.n1, act.n2)] == states
        assert act.db.execute(sa.select(sa.func.count(SemanticEvent.id))).scalar_one() == events
        assert act.db.get(FamilyCategoryProposal, (act.job.id, act.fa.id)).status == proposal
        assert act.db.get(WorkFamily, act.fa.id).family_category_id is None


_LOCK_RE = re.compile(r"\bFOR (KEY SHARE|SHARE|UPDATE)\b")
_FROM_RE = re.compile(r"\bFROM (\w+)")


class TestLockOrder:
    def _statements(self, a):
        statements: list[str] = []

        def _listener(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        connection = a.db.connection()
        event.listen(connection, "before_cursor_execute", _listener)
        try:
            _call(a, drafts=[a.d1.id], not_work=[a.n1], pairs=[(a.fa.id, a.work)])
        finally:
            event.remove(connection, "before_cursor_execute", _listener)
        return statements

    def test_first_lock_of_each_table_follows_the_order_of_the_specification(self, act):
        statements = self._statements(act)

        seen: list[str] = []
        for statement in statements:
            lock, table = _LOCK_RE.search(statement), _FROM_RE.search(statement)
            if lock and table and table.group(1) not in seen:
                seen.append(table.group(1))
        assert seen == [
            "family_categories", "family_drafts", "family_category_proposals",
            "work_families", "catalog_contexts",
        ]

    def test_categories_are_locked_before_anything_is_written_and_never_upgraded(self, act):
        statements = self._statements(act)

        first_category = next(
            i for i, s in enumerate(statements)
            if "family_categories" in s and "FOR SHARE" in s
        )
        first_write = next(
            i for i, s in enumerate(statements)
            if re.match(r"\s*(INSERT|UPDATE|DELETE)\b", s)
        )
        assert first_category < first_write
        assert not any("family_categories" in s and "FOR UPDATE" in s for s in statements)

    def test_drafts_and_proposals_are_locked_before_any_family_row(self, act):
        statements = self._statements(act)

        def first(table, mode):
            return next(
                i for i, s in enumerate(statements)
                if f"FROM {table}" in s and f"FOR {mode}" in s
            )

        assert first("family_drafts", "UPDATE") < first("work_families", "UPDATE")
        assert first("family_category_proposals", "UPDATE") < first("work_families", "UPDATE")
        assert first("work_families", "UPDATE") < first("catalog_contexts", "UPDATE")

    def test_category_families_are_locked_before_the_not_work_contexts(self, act):
        """Без новых семей первую семью берёт шаг 3 (`FOR UPDATE`), первый контекст —
        шаг 4: семья раньше контекста (решение 20)."""
        statements: list[str] = []

        def _listener(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        connection = act.db.connection()
        event.listen(connection, "before_cursor_execute", _listener)
        try:
            _call(act, not_work=[act.n1], pairs=[(act.fa.id, act.work)])
        finally:
            event.remove(connection, "before_cursor_execute", _listener)

        def first(table, mode):
            return next(
                i for i, s in enumerate(statements)
                if f"FROM {table}" in s and f"FOR {mode}" in s
            )

        assert first("work_families", "UPDATE") < first("catalog_contexts", "UPDATE")
        # Семьи пар берутся сразу `FOR UPDATE`, без `FOR SHARE` раньше: повышения нет.
        assert not any(
            "FROM work_families" in s and "FOR SHARE" in s
            for s in statements[: first("work_families", "UPDATE")]
        )


# ---------------------------------------------------------------------------
#  Снимок после commit обработки: потребители перепроверяют (DoD 7)
# ---------------------------------------------------------------------------

def _answer_with(scene, *, groups, not_work=(), categories=()):
    return parse_discovery_answer(
        _answer(groups, not_work=not_work, family_categories=categories), scene.claim.sent
    )


def _process(scene, answer=None):
    """Обработка ответа сцены и `commit`: дальше меняется всё, как между обработкой
    и чтением или активацией черновиков."""
    with scene.factory() as db:
        outcome = apply_discovery(
            db, job_id=scene.job_id, claim_token=scene.claim.claim_token,
            answer=answer or scene.answer, settings=S,
        )
        db.commit()
    assert outcome.applied, "вход теста: обработка должна записать черновики"


def _new_draft_id(scene) -> int:
    with scene.factory() as db:
        return db.execute(
            sa.select(FamilyDraft.id).where(
                FamilyDraft.job_id == scene.job_id, FamilyDraft.grp == "new"
            )
        ).scalar_one()


def _activate(scene, **kwargs):
    with scene.factory() as db:
        try:
            outcome = activate_discovery(
                db, job_id=scene.job_id, draft_ids=kwargs.get("drafts", []),
                not_work_context_ids=kwargs.get("not_work", []),
                family_categories=kwargs.get("pairs", []), actor_id=scene.admin.id,
            )
            db.commit()
        except WorkFamilyError:
            db.rollback()
            raise
    return outcome


def _view(scene):
    with scene.factory() as db:
        return discovery_drafts(db, unit_id=scene.unit)


def _count_families(scene) -> int:
    with scene.factory() as db:
        return db.execute(sa.select(sa.func.count(WorkFamily.id))).scalar_one()


class TestSnapshotConsumers:
    def test_member_that_got_a_family_is_not_counted_by_the_screen(self, scene):
        _process(scene)
        draft_id = _new_draft_id(scene)
        assert _view(scene)["drafts"][0]["rows"] == 1
        with scene.factory() as db:
            assign_family(db, context_id=scene.c1, family_id=scene.f1.id, actor_id=scene.admin.id)
            db.commit()

        view = _view(scene)

        assert [(r["id"], r["rows"]) for r in view["drafts"]] == [(draft_id, 0)]

    def test_member_archived_by_the_regular_operations_is_not_counted_by_the_screen(self, scene):
        _process(scene)
        assert [g["rows"] for g in _view(scene)["existing"]] == [1]
        with scene.factory() as db:
            move_members(
                db, position_item_ids=[scene.p_keep], target_context_id=scene.target_context,
                actor_id=scene.admin.id, reason="manual",
            )
            is_default = db.execute(
                sa.select(CatalogContext.is_default).where(CatalogContext.id == scene.c2)
            ).scalar_one()
            archive_context(
                db, context_id=scene.c2,
                new_default_context_id=scene.target_context if is_default else None,
                actor_id=scene.admin.id,
            )
            db.commit()

        view = _view(scene)

        assert [g["rows"] for g in view["existing"]] == [0]
        assert scene.target_context not in _members_of_drafts(scene)

    def test_not_work_member_that_got_a_family_is_skipped_and_named(self, scene):
        _process(scene)
        with scene.factory() as db:
            assign_family(db, context_id=scene.c3, family_id=scene.f1.id, actor_id=scene.admin.id)
            db.commit()

        outcome = _activate(scene, not_work=[scene.c3])

        assert outcome.not_work_skipped == (scene.c3,) and outcome.not_work_applied == ()
        with scene.factory() as db:
            context = db.get(CatalogContext, scene.c3)
            assert (context.work_family_id, context.semantic_state) == (scene.f1.id, "SUGGESTED")

    def test_not_work_member_archived_by_the_regular_operations_is_skipped_and_named(self, scene):
        _process(
            scene,
            _answer_with(
                scene,
                groups=[_group([1], category_id=scene.work, similar=scene.f1.id)],
                not_work=[2, 3], categories=[(scene.f3.id, scene.work)],
            ),
        )
        with scene.factory() as db:
            move_members(
                db, position_item_ids=[scene.p_keep], target_context_id=scene.target_context,
                actor_id=scene.admin.id, reason="manual",
            )
            is_default = db.execute(
                sa.select(CatalogContext.is_default).where(CatalogContext.id == scene.c2)
            ).scalar_one()
            archive_context(
                db, context_id=scene.c2,
                new_default_context_id=scene.target_context if is_default else None,
                actor_id=scene.admin.id,
            )
            db.commit()

        outcome = _activate(scene, not_work=[scene.c2, scene.c3])

        assert outcome.not_work_skipped == (scene.c2,) and outcome.not_work_applied == (scene.c3,)
        with scene.factory() as db:
            assert db.get(CatalogContext, scene.c3).semantic_state == "NOT_APPLICABLE"
            assert db.get(CatalogContext, scene.c2).semantic_state != "NOT_APPLICABLE"

    def test_new_family_with_the_draft_name_makes_the_activation_refuse(self, scene):
        _process(scene)
        draft_id = _new_draft_id(scene)
        with scene.factory() as db:
            family = create_family(
                db, title="Новая семья", unit_name="M3", definition="Определение",
                actor_id=scene.admin.id, family_category_id=scene.work,
            )
            activate_family(db, family_id=family.id, actor_id=scene.admin.id)
            db.commit()
        families = _count_families(scene)

        with pytest.raises(WorkFamilyError) as exc:
            _activate(scene, drafts=[draft_id], not_work=[scene.c3])

        assert exc.value.code == "duplicate_active_family"
        assert _count_families(scene) == families
        with scene.factory() as db:
            assert db.get(FamilyDraft, draft_id).status == "open"
            assert db.get(CatalogContext, scene.c3).semantic_state != "NOT_APPLICABLE"

    def test_archived_similar_family_makes_the_merge_refuse(self, scene):
        _process(scene)
        draft_id = _new_draft_id(scene)
        with scene.factory() as db:
            archive_family(db, family_id=scene.f1.id, actor_id=scene.admin.id)
            db.commit()

        with scene.factory() as db:
            with pytest.raises(WorkFamilyError) as exc:
                merge_draft(
                    db, draft_id=draft_id, target_family_id=scene.f1.id, actor_id=scene.admin.id
                )
            db.rollback()
        assert exc.value.code == "family_not_active"
        with scene.factory() as db:
            assert db.get(FamilyDraft, draft_id).status == "open"

    def test_family_of_a_category_proposal_that_got_a_category_is_skipped(self, scene):
        _process(scene)
        with scene.factory() as db:
            other = create_category(db, title="Чужая", definition="Д", actor_id=scene.admin.id)
            update_family(
                db, family_id=scene.f3.id, family_category_id=other.id, actor_id=scene.admin.id
            )
            db.commit()
            other_id = other.id

        outcome = _activate(scene, pairs=[(scene.f3.id, scene.work)])

        assert outcome.categories_skipped == (scene.f3.id,) and outcome.categories_applied == ()
        with scene.factory() as db:
            assert db.get(WorkFamily, scene.f3.id).family_category_id == other_id

    def test_archived_family_of_a_category_proposal_is_skipped(self, scene):
        _process(scene)
        with scene.factory() as db:
            archive_family(db, family_id=scene.f3.id, actor_id=scene.admin.id)
            db.commit()

        outcome = _activate(scene, pairs=[(scene.f3.id, scene.work)])

        assert outcome.categories_skipped == (scene.f3.id,) and outcome.categories_applied == ()

    def test_new_context_of_the_scope_is_neither_in_the_drafts_nor_touched(self, scene):
        _process(scene)
        draft_id = _new_draft_id(scene)
        with scene.factory() as db:
            scene.cf._register_session(db)
            try:
                fresh, _ = _make(
                    db, scene.cf, _proposal(scene.cf), scene.unit, "Имя Д — импорт"
                )
                _make_system(db, fresh, scene.admin.id)
            finally:
                scene.cf._register_session(scene.cdb)
            db.commit()

        view = _view(scene)
        assert view["rest"] == 1 and fresh not in _members_of_drafts(scene)
        _refusal("context_not_in_group", lambda: _activate(scene, not_work=[fresh]))
        _activate(scene, drafts=[draft_id])
        with scene.factory() as db:
            context = db.get(CatalogContext, fresh)
            assert (context.semantic_state, context.work_family_id) == ("SUGGESTED", None)


# ---------------------------------------------------------------------------
#  Гонки активации
# ---------------------------------------------------------------------------

def _old_run(scene, *, category_id, with_proposal=False):
    """Выполненное открытие единицы с двумя открытыми новыми черновиками: первый
    будет активирован, второй останется неотмеченным."""
    with scene.factory() as db:
        # Прежнее открытие старше текущего: его `id` меньше (в живой базе следующее
        # открытие всегда получает больший `id`, а «последнее» выбирается по нему).
        old = SemanticJob(
            id=-(1_000_000 + getattr(scene, "job_id", 0)),
            kind="family_discovery", status="done", unit_id=scene.unit,
            request_hash=f"old-{uuid.uuid4().hex}", prompt_version="discovery:1",
            model_requested="m", place_dictionary_version=1, candidates_hash="c",
            prefix_hash="p", input_hash="i", response_schema_version="v",
            serialization_version="1",
        )
        db.add(old)
        db.flush()
        ids = []
        for ordinal, title in ((1, "Старый черновик"), (2, "Неотмеченный")):
            draft = FamilyDraft(
                job_id=old.id, unit_id=scene.unit, ordinal=ordinal, grp="new", title=title,
                definition="Определение", family_category_id=category_id, status="open",
            )
            db.add(draft)
            db.flush()
            ids.append(draft.id)
        if with_proposal:
            db.add(
                FamilyCategoryProposal(
                    job_id=old.id, family_id=scene.f3.id, family_category_id=category_id,
                    status="open",
                )
            )
        job_id = old.id
        db.commit()
    return job_id, ids


class _Activation:
    """`activate_discovery` в своей сессии; `hook` — перехват после чтения
    черновиков (`FROM family_drafts` — до или после снятия блокировки)."""

    def __init__(
        self, scene, *, job_id, draft_ids, pause=False, pairs=(), pause_on="FROM family_drafts"
    ):
        self.scene, self.job_id, self.draft_ids = scene, job_id, draft_ids
        self.pause_on = pause_on
        self.pairs = list(pairs)
        self.pause = pause
        self.paused = threading.Event()
        self.go = threading.Event()
        self.started = threading.Event()
        self.pid = None
        self.error: str | None = None
        self.code: str | None = None
        self.outcome = None
        self.thread = threading.Thread(target=self._run, name="activation", daemon=True)

    def _run(self):
        try:
            with self.scene.factory() as db:
                self.pid = db.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                self.started.set()
                connection = db.connection()

                def _hook(conn, cursor, statement, parameters, context, executemany):
                    if self.pause and not self.paused.is_set() and self.pause_on in statement:
                        self.paused.set()
                        if not self.go.wait(_T):
                            raise TimeoutError("go не пришёл")

                event.listen(connection, "after_cursor_execute", _hook)
                try:
                    self.outcome = activate_discovery(
                        db, job_id=self.job_id, draft_ids=self.draft_ids,
                        not_work_context_ids=[], family_categories=self.pairs,
                        actor_id=self.scene.admin.id,
                    )
                    db.commit()
                except WorkFamilyError as exc:
                    db.rollback()
                    self.code = exc.code
                finally:
                    event.remove(connection, "after_cursor_execute", _hook)
        except Exception as exc:  # noqa: BLE001 — исход фиксируется
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.started.set()
            self.paused.set()

    def start(self):
        self.thread.start()
        assert self.started.wait(_T), "поток активации не стартовал"

    def finish(self):
        self.go.set()
        self.thread.join(timeout=_T)
        if self.thread.is_alive():
            _terminate(self.scene.factory, self.pid)
            self.thread.join(timeout=5)
        assert not self.thread.is_alive(), "поток активации завис"


class TestActivationRaces:
    def test_activation_vs_category_delete_finishes_without_deadlock(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """Активация держит категории `FOR SHARE` (шаг 0), затем черновик; удаление
        категории берёт её `FOR UPDATE` и ждёт черновики с ней. Без шага 0 активация,
        державшая черновик, ждала бы категорию у `create_family`, а удаление, державшее
        категорию, — черновик: цикл. Допустимый исход: активация успела раньше, и
        удаление отказывает `category_in_use` (семья уже носит категорию)."""
        cdb, cf = committing_db, committing_factories
        admin = cf.UserFactory.create()
        unit = _unit_id(cdb, "M3")
        extra = create_category(cdb, title="Временная", definition="Для гонки", actor_id=admin.id)
        cdb.commit()
        scene = Scene()
        scene.factory, scene.unit, scene.admin = committing_session_factory, unit, admin
        job_id, (selected, _other) = _old_run(scene, category_id=extra.id)
        activation = _Activation(scene, job_id=job_id, draft_ids=[selected], pause=True)
        deleter: dict[str, object] = {"pid": None, "error": None, "code": None}

        def delete():
            try:
                with scene.factory() as db:
                    deleter["pid"] = db.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                    delete_category(db, category_id=extra.id, actor_id=admin.id)
                    db.commit()
            except WorkFamilyError as exc:
                deleter["code"] = exc.code
            except Exception as exc:  # noqa: BLE001
                deleter["error"] = f"{type(exc).__name__}: {exc}"

        td = threading.Thread(target=delete, name="deleter", daemon=True)
        activation.start()
        try:
            assert activation.paused.wait(_T), "активация не дошла до чтения черновиков"
            td.start()
            for _ in range(int(_T / 0.02)):
                if deleter["pid"] is not None or not td.is_alive():
                    break
                threading.Event().wait(0.02)
            assert deleter["pid"] is not None, f"удаление не стартовало: {deleter['error']}"
            blocked = _waits_for_lock(scene.factory, deleter["pid"], td)
            activation.go.set()
        finally:
            activation.finish()
            td.join(timeout=_T)
            if td.is_alive():
                _terminate(scene.factory, deleter["pid"])
                td.join(timeout=5)

        assert not td.is_alive(), "поток удаления завис"
        assert activation.error is None, activation.error
        assert deleter["error"] is None, deleter["error"]
        assert blocked, "удаление должно ждать активацию, а не идти мимо неё"
        assert activation.code is None and activation.outcome is not None
        assert deleter["code"] == "category_in_use"
        with scene.factory() as db:
            assert db.get(FamilyCategory, extra.id) is not None
            family = db.get(WorkFamily, activation.outcome.created_family_ids[0])
            assert (family.status, family.family_category_id) == ("active", extra.id)
            assert db.get(FamilyDraft, selected).status == "activated"

    def test_category_pair_vs_a_human_category_change_loses_no_write(self, scene):
        """Шаг 3 берёт семьи пар `FOR UPDATE` и только затем читает статус и категорию.
        Смена категории человеком на карточке семьи ждёт конца активации и пишет
        поверх неё. Без замка до чтения человек успевал бы между чтением и
        `update_family` активации, и активация, прочитавшая «без категории»,
        затирала бы его выбор."""
        job_id, _ids = _old_run(scene, category_id=scene.work, with_proposal=True)
        with scene.factory() as db:
            other = db.execute(
                sa.select(FamilyCategory.id).where(FamilyCategory.id != scene.work).limit(1)
            ).scalar_one()
        activation = _Activation(
            scene, job_id=job_id, draft_ids=[], pause=True, pairs=[(scene.f3.id, scene.work)],
            pause_on="FROM work_families",
        )
        human: dict[str, object] = {"pid": None, "error": None}

        def change():
            try:
                with scene.factory() as db:
                    human["pid"] = db.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                    update_family(
                        db, family_id=scene.f3.id, family_category_id=other,
                        actor_id=scene.admin.id,
                    )
                    db.commit()
            except Exception as exc:  # noqa: BLE001
                human["error"] = f"{type(exc).__name__}: {exc}"

        th = threading.Thread(target=change, name="human", daemon=True)
        activation.start()
        blocked = False
        try:
            assert activation.paused.wait(_T), "активация не дошла до семьи пары"
            th.start()
            for _ in range(int(_T / 0.02)):
                if human["pid"] is not None or not th.is_alive():
                    break
                threading.Event().wait(0.02)
            assert human["pid"] is not None, f"смена категории не стартовала: {human['error']}"
            blocked = _waits_for_lock(scene.factory, human["pid"], th)
        finally:
            activation.finish()
            th.join(timeout=_T)
            if th.is_alive():
                _terminate(scene.factory, human["pid"])
                th.join(timeout=5)

        assert not th.is_alive(), "поток смены категории завис"
        assert activation.error is None and activation.code is None, (
            activation.error, activation.code,
        )
        assert human["error"] is None, human["error"]
        assert blocked, "смена категории должна ждать активацию, а не идти мимо неё"
        assert activation.outcome.categories_applied == (scene.f3.id,)
        with scene.factory() as db:
            assert db.get(WorkFamily, scene.f3.id).family_category_id == other, (
                "выбор человека затёрт активацией"
            )

    def test_activation_first_makes_the_processing_stale_and_keeps_unmarked_drafts(self, scene):
        """Активация берёт черновики `FOR UPDATE`, затем семью предложения категории
        `FOR UPDATE`. Обработка без `FOR UPDATE` открытых черновиков (шаг 2) успела бы
        взять эту семью `FOR KEY SHARE` раньше и замкнула бы цикл; с ним она ждёт."""
        job_id, (selected, other) = _old_run(
            scene, category_id=scene.work, with_proposal=True
        )
        activation = _Activation(
            scene, job_id=job_id, draft_ids=[selected], pause=True,
            pairs=[(scene.f3.id, scene.work)],
        )
        apply = _Apply(scene, barrier=False)
        activation.start()
        try:
            assert activation.paused.wait(_T), "активация не дошла до чтения черновиков"
            apply.start()
            blocked = _waits_for_lock(scene.factory, apply.pid, apply.thread)
            activation.go.set()
        finally:
            activation.finish()
            apply.finish()

        assert blocked, "обработка должна ждать активацию, а не обгонять её"
        assert activation.error is None and activation.code is None, (
            activation.error, activation.code,
        )
        assert apply.error is None, apply.error
        # Активация прошла раньше: её семья меняет вход открытия.
        assert activation.outcome.categories_applied == (scene.f3.id,)
        assert (apply.outcome.applied, apply.outcome.unapplied_reason) == (
            False, "stale_fingerprint",
        )
        assert _drafts_written(scene) == 0
        with scene.factory() as db:
            job = db.get(SemanticJob, scene.job_id)
            assert (job.status, job.cancel_reason) == ("cancelled", "input_changed")
            assert db.get(FamilyDraft, selected).status == "activated"
            assert db.get(FamilyDraft, other).status == "open"
            assert (
                db.execute(
                    sa.select(sa.func.count(WorkFamily.id)).where(
                        WorkFamily.title == "Старый черновик", WorkFamily.status == "active"
                    )
                ).scalar_one()
                == 1
            )

    def test_processing_first_supersedes_the_drafts_and_refuses_the_activation(self, scene):
        job_id, (selected, other) = _old_run(scene, category_id=scene.work)
        families = _count_families(scene)
        activation = _Activation(scene, job_id=job_id, draft_ids=[selected])
        apply = _Apply(scene)
        apply.start()
        try:
            apply.wait_paused()
            activation.start()
            blocked = _waits_for_lock(scene.factory, activation.pid, activation.thread)
        finally:
            apply.finish()
            activation.finish()

        assert blocked, "активация должна ждать обработку, а не идти мимо неё"
        assert apply.error is None and activation.error is None, (apply.error, activation.error)
        assert activation.code == "discovery_run_superseded" and activation.outcome is None
        assert apply.outcome.applied and apply.outcome.superseded_drafts == 2
        assert _drafts_written(scene) == 3
        assert _count_families(scene) == families, "ни одной семьи не создано"
        with scene.factory() as db:
            assert {db.get(FamilyDraft, i).status for i in (selected, other)} == {"superseded"}
