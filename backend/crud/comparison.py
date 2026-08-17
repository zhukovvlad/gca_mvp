"""Роллап выборки договоров для сравнения (спека 2026-08-17 §2.1, §2.1.3).

Паспорт одного договора (`crud/project_passport.py`) читает `v_category_totals`
и складывает дерево статей `services.category_rollup.build_tree` для ОДНОЙ
сметы. Сравнению нужно то же самое сразу для НЕСКОЛЬКИХ договоров, пакетно —
и с фактами, которых дерево `CategoryNode` не несёт.

**Почему `CategoryNode` сам по себе недостаточен как структура результата**
(план, раздел «Три ошибки основания»). `CategoryNode` копит только числа:
итог, собственные деньги, счётчики строк. У сравнения дополнительно должны
дожить до конца причины неполноты ячейки (`unpriced_rows`, `not_finite_rows`,
`vat_base_unknown`, спека §2.1.3) — а `CategoryNode` причин не различает и не
хранит. Если обнулять `rows_priced` при неизвестной базе НДС, чтобы «дерево
само показало неполноту», факт «цена есть, а ставка неизвестна» становится
неотличим от факта «цены нет вовсе» — это ложная вторая причина, а не итог
свёртки. Поэтому этот модуль ведёт причины ОТДЕЛЬНО, по своей же свёртке
поддерева (`_fold_subtree`), а `build_tree` используется только за тем, для
чего он написан: арифметика итога/собственных денег и порядок узлов.

**Почему `crud.project_passport._direct_totals` не переиспользуется.** Функция
приватна для своего модуля и, что важнее, приводит валовые суммы К ОДНОЙ
ставке показа сметы ДО того, как отдать их вызывающему коду (`restate_gross`
внутри цикла по строкам VIEW). Сравнению нужно нетто — оно единственная ось
сравнения (спека §2.3.1, `AGENTS.md` §10) и служит основанием, из которого
режимы показа (§2.3) выводятся уже ПОСЛЕ роллапа, а не встроены в него.

**Строки VIEW накапливаются, а не присваиваются.** `v_category_totals`
группируется дополнительно по `proposal_id` и его базе НДС (миграция 0012):
на одну смету, статью и источник может прийти НЕСКОЛЬКО строк — например, у
сметы с двумя лотами. `bucket[source] = ...` вместо накопления потерял бы все
строки, кроме последней; замер плана — на стенде дубликатов нет (один лот на
смету), поэтому дефект не ловится живыми данными и проверяется только
искусственной фикстурой.

**Пакетность.** `load_rollups` — ровно четыре запроса на ЛЮБОЙ размер выборки:
справочник статей, сметы выборки, заявленные ставки предложений тех смет,
строки VIEW тех смет. Ни один из четырёх не выполняется в цикле по сметам —
иначе пакетный агрегат имел бы стоимость N+1 запросов там, где выборка растёт.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.project_passport import CATEGORY_TOTALS
from models import Estimate, Lot, Proposal, WorkCategory
from money.vat import effective_display_rate, gross_to_net
from services.category_rollup import (
    SOURCE_ADDITIONAL_WORKS,
    SOURCE_POSITIONS,
    CategoryNode,
    CategoryRef,
    DirectTotals,
    build_tree,
)

__all__ = [
    "SOURCE_ADDITIONAL_WORKS",
    "SOURCE_POSITIONS",
    "net_of",
    "DirectBranch",
    "EstimateRollup",
    "load_rollups",
    "own_net",
]


def net_of(gross: Decimal, base: Decimal) -> Decimal:
    """Тонкая именованная обёртка над `money.vat.gross_to_net`.

    Названа под словарь этого модуля (нетто — основание сравнения, а не один
    из режимов показа, план §«Архитектура»); собственной арифметики не несёт.
    """
    return gross_to_net(gross, base)


@dataclass(frozen=True)
class DirectBranch:
    """Прямые суммы одной ветви источника, НАКОПЛЕННЫЕ по группам VIEW.

    `rows_priced` — исходный `rows_with_amount` VIEW, НИКОГДА не обнуляется
    неизвестной базой НДС: цена и ставка показа — разные факты (спека §2.1.3).
    `rows_vat_base_unknown` считает строки, чья ГРУППА VIEW пришла с
    `vat_rate_base IS NULL` — отдельный счётчик, не смешанный с `rows_priced`.
    """

    net: Decimal | None
    rows: int
    rows_priced: int
    rows_not_finite: int
    rows_vat_base_unknown: int


@dataclass(frozen=True)
class EstimateRollup:
    """Роллап одной сметы: дерево, прямые суммы по источникам, причины неполноты."""

    estimate_id: int
    contract_id: int
    amendment_no: int | None
    display_rate: Decimal | None
    tree: tuple[CategoryNode, ...]
    direct: dict[int, dict[str, DirectBranch]]
    unallocated: dict[str, DirectBranch]
    reasons: dict[int, frozenset[str]]


def own_net(rollup: EstimateRollup, category_id: int) -> Decimal | None:
    """Собственные деньги статьи: positions + additional_works, БЕЗ вычитания.

    `node.total - sum(children)` запрещён и спекой §2.1, и докстрокой
    `category_rollup` — это выдало бы результат вычитания за прямой факт.
    Обе ветви складываются известными слагаемыми (неизвестное — отсутствующее
    слагаемое, не ноль); если обе неизвестны, результат — `None`.
    """
    branches = rollup.direct.get(category_id, {})
    nets = [
        branch.net
        for branch in (branches.get(SOURCE_POSITIONS), branches.get(SOURCE_ADDITIONAL_WORKS))
        if branch is not None and branch.net is not None
    ]
    if not nets:
        return None
    total = nets[0]
    for value in nets[1:]:
        total += value
    return total


# ---------------------------------------------------------------------------
#  Загрузка входов — четыре запроса, каждый на ВСЮ выборку
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _EstimateRow:
    id: int
    contract_id: int
    amendment_no: int | None
    vat_rate_target: Decimal | None
    vat_rate_base_override: Decimal | None


def _load_categories(db: Session) -> list[CategoryRef]:
    """Весь справочник статей — одним запросом, независимо от выборки.

    `build_tree` требует полный плоский список: корни присутствуют ВСЕГДА
    (общий скелет, спека §2.1), и построить это дерево с усечённым списком
    статей нельзя.
    """
    rows = db.execute(
        sa.select(
            WorkCategory.id,
            WorkCategory.code,
            WorkCategory.title,
            WorkCategory.parent_id,
            WorkCategory.is_bucket,
            WorkCategory.sort_order,
        )
    ).all()
    return [CategoryRef(*row) for row in rows]


def _load_estimates(db: Session, contract_ids: Sequence[int]) -> list[_EstimateRow]:
    """Сметы ВСЕХ договоров выборки — одним запросом с `contract_id IN (...)`.

    Порядок задан явно, хотя корзины (задача 4) отбирают сметы по
    `amendment_no`, а не по позиции в списке: без `ORDER BY` PostgreSQL не
    обещает порядок вовсе, и первое же утверждение о порядке роллапов внутри
    договора стало бы плавающим. Исходная смета (`amendment_no IS NULL`) идёт
    первой — тем же чтением «NULL = исходная», что в §4 `AGENTS.md`.
    """
    rows = db.execute(
        sa.select(
            Estimate.id,
            Estimate.contract_id,
            Estimate.amendment_no,
            Estimate.vat_rate_target,
            Estimate.vat_rate_base_override,
        )
        .where(Estimate.contract_id.in_(contract_ids))
        .order_by(
            Estimate.contract_id,
            Estimate.amendment_no.nulls_first(),
            Estimate.id,
        )
    ).all()
    return [_EstimateRow(*row) for row in rows]


def _load_declared_rates(db: Session, estimate_ids: Sequence[int]) -> dict[int, list[Decimal | None]]:
    """Заявленные ставки предложений тех смет — одним запросом.

    Правило единогласия живёт в `effective_display_rate`; здесь список только
    собирается, сырым, без свёртки.
    """
    if not estimate_ids:
        return {}
    rows = db.execute(
        sa.select(Lot.estimate_id, Proposal.vat_rate)
        .select_from(Proposal)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id.in_(estimate_ids))
    ).all()
    by_estimate: dict[int, list[Decimal | None]] = defaultdict(list)
    for estimate_id, vat_rate in rows:
        by_estimate[estimate_id].append(vat_rate)
    return by_estimate


def _load_view_rows(db: Session, estimate_ids: Sequence[int]) -> list[sa.Row]:
    """Строки `v_category_totals` тех смет — одним запросом.

    `CATEGORY_TOTALS` переиспользуется из `crud.project_passport`: это
    отражение VIEW уже сверено с `information_schema`
    (`test_category_totals_view.py::test_declared_view_columns_match_the_database`);
    заводить второе, независимое объявление того же VIEW значило бы держать
    две копии, которые могут разъехаться незаметно.
    """
    if not estimate_ids:
        return []
    return db.execute(
        sa.select(CATEGORY_TOTALS).where(CATEGORY_TOTALS.c.estimate_id.in_(estimate_ids))
    ).all()


# ---------------------------------------------------------------------------
#  Накопление строк VIEW — по ключу «смета × статья × источник»
# ---------------------------------------------------------------------------

@dataclass
class _Acc:
    """Изменяемый аккумулятор одной ветви ДО заморозки в `DirectBranch`."""

    net: Decimal | None = None
    rows: int = 0
    rows_priced: int = 0
    rows_not_finite: int = 0
    rows_vat_base_unknown: int = 0

    def add_row(self, row: sa.Row) -> None:
        self.rows += row.row_count
        self.rows_priced += row.rows_with_amount
        self.rows_not_finite += row.rows_not_finite
        if row.vat_rate_base is None:
            # База неизвестна у ЭТОЙ группы VIEW — все её строки в счётчик
            # "неизвестная база", независимо от того, есть у них цена или нет
            # (это ДРУГОЙ факт, спека §2.1.3).
            self.rows_vat_base_unknown += row.row_count
        elif row.amount is not None:
            net = net_of(row.amount, row.vat_rate_base)
            self.net = net if self.net is None else self.net + net

    def freeze(self) -> DirectBranch:
        return DirectBranch(
            net=self.net,
            rows=self.rows,
            rows_priced=self.rows_priced,
            rows_not_finite=self.rows_not_finite,
            rows_vat_base_unknown=self.rows_vat_base_unknown,
        )


def _accumulate(view_rows: Sequence[sa.Row]) -> dict[int, dict[int | None, dict[str, _Acc]]]:
    """Накопление строк VIEW по (estimate_id, work_category_id, source).

    НАКОПЛЕНИЕ, а не присваивание (план, «Три ошибки основания», ошибка 1):
    несколько строк VIEW на одну статью и источник — штатный случай сметы из
    нескольких лотов/предложений (миграция 0012 группирует ещё по
    `proposal_id`/базе НДС).
    """
    by_estimate: dict[int, dict[int | None, dict[str, _Acc]]] = defaultdict(dict)
    for row in view_rows:
        by_category = by_estimate[row.estimate_id]
        by_source = by_category.setdefault(row.work_category_id, {})
        acc = by_source.get(row.source)
        if acc is None:
            acc = _Acc()
            by_source[row.source] = acc
        acc.add_row(row)
    return by_estimate


# ---------------------------------------------------------------------------
#  Причины неполноты — свёртка по поддереву, ОТДЕЛЬНО от build_tree
# ---------------------------------------------------------------------------

#: (rows, rows_priced, rows_not_finite, rows_vat_base_unknown)
_Counts = tuple[int, int, int, int]

_ZERO_COUNTS: _Counts = (0, 0, 0, 0)


def _own_counts(direct: dict[int, dict[str, DirectBranch]]) -> dict[int, _Counts]:
    """Счётчики СОБСТВЕННОЙ (не поддерева) статьи — из обеих её ветвей."""
    result: dict[int, _Counts] = {}
    for category_id, branches in direct.items():
        rows = sum(branch.rows for branch in branches.values())
        rows_priced = sum(branch.rows_priced for branch in branches.values())
        rows_not_finite = sum(branch.rows_not_finite for branch in branches.values())
        rows_vat_base_unknown = sum(branch.rows_vat_base_unknown for branch in branches.values())
        result[category_id] = (rows, rows_priced, rows_not_finite, rows_vat_base_unknown)
    return result


def _fold_subtree_counts(
    categories: Sequence[CategoryRef], own: dict[int, _Counts]
) -> dict[int, _Counts]:
    """Свернуть счётчики по поддереву тем же обходом дерева, что `build_tree`.

    Свёртка своя, а не поверх готового дерева, по двум причинам, и первая
    достаточна сама по себе: `CategoryNode` причин неполноты не несёт вовсе
    (`vat_base_unknown` в нём нет ни в каком виде), поэтому второй обход
    справочника нужен независимо от того, что видно в дереве.

    Вторая — развязка контрактов. `build_tree` показывает глубже корня только
    узлы с `rows > 0`; это правило ПОКАЗА паспорта, а не факт о неполноте.
    Считая причины по `node.children`, мы привязали бы состав причин к чужому
    решению о том, что прятать, и правка паспорта молча поехала бы в сравнение.

    Оговорка, чтобы её не пришлось выводить заново: сегодня видимость ничего
    не теряет — все три счётчика растут не быстрее `rows` (`rows_with_amount`
    и `rows_not_finite` — COUNT с FILTER против COUNT(*) в VIEW,
    `rows_vat_base_unknown` — тот же `row_count` группы), поэтому у узла с
    `rows == 0` они нулевые, и отброшенный узел причин не несёт. То есть
    основание здесь — развязка, а не спасение факта: обосновывать отдельную
    свёртку потерей данных значило бы утверждать то, чего проверить нельзя.
    """
    children_by_parent: dict[int, list[CategoryRef]] = defaultdict(list)
    roots: list[CategoryRef] = []
    for category in categories:
        if category.parent_id is None:
            roots.append(category)
        else:
            children_by_parent[category.parent_id].append(category)

    result: dict[int, _Counts] = {}

    def visit(ref: CategoryRef) -> _Counts:
        total = list(own.get(ref.id, _ZERO_COUNTS))
        for child in children_by_parent.get(ref.id, ()):
            child_total = visit(child)
            for i in range(4):
                total[i] += child_total[i]
        frozen = (total[0], total[1], total[2], total[3])
        result[ref.id] = frozen
        return frozen

    for root in roots:
        visit(root)
    return result


def _reasons_from_counts(totals: dict[int, _Counts]) -> dict[int, frozenset[str]]:
    """Причины неполноты §2.1.3, СПИСКОМ — совмещаются, не приоритезируются."""
    reasons: dict[int, frozenset[str]] = {}
    for category_id, (rows, rows_priced, rows_not_finite, rows_vat_base_unknown) in totals.items():
        codes: set[str] = set()
        if rows_priced < rows:
            codes.add("unpriced_rows")
        if rows_not_finite > 0:
            codes.add("not_finite_rows")
        if rows_vat_base_unknown > 0:
            codes.add("vat_base_unknown")
        reasons[category_id] = frozenset(codes)
    return reasons


def _direct_totals_view(direct: dict[int, dict[str, DirectBranch]]) -> dict[int, dict[str, DirectTotals]]:
    """Представление нетто-ветвей в виде `DirectTotals` — вход `build_tree`.

    `build_tree` не меняется (Global Constraints плана); ему нужен
    `Mapping[int, Mapping[str, DirectTotals]]`, а наши ветви несут нетто и
    отдельные причины. Оборачивание — чистое проецирование полей, без новой
    арифметики.
    """
    return {
        category_id: {
            source: DirectTotals(
                amount=branch.net,
                row_count=branch.rows,
                rows_with_amount=branch.rows_priced,
                rows_not_finite=branch.rows_not_finite,
            )
            for source, branch in branches.items()
        }
        for category_id, branches in direct.items()
    }


# ---------------------------------------------------------------------------
#  Публичная сборка
# ---------------------------------------------------------------------------

def load_rollups(db: Session, contract_ids: Sequence[int]) -> dict[int, list[EstimateRollup]]:
    """Роллапы всех смет выборки, ОДНИМ запросом к каждому из четырёх входов.

    Ключ результата — `contract_id`; договор без смет получает пустой список
    (а не отсутствует в словаре — вызывающему коду не придётся гадать про
    KeyError на договоре, который есть в выборке, но ещё не разобран).
    """
    contract_ids = list(contract_ids)
    result: dict[int, list[EstimateRollup]] = {contract_id: [] for contract_id in contract_ids}
    if not contract_ids:
        return result

    categories = _load_categories(db)
    estimates = _load_estimates(db, contract_ids)
    if not estimates:
        return result

    estimate_ids = [estimate.id for estimate in estimates]
    declared_rates = _load_declared_rates(db, estimate_ids)
    view_rows = _load_view_rows(db, estimate_ids)
    accumulated = _accumulate(view_rows)

    for estimate in estimates:
        by_category = accumulated.get(estimate.id, {})

        direct: dict[int, dict[str, DirectBranch]] = {}
        unallocated: dict[str, DirectBranch] = {}
        for category_id, by_source in by_category.items():
            frozen_branches = {source: acc.freeze() for source, acc in by_source.items()}
            if category_id is None:
                # «Нераспределённое» — вынимается ДО build_tree, он его не пускает
                # (спека §2.1.4, план п. 6 требований).
                unallocated = frozen_branches
            else:
                direct[category_id] = frozen_branches

        own_counts = _own_counts(direct)
        subtree_counts = _fold_subtree_counts(categories, own_counts)
        reasons = _reasons_from_counts(subtree_counts)

        tree = build_tree(categories, _direct_totals_view(direct))

        rollup = EstimateRollup(
            estimate_id=estimate.id,
            contract_id=estimate.contract_id,
            amendment_no=estimate.amendment_no,
            display_rate=effective_display_rate(
                estimate.vat_rate_target,
                estimate.vat_rate_base_override,
                declared_rates.get(estimate.id, []),
            ),
            tree=tree,
            direct=direct,
            unallocated=unallocated,
            reasons=reasons,
        )
        result.setdefault(estimate.contract_id, []).append(rollup)

    return result
