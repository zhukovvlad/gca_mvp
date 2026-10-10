"""Решения `admin` над очередью семантических предложений: предложения,
задержанные проверкой задания, preview и подтверждение перезапросов, удержанные
пачки, ручной повтор, снятие остановки, массовая постановка (спека
`2026-09-28-semantic-suggestions-design.md` §2.9, §2.10, §2.6).

Задача 12 фичи «Семантические предложения»
(`docs/superpowers/plans/2026-09-28-semantic-suggestions.md`). Каждая функция —
часть транзакции вызывающего: `commit` делает он, `DecisionConflict` (409)
он же превращает в откат и ответ — кроме отказа с `keep=True` (обновлённый набор
совпадений задержанного задания): его вызывающий фиксирует, а не откатывает.

Порядок блокировок решений по предложению единый с фичей 1 и со сверкой:
предложение читается без блокировки (узнать контекст и семью), затем все семьи
решения — запрашиваемая, текущая и ожидаемая семьи контекста — `FOR SHARE` по
возрастанию `id`, `FOR UPDATE` на контекст, `FOR UPDATE` на предложение и
ПЕРЕПРОВЕРКА — опубликовано, решения нет, текущий отпечаток контекста
(материал -> рендер) равен `request_hash` предложения, контекст применим.
Захват — общий помощник `family_change.acquire_family_locks` (попытка в точке
сохранения: текущая семья, прочитанная без блокировки, могла смениться).
Группа берёт семьи и контексты всей группы разом, по возрастанию `id`.

Смену семьи решения человека не делают сами: они идут через
`family_change.request_family_change` — контекст с вариантом получает ожидание
(`accepted_pending` у подтверждения), без варианта — назначение сразу.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from config import settings
from models import (
    CatalogContext,
    CatalogPosition,
    ContextBucket,
    FamilyParameterSchema,
    FamilySource,
    FamilySuggestion,
    ReconcileBatchSource,
    ReconcileBatchStatus,
    SchemaStatus,
    SemanticCancelReason,
    SemanticJob,
    SemanticJobKind,
    SemanticJobStatus,
    SemanticReconcileBatch,
    SemanticWorkerState,
    SuggestionDecision,
    SuggestionUnpublishedReason,
    WorkFamily,
)
from services.family_categories import require_category
from services.family_change import (
    acquire_family_locks,
    family_change_route,
    lock_and_recheck_suggestion,
    request_family_change,
)
from services.semantic_cost import RESERVE_FORMULA_VERSION, event_cap_from, tariffs_from
from services.semantic_privacy import PrivacyDictionary, build_privacy_dictionary, find_privacy_matches
from services.semantic_reconcile import (
    NO_CAP,
    Fingerprint,
    ReconcileReport,
    estimate_enqueue,
    families_awaiting_schema,
    get_or_create_held_batch,
    reconcile_family_schemas,
    reconcile_semantic_jobs,
)
from services.semantic_request import load_request_material
from services.semantic_worker import render_job_request, serialize_privacy_matches
from services.variant_request import load_schema_material, render_schema_request
from services.work_families import (
    REFUSE_DUPLICATE_ACTIVE_FAMILY,
    WorkFamilyError,
    _lock_families,
    activate_family,
    create_family,
)
from services.work_variants import cancel_building_version

__all__ = [
    "ConfirmReport",
    "DecisionConflict",
    "Preview",
    "approve_batch",
    "assign_other_family",
    "confirm_config_reask",
    "confirm_suggestions",
    "confirm_unit_reask",
    "create_family_from_suggestion",
    "decline_privacy_hold",
    "discard_batch",
    "backfill_family_schemas",
    "enqueue_all",
    "preview_batch",
    "preview_config_reask",
    "preview_unit_reask",
    "reject_suggestion",
    "release_privacy_hold",
    "release_unit_privacy_holds",
    "resume_worker",
    "retry_job",
]

CODE_SUGGESTION_CHANGED = "suggestion_changed"
CODE_JOB_CHANGED = "job_changed"
CODE_PREVIEW_CHANGED = "preview_changed"
CODE_BATCH_DECIDED = "batch_decided"
CODE_FAMILY_EXISTS = "family_exists"


class DecisionConflict(Exception):
    """Экран устарел: состояние изменилось между показом и решением (`409`).
    `code` — одно из `suggestion_changed | job_changed | preview_changed |
    batch_decided | family_exists`; у `family_exists` есть `family_id` уже
    существующей семьи. `keep` — отказ оставляет запись, сделанную сервисом до
    него (обновлённый набор задержанного задания): роутер коммитит, а не откатывает.
    Сам сервис не коммитит — коммитит вызывающий."""

    def __init__(
        self, code: str, message: str = "", *, family_id: int | None = None, keep: bool = False
    ) -> None:
        super().__init__(message or code)
        self.code = code
        self.family_id = family_id
        self.keep = keep


@dataclass(frozen=True)
class Preview:
    context_count: int
    reserve_usd: Decimal
    expected_cached_usd: Decimal
    preview_hash: str


@dataclass(frozen=True)
class ConfirmReport:
    confirmed: list[int]
    skipped: list[int]


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
#  Решения по предложению
# ---------------------------------------------------------------------------

def _read_suggestion(db: Session, suggestion_id: int) -> FamilySuggestion:
    suggestion = db.get(FamilySuggestion, suggestion_id)
    if suggestion is None:
        raise LookupError(f"предложение {suggestion_id} не найдено")
    return suggestion


def _lock_and_recheck_suggestion(db: Session, suggestion_id: int) -> FamilySuggestion | None:
    """`FOR UPDATE` на предложение и перепроверка; `None` — не прошло. Контекст
    к этому моменту уже заблокирован вызывающим."""
    return lock_and_recheck_suggestion(db, suggestion_id)


def _record_decision(
    suggestion: FamilySuggestion, decision: SuggestionDecision, actor_id: int
) -> None:
    suggestion.decision = decision.value
    suggestion.decided_by = actor_id
    suggestion.decided_at = _now()


def _lock_for_decision(
    db: Session, *, context_id: int, family_id: int | None, suggestion_id: int
) -> FamilySuggestion | None:
    """Семьи решения (`FOR SHARE`) -> контекст (`FOR UPDATE`) -> предложение с
    перепроверкой. Семья контекста сменилась между чтением и блокировкой и
    после повтора — `DecisionConflict` (`409`), блокировки попытки сняты."""
    unstable = acquire_family_locks(db, [(context_id, family_id)], release_on_failure=True)
    if unstable:
        raise DecisionConflict(CODE_SUGGESTION_CHANGED)
    return _lock_and_recheck_suggestion(db, suggestion_id)


def _assign_decided(
    db: Session,
    suggestion: FamilySuggestion,
    *,
    family_id: int,
    source: FamilySource,
    decision: SuggestionDecision,
    actor_id: int,
) -> None:
    """Решение записывается ДО смены семьи; смена идёт через
    `request_family_change`. Привязанный контекст применим (спека §2.5), поэтому
    решённое предложение остаётся опубликованным. У контекста с вариантом смена
    — ожидание, и подтверждение (`accepted`) записывается как
    `accepted_pending`: семья ещё не сменилась; «Другая семья» и «Завести
    семью» сохраняют своё решение."""
    context_id = suggestion.context_id
    suggestion_id = suggestion.id
    with db.begin_nested():
        context = db.execute(
            sa.select(CatalogContext)
            .where(CatalogContext.id == context_id)
            .execution_options(populate_existing=True)
        ).scalar_one()
        recorded = decision
        if (
            decision == SuggestionDecision.accepted
            and family_change_route(context, family_id, source.value) == "pending"
        ):
            recorded = SuggestionDecision.accepted_pending
        _record_decision(suggestion, recorded, actor_id)
        db.flush()
        request_family_change(
            db,
            context_id=context_id,
            family_id=family_id,
            actor_id=actor_id,
            source=source.value,  # type: ignore[arg-type]
            suggestion_id=suggestion_id if source == FamilySource.suggestion else None,
        )


def confirm_suggestions(
    db: Session, *, suggestion_ids: list[int], actor_id: int
) -> ConfirmReport:
    """«Подтвердить» строку или отмеченные в группе: семья предложения,
    `source = suggestion`, `decision = accepted` (`accepted_pending` у контекста
    с вариантом — смена ждёт значений). Одна транзакция на группу;
    предложение без семьи («новая»/«СИСТЕМА»), не прошедшее перепроверку или
    отвергнутое назначением — в `skipped`.

    Блокировки группы — один захват: семьи всей группы (запрашиваемые,
    текущие, ожидаемые) `FOR SHARE` по возрастанию `id`, затем контексты
    `FOR UPDATE`. Контекст, чья семья сменилась при захвате дважды, — в
    `skipped`."""
    unique_ids = list(dict.fromkeys(suggestion_ids))
    known: dict[int, FamilySuggestion] = {
        s.id: s
        for s in db.execute(
            sa.select(FamilySuggestion).where(FamilySuggestion.id.in_(unique_ids))
        ).scalars()
    }
    confirmed: list[int] = []
    skipped: list[int] = [sid for sid in unique_ids if sid not in known]

    plan = sorted(
        ((s.family_id, s.context_id, s.id) for s in known.values() if s.family_id is not None),
        key=lambda item: (item[0], item[1], item[2]),
    )
    skipped.extend(s.id for s in known.values() if s.family_id is None)

    unstable = acquire_family_locks(
        db, [(context_id, family_id) for family_id, context_id, _ in plan],
        release_on_failure=False,
    )
    for family_id, context_id, suggestion_id in plan:
        if context_id in unstable:
            skipped.append(suggestion_id)
            continue
        suggestion = _lock_and_recheck_suggestion(db, suggestion_id)
        if suggestion is None or suggestion.family_id != family_id:
            skipped.append(suggestion_id)
            continue
        try:
            _assign_decided(
                db,
                suggestion,
                family_id=family_id,
                source=FamilySource.suggestion,
                decision=SuggestionDecision.accepted,
                actor_id=actor_id,
            )
        except WorkFamilyError:
            skipped.append(suggestion_id)
            continue
        confirmed.append(suggestion_id)
    return ConfirmReport(confirmed=confirmed, skipped=skipped)


def reject_suggestion(db: Session, *, suggestion_id: int, actor_id: int) -> None:
    """«Отклонить»: назначения нет; `is_published = false`, причина `rejected`.
    Повторная сверка воскресить его не может: решение записано."""
    suggestion = _read_suggestion(db, suggestion_id)
    suggestion = _lock_for_decision(
        db, context_id=suggestion.context_id, family_id=None, suggestion_id=suggestion_id
    )
    if suggestion is None:
        raise DecisionConflict(CODE_SUGGESTION_CHANGED)
    _record_decision(suggestion, SuggestionDecision.rejected, actor_id)
    suggestion.is_published = False
    suggestion.unpublished_reason = SuggestionUnpublishedReason.rejected.value
    db.flush()


def assign_other_family(
    db: Session, *, suggestion_id: int, family_id: int, actor_id: int
) -> None:
    """«Другая семья…»: выбранная семья, `source = manual` — семью выбрал
    человек, `decision = other_family`."""
    suggestion = _read_suggestion(db, suggestion_id)
    suggestion = _lock_for_decision(
        db, context_id=suggestion.context_id, family_id=family_id, suggestion_id=suggestion_id
    )
    if suggestion is None:
        raise DecisionConflict(CODE_SUGGESTION_CHANGED)
    _assign_decided(
        db,
        suggestion,
        family_id=family_id,
        source=FamilySource.manual,
        decision=SuggestionDecision.other_family,
        actor_id=actor_id,
    )


def create_family_from_suggestion(
    db: Session,
    *,
    suggestion_id: int,
    title: str,
    definition: str,
    family_category_id: int,
    actor_id: int,
) -> int:
    """«Завести семью…»: одной транзакцией `create_family` -> `activate_family`
    -> `assign_family(source = suggestion)`, предложение `family_created`.
    Категория обязательна (спека 3б §2.9, решение 10) и берётся `FOR SHARE` ДО
    строк контекста и семьи — первой в общем порядке блокировок. Возвращает id
    новой семьи.

    Raises:
        ValueError: пустое имя или определение — до любой записи.
        WorkFamilyError: `category_not_found` — категорию удалили.
        DecisionConflict: `suggestion_changed`; `family_exists` (с `family_id`
            существующей активной семьи с тем же именем и единицей) — записано
            ничего, точка сохранения откатывается."""
    clean_title = (title or "").strip()
    clean_definition = (definition or "").strip()
    if not clean_title:
        raise ValueError("имя семьи обязательно")
    if not clean_definition:
        raise ValueError("определение семьи обязательно")

    require_category(db, family_category_id, exclusive=False)

    suggestion = _read_suggestion(db, suggestion_id)
    suggestion = _lock_for_decision(
        db, context_id=suggestion.context_id, family_id=None, suggestion_id=suggestion_id
    )
    if suggestion is None:
        raise DecisionConflict(CODE_SUGGESTION_CHANGED)

    unit_name = load_request_material(db, [suggestion.context_id])[suggestion.context_id].unit_code

    with db.begin_nested():
        try:
            family = create_family(
                db, title=clean_title, unit_name=unit_name, definition=clean_definition,
                actor_id=actor_id, family_category_id=family_category_id,
            )
            family_id = family.id
            activate_family(db, family_id=family_id, actor_id=actor_id)
        except WorkFamilyError as exc:
            if exc.code != REFUSE_DUPLICATE_ACTIVE_FAMILY:
                raise
            raise DecisionConflict(
                CODE_FAMILY_EXISTS,
                "такая семья уже есть",
                family_id=getattr(exc, "duplicate_family_id", None),
            ) from exc
        # Новая активная семья входит в кандидатов и меняет отпечаток контекста:
        # перепроверка уже сделана под блокировкой, здесь предложение только
        # перечитывается (активация сбросила кэш сессии).
        suggestion = db.get(FamilySuggestion, suggestion_id)
        _assign_decided(
            db,
            suggestion,
            family_id=family_id,
            source=FamilySource.suggestion,
            decision=SuggestionDecision.family_created,
            actor_id=actor_id,
        )
    return family_id


# ---------------------------------------------------------------------------
#  Задания, задержанные проверкой
# ---------------------------------------------------------------------------

def _lock_job(db: Session, job_id: int) -> SemanticJob:
    job = db.execute(
        sa.select(SemanticJob)
        .where(SemanticJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if job is None:
        raise LookupError(f"задание {job_id} не найдено")
    return job


def _hold_verdict(
    db: Session, job: SemanticJob, shown_matches: list[dict], dictionary: PrivacyDictionary
) -> tuple[bool, list[dict] | None]:
    """Перепроверка задержанного: `privacy_hold`; предмет задания по его виду
    применим (контекст или семья с версией `building`); текущий
    отпечаток равен `request_hash`; проверка по текущему словарю даёт показанный
    набор совпадений. Возвращает `(прошло, текущий_набор)`: набор отдан, только
    если единственное нарушение — расхождение набора (словарь изменился после
    задержания), иначе `None`."""
    if job.status != SemanticJobStatus.privacy_hold.value:
        return False, None
    # Предмет задания — по его виду (контекст или семья), как при захвате.
    rendered = render_job_request(db, job, settings=settings).rendered
    if rendered is None or rendered.request_hash != job.request_hash:
        return False, None
    current = serialize_privacy_matches(find_privacy_matches(dictionary, rendered))
    if current == shown_matches:
        return True, None
    return False, current


def _refresh_hold(job: SemanticJob, current: list[dict]) -> None:
    """Словарь изменился после задержания: в задание пишется текущий набор, чтобы
    следующее решение шло по нему, а не по несуществующему сохранённому. Набор
    пуст — совпадений больше нет, задание уходит в очередь, как «Отправить» без
    совпадений."""
    job.privacy_matches = current
    if not current:
        job.status = SemanticJobStatus.pending.value
        job.next_attempt_at = _now()


def _release_locked(job: SemanticJob, shown_matches: list[dict], actor_id: int) -> None:
    now = _now()
    job.status = SemanticJobStatus.pending.value
    job.privacy_released_matches = shown_matches
    job.privacy_decided_by = actor_id
    job.privacy_decided_at = now
    job.next_attempt_at = now


def _conflict_keeping_refresh(
    db: Session, job: SemanticJob, current: list[dict] | None
) -> None:
    """Отказ `job_changed`. Расхождение набора из-за словаря — запись текущего
    набора остаётся: отказ несёт `keep`, и роутер её коммитит, а не откатывает;
    иначе задание осталось бы с набором, по которому ни одно решение не пройдёт."""
    if current is not None:
        _refresh_hold(job, current)
        db.flush()
        raise DecisionConflict(CODE_JOB_CHANGED, keep=True)
    raise DecisionConflict(CODE_JOB_CHANGED)


def release_privacy_hold(
    db: Session, *, job_id: int, shown_matches: list[dict], actor_id: int
) -> None:
    """«Отправить»: задание -> `pending`, разрешённый набор = показанный."""
    job = _lock_job(db, job_id)
    ok, current = _hold_verdict(db, job, shown_matches, build_privacy_dictionary(db))
    if not ok:
        _conflict_keeping_refresh(db, job, current)
    _release_locked(job, shown_matches, actor_id)
    db.flush()


def _lock_job_for_decline(db: Session, job_id: int) -> SemanticJob:
    """Задание, которое может отменить версию схемы: порядок блокировок общий со
    сверкой — семья, версия, затем задание. Остальные виды — как `_lock_job`."""
    row = db.execute(
        sa.select(SemanticJob.kind, SemanticJob.family_id, SemanticJob.schema_id).where(
            SemanticJob.id == job_id
        )
    ).one_or_none()
    if row is not None and row.kind == SemanticJobKind.family_schema.value:
        _lock_families(db, [row.family_id], exclusive=True)
        db.execute(
            sa.select(FamilyParameterSchema.id)
            .where(FamilyParameterSchema.id == row.schema_id)
            .with_for_update()
        ).all()
    return _lock_job(db, job_id)


def _represented_elsewhere(
    db: Session, schema: FamilyParameterSchema, *, except_batch_id: int | None
) -> bool:
    """Версию `building` кто-то ещё представляет ЕЁ ТЕКУЩИМ отпечатком (версия и
    `request_hash` её текущего рендера): живое задание схемы
    (`pending`/`running`/`privacy_hold`) или другая пачка `held`/`approved` с
    таким отпечатком (спека §2.9, отмена пересборки). Отпечаток прежнего
    входа версию не держит."""
    current = render_schema_request(
        load_schema_material(db, schema.family_id, schema.id), settings=settings
    ).request_hash
    live = sa.select(SemanticJob.id).where(
        SemanticJob.kind == SemanticJobKind.family_schema.value,
        SemanticJob.schema_id == schema.id,
        SemanticJob.request_hash == current,
        SemanticJob.status.in_(
            (
                SemanticJobStatus.pending.value,
                SemanticJobStatus.running.value,
                SemanticJobStatus.privacy_hold.value,
            )
        ),
    )
    if db.execute(live.limit(1)).first() is not None:
        return True
    batches = sa.select(SemanticReconcileBatch.id).where(
        SemanticReconcileBatch.status.in_(
            (ReconcileBatchStatus.held.value, ReconcileBatchStatus.approved.value)
        ),
        SemanticReconcileBatch.held_fingerprints.contains(
            [
                {
                    "kind": SemanticJobKind.family_schema.value,
                    "schema_id": schema.id,
                    "request_hash": current,
                }
            ]
        ),
    )
    if except_batch_id is not None:
        batches = batches.where(SemanticReconcileBatch.id != except_batch_id)
    return db.execute(batches.limit(1)).first() is not None


def _cancel_unrepresented_versions(
    db: Session,
    schema_ids: Sequence[int],
    *,
    except_batch_id: int | None = None,
) -> None:
    """Версии `building` из `schema_ids`, чей отпечаток больше никто не
    представляет, -> `cancelled` (задание сверх потолка не создано или
    отклонено, а частичный UNIQUE запирал бы пересборку). Семьи блокируются по
    возрастанию `id`, затем версии — порядок сверки; вызывающий задание и пачку
    уже держит (пачка — не доменная строка). Это тот же переход, что отмена
    пересборки: незавершённые задания версии (`pending`, `privacy_hold`, `error`)
    отменяются как `not_applicable` в той же транзакции; `running` не трогается
    (его результат окажется `not_applicable`)."""
    ids = sorted(set(schema_ids))
    if not ids:
        return
    family_ids = sorted(
        set(
            db.execute(
                sa.select(FamilyParameterSchema.family_id).where(FamilyParameterSchema.id.in_(ids))
            ).scalars()
        )
    )
    _lock_families(db, family_ids, exclusive=True)
    building = db.execute(
        sa.select(FamilyParameterSchema)
        .where(
            FamilyParameterSchema.id.in_(ids),
            FamilyParameterSchema.status == SchemaStatus.building.value,
        )
        .order_by(FamilyParameterSchema.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalars()
    for schema in building:
        if _represented_elsewhere(db, schema, except_batch_id=except_batch_id):
            continue
        cancel_building_version(db, schema)
    db.flush()


def decline_privacy_hold(
    db: Session, *, job_id: int, shown_matches: list[dict], actor_id: int
) -> None:
    """«Не отправлять»: задание -> `cancelled` / `privacy_declined`. Задание
    схемы семьи отменяет и свою версию `building`, если её отпечаток больше
    никто не представляет."""
    job = _lock_job_for_decline(db, job_id)
    ok, current = _hold_verdict(db, job, shown_matches, build_privacy_dictionary(db))
    if not ok:
        _conflict_keeping_refresh(db, job, current)
    job.status = SemanticJobStatus.cancelled.value
    job.cancel_reason = SemanticCancelReason.privacy_declined.value
    job.privacy_decided_by = actor_id
    job.privacy_decided_at = _now()
    db.flush()
    if job.kind == SemanticJobKind.family_schema.value:
        _cancel_unrepresented_versions(db, [job.schema_id], except_batch_id=job.batch_id)


def release_unit_privacy_holds(
    db: Session, *, unit_id: int | None, shown_matches: list[dict], actor_id: int
) -> ConfirmReport:
    """«Отправить все K»: все `privacy_hold` задания единицы (`None` — задания
    без единицы и только они), у каждого своя перепроверка; прошедшие
    отправлены, прочие — в `skipped` (по `job_id`). Задание, у которого разошёлся
    только набор совпадений, пропускается с обновлённым набором."""
    unit_clause = SemanticJob.unit_id.is_(None) if unit_id is None else SemanticJob.unit_id == unit_id
    job_ids = list(
        db.execute(
            sa.select(SemanticJob.id)
            .where(SemanticJob.status == SemanticJobStatus.privacy_hold.value, unit_clause)
            .order_by(SemanticJob.id)
        ).scalars()
    )
    dictionary = build_privacy_dictionary(db)
    confirmed: list[int] = []
    skipped: list[int] = []
    for job_id in job_ids:
        job = _lock_job(db, job_id)
        ok, current = _hold_verdict(db, job, shown_matches, dictionary)
        if ok:
            _release_locked(job, shown_matches, actor_id)
            confirmed.append(job_id)
        else:
            if current is not None:
                _refresh_hold(job, current)
            skipped.append(job_id)
    db.flush()
    return ConfirmReport(confirmed=confirmed, skipped=skipped)


def retry_job(db: Session, *, job_id: int, actor_id: int) -> None:
    """Ручной повтор (спека §2.6): только из `error`; ТО ЖЕ задание -> `pending`
    с новым поколением. Попытки прежних поколений не трогаются. `done` с тем же
    отпечатком повторять нельзя."""
    job = _lock_job(db, job_id)
    if job.status != SemanticJobStatus.error.value:
        raise DecisionConflict(CODE_JOB_CHANGED)
    job.status = SemanticJobStatus.pending.value
    job.retry_generation = job.retry_generation + 1
    job.attempts_in_generation = 0
    job.next_attempt_at = _now()
    job.last_error_class = None
    db.flush()


# ---------------------------------------------------------------------------
#  Preview и подтверждение перезапросов
# ---------------------------------------------------------------------------

def _tariffs_of_kinds(kinds: Sequence[SemanticJobKind | str]) -> dict[str, dict[str, str]]:
    """Тарифы каждого вида из набора — по виду, а не одни тарифы предложений:
    резерв каждого задания считается тарифами его вида, и preview обязан
    менять хэш при смене тарифа любого вида набора. Тарифы предложений входят
    всегда, и у пустого набора."""
    result: dict[str, dict[str, str]] = {}
    present = {SemanticJobKind(kind).value for kind in kinds}
    for kind in sorted(present | {SemanticJobKind.family_suggestion.value}):
        tariffs = tariffs_from(settings, SemanticJobKind(kind))
        result[kind] = {
            "input_per_m": str(tariffs.input_per_m),
            "cache_write_per_m": str(tariffs.cache_write_per_m),
            "cache_read_per_m": str(tariffs.cache_read_per_m),
            "output_per_m": str(tariffs.output_per_m),
        }
    return result


def _preview_and_pairs(
    db: Session, context_ids: Sequence[int], *, family_ids: Sequence[int] = ()
) -> tuple[Preview, list[Fingerprint]]:
    pairs, reserve, cached = estimate_enqueue(db, context_ids, family_ids)
    canonical = json.dumps(
        {
            "fingerprints": [fingerprint.as_dict() for fingerprint in pairs],
            "reserve_usd": str(reserve),
            "cached_usd": str(cached),
            "tariffs": _tariffs_of_kinds([fingerprint.kind for fingerprint in pairs]),
            "reserve_formula_version": RESERVE_FORMULA_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    preview = Preview(
        context_count=len(pairs),
        reserve_usd=reserve,
        expected_cached_usd=cached,
        preview_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )
    return preview, pairs


def _preview_of(db: Session, context_ids: Sequence[int]) -> Preview:
    return _preview_and_pairs(db, context_ids)[0]


def _unit_context_ids(db: Session, unit_id: int | None) -> list[int]:
    """Все контексты единицы каталожной строки; `None` — контексты без
    единицы и только они."""
    unit_clause = CatalogPosition.unit_id.is_(None) if unit_id is None else CatalogPosition.unit_id == unit_id
    return list(
        db.execute(
            sa.select(CatalogContext.id)
            .join(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
            .join(CatalogPosition, CatalogPosition.id == ContextBucket.catalog_position_id)
            .where(unit_clause)
            .order_by(CatalogContext.id)
        ).scalars()
    )


def _all_live_context_ids(db: Session) -> list[int]:
    return list(
        db.execute(
            sa.select(CatalogContext.id)
            .where(CatalogContext.archived_at.is_(None))
            .order_by(CatalogContext.id)
        ).scalars()
    )


def _confirm(
    db: Session, context_ids: Sequence[int], preview_hash: str, *, source: str
) -> ReconcileReport:
    # Хэш перепроверяется заново, а сверка сама загружает вход: общий снимок
    # между ними пропустил бы вход и задание, закоммиченные параллельным
    # импортом, и сверка отменила бы свежее задание как устаревшее.
    if _preview_and_pairs(db, context_ids)[0].preview_hash != preview_hash:
        raise DecisionConflict(CODE_PREVIEW_CHANGED)
    return reconcile_semantic_jobs(db, context_ids, cap=NO_CAP, source=source)


def preview_unit_reask(db: Session, *, unit_id: int | None) -> Preview:
    return _preview_of(db, _unit_context_ids(db, unit_id))


def confirm_unit_reask(
    db: Session, *, unit_id: int | None, preview_hash: str, actor_id: int
) -> ReconcileReport:
    return _confirm(db, _unit_context_ids(db, unit_id), preview_hash, source="unit_reask")


def preview_config_reask(db: Session) -> Preview:
    return _preview_of(db, _all_live_context_ids(db))


def confirm_config_reask(db: Session, *, preview_hash: str, actor_id: int) -> ReconcileReport:
    return _confirm(db, _all_live_context_ids(db), preview_hash, source="config_reask")


# ---------------------------------------------------------------------------
#  Удержанные пачки
# ---------------------------------------------------------------------------

def _batch_context_ids(batch: SemanticReconcileBatch) -> list[int]:
    """Контексты отпечатков предложений и значений; отпечатки схем семей —
    предмет `_batch_family_ids`."""
    return sorted(
        {
            int(element["context_id"])
            for element in batch.held_fingerprints
            if element["kind"] != SemanticJobKind.family_schema.value
        }
    )


def _batch_family_ids(batch: SemanticReconcileBatch) -> list[int]:
    """Семьи отпечатков схем: подтверждение сверяет схемы по семьям."""
    return sorted(
        {
            int(element["family_id"])
            for element in batch.held_fingerprints
            if element["kind"] == SemanticJobKind.family_schema.value
        }
    )


def _batch_schema_ids(batch: SemanticReconcileBatch) -> list[int]:
    """Версии схем отпечатков схем; у отпечатка, поставленного до заведения
    версии, `schema_id` пуст — такой версии отменять нечего."""
    return sorted(
        {
            int(element["schema_id"])
            for element in batch.held_fingerprints
            if element["kind"] == SemanticJobKind.family_schema.value
            and element["schema_id"] is not None
        }
    )


def _lock_batch(db: Session, batch_id: int) -> SemanticReconcileBatch:
    batch = db.execute(
        sa.select(SemanticReconcileBatch)
        .where(SemanticReconcileBatch.id == batch_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if batch is None:
        raise LookupError(f"пачка {batch_id} не найдена")
    return batch


def preview_batch(db: Session, *, batch_id: int) -> Preview:
    """Preview по ТЕКУЩИМ отпечаткам контекстов пачки (сохранённые в пачке —
    аудит удержанного)."""
    batch = db.get(SemanticReconcileBatch, batch_id)
    if batch is None:
        raise LookupError(f"пачка {batch_id} не найдена")
    return _preview_and_pairs(
        db, _batch_context_ids(batch), family_ids=_batch_family_ids(batch)
    )[0]


def approve_batch(
    db: Session, *, batch_id: int, preview_hash: str, actor_id: int
) -> ReconcileReport:
    """«Поставить…»: `held -> approved` под `FOR UPDATE` на пачку и сверка
    предметов пачки без потолка: предложения и значения — по контекстам, схемы —
    по семьям. Суточный бюджет остаётся в силе."""
    batch = _lock_batch(db, batch_id)
    if batch.status != ReconcileBatchStatus.held.value:
        raise DecisionConflict(CODE_BATCH_DECIDED)
    context_ids = _batch_context_ids(batch)
    family_ids = _batch_family_ids(batch)
    preview, pairs = _preview_and_pairs(db, context_ids, family_ids=family_ids)
    if preview.preview_hash != preview_hash:
        raise DecisionConflict(CODE_PREVIEW_CHANGED)
    batch.status = ReconcileBatchStatus.approved.value
    batch.decided_by = actor_id
    batch.decided_at = _now()
    db.flush()
    report = _merged(
        reconcile_semantic_jobs(db, context_ids, cap=NO_CAP, source=batch.source),
        reconcile_family_schemas(db, family_ids, cap=NO_CAP, source=batch.source),
    )
    _mark_batch_jobs(db, batch.id, pairs)
    return report


def _merged(first: ReconcileReport, second: ReconcileReport) -> ReconcileReport:
    return ReconcileReport(
        created=first.created + second.created,
        revived=first.revived + second.revived,
        cancelled=first.cancelled + second.cancelled,
        republished=first.republished + second.republished,
        unpublished=first.unpublished + second.unpublished,
        held_batch_id=first.held_batch_id or second.held_batch_id,
    )


def _mark_batch_jobs(db: Session, batch_id: int, pairs: Sequence[Fingerprint]) -> None:
    """Задания отпечатков пачки получают `batch_id`: предложения — по контексту и
    хэшу, значения — по контексту, версии и хэшу, схема — по семье и хэшу (версия,
    если отпечаток её уже нёс). Каждый вид сопоставляется со своим видом
    заданий."""
    suggestion: list[tuple[int, str]] = []
    values: list[tuple[int, int, str]] = []
    schema: list[tuple[int, int, str]] = []
    schema_unbuilt: list[tuple[int, str]] = []
    for fingerprint in pairs:
        kind = SemanticJobKind(fingerprint.kind)
        if kind == SemanticJobKind.family_discovery:
            # Открытие создаёт только запуск оператора; в пачки сверки оно не входит.
            continue
        if kind == SemanticJobKind.family_suggestion:
            suggestion.append((fingerprint.context_id, fingerprint.request_hash))
        elif kind == SemanticJobKind.context_values:
            values.append((fingerprint.context_id, fingerprint.schema_id, fingerprint.request_hash))
        elif fingerprint.schema_id is not None:
            schema.append((fingerprint.family_id, fingerprint.schema_id, fingerprint.request_hash))
        else:
            schema_unbuilt.append((fingerprint.family_id, fingerprint.request_hash))
    clauses = []
    if suggestion:
        clauses.append(
            sa.tuple_(SemanticJob.context_id, SemanticJob.request_hash).in_(suggestion)
        )
    if values:
        clauses.append(
            sa.tuple_(
                SemanticJob.context_id, SemanticJob.schema_id, SemanticJob.request_hash
            ).in_(values)
        )
    if schema:
        clauses.append(
            sa.tuple_(
                SemanticJob.family_id, SemanticJob.schema_id, SemanticJob.request_hash
            ).in_(schema)
        )
    if schema_unbuilt:
        clauses.append(
            sa.tuple_(SemanticJob.family_id, SemanticJob.request_hash).in_(schema_unbuilt)
        )
    if clauses:
        db.execute(sa.update(SemanticJob).where(sa.or_(*clauses)).values(batch_id=batch_id))


def discard_batch(db: Session, *, batch_id: int, actor_id: int) -> None:
    """«Отбросить»: `held -> discarded`; второе решение — `batch_decided`.
    Версии `building`, чьи отпечатки схем были в пачке и больше нигде не
    представлены, отменяются."""
    batch = _lock_batch(db, batch_id)
    if batch.status != ReconcileBatchStatus.held.value:
        raise DecisionConflict(CODE_BATCH_DECIDED)
    batch.status = ReconcileBatchStatus.discarded.value
    batch.decided_by = actor_id
    batch.decided_at = _now()
    db.flush()
    _cancel_unrepresented_versions(db, _batch_schema_ids(batch), except_batch_id=batch.id)
    _reconcile_schemas_after_discard(db, batch)


def _reconcile_schemas_after_discard(db: Session, batch: SemanticReconcileBatch) -> None:
    """Удержанная пачка предложений не давала строить схемы своих единиц (`mass`
    — всех, спека §2.6); после отбрасывания блокировки нет, а задания
    предложений из пачки не создавались и сверку схем не вызовут. Семьи без
    схемы затронутых единиц сверяются здесь."""
    contexts = {
        int(element["context_id"])
        for element in batch.held_fingerprints
        if element["kind"] == SemanticJobKind.family_suggestion.value
    }
    if not contexts:
        return
    units: set[int | None] | None = None
    if batch.source != ReconcileBatchSource.mass.value:
        units = set(
            db.execute(
                sa.select(CatalogPosition.unit_id)
                .select_from(CatalogContext)
                .join(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
                .join(CatalogPosition, CatalogPosition.id == ContextBucket.catalog_position_id)
                .where(CatalogContext.id.in_(contexts))
                .distinct()
            )
            .scalars()
            .all()
        )
    family_ids = families_awaiting_schema(db, unit_ids=units)
    if family_ids:
        reconcile_family_schemas(
            db, family_ids, cap=event_cap_from(settings), source="operation"
        )


# ---------------------------------------------------------------------------
#  Остановка захвата, массовая постановка
# ---------------------------------------------------------------------------

def resume_worker(db: Session, *, actor_id: int) -> None:
    """Снять остановку захвата: причина, попытка и время очищены, автор и время
    снятия записаны. Захват не остановлен — ничего не пишем (повторное нажатие
    не ошибка)."""
    state = db.execute(
        sa.select(SemanticWorkerState)
        .where(SemanticWorkerState.id == 1)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if not state.claim_paused:
        return
    state.claim_paused = False
    state.paused_reason = None
    state.paused_attempt_id = None
    state.paused_at = None
    state.last_resumed_by = actor_id
    state.last_resumed_at = _now()
    db.flush()


def enqueue_all(db: Session) -> int | None:
    """Массовая постановка (CLI `semantic-enqueue-all`): набор постановки по
    всем неархивным контекстам ВСЕГДА записывается удержанной пачкой `mass`,
    даже под потолком, — поставить может только `admin` с экрана. Ставить
    нечего — `None`, пачки нет. Отмен не делает."""
    pairs, reserve, cached = estimate_enqueue(db, _all_live_context_ids(db))
    if not pairs:
        return None
    return get_or_create_held_batch(
        db,
        fingerprints=pairs,
        source="mass",
        import_job_id=None,
        unit_id=None,
        reserve_estimate_usd=reserve,
        cached_estimate_usd=cached,
    )


def backfill_family_schemas(db: Session) -> ReconcileReport:
    """Массовая постановка схем (CLI `semantic-schemas-backfill`): сверка ветви
    `family_schema` по всем семьям, источник пачки `mass`. Схему получают
    активные семьи без текущей версии: ветвь плана пропускает семью с текущей
    версией и неактивную. Постановка идёт под потолком события
    из настроек, сверх него - ОДНА удержанная пачка `mass` в `report.held_batch_id`."""
    family_ids = db.execute(sa.select(WorkFamily.id)).scalars().all()
    return reconcile_family_schemas(
        db, family_ids, cap=event_cap_from(settings), source="mass"
    )
