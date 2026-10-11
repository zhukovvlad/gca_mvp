"""Действия над черновиками открытия семей и их вид (спека 3б §2.4; DoD 9, 12).

Черновики строятся напрямую в базе: задание открытия `done` и группы с членами —
так каждый вход тестов задан явно, а не вытекает из ответа модели. Отказ
проверяется кодом (`exc.code`) и неизменённым состоянием строк."""
# ruff: noqa: F811 — `world` — фикстура, импортированная из соседнего набора
from __future__ import annotations

import datetime as dt
import uuid

import pytest
import sqlalchemy as sa

from crud.discovery import discovery_drafts
from models import (
    FamilyCategoryProposal,
    FamilyDraft,
    FamilyDraftMember,
    SemanticJob,
)
from services.discovery_drafts import (
    activate_discovery,
    discard_draft,
    edit_draft,
    latest_discovery_job_id,
    merge_draft,
    restore_draft,
)
from services.family_categories import create_category, delete_category
from services.work_families import UNSET, WorkFamilyError, archive_family, assign_family
from tests.factories import seed_category_id
from tests.integration.test_catalog_discovery_scope import (
    _ctx_bare_unit,
    _ctx_family_unit,
    _leaf_categories,
    _make,
    _make_system,
    _set,
    _uncategorize,
    world,  # noqa: F401 — фикстура
)
from tests.integration.test_semantic_queue_api import _active_family, _make_job

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _job(db, unit_id, *, status="done") -> SemanticJob:
    job = SemanticJob(
        kind="family_discovery", status=status, unit_id=unit_id,
        cancel_reason="input_changed" if status == "cancelled" else None,
        request_hash=f"t-{uuid.uuid4().hex}", prompt_version="discovery:1",
        model_requested="m", place_dictionary_version=1, candidates_hash="c",
        prefix_hash="p", input_hash="i", response_schema_version="v",
        serialization_version="1",
    )
    db.add(job)
    db.flush()
    return job


def _draft(
    db, job, ordinal, *, grp="new", title=None, definition="Определение", category_id=None,
    members=(), existing_family_id=None, similar_family_id=None,
) -> FamilyDraft:
    if grp == "new" and title is None:
        title = f"Черновик {ordinal}"
    if grp != "new":
        title = definition = category_id = similar_family_id = None
    draft = FamilyDraft(
        job_id=job.id, unit_id=job.unit_id, ordinal=ordinal, grp=grp, title=title,
        definition=definition, family_category_id=category_id,
        existing_family_id=existing_family_id, similar_family_id=similar_family_id,
        status="open",
    )
    db.add(draft)
    db.flush()
    for index, context_id in enumerate(members, start=1):
        db.add(
            FamilyDraftMember(
                draft_id=draft.id, job_id=job.id, context_id=context_id, name_index=index
            )
        )
    db.flush()
    return draft


def _sys(w, title, **kw):
    """Контекст-система единицы с семьёй: он в охвате (`SYSTEM` без семьи)."""
    context_id = _ctx_family_unit(w, title, **kw)
    _make_system(w.db, context_id, w.admin.id)
    return context_id


def _snap(db, *draft_ids) -> list[dict]:
    db.expire_all()
    rows = db.execute(
        sa.text("SELECT * FROM family_drafts WHERE id = ANY(:ids) ORDER BY id"),
        {"ids": list(draft_ids)},
    ).mappings()
    return [dict(row) for row in rows]


def _family_snap(db, family_id) -> dict:
    db.expire_all()
    return dict(
        db.execute(
            sa.text("SELECT * FROM work_families WHERE id = :f"), {"f": family_id}
        ).mappings().one()
    )


def _members_of(db, draft_id) -> list[int]:
    return sorted(
        db.execute(
            sa.select(FamilyDraftMember.context_id).where(FamilyDraftMember.draft_id == draft_id)
        ).scalars()
    )


def _refusal(code, call):
    with pytest.raises(WorkFamilyError) as exc:
        call()
    assert exc.value.code == code, f"ожидался {code}, получен {exc.value.code}: {exc.value}"


def _rows(view, draft_id) -> int:
    for key in ("drafts", "folded"):
        for row in view[key]:
            if row["id"] == draft_id:
                return row["rows"]
    raise AssertionError(f"черновика {draft_id} нет в виде")


class Run:
    """Выполненное открытие единицы `family_unit` с тремя черновиками A, B, C
    (по одному контексту-системе у каждого) и вторым открытием другой единицы."""


@pytest.fixture
def run(world):
    r = Run()
    r.w = world
    r.db = world.db
    r.admin = world.admin.id
    r.unit = world.family_unit
    r.job = _job(world.db, r.unit)
    r.ca, r.cb, r.cc = (_sys(world, name) for name in ("Имя А", "Имя Б", "Имя В"))
    r.a = _draft(world.db, r.job, 1, members=[r.ca])
    r.b = _draft(world.db, r.job, 2, members=[r.cb])
    r.c = _draft(world.db, r.job, 3, members=[r.cc])
    return r


# ---------------------------------------------------------------------------
#  «Последнее открытие»
# ---------------------------------------------------------------------------

class TestLatestJob:
    def test_none_without_a_done_discovery(self, world):
        assert latest_discovery_job_id(world.db, world.family_unit) is None

    def test_only_done_jobs_count_and_the_greatest_id_wins(self, world):
        first = _job(world.db, world.family_unit)
        second = _job(world.db, world.family_unit)
        _job(world.db, world.family_unit, status="cancelled")

        assert latest_discovery_job_id(world.db, world.family_unit) == second.id > first.id

    def test_pending_job_after_a_done_one_does_not_replace_it(self, world):
        done = _job(world.db, world.family_unit)
        _job(world.db, world.family_unit, status="pending")

        assert latest_discovery_job_id(world.db, world.family_unit) == done.id

    def test_units_are_separate_and_no_unit_is_its_own_unit(self, world):
        mine = _job(world.db, world.family_unit)
        other = _job(world.db, world.bare_unit)
        nobody = _job(world.db, None)

        assert latest_discovery_job_id(world.db, world.family_unit) == mine.id
        assert latest_discovery_job_id(world.db, world.bare_unit) == other.id
        assert latest_discovery_job_id(world.db, None) == nobody.id

    def test_done_job_of_another_kind_in_the_unit_does_not_count(self, world):
        done = _job(world.db, world.family_unit)
        context_id = _ctx_family_unit(world, "Задание другого вида")
        other = _make_job(world.db, context_id, status="done", unit_id=world.family_unit)
        assert other.id > done.id and other.kind != "family_discovery"

        assert latest_discovery_job_id(world.db, world.family_unit) == done.id


# ---------------------------------------------------------------------------
#  Править
# ---------------------------------------------------------------------------

class TestEdit:
    def test_changes_title_definition_and_category_and_stamps_the_editor(self, run):
        category = create_category(
            run.db, title="Другая", definition="Другое определение", actor_id=run.admin
        )

        draft = edit_draft(
            run.db, draft_id=run.a.id, title="  Новое имя ", definition=" Новое определение ",
            family_category_id=category.id, actor_id=run.admin,
        )

        assert (draft.title, draft.definition, draft.family_category_id) == (
            "Новое имя", "Новое определение", category.id,
        )
        assert draft.edited_by == run.admin and draft.edited_at is not None
        assert draft.status == "open"

    @pytest.mark.parametrize("field", ["title", "definition", "family_category_id"])
    def test_each_field_alone_leaves_the_others(self, run, field):
        category = create_category(run.db, title="Другая", definition="Д", actor_id=run.admin)
        value = {"title": "Только имя", "definition": "Только определение",
                 "family_category_id": category.id}[field]
        before = _snap(run.db, run.a.id)[0]

        edit_draft(run.db, draft_id=run.a.id, actor_id=run.admin, **{field: value})

        after = _snap(run.db, run.a.id)[0]
        changed = {k for k in before if before[k] != after[k]}
        assert changed == {field, "edited_by", "edited_at"}

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_title_is_refused_before_the_write(self, run, blank):
        before = _snap(run.db, run.a.id)
        _refusal(
            "draft_blank_title",
            lambda: edit_draft(run.db, draft_id=run.a.id, title=blank, actor_id=run.admin),
        )
        assert _snap(run.db, run.a.id) == before

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_definition_is_refused_before_the_write(self, run, blank):
        before = _snap(run.db, run.a.id)
        _refusal(
            "draft_blank_definition",
            lambda: edit_draft(run.db, draft_id=run.a.id, definition=blank, actor_id=run.admin),
        )
        assert _snap(run.db, run.a.id) == before

    def test_unset_title_is_not_a_blank_title(self, run):
        edit_draft(run.db, draft_id=run.a.id, title=UNSET, definition="Д2", actor_id=run.admin)

        assert _snap(run.db, run.a.id)[0]["title"] == "Черновик 1"

    def test_missing_category_is_category_not_found_and_changes_nothing(self, run):
        before = _snap(run.db, run.a.id)
        _refusal(
            "category_not_found",
            lambda: edit_draft(
                run.db, draft_id=run.a.id, title="Имя", family_category_id=987654,
                actor_id=run.admin,
            ),
        )
        assert _snap(run.db, run.a.id) == before

    def test_deleted_category_is_category_not_found(self, run):
        category = create_category(run.db, title="Временная", definition="Д", actor_id=run.admin)
        delete_category(run.db, category_id=category.id, actor_id=run.admin)
        before = _snap(run.db, run.a.id)

        _refusal(
            "category_not_found",
            lambda: edit_draft(
                run.db, draft_id=run.a.id, family_category_id=category.id, actor_id=run.admin
            ),
        )
        assert _snap(run.db, run.a.id) == before

    def test_discarded_draft_is_not_open(self, run):
        discard_draft(run.db, draft_id=run.a.id, actor_id=run.admin)
        _refusal(
            "draft_not_open",
            lambda: edit_draft(run.db, draft_id=run.a.id, title="Имя", actor_id=run.admin),
        )

    def test_groups_existing_and_not_work_are_not_open(self, run):
        existing = _draft(run.db, run.job, 4, grp="existing", existing_family_id=run.w.family.id)
        not_work = _draft(run.db, run.job, 5, grp="not_work")
        for draft in (existing, not_work):
            _refusal(
                "draft_not_open",
                lambda d=draft: edit_draft(run.db, draft_id=d.id, title="Имя", actor_id=run.admin),
            )

    def test_draft_of_an_older_run_is_superseded(self, run):
        _job(run.db, run.unit)  # новое выполненное открытие той же единицы
        before = _snap(run.db, run.a.id)
        _refusal(
            "discovery_run_superseded",
            lambda: edit_draft(run.db, draft_id=run.a.id, title="Имя", actor_id=run.admin),
        )
        assert _snap(run.db, run.a.id) == before

    def test_missing_draft_is_a_lookup_error(self, run):
        with pytest.raises(LookupError):
            edit_draft(run.db, draft_id=987654, title="Имя", actor_id=run.admin)


# ---------------------------------------------------------------------------
#  Слить с черновиком
# ---------------------------------------------------------------------------

class TestMergeIntoDraft:
    def test_source_is_merged_and_keeps_its_members(self, run):
        before_target = _snap(run.db, run.b.id)

        draft = merge_draft(
            run.db, draft_id=run.a.id, target_draft_id=run.b.id, actor_id=run.admin
        )

        assert (draft.status, draft.merged_into_draft_id, draft.merged_into_family_id) == (
            "merged", run.b.id, None,
        )
        assert draft.decided_by == run.admin and draft.decided_at is not None
        assert _members_of(run.db, run.a.id) == [run.ca]
        assert _members_of(run.db, run.b.id) == [run.cb]
        assert _snap(run.db, run.b.id) == before_target

    def test_the_target_shows_own_and_merged_rows(self, run):
        merge_draft(run.db, draft_id=run.a.id, target_draft_id=run.b.id, actor_id=run.admin)

        view = discovery_drafts(run.db, unit_id=run.unit)

        assert _rows(view, run.b.id) == 2
        assert [r["id"] for r in view["drafts"]][0] == run.b.id

    def test_merge_chain_counts_three_sets_in_the_last_target(self, run):
        merge_draft(run.db, draft_id=run.a.id, target_draft_id=run.b.id, actor_id=run.admin)
        merge_draft(run.db, draft_id=run.b.id, target_draft_id=run.c.id, actor_id=run.admin)

        view = discovery_drafts(run.db, unit_id=run.unit)

        assert _rows(view, run.c.id) == 3
        assert [r["id"] for r in view["drafts"]] == [run.c.id]
        assert sorted(r["id"] for r in view["folded"]) == sorted([run.a.id, run.b.id])

    def test_merge_into_itself_is_refused(self, run):
        before = _snap(run.db, run.a.id)
        _refusal(
            "draft_not_open",
            lambda: merge_draft(
                run.db, draft_id=run.a.id, target_draft_id=run.a.id, actor_id=run.admin
            ),
        )
        assert _snap(run.db, run.a.id) == before

    def test_merge_into_a_not_open_draft_is_refused(self, run):
        discard_draft(run.db, draft_id=run.b.id, actor_id=run.admin)
        before = _snap(run.db, run.a.id)
        _refusal(
            "draft_not_open",
            lambda: merge_draft(
                run.db, draft_id=run.a.id, target_draft_id=run.b.id, actor_id=run.admin
            ),
        )
        assert _snap(run.db, run.a.id) == before

    def test_merge_into_a_draft_of_another_run_is_refused(self, run):
        other_job = _job(run.db, run.unit)
        foreign = _draft(run.db, other_job, 1, members=[_sys(run.w, "Имя Г")])
        # `run.job` перестал быть последним: ставим тот же источник в последнее открытие.
        source = _draft(run.db, other_job, 2, members=[_sys(run.w, "Имя Д")])
        other_run_target = run.a
        before = _snap(run.db, source.id)

        _refusal(
            "draft_not_open",
            lambda: merge_draft(
                run.db, draft_id=source.id, target_draft_id=other_run_target.id,
                actor_id=run.admin,
            ),
        )
        assert _snap(run.db, source.id) == before
        # Соседний вход: тот же источник сливается с черновиком СВОЕГО открытия.
        merge_draft(run.db, draft_id=source.id, target_draft_id=foreign.id, actor_id=run.admin)

    @pytest.mark.parametrize("grp", ["existing", "not_work"])
    def test_merge_into_a_group_that_is_not_new_is_refused(self, run, grp):
        target = _draft(
            run.db, run.job, 4, grp=grp,
            existing_family_id=run.w.family.id if grp == "existing" else None,
        )
        _refusal(
            "draft_not_open",
            lambda: merge_draft(
                run.db, draft_id=run.a.id, target_draft_id=target.id, actor_id=run.admin
            ),
        )

    def test_merge_of_a_draft_of_an_older_run_is_superseded(self, run):
        _job(run.db, run.unit)
        _refusal(
            "discovery_run_superseded",
            lambda: merge_draft(
                run.db, draft_id=run.a.id, target_draft_id=run.b.id, actor_id=run.admin
            ),
        )

    def test_missing_target_draft_is_a_lookup_error(self, run):
        with pytest.raises(LookupError):
            merge_draft(run.db, draft_id=run.a.id, target_draft_id=987654, actor_id=run.admin)

    def test_exactly_one_target_is_required(self, run):
        with pytest.raises(ValueError):
            merge_draft(run.db, draft_id=run.a.id, actor_id=run.admin)
        with pytest.raises(ValueError):
            merge_draft(
                run.db, draft_id=run.a.id, target_draft_id=run.b.id,
                target_family_id=run.w.family.id, actor_id=run.admin,
            )


# ---------------------------------------------------------------------------
#  Слить с активной семьёй
# ---------------------------------------------------------------------------

class TestMergeIntoFamily:
    def test_source_is_merged_and_the_family_does_not_change_by_a_single_field(self, run):
        family = run.w.family
        before = _family_snap(run.db, family.id)

        draft = merge_draft(
            run.db, draft_id=run.a.id, target_family_id=family.id, actor_id=run.admin
        )

        assert (draft.status, draft.merged_into_family_id, draft.merged_into_draft_id) == (
            "merged", family.id, None,
        )
        assert draft.decided_by == run.admin
        assert _members_of(run.db, run.a.id) == [run.ca]
        assert _family_snap(run.db, family.id) == before

    def test_archived_family_is_not_active(self, run):
        target = _active_family(
            run.db, title="Архивная", unit_name="M2", actor_id=run.admin
        )
        archive_family(run.db, family_id=target.id, actor_id=run.admin)
        before = _snap(run.db, run.a.id)

        _refusal(
            "family_not_active",
            lambda: merge_draft(
                run.db, draft_id=run.a.id, target_family_id=target.id, actor_id=run.admin
            ),
        )
        assert _snap(run.db, run.a.id) == before

    def test_family_of_another_unit_is_refused(self, run):
        other = _active_family(run.db, title="Чужая единица", unit_name="M3", actor_id=run.admin)
        _refusal(
            "unit_mismatch",
            lambda: merge_draft(
                run.db, draft_id=run.a.id, target_family_id=other.id, actor_id=run.admin
            ),
        )

    def test_missing_family_is_family_not_found(self, run):
        _refusal(
            "family_not_found",
            lambda: merge_draft(
                run.db, draft_id=run.a.id, target_family_id=987654, actor_id=run.admin
            ),
        )

    def test_merge_of_a_not_open_draft_is_refused(self, run):
        discard_draft(run.db, draft_id=run.a.id, actor_id=run.admin)
        _refusal(
            "draft_not_open",
            lambda: merge_draft(
                run.db, draft_id=run.a.id, target_family_id=run.w.family.id, actor_id=run.admin
            ),
        )


# ---------------------------------------------------------------------------
#  Отбросить
# ---------------------------------------------------------------------------

class TestDiscard:
    def test_draft_is_discarded_and_keeps_its_members(self, run):
        draft = discard_draft(run.db, draft_id=run.a.id, actor_id=run.admin)

        assert draft.status == "discarded"
        assert draft.decided_by == run.admin and draft.decided_at is not None
        assert _members_of(run.db, run.a.id) == [run.ca]

    def test_a_second_discard_is_not_open(self, run):
        discard_draft(run.db, draft_id=run.a.id, actor_id=run.admin)
        _refusal(
            "draft_not_open", lambda: discard_draft(run.db, draft_id=run.a.id, actor_id=run.admin)
        )

    def test_merged_draft_cannot_be_discarded(self, run):
        merge_draft(run.db, draft_id=run.a.id, target_draft_id=run.b.id, actor_id=run.admin)
        _refusal(
            "draft_not_open", lambda: discard_draft(run.db, draft_id=run.a.id, actor_id=run.admin)
        )

    @pytest.mark.parametrize("grp", ["existing", "not_work"])
    def test_group_that_is_not_new_is_not_open(self, run, grp):
        draft = _draft(
            run.db, run.job, 4, grp=grp,
            existing_family_id=run.w.family.id if grp == "existing" else None,
        )
        _refusal(
            "draft_not_open", lambda: discard_draft(run.db, draft_id=draft.id, actor_id=run.admin)
        )
        assert _snap(run.db, draft.id)[0]["status"] == "open"

    def test_draft_of_an_older_run_is_superseded(self, run):
        _job(run.db, run.unit)
        _refusal(
            "discovery_run_superseded",
            lambda: discard_draft(run.db, draft_id=run.a.id, actor_id=run.admin),
        )
        assert _snap(run.db, run.a.id)[0]["status"] == "open"


# ---------------------------------------------------------------------------
#  Вернуть
# ---------------------------------------------------------------------------

class TestRestore:
    def test_discarded_draft_is_open_again_with_the_decision_cleared(self, run):
        before = _snap(run.db, run.a.id)[0]
        discard_draft(run.db, draft_id=run.a.id, actor_id=run.admin)

        draft = restore_draft(run.db, draft_id=run.a.id, actor_id=run.admin)

        assert draft.status == "open"
        assert (draft.decided_by, draft.decided_at) == (None, None)
        assert _snap(run.db, run.a.id)[0] == before

    def test_draft_merged_into_a_draft_is_back_and_the_target_numbers_return(self, run):
        before = _snap(run.db, run.a.id, run.b.id)
        merge_draft(run.db, draft_id=run.a.id, target_draft_id=run.b.id, actor_id=run.admin)
        assert _rows(discovery_drafts(run.db, unit_id=run.unit), run.b.id) == 2

        draft = restore_draft(run.db, draft_id=run.a.id, actor_id=run.admin)

        assert draft.status == "open"
        assert (draft.merged_into_draft_id, draft.merged_into_family_id) == (None, None)
        assert (draft.decided_by, draft.decided_at) == (None, None)
        view = discovery_drafts(run.db, unit_id=run.unit)
        assert (_rows(view, run.a.id), _rows(view, run.b.id)) == (1, 1)
        assert _snap(run.db, run.a.id, run.b.id) == before

    def test_draft_merged_into_a_family_is_back_with_the_family_unchanged(self, run):
        family = run.w.family
        family_before = _family_snap(run.db, family.id)
        before = _snap(run.db, run.a.id)[0]
        merge_draft(run.db, draft_id=run.a.id, target_family_id=family.id, actor_id=run.admin)

        draft = restore_draft(run.db, draft_id=run.a.id, actor_id=run.admin)

        assert draft.status == "open"
        assert (draft.merged_into_draft_id, draft.merged_into_family_id) == (None, None)
        assert _snap(run.db, run.a.id)[0] == before
        assert _family_snap(run.db, family.id) == family_before

    def test_drafts_merged_into_the_restored_one_stay_merged(self, run):
        merge_draft(run.db, draft_id=run.a.id, target_draft_id=run.b.id, actor_id=run.admin)
        merge_draft(run.db, draft_id=run.b.id, target_draft_id=run.c.id, actor_id=run.admin)

        restore_draft(run.db, draft_id=run.b.id, actor_id=run.admin)

        view = discovery_drafts(run.db, unit_id=run.unit)
        snaps = {s["id"]: s for s in _snap(run.db, run.a.id, run.b.id, run.c.id)}
        assert snaps[run.a.id]["status"] == "merged"
        assert snaps[run.a.id]["merged_into_draft_id"] == run.b.id
        assert snaps[run.b.id]["status"] == "open"
        assert (_rows(view, run.b.id), _rows(view, run.c.id)) == (2, 1)

    def test_activated_draft_is_not_restorable(self, run):
        run.db.execute(
            sa.update(FamilyDraft).where(FamilyDraft.id == run.a.id).values(
                status="activated", activated_family_id=run.w.family.id,
                decided_by=run.admin, decided_at=dt.datetime.now(dt.UTC),
            )
        )
        before = _snap(run.db, run.a.id)
        _refusal(
            "draft_not_restorable",
            lambda: restore_draft(run.db, draft_id=run.a.id, actor_id=run.admin),
        )
        assert _snap(run.db, run.a.id) == before

    def test_superseded_draft_is_not_restorable(self, run):
        run.db.execute(
            sa.update(FamilyDraft).where(FamilyDraft.id == run.a.id).values(status="superseded")
        )
        _refusal(
            "draft_not_restorable",
            lambda: restore_draft(run.db, draft_id=run.a.id, actor_id=run.admin),
        )

    def test_open_draft_is_not_restorable(self, run):
        before = _snap(run.db, run.a.id)
        _refusal(
            "draft_not_restorable",
            lambda: restore_draft(run.db, draft_id=run.a.id, actor_id=run.admin),
        )
        assert _snap(run.db, run.a.id) == before

    @pytest.mark.parametrize("grp", ["existing", "not_work"])
    def test_group_that_is_not_new_is_not_open(self, run, grp):
        draft = _draft(
            run.db, run.job, 4, grp=grp,
            existing_family_id=run.w.family.id if grp == "existing" else None,
        )
        _refusal(
            "draft_not_open", lambda: restore_draft(run.db, draft_id=draft.id, actor_id=run.admin)
        )

    def test_discarded_draft_of_an_older_run_is_not_restorable(self, run):
        discard_draft(run.db, draft_id=run.a.id, actor_id=run.admin)
        _job(run.db, run.unit)
        before = _snap(run.db, run.a.id)

        _refusal(
            "draft_not_restorable",
            lambda: restore_draft(run.db, draft_id=run.a.id, actor_id=run.admin),
        )
        assert _snap(run.db, run.a.id) == before


# ---------------------------------------------------------------------------
#  Вид черновиков
# ---------------------------------------------------------------------------

class TestView:
    def test_unmarked_draft_keeps_its_rows_after_a_sibling_is_activated(self, world):
        """Единица без семей: активация одного черновика заводит в ней семью, и по
        полному предикату охвата контексты единицы вышли бы из охвата. Экран считает
        строки снимка тем же режимом, что активация «не работы»."""
        unit = world.bare_unit
        job = _job(world.db, unit)
        first, _ = _make(world.db, world.factories, world.proposal, unit, "Голое один")
        second, _ = _make(world.db, world.factories, world.proposal, unit, "Голое два")
        note, _ = _make(world.db, world.factories, world.proposal, unit, "Голая заметка")
        work = seed_category_id(world.db)
        d1 = _draft(world.db, job, 1, title="Семья один", category_id=work, members=[first])
        d2 = _draft(world.db, job, 2, title="Семья два", category_id=work, members=[second])
        _draft(world.db, job, 3, grp="not_work", members=[note])
        activate_discovery(
            world.db, job_id=job.id, draft_ids=[d1.id], not_work_context_ids=[],
            family_categories=[], actor_id=world.admin.id,
        )

        view = discovery_drafts(world.db, unit_id=unit)

        assert _rows(view, d2.id) == 1
        assert view["not_work"]["names"][0]["contexts"] == 1
        assert [r["examples"] for r in view["drafts"]] == [["Голое два"]]

    def test_rest_follows_the_full_predicate_after_the_activation_of_a_sibling(self, world):
        """Остаток — нынешний неоткрытый остаток единицы, а не снимок: после
        активации в единице появилась семья, и обычный контекст, державшийся в охвате
        условием «в единице нет семей», в остаток не попадает."""
        unit = world.bare_unit
        job = _job(world.db, unit)
        first, _ = _make(world.db, world.factories, world.proposal, unit, "Голое один")
        _make(world.db, world.factories, world.proposal, unit, "Голое в остатке")
        d1 = _draft(
            world.db, job, 1, title="Семья один", category_id=seed_category_id(world.db),
            members=[first],
        )
        activate_discovery(
            world.db, job_id=job.id, draft_ids=[d1.id], not_work_context_ids=[],
            family_categories=[], actor_id=world.admin.id,
        )

        assert discovery_drafts(world.db, unit_id=unit)["rest"] == 0

    def test_links_show_the_status_of_the_family(self, run):
        similar = _active_family(run.db, title="Похожая", unit_name="M2", actor_id=run.admin)
        target = _active_family(run.db, title="Цель слияния", unit_name="M2", actor_id=run.admin)
        group = _draft(
            run.db, run.job, 4, grp="existing", existing_family_id=run.w.family.id,
            members=[_sys(run.w, "Имя в семью")],
        )
        with_similar = _draft(
            run.db, run.job, 5, similar_family_id=similar.id, members=[_sys(run.w, "Имя Я")]
        )
        merge_draft(run.db, draft_id=run.a.id, target_family_id=target.id, actor_id=run.admin)
        archive_family(run.db, family_id=similar.id, actor_id=run.admin)

        view = discovery_drafts(run.db, unit_id=run.unit)

        open_row = next(r for r in view["drafts"] if r["id"] == with_similar.id)
        assert open_row["similar_family_status"] == "archived"
        assert next(r for r in view["folded"] if r["id"] == run.a.id)[
            "merged_into_family_status"
        ] == "active"
        assert [(g["id"], g["family_status"]) for g in view["existing"]] == [(group.id, "active")]

    def test_none_without_a_done_discovery(self, world):
        _job(world.db, world.family_unit, status="pending")

        assert discovery_drafts(world.db, unit_id=world.family_unit) is None

    def test_groups_are_sorted_by_rows_descending(self, run):
        extra = _sys(run.w, "Имя Д")
        run.db.add(FamilyDraftMember(draft_id=run.c.id, job_id=run.job.id, context_id=extra, name_index=9))
        run.db.flush()

        view = discovery_drafts(run.db, unit_id=run.unit)

        assert [(r["id"], r["rows"]) for r in view["drafts"]] == [
            (run.c.id, 2), (run.a.id, 1), (run.b.id, 1),
        ]
        assert view["job_id"] == run.job.id and view["unit_id"] == run.unit

    def test_member_that_got_a_family_is_not_counted(self, run):
        assign_family(
            run.db, context_id=run.ca, family_id=run.w.family.id, actor_id=run.admin
        )

        view = discovery_drafts(run.db, unit_id=run.unit)

        assert _rows(view, run.a.id) == 0
        assert _rows(view, run.b.id) == 1

    def test_archived_member_is_not_counted(self, run):
        _set(run.db, run.ca, archived_at=dt.datetime.now(dt.UTC))

        view = discovery_drafts(run.db, unit_id=run.unit)

        assert (_rows(view, run.a.id), _rows(view, run.b.id)) == (0, 1)

    def test_examples_are_three_names_by_contexts_then_alphabet(self, run):
        first, second = _leaf_categories(run.db, 2)
        proposal = run.w.proposal
        ctx_b1, cp = _make(
            run.db, run.w.factories, proposal, run.unit, "Бетон", article_id=first
        )
        ctx_b2, _ = _make(
            run.db, run.w.factories, proposal, run.unit, "Бетон", cp=cp, article_id=second
        )
        titles = {"Вода": None, "Арматура": None, "Гвозди": None}
        members = [ctx_b1, ctx_b2]
        for title in titles:
            members.append(_sys(run.w, title))
        for context_id in (ctx_b1, ctx_b2):
            _make_system(run.db, context_id, run.admin)
        wide = _draft(run.db, run.job, 4, members=members)

        view = discovery_drafts(run.db, unit_id=run.unit)

        row = next(r for r in view["drafts"] if r["id"] == wide.id)
        assert row["rows"] == 5
        assert row["examples"] == ["Бетон", "Арматура", "Вода"]

    def test_not_work_group_is_listed_by_name_with_context_counts(self, run):
        first, second = _leaf_categories(run.db, 2)
        ctx_1, cp = _make(
            run.db, run.w.factories, run.w.proposal, run.unit, "Примечание", article_id=first
        )
        ctx_2, _ = _make(
            run.db, run.w.factories, run.w.proposal, run.unit, "Примечание", cp=cp,
            article_id=second,
        )
        lone = _sys(run.w, "Оговорка")
        for context_id in (ctx_1, ctx_2):
            _make_system(run.db, context_id, run.admin)
        group = _draft(run.db, run.job, 4, grp="not_work", members=[ctx_1, ctx_2, lone])

        not_work = discovery_drafts(run.db, unit_id=run.unit)["not_work"]

        assert not_work["id"] == group.id
        assert not_work["names"] == [
            {"title": "Примечание", "contexts": 2, "context_ids": sorted([ctx_1, ctx_2])},
            {"title": "Оговорка", "contexts": 1, "context_ids": [lone]},
        ]

    def test_not_work_is_none_without_the_group(self, run):
        assert discovery_drafts(run.db, unit_id=run.unit)["not_work"] is None

    def test_group_into_an_active_family_shows_family_and_rows(self, run):
        member = _sys(run.w, "Имя Е")
        group = _draft(run.db, run.job, 4, grp="existing", existing_family_id=run.w.family.id,
                       members=[member])

        existing = discovery_drafts(run.db, unit_id=run.unit)["existing"]

        assert existing == [
            {"id": group.id, "family_id": run.w.family.id,
             "family_title": run.w.family.title, "family_status": "active", "rows": 1}
        ]

    def test_rest_counts_scope_contexts_outside_every_group_of_the_run(self, run):
        _sys(run.w, "Имя из импорта")  # в охвате, в ответе его не было

        view = discovery_drafts(run.db, unit_id=run.unit)

        assert view["rest"] == 1

    def test_rest_is_zero_when_every_context_is_in_a_group(self, run):
        assert discovery_drafts(run.db, unit_id=run.unit)["rest"] == 0

    def test_similar_family_title_and_category_title_are_shown(self, run):
        work = seed_category_id(run.db)
        draft = _draft(
            run.db, run.job, 4, category_id=work, similar_family_id=run.w.family.id,
            members=[_sys(run.w, "Имя Ж")],
        )

        row = next(
            r for r in discovery_drafts(run.db, unit_id=run.unit)["drafts"] if r["id"] == draft.id
        )

        assert row["similar_family_id"] == run.w.family.id
        assert row["similar_family_title"] == run.w.family.title
        assert row["family_category_id"] == work and row["family_category_title"]

    def test_folded_lists_merged_and_discarded_and_open_lists_neither(self, run):
        merge_draft(run.db, draft_id=run.a.id, target_draft_id=run.b.id, actor_id=run.admin)
        discard_draft(run.db, draft_id=run.c.id, actor_id=run.admin)

        view = discovery_drafts(run.db, unit_id=run.unit)

        assert [r["id"] for r in view["drafts"]] == [run.b.id]
        assert {(r["id"], r["status"]) for r in view["folded"]} == {
            (run.a.id, "merged"), (run.c.id, "discarded"),
        }

    def test_activated_drafts_are_listed_apart(self, run):
        run.db.execute(
            sa.update(FamilyDraft).where(FamilyDraft.id == run.a.id).values(
                status="activated", activated_family_id=run.w.family.id,
                decided_by=run.admin, decided_at=dt.datetime.now(dt.UTC),
            )
        )
        run.db.expire_all()

        view = discovery_drafts(run.db, unit_id=run.unit)

        assert [r["id"] for r in view["activated"]] == [run.a.id]
        assert run.a.id not in [r["id"] for r in view["drafts"] + view["folded"]]

    def test_only_the_latest_run_is_shown(self, run):
        newer = _job(run.db, run.unit)
        fresh = _draft(run.db, newer, 1, members=[_sys(run.w, "Имя З")])

        view = discovery_drafts(run.db, unit_id=run.unit)

        assert view["job_id"] == newer.id
        assert [r["id"] for r in view["drafts"]] == [fresh.id]

    def test_category_proposals_are_those_a_family_still_needs(self, run):
        work = seed_category_id(run.db)
        needs = _active_family(run.db, title="Нужна категория", unit_name="M2", actor_id=run.admin)
        got = _active_family(run.db, title="Уже с категорией", unit_name="M2", actor_id=run.admin)
        archived = _active_family(run.db, title="Архивная", unit_name="M2", actor_id=run.admin)
        _uncategorize(run.db, needs.id)
        _uncategorize(run.db, archived.id)
        archive_family(run.db, family_id=archived.id, actor_id=run.admin)
        for family in (needs, got, archived):
            run.db.add(
                FamilyCategoryProposal(
                    job_id=run.job.id, family_id=family.id, family_category_id=work, status="open"
                )
            )
        run.db.flush()

        proposals = discovery_drafts(run.db, unit_id=run.unit)["category_proposals"]

        assert [(p["family_id"], p["family_title"], p["family_category_id"]) for p in proposals] == [
            (needs.id, "Нужна категория", work)
        ]
        assert proposals[0]["family_category_title"]
        assert proposals[0]["family_definition"] == needs.definition
        assert needs.definition

    def test_unit_null_is_a_unit_of_its_own(self, world):
        job = _job(world.db, None)
        context_id, _ = _make(world.db, world.factories, world.proposal, None, "Без единицы")
        group = _draft(world.db, job, 1, members=[context_id])

        view = discovery_drafts(world.db, unit_id=None)

        assert view["unit_id"] is None
        assert [(r["id"], r["rows"]) for r in view["drafts"]] == [(group.id, 1)]
        assert discovery_drafts(world.db, unit_id=world.family_unit) is None

    def test_bare_unit_contexts_count_in_their_own_unit(self, world):
        job = _job(world.db, world.bare_unit)
        context_id = _ctx_bare_unit(world, "Голая")
        group = _draft(world.db, job, 1, members=[context_id])

        view = discovery_drafts(world.db, unit_id=world.bare_unit)

        assert [(r["id"], r["rows"]) for r in view["drafts"]] == [(group.id, 1)]


# ---------------------------------------------------------------------------
#  Категория удалена до правки
# ---------------------------------------------------------------------------

class TestCategoryDeletedBeforeTheEdit:
    def test_deletion_nulls_the_draft_category_and_the_edit_then_refuses(self, run):
        category = create_category(run.db, title="Временная", definition="Д", actor_id=run.admin)
        edit_draft(
            run.db, draft_id=run.a.id, family_category_id=category.id, actor_id=run.admin
        )
        delete_category(run.db, category_id=category.id, actor_id=run.admin)
        assert _snap(run.db, run.a.id)[0]["family_category_id"] is None

        _refusal(
            "category_not_found",
            lambda: edit_draft(
                run.db, draft_id=run.a.id, family_category_id=category.id, actor_id=run.admin
            ),
        )
        assert _snap(run.db, run.a.id)[0]["family_category_id"] is None
