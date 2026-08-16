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

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.project_passport import CATEGORY_TOTALS
from models import Contract, Estimate, Lot, Proposal
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
