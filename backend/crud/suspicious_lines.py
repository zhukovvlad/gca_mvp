"""Загрузчики, индекс через кэш и представления «подозрительных строк договоров» (спека
`2026-11-24-suspicious-lines-design.md` §2 п. 6–7, §3 п. 3–4, §4).

Правила — в чистом ядре `services/suspicious_lines.py`; здесь только SQL и свёртка в ответ.
Модуль НЕ импортирует `crud.our_price` (цикла нет: `our_price → suspicious_lines → line_views`);
общие представления пути и статьи — `crud/line_views.py`.

Деньги в ответе — строки (`format(value, "f")`, как у `DecimalJSONResponse`): цены за единицу и
`ratio` — без округления, сумма строки `NeighborRow.total` — `quantize_money`.

Дополнение к спеке (решения оркестратора R2, R8–R13 журнала): `NeighborRow.gross` — всегда
присутствует, `true`, когда база НДС сметы неизвестна и деньги даны как в смете; `titles` для
`fact_view`/`decision_view` — словарь `{"family": {id: название}, "unit": {id: символ},
"item_title": {id: название}, "item_number": {id: номер}}`; фильтр `contract_id` сужает оба
блока списка, а `contracts` остаётся полным списком договоров с неразобранными; `facts` пункта
неразобранной строки — факты БЕЗ решения, все решения строки лежат в `previous`; у «Разобраны» —
все текущие факты.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from crud.analytics import latest_estimates
from crud.common import DomainError, iso
from crud.line_views import article_ref, path_view
from crud.not_analog import Mark, load_price_error_marks, mark_view
from crud.our_price_load import category_tree, load_history, load_work_history
from models import (
    Contract,
    Estimate,
    Lot,
    NotErrorMark,
    ObjectModel,
    PositionItem,
    PriceErrorMark,
    Proposal,
    UnitOfMeasure,
    User,
    WorkFamily,
)
from money.vat import gross_to_net, quantize_money
from services.context_routing import chapter_steps
from services.status_cache import suspicion_cache
from services.suspicious_lines import (
    Basis,
    Fact,
    NeighborRow,
    OutlierBasis,
    OutlierFact,
    SuspicionIndex,
    TwinBasis,
    TwinFact,
    build_suspicion_index,
    state_of,
)

#: Окно соседей в пункте списка: строк без глав до и после (спека §4 п. 2).
NEIGHBOR_WINDOW = 3

Titles = Mapping[str, Mapping[int, str]]


def _s(value: Decimal | None) -> str | None:
    """Деньги и множитель в ответ: `None` — `null`; конечное — десятичная строка без экспоненты,
    нечисловое — как хранится (`NaN`, `Infinity`, `-Infinity`)."""
    return None if value is None else format(value, "f")


# ---------------------------------------------------------------------------
#  Соседи по смете
# ---------------------------------------------------------------------------


def load_estimate_neighbors(db: Session, estimate_ids: Collection[int]) -> list[NeighborRow]:
    """Строки смет без глав с номером места: один запрос, `row_number()` по `position_items.id`
    внутри сметы (глава места не занимает: окно считается после отбора). Пустой вход — без запроса."""
    ids = sorted(set(estimate_ids))
    if not ids:
        return []
    place = sa.func.row_number().over(partition_by=Estimate.id, order_by=PositionItem.id)
    rows = db.execute(
        sa.select(PositionItem.id, Estimate.id, place, PositionItem.unit_id, PositionItem.unit_cost_total)
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .join(Estimate, Estimate.id == Lot.estimate_id)
        .where(Estimate.id.in_(ids), PositionItem.is_chapter.is_(False))
    ).all()
    return [
        NeighborRow(position_item_id=row[0], estimate_id=row[1], place=row[2], unit_id=row[3], unit_cost_total=row[4])
        for row in rows
    ]


# ---------------------------------------------------------------------------
#  Подробности строк
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LineDetails:
    position_item_id: int
    item_number: str | None
    title: str
    unit: str | None  # единица строки сметы, не каталожная (R10)
    quantity: Decimal | None  # coalesce(suggested_quantity, quantity)
    unit_cost_total: Decimal | None
    unit_cost_materials: Decimal | None
    unit_cost_works: Decimal | None
    total_cost_total: Decimal | None
    vat_base: Decimal | None
    category_id: int | None  # статья раздела строки — как у аналога панели
    chapter_item_id: int | None


def load_line_details(db: Session, position_item_ids: Collection[int]) -> dict[int, LineDetails]:
    """Подписи и деньги строк одним запросом. Статья — статья раздела, в котором лежит строка, как у
    аналога панели (`load_analog_details`)."""
    ids = sorted(set(position_item_ids))
    if not ids:
        return {}
    chapter = aliased(PositionItem)
    rows = db.execute(
        sa.select(
            PositionItem.id,
            PositionItem.item_number_in_proposal,
            PositionItem.job_title_in_proposal,
            UnitOfMeasure.symbol,
            sa.func.coalesce(PositionItem.suggested_quantity, PositionItem.quantity),
            PositionItem.unit_cost_total,
            PositionItem.unit_cost_materials,
            PositionItem.unit_cost_works,
            PositionItem.total_cost_total,
            sa.func.coalesce(Estimate.vat_rate_base_override, Proposal.vat_rate),
            chapter.work_category_id,
            PositionItem.chapter_item_id,
        )
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .join(Estimate, Estimate.id == Lot.estimate_id)
        .outerjoin(UnitOfMeasure, UnitOfMeasure.id == PositionItem.unit_id)
        .outerjoin(chapter, chapter.id == PositionItem.chapter_item_id)
        .where(PositionItem.id.in_(ids))
    ).all()
    return {row[0]: LineDetails(*row) for row in rows}


def _neighbor_money(value: Decimal | None, vat_base: Decimal | None, *, total: bool = False) -> str | None:
    """Деньги соседа: конечное — нетто по базе сметы (при неизвестной базе — как в смете), в том числе
    0 и отрицательное; нечисловое — как хранится; пусто — `None`. Сумма строки — до копеек."""
    if value is None:
        return None
    if not value.is_finite():
        return _s(value)
    shown = value if vat_base is None else gross_to_net(value, vat_base)
    return _s(quantize_money(shown) if total else shown)


def neighbor_row_view(details: LineDetails, *, is_self: bool, is_twin: bool) -> dict:
    """`NeighborRow` спеки §4 п. 2 (+ `gross`, R2: `true` — база НДС сметы неизвестна)."""
    base = details.vat_base
    return {
        "position_item_id": details.position_item_id,
        "item_number": details.item_number,
        "title": details.title,
        "unit": details.unit,
        "quantity": _s(details.quantity),
        "unit_price": _neighbor_money(details.unit_cost_total, base),
        "materials": _neighbor_money(details.unit_cost_materials, base),
        "works": _neighbor_money(details.unit_cost_works, base),
        "total": _neighbor_money(details.total_cost_total, base, total=True),
        "gross": base is None,
        "is_self": is_self,
        "is_twin": is_twin,
    }


# ---------------------------------------------------------------------------
#  Индекс через кэш
# ---------------------------------------------------------------------------


def _build_index(db: Session) -> SuspicionIndex:
    contracts, _ = load_history(db, exclude_object_id=None)
    estimate_of = {contract.contract_id: contract.estimate_id for contract in contracts}
    lines, _ = load_work_history(db, estimate_of=estimate_of)
    neighbors = load_estimate_neighbors(db, estimate_of.values())
    return build_suspicion_index(lines, neighbors)


def suspicion_index(db: Session) -> SuspicionIndex:
    """Индекс подозрений по всем договорам через `suspicion_cache` (срок `SUSPICION_CACHE_TTL_S`,
    сбрасывает закрытый список мест спеки §1 п. 3)."""
    return suspicion_cache.get(lambda: _build_index(db))


# ---------------------------------------------------------------------------
#  Решения «не ошибка»
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NotErrorRecord:
    basis: Basis
    reason: str
    set_by_id: int
    set_by_email: str
    set_at: datetime


def _basis_of_row(row: NotErrorMark) -> Basis:
    if row.kind == "outlier":
        return OutlierBasis(family_id=row.family_id, unit_id=row.unit_id, direction=row.direction)
    return TwinBasis(neighbor_item_id=row.neighbor_item_id)


def load_not_error(db: Session, position_item_ids: Collection[int] | None) -> dict[int, list[NotErrorRecord]]:
    """Решения «не ошибка» по строкам, новые сверху (`set_at`, затем `id`); `None` — по всем строкам.
    Пустой набор — без запроса."""
    query = (
        sa.select(NotErrorMark, User.email)
        .join(User, User.id == NotErrorMark.set_by)
        .order_by(NotErrorMark.set_at.desc(), NotErrorMark.id.desc())
    )
    if position_item_ids is not None:
        ids = sorted(set(position_item_ids))
        if not ids:
            return {}
        query = query.where(NotErrorMark.position_item_id.in_(ids))
    result: dict[int, list[NotErrorRecord]] = {}
    for row, email in db.execute(query).all():
        result.setdefault(row.position_item_id, []).append(
            NotErrorRecord(
                basis=_basis_of_row(row),
                reason=row.reason,
                set_by_id=row.set_by,
                set_by_email=email,
                set_at=row.set_at,
            )
        )
    return result


# ---------------------------------------------------------------------------
#  Строки решений и цена
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecidedLine:
    """Строка договора с её ценой и сметой — независимо от индекса (строка вне пула тоже)."""

    position_item_id: int
    contract_id: int
    latest: bool  # строка последней сметы договора
    unit_cost_total: Decimal | None
    vat_base: Decimal | None


def _lines_of(db: Session, where) -> list[DecidedLine]:
    latest = latest_estimates()
    rows = db.execute(
        sa.select(
            PositionItem.id,
            Estimate.contract_id,
            latest.c.estimate_id.is_not(None),
            PositionItem.unit_cost_total,
            sa.func.coalesce(Estimate.vat_rate_base_override, Proposal.vat_rate),
        )
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .join(Estimate, Estimate.id == Lot.estimate_id)
        .outerjoin(latest, latest.c.estimate_id == Estimate.id)
        .where(Estimate.contract_id.is_not(None), where)
        .order_by(PositionItem.id)
    ).all()
    return [
        DecidedLine(position_item_id=row[0], contract_id=row[1], latest=row[2], unit_cost_total=row[3], vat_base=row[4])
        for row in rows
    ]


def load_decided_lines(db: Session) -> list[DecidedLine]:
    """Все строки с «ошибкой в цене» или решением «не ошибка» — одним запросом, независимо от пула
    кандидатов (спека §2 п. 7): комплект, строка прежней сметы, строка без варианта, цены или базы НДС."""
    return _lines_of(
        db,
        sa.or_(
            PositionItem.id.in_(sa.select(PriceErrorMark.position_item_id)),
            PositionItem.id.in_(sa.select(NotErrorMark.position_item_id)),
        ),
    )


def price_head(line: DecidedLine, net: Decimal | None) -> dict:
    """Цена `LineHead` (спека §4 п. 2): `net`, `gross`, `raw`, `price_note`.

    `net` — нетто индекса (кандидат): примечания нет. Иначе по порядку таблицы спеки: пусто или 0 —
    `no_price`; нечисловое — `not_finite` и `raw` как хранится; меньше нуля — `negative` и `raw`;
    база НДС неизвестна — `vat_unknown` и `gross`; иначе — нетто по базе сметы."""
    head: dict = {"net": None, "gross": None, "raw": None, "price_note": None}
    if net is not None:
        head["net"] = _s(net)
        return head
    value = line.unit_cost_total
    if value is None or (value.is_finite() and value == 0):
        head["price_note"] = "no_price"
    elif not value.is_finite():
        head["price_note"] = "not_finite"
        head["raw"] = _s(value)
    elif value < 0:
        head["price_note"] = "negative"
        head["raw"] = _s(value)
    elif line.vat_base is None:
        head["price_note"] = "vat_unknown"
        head["gross"] = _s(value)
    else:
        head["net"] = _s(gross_to_net(value, line.vat_base))
    return head


# ---------------------------------------------------------------------------
#  Представления фактов и решений
# ---------------------------------------------------------------------------


def _basis_key(basis: Basis) -> dict:
    if isinstance(basis, OutlierBasis):
        return {
            "kind": "outlier",
            "family_id": basis.family_id,
            "unit_id": basis.unit_id,
            "direction": basis.direction,
        }
    return {"kind": "twin", "neighbor_item_id": basis.neighbor_item_id}


def fact_view(
    index: SuspicionIndex, fact: Fact, details: Mapping[int, LineDetails], titles: Titles
) -> dict:
    """`FactView` спеки §4 п. 2. У двойника сосед берётся из `details` (в том числе сосед вне пула)."""
    if isinstance(fact, OutlierFact):
        basis = fact.basis
        group = index.groups.get((basis.family_id, basis.unit_id))
        return {
            "basis": _basis_key(basis),
            "kind": "outlier",
            "direction": basis.direction,
            "ratio": _s(fact.ratio),
            "usual": _s(fact.usual),
            "family_title": titles.get("family", {}).get(basis.family_id),
            "unit": titles.get("unit", {}).get(basis.unit_id),
            "contracts": 0 if group is None else len(group.contracts),
        }
    assert isinstance(fact, TwinFact)
    neighbor_id = fact.basis.neighbor_item_id
    return {
        "basis": _basis_key(fact.basis),
        "kind": "twin",
        "neighbor": neighbor_row_view(details[neighbor_id], is_self=False, is_twin=True),
    }


def decision_view(record: NotErrorRecord, titles: Titles) -> dict:
    """`NotErrorDecision` спеки §4 п. 1."""
    basis = record.basis
    view = _basis_key(basis)
    if isinstance(basis, OutlierBasis):
        view["family_title"] = titles.get("family", {}).get(basis.family_id)
        view["unit"] = titles.get("unit", {}).get(basis.unit_id)
    else:
        view["neighbor"] = {
            "item_number": titles.get("item_number", {}).get(basis.neighbor_item_id),
            "title": titles.get("item_title", {}).get(basis.neighbor_item_id),
        }
    return {
        "basis": view,
        "reason": record.reason,
        "set_by": {"id": record.set_by_id, "email": record.set_by_email},
        "set_at": iso(record.set_at),
    }


# ---------------------------------------------------------------------------
#  Состояния строк
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Scene:
    index: SuspicionIndex
    lines: Mapping[int, DecidedLine]
    states: Mapping[int, str]
    records: Mapping[int, list[NotErrorRecord]]
    price_errors: Mapping[int, Mark]


def _scene(db: Session) -> _Scene:
    """Индекс, решения и состояние каждой строки списка. Строки решений читаются отдельно от пула."""
    index = suspicion_index(db)
    lines = {line.position_item_id: line for line in load_decided_lines(db)}
    price_errors = load_price_error_marks(db, lines)
    records = load_not_error(db, None)
    extra = [i for i in index.facts if i not in lines]
    if extra:
        for line in _lines_of(db, PositionItem.id.in_(extra)):
            lines[line.position_item_id] = line
    states: dict[int, str] = {}
    for item_id in lines:
        state = state_of(
            index.facts.get(item_id, ()),
            {record.basis for record in records.get(item_id, ())},
            item_id in price_errors,
        )
        if state is not None:
            states[item_id] = state
    return _Scene(index=index, lines=lines, states=states, records=records, price_errors=price_errors)


def suspicious_counts(db: Session) -> dict:
    """Счётчики (спека §3 п. 4): только не разобранные; договоры с нулём не перечисляются."""
    scene = _scene(db)
    by_contract = Counter(scene.lines[i].contract_id for i, state in scene.states.items() if state == "unresolved")
    return {"total": sum(by_contract.values()), "by_contract": dict(sorted(by_contract.items()))}


# ---------------------------------------------------------------------------
#  Список
# ---------------------------------------------------------------------------


def _contract_heads(db: Session, contract_ids: Collection[int]) -> dict[int, tuple[str, str]]:
    ids = sorted(set(contract_ids))
    if not ids:
        return {}
    return {
        cid: (number, title)
        for cid, number, title in db.execute(
            sa.select(Contract.id, Contract.contract_number, ObjectModel.title)
            .join(ObjectModel, ObjectModel.id == Contract.object_id)
            .where(Contract.id.in_(ids))
        ).all()
    }


def _titles(db: Session, *, families: Collection[int], units: Collection[int], details: Mapping[int, LineDetails]) -> dict:
    family_ids, unit_ids = sorted(set(families)), sorted(set(units))
    return {
        "family": (
            dict(db.execute(sa.select(WorkFamily.id, WorkFamily.title).where(WorkFamily.id.in_(family_ids))).all())
            if family_ids
            else {}
        ),
        "unit": (
            dict(
                db.execute(
                    sa.select(UnitOfMeasure.id, UnitOfMeasure.symbol).where(UnitOfMeasure.id.in_(unit_ids))
                ).all()
            )
            if unit_ids
            else {}
        ),
        "item_title": {i: d.title for i, d in details.items()},
        "item_number": {i: d.item_number for i, d in details.items() if d.item_number is not None},
    }


def _bases(facts, records) -> list[Basis]:
    return [fact.basis for fact in facts] + [record.basis for record in records]


def suspicious_list(db: Session, *, contract_id: int | None) -> dict:
    """Ответ списка (спека §4 п. 2). `unresolved` — по договору, затем по месту в смете; `resolved` — по
    времени последнего решения строки, новые сверху (при равенстве — по `position_item_id`); фильтр
    сужает оба блока, `contracts` — все договоры с не разобранными (для самого фильтра)."""
    if contract_id is not None and db.get(Contract, contract_id) is None:
        raise DomainError(404, f"Договор {contract_id} не найден.")
    scene = _scene(db)
    index, lines, states = scene.index, scene.lines, scene.states

    unresolved_all = [i for i, state in states.items() if state == "unresolved"]
    place_of: dict[int, tuple[int, int]] = {}  # строка -> (смета, позиция в её строках)
    wanted = set(unresolved_all)
    for estimate_id, rows in index.rows.items():
        for position, row in enumerate(rows):
            if row.position_item_id in wanted:
                place_of[row.position_item_id] = (estimate_id, position)
    counts = Counter(lines[i].contract_id for i in unresolved_all)

    def shown(item_id: int) -> bool:
        return contract_id is None or lines[item_id].contract_id == contract_id

    unresolved_ids = sorted(
        (i for i in unresolved_all if shown(i)),
        key=lambda i: (lines[i].contract_id, place_of[i][1] if i in place_of else 0, i),
    )
    resolved_ids = [i for i, state in states.items() if state == "resolved" and shown(i)]

    windows: dict[int, tuple[NeighborRow, ...]] = {}
    for item_id in unresolved_ids:
        estimate_id, position = place_of[item_id]
        windows[item_id] = index.rows[estimate_id][max(0, position - NEIGHBOR_WINDOW) : position + NEIGHBOR_WINDOW + 1]

    listed = unresolved_ids + resolved_ids
    twin_ids = {
        basis.neighbor_item_id
        for i in listed
        for basis in _bases(index.facts.get(i, ()), scene.records.get(i, ()))
        if isinstance(basis, TwinBasis)
    }
    detail_ids = set(listed) | twin_ids | {row.position_item_id for rows in windows.values() for row in rows}
    details = load_line_details(db, detail_ids)

    families: set[int] = set()
    units: set[int] = set()
    for i in listed:
        for basis in _bases(index.facts.get(i, ()), scene.records.get(i, ())):
            if isinstance(basis, OutlierBasis):
                families.add(basis.family_id)
                units.add(basis.unit_id)
    for i in unresolved_ids:
        key = index.group_of.get(i)
        if key is not None:
            families.add(key[0])
            units.add(key[1])
    titles = _titles(db, families=families, units=units, details=details)

    group_contracts = {
        c.contract_id
        for i in unresolved_ids
        if (key := index.group_of.get(i)) is not None and key in index.groups
        for c in index.groups[key].contracts
    }
    heads = _contract_heads(db, {lines[i].contract_id for i in listed} | group_contracts | set(counts))

    steps = chapter_steps(db, {details[i].chapter_item_id for i in unresolved_ids if i in details} - {None})
    _, tree = category_tree(db) if unresolved_ids else (None, {})

    def line_head(item_id: int) -> dict:
        line, detail = lines[item_id], details[item_id]
        number, object_title = heads[line.contract_id]
        return {
            "position_item_id": item_id,
            "contract": {"id": line.contract_id, "number": number, "object_title": object_title},
            "item_number": detail.item_number,
            "title": detail.title,
            "unit": detail.unit,
            "latest": line.latest,
            **price_head(line, index.net.get(item_id)),
        }

    def prices_view(item_id: int) -> dict | None:
        key = index.group_of.get(item_id)
        group = None if key is None else index.groups.get(key)
        if group is None or group.usual is None:
            return None
        return {
            "family_title": titles["family"].get(key[0]),
            "unit": titles["unit"].get(key[1]),
            "usual": _s(group.usual),
            "contracts": [
                {
                    "contract_id": c.contract_id,
                    "object_title": heads[c.contract_id][1],
                    "lines": c.lines,
                    "price": _s(c.price),
                    "low": _s(c.low),
                    "high": _s(c.high),
                    "is_own": c.contract_id == lines[item_id].contract_id,
                }
                for c in group.contracts
            ],
        }

    unresolved = []
    for item_id in unresolved_ids:
        facts = index.facts.get(item_id, ())
        records = scene.records.get(item_id, [])
        decided = {record.basis for record in records}
        twins = {f.basis.neighbor_item_id for f in facts if isinstance(f, TwinFact)}
        detail = details[item_id]
        unresolved.append(
            {
                **line_head(item_id),
                "facts": [fact_view(index, f, details, titles) for f in facts if f.basis not in decided],
                "previous": [decision_view(record, titles) for record in records],
                "neighbors": [
                    neighbor_row_view(
                        details[row.position_item_id],
                        is_self=row.position_item_id == item_id,
                        is_twin=row.position_item_id in twins,
                    )
                    for row in windows[item_id]
                ],
                "where": {
                    "article": article_ref(tree, detail.category_id),
                    "chapter_path": path_view(steps.get(detail.chapter_item_id, ())),
                },
                "prices": prices_view(item_id),
            }
        )

    def decided_at(item_id: int) -> datetime:
        stamps = [record.set_at for record in scene.records.get(item_id, ())]
        if item_id in scene.price_errors:
            stamps.append(scene.price_errors[item_id].set_at)
        return max(stamps)

    resolved = []
    for item_id in sorted(sorted(resolved_ids), key=decided_at, reverse=True):
        mark = scene.price_errors.get(item_id)
        resolved.append(
            {
                **line_head(item_id),
                "price_error": None if mark is None else mark_view(mark),
                "not_error": [decision_view(record, titles) for record in scene.records.get(item_id, ())],
                "facts": [fact_view(index, f, details, titles) for f in index.facts.get(item_id, ())],
            }
        )

    return {
        "unresolved": unresolved,
        "resolved": resolved,
        "contracts": [
            {"contract_id": cid, "number": heads[cid][0], "object_title": heads[cid][1], "unresolved": counts[cid]}
            for cid in sorted(counts)
        ],
    }
