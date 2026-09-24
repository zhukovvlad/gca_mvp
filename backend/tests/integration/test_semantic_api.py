"""API семантического контура — восемнадцать маршрутов `/api/v1/semantic`
(спека `2026-09-22-catalog-families-design.md` §2.10; план, задача 12).

Сервисы задач 4, 6-10 (`services/context_routing.py`,
`services/context_operations.py`, `services/work_families.py`) сами не
тестируются здесь повторно — только HTTP-слой: права, форма запроса/ответа,
трансляция отказов, фильтры очереди контекстов и ограниченное число запросов
`list_contexts`.

Помощники (`_proposal`, `_chapter`, `_position`, `_leaf_category_ids`,
`_bucket`, `_context`, `_member`, `_rule`, `_family`) — ЛОКАЛЬНАЯ копия
(докстрока `test_context_operations.py`: наборы помощников тестов друг у
друга не импортируют).
"""
from __future__ import annotations

import contextlib
import datetime as dt
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy import event

import crud.semantic as crud_semantic
import services.work_families as work_families
from models import (
    CatalogContext,
    ComparabilityReason,
    ContextBucket,
    ContextMember,
    ContextRoutingRule,
    DecisionSource,
    FamilyStatus,
    MembershipState,
    NameRole,
    RoutedBy,
    SemanticKind,
    SemanticState,
    WorkCategory,
    WorkFamily,
)
from services import context_operations
from services.context_routing import PREDICATE_NEAREST_CHAPTER_EQUALS
from services.unit_resolution import UnitResolver

pytestmark = pytest.mark.integration

BASE = "/api/v1/semantic"

# ---------------------------------------------------------------------------
#  Литерал восемнадцати маршрутов плана (Task 12, Interfaces) — НЕЗАВИСИМЫЙ
#  от `app.routes`: перебор прав обязан ловить забытый `require_admin` на
#  ОДНОМ маршруте, а не читать список из того же дерева, которое проверяет.
# ---------------------------------------------------------------------------
EIGHTEEN_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/api/v1/semantic/families"),
    ("POST", "/api/v1/semantic/families"),
    ("PATCH", "/api/v1/semantic/families/1"),
    ("POST", "/api/v1/semantic/families/1/activate"),
    ("POST", "/api/v1/semantic/families/1/archive"),
    ("POST", "/api/v1/semantic/families/1/merge"),
    ("GET", "/api/v1/semantic/contexts"),
    ("GET", "/api/v1/semantic/contexts/1"),
    ("POST", "/api/v1/semantic/contexts/1/kind"),
    ("POST", "/api/v1/semantic/contexts/1/name-role"),
    ("POST", "/api/v1/semantic/contexts/1/family"),
    ("POST", "/api/v1/semantic/contexts/1/split"),
    ("POST", "/api/v1/semantic/contexts/1/merge"),
    ("POST", "/api/v1/semantic/contexts/1/archive"),
    ("POST", "/api/v1/semantic/members/move"),
    ("GET", "/api/v1/semantic/members/1/transfer-proposal"),
    ("POST", "/api/v1/semantic/members/1/transfer"),
    ("POST", "/api/v1/semantic/members/accept-target-decision"),
)
assert len(EIGHTEEN_ROUTES) == 18


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _proposal(factories):
    estimate = factories.EstimateFactory.create()
    lot = factories.LotFactory.create(estimate=estimate)
    return factories.ProposalFactory.create(lot=lot)


def _chapter(factories, proposal, *, title="Раздел", category_id=None, category_source="file"):
    kwargs = dict(
        proposal=proposal, is_chapter=True, job_title_in_proposal=title,
        chapter_number_in_proposal="1",
    )
    if category_id is not None:
        kwargs["work_category_id"] = category_id
        kwargs["category_source"] = category_source
    return factories.PositionItemFactory.create(**kwargs)


def _position(factories, proposal, *, chapter=None, catalog_position=None, title="Работа"):
    kwargs = dict(proposal=proposal, is_chapter=False, job_title_in_proposal=title)
    if chapter is not None:
        kwargs["chapter_item_id"] = chapter.id
    if catalog_position is not None:
        kwargs["catalog_position_id"] = catalog_position.id
    return factories.PositionItemFactory.create(**kwargs)


def _leaf_category_ids(session, n=1) -> list[int]:
    """N различных ЛИСТЬЕВ классификатора (не использованных как parent_id) —
    тот же приём, что `test_context_routing.py::_leaf_category_ids`."""
    used_as_parent = sa.select(WorkCategory.parent_id).where(WorkCategory.parent_id.is_not(None))
    return list(
        session.execute(
            sa.select(WorkCategory.id)
            .where(WorkCategory.id.not_in(used_as_parent))
            .order_by(WorkCategory.sort_order)
            .limit(n)
        )
        .scalars()
        .all()
    )


def _unit_id(db, code: str) -> int:
    return UnitResolver(db).resolve(code).unit_id


def _bucket(db, *, catalog_position, work_category_id=None) -> ContextBucket:
    bucket = ContextBucket(catalog_position_id=catalog_position.id, work_category_id=work_category_id)
    db.add(bucket)
    db.flush()
    return bucket


def _context(db, bucket, *, is_default=True, **overrides) -> CatalogContext:
    defaults = dict(
        bucket_id=bucket.id,
        is_default=is_default,
        semantic_kind=SemanticKind.WORK.value,
        semantic_kind_source=DecisionSource.rule.value,
        semantic_kind_at=_now(),
        name_role=NameRole.WORK.value,
        name_role_source=DecisionSource.rule.value,
        name_role_at=_now(),
        place_dictionary_version=1,
        semantic_state=SemanticState.SUGGESTED.value,
    )
    defaults.update(overrides)
    ctx = CatalogContext(**defaults)
    db.add(ctx)
    db.flush()
    return ctx


def _member(
    db, position_item, context, *,
    membership_state=MembershipState.CURRENT.value,
    routed_by=RoutedBy.default.value,
    conflict_at=None,
    conflict_from_context_id=None,
) -> ContextMember:
    member = ContextMember(
        position_item_id=position_item.id,
        context_id=context.id,
        bucket_id=context.bucket_id,
        membership_state=membership_state,
        routed_by=routed_by,
        conflict_at=conflict_at,
        conflict_from_context_id=conflict_from_context_id,
    )
    db.add(member)
    db.flush()
    return member


def _rule(db, bucket, *, context, admin, ordinal=1, predicate=None) -> ContextRoutingRule:
    rule = ContextRoutingRule(
        bucket_id=bucket.id,
        ordinal=ordinal,
        predicate=predicate or {"kind": PREDICATE_NEAREST_CHAPTER_EQUALS, "value": "Раздел"},
        context_id=context.id,
        created_by=admin.id,
    )
    db.add(rule)
    db.flush()
    return rule


def _family(db, admin, *, title="Семья", unit_name=None, definition="Определение", active=True):
    fam = work_families.create_family(
        db, title=title, unit_name=unit_name, definition=definition, actor_id=admin.id
    )
    if active:
        work_families.activate_family(db, family_id=fam.id, actor_id=admin.id)
        db.expire(fam)
    return fam


@contextlib.contextmanager
def _capturing_sql(session):
    """Перехватывает каждый SQL-текст, реально отправленный на этом
    соединении (`before_cursor_execute`) — тот же приём, что
    `test_context_operations.py::_capturing_sql`."""
    statements: list[str] = []

    def _listener(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    connection = session.connection()
    event.listen(connection, "before_cursor_execute", _listener)
    try:
        yield statements
    finally:
        event.remove(connection, "before_cursor_execute", _listener)


# ---------------------------------------------------------------------------
#  Инвентарь маршрутов
# ---------------------------------------------------------------------------

def _collect_semantic_routes() -> set[tuple[str, str]]:
    """Множество путей под `/api/v1/semantic`, собранное из `app.routes`
    (план, задача 12, «Утверждения») — разворачивает `_IncludedRouter`
    (FastAPI ≥0.139), тот же приём, что `tests/test_auth_coverage.py`."""
    from main import app

    def walk(routes, prefix: str = "") -> list[tuple[str, str]]:
        collected: list[tuple[str, str]] = []
        for route in routes:
            included = getattr(route, "original_router", None)
            if included is not None:
                ctx = getattr(route, "include_context", None)
                sub_prefix = getattr(ctx, "prefix", "") or ""
                collected.extend(walk(included.routes, prefix + sub_prefix))
            elif hasattr(route, "methods") and getattr(route, "path", None) is not None:
                for method in route.methods:
                    if method in ("HEAD", "OPTIONS"):
                        continue
                    collected.append((method, prefix + route.path))
            elif hasattr(route, "routes"):
                collected.extend(walk(route.routes, prefix + getattr(route, "path", "")))
        return collected

    all_routes = walk(app.routes)
    return {(method, path) for method, path in all_routes if path.startswith(BASE)}


#: Та же восемнадцать маршрутов, но ШАБЛОНАМИ пути — так их несёт
#: `app.routes` (`{family_id}`, а не подставленный `1` из EIGHTEEN_ROUTES,
#: который existует ради HTTP-вызовов теста прав).
EIGHTEEN_ROUTE_TEMPLATES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", f"{BASE}/families"),
        ("POST", f"{BASE}/families"),
        ("PATCH", f"{BASE}/families/{{family_id}}"),
        ("POST", f"{BASE}/families/{{family_id}}/activate"),
        ("POST", f"{BASE}/families/{{family_id}}/archive"),
        ("POST", f"{BASE}/families/{{family_id}}/merge"),
        ("GET", f"{BASE}/contexts"),
        ("GET", f"{BASE}/contexts/{{context_id}}"),
        ("POST", f"{BASE}/contexts/{{context_id}}/kind"),
        ("POST", f"{BASE}/contexts/{{context_id}}/name-role"),
        ("POST", f"{BASE}/contexts/{{context_id}}/family"),
        ("POST", f"{BASE}/contexts/{{context_id}}/split"),
        ("POST", f"{BASE}/contexts/{{context_id}}/merge"),
        ("POST", f"{BASE}/contexts/{{context_id}}/archive"),
        ("POST", f"{BASE}/members/move"),
        ("GET", f"{BASE}/members/{{position_item_id}}/transfer-proposal"),
        ("POST", f"{BASE}/members/{{position_item_id}}/transfer"),
        ("POST", f"{BASE}/members/accept-target-decision"),
    }
)
assert len(EIGHTEEN_ROUTE_TEMPLATES) == 18


def test_route_set_under_prefix_equals_eighteen_literal():
    """Множество путей под `/api/v1/semantic`, собранное из `app.routes`,
    равно литералу восемнадцати (план, задача 12, «Утверждения») —
    единственное место, где такое утверждение осмысленно (задача 6 роутера
    ещё не заводила); маршрута восстановления архивного контекста
    (`…/restore`) в нём нет."""
    collected = _collect_semantic_routes()
    assert collected == EIGHTEEN_ROUTE_TEMPLATES
    assert not any(path.endswith("/restore") for _method, path in collected)


# ---------------------------------------------------------------------------
#  Права: КАЖДЫЙ из восемнадцати маршрутов
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method,path", EIGHTEEN_ROUTES)
def test_member_rejected_on_every_route(method, path, member_client):
    response = member_client.request(method, path)
    assert response.status_code == 403, f"{method} {path} -> {response.status_code}"


@pytest.mark.parametrize("method,path", EIGHTEEN_ROUTES)
def test_unauthenticated_rejected_on_every_route(method, path, anon_client):
    response = anon_client.request(method, path)
    assert response.status_code in (401, 403), f"{method} {path} -> {response.status_code}"


# ---------------------------------------------------------------------------
#  Семьи
# ---------------------------------------------------------------------------

class TestFamilies:
    def test_create_activate_list_shows_context_count(self, admin_client, db_session, factories):
        created = admin_client.post(
            f"{BASE}/families", json={"title": "Стяжка пола", "unit_name": "M2", "definition": None}
        )
        assert created.status_code == 201
        family_id = created.json()["id"]
        assert created.json()["status"] == FamilyStatus.draft.value

        activated = admin_client.post(
            f"{BASE}/families/{family_id}/activate"
        )
        # Без определения активация отказывает 409 с кодом и family_id.
        assert activated.status_code == 409
        body = activated.json()["detail"]
        assert body["code"] == work_families.REFUSE_ACTIVATE_WITHOUT_DEFINITION
        assert body["family_id"] == family_id

        patched = admin_client.patch(
            f"{BASE}/families/{family_id}", json={"definition": "Снятие и устройство стяжки"}
        )
        assert patched.status_code == 200
        activated = admin_client.post(f"{BASE}/families/{family_id}/activate")
        assert activated.status_code == 200
        assert activated.json()["status"] == FamilyStatus.active.value

        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        ctx.work_family_id = family_id
        ctx.family_source = "manual"
        ctx.family_by = admin_client.user.id
        ctx.family_at = _now()
        db_session.flush()

        listed = admin_client.get(f"{BASE}/families", params={"status": "active"})
        assert listed.status_code == 200
        rows = {row["id"]: row for row in listed.json()["items"]}
        assert rows[family_id]["context_count"] == 1

        # Правка единицы недоступна при живых привязках — 409, число.
        setu = admin_client.patch(f"{BASE}/families/{family_id}", json={"title": "Переименовано"})
        assert setu.status_code == 200  # правка имени живым привязкам не мешает

        archived = admin_client.post(f"{BASE}/families/{family_id}/archive")
        assert archived.status_code == 409
        archived_detail = archived.json()["detail"]
        assert archived_detail["code"] == work_families.REFUSE_ARCHIVE_WITH_LINKS
        assert archived_detail["count"] == 1

    def test_list_families_filters_by_unit(self, admin_client, db_session):
        m2 = _unit_id(db_session, "M2")
        pcs = _unit_id(db_session, "PCS")
        fam_m2 = _family(db_session, admin_client.user, title="По площади", unit_name="M2")
        _family(db_session, admin_client.user, title="Поштучно", unit_name="PCS")

        listed = admin_client.get(f"{BASE}/families", params={"unit_id": m2})
        ids = {row["id"] for row in listed.json()["items"]}
        assert fam_m2.id in ids
        listed_pcs = admin_client.get(f"{BASE}/families", params={"unit_id": pcs})
        assert fam_m2.id not in {row["id"] for row in listed_pcs.json()["items"]}

    def test_merge_families_moves_contexts_and_reports_count(self, admin_client, db_session, factories):
        m2 = "M2"
        source = _family(db_session, admin_client.user, title="Источник", unit_name=m2)
        target = _family(db_session, admin_client.user, title="Цель", unit_name=m2)
        cp = factories.CatalogPositionFactory.create(unit_id=_unit_id(db_session, m2))
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        ctx.work_family_id = source.id
        ctx.family_source = "manual"
        ctx.family_by = admin_client.user.id
        ctx.family_at = _now()
        db_session.flush()

        response = admin_client.post(
            f"{BASE}/families/{source.id}/merge", json={"target_family_id": target.id}
        )
        assert response.status_code == 200
        assert response.json()["moved_contexts"] == 1

        # Источник и цель совпадают — 422, код merge_same_family.
        same = admin_client.post(f"{BASE}/families/{target.id}/merge", json={"target_family_id": target.id})
        assert same.status_code == 422
        assert same.json()["detail"]["code"] == work_families.REFUSE_MERGE_SAME_FAMILY

    def test_family_not_found_gives_404_with_code(self, admin_client):
        response = admin_client.patch(f"{BASE}/families/999999999", json={"title": "x"})
        assert response.status_code == 404
        detail = response.json()["detail"]
        assert detail["code"] == work_families.REFUSE_FAMILY_NOT_FOUND
        assert detail["family_id"] == 999999999

    def test_patch_family_unit_name_reaches_set_unit(self, admin_client, db_session, factories):
        """`PATCH` с `unit_name` реально доходит до
        `set_unit` — успех без привязок, единица меняется; неизвестная
        единица — код `unknown_unit`; живая привязка — `409` с `count`."""
        fam = _family(db_session, admin_client.user, title="СменаЕдиницы", unit_name="M2")
        db_session.commit()
        pcs = _unit_id(db_session, "PCS")

        changed = admin_client.patch(f"{BASE}/families/{fam.id}", json={"unit_name": "PCS"})
        assert changed.status_code == 200
        assert changed.json()["unit_id"] == pcs

        unknown = admin_client.patch(
            f"{BASE}/families/{fam.id}", json={"unit_name": "не-единица-xyz-probe"}
        )
        assert unknown.status_code == 422
        assert unknown.json()["detail"]["code"] == work_families.REFUSE_UNKNOWN_UNIT

        cp = factories.CatalogPositionFactory.create(unit_id=pcs)
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        ctx.work_family_id = fam.id
        ctx.family_source = "manual"
        ctx.family_by = admin_client.user.id
        ctx.family_at = _now()
        db_session.commit()

        with_links = admin_client.patch(f"{BASE}/families/{fam.id}", json={"unit_name": "M2"})
        assert with_links.status_code == 409
        with_links_detail = with_links.json()["detail"]
        assert with_links_detail["code"] == work_families.REFUSE_UNIT_CHANGE_WITH_LINKS
        assert with_links_detail["count"] == 1

    def test_list_families_filters_by_status_both_sides(self, admin_client, db_session):
        draft = _family(db_session, admin_client.user, title="Черновик", unit_name=None, active=False)
        active = _family(db_session, admin_client.user, title="Активная", unit_name=None, active=True)

        by_draft = admin_client.get(f"{BASE}/families", params={"status": "draft"})
        draft_ids = {row["id"] for row in by_draft.json()["items"]}
        assert draft.id in draft_ids
        assert active.id not in draft_ids

        by_active = admin_client.get(f"{BASE}/families", params={"status": "active"})
        active_ids = {row["id"] for row in by_active.json()["items"]}
        assert active.id in active_ids
        assert draft.id not in active_ids

    def test_list_families_context_count_supports_zero_and_multiple(
        self, admin_client, db_session, factories
    ):
        """Проверяются 0 и 2, не только `1`:
        `count(*)` вместо `count(CatalogContext.id)` на outer join —
        у семьи без контекстов outer join даёт одну строку с NULL,
        `count(*)` посчитал бы её единицей вместо нуля."""
        m2 = _unit_id(db_session, "M2")
        zero = _family(db_session, admin_client.user, title="БезКонтекстов", unit_name="M2")
        many = _family(db_session, admin_client.user, title="СДвумяКонтекстами", unit_name="M2")

        for _ in range(2):
            cp = factories.CatalogPositionFactory.create(unit_id=m2)
            bucket = _bucket(db_session, catalog_position=cp)
            ctx = _context(db_session, bucket)
            ctx.work_family_id = many.id
            ctx.family_source = "manual"
            ctx.family_by = admin_client.user.id
            ctx.family_at = _now()
        db_session.flush()

        listed = admin_client.get(f"{BASE}/families")
        rows = {row["id"]: row for row in listed.json()["items"]}
        assert rows[zero.id]["context_count"] == 0
        assert rows[many.id]["context_count"] == 2


# ---------------------------------------------------------------------------
#  Контексты — очередь: фильтры
# ---------------------------------------------------------------------------

class TestContextsQueue:
    def _scene(self, db_session, factories):
        """Пять контекстов: устаревшее членство, конфликтное И устаревшее
        разом, конфликтное БЕЗ устаревания, пустой
        (без членств) и обычный. Вход со STALE и conflict_at ОДНОВРЕМЕННО
        обязан попасть в ОБА первых фильтра (план, задача 12,
        «Утверждения»); вход с ТОЛЬКО conflict_at обязан НЕ попасть в
        `has_stale_members=True` — иначе фильтр устаревших слит с
        конфликтным."""
        cp1 = factories.CatalogPositionFactory.create(standard_job_title="Штукатурка стен")
        cp2 = factories.CatalogPositionFactory.create(standard_job_title="Окраска потолка")
        cp3 = factories.CatalogPositionFactory.create(standard_job_title="Пустой контекст")
        cp4 = factories.CatalogPositionFactory.create(standard_job_title="Обычный контекст")
        cp5 = factories.CatalogPositionFactory.create(standard_job_title="Только конфликт")

        proposal = _proposal(factories)

        bucket_stale = _bucket(db_session, catalog_position=cp1)
        ctx_stale = _context(db_session, bucket_stale)
        pos_stale = _position(factories, proposal, catalog_position=cp1)
        _member(db_session, pos_stale, ctx_stale, membership_state=MembershipState.STALE.value)

        bucket_both = _bucket(db_session, catalog_position=cp2)
        ctx_both = _context(db_session, bucket_both)
        pos_both = _position(factories, proposal, catalog_position=cp2)
        _member(
            db_session, pos_both, ctx_both,
            membership_state=MembershipState.STALE.value,
            conflict_at=_now(), conflict_from_context_id=ctx_stale.id,
        )

        bucket_empty = _bucket(db_session, catalog_position=cp3)
        ctx_empty = _context(db_session, bucket_empty)

        bucket_plain = _bucket(db_session, catalog_position=cp4)
        ctx_plain = _context(db_session, bucket_plain)
        pos_plain = _position(factories, proposal, catalog_position=cp4)
        _member(db_session, pos_plain, ctx_plain)

        bucket_conflict_only = _bucket(db_session, catalog_position=cp5)
        ctx_conflict_only = _context(db_session, bucket_conflict_only)
        pos_conflict_only = _position(factories, proposal, catalog_position=cp5)
        _member(
            db_session, pos_conflict_only, ctx_conflict_only,
            membership_state=MembershipState.CURRENT.value,
            conflict_at=_now(), conflict_from_context_id=ctx_stale.id,
        )

        return {
            "stale": ctx_stale.id, "both": ctx_both.id, "empty": ctx_empty.id, "plain": ctx_plain.id,
            "conflict_only": ctx_conflict_only.id,
        }

    def test_has_stale_members_both_sides(self, admin_client, db_session, factories):
        ids = self._scene(db_session, factories)
        stale_true = admin_client.get(f"{BASE}/contexts", params={"has_stale_members": True})
        stale_ids = {row["id"] for row in stale_true.json()["items"]}
        assert ids["stale"] in stale_ids
        assert ids["both"] in stale_ids
        assert ids["plain"] not in stale_ids
        # Конфликт БЕЗ устаревания не попадает в
        # `has_stale_members=True` — иначе фильтр слит с конфликтным.
        assert ids["conflict_only"] not in stale_ids

        stale_false = admin_client.get(f"{BASE}/contexts", params={"has_stale_members": False})
        stale_false_ids = {row["id"] for row in stale_false.json()["items"]}
        assert ids["plain"] in stale_false_ids
        assert ids["stale"] not in stale_false_ids
        assert ids["conflict_only"] in stale_false_ids

    def test_has_conflicting_members_both_sides_and_independent_axis(self, admin_client, db_session, factories):
        ids = self._scene(db_session, factories)
        conflict_true = admin_client.get(f"{BASE}/contexts", params={"has_conflicting_members": True})
        conflict_ids = {row["id"] for row in conflict_true.json()["items"]}
        # Решающий вход плана: STALE + conflict_at ОДНОВРЕМЕННО — попадает
        # И в has_stale_members, И в has_conflicting_members (обе оси).
        assert ids["both"] in conflict_ids
        assert ids["stale"] not in conflict_ids  # устарело, но без конфликта
        assert ids["conflict_only"] in conflict_ids  # конфликт без устаревания — тоже конфликт

        conflict_false = admin_client.get(f"{BASE}/contexts", params={"has_conflicting_members": False})
        conflict_false_ids = {row["id"] for row in conflict_false.json()["items"]}
        assert ids["stale"] in conflict_false_ids
        assert ids["both"] not in conflict_false_ids

    def test_has_no_members_both_sides(self, admin_client, db_session, factories):
        ids = self._scene(db_session, factories)
        empty_true = admin_client.get(f"{BASE}/contexts", params={"has_no_members": True})
        empty_ids = {row["id"] for row in empty_true.json()["items"]}
        assert ids["empty"] in empty_ids
        assert ids["plain"] not in empty_ids

        empty_false = admin_client.get(f"{BASE}/contexts", params={"has_no_members": False})
        empty_false_ids = {row["id"] for row in empty_false.json()["items"]}
        assert ids["plain"] in empty_false_ids
        assert ids["empty"] not in empty_false_ids

    def test_catalog_query_and_semantic_state_filters(self, admin_client, db_session, factories):
        ids = self._scene(db_session, factories)
        by_query = admin_client.get(f"{BASE}/contexts", params={"catalog_query": "Штукатурка"})
        assert {row["id"] for row in by_query.json()["items"]} == {ids["stale"]}

        by_state = admin_client.get(f"{BASE}/contexts", params={"semantic_state": "SUGGESTED"})
        state_ids = {row["id"] for row in by_state.json()["items"]}
        assert ids["stale"] in state_ids
        # И НЕсовпадающий вход — все контексты сцены
        # SUGGESTED, NOT_APPLICABLE не встречается вовсе.
        by_other_state = admin_client.get(f"{BASE}/contexts", params={"semantic_state": "NOT_APPLICABLE"})
        assert ids["stale"] not in {row["id"] for row in by_other_state.json()["items"]}

    def test_catalog_query_matches_percent_literally(self, admin_client, db_session, factories):
        """`%` в `catalog_query` — буквальный текст, а
        не метасимвол `ILIKE`. Без экранирования `40%состав` стал бы
        паттерном «40, что угодно, состав» и совпал бы ТАКЖЕ со строкой
        без буквального `%` (`cp_wildcard_like` ниже) — решающий негативный
        вход, а не просто «нашёлся один результат»."""
        cp_literal = factories.CatalogPositionFactory.create(standard_job_title="Раствор 40%состав А")
        cp_wildcard_like = factories.CatalogPositionFactory.create(
            standard_job_title="Раствор 40шпаклёвкасостав А"
        )
        bucket_literal = _bucket(db_session, catalog_position=cp_literal)
        _context(db_session, bucket_literal)
        bucket_wildcard_like = _bucket(db_session, catalog_position=cp_wildcard_like)
        _context(db_session, bucket_wildcard_like)

        response = admin_client.get(f"{BASE}/contexts", params={"catalog_query": "40%состав"})
        titles = {row["standard_job_title"] for row in response.json()["items"]}
        assert titles == {"Раствор 40%состав А"}

    def test_semantic_kind_name_role_and_category_filters_both_sides(
        self, admin_client, db_session, factories
    ):
        """`semantic_kind`, `name_role`,
        `work_category_id` — ни один не передавал ни один тест.
        Два контекста с РАЗНЫМИ значениями по каждой оси, обе
        стороны каждого фильтра."""
        cat_x, cat_y = _leaf_category_ids(db_session, 2)
        cp_work = factories.CatalogPositionFactory.create(standard_job_title="Ось WORK")
        cp_system = factories.CatalogPositionFactory.create(standard_job_title="Ось SYSTEM")

        bucket_work = _bucket(db_session, catalog_position=cp_work, work_category_id=cat_x)
        ctx_work = _context(
            db_session, bucket_work,
            semantic_kind=SemanticKind.WORK.value, name_role=NameRole.WORK.value,
        )
        bucket_system = _bucket(db_session, catalog_position=cp_system, work_category_id=cat_y)
        ctx_system = _context(
            db_session, bucket_system,
            semantic_kind=SemanticKind.SYSTEM.value, name_role=NameRole.LOCATION_ONLY.value,
        )

        by_kind_work = admin_client.get(f"{BASE}/contexts", params={"semantic_kind": "WORK"})
        kind_work_ids = {row["id"] for row in by_kind_work.json()["items"]}
        assert ctx_work.id in kind_work_ids
        assert ctx_system.id not in kind_work_ids

        by_kind_system = admin_client.get(f"{BASE}/contexts", params={"semantic_kind": "SYSTEM"})
        kind_system_ids = {row["id"] for row in by_kind_system.json()["items"]}
        assert ctx_system.id in kind_system_ids
        assert ctx_work.id not in kind_system_ids

        by_role_work = admin_client.get(f"{BASE}/contexts", params={"name_role": "WORK"})
        role_work_ids = {row["id"] for row in by_role_work.json()["items"]}
        assert ctx_work.id in role_work_ids
        assert ctx_system.id not in role_work_ids

        by_role_location = admin_client.get(f"{BASE}/contexts", params={"name_role": "LOCATION_ONLY"})
        role_location_ids = {row["id"] for row in by_role_location.json()["items"]}
        assert ctx_system.id in role_location_ids
        assert ctx_work.id not in role_location_ids

        by_cat_x = admin_client.get(f"{BASE}/contexts", params={"work_category_id": cat_x})
        cat_x_ids = {row["id"] for row in by_cat_x.json()["items"]}
        assert ctx_work.id in cat_x_ids
        assert ctx_system.id not in cat_x_ids

        by_cat_y = admin_client.get(f"{BASE}/contexts", params={"work_category_id": cat_y})
        cat_y_ids = {row["id"] for row in by_cat_y.json()["items"]}
        assert ctx_system.id in cat_y_ids
        assert ctx_work.id not in cat_y_ids

    def test_list_contexts_query_count_independent_of_row_count(self, admin_client, db_session, factories):
        """Число запросов `list_contexts` не зависит от числа строк выдачи
        (план, задача 12, «Утверждения») — считает СЧЁТЧИКОМ `before_cursor_
        execute` на выдаче 2 и 10 строк, число одинаковое."""
        for i in range(10):
            cp = factories.CatalogPositionFactory.create(standard_job_title=f"Позиция N+1 {i}")
            bucket = _bucket(db_session, catalog_position=cp)
            _context(db_session, bucket)
        db_session.flush()

        with _capturing_sql(db_session) as statements_small:
            response_small = admin_client.get(
                f"{BASE}/contexts", params={"catalog_query": "Позиция N+1", "limit": 2}
            )
        assert response_small.status_code == 200
        assert len(response_small.json()["items"]) == 2
        count_small = len(statements_small)

        with _capturing_sql(db_session) as statements_large:
            response_large = admin_client.get(
                f"{BASE}/contexts", params={"catalog_query": "Позиция N+1", "limit": 10}
            )
        assert response_large.status_code == 200
        assert len(response_large.json()["items"]) == 10
        count_large = len(statements_large)

        assert count_small == count_large, (count_small, count_large)


# ---------------------------------------------------------------------------
#  Контексты — карточка
# ---------------------------------------------------------------------------

class TestContextCard:
    def test_card_reports_writing_article_kind_role_family_and_journal(
        self, admin_client, db_session, factories
    ):
        (category_id,) = _leaf_category_ids(db_session, 1)
        category = db_session.get(WorkCategory, category_id)
        m2 = _unit_id(db_session, "M2")
        cp = factories.CatalogPositionFactory.create(standard_job_title="Кладка кирпича", unit_id=m2)
        proposal = _proposal(factories)
        chapter = _chapter(factories, proposal, title="Стены", category_id=category_id, category_source="manual")
        bucket = _bucket(db_session, catalog_position=cp, work_category_id=category_id)
        # Изначальный вид — SYSTEM, а не WORK, которым ниже подтверждается:
        # без этого разрыва тест не отличал бы
        # применение `body.kind` от его игнорирования — оба дали бы WORK.
        # `comparability_reason` — непустое значение.
        ctx = _context(
            db_session, bucket,
            semantic_kind=SemanticKind.SYSTEM.value,
            comparability_reason=ComparabilityReason.insufficient_description.value,
        )
        position = _position(factories, proposal, chapter=chapter, catalog_position=cp)
        _member(db_session, position, ctx)
        # Второе членство — число членств обязано быть ≥ 2, не единственное
        # проверенное значение.
        position_two = _position(factories, proposal, catalog_position=cp, title="Кладка кирпича, доп. позиция")
        _member(db_session, position_two, ctx)

        family = _family(db_session, admin_client.user, title="Кладка", unit_name="M2")
        assign = admin_client.post(f"{BASE}/contexts/{ctx.id}/family", json={"family_id": family.id})
        assert assign.status_code == 200

        confirm = admin_client.post(f"{BASE}/contexts/{ctx.id}/kind", json={"kind": "WORK"})
        assert confirm.status_code == 200

        role = admin_client.post(f"{BASE}/contexts/{ctx.id}/name-role", json={"role": "WORK"})
        assert role.status_code == 200

        card = admin_client.get(f"{BASE}/contexts/{ctx.id}")
        assert card.status_code == 200
        body = card.json()
        assert body["standard_job_title"] == "Кладка кирпича"
        assert body["unit_code"] == "M2"
        assert body["work_category_id"] == category_id
        assert body["work_category_code"] == category.code
        assert body["work_category_source"] == "manual"
        # Вид — WORK, ХОТЯ контекст создан SYSTEM: доказывает, что маршрут
        # `POST .../kind` реально применяет `body.kind`, а не игнорирует его.
        assert body["semantic_kind"] == "WORK"
        assert body["semantic_kind_source"] == DecisionSource.manual.value
        assert body["name_role"] == "WORK"
        assert body["name_role_source"] == DecisionSource.manual.value
        assert body["place_dictionary_version"] >= 1
        assert body["comparability_reason"] == ComparabilityReason.insufficient_description.value
        assert body["work_family_id"] == family.id
        assert body["family_title"] == "Кладка"
        assert body["family_source"] == "manual"
        assert body["member_count"] == 2

        event_types = [event["event_type"] for event in body["events"]]
        assert event_types == ["context_family_assigned", "kind_set", "name_role_set"]
        # Журнал по времени — неубывающая последовательность created_at.
        timestamps = [event["created_at"] for event in body["events"]]
        assert timestamps == sorted(timestamps)

    def test_card_reports_dictionary_version_from_context_not_hardcoded(
        self, admin_client, db_session, factories
    ):
        """Фикстура версии словаря — 2,
        а не совпадающая с константой `PLACE_DICTIONARY_VERSION` (обычно 1)
        — иначе тест не отличил бы честное чтение поля от захардкоженной
        единицы. Ни одна операция теста НЕ вызывает `POST .../name-role`:
        тот маршрут пишет ТЕКУЩУЮ константу словаря поверх фикстуры (спека
        §2.7) и свёл бы фикстуру обратно к 1."""
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket, place_dictionary_version=2)

        card = admin_client.get(f"{BASE}/contexts/{ctx.id}")
        assert card.status_code == 200
        assert card.json()["place_dictionary_version"] == 2

    def test_card_not_found_gives_404(self, admin_client):
        response = admin_client.get(f"{BASE}/contexts/999999999")
        assert response.status_code == 404

    def test_card_lists_members_with_job_title_and_estimate(self, admin_client, db_session, factories):
        """Экран не может предложить действие по членству, не видя его id
        и состояния — `member_count` был только агрегатом. `members` несёт
        id позиции, её название ПО
        СМЕТЕ (`job_title_in_proposal`, не каталожное имя — они могут
        расходиться) и id сметы, куда эта позиция ведёт."""
        # id лота обязан отличаться от id сметы: при совпадении (на свежей базе
        # последовательности идут вровень) `Proposal.lot_id` вместо
        # `Lot.estimate_id` прошёл бы сверку `estimate_id` ниже.
        estimate = factories.EstimateFactory.create()
        lot = factories.LotFactory.create(estimate=estimate)
        if lot.id == estimate.id:
            lot = factories.LotFactory.create(estimate=estimate)
        assert lot.id != estimate.id
        proposal = factories.ProposalFactory.create(lot=lot)
        cp = factories.CatalogPositionFactory.create(standard_job_title="Кладка кирпича")
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        position_a = _position(factories, proposal, catalog_position=cp, title="Кладка кирпича, поз. А")
        position_b = _position(factories, proposal, catalog_position=cp, title="Кладка кирпича, поз. Б")
        _member(db_session, position_a, ctx)
        _member(db_session, position_b, ctx)
        db_session.flush()

        card = admin_client.get(f"{BASE}/contexts/{ctx.id}")
        assert card.status_code == 200
        body = card.json()
        assert body["member_count"] == 2
        assert body["members_truncated"] is False
        members = body["members"]
        assert len(members) == 2
        # Порядок — по `position_item_id`, тот же детерминизм, что у
        # представительного членства карточки выше.
        ids = [m["position_item_id"] for m in members]
        assert ids == sorted(ids)
        by_id = {m["position_item_id"]: m for m in members}
        assert by_id[position_a.id]["job_title"] == "Кладка кирпича, поз. А"
        assert by_id[position_b.id]["job_title"] == "Кладка кирпича, поз. Б"
        assert by_id[position_a.id]["estimate_id"] == estimate.id
        assert by_id[position_b.id]["estimate_id"] == estimate.id
        assert by_id[position_a.id]["membership_state"] == MembershipState.CURRENT.value

    def test_card_member_reports_stale_and_conflict_fields(self, admin_client, db_session, factories):
        """Устаревшее и конфликтное членства несут РАЗНЫЕ факты (спека §2.5):
        `membership_state=STALE` у одного, `conflict_at`/
        `conflict_from_context_id` у другого — экран различает их действия
        («принять предложение переноса» / «принять решение цели») ИМЕННО по
        этим полям, а не по догадке."""
        estimate = factories.EstimateFactory.create()
        lot = factories.LotFactory.create(estimate=estimate)
        proposal = factories.ProposalFactory.create(lot=lot)
        (other_category_id,) = _leaf_category_ids(db_session, 1)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        # Другая корзина — обязана нести ДРУГУЮ статью: без неё вторая
        # безстатейная корзина той же строки нарушила бы
        # `uq_context_buckets_position_category` (COALESCE(work_category_id, -1)).
        other_bucket = _bucket(db_session, catalog_position=cp, work_category_id=other_category_id)
        other_ctx = _context(db_session, other_bucket, is_default=False)

        stale_position = _position(factories, proposal, catalog_position=cp, title="Устаревшее членство")
        _member(db_session, stale_position, ctx, membership_state=MembershipState.STALE.value)

        conflicted_position = _position(factories, proposal, catalog_position=cp, title="Конфликтное членство")
        _member(
            db_session, conflicted_position, ctx,
            conflict_at=_now(), conflict_from_context_id=other_ctx.id,
            routed_by=RoutedBy.manual.value,
        )
        db_session.flush()

        card = admin_client.get(f"{BASE}/contexts/{ctx.id}")
        assert card.status_code == 200
        by_id = {m["position_item_id"]: m for m in card.json()["members"]}

        stale = by_id[stale_position.id]
        assert stale["membership_state"] == MembershipState.STALE.value
        assert stale["conflict_at"] is None
        assert stale["conflict_from_context_id"] is None

        conflicted = by_id[conflicted_position.id]
        assert conflicted["membership_state"] == MembershipState.CURRENT.value
        assert conflicted["conflict_at"] is not None
        assert conflicted["conflict_from_context_id"] == other_ctx.id
        assert conflicted["routed_by"] == RoutedBy.manual.value

    def test_card_members_truncated_at_cap_member_count_stays_full(
        self, admin_client, db_session, factories, monkeypatch
    ):
        """Потолок — монки-патч константы, не 501 реальная строка:
        `member_count` обязан остаться ПОЛНЫМ (3), а `members` — обрезанным
        до потолка (2), с `members_truncated=True`."""
        monkeypatch.setattr(crud_semantic, "CONTEXT_MEMBERS_PAGE_CAP", 2)
        estimate = factories.EstimateFactory.create()
        lot = factories.LotFactory.create(estimate=estimate)
        proposal = factories.ProposalFactory.create(lot=lot)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        for i in range(3):
            position = _position(factories, proposal, catalog_position=cp, title=f"Позиция {i}")
            _member(db_session, position, ctx)
        db_session.flush()

        card = admin_client.get(f"{BASE}/contexts/{ctx.id}")
        assert card.status_code == 200
        body = card.json()
        assert body["member_count"] == 3
        assert len(body["members"]) == 2
        assert body["members_truncated"] is True

    def test_card_members_exactly_at_cap_are_not_truncated(
        self, admin_client, db_session, factories, monkeypatch
    ):
        """Вторая граница потолка: РОВНО потолок членств — не обрезка.
        `members_truncated` обязан быть `False`, иначе `>=` вместо `>`
        (или `LIMIT CAP` без `+1`) прошёл бы тест «больше потолка» выше."""
        monkeypatch.setattr(crud_semantic, "CONTEXT_MEMBERS_PAGE_CAP", 2)
        estimate = factories.EstimateFactory.create()
        lot = factories.LotFactory.create(estimate=estimate)
        proposal = factories.ProposalFactory.create(lot=lot)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        for i in range(2):
            position = _position(factories, proposal, catalog_position=cp, title=f"Позиция {i}")
            _member(db_session, position, ctx)
        db_session.flush()

        body = admin_client.get(f"{BASE}/contexts/{ctx.id}").json()
        assert body["member_count"] == 2
        assert len(body["members"]) == 2
        assert body["members_truncated"] is False

    def test_card_members_ordered_by_position_even_when_inserted_in_reverse(
        self, admin_client, db_session, factories, monkeypatch
    ):
        """Порядок по `position_item_id` — свойство запроса, а не порядка
        вставки: членства заводятся в ОБРАТНОМ порядке id, и при потолке 2 из 3
        карточка обязана отдать два НАИМЕНЬШИХ id, по возрастанию."""
        monkeypatch.setattr(crud_semantic, "CONTEXT_MEMBERS_PAGE_CAP", 2)
        estimate = factories.EstimateFactory.create()
        lot = factories.LotFactory.create(estimate=estimate)
        proposal = factories.ProposalFactory.create(lot=lot)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        positions = [
            _position(factories, proposal, catalog_position=cp, title=f"Позиция {i}") for i in range(3)
        ]
        for position in reversed(positions):
            _member(db_session, position, ctx)
            db_session.flush()

        body = admin_client.get(f"{BASE}/contexts/{ctx.id}").json()
        expected = sorted(p.id for p in positions)[:2]
        assert [m["position_item_id"] for m in body["members"]] == expected

    def test_card_members_query_count_independent_of_member_count(
        self, admin_client, db_session, factories
    ):
        """Тот же приём, что `test_list_contexts_query_count_independent_
        of_row_count`: число запросов карточки не растёт вместе с числом
        членств — один ограниченный запрос членств, а не N+1 по каждому."""
        estimate = factories.EstimateFactory.create()
        lot = factories.LotFactory.create(estimate=estimate)
        proposal = factories.ProposalFactory.create(lot=lot)

        def _context_with_n_members(n: int) -> CatalogContext:
            cp = factories.CatalogPositionFactory.create()
            bucket = _bucket(db_session, catalog_position=cp)
            ctx = _context(db_session, bucket)
            for i in range(n):
                position = _position(factories, proposal, catalog_position=cp, title=f"Поз {i}")
                _member(db_session, position, ctx)
            db_session.flush()
            return ctx

        ctx_small = _context_with_n_members(2)
        with _capturing_sql(db_session) as statements_small:
            response_small = admin_client.get(f"{BASE}/contexts/{ctx_small.id}")
        assert response_small.status_code == 200
        assert len(response_small.json()["members"]) == 2
        count_small = len(statements_small)

        ctx_large = _context_with_n_members(10)
        with _capturing_sql(db_session) as statements_large:
            response_large = admin_client.get(f"{BASE}/contexts/{ctx_large.id}")
        assert response_large.status_code == 200
        assert len(response_large.json()["members"]) == 10
        count_large = len(statements_large)

        assert count_small == count_large, (count_small, count_large)

    def test_card_lists_bucket_contexts_including_archived(
        self, admin_client, db_session, factories
    ):
        """Соседи по корзине — цель слияния/переноса (спека §2.4): живой сосед
        видим и годится в цель, архивный видим, но остаётся вне выбора (это
        решает фронт по `archived_at`, бэкенд лишь называет факт). Три
        контекста ОДНОЙ корзины: под тестом (default), живой сосед с двумя
        членствами, архивный сосед без членств — `member_count` у каждого
        свой, не спутан с чужим."""
        estimate = factories.EstimateFactory.create()
        lot = factories.LotFactory.create(estimate=estimate)
        proposal = factories.ProposalFactory.create(lot=lot)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket, is_default=True)
        sibling_live = _context(db_session, bucket, is_default=False)
        sibling_archived = _context(
            db_session, bucket, is_default=False, archived_at=_now()
        )
        for i in range(2):
            position = _position(factories, proposal, catalog_position=cp, title=f"Сосед {i}")
            _member(db_session, position, sibling_live)
        db_session.flush()

        card = admin_client.get(f"{BASE}/contexts/{ctx.id}")
        assert card.status_code == 200
        by_id = {row["id"]: row for row in card.json()["bucket_contexts"]}
        assert set(by_id) == {ctx.id, sibling_live.id, sibling_archived.id}
        assert by_id[ctx.id]["is_default"] is True
        assert by_id[ctx.id]["archived_at"] is None
        assert by_id[ctx.id]["member_count"] == 0
        assert by_id[sibling_live.id]["is_default"] is False
        assert by_id[sibling_live.id]["archived_at"] is None
        assert by_id[sibling_live.id]["member_count"] == 2
        assert by_id[sibling_archived.id]["archived_at"] is not None
        assert by_id[sibling_archived.id]["member_count"] == 0
        # Порядок — по id, тот же детерминизм, что у `members`.
        ids = [row["id"] for row in card.json()["bucket_contexts"]]
        assert ids == sorted(ids)

    def test_card_bucket_contexts_query_count_independent_of_sibling_count(
        self, admin_client, db_session, factories
    ):
        """Тот же приём, что у `members`: число запросов карточки не растёт
        вместе с числом соседей по корзине — один агрегирующий запрос."""

        def _context_with_n_siblings(n: int) -> CatalogContext:
            cp = factories.CatalogPositionFactory.create()
            bucket = _bucket(db_session, catalog_position=cp)
            ctx = _context(db_session, bucket, is_default=True)
            for _ in range(n):
                _context(db_session, bucket, is_default=False)
            db_session.flush()
            return ctx

        ctx_small = _context_with_n_siblings(2)
        with _capturing_sql(db_session) as statements_small:
            response_small = admin_client.get(f"{BASE}/contexts/{ctx_small.id}")
        assert response_small.status_code == 200
        assert len(response_small.json()["bucket_contexts"]) == 3
        count_small = len(statements_small)

        ctx_large = _context_with_n_siblings(6)
        with _capturing_sql(db_session) as statements_large:
            response_large = admin_client.get(f"{BASE}/contexts/{ctx_large.id}")
        assert response_large.status_code == 200
        assert len(response_large.json()["bucket_contexts"]) == 7
        count_large = len(statements_large)

        assert count_small == count_large, (count_small, count_large)


# ---------------------------------------------------------------------------
#  Контексты — операции
# ---------------------------------------------------------------------------

class TestContextOperations:
    def test_assign_family_unit_mismatch_reports_both_units(self, admin_client, db_session, factories):
        m2 = _unit_id(db_session, "M2")
        pcs = _unit_id(db_session, "PCS")
        cp = factories.CatalogPositionFactory.create(unit_id=m2)
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        family = _family(db_session, admin_client.user, title="Другая единица", unit_name="PCS")

        response = admin_client.post(f"{BASE}/contexts/{ctx.id}/family", json={"family_id": family.id})
        assert response.status_code == 409
        body = response.json()["detail"]
        assert body["code"] == work_families.REFUSE_UNIT_MISMATCH
        assert body["family_unit_id"] == pcs
        assert body["context_unit_id"] == m2

    def test_assign_family_null_removes_family(self, admin_client, db_session, factories):
        """Снятие семьи (`family_id: null`) не было
        покрыто ни одним тестом."""
        m2 = _unit_id(db_session, "M2")
        cp = factories.CatalogPositionFactory.create(unit_id=m2)
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        family = _family(db_session, admin_client.user, title="ДляСнятия", unit_name="M2")

        assign = admin_client.post(f"{BASE}/contexts/{ctx.id}/family", json={"family_id": family.id})
        assert assign.status_code == 200
        assert assign.json()["work_family_id"] == family.id

        unassign = admin_client.post(f"{BASE}/contexts/{ctx.id}/family", json={"family_id": None})
        assert unassign.status_code == 200
        assert unassign.json()["work_family_id"] is None
        assert unassign.json()["family_source"] is None

    def test_split_context_without_rule_moves_selected_members(self, admin_client, db_session, factories):
        proposal = _proposal(factories)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket, is_default=True)
        pos_a = _position(factories, proposal, catalog_position=cp, title="A")
        pos_b = _position(factories, proposal, catalog_position=cp, title="B")
        _member(db_session, pos_a, ctx)
        _member(db_session, pos_b, ctx)

        response = admin_client.post(
            f"{BASE}/contexts/{ctx.id}/split", json={"position_item_ids": [pos_a.id]}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["moved_members"] == 1
        assert body["rule_id"] is None
        assert body["default_replaced"] is True
        assert body["new_context_id"] != ctx.id

    def test_split_context_with_malformed_rule_gives_422_uncoded(self, admin_client, db_session, factories):
        proposal = _proposal(factories)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        pos = _position(factories, proposal, catalog_position=cp)
        _member(db_session, pos, ctx)

        response = admin_client.post(
            f"{BASE}/contexts/{ctx.id}/split",
            json={"position_item_ids": [pos.id], "rule": {"kind": "not_a_real_kind", "value": "x"}},
        )
        assert response.status_code == 422
        # RoutingError не несёт кода (задача 4 не заводит для неё REFUSE_*) —
        # `detail` НЕКОДИРОВАННЫМ текстом, не объектом `{"code": ...}`.
        assert isinstance(response.json()["detail"], str)

    def test_split_context_with_valid_rule_registers_routing_rule(
        self, admin_client, db_session, factories
    ):
        """Счастливый путь разделения С правилом
        (`rule_id is not None`) не был покрыт — только `без правила` и
        `с невалидным правилом`."""
        proposal = _proposal(factories)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket, is_default=True)
        chapter = _chapter(factories, proposal, title="ОсобыйРазделПравила")
        pos = _position(factories, proposal, chapter=chapter, catalog_position=cp)
        _member(db_session, pos, ctx)

        response = admin_client.post(
            f"{BASE}/contexts/{ctx.id}/split",
            json={
                "position_item_ids": [pos.id],
                "rule": {"kind": PREDICATE_NEAREST_CHAPTER_EQUALS, "value": "ОсобыйРазделПравила"},
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["moved_members"] == 1
        assert body["rule_id"] is not None
        # Разделение С правилом не трогает умолчание корзины (спека §2.4) —
        # в отличие от разделения без правила (см. тест выше).
        assert body["default_replaced"] is False

    def test_merge_contexts_moves_members(self, admin_client, db_session, factories):
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        source = _context(db_session, bucket, is_default=False)
        target = _context(db_session, bucket, is_default=True)
        proposal = _proposal(factories)
        pos = _position(factories, proposal, catalog_position=cp)
        _member(db_session, pos, source)

        response = admin_client.post(
            f"{BASE}/contexts/{source.id}/merge", json={"target_context_id": target.id}
        )
        assert response.status_code == 200
        assert response.json()["moved_members"] == 1

    def test_archive_context_preconditions(self, admin_client, db_session, factories):
        # `db_session.commit()` после КАЖДОЙ сцены: маршрут внутри `_mutating`
        # делает `db.rollback()` на отказе, а сессия теста и роутера — ОДНА и
        # та же (`join_transaction_mode="create_savepoint"`, `tests/conftest.py`)
        # — без коммита откат размотал бы и несохранённые фикстуры теста, не
        # только попытку мутации самого маршрута.
        cp1 = factories.CatalogPositionFactory.create()
        bucket1 = _bucket(db_session, catalog_position=cp1)
        ctx_with_member = _context(db_session, bucket1, is_default=False)
        proposal = _proposal(factories)
        pos = _position(factories, proposal, catalog_position=cp1)
        _member(db_session, pos, ctx_with_member)
        db_session.commit()

        not_empty = admin_client.post(f"{BASE}/contexts/{ctx_with_member.id}/archive", json={})
        assert not_empty.status_code == 409
        not_empty_detail = not_empty.json()["detail"]
        assert not_empty_detail["code"] == context_operations.REFUSE_CONTEXT_NOT_EMPTY
        assert not_empty_detail["count"] == 1

        cp2 = factories.CatalogPositionFactory.create()
        bucket2 = _bucket(db_session, catalog_position=cp2)
        ctx_with_rule = _context(db_session, bucket2, is_default=False)
        _rule(db_session, bucket2, context=ctx_with_rule, admin=admin_client.user)
        db_session.commit()
        with_rules = admin_client.post(f"{BASE}/contexts/{ctx_with_rule.id}/archive", json={})
        assert with_rules.status_code == 409
        with_rules_detail = with_rules.json()["detail"]
        assert with_rules_detail["code"] == context_operations.REFUSE_INCOMING_RULES
        assert with_rules_detail["count"] == 1

        cp3 = factories.CatalogPositionFactory.create()
        bucket3 = _bucket(db_session, catalog_position=cp3)
        default_ctx = _context(db_session, bucket3, is_default=True)
        db_session.commit()
        without_successor = admin_client.post(f"{BASE}/contexts/{default_ctx.id}/archive", json={})
        assert without_successor.status_code == 409
        without_successor_detail = without_successor.json()["detail"]
        assert without_successor_detail["code"] == context_operations.REFUSE_DEFAULT_WITHOUT_SUCCESSOR
        # Отказ (A12.4) обязан называть корзину, а не
        # только код.
        assert without_successor_detail["bucket_id"] == bucket3.id

        successor = _context(db_session, bucket3, is_default=False)
        with_successor = admin_client.post(
            f"{BASE}/contexts/{default_ctx.id}/archive",
            json={"new_default_context_id": successor.id},
        )
        assert with_successor.status_code == 200
        assert with_successor.json()["archived"] is True


# ---------------------------------------------------------------------------
#  Членства
# ---------------------------------------------------------------------------

class TestMembers:
    def test_move_members_invalid_reason_gives_422(self, admin_client, db_session, factories):
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        target = _context(db_session, bucket)
        proposal = _proposal(factories)
        pos = _position(factories, proposal, catalog_position=cp)
        _member(db_session, pos, target)

        response = admin_client.post(
            f"{BASE}/members/move",
            json={"position_item_ids": [pos.id], "target_context_id": target.id, "reason": "не по спеку"},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == context_operations.REFUSE_INVALID_REASON

    def test_move_members_happy_path(self, admin_client, db_session, factories):
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        source = _context(db_session, bucket, is_default=True)
        target = _context(db_session, bucket, is_default=False)
        proposal = _proposal(factories)
        pos = _position(factories, proposal, catalog_position=cp)
        _member(db_session, pos, source)

        response = admin_client.post(
            f"{BASE}/members/move",
            json={"position_item_ids": [pos.id], "target_context_id": target.id, "reason": "manual"},
        )
        assert response.status_code == 200
        assert response.json()["moved_members"] == 1

    def test_move_members_different_bucket_names_bucket_ids(self, admin_client, db_session, factories):
        """`REFUSE_DIFFERENT_BUCKET` через API (A12.4)
        не вызывался вовсе — членство и цель в РАЗНЫХ корзинах, отказ обязан
        назвать обе."""
        cp_a = factories.CatalogPositionFactory.create()
        cp_b = factories.CatalogPositionFactory.create()
        bucket_a = _bucket(db_session, catalog_position=cp_a)
        bucket_b = _bucket(db_session, catalog_position=cp_b)
        source = _context(db_session, bucket_a)
        target = _context(db_session, bucket_b)
        proposal = _proposal(factories)
        pos = _position(factories, proposal, catalog_position=cp_a)
        _member(db_session, pos, source)

        response = admin_client.post(
            f"{BASE}/members/move",
            json={"position_item_ids": [pos.id], "target_context_id": target.id, "reason": "manual"},
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == context_operations.REFUSE_DIFFERENT_BUCKET
        assert set(detail["bucket_ids"]) == {bucket_a.id, bucket_b.id}

    def test_transfer_proposal_missing_membership_gives_domain_error(self, admin_client):
        response = admin_client.get(f"{BASE}/members/999999999/transfer-proposal")
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == context_operations.REFUSE_INVALID_MEMBERSHIP

    def test_transfer_proposal_current_membership_is_not_an_error(self, admin_client, db_session, factories):
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        proposal = _proposal(factories)
        pos = _position(factories, proposal, catalog_position=cp)
        _member(db_session, pos, ctx, membership_state=MembershipState.CURRENT.value)

        response = admin_client.get(f"{BASE}/members/{pos.id}/transfer-proposal")
        assert response.status_code == 200
        assert response.json()["proposal"] is None

    def test_accept_target_decision_not_conflicted_reports_ids_and_count(self, admin_client, db_session, factories):
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        proposal = _proposal(factories)
        pos = _position(factories, proposal, catalog_position=cp)
        _member(db_session, pos, ctx)  # без конфликта
        # Коммит ДО отказывающего вызова — см. докстринг-комментарий
        # `test_archive_context_preconditions`: без него `db.rollback()`
        # внутри `_mutating` размотал бы и эту, ещё не сохранённую, фикстуру.
        db_session.commit()

        response = admin_client.post(
            f"{BASE}/members/accept-target-decision", json={"position_item_ids": [pos.id]}
        )
        assert response.status_code == 409
        body = response.json()["detail"]
        assert body["code"] == context_operations.REFUSE_NOT_CONFLICTED
        assert body["count"] == 1
        assert body["position_item_ids"] == [pos.id]

    def test_accept_target_decision_happy_path(self, admin_client, db_session, factories):
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        other = _context(db_session, bucket, is_default=False)
        proposal = _proposal(factories)
        pos = _position(factories, proposal, catalog_position=cp)
        _member(db_session, pos, ctx, conflict_at=_now(), conflict_from_context_id=other.id)

        response = admin_client.post(
            f"{BASE}/members/accept-target-decision", json={"position_item_ids": [pos.id]}
        )
        assert response.status_code == 200
        assert response.json()["updated_members"] == 1

    def _stale_transfer_scene(self, db_session, factories):
        """STALE-членство с ДВУМЯ существующими корзинами — исходной
        (статья `cat_b`, устарела) и целевой по СВЕЖЕЙ эффективной статье
        (`cat_a`, из раздела)."""
        cat_a, cat_b = _leaf_category_ids(db_session, 2)
        cp = factories.CatalogPositionFactory.create()
        proposal = _proposal(factories)
        chapter = _chapter(factories, proposal, title="СтеныПереноса", category_id=cat_a, category_source="file")
        bucket_b = _bucket(db_session, catalog_position=cp, work_category_id=cat_b)
        ctx_b = _context(db_session, bucket_b)
        pos = _position(factories, proposal, chapter=chapter, catalog_position=cp)
        _member(db_session, pos, ctx_b, membership_state=MembershipState.STALE.value)
        db_session.commit()
        return pos, cat_a, cat_b

    def test_accept_transfer_category_changed_carries_json_safe_new_proposal(
        self, admin_client, db_session, factories
    ):
        """`_domain_error` клала в контекст
        `vars(exc)` БЕЗ преобразования — `new_proposal` (dataclass
        `TransferProposal`) не сериализуется штатным JSON-кодером, и маршрут
        падал `500` (`TypeError: Object of type TransferProposal
        is not JSON serializable`). `expected_category_id=cat_b` — то, что
        показало БЫ старое (устаревшее) предложение, а не свежее."""
        pos, cat_a, cat_b = self._stale_transfer_scene(db_session, factories)

        response = admin_client.post(
            f"{BASE}/members/{pos.id}/transfer", json={"expected_category_id": cat_b}
        )
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == context_operations.REFUSE_CATEGORY_CHANGED
        assert detail["position_item_id"] == pos.id
        new_proposal = detail["new_proposal"]
        assert new_proposal["position_item_id"] == pos.id
        assert new_proposal["effective_category_id"] == cat_a

    def test_accept_transfer_happy_path(self, admin_client, db_session, factories):
        pos, cat_a, _cat_b = self._stale_transfer_scene(db_session, factories)

        response = admin_client.post(
            f"{BASE}/members/{pos.id}/transfer", json={"expected_category_id": cat_a}
        )
        assert response.status_code == 200
        assert response.json()["moved_members"] == 1

    def test_domain_error_rolls_back_partial_writes(
        self, committing_client, committing_session_factory, monkeypatch
    ):
        """В транзакционной фикстуре сервис отказывает
        ДО любой записи, и `commit` вместо `rollback` в `_mutating` ничем не
        отличим от правильного отката. Здесь —
        `committing_client` (настоящий `commit`/`rollback` на реальном
        Postgres) и сервис, подменённый на «сначала запись, потом отказ»:
        если `_mutating` коммитит вместо отката, вставленная семья
        переживёт ответ `4xx` и найдётся в ОТДЕЛЬНОЙ сессии."""
        probe_title = f"RollbackProbe-{uuid.uuid4().hex[:8]}"

        def _fake_create_family_then_fail(db, *, title, unit_name, definition, actor_id):
            # `created_by=None` + `seed_key=probe_title` — нарочно: подмена
            # не должна зависеть от того, существует ли пользователь
            # `actor_id` в НАСТОЯЩЕЙ базе (`committing_client` подставляет
            # `MagicMock(id=1)`, реальной строки `users` с этим id может не
            # быть), а `CK_FAMILY_AUTHOR_IFF_NOT_SEED` требует РОВНО одного
            # из `created_by`/`seed_key` — `seed_key` его и держит.
            family = WorkFamily(
                seed_key=probe_title, title=title, unit_id=None, definition=None,
                status=FamilyStatus.draft.value, created_by=None,
            )
            db.add(family)
            db.flush()
            raise work_families.WorkFamilyError(
                work_families.REFUSE_UNKNOWN_UNIT,
                "probe: injected failure for rollback proof",
                unit_name="probe",
            )

        monkeypatch.setattr(work_families, "create_family", _fake_create_family_then_fail)

        response = committing_client.post(
            f"{BASE}/families", json={"title": probe_title, "unit_name": None, "definition": None}
        )
        assert response.status_code == 422

        probe_session = committing_session_factory()
        try:
            found = probe_session.execute(
                sa.select(WorkFamily.id).where(WorkFamily.title == probe_title)
            ).scalar_one_or_none()
        finally:
            probe_session.close()
        assert found is None, "запись пережила отказ 4xx — commit вместо rollback"
