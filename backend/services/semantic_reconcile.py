"""Сверка `reconcile_semantic_jobs` и удержанные пачки события (спека
`2026-09-28-semantic-suggestions-design.md` §2.7, §2.8, §2.11).

Задача 6 фичи «Семантические предложения»
(`docs/superpowers/plans/2026-09-28-semantic-suggestions.md`). Единственная
точка, которую обязана звать любая транзакция, меняющая применимость
контекста или данные, читаемые `render_context_request` (инвариант спеки
§2.7) — перечень операций в самой спеке, эта функция решений о том, КОГДА
её звать, не несёт.

Материал и текущий отпечаток каждого контекста загружаются пакетно
(`load_request_material`, `render_context_request` — задача 2); все
существующие задания этих контекстов читаются ОДНИМ запросом и разбираются
на классы переходов в памяти — раздел ниже воспроизводит таблицу исходов
спеки §2.7 построчно. Резерв на потолок события (§2.11) не делает лишних
обращений к базе: токены известного префикса узнаются один раз на РАЗЛИЧНЫЙ
`prefix_hash` набора (`_prefix_tokens_by_hash`), а не на контекст — контексты одной
единицы измерения делят один и тот же префикс.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from config import settings
from models import (
    ContextMember,
    FamilySuggestion,
    Lot,
    PositionItem,
    Proposal,
    ReconcileBatchSource,
    ReconcileBatchStatus,
    SemanticCancelReason,
    SemanticJob,
    SemanticJobStatus,
    SemanticReconcileBatch,
    SuggestionUnpublishedReason,
)
from services.semantic_answer import RESPONSE_SCHEMA_VERSION
from services.semantic_cost import (
    EventCap,
    Tariffs,
    event_cap_from,
    exceeds_cap,
    expected_cached_cost_known_prefix,
    known_prefix_tokens,
    reserve_for_known_prefix,
    tariffs_from,
)
from services.semantic_request import (
    PROMPT_VERSION,
    SERIALIZATION_VERSION,
    ContextRequestMaterial,
    RenderedRequest,
    is_applicable,
    load_request_material,
    render_context_request,
)

#: Маркер «без потолка» для подтверждённого `admin` (спека §2.11): отличим от
#: `None`, чтобы вызывающий не мог случайно передать отсутствие аргумента —
#: `cap` обязателен и именован.
NO_CAP = object()

#: Причины отмены, при которых задание ТЕКУЩЕГО отпечатка возвращается в
#: `pending` (множество постановки `E`, спека §2.7 таблица исходов, строки
#: `input_changed`/`not_applicable`/`stale_hold`).
_REVIVABLE_CANCEL_REASONS = frozenset(
    {
        SemanticCancelReason.input_changed.value,
        SemanticCancelReason.not_applicable.value,
        SemanticCancelReason.stale_hold.value,
    }
)

_VALID_SOURCES = {source.value for source in ReconcileBatchSource}

#: Модули, которым разрешена запись в защищённые данные контекстов —
#: членства `context_members`, поля `catalog_contexts.semantic_kind|
#: semantic_state|work_family_id|archived_at`, удаление `position_items` (в том
#: числе каскадом от смет, договоров, тендеров и раундов): в каждом такая запись
#: покрыта сверкой (спека §2.7, «Проверка инварианта»). Архитектурный тест
#: сверяет список с деревом `services`, `crud`, `routers` в обе стороны, структурный
#: — требует у каждого модуля тест точки в `test_semantic_queue_hooks_*.py`.
RECONCILE_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Операции над контекстами: разделение, слияние, перенос, архивирование,
        # решение цели, перенос устаревших — каждая сверяет очередь сама.
        "services.context_operations",
        # Создаёт членства и контексты при маршрутизации; сам сверку не зовёт:
        # его вызывают импорт (`run_import_job`), разовый проход (`run_backfill`)
        # и перенос устаревшего (`accept_transfer`), а сверка идёт в их транзакции.
        "services.context_routing",
        # Смена и снятие вида, назначение и снятие семьи сверяют очередь сами;
        # слияние семей переназначает `work_family_id` без сверки — правки списка
        # семей под инвариант не попадают (спека §2.7, решение спеки 2).
        "services.work_families",
        # Слияние в Review и смена `kind` строки.
        "services.review",
        # Замена сметы удаляет позиции; контексты вытесненной сметы собираются
        # здесь до удаления, сверку зовёт `run_import_job`.
        "services.estimate_import",
        "services.round_import",
        # Удаление договора, тендера, раунда и участника каскадом удаляет позиции.
        "crud.contracts",
        "crud.tenders",
    }
)


@dataclass(frozen=True)
class ReconcileReport:
    """Итог одного вызова `reconcile_semantic_jobs` (план, задача 6)."""

    created: int
    revived: int
    cancelled: int
    republished: int
    unpublished: int
    held_batch_id: int | None


# ---------------------------------------------------------------------------
#  Контексты, затронутые операцией — собираются ДО каскада (спека §2.7)
# ---------------------------------------------------------------------------

def contexts_of_positions(db: Session, position_item_ids: Collection[int]) -> set[int]:
    """Контексты членств этих позиций одним запросом; пустой вход — пустое
    множество без обращения к базе."""
    if not position_item_ids:
        return set()
    return set(
        db.execute(
            sa.select(ContextMember.context_id)
            .where(ContextMember.position_item_id.in_(list(position_item_ids)))
            .distinct()
        )
        .scalars()
        .all()
    )


def contexts_of_estimates(db: Session, estimate_ids: Collection[int]) -> set[int]:
    """Контексты членств ВСЕХ позиций этих смет одним запросом (позиция →
    предложение → лот → смета); пустой вход — пустое множество без запроса.
    Зовётся до удаления смет: после каскада членств уже не найти."""
    if not estimate_ids:
        return set()
    return set(
        db.execute(
            sa.select(ContextMember.context_id)
            .join(PositionItem, PositionItem.id == ContextMember.position_item_id)
            .join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id.in_(list(estimate_ids)))
            .distinct()
        )
        .scalars()
        .all()
    )


# ---------------------------------------------------------------------------
#  fingerprints_hash, held_fingerprints — дедупликация удержанных пачек
# ---------------------------------------------------------------------------

def fingerprints_hash(fingerprints: Sequence[tuple[int, str]]) -> str:
    """sha256 канонической сериализации набора пар, отсортированного по
    `(context_id, request_hash)` — не зависит от порядка входа (спека
    §2.11): перестановка одного набора даёт один и тот же хэш."""
    ordered = sorted(fingerprints)
    canonical = json.dumps(
        [[context_id, request_hash] for context_id, request_hash in ordered],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _render_applicable(
    material_by_context: dict[int, ContextRequestMaterial],
) -> dict[int, RenderedRequest]:
    """Рендер тела и отпечатков ТОЛЬКО применимых контекстов (спека §2.7) —
    настройки читаются в момент вызова (`config.settings`), сигнатура их не
    принимает."""
    return {
        context_id: render_context_request(material, settings=settings)
        for context_id, material in material_by_context.items()
        if is_applicable(material)
    }


def _load_jobs_for_contexts(db: Session, context_ids: list[int]) -> list[SemanticJob]:
    """Все задания набора контекстов (ЛЮБОЙ `request_hash`) одним запросом —
    единственное чтение `semantic_jobs` на весь вызов сверки; классификация
    по видам перехода происходит в памяти, а не повторными запросами."""
    if not context_ids:
        return []
    return list(
        db.execute(sa.select(SemanticJob).where(SemanticJob.context_id.in_(context_ids)))
        .scalars()
        .all()
    )


def _postanovka_set(
    applicable_render: dict[int, RenderedRequest],
    all_jobs: list[SemanticJob],
) -> tuple[list[tuple[int, str]], dict[int, SemanticJob]]:
    """Множество постановки `E` (спека §2.11): контексты, у
    которых задание с ТЕКУЩИМ отпечатком будет создано (нет задания) или
    возвращено в `pending` (`cancelled`/`input_changed`,
    `cancelled`/`not_applicable`, `cancelled`/`stale_hold`). ОДНА функция
    вычисляет `E` и для проверки потолка внутри `reconcile_semantic_jobs`, и
    для отдельного вызова `held_fingerprints`.

    Возвращает `(E, current_by_context)` — второе достаётся вызывающему
    даром: словарь «контекст → его задание ТЕКУЩЕГО отпечатка», нужный
    `reconcile_semantic_jobs` для остальных веток таблицы исходов
    (`done`, `running`, `error`, …), которые сами в `E` не входят."""
    current_by_context: dict[int, SemanticJob] = {}
    for job in all_jobs:
        rendered = applicable_render.get(job.context_id)
        if rendered is not None and job.request_hash == rendered.request_hash:
            current_by_context[job.context_id] = job

    postanovka: list[tuple[int, str]] = []
    for context_id, rendered in applicable_render.items():
        job = current_by_context.get(context_id)
        if job is None or (
            job.status == SemanticJobStatus.cancelled.value
            and job.cancel_reason in _REVIVABLE_CANCEL_REASONS
        ):
            postanovka.append((context_id, rendered.request_hash))
    return postanovka, current_by_context


def held_fingerprints(db: Session, context_ids: Collection[int]) -> list[tuple[int, str]]:
    """Множество постановки `E` для набора контекстов, канонически
    отсортированное по `(context_id, request_hash)` — БЕЗ каких-либо записей
    (preview потолка: план, задача 6). Использует ту же внутреннюю функцию,
    что и `reconcile_semantic_jobs`, — иначе оценка потолка и предъявление
    `held_fingerprints` могли бы разойтись."""
    material_by_context = load_request_material(db, context_ids)
    applicable_render = _render_applicable(material_by_context)
    all_jobs = _load_jobs_for_contexts(db, list(material_by_context))
    postanovka, _current = _postanovka_set(applicable_render, all_jobs)
    return sorted(postanovka)


# ---------------------------------------------------------------------------
#  get_or_create_held_batch — атомарная дедупликация (спека §2.11)
# ---------------------------------------------------------------------------

def get_or_create_held_batch(
    db: Session,
    *,
    fingerprints: Sequence[tuple[int, str]],
    source: str,
    import_job_id: int | None,
    unit_id: int | None,
    reserve_estimate_usd: Decimal,
    cached_estimate_usd: Decimal,
) -> int:
    """`INSERT ... ON CONFLICT (fingerprints_hash) WHERE status='held' DO
    NOTHING RETURNING id`; при конфликте — `SELECT` существующей `held`-пачки
    (спека §2.11, круг 2 гейта 3). Предикат `WHERE status = 'held'` —
    ЛИТЕРАЛ в `index_where` (`sa.text`), не связанный параметр: связанный
    параметр перестаёт находить индекс частичного уникального ограничения
    после порога подготовки запросов psycopg3
    (`docs/insights/batch-larger-than-five.md`) — тот же класс дефекта, что
    `services/context_routing.py::_bucket_conflict_arbiter`."""
    if source not in _VALID_SOURCES:
        raise ValueError(f"неизвестный источник пачки: {source!r}")

    ordered = sorted(fingerprints)
    payload = [[context_id, request_hash] for context_id, request_hash in ordered]
    fp_hash = fingerprints_hash(ordered)

    stmt = (
        pg_insert(SemanticReconcileBatch)
        .values(
            source=source,
            import_job_id=import_job_id,
            unit_id=unit_id,
            held_fingerprints=payload,
            fingerprints_hash=fp_hash,
            contexts_count=len(ordered),
            reserve_estimate_usd=reserve_estimate_usd,
            cached_estimate_usd=cached_estimate_usd,
            status=ReconcileBatchStatus.held.value,
        )
        .on_conflict_do_nothing(
            index_elements=[SemanticReconcileBatch.fingerprints_hash],
            index_where=sa.text("status = 'held'"),
        )
        .returning(SemanticReconcileBatch.id)
    )
    row = db.execute(stmt).first()
    if row is not None:
        return row.id

    return db.execute(
        sa.select(SemanticReconcileBatch.id).where(
            SemanticReconcileBatch.fingerprints_hash == fp_hash,
            SemanticReconcileBatch.status == ReconcileBatchStatus.held.value,
        )
    ).scalar_one()


# ---------------------------------------------------------------------------
#  reserve_for(E) / expected_cached_cost(E) — без лишних запросов префикса
# ---------------------------------------------------------------------------

def _prefix_tokens_by_hash(
    db: Session,
    postanovka: list[tuple[int, str]],
    applicable_render: dict[int, RenderedRequest],
) -> dict[str, int | None]:
    """Токены известного префикса по `E` — один запрос `known_prefix_tokens`
    на РАЗЛИЧНЫЙ `prefix_hash` набора, а не на контекст: контексты одной
    единицы делят один и тот же префикс. Общий вход обеих сумм ниже."""
    known_by_prefix: dict[str, int | None] = {}
    for context_id, _request_hash in postanovka:
        prefix_hash = applicable_render[context_id].prefix_hash
        if prefix_hash not in known_by_prefix:
            known_by_prefix[prefix_hash] = known_prefix_tokens(db, prefix_hash)
    return known_by_prefix


def _reserve_total(
    known_by_prefix: dict[str, int | None],
    postanovka: list[tuple[int, str]],
    applicable_render: dict[int, RenderedRequest],
    tariffs: Tariffs,
    max_tokens: int,
) -> Decimal:
    """Σ `reserve_for` по `E`; без наблюдения префикса — его байты."""
    total = Decimal("0")
    for context_id, _request_hash in postanovka:
        rendered = applicable_render[context_id]
        known = known_by_prefix[rendered.prefix_hash]
        prefix_tokens = known if known is not None else rendered.prefix_bytes
        total += reserve_for_known_prefix(prefix_tokens, rendered, tariffs, max_tokens)
    return total


def _cached_total(
    known_by_prefix: dict[str, int | None],
    postanovka: list[tuple[int, str]],
    applicable_render: dict[int, RenderedRequest],
    tariffs: Tariffs,
) -> Decimal:
    """Σ `expected_cached_cost` по `E`; без наблюдения префикса — его байты."""
    total = Decimal("0")
    for context_id, _request_hash in postanovka:
        rendered = applicable_render[context_id]
        known = known_by_prefix[rendered.prefix_hash]
        prefix_tokens = known if known is not None else rendered.prefix_bytes
        total += expected_cached_cost_known_prefix(prefix_tokens, rendered, tariffs)
    return total


# ---------------------------------------------------------------------------
#  Мутации — по одному UPDATE/INSERT на вид перехода, не по строке
# ---------------------------------------------------------------------------

def _cancel_where_status(
    db: Session, job_ids: list[int], *, from_statuses: tuple[str, ...], cancel_reason: str
) -> int:
    """Один `UPDATE ... WHERE id IN (...) AND status IN (...)` на вид
    перехода (спека §2.7): условно
    по статусу — гонка с захватом (§2.5), где захват победил, отменяет ноль
    строк, и это не ошибка."""
    if not job_ids:
        return 0
    result = db.execute(
        sa.update(SemanticJob)
        .where(SemanticJob.id.in_(job_ids), SemanticJob.status.in_(from_statuses))
        .values(status=SemanticJobStatus.cancelled.value, cancel_reason=cancel_reason)
    )
    return result.rowcount


def _revive_jobs(db: Session, job_ids: list[int]) -> int:
    """Возврат в `pending` — ТО ЖЕ задание (спека §2.7): `cancel_reason` снят, `retry_generation` инкрементирован,
    попытки текущего поколения и отсрочка обнулены, разрешение приватности
    и класс последней ошибки сброшены. Условно по `status = 'cancelled'` —
    та же гонка с захватом, что у `_cancel_where_status`."""
    if not job_ids:
        return 0
    result = db.execute(
        sa.update(SemanticJob)
        .where(SemanticJob.id.in_(job_ids), SemanticJob.status == SemanticJobStatus.cancelled.value)
        .values(
            status=SemanticJobStatus.pending.value,
            cancel_reason=None,
            retry_generation=SemanticJob.retry_generation + 1,
            attempts_in_generation=0,
            next_attempt_at=sa.func.now(),
            privacy_released_matches=None,
            last_error_class=None,
        )
    )
    return result.rowcount


def _insert_new_jobs(
    db: Session,
    create_context_ids: list[int],
    applicable_render: dict[int, RenderedRequest],
    material_by_context: dict[int, ContextRequestMaterial],
) -> int:
    """Один `INSERT ... ON CONFLICT (context_id, request_hash) DO NOTHING`
    на весь набор (спека §2.7) — все аудиторские
    колонки из `RenderedRequest` и модулей запроса/ответа; колонки, текстовые
    в схеме, но целочисленные по своей природе (`prompt_version`,
    `response_schema_version`, `serialization_version`), приводятся к `str`
    явно."""
    if not create_context_ids:
        return 0
    rows = [
        {
            "context_id": context_id,
            "request_hash": applicable_render[context_id].request_hash,
            "status": SemanticJobStatus.pending.value,
            "unit_id": material_by_context[context_id].unit_id,
            "prompt_version": str(PROMPT_VERSION),
            "model_requested": settings.SEMANTIC_MODEL,
            "place_dictionary_version": applicable_render[context_id].place_dictionary_version,
            "candidates_hash": applicable_render[context_id].candidates_hash,
            "prefix_hash": applicable_render[context_id].prefix_hash,
            "input_hash": applicable_render[context_id].input_hash,
            "response_schema_version": str(RESPONSE_SCHEMA_VERSION),
            "serialization_version": str(SERIALIZATION_VERSION),
        }
        for context_id in create_context_ids
    ]
    stmt = (
        pg_insert(SemanticJob)
        .on_conflict_do_nothing(index_elements=[SemanticJob.context_id, SemanticJob.request_hash])
        .returning(SemanticJob.id)
    )
    result = db.execute(stmt, rows)
    return len(result.fetchall())


def _unpublish_where_published(db: Session, context_ids: list[int], *, reason: str) -> int:
    """Один `UPDATE` — снимает публикацию у ВСЕХ опубликованных предложений
    перечисленных контекстов, условно по `is_published`."""
    if not context_ids:
        return 0
    result = db.execute(
        sa.update(FamilySuggestion)
        .where(FamilySuggestion.context_id.in_(context_ids), FamilySuggestion.is_published.is_(True))
        .values(is_published=False, unpublished_reason=reason)
    )
    return result.rowcount


def _publish_suggestions(db: Session, suggestion_ids: list[int]) -> int:
    if not suggestion_ids:
        return 0
    result = db.execute(
        sa.update(FamilySuggestion)
        .where(FamilySuggestion.id.in_(suggestion_ids))
        .values(is_published=True, unpublished_reason=None)
    )
    return result.rowcount


# ---------------------------------------------------------------------------
#  reconcile_semantic_jobs
# ---------------------------------------------------------------------------

def reconcile_semantic_jobs(
    db: Session,
    context_ids: Collection[int],
    *,
    cap: EventCap | object,
    source: str,
    import_job_id: int | None = None,
) -> ReconcileReport:
    """Сверка очереди семантических предложений с текущим состоянием набора
    контекстов (спека §2.7, инвариант). Одна транзакция ВЫЗЫВАЮЩЕГО — `commit` эта функция
    не делает.

    Порядок: (1) материал, применимость, рендер применимых; (2) все задания
    набора одним запросом; (3) неприменимые контексты — их незавершённые
    задания отменяются, опубликованное предложение снимается; (4) применимые
    контексты, задания СТАРОГО отпечатка — отменяются; (5) задание ТЕКУЩЕГО
    отпечатка — по таблице исходов спеки §2.7 (публикация решается здесь,
    вызова модели нет); (6) множество постановки `E` (создание/возврат в
    `pending`) — либо выполняется целиком, либо не выполняется вовсе и
    заменяется удержанной пачкой (потолок события, §2.11). Отмены, снятие
    публикации и повторная публикация выполняются ПРИ ЛЮБОМ потолке — денег
    не стоят."""
    if source not in _VALID_SOURCES:
        raise ValueError(f"неизвестный источник пачки: {source!r}")
    if cap is not NO_CAP and not isinstance(cap, EventCap):
        raise TypeError("cap обязан быть EventCap или NO_CAP")

    # 1. Материал, применимость, рендер применимых.
    material_by_context = load_request_material(db, context_ids)
    applicable_render = _render_applicable(material_by_context)
    applicable_ids = set(applicable_render)
    inapplicable_ids = [cid for cid in material_by_context if cid not in applicable_ids]

    # 2. Все задания найденных контекстов одним запросом.
    all_jobs = _load_jobs_for_contexts(db, list(material_by_context))

    cancelled_count = 0
    unpublished_count = 0
    republished_count = 0

    # 3. Неприменимые: незавершённые задания — cancelled/not_applicable;
    #    опубликованное предложение теряет публикацию.
    inapplicable_set = set(inapplicable_ids)
    if inapplicable_set:
        ids_inapplicable_open = [job.id for job in all_jobs if job.context_id in inapplicable_set]
        cancelled_count += _cancel_where_status(
            db,
            ids_inapplicable_open,
            from_statuses=(SemanticJobStatus.pending.value, SemanticJobStatus.privacy_hold.value),
            cancel_reason=SemanticCancelReason.not_applicable.value,
        )
        unpublished_count += _unpublish_where_published(
            db, inapplicable_ids, reason=SuggestionUnpublishedReason.context_not_applicable.value
        )

    # 4. Применимые, задания СТАРОГО отпечатка: pending → input_changed,
    #    privacy_hold → stale_hold.
    ids_old_pending: list[int] = []
    ids_old_privacy_hold: list[int] = []
    for job in all_jobs:
        rendered = applicable_render.get(job.context_id)
        if rendered is None or job.request_hash == rendered.request_hash:
            continue
        if job.status == SemanticJobStatus.pending.value:
            ids_old_pending.append(job.id)
        elif job.status == SemanticJobStatus.privacy_hold.value:
            ids_old_privacy_hold.append(job.id)
    cancelled_count += _cancel_where_status(
        db,
        ids_old_pending,
        from_statuses=(SemanticJobStatus.pending.value,),
        cancel_reason=SemanticCancelReason.input_changed.value,
    )
    cancelled_count += _cancel_where_status(
        db,
        ids_old_privacy_hold,
        from_statuses=(SemanticJobStatus.privacy_hold.value,),
        cancel_reason=SemanticCancelReason.stale_hold.value,
    )

    # 5. Задание ТЕКУЩЕГО отпечатка — множество постановки E (общая функция
    #    с held_fingerprints) плюс повторная публикация `done`.
    postanovka, current_by_context = _postanovka_set(applicable_render, all_jobs)

    done_with_suggestion_ids = [
        job.result_suggestion_id
        for job in current_by_context.values()
        if job.status == SemanticJobStatus.done.value and job.result_suggestion_id is not None
    ]
    if done_with_suggestion_ids:
        suggestion_rows = db.execute(
            sa.select(
                FamilySuggestion.id, FamilySuggestion.context_id, FamilySuggestion.decision,
                FamilySuggestion.is_published,
            ).where(FamilySuggestion.id.in_(done_with_suggestion_ids))
        ).all()
        to_republish = [
            (row.id, row.context_id)
            for row in suggestion_rows
            if row.decision is None and not row.is_published
        ]
        if to_republish:
            republish_context_ids = [context_id for _sid, context_id in to_republish]
            republish_ids = [sid for sid, _cid in to_republish]
            # Сперва снять публикацию у ДРУГИХ опубликованных предложений
            # этого же контекста: частичный уникальный индекс допускает одно
            # опубликованное предложение на контекст.
            unpublished_count += _unpublish_where_published(
                db, republish_context_ids, reason=SuggestionUnpublishedReason.stale_fingerprint.value
            )
            republished_count += _publish_suggestions(db, republish_ids)

    # 6. Потолок события (§2.11): E выполняется целиком либо заменяется
    #    удержанной пачкой ровно с этими парами.
    create_ids = [context_id for context_id, _h in postanovka if context_id not in current_by_context]
    revive_job_ids = [
        current_by_context[context_id].id
        for context_id, _h in postanovka
        if context_id in current_by_context
    ]

    held_batch_id: int | None = None
    created_count = 0
    revived_count = 0

    if cap is NO_CAP:
        proceed = True
    else:
        tariffs = tariffs_from(settings)
        known_by_prefix = _prefix_tokens_by_hash(db, postanovka, applicable_render)
        reserve_total = _reserve_total(
            known_by_prefix, postanovka, applicable_render, tariffs, settings.SEMANTIC_MAX_TOKENS
        )
        proceed = not exceeds_cap(len(postanovka), reserve_total, cap)

    if proceed:
        created_count = _insert_new_jobs(db, create_ids, applicable_render, material_by_context)
        revived_count = _revive_jobs(db, revive_job_ids)
    else:
        cached_total = _cached_total(known_by_prefix, postanovka, applicable_render, tariffs)
        held_batch_id = get_or_create_held_batch(
            db,
            fingerprints=postanovka,
            source=source,
            import_job_id=import_job_id,
            unit_id=None,
            reserve_estimate_usd=reserve_total,
            cached_estimate_usd=cached_total,
        )

    return ReconcileReport(
        created=created_count,
        revived=revived_count,
        cancelled=cancelled_count,
        republished=republished_count,
        unpublished=unpublished_count,
        held_batch_id=held_batch_id,
    )


# ---------------------------------------------------------------------------
#  Одна сверка на операцию над несколькими элементами
# ---------------------------------------------------------------------------

#: Контексты, которые копит активный `deferred_reconcile`; `None` — сверка
#: идёт сразу.
_DEFERRED_CONTEXTS: ContextVar[set[int] | None] = ContextVar("deferred_contexts", default=None)


def reconcile_or_defer(
    db: Session, context_ids: Collection[int], *, source: str = "operation"
) -> ReconcileReport | None:
    """Точка инварианта операции: сверяет контексты сразу с потолком события из
    настроек либо, внутри `deferred_reconcile`, только копит их и возвращает
    `None` (сверку сделает выход из блока)."""
    accumulated = _DEFERRED_CONTEXTS.get()
    if accumulated is not None:
        accumulated.update(context_ids)
        return None
    return reconcile_semantic_jobs(db, context_ids, cap=event_cap_from(settings), source=source)


@contextmanager
def deferred_reconcile(db: Session, *, source: str = "operation") -> Iterator[None]:
    """Операция над несколькими элементами сверяет очередь ОДИН раз: потолок
    события (спека §2.11) действует на сверку транзакции, а не на каждый элемент.

    Внутри блока точки, звавшие бы сверку через `reconcile_or_defer`, только
    копят контексты; при нормальном выходе — одна `reconcile_semantic_jobs` по
    объединению; при исключении накопленное отбрасывается и исключение
    летит дальше. Вложенный блок присоединяется к внешнему."""
    if _DEFERRED_CONTEXTS.get() is not None:
        yield
        return
    accumulated: set[int] = set()
    token = _DEFERRED_CONTEXTS.set(accumulated)
    try:
        yield
    finally:
        _DEFERRED_CONTEXTS.reset(token)
    if accumulated:
        reconcile_semantic_jobs(db, accumulated, cap=event_cap_from(settings), source=source)
