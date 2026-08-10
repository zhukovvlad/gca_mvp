"""Роутер аналитики: паспорт объекта и сквозная матрица (AGENTS.md §6, §7.4, §7.5).

Права: чтение — любому аутентифицированному. §3 закрепляет за `admin` управление
пользователями, классами, нормативами и замену смет; аналитика — чтение, а его §3
отдаёт и `member` («member — чтение, загрузка смет, ручной матчинг»).

**Все ответы идут через `responses.decimal_json`** — это не перестраховка, а
требование §3, проверенное фазой 5: FastAPI прогоняет `dict` через
`jsonable_encoder` до рендера ответа и превращает `Decimal` во `float`
(`docs/phase5-crud-review.md` §3.1). Здесь деньги есть в каждом ответе — ставка,
сумма позиции, норматив, сумма договора, — а в матрице ещё и в каждой ячейке.
`deviation_pct` тоже `numeric` и тоже уезжает строкой: §4 требует точный `Decimal`
с округлением «только на слое представления».
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from crud import analytics as crud_analytics
from crud import project_passport as crud_project_passport
from crud.common import DomainError
from database import get_db
from responses import decimal_json

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


def _raise(err: DomainError):
    raise HTTPException(err.status_code, err.detail)


@router.get("/passport/{contract_id}")
def get_passport(contract_id: int, db: Session = Depends(get_db)):
    """Данные паспорта объекта (§7.4): реквизиты, ключевые расценки, отклонения.

    Договор без сметы — **не** 404: карточка заведена, файл ещё не загружен, и
    реквизиты уже есть что печатать. В ответе тогда `estimate: null` и пустой топ.
    404 остаётся за несуществующим договором.
    """
    try:
        return decimal_json(crud_analytics.get_passport(db, contract_id))
    except DomainError as e:
        _raise(e)


@router.get("/matrix")
def get_matrix(
    rate_class_id: int | None = Query(default=None),
    date_from: dt.date | None = Query(default=None),
    date_to: dt.date | None = Query(default=None),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(
        default=crud_analytics.MATRIX_PAGE_SIZE_DEFAULT,
        ge=1,
        le=crud_analytics.MATRIX_PAGE_SIZE_MAX,
    ),
    db: Session = Depends(get_db),
):
    """Сквозная матрица (§6): строки — работы каталога, колонки — договоры.

    Фильтры §6: класс, период, текстовый поиск по названию работы. Фильтра
    «статья/раздел» нет намеренно — §6 исключает его из MVP, потому что у него нет
    формального источника: разделы специфичны для каждой сметы, а строки матрицы
    каталожные.
    """
    return decimal_json(
        crud_analytics.get_matrix(
            db,
            rate_class_id=rate_class_id,
            date_from=date_from,
            date_to=date_to,
            q=q,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/matrix/cell")
def get_matrix_cell(
    contract_id: int = Query(...),
    catalog_position_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Drill-down по ячейке матрицы (§6): позиции, сложившиеся в средневзвешенную ставку."""
    try:
        return decimal_json(
            crud_analytics.get_matrix_cell(
                db, contract_id=contract_id, catalog_position_id=catalog_position_id
            )
        )
    except DomainError as e:
        _raise(e)


@router.get("/project-passport/{contract_id}")
def get_project_passport(contract_id: int, db: Session = Depends(get_db)):
    """Паспорт проекта по статьям классификатора (Ф6 фазы 7).

    Чтение — доступно `member` (§3 AGENTS.md: аналитика есть чтение).
    """
    try:
        return decimal_json(crud_project_passport.get_project_passport(db, contract_id))
    except DomainError as e:
        _raise(e)
