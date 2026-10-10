"""API экрана «Предложения» — маршруты `/api/v1/semantic` из спеки
`2026-09-28-semantic-suggestions-design.md` §2.13 (план, задача 13): чтение
очередей, заданий и шапки (`crud/semantic_queue.py`) и решения `admin` поверх
сервисов задачи 12 (`routers/semantic.py`).

Сами сервисы решений здесь заново не проверяются (`test_semantic_queue_decisions.py`)
— только HTTP-слой: права на КАЖДОМ маршруте, форма ответов, трансляция отказов
(`DecisionConflict` -> `409` с кодом, `LookupError` -> `404`, пустые имя и
определение -> `422`), обязательность `unit_id` в теле, число запросов чтения.

Один тест — одно строго отличающееся свойство: границы полос, каждый вид
владельца, каждая причина расхождения — отдельные входы. Помощники — ЛОКАЛЬНАЯ
копия помощников соседних наборов.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy import event

import services.semantic_decisions as decisions
from config import settings as app_settings
from models import (
    CatalogContext,
    ContextMember,
    FamilyParameterSchema,
    FamilySuggestion,
    SemanticJob,
    SemanticJobAttempt,
    SemanticReconcileBatch,
    SemanticWorkerState,
    WorkCategory,
    WorkFamily,
)
from services.context_routing import route_position
from services.semantic_privacy import build_privacy_dictionary, find_privacy_matches
from services.semantic_request import load_request_material, render_context_request
from services.work_families import activate_family, assign_family, create_family
from tests.factories import seed_category_id

pytestmark = pytest.mark.integration

BASE = "/api/v1/semantic"

#: Маршруты экрана «Предложения» с подставленными id — НЕЗАВИСИМО от `app.routes`
#: (тот же приём, что `TWENTY_ONE_ROUTES` в `test_semantic_api.py`): забытый
#: `require_admin` на одном маршруте ловится перебором, а не общей защитой.
QUEUE_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", f"{BASE}/suggestions"),
    ("POST", f"{BASE}/suggestions/confirm"),
    ("POST", f"{BASE}/suggestions/1/reject"),
    ("POST", f"{BASE}/suggestions/1/other-family"),
    ("POST", f"{BASE}/suggestions/1/create-family"),
    ("GET", f"{BASE}/jobs?status=error"),
    ("POST", f"{BASE}/jobs/1/retry"),
    ("POST", f"{BASE}/jobs/1/privacy-release"),
    ("POST", f"{BASE}/jobs/1/privacy-decline"),
    ("POST", f"{BASE}/unit-privacy-release"),
    ("GET", f"{BASE}/status"),
    ("POST", f"{BASE}/unit-reask/preview"),
    ("POST", f"{BASE}/unit-reask"),
    ("POST", f"{BASE}/reask-all/preview"),
    ("POST", f"{BASE}/reask-all"),
    ("POST", f"{BASE}/batches/1/preview"),
    ("POST", f"{BASE}/batches/1/approve"),
    ("POST", f"{BASE}/batches/1/discard"),
    ("POST", f"{BASE}/worker/resume"),
)
assert len(QUEUE_ROUTES) == 19

#: Набор совпадений, которого у задержанного задания нет.
_OTHER_MATCHES = [{"text": "иное имя", "kind": "contractor", "where": "context"}]


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _proposal(factories):
    estimate = factories.EstimateFactory.create()
    lot = factories.LotFactory.create(estimate=estimate)
    return factories.ProposalFactory.create(lot=lot)


def _unit_id(db, code):
    from services.unit_resolution import UnitResolver

    return UnitResolver(db).resolve(code).unit_id


def _active_family(db, *, title, unit_name, actor_id, definition="Определение семьи"):
    fam = create_family(db, title=title, unit_name=unit_name, definition=definition, actor_id=actor_id, family_category_id=seed_category_id(db))
    family = activate_family(db, family_id=fam.id, actor_id=actor_id)
    # Семья уже со схемой: сцены этого файла проверяют задания предложений, а
    # активная семья без схемы получала бы ещё и задание схемы.
    db.add(
        FamilyParameterSchema(
            family_id=family.id, version=1, status="frozen", origin="model",
            frozen_at=dt.datetime.now(dt.UTC),
        )
    )
    db.flush()
    return family


def _simple_context(db, factories, proposal, *, unit_id, title) -> int:
    cp = factories.CatalogPositionFactory.create(unit_id=unit_id, standard_job_title=title)
    position = factories.PositionItemFactory.create(
        proposal=proposal, is_chapter=False, job_title_in_proposal=title, catalog_position_id=cp.id
    )
    return route_position(db, position_item_id=position.id).context_id


def _shared_context(db, factories, estimates, *, unit_id, title) -> int:
    """Один контекст с членами из каждой из смет `estimates` (одна каталожная
    строка -> одна корзина -> один контекст по умолчанию)."""
    cp = factories.CatalogPositionFactory.create(unit_id=unit_id, standard_job_title=title)
    context_id = None
    for estimate in estimates:
        lot = factories.LotFactory.create(estimate=estimate)
        if estimate.round_id is not None:
            proposal = factories.ProposalFactory.create(lot=lot, contractor=None, is_baseline=True)
        else:
            proposal = factories.ProposalFactory.create(lot=lot)
        position = factories.PositionItemFactory.create(
            proposal=proposal, is_chapter=False, job_title_in_proposal=title,
            catalog_position_id=cp.id,
        )
        routed = route_position(db, position_item_id=position.id).context_id
        assert context_id in (None, routed), "вход теста: члены обязаны попасть в один контекст"
        context_id = routed
    return context_id


def _leaf_category_id(db) -> int:
    """Лист классификатора (не использованный как `parent_id`) — тот же приём,
    что `test_semantic_queue_material.py::_leaf_category_ids`."""
    used_as_parent = sa.select(WorkCategory.parent_id).where(WorkCategory.parent_id.is_not(None))
    return db.execute(
        sa.select(WorkCategory.id)
        .where(WorkCategory.id.not_in(used_as_parent))
        .order_by(WorkCategory.sort_order)
        .limit(1)
    ).scalar_one()


def _rendered(db, context_id):
    material = load_request_material(db, [context_id])[context_id]
    return render_context_request(material, settings=app_settings)


class _Scene:
    pass


def _scene(db, factories, admin, *, titles=("Устройство пола",), unit="M2") -> _Scene:
    """Активная семья единицы и по контексту на каждое название."""
    scene = _Scene()
    scene.user = admin
    scene.unit_id = _unit_id(db, unit) if unit else None
    scene.unit = unit
    scene.family = _active_family(db, title="Семья пола", unit_name=unit, actor_id=admin.id)
    scene.proposal = _proposal(factories)
    scene.context_ids = [
        _simple_context(db, factories, scene.proposal, unit_id=scene.unit_id, title=t)
        for t in titles
    ]
    return scene


def _make_job(
    db, context_id, *, status="pending", request_hash=None, unit_id=None, privacy_matches=None,
    last_error_class=None, retry_generation=0,
) -> SemanticJob:
    rendered = _rendered(db, context_id)
    job = SemanticJob(
        context_id=context_id,
        request_hash=request_hash or rendered.request_hash,
        status=status,
        unit_id=unit_id,
        privacy_matches=privacy_matches,
        last_error_class=last_error_class,
        retry_generation=retry_generation,
        next_attempt_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1),
        prompt_version="1",
        model_requested=app_settings.SEMANTIC_MODEL,
        place_dictionary_version=rendered.place_dictionary_version,
        candidates_hash=rendered.candidates_hash,
        prefix_hash=rendered.prefix_hash,
        input_hash=rendered.input_hash,
        response_schema_version="1",
        serialization_version="1",
    )
    db.add(job)
    db.flush()
    return job


def _make_attempt(db, job_id, *, error_text=None, validation_error=None, cost_usd=None):
    attempt = SemanticJobAttempt(
        job_id=job_id,
        claim_token=uuid.uuid4(),
        retry_generation=0,
        started_at=dt.datetime.now(dt.UTC),
        reserve_usd=Decimal("0.01"),
        prefix_hash="prefix-hash",
        privacy_dictionary_hash="dict-hash",
        error_text=error_text,
        validation_error=validation_error,
        cost_usd=cost_usd,
    )
    db.add(attempt)
    db.flush()
    return attempt


def _published(
    db, context_id, *, family_id, confidence="0.9", new_family_name=None, is_published=True,
    decision=None, decided_by=None, superseded=False,
) -> FamilySuggestion:
    """Предложение на ТЕКУЩИЙ отпечаток контекста (по умолчанию опубликованное);
    `superseded` — на прежний отпечаток (у контекста уже есть текущее задание)."""
    job = _make_job(
        db, context_id, status="done",
        request_hash=f"прежний-{uuid.uuid4().hex}" if superseded else None,
    )
    attempt = _make_attempt(db, job.id)
    suggestion = FamilySuggestion(
        context_id=context_id,
        job_id=job.id,
        attempt_id=attempt.id,
        request_hash=job.request_hash,
        candidates_hash=job.candidates_hash,
        candidates_snapshot=[],
        family_id=family_id,
        new_family_name=(new_family_name or "Новая семья") if family_id is None else None,
        confidence=Decimal(confidence),
        reason="проверочная причина",
        is_published=is_published,
        unpublished_reason=None if is_published else "stale_fingerprint",
        decision=decision,
        decided_by=decided_by,
        decided_at=dt.datetime.now(dt.UTC) if decision else None,
    )
    db.add(suggestion)
    db.flush()
    job.result_suggestion_id = suggestion.id
    db.flush()
    return suggestion


def _hold_job(db, context_id, *, unit_id=None) -> SemanticJob:
    """Задание в `privacy_hold` с ТЕКУЩИМ набором совпадений (считает поиск по
    словарю, как захват)."""
    rendered = _rendered(db, context_id)
    found = find_privacy_matches(build_privacy_dictionary(db), rendered)
    matches = [{"text": m.text, "kind": m.kind, "where": m.where} for m in found]
    assert matches, "вход теста: у контекста обязано быть совпадение"
    return _make_job(db, context_id, status="privacy_hold", unit_id=unit_id, privacy_matches=matches)


@contextlib.contextmanager
def _capturing_sql(session):
    statements: list[str] = []

    def _listener(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    connection = session.connection()
    event.listen(connection, "before_cursor_execute", _listener)
    try:
        yield statements
    finally:
        event.remove(connection, "before_cursor_execute", _listener)


def _detail(response) -> dict:
    body = response.json()["detail"]
    assert isinstance(body, dict), body
    return body


def _rows(payload) -> list[dict]:
    return [row for group in payload["groups"] for row in group["rows"]]


# ---------------------------------------------------------------------------
#  Права: КАЖДЫЙ маршрут
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method,path", QUEUE_ROUTES)
def test_member_rejected_on_every_queue_route(method, path, member_client):
    response = member_client.request(method, path)
    assert response.status_code == 403, f"{method} {path} -> {response.status_code}"


@pytest.mark.parametrize("method,path", QUEUE_ROUTES)
def test_unauthenticated_rejected_on_every_queue_route(method, path, anon_client):
    response = anon_client.request(method, path)
    assert response.status_code == 401, f"{method} {path} -> {response.status_code}"


# ---------------------------------------------------------------------------
#  Очередь «Семья из списка»
# ---------------------------------------------------------------------------

class TestListQueue:
    def test_groups_rows_by_suggested_family_with_row_shape(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user, titles=("Пол А", "Пол Б", "Пол В"))
        other = _active_family(
            db_session, title="Другая семья пола", unit_name="M2", actor_id=admin_client.user.id
        )
        a, b, c = scene.context_ids
        s_a = _published(db_session, a, family_id=scene.family.id, confidence="0.91")
        s_b = _published(db_session, b, family_id=scene.family.id, confidence="0.95")
        _published(db_session, c, family_id=other.id, confidence="0.85")

        response = admin_client.get(f"{BASE}/suggestions", params={"queue": "list"})

        assert response.status_code == 200
        payload = response.json()
        assert payload["queue"] == "list"
        groups = {g["family_id"]: g for g in payload["groups"]}
        assert set(groups) == {scene.family.id, other.id}
        first = groups[scene.family.id]
        assert (first["family_title"], first["unit_code"], first["total"]) == (
            "Семья пола", "M2", 2,
        )
        assert [row["suggestion_id"] for row in first["rows"]] == [s_b.id, s_a.id]
        assert first["rows"][0] == {
            "suggestion_id": s_b.id,
            "context_id": b,
            "title": "Пол Б",
            "unit_code": "M2",
            "article": None,
            "path": [],
            "confidence": "0.95",
            "reason": "проверочная причина",
            "multi_owner": False,
            "previously_rejected": None,
        }

    @pytest.mark.parametrize("case", ["unpublished", "decided_but_published"])
    def test_unpublished_and_decided_suggestions_are_not_shown(
        self, admin_client, db_session, factories, case
    ):
        scene = _scene(db_session, factories, admin_client.user, titles=("Видимое", "Скрытое"))
        shown, hidden = scene.context_ids
        visible = _published(db_session, shown, family_id=scene.family.id)
        if case == "unpublished":
            _published(db_session, hidden, family_id=scene.family.id, is_published=False)
        else:
            _published(
                db_session, hidden, family_id=scene.family.id,
                decision="accepted", decided_by=admin_client.user.id,
            )

        payload = admin_client.get(f"{BASE}/suggestions", params={"queue": "list"}).json()

        assert [row["suggestion_id"] for row in _rows(payload)] == [visible.id]

    def test_unresolved_suggestion_on_a_superseded_fingerprint_is_not_listed(
        self, admin_client, db_session, factories
    ):
        """Опубликованное нерешённое предложение, отпечаток запроса которого с тех
        пор изменился, решить нельзя: в очереди его нет, а соседнее на текущем
        отпечатке — на месте."""
        scene = _scene(db_session, factories, admin_client.user, titles=("Текущее", "Прежнее"))
        current_ctx, old_ctx = scene.context_ids
        current = _published(db_session, current_ctx, family_id=scene.family.id)
        _published(db_session, old_ctx, family_id=scene.family.id, superseded=True)

        payload = admin_client.get(f"{BASE}/suggestions", params={"queue": "list"}).json()

        assert [row["suggestion_id"] for row in _rows(payload)] == [current.id]
        assert [g["total"] for g in payload["groups"]] == [1]

    def test_unresolved_suggestion_is_dropped_when_the_family_list_changed_after_it(
        self, admin_client, db_session, factories
    ):
        """Тот же случай от причины: после ответа модели в единице появилась ещё
        одна активная семья — список кандидатов, а с ним и запрос, стал другим."""
        scene = _scene(db_session, factories, admin_client.user)
        _published(db_session, scene.context_ids[0], family_id=scene.family.id)
        assert len(_rows(admin_client.get(f"{BASE}/suggestions").json())) == 1
        _active_family(
            db_session, title="Новая активная", unit_name="M2", actor_id=admin_client.user.id
        )

        payload = admin_client.get(f"{BASE}/suggestions", params={"queue": "list"}).json()

        assert payload["groups"] == []

    def test_unresolved_suggestion_of_a_context_that_is_no_longer_applicable_is_not_listed(
        self, admin_client, db_session, factories
    ):
        """Отпечаток запроса у такого контекста остался прежним, отличается только
        применимость (контекст признан неприменимым): в очереди предложения нет."""
        scene = _scene(db_session, factories, admin_client.user, titles=("Свободный", "Занятый"))
        free_ctx, taken_ctx = scene.context_ids
        free = _published(db_session, free_ctx, family_id=scene.family.id)
        _published(db_session, taken_ctx, family_id=scene.family.id)
        db_session.execute(
            sa.update(CatalogContext)
            .where(CatalogContext.id == taken_ctx)
            .values(semantic_state="NOT_APPLICABLE")
        )
        db_session.expire_all()

        payload = admin_client.get(f"{BASE}/suggestions", params={"queue": "list"}).json()

        assert [row["suggestion_id"] for row in _rows(payload)] == [free.id]

    def test_current_suggestions_of_two_units_are_both_listed(
        self, admin_client, db_session, factories
    ):
        """Отпечаток сверяется по списку семей СВОЕЙ единицы: текущие предложения
        двух единиц с разными списками семей оба на месте."""
        scene = _scene(db_session, factories, admin_client.user, titles=("Пол А",))
        pcs_unit = _unit_id(db_session, "PCS")
        pcs_family = _active_family(
            db_session, title="Семья штук", unit_name="PCS", actor_id=admin_client.user.id
        )
        pcs_ctx = _simple_context(
            db_session, factories, scene.proposal, unit_id=pcs_unit, title="Штука А"
        )
        m2 = _published(db_session, scene.context_ids[0], family_id=scene.family.id)
        pcs = _published(db_session, pcs_ctx, family_id=pcs_family.id)

        payload = admin_client.get(f"{BASE}/suggestions", params={"queue": "list"}).json()

        assert sorted(row["suggestion_id"] for row in _rows(payload)) == sorted([m2.id, pcs.id])

    def test_unit_filter_keeps_only_contexts_of_that_unit(
        self, admin_client, db_session, factories
    ):
        m2 = _scene(db_session, factories, admin_client.user, titles=("Площадная",), unit="M2")
        pcs_family = _active_family(
            db_session, title="Штучная семья", unit_name="PCS", actor_id=admin_client.user.id
        )
        pcs_ctx = _simple_context(
            db_session, factories, m2.proposal, unit_id=_unit_id(db_session, "PCS"), title="Штучная"
        )
        s_m2 = _published(db_session, m2.context_ids[0], family_id=m2.family.id)
        s_pcs = _published(db_session, pcs_ctx, family_id=pcs_family.id)

        by_m2 = admin_client.get(f"{BASE}/suggestions", params={"unit": m2.unit_id}).json()
        by_pcs = admin_client.get(
            f"{BASE}/suggestions", params={"unit": _unit_id(db_session, "PCS")}
        ).json()

        assert [r["suggestion_id"] for r in _rows(by_m2)] == [s_m2.id]
        assert [r["suggestion_id"] for r in _rows(by_pcs)] == [s_pcs.id]

    def test_unit_none_filter_keeps_only_contexts_without_unit(
        self, admin_client, db_session, factories
    ):
        with_unit = _scene(db_session, factories, admin_client.user, titles=("С единицей",))
        no_unit = _scene(db_session, factories, admin_client.user, titles=("Без единицы",), unit=None)
        assert no_unit.unit_id is None
        s_with = _published(db_session, with_unit.context_ids[0], family_id=with_unit.family.id)
        s_none = _published(db_session, no_unit.context_ids[0], family_id=no_unit.family.id)

        only_none = admin_client.get(f"{BASE}/suggestions", params={"unit": "none"}).json()
        everything = admin_client.get(f"{BASE}/suggestions").json()

        assert [r["suggestion_id"] for r in _rows(only_none)] == [s_none.id]
        assert {r["suggestion_id"] for r in _rows(everything)} == {s_with.id, s_none.id}

    @pytest.mark.parametrize("value", ["abc", "1.5", "-1", "None", "", "²", "٣"])
    def test_unit_filter_with_other_value_is_422(self, admin_client, value):
        response = admin_client.get(f"{BASE}/suggestions", params={"unit": value})
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "confidence,expected_band",
        [
            ("0.95", "high"),
            ("0.90", "high"),
            ("0.89", "mid"),
            ("0.70", "mid"),
            ("0.69", "low"),
        ],
    )
    def test_confidence_band_boundaries(
        self, admin_client, db_session, factories, confidence, expected_band
    ):
        scene = _scene(db_session, factories, admin_client.user)
        suggestion = _published(
            db_session, scene.context_ids[0], family_id=scene.family.id, confidence=confidence
        )

        for band in ("high", "mid", "low"):
            payload = admin_client.get(f"{BASE}/suggestions", params={"band": band}).json()
            ids = [r["suggestion_id"] for r in _rows(payload)]
            assert (ids == [suggestion.id]) is (band == expected_band), (confidence, band, ids)
        unfiltered = admin_client.get(f"{BASE}/suggestions").json()
        assert [g["band"] for g in unfiltered["groups"]] == [expected_band]

    def test_one_family_splits_into_groups_by_band_and_band_filter_selects_them(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user, titles=("Высокая", "Низкая"))
        high_ctx, low_ctx = scene.context_ids
        high = _published(db_session, high_ctx, family_id=scene.family.id, confidence="0.95")
        low = _published(db_session, low_ctx, family_id=scene.family.id, confidence="0.5")

        payload = admin_client.get(f"{BASE}/suggestions").json()
        only_high = admin_client.get(f"{BASE}/suggestions", params={"band": "high"}).json()

        assert [(g["family_id"], g["band"], g["total"]) for g in payload["groups"]] == [
            (scene.family.id, "high", 1), (scene.family.id, "low", 1),
        ]
        assert [[r["suggestion_id"] for r in g["rows"]] for g in payload["groups"]] == [
            [high.id], [low.id],
        ]
        assert [(g["band"], g["total"]) for g in only_high["groups"]] == [("high", 1)]
        assert [r["suggestion_id"] for r in _rows(only_high)] == [high.id]

    def test_confidence_is_returned_as_decimal_string_without_float_rounding(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user)
        _published(db_session, scene.context_ids[0], family_id=scene.family.id, confidence="0.123")

        payload = admin_client.get(f"{BASE}/suggestions").json()

        assert _rows(payload)[0]["confidence"] == "0.123"

    @pytest.mark.parametrize(
        "owners,expected",
        [
            (("contract", "contract"), True),
            (("offer", "offer"), True),
            (("contract", "offer"), True),
            (("same_contract_twice",), False),
            (("offer_and_round_of_same_tender",), False),
        ],
    )
    def test_multi_owner_flag_and_filter(
        self, admin_client, db_session, factories, owners, expected
    ):
        scene = _scene(db_session, factories, admin_client.user, titles=("Не разделяемая",))
        estimates = self._estimates(db_session, factories, owners)
        shared = _shared_context(
            db_session, factories, estimates, unit_id=scene.unit_id, title="Разделяемая"
        )
        suggestion = _published(db_session, shared, family_id=scene.family.id)
        _published(db_session, scene.context_ids[0], family_id=scene.family.id)

        everything = admin_client.get(f"{BASE}/suggestions").json()
        only_multi = admin_client.get(f"{BASE}/suggestions", params={"multi_owner": "true"}).json()

        flag = {r["suggestion_id"]: r["multi_owner"] for r in _rows(everything)}
        assert flag[suggestion.id] is expected
        assert [r["suggestion_id"] for r in _rows(only_multi)] == ([suggestion.id] if expected else [])

    @staticmethod
    def _estimates(db, factories, owners):
        def offer_estimate(tender):
            rnd = factories.TenderRoundFactory.create(tender=tender)
            offer = factories.OfferFactory.create(round=rnd)
            return factories.EstimateFactory.create(contract=None, offer=offer), rnd

        estimates = []
        if owners == ("same_contract_twice",):
            contract = factories.ContractFactory.create()
            estimates.append(factories.EstimateFactory.create(contract=contract))
            estimates.append(factories.EstimateFactory.create(contract=contract, amendment_no=1))
        elif owners == ("offer_and_round_of_same_tender",):
            tender = factories.TenderFactory.create()
            est, rnd = offer_estimate(tender)
            estimates.append(est)
            estimates.append(factories.EstimateFactory.create(contract=None, round=rnd))
        else:
            for kind in owners:
                if kind == "contract":
                    estimates.append(factories.EstimateFactory.create())
                else:
                    estimates.append(offer_estimate(factories.TenderFactory.create())[0])
        db.flush()
        return estimates

    def test_previously_rejected_mark_when_same_family_was_rejected(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user)
        ctx = scene.context_ids[0]
        _published(
            db_session, ctx, family_id=scene.family.id, is_published=False,
            decision="rejected", decided_by=admin_client.user.id, superseded=True,
        )
        current = _published(db_session, ctx, family_id=scene.family.id)

        payload = admin_client.get(f"{BASE}/suggestions").json()

        row = _rows(payload)[0]
        assert row["suggestion_id"] == current.id
        mark = row["previously_rejected"]
        assert (mark["family_id"], mark["family_title"]) == (scene.family.id, "Семья пола")
        assert dt.datetime.fromisoformat(mark["decided_at"]).tzinfo is not None

    def test_no_mark_when_rejected_suggestion_named_another_family(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user)
        other = _active_family(
            db_session, title="Иная семья", unit_name="M2", actor_id=admin_client.user.id
        )
        ctx = scene.context_ids[0]
        _published(
            db_session, ctx, family_id=other.id, is_published=False,
            decision="rejected", decided_by=admin_client.user.id, superseded=True,
        )
        _published(db_session, ctx, family_id=scene.family.id)

        payload = admin_client.get(f"{BASE}/suggestions").json()

        assert _rows(payload)[0]["previously_rejected"] is None

    def test_no_mark_when_same_family_suggestion_was_decided_differently(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user)
        ctx = scene.context_ids[0]
        _published(
            db_session, ctx, family_id=scene.family.id, is_published=False,
            decision="other_family", decided_by=admin_client.user.id, superseded=True,
        )
        _published(db_session, ctx, family_id=scene.family.id)

        payload = admin_client.get(f"{BASE}/suggestions").json()

        assert _rows(payload)[0]["previously_rejected"] is None

    def test_previously_rejected_mark_is_keyed_by_context_and_family_together(
        self, admin_client, db_session, factories
    ):
        """Отклонение семьи Б у первого контекста не помечает ни его строку с
        семьёй А, ни строку второго контекста с семьёй Б: пометка — по паре
        (контекст, семья), а не по контексту или по семье отдельно."""
        scene = _scene(db_session, factories, admin_client.user, titles=("Первая", "Вторая"))
        other = _active_family(
            db_session, title="Иная семья", unit_name="M2", actor_id=admin_client.user.id
        )
        first, second = scene.context_ids
        _published(
            db_session, first, family_id=other.id, is_published=False,
            decision="rejected", decided_by=admin_client.user.id, superseded=True,
        )
        _published(db_session, first, family_id=scene.family.id)
        _published(db_session, second, family_id=other.id)

        payload = admin_client.get(f"{BASE}/suggestions").json()

        marks = {row["context_id"]: row["previously_rejected"] for row in _rows(payload)}
        assert marks == {first: None, second: None}

    def test_previously_rejected_mark_carries_the_latest_rejection_date(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user)
        ctx = scene.context_ids[0]
        late = dt.datetime(2026, 9, 20, 12, 0, tzinfo=dt.UTC)
        early = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC)
        for decided_at in (late, early):  # позднее записано первым: порядок вставки не подсказка
            rejected = _published(
                db_session, ctx, family_id=scene.family.id, is_published=False,
                decision="rejected", decided_by=admin_client.user.id, superseded=True,
            )
            db_session.execute(
                sa.update(FamilySuggestion)
                .where(FamilySuggestion.id == rejected.id)
                .values(decided_at=decided_at)
            )
        _published(db_session, ctx, family_id=scene.family.id)

        payload = admin_client.get(f"{BASE}/suggestions").json()

        mark = _rows(payload)[0]["previously_rejected"]
        assert dt.datetime.fromisoformat(mark["decided_at"]) == late

    def test_groups_are_ordered_by_family_title(self, admin_client, db_session, factories):
        scene = _scene(db_session, factories, admin_client.user, titles=("Первая", "Вторая"))
        earlier_in_alphabet = _active_family(
            db_session, title="Армирование", unit_name="M2", actor_id=admin_client.user.id
        )
        first, second = scene.context_ids
        _published(db_session, first, family_id=scene.family.id)  # «Семья пола» записана раньше
        _published(db_session, second, family_id=earlier_in_alphabet.id)

        payload = admin_client.get(f"{BASE}/suggestions").json()

        assert [g["family_title"] for g in payload["groups"]] == ["Армирование", "Семья пола"]

    def test_row_carries_article_and_section_path(self, admin_client, db_session, factories):
        scene = _scene(db_session, factories, admin_client.user, titles=())
        category_id = _leaf_category_id(db_session)
        category = db_session.get(WorkCategory, category_id)
        root = factories.PositionItemFactory.create(
            proposal=scene.proposal, is_chapter=True, job_title_in_proposal="Секция 1",
        )
        leaf = factories.PositionItemFactory.create(
            proposal=scene.proposal, is_chapter=True, job_title_in_proposal="Этаж 2",
            chapter_item_id=root.id, work_category_id=category_id, category_source="file",
        )
        cp = factories.CatalogPositionFactory.create(unit_id=scene.unit_id, standard_job_title="Стяжка")
        position = factories.PositionItemFactory.create(
            proposal=scene.proposal, is_chapter=False, job_title_in_proposal="Стяжка",
            chapter_item_id=leaf.id, catalog_position_id=cp.id,
        )
        ctx = route_position(db_session, position_item_id=position.id).context_id
        _published(db_session, ctx, family_id=scene.family.id)

        payload = admin_client.get(f"{BASE}/suggestions").json()

        row = _rows(payload)[0]
        assert (row["article"], row["path"]) == (
            f"{category.code} {category.title}", ["Секция 1", "Этаж 2"],
        )

    def test_query_count_does_not_grow_with_row_count(self, admin_client, db_session, factories):
        scene = _scene(db_session, factories, admin_client.user, titles=("Первая",))
        _published(db_session, scene.context_ids[0], family_id=scene.family.id)
        for i in range(19):
            ctx = _simple_context(
                db_session, factories, scene.proposal, unit_id=scene.unit_id, title=f"Строка {i}"
            )
            _published(db_session, ctx, family_id=scene.family.id)
        admin_client.get(f"{BASE}/suggestions")  # прогрев: загрузка пользователя сессией
        with _capturing_sql(db_session) as small:
            r_small = admin_client.get(f"{BASE}/suggestions")
        assert len(_rows(r_small.json())) == 20

        for i in range(100):
            ctx = _simple_context(
                db_session, factories, scene.proposal, unit_id=scene.unit_id, title=f"Ещё {i}"
            )
            _published(db_session, ctx, family_id=scene.family.id)
        with _capturing_sql(db_session) as large:
            r_large = admin_client.get(f"{BASE}/suggestions")
        assert len(_rows(r_large.json())) == 120

        assert len(small) == len(large), (len(small), len(large))

    def test_unknown_queue_value_is_rejected(self, admin_client):
        assert admin_client.get(f"{BASE}/suggestions", params={"queue": "other"}).status_code == 422


# ---------------------------------------------------------------------------
#  Очередь «Новая»
# ---------------------------------------------------------------------------

class TestNewQueue:
    def test_new_family_answer_is_listed_with_name_and_not_system(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user)
        s = _published(
            db_session, scene.context_ids[0], family_id=None, new_family_name="Кладка стен"
        )

        payload = admin_client.get(f"{BASE}/suggestions", params={"queue": "new"}).json()

        assert payload["queue"] == "new"
        assert [(i["suggestion_id"], i["new_family_name"], i["is_system"]) for i in payload["items"]] == [
            (s.id, "Кладка стен", False)
        ]

    @pytest.mark.parametrize("name", ["СИСТЕМА", "система"])
    def test_system_answer_is_flagged(self, admin_client, db_session, factories, name):
        scene = _scene(db_session, factories, admin_client.user)
        _published(db_session, scene.context_ids[0], family_id=None, new_family_name=name)

        payload = admin_client.get(f"{BASE}/suggestions", params={"queue": "new"}).json()

        assert [i["is_system"] for i in payload["items"]] == [True]

    def test_family_from_list_answer_is_not_in_new_queue(self, admin_client, db_session, factories):
        scene = _scene(db_session, factories, admin_client.user)
        _published(db_session, scene.context_ids[0], family_id=scene.family.id)

        payload = admin_client.get(f"{BASE}/suggestions", params={"queue": "new"}).json()

        assert payload["items"] == []

    def test_context_of_unit_without_active_families_is_a_row_without_suggestion(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user)
        pcs = _unit_id(db_session, "PCS")
        bare = _simple_context(db_session, factories, scene.proposal, unit_id=pcs, title="Без семей")

        payload = admin_client.get(f"{BASE}/suggestions", params={"queue": "new"}).json()

        items = {i["context_id"]: i for i in payload["items"]}
        assert set(items) == {bare}, "контекст единицы с активной семьёй без предложения не показан"
        row = items[bare]
        assert (row["suggestion_id"], row["confidence"], row["reason"], row["new_family_name"]) == (
            None, None, None, None,
        )
        assert (row["title"], row["unit_code"], row["is_system"]) == ("Без семей", "PCS", False)

    def test_context_with_a_chapter_cycle_is_not_a_row_without_families(
        self, admin_client, db_session, factories
    ):
        """Цикл разделов — другая причина, чем «в единице нет активных семей»:
        строка с таким пояснением обманула бы."""
        scene = _scene(db_session, factories, admin_client.user)
        pcs = _unit_id(db_session, "PCS")
        bare = _simple_context(db_session, factories, scene.proposal, unit_id=pcs, title="Без семей")
        cp = factories.CatalogPositionFactory.create(unit_id=pcs, standard_job_title="Цикл")
        outer = factories.PositionItemFactory.create(
            proposal=scene.proposal, is_chapter=True, job_title_in_proposal="Внешний",
            chapter_number_in_proposal="1",
        )
        inner = factories.PositionItemFactory.create(
            proposal=scene.proposal, is_chapter=True, job_title_in_proposal="Внутренний",
            chapter_item_id=outer.id,
        )
        item = factories.PositionItemFactory.create(
            proposal=scene.proposal, is_chapter=False, job_title_in_proposal="Цикл",
            catalog_position_id=cp.id, chapter_item_id=inner.id,
        )
        cyclic = route_position(db_session, position_item_id=item.id).context_id
        outer.chapter_item_id = inner.id
        db_session.flush()
        db_session.expire_all()
        assert load_request_material(db_session, [cyclic])[cyclic].path_broken, "вход теста: цикл"

        payload = admin_client.get(f"{BASE}/suggestions", params={"queue": "new"}).json()

        assert {i["context_id"] for i in payload["items"]} == {bare}

    def test_unit_none_filter_applies_to_new_queue(self, admin_client, db_session, factories):
        with_unit = _scene(db_session, factories, admin_client.user, titles=("С единицей",))
        no_unit = _scene(db_session, factories, admin_client.user, titles=("Без единицы",), unit=None)
        _published(db_session, with_unit.context_ids[0], family_id=None)
        s_none = _published(db_session, no_unit.context_ids[0], family_id=None)

        payload = admin_client.get(
            f"{BASE}/suggestions", params={"queue": "new", "unit": "none"}
        ).json()

        assert [i["suggestion_id"] for i in payload["items"]] == [s_none.id]

    def test_unit_none_filter_applies_to_rows_without_families(
        self, admin_client, db_session, factories
    ):
        """`unit=none` у строк «в единице нет активных семей»: контекст без единицы
        (активной семьи без единицы нет) — в очереди, строка единицы шт — нет."""
        scene = _scene(db_session, factories, admin_client.user, titles=())
        no_unit = _simple_context(db_session, factories, scene.proposal, unit_id=None, title="Без единицы")
        pcs = _simple_context(
            db_session, factories, scene.proposal, unit_id=_unit_id(db_session, "PCS"), title="Штучная"
        )

        everything = admin_client.get(f"{BASE}/suggestions", params={"queue": "new"}).json()
        only_none = admin_client.get(
            f"{BASE}/suggestions", params={"queue": "new", "unit": "none"}
        ).json()

        assert {i["context_id"] for i in everything["items"]} == {no_unit, pcs}
        assert [i["context_id"] for i in only_none["items"]] == [no_unit]

    def test_unit_filter_applies_to_new_queue(self, admin_client, db_session, factories):
        scene = _scene(db_session, factories, admin_client.user)
        pcs = _unit_id(db_session, "PCS")
        _simple_context(db_session, factories, scene.proposal, unit_id=pcs, title="Штучная")
        s = _published(db_session, scene.context_ids[0], family_id=None)

        payload = admin_client.get(
            f"{BASE}/suggestions", params={"queue": "new", "unit": scene.unit_id}
        ).json()

        assert [i["suggestion_id"] for i in payload["items"]] == [s.id]

    def test_unit_filter_drops_new_answers_of_another_unit(self, admin_client, db_session, factories):
        """У единицы шт есть активная семья — её контекст строкой «без семей» не
        идёт, и отсеять его ответ «новая» может только фильтр по единице."""
        scene = _scene(db_session, factories, admin_client.user)
        _active_family(db_session, title="Штучная семья", unit_name="PCS", actor_id=admin_client.user.id)
        pcs_ctx = _simple_context(
            db_session, factories, scene.proposal, unit_id=_unit_id(db_session, "PCS"), title="Штучная"
        )
        m2 = _published(db_session, scene.context_ids[0], family_id=None)
        _published(db_session, pcs_ctx, family_id=None)

        payload = admin_client.get(
            f"{BASE}/suggestions", params={"queue": "new", "unit": scene.unit_id}
        ).json()

        assert [i["suggestion_id"] for i in payload["items"]] == [m2.id]

    @pytest.mark.parametrize("case", ["unpublished", "decided_but_published"])
    def test_unpublished_and_decided_new_answers_are_not_shown(
        self, admin_client, db_session, factories, case
    ):
        scene = _scene(db_session, factories, admin_client.user, titles=("Видимая", "Скрытая"))
        shown, hidden = scene.context_ids
        visible = _published(db_session, shown, family_id=None)
        if case == "unpublished":
            _published(db_session, hidden, family_id=None, is_published=False)
        else:
            _published(
                db_session, hidden, family_id=None,
                decision="family_created", decided_by=admin_client.user.id,
            )

        payload = admin_client.get(f"{BASE}/suggestions", params={"queue": "new"}).json()

        assert [i["suggestion_id"] for i in payload["items"]] == [visible.id]

    def test_unresolved_new_answer_on_a_superseded_fingerprint_is_not_shown(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user, titles=("Текущая", "Прежняя"))
        current_ctx, old_ctx = scene.context_ids
        current = _published(db_session, current_ctx, family_id=None)
        _published(db_session, old_ctx, family_id=None, superseded=True)

        payload = admin_client.get(f"{BASE}/suggestions", params={"queue": "new"}).json()

        assert [i["suggestion_id"] for i in payload["items"]] == [current.id]

    def test_unresolved_new_answer_is_dropped_when_the_family_list_changed_after_it(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user)
        _published(db_session, scene.context_ids[0], family_id=None)
        assert len(admin_client.get(f"{BASE}/suggestions", params={"queue": "new"}).json()["items"]) == 1
        _active_family(
            db_session, title="Новая активная", unit_name="M2", actor_id=admin_client.user.id
        )

        payload = admin_client.get(f"{BASE}/suggestions", params={"queue": "new"}).json()

        assert payload["items"] == []

    def test_unresolved_new_answer_of_a_context_that_is_no_longer_applicable_is_not_shown(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user, titles=("Свободная", "Занятая"))
        free_ctx, taken_ctx = scene.context_ids
        free = _published(db_session, free_ctx, family_id=None)
        _published(db_session, taken_ctx, family_id=None)
        db_session.execute(
            sa.update(CatalogContext)
            .where(CatalogContext.id == taken_ctx)
            .values(semantic_state="NOT_APPLICABLE")
        )
        db_session.expire_all()

        payload = admin_client.get(f"{BASE}/suggestions", params={"queue": "new"}).json()

        assert [i["suggestion_id"] for i in payload["items"]] == [free.id]

    def test_multi_owner_flag_and_filter_cover_answers_and_rows_without_families(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user, titles=("Одиночная",))
        pcs = _unit_id(db_session, "PCS")
        shared = _shared_context(
            db_session, factories,
            [factories.EstimateFactory.create(), factories.EstimateFactory.create()],
            unit_id=scene.unit_id, title="Разделяемая",
        )
        bare_shared = _shared_context(
            db_session, factories,
            [factories.EstimateFactory.create(), factories.EstimateFactory.create()],
            unit_id=pcs, title="Штучная разделяемая",
        )
        bare_single = _simple_context(
            db_session, factories, scene.proposal, unit_id=pcs, title="Штучная одиночная"
        )
        _published(db_session, shared, family_id=None)
        _published(db_session, scene.context_ids[0], family_id=None)

        everything = admin_client.get(f"{BASE}/suggestions", params={"queue": "new"}).json()
        only_multi = admin_client.get(
            f"{BASE}/suggestions", params={"queue": "new", "multi_owner": "true"}
        ).json()

        assert {i["context_id"]: i["multi_owner"] for i in everything["items"]} == {
            shared: True, scene.context_ids[0]: False, bare_shared: True, bare_single: False,
        }
        assert {i["context_id"] for i in only_multi["items"]} == {shared, bare_shared}

    @pytest.mark.parametrize(
        "case", ["archived", "assigned", "not_applicable", "system", "no_members"]
    )
    def test_row_without_families_requires_a_live_unassigned_work_context(
        self, admin_client, db_session, factories, case
    ):
        """Строка «в единице нет активных семей» — только у контекста, который
        был бы применим, будь у единицы семьи: каждое условие — свой вход."""
        admin = admin_client.user
        scene = _scene(db_session, factories, admin, titles=())
        pcs = _unit_id(db_session, "PCS")
        kept = _simple_context(db_session, factories, scene.proposal, unit_id=pcs, title="Остаётся")
        dropped = _simple_context(db_session, factories, scene.proposal, unit_id=pcs, title="Уходит")
        now = dt.datetime.now(dt.UTC)
        values = {
            "archived": {"archived_at": now},
            "not_applicable": {"semantic_state": "NOT_APPLICABLE"},
            "system": {
                "semantic_kind": "SYSTEM", "semantic_kind_source": "manual",
                "semantic_kind_by": admin.id, "semantic_kind_at": now,
            },
        }
        if case == "assigned":
            draft = create_family(
                db_session, title="Черновик штучной", unit_name="PCS",
                definition="черновик", actor_id=admin.id,
            )
            values["assigned"] = {
                "work_family_id": draft.id, "family_source": "manual",
                "family_by": admin.id, "family_at": now,
            }
        if case == "no_members":
            db_session.execute(sa.delete(ContextMember).where(ContextMember.context_id == dropped))
        else:
            db_session.execute(
                sa.update(CatalogContext).where(CatalogContext.id == dropped).values(**values[case])
            )
        db_session.expire_all()

        payload = admin_client.get(f"{BASE}/suggestions", params={"queue": "new"}).json()

        assert [i["context_id"] for i in payload["items"]] == [kept]

    def test_context_without_unit_is_not_bare_when_a_family_without_unit_is_active(
        self, admin_client, db_session, factories
    ):
        """Единица `NULL` — тоже единица: активная семья без единицы есть, значит
        контекст без единицы строкой «без семей» не идёт."""
        _scene(db_session, factories, admin_client.user, titles=("Без единицы",), unit=None)

        payload = admin_client.get(f"{BASE}/suggestions", params={"queue": "new"}).json()

        assert payload["items"] == []


# ---------------------------------------------------------------------------
#  Задания
# ---------------------------------------------------------------------------

class TestJobs:
    def test_error_jobs_show_class_generation_and_last_attempt_text(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user)
        job = _make_job(
            db_session, scene.context_ids[0], status="error", unit_id=scene.unit_id,
            last_error_class="TimeoutError", retry_generation=2,
        )
        _make_attempt(db_session, job.id, error_text="таймаут провайдера")
        _make_job(db_session, scene.context_ids[0], status="done", request_hash="иной")

        payload = admin_client.get(f"{BASE}/jobs", params={"status": "error"}).json()

        assert payload["status"] == "error"
        assert payload["unit_groups"] == []
        [row] = payload["items"]
        assert (row["job_id"], row["context_id"], row["unit_code"]) == (job.id, scene.context_ids[0], "M2")
        assert (row["last_error_class"], row["error_text"], row["retry_generation"]) == (
            "TimeoutError", "таймаут провайдера", 2,
        )
        assert row["matches"] is None

    def test_privacy_hold_jobs_show_matches_and_group_family_list_matches_by_unit(
        self, admin_client, db_session, factories
    ):
        factories.ContractorFactory.create(title="ООО «Ромашка Строй»")
        admin = admin_client.user
        family = _active_family(
            db_session, title="Семья кладки", unit_name="M2", actor_id=admin.id,
            definition="кладка для ромашка строй",
        )
        proposal = _proposal(factories)
        m2 = _unit_id(db_session, "M2")
        ctxs = [
            _simple_context(db_session, factories, proposal, unit_id=m2, title=t)
            for t in ("Кладка А", "Кладка Б")
        ]
        jobs = [_hold_job(db_session, c, unit_id=m2) for c in ctxs]

        payload = admin_client.get(f"{BASE}/jobs", params={"status": "privacy_hold"}).json()

        assert sorted(i["job_id"] for i in payload["items"]) == sorted(j.id for j in jobs)
        assert payload["items"][0]["matches"] == jobs[0].privacy_matches
        [group] = payload["unit_groups"]
        assert (group["unit_id"], group["unit_code"], group["family_id"], group["jobs_count"]) == (
            m2, "M2", family.id, 2,
        )
        assert group["family_title"] == "Семья кладки"
        assert group["place"] == "family"
        assert group["matches"] == jobs[0].privacy_matches

    def test_prompt_match_is_grouped_by_unit_and_shows_its_place(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user, titles=("Первая", "Вторая"))
        matches = [{"text": "слово из промпта", "kind": "contractor", "where": "prompt"}]
        for ctx in scene.context_ids:
            _make_job(
                db_session, ctx, status="privacy_hold", unit_id=scene.unit_id,
                privacy_matches=matches,
            )

        payload = admin_client.get(f"{BASE}/jobs", params={"status": "privacy_hold"}).json()

        [group] = payload["unit_groups"]
        assert (group["unit_id"], group["unit_code"], group["place"], group["jobs_count"]) == (
            scene.unit_id, "M2", "prompt", 2,
        )
        assert (group["family_id"], group["family_title"]) == (None, None)
        assert group["matches"] == matches

    def test_family_and_prompt_matches_together_are_a_mixed_group(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user, titles=("Первая", "Вторая"))
        matches = [
            {"text": "слово из промпта", "kind": "contractor", "where": "prompt"},
            {"text": "ромашка строй", "kind": "contractor", "where": f"family:{scene.family.id}"},
        ]
        for ctx in scene.context_ids:
            _make_job(
                db_session, ctx, status="privacy_hold", unit_id=scene.unit_id,
                privacy_matches=matches,
            )

        payload = admin_client.get(f"{BASE}/jobs", params={"status": "privacy_hold"}).json()

        [group] = payload["unit_groups"]
        assert (group["place"], group["family_id"], group["family_title"], group["jobs_count"]) == (
            "mixed", scene.family.id, "Семья пола", 2,
        )
        assert group["matches"] == matches

    def test_context_match_is_not_grouped_as_family_list_match(
        self, admin_client, db_session, factories
    ):
        factories.ContractorFactory.create(title="ООО «Ромашка Строй»")
        scene = _scene(
            db_session, factories, admin_client.user, titles=("Кладка Ромашка Строй стен",)
        )
        _hold_job(db_session, scene.context_ids[0], unit_id=scene.unit_id)

        payload = admin_client.get(f"{BASE}/jobs", params={"status": "privacy_hold"}).json()

        assert len(payload["items"]) == 1
        assert payload["unit_groups"] == []

    def test_mixed_family_and_context_matches_are_not_grouped(
        self, admin_client, db_session, factories
    ):
        """«Отправить все K» — только когда ВСЕ совпадения лежат в списке семей:
        задание с совпадением ещё и в строке контекста группой не идёт."""
        factories.ContractorFactory.create(title="ООО «Ромашка Строй»")
        family = _active_family(
            db_session, title="Семья кладки", unit_name="M2", actor_id=admin_client.user.id,
            definition="кладка для ромашка строй",
        )
        m2 = _unit_id(db_session, "M2")
        ctx = _simple_context(
            db_session, factories, _proposal(factories), unit_id=m2, title="Кладка Ромашка Строй стен"
        )
        job = _hold_job(db_session, ctx, unit_id=m2)
        assert {m["where"] for m in job.privacy_matches} == {"context", f"family:{family.id}"}

        payload = admin_client.get(f"{BASE}/jobs", params={"status": "privacy_hold"}).json()

        assert [i["job_id"] for i in payload["items"]] == [job.id]
        assert payload["unit_groups"] == []

    def test_error_text_comes_from_the_latest_attempt(self, admin_client, db_session, factories):
        scene = _scene(db_session, factories, admin_client.user)
        job = _make_job(
            db_session, scene.context_ids[0], status="error", last_error_class="TimeoutError"
        )
        _make_attempt(db_session, job.id, error_text="первая ошибка")
        _make_attempt(db_session, job.id, error_text="последняя ошибка")

        payload = admin_client.get(f"{BASE}/jobs", params={"status": "error"}).json()

        assert [i["error_text"] for i in payload["items"]] == ["последняя ошибка"]

    def test_schema_error_shows_the_validation_reason(self, admin_client, db_session, factories):
        """Ответ, не прошедший схему, не пишет `error_text`: причина лежит в
        `validation_error` последней попытки и должна дойти до экрана ошибок."""
        scene = _scene(db_session, factories, admin_client.user)
        job = _make_job(
            db_session, scene.context_ids[0], status="error", last_error_class="AnswerSchemaError"
        )
        _make_attempt(db_session, job.id, validation_error="suggestions[0].family_id: нет в списке")

        payload = admin_client.get(f"{BASE}/jobs", params={"status": "error"}).json()

        assert [i["error_text"] for i in payload["items"]] == [
            "suggestions[0].family_id: нет в списке"
        ]

    def test_error_text_is_preferred_over_the_validation_reason(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user)
        job = _make_job(
            db_session, scene.context_ids[0], status="error", last_error_class="TimeoutError"
        )
        _make_attempt(
            db_session, job.id, error_text="таймаут провайдера", validation_error="прежняя схема"
        )

        payload = admin_client.get(f"{BASE}/jobs", params={"status": "error"}).json()

        assert [i["error_text"] for i in payload["items"]] == ["таймаут провайдера"]

    def test_error_job_released_from_hold_shows_no_matches(
        self, admin_client, db_session, factories
    ):
        """Отпущенное задание хранит прежние `privacy_matches`; упав в `error`, оно
        в очереди ошибок идёт без совпадений — они относятся к задержанным."""
        scene = _scene(db_session, factories, admin_client.user)
        _make_job(
            db_session, scene.context_ids[0], status="error", last_error_class="TimeoutError",
            privacy_matches=[{"text": "ромашка строй", "kind": "contractor", "where": "context"}],
        )

        payload = admin_client.get(f"{BASE}/jobs", params={"status": "error"}).json()

        assert [i["matches"] for i in payload["items"]] == [None]

    @pytest.mark.parametrize("path", ["jobs?status=error", "status"])
    def test_query_count_of_jobs_and_status_does_not_grow_with_row_count(
        self, admin_client, db_session, factories, path
    ):
        scene = _scene(db_session, factories, admin_client.user, titles=("Первая", "Вторая"))

        def _add(titles):
            for title in titles:
                ctx = _simple_context(
                    db_session, factories, scene.proposal, unit_id=scene.unit_id, title=title
                )
                job = _make_job(db_session, ctx, status="error", unit_id=scene.unit_id)
                _make_attempt(db_session, job.id, error_text="ошибка")

        _add([f"Малая {i}" for i in range(3)])
        admin_client.get(f"{BASE}/{path}")  # прогрев: загрузка пользователя сессией
        with _capturing_sql(db_session) as small:
            admin_client.get(f"{BASE}/{path}")
        _add([f"Большая {i}" for i in range(12)])
        with _capturing_sql(db_session) as large:
            response = admin_client.get(f"{BASE}/{path}")

        assert response.status_code == 200
        if path.startswith("jobs"):
            assert len(response.json()["items"]) == 3 + 12
        assert len(small) == len(large), (len(small), len(large))

    @pytest.mark.parametrize("value", ["done", "pending", ""])
    def test_only_error_and_privacy_hold_are_accepted(self, admin_client, value):
        assert admin_client.get(f"{BASE}/jobs", params={"status": value}).status_code == 422

    def test_status_is_required(self, admin_client):
        assert admin_client.get(f"{BASE}/jobs").status_code == 422


# ---------------------------------------------------------------------------
#  Шапка
# ---------------------------------------------------------------------------

#: Состояние задания текущего отпечатка после A -> B -> A и покрывает ли оно контекст.
_BACK_TO_A_STATES = [
    ("cancelled_input_changed", False),
    ("done_unpublished", False),
    ("done_published", True),
    ("done_decided", True),
    ("pending", True),
    ("running", True),
    ("privacy_hold", True),
    ("error", True),
    ("cancelled_privacy_declined", True),
]


class TestStatus:
    def test_empty_system_shape(self, admin_client):
        payload = admin_client.get(f"{BASE}/status").json()

        assert payload == {
            "spent_24h_usd": "0",
            "daily_budget_usd": str(app_settings.SEMANTIC_DAILY_BUDGET_USD),
            "claim_paused": None,
            "held_batches": [],
            "stale_units": [],
            "config_stale": None,
            "catalog_to_review": 0,
            "catalog_position": 0,
            "contexts_with_variant": 0,
            "contexts_pending": 0,
            "families_without_schema": 0,
        }

    def test_exponent_budget_is_served_in_fixed_notation(self, admin_client, monkeypatch):
        monkeypatch.setattr(app_settings, "SEMANTIC_DAILY_BUDGET_USD", Decimal("1E+2"))

        payload = admin_client.get(f"{BASE}/status").json()

        assert payload["daily_budget_usd"] == "100"

    def test_ordinary_budget_is_served_unchanged(self, admin_client, monkeypatch):
        monkeypatch.setattr(app_settings, "SEMANTIC_DAILY_BUDGET_USD", Decimal("12.50"))

        payload = admin_client.get(f"{BASE}/status").json()

        assert payload["daily_budget_usd"] == "12.50"

    def test_spent_is_a_decimal_string_of_the_last_24_hours(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user)
        job = _make_job(db_session, scene.context_ids[0], status="done")
        _make_attempt(db_session, job.id, cost_usd=Decimal("1.2345"))

        payload = admin_client.get(f"{BASE}/status").json()

        assert payload["spent_24h_usd"] == "1.2345"

    def test_tiny_spent_is_served_in_fixed_notation(self, admin_client, db_session, factories):
        scene = _scene(db_session, factories, admin_client.user)
        job = _make_job(db_session, scene.context_ids[0], status="done")
        _make_attempt(db_session, job.id, cost_usd=Decimal("0.0000001"))

        payload = admin_client.get(f"{BASE}/status").json()

        assert payload["spent_24h_usd"] == "0.0000001"

    def test_tiny_held_batch_money_is_served_in_fixed_notation(
        self, admin_client, db_session, factories
    ):
        _scene(db_session, factories, admin_client.user, titles=("А", "Б"))
        batch_id = decisions.enqueue_all(db_session)
        db_session.execute(
            sa.update(SemanticReconcileBatch)
            .where(SemanticReconcileBatch.id == batch_id)
            .values(
                reserve_estimate_usd=Decimal("0.0000001"),
                cached_estimate_usd=Decimal("0.00000002"),
            )
        )
        db_session.expire_all()

        payload = admin_client.get(f"{BASE}/status").json()

        [info] = payload["held_batches"]
        assert (info["reserve_usd"], info["expected_cached_usd"]) == ("0.0000001", "0.00000002")

    def test_paused_claim_is_reported_with_reason_attempt_and_time(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user)
        job = _make_job(db_session, scene.context_ids[0], status="done")
        attempt = _make_attempt(db_session, job.id)
        paused_at = dt.datetime(2026, 9, 29, 10, 0, tzinfo=dt.UTC)
        db_session.execute(
            sa.update(SemanticWorkerState)
            .where(SemanticWorkerState.id == 1)
            .values(
                claim_paused=True, paused_reason="reserve_exceeded",
                paused_attempt_id=attempt.id, paused_at=paused_at,
            )
        )
        db_session.expire_all()

        payload = admin_client.get(f"{BASE}/status").json()

        assert payload["claim_paused"]["reason"] == "reserve_exceeded"
        assert payload["claim_paused"]["attempt_id"] == attempt.id
        assert dt.datetime.fromisoformat(payload["claim_paused"]["paused_at"]) == paused_at

    def test_held_batch_is_listed_with_money_as_strings(self, admin_client, db_session, factories):
        scene = _scene(db_session, factories, admin_client.user, titles=("А", "Б"))
        batch_id = decisions.enqueue_all(db_session)
        assert batch_id is not None
        batch = db_session.get(SemanticReconcileBatch, batch_id)

        payload = admin_client.get(f"{BASE}/status").json()

        [info] = payload["held_batches"]
        assert (info["batch_id"], info["source"], info["contexts_count"]) == (batch_id, "mass", 2)
        assert (info["import_job_id"], info["unit_id"]) == (None, None)
        assert batch.reserve_estimate_usd > 0
        assert info["reserve_usd"] == str(batch.reserve_estimate_usd)
        assert info["expected_cached_usd"] == str(batch.cached_estimate_usd)
        assert info["created_at"]
        assert scene.context_ids

    def test_decided_batch_is_not_listed(self, admin_client, db_session, factories):
        _scene(db_session, factories, admin_client.user)
        batch_id = decisions.enqueue_all(db_session)
        decisions.discard_batch(db_session, batch_id=batch_id, actor_id=admin_client.user.id)

        assert admin_client.get(f"{BASE}/status").json()["held_batches"] == []

    def test_context_with_current_job_makes_nothing_stale(self, admin_client, db_session, factories):
        scene = _scene(db_session, factories, admin_client.user)
        _make_job(db_session, scene.context_ids[0], status="done")

        payload = admin_client.get(f"{BASE}/status").json()

        assert (payload["stale_units"], payload["config_stale"]) == ([], None)

    def test_bound_context_with_its_own_current_jobs_is_not_stale(
        self, admin_client, db_session, factories
    ):
        """Контекст с назначенной семьёй применим (спека вариантов §2.5): у него
        есть собственные задания по текущему отпечатку, и пометки «список семей
        изменён» он не даёт."""
        scene = _scene(db_session, factories, admin_client.user)
        assign_family(
            db_session, context_id=scene.context_ids[0], family_id=scene.family.id,
            actor_id=admin_client.user.id,
        )
        # Задание предложения и задание значений по схеме семьи. Состав
        # сверяется целиком: ровно эти два задания этого контекста.
        assert sorted(
            (job.kind, job.context_id)
            for job in db_session.execute(sa.select(SemanticJob)).scalars().all()
        ) == [
            ("context_values", scene.context_ids[0]),
            ("family_suggestion", scene.context_ids[0]),
        ]

        payload = admin_client.get(f"{BASE}/status").json()

        assert (payload["stale_units"], payload["config_stale"]) == ([], None)

    def test_changed_family_list_marks_the_unit_stale(self, admin_client, db_session, factories):
        scene = _scene(db_session, factories, admin_client.user, titles=("Один", "Два"))
        for ctx in scene.context_ids:
            _make_job(db_session, ctx, status="done")
        _active_family(
            db_session, title="Новая активная", unit_name="M2", actor_id=admin_client.user.id
        )

        payload = admin_client.get(f"{BASE}/status").json()

        assert payload["stale_units"] == [
            {"unit_id": scene.unit_id, "unit_code": "M2", "stale_count": 2}
        ]
        assert payload["config_stale"] is None

    def test_same_family_list_with_other_request_marks_configuration_stale(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user)
        _make_job(db_session, scene.context_ids[0], status="done", request_hash="прежний-запрос")

        payload = admin_client.get(f"{BASE}/status").json()

        assert payload["stale_units"] == []
        assert payload["config_stale"] == {"stale_count": 1, "prompt_version_current": 1}

    def test_whitespace_only_family_edit_keeps_the_unit_current(
        self, admin_client, db_session, factories
    ):
        """Строка семьи в запросе схлопывает пробелы: правка определения одними
        пробелами и переносами не меняет тело запроса, и задание с тем же
        `request_hash` по-прежнему покрывает контекст, хотя сырой снимок семей
        (`candidates_hash`) уже другой."""
        scene = _scene(db_session, factories, admin_client.user)
        job = _make_job(db_session, scene.context_ids[0], status="done")
        scene.family.definition = "  Определение\n\n  семьи   "
        db_session.flush()
        assert _rendered(db_session, scene.context_ids[0]).request_hash == job.request_hash
        assert _rendered(db_session, scene.context_ids[0]).candidates_hash != job.candidates_hash

        payload = admin_client.get(f"{BASE}/status").json()

        assert (payload["stale_units"], payload["config_stale"]) == ([], None)

    def test_meaning_family_edit_marks_the_unit_stale(self, admin_client, db_session, factories):
        scene = _scene(db_session, factories, admin_client.user)
        _make_job(db_session, scene.context_ids[0], status="done")
        scene.family.definition = "Определение семьи, дополненное"
        db_session.flush()

        payload = admin_client.get(f"{BASE}/status").json()

        assert payload["stale_units"] == [
            {"unit_id": scene.unit_id, "unit_code": "M2", "stale_count": 1}
        ]

    def _history_job(self, db, context_id, *, status, request_hash=None, candidates_hash=None,
                     cancel_reason=None):
        """Задание в нужном состоянии: парные ограничения таблицы выполняются
        одним UPDATE."""
        job = _make_job(db, context_id, status="pending", request_hash=request_hash)
        values: dict = {}
        if candidates_hash is not None:
            values["candidates_hash"] = candidates_hash
        if status != "pending":
            values["status"] = status
        if status == "cancelled":
            values["cancel_reason"] = cancel_reason
        if status == "running":
            values["claim_token"] = uuid.uuid4()
        if status == "privacy_hold":
            values["privacy_matches"] = [{"text": "х", "kind": "contractor", "where": "context"}]
        if values:
            db.execute(sa.update(SemanticJob).where(SemanticJob.id == job.id).values(**values))
            db.expire(job)
        return job

    def _back_to_a(self, db, factories, admin, current_state):
        """A -> B -> A: задание прежнего списка B и задание текущего отпечатка A
        в состоянии `current_state`."""
        scene = _scene(db, factories, admin)
        context_id = scene.context_ids[0]
        self._history_job(
            db, context_id, status="done", request_hash="запрос-B", candidates_hash="список-B",
        )
        if current_state.startswith("done_"):
            # «Отклонить» снимает публикацию: решённое предложение не опубликовано,
            # и покрывает контекст именно решение, а не публикация.
            decided = current_state == "done_decided"
            suggestion = _published(
                db, context_id, family_id=scene.family.id,
                is_published=current_state == "done_published",
                decision="rejected" if decided else None,
                decided_by=admin.id if decided else None,
            )
            if decided:
                suggestion.unpublished_reason = "rejected"
                db.flush()
            assert suggestion.is_published is (current_state == "done_published")
        elif current_state.startswith("cancelled_"):
            reason = current_state.removeprefix("cancelled_")
            self._history_job(db, context_id, status="cancelled", cancel_reason=reason)
        else:
            self._history_job(db, context_id, status=current_state)
        return scene

    @pytest.mark.parametrize(("current_state", "covers"), _BACK_TO_A_STATES)
    def test_family_list_returning_to_an_earlier_state_is_stale_unless_the_old_answer_is_alive(
        self, admin_client, db_session, factories, current_state, covers
    ):
        """A -> B -> A: задание отпечатка A есть, но покрывает контекст, только
        если его ответ жив; прежний список B задания не заменяет."""
        scene = self._back_to_a(db_session, factories, admin_client.user, current_state)

        payload = admin_client.get(f"{BASE}/status").json()

        expected = [] if covers else [{"unit_id": scene.unit_id, "unit_code": "M2", "stale_count": 1}]
        assert payload["stale_units"] == expected
        assert payload["config_stale"] is None

    @pytest.mark.parametrize("current_state", [state for state, _covers in _BACK_TO_A_STATES])
    def test_unit_is_stale_exactly_when_its_reask_would_change_something(
        self, admin_client, db_session, factories, current_state
    ):
        """Шапка и сверка судят об одном и том же с двух сторон: единица помечена
        устаревшей ровно тогда, когда её перезапрос что-то создаёт, оживляет или
        переопубликовывает, а после перезапроса пометки нет."""
        scene = self._back_to_a(db_session, factories, admin_client.user, current_state)
        stale = admin_client.get(f"{BASE}/status").json()["stale_units"] != []

        preview = decisions.preview_unit_reask(db_session, unit_id=scene.unit_id)
        report = decisions.confirm_unit_reask(
            db_session, unit_id=scene.unit_id, preview_hash=preview.preview_hash,
            actor_id=admin_client.user.id,
        )
        db_session.flush()
        db_session.expire_all()

        assert (report.created + report.revived + report.republished > 0) is stale
        assert admin_client.get(f"{BASE}/status").json()["stale_units"] == []

    def test_configuration_returning_to_an_earlier_state_is_stale_when_the_old_answer_is_gone(
        self, admin_client, db_session, factories
    ):
        """A -> B -> A по запросу при прежнем списке семей: ответ отпечатка A снят,
        а B покрывает тот же список — устарела конфигурация, не единица."""
        scene = _scene(db_session, factories, admin_client.user)
        context_id = scene.context_ids[0]
        self._history_job(db_session, context_id, status="done", request_hash="запрос-B")
        _published(db_session, context_id, family_id=scene.family.id, is_published=False)

        payload = admin_client.get(f"{BASE}/status").json()

        assert payload["stale_units"] == []
        assert payload["config_stale"] == {"stale_count": 1, "prompt_version_current": 1}

    def test_configuration_with_the_old_answer_alive_is_not_stale(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user)
        context_id = scene.context_ids[0]
        self._history_job(db_session, context_id, status="done", request_hash="запрос-B")
        _published(db_session, context_id, family_id=scene.family.id)

        payload = admin_client.get(f"{BASE}/status").json()

        assert (payload["stale_units"], payload["config_stale"]) == ([], None)

    def test_only_the_unit_whose_family_list_changed_is_stale(
        self, admin_client, db_session, factories
    ):
        """Единиц несколько, устарела одна: остальные не попадают в ответ, а
        конфигурация другой единицы считается отдельно."""
        actor = admin_client.user.id
        scene = _scene(db_session, factories, admin_client.user, titles=("Пол А", "Пол Б"))
        pcs_unit = _unit_id(db_session, "PCS")
        _active_family(db_session, title="Семья штук", unit_name="PCS", actor_id=actor)
        pcs_contexts = [
            _simple_context(db_session, factories, scene.proposal, unit_id=pcs_unit, title=t)
            for t in ("Штука А", "Штука Б", "Штука В")
        ]
        for ctx in [*scene.context_ids, *pcs_contexts]:
            _make_job(db_session, ctx, status="done")
        _active_family(db_session, title="Ещё семья штук", unit_name="PCS", actor_id=actor)
        # Единица м²: список семей прежний, а у одного контекста запрос другой.
        db_session.execute(
            sa.update(SemanticJob)
            .where(SemanticJob.context_id == scene.context_ids[0])
            .values(request_hash="прежний-запрос")
        )

        payload = admin_client.get(f"{BASE}/status").json()

        assert payload["stale_units"] == [
            {"unit_id": pcs_unit, "unit_code": "PCS", "stale_count": 3}
        ]
        assert payload["config_stale"] == {"stale_count": 1, "prompt_version_current": 1}

    def test_two_units_with_current_jobs_are_both_current(
        self, admin_client, db_session, factories
    ):
        """Отпечатки считаются по списку семей своей единицы: у двух единиц с
        разными списками и текущими заданиями устаревшего нет."""
        actor = admin_client.user.id
        scene = _scene(db_session, factories, admin_client.user, titles=("Пол А", "Пол Б"))
        pcs_unit = _unit_id(db_session, "PCS")
        _active_family(db_session, title="Семья штук", unit_name="PCS", actor_id=actor)
        pcs_contexts = [
            _simple_context(db_session, factories, scene.proposal, unit_id=pcs_unit, title=t)
            for t in ("Штука А", "Штука Б")
        ]
        for ctx in [*scene.context_ids, *pcs_contexts]:
            _make_job(db_session, ctx, status="done")

        payload = admin_client.get(f"{BASE}/status").json()

        assert (payload["stale_units"], payload["config_stale"]) == ([], None)

    @pytest.mark.parametrize("contexts", [3, 12])
    def test_status_answer_does_not_depend_on_how_many_contexts_share_the_unit(
        self, admin_client, db_session, factories, contexts
    ):
        """Число устаревших единиц и контекстов — ровно по входу, сколько бы
        контекстов ни делили единицу."""
        scene = _scene(db_session, factories, admin_client.user, titles=("Первая",))
        ids = list(scene.context_ids) + [
            _simple_context(
                db_session, factories, scene.proposal, unit_id=scene.unit_id, title=f"Строка {i}"
            )
            for i in range(contexts - 1)
        ]
        for ctx in ids:
            _make_job(db_session, ctx, status="done")
        _active_family(
            db_session, title="Новая активная", unit_name="M2", actor_id=admin_client.user.id
        )

        payload = admin_client.get(f"{BASE}/status").json()

        assert payload["stale_units"] == [
            {"unit_id": scene.unit_id, "unit_code": "M2", "stale_count": contexts}
        ]

    def test_status_query_count_and_renders_do_not_grow_with_context_count(
        self, admin_client, db_session, factories, monkeypatch
    ):
        """Число запросов и полных рендеров списка семей не растёт с числом
        контекстов: рендер один на единицу (их здесь две)."""
        import crud.semantic_queue as queue_crud

        renders = []
        real_render = queue_crud.render_context_request

        def _counting_render(material, *, settings):
            renders.append(material.context_id)
            return real_render(material, settings=settings)

        monkeypatch.setattr(queue_crud, "render_context_request", _counting_render)
        scene = _scene(db_session, factories, admin_client.user, titles=("Первая",))
        pcs_unit = _unit_id(db_session, "PCS")
        _active_family(db_session, title="Семья штук", unit_name="PCS", actor_id=admin_client.user.id)

        def _add(count, tag):
            for i in range(count):
                for unit_id in (scene.unit_id, pcs_unit):
                    ctx = _simple_context(
                        db_session, factories, scene.proposal, unit_id=unit_id,
                        title=f"{tag} {unit_id} {i}",
                    )
                    _make_job(db_session, ctx, status="done")

        _add(10, "Малая")
        admin_client.get(f"{BASE}/status")  # прогрев: загрузка пользователя сессией
        renders.clear()
        with _capturing_sql(db_session) as small:
            r_small = admin_client.get(f"{BASE}/status")
        renders_small = len(renders)
        _add(50, "Большая")
        renders.clear()
        with _capturing_sql(db_session) as large:
            r_large = admin_client.get(f"{BASE}/status")
        renders_large = len(renders)

        assert (r_small.json()["stale_units"], r_large.json()["stale_units"]) == (
            [{"unit_id": scene.unit_id, "unit_code": "M2", "stale_count": 1}],
            [{"unit_id": scene.unit_id, "unit_code": "M2", "stale_count": 1}],
        )
        assert len(small) == len(large), (len(small), len(large))
        assert (renders_small, renders_large) == (2, 2)


# ---------------------------------------------------------------------------
#  Решения по предложению
# ---------------------------------------------------------------------------

class TestSuggestionActions:
    def test_confirm_assigns_and_reports_confirmed_and_skipped(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user, titles=("Верная", "Снятая"))
        good_ctx, bad_ctx = scene.context_ids
        good = _published(db_session, good_ctx, family_id=scene.family.id)
        bad = _published(db_session, bad_ctx, family_id=scene.family.id, is_published=False)

        response = admin_client.post(
            f"{BASE}/suggestions/confirm", json={"suggestion_ids": [good.id, bad.id]}
        )

        assert response.status_code == 200
        assert response.json() == {"confirmed": [good.id], "skipped": [bad.id]}
        db_session.expire_all()
        assert db_session.get(CatalogContext, good_ctx).work_family_id == scene.family.id
        assert db_session.get(CatalogContext, bad_ctx).work_family_id is None

    def test_confirm_with_empty_list_is_422(self, admin_client):
        response = admin_client.post(f"{BASE}/suggestions/confirm", json={"suggestion_ids": []})
        assert response.status_code == 422

    def test_reject_unpublishes_and_second_reject_is_409_suggestion_changed(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user)
        s = _published(db_session, scene.context_ids[0], family_id=scene.family.id)

        first = admin_client.post(f"{BASE}/suggestions/{s.id}/reject")
        second = admin_client.post(f"{BASE}/suggestions/{s.id}/reject")

        assert first.status_code == 200
        assert first.json() == {"suggestion_id": s.id, "decision": "rejected"}
        assert second.status_code == 409
        assert _detail(second)["code"] == "suggestion_changed"
        db_session.expire_all()
        assert db_session.get(FamilySuggestion, s.id).decision == "rejected"

    def test_unknown_suggestion_is_404_with_code(self, admin_client):
        response = admin_client.post(f"{BASE}/suggestions/999999999/reject")
        assert response.status_code == 404
        assert _detail(response)["code"] == "not_found"

    @pytest.mark.parametrize("defect", [KeyError, IndexError])
    def test_key_or_index_error_inside_a_decision_is_not_masked_as_404(
        self, admin_client, monkeypatch, defect
    ):
        """`KeyError`/`IndexError` — подклассы `LookupError`, но это дефект кода,
        а не «предложение не найдено»: они летят дальше, а не становятся `404`."""

        def _broken(db, **kwargs):
            raise defect("дефект")

        monkeypatch.setattr(decisions, "reject_suggestion", _broken)

        with pytest.raises(defect):
            admin_client.post(f"{BASE}/suggestions/1/reject")

    def test_other_family_assigns_chosen_family(self, admin_client, db_session, factories):
        scene = _scene(db_session, factories, admin_client.user)
        chosen = _active_family(
            db_session, title="Выбранная вручную", unit_name="M2", actor_id=admin_client.user.id
        )
        s = _published(db_session, scene.context_ids[0], family_id=scene.family.id)

        response = admin_client.post(
            f"{BASE}/suggestions/{s.id}/other-family", json={"family_id": chosen.id}
        )

        assert response.status_code == 200
        assert response.json()["family_id"] == chosen.id
        db_session.expire_all()
        ctx = db_session.get(CatalogContext, scene.context_ids[0])
        assert (ctx.work_family_id, ctx.family_source) == (chosen.id, "manual")

    def test_other_family_of_another_unit_is_refused_with_family_error_code(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user)
        wrong_unit = _active_family(
            db_session, title="Штучная семья", unit_name="PCS", actor_id=admin_client.user.id
        )
        s = _published(db_session, scene.context_ids[0], family_id=scene.family.id)
        db_session.commit()  # отказ откатывает транзакцию маршрута: подготовка обязана её пережить

        response = admin_client.post(
            f"{BASE}/suggestions/{s.id}/other-family", json={"family_id": wrong_unit.id}
        )

        assert response.status_code == 409
        assert _detail(response)["code"] == "unit_mismatch"
        db_session.expire_all()
        assert db_session.get(FamilySuggestion, s.id).decision is None

    def test_other_family_that_does_not_exist_is_404(self, admin_client, db_session, factories):
        scene = _scene(db_session, factories, admin_client.user)
        s = _published(db_session, scene.context_ids[0], family_id=scene.family.id)

        response = admin_client.post(
            f"{BASE}/suggestions/{s.id}/other-family", json={"family_id": 999999999}
        )

        assert response.status_code == 404
        assert _detail(response)["code"] == "family_not_found"

    def test_create_family_makes_active_family_and_assigns_it(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user)
        s = _published(db_session, scene.context_ids[0], family_id=None, new_family_name="Кладка")

        response = admin_client.post(
            f"{BASE}/suggestions/{s.id}/create-family",
            json={"title": "Кладка стен", "definition": "Что входит и что не входит", "family_category_id": seed_category_id(db_session)},
        )

        assert response.status_code == 200
        family_id = response.json()["family_id"]
        db_session.expire_all()
        family = db_session.get(WorkFamily, family_id)
        assert (family.title, family.status) == ("Кладка стен", "active")
        assert db_session.get(CatalogContext, scene.context_ids[0]).work_family_id == family_id
        assert db_session.get(FamilySuggestion, s.id).decision == "family_created"

    @pytest.mark.parametrize(
        "body",
        [
            {"title": "   ", "definition": "определение"},
            {"title": "Название", "definition": "   "},
        ],
    )
    def test_create_family_with_blank_title_or_definition_is_422(
        self, admin_client, db_session, factories, body
    ):
        scene = _scene(db_session, factories, admin_client.user)
        s = _published(db_session, scene.context_ids[0], family_id=None)
        db_session.commit()

        response = admin_client.post(f"{BASE}/suggestions/{s.id}/create-family", json={**body, "family_category_id": seed_category_id(db_session)})

        assert response.status_code == 422
        db_session.expire_all()
        assert db_session.get(FamilySuggestion, s.id).decision is None

    def test_create_family_with_existing_name_is_409_family_exists_with_link(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user)
        s = _published(db_session, scene.context_ids[0], family_id=None)
        db_session.commit()
        before = db_session.scalar(sa.select(sa.func.count()).select_from(WorkFamily))
        family_id = scene.family.id

        response = admin_client.post(
            f"{BASE}/suggestions/{s.id}/create-family",
            json={"title": scene.family.title, "definition": "иное определение", "family_category_id": seed_category_id(db_session)},
        )

        assert response.status_code == 409
        detail = _detail(response)
        assert detail["code"] == "family_exists"
        assert detail["family_id"] == family_id
        db_session.expire_all()
        assert db_session.scalar(sa.select(sa.func.count()).select_from(WorkFamily)) == before
        assert db_session.get(FamilySuggestion, s.id).decision is None

    def test_family_exists_without_known_duplicate_id_still_answers_409_with_null_family_id(
        self, admin_client, db_session, factories, monkeypatch
    ):
        scene = _scene(db_session, factories, admin_client.user)
        s = _published(db_session, scene.context_ids[0], family_id=None)

        def _race(db, **kwargs):
            raise decisions.DecisionConflict(decisions.CODE_FAMILY_EXISTS, "такая семья уже есть")

        monkeypatch.setattr(decisions, "create_family_from_suggestion", _race)

        response = admin_client.post(
            f"{BASE}/suggestions/{s.id}/create-family",
            json={"title": "Гонка", "definition": "определение", "family_category_id": seed_category_id(db_session)},
        )

        assert response.status_code == 409
        detail = _detail(response)
        assert (detail["code"], "family_id" in detail, detail["family_id"]) == (
            "family_exists", True, None,
        )

    def test_create_family_on_stale_suggestion_is_409_suggestion_changed(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user)
        s = _published(db_session, scene.context_ids[0], family_id=None, is_published=False)

        response = admin_client.post(
            f"{BASE}/suggestions/{s.id}/create-family",
            json={"title": "Другая", "definition": "определение", "family_category_id": seed_category_id(db_session)},
        )

        assert response.status_code == 409
        detail = _detail(response)
        assert detail["code"] == "suggestion_changed"
        assert "family_id" not in detail


# ---------------------------------------------------------------------------
#  Задания: повтор и решения по задержанным
# ---------------------------------------------------------------------------

class TestJobActions:
    def test_retry_moves_error_job_to_pending(self, admin_client, db_session, factories):
        scene = _scene(db_session, factories, admin_client.user)
        job = _make_job(db_session, scene.context_ids[0], status="error", last_error_class="X")

        response = admin_client.post(f"{BASE}/jobs/{job.id}/retry")

        assert response.status_code == 200
        db_session.expire_all()
        fresh = db_session.get(SemanticJob, job.id)
        assert (fresh.status, fresh.retry_generation) == ("pending", 1)

    def test_retry_of_done_job_is_409_job_changed(self, admin_client, db_session, factories):
        scene = _scene(db_session, factories, admin_client.user)
        job = _make_job(db_session, scene.context_ids[0], status="done")

        response = admin_client.post(f"{BASE}/jobs/{job.id}/retry")

        assert response.status_code == 409
        assert _detail(response)["code"] == "job_changed"

    def test_retry_of_unknown_job_is_404(self, admin_client):
        response = admin_client.post(f"{BASE}/jobs/999999999/retry")
        assert response.status_code == 404
        assert _detail(response)["code"] == "not_found"

    def _held(self, db_session, factories, admin):
        factories.ContractorFactory.create(title="ООО «Ромашка Строй»")
        scene = _scene(db_session, factories, admin, titles=("Кладка Ромашка Строй стен",))
        job = _hold_job(db_session, scene.context_ids[0], unit_id=scene.unit_id)
        return scene, job

    def test_privacy_release_sends_the_shown_set(self, admin_client, db_session, factories):
        _, job = self._held(db_session, factories, admin_client.user)

        response = admin_client.post(
            f"{BASE}/jobs/{job.id}/privacy-release", json={"shown_matches": job.privacy_matches}
        )

        assert response.status_code == 200
        db_session.expire_all()
        fresh = db_session.get(SemanticJob, job.id)
        assert (fresh.status, fresh.privacy_released_matches) == ("pending", job.privacy_matches)

    def test_privacy_release_with_other_matches_is_409_job_changed(
        self, admin_client, db_session, factories
    ):
        _, job = self._held(db_session, factories, admin_client.user)
        db_session.commit()

        response = admin_client.post(
            f"{BASE}/jobs/{job.id}/privacy-release", json={"shown_matches": _OTHER_MATCHES}
        )

        assert response.status_code == 409
        assert _detail(response)["code"] == "job_changed"
        db_session.expire_all()
        assert db_session.get(SemanticJob, job.id).status == "privacy_hold"

    def test_changed_dictionary_refusal_keeps_the_refreshed_set_for_the_next_listing(
        self, admin_client, db_session, factories
    ):
        scene, job = self._held(db_session, factories, admin_client.user)
        old = list(job.privacy_matches)
        factories.ContractorFactory.create(title="ООО «Кладка Ромашка»")
        db_session.commit()

        refused = admin_client.post(
            f"{BASE}/jobs/{job.id}/privacy-release", json={"shown_matches": old}
        )

        assert refused.status_code == 409 and _detail(refused)["code"] == "job_changed"
        listed = admin_client.get(f"{BASE}/jobs", params={"status": "privacy_hold"}).json()
        [item] = listed["items"]
        assert item["matches"] != old
        again = admin_client.post(
            f"{BASE}/jobs/{job.id}/privacy-release", json={"shown_matches": item["matches"]}
        )
        assert again.status_code == 200
        db_session.expire_all()
        assert db_session.get(SemanticJob, job.id).status == "pending"

    def test_privacy_decline_cancels_the_job(self, admin_client, db_session, factories):
        _, job = self._held(db_session, factories, admin_client.user)

        response = admin_client.post(
            f"{BASE}/jobs/{job.id}/privacy-decline", json={"shown_matches": job.privacy_matches}
        )

        assert response.status_code == 200
        db_session.expire_all()
        fresh = db_session.get(SemanticJob, job.id)
        assert (fresh.status, fresh.cancel_reason) == ("cancelled", "privacy_declined")

    def test_privacy_release_requires_shown_matches(self, admin_client, db_session, factories):
        _, job = self._held(db_session, factories, admin_client.user)
        assert admin_client.post(f"{BASE}/jobs/{job.id}/privacy-release", json={}).status_code == 422

    def test_unit_privacy_release_sends_all_holds_of_unit(self, admin_client, db_session, factories):
        scene, job = self._held(db_session, factories, admin_client.user)

        response = admin_client.post(
            f"{BASE}/unit-privacy-release",
            json={"unit_id": scene.unit_id, "shown_matches": job.privacy_matches},
        )

        assert response.status_code == 200
        assert response.json() == {"confirmed": [job.id], "skipped": []}

    def test_unit_privacy_release_with_null_unit_touches_only_jobs_without_unit(
        self, admin_client, db_session, factories
    ):
        factories.ContractorFactory.create(title="ООО «Ромашка Строй»")
        admin = admin_client.user
        scene = _scene(
            db_session, factories, admin, titles=("Кладка Ромашка Строй А", "Кладка Ромашка Строй Б")
        )
        with_unit = _hold_job(db_session, scene.context_ids[0], unit_id=scene.unit_id)
        without_unit = _hold_job(db_session, scene.context_ids[1], unit_id=None)

        response = admin_client.post(
            f"{BASE}/unit-privacy-release",
            json={"unit_id": None, "shown_matches": without_unit.privacy_matches},
        )

        assert response.status_code == 200
        assert response.json() == {"confirmed": [without_unit.id], "skipped": []}
        db_session.expire_all()
        assert db_session.get(SemanticJob, with_unit.id).status == "privacy_hold"

    def test_unit_privacy_release_without_unit_key_is_422(self, admin_client):
        response = admin_client.post(f"{BASE}/unit-privacy-release", json={"shown_matches": []})
        assert response.status_code == 422


# ---------------------------------------------------------------------------
#  Перезапросы, пачки, остановка захвата
# ---------------------------------------------------------------------------

class TestReaskAndBatches:
    def test_unit_reask_preview_has_count_money_strings_and_hash(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user, titles=("А", "Б"))

        response = admin_client.post(
            f"{BASE}/unit-reask/preview", json={"unit_id": scene.unit_id}
        )

        assert response.status_code == 200
        expected = decisions.preview_unit_reask(db_session, unit_id=scene.unit_id)
        assert expected.context_count == 2 and expected.reserve_usd > 0
        assert response.json() == {
            "context_count": 2,
            "reserve_usd": str(expected.reserve_usd),
            "expected_cached_usd": str(expected.expected_cached_usd),
            "preview_hash": expected.preview_hash,
        }

    def test_unit_reask_preview_with_null_unit_counts_contexts_without_unit(
        self, admin_client, db_session, factories
    ):
        _scene(db_session, factories, admin_client.user, titles=("С единицей",), unit="M2")
        no_unit = _scene(db_session, factories, admin_client.user, titles=("Без единицы",), unit=None)
        assert no_unit.unit_id is None

        counted = admin_client.post(f"{BASE}/unit-reask/preview", json={"unit_id": None}).json()
        with_unit = admin_client.post(
            f"{BASE}/unit-reask/preview", json={"unit_id": _unit_id(db_session, "M2")}
        ).json()

        assert (counted["context_count"], with_unit["context_count"]) == (1, 1)
        assert counted["preview_hash"] != with_unit["preview_hash"]

    def test_unit_reask_with_null_unit_enqueues_only_contexts_without_unit(
        self, admin_client, db_session, factories
    ):
        _scene(db_session, factories, admin_client.user, titles=("С единицей",), unit="M2")
        no_unit = _scene(db_session, factories, admin_client.user, titles=("Без единицы",), unit=None)
        preview = admin_client.post(f"{BASE}/unit-reask/preview", json={"unit_id": None}).json()

        response = admin_client.post(
            f"{BASE}/unit-reask", json={"unit_id": None, "preview_hash": preview["preview_hash"]}
        )

        assert response.status_code == 200
        assert response.json()["created"] == 1
        assert list(db_session.scalars(sa.select(SemanticJob.context_id))) == no_unit.context_ids

    @pytest.mark.parametrize("path", ["unit-reask/preview", "unit-reask"])
    def test_unit_reask_without_unit_key_is_422(self, admin_client, path):
        response = admin_client.post(f"{BASE}/{path}", json={"preview_hash": "x"})
        assert response.status_code == 422

    def test_unit_reask_with_current_hash_enqueues_jobs(self, admin_client, db_session, factories):
        scene = _scene(db_session, factories, admin_client.user, titles=("А", "Б"))
        preview = admin_client.post(
            f"{BASE}/unit-reask/preview", json={"unit_id": scene.unit_id}
        ).json()

        response = admin_client.post(
            f"{BASE}/unit-reask",
            json={"unit_id": scene.unit_id, "preview_hash": preview["preview_hash"]},
        )

        assert response.status_code == 200
        assert response.json()["created"] == 2
        assert db_session.scalar(sa.select(sa.func.count()).select_from(SemanticJob)) == 2

    def test_unit_reask_with_other_hash_is_409_preview_changed_and_writes_nothing(
        self, admin_client, db_session, factories
    ):
        scene = _scene(db_session, factories, admin_client.user)
        db_session.commit()

        response = admin_client.post(
            f"{BASE}/unit-reask", json={"unit_id": scene.unit_id, "preview_hash": "0" * 64}
        )

        assert response.status_code == 409
        assert _detail(response)["code"] == "preview_changed"
        assert db_session.get(CatalogContext, scene.context_ids[0]) is not None
        assert db_session.scalar(sa.select(sa.func.count()).select_from(SemanticJob)) == 0

    def test_reask_all_preview_and_confirm(self, admin_client, db_session, factories):
        _scene(db_session, factories, admin_client.user, titles=("А", "Б", "В"))
        db_session.commit()

        preview = admin_client.post(f"{BASE}/reask-all/preview").json()
        stale = admin_client.post(f"{BASE}/reask-all", json={"preview_hash": "1" * 64})
        done = admin_client.post(f"{BASE}/reask-all", json={"preview_hash": preview["preview_hash"]})

        assert preview["context_count"] == 3
        assert (stale.status_code, _detail(stale)["code"]) == (409, "preview_changed")
        assert (done.status_code, done.json()["created"]) == (200, 3)

    def _batch(self, db_session, factories, admin):
        scene = _scene(db_session, factories, admin, titles=("А", "Б"))
        batch_id = decisions.enqueue_all(db_session)
        assert batch_id is not None
        return scene, batch_id

    def test_batch_preview_then_approve_enqueues_the_held_contexts(
        self, admin_client, db_session, factories
    ):
        _, batch_id = self._batch(db_session, factories, admin_client.user)

        preview = admin_client.post(f"{BASE}/batches/{batch_id}/preview")
        approved = admin_client.post(
            f"{BASE}/batches/{batch_id}/approve",
            json={"preview_hash": preview.json()["preview_hash"]},
        )

        assert preview.status_code == 200
        assert preview.json()["context_count"] == 2
        assert approved.status_code == 200
        assert approved.json()["created"] == 2
        db_session.expire_all()
        assert db_session.get(SemanticReconcileBatch, batch_id).status == "approved"

    def test_batch_approve_with_other_hash_is_409_preview_changed(
        self, admin_client, db_session, factories
    ):
        _, batch_id = self._batch(db_session, factories, admin_client.user)
        db_session.commit()

        response = admin_client.post(
            f"{BASE}/batches/{batch_id}/approve", json={"preview_hash": "2" * 64}
        )

        assert response.status_code == 409
        assert _detail(response)["code"] == "preview_changed"
        db_session.expire_all()
        assert db_session.get(SemanticReconcileBatch, batch_id).status == "held"

    def test_batch_discard_then_second_decision_is_409_batch_decided(
        self, admin_client, db_session, factories
    ):
        _, batch_id = self._batch(db_session, factories, admin_client.user)

        first = admin_client.post(f"{BASE}/batches/{batch_id}/discard")
        second = admin_client.post(f"{BASE}/batches/{batch_id}/discard")

        assert first.status_code == 200
        assert (second.status_code, _detail(second)["code"]) == (409, "batch_decided")

    @pytest.mark.parametrize("path,body", [("preview", None), ("approve", {"preview_hash": "x"}), ("discard", None)])
    def test_unknown_batch_is_404(self, admin_client, path, body):
        response = admin_client.post(f"{BASE}/batches/999999999/{path}", json=body)
        assert response.status_code == 404
        assert _detail(response)["code"] == "not_found"

    def test_resume_clears_the_pause(self, admin_client, db_session, factories):
        scene = _scene(db_session, factories, admin_client.user)
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

        response = admin_client.post(f"{BASE}/worker/resume")

        assert response.status_code == 200
        assert admin_client.get(f"{BASE}/status").json()["claim_paused"] is None

    def test_resume_when_not_paused_is_not_an_error(self, admin_client):
        assert admin_client.post(f"{BASE}/worker/resume").status_code == 200


class TestRefusalTransaction:
    """Транзакция маршрута при отказе решения: запись до отказа остаётся только
    у отказа, который сам об этом просит (`keep`), любой другой её откатывает."""

    @pytest.mark.parametrize("keep", [False, True])
    def test_write_before_a_refusal_survives_only_when_the_refusal_keeps_it(
        self, db_session, factories, keep
    ):
        from fastapi import HTTPException

        from models import Contractor
        from routers.semantic import _deciding

        with pytest.raises(HTTPException) as caught, _deciding(db_session):
            factories.ContractorFactory.create(title="Запись до отказа")
            raise decisions.DecisionConflict(decisions.CODE_JOB_CHANGED, keep=keep)

        assert caught.value.status_code == 409
        db_session.expire_all()
        written = db_session.execute(
            sa.select(sa.func.count()).select_from(Contractor).where(Contractor.title == "Запись до отказа")
        ).scalar_one()
        assert written == (1 if keep else 0)
