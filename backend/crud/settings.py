"""CRUD настроек приложения (AGENTS.md §7.4; форма таблицы — решение §6.2 фазы 6).

В MVP настройка одна — `passport_top_n`, число «ключевых расценок» паспорта. §7.4
требует хранить её **в БД**: «N хранится в БД (таблица настроек, экран Settings из
udp-tenders), не в конфиге приложения».

Почему не «ключ→значение» и почему верхняя граница живёт в `CHECK`, а не в Python,
— `docs/phase6-analytics.md` §1.2 и §1.5. Здесь важны два следствия:

* **Границы отдаются клиенту.** Экран настроек обязан знать допустимый диапазон,
  чтобы не отправлять заведомо отвергаемое значение. Продублировать числа на
  фронтенде значило бы завести второе представление ограничения, которое разъедется
  с `CHECK` при первом же изменении.
* **Запись — только через ORM.** `updated_at` обновляется `onupdate` на стороне
  SQLAlchemy, и raw-SQL `UPDATE` метку не тронет (соглашение
  `docs/phase2-schema.md`). Поэтому правка идёт присваиванием полю, а не `UPDATE`.

Денег в настройках нет, поэтому `decimal_json` здесь не нужен — и это решение, а не
забывчивость: `passport_top_n` целое, `Decimal` в ответе не появляется.
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.common import DomainError, iso
from models import (
    PASSPORT_TOP_N_MAX,
    PASSPORT_TOP_N_MIN,
    AppSettings,
)

log = logging.getLogger(__name__)

#: Единственный допустимый `id` (singleton, `CHECK (id = 1)` в миграции 0004).
SETTINGS_ID = 1


def _settings_dict(row: AppSettings) -> dict:
    return {
        "passport_top_n": row.passport_top_n,
        # Границы — часть контракта: см. модульную документацию.
        "passport_top_n_min": PASSPORT_TOP_N_MIN,
        "passport_top_n_max": PASSPORT_TOP_N_MAX,
        "updated_at": iso(row.updated_at),
    }


def get_settings_row(db: Session) -> AppSettings:
    """Строка настроек. Отсутствующую досоздаёт со значениями по умолчанию.

    Строку сеет миграция 0004, и в норме этот путь не исполняется. Досоздание здесь
    — не дублирование сева, а защита от единственного случайного `DELETE`: без строки
    настроек отказали бы сразу и паспорт, и экран настроек, то есть цена пустяковой
    ошибки была бы несоразмерной. `ON CONFLICT DO NOTHING` делает досоздание
    безопасным и при гонке двух запросов.
    """
    row = db.get(AppSettings, SETTINGS_ID)
    if row is not None:
        return row

    log.warning("Строка настроек отсутствует — досоздаю со значениями по умолчанию")
    db.execute(
        sa.text("INSERT INTO app_settings (id) VALUES (:id) ON CONFLICT (id) DO NOTHING"),
        {"id": SETTINGS_ID},
    )
    db.commit()
    row = db.get(AppSettings, SETTINGS_ID)
    if row is None:  # pragma: no cover — недостижимо: строку только что вставили
        raise DomainError(500, "Не удалось создать строку настроек приложения.")
    return row


def get_settings(db: Session) -> dict:
    return _settings_dict(get_settings_row(db))


def get_passport_top_n(db: Session) -> int:
    """N для «ключевых расценок» паспорта (§7.4). Точка входа для эндпоинта паспорта."""
    return get_settings_row(db).passport_top_n


def update_settings(db: Session, *, passport_top_n: int) -> dict:
    """Изменить настройки (право `admin` проверяет роутер).

    Диапазон проверяется здесь **до** записи, чтобы человек получил понятный 422, а
    не нарушение `CHECK` пятисотым. Сам `CHECK` при этом остаётся — он закрывает
    любой другой путь записи, включая ручной `UPDATE` в psql.
    """
    if not PASSPORT_TOP_N_MIN <= passport_top_n <= PASSPORT_TOP_N_MAX:
        raise DomainError(
            422,
            f"Число ключевых расценок должно быть от {PASSPORT_TOP_N_MIN} до "
            f"{PASSPORT_TOP_N_MAX}. Верхняя граница — не прихоть: паспорт обязан "
            "печататься на одну страницу А4 со всеми полями (AGENTS.md §10), а при "
            "большем N топ расценок на страницу не влезает.",
        )

    row = get_settings_row(db)
    # Присваивание полю, а не UPDATE: иначе updated_at останется прежним.
    row.passport_top_n = passport_top_n
    db.commit()
    log.info("app_settings_updated passport_top_n=%s", passport_top_n)
    return _settings_dict(row)
