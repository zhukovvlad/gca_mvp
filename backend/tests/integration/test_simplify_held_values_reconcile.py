"""Удержанное по приватности задание значений, чей отпечаток сдвинулся из-за смены
текста промпта, при следующей сверке закрывается как `stale_hold`, а вместо него
ставится новое задание текущего отпечатка (спека семантических предложений
§2.11; то же делает шаг 2 `catalog-simplify-backfill`, он зовёт
`reconcile_context_values`).
"""
from __future__ import annotations

import pytest

from services.semantic_reconcile import NO_CAP, reconcile_context_values, reconcile_or_defer
from tests.integration.test_work_variants_schema_life import _active_world, _jobs

pytestmark = pytest.mark.integration


def test_a_held_values_job_with_an_old_request_hash_is_replaced_on_reconcile(
    db_session, factories
):
    world = _active_world(db_session, factories)
    reconcile_or_defer(db_session, [world.context_id])
    [first] = _jobs(db_session, "context_values")
    current_hash = first.request_hash
    first.status = "privacy_hold"
    first.request_hash = "0" * 64
    first.prompt_version = "1"
    first.privacy_matches = [{"match": "x"}]
    db_session.flush()

    report = reconcile_context_values(
        db_session, [world.context_id], cap=NO_CAP, source="operation"
    )

    jobs = _jobs(db_session, "context_values")
    assert [(j.status, j.cancel_reason) for j in jobs if j.id == first.id] == [
        ("cancelled", "stale_hold")
    ]
    fresh = [j for j in jobs if j.id != first.id]
    assert [(j.status, j.request_hash) for j in fresh] == [("pending", current_hash)]
    assert report.created == 1


def test_a_held_values_job_with_the_current_request_hash_stays_held(db_session, factories):
    world = _active_world(db_session, factories)
    reconcile_or_defer(db_session, [world.context_id])
    [first] = _jobs(db_session, "context_values")
    first.status = "privacy_hold"
    first.privacy_matches = [{"match": "x"}]
    db_session.flush()

    report = reconcile_context_values(
        db_session, [world.context_id], cap=NO_CAP, source="operation"
    )

    jobs = _jobs(db_session, "context_values")
    assert [(j.id, j.status) for j in jobs] == [(first.id, "privacy_hold")]
    assert report.created == 0
