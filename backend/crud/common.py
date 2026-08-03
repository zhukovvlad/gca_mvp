"""Общее для CRUD-слоя фазы 5: доменная ошибка, пагинация, сериализация дат.

Паттерн взят у `crud/admin.py` (`AdminError`): доменный слой бросает исключение с
рекомендованным HTTP-статусом, роутер остаётся тонким и только транслирует его.
Отдельный модуль нужен потому, что фаза 5 добавляет четыре CRUD-модуля, и
дублировать это исключение в каждом означало бы четыре разных `except` в роутерах.

`AdminError` намеренно не тронут: на нём висят зелёные тесты фазы 1, а сходство
формы (`status_code`, `detail`) и так делает трансляцию одинаковой.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy import Select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

#: Потолок размера страницы. Тот же, что у `crud/admin.py`.
MAX_PAGE_SIZE = 100


class DomainError(Exception):
    """Отказ доменной операции. Текст показывается человеку.

    Attributes:
        status_code: рекомендованный HTTP-статус (400/404/409/422).
        detail: понятное сообщение — оно доезжает до тоста на экране.
    """

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def iso(value: datetime | date | None) -> str | None:
    """Дата/время в ISO-строку.

    `DecimalJSONResponse` (main.py) умеет только `Decimal`; его энкодер бросает
    `TypeError` на `datetime`. Поэтому даты приводятся явно — как это делает
    `routers/estimates.job_response`.
    """
    return value.isoformat() if value is not None else None


def clamp_page(page: int, page_size: int) -> tuple[int, int]:
    """Границы пагинации. Совпадает с поведением `list_users_paginated`."""
    return max(1, page), max(1, min(MAX_PAGE_SIZE, page_size))


def paginated(db: Session, stmt: Select, *, order_by, page: int, page_size: int) -> tuple[list, int]:
    """Страница и общее число строк для готового `select`.

    `total` считается по тому же запросу, обёрнутому в подзапрос и без сортировки:
    так счётчик и страница гарантированно отвечают про одно множество, какие бы
    фильтры и join-ы к запросу ни добавили выше.

    Returns:
        (строки страницы, общее число строк до пагинации).
    """
    total = db.execute(
        sa.select(sa.func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()
    rows = db.execute(
        stmt.order_by(*order_by).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return rows, total


@contextmanager
def translating_integrity(db: Session, messages: dict[str, str]) -> Iterator[None]:
    """Переводит нарушение известного констрейнта в `DomainError`, чужое — пропускает.

    Проверка «уже занято» перед вставкой остаётся (её текст точнее), но одна она
    неполна: между проверкой и `INSERT` вклинивается параллельный запрос.
    Констрейнт закрывает гонку, а этот менеджер делает её ответ тем же, что и у
    синхронной проверки, — 409, а не 500. Тот же приём, что у
    `routers/estimates.py` с `uq_import_jobs_active_pair`.

    Args:
        db: сессия; при нарушении откатывается — продолжать в ней нельзя.
        messages: имя констрейнта → текст для человека.
    """
    try:
        yield
    except IntegrityError as exc:
        db.rollback()
        text = str(exc.orig)
        for name, message in messages.items():
            if name in text:
                raise DomainError(409, message) from exc
        raise


def require_text(value: str | None, field: str) -> str:
    """Обязательное текстовое поле: пусто или пробелы — отказ 422.

    Пустая строка проходит NOT NULL, но означает незаполненную карточку, а
    карточка договора — источник истины при импорте (§3).
    """
    text = (value or "").strip()
    if not text:
        raise DomainError(422, f"Поле «{field}» не может быть пустым.")
    return text
