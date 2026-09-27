"""API семантического контура — двадцать один маршрут `/api/v1/semantic`
(спека `2026-09-22-catalog-families-design.md` §2.10; план, задача 12; чтение
членств группы — спека `2026-09-25-families-screen-design.md` §2.8 п. 3;
пакетный перенос устаревшей группы — та же спека §2.6, §2.8 п. 4).

Сервисы задач 4, 6-10 (`services/context_routing.py`,
`services/context_operations.py`, `services/work_families.py`) сами не
тестируются здесь повторно — только HTTP-слой: права, форма запроса/ответа,
трансляция отказов, фильтры очереди контекстов и ограниченное число запросов
`list_contexts`.

Помощники (`_proposal`, `_chapter`, `_position`, `_leaf_category_ids`,
`_bucket`, `_context`, `_member`, `_rule`, `_family`, `_bulk_members`) —
ЛОКАЛЬНАЯ копия (докстрока `test_context_operations.py`: наборы помощников
тестов друг у друга не импортируют).
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
    PositionItem,
    RoutedBy,
    SemanticEvent,
    SemanticKind,
    SemanticState,
    WorkCategory,
    WorkFamily,
)
from services import context_operations
from services.context_routing import (
    PREDICATE_NEAREST_CHAPTER_EQUALS,
    RoutingError,
    chapter_context,
    chapter_paths,
)
from services.unit_resolution import UnitResolver

pytestmark = pytest.mark.integration

BASE = "/api/v1/semantic"

# ---------------------------------------------------------------------------
#  Литерал двадцати одного маршрута плана (Task 12, Interfaces; + два
#  маршрута чтения членств группы, спека `2026-09-25-families-screen-design.md`
#  §2.8 п. 3; + пакетный перенос устаревшей группы, та же спека §2.8 п. 4) —
#  НЕЗАВИСИМЫЙ от `app.routes`: перебор прав обязан ловить забытый
#  `require_admin` на ОДНОМ маршруте, а не читать список из того же дерева,
#  которое проверяет.
# ---------------------------------------------------------------------------
TWENTY_ONE_ROUTES: tuple[tuple[str, str], ...] = (
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
    ("GET", "/api/v1/semantic/contexts/1/members"),
    ("GET", "/api/v1/semantic/contexts/1/member-ids"),
    ("POST", "/api/v1/semantic/contexts/1/stale-groups/transfer"),
    ("POST", "/api/v1/semantic/members/move"),
    ("GET", "/api/v1/semantic/members/1/transfer-proposal"),
    ("POST", "/api/v1/semantic/members/1/transfer"),
    ("POST", "/api/v1/semantic/members/accept-target-decision"),
)
assert len(TWENTY_ONE_ROUTES) == 21


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _proposal(factories):
    estimate = factories.EstimateFactory.create()
    lot = factories.LotFactory.create(estimate=estimate)
    return factories.ProposalFactory.create(lot=lot)


def _chapter(
    factories, proposal, *, title="Раздел", category_id=None, category_source="file", parent=None
):
    """Строка-раздел. `parent` — родительский раздел (`chapter_item_id`) для
    построения цепочки глубже одного уровня (тесты `chapter_paths` и групп
    членств, §2.8 п. 2) — те же три составных ограничения, что у обычной
    позиции с разделом: `proposal_id`/`chapter_item_id` внутри одной сметы."""
    kwargs = dict(
        proposal=proposal, is_chapter=True, job_title_in_proposal=title,
        chapter_number_in_proposal="1",
    )
    if category_id is not None:
        kwargs["work_category_id"] = category_id
        kwargs["category_source"] = category_source
    if parent is not None:
        kwargs["chapter_item_id"] = parent.id
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


def _category_with_dot_count(session, dots: int) -> WorkCategory:
    """Первая статья классификатора с РОВНО `dots` точками в коде (глубина
    `dots + 1`) — найдена по СТРУКТУРЕ кода, а не хардкодом id/литерала:
    утверждается СВОЙСТВО глубины (спека §2.8 п. 1), а не конкретный код,
    который совпал бы случайно. Падает явно, если в засеянном справочнике
    такой глубины нет."""
    rows = session.execute(sa.select(WorkCategory)).scalars().all()
    for row in rows:
        if row.code.count(".") == dots:
            return row
    raise AssertionError(f"no work_category with {dots} dots in code found in seeded reference data")


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


def _bulk_members(db, factories, *, context, count: int) -> list[int]:
    """`count` строк-позиций (без раздела) и членств контекста ОДНИМ
    `executemany` на таблицу (`sa.insert`, core, не ORM-фабрика на штуку) —
    группа из 520 нужна нескольким тестам эндпоинтов группы (спека §2.8 п. 3),
    и `factories.PositionItemFactory.create` в цикле флашит на КАЖДУЮ строку,
    заметно медленнее core-вставки. Раздела у позиций нет — эти тесты берут
    группу «весь контекст» (`GroupSelector(None, False)`), а не конкретный
    раздел. Возвращает id новых позиций (порядок вставки — по возрастанию,
    БД сама назначает id по возрастающей последовательности)."""
    proposal = _proposal(factories)
    db.execute(
        sa.insert(PositionItem),
        [
            {
                "proposal_id": proposal.id,
                "position_key_in_proposal": f"bulk_{i}",
                "job_title_in_proposal": f"Массовая позиция {i}",
                "is_chapter": False,
            }
            for i in range(count)
        ],
    )
    position_ids = list(
        db.execute(
            sa.select(PositionItem.id)
            .where(PositionItem.proposal_id == proposal.id)
            .order_by(PositionItem.id)
        )
        .scalars()
        .all()
    )
    assert len(position_ids) == count
    db.execute(
        sa.insert(ContextMember),
        [
            {
                "position_item_id": pid,
                "context_id": context.id,
                "bucket_id": context.bucket_id,
                "membership_state": MembershipState.CURRENT.value,
                "routed_by": RoutedBy.default.value,
            }
            for pid in position_ids
        ],
    )
    db.flush()
    return position_ids


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


#: Число SQL-запросов `GET /contexts/{id}` (всё, что `_capturing_sql` видит за
#: время запроса) на контексте с членствами под одним разделом, после удаления
#: членств поштучно из карточки (спека `2026-09-25-families-screen-design.md`
#: §2.8 п. 5). Замер по `statements_small` — восемь: загрузка корзины и строки
#: каталога (сам контекст уже лежит в identity map сессии), представительное
#: членство, счётчик членств, сводки групп членств (`GROUP BY` раздела), пути
#: групп одним рекурсивным CTE, соседи по корзине, журнал. Статьи у корзины
#: этого теста нет (`_bucket` без `work_category_id`), поэтому ни загрузки
#: статьи, ни запроса её пути классификатора (`_work_category_path`) в числе
#: нет — у корзины со статьёй их до двух сверх восьми, и тоже константой. Держит утверждение «число запросов карточки не растёт ни с числом членств,
#: ни с числом уникальных путей, ни с их глубиной» (спека §2.8) в абсолютной
#: форме — см. `test_card_members_query_count_independent_of_member_count`.
_CARD_QUERY_COUNT = 8


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


#: Те же двадцать один маршрут, но ШАБЛОНАМИ пути — так их несёт
#: `app.routes` (`{family_id}`, а не подставленный `1` из TWENTY_ONE_ROUTES,
#: который существует ради HTTP-вызовов теста прав).
TWENTY_ONE_ROUTE_TEMPLATES: frozenset[tuple[str, str]] = frozenset(
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
        ("GET", f"{BASE}/contexts/{{context_id}}/members"),
        ("GET", f"{BASE}/contexts/{{context_id}}/member-ids"),
        ("POST", f"{BASE}/contexts/{{context_id}}/stale-groups/transfer"),
        ("POST", f"{BASE}/members/move"),
        ("GET", f"{BASE}/members/{{position_item_id}}/transfer-proposal"),
        ("POST", f"{BASE}/members/{{position_item_id}}/transfer"),
        ("POST", f"{BASE}/members/accept-target-decision"),
    }
)
assert len(TWENTY_ONE_ROUTE_TEMPLATES) == 21


def test_route_set_under_prefix_equals_twenty_one_literal():
    """Множество путей под `/api/v1/semantic`, собранное из `app.routes`,
    равно литералу двадцати одного (план, задача 12, «Утверждения»; два
    маршрута членств группы и пакетный перенос устаревшей группы — спека
    `2026-09-25-families-screen-design.md` §2.8 п. 3-4) — единственное место,
    где такое утверждение осмысленно (задача 6 роутера ещё не заводила);
    маршрута восстановления архивного контекста (`…/restore`) в нём нет."""
    collected = _collect_semantic_routes()
    assert collected == TWENTY_ONE_ROUTE_TEMPLATES
    assert not any(path.endswith("/restore") for _method, path in collected)


# ---------------------------------------------------------------------------
#  Права: КАЖДЫЙ из двадцати одного маршрута
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method,path", TWENTY_ONE_ROUTES)
def test_member_rejected_on_every_route(method, path, member_client):
    response = member_client.request(method, path)
    assert response.status_code == 403, f"{method} {path} -> {response.status_code}"


@pytest.mark.parametrize("method,path", TWENTY_ONE_ROUTES)
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

    def test_create_with_whitespace_only_title_gives_422_not_500(self, admin_client):
        """`min_length=1` на `CreateFamilyRequest.title` пропускает
        пробельную строку — сервис обязан отказать доменным кодом до
        `IntegrityError` от `ck_work_families_title_not_blank`."""
        response = admin_client.post(
            f"{BASE}/families", json={"title": "   ", "unit_name": None, "definition": None}
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == work_families.REFUSE_BLANK_TITLE

    def test_update_with_whitespace_only_title_gives_422_not_500(self, admin_client):
        created = admin_client.post(
            f"{BASE}/families", json={"title": "Имя остаётся", "unit_name": None, "definition": None}
        )
        family_id = created.json()["id"]

        response = admin_client.patch(f"{BASE}/families/{family_id}", json={"title": "   "})
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == work_families.REFUSE_BLANK_TITLE

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
        # Ответ — строка ЦЕЛЕВОЙ семьи (форма списка), не сводка `moved_contexts`
        # (последняя проверяется через её `context_count`, задача формы ответа).
        assert response.json()["id"] == target.id
        assert response.json()["context_count"] == 1

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

    def test_activate_duplicate_active_name_and_unit_gives_409_not_500(
        self, admin_client, db_session
    ):
        """Черновики могут делить нормализованные имя и единицу — второй
        такой черновик обычный, достижимый вход. Активировать его при уже
        активном первом — доменный отказ, а не необработанная ошибка базы."""
        title = "Штукатурка стен API-дубль"
        first = work_families.create_family(
            db_session, title=title, unit_name="M2", definition="Определение А",
            actor_id=admin_client.user.id,
        )
        second = work_families.create_family(
            db_session, title=title, unit_name="M2", definition="Определение Б",
            actor_id=admin_client.user.id,
        )
        db_session.commit()

        activated_first = admin_client.post(f"{BASE}/families/{first.id}/activate")
        assert activated_first.status_code == 200

        activated_second = admin_client.post(f"{BASE}/families/{second.id}/activate")
        assert activated_second.status_code == 409
        detail = activated_second.json()["detail"]
        assert detail["code"] == work_families.REFUSE_DUPLICATE_ACTIVE_FAMILY
        assert detail["duplicate_family_id"] == first.id

    def test_patch_rename_to_duplicate_active_name_and_unit_gives_409_not_500(
        self, admin_client, db_session
    ):
        """Переименование активной семьи в нормализованное имя другой
        активной семьи с той же единицей — обычный, достижимый вход через
        ОДИН и тот же PATCH-маршрут: доменный отказ, а не необработанная
        ошибка базы."""
        first = _family(db_session, admin_client.user, title="Штукатурка стен-АПИ", unit_name="M2")
        second = _family(db_session, admin_client.user, title="Шпаклёвка стен-АПИ", unit_name="M2")
        db_session.commit()

        response = admin_client.patch(f"{BASE}/families/{second.id}", json={"title": first.title})
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == work_families.REFUSE_DUPLICATE_ACTIVE_FAMILY
        assert detail["duplicate_family_id"] == first.id

        db_session.expire_all()
        untouched = db_session.get(WorkFamily, second.id)
        assert untouched.title == "Шпаклёвка стен-АПИ"

    def test_patch_unit_change_to_duplicate_active_name_and_unit_gives_409_not_500(
        self, admin_client, db_session
    ):
        """Тот же класс отказа, другой запускающий путь ОДНОГО и того же
        PATCH-маршрута — `unit_name` доходит до `set_unit`, не до
        `update_family`."""
        same_title = "Гидроизоляция кровли-АПИ"
        first = _family(db_session, admin_client.user, title=same_title, unit_name="M2")
        second = _family(db_session, admin_client.user, title=same_title, unit_name="PCS")
        db_session.commit()

        response = admin_client.patch(f"{BASE}/families/{second.id}", json={"unit_name": "M2"})
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == work_families.REFUSE_DUPLICATE_ACTIVE_FAMILY
        assert detail["duplicate_family_id"] == first.id

        db_session.expire_all()
        untouched = db_session.get(WorkFamily, second.id)
        assert untouched.unit_id == _unit_id(db_session, "PCS")

    def test_patch_explicit_null_definition_clears_draft_family(self, admin_client, db_session):
        fam = _family(
            db_session, admin_client.user, title="Черновик с определением",
            unit_name=None, definition="Было", active=False,
        )
        db_session.commit()

        response = admin_client.patch(
            f"{BASE}/families/{fam.id}", json={"definition": None}
        )
        assert response.status_code == 200
        assert response.json()["definition"] is None

    def test_patch_omitted_definition_leaves_it_untouched(self, admin_client, db_session):
        fam = _family(
            db_session, admin_client.user, title="Черновик, поле не передано",
            unit_name=None, definition="Остаётся", active=False,
        )
        db_session.commit()

        response = admin_client.patch(f"{BASE}/families/{fam.id}", json={"title": "Переименовано"})
        assert response.status_code == 200
        assert response.json()["definition"] == "Остаётся"

    def test_patch_explicit_null_definition_on_active_family_is_domain_refusal(
        self, admin_client, db_session
    ):
        fam = _family(
            db_session, admin_client.user, title="Активная с определением",
            unit_name=None, definition="Было", active=True,
        )
        db_session.commit()

        response = admin_client.patch(
            f"{BASE}/families/{fam.id}", json={"definition": None}
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == work_families.REFUSE_CLEAR_DEFINITION_ACTIVE

        db_session.expire_all()
        untouched = db_session.get(WorkFamily, fam.id)
        assert untouched.definition == "Было"

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

    def test_mutation_responses_match_list_row_shape(self, admin_client, db_session, factories):
        """Ответ КАЖДОЙ мутации, отдающей семью (`create`/`update`/`activate`/
        `archive`/`merge`), несёт РОВНО те же ключи, что строка списка
        (`GET /families`) — независимый литерал множества ключей `WorkFamily`
        (`frontend/src/types/domain.ts`), а не подмножество/надмножество.
        `unit_code` и `context_count` при этом верны на семье с двумя
        привязанными контекстами (`update`, затем цель `merge`)."""
        family_row_keys = frozenset({
            "id", "title", "unit_id", "unit_code", "definition", "status",
            "seed_key", "created_by", "created_at", "updated_at",
            "activated_by", "activated_at", "archived_at", "context_count",
        })
        m2 = "M2"

        created = admin_client.post(
            f"{BASE}/families",
            json={"title": "Форма ответа мутации", "unit_name": m2, "definition": "Определение"},
        )
        assert created.status_code == 201
        assert set(created.json().keys()) == family_row_keys
        family_id = created.json()["id"]

        activated = admin_client.post(f"{BASE}/families/{family_id}/activate")
        assert activated.status_code == 200
        assert set(activated.json().keys()) == family_row_keys

        unit_id = _unit_id(db_session, m2)
        for _ in range(2):
            cp = factories.CatalogPositionFactory.create(unit_id=unit_id)
            bucket = _bucket(db_session, catalog_position=cp)
            ctx = _context(db_session, bucket)
            ctx.work_family_id = family_id
            ctx.family_source = "manual"
            ctx.family_by = admin_client.user.id
            ctx.family_at = _now()
        db_session.flush()

        updated = admin_client.patch(f"{BASE}/families/{family_id}", json={"title": "Переименовано в мутации"})
        assert updated.status_code == 200
        assert set(updated.json().keys()) == family_row_keys
        assert updated.json()["unit_code"] == m2
        assert updated.json()["context_count"] == 2

        target = _family(db_session, admin_client.user, title="Цель слияния формы", unit_name=m2)
        merged = admin_client.post(
            f"{BASE}/families/{family_id}/merge", json={"target_family_id": target.id}
        )
        assert merged.status_code == 200
        assert set(merged.json().keys()) == family_row_keys
        assert merged.json()["unit_code"] == m2
        assert merged.json()["context_count"] == 2

        empty = admin_client.post(
            f"{BASE}/families", json={"title": "Для архивации формы", "unit_name": None, "definition": None}
        )
        empty_id = empty.json()["id"]
        archived = admin_client.post(f"{BASE}/families/{empty_id}/archive")
        assert archived.status_code == 200
        assert set(archived.json().keys()) == family_row_keys


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

    def test_list_contexts_reports_member_count_and_stale_flag(self, admin_client, db_session, factories):
        """Спека §2.8 п. 1: контекст с тремя членствами, одно из
        них STALE — `member_count = 3`, `has_stale_members = true`,
        `has_conflicting_members = false` (конфликта в сцене нет вовсе)."""
        cp = factories.CatalogPositionFactory.create(standard_job_title="Три членства с устаревшим")
        proposal = _proposal(factories)
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        for i in range(2):
            pos = _position(factories, proposal, catalog_position=cp, title=f"Позиция {i}")
            _member(db_session, pos, ctx)
        pos_stale = _position(factories, proposal, catalog_position=cp, title="Позиция устаревшая")
        _member(db_session, pos_stale, ctx, membership_state=MembershipState.STALE.value)

        response = admin_client.get(f"{BASE}/contexts", params={"catalog_query": "Три членства с устаревшим"})
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        item = items[0]
        assert item["member_count"] == 3
        assert item["has_stale_members"] is True
        assert item["has_conflicting_members"] is False

    def test_list_contexts_reports_conflicting_members_flag(self, admin_client, db_session, factories):
        """Спека §2.8 п. 1: членство с непустым `conflict_at` —
        `has_conflicting_members = true`."""
        cp_other = factories.CatalogPositionFactory.create(standard_job_title="Источник конфликта очереди")
        bucket_other = _bucket(db_session, catalog_position=cp_other)
        ctx_other = _context(db_session, bucket_other)

        cp = factories.CatalogPositionFactory.create(standard_job_title="Конфликтное членство очереди")
        proposal = _proposal(factories)
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        pos = _position(factories, proposal, catalog_position=cp)
        _member(db_session, pos, ctx, conflict_at=_now(), conflict_from_context_id=ctx_other.id)

        response = admin_client.get(f"{BASE}/contexts", params={"catalog_query": "Конфликтное членство очереди"})
        item = response.json()["items"][0]
        assert item["has_conflicting_members"] is True

    def test_list_contexts_reports_zero_member_count_and_false_flags_for_empty_context(
        self, admin_client, db_session, factories
    ):
        """Спека §2.8 п. 1: пустой контекст — `member_count = 0`,
        оба признака `false`.

        Рядом — СОСЕДНИЙ контекст (вне выдачи по `catalog_query`) с
        членством, одновременно `STALE` и конфликтным: без него в базе теста
        нет ни одного членства, и число с признаками, посчитанные БЕЗ
        корреляции с контекстом строки (по всей таблице членств), дали бы те
        же `0`/`false` — тест не отличил бы «у этого контекста нет членств»
        от «членств нет нигде»."""
        cp_neighbour = factories.CatalogPositionFactory.create(standard_job_title="Соседний непустой контекст")
        proposal = _proposal(factories)
        bucket_neighbour = _bucket(db_session, catalog_position=cp_neighbour)
        ctx_neighbour = _context(db_session, bucket_neighbour)
        cp_source = factories.CatalogPositionFactory.create(standard_job_title="Источник конфликта соседа")
        ctx_source = _context(db_session, _bucket(db_session, catalog_position=cp_source))
        pos_neighbour = _position(factories, proposal, catalog_position=cp_neighbour)
        _member(
            db_session, pos_neighbour, ctx_neighbour,
            membership_state=MembershipState.STALE.value,
            conflict_at=_now(), conflict_from_context_id=ctx_source.id,
        )

        cp = factories.CatalogPositionFactory.create(standard_job_title="Пустой контекст очереди")
        bucket = _bucket(db_session, catalog_position=cp)
        _context(db_session, bucket)

        response = admin_client.get(f"{BASE}/contexts", params={"catalog_query": "Пустой контекст очереди"})
        item = response.json()["items"][0]
        assert item["member_count"] == 0
        assert item["has_stale_members"] is False
        assert item["has_conflicting_members"] is False

    def test_list_contexts_reports_work_category_path_third_level(self, admin_client, db_session, factories):
        """Спека §2.8 п. 1: статья третьего уровня (как `8.2.3`) —
        `work_category_path` из ДВУХ элементов, прародитель затем родитель
        (код и название каждого), а не сама статья."""
        category = _category_with_dot_count(db_session, 2)
        parent = db_session.get(WorkCategory, category.parent_id)
        assert parent is not None
        grandparent = db_session.get(WorkCategory, parent.parent_id)
        assert grandparent is not None

        cp = factories.CatalogPositionFactory.create(standard_job_title="Путь классификатора третий уровень")
        bucket = _bucket(db_session, catalog_position=cp, work_category_id=category.id)
        _context(db_session, bucket)

        response = admin_client.get(
            f"{BASE}/contexts", params={"catalog_query": "Путь классификатора третий уровень"}
        )
        item = response.json()["items"][0]
        assert item["work_category_path"] == [
            {"code": grandparent.code, "title": grandparent.title},
            {"code": parent.code, "title": parent.title},
        ]

    def test_list_contexts_reports_work_category_path_second_level(self, admin_client, db_session, factories):
        """Спека §2.8 п. 1 (родители статьи, от корня, без неё
        самой): статья ВТОРОГО уровня — `work_category_path` из ОДНОГО
        элемента, её родителя. Это состояние, которое вытесняют оба случая
        плана: прародителя нет, родитель есть, — и путь, собираемый только при
        обоих предках (или ставящий родителя лишь вслед за прародителем),
        проходит третий и первый уровни, а здесь теряет родителя."""
        category = _category_with_dot_count(db_session, 1)
        parent = db_session.get(WorkCategory, category.parent_id)
        assert parent is not None
        assert parent.parent_id is None

        cp = factories.CatalogPositionFactory.create(standard_job_title="Путь классификатора второй уровень")
        bucket = _bucket(db_session, catalog_position=cp, work_category_id=category.id)
        _context(db_session, bucket)

        response = admin_client.get(
            f"{BASE}/contexts", params={"catalog_query": "Путь классификатора второй уровень"}
        )
        item = response.json()["items"][0]
        assert item["work_category_path"] == [{"code": parent.code, "title": parent.title}]

    def test_list_contexts_reports_empty_work_category_path_for_first_level_category(
        self, admin_client, db_session, factories
    ):
        """Спека §2.8 п. 1: статья первого уровня —
        `work_category_path` пуст."""
        category = _category_with_dot_count(db_session, 0)
        assert category.parent_id is None

        cp = factories.CatalogPositionFactory.create(standard_job_title="Путь классификатора первый уровень")
        bucket = _bucket(db_session, catalog_position=cp, work_category_id=category.id)
        _context(db_session, bucket)

        response = admin_client.get(
            f"{BASE}/contexts", params={"catalog_query": "Путь классификатора первый уровень"}
        )
        item = response.json()["items"][0]
        assert item["work_category_path"] == []

    def test_list_contexts_reports_empty_work_category_path_without_category(
        self, admin_client, db_session, factories
    ):
        """Спека §2.8 п. 1: корзина без статьи
        (`work_category_id IS NULL`) — `work_category_path` пуст."""
        cp = factories.CatalogPositionFactory.create(standard_job_title="Корзина без статьи очереди")
        bucket = _bucket(db_session, catalog_position=cp)
        _context(db_session, bucket)

        response = admin_client.get(f"{BASE}/contexts", params={"catalog_query": "Корзина без статьи очереди"})
        item = response.json()["items"][0]
        assert item["work_category_path"] == []

    def test_work_categories_seed_depth_is_at_most_three(self, db_session):
        """Спека §2.8 п. 1: максимальная глубина `work_categories`
        в засеянном справочнике `<= 3` — путь строится ДВУМЯ соединениями
        (родитель + прародитель), и это ровно тот предел, который они
        покрывают. Поднимется глубина классификатора — этот тест краснеет
        первым и называет найденную глубину, а не список молча обрежет путь."""
        max_depth = db_session.execute(
            sa.text(
                """
                WITH RECURSIVE depth_cte(id, depth) AS (
                    SELECT id, 1 FROM work_categories WHERE parent_id IS NULL
                    UNION ALL
                    SELECT wc.id, d.depth + 1
                    FROM work_categories wc
                    JOIN depth_cte d ON wc.parent_id = d.id
                )
                SELECT MAX(depth) FROM depth_cte
                """
            )
        ).scalar_one()
        assert max_depth <= 3, f"work_categories depth is {max_depth}, path building covers only 3 levels"

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

    def test_list_contexts_issues_exactly_two_statements(self, admin_client, db_session, factories):
        """Спека §2.8 п. 1: `list_contexts` — РОВНО два запроса,
        счётчик и страница (спека §2.8 п. 1). Соседний тест выше доказывает
        только независимость от числа строк: постоянный ТРЕТИЙ запрос (например,
        разовое чтение справочника `work_categories` ради пути
        классификатора — именно то, что спека запрещает) он пропускает, потому
        что третий запрос одинаков на 2 и на 10 строках."""
        category = _category_with_dot_count(db_session, 2)
        cp = factories.CatalogPositionFactory.create(standard_job_title="Ровно два запроса очереди")
        bucket = _bucket(db_session, catalog_position=cp, work_category_id=category.id)
        _context(db_session, bucket)
        db_session.flush()

        with _capturing_sql(db_session) as statements:
            response = admin_client.get(f"{BASE}/contexts", params={"catalog_query": "Ровно два запроса очереди"})
        assert response.status_code == 200
        assert len(response.json()["items"]) == 1
        assert len(statements) == 2, statements


# ---------------------------------------------------------------------------
#  Пути членств (`chapter_paths`, спека §2.8 п. 2)
# ---------------------------------------------------------------------------

class TestChapterPaths:
    def test_depth_four_path_matches_reversed_chapter_context_chain(
        self, db_session, factories
    ):
        """Путь СВЕРХУ ВНИЗ для раздела глубины 4 — четыре названия, и
        совпадает с `tuple(reversed(chapter_context(db, позиция).chain))`
        для позиции этого раздела (спека §2.8 п. 2)."""
        proposal = _proposal(factories)
        level1 = _chapter(factories, proposal, title="Раздел уровня 1")
        level2 = _chapter(factories, proposal, title="Раздел уровня 2", parent=level1)
        level3 = _chapter(factories, proposal, title="Раздел уровня 3", parent=level2)
        level4 = _chapter(factories, proposal, title="Раздел уровня 4", parent=level3)
        position = _position(factories, proposal, chapter=level4)
        db_session.flush()

        paths = chapter_paths(db_session, [level4.id])
        assert len(paths) == 1
        assert paths[level4.id] == (
            "Раздел уровня 1", "Раздел уровня 2", "Раздел уровня 3", "Раздел уровня 4",
        )
        assert paths[level4.id] == tuple(
            reversed(chapter_context(db_session, position.id).chain)
        )

    def test_repeated_calls_beyond_prepare_threshold(self, db_session, factories):
        """`docs/insights/batch-larger-than-five.md`: порог psycopg3
        `prepare_threshold = 5` считает ИСПОЛНЕНИЯ одного и того же запроса на
        соединении, а не число id в одном вызове — один вызов с 12 id порог
        не переходит. Поэтому здесь ВОСЕМЬ вызовов подряд на одном
        соединении с РАЗНЫМИ наборами разделов (от 1 до 12 id): начиная с
        шестого запрос исполняется подготовленным планом, и путь каждого
        раздела в каждом вызове обязан совпасть с независимым эталоном
        `chapter_context()`. Что порог реально пройден, тест проверяет сам —
        по `pg_prepared_statements` этого соединения, а не по допущению."""
        proposal = _proposal(factories)
        leaves = []
        for i in range(12):
            root = _chapter(factories, proposal, title=f"Ветка {i}")
            leaf = _chapter(factories, proposal, title=f"Лист {i}", parent=root)
            position = _position(factories, proposal, chapter=leaf, title=f"Поз {i}")
            leaves.append((leaf, position))
        db_session.flush()
        expected = {
            leaf.id: tuple(reversed(chapter_context(db_session, position.id).chain))
            for leaf, position in leaves
        }

        for size in (1, 2, 3, 5, 7, 9, 11, 12):
            ids = [leaf.id for leaf, _ in leaves[:size]]
            paths = chapter_paths(db_session, ids)
            assert paths == {chapter_id: expected[chapter_id] for chapter_id in ids}, size

        prepared = db_session.execute(
            sa.text(
                "SELECT count(*) FROM pg_prepared_statements "
                "WHERE statement LIKE '%WITH RECURSIVE chain%'"
            )
        ).scalar_one()
        assert prepared >= 1, "запрос chapter_paths так и не был подготовлен — порог не пройден"

    def test_cycle_not_through_start_chapter_raises_routing_error(self, db_session, factories):
        """Цикл, НЕ проходящий через стартовый раздел: S -> A -> B -> A.
        Стартовый раздел в цикле не участвует, поэтому проверка «цепочка
        вернулась к старту» его не видит; отказ обязан быть тем же
        `RoutingError`, а не обрезанный путь."""
        proposal = _proposal(factories)
        a = _chapter(factories, proposal, title="Раздел A")
        b = _chapter(factories, proposal, title="Раздел B", parent=a)
        s = _chapter(factories, proposal, title="Раздел S", parent=a)
        db_session.flush()
        a.chapter_item_id = b.id
        db_session.flush()

        with pytest.raises(RoutingError):
            chapter_paths(db_session, [s.id])

    def test_cycle_raises_routing_error_without_hanging(self, db_session, factories):
        """Цикл `A -> B -> A` — `RoutingError`, не зависание (план, спека §2.8 п. 2). Прогон обязан идти под шелл-`timeout` (см. отчёт
        задачи) — плагина `pytest-timeout` в проекте нет."""
        proposal = _proposal(factories)
        a = _chapter(factories, proposal, title="Раздел A")
        b = _chapter(factories, proposal, title="Раздел B", parent=a)
        db_session.flush()
        a.chapter_item_id = b.id
        db_session.flush()

        with pytest.raises(RoutingError):
            chapter_paths(db_session, [a.id])

    def test_empty_input_gives_empty_dict_without_querying(self, db_session):
        """Пустой вход — пустой словарь БЕЗ обращения к БД (докстрока
        `chapter_paths`): карточке без разделов третий запрос не нужен."""
        with _capturing_sql(db_session) as statements:
            result = chapter_paths(db_session, [])
        assert result == {}
        assert statements == []


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

    def test_unconfirm_kind_route_recomputes_by_rule(self, admin_client, db_session, factories):
        """`unconfirm: true` в теле того же маршрута `POST .../kind`
        возвращает подтверждённый вид к `SUGGESTED`, вид пересчитан
        правилом (`source='rule'`), а не сохранён прежним."""
        m2 = _unit_id(db_session, "M2")
        cp = factories.CatalogPositionFactory.create(standard_job_title="Стяжка пола", unit_id=m2)
        proposal = _proposal(factories)
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket, semantic_kind=SemanticKind.SYSTEM.value)
        position = _position(factories, proposal, catalog_position=cp)
        _member(db_session, position, ctx)

        confirmed = admin_client.post(f"{BASE}/contexts/{ctx.id}/kind", json={"kind": "WORK"})
        assert confirmed.status_code == 200
        assert confirmed.json()["semantic_state"] == "CONFIRMED"

        unconfirmed = admin_client.post(f"{BASE}/contexts/{ctx.id}/kind", json={"unconfirm": True})
        assert unconfirmed.status_code == 200
        body = unconfirmed.json()
        assert body["semantic_state"] == "SUGGESTED"
        assert body["semantic_kind_source"] == DecisionSource.rule.value

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

    def test_card_reports_member_count_without_members_list(self, admin_client, db_session, factories):
        """Карточка не несёт членств поштучно (спека §2.8 п. 5, удалены
        `members`/`members_truncated`) — только агрегат `member_count`;
        строка ПО СМЕТЕ (`job_title_in_proposal`, не каталожное имя — они
        могут расходиться) и id сметы читаются постранично запросом группы
        (`TestGroupMembers`), не карточкой."""
        estimate = factories.EstimateFactory.create()
        lot = factories.LotFactory.create(estimate=estimate)
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
        assert "members" not in body
        assert "members_truncated" not in body

    def test_card_of_empty_context_has_no_members_keys_either(
        self, admin_client, db_session, factories
    ):
        """Тот же отказ от `members`/`members_truncated` — на контексте БЕЗ
        членств вовсе: пустая карточка не сохранила бы поле пустым списком
        случайно, если бы сериализатор строил его условно."""
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        db_session.flush()

        card = admin_client.get(f"{BASE}/contexts/{ctx.id}")
        assert card.status_code == 200
        body = card.json()
        assert body["member_count"] == 0
        assert "members" not in body
        assert "members_truncated" not in body

    def test_card_member_reports_stale_and_conflict_fields(self, admin_client, db_session, factories):
        """Устаревшее и конфликтное членства несут РАЗНЫЕ факты (спека §2.5):
        `membership_state=STALE` у одного, `conflict_at`/
        `conflict_from_context_id` у другого — экран различает их действия
        («принять предложение переноса» / «принять решение цели») ИМЕННО по
        этим полям, а не по догадке. Строка читается запросом группы
        (`GET .../members`, спека §2.8 п. 3) — карточка членства поштучно
        больше не несёт (§2.8 п. 5)."""
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

        page = admin_client.get(f"{BASE}/contexts/{ctx.id}/members")
        assert page.status_code == 200
        by_id = {m["position_item_id"]: m for m in page.json()["items"]}

        stale = by_id[stale_position.id]
        assert stale["membership_state"] == MembershipState.STALE.value
        assert stale["conflict_at"] is None
        assert stale["conflict_from_context_id"] is None

        conflicted = by_id[conflicted_position.id]
        assert conflicted["membership_state"] == MembershipState.CURRENT.value
        assert conflicted["conflict_at"] is not None
        assert conflicted["conflict_from_context_id"] == other_ctx.id
        assert conflicted["routed_by"] == RoutedBy.manual.value

    def test_card_members_query_count_independent_of_member_count(
        self, admin_client, db_session, factories
    ):
        """Спека §2.8 п. 2 УСИЛИВАЕТ старый инвариант: число запросов
        карточки не растёт ни с числом членств, ни с числом РАЗЛИЧНЫХ путей,
        ни с их ГЛУБИНОЙ (спека §2.8 п. 2). Старое сравнение (все
        позиции без разделов) N+1 по путям не заметило бы — здесь контекст с
        ОДНИМ путём глубины 2 и 2 членствами против контекста с ДВЕНАДЦАТЬЮ
        РАЗНЫМИ путями глубины 4 и 30 членствами; верхние уровни путей тоже
        РАЗНЫЕ (не общий корень для всех 12) — иначе N+1 по предкам остался
        бы незамеченным."""
        def _context_with_one_path(*, depth: int, member_count: int) -> CatalogContext:
            estimate = factories.EstimateFactory.create()
            lot = factories.LotFactory.create(estimate=estimate)
            proposal = factories.ProposalFactory.create(lot=lot)
            cp = factories.CatalogPositionFactory.create()
            bucket = _bucket(db_session, catalog_position=cp)
            # `LOCATION_ONLY` — эта сторона сравнения проносит вычисление
            # `representative_work_title` (задача 3) через путь раздела
            # представителя, уже посчитанный тем же вызовом `chapter_paths()`;
            # число запросов ниже обязано остаться равным независимо от роли.
            ctx = _context(db_session, bucket, name_role=NameRole.LOCATION_ONLY.value)
            chapter = None
            for level in range(depth):
                chapter = _chapter(factories, proposal, title=f"Раздел {level}", parent=chapter)
            for i in range(member_count):
                position = _position(
                    factories, proposal, chapter=chapter, catalog_position=cp, title=f"Поз {i}"
                )
                _member(db_session, position, ctx)
            db_session.flush()
            return ctx

        def _context_with_many_paths(
            *, path_count: int, depth: int, member_count: int
        ) -> CatalogContext:
            estimate = factories.EstimateFactory.create()
            lot = factories.LotFactory.create(estimate=estimate)
            proposal = factories.ProposalFactory.create(lot=lot)
            cp = factories.CatalogPositionFactory.create()
            bucket = _bucket(db_session, catalog_position=cp)
            ctx = _context(db_session, bucket)
            leaves = []
            for path_index in range(path_count):
                chapter = None
                for level in range(depth):
                    # Заголовок несёт и индекс ветки, и уровень — верхние
                    # уровни РАЗНЫХ веток не совпадают, N+1 по предкам был бы
                    # виден числом запросов, а не только результатом.
                    chapter = _chapter(
                        factories, proposal,
                        title=f"Ветка {path_index}, уровень {level}",
                        parent=chapter,
                    )
                leaves.append(chapter)
            for path_index, leaf in enumerate(leaves):
                n = member_count // path_count + (1 if path_index < member_count % path_count else 0)
                for i in range(n):
                    position = _position(
                        factories, proposal, chapter=leaf, catalog_position=cp,
                        title=f"Поз {path_index}-{i}",
                    )
                    _member(db_session, position, ctx)
            db_session.flush()
            return ctx

        ctx_small = _context_with_one_path(depth=2, member_count=2)
        with _capturing_sql(db_session) as statements_small:
            response_small = admin_client.get(f"{BASE}/contexts/{ctx_small.id}")
        assert response_small.status_code == 200
        body_small = response_small.json()
        assert body_small["member_count"] == 2
        assert len(body_small["member_paths"]) == 1
        assert len(body_small["member_paths"][0]["path"]) == 2
        count_small = len(statements_small)
        # Ветка `representative_work_title` на этой стороне реально дошла до
        # названия раздела (а не вышла по `None` раньше) — иначе равенство
        # ниже не говорило бы ничего о её запросах.
        assert body_small["representative_work_title"] == "Раздел 1"
        # Сравнение small/large ниже не видит запроса, добавленного НА КАЖДУЮ
        # карточку (например, путь представителя, читаемый отдельно ДО
        # проверки роли, — обе стороны получили бы +1). Абсолютное число —
        # см. докстроку `_CARD_QUERY_COUNT`; правка, законно меняющая набор
        # запросов карточки, обновляет константу осознанно.
        assert count_small == _CARD_QUERY_COUNT, count_small

        ctx_large = _context_with_many_paths(path_count=12, depth=4, member_count=30)
        with _capturing_sql(db_session) as statements_large:
            response_large = admin_client.get(f"{BASE}/contexts/{ctx_large.id}")
        assert response_large.status_code == 200
        body_large = response_large.json()
        assert body_large["member_count"] == 30
        assert len(body_large["member_paths"]) == 12
        assert all(len(mp["path"]) == 4 for mp in body_large["member_paths"])
        count_large = len(statements_large)

        assert count_small == count_large, (count_small, count_large)

    def test_card_member_paths_four_groups_null_last_at_any_size(
        self, admin_client, db_session, factories
    ):
        """Контекст с позициями в трёх разделах и одной позицией без раздела
        — `member_paths` из ЧЕТЫРЁХ групп, сумма `member_count` групп равна
        `member_count` карточки, порядок — по убыванию `member_count`, группа
        `chapter_item_id=None` (`path=[]`) — ПОСЛЕДНЕЙ ПРИ ЛЮБОМ её размере
        (спека §2.8 п. 2): здесь безраздельная группа — САМАЯ
        БОЛЬШАЯ (5 позиций), и всё равно идёт последней."""
        estimate = factories.EstimateFactory.create()
        lot = factories.LotFactory.create(estimate=estimate)
        proposal = factories.ProposalFactory.create(lot=lot)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)

        chapter_a = _chapter(factories, proposal, title="Раздел А")
        chapter_b = _chapter(factories, proposal, title="Раздел Б")
        chapter_c = _chapter(factories, proposal, title="Раздел В")

        counts_by_chapter = {chapter_a: 1, chapter_b: 3, chapter_c: 2}
        for chapter, n in counts_by_chapter.items():
            for i in range(n):
                position = _position(
                    factories, proposal, chapter=chapter, catalog_position=cp,
                    title=f"{chapter.job_title_in_proposal} поз {i}",
                )
                _member(db_session, position, ctx)
        # Безраздельная группа — САМАЯ БОЛЬШАЯ (5 позиций): без раздела
        # обязана идти последней несмотря на размер.
        for i in range(5):
            position = _position(factories, proposal, catalog_position=cp, title=f"Без раздела {i}")
            _member(db_session, position, ctx)
        db_session.flush()

        card = admin_client.get(f"{BASE}/contexts/{ctx.id}")
        assert card.status_code == 200
        body = card.json()
        assert body["member_count"] == 1 + 3 + 2 + 5
        member_paths = body["member_paths"]
        assert len(member_paths) == 4
        assert sum(mp["member_count"] for mp in member_paths) == body["member_count"]
        # Последняя группа — безраздельная, несмотря на то, что она самая
        # большая по числу членств.
        assert member_paths[-1]["chapter_item_id"] is None
        assert member_paths[-1]["path"] == []
        assert member_paths[-1]["member_count"] == 5
        # Остальные три — по убыванию member_count: Б(3), В(2), А(1).
        rest = member_paths[:-1]
        assert [mp["member_count"] for mp in rest] == [3, 2, 1]
        assert [mp["path"] for mp in rest] == [["Раздел Б"], ["Раздел В"], ["Раздел А"]]

    def test_card_member_paths_stale_and_conflict_counts_match_actual_memberships(
        self, admin_client, db_session, factories
    ):
        """`stale_count` и `conflict_count` группы равны числу ТАКИХ членств
        В НЕЙ (спека §2.8 п. 2) — обе оси независимы (спека
        §2.5), одна и та же группа несёт оба счётчика раздельно."""
        estimate = factories.EstimateFactory.create()
        lot = factories.LotFactory.create(estimate=estimate)
        proposal = factories.ProposalFactory.create(lot=lot)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        (other_category_id,) = _leaf_category_ids(db_session, 1)
        other_bucket = _bucket(db_session, catalog_position=cp, work_category_id=other_category_id)
        other_ctx = _context(db_session, other_bucket, is_default=False)

        # Счётчики РАЗНЫЕ (устаревших 3, конфликтных 2, всего 5): при равных
        # подмена одного счётчика другим прошла бы незамеченной. Одно
        # членство несёт ОБЕ оси сразу — оно входит в оба счётчика.
        chapter = _chapter(factories, proposal, title="Раздел с устареванием")
        current_position = _position(factories, proposal, chapter=chapter, catalog_position=cp, title="Текущее")
        _member(db_session, current_position, ctx)
        for i in range(2):
            stale_position = _position(
                factories, proposal, chapter=chapter, catalog_position=cp, title=f"Устаревшее {i}"
            )
            _member(db_session, stale_position, ctx, membership_state=MembershipState.STALE.value)
        conflicted_position = _position(
            factories, proposal, chapter=chapter, catalog_position=cp, title="Конфликтное"
        )
        _member(
            db_session, conflicted_position, ctx,
            conflict_at=_now(), conflict_from_context_id=other_ctx.id,
            routed_by=RoutedBy.manual.value,
        )
        stale_and_conflicted = _position(
            factories, proposal, chapter=chapter, catalog_position=cp, title="Устаревшее и конфликтное"
        )
        _member(
            db_session, stale_and_conflicted, ctx,
            membership_state=MembershipState.STALE.value,
            conflict_at=_now(), conflict_from_context_id=other_ctx.id,
            routed_by=RoutedBy.manual.value,
        )
        # Вторая группа — свои счётчики, не общие на карточку.
        other_chapter = _chapter(factories, proposal, title="Раздел без отметок")
        plain_position = _position(
            factories, proposal, chapter=other_chapter, catalog_position=cp, title="Текущее другое"
        )
        _member(db_session, plain_position, ctx)
        db_session.flush()

        card = admin_client.get(f"{BASE}/contexts/{ctx.id}")
        assert card.status_code == 200
        member_paths = card.json()["member_paths"]
        assert len(member_paths) == 2
        by_chapter = {mp["chapter_item_id"]: mp for mp in member_paths}
        group = by_chapter[chapter.id]
        assert group["member_count"] == 5
        assert group["stale_count"] == 3
        assert group["conflict_count"] == 2
        plain_group = by_chapter[other_chapter.id]
        assert plain_group["member_count"] == 1
        assert plain_group["stale_count"] == 0
        assert plain_group["conflict_count"] == 0

    # -----------------------------------------------------------------
    #  representative_work_title — от СОХРАНЁННОЙ роли, не повторной
    #  классификацией (спека `2026-09-25-families-screen-design.md` §2.8
    #  п. 2; план, задача 3, «Утверждения»).
    # -----------------------------------------------------------------

    def test_card_representative_work_title_uses_working_chapter_of_smallest_position_item_id(
        self, admin_client, db_session, factories
    ):
        """`LOCATION_ONLY` под рабочим разделом — `representative_work_title`
        равен разделу членства с НАИМЕНЬШИМ `position_item_id` (тот же
        критерий, что `_classify_new_context_semantics`,
        `services/context_operations.py`, применяет к представителю при
        разделении). Второе членство несёт ДРУГОЙ рабочий раздел и БОЛЬШИЙ
        `position_item_id` — выбор не того представителя был бы виден по
        несовпадению названия раздела."""
        estimate = factories.EstimateFactory.create()
        lot = factories.LotFactory.create(estimate=estimate)
        proposal = factories.ProposalFactory.create(lot=lot)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket, name_role=NameRole.LOCATION_ONLY.value)

        chapter_first = _chapter(factories, proposal, title="Общестроительные работы")
        chapter_second = _chapter(factories, proposal, title="Электромонтажные работы")
        position_first = _position(
            factories, proposal, chapter=chapter_first, catalog_position=cp, title="Секция 1"
        )
        position_second = _position(
            factories, proposal, chapter=chapter_second, catalog_position=cp, title="Секция 2"
        )
        position_third = _position(
            factories, proposal, chapter=chapter_second, catalog_position=cp, title="Секция 3"
        )
        assert position_first.id < position_second.id < position_third.id
        # Членства добавляются в ОБРАТНОМ порядке id: порядок ВСТАВКИ не
        # маскирует выбор по наименьшему `position_item_id`.
        _member(db_session, position_third, ctx)
        _member(db_session, position_second, ctx)
        _member(db_session, position_first, ctx)
        db_session.flush()

        card = admin_client.get(f"{BASE}/contexts/{ctx.id}")
        assert card.status_code == 200
        body = card.json()
        # Группа представителя — НЕ первая в `member_paths` (у второго раздела
        # два членства против одного): путь первой группы вместо пути
        # представителя дал бы другое название.
        assert body["member_paths"][0]["chapter_item_id"] == chapter_second.id
        assert body["representative_work_title"] == "Общестроительные работы"

    def test_card_representative_work_title_from_manual_location_only_on_work_row(
        self, admin_client, db_session, factories
    ):
        """Оператор вручную поставил `LOCATION_ONLY` строке, которую
        классификатор посчитал бы `WORK` («Шпатлевка стен» — обычное имя
        работы, не место): `representative_work_title` — рабочий раздел
        представителя, а НЕ наименование строки. КЛЮЧЕВОЙ вход задачи 3:
        реализация повторной классификацией (`classify_name_role` от
        исходного `title`) вернула бы наименование строки, а не раздел."""
        estimate = factories.EstimateFactory.create()
        lot = factories.LotFactory.create(estimate=estimate)
        proposal = factories.ProposalFactory.create(lot=lot)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket, name_role=NameRole.WORK.value)
        chapter = _chapter(factories, proposal, title="Общестроительные работы")
        position = _position(
            factories, proposal, chapter=chapter, catalog_position=cp, title="Шпатлевка стен"
        )
        _member(db_session, position, ctx)
        db_session.flush()

        role_response = admin_client.post(
            f"{BASE}/contexts/{ctx.id}/name-role", json={"role": "LOCATION_ONLY"}
        )
        assert role_response.status_code == 200

        card = admin_client.get(f"{BASE}/contexts/{ctx.id}")
        assert card.status_code == 200
        assert card.json()["representative_work_title"] == "Общестроительные работы"

    def test_card_representative_work_title_none_after_manual_work_override(
        self, admin_client, db_session, factories
    ):
        """Оператор вручную сменил `LOCATION_ONLY` на `WORK` строке-месту
        («Секция 1» — классификатор дал бы `LOCATION_ONLY`):
        `representative_work_title = None` при сохранённой роли `WORK`, хотя
        цепочка несёт рабочий раздел — реализация повторной классификацией
        ошибочно вернула бы название раздела."""
        estimate = factories.EstimateFactory.create()
        lot = factories.LotFactory.create(estimate=estimate)
        proposal = factories.ProposalFactory.create(lot=lot)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket, name_role=NameRole.LOCATION_ONLY.value)
        chapter = _chapter(factories, proposal, title="Общестроительные работы")
        position = _position(
            factories, proposal, chapter=chapter, catalog_position=cp, title="Секция 1"
        )
        _member(db_session, position, ctx)
        db_session.flush()

        role_response = admin_client.post(
            f"{BASE}/contexts/{ctx.id}/name-role", json={"role": "WORK"}
        )
        assert role_response.status_code == 200

        card = admin_client.get(f"{BASE}/contexts/{ctx.id}")
        assert card.status_code == 200
        assert card.json()["representative_work_title"] is None

    def test_card_representative_work_title_none_for_generic_work_role(
        self, admin_client, db_session, factories
    ):
        """Сохранённая роль `GENERIC_WORK` (третье значение `NameRole`) под
        рабочим разделом — `None`: подпись есть только у `LOCATION_ONLY`, а не
        у всякой роли, отличной от `WORK`. Строка — место («Секция 1»), так что
        и цепочка, и классификатор дали бы название раздела."""
        estimate = factories.EstimateFactory.create()
        lot = factories.LotFactory.create(estimate=estimate)
        proposal = factories.ProposalFactory.create(lot=lot)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket, name_role=NameRole.GENERIC_WORK.value)
        chapter = _chapter(factories, proposal, title="Общестроительные работы")
        position = _position(
            factories, proposal, chapter=chapter, catalog_position=cp, title="Секция 1"
        )
        _member(db_session, position, ctx)
        db_session.flush()

        card = admin_client.get(f"{BASE}/contexts/{ctx.id}")
        assert card.status_code == 200
        assert card.json()["representative_work_title"] is None

    def test_card_representative_work_title_none_for_empty_context(
        self, admin_client, db_session, factories
    ):
        """Контекст без членств — представителя нет по построению, `None`,
        хотя сохранённая роль `LOCATION_ONLY`."""
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket, name_role=NameRole.LOCATION_ONLY.value)
        db_session.flush()

        card = admin_client.get(f"{BASE}/contexts/{ctx.id}")
        assert card.status_code == 200
        assert card.json()["representative_work_title"] is None

    def test_card_representative_work_title_none_when_chain_is_all_places(
        self, admin_client, db_session, factories
    ):
        """`LOCATION_ONLY` сохранено, но цепочка над разделом представителя
        целиком из словарных мест — рабочего раздела нет ни на одном
        уровне, `None`."""
        estimate = factories.EstimateFactory.create()
        lot = factories.LotFactory.create(estimate=estimate)
        proposal = factories.ProposalFactory.create(lot=lot)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket, name_role=NameRole.LOCATION_ONLY.value)
        root_chapter = _chapter(factories, proposal, title="Паркинг")
        chapter = _chapter(factories, proposal, title="Секция 1", parent=root_chapter)
        position = _position(
            factories, proposal, chapter=chapter, catalog_position=cp, title="Секция 2"
        )
        _member(db_session, position, ctx)
        db_session.flush()

        card = admin_client.get(f"{BASE}/contexts/{ctx.id}")
        assert card.status_code == 200
        assert card.json()["representative_work_title"] is None

    def test_card_representative_work_title_none_when_representative_has_no_chapter(
        self, admin_client, db_session, factories
    ):
        """Представитель (наименьший `position_item_id`) — позиция БЕЗ раздела,
        а у второго членства рабочий раздел есть: `None`. Работа берётся из
        цепочки ПРЕДСТАВИТЕЛЯ, а не из первого членства, у которого раздел
        нашёлся, и не из самой населённой группы `member_paths`."""
        estimate = factories.EstimateFactory.create()
        lot = factories.LotFactory.create(estimate=estimate)
        proposal = factories.ProposalFactory.create(lot=lot)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket, name_role=NameRole.LOCATION_ONLY.value)
        chapter = _chapter(factories, proposal, title="Общестроительные работы")
        position_without_chapter = _position(
            factories, proposal, catalog_position=cp, title="Секция 1"
        )
        position_with_chapter = _position(
            factories, proposal, chapter=chapter, catalog_position=cp, title="Секция 2"
        )
        assert position_without_chapter.id < position_with_chapter.id
        _member(db_session, position_with_chapter, ctx)
        _member(db_session, position_without_chapter, ctx)
        db_session.flush()

        card = admin_client.get(f"{BASE}/contexts/{ctx.id}")
        assert card.status_code == 200
        body = card.json()
        # Обе группы на месте — раздел второго членства карточке известен.
        assert [mp["chapter_item_id"] for mp in body["member_paths"]] == [chapter.id, None]
        assert body["representative_work_title"] is None

    def test_card_representative_work_title_none_when_representative_path_missing(
        self, admin_client, db_session, factories, monkeypatch
    ):
        """Раздел представителя читается одним запросом, а пути — другим
        (`_member_paths_and_stale_groups`); между ними конкурентная операция
        может увести представителя из контекста, и его раздела среди путей не
        окажется. Карточка тогда отдаёт `None`, а не падает. Гонка
        воспроизведена подменой: настоящий `_member_paths_and_stale_groups`
        отдаёт пути без раздела представителя."""
        estimate = factories.EstimateFactory.create()
        lot = factories.LotFactory.create(estimate=estimate)
        proposal = factories.ProposalFactory.create(lot=lot)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket, name_role=NameRole.LOCATION_ONLY.value)
        chapter = _chapter(factories, proposal, title="Общестроительные работы")
        position = _position(
            factories, proposal, chapter=chapter, catalog_position=cp, title="Секция 1"
        )
        _member(db_session, position, ctx)
        db_session.flush()

        real = crud_semantic._member_paths_and_stale_groups

        def _without_representative_path(db, *, context_id):
            member_paths, stale_groups, paths_by_chapter = real(db, context_id=context_id)
            assert chapter.id in paths_by_chapter
            return member_paths, stale_groups, {}

        monkeypatch.setattr(
            crud_semantic, "_member_paths_and_stale_groups", _without_representative_path
        )

        card = admin_client.get(f"{BASE}/contexts/{ctx.id}")
        assert card.status_code == 200
        assert card.json()["representative_work_title"] is None

    def test_card_stale_groups_target_category_from_manual_reallocation(
        self, admin_client, db_session, factories
    ):
        """После ручного разноса раздела в статью C у его устаревших членств
        — ОДНА запись `stale_groups` с `target_category_id = C` и её кодом и
        названием; членства ДРУГОГО раздела той же карточки в эту запись не
        попадают (спека §2.8 п. 2)."""
        estimate = factories.EstimateFactory.create()
        lot = factories.LotFactory.create(estimate=estimate)
        proposal = factories.ProposalFactory.create(lot=lot)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        (category_c,) = _leaf_category_ids(db_session, 1)
        category = db_session.get(WorkCategory, category_c)

        reallocated_chapter = _chapter(
            factories, proposal, title="Раздел, разнесённый вручную",
            category_id=category_c, category_source="manual",
        )
        other_chapter = _chapter(factories, proposal, title="Другой раздел")

        # Две устаревшие позиции разнесённого раздела с РАЗНЫМ `routed_by`
        # (запись обязана быть одна на раздел, а не на иной признак членства)
        # и одна ТЕКУЩАЯ — она в счёт устаревшей группы не входит.
        for i, routed_by in enumerate((RoutedBy.default.value, RoutedBy.manual.value)):
            stale_in_reallocated = _position(
                factories, proposal, chapter=reallocated_chapter, catalog_position=cp,
                title=f"Устаревшее в разнесённом {i}",
            )
            _member(
                db_session, stale_in_reallocated, ctx,
                membership_state=MembershipState.STALE.value, routed_by=routed_by,
            )
        current_in_reallocated = _position(
            factories, proposal, chapter=reallocated_chapter, catalog_position=cp,
            title="Текущее в разнесённом",
        )
        _member(db_session, current_in_reallocated, ctx)

        stale_in_other = _position(
            factories, proposal, chapter=other_chapter, catalog_position=cp,
            title="Устаревшее в другом",
        )
        _member(db_session, stale_in_other, ctx, membership_state=MembershipState.STALE.value)

        # Устаревшее членство позиции БЕЗ раздела — своя группа
        # `chapter_item_id=None` (спека §2.8 п. 2: «`int | None` тем же
        # правилом»), цель переноса у неё не определена.
        stale_without_chapter = _position(
            factories, proposal, catalog_position=cp, title="Устаревшее без раздела",
        )
        _member(db_session, stale_without_chapter, ctx, membership_state=MembershipState.STALE.value)
        db_session.flush()

        card = admin_client.get(f"{BASE}/contexts/{ctx.id}")
        assert card.status_code == 200
        stale_groups = card.json()["stale_groups"]
        chapter_ids = [sg["chapter_item_id"] for sg in stale_groups]
        assert chapter_ids.count(reallocated_chapter.id) == 1, stale_groups
        assert len(stale_groups) == 3, stale_groups
        by_chapter = {sg["chapter_item_id"]: sg for sg in stale_groups}
        entry = by_chapter[reallocated_chapter.id]
        assert entry["count"] == 2
        assert entry["path"] == ["Раздел, разнесённый вручную"]
        assert entry["target_category_id"] == category_c
        assert entry["target_category_code"] == category.code
        assert entry["target_category_title"] == category.title

        # Членства другого раздела — своя ОТДЕЛЬНАЯ запись, не смешаны с
        # первой.
        other_entry = by_chapter[other_chapter.id]
        assert other_entry["count"] == 1
        assert other_entry["target_category_id"] is None

        assert None in by_chapter, stale_groups
        null_entry = by_chapter[None]
        assert null_entry["count"] == 1
        assert null_entry["path"] == []
        assert null_entry["target_category_id"] is None
        assert null_entry["target_category_code"] is None
        assert null_entry["target_category_title"] is None
        # Безраздельная группа — последней, как у `member_paths`.
        assert stale_groups[-1]["chapter_item_id"] is None

    def test_card_stale_groups_excludes_group_whose_only_stale_member_is_conflicted(
        self, admin_client, db_session, factories
    ):
        """Группа, где ЕДИНСТВЕННОЕ устаревшее членство ещё и конфликтное, не
        должна давать запись `stale_groups` вовсе (MAJOR-1, ревью Fable
        27.09.2026): `stale_groups.count` — множество, которое реально берёт
        пакетный перенос (`transfer_stale_group`,
        `services/context_operations.py`) — `STALE AND conflict_at IS NULL`.
        Конфликтное устаревшее членство решается отдельным действием
        «Принять решение цели» (спека §2.6), не пакетным переносом; если бы
        карточка предложила перенос группы без единого переносимого членства,
        ответ пакета всегда был бы `moved=0, refused=0` — кнопка,
        неспособная сама себя выполнить."""
        estimate = factories.EstimateFactory.create()
        lot = factories.LotFactory.create(estimate=estimate)
        proposal = factories.ProposalFactory.create(lot=lot)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        (other_category_id,) = _leaf_category_ids(db_session, 1)
        other_bucket = _bucket(db_session, catalog_position=cp, work_category_id=other_category_id)
        other_ctx = _context(db_session, other_bucket, is_default=False)

        chapter = _chapter(factories, proposal, title="Раздел с конфликтным устареванием")
        stale_and_conflicted = _position(
            factories, proposal, chapter=chapter, catalog_position=cp,
            title="Устаревшее и конфликтное",
        )
        _member(
            db_session, stale_and_conflicted, ctx,
            membership_state=MembershipState.STALE.value,
            conflict_at=_now(), conflict_from_context_id=other_ctx.id,
            routed_by=RoutedBy.manual.value,
        )
        db_session.flush()

        card = admin_client.get(f"{BASE}/contexts/{ctx.id}")
        assert card.status_code == 200
        body = card.json()
        # Ось `member_paths.stale_count` не тронута — она считает ВСЕ
        # `STALE`-членства, включая конфликтные (независимая величина).
        member_paths = body["member_paths"]
        assert len(member_paths) == 1
        assert member_paths[0]["stale_count"] == 1
        assert member_paths[0]["conflict_count"] == 1
        # А вот `stale_groups` — пуст: единственное устаревшее членство
        # группы конфликтно, переносить пакетом нечего.
        assert body["stale_groups"] == [], body["stale_groups"]

    def test_card_stale_groups_count_excludes_conflicted_and_batch_transfer_moves_only_them(
        self, admin_client, db_session, factories
    ):
        """Группа «2 обычных STALE + 1 STALE-и-конфликтное» — `stale_groups`
        несёт `count = 2` (только переносимые), пакетный перенос переносит
        РОВНО эти 2, а перечитанная карточка после переноса не содержит
        записи `stale_groups` для этого раздела вовсе (конфликтное членство
        осталось, но оно уже не переносимо, значит группы без
        переносимых членств быть не должно — MAJOR-1)."""
        cp = factories.CatalogPositionFactory.create()
        proposal = _proposal(factories)
        cat_target, cat_source, cat_conflict_from = _leaf_category_ids(db_session, 3)
        chapter = _chapter(
            factories, proposal, title="ГруппаСКонфликтным",
            category_id=cat_target, category_source="file",
        )
        source_bucket = _bucket(db_session, catalog_position=cp, work_category_id=cat_source)
        source_ctx = _context(db_session, source_bucket)
        # Категория «источника конфликта» — ТРЕТЬЯ, отличная от цели переноса:
        # бакет с `work_category_id=cat_target` уже существовал бы как
        # НЕ-дефолтный, и `accept_transfer` не смог бы (пере)использовать его
        # дефолтным целевым бакетом группы — эта коллизия не то, что здесь
        # проверяется.
        other_bucket = _bucket(db_session, catalog_position=cp, work_category_id=cat_conflict_from)
        other_ctx = _context(db_session, other_bucket, is_default=False)

        plain_stale = [
            _position(
                factories, proposal, chapter=chapter, catalog_position=cp,
                title=f"Обычное устаревшее {i}",
            )
            for i in range(2)
        ]
        for pos in plain_stale:
            _member(db_session, pos, source_ctx, membership_state=MembershipState.STALE.value)
        conflicted_stale = _position(
            factories, proposal, chapter=chapter, catalog_position=cp,
            title="Устаревшее и конфликтное",
        )
        _member(
            db_session, conflicted_stale, source_ctx,
            membership_state=MembershipState.STALE.value,
            conflict_at=_now(), conflict_from_context_id=other_ctx.id,
            routed_by=RoutedBy.manual.value,
        )
        db_session.commit()

        card_before = admin_client.get(f"{BASE}/contexts/{source_ctx.id}")
        assert card_before.status_code == 200
        stale_groups_before = card_before.json()["stale_groups"]
        by_chapter_before = {sg["chapter_item_id"]: sg for sg in stale_groups_before}
        assert by_chapter_before[chapter.id]["count"] == 2, stale_groups_before

        response = admin_client.post(
            f"{BASE}/contexts/{source_ctx.id}/stale-groups/transfer",
            json={"chapter_item_id": chapter.id, "expected_category_id": cat_target},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["moved"] == 2
        assert body["refused"] == 0
        moved_ids = {r["position_item_id"] for r in body["results"]}
        assert moved_ids == {pos.id for pos in plain_stale}

        db_session.expire_all()
        card_after = admin_client.get(f"{BASE}/contexts/{source_ctx.id}")
        assert card_after.status_code == 200
        stale_groups_after = card_after.json()["stale_groups"]
        by_chapter_after = {sg["chapter_item_id"]: sg for sg in stale_groups_after}
        assert chapter.id not in by_chapter_after, stale_groups_after

    def test_card_of_context_with_chapter_cycle_gives_422_not_500(
        self, admin_client, db_session, factories
    ):
        """Карточка контекста, чей ближайший раздел зациклен, отвечает
        доменной ошибкой (422), а не `500` (спека §2.8 п. 2):
        роутер обязан переводить `RoutingError` `context_card`
        (`_read_domain_errors`, `routers/semantic.py`)."""
        estimate = factories.EstimateFactory.create()
        lot = factories.LotFactory.create(estimate=estimate)
        proposal = factories.ProposalFactory.create(lot=lot)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)

        chapter_a = _chapter(factories, proposal, title="Циклический А")
        chapter_b = _chapter(factories, proposal, title="Циклический Б", parent=chapter_a)
        db_session.flush()
        chapter_a.chapter_item_id = chapter_b.id
        db_session.flush()

        position = _position(factories, proposal, chapter=chapter_a, catalog_position=cp)
        _member(db_session, position, ctx)
        db_session.flush()

        card = admin_client.get(f"{BASE}/contexts/{ctx.id}")
        assert card.status_code == 422

    def test_card_of_context_with_cycle_above_its_chapter_gives_422(
        self, admin_client, db_session, factories
    ):
        """Цикл ВЫШЕ ближайшего раздела членства (S -> A -> B -> A): сам
        раздел S в цикле не участвует, но цикл достижим из него — карточка
        обязана ответить `422`, а не отдать обрезанный путь и не `500`."""
        estimate = factories.EstimateFactory.create()
        lot = factories.LotFactory.create(estimate=estimate)
        proposal = factories.ProposalFactory.create(lot=lot)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)

        chapter_a = _chapter(factories, proposal, title="Циклический А")
        chapter_b = _chapter(factories, proposal, title="Циклический Б", parent=chapter_a)
        chapter_s = _chapter(factories, proposal, title="Раздел под циклом", parent=chapter_a)
        db_session.flush()
        chapter_a.chapter_item_id = chapter_b.id
        db_session.flush()

        position = _position(factories, proposal, chapter=chapter_s, catalog_position=cp)
        _member(db_session, position, ctx)
        db_session.flush()

        card = admin_client.get(f"{BASE}/contexts/{ctx.id}")
        assert card.status_code == 422

    @pytest.mark.parametrize("route", ["kind", "name-role", "family"])
    def test_mutation_route_commits_even_when_card_read_gives_422(
        self, admin_client, db_session, factories, route
    ):
        """Каждая мутация, отдающая карточку в ответе (`POST .../kind`,
        `.../name-role`, `.../family`), обязана ЗАКОММИТИТЬСЯ, даже если
        чтение карточки для ОТВЕТА отказывает доменной ошибкой — цикл
        разделов ВЫШЕ ближайшего раздела членства, тот же приём, что
        `test_card_of_context_with_cycle_above_its_chapter_gives_422`. Ответ
        маршрута — `422`, а не `500` (`routers/semantic.py`: чтение карточки
        после `_mutating(db)` идёт через `_read_domain_errors`), и изменение
        при этом уже в БД — читается ЗАНОВО (`db_session.expire_all()` +
        отдельный `SELECT`), а не с ORM-объекта. Чтение карточки ВНУТРИ
        `_mutating` откатило бы мутацию — это и ловит вторая половина."""
        estimate = factories.EstimateFactory.create()
        lot = factories.LotFactory.create(estimate=estimate)
        proposal = factories.ProposalFactory.create(lot=lot)
        m2 = _unit_id(db_session, "M2")
        cp = factories.CatalogPositionFactory.create(unit_id=m2)
        bucket = _bucket(db_session, catalog_position=cp)
        # Исходное состояние отличается от того, что пишет каждая мутация:
        # вид SYSTEM (мутация — WORK), роль WORK от правила (мутация —
        # LOCATION_ONLY вручную), семьи нет (мутация — назначает).
        ctx = _context(
            db_session, bucket,
            semantic_kind=SemanticKind.SYSTEM.value if route == "kind" else SemanticKind.WORK.value,
        )
        family = _family(db_session, admin_client.user, title="Семья под циклом", unit_name="M2")
        body = {
            "kind": {"kind": "WORK"},
            "name-role": {"role": NameRole.LOCATION_ONLY.value},
            "family": {"family_id": family.id},
        }[route]

        chapter_a = _chapter(factories, proposal, title="Циклический А")
        chapter_b = _chapter(factories, proposal, title="Циклический Б", parent=chapter_a)
        chapter_s = _chapter(factories, proposal, title="Раздел под циклом", parent=chapter_a)
        db_session.flush()
        chapter_a.chapter_item_id = chapter_b.id
        db_session.flush()

        position = _position(factories, proposal, chapter=chapter_s, catalog_position=cp)
        _member(db_session, position, ctx)
        db_session.flush()

        response = admin_client.post(f"{BASE}/contexts/{ctx.id}/{route}", json=body)
        assert response.status_code == 422, response.text

        db_session.expire_all()
        row = db_session.execute(
            sa.select(
                CatalogContext.semantic_kind,
                CatalogContext.semantic_kind_source,
                CatalogContext.name_role,
                CatalogContext.name_role_source,
                CatalogContext.work_family_id,
            ).where(CatalogContext.id == ctx.id)
        ).one()
        if route == "kind":
            assert row.semantic_kind == SemanticKind.WORK.value
            assert row.semantic_kind_source == DecisionSource.manual.value
        elif route == "name-role":
            assert row.name_role == NameRole.LOCATION_ONLY.value
            assert row.name_role_source == DecisionSource.manual.value
        else:
            assert row.work_family_id == family.id

    def test_card_member_paths_equal_counts_ordered_by_path(
        self, admin_client, db_session, factories
    ):
        """Спека §2.8 п. 2: `member_count DESC`, ЗАТЕМ путь
        лексикографически. Три группы с РАВНЫМ числом членств, разделы
        заведены в порядке, ОБРАТНОМ алфавитному (id растут от «В» к «А»), —
        порядок по id или порядок выдачи `GROUP BY` здесь не совпадает с
        порядком по пути. Четвёртая группа крупнее и идёт первой."""
        estimate = factories.EstimateFactory.create()
        lot = factories.LotFactory.create(estimate=estimate)
        proposal = factories.ProposalFactory.create(lot=lot)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)

        counts = [("Раздел В", 2), ("Раздел Б", 2), ("Раздел А", 2), ("Раздел Г", 3)]
        for title, n in counts:
            chapter = _chapter(factories, proposal, title=title)
            for i in range(n):
                position = _position(
                    factories, proposal, chapter=chapter, catalog_position=cp, title=f"{title} {i}"
                )
                _member(db_session, position, ctx)
        db_session.flush()

        card = admin_client.get(f"{BASE}/contexts/{ctx.id}")
        assert card.status_code == 200
        member_paths = card.json()["member_paths"]
        assert [(mp["path"], mp["member_count"]) for mp in member_paths] == [
            (["Раздел Г"], 3),
            (["Раздел А"], 2),
            (["Раздел Б"], 2),
            (["Раздел В"], 2),
        ]

    @pytest.mark.parametrize("dot_count", [2, 1, 0, None])
    def test_card_work_category_path(self, admin_client, db_session, factories, dot_count):
        """`work_category_path` карточки — то же значение, что у строки
        списка (спека §2.8 п. 2): родители статьи корзины от корня, без неё
        самой. Третий уровень — прародитель, затем родитель; второй — один
        родитель; первый уровень и корзина без статьи — пустой список.
        Эталон — предки, прочитанные по `parent_id` здесь же, а не через
        запрос карточки."""
        expected: list[dict] = []
        category_id = None
        if dot_count is not None:
            category = _category_with_dot_count(db_session, dot_count)
            category_id = category.id
            ancestor_id = category.parent_id
            while ancestor_id is not None:
                ancestor = db_session.get(WorkCategory, ancestor_id)
                expected.insert(0, {"code": ancestor.code, "title": ancestor.title})
                ancestor_id = ancestor.parent_id
            assert len(expected) == dot_count

        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp, work_category_id=category_id)
        ctx = _context(db_session, bucket)
        db_session.flush()

        card = admin_client.get(f"{BASE}/contexts/{ctx.id}")
        assert card.status_code == 200
        assert card.json()["work_category_path"] == expected

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
#  Членства группы — `GET /members`, `GET /member-ids` (спека
#  `2026-09-25-families-screen-design.md` §2.8 п. 3)
# ---------------------------------------------------------------------------

class TestGroupMembers:
    def test_chapter_no_chapter_and_whole_context_selectors(
        self, admin_client, db_session, factories
    ):
        """Один контекст с позициями под ДВУМЯ разделами и одной БЕЗ
        раздела: `chapter_item_id=A` отдаёт только позиции A, `no_chapter=
        true` — только без раздела, ни то ни другое — ВЕСЬ контекст (спека §2.8 п. 3)."""
        proposal = _proposal(factories)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        chapter_a = _chapter(factories, proposal, title="Раздел А членств")
        chapter_b = _chapter(factories, proposal, title="Раздел Б членств")
        pos_a1 = _position(factories, proposal, chapter=chapter_a, catalog_position=cp, title="А-1")
        pos_a2 = _position(factories, proposal, chapter=chapter_a, catalog_position=cp, title="А-2")
        pos_b = _position(factories, proposal, chapter=chapter_b, catalog_position=cp, title="Б-1")
        pos_none = _position(factories, proposal, catalog_position=cp, title="Без раздела членств")
        for pos in (pos_a1, pos_a2, pos_b, pos_none):
            _member(db_session, pos, ctx)
        db_session.flush()

        by_chapter_a = admin_client.get(
            f"{BASE}/contexts/{ctx.id}/members", params={"chapter_item_id": chapter_a.id}
        )
        assert by_chapter_a.status_code == 200
        body_a = by_chapter_a.json()
        assert body_a["total"] == 2
        assert {m["position_item_id"] for m in body_a["items"]} == {pos_a1.id, pos_a2.id}

        by_no_chapter = admin_client.get(
            f"{BASE}/contexts/{ctx.id}/members", params={"no_chapter": True}
        )
        assert by_no_chapter.status_code == 200
        body_none = by_no_chapter.json()
        assert body_none["total"] == 1
        assert [m["position_item_id"] for m in body_none["items"]] == [pos_none.id]

        whole = admin_client.get(f"{BASE}/contexts/{ctx.id}/members")
        assert whole.status_code == 200
        body_whole = whole.json()
        assert body_whole["total"] == 4
        assert {m["position_item_id"] for m in body_whole["items"]} == {
            pos_a1.id, pos_a2.id, pos_b.id, pos_none.id,
        }

    def test_member_ids_chapter_filter_matches_members_filter(
        self, admin_client, db_session, factories
    ):
        """`member-ids` фильтрует ТЕМИ ЖЕ условиями, что `members` — тот же
        набор, что галочка группы передаёт в «Разделить…»/«Перенести…»
        (спека §2.8 п. 3)."""
        proposal = _proposal(factories)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        chapter = _chapter(factories, proposal, title="Раздел для member-ids")
        pos_in = _position(factories, proposal, chapter=chapter, catalog_position=cp, title="В разделе")
        pos_out = _position(factories, proposal, catalog_position=cp, title="Без раздела для member-ids")
        _member(db_session, pos_in, ctx)
        _member(db_session, pos_out, ctx)
        db_session.flush()

        response = admin_client.get(
            f"{BASE}/contexts/{ctx.id}/member-ids", params={"chapter_item_id": chapter.id}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["position_item_ids"] == [pos_in.id]

    @pytest.mark.parametrize("path_suffix", ["members", "member-ids"])
    def test_chapter_and_no_chapter_together_gives_422(
        self, admin_client, path_suffix
    ):
        """`chapter_item_id` и `no_chapter=true` вместе — противоречие
        («раздел X» и «без раздела» разом невозможны, спека §2.8 п. 3) —
        `422` на ОБОИХ маршрутах. Контекст нарочно НЕСУЩЕСТВУЮЩИЙ: вход
        отвергается ДО обращения к crud (`_group_selector` роутера), значит
        `422`, а не `404`, независимо от того, существует ли контекст."""
        response = admin_client.get(
            f"{BASE}/contexts/999999999/{path_suffix}",
            params={"chapter_item_id": 1, "no_chapter": True},
        )
        assert response.status_code == 422

    def test_state_filters_are_independent_axes(self, admin_client, db_session, factories):
        """`state=stale` фильтрует `membership_state=STALE`, `state=conflict`
        — `conflict_at IS NOT NULL` (спека §2.8 п. 3) — независимые оси:
        членство, отмеченное ОБЕИМИ, входит в оба фильтра, а «ни то ни
        другое» — ни в один."""
        (other_category_id,) = _leaf_category_ids(db_session, 1)
        proposal = _proposal(factories)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        other_bucket = _bucket(db_session, catalog_position=cp, work_category_id=other_category_id)
        other_ctx = _context(db_session, other_bucket, is_default=False)

        pos_both = _position(factories, proposal, catalog_position=cp, title="И то и другое")
        _member(
            db_session, pos_both, ctx,
            membership_state=MembershipState.STALE.value,
            conflict_at=_now(), conflict_from_context_id=other_ctx.id,
        )
        pos_stale_only = _position(factories, proposal, catalog_position=cp, title="Только устарело")
        _member(db_session, pos_stale_only, ctx, membership_state=MembershipState.STALE.value)
        pos_conflict_only = _position(factories, proposal, catalog_position=cp, title="Только конфликт")
        _member(
            db_session, pos_conflict_only, ctx,
            conflict_at=_now(), conflict_from_context_id=other_ctx.id,
        )
        pos_neither = _position(factories, proposal, catalog_position=cp, title="Ни то ни другое")
        _member(db_session, pos_neither, ctx)
        db_session.flush()

        stale = admin_client.get(
            f"{BASE}/contexts/{ctx.id}/members", params={"state": "stale"}
        ).json()
        assert {m["position_item_id"] for m in stale["items"]} == {pos_both.id, pos_stale_only.id}

        conflict = admin_client.get(
            f"{BASE}/contexts/{ctx.id}/members", params={"state": "conflict"}
        ).json()
        assert {m["position_item_id"] for m in conflict["items"]} == {
            pos_both.id, pos_conflict_only.id,
        }

        everything = admin_client.get(
            f"{BASE}/contexts/{ctx.id}/members", params={"state": "all"}
        ).json()
        assert everything["total"] == 4

    def test_member_ids_full_list_no_truncation_on_group_of_520(
        self, admin_client, db_session, factories
    ):
        """Группа в тысячи позиций — `member-ids` отдаёт ПОЛНЫЙ список без
        обрезки (спека §2.8 п. 3). 520 — специально больше прежнего потолка
        членств карточки, снятого спекой §2.8 п. 5."""
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        db_session.flush()
        position_ids = _bulk_members(db_session, factories, context=ctx, count=520)

        response = admin_client.get(f"{BASE}/contexts/{ctx.id}/member-ids")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 520
        assert len(body["position_item_ids"]) == 520
        assert sorted(body["position_item_ids"]) == sorted(position_ids)

    def test_members_paginated_last_page_of_group_of_520(
        self, admin_client, db_session, factories
    ):
        """`limit=100, offset=500` на группе из 520 — 20 строк, `total=520`,
        порядок по `position_item_id` (спека §2.8 п. 3): точные
        id страницы, не только её длина."""
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        db_session.flush()
        position_ids = _bulk_members(db_session, factories, context=ctx, count=520)
        expected_page = sorted(position_ids)[500:520]

        response = admin_client.get(
            f"{BASE}/contexts/{ctx.id}/members", params={"limit": 100, "offset": 500}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 520
        assert len(body["items"]) == 20
        assert [m["position_item_id"] for m in body["items"]] == expected_page
        assert (body["limit"], body["offset"]) == (100, 500)

        # Последняя страница короче ЛЮБОГО limit >= 20, поэтому сама по себе
        # не видит, дошёл ли `limit` до запроса: полная страница из середины —
        # ровно 100 id, не умолчание 50.
        middle = admin_client.get(
            f"{BASE}/contexts/{ctx.id}/members", params={"limit": 100, "offset": 100}
        ).json()
        assert [m["position_item_id"] for m in middle["items"]] == sorted(position_ids)[100:200]

    def test_members_nonexistent_context_gives_404(self, admin_client):
        response = admin_client.get(f"{BASE}/contexts/999999999/members")
        assert response.status_code == 404

    def test_member_ids_nonexistent_context_gives_404(self, admin_client):
        response = admin_client.get(f"{BASE}/contexts/999999999/member-ids")
        assert response.status_code == 404

    def test_members_exactly_two_statements_on_small_and_large_group(
        self, admin_client, db_session, factories
    ):
        """Ровно 2 запроса на группах из 5 и из 520 позиций (спека §2.8 п. 3): счётчик+существование одним SELECT и страница —
        число не растёт вместе с размером группы."""
        cp_small = factories.CatalogPositionFactory.create()
        bucket_small = _bucket(db_session, catalog_position=cp_small)
        ctx_small = _context(db_session, bucket_small)
        db_session.flush()
        _bulk_members(db_session, factories, context=ctx_small, count=5)

        with _capturing_sql(db_session) as statements_small:
            response_small = admin_client.get(f"{BASE}/contexts/{ctx_small.id}/members")
        assert response_small.status_code == 200
        assert len(statements_small) == 2, statements_small

        cp_large = factories.CatalogPositionFactory.create()
        bucket_large = _bucket(db_session, catalog_position=cp_large)
        ctx_large = _context(db_session, bucket_large)
        db_session.flush()
        _bulk_members(db_session, factories, context=ctx_large, count=520)

        with _capturing_sql(db_session) as statements_large:
            response_large = admin_client.get(f"{BASE}/contexts/{ctx_large.id}/members")
        assert response_large.status_code == 200
        assert len(statements_large) == 2, statements_large

    def test_member_ids_exactly_two_statements_on_small_and_large_group(
        self, admin_client, db_session, factories
    ):
        """То же для `member-ids` (спека §2.8 п. 3)."""
        cp_small = factories.CatalogPositionFactory.create()
        bucket_small = _bucket(db_session, catalog_position=cp_small)
        ctx_small = _context(db_session, bucket_small)
        db_session.flush()
        _bulk_members(db_session, factories, context=ctx_small, count=5)

        with _capturing_sql(db_session) as statements_small:
            response_small = admin_client.get(f"{BASE}/contexts/{ctx_small.id}/member-ids")
        assert response_small.status_code == 200
        assert len(statements_small) == 2, statements_small

        cp_large = factories.CatalogPositionFactory.create()
        bucket_large = _bucket(db_session, catalog_position=cp_large)
        ctx_large = _context(db_session, bucket_large)
        db_session.flush()
        _bulk_members(db_session, factories, context=ctx_large, count=520)

        with _capturing_sql(db_session) as statements_large:
            response_large = admin_client.get(f"{BASE}/contexts/{ctx_large.id}/member-ids")
        assert response_large.status_code == 200
        assert len(statements_large) == 2, statements_large

    @pytest.mark.parametrize("path_suffix", ["members", "member-ids"])
    @pytest.mark.parametrize("shape", ["empty_context", "empty_group"])
    def test_existing_context_with_empty_group_gives_200_total_zero_not_404(
        self, admin_client, db_session, factories, path_suffix, shape
    ):
        """Вытесняемое состояние ветки `404`: контекст СУЩЕСТВУЕТ, но группа
        пуста — `200` с `total = 0` и пустым списком, а не `404`. Два входа:
        контекст без единого членства (ловит «существование» по членствам,
        а не по `catalog_contexts`) и контекст с членством, у которого
        выбранный раздел пуст (ловит «нет строк — значит нет контекста»)."""
        proposal = _proposal(factories)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        empty_chapter = _chapter(factories, proposal, title="Пустой раздел группы")
        params = {}
        if shape == "empty_group":
            pos = _position(factories, proposal, catalog_position=cp, title="Вне пустого раздела")
            _member(db_session, pos, ctx)
            params = {"chapter_item_id": empty_chapter.id}
        db_session.flush()

        response = admin_client.get(f"{BASE}/contexts/{ctx.id}/{path_suffix}", params=params)
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 0
        listed = body["items"] if path_suffix == "members" else body["position_item_ids"]
        assert listed == []

    @pytest.mark.parametrize("path_suffix", ["members", "member-ids"])
    def test_chapter_and_no_chapter_together_gives_422_on_existing_context(
        self, admin_client, db_session, factories, path_suffix
    ):
        """`422` на противоречивом селекторе — и у СУЩЕСТВУЮЩЕГО контекста с
        членством в выбранном разделе: без проверки роутера crud молча взял
        бы `chapter_item_id` и ответил `200`. Каждый селектор ПО ОТДЕЛЬНОСТИ
        на том же контексте — не `422`."""
        proposal = _proposal(factories)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        chapter = _chapter(factories, proposal, title="Раздел для 422")
        pos = _position(factories, proposal, chapter=chapter, catalog_position=cp, title="В разделе 422")
        _member(db_session, pos, ctx)
        db_session.flush()

        url = f"{BASE}/contexts/{ctx.id}/{path_suffix}"
        both = admin_client.get(url, params={"chapter_item_id": chapter.id, "no_chapter": True})
        assert both.status_code == 422
        assert admin_client.get(url, params={"chapter_item_id": chapter.id}).status_code == 200
        assert admin_client.get(url, params={"no_chapter": True}).status_code == 200

    def test_member_ids_forwards_no_chapter_and_state(self, admin_client, db_session, factories):
        """`member-ids` передаёт в фильтр ОБА параметра, не только
        `chapter_item_id`: `no_chapter=true` — только позиции без раздела,
        `state=stale`/`state=conflict` — только такие членства (тот же набор,
        что экран берёт для «Принять решение цели» и пакета)."""
        (other_category_id,) = _leaf_category_ids(db_session, 1)
        proposal = _proposal(factories)
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        other_bucket = _bucket(db_session, catalog_position=cp, work_category_id=other_category_id)
        other_ctx = _context(db_session, other_bucket, is_default=False)
        chapter = _chapter(factories, proposal, title="Раздел member-ids состояний")
        pos_stale = _position(factories, proposal, chapter=chapter, catalog_position=cp, title="Устар. в разделе")
        _member(db_session, pos_stale, ctx, membership_state=MembershipState.STALE.value)
        pos_conflict = _position(factories, proposal, catalog_position=cp, title="Конфликт без раздела")
        _member(db_session, pos_conflict, ctx, conflict_at=_now(), conflict_from_context_id=other_ctx.id)
        pos_plain = _position(factories, proposal, catalog_position=cp, title="Обычная без раздела")
        _member(db_session, pos_plain, ctx)
        db_session.flush()

        url = f"{BASE}/contexts/{ctx.id}/member-ids"
        no_chapter = admin_client.get(url, params={"no_chapter": True}).json()
        assert sorted(no_chapter["position_item_ids"]) == sorted([pos_conflict.id, pos_plain.id])
        assert no_chapter["total"] == 2
        stale = admin_client.get(url, params={"state": "stale"}).json()
        assert stale["position_item_ids"] == [pos_stale.id]
        assert stale["total"] == 1
        conflict = admin_client.get(url, params={"state": "conflict"}).json()
        assert conflict["position_item_ids"] == [pos_conflict.id]
        assert conflict["total"] == 1

    @pytest.mark.parametrize(
        "params",
        [{"limit": 0}, {"limit": 101}, {"offset": -1}],
        ids=["limit_zero", "limit_over_max_page_size", "negative_offset"],
    )
    def test_members_page_bounds_give_422(self, admin_client, db_session, factories, params):
        """Спека §2.8 п. 3: «`limit` до `MAX_PAGE_SIZE`» (100); `limit < 1` и
        `offset < 0` — тоже `422`. Контекст существует, иначе вход без
        проверки границ дал бы `404`, а не `200`, и граница читалась бы
        невнятно. Верхняя граница включительно (`limit=100`) — `200` в
        `test_members_paginated_last_page_of_group_of_520`."""
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        db_session.flush()
        _bulk_members(db_session, factories, context=ctx, count=2)

        response = admin_client.get(f"{BASE}/contexts/{ctx.id}/members", params=params)
        assert response.status_code == 422

    def test_members_page_ordered_by_position_not_by_physical_order(
        self, admin_client, db_session, factories
    ):
        """Порядок страницы — `ORDER BY position_item_id`, а не физический
        порядок строк: членства заводятся в ОБРАТНОМ порядке id, а позиции с
        наименьшими id переписываются `UPDATE` (новая версия строки уходит в
        конец кучи) — без `ORDER BY` последовательный просмотр отдал бы их
        последними. Страница `limit=3, offset=0` обязана нести три НАИМЕНЬШИХ
        id по возрастанию."""
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        proposal = _proposal(factories)
        positions = [
            _position(factories, proposal, catalog_position=cp, title=f"Порядок {i}") for i in range(6)
        ]
        db_session.flush()
        ids = sorted(p.id for p in positions)
        for pid in reversed(ids):
            db_session.execute(
                sa.insert(ContextMember).values(
                    position_item_id=pid, context_id=ctx.id, bucket_id=ctx.bucket_id,
                    membership_state=MembershipState.CURRENT.value, routed_by=RoutedBy.default.value,
                )
            )
        db_session.execute(
            sa.update(PositionItem)
            .where(PositionItem.id.in_(ids[:3]))
            .values(job_title_in_proposal=PositionItem.job_title_in_proposal + " (правка)")
        )
        db_session.flush()

        body = admin_client.get(
            f"{BASE}/contexts/{ctx.id}/members", params={"limit": 3, "offset": 0}
        ).json()
        assert [m["position_item_id"] for m in body["items"]] == ids[:3]

    @pytest.mark.parametrize("path_suffix", ["members", "member-ids"])
    def test_unknown_state_gives_422(self, admin_client, db_session, factories, path_suffix):
        """`state` — закрытое множество `all|stale|conflict` (спека §2.8 п. 3):
        иное значение — `422`, а не молчаливое «все»."""
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        db_session.flush()
        _bulk_members(db_session, factories, context=ctx, count=2)

        response = admin_client.get(
            f"{BASE}/contexts/{ctx.id}/{path_suffix}", params={"state": "stail"}
        )
        assert response.status_code == 422

    def test_members_row_shape_has_all_seven_fields(self, admin_client, db_session, factories):
        """Спека §2.8 п. 3: «форма строки — та же, что была у `members[]`
        карточки ДО удаления списка поштучно» — строка `members` группового
        эндпоинта несёт все семь полей, включая конфликт и `routed_by`, а не
        только `position_item_id` (карточка сама этот список больше не
        строит, спека §2.8 п. 5)."""
        (other_category_id,) = _leaf_category_ids(db_session, 1)
        estimate = factories.EstimateFactory.create()
        # id лота обязан отличаться от id сметы: при совпадении (на свежей базе
        # последовательности идут вровень) `Proposal.lot_id` вместо
        # `Lot.estimate_id` прошёл бы сверку `estimate_id` ниже. Эта защита
        # стояла в удалённом тесте `members[]` карточки и переехала сюда
        # вместе с самой строкой членства.
        lot = factories.LotFactory.create(estimate=estimate)
        if lot.id == estimate.id:
            lot = factories.LotFactory.create(estimate=estimate)
        assert lot.id != estimate.id
        proposal = factories.ProposalFactory.create(lot=lot)
        cp = factories.CatalogPositionFactory.create(standard_job_title="Каталожное имя строки")
        bucket = _bucket(db_session, catalog_position=cp)
        ctx = _context(db_session, bucket)
        other_bucket = _bucket(db_session, catalog_position=cp, work_category_id=other_category_id)
        other_ctx = _context(db_session, other_bucket, is_default=False)
        pos = _position(factories, proposal, catalog_position=cp, title="Строка группы по смете")
        _member(
            db_session, pos, ctx,
            membership_state=MembershipState.STALE.value,
            conflict_at=_now(), conflict_from_context_id=other_ctx.id,
            routed_by=RoutedBy.manual.value,
        )
        db_session.flush()

        page = admin_client.get(f"{BASE}/contexts/{ctx.id}/members").json()
        row = page["items"][0]
        assert set(row) == {
            "position_item_id", "job_title", "estimate_id", "membership_state",
            "conflict_at", "conflict_from_context_id", "routed_by",
        }
        assert row["routed_by"] == RoutedBy.manual.value
        assert row["estimate_id"] == estimate.id
        # Название ПО СМЕТЕ (`job_title_in_proposal`), не каталожное имя —
        # они могут расходиться (утверждение удалённого теста `members[]`
        # карточки, переехавшее со строкой членства).
        assert row["job_title"] == "Строка группы по смете"
        assert row["position_item_id"] == pos.id
        assert row["membership_state"] == MembershipState.STALE.value
        assert row["conflict_from_context_id"] == other_ctx.id


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

    @pytest.mark.parametrize("reason", ["stale_accepted", "review_merge"])
    def test_move_members_rejects_reasons_reserved_for_other_operations(
        self, admin_client, db_session, factories, reason
    ):
        """`stale_accepted`/`review_merge` — причины, которые в журнал пишут
        `accept_transfer` и слияние в Review соответственно, не ручной
        перенос: клиент этого маршрута не вправе подписать ими членство,
        даже хотя обе строки входят в общий алфавит `members_moved.reason`."""
        cp = factories.CatalogPositionFactory.create()
        bucket = _bucket(db_session, catalog_position=cp)
        target = _context(db_session, bucket)
        proposal = _proposal(factories)
        pos = _position(factories, proposal, catalog_position=cp)
        _member(db_session, pos, target)

        response = admin_client.post(
            f"{BASE}/members/move",
            json={"position_item_ids": [pos.id], "target_context_id": target.id, "reason": reason},
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


# ---------------------------------------------------------------------------
#  Пакетный перенос устаревшей группы (спека §2.6, §2.8 п. 4)
# ---------------------------------------------------------------------------

class TestStaleGroupTransfer:
    def _stale_group_scene(self, db_session, factories, *, cat_target, cat_source, count=3):
        """`count` STALE-членств ОДНОЙ группы (общий раздел `chapter`, общий
        `catalog_position` — их целевая корзина «написание × статья» и,
        следовательно, целевой контекст СОВПАДУТ, как и требует утверждение
        плана «все три в одном целевом контексте»). Источник — контекст
        `bucket_b` (статья `cat_source`, устаревшая), эффективная статья
        раздела — `cat_target`."""
        cp = factories.CatalogPositionFactory.create()
        proposal = _proposal(factories)
        chapter = _chapter(
            factories, proposal, title="ГруппаПереноса",
            category_id=cat_target, category_source="file",
        )
        source_bucket = _bucket(db_session, catalog_position=cp, work_category_id=cat_source)
        source_ctx = _context(db_session, source_bucket)
        positions = [
            _position(factories, proposal, chapter=chapter, catalog_position=cp)
            for _ in range(count)
        ]
        for pos in positions:
            _member(db_session, pos, source_ctx, membership_state=MembershipState.STALE.value)
        db_session.commit()
        return chapter, source_ctx, positions

    def test_moves_all_into_one_context_ordered_and_writes_three_events(
        self, admin_client, db_session, factories
    ):
        """Утверждение плана: группа из трёх устаревших позиций с общей
        статьёй раздела — `moved=3`, `refused=0`, все три в ОДНОМ целевом
        контексте, обход и `results` — по возрастанию `position_item_id`, и
        РОВНО три события `members_moved` (считано по целевому контексту —
        не глобальным счётчиком, спека `verifying-guards.md`: посторонние
        события того же типа в базе не должны попасть в счёт)."""
        cat_target, cat_source = _leaf_category_ids(db_session, 2)
        chapter, source_ctx, positions = self._stale_group_scene(
            db_session, factories, cat_target=cat_target, cat_source=cat_source,
        )
        ordered_ids = sorted(pos.id for pos in positions)
        # Корзина цели и её контекст по умолчанию рождаются ОДНОЙ операцией,
        # и при выровненных последовательностях их id совпадают — тогда
        # `target_context_id`, по ошибке прочитанный из `bucket_id`, был бы
        # неотличим от верного. Разводим последовательности (они вне
        # транзакций, откат теста их не вернёт — и не должен).
        db_session.execute(sa.text(
            "SELECT setval(pg_get_serial_sequence('catalog_contexts', 'id'), "
            "GREATEST(nextval(pg_get_serial_sequence('catalog_contexts', 'id')), "
            "nextval(pg_get_serial_sequence('context_buckets', 'id'))) + 1000)"
        ))

        response = admin_client.post(
            f"{BASE}/contexts/{source_ctx.id}/stale-groups/transfer",
            json={"chapter_item_id": chapter.id, "expected_category_id": cat_target},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["moved"] == 3
        assert body["refused"] == 0
        results = body["results"]
        assert [r["position_item_id"] for r in results] == ordered_ids
        assert all(r["outcome"] == "moved" for r in results)
        assert all(r["error_code"] is None and r["message"] is None for r in results)
        target_context_ids = {r["target_context_id"] for r in results}
        assert len(target_context_ids) == 1
        (target_context_id,) = target_context_ids
        assert target_context_id != source_ctx.id

        db_session.expire_all()
        members = db_session.execute(
            sa.select(ContextMember).where(ContextMember.position_item_id.in_(ordered_ids))
        ).scalars().all()
        assert {m.context_id for m in members} == {target_context_id}
        assert {m.membership_state for m in members} == {MembershipState.CURRENT.value}

        events_count = db_session.execute(
            sa.select(sa.func.count())
            .select_from(SemanticEvent)
            .where(
                SemanticEvent.context_id == target_context_id,
                SemanticEvent.event_type == "members_moved",
            )
        ).scalar_one()
        assert events_count == 3

    def test_one_forced_refusal_does_not_roll_back_the_other_two(
        self, admin_client, db_session, factories, monkeypatch
    ):
        """Ключевое утверждение плана: отказ ОДНОЙ позиции пакета не
        откатывает две другие — и откатывает ВСЁ, что успела записать сама
        отказавшая позиция (её точка сохранения).

        Буквальный сценарий плана — «у одной из трёх статья раздела сменилась
        после показа строки внимания» — однопоточно НЕДОСТИЖИМ: группа
        определена ОБЩИМ ближайшим разделом (`chapter_item_id`), а эффективная
        статья позиции — это статья ИМЕННО её ближайшего раздела
        (`chapter_context`, `services/context_routing.py`); у всех членств
        одной группы раздел общий, значит и эффективная статья общая — им
        неоткуда разойтись. Реальный отказ ровно одной позиции достижим только
        конкурентной сессией — см.
        `test_concurrent_target_bucket_birth_refuses_exactly_one_via_real_path`.

        Здесь подмена `accept_transfer` для СРЕДНЕЙ позиции сначала выполняет
        НАСТОЯЩИЙ перенос (членство, событие `members_moved` записаны и
        сброшены в базу), а затем отказывает доменной ошибкой. Ни одна
        сегодняшняя ветка отказа `accept_transfer` не пишет до `raise`, поэтому
        без записи перед отказом точка сохранения была бы неотличима от её
        отсутствия (`try/except` без `begin_nested` дал бы тот же итог); с
        записью — без точки сохранения отказавшая позиция осталась бы
        перенесённой, и событий в цели было бы три."""
        cat_target, cat_source = _leaf_category_ids(db_session, 2)
        chapter, source_ctx, positions = self._stale_group_scene(
            db_session, factories, cat_target=cat_target, cat_source=cat_source,
        )
        ordered_ids = sorted(pos.id for pos in positions)
        refused_id = ordered_ids[1]

        real_accept_transfer = context_operations.accept_transfer

        def _fake_accept_transfer(db, *, position_item_id, expected_category_id, actor_id):
            if position_item_id == refused_id:
                real_accept_transfer(
                    db, position_item_id=position_item_id,
                    expected_category_id=expected_category_id, actor_id=actor_id,
                )
                raise context_operations.ContextOperationError(
                    "test_forced_refusal", "принудительный отказ для проверки пакета",
                    position_item_id=position_item_id,
                )
            return real_accept_transfer(
                db, position_item_id=position_item_id,
                expected_category_id=expected_category_id, actor_id=actor_id,
            )

        monkeypatch.setattr(context_operations, "accept_transfer", _fake_accept_transfer)

        response = admin_client.post(
            f"{BASE}/contexts/{source_ctx.id}/stale-groups/transfer",
            json={"chapter_item_id": chapter.id, "expected_category_id": cat_target},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["moved"] == 2
        assert body["refused"] == 1
        results_by_id = {r["position_item_id"]: r for r in body["results"]}
        assert results_by_id[refused_id]["outcome"] == "refused"
        assert results_by_id[refused_id]["error_code"] == "test_forced_refusal"
        assert results_by_id[refused_id]["message"] == "принудительный отказ для проверки пакета"
        assert results_by_id[refused_id]["target_context_id"] is None
        moved_ids = [pid for pid in ordered_ids if pid != refused_id]
        for pid in moved_ids:
            assert results_by_id[pid]["outcome"] == "moved"
            assert results_by_id[pid]["error_code"] is None
            assert results_by_id[pid]["message"] is None
        moved_target_ids = {results_by_id[pid]["target_context_id"] for pid in moved_ids}
        assert len(moved_target_ids) == 1
        (target_context_id,) = moved_target_ids
        assert target_context_id is not None and target_context_id != source_ctx.id

        # Состояние — СВЕЖИМ чтением той же сессии (admin_client и db_session —
        # одна транзакционная сессия теста, `commit` маршрута здесь лишь
        # отпускает точку сохранения внешней транзакции теста). Это доказывает
        # «не откатано отказом соседа», но НЕ «закоммичено»: переживание
        # настоящего commit'а проверяет отдельной сессией
        # `test_concurrent_target_bucket_birth_refuses_exactly_one_via_real_path`.
        db_session.expire_all()
        refused_member = db_session.execute(
            sa.select(ContextMember).where(ContextMember.position_item_id == refused_id)
        ).scalar_one()
        assert refused_member.context_id == source_ctx.id
        assert refused_member.membership_state == MembershipState.STALE.value

        moved_members = db_session.execute(
            sa.select(ContextMember).where(ContextMember.position_item_id.in_(moved_ids))
        ).scalars().all()
        assert len(moved_members) == 2
        assert all(m.membership_state == MembershipState.CURRENT.value for m in moved_members)
        assert {m.context_id for m in moved_members} == {target_context_id}

        # Событие отказавшей позиции (записанное подменой ДО отказа) откатано
        # вместе с её точкой сохранения: в цели ровно два `members_moved`.
        events_count = db_session.execute(
            sa.select(sa.func.count())
            .select_from(SemanticEvent)
            .where(
                SemanticEvent.context_id == target_context_id,
                SemanticEvent.event_type == "members_moved",
            )
        ).scalar_one()
        assert events_count == 2

    def test_concurrent_target_bucket_birth_refuses_exactly_one_via_real_path(
        self, committing_client, committing_db, committing_factories,
        committing_session_factory, monkeypatch,
    ):
        """Утверждение плана «отказ ОДНОЙ из трёх — `moved=2`, `refused=1`,
        отказ несёт код и текст доменной ошибки `accept_transfer`, две другие
        перенесены и закоммичены» на НАСТОЯЩЕМ `accept_transfer`, без подмены
        самого отказа.

        Однопоточно отказ ровно одной позиции группы недостижим (общий
        ближайший раздел — общая эффективная статья, докстрока
        `test_one_forced_refusal_does_not_roll_back_the_other_two`). Достижим
        он конкурентной сессией: целевой корзины ещё нет, первая позиция
        читает кандидата цели (`None`), и МЕЖДУ этим чтением и её блокировкой
        другая сессия рождает корзину цели и коммитит — ветка «целевая
        корзина не входит в запертый набор» `accept_transfer` отказывает
        `REFUSE_CATEGORY_CHANGED` именно первой позиции; вторая и третья
        находят уже закоммиченную корзину, запирают её и переносятся в её
        контекст по умолчанию. Конкурентная сессия вставлена подменой
        `lock_buckets` ПЕРЕД настоящей блокировкой — тот же приём, что
        `TestAcceptTransferRelocksWhenTargetBucketAppearsBetweenReadAndLock`
        (`test_context_cascade.py`).

        `committing_client` — настоящий `commit` маршрута; итог читается
        ОТДЕЛЬНОЙ сессией, то есть доказывает «закоммичено», а не «видно в
        той же транзакции»."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from auth import get_current_user
        from main import app
        from models import UserRole
        from services.context_routing import route_position

        admin = committing_factories.UserFactory.create(role=UserRole.admin)
        committing_db.commit()
        admin_id = admin.id

        def _real_admin():
            # Не ORM-объект чужой сессии: маршрут исполняется в другом потоке.
            user = MagicMock()
            user.id = admin_id
            user.role = UserRole.admin
            user.is_active = True
            return user

        app.dependency_overrides[get_current_user] = _real_admin

        cat_target, cat_source = _leaf_category_ids(committing_db, 2)
        chapter, source_ctx, positions = self._stale_group_scene(
            committing_db, committing_factories, cat_target=cat_target, cat_source=cat_source,
        )
        ordered_ids = sorted(pos.id for pos in positions)
        catalog_position = SimpleNamespace(id=positions[0].catalog_position_id)
        source_ctx_id = source_ctx.id
        chapter_id = chapter.id

        # Донор той же строки каталога под разделом со статьёй `cat_target`:
        # его маршрутизация другой сессией и рождает корзину цели.
        donor_proposal = _proposal(committing_factories)
        donor_chapter = _chapter(
            committing_factories, donor_proposal, title="Раздел-донор",
            category_id=cat_target, category_source="file",
        )
        donor_position = _position(
            committing_factories, donor_proposal, chapter=donor_chapter,
            catalog_position=catalog_position,
        )
        donor_position_id = donor_position.id
        committing_db.commit()

        real_lock_buckets = context_operations.lock_buckets
        lock_calls = {"n": 0}

        def _lock_after_concurrent_bucket_birth(db, bucket_ids, *, exclusive):
            lock_calls["n"] += 1
            if lock_calls["n"] == 1:
                with committing_session_factory() as other:
                    route_position(other, position_item_id=donor_position_id)
                    other.commit()
            return real_lock_buckets(db, bucket_ids, exclusive=exclusive)

        monkeypatch.setattr(context_operations, "lock_buckets", _lock_after_concurrent_bucket_birth)

        response = committing_client.post(
            f"{BASE}/contexts/{source_ctx_id}/stale-groups/transfer",
            json={"chapter_item_id": chapter_id, "expected_category_id": cat_target},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["moved"] == 2
        assert body["refused"] == 1
        results = body["results"]
        assert [r["position_item_id"] for r in results] == ordered_ids

        refused = results[0]
        assert refused["outcome"] == "refused"
        assert refused["error_code"] == context_operations.REFUSE_CATEGORY_CHANGED
        assert refused["message"] == (
            f"целевая корзина позиции {ordered_ids[0]} изменилась между чтением и "
            "блокировкой (гонка на создании корзины)"
        )
        assert refused["target_context_id"] is None

        probe = committing_session_factory()
        try:
            donor_context_id = probe.execute(
                sa.select(ContextMember.context_id).where(
                    ContextMember.position_item_id == donor_position_id
                )
            ).scalar_one()
            for moved in results[1:]:
                assert moved["outcome"] == "moved"
                assert moved["error_code"] is None and moved["message"] is None
                assert moved["target_context_id"] == donor_context_id

            members = {
                m.position_item_id: m
                for m in probe.execute(
                    sa.select(ContextMember).where(ContextMember.position_item_id.in_(ordered_ids))
                ).scalars()
            }
            assert members[ordered_ids[0]].context_id == source_ctx_id
            assert members[ordered_ids[0]].membership_state == MembershipState.STALE.value
            for pid in ordered_ids[1:]:
                assert members[pid].context_id == donor_context_id
                assert members[pid].membership_state == MembershipState.CURRENT.value

            events_count = probe.execute(
                sa.select(sa.func.count())
                .select_from(SemanticEvent)
                .where(
                    SemanticEvent.context_id == donor_context_id,
                    SemanticEvent.event_type == "members_moved",
                )
            ).scalar_one()
            assert events_count == 2
        finally:
            probe.close()

    def test_wrong_expected_category_refuses_whole_group_via_real_path(
        self, admin_client, db_session, factories
    ):
        """Реальный (не подменённый) путь `REFUSE_CATEGORY_CHANGED`
        `accept_transfer`: `expected_category_id`, не совпадающий со свежей
        эффективной статьёй ВСЕЙ группы (она у группы одна — см. докстроку
        предыдущего теста), отказывает КАЖДОЙ позиции группы —
        `moved=0`, `refused=3`, ответ всё равно `200` (частичный/полный
        отказ пакета — не HTTP-ошибка)."""
        cat_target, cat_source = _leaf_category_ids(db_session, 2)
        chapter, source_ctx, positions = self._stale_group_scene(
            db_session, factories, cat_target=cat_target, cat_source=cat_source,
        )
        ordered_ids = sorted(pos.id for pos in positions)

        response = admin_client.post(
            f"{BASE}/contexts/{source_ctx.id}/stale-groups/transfer",
            json={"chapter_item_id": chapter.id, "expected_category_id": cat_source},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["moved"] == 0
        assert body["refused"] == 3
        results = body["results"]
        assert [r["position_item_id"] for r in results] == ordered_ids
        assert all(r["outcome"] == "refused" for r in results)
        assert all(
            r["error_code"] == context_operations.REFUSE_CATEGORY_CHANGED for r in results
        )

        db_session.expire_all()
        members = db_session.execute(
            sa.select(ContextMember).where(ContextMember.position_item_id.in_(ordered_ids))
        ).scalars().all()
        assert {m.context_id for m in members} == {source_ctx.id}
        assert {m.membership_state for m in members} == {MembershipState.STALE.value}

    def test_group_with_no_stale_members_returns_empty(self, admin_client, db_session, factories):
        (cat_target,) = _leaf_category_ids(db_session, 1)
        cp = factories.CatalogPositionFactory.create()
        proposal = _proposal(factories)
        chapter = _chapter(
            factories, proposal, title="БезУстаревших",
            category_id=cat_target, category_source="file",
        )
        bucket = _bucket(db_session, catalog_position=cp, work_category_id=cat_target)
        ctx = _context(db_session, bucket)
        pos = _position(factories, proposal, chapter=chapter, catalog_position=cp)
        _member(db_session, pos, ctx, membership_state=MembershipState.CURRENT.value)
        db_session.commit()

        response = admin_client.post(
            f"{BASE}/contexts/{ctx.id}/stale-groups/transfer",
            json={"chapter_item_id": chapter.id, "expected_category_id": cat_target},
        )
        assert response.status_code == 200
        assert response.json() == {"results": [], "moved": 0, "refused": 0}

    def test_null_chapter_moves_only_positions_without_chapter(
        self, admin_client, db_session, factories
    ):
        cat_target, cat_source = _leaf_category_ids(db_session, 2)
        cp = factories.CatalogPositionFactory.create()
        proposal = _proposal(factories)
        source_bucket = _bucket(db_session, catalog_position=cp, work_category_id=cat_source)
        source_ctx = _context(db_session, source_bucket)

        # Устаревшая позиция БЕЗ раздела вовсе — эффективная статья `None`
        # (пустая цепочка, `chapter_context`).
        no_chapter_pos = _position(factories, proposal, catalog_position=cp)
        _member(db_session, no_chapter_pos, source_ctx, membership_state=MembershipState.STALE.value)

        # Устаревшая позиция С разделом — та же корзина-источник, другая
        # группа; трогать её нельзя.
        chapter = _chapter(
            factories, proposal, title="СРазделом",
            category_id=cat_target, category_source="file",
        )
        with_chapter_pos = _position(factories, proposal, chapter=chapter, catalog_position=cp)
        _member(db_session, with_chapter_pos, source_ctx, membership_state=MembershipState.STALE.value)

        db_session.commit()

        response = admin_client.post(
            f"{BASE}/contexts/{source_ctx.id}/stale-groups/transfer",
            json={"chapter_item_id": None, "expected_category_id": None},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["moved"] == 1
        assert body["refused"] == 0
        assert [r["position_item_id"] for r in body["results"]] == [no_chapter_pos.id]

        db_session.expire_all()
        with_chapter_member = db_session.execute(
            sa.select(ContextMember).where(ContextMember.position_item_id == with_chapter_pos.id)
        ).scalar_one()
        assert with_chapter_member.context_id == source_ctx.id
        assert with_chapter_member.membership_state == MembershipState.STALE.value

    def test_current_and_conflicted_members_of_same_group_are_untouched(
        self, admin_client, db_session, factories
    ):
        cat_target, cat_source = _leaf_category_ids(db_session, 2)
        cp = factories.CatalogPositionFactory.create()
        proposal = _proposal(factories)
        chapter = _chapter(
            factories, proposal, title="СмешаннаяГруппа",
            category_id=cat_target, category_source="file",
        )
        source_bucket = _bucket(db_session, catalog_position=cp, work_category_id=cat_source)
        source_ctx = _context(db_session, source_bucket)
        other_ctx = _context(db_session, source_bucket, is_default=False)

        stale_pos = _position(factories, proposal, chapter=chapter, catalog_position=cp)
        _member(db_session, stale_pos, source_ctx, membership_state=MembershipState.STALE.value)

        current_pos = _position(factories, proposal, chapter=chapter, catalog_position=cp)
        _member(db_session, current_pos, source_ctx, membership_state=MembershipState.CURRENT.value)

        conflicted_pos = _position(factories, proposal, chapter=chapter, catalog_position=cp)
        _member(
            db_session, conflicted_pos, source_ctx,
            membership_state=MembershipState.STALE.value,
            conflict_at=_now(), conflict_from_context_id=other_ctx.id,
        )

        db_session.commit()

        response = admin_client.post(
            f"{BASE}/contexts/{source_ctx.id}/stale-groups/transfer",
            json={"chapter_item_id": chapter.id, "expected_category_id": cat_target},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["moved"] == 1
        assert body["refused"] == 0
        assert [r["position_item_id"] for r in body["results"]] == [stale_pos.id]

        db_session.expire_all()
        current_member = db_session.execute(
            sa.select(ContextMember).where(ContextMember.position_item_id == current_pos.id)
        ).scalar_one()
        assert current_member.context_id == source_ctx.id
        assert current_member.membership_state == MembershipState.CURRENT.value

        conflicted_member = db_session.execute(
            sa.select(ContextMember).where(ContextMember.position_item_id == conflicted_pos.id)
        ).scalar_one()
        assert conflicted_member.context_id == source_ctx.id
        assert conflicted_member.conflict_at is not None

    def test_chapter_group_leaves_other_groups_and_other_contexts_untouched(
        self, admin_client, db_session, factories
    ):
        """Группа — пара «ЭТОТ контекст × ЭТОТ ближайший раздел»: устаревшие
        членства того же контекста под ДРУГИМ разделом и без раздела, и
        устаревшее членство ДРУГОГО контекста под тем же разделом (другая
        строка каталога той же сметы — обычный случай: раздел «Отделка» несёт
        разные работы, каждая в своём контексте), в пакет не входят. У
        раздела H та же статья, что у G, — попади он в пакет, его позиция
        переехала бы, а не отказала бы, и счёт разошёлся бы."""
        cat_target, cat_source = _leaf_category_ids(db_session, 2)
        cp = factories.CatalogPositionFactory.create()
        cp_other = factories.CatalogPositionFactory.create()
        proposal = _proposal(factories)
        chapter_g = _chapter(
            factories, proposal, title="ГруппаG", category_id=cat_target, category_source="file",
        )
        chapter_h = _chapter(
            factories, proposal, title="ГруппаH", category_id=cat_target, category_source="file",
        )
        source_ctx = _context(
            db_session, _bucket(db_session, catalog_position=cp, work_category_id=cat_source)
        )
        other_ctx = _context(
            db_session, _bucket(db_session, catalog_position=cp_other, work_category_id=cat_source)
        )

        in_group = _position(factories, proposal, chapter=chapter_g, catalog_position=cp)
        other_chapter = _position(factories, proposal, chapter=chapter_h, catalog_position=cp)
        no_chapter = _position(factories, proposal, catalog_position=cp)
        other_context = _position(factories, proposal, chapter=chapter_g, catalog_position=cp_other)
        for pos, ctx in (
            (in_group, source_ctx), (other_chapter, source_ctx),
            (no_chapter, source_ctx), (other_context, other_ctx),
        ):
            _member(db_session, pos, ctx, membership_state=MembershipState.STALE.value)
        db_session.commit()

        response = admin_client.post(
            f"{BASE}/contexts/{source_ctx.id}/stale-groups/transfer",
            json={"chapter_item_id": chapter_g.id, "expected_category_id": cat_target},
        )
        assert response.status_code == 200
        body = response.json()
        assert [r["position_item_id"] for r in body["results"]] == [in_group.id]
        assert body["moved"] == 1
        assert body["refused"] == 0

        db_session.expire_all()
        untouched = {
            m.position_item_id: m
            for m in db_session.execute(
                sa.select(ContextMember).where(
                    ContextMember.position_item_id.in_(
                        [other_chapter.id, no_chapter.id, other_context.id]
                    )
                )
            ).scalars()
        }
        assert untouched[other_chapter.id].context_id == source_ctx.id
        assert untouched[no_chapter.id].context_id == source_ctx.id
        assert untouched[other_context.id].context_id == other_ctx.id
        assert {m.membership_state for m in untouched.values()} == {MembershipState.STALE.value}

    def test_chapter_cycle_is_not_swallowed_as_a_per_position_refusal(
        self, admin_client, db_session, factories
    ):
        """Перехватывается ТОЛЬКО доменный отказ `ContextOperationError`
        (докстрока `transfer_stale_group`): цикл разделов группы —
        `RoutingError` `chapter_context` — не становится «отказом позиции» в
        `results` с `200`, а проходит к `_mutating` и даёт доменную `422` с
        откатом всей транзакции маршрута."""
        cat_source = _leaf_category_ids(db_session, 1)[0]
        cp = factories.CatalogPositionFactory.create()
        proposal = _proposal(factories)
        source_ctx = _context(
            db_session, _bucket(db_session, catalog_position=cp, work_category_id=cat_source)
        )
        chapter_a = _chapter(factories, proposal, title="Циклический А")
        chapter_b = _chapter(factories, proposal, title="Циклический Б", parent=chapter_a)
        db_session.flush()
        chapter_a.chapter_item_id = chapter_b.id
        db_session.flush()
        pos = _position(factories, proposal, chapter=chapter_a, catalog_position=cp)
        _member(db_session, pos, source_ctx, membership_state=MembershipState.STALE.value)
        db_session.commit()

        response = admin_client.post(
            f"{BASE}/contexts/{source_ctx.id}/stale-groups/transfer",
            json={"chapter_item_id": chapter_a.id, "expected_category_id": None},
        )
        assert response.status_code == 422

        db_session.expire_all()
        member = db_session.execute(
            sa.select(ContextMember).where(ContextMember.position_item_id == pos.id)
        ).scalar_one()
        assert member.context_id == source_ctx.id
        assert member.membership_state == MembershipState.STALE.value

    @pytest.mark.parametrize(
        "body",
        [{"expected_category_id": None}, {"chapter_item_id": None}],
        ids=["no_chapter_item_id", "no_expected_category_id"],
    )
    def test_both_body_fields_are_required(self, admin_client, db_session, factories, body):
        """Оба поля тела обязательны (план, Interfaces: без умолчаний).
        Умолчание `None` у `chapter_item_id` молча превратило бы забытое поле в
        «группу без раздела» и перенесло бы ЧУЖУЮ группу; у
        `expected_category_id` — в ожидание «нет статьи»."""
        cat_source = _leaf_category_ids(db_session, 1)[0]
        cp = factories.CatalogPositionFactory.create()
        proposal = _proposal(factories)
        source_ctx = _context(
            db_session, _bucket(db_session, catalog_position=cp, work_category_id=cat_source)
        )
        pos = _position(factories, proposal, catalog_position=cp)
        _member(db_session, pos, source_ctx, membership_state=MembershipState.STALE.value)
        db_session.commit()

        response = admin_client.post(
            f"{BASE}/contexts/{source_ctx.id}/stale-groups/transfer", json=body,
        )
        assert response.status_code == 422

        db_session.expire_all()
        member = db_session.execute(
            sa.select(ContextMember).where(ContextMember.position_item_id == pos.id)
        ).scalar_one()
        assert member.context_id == source_ctx.id
        assert member.membership_state == MembershipState.STALE.value

    def test_member_forbidden(self, member_client):
        response = member_client.post(
            f"{BASE}/contexts/1/stale-groups/transfer",
            json={"chapter_item_id": None, "expected_category_id": None},
        )
        assert response.status_code == 403

    def test_missing_context_gives_404(self, admin_client):
        response = admin_client.post(
            f"{BASE}/contexts/999999999/stale-groups/transfer",
            json={"chapter_item_id": None, "expected_category_id": None},
        )
        assert response.status_code == 404
        # Доменный отказ сервиса, а не «маршрут не найден» FastAPI (тот тоже
        # `404`, и до появления маршрута этот тест был бы зелёным).
        assert response.json()["detail"]["code"] == context_operations.REFUSE_CONTEXT_NOT_FOUND
