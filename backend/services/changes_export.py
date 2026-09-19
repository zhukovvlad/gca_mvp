"""Сборка листа «Изменения КП» — чистый расчёт (спека
2026-09-16-tender-changes-export-design.md §2.2-§2.9).

Модуль чистый, тем же приёмом, что и сосед `services/stage_summary.py`: ни
`Session`, ни ORM, ни импортов из `crud` — на входе датаклассы, на выходе
датаклассы; чтение БД и построчные счётчики (в том числе «у строки есть все
три конечные составляющие») — дело `crud/changes_export.py`. Здесь считаются
свёртка по ключу строки, выбор налоговой оси, Δ и тождество состава.

**Три вещи, из-за которых этот модуль вообще потребовался отдельным слоем.**

1. **Округление денег — только `finance.money_round` (`ROUND_HALF_UP`).**
   Умолчание `Decimal.quantize` — `ROUND_HALF_EVEN`, и на `0,005` оно даёт
   другой ответ. Своего написания правила округления модуль не заводит.
2. **Тождество состава `Δ работы + Δ материалы + Δ косвенные = Δ суммы`
   проверяется на ПЕЧАТАЕМЫХ величинах** (каждая округлена до копейки), а не
   на свёртке до округления: `0,004 × 3` сходится с `0,012` до округления и
   расходится с напечатанными `0,00 × 3` против `0,01` — ровно там, где
   тождество читают (спека §2.3). Допуска у проверки нет.
3. **Недоступность по ОСИ (неизвестная база НДС этапа либо `TAX_NONE`) и
   недоступность по СТРОКЕ («конечной суммы нет» у одной группы) — разные
   вещи.** Первая гасит этап целиком: ячейки, подытоги, общий итог. Вторая
   вносит в подытог ноль и поднимает `incomplete`, но подытог не гасит — тот
   же приём, которым `row_amount`/`row_amount_incomplete` (`AGENTS.md` §6)
   разводит частичный вес и полный. Смешать их нельзя: VIEW, с которым лист
   обязан сходиться (спека DoD 4), тоже считает такую строку нулевым вкладом,
   а не недоступностью.

**Ось конвертируется этим модулем, а не подаётся готовой.** `crud.changes_export`
отдаёт ВАЛОВЫЕ суммы — тем же соглашением, что и `v_category_totals`
(«остаётся ВАЛОВЫМ всегда», `AGENTS.md` §4). Выбор оси (`pick_tax_basis`) и
перевод в неё (`to_shown`) — работа этого слоя, той же парой функций, которой
уже пользуется `services/stage_summary.py`. Прямой вызов `money.vat.gross_to_net`
здесь не заводится: `to_shown` уже вызывает его внутри и уже решает три
пограничных случая (неизвестна ставка этапа, ось `TAX_NONE`, ось `TAX_GROSS` —
тождество без арифметики). Второе написание того же выбора разъехалось бы с
первым — тем же доводом, каким сама спека (§2.4) запрещает второе написание
предиката цены.

Объём (`quantity`) и вес цены (`price_den`) деньгами не являются и оси не
знают: они складываются как есть, независимо от НДС.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Context, Decimal, DivisionByZero, InvalidOperation, Overflow, localcontext

from finance import money_round
from parser.summary_block import ARITHMETIC_PRECISION
from services.stage_summary import TAX_NONE, TaxBasis, pick_tax_basis, to_shown

#: Ветви строки листа (спека §2.2). `POSITION`/`TO_REVIEW` — работа; три вида
#: каталожной записи, работой не являющиеся, и строка без каталожной записи —
#: диагностические группы.
KIND_WORK = "work"
KIND_ADDITIONAL = "additional"
KIND_NONWORK = "nonwork"
KIND_UNMATCHED = "unmatched"

#: Ветви, у которых печатается ТОЛЬКО сумма (спека §2.2, таблица): единицы,
#: объёма, цены и состава у допработы и диагностических групп нет по контракту
#: — не потому, что данных не хватило, а потому, что группа их не несёт.
MONEY_ONLY_KINDS: frozenset[str] = frozenset({KIND_ADDITIONAL, KIND_NONWORK, KIND_UNMATCHED})

#: Три вида каталожной записи, не являющиеся работой, → своя названная группа
#: НА КАЖДУЮ СТАТЬЮ (спека §2.2). Ни одна строка не выбрасывается: `v_category_totals`
#: фильтра по виду каталожной записи не имеет (миграция 0012), и книга обязана
#: сохранить каждый её рубль.
NON_WORK_TITLES: dict[str, str] = {
    "HEADER": "Строки, размеченные как заголовок раздела",
    "LOT_HEADER": "Строки, размеченные как заголовок лота",
    "TRASH": "Строки, размеченные как мусор",
}

UNMATCHED_TITLE = "Непривязанные строки сметы"

#: Перечень статей в подписи маршрута режется на этом числе кодов, дальше —
#: «и ещё N» (спека §2.7): худший случай печатал одиннадцать кодов подряд.
BRIEF_LIMIT = 2

#: «Суммы нет» / Δ недоступна — строка присутствует, конечной суммы нет
#: (`rows_with_amount = 0`), это НЕ ноль (спека §2.5).
REASON_NO_AMOUNT = "no_amount"
#: «Цены нет» — пригодных строк для матрицы `Σ(цена×вес)/Σвес` нет (спека §2.4,
#: §2.5); та же причина используется у ветвей `MONEY_ONLY_KINDS`, где цены нет
#: по контракту, а не по нехватке данных — различитель для показа лежит в
#: `Cell.kind`, а не в этой причине (см. докстроку `_cell_for`).
REASON_NO_PRICE = "no_price"
#: «Состав неполон» — не выполнено хотя бы одно из двух условий тождества
#: (спека §2.3): построчная полнота ЛИБО сходимость на печатаемых Δ.
REASON_MIX_INCOMPLETE = "mix_incomplete"
#: Неизвестна база НДС этапа либо ось листа `TAX_NONE` (спека §2.9) — гасит
#: цену, сумму и компоненты НА ЭТОМ ЭТАПЕ целиком, а не только у одной ветви.
REASON_UNKNOWN_VAT_BASE = "unknown_vat_base"

#: Внутренний ключ ветви «непривязанная строка» — не публикуется, участвует
#: только в идентичности `SheetRow.key` наравне с `catalog_position_id`
#: (работа) и `catalog_kind` (диагностика по виду).
_UNMATCHED_SUBKEY = "unmatched"

#: Контекст деления — та же форма и по той же причине, что `stage_summary._DIV_CONTEXT`
#: и `money.vat._VAT_CONTEXT`: явные трапы БЕЗ `Inexact` (частное почти никогда
#: не представимо конечной десятичной дробью). Приватный контекст соседа не
#: импортируется — у каждого модуля свой экземпляр той же формы.
_DIV_CONTEXT = Context(prec=ARITHMETIC_PRECISION, traps=[Overflow, DivisionByZero, InvalidOperation])

_ZERO = Decimal(0)
_HUNDRED = Decimal(100)


@dataclass(frozen=True)
class ArticleRef:
    """Статья классификатора или сентинел «Нераспределённое» (`UNALLOCATED_ARTICLE`).

    `sort_order is None` — признак ИМЕННО «Нераспределённого»: у настоящей
    статьи `WorkCategory.sort_order` `UNIQUE` и всегда есть (`crud.changes_export`).
    """

    code: str | None
    title: str | None
    sort_order: int | None


#: Сентинел статьи для строк без привязки к классификатору (позиции, допработы,
#: непривязанные строки — спека §2.2). `crud.changes_export` строит СВОЙ
#: экземпляр с теми же полями; сравнение по значению (`==` датакласса) работает
#: независимо от того, какой модуль его создал.
UNALLOCATED_ARTICLE = ArticleRef(code=None, title="Нераспределённое", sort_order=None)


@dataclass(frozen=True)
class Money:
    """Денежное состояние: величина, причина её отсутствия и признак неполноты.

    Инвариант: `reason is None` ⟺ `value is not None`.
    Оси независимы: `incomplete` говорит «величина есть, но посчитана не по всем
    строкам», а не «величины нет». Один тип несут ячейка, подытог статьи и
    общий итог (спека §2.3, §2.9) — иначе неизвестная база НДС могла бы попасть
    в подытог ложным нулём, тем же дефектом «одно значение — два состояния»,
    что уже решён у `stage_summary.TotalCell`, только этажом выше ячейки.
    """

    value: Decimal | None
    reason: str | None
    incomplete: bool


@dataclass(frozen=True)
class Components:
    """Три составляющие суммы. Поля нарочно нулевые по отдельности: на входе
    (`GroupRow.components`) отсутствие одной из трёх — самостоятельный факт
    (спека §2.3: «составляющие объявлены nullable»), а не то же самое, что ноль."""

    works: Decimal | None
    materials: Decimal | None
    indirect: Decimal | None


@dataclass(frozen=True)
class GroupRow:
    """Одна строка агрегата позиций: (этап, статья, каталожная позиция).

    Форма — прямой перенос полей запроса гейта 1 (`GROUPS_SQL` в
    `docs/superpowers/specs/2026-09-16-tender-changes-export/gen_mockup.py`) в
    датакласс: три множества строк разведены уже здесь (`rows_all` — присутствие,
    `amount`/`rows_with_amount` — сумма, `price_num`/`price_den`/`rows_priced` —
    матрица, спека §2.4). Суммы — ВАЛОВЫЕ (соглашение `v_category_totals`);
    ось переводит `build_sheet`.
    """

    stage_no: int
    article: ArticleRef
    catalog_position_id: int | None
    catalog_kind: str | None
    work_title: str | None
    unit: str | None
    rows_all: int
    rows_with_amount: int
    rows_with_mix: int
    rows_priced: int
    amount: Decimal | None
    components: Components
    quantity: Decimal | None
    price_num: Decimal | None
    price_den: Decimal | None
    unit_components_num: Components
    numbers: tuple[str, ...]


@dataclass(frozen=True)
class AdditionalRow:
    """Вторая ветвь свода — допработы. Ключ — `(lot_key, chapter_ref_raw)`, а не
    `lots.id`: идентификатор лота свой у каждой сметы раунда (спека §2.2)."""

    stage_no: int
    article: ArticleRef
    lot_key: str
    chapter_ref_raw: str | None
    work_title: str | None
    amount: Decimal | None
    rows_all: int
    rows_with_amount: int


@dataclass(frozen=True)
class StageInput:
    stage_no: int
    label: str | None
    held_on: dt.date | None
    vat_rate_base: Decimal | None


@dataclass(frozen=True)
class SheetInput:
    participant_title: str
    stages: list[StageInput]
    groups: list[GroupRow]
    additional: list[AdditionalRow]


@dataclass(frozen=True)
class Cell:
    """Ячейка одного этапа. У ветвей `MONEY_ONLY_KINDS` `quantity` и `components`
    — `None`, а `unit_price` несёт `Money(None, REASON_NO_PRICE, False)`: цены у
    них нет по контракту (спека §2.2), а не потому, что не нашлось пригодных
    строк. Различитель для показа — `kind`, а не сама причина: излучатель листа
    (задача 4) обязан печатать прочерк по ветви, а не текст «цены нет», иначе
    подпись обещала бы величину, которой в принципе не бывает (спека §2.5)."""

    kind: str
    amount: Money
    quantity: Decimal | None
    unit_price: Money
    components: Components | None
    numbers: tuple[str, ...]
    rows_all: int
    rows_with_amount: int
    rows_priced: int


@dataclass(frozen=True)
class SheetRow:
    key: tuple
    kind: str
    article: ArticleRef
    work_title: str
    unit: str | None
    cells: list[Cell | None]
    delta_amount: Money
    delta_pct: Decimal | None
    delta_components: Components | None
    components_reason: str | None
    completeness: str
    route: list[str]


@dataclass(frozen=True)
class Subtotal:
    article: ArticleRef
    rows: int
    by_stage: list[Money]
    delta: Money


@dataclass(frozen=True)
class Sheet:
    title: str
    stages: list[StageInput]
    rows: list[SheetRow]
    subtotals: list[Subtotal]
    grand_by_stage: list[Money]
    grand_delta: Money
    tax_basis: TaxBasis
    moves_note: str


def _dec(value: Decimal | None) -> Decimal:
    """Свёрнутая сумма трактует отсутствующую составляющую как нулевой вклад —
    тот же приём, каким `SUM` в SQL игнорирует `NULL` (спека §2.4)."""
    return value if value is not None else _ZERO


def _article_label(article: ArticleRef) -> str:
    """Строковый identifier статьи для множеств маршрута и подписи `brief()`.

    `article.code` у «Нераспределённого» — `None`; подмена на заголовок
    сентинела здесь, а не в самих множествах, единственно ради того, чтобы
    `sorted()` внутри `_brief` никогда не сравнивал `None` со строкой — тот же
    довод, каким `article_sort_key` ниже обходит `None` в ключе сортировки
    строк."""
    return article.code if article.code is not None else UNALLOCATED_ARTICLE.title  # type: ignore[return-value]


def _brief(codes: set[str]) -> str:
    """Перечень кодов статей, урезанный до `BRIEF_LIMIT` (спека §2.7)."""
    ordered = sorted(codes)
    if len(ordered) <= BRIEF_LIMIT:
        return ", ".join(ordered)
    return f"{', '.join(ordered[:BRIEF_LIMIT])} и ещё {len(ordered) - BRIEF_LIMIT}"


def _blank_slot() -> dict:
    """Свёрточный аккумулятор одной (ключ, этап)-ячейки ДО перевода в `Cell`.

    Технические поля есть у всех ветвей безусловно — прежняя редакция макета
    гейта 1 не заводила их денежным ветвям и падала `KeyError: 'qty'` на
    `HEADER` (`check_diagnostic_branches.py`); гасить величину нужно там, где её
    показывают (в `_cell_for`), а не там, где её складывают."""
    return {
        "rows_all": 0,
        "rows_with_amount": 0,
        "rows_with_mix": 0,
        "rows_priced": 0,
        "amount": _ZERO,
        "works": _ZERO,
        "materials": _ZERO,
        "indirect": _ZERO,
        "quantity": _ZERO,
        "price_num": _ZERO,
        "price_den": _ZERO,
        "unit_works_num": _ZERO,
        "unit_materials_num": _ZERO,
        "unit_indirect_num": _ZERO,
        "numbers": [],
    }


def _classify_group(row: GroupRow) -> tuple[str, object, str]:
    """Ветвь, подключ и печатное имя одной строки `GROUPS_SQL` (спека §2.2).

    Порядок проверки фиксирован: непривязанность (`catalog_position_id is None`)
    проверяется РАНЬШЕ вида каталожной записи, потому что `catalog_kind` для
    непривязанной строки тоже пуст — иначе она молча ушла бы в ветвь `work`.
    """
    if row.catalog_position_id is None:
        return KIND_UNMATCHED, _UNMATCHED_SUBKEY, UNMATCHED_TITLE
    if row.catalog_kind in NON_WORK_TITLES:
        return KIND_NONWORK, row.catalog_kind, NON_WORK_TITLES[row.catalog_kind]
    return KIND_WORK, row.catalog_position_id, row.work_title


def _accumulate_group(slot: dict, row: GroupRow) -> None:
    slot["rows_all"] += row.rows_all
    slot["rows_with_amount"] += row.rows_with_amount
    slot["rows_with_mix"] += row.rows_with_mix
    slot["rows_priced"] += row.rows_priced
    slot["amount"] += _dec(row.amount)
    slot["works"] += _dec(row.components.works)
    slot["materials"] += _dec(row.components.materials)
    slot["indirect"] += _dec(row.components.indirect)
    slot["quantity"] += _dec(row.quantity)
    slot["price_num"] += _dec(row.price_num)
    slot["price_den"] += _dec(row.price_den)
    slot["unit_works_num"] += _dec(row.unit_components_num.works)
    slot["unit_materials_num"] += _dec(row.unit_components_num.materials)
    slot["unit_indirect_num"] += _dec(row.unit_components_num.indirect)
    if row.numbers:
        slot["numbers"].extend(row.numbers)


def _accumulate_additional(slot: dict, row: AdditionalRow) -> None:
    slot["rows_all"] += row.rows_all
    slot["rows_with_amount"] += row.rows_with_amount
    slot["amount"] += _dec(row.amount)


def _shown(value: Decimal, rate: Decimal | None, basis: TaxBasis) -> Decimal:
    """`to_shown`, но для контекста, где вызывающий уже знает, что ось известна
    (`rate is not None` и `basis.basis != TAX_NONE`) — оболочка, только чтобы не
    писать `assert` на месте вызова четыре раза подряд."""
    shown = to_shown(value, rate, basis)
    assert shown is not None, "вызывающий обязан проверить ось до вызова _shown"
    return shown


def _cell_for(kind: str, slot: dict, stage: StageInput, tax_basis: TaxBasis, axis_reason: str | None) -> Cell:
    """Один `Cell` из свёрнутого аккумулятора. Порядок проверок — ось раньше
    состояния строки: неизвестная база НДС гасит сумму/цену/компоненты ЦЕЛИКОМ,
    даже если сами строки присутствуют и посчитаны (спека §2.9); только когда
    ось известна, вступает трёхсостоянийное правило суммы (спека §2.5)."""
    if axis_reason is not None:
        amount = Money(None, axis_reason, False)
    elif slot["rows_with_amount"] == 0:
        amount = Money(None, REASON_NO_AMOUNT, False)
    else:
        amount = Money(_shown(slot["amount"], stage.vat_rate_base, tax_basis), None, False)

    if kind in MONEY_ONLY_KINDS:
        return Cell(
            kind=kind,
            amount=amount,
            quantity=None,
            unit_price=Money(None, REASON_NO_PRICE, False),
            components=None,
            numbers=(),
            rows_all=slot["rows_all"],
            rows_with_amount=slot["rows_with_amount"],
            rows_priced=0,
        )

    if axis_reason is not None:
        unit_price = Money(None, axis_reason, False)
    elif slot["price_den"] == 0:
        unit_price = Money(None, REASON_NO_PRICE, False)
    else:
        with localcontext(_DIV_CONTEXT):
            raw_price = slot["price_num"] / slot["price_den"]
        unit_price = Money(_shown(raw_price, stage.vat_rate_base, tax_basis), None, False)

    components = None
    if amount.value is not None:
        components = Components(
            _shown(slot["works"], stage.vat_rate_base, tax_basis),
            _shown(slot["materials"], stage.vat_rate_base, tax_basis),
            _shown(slot["indirect"], stage.vat_rate_base, tax_basis),
        )

    return Cell(
        kind=kind,
        amount=amount,
        quantity=slot["quantity"],
        unit_price=unit_price,
        components=components,
        numbers=tuple(sorted(slot["numbers"])),
        rows_all=slot["rows_all"],
        rows_with_amount=slot["rows_with_amount"],
        rows_priced=slot["rows_priced"],
    )


def _per_unit_mix(slot: dict, stage: StageInput, tax_basis: TaxBasis, axis_reason: str | None) -> tuple | None:
    """Состав НА ЕДИНИЦУ, тем же множеством строк, что и цена (спека §2.4).
    Используется только для маршрута «Что двигалось» — по абсолютам один рост
    объёма выглядел бы сменой состава (спека §2.7)."""
    if axis_reason is not None or slot["price_den"] == 0:
        return None
    with localcontext(_DIV_CONTEXT):
        raw = (
            slot["unit_works_num"] / slot["price_den"],
            slot["unit_materials_num"] / slot["price_den"],
            slot["unit_indirect_num"] / slot["price_den"],
        )
    return tuple(_shown(v, stage.vat_rate_base, tax_basis) for v in raw)


def _mix_complete_at(slot: dict | None) -> bool:
    """Построчная полнота — первое из двух условий тождества (спека §2.3).
    Отсутствующий конец полон по построению: его вклад нулевой по всем трём."""
    return slot is None or slot["rows_with_mix"] == slot["rows_with_amount"]


def _row_delta_amount(first: Cell | None, last: Cell | None) -> Money:
    """Δ суммы строки — концы КРАЙНИЕ (спека §2.6). Отсутствие строки на конце
    значит нулевой вклад в статью; присутствие без конечной суммы — НЕ ноль, и
    гасит Δ целиком с причиной этого конца (первый конец приоритетнее — тот же
    порядок, что у `stage_summary._endpoints`)."""
    first_value = _ZERO if first is None else first.amount.value
    last_value = _ZERO if last is None else last.amount.value
    if first_value is None or last_value is None:
        reason = None
        if first is not None and first.amount.value is None:
            reason = first.amount.reason
        if reason is None and last is not None and last.amount.value is None:
            reason = last.amount.reason
        return Money(None, reason, False)
    incomplete = (first is not None and first.rows_with_amount < first.rows_all) or (
        last is not None and last.rows_with_amount < last.rows_all
    )
    return Money(last_value - first_value, None, incomplete)


def _row_delta_pct(first: Cell | None, delta_amount: Money) -> Decimal | None:
    """Процент недоступен, если строки на первом этапе нет либо её сумма на
    первом этапе неположительна — отрицательный и нулевой знаменатель дают тот
    же исход, что отсутствие (спека §2.6)."""
    if delta_amount.value is None or first is None or first.amount.value is None or first.amount.value <= 0:
        return None
    start = first.amount.value
    end = start + delta_amount.value
    with localcontext(_DIV_CONTEXT):
        return (end / start - 1) * _HUNDRED


def _row_delta_components(
    first: Cell | None,
    last: Cell | None,
    first_mix_complete: bool,
    last_mix_complete: bool,
    delta_amount: Money,
) -> tuple[Components | None, str | None]:
    """Три Δ состава печатаются ТОЛЬКО когда держится тождество на ПЕЧАТАЕМЫХ
    величинах (спека §2.3). Причина недоступности Δ суммы (ось либо «суммы
    нет») приоритетнее `REASON_MIX_INCOMPLETE`: если концов сравнивать не с чем,
    несходимость состава — не тот факт, который стоит утверждать."""
    if delta_amount.value is None:
        return None, delta_amount.reason
    if not (first_mix_complete and last_mix_complete):
        return None, REASON_MIX_INCOMPLETE

    zero_components = Components(_ZERO, _ZERO, _ZERO)
    first_components = first.components if first is not None else zero_components
    last_components = last.components if last is not None else zero_components
    assert first_components is not None and last_components is not None

    rounded = {
        name: money_round(getattr(last_components, name) - getattr(first_components, name))
        for name in ("works", "materials", "indirect")
    }
    if sum(rounded.values(), _ZERO) != money_round(delta_amount.value):
        return None, REASON_MIX_INCOMPLETE
    return Components(**rounded), None


def _completeness_text(cells: list[Cell | None], stages: Sequence[StageInput], kind: str) -> str:
    """«Полнота» различает ДВА множества строк и называет их раздельно (спека
    §2.5): у `MONEY_ONLY_KINDS` цены не бывает по контракту, поэтому для них
    печатается только дробь суммы."""
    notes = []
    for idx, cell in enumerate(cells):
        if cell is None:
            continue
        parts = []
        if cell.rows_with_amount < cell.rows_all:
            parts.append(f"сумма {cell.rows_with_amount}/{cell.rows_all}")
        if kind not in MONEY_ONLY_KINDS and cell.rows_priced < cell.rows_all:
            parts.append(f"цена {cell.rows_priced}/{cell.rows_all}")
        if parts:
            notes.append(f"Э{stages[idx].stage_no}: " + ", ".join(parts))
    return "; ".join(notes)


def _route_for(
    key: tuple,
    kind: str,
    cells: list[Cell | None],
    slots: list[dict | None],
    article: ArticleRef,
    work_membership: dict[int, list[set[str]]],
    stages: Sequence[StageInput],
    tax_basis: TaxBasis,
    axis_reason: list[str | None],
) -> list[str]:
    """Маршрут «Что двигалось» — по КАЖДОЙ соседней ПО ШКАЛЕ паре этапов (спека
    §2.7). Структурные основания («появилась», «исчезла», «переезд») считаются
    ПО РАБОТЕ ЦЕЛИКОМ через `work_membership` — множество статей, которым
    принадлежит `catalog_position_id` на каждом этапе; числовые основания
    (сумма, объём, цена, состав) требуют присутствия ОБЕИХ ячеек."""
    steps: list[str] = []
    own_label = _article_label(article)
    work_id = key[2] if kind == KIND_WORK else None
    n = len(stages)
    for i in range(1, n):
        prev_cell, cur_cell = cells[i - 1], cells[i]
        words: list[str] = []

        if kind == KIND_WORK:
            prev_set = work_membership[work_id][i - 1]
            cur_set = work_membership[work_id][i]
            here_prev, here_cur = own_label in prev_set, own_label in cur_set
            if here_prev and not here_cur:
                words.append("исчезла из КП" if not cur_set else "ушла в " + _brief(cur_set - prev_set or cur_set))
            elif here_cur and not here_prev:
                words.append("появилась в КП" if not prev_set else "пришла из " + _brief(prev_set - cur_set or prev_set))
            elif here_prev and here_cur:
                added, removed = cur_set - prev_set, prev_set - cur_set
                if added:
                    words.append("часть ушла в " + _brief(added))
                if removed:
                    words.append("часть пришла из " + _brief(removed))
        elif prev_cell is not None and cur_cell is None:
            words.append("исчезла из КП")
        elif prev_cell is None and cur_cell is not None:
            words.append("появилась в КП")

        if prev_cell is not None and cur_cell is not None:
            prev_amount, cur_amount = prev_cell.amount.value, cur_cell.amount.value
            if prev_amount is None or cur_amount is None:
                if prev_amount is not cur_amount:
                    words.append("сумма недоступна")
            elif prev_amount != cur_amount:
                words.append("сумма")
            if kind not in MONEY_ONLY_KINDS:
                if prev_cell.quantity != cur_cell.quantity:
                    words.append("объём")
                prev_price, cur_price = prev_cell.unit_price.value, cur_cell.unit_price.value
                if prev_price is not None and cur_price is not None and prev_price != cur_price:
                    words.append("цена")
                prev_mix = _per_unit_mix(slots[i - 1], stages[i - 1], tax_basis, axis_reason[i - 1]) if slots[i - 1] else None
                cur_mix = _per_unit_mix(slots[i], stages[i], tax_basis, axis_reason[i]) if slots[i] else None
                if prev_mix is not None and cur_mix is not None and prev_mix != cur_mix:
                    words.append("состав")

        if words:
            steps.append(f"Э{stages[i - 1].stage_no}→Э{stages[i].stage_no}: " + ", ".join(words))
    return steps


def _money_delta(first: Money, last: Money) -> Money:
    """Δ между двумя УЖЕ ГОТОВЫМИ `Money` одного уровня (подытог, итог) — в
    отличие от `_row_delta_amount`, здесь нет понятия «отсутствующего конца»:
    оба всегда либо число (в т.ч. ноль), либо погашены причиной. Первая причина
    приоритетнее второй — тот же порядок, что у `_row_delta_amount`."""
    reason = first.reason or last.reason
    if reason is not None:
        return Money(None, reason, False)
    assert first.value is not None and last.value is not None
    return Money(last.value - first.value, None, first.incomplete or last.incomplete)


def _row_sort_key(row: SheetRow) -> tuple:
    """Порядок строк листа (решение плана — перенос §2.13 `stage_summary.sort_key`
    на новый ключ строки): первичный — `sort_order` классификатора, вторичный —
    наименование работы, третичный — ключ ветви. `article_sort_key` уже
    отправляет «Нераспределённое» в конец, не сравнивая `None` со строкой;
    третий компонент — строковое представление `key`, а не сам `key`: элементы
    ключа разных ветвей (int, tuple, str) друг с другом не сравнимы напрямую."""
    return (*article_sort_key(row.article), row.work_title or "", repr(row.key))


def article_sort_key(article: ArticleRef) -> tuple[int, int]:
    """Первые два поля ключа сортировки строк листа: `(0, sort_order)` для
    настоящей статьи, `(1, 0)` для «Нераспределённого». Первое поле решает
    всё — второе поле у «Нераспределённого» никогда не сравнивается со `sort_order`
    настоящей статьи, поэтому `None` в сравнение не попадает НИКОГДА (решение
    плана; спека сортировки статей не называет)."""
    if article.sort_order is None:
        return (1, 0)
    return (0, article.sort_order)


_FORBIDDEN_SHEET_CHARS = frozenset("[]:*?/\\")
_SHEET_NAME_LIMIT = 31


def _clean_sheet_title(title: str) -> str:
    return "".join("-" if ch in _FORBIDDEN_SHEET_CHARS else ch for ch in title).strip()


def sheet_names(titles: Sequence[str]) -> list[str]:
    """Имена листов книги: очистка `[]:*?/\\`, обрезка до 31 знака, разведение
    совпадений суффиксами ` (2)`, ` (3)` — обрезка ПОВТОРЯЕТСЯ ПОСЛЕ добавления
    суффикса, иначе разведённое имя вышло бы за формат (решение плана). Имя,
    ставшее пустым после чистки, заменяется на `Участник N` (N — порядковый
    номер участника, 1-based).

    **Разведение — по `casefold()`, а не по буквальной строке** (внешнее
    ревью H2): Excel и `openpyxl` сравнивают имена листов регистронезависимо,
    поэтому «AAAA» и «aaaa» — одно и то же имя книги, даже когда хелпер увидел
    бы в них две разные строки. Не разведя их сам, хелпер отдал бы `openpyxl`
    два «разных» имени по 31 знаку, а тот доразвёл бы их СВОИМ суффиксом без
    повторной обрезки — и книга вышла бы за формат с `UserWarning`. Отображаемый
    регистр при этом сохраняется: `casefold()` участвует только в сравнении."""
    used: set[str] = set()
    result: list[str] = []
    for index, title in enumerate(titles, start=1):
        base = _clean_sheet_title(title)[:_SHEET_NAME_LIMIT]
        if not base:
            base = f"Участник {index}"[:_SHEET_NAME_LIMIT]
        name = base
        suffix_no = 2
        while name.casefold() in used:
            suffix = f" ({suffix_no})"
            name = base[: _SHEET_NAME_LIMIT - len(suffix)] + suffix
            suffix_no += 1
        used.add(name.casefold())
        result.append(name)
    return result


def build_sheet(data: SheetInput) -> Sheet:
    """Собрать лист одного участника (спека §2.2-§2.9)."""
    stages = data.stages
    n = len(stages)
    stage_index = {stage.stage_no: idx for idx, stage in enumerate(stages)}
    tax_basis = pick_tax_basis([stage.vat_rate_base for stage in stages])
    axis_reason: list[str | None] = [
        REASON_UNKNOWN_VAT_BASE if (tax_basis.basis == TAX_NONE or stage.vat_rate_base is None) else None
        for stage in stages
    ]

    slots_by_key: dict[tuple, list[dict | None]] = {}
    meta: dict[tuple, dict] = {}
    # Множество статей, которым принадлежит работа (catalog_position_id) на
    # каждом этапе — контрпара «переезда» и «появилась/исчезла» (спека §1.5,
    # §2.7): та же работа законно стоит в нескольких статьях одновременно.
    work_membership: dict[int, list[set[str]]] = defaultdict(lambda: [set() for _ in range(n)])

    for row in data.groups:
        idx = stage_index[row.stage_no]
        kind, subkey, title = _classify_group(row)
        key = (kind, row.article, subkey)
        slots = slots_by_key.setdefault(key, [None] * n)
        slot = slots[idx] or _blank_slot()
        _accumulate_group(slot, row)
        slots[idx] = slot
        meta.setdefault(key, {"kind": kind, "article": row.article, "work_title": title, "unit": row.unit})
        if kind == KIND_WORK:
            work_membership[subkey][idx].add(_article_label(row.article))

    for row in data.additional:
        idx = stage_index[row.stage_no]
        key = (KIND_ADDITIONAL, row.article, (row.lot_key, row.chapter_ref_raw))
        slots = slots_by_key.setdefault(key, [None] * n)
        slot = slots[idx] or _blank_slot()
        _accumulate_additional(slot, row)
        slots[idx] = slot
        meta.setdefault(key, {"kind": KIND_ADDITIONAL, "article": row.article, "work_title": row.work_title, "unit": None})

    rows: list[SheetRow] = []
    for key, slots in slots_by_key.items():
        info = meta[key]
        kind = info["kind"]
        cells: list[Cell | None] = [
            None if slot is None else _cell_for(kind, slot, stages[idx], tax_basis, axis_reason[idx])
            for idx, slot in enumerate(slots)
        ]
        first_cell, last_cell = cells[0], cells[-1]
        delta_amount = _row_delta_amount(first_cell, last_cell)
        delta_pct = _row_delta_pct(first_cell, delta_amount)

        if kind in MONEY_ONLY_KINDS:
            delta_components: Components | None = None
            components_reason: str | None = None
        else:
            delta_components, components_reason = _row_delta_components(
                first_cell,
                last_cell,
                _mix_complete_at(slots[0]),
                _mix_complete_at(slots[-1]),
                delta_amount,
            )

        route = _route_for(key, kind, cells, slots, info["article"], work_membership, stages, tax_basis, axis_reason)
        completeness = _completeness_text(cells, stages, kind)

        rows.append(
            SheetRow(
                key=key,
                kind=kind,
                article=info["article"],
                work_title=info["work_title"],
                unit=info["unit"],
                cells=cells,
                delta_amount=delta_amount,
                delta_pct=delta_pct,
                delta_components=delta_components,
                components_reason=components_reason,
                completeness=completeness,
                route=route,
            )
        )
    rows.sort(key=_row_sort_key)

    by_article: dict[ArticleRef, list[SheetRow]] = defaultdict(list)
    for row in rows:
        by_article[row.article].append(row)

    subtotals: list[Subtotal] = []
    for article, article_rows in by_article.items():
        by_stage: list[Money] = []
        for idx in range(n):
            reason = axis_reason[idx]
            if reason is not None:
                by_stage.append(Money(None, reason, False))
                continue
            total = _ZERO
            incomplete = False
            for row in article_rows:
                cell = row.cells[idx]
                if cell is None:
                    continue  # отсутствие строки — нулевой вклад, не неполнота
                if cell.amount.value is None:
                    # Недоступность ПО СТРОКЕ: подытог не гасится, но помечается
                    # неполным — решение плана, «две недоступности разведены».
                    incomplete = True
                    continue
                if cell.rows_with_amount < cell.rows_all:
                    # Частичная свёртка ЯЧЕЙКИ (сумма есть, но не по всем
                    # строкам группы) — тот же факт, что `_row_delta_amount`
                    # уже поднимает у СТРОКИ (план, решение 10; AGENTS.md §6,
                    # приём `row_amount`/`row_amount_incomplete`): исключённая
                    # позиция не гасит подытог, но обязана пометить его неполным,
                    # иначе частичная сумма вносится молча.
                    incomplete = True
                total += cell.amount.value
            by_stage.append(Money(total, None, incomplete))
        subtotals.append(
            Subtotal(article=article, rows=len(article_rows), by_stage=by_stage, delta=_money_delta(by_stage[0], by_stage[-1]))
        )
    subtotals.sort(key=lambda s: article_sort_key(s.article))

    grand_by_stage: list[Money] = []
    for idx in range(n):
        reason = axis_reason[idx]
        if reason is not None:
            grand_by_stage.append(Money(None, reason, False))
            continue
        total = _ZERO
        incomplete = False
        for subtotal in subtotals:
            cell_money = subtotal.by_stage[idx]
            assert cell_money.value is not None
            total += cell_money.value
            incomplete = incomplete or cell_money.incomplete
        grand_by_stage.append(Money(total, None, incomplete))
    grand_delta = _money_delta(grand_by_stage[0], grand_by_stage[-1])

    moves_note = _moves_note(work_membership, [stage.stage_no for stage in stages])

    return Sheet(
        title=data.participant_title,
        stages=list(stages),
        rows=rows,
        subtotals=subtotals,
        grand_by_stage=grand_by_stage,
        grand_delta=grand_delta,
        tax_basis=tax_basis,
        moves_note=moves_note,
    )


def _moves_note(work_membership: dict[int, list[set[str]]], stage_numbers: list[int]) -> str:
    """Массовость переезда — в шапке, один раз (спека §2.8). Переклассификацией
    считается переход, где ОБА множества статей непусты и различаются;
    появление работы после пустого этапа переездом не считается."""
    moves_per_step: dict[tuple[int, int], set[int]] = defaultdict(set)
    for work_id, sets_by_stage in work_membership.items():
        for i in range(len(stage_numbers) - 1):
            set_a, set_b = sets_by_stage[i], sets_by_stage[i + 1]
            if set_a and set_b and set_a != set_b:
                moves_per_step[(stage_numbers[i], stage_numbers[i + 1])].add(work_id)

    works_total = len(work_membership)
    if not moves_per_step:
        return "переездов нет"
    parts = []
    for (stage_a, stage_b), moved in sorted(moves_per_step.items()):
        pct = round(100 * len(moved) / works_total) if works_total else 0
        parts.append(f"Э{stage_a}→Э{stage_b}: {len(moved)} из {works_total} ({pct}%)")
    return " · ".join(parts)
