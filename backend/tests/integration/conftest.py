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

from collections.abc import Iterator
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


# ---------------------------------------------------------------------------
#  Клиенты HTTP-слоя разноса (Задача 5)
# ---------------------------------------------------------------------------
#
# Корневой `client` (tests/conftest.py) не годится сюда: он подсовывает
# `get_current_user` MagicMock с `.id = 1`, а `PUT` этой фичи кладёт
# `current_user.id` в `assigned_by` — RESTRICT FK на `users.id`. Ни одного
# пользователя с id=1 в тестовой базе нет (сиды сдвинули последовательность),
# так что мок уронил бы `flush` `IntegrityError`-ом внутри сервиса, и `PUT`
# отдавал бы 500 вместо ожидаемого кода — искать пришлось бы мнимый баг роутера.


def _member_client(db_session, factories, *, raise_server_exceptions: bool) -> Iterator:
    """Общий строитель `member_client`/`member_client_no_raise` — две ручные
    копии одного и того же клиента отличались только одним флагом `TestClient`,
    а правило проекта против второй реализации одного понятия относится и к
    фикстурам, не только к продовому коду.

    `c.user` — держатель ТЕКУЩЕГО автора, `c.set_user(other)` его меняет:
    тесту на перенос аудита (`assigned_by`) нужен ВТОРОЙ, отличный от первого,
    пользователь для второго запроса — иначе поле `assigned_by` не может
    сдвинуться в принципе, что бы ни делал код (тот же приём, что у
    `auth_state["role"]` в корневом `client`).
    """
    from fastapi.testclient import TestClient

    from auth import get_current_user
    from database import get_db
    from main import app

    holder = {"user": factories.UserFactory.create(role=UserRole.member)}

    def override_get_db():
        try:
            yield db_session
        finally:
            pass  # cleanup в db_session фикстуре

    def override_get_current_user():
        return holder["user"]

    _csrf_token = "test-csrf-token"
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(
        app,
        headers={"X-CSRF-Token": _csrf_token},
        raise_server_exceptions=raise_server_exceptions,
    ) as c:
        c.cookies.set("csrf_token", _csrf_token)
        c.user = holder["user"]
        c.set_user = lambda u: (holder.__setitem__("user", u), setattr(c, "user", u))
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def member_client(db_session, factories) -> Iterator:
    """Как корневой `client`, но `get_current_user` возвращает НАСТОЯЩУЮ
    строку `users` с ролью `member` — право разноса статей по классу принадлежит
    ей, как и ручному матчингу (§3), а не только `admin`."""
    yield from _member_client(db_session, factories, raise_server_exceptions=True)


@pytest.fixture
def member_client_no_raise(db_session, factories) -> Iterator:
    """Как `member_client`, но `TestClient(..., raise_server_exceptions=False)`.

    Нужен тестам, где сервис доходит до `500` через непойманное исключение
    (`_apply` в `except CategoryOverrideError` для кодов вне `_STATUS` делает
    голый `raise`, а не `HTTPException`) — обычный `TestClient` пробрасывает
    такое исключение вызывающему коду теста вместо того, чтобы завернуть его
    в ответ, и `assert response.status_code == 500` до этой строки просто не
    дошёл бы. Тот же приём, что у `unauth_client` в `tests/test_auth_coverage.py`.
    """
    yield from _member_client(db_session, factories, raise_server_exceptions=False)


@pytest.fixture
def anon_client(db_session) -> Iterator:
    """`TestClient` БЕЗ переопределения `get_current_user`: запрос идёт в
    настоящую auth-зависимость и обязан получить `401` — эндпоинты разноса
    закрыты аутентификацией целиком, а не какой-то отдельной ролью (§3)."""
    from fastapi.testclient import TestClient

    from database import get_db
    from main import app

    def override_get_db():
        try:
            yield db_session
        finally:
            pass  # cleanup в db_session фикстуре

    _csrf_token = "test-csrf-token"
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, headers={"X-CSRF-Token": _csrf_token}) as c:
        c.cookies.set("csrf_token", _csrf_token)
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def other_category_id(db_session, category_id) -> int:
    """Статья-лист, отличная от `category_id` — тест переноса аудита проверяет
    смену `work_category_id`, и для этого нужны ДВЕ разные статьи."""
    used_as_parent = sa.select(WorkCategory.parent_id).where(WorkCategory.parent_id.is_not(None))
    return db_session.execute(
        sa.select(WorkCategory.id)
        .where(WorkCategory.id.not_in(used_as_parent), WorkCategory.id != category_id)
        .order_by(WorkCategory.sort_order)
        .limit(1)
    ).scalar_one()


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
def contract_with_amendment(db_session, factories):
    """Один договор с ДВУМЯ сметами — исходной и допсоглашением №1, обе через
    настоящий импорт (см. `make_imported_estimate`): та фабрика всегда создаёт
    себе новый договор, а тест посметного счётчика (задача 6) нуждается ровно
    в противоположном — паре смет ОДНОГО договора, чтобы отличить «решения
    есть у договора» от «решения есть у ЭТОЙ его сметы».

    Раздел без статьи в каждой смете — цель `set_override` в тесте.

    `resolver` — своя копия, не общая фикстура: `UnitResolver` дешёв
    (`services/unit_resolution.py`), а завязка на одноимённую фикстуру
    `test_estimate_import.py` сделала бы этот файл зависимым от того, какой
    тестовый модуль запущен рядом.
    """
    contract = factories.ContractFactory.create()
    db_session.flush()
    resolver = UnitResolver(db_session)

    def _import(amendment_no):
        data = payload_for(
            contract, [position(job_title="Раздел", is_chapter=True, chapter_number="1")]
        )
        outcome = import_estimate(
            db_session,
            contract=contract,
            amendment_no=amendment_no,
            data=data,
            parser_version="1.0.0",
            import_job_id=None,
            replace=False,
            unit_resolver=resolver,
            category_resolver=CategoryResolver.from_db(db_session),
        )
        db_session.flush()
        return outcome.estimate_id

    source_estimate_id = _import(None)
    amendment_estimate_id = _import(1)
    db_session.commit()
    return SimpleNamespace(
        id=contract.id,
        source_estimate_id=source_estimate_id,
        amendment_estimate_id=amendment_estimate_id,
    )


@pytest.fixture
def chapter_of_source(db_session, contract_with_amendment):
    """Раздел исходной сметы `contract_with_amendment` — цель решения в тесте
    посметного счётчика."""
    return db_session.execute(
        sa.select(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(
            Lot.estimate_id == contract_with_amendment.source_estimate_id,
            PositionItem.is_chapter.is_(True),
        )
    ).scalar_one()


@pytest.fixture
def chapter_of_amendment(db_session, contract_with_amendment):
    """Раздел допсоглашения №1 `contract_with_amendment` — второй, независимый
    раздел ТОЙ ЖЕ пары смет."""
    return db_session.execute(
        sa.select(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(
            Lot.estimate_id == contract_with_amendment.amendment_estimate_id,
            PositionItem.is_chapter.is_(True),
        )
    ).scalar_one()


@pytest.fixture
def unallocated_tree(db_session, make_imported_estimate):
    """Нераспределённая часть дерева разделов Задачи 4 — смета через НАСТОЯЩИЙ
    импорт (`make_imported_estimate`, не голые ORM-строки поверх чужого
    предложения): `services/category_override.apply_overrides` при разносе
    ВСЕГДА пересчитывает предложение целиком с проверкой биекции ключей
    `raw_data` ↔ строки БД (спека разноса §2.3) — строка, добавленная в обход
    импорта, тут же провалила бы эту биекцию `mapping_broken`. Поэтому у
    `unallocated_tree` СВОЯ смета, а не надстройка над `imported_estimate`.

    Числа заданы НЕЗАВИСИМО от кода, который их будет считать (спека разноса
    §5.2, задача 4): вершина «9» без единой своей позиции; ребёнок «9.1» с
    двумя расценёнными позициями (30 и 30); ребёнок «9.2» с одной
    расценённой (20), одной без цены и одной с ценой `'NaN'` — не число,
    исключается из суммы, но считается строкой; раздел «8» вовсе без позиций
    в поддереве — граница §5.2 обязана убрать его из выдачи. Ни одна из этих
    строк не несёт статьи — весь кусок нераспределён. Порядок строк ниже —
    файловый (глубина раздела резолвер считает по числу точек в номере, а
    родителя — стеком по порядку строк, спека разноса §1.2): «9.1»/«9.2»
    обязаны идти сразу за «9», иначе резолвер не признает их детьми «9».

    Второй кусок («20», «20.1», «20.1.1») — случай переподвешивания родителя
    (ревью гейта после Задачи 4): «20» несёт ВАЛИДНУЮ статью файла (код «1»),
    «20.1» несёт код «9999», которого нет в справочнике — правило Ф3
    «утверждение файла сильнее наследования» (`services/category_resolution.
    _article_for`) не даёт ей унаследовать статью «20» именно ПОТОМУ, что у
    неё есть собственное (хоть и нечитаемое системой) утверждение: «20.1»
    действительно без статьи, хотя её файловый родитель — раздел С статьёй.
    «20.1.1» — без своей статьи вовсе, наследует от «20.1» (та без статьи —
    наследовать нечего), тоже без статьи.

    Третий кусок («21», «21.1», «21.2») — случай смешанного поддерева (та же
    ревью-находка, пункт 2): «21» и «21.1» без статьи, «21.2» несёт СВОЮ
    валидную статью файла (код «2») и потому ничего не наследует и ничего не
    отдаст решению на «21» — правило Ф3 в чистом виде, без «нечитаемого» кода.

    Четвёртый кусок («22», «22.1», «22.2») — та же ревью-находка, но с
    блокирующим узлом («22.1», код «9999») ПОД родителем, у которого своей
    статьи нет вовсе (в отличие от куска «20», где родитель СО статьёй): «22»
    без утверждения, «22.1» — код «9999» (блокирует наследование СВОИМ
    утверждением, не статусом родителя), «22.2» — без утверждения, наследует
    от «22» нормально. Доказывает, что предикат — «есть своё утверждение»,
    а не «предок со статьёй»: у «22» самой статьи нет, а «22.1» всё равно не
    наследует от неё и не отдаёт ей своих денег.
    """
    estimate = make_imported_estimate(
        [
            position(job_title="Раздел 8 — пустой", is_chapter=True, chapter_number="8"),
            position(job_title="Раздел 9", is_chapter=True, chapter_number="9"),
            position(job_title="Подраздел 9.1", is_chapter=True, chapter_number="9.1"),
            position(
                job_title="Позиция 9.1-а", unit="м2", quantity=1, suggested_quantity=1,
                unit_cost_total="30", total_cost_total="30", chapter_ref="9.1",
            ),
            position(
                job_title="Позиция 9.1-б", unit="м2", quantity=1, suggested_quantity=1,
                unit_cost_total="30", total_cost_total="30", chapter_ref="9.1",
            ),
            position(job_title="Подраздел 9.2", is_chapter=True, chapter_number="9.2"),
            position(
                job_title="Позиция 9.2-а", unit="м2", quantity=1, suggested_quantity=1,
                unit_cost_total="20", total_cost_total="20", chapter_ref="9.2",
            ),
            position(
                job_title="Позиция 9.2-б без цены", unit="м2", quantity=1, suggested_quantity=1,
                unit_cost_total=None, total_cost_total=None, chapter_ref="9.2",
            ),
            position(
                job_title="Позиция 9.2-в с ценой NaN", unit="м2", quantity=1, suggested_quantity=1,
                unit_cost_total="NaN", total_cost_total="NaN", chapter_ref="9.2",
            ),
            # Переподвешивание: «20» — со статьёй, «20.1» — без (код «9999» не
            # в справочнике, наследовать не даёт), «20.1.1» — без (наследует
            # от «20.1», а там наследовать нечего).
            position(
                job_title="Раздел 20 — со статьёй", is_chapter=True, chapter_number="20",
                article_smr="1",
            ),
            position(
                job_title="Подраздел 20.1 — код не в справочнике", is_chapter=True,
                chapter_number="20.1", article_smr="9999",
            ),
            position(
                job_title="Позиция 20.1-а", unit="м2", quantity=1, suggested_quantity=1,
                unit_cost_total="15", total_cost_total="15", chapter_ref="20.1",
            ),
            position(job_title="Подраздел 20.1.1", is_chapter=True, chapter_number="20.1.1"),
            position(
                job_title="Позиция 20.1.1-а", unit="м2", quantity=1, suggested_quantity=1,
                unit_cost_total="5", total_cost_total="5", chapter_ref="20.1.1",
            ),
            # Смешанное поддерево: «21» без статьи, «21.1» без статьи (её
            # деньги решение на «21» переместит), «21.2» — своя валидная
            # статья файла (её деньги решение на «21» НЕ переместит).
            position(job_title="Раздел 21", is_chapter=True, chapter_number="21"),
            position(job_title="Подраздел 21.1 — без статьи", is_chapter=True, chapter_number="21.1"),
            position(
                job_title="Позиция 21.1-а", unit="м2", quantity=1, suggested_quantity=1,
                unit_cost_total="45", total_cost_total="45", chapter_ref="21.1",
            ),
            position(
                job_title="Подраздел 21.2 — своя статья", is_chapter=True, chapter_number="21.2",
                article_smr="2",
            ),
            position(
                job_title="Позиция 21.2-а", unit="м2", quantity=1, suggested_quantity=1,
                unit_cost_total="999", total_cost_total="999", chapter_ref="21.2",
            ),
            # Блокирующий узел ПОД родителем без статьи: «22» без утверждения,
            # «22.1» — код «9999» (блокирует своим утверждением), «22.2» —
            # без утверждения (наследует от «22» нормально).
            position(job_title="Раздел 22 — без утверждения", is_chapter=True, chapter_number="22"),
            position(
                job_title="Подраздел 22.1 — код не в справочнике", is_chapter=True,
                chapter_number="22.1", article_smr="9999",
            ),
            position(
                job_title="Позиция 22.1-а", unit="м2", quantity=1, suggested_quantity=1,
                unit_cost_total="777", total_cost_total="777", chapter_ref="22.1",
            ),
            position(job_title="Подраздел 22.2 — без статьи", is_chapter=True, chapter_number="22.2"),
            position(
                job_title="Позиция 22.2-а", unit="м2", quantity=1, suggested_quantity=1,
                unit_cost_total="33", total_cost_total="33", chapter_ref="22.2",
            ),
        ]
    )

    def _chapter(number: str) -> int:
        return db_session.execute(
            sa.select(PositionItem.id)
            .join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(
                Lot.estimate_id == estimate.id,
                PositionItem.is_chapter.is_(True),
                PositionItem.chapter_number_in_proposal == number,
            )
        ).scalar_one()

    return SimpleNamespace(
        contract_id=estimate.contract_id,
        estimate_id=estimate.id,
        top_chapter_id=_chapter("9"),
        empty_chapter_id=_chapter("8"),
        mixed_top_chapter_id=_chapter("21"),
    )


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
