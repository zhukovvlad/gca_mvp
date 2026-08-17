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
