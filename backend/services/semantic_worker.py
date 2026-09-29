"""Исполнитель очереди семантических предложений: захват под блокировкой
состояния и бюджетом, запись результата, повторы и предохранитель (спека
`2026-09-28-semantic-suggestions-design.md` §2.5, §2.6, §2.8, §2.11).

Задача 10 фичи «Семантические предложения»
(`docs/superpowers/plans/2026-09-28-semantic-suggestions.md`). `claim_next`,
`record_result` и `record_failure` — каждая коммитит свою короткую транзакцию;
вызов модели стоит между ними, вне транзакции, и его делает только
`process_one`. Опросчик в потоке и запуск из `lifespan` — не этот модуль.
"""
from __future__ import annotations

import logging
import random
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session

from config import Settings
from models import (
    FamilySuggestion,
    SemanticAttemptOutcome,
    SemanticCancelReason,
    SemanticJob,
    SemanticJobAttempt,
    SemanticJobStatus,
    SemanticWorkerState,
    SuggestionUnpublishedReason,
)
from services.semantic_answer import AnswerSchemaError, parse_model_answer
from services.semantic_client import (
    ModelClient,
    ModelResponse,
    PermanentModelError,
    TransientModelError,
)
from services.semantic_cost import reserve_for, spent_last_24h, tariffs_from
from services.semantic_privacy import (
    PrivacyDictionary,
    PrivacyMatch,
    build_privacy_dictionary,
    find_privacy_matches,
)
from services.semantic_request import (
    CandidateFamily,
    RenderedRequest,
    is_applicable,
    load_request_material,
    render_context_request,
)

logger = logging.getLogger(__name__)

__all__ = [
    "Claim",
    "ModelClient",
    "ModelResponse",
    "PermanentModelError",
    "TransientModelError",
    "claim_next",
    "process_one",
    "record_failure",
    "record_result",
    "serialize_privacy_matches",
]

#: Первая отсрочка повтора, секунды; каждая следующая попытка поколения — вдвое
#: длиннее, к ней добавляется разброс из `[0, база/2)`.
_RETRY_BASE_SECONDS = 30

PAUSE_REASON_RESERVE_EXCEEDED = "reserve_exceeded"

_ERROR_TEXT_LIMIT = 2000


@dataclass(frozen=True)
class Claim:
    """Захваченное задание: всё, что нужно вызову и записи результата."""

    job_id: int
    attempt_id: int
    claim_token: UUID
    rendered: RenderedRequest
    candidates: tuple[CandidateFamily, ...]


def serialize_privacy_matches(matches: Sequence[PrivacyMatch]) -> list[dict]:
    """Набор совпадений в форме, в которой он лежит в `privacy_matches` и
    `privacy_released_matches`: список `{text, kind, where}` в порядке
    `find_privacy_matches`. Захват сравнивает набор с разрешённым, а решение
    `admin` фиксирует показанный набор — обоим нужна одна и та же форма."""
    return [{"text": m.text, "kind": m.kind, "where": m.where} for m in matches]


# ---------------------------------------------------------------------------
#  Блокировка состояния и предохранитель
# ---------------------------------------------------------------------------

def _lock_worker_state(db: Session) -> SemanticWorkerState:
    """`SELECT ... FOR UPDATE` единственной строки состояния: сериализует
    захваты между собой (спека §2.5), а предохранитель — с ними."""
    return db.execute(
        sa.select(SemanticWorkerState)
        .where(SemanticWorkerState.id == 1)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()


def _apply_fuse(db: Session, attempt: SemanticJobAttempt, *, now: datetime) -> None:
    """Сверка факта с резервом завершённой попытки (спека §2.11): факт больше
    резерва — попытка помечена, захват остановлен. Уже остановленный захват не
    переписывается: остаётся первая остановка. Стоимость неизвестна — попытка
    остаётся с резервом и не сверяется."""
    if attempt.cost_usd is None or attempt.cost_usd <= attempt.reserve_usd:
        return
    attempt.reserve_exceeded = True
    db.flush()
    # Порядок блокировок здесь «задание, затем состояние» — обратный захвату
    # («состояние, затем задание»). Взаимной блокировки нет только потому, что
    # захват берёт задания через SKIP LOCKED и на занятой строке не ждёт.
    state = _lock_worker_state(db)
    if state.claim_paused:
        return
    state.claim_paused = True
    state.paused_reason = PAUSE_REASON_RESERVE_EXCEEDED
    state.paused_attempt_id = attempt.id
    state.paused_at = now
    db.flush()


# ---------------------------------------------------------------------------
#  Захват
# ---------------------------------------------------------------------------

def _next_pending_job(db: Session, *, now: datetime) -> SemanticJob | None:
    return db.execute(
        sa.select(SemanticJob)
        .where(
            SemanticJob.status == SemanticJobStatus.pending.value,
            SemanticJob.next_attempt_at <= now,
        )
        .order_by(SemanticJob.unit_id, SemanticJob.next_attempt_at, SemanticJob.id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _cancel(db: Session, job: SemanticJob, reason: SemanticCancelReason) -> None:
    job.status = SemanticJobStatus.cancelled.value
    job.cancel_reason = reason.value
    db.flush()


def claim_next(db: Session, *, settings: Settings, now: datetime) -> Claim | None:
    """Захват одного задания — шаги спеки §2.5 строго по порядку; коммитит сама.
    `None` — захвата нет: остановка, нет готовых заданий или не хватает бюджета."""
    state = _lock_worker_state(db)
    if state.claim_paused:
        db.commit()
        return None

    dictionary: PrivacyDictionary | None = None
    while True:
        job = _next_pending_job(db, now=now)
        if job is None:
            db.commit()
            return None

        # Контекст задания существует всегда (внешний ключ RESTRICT), а загрузчик
        # отдаёт каждый существующий контекст.
        material = load_request_material(db, [job.context_id])[job.context_id]
        if not is_applicable(material):
            _cancel(db, job, SemanticCancelReason.not_applicable)
            continue

        rendered = render_context_request(material, settings=settings)
        if rendered.request_hash != job.request_hash:
            _cancel(db, job, SemanticCancelReason.input_changed)
            continue

        if dictionary is None:
            dictionary = build_privacy_dictionary(db)
        matches = serialize_privacy_matches(find_privacy_matches(dictionary, rendered))
        if matches and matches != job.privacy_released_matches:
            job.status = SemanticJobStatus.privacy_hold.value
            job.privacy_matches = matches
            db.flush()
            continue

        reserve = reserve_for(db, rendered, tariffs_from(settings), rendered.body["max_tokens"])
        if spent_last_24h(db, now=now) + reserve > settings.SEMANTIC_DAILY_BUDGET_USD:
            db.commit()
            return None

        token = uuid.uuid4()
        job.status = SemanticJobStatus.running.value
        job.claim_token = token
        job.attempts_in_generation = job.attempts_in_generation + 1
        attempt = SemanticJobAttempt(
            job_id=job.id,
            claim_token=token,
            retry_generation=job.retry_generation,
            started_at=now,
            reserve_usd=reserve,
            prefix_hash=rendered.prefix_hash,
            privacy_dictionary_hash=dictionary.digest,
        )
        db.add(attempt)
        db.flush()
        # Загрузчик отдаёт кандидатов по `id`, как и снимок рендера.
        candidates = material.candidates
        claim = Claim(
            job_id=job.id,
            attempt_id=attempt.id,
            claim_token=token,
            rendered=rendered,
            candidates=candidates,
        )
        db.commit()
        return claim


# ---------------------------------------------------------------------------
#  Запись результата
# ---------------------------------------------------------------------------

def _lock_job(db: Session, job_id: int) -> SemanticJob:
    return db.execute(
        sa.select(SemanticJob)
        .where(SemanticJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()


def _load_attempt(db: Session, attempt_id: int) -> SemanticJobAttempt:
    # `populate_existing`: охрана `record_failure` читает `finished_at` и обязана
    # видеть закоммиченное значение, даже когда попытка уже лежит в сессии
    # вызывающего загруженной.
    return db.execute(
        sa.select(SemanticJobAttempt)
        .where(SemanticJobAttempt.id == attempt_id)
        .execution_options(populate_existing=True)
    ).scalar_one()


def _owns_job(job: SemanticJob, claim: Claim) -> bool:
    # Токен непуст ровно при `running` (CHECK `(status = 'running') = (claim_token
    # IS NOT NULL)`), поэтому совпавший токен уже означает статус `running`.
    return job.claim_token == claim.claim_token


def _close_with_response(
    attempt: SemanticJobAttempt,
    response: ModelResponse,
    *,
    outcome: SemanticAttemptOutcome,
    now: datetime,
) -> None:
    attempt.finished_at = now
    attempt.outcome = outcome.value
    attempt.raw_response = response.content
    attempt.actual_model = response.actual_model
    attempt.provider = response.provider
    attempt.prompt_tokens = response.prompt_tokens
    attempt.completion_tokens = response.completion_tokens
    attempt.cache_write_tokens = response.cache_write_tokens
    attempt.cached_tokens = response.cached_tokens
    attempt.cost_usd = response.cost_usd


def _candidates_snapshot(candidates: Sequence[CandidateFamily]) -> list[dict]:
    return [
        {"id": c.id, "title": c.title, "unit_code": c.unit_code, "definition": c.definition}
        for c in candidates
    ]


def _publication_verdict(
    db: Session, job: SemanticJob, claim: Claim, settings: Settings
) -> SuggestionUnpublishedReason | None:
    """Спека §2.8: публикуется только ответ на ТЕКУЩИЙ отпечаток применимого
    контекста; `None` — публикуется, иначе причина, по которой нет."""
    # Контекст задания существует всегда (внешний ключ RESTRICT), а загрузчик
    # отдаёт каждый существующий контекст.
    material = load_request_material(db, [job.context_id])[job.context_id]
    if not is_applicable(material):
        return SuggestionUnpublishedReason.context_not_applicable
    current = render_context_request(material, settings=settings)
    if current.request_hash != claim.rendered.request_hash:
        return SuggestionUnpublishedReason.stale_fingerprint
    return None


def record_result(
    db: Session,
    claim: Claim,
    response: ModelResponse,
    *,
    now: datetime,
    settings: Settings,
) -> None:
    """Условная запись ответа модели (спека §2.5 шаг 3, §2.8); коммитит сама.
    Ответ разбирается здесь же строгим `parse_model_answer`. `settings` нужны
    для повторного рендера контекста при проверке отпечатка — те же, что и у
    захвата."""
    job = _lock_job(db, claim.job_id)
    attempt = _load_attempt(db, claim.attempt_id)
    owned = _owns_job(job, claim)

    answer = None
    schema_error: AnswerSchemaError | None = None
    try:
        answer = parse_model_answer(response.content, claim.candidates)
    except AnswerSchemaError as exc:
        schema_error = exc

    if not owned:
        outcome = SemanticAttemptOutcome.lost_claim
        verdict: SuggestionUnpublishedReason | None = SuggestionUnpublishedReason.lost_claim
    elif schema_error is not None:
        outcome = SemanticAttemptOutcome.schema_error
        verdict = None
    else:
        outcome = SemanticAttemptOutcome.ok
        verdict = _publication_verdict(db, job, claim, settings)

    _close_with_response(attempt, response, outcome=outcome, now=now)
    if schema_error is not None:
        attempt.validation_error = schema_error.detail
    db.flush()

    if answer is not None:
        publish = verdict is None
        if publish:
            # Прежнее опубликованное снимается раньше вставки нового: частичный
            # уникальный индекс допускает одно опубликованное на контекст.
            db.execute(
                sa.update(FamilySuggestion)
                .where(
                    FamilySuggestion.context_id == job.context_id,
                    FamilySuggestion.is_published.is_(True),
                )
                .values(
                    is_published=False,
                    unpublished_reason=SuggestionUnpublishedReason.stale_fingerprint.value,
                )
            )
        suggestion = FamilySuggestion(
            context_id=job.context_id,
            job_id=job.id,
            attempt_id=attempt.id,
            request_hash=claim.rendered.request_hash,
            candidates_hash=claim.rendered.candidates_hash,
            candidates_snapshot=_candidates_snapshot(claim.candidates),
            family_id=answer.family_id,
            new_family_name=answer.new_family_name,
            confidence=answer.confidence,
            reason=answer.reason,
            is_published=publish,
            unpublished_reason=None if publish else verdict.value,
        )
        db.add(suggestion)
        db.flush()
        if owned:
            job.status = SemanticJobStatus.done.value
            job.claim_token = None
            job.result_suggestion_id = suggestion.id
    elif owned:
        job.status = SemanticJobStatus.error.value
        job.claim_token = None
        job.last_error_class = "schema_error"

    db.flush()
    _apply_fuse(db, attempt, now=now)
    db.commit()


# ---------------------------------------------------------------------------
#  Запись неудачи вызова
# ---------------------------------------------------------------------------

def _retry_delay(attempts_in_generation: int, rng: Callable[[], float]) -> timedelta:
    """`30 с x 2^(n - 1)` плюс разброс из `[0, база/2)`: следующая отсрочка
    (не меньше `2 x база`) всегда длиннее предыдущей (не больше `1,5 x база`)
    при любом разбросе."""
    base = _RETRY_BASE_SECONDS * 2 ** (attempts_in_generation - 1)
    return timedelta(seconds=base + base / 2 * rng())


def record_failure(
    db: Session,
    claim: Claim,
    error: TransientModelError | PermanentModelError,
    *,
    now: datetime,
    settings: Settings,
    rng: Callable[[], float] = random.random,
) -> None:
    """Запись неудачи вызова (ответа нет, стоимость неизвестна — попытка
    остаётся с резервом); коммитит сама. Закрытую попытку не переписывает:
    ответ мог быть записан, а падение случилось уже после подтверждения.
    Временная ошибка — повтор с отсрочкой,
    пока попыток поколения меньше `SEMANTIC_MAX_ATTEMPTS`; постоянная и
    исчерпание попыток — `error`."""
    job = _lock_job(db, claim.job_id)
    attempt = _load_attempt(db, claim.attempt_id)
    if attempt.finished_at is not None:
        db.commit()
        return
    owned = _owns_job(job, claim)
    transient = isinstance(error, TransientModelError)
    error_class = getattr(error, "error_class", None) or type(error).__name__

    attempt.finished_at = now
    attempt.error_class = error_class
    attempt.error_text = str(error)[:_ERROR_TEXT_LIMIT]
    if not owned:
        attempt.outcome = SemanticAttemptOutcome.lost_claim.value
    else:
        attempt.outcome = (
            SemanticAttemptOutcome.transient_error.value
            if transient
            else SemanticAttemptOutcome.permanent_error.value
        )
        job.claim_token = None
        job.last_error_class = error_class
        if transient and job.attempts_in_generation < settings.SEMANTIC_MAX_ATTEMPTS:
            job.status = SemanticJobStatus.pending.value
            job.next_attempt_at = now + _retry_delay(job.attempts_in_generation, rng)
        else:
            job.status = SemanticJobStatus.error.value
    db.commit()


# ---------------------------------------------------------------------------
#  Один проход
# ---------------------------------------------------------------------------

def _record_failure_in_new_session(
    session_factory: Callable[[], Session],
    claim: Claim,
    error: TransientModelError | PermanentModelError,
    *,
    settings: Settings,
    clock: Callable[[], datetime],
) -> None:
    with session_factory() as db:
        record_failure(db, claim, error, now=clock(), settings=settings)


def process_one(
    session_factory: Callable[[], Session],
    client: ModelClient,
    *,
    settings: Settings,
    clock: Callable[[], datetime],
) -> bool:
    """Захват, вызов, запись — по сессии на шаг, вызов модели вне транзакции.
    `False` — захватывать нечего. Иначе `True`, и исключение задания наружу не
    уходит: попытка закрывается ошибкой, цикл продолжается (спека §2.5, п. 4)."""
    with session_factory() as db:
        claim = claim_next(db, settings=settings, now=clock())
    if claim is None:
        return False

    try:
        try:
            response = client.complete(
                claim.rendered.body, timeout_s=settings.SEMANTIC_CALL_TIMEOUT_S
            )
        except (TransientModelError, PermanentModelError) as exc:
            _record_failure_in_new_session(
                session_factory, claim, exc, settings=settings, clock=clock
            )
            return True
        except Exception as exc:  # noqa: BLE001 — любое исключение клиента временное
            _record_failure_in_new_session(
                session_factory,
                claim,
                TransientModelError(str(exc), error_class=type(exc).__name__),
                settings=settings,
                clock=clock,
            )
            return True

        try:
            with session_factory() as db:
                record_result(db, claim, response, now=clock(), settings=settings)
        except Exception as exc:  # noqa: BLE001 — запись не удалась: попытку закрыть
            logger.exception("Запись результата задания %s не удалась", claim.job_id)
            _record_failure_in_new_session(
                session_factory,
                claim,
                TransientModelError(str(exc), error_class=type(exc).__name__),
                settings=settings,
                clock=clock,
            )
    except Exception:  # noqa: BLE001 — закрыть попытку не удалось: задание вернёт восстановление
        logger.exception("Попытка задания %s осталась открытой", claim.job_id)
    return True
