"""Справочник рядов индексов инфляции (спека
`2026-08-18-inflation-adjustment-design.md` §2.6, §2.10, §2.12).

**Запись — ОДНА операция на всё окно правки, в одной транзакции.** Окно правит
название, примечание и несколько годов одной кнопкой; отдельный маршрут на каждый
год слал бы четыре запроса, и отказ на середине оставил бы ряд исправленным
наполовину. Ряд общий и без версий, поэтому «наполовину» здесь означает не
испорченную форму, а **неверные числа у всех, кто в этот момент смотрит
сравнение**. Довод тот же, которым §5 `AGENTS.md` требует атомарности сессии B
при импорте.

**`PATCH`, а не `PUT`** — потому что `DELETE` запрещён (§2.10): годы, не
перечисленные в теле, остаются как были. `PUT` по смыслу удалил бы их, и контракт
противоречил бы запрету удаления.

**Доменные проверки года живут ЗДЕСЬ, а не в схеме запроса.** `coefficient > 0` и
«источник непуст после `btrim`» проверяет домен — как это делает
`create_rate_standard`. Причина не стилистическая: отказ обязан случиться внутри
транзакции, ПОСЛЕ того как первый год уже применён, иначе тест атомарности стал бы
вакуозным — снятие транзакции осталось бы зелёным, потому что схема отвергла бы
тело раньше.

**Идемпотентность доведена до наблюдаемого состояния, иначе она мнимая.**
`updated_at` входит в аудит и печатается на листе выгрузки, поэтому год, чья
тройка совпала с сохранённой, — no-op: его метка не двигается. И метка самого ряда
двигается тогда и только тогда, когда фактически изменилось хоть что-то, — полоса
уровней показывает эту дату, и сдвиг без изменений соврал бы читателю, что ряд
правили.

**Удаления нет вовсе** (§2.10): ошибочное значение исправляется правкой, ненужный
ряд архивируется через `is_active` и остаётся читаемым по старой ссылке.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.common import (
    DomainError,
    iso,
    require_text,
    rollback_on_domain_error,
    translating_integrity,
)
from models import InflationIndexValue, InflationSeries

log = logging.getLogger(__name__)

UNSET = object()

_NAME_TAKEN = {"uq_inflation_series_name": "Ряд индексов с таким названием уже есть."}

#: Три поля, которыми задаётся год. Год — это его описание ЦЕЛИКОМ, а не подкрутка
#: одного значения: иначе `updated_at` двигался бы правкой, которая описывает год
#: не полностью, и лист утверждал бы, что коэффициент исправлен (§2.12).
_YEAR_FIELDS = ("coefficient", "source", "is_forecast")


# ---------------------------------------------------------------------------
#  Чтение
# ---------------------------------------------------------------------------

def _series_dict(series: InflationSeries, *, year_from, year_to, value_count: int) -> dict:
    return {
        "id": series.id,
        "name": series.name,
        "note": series.note,
        "is_active": series.is_active,
        # Охват годов — то, что список рядов показывает вместо самих значений
        # (§2.12): «2024–2026» отвечает на вопрос «применим ли ряд к моей
        # выборке» без второго запроса.
        "year_from": year_from,
        "year_to": year_to,
        "value_count": value_count,
        "created_at": iso(series.created_at),
        "updated_at": iso(series.updated_at),
    }


def _coverage(db: Session, series_ids: list[int]) -> dict[int, tuple]:
    if not series_ids:
        return {}
    rows = db.execute(
        sa.select(
            InflationIndexValue.series_id,
            sa.func.min(InflationIndexValue.year),
            sa.func.max(InflationIndexValue.year),
            sa.func.count(),
        )
        .where(InflationIndexValue.series_id.in_(series_ids))
        .group_by(InflationIndexValue.series_id)
    ).all()
    return {row[0]: (row[1], row[2], row[3]) for row in rows}


def list_series(db: Session, *, include_archived: bool = False) -> list[dict]:
    """Ряды по названию. Архивные по умолчанию НЕ предлагаются (§2.10)."""
    stmt = sa.select(InflationSeries).order_by(InflationSeries.name)
    if not include_archived:
        stmt = stmt.where(InflationSeries.is_active.is_(True))
    rows = db.execute(stmt).scalars().all()
    coverage = _coverage(db, [series.id for series in rows])
    return [
        _series_dict(
            series,
            year_from=coverage.get(series.id, (None, None, 0))[0],
            year_to=coverage.get(series.id, (None, None, 0))[1],
            value_count=coverage.get(series.id, (None, None, 0))[2],
        )
        for series in rows
    ]


def get_series(db: Session, series_id: int) -> InflationSeries:
    series = db.get(InflationSeries, series_id)
    if series is None:
        raise DomainError(404, f"Ряд индексов {series_id} не найден.")
    return series


def get_series_dict(db: Session, series_id: int) -> dict:
    """Ряд по id. **Архивный читается** — старая ссылка обязана работать (§2.10)."""
    series = get_series(db, series_id)
    year_from, year_to, count = _coverage(db, [series_id]).get(series_id, (None, None, 0))
    return _series_dict(series, year_from=year_from, year_to=year_to, value_count=count)


def _value_dict(value: InflationIndexValue) -> dict:
    return {
        "year": value.year,
        "coefficient": value.coefficient,
        "source": value.source,
        "is_forecast": value.is_forecast,
        "created_at": iso(value.created_at),
        "updated_at": iso(value.updated_at),
    }


def list_values(db: Session, series_id: int) -> list[dict]:
    """Годы ряда по возрастанию. Архивный ряд ЧИТАЕТСЯ (§2.10)."""
    get_series(db, series_id)
    rows = db.execute(
        sa.select(InflationIndexValue)
        .where(InflationIndexValue.series_id == series_id)
        .order_by(InflationIndexValue.year)
    ).scalars().all()
    return [_value_dict(value) for value in rows]


# ---------------------------------------------------------------------------
#  Проверки и применение
# ---------------------------------------------------------------------------

def _reject_duplicate_years(values: list[dict]) -> None:
    """Повтор года в теле — отказ ДО ЛЮБОЙ ЗАПИСИ (§2.12, DoD 29).

    Проверка до записи, а не опора на уникальный индекс: индекс защищает данные,
    но не объясняет пользователю, что он прислал, а поведение иначе зависело бы от
    порядка — победил бы первый или последний.
    """
    seen: set[int] = set()
    duplicates: set[int] = set()
    for item in values:
        year = item.get("year")
        if year in seen:
            duplicates.add(year)
        seen.add(year)
    if duplicates:
        years = sorted(duplicates)
        listed = ", ".join(str(year) for year in years)
        raise DomainError(
            422,
            f"Год встречается в запросе дважды: {listed}.",
            code="duplicate_year",
            context={"years": years},
        )


def _year_triple(item: dict) -> tuple[int, Decimal, str, bool]:
    """Разобрать год: он задаётся ВСЕМИ ТРЕМЯ полями (DoD 27).

    Обязательность полей проверяет и схема запроса — там это форма. Здесь она
    проверяется снова не ради дублирования правила, а чтобы CRUD был безопасен
    сам по себе: без этого неполный словарь давал бы `KeyError`, то есть `500`
    вместо внятного отказа.
    """
    year = item.get("year")
    if year is None:
        raise DomainError(422, "У года ряда не указан сам год.")
    missing = [field for field in _YEAR_FIELDS if item.get(field) is None]
    if missing:
        listed = ", ".join(missing)
        raise DomainError(
            422,
            f"Год {year} задан не полностью: не хватает полей {listed}. "
            "Год описывается коэффициентом, источником и признаком прогноза сразу.",
        )
    coefficient = item["coefficient"]
    if coefficient <= 0:
        raise DomainError(
            422, f"Коэффициент за {year} год должен быть больше нуля, получено {coefficient}."
        )
    source = require_text(item["source"], f"Источник коэффициента за {year} год")
    return year, coefficient, source, bool(item["is_forecast"])


def _apply_year(db: Session, series: InflationSeries, item: dict) -> bool:
    """Применить один год. Возвращает, изменилось ли что-нибудь.

    Тройка, совпавшая с сохранённой, — **no-op**: строка не трогается вовсе,
    поэтому `onupdate` по ней не срабатывает и `updated_at` года не двигается.
    Сравнение числовое (`Decimal.__eq__`), то есть `1.0830` и `1.083` считаются
    одним значением: разной у них только запись, а лист печатает величину.
    """
    year, coefficient, source, is_forecast = _year_triple(item)
    existing = db.execute(
        sa.select(InflationIndexValue).where(
            InflationIndexValue.series_id == series.id,
            InflationIndexValue.year == year,
        )
    ).scalar_one_or_none()

    if existing is None:
        db.add(
            InflationIndexValue(
                series_id=series.id, year=year, coefficient=coefficient,
                source=source, is_forecast=is_forecast,
            )
        )
        return True

    if (
        existing.coefficient == coefficient
        and existing.source == source
        and existing.is_forecast == is_forecast
    ):
        return False

    existing.coefficient = coefficient
    existing.source = source
    existing.is_forecast = is_forecast
    return True


def _require_name_available(db: Session, name: str, *, exclude_id: int | None) -> None:
    """«Название занято» проверяется ЯВНО, а `translating_integrity` остаётся.

    Одна проверка неполна — между ней и `INSERT` вклинивается параллельный
    запрос, — а один констрейнт даёт ответ без внятного текста. Пара из проверки и
    трансляции: это идиома проекта (`translating_integrity`, `routers/estimates.py`
    с `uq_import_jobs_active_pair`).
    """
    stmt = sa.select(InflationSeries.id).where(InflationSeries.name == name)
    if exclude_id is not None:
        stmt = stmt.where(InflationSeries.id != exclude_id)
    if db.execute(stmt).first() is not None:
        raise DomainError(409, _NAME_TAKEN["uq_inflation_series_name"])


def _apply_series_fields(series: InflationSeries, *, name, note, is_active) -> bool:
    changed = False
    if name is not UNSET:
        new_name = require_text(name, "Название ряда")
        if new_name != series.name:
            series.name = new_name
            changed = True
    if note is not UNSET:
        new_note = (note or "").strip() or None
        if new_note != series.note:
            series.note = new_note
            changed = True
    if is_active is not UNSET and bool(is_active) != series.is_active:
        series.is_active = bool(is_active)
        changed = True
    return changed


def _apply_archived_rules(series: InflationSeries, *, name, note, is_active, values) -> None:
    """Архивный ряд заморожен, но обратим (§2.10, DoD 28).

    Только `is_active: true` в одиночку — размораживает; вместе с полями или
    годами — `409`; любое тело без `is_active: true` — `409`.

    Совмещённая расконсервация запрещена намеренно: иначе «заморожен»
    проверялось бы внутри той же транзакции, которая размораживает, и правило
    перестало бы быть проверяемым. Сначала вернуть ряд в активные, потом править —
    два шага, каждый со своим смыслом.

    `409` выбран по прецеденту: у `category_overrides` тем же кодом отвечает
    `structure_disabled` — «объект выключен», случай того же рода.
    """
    if series.is_active:
        return
    unfreeze_only = (
        is_active is not UNSET
        and bool(is_active) is True
        and name is UNSET
        and note is UNSET
        # Пустой список годов — это отсутствие правок, а не правка: отвечать на
        # него `409` значило бы отличать `values: []` от опущенного поля там, где
        # разницы нет.
        and not values
    )
    if unfreeze_only:
        return
    raise DomainError(
        409,
        f"Ряд «{series.name}» в архиве и не правится. Сначала верните его в "
        "активные отдельным запросом, затем правьте.",
    )


# ---------------------------------------------------------------------------
#  Запись
# ---------------------------------------------------------------------------

def create_series(
    db: Session, *, name: str, note: str | None = None, values: list[dict] | None = None
) -> dict:
    """Создать ряд сразу с годами — одной транзакцией.

    Это первый и обязательный пользовательский путь: миграция создаёт справочник
    ПУСТЫМ (§2.6), и без создания через интерфейс фича не заводится вовсе.
    """
    payload_values = list(values or [])
    # Трансляция обёрнута вокруг ВСЕГО окна, а не только вокруг `commit`: `id`
    # ряда нужен годам, поэтому внутри окна есть `flush`, и нарушение уникальности
    # названия приходит именно на нём. Замерено красным тестом: без этого наружу
    # уезжал сырой `IntegrityError`, то есть 500 вместо 409.
    with translating_integrity(db, _NAME_TAKEN):
        with rollback_on_domain_error(db):
            _reject_duplicate_years(payload_values)
            series_name = require_text(name, "Название ряда")
            _require_name_available(db, series_name, exclude_id=None)
            series = InflationSeries(
                name=series_name,
                note=(note or "").strip() or None,
            )
            db.add(series)
            db.flush()
            for item in payload_values:
                _apply_year(db, series, item)
        db.commit()
    log.info("inflation_series_created id=%s years=%s", series.id, len(payload_values))
    return get_series_dict(db, series.id)


def update_series(
    db: Session,
    series_id: int,
    *,
    name: Any = UNSET,
    note: Any = UNSET,
    is_active: Any = UNSET,
    values: list[dict] | None = None,
) -> dict:
    """Изменить ряд целиком: название, примечание, `is_active` и годы — ОДНОЙ
    транзакцией (§2.12).

    Годы, не перечисленные в `values`, остаются как были: это `PATCH`, а `DELETE`
    запрещён (§2.10).
    """
    payload_values = list(values or [])
    with translating_integrity(db, _NAME_TAKEN), rollback_on_domain_error(db):
        # ДО мутаций: повтор года обязан отказать, не изменив ничего (DoD 29).
        _reject_duplicate_years(payload_values)
        series = _get_for_update(db, series_id)
        _apply_archived_rules(
            series, name=name, note=note, is_active=is_active, values=payload_values
        )
        if name is not UNSET:
            _require_name_available(
                db, require_text(name, "Название ряда"), exclude_id=series_id
            )
        changed = _apply_series_fields(series, name=name, note=note, is_active=is_active)
        # Порядок тела сохраняется: годы применяются в том порядке, в котором
        # пришли, — так отказ на втором годе застаёт первый уже применённым, и
        # именно это состояние обязан снять откат.
        for item in payload_values:
            changed |= _apply_year(db, series, item)
        if changed:
            # Метка ряда двигается ЯВНО: правка одного только года не делает
            # строку ряда «грязной», и `onupdate` по ней не сработал бы —
            # полоса уровней осталась бы с прежней датой при изменившихся числах.
            series.updated_at = sa.func.now()
    db.commit()
    log.info("inflation_series_updated id=%s changed=%s", series_id, changed)
    return get_series_dict(db, series_id)


def _get_for_update(db: Session, series_id: int) -> InflationSeries:
    series = db.execute(
        sa.select(InflationSeries)
        .where(InflationSeries.id == series_id)
        .with_for_update()
        # identity map отдал бы объект, загруженный ДО блокировки, и проверка
        # «в архиве» шла бы по устаревшему состоянию (урок фазы 5).
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if series is None:
        raise DomainError(404, f"Ряд индексов {series_id} не найден.")
    return series
