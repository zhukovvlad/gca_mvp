"""Суммы статей сметы, ₽/м², пары нетто/показано и ставка общей колонки истории.

Чистый модуль: без базы и SQL, строки `v_category_totals` и дерево статей ему
передаёт загрузчик. Правило сумм здесь — правило VIEW (спека 4а §2.8): все
конечные суммы, отрицательные тоже, с допработами; предикат цены «конечно и
больше нуля» относится к расценке и применяется только к площади в `per_sqm`.
Промежуточного округления нет; `quantize_money` вызывается один раз на поле
пары, и только для сумм (ставки за единицу и ₽/м² — без округления, §2.9).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext

from money.price import is_price
from money.vat import gross_to_net, net_to_gross, quantize_money
from services.our_price import _CONTEXT, _median

#: Ключ «Нераспределённое» в разбивке по корням статей.
UNALLOCATED: None = None

_ZERO = Decimal(0)
_HUNDRED = Decimal(100)


@dataclass(frozen=True)
class CategoryRow:
    """Строка `v_category_totals`: сумма статьи сметы в базе НДС этой сметы."""

    estimate_id: int
    work_category_id: int | None
    vat_base: Decimal | None
    amount: Decimal | None


def net_by_root(rows: Iterable[CategoryRow], root_of: Mapping[int, int]) -> dict[int, dict[int | None, Decimal | None]]:
    """Нетто по корням статей для каждой сметы: смета -> корень -> нетто | `None`.

    Строки одной сметы и одного корня накапливаются (смета из двух лотов — две
    строки VIEW). Сумма переводится в нетто базой своей строки. Строка с
    неизвестной базой и непустой суммой делает корень этой сметы `None`
    навсегда: следующие строки его не «лечат». Строка без суммы (или с
    неконечной) ничего не вкладывает и корень не отравляет. Категория вне
    `root_of` — ошибка вызывающего (`KeyError`); `work_category_id is None`
    идёт в `UNALLOCATED`.
    """
    result: dict[int, dict[int | None, Decimal | None]] = {}
    for row in rows:
        root = UNALLOCATED if row.work_category_id is None else root_of[row.work_category_id]
        roots = result.setdefault(row.estimate_id, {})
        if row.amount is None or not row.amount.is_finite():
            continue
        if root in roots and roots[root] is None:
            continue
        if row.vat_base is None:
            roots[root] = None
            continue
        net = gross_to_net(row.amount, row.vat_base)
        previous = roots.get(root, _ZERO)
        with localcontext(_CONTEXT):
            roots[root] = previous + net
    return result


def estimate_net_total(by_root: Mapping[int | None, Decimal | None]) -> Decimal | None:
    """Нетто сметы: сумма корней; хотя бы один неизвестный корень — итог `None`."""
    total = _ZERO
    with localcontext(_CONTEXT):
        for value in by_root.values():
            if value is None:
                return None
            total = total + value
    return total


def per_sqm(net: Decimal | None, area: Decimal | None) -> Decimal | None:
    """₽/м² без округления; `None`, если нетто нет или площадь не проходит `is_price`."""
    if net is None or not is_price(area):
        return None
    with localcontext(_CONTEXT):
        return net / area


def money_pair(net: Decimal | None, rate: Decimal | None, *, unit: bool) -> dict[str, Decimal | None] | None:
    """Пара `{net, shown}`: `shown` — нетто, пересчитанное в ставку показа.

    Нетто `None` — результата нет (`None`, не словарь); ставка `None` —
    `shown=None`. Для сумм (`unit=False`) оба поля округляются до копеек, для
    ставок за единицу (`unit=True`) — без округления.
    """
    if net is None:
        return None
    shown = None if rate is None else net_to_gross(net, rate)
    if unit:
        return {"net": net, "shown": shown}
    return {"net": quantize_money(net), "shown": quantize_money(shown)}


def history_shown_rate(participant_rates: Iterable[Decimal | None]) -> Decimal | None:
    """Ставка общей колонки истории: единственная различная известная ставка.

    Участники с неизвестной базой (`None`) голос не подают. Две и больше
    различных ставок или ни одной — `None`: общей колонки в одной ставке нет.
    """
    distinct: list[Decimal] = []
    for rate in participant_rates:
        if rate is not None and rate not in distinct:
            distinct.append(rate)
    return distinct[0] if len(distinct) == 1 else None


def pct_to_median(value: Decimal | None, pool: Sequence[Decimal]) -> Decimal | None:
    """Отклонение от медианы пула в процентах, без округления.

    `None`, если значения нет, пул пуст или медиана не положительна: нулевой и
    отрицательный итог статьи законны по правилу сумм, деление на ноль падало
    бы, а процент к отрицательной базе меняет направление на обратное.
    """
    if value is None or not pool:
        return None
    median = _median(pool)
    if median <= _ZERO:
        return None
    with localcontext(_CONTEXT):
        return (value / median - 1) * _HUNDRED
