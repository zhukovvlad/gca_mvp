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
from sqlalchemy.orm import Session, aliased

from config import settings
from models import (
    CatalogContext,
    ContextMember,
    ContextParameterValue,
    FamilyParameter,
    FamilyParameterSchema,
    FamilyParameterValue,
    ParameterOrigin,
    ParameterRole,
    SchemaStatus,
    SemanticJob,
    SemanticJobKind,
    SemanticJobStatus,
    WorkFamily,
    WorkVariant,
    WorkVariantValue,
)
from services.schema_edit import EditSummary
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
from services.work_variants import normalize_value

#: Версия схемы, которой у ещё не построенной схемы нет: тот же приём, что у
#: сверки (`services/semantic_reconcile.py`) — рендер запроса схемы от
#: `schema_id` не зависит.
_UNBUILT_SCHEMA_ID = 0


class ValueOut(TypedDict):
    """Значение списка параметра. `contexts` и `rows` — живые (не архивные)
    контексты со значением и их строки каталога; границы диапазона — строками
    (`None` — «до …» и «… и более»). Выведенные из списка значения не отдаются,
    поэтому `retired` у отданных всегда `False`."""

    id: int
    value: str
    origin: str
    merged_into_id: int | None
    range_lo: str | None
    range_hi: str | None
    retired: bool
    contexts: int
    rows: int


class RangeOut(TypedDict):
    start: str
    stop: str
    step: str


class OffListOut(TypedDict):
    """Значения не из списка по норме текста: `text` — написание у большего числа
    контекстов (при равенстве — первое по алфавиту), числа — по всем написаниям."""

    text_norm: str
    text: str
    contexts: int
    rows: int


class ParameterOut(TypedDict):
    """Активный параметр версии. `refreshing` — число живых контекстов, у которых
    строка параметра помечена «уточняется»."""

    id: int
    ordinal: int
    name: str
    values: list[ValueOut]
    role: str
    value_kind: str
    closed: bool
    range: RangeOut | None
    origin: str
    off_list: list[OffListOut]
    refreshing: int


class SchemaOut(TypedDict):
    """Текущая (замороженная) версия схемы семьи. `status`/`version` — у неё;
    `None` — текущей версии нет. `building` — идёт пересборка (версия
    `building`) и показанная версия остаётся прежней. `ready_to_build` — схему
    можно строить сейчас (перезапрос единицы окончен). `values_jobs_live` — число
    заданий значений в `pending`/`running` по текущей версии схемы семьи: пока оно
    не ноль, количества контекстов в вариантах ещё меняются. `revision` — счётчик
    правок версии на месте (у семьи без версии — `0`)."""

    family_id: int
    status: str | None
    version: int | None
    ready_to_build: bool
    building: bool
    values_jobs_live: int
    parameters: list[ParameterOut]
    revision: int


class AskedOut(TypedDict):
    ordinal: int
    name: str
    contexts: int


class ValueMoveOut(TypedDict):
    parameter: str
    from_value: str
    to_value: str
    contexts: int


class RetiredOut(TypedDict):
    parameter: str
    value: str
    contexts: int


class EditSummaryOut(TypedDict):
    """Следствия правки схемы на месте (спека 3в §2.5, §2.10): деньги строками,
    `preview_hash` — для подтверждения сохранения."""

    variants_before: int
    variants_after_now: int
    variants_pending_answers: bool
    asked_by_parameter: list[AskedOut]
    reserve_usd: str
    expected_cached_usd: str
    value_moves: list[ValueMoveOut]
    retired: list[RetiredOut]
    refreshing_contexts: int
    repriced_pending_jobs: int
    preview_hash: str


def edit_summary_out(summary: EditSummary) -> EditSummaryOut:
    return EditSummaryOut(
        variants_before=summary.variants_before,
        variants_after_now=summary.variants_after_now,
        variants_pending_answers=summary.variants_pending_answers,
        asked_by_parameter=[
            AskedOut(ordinal=ordinal, name=name, contexts=contexts)
            for ordinal, name, contexts in summary.asked_by_parameter
        ],
        reserve_usd=format(summary.reserve_usd, "f"),
        expected_cached_usd=format(summary.expected_cached_usd, "f"),
        value_moves=[
            ValueMoveOut(parameter=parameter, from_value=source, to_value=target, contexts=contexts)
            for parameter, source, target, contexts in summary.value_moves
        ],
        retired=[
            RetiredOut(parameter=parameter, value=value, contexts=contexts)
            for parameter, value, contexts in summary.retired
        ],
        refreshing_contexts=summary.refreshing_contexts,
        repriced_pending_jobs=summary.repriced_pending_jobs,
        preview_hash=summary.preview_hash,
    )


class VariantOut(TypedDict):
    id: int
    values: list[str | None]
    contexts: int
    status: str


class ContextValueOut(TypedDict):
    """Строка значения карточки: главные параметры идут раньше второстепенных,
    внутри роли — по номеру. `refreshing` — значение ждёт перезапроса,
    `off_list_text` — текст не из списка (у строки `off_list`)."""

    parameter_id: int
    ordinal: int
    name: str
    value_id: int | None
    value: str | None
    source: str
    role: str
    refreshing: bool
    off_list_text: str | None


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
    `merged_into_id` (экран показывает их синонимами цели); выведенные значения
    и архивные параметры не отдаются (спека 3в §2.10)."""
    if not family_exists(db, family_id):
        return None
    versions = db.execute(
        sa.select(
            FamilyParameterSchema.id,
            FamilyParameterSchema.version,
            FamilyParameterSchema.status,
            FamilyParameterSchema.revision,
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
        revision=frozen.revision if frozen is not None else 0,
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


def _decimal_text(number: Decimal | None) -> str | None:
    """Число границы строкой в канонической записи правки схемы (`50`, не `50.000`)."""
    return None if number is None else format(number.normalize(), "f")


def _parameters_of(db: Session, schema_id: int) -> list[ParameterOut]:
    """Активные параметры версии со значениями, числами по значению и агрегатом
    «не из списка». Постоянное число запросов: параметры со значениями — один,
    числа контекстов и строк — один групповой запрос по строкам значений версии."""
    rows = db.execute(
        sa.select(
            FamilyParameter.id.label("parameter_id"),
            FamilyParameter.ordinal,
            FamilyParameter.name,
            FamilyParameter.role,
            FamilyParameter.value_kind,
            FamilyParameter.closed,
            FamilyParameter.range_from,
            FamilyParameter.range_to,
            FamilyParameter.range_step,
            FamilyParameter.origin.label("parameter_origin"),
            FamilyParameterValue.id.label("value_id"),
            FamilyParameterValue.value,
            FamilyParameterValue.origin,
            FamilyParameterValue.merged_into_id,
            FamilyParameterValue.range_lo,
            FamilyParameterValue.range_hi,
        )
        .select_from(FamilyParameter)
        .outerjoin(
            FamilyParameterValue,
            sa.and_(
                FamilyParameterValue.parameter_id == FamilyParameter.id,
                FamilyParameterValue.retired_at.is_(None),
            ),
        )
        .where(FamilyParameter.schema_id == schema_id, FamilyParameter.archived_at.is_(None))
        .order_by(FamilyParameter.ordinal, FamilyParameterValue.id)
    ).all()
    by_value, off_list, refreshing = _row_statistics(db, schema_id)
    parameters: dict[int, ParameterOut] = {}
    for row in rows:
        parameter = parameters.setdefault(
            row.parameter_id,
            ParameterOut(
                id=row.parameter_id,
                ordinal=row.ordinal,
                name=row.name,
                values=[],
                role=row.role,
                value_kind=row.value_kind,
                closed=row.closed,
                range=None
                if row.range_from is None
                else RangeOut(
                    start=_decimal_text(row.range_from),
                    stop=_decimal_text(row.range_to),
                    step=_decimal_text(row.range_step),
                ),
                origin=row.parameter_origin,
                off_list=off_list.get(row.parameter_id, []),
                refreshing=refreshing.get(row.parameter_id, 0),
            ),
        )
        if row.value_id is not None:
            contexts, member_rows = by_value.get(row.value_id, (0, 0))
            parameter["values"].append(
                ValueOut(
                    id=row.value_id,
                    value=row.value,
                    origin=row.origin,
                    merged_into_id=row.merged_into_id,
                    range_lo=_decimal_text(row.range_lo),
                    range_hi=_decimal_text(row.range_hi),
                    retired=False,
                    contexts=contexts,
                    rows=member_rows,
                )
            )
    return list(parameters.values())


def _row_statistics(
    db: Session, schema_id: int
) -> tuple[dict[int, tuple[int, int]], dict[int, list[OffListOut]], dict[int, int]]:
    """Числа по строкам значений версии одним групповым запросом: `{value_id:
    (контексты, строки каталога)}`, агрегат «не из списка» по параметрам и
    `{parameter_id: число контекстов «уточняется»}`. Считаются живые (не
    архивные) контексты; строки каталога — членства контекста."""
    members = (
        sa.select(ContextMember.context_id, sa.func.count().label("n"))
        .where(
            ContextMember.context_id.in_(
                sa.select(ContextParameterValue.context_id).where(
                    ContextParameterValue.schema_id == schema_id
                )
            )
        )
        .group_by(ContextMember.context_id)
        .subquery()
    )
    marked = ContextParameterValue.refresh_requested_at.is_not(None).label("marked")
    groups = db.execute(
        sa.select(
            ContextParameterValue.parameter_id,
            ContextParameterValue.value_id,
            ContextParameterValue.off_list_text,
            marked,
            sa.func.count().label("contexts"),
            sa.func.coalesce(sa.func.sum(members.c.n), 0).label("member_rows"),
        )
        .select_from(ContextParameterValue)
        .join(CatalogContext, CatalogContext.id == ContextParameterValue.context_id)
        .outerjoin(members, members.c.context_id == ContextParameterValue.context_id)
        .where(ContextParameterValue.schema_id == schema_id, CatalogContext.archived_at.is_(None))
        .group_by(
            ContextParameterValue.parameter_id,
            ContextParameterValue.value_id,
            ContextParameterValue.off_list_text,
            marked,
        )
    ).all()

    by_value: dict[int, tuple[int, int]] = {}
    refreshing: dict[int, int] = {}
    spellings: dict[int, dict[str, dict[str, list[int]]]] = {}
    for group in groups:
        contexts, member_rows = int(group.contexts), int(group.member_rows)
        if group.value_id is not None:
            have = by_value.get(group.value_id, (0, 0))
            by_value[group.value_id] = (have[0] + contexts, have[1] + member_rows)
        if group.marked:
            refreshing[group.parameter_id] = refreshing.get(group.parameter_id, 0) + contexts
        if group.off_list_text is not None:
            norm = normalize_value(group.off_list_text)
            counts = spellings.setdefault(group.parameter_id, {}).setdefault(norm, {})
            have_text = counts.setdefault(group.off_list_text, [0, 0])
            have_text[0] += contexts
            have_text[1] += member_rows

    off_list: dict[int, list[OffListOut]] = {}
    for parameter_id, by_norm in spellings.items():
        entries = [
            OffListOut(
                text_norm=norm,
                text=min(texts, key=lambda text: (-texts[text][0], text)),
                contexts=sum(count[0] for count in texts.values()),
                rows=sum(count[1] for count in texts.values()),
            )
            for norm, texts in by_norm.items()
        ]
        entries.sort(key=lambda entry: (-entry["contexts"], entry["text_norm"]))
        off_list[parameter_id] = entries
    return by_value, off_list, refreshing


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
    `values` — тексты значений главных параметров по порядку (второстепенные
    вариант не делят), `None` — «не уточнено»; `contexts` — живые (не архивные) контексты, стоящие на варианте сейчас."""
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
        .where(
            WorkVariantValue.variant_id.in_(variant_ids),
            FamilyParameter.role == ParameterRole.main.value,
        )
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
    всегда — один запрос на любую карточку; сверх него с вариантом или семьёй
    добавляется один запрос значений, с ожиданием — ещё один (имя ожидаемой семьи).

    Значения берутся ровно одной версии схемы (спека 3в §2.10): версии нынешнего
    варианта контекста, если у контекста есть строки этой версии, иначе действующей
    версии семьи контекста (так же у контекста без варианта). Между заморозкой
    новой версии и первым ответом по ней вариант остаётся прежним, а копии строк
    человека уже лежат в новой версии — в карточку они не попадают; когда строки
    версии варианта уже заменены ответом, а вариант ещё прежний, показывается
    действующая версия."""
    values: list[ContextValueOut] = []
    if context.work_variant_id is not None or context.work_family_id is not None:
        variant_schema_id = (
            sa.select(WorkVariant.schema_id)
            .where(WorkVariant.id == context.work_variant_id)
            .scalar_subquery()
        )
        current_schema_id = (
            sa.select(FamilyParameterSchema.id)
            .where(
                FamilyParameterSchema.family_id == context.work_family_id,
                FamilyParameterSchema.status == SchemaStatus.frozen.value,
            )
            .scalar_subquery()
        )
        in_variant_schema = aliased(ContextParameterValue)
        shown_schema_id = sa.case(
            (
                sa.exists().where(
                    in_variant_schema.context_id == context.id,
                    in_variant_schema.schema_id == variant_schema_id,
                ),
                variant_schema_id,
            ),
            else_=current_schema_id,
        )
        values = [
            ContextValueOut(
                parameter_id=row.parameter_id,
                ordinal=row.ordinal,
                name=row.name,
                value_id=row.value_id,
                value=row.value,
                source=row.source,
                role=row.role,
                refreshing=row.refreshing,
                off_list_text=row.off_list_text,
            )
            for row in db.execute(
                sa.select(
                    ContextParameterValue.parameter_id,
                    FamilyParameter.ordinal,
                    FamilyParameter.name,
                    ContextParameterValue.value_id,
                    FamilyParameterValue.value,
                    ContextParameterValue.source,
                    FamilyParameter.role,
                    ContextParameterValue.refresh_requested_at.is_not(None).label("refreshing"),
                    ContextParameterValue.off_list_text,
                )
                .select_from(ContextParameterValue)
                .join(FamilyParameter, FamilyParameter.id == ContextParameterValue.parameter_id)
                .outerjoin(
                    FamilyParameterValue,
                    FamilyParameterValue.id == ContextParameterValue.value_id,
                )
                .where(
                    ContextParameterValue.context_id == context.id,
                    ContextParameterValue.schema_id == shown_schema_id,
                    FamilyParameter.archived_at.is_(None),
                )
                .order_by(
                    (FamilyParameter.role != ParameterRole.main.value), FamilyParameter.ordinal
                )
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
    #: Оценка значений приблизительна: у новой схемы до ответа модели параметров
    #: нет, и считаются нынешние параметры МОДЕЛИ действующей версии (спека 3в
    #: §2.8). Истинно ровно тогда, когда значения вошли в оценку.
    values_approximate: bool


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
    контекстов семьи, тарифы каждого вида. Задание схемы при десяти параметрах
    человека модели не уходит и стоит 0. Задания значений оцениваются по
    нынешним параметрам МОДЕЛИ действующей версии как приближению (спека 3в
    §2.8: у новой схемы до ответа параметров нет); у семьи без текущей версии
    они в оценку не входят. `None` — семьи нет. `preview_hash` — по входам
    оценки: семья, текущая версия и её `revision` (правка на месте меняет
    оценку, не меняя номер версии), наличие `building`, число контекстов,
    суммы и тарифы обоих видов."""
    if not family_exists(db, family_id):
        return None
    versions = db.execute(
        sa.select(
            FamilyParameterSchema.id, FamilyParameterSchema.version,
            FamilyParameterSchema.status, FamilyParameterSchema.revision,
        ).where(
            FamilyParameterSchema.family_id == family_id,
            FamilyParameterSchema.status.in_(
                (SchemaStatus.frozen.value, SchemaStatus.building.value)
            ),
        )
    ).all()
    current = next((row for row in versions if row.status == SchemaStatus.frozen.value), None)
    current_version = current.version if current is not None else None
    has_building = any(row.status == SchemaStatus.building.value for row in versions)

    context_ids = _family_context_ids(db, family_id)
    entries: list[tuple[SemanticJobKind, RenderedRequest]] = []
    schema_material = load_schema_material(db, family_id, _UNBUILT_SCHEMA_ID)
    if schema_material.model_limit > 0:
        entries.append(
            (
                SemanticJobKind.family_schema,
                render_schema_request(schema_material, settings=settings),
            )
        )
    model_ordinals: tuple[int, ...] = ()
    if current is not None:
        model_ordinals = tuple(
            db.execute(
                sa.select(FamilyParameter.ordinal)
                .where(
                    FamilyParameter.schema_id == current.id,
                    FamilyParameter.origin == ParameterOrigin.model.value,
                    FamilyParameter.archived_at.is_(None),
                )
                .order_by(FamilyParameter.ordinal)
            ).scalars()
        )
    # Параметров модели в действующей версии нет — спрашивать о значениях нечего.
    values_material = (
        load_values_material(db, context_ids, requested_ordinals=model_ordinals)
        if model_ordinals
        else {}
    )
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
            "current_revision": current.revision if current is not None else None,
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
        values_approximate=current_version is not None,
    )
