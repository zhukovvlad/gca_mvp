"""Загрузчики экрана «КП против нашей цены» (спека 4а §2.11, §2.17).

Модуль только читает: ни одного `INSERT`/`UPDATE`/`DELETE`. Расчёт — в чистых
слоях `services/our_price.py` и `services/our_price_money.py`; здесь только SQL
и свёртка прочитанного в плоские значения.

**Сигнал «каталог ещё разбирает»** (`catalog_signal`) считает два флага для
каждой строки сметы одним фиксированным числом запросов, не зависящим от числа
строк: строки со связями, задания очереди, удержанные пачки, состояние захвата.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from crud.analytics import latest_estimates
from crud.project_passport import CATEGORY_TOTALS
from crud.semantic_queue import open_suggestion_queues
from models import (
    CatalogContext,
    CatalogPosition,
    ContextBucket,
    ContextMember,
    ContextParameterValue,
    Contract,
    Contractor,
    Estimate,
    FamilyParameter,
    FamilyParameterSchema,
    FamilyParameterValue,
    Lot,
    ObjectModel,
    ParameterRole,
    PositionItem,
    Proposal,
    RateClass,
    ReconcileBatchStatus,
    SchemaStatus,
    SemanticJob,
    SemanticJobKind,
    SemanticJobStatus,
    SemanticReconcileBatch,
    SemanticWorkerState,
    UnitOfMeasure,
    WorkCategory,
    WorkFamily,
    WorkVariant,
    WorkVariantValue,
)
from services.our_price import HistoryContract, HistoryLine, KpLine, LineKey
from services.our_price_money import CategoryRow

#: Задание разбирается прямо сейчас (спека 4а §2.11).
_LIVE_STATUSES = (SemanticJobStatus.pending.value, SemanticJobStatus.running.value)
#: Задание остановилось и ждёт человека: не «разбирает», а «ждёт администратора».
_STUCK_STATUSES = (SemanticJobStatus.error.value, SemanticJobStatus.privacy_hold.value)

_CONTEXT_KINDS = (
    SemanticJobKind.family_suggestion.value,
    SemanticJobKind.context_values.value,
)


@dataclass(frozen=True)
class CatalogSignal:
    """Два независимых флага строки КП: каталог ещё разбирает её / строка ждёт
    решения администратора. Подпись по сочетанию флагов и состояния работы
    наносит чистое ядро."""

    in_progress: bool
    waiting_admin: bool
    #: Строка ждёт, потому что у её контекста есть открытое предложение семьи, видимое
    #: администратору, и очередь «Предложения», где оно лежит: `list` (семья из списка)
    #: или `new` (новая семья); иначе `None`.
    suggestion_queue: Literal["list", "new"] | None = None


@dataclass(frozen=True)
class _Row:
    position_item_id: int
    context_id: int | None
    family_id: int | None
    unit_id: int | None


def _rows_of(db: Session, estimate_ids: Sequence[int]) -> list[_Row]:
    """Все не-главы смет с контекстом, семьёй контекста и единицей каталожной
    строки его корзины (`NULL` у строки без членства)."""
    stmt = (
        sa.select(
            PositionItem.id,
            ContextMember.context_id,
            CatalogContext.work_family_id,
            CatalogPosition.unit_id,
        )
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .outerjoin(ContextMember, ContextMember.position_item_id == PositionItem.id)
        .outerjoin(CatalogContext, CatalogContext.id == ContextMember.context_id)
        .outerjoin(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
        .outerjoin(CatalogPosition, CatalogPosition.id == ContextBucket.catalog_position_id)
        .where(Lot.estimate_id.in_(list(estimate_ids)), PositionItem.is_chapter.is_(False))
    )
    return [_Row(*r) for r in db.execute(stmt).all()]


def _jobs_by_subject(
    db: Session, rows: Sequence[_Row]
) -> tuple[dict[int, set[str]], dict[int, set[str]], dict[int | None, set[str]]]:
    """Статусы заданий по предмету: контекст, семья (`family_schema`), единица
    (`family_discovery`). Единица без значения (`None`) — свой предмет."""
    context_ids = {r.context_id for r in rows if r.context_id is not None}
    family_ids = {r.family_id for r in rows if r.family_id is not None}
    unit_ids = {r.unit_id for r in rows if r.context_id is not None and r.family_id is None}
    unit_clause = SemanticJob.unit_id.in_([u for u in unit_ids if u is not None])
    if None in unit_ids:
        unit_clause = sa.or_(unit_clause, SemanticJob.unit_id.is_(None))
    stmt = sa.select(
        SemanticJob.kind,
        SemanticJob.status,
        SemanticJob.context_id,
        SemanticJob.family_id,
        SemanticJob.unit_id,
    ).where(
        SemanticJob.status.in_(_LIVE_STATUSES + _STUCK_STATUSES),
        sa.or_(
            sa.and_(
                SemanticJob.kind.in_(_CONTEXT_KINDS),
                SemanticJob.context_id.in_(list(context_ids)),
            ),
            sa.and_(
                SemanticJob.kind == SemanticJobKind.family_schema.value,
                SemanticJob.family_id.in_(list(family_ids)),
            ),
            sa.and_(SemanticJob.kind == SemanticJobKind.family_discovery.value, unit_clause),
        ),
    )
    by_context: dict[int, set[str]] = defaultdict(set)
    by_family: dict[int, set[str]] = defaultdict(set)
    by_unit: dict[int | None, set[str]] = defaultdict(set)
    for kind, status, context_id, family_id, unit_id in db.execute(stmt).all():
        if kind in _CONTEXT_KINDS:
            by_context[context_id].add(status)
        elif kind == SemanticJobKind.family_schema.value:
            by_family[family_id].add(status)
        else:
            by_unit[unit_id].add(status)
    return by_context, by_family, by_unit


def _held_subjects(db: Session) -> tuple[set[int], set[int]]:
    """Контексты и семьи, названные в отпечатках удержанных пачек. Элемент —
    объект с ключами `kind`, `context_id`, `family_id`, `schema_id`,
    `request_hash` (отсутствующий предмет — `null`)."""
    held_contexts: set[int] = set()
    held_families: set[int] = set()
    batches = db.execute(
        sa.select(SemanticReconcileBatch.held_fingerprints).where(
            SemanticReconcileBatch.status == ReconcileBatchStatus.held.value
        )
    ).scalars()
    for held in batches:
        for element in held:
            if not isinstance(element, dict):
                continue
            if element.get("context_id") is not None:
                held_contexts.add(int(element["context_id"]))
            if element.get("family_id") is not None:
                held_families.add(int(element["family_id"]))
    return held_contexts, held_families


def _suggestion_queues(db: Session, rows: Sequence[_Row]) -> dict[int, Literal["list", "new"]]:
    """Контексты без семьи → очередь «Предложения» с их открытым предложением.
    Видимость та же, что у очередей (`semantic_queue.open_suggestion_queues`):
    предложение опубликовано, не решено и на текущий отпечаток запроса."""
    return open_suggestion_queues(db, {r.context_id for r in rows if r.context_id is not None})


def _claim_paused(db: Session) -> bool:
    """Захват остановлен; строки состояния может не быть — тогда нет."""
    paused = db.execute(sa.select(SemanticWorkerState.claim_paused)).scalar_one_or_none()
    return bool(paused)


def catalog_signal(db: Session, estimate_ids: Sequence[int]) -> dict[int, CatalogSignal]:
    """`position_item_id` → сигнал для каждой строки (не главы) заданных смет.

    `in_progress` — у контекста строки `family_suggestion`/`context_values`, у
    семьи контекста `family_schema`, а при контексте без семьи — `family_discovery`
    по единице каталожной строки в `pending`/`running`. `waiting_admin` — те же
    три правила для заданий в `error`/`privacy_hold`, удержанная пачка с
    контекстом или семьёй строки в отпечатках, либо остановленный захват при
    живом задании строки (тогда `in_progress` гаснет: плашка не должна висеть
    без конца), либо открытое предложение семьи (опубликованное и не решённое) у контекста без
    семьи (живое задание старше: тогда `in_progress`); `suggestion_queue` называет
    очередь «Предложения» с этим предложением. Строка без членства в контексте — оба флага ложны. Флаги не
    смотрят на вариант контекста.
    """
    if not estimate_ids:
        return {}
    rows = _rows_of(db, estimate_ids)
    if not rows:
        return {}
    by_context, by_family, by_unit = _jobs_by_subject(db, rows)
    held_contexts, held_families = _held_subjects(db)
    queues = _suggestion_queues(db, rows)
    paused = _claim_paused(db)

    signals: dict[int, CatalogSignal] = {}
    for row in rows:
        if row.context_id is None:
            signals[row.position_item_id] = CatalogSignal(False, False)
            continue
        statuses = set(by_context.get(row.context_id, ()))
        if row.family_id is not None:
            statuses |= by_family.get(row.family_id, set())
        else:
            statuses |= by_unit.get(row.unit_id, set())
        live = bool(statuses & set(_LIVE_STATUSES))
        stuck = bool(statuses & set(_STUCK_STATUSES))
        held = row.context_id in held_contexts or row.family_id in held_families
        queue = queues.get(row.context_id)
        waiting = stuck or held or (live and paused) or (queue is not None and not live)
        signals[row.position_item_id] = CatalogSignal(
            in_progress=live and not paused,
            waiting_admin=waiting,
            suggestion_queue=queue if waiting else None,
        )
    return signals


# ---------------------------------------------------------------------------
#  Предикаты цены и веса в SQL
# ---------------------------------------------------------------------------


def _finite_positive(column: sa.ColumnElement) -> sa.ColumnElement[bool]:
    """«Конечно и больше нуля» без `NULL`: `IS NOT NULL` стоит первым конъюнктом,
    и на пустом входе итог `FALSE`, а не `NULL` (`FALSE AND NULL = FALSE`). Голое
    `> 0` пропустило бы `'NaN'::numeric` и `'Infinity'::numeric`."""
    return sa.and_(
        column.is_not(None),
        column > 0,
        column != Decimal("NaN"),
        column != Decimal("Infinity"),
    )


def _price_ok(column: sa.ColumnElement) -> sa.ColumnElement[bool]:
    """Цена пригодна: конечна и больше нуля. SQL-сторона `money.price.is_price`;
    согласие с ней держит тест против независимого оракула."""
    return _finite_positive(column)


def _weight_ok(column: sa.ColumnElement) -> sa.ColumnElement[bool]:
    """Вес пригоден: та же форма, другой факт (объём строки, а не цена). Отдельная
    функция, как `money.price.is_weight`: правила могут разойтись, и менять
    придётся одно место."""
    return _finite_positive(column)


#: Вес строки: предложенный объём, а при его отсутствии — объём заказчика.
_WEIGHT = sa.func.coalesce(PositionItem.suggested_quantity, PositionItem.quantity)

_UNKNOWN_LABEL = "не уточнено"
_LABEL_SEPARATOR = " · "


@dataclass(frozen=True)
class KpLineRow:
    """Строка КП с семантикой для экрана: числа для ядра и подписи для таблицы."""

    line: KpLine
    title: str
    unit: str | None
    #: Контекст членства строки; `None` у строки без членства.
    context_id: int | None
    #: Единица каталожной строки корзины контекста; `None` без членства или без единицы.
    unit_id: int | None
    #: Очередь «Предложения» с открытым предложением, из-за которого строка ждёт; иначе `None`.
    suggestion_queue: Literal["list", "new"] | None
    #: Объём строки — вес сравнения: предложенный объём, а при его отсутствии объём заказчика
    #: (`quantity` файла бывает заглушкой «1», спека 4а §2.12).
    quantity: Decimal | None
    section_ref: str | None
    article_root_id: int | None
    family_title: str | None
    variant_label: str | None
    secondary: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ArticleMeta:
    id: int
    code: str
    title: str
    sort_order: int


@dataclass(frozen=True)
class _VariantInfo:
    family_id: int
    family_title: str
    first_main: tuple[int, int | None] | None
    label: str


def _variant_infos(db: Session, variant_ids: Sequence[int]) -> dict[int, _VariantInfo]:
    """Ключ и подпись каждого варианта тремя запросами, сколько бы вариантов ни
    было: заголовки (семья, версия схемы варианта, действующая версия семьи),
    активные главные параметры версий и значения вариантов.

    Первый главный параметр — у варианта на действующей (`frozen`) версии, чьих
    активных главных параметров два и больше; у варианта на замещённой версии
    его нет (значения разных версий по идентификатору несопоставимы). Подпись
    строится по собственной версии варианта: «Семья · знач1 · знач2», «не
    уточнено» для пустого значения.
    """
    ids = sorted(set(variant_ids))
    current = aliased(FamilyParameterSchema)
    heads = db.execute(
        sa.select(
            WorkVariant.id,
            WorkVariant.family_id,
            WorkVariant.schema_id,
            WorkFamily.title,
            current.id.label("current_schema_id"),
        )
        .select_from(WorkVariant)
        .join(WorkFamily, WorkFamily.id == WorkVariant.family_id)
        .outerjoin(
            current,
            sa.and_(
                current.family_id == WorkVariant.family_id,
                current.status == SchemaStatus.frozen.value,
            ),
        )
        .where(WorkVariant.id.in_(ids))
    ).all()
    schema_ids = sorted({head.schema_id for head in heads})
    mains: dict[int, list[int]] = defaultdict(list)
    for schema_id, parameter_id in db.execute(
        sa.select(FamilyParameter.schema_id, FamilyParameter.id)
        .where(
            FamilyParameter.schema_id.in_(schema_ids),
            FamilyParameter.role == ParameterRole.main.value,
            FamilyParameter.archived_at.is_(None),
        )
        .order_by(FamilyParameter.schema_id, FamilyParameter.ordinal)
    ).all():
        mains[schema_id].append(parameter_id)
    values: dict[int, dict[int, tuple[int | None, str | None]]] = defaultdict(dict)
    for variant_id, parameter_id, value_id, text in db.execute(
        sa.select(
            WorkVariantValue.variant_id,
            WorkVariantValue.parameter_id,
            WorkVariantValue.value_id,
            FamilyParameterValue.value,
        )
        .select_from(WorkVariantValue)
        .outerjoin(FamilyParameterValue, FamilyParameterValue.id == WorkVariantValue.value_id)
        .where(WorkVariantValue.variant_id.in_(ids))
    ).all():
        values[variant_id][parameter_id] = (value_id, text)

    infos: dict[int, _VariantInfo] = {}
    for head in heads:
        parameters = mains.get(head.schema_id, [])
        own = values.get(head.id, {})
        first_main = None
        if head.current_schema_id == head.schema_id and len(parameters) >= 2:
            first_main = (parameters[0], own.get(parameters[0], (None, None))[0])
        parts = [head.title] + [own.get(parameter_id, (None, None))[1] or _UNKNOWN_LABEL for parameter_id in parameters]
        infos[head.id] = _VariantInfo(
            family_id=head.family_id,
            family_title=head.title,
            first_main=first_main,
            label=_LABEL_SEPARATOR.join(parts),
        )
    return infos


def _line_key(info: _VariantInfo, variant_id: int) -> LineKey:
    return LineKey(family_id=info.family_id, variant_id=variant_id, first_main=info.first_main)


def load_history(db: Session, *, exclude_object_id: int) -> tuple[list[HistoryContract], list[HistoryLine]]:
    """Последние сметы договоров и их строки с вариантом (спека 4а §2.3).

    В историю не входят смета оферты (`contract_id IS NULL`: её отсекает
    соединение с договорами) и договор объекта тендера; дата подписания состав не ограничивает. Строка истории — строка
    последней сметы без глав, чей контекст несёт вариант и не архивирован; цена
    и вес проходят предикаты в SQL (у системы — предикат на сумме строки).
    Строка с неизвестной базой НДС остаётся: она нужна ядру для названия
    причины выпадения договора. Версия сметы выбирается один раз, первым запросом;
    строки читаются по её идентификаторам, а не по повторному выбору «последней».
    """
    latest = latest_estimates()
    contract_rows = db.execute(
        sa.select(
            Contract.id,
            latest.c.estimate_id,
            Contract.contract_number,
            ObjectModel.title,
            Contract.signed_date,
            Contract.rate_class_id,
            RateClass.title,
            Contract.contractor_id,
            Contractor.title,
            ObjectModel.area_total_sp,
        )
        .select_from(latest)
        .join(Contract, Contract.id == latest.c.contract_id)
        .join(ObjectModel, ObjectModel.id == Contract.object_id)
        .join(RateClass, RateClass.id == Contract.rate_class_id)
        .join(Contractor, Contractor.id == Contract.contractor_id)
        .where(Contract.object_id != exclude_object_id)
        .order_by(Contract.id)
    ).all()
    contracts = [
        HistoryContract(
            contract_id=row[0],
            estimate_id=row[1],
            number=row[2],
            object_title=row[3],
            year=row[4].year,
            rate_class_id=row[5],
            rate_class_title=row[6],
            contractor_id=row[7],
            contractor_title=row[8],
            area=row[9],
        )
        for row in contract_rows
    ]

    is_system = CatalogContext.semantic_kind == "SYSTEM"
    suitable = sa.or_(
        sa.and_(is_system, _price_ok(PositionItem.total_cost_total)),
        sa.and_(sa.not_(is_system), _price_ok(PositionItem.unit_cost_total), _weight_ok(_WEIGHT)),
    )
    estimate_of = {contract.estimate_id: contract.contract_id for contract in contracts}
    line_rows = db.execute(
        sa.select(
            Estimate.id,
            CatalogContext.work_variant_id,
            is_system,
            PositionItem.unit_cost_total,
            PositionItem.total_cost_total,
            _WEIGHT,
            sa.func.coalesce(Estimate.vat_rate_base_override, Proposal.vat_rate),
        )
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .join(Estimate, Estimate.id == Lot.estimate_id)
        .join(ContextMember, ContextMember.position_item_id == PositionItem.id)
        .join(CatalogContext, CatalogContext.id == ContextMember.context_id)
        .where(
            Estimate.id.in_(list(estimate_of)),
            PositionItem.is_chapter.is_(False),
            CatalogContext.work_variant_id.is_not(None),
            CatalogContext.archived_at.is_(None),
            suitable,
        )
        .order_by(Estimate.contract_id, PositionItem.id)
    ).all()
    infos = _variant_infos(db, [row[1] for row in line_rows])
    lines = [
        HistoryLine(
            contract_id=estimate_of[row[0]],
            key=_line_key(infos[row[1]], row[1]),
            is_system=row[2],
            unit_price=row[3],
            total=row[4],
            weight=row[5],
            vat_base=row[6],
        )
        for row in line_rows
    ]
    return contracts, lines


def load_kp_lines(db: Session, estimate_ids: Sequence[int]) -> dict[int, list[KpLineRow]]:
    """Строки КП заданных смет (не главы) с семантикой и сигналом каталога.

    Строки отдаются все, без предикатов цены и веса: пригодность решает ядро.
    Число запросов не зависит от числа строк: строки, сигнал каталога, три
    запроса вариантов, второстепенные параметры и дерево статей. Строка без
    членства или без варианта — `key = None`, `variant_label = None`; название
    семьи берётся у любого контекста, у которого она есть.
    """
    result: dict[int, list[KpLineRow]] = {estimate_id: [] for estimate_id in estimate_ids}
    if not result:
        return result
    chapter = aliased(PositionItem)
    rows = db.execute(
        sa.select(
            Lot.estimate_id,
            PositionItem.id,
            PositionItem.job_title_in_proposal,
            UnitOfMeasure.symbol,
            PositionItem.item_number_in_proposal,
            PositionItem.position_key_in_proposal,
            PositionItem.unit_cost_total,
            PositionItem.total_cost_total,
            _WEIGHT,
            chapter.work_category_id,
            CatalogContext.id,
            CatalogContext.work_variant_id,
            CatalogContext.semantic_kind,
            WorkFamily.title,
            CatalogPosition.unit_id,
        )
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .outerjoin(UnitOfMeasure, UnitOfMeasure.id == PositionItem.unit_id)
        # составной FK держит главу и строку в одном предложении
        .outerjoin(chapter, chapter.id == PositionItem.chapter_item_id)
        .outerjoin(ContextMember, ContextMember.position_item_id == PositionItem.id)
        .outerjoin(CatalogContext, CatalogContext.id == ContextMember.context_id)
        .outerjoin(WorkFamily, WorkFamily.id == CatalogContext.work_family_id)
        .outerjoin(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
        .outerjoin(CatalogPosition, CatalogPosition.id == ContextBucket.catalog_position_id)
        .where(Lot.estimate_id.in_(list(estimate_ids)), PositionItem.is_chapter.is_(False))
        .order_by(Lot.estimate_id, PositionItem.id)
    ).all()
    signals = catalog_signal(db, estimate_ids)
    infos = _variant_infos(db, [row[11] for row in rows if row[11] is not None])
    secondary = _secondary_values(db, [row[10] for row in rows if row[10] is not None])
    root_of, _ = category_roots(db)
    for row in rows:
        (
            estimate_id,
            item_id,
            title,
            unit,
            number,
            key_text,
            unit_price,
            total,
            weight,
            category_id,
            context_id,
            variant_id,
            kind,
            family_title,
            unit_id,
        ) = row
        info = infos.get(variant_id) if variant_id is not None else None
        signal = signals.get(item_id, CatalogSignal(False, False))
        result[estimate_id].append(
            KpLineRow(
                line=KpLine(
                    position_item_id=item_id,
                    unit_price=unit_price,
                    total=total,
                    weight=weight,
                    key=_line_key(info, variant_id) if info is not None else None,
                    is_system=kind == "SYSTEM",
                    in_progress=signal.in_progress,
                    waiting_admin=signal.waiting_admin,
                ),
                title=title,
                unit=unit,
                context_id=context_id,
                unit_id=unit_id,
                suggestion_queue=signal.suggestion_queue,
                quantity=weight,
                section_ref=number or key_text,
                article_root_id=root_of.get(category_id) if category_id is not None else None,
                family_title=family_title,
                variant_label=info.label if info is not None else None,
                secondary=secondary.get(context_id, ()) if context_id is not None else (),
            )
        )
    return result


def _secondary_values(db: Session, context_ids: Sequence[int]) -> dict[int, tuple[tuple[str, str], ...]]:
    """Пары «параметр, значение» второстепенных активных параметров контекстов
    по номеру параметра; пустое значение читается «не уточнено», текст не из
    списка — «не из списка: «текст»», как его показывает каталог семей."""
    rows = db.execute(
        sa.select(
            ContextParameterValue.context_id,
            FamilyParameter.name,
            FamilyParameterValue.value,
            ContextParameterValue.off_list_text,
        )
        .select_from(ContextParameterValue)
        .join(FamilyParameter, FamilyParameter.id == ContextParameterValue.parameter_id)
        .outerjoin(FamilyParameterValue, FamilyParameterValue.id == ContextParameterValue.value_id)
        .where(
            ContextParameterValue.context_id.in_(sorted(set(context_ids))),
            FamilyParameter.role == ParameterRole.secondary.value,
            FamilyParameter.archived_at.is_(None),
        )
        .order_by(ContextParameterValue.context_id, FamilyParameter.ordinal)
    ).all()
    grouped: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for context_id, name, value, off_list in rows:
        text = value or (f"не из списка: «{off_list}»" if off_list else _UNKNOWN_LABEL)
        grouped[context_id].append((name, text))
    return {context_id: tuple(pairs) for context_id, pairs in grouped.items()}


def kp_vat_bases(db: Session, estimate_ids: Sequence[int]) -> dict[int, Decimal | None]:
    """База НДС смет КП: `vat_rate_base_override`, иначе единая ставка всех
    предложений сметы; разногласие или неизвестная ставка — `None` (AGENTS.md §4).
    Своя копия правила свода по этапам; согласие с ним держит тест."""
    ids = list(estimate_ids)
    overrides = dict(
        db.execute(sa.select(Estimate.id, Estimate.vat_rate_base_override).where(Estimate.id.in_(ids))).all()
    )
    declared: dict[int, set[Decimal | None]] = {estimate_id: set() for estimate_id in ids}
    for estimate_id, rate in db.execute(
        sa.select(Lot.estimate_id, Proposal.vat_rate)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id.in_(ids))
    ).all():
        declared[estimate_id].add(rate)
    bases: dict[int, Decimal | None] = {}
    for estimate_id in ids:
        override = overrides.get(estimate_id)
        rates = declared[estimate_id]
        if override is not None:
            bases[estimate_id] = override
        elif len(rates) == 1:
            bases[estimate_id] = next(iter(rates))
        else:
            bases[estimate_id] = None
    return bases


def category_rows(db: Session, estimate_ids: Sequence[int]) -> list[CategoryRow]:
    """Строки `v_category_totals` заданных смет как есть: по одной на предложение
    (и источник суммы), без накопления — его делает ядро. Базу НДС несёт сама
    строка VIEW."""
    if not estimate_ids:
        return []
    rows = db.execute(
        sa.select(
            CATEGORY_TOTALS.c.estimate_id,
            CATEGORY_TOTALS.c.work_category_id,
            CATEGORY_TOTALS.c.vat_rate_base,
            CATEGORY_TOTALS.c.amount,
        )
        .where(CATEGORY_TOTALS.c.estimate_id.in_(list(estimate_ids)))
        .order_by(
            CATEGORY_TOTALS.c.estimate_id,
            CATEGORY_TOTALS.c.proposal_id,
            CATEGORY_TOTALS.c.work_category_id,
            CATEGORY_TOTALS.c.source,
        )
    ).all()
    return [CategoryRow(estimate_id=r[0], work_category_id=r[1], vat_base=r[2], amount=r[3]) for r in rows]


def category_roots(db: Session) -> tuple[dict[int, int], dict[int, ArticleMeta]]:
    """Корень каждой статьи классификатора (корень — сам себе) и описание корней.
    Порядок статей экрана — `sort_order` корня. Дерево читается одним запросом."""
    rows = db.execute(
        sa.select(
            WorkCategory.id,
            WorkCategory.parent_id,
            WorkCategory.code,
            WorkCategory.title,
            WorkCategory.sort_order,
        )
    ).all()
    parent = {row[0]: row[1] for row in rows}

    def root(category_id: int) -> int:
        seen = {category_id}
        while parent[category_id] is not None:
            category_id = parent[category_id]
            if category_id in seen:
                raise ValueError(f"цикл в дереве статей у {category_id}")
            seen.add(category_id)
        return category_id

    root_of = {category_id: root(category_id) for category_id in parent}
    meta = {
        row[0]: ArticleMeta(id=row[0], code=row[2], title=row[3], sort_order=row[4]) for row in rows if row[1] is None
    }
    return root_of, meta
