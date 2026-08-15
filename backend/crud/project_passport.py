"""Паспорт проекта по статьям классификатора (фаза 7, Ф6).

Модуль отдельный от `crud/analytics.py` намеренно. Там код фазы 6 со своим
правилом «последняя смета» (`AGENTS.md` §6); здесь — правило «исходная смета»
(спека Ф6 §2.4). Это два разных правила, и вторая копия первого была бы
дефектом, а не экономией. Спека §2.1 требует код фазы 6 не трогать.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from crud.common import DomainError, iso
from models import (
    Contract,
    Contractor,
    Estimate,
    EstimateAdditionalWork,
    EstimateCategoryOverride,
    EstimateRawData,
    Lot,
    ObjectModel,
    PositionItem,
    Proposal,
    ProposalSummaryLine,
    RateClass,
    User,
    WorkCategory,
)
from money.vat import (
    AmountStatus,
    NetReconciliation,
    NetStatus,
    check_proposal_net,
    effective_display_rate,
    fold_net_reconciliation,
    quantize_money,
    restate_gross,
)
from parser.constants import (
    JSON_KEY_TOTAL_COST_EXCLUDING_VAT,
    JSON_KEY_TOTAL_COST_INCLUDING_VAT,
)
from services.category_rollup import (
    SOURCE_ADDITIONAL_WORKS,
    SOURCE_POSITIONS,
    CategoryNode,
    CategoryRef,
    DirectTotals,
    build_tree,
)

# ---------------------------------------------------------------------------
#  VIEW прямых сумм по статьям как объект SQLAlchemy
# ---------------------------------------------------------------------------

#: `v_category_totals` создаётся raw SQL в миграции 0010 (регруппирован по
#: предложению и его базовой ставке НДС миграцией 0012 — спека пересчёта §2.4) и
#: потому невидим для `Base.metadata` — ровно как `v_position_deviation_inputs`
#: фазы 2. Отражение объявлено здесь, чтобы запросы собирались `select()`-ом, а
#: не склеивались из строк: строковый SQL не проверяется ничем до попадания в БД.
#:
#: Цена отражения — оно может разъехаться с настоящим VIEW. Это ловит
#: `test_category_totals_view.py::test_declared_view_columns_match_the_database`,
#: сверяя объявление с `information_schema` ПО ПОРЯДКУ КОЛОНОК — порядок здесь
#: обязан совпасть с порядком в `CREATE VIEW` миграции 0012, иначе сверка падает
#: сообщением про несовпадение кортежей, из которого причина не читается.
CATEGORY_TOTALS = sa.table(
    "v_category_totals",
    sa.column("estimate_id", sa.BigInteger),
    sa.column("proposal_id", sa.BigInteger),
    sa.column("vat_rate_base", sa.Numeric),
    sa.column("work_category_id", sa.BigInteger),
    sa.column("source", sa.Text),
    sa.column("amount", sa.Numeric),
    sa.column("row_count", sa.Integer),
    sa.column("rows_with_amount", sa.Integer),
    sa.column("rows_not_finite", sa.Integer),
)

#: Объявленный порядок колонок — он же ожидаемый в БД (`ordinal_position`).
DECLARED_CATEGORY_TOTALS_COLUMNS = tuple(c.name for c in CATEGORY_TOTALS.columns)

#: Имена источников (`SOURCE_POSITIONS`, `SOURCE_ADDITIONAL_WORKS`) объявлены не
#: здесь, а в `services/category_rollup.py`, и импортированы выше. Причина — в его
#: докстроке: обратное направление замкнуло бы модули в цикл и втянуло бы `models`
#: с `Session` в модуль, объявленный чистым. Источник истины остаётся один.


def _finite_amount(column: sa.ColumnElement) -> sa.ColumnElement:
    """Предикат годности денежной колонки: истина только на конечном числе.

    `NULL` явно не проверяется — сравнение `NULL <> число` само даёт `NULL`
    (SQL-ложь в `WHERE`/`CASE WHEN`), и колонка без значения естественно
    выпадает из суммы и из счётчика «расценено», не будучи при этом «не
    числом» (`rows_not_finite` её не считает — она просто отсутствует).

    Это ТА ЖЕ проверка, что несёт `v_category_totals` в неизменяемой миграции
    0010 (`amount <> 'NaN'::numeric AND amount <> 'Infinity'::numeric AND
    amount <> '-Infinity'::numeric`), но не общая с ней реализация: строку
    миграции нельзя ни импортировать, ни переиспользовать — она застыла
    навсегда, а `_section_metrics` ниже агрегирует по РАЗДЕЛУ, тогда как VIEW
    агрегирует по `(estimate_id, work_category_id)`, и её агрегат для этой
    задачи не годится ни в каком виде. Поэтому предикат выражен здесь ОДИН
    РАЗ, и весь модуль, которому он нужен, пользуется этой функцией, а не
    повторяет три сравнения инлайном, — это ИМЕНОВАННАЯ ГРАНИЦА между двумя
    местами, которые обязаны совпадать по смыслу, а не общий код: расхождение
    между этой функцией и строкой миграции 0010 придётся замечать вручную,
    страхует его только парность двух докстрок.
    """
    return sa.and_(
        column != Decimal("NaN"),
        column != Decimal("Infinity"),
        column != Decimal("-Infinity"),
    )


# ---------------------------------------------------------------------------
#  Исходная смета договора (спека §2.4)
# ---------------------------------------------------------------------------

def _source_estimate(db: Session, contract_id: int) -> Estimate | None:
    """Исходная смета договора: `amendment_no IS NULL` (спека Ф6 §2.4).

    НЕ переиспользует `latest_estimates()`/`get_latest_estimate()` из
    `crud/analytics.py`. Там правило другое — «последняя смета» (максимальный
    `amendment_no`, AGENTS.md §6) для сквозной матрицы и паспорта объекта фазы 6.
    Паспорт проекта (эта фаза) наоборот показывает КАК ПОДАНО изначально —
    правило «исходная смета». Это два разных правила по одному и тому же полю, и
    вторая копия «последней сметы» под другим именем была бы дефектом (молча
    показывала бы допсоглашение там, где нужен оригинал), а не экономией кода.
    """
    return db.execute(
        sa.select(Estimate).where(
            Estimate.contract_id == contract_id,
            Estimate.amendment_no.is_(None),
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
#  Нераспределённое: две разные причины одного следствия (спека §2.6)
# ---------------------------------------------------------------------------

def _unallocated_breakdown(db: Session, estimate_id: int) -> tuple[int, int]:
    """Две РАЗНЫЕ причины денег без статьи, посчитанные раздельно (спека §2.6).

    `chapters` — сколько разных строк-разделов без статьи несут под собой хотя
    бы одну позицию: раздел заведён, но не привязан к классификатору.
    `rows_outside_structure` — сколько позиций вовсе не сослались на раздел
    (`chapter_item_id IS NULL`): структуры нет вообще, это не то же самое, что
    «раздел есть, но без статьи». Слияние этих двух счётчиков в один стёрло бы
    разницу между «поправить привязку раздела» и «строка сиротлива» — а это
    разные действия для человека, разбирающего паспорт.
    """
    referencing_position = aliased(PositionItem, name="referencing_position")
    chapter = aliased(PositionItem, name="unallocated_chapter")

    chapters = db.execute(
        sa.select(sa.func.count(sa.distinct(chapter.id)))
        .select_from(chapter)
        .join(
            referencing_position,
            sa.and_(
                referencing_position.chapter_item_id == chapter.id,
                referencing_position.proposal_id == chapter.proposal_id,
            ),
        )
        .join(Proposal, Proposal.id == chapter.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(
            Lot.estimate_id == estimate_id,
            chapter.is_chapter.is_(True),
            chapter.work_category_id.is_(None),
            referencing_position.is_chapter.is_(False),
        )
    ).scalar_one()

    rows_outside_structure = db.execute(
        sa.select(sa.func.count())
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(
            Lot.estimate_id == estimate_id,
            PositionItem.is_chapter.is_(False),
            PositionItem.chapter_item_id.is_(None),
        )
    ).scalar_one()

    return chapters, rows_outside_structure


# ---------------------------------------------------------------------------
#  Допработы вне VIEW: строки, а не сумма (задача 5)
# ---------------------------------------------------------------------------

def _extras_select(estimate_id: int) -> sa.Select:
    """Строки допработ одной сметы — вне `v_category_totals` намеренно: там
    деньги свёрнуты в одну сумму на статью, а экрану паспорта нужны СТРОКИ
    (спека §2.9 п.6), и достать строки из готовой суммы невозможно.

    Порядок ОБЪЯВЛЕН ЯВНО: `ORDER BY estimate_additional_works.proposal_id,
    estimate_additional_works.ordinal`. Ключ полный (`UNIQUE (proposal_id,
    ordinal)`, Ф4) — порядок тотален, ничьих нет. Без явного `ORDER BY`
    PostgreSQL не обязан возвращать строки в каком-либо порядке, и паспорт
    перетасовывал бы строки между запусками (замерено в
    `test_extras_select_declares_an_explicit_order`: без этого предложения
    Bitmap Heap Scan и Index Only Scan на `gca_test` дают РАЗНЫЙ порядок).

    Граница: `proposal_id` — суррогатный ключ, порядок лотов здесь — это
    порядок их СОЗДАНИЯ. Он совпадает с порядком файла потому, что импорт
    вставляет лоты в порядке парсера, а не потому, что это объявлено
    контрактом. Доменный ключ `lots.lot_key` отвергнут измерением: это строка,
    и при десяти лотах `lot_10` встала бы раньше `lot_2`, тогда как
    `proposal_id` в этом же случае даёт правильный файловый порядок.
    """
    return (
        sa.select(
            EstimateAdditionalWork.id,
            EstimateAdditionalWork.ordinal,
            EstimateAdditionalWork.title,
            EstimateAdditionalWork.total_amount,
            EstimateAdditionalWork.work_category_id,
        )
        .select_from(EstimateAdditionalWork)
        .join(Proposal, Proposal.id == EstimateAdditionalWork.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id == estimate_id)
        .order_by(EstimateAdditionalWork.proposal_id, EstimateAdditionalWork.ordinal)
    )


def _extras_by_category(db: Session, estimate_id: int) -> tuple[dict[int, list[dict]], list[dict]]:
    """Строки допработ, разложенные по статьям: `({category_id: [rows]},
    [строки без статьи])`. Каждая строка — `{id, ordinal, title, amount}`;
    `id`/`ordinal` — чтобы строку можно было найти в БД, не гадая.

    Инвариант, который это обязано сохранить (проверен `test_extras_of_a_
    node_sum_to_the_view_branch`): сумма `extras` узла равна ветке
    `additional_works` того же узла в `v_category_totals` — те же самые
    строки, посчитанные VIEW и перечисленные здесь по отдельности. Без этой
    проверки два запроса могли бы молча разъехаться.
    """
    rows = db.execute(_extras_select(estimate_id)).all()
    by_category: dict[int, list[dict]] = {}
    unallocated: list[dict] = []
    for row in rows:
        item = {"id": row.id, "ordinal": row.ordinal, "title": row.title, "amount": row.total_amount}
        if row.work_category_id is None:
            unallocated.append(item)
        else:
            by_category.setdefault(row.work_category_id, []).append(item)
    return by_category, unallocated


# ---------------------------------------------------------------------------
#  Собственные разделы узла (спека §2.9 п.6 — уточнение §2.6, задача 5)
# ---------------------------------------------------------------------------

def _own_sections_select(estimate_id: int) -> sa.Select:
    """Строки-разделы (`is_chapter = true`) исходной сметы, у которых есть хотя
    бы одна прямая позиция (не раздел) под ними и которые несут статью — то
    есть разделы, давшие узлу его СОБСТВЕННЫЕ деньги.

    Зачем это поле вообще существует (закрывает внутреннее противоречие
    спеки, задокументированное здесь как её уточнение): §2.9 п.6 требует
    подписывать служебную строку «какие разделы сметы туда попали», §6
    отвергает «имя раздела как заголовок» именно В ПОЛЬЗУ этой подписи, а
    макет, одобренный на гейте 1, показывает её живьём — но форма ответа §2.6
    само поле не несёт. Эта реализация закрывает эту нестыковку спеки полем
    `own_sections` и фиксирует это как уточнение спеки.

    «Есть позиция» — `EXISTS`, а не join+DISTINCT: раздел с двумя и более
    позициями не должен размножить себя в списке.

    Порядок — НЕ `position_items.id` (прямой запрет из хвоста Ф3: то решение
    сохраняет порядок вставки, но не объявляет его контрактом). Порядок —
    числовой по ключу позиции, БЕЗ приведения типа:
    `ORDER BY proposals.id, length(position_key_in_proposal),
    position_key_in_proposal`. `::numeric` отвергнут намеренно: колонка
    объявлена `String(255)` без `CHECK`, непрерывность `1..N` держится
    СБОРКОЙ парсера, а не схемой, и на ключе, который схема не запрещает,
    привести к нечисловому виду значит уронить само ЧТЕНИЕ паспорта.
    «Длина, потом лексикографически» даёт ровно числовой порядок на цифровых
    строках без ведущих нулей и никогда не падает ни на каком входе. Граница:
    на нечисловом ключе порядок становится детерминированным, но
    произвольным.
    """
    referencing_position = aliased(PositionItem, name="own_sections_referencing_position")
    has_positions = (
        sa.select(sa.literal(1))
        .select_from(referencing_position)
        .where(
            referencing_position.chapter_item_id == PositionItem.id,
            referencing_position.proposal_id == PositionItem.proposal_id,
            referencing_position.is_chapter.is_(False),
        )
        .exists()
    )
    return (
        sa.select(
            PositionItem.id,
            PositionItem.work_category_id,
            PositionItem.chapter_number_in_proposal,
            PositionItem.job_title_in_proposal,
            PositionItem.category_source,
        )
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(
            Lot.estimate_id == estimate_id,
            PositionItem.is_chapter.is_(True),
            PositionItem.work_category_id.is_not(None),
            has_positions,
        )
        .order_by(
            Proposal.id,
            sa.func.length(PositionItem.position_key_in_proposal),
            PositionItem.position_key_in_proposal,
        )
    )


def _own_sections_by_category(db: Session, estimate_id: int) -> dict[int, list[dict]]:
    """Собственные разделы, разложенные по статьям: `{category_id: [{id,
    number, title, source}]}`. Узел без собственных денег получает пустой
    список — вызывающий код читает через `.get(id, [])`.

    `source` — `category_source` раздела (`'file'`/`'manual'`, задача 4):
    экран обязан отличать статью, пришедшую из файла, от статьи, назначенной
    аналитиком руками, — обе несут одинаковые деньги, но разное доверие.
    """
    rows = db.execute(_own_sections_select(estimate_id)).all()
    by_category: dict[int, list[dict]] = {}
    for row in rows:
        by_category.setdefault(row.work_category_id, []).append(
            {
                "id": row.id,
                "number": row.chapter_number_in_proposal,
                "title": row.job_title_in_proposal,
                "source": row.category_source,
            }
        )
    return by_category


# ---------------------------------------------------------------------------
#  Валовое ИТОГО сметы и ставка НДС (спека §2.5, правила 1-2, 5-6)
# ---------------------------------------------------------------------------

def _file_total_including_vat(db: Session, estimate_id: int) -> Decimal | None:
    """Сумма `total_cost_including_vat` по ВСЕМ предложениям ВСЕХ лотов сметы
    (спека §2.5, правило 1) — либо `None`, если нарушено хотя бы одно из условий
    правила 2: предложений нет вовсе, хотя бы одно не несёт строки с этим
    ключом, либо хотя бы одно значение пусто (`NULL`) или не `is_finite()`.

    Проверка `is_finite()` — не перестраховка, а тот же открытый хвост Ф4, что
    у `v_category_totals` (спека §1.11): `_money` пропускает `NaN`/`Infinity` в
    `numeric` при импорте, а `SUM` по такой колонке молча вернул бы `NaN`.
    Чтение паспорта не должно падать на таком мусоре — оно обязано честно
    ответить «неизвестно».
    """
    proposal_ids = db.execute(
        sa.select(Proposal.id)
        .select_from(Proposal)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id == estimate_id)
    ).scalars().all()
    if not proposal_ids:
        return None

    totals = db.execute(
        sa.select(ProposalSummaryLine.total_cost).where(
            ProposalSummaryLine.proposal_id.in_(proposal_ids),
            ProposalSummaryLine.summary_key == JSON_KEY_TOTAL_COST_INCLUDING_VAT,
        )
    ).scalars().all()
    if len(totals) != len(proposal_ids):
        return None  # хотя бы одно предложение не несёт этой строки вовсе
    if any(value is None or not value.is_finite() for value in totals):
        return None

    return sum(totals)


def _vat_rate(db: Session, estimate_id: int) -> Decimal | None:
    """Ставка НДС сметы — правило единогласия (спека §2.5, правило 5): значение
    возвращается, только если ВСЕ предложения сметы заявили ОДНУ И ТУ ЖЕ
    ставку; `None` — при разногласии, при хотя бы одном `NULL`, и когда
    предложений нет вовсе.

    Сравнения здесь — только `is None`/`is not None`/`==`, никогда
    истинностные (`if rate`, `or None`): заявленный ноль (`Decimal('0')`) ложен
    в Python, но это число, а не отсутствие ставки (спека §2.5, правило 6 — та
    же ловушка, что четырежды стоила Ф4б доказательств снятием защиты).
    """
    rates = db.execute(
        sa.select(Proposal.vat_rate)
        .select_from(Proposal)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id == estimate_id)
    ).scalars().all()
    if not rates:
        return None
    if any(rate is None for rate in rates):
        return None
    first_rate = rates[0]
    if all(rate == first_rate for rate in rates):
        return first_rate
    return None


# ---------------------------------------------------------------------------
#  Сверка выведенного нетто с файловым, по предложениям (спека §2.10, задача 5)
# ---------------------------------------------------------------------------

def _net_reconciliation(db: Session, estimate: Estimate) -> NetReconciliation:
    """Сверка выведенного нетто с файловым, по предложениям, свёрнутая в вердикт.

    ЭТО ТРЕТЬЯ, ОТДЕЛЬНАЯ диагностика (приложение оркестратора, п.3 «три
    диагностики — про разное»), и её нельзя путать ни с одной из двух других:

    * `delta_to_file_total` (выше) сверяет валовое с валовым, из исходных
      файловых денег, и НИКОГДА не зависит от поправок — трогать его отсюда
      запрещено;
    * согласованность самого файла (заявленная `proposals.vat_rate` против
      файлового блока итогов) — диагностика парсера, ей здесь не место.

    База берётся ЭФФЕКТИВНАЯ (`COALESCE(estimates.vat_rate_base_override,
    proposals.vat_rate)`) — та самая, на которую опирается пересчёт §2.4.
    Гранулярность — ОДНО ПРЕДЛОЖЕНИЕ на строку (не сумма по смете, как у
    `_file_total_including_vat` — та агрегирующая семантика этой сверке не
    подходит): `check_proposal_net` сравнивает валовое ИТОГО одного
    предложения с его же файловым нетто.

    Оба операнда читаются LEFT JOIN-ом на `proposal_summary_lines` (пара
    `(proposal_id, summary_key)` уникальна схемой, `uq_proposal_summary_lines_
    key`, — не более одной строки на предложение и ключ, дубль исключён
    ограничением). Предложение без нужной строки закономерно даёт `NULL`, и
    `check_proposal_net` обязан отличить это от найденного, но не сошедшегося
    значения — он делает это сам (`NOT_APPLICABLE`), и это тот же открытый
    хвост Ф4 (`NaN`/`Infinity`), что и у `_file_total_including_vat`: обе
    проверки годности значения инкапсулированы в `check_proposal_net`, а не
    продублированы здесь второй раз.
    """
    gross_line = aliased(ProposalSummaryLine, name="net_reconciliation_gross")
    net_line = aliased(ProposalSummaryLine, name="net_reconciliation_net")
    rows = db.execute(
        sa.select(
            Proposal.id.label("proposal_id"),
            Proposal.vat_rate,
            gross_line.total_cost.label("gross_total"),
            net_line.total_cost.label("file_net"),
        )
        .select_from(Proposal)
        .join(Lot, Lot.id == Proposal.lot_id)
        .outerjoin(
            gross_line,
            sa.and_(
                gross_line.proposal_id == Proposal.id,
                gross_line.summary_key == JSON_KEY_TOTAL_COST_INCLUDING_VAT,
            ),
        )
        .outerjoin(
            net_line,
            sa.and_(
                net_line.proposal_id == Proposal.id,
                net_line.summary_key == JSON_KEY_TOTAL_COST_EXCLUDING_VAT,
            ),
        )
        .where(Lot.estimate_id == estimate.id)
    ).all()

    override = estimate.vat_rate_base_override
    checks = [
        check_proposal_net(
            row.proposal_id,
            row.gross_total,
            row.file_net,
            override if override is not None else row.vat_rate,
        )
        for row in rows
    ]
    return fold_net_reconciliation(checks)


# ---------------------------------------------------------------------------
#  Арифметика «только известные слагаемые» (спека §2.6, правила 3, 5, 6)
# ---------------------------------------------------------------------------

def _sum_known(*values):
    """Сумма известных слагаемых; `None`, если известных нет вовсе.

    Неизвестное (`None`) — отсутствующее слагаемое, а не ноль (та же логика,
    что у `build_tree._build_node`, только применённая к корням дерева и
    «Нераспределённому», а не к одному узлу).
    """
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _share_pct(value, grand_total):
    """Доля узла от общего итога — БЕЗ округления (спека §2.6, правило 5).

    `None`, когда знаменатель непригоден (не известен или равен нулю) — ЛИБО
    когда сама сумма узла не известна. Знаменатель — ВСЕГДА `totals.amount`,
    один на все строки паспорта, включая «Нераспределённое»; округление —
    забота слоя представления, не этой функции.
    """
    if value is None or grand_total is None or grand_total == 0:
        return None
    return value / grand_total * 100


def _per_sqm(value, area_total):
    """Деньги на квадратный метр — `None` при отсутствующей площади (спека §2.6,
    правило 6). Деление на ноль здесь невозможно: `CHECK` Ф5 держит
    `area_total_sp` строго положительной, если она вообще задана."""
    if value is None or area_total is None:
        return None
    return value / area_total


# ---------------------------------------------------------------------------
#  Сборка дерева статей в плоский список (спека §2.6, правило 2)
# ---------------------------------------------------------------------------

def _flatten_categories(nodes) -> list[CategoryNode]:
    """Дерево `build_tree` в плоский список: узел, потом рекурсивно его видимые
    дети (порядок глубины — спека §2.6, правило 2). Корни уже упорядочены
    `build_tree` по `sort_order`, дети — тоже; здесь только обход."""
    flat: list = []
    for node in nodes:
        flat.append(node)
        flat.extend(_flatten_categories(node.children))
    return flat


def _quantize_if_restated(value: Decimal | None, restated_any: bool) -> Decimal | None:
    """Округлить ГОТОВОЕ денежное поле — но только если задача 8 реально что-то
    пересчитала (`restated_any`). Без поправки поле обязано остаться тем же
    объектом-по-значению, что и до задачи 8 (тождество §2.4, посимвольно): вызов
    `quantize_money` на нетронутой сумме сдвинул бы её `exponent` без всякой
    арифметики, а именно от этого сдвига и защищает ветка тождества
    `restate_gross`."""
    return quantize_money(value) if restated_any else value


def _category_dict(
    node: CategoryNode,
    grand_total,
    area_total,
    extras_by_category: dict[int, list[dict]],
    own_sections_by_category: dict[int, list[dict]],
    restated_any: bool,
) -> dict:
    ref = node.ref
    return {
        "id": ref.id,
        "code": ref.code,
        "title": ref.title,
        "parent_id": ref.parent_id,
        "is_bucket": ref.is_bucket,
        "sort_order": ref.sort_order,
        "total": _quantize_if_restated(node.total, restated_any),
        "rows": node.rows,
        "rows_priced": node.rows_priced,
        "rows_not_finite": node.rows_not_finite,
        "share_pct": _share_pct(node.total, grand_total),
        "per_sqm": _quantize_if_restated(_per_sqm(node.total, area_total), restated_any),
        "own": _quantize_if_restated(node.own, restated_any),
        "own_rows": node.own_rows,
        "own_rows_priced": node.own_rows_priced,
        "own_rows_not_finite": node.own_rows_not_finite,
        "extras": extras_by_category.get(ref.id, []),
        "own_sections": own_sections_by_category.get(ref.id, []),
    }


def _direct_totals(
    db: Session, estimate_id: int, effective_rate: Decimal | None
) -> tuple[dict[int | None, dict[str, DirectTotals]], bool, Decimal | None]:
    """Прямые суммы VIEW для одной сметы: `{work_category_id -> {source -> DirectTotals}}`.

    `None` в ключе — «Нераспределённое» (спека §2.2): позиции и допработы без
    привязки к статье.

    Строки VIEW оборачиваются в `DirectTotals`, а не передаются в `build_tree`
    сырыми `Row`. У `Row` те же имена атрибутов, и утиная типизация «сработала
    бы», но тогда юнит-тесты `build_tree` исполняли бы один тип, а продакшен —
    другой: объявленный контракт `Mapping[str, DirectTotals]` стал бы неправдой,
    а новое поле у `DirectTotals` упало бы только на живом стенде.

    **НАКОПЛЕНИЕ, а не присваивание** (задача 3 пересчёта НДС, §3 приложения).
    С миграцией 0012 VIEW группируется ещё и по `proposal_id`/`vat_rate_base`, то
    есть на статью со сметой из НЕСКОЛЬКИХ предложений придёт НЕСКОЛЬКО строк.
    Присваивание молча оставило бы только последнюю — на одном предложении (все
    сегодняшние фикстуры) это было бы незаметно, а сумма статьи стала бы неверной
    ровно там, где предложений больше одного (`test_direct_totals_accumulate_
    across_two_proposals`). `amount` складывается через `_sum_known` — та же
    NULL-семантика, что у остальной арифметики модуля: неизвестное слагаемое, а
    не ноль.

    **Ставка показа приводится ЗДЕСЬ, ДО накопления** (задача 8 пересчёта НДС,
    приложение оркестратора §1): каждая строка VIEW уже несёт СВОЮ, постоянную
    внутри себя `vat_rate_base` (группировка по `proposal_id`/`vat_rate_base` —
    ровно ради этого), поэтому `restate_gross(row.amount, row.vat_rate_base,
    effective_rate)` вызывается ДЛЯ КАЖДОЙ строки, и только РЕЗУЛЬТАТ идёт в
    `_sum_known`. Пересчитывать уже накопленную сумму статьи нельзя — в ней
    смешаны строки с разными базами. Возвращаемый `restated_any` — признак «хоть
    одна строка реально пересчитана» (не тождество): вызывающий код квантует
    деньги ответа только при `restated_any`, иначе поле осталось бы численно тем
    же, что и без задачи 8, но потеряло бы `exponent` — а посимвольное совпадение
    (спека §2.4) требует именно ветку тождества `restate_gross`, а не квантование
    того же числа.

    Третий элемент — `raw_total`, СЫРАЯ сумма `amount` по ВСЕМ строкам VIEW, ДО
    пересчёта, тем же `_sum_known`. Она нужна `delta_to_file_total` (диагностика
    ИМПОРТА, приложение оркестратора п.6): её операнды обязаны остаться
    валовыми и исходными НЕЗАВИСИМО от ставки показа, а `totals["amount"]`
    (ветка дерева статей) с задачи 8 может быть уже пересчитан — те же деньги
    статьи и «Нераспределённого», просто до и после приведения к ставке показа.
    """
    rows = db.execute(
        sa.select(CATEGORY_TOTALS).where(CATEGORY_TOTALS.c.estimate_id == estimate_id)
    ).all()
    direct: dict[int | None, dict[str, DirectTotals]] = {}
    restated_any = False
    raw_total = None
    for row in rows:
        raw_total = _sum_known(raw_total, row.amount)
        restated = restate_gross(row.amount, row.vat_rate_base, effective_rate)
        if restated.status is AmountStatus.RESTATED:
            restated_any = True
        amount = restated.amount
        bucket = direct.setdefault(row.work_category_id, {})
        existing = bucket.get(row.source)
        if existing is None:
            bucket[row.source] = DirectTotals(
                amount=amount,
                row_count=row.row_count,
                rows_with_amount=row.rows_with_amount,
                rows_not_finite=row.rows_not_finite,
            )
        else:
            bucket[row.source] = DirectTotals(
                amount=_sum_known(existing.amount, amount),
                row_count=existing.row_count + row.row_count,
                rows_with_amount=existing.rows_with_amount + row.rows_with_amount,
                rows_not_finite=existing.rows_not_finite + row.rows_not_finite,
            )
    return direct, restated_any, raw_total


def _branch(direct_for_key: dict[str, DirectTotals], source: str) -> tuple:
    """`(amount, row_count, rows_with_amount, rows_not_finite)` одной ветки
    источника, с нулями по умолчанию, если ветки нет вовсе (спека §1.11 —
    отсутствующая ветка это не то же самое, что ветка с нулевыми строками, но
    для арифметики «Нераспределённого» разница не нужна: обеих причин ноль)."""
    branch = direct_for_key.get(source)
    if branch is None:
        return None, 0, 0, 0
    return branch.amount, branch.row_count, branch.rows_with_amount, branch.rows_not_finite


# ---------------------------------------------------------------------------
#  Дерево разделов: нераспределённое, ручной разнос, справочник целиком
#  (фаза 7, разнос по статьям, задача 4)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SectionMetrics:
    """Метрики дерева разделов ОДНОГО раздела сметы: свои деньги (`own_*`) и
    свёртка всего его поддерева (`rows`/`rows_priced`/`rows_not_finite`/
    `subtree_amount`), плюс место раздела в файловой структуре
    (`parent_position_item_id`, `depth`) и его текущая статья.

    `proposal_id`/`position_key_in_proposal` не входят в ответ паспорта — они
    здесь только для устойчивого порядка `_manual_assignments`, тот же
    числовой порядок по ключу позиции, что несёт `_own_sections_select`.
    """

    proposal_id: int
    position_key_in_proposal: str
    parent_position_item_id: int | None
    depth: int
    number: str | None
    title: str
    smr_article_raw: str | None
    work_category_id: int | None
    own_amount: Decimal | None
    own_rows: int
    own_rows_priced: int
    own_rows_not_finite: int
    subtree_amount: Decimal | None
    rows: int
    rows_priced: int
    rows_not_finite: int


def _fold_subtree(
    own_amount: Decimal | None,
    own_rows: int,
    own_rows_priced: int,
    own_rows_not_finite: int,
    child_folds: Sequence[tuple[Decimal | None, int, int, int]],
) -> tuple[Decimal | None, int, int, int]:
    """Закон свёртки поддерева «только известные слагаемые» — ОДНА функция на
    оба потребителя внутри этого модуля: `_section_metrics._fold` (полная
    файловая структура, без разбора статей детей) и `_unallocated_sections.
    _unallocated_fold` (только нераспределённая часть поддерева). Та же
    логика, что у `build_tree._build_node` (спека §2.6): неизвестное (`None`)
    — отсутствующее слагаемое, а не ноль.

    Разница между двумя потребителями — не в законе свёртки (он один, здесь),
    а в том, КАКИХ детей они передают уже свёрнутыми в `child_folds`: выбор
    детей — дело вызывающего, эта функция его не знает и не должна. Именно
    поэтому предикат «правило Ф3 против сырой структуры» правится в ОДНОМ
    месте (фильтр перед вызовом), а не раздвоением этой функции.
    """
    rows = own_rows + sum(r for _, r, _, _ in child_folds)
    rows_priced = own_rows_priced + sum(p for _, _, p, _ in child_folds)
    rows_not_finite = own_rows_not_finite + sum(n for _, _, _, n in child_folds)

    summands = [own_amount] if own_amount is not None else []
    summands.extend(a for a, _, _, _ in child_folds if a is not None)
    amount = sum(summands) if summands else None

    return amount, rows, rows_priced, rows_not_finite


def _section_metrics(db: Session, estimate_id: int) -> dict[int, SectionMetrics]:
    """Метрики дерева разделов сметы — ЕДИНСТВЕННЫЙ расчёт (спека разноса
    §5.2-5.3): `_unallocated_sections` берёт из него подмножество без статьи,
    `_manual_assignments` обогащает им свои строки. Два независимых расчёта
    одних и тех же чисел разъехались бы — это и есть класс дефекта, ради
    ухода от которого существует вся фича.

    Родитель раздела — его СОБСТВЕННЫЙ `chapter_item_id`: он ведёт на
    раздел-предок точно так же, как на позициях (материализация пишет это
    поле одинаково для любой строки при импорте и при пересчёте разноса).
    Глубина — длина цепочки родителей от истинного корня файла; это НЕ то же
    самое, что глубина в выдаче `_unallocated_sections` — там корень
    переподвешен на границу выборки, а не на структуру файла целиком.

    Свёртка поддерева — «только известные слагаемые», как `build_tree.
    _build_node` (спека §2.6): раздел без своих позиций даёт `own_amount is
    None`, и это неизвестное слагаемое, а не ноль. Дерево крошечное, поэтому
    свёртка написана в Python, а не рекурсивным CTE — второе место, знающее
    закон свёртки, разъехалось бы с первым (build_tree). Сам закон — в
    `_fold_subtree`, ОДНОЙ функции на этот расчёт и на `_unallocated_sections.
    _unallocated_fold`.

    Парная граница с миграцией 0010 здесь ровно одна: предикат годности
    (`_finite_amount`). `own_totals` ниже группирует по `chapter_item_id` без
    дополнительного равенства `proposal_id` — в отличие от `v_category_totals`,
    чей JOIN несёт `ch.proposal_id = pi.proposal_id` явно. Это НЕ вторая
    парная граница, требующая отдельного именования: составной self-FK
    `fk_position_items_chapter` (`(proposal_id, chapter_item_id)` →
    `(proposal_id, id)`) делает невозможным `chapter_item_id`, ссылающийся на
    строку ДРУГОГО предложения — группировка по нему одному однозначна по
    ограничению схемы, а не по соглашению между этим запросом и VIEW.
    """
    finite = _finite_amount(PositionItem.total_cost_total)
    priced_and_finite = sa.and_(PositionItem.total_cost_total.is_not(None), finite)
    priced_not_finite = sa.and_(PositionItem.total_cost_total.is_not(None), sa.not_(finite))

    own_totals = db.execute(
        sa.select(
            PositionItem.chapter_item_id,
            sa.func.sum(sa.case((finite, PositionItem.total_cost_total))).label("own_amount"),
            sa.func.count().label("own_rows"),
            sa.func.count(sa.case((priced_and_finite, 1))).label("own_rows_priced"),
            sa.func.count(sa.case((priced_not_finite, 1))).label("own_rows_not_finite"),
        )
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(
            Lot.estimate_id == estimate_id,
            PositionItem.is_chapter.is_(False),
            PositionItem.chapter_item_id.is_not(None),
        )
        .group_by(PositionItem.chapter_item_id)
    ).all()
    own_by_chapter = {
        row.chapter_item_id: (row.own_amount, row.own_rows, row.own_rows_priced, row.own_rows_not_finite)
        for row in own_totals
    }

    chapter_rows = db.execute(
        sa.select(
            PositionItem.id,
            PositionItem.chapter_item_id,
            PositionItem.chapter_number_in_proposal,
            PositionItem.job_title_in_proposal,
            PositionItem.smr_article_raw,
            PositionItem.work_category_id,
            PositionItem.proposal_id,
            PositionItem.position_key_in_proposal,
        )
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id == estimate_id, PositionItem.is_chapter.is_(True))
    ).all()

    chapters_by_id = {row.id: row for row in chapter_rows}
    children_by_parent: dict[int, list[int]] = defaultdict(list)
    for row in chapter_rows:
        if row.chapter_item_id is not None:
            children_by_parent[row.chapter_item_id].append(row.id)

    depth_cache: dict[int, int] = {}

    def _depth(chapter_id: int) -> int:
        if chapter_id not in depth_cache:
            parent = chapters_by_id[chapter_id].chapter_item_id
            depth_cache[chapter_id] = 0 if parent is None else _depth(parent) + 1
        return depth_cache[chapter_id]

    metrics: dict[int, SectionMetrics] = {}

    def _fold(chapter_id: int) -> SectionMetrics:
        if chapter_id in metrics:
            return metrics[chapter_id]
        row = chapters_by_id[chapter_id]
        own_amount, own_rows, own_rows_priced, own_rows_not_finite = own_by_chapter.get(
            chapter_id, (None, 0, 0, 0)
        )
        # Полная файловая структура — дети берутся ВСЕ, без разбора статей
        # (в отличие от `_unallocated_sections._unallocated_fold`, которая
        # передаёт сюда же только не блокирующих наследование детей).
        children = [_fold(child_id) for child_id in children_by_parent.get(chapter_id, ())]
        child_folds = [(c.subtree_amount, c.rows, c.rows_priced, c.rows_not_finite) for c in children]
        subtree_amount, rows, rows_priced, rows_not_finite = _fold_subtree(
            own_amount, own_rows, own_rows_priced, own_rows_not_finite, child_folds
        )

        result = SectionMetrics(
            proposal_id=row.proposal_id,
            position_key_in_proposal=row.position_key_in_proposal,
            parent_position_item_id=row.chapter_item_id,
            depth=_depth(chapter_id),
            number=row.chapter_number_in_proposal,
            title=row.job_title_in_proposal,
            smr_article_raw=row.smr_article_raw,
            work_category_id=row.work_category_id,
            own_amount=own_amount,
            own_rows=own_rows,
            own_rows_priced=own_rows_priced,
            own_rows_not_finite=own_rows_not_finite,
            subtree_amount=subtree_amount,
            rows=rows,
            rows_priced=rows_priced,
            rows_not_finite=rows_not_finite,
        )
        metrics[chapter_id] = result
        return result

    for chapter_id in chapters_by_id:
        _fold(chapter_id)
    return metrics


def _unallocated_sections(db: Session, estimate_id: int) -> list[dict]:
    """Нераспределённая часть дерева разделов: подмножество `_section_metrics`
    без статьи (`work_category_id IS NULL`), минус граница §5.2 — узел, у
    которого нет ни единой строки ни у себя, ни в НАСЛЕДУЕМОЙ части поддерева
    (см. ниже): там нечего разносить, и его не должно быть в списке решений.

    **Кого решение на узле реально наследует.** Правило Ф3 «утверждение файла
    сильнее наследования» (`services/category_resolution._article_for`):
    ребёнок наследует статью предка ТОЛЬКО когда у него самого нет НИКАКОГО
    утверждения — ни валидного, ни нечитаемого, ни неизвестного справочнику
    (`smr_article_raw is None`). Ребёнок с любым СВОИМ утверждением ничего не
    наследует, даже если это утверждение не дало ему статьи (нечитаемый
    префикс или код вне справочника — тот же исход `"unassigned"`, что и у
    пустой ячейки, но по другой причине: там сам файл НЕ промолчал). Поэтому
    множество «наследует от этого узла» — это `work_category_id IS NULL AND
    smr_article_raw IS NULL`, а НЕ просто `work_category_id IS NULL`: раздел
    без статьи, но со своим (хотя бы нечитаемым) утверждением, — блокирующий
    узел, он сам был бы кандидатом на РУЧНОЕ решение (Task 1: решение на нём
    самом сильнее его же нечитаемого файлового утверждения), но решение
    ВЫШЕ него его не тронет.

    `subtree_amount`/`rows`/`rows_priced`/`rows_not_finite` здесь — НЕ те же
    числа, что несёт `_section_metrics.subtree_amount` по всей файловой
    структуре (см. докстроку `_manual_assignments` — там те же поля значат
    ДРУГОЕ, полную файловую структуру): свёртка ниже ограничена наследуемой
    частью поддерева, а не просто узлами без статьи. Блокирующий ребёнок (и
    всё, что под ним) исключён из свёртки предка целиком — решение,
    принятое на предке, его деньги не переместит, и подсказка на предке не
    должна их обещать. Свёртку считаем прямо на выходе `_section_metrics`
    (закон — `_fold_subtree`, ОДНА функция на оба потребителя, спека §2.6):
    отдельного расчёта, дублирующего `_section_metrics`, не заводим — только
    фильтр детей ПЕРЕД тем же законом свёртки. Ряды (`rows`/`rows_priced`/
    `rows_not_finite`) ограничены той же выборкой, что и `subtree_amount`, —
    иначе сумма и её знаменатели описывали бы два разных поддерева, что
    внутренне противоречиво.

    Родитель переподвешен ВНУТРЬ выборки: у корня нераспределённого куска
    `parent_position_item_id = None`, а глубина считается от ЭТОГО корня, не
    от структуры файла целиком — иначе экран получил бы отступ от того, что
    он вообще не показывает. Переподвешивание применяет ТОТ ЖЕ предикат, что
    и свёртка, а не только к детям: узел со своим утверждением (блокирующий)
    — ВСЕГДА корень своего кусочка, независимо от того, что выше него в
    файле, даже если файловый предок сам без статьи. Без этого дерево
    противоречило бы самому себе: свёртка обещает, что деньги блокирующего
    узла предок не получит, а строка блокирующего узла всё равно висела бы
    отступом под предком на экране — «родитель = сумма показанных детей»
    перестало бы держаться.

    Для узла БЕЗ своего утверждения цикл поиска ближайшего предка не
    меняется: он ищет ближайшего предка без статьи (`unassigned_ids`),
    невзирая на то, блокирующий тот предок или нет. Это корректно ровно
    потому, что у узла без своего утверждения нет статьи только если её не
    было и у прямого файлового родителя (иначе узел унаследовал бы её) —
    значит ближайший предок без статьи, кем бы он ни был, и есть настоящий
    источник наследования этого узла, а решение НА НЁМ (не выше) действительно
    дойдёт до узла через цепочку наследования.
    """
    metrics = _section_metrics(db, estimate_id)
    unassigned_ids = {pid for pid, sm in metrics.items() if sm.work_category_id is None}

    children_by_parent: dict[int, list[int]] = defaultdict(list)
    for pid, sm in metrics.items():
        if sm.parent_position_item_id is not None:
            children_by_parent[sm.parent_position_item_id].append(pid)

    def _inherits(pid: int) -> bool:
        """Наследует ли `pid` от предка: без статьи И без своего утверждения
        (см. докстроку функции) — ключевой предикат и свёртки, и переподвешивания."""
        return pid in unassigned_ids and metrics[pid].smr_article_raw is None

    # Свёртка ТОЛЬКО по наследуемой части поддерева: блокирующий ребёнок (со
    # своим утверждением, пусть и не давшим статьи) и всё, что под ним,
    # исключены целиком — их деньги решение на этом узле не переместит.
    fold_cache: dict[int, tuple[Decimal | None, int, int, int]] = {}

    def _unallocated_fold(pid: int) -> tuple[Decimal | None, int, int, int]:
        if pid not in fold_cache:
            sm = metrics[pid]
            children = [c for c in children_by_parent.get(pid, ()) if _inherits(c)]
            child_folds = [_unallocated_fold(c) for c in children]
            fold_cache[pid] = _fold_subtree(
                sm.own_amount, sm.own_rows, sm.own_rows_priced, sm.own_rows_not_finite, child_folds
            )
        return fold_cache[pid]

    rehung_parent: dict[int, int | None] = {}
    for pid in unassigned_ids:
        if not _inherits(pid):
            # Блокирующий узел — всегда корень своего кусочка (см. докстроку).
            rehung_parent[pid] = None
            continue
        parent = metrics[pid].parent_position_item_id
        while parent is not None and parent not in unassigned_ids:
            parent = metrics[parent].parent_position_item_id
        rehung_parent[pid] = parent

    depth_cache: dict[int, int] = {}

    def _rehung_depth(pid: int) -> int:
        if pid not in depth_cache:
            parent = rehung_parent[pid]
            depth_cache[pid] = 0 if parent is None else _rehung_depth(parent) + 1
        return depth_cache[pid]

    sections = []
    for pid in unassigned_ids:
        amount, rows, rows_priced, rows_not_finite = _unallocated_fold(pid)
        if rows == 0:
            continue
        sm = metrics[pid]
        sections.append(
            {
                "position_item_id": pid,
                "parent_position_item_id": rehung_parent[pid],
                "number": sm.number,
                "title": sm.title,
                "depth": _rehung_depth(pid),
                "amount": sm.own_amount,
                "subtree_amount": amount,
                "rows": rows,
                "rows_priced": rows_priced,
                "rows_not_finite": rows_not_finite,
                "smr_article_raw": sm.smr_article_raw,
            }
        )

    # Порядок — детерминированный: сначала переподвешенные корни, затем их
    # поддеревья, тем же числовым порядком ключа позиции, что у
    # `_own_sections_select`.
    sections.sort(
        key=lambda s: (
            s["depth"],
            metrics[s["position_item_id"]].proposal_id,
            len(metrics[s["position_item_id"]].position_key_in_proposal),
            metrics[s["position_item_id"]].position_key_in_proposal,
        )
    )
    return sections


def _manual_assignments(db: Session, estimate_id: int) -> list[dict]:
    """Действующие ручные решения сметы, обогащённые ТЕМИ ЖЕ метриками дерева
    разделов, что несёт `_unallocated_sections` (`_section_metrics` — расчёт
    один на двух потребителей, спека разноса §5.2-5.3).

    **`subtree_amount`/`rows`/`rows_priced`/`rows_not_finite` здесь значат
    ДРУГОЕ, чем в `unallocated.sections[]`, хотя поля называются одинаково.**
    Здесь это ПОЛНАЯ файловая свёртка `_section_metrics` — по всей структуре
    под решённым разделом, без разбора статей внутренних узлов. Она НЕ обрезана
    правилом Ф3 (см. докстроку `_unallocated_sections`) намеренно: спека прямо
    принимает, что эти числа не складываются между вложенными решениями —
    если внутри поддерева решённого раздела есть СВОЁ вложенное решение (или
    свой валидный файловый код), его деньги всё равно попадают в файловую
    свёртку внешнего решения, потому что до появления вложенного решения
    внешнее реально их накрывало, а печатаемая здесь строка — учётная запись
    «сколько денег лежит под этим решением по факту файла», а не «сколько
    денег ЭТО решение продолжает двигать прямо сейчас» (это второе — то, что
    считает `unallocated.sections[]`, и только там). Обрезать это поле тем же
    правилом было бы другой задачей — координатор явно попросил его не трогать.

    Порядок — по `subtree_amount` убывающе (крупные решения сверху; решение
    без единой строки в поддереве — `None`, оно в самом низу), затем по
    ключу позиции (числовой порядок `_own_sections_select`).
    """
    metrics = _section_metrics(db, estimate_id)
    author = aliased(User)
    rows = db.execute(
        sa.select(
            EstimateCategoryOverride.position_item_id,
            EstimateCategoryOverride.note,
            EstimateCategoryOverride.assigned_at,
            WorkCategory.id.label("work_category_id"),
            WorkCategory.code.label("category_code"),
            WorkCategory.title.label("category_title"),
            author.email.label("assigned_by_email"),
        )
        .select_from(EstimateCategoryOverride)
        .join(PositionItem, PositionItem.id == EstimateCategoryOverride.position_item_id)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .join(WorkCategory, WorkCategory.id == EstimateCategoryOverride.work_category_id)
        .join(author, author.id == EstimateCategoryOverride.assigned_by)
        .where(Lot.estimate_id == estimate_id)
    ).all()

    assignments = []
    for row in rows:
        sm = metrics[row.position_item_id]
        assignments.append(
            {
                "position_item_id": row.position_item_id,
                "parent_position_item_id": sm.parent_position_item_id,
                "number": sm.number,
                "title": sm.title,
                "depth": sm.depth,
                "amount": sm.own_amount,
                "subtree_amount": sm.subtree_amount,
                "rows": sm.rows,
                "rows_priced": sm.rows_priced,
                "rows_not_finite": sm.rows_not_finite,
                "smr_article_raw": sm.smr_article_raw,
                "work_category_id": row.work_category_id,
                "category_code": row.category_code,
                "category_title": row.category_title,
                "assigned_by_email": row.assigned_by_email,
                "assigned_at": iso(row.assigned_at),
                "note": row.note,
            }
        )

    def _sort_key(item: dict) -> tuple:
        sm = metrics[item["position_item_id"]]
        amount = item["subtree_amount"]
        rank = (0, -amount) if amount is not None else (1, Decimal(0))
        return (rank, sm.proposal_id, len(sm.position_key_in_proposal), sm.position_key_in_proposal)

    assignments.sort(key=_sort_key)
    return assignments


def _category_options(refs: Sequence[CategoryRef]) -> list[dict]:
    """Плоский список ВСЕГО справочника статей — не `passport.categories`:
    там `build_tree` прячет вложенные узлы без строк (правило видимости
    спеки §2.2), а разносить нужно и в статьи, которых в смете ещё нет вовсе
    (спека разноса §1, ключевой случай ревью). Отсортирован по `sort_order` —
    порядок отображения справочника, тот же, что несёт `build_tree` для
    корней и детей.
    """
    return [
        {"id": ref.id, "code": ref.code, "title": ref.title, "is_bucket": ref.is_bucket}
        for ref in sorted(refs, key=lambda r: r.sort_order)
    ]


# ---------------------------------------------------------------------------
#  Паспорт проекта целиком (спека §2.6)
# ---------------------------------------------------------------------------

def get_project_passport(db: Session, contract_id: int) -> dict:
    """Паспорт проекта по статьям классификатора (спека Ф6 §2.6).

    Форма ответа — решённый контракт (§2.6), ключи и вложенность менять нельзя.

    Правила, реализованные здесь (§2.6, не смягчать):

    1. Фильтр `amendment_no IS NULL` — в CRUD (`_source_estimate`), не в VIEW:
       VIEW обязан описывать своё содержимое целиком (спека §2.2).
    2. `categories` — ПЛОСКИЙ список в глубинном порядке (`_flatten_categories`);
       видимость — правило `build_tree` (корни всегда, глубже — только у кого
       есть строки).
    3. `totals.amount` — сумма ТОЛЬКО известных слагаемых (корни + unallocated);
       `None` тогда и только тогда, когда ни одна строка не вошла в сумму.
    4. Счётчики строк `totals.*` — по ВСЕЙ смете (категории + Нераспределённое),
       не по показанному дереву отдельно.
    5. `share_pct` — от ОДНОГО знаменателя (`totals.amount`) для всех строк,
       включая «Нераспределённое»; без округления.
    6. `per_sqm` — из `object.area_total_sp`; `None` при отсутствующей площади
       ИЛИ неизвестной сумме, никогда 0.
    7. `object_contracts_count` — число договоров объекта, для бейджа на экране.
    8. Договор без сметы — не 404 (карточка есть, файла ещё нет): `estimate`
       становится `None`, дерево статей и «Нераспределённое» — пустой скелет.
       Несуществующий договор — `DomainError(404, ...)`.
    9. `parser_version` — из `estimate_raw_data`, может законно отсутствовать.
    10. VIEW читается через объявленное отражение `CATEGORY_TOTALS`, `select()`-ом.
    11. `estimate.vat_rate` — правило единогласия предложений сметы (спека
        §2.5, правило 5): `_vat_rate`. Заявленный ноль — число, не отсутствие
        (правило 6) — сравнения там только `is None`/`is not None`/`==`.
    12. `totals.file_total_including_vat` — сумма файлового «Итого включая
        НДС» по всем предложениям сметы (спека §2.5, правила 1-2): `_file_
        total_including_vat`. `totals.delta_to_file_total` требует ДВА
        известных операнда (`totals.amount` и файловый итог) — `None`, если
        хотя бы один неизвестен, а не только когда неизвестен файловый итог
        (правило 3); иначе точная `Decimal`-разница (правило 4).
    13. `categories[].extras`, `unallocated.extras` — строки допработ вне VIEW
        (задача 5, `_extras_by_category`): гранулярность VIEW — одна сумма на
        статью, а экрану нужны СТРОКИ (спека §2.9 п.6).
    14. `categories[].own_sections` — разделы, давшие узлу его собственные
        деньги (задача 5, `_own_sections_by_category`) — уточнение спеки §2.6
        полем, закрывающим противоречие §2.9 п.6 / §6 (см. докстринг
        `_own_sections_select`). Каждая запись несёт `source` (`'file'`/
        `'manual'`, спека разноса, задача 4) — какая статья пришла из файла,
        а какая назначена аналитиком руками.
    15. `unallocated.sections` — дерево нераспределённых разделов
        (`_unallocated_sections`, спека разноса §5.2): включает промежуточные
        разделы без своих позиций, у которых есть деньги ниже, — без них
        разнос вершиной недоступен. `manual_assignments` — действующие
        ручные решения (`_manual_assignments`, §5.3), обогащённые ТЕМИ ЖЕ
        метриками дерева (`_section_metrics` — расчёт один на обоих
        потребителей).
    16. `category_options` — справочник статей ЦЕЛИКОМ (`_category_options`),
        не `categories`: там `build_tree` прячет вложенные узлы без строк, а
        разносить нужно и в статьи, которых в смете ещё нет вовсе. Это поле
        не зависит от наличия сметы (правило 8) — оно то же самое на обоих
        путях функции.
    17. `totals.net_reconciliation` — сверка выведенного нетто с файловым, по
        предложениям (спека §2.10, задача 5, `_net_reconciliation`): ТРЕТЬЯ,
        независимая от `delta_to_file_total` диагностика (валовое против
        валового, исходное, никогда не зависящее от поправок) и от
        согласованности файла (заявленная ставка против файлового блока
        итогов, живёт в парсере) — сравнивает `gross_to_net(валовое,
        ЭФФЕКТИВНАЯ база)` с файловым нетто. Ключ ДОБАВЛЕН, ничего в
        `delta_to_file_total` не тронуто (тождество §2.4).
    18. `totals.amount`, `per_sqm`, `categories[].total`/`own`/`per_sqm`,
        `unallocated.amount`/`per_sqm` — в СТАВКЕ ПОКАЗА (задача 8 пересчёта
        НДС, спека §5.1, `effective_display_rate`): цель, иначе перекрытая
        база, иначе единогласная заявленная ставка предложений; `None` при
        разногласии — тогда пересчёт не применяется (эффективная ставка
        отсутствует, и восстанавливать её выбором «какой-то из» нельзя).
        Пересчёт применяется К КАЖДОЙ строке VIEW ПО ЕЁ базе, ДО накопления
        (`_direct_totals`), и квантуется ОДИН раз, на границе ответа
        (`_quantize_if_restated`) — и только если хоть что-то реально
        пересчиталось, иначе поле обязано остаться посимвольно тем же, что и
        без задачи 8 (ветка тождества `restate_gross`). `delta_to_file_total`
        и `net_reconciliation` (правила 12, 17) не тронуты — они опираются на
        `raw_grand_total`/файловую базу, а не на ставку показа.
    """
    row = db.execute(
        sa.select(Contract, ObjectModel, Contractor.title, RateClass.title)
        .join(ObjectModel, ObjectModel.id == Contract.object_id)
        .join(Contractor, Contractor.id == Contract.contractor_id)
        .join(RateClass, RateClass.id == Contract.rate_class_id)
        .where(Contract.id == contract_id)
    ).first()
    if row is None:
        raise DomainError(404, f"Договор {contract_id} не найден.")
    contract, obj, contractor_title, rate_class_title = row

    object_contracts_count = db.execute(
        sa.select(sa.func.count()).select_from(Contract).where(Contract.object_id == contract.object_id)
    ).scalar_one()

    contract_dict = {
        "id": contract.id,
        "contract_number": contract.contract_number,
        "title": contract.title,
        "signer": contract.signer,
        "signed_date": iso(contract.signed_date),
        "object_id": contract.object_id,
        "object_title": obj.title,
        "contractor_title": contractor_title,
        "rate_class_title": rate_class_title,
        "advance_pct": contract.advance_pct,
        "advance_note": contract.advance_note,
        "bank_guarantee_pct": contract.bank_guarantee_pct,
        "bank_guarantee_note": contract.bank_guarantee_note,
        "retention_pct": contract.retention_pct,
        "retention_note": contract.retention_note,
        "object_contracts_count": object_contracts_count,
    }

    object_dict = {
        "id": obj.id,
        "title": obj.title,
        "area_underground_sp": obj.area_underground_sp,
        "area_aboveground_sp": obj.area_aboveground_sp,
        "area_total_sp": obj.area_total_sp,
        # Полезная площадь только ПОКАЗЫВАЕТСЯ: знаменатель `per_sqm` ниже
        # остаётся `area_total_sp` и этой фичей не меняется (спека
        # 2026-08-15 §2.2, §4) — иначе поехали бы все удельные показатели,
        # уже показанные пользователю и напечатанные.
        "area_useful_sp": obj.area_useful_sp,
    }

    refs = [
        CategoryRef(
            id=c.id, code=c.code, title=c.title, parent_id=c.parent_id,
            is_bucket=c.is_bucket, sort_order=c.sort_order,
        )
        for c in db.execute(sa.select(WorkCategory)).scalars().all()
    ]

    estimate = _source_estimate(db, contract_id)

    if estimate is None:
        # Договор без сметы — не ошибка (правило 8): карточка заведена, файл
        # ещё не загружен. Дерево статей всё равно полное — экран показывает
        # тот же скелет, что и для договора со сметой, просто без чисел.
        roots = build_tree(refs, {})
        categories = [
            _category_dict(node, None, None, {}, {}, restated_any=False)
            for node in _flatten_categories(roots)
        ]
        return {
            "contract": contract_dict,
            "object": object_dict,
            "estimate": None,
            "totals": {
                "amount": None,
                "per_sqm": None,
                "positions_rows": 0,
                "positions_rows_priced": 0,
                "positions_rows_not_finite": 0,
                "additional_works_rows": 0,
                "file_total_including_vat": None,
                "delta_to_file_total": None,
                # Форма ответа одна и та же на обоих путях функции (правило 8):
                # без сметы сверить нечего, тот же вердикт, что дал бы
                # `fold_net_reconciliation([])` на пустом списке предложений.
                "net_reconciliation": {
                    "status": NetStatus.NOT_APPLICABLE.value,
                    "delta": None,
                    "mismatched_proposal_ids": [],
                },
            },
            "categories": categories,
            "unallocated": {
                "amount": None,
                "rows": 0,
                "rows_priced": 0,
                "rows_not_finite": 0,
                "share_pct": None,
                "per_sqm": None,
                "chapters": 0,
                "rows_outside_structure": 0,
                "extras": [],
                "sections": [],
            },
            "manual_assignments": [],
            # Справочник целиком, а не пустой список: форма ответа обязана
            # быть той же, что при наличии сметы (правило 8), а классификатор
            # от наличия сметы не зависит вовсе (задача 4).
            "category_options": _category_options(refs),
        }

    parser_version = db.execute(
        sa.select(EstimateRawData.parser_version).where(EstimateRawData.estimate_id == estimate.id)
    ).scalar_one_or_none()

    # Единогласная заявленная ставка предложений сметы (правило §2.5, правило
    # 5) — считается ОДИН раз и переиспользуется как вход "declared" для
    # `effective_display_rate` (задача 8, спека §5.1): обёрнутая в
    # одноэлементный список, она несёт ровно ту же информацию, что и сырой
    # список заявленных ставок — `None` при разногласии/незнании/отсутствии
    # предложений, значение при единогласии, — так что вторая, дублирующая
    # копия того же свёртывающего запроса здесь не нужна.
    vat_rate = _vat_rate(db, estimate.id)
    effective_rate = effective_display_rate(
        estimate.vat_rate_target, estimate.vat_rate_base_override, [vat_rate]
    )

    estimate_dict = {
        "id": estimate.id,
        "amendment_no": estimate.amendment_no,
        "title": estimate.title,
        "data_prepared_on_date": iso(estimate.data_prepared_on_date),
        "parser_version": parser_version,
        "vat_rate": vat_rate,
        "vat_rate_base_override": estimate.vat_rate_base_override,
        "vat_rate_target": estimate.vat_rate_target,
        # Ставка показа одно-договорной поверхности (задача 8, спека §5.1):
        # цель, иначе перекрытая база, иначе единогласная заявленная ставка;
        # `None` при разногласии. Экран подписывает суммы паспорта этой
        # ставкой («суммы показаны с НДС N %» либо «без НДС» при 0).
        "vat_display_rate": effective_rate,
    }

    direct, restated_any, raw_grand_total = _direct_totals(db, estimate.id, effective_rate)
    roots = build_tree(refs, direct)
    flat_nodes = _flatten_categories(roots)

    # Счётчики строк — по ВСЕЙ смете (правило 4): категории плюс Нераспределённое,
    # то есть по каждой ветке `direct`, а не только по показанному дереву.
    positions_rows = positions_rows_priced = positions_rows_not_finite = 0
    additional_works_rows = 0
    for by_source in direct.values():
        _, p_rows, p_priced, p_not_finite = _branch(by_source, SOURCE_POSITIONS)
        positions_rows += p_rows
        positions_rows_priced += p_priced
        positions_rows_not_finite += p_not_finite
        _, extra_rows, _, _ = _branch(by_source, SOURCE_ADDITIONAL_WORKS)
        additional_works_rows += extra_rows

    unallocated_direct = direct.get(None, {})
    pos_amount, pos_rows, pos_priced, pos_not_finite = _branch(unallocated_direct, SOURCE_POSITIONS)
    extra_amount, extra_rows, extra_priced, extra_not_finite = _branch(
        unallocated_direct, SOURCE_ADDITIONAL_WORKS
    )
    unallocated_amount = _sum_known(pos_amount, extra_amount)
    chapters, rows_outside_structure = _unallocated_breakdown(db, estimate.id)

    extras_by_category, unallocated_extras = _extras_by_category(db, estimate.id)
    own_sections_by_category = _own_sections_by_category(db, estimate.id)

    grand_total = _sum_known(*(node.total for node in roots), unallocated_amount)
    area_total = obj.area_total_sp

    file_total_including_vat = _file_total_including_vat(db, estimate.id)
    # Сверка требует ДВА известных операнда (спека §2.5, правило 3): проверка
    # `is not None` на ОБОИХ, не только на файловом итоге — табличная сумма
    # законно неизвестна сама по себе (например, все позиции без цены), и
    # `None - Decimal` тут же уронил бы вычитание.
    #
    # Операнд слева — `raw_grand_total`, а НЕ `grand_total`: диагностика
    # ИМПОРТА сравнивает валовое с валовым, исходное, и ручные ставки на неё
    # не влияют НИКОГДА (приложение оркестратора задачи 8, п.6). С задачи 8
    # `grand_total` может быть уже приведён к ставке показа — это тождество
    # §2.4 сохранило бы совпадение только пока ставка показа не задана, а
    # `delta_to_file_total` обязана остаться той же цифрой всегда, а не только
    # в этом частном случае.
    if raw_grand_total is not None and file_total_including_vat is not None:
        delta_to_file_total = raw_grand_total - file_total_including_vat
    else:
        delta_to_file_total = None

    reconciliation = _net_reconciliation(db, estimate)
    totals = {
        "amount": _quantize_if_restated(grand_total, restated_any),
        "per_sqm": _quantize_if_restated(_per_sqm(grand_total, area_total), restated_any),
        "positions_rows": positions_rows,
        "positions_rows_priced": positions_rows_priced,
        "positions_rows_not_finite": positions_rows_not_finite,
        "additional_works_rows": additional_works_rows,
        "file_total_including_vat": file_total_including_vat,
        # Тождество §2.4 (приложение оркестратора, п.5): ключ ДОБАВЛЕН, ничего
        # из полей выше не тронуто — ни расчёт, ни порядок, ни значение.
        "delta_to_file_total": delta_to_file_total,
        "net_reconciliation": {
            "status": reconciliation.status.value,
            "delta": reconciliation.delta,
            "mismatched_proposal_ids": reconciliation.mismatched_proposal_ids,
        },
    }

    unallocated_dict = {
        "amount": _quantize_if_restated(unallocated_amount, restated_any),
        "rows": pos_rows + extra_rows,
        "rows_priced": pos_priced + extra_priced,
        "rows_not_finite": pos_not_finite + extra_not_finite,
        "share_pct": _share_pct(unallocated_amount, grand_total),
        "per_sqm": _quantize_if_restated(_per_sqm(unallocated_amount, area_total), restated_any),
        "chapters": chapters,
        "rows_outside_structure": rows_outside_structure,
        "extras": unallocated_extras,
        "sections": _unallocated_sections(db, estimate.id),
    }

    categories = [
        _category_dict(
            node, grand_total, area_total, extras_by_category, own_sections_by_category,
            restated_any=restated_any,
        )
        for node in flat_nodes
    ]

    return {
        "contract": contract_dict,
        "object": object_dict,
        "estimate": estimate_dict,
        "totals": totals,
        "categories": categories,
        "unallocated": unallocated_dict,
        "manual_assignments": _manual_assignments(db, estimate.id),
        "category_options": _category_options(refs),
    }
