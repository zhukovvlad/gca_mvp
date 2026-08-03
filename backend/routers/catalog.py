"""Роутер каталога работ: поиск POSITION-строк (AGENTS.md §4).

Нужен формам, которым надо выбрать работу: назначение норматива (§7.3) и — тем
же кодом, но под своим именем — выбор цели слияния в Review (§7.2). Отдельный
путь, а не переиспользование `/api/v1/review/targets`: экран нормативов к Review
отношения не имеет, и зависеть от его URL он не должен.

Чтение — любому аутентифицированному. Изменять каталог напрямую нельзя: строки
рождаются матчингом (§5.4.3), а размечаются решениями оператора в Review.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from crud import catalog as crud_catalog
from crud.common import DomainError
from database import get_db

router = APIRouter(prefix="/api/v1/catalog-positions", tags=["catalog"])


@router.get("")
def search_catalog_positions(
    q: str = Query(min_length=1),
    unit_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Поиск работ каталога по фрагменту названия (ILIKE, решение §6.4)."""
    return crud_catalog.search_positions(db, q=q, unit_id=unit_id)


@router.get("/{catalog_position_id}")
def get_catalog_position(catalog_position_id: int, db: Session = Depends(get_db)):
    try:
        return crud_catalog.position_dict(db, catalog_position_id)
    except DomainError as e:
        raise HTTPException(e.status_code, e.detail) from e
