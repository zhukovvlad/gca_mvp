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

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Context, Decimal, DivisionByZero, InvalidOperation, Overflow, localcontext

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.common import DomainError, iso
from crud.project_passport import CATEGORY_TOTALS
from models import Contract, Contractor, Estimate, Lot, ObjectModel, Proposal, RateClass, WorkCategory
from money.vat import effective_display_rate, gross_to_net, net_to_gross
from parser.summary_block import ARITHMETIC_PRECISION
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
    "BUCKET_BASE",
    "BUCKET_AMENDMENTS",
    "BUCKET_TOTAL",
    "VAT_MODE_OWN",
    "VAT_MODE_SINGLE",
    "VAT_MODE_NET",
    "REASON_DISPLAY_RATE_UNDEFINED",
    "net_of",
    "net_to_gross",
    "DirectBranch",
    "EstimateRollup",
    "load_rollups",
    "own_net",
    "RowRef",
    "CellNet",
    "build_rows",
    "cell_net",
    "cells_for",
    "BucketCell",
    "Cell",
    "build_comparison",
    "rate_options",
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

#: Три корзины сравнения (спека §2.2): базовый договор, допсоглашения, итог.
BUCKET_BASE = "base"
BUCKET_AMENDMENTS = "amendments"
BUCKET_TOTAL = "total"

#: Три режима показа НДС (спека §2.3). Ось сравнения (нетто) от режима не
#: зависит НИКОГДА (AGENTS.md §10, спека §2.5 правило 2) — режим определяет
#: только множитель, применяемый к готовому нетто на границе показа.
VAT_MODE_OWN = "own"
VAT_MODE_SINGLE = "single"
VAT_MODE_NET = "net"

#: Причина неполноты, зависящая от режима показа — единственная из четырёх
#: (спека §2.1.3 п.4, §2.3.2). Гасит `shown`/`shown_per_sqm` ЯЧЕЙКИ корзины,
#: а не `net`/`net_per_sqm`: ось сравнения остаётся вычислимой и в режиме
#: «своя ставка» (иначе медиана «своей ставки» разошлась бы с медианой
#: «нетто», а DoD 10 требует их тождества).
REASON_DISPLAY_RATE_UNDEFINED = "display_rate_undefined"

#: Контекст деления для ₽/м² и медианы — тот же приём и тот же
#: `ARITHMETIC_PRECISION`, что `money.vat._VAT_CONTEXT`: явные трапы БЕЗ
#: `Inexact` (деление почти никогда не представимо конечной десятичной дробью).
_DIV_CONTEXT = Context(prec=ARITHMETIC_PRECISION, traps=[Overflow, DivisionByZero, InvalidOperation])


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

    `base_rates` (задача 4) — известные (не NULL) базы НДС групп VIEW этой
    сметы, СОБСТВЕННЫЙ обход, не производная от `direct`/`unallocated`:
    `rate_options` (спека §2.3, второй набор) нужен резерв списка ставок
    ИМЕННО из баз групп, включая группы смет без определённой ставки показа.
    Без этого поля `rate_options` пришлось бы второй раз читать VIEW, ломая
    единый путь чтения (план, «Архитектура»).
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
    base_rates: frozenset[Decimal]


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


def _collect_base_rates(view_rows: Sequence[sa.Row]) -> dict[int, set[Decimal]]:
    """Известные (не NULL) базы НДС групп VIEW, по смете (задача 4, `rate_options`).

    Отдельный обход строк VIEW, а не побочный эффект `_accumulate`:
    `EstimateRollup.base_rates` — вход `rate_options`, и не должен зависеть от
    того, как накапливаются деньги (сумма/причины) в той функции.
    """
    result: dict[int, set[Decimal]] = defaultdict(set)
    for row in view_rows:
        if row.vat_rate_base is not None:
            result[row.estimate_id].add(row.vat_rate_base)
    return result


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

    **«Без цены» считается за вычетом НЕФИНИТНЫХ строк, и это осознанное
    отступление от буквы спеки §2.1.3 п.1** (найдено финальным ревью ветки).
    Спека пишет условие как `rows_priced < rows`, но `rows_with_amount` в VIEW
    (миграция 0012) исключает `NaN`/`±Infinity` наравне с `NULL` — значит на
    строке, у которой цена ЕСТЬ и она нефинитна, `rows_priced < rows`
    срабатывает тоже, и ячейка получала бы ВТОРУЮ, ложную причину: «без цены»
    там, где строк без цены ноль. Утверждение о данных, которого нет, — хуже
    отсутствующего: экран и лист называют причины словами, и читатель искал бы
    непроставленную цену.

    Правильное вычитание уже существует в проекте и различает эти факты:
    свёртка паспорта считает «без цены» как `rows - rows_priced -
    rows_not_finite` (`tests/unit/test_category_rollup.py` прямо утверждает
    `missing_price != rows_not_finite`), а паспортная подпись печатает «без
    цены: N, с ошибкой: M» раздельно. Здесь то же самое.
    """
    rows, rows_priced, rows_not_finite, rows_vat_base_unknown = counts
    codes: set[str] = set()
    if rows_priced + rows_not_finite < rows:
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
    base_rates_by_estimate = _collect_base_rates(view_rows)

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
            base_rates=frozenset(base_rates_by_estimate.get(estimate.id, ())),
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


# ---------------------------------------------------------------------------
#  Корзины, режимы НДС, медианы (спека §2.2, §2.3, §2.5) — задача 4
# ---------------------------------------------------------------------------
#
# Слой поверх задач 2-3: КАЖДАЯ строка (статья, «Без подстатьи»,
# «Нераспределённое») и «Итого по договору» считаются СРАЗУ для трёх корзин
# (ДГП/ДС/Итого) — экран выбирает представление, Excel (задача 6) получает
# готовые три с отдельными медианами (план, «интерфейс, исправленный по
# ревью»). Медиана всегда по нетто (AGENTS.md §10, спека §2.5 правило 2),
# поэтому режим показа влияет только на `shown`/`shown_per_sqm`, никогда на
# `net`/`net_per_sqm`/`deviation_pct` — это и даёт тождество DoD 10.


@dataclass(frozen=True)
class BucketCell:
    """Ячейка ОДНОЙ корзины ОДНОГО договора над ОДНОЙ строкой (спека §2.2, §2.3).

    ДВЕ удельные величины вместо одной (ревью плана): `net_per_sqm` — вход
    медианы, не зависящий от режима; `shown_per_sqm` — то, что видит человек,
    следует режиму показа. Одно поле заставило бы клиента либо досчитывать
    часть агрегата на своей стороне, либо показывать нетто рядом с валовой
    суммой.
    """

    net: Decimal | None
    shown: Decimal | None
    net_per_sqm: Decimal | None
    shown_per_sqm: Decimal | None
    state: str
    deviation_pct: Decimal | None
    incomplete_reasons: frozenset[str]


@dataclass(frozen=True)
class Cell:
    """Ячейка договора над строкой — ВСЕ ТРИ корзины сразу (спека §2.2).

    Один агрегат считает три корзины разом, а не выбранную режимом
    представления — иначе Excel не мог бы получить их одним и тем же расчётом,
    что спека §2.7 требует явно («один агрегат — два представления»).
    """

    base: BucketCell
    amendments: BucketCell
    total: BucketCell


@dataclass(frozen=True)
class _MedianResult:
    """Медиана строки/корзины и метаданные, нужные экрану (план, задача 4).

    `comparable_count` и `contract_ids` отдаются ВСЕГДА, даже когда
    `value is None` (меньше трёх сопоставимых, спека §2.5 правило 5): экрану
    нужно число, чтобы написать «сопоставимых меньше трёх», а не просто
    скрыть подсветку молча.
    """

    value: Decimal | None
    comparable_count: int
    contract_ids: list[int]


def _split_buckets(rollups: Sequence[EstimateRollup]) -> dict[str, list[EstimateRollup]]:
    """Разложить сметы ОДНОГО договора по корзинам (спека §2.2).

    `base` — сметы без допсоглашения (`amendment_no IS NULL`); `amendments` —
    сметы допсоглашений; `total` — все сметы разом. DoD 5 (база + допы = итог)
    следует из этого разбиения по построению: `total` — это буквально
    конкатенация `base` и `amendments`, не отдельный проход по договору.
    """
    base = [rollup for rollup in rollups if rollup.amendment_no is None]
    amendments = [rollup for rollup in rollups if rollup.amendment_no is not None]
    return {BUCKET_BASE: base, BUCKET_AMENDMENTS: amendments, BUCKET_TOTAL: list(rollups)}


def _upgrade_absent_to_zero(bucket_axis: CellNet, contract_axis: CellNet) -> CellNet:
    """Прочерк корзины при непустом договоре в целом — это НОЛЬ (спека §2.2).

    Присутствие статьи решается на уровне ДОГОВОРА (`contract_axis`, уже
    вычислена по ВСЕМ сметам — корзина «Итого»), нулевость — на уровне
    КОРЗИНЫ. Статьи нет ни в одной смете договора -> прочерк во всех трёх
    корзинах (обе оси ABSENT, апгрейд не срабатывает). Статья в договоре
    есть, а в этой корзине её нет (бакет пуст) -> ноль, а не прочерк.
    """
    if bucket_axis.state == ABSENT and contract_axis.state != ABSENT:
        return CellNet(net=Decimal("0"), state=ZERO, incomplete_reasons=frozenset())
    return bucket_axis


def _grand_total_cell(rollups: Sequence[EstimateRollup]) -> CellNet:
    """Ячейка «Итого по договору»: сумма ВСЕХ статей плюс «Нераспределённое»
    (спека §2.1.4, DoD 5в — остаток обязан войти в итог, иначе деньги исчезают
    из таблицы молча).

    Корни `rollup.tree` — ВСЕГДА полный, непересекающийся раздел
    классификатора (`build_tree` возвращает их безусловно, задача 2), а
    `rollup.reasons` уже свёрнут ПО ПОДДЕРЕВУ (`_fold_subtree_counts`) —
    значит объединение причин и строк по одним лишь корням покрывает ВЕСЬ
    учтённый список статей без повторного обхода дерева. Форма — зеркало
    `_cell_for_category`/`_cell_for_unallocated`: тот же примитив
    `_resolve_cell`, те же гарантии (накопление НЕ через builtin `sum`,
    неизвестное слагаемое пропускается, а не подставляется нулём).
    """
    total_rows = 0
    reasons: set[str] = set()
    net_parts: list[Decimal] = []
    for rollup in rollups:
        for root in rollup.tree:
            total_rows += root.rows
            if root.total is not None:
                net_parts.append(root.total)
            reasons |= rollup.reasons.get(root.ref.id, frozenset())
        total_rows += sum(branch.rows for branch in rollup.unallocated.values())
        reasons |= rollup.unallocated_reasons
        for branch in rollup.unallocated.values():
            if branch.net is not None:
                net_parts.append(branch.net)
    return _resolve_cell(total_rows, frozenset(reasons), net_parts)


def _single_rollup_axis(rollup: EstimateRollup, row: RowRef | None) -> CellNet:
    """Нетто-ячейка ОДНОЙ сметы над строкой (`row is None` — «Итого»).

    Примитив режима «своя ставка» (§2.3.2): пересчёт в ставку показа
    делается НА СМЕТУ до суммирования в корзину, поэтому нужно нетто именно
    одной сметы, а не готовой суммы бакета — «ставки корзины» не существует.
    """
    if row is None:
        return _grand_total_cell([rollup])
    return cell_net([rollup], row)


def _own_mode_shown(
    bucket_rollups: Sequence[EstimateRollup], row: RowRef | None
) -> tuple[Decimal | None, bool]:
    """Валовая сумма в режиме «своя ставка»: множитель НА КАЖДУЮ смету ДО
    суммы в корзину (спека §2.3.2) — у корзины ДС может быть несколько смет
    (ДС №1, №2) с разными ставками, и «ставки корзины» не существует.

    Возвращает `(сумма | None, гасит_ли_причина)`. Причина
    `display_rate_undefined` гасит ЯЧЕЙКУ, а не столбец (третий круг ревью):
    смета БЕЗ определённой ставки показа гасит, только если она НЕПУСТА по
    этой строке (`state != ABSENT`, т.е. реально вносит в неё строки) — смета
    из другой корзины сюда вообще не попадает (другой список `bucket_rollups`),
    а смета без строк по этой статье пропускается (`continue`) до проверки
    ставки, поэтому не гасит ничего.
    """
    has_undefined = False
    parts: list[Decimal] = []
    for rollup in bucket_rollups:
        single = _single_rollup_axis(rollup, row)
        if single.state == ABSENT:
            continue
        if rollup.display_rate is None:
            has_undefined = True
            continue
        if single.net is not None:
            parts.append(net_to_gross(single.net, rollup.display_rate))
    if has_undefined:
        return None, True
    if not parts:
        return Decimal("0"), False
    total = parts[0]
    for value in parts[1:]:
        total += value
    return total, False


def _build_bucket_cell(
    axis: CellNet,
    bucket_rollups: Sequence[EstimateRollup],
    row: RowRef | None,
    *,
    vat_mode: str,
    single_rate: Decimal | None,
    area_total_sp: Decimal | None,
) -> BucketCell:
    """Собрать `BucketCell` из готовой нетто-оси и режима показа.

    `deviation_pct` здесь всегда `None`: отклонение — свойство СТРОКИ (нужны
    значения ВСЕХ договоров выборки разом), проставляется один раз позже
    (`_apply_deviation`), а не ячейкой поодиночке.
    """
    net = axis.net
    reasons = set(axis.incomplete_reasons)

    shown: Decimal | None
    if net is None:
        shown = None
    elif vat_mode == VAT_MODE_NET:
        shown = net
    elif vat_mode == VAT_MODE_SINGLE:
        shown = None if single_rate is None else net_to_gross(net, single_rate)
    elif vat_mode == VAT_MODE_OWN:
        shown_value, undefined = _own_mode_shown(bucket_rollups, row)
        if undefined:
            reasons.add(REASON_DISPLAY_RATE_UNDEFINED)
            shown = None
        else:
            shown = shown_value
    else:
        raise ValueError(f"неизвестный режим показа НДС: {vat_mode!r}")

    net_per_sqm: Decimal | None = None
    if net is not None and area_total_sp is not None:
        with localcontext(_DIV_CONTEXT):
            net_per_sqm = net / area_total_sp

    shown_per_sqm: Decimal | None = None
    if shown is not None and area_total_sp is not None:
        with localcontext(_DIV_CONTEXT):
            shown_per_sqm = shown / area_total_sp

    return BucketCell(
        net=net,
        shown=shown,
        net_per_sqm=net_per_sqm,
        shown_per_sqm=shown_per_sqm,
        state=axis.state,
        deviation_pct=None,
        incomplete_reasons=frozenset(reasons),
    )


def _compute_median(cells: dict[int, BucketCell], ordered_ids: Sequence[int]) -> _MedianResult:
    """Медиана строки/корзины по `net_per_sqm` (спека §2.5, правила 1-5).

    Исключены: пустые ячейки (`net_per_sqm is None` — нет ТЭП либо нет числа
    вовсе) и нулевые (`net_per_sqm == 0` — «работ нет либо учтены в другой
    статье», не «дёшево», правило 4). Меньше трёх сопоставимых -> `value is
    None` (правило 5, DoD 11): подсветки в строке нет вовсе, но счётчик и
    список id всё равно возвращаются — экрану нужно число для «сопоставимых
    меньше трёх», а не молчаливо пустая строка.
    """
    comparable = [
        (contract_id, cells[contract_id].net_per_sqm)
        for contract_id in ordered_ids
        if cells[contract_id].net_per_sqm is not None and cells[contract_id].net_per_sqm != 0
    ]
    ids = [contract_id for contract_id, _ in comparable]
    if len(comparable) < 3:
        return _MedianResult(value=None, comparable_count=len(comparable), contract_ids=ids)

    values = sorted(value for _, value in comparable)
    mid = len(values) // 2
    if len(values) % 2 == 1:
        median = values[mid]
    else:
        with localcontext(_DIV_CONTEXT):
            median = (values[mid - 1] + values[mid]) / Decimal(2)
    return _MedianResult(value=median, comparable_count=len(comparable), contract_ids=ids)


def _apply_deviation(
    cells: dict[int, BucketCell], median: _MedianResult
) -> dict[int, BucketCell]:
    """Проставить `deviation_pct` сопоставимым ячейкам от готовой медианы.

    Точная `Decimal`-арифметика, БЕЗ округления (AGENTS.md §3; `money/vat.py`
    — округление живёт на границе показа, не в агрегате). Ячейки вне
    `median.contract_ids` (пустые, нулевые, отсутствующие) отклонения не
    получают: «Нераспределённое» и подобные им сюда просто не передаются
    (вызывающий код решает это заранее, до вызова медианы), а ноль/прочерк в
    ЭТОЙ строке уже отфильтрован `_compute_median`.
    """
    if median.value is None or median.value == 0:
        # Медиана-ноль: отклонение от нуля не определено, и делить на неё нельзя
        # — `_DIV_CONTEXT` трапит `DivisionByZero`, то есть эндпоинт отдал бы 500.
        # Нули из ВХОДА медианы исключены (§2.5 правило 4), но сама она может
        # выйти нулём: при чётном числе сопоставимых с парой средних `-x` и `+x`.
        # Отрицательное нетто представимо — `position_items.total_cost_total`
        # знака не ограничивает, в отличие от `total_amount` допработ. Строка
        # просто остаётся без подсветки; заметку «меньше трёх сопоставимых»
        # экран при этом НЕ покажет — она читает `comparable_count`, а он верен.
        return cells
    result = dict(cells)
    for contract_id in median.contract_ids:
        cell = result[contract_id]
        with localcontext(_DIV_CONTEXT):
            deviation = (cell.net_per_sqm / median.value - Decimal(1)) * 100
        result[contract_id] = replace(cell, deviation_pct=deviation)
    return result


def _median_dict(median: _MedianResult) -> dict:
    return {
        "value": median.value,
        "comparable_count": median.comparable_count,
        "contract_ids": median.contract_ids,
    }


def _bucket_cell_dict(cell: BucketCell) -> dict:
    return {
        "net": cell.net,
        "shown": cell.shown,
        "net_per_sqm": cell.net_per_sqm,
        "shown_per_sqm": cell.shown_per_sqm,
        "state": cell.state,
        "deviation_pct": cell.deviation_pct,
        "incomplete_reasons": sorted(cell.incomplete_reasons),
    }


def _cell_entry(contract_id: int, by_bucket: dict[str, dict[int, BucketCell]]) -> dict:
    """Ячейка договора над строкой/«Итого» — ОДНА форма для `rows[].cells[]`
    и `totals[]` (интерфейс задачи 4: единообразно, `contract_id` на каждой
    ячейке — планировщик экрана не обязан помнить порядок колонок отдельно)."""
    return {
        "contract_id": contract_id,
        BUCKET_BASE: _bucket_cell_dict(by_bucket[BUCKET_BASE][contract_id]),
        BUCKET_AMENDMENTS: _bucket_cell_dict(by_bucket[BUCKET_AMENDMENTS][contract_id]),
        BUCKET_TOTAL: _bucket_cell_dict(by_bucket[BUCKET_TOTAL][contract_id]),
    }


def _row_cells(
    bucket_rollups: dict[int, dict[str, list[EstimateRollup]]],
    ordered_ids: Sequence[int],
    area_by_contract: dict[int, Decimal | None],
    row: RowRef | None,
    *,
    vat_mode: str,
    single_rate: Decimal | None,
    skip_median: bool,
) -> tuple[dict[str, dict[int, BucketCell]], dict[str, _MedianResult]]:
    """Ячейки и медианы ОДНОЙ строки (`row is None` — «Итого по договору») по
    трём корзинам сразу. Общий проход для строк дерева и для грандтотала —
    в обоих случаях один и тот же порядок действий: ось по договору в целом
    (для ABSENT/ZERO, §2.2), ось по каждой корзине с апгрейдом, показ, потом
    медиана НА ВСЮ строку разом (нужны значения всех договоров сразу).
    """
    by_bucket: dict[str, dict[int, BucketCell]] = {
        BUCKET_BASE: {}, BUCKET_AMENDMENTS: {}, BUCKET_TOTAL: {},
    }

    def axis_for(rollups_seq: Sequence[EstimateRollup]) -> CellNet:
        if row is None:
            return _grand_total_cell(rollups_seq)
        return cell_net(rollups_seq, row)

    for contract_id in ordered_ids:
        buckets = bucket_rollups[contract_id]
        contract_axis = axis_for(buckets[BUCKET_TOTAL])
        area = area_by_contract.get(contract_id)
        for bucket_name in (BUCKET_BASE, BUCKET_AMENDMENTS, BUCKET_TOTAL):
            bucket_list = buckets[bucket_name]
            if bucket_name == BUCKET_TOTAL:
                axis = contract_axis
            else:
                axis = _upgrade_absent_to_zero(axis_for(bucket_list), contract_axis)
            by_bucket[bucket_name][contract_id] = _build_bucket_cell(
                axis, bucket_list, row,
                vat_mode=vat_mode, single_rate=single_rate, area_total_sp=area,
            )

    medians: dict[str, _MedianResult] = {}
    for bucket_name in (BUCKET_BASE, BUCKET_AMENDMENTS, BUCKET_TOTAL):
        if skip_median:
            medians[bucket_name] = _MedianResult(value=None, comparable_count=0, contract_ids=[])
            continue
        median = _compute_median(by_bucket[bucket_name], ordered_ids)
        by_bucket[bucket_name] = _apply_deviation(by_bucket[bucket_name], median)
        medians[bucket_name] = median

    return by_bucket, medians


def _format_rate(rate: Decimal) -> str:
    """Ставка НДС для подписи, без хвостовых нулей (`20`, не `20.00`).

    `Decimal.normalize()` на целом значении ("20") даёт "2E+1" — экспоненциальную
    запись, которую `to_integral_value()` не убирает; `str(int(...))` и
    `format(..., "f")` — единственные пути, обходящие это без побочных эффектов.
    """
    normalized = rate.normalize()
    if normalized == normalized.to_integral_value():
        return str(int(normalized))
    return format(normalized, "f")


def _rate_label(rate: Decimal | None) -> str:
    if rate is None:
        return "ставка не определена"
    return f"{_format_rate(rate)} %"


def _mode_caption(vat_mode: str, single_rate: Decimal | None) -> str:
    """Подпись состава денег НА ПОВЕРХНОСТИ (AGENTS.md §10 v6.8, спека §2.3.1).

    Netto объявляет состав явно; «единая» и «своя» ставка ДОПОЛНИТЕЛЬНО
    обязаны сказать, что отклонения посчитаны без НДС (спека §2.5 правило 2)
    — иначе подсветка читалась бы как разница цен там, где на деле разница
    ставок НДС.
    """
    if vat_mode == VAT_MODE_NET:
        return "Суммы показаны без НДС (нетто)."
    if vat_mode == VAT_MODE_SINGLE:
        rate_text = "не выбрана" if single_rate is None else _rate_label(single_rate)
        return (
            f"Суммы пересчитаны в единую ставку НДС ({rate_text}). "
            "Отклонения от медианы посчитаны без НДС."
        )
    if vat_mode == VAT_MODE_OWN:
        return (
            "Каждый договор показан в своей действующей ставке НДС. "
            "Отклонения от медианы посчитаны без НДС."
        )
    raise ValueError(f"неизвестный режим показа НДС: {vat_mode!r}")


def _composition_caption(rollups: Sequence[EstimateRollup]) -> str:
    """Подпись состава колонки в режиме «своя ставка» (спека §2.3.2, DoD 8б/8в).

    Три уровня свёртки, от самого свёрнутого к самому подробному: полное
    совпадение ВСЕХ смет договора -> одна ставка («20 %»); ставки ДС
    совпадают между собой (но не обязательно со ставкой ДГП) -> «ДГП 20 % ·
    ДС 20 %»; ставки ДС расходятся -> перечислить каждую по номеру («ДС №1
    20 %, №2 22 %»). Смета БЕЗ определённой ставки показа обязана быть видна
    словами — правило спеки названо явно, а не выведено по умолчанию.
    """
    if not rollups:
        return "смет нет"

    all_rates = [rollup.display_rate for rollup in rollups]
    if all(rate is not None for rate in all_rates) and len(set(all_rates)) == 1:
        return _rate_label(all_rates[0])

    base = [rollup for rollup in rollups if rollup.amendment_no is None]
    amendments = sorted(
        (rollup for rollup in rollups if rollup.amendment_no is not None),
        key=lambda rollup: rollup.amendment_no,
    )

    parts: list[str] = []
    if base:
        base_rates = {rollup.display_rate for rollup in base}
        if len(base_rates) == 1:
            parts.append(f"ДГП {_rate_label(next(iter(base_rates)))}")
        else:
            parts.append("ДГП " + ", ".join(_rate_label(rollup.display_rate) for rollup in base))
    if amendments:
        amd_rates = {rollup.display_rate for rollup in amendments}
        if len(amd_rates) == 1:
            parts.append(f"ДС {_rate_label(next(iter(amd_rates)))}")
        else:
            enumerated = ", ".join(
                f"№{rollup.amendment_no} {_rate_label(rollup.display_rate)}" for rollup in amendments
            )
            parts.append(f"ДС {enumerated}")
    return " · ".join(parts) if parts else "смет нет"


def _mode_then_larger(rates: Sequence[Decimal]) -> Decimal | None:
    """Самая частая ставка из списка, при равенстве частот — бо́льшая (§2.3.2)."""
    if not rates:
        return None
    counts = Counter(rates)
    top = max(counts.values())
    candidates = [rate for rate, count in counts.items() if count == top]
    return max(candidates)


def _rate_options_from_rollups(
    rollups: dict[int, list[EstimateRollup]]
) -> tuple[list[Decimal], Decimal | None]:
    """Список ставок и предвыбор — чистая функция над роллапами (спека §2.3.2).

    Список — союз определённых ставок показа и известных баз групп (второе
    множество — резерв, гарантирующий непустой список, когда ставка показа не
    определена ни у одной сметы выборки). Частота для предвыбора считается
    ТОЛЬКО по ставкам показа; базы групп в подсчёт не идут и становятся
    основанием предвыбора лишь тогда, когда определённых ставок показа нет
    вовсе (спека §2.3.2, пятый круг ревью — иначе предвыбор был бы не
    определён именно в том углу, ради которого резерв заводился).
    """
    display_rates: list[Decimal] = []
    base_rate_values: list[Decimal] = []
    all_rates: set[Decimal] = set()
    for contract_rollups in rollups.values():
        for rollup in contract_rollups:
            if rollup.display_rate is not None:
                display_rates.append(rollup.display_rate)
                all_rates.add(rollup.display_rate)
            for rate in rollup.base_rates:
                base_rate_values.append(rate)
                all_rates.add(rate)

    options = sorted(all_rates)
    preselected = _mode_then_larger(display_rates) if display_rates else _mode_then_larger(base_rate_values)
    return options, preselected


def rate_options(db: Session, contract_ids: Sequence[int]) -> tuple[list[Decimal], Decimal | None]:
    """Список ставок и предвыбор для режима «единая ставка» (спека §2.3).

    Тонкая обёртка над `load_rollups`: `EstimateRollup` уже несёт всё нужное
    (`display_rate`, `base_rates`), второго обращения к VIEW здесь нет.
    """
    return _rate_options_from_rollups(load_rollups(db, contract_ids))


# ---------------------------------------------------------------------------
#  Разрешение выборки: `ids` либо фильтр (спека §2.6)
# ---------------------------------------------------------------------------

def parse_ids_param(raw: str | None) -> list[int] | None:
    """`"1,2,4"` → `[1, 2, 4]`; `None` → `None` (форма выборки не задана).

    Живёт РЯДОМ с `resolve_selection`, а не в роутере, потому что роутеров два —
    экран сравнения и выгрузка листа — и формат `ids` у них обязан быть один.
    Две копии этого разбора уже расходились: в одной элемент обрезался, в другой
    нет, и сообщения об ошибке были разные, то есть один и тот же адрес получал
    два разных ответа в зависимости от того, куда его послали.

    Нечисловой элемент — `DomainError(400)` с самим значением в тексте, а не
    422-трасса валидатора: строка приходит из адреса, который человек мог набрать
    руками, и ответ обязан говорить, что именно в нём не так.
    """
    if raw is None:
        return None
    try:
        return [int(part) for part in raw.split(",") if part.strip()]
    except ValueError:
        raise DomainError(
            400, f"`ids` должен быть списком чисел через запятую, а не {raw!r}."
        ) from None


def resolve_selection(
    db: Session,
    *,
    ids: Sequence[int] | None = None,
    use_filter: bool = False,
    q: str | None = None,
    object_id: int | None = None,
    contractor_id: int | None = None,
    rate_class_id: int | None = None,
) -> list[int]:
    """Договоры выборки по одной из ДВУХ форм входа (спека §2.6).

    Живёт здесь, а не в роутере, потому что форм входа две, а эндпоинтов —
    тоже два (сравнение и выгрузка листа): вторая копия этого правила означала
    бы, что экран и файл могут сравнивать РАЗНЫЕ множества договоров, а спека
    §2.7 требует ровно обратного — один агрегат на оба представления.

    Фильтры не переписаны, а взяты у списка договоров
    (`crud.contracts.filtered_contract_ids`, тот же `apply_contract_filters`):
    DoD 1 требует, чтобы обе формы давали одинаковый ответ на одинаковом
    множестве, и со второй копией условий это держалось бы на совпадении.
    `page`/`page_size` не переносятся — сравнение берёт всю выборку.

    Отсутствующий id — ОТКАЗ 404 с его номером, а не молчаливое выпадение
    колонки: `Contract.id.in_(...)` сам по себе просто не нашёл бы её, и
    страница сравнила бы меньше договоров, чем просил человек, ничего об этом не
    сказав. Пустой результат ФИЛЬТРА, напротив, — законный ответ: договоров под
    фильтр может не быть, и сказать об этом надо пустой таблицей, а не ошибкой.
    """
    from crud import contracts as crud_contracts

    if use_filter and ids:
        raise DomainError(
            400,
            "Выборка задана дважды: и списком договоров, и фильтром. "
            "Оставьте одну форму — либо `ids`, либо `all=1` с фильтрами.",
        )
    if use_filter:
        return crud_contracts.filtered_contract_ids(
            db, q=q, object_id=object_id, contractor_id=contractor_id,
            rate_class_id=rate_class_id,
        )
    if not ids:
        raise DomainError(
            400,
            "Выборка не задана: передайте `ids` со списком договоров либо "
            "`all=1` для выборки по фильтру.",
        )

    # Порядок здесь не важен (колонки упорядочивает `_load_columns` по
    # signed_date/id), но дубликаты убрать обязательно: повторённый id дал бы
    # вторую колонку того же договора.
    unique = list(dict.fromkeys(ids))
    existing = set(
        db.execute(sa.select(Contract.id).where(Contract.id.in_(unique))).scalars().all()
    )
    missing = [contract_id for contract_id in unique if contract_id not in existing]
    if missing:
        raise DomainError(
            404, "Договоры не найдены: " + ", ".join(str(value) for value in missing) + "."
        )
    return unique


def _load_columns(db: Session, contract_ids: Sequence[int]) -> list[dict]:
    """Шапки колонок выборки, в порядке `signed_date DESC`, затем `id DESC`
    (спека §2.1, DoD 3) — от новых договоров к старым."""
    if not contract_ids:
        return []
    rows = db.execute(
        sa.select(Contract, ObjectModel, Contractor.title, RateClass.title)
        .join(ObjectModel, ObjectModel.id == Contract.object_id)
        .join(Contractor, Contractor.id == Contract.contractor_id)
        .join(RateClass, RateClass.id == Contract.rate_class_id)
        .where(Contract.id.in_(contract_ids))
        .order_by(Contract.signed_date.desc(), Contract.id.desc())
    ).all()
    return [
        {
            "contract_id": contract.id,
            "contract_number": contract.contract_number,
            "object_title": obj.title,
            "contractor_title": contractor_title,
            "rate_class_title": rate_class_title,
            "signed_date": iso(contract.signed_date),
            "area_total_sp": obj.area_total_sp,
            "advance_pct": contract.advance_pct,
            "bank_guarantee_pct": contract.bank_guarantee_pct,
            "retention_pct": contract.retention_pct,
        }
        for contract, obj, contractor_title, rate_class_title in rows
    ]


def build_comparison(
    db: Session,
    contract_ids: Sequence[int],
    *,
    vat_mode: str,
    single_rate: Decimal | None = None,
) -> dict:
    """Полный агрегат сравнения: колонки, строки, ячейки по ТРЁМ корзинам,
    медианы, подписи (спека §2.1-§2.5, план — задача 4).

    Считает ВСЕ ТРИ корзины разом, независимо от `vat_mode` (который влияет
    только на показ, не на то, что вообще посчитано) — экран берёт одно
    представление, Excel (задача 6) получает все три сразу с отдельными
    медианами (спека §2.7 «один агрегат — два представления»).

    **Режим «единая ставка» без явной ставки открывается на ПРЕДВЫБОРЕ.** DoD 8ж
    требует, чтобы этот режим открывался с числами, а ссылка на страницу вправе
    не нести ставку вовсе (§2.3: режим и ставка живут в URL, но URL приходит и
    от человека). Без подстановки весь лист стал бы пустым — причём пустым БЕЗ
    причины, потому что `incomplete_reasons` тут нечего сказать: данные-то в
    порядке. Отдаваемый `single_rate` — ставка, в которой числа ДЕЙСТВИТЕЛЬНО
    показаны, а не та, что пришла в запросе: экран и лист подписывают состав по
    этому полю, и вернуть здесь `None` значило бы подписать неправду.
    """
    contract_ids = list(contract_ids)
    if vat_mode not in (VAT_MODE_OWN, VAT_MODE_SINGLE, VAT_MODE_NET):
        # Проверка на входе, а не внутри сборки ячейки: неизвестный режим —
        # это ошибка запроса (400), и узнать о ней надо до того, как агрегат
        # проделает всю работу и упадёт `ValueError`-ом на подписи, то есть 500.
        raise DomainError(400, f"Неизвестный режим показа НДС: {vat_mode!r}.")

    rollups = load_rollups(db, contract_ids)
    columns_meta = _load_columns(db, contract_ids)
    ordered_ids = [column["contract_id"] for column in columns_meta]

    rate_opts, rate_preselected = _rate_options_from_rollups(rollups)
    effective_single_rate = single_rate
    if vat_mode == VAT_MODE_SINGLE and effective_single_rate is None:
        effective_single_rate = rate_preselected

    bucket_rollups = {
        contract_id: _split_buckets(rollups.get(contract_id, [])) for contract_id in ordered_ids
    }
    area_by_contract = {
        column["contract_id"]: column["area_total_sp"] for column in columns_meta
    }

    rows_ref = build_rows(rollups)
    rows_out = []
    for row in rows_ref:
        by_bucket, medians = _row_cells(
            bucket_rollups, ordered_ids, area_by_contract, row,
            vat_mode=vat_mode, single_rate=effective_single_rate,
            skip_median=row.kind == "unallocated",
        )
        rows_out.append({
            "kind": row.kind,
            "category_id": row.category_id,
            "code": row.code,
            "title": row.title,
            "level": row.level,
            "parent_code": row.parent_code,
            "cells": [_cell_entry(contract_id, by_bucket) for contract_id in ordered_ids],
            "medians": {bucket: _median_dict(medians[bucket]) for bucket in medians},
        })

    totals_by_bucket, totals_medians = _row_cells(
        bucket_rollups, ordered_ids, area_by_contract, None,
        vat_mode=vat_mode, single_rate=effective_single_rate, skip_median=False,
    )

    columns = [
        {
            **column,
            "composition_caption": _composition_caption(rollups.get(column["contract_id"], [])),
        }
        for column in columns_meta
    ]

    return {
        "vat_mode": vat_mode,
        "single_rate": effective_single_rate,
        "rate_options": rate_opts,
        "rate_preselected": rate_preselected,
        "caption": _mode_caption(vat_mode, effective_single_rate),
        "columns": columns,
        "rows": rows_out,
        "totals": [_cell_entry(contract_id, totals_by_bucket) for contract_id in ordered_ids],
        "totals_medians": {
            bucket: _median_dict(totals_medians[bucket]) for bucket in totals_medians
        },
    }
