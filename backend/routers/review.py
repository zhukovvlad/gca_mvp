"""Роутер ручного матчинга (AGENTS.md §5 «Ручной матчинг», §7.2).

HTTP-слой над готовым `services/review.py` — сервис фазой 5 не переписывается.
Здесь: выборки для экрана, валидация, права и **управление транзакцией** —
`merge_into_position` и `set_kind` требуют, чтобы её вёл вызывающий.

**Права: `member` тоже вправе** — §3 относит ручной матчинг к его правам прямо.
Поэтому `require_admin` здесь нет, только аутентификация (навешена в main.py).

**Трансляция `ReviewError`.** Сервис не различает «строки нет» и «строка уже
разобрана» — обе ситуации у него один `ReviewError`. Роутер поэтому проверяет
существование сам и отдаёт 404 на заведомо отсутствующую строку, а `ReviewError`
переводит в **409**: если строка была, а операция не применилась, значит её
состояние изменилось — как правило, её разобрал другой оператор, пока этот ждал
блокировку. Недопустимый `kind` и слияние с собой отсекаются до сервиса и дают 422.
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from crud import review as crud_review
from crud.common import DomainError
from database import get_db
from models import CatalogPosition
from services.review import ReviewError, merge_into_position, set_kind
from services.unit_resolution import UnitResolver

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/review", tags=["review"])

#: Потолок пакета. Оператор размечает мусор сотнями, но пакет держит транзакцию
#: открытой и лочит строки `FOR UPDATE` — неограниченный размер превратил бы
#: одно нажатие в блокировку всего каталога.
MAX_BATCH_SIZE = 200

#: Что оператор вправе поставить (§5). `LOT_HEADER` сюда НЕ входит — это разметка
#: структуры файла, а не решение о работе.
#:
#: Литерал дублирует `services.review.MANUAL_KINDS` не по небрежности: Pydantic
#: нужен статический тип, чтобы отдать 422 на чужой `kind` до вызова сервиса.
#: Расхождение двух списков ловит тест
#: `test_review_api.py::test_manual_kind_literal_matches_service`.
ManualKind = Literal["POSITION", "HEADER", "TRASH"]


class MergeRequest(BaseModel):
    target_id: int


class KindRequest(BaseModel):
    kind: ManualKind


class BatchKindRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=MAX_BATCH_SIZE)
    kind: ManualKind


def _raise(err: DomainError):
    raise HTTPException(err.status_code, err.detail)


def _require_exists(db: Session, catalog_position_id: int) -> CatalogPosition:
    """404 на заведомо отсутствующую строку — чтобы её не путать с 409-гонкой."""
    row = db.get(CatalogPosition, catalog_position_id)
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Каталожная строка {catalog_position_id} не найдена.",
        )
    return row


@router.get("/queue")
def review_queue(
    q: str | None = Query(default=None),
    unit_id: int | None = Query(default=None),
    without_unit: bool = Query(default=False),
    sort: str = Query(default=crud_review.SORT_BY_POSITIONS),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Очередь ручного матчинга: TO_REVIEW-строки со ссылками и примерами (§7.2).

    Строки, на которые не ссылается ни одна позиция, не показываются (§6.3).
    """
    try:
        return crud_review.list_review_queue(
            db,
            q=q,
            unit_id=unit_id,
            without_unit=without_unit,
            sort=sort,
            page=page,
            page_size=page_size,
        )
    except DomainError as e:
        _raise(e)


@router.get("/targets")
def merge_targets(
    q: str = Query(min_length=1),
    unit_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Поиск каталожной POSITION-строки как цели слияния (ILIKE, решение §6.4)."""
    return crud_review.search_merge_targets(db, q=q, unit_id=unit_id)


@router.post("/{to_review_id}/merge")
def merge(
    to_review_id: int,
    body: MergeRequest,
    db: Session = Depends(get_db),
):
    """Слить TO_REVIEW-строку с существующей POSITION (§5).

    Решение применяется ко ВСЕМ позициям, ссылающимся на эту строку; сама строка
    после переноса ссылок удаляется, освобождая свою нормализованную пару.
    """
    if to_review_id == body.target_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Нельзя слить строку с собой."
        )
    _require_exists(db, to_review_id)
    _require_exists(db, body.target_id)

    try:
        moved = merge_into_position(db, to_review_id=to_review_id, target_id=body.target_id)
        db.commit()
    except ReviewError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except Exception:
        db.rollback()
        raise

    log.info("Review API: строка %d слита с %d, позиций %d", to_review_id, body.target_id, moved)
    return {
        "to_review_id": to_review_id,
        "target": crud_review.catalog_position_dict(db, body.target_id),
        "moved_positions": moved,
    }


@router.post("/{to_review_id}/kind")
def set_review_kind(
    to_review_id: int,
    body: KindRequest,
    db: Session = Depends(get_db),
):
    """Утвердить строку как POSITION либо пометить HEADER/TRASH (§5).

    Строка не удаляется: её нормализованная пара остаётся за ней, и следующий
    импорт той же работы найдёт уже размеченную строку.
    """
    _require_exists(db, to_review_id)
    try:
        set_kind(db, to_review_id=to_review_id, kind=body.kind)
        db.commit()
    except ReviewError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    return crud_review.catalog_position_dict(db, to_review_id)


@router.post("/batch-kind")
def batch_set_kind(
    body: BatchKindRequest,
    db: Session = Depends(get_db),
):
    """Разметить пакет строк одним `kind` (решение §6.5).

    Первичный разбор очереди — это в основном отсев не-работ, а построчно на
    реальной смете это ~1100 запросов. Пакет исполняется **одной транзакцией с
    общим `UnitResolver`**: именно для этого у сервиса есть параметр `resolver` —
    иначе справочник единиц перечитывался бы на каждое решение.

    Строка, которую применить нельзя (её уже разобрал другой оператор), пакет не
    роняет: она попадает в `skipped` с человекочитаемой причиной. Это безопасно —
    `set_kind` проверяет `kind` после захвата `FOR UPDATE` и **до** любой
    мутации, поэтому отказ по одной строке не оставляет за собой половины
    изменения.

    Идентификаторы обрабатываются **по возрастанию**: одинаковый порядок захвата
    блокировок у двух параллельных пакетов исключает взаимную блокировку на
    пересекающихся строках — тем же приёмом, что `_lock_rows` внутри сервиса.
    """
    resolver = UnitResolver(db)
    applied: list[int] = []
    skipped: list[dict] = []

    try:
        for catalog_id in sorted(set(body.ids)):
            if db.get(CatalogPosition, catalog_id) is None:
                skipped.append(
                    {"id": catalog_id, "reason": f"Каталожная строка {catalog_id} не найдена."}
                )
                continue
            try:
                set_kind(db, to_review_id=catalog_id, kind=body.kind, resolver=resolver)
            except ReviewError as exc:
                skipped.append({"id": catalog_id, "reason": str(exc)})
                continue
            applied.append(catalog_id)
        db.commit()
    except Exception:
        db.rollback()
        raise

    log.info(
        "Review API: пакет kind=%s применён к %d строкам, пропущено %d",
        body.kind,
        len(applied),
        len(skipped),
    )
    return {"kind": body.kind, "applied": applied, "skipped": skipped}
