"""Сценарные сборщики данных для тестов сравнения договоров.

БЕЗ префикса `test_`: pytest не собирает этот модуль как набор тестов.
Универсальные фабрики сущностей живут в `tests/factories.py` и здесь только
вызываются — дублировать их нельзя, разъедутся.

Каждый сборщик задаёт ЯВНО: `vat_rate` предложения, статью, `is_chapter`, суммы
и связи лот → предложение → раздел → позиция. Причины — в шапке плана задач
(«Ловушки фикстур»):

1. `ProposalFactory` не задаёт `vat_rate` — база НДС была бы `NULL`, и ячейка
   стала бы неполной по `vat_base_unknown` вместо проверки того, что задумано.
2. Статья позиции берётся VIEW с её РАЗДЕЛА (`chapter_item_id`), а не с самой
   позиции — раздел и позиции обязаны идти парой.
3. Раздел со статьёй обязан нести `category_source='file'` — этого требует
   CHECK `ck_position_items_category_source_pairs`
   ((work_category_id IS NULL) = (category_source IS NULL)); без него вставка
   раздела со статьёй отклоняется схемой ДО того, как сборщик успеет отработать.
"""
from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa

from models import EstimateAdditionalWork, WorkCategory

VAT_20 = Decimal("20")
VAT_22 = Decimal("22")


def category_id(db, code: str) -> int:
    """id статьи классификатора по коду. Классификатор засеян миграцией 0005."""
    return db.execute(
        sa.select(WorkCategory.id).where(WorkCategory.code == code)
    ).scalar_one()


def make_proposal(factories, *, estimate, vat_rate=VAT_20, lot_key=None):
    """Лот и предложение с ЯВНОЙ ставкой НДС."""
    lot = factories.LotFactory.create(estimate=estimate, **({"lot_key": lot_key} if lot_key else {}))
    return factories.ProposalFactory.create(lot=lot, vat_rate=vat_rate)


def seed_chapter_with_positions(db, factories, *, proposal, code, amounts):
    """Раздел со статьёй `code` и позиции под ним.

    Статья ставится РАЗДЕЛУ: VIEW читает `ch.work_category_id` через
    `chapter_item_id`. Позиция со своей `work_category_id` и без раздела попала бы
    в «Нераспределённое».

    `category_source='file'` на разделе ОБЯЗАТЕЛЕН — CHECK
    `ck_position_items_category_source_pairs` требует `(work_category_id IS NULL)
    = (category_source IS NULL)`, иначе вставка раздела со статьёй отклоняется
    схемой.
    """
    chapter = factories.PositionItemFactory.create(
        proposal=proposal, is_chapter=True,
        work_category_id=category_id(db, code),
        category_source="file",
        total_cost_total=None, unit_cost_total=None,
    )
    db.flush()
    items = [
        factories.PositionItemFactory.create(
            proposal=proposal, is_chapter=False, chapter_item_id=chapter.id,
            total_cost_total=Decimal(a) if a is not None else None,
        )
        for a in amounts
    ]
    db.flush()
    return chapter, items


def seed_unallocated_positions(db, factories, *, proposal, amounts):
    """Позиции БЕЗ раздела → в VIEW `work_category_id IS NULL` (§2.1.4)."""
    items = [
        factories.PositionItemFactory.create(
            proposal=proposal, is_chapter=False, chapter_item_id=None,
            total_cost_total=Decimal(a),
        )
        for a in amounts
    ]
    db.flush()
    return items


def contract_with(db, factories, categories: dict[str, list[str]]) -> int:
    """Договор с одной сметой и предложением, статьи заданы словарём код -> суммы.

    Для задачи 3 (союз строк, состояния ячеек): каждая статья получает свой
    раздел с позициями на указанные суммы, ставка НДС фиксирована (`VAT_20`).
    Возвращает `contract_id`, а не сам договор — тестам этого слоя нужен только
    идентификатор для `load_rollups`.
    """
    estimate = factories.EstimateFactory.create()
    proposal = make_proposal(factories, estimate=estimate)
    for code, amounts in categories.items():
        seed_chapter_with_positions(db, factories, proposal=proposal, code=code, amounts=amounts)
    return estimate.contract_id


def contract_with_area(
    db, factories, categories: dict[str, list[str]], *,
    vat_rate=VAT_20, area_aboveground="50000", area_underground="50000",
):
    """Как `contract_with`, но объект несёт ТЭП и ставка НДС параметризована.

    ₽/м² и медиана (спека §2.4, §2.5) вычислимы только при известной
    `objects.area_total_sp`; без явного её задания тесты медианы стали бы
    проверять «нет ТЭП», думая, что проверяют разброс цен.
    """
    obj = factories.ObjectFactory.create(
        area_aboveground_sp=Decimal(area_aboveground),
        area_underground_sp=Decimal(area_underground),
    )
    contract = factories.ContractFactory.create(object=obj)
    estimate = factories.EstimateFactory.create(contract=contract)
    proposal = make_proposal(factories, estimate=estimate, vat_rate=vat_rate)
    for code, amounts in categories.items():
        seed_chapter_with_positions(db, factories, proposal=proposal, code=code, amounts=amounts)
    db.flush()
    return contract.id


def contract_with_disagreeing_rates(
    db, factories, *, rates: list[Decimal], area_aboveground="50000", area_underground="50000",
):
    """Одна смета, НЕСКОЛЬКО предложений с РАЗНЫМИ ставками -> `effective_display_rate`
    не определён (разногласие заявленных ставок, спека §2.3.2).

    Каждая ставка получает СВОЮ статью с деньгами: предложение без строк не
    попадает в VIEW вовсе, и его база НДС не вошла бы в `EstimateRollup.base_rates`
    (задача 4, резерв списка ставок §2.3).
    """
    obj = factories.ObjectFactory.create(
        area_aboveground_sp=Decimal(area_aboveground),
        area_underground_sp=Decimal(area_underground),
    )
    contract = factories.ContractFactory.create(object=obj)
    estimate = factories.EstimateFactory.create(contract=contract)
    codes = ["1", "2", "3", "4"][: len(rates)]
    for index, (rate, code) in enumerate(zip(rates, codes, strict=True)):
        proposal = make_proposal(factories, estimate=estimate, vat_rate=rate, lot_key=f"lot_{index}")
        seed_chapter_with_positions(db, factories, proposal=proposal, code=code, amounts=["100.00"])
    db.flush()
    return contract.id


def contract_with_undefined_display_rate(
    db, factories, *, base_rate, codes, area_aboveground="50000", area_underground="50000",
):
    """Смета с ЗАДАННЫМ числом групп одной базовой ставки и БЕЗ определённой
    ставки показа.

    Нужна ровно для одного различения (внешнее ревью, P2): резервный предвыбор
    обязан считать частоту ГРУПП VIEW, а не смет, и отличить два чтения можно
    только там, где у одной сметы групп больше, чем смет у конкурирующей ставки.
    `contract_with_disagreeing_rates` для этого не годится — там на каждую ставку
    ровно одна статья, то есть одна группа.

    Ставку показа гасит ВТОРОЕ предложение с `vat_rate = None`, а не расхождение
    ставок: `effective_display_rate` возвращает `None`, как только среди
    заявленных есть неизвестная, и такое предложение НЕ добавляет своей ставки в
    счётчик — его группы приходят с `vat_rate_base = NULL` (а строк у него и нет
    вовсе). Расхождением гасить нельзя: вторая ставка попала бы в счёт групп и
    смазала бы то самое различение, ради которого фикстура и написана.
    """
    obj = factories.ObjectFactory.create(
        area_aboveground_sp=Decimal(area_aboveground),
        area_underground_sp=Decimal(area_underground),
    )
    contract = factories.ContractFactory.create(object=obj)
    estimate = factories.EstimateFactory.create(contract=contract)

    priced = make_proposal(factories, estimate=estimate, vat_rate=base_rate, lot_key="lot_priced")
    for code in codes:
        seed_chapter_with_positions(db, factories, proposal=priced, code=code, amounts=["100.00"])

    # Предложение без строк и без заявленной ставки: гасит ставку показа сметы,
    # но в VIEW не попадает и потому в счётчик групп не вносит ничего.
    make_proposal(factories, estimate=estimate, vat_rate=None, lot_key="lot_no_rate")

    db.flush()
    return contract.id


def contract_with_amendment(
    db, factories, *, base_rate, amd_rate, base, amd,
    area_aboveground="50000", area_underground="50000",
):
    """Договор с базовой сметой и ОДНИМ допсоглашением, у каждого своя `vat_rate`.

    Допсоглашений в системе НЕТ ни одного (спека §6): весь путь корзин
    (ДГП/ДС/Итого) на живых данных не исполняется, и этот сборщик — единственный
    источник данных для задачи 4. Обе сметы кладут деньги в статью "1"; объект
    несёт ТЭП по умолчанию, чтобы медиана была вычислима без отдельной
    подготовки в каждом тесте.
    """
    obj = factories.ObjectFactory.create(
        area_aboveground_sp=Decimal(area_aboveground),
        area_underground_sp=Decimal(area_underground),
    )
    contract = factories.ContractFactory.create(object=obj)
    base_estimate = factories.EstimateFactory.create(contract=contract, amendment_no=None)
    amd_estimate = factories.EstimateFactory.create(contract=contract, amendment_no=1)

    base_proposal = make_proposal(factories, estimate=base_estimate, vat_rate=base_rate)
    amd_proposal = make_proposal(
        factories, estimate=amd_estimate, vat_rate=amd_rate, lot_key="amendment"
    )

    seed_chapter_with_positions(db, factories, proposal=base_proposal, code="1", amounts=[base])
    seed_chapter_with_positions(db, factories, proposal=amd_proposal, code="1", amounts=[amd])
    db.flush()
    return contract


def seed_additional_work(db, factories, *, proposal, code, amount, ordinal=1):
    """Разрешённая допработа со статьёй.

    `chapter_ref_raw` и `raw_line` обязательны: без первого не пустит CHECK
    `ck_estimate_additional_works_unresolved_ref`, без второго —
    `ck_estimate_additional_works_raw_line_pairs`.
    """
    work = EstimateAdditionalWork(
        proposal_id=proposal.id, ordinal=ordinal,
        title="Дополнительные работы",
        total_amount=Decimal(amount),
        work_category_id=category_id(db, code),
        chapter_ref_raw=code,
        raw_line=f"{code} Дополнительные работы",
    )
    db.add(work)
    db.flush()
    return work
