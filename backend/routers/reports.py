"""Роутер выгрузок §7.6: свод по договору, отчёт «для банка» и сравнение договоров.

Было два эндпоинта, два файла (решение §6.6): у отчётов разный охват и разные
параметры — свод берёт один договор, «для банка» — выборку по периоду и классу.
Один файл на два листа заставил бы отчёт «для банка» отвечать на вопрос «какой
договор?», которого у него нет.

**С этой фичей — три (спека §2.10 п.3, `AGENTS.md` v6.8: «два отчёта — два
файла» пересмотрено на «три»).** Довод не меняется, он расширяется: у выгрузки
сравнения СВОЯ выборка (набор договоров, §2.6 — `ids` либо `all=1` с фильтром)
и свой разрез (статьи × договоры, а не «класс → работа», §2.8). Она не может
переиспользовать ни выборку свода (один договор), ни выборку «для банка»
(период + класс) — значит это третий маршрут, а не третий параметр на старом.

Права: чтение — любому аутентифицированному. Выгрузка — это чтение, а §3 отдаёт
чтение и `member`; защита цифр перед банком (§1 пункт 4) не является admin-операцией.
Сравнение — та же логика (спека §2.9): читают `admin` и `member`.

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
from decimal import Decimal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from crud import comparison as crud_comparison
from crud import reports as crud_reports
from crud.common import DomainError
from database import get_db
from routers.domain_errors import raise_domain_error
from services.excel_comparison import build_comparison_sheet
from services.excel_reports import build_bank_comparison, build_contract_summary

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])

log = logging.getLogger(__name__)

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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
        raise_domain_error(e)
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


#: Сколько номеров договоров помещается в имя файла до того, как оно станет
#: непригодным. Предела на число колонок у сравнения НЕТ намеренно (спека §3
#: отвергает «жёсткий предел 5-6 колонок»), поэтому склейка всех номеров рано
#: или поздно упирается не в наш вкус, а в предел длины пути: пятьдесят
#: договоров дают имя в несколько сотен символов, и файл просто не сохранится.
#: Дальше порога имя называет ЧИСЛО договоров — это правда о выборке, тогда как
#: обрезанный по середине список выглядел бы как полный.
_MAX_NUMBERS_IN_FILENAME = 3


def _comparison_filename(columns: list[dict]) -> str:
    if not columns:
        # Пустая выборка — законный ответ (`resolve_selection`), и имя обязано
        # это признавать. `_safe_filename_part` отдал бы здесь своё умолчание
        # «договор», то есть «Сравнение договоров договор.xlsx».
        return "Сравнение договоров.xlsx"
    if len(columns) > _MAX_NUMBERS_IN_FILENAME:
        return f"Сравнение договоров ({len(columns)}).xlsx"
    numbers = _safe_filename_part("-".join(column["contract_number"] for column in columns))
    return f"Сравнение договоров {numbers}.xlsx"


@router.get("/comparison")
def comparison_report(
    ids: str | None = Query(default=None, description="Список id договоров через запятую"),
    all_: bool = Query(default=False, alias="all", description="Выборка по фильтру, не по ids"),
    q: str | None = Query(default=None),
    object_id: int | None = Query(default=None),
    contractor_id: int | None = Query(default=None),
    rate_class_id: int | None = Query(default=None),
    vat_mode: str = Query(default=crud_comparison.VAT_MODE_OWN),
    single_rate: Decimal | None = Query(default=None),
    inflation_series_id: int | None = Query(
        default=None, description="Ряд индексов инфляции; без него приведения нет"
    ),
    target_month: str | None = Query(
        default=None,
        description="Целевой ценовой уровень, YYYY-MM; по умолчанию текущий месяц",
    ),
    db: Session = Depends(get_db),
):
    """Выгрузка сравнения договоров в Excel (§7.6, отчёт «в»; спека §2.7, §2.8).

    Выборка — ровно тем же контрактом, что у экрана сравнения (§2.6): `ids=1,2,4`
    явным списком либо `all=1` с фильтром (`q`, `object_id`, `contractor_id`,
    `rate_class_id`). Обе формы, включая их взаимоисключение и коды ошибок,
    разрешает `crud.comparison.resolve_selection` — здесь её правила НЕ
    повторяются: вторая копия рисковала бы разойтись с экраном в том, что
    считается выборкой (спека §2.7, «один агрегат — два представления»).

    `vat_mode`/`single_rate` — тот же режим показа НДС, что и на экране (§2.3);
    лист печатает подпись состава, поэтому неверный режим — отказ 400 из
    `build_comparison`, а не тихая подмена на умолчание.

    **Приведение — те же два параметра, что у экрана**, и отказ у них тот же
    структурированный `422` (§2.9). При отказе лист НЕ собирается, поэтому файла с
    ошибкой не существует вовсе: ранняя редакция спеки обещала «тот же отказ и ту
    же формулировку на листе Excel» — обещание невыполнимое, и здесь его нет.
    Проверяется отсутствием вложения, а не содержимым листа.
    """
    try:
        selection = crud_comparison.resolve_selection(
            db, ids=crud_comparison.parse_ids_param(ids), use_filter=all_, q=q,
            object_id=object_id, contractor_id=contractor_id, rate_class_id=rate_class_id,
        )
        data = crud_comparison.build_comparison(
            db, selection.contract_ids, facet_ids=selection.facet_ids,
            vat_mode=vat_mode, single_rate=single_rate,
            inflation_series_id=inflation_series_id,
            target_month=crud_comparison.parse_target_month_param(target_month),
        )
    except DomainError as e:
        raise_domain_error(e)

    content = build_comparison_sheet(data, generated_at=dt.date.today())
    log.info(
        "report_comparison contracts=%s rows=%s",
        len(selection.contract_ids), len(data["rows"]),
    )
    return _xlsx(content, _comparison_filename(data["columns"]))
