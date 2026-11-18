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
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from crud.analytics import latest_estimates
from crud.comparison import _estimate_period
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
    EstimateAdditionalWork,
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
    SectionChain,
    SemanticJob,
    SemanticJobKind,
    SemanticJobStatus,
    SemanticReconcileBatch,
    SemanticWorkerState,
    UnitOfMeasure,
    ValueSource,
    WorkCategory,
    WorkFamily,
    WorkVariant,
    WorkVariantValue,
)
from services.analog_ladder import CompositeValue, FamilyLadderInfo, Form, WorkHistoryLine
from services.composite_values import composite_parts
from services.our_price import HistoryContract, HistoryLine, KpLine, LineKey
from services.our_price_money import CategoryRow
from services.zone_rules import Zone, normalize_title

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


def _rows_of(db: Session, estimate_ids: Sequence[int], only_position_item_id: int | None = None) -> list[_Row]:
    """Все не-главы смет с контекстом, семьёй контекста и единицей каталожной
    строки его корзины (`NULL` у строки без членства); `only_position_item_id` — одна строка."""
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
    if only_position_item_id is not None:
        stmt = stmt.where(PositionItem.id == only_position_item_id)
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


def catalog_signal(
    db: Session, estimate_ids: Sequence[int], *, only_position_item_id: int | None = None
) -> dict[int, CatalogSignal]:
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
    rows = _rows_of(db, estimate_ids, only_position_item_id)
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

#: Откуда значение параметра в строке: шесть источников записи контекста как есть и три
#: производных — записи нет (`missing`), вариант на прежней схеме (`old_schema`), запись
#: разошлась с вариантом при той же версии схемы (`stale`).
ParamSource = Literal["name", "path", "manual", "path_conflict", "none", "off_list", "missing", "old_schema", "stale"]


@dataclass(frozen=True)
class LineParam:
    """Параметр варианта строки КП с названием, значением и происхождением значения
    (спека параметров варианта §2.2)."""

    parameter_id: int
    name: str
    role: Literal["main", "secondary"]
    #: Главный — текст значения варианта; второстепенный — текст записи.
    value: str | None
    #: Текст «не из списка» записи; `None` у остальных источников.
    off_list: str | None
    #: Составное значение главного параметра варианта на действующей схеме.
    composite: bool
    source: ParamSource
    #: Значение ждёт перезапроса (`refresh_requested_at` записи).
    refreshing: bool
    #: Что говорит запись каталога, когда она разошлась с вариантом (`stale`); иначе `None`.
    catalog_value: str | None


@dataclass(frozen=True)
class VariantMain:
    """Активный главный параметр схемы варианта и значение варианта по нему."""

    parameter_id: int
    name: str
    value_id: int | None
    text: str | None


@dataclass(frozen=True)
class _ContextValue:
    """Запись значения контекста по параметру: что в ней лежит и ждёт ли перезапроса."""

    value_id: int | None
    text: str | None
    off_list: str | None
    source: str
    refreshing: bool


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
    #: `item_number_in_proposal` как в файле, без подмены ключом позиции.
    item_number: str | None
    article_root_id: int | None
    family_title: str | None
    variant_label: str | None
    secondary: tuple[tuple[str, str], ...]
    #: Статья раздела строки (`chapter.work_category_id`); `None` — «Нераспределённое».
    category_id: int | None = None
    #: Раздел сметы, в котором лежит строка; `None` — строка вне разделов.
    chapter_item_id: int | None = None
    #: Параметры варианта строки с названиями: главные схемы варианта, затем второстепенные
    #: действующей схемы (спека параметров варианта §2.2); `()` у строки без варианта.
    params: tuple[LineParam, ...] = ()


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
    #: Версия схемы варианта и действующая версия семьи (`None` — действующей нет).
    schema_id: int
    current_schema_id: int | None
    #: parameter_id -> value_id | `None` по активным главным параметрам; только у варианта
    #: на действующей версии (у варианта на замещённой версии значения несопоставимы).
    main_values: Mapping[int, int | None] = field(default_factory=dict)
    #: parameter_id -> форма значения: id составного значения либо `"simple"`; ключи те же, что у `main_values`.
    forms: Mapping[int, Form] = field(default_factory=dict)
    #: Активные главные параметры собственной схемы варианта по `ordinal`, и на прежней схеме тоже.
    mains: tuple[VariantMain, ...] = ()


def _variant_infos(db: Session, variant_ids: Sequence[int]) -> dict[int, _VariantInfo]:
    """Ключ и подпись каждого варианта четырьмя запросами, сколько бы вариантов ни
    было: заголовки (семья, версия схемы варианта, действующая версия семьи),
    активные главные параметры версий, значения вариантов и составные среди них
    (форма значения, спека упрощения каталога §2.2).

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
    names: dict[int, str] = {}
    for schema_id, parameter_id, parameter_name in db.execute(
        sa.select(FamilyParameter.schema_id, FamilyParameter.id, FamilyParameter.name)
        .where(
            FamilyParameter.schema_id.in_(schema_ids),
            FamilyParameter.role == ParameterRole.main.value,
            FamilyParameter.archived_at.is_(None),
        )
        .order_by(FamilyParameter.schema_id, FamilyParameter.ordinal)
    ).all():
        mains[schema_id].append(parameter_id)
        names[parameter_id] = parameter_name
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
    composite_of = composite_parts(
        db, {value_id for own in values.values() for value_id, _ in own.values() if value_id is not None}
    )

    infos: dict[int, _VariantInfo] = {}
    for head in heads:
        parameters = mains.get(head.schema_id, [])
        own = values.get(head.id, {})
        first_main = None
        on_current = head.current_schema_id == head.schema_id
        if on_current and len(parameters) >= 2:
            first_main = (parameters[0], own.get(parameters[0], (None, None))[0])
        parts = [head.title] + [own.get(parameter_id, (None, None))[1] or _UNKNOWN_LABEL for parameter_id in parameters]
        main_values = (
            {parameter_id: own.get(parameter_id, (None, None))[0] for parameter_id in parameters} if on_current else {}
        )
        infos[head.id] = _VariantInfo(
            family_id=head.family_id,
            family_title=head.title,
            first_main=first_main,
            label=_LABEL_SEPARATOR.join(parts),
            schema_id=head.schema_id,
            current_schema_id=head.current_schema_id,
            main_values=main_values,
            forms={
                parameter_id: value_id if value_id in composite_of else "simple"
                for parameter_id, value_id in main_values.items()
            },
            mains=tuple(
                VariantMain(
                    parameter_id=parameter_id,
                    name=names[parameter_id],
                    value_id=own.get(parameter_id, (None, None))[0],
                    text=own.get(parameter_id, (None, None))[1],
                )
                for parameter_id in parameters
            ),
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
    причины выпадения договора. Период договора (`period`) — ценовой период его последней сметы
    без ряда ИПЦ (`_estimate_period`). Версия сметы выбирается один раз, первым запросом;
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
            latest.c.amendment_no,
            latest.c.data_prepared_on_date,
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
            period=_estimate_period(row[10], row[11], row[4]),
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


def _family_ladder_infos(db: Session) -> dict[int, FamilyLadderInfo]:
    """Сведения семей для ступеней 2 и 4: действующая версия, активные главные параметры по
    порядку `ordinal`, отпускаемые (`releasable IS TRUE`) и имена. Один запрос на все семьи."""
    ordered: dict[int, tuple[int, list[tuple[int, str, bool]]]] = {}
    for family_id, schema_id, parameter_id, name, releasable in db.execute(
        sa.select(
            FamilyParameterSchema.family_id,
            FamilyParameterSchema.id,
            FamilyParameter.id,
            FamilyParameter.name,
            FamilyParameter.releasable,
        )
        .select_from(FamilyParameterSchema)
        .outerjoin(
            FamilyParameter,
            sa.and_(
                FamilyParameter.schema_id == FamilyParameterSchema.id,
                FamilyParameter.role == ParameterRole.main.value,
                FamilyParameter.archived_at.is_(None),
            ),
        )
        .where(FamilyParameterSchema.status == SchemaStatus.frozen.value)
        .order_by(FamilyParameterSchema.family_id, FamilyParameter.ordinal)
    ).all():
        _, parameters = ordered.setdefault(family_id, (schema_id, []))
        if parameter_id is not None:
            parameters.append((parameter_id, name, releasable is True))
    return {
        family_id: FamilyLadderInfo(
            schema_id=schema_id,
            main_parameter_ids=tuple(pid for pid, _, _ in parameters),
            releasable_ids=frozenset(pid for pid, _, releasable in parameters if releasable),
            names={pid: name for pid, name, _ in parameters},
        )
        for family_id, (schema_id, parameters) in ordered.items()
    }


def _conflicted_parameters(db: Session, context_ids: Sequence[int]) -> dict[int, dict[int, frozenset[int]]]:
    """Главные параметры контекстов со значением `path_conflict`: context_id -> schema_id ->
    parameter_id. Один запрос на любое число контекстов; конфликт — не значение (спека §2.8)."""
    ids = sorted(set(context_ids))
    if not ids:
        return {}
    grouped: dict[int, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
    for context_id, schema_id, parameter_id in db.execute(
        sa.select(ContextParameterValue.context_id, FamilyParameter.schema_id, FamilyParameter.id)
        .select_from(ContextParameterValue)
        .join(FamilyParameter, FamilyParameter.id == ContextParameterValue.parameter_id)
        .where(
            ContextParameterValue.context_id.in_(ids),
            ContextParameterValue.source == ValueSource.path_conflict.value,
            FamilyParameter.role == ParameterRole.main.value,
            FamilyParameter.archived_at.is_(None),
        )
    ).all():
        grouped[context_id][schema_id].add(parameter_id)
    return {
        context_id: {schema_id: frozenset(parameters) for schema_id, parameters in by_schema.items()}
        for context_id, by_schema in grouped.items()
    }


def _conflicts_of(
    conflicts: Mapping[int, Mapping[int, frozenset[int]]], context_id: int | None, schema_id: int | None
) -> frozenset[int]:
    """Конфликтные главные параметры контекста на версии схемы варианта; иначе пусто."""
    if context_id is None or schema_id is None:
        return frozenset()
    return conflicts.get(context_id, {}).get(schema_id, frozenset())


def load_work_history(
    db: Session, *, estimate_of: Mapping[int, int], family_ids: Collection[int] | None = None
) -> tuple[list[WorkHistoryLine], dict[int, FamilyLadderInfo]]:
    """Строки смет договоров по работам для лестницы аналогов и сведения семей.

    `estimate_of` — отображение `contract_id -> estimate_id` смет, которые выбрал
    `load_history` (`HistoryContract.contract_id`, `.estimate_id`). Сметы здесь заново не
    выбираются: иначе допсоглашение, закоммиченное между двумя чтениями, дало бы строки новой
    сметы под договором со старым периодом и коэффициентом ИПЦ. Состав истории (договор
    объекта тендера и сметы КП не входят) наследуется от `load_history`. Строка — не глава,
    не система, с вариантом в неархивном контексте, ценой и весом, прошедшими предикаты, и с
    цепочкой, у которой зона определена: аналог без зоны не аналог (спека §2.8). Строка с
    неизвестной базой НДС остаётся — ядро называет по ней выпавший договор. Строки читаются
    по идентификаторам смет. Число запросов фиксировано: строки, три запроса вариантов,
    конфликтные параметры контекстов, семьи. `family_ids` — только строки этих семей (поиск
    справочных строк берёт семью строки КП и связанные с ней); `None` — все семьи.
    """
    contract_of = {estimate_id: contract_id for contract_id, estimate_id in estimate_of.items()}
    is_system = CatalogContext.semantic_kind == "SYSTEM"
    rows = db.execute(
        sa.select(
            PositionItem.id,
            Estimate.id,
            PositionItem.section_chain_id,
            SectionChain.zone,
            SectionChain.titles,
            CatalogContext.work_variant_id,
            CatalogContext.id,
            PositionItem.unit_cost_total,
            _WEIGHT,
            sa.func.coalesce(Estimate.vat_rate_base_override, Proposal.vat_rate),
            CatalogPosition.unit_id,
        )
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .join(Estimate, Estimate.id == Lot.estimate_id)
        .join(SectionChain, SectionChain.id == PositionItem.section_chain_id)
        .join(ContextMember, ContextMember.position_item_id == PositionItem.id)
        .join(CatalogContext, CatalogContext.id == ContextMember.context_id)
        .outerjoin(CatalogPosition, CatalogPosition.id == PositionItem.catalog_position_id)
        .where(
            Estimate.id.in_(list(contract_of)),
            PositionItem.is_chapter.is_(False),
            SectionChain.zone.is_not(None),
            CatalogContext.work_variant_id.is_not(None),
            CatalogContext.archived_at.is_(None),
            sa.not_(is_system),
            _price_ok(PositionItem.unit_cost_total),
            _weight_ok(_WEIGHT),
            *([] if family_ids is None else [CatalogContext.work_family_id.in_(sorted(set(family_ids)))]),
        )
        .order_by(Estimate.contract_id, PositionItem.id)
    ).all()
    infos = _variant_infos(db, [row[5] for row in rows])
    conflicts = _conflicted_parameters(db, [row[6] for row in rows])
    titles_of: dict[int, frozenset[str]] = {}
    lines: list[WorkHistoryLine] = []
    for (
        item_id, estimate_id, chain_id, zone, titles, variant_id, context_id, unit_price, weight, vat_base, unit_id
    ) in rows:
        if chain_id not in titles_of:
            titles_of[chain_id] = frozenset(normalize_title(title) for title in titles)
        info = infos[variant_id]
        lines.append(
            WorkHistoryLine(
                position_item_id=item_id,
                contract_id=contract_of[estimate_id],
                family_id=info.family_id,
                chain_id=chain_id,
                zone=zone,
                chain_titles=titles_of[chain_id],
                variant_id=variant_id,
                schema_id=info.schema_id,
                main_values=info.main_values,
                unit_price=unit_price,
                weight=weight,
                vat_base=vat_base,
                conflicted=_conflicts_of(conflicts, context_id, info.schema_id),
                forms=info.forms,
                unit_id=unit_id,
            )
        )
    return lines, _family_ladder_infos(db)


def load_composites(db: Session, line: KpLine) -> dict[int, CompositeValue]:
    """Составные значения главных параметров одной строки КП: parameter_id -> вид и части (id, текст).
    У строки без составных значений запросов нет; иначе два запроса, сколько бы частей ни было."""
    composite_ids = {parameter_id: form for parameter_id, form in line.forms.items() if form != "simple"}
    parts_of = composite_parts(db, composite_ids.values())
    part_ids = {part for _, parts in parts_of.values() for part in parts}
    texts = (
        dict(
            db.execute(
                sa.select(FamilyParameterValue.id, FamilyParameterValue.value).where(
                    FamilyParameterValue.id.in_(part_ids)
                )
            ).all()
        )
        if part_ids
        else {}
    )
    return {
        parameter_id: CompositeValue(
            kind=parts_of[value_id][0], parts=tuple((part, texts[part]) for part in parts_of[value_id][1])
        )
        for parameter_id, value_id in composite_ids.items()
    }


def load_value_texts(db: Session, value_ids: Collection[int]) -> dict[int, str]:
    """Тексты значений параметров одним запросом; составное значение — его собственный текст.
    Пустое множество — без запроса."""
    if not value_ids:
        return {}
    return dict(
        db.execute(
            sa.select(FamilyParameterValue.id, FamilyParameterValue.value).where(
                FamilyParameterValue.id.in_(set(value_ids))
            )
        ).all()
    )


def load_analog_details(
    db: Session, position_item_ids: Sequence[int]
) -> dict[int, tuple[str, str | None, list[str], int | None, int | None]]:
    """Справка панели по строкам договоров одним запросом: наименование, единица, названия
    разделов цепочки, статья раздела строки договора (`None` — раздела нет или у него нет статьи)
    и раздел сметы договора, в котором лежит строка (`None` — вне разделов)."""
    ids = sorted(set(position_item_ids))
    if not ids:
        return {}
    chapter = aliased(PositionItem)
    return {
        item_id: (title, unit, list(titles or []), category_id, chapter_item_id)
        for item_id, title, unit, titles, category_id, chapter_item_id in db.execute(
            sa.select(
                PositionItem.id,
                PositionItem.job_title_in_proposal,
                UnitOfMeasure.symbol,
                SectionChain.titles,
                chapter.work_category_id,
                PositionItem.chapter_item_id,
            )
            .select_from(PositionItem)
            .outerjoin(UnitOfMeasure, UnitOfMeasure.id == PositionItem.unit_id)
            .outerjoin(SectionChain, SectionChain.id == PositionItem.section_chain_id)
            # составной FK держит главу и строку в одном предложении
            .outerjoin(chapter, chapter.id == PositionItem.chapter_item_id)
            .where(PositionItem.id.in_(ids))
        ).all()
    }


@dataclass(frozen=True)
class _Chain:
    zone: Zone | None
    titles: frozenset[str]
    #: Зона не определена, но у цепочки есть предложение модели или ответ «не знаю».
    disputed: bool
    live: bool
    stuck: bool


def _chain_states(db: Session, chain_ids: Sequence[int]) -> dict[int, _Chain]:
    """Зона, названия и состояние задания `section_zone` цепочек: два запроса на любое число."""
    ids = sorted(set(chain_ids))
    if not ids:
        return {}
    statuses: dict[int, set[str]] = defaultdict(set)
    for chain_id, status in db.execute(
        sa.select(SemanticJob.section_chain_id, SemanticJob.status).where(
            SemanticJob.kind == SemanticJobKind.section_zone.value,
            SemanticJob.section_chain_id.in_(ids),
            SemanticJob.status.in_(_LIVE_STATUSES + _STUCK_STATUSES),
        )
    ).all():
        statuses[chain_id].add(status)
    return {
        chain_id: _Chain(
            zone=zone,
            titles=frozenset(normalize_title(title) for title in titles),
            disputed=zone is None and proposed is not None,
            live=bool(statuses[chain_id] & set(_LIVE_STATUSES)),
            stuck=bool(statuses[chain_id] & set(_STUCK_STATUSES)),
        )
        for chain_id, zone, titles, proposed in db.execute(
            sa.select(SectionChain.id, SectionChain.zone, SectionChain.titles, SectionChain.proposed_zone).where(
                SectionChain.id.in_(ids)
            )
        ).all()
    }


def _releasable_live(db: Session, current_schema_of: Mapping[int, int]) -> set[int]:
    """Семьи, у которых живое задание `releasable_params` на действующую схему (спека §2.16 п. 5)."""
    if not current_schema_of:
        return set()
    return {
        family_id
        for family_id, schema_id in db.execute(
            sa.select(SemanticJob.family_id, SemanticJob.schema_id).where(
                SemanticJob.kind == SemanticJobKind.releasable_params.value,
                SemanticJob.status.in_(_LIVE_STATUSES),
                SemanticJob.family_id.in_(list(current_schema_of)),
            )
        ).all()
        if current_schema_of.get(family_id) == schema_id
    }


def load_kp_lines(
    db: Session, estimate_ids: Sequence[int], *, only_position_item_id: int | None = None
) -> dict[int, list[KpLineRow]]:
    """Строки КП заданных смет (не главы) с семантикой и сигналом каталога.

    `only_position_item_id` — только эта строка (поиск справочных строк не читает всю смету);
    её `KpLineRow` равна той, что даёт полный вызов.

    Строки отдаются все, без предикатов цены и веса: пригодность решает ядро.
    Число запросов не зависит от числа строк: строки, сигнал каталога, три
    запроса вариантов, второстепенные параметры, записи значений контекстов и второстепенные параметры
    действующих схем (параметры строки), конфликтные главные параметры и дерево статей. Строка без
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
            PositionItem.section_chain_id,
            PositionItem.chapter_item_id,
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
        .where(
            Lot.estimate_id.in_(list(estimate_ids)),
            PositionItem.is_chapter.is_(False),
            *([] if only_position_item_id is None else [PositionItem.id == only_position_item_id]),
        )
        .order_by(Lot.estimate_id, PositionItem.id)
    ).all()
    signals = catalog_signal(db, estimate_ids, only_position_item_id=only_position_item_id)
    infos = _variant_infos(db, [row[11] for row in rows if row[11] is not None])
    secondary = _secondary_values(db, [row[10] for row in rows if row[10] is not None])
    variant_context_ids = [row[10] for row in rows if row[10] is not None and row[11] is not None]
    context_values = _context_values(db, variant_context_ids)
    current_secondaries = _current_secondaries(db, sorted({info.family_id for info in infos.values()}))
    conflicts = _conflicted_parameters(db, [row[10] for row in rows if row[10] is not None])
    root_of, _ = category_roots(db)
    chains = _chain_states(db, [row[15] for row in rows if row[15] is not None])
    current_schema_of = {
        info.family_id: info.current_schema_id for info in infos.values() if info.current_schema_id is not None
    }
    releasable_live = _releasable_live(db, current_schema_of)
    paused = _claim_paused(db) if chains or releasable_live else False
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
            chain_id,
            chapter_item_id,
        ) = row
        info = infos.get(variant_id) if variant_id is not None else None
        signal = signals.get(item_id, CatalogSignal(False, False))
        is_system = kind == "SYSTEM"
        chain = chains.get(chain_id) if chain_id is not None else None
        # у работы с вариантом: живое задание зоны цепочки без зоны или признака «можно
        # отпустить» семьи на действующую схему (спека §2.16); пауза — не «разбирает», а «ждёт»
        waiting_chain = chain is not None and chain.zone is None and chain.live and paused
        analog_live = (
            not is_system
            and info is not None
            and not paused
            and (
                (chain is not None and chain.zone is None and chain.live)
                or info.family_id in releasable_live
            )
        )
        result[estimate_id].append(
            KpLineRow(
                line=KpLine(
                    position_item_id=item_id,
                    unit_price=unit_price,
                    total=total,
                    weight=weight,
                    key=_line_key(info, variant_id) if info is not None else None,
                    is_system=is_system,
                    in_progress=signal.in_progress or analog_live,
                    waiting_admin=signal.waiting_admin,
                    section_chain_id=chain_id,
                    zone=chain.zone if chain is not None else None,
                    chain_titles=chain.titles if chain is not None else frozenset(),
                    schema_id=info.schema_id if info is not None else None,
                    main_values=info.main_values if info is not None else {},
                    forms=info.forms if info is not None else {},
                    conflicted=_conflicts_of(conflicts, context_id, info.schema_id if info else None),
                    zone_waiting=chain is not None
                    and chain.zone is None
                    and (chain.disputed or chain.stuck or waiting_chain),
                ),
                title=title,
                unit=unit,
                context_id=context_id,
                unit_id=unit_id,
                suggestion_queue=signal.suggestion_queue,
                quantity=weight,
                section_ref=number or key_text,
                item_number=number,
                article_root_id=root_of.get(category_id) if category_id is not None else None,
                family_title=family_title,
                variant_label=info.label if info is not None else None,
                secondary=secondary.get(context_id, ()) if context_id is not None else (),
                category_id=category_id,
                chapter_item_id=chapter_item_id,
                params=(
                    _line_params(
                        info,
                        context_values.get(context_id, {}) if context_id is not None else {},
                        current_secondaries.get(info.family_id, ()),
                    )
                    if info is not None
                    else ()
                ),
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


def catalog_text(value: str | None, source: str, off_list: str | None) -> str:
    """Подпись значения записи каталога по таблице показа (спека параметров варианта §2.2):
    расхождение разделов и текст не из списка проверяются раньше значения."""
    if source == ValueSource.path_conflict.value:
        return "расходится по разделам"
    if source == ValueSource.off_list.value:
        return f"не из списка: «{off_list}»"
    return value or _UNKNOWN_LABEL


def _context_values(db: Session, context_ids: Sequence[int]) -> dict[int, dict[int, _ContextValue]]:
    """Все записи значений (главных и второстепенных) заданных контекстов одним запросом:
    `context_id -> parameter_id -> запись`."""
    ids = sorted(set(context_ids))
    if not ids:
        return {}
    grouped: dict[int, dict[int, _ContextValue]] = defaultdict(dict)
    for context_id, parameter_id, value_id, text, off_list, source, refresh_requested_at in db.execute(
        sa.select(
            ContextParameterValue.context_id,
            ContextParameterValue.parameter_id,
            ContextParameterValue.value_id,
            FamilyParameterValue.value,
            ContextParameterValue.off_list_text,
            ContextParameterValue.source,
            ContextParameterValue.refresh_requested_at,
        )
        .select_from(ContextParameterValue)
        .outerjoin(FamilyParameterValue, FamilyParameterValue.id == ContextParameterValue.value_id)
        .where(ContextParameterValue.context_id.in_(ids))
    ).all():
        grouped[context_id][parameter_id] = _ContextValue(
            value_id=value_id,
            text=text,
            off_list=off_list,
            source=source,
            refreshing=refresh_requested_at is not None,
        )
    return dict(grouped)


def _current_secondaries(db: Session, family_ids: Sequence[int]) -> dict[int, tuple[tuple[int, str], ...]]:
    """Активные второстепенные параметры действующей (`frozen`) схемы каждой семьи по
    `ordinal` одним запросом: `family_id -> ((parameter_id, name), …)`."""
    ids = sorted(set(family_ids))
    if not ids:
        return {}
    grouped: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for family_id, parameter_id, name in db.execute(
        sa.select(FamilyParameterSchema.family_id, FamilyParameter.id, FamilyParameter.name)
        .select_from(FamilyParameter)
        .join(FamilyParameterSchema, FamilyParameterSchema.id == FamilyParameter.schema_id)
        .where(
            FamilyParameterSchema.family_id.in_(ids),
            FamilyParameterSchema.status == SchemaStatus.frozen.value,
            FamilyParameter.role == ParameterRole.secondary.value,
            FamilyParameter.archived_at.is_(None),
        )
        .order_by(FamilyParameterSchema.family_id, FamilyParameter.ordinal)
    ).all():
        grouped[family_id].append((parameter_id, name))
    return {family_id: tuple(pairs) for family_id, pairs in grouped.items()}


def _line_params(
    info: _VariantInfo, values: Mapping[int, _ContextValue], secondaries: Sequence[tuple[int, str]]
) -> tuple[LineParam, ...]:
    """Параметры строки с вариантом: главные схемы варианта, затем второстепенные
    действующей схемы (спека параметров варианта §2.2).

    Вариант на прежней схеме: все главные `old_schema`, записи не смотрятся. Иначе главный без
    записи — `missing`; запись с другим `value_id`, чем у варианта, — `stale` (значение остаётся
    вариантным, запись показывается в `catalog_value`); иначе источник записи как есть.
    `NULL` против `NULL` расхождением не считается.
    """
    on_current = info.schema_id == info.current_schema_id
    result: list[LineParam] = []
    for main in info.mains:
        record = values.get(main.parameter_id) if on_current else None
        composite = on_current and info.forms.get(main.parameter_id, "simple") != "simple"
        if not on_current:
            source: ParamSource = "old_schema"
        elif record is None:
            source = "missing"
        elif record.value_id != main.value_id:
            source = "stale"
        else:
            source = record.source  # type: ignore[assignment]
        steady = on_current and record is not None and source != "stale"
        result.append(
            LineParam(
                parameter_id=main.parameter_id,
                name=main.name,
                role="main",
                value=main.text,
                off_list=record.off_list if steady and record is not None else None,
                composite=composite,
                source=source,
                refreshing=record.refreshing if record is not None else False,
                catalog_value=(
                    catalog_text(record.text, record.source, record.off_list)
                    if source == "stale" and record is not None
                    else None
                ),
            )
        )
    for parameter_id, name in secondaries:
        record = values.get(parameter_id)
        result.append(
            LineParam(
                parameter_id=parameter_id,
                name=name,
                role="secondary",
                value=record.text if record is not None else None,
                off_list=record.off_list if record is not None else None,
                composite=False,
                source=record.source if record is not None else "missing",  # type: ignore[arg-type]
                refreshing=record.refreshing if record is not None else False,
                catalog_value=None,
            )
        )
    return tuple(result)


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


def load_additional_works(db: Session, estimate_ids: Sequence[int]) -> dict[int, list[tuple[int | None, Decimal]]]:
    """Дополнительные работы смет одним запросом: смета -> пары (статья или `None`, валовая сумма).
    Это второй источник `v_category_totals`; строк позиций они не образуют и в разделы сметы не
    входят (у них нет связи с разделом, только исходный текст)."""
    result: dict[int, list[tuple[int | None, Decimal]]] = {estimate_id: [] for estimate_id in estimate_ids}
    if not result:
        return result
    for estimate_id, category_id, amount in db.execute(
        sa.select(Lot.estimate_id, EstimateAdditionalWork.work_category_id, EstimateAdditionalWork.total_amount)
        .select_from(EstimateAdditionalWork)
        .join(Proposal, Proposal.id == EstimateAdditionalWork.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id.in_(list(estimate_ids)))
        .order_by(Lot.estimate_id, EstimateAdditionalWork.id)
    ).all():
        result[estimate_id].append((category_id, amount))
    return result


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


@dataclass(frozen=True)
class CategoryMeta:
    """Статья классификатора на любом уровне дерева."""

    id: int
    parent_id: int | None
    code: str | None
    title: str
    sort_order: int | None


def category_tree(db: Session) -> tuple[dict[int, int], dict[int, CategoryMeta]]:
    """Корень каждой статьи классификатора (корень — сам себе) и описание всех статей.
    Дерево читается одним запросом; цикл в родителях — `ValueError`."""
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
    tree = {
        row[0]: CategoryMeta(id=row[0], parent_id=row[1], code=row[2], title=row[3], sort_order=row[4]) for row in rows
    }
    return root_of, tree


def category_roots(db: Session) -> tuple[dict[int, int], dict[int, ArticleMeta]]:
    """Корень каждой статьи классификатора (корень — сам себе) и описание корней.
    Порядок статей экрана — `sort_order` корня. Дерево читается одним запросом."""
    root_of, tree = category_tree(db)
    meta = {
        category_id: ArticleMeta(id=category_id, code=node.code, title=node.title, sort_order=node.sort_order)
        for category_id, node in tree.items()
        if node.parent_id is None
    }
    return root_of, meta
