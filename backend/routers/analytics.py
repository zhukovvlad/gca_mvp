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
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from auth import require_admin
from crud import analytics as crud_analytics
from crud import comparison as crud_comparison
from crud import dashboard as crud_dashboard
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


@router.get("/dashboard")
def get_dashboard(db: Session = Depends(get_db)):
    """Основной таб стартового дашборда (спека 2026-08-16 §2.8).

    Чтение — любому аутентифицированному, включая `member`: §3 отдаёт аналитику
    и читателю. Диагностики второго таба живут ОТДЕЛЬНЫМ эндпоинтом под
    `require_admin` — сложи их сюда, и `require_admin` закрыл бы вместе с ними
    весь дашборд, который читателю положен.

    `decimal_json` обязателен: ответ несёт деньги (ИТОГО, сумма каждой карточки
    рейтинга), площади и ₽/м² — всё это `Decimal`, и без него FastAPI отдал бы
    `float` (`responses.py`).
    """
    return decimal_json(crud_dashboard.get_dashboard(db))


@router.get("/dashboard/attention", dependencies=[Depends(require_admin)])
def get_dashboard_attention(db: Session = Depends(get_db)):
    """Таб «На что обратить внимание» (решение 12 макета) — ТОЛЬКО `admin`.

    Первое использование `require_admin` в этом роутере: остальная аналитика —
    чтение, а это диагностики и работа аналитика (§3). Отдельный эндпоинт, а не
    ветка внутри `/dashboard`, ровно затем, чтобы право закрывало диагностики, а
    не весь дашборд (спека §2.8).

    Ответ — ПЯТЬ СЧЁТЧИКОВ, без списков затронутых сущностей: списки нужны были
    бы действиям, а действия отложены (решение пользователя на гейте 3).
    Денег в ответе нет, поэтому `decimal_json` здесь не нужен — но и `Decimal`
    сюда попасть не может, счётчики целые.
    """
    return crud_dashboard.attention_counters(db)


@router.get("/project-passport/{contract_id}")
def get_project_passport(contract_id: int, db: Session = Depends(get_db)):
    """Паспорт проекта по статьям классификатора (Ф6 фазы 7).

    Чтение — доступно `member` (§3 AGENTS.md: аналитика есть чтение).
    """
    try:
        return decimal_json(crud_project_passport.get_project_passport(db, contract_id))
    except DomainError as e:
        _raise(e)


def _parse_comparison_ids(ids: str) -> list[int]:
    """`ids=1,2,4` → `[1, 2, 4]`. Нечисловой элемент — 400 с самим значением
    в сообщении, а не 422-трасса валидатора (спека §2.6, план — задача 5):
    строка приходит из адреса, который человек мог набрать руками.
    """
    try:
        return [int(part.strip()) for part in ids.split(",") if part.strip() != ""]
    except ValueError:
        raise HTTPException(
            400, f"`ids` должен быть списком чисел через запятую, а не {ids!r}."
        ) from None


@router.get("/comparison")
def get_comparison(
    ids: str | None = Query(default=None, description="Список id договоров через запятую: 1,2,4"),
    all_: bool = Query(default=False, alias="all", description="Сравнить всё по текущему фильтру"),
    q: str | None = Query(default=None),
    object_id: int | None = Query(default=None),
    contractor_id: int | None = Query(default=None),
    rate_class_id: int | None = Query(default=None),
    vat_mode: str = Query(default=crud_comparison.VAT_MODE_OWN),
    single_rate: Decimal | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Сравнение договоров по статьям классификатора (спека 2026-08-17 §2.1-§2.6).

    Чтение — доступно `member` (§2.9 спеки, §3 AGENTS.md: аналитика есть чтение),
    поэтому `require_admin` здесь нет.

    Выборка — ровно ДВЕ формы (§2.6), обе разрешает `crud.comparison.
    resolve_selection`: `ids=1,2,4` — явный список, либо `all=1` вместе с теми
    же четырьмя фильтрами списка договоров (`q`, `object_id`, `contractor_id`,
    `rate_class_id` — контракт `routers.contracts.list_contracts`, DoD 22).
    Обе формы разом и ни одной — обе ошибки 400, неизвестный `id` — 404;
    правило и его причина живут в докстроке `resolve_selection`, здесь не
    дублируются.

    **`page`/`page_size` НЕ принимаются.** Это решение, а не недосмотр:
    сравнение берёт выборку целиком, а не страницу списка (§2.6) — FastAPI
    просто не видит этих параметров в адресе и ничего не режет.

    `vat_mode` по умолчанию — «своя ставка» (§2.3); `single_rate` действует
    только в режиме «единая» и без него подставляется предвыбор
    (`build_comparison`, DoD 8ж).
    """
    parsed_ids = _parse_comparison_ids(ids) if ids is not None else None
    try:
        contract_ids = crud_comparison.resolve_selection(
            db,
            ids=parsed_ids,
            use_filter=all_,
            q=q,
            object_id=object_id,
            contractor_id=contractor_id,
            rate_class_id=rate_class_id,
        )
        return decimal_json(
            crud_comparison.build_comparison(
                db, contract_ids, vat_mode=vat_mode, single_rate=single_rate
            )
        )
    except DomainError as e:
        _raise(e)
