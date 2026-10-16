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

Массовое автопринятие (`preview_auto_accept`, `apply_auto_accept`, спека §2.12)
применяет ту же таблицу публикации к опубликованным предложениям пачкой: исход
каждой строки считает одна функция (`rule_outcome`), поэтому предварительный
показ и правило публикации разойтись не могут.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from config import Settings, settings
from models import (
    CatalogContext,
    CatalogKind,
    CatalogPosition,
    ContextBucket,
    FamilySource,
    FamilyStatus,
    FamilySuggestion,
    SemanticKind,
    SuggestionDecision,
    WorkFamily,
)
from services.semantic_events import record_event
from services.semantic_reconcile import deferred_reconcile, reconcile_or_defer
from services.semantic_request import (
    ContextRequestMaterial,
    is_applicable,
    load_request_material,
    render_context_request,
)
from services.work_families import (
    REFUSE_CONTEXT_ARCHIVED,
    REFUSE_CONTEXT_NOT_FOUND,
    WorkFamilyError,
    _lock_contexts,
    _lock_families,
    _lock_variants,
    assign_family,
    check_family_assignable,
)

__all__ = [
    "AutoAcceptError",
    "AutoAcceptPreview",
    "FamilyChangeOutcome",
    "FamilyChangeSource",
    "FamilyLockMismatch",
    "PendingOutcome",
    "PendingState",
    "acquire_family_locks",
    "apply_auto_accept",
    "apply_publication_rules",
    "cancel_pending_family",
    "clear_pending",
    "family_change_route",
    "lock_and_recheck_suggestion",
    "preview_auto_accept",
    "record_pending_outcome",
    "request_family_change",
    "rule_outcome",
]

FamilyChangeSource = Literal["manual", "suggestion", "auto_suggestion"]
PendingOutcome = Literal["superseded", "cancelled", "applied", "redirected"]

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


def _read_variants(db: Session, context_ids: Sequence[int]) -> dict[int, int | None]:
    """`{контекст: вариант}` без блокировок."""
    rows = db.execute(
        sa.select(CatalogContext.id, CatalogContext.work_variant_id).where(
            CatalogContext.id.in_(list(context_ids))
        )
    ).all()
    return {row.id: row.work_variant_id for row in rows}


def acquire_family_locks(
    db: Session,
    items: Sequence[tuple[int, int | None]],
    *,
    release_on_failure: bool,
    lock_variants: bool = False,
    exclusive_families: bool = False,
    extra_context_ids: Collection[int] = (),
) -> set[int]:
    """Захват семей и контекстов решения (или группы решений): `items` — пары
    `(контекст, запрашиваемая семья)`.

    (1) без блокировок читаются текущая и ожидаемая семьи контекстов;
    (2) открывается точка сохранения ДО первой блокировки попытки;
    (3) ВСЕ семьи — запрашиваемые, текущие, ожидаемые — берутся `FOR SHARE`
    (`FOR UPDATE` при `exclusive_families`)
    одним запросом по возрастанию `id`; при `lock_variants` затем читаются
    варианты контекстов (уже под блокировкой семей: менять вариант контекста
    можно только под `FOR UPDATE` его семьи, поэтому он устойчив, а параллельное
    снятие только очищает его) и берутся `FOR UPDATE` по возрастанию `id`;
    затем контексты — `items` и `extra_context_ids` — `FOR UPDATE` по `id`
    одним запросом;
    (4) пары семей контекстов перечитываются под блокировкой. Разошлись с
    прочитанным — откат к точке сохранения (блокировки сняты), повторное
    чтение и ПОЛНЫЙ перезахват. Повтор один. Варианты не перечитываются:
    они читаются под замком семей и устойчивы. Расхождение и на повторе: новых блокировок не берётся;
    `release_on_failure=True` откатывает точку сохранения (одиночное решение —
    блокировки сняты), `False` оставляет захваченное (группа — прочие
    предложения продолжают).

    `lock_variants` нужен путям, снимающим вариант с контекста: порядок
    «семья -> вариант -> контекст» тот же, что у обработчика значений.
    `exclusive_families` берёт семьи `FOR UPDATE` вместо `FOR SHARE` — для
    путей, меняющих семью, её схему или варианты (глобальная пометка строки).

    Возвращает контексты, чья пара семей так и не устоялась (пусто — захват
    удался)."""
    context_ids = sorted({context_id for context_id, _ in items})
    lock_context_ids = sorted(set(context_ids) | set(extra_context_ids))
    requested = {family_id for _, family_id in items if family_id is not None}
    unstable: set[int] = set()
    for attempt in (1, 2):
        seen = _read_family_pairs(db, context_ids)
        savepoint = db.begin_nested()
        family_ids = sorted(
            requested
            | {family for pair in seen.values() for family in pair if family is not None}
        )
        _lock_families(db, family_ids, exclusive=exclusive_families)
        variants: dict[int, int | None] = {}
        if lock_variants:
            variants = _read_variants(db, context_ids)
            _lock_variants(db, sorted({v for v in variants.values() if v is not None}))
        _lock_contexts(db, lock_context_ids)
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


def _acquire_single(
    db: Session, context_id: int, family_id: int | None, *, lock_variants: bool = False
) -> None:
    unstable = acquire_family_locks(
        db, [(context_id, family_id)], release_on_failure=True, lock_variants=lock_variants
    )
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
    if not _is_current(suggestion.request_hash, material):
        return None
    return suggestion


def _is_current(request_hash: str, material: ContextRequestMaterial | None) -> bool:
    """Контекст применим, и отпечаток его запроса (материал -> рендер) равен
    `request_hash` предложения."""
    if material is None or not is_applicable(material):
        return False
    return render_context_request(material, settings=settings).request_hash == request_hash


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
    (`other_family`, `family_created`) не трогаются. Перенаправление ожидания
    слиянием семей решения не меняет: семья, которую предлагало предложение, теперь
    и есть цель."""
    if state.suggestion_id is None or outcome == "redirected":
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
    db: Session, context: CatalogContext, *, actor_id: int | None,
    outcome: PendingOutcome = "superseded",
) -> int | None:
    """Прежнее ожидание вытеснено новым запросом: `outcome = superseded`; снятие
    семьи (`family_id=None`) не вытесняет ожидание другим, а снимает его —
    `cancelled`, как у `assign_family(None)` и «не работа». Возвращает
    предложение снятого ожидания."""
    state = clear_pending(db, context, outcome=outcome, actor_id=actor_id)
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

    `family_id=None` — снятие семьи, путь 1 и у контекста с вариантом: ожидание,
    вариант, значения и подсказка о расхождении путей уходят вместе с семьёй
    (спека §2.5, последний абзац; `assign_family(family_id=None)`).

    Raises:
        ValueError: несогласованные `actor_id`/`source`/`suggestion_id`/
            `threshold`.
        FamilyLockMismatch: семья контекста сменилась при захвате дважды.
        WorkFamilyError: контекст не найден или архивирован; семья не найдена,
            не `active` или с другой единицей."""
    _validate_call(
        family_id=family_id, actor_id=actor_id, source=source,
        suggestion_id=suggestion_id, threshold=threshold,
    )
    # Снятие семьи снимает и вариант: вариант берётся до контекста.
    _acquire_single(db, context_id, family_id, lock_variants=family_id is None)
    context = _load_context(db, context_id)
    route = family_change_route(context, family_id, source)

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

    superseded = _supersede(
        db, context, actor_id=actor_id,
        outcome="cancelled" if family_id is None else "superseded",
    )

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


def _catalog_row(db: Session, context_id: int) -> tuple[str, int | None]:
    """`(вид строки каталога, единица)` контекста."""
    row = db.execute(
        sa.select(CatalogPosition.kind, CatalogPosition.unit_id)
        .select_from(CatalogContext)
        .join(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
        .join(CatalogPosition, CatalogPosition.id == ContextBucket.catalog_position_id)
        .where(CatalogContext.id == context_id)
    ).one()
    return row.kind, row.unit_id


def _fitting_families(
    db: Session, pairs: Collection[tuple[int, int | None]]
) -> set[tuple[int, int | None]]:
    """Пары `(семья, единица контекста)`, для которых семья годится контексту:
    существует, `active` и её единица равна единице строки каталога - тот же
    предикат, что у `check_family_assignable`, но без отказа. Единицы
    сравниваются в Python, где `None == None` истинно."""
    family_ids = {family_id for family_id, _unit in pairs}
    if not family_ids:
        return set()
    active = {
        (row.id, row.unit_id)
        for row in db.execute(
            sa.select(WorkFamily.id, WorkFamily.unit_id).where(
                WorkFamily.id.in_(sorted(family_ids)),
                WorkFamily.status == FamilyStatus.active.value,
            )
        ).all()
    }
    return {pair for pair in pairs if pair in active}


@dataclass(frozen=True)
class Thresholds:
    """Пороги автопринятия по виду контекста (спека 3б §2.7): `work` - порог
    работ (и всех видов, кроме системы), `system` - порог систем. `None` -
    правило к контексту такого вида не применяется целиком."""

    work: Decimal | None
    system: Decimal | None


def thresholds_from(settings: Settings) -> Thresholds:
    """Оба порога из настроек."""
    return Thresholds(
        work=settings.SEMANTIC_AUTO_ACCEPT_THRESHOLD,
        system=settings.SEMANTIC_SYSTEM_AUTO_ACCEPT_THRESHOLD,
    )


def threshold_for(context, thresholds: Thresholds) -> Decimal | None:
    """Порог контекста по его виду: система - порог систем, прочие виды -
    порог работ. `context` - всё, у чего есть `semantic_kind`."""
    if context.semantic_kind == SemanticKind.SYSTEM.value:
        return thresholds.system
    return thresholds.work


#: Исходы таблицы публикации (спека §2.5): привязка подтверждена без смены
#: семьи; семья назначена сразу; поставлено ожидающее назначение; правило
#: предложение не трогает.
RULE_CONFIRM = "confirm"
RULE_ASSIGN = "assign"
RULE_PENDING = "pending"
RULE_NONE = "none"

#: Что должен вернуть `request_family_change` на исход правила.
_ROUTE_OF_OUTCOME = {RULE_ASSIGN: "assigned", RULE_PENDING: "pending"}


def rule_outcome(
    *,
    family_id: int,
    confidence: Decimal,
    context,
    threshold: Decimal,
    family_fits: bool,
) -> str:
    """Исход строки таблицы публикации (спека §2.5) для предложения семьи
    `family_id` и состояния контекста - единственное место, где он считается:
    правило публикации и массовое автопринятие зовут её же. `context` - всё,
    у чего есть `work_family_id`, `family_source`, `work_variant_id`,
    `pending_family_id`, `pending_family_source`. `family_fits` - семья годится
    контексту (активна, единица та же); негодная семья (например, слитая или
    заархивированная после ответа) правилом не применяется."""
    auto_source = FamilySource.auto_suggestion.value
    if context.work_family_id == family_id:
        return RULE_CONFIRM
    if confidence < threshold:
        return RULE_NONE
    if context.work_family_id is not None and context.family_source != auto_source:
        return RULE_NONE  # привязка человека: очередь «смена семьи»
    route = family_change_route(context, family_id, auto_source)
    if route == "unchanged" or not family_fits:
        return RULE_NONE  # ожидание человека правилом не вытесняется
    return RULE_PENDING if route == "pending" else RULE_ASSIGN


def _apply_outcome(
    db: Session,
    suggestion: FamilySuggestion,
    context: CatalogContext,
    outcome: str,
    threshold: Decimal,
) -> FamilyChangeOutcome | None:
    """Применяет исход `rule_outcome` под блокировками. `None` - правило
    предложения не трогает: оно остаётся опубликованным человеку."""
    if outcome == RULE_NONE:
        return None
    family_id = suggestion.family_id
    assert family_id is not None
    auto_source = FamilySource.auto_suggestion.value

    if outcome == RULE_CONFIRM:
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

    _record_auto_decision(
        suggestion,
        SuggestionDecision.auto_pending
        if outcome == RULE_PENDING
        else SuggestionDecision.auto_accepted,
    )
    db.flush()
    result = request_family_change(
        db,
        context_id=context.id,
        family_id=family_id,
        actor_id=None,
        source="auto_suggestion",
        suggestion_id=suggestion.id,
        threshold=threshold,
    )
    if result.kind != _ROUTE_OF_OUTCOME[outcome]:
        raise RuntimeError(
            f"исход правила {outcome!r} разошёлся с путём смены семьи {result.kind!r}"
        )
    return result


def _apply_rules_locked(
    db: Session,
    suggestion: FamilySuggestion,
    context: CatalogContext,
    threshold: Decimal,
    *,
    unit_id: int | None,
) -> FamilyChangeOutcome | None:
    """Таблица публикации (спека §2.5) под блокировками."""
    family_id = suggestion.family_id
    assert family_id is not None
    outcome = rule_outcome(
        family_id=family_id,
        confidence=suggestion.confidence,
        context=context,
        threshold=threshold,
        family_fits=(family_id, unit_id) in _fitting_families(db, [(family_id, unit_id)]),
    )
    return _apply_outcome(db, suggestion, context, outcome, threshold)


def apply_publication_rules(
    db: Session, *, suggestion_id: int, thresholds: Thresholds
) -> FamilyChangeOutcome | None:
    """Правила публикации по только что записанному предложению — отдельная
    транзакция после `commit` записи ответа (спека §2.5): блокировки (семьи по
    `id` `FOR SHARE`, контекст `FOR UPDATE`) -> предложение `FOR UPDATE` с
    перепроверкой (опубликовано, решения нет, отпечаток текущий) -> таблица.
    Коммитит сама; ошибка откатывает только эту транзакцию — ответ модели уже
    записан, предложение остаётся опубликованным.

    Порог берётся по виду контекста, прочитанному под блокировкой: вид мог
    смениться после ответа модели (спека 3б §2.7).

    `None` — предложение не тронуто: порога нет, ответ без «своей» семьи,
    предложение изменилось или решено, контекст не применим либо строка
    каталога не работа, семья контекста сменилась при захвате, либо таблица
    оставила его человеку."""
    if thresholds.work is None and thresholds.system is None:
        return None
    try:
        outcome = _apply_publication_rules(db, suggestion_id, thresholds)
        db.commit()
        return outcome
    except FamilyLockMismatch:
        db.rollback()
        return None
    except BaseException:
        db.rollback()
        raise


def _apply_publication_rules(
    db: Session, suggestion_id: int, thresholds: Thresholds
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
    kind, unit_id = _catalog_row(db, context.id)
    if kind not in _APPLICABLE_CATALOG_KINDS:
        return None
    threshold = threshold_for(context, thresholds)
    if threshold is None:
        return None
    return _apply_rules_locked(db, suggestion, context, threshold, unit_id=unit_id)


# ---------------------------------------------------------------------------
#  Массовое автопринятие (спека §2.12)
# ---------------------------------------------------------------------------

CODE_THRESHOLD_MISSING = "threshold_missing"
CODE_PREVIEW_CHANGED = "preview_changed"

#: Порядок исходов в показе: сначала те, что что-то меняют.
_OUTCOMES = (RULE_CONFIRM, RULE_ASSIGN, RULE_PENDING, RULE_NONE)


class AutoAcceptError(Exception):
    """Массовое автопринятие отказано. `code` - `threshold_missing` (порог не
    задан) либо `preview_changed` (состояние изменилось после показа; `409`).
    Сервис не коммитит и ничего не применил к этому моменту: вызывающий
    откатывает транзакцию."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class AutoAcceptPreview:
    """`by_outcome` - число кандидатов по каждому исходу (в том числе тех,
    кого правило не трогает: их состояние тоже входит в хэш); `total` - число
    кандидатов. `threshold` - порог работ, `system_threshold` - порог систем;
    кандидатами считаются только контексты вида, чей порог задан."""

    preview_hash: str
    threshold: Decimal | None
    system_threshold: Decimal | None
    by_outcome: Mapping[str, int]
    total: int


@dataclass(frozen=True)
class _Candidate:
    suggestion_id: int
    request_hash: str
    family_id: int
    context_id: int
    semantic_kind: str
    outcome: str
    work_family_id: int | None
    family_source: str | None
    work_variant_id: int | None
    pending_family_id: int | None
    pending_family_source: str | None


def _require_thresholds() -> Thresholds:
    thresholds = thresholds_from(settings)
    if thresholds.work is None and thresholds.system is None:
        raise AutoAcceptError(
            CODE_THRESHOLD_MISSING,
            "пороги автопринятия SEMANTIC_AUTO_ACCEPT_THRESHOLD и "
            "SEMANTIC_SYSTEM_AUTO_ACCEPT_THRESHOLD не заданы",
        )
    return thresholds


def _load_candidates(db: Session, thresholds: Thresholds) -> list[_Candidate]:
    """Опубликованные предложения «своей» семьи без решения с текущим
    отпечатком - те же условия, что у перепроверки правила публикации
    (`lock_and_recheck_suggestion`): контекст не архивирован и применим, строка
    каталога - работа. Сюда попадают и предложения, не принятые из-за сбоя между
    записью ответа и транзакцией правил. Контекст вида с незаданным порогом
    кандидатом не считается: правило к нему не применяется. Чтение без
    блокировок, строки по `suggestion_id`."""
    rows = db.execute(
        sa.select(
            FamilySuggestion.id,
            FamilySuggestion.request_hash,
            FamilySuggestion.family_id,
            FamilySuggestion.confidence,
            FamilySuggestion.context_id,
            CatalogContext.work_family_id,
            CatalogContext.family_source,
            CatalogContext.work_variant_id,
            CatalogContext.pending_family_id,
            CatalogContext.pending_family_source,
            CatalogContext.semantic_kind,
            CatalogPosition.unit_id,
        )
        .join(CatalogContext, CatalogContext.id == FamilySuggestion.context_id)
        .join(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
        .join(CatalogPosition, CatalogPosition.id == ContextBucket.catalog_position_id)
        .where(
            FamilySuggestion.is_published.is_(True),
            FamilySuggestion.decision.is_(None),
            FamilySuggestion.family_id.is_not(None),
            CatalogPosition.kind.in_(_APPLICABLE_CATALOG_KINDS),
        )
        .order_by(FamilySuggestion.id)
    ).all()
    if not rows:
        return []
    material = load_request_material(db, sorted({row.context_id for row in rows}))
    current = [
        row
        for row in rows
        if threshold_for(row, thresholds) is not None
        and _is_current(row.request_hash, material.get(row.context_id))
    ]
    fitting = _fitting_families(db, {(row.family_id, row.unit_id) for row in current})
    return [
        _Candidate(
            suggestion_id=row.id,
            request_hash=row.request_hash,
            family_id=row.family_id,
            context_id=row.context_id,
            semantic_kind=row.semantic_kind,
            outcome=rule_outcome(
                family_id=row.family_id,
                confidence=row.confidence,
                context=row,
                threshold=threshold_for(row, thresholds),
                family_fits=(row.family_id, row.unit_id) in fitting,
            ),
            work_family_id=row.work_family_id,
            family_source=row.family_source,
            work_variant_id=row.work_variant_id,
            pending_family_id=row.pending_family_id,
            pending_family_source=row.pending_family_source,
        )
        for row in current
    ]


def _threshold_text(threshold: Decimal | None) -> str | None:
    return None if threshold is None else str(threshold)


def _preview_hash(thresholds: Thresholds, candidates: Sequence[_Candidate]) -> str:
    """sha256 канонического JSON по обоим порогам и отсортированным кортежам
    спеки §2.12 (вид контекста добавлен): `(suggestion_id, request_hash, семья
    предложения, вид контекста, исход, work_family_id, family_source,
    work_variant_id, pending_family_id, pending_family_source)`."""
    canonical = json.dumps(
        {
            "threshold": _threshold_text(thresholds.work),
            "system_threshold": _threshold_text(thresholds.system),
            "rows": [
                [
                    c.suggestion_id, c.request_hash, c.family_id, c.semantic_kind, c.outcome,
                    c.work_family_id,
                    c.family_source, c.work_variant_id, c.pending_family_id,
                    c.pending_family_source,
                ]
                for c in candidates
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def preview_auto_accept(db: Session) -> AutoAcceptPreview:
    """Что сделает `apply_auto_accept` с порогом из настройки: число кандидатов
    по исходам таблицы публикации и хэш их состояния. Ничего не пишет и не
    блокирует.

    Raises:
        AutoAcceptError: ни один порог не задан (`threshold_missing`)."""
    thresholds = _require_thresholds()
    candidates = _load_candidates(db, thresholds)
    by_outcome = {outcome: 0 for outcome in _OUTCOMES}
    for candidate in candidates:
        by_outcome[candidate.outcome] += 1
    return AutoAcceptPreview(
        preview_hash=_preview_hash(thresholds, candidates),
        threshold=thresholds.work,
        system_threshold=thresholds.system,
        by_outcome=by_outcome,
        total=len(candidates),
    )


def _candidate_keys(candidates: Sequence[_Candidate]) -> set[tuple[int, int, int]]:
    return {(c.suggestion_id, c.family_id, c.context_id) for c in candidates}


def apply_auto_accept(db: Session, *, preview_hash: str) -> Mapping[str, int]:
    """Применяет таблицу публикации (спека §2.5) ко всем кандидатам одной
    транзакцией вызывающего. Порядок: (1) кандидаты читаются без блокировок;
    (2) все затронутые семьи - текущие, ожидаемые, предложенные - `FOR SHARE`
    одним запросом по возрастанию `id`; (3) контексты `FOR UPDATE` по
    возрастанию `id` (оба шага - `acquire_family_locks`); (4) предложения
    `FOR UPDATE`; (5) кандидаты и хэш считаются заново под блокировками -
    не совпал с `preview_hash`: отказ, ничего не применено; (6) строки таблицы
    применяются теми же путями, что у правила публикации, сверка очереди - одна,
    в конце, до `commit` вызывающего. Повышения `FOR SHARE` до `FOR UPDATE`
    нет. Автопринятие пишется без автора, как правило публикации.

    Возвращает число применённых по исходу (`confirm`, `assign`, `pending`).

    Raises:
        AutoAcceptError: порог не задан; состояние изменилось после показа.
            Вызывающий откатывает транзакцию."""
    thresholds = _require_thresholds()
    first = _load_candidates(db, thresholds)
    suggestions: dict[int, FamilySuggestion] = {}
    if first:
        unstable = acquire_family_locks(
            db, [(c.context_id, c.family_id) for c in first], release_on_failure=True
        )
        if unstable:
            raise AutoAcceptError(CODE_PREVIEW_CHANGED, "семьи контекстов изменились при захвате")
        suggestions = {
            suggestion.id: suggestion
            for suggestion in db.execute(
                sa.select(FamilySuggestion)
                .where(FamilySuggestion.id.in_([c.suggestion_id for c in first]))
                .order_by(FamilySuggestion.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalars()
        }
    candidates = _load_candidates(db, thresholds)
    if _candidate_keys(candidates) != _candidate_keys(first) or (
        _preview_hash(thresholds, candidates) != preview_hash
    ):
        raise AutoAcceptError(CODE_PREVIEW_CHANGED, "состояние изменилось после показа")

    applied = {RULE_CONFIRM: 0, RULE_ASSIGN: 0, RULE_PENDING: 0}
    with deferred_reconcile(db):
        for candidate in candidates:
            if candidate.outcome == RULE_NONE:
                continue
            context = _load_context(db, candidate.context_id)
            threshold = threshold_for(context, thresholds)
            assert threshold is not None  # кандидаты - только контексты с заданным порогом
            _apply_outcome(
                db, suggestions[candidate.suggestion_id], context, candidate.outcome, threshold
            )
            applied[candidate.outcome] += 1
    return applied
