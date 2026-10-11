"""Признак `actionable` последнего открытия единицы: на экране черновиков ещё есть что решать
(спека 3б §2.4). Один помощник считает его и для строки блока (`discovery_units`), и для вида
черновиков (`discovery_drafts`); каждая причина проверяется отдельно, соседний вход — без неё."""
# ruff: noqa: F811 — `world`, `run` — фикстуры, импортированные из соседних наборов
from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa

from config import settings as app_settings
from crud.discovery import discovery_drafts, discovery_units
from models import FamilyCategoryProposal, FamilyDraft
from services.discovery_drafts import discard_draft, merge_draft
from services.work_families import archive_family, assign_family
from tests.factories import seed_category_id
from tests.integration.test_catalog_discovery_drafts import (
    _draft,
    _job,
    _sys,
    run,  # noqa: F401 — фикстура
)
from tests.integration.test_catalog_discovery_scope import _uncategorize, world  # noqa: F401
from tests.integration.test_semantic_queue_api import _active_family

pytestmark = pytest.mark.integration


def _flags(run) -> tuple[bool, bool]:
    """`actionable` в виде черновиков и в строке блока той же единицы."""
    run.db.expire_all()
    view = discovery_drafts(run.db, unit_id=run.unit)
    row = next(r for r in discovery_units(run.db, settings=app_settings) if r["unit_id"] == run.unit)
    return view["actionable"], row["last_discovery"]["actionable"]


def _activate_all(run) -> None:
    run.db.execute(
        sa.update(FamilyDraft)
        .where(FamilyDraft.job_id == run.job.id, FamilyDraft.grp == "new")
        .values(
            status="activated", activated_family_id=run.w.family.id,
            decided_by=run.admin, decided_at=dt.datetime.now(dt.UTC),
        )
    )
    run.db.expire_all()


@pytest.fixture
def listed(run):
    """Единица всегда в блоке: у активной семьи нет категории."""
    _uncategorize(run.db, run.w.family.id)
    return run


class TestReasons:
    def test_an_open_new_draft_alone(self, listed):
        assert _flags(listed) == (True, True)

    def test_discarded_and_merged_drafts_alone_can_be_restored(self, listed):
        merge_draft(
            listed.db, draft_id=listed.a.id, target_family_id=listed.w.family.id, actor_id=listed.admin
        )
        discard_draft(listed.db, draft_id=listed.b.id, actor_id=listed.admin)
        discard_draft(listed.db, draft_id=listed.c.id, actor_id=listed.admin)

        assert _flags(listed) == (True, True)

    def test_only_discarded_drafts(self, listed):
        for draft in (listed.a, listed.b, listed.c):
            discard_draft(listed.db, draft_id=draft.id, actor_id=listed.admin)

        assert _flags(listed) == (True, True)

    def test_an_open_category_proposal_alone(self, listed):
        _activate_all(listed)
        needs = _active_family(
            listed.db, title="Нужна категория", unit_name="M2", actor_id=listed.admin
        )
        _uncategorize(listed.db, needs.id)
        listed.db.add(
            FamilyCategoryProposal(
                job_id=listed.job.id, family_id=needs.id,
                family_category_id=seed_category_id(listed.db), status="open",
            )
        )
        listed.db.flush()

        assert _flags(listed) == (True, True)

    def test_a_proposal_the_family_no_longer_needs_is_not_a_reason(self, listed):
        _activate_all(listed)
        archived = _active_family(
            listed.db, title="Архивная", unit_name="M2", actor_id=listed.admin
        )
        _uncategorize(listed.db, archived.id)
        archive_family(listed.db, family_id=archived.id, actor_id=listed.admin)
        listed.db.add(
            FamilyCategoryProposal(
                job_id=listed.job.id, family_id=archived.id,
                family_category_id=seed_category_id(listed.db), status="open",
            )
        )
        listed.db.flush()

        assert _flags(listed) == (False, False)

    def test_a_not_work_member_in_scope_alone(self, listed):
        _activate_all(listed)
        member = _sys(listed.w, "Примечание")
        _draft(listed.db, listed.job, 4, grp="not_work", members=[member])

        assert _flags(listed) == (True, True)

    def test_a_not_work_group_whose_members_left_the_scope_is_not_a_reason(self, listed):
        _activate_all(listed)
        member = _sys(listed.w, "Примечание")
        _draft(listed.db, listed.job, 4, grp="not_work", members=[member])
        assign_family(
            listed.db, context_id=member, family_id=listed.w.family.id, actor_id=listed.admin
        )

        assert _flags(listed) == (False, False)

    def test_existing_groups_alone_are_informational(self, listed):
        _activate_all(listed)
        member = _sys(listed.w, "Имя Е")
        _draft(
            listed.db, listed.job, 4, grp="existing", existing_family_id=listed.w.family.id,
            members=[member],
        )

        assert _flags(listed) == (False, False)

    def test_everything_activated_and_applied(self, listed):
        _activate_all(listed)
        member = _sys(listed.w, "Примечание")
        _draft(listed.db, listed.job, 4, grp="not_work", members=[member])
        needs = _active_family(
            listed.db, title="Нужна категория", unit_name="M2", actor_id=listed.admin
        )
        _uncategorize(listed.db, needs.id)
        proposal = FamilyCategoryProposal(
            job_id=listed.job.id, family_id=needs.id,
            family_category_id=seed_category_id(listed.db), status="open",
        )
        listed.db.add(proposal)
        listed.db.flush()
        assert _flags(listed)[0] is True
        # Применено: у семьи есть категория, строка «не работы» получила семью.
        listed.db.execute(
            sa.text("UPDATE work_families SET family_category_id = :c WHERE id = :f"),
            {"c": seed_category_id(listed.db), "f": needs.id},
        )
        assign_family(
            listed.db, context_id=member, family_id=listed.w.family.id, actor_id=listed.admin
        )

        assert _flags(listed) == (False, False)


class TestNewerJob:
    @pytest.mark.parametrize("status", ["error", "cancelled", "pending"])
    def test_a_newer_job_of_another_status_does_not_hide_the_latest_done_run(self, listed, status):
        _job(listed.db, listed.unit, status=status)

        listed.db.expire_all()
        row = next(
            r for r in discovery_units(listed.db, settings=app_settings)
            if r["unit_id"] == listed.unit
        )

        # Строка называет статус новейшего задания, а `actionable` — у последнего ВЫПОЛНЕННОГО.
        assert row["last_discovery"]["status"] == status
        assert row["last_discovery"]["actionable"] is True
