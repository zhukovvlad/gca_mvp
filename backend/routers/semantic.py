"""API семантического контура — восемнадцать маршрутов под `/api/v1/semantic`,
все под правом `admin` (спека `2026-09-22-catalog-families-design.md` §2.7,
§2.10; план, задача 12).

HTTP-слой поверх готовых сервисов задач 4, 6-10 (`services/context_routing.py`,
`services/context_operations.py`, `services/work_families.py`) — они не
переписываются. Здесь: тела запросов/ответов, права, **управление
транзакцией** (образец — `routers/review.py`) и трансляция трёх доменных
исключений сервисов в HTTP через `raise_domain_error`
(`routers/domain_errors.py`).

**Права.** Каждый маршрут несёт СВОЙ `Depends(require_admin)` (не общий
роутерный `dependencies=`) — так проверка «`member` отвергнут на КАЖДОМ»
(план, задача 12, «Утверждения») ловит случайно забытый параметр на одном
конкретном маршруте, а не молчит из-за общей защиты роутера.

**Трансляция отказов.** `WorkFamilyError`/`ContextOperationError` несут
`code` и контекст именованными атрибутами (не `DomainError.context`-словарём)
— `_domain_error` строит из них `DomainError` статусом по `_STATUS_BY_CODE`
(404/409/422 — по образцу соседних доменных отказов, `routers/domain_errors.py`).
`RoutingError` (`services/context_routing.py`) кодов не несёт вовсе (задача 4
не заводит для неё `REFUSE_*`) — переводится в `422` НЕКОДИРОВАННЫМ текстом
(`DomainError.code=None`), тем же путём, каким `raise_domain_error` уже
обрабатывает отказы без кода (`code is None` → `detail` строкой).

**Транзакция.** `_mutating(db)` — контекстный менеджер одной транзакции на
маршрут: успешный выход коммитит, любое из трёх исключений сервисов
откатывает и транслирует в `HTTPException`, любое другое исключение
откатывает и пробрасывается дальше (та же дисциплина, что `except Exception:
db.rollback(); raise` в `routers/review.py`).
"""
from __future__ import annotations

import contextlib
import dataclasses
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import crud.semantic as crud_semantic
from auth import require_admin
from crud.common import DomainError
from database import get_db
from models import NameRole, SemanticKind, SemanticState, User
from routers.domain_errors import raise_domain_error
from services import context_operations, work_families
from services.context_operations import ContextOperationError
from services.context_routing import RoutingError
from services.work_families import WorkFamilyError

router = APIRouter(prefix="/api/v1/semantic", tags=["semantic"])

#: Потолок страницы `list_contexts` — тот же порядок величины, что у соседних
#: очередей (`routers/review.py::MAX_BATCH_SIZE`, `crud/common.py::MAX_PAGE_SIZE`).
MAX_PAGE_SIZE = 100

# ---------------------------------------------------------------------------
#  Статусы доменных отказов — по образцу соседних (404/409/422,
#  `routers/domain_errors.py`). Решение исполнителя (план, задача 12,
#  Interfaces не называет карту): «не найдено» → 404; «ресурс существует, но
#  не в нужном для операции состоянии» (архивирован/не активен/не draft/с
#  привязками/без конфликта/устарело/статья успела измениться) → 409;
#  «вход структурно не годится» (совпадение источника и цели, разные
#  корзины, неизвестная единица/вид/роль, пустой или битый список id) → 422.
# ---------------------------------------------------------------------------
_STATUS_NOT_FOUND = frozenset(
    {
        work_families.REFUSE_FAMILY_NOT_FOUND,
        work_families.REFUSE_CONTEXT_NOT_FOUND,
        context_operations.REFUSE_CONTEXT_NOT_FOUND,
    }
)

_STATUS_CONFLICT = frozenset(
    {
        work_families.REFUSE_UPDATE_ARCHIVED,
        work_families.REFUSE_ACTIVATE_NOT_DRAFT,
        work_families.REFUSE_ACTIVATE_WITHOUT_DEFINITION,
        work_families.REFUSE_UNIT_MISMATCH,
        work_families.REFUSE_FAMILY_NOT_ACTIVE,
        work_families.REFUSE_UNIT_CHANGE_WITH_LINKS,
        work_families.REFUSE_ARCHIVE_WITH_LINKS,
        work_families.REFUSE_MERGE_UNIT_MISMATCH,
        work_families.REFUSE_MERGE_INACTIVE,
        work_families.REFUSE_CONTEXT_NOT_APPLICABLE,
        context_operations.REFUSE_CONTEXT_NOT_EMPTY,
        context_operations.REFUSE_INCOMING_RULES,
        context_operations.REFUSE_DEFAULT_WITHOUT_SUCCESSOR,
        context_operations.REFUSE_CONTEXT_ARCHIVED,
        context_operations.REFUSE_RULE_DOES_NOT_COVER,
        context_operations.REFUSE_NOT_CONFLICTED,
        context_operations.REFUSE_CATEGORY_CHANGED,
        context_operations.REFUSE_NOT_STALE,
    }
)

_STATUS_UNPROCESSABLE = frozenset(
    {
        work_families.REFUSE_UNKNOWN_UNIT,
        work_families.REFUSE_INVALID_KIND,
        work_families.REFUSE_INVALID_NAME_ROLE,
        work_families.REFUSE_MERGE_SAME_FAMILY,
        work_families.REFUSE_BLANK_TITLE,
        context_operations.REFUSE_DIFFERENT_BUCKET,
        context_operations.REFUSE_INVALID_MEMBERSHIP,
        context_operations.REFUSE_INVALID_NEW_DEFAULT,
        context_operations.REFUSE_SAME_CONTEXT,
        context_operations.REFUSE_INVALID_REASON,
    }
)


def _status_for_code(code: str) -> int:
    if code in _STATUS_NOT_FOUND:
        return status.HTTP_404_NOT_FOUND
    if code in _STATUS_CONFLICT:
        return status.HTTP_409_CONFLICT
    if code in _STATUS_UNPROCESSABLE:
        return status.HTTP_422_UNPROCESSABLE_CONTENT
    # Не молчаливый 500: неизвестный код — это код, который завели в сервисе
    # и забыли занести в одну из трёх карт выше, дефект ЭТОГО модуля.
    raise AssertionError(f"нет HTTP-статуса для кода отказа {code!r}")


def _json_safe(value: object) -> object:
    """Приводит значение контекста отказа к JSON-совместимому виду:
    `accept_transfer` кладёт в контекст `new_proposal`
    — `TransferProposal`, обычный `@dataclass`, не сериализуемый штатным
    JSON-кодером FastAPI/Starlette (`TypeError: Object of type
    TransferProposal is not JSON serializable`). Рекурсивно разворачивает dataclass-ы (включая вложенные и внутри
    списков/словарей) в обычные `dict`/`list` — а не точечно зовёт
    `_serialize_transfer_proposal` только для одного известного поля: любой
    БУДУЩИЙ контекст отказа, кладущий dataclass, ловится тем же путём, а не
    новым падением в проде."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _json_safe(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


def _domain_error(exc: WorkFamilyError | ContextOperationError) -> DomainError:
    """`WorkFamilyError`/`ContextOperationError` несут `code` и контекст
    именованными атрибутами (`**context` конструктора, не словарём) —
    `vars(exc)` минус `code` и есть контекст отказа. Каждое значение
    проходит через `_json_safe` (см. её докстринг) — контекст
    обязан долетать до `HTTPException` сериализуемым, а не падать в
    Starlette дальше по цепочке."""
    context = {
        key: _json_safe(value) for key, value in vars(exc).items() if key != "code"
    }
    return DomainError(_status_for_code(exc.code), str(exc), code=exc.code, context=context)


@contextlib.contextmanager
def _mutating(db: Session):
    """Одна транзакция на маршрут:
    успех коммитит, отказ сервиса откатывает и транслирует в `HTTPException`
    через `raise_domain_error`, любое другое исключение откатывает и летит
    дальше — тот же протокол, что `routers/review.py`."""
    try:
        yield
    except (WorkFamilyError, ContextOperationError) as exc:
        db.rollback()
        raise_domain_error(_domain_error(exc))
    except RoutingError as exc:
        db.rollback()
        # Без кода — тем же путём, что и `raise_domain_error` уже обрабатывает
        # отказы без кода: `detail` НЕКОДИРОВАННЫМ текстом (см. докстринг
        # модуля).
        raise_domain_error(DomainError(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)))
    except Exception:
        db.rollback()
        raise
    else:
        db.commit()


def _read_domain_errors(fn, /, *args, **kwargs):
    """Обёртка ЧТЕНИЯ (не пишет, коммит/rollback не нужны): переводит те же
    три исключения в `HTTPException` — используется маршрутом
    `transfer-proposal`, единственным GET, которому есть что переводить."""
    try:
        return fn(*args, **kwargs)
    except (WorkFamilyError, ContextOperationError) as exc:
        raise_domain_error(_domain_error(exc))
    except RoutingError as exc:
        raise_domain_error(DomainError(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)))


# ---------------------------------------------------------------------------
#  Сериализация
# ---------------------------------------------------------------------------

def _serialize_family(family) -> dict:
    return {
        "id": family.id,
        "title": family.title,
        "unit_id": family.unit_id,
        "definition": family.definition,
        "status": family.status,
        "seed_key": family.seed_key,
        "created_by": family.created_by,
        "created_at": family.created_at,
        "updated_at": family.updated_at,
        "activated_by": family.activated_by,
        "activated_at": family.activated_at,
        "archived_at": family.archived_at,
    }


def _serialize_transfer_proposal(proposal) -> dict | None:
    if proposal is None:
        return None
    return {
        "position_item_id": proposal.position_item_id,
        "current_context_id": proposal.current_context_id,
        "proposed_bucket_id": proposal.proposed_bucket_id,
        "proposed_context_id": proposal.proposed_context_id,
        "effective_category_id": proposal.effective_category_id,
    }


# ---------------------------------------------------------------------------
#  Тела запросов
# ---------------------------------------------------------------------------

class CreateFamilyRequest(BaseModel):
    title: str = Field(min_length=1)
    unit_name: str | None = None
    definition: str | None = None


class UpdateFamilyRequest(BaseModel):
    """`unit_name`: спека §2.10
    описывает правку единицы семьи как операцию экрана («недоступна, пока
    привязки есть, и подпись называет их число»), а Task 12 не заводила ей
    отдельного маршрута из восемнадцати — эта задача не меняет их множество
    (план, задача 12, «Утверждения»: набор путей проверяется целиком), так
    правка единицы едет ЭТИМ же `PATCH`. `unit_name` отсутствует в теле и
    `unit_name` присутствует со значением `null` — РАЗНЫЕ входы (`не
    трогать` vs `снять единицу`); маршрут различает их через
    `model_fields_set`, не через `unit_name is None`."""

    title: str | None = None
    definition: str | None = None
    unit_name: str | None = None


class MergeFamilyRequest(BaseModel):
    target_family_id: int


class ConfirmKindRequest(BaseModel):
    kind: SemanticKind | None = None


class SetNameRoleRequest(BaseModel):
    role: NameRole


class AssignFamilyRequest(BaseModel):
    family_id: int | None = None


class SplitRulePredicate(BaseModel):
    """Форма `context_routing_rules.predicate` (`services/context_routing.py`,
    `RulePredicate`) — `level` только у `chapter_level_equals`, форму и
    полноту проверяет сам сервис (`evaluate_predicate`, `RoutingError`)."""

    kind: str
    value: str
    level: int | None = None


class SplitContextRequest(BaseModel):
    position_item_ids: list[int] = Field(min_length=1)
    rule: SplitRulePredicate | None = None


class MergeContextRequest(BaseModel):
    target_context_id: int


class ArchiveContextRequest(BaseModel):
    new_default_context_id: int | None = None


class MoveMembersRequest(BaseModel):
    position_item_ids: list[int] = Field(min_length=1)
    target_context_id: int
    reason: str


class AcceptTransferRequest(BaseModel):
    expected_category_id: int | None = None


class AcceptTargetDecisionRequest(BaseModel):
    position_item_ids: list[int] = Field(min_length=1)


# ---------------------------------------------------------------------------
#  Семьи
# ---------------------------------------------------------------------------

@router.get("/families")
def list_families_route(
    status_: Literal["draft", "active", "archived"] | None = Query(default=None, alias="status"),
    unit_id: int | None = Query(default=None),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return {"items": crud_semantic.list_families(db, status=status_, unit_id=unit_id)}


@router.post("/families", status_code=status.HTTP_201_CREATED)
def create_family_route(
    body: CreateFamilyRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _mutating(db):
        family = work_families.create_family(
            db, title=body.title, unit_name=body.unit_name, definition=body.definition,
            actor_id=admin.id,
        )
    return _serialize_family(family)


@router.patch("/families/{family_id}")
def update_family_route(
    family_id: int,
    body: UpdateFamilyRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _mutating(db):
        family = work_families.update_family(
            db, family_id=family_id, title=body.title, definition=body.definition,
            actor_id=admin.id,
        )
        if "unit_name" in body.model_fields_set:
            family = work_families.set_unit(
                db, family_id=family_id, unit_name=body.unit_name, actor_id=admin.id
            )
    return _serialize_family(family)


@router.post("/families/{family_id}/activate")
def activate_family_route(
    family_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _mutating(db):
        family = work_families.activate_family(db, family_id=family_id, actor_id=admin.id)
    return _serialize_family(family)


@router.post("/families/{family_id}/archive")
def archive_family_route(
    family_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _mutating(db):
        family = work_families.archive_family(db, family_id=family_id, actor_id=admin.id)
    return _serialize_family(family)


@router.post("/families/{family_id}/merge")
def merge_families_route(
    family_id: int,
    body: MergeFamilyRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _mutating(db):
        moved = work_families.merge_families(
            db, source_family_id=family_id, target_family_id=body.target_family_id,
            actor_id=admin.id,
        )
    return {"source_family_id": family_id, "target_family_id": body.target_family_id, "moved_contexts": moved}


# ---------------------------------------------------------------------------
#  Контексты — очередь и карточка
# ---------------------------------------------------------------------------

@router.get("/contexts")
def list_contexts_route(
    catalog_query: str | None = Query(default=None),
    work_category_id: int | None = Query(default=None),
    semantic_kind: SemanticKind | None = Query(default=None),
    name_role: NameRole | None = Query(default=None),
    semantic_state: SemanticState | None = Query(default=None),
    has_stale_members: bool | None = Query(default=None),
    has_conflicting_members: bool | None = Query(default=None),
    has_no_members: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    filters = crud_semantic.ContextFilters(
        catalog_query=catalog_query,
        work_category_id=work_category_id,
        semantic_kind=semantic_kind.value if semantic_kind is not None else None,
        name_role=name_role.value if name_role is not None else None,
        semantic_state=semantic_state.value if semantic_state is not None else None,
        has_stale_members=has_stale_members,
        has_conflicting_members=has_conflicting_members,
        has_no_members=has_no_members,
    )
    return crud_semantic.list_contexts(db, filters=filters, limit=limit, offset=offset)


@router.get("/contexts/{context_id}")
def context_card_route(
    context_id: int,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    card = crud_semantic.context_card(db, context_id=context_id)
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Контекст {context_id} не найден.")
    return card


# ---------------------------------------------------------------------------
#  Контексты — операции
# ---------------------------------------------------------------------------

@router.post("/contexts/{context_id}/kind")
def confirm_kind_route(
    context_id: int,
    body: ConfirmKindRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _mutating(db):
        work_families.confirm_kind(
            db, context_id=context_id, kind=body.kind.value if body.kind is not None else None,
            actor_id=admin.id,
        )
    return crud_semantic.context_card(db, context_id=context_id)


@router.post("/contexts/{context_id}/name-role")
def set_name_role_route(
    context_id: int,
    body: SetNameRoleRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _mutating(db):
        work_families.set_name_role(
            db, context_id=context_id, role=body.role.value, actor_id=admin.id
        )
    return crud_semantic.context_card(db, context_id=context_id)


@router.post("/contexts/{context_id}/family")
def assign_family_route(
    context_id: int,
    body: AssignFamilyRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _mutating(db):
        work_families.assign_family(
            db, context_id=context_id, family_id=body.family_id, actor_id=admin.id
        )
    return crud_semantic.context_card(db, context_id=context_id)


@router.post("/contexts/{context_id}/split")
def split_context_route(
    context_id: int,
    body: SplitContextRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rule = body.rule.model_dump(exclude_none=True) if body.rule is not None else None
    with _mutating(db):
        result = context_operations.split_context(
            db, context_id=context_id, position_item_ids=body.position_item_ids, rule=rule,
            actor_id=admin.id,
        )
    return {
        "new_context_id": result.new_context_id,
        "moved_members": result.moved_members,
        "rule_id": result.rule_id,
        "default_replaced": result.default_replaced,
    }


@router.post("/contexts/{context_id}/merge")
def merge_contexts_route(
    context_id: int,
    body: MergeContextRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _mutating(db):
        moved = context_operations.merge_contexts(
            db, source_context_id=context_id, target_context_id=body.target_context_id,
            actor_id=admin.id,
        )
    return {"source_context_id": context_id, "target_context_id": body.target_context_id, "moved_members": moved}


@router.post("/contexts/{context_id}/archive")
def archive_context_route(
    context_id: int,
    body: ArchiveContextRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _mutating(db):
        context_operations.archive_context(
            db, context_id=context_id, new_default_context_id=body.new_default_context_id,
            actor_id=admin.id,
        )
    return {"context_id": context_id, "archived": True}


# ---------------------------------------------------------------------------
#  Членства
# ---------------------------------------------------------------------------

@router.post("/members/move")
def move_members_route(
    body: MoveMembersRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _mutating(db):
        moved = context_operations.move_members(
            db, position_item_ids=body.position_item_ids, target_context_id=body.target_context_id,
            actor_id=admin.id, reason=body.reason,
        )
    return {"target_context_id": body.target_context_id, "moved_members": moved}


@router.get("/members/{position_item_id}/transfer-proposal")
def transfer_proposal_route(
    position_item_id: int,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Только чтение (план, задача 12, «Утверждения»): ничего не пишет и не
    коммитит. `404`/доменный отказ, если у позиции нет членства вовсе —
    `ContextOperationError(REFUSE_INVALID_MEMBERSHIP)` сервиса переводится в
    `422` через `_read_domain_errors`; `CURRENT`-членство (переносить
    нечего) — законный ответ `proposal: null`, не отказ."""
    proposal = _read_domain_errors(
        context_operations.transfer_proposal, db, position_item_id=position_item_id
    )
    return {"position_item_id": position_item_id, "proposal": _serialize_transfer_proposal(proposal)}


@router.post("/members/{position_item_id}/transfer")
def accept_transfer_route(
    position_item_id: int,
    body: AcceptTransferRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _mutating(db):
        moved = context_operations.accept_transfer(
            db, position_item_id=position_item_id, expected_category_id=body.expected_category_id,
            actor_id=admin.id,
        )
    return {"position_item_id": position_item_id, "moved_members": moved}


@router.post("/members/accept-target-decision")
def accept_target_decision_route(
    body: AcceptTargetDecisionRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _mutating(db):
        updated = context_operations.accept_target_decision(
            db, position_item_ids=body.position_item_ids, actor_id=admin.id
        )
    return {"updated_members": updated}
