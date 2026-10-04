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
предложение читается без блокировки (узнать контекст и семью), затем `FOR SHARE`
на семью (если назначается), `FOR UPDATE` на контекст, `FOR UPDATE` на
предложение и ПЕРЕПРОВЕРКА — опубликовано, решения нет, текущий отпечаток
контекста (материал -> рендер) равен `request_hash` предложения, контекст
применим. Группа берёт предложения в порядке `(family_id, context_id)`.
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
    FamilySource,
    FamilySuggestion,
    ReconcileBatchStatus,
    SemanticCancelReason,
    SemanticJob,
    SemanticJobKind,
    SemanticJobStatus,
    SemanticReconcileBatch,
    SemanticWorkerState,
    SuggestionDecision,
    SuggestionUnpublishedReason,
)
from services.semantic_cost import RESERVE_FORMULA_VERSION, tariffs_from
from services.semantic_privacy import PrivacyDictionary, build_privacy_dictionary, find_privacy_matches
from services.semantic_reconcile import (
    NO_CAP,
    Fingerprint,
    ReconcileReport,
    estimate_enqueue,
    get_or_create_held_batch,
    reconcile_semantic_jobs,
)
from services.semantic_request import is_applicable, load_request_material, render_context_request
from services.semantic_worker import serialize_privacy_matches
from services.work_families import (
    REFUSE_DUPLICATE_ACTIVE_FAMILY,
    WorkFamilyError,
    _lock_contexts,
    _lock_families,
    activate_family,
    assign_family,
    create_family,
)

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


def _record_decision(
    suggestion: FamilySuggestion, decision: SuggestionDecision, actor_id: int
) -> None:
    suggestion.decision = decision.value
    suggestion.decided_by = actor_id
    suggestion.decided_at = _now()


def _lock_for_decision(
    db: Session, *, context_id: int, family_id: int | None, suggestion_id: int
) -> FamilySuggestion | None:
    """Семья (`FOR SHARE`) -> контекст (`FOR UPDATE`) -> предложение с
    перепроверкой."""
    if family_id is not None:
        _lock_families(db, [family_id], exclusive=False)
    _lock_contexts(db, [context_id])
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
    """Решение записывается ДО назначения: сверка внутри `assign_family` снимет
    публикацию с причиной `context_not_applicable`, и это допустимо."""
    context_id = suggestion.context_id
    suggestion_id = suggestion.id
    with db.begin_nested():
        _record_decision(suggestion, decision, actor_id)
        db.flush()
        assign_family(
            db,
            context_id=context_id,
            family_id=family_id,
            actor_id=actor_id,
            source=source,
            suggestion_id=suggestion_id if source == FamilySource.suggestion else None,
        )


def confirm_suggestions(
    db: Session, *, suggestion_ids: list[int], actor_id: int
) -> ConfirmReport:
    """«Подтвердить» строку или отмеченные в группе: семья предложения,
    `source = suggestion`, `decision = accepted`. Одна транзакция на группу;
    предложение без семьи («новая»/«СИСТЕМА»), не прошедшее перепроверку или
    отвергнутое назначением — в `skipped`."""
    unique_ids = list(dict.fromkeys(suggestion_ids))
    known: dict[int, FamilySuggestion] = {
        s.id: s
        for s in db.execute(
            sa.select(FamilySuggestion).where(FamilySuggestion.id.in_(unique_ids))
        ).scalars()
    }
    confirmed: list[int] = []
    skipped: list[int] = [sid for sid in unique_ids if sid not in known]

    # Порядок блокировок группы — `(family_id, context_id)`.
    plan = sorted(
        ((s.family_id, s.context_id, s.id) for s in known.values() if s.family_id is not None),
        key=lambda item: (item[0], item[1], item[2]),
    )
    skipped.extend(s.id for s in known.values() if s.family_id is None)

    for family_id, context_id, suggestion_id in plan:
        suggestion = _lock_for_decision(
            db, context_id=context_id, family_id=family_id, suggestion_id=suggestion_id
        )
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
    db: Session, *, suggestion_id: int, title: str, definition: str, actor_id: int
) -> int:
    """«Завести семью…»: одной транзакцией `create_family` -> `activate_family`
    -> `assign_family(source = suggestion)`, предложение `family_created`.
    Возвращает id новой семьи.

    Raises:
        ValueError: пустое имя или определение — до любой записи.
        DecisionConflict: `suggestion_changed`; `family_exists` (с `family_id`
            существующей активной семьи с тем же именем и единицей) — записано
            ничего, точка сохранения откатывается."""
    clean_title = (title or "").strip()
    clean_definition = (definition or "").strip()
    if not clean_title:
        raise ValueError("имя семьи обязательно")
    if not clean_definition:
        raise ValueError("определение семьи обязательно")

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
                actor_id=actor_id,
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
    """Перепроверка задержанного: `privacy_hold`; контекст применим; текущий
    отпечаток равен `request_hash`; проверка по текущему словарю даёт показанный
    набор совпадений. Возвращает `(прошло, текущий_набор)`: набор отдан, только
    если единственное нарушение — расхождение набора (словарь изменился после
    задержания), иначе `None`."""
    if job.status != SemanticJobStatus.privacy_hold.value:
        return False, None
    material = load_request_material(db, [job.context_id]).get(job.context_id)
    if material is None or not is_applicable(material):
        return False, None
    rendered = render_context_request(material, settings=settings)
    if rendered.request_hash != job.request_hash:
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


def decline_privacy_hold(
    db: Session, *, job_id: int, shown_matches: list[dict], actor_id: int
) -> None:
    """«Не отправлять»: задание -> `cancelled` / `privacy_declined`."""
    job = _lock_job(db, job_id)
    ok, current = _hold_verdict(db, job, shown_matches, build_privacy_dictionary(db))
    if not ok:
        _conflict_keeping_refresh(db, job, current)
    job.status = SemanticJobStatus.cancelled.value
    job.cancel_reason = SemanticCancelReason.privacy_declined.value
    job.privacy_decided_by = actor_id
    job.privacy_decided_at = _now()
    db.flush()


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

def _preview_and_pairs(
    db: Session, context_ids: Sequence[int]
) -> tuple[Preview, list[Fingerprint]]:
    pairs, reserve, cached = estimate_enqueue(db, context_ids)
    tariffs = tariffs_from(settings)
    canonical = json.dumps(
        {
            "fingerprints": [fingerprint.as_dict() for fingerprint in pairs],
            "reserve_usd": str(reserve),
            "cached_usd": str(cached),
            "tariffs": {
                "input_per_m": str(tariffs.input_per_m),
                "cache_write_per_m": str(tariffs.cache_write_per_m),
                "cache_read_per_m": str(tariffs.cache_read_per_m),
                "output_per_m": str(tariffs.output_per_m),
            },
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
    """Контексты отпечатков предложений и значений; отпечатки схем семей
    подтверждение пачки пока не обрабатывает."""
    return sorted(
        {
            int(element["context_id"])
            for element in batch.held_fingerprints
            if element["kind"] != SemanticJobKind.family_schema.value
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
    return _preview_of(db, _batch_context_ids(batch))


def approve_batch(
    db: Session, *, batch_id: int, preview_hash: str, actor_id: int
) -> ReconcileReport:
    """«Поставить…»: `held -> approved` под `FOR UPDATE` на пачку и сверка
    контекстов пачки без потолка. Суточный бюджет остаётся в силе."""
    batch = _lock_batch(db, batch_id)
    if batch.status != ReconcileBatchStatus.held.value:
        raise DecisionConflict(CODE_BATCH_DECIDED)
    context_ids = _batch_context_ids(batch)
    preview, pairs = _preview_and_pairs(db, context_ids)
    if preview.preview_hash != preview_hash:
        raise DecisionConflict(CODE_PREVIEW_CHANGED)
    batch.status = ReconcileBatchStatus.approved.value
    batch.decided_by = actor_id
    batch.decided_at = _now()
    db.flush()
    report = reconcile_semantic_jobs(db, context_ids, cap=NO_CAP, source=batch.source)
    context_pairs = [(fingerprint.context_id, fingerprint.request_hash) for fingerprint in pairs]
    if context_pairs:
        db.execute(
            sa.update(SemanticJob)
            .where(sa.tuple_(SemanticJob.context_id, SemanticJob.request_hash).in_(context_pairs))
            .values(batch_id=batch.id)
        )
    return report


def discard_batch(db: Session, *, batch_id: int, actor_id: int) -> None:
    """«Отбросить»: `held -> discarded`; второе решение — `batch_decided`."""
    batch = _lock_batch(db, batch_id)
    if batch.status != ReconcileBatchStatus.held.value:
        raise DecisionConflict(CODE_BATCH_DECIDED)
    batch.status = ReconcileBatchStatus.discarded.value
    batch.decided_by = actor_id
    batch.decided_at = _now()
    db.flush()


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
