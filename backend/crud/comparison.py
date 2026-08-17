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
    "ABSENT",
    "ZERO",
    "VALUE",
    "OWN_ROW_TITLE",
    "UNALLOCATED_ROW_TITLE",
    "net_of",
    "DirectBranch",
    "EstimateRollup",
    "load_rollups",
    "own_net",
    "RowRef",
    "CellNet",
    "build_rows",
    "cell_net",
    "cells_for",
]

#: Состояния ячейки сравнения (спека §2.1.2): статьи нет ("absent") — не то же
#: самое, что статья есть и расценена в ноль ("zero"). "value" — есть число
#: (в т.ч. когда ячейка неполна и число погашено `incomplete_reasons`, спека
#: §2.1.3 — тогда `net is None`, но состояние всё равно не ABSENT).
ABSENT = "absent"
ZERO = "zero"
VALUE = "value"

#: Заголовки синтетических строк (спека §2.1, §2.1.4). Названы здесь, а не в
#: `RowRef`, потому что оба заголовка — константы одного словаря экрана.
OWN_ROW_TITLE = "Без подстатьи"
UNALLOCATED_ROW_TITLE = "Нераспределённое"


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
    """Роллап одной сметы: дерево, прямые суммы по источникам, причины неполноты.

    `reasons` — причины ПОДДЕРЕВА (задача 2). `own_reasons` и
    `unallocated_reasons` — отдельный словарь причин: собственная строка узла
    («Без подстатьи») и остаток («Нераспределённое») не являются поддеревом, и
    подмешивать им причины детей значило бы гасить «Без подстатьи» чужой
    неполнотой (задача 3, спека §2.1.3).
    """

    estimate_id: int
    contract_id: int
    amendment_no: int | None
    display_rate: Decimal | None
    tree: tuple[CategoryNode, ...]
    direct: dict[int, dict[str, DirectBranch]]
    unallocated: dict[str, DirectBranch]
    reasons: dict[int, frozenset[str]]
    own_reasons: dict[int, frozenset[str]]
    unallocated_reasons: frozenset[str]


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


def _counts_from_branches(branches: dict[str, DirectBranch]) -> _Counts:
    """Счётчики одного узла из ЕГО ветвей источников (обе, без свёртки поддерева).

    Общий примитив для «своих» счётчиков категории (`_own_counts`) и для
    «Нераспределённого» (задача 3) — оба читают ровно эти четыре счётчика из
    словаря `source -> DirectBranch`, разницы между ними в подсчёте нет.
    """
    rows = sum(branch.rows for branch in branches.values())
    rows_priced = sum(branch.rows_priced for branch in branches.values())
    rows_not_finite = sum(branch.rows_not_finite for branch in branches.values())
    rows_vat_base_unknown = sum(branch.rows_vat_base_unknown for branch in branches.values())
    return (rows, rows_priced, rows_not_finite, rows_vat_base_unknown)


def _own_counts(direct: dict[int, dict[str, DirectBranch]]) -> dict[int, _Counts]:
    """Счётчики СОБСТВЕННОЙ (не поддерева) статьи — из обеих её ветвей."""
    return {category_id: _counts_from_branches(branches) for category_id, branches in direct.items()}


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


def _reasons_from_one(counts: _Counts) -> frozenset[str]:
    """Причины неполноты §2.1.3 из ОДНОГО набора счётчиков, СПИСКОМ.

    Причины совмещаются, а не приоритезируются (DoD 8д) — множество, а не
    первая сработавшая проверка.
    """
    rows, rows_priced, rows_not_finite, rows_vat_base_unknown = counts
    codes: set[str] = set()
    if rows_priced < rows:
        codes.add("unpriced_rows")
    if rows_not_finite > 0:
        codes.add("not_finite_rows")
    if rows_vat_base_unknown > 0:
        codes.add("vat_base_unknown")
    return frozenset(codes)


def _reasons_from_counts(totals: dict[int, _Counts]) -> dict[int, frozenset[str]]:
    """Причины неполноты §2.1.3 для КАЖДОЙ статьи словаря счётчиков."""
    return {category_id: _reasons_from_one(counts) for category_id, counts in totals.items()}


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
        # Причины СОБСТВЕННОЙ строки узла («Без подстатьи») и остатка
        # («Нераспределённое») — из их же счётчиков, а не из поддерева
        # (задача 3): это разные строки экрана, и подмешивать им чужую
        # неполноту значило бы гасить «Без подстатьи» проблемой ребёнка.
        own_reasons = _reasons_from_counts(own_counts)
        unallocated_reasons = _reasons_from_one(_counts_from_branches(unallocated))

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
            own_reasons=own_reasons,
            unallocated_reasons=unallocated_reasons,
        )
        result.setdefault(estimate.contract_id, []).append(rollup)

    return result


# ---------------------------------------------------------------------------
#  Союз узлов выборки, состояния ячеек, синтетические строки (спека §2.1)
# ---------------------------------------------------------------------------
#
# Это следующий слой поверх `load_rollups`, а не отдельный проход по базе:
# всё, что ему нужно, уже лежит в деревьях и словарях `EstimateRollup`.
#
# «Союз» — не одно дерево, а СЛИЯНИЕ структуры деревьев ВСЕХ роллапов выборки.
# `build_tree` строит дерево одной сметы и обрезает не-корневые узлы с нулём
# строк (`services.category_rollup._build_node`, `visible_children`); корни же
# присутствуют в дереве ВСЕГДА, даже с нулём строк (общий скелет паспорта). Из
# этого следует всё, что ниже:
#
#   * не-корневой узел, встреченный хоть в ОДНОМ дереве выборки, уже по
#     построению `build_tree` имеет там `rows > 0` — значит он «в союзе» самим
#     фактом появления; отдельно проверять нечего;
#   * корень нужно проверять явно: он встречается всегда, но может быть с
#     `rows == 0` во всех деревьях выборки — тогда он в союз не попадает
#     (спека §2.1.1, задача 3 план — «пустой у всех корень тоже выпадает»).
#
# Поэтому «в союзе» сводится к одной проверке: `node.rows > 0` хоть у одного
# встреченного экземпляра узла, независимо от того, корень это или нет.

@dataclass(frozen=True)
class RowRef:
    """Строка сравнения: статья классификатора либо синтетическая строка.

    `level` — глубина+1 (корни статей — уровень 1), теми же единицами, что
    порядок раскрытия у паспорта. `parent_code` — код родителя либо `None`
    (у корня и у «Нераспределённого»).
    """

    kind: str  # "category" | "own" | "unallocated"
    category_id: int | None
    code: str
    title: str
    level: int
    parent_code: str | None


@dataclass(frozen=True)
class CellNet:
    """Ячейка сравнения ОДНОГО договора над ОДНОЙ строкой (спека §2.1.2, §2.1.3).

    У `net is None` РОВНО ДВЕ причины, и различает их `state`, а не сам `None`:

    * `state == ABSENT` — статьи нет в сметах договора; причин при этом нет
      вовсе (там, где статьи нет, нечему быть неполным);
    * `state == VALUE` при непустых `incomplete_reasons` — статья есть, но
      число погашено: ячейка пуста ЦЕЛИКОМ, частичная сумма не просачивается
      никогда (спека §2.1.3, единица неполноты — ячейка).

    Смешивать их нельзя — это и есть требование §2.1.2 «прочерк и ноль (и
    погашенное число) суть разные факты»; UI подписывает их по-разному.
    При непустых причинах `net` всегда `None`, но обратное неверно.
    """

    net: Decimal | None
    state: str
    incomplete_reasons: frozenset[str]


class _UnionNode:
    """Рабочий узел слияния деревьев выборки — накопитель, не результат.

    `ever_has_rows` — встречен ли этот узел хоть в ОДНОМ дереве выборки с
    `rows > 0` (для не-корневых это гарантировано самим фактом появления в
    `node.children`, для корней — нет, проверяется явно). `child_ids` —
    ОБЪЕДИНЕНИЕ детей узла по всем деревьям, где он встречен: у разных смет
    выборки под одним узлом могут быть видны разные дети.
    """

    __slots__ = ("ref", "ever_has_rows", "child_ids")

    def __init__(self, ref: CategoryRef) -> None:
        self.ref = ref
        self.ever_has_rows = False
        self.child_ids: set[int] = set()


def _collect_union_nodes(rollups: dict[int, list[EstimateRollup]]) -> dict[int, _UnionNode]:
    """Слить структуру деревьев ВСЕХ роллапов выборки в один граф узлов."""
    nodes: dict[int, _UnionNode] = {}

    def visit(node: CategoryNode) -> None:
        entry = nodes.get(node.ref.id)
        if entry is None:
            entry = _UnionNode(node.ref)
            nodes[node.ref.id] = entry
        if node.rows > 0:
            entry.ever_has_rows = True
        for child in node.children:
            entry.child_ids.add(child.ref.id)
            visit(child)

    for contract_rollups in rollups.values():
        for rollup in contract_rollups:
            for root in rollup.tree:
                visit(root)

    return nodes


def _categories_with_direct_rows(rollups: dict[int, list[EstimateRollup]]) -> set[int]:
    """Статьи, у которых хоть у ОДНОГО договора выборки есть прямые строки.

    Счётчик, не сумма (план задача 3, правило 3): нулевая прямая сумма при
    непустом счётчике — тоже факт присутствия «Без подстатьи».
    """
    result: set[int] = set()
    for contract_rollups in rollups.values():
        for rollup in contract_rollups:
            for category_id, branches in rollup.direct.items():
                if sum(branch.rows for branch in branches.values()) > 0:
                    result.add(category_id)
    return result


def _has_unallocated_rows(rollups: dict[int, list[EstimateRollup]]) -> bool:
    """Остаток непуст хоть у ОДНОГО договора выборки (правило 4)."""
    for contract_rollups in rollups.values():
        for rollup in contract_rollups:
            if sum(branch.rows for branch in rollup.unallocated.values()) > 0:
                return True
    return False


def build_rows(rollups: dict[int, list[EstimateRollup]]) -> list[RowRef]:
    """Союз узлов ВЫБОРКИ в порядке `sort_order`, с синтетическими строками.

    Порядок (фиксирован планом задачи 3, совпадает с чтением
    `CategoryTable.tsx` паспорта): для каждой статьи — её строка, затем ВСЕ её
    потомки (тем же правилом рекурсивно), затем строка «Без подстатьи» этой
    статьи — ПОСЛЕДНЕЙ среди её содержимого. «Нераспределённое» — последняя
    строка всей таблицы.
    """
    nodes = _collect_union_nodes(rollups)
    union_ids = {category_id for category_id, entry in nodes.items() if entry.ever_has_rows}
    direct_rows_ids = _categories_with_direct_rows(rollups)

    rows: list[RowRef] = []

    def visit(category_id: int, level: int, parent_code: str | None) -> None:
        entry = nodes[category_id]
        ref = entry.ref
        rows.append(RowRef(
            kind="category", category_id=ref.id, code=ref.code, title=ref.title,
            level=level, parent_code=parent_code,
        ))

        children = sorted(
            (child_id for child_id in entry.child_ids if child_id in union_ids),
            key=lambda child_id: nodes[child_id].ref.sort_order,
        )
        for child_id in children:
            visit(child_id, level + 1, ref.code)

        if ref.id in direct_rows_ids and children:
            rows.append(RowRef(
                kind="own", category_id=ref.id, code=f"{ref.code}::own",
                title=OWN_ROW_TITLE, level=level + 1, parent_code=ref.code,
            ))

    roots = sorted(
        (category_id for category_id, entry in nodes.items()
         if entry.ref.parent_id is None and category_id in union_ids),
        key=lambda category_id: nodes[category_id].ref.sort_order,
    )
    for root_id in roots:
        visit(root_id, level=1, parent_code=None)

    if _has_unallocated_rows(rollups):
        rows.append(RowRef(
            kind="unallocated", category_id=None, code="::unallocated",
            title=UNALLOCATED_ROW_TITLE, level=1, parent_code=None,
        ))

    return rows


def _find_node(tree: Sequence[CategoryNode], category_id: int) -> CategoryNode | None:
    """Найти узел статьи в дереве ОДНОЙ сметы (или `None`, если его там нет)."""
    for node in tree:
        if node.ref.id == category_id:
            return node
        found = _find_node(node.children, category_id)
        if found is not None:
            return found
    return None


def _resolve_cell(total_rows: int, reasons: frozenset[str], net_parts: list[Decimal]) -> CellNet:
    """Общая развязка состояния ячейки из накопленных счётчика/причин/сумм.

    `ABSENT`, если строки не было ни у одной сметы договора. Иначе: причины
    непусты -> ячейка гасится ЦЕЛИКОМ (`net=None`, спека §2.1.3); причин нет ->
    число, `ZERO`, если оно равно нулю, иначе `VALUE`.

    Суммирование — ТЕМ ЖЕ приёмом, что `own_net` (не встроенный `sum`,
    начинающий с `0`): сложение `Decimal` округляется по АМБИЕНТНОМУ контексту
    (умолчание — 28 значащих цифр), а `money.vat.gross_to_net` считает в своём,
    более точном `_VAT_CONTEXT`. `0 + x` под чужим контекстом тихо срезал бы
    точность первого слагаемого даже там, где складывать было нечего.
    """
    if total_rows == 0:
        return CellNet(net=None, state=ABSENT, incomplete_reasons=frozenset())
    if reasons:
        return CellNet(net=None, state=VALUE, incomplete_reasons=reasons)
    if not net_parts:
        # НЕДОСТИЖИМО по инварианту VIEW, и потому не «на всякий случай», а
        # громкий отказ. Строки есть, причин нет — значит все строки расценены
        # (иначе была бы `unpriced_rows`) и база НДС известна у каждой группы
        # (иначе `vat_base_unknown`), а тогда слагаемое существует: `amount is
        # None` в VIEW равносильно `rows_with_amount == 0`.
        # Подставить здесь ноль было бы худшим из ответов: ячейка получила бы
        # состояние ZERO, то есть сказала бы «статья есть и стоит ноль» там,
        # где сумма НЕИЗВЕСТНА, — ровно та подмена, которую §2.1.2 запрещает.
        # Форма отказа — как у `worker_database_url` в conftest: громкий
        # RuntimeError вместо тихого фолбэка.
        raise RuntimeError(
            f"строки есть ({total_rows}), причин неполноты нет, а слагаемых нет — "
            "нарушен инвариант VIEW «amount is None ⟺ rows_with_amount == 0»"
        )
    net = net_parts[0]
    for value in net_parts[1:]:
        net += value
    return CellNet(net=net, state=ZERO if net == 0 else VALUE, incomplete_reasons=frozenset())


def _cell_for_category(rollups: Sequence[EstimateRollup], category_id: int) -> CellNet:
    """Ячейка статьи: суммируется по ВСЕМ сметам договора (§2.1.3, поддерево)."""
    total_rows = 0
    reasons: set[str] = set()
    net_parts: list[Decimal] = []
    for rollup in rollups:
        node = _find_node(rollup.tree, category_id)
        if node is not None:
            total_rows += node.rows
            if node.total is not None:
                net_parts.append(node.total)
        reasons |= rollup.reasons.get(category_id, frozenset())
    return _resolve_cell(total_rows, frozenset(reasons), net_parts)


def _cell_for_own(rollups: Sequence[EstimateRollup], parent_id: int) -> CellNet:
    """Ячейка «Без подстатьи»: суммируются СВОИ (не поддерева) деньги статьи."""
    total_rows = 0
    reasons: set[str] = set()
    net_parts: list[Decimal] = []
    for rollup in rollups:
        branches = rollup.direct.get(parent_id, {})
        total_rows += sum(branch.rows for branch in branches.values())
        reasons |= rollup.own_reasons.get(parent_id, frozenset())
        value = own_net(rollup, parent_id)
        if value is not None:
            net_parts.append(value)
    return _resolve_cell(total_rows, frozenset(reasons), net_parts)


def _cell_for_unallocated(rollups: Sequence[EstimateRollup]) -> CellNet:
    """Ячейка «Нераспределённое»: суммируется остаток по всем сметам договора."""
    total_rows = 0
    reasons: set[str] = set()
    net_parts: list[Decimal] = []
    for rollup in rollups:
        branches = rollup.unallocated
        total_rows += sum(branch.rows for branch in branches.values())
        reasons |= rollup.unallocated_reasons
        for branch in branches.values():
            if branch.net is not None:
                net_parts.append(branch.net)
    return _resolve_cell(total_rows, frozenset(reasons), net_parts)


def cell_net(rollups: Sequence[EstimateRollup], row: RowRef) -> CellNet:
    """Ячейка ОДНОГО договора над ОДНОЙ строкой — примитив для корзин задачи 4.

    Принимает роллапы ОДНОГО договора и не предполагает, что это ВСЕ его
    сметы: задача 4 передаст сюда сметы одной корзины (ДГП либо ДС), эта
    функция не завязана на то, что видела все сметы контракта разом.
    """
    if row.kind == "category":
        return _cell_for_category(rollups, row.category_id)
    if row.kind == "own":
        return _cell_for_own(rollups, row.category_id)
    if row.kind == "unallocated":
        return _cell_for_unallocated(rollups)
    raise ValueError(f"неизвестный вид строки: {row.kind!r}")


def cells_for(rollups: dict[int, list[EstimateRollup]], *, code: str) -> dict[int, CellNet]:
    """Ячейки ОДНОЙ строки (по коду) для ВСЕХ договоров выборки.

    `code` — код статьи либо синтетический код (`"<код>::own"`,
    `"::unallocated"`). Строка ищется через `build_rows`, чтобы не заводить
    вторую, отдельную логику резолюции кода в `RowRef` — здесь она была бы
    ровно той же самой.
    """
    rows = build_rows(rollups)
    matches = [row for row in rows if row.code == code]
    if not matches:
        raise ValueError(f"строки с кодом {code!r} нет в союзе этой выборки")
    row = matches[0]
    return {
        contract_id: cell_net(contract_rollups, row)
        for contract_id, contract_rollups in rollups.items()
    }
