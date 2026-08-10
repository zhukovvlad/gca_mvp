"""Roll-up дерева статей классификатора для паспорта проекта (спека Ф6 §2.2-2.4).

`v_category_totals` даёт ПРЯМЫЕ суммы по статье и источнику (`positions` /
`additional_works`), без свёртки по дереву — экрану паспорта нужны обе величины
сразу: итог поддерева и собственные деньги узла, а выводить «собственные» как
разность «родитель минус дети» значило бы выдавать за факт то, что на самом
деле вычитание. Свёртку делает этот модуль, и делает её в Python, а не в SQL:
дерево статей маленькое (362 строки), а Decimal-арифметика в PostgreSQL и в
Python обязана давать один и тот же результат — гарантировать это проще одним
местом, читающим только простые числа.

Модуль чистый: ни `Session`, ни ORM, ни SQL, ни импортов из `crud` — только
`CategoryRef` / `DirectTotals` на входе и `CategoryNode` на выходе.

Имена источников живут ЗДЕСЬ, а не в `crud/project_passport.py`, хотя описывают
колонку `source` того VIEW. Причина замерена, а не вкусовая: `build_tree` — их
единственный содержательный потребитель (он и различает две ветки), и обратный
импорт замыкал бы модули в цикл, а заодно тянул бы в «чистый» модуль `models`,
`Session` и `crud.common` — то есть ровно то, чего первый абзац этой докстроки
обещает не делать. Единственный источник истины при этом сохранён: `crud`
импортирует эти имена отсюда, а строки в SQL — литералы миграции 0010, которая
обязана быть неизменной во времени.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

#: Значения колонки `source` VIEW `v_category_totals` (миграция 0010).
SOURCE_POSITIONS = "positions"
SOURCE_ADDITIONAL_WORKS = "additional_works"

__all__ = [
    "SOURCE_ADDITIONAL_WORKS",
    "SOURCE_POSITIONS",
    "CategoryRef",
    "DirectTotals",
    "CategoryNode",
    "build_tree",
]


@dataclass(frozen=True)
class CategoryRef:
    id: int
    code: str
    title: str
    parent_id: int | None
    is_bucket: bool
    sort_order: int


@dataclass(frozen=True)
class DirectTotals:
    """Строка VIEW `v_category_totals`, уже разложенная по источникам.

    Инвариант источника (спека §1.11): `amount is None` тогда и только тогда,
    когда `rows_with_amount == 0`. `build_tree` полагается на него и обязан его
    сохранить при свёртке — отсюда парная проверка «total is None ⟺
    rows_priced == 0» в тестах.
    """

    amount: Decimal | None
    row_count: int
    rows_with_amount: int
    rows_not_finite: int


@dataclass(frozen=True)
class CategoryNode:
    ref: CategoryRef
    total: Decimal | None
    rows: int
    rows_priced: int
    rows_not_finite: int
    own: Decimal | None
    own_rows: int
    own_rows_priced: int
    own_rows_not_finite: int
    children: tuple[CategoryNode, ...]


#: Пустая ветка источника — узел, у которого этого источника нет вовсе.
#: Один и тот же объект переиспользуется, чтобы не заводить `if ... else 0`
#: четыре раза на каждую ветку.
_EMPTY = DirectTotals(amount=None, row_count=0, rows_with_amount=0, rows_not_finite=0)


def build_tree(
    categories: Sequence[CategoryRef],
    direct: Mapping[int | None, Mapping[str, DirectTotals]],
) -> tuple[CategoryNode, ...]:
    """Строит дерево паспорта из плоского справочника и прямых сумм.

    `direct[None]` (Нераспределённое) сюда не заходит вовсе — вызывающий код
    показывает его отдельно, это не категория и не претендует на место в дереве.

    Корни (`parent_id is None`) присутствуют ВСЕГДА, даже с нулём строк: паспорта
    разных объектов должны иметь общий скелет, и «статья — прочерк» — такой же
    факт, как ненулевая сумма. Глубже первого уровня узел появляется, только
    если в его поддереве вообще есть строки (`rows > 0`) — иначе паспорт нёс бы
    все 362 строки справочника вместо ~100 по факту сметы.
    """
    children_by_parent: dict[int, list[CategoryRef]] = defaultdict(list)
    roots: list[CategoryRef] = []
    for category in categories:
        if category.parent_id is None:
            roots.append(category)
        else:
            children_by_parent[category.parent_id].append(category)

    roots.sort(key=lambda c: c.sort_order)
    for siblings in children_by_parent.values():
        siblings.sort(key=lambda c: c.sort_order)

    return tuple(_build_node(root, children_by_parent, direct) for root in roots)


def _build_node(
    ref: CategoryRef,
    children_by_parent: Mapping[int, list[CategoryRef]],
    direct: Mapping[int | None, Mapping[str, DirectTotals]],
) -> CategoryNode:
    child_nodes = tuple(
        _build_node(child_ref, children_by_parent, direct)
        for child_ref in children_by_parent.get(ref.id, ())
    )

    own_branch = direct.get(ref.id, {}).get(SOURCE_POSITIONS, _EMPTY)
    extra_branch = direct.get(ref.id, {}).get(SOURCE_ADDITIONAL_WORKS, _EMPTY)

    rows = own_branch.row_count + extra_branch.row_count + sum(c.rows for c in child_nodes)
    rows_priced = (
        own_branch.rows_with_amount
        + extra_branch.rows_with_amount
        + sum(c.rows_priced for c in child_nodes)
    )
    rows_not_finite = (
        own_branch.rows_not_finite
        + extra_branch.rows_not_finite
        + sum(c.rows_not_finite for c in child_nodes)
    )

    # Только известные слагаемые входят в сумму: неизвестное (None) — это
    # отсутствующее слагаемое, а не ноль. Значит total is None ровно тогда,
    # когда ни own, ни extra_branch.amount, ни один c.total не известны —
    # то есть ровно тогда, когда rows_priced == 0 (по индукции и инварианту VIEW).
    summands = [amount for amount in (own_branch.amount, extra_branch.amount) if amount is not None]
    summands.extend(c.total for c in child_nodes if c.total is not None)
    total = sum(summands) if summands else None

    visible_children = tuple(c for c in child_nodes if c.rows > 0)

    return CategoryNode(
        ref=ref,
        total=total,
        rows=rows,
        rows_priced=rows_priced,
        rows_not_finite=rows_not_finite,
        own=own_branch.amount,
        own_rows=own_branch.row_count,
        own_rows_priced=own_branch.rows_with_amount,
        own_rows_not_finite=own_branch.rows_not_finite,
        children=visible_children,
    )
