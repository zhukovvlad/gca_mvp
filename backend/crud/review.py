"""Очередь ручного матчинга: чтение (AGENTS.md §5 «Ручной матчинг», §7.2).

Применение решений оператора живёт в `services/review.py` и фазой 5 не
переписывается — здесь только выборки для экрана: очередь и поиск цели слияния.

**Решение фазы 5 §6.3: строки с нулём ссылок в очередь не попадают.** Это долг
фазы 4: после `replace` позиции старой сметы удаляются каскадом, а созданная ими
TO_REVIEW-строка остаётся. Решать по ней нечего — она не описывает работу ни в
одной смете. Реализовано не фильтром `HAVING`, а самим **внутренним** join-ом с
`position_items`: строка без ссылок из результата выпадает сама. Целостности это
не касается — строка продолжает держать свою нормализованную пару, и следующий
импорт той же работы переиспользует её штатным get-or-create (§5.4.3), то есть
она вернётся в очередь сама, уже со ссылками.

**Решение фазы 5 §6.4: поиск цели — ILIKE, а не FTS.** `catalog_positions.fts_vector`
это `to_tsvector('simple', standard_job_title)`, то есть вектор **словоформ**, а
`prepare_for_fts_query` отдаёт **леммы** — такой запрос не находит ничего
(замер в `docs/phase5-crud-review.md` §1.4). Ищем по двум колонкам: по
`standard_job_title` — тем, что оператор видит, и по `normalized_job_title` —
леммами его запроса, чтобы «кабеля» находило «кабелей». Цена — seq scan; она
осознанна при масштабе каталога MVP (единицы тысяч строк).
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.common import DomainError, clamp_page, iso
from models import CatalogKind, CatalogPosition, PositionItem, UnitOfMeasure
from parser.sanitize_text import normalize_job_title_with_lemmatization

log = logging.getLogger(__name__)

#: Сколько наименований из смет показывать в строке очереди. Оператору нужно
#: понять, что за работа скрыта за каталожной строкой; трёх примеров хватает,
#: а полный список на 200 позиций сделал бы таблицу нечитаемой.
SAMPLE_TITLES_LIMIT = 3

#: Потолок выдачи поиска цели: оператор выбирает глазами, а не листает.
TARGET_SEARCH_LIMIT = 50

#: Сортировки очереди. По умолчанию — самые «тяжёлые» работы первыми: их разбор
#: даёт наибольший прирост метрики §10 (решение §6.5).
SORT_BY_POSITIONS = "positions"
SORT_BY_TITLE = "title"
ALLOWED_SORTS = (SORT_BY_POSITIONS, SORT_BY_TITLE)


def _sample_titles(db: Session, catalog_ids: list[int]) -> dict[int, list[str]]:
    """Наименования из смет для строк страницы — одним запросом на страницу.

    Ограничение накладывается в SQL оконной функцией, а не срезом в Python:
    у «тяжёлой» строки очереди могут быть сотни различных наименований, и тащить
    их все, чтобы выбросить все кроме трёх, — это трафик ради ничего.
    """
    if not catalog_ids:
        return {}

    distinct_titles = (
        sa.select(
            PositionItem.catalog_position_id.label("catalog_position_id"),
            PositionItem.job_title_in_proposal.label("title"),
        )
        .where(PositionItem.catalog_position_id.in_(catalog_ids))
        .distinct()
        .subquery()
    )
    ranked = sa.select(
        distinct_titles.c.catalog_position_id,
        distinct_titles.c.title,
        sa.func.row_number()
        .over(
            partition_by=distinct_titles.c.catalog_position_id,
            order_by=distinct_titles.c.title,
        )
        .label("rn"),
    ).subquery()

    rows = db.execute(
        sa.select(ranked.c.catalog_position_id, ranked.c.title)
        .where(ranked.c.rn <= SAMPLE_TITLES_LIMIT)
        .order_by(ranked.c.catalog_position_id, ranked.c.rn)
    ).all()

    samples: dict[int, list[str]] = {}
    for catalog_id, title in rows:
        samples.setdefault(catalog_id, []).append(title)
    return samples


def list_review_queue(
    db: Session,
    *,
    q: str | None = None,
    unit_id: int | None = None,
    without_unit: bool = False,
    sort: str = SORT_BY_POSITIONS,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """Очередь TO_REVIEW-строк со числом ссылающихся позиций и примерами (§7.2).

    Args:
        q: подстрока названия каталожной строки.
        unit_id: фильтр по единице измерения.
        without_unit: только строки без единицы (`unit_id IS NULL`). Отдельный
            флаг, а не «магическое» значение `unit_id`: 0 — валидный id.
        sort: `positions` (по числу ссылок, убывая) либо `title`.
        page, page_size: серверная пагинация — на реальной смете очередь
            начинается с ~1100 строк (§6.5).

    Raises:
        DomainError 422: неизвестная сортировка.
    """
    if sort not in ALLOWED_SORTS:
        raise DomainError(
            422, f"Неизвестная сортировка «{sort}»; допустимы: {', '.join(ALLOWED_SORTS)}."
        )
    page, page_size = clamp_page(page, page_size)

    positions_count = sa.func.count(PositionItem.id).label("positions_count")
    stmt = (
        sa.select(CatalogPosition, positions_count, UnitOfMeasure.code, UnitOfMeasure.name)
        # INNER JOIN — он же и реализует решение §6.3: строка без ссылок
        # (осиротевшая после replace) в очередь не попадает.
        .join(PositionItem, PositionItem.catalog_position_id == CatalogPosition.id)
        .outerjoin(UnitOfMeasure, UnitOfMeasure.id == CatalogPosition.unit_id)
        .where(CatalogPosition.kind == CatalogKind.TO_REVIEW.value)
        .group_by(CatalogPosition.id, UnitOfMeasure.code, UnitOfMeasure.name)
    )
    if q and q.strip():
        stmt = stmt.where(CatalogPosition.standard_job_title.ilike(f"%{q.strip()}%"))
    if without_unit:
        stmt = stmt.where(CatalogPosition.unit_id.is_(None))
    elif unit_id is not None:
        stmt = stmt.where(CatalogPosition.unit_id == unit_id)

    order = (
        # id — тай-брейк: без него строки с равным числом ссылок могли бы
        # разъехаться между страницами и одна попала бы на две сразу.
        (positions_count.desc(), CatalogPosition.id.asc())
        if sort == SORT_BY_POSITIONS
        else (CatalogPosition.standard_job_title.asc(), CatalogPosition.id.asc())
    )

    total = db.execute(
        sa.select(sa.func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()
    rows = db.execute(
        stmt.order_by(*order).offset((page - 1) * page_size).limit(page_size)
    ).all()

    samples = _sample_titles(db, [row[0].id for row in rows])
    return {
        "items": [
            {
                "id": position.id,
                "standard_job_title": position.standard_job_title,
                "normalized_job_title": position.normalized_job_title,
                "unit_id": position.unit_id,
                "unit_code": unit_code,
                "unit_name": unit_name,
                "position_count": count,
                "sample_titles": samples.get(position.id, []),
                "created_at": iso(position.created_at),
            }
            for position, count, unit_code, unit_name in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def search_merge_targets(
    db: Session, *, q: str, unit_id: int | None = None, limit: int = TARGET_SEARCH_LIMIT
) -> list[dict]:
    """Каталожные POSITION-строки, подходящие как цель слияния (§6.4).

    Args:
        q: то, что напечатал оператор, — человеческое название или его фрагмент.
        unit_id: подсказка «сначала та же единица». НЕ фильтр: слияние в другую
            единицу оператор вправе сделать осознанно (например, каталожная строка
            в м², а смета посчитана в м), и спрятать такую цель значило бы решить
            за него.
        limit: потолок выдачи.

    Returns:
        Список целей; совпадения с начала названия — первыми, затем короткие.
    """
    text = (q or "").strip()
    if not text:
        return []

    pattern = f"%{text}%"
    conditions = [CatalogPosition.standard_job_title.ilike(pattern)]

    # Леммы запроса — против уже лемматизированной колонки каталога. Это и даёт
    # морфологию: «кабеля» находит «кабелей», чего ILIKE по сырому тексту не
    # умеет, а FTS в текущей обвязке не умеет тем более (§6.4).
    normalized = normalize_job_title_with_lemmatization(text)
    if normalized:
        conditions.append(CatalogPosition.normalized_job_title.ilike(f"%{normalized}%"))

    starts_with = sa.case(
        (CatalogPosition.standard_job_title.ilike(f"{text}%"), 0), else_=1
    )
    same_unit = (
        sa.case((CatalogPosition.unit_id == unit_id, 0), else_=1)
        if unit_id is not None
        else sa.literal(0)
    )

    rows = db.execute(
        sa.select(CatalogPosition, UnitOfMeasure.code, UnitOfMeasure.name)
        .outerjoin(UnitOfMeasure, UnitOfMeasure.id == CatalogPosition.unit_id)
        .where(
            CatalogPosition.kind == CatalogKind.POSITION.value,
            sa.or_(*conditions),
        )
        .order_by(
            same_unit,
            starts_with,
            sa.func.length(CatalogPosition.standard_job_title),
            CatalogPosition.id,
        )
        .limit(limit)
    ).all()

    return [
        {
            "id": position.id,
            "standard_job_title": position.standard_job_title,
            "unit_id": position.unit_id,
            "unit_code": unit_code,
            "unit_name": unit_name,
        }
        for position, unit_code, unit_name in rows
    ]


def catalog_position_dict(db: Session, catalog_position_id: int) -> dict:
    """Каталожная строка с единицей — ответ на решение оператора."""
    row = db.execute(
        sa.select(CatalogPosition, UnitOfMeasure.code, UnitOfMeasure.name)
        .outerjoin(UnitOfMeasure, UnitOfMeasure.id == CatalogPosition.unit_id)
        .where(CatalogPosition.id == catalog_position_id)
    ).first()
    if row is None:
        raise DomainError(404, f"Каталожная строка {catalog_position_id} не найдена.")
    position, unit_code, unit_name = row
    return {
        "id": position.id,
        "standard_job_title": position.standard_job_title,
        "normalized_job_title": position.normalized_job_title,
        "kind": position.kind,
        "unit_id": position.unit_id,
        "unit_code": unit_code,
        "unit_name": unit_name,
    }
