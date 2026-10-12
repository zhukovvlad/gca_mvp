"""Строка семьи несёт число живых заданий предложений её единицы
(`unit_busy_jobs`): экран предупреждает о сбросе перезапроса при правке семьи.

Число обязано быть тем самым, по которому отказывает запуск открытия
(`discovery_unit_busy`), поэтому каждый вход сверяется и с текстом отказа.
Эталоны — независимые литералы."""
# ruff: noqa: F811 — `world` — фикстура, импортированная из соседнего набора
from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

import crud.semantic as crud_semantic
from config import settings as app_settings
from models import SemanticJob
from services.family_discovery import DiscoveryError, launch_discovery, preview_discovery
from tests.integration.test_catalog_discovery_launch import _fingerprint, _held_batch
from tests.integration.test_catalog_discovery_scope import (
    _ctx_bare_unit,
    _ctx_family_unit,
    _make,
    world,  # noqa: F401 — фикстура
)
from tests.integration.test_semantic_queue_api import _active_family, _make_job

pytestmark = pytest.mark.integration

BASE = "/api/v1/semantic"


def _row(db, family_id) -> dict:
    return next(
        r for r in crud_semantic.list_families(db, status=None, unit_id=None) if r["id"] == family_id
    )


def _job(w, context_id, unit_id, status):
    job = _make_job(w.db, context_id, unit_id=unit_id)
    values = {"status": status}
    if status == "running":
        values["claim_token"] = uuid.uuid4()
    if status == "cancelled":
        values["cancel_reason"] = "not_applicable"
    w.db.execute(sa.update(SemanticJob).where(SemanticJob.id == job.id).values(**values))
    w.db.expire_all()
    return job


def _refusal_text(w, unit_id) -> str:
    preview = preview_discovery(w.db, unit_id=unit_id, settings=app_settings)
    with pytest.raises(DiscoveryError) as raised:
        launch_discovery(
            w.db, unit_id=unit_id, preview_hash=preview.preview_hash, actor_id=w.admin.id,
            settings=app_settings,
        )
    assert raised.value.code == "discovery_unit_busy"
    return str(raised.value)


class TestUnitBusyJobs:
    def test_idle_unit_is_zero(self, world):
        assert _row(world.db, world.family.id)["unit_busy_jobs"] == 0

    @pytest.mark.parametrize("status", ["pending", "running"])
    def test_open_suggestion_job_of_the_unit_is_counted(self, world, status):
        context_id = _ctx_family_unit(world, "Работа")
        _job(world, context_id, world.family_unit, status)

        assert _row(world.db, world.family.id)["unit_busy_jobs"] == 1
        assert "идёт перезапрос: 1 заданий" in _refusal_text(world, world.family_unit)

    def test_pending_and_running_jobs_add_up(self, world):
        for n, status in enumerate(["pending", "pending", "running"]):
            context_id = _ctx_family_unit(world, f"Работа {n}")
            _job(world, context_id, world.family_unit, status)

        assert _row(world.db, world.family.id)["unit_busy_jobs"] == 3
        assert "идёт перезапрос: 3 заданий" in _refusal_text(world, world.family_unit)

    @pytest.mark.parametrize("status", ["done", "error", "cancelled"])
    def test_closed_job_is_not_counted(self, world, status):
        context_id = _ctx_family_unit(world, "Работа")
        _job(world, context_id, world.family_unit, status)

        assert _row(world.db, world.family.id)["unit_busy_jobs"] == 0

    def test_held_batch_touching_the_unit_is_counted(self, world):
        context_id = _ctx_family_unit(world, "Работа")
        _held_batch(world.db, [_fingerprint("family_suggestion", context_id)])

        assert _row(world.db, world.family.id)["unit_busy_jobs"] == 1
        assert "идёт перезапрос: 1 заданий" in _refusal_text(world, world.family_unit)

    def test_held_batch_of_another_kind_is_not_counted(self, world):
        context_id = _ctx_family_unit(world, "Работа")
        _held_batch(world.db, [_fingerprint("context_values", context_id, schema_id=1)])

        assert _row(world.db, world.family.id)["unit_busy_jobs"] == 0

    def test_job_and_held_batch_add_up(self, world):
        running = _ctx_family_unit(world, "Работа А")
        held = _ctx_family_unit(world, "Работа Б")
        _job(world, running, world.family_unit, "pending")
        _held_batch(world.db, [_fingerprint("family_suggestion", held)])

        assert _row(world.db, world.family.id)["unit_busy_jobs"] == 2

    def test_job_of_another_unit_is_not_counted(self, world):
        other_family = _active_family(
            world.db, title="Семья другой единицы", unit_name="M3", actor_id=world.admin.id
        )
        context_id = _ctx_bare_unit(world, "Работа другой единицы")
        _job(world, context_id, world.bare_unit, "pending")

        assert _row(world.db, world.family.id)["unit_busy_jobs"] == 0
        assert _row(world.db, other_family.id)["unit_busy_jobs"] == 1

    def test_held_batch_of_another_unit_is_not_counted(self, world):
        # Удержанная пачка фильтруется по единице отдельно от заданий: свой вход.
        other_family = _active_family(
            world.db, title="Семья другой единицы", unit_name="M3", actor_id=world.admin.id
        )
        context_id = _ctx_bare_unit(world, "Работа другой единицы")
        _held_batch(world.db, [_fingerprint("family_suggestion", context_id)])

        assert _row(world.db, world.family.id)["unit_busy_jobs"] == 0
        assert _row(world.db, other_family.id)["unit_busy_jobs"] == 1

    def test_family_without_a_unit_counts_jobs_without_a_unit_only(self, world):
        no_unit_family = _active_family(
            world.db, title="Семья без единицы", unit_name=None, actor_id=world.admin.id
        )
        context_id, _ = _make(world.db, world.factories, world.proposal, None, "Без единицы")
        _job(world, context_id, None, "pending")
        assert _row(world.db, no_unit_family.id)["unit_busy_jobs"] == 1
        assert _row(world.db, world.family.id)["unit_busy_jobs"] == 0

        other = _ctx_family_unit(world, "Работа единицы")
        _job(world, other, world.family_unit, "pending")
        assert _row(world.db, no_unit_family.id)["unit_busy_jobs"] == 1
        assert _row(world.db, world.family.id)["unit_busy_jobs"] == 1

    def test_single_row_has_the_same_value_as_the_list_row(self, world):
        context_id = _ctx_family_unit(world, "Работа")
        _job(world, context_id, world.family_unit, "pending")

        single = crud_semantic.get_family_row(world.db, family_id=world.family.id)

        assert single["unit_busy_jobs"] == 1
        assert single == _row(world.db, world.family.id)

    def test_route_returns_the_field(self, world, admin_client):
        context_id = _ctx_family_unit(world, "Работа")
        _job(world, context_id, world.family_unit, "pending")

        response = admin_client.get(f"{BASE}/families")

        assert response.status_code == 200
        rows = {r["id"]: r for r in response.json()["items"]}
        assert rows[world.family.id]["unit_busy_jobs"] == 1
