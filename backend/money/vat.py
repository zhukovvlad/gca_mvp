"""Пересчёт денежных величин между ставками НДС (спека §2.2).

Функции СМЫСЛОВЫЕ, а не универсальные: `gross_to_net` нельзя применять к сумме
налога и к нетто-строке блока итогов — они уже не валовые. Универсального
множителя `(100+цель)/(100+база)` здесь нет намеренно: применённый к сумме
налога, при цели 0 % он оставил бы ненулевой налог.

Канон — валовое. И валовое, и нетто суть факты файла, но единый путь
преобразования и сохранение аддитивности позиций дают именно валовому; файловое
нетто служит независимой перекрёстной проверкой (§2.2, §2.10).

ОКРУГЛЕНИЕ ЗДЕСЬ НЕ ДЕЛАЕТСЯ. `quantize_money` вызывается один раз, на границе
ответа, над ГОТОВЫМ полем; внутрь агрегации она попасть не имеет права, иначе
`Σ round(x) ≠ round(Σ x)`.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import (
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from enum import StrEnum

from finance import money_round
from parser.summary_block import ARITHMETIC_PRECISION

_HUNDRED = Decimal(100)

#: Контекст строится ЯВНО и не наследует глобальный: `localcontext()` без
#: аргумента копирует текущий контекст ВМЕСТЕ С ЕГО ТРАПАМИ, и включённый
#: где-то `Inexact` превратил бы штатное деление в исключение. Округление здесь
#: РАЗРЕШЕНО — частное почти никогда не представимо конечной десятичной дробью
#: (тот же довод и та же форма, что у `parser/vat_rate.py`).
_VAT_CONTEXT = Context(
    prec=ARITHMETIC_PRECISION,
    traps=[Overflow, DivisionByZero, InvalidOperation],
)

#: Допуск сверки выведенного нетто с файловым, в рублях.
#: Снят замером Г нулевой задачи: 3.5 взят на два порядка выше
#: замеренного шума сверки 0.035.
NET_RECONCILIATION_TOLERANCE = Decimal("3.5")


class AmountStatus(StrEnum):
    """Что произошло с суммой при приведении к ставке показа."""

    ORIGINAL = "original"
    """Значение НЕ тронуто, арифметики не было — либо потому что цель равна
    базе, либо потому что суммы нет вовсе."""

    RESTATED = "restated"
    """Пересчитано: нетто выведено из базы, затем поднято до цели."""

    UNKNOWN_BASE = "unknown_base"
    """База неизвестна — нетто не существует, показано исходное значение."""

    NOT_FINITE = "not_finite"
    """`NaN`/`±Infinity` в источнике (открытый хвост Ф4): пересчёт невозможен."""


@dataclass(frozen=True)
class RestatedAmount:
    amount: Decimal | None
    status: AmountStatus


def gross_to_net(gross: Decimal, base: Decimal) -> Decimal:
    """Убрать из валовой суммы НДС по ставке, которой она соответствует."""
    with localcontext(_VAT_CONTEXT):
        return gross * _HUNDRED / (_HUNDRED + base)


def net_to_gross(net: Decimal, target: Decimal) -> Decimal:
    """Поднять нетто до валовой суммы по целевой ставке."""
    with localcontext(_VAT_CONTEXT):
        return net * (_HUNDRED + target) / _HUNDRED


def vat_from_net(net: Decimal, target: Decimal) -> Decimal:
    """Сумма налога от нетто по целевой ставке. При цели 0 даёт ровно 0."""
    with localcontext(_VAT_CONTEXT):
        return net * target / _HUNDRED


def restate_gross(
    gross: Decimal | None, base: Decimal | None, target: Decimal | None
) -> RestatedAmount:
    """Показать валовую сумму в ставке показа. Три ветки спеки §2.4.

    Ветка тождества — ТРЕБОВАНИЕ, а не оптимизация, и срабатывает всегда при
    равенстве цели и базы, независимо от того, перекрыта база или нет. Умножение
    на единицу сдвинуло бы `exponent`, и посимвольное совпадение денежных полей
    (§2.4) перестало бы выполняться.
    """
    if gross is None:
        # Значение отсутствует. Проверяем базу: если её нет, статус UNKNOWN_BASE,
        # иначе None пойдёт в ORIGINAL (не пересчитываем то, чего нет).
        if base is None:
            return RestatedAmount(amount=None, status=AmountStatus.UNKNOWN_BASE)
        # При None сумме тождество сохраняется: не было арифметики.
        return RestatedAmount(amount=None, status=AmountStatus.ORIGINAL)

    # Статус NOT_FINITE перевешивает UNKNOWN_BASE и ORIGINAL: негодная сумма
    # негодна при любой базе и при любой цели.
    if not gross.is_finite():
        return RestatedAmount(amount=gross, status=AmountStatus.NOT_FINITE)

    if base is None:
        return RestatedAmount(amount=gross, status=AmountStatus.UNKNOWN_BASE)

    effective = base if target is None else target
    if effective == base:
        return RestatedAmount(amount=gross, status=AmountStatus.ORIGINAL)

    net = gross_to_net(gross, base)
    return RestatedAmount(amount=net_to_gross(net, effective), status=AmountStatus.RESTATED)


def quantize_money(value: Decimal | None) -> Decimal | None:
    """Округлить до копеек. Вызывается ОДИН раз, над готовым полем ответа.

    На не-конечном значении (NaN, ±Infinity) возвращает его без изменений —
    округлять нечего, а терять факт «сумма не число» нельзя (None неверный
    ответ). НИКОГДА не бросает исключение.
    """
    if value is None:
        return None
    if not value.is_finite():
        return value
    return money_round(value, 2)


class NetStatus(StrEnum):
    """Вердикт сверки выведенного нетто с файловым (спека §2.10)."""

    OK = "ok"
    MISMATCH = "mismatch"
    UNKNOWN_BASE = "unknown_base"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class ProposalNetCheck:
    proposal_id: int
    status: NetStatus
    delta: Decimal | None


@dataclass(frozen=True)
class NetReconciliation:
    status: NetStatus
    delta: Decimal | None
    mismatched_proposal_ids: list[int]


_COMPARABLE = (NetStatus.OK, NetStatus.MISMATCH)


def check_proposal_net(
    proposal_id: int,
    gross_total: Decimal | None,
    file_net: Decimal | None,
    base: Decimal | None,
) -> ProposalNetCheck:
    """Сверить выведенное из валового нетто с тем, что заявил файл.

    Перекрёстная проверка, не источник значения: эталон приезжает из блока
    итогов, разобранного другим кодом и по другим правилам, поэтому одна ошибка
    не сдвигает обе стороны сравнения сразу.
    """
    if base is None:
        return ProposalNetCheck(proposal_id, NetStatus.UNKNOWN_BASE, None)

    if any(value is None or not value.is_finite() for value in (gross_total, file_net)):
        return ProposalNetCheck(proposal_id, NetStatus.NOT_APPLICABLE, None)

    delta = gross_to_net(gross_total, base) - file_net
    status = NetStatus.MISMATCH if abs(delta) > NET_RECONCILIATION_TOLERANCE else NetStatus.OK
    return ProposalNetCheck(proposal_id, status, delta)


def fold_net_reconciliation(checks: Sequence[ProposalNetCheck]) -> NetReconciliation:
    """Свернуть проверки предложений в один вердикт по смете (спека §2.10).

    Дельты непроверяемых предложений в сумму НЕ входят: иначе величина с именем
    «расхождение нетто» несла бы в себе нули, означающие «не сверяли», и
    уменьшалась бы от добавления предложений, которых сверка не касалась.
    """
    comparable = [check for check in checks if check.status in _COMPARABLE]
    mismatched = [check.proposal_id for check in checks if check.status is NetStatus.MISMATCH]

    if mismatched:
        status = NetStatus.MISMATCH
    elif any(check.status is NetStatus.UNKNOWN_BASE for check in checks):
        status = NetStatus.UNKNOWN_BASE
    elif comparable:
        status = NetStatus.OK
    else:
        status = NetStatus.NOT_APPLICABLE

    delta = sum((check.delta for check in comparable), start=Decimal(0)) if comparable else None
    return NetReconciliation(status=status, delta=delta, mismatched_proposal_ids=mismatched)
