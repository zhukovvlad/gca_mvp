"""Поиск по каталогу работ — общий для Review и нормативов (AGENTS.md §4).

Обе задачи ищут одно и то же: каталожную строку `kind='POSITION'` по
человеческому названию. В Review это цель слияния (§5), на экране нормативов —
работа, которой назначают ставку (§7.3). Выборка одна, поэтому и код один: иначе
два экрана искали бы по-разному и оператор видел бы разные списки на один запрос.

**Поиск — ILIKE, решение фазы 5 §6.4.** `catalog_positions.fts_vector` это
`to_tsvector('simple', standard_job_title)`, то есть вектор **словоформ**, тогда
как `prepare_for_fts_query` отдаёт **леммы** — такой запрос не находит ничего
(замер в `docs/phase5-crud-review.md` §1.4). Ищем по двум колонкам:

* `standard_job_title` — по тому, что оператор видит на экране;
* `normalized_job_title` — леммами его запроса, чтобы «кирпичной» находило
  «кирпичная». Колонка каталога уже лемматизирована той же функцией.

Цена — seq scan по `%q%`. Она осознанна: каталог MVP это единицы тысяч строк
(реальная смета даёт ~1100 уникальных пар). Правильный FTS потребовал бы
лемматизированного tsvector-столбца — записано долгом.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.common import DomainError
from models import CatalogKind, CatalogPosition, UnitOfMeasure
from parser.sanitize_text import normalize_job_title_with_lemmatization

#: Потолок выдачи поиска: оператор выбирает глазами, а не листает.
SEARCH_LIMIT = 50


def _position_row_dict(position: CatalogPosition, unit_code, unit_name) -> dict:
    return {
        "id": position.id,
        "standard_job_title": position.standard_job_title,
        "unit_id": position.unit_id,
        "unit_code": unit_code,
        "unit_name": unit_name,
    }


def search_positions(
    db: Session,
    *,
    q: str,
    unit_id: int | None = None,
    limit: int = SEARCH_LIMIT,
) -> list[dict]:
    """Каталожные строки `kind='POSITION'` по фрагменту человеческого названия.

    Args:
        q: то, что напечатал оператор.
        unit_id: подсказка «сначала та же единица». **Не фильтр:** работа в другой
            единице — законная цель осознанного решения (каталожная строка в м², а
            смета посчитана в м), и спрятать её значило бы решить за оператора.
        limit: потолок выдачи.

    Returns:
        Совпадения с начала названия — первыми, затем более короткие.
    """
    text = (q or "").strip()
    if not text:
        return []

    pattern = f"%{text}%"
    conditions = [CatalogPosition.standard_job_title.ilike(pattern)]
    normalized = normalize_job_title_with_lemmatization(text)
    if normalized:
        conditions.append(CatalogPosition.normalized_job_title.ilike(f"%{normalized}%"))

    starts_with = sa.case((CatalogPosition.standard_job_title.ilike(f"{text}%"), 0), else_=1)
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
    return [_position_row_dict(*row) for row in rows]


def position_dict(db: Session, catalog_position_id: int) -> dict:
    """Каталожная строка с единицей и `kind` — ответ на решение оператора."""
    row = db.execute(
        sa.select(CatalogPosition, UnitOfMeasure.code, UnitOfMeasure.name)
        .outerjoin(UnitOfMeasure, UnitOfMeasure.id == CatalogPosition.unit_id)
        .where(CatalogPosition.id == catalog_position_id)
    ).first()
    if row is None:
        raise DomainError(404, f"Каталожная строка {catalog_position_id} не найдена.")
    position, unit_code, unit_name = row
    body = _position_row_dict(position, unit_code, unit_name)
    body["normalized_job_title"] = position.normalized_job_title
    body["kind"] = position.kind
    return body
