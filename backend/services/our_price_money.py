"""Суммы статей сметы, ₽/м², пары нетто/показано и ставка общей колонки истории.

Чистый модуль: без базы и SQL, строки `v_category_totals` и дерево статей ему
передаёт загрузчик. Правило сумм здесь — правило VIEW (спека 4а §2.8): все
конечные суммы, отрицательные тоже, с допработами; предикат цены «конечно и
больше нуля» относится к расценке и применяется только к площади в `per_sqm`;
нулевая сумма отдельной статьи — «статьи нет» (`article_net`).
Промежуточного округления нет; `quantize_money` вызывается один раз на поле
пары и только для сумм (ставки за единицу и ₽/м² — без округления, §2.9);
кроме того — как порог нуля статьи в `article_net`, само значение не
округляется.
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


def article_net(net: Decimal | None) -> Decimal | None:
    """Нетто статьи как значение: нулевая сумма — «статьи нет» (`None`).

    Ноль решается по сумме, округлённой до копеек (`quantize_money`, половина
    вверх): строки VIEW переводятся в нетто по одной, и взаимно гасящиеся в
    брутто суммы оставляют остаток порядка 1E-98, который на экране «0,00».
    Само значение не округляется. Одно правило для вилки класса по статье и
    ячейки КП (спека 4а §2.8). Отрицательная сумма (зачёт) остаётся
    значением; «Итого» сметы правило не затрагивает — оно считается по
    `estimate_net_total` из всех корней.
    """
    return None if quantize_money(net) == _ZERO else net


def estimate_net_total(by_root: Mapping[int | None, Decimal | None]) -> Decimal | None:
    """Нетто сметы: сумма корней; хотя бы один неизвестный корень — итог `None`."""
    total = _ZERO
    with localcontext(_CONTEXT):
        for value in by_root.values():
            if value is None:
                return None
            total = total + value
    return total


@dataclass(frozen=True)
class NodeLine:
    """Строка КП для итогов узлов дерева: валовая сумма строки (`total_cost_total`), признак
    состояния «без цены» и узлы пути строки — статьи (`None` — «Нераспределённое») и разделы сметы."""

    total: Decimal | None
    no_price: bool
    categories: tuple[int | None, ...]
    chapters: tuple[int, ...]


@dataclass(frozen=True)
class NodeAmount:
    """Сумма вне строк позиций (дополнительная работа): валовая сумма и статьи пути (`None` —
    «Нераспределённое»). Входит в статьи, не в разделы и не в счётчики строк."""

    total: Decimal | None
    categories: tuple[int | None, ...]


def node_totals(
    lines: Iterable[NodeLine], *, base: Decimal | None, amounts: Iterable[NodeAmount] = ()
) -> dict[str, dict]:
    """Итоги узлов дерева страницы участника: `{categories: {id | None: итог}, chapters: {статья | None: {id раздела: итог}}}`;
    итог раздела — по каждой статье отдельно, только строки этой статьи в поддереве раздела.

    Узел получает каждую строку, у которой он на пути, — так итог узла равен сумме строк его
    поддерева. Итог узла — `{lines, no_price, kp_total}`: число строк, число строк в состоянии
    «без цены» и сумма КП `{net, shown}`. Сумма — по правилу сумм статей (см. модульную докстроку):
    все конечные суммы строк, отрицательные тоже, строка без цены входит со своей конечной суммой;
    предикат цены тут не применяется. Нетто — по базе НДС КП, `shown` — в той же ставке; база
    неизвестна — `kp_total = None`. Нулевая сумма остаётся парой нулей: «ноль» и «нет» разные
    вещи, в отличие от `article_net` у корня статьи. Узел, где нет ни одной конечной суммы, —
    `kp_total = None` («суммы нет»), как у корня.

    `amounts` — суммы вне строк (дополнительные работы): они входят в свою статью и её предков
    так же, как в корневые `articles`, но не в число строк, не в «без цены» и не в разделы.
    """
    accumulated: dict[tuple, list] = {}

    def add(key: tuple, total: Decimal | None, *, line: bool, no_price: bool = False) -> None:
        node = accumulated.setdefault(key, [0, 0, _ZERO, False])
        if line:
            node[0] += 1
            node[1] += 1 if no_price else 0
        if total is not None and total.is_finite():
            with localcontext(_CONTEXT):
                node[2] = node[2] + total
            node[3] = True

    for line in lines:
        for key in line.categories:
            add(("categories", key), line.total, line=True, no_price=line.no_price)
        # раздел считается отдельно по каждой статье: строки одного раздела бывают в разных статьях,
        # а раздел показан внутри статьи — своя статья строки есть последний узел её цепочки
        own = line.categories[-1]
        for chapter in line.chapters:
            add(("chapters", own, chapter), line.total, line=True, no_price=line.no_price)
    for amount in amounts:
        for key in amount.categories:
            add(("categories", key), amount.total, line=False)
    result: dict[str, dict] = {"categories": {}, "chapters": {}}
    for key, (count, no_price, gross, has_amount) in accumulated.items():
        node = {
            "lines": count,
            "no_price": no_price,
            "kp_total": (
                money_pair(gross_to_net(gross, base), base, unit=False) if base is not None and has_amount else None
            ),
        }
        if key[0] == "categories":
            result["categories"][key[1]] = node
        else:
            result["chapters"].setdefault(key[1], {})[key[2]] = node
    return result


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
