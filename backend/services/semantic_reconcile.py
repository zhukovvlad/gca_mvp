"""Сверка очереди для трёх видов заданий и удержанные пачки события (спеки
`2026-09-28-semantic-suggestions-design.md` §2.7, §2.8, §2.11 и
`2026-10-02-catalog-variants-design.md` §2.6, §2.7, §2.8).

Единственная точка, которую обязана звать любая транзакция, меняющая
применимость контекста, данные запроса или состояние схемы семьи (инвариант
спеки §2.7) — перечень операций в самой спеке, эта функция решений о том, КОГДА
её звать, не несёт.

Виды заданий и их предмет: `family_suggestion` — контекст; `context_values` —
контекст, версия схемы и хэш путей (ставится, только когда нужен: есть
ожидание, нет варианта или его версия не текущая, пути разошлись с путями
варианта); `family_schema` — семья и версия `building` (версия заводится
активной семье без текущей, когда перезапрос её единицы окончен).
Таблица исходов фичи «Семантические предложения» действует для каждого вида по
его предмету и текущему отпечатку.

Работа идёт в два шага. План (`_plan_all`) читает базу и ничего не пишет:
материал и отпечатки загружаются пакетно, все задания набора читаются
одним запросом на вид и разбираются в памяти на отмены и множество постановки
`E`. Исполнение (`_execute_plan`) выполняет отмены при любом потолке, а `E`
всех видов вместе — либо целиком, либо одной удержанной пачкой отпечатков
(`Fingerprint`), если потолок события (§2.11) превышен. Резерв считается
тарифами вида каждого задания; токены известного префикса узнаются один раз на
РАЗЛИЧНЫЙ `prefix_hash` набора, а не на задание. Те же функции плана питают
preview `admin` (`held_fingerprints`, `estimate_enqueue`).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased

from config import settings
from models import (
    CatalogContext,
    CatalogKind,
    CatalogPosition,
    ContextBucket,
    ContextMember,
    ContextParameterValue,
    FamilyParameterSchema,
    FamilyStatus,
    FamilySuggestion,
    Lot,
    PositionItem,
    Proposal,
    ReconcileBatchSource,
    ReconcileBatchStatus,
    SchemaOrigin,
    SchemaStatus,
    SemanticCancelReason,
    SemanticJob,
    SemanticJobKind,
    SemanticJobStatus,
    SemanticKind,
    SemanticReconcileBatch,
    SemanticState,
    SuggestionUnpublishedReason,
    ValueSource,
    WorkFamily,
    WorkVariant,
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
from services.variant_request import (
    SCHEMA_PROMPT_VERSION,
    VALUES_PROMPT_VERSION,
    ValuesRequestMaterial,
    load_schema_material,
    load_values_material,
    paths_hash_of,
    render_schema_request,
    render_values_request,
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

#: Статусы заданий неприменимого контекста, которые сверка отменяет.
_CANCELLED_WHEN_INAPPLICABLE = (
    SemanticJobStatus.pending.value,
    SemanticJobStatus.privacy_hold.value,
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
        # Запрос смены семьи ставит и снимает ожидающую семью контекста
        # (`pending_family_id`) и зовёт сверку сам: ожидание определяет, по
        # схеме какой семьи ставится задание значений.
        "services.family_change",
        # Обработчик результата значений меняет вариант, ожидающую и текущую
        # семью контекста и строку каталога; волну после расширений ставит сам.
        # «Не работа» контексту снимает семью и ставит `NOT_APPLICABLE`, сверку
        # зовёт сама.
        "services.work_variants",
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


@dataclass(frozen=True)
class PreparedContexts:
    """Материал набора контекстов и рендер применимых из него — дорогая часть
    сверки и оценки постановки. Каждый вызов читает вход заново: общий снимок
    между оценкой и сверкой пропустил бы коммит параллельного импорта."""

    material_by_context: dict[int, ContextRequestMaterial]
    applicable_render: dict[int, RenderedRequest]


def prepare_contexts(db: Session, context_ids: Collection[int]) -> PreparedContexts:
    material_by_context = load_request_material(db, context_ids)
    return PreparedContexts(material_by_context, _render_applicable(material_by_context))


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
#  Fingerprint, fingerprints_hash — отпечаток предмета задания и дедупликация
#  удержанных пачек
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Fingerprint:
    """Отпечаток задания любого из трёх видов (спека §2.4, §2.7): предмет и
    `request_hash`. Предмет `family_suggestion` — контекст; `context_values` —
    контекст и версия схемы; `family_schema` — семья и версия схемы (у ещё не
    заведённой версии `schema_id` пуст)."""

    kind: SemanticJobKind
    context_id: int | None
    family_id: int | None
    schema_id: int | None
    request_hash: str

    def as_dict(self) -> dict:
        return {
            "kind": SemanticJobKind(self.kind).value,
            "context_id": self.context_id,
            "family_id": self.family_id,
            "schema_id": self.schema_id,
            "request_hash": self.request_hash,
        }

    @classmethod
    def from_dict(cls, element: dict) -> Fingerprint:
        return cls(
            kind=SemanticJobKind(element["kind"]),
            context_id=element["context_id"],
            family_id=element["family_id"],
            schema_id=element["schema_id"],
            request_hash=element["request_hash"],
        )


def _fingerprint_sort_key(fingerprint: Fingerprint) -> tuple:
    return (
        SemanticJobKind(fingerprint.kind).value,
        fingerprint.context_id or -1,
        fingerprint.family_id or -1,
        fingerprint.schema_id or -1,
        fingerprint.request_hash,
    )


def fingerprints_hash(fingerprints: Sequence[Fingerprint]) -> str:
    """sha256 канонической сериализации набора отпечатков, отсортированного по
    `(kind, context_id, family_id, schema_id, request_hash)` с `-1` вместо
    отсутствующего предмета — не зависит от порядка входа (спека §2.7).
    Байт в байт канон перевода пачек миграцией 0019."""
    ordered = sorted(fingerprints, key=_fingerprint_sort_key)
    canonical = json.dumps(
        [fingerprint.as_dict() for fingerprint in ordered],
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
    """Все задания набора контекстов (ЛЮБОЙ вид и `request_hash`) одним
    запросом — единственное чтение `semantic_jobs` по контекстам на весь вызов
    сверки; разбор по видам и классам переходов происходит в памяти, а не
    повторными запросами."""
    if not context_ids:
        return []
    return list(
        db.execute(sa.select(SemanticJob).where(SemanticJob.context_id.in_(context_ids)))
        .scalars()
        .all()
    )


# ---------------------------------------------------------------------------
#  План сверки: что отменить, создать, вернуть в pending. Чтение без записей.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _NewJob:
    fingerprint: Fingerprint
    rendered: RenderedRequest
    row: dict


@dataclass(frozen=True)
class _Revival:
    job_id: int
    fingerprint: Fingerprint
    rendered: RenderedRequest


@dataclass
class _Plan:
    """Решения одной ветви сверки (вида заданий). Отмены выполняются при любом
    потолке — денег не стоят; создания и возвраты в `pending` образуют множество
    постановки `E` (спека §2.11)."""

    cancels: list[tuple[list[int], tuple[str, ...], str]] = field(default_factory=list)
    creates: list[_NewJob] = field(default_factory=list)
    revivals: list[_Revival] = field(default_factory=list)
    # Отпечатки заданий, не вызывающих модель (схема без параметров): в резерв
    # потолка дают 0, но считаются в числе контекстов события.
    free: set[Fingerprint] = field(default_factory=set)

    def entries(self) -> list[tuple[Fingerprint, RenderedRequest]]:
        return [(new.fingerprint, new.rendered) for new in self.creates] + [
            (revival.fingerprint, revival.rendered) for revival in self.revivals
        ]

    def locked_job_ids(self) -> list[int]:
        return [job_id for ids, _statuses, _reason in self.cancels for job_id in ids] + [
            revival.job_id for revival in self.revivals
        ]


@dataclass
class _FullPlan:
    suggestions: _Plan = field(default_factory=_Plan)
    values: _Plan = field(default_factory=_Plan)
    schemas: _Plan = field(default_factory=_Plan)
    inapplicable_suggestion_contexts: list[int] = field(default_factory=list)
    done_suggestion_ids: list[int] = field(default_factory=list)
    prepared: PreparedContexts | None = None

    def plans(self) -> list[_Plan]:
        return [self.suggestions, self.values, self.schemas]

    def entries(self) -> list[tuple[Fingerprint, RenderedRequest]]:
        return [entry for plan in self.plans() for entry in plan.entries()]

    def free_fingerprints(self) -> set[Fingerprint]:
        return {fingerprint for plan in self.plans() for fingerprint in plan.free}


def _job_row(
    fingerprint: Fingerprint,
    rendered: RenderedRequest,
    *,
    unit_id: int | None,
    prompt_version: int,
    paths_hash: str | None = None,
) -> dict:
    """Столбцы нового задания: предмет, отпечаток и оси запроса для журнала.
    Колонки, текстовые в схеме, но целочисленные по природе (версии),
    приводятся к `str` явно."""
    return {
        "kind": SemanticJobKind(fingerprint.kind).value,
        "context_id": fingerprint.context_id,
        "family_id": fingerprint.family_id,
        "schema_id": fingerprint.schema_id,
        "paths_hash": paths_hash,
        "request_hash": rendered.request_hash,
        "status": SemanticJobStatus.pending.value,
        "unit_id": unit_id,
        "prompt_version": str(prompt_version),
        "model_requested": rendered.body["model"],
        "place_dictionary_version": rendered.place_dictionary_version,
        "candidates_hash": rendered.candidates_hash,
        "prefix_hash": rendered.prefix_hash,
        "input_hash": rendered.input_hash,
        "response_schema_version": str(RESPONSE_SCHEMA_VERSION),
        "serialization_version": str(SERIALIZATION_VERSION),
    }


def _plan_inapplicable(plan: _Plan, jobs: Collection[SemanticJob]) -> None:
    """Предмет неприменим: незавершённые задания — `cancelled/not_applicable`;
    выполняющееся сверка не трогает (замок на него держал бы запись его
    результата)."""
    open_ids = [job.id for job in jobs if job.status in _CANCELLED_WHEN_INAPPLICABLE]
    plan.cancels.append(
        (open_ids, _CANCELLED_WHEN_INAPPLICABLE, SemanticCancelReason.not_applicable.value)
    )


def _plan_applicable(
    plan: _Plan, jobs: Collection[SemanticJob], current_key: tuple[int | None, str]
) -> SemanticJob | None:
    """Задания применимого предмета: с отпечатком не ТЕКУЩИМ (версия схемы и
    `request_hash`) `pending` -> `input_changed`, `privacy_hold` -> `stale_hold`.
    Возвращает задание текущего отпечатка, если оно есть."""
    current: SemanticJob | None = None
    old_pending: list[int] = []
    old_hold: list[int] = []
    for job in jobs:
        if (job.schema_id, job.request_hash) == current_key:
            current = job
        elif job.status == SemanticJobStatus.pending.value:
            old_pending.append(job.id)
        elif job.status == SemanticJobStatus.privacy_hold.value:
            old_hold.append(job.id)
    plan.cancels.append(
        (old_pending, (SemanticJobStatus.pending.value,), SemanticCancelReason.input_changed.value)
    )
    plan.cancels.append(
        (
            old_hold,
            (SemanticJobStatus.privacy_hold.value,),
            SemanticCancelReason.stale_hold.value,
        )
    )
    return current


def _is_revivable(job: SemanticJob, *, revive_done: bool = False) -> bool:
    if revive_done and job.status == SemanticJobStatus.done.value:
        return True
    return (
        job.status == SemanticJobStatus.cancelled.value
        and job.cancel_reason in _REVIVABLE_CANCEL_REASONS
    )


def _plan_postanovka(
    plan: _Plan, current: SemanticJob | None, new_job: _NewJob, *, revive_done: bool = False
) -> None:
    """Множество постановки `E` (спека §2.11): нет задания текущего отпечатка —
    создать; оно `cancelled` по `input_changed`/`not_applicable`/`stale_hold` —
    вернуть в `pending`. Остальные исходы таблицы (`done`, `running`, `error`,
    `pending`, ...) в `E` не входят. `revive_done` — задание значений: нужное, но
    уже выполненное по ТЕКУЩЕМУ отпечатку (пути вернулись к прежним) ставится
    заново, иначе уникальный ключ навсегда оставил бы контекст без перезапроса."""
    if current is None:
        plan.creates.append(new_job)
    elif _is_revivable(current, revive_done=revive_done):
        plan.revivals.append(_Revival(current.id, new_job.fingerprint, new_job.rendered))


def _group_by(jobs: Collection[SemanticJob], key) -> dict:
    grouped: dict = {}
    for job in jobs:
        grouped.setdefault(key(job), []).append(job)
    return grouped


def _plan_suggestions(
    prepared: PreparedContexts, jobs: Collection[SemanticJob], full: _FullPlan
) -> None:
    """Ветвь `family_suggestion` — таблица исходов фичи «Семантические
    предложения» по предмету «контекст»."""
    material_by_context = prepared.material_by_context
    applicable_render = prepared.applicable_render
    plan = full.suggestions
    jobs_by_context = _group_by(jobs, lambda job: job.context_id)
    inapplicable_ids = [cid for cid in material_by_context if cid not in applicable_render]
    full.inapplicable_suggestion_contexts = inapplicable_ids
    for context_id in inapplicable_ids:
        _plan_inapplicable(plan, jobs_by_context.get(context_id, []))
    done_ids: list[int] = []
    for context_id, rendered in applicable_render.items():
        current = _plan_applicable(
            plan, jobs_by_context.get(context_id, []), (None, rendered.request_hash)
        )
        fingerprint = Fingerprint(
            SemanticJobKind.family_suggestion, context_id, None, None, rendered.request_hash
        )
        _plan_postanovka(plan, current, _NewJob(fingerprint, rendered, {}))
        if (
            current is not None
            and current.status == SemanticJobStatus.done.value
            and current.result_suggestion_id is not None
        ):
            done_ids.append(current.result_suggestion_id)
    full.done_suggestion_ids = done_ids


@dataclass(frozen=True)
class _ValuesColumns:
    pending_family_id: int | None
    work_variant_id: int | None
    variant_paths_hash: str | None
    variant_schema_id: int | None
    catalog_kind: str


def _load_values_columns(db: Session, context_ids: Collection[int]) -> dict[int, _ValuesColumns]:
    variant = aliased(WorkVariant)
    rows = db.execute(
        sa.select(
            CatalogContext.id,
            CatalogContext.pending_family_id,
            CatalogContext.work_variant_id,
            CatalogContext.variant_paths_hash,
            variant.schema_id.label("variant_schema_id"),
            CatalogPosition.kind.label("catalog_kind"),
        )
        .select_from(CatalogContext)
        .join(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
        .join(CatalogPosition, CatalogPosition.id == ContextBucket.catalog_position_id)
        .outerjoin(variant, variant.id == CatalogContext.work_variant_id)
        .where(CatalogContext.id.in_(list(context_ids)))
    ).all()
    return {
        row.id: _ValuesColumns(
            row.pending_family_id, row.work_variant_id, row.variant_paths_hash,
            row.variant_schema_id, row.catalog_kind,
        )
        for row in rows
    }


_VALUES_CATALOG_KINDS = (CatalogKind.TO_REVIEW.value, CatalogKind.POSITION.value)


def _values_applicable(material: ContextRequestMaterial, columns: _ValuesColumns) -> bool:
    """Применимость `context_values` (спека §2.6): предикат §2.5 без условия
    кандидатов — не архивирован, есть членства, не `NOT_APPLICABLE`, не `SYSTEM`,
    строка каталога `TO_REVIEW` или `POSITION`. Путь вычислим и схема есть —
    это уже наличие материала значений."""
    return (
        not material.archived
        and material.member_count > 0
        and material.semantic_state != SemanticState.NOT_APPLICABLE.value
        and material.semantic_kind != SemanticKind.SYSTEM.value
        and columns.catalog_kind in _VALUES_CATALOG_KINDS
    )


def _values_needed(columns: _ValuesColumns, material: ValuesRequestMaterial) -> bool:
    """Нужность задания значений (спека §2.6, «Когда ставятся»): есть ожидание;
    версия схемы варианта не текущая у целевой семьи (у контекста без варианта
    её нет, и он сюда же попадает); у текущей версии хотя бы один параметр и пути контекста разошлись с путями варианта.
    Иначе каждое расширение списка и каждая смена конфигурации перезапрашивали
    бы всю семью."""
    if columns.pending_family_id is not None:
        return True
    if columns.variant_schema_id != material.schema_id:
        return True
    return len(material.parameters) >= 1 and columns.variant_paths_hash != paths_hash_of(
        material.paths
    )


def _plan_context_values(
    db: Session,
    material_by_context: dict[int, ContextRequestMaterial],
    jobs: Collection[SemanticJob],
    plan: _Plan,
    *,
    force_needed: bool = False,
) -> None:
    """Ветвь `context_values` — таблица исходов по предмету «контекст + версия
    схемы» с текущим отпечатком; новое ставится только нужным контекстам
    (`force_needed` — волна после расширений ставит минуя нужность)."""
    if not material_by_context:
        return
    ids = list(material_by_context)
    values_material = load_values_material(db, ids)
    columns = _load_values_columns(db, list(values_material)) if values_material else {}
    applicable = {
        cid
        for cid, material in values_material.items()
        if _values_applicable(material_by_context[cid], columns[cid])
    }
    jobs_by_context = _group_by(jobs, lambda job: job.context_id)
    for context_id in ids:
        if context_id not in applicable:
            _plan_inapplicable(plan, jobs_by_context.get(context_id, []))
    for context_id in sorted(applicable):
        material = values_material[context_id]
        rendered = render_values_request(material, settings=settings)
        current = _plan_applicable(
            plan, jobs_by_context.get(context_id, []), (material.schema_id, rendered.request_hash)
        )
        if not force_needed and not _values_needed(columns[context_id], material):
            continue
        fingerprint = Fingerprint(
            SemanticJobKind.context_values, context_id, None, material.schema_id,
            rendered.request_hash,
        )
        row = _job_row(
            fingerprint, rendered, unit_id=material_by_context[context_id].unit_id,
            prompt_version=VALUES_PROMPT_VERSION, paths_hash=paths_hash_of(material.paths),
        )
        _plan_postanovka(plan, current, _NewJob(fingerprint, rendered, row), revive_done=True)
        if not material.parameters:
            plan.free.add(fingerprint)


# Версия схемы, которой ещё нет: на тело запроса `family_schema` номер версии не
# влияет, отпечаток же хранит пустой `schema_id`.
_UNBUILT_SCHEMA_ID = 0


def _open_suggestion_state(db: Session) -> tuple[bool, set[int | None]]:
    """Где единица ещё перезапрашивается (спека §2.6, «Когда ставятся»):
    `(есть ли пачка mass с отпечатками предложений, единицы с заданиями
    предложений в pending/running или с удержанной пачкой, касающейся их
    отпечатками предложений)`. Пачка не-`mass` касается единицы контекстов своих
    отпечатков: `unit_id` самой пачки у автоматических пуст. Отпечатки других
    видов единицу не держат."""
    units: set[int | None] = set(
        db.execute(
            sa.select(SemanticJob.unit_id)
            .where(
                SemanticJob.kind == SemanticJobKind.family_suggestion.value,
                SemanticJob.status.in_(
                    (SemanticJobStatus.pending.value, SemanticJobStatus.running.value)
                ),
            )
            .distinct()
        )
        .scalars()
        .all()
    )
    mass = False
    held_contexts: set[int] = set()
    batches = db.execute(
        sa.select(SemanticReconcileBatch.source, SemanticReconcileBatch.held_fingerprints).where(
            SemanticReconcileBatch.status == ReconcileBatchStatus.held.value
        )
    ).all()
    for source, held in batches:
        contexts = {
            element["context_id"]
            for element in held
            if isinstance(element, dict)
            and element.get("kind") == SemanticJobKind.family_suggestion.value
        }
        if not contexts:
            continue
        if source == ReconcileBatchSource.mass.value:
            mass = True
        held_contexts |= contexts
    if held_contexts:
        units |= set(
            db.execute(
                sa.select(CatalogPosition.unit_id)
                .select_from(CatalogContext)
                .join(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
                .join(CatalogPosition, CatalogPosition.id == ContextBucket.catalog_position_id)
                .where(CatalogContext.id.in_(held_contexts))
                .distinct()
            )
            .scalars()
            .all()
        )
    return mass, units


def schema_ready_to_build(db: Session, family_id: int) -> bool:
    """Схему семьи можно строить, когда перезапрос её единицы окончен (спека
    §2.6): в единице нет заданий предложений в `pending`/`running` и нет
    удержанной пачки, касающейся её отпечатками предложений (`mass` касается
    всех единиц). Задержанные и ошибочные не считаются; пачка из одних значений
    состав имён не меняет."""
    unit = db.execute(
        sa.select(WorkFamily.unit_id).where(WorkFamily.id == family_id)
    ).one_or_none()
    if unit is None:
        return False
    mass, units = _open_suggestion_state(db)
    return not mass and unit.unit_id not in units


def families_awaiting_schema(
    db: Session, *, unit_ids: Collection[int | None] | None = None
) -> list[int]:
    """Активные семьи без текущей и без строящейся версии схемы (кандидаты на
    сверку схем); `unit_ids` ограничивает единицами (`None` в наборе — семьи без
    единицы), без него — все единицы. Готовность единицы здесь не проверяется:
    её решает сама сверка."""
    has_version = sa.exists().where(
        FamilyParameterSchema.family_id == WorkFamily.id,
        FamilyParameterSchema.status.in_(
            (SchemaStatus.frozen.value, SchemaStatus.building.value)
        ),
    )
    query = sa.select(WorkFamily.id).where(
        WorkFamily.status == FamilyStatus.active.value, ~has_version
    )
    if unit_ids is not None:
        non_null = [unit for unit in unit_ids if unit is not None]
        conditions = []
        if non_null:
            conditions.append(WorkFamily.unit_id.in_(non_null))
        if None in unit_ids:
            conditions.append(WorkFamily.unit_id.is_(None))
        if not conditions:
            return []
        query = query.where(sa.or_(*conditions))
    return list(db.execute(query.order_by(WorkFamily.id)).scalars().all())


def _discarded_fingerprints(db: Session, kind: SemanticJobKind) -> set[tuple]:
    """Отпечатки вида `kind` из отброшенных пачек: `(context_id, family_id,
    schema_id, request_hash)`."""
    rows = db.execute(
        sa.select(SemanticReconcileBatch.held_fingerprints).where(
            SemanticReconcileBatch.status == ReconcileBatchStatus.discarded.value
        )
    ).scalars()
    return {
        (
            element["context_id"], element["family_id"], element["schema_id"],
            element["request_hash"],
        )
        for held in rows
        for element in held
        if isinstance(element, dict) and element.get("kind") == kind.value
    }


def without_discarded_values(db: Session, context_ids: Collection[int]) -> list[int]:
    """Контексты, чей ТЕКУЩИЙ отпечаток значений (версия схемы и хэш запроса) не
    значится в отброшенной пачке: проход не возвращает отброшенное, пока вход не
    изменился. Контекст без материала значений остаётся — его вопрос решает
    сверка."""
    ids = list(context_ids)
    discarded = _discarded_fingerprints(db, SemanticJobKind.context_values)
    if not discarded or not ids:
        return ids
    material = load_values_material(db, ids)
    kept = []
    for context_id in ids:
        values = material.get(context_id)
        if values is not None:
            key = (
                context_id, None, values.schema_id,
                render_values_request(values, settings=settings).request_hash,
            )
            if key in discarded:
                continue
        kept.append(context_id)
    return kept


def without_discarded_schemas(db: Session, family_ids: Collection[int]) -> list[int]:
    """Семьи без версии схемы, чей ТЕКУЩИЙ вход (семья и хэш запроса) не значится
    в отброшенной пачке. Номер версии в сравнении не участвует: тело запроса схемы
    его не содержит, а отброшенная версия `building` отменена, и у семьи, чью версию
    отменило отбрасывание, в отпечатке остался прежний номер."""
    ids = list(family_ids)
    discarded = {
        (family_id, request_hash)
        for _context_id, family_id, _schema_id, request_hash in _discarded_fingerprints(
            db, SemanticJobKind.family_schema
        )
    }
    if not discarded or not ids:
        return ids
    return [
        family_id
        for family_id in ids
        if (
            family_id,
            render_schema_request(
                load_schema_material(db, family_id, _UNBUILT_SCHEMA_ID), settings=settings
            ).request_hash,
        )
        not in discarded
    ]


def _plan_family_schemas(
    db: Session,
    family_ids: Collection[int],
    plan: _Plan,
    *,
    planned_units: Collection[int | None] = (),
) -> None:
    """Ветвь `family_schema` — таблица исходов по предмету «семья + версия
    `building`». Активной семье без текущей и без `building` версии при
    готовности единицы создаётся версия и задание; `planned_units` — единицы,
    предложения которых эта же сверка ставит или удерживает (перезапрос единицы
    ещё не окончен)."""
    ids = sorted(set(family_ids))
    if not ids:
        return
    families = db.execute(
        sa.select(WorkFamily.id, WorkFamily.status, WorkFamily.unit_id).where(
            WorkFamily.id.in_(ids)
        )
    ).all()
    building_of: dict[int, int] = {}
    with_frozen: set[int] = set()
    for row in db.execute(
        sa.select(
            FamilyParameterSchema.id, FamilyParameterSchema.family_id, FamilyParameterSchema.status
        ).where(FamilyParameterSchema.family_id.in_(ids))
    ).all():
        if row.status == SchemaStatus.building.value:
            building_of[row.family_id] = row.id
        elif row.status == SchemaStatus.frozen.value:
            with_frozen.add(row.family_id)
    jobs_by_family = _group_by(
        db.execute(
            sa.select(SemanticJob).where(
                SemanticJob.kind == SemanticJobKind.family_schema.value,
                SemanticJob.family_id.in_(ids),
            )
        )
        .scalars()
        .all(),
        lambda job: job.family_id,
    )
    busy: tuple[bool, set[int | None]] | None = None
    for family in families:
        jobs = jobs_by_family.get(family.id, [])
        active = family.status == FamilyStatus.active.value
        building_id = building_of.get(family.id)
        if active and building_id is not None:
            _plan_inapplicable(plan, [job for job in jobs if job.schema_id != building_id])
            rendered = render_schema_request(
                load_schema_material(db, family.id, building_id), settings=settings
            )
            current = _plan_applicable(
                plan, [job for job in jobs if job.schema_id == building_id],
                (building_id, rendered.request_hash),
            )
            fingerprint = Fingerprint(
                SemanticJobKind.family_schema, None, family.id, building_id, rendered.request_hash
            )
            row = _job_row(
                fingerprint, rendered, unit_id=family.unit_id, prompt_version=SCHEMA_PROMPT_VERSION
            )
            _plan_postanovka(plan, current, _NewJob(fingerprint, rendered, row))
            continue
        _plan_inapplicable(plan, jobs)
        if not active or family.id in with_frozen:
            continue
        if family.unit_id in planned_units:
            continue
        if busy is None:
            busy = _open_suggestion_state(db)
        if busy[0] or family.unit_id in busy[1]:
            continue
        rendered = render_schema_request(
            load_schema_material(db, family.id, _UNBUILT_SCHEMA_ID), settings=settings
        )
        fingerprint = Fingerprint(
            SemanticJobKind.family_schema, None, family.id, None, rendered.request_hash
        )
        row = _job_row(
            fingerprint, rendered, unit_id=family.unit_id, prompt_version=SCHEMA_PROMPT_VERSION
        )
        plan.creates.append(_NewJob(fingerprint, rendered, row))


def _schema_scope(
    db: Session, material_by_context: dict[int, ContextRequestMaterial]
) -> set[int]:
    """Семьи, чьи схемы сверяет сверка контекстов: все активные семьи единиц
    контекстов (готовность схемы зависит от состояния всей единицы; семья
    контекста той же единицы входит сюда же)."""
    if not material_by_context:
        return set()
    units = {material.unit_id for material in material_by_context.values()}
    non_null_units = [unit for unit in units if unit is not None]
    conditions = []
    if non_null_units:
        conditions.append(WorkFamily.unit_id.in_(non_null_units))
    if None in units:
        conditions.append(WorkFamily.unit_id.is_(None))
    return set(
        db.execute(
            sa.select(WorkFamily.id).where(
                WorkFamily.status == FamilyStatus.active.value, sa.or_(*conditions)
            )
        )
        .scalars()
        .all()
    )


def _split_jobs(
    jobs: Collection[SemanticJob],
) -> tuple[list[SemanticJob], list[SemanticJob]]:
    suggestions = [job for job in jobs if job.kind == SemanticJobKind.family_suggestion.value]
    values = [job for job in jobs if job.kind == SemanticJobKind.context_values.value]
    return suggestions, values


def _plan_all(
    db: Session, context_ids: Collection[int], family_ids: Collection[int] = ()
) -> _FullPlan:
    """План сверки всех трёх видов по набору контекстов — без записей.
    `family_ids` — семьи, чьи схемы сверяются сверх семей единиц контекстов
    (отпечатки схем удержанной пачки)."""
    prepared = prepare_contexts(db, context_ids)
    material_by_context = prepared.material_by_context
    all_jobs = _load_jobs_for_contexts(db, list(material_by_context))
    suggestion_jobs, values_jobs = _split_jobs(all_jobs)
    full = _FullPlan(prepared=prepared)
    _plan_suggestions(prepared, suggestion_jobs, full)
    _plan_context_values(db, material_by_context, values_jobs, full.values)
    planned_units = {
        material_by_context[fingerprint.context_id].unit_id
        for fingerprint, _rendered in full.suggestions.entries()
    }
    _plan_family_schemas(
        db,
        _schema_scope(db, material_by_context) | set(family_ids),
        full.schemas,
        planned_units=planned_units,
    )
    return full


def held_fingerprints(db: Session, context_ids: Collection[int]) -> list[Fingerprint]:
    """Множество постановки `E` для набора контекстов, канонически
    отсортированное, БЕЗ каких-либо записей (preview потолка). Использует ту же
    функцию плана, что `reconcile_semantic_jobs`, — иначе оценка потолка и
    предъявление `held_fingerprints` могли бы разойтись."""
    return sorted(
        (fingerprint for fingerprint, _rendered in _plan_all(db, context_ids).entries()),
        key=_fingerprint_sort_key,
    )


# ---------------------------------------------------------------------------
#  get_or_create_held_batch — атомарная дедупликация (спека §2.11)
# ---------------------------------------------------------------------------

def get_or_create_held_batch(
    db: Session,
    *,
    fingerprints: Sequence[Fingerprint],
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

    ordered = sorted(fingerprints, key=_fingerprint_sort_key)
    payload = [fingerprint.as_dict() for fingerprint in ordered]
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
#  Резерв и ожидаемая цена по E — тарифы каждого задания по его виду
# ---------------------------------------------------------------------------

def _estimate_totals(
    db: Session,
    entries: Sequence[tuple],
) -> tuple[Decimal, Decimal]:
    """Резерв и ожидаемая цена при попадании в кэш по `E`: каждое задание
    считается тарифами СВОЕГО вида (`tariffs_from(settings, kind)`) и лимитом
    токенов своего тела — одна формула для потолка события и для preview
    `admin`. Токены известного префикса — один запрос на РАЗЛИЧНЫЙ
    `prefix_hash` набора, а не на задание; без наблюдения префикса — его
    байты. Элемент — `(вид, рендер)` или `(вид, рендер, без_модели)`: задание без
    вызова модели (схема без параметров) стоит 0."""
    known_by_prefix: dict[str, int | None] = {}
    tariffs_by_kind: dict[SemanticJobKind, Tariffs] = {}
    reserve_total = cached_total = Decimal("0")
    for entry in entries:
        kind, rendered = SemanticJobKind(entry[0]), entry[1]
        if len(entry) > 2 and entry[2]:
            continue
        if rendered.prefix_hash not in known_by_prefix:
            known_by_prefix[rendered.prefix_hash] = known_prefix_tokens(db, rendered.prefix_hash)
        if kind not in tariffs_by_kind:
            tariffs_by_kind[kind] = tariffs_from(settings, kind)
        known = known_by_prefix[rendered.prefix_hash]
        prefix_tokens = known if known is not None else rendered.prefix_bytes
        reserve_total += reserve_for_known_prefix(
            prefix_tokens, rendered, tariffs_by_kind[kind], rendered.body["max_tokens"]
        )
        cached_total += expected_cached_cost_known_prefix(
            prefix_tokens, rendered, tariffs_by_kind[kind]
        )
    return reserve_total, cached_total


def _estimate_entries(
    db: Session,
    entries: Sequence[tuple[Fingerprint, RenderedRequest]],
    free: Collection[Fingerprint] = (),
) -> tuple[Decimal, Decimal]:
    return _estimate_totals(
        db,
        [(fingerprint.kind, rendered, fingerprint in free) for fingerprint, rendered in entries],
    )


def estimate_enqueue(
    db: Session, context_ids: Collection[int], family_ids: Collection[int] = ()
) -> tuple[list[Fingerprint], Decimal, Decimal]:
    """Набор постановки `E` контекстов (те же отпечатки, что у
    `held_fingerprints`) и его оценка `(отпечатки, резерв, ожидаемая цена при
    попадании в кэш)` — те же суммы, что сверка кладёт в удержанную пачку.
    `family_ids` добавляют схемы семей (подтверждение пачки с отпечатками
    схем). Ничего не пишет."""
    full = _plan_all(db, context_ids, family_ids)
    entries = sorted(full.entries(), key=lambda entry: _fingerprint_sort_key(entry[0]))
    reserve_total, cached_total = _estimate_entries(db, entries, full.free_fingerprints())
    return [fingerprint for fingerprint, _rendered in entries], reserve_total, cached_total


# ---------------------------------------------------------------------------
#  Мутации — по одному UPDATE/INSERT на вид перехода, не по строке
# ---------------------------------------------------------------------------

def _lock_jobs(db: Session, job_ids: Collection[int]) -> None:
    """Замки на задания, которые сверка собирается менять, — все сразу и в
    порядке `id`. Две параллельные сверки пересекающихся наборов (общие строки
    каталога у разных импортов) иначе берут замки в порядке скана своих
    `UPDATE`, и пара сверок замыкается в цикл; общий порядок цикла не допускает."""
    if not job_ids:
        return
    db.execute(
        sa.select(SemanticJob.id)
        .where(SemanticJob.id.in_(sorted(set(job_ids))))
        .order_by(SemanticJob.id)
        .with_for_update()
    ).all()


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
    та же гонка с захватом, что у `_cancel_where_status`; `done` возвращается
    только у заданий значений, нужных по отпечатку, который уже выполнялся."""
    if not job_ids:
        return 0
    result = db.execute(
        sa.update(SemanticJob)
        .where(
            SemanticJob.id.in_(job_ids),
            SemanticJob.status.in_(
                (SemanticJobStatus.cancelled.value, SemanticJobStatus.done.value)
            ),
        )
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


def _job_conflict_arbiter() -> list:
    """Арбитр `ON CONFLICT` — ТЕ ЖЕ выражения, что уникальный индекс миграции
    0019 (`uq_semantic_jobs_subject_request_hash`): `literal_column`, а не
    обычный Python-литерал, иначе psycopg3 после `prepare_threshold=5`
    перестанет находить индекс у подготовленного плана
    (`docs/insights/batch-larger-than-five.md`)."""
    no_subject = sa.literal_column("-1")
    return [
        SemanticJob.kind,
        sa.func.coalesce(SemanticJob.context_id, no_subject),
        sa.func.coalesce(SemanticJob.family_id, no_subject),
        sa.func.coalesce(SemanticJob.schema_id, no_subject),
        SemanticJob.request_hash,
    ]


def _insert_job_rows(db: Session, rows: list[dict]) -> int:
    """Один `INSERT ... ON CONFLICT (kind, COALESCE(context_id, -1), ...) DO
    NOTHING` на все строки одного вида (спека §2.7)."""
    if not rows:
        return 0
    stmt = (
        pg_insert(SemanticJob)
        .on_conflict_do_nothing(index_elements=_job_conflict_arbiter())
        .returning(SemanticJob.id)
    )
    result = db.execute(stmt, rows)
    return len(result.fetchall())


def _insert_new_jobs(
    db: Session,
    create_context_ids: list[int],
    applicable_render: dict[int, RenderedRequest],
    material_by_context: dict[int, ContextRequestMaterial],
) -> int:
    """Задания `family_suggestion` по контекстам одним `INSERT`; строки идут по
    возрастанию `context_id` при любом порядке входа: две сверки общих
    контекстов ждут друг друга на уникальном индексе в одном и том же порядке."""
    rows = []
    for context_id in sorted(create_context_ids):
        rendered = applicable_render[context_id]
        fingerprint = Fingerprint(
            SemanticJobKind.family_suggestion, context_id, None, None, rendered.request_hash
        )
        rows.append(
            _job_row(
                fingerprint, rendered, unit_id=material_by_context[context_id].unit_id,
                prompt_version=PROMPT_VERSION,
            )
        )
    return _insert_job_rows(db, rows)


def _free_parents(db: Session, model, ids) -> set[int]:
    """Родительские строки внешних ключей вставок сверки, которые можно взять
    `FOR KEY SHARE` без ожидания (`SKIP LOCKED`), одним запросом по `id`.

    Вставка строки задания неявно берёт `FOR KEY SHARE` на родителей (семью,
    версию схемы, контекст). Родителя, которого не держит сверка, может держать
    `FOR UPDATE` другая операция, ждущая замок задания, уже взятый сверкой, —
    ожидание на вставке замкнуло бы цикл. Для занятого родителя вставку
    пропускаем; пропущенный ряд не гарантированно заведёт сверка держателя
    замка, его восстановит одна из следующих сверок единицы. Родитель, которого
    держит сама транзакция вызывающего, возвращается обычно."""
    wanted = sorted({parent_id for parent_id in ids if parent_id is not None})
    if not wanted:
        return set()
    return set(
        db.execute(
            sa.select(model.id)
            .where(model.id.in_(wanted))
            .order_by(model.id)
            .with_for_update(read=True, key_share=True, skip_locked=True)
        )
        .scalars()
        .all()
    )


def _ensure_building_version(db: Session, family_id: int) -> int | None:
    """Версия схемы `building` для семьи: `version = max + 1` (отменённые тоже
    считаются). Вставка `ON CONFLICT DO NOTHING` без цели — две параллельные
    сверки одной семьи не падают на уникальности ни по `(семья, версия)`, ни по
    единственной `building`; проигравшая перечитывает версию победившей.

    Семью вызывающий держит сам (пересборка, слияние) либо уже проверил как
    свободную (`_create_jobs` пропускает занятые семьи через `_free_parents`)."""
    next_version = db.execute(
        sa.select(sa.func.coalesce(sa.func.max(FamilyParameterSchema.version), 0) + 1).where(
            FamilyParameterSchema.family_id == family_id
        )
    ).scalar_one()
    created = db.execute(
        pg_insert(FamilyParameterSchema)
        .values(
            family_id=family_id,
            version=next_version,
            status=SchemaStatus.building.value,
            origin=SchemaOrigin.model.value,
        )
        .on_conflict_do_nothing()
        .returning(FamilyParameterSchema.id)
    ).first()
    if created is not None:
        return created.id
    return db.execute(
        sa.select(FamilyParameterSchema.id).where(
            FamilyParameterSchema.family_id == family_id,
            FamilyParameterSchema.status == SchemaStatus.building.value,
        )
    ).scalar_one_or_none()


def _create_jobs(db: Session, full: _FullPlan) -> int:
    # Родители внешних ключей берутся в порядке домена «семья -> версия схемы ->
    # контекст» и без ожидания: занятого другой операцией родителя пропускаем.
    schema_creates = sorted(
        full.schemas.creates, key=lambda n: _fingerprint_sort_key(n.fingerprint)
    )
    free_families = _free_parents(db, WorkFamily, [new.fingerprint.family_id for new in schema_creates])
    free_schemas = _free_parents(
        db, FamilyParameterSchema, [new.fingerprint.schema_id for new in full.values.creates]
    )
    free_contexts = _free_parents(
        db,
        CatalogContext,
        [new.fingerprint.context_id for new in full.suggestions.creates]
        + [new.fingerprint.context_id for new in full.values.creates],
    )
    created = 0
    suggestion_contexts = [
        new.fingerprint.context_id
        for new in full.suggestions.creates
        if new.fingerprint.context_id in free_contexts
    ]
    if suggestion_contexts:
        created += _insert_new_jobs(
            db,
            suggestion_contexts,
            full.prepared.applicable_render,
            full.prepared.material_by_context,
        )
    created += _insert_job_rows(
        db,
        [
            new.row
            for new in full.values.creates
            if new.fingerprint.context_id in free_contexts
            and new.fingerprint.schema_id in free_schemas
        ],
    )
    schema_rows: list[dict] = []
    for new in schema_creates:
        if new.fingerprint.family_id not in free_families:
            continue
        schema_id = new.fingerprint.schema_id
        if schema_id is None:
            schema_id = _ensure_building_version(db, new.fingerprint.family_id)
            if schema_id is None:
                continue
        schema_rows.append(dict(new.row, schema_id=schema_id))
    created += _insert_job_rows(db, schema_rows)
    return created


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
#  Исполнение плана
# ---------------------------------------------------------------------------

def _check_arguments(cap: EventCap | object, source: str) -> None:
    if source not in _VALID_SOURCES:
        raise ValueError(f"неизвестный источник пачки: {source!r}")
    if cap is not NO_CAP and not isinstance(cap, EventCap):
        raise TypeError("cap обязан быть EventCap или NO_CAP")


def _execute_plan(
    db: Session,
    full: _FullPlan,
    *,
    cap: EventCap | object,
    source: str,
    import_job_id: int | None,
) -> ReconcileReport:
    """Записи сверки: отмены, снятие и повтор публикации выполняются ПРИ ЛЮБОМ
    потолке — денег не стоят; множество постановки `E` всех видов вместе
    (потолок события, спека §2.7, §2.11) либо выполняется целиком, либо не
    выполняется вовсе и заменяется ОДНОЙ удержанной пачкой с его отпечатками."""
    # Что будет менять сверка, известно из плана целиком — до первой записи.
    # Замки на эти задания берутся разом и в порядке `id`.
    _lock_jobs(db, [job_id for plan in full.plans() for job_id in plan.locked_job_ids()])

    cancelled_count = 0
    for plan in full.plans():
        for job_ids, from_statuses, reason in plan.cancels:
            cancelled_count += _cancel_where_status(
                db, job_ids, from_statuses=from_statuses, cancel_reason=reason
            )
    unpublished_count = _unpublish_where_published(
        db,
        full.inapplicable_suggestion_contexts,
        reason=SuggestionUnpublishedReason.context_not_applicable.value,
    )

    # Повторная публикация `done` предложения текущего отпечатка.
    republished_count = 0
    if full.done_suggestion_ids:
        suggestion_rows = db.execute(
            sa.select(
                FamilySuggestion.id, FamilySuggestion.context_id, FamilySuggestion.decision,
                FamilySuggestion.is_published,
            ).where(FamilySuggestion.id.in_(full.done_suggestion_ids))
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

    held_batch_id: int | None = None
    created_count = 0
    revived_count = 0
    entries = full.entries()
    reserve_total = cached_total = Decimal("0")
    if cap is NO_CAP:
        proceed = True
    else:
        reserve_total, cached_total = _estimate_entries(db, entries, full.free_fingerprints())
        proceed = not exceeds_cap(len(entries), reserve_total, cap)

    if proceed:
        created_count = _create_jobs(db, full)
        revived_count = _revive_jobs(
            db, [revival.job_id for plan in full.plans() for revival in plan.revivals]
        )
    else:
        held_batch_id = get_or_create_held_batch(
            db,
            fingerprints=[fingerprint for fingerprint, _rendered in entries],
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
#  reconcile_semantic_jobs и ветви по видам
# ---------------------------------------------------------------------------

def reconcile_semantic_jobs(
    db: Session,
    context_ids: Collection[int],
    *,
    cap: EventCap | object,
    source: str,
    import_job_id: int | None = None,
) -> ReconcileReport:
    """Сверка очереди с текущим состоянием набора контекстов для трёх видов
    заданий (спека §2.7, инвариант). Одна транзакция ВЫЗЫВАЮЩЕГО — `commit` эта
    функция не делает.

    Порядок: (1) материал, применимость, рендер; (2) задания набора одним
    запросом; (3) план каждого вида — отмены неприменимых и старых отпечатков,
    множество постановки `E` по таблице исходов (предложения — по контексту,
    значения — по контексту и версии схемы с проверкой нужности, схемы — по
    семье и версии `building`); (4) отмены, снятие и повтор публикации
    выполняются при любом потолке; (5) `E` всех видов вместе — либо целиком,
    либо одной удержанной пачкой (потолок события, §2.11)."""
    _check_arguments(cap, source)
    full = _plan_all(db, context_ids)
    return _execute_plan(db, full, cap=cap, source=source, import_job_id=import_job_id)


def reconcile_context_values(
    db: Session, context_ids: Collection[int], *, cap: EventCap | object, source: str
) -> ReconcileReport:
    """Ветвь `context_values`: сверка заданий значений набора контекстов."""
    _check_arguments(cap, source)
    material = load_request_material(db, context_ids)
    _suggestion_jobs, values_jobs = _split_jobs(_load_jobs_for_contexts(db, list(material)))
    full = _FullPlan()
    _plan_context_values(db, material, values_jobs, full.values)
    return _execute_plan(db, full, cap=cap, source=source, import_job_id=None)


def reconcile_family_schemas(
    db: Session, family_ids: Collection[int], *, cap: EventCap | object, source: str
) -> ReconcileReport:
    """Ветвь `family_schema`: сверка схем набора семей."""
    _check_arguments(cap, source)
    full = _FullPlan()
    _plan_family_schemas(db, family_ids, full.schemas)
    return _execute_plan(db, full, cap=cap, source=source, import_job_id=None)


def schedule_extension_wave(db: Session, *, family_id: int, parameter_id: int) -> int:
    """Волна перезапроса после расширений списка (спека §2.8, §2.6): если у
    семьи нет заданий `context_values` в `pending`/`running`, ставит задания
    ТЕКУЩЕГО отпечатка (минуя нужность) всем применимым контекстам семьи, у
    которых значение этого параметра пусто (`source = 'none'`: расхождение путей
    `path_conflict` значением не пусто). Вызывается обработчиком результата
    после того, как собственное задание уже `done`. Потолок события — из настроек. Возвращает число
    поставленных или возвращённых в `pending` заданий."""
    open_jobs = (
        sa.select(SemanticJob.id)
        .join(FamilyParameterSchema, FamilyParameterSchema.id == SemanticJob.schema_id)
        .where(
            SemanticJob.kind == SemanticJobKind.context_values.value,
            SemanticJob.status.in_(
                (SemanticJobStatus.pending.value, SemanticJobStatus.running.value)
            ),
            FamilyParameterSchema.family_id == family_id,
        )
    )
    if db.execute(open_jobs.limit(1)).first() is not None:
        return 0
    context_ids = (
        db.execute(
            sa.select(ContextParameterValue.context_id)
            .join(CatalogContext, CatalogContext.id == ContextParameterValue.context_id)
            .where(
                ContextParameterValue.parameter_id == parameter_id,
                ContextParameterValue.source == ValueSource.none.value,
                sa.func.coalesce(CatalogContext.pending_family_id, CatalogContext.work_family_id)
                == family_id,
            )
        )
        .scalars()
        .all()
    )
    if not context_ids:
        return 0
    material = load_request_material(db, context_ids)
    _suggestion_jobs, values_jobs = _split_jobs(_load_jobs_for_contexts(db, list(material)))
    full = _FullPlan()
    _plan_context_values(db, material, values_jobs, full.values, force_needed=True)
    report = _execute_plan(
        db, full, cap=event_cap_from(settings), source="operation", import_job_id=None
    )
    return report.created + report.revived


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
