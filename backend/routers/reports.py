"""Роутер выгрузок §7.6: свод по договору и отчёт «для банка».

Два эндпоинта, два файла (решение §6.6): у отчётов разный охват и разные параметры —
свод берёт один договор, «для банка» — выборку. Один файл на два листа заставил бы
отчёт «для банка» отвечать на вопрос «какой договор?», которого у него нет.

Права: чтение — любому аутентифицированному. Выгрузка — это чтение, а §3 отдаёт
чтение и `member`; защита цифр перед банком (§1 пункт 4) не является admin-операцией.

**Файл отдаётся `bytes`, а не `StreamingResponse` с файловым объектом** — грабли
фазы 4: Starlette итерирует такой объект построчно и не закрывает хендл. Здесь xlsx
целиком в памяти, и правильный ответ — вернуть содержимое (§7 брифинга фазы 6).

Имя файла уезжает в `filename*=UTF-8''…` (percent-кодирование): в нём русские буквы
и номер договора, а `filename=` в ASCII их не переносит — браузер получил бы
искажённое имя.
"""
from __future__ import annotations

import datetime as dt
import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from crud import reports as crud_reports
from crud.common import DomainError
from database import get_db
from services.excel_reports import build_bank_comparison, build_contract_summary

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])

log = logging.getLogger(__name__)

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _raise(err: DomainError):
    raise HTTPException(err.status_code, err.detail)


def _xlsx(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


def _safe_filename_part(value: str) -> str:
    """Убирает из части имени файла то, что ломает путь.

    Номер договора приходит из карточки и может содержать `/` или `\\` (нумерация
    вида «12/2025» встречается), а такой символ в имени файла Windows и часть
    браузеров трактуют как разделитель пути.
    """
    for bad in '/\\:*?"<>|':
        value = value.replace(bad, "-")
    return value.strip() or "договор"


@router.get("/contract-summary")
def contract_summary(
    contract_id: int = Query(..., description="Договор, по которому строится свод"),
    db: Session = Depends(get_db),
):
    """Свод расценок по договору с отклонениями (§7.6, отчёт «а»)."""
    try:
        data = crud_reports.contract_summary(db, contract_id)
    except DomainError as e:
        _raise(e)
    content = build_contract_summary(data, generated_at=dt.date.today())
    number = _safe_filename_part(data["header"]["contract_number"])
    log.info("report_contract_summary contract=%s rows=%s", contract_id, len(data["rows"]))
    return _xlsx(content, f"Свод расценок {number}.xlsx")


@router.get("/bank-comparison")
def bank_comparison(
    date_from: dt.date | None = Query(default=None),
    date_to: dt.date | None = Query(default=None),
    rate_class_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Сравнение с нормативами «для банка» (§7.6, отчёт «б»).

    Фильтры те же, что у матрицы, и специально: экран и файл обязаны показывать одно
    и то же, иначе расхождение придётся объяснять банку.
    """
    data = crud_reports.bank_comparison(
        db, date_from=date_from, date_to=date_to, rate_class_id=rate_class_id
    )
    content = build_bank_comparison(data, generated_at=dt.date.today())
    log.info(
        "report_bank_comparison classes=%s works=%s",
        len(data["sections"]),
        data["totals"]["works"],
    )
    return _xlsx(content, "Сравнение с нормативами.xlsx")
