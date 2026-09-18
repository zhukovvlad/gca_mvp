"""Чтение входов книги «Изменения КП» (спека
2026-09-16-tender-changes-export-design.md §2.1-§2.4, план, Task 3).

Модуль отвечает ТОЛЬКО за SQL и построчные счётчики; свёртка по ключу строки,
выбор налоговой оси, Δ и тождество состава — дело чистого слоя
`services/changes_export.py` (тот же приём, что у соседа `crud/stage_summary.py`
↔ `services/stage_summary.py`).

**Предикаты цены, веса и конечности заведены здесь, а не взяты из
`crud.analytics`** (решение плана 1): те функции — приватные имена другого
модуля с другой ролью (полнота матрицы отклонений, а не сборка листа), и их
докстроки прямо объявляют, что модули проекта не тянут друг у друга приватные
символы. Согласие с `money.price.is_price`/`is_weight` доказывается тестом на
семи граничных входах, а не общим кодом (внешний оракул, а не общий предикат).

**Ни одна строка сметы не выбрасывается** (спека §2.2): запрос группы читает
ВСЕ строки `position_items` с `is_chapter = false`, без фильтра по
`catalog_positions.kind` — тем же правилом, что `v_category_totals`
(миграция 0012, `WHERE pi.is_chapter = false`, без единого слова про вид
каталожной записи). Строки за `HEADER`/`LOT_HEADER`/`TRASH` и строки без
каталожной привязки остаются в выборке; классификацию по ветке (работа /
диагностическая группа / непривязанная) наносит `services.changes_export`
поверх уже прочитанных строк — здесь она видна только как `catalog_kind` и
`catalog_position_id`, донесённые без потери.

**Статья позиции — через `chapter_item_id` → `work_category_id` строки-раздела**,
тем же самым JOIN-ом (`LEFT JOIN position_items ch ON ch.id = pi.chapter_item_id
AND ch.proposal_id = pi.proposal_id`), каким её берёт `v_category_totals`
(миграция 0012). Второе написание этого правила не заводится — расхождение
здесь означало бы, что лист и свод согласуют статью по-разному.

**Число запросов не зависит ни от числа участников, ни от числа этапов**
(спека §2.1): все данные читаются `IN (estimate_ids)`/`IN (package_ids)` по
тендеру целиком — по одному запросу на участников, на этапы, на ставки НДС
(две штуки, как у `crud.stage_summary._rates_by_estimate`), на группы позиций
и на допработы.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from crud.common import DomainError
from models import (
    CatalogPosition,
    Contractor,
    Estimate,
    EstimateAdditionalWork,
    Lot,
    Offer,
    OfferPackage,
    PositionItem,
    Proposal,
    Tender,
    TenderRound,
    UnitOfMeasure,
    WorkCategory,
)
from services import changes_export as cx

#: Тендера с таким id нет вовсе (спека §2.11, план — решение 7: коды отказов
#: заведены СВОИМ экземпляром строки, не импортом из `crud.stage_summary`).
CODE_TENDER_NOT_FOUND = "tender_not_found"
#: Ни одного участника с двумя и более сметами — сравнивать нечего (спека §2.1).
CODE_NO_COMPARABLE = "no_comparable_participants"
#: Минимум смет участника, чтобы попасть в книгу (спека §2.1): «у отторговавшего
#: один сравнивать нечего». Считается по СМЕТАМ, а не по предложениям.
MIN_STAGES = 2

_ARTICLE_UNALLOCATED_TITLE = "Нераспределённое"


def _price_ok(column: sa.ColumnElement) -> sa.ColumnElement:
    """Цена пригодна: конечна и больше нуля — ДОСЛОВНО форма миграции 0016
    (`docs/superpowers/specs/2026-09-16-tender-changes-export/gen_mockup.py`,
    `PRICE_OK`). `NaN > 0` и `Infinity > 0` в PostgreSQL истинны, поэтому
    голого `> 0` мало; `NULL > 0` даёт `NULL` (SQL-ложь), и `NULL` отсеивается
    тем же условием без отдельной проверки `IS NOT NULL`."""
    return sa.and_(
        column > 0,
        column != Decimal("NaN"),
        column != Decimal("Infinity"),
        column != Decimal("-Infinity"),
    )


def _weight_ok(column: sa.ColumnElement) -> sa.ColumnElement:
    """Вес пригоден — та же форма, что `_price_ok`, другой факт (§2.4: объём
    строки, а не цена за единицу). Отдельная функция, а не переиспользование
    `_price_ok`: если правила когда-нибудь разойдутся, менять придётся одно
    место, не разбирая, какой смысл где имелся в виду (тот же довод, что у
    `money.price.is_weight`)."""
    return sa.and_(
        column > 0,
        column != Decimal("NaN"),
        column != Decimal("Infinity"),
        column != Decimal("-Infinity"),
    )


def _finite(column: sa.ColumnElement) -> sa.ColumnElement:
    """Конечность денежной/числовой величины — то же правило, что несёт
    `v_category_totals` (миграция 0012) для суммы: нефинитная строка считается
    в присутствии (`rows_all`), но не входит в сумму. `IS NOT NULL` — явным
    первым конъюнктом (форма `finite()` из `gen_mockup.py`), а не понадеявшись
    на трёхзначную логику `NULL <> x`."""
    return sa.and_(
        column.is_not(None),
        column != Decimal("NaN"),
        column != Decimal("Infinity"),
        column != Decimal("-Infinity"),
    )


def _article_ref(category_id: int | None, code: str | None, title: str | None,
                  sort_order: int | None) -> cx.ArticleRef:
    """Строка без статьи (`category_id is None`) — сентинел «Нераспределённое»,
    СВОИМ экземпляром `cx.ArticleRef` (докстрока `cx.UNALLOCATED_ARTICLE`:
    сравнение датакласса — по значению, не по тождеству объекта)."""
    if category_id is None:
        return cx.ArticleRef(code=None, title=_ARTICLE_UNALLOCATED_TITLE, sort_order=None)
    return cx.ArticleRef(code=code, title=title, sort_order=sort_order)


@dataclass(frozen=True)
class _EstimateStage:
    estimate_id: int
    stage_no: int
    label: str | None
    held_on: dt.date | None


@dataclass(frozen=True)
class _Participant:
    package_id: int
    title: str
    estimates: list[_EstimateStage]


def _participants(db: Session, tender_id: int) -> list[_Participant]:
    """Участники тендера с ДВУМЯ И БОЛЕЕ сметами (спека §2.1) — считается по
    `estimates`, не по `offers`: `JOIN Estimate` внутренним соединением роняет
    из счёта предложение без сметы, и участник с двумя предложениями, у
    одного из которых сметы нет, в книгу не попадает. Два запроса, оба
    `IN (…)` по тендеру/участникам целиком — число запросов не растёт с
    числом участников."""
    package_rows = db.execute(
        sa.select(OfferPackage.id, Contractor.title)
        .select_from(OfferPackage)
        .join(Contractor, Contractor.id == OfferPackage.contractor_id)
        .join(Offer, Offer.package_id == OfferPackage.id)
        .join(Estimate, Estimate.offer_id == Offer.id)
        .where(OfferPackage.tender_id == tender_id)
        .group_by(OfferPackage.id, Contractor.title)
        .having(sa.func.count(Estimate.id) >= MIN_STAGES)
        .order_by(Contractor.title)
    ).all()
    if not package_rows:
        return []

    package_ids = [row.id for row in package_rows]
    titles = {row.id: row.title for row in package_rows}

    stage_rows = db.execute(
        sa.select(OfferPackage.id.label("package_id"), Estimate.id.label("estimate_id"),
                  TenderRound.stage_no, TenderRound.label, TenderRound.held_on)
        .select_from(Estimate)
        .join(Offer, Offer.id == Estimate.offer_id)
        .join(OfferPackage, OfferPackage.id == Offer.package_id)
        .join(TenderRound, TenderRound.id == Offer.round_id)
        .where(OfferPackage.id.in_(package_ids))
        .order_by(OfferPackage.id, TenderRound.stage_no)
    ).all()

    by_package: dict[int, list[_EstimateStage]] = defaultdict(list)
    for row in stage_rows:
        by_package[row.package_id].append(
            _EstimateStage(estimate_id=row.estimate_id, stage_no=row.stage_no,
                            label=row.label, held_on=row.held_on)
        )
    return [_Participant(package_id=pid, title=titles[pid], estimates=by_package[pid])
            for pid in package_ids]


def _rates(db: Session, estimate_ids: Sequence[int]) -> dict[int, Decimal | None]:
    """База НДС сметы — `COALESCE(estimates.vat_rate_base_override, единогласие
    Proposal.vat_rate)` (спека §2.9): то же правило, что у `v_category_totals`
    и у `crud.stage_summary._rates_by_estimate` — своя копия, не импорт (план,
    решение 1: второй модуль своего домена не тянет чужой приватный код)."""
    if not estimate_ids:
        return {}
    overrides = dict(db.execute(
        sa.select(Estimate.id, Estimate.vat_rate_base_override).where(Estimate.id.in_(estimate_ids))
    ).all())
    declared: dict[int, set] = {e: set() for e in estimate_ids}
    for estimate_id, rate in db.execute(
        sa.select(Lot.estimate_id, Proposal.vat_rate).join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id.in_(estimate_ids))
    ).all():
        declared[estimate_id].add(rate)
    out: dict[int, Decimal | None] = {}
    for e in estimate_ids:
        if overrides.get(e) is not None:
            out[e] = overrides[e]
        else:
            s = declared[e]
            out[e] = next(iter(s)) if len(s) == 1 and None not in s else None
    return out


@dataclass(frozen=True)
class _RawGroup:
    article: cx.ArticleRef
    catalog_position_id: int | None
    catalog_kind: str | None
    work_title: str | None
    unit: str | None
    rows_all: int
    rows_with_amount: int
    rows_with_mix: int
    rows_priced: int
    amount: Decimal | None
    components: cx.Components
    quantity: Decimal | None
    price_num: Decimal | None
    price_den: Decimal | None
    unit_components_num: cx.Components
    numbers: tuple[str, ...]


def _groups(db: Session, estimate_ids: Sequence[int]) -> dict[int, list[_RawGroup]]:
    """Ветвь «позиции» — ОДИН запрос на весь тендер, `IN (estimate_ids)` (спека
    §2.2, §2.4). Три множества строк разведены запросом: `rows_all` — по всем
    строкам `is_chapter = false`, независимо от денег; `amount`/`rows_with_amount`
    — по конечным `total_cost_total` (`_finite`); `rows_priced`/`price_num`/
    `price_den` — по строкам, где пригодны И цена, И вес (`_price_ok` И
    `_weight_ok`, спека §2.4) — нерасценённая строка не входит в матрицу вовсе,
    поэтому не занижает цену группы. `rows_with_mix` — строки, где конечен ИТОГ
    И все три составляющие (SQL-классификация, которую спека §2.2 DoD 15
    называет непроверяемой синтетическим прогоном).

    Форма — прямой перенос `GROUPS_SQL` гейта 1 (`gen_mockup.py`) на
    SQLAlchemy, с добавлением `Lot.estimate_id` в SELECT/GROUP BY вместо
    `stage_no`/`participant` (там — один участник литералом, здесь — весь
    тендер разом; `stage_no` восстанавливается вызывающим кодом по карте
    `estimate_id -> stage_no`, которую строит `_participants`).
    """
    if not estimate_ids:
        return {}

    chapter = aliased(PositionItem, name="cx_chapter")
    total = PositionItem.total_cost_total
    works = PositionItem.total_cost_works
    materials = PositionItem.total_cost_materials
    indirect = PositionItem.total_cost_indirect_costs
    # Числитель СОСТАВА НА ЕДИНИЦУ — от `unit_cost_*` (цена составляющей за
    # единицу), а НЕ от `total_cost_*` (абсолютная составляющая строки, уже
    # включающая объём): `_per_unit_mix` делит этот числитель на `price_den`
    # (сумму весов), и деление ВТОРОЙ РАЗ на объём величины, уже умноженной на
    # объём (`total_cost_* × qty`), даёт число, которое растёт вместе с
    # объёмом строки — ровно тот ложный сигнал «состав изменился», который
    # спека §2.7 запрещает («по абсолютам один лишь рост объёма выглядел бы
    # сменой состава»). `unit_works`/`unit_materials`/`unit_indirect` — ОТДЕЛЬНЫЕ
    # переменные от `works`/`materials`/`indirect`: те остаются на `c_works`/
    # `c_materials`/`c_indirect` (абсолютный состав `GroupRow.components`,
    # спека §2.3) и этой правкой не тронуты.
    unit_works = PositionItem.unit_cost_works
    unit_materials = PositionItem.unit_cost_materials
    unit_indirect = PositionItem.unit_cost_indirect_costs
    qty = PositionItem.suggested_quantity
    price = PositionItem.unit_cost_total

    priced = sa.and_(_price_ok(price), _weight_ok(qty))
    mix_ok = sa.and_(_finite(total), _finite(works), _finite(materials), _finite(indirect))

    rows = db.execute(
        sa.select(
            Lot.estimate_id.label("estimate_id"),
            chapter.work_category_id.label("category_id"),
            WorkCategory.code.label("article_code"),
            WorkCategory.title.label("article_title"),
            WorkCategory.sort_order.label("article_sort_order"),
            PositionItem.catalog_position_id.label("catalog_position_id"),
            CatalogPosition.standard_job_title.label("work_title"),
            CatalogPosition.kind.label("catalog_kind"),
            UnitOfMeasure.code.label("unit_code"),
            sa.func.count().label("rows_all"),
            sa.func.count().filter(_finite(total)).label("rows_with_amount"),
            sa.func.sum(total).filter(_finite(total)).label("amount"),
            sa.func.sum(works).filter(_finite(works)).label("c_works"),
            sa.func.sum(materials).filter(_finite(materials)).label("c_materials"),
            sa.func.sum(indirect).filter(_finite(indirect)).label("c_indirect"),
            sa.func.count().filter(mix_ok).label("rows_with_mix"),
            sa.func.sum(qty).filter(_finite(qty)).label("qty"),
            sa.func.count().filter(priced).label("rows_priced"),
            sa.func.sum(price * qty).filter(priced).label("price_num"),
            sa.func.sum(qty).filter(priced).label("price_den"),
            sa.func.sum(unit_works * qty).filter(priced).label("unit_works_num"),
            sa.func.sum(unit_materials * qty).filter(priced).label("unit_materials_num"),
            sa.func.sum(unit_indirect * qty).filter(priced).label("unit_indirect_num"),
            sa.func.array_agg(PositionItem.item_number_in_proposal)
              .filter(PositionItem.item_number_in_proposal.is_not(None))
              .label("numbers"),
        )
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .outerjoin(chapter, sa.and_(chapter.id == PositionItem.chapter_item_id,
                                     chapter.proposal_id == PositionItem.proposal_id))
        .outerjoin(WorkCategory, WorkCategory.id == chapter.work_category_id)
        .outerjoin(CatalogPosition, CatalogPosition.id == PositionItem.catalog_position_id)
        .outerjoin(UnitOfMeasure, UnitOfMeasure.id == PositionItem.unit_id)
        .where(Lot.estimate_id.in_(estimate_ids), PositionItem.is_chapter.is_(False))
        .group_by(Lot.estimate_id, chapter.work_category_id, WorkCategory.code, WorkCategory.title,
                  WorkCategory.sort_order, PositionItem.catalog_position_id,
                  CatalogPosition.standard_job_title, CatalogPosition.kind, UnitOfMeasure.code)
    ).all()

    out: dict[int, list[_RawGroup]] = defaultdict(list)
    for row in rows:
        article = _article_ref(row.category_id, row.article_code, row.article_title, row.article_sort_order)
        out[row.estimate_id].append(_RawGroup(
            article=article,
            catalog_position_id=row.catalog_position_id,
            catalog_kind=row.catalog_kind,
            work_title=row.work_title,
            unit=row.unit_code,
            rows_all=row.rows_all,
            rows_with_amount=row.rows_with_amount,
            rows_with_mix=row.rows_with_mix,
            rows_priced=row.rows_priced,
            amount=row.amount,
            components=cx.Components(row.c_works, row.c_materials, row.c_indirect),
            quantity=row.qty,
            price_num=row.price_num,
            price_den=row.price_den,
            unit_components_num=cx.Components(row.unit_works_num, row.unit_materials_num, row.unit_indirect_num),
            numbers=tuple(row.numbers) if row.numbers else (),
        ))
    return out


@dataclass(frozen=True)
class _RawAdditional:
    article: cx.ArticleRef
    lot_key: str
    chapter_ref_raw: str | None
    work_title: str | None
    amount: Decimal | None
    rows_all: int
    rows_with_amount: int


def _additional(db: Session, estimate_ids: Sequence[int]) -> dict[int, list[_RawAdditional]]:
    """Ветвь «допработы» — ключ `(lots.lot_key, chapter_ref_raw)`, НЕ `lots.id`
    (спека §2.2): `lots.id` свой у каждой сметы раунда, и допработа,
    присутствующая в двух раундах, рассыпалась бы на цепочку ложных
    появлений. Один запрос, `IN (estimate_ids)`."""
    if not estimate_ids:
        return {}
    total = EstimateAdditionalWork.total_amount
    rows = db.execute(
        sa.select(
            Lot.estimate_id.label("estimate_id"),
            EstimateAdditionalWork.work_category_id.label("category_id"),
            WorkCategory.code.label("article_code"),
            WorkCategory.title.label("article_title"),
            WorkCategory.sort_order.label("article_sort_order"),
            Lot.lot_key.label("lot_key"),
            EstimateAdditionalWork.chapter_ref_raw.label("chapter_ref_raw"),
            sa.func.min(EstimateAdditionalWork.title).label("work_title"),
            sa.func.sum(total).filter(_finite(total)).label("amount"),
            sa.func.count().label("rows_all"),
            sa.func.count().filter(_finite(total)).label("rows_with_amount"),
        )
        .select_from(EstimateAdditionalWork)
        .join(Proposal, Proposal.id == EstimateAdditionalWork.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .outerjoin(WorkCategory, WorkCategory.id == EstimateAdditionalWork.work_category_id)
        .where(Lot.estimate_id.in_(estimate_ids))
        .group_by(Lot.estimate_id, EstimateAdditionalWork.work_category_id, WorkCategory.code,
                  WorkCategory.title, WorkCategory.sort_order, Lot.lot_key,
                  EstimateAdditionalWork.chapter_ref_raw)
    ).all()

    out: dict[int, list[_RawAdditional]] = defaultdict(list)
    for row in rows:
        article = _article_ref(row.category_id, row.article_code, row.article_title, row.article_sort_order)
        out[row.estimate_id].append(_RawAdditional(
            article=article,
            lot_key=row.lot_key,
            chapter_ref_raw=row.chapter_ref_raw,
            work_title=row.work_title,
            amount=row.amount,
            rows_all=row.rows_all,
            rows_with_amount=row.rows_with_amount,
        ))
    return out


def load_book(db: Session, tender_id: int) -> list[cx.SheetInput]:
    """Собрать входы книги «Изменения КП» для всех сравнимых участников тендера
    (спека §2.1). Отказы: тендера нет — `404 tender_not_found`; ни одного
    участника с `MIN_STAGES` и более сметами — `422 no_comparable_participants`
    (не пустая книга, спека §2.1)."""
    tender = db.get(Tender, tender_id)
    if tender is None:
        raise DomainError(404, f"Тендер {tender_id} не найден.", code=CODE_TENDER_NOT_FOUND)

    participants = _participants(db, tender_id)
    if not participants:
        raise DomainError(
            422,
            "В тендере нет участников с двумя и более сметами — сравнивать нечего.",
            code=CODE_NO_COMPARABLE,
        )

    estimate_ids = [es.estimate_id for p in participants for es in p.estimates]
    rates = _rates(db, estimate_ids)
    groups_by_estimate = _groups(db, estimate_ids)
    additional_by_estimate = _additional(db, estimate_ids)

    sheets: list[cx.SheetInput] = []
    for participant in participants:
        stages = [
            cx.StageInput(stage_no=es.stage_no, label=es.label, held_on=es.held_on,
                          vat_rate_base=rates.get(es.estimate_id))
            for es in participant.estimates
        ]
        groups: list[cx.GroupRow] = []
        additional: list[cx.AdditionalRow] = []
        for es in participant.estimates:
            for raw in groups_by_estimate.get(es.estimate_id, ()):
                groups.append(cx.GroupRow(
                    stage_no=es.stage_no,
                    article=raw.article,
                    catalog_position_id=raw.catalog_position_id,
                    catalog_kind=raw.catalog_kind,
                    work_title=raw.work_title,
                    unit=raw.unit,
                    rows_all=raw.rows_all,
                    rows_with_amount=raw.rows_with_amount,
                    rows_with_mix=raw.rows_with_mix,
                    rows_priced=raw.rows_priced,
                    amount=raw.amount,
                    components=raw.components,
                    quantity=raw.quantity,
                    price_num=raw.price_num,
                    price_den=raw.price_den,
                    unit_components_num=raw.unit_components_num,
                    numbers=raw.numbers,
                ))
            for raw in additional_by_estimate.get(es.estimate_id, ()):
                additional.append(cx.AdditionalRow(
                    stage_no=es.stage_no,
                    article=raw.article,
                    lot_key=raw.lot_key,
                    chapter_ref_raw=raw.chapter_ref_raw,
                    work_title=raw.work_title,
                    amount=raw.amount,
                    rows_all=raw.rows_all,
                    rows_with_amount=raw.rows_with_amount,
                ))
        sheets.append(cx.SheetInput(
            participant_title=participant.title,
            stages=stages,
            groups=groups,
            additional=additional,
        ))
    return sheets
