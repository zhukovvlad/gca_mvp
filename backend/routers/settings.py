"""Роутер настроек приложения (AGENTS.md §7.4, §9.6).

Права: чтение — любому аутентифицированному (паспорт читает `passport_top_n`, а
паспорт доступен и `member`), изменение — `admin` по §3 («admin — управление
пользователями, классами, нормативами…»: настройка печатной формы того же рода).

Роутер тонкий: валидация формы — Pydantic, диапазон и запись — `crud/settings.py`,
трансляция `DomainError` → HTTP — `_raise`. CSRF на PATCH обеспечивает middleware
из `main.py`.

`settings.py` **источника** (udp-tenders) непереносим: он про LLM/OpenRouter и писал
значения в `.env` через `set_key` — у настройки не было ни аудита, ни истории
(§3 брифинга фазы 6). Общее здесь только имя файла.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import require_admin
from crud import settings as crud_settings
from crud.common import DomainError
from database import get_db
from models import User

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


def _raise(err: DomainError):
    raise HTTPException(err.status_code, err.detail)


class SettingsUpdate(BaseModel):
    """Правка настроек.

    **Границы диапазона здесь намеренно НЕ объявлены** (`Field(ge=…, le=…)` убран).
    Замер показал, почему: Pydantic проверяет ограничения поля **до** тела хендлера,
    поэтому его отказ затеняет отказ `crud/settings.py` целиком — и человек получает
    «Input should be less than or equal to 20» вместо объяснения, что верхняя
    граница держит требование DoD §10 о печати на одну страницу А4. Ограничение
    осталось бы продублированным в трёх местах, а работал бы из них худший.

    Проверок остаётся две, и обе на своих местах: понятный 422 с причиной — в
    `crud/settings.py` (он же закрывает вызовы мимо роутера), и `CHECK` в БД,
    который нельзя обойти вовсе. Тип поля Pydantic по-прежнему проверяет: «много»
    вместо числа — ошибка формы, и для неё стандартного текста достаточно.
    """

    passport_top_n: int


@router.get("")
def get_settings(db: Session = Depends(get_db)):
    """Настройки приложения вместе с допустимым диапазоном `passport_top_n`."""
    return crud_settings.get_settings(db)


@router.patch("")
def update_settings(
    body: SettingsUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    try:
        return crud_settings.update_settings(db, passport_top_n=body.passport_top_n)
    except DomainError as e:
        _raise(e)
