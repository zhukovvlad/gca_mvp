"""Паспорт проекта по статьям классификатора (фаза 7, Ф6).

Модуль отдельный от `crud/analytics.py` намеренно. Там код фазы 6 со своим
правилом «последняя смета» (`AGENTS.md` §6); здесь — правило «исходная смета»
(спека Ф6 §2.4). Это два разных правила, и вторая копия первого была бы
дефектом, а не экономией. Спека §2.1 требует код фазы 6 не трогать.
"""
from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from crud.common import DomainError, iso
from models import (
    Contract,
    Contractor,
    Estimate,
    EstimateRawData,
    Lot,
    ObjectModel,
    PositionItem,
    Proposal,
    ProposalSummaryLine,
    RateClass,
    WorkCategory,
)
from parser.constants import JSON_KEY_TOTAL_COST_INCLUDING_VAT
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

#: `v_category_totals` создаётся raw SQL в миграции 0010 и потому невидим для
#: `Base.metadata` — ровно как `v_position_deviations` фазы 2. Отражение
#: объявлено здесь, чтобы запросы собирались `select()`-ом, а не склеивались из
#: строк: строковый SQL не проверяется ничем до попадания в БД.
#:
#: Цена отражения — оно может разъехаться с настоящим VIEW. Это ловит
#: `test_category_totals_view.py::test_declared_view_columns_match_the_database`,
#: сверяя объявление с `information_schema`; без сверки переименованная в
#: миграции колонка проявилась бы ошибкой выполнения на живом стенде.
CATEGORY_TOTALS = sa.table(
    "v_category_totals",
    sa.column("estimate_id", sa.BigInteger),
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


def _category_dict(node: CategoryNode, grand_total, area_total) -> dict:
    ref = node.ref
    return {
        "id": ref.id,
        "code": ref.code,
        "title": ref.title,
        "parent_id": ref.parent_id,
        "is_bucket": ref.is_bucket,
        "sort_order": ref.sort_order,
        "total": node.total,
        "rows": node.rows,
        "rows_priced": node.rows_priced,
        "rows_not_finite": node.rows_not_finite,
        "share_pct": _share_pct(node.total, grand_total),
        "per_sqm": _per_sqm(node.total, area_total),
        "own": node.own,
        "own_rows": node.own_rows,
        "own_rows_priced": node.own_rows_priced,
        "own_rows_not_finite": node.own_rows_not_finite,
    }


def _direct_totals(
    db: Session, estimate_id: int
) -> dict[int | None, dict[str, DirectTotals]]:
    """Прямые суммы VIEW для одной сметы: `{work_category_id -> {source -> DirectTotals}}`.

    `None` в ключе — «Нераспределённое» (спека §2.2): позиции и допработы без
    привязки к статье.

    Строки VIEW оборачиваются в `DirectTotals`, а не передаются в `build_tree`
    сырыми `Row`. У `Row` те же имена атрибутов, и утиная типизация «сработала
    бы», но тогда юнит-тесты `build_tree` исполняли бы один тип, а продакшен —
    другой: объявленный контракт `Mapping[str, DirectTotals]` стал бы неправдой,
    а новое поле у `DirectTotals` упало бы только на живом стенде.
    """
    rows = db.execute(
        sa.select(CATEGORY_TOTALS).where(CATEGORY_TOTALS.c.estimate_id == estimate_id)
    ).all()
    direct: dict[int | None, dict[str, DirectTotals]] = {}
    for row in rows:
        direct.setdefault(row.work_category_id, {})[row.source] = DirectTotals(
            amount=row.amount,
            row_count=row.row_count,
            rows_with_amount=row.rows_with_amount,
            rows_not_finite=row.rows_not_finite,
        )
    return direct


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
#  Паспорт проекта целиком (спека §2.6)
# ---------------------------------------------------------------------------

def get_project_passport(db: Session, contract_id: int) -> dict:
    """Паспорт проекта по статьям классификатора (спека Ф6 §2.6).

    Форма ответа — решённый контракт (§2.6), ключи и вложенность менять нельзя.
    Часть полей НАМЕРЕННО отсутствует и появится в задаче 5:
    `categories[].extras`, `categories[].own_sections`, `unallocated.extras`.

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
        categories = [_category_dict(node, None, None) for node in _flatten_categories(roots)]
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
            },
        }

    parser_version = db.execute(
        sa.select(EstimateRawData.parser_version).where(EstimateRawData.estimate_id == estimate.id)
    ).scalar_one_or_none()

    estimate_dict = {
        "id": estimate.id,
        "amendment_no": estimate.amendment_no,
        "title": estimate.title,
        "data_prepared_on_date": iso(estimate.data_prepared_on_date),
        "parser_version": parser_version,
        "vat_rate": _vat_rate(db, estimate.id),
    }

    direct = _direct_totals(db, estimate.id)
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

    grand_total = _sum_known(*(node.total for node in roots), unallocated_amount)
    area_total = obj.area_total_sp

    file_total_including_vat = _file_total_including_vat(db, estimate.id)
    # Сверка требует ДВА известных операнда (спека §2.5, правило 3): проверка
    # `is not None` на ОБОИХ, не только на файловом итоге — табличная сумма
    # законно неизвестна сама по себе (например, все позиции без цены), и
    # `None - Decimal` тут же уронил бы вычитание.
    if grand_total is not None and file_total_including_vat is not None:
        delta_to_file_total = grand_total - file_total_including_vat
    else:
        delta_to_file_total = None

    totals = {
        "amount": grand_total,
        "per_sqm": _per_sqm(grand_total, area_total),
        "positions_rows": positions_rows,
        "positions_rows_priced": positions_rows_priced,
        "positions_rows_not_finite": positions_rows_not_finite,
        "additional_works_rows": additional_works_rows,
        "file_total_including_vat": file_total_including_vat,
        "delta_to_file_total": delta_to_file_total,
    }

    unallocated_dict = {
        "amount": unallocated_amount,
        "rows": pos_rows + extra_rows,
        "rows_priced": pos_priced + extra_priced,
        "rows_not_finite": pos_not_finite + extra_not_finite,
        "share_pct": _share_pct(unallocated_amount, grand_total),
        "per_sqm": _per_sqm(unallocated_amount, area_total),
        "chapters": chapters,
        "rows_outside_structure": rows_outside_structure,
    }

    categories = [_category_dict(node, grand_total, area_total) for node in flat_nodes]

    return {
        "contract": contract_dict,
        "object": object_dict,
        "estimate": estimate_dict,
        "totals": totals,
        "categories": categories,
        "unallocated": unallocated_dict,
    }
