"""API семантического контура — двадцать один маршрут семей и контекстов и
девятнадцать маршрутов экрана «Предложения» под `/api/v1/semantic`, все под
правом `admin` (спека `2026-09-22-catalog-families-design.md` §2.7, §2.10; план,
задача 12; чтение членств группы — спека `2026-09-25-families-screen-design.md`
§2.8 п. 3; пакетный перенос устаревшей группы — та же спека §2.6, §2.8 п. 4;
очередь предложений, задания, шапка и решения `admin` — спека
`2026-09-28-semantic-suggestions-design.md` §2.9, §2.10, §2.13).

HTTP-слой поверх готовых сервисов задач 4, 6-10 (`services/context_routing.py`,
`services/context_operations.py`, `services/work_families.py`) — они не
переписываются. Здесь: тела запросов/ответов, права, **управление
транзакцией** (образец — `routers/review.py`) и трансляция трёх доменных
исключений сервисов в HTTP через `raise_domain_error`
(`routers/domain_errors.py`). Решения `admin` над очередью предложений
(`services/semantic_decisions.py`) идут через `_deciding`: к тем же исключениям
добавляются `DecisionConflict` (`409` с кодом; у `family_exists` в теле есть
`family_id` существующей семьи, возможно `null`) и `LookupError` (`404`).

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
db.rollback(); raise` в `routers/review.py`). Исключение — `DecisionConflict`
с `keep`: запись сервиса до отказа коммитится, отказ всё равно уходит `409`.
"""
from __future__ import annotations

import contextlib
import dataclasses
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

import crud.semantic as crud_semantic
import crud.semantic_queue as crud_semantic_queue
from auth import require_admin
from crud.common import DomainError
from database import get_db
from models import NameRole, SemanticKind, SemanticState, User
from routers.domain_errors import raise_domain_error
from services import context_operations, semantic_decisions, work_families
from services.context_operations import ContextOperationError
from services.context_routing import RoutingError
from services.semantic_decisions import DecisionConflict
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
        work_families.REFUSE_CLEAR_DEFINITION_ACTIVE,
        work_families.REFUSE_DUPLICATE_ACTIVE_FAMILY,
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
        context_operations.REFUSE_CONFLICTED,
        context_operations.REFUSE_MEMBER_CONTEXT_CHANGED,
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
    успех коммитит, отказ сервиса откатывает (отказ с `keep` — коммитит) и
    транслирует в `HTTPException`
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
    except DecisionConflict as exc:
        # Отказ с `keep` оставляет запись сервиса (обновлённый набор задержанного
        # задания): коммит вместо отката, 409 отдаёт `_deciding`.
        if exc.keep:
            db.commit()
        else:
            db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    else:
        db.commit()


#: Код `404` для `LookupError` сервисов решений («не найдено»).
CODE_NOT_FOUND = "not_found"


@contextlib.contextmanager
def _deciding(db: Session):
    """`_mutating` для решений над очередью предложений: дополнительно переводит
    `DecisionConflict` в `409` с кодом (у `family_exists` — с `family_id`, он
    может быть `null`) и `LookupError` (предложение, задание или пачка не
    найдены) в `404`. Откат (или, для отказа с `keep`, коммит) уже сделал
    внутренний `_mutating`."""
    try:
        with _mutating(db):
            yield
    except DecisionConflict as exc:
        context = (
            {"family_id": exc.family_id} if exc.code == semantic_decisions.CODE_FAMILY_EXISTS else {}
        )
        raise_domain_error(
            DomainError(status.HTTP_409_CONFLICT, str(exc), code=exc.code, context=context)
        )
    except LookupError as exc:
        if isinstance(exc, KeyError | IndexError):
            # Это дефект кода, а не «предложение не найдено»: не маскировать под 404.
            raise
        raise_domain_error(DomainError(status.HTTP_404_NOT_FOUND, str(exc), code=CODE_NOT_FOUND))


def _read_domain_errors(fn, /, *args, **kwargs):
    """Обёртка ЧТЕНИЯ (не пишет, коммит/rollback не нужны): переводит те же
    три исключения в `HTTPException` — используется маршрутом
    `transfer-proposal` и КАЖДЫМ маршрутом, читающим `context_card` (сам
    `GET /contexts/{id}` и мутации вида/роли/семьи, отдающие карточку ПОСЛЕ
    своего `_mutating(db)`): цикл разделов, обнаруженный при построении
    `member_paths` (`chapter_paths`, `services/context_routing.py`), тем
    самым переводится в доменную `422` там же, где угодно читается карточка."""
    try:
        return fn(*args, **kwargs)
    except (WorkFamilyError, ContextOperationError) as exc:
        raise_domain_error(_domain_error(exc))
    except RoutingError as exc:
        raise_domain_error(DomainError(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)))


# ---------------------------------------------------------------------------
#  Сериализация
# ---------------------------------------------------------------------------

def _serialize_family(db: Session, family_id: int) -> dict:
    """Ответ мутации семьи — той же формы, что строка списка (`GET
    /families`, `crud/semantic.py::list_families`): `unit_code` и
    `context_count` читаются заново (мутация уже закоммичена к этому
    вызову), а не берутся с ORM-объекта, на котором их нет."""
    row = crud_semantic.get_family_row(db, family_id=family_id)
    assert row is not None, f"семья {family_id} исчезла между мутацией и сериализацией ответа"
    return row


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
    #: Обратный переход `CONFIRMED → SUGGESTED` (спека §2.5, таблица
    #: переходов) — тем же маршрутом, что и подтверждение: `True` зовёт
    #: `unconfirm_kind` (вид пересчитывается правилом), `kind` в этом случае
    #: не участвует.
    unconfirm: bool = False


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


class StaleGroupTransferRequest(BaseModel):
    """Тело пакетного переноса устаревшей группы (спека §2.6, §2.8 п. 4,
    редакция 3): `chapter_item_ids` — РАЗДЕЛЫ группы (текст пути сливает
    разделы разных смет в одну группу экрана, `member_paths`/`stale_groups`,
    `crud/semantic.py`) — `null` — группа «без раздела» (то же значение, что
    несёт строка внимания экрана), а не «весь контекст» — второго смысла у
    `null` здесь нет, в отличие от `GroupSelector` чтения членств группы;
    пустой список — структурно бессмысленный вход (группа без разделов, не
    «без раздела вовсе») — `422`."""

    chapter_item_ids: list[int] | None
    expected_category_id: int | None

    @field_validator("chapter_item_ids")
    @classmethod
    def _chapter_item_ids_not_empty_list(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and len(value) == 0:
            raise ValueError(
                "chapter_item_ids: пустой список запрещён; null — группа «без раздела»."
            )
        return value


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
    return _serialize_family(db, family.id)


@router.patch("/families/{family_id}")
def update_family_route(
    family_id: int,
    body: UpdateFamilyRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _mutating(db):
        # `definition` — та же дисциплина, что `unit_name`: отсутствие поля в
        # теле и явный `null` РАЗЛИЧИМЫ (`work_families.UNSET` — «не
        # трогать», иначе «явный вход», доменное решение о нём — внутри
        # `update_family`).
        definition_kwargs: dict[str, object] = {}
        if "definition" in body.model_fields_set:
            definition_kwargs["definition"] = body.definition
        family = work_families.update_family(
            db, family_id=family_id, title=body.title, actor_id=admin.id, **definition_kwargs,
        )
        if "unit_name" in body.model_fields_set:
            family = work_families.set_unit(
                db, family_id=family_id, unit_name=body.unit_name, actor_id=admin.id
            )
    return _serialize_family(db, family.id)


@router.post("/families/{family_id}/activate")
def activate_family_route(
    family_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _mutating(db):
        family = work_families.activate_family(db, family_id=family_id, actor_id=admin.id)
    return _serialize_family(db, family.id)


@router.post("/families/{family_id}/archive")
def archive_family_route(
    family_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _mutating(db):
        family = work_families.archive_family(db, family_id=family_id, actor_id=admin.id)
    return _serialize_family(db, family.id)


@router.post("/families/{family_id}/merge")
def merge_families_route(
    family_id: int,
    body: MergeFamilyRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _mutating(db):
        work_families.merge_families(
            db, source_family_id=family_id, target_family_id=body.target_family_id,
            actor_id=admin.id,
        )
    # Ответ — строка ЦЕЛЕВОЙ (пережившей) семьи, той же формы, что список:
    # источник ушёл в архив, дальнейшая работа продолжается с целью.
    return _serialize_family(db, body.target_family_id)


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
    card = _read_domain_errors(crud_semantic.context_card, db, context_id=context_id)
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Контекст {context_id} не найден.")
    return card


def _group_selector(
    chapter_item_id: list[int] | None, no_chapter: bool
) -> crud_semantic.GroupSelector:
    """`chapter_item_id` — параметр запроса ПОВТОРЯЕТСЯ (редакция 3, спека
    §2.8 п. 2, 3): группа экрана — текст пути, и её галочка обязана раскрыть
    позиции ВСЕХ разделов, чей путь совпал (`member_paths.chapter_item_ids`),
    а не одного. Непустой `chapter_item_id` и `no_chapter=True` вместе —
    противоречие («разделы X, Y…» и «без раздела» разом невозможны, спека
    §2.8 п. 3) — `422` НЕКОДИРОВАННЫМ текстом, тем же путём, что и другие
    структурно неверные входы этого роутера (например, `split` с битым
    правилом)."""
    chapter_item_ids = tuple(chapter_item_id) if chapter_item_id else ()
    if chapter_item_ids and no_chapter:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "chapter_item_id и no_chapter=true нельзя передавать одновременно.",
        )
    return crud_semantic.GroupSelector(chapter_item_ids=chapter_item_ids, no_chapter=no_chapter)


@router.get("/contexts/{context_id}/members")
def list_group_members_route(
    context_id: int,
    chapter_item_id: list[int] | None = Query(default=None),
    no_chapter: bool = Query(default=False),
    state: crud_semantic.GroupState = Query(default="all"),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Постраничный список членств ОДНОЙ группы контекста (спека §2.8 п. 3,
    редакция 3): галочка группы карточки раскрывает её позиции ИМЕННО этим
    запросом, а не обрезанным списком карточки. `chapter_item_id` повторяется
    — группа из нескольких разделов, отбираются позиции ЛЮБОГО из них. Без
    `chapter_item_id`/`no_chapter` — группа «весь контекст»."""
    selector = _group_selector(chapter_item_id, no_chapter)
    result = crud_semantic.list_group_members(
        db, context_id=context_id, selector=selector, state=state, limit=limit, offset=offset,
    )
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Контекст {context_id} не найден.")
    return result


@router.get("/contexts/{context_id}/member-ids")
def list_group_member_ids_route(
    context_id: int,
    chapter_item_id: list[int] | None = Query(default=None),
    no_chapter: bool = Query(default=False),
    state: crud_semantic.GroupState = Query(default="all"),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Полный список id членств группы, БЕЗ обрезки и без `limit`/`offset`
    (спека §2.8 п. 3, редакция 3 — `chapter_item_id` повторяется): тот же
    набор, что галочка группы передаёт целиком в «Разделить…»/«Перенести…»."""
    selector = _group_selector(chapter_item_id, no_chapter)
    result = crud_semantic.list_group_member_ids(
        db, context_id=context_id, selector=selector, state=state,
    )
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Контекст {context_id} не найден.")
    return result


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
        if body.unconfirm:
            work_families.unconfirm_kind(db, context_id=context_id, actor_id=admin.id)
        else:
            work_families.confirm_kind(
                db, context_id=context_id, kind=body.kind.value if body.kind is not None else None,
                actor_id=admin.id,
            )
    # Мутация уже закоммичена строкой выше (`_mutating(db)` вышел без
    # исключения) — отказ ниже описывает ТОЛЬКО чтение карточки для ответа
    # (цикл разделов в `member_paths`), не саму мутацию: она остаётся в силе,
    # даже когда клиент получает 422 вместо тела карточки.
    return _read_domain_errors(crud_semantic.context_card, db, context_id=context_id)


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
    # Та же дисциплина, что у `confirm_kind_route`: мутация уже закоммичена,
    # отказ ниже — только о чтении карточки, не о смене роли.
    return _read_domain_errors(crud_semantic.context_card, db, context_id=context_id)


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
    # Та же дисциплина, что у `confirm_kind_route`: мутация уже закоммичена,
    # отказ ниже — только о чтении карточки, не о назначении семьи.
    return _read_domain_errors(crud_semantic.context_card, db, context_id=context_id)


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


@router.post("/contexts/{context_id}/stale-groups/transfer")
def transfer_stale_group_route(
    context_id: int,
    body: StaleGroupTransferRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Пакетный перенос устаревшей группы одной строки внимания (спека §2.6,
    §2.8 п. 4, редакция 3): группа — `chapter_item_ids` (разделы группы,
    `null` — без раздела, пустой список отвергнут `StaleGroupTransferRequest`
    валидатором), `expected_category_id` — статья, которую оператор видел в
    строке внимания. Ответ несёт результат по КАЖДОЙ позиции пакета;
    частичный отказ — законный `200`, не ошибка (пачка не атомарна,
    `context_operations.transfer_stale_group`)."""
    with _mutating(db):
        result = context_operations.transfer_stale_group(
            db, context_id=context_id,
            chapter_item_ids=(
                tuple(body.chapter_item_ids) if body.chapter_item_ids is not None else None
            ),
            expected_category_id=body.expected_category_id, actor_id=admin.id,
        )
    return {
        "results": [
            {
                "position_item_id": item.position_item_id,
                "outcome": item.outcome,
                "target_context_id": item.target_context_id,
                "error_code": item.error_code,
                "message": item.message,
            }
            for item in result.results
        ],
        "moved": result.moved,
        "refused": result.refused,
    }


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


# ---------------------------------------------------------------------------
#  Экран «Предложения»: тела запросов
# ---------------------------------------------------------------------------

class ConfirmSuggestionsRequest(BaseModel):
    suggestion_ids: list[int] = Field(min_length=1)


class OtherFamilyRequest(BaseModel):
    family_id: int


class CreateFamilyFromSuggestionRequest(BaseModel):
    title: str
    definition: str


class PrivacyMatchIn(BaseModel):
    text: str
    kind: str
    where: str


class PrivacyDecisionRequest(BaseModel):
    """`shown_matches` — набор совпадений, который экран показал: сервис
    сверяет его с текущей проверкой и при расхождении отвечает `409`."""

    shown_matches: list[PrivacyMatchIn]


class UnitPrivacyReleaseRequest(BaseModel):
    """`unit_id` обязателен и допускает `null` («задания без единицы»): нет ключа —
    `422`, чтобы «забыли передать» не читалось как «без единицы»."""

    unit_id: int | None
    shown_matches: list[PrivacyMatchIn]


class UnitReaskPreviewRequest(BaseModel):
    """`unit_id`: как в `UnitPrivacyReleaseRequest` — обязателен, `null` допустим."""

    unit_id: int | None


class UnitReaskRequest(BaseModel):
    unit_id: int | None
    preview_hash: str


class PreviewHashRequest(BaseModel):
    preview_hash: str


def _shown(matches: list[PrivacyMatchIn]) -> list[dict]:
    return [match.model_dump() for match in matches]


def _serialize_preview(preview: semantic_decisions.Preview) -> dict:
    return {
        "context_count": preview.context_count,
        "reserve_usd": crud_semantic_queue.money_str(preview.reserve_usd),
        "expected_cached_usd": crud_semantic_queue.money_str(preview.expected_cached_usd),
        "preview_hash": preview.preview_hash,
    }


def _serialize_reconcile(report) -> dict:
    return dataclasses.asdict(report)


# ---------------------------------------------------------------------------
#  Экран «Предложения»: чтение
# ---------------------------------------------------------------------------

@router.get("/suggestions")
def list_suggestions_route(
    queue: Literal["list", "new"] = Query(default="list"),
    unit: str | None = Query(default=None),
    band: Literal["high", "mid", "low"] | None = Query(default=None),
    multi_owner: bool = Query(default=False),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """`unit`: id единицы либо `none` (контексты без единицы); нет параметра — все
    единицы; иное значение — `422`."""
    unit_filter: crud_semantic_queue.UnitFilter
    if unit is None:
        unit_filter = None
    elif unit == crud_semantic_queue.UNIT_NONE:
        unit_filter = crud_semantic_queue.UNIT_NONE
    elif unit.isascii() and unit.isdigit():
        unit_filter = int(unit)
    else:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "unit: ожидается id единицы или `none` (контексты без единицы).",
        )
    return crud_semantic_queue.list_suggestions(
        db, queue=queue, unit_id=unit_filter, band=band, multi_owner_only=multi_owner
    )


@router.get("/jobs")
def list_jobs_route(
    status_: Literal["error", "privacy_hold"] = Query(alias="status"),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return crud_semantic_queue.list_jobs(db, status=status_)


@router.get("/status")
def queue_status_route(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return crud_semantic_queue.queue_status(db)


# ---------------------------------------------------------------------------
#  Экран «Предложения»: решения по предложению
# ---------------------------------------------------------------------------

@router.post("/suggestions/confirm")
def confirm_suggestions_route(
    body: ConfirmSuggestionsRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _deciding(db):
        report = semantic_decisions.confirm_suggestions(
            db, suggestion_ids=body.suggestion_ids, actor_id=admin.id
        )
    return {"confirmed": report.confirmed, "skipped": report.skipped}


@router.post("/suggestions/{suggestion_id}/reject")
def reject_suggestion_route(
    suggestion_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _deciding(db):
        semantic_decisions.reject_suggestion(db, suggestion_id=suggestion_id, actor_id=admin.id)
    return {"suggestion_id": suggestion_id, "decision": "rejected"}


@router.post("/suggestions/{suggestion_id}/other-family")
def other_family_route(
    suggestion_id: int,
    body: OtherFamilyRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _deciding(db):
        semantic_decisions.assign_other_family(
            db, suggestion_id=suggestion_id, family_id=body.family_id, actor_id=admin.id
        )
    return {"suggestion_id": suggestion_id, "decision": "other_family", "family_id": body.family_id}


@router.post("/suggestions/{suggestion_id}/create-family")
def create_family_from_suggestion_route(
    suggestion_id: int,
    body: CreateFamilyFromSuggestionRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _deciding(db):
        try:
            family_id = semantic_decisions.create_family_from_suggestion(
                db, suggestion_id=suggestion_id, title=body.title,
                definition=body.definition, actor_id=admin.id,
            )
        except ValueError as exc:
            raise_domain_error(DomainError(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)))
    return {"suggestion_id": suggestion_id, "decision": "family_created", "family_id": family_id}


# ---------------------------------------------------------------------------
#  Экран «Предложения»: задания
# ---------------------------------------------------------------------------

@router.post("/jobs/{job_id}/retry")
def retry_job_route(
    job_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _deciding(db):
        semantic_decisions.retry_job(db, job_id=job_id, actor_id=admin.id)
    return {"job_id": job_id, "status": "pending"}


@router.post("/jobs/{job_id}/privacy-release")
def privacy_release_route(
    job_id: int,
    body: PrivacyDecisionRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _deciding(db):
        semantic_decisions.release_privacy_hold(
            db, job_id=job_id, shown_matches=_shown(body.shown_matches), actor_id=admin.id
        )
    return {"job_id": job_id, "status": "pending"}


@router.post("/jobs/{job_id}/privacy-decline")
def privacy_decline_route(
    job_id: int,
    body: PrivacyDecisionRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _deciding(db):
        semantic_decisions.decline_privacy_hold(
            db, job_id=job_id, shown_matches=_shown(body.shown_matches), actor_id=admin.id
        )
    return {"job_id": job_id, "status": "cancelled"}


@router.post("/unit-privacy-release")
def unit_privacy_release_route(
    body: UnitPrivacyReleaseRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _deciding(db):
        report = semantic_decisions.release_unit_privacy_holds(
            db, unit_id=body.unit_id, shown_matches=_shown(body.shown_matches), actor_id=admin.id
        )
    return {"confirmed": report.confirmed, "skipped": report.skipped}


# ---------------------------------------------------------------------------
#  Экран «Предложения»: перезапросы, пачки, остановка захвата
# ---------------------------------------------------------------------------

@router.post("/unit-reask/preview")
def unit_reask_preview_route(
    body: UnitReaskPreviewRequest,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _serialize_preview(semantic_decisions.preview_unit_reask(db, unit_id=body.unit_id))


@router.post("/unit-reask")
def unit_reask_route(
    body: UnitReaskRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _deciding(db):
        report = semantic_decisions.confirm_unit_reask(
            db, unit_id=body.unit_id, preview_hash=body.preview_hash, actor_id=admin.id
        )
    return _serialize_reconcile(report)


@router.post("/reask-all/preview")
def reask_all_preview_route(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _serialize_preview(semantic_decisions.preview_config_reask(db))


@router.post("/reask-all")
def reask_all_route(
    body: PreviewHashRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _deciding(db):
        report = semantic_decisions.confirm_config_reask(
            db, preview_hash=body.preview_hash, actor_id=admin.id
        )
    return _serialize_reconcile(report)


@router.post("/batches/{batch_id}/preview")
def batch_preview_route(
    batch_id: int,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Preview удержанной пачки по текущим отпечаткам её контекстов: без него
    «Поставить…» не получит `preview_hash`."""
    with _deciding(db):
        preview = semantic_decisions.preview_batch(db, batch_id=batch_id)
    return _serialize_preview(preview)


@router.post("/batches/{batch_id}/approve")
def batch_approve_route(
    batch_id: int,
    body: PreviewHashRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _deciding(db):
        report = semantic_decisions.approve_batch(
            db, batch_id=batch_id, preview_hash=body.preview_hash, actor_id=admin.id
        )
    return _serialize_reconcile(report)


@router.post("/batches/{batch_id}/discard")
def batch_discard_route(
    batch_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _deciding(db):
        semantic_decisions.discard_batch(db, batch_id=batch_id, actor_id=admin.id)
    return {"batch_id": batch_id, "status": "discarded"}


@router.post("/worker/resume")
def worker_resume_route(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    with _deciding(db):
        semantic_decisions.resume_worker(db, actor_id=admin.id)
    return {"claim_paused": False}
