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

from models import Estimate, Lot, PositionItem, Proposal, UserRole, WorkCategory
from services.category_resolution import CategoryResolver
from services.estimate_import import import_estimate
from services.unit_resolution import UnitResolver
from tests.payloads import additional_works_row, payload_for, position, svedeniya_info


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


# ---------------------------------------------------------------------------
#  Второй строитель цепочки — через настоящий импорт (Задача 3 и далее)
# ---------------------------------------------------------------------------
#
# `make_override_proposal` выше даёт цепочку договор→смета→лот→предложение из
# ORM-фабрик, БЕЗ `estimate_raw_data` — этого достаточно тестам схемы, которым
# нужны только строки. Сервису применения решений (`services/category_override.py`)
# настоящий `raw_data` необходим: вход его резолвера собирается ИЗ НЕГО, а не из
# строк БД (спека разноса §2.3, проверяемость неизменяемым JSON). Поэтому здесь —
# второй строитель, а не второе применение первого: он идёт через реальный
# `import_estimate`, как `run_import` в `test_estimate_import.py` (тот хелпер
# локален своему модулю — по правилу этого репозитория тестовые хелперы не
# импортируются между модулями тестов, поэтому здесь его эквивалент, не импорт).


@pytest.fixture
def make_imported_estimate(db_session, factories):
    """Смета через НАСТОЯЩИЙ импорт — callable, тестам Задач 3-6 нужно несколько
    независимых смет за один тест (чужая смета, смета со сломанной нумерацией
    разделов и т.п., как и `make_override_proposal` выше)."""

    def _make(positions, **kwargs):
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(contract, positions, **kwargs)
        outcome = import_estimate(
            db_session,
            contract=contract,
            amendment_no=None,
            data=data,
            parser_version="1.0.0",
            import_job_id=None,
            replace=False,
            unit_resolver=UnitResolver(db_session),
            category_resolver=CategoryResolver.from_db(db_session),
        )
        db_session.flush()
        return db_session.get(Estimate, outcome.estimate_id)

    return _make


@pytest.fixture
def imported_estimate(make_imported_estimate):
    """Смета Задачи 3: раздел без статьи с подразделом ПОД СОБОЙ (нужен
    `top_unassigned_chapter` — наследование решения обязано дойти до всего
    поддерева, не только до самого раздела) и отдельный раздел С валидной
    статьёй файла — без него пересчёт без решений воспроизводил бы только
    пустоту, и тест на воспроизводимость импорта ничего не стерёг бы."""
    return make_imported_estimate(
        [
            position(job_title="Раздел без статьи", is_chapter=True, chapter_number="1"),
            position(job_title="Подраздел без статьи", is_chapter=True, chapter_number="1.1"),
            position(
                job_title="Работа под подразделом",
                unit="м2",
                quantity=1,
                suggested_quantity=1,
                unit_cost_total="100.00",
                total_cost_total="100.00",
                chapter_ref="1.1",
                number="3",
            ),
            position(
                job_title="Раздел со статьёй",
                is_chapter=True,
                chapter_number="2",
                article_smr="1",
                number="4",
            ),
            position(
                job_title="Работа под разделом со статьёй",
                unit="м2",
                quantity=1,
                suggested_quantity=1,
                unit_cost_total="200.00",
                total_cost_total="200.00",
                chapter_ref="2",
                number="5",
            ),
        ]
    )


@pytest.fixture
def top_unassigned_chapter(db_session, imported_estimate):
    """Раздел «1» без статьи, с подразделом «1.1» под собой — цель ручного
    решения в тестах сервиса разноса."""
    return db_session.execute(
        sa.select(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(
            Lot.estimate_id == imported_estimate.id,
            PositionItem.job_title_in_proposal == "Раздел без статьи",
        )
    ).scalar_one()


@pytest.fixture
def any_position_row(db_session, imported_estimate):
    """Любая строка-ПОЗИЦИЯ (не раздел) `imported_estimate` — статья привязывается
    только к разделам (спека §1.3), и это то, что здесь проверяется отказом."""
    return db_session.execute(
        sa.select(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id == imported_estimate.id, PositionItem.is_chapter.is_(False))
        .limit(1)
    ).scalar_one()


@pytest.fixture
def other_estimate_chapter(db_session, make_imported_estimate):
    """Раздел ЧУЖОЙ сметы — второй вызов строителя даёт независимую цепочку."""
    other = make_imported_estimate(
        [position(job_title="Раздел чужой сметы", is_chapter=True, chapter_number="1")]
    )
    return db_session.execute(
        sa.select(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id == other.id, PositionItem.is_chapter.is_(True))
    ).scalar_one()


@pytest.fixture
def estimate_with_extra_ref(make_imported_estimate):
    """Смета с агрегатной строкой допработ, расшитой через «Сведения» на
    раздел «1», у которого своей статьи нет — начальное состояние
    `resolve_ref` обязано быть «кандидат без статьи» (спека §1.4)."""
    return make_imported_estimate(
        [
            position(job_title="Раздел без статьи", is_chapter=True, chapter_number="1"),
            position(
                job_title="Работа",
                unit="м2",
                quantity=1,
                suggested_quantity=1,
                unit_cost_total="500.00",
                total_cost_total="500.00",
                chapter_ref="1",
                number="2",
            ),
        ],
        additional_works=additional_works_row(total="500.00"),
        additional_info=svedeniya_info("1 Допработы по разделу - 500.00 руб."),
    )


@pytest.fixture
def referenced_chapter(db_session, estimate_with_extra_ref):
    """Раздел «1» `estimate_with_extra_ref` — на него ссылается строка допработ."""
    return db_session.execute(
        sa.select(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(
            Lot.estimate_id == estimate_with_extra_ref.id, PositionItem.is_chapter.is_(True)
        )
    ).scalar_one()


@pytest.fixture
def estimate_with_broken_numbering(make_imported_estimate):
    """Смета, у которой резолвер отключает структуру целиком: номер раздела
    не разбирается в глубину (тот же приём, что в `test_estimate_import.py`)."""
    return make_imported_estimate(
        [position(job_title="Примечание", number="3", chapter_number="прим.", is_chapter=True)]
    )


@pytest.fixture
def broken_chapter(db_session, estimate_with_broken_numbering):
    """Единственный раздел `estimate_with_broken_numbering` — цель решения,
    которое обязано быть отказано кодом `structure_disabled`."""
    return db_session.execute(
        sa.select(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(
            Lot.estimate_id == estimate_with_broken_numbering.id,
            PositionItem.is_chapter.is_(True),
        )
    ).scalar_one()
