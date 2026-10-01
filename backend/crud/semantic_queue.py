"""Чтение для экрана «Предложения» (спека `2026-09-28-semantic-suggestions-design.md`
§2.12, §2.13; план, задача 13): очереди предложений, задания в ошибке и
задержанные проверкой, сводка шапки.

Модуль ничего не пишет. Решения `admin` — `services/semantic_decisions.py`.

Число запросов очереди `list`/`new` не зависит от числа строк: строки выбираются
одним запросом, материал строк (название, единица, статья, путь) —
`load_request_material` (постоянное число запросов на весь набор), признаки
«многовладельческий» и «ранее отклонено» — один коррелированный подзапрос и один
запрос на весь набор.

«СИСТЕМА» определяется по сохранённому имени (`is_system_name`): колонки-признака
у предложения нет.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Literal, TypedDict

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from config import settings
from models import (
    CatalogContext,
    CatalogPosition,
    ContextBucket,
    ContextMember,
    Estimate,
    FamilyStatus,
    FamilySuggestion,
    Lot,
    Offer,
    PositionItem,
    Proposal,
    ReconcileBatchStatus,
    SemanticCancelReason,
    SemanticJob,
    SemanticJobAttempt,
    SemanticJobStatus,
    SemanticKind,
    SemanticReconcileBatch,
    SemanticState,
    SemanticWorkerState,
    TenderRound,
    UnitOfMeasure,
    WorkFamily,
)
from services.semantic_answer import is_system_name
from services.semantic_cost import spent_last_24h
from services.semantic_request import (
    PROMPT_VERSION,
    ContextRequestMaterial,
    RequestHasher,
    is_applicable,
    load_request_material,
    render_context_request,
    top_path,
)

Band = Literal["high", "mid", "low"]

#: Фильтр единицы очереди: id единицы, `"none"` — контексты без единицы, `None` — все.
UnitFilter = int | Literal["none"] | None

UNIT_NONE = "none"

#: Границы полос уверенности (спека §2.12): `high` — не ниже 0,9, `mid` — от 0,7
#: до 0,9 (0,9 не входит), `low` — ниже 0,7.
BAND_HIGH_MIN = Decimal("0.9")
BAND_MID_MIN = Decimal("0.7")

#: Разделитель звеньев пути в `ContextRequestMaterial.path_counts`.
_PATH_SEPARATOR = " / "


class RejectedMark(TypedDict):
    family_id: int
    family_title: str
    decided_at: str


class SuggestionRow(TypedDict):
    suggestion_id: int
    context_id: int
    title: str
    unit_code: str | None
    article: str | None
    path: list[str]
    confidence: str
    reason: str
    multi_owner: bool
    previously_rejected: RejectedMark | None


class SuggestionGroup(TypedDict):
    family_id: int
    family_title: str
    unit_code: str | None
    band: Band
    rows: list[SuggestionRow]
    total: int


class NewRow(TypedDict):
    """Строка очереди «Новая»: `suggestion_id is None` — контекст единицы без
    активных семей (модель не спрашивали)."""

    suggestion_id: int | None
    context_id: int
    title: str
    unit_code: str | None
    article: str | None
    path: list[str]
    new_family_name: str | None
    is_system: bool
    confidence: str | None
    reason: str | None
    multi_owner: bool


class SuggestionQueue(TypedDict):
    queue: Literal["list", "new"]
    groups: list[SuggestionGroup]
    items: list[NewRow]


class PausedInfo(TypedDict):
    reason: str
    attempt_id: int
    paused_at: str


class HeldBatchInfo(TypedDict):
    batch_id: int
    source: str
    import_job_id: int | None
    unit_id: int | None
    contexts_count: int
    reserve_usd: str
    expected_cached_usd: str
    created_at: str


class StaleUnitInfo(TypedDict):
    unit_id: int | None
    unit_code: str | None
    stale_count: int


class ConfigStaleInfo(TypedDict):
    stale_count: int
    prompt_version_current: int


class QueueStatus(TypedDict):
    spent_24h_usd: str
    daily_budget_usd: str
    claim_paused: PausedInfo | None
    held_batches: list[HeldBatchInfo]
    stale_units: list[StaleUnitInfo]
    config_stale: ConfigStaleInfo | None


class JobRow(TypedDict):
    job_id: int
    context_id: int
    title: str
    unit_id: int | None
    unit_code: str | None
    article: str | None
    path: list[str]
    status: str
    last_error_class: str | None
    error_text: str | None
    retry_generation: int
    attempts_in_generation: int
    matches: list[dict] | None
    updated_at: str


class UnitHoldGroup(TypedDict):
    """Задержанные одним и тем же набором совпадений в списке семей или в тексте
    промпта: «Список семей единицы м² содержит „…“ (семья N) — задержано K
    запросов». `place` — где совпадение: `family`, `prompt` или `mixed`;
    `family_id`/`family_title` заполнены, когда среди совпадений есть строка семьи."""

    unit_id: int | None
    unit_code: str | None
    place: Literal["family", "prompt", "mixed"]
    family_id: int | None
    family_title: str | None
    matches: list[dict]
    jobs_count: int


class JobsResponse(TypedDict):
    status: Literal["error", "privacy_hold"]
    items: list[JobRow]
    unit_groups: list[UnitHoldGroup]


def band_of(confidence: Decimal) -> Band:
    if confidence >= BAND_HIGH_MIN:
        return "high"
    if confidence >= BAND_MID_MIN:
        return "mid"
    return "low"


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _unit_clause(unit: int | str):
    """Условие на единицу каталожной строки: `"none"` — без единицы."""
    if unit == UNIT_NONE:
        return CatalogPosition.unit_id.is_(None)
    return CatalogPosition.unit_id == unit


class _UnitFingerprints:
    """Текущие отпечатки запроса контекстов по единицам. Список семей у всех
    контекстов единицы один, поэтому `candidates_hash` считается одним рендером на
    единицу, а `request_hash` контекста — `RequestHasher` единицы без повторного
    рендера списка семей."""

    def __init__(self) -> None:
        self._by_unit: dict[int | None, tuple[str, RequestHasher]] = {}

    def _entry(self, material: ContextRequestMaterial) -> tuple[str, RequestHasher]:
        entry = self._by_unit.get(material.unit_id)
        if entry is None:
            rendered = render_context_request(material, settings=settings)
            entry = (rendered.candidates_hash, RequestHasher(material, settings=settings))
            self._by_unit[material.unit_id] = entry
        return entry

    def candidates_hash(self, material: ContextRequestMaterial) -> str:
        return self._entry(material)[0]

    def request_hash(self, material: ContextRequestMaterial) -> str:
        return self._entry(material)[1].request_hash(material)

    def is_current(self, material: ContextRequestMaterial, request_hash: str) -> bool:
        """Опубликованное предложение на `request_hash` ещё актуально: контекст
        применим, и отпечаток его запроса сегодня тот же (спека §2.8)."""
        return is_applicable(material) and self.request_hash(material) == request_hash


def _path_list(material: ContextRequestMaterial) -> list[str]:
    if not material.path_counts:
        return []
    joined = top_path(material.path_counts)
    return joined.split(_PATH_SEPARATOR) if joined else []


# ---------------------------------------------------------------------------
#  Признаки строки
# ---------------------------------------------------------------------------

def _owner_count(context_id_column):
    """Число различных владельцев смет среди членов контекста: договор
    (`estimates.contract_id`) либо тендер (`tender_id` через предложение или раунд).
    Коррелированный скалярный подзапрос по колонке контекста внешнего запроса."""
    offer = aliased(Offer)
    rnd = aliased(TenderRound)
    owner_key = sa.case(
        (Estimate.contract_id.is_not(None), sa.func.concat("c", Estimate.contract_id)),
        else_=sa.func.concat("t", sa.func.coalesce(offer.tender_id, rnd.tender_id)),
    )
    return (
        sa.select(sa.func.count(sa.distinct(owner_key)))
        .select_from(ContextMember)
        .join(PositionItem, PositionItem.id == ContextMember.position_item_id)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .join(Estimate, Estimate.id == Lot.estimate_id)
        .outerjoin(offer, offer.id == Estimate.offer_id)
        .outerjoin(rnd, rnd.id == Estimate.round_id)
        .where(ContextMember.context_id == context_id_column)
        .scalar_subquery()
    )


def _rejected_marks(
    db: Session, pairs: set[tuple[int, int]]
) -> dict[tuple[int, int], dt.datetime]:
    """Последнее отклонение предложения той же семьи по контексту:
    `(context_id, family_id) -> decided_at`."""
    if not pairs:
        return {}
    rows = db.execute(
        sa.select(
            FamilySuggestion.context_id,
            FamilySuggestion.family_id,
            sa.func.max(FamilySuggestion.decided_at),
        )
        .where(
            FamilySuggestion.decision == "rejected",
            FamilySuggestion.context_id.in_({c for c, _ in pairs}),
            FamilySuggestion.family_id.in_({f for _, f in pairs}),
        )
        .group_by(FamilySuggestion.context_id, FamilySuggestion.family_id)
    ).all()
    return {(c, f): at for c, f, at in rows}


# ---------------------------------------------------------------------------
#  Очередь «Семья из списка»
# ---------------------------------------------------------------------------

def _list_queue(
    db: Session, *, unit_id: UnitFilter, band: Band | None, multi_owner_only: bool
) -> list[SuggestionGroup]:
    family = aliased(WorkFamily)
    family_unit = aliased(UnitOfMeasure)
    owners = _owner_count(FamilySuggestion.context_id)
    stmt = (
        sa.select(
            FamilySuggestion.id,
            FamilySuggestion.context_id,
            FamilySuggestion.family_id,
            FamilySuggestion.confidence,
            FamilySuggestion.reason,
            FamilySuggestion.request_hash,
            family.title.label("family_title"),
            family_unit.code.label("family_unit_code"),
            (owners >= 2).label("multi_owner"),
        )
        .join(family, family.id == FamilySuggestion.family_id)
        .outerjoin(family_unit, family_unit.id == family.unit_id)
        .where(
            FamilySuggestion.is_published.is_(True),
            FamilySuggestion.decision.is_(None),
        )
    )
    if unit_id is not None:
        stmt = (
            stmt.join(CatalogContext, CatalogContext.id == FamilySuggestion.context_id)
            .join(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
            .join(CatalogPosition, CatalogPosition.id == ContextBucket.catalog_position_id)
            .where(_unit_clause(unit_id))
        )
    if band == "high":
        stmt = stmt.where(FamilySuggestion.confidence >= BAND_HIGH_MIN)
    elif band == "mid":
        stmt = stmt.where(
            FamilySuggestion.confidence >= BAND_MID_MIN, FamilySuggestion.confidence < BAND_HIGH_MIN
        )
    elif band == "low":
        stmt = stmt.where(FamilySuggestion.confidence < BAND_MID_MIN)
    if multi_owner_only:
        stmt = stmt.where(owners >= 2)
    found = db.execute(stmt).all()
    if not found:
        return []

    # Опубликованное предложение на прежний отпечаток (вход контекста или список
    # семей изменились) решить нельзя: его не показываем до нового ответа модели.
    materials = load_request_material(db, {row.context_id for row in found})
    fingerprints = _UnitFingerprints()
    found = [
        row for row in found
        if fingerprints.is_current(materials[row.context_id], row.request_hash)
    ]
    if not found:
        return []
    rejected = _rejected_marks(db, {(row.context_id, row.family_id) for row in found})

    # Группа — пара (семья, полоса): у каждой группы ровно одна полоса.
    by_group: dict[tuple[int, Band], list] = {}
    for row in found:
        by_group.setdefault((row.family_id, band_of(row.confidence)), []).append(row)

    groups: list[SuggestionGroup] = []
    for (family_id, group_band), rows in by_group.items():
        rows.sort(key=lambda r: (-r.confidence, r.context_id))
        built: list[SuggestionRow] = []
        for r in rows:
            material = materials[r.context_id]
            decided_at = rejected.get((r.context_id, family_id))
            built.append(
                SuggestionRow(
                    suggestion_id=r.id,
                    context_id=r.context_id,
                    title=material.title,
                    unit_code=material.unit_code,
                    article=material.article,
                    path=_path_list(material),
                    confidence=str(r.confidence),
                    reason=r.reason,
                    multi_owner=bool(r.multi_owner),
                    previously_rejected=(
                        RejectedMark(
                            family_id=family_id,
                            family_title=r.family_title,
                            decided_at=decided_at.isoformat(),
                        )
                        if decided_at is not None
                        else None
                    ),
                )
            )
        groups.append(
            SuggestionGroup(
                family_id=family_id,
                family_title=rows[0].family_title,
                unit_code=rows[0].family_unit_code,
                band=group_band,
                rows=built,
                total=len(built),
            )
        )
    band_order = {"high": 0, "mid": 1, "low": 2}
    groups.sort(
        key=lambda g: (g["family_title"].casefold(), g["family_id"], band_order[g["band"]])
    )
    return groups


# ---------------------------------------------------------------------------
#  Очередь «Новая»
# ---------------------------------------------------------------------------

def _new_queue(db: Session, *, unit_id: UnitFilter, multi_owner_only: bool) -> list[NewRow]:
    owners_of_suggestion = _owner_count(FamilySuggestion.context_id)
    suggestions_stmt = (
        sa.select(
            FamilySuggestion.id,
            FamilySuggestion.context_id,
            FamilySuggestion.new_family_name,
            FamilySuggestion.confidence,
            FamilySuggestion.reason,
            FamilySuggestion.request_hash,
            (owners_of_suggestion >= 2).label("multi_owner"),
        )
        .where(
            FamilySuggestion.is_published.is_(True),
            FamilySuggestion.decision.is_(None),
            FamilySuggestion.family_id.is_(None),
        )
        .order_by(FamilySuggestion.confidence.desc(), FamilySuggestion.context_id)
    )
    if unit_id is not None:
        suggestions_stmt = (
            suggestions_stmt.join(CatalogContext, CatalogContext.id == FamilySuggestion.context_id)
            .join(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
            .join(CatalogPosition, CatalogPosition.id == ContextBucket.catalog_position_id)
            .where(_unit_clause(unit_id))
        )
    if multi_owner_only:
        suggestions_stmt = suggestions_stmt.where(owners_of_suggestion >= 2)
    suggestion_rows = db.execute(suggestions_stmt).all()

    # Контексты единицы без активных семей: модель их не спрашивали, предложения
    # нет. Условия — применимость (спека §2.7) без последнего пункта (кандидаты).
    owners_of_context = _owner_count(CatalogContext.id)
    active_family = (
        sa.select(sa.literal(1))
        .select_from(WorkFamily)
        .where(
            WorkFamily.status == FamilyStatus.active.value,
            WorkFamily.unit_id.is_not_distinct_from(CatalogPosition.unit_id),
        )
        .correlate(CatalogPosition)
        .exists()
    )
    has_member = (
        sa.select(sa.literal(1))
        .select_from(ContextMember)
        .where(ContextMember.context_id == CatalogContext.id)
        .correlate(CatalogContext)
        .exists()
    )
    bare_stmt = (
        sa.select(CatalogContext.id, (owners_of_context >= 2).label("multi_owner"))
        .join(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
        .join(CatalogPosition, CatalogPosition.id == ContextBucket.catalog_position_id)
        .where(
            CatalogContext.archived_at.is_(None),
            CatalogContext.work_family_id.is_(None),
            CatalogContext.semantic_state != SemanticState.NOT_APPLICABLE.value,
            CatalogContext.semantic_kind != SemanticKind.SYSTEM.value,
            has_member,
            ~active_family,
        )
        .order_by(CatalogContext.id)
    )
    if unit_id is not None:
        bare_stmt = bare_stmt.where(_unit_clause(unit_id))
    if multi_owner_only:
        bare_stmt = bare_stmt.where(owners_of_context >= 2)
    bare_rows = db.execute(bare_stmt).all()

    materials = load_request_material(
        db, {r.context_id for r in suggestion_rows} | {r.id for r in bare_rows}
    )
    # Предложение на прежний отпечаток в очереди не показывается (см. `_list_queue`).
    fingerprints = _UnitFingerprints()
    suggestion_rows = [
        r for r in suggestion_rows
        if fingerprints.is_current(materials[r.context_id], r.request_hash)
    ]
    items: list[NewRow] = []
    for r in suggestion_rows:
        material = materials[r.context_id]
        items.append(
            NewRow(
                suggestion_id=r.id,
                context_id=r.context_id,
                title=material.title,
                unit_code=material.unit_code,
                article=material.article,
                path=_path_list(material),
                new_family_name=r.new_family_name,
                is_system=is_system_name(r.new_family_name),
                confidence=str(r.confidence),
                reason=r.reason,
                multi_owner=bool(r.multi_owner),
            )
        )
    for r in bare_rows:
        material = materials[r.id]
        if material.path_broken:
            # Цикл разделов делает контекст неприменимым по другой причине: строка
            # «в единице нет активных семей» была бы неправдой.
            continue
        items.append(
            NewRow(
                suggestion_id=None,
                context_id=r.id,
                title=material.title,
                unit_code=material.unit_code,
                article=material.article,
                path=_path_list(material),
                new_family_name=None,
                is_system=False,
                confidence=None,
                reason=None,
                multi_owner=bool(r.multi_owner),
            )
        )
    return items


def list_suggestions(
    db: Session,
    *,
    queue: Literal["list", "new"],
    unit_id: UnitFilter = None,
    band: Band | None = None,
    multi_owner_only: bool = False,
) -> SuggestionQueue:
    """Очередь предложений экрана. `band` относится только к очереди `list`
    (у строк «новая» и «без семей» полосы нет)."""
    if queue == "list":
        return SuggestionQueue(
            queue="list",
            groups=_list_queue(db, unit_id=unit_id, band=band, multi_owner_only=multi_owner_only),
            items=[],
        )
    return SuggestionQueue(
        queue="new",
        groups=[],
        items=_new_queue(db, unit_id=unit_id, multi_owner_only=multi_owner_only),
    )


# ---------------------------------------------------------------------------
#  Задания: ошибки и задержанные
# ---------------------------------------------------------------------------

def _family_id_of_where(where: str) -> int | None:
    prefix = "family:"
    if not where.startswith(prefix):
        return None
    tail = where[len(prefix):]
    return int(tail) if tail.isdigit() else None


def _is_unit_level_where(where: str) -> bool:
    return where == "prompt" or _family_id_of_where(where) is not None


def _unit_hold_groups(
    db: Session, jobs: list, unit_codes: dict[int | None, str | None]
) -> list[UnitHoldGroup]:
    """Задержанные, у которых ВСЕ совпадения лежат в строках семей или в тексте
    промпта: «Отправить все K» отправляет задания единицы с точно таким набором
    совпадений."""
    grouped: dict[tuple, list] = {}
    for job in jobs:
        matches = job.privacy_matches or []
        if not matches or not all(_is_unit_level_where(m["where"]) for m in matches):
            continue
        key = (job.unit_id, tuple((m["text"], m["kind"], m["where"]) for m in matches))
        grouped.setdefault(key, []).append(job)
    if not grouped:
        return []
    family_ids = {
        fid
        for js in grouped.values()
        for m in js[0].privacy_matches
        if (fid := _family_id_of_where(m["where"])) is not None
    }
    titles = (
        dict(
            db.execute(
                sa.select(WorkFamily.id, WorkFamily.title).where(WorkFamily.id.in_(family_ids))
            ).all()
        )
        if family_ids
        else {}
    )
    groups: list[UnitHoldGroup] = []
    for (unit_id, _sig), members in grouped.items():
        first = members[0].privacy_matches
        family_ids_in_group = [
            fid for m in first if (fid := _family_id_of_where(m["where"])) is not None
        ]
        family_id = family_ids_in_group[0] if family_ids_in_group else None
        if not family_ids_in_group:
            place = "prompt"
        elif len(family_ids_in_group) == len(first):
            place = "family"
        else:
            place = "mixed"
        groups.append(
            UnitHoldGroup(
                unit_id=unit_id,
                unit_code=unit_codes.get(unit_id),
                place=place,
                family_id=family_id,
                family_title=titles.get(family_id),
                matches=[dict(m) for m in first],
                jobs_count=len(members),
            )
        )
    groups.sort(key=lambda g: (g["unit_code"] or "", g["family_id"] or 0, g["place"]))
    return groups


def list_jobs(db: Session, *, status: Literal["error", "privacy_hold"]) -> JobsResponse:
    """Задания в `error` или `privacy_hold`. Для `error` — последнее сообщение
    попытки (текст ошибки, а у схемной ошибки, где его нет, — причина из
    `validation_error`); для `privacy_hold` — набор совпадений и группировка по единице для
    совпадений в списке семей и в тексте промпта."""
    last_error_text = (
        sa.select(sa.func.coalesce(SemanticJobAttempt.error_text, SemanticJobAttempt.validation_error))
        .where(SemanticJobAttempt.job_id == SemanticJob.id)
        .order_by(SemanticJobAttempt.id.desc())
        .limit(1)
        .correlate(SemanticJob)
        .scalar_subquery()
    )
    jobs = db.execute(
        sa.select(SemanticJob, last_error_text.label("error_text"))
        .where(SemanticJob.status == status)
        .order_by(SemanticJob.id)
    ).all()
    if not jobs:
        return JobsResponse(status=status, items=[], unit_groups=[])

    materials = load_request_material(db, {job.context_id for job, _ in jobs})
    unit_codes: dict[int | None, str | None] = {None: None}
    unit_ids = {job.unit_id for job, _ in jobs if job.unit_id is not None}
    if unit_ids:
        unit_codes.update(
            db.execute(
                sa.select(UnitOfMeasure.id, UnitOfMeasure.code).where(UnitOfMeasure.id.in_(unit_ids))
            ).all()
        )

    items: list[JobRow] = []
    for job, error_text in jobs:
        material = materials[job.context_id]
        items.append(
            JobRow(
                job_id=job.id,
                context_id=job.context_id,
                title=material.title,
                unit_id=job.unit_id,
                unit_code=unit_codes.get(job.unit_id),
                article=material.article,
                path=_path_list(material),
                status=job.status,
                last_error_class=job.last_error_class,
                error_text=error_text,
                retry_generation=job.retry_generation,
                attempts_in_generation=job.attempts_in_generation,
                matches=job.privacy_matches if status == "privacy_hold" else None,
                updated_at=job.updated_at.isoformat(),
            )
        )
    unit_groups = (
        _unit_hold_groups(db, [job for job, _ in jobs], unit_codes)
        if status == SemanticJobStatus.privacy_hold.value
        else []
    )
    return JobsResponse(status=status, items=items, unit_groups=unit_groups)


# ---------------------------------------------------------------------------
#  Шапка
# ---------------------------------------------------------------------------

def _covers_context():
    """Условие над `SemanticJob` с внешним соединением `FamilySuggestion` по
    `result_suggestion_id`: задание покрывает контекст своего отпечатка (см.
    `_stale_scan`). Зеркало того, что сверка оживляет или переопубликовывает."""
    working = SemanticJob.status.in_(
        [
            SemanticJobStatus.pending.value,
            SemanticJobStatus.running.value,
            SemanticJobStatus.privacy_hold.value,
            SemanticJobStatus.error.value,
        ]
    )
    answered = sa.and_(
        SemanticJob.status == SemanticJobStatus.done.value,
        sa.or_(
            SemanticJob.result_suggestion_id.is_(None),
            FamilySuggestion.is_published.is_(True),
            FamilySuggestion.decision.isnot(None),
        ),
    )
    declined = sa.and_(
        SemanticJob.status == SemanticJobStatus.cancelled.value,
        SemanticJob.cancel_reason == SemanticCancelReason.privacy_declined.value,
    )
    return sa.or_(working, answered, declined)


def _stale_scan(db: Session) -> tuple[list[StaleUnitInfo], ConfigStaleInfo | None]:
    """Устаревшие единицы и конфигурация по применимым контекстам (спека §2.10).

    Единица устарела: у применимого контекста нет ПОКРЫВАЮЩЕГО задания с текущим
    `candidates_hash` (список семей изменился). Конфигурация изменена: такое
    задание есть, а с текущим `request_hash` — нет (промпт, модель или параметры
    поменяли тело запроса при том же списке семей).

    Покрывает задание, которое ещё работает или чей ответ жив: `pending`,
    `running`, `privacy_hold`, `error`, `done` без предложения, с опубликованным
    или с решённым предложением, `cancelled` по `privacy_declined`. Не покрывают
    те, что сверка при перезапросе оживит или переопубликует: `cancelled` по
    остальным причинам и `done` с предложением, снятым без решения. Иначе возврат
    к прежнему отпечатку (A -> B -> A) молчал бы при том, что видимого и
    исполняемого ответа нет.

    Список семей рендерится один раз на единицу, а не на каждый контекст
    (`_UnitFingerprints`): число запросов к базе от числа контекстов не зависит,
    а работа на контекст — разбор его материала и один SHA-256."""
    context_ids = list(
        db.execute(
            sa.select(CatalogContext.id).where(CatalogContext.archived_at.is_(None))
        ).scalars()
    )
    materials = load_request_material(db, context_ids)
    applicable = {cid: m for cid, m in materials.items() if is_applicable(m)}
    if not applicable:
        return [], None

    request_hashes: dict[int, set[str]] = {}
    candidates_hashes: dict[int, set[str]] = {}
    for context_id, request_hash, candidates_hash in db.execute(
        sa.select(SemanticJob.context_id, SemanticJob.request_hash, SemanticJob.candidates_hash)
        .outerjoin(FamilySuggestion, FamilySuggestion.id == SemanticJob.result_suggestion_id)
        .where(SemanticJob.context_id.in_(list(applicable)), _covers_context())
    ):
        request_hashes.setdefault(context_id, set()).add(request_hash)
        candidates_hashes.setdefault(context_id, set()).add(candidates_hash)

    fingerprints = _UnitFingerprints()
    stale_by_unit: dict[int | None, list] = {}
    config_stale = 0
    for context_id, material in applicable.items():
        if fingerprints.candidates_hash(material) not in candidates_hashes.get(context_id, ()):
            entry = stale_by_unit.setdefault(material.unit_id, [material.unit_code, 0])
            entry[1] += 1
        elif fingerprints.request_hash(material) not in request_hashes.get(context_id, ()):
            config_stale += 1

    stale_units = [
        StaleUnitInfo(unit_id=unit_id, unit_code=code, stale_count=count)
        for unit_id, (code, count) in stale_by_unit.items()
    ]
    stale_units.sort(key=lambda u: (u["unit_code"] is None, u["unit_code"] or "", u["unit_id"] or 0))
    return stale_units, (
        ConfigStaleInfo(stale_count=config_stale, prompt_version_current=PROMPT_VERSION)
        if config_stale
        else None
    )


def queue_status(db: Session) -> QueueStatus:
    """Шапка экрана: расход за 24 часа против бюджета, остановка захвата,
    удержанные пачки, устаревшие единицы и конфигурация. Деньги — строками."""
    now = dt.datetime.now(dt.UTC)
    state = db.get(SemanticWorkerState, 1)
    paused: PausedInfo | None = None
    if state is not None and state.claim_paused:
        paused = PausedInfo(
            reason=state.paused_reason,
            attempt_id=state.paused_attempt_id,
            paused_at=state.paused_at.isoformat(),
        )
    batches = db.execute(
        sa.select(SemanticReconcileBatch)
        .where(SemanticReconcileBatch.status == ReconcileBatchStatus.held.value)
        .order_by(SemanticReconcileBatch.id)
    ).scalars()
    held = [
        HeldBatchInfo(
            batch_id=b.id,
            source=b.source,
            import_job_id=b.import_job_id,
            unit_id=b.unit_id,
            contexts_count=b.contexts_count,
            reserve_usd=str(b.reserve_estimate_usd),
            expected_cached_usd=str(b.cached_estimate_usd),
            created_at=b.created_at.isoformat(),
        )
        for b in batches
    ]
    stale_units, config_stale = _stale_scan(db)
    return QueueStatus(
        spent_24h_usd=str(spent_last_24h(db, now=now)),
        daily_budget_usd=str(settings.SEMANTIC_DAILY_BUDGET_USD),
        claim_paused=paused,
        held_batches=held,
        stale_units=stale_units,
        config_stale=config_stale,
    )
