"""Шаблонный комментарий строки КП: случай, цель, договор-источник, текст.

Чистый расчёт без базы (спека 4б §2.2, §2.3, §2.7, §2.8). Подрядчику называются
только цель строки и её источник: ни вилки, ни медианы, ни числа договоров, ни
ступени в тексте нет. Текст не зависит от переключателя НДС: все суммы в нём
стоят в базе НДС КП (как «Наша расценка» в файле), и это сказано словами.
Случай и сравнения «выше» считаются по нетто. Деньги — `Decimal`, округление до
копеек только в сравнениях «выше» и при выводе числа.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Context, Decimal, DivisionByZero, InvalidOperation, Overflow, localcontext
from typing import Literal

from money.price import is_price, is_weight
from money.vat import net_to_gross, quantize_money
from parser.summary_block import ARITHMETIC_PRECISION
from services.our_price import (
    ContractRate,
    HistoryContract,
    HistoryIndex,
    Level,
    LineResult,
    LineState,
)

CommentCase = Literal["agreed", "kp_unit", "history_unit", "below", "over"]

#: Промежуточных округлений нет; ловушки включены, чтобы деление на ноль не прошло молча.
_CONTEXT = Context(
    prec=ARITHMETIC_PRECISION,
    traps=[Overflow, DivisionByZero, InvalidOperation],
)


@dataclass(frozen=True)
class SourceContract:
    contract: HistoryContract
    #: `net` — цель (с рядом), `nominal` — цитата, `lines` — строк в ставке.
    rate: ContractRate


@dataclass(frozen=True)
class InflationLabel:
    series_name: str
    #: Целевой месяц этапа, `ММ.ГГГГ`.
    month: str


@dataclass(frozen=True)
class CommentInputs:
    result: LineResult
    is_system: bool
    #: Единица строки КП, как её назвал файл.
    unit_text: str
    #: `unit_cost_total` в нетто.
    kp_unit_net: Decimal | None
    kp_total_net: Decimal | None
    #: Вес строки.
    quantity: Decimal | None
    agreed_net: Decimal | None
    #: Уровень действующей ступени.
    level: Level | None
    source: SourceContract | None
    tender_contractor_id: int
    tender_area: Decimal | None
    #: `None` — без ряда ИПЦ.
    inflation: InflationLabel | None
    #: База НДС КП: в ней печатаются все суммы текстов.
    vat_base: Decimal


def pick_source(
    result: LineResult,
    history: HistoryIndex,
    *,
    tender_class_id: int,
    tender_contractor_id: int,
) -> SourceContract | None:
    """Договор, давший нашу расценку (спека 4б §2.7, «Подстановки»)."""
    candidates: list[SourceContract] = []
    for rate in result.contracts:
        if rate.net != result.ours:
            continue
        contract = history.contract(rate.contract_id)
        if not result.ours_from_all_classes and contract.rate_class_id != tender_class_id:
            continue
        candidates.append(SourceContract(contract, rate))
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda s: (s.contract.contractor_id != tender_contractor_id, -s.contract.year, s.contract.contract_id),
    )


def target_sum_net(inp: CommentInputs) -> Decimal | None:
    """Автоматическая целевая сумма строки системы: наша ₽/м² * площадь * доля."""
    res = inp.result
    if not inp.is_system or res.state is not LineState.comparable or res.ours is None:
        return None
    if not is_price(inp.tender_area):
        return None
    with localcontext(_CONTEXT):
        return res.ours * inp.tender_area * res.share


def target_net(inp: CommentInputs) -> Decimal | None:
    """Цель строки за единицу строки КП (спека 4б §2.2)."""
    if inp.agreed_net is not None:
        return inp.agreed_net
    if inp.result.state is not LineState.comparable:
        return None
    if not inp.is_system:
        return inp.result.ours
    total = target_sum_net(inp)
    if total is None or not is_weight(inp.quantity):
        return None
    with localcontext(_CONTEXT):
        return total / inp.quantity


def _above(value: Decimal, other: Decimal) -> bool:
    return quantize_money(value) > quantize_money(other)


def comment_case(inp: CommentInputs) -> CommentCase | None:
    """Случай комментария — первая подходящая строка таблицы спеки 4б §2.7."""
    if inp.agreed_net is not None:
        if is_price(inp.kp_unit_net) and _above(inp.kp_unit_net, inp.agreed_net):
            return "agreed"
        return None
    res = inp.result
    if res.state is LineState.unit_mismatch:
        return "kp_unit" if res.mismatch.kp_ratio is not None else "history_unit"
    if res.state is not LineState.comparable:
        return None
    if res.position == "below":
        return "below"
    if inp.is_system:
        kp, target = inp.kp_total_net, target_sum_net(inp)
    else:
        kp, target = inp.kp_unit_net, target_net(inp)
    return "over" if is_price(kp) and is_price(target) and _above(kp, target) else None


# --- числа ------------------------------------------------------------------------


def _group_digits(integer: str) -> str:
    return re.sub(r"(?<=\d)(?=(?:\d{3})+$)", " ", integer)


def format_rub(value: Decimal) -> str:
    """`34 154,00`: копейки половиной вверх, разряды пробелом, запятая."""
    integer, _, fraction = format(quantize_money(value), "f").partition(".")
    return f"{_group_digits(integer)},{fraction}"


def _format_quantity(value: Decimal) -> str:
    """Объём: разряды пробелом, запятая, нули справа отброшены, экспоненты нет."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    integer, _, fraction = text.partition(".")
    return _group_digits(integer) + (f",{fraction}" if fraction else "")


# --- тексты -----------------------------------------------------------------------


def vat_words(base: Decimal) -> str:
    """Пометка базы: «без НДС» у базы 0 %, иначе «с НДС 20 %»."""
    if base == 0:
        return "без НДС"
    return f"с НДС {format(base.normalize(), 'f')} %"


def rub_in_base(inp: CommentInputs, net: Decimal) -> str:
    """Нетто-сумма в базе КП, `34 154,00`; тот же `net_to_gross`, что у «Нашей расценки»."""
    return format_rub(net_to_gross(net, inp.vat_base))


def _kp_phrase(inp: CommentInputs) -> str:
    """Как названа цена КП: за единицу или суммой строки (три формы §2.7)."""
    unit = inp.unit_text
    if not inp.is_system:
        return f"{rub_in_base(inp, inp.kp_unit_net)} ₽ за {unit}"
    if not is_weight(inp.quantity):
        return f"{rub_in_base(inp, inp.kp_total_net)} ₽ за весь объём"
    if is_price(inp.kp_unit_net):
        return f"{rub_in_base(inp, inp.kp_unit_net)} ₽ за {unit}"
    return f"{rub_in_base(inp, inp.kp_total_net)} ₽ за весь объём ({_format_quantity(inp.quantity)} {unit})"


def _what(inp: CommentInputs) -> tuple[str, str]:
    singular, plural = ("система", "системы") if inp.is_system else ("работа", "работы")
    if inp.level == "v":
        return f"эта {singular}", "стоила"
    return f"сопоставимые {plural}", "стоили"


def _over_text(inp: CommentInputs) -> str | None:
    src = inp.source
    if src is None:
        return None
    unit = inp.unit_text
    vat = vat_words(inp.vat_base)
    what, verb = _what(inp)
    with_you = "с вами " if src.contract.contractor_id == inp.tender_contractor_id else ""
    head = (
        f"Ваша цена — {_kp_phrase(inp)} {vat}. "
        f"В нашем договоре {with_you}по объекту {src.contract.object_title}, {src.contract.year} г., {what} {verb} "
    )
    inflated = inp.inflation is not None and src.rate.net != src.rate.nominal
    series = f"; с учётом инфляции ({inp.inflation.series_name}, к {inp.inflation.month}) — " if inflated else ""
    nominal = src.rate.nominal
    if not inp.is_system:
        average = "в среднем " if src.rate.lines >= 2 else ""
        goal = rub_in_base(inp, target_net(inp))
        tail = f"{series}{goal} ₽" if inflated else ""
        return (
            f"{head}{average}{rub_in_base(inp, nominal)} ₽ за {unit} {vat}{tail}. "
            f"Просим снизить до {goal} ₽ или обосновать разницу."
        )
    with localcontext(_CONTEXT):
        nominal_sum = nominal * inp.tender_area * inp.result.share
    body = f"{head}{rub_in_base(inp, nominal)} ₽ на м² объекта {vat} — для вашего объекта это {rub_in_base(inp, nominal_sum)} ₽ за весь объём"
    if is_weight(inp.quantity):
        with localcontext(_CONTEXT):
            nominal_unit = nominal_sum / inp.quantity
        goal = f"{rub_in_base(inp, target_net(inp))} ₽ за {unit}"
        body += f" ({_format_quantity(inp.quantity)} {unit}), то есть {rub_in_base(inp, nominal_unit)} ₽ за {unit}"
        ask = goal
    else:
        goal = f"{rub_in_base(inp, target_sum_net(inp))} ₽"
        ask = f"{goal} за весь объём"
    tail = f"{series}{goal}" if inflated else ""
    return f"{body}{tail}. Просим снизить до {ask} или обосновать разницу."


def template_text(inp: CommentInputs) -> str | None:
    """Шаблонный комментарий строки; `None` — комментария нет."""
    case = comment_case(inp)
    if case is None:
        return None
    if case == "over":
        return _over_text(inp)
    vat = vat_words(inp.vat_base)
    if case == "agreed":
        return (
            f"Согласовано: {rub_in_base(inp, inp.agreed_net)} ₽ за {inp.unit_text} {vat}. "
            "Просим отразить в следующей редакции КП."
        )
    phrase = _kp_phrase(inp)
    if case == "below":
        return f"Просим подтвердить, что в цену {phrase} {vat} включены все работы и материалы по ведомости."
    if case == "kp_unit":
        if inp.is_system and not (is_weight(inp.quantity) and is_price(inp.kp_unit_net)):
            return f"Ваша цена — {phrase} {vat}. Уточните, пожалуйста, объём и единицу учёта."
        return (
            f"Цена {phrase} {vat} похожа на цену за весь объём, а не за единицу. "
            "Уточните, пожалуйста, единицу учёта."
        )
    return f"Просим расшифровать, что входит в цену {phrase} {vat} и за какую единицу она дана."
