"""Стартовый дашборд: расчёт обзорной главной (спека 2026-08-16).

**Охватов ДВА, и это несущая конструкция, а не деталь** (спека §2.5). Договорный
охват защищает ДЕНЬГИ, объектный — РЕЙТИНГ и диаграмму, у каждого свой строгий
приоритет причин. Смешивать их в один фильтр нельзя: объект с несколькими
договорами ГП выпадает ТОЛЬКО из рейтинга, а деньги его договоров остаются в
ИТОГО — они настоящие.

**Валовая семантика.** Главная — управленческий обзор, а не расчётный документ:
суммы идут С НДС, каждый договор в СВОЕЙ действующей ставке (спека §2.2,
ревизия `AGENTS.md` §10). Ставка считается `money.vat.effective_display_rate` —
той же функцией, что паспорт проекта, паспорт Ф6 и свод по договору. Своей
формулы здесь нет намеренно: буква решения 3 макета (`target` иначе
`proposals.vat_rate`) теряет договор, у которого ставка заявлена человеком в
`estimates.vat_rate_base_override`, — на стенде это 2 договора из 6 и 24,6 %
валовой суммы (спека §2.3).

**Пересчёт — построчно, ДО накопления** (спека §2.6). С миграции 0012
`v_category_totals` группируется ещё и по `proposal_id`/`vat_rate_base`, то есть
у сметы с несколькими предложениями приходит несколько строк с РАЗНЫМИ базами.
Сложить сырые строки и пересчитать сумму один раз нельзя — в накопленном
смешаны базы. То же правило и по той же причине несёт
`crud/project_passport.py::_direct_totals`.

**Число запросов фиксировано и не растёт с числом договоров и смет.** Все
выборки — по всей базе целиком, свёртка идёт в Python. Соблазн позвать
`effective_display_rate` в цикле по сметам, дозапрашивая предложения на каждую,
дал бы N+1 ровно на той странице, которая открывается чаще всех.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.common import iso
from crud.project_passport import CATEGORY_TOTALS
from models import Contract, Contractor, Estimate, Lot, ObjectModel, Proposal, RateClass
from money.vat import effective_display_rate, restate_gross

# ---------------------------------------------------------------------------
#  Договорная лестница охвата (спека §2.5)
# ---------------------------------------------------------------------------

CONTRACT_REASON_NO_ESTIMATE = "no_estimate"
CONTRACT_REASON_AMENDMENT = "amendment"
CONTRACT_REASON_NO_RATE = "no_rate"
CONTRACT_REASON_INCOMPLETE = "incomplete"

#: Порядок — ЭТО И ЕСТЬ ПРИОРИТЕТ, сверху вниз (спека §2.5). Договор считается
#: РОВНО ОДИН РАЗ, в первой подходящей причине: без строгого приоритета договор
#: с ДС, у которого вдобавок неизвестна ставка, попал бы в два счётчика, и они
#: перестали бы быть разбиением.
CONTRACT_REASONS: tuple[str, ...] = (
    CONTRACT_REASON_NO_ESTIMATE,
    CONTRACT_REASON_AMENDMENT,
    CONTRACT_REASON_NO_RATE,
    CONTRACT_REASON_INCOMPLETE,
)


@dataclass(frozen=True)
class ContractMoney:
    """Место одного договора в договорной лестнице и его валовая сумма.

    `reason is None` — договор УЧТЁН; тогда `amount` и `display_rate` заданы.
    У исключённого договора `amount` всегда `None`: частичной суммы на главной
    не существует (спека §2.6 — пометка «частичная» работает на карточке, но
    ИТОГО это сумма, и место для пометки у одного числа отсутствует).
    """

    contract_id: int
    object_id: int
    rate_class_id: int
    reason: str | None
    display_rate: Decimal | None
    amount: Decimal | None


@dataclass(frozen=True)
class _EstimateRow:
    id: int
    contract_id: int
    amendment_no: int | None
    vat_rate_target: Decimal | None
    vat_rate_base_override: Decimal | None


def _contract_estimates(db: Session) -> dict[int, list[_EstimateRow]]:
    rows = db.execute(
        sa.select(
            Estimate.id,
            Estimate.contract_id,
            Estimate.amendment_no,
            Estimate.vat_rate_target,
            Estimate.vat_rate_base_override,
        )
    ).all()
    by_contract: dict[int, list[_EstimateRow]] = {}
    for row in rows:
        by_contract.setdefault(row.contract_id, []).append(_EstimateRow(*row))
    return by_contract


def _declared_rates(db: Session) -> dict[int, list[Decimal | None]]:
    """Заявленные ставки предложений по сметам — ОДНИМ запросом на всю базу.

    Список отдаётся сырым, без свёртки в «единогласную»: правило единогласия
    живёт внутри `effective_display_rate`, и второй его реализации здесь быть не
    должно. Смета без предложений отсутствует в словаре — пустой список даёт
    `None`, что и требуется.
    """
    rows = db.execute(
        sa.select(Lot.estimate_id, Proposal.vat_rate)
        .select_from(Proposal)
        .join(Lot, Lot.id == Proposal.lot_id)
    ).all()
    by_estimate: dict[int, list[Decimal | None]] = {}
    for estimate_id, vat_rate in rows:
        by_estimate.setdefault(estimate_id, []).append(vat_rate)
    return by_estimate


@dataclass(frozen=True)
class _TotalsRow:
    vat_rate_base: Decimal | None
    amount: Decimal | None
    row_count: int
    rows_with_amount: int
    rows_not_finite: int


def _category_totals(db: Session) -> dict[int, list[_TotalsRow]]:
    rows = db.execute(
        sa.select(
            CATEGORY_TOTALS.c.estimate_id,
            CATEGORY_TOTALS.c.vat_rate_base,
            CATEGORY_TOTALS.c.amount,
            CATEGORY_TOTALS.c.row_count,
            CATEGORY_TOTALS.c.rows_with_amount,
            CATEGORY_TOTALS.c.rows_not_finite,
        )
    ).all()
    by_estimate: dict[int, list[_TotalsRow]] = {}
    for row in rows:
        by_estimate.setdefault(row.estimate_id, []).append(
            _TotalsRow(
                vat_rate_base=row.vat_rate_base,
                amount=row.amount,
                row_count=row.row_count,
                rows_with_amount=row.rows_with_amount,
                rows_not_finite=row.rows_not_finite,
            )
        )
    return by_estimate


def _is_incomplete(rows: Sequence[_TotalsRow]) -> bool:
    """Три условия §2.6. Первое обязательно и из второго НЕ выводится.

    Позиционная ветвь VIEW строится `WHERE pi.is_chapter = false`, поэтому смета
    без единой нераздельной позиции (пустая либо состоящая только из разделов) не
    даёт ни одной строки. `SUM` по пустому множеству равен `NULL`, а не нулю:
    сравнение `rows_with_amount < row_count` даёт `NULL`, то есть ЛОЖЬ, и договор
    без единой суммы прошёл бы как полный.
    """
    row_count = sum(row.row_count for row in rows)
    if row_count == 0:
        return True
    if sum(row.rows_with_amount for row in rows) < row_count:
        return True
    return sum(row.rows_not_finite for row in rows) > 0


def _restated_total(rows: Iterable[_TotalsRow], display_rate: Decimal | None) -> Decimal:
    """Валовая сумма в ставке показа: `restate_gross` НА КАЖДУЮ строку, и только
    результат складывается (спека §2.6). Порядок обязателен — у сметы с
    несколькими предложениями базы у строк разные, и приведение накопленной
    суммы к одной ставке дало бы неверное число. На одном предложении (все
    сегодняшние фикстуры и весь стенд) ошибка порядка НЕВИДИМА."""
    total = Decimal(0)
    for row in rows:
        restated = restate_gross(row.amount, row.vat_rate_base, display_rate)
        if restated.amount is not None:
            total += restated.amount
    return total


def contract_layer(db: Session) -> list[ContractMoney]:
    """Договорный слой целиком: по строке на КАЖДЫЙ договор базы.

    Исключённые договоры остаются в списке со своей причиной — иначе охват
    пришлось бы считать вторым проходом по тем же данным, и два счёта одного
    множества разъехались бы при первой правке.

    Запросов ровно четыре, и это число не зависит ни от числа договоров, ни от
    числа смет.
    """
    contracts = db.execute(
        sa.select(Contract.id, Contract.object_id, Contract.rate_class_id).order_by(Contract.id)
    ).all()
    estimates_by_contract = _contract_estimates(db)
    declared_by_estimate = _declared_rates(db)
    totals_by_estimate = _category_totals(db)

    layer: list[ContractMoney] = []
    for contract_id, object_id, rate_class_id in contracts:
        estimates = estimates_by_contract.get(contract_id, [])

        reason: str | None = None
        display_rate: Decimal | None = None
        amount: Decimal | None = None

        if not estimates:
            reason = CONTRACT_REASON_NO_ESTIMATE
        elif any(estimate.amendment_no is not None for estimate in estimates):
            # Признак — НАЛИЧИЕ сметы с `amendment_no NOT NULL`, а не
            # `COUNT(estimates) > 1` (спека §2.4): загрузка не требует исходной
            # сметы, поэтому договор может состоять из ОДНОГО допсоглашения —
            # счёт по числу смет принял бы его за обычный и посчитал бы ДС за
            # весь договор. Это худший из исходов: тихая неверная цифра вместо
            # честного исключения.
            reason = CONTRACT_REASON_AMENDMENT
        else:
            estimate = estimates[0]
            display_rate = effective_display_rate(
                estimate.vat_rate_target,
                estimate.vat_rate_base_override,
                declared_by_estimate.get(estimate.id, []),
            )
            totals = totals_by_estimate.get(estimate.id, [])
            if display_rate is None:
                reason = CONTRACT_REASON_NO_RATE
            elif _is_incomplete(totals):
                reason = CONTRACT_REASON_INCOMPLETE
            else:
                amount = _restated_total(totals, display_rate)

        if reason is not None:
            # Исключённый договор не несёт ни суммы, ни ставки показа: показать
            # их значило бы дать числу, которое никуда не входит, вид учтённого.
            display_rate = None
            amount = None

        layer.append(
            ContractMoney(
                contract_id=contract_id,
                object_id=object_id,
                rate_class_id=rate_class_id,
                reason=reason,
                display_rate=display_rate,
                amount=amount,
            )
        )
    return layer


def money_total(rows: Sequence[ContractMoney]) -> Decimal:
    """ИТОГО: сумма УЧТЁННЫХ договоров. Складываются суммы, измеренные каждая в
    своей действующей ставке, — осознанная ревизия `AGENTS.md` §10 (спека §2.2),
    поэтому поверхность обязана нести подпись о величине, а не тултип."""
    return sum((row.amount for row in rows if row.amount is not None), start=Decimal(0))


def contract_coverage(rows: Sequence[ContractMoney]) -> dict:
    """Охват денежных итогов: сколько договоров учтено из скольких и почему нет.

    Все четыре причины присутствуют всегда, в том числе нулевые: разбиение
    читается по составу ключей, а исчезающая причина выглядела бы как забытая.
    """
    reasons = {reason: 0 for reason in CONTRACT_REASONS}
    counted = 0
    for row in rows:
        if row.reason is None:
            counted += 1
        else:
            reasons[row.reason] += 1
    return {"total": len(rows), "counted": counted, "reasons": reasons}


# ---------------------------------------------------------------------------
#  Объектная лестница охвата (спека §2.5) — ВТОРАЯ, не та же самая
# ---------------------------------------------------------------------------
#
# Объектный охват защищает РЕЙТИНГ и ДИАГРАММУ, договорный — ДЕНЬГИ, и места
# исключения у них разные. Объект с несколькими договорами ГП выпадает ТОЛЬКО из
# рейтинга: рейтинг устроен как «один объект — один договор», и объект с двумя в
# нём непредставим, — а деньги его договоров настоящие и из ИТОГО выпадать не
# должны. Один общий фильтр на оба охвата — самая дорогая из возможных здесь
# ошибок, потому что снаружи она выглядит как аккуратность.

OBJECT_REASON_MANY_CONTRACTS = "many_contracts"
OBJECT_REASON_NO_COUNTED_CONTRACT = "no_counted_contract"

#: Порядок — приоритет, сверху вниз, как и у договорной лестницы.
OBJECT_REASONS: tuple[str, ...] = (
    OBJECT_REASON_MANY_CONTRACTS,
    OBJECT_REASON_NO_COUNTED_CONTRACT,
)

#: Сколько объектов показывает рейтинг (решение 8 макета).
RANKING_LIMIT = 10


@dataclass(frozen=True)
class ObjectMoney:
    """Объект в рейтинге: место в объектной лестнице, деньги его единственного
    учтённого договора и удельная стоимость.

    Класс берётся с ДОГОВОРА, а не с объекта: `contracts.rate_class_id` — снимок
    на момент заключения и авторитетен (`AGENTS.md` §4), тогда как
    `objects.rate_class_id` лишь значение по умолчанию для новых договоров.
    """

    object_id: int
    title: str
    reason: str | None
    amount: Decimal | None
    area_total_sp: Decimal | None
    area_useful_sp: Decimal | None
    per_sqm: Decimal | None
    rate_class_id: int | None
    rate_class_title: str | None
    contract_id: int | None
    contract_number: str | None
    signed_date: dt.date | None
    contractor_title: str | None
    display_rate: Decimal | None


def _per_sqm(amount: Decimal | None, area_total: Decimal | None) -> Decimal | None:
    """Удельная стоимость. Знаменатель — `area_total_sp`, то есть НАДЗЕМНАЯ ПЛЮС
    ПОДЗЕМНАЯ: полезная площадь (миграция 0013) в общую не входит и знаменатель
    не меняет — фича полезной площади его нигде не трогала. Деления на ноль быть
    не может: `CHECK` Ф5 держит `area_total_sp` строго положительной, если она
    вообще задана.
    """
    if amount is None or area_total is None:
        return None
    return amount / area_total


def _contract_meta(db: Session) -> dict[int, dict]:
    """Карточка договора для рейтинга — ОДНИМ запросом на всю базу."""
    rows = db.execute(
        sa.select(
            Contract.id,
            Contract.contract_number,
            Contract.signed_date,
            Contractor.title,
            RateClass.id,
            RateClass.title,
        )
        .join(Contractor, Contractor.id == Contract.contractor_id)
        .join(RateClass, RateClass.id == Contract.rate_class_id)
    ).all()
    return {
        row[0]: {
            "contract_number": row[1],
            "signed_date": row[2],
            "contractor_title": row[3],
            "rate_class_id": row[4],
            "rate_class_title": row[5],
        }
        for row in rows
    }


def object_layer(db: Session, contracts: Sequence[ContractMoney]) -> list[ObjectMoney]:
    """Объектный слой целиком: по строке на КАЖДЫЙ объект базы, со своей причиной.

    Договорный слой приходит ГОТОВЫМ, а не пересчитывается: «учтённость»
    договора — понятие ДОГОВОРНОЙ лестницы, и второй её реализации здесь быть не
    должно.
    """
    rows = db.execute(
        sa.select(
            ObjectModel.id,
            ObjectModel.title,
            ObjectModel.area_total_sp,
            ObjectModel.area_useful_sp,
        ).order_by(ObjectModel.id)
    ).all()

    by_object: dict[int, list[ContractMoney]] = {}
    for contract in contracts:
        by_object.setdefault(contract.object_id, []).append(contract)

    meta = _contract_meta(db)

    layer: list[ObjectMoney] = []
    for object_id, title, area_total_sp, area_useful_sp in rows:
        object_contracts = by_object.get(object_id, [])
        counted = [row for row in object_contracts if row.reason is None]

        reason: str | None = None
        if len(object_contracts) > 1:
            # Признак — `COUNT(contracts) > 1`, БЕЗ слова «действующих»: у
            # `Contract` нет ни статуса, ни признака архивности, поэтому
            # «действующий» в схеме не определён, и выдумывать ему смысл здесь
            # нельзя (спека §2.5, граница §4).
            reason = OBJECT_REASON_MANY_CONTRACTS
        elif not counted:
            reason = OBJECT_REASON_NO_COUNTED_CONTRACT

        amount = per_sqm = display_rate = None
        contract_id = contract_number = contractor_title = signed_date = None
        rate_class_id = rate_class_title = None
        if reason is None:
            contract = counted[0]
            amount = contract.amount
            per_sqm = _per_sqm(amount, area_total_sp)
            contract_id = contract.contract_id
            display_rate = contract.display_rate
            card = meta.get(contract.contract_id)
            if card is not None:
                contract_number = card["contract_number"]
                signed_date = card["signed_date"]
                contractor_title = card["contractor_title"]
                rate_class_id = card["rate_class_id"]
                rate_class_title = card["rate_class_title"]

        layer.append(
            ObjectMoney(
                object_id=object_id,
                title=title,
                reason=reason,
                amount=amount,
                area_total_sp=area_total_sp,
                area_useful_sp=area_useful_sp,
                per_sqm=per_sqm,
                rate_class_id=rate_class_id,
                rate_class_title=rate_class_title,
                contract_id=contract_id,
                contract_number=contract_number,
                signed_date=signed_date,
                contractor_title=contractor_title,
                display_rate=display_rate,
            )
        )
    return layer


def object_coverage(rows: Sequence[ObjectMoney]) -> dict:
    """Охват рейтинга. Его счётчики НЕ складываются с договорными: «5 договоров»
    и «1 объект» — разные сущности (решение 9 макета)."""
    reasons = {reason: 0 for reason in OBJECT_REASONS}
    counted = 0
    for row in rows:
        if row.reason is None:
            counted += 1
        else:
            reasons[row.reason] += 1
    return {"total": len(rows), "counted": counted, "reasons": reasons}


def ranking_sort_key(row: ObjectMoney) -> tuple:
    """Ключ порядка рейтинга: сумма по убыванию, `object_id` по возрастанию.

    ВТОРИЧНЫЙ КЛЮЧ ОБЯЗАТЕЛЕН, и стережёт его тест ФОРМЫ
    (`tests/unit/test_dashboard_ranking.py`), а не повторные прогоны: без него
    два объекта с равными суммами получают ОДИНАКОВЫЙ ключ, и порядок начинает
    зависеть от того, в каком порядке строки пришли из базы. Совпасть с
    ожидаемым он при этом может сколько угодно раз подряд — это и делает
    поведенческую проверку недостаточной.
    """
    return (-(row.amount if row.amount is not None else Decimal(0)), row.object_id)


def object_ranking(
    rows: Sequence[ObjectMoney], limit: int = RANKING_LIMIT
) -> list[ObjectMoney]:
    """Топ-N учтённых объектов по сумме. Объект, не влезший в N, остаётся УЧТЁННЫМ
    в охвате: сноска говорит про исключённых, а не про непоказанных."""
    counted = [row for row in rows if row.reason is None]
    return sorted(counted, key=ranking_sort_key)[:limit]


def per_sqm_chart(rows: Sequence[ObjectMoney]) -> dict:
    """Дорожка на класс, точка на объект, полоса размаха (решение 11 макета).

    **У класса с ОДНИМ объектом полосы НЕТ** — размаха не существует, и нарисовать
    его значило бы выдумать факт. Обратная ошибка (не рисовать полосу никогда)
    отрицательным тестом не ловится вовсе, поэтому её стережёт отдельный,
    ПОЛОЖИТЕЛЬНЫЙ тест класса с двумя объектами.

    Охват диаграммы СВОЙ: объект без `area_total_sp` в рейтинге есть, а точки у
    него нет.
    """
    lanes: dict[int, dict] = {}
    counted = 0
    for row in rows:
        if row.reason is not None or row.per_sqm is None or row.rate_class_id is None:
            continue
        counted += 1
        lane = lanes.setdefault(
            row.rate_class_id,
            {
                "rate_class_id": row.rate_class_id,
                "rate_class_title": row.rate_class_title,
                "points": [],
            },
        )
        lane["points"].append(
            {
                "object_id": row.object_id,
                "title": row.title,
                "per_sqm": row.per_sqm,
                "amount": row.amount,
                "area_total_sp": row.area_total_sp,
            }
        )

    classes = []
    for lane in sorted(lanes.values(), key=lambda item: item["rate_class_id"]):
        lane["points"].sort(key=lambda point: (point["per_sqm"], point["object_id"]))
        values = [point["per_sqm"] for point in lane["points"]]
        lane["spread"] = {"min": min(values), "max": max(values)} if len(values) > 1 else None
        classes.append(lane)

    return {"classes": classes, "coverage": {"total": len(rows), "counted": counted}}


def per_sqm_extremes(rows: Sequence[ObjectMoney]) -> dict:
    """Максимум и минимум ₽/м². Они тоже выборка по ЧАСТИ базы, поэтому подписаны
    своим охватом (решение 4 макета: «из 8»)."""
    points = [row for row in rows if row.reason is None and row.per_sqm is not None]
    coverage = {"total": len(rows), "counted": len(points)}

    def _point(row: ObjectMoney) -> dict:
        return {
            "object_id": row.object_id,
            "title": row.title,
            "per_sqm": row.per_sqm,
            "area_total_sp": row.area_total_sp,
            "rate_class_title": row.rate_class_title,
        }

    if not points:
        return {"max": None, "min": None, "coverage": coverage}
    return {
        "max": _point(max(points, key=lambda row: (row.per_sqm, -row.object_id))),
        "min": _point(min(points, key=lambda row: (row.per_sqm, row.object_id))),
        "coverage": coverage,
    }


# ---------------------------------------------------------------------------
#  Показатели шапки: площади и счётчики (решения 4, 5, 7 макета)
# ---------------------------------------------------------------------------

def _sum_area(values: Sequence[Decimal | None]) -> tuple[Decimal | None, int]:
    known = [value for value in values if value is not None]
    return (sum(known, start=Decimal(0)) if known else None), len(known)


def area_summary(db: Session) -> dict:
    """Площади базы: общая, надземная, подземная, полезная — У КАЖДОЙ СВОЙ ОХВАТ.

    Надземная и подземная заводятся ПАРОЙ (`CHECK` миграции 0009 — «обе или ни
    одной»), полезная от пары НЕ зависит (`CHECK` миграции 0013 сравнивает её со
    слагаемыми, и при `NULL`-паре сравнение пропускает). Поэтому объект, у
    которого заведена только полезная, входит в охват полезной и НЕ входит в
    охват пары, а общий счётчик «не заведена у N» это различие скрывал бы
    (решение 4 макета).
    """
    rows = db.execute(
        sa.select(
            ObjectModel.id,
            ObjectModel.title,
            ObjectModel.area_total_sp,
            ObjectModel.area_aboveground_sp,
            ObjectModel.area_underground_sp,
            ObjectModel.area_useful_sp,
        ).order_by(ObjectModel.id)
    ).all()
    total_objects = len(rows)

    def _block(index: int) -> dict:
        value, counted = _sum_area([row[index] for row in rows])
        return {"value": value, "coverage": {"total": total_objects, "counted": counted}}

    with_total = sorted(
        (row for row in rows if row.area_total_sp is not None),
        key=lambda row: (row.area_total_sp, row.id),
    )

    def _named(row) -> dict:
        return {"object_id": row.id, "title": row.title, "area_total_sp": row.area_total_sp}

    return {
        "total": _block(2),
        "aboveground": _block(3),
        "underground": _block(4),
        "useful": _block(5),
        "largest": _named(with_total[-1]) if with_total else None,
        "smallest": _named(with_total[0]) if with_total else None,
    }


def base_counters(db: Session, contracts: Sequence[ContractMoney]) -> dict:
    """Счётчики шапки. Объекты, классы и договоры — РАЗНЫЕ сущности и приезжают
    порознь (решение 9 макета: «5 договоров» и «1 объект» не складываются).

    «Классов» считается по `objects.rate_class_id` — это «объекты лежат в N
    классах», а не размер справочника: пустой класс на главной ничего не
    описывает.
    """
    objects = db.execute(sa.select(sa.func.count()).select_from(ObjectModel)).scalar_one()
    classes = db.execute(
        sa.select(sa.func.count(sa.distinct(ObjectModel.rate_class_id))).where(
            ObjectModel.rate_class_id.is_not(None)
        )
    ).scalar_one()
    with_estimate = sum(1 for row in contracts if row.reason != CONTRACT_REASON_NO_ESTIMATE)
    return {
        "objects": objects,
        "classes": classes,
        "contracts": len(contracts),
        "contracts_with_estimate": with_estimate,
    }


# ---------------------------------------------------------------------------
#  Сборка ответа основного таба (спека §2.8)
# ---------------------------------------------------------------------------

def _ranking_card(row: ObjectMoney) -> dict:
    """Карточка объекта в рейтинге (решение 8 макета).

    `signed_date` приводится к ISO ЗДЕСЬ: энкодер `responses.decimal_json`
    намеренно строгий и бросает `TypeError` на `date` — забывчивость видна сразу,
    а не как «дата в неожиданном формате» на экране.
    """
    return {
        "object_id": row.object_id,
        "title": row.title,
        "rate_class_id": row.rate_class_id,
        "rate_class_title": row.rate_class_title,
        "area_total_sp": row.area_total_sp,
        "amount": row.amount,
        "per_sqm": row.per_sqm,
        "display_rate": row.display_rate,
        "contract": {
            "id": row.contract_id,
            "contract_number": row.contract_number,
            "signed_date": iso(row.signed_date),
            "contractor_title": row.contractor_title,
        },
    }


def get_dashboard(db: Session) -> dict:
    """Основной таб главной целиком (спека §2.8): деньги, площади, счётчики,
    рейтинг и диаграмма — ОДНИМ ответом.

    Диагностики второго таба здесь НЕТ намеренно: они закрыты `require_admin`, и
    сложенные в один ответ они закрыли бы вместе с собой и весь остальной
    дашборд, который читателю положен (§2.8).

    Договорный и объектный слои считаются по одному разу и переиспользуются: два
    прохода по одним данным разъехались бы при первой правке.
    """
    contracts = contract_layer(db)
    objects = object_layer(db, contracts)
    return {
        "money": {
            "amount": money_total(contracts),
            "coverage": contract_coverage(contracts),
        },
        "areas": area_summary(db),
        "counters": base_counters(db, contracts),
        "per_sqm": per_sqm_extremes(objects),
        "ranking": [_ranking_card(row) for row in object_ranking(objects)],
        "ranking_coverage": object_coverage(objects),
        "chart": per_sqm_chart(objects),
    }
