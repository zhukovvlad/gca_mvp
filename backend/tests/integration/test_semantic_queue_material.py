"""Пакетная загрузка материала запроса, предикат применимости и рендер на
реальных данных (задача 2 фичи «Семантические предложения»).

План: `docs/superpowers/plans/2026-09-28-semantic-suggestions.md`, задача 2.
Спека: `docs/superpowers/specs/2026-09-28-semantic-suggestions-design.md`
§2.2, §2.7, §2.10.

Чистые функции (`top_path`, `is_applicable`, `render_context_request` на
вручную собранном материале) — в `tests/unit/test_semantic_queue_request.py`.
Здесь — то, что нельзя проверить без базы: `load_request_material` (число
запросов, агрегаты, батч кандидатов) и сквозной путь «реальный контекст →
материал → тело».

Помощники (`_proposal`, `_chain_context`, `_leaf_category_ids`,
`_capturing_sql`) — ЛОКАЛЬНАЯ копия помощников `test_context_routing.py` и
`test_work_families.py`, не импорт (докстрока `test_context_routing.py`:
наборы помощников тестов проекта друг у друга не импортируют).
"""
from __future__ import annotations

import contextlib
import json
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy import event

from config import Settings
from models import (
    CatalogContext,
    ContextMember,
    MembershipState,
    SemanticState,
    WorkCategory,
)
from services.context_routing import route_position
from services.semantic_request import load_request_material, render_context_request, top_path
from services.unit_resolution import UnitResolver
from services.work_families import activate_family, archive_family, create_family

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
#  Помощники (локальная копия)
# ---------------------------------------------------------------------------

def _proposal(factories):
    estimate = factories.EstimateFactory.create()
    lot = factories.LotFactory.create(estimate=estimate)
    return factories.ProposalFactory.create(lot=lot)


def _unit_id(db, code):
    return UnitResolver(db).resolve(code).unit_id


def _leaf_category_ids(session, n=1) -> list[int]:
    """N различных ЛИСТЬЕВ классификатора (не использованных как parent_id) —
    тот же приём, что `test_context_routing.py::_leaf_category_ids`."""
    used_as_parent = sa.select(WorkCategory.parent_id).where(WorkCategory.parent_id.is_not(None))
    return list(
        session.execute(
            sa.select(WorkCategory.id)
            .where(WorkCategory.id.not_in(used_as_parent))
            .order_by(WorkCategory.sort_order)
            .limit(n)
        )
        .scalars()
        .all()
    )


def _active_family(db, *, title, unit_name, actor_id, definition="Определение семьи"):
    fam = create_family(db, title=title, unit_name=unit_name, definition=definition, actor_id=actor_id)
    return activate_family(db, family_id=fam.id, actor_id=actor_id)


@contextlib.contextmanager
def _capturing_sql(session):
    """Перехватывает каждый SQL-текст, реально отправленный на этом
    соединении — тот же приём, что `test_context_routing.py::_capturing_sql`."""
    statements: list[str] = []

    def _listener(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    connection = session.connection()
    event.listen(connection, "before_cursor_execute", _listener)
    try:
        yield statements
    finally:
        event.remove(connection, "before_cursor_execute", _listener)


def _simple_context(db, factories, proposal, *, unit_id, title) -> int:
    """Одна каталожная строка без раздела и без статьи — своя корзина, свой
    контекст по умолчанию, ровно одно членство."""
    cp = factories.CatalogPositionFactory.create(unit_id=unit_id, standard_job_title=title)
    position = factories.PositionItemFactory.create(
        proposal=proposal, is_chapter=False, job_title_in_proposal=title, catalog_position_id=cp.id
    )
    member = route_position(db, position_item_id=position.id)
    return member.context_id


def _chain_context(db, factories, *, catalog_position, path_specs) -> tuple[int, list[int]]:
    """Строит ОДИН контекст с несколькими членствами по заданным цепочкам
    разделов. `path_specs` — список `(titles, count)`, `titles` — заголовки
    от КОРНЯ К ЛИСТУ; `titles=()` — членство без раздела вовсе. Одна и та же
    каталожная строка без статьи у всех позиций — маршрутизация кладёт их в
    ОДНУ корзину и, значит, в один контекст по умолчанию (спека §2.1)."""
    proposal = _proposal(factories)
    context_id: int | None = None
    position_ids: list[int] = []
    for idx, (titles, count) in enumerate(path_specs):
        parent_id: int | None = None
        for title in titles:
            chapter = factories.PositionItemFactory.create(
                proposal=proposal, is_chapter=True, job_title_in_proposal=title,
                chapter_item_id=parent_id,
            )
            parent_id = chapter.id
        for j in range(count):
            position = factories.PositionItemFactory.create(
                proposal=proposal, is_chapter=False,
                job_title_in_proposal=f"Позиция {idx}-{j}",
                chapter_item_id=parent_id, catalog_position_id=catalog_position.id,
            )
            member = route_position(db, position_item_id=position.id)
            context_id = member.context_id
            position_ids.append(position.id)
    assert context_id is not None
    return context_id, position_ids


def _settings(**overrides) -> Settings:
    base = dict(SECRET_KEY="x" * 32)
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------------------------
#  Число запросов не растёт с числом контекстов (спека §2.2, решение задачи 2)
# ---------------------------------------------------------------------------

class TestLoadRequestMaterialBatching:
    def test_query_count_does_not_grow_with_context_count(self, db_session, factories):
        proposal = _proposal(factories)
        unit_id = _unit_id(db_session, "M2")
        small_ids = [
            _simple_context(db_session, factories, proposal, unit_id=unit_id, title=f"Работа {i}")
            for i in range(10)
        ]
        large_ids = [
            _simple_context(
                db_session, factories, proposal, unit_id=unit_id, title=f"Работа Б{i}"
            )
            for i in range(60)
        ]

        with _capturing_sql(db_session) as small_statements:
            small_material = load_request_material(db_session, small_ids)
        with _capturing_sql(db_session) as large_statements:
            large_material = load_request_material(db_session, large_ids)

        assert len(small_material) == 10
        assert len(large_material) == 60
        assert len(small_statements) == len(large_statements)

    def test_missing_context_id_omitted_from_result(self, db_session, factories):
        proposal = _proposal(factories)
        unit_id = _unit_id(db_session, "M2")
        real_id = _simple_context(db_session, factories, proposal, unit_id=unit_id, title="Настоящая работа")
        missing_id = real_id + 1_000_000

        material = load_request_material(db_session, [real_id, missing_id])

        assert set(material) == {real_id}

    def test_only_missing_ids_cost_a_single_query(self, db_session, factories):
        """Ни одного найденного контекста — пустой результат после ПЕРВОГО
        запроса; агрегаты и кандидаты не запрашиваются."""
        proposal = _proposal(factories)
        unit_id = _unit_id(db_session, "M2")
        real_id = _simple_context(db_session, factories, proposal, unit_id=unit_id, title="Опорная работа")

        with _capturing_sql(db_session) as statements:
            material = load_request_material(db_session, [real_id + 1_000_000])

        assert material == {}
        assert len(statements) == 1

    def test_query_count_constant_with_chapters_articles_and_mixed_units(self, db_session, factories):
        """Тот же замер, что выше, но на входе, где работает КАЖДЫЙ шаг
        загрузки: у каждого контекста цепочка из двух разделов (вызов
        `chapter_paths`), статья, а единиц в большой партии больше, чем в
        малой, включая «без единицы». У каждой единицы — своя активная семья,
        и каждый контекст обязан получить ровно семью своей единицы."""
        user = factories.UserFactory.create()
        (category_id,) = _leaf_category_ids(db_session, 1)
        proposal = _proposal(factories)
        unit_ids = {code: _unit_id(db_session, code) for code in ("M2", "PCS", "M3")}
        family_by_unit = {
            unit_ids["M2"]: _active_family(db_session, title="Партия м2", unit_name="M2", actor_id=user.id).id,
            unit_ids["PCS"]: _active_family(db_session, title="Партия шт", unit_name="PCS", actor_id=user.id).id,
            unit_ids["M3"]: _active_family(db_session, title="Партия м3", unit_name="M3", actor_id=user.id).id,
            None: _active_family(db_session, title="Партия без единицы", unit_name=None, actor_id=user.id).id,
        }

        def _rich_context(title, unit_id):
            root = factories.PositionItemFactory.create(
                proposal=proposal, is_chapter=True, job_title_in_proposal=f"Корень {title}",
            )
            leaf = factories.PositionItemFactory.create(
                proposal=proposal, is_chapter=True, job_title_in_proposal=f"Лист {title}",
                chapter_item_id=root.id, work_category_id=category_id, category_source="file",
            )
            cp = factories.CatalogPositionFactory.create(unit_id=unit_id, standard_job_title=title)
            position = factories.PositionItemFactory.create(
                proposal=proposal, is_chapter=False, job_title_in_proposal=title,
                chapter_item_id=leaf.id, catalog_position_id=cp.id,
            )
            return route_position(db_session, position_item_id=position.id).context_id, unit_id

        small_units = [unit_ids["M2"], unit_ids["PCS"]]
        large_units = [unit_ids["M2"], unit_ids["PCS"], unit_ids["M3"], None]
        small = dict(_rich_context(f"Малая {i}", small_units[i % 2]) for i in range(10))
        large = dict(_rich_context(f"Большая {i}", large_units[i % 4]) for i in range(60))

        with _capturing_sql(db_session) as small_statements:
            small_material = load_request_material(db_session, list(small))
        with _capturing_sql(db_session) as large_statements:
            large_material = load_request_material(db_session, list(large))

        assert len(small_statements) == len(large_statements)
        for expected_units, material in ((small, small_material), (large, large_material)):
            assert set(material) == set(expected_units)
            for cid, m in material.items():
                assert [c.id for c in m.candidates] == [family_by_unit[expected_units[cid]]]
                assert m.article is not None
                ((path, count),) = m.path_counts
                assert path.startswith("Корень ") and " / Лист " in path and count == 1


# ---------------------------------------------------------------------------
#  Пути разделов: включают STALE, сливают одинаковый текст разных id
# ---------------------------------------------------------------------------

class TestLoadRequestMaterialPaths:
    def test_includes_all_membership_states(self, db_session, factories):
        cp = factories.CatalogPositionFactory.create()
        context_id, position_ids = _chain_context(
            db_session, factories, catalog_position=cp,
            path_specs=[(("Секция 1", "Этаж 2"), 3), (("Секция 2", "Этаж 1"), 2), ((), 1)],
        )
        db_session.execute(
            sa.update(ContextMember)
            .where(ContextMember.position_item_id == position_ids[0])
            .values(membership_state=MembershipState.STALE.value)
        )
        db_session.flush()

        material = load_request_material(db_session, [context_id])[context_id]

        assert material.member_count == 6
        counts = dict(material.path_counts)
        assert counts["Секция 1 / Этаж 2"] == 3
        assert counts["Секция 2 / Этаж 1"] == 2
        assert counts[""] == 1
        assert top_path(material.path_counts) == "Секция 1 / Этаж 2"

    def test_merges_different_chapter_ids_with_identical_path_text(self, db_session, factories):
        """Два НЕЗАВИСИМЫХ раздела с одинаковым текстом (разные строки
        `position_items`, разные `chapter_item_id`) обязаны слиться в ОДНУ
        запись `path_counts` по тексту пути, а не остаться двумя строками с
        count=1 каждая."""
        cp = factories.CatalogPositionFactory.create()
        context_id, _ = _chain_context(
            db_session, factories, catalog_position=cp,
            path_specs=[(("Секция 1", "Этаж 2"), 1), (("Секция 1", "Этаж 2"), 1)],
        )

        material = load_request_material(db_session, [context_id])[context_id]

        assert material.path_counts == (("Секция 1 / Этаж 2", 2),)


# ---------------------------------------------------------------------------
#  Статья — статья корзины контекста (спека §2.2)
# ---------------------------------------------------------------------------

class TestLoadRequestMaterialArticle:
    def test_article_from_bucket_work_category(self, db_session, factories):
        (category_id,) = _leaf_category_ids(db_session, 1)
        category = db_session.get(WorkCategory, category_id)
        proposal = _proposal(factories)
        chapter = factories.PositionItemFactory.create(
            proposal=proposal, is_chapter=True, job_title_in_proposal="Раздел со статьёй",
            work_category_id=category_id, category_source="file",
        )
        cp = factories.CatalogPositionFactory.create()
        position = factories.PositionItemFactory.create(
            proposal=proposal, is_chapter=False, job_title_in_proposal="Работа под статьёй",
            chapter_item_id=chapter.id, catalog_position_id=cp.id,
        )
        member = route_position(db_session, position_item_id=position.id)

        material = load_request_material(db_session, [member.context_id])[member.context_id]

        assert material.article == f"{category.code} {category.title}"

    def test_article_none_without_category(self, db_session, factories):
        proposal = _proposal(factories)
        unit_id = _unit_id(db_session, "M2")
        context_id = _simple_context(db_session, factories, proposal, unit_id=unit_id, title="Без статьи")

        material = load_request_material(db_session, [context_id])[context_id]

        assert material.article is None


# ---------------------------------------------------------------------------
#  Кандидаты — активные семьи ЕДИНИЦЫ контекста (спека §2.2)
# ---------------------------------------------------------------------------

class TestLoadRequestMaterialCandidates:
    def test_candidates_are_active_families_matching_unit(self, db_session, factories):
        user = factories.UserFactory.create()
        proposal = _proposal(factories)
        unit_id = _unit_id(db_session, "M2")
        context_id = _simple_context(db_session, factories, proposal, unit_id=unit_id, title="Штукатурка стен")

        matching = _active_family(db_session, title="Штукатурка", unit_name="M2", actor_id=user.id)
        matching_second = _active_family(db_session, title="Шпатлёвка", unit_name="M2", actor_id=user.id)
        other_unit = _active_family(db_session, title="Устройство перегородок", unit_name="PCS", actor_id=user.id)
        draft = create_family(
            db_session, title="Черновая семья", unit_name="M2", definition="черновик", actor_id=user.id
        )
        archived = _active_family(db_session, title="Архивная семья", unit_name="M2", actor_id=user.id)
        archive_family(db_session, family_id=archived.id, actor_id=user.id)

        material = load_request_material(db_session, [context_id])[context_id]

        candidate_ids = {c.id for c in material.candidates}
        assert matching.id in candidate_ids
        assert other_unit.id not in candidate_ids
        assert draft.id not in candidate_ids
        assert archived.id not in candidate_ids
        # Контракт `ContextRequestMaterial.candidates`: активные семьи единицы по `id`.
        own = [c.id for c in material.candidates if c.id in {matching.id, matching_second.id}]
        assert own == sorted([matching.id, matching_second.id])
        assert [c.id for c in material.candidates] == sorted(candidate_ids)

    def test_candidate_query_fetches_only_units_of_the_batch(self, db_session, factories, monkeypatch):
        """Запрос кандидатов ограничен единицами набора в SQL, а не только
        раскладкой по единицам в памяти: раскладка скрыла бы из результата
        семьи чужих единиц, но выборка всех активных семей каталога на каждое
        событие осталась бы. Наблюдатель — каждая построенная из строки
        выборки `CandidateFamily`."""
        from services import semantic_request

        built: list[int] = []
        real = semantic_request.CandidateFamily

        def _recording(**fields):
            built.append(fields["id"])
            return real(**fields)

        user = factories.UserFactory.create()
        proposal = _proposal(factories)
        unit_id = _unit_id(db_session, "M2")
        context_id = _simple_context(db_session, factories, proposal, unit_id=unit_id, title="Работа выборки")
        own = _active_family(db_session, title="Своя семья выборки", unit_name="M2", actor_id=user.id)
        foreign = _active_family(db_session, title="Чужая семья выборки", unit_name="SET", actor_id=user.id)

        monkeypatch.setattr(semantic_request, "CandidateFamily", _recording)
        load_request_material(db_session, [context_id])

        assert own.id in built
        assert foreign.id not in built

    def test_candidates_for_context_without_unit(self, db_session, factories):
        user = factories.UserFactory.create()
        proposal = _proposal(factories)
        context_id = _simple_context(
            db_session, factories, proposal, unit_id=None, title="Система без единицы"
        )

        no_unit_family = _active_family(
            db_session, title="Система без единицы (семья)", unit_name=None, actor_id=user.id
        )
        m2_family = _active_family(
            db_session, title="Семья с единицей", unit_name="M2", actor_id=user.id
        )

        material = load_request_material(db_session, [context_id])[context_id]

        candidate_ids = {c.id for c in material.candidates}
        assert no_unit_family.id in candidate_ids
        assert m2_family.id not in candidate_ids
        assert material.unit_code is None


# ---------------------------------------------------------------------------
#  is_applicable сквозным путём: реальный контекст → материал
# ---------------------------------------------------------------------------

class TestIsApplicableEndToEnd:
    def test_true_for_ordinary_context_with_active_family(self, db_session, factories):
        from services.semantic_request import is_applicable

        user = factories.UserFactory.create()
        proposal = _proposal(factories)
        unit_id = _unit_id(db_session, "M2")
        context_id = _simple_context(db_session, factories, proposal, unit_id=unit_id, title="Обычная работа")
        _active_family(db_session, title="Обычная семья", unit_name="M2", actor_id=user.id)

        material = load_request_material(db_session, [context_id])[context_id]

        assert is_applicable(material) is True

    def test_false_when_context_marked_not_applicable(self, db_session, factories):
        from services.semantic_request import is_applicable

        user = factories.UserFactory.create()
        proposal = _proposal(factories)
        unit_id = _unit_id(db_session, "M2")
        context_id = _simple_context(db_session, factories, proposal, unit_id=unit_id, title="Помеченная работа")
        _active_family(db_session, title="Ещё семья", unit_name="M2", actor_id=user.id)

        db_session.execute(
            sa.update(CatalogContext)
            .where(CatalogContext.id == context_id)
            .values(semantic_state=SemanticState.NOT_APPLICABLE.value)
        )
        db_session.flush()

        material = load_request_material(db_session, [context_id])[context_id]

        assert is_applicable(material) is False

    def test_material_carries_archived_kind_and_family_columns(self, db_session, factories):
        """Остальные поля предиката — из колонок контекста, каждое на своём
        контексте, отличающемся от контрольного ровно этой колонкой."""
        from services.semantic_request import is_applicable

        user = factories.UserFactory.create()
        proposal = _proposal(factories)
        unit_id = _unit_id(db_session, "M2")
        family = _active_family(db_session, title="Семья колонок", unit_name="M2", actor_id=user.id)
        control, archived, system, with_family = (
            _simple_context(db_session, factories, proposal, unit_id=unit_id, title=f"Колонка {name}")
            for name in ("контроль", "архив", "система", "семья")
        )
        now = sa.func.now()
        for cid, values in (
            (archived, {"archived_at": now}),
            (system, {"semantic_kind": "SYSTEM"}),
            (with_family, {"work_family_id": family.id, "family_source": "suggestion", "family_at": now}),
        ):
            db_session.execute(sa.update(CatalogContext).where(CatalogContext.id == cid).values(**values))
        db_session.flush()

        material = load_request_material(db_session, [control, archived, system, with_family])

        assert is_applicable(material[control]) is True
        assert material[archived].archived is True and material[control].archived is False
        assert material[system].semantic_kind == "SYSTEM" and material[control].semantic_kind != "SYSTEM"
        assert material[with_family].work_family_id == family.id and material[control].work_family_id is None
        # Привязка к семье применимость не отменяет (спека вариантов §2.5).
        assert [is_applicable(material[c]) for c in (archived, system, with_family)] == [
            False, False, True,
        ]


# ---------------------------------------------------------------------------
#  Роль имени контекста не входит в материал — не влияет на отпечаток
# ---------------------------------------------------------------------------

class TestNameRoleIndependence:
    def test_name_role_change_does_not_affect_request_hash(self, db_session, factories):
        """`name_role` — поле контекста, которого нет ни в `ContextRequestMaterial`,
        ни в теле запроса (спека §2.10). Тот же контекст перезагружается
        ПОСЛЕ смены роли — отдельный вход, отличающийся от базового ровно
        этим полем; если бы `load_request_material` вдруг начал читать
        `name_role`, второй хэш разошёлся бы с первым."""
        user = factories.UserFactory.create()
        proposal = _proposal(factories)
        unit_id = _unit_id(db_session, "M2")
        context_id = _simple_context(
            db_session, factories, proposal, unit_id=unit_id, title="Работа с ролью имени"
        )
        _active_family(db_session, title="Семья для роли имени", unit_name="M2", actor_id=user.id)
        settings = _settings()

        material_before = load_request_material(db_session, [context_id])[context_id]
        hash_before = render_context_request(material_before, settings=settings).request_hash

        db_session.execute(
            sa.update(CatalogContext).where(CatalogContext.id == context_id).values(name_role="LOCATION_ONLY")
        )
        db_session.flush()

        material_after = load_request_material(db_session, [context_id])[context_id]
        hash_after = render_context_request(material_after, settings=settings).request_hash

        assert hash_before == hash_after


# ---------------------------------------------------------------------------
#  Рендер от реально загруженного материала — согласован с БД
# ---------------------------------------------------------------------------

class TestRenderFromLoadedMaterial:
    def test_round_trip_title_unit_and_article(self, db_session, factories):
        (category_id,) = _leaf_category_ids(db_session, 1)
        category = db_session.get(WorkCategory, category_id)
        unit_id = _unit_id(db_session, "PCS")
        unit_code = db_session.execute(
            sa.text("SELECT code FROM units_of_measure WHERE id = :id"), {"id": unit_id}
        ).scalar_one()
        user = factories.UserFactory.create()
        proposal = _proposal(factories)
        chapter = factories.PositionItemFactory.create(
            proposal=proposal, is_chapter=True, job_title_in_proposal="Раздел для рендера",
            work_category_id=category_id, category_source="file",
        )
        cp = factories.CatalogPositionFactory.create(unit_id=unit_id, standard_job_title="Заданная работа")
        position = factories.PositionItemFactory.create(
            proposal=proposal, is_chapter=False, job_title_in_proposal="Заданная работа",
            chapter_item_id=chapter.id, catalog_position_id=cp.id,
        )
        member = route_position(db_session, position_item_id=position.id)
        _active_family(db_session, title="Семья для рендера", unit_name="PCS", actor_id=user.id)

        material = load_request_material(db_session, [member.context_id])[member.context_id]
        rendered = render_context_request(material, settings=_settings())
        user_text = rendered.body["messages"][1]["content"]

        assert "Наименование: Заданная работа" in user_text
        assert f"Единица: {unit_code}" in user_text
        assert f"Статья: {category.code} {category.title}" in user_text
        assert "Раздел для рендера" in user_text


# ---------------------------------------------------------------------------
#  Приватность: реальные позиции с ценой/подрядчиком/договором/объектом
# ---------------------------------------------------------------------------

class TestPrivacyExclusion:
    def test_body_excludes_price_quantity_contractor_contract_object(self, db_session, factories):
        user = factories.UserFactory.create()
        unit_id = _unit_id(db_session, "M2")

        contractor = factories.ContractorFactory.create(title="Подрядчик МАРКЕР Ромашка")
        obj = factories.ObjectFactory.create(title="Объект МАРКЕР Алматытауэнерго")
        contract = factories.ContractFactory.create(
            contractor=contractor, object=obj, contract_number="ГП-МАРКЕР-0042"
        )
        estimate = factories.EstimateFactory.create(contract=contract)
        lot = factories.LotFactory.create(estimate=estimate)
        proposal = factories.ProposalFactory.create(lot=lot, contractor=contractor)
        cp = factories.CatalogPositionFactory.create(unit_id=unit_id, standard_job_title="Работа с деньгами")
        position = factories.PositionItemFactory.create(
            proposal=proposal, is_chapter=False, job_title_in_proposal="Работа с деньгами",
            catalog_position_id=cp.id,
            unit_cost_total=Decimal("123456.78"), quantity=Decimal("73519.25"),
            suggested_quantity=Decimal("81642.75"), total_cost_total=Decimal("95884341.06"),
        )
        member = route_position(db_session, position_item_id=position.id)
        _active_family(db_session, title="Семья для приватности", unit_name="M2", actor_id=user.id)

        material = load_request_material(db_session, [member.context_id])[member.context_id]
        rendered = render_context_request(material, settings=_settings())
        serialized = json.dumps(rendered.body, ensure_ascii=False)

        # Числа — пятизначные и выше, с дробью: короткое «777» находилось бы в
        # любом `id` семьи блока кандидатов (ложная краснота) и не отличало бы
        # утечку от совпадения.
        leaked = [
            marker
            for marker in (
                "Ромашка", "Алматытауэнерго", "ГП-МАРКЕР-0042",
                "123456.78", "123456,78", "73519", "81642", "95884341",
            )
            if marker in serialized
        ]
        assert leaked == []
