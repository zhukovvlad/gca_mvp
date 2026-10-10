"""Исполнитель очереди семантических предложений: захват под блокировкой
состояния и бюджетом, запись результата, повторы и предохранитель (спека
`2026-09-28-semantic-suggestions-design.md` §2.5, §2.6, §2.8, §2.11).

Задача 10 фичи «Семантические предложения»
(`docs/superpowers/plans/2026-09-28-semantic-suggestions.md`). `claim_next`,
`record_result` и `record_failure` — каждая коммитит свою короткую транзакцию;
вызов модели стоит между ними, вне транзакции, и его делает только
`process_one`. Опросчик в потоке и запуск из `lifespan` — не этот модуль.

Задания `family_schema` и `context_values` (спека
`2026-10-02-catalog-variants-design.md` §2.3, §2.6) идут тем же захватом,
повторами и предохранителем; отличается предмет (рендер по виду задания) и
запись результата: она не берёт задание первой, а отдаёт охрану захвата
обработчикам `work_variants`, которые блокируют задание после доменных строк.
Задание значений схемы без параметров завершается без вызова модели и без
попытки (синтетический захват).
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
from config import settings as default_settings
from models import (
    CatalogContext,
    FamilyParameter,
    FamilyParameterSchema,
    FamilyParameterValue,
    FamilyStatus,
    FamilySuggestion,
    SchemaStatus,
    SemanticAttemptOutcome,
    SemanticCancelReason,
    SemanticJob,
    SemanticJobAttempt,
    SemanticJobKind,
    SemanticJobStatus,
    SemanticWorkerState,
    SuggestionUnpublishedReason,
    WorkFamily,
)
from services.discovery_result import apply_discovery
from services.family_change import apply_publication_rules, thresholds_from
from services.family_discovery import discovery_scope, render_discovery_request, sent_of
from services.semantic_answer import AnswerSchemaError, parse_model_answer
from services.semantic_client import (
    ModelClient,
    ModelResponse,
    PermanentModelError,
    TransientModelError,
)
from services.semantic_cost import event_cap_from, reserve_for, spent_last_24h, tariffs_from
from services.semantic_privacy import (
    PrivacyDictionary,
    PrivacyMatch,
    build_privacy_dictionary,
    find_privacy_matches,
)
from services.semantic_reconcile import (
    _load_values_columns,
    _values_applicable,
    families_awaiting_schema,
    reconcile_context_values,
    reconcile_family_schemas,
    without_discarded_schemas,
    without_discarded_values,
)
from services.semantic_request import (
    CandidateFamily,
    RenderedRequest,
    is_applicable,
    load_request_material,
    render_context_request,
)
from services.variant_answer import (
    DiscoverySent,
    ValuesAnswer,
    parse_discovery_answer,
    parse_schema_answer,
    parse_values_answer,
)
from services.variant_request import (
    SchemaParameterIn,
    load_values_material,
    paths_hash_of,
    render_request_for,
    render_values_request,
)
from services.work_variants import JobGuard, apply_values, freeze_schema

logger = logging.getLogger(__name__)

__all__ = [
    "Claim",
    "JobRender",
    "ModelClient",
    "ModelResponse",
    "PermanentModelError",
    "TransientModelError",
    "claim_next",
    "complete_without_model",
    "process_one",
    "record_failure",
    "record_result",
    "render_job_request",
    "serialize_privacy_matches",
]

#: Первая отсрочка повтора, секунды; каждая следующая попытка поколения — вдвое
#: длиннее, к ней добавляется разброс из `[0, база/2)`.
_RETRY_BASE_SECONDS = 30

PAUSE_REASON_RESERVE_EXCEEDED = "reserve_exceeded"

_ERROR_TEXT_LIMIT = 2000


@dataclass(frozen=True)
class Claim:
    """Захваченное задание: всё, что нужно вызову и записи результата. У
    синтетического захвата (`synthesized`: схема без параметров, вызова модели
    нет) нет ни попытки, ни тела запроса; `candidates` — только у предложений."""

    job_id: int
    attempt_id: int | None
    claim_token: UUID
    rendered: RenderedRequest | None
    candidates: tuple[CandidateFamily, ...]
    kind: SemanticJobKind
    synthesized: bool
    #: Что ушло модели (только у открытия семей): по нему проверяются ссылки ответа.
    sent: DiscoverySent | None = None


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


@dataclass(frozen=True)
class JobRender:
    """Текущий запрос предмета задания либо причина, по которой его нет.
    `parameterless` — у задания значений в схеме нет параметров."""

    rendered: RenderedRequest | None
    cancel_reason: SemanticCancelReason | None = None
    candidates: tuple[CandidateFamily, ...] = ()
    parameterless: bool = False
    sent: DiscoverySent | None = None


def _not_renderable(reason: SemanticCancelReason) -> JobRender:
    return JobRender(rendered=None, cancel_reason=reason)


def render_job_request(db: Session, job: SemanticJob, *, settings: Settings) -> JobRender:
    """Рендерит ТЕКУЩИЙ запрос предмета задания по его виду; захват и
    перепроверка задержанного идут через одну функцию. Предмет неприменим —
    `not_applicable`; запрос задан по версии схемы, которая уже не та, —
    `input_changed`. Сравнение отпечатка с `job.request_hash` — за вызывающим.

    `family_schema`: семья `active` и версия задания в `building`;
    `context_values`: контекст применим по §2.5 без условия кандидатов, у него
    есть схема, и она — версия задания."""
    kind = job.kind
    if kind == SemanticJobKind.family_suggestion.value:
        # Контекст задания существует всегда (внешний ключ RESTRICT), а загрузчик
        # отдаёт каждый существующий контекст.
        material = load_request_material(db, [job.context_id])[job.context_id]
        if not is_applicable(material):
            return _not_renderable(SemanticCancelReason.not_applicable)
        return JobRender(
            rendered=render_context_request(material, settings=settings),
            candidates=material.candidates,
        )
    if kind == SemanticJobKind.family_schema.value:
        family_status = db.execute(
            sa.select(WorkFamily.status).where(WorkFamily.id == job.family_id)
        ).scalar_one_or_none()
        schema_status = db.execute(
            sa.select(FamilyParameterSchema.status).where(FamilyParameterSchema.id == job.schema_id)
        ).scalar_one_or_none()
        if (
            family_status != FamilyStatus.active.value
            or schema_status != SchemaStatus.building.value
        ):
            return _not_renderable(SemanticCancelReason.not_applicable)
        # Семья существует (статус прочитан выше), поэтому материал схемы есть
        # всегда и `SubjectNotRenderable` здесь недостижим.
        return JobRender(rendered=render_request_for(db, job, settings=settings))
    if kind == SemanticJobKind.context_values.value:
        material = load_request_material(db, [job.context_id])[job.context_id]
        columns = _load_values_columns(db, [job.context_id])[job.context_id]
        values_material = load_values_material(db, [job.context_id]).get(job.context_id)
        if values_material is None or not _values_applicable(material, columns):
            return _not_renderable(SemanticCancelReason.not_applicable)
        if values_material.schema_id != job.schema_id:
            return _not_renderable(SemanticCancelReason.input_changed)
        return JobRender(
            rendered=render_values_request(values_material, settings=settings),
            parameterless=not values_material.parameters,
        )
    if kind == SemanticJobKind.family_discovery.value:
        # Предмет — единица. Охват пуст и семей без категории нет — открывать
        # нечего: задание неприменимо.
        scope = discovery_scope(db, job.unit_id)
        if not scope.names and not scope.uncategorized_family_ids:
            return _not_renderable(SemanticCancelReason.not_applicable)
        return JobRender(
            rendered=render_discovery_request(scope, db, settings=settings), sent=sent_of(scope)
        )
    raise ValueError(f"неизвестный вид задания: {kind!r}")


def _note_closed(closed_units: list[int | None] | None, job: SemanticJob) -> None:
    """Единица закрытого задания предложения — для сверки схем её семей. Задания
    других видов, в том числе открытие семей, единицу не отмечают."""
    if closed_units is not None and job.kind == SemanticJobKind.family_suggestion.value:
        closed_units.append(job.unit_id)


def claim_next(
    db: Session,
    *,
    settings: Settings,
    now: datetime,
    closed_units: list[int | None] | None = None,
) -> Claim | None:
    """Захват одного задания — шаги спеки §2.5 строго по порядку; коммитит сама.
    `None` — захвата нет: остановка, нет готовых заданий или не хватает бюджета.
    В `closed_units` (если передан) попадают единицы заданий предложений, которые
    захват отменил или задержал: открытых заданий в такой единице могло не
    остаться, и схемы её семей ждут сверки."""
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

        current = render_job_request(db, job, settings=settings)
        if current.cancel_reason is not None:
            _cancel(db, job, current.cancel_reason)
            _note_closed(closed_units, job)
            continue
        rendered = current.rendered
        assert rendered is not None  # причины отмены нет — запрос отрендерен
        if rendered.request_hash != job.request_hash:
            _cancel(db, job, SemanticCancelReason.input_changed)
            _note_closed(closed_units, job)
            continue
        kind = SemanticJobKind(job.kind)

        if current.parameterless:
            # Модели нечего спрашивать: тело никуда не уходит, поэтому ни
            # приватность, ни бюджет не применяются; попытки и резерва нет.
            token = uuid.uuid4()
            job.status = SemanticJobStatus.running.value
            job.claim_token = token
            db.flush()
            synthetic = Claim(
                job_id=job.id,
                attempt_id=None,
                claim_token=token,
                rendered=None,
                candidates=(),
                kind=kind,
                synthesized=True,
            )
            db.commit()
            return synthetic

        if dictionary is None:
            dictionary = build_privacy_dictionary(db)
        matches = serialize_privacy_matches(find_privacy_matches(dictionary, rendered))
        if matches and matches != job.privacy_released_matches:
            job.status = SemanticJobStatus.privacy_hold.value
            job.privacy_matches = matches
            db.flush()
            _note_closed(closed_units, job)
            continue

        reserve = reserve_for(
            db, rendered, tariffs_from(settings, kind), rendered.body["max_tokens"]
        )
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
        claim = Claim(
            job_id=job.id,
            attempt_id=attempt.id,
            claim_token=token,
            rendered=rendered,
            candidates=current.candidates,
            kind=kind,
            synthesized=False,
            sent=current.sent,
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


def _share_domain_rows(db: Session, job_id: int, family_id: int | None) -> None:
    """`FOR KEY SHARE` на семью ответа (если есть), затем на контекст задания.
    Контекст задания неизменен, читается без блокировок."""
    context_id = db.execute(
        sa.select(SemanticJob.context_id).where(SemanticJob.id == job_id)
    ).scalar_one()
    if family_id is not None:
        db.execute(
            sa.select(WorkFamily.id)
            .where(WorkFamily.id == family_id)
            .with_for_update(read=True, key_share=True)
        ).all()
    db.execute(
        sa.select(CatalogContext.id)
        .where(CatalogContext.id == context_id)
        .with_for_update(read=True, key_share=True)
    ).all()


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
    assert claim.rendered is not None  # у предложения тело есть всегда
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
) -> int | None:
    """Условная запись ответа модели (спека §2.5 шаг 3, §2.8); коммитит сама.
    Ответ разбирается здесь же строгим `parse_model_answer`. `settings` нужны
    для повторного рендера контекста при проверке отпечатка — те же, что и у
    захвата. Задания схемы и значений пишутся своим путём
    (`_record_variant_result`).

    Возвращает `id` опубликованного предложения (его правилам публикации
    предъявляет `process_one` отдельной транзакцией) либо `None`: ответ не
    опубликован, разобран с ошибкой или задание не наше. Семья ответа и
    контекст берутся `FOR KEY SHARE` до замка задания: порядок «задание ->
    домен» не возникает, ни явно, ни неявным замком внешнего ключа."""
    if claim.synthesized:
        raise ValueError("у синтетического захвата нет ответа модели")
    if claim.kind == SemanticJobKind.family_discovery:
        _record_discovery_result(db, claim, response, now=now, settings=settings)
        return None
    if claim.kind != SemanticJobKind.family_suggestion:
        _record_variant_result(db, claim, response, now=now, settings=settings)
        return None
    assert claim.attempt_id is not None and claim.rendered is not None
    answer = None
    published_id: int | None = None
    schema_error: AnswerSchemaError | None = None
    try:
        answer = parse_model_answer(response.content, claim.candidates)
    except AnswerSchemaError as exc:
        schema_error = exc

    # Вставка предложения неявно берёт `FOR KEY SHARE` на контекст и семью
    # ответа (внешние ключи). Берём их явно ДО замка задания, в порядке домена
    # «семья -> контекст»: иначе порядок «задание -> домен» замыкается в цикл с
    # операцией, держащей контекст или семью и сверяющей это задание
    # (потерянный захват возвращает его в `pending`).
    _share_domain_rows(db, claim.job_id, answer.family_id if answer is not None else None)
    job = _lock_job(db, claim.job_id)
    attempt = _load_attempt(db, claim.attempt_id)
    owned = _owns_job(job, claim)

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
        if publish:
            published_id = suggestion.id
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
    return published_id


def _schema_parameters(db: Session, schema_id: int) -> tuple[SchemaParameterIn, ...]:
    """Параметры версии схемы и их текущие значения (слитые исключены) — то, с
    чем сверяется ответ значений. Версия замороженная, поэтому чтение без
    блокировки; значения могут только прибавляться."""
    parameters = db.execute(
        sa.select(FamilyParameter.id, FamilyParameter.ordinal, FamilyParameter.name)
        .where(FamilyParameter.schema_id == schema_id)
        .order_by(FamilyParameter.ordinal)
    ).all()
    values: dict[int, list[str]] = {row.id: [] for row in parameters}
    if values:
        for row in db.execute(
            sa.select(FamilyParameterValue.parameter_id, FamilyParameterValue.value)
            .where(
                FamilyParameterValue.parameter_id.in_(list(values)),
                FamilyParameterValue.merged_into_id.is_(None),
            )
            .order_by(FamilyParameterValue.parameter_id, FamilyParameterValue.id)
        ).all():
            values[row.parameter_id].append(row.value)
    return tuple(
        SchemaParameterIn(ordinal=row.ordinal, name=row.name, values=tuple(values[row.id]))
        for row in parameters
    )


def _record_variant_result(
    db: Session,
    claim: Claim,
    response: ModelResponse,
    *,
    now: datetime,
    settings: Settings,
) -> None:
    """Запись результата `family_schema` / `context_values` (спека §2.6).

    Задание здесь первым НЕ берётся: обработчики `work_variants` блокируют
    доменные строки, затем задание, и сами выносят вердикт под блокировками
    (охрана `JobGuard` — из захвата). Обратный порядок «задание, затем домен»
    замкнулся бы в цикл со сверкой. Схемная ошибка разбора доменных блокировок
    не берёт — задание блокируется само, как у предложений. Ошибка схемы изнутри
    обработчика (пустое после нормализации) откатывает его точку сохранения и идёт
    тем же путём."""
    assert claim.attempt_id is not None and claim.rendered is not None
    subject = db.execute(
        sa.select(
            SemanticJob.context_id, SemanticJob.schema_id, SemanticJob.paths_hash
        ).where(SemanticJob.id == claim.job_id)
    ).one()
    is_schema = claim.kind == SemanticJobKind.family_schema

    schema_error: AnswerSchemaError | None = None
    answer = None
    try:
        if is_schema:
            answer = parse_schema_answer(response.content)
        else:
            answer = parse_values_answer(
                response.content, _schema_parameters(db, subject.schema_id)
            )
    except AnswerSchemaError as exc:
        schema_error = exc

    if answer is not None:
        guard = JobGuard(
            job_id=claim.job_id,
            claim_token=claim.claim_token,
            expected_request_hash=claim.rendered.request_hash,
        )
        try:
            with db.begin_nested():
                if is_schema:
                    outcome = freeze_schema(
                        db, schema_id=subject.schema_id, answer=answer, guard=guard,
                        settings=settings,
                    )
                else:
                    outcome = apply_values(
                        db, context_id=subject.context_id, schema_id=subject.schema_id,
                        answer=answer, paths_hash=subject.paths_hash, guard=guard,
                        settings=settings,
                    )
        except AnswerSchemaError as exc:
            schema_error = exc
        else:
            attempt = _load_attempt(db, claim.attempt_id)
            _close_with_response(
                attempt,
                response,
                outcome=(
                    SemanticAttemptOutcome.lost_claim
                    if outcome.unapplied_reason == "lost_claim"
                    else SemanticAttemptOutcome.ok
                ),
                now=now,
            )
            db.flush()
            _apply_fuse(db, attempt, now=now)
            db.commit()
            return

    assert schema_error is not None
    _record_schema_error(db, claim, response, schema_error, now=now)


def _record_schema_error(
    db: Session,
    claim: Claim,
    response: ModelResponse,
    schema_error: AnswerSchemaError,
    *,
    now: datetime,
) -> None:
    """Схемная ошибка разбора: задание блокируется само (доменных блокировок
    разбор не берёт), попытка закрывается `schema_error` (или `lost_claim`, если
    задание уже не наше), задание — `error` без автоповтора; коммитит сама."""
    assert claim.attempt_id is not None
    job = _lock_job(db, claim.job_id)
    attempt = _load_attempt(db, claim.attempt_id)
    owned = _owns_job(job, claim)
    _close_with_response(
        attempt,
        response,
        outcome=(
            SemanticAttemptOutcome.schema_error if owned else SemanticAttemptOutcome.lost_claim
        ),
        now=now,
    )
    attempt.validation_error = schema_error.detail
    if owned:
        job.status = SemanticJobStatus.error.value
        job.claim_token = None
        job.last_error_class = "schema_error"
    db.flush()
    _apply_fuse(db, attempt, now=now)
    db.commit()


def _record_discovery_result(
    db: Session,
    claim: Claim,
    response: ModelResponse,
    *,
    now: datetime,
    settings: Settings,
) -> None:
    """Запись результата открытия семей: ответ разбирается по тому, что ушло
    модели (`claim.sent`), затем `apply_discovery` — одна транзакция в общем
    порядке блокировок (домен, затем задание). Схемная ошибка доменных блокировок
    не берёт. Попытка закрывается после обработки: `lost_claim` — задание не наше,
    иначе `ok` (в том числе когда охват успел измениться и задание отменено);
    предохранитель «факт выше резерва» — как у остальных видов."""
    assert claim.attempt_id is not None and claim.sent is not None
    try:
        answer = parse_discovery_answer(response.content, claim.sent)
    except AnswerSchemaError as exc:
        _record_schema_error(db, claim, response, exc, now=now)
        return
    outcome = apply_discovery(
        db, job_id=claim.job_id, claim_token=claim.claim_token, answer=answer, settings=settings
    )
    attempt = _load_attempt(db, claim.attempt_id)
    _close_with_response(
        attempt,
        response,
        outcome=(
            SemanticAttemptOutcome.lost_claim
            if outcome.unapplied_reason == "lost_claim"
            else SemanticAttemptOutcome.ok
        ),
        now=now,
    )
    db.flush()
    _apply_fuse(db, attempt, now=now)
    db.commit()


def complete_without_model(
    db: Session, claim: Claim, *, now: datetime, settings: Settings | None = None
) -> None:
    """Завершает синтетический захват: ответ `{"values": []}` идёт в тот же
    обработчик результата, что и ответ модели, — те же блокировки, вердикт,
    переключение ожидающей семьи, промоушен и события; попытки и резерва нет.
    Коммитит сама. `expected_request_hash` — отпечаток задания, прошедший
    проверку при захвате. `now` — как у остальных записей исполнителя;
    `settings` по умолчанию — настройки приложения."""
    if not claim.synthesized:
        raise ValueError("завершение без модели — только для синтетического захвата")
    subject = db.execute(
        sa.select(
            SemanticJob.context_id, SemanticJob.schema_id, SemanticJob.request_hash,
        ).where(SemanticJob.id == claim.job_id)
    ).one()
    apply_values(
        db,
        context_id=subject.context_id,
        schema_id=subject.schema_id,
        answer=ValuesAnswer(items=()),
        # У схемы без параметров путей нет: обработчик читает константу пустого
        # списка, а не хэш задания.
        paths_hash=paths_hash_of(()),
        guard=JobGuard(
            job_id=claim.job_id,
            claim_token=claim.claim_token,
            expected_request_hash=subject.request_hash,
        ),
        settings=settings if settings is not None else default_settings,
    )
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
    if claim.attempt_id is None:
        raise ValueError("у синтетического захвата нет попытки, неудачи вызова быть не может")
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


def _park_in_error(db: Session, claim: Claim, exc: Exception) -> None:
    """Задание, которое не удалось завершить без модели: у него нет попытки,
    которую закрыла бы `record_failure`, поэтому оно уходит в `error` с классом
    исключения (ручное «Повторить» остаётся)."""
    job = _lock_job(db, claim.job_id)
    if _owns_job(job, claim):
        job.status = SemanticJobStatus.error.value
        job.claim_token = None
        job.last_error_class = type(exc).__name__
    db.commit()


def _complete_synthesized(
    session_factory: Callable[[], Session],
    claim: Claim,
    *,
    settings: Settings,
    clock: Callable[[], datetime],
) -> None:
    try:
        with session_factory() as db:
            complete_without_model(db, claim, now=clock(), settings=settings)
    except Exception as exc:  # noqa: BLE001 — исключение задания наружу не уходит
        logger.exception("Завершение задания %s без модели не удалось", claim.job_id)
        try:
            with session_factory() as db:
                _park_in_error(db, claim, exc)
        except Exception:  # noqa: BLE001 — задание вернёт восстановление при запуске
            logger.exception("Задание %s осталось захваченным", claim.job_id)


def _apply_rules_in_new_session(
    session_factory: Callable[[], Session], suggestion_id: int, *, settings: Settings
) -> None:
    """Правила публикации (спека вариантов §2.5) — отдельная транзакция после
    записи ответа. Её ошибка ответа не трогает: предложение остаётся
    опубликованным, а исключение не выходит из цикла исполнителя."""
    try:
        with session_factory() as db:
            apply_publication_rules(
                db, suggestion_id=suggestion_id, thresholds=thresholds_from(settings),
            )
    except Exception:  # noqa: BLE001 — исключение правил наружу не уходит
        logger.exception("Правила публикации предложения %s не применены", suggestion_id)


def _reconcile_schemas_of_unit(
    session_factory: Callable[[], Session], unit_id: int | None, *, settings: Settings
) -> None:
    """Сверка схем единицы после завершения задания предложения (спека §2.6,
    §2.7): когда последнее задание единицы закончилось любым исходом, семьи без
    текущей версии получают задание схемы. Готовность единицы проверяет сама
    сверка (в единице ещё открытое задание или удержанная пачка — ничего не
    ставится). Отдельная транзакция после записи ответа и правил: её ошибка
    записанного не трогает, исключение не выходит из цикла исполнителя."""
    try:
        with session_factory() as db:
            unit_filter = (
                WorkFamily.unit_id.is_(None) if unit_id is None else WorkFamily.unit_id == unit_id
            )
            family_ids = (
                db.execute(
                    sa.select(WorkFamily.id).where(
                        WorkFamily.status == FamilyStatus.active.value, unit_filter
                    )
                )
                .scalars()
                .all()
            )
            if family_ids:
                reconcile_family_schemas(
                    db, family_ids, cap=event_cap_from(settings), source="operation"
                )
            db.commit()
    except Exception:  # noqa: BLE001 — исключение сверки наружу не уходит
        logger.exception("Сверка схем единицы %s не удалась", unit_id)


#: Сколько контекстов проход берёт на проверку нужности значений; остальные
#: достаются следующим проходам по курсору.
SWEEP_CONTEXT_LIMIT = 500


def _sweep_family_schemas(session_factory: Callable[[], Session], *, settings: Settings) -> None:
    with session_factory() as db:
        family_ids = without_discarded_schemas(db, families_awaiting_schema(db))
        if family_ids:
            reconcile_family_schemas(
                db, family_ids, cap=event_cap_from(settings), source="operation"
            )
        db.commit()


def _sweep_context_values(
    session_factory: Callable[[], Session], *, settings: Settings, after_context_id: int
) -> int:
    """Окно контекстов с семьёй (целевой или ожидаемой), без заданий значений в
    работе, по возрастанию `id` после курсора; нужность решает сама сверка.
    Возвращает новый курсор: `id` последнего контекста окна, а когда окно
    короче лимита, — `0`: круг пройден."""
    live_job = sa.exists().where(
        SemanticJob.context_id == CatalogContext.id,
        SemanticJob.kind == SemanticJobKind.context_values.value,
        SemanticJob.status.in_(
            (
                SemanticJobStatus.pending.value,
                SemanticJobStatus.running.value,
                SemanticJobStatus.privacy_hold.value,
            )
        ),
    )
    with session_factory() as db:
        context_ids = (
            db.execute(
                sa.select(CatalogContext.id)
                .where(
                    CatalogContext.id > after_context_id,
                    CatalogContext.archived_at.is_(None),
                    sa.func.coalesce(
                        CatalogContext.pending_family_id, CatalogContext.work_family_id
                    ).is_not(None),
                    ~live_job,
                )
                .order_by(CatalogContext.id)
                .limit(SWEEP_CONTEXT_LIMIT)
            )
            .scalars()
            .all()
        )
        eligible = without_discarded_values(db, context_ids)
        if eligible:
            reconcile_context_values(
                db, eligible, cap=event_cap_from(settings), source="operation"
            )
        db.commit()
    return context_ids[-1] if len(context_ids) >= SWEEP_CONTEXT_LIMIT else 0


def sweep_semantic_queue(
    session_factory: Callable[[], Session], *, settings: Settings, after_context_id: int = 0
) -> int:
    """Проход «по кругу» (спека вариантов §2.7): гарантированный повтор для
    предметов, которые сверка операции пропустила под замком (`SKIP LOCKED`) или
    не успела вызвать после записи. Две части, каждая в своей транзакции и со
    своим перехватом: схемы активных семей без текущей и строящейся версии и
    значения контекстов без задания в работе. Потолок события — из настроек
    (сверх него — удержанная пачка, как всегда). Возвращает курсор следующего
    прохода по значениям; исключение наружу не уходит."""
    try:
        _sweep_family_schemas(session_factory, settings=settings)
    except Exception:  # noqa: BLE001 — исключение прохода наружу не уходит
        logger.exception("Проход очереди: сверка схем семей не удалась")
    try:
        return _sweep_context_values(
            session_factory, settings=settings, after_context_id=after_context_id
        )
    except Exception:  # noqa: BLE001 — исключение прохода наружу не уходит
        logger.exception("Проход очереди: сверка значений контекстов не удалась")
        return after_context_id


def _unit_of_job(session_factory: Callable[[], Session], job_id: int) -> int | None:
    with session_factory() as db:
        return db.execute(
            sa.select(SemanticJob.unit_id).where(SemanticJob.id == job_id)
        ).scalar_one_or_none()


def process_one(
    session_factory: Callable[[], Session],
    client: ModelClient,
    *,
    settings: Settings,
    clock: Callable[[], datetime],
) -> bool:
    """Захват, вызов, запись — по сессии на шаг, вызов модели вне транзакции.
    `False` — захватывать нечего. Иначе `True`, и исключение задания наружу не
    уходит: попытка закрывается ошибкой, цикл продолжается (спека §2.5, п. 4).
    После задания предложения (любой исход) — сверка схем его единицы."""
    closed_units: list[int | None] = []
    try:
        with session_factory() as db:
            claim = claim_next(db, settings=settings, now=clock(), closed_units=closed_units)
        if claim is None:
            return False
        try:
            _run_claim(session_factory, client, claim, settings=settings, clock=clock)
        finally:
            if claim.kind == SemanticJobKind.family_suggestion:
                try:
                    closed_units.append(_unit_of_job(session_factory, claim.job_id))
                except Exception:  # noqa: BLE001 — исключение хука наружу не уходит
                    logger.exception("Единица задания %s не прочитана", claim.job_id)
        return True
    finally:
        for unit_id in dict.fromkeys(closed_units):
            _reconcile_schemas_of_unit(session_factory, unit_id, settings=settings)


def _run_claim(
    session_factory: Callable[[], Session],
    client: ModelClient,
    claim: Claim,
    *,
    settings: Settings,
    clock: Callable[[], datetime],
) -> None:
    """Вызов модели и запись результата захваченного задания."""
    if claim.synthesized:
        _complete_synthesized(session_factory, claim, settings=settings, clock=clock)
        return
    assert claim.rendered is not None

    try:
        try:
            response = client.complete(
                claim.rendered.body, timeout_s=settings.SEMANTIC_CALL_TIMEOUT_S
            )
        except (TransientModelError, PermanentModelError) as exc:
            _record_failure_in_new_session(
                session_factory, claim, exc, settings=settings, clock=clock
            )
            return
        except Exception as exc:  # noqa: BLE001 — любое исключение клиента временное
            _record_failure_in_new_session(
                session_factory,
                claim,
                TransientModelError(str(exc), error_class=type(exc).__name__),
                settings=settings,
                clock=clock,
            )
            return

        published_id: int | None = None
        try:
            with session_factory() as db:
                published_id = record_result(db, claim, response, now=clock(), settings=settings)
        except Exception as exc:  # noqa: BLE001 — запись не удалась: попытку закрыть
            logger.exception("Запись результата задания %s не удалась", claim.job_id)
            _record_failure_in_new_session(
                session_factory,
                claim,
                TransientModelError(str(exc), error_class=type(exc).__name__),
                settings=settings,
                clock=clock,
            )
        if published_id is not None:
            _apply_rules_in_new_session(session_factory, published_id, settings=settings)
    except Exception:  # noqa: BLE001 — закрыть попытку не удалось: задание вернёт восстановление
        logger.exception("Попытка задания %s осталась открытой", claim.job_id)
