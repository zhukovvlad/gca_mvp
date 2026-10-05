"""Смена семьи контекста: единая точка входа, ожидающее назначение,
автопринятие и общий захват блокировок решений (спека
`2026-10-02-catalog-variants-design.md` §2.5, §2.6, §2.13).

Контекст без варианта получает семью сразу (`assign_family`). Контекст с
вариантом любую смену семьи получает как ожидание: колонки `pending_*`,
событие `context_family_pending`, задание значений по схеме новой семьи ставит
сверка. Переключение семьи и варианта по готовности значений делает
`work_variants.apply_values` одной транзакцией.

Порядок блокировок во всей фиче: строка каталога -> семьи по возрастанию `id`
-> вариант -> контекст -> задание. Решения человека, автопринятие, запрос
смены и отмена ожидания берут семьи `FOR SHARE`, контекст `FOR UPDATE`.
Текущая и ожидаемая семьи контекста сначала читаются БЕЗ блокировок, поэтому
захват идёт попыткой в точке сохранения (`acquire_family_locks`): расхождение
перечитанного с прочитанным откатывает точку сохранения (Postgres снимает
блокировки, взятые после неё) и повторяет захват целиком; новая семья
никогда не запрашивается при удерживаемом контексте.

Функции не коммитят: транзакцией владеет вызывающий. Исключение —
`apply_publication_rules`: правила публикации идут отдельной транзакцией после
записи ответа модели и коммитят сами.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from config import settings
from models import (
    CatalogContext,
    CatalogKind,
    CatalogPosition,
    ContextBucket,
    FamilySource,
    FamilySuggestion,
    SuggestionDecision,
)
from services.semantic_events import record_event
from services.semantic_reconcile import reconcile_or_defer
from services.semantic_request import is_applicable, load_request_material, render_context_request
from services.work_families import (
    REFUSE_CONTEXT_ARCHIVED,
    REFUSE_CONTEXT_NOT_FOUND,
    WorkFamilyError,
    _lock_contexts,
    _lock_families,
    assign_family,
    check_family_assignable,
)

__all__ = [
    "FamilyChangeOutcome",
    "FamilyChangeSource",
    "FamilyLockMismatch",
    "PendingOutcome",
    "PendingState",
    "acquire_family_locks",
    "apply_publication_rules",
    "cancel_pending_family",
    "clear_pending",
    "family_change_route",
    "lock_and_recheck_suggestion",
    "record_pending_outcome",
    "request_family_change",
]

FamilyChangeSource = Literal["manual", "suggestion", "auto_suggestion"]
PendingOutcome = Literal["superseded", "cancelled", "applied"]

#: Колонки контекста, составляющие ожидающее назначение (CHECK
#: `ck_catalog_contexts_pending`: все пусты либо заполнены согласованно).
_PENDING_COLUMNS = (
    "pending_family_id", "pending_family_source", "pending_suggestion_id", "pending_by",
    "pending_threshold", "pending_at",
)

#: Виды строки каталога, к которым применяется автопринятие: `HEADER` и
#: `TRASH` — не работа.
_APPLICABLE_CATALOG_KINDS = (CatalogKind.TO_REVIEW.value, CatalogKind.POSITION.value)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class FamilyChangeOutcome:
    """Итог запроса смены семьи: `assigned` — семья сменилась сразу, `pending`
    — поставлено ожидание, `unchanged` — запрос ничего не изменил (та же
    семья, либо правило не вытесняет ожидание человека).
    `superseded_suggestion_id` — предложение, чьё ожидание этот запрос
    вытеснил."""

    kind: Literal["assigned", "pending", "unchanged"]
    context_id: int
    family_id: int | None
    superseded_suggestion_id: int | None


class FamilyLockMismatch(Exception):
    """Семья контекста сменилась между чтением и блокировкой и после повтора
    (`context_ids` — такие контексты). Вызывающий превращает отказ в `409`
    либо относит предложение в пропущенные."""

    def __init__(self, context_ids: Sequence[int]) -> None:
        super().__init__(f"семья контекста сменилась при захвате: {sorted(context_ids)}")
        self.context_ids = tuple(sorted(context_ids))


# ---------------------------------------------------------------------------
#  Захват блокировок
# ---------------------------------------------------------------------------

def _read_family_pairs(
    db: Session, context_ids: Sequence[int]
) -> dict[int, tuple[int | None, int | None]]:
    """`{контекст: (текущая семья, ожидаемая семья)}` без блокировок."""
    rows = db.execute(
        sa.select(
            CatalogContext.id, CatalogContext.work_family_id, CatalogContext.pending_family_id
        ).where(CatalogContext.id.in_(list(context_ids)))
    ).all()
    return {row.id: (row.work_family_id, row.pending_family_id) for row in rows}


def acquire_family_locks(
    db: Session,
    items: Sequence[tuple[int, int | None]],
    *,
    release_on_failure: bool,
) -> set[int]:
    """Захват семей и контекстов решения (или группы решений): `items` — пары
    `(контекст, запрашиваемая семья)`.

    (1) без блокировок читаются текущая и ожидаемая семьи контекстов;
    (2) открывается точка сохранения ДО первой блокировки попытки;
    (3) ВСЕ семьи — запрашиваемые, текущие, ожидаемые — берутся `FOR SHARE`
    одним запросом по возрастанию `id`, затем контексты `FOR UPDATE` по `id`;
    (4) пары семей контекстов перечитываются под блокировкой. Разошлись с
    прочитанным — откат к точке сохранения (блокировки сняты), повторное
    чтение и ПОЛНЫЙ перезахват. Повтор один. Расхождение и на нём: новых
    блокировок не берётся; `release_on_failure=True` откатывает точку
    сохранения (одиночное решение — блокировки сняты), `False` оставляет
    захваченное (группа — прочие предложения продолжают).

    Возвращает контексты, чья пара семей так и не устоялась (пусто — захват
    удался)."""
    context_ids = sorted({context_id for context_id, _ in items})
    requested = {family_id for _, family_id in items if family_id is not None}
    unstable: set[int] = set()
    for attempt in (1, 2):
        seen = _read_family_pairs(db, context_ids)
        savepoint = db.begin_nested()
        family_ids = sorted(
            requested
            | {family for pair in seen.values() for family in pair if family is not None}
        )
        _lock_families(db, family_ids, exclusive=False)
        _lock_contexts(db, context_ids)
        now = _read_family_pairs(db, context_ids)
        unstable = {cid for cid in context_ids if now.get(cid) != seen.get(cid)}
        if not unstable:
            savepoint.commit()
            return set()
        if attempt == 1 or release_on_failure:
            savepoint.rollback()
        else:
            savepoint.commit()
    return unstable


def _acquire_single(db: Session, context_id: int, family_id: int | None) -> None:
    unstable = acquire_family_locks(db, [(context_id, family_id)], release_on_failure=True)
    if unstable:
        raise FamilyLockMismatch(sorted(unstable))


def lock_and_recheck_suggestion(db: Session, suggestion_id: int) -> FamilySuggestion | None:
    """`FOR UPDATE` на предложение и перепроверка; `None` — не прошло:
    опубликовано, решения нет, контекст применим, текущий отпечаток контекста
    (материал -> рендер) равен `request_hash` предложения. Контекст к этому
    моменту уже заблокирован вызывающим."""
    suggestion = db.execute(
        sa.select(FamilySuggestion)
        .where(FamilySuggestion.id == suggestion_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if suggestion is None or not suggestion.is_published or suggestion.decision is not None:
        return None
    material = load_request_material(db, [suggestion.context_id]).get(suggestion.context_id)
    if material is None or not is_applicable(material):
        return None
    current = render_context_request(material, settings=settings)
    if current.request_hash != suggestion.request_hash:
        return None
    return suggestion


# ---------------------------------------------------------------------------
#  Ожидание: чтение, снятие, исход предложения
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PendingState:
    """Ожидающее назначение контекста, снятое с его колонок."""

    family_id: int
    source: str
    suggestion_id: int | None
    by: int | None
    threshold: Decimal | None

    @staticmethod
    def of(context: CatalogContext) -> PendingState | None:
        if context.pending_family_id is None:
            return None
        return PendingState(
            family_id=context.pending_family_id,
            source=context.pending_family_source,
            suggestion_id=context.pending_suggestion_id,
            by=context.pending_by,
            threshold=context.pending_threshold,
        )


def _pending_payload(state: PendingState, outcome: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "pending_family_id": state.family_id,
        "source": state.source,
        "suggestion_id": state.suggestion_id,
        "outcome": outcome,
    }
    if state.source == FamilySource.auto_suggestion.value:
        payload["threshold"] = str(state.threshold)
    return payload


def _settle_suggestion(
    db: Session, state: PendingState, outcome: PendingOutcome, actor_id: int | None
) -> None:
    """Исход предложения, породившего ожидание (спека §2.5 п. 2, 4, 6).
    Применено: `auto_pending` -> `auto_accepted`, `accepted_pending` ->
    `accepted`. Снято или вытеснено человеком (`actor_id`): оба -> `rejected` с
    автором. Снято или вытеснено без человека: `auto_pending` ->
    `auto_superseded` без автора (схема не допускает отклонения без автора),
    `accepted_pending` -> `rejected` с прежним автором. Прочие решения
    (`other_family`, `family_created`) не трогаются."""
    if state.suggestion_id is None:
        return
    suggestion = db.execute(
        sa.select(FamilySuggestion)
        .where(FamilySuggestion.id == state.suggestion_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if suggestion is None:
        return
    decision = suggestion.decision
    if decision not in (
        SuggestionDecision.auto_pending.value, SuggestionDecision.accepted_pending.value
    ):
        return
    auto = decision == SuggestionDecision.auto_pending.value
    if outcome == "applied":
        suggestion.decision = (
            SuggestionDecision.auto_accepted.value if auto else SuggestionDecision.accepted.value
        )
    elif actor_id is not None:
        suggestion.decision = SuggestionDecision.rejected.value
        suggestion.decided_by = actor_id
        suggestion.decided_at = _now()
    elif auto:
        suggestion.decision = SuggestionDecision.auto_superseded.value
    else:
        suggestion.decision = SuggestionDecision.rejected.value
    db.flush()


def record_pending_outcome(
    db: Session,
    context_id: int,
    state: PendingState,
    outcome: PendingOutcome,
    *,
    actor_id: int | None,
) -> None:
    """Событие `context_family_pending` и исход предложения для ожидания,
    уже снятого с колонок контекста (`apply_values` очищает их сам, вместе с
    переключением семьи)."""
    record_event(
        db,
        event_type="context_family_pending",
        context_id=context_id,
        actor_id=actor_id,
        payload=_pending_payload(state, outcome),
    )
    _settle_suggestion(db, state, outcome, actor_id)


def clear_pending(
    db: Session, context: CatalogContext, *, outcome: PendingOutcome, actor_id: int | None
) -> PendingState | None:
    """Очищает колонки ожидания контекста (под его блокировкой) и пишет исход.
    `None` — ожидания не было."""
    state = PendingState.of(context)
    if state is None:
        return None
    for column in _PENDING_COLUMNS:
        setattr(context, column, None)
    db.flush()
    record_pending_outcome(db, context.id, state, outcome, actor_id=actor_id)
    return state


# ---------------------------------------------------------------------------
#  Запрос смены семьи
# ---------------------------------------------------------------------------

def _human(source: str) -> bool:
    return source != FamilySource.auto_suggestion.value


def _may_displace(existing_source: str | None, new_source: str) -> bool:
    """Правило не вытесняет ожидание человека; ожидание `auto_suggestion`
    вытесняется и правилом, и человеком (спека §2.5 п. 4)."""
    return existing_source is None or _human(new_source) or not _human(existing_source)


def family_change_route(
    context: CatalogContext, family_id: int | None, source: str
) -> Literal["assigned", "pending", "unchanged"]:
    """Какой путь выберет `request_family_change` для контекста в данном
    состоянии (под его блокировкой): та же семья или ожидание человека, которое
    правило не вытесняет, — `unchanged`; контекст с вариантом — `pending`;
    без варианта — `assigned`."""
    if not _may_displace(context.pending_family_source, source) and (
        context.pending_family_id is not None
    ):
        return "unchanged"
    if family_id is not None and context.work_family_id == family_id:
        return "unchanged"
    if family_id is not None and context.work_variant_id is not None:
        return "pending"
    return "assigned"


def _validate_call(
    *, family_id: int | None, actor_id: int | None, source: str,
    suggestion_id: int | None, threshold: Decimal | None,
) -> None:
    if source not in {member.value for member in FamilySource}:
        raise ValueError(f"неизвестный источник смены семьи: {source!r}")
    auto = source == FamilySource.auto_suggestion.value
    if (actor_id is None) != auto:
        raise ValueError("actor_id пуст тогда и только тогда, когда source=auto_suggestion")
    if source == FamilySource.manual.value:
        if suggestion_id is not None:
            raise ValueError("suggestion_id допустим только при source=suggestion и auto_suggestion")
    else:
        if suggestion_id is None:
            raise ValueError(f"source={source} требует suggestion_id")
        if family_id is None:
            raise ValueError(f"source={source} не снимает семью: family_id обязателен")
    if auto:
        if threshold is None:
            raise ValueError("source=auto_suggestion требует threshold")
    elif threshold is not None:
        raise ValueError("threshold допустим только при source=auto_suggestion")


def _load_context(db: Session, context_id: int) -> CatalogContext:
    context = db.execute(
        sa.select(CatalogContext)
        .where(CatalogContext.id == context_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if context is None:
        raise WorkFamilyError(
            REFUSE_CONTEXT_NOT_FOUND, f"контекст {context_id} не найден", context_id=context_id
        )
    return context


def _suggestion_confidence(db: Session, suggestion_id: int) -> Decimal:
    return db.execute(
        sa.select(FamilySuggestion.confidence).where(FamilySuggestion.id == suggestion_id)
    ).scalar_one()


def _supersede(
    db: Session, context: CatalogContext, *, actor_id: int | None
) -> int | None:
    """Прежнее ожидание вытеснено новым запросом: `outcome = superseded`.
    Возвращает предложение вытесненного ожидания."""
    state = clear_pending(db, context, outcome="superseded", actor_id=actor_id)
    return None if state is None else state.suggestion_id


def request_family_change(
    db: Session,
    *,
    context_id: int,
    family_id: int | None,
    actor_id: int | None,
    source: FamilyChangeSource,
    suggestion_id: int | None = None,
    threshold: Decimal | None = None,
) -> FamilyChangeOutcome:
    """Единая точка смены семьи контекста (спека §2.5): выбирает путь.

    Путь 1 (нет варианта): `assign_family` сразу — есть у контекста семья или
    нет; прежнее задание значений отменяет сверка. Путь 2 (есть вариант,
    семья другая): ожидание — `pending_*`, событие `context_family_pending`
    (`set`), прежнее ожидание вытеснено (`superseded`) — правило не вытесняет
    ожидание человека, и тогда запрос ничего не меняет. Та же семья, что
    текущая, ничего не меняет, но вытесняет ожидание другой семьи. Сверка
    очереди (задание значений по схеме новой семьи) вызывается здесь же, до
    `commit` вызывающего; решение предложения вызывающий записывает ДО вызова
    (по `family_change_route`).

    `family_id=None` (снятие семьи) поддержано только у контекста без варианта;
    у контекста с вариантом снятие меняет и вариант, и значения (спека §2.5,
    последний абзац) — это отдельная операция.

    Raises:
        ValueError: несогласованные `actor_id`/`source`/`suggestion_id`/
            `threshold`, либо снятие семьи у контекста с вариантом.
        FamilyLockMismatch: семья контекста сменилась при захвате дважды.
        WorkFamilyError: контекст не найден или архивирован; семья не найдена,
            не `active` или с другой единицей."""
    _validate_call(
        family_id=family_id, actor_id=actor_id, source=source,
        suggestion_id=suggestion_id, threshold=threshold,
    )
    _acquire_single(db, context_id, family_id)
    context = _load_context(db, context_id)
    route = family_change_route(context, family_id, source)

    if family_id is None and context.work_variant_id is not None:
        raise ValueError("снятие семьи у контекста с вариантом — отдельная операция (спека §2.5)")

    if route == "unchanged":
        superseded: int | None = None
        if (
            family_id is not None
            and context.work_family_id == family_id
            and context.pending_family_id is not None
            and _may_displace(context.pending_family_source, source)
        ):
            superseded = _supersede(db, context, actor_id=actor_id)
            reconcile_or_defer(db, [context_id])
        return FamilyChangeOutcome("unchanged", context_id, family_id, superseded)

    if context.archived_at is not None and family_id is not None:
        raise WorkFamilyError(
            REFUSE_CONTEXT_ARCHIVED, f"контекст {context_id} архивирован", context_id=context_id
        )
    if family_id is not None:
        check_family_assignable(db, context, family_id)

    superseded = _supersede(db, context, actor_id=actor_id)

    if route == "assigned":
        assign_kwargs: dict[str, object] = {}
        if source == FamilySource.auto_suggestion.value:
            assign_kwargs = {
                "threshold": threshold,
                "confidence": _suggestion_confidence(db, suggestion_id),  # type: ignore[arg-type]
            }
        assign_family(
            db,
            context_id=context_id,
            family_id=family_id,
            actor_id=actor_id,
            source=FamilySource(source),
            suggestion_id=suggestion_id,
            **assign_kwargs,  # type: ignore[arg-type]
        )
        return FamilyChangeOutcome("assigned", context_id, family_id, superseded)

    assert family_id is not None
    state = PendingState(
        family_id=family_id,
        source=source,
        suggestion_id=suggestion_id,
        by=actor_id if source == FamilySource.manual.value else None,
        threshold=threshold,
    )
    context.pending_family_id = state.family_id
    context.pending_family_source = state.source
    context.pending_suggestion_id = state.suggestion_id
    context.pending_by = state.by
    context.pending_threshold = state.threshold
    context.pending_at = _now()
    db.flush()
    record_event(
        db,
        event_type="context_family_pending",
        context_id=context_id,
        actor_id=actor_id,
        payload=_pending_payload(state, "set"),
    )
    reconcile_or_defer(db, [context_id])
    return FamilyChangeOutcome("pending", context_id, family_id, superseded)


def cancel_pending_family(db: Session, *, context_id: int, actor_id: int) -> None:
    """«Отменить ожидание»: очищает колонки, `outcome = cancelled`; порождавшее
    предложение -> `rejected` с автором-отменившим; сверка отменяет задание
    значений по ожидаемой семье. Ожидания нет — ничего не делает.

    Raises:
        FamilyLockMismatch: семья контекста сменилась при захвате дважды.
        WorkFamilyError: контекст не найден."""
    _acquire_single(db, context_id, None)
    context = _load_context(db, context_id)
    if clear_pending(db, context, outcome="cancelled", actor_id=actor_id) is None:
        return
    reconcile_or_defer(db, [context_id])


# ---------------------------------------------------------------------------
#  Правила публикации (автопринятие)
# ---------------------------------------------------------------------------

def _record_auto_decision(suggestion: FamilySuggestion, decision: SuggestionDecision) -> None:
    suggestion.decision = decision.value
    suggestion.decided_by = None
    suggestion.decided_at = _now()


def _catalog_kind(db: Session, context_id: int) -> str:
    return db.execute(
        sa.select(CatalogPosition.kind)
        .select_from(CatalogContext)
        .join(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
        .join(CatalogPosition, CatalogPosition.id == ContextBucket.catalog_position_id)
        .where(CatalogContext.id == context_id)
    ).scalar_one()


def _apply_rules_locked(
    db: Session, suggestion: FamilySuggestion, context: CatalogContext, threshold: Decimal
) -> FamilyChangeOutcome | None:
    """Таблица публикации (спека §2.5) под блокировками. `None` — правило
    предложения не трогает: оно остаётся опубликованным человеку."""
    family_id = suggestion.family_id
    assert family_id is not None
    auto_source = FamilySource.auto_suggestion.value

    if context.work_family_id == family_id:
        # Та же семья: ответ модели подтверждает привязку. Ожидание другой
        # семьи от автопринятия снимается (иначе более старое автоожидание
        # отменило бы более новое решение), ожидание человека не тронуто.
        _record_auto_decision(suggestion, SuggestionDecision.auto_accepted)
        superseded: int | None = None
        if (
            context.pending_family_id is not None
            and context.pending_family_source == auto_source
        ):
            superseded = _supersede(db, context, actor_id=None)
            reconcile_or_defer(db, [context.id])
        db.flush()
        return FamilyChangeOutcome("unchanged", context.id, family_id, superseded)

    if suggestion.confidence < threshold:
        return None
    if context.work_family_id is not None and context.family_source != auto_source:
        return None  # привязка человека: очередь «смена семьи»
    route = family_change_route(context, family_id, auto_source)
    if route == "unchanged":
        return None  # ожидание человека правилом не вытесняется
    _record_auto_decision(
        suggestion,
        SuggestionDecision.auto_pending if route == "pending" else SuggestionDecision.auto_accepted,
    )
    db.flush()
    return request_family_change(
        db,
        context_id=context.id,
        family_id=family_id,
        actor_id=None,
        source="auto_suggestion",
        suggestion_id=suggestion.id,
        threshold=threshold,
    )


def apply_publication_rules(
    db: Session, *, suggestion_id: int, threshold: Decimal | None
) -> FamilyChangeOutcome | None:
    """Правила публикации по только что записанному предложению — отдельная
    транзакция после `commit` записи ответа (спека §2.5): блокировки (семьи по
    `id` `FOR SHARE`, контекст `FOR UPDATE`) -> предложение `FOR UPDATE` с
    перепроверкой (опубликовано, решения нет, отпечаток текущий) -> таблица.
    Коммитит сама; ошибка откатывает только эту транзакцию — ответ модели уже
    записан, предложение остаётся опубликованным.

    `None` — предложение не тронуто: порога нет, ответ без «своей» семьи,
    предложение изменилось или решено, контекст не применим либо строка
    каталога не работа, семья контекста сменилась при захвате, либо таблица
    оставила его человеку."""
    if threshold is None:
        return None
    try:
        outcome = _apply_publication_rules(db, suggestion_id, threshold)
        db.commit()
        return outcome
    except FamilyLockMismatch:
        db.rollback()
        return None
    except BaseException:
        db.rollback()
        raise


def _apply_publication_rules(
    db: Session, suggestion_id: int, threshold: Decimal
) -> FamilyChangeOutcome | None:
    row = db.execute(
        sa.select(
            FamilySuggestion.context_id, FamilySuggestion.family_id,
            FamilySuggestion.is_published, FamilySuggestion.decision,
        ).where(FamilySuggestion.id == suggestion_id)
    ).one_or_none()
    if row is None or row.family_id is None or not row.is_published or row.decision is not None:
        return None
    _acquire_single(db, row.context_id, row.family_id)
    suggestion = lock_and_recheck_suggestion(db, suggestion_id)
    if suggestion is None:
        return None
    context = _load_context(db, suggestion.context_id)
    if _catalog_kind(db, context.id) not in _APPLICABLE_CATALOG_KINDS:
        return None
    return _apply_rules_locked(db, suggestion, context, threshold)
