"""Роутер рядов индексов инфляции (спека
`2026-08-18-inflation-adjustment-design.md` §2.10, §2.12; AGENTS.md §7 п. 3).

Права: **читать ряды и применять приведение вправе `member`**, писать — только
`admin`. Это не решение роутера, а буква §3: значение, введённое одним человеком,
немедленно меняет числа у всех, включая защиту перед банком, и §3 закрепляет за
`admin` всё, что меняет расчёт для всех. Читать `member` обязан — без этого он не
увидел бы даже названия ряда, которым приведены показанные ему числа.

`DELETE` не заводится нигде (§2.10): ошибочное значение исправляется правкой,
ненужный ряд архивируется через `is_active`.

**Маршрутов ровно четыре, и их таблица в §2.12 исчерпывающая.** `GET /{id}`
отдельным маршрутом не заводится: списку он не нужен, а полоса уровней на
`/compare` получает примечание и дату правки ряда ИЗ ОТВЕТА СРАВНЕНИЯ — именно
потому, что по прямой ссылке ряд может оказаться архивным, и второй запрос к
списку выбора его бы не нашёл (§2.12). Заводить маршрут «на будущее» значило бы
расширять согласованный контракт молча.

**В схемах живёт только ФОРМА** — типы, обязательность полей, запрет `float`.
`gt=0` и `min_length` здесь запрещены явно (Global Constraints плана): доменные
правила «коэффициент больше нуля» и «источник непуст после `btrim`» проверяет
`crud.inflation_series`, и увести их в схему значило бы обессмыслить тест
атомарности — отказ обязан случиться ВНУТРИ транзакции, после того как первый год
уже применён.

`coefficient` уезжает в JSON строкой: все ответы идут через `decimal_json`, а
`float` на входе отклоняется валидатором с УНИКАЛЬНЫМ именем метода — `pydantic`
ключует `field_validator`-ы по имени, и одноимённые молча схлопываются в один
(§11 `AGENTS.md`, замерено в Ф5 фазы 7).
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from auth import require_admin
from crud import inflation_series as crud_series
from crud.common import DomainError
from database import get_db
from models import User
from responses import decimal_json
from routers.domain_errors import raise_domain_error

router = APIRouter(prefix="/api/v1/inflation-series", tags=["inflation-series"])

#: Поля правки ряда, для которых `null` бессмыслен: колонки `NOT NULL`. Тот же
#: приём, что у `_NON_NULLABLE` в `routers/rate_standards.py`.
_NON_NULLABLE = ("name", "is_active")


class InflationYearInput(BaseModel):
    """Год ряда. ВСЕ ТРИ поля обязательны — это описание года целиком (§2.12).

    Обязательность — форма, а не доменное правило: пустой `source` из пробелов
    схему прошёл бы (`min_length=1` не ловит пробел), и отвергает его домен.
    """

    year: int
    coefficient: Decimal
    source: str
    is_forecast: bool

    @field_validator("coefficient", mode="before")
    @classmethod
    def _reject_float_coefficient(cls, value):
        """Имя метода уникально по всему дереву схемы — иначе валидатор молча
        схлопнулся бы с одноимённым и защита читалась бы как живая, будучи
        мёртвой (§11, замер Ф5 фазы 7)."""
        if isinstance(value, float):
            raise ValueError(
                "Передавайте коэффициент строкой (например \"1.083\"), а не числом с "
                "плавающей точкой: float теряет точность."
            )
        return value


class InflationSeriesCreate(BaseModel):
    name: str
    note: str | None = None
    values: list[InflationYearInput] = Field(default_factory=list)


class InflationSeriesPatch(BaseModel):
    """Правка ряда ЦЕЛИКОМ одним запросом: поля и годы вместе (§2.12).

    Годы, не перечисленные в `values`, остаются как были — это `PATCH`, а `DELETE`
    запрещён (§2.10). Отсутствие поля отличается от его `null` через
    `exclude_unset`, поэтому «не присылал» и «прислал пустоту» не путаются.
    """

    name: str | None = None
    note: str | None = None
    is_active: bool | None = None
    values: list[InflationYearInput] | None = None


def _values_payload(values: list[InflationYearInput] | None) -> list[dict] | None:
    """Годы в словари для CRUD. `None` остаётся `None`: для правила архивного ряда
    «поле не передано» и «передан пустой список» — разные тела (§2.12)."""
    if values is None:
        return None
    return [item.model_dump() for item in values]


@router.get("")
def list_inflation_series(
    include_archived: bool = Query(
        default=False, description="Показать и архивные ряды (по умолчанию нет)"
    ),
    db: Session = Depends(get_db),
):
    """Список рядов. Архивные по умолчанию не предлагаются для выбора (§2.10)."""
    return decimal_json(crud_series.list_series(db, include_archived=include_archived))


@router.get("/{series_id}/values")
def list_inflation_series_values(series_id: int, db: Session = Depends(get_db)):
    """Годы ряда. **Архивный читается** — старая ссылка обязана работать (§2.10)."""
    try:
        return decimal_json(crud_series.list_values(db, series_id))
    except DomainError as e:
        raise_domain_error(e)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_inflation_series(
    body: InflationSeriesCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Создать ряд сразу с годами, одной транзакцией.

    Это первый и обязательный пользовательский путь: миграция создаёт справочник
    ПУСТЫМ (§2.6), и без создания через интерфейс фича не заводится вовсе.
    """
    try:
        created = crud_series.create_series(
            db, name=body.name, note=body.note, values=_values_payload(body.values)
        )
    except DomainError as e:
        raise_domain_error(e)
    return decimal_json(created, status.HTTP_201_CREATED)


@router.patch("/{series_id}")
def update_inflation_series(
    series_id: int,
    body: InflationSeriesPatch,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Изменить ряд: название, примечание, `is_active` и годы — ОДНОЙ транзакцией.

    Архивный ряд заморожен: `{"is_active": true}` в одиночку возвращает его в
    активные, любое другое тело даёт `409` (§2.10). Сначала вернуть, потом
    править — два шага, каждый со своим смыслом.
    """
    fields = body.model_dump(exclude_unset=True)
    for name in _NON_NULLABLE:
        if name in fields and fields[name] is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"Поле {name} не может быть null."
            )
    if "values" in fields:
        fields["values"] = _values_payload(body.values)
    try:
        updated = crud_series.update_series(db, series_id, **fields)
    except DomainError as e:
        raise_domain_error(e)
    return decimal_json(updated)
