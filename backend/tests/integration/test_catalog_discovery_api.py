"""Маршруты черновиков открытия и «Вернуть в разбор» (спека 3б §2.12): права,
статусы кодов отказов, форма ответов, `unit_id: null`."""
# ruff: noqa: F811 — `run`, `world` — фикстуры, импортированные из соседних наборов
from __future__ import annotations

import pytest
import sqlalchemy as sa

import services.discovery_drafts as dd
import services.work_families as wf
import services.work_variants as wv
from routers import semantic as semantic_router
from services.family_categories import create_category, delete_category
from services.work_families import archive_family
from tests.factories import seed_category_id
from tests.integration.test_catalog_discovery_activate import (
    _all_families,
    _state,
    act,  # noqa: F401 — фикстура
)
from tests.integration.test_catalog_discovery_drafts import (
    _draft,
    _job,
    _snap,
    _sys,
    run,  # noqa: F401 — фикстура
)
from tests.integration.test_catalog_discovery_reopen import _not_work_by_human
from tests.integration.test_catalog_discovery_scope import _make, world  # noqa: F401 — фикстура
from tests.integration.test_semantic_queue_api import _active_family

pytestmark = pytest.mark.integration

BASE = "/api/v1/semantic"


def _detail(response) -> dict:
    body = response.json()["detail"]
    assert isinstance(body, dict), body
    return body


# ---------------------------------------------------------------------------
#  Права
# ---------------------------------------------------------------------------

ROUTES = (
    ("GET", f"{BASE}/discovery/drafts?unit_id=1", None),
    ("PATCH", f"{BASE}/discovery/drafts/1", {"title": "Имя"}),
    ("POST", f"{BASE}/discovery/drafts/1/merge", {"target_draft_id": 2}),
    ("POST", f"{BASE}/discovery/drafts/1/discard", None),
    ("POST", f"{BASE}/discovery/drafts/1/restore", None),
    ("POST", f"{BASE}/contexts/1/reopen", None),
    ("POST", f"{BASE}/discovery/1/activate", {"draft_ids": [1]}),
)


class TestPermissions:
    @pytest.mark.parametrize(("method", "path", "body"), ROUTES)
    def test_member_gets_403(self, member_client, world, method, path, body):
        response = member_client.request(method, path, json=body)

        assert response.status_code == 403


# ---------------------------------------------------------------------------
#  Чтение
# ---------------------------------------------------------------------------

class TestDraftsRoute:
    def test_returns_groups_members_rest_and_proposals(self, admin_client, run):
        response = admin_client.get(f"{BASE}/discovery/drafts", params={"unit_id": run.unit})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["unit_id"] == run.unit
        drafts = body["drafts"]
        assert drafts["job_id"] == run.job.id
        assert {r["id"] for r in drafts["drafts"]} == {run.a.id, run.b.id, run.c.id}
        assert set(drafts) >= {
            "drafts", "folded", "activated", "existing", "not_work", "rest", "category_proposals",
            "opened_at",
        }
        assert all(r["rows"] == 1 and len(r["examples"]) == 1 for r in drafts["drafts"])

    def test_no_completed_discovery_is_null(self, admin_client, world):
        response = admin_client.get(f"{BASE}/discovery/drafts", params={"unit_id": world.family_unit})

        assert response.status_code == 200
        assert response.json() == {"unit_id": world.family_unit, "drafts": None}

    def test_missing_unit_id_is_the_unit_without_a_unit(self, admin_client, world):
        job = _job(world.db, None)
        context_id, _ = _make(world.db, world.factories, world.proposal, None, "Без единицы")
        group = _draft(world.db, job, 1, members=[context_id])

        response = admin_client.get(f"{BASE}/discovery/drafts")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["unit_id"] is None
        assert [(r["id"], r["rows"]) for r in body["drafts"]["drafts"]] == [(group.id, 1)]


# ---------------------------------------------------------------------------
#  Действия
# ---------------------------------------------------------------------------

class TestEditRoute:
    def test_edit_returns_the_draft_and_persists(self, admin_client, run):
        response = admin_client.patch(
            f"{BASE}/discovery/drafts/{run.a.id}", json={"title": "Новое имя"}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["id"], body["title"], body["status"]) == (run.a.id, "Новое имя", "open")
        assert _snap(run.db, run.a.id)[0]["title"] == "Новое имя"

    def test_category_change_reaches_the_service(self, admin_client, run):
        category = create_category(run.db, title="Другая", definition="Д", actor_id=run.admin)

        response = admin_client.patch(
            f"{BASE}/discovery/drafts/{run.a.id}", json={"family_category_id": category.id}
        )

        assert response.status_code == 200, response.text
        assert response.json()["family_category_id"] == category.id

    @pytest.mark.parametrize(
        ("body", "code"),
        [
            ({"title": ""}, "draft_blank_title"),
            ({"title": "   "}, "draft_blank_title"),
            ({"definition": ""}, "draft_blank_definition"),
            ({"definition": "  "}, "draft_blank_definition"),
        ],
    )
    def test_blank_fields_are_422_with_the_code(self, admin_client, run, body, code):
        run.db.commit()  # откат отказа не должен снести подготовку теста
        before = _snap(run.db, run.a.id)

        response = admin_client.patch(f"{BASE}/discovery/drafts/{run.a.id}", json=body)

        assert response.status_code == 422, response.text
        detail = _detail(response)
        assert detail["code"] == code and detail["message"]
        assert _snap(run.db, run.a.id) == before

    def test_deleted_category_is_409(self, admin_client, run):
        category = create_category(run.db, title="Временная", definition="Д", actor_id=run.admin)
        delete_category(run.db, category_id=category.id, actor_id=run.admin)

        response = admin_client.patch(
            f"{BASE}/discovery/drafts/{run.a.id}", json={"family_category_id": category.id}
        )

        assert response.status_code == 409
        assert _detail(response)["code"] == "category_not_found"

    def test_null_category_is_a_validation_error(self, admin_client, run):
        response = admin_client.patch(
            f"{BASE}/discovery/drafts/{run.a.id}", json={"family_category_id": None}
        )

        assert response.status_code == 422

    def test_missing_draft_is_404(self, admin_client, run):
        response = admin_client.patch(f"{BASE}/discovery/drafts/987654", json={"title": "Имя"})

        assert response.status_code == 404

    def test_draft_of_an_older_run_is_409(self, admin_client, run):
        _job(run.db, run.unit)

        response = admin_client.patch(f"{BASE}/discovery/drafts/{run.a.id}", json={"title": "Имя"})

        assert response.status_code == 409
        assert _detail(response)["code"] == "discovery_run_superseded"


class TestMergeRoute:
    def test_merge_into_a_draft(self, admin_client, run):
        response = admin_client.post(
            f"{BASE}/discovery/drafts/{run.a.id}/merge", json={"target_draft_id": run.b.id}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["status"], body["merged_into_draft_id"], body["merged_into_family_id"]) == (
            "merged", run.b.id, None,
        )

    def test_merge_into_a_family(self, admin_client, run):
        response = admin_client.post(
            f"{BASE}/discovery/drafts/{run.a.id}/merge",
            json={"target_family_id": run.w.family.id},
        )

        assert response.status_code == 200, response.text
        assert response.json()["merged_into_family_id"] == run.w.family.id

    @pytest.mark.parametrize(
        "body", [{}, {"target_draft_id": None, "target_family_id": None}]
    )
    def test_no_target_is_422(self, admin_client, run, body):
        response = admin_client.post(f"{BASE}/discovery/drafts/{run.a.id}/merge", json=body)

        assert response.status_code == 422

    def test_two_targets_are_422(self, admin_client, run):
        response = admin_client.post(
            f"{BASE}/discovery/drafts/{run.a.id}/merge",
            json={"target_draft_id": run.b.id, "target_family_id": run.w.family.id},
        )

        assert response.status_code == 422

    def test_not_open_target_is_409_and_nothing_is_written(self, admin_client, run):
        admin_client.post(f"{BASE}/discovery/drafts/{run.b.id}/discard")
        before = _snap(run.db, run.a.id)

        response = admin_client.post(
            f"{BASE}/discovery/drafts/{run.a.id}/merge", json={"target_draft_id": run.b.id}
        )

        assert response.status_code == 409
        assert _detail(response)["code"] == "draft_not_open"
        assert _snap(run.db, run.a.id) == before

    def test_inactive_family_is_409(self, admin_client, run):
        target = _active_family(run.db, title="Архивная", unit_name="M2", actor_id=run.admin)
        archive_family(run.db, family_id=target.id, actor_id=run.admin)

        response = admin_client.post(
            f"{BASE}/discovery/drafts/{run.a.id}/merge", json={"target_family_id": target.id}
        )

        assert response.status_code == 409
        assert _detail(response)["code"] == "family_not_active"

    def test_missing_target_draft_is_404(self, admin_client, run):
        response = admin_client.post(
            f"{BASE}/discovery/drafts/{run.a.id}/merge", json={"target_draft_id": 987654}
        )

        assert response.status_code == 404


class TestDiscardAndRestoreRoutes:
    def test_discard_then_restore(self, admin_client, run):
        discarded = admin_client.post(f"{BASE}/discovery/drafts/{run.a.id}/discard")
        assert discarded.status_code == 200, discarded.text
        assert discarded.json()["status"] == "discarded"

        restored = admin_client.post(f"{BASE}/discovery/drafts/{run.a.id}/restore")

        assert restored.status_code == 200, restored.text
        assert restored.json()["status"] == "open"

    def test_discard_of_a_discarded_draft_is_409_not_open(self, admin_client, run):
        admin_client.post(f"{BASE}/discovery/drafts/{run.a.id}/discard")

        response = admin_client.post(f"{BASE}/discovery/drafts/{run.a.id}/discard")

        assert response.status_code == 409
        assert _detail(response)["code"] == "draft_not_open"

    def test_restore_of_an_open_draft_is_409_not_restorable(self, admin_client, run):
        response = admin_client.post(f"{BASE}/discovery/drafts/{run.a.id}/restore")

        assert response.status_code == 409
        assert _detail(response)["code"] == "draft_not_restorable"

    def test_restore_after_a_new_run_is_409_not_restorable(self, admin_client, run):
        admin_client.post(f"{BASE}/discovery/drafts/{run.a.id}/discard")
        _job(run.db, run.unit)

        response = admin_client.post(f"{BASE}/discovery/drafts/{run.a.id}/restore")

        assert response.status_code == 409
        assert _detail(response)["code"] == "draft_not_restorable"

    def test_missing_draft_is_404_on_both(self, admin_client, run):
        assert admin_client.post(f"{BASE}/discovery/drafts/987654/discard").status_code == 404
        assert admin_client.post(f"{BASE}/discovery/drafts/987654/restore").status_code == 404

    def test_unit_null_draft_passes_through_the_actions(self, admin_client, world):
        job = _job(world.db, None)
        context_id, _ = _make(world.db, world.factories, world.proposal, None, "Без единицы")
        group = _draft(world.db, job, 1, members=[context_id])

        response = admin_client.post(f"{BASE}/discovery/drafts/{group.id}/discard")

        assert response.status_code == 200, response.text
        assert response.json()["unit_id"] is None


# ---------------------------------------------------------------------------
#  Вернуть в разбор
# ---------------------------------------------------------------------------

class TestReopenRoute:
    def test_returns_the_card_of_the_reopened_context(self, admin_client, world):
        context_id = _not_work_by_human(world, "Возврат по маршруту")

        response = admin_client.post(f"{BASE}/contexts/{context_id}/reopen")

        assert response.status_code == 200, response.text
        card = response.json()
        assert (card["id"], card["semantic_state"], card["reopenable"]) == (
            context_id, "SUGGESTED", False,
        )
        assert card["catalog_kind"] == "POSITION"

    def test_state_refusal_is_409(self, admin_client, world):
        context_id = _make(
            world.db, world.factories, world.proposal, world.family_unit, "Рабочий"
        )[0]

        response = admin_client.post(f"{BASE}/contexts/{context_id}/reopen")

        assert response.status_code == 409
        assert _detail(response)["code"] == "context_not_reopenable_state"

    def test_position_refusal_is_409(self, admin_client, world):
        context_id = _make(
            world.db, world.factories, world.proposal, world.family_unit, "Заголовок",
            cp_kind="HEADER",
        )[0]
        world.db.commit()  # откат отказа не должен снести подготовку теста

        response = admin_client.post(f"{BASE}/contexts/{context_id}/reopen")

        assert response.status_code == 409
        assert _detail(response)["code"] == "context_not_applicable_by_position"
        assert world.db.execute(
            sa.text("SELECT semantic_state FROM catalog_contexts WHERE id = :c"), {"c": context_id}
        ).scalar_one() == "NOT_APPLICABLE"

    def test_missing_context_is_404(self, admin_client, world):
        assert admin_client.post(f"{BASE}/contexts/987654/reopen").status_code == 404


# ---------------------------------------------------------------------------
#  Карта кодов
# ---------------------------------------------------------------------------

class TestStatusMap:
    @pytest.mark.parametrize(
        ("code", "status"),
        [
            (dd.REFUSE_DRAFT_NOT_OPEN, 409),
            (dd.REFUSE_DRAFT_NOT_RESTORABLE, 409),
            (dd.REFUSE_DISCOVERY_RUN_SUPERSEDED, 409),
            (wv.REFUSE_CONTEXT_NOT_REOPENABLE_STATE, 409),
            (wv.REFUSE_CONTEXT_NOT_APPLICABLE_BY_POSITION, 409),
            (dd.REFUSE_DRAFT_BLANK_TITLE, 422),
            (dd.REFUSE_DRAFT_BLANK_DEFINITION, 422),
            (dd.REFUSE_DRAFT_WITHOUT_CATEGORY, 422),
            (dd.REFUSE_CONTEXT_NOT_IN_GROUP, 422),
            (dd.REFUSE_CATEGORY_NOT_PROPOSED, 422),
        ],
    )
    def test_every_code_has_the_status_of_the_table(self, code, status):
        assert semantic_router._status_for_code(code) == status

    def test_codes_are_the_literals_of_the_table(self):
        assert {
            dd.REFUSE_DRAFT_NOT_OPEN, dd.REFUSE_DRAFT_NOT_RESTORABLE,
            dd.REFUSE_DISCOVERY_RUN_SUPERSEDED, dd.REFUSE_DRAFT_BLANK_TITLE,
            dd.REFUSE_DRAFT_BLANK_DEFINITION, wv.REFUSE_CONTEXT_NOT_REOPENABLE_STATE,
            wv.REFUSE_CONTEXT_NOT_APPLICABLE_BY_POSITION, dd.REFUSE_DRAFT_WITHOUT_CATEGORY,
            dd.REFUSE_CONTEXT_NOT_IN_GROUP, dd.REFUSE_CATEGORY_NOT_PROPOSED,
        } == {
            "draft_not_open", "draft_not_restorable", "discovery_run_superseded",
            "draft_blank_title", "draft_blank_definition", "context_not_reopenable_state",
            "context_not_applicable_by_position", "draft_without_category",
            "context_not_in_group", "category_not_proposed",
        }


# ---------------------------------------------------------------------------
#  Активация
# ---------------------------------------------------------------------------

class TestActivateRoute:
    def _post(self, client, act, **body):
        return client.post(f"{BASE}/discovery/{act.job.id}/activate", json=body)

    def test_returns_the_outcome_and_creates_the_families(self, admin_client, act):
        response = self._post(
            admin_client, act, draft_ids=[act.d1.id], not_work_context_ids=[act.n1],
            family_categories=[{"family_id": act.fa.id, "family_category_id": act.work}],
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["created_family_ids"]) == 1
        assert body["categories_applied"] == [act.fa.id] and body["categories_skipped"] == []
        assert body["not_work_applied"] == [act.n1] and body["not_work_skipped"] == []
        assert body["reask_unit_id"] == act.unit
        assert _state(act.db, act.n1) == "NOT_APPLICABLE"

    def test_unit_null_passes_through_the_activation(self, admin_client, world):
        job = _job(world.db, None)
        context_id, _ = _make(world.db, world.factories, world.proposal, None, "Без единицы")
        draft = _draft(
            world.db, job, 1, title="Семья без единицы", category_id=seed_category_id(world.db),
            members=[context_id],
        )

        response = admin_client.post(
            f"{BASE}/discovery/{job.id}/activate", json={"draft_ids": [draft.id]}
        )

        assert response.status_code == 200, response.text
        assert response.json()["reask_unit_id"] is None

    @pytest.mark.parametrize(
        ("code", "status"),
        [
            ("draft_without_category", 422),
            ("context_not_in_group", 422),
            ("category_not_proposed", 422),
            ("duplicate_active_family", 409),
            ("draft_not_open", 409),
            ("discovery_run_superseded", 409),
            ("category_not_found", 409),
        ],
    )
    def test_every_refusal_has_the_status_and_the_code_of_the_table(
        self, admin_client, act, code, status
    ):
        body: dict = {}
        if code == "draft_without_category":
            body = {"draft_ids": [act.d3.id]}
        elif code == "context_not_in_group":
            body = {"not_work_context_ids": [act.s[0]]}
        elif code == "category_not_proposed":
            body = {"family_categories": [{"family_id": act.w.family.id,
                                           "family_category_id": act.work}]}
        elif code == "duplicate_active_family":
            dup = _draft(act.db, act.job, 5, title="Семья пола", category_id=act.work,
                         members=[_sys(act.w, "Имя дубля")])
            body = {"draft_ids": [dup.id]}
        elif code == "draft_not_open":
            body = {"draft_ids": [act.nw.id]}
        elif code == "discovery_run_superseded":
            _job(act.db, act.unit)
            body = {"draft_ids": [act.d1.id]}
        else:
            gone = create_category(act.db, title="Исчезнет", definition="Д", actor_id=act.admin)
            delete_category(act.db, category_id=gone.id, actor_id=act.admin)
            body = {"family_categories": [{"family_id": act.fa.id, "family_category_id": gone.id}]}
        act.db.commit()  # откат отказа не должен снести подготовку теста

        response = self._post(admin_client, act, **body)

        assert response.status_code == status, response.text
        detail = _detail(response)
        assert detail["code"] == code and detail["message"]

    def test_unknown_job_is_404(self, admin_client, act):
        response = admin_client.post(f"{BASE}/discovery/987654/activate", json={})

        assert response.status_code == 404

    def test_refusal_leaves_no_records(self, admin_client, act):
        dup = _draft(act.db, act.job, 5, title="Семья пола", category_id=act.work,
                     members=[_sys(act.w, "Имя дубля")])
        act.db.commit()
        families = _all_families(act.db)

        response = self._post(admin_client, act, draft_ids=[act.d1.id, dup.id],
                              not_work_context_ids=[act.n1])

        assert response.status_code == 409
        act.db.expire_all()
        assert _all_families(act.db) == families
        assert _snap(act.db, act.d1.id, dup.id)[0]["status"] == "open"
        assert _state(act.db, act.n1) == "SUGGESTED"

    def test_second_line_through_the_key_is_409_and_leaves_no_families(
        self, admin_client, act, monkeypatch
    ):
        dup = _draft(act.db, act.job, 5, title="Семья пола", category_id=act.work,
                     members=[_sys(act.w, "Имя ключа")])
        act.db.commit()
        families = _all_families(act.db)
        monkeypatch.setattr(wf, "_duplicate_active_family_id", lambda *a, **k: None)

        response = self._post(admin_client, act, draft_ids=[act.d1.id, dup.id])

        assert response.status_code == 409, response.text
        assert _detail(response)["code"] == "duplicate_active_family"
        act.db.expire_all()
        assert _all_families(act.db) == families

    def test_failure_at_the_last_step_leaves_no_records(self, admin_client, act, monkeypatch):
        act.db.commit()
        families = _all_families(act.db)
        before = _snap(act.db, act.d1.id)

        def boom(*args, **kwargs):
            raise RuntimeError("сбой на последнем шаге")

        monkeypatch.setattr(dd, "reconcile_or_defer", boom)
        with pytest.raises(RuntimeError):
            self._post(admin_client, act, draft_ids=[act.d1.id], not_work_context_ids=[act.n1])

        act.db.expire_all()
        assert _all_families(act.db) == families
        assert _snap(act.db, act.d1.id) == before
        assert _state(act.db, act.n1) == "SUGGESTED"
