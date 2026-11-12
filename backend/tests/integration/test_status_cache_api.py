"""Кэш сводки очереди на уровне HTTP: срок и сброс запросом на изменение (спека
`2026-11-12-semantic-status-cache-design.md` §2.4 п. 1, §3 проверки 1 и 2).

Срок включается явно (автофикстура `tests/conftest.py` держит 0), часы подменены у
общего `status_cache`, счётчик — обёртка над `heavy_status_parts`, которую маршрут
зовёт через модуль (`crud_semantic_queue`).
"""
from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa

import crud.semantic_queue as crud_semantic_queue
import services.semantic_decisions as semantic_decisions
from config import settings
from models import SemanticWorkerState
from services.status_cache import status_cache
from tests.integration.test_semantic_queue_api import _make_attempt, _make_job, _scene

pytestmark = pytest.mark.integration

BASE = "/api/v1/semantic"
TTL = 300


class _Clock:
    def __init__(self) -> None:
        self.now = 5000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(settings, "SEMANTIC_STATUS_CACHE_TTL_S", TTL)
    monkeypatch.setattr(status_cache, "clock", fake)
    return fake


@pytest.fixture
def heavy_calls(monkeypatch) -> list[int]:
    real = crud_semantic_queue.heavy_status_parts
    calls: list[int] = []

    def counted(db):
        calls.append(1)
        return real(db)

    monkeypatch.setattr(crud_semantic_queue, "heavy_status_parts", counted)
    return calls


def _pause_claim(db_session, factories, admin) -> None:
    scene = _scene(db_session, factories, admin)
    job = _make_job(db_session, scene.context_ids[0], status="done")
    attempt = _make_attempt(db_session, job.id)
    db_session.execute(
        sa.update(SemanticWorkerState)
        .where(SemanticWorkerState.id == 1)
        .values(
            claim_paused=True, paused_reason="reserve_exceeded",
            paused_attempt_id=attempt.id, paused_at=dt.datetime.now(dt.UTC),
        )
    )
    db_session.expire_all()


class TestStatusCacheTtlThroughApi:
    def test_second_get_within_the_ttl_does_not_recompute_but_cheap_fields_are_fresh(
        self, admin_client, db_session, factories, clock, heavy_calls
    ):
        first = admin_client.get(f"{BASE}/status")
        _pause_claim(db_session, factories, admin_client.user)  # через crud, мимо HTTP: сброса кэша нет
        second = admin_client.get(f"{BASE}/status")

        assert first.status_code == second.status_code == 200
        assert len(heavy_calls) == 1
        assert first.json()["claim_paused"] is None
        assert second.json()["claim_paused"]["reason"] == "reserve_exceeded"

    def test_get_after_the_ttl_recomputes(self, admin_client, clock, heavy_calls):
        admin_client.get(f"{BASE}/status")
        clock.now += TTL

        admin_client.get(f"{BASE}/status")

        assert len(heavy_calls) == 2

    def test_zero_ttl_recomputes_on_every_get(self, admin_client, heavy_calls):
        # TTL = 0 ставит автофикстура; здесь срок намеренно не включён.
        admin_client.get(f"{BASE}/status")
        admin_client.get(f"{BASE}/status")

        assert len(heavy_calls) == 2

    def test_response_has_the_same_keys_and_data_with_and_without_the_cache(
        self, admin_client, monkeypatch, clock
    ):
        cached = admin_client.get(f"{BASE}/status").json()
        monkeypatch.setattr(settings, "SEMANTIC_STATUS_CACHE_TTL_S", 0)
        plain = admin_client.get(f"{BASE}/status").json()

        assert set(cached) == set(plain)
        assert cached == plain


def _boom(*args, **kwargs):
    raise RuntimeError("обработчик упал")


class TestStatusCacheResetByWriteRequest:
    def _warm(self, admin_client, heavy_calls) -> None:
        admin_client.get(f"{BASE}/status")
        admin_client.get(f"{BASE}/status")
        assert len(heavy_calls) == 1  # предусловие: кэш держит значение

    def test_post_with_2xx_resets(self, admin_client, clock, heavy_calls):
        self._warm(admin_client, heavy_calls)

        assert admin_client.post(f"{BASE}/worker/resume").status_code == 200
        admin_client.get(f"{BASE}/status")

        assert len(heavy_calls) == 2

    def test_post_with_4xx_resets(self, admin_client, clock, heavy_calls):
        self._warm(admin_client, heavy_calls)

        response = admin_client.post(f"{BASE}/suggestions/confirm", json=[1])
        assert response.status_code == 422
        admin_client.get(f"{BASE}/status")

        assert len(heavy_calls) == 2

    def test_post_with_an_exception_in_the_handler_resets(
        self, admin_client, clock, heavy_calls, monkeypatch
    ):
        self._warm(admin_client, heavy_calls)
        monkeypatch.setattr(semantic_decisions, "resume_worker", _boom)

        with pytest.raises(RuntimeError, match="обработчик упал"):
            admin_client.post(f"{BASE}/worker/resume")
        admin_client.get(f"{BASE}/status")

        assert len(heavy_calls) == 2

    def test_get_of_another_route_does_not_reset(self, admin_client, clock, heavy_calls):
        self._warm(admin_client, heavy_calls)

        assert admin_client.get(f"{BASE}/jobs?status=error").status_code == 200
        admin_client.get(f"{BASE}/status")

        assert len(heavy_calls) == 1
