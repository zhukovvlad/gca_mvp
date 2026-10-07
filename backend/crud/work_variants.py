"""Чтение схемы семьи, вариантов и варианта контекста для экрана «Семьи и
контексты» (спека `2026-10-02-catalog-variants-design.md` §2.12).

Модуль ничего не пишет. Операции над схемой и вариантами —
`services/work_variants.py`, смена семьи — `services/family_change.py`.

Число запросов чтения не зависит от числа параметров, значений, вариантов и
контекстов: каждая форма читается постоянным числом запросов. Деньги —
строками (`AGENTS.md` §3), никаких `float`.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TypedDict

import sqlalchemy as sa
from sqlalchemy.orm import Session

from config import settings
from models import (
    CatalogContext,
    ContextParameterValue,
    FamilyParameter,
    FamilyParameterSchema,
    FamilyParameterValue,
    SchemaStatus,
    SemanticJob,
    SemanticJobKind,
    SemanticJobStatus,
    WorkFamily,
    WorkVariant,
    WorkVariantValue,
)
from services.semantic_cost import (
    RESERVE_FORMULA_VERSION,
    expected_cached_cost_known_prefix,
    known_prefix_tokens,
    reserve_for_known_prefix,
    tariffs_from,
)
from services.semantic_reconcile import schema_ready_to_build
from services.semantic_request import RenderedRequest
from services.variant_request import (
    load_schema_material,
    load_values_material,
    render_schema_request,
    render_values_request,
)

#: Версия схемы, которой у ещё не построенной схемы нет: тот же приём, что у
#: сверки (`services/semantic_reconcile.py`) — рендер запроса схемы от
#: `schema_id` не зависит.
_UNBUILT_SCHEMA_ID = 0


class ValueOut(TypedDict):
    id: int
    value: str
    origin: str
    merged_into_id: int | None


class ParameterOut(TypedDict):
    id: int
    ordinal: int
    name: str
    values: list[ValueOut]


class SchemaOut(TypedDict):
    """Текущая (замороженная) версия схемы семьи. `status`/`version` — у неё;
    `None` — текущей версии нет. `building` — идёт пересборка (версия
    `building`) и показанная версия остаётся прежней. `ready_to_build` — схему
    можно строить сейчас (перезапрос единицы окончен). `values_jobs_live` — число
    заданий значений в `pending`/`running` по текущей версии схемы семьи: пока оно
    не ноль, количества контекстов в вариантах ещё меняются."""

    family_id: int
    status: str | None
    version: int | None
    ready_to_build: bool
    building: bool
    values_jobs_live: int
    parameters: list[ParameterOut]


class VariantOut(TypedDict):
    id: int
    values: list[str | None]
    contexts: int
    status: str


class ContextValueOut(TypedDict):
    parameter_id: int
    ordinal: int
    name: str
    value_id: int | None
    value: str | None
    source: str


class PendingOut(TypedDict):
    family_id: int
    family_title: str
    source: str
    by: int | None
    at: dt.datetime
    threshold: str | None
    suggestion_id: int | None


class ContextVariantOut(TypedDict):
    variant_id: int | None
    values: list[ContextValueOut]
    split_hint: bool
    pending: PendingOut | None
    values_job_status: str | None


def family_exists(db: Session, family_id: int) -> bool:
    return (
        db.execute(sa.select(WorkFamily.id).where(WorkFamily.id == family_id)).first()
        is not None
    )


# ---------------------------------------------------------------------------
#  Схема семьи
# ---------------------------------------------------------------------------

def schema_out(db: Session, family_id: int) -> SchemaOut | None:
    """Схема семьи; `None` — семьи нет. Слитые значения включены с
    `merged_into_id` (экран показывает их синонимами цели)."""
    if not family_exists(db, family_id):
        return None
    versions = db.execute(
        sa.select(
            FamilyParameterSchema.id,
            FamilyParameterSchema.version,
            FamilyParameterSchema.status,
        ).where(
            FamilyParameterSchema.family_id == family_id,
            FamilyParameterSchema.status.in_(
                (SchemaStatus.frozen.value, SchemaStatus.building.value)
            ),
        )
    ).all()
    frozen = next((row for row in versions if row.status == SchemaStatus.frozen.value), None)
    building = any(row.status == SchemaStatus.building.value for row in versions)

    parameters: list[ParameterOut] = []
    if frozen is not None:
        parameters = _parameters_of(db, frozen.id)
    return SchemaOut(
        family_id=family_id,
        status=frozen.status if frozen is not None else None,
        version=frozen.version if frozen is not None else None,
        ready_to_build=schema_ready_to_build(db, family_id),
        building=building,
        values_jobs_live=_values_jobs_live(db, frozen.id) if frozen is not None else 0,
        parameters=parameters,
    )


def _values_jobs_live(db: Session, schema_id: int) -> int:
    return db.execute(
        sa.select(sa.func.count(SemanticJob.id)).where(
            SemanticJob.kind == SemanticJobKind.context_values.value,
            SemanticJob.schema_id == schema_id,
            SemanticJob.status.in_(
                (SemanticJobStatus.pending.value, SemanticJobStatus.running.value)
            ),
        )
    ).scalar_one()


def _parameters_of(db: Session, schema_id: int) -> list[ParameterOut]:
    rows = db.execute(
        sa.select(
            FamilyParameter.id.label("parameter_id"),
            FamilyParameter.ordinal,
            FamilyParameter.name,
            FamilyParameterValue.id.label("value_id"),
            FamilyParameterValue.value,
            FamilyParameterValue.origin,
            FamilyParameterValue.merged_into_id,
        )
        .select_from(FamilyParameter)
        .outerjoin(FamilyParameterValue, FamilyParameterValue.parameter_id == FamilyParameter.id)
        .where(FamilyParameter.schema_id == schema_id)
        .order_by(FamilyParameter.ordinal, FamilyParameterValue.id)
    ).all()
    parameters: dict[int, ParameterOut] = {}
    for row in rows:
        parameter = parameters.setdefault(
            row.parameter_id,
            ParameterOut(id=row.parameter_id, ordinal=row.ordinal, name=row.name, values=[]),
        )
        if row.value_id is not None:
            parameter["values"].append(
                ValueOut(
                    id=row.value_id,
                    value=row.value,
                    origin=row.origin,
                    merged_into_id=row.merged_into_id,
                )
            )
    return list(parameters.values())


def parameter_family_id(db: Session, parameter_id: int) -> int | None:
    """Семья, которой принадлежит параметр; `None` — параметра нет."""
    return db.execute(
        sa.select(FamilyParameterSchema.family_id)
        .join(FamilyParameter, FamilyParameter.schema_id == FamilyParameterSchema.id)
        .where(FamilyParameter.id == parameter_id)
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
#  Варианты семьи
# ---------------------------------------------------------------------------

def variants_out(db: Session, family_id: int) -> list[VariantOut] | None:
    """Варианты семьи, активные и архивные, по `id`; `None` — семьи нет.
    `values` — тексты значений по порядку параметров версии варианта, `None` —
    «не уточнено»; `contexts` — живые (не архивные) контексты, стоящие на варианте сейчас."""
    if not family_exists(db, family_id):
        return None
    variants = db.execute(
        sa.select(WorkVariant.id, WorkVariant.status)
        .where(WorkVariant.family_id == family_id)
        .order_by(WorkVariant.id)
    ).all()
    if not variants:
        return []
    variant_ids = [row.id for row in variants]

    values_of: dict[int, list[str | None]] = {variant_id: [] for variant_id in variant_ids}
    for row in db.execute(
        sa.select(WorkVariantValue.variant_id, FamilyParameterValue.value)
        .select_from(WorkVariantValue)
        .join(FamilyParameter, FamilyParameter.id == WorkVariantValue.parameter_id)
        .outerjoin(FamilyParameterValue, FamilyParameterValue.id == WorkVariantValue.value_id)
        .where(WorkVariantValue.variant_id.in_(variant_ids))
        .order_by(WorkVariantValue.variant_id, FamilyParameter.ordinal)
    ).all():
        values_of[row.variant_id].append(row.value)

    contexts_of = dict(
        db.execute(
            sa.select(CatalogContext.work_variant_id, sa.func.count())
            .where(
                CatalogContext.work_variant_id.in_(variant_ids),
                CatalogContext.archived_at.is_(None),
            )
            .group_by(CatalogContext.work_variant_id)
        ).all()
    )
    return [
        VariantOut(
            id=row.id,
            values=values_of[row.id],
            contexts=contexts_of.get(row.id, 0),
            status=row.status,
        )
        for row in variants
    ]


# ---------------------------------------------------------------------------
#  Вариант контекста — для карточки
# ---------------------------------------------------------------------------

def context_variant_out(db: Session, context: CatalogContext) -> ContextVariantOut:
    """Вариант контекста для карточки: значения с источником каждого, пометка
    «к делению», ожидание, состояние задания значений. Задание значений читается
    всегда — один запрос на любую карточку; сверх него с вариантом добавляется один
    запрос значений, с ожиданием — ещё один (имя ожидаемой семьи)."""
    values: list[ContextValueOut] = []
    if context.work_variant_id is not None:
        values = [
            ContextValueOut(
                parameter_id=row.parameter_id,
                ordinal=row.ordinal,
                name=row.name,
                value_id=row.value_id,
                value=row.value,
                source=row.source,
            )
            for row in db.execute(
                sa.select(
                    ContextParameterValue.parameter_id,
                    FamilyParameter.ordinal,
                    FamilyParameter.name,
                    ContextParameterValue.value_id,
                    FamilyParameterValue.value,
                    ContextParameterValue.source,
                )
                .select_from(ContextParameterValue)
                .join(FamilyParameter, FamilyParameter.id == ContextParameterValue.parameter_id)
                .outerjoin(
                    FamilyParameterValue,
                    FamilyParameterValue.id == ContextParameterValue.value_id,
                )
                .where(ContextParameterValue.context_id == context.id)
                .order_by(FamilyParameter.ordinal)
            ).all()
        ]
    pending: PendingOut | None = None
    if context.pending_family_id is not None:
        title = db.execute(
            sa.select(WorkFamily.title).where(WorkFamily.id == context.pending_family_id)
        ).scalar_one()
        pending = PendingOut(
            family_id=context.pending_family_id,
            family_title=title,
            source=context.pending_family_source,
            by=context.pending_by,
            at=context.pending_at,
            threshold=(
                format(context.pending_threshold, "f")
                if context.pending_threshold is not None
                else None
            ),
            suggestion_id=context.pending_suggestion_id,
        )
    return ContextVariantOut(
        variant_id=context.work_variant_id,
        values=values,
        split_hint=context.variant_split_hint is not None,
        pending=pending,
        values_job_status=_values_job_status(db, context.id),
    )


#: Статусы задания значений, которые экран считает «живыми»: у закрытых (`done`,
#: `cancelled`) значений ждать нечего.
_LIVE_VALUES_JOB_STATUSES = (
    SemanticJobStatus.pending.value,
    SemanticJobStatus.running.value,
    SemanticJobStatus.privacy_hold.value,
    SemanticJobStatus.error.value,
)


def _values_job_status(db: Session, context_id: int) -> str | None:
    """Статус живого задания значений контекста (самого нового по `id`); `None`, если его нет."""
    return db.execute(
        sa.select(SemanticJob.status)
        .where(
            SemanticJob.context_id == context_id,
            SemanticJob.kind == SemanticJobKind.context_values.value,
            SemanticJob.status.in_(_LIVE_VALUES_JOB_STATUSES),
        )
        .order_by(SemanticJob.id.desc())
        .limit(1)
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
#  Оценка пересборки схемы
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RebuildPreview:
    family_id: int
    context_count: int
    reserve_usd: Decimal
    expected_cached_usd: Decimal
    preview_hash: str
    #: Задания значений вошли в оценку; `False` — у семьи нет текущей схемы,
    #: оценка неполна (только задание схемы). Определяется текущей версией,
    #: которая уже в хэше, поэтому в хэш не входит.
    values_included: bool


def _family_context_ids(db: Session, family_id: int) -> list[int]:
    """Живые контексты, чьи значения ставит схема семьи: ожидаемая семья
    контекста, а без ожидания — текущая (тот же выбор, что у материала
    заданий значений)."""
    return list(
        db.execute(
            sa.select(CatalogContext.id)
            .where(
                CatalogContext.archived_at.is_(None),
                sa.func.coalesce(
                    CatalogContext.pending_family_id, CatalogContext.work_family_id
                )
                == family_id,
            )
            .order_by(CatalogContext.id)
        ).scalars()
    )


def _tariff_snapshot() -> dict[str, dict[str, str]]:
    snapshot: dict[str, dict[str, str]] = {}
    for kind in (SemanticJobKind.family_schema, SemanticJobKind.context_values):
        tariffs = tariffs_from(settings, kind)
        snapshot[kind.value] = {
            "input_per_m": str(tariffs.input_per_m),
            "cache_write_per_m": str(tariffs.cache_write_per_m),
            "cache_read_per_m": str(tariffs.cache_read_per_m),
            "output_per_m": str(tariffs.output_per_m),
        }
    return snapshot


def _estimate(
    db: Session, entries: Sequence[tuple[SemanticJobKind, RenderedRequest]]
) -> tuple[Decimal, Decimal]:
    """Резерв и ожидаемая цена при попадании в кэш: каждое задание считается
    тарифами своего вида, токены известного префикса читаются один раз на
    различный `prefix_hash` (формулы — `services/semantic_cost.py`)."""
    known_by_prefix: dict[str, int | None] = {}
    reserve_total = cached_total = Decimal("0")
    for kind, rendered in entries:
        if rendered.prefix_hash not in known_by_prefix:
            known_by_prefix[rendered.prefix_hash] = known_prefix_tokens(db, rendered.prefix_hash)
        known = known_by_prefix[rendered.prefix_hash]
        prefix_tokens = known if known is not None else rendered.prefix_bytes
        tariffs = tariffs_from(settings, kind)
        reserve_total += reserve_for_known_prefix(
            prefix_tokens, rendered, tariffs, rendered.body["max_tokens"]
        )
        cached_total += expected_cached_cost_known_prefix(prefix_tokens, rendered, tariffs)
    return reserve_total, cached_total


def rebuild_preview(db: Session, family_id: int) -> RebuildPreview | None:
    """Оценка пересборки схемы семьи: задание схемы плюс задания значений
    контекстов семьи, тарифы каждого вида. Задания значений оцениваются по
    текущей версии схемы (новой ещё нет); у семьи без текущей версии они в
    оценку не входят. `None` — семьи нет. `preview_hash` — по входам оценки:
    семья, текущая версия, наличие `building`, число контекстов, суммы и
    тарифы обоих видов."""
    if not family_exists(db, family_id):
        return None
    versions = db.execute(
        sa.select(FamilyParameterSchema.version, FamilyParameterSchema.status).where(
            FamilyParameterSchema.family_id == family_id,
            FamilyParameterSchema.status.in_(
                (SchemaStatus.frozen.value, SchemaStatus.building.value)
            ),
        )
    ).all()
    current_version = next(
        (row.version for row in versions if row.status == SchemaStatus.frozen.value), None
    )
    has_building = any(row.status == SchemaStatus.building.value for row in versions)

    context_ids = _family_context_ids(db, family_id)
    entries: list[tuple[SemanticJobKind, RenderedRequest]] = [
        (
            SemanticJobKind.family_schema,
            render_schema_request(
                load_schema_material(db, family_id, _UNBUILT_SCHEMA_ID), settings=settings
            ),
        )
    ]
    values_material = load_values_material(db, context_ids)
    for context_id in sorted(values_material):
        entries.append(
            (
                SemanticJobKind.context_values,
                render_values_request(values_material[context_id], settings=settings),
            )
        )
    reserve, cached = _estimate(db, entries)

    canonical = json.dumps(
        {
            "family_id": family_id,
            "current_version": current_version,
            "has_building": has_building,
            "contexts": len(context_ids),
            "reserve_usd": str(reserve),
            "cached_usd": str(cached),
            "tariffs": _tariff_snapshot(),
            "reserve_formula_version": RESERVE_FORMULA_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return RebuildPreview(
        family_id=family_id,
        context_count=len(context_ids),
        reserve_usd=reserve,
        expected_cached_usd=cached,
        preview_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        values_included=current_version is not None,
    )
