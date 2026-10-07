"""API и чтение для экрана «Варианты и промоушен» — маршруты `/api/v1/semantic`
из спеки `2026-10-02-catalog-variants-design.md` §2.12 (схема семьи и варианты,
смена семьи, «не работа», глобальная пометка строки, автопринятие, очередь
«Смена семьи», счётчики шапки, фильтры контекстов, карточка).

Сами сервисы здесь заново не проверяются (`test_work_variants_*.py`) — только
HTTP-слой: права на КАЖДОМ маршруте, форма ответов, трансляция КАЖДОГО отказа
(ни один не уходит `500`), чтение. Помощники сцен приходят из наборов сервисов.
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

import pytest
import sqlalchemy as sa

import routers.semantic as semantic_router
import services.context_operations as context_operations_module
import services.family_change as family_change_module
import services.review as review_module
import services.semantic_decisions as decisions_module
import services.work_families as work_families_module
import services.work_variants as work_variants_module
from config import settings as app_settings
from models import (
    CatalogContext,
    CatalogPosition,
    ContextParameterValue,
    FamilyParameterSchema,
    SemanticJob,
    SemanticReconcileBatch,
    WorkVariantValue,
)
from services.family_change import FamilyLockMismatch
from services.work_variants import rebuild_schema, values_key_of
from tests.integration.test_semantic_queue_decisions import (
    _active_family,
    _make_job,
    _make_stale,
    _scene,
    _unit_id,
)
from tests.integration.test_work_variants_auto_accept import (
    THRESHOLD,
    _deploy_scene,
    _state,
)
from tests.integration.test_work_variants_core import _attach_variant, _set_pending, _world
from tests.integration.test_work_variants_family_change import (
    _bind_source,
    _current_schema,
    _publish,
    _two_families,
    _unpublish,
    _with_variant,
)
from tests.integration.test_work_variants_material import _uid
from tests.integration.test_work_variants_position_kind import _human_position, _standard
from tests.integration.test_work_variants_reconcile import _active_family as _schemaless_family
from tests.integration.test_work_variants_schema import _family as _draft_family
from tests.integration.test_work_variants_schema import _param, _value, _variant

pytestmark = pytest.mark.integration

BASE = "/api/v1/semantic"

#: Маршруты экрана «Варианты и промоушен» с подставленными id — НЕЗАВИСИМО от
#: `app.routes` (тот же приём, что `QUEUE_ROUTES` в `test_semantic_queue_api.py`):
#: забытый `require_admin` на одном маршруте ловится перебором, а не общей
#: защитой роутера.
VARIANT_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", f"{BASE}/families/1/schema"),
    ("POST", f"{BASE}/families/1/schema/rebuild/preview"),
    ("POST", f"{BASE}/families/1/schema/rebuild"),
    ("PATCH", f"{BASE}/families/1/schema"),
    ("POST", f"{BASE}/families/1/schema/cancel"),
    ("POST", f"{BASE}/families/1/schema/values/merge"),
    ("GET", f"{BASE}/families/1/variants"),
    ("DELETE", f"{BASE}/contexts/1/pending-family"),
    ("POST", f"{BASE}/contexts/1/not-work"),
    ("POST", f"{BASE}/positions/1/kind"),
    ("POST", f"{BASE}/auto-accept/preview"),
    ("POST", f"{BASE}/auto-accept"),
    ("GET", f"{BASE}/suggestions?queue=change"),
    ("POST", f"{BASE}/contexts/1/family"),
)
assert len(VARIANT_ROUTES) == 14

_PARAMS = ((1, "Толщина", ("50 мм", "100 мм")), (2, "Материал", ("бетон", "кирпич")))


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _money(text: str) -> Decimal:
    assert re.fullmatch(r"\d+(\.\d+)?", text), f"деньги строкой без экспоненты: {text!r}"
    return Decimal(text)


def _family_with_params(db, factories, titles=("Устройство пола", "Стяжка пола")):
    """Сцена очереди: активная семья A с текущей схемой из двух параметров и
    контексты семьи (привязаны человеком, без вариантов)."""
    scene = _scene(db, factories, titles=titles)
    schema = _current_schema(db, scene.family)
    for ordinal, name, values in _PARAMS:
        parameter = _param(db, schema, ordinal, name)
        for value in values:
            _value(db, parameter, value)
    for context_id in scene.context_ids:
        _bind_source(db, scene, context_id, scene.family)
    scene.schema = schema
    return scene


def _building(db, family):
    schema = FamilyParameterSchema(
        family_id=family.id, version=2, status="building", origin="model"
    )
    db.add(schema)
    db.flush()
    return schema


def _values_job(db, context_id, schema_id, status, *, kind="context_values") -> SemanticJob:
    """Задание значений контекста по версии схемы (или иного вида) в нужном
    статусе, с полями, которых требуют CHECK статуса."""
    import uuid

    extra = {}
    if status == "running":
        extra["claim_token"] = uuid.uuid4()
    if status == "privacy_hold":
        extra["privacy_matches"] = [{"text": "x", "kind": "name", "where": "context"}]
    if status == "cancelled":
        extra["cancel_reason"] = "input_changed"
    job = SemanticJob(
        kind=kind, context_id=context_id, schema_id=schema_id,
        request_hash=f"hash-{kind}-{status}-{uuid.uuid4().hex}", status=status,
        next_attempt_at=dt.datetime.now(dt.UTC), prompt_version="1",
        model_requested=app_settings.SEMANTIC_MODEL, place_dictionary_version=1,
        candidates_hash="c", prefix_hash="p", input_hash="i", response_schema_version="1",
        serialization_version="1", **extra,
    )
    db.add(job)
    db.flush()
    return job


def _detail(response) -> dict:
    body = response.json()
    assert isinstance(body["detail"], dict), body
    return body["detail"]


def _jobs(db, kind, **where):
    db.expire_all()
    stmt = sa.select(SemanticJob).where(SemanticJob.kind == kind).order_by(SemanticJob.id)
    for column, value in where.items():
        stmt = stmt.where(getattr(SemanticJob, column) == value)
    return list(db.execute(stmt).scalars().all())


def _change_queue(client, **params):
    response = client.get(f"{BASE}/suggestions", params={"queue": "change", **params})
    assert response.status_code == 200, response.text
    return response.json()


def _change_context_ids(client, **params) -> set[int]:
    body = _change_queue(client, **params)
    return {row["context_id"] for group in body["groups"] for row in group["rows"]}


# ---------------------------------------------------------------------------
#  Права: КАЖДЫЙ маршрут
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method,path", VARIANT_ROUTES)
def test_member_rejected_on_every_route(method, path, member_client):
    response = member_client.request(method, path)
    assert response.status_code == 403, f"{method} {path} -> {response.status_code}"


@pytest.mark.parametrize("method,path", VARIANT_ROUTES)
def test_unauthenticated_rejected_on_every_route(method, path, anon_client):
    response = anon_client.request(method, path)
    assert response.status_code in (401, 403), f"{method} {path} -> {response.status_code}"


# ---------------------------------------------------------------------------
#  Отказы: каждый код отказа имеет статус, и он не 500
# ---------------------------------------------------------------------------

#: Статусы кодов новых сервисов — независимый литерал: состояние мешает — 409,
#: неверный ввод — 422, объекта нет — 404.
_EXPECTED_STATUS = {
    "merge_schema_building": 409,
    "schema_no_building": 409,
    "schema_building": 409,
    "schema_no_current": 409,
    "merge_source_merged": 409,
    "position_not_position": 409,
    "position_has_standards": 409,
    "schema_parameter_renamed": 422,
    "schema_value_removed": 422,
    "schema_blank": 422,
    "schema_bad_ordinals": 422,
    "merge_values_other_parameter": 422,
    "merge_value_cycle": 422,
    "parameter_not_found": 404,
    "value_not_found": 404,
    "position_not_found": 404,
}


def _refusal_codes() -> dict[str, str]:
    codes: dict[str, str] = {}
    for module in (
        work_families_module,
        work_variants_module,
        review_module,
        context_operations_module,
    ):
        for name, value in vars(module).items():
            if name.startswith("REFUSE_") and isinstance(value, str):
                codes[value] = f"{module.__name__}.{name}"
    return codes


@pytest.mark.parametrize("code", sorted(_refusal_codes()))
def test_every_refusal_code_has_an_http_status(code):
    """Код отказа сервиса, которого нет ни в одной из трёх карт статусов, уходил
    `500` (`AssertionError` в `_status_for_code`)."""
    assert semantic_router._status_for_code(code) in (404, 409, 422), _refusal_codes()[code]


@pytest.mark.parametrize(("code", "expected"), sorted(_EXPECTED_STATUS.items()))
def test_refusal_codes_of_the_new_services_map_to_the_agreed_status(code, expected):
    assert code in _refusal_codes()
    assert semantic_router._status_for_code(code) == expected


# ---------------------------------------------------------------------------
#  FamilyLockMismatch -> 409 на всех маршрутах, где он может всплыть
# ---------------------------------------------------------------------------

def _raise_mismatch(*_args, **_kwargs):
    raise FamilyLockMismatch([1])


_MISMATCH_ROUTES = (
    pytest.param(
        family_change_module, "request_family_change", "POST", "/contexts/1/family",
        {"family_id": 1}, id="family",
    ),
    pytest.param(
        family_change_module, "cancel_pending_family", "DELETE", "/contexts/1/pending-family",
        None, id="pending-family",
    ),
    pytest.param(
        work_variants_module, "mark_context_not_work", "POST", "/contexts/1/not-work",
        None, id="not-work",
    ),
    pytest.param(
        review_module, "set_position_kind_global", "POST", "/positions/1/kind",
        {"kind": "HEADER"}, id="position-kind",
    ),
    pytest.param(
        decisions_module, "confirm_suggestions", "POST", "/suggestions/confirm",
        {"suggestion_ids": [1]}, id="suggestion-confirm",
    ),
    pytest.param(
        decisions_module, "reject_suggestion", "POST", "/suggestions/1/reject",
        None, id="suggestion-reject",
    ),
    pytest.param(
        decisions_module, "assign_other_family", "POST", "/suggestions/1/other-family",
        {"family_id": 1}, id="suggestion-other-family",
    ),
    pytest.param(
        decisions_module, "create_family_from_suggestion", "POST",
        "/suggestions/1/create-family", {"title": "Т", "definition": "О"},
        id="suggestion-create-family",
    ),
    pytest.param(
        family_change_module, "apply_auto_accept", "POST", "/auto-accept",
        {"preview_hash": "x"}, id="auto-accept",
    ),
)


@pytest.mark.parametrize(("module", "attribute", "method", "path", "body"), _MISMATCH_ROUTES)
def test_family_lock_mismatch_is_409_not_500(
    module, attribute, method, path, body, admin_client, monkeypatch
):
    monkeypatch.setattr(module, attribute, _raise_mismatch)

    response = admin_client.request(method, f"{BASE}{path}", json=body)

    assert response.status_code == 409, response.text
    assert _detail(response)["code"] == "family_lock_mismatch"


def test_review_merge_family_lock_mismatch_is_409(admin_client, db_session, factories, monkeypatch):
    import routers.review as review_router

    source = factories.CatalogPositionFactory.create(kind="TO_REVIEW")
    target = factories.CatalogPositionFactory.create(kind="POSITION")
    monkeypatch.setattr(review_router, "merge_into_position", _raise_mismatch)

    response = admin_client.post(
        f"/api/v1/review/{source.id}/merge", json={"target_id": target.id}
    )

    assert response.status_code == 409, response.text


def test_review_kind_family_lock_mismatch_is_409(admin_client, factories, monkeypatch):
    import routers.review as review_router

    row = factories.CatalogPositionFactory.create(kind="TO_REVIEW")
    monkeypatch.setattr(review_router, "set_kind", _raise_mismatch)

    response = admin_client.post(f"/api/v1/review/{row.id}/kind", json={"kind": "HEADER"})

    assert response.status_code == 409, response.text


def test_review_batch_kind_family_lock_mismatch_is_409(admin_client, factories, monkeypatch):
    import routers.review as review_router

    rows = [factories.CatalogPositionFactory.create(kind="TO_REVIEW") for _ in range(2)]
    monkeypatch.setattr(review_router, "set_kind", _raise_mismatch)

    response = admin_client.post(
        "/api/v1/review/batch-kind", json={"ids": [r.id for r in rows], "kind": "HEADER"}
    )

    assert response.status_code == 409, response.text


# ---------------------------------------------------------------------------
#  Слияние семей: идущая пересборка — 409
# ---------------------------------------------------------------------------

class TestFamilyMergeWithBuilding:
    def test_building_source_is_409_with_the_code(self, admin_client, db_session, factories):
        scene = _two_families(db_session, factories)
        _building(db_session, scene.family)

        response = admin_client.post(
            f"{BASE}/families/{scene.family.id}/merge",
            json={"target_family_id": scene.family_b.id},
        )

        assert response.status_code == 409, response.text
        detail = _detail(response)
        assert detail["code"] == "merge_schema_building"
        assert detail["family_id"] == scene.family.id

    def test_building_target_is_409_with_the_code(self, admin_client, db_session, factories):
        scene = _two_families(db_session, factories)
        _building(db_session, scene.family_b)

        response = admin_client.post(
            f"{BASE}/families/{scene.family.id}/merge",
            json={"target_family_id": scene.family_b.id},
        )

        assert response.status_code == 409, response.text
        assert _detail(response)["family_id"] == scene.family_b.id

    def test_without_building_the_merge_goes_through(self, admin_client, db_session, factories):
        scene = _two_families(db_session, factories)

        response = admin_client.post(
            f"{BASE}/families/{scene.family.id}/merge",
            json={"target_family_id": scene.family_b.id},
        )

        assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
#  Схема семьи: чтение
# ---------------------------------------------------------------------------

class TestSchemaRead:
    def test_shape_and_order(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)

        response = admin_client.get(f"{BASE}/families/{scene.family.id}/schema")

        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {
            "family_id", "status", "version", "ready_to_build", "building",
            "values_jobs_live", "parameters",
        }
        assert body["family_id"] == scene.family.id
        assert (body["status"], body["version"], body["building"]) == ("frozen", 1, False)
        assert [p["ordinal"] for p in body["parameters"]] == [1, 2]
        assert [p["name"] for p in body["parameters"]] == ["Толщина", "Материал"]
        assert [[v["value"] for v in p["values"]] for p in body["parameters"]] == [
            ["50 мм", "100 мм"],
            ["бетон", "кирпич"],
        ]
        value = body["parameters"][0]["values"][0]
        assert set(value) == {"id", "value", "origin", "merged_into_id"}
        assert value["origin"] == "schema"
        assert value["merged_into_id"] is None

    def test_merged_value_is_listed_with_its_target(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)
        body = admin_client.get(f"{BASE}/families/{scene.family.id}/schema").json()
        first, second = body["parameters"][0]["values"]
        db_session.execute(
            sa.text("UPDATE family_parameter_values SET merged_into_id = :t WHERE id = :s"),
            {"t": first["id"], "s": second["id"]},
        )
        db_session.expire_all()

        values = admin_client.get(f"{BASE}/families/{scene.family.id}/schema").json()[
            "parameters"
        ][0]["values"]

        assert [(v["value"], v["merged_into_id"]) for v in values] == [
            ("50 мм", None),
            ("100 мм", first["id"]),
        ]

    def test_family_without_current_version(self, admin_client, db_session, factories):
        family = _schemaless_family(db_session)

        body = admin_client.get(f"{BASE}/families/{family.id}/schema").json()

        assert (body["status"], body["version"], body["parameters"], body["building"]) == (
            None, None, [], False,
        )

    def test_building_version_is_flagged_and_the_shown_one_stays(
        self, admin_client, db_session, factories
    ):
        scene = _family_with_params(db_session, factories)
        _building(db_session, scene.family)

        body = admin_client.get(f"{BASE}/families/{scene.family.id}/schema").json()

        assert body["building"] is True
        assert (body["status"], body["version"]) == ("frozen", 1)
        assert len(body["parameters"]) == 2

    def test_shown_version_is_the_frozen_one_whatever_the_row_order(
        self, admin_client, db_session, factories
    ):
        """Версия `building` записана РАНЬШЕ текущей: показ выбирает `frozen` по
        статусу, а не первой строкой выборки."""
        family = _schemaless_family(db_session)
        _building(db_session, family)
        db_session.add(
            FamilyParameterSchema(
                family_id=family.id, version=1, status="frozen", origin="model",
                frozen_at=dt.datetime.now(dt.UTC),
            )
        )
        db_session.flush()

        body = admin_client.get(f"{BASE}/families/{family.id}/schema").json()

        assert (body["status"], body["version"], body["building"]) == ("frozen", 1, True)

    def test_ready_to_build_follows_the_unit_requeue(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)
        path = f"{BASE}/families/{scene.family.id}/schema"
        assert admin_client.get(path).json()["ready_to_build"] is True

        _make_job(
            db_session, scene.context_ids[0], status="pending", unit_id=scene.unit_id
        )

        assert admin_client.get(path).json()["ready_to_build"] is False

    def test_values_jobs_live_counts_open_values_jobs_of_the_current_schema(
        self, admin_client, db_session, factories
    ):
        scene = _family_with_params(db_session, factories)
        path = f"{BASE}/families/{scene.family.id}/schema"
        assert admin_client.get(path).json()["values_jobs_live"] == 0

        _values_job(db_session, scene.context_ids[0], scene.schema.id, "pending")
        _values_job(db_session, scene.context_ids[1], scene.schema.id, "running")

        assert admin_client.get(path).json()["values_jobs_live"] == 2

    @pytest.mark.parametrize("status", ["done", "cancelled", "error", "privacy_hold"])
    def test_values_jobs_live_ignores_jobs_that_are_not_pending_or_running(
        self, admin_client, db_session, factories, status
    ):
        scene = _family_with_params(db_session, factories)
        _values_job(db_session, scene.context_ids[0], scene.schema.id, "pending")
        _values_job(db_session, scene.context_ids[1], scene.schema.id, status)

        body = admin_client.get(f"{BASE}/families/{scene.family.id}/schema").json()

        assert body["values_jobs_live"] == 1

    def test_values_jobs_live_ignores_other_families_and_other_kinds(
        self, admin_client, db_session, factories
    ):
        scene = _family_with_params(db_session, factories)
        other = _active_family(
            db_session, title="Другая семья", unit_name="M2", actor_id=scene.user.id
        )
        _values_job(db_session, scene.context_ids[0], scene.schema.id, "pending")
        _values_job(
            db_session, scene.context_ids[1], _current_schema(db_session, other).id, "pending"
        )
        _values_job(db_session, scene.context_ids[1], None, "pending", kind="family_suggestion")

        body = admin_client.get(f"{BASE}/families/{scene.family.id}/schema").json()

        assert body["values_jobs_live"] == 1

    def test_values_jobs_live_is_zero_without_a_current_version(
        self, admin_client, db_session, factories
    ):
        family = _schemaless_family(db_session)

        body = admin_client.get(f"{BASE}/families/{family.id}/schema").json()

        assert body["values_jobs_live"] == 0

    def test_unknown_family_is_404_with_the_code(self, admin_client):
        response = admin_client.get(f"{BASE}/families/999999999/schema")

        assert response.status_code == 404
        assert _detail(response)["code"] == "family_not_found"


# ---------------------------------------------------------------------------
#  Варианты семьи
# ---------------------------------------------------------------------------

def _variant_with_values(db, family, schema, texts):
    """Вариант с построчными значениями: `texts` — тексты по порядку параметров
    (`None` — «не уточнено»)."""
    variant = _variant(db, family, schema, values_key=f"k-{_uid()}")
    parameters = db.execute(
        sa.text(
            "SELECT id, ordinal FROM family_parameters WHERE schema_id = :s ORDER BY ordinal"
        ),
        {"s": schema.id},
    ).all()
    for (parameter_id, _ordinal), text in zip(parameters, texts, strict=True):
        value_id = None
        if text is not None:
            value_id = db.execute(
                sa.text(
                    "SELECT id FROM family_parameter_values "
                    "WHERE parameter_id = :p AND value = :v"
                ),
                {"p": parameter_id, "v": text},
            ).scalar_one()
        db.add(
            WorkVariantValue(
                variant_id=variant.id, schema_id=schema.id, parameter_id=parameter_id,
                value_id=value_id,
            )
        )
    db.flush()
    return variant


class TestVariantsRead:
    def test_values_contexts_and_status_per_variant(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories, titles=("Пол 1", "Пол 2", "Пол 3"))
        first = _variant_with_values(db_session, scene.family, scene.schema, ["50 мм", "бетон"])
        second = _variant_with_values(db_session, scene.family, scene.schema, [None, "кирпич"])
        db_session.execute(
            sa.text(
                "UPDATE work_variants SET status = 'archived', archived_at = now() WHERE id = :v"
            ),
            {"v": second.id},
        )
        for context_id in scene.context_ids[:2]:
            _attach_variant(db_session, context_id, first)

        response = admin_client.get(f"{BASE}/families/{scene.family.id}/variants")

        assert response.status_code == 200, response.text
        assert response.json() == [
            {"id": first.id, "values": ["50 мм", "бетон"], "contexts": 2, "status": "active"},
            {"id": second.id, "values": [None, "кирпич"], "contexts": 0, "status": "archived"},
        ]

    def test_archived_contexts_are_not_counted(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories, titles=("Пол 1", "Пол 2"))
        variant = _variant_with_values(db_session, scene.family, scene.schema, ["50 мм", "бетон"])
        for context_id in scene.context_ids:
            _attach_variant(db_session, context_id, variant)
        db_session.execute(
            sa.update(CatalogContext)
            .where(CatalogContext.id == scene.context_ids[1])
            .values(archived_at=dt.datetime.now(dt.UTC))
        )

        [listed] = admin_client.get(f"{BASE}/families/{scene.family.id}/variants").json()

        assert listed["contexts"] == 1

    def test_family_without_variants_is_an_empty_list(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)

        response = admin_client.get(f"{BASE}/families/{scene.family.id}/variants")

        assert response.status_code == 200
        assert response.json() == []

    def test_other_family_variants_are_not_listed(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)
        other = _active_other_family(db_session, factories)
        _variant_with_values(db_session, scene.family, scene.schema, ["50 мм", "бетон"])

        response = admin_client.get(f"{BASE}/families/{other.id}/variants")

        assert response.json() == []

    def test_unknown_family_is_404(self, admin_client):
        response = admin_client.get(f"{BASE}/families/999999999/variants")

        assert response.status_code == 404
        assert _detail(response)["code"] == "family_not_found"


# ---------------------------------------------------------------------------
#  Пересборка схемы
# ---------------------------------------------------------------------------

def _tariffs(monkeypatch, *, schema_output, schema_tokens, values_output, values_tokens):
    """Тарифы, при которых резерв считается в уме: всё, кроме выхода, — ноль."""
    zero = Decimal("0")
    for prefix, output, tokens in (
        ("SCHEMA_", schema_output, schema_tokens),
        ("VALUES_", values_output, values_tokens),
    ):
        for suffix in ("INPUT", "CACHE_WRITE", "CACHE_READ"):
            monkeypatch.setattr(app_settings, f"SEMANTIC_{prefix}PRICE_{suffix}_PER_M", zero)
        monkeypatch.setattr(app_settings, f"SEMANTIC_{prefix}PRICE_OUTPUT_PER_M", output)
        monkeypatch.setattr(app_settings, f"SEMANTIC_{prefix}MAX_TOKENS", tokens)


class TestRebuildPreview:
    def test_shape_and_money_as_strings(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)

        response = admin_client.post(f"{BASE}/families/{scene.family.id}/schema/rebuild/preview")

        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {
            "family_id", "context_count", "reserve_usd", "expected_cached_usd", "preview_hash",
            "values_included",
        }
        assert body["family_id"] == scene.family.id
        assert body["context_count"] == 2
        assert _money(body["reserve_usd"]) > 0
        assert _money(body["expected_cached_usd"]) > 0
        assert re.fullmatch(r"[0-9a-f]{64}", body["preview_hash"])

    def test_cost_is_schema_job_plus_a_values_job_per_context(
        self, admin_client, db_session, factories, monkeypatch
    ):
        scene = _family_with_params(db_session, factories)
        _tariffs(
            monkeypatch, schema_output=Decimal("10"), schema_tokens=2000,
            values_output=Decimal("5"), values_tokens=1000,
        )

        body = admin_client.post(
            f"{BASE}/families/{scene.family.id}/schema/rebuild/preview"
        ).json()

        # 2000 токенов по 10 $/млн + два контекста по 1000 токенов по 5 $/млн.
        assert _money(body["reserve_usd"]) == Decimal("0.03")
        assert _money(body["expected_cached_usd"]) == Decimal("0.03")

    def test_context_waiting_for_another_family_is_counted_for_that_family(
        self, admin_client, db_session, factories
    ):
        """Значения контекста ставит схема ожидаемой семьи, поэтому в оценку
        пересборки идёт контекст, у которого она (или текущая, когда ожидания
        нет), а не оба сразу."""
        scene = _family_with_params(db_session, factories)
        other = _active_other_family(db_session, factories)
        _set_pending(db_session, factories, scene.context_ids[0], other, source="manual")

        counts = {
            family.id: admin_client.post(
                f"{BASE}/families/{family.id}/schema/rebuild/preview"
            ).json()["context_count"]
            for family in (scene.family, other)
        }

        assert counts == {scene.family.id: 1, other.id: 1}

    def test_values_jobs_are_flagged_as_included_with_a_current_schema(
        self, admin_client, db_session, factories
    ):
        scene = _family_with_params(db_session, factories)

        body = admin_client.post(
            f"{BASE}/families/{scene.family.id}/schema/rebuild/preview"
        ).json()

        assert body["values_included"] is True

    def test_family_without_a_current_schema_is_flagged_as_a_partial_sum(
        self, admin_client, db_session, factories
    ):
        family = _schemaless_family(db_session)

        body = admin_client.post(f"{BASE}/families/{family.id}/schema/rebuild/preview").json()

        assert body["values_included"] is False

    def test_family_without_contexts_costs_the_schema_job_only(
        self, admin_client, db_session, factories, monkeypatch
    ):
        scene = _two_families(db_session, factories)
        _tariffs(
            monkeypatch, schema_output=Decimal("10"), schema_tokens=2000,
            values_output=Decimal("5"), values_tokens=1000,
        )

        body = admin_client.post(
            f"{BASE}/families/{scene.family_b.id}/schema/rebuild/preview"
        ).json()

        assert body["context_count"] == 0
        assert _money(body["reserve_usd"]) == Decimal("0.02")

    def test_hash_is_stable_and_follows_each_input(
        self, admin_client, db_session, factories, monkeypatch
    ):
        scene = _family_with_params(db_session, factories, titles=("Пол 1", "Пол 2", "Пол 3"))
        spare = scene.context_ids[2]
        db_session.execute(
            sa.update(CatalogContext)
            .where(CatalogContext.id == spare)
            .values(work_family_id=None, family_source=None, family_by=None, family_at=None)
        )
        path = f"{BASE}/families/{scene.family.id}/schema/rebuild/preview"

        def preview_hash() -> str:
            db_session.expire_all()
            return admin_client.post(path).json()["preview_hash"]

        base = preview_hash()
        assert preview_hash() == base

        _bind_source(db_session, scene, spare, scene.family)
        with_context = preview_hash()
        assert with_context != base

        _building(db_session, scene.family)
        with_building = preview_hash()
        assert with_building != with_context

        monkeypatch.setattr(
            app_settings, "SEMANTIC_VALUES_PRICE_OUTPUT_PER_M",
            app_settings.SEMANTIC_VALUES_PRICE_OUTPUT_PER_M + Decimal("1"),
        )
        assert preview_hash() != with_building

    # Каждый вход хэша — своим входом: меняется ровно он, остальные стоят.

    def _zero_tariffs(self, monkeypatch):
        _tariffs(
            monkeypatch, schema_output=Decimal("0"), schema_tokens=2000,
            values_output=Decimal("0"), values_tokens=1000,
        )

    def _preview(self, client, db, family_id) -> dict:
        db.expire_all()
        return client.post(f"{BASE}/families/{family_id}/schema/rebuild/preview").json()

    def test_hash_follows_the_context_count_alone(
        self, admin_client, db_session, factories, monkeypatch
    ):
        scene = _family_with_params(db_session, factories, titles=("Пол 1", "Пол 2", "Пол 3"))
        spare = scene.context_ids[2]
        db_session.execute(
            sa.update(CatalogContext)
            .where(CatalogContext.id == spare)
            .values(work_family_id=None, family_source=None, family_by=None, family_at=None)
        )
        self._zero_tariffs(monkeypatch)
        before = self._preview(admin_client, db_session, scene.family.id)

        _bind_source(db_session, scene, spare, scene.family)
        after = self._preview(admin_client, db_session, scene.family.id)

        assert (before["reserve_usd"], before["expected_cached_usd"]) == (
            after["reserve_usd"], after["expected_cached_usd"],
        )
        assert (before["context_count"], after["context_count"]) == (2, 3)
        assert before["preview_hash"] != after["preview_hash"]

    def test_hash_follows_the_tariffs_alone(
        self, admin_client, db_session, factories, monkeypatch
    ):
        """У семьи без контекстов тариф заданий значений сумм не меняет — хэш
        меняется только через снимок тарифов."""
        scene = _two_families(db_session, factories)
        before = self._preview(admin_client, db_session, scene.family_b.id)

        monkeypatch.setattr(
            app_settings, "SEMANTIC_VALUES_PRICE_OUTPUT_PER_M",
            app_settings.SEMANTIC_VALUES_PRICE_OUTPUT_PER_M + Decimal("1"),
        )
        after = self._preview(admin_client, db_session, scene.family_b.id)

        assert before["context_count"] == after["context_count"] == 0
        assert before["reserve_usd"] == after["reserve_usd"]
        assert before["preview_hash"] != after["preview_hash"]

    def test_hash_follows_the_current_version_alone(
        self, admin_client, db_session, factories, monkeypatch
    ):
        scene = _family_with_params(db_session, factories)
        self._zero_tariffs(monkeypatch)
        before = self._preview(admin_client, db_session, scene.family.id)

        edited = admin_client.patch(
            f"{BASE}/families/{scene.family.id}/schema",
            json=_edit(
                (1, "Толщина", ("50 мм", "100 мм", "150 мм")), (2, "Материал", ("бетон", "кирпич"))
            ),
        )
        assert edited.json()["version"] == 2
        after = self._preview(admin_client, db_session, scene.family.id)

        assert (before["reserve_usd"], before["context_count"]) == (
            after["reserve_usd"], after["context_count"],
        )
        assert before["preview_hash"] != after["preview_hash"]

    def test_hash_follows_the_reserve_formula_version(
        self, admin_client, db_session, factories, monkeypatch
    ):
        import crud.work_variants as crud_work_variants_module

        scene = _family_with_params(db_session, factories)
        before = self._preview(admin_client, db_session, scene.family.id)

        monkeypatch.setattr(
            crud_work_variants_module, "RESERVE_FORMULA_VERSION",
            f"{crud_work_variants_module.RESERVE_FORMULA_VERSION}-next",
        )
        after = self._preview(admin_client, db_session, scene.family.id)

        assert before["reserve_usd"] == after["reserve_usd"]
        assert before["preview_hash"] != after["preview_hash"]

    def test_hash_follows_the_sums_alone(self, admin_client, db_session, factories):
        """Число контекстов, версия, `building` и тарифы те же; длиннее стало
        наименование строки контекста — выросла оценка, и хэш обязан это
        увидеть."""
        scene = _family_with_params(db_session, factories)
        before = self._preview(admin_client, db_session, scene.family.id)

        db_session.execute(
            sa.text(
                "UPDATE catalog_positions SET standard_job_title = :t WHERE id = ("
                " SELECT b.catalog_position_id FROM context_buckets b"
                " JOIN catalog_contexts c ON c.bucket_id = b.id WHERE c.id = :c)"
            ),
            {"t": "Устройство пола " + "очень длинное наименование " * 20, "c": scene.context_ids[0]},
        )
        after = self._preview(admin_client, db_session, scene.family.id)

        assert before["context_count"] == after["context_count"]
        assert _money(after["reserve_usd"]) > _money(before["reserve_usd"])
        assert before["preview_hash"] != after["preview_hash"]

    def test_archived_context_is_not_estimated(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)
        before = self._preview(admin_client, db_session, scene.family.id)

        db_session.execute(
            sa.update(CatalogContext)
            .where(CatalogContext.id == scene.context_ids[0])
            .values(archived_at=dt.datetime.now(dt.UTC))
        )
        after = self._preview(admin_client, db_session, scene.family.id)

        assert (before["context_count"], after["context_count"]) == (2, 1)

    def test_unknown_family_is_404(self, admin_client):
        response = admin_client.post(f"{BASE}/families/999999999/schema/rebuild/preview")

        assert response.status_code == 404
        assert _detail(response)["code"] == "family_not_found"


class TestRebuild:
    def _hash(self, client, family_id) -> str:
        return client.post(f"{BASE}/families/{family_id}/schema/rebuild/preview").json()[
            "preview_hash"
        ]

    def test_confirmed_hash_creates_the_version_and_the_job(
        self, admin_client, db_session, factories
    ):
        scene = _family_with_params(db_session, factories)
        preview_hash = self._hash(admin_client, scene.family.id)

        response = admin_client.post(
            f"{BASE}/families/{scene.family.id}/schema/rebuild",
            json={"preview_hash": preview_hash},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["family_id"], body["version"], body["status"]) == (
            scene.family.id, 2, "building",
        )
        [job] = _jobs(db_session, "family_schema", family_id=scene.family.id)
        assert (job.schema_id, job.status) == (body["schema_id"], "pending")

    def test_confirmed_cost_is_not_held_by_the_event_cap(
        self, admin_client, db_session, factories, monkeypatch
    ):
        scene = _family_with_params(db_session, factories)
        preview_hash = self._hash(admin_client, scene.family.id)
        monkeypatch.setattr(app_settings, "SEMANTIC_EVENT_MAX_CONTEXTS", 0)

        response = admin_client.post(
            f"{BASE}/families/{scene.family.id}/schema/rebuild",
            json={"preview_hash": preview_hash},
        )

        assert response.status_code == 200, response.text
        assert len(_jobs(db_session, "family_schema", family_id=scene.family.id)) == 1
        assert db_session.execute(sa.select(sa.func.count(SemanticReconcileBatch.id))).scalar_one() == 0

    def test_event_cap_of_one_does_not_hold_it_either(
        self, admin_client, db_session, factories, monkeypatch
    ):
        scene = _family_with_params(db_session, factories)
        preview_hash = self._hash(admin_client, scene.family.id)
        monkeypatch.setattr(app_settings, "SEMANTIC_EVENT_MAX_CONTEXTS", 1)
        monkeypatch.setattr(app_settings, "SEMANTIC_EVENT_MAX_RESERVE_USD", Decimal("0"))

        response = admin_client.post(
            f"{BASE}/families/{scene.family.id}/schema/rebuild",
            json={"preview_hash": preview_hash},
        )

        assert response.status_code == 200, response.text
        assert len(_jobs(db_session, "family_schema", family_id=scene.family.id)) == 1

    def test_other_callers_keep_the_event_cap(self, db_session, factories, monkeypatch):
        """`rebuild_schema` без `cap` по-прежнему подчиняется потолку из
        настроек: задание удерживается пачкой."""
        scene = _family_with_params(db_session, factories)
        monkeypatch.setattr(app_settings, "SEMANTIC_EVENT_MAX_CONTEXTS", 0)

        rebuild_schema(db_session, family_id=scene.family.id, actor_id=scene.user.id)

        assert _jobs(db_session, "family_schema", family_id=scene.family.id) == []
        assert db_session.execute(sa.select(sa.func.count(SemanticReconcileBatch.id))).scalar_one() == 1

    def test_changed_state_is_409_and_writes_nothing(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories, titles=("Пол 1", "Пол 2", "Пол 3"))
        preview_hash = self._hash(admin_client, scene.family.id)
        db_session.execute(
            sa.update(CatalogContext)
            .where(CatalogContext.id == scene.context_ids[2])
            .values(work_family_id=None, family_source=None, family_by=None, family_at=None)
        )
        db_session.expire_all()
        db_session.commit()

        response = admin_client.post(
            f"{BASE}/families/{scene.family.id}/schema/rebuild",
            json={"preview_hash": preview_hash},
        )

        assert response.status_code == 409, response.text
        assert _detail(response)["code"] == "preview_changed"
        assert _jobs(db_session, "family_schema", family_id=scene.family.id) == []
        assert (
            db_session.execute(
                sa.select(sa.func.count(FamilyParameterSchema.id)).where(
                    FamilyParameterSchema.family_id == scene.family.id,
                    FamilyParameterSchema.status == "building",
                )
            ).scalar_one()
            == 0
        )

    def test_family_is_locked_before_the_estimate_is_reread(
        self, admin_client, db_session, factories
    ):
        """Подтверждение перепроверяет оценку ПОД замком семьи: `FOR UPDATE`
        семьи уходит в базу раньше первого чтения контекстов семьи (оценка),
        иначе состав контекстов мог смениться между перепроверкой и постановкой
        задания. Проверяется SQL, ушедший в базу (`docs/pitfalls/db.md`)."""
        scene = _family_with_params(db_session, factories)
        preview_hash = self._hash(admin_client, scene.family.id)
        statements: list[str] = []
        engine = db_session.get_bind().engine

        def _listener(conn, cursor, statement, parameters, context, executemany):
            statements.append(" ".join(statement.split()))

        sa.event.listen(engine, "before_cursor_execute", _listener)
        try:
            response = admin_client.post(
                f"{BASE}/families/{scene.family.id}/schema/rebuild",
                json={"preview_hash": preview_hash},
            )
        finally:
            sa.event.remove(engine, "before_cursor_execute", _listener)

        assert response.status_code == 200, response.text
        locks = [
            (index, "SHARE" if " FOR SHARE" in text else "UPDATE")
            for index, text in enumerate(statements)
            if re.search(r" FROM work_families\b", text)
            and (" FOR UPDATE" in text or " FOR SHARE" in text)
        ]
        first_context_read = next(
            index for index, text in enumerate(statements)
            if re.search(r"\bFROM catalog_contexts\b", text)
        )
        assert locks, statements
        assert locks[0][1] == "UPDATE"
        assert locks[0][0] < first_context_read

    def test_missing_hash_is_422(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)

        response = admin_client.post(f"{BASE}/families/{scene.family.id}/schema/rebuild", json={})

        assert response.status_code == 422

    def test_inactive_family_is_409(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)
        preview_hash = self._hash(admin_client, scene.family.id)
        db_session.execute(
            sa.text("UPDATE work_families SET status = 'archived', archived_at = now() WHERE id = :f"),
            {"f": scene.family.id},
        )
        db_session.expire_all()

        response = admin_client.post(
            f"{BASE}/families/{scene.family.id}/schema/rebuild",
            json={"preview_hash": preview_hash},
        )

        assert response.status_code == 409, response.text
        assert _detail(response)["code"] == "family_not_active"

    def test_unknown_family_is_404(self, admin_client):
        response = admin_client.post(
            f"{BASE}/families/999999999/schema/rebuild", json={"preview_hash": "x"}
        )

        assert response.status_code == 404
        assert _detail(response)["code"] == "family_not_found"

    def test_the_new_job_shows_in_the_errors_list_by_family(
        self, admin_client, db_session, factories
    ):
        scene = _family_with_params(db_session, factories)
        preview_hash = self._hash(admin_client, scene.family.id)
        admin_client.post(
            f"{BASE}/families/{scene.family.id}/schema/rebuild",
            json={"preview_hash": preview_hash},
        )
        [job] = _jobs(db_session, "family_schema", family_id=scene.family.id)
        db_session.execute(
            sa.update(SemanticJob)
            .where(SemanticJob.id == job.id)
            .values(status="error", last_error_class="provider_error")
        )
        db_session.expire_all()

        response = admin_client.get(f"{BASE}/jobs", params={"status": "error"})

        assert response.status_code == 200, response.text
        [item] = response.json()["items"]
        assert item["kind"] == "family_schema"
        assert item["context_id"] is None
        assert item["family_id"] == scene.family.id
        assert item["title"] == scene.family.title
        assert item["schema_version"] == 2
        assert item["names_count"] == 2


# ---------------------------------------------------------------------------
#  Ручная правка, отмена пересборки, слияние значений
# ---------------------------------------------------------------------------

def _edit(*parameters):
    return {
        "parameters": [
            {"ordinal": ordinal, "name": name, "values": list(values)}
            for ordinal, name, values in parameters
        ]
    }


class TestSchemaEdit:
    def test_cosmetic_rename_keeps_the_version(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)

        response = admin_client.patch(
            f"{BASE}/families/{scene.family.id}/schema",
            json=_edit((1, "Толщина", ("50 мм", "100 мм")), (2, "Материал", ("Бетон", "кирпич"))),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["version"] == 1
        assert [v["value"] for v in body["parameters"][1]["values"]] == ["Бетон", "кирпич"]

    def test_added_value_makes_a_new_manual_version(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)

        response = admin_client.patch(
            f"{BASE}/families/{scene.family.id}/schema",
            json=_edit(
                (1, "Толщина", ("50 мм", "100 мм", "150 мм")), (2, "Материал", ("бетон", "кирпич"))
            ),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["status"], body["version"]) == ("frozen", 2)
        assert [v["value"] for v in body["parameters"][0]["values"]] == [
            "50 мм", "100 мм", "150 мм",
        ]

    @pytest.mark.parametrize(
        ("parameters", "code"),
        [
            pytest.param(
                _edit((1, "Диаметр", ("50 мм", "100 мм")), (2, "Материал", ("бетон", "кирпич"))),
                "schema_parameter_renamed", id="renamed",
            ),
            pytest.param(
                _edit((1, "Толщина", ("50 мм",)), (2, "Материал", ("бетон", "кирпич"))),
                "schema_value_removed", id="value-removed",
            ),
            pytest.param(
                _edit((1, "  ", ("50 мм", "100 мм")), (2, "Материал", ("бетон", "кирпич"))),
                "schema_blank", id="blank-name",
            ),
            pytest.param(
                _edit((1, "Толщина", ("50 мм", " ")), (2, "Материал", ("бетон", "кирпич"))),
                "schema_blank", id="blank-value",
            ),
            pytest.param(
                _edit((1, "Толщина", ("50 мм",)), (1, "Материал", ("бетон",))),
                "schema_bad_ordinals", id="repeated-ordinal",
            ),
            pytest.param(
                _edit((4, "Толщина", ("50 мм",))), "schema_bad_ordinals", id="ordinal-out-of-range",
            ),
        ],
    )
    def test_invalid_input_is_422_with_the_code(
        self, parameters, code, admin_client, db_session, factories
    ):
        scene = _family_with_params(db_session, factories)

        response = admin_client.patch(f"{BASE}/families/{scene.family.id}/schema", json=parameters)

        assert response.status_code == 422, response.text
        assert _detail(response)["code"] == code

    def test_building_version_is_409(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)
        _building(db_session, scene.family)

        response = admin_client.patch(
            f"{BASE}/families/{scene.family.id}/schema",
            json=_edit((1, "Толщина", ("50 мм", "100 мм")), (2, "Материал", ("бетон", "кирпич"))),
        )

        assert response.status_code == 409, response.text
        assert _detail(response)["code"] == "schema_building"

    def test_family_without_current_version_is_409(self, admin_client, db_session):
        family = _schemaless_family(db_session)

        response = admin_client.patch(
            f"{BASE}/families/{family.id}/schema", json=_edit((1, "Толщина", ("50 мм",)))
        )

        assert response.status_code == 409, response.text
        assert _detail(response)["code"] == "schema_no_current"

    def test_unknown_family_is_404(self, admin_client):
        response = admin_client.patch(
            f"{BASE}/families/999999999/schema", json=_edit((1, "Толщина", ("50 мм",)))
        )

        assert response.status_code == 404
        assert _detail(response)["code"] == "family_not_found"

    def test_malformed_body_is_422(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)

        response = admin_client.patch(
            f"{BASE}/families/{scene.family.id}/schema", json={"parameters": [{"ordinal": 1}]}
        )

        assert response.status_code == 422


class TestSchemaCancel:
    def test_building_version_is_cancelled_with_its_job(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)
        preview_hash = admin_client.post(
            f"{BASE}/families/{scene.family.id}/schema/rebuild/preview"
        ).json()["preview_hash"]
        admin_client.post(
            f"{BASE}/families/{scene.family.id}/schema/rebuild",
            json={"preview_hash": preview_hash},
        )

        response = admin_client.post(f"{BASE}/families/{scene.family.id}/schema/cancel")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["building"] is False
        assert (body["status"], body["version"]) == ("frozen", 1)
        [job] = _jobs(db_session, "family_schema", family_id=scene.family.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "not_applicable")

    def test_without_building_is_409(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)

        response = admin_client.post(f"{BASE}/families/{scene.family.id}/schema/cancel")

        assert response.status_code == 409, response.text
        assert _detail(response)["code"] == "schema_no_building"

    def test_unknown_family_is_404(self, admin_client):
        response = admin_client.post(f"{BASE}/families/999999999/schema/cancel")

        assert response.status_code == 404
        assert _detail(response)["code"] == "family_not_found"


class TestValuesMerge:
    def _ids(self, client, family_id):
        parameters = client.get(f"{BASE}/families/{family_id}/schema").json()["parameters"]
        first, second = parameters[0]["values"]
        return parameters[0]["id"], first["id"], second["id"], parameters[1]["values"][0]["id"]

    def test_source_becomes_a_synonym_of_the_target(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)
        parameter_id, first, second, _ = self._ids(admin_client, scene.family.id)

        response = admin_client.post(
            f"{BASE}/families/{scene.family.id}/schema/values/merge",
            json={"parameter_id": parameter_id, "source_value_id": second, "target_value_id": first},
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"merged_variants": []}
        values = admin_client.get(f"{BASE}/families/{scene.family.id}/schema").json()[
            "parameters"
        ][0]["values"]
        assert [(v["id"], v["merged_into_id"]) for v in values] == [(first, None), (second, first)]

    def test_merged_variants_are_reported_as_pairs(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)
        parameter_id, first, second, _ = self._ids(admin_client, scene.family.id)
        parameters = admin_client.get(f"{BASE}/families/{scene.family.id}/schema").json()[
            "parameters"
        ]
        params_by_ordinal = {p["ordinal"]: p for p in parameters}
        material = params_by_ordinal[2]["values"][0]["id"]
        schema_id = scene.schema.id
        source_variant = _variant(db_session, scene.family, scene.schema, values_key=values_key_of({1: second, 2: material}))
        target_variant = _variant(db_session, scene.family, scene.schema, values_key=values_key_of({1: first, 2: material}))
        for variant, value in ((source_variant, second), (target_variant, first)):
            for ordinal, value_id in ((1, value), (2, material)):
                db_session.add(
                    WorkVariantValue(
                        variant_id=variant.id, schema_id=schema_id,
                        parameter_id=params_by_ordinal[ordinal]["id"], value_id=value_id,
                    )
                )
        db_session.flush()

        response = admin_client.post(
            f"{BASE}/families/{scene.family.id}/schema/values/merge",
            json={"parameter_id": parameter_id, "source_value_id": second, "target_value_id": first},
        )

        assert response.status_code == 200, response.text
        assert response.json() == {
            "merged_variants": [
                {"source_variant_id": source_variant.id, "target_variant_id": target_variant.id}
            ]
        }

    def test_value_of_another_parameter_is_422(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)
        parameter_id, first, _second, foreign = self._ids(admin_client, scene.family.id)

        response = admin_client.post(
            f"{BASE}/families/{scene.family.id}/schema/values/merge",
            json={"parameter_id": parameter_id, "source_value_id": foreign, "target_value_id": first},
        )

        assert response.status_code == 422, response.text
        assert _detail(response)["code"] == "merge_values_other_parameter"

    def test_target_that_is_an_ancestor_is_422(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)
        parameter_id, first, second, _ = self._ids(admin_client, scene.family.id)
        path = f"{BASE}/families/{scene.family.id}/schema/values/merge"
        admin_client.post(
            path, json={"parameter_id": parameter_id, "source_value_id": second, "target_value_id": first}
        )

        # `first` — цель слияния; сделать его синонимом `second` значит замкнуть цепочку.
        response = admin_client.post(
            path, json={"parameter_id": parameter_id, "source_value_id": first, "target_value_id": second}
        )

        assert response.status_code == 422, response.text
        assert _detail(response)["code"] == "merge_value_cycle"

    def test_source_already_merged_is_409(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)
        parameter_id, first, second, _ = self._ids(admin_client, scene.family.id)
        path = f"{BASE}/families/{scene.family.id}/schema/values/merge"
        body = {"parameter_id": parameter_id, "source_value_id": second, "target_value_id": first}
        admin_client.post(path, json=body)

        response = admin_client.post(path, json=body)

        assert response.status_code == 409, response.text
        assert _detail(response)["code"] == "merge_source_merged"

    def test_missing_value_is_404(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)
        parameter_id, first, _second, _ = self._ids(admin_client, scene.family.id)

        response = admin_client.post(
            f"{BASE}/families/{scene.family.id}/schema/values/merge",
            json={"parameter_id": parameter_id, "source_value_id": 999999999, "target_value_id": first},
        )

        assert response.status_code == 404, response.text
        assert _detail(response)["code"] == "value_not_found"

    def test_missing_parameter_is_404(self, admin_client, db_session, factories):
        scene = _family_with_params(db_session, factories)

        response = admin_client.post(
            f"{BASE}/families/{scene.family.id}/schema/values/merge",
            json={"parameter_id": 999999999, "source_value_id": 1, "target_value_id": 2},
        )

        assert response.status_code == 404, response.text
        assert _detail(response)["code"] == "parameter_not_found"

    def test_parameter_of_another_family_is_404_and_nothing_is_merged(
        self, admin_client, db_session, factories
    ):
        scene = _family_with_params(db_session, factories)
        other = _active_other_family(db_session, factories)
        parameter_id, first, second, _ = self._ids(admin_client, scene.family.id)
        db_session.commit()

        response = admin_client.post(
            f"{BASE}/families/{other.id}/schema/values/merge",
            json={"parameter_id": parameter_id, "source_value_id": second, "target_value_id": first},
        )

        assert response.status_code == 404, response.text
        assert _detail(response)["code"] == "parameter_not_found"
        values = admin_client.get(f"{BASE}/families/{scene.family.id}/schema").json()[
            "parameters"
        ][0]["values"]
        assert [v["merged_into_id"] for v in values] == [None, None]


# ---------------------------------------------------------------------------
#  Смена семьи, ожидание, «не работа»
# ---------------------------------------------------------------------------

class TestFamilyChange:
    def test_assigned_without_a_variant(self, admin_client, db_session, factories):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]

        response = admin_client.post(
            f"{BASE}/contexts/{context_id}/family", json={"family_id": scene.family_b.id}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["outcome"] == "assigned"
        assert (body["context_id"], body["family_id"]) == (context_id, scene.family_b.id)
        db_session.expire_all()
        context = db_session.get(CatalogContext, context_id)
        assert (context.work_family_id, context.pending_family_id) == (scene.family_b.id, None)

    def test_pending_with_a_variant(self, admin_client, db_session, factories):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        variant = _with_variant(db_session, scene, context_id, scene.family)

        response = admin_client.post(
            f"{BASE}/contexts/{context_id}/family", json={"family_id": scene.family_b.id}
        )

        assert response.status_code == 200, response.text
        assert response.json()["outcome"] == "pending"
        db_session.expire_all()
        context = db_session.get(CatalogContext, context_id)
        assert (context.work_family_id, context.work_variant_id) == (scene.family.id, variant.id)
        assert (context.pending_family_id, context.pending_family_source) == (
            scene.family_b.id, "manual",
        )

    def test_the_same_family_is_unchanged(self, admin_client, db_session, factories):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)

        response = admin_client.post(
            f"{BASE}/contexts/{context_id}/family", json={"family_id": scene.family.id}
        )

        assert response.status_code == 200, response.text
        assert response.json()["outcome"] == "unchanged"

    def test_null_removes_the_family_and_the_variant(self, admin_client, db_session, factories):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)

        response = admin_client.post(
            f"{BASE}/contexts/{context_id}/family", json={"family_id": None}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["outcome"], body["family_id"]) == ("assigned", None)
        db_session.expire_all()
        context = db_session.get(CatalogContext, context_id)
        assert (context.work_family_id, context.work_variant_id) == (None, None)

    def test_unknown_context_is_404(self, admin_client, db_session, factories):
        scene = _two_families(db_session, factories)

        response = admin_client.post(
            f"{BASE}/contexts/999999999/family", json={"family_id": scene.family_b.id}
        )

        assert response.status_code == 404
        assert _detail(response)["code"] == "context_not_found"

    def test_inactive_family_is_409(self, admin_client, db_session, factories):
        scene = _two_families(db_session, factories)
        db_session.execute(
            sa.text("UPDATE work_families SET status = 'archived', archived_at = now() WHERE id = :f"),
            {"f": scene.family_b.id},
        )
        db_session.expire_all()

        response = admin_client.post(
            f"{BASE}/contexts/{scene.context_ids[0]}/family", json={"family_id": scene.family_b.id}
        )

        assert response.status_code == 409
        assert _detail(response)["code"] == "family_not_active"


class TestPendingFamily:
    def test_cancel_clears_the_pending_assignment(self, admin_client, db_session, factories):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)
        _set_pending(db_session, factories, context_id, scene.family_b)

        response = admin_client.delete(f"{BASE}/contexts/{context_id}/pending-family")

        assert response.status_code == 200, response.text
        assert response.json()["variant"]["pending"] is None
        db_session.expire_all()
        context = db_session.get(CatalogContext, context_id)
        assert (context.pending_family_id, context.work_family_id) == (None, scene.family.id)

    def test_unknown_context_is_404(self, admin_client):
        response = admin_client.delete(f"{BASE}/contexts/999999999/pending-family")

        assert response.status_code == 404
        assert _detail(response)["code"] == "context_not_found"


@pytest.mark.parametrize("route", ["pending-family", "not-work"])
def test_card_returning_mutation_commits_even_when_the_card_read_fails(
    route, admin_client, db_session, factories, monkeypatch
):
    """Карточка в ответе читается ПОСЛЕ коммита мутации (тот же приём, что у
    `POST .../kind` фичи 1): отказ чтения карточки — доменный ответ, а мутация
    уже записана."""
    import crud.semantic as crud_semantic_module

    scene = _two_families(db_session, factories)
    context_id = scene.context_ids[0]
    _with_variant(db_session, scene, context_id, scene.family)
    _set_pending(db_session, factories, context_id, scene.family_b)
    db_session.commit()

    def _card_fails(*_args, **_kwargs):
        raise work_families_module.WorkFamilyError(
            work_families_module.REFUSE_CONTEXT_NOT_FOUND, "карточка не читается"
        )

    monkeypatch.setattr(crud_semantic_module, "context_card", _card_fails)
    method = "DELETE" if route == "pending-family" else "POST"

    response = admin_client.request(method, f"{BASE}/contexts/{context_id}/{route}")

    assert response.status_code == 404, response.text
    db_session.expire_all()
    context = db_session.get(CatalogContext, context_id)
    assert context.pending_family_id is None
    if route == "not-work":
        assert context.semantic_state == "NOT_APPLICABLE"


class TestNotWork:
    def test_context_leaves_the_work_with_its_family_and_variant(
        self, admin_client, db_session, factories
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)

        response = admin_client.post(f"{BASE}/contexts/{context_id}/not-work")

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["semantic_state"], body["work_family_id"]) == ("NOT_APPLICABLE", None)
        assert body["variant"]["variant_id"] is None

    def test_repeat_is_409_with_the_code(self, admin_client, db_session, factories):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        admin_client.post(f"{BASE}/contexts/{context_id}/not-work")

        response = admin_client.post(f"{BASE}/contexts/{context_id}/not-work")

        assert response.status_code == 409
        assert _detail(response)["code"] == "context_not_applicable"

    def test_unknown_context_is_404(self, admin_client):
        response = admin_client.post(f"{BASE}/contexts/999999999/not-work")

        assert response.status_code == 404
        assert _detail(response)["code"] == "context_not_found"


# ---------------------------------------------------------------------------
#  Глобальная пометка строки каталога
# ---------------------------------------------------------------------------

class TestPositionKind:
    def test_marks_the_row_and_takes_its_contexts_off_work(
        self, admin_client, db_session, factories
    ):
        world = _human_position(db_session, factories)

        response = admin_client.post(
            f"{BASE}/positions/{world.catalog_id}/kind", json={"kind": "TRASH"}
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"position_id": world.catalog_id, "kind": "TRASH"}
        db_session.expire_all()
        assert db_session.get(CatalogPosition, world.catalog_id).kind == "TRASH"
        assert db_session.get(CatalogContext, world.context_id).semantic_state == "NOT_APPLICABLE"

    def test_standards_make_a_409_with_the_list(self, admin_client, db_session, factories):
        world = _human_position(db_session, factories)
        standard = _standard(
            db_session, factories, world.catalog_id, valid_from=dt.date(2026, 1, 1),
            valid_to=dt.date(2026, 12, 31),
        )
        db_session.commit()

        response = admin_client.post(
            f"{BASE}/positions/{world.catalog_id}/kind", json={"kind": "HEADER"}
        )

        assert response.status_code == 409, response.text
        detail = _detail(response)
        assert detail["code"] == "position_has_standards"
        [listed] = detail["standards"]
        assert listed["id"] == standard.id
        assert (listed["valid_from"], listed["valid_to"]) == ("2026-01-01", "2026-12-31")
        db_session.expire_all()
        assert db_session.get(CatalogPosition, world.catalog_id).kind == "POSITION"

    def test_a_row_that_is_not_a_position_is_409(self, admin_client, db_session, factories):
        world = _world(db_session, factories, catalog_kind="TO_REVIEW")

        response = admin_client.post(
            f"{BASE}/positions/{world.catalog_id}/kind", json={"kind": "HEADER"}
        )

        assert response.status_code == 409
        assert _detail(response)["code"] == "position_not_position"

    @pytest.mark.parametrize("kind", ["POSITION", "WORK", ""])
    def test_wrong_kind_is_422_with_the_code(self, kind, admin_client, db_session, factories):
        world = _human_position(db_session, factories)

        response = admin_client.post(
            f"{BASE}/positions/{world.catalog_id}/kind", json={"kind": kind}
        )

        assert response.status_code == 422, response.text
        assert _detail(response)["code"] == "invalid_kind"

    def test_missing_kind_is_422(self, admin_client, db_session, factories):
        world = _human_position(db_session, factories)

        response = admin_client.post(f"{BASE}/positions/{world.catalog_id}/kind", json={})

        assert response.status_code == 422

    def test_unknown_row_is_404(self, admin_client):
        response = admin_client.post(f"{BASE}/positions/999999999/kind", json={"kind": "HEADER"})

        assert response.status_code == 404
        assert _detail(response)["code"] == "position_not_found"


# ---------------------------------------------------------------------------
#  Массовое автопринятие
# ---------------------------------------------------------------------------

class TestAutoAccept:
    def test_preview_without_a_threshold_is_409(self, admin_client, db_session, factories, monkeypatch):
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", None)

        response = admin_client.post(f"{BASE}/auto-accept/preview")

        assert response.status_code == 409, response.text
        assert _detail(response)["code"] == "threshold_missing"

    def test_apply_without_a_threshold_is_409(self, admin_client, monkeypatch):
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", None)

        response = admin_client.post(f"{BASE}/auto-accept", json={"preview_hash": "x"})

        assert response.status_code == 409, response.text
        assert _detail(response)["code"] == "threshold_missing"

    def test_preview_shape_and_counts(self, admin_client, db_session, factories, monkeypatch):
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", THRESHOLD)
        _deploy_scene(db_session, factories)

        response = admin_client.post(f"{BASE}/auto-accept/preview")

        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"by_outcome", "total", "preview_hash", "threshold"}
        assert body["by_outcome"] == {"confirm": 1, "assign": 2, "pending": 1, "none": 1}
        assert body["total"] == 5
        assert body["threshold"] == "0.80"
        assert re.fullmatch(r"[0-9a-f]{64}", body["preview_hash"])

    def test_apply_with_the_previewed_hash(self, admin_client, db_session, factories, monkeypatch):
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", THRESHOLD)
        scene = _deploy_scene(db_session, factories)
        preview_hash = admin_client.post(f"{BASE}/auto-accept/preview").json()["preview_hash"]

        response = admin_client.post(f"{BASE}/auto-accept", json={"preview_hash": preview_hash})

        assert response.status_code == 200, response.text
        assert response.json() == {"applied": {"confirm": 1, "assign": 2, "pending": 1}}
        db_session.expire_all()
        assert db_session.get(CatalogContext, scene.c_assign).work_family_id == scene.family_b.id
        assert db_session.get(CatalogContext, scene.c_pending).pending_family_id == scene.family_b.id

    def test_apply_with_a_stale_hash_is_409_and_writes_nothing(
        self, admin_client, db_session, factories, monkeypatch
    ):
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", THRESHOLD)
        scene = _deploy_scene(db_session, factories)
        preview_hash = admin_client.post(f"{BASE}/auto-accept/preview").json()["preview_hash"]
        _bind_source(db_session, scene, scene.c_assign, scene.family, "manual")
        db_session.commit()
        before = _state(db_session, scene)

        response = admin_client.post(f"{BASE}/auto-accept", json={"preview_hash": preview_hash})

        assert response.status_code == 409, response.text
        assert _detail(response)["code"] == "preview_changed"
        assert _state(db_session, scene) == before


# ---------------------------------------------------------------------------
#  Очередь «Смена семьи»
# ---------------------------------------------------------------------------

def _decide_like_the_rule(db, scene, *context_ids):
    """Предложения, которые правило публикации УЖЕ приняло (`decision` проставлен
    его транзакцией): в очереди «Смена семьи» человеку они не нужны."""
    for context_id, decision in (
        (scene.c_pending, "auto_pending"), (scene.c_auto, "auto_accepted"),
    ):
        if context_id in context_ids:
            suggestion = scene.suggestions[context_id]
            suggestion.decision = decision
            suggestion.decided_at = dt.datetime.now(dt.UTC)
    db.flush()


class TestChangeQueue:
    def test_manual_binding_gets_any_confidence_and_auto_binding_below_the_threshold_only(
        self, admin_client, db_session, factories, monkeypatch
    ):
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", THRESHOLD)
        scene = _deploy_scene(db_session, factories)
        _decide_like_the_rule(db_session, scene, scene.c_pending, scene.c_auto)

        # Автопривязки (с вариантом и без) с уверенностью 0.9 при пороге 0.80
        # правило применило (решение проставлено), а человека — нет: в очереди
        # только ручная привязка.
        assert _change_context_ids(admin_client) == {scene.c_none}

    def test_binding_confirmed_by_a_person_gets_any_confidence(
        self, admin_client, db_session, factories, monkeypatch
    ):
        """Привязка из подтверждённого человеком предложения (`suggestion`) —
        привязка человека (строка 7 таблицы §2.5): правило её не трогает и выше
        порога, предложение другой семьи — в очереди."""
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", THRESHOLD)
        scene = _deploy_scene(db_session, factories)
        _decide_like_the_rule(db_session, scene, scene.c_pending)
        _bind_source(db_session, scene, scene.c_auto, scene.family, "suggestion")

        assert _change_context_ids(admin_client) == {scene.c_none, scene.c_auto}

    def test_a_suggestion_the_rule_did_not_decide_stays_with_the_human(
        self, admin_client, db_session, factories, monkeypatch
    ):
        """Правило применило бы предложения `c_pending` и `c_auto` (уверенность
        0.9 при пороге 0.80), но его транзакция не состоялась (`decision IS
        NULL`): человек видит их в очереди (спека §2.5)."""
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", THRESHOLD)
        scene = _deploy_scene(db_session, factories)

        assert _change_context_ids(admin_client) == {scene.c_pending, scene.c_none, scene.c_auto}

    def test_the_queue_follows_the_recorded_decision_not_the_threshold(
        self, admin_client, db_session, factories, monkeypatch
    ):
        """Решённое правилом предложение остаётся решённым при любом пороге
        (порог читает правило, а не очередь)."""
        scene = _deploy_scene(db_session, factories)
        _decide_like_the_rule(db_session, scene, scene.c_pending, scene.c_auto)

        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", Decimal("0.90"))
        at_threshold = _change_context_ids(admin_client)
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", Decimal("0.91"))
        above_confidence = _change_context_ids(admin_client)

        assert at_threshold == {scene.c_none}
        assert above_confidence == {scene.c_none}

    def test_without_a_threshold_every_bound_context_with_another_family_is_in_the_queue(
        self, admin_client, db_session, factories, monkeypatch
    ):
        """Порог снят после того, как правило успело завести автопривязки: новые
        предложения к ним видны человеку (применить их массово нельзя)."""
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", None)
        scene = _deploy_scene(db_session, factories)

        assert _change_context_ids(admin_client) == {scene.c_pending, scene.c_none, scene.c_auto}

    def test_unpublished_and_decided_suggestions_are_not_in_the_queue(
        self, admin_client, db_session, factories, monkeypatch
    ):
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", THRESHOLD)
        scene = _deploy_scene(db_session, factories)
        _decide_like_the_rule(db_session, scene, scene.c_pending, scene.c_auto)
        suggestion = scene.suggestions[scene.c_none]

        _unpublish(db_session, suggestion)
        assert _change_context_ids(admin_client) == set()

        suggestion.is_published = True
        suggestion.unpublished_reason = None
        suggestion.decision = "rejected"
        suggestion.decided_by = scene.user.id
        suggestion.decided_at = dt.datetime.now(dt.UTC)
        db_session.flush()
        assert _change_context_ids(admin_client) == set()

    def test_context_without_a_family_is_not_in_the_queue(
        self, admin_client, db_session, factories, monkeypatch
    ):
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", Decimal("0.99"))
        scene = _deploy_scene(db_session, factories)

        assert scene.c_assign not in _change_context_ids(admin_client)

    def test_suggestion_of_the_current_family_is_not_in_the_queue(
        self, admin_client, db_session, factories, monkeypatch
    ):
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", Decimal("0.99"))
        scene = _deploy_scene(db_session, factories)

        assert scene.c_confirm not in _change_context_ids(admin_client)

    def test_groups_are_family_to_family_and_band(
        self, admin_client, db_session, factories, monkeypatch
    ):
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", Decimal("0.95"))
        scene = _deploy_scene(db_session, factories)
        scene.suggestions[scene.c_none].confidence = Decimal("0.75")
        db_session.flush()

        body = _change_queue(admin_client)

        assert body["queue"] == "change"
        assert body["items"] == []
        shape = [
            (g["from_family_id"], g["family_id"], g["band"], [r["context_id"] for r in g["rows"]])
            for g in body["groups"]
        ]
        assert shape == [
            (scene.family.id, scene.family_b.id, "high", sorted([scene.c_pending, scene.c_auto])),
            (scene.family.id, scene.family_b.id, "mid", [scene.c_none]),
        ]
        group = body["groups"][0]
        assert group["from_family_title"] == scene.family.title
        assert group["family_title"] == scene.family_b.title
        assert group["total"] == 2
        row = group["rows"][0]
        assert {"suggestion_id", "context_id", "title", "confidence", "reason"} <= set(row)

    def test_band_and_unit_filters_apply(self, admin_client, db_session, factories, monkeypatch):
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", Decimal("0.95"))
        scene = _deploy_scene(db_session, factories)
        scene.suggestions[scene.c_none].confidence = Decimal("0.75")
        db_session.flush()

        assert _change_context_ids(admin_client, band="high") == {scene.c_pending, scene.c_auto}
        assert _change_context_ids(admin_client, band="mid") == {scene.c_none}
        assert _change_context_ids(admin_client, band="low") == set()
        assert _change_context_ids(admin_client, unit=str(scene.unit_id)) == {
            scene.c_pending, scene.c_none, scene.c_auto,
        }
        assert _change_context_ids(admin_client, unit="none") == set()

    def test_multi_owner_filter_applies(self, admin_client, db_session, factories, monkeypatch):
        from tests.integration.test_semantic_queue_api import _shared_context

        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", THRESHOLD)
        scene = _deploy_scene(db_session, factories)
        _decide_like_the_rule(db_session, scene, scene.c_pending, scene.c_auto)
        estimates = [
            factories.EstimateFactory.create(contract=factories.ContractFactory.create())
            for _ in range(2)
        ]
        shared = _shared_context(
            db_session, factories, estimates, unit_id=scene.unit_id, title="Разделяемая"
        )
        _bind_source(db_session, scene, shared, scene.family, "manual")
        _publish(db_session, shared, family_id=scene.family_b.id)

        everything = _change_queue(admin_client)
        flags = {r["context_id"]: r["multi_owner"] for g in everything["groups"] for r in g["rows"]}

        assert flags == {scene.c_none: False, shared: True}
        assert _change_context_ids(admin_client, multi_owner="true") == {shared}

    def test_previously_rejected_mark_is_shown(
        self, admin_client, db_session, factories, monkeypatch
    ):
        """Отклонённое раньше предложение той же семьи тому же контексту
        помечает строку очереди «Смена семьи», как в очереди `list`."""
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", THRESHOLD)
        scene = _deploy_scene(db_session, factories)
        _decide_like_the_rule(db_session, scene, scene.c_pending, scene.c_auto)
        old = scene.suggestions[scene.c_none]
        decided_at = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC)
        _unpublish(db_session, old)
        old.decision = "rejected"
        old.decided_by = scene.user.id
        old.decided_at = decided_at
        db_session.flush()
        fresh = _publish(db_session, scene.c_none, family_id=scene.family_b.id)

        [group] = _change_queue(admin_client)["groups"]
        [row] = group["rows"]

        assert row["suggestion_id"] == fresh.id
        mark = row["previously_rejected"]
        assert (mark["family_id"], mark["family_title"]) == (scene.family_b.id, scene.family_b.title)
        assert dt.datetime.fromisoformat(mark["decided_at"]) == decided_at

    def test_suggestion_on_an_outdated_fingerprint_is_not_shown(
        self, admin_client, db_session, factories, monkeypatch
    ):
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", THRESHOLD)
        scene = _deploy_scene(db_session, factories)
        _decide_like_the_rule(db_session, scene, scene.c_pending, scene.c_auto)
        assert _change_context_ids(admin_client) == {scene.c_none}

        _make_stale(db_session, scene)

        assert _change_context_ids(admin_client) == set()

    def test_bound_context_is_in_change_only_and_unbound_in_list_and_new(
        self, admin_client, db_session, factories, monkeypatch
    ):
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", None)
        scene = _two_families(db_session, factories, titles=("Пол 1", "Пол 2", "Пол 3", "Пол 4"))
        bound_family, unbound_family, bound_new, unbound_new = scene.context_ids
        for context_id in (bound_family, bound_new):
            _bind_source(db_session, scene, context_id, scene.family, "manual")
        _publish(db_session, bound_family, family_id=scene.family_b.id)
        _publish(db_session, unbound_family, family_id=scene.family_b.id)
        _publish(db_session, bound_new, family_id=None)
        _publish(db_session, unbound_new, family_id=None)

        in_list = {
            row["context_id"]
            for group in admin_client.get(f"{BASE}/suggestions", params={"queue": "list"}).json()[
                "groups"
            ]
            for row in group["rows"]
        }
        in_new = {
            row["context_id"]
            for row in admin_client.get(f"{BASE}/suggestions", params={"queue": "new"}).json()[
                "items"
            ]
            if row["suggestion_id"] is not None
        }

        assert in_list == {unbound_family}
        assert in_new == {unbound_new}
        assert _change_context_ids(admin_client) == {bound_family}

    def test_unit_filter_keeps_the_unbound_only_rule(
        self, admin_client, db_session, factories, monkeypatch
    ):
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", None)
        scene = _two_families(db_session, factories, titles=("Пол 1", "Пол 2"))
        bound, unbound = scene.context_ids
        _bind_source(db_session, scene, bound, scene.family, "manual")
        _publish(db_session, bound, family_id=scene.family_b.id)
        _publish(db_session, unbound, family_id=scene.family_b.id)

        body = admin_client.get(
            f"{BASE}/suggestions", params={"queue": "list", "unit": str(scene.unit_id)}
        ).json()

        assert {r["context_id"] for g in body["groups"] for r in g["rows"]} == {unbound}

    def test_auto_binding_with_a_human_pending_stays_in_the_queue_above_the_threshold(
        self, admin_client, db_session, factories, monkeypatch
    ):
        """Правило не вытесняет ожидание человека и предложение не применяет;
        оно идёт человеку, хотя уверенность не ниже порога."""
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", THRESHOLD)
        scene = _two_families(db_session, factories, titles=("Пол 1",))
        context_id = scene.context_ids[0]
        _bind_source(db_session, scene, context_id, scene.family, "auto_suggestion")
        _set_pending(db_session, factories, context_id, scene.family_c, source="manual")
        suggestion = _publish(db_session, context_id, family_id=scene.family_b.id)
        assert suggestion.confidence >= THRESHOLD

        assert _change_context_ids(admin_client) == {context_id}

    def test_auto_binding_without_a_pending_is_left_to_the_rule_once_it_decided(
        self, admin_client, db_session, factories, monkeypatch
    ):
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", THRESHOLD)
        scene = _two_families(db_session, factories, titles=("Пол 1",))
        context_id = scene.context_ids[0]
        _bind_source(db_session, scene, context_id, scene.family, "auto_suggestion")
        _publish(
            db_session, context_id, family_id=scene.family_b.id, decision="auto_accepted",
        )

        assert _change_context_ids(admin_client) == set()

    def test_auto_binding_whose_auto_accept_did_not_happen_is_in_the_queue(
        self, admin_client, db_session, factories, monkeypatch
    ):
        """Тот же вход без решения (транзакция правила не состоялась): высокая
        уверенность другой семьи не прячет предложение от человека."""
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", THRESHOLD)
        scene = _two_families(db_session, factories, titles=("Пол 1",))
        context_id = scene.context_ids[0]
        _bind_source(db_session, scene, context_id, scene.family, "auto_suggestion")
        suggestion = _publish(db_session, context_id, family_id=scene.family_b.id)
        assert (suggestion.confidence >= THRESHOLD, suggestion.decision) == (True, None)

        assert _change_context_ids(admin_client) == {context_id}

    @pytest.mark.parametrize("break_family", ["archived", "other_unit"])
    def test_suggested_family_that_does_not_fit_is_not_shown(
        self, break_family, admin_client, db_session, factories, monkeypatch
    ):
        monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", None)
        scene = _two_families(db_session, factories, titles=("Пол 1",))
        context_id = scene.context_ids[0]
        _bind_source(db_session, scene, context_id, scene.family, "manual")

        if break_family == "archived":
            db_session.execute(
                sa.text("UPDATE work_families SET status = 'archived', archived_at = now() WHERE id = :f"),
                {"f": scene.family_b.id},
            )
        else:
            other_unit = _unit_id(db_session, "PCS")
            db_session.execute(
                sa.text("UPDATE work_families SET unit_id = :u WHERE id = :f"),
                {"u": other_unit, "f": scene.family_b.id},
            )
        db_session.expire_all()
        # Предложение — на отпечаток ПОСЛЕ порчи семьи: устаревшим оно не станет,
        # и скрывает его именно непригодность семьи.
        _publish(db_session, context_id, family_id=scene.family_b.id)

        assert _change_context_ids(admin_client) == set()

    def test_unknown_queue_is_422(self, admin_client):
        response = admin_client.get(f"{BASE}/suggestions", params={"queue": "other"})

        assert response.status_code == 422

    def test_the_other_queues_still_answer(self, admin_client, db_session, factories):
        for queue in ("list", "new"):
            response = admin_client.get(f"{BASE}/suggestions", params={"queue": queue})

            assert response.status_code == 200
            assert response.json()["queue"] == queue


# ---------------------------------------------------------------------------
#  Шапка: счётчики промоушена
# ---------------------------------------------------------------------------

_COUNTERS = (
    "catalog_to_review", "catalog_position", "contexts_with_variant", "contexts_pending",
    "families_without_schema",
)


class TestStatusCounters:
    def _counters(self, client) -> dict[str, int]:
        body = client.get(f"{BASE}/status").json()
        return {name: body[name] for name in _COUNTERS}

    def test_counters_are_present_and_integer(self, admin_client):
        counters = self._counters(admin_client)

        assert all(isinstance(value, int) for value in counters.values()), counters

    def test_catalog_rows_by_kind(self, admin_client, db_session, factories):
        before = self._counters(admin_client)

        for kind, count in (("TO_REVIEW", 3), ("POSITION", 2), ("HEADER", 4)):
            for _ in range(count):
                factories.CatalogPositionFactory.create(kind=kind)
        after = self._counters(admin_client)

        assert after["catalog_to_review"] - before["catalog_to_review"] == 3
        assert after["catalog_position"] - before["catalog_position"] == 2

    def test_contexts_with_a_variant(self, admin_client, db_session, factories):
        scene = _two_families(db_session, factories, titles=("Пол 1", "Пол 2", "Пол 3"))
        before = self._counters(admin_client)

        for context_id in scene.context_ids[:2]:
            _with_variant(db_session, scene, context_id, scene.family)
        after = self._counters(admin_client)

        assert after["contexts_with_variant"] - before["contexts_with_variant"] == 2
        assert after["contexts_pending"] == before["contexts_pending"]

    def test_pending_contexts(self, admin_client, db_session, factories):
        scene = _two_families(db_session, factories, titles=("Пол 1", "Пол 2", "Пол 3"))
        before = self._counters(admin_client)

        for context_id in scene.context_ids[:2]:
            _with_variant(db_session, scene, context_id, scene.family)
            _set_pending(db_session, factories, context_id, scene.family_b)
        after = self._counters(admin_client)

        assert after["contexts_pending"] - before["contexts_pending"] == 2

    def test_active_families_without_a_current_schema(self, admin_client, db_session, factories):
        before = self._counters(admin_client)

        _schemaless_family(db_session)
        _schemaless_family(db_session)
        _draft_family(db_session)
        after = self._counters(admin_client)

        assert after["families_without_schema"] - before["families_without_schema"] == 2

    def test_family_with_only_a_building_version_has_no_schema(
        self, admin_client, db_session, factories
    ):
        """Текущая версия — `frozen`; версия `building` схемой семьи не делает."""
        family = _schemaless_family(db_session)
        before = self._counters(admin_client)

        _building(db_session, family)
        after = self._counters(admin_client)

        assert after["families_without_schema"] == before["families_without_schema"]

    def test_archived_contexts_are_not_counted(self, admin_client, db_session, factories):
        scene = _two_families(db_session, factories, titles=("Пол 1", "Пол 2"))
        for context_id in scene.context_ids:
            _with_variant(db_session, scene, context_id, scene.family)
            _set_pending(db_session, factories, context_id, scene.family_b)
        before = self._counters(admin_client)

        db_session.execute(
            sa.update(CatalogContext)
            .where(CatalogContext.id == scene.context_ids[0])
            .values(archived_at=dt.datetime.now(dt.UTC))
        )
        db_session.expire_all()
        after = self._counters(admin_client)

        assert before["contexts_with_variant"] - after["contexts_with_variant"] == 1
        assert before["contexts_pending"] - after["contexts_pending"] == 1


# ---------------------------------------------------------------------------
#  Фильтры контекстов: в SQL, до пагинации
# ---------------------------------------------------------------------------

class TestContextFilters:
    def _scene(self, db, factories):
        """Пять контекстов: три с вариантом (у двух — ожидание, у двух — «к
        делению» ровно на двух из трёх вариантных), два без варианта."""
        scene = _two_families(db, factories, titles=tuple(f"Пол {n}" for n in range(1, 6)))
        with_variant = scene.context_ids[:3]
        for context_id in with_variant:
            _with_variant(db, scene, context_id, scene.family)
        for context_id in with_variant[:2]:
            _set_pending(db, factories, context_id, scene.family_b)
        for context_id in with_variant[1:]:
            db.execute(
                sa.update(CatalogContext)
                .where(CatalogContext.id == context_id)
                .values(variant_split_hint="path_conflict")
            )
        db.flush()
        scene.with_variant = with_variant
        scene.without_variant = scene.context_ids[3:]
        return scene

    def _list(self, client, **params):
        response = client.get(f"{BASE}/contexts", params=params)
        assert response.status_code == 200, response.text
        return response.json()

    def test_variant_state_with_counts_all_pages(self, admin_client, db_session, factories):
        scene = self._scene(db_session, factories)

        first = self._list(admin_client, variant_state="with", limit=2, offset=0)
        second = self._list(admin_client, variant_state="with", limit=2, offset=2)

        assert first["total"] == second["total"] == 3
        assert (len(first["items"]), len(second["items"])) == (2, 1)
        assert {i["id"] for i in first["items"] + second["items"]} == set(scene.with_variant)

    def test_variant_state_without(self, admin_client, db_session, factories):
        scene = self._scene(db_session, factories)

        body = self._list(admin_client, variant_state="without", limit=1)

        assert body["total"] == 2
        assert len(body["items"]) == 1
        assert body["items"][0]["id"] in scene.without_variant

    def test_pending_true_and_false(self, admin_client, db_session, factories):
        scene = self._scene(db_session, factories)

        pending = self._list(admin_client, pending="true", limit=1)
        not_pending = self._list(admin_client, pending="false", limit=2)

        assert pending["total"] == 2 and len(pending["items"]) == 1
        assert not_pending["total"] == 3 and len(not_pending["items"]) == 2
        every = self._list(admin_client, pending="true", limit=10)
        assert {i["id"] for i in every["items"]} == set(scene.with_variant[:2])

    def test_split_hint_true_and_false(self, admin_client, db_session, factories):
        scene = self._scene(db_session, factories)

        hinted = self._list(admin_client, split_hint="true", limit=1)
        plain = self._list(admin_client, split_hint="false", limit=10)

        assert hinted["total"] == 2 and len(hinted["items"]) == 1
        assert plain["total"] == 3
        every = self._list(admin_client, split_hint="true", limit=10)
        assert {i["id"] for i in every["items"]} == set(scene.with_variant[1:])

    def test_filters_combine(self, admin_client, db_session, factories):
        scene = self._scene(db_session, factories)

        body = self._list(admin_client, pending="true", split_hint="true", limit=10)

        assert body["total"] == 1
        assert [i["id"] for i in body["items"]] == [scene.with_variant[1]]

    def test_unknown_variant_state_is_422(self, admin_client):
        response = admin_client.get(f"{BASE}/contexts", params={"variant_state": "maybe"})

        assert response.status_code == 422

    def test_without_the_filters_nothing_is_narrowed(self, admin_client, db_session, factories):
        self._scene(db_session, factories)

        assert self._list(admin_client, limit=1)["total"] == 5


# ---------------------------------------------------------------------------
#  Карточка контекста: вариант
# ---------------------------------------------------------------------------

class TestContextCard:
    def _context_with_variant(self, db, factories):
        world = _world(db, factories)
        first = _variant_with_values(db, world.family, world.schema, ["50 мм", None])
        _attach_variant(db, world.context_id, first)
        thickness, material = world.parameters[1], world.parameters[2]
        db.add_all(
            [
                ContextParameterValue(
                    context_id=world.context_id, schema_id=world.schema.id,
                    parameter_id=thickness, value_id=world.values[(1, "50 мм")], source="name",
                ),
                ContextParameterValue(
                    context_id=world.context_id, schema_id=world.schema.id,
                    parameter_id=material, value_id=None, source="none",
                ),
            ]
        )
        db.flush()
        world.variant = first
        return world

    def test_card_without_a_variant(self, admin_client, db_session, factories):
        scene = _two_families(db_session, factories)

        card = admin_client.get(f"{BASE}/contexts/{scene.context_ids[0]}").json()

        assert card["variant"] == {
            "variant_id": None, "values": [], "split_hint": False, "pending": None,
            "values_job_status": None,
        }

    def test_values_carry_their_source(self, admin_client, db_session, factories):
        world = self._context_with_variant(db_session, factories)

        card = admin_client.get(f"{BASE}/contexts/{world.context_id}").json()

        assert card["variant"]["variant_id"] == world.variant.id
        assert card["variant"]["split_hint"] is False
        assert card["variant"]["pending"] is None
        assert card["variant"]["values"] == [
            {
                "parameter_id": world.parameters[1], "ordinal": 1, "name": "Толщина",
                "value_id": world.values[(1, "50 мм")], "value": "50 мм", "source": "name",
            },
            {
                "parameter_id": world.parameters[2], "ordinal": 2, "name": "Материал",
                "value_id": None, "value": None, "source": "none",
            },
        ]

    def test_split_hint_is_a_flag(self, admin_client, db_session, factories):
        world = self._context_with_variant(db_session, factories)
        db_session.execute(
            sa.update(CatalogContext)
            .where(CatalogContext.id == world.context_id)
            .values(variant_split_hint="path_conflict")
        )
        db_session.expire_all()

        card = admin_client.get(f"{BASE}/contexts/{world.context_id}").json()

        assert card["variant"]["split_hint"] is True

    def test_pending_of_a_person(self, admin_client, db_session, factories):
        world = self._context_with_variant(db_session, factories)
        other = _active_other_family(db_session, factories)
        _set_pending(db_session, factories, world.context_id, other, source="manual")

        pending = admin_client.get(f"{BASE}/contexts/{world.context_id}").json()["variant"][
            "pending"
        ]

        assert pending["family_id"] == other.id
        assert pending["family_title"] == other.title
        assert pending["source"] == "manual"
        assert isinstance(pending["by"], int)
        assert pending["threshold"] is None
        assert pending["suggestion_id"] is None
        assert isinstance(pending["at"], str)

    def test_pending_of_the_rule_carries_the_threshold_as_a_string(
        self, admin_client, db_session, factories
    ):
        world = self._context_with_variant(db_session, factories)
        other = _active_other_family(db_session, factories)
        suggestion = _set_pending(
            db_session, factories, world.context_id, other, source="auto_suggestion",
            threshold="0.95",
        )

        pending = admin_client.get(f"{BASE}/contexts/{world.context_id}").json()["variant"][
            "pending"
        ]

        assert (pending["source"], pending["threshold"]) == ("auto_suggestion", "0.95")
        assert pending["suggestion_id"] == suggestion.id
        assert pending["by"] is None

    def test_pending_without_a_variant_is_shown(self, admin_client, db_session, factories):
        world = _world(db_session, factories)
        other = _active_other_family(db_session, factories)
        _set_pending(db_session, factories, world.context_id, other, source="manual")

        variant = admin_client.get(f"{BASE}/contexts/{world.context_id}").json()["variant"]

        assert variant["variant_id"] is None and variant["values"] == []
        assert variant["pending"]["family_id"] == other.id


class TestContextCardValuesJob:
    """`variant.values_job_status` — статус живого задания значений контекста (спека
    вариантов §2.12): `pending`, `running`, `privacy_hold`, `error`; без такого задания
    — `None`. Закрытые задания (`done`, `cancelled`) и задания других видов и контекстов
    не считаются."""

    def _job(self, db, world, *, status, kind="context_values", context_id=None, hash_suffix=""):
        import uuid

        extra = {}
        if status == "running":
            extra["claim_token"] = uuid.uuid4()
        if status == "privacy_hold":
            extra["privacy_matches"] = [{"text": "x", "kind": "name", "where": "context"}]
        if status == "cancelled":
            extra["cancel_reason"] = "input_changed"
        job = SemanticJob(
            kind=kind,
            context_id=context_id or world.context_id,
            schema_id=world.schema.id if kind != "family_suggestion" else None,
            request_hash=f"hash-{kind}-{status}-{hash_suffix}-{uuid.uuid4().hex}",
            status=status,
            next_attempt_at=dt.datetime.now(dt.UTC),
            prompt_version="1",
            model_requested=app_settings.SEMANTIC_MODEL,
            place_dictionary_version=1,
            candidates_hash="c",
            prefix_hash="p",
            input_hash="i",
            response_schema_version="1",
            serialization_version="1",
            **extra,
        )
        db.add(job)
        db.flush()
        return job

    def _status(self, client, context_id):
        return client.get(f"{BASE}/contexts/{context_id}").json()["variant"]["values_job_status"]

    def test_none_without_a_job(self, admin_client, db_session, factories):
        world = _world(db_session, factories)

        assert self._status(admin_client, world.context_id) is None

    @pytest.mark.parametrize("status", ["pending", "running", "privacy_hold", "error"])
    def test_each_live_status_comes_through(self, admin_client, db_session, factories, status):
        world = _world(db_session, factories)
        self._job(db_session, world, status=status)

        assert self._status(admin_client, world.context_id) == status

    @pytest.mark.parametrize("status", ["done", "cancelled"])
    def test_closed_jobs_give_none(self, admin_client, db_session, factories, status):
        world = _world(db_session, factories)
        self._job(db_session, world, status=status)

        assert self._status(admin_client, world.context_id) is None

    def test_a_job_of_another_kind_is_ignored(self, admin_client, db_session, factories):
        world = _world(db_session, factories)
        self._job(db_session, world, status="pending", kind="family_suggestion")

        assert self._status(admin_client, world.context_id) is None

    def test_a_job_of_another_context_is_ignored(self, admin_client, db_session, factories):
        world = _world(db_session, factories)
        scene = _two_families(db_session, factories)
        self._job(db_session, world, status="pending", context_id=scene.context_ids[0])

        assert self._status(admin_client, world.context_id) is None

    def test_the_newest_of_two_live_jobs_wins(self, admin_client, db_session, factories):
        world = _world(db_session, factories)
        self._job(db_session, world, status="error")
        self._job(db_session, world, status="pending")

        assert self._status(admin_client, world.context_id) == "pending"


def _active_other_family(db, factories):
    user = factories.UserFactory.create()
    return _active_family(db, title=f"Другая {_uid()}", unit_name=None, actor_id=user.id)

