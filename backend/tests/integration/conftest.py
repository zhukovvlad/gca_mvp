"""Фикстуры каталога `tests/integration/` — общие для ВСЕХ его тестов.

Область видимости — не только фича «ручной разнос статей»: любое имя,
определённое здесь, автоматически доступно всем ~700 существующим
интеграционным тестам каталога, а не только тестам этой фичи. Задачи 2-6
фичи добавляют фикстуры сюда же, а не заводят второй conftest в этой папке —
но каждое новое имя обязано быть недвусмысленным именно в этом более широком
масштабе: например, `make_override_proposal`/`_override_proposal` ниже
названы не просто `_proposal`, потому что `test_category_totals_view.py` и
`test_project_passport_api.py` уже держат ЛОКАЛЬНУЮ функцию с этим именем —
совпадение имени с фикстурой conftest тихо подменило бы её, если там появится
тест с параметром `_proposal`.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from models import UserRole, WorkCategory


@pytest.fixture
def admin_user(factories):
    """Автор ручного решения — роль admin (право на разнос, спека §3)."""
    return factories.UserFactory.create(role=UserRole.admin)


@pytest.fixture
def category_id(db_session) -> int:
    """Любая статья справочника, но обязательно ЛИСТ дерева.

    Родитель («1» и т.п.) упёрся бы в work_categories_parent_id_fkey раньше, чем в
    FK решения на статью — тот же урок Ф3, что у
    test_schema_constraints.py::TestPositionItemCategoryColumns._any_category_id.

    `.is_not(None)` в фильтре обязателен: без него `NOT IN` сравнивал бы
    `WorkCategory.id` с подзапросом, где хотя бы одно значение — NULL (корни
    дерева), а `x NOT IN (a, NULL, ...)` в SQL — всегда NULL, то есть ЛОЖЬ для
    каждой строки. Без фильтра подзапрос вернул бы ноль строк для абсолютно
    любой статьи, включая листья.
    """
    used_as_parent = sa.select(WorkCategory.parent_id).where(WorkCategory.parent_id.is_not(None))
    return db_session.execute(
        sa.select(WorkCategory.id)
        .where(WorkCategory.id.not_in(used_as_parent))
        .order_by(WorkCategory.sort_order)
        .limit(1)
    ).scalar_one()


@pytest.fixture
def make_override_proposal(factories):
    """Фабрика цепочки договор→смета→лот→предложение — CALLABLE, не синглтон.

    Тестам Задач 3-6 нужны НЕСКОЛЬКО независимых смет внутри ОДНОГО теста:
    вторая смета (строка-раздел чужой сметы отклоняется), смета со сломанной
    нумерацией разделов (резолвер отключает её структуру), смета с
    `estimate_additional_works`. Синглтон-фикстура строится ровно один раз за
    тест и такого дать не может — отсюда фабрика, а не готовый объект.
    """
    def _make():
        return factories.ProposalFactory.create()
    return _make


@pytest.fixture
def _override_proposal(make_override_proposal):
    """Одна цепочка, построенная `make_override_proposal` РОВНО ОДИН раз за тест.

    Общая для `estimate_id`/`chapter_row`/`chapter_rows_ten` ниже: тест каскада
    от сметы (`test_deleting_the_estimate_takes_the_override`) удаляет смету по
    `estimate_id` и ожидает, что решение на `chapter_row` исчезнет вместе с
    ней — это осмысленно только если оба id взяты из ОДНОЙ цепочки.
    """
    return make_override_proposal()


@pytest.fixture
def estimate_id(_override_proposal) -> int:
    return _override_proposal.lot.estimate.id


@pytest.fixture
def chapter_row(factories, _override_proposal):
    """Строка-раздел без статьи — цель ручного решения в тестах схемы."""
    return factories.PositionItemFactory.create(proposal=_override_proposal, is_chapter=True)


@pytest.fixture
def chapter_rows_ten(factories, _override_proposal):
    """Десять строк-разделов — партия СТРОГО больше пяти.

    `prepare_threshold = 5` в psycopg3: партия из 5 объектов схемы проходит
    даже с молчаливой опечаткой в имени, партия из 10 её ловит.
    """
    return [
        factories.PositionItemFactory.create(proposal=_override_proposal, is_chapter=True)
        for _ in range(10)
    ]


@pytest.fixture
def override_row(db_session, chapter_row, category_id, admin_user):
    """Одно решение о статье `chapter_row`.

    Саму строку `position_items` не трогает: решение (эта таблица) и
    материализация (обновление `position_items.category_source`) — разные
    шаги (спека §2.3), и таблица решений обязана жить сама по себе.

    Вставка raw SQL, а не ORM `add`: иначе объект осел бы в identity map
    сессии, и `db_session.get(EstimateCategoryOverride, ...)` в тесте каскада
    от сметы вернул бы закешированный питон-объект вместо фактического
    состояния БД после DELETE — тот же урок, что у test_schema_constraints.py
    (`TestCascades`, где по этой причине зовут `db_session.expire_all()`).
    """
    position_item_id = db_session.execute(
        sa.text(
            "INSERT INTO estimate_category_overrides "
            "(position_item_id, work_category_id, assigned_by) "
            "VALUES (:pid, :cat, :uid) RETURNING position_item_id"
        ),
        {"pid": chapter_row.id, "cat": category_id, "uid": admin_user.id},
    ).scalar_one()
    return SimpleNamespace(
        position_item_id=position_item_id, work_category_id=category_id, assigned_by=admin_user.id
    )
