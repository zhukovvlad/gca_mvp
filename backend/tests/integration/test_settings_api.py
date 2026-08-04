"""Настройки приложения: топ-N ключевых расценок паспорта (§7.4, решение §6.2 фазы 6).

Проверяется то, что решено фазой 6, а не то, что и так держит схема (отказы БД —
`test_schema_constraints.py::TestAppSettings`):

* чтение доступно и `member` — паспорт читает `passport_top_n`, а паспорт не admin-only;
* изменение — только `admin`;
* диапазон отвергается **понятным 422**, а не пятисотым от нарушения `CHECK`;
* границы диапазона отдаются клиенту (иначе форма завела бы второе представление
  ограничения, которое разъедется с `CHECK`);
* `updated_at` двигается при правке — то есть запись идёт через ORM, а не raw-SQL
  (соглашение `docs/phase2-schema.md`);
* исчезнувшая строка настроек досоздаётся, а не роняет экраны.
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from models import PASSPORT_TOP_N_DEFAULT, PASSPORT_TOP_N_MAX, PASSPORT_TOP_N_MIN, UserRole

pytestmark = pytest.mark.integration


@pytest.fixture
def member(client):
    client.auth_state["role"] = UserRole.member
    yield client
    client.auth_state["role"] = UserRole.admin


@pytest.fixture
def restore_settings(db_engine):
    """Возвращает `passport_top_n` к значению по умолчанию после теста с настоящими
    commit-ами.

    Нужна потому, что `app_settings` намеренно НЕ входит в `_DOMAIN_TABLES` conftest-а:
    её строку сеет миграция, а не тест, и `TRUNCATE` вокруг committing-фикстур её не
    трогает. Значит настоящий commit в настройки переживает тест и утекает в другие
    файлы — `test_schema_constraints.py::TestAppSettings` проверяет **засеянное**
    значение и падал в зависимости от порядка файлов. Дефект нашёлся контрольным
    прогоном пробника снятия защит, а не рассуждением.
    """
    yield
    with db_engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE app_settings SET passport_top_n = :n WHERE id = 1"),
            {"n": PASSPORT_TOP_N_DEFAULT},
        )


def test_get_settings_returns_default_and_bounds(client):
    body = client.get("/api/v1/settings").json()
    assert body["passport_top_n"] == PASSPORT_TOP_N_DEFAULT
    # Границы — часть контракта: их использует форма экрана настроек.
    assert body["passport_top_n_min"] == PASSPORT_TOP_N_MIN
    assert body["passport_top_n_max"] == PASSPORT_TOP_N_MAX


def test_member_can_read_settings(member):
    """Паспорт доступен `member` (§3), а он читает N — значит и чтение настроек тоже."""
    assert member.get("/api/v1/settings").status_code == 200


def test_member_cannot_change_settings(member):
    response = member.patch("/api/v1/settings", json={"passport_top_n": 10})
    assert response.status_code == 403


def test_admin_updates_top_n(client):
    response = client.patch("/api/v1/settings", json={"passport_top_n": 10})
    assert response.status_code == 200
    assert response.json()["passport_top_n"] == 10
    assert client.get("/api/v1/settings").json()["passport_top_n"] == 10


@pytest.mark.parametrize("value", [PASSPORT_TOP_N_MIN, PASSPORT_TOP_N_MAX])
def test_boundary_values_accepted(client, value):
    response = client.patch("/api/v1/settings", json={"passport_top_n": value})
    assert response.status_code == 200, response.text
    assert response.json()["passport_top_n"] == value


@pytest.mark.parametrize("value", [0, -1, PASSPORT_TOP_N_MAX + 1, 999])
def test_out_of_range_gives_422_not_500(client, value):
    """Нарушение диапазона обязано доехать до человека текстом, а не как 500 от CHECK."""
    response = client.patch("/api/v1/settings", json={"passport_top_n": value})
    assert response.status_code == 422, response.text


def test_out_of_range_message_explains_the_a4_reason(client):
    """Текст отказа объясняет ПРИЧИНУ верхней границы (DoD §10 про одну страницу А4).

    Проверяется не формулировка, а наличие объяснения: настройка «почему нельзя
    больше» неочевидна, и без причины отказ читается как произвол.
    """
    response = client.patch("/api/v1/settings", json={"passport_top_n": PASSPORT_TOP_N_MAX + 1})
    detail = response.json()["detail"]
    assert "А4" in str(detail)


def test_non_integer_rejected(client):
    assert client.patch("/api/v1/settings", json={"passport_top_n": "много"}).status_code == 422


def test_update_persists_the_value(client):
    client.patch("/api/v1/settings", json={"passport_top_n": 7})
    assert client.get("/api/v1/settings").json()["passport_top_n"] == 7


def test_update_moves_updated_at(committing_client, restore_settings):
    """Запись через ORM, а не raw-SQL UPDATE: иначе метка осталась бы прежней.

    **Фикстура здесь обязана быть `committing_client`, а не `client`.** `updated_at`
    получает `now()`, то есть время начала ТРАНЗАКЦИИ, а транзакционная фикстура
    держит весь тест в одной транзакции — метка не сдвинулась бы и при полностью
    правильном коде, и тест проходил бы, ничего не проверяя (первая редакция этого
    теста именно так и была написана: `assert after >= before` истинно и при
    равенстве). Здесь каждый запрос — своя транзакция, поэтому сравнение осмысленно,
    и снятие защиты (замена присваивания на raw-SQL `UPDATE`) его валит.
    """
    before = committing_client.get("/api/v1/settings").json()["updated_at"]
    assert before is not None

    assert committing_client.patch("/api/v1/settings", json={"passport_top_n": 7}).status_code == 200

    after = committing_client.get("/api/v1/settings").json()["updated_at"]
    assert after > before, f"updated_at не сдвинулся: {before} → {after}"


def test_missing_settings_row_is_recreated(client, db_session):
    """Случайное удаление строки настроек не должно ронять паспорт и экран настроек."""
    db_session.execute(sa.text("DELETE FROM app_settings"))
    db_session.flush()

    body = client.get("/api/v1/settings").json()
    assert body["passport_top_n"] == PASSPORT_TOP_N_DEFAULT
