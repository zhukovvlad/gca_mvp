"""Охват единицы и тело запроса открытия семей на базе (спека 3б §2.3, DoD 2, 3).

Охват считает один предикат: `discovery_scope`, `is_in_scope` и рендер тела
берут контексты из одного запроса. Каждое условие предиката — отдельный вход:
контрольный контекст проходит, соседний, нарушающий ровно это условие, нет."""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest
import sqlalchemy as sa

from config import settings as app_settings
from models import CatalogContext, FamilyCategory, WorkCategory, WorkFamily
from services.context_routing import route_position
from services.family_discovery import (
    DiscoveryScope,
    ScopeCounts,
    discovery_scope,
    is_in_scope,
    render_discovery_request,
)
from services.semantic_privacy import build_privacy_dictionary, find_privacy_matches
from services.semantic_request import FAMILY_BLOCK_HEADER, load_request_material
from services.work_families import assign_family
from tests.factories import seed_category_id
from tests.integration.test_semantic_queue_api import (
    _active_family,
    _proposal,
    _published,
    _unit_id,
)

pytestmark = pytest.mark.integration

S = app_settings


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _leaf_categories(db, count: int) -> list[int]:
    used_as_parent = sa.select(WorkCategory.parent_id).where(WorkCategory.parent_id.is_not(None))
    ids = (
        db.execute(
            sa.select(WorkCategory.id)
            .where(WorkCategory.id.not_in(used_as_parent))
            .order_by(WorkCategory.sort_order)
            .limit(count)
        )
        .scalars()
        .all()
    )
    assert len(ids) == count, "вход теста: в классификаторе мало листьев"
    return list(ids)


def _make(
    db, factories, proposal, unit_id, title, *, cp=None, cp_kind="POSITION", article_id=None,
    chapters=(), position_values=None,
):
    """Контекст по каталожной строке; `cp` — повторное использование строки
    (тот же заголовок в другой статье); `chapters` — пути разделов сверху вниз,
    `article_id` — статья на листовом разделе. Возвращает `(context_id, cp)`."""
    parent_id = None
    for chapter_title in chapters:
        chapter = factories.PositionItemFactory.create(
            proposal=proposal, is_chapter=True, job_title_in_proposal=chapter_title,
            chapter_item_id=parent_id,
        )
        parent_id = chapter.id
    if article_id is not None:
        leaf = factories.PositionItemFactory.create(
            proposal=proposal, is_chapter=True, job_title_in_proposal=f"Статья {article_id}",
            chapter_item_id=parent_id, work_category_id=article_id, category_source="file",
        )
        parent_id = leaf.id
    if cp is None:
        cp = factories.CatalogPositionFactory.create(
            unit_id=unit_id, standard_job_title=title, kind=cp_kind
        )
    position = factories.PositionItemFactory.create(
        proposal=proposal, is_chapter=False, job_title_in_proposal=title,
        chapter_item_id=parent_id, catalog_position_id=cp.id, **(position_values or {}),
    )
    return route_position(db, position_item_id=position.id).context_id, cp


def _set(db, context_id, **values):
    db.execute(sa.update(CatalogContext).where(CatalogContext.id == context_id).values(**values))
    db.expire_all()


def _make_system(db, context_id, user_id):
    _set(
        db, context_id, semantic_kind="SYSTEM", semantic_kind_source="manual",
        semantic_kind_by=user_id, semantic_kind_at=dt.datetime.now(dt.UTC),
    )


def _uncategorize(db, family_id):
    db.execute(sa.update(WorkFamily).where(WorkFamily.id == family_id).values(family_category_id=None))
    db.expire_all()


def _agree(db, unit_id, context_id) -> bool:
    """Контекст в охвате единицы — и один и тот же ответ у `is_in_scope`."""
    in_scope = context_id in discovery_scope(db, unit_id).context_ids
    assert (context_id in is_in_scope(db, [context_id])) == in_scope
    return in_scope


class World:
    """Единица с активной семьёй (`family_unit`) и единица без семей (`bare_unit`)."""


@pytest.fixture
def world(db_session, factories, admin_user):
    w = World()
    w.db = db_session
    w.factories = factories
    w.admin = admin_user
    w.family_unit = _unit_id(db_session, "M2")
    w.bare_unit = _unit_id(db_session, "M3")
    w.family = _active_family(
        db_session, title="Семья пола", unit_name="M2", actor_id=admin_user.id
    )
    w.proposal = _proposal(factories)
    return w


def _ctx_family_unit(w, title, **kw):
    return _make(w.db, w.factories, w.proposal, w.family_unit, title, **kw)[0]


def _ctx_bare_unit(w, title, **kw):
    return _make(w.db, w.factories, w.proposal, w.bare_unit, title, **kw)[0]


# ---------------------------------------------------------------------------
#  Предикат охвата: условие за условием
# ---------------------------------------------------------------------------

class TestScopePredicate:
    def test_bare_context_in_a_unit_without_families_is_in_scope(self, world):
        c = _ctx_bare_unit(world, "Голая единица")

        assert _agree(world.db, world.bare_unit, c)

    def test_work_without_suggestion_in_a_unit_with_families_is_out(self, world):
        c = _ctx_family_unit(world, "Работа без предложения")

        assert not _agree(world.db, world.family_unit, c)

    def test_archived_context_is_out(self, world):
        keep = _ctx_bare_unit(world, "Живой")
        gone = _ctx_bare_unit(world, "Архивный")
        _set(world.db, gone, archived_at=dt.datetime.now(dt.UTC))

        assert _agree(world.db, world.bare_unit, keep)
        assert not _agree(world.db, world.bare_unit, gone)

    def test_context_without_membership_is_out(self, world):
        keep = _ctx_bare_unit(world, "С членом")
        orphan = _ctx_bare_unit(world, "Без членов")
        world.db.execute(sa.text("DELETE FROM context_members WHERE context_id = :c"), {"c": orphan})
        world.db.expire_all()

        assert _agree(world.db, world.bare_unit, keep)
        assert not _agree(world.db, world.bare_unit, orphan)

    def test_not_applicable_context_is_out(self, world):
        keep = _ctx_bare_unit(world, "Применимый")
        skipped = _ctx_bare_unit(world, "Неприменимый")
        _set(world.db, skipped, semantic_state="NOT_APPLICABLE")

        assert _agree(world.db, world.bare_unit, keep)
        assert not _agree(world.db, world.bare_unit, skipped)

    def test_context_with_a_family_is_out(self, world):
        keep = _ctx_family_unit(world, "Система без семьи")
        assigned = _ctx_family_unit(world, "Система с семьёй")
        _make_system(world.db, keep, world.admin.id)
        _make_system(world.db, assigned, world.admin.id)
        assign_family(
            world.db, context_id=assigned, family_id=world.family.id, actor_id=world.admin.id
        )

        assert _agree(world.db, world.family_unit, keep)
        assert not _agree(world.db, world.family_unit, assigned)

    def test_context_with_a_pending_family_is_out(self, world):
        keep = _ctx_family_unit(world, "Система свободная")
        waiting = _ctx_family_unit(world, "Система в ожидании")
        _make_system(world.db, keep, world.admin.id)
        _make_system(world.db, waiting, world.admin.id)
        _set(
            world.db, waiting, pending_family_id=world.family.id,
            pending_family_source="manual", pending_by=world.admin.id,
            pending_at=dt.datetime.now(dt.UTC),
        )

        assert _agree(world.db, world.family_unit, keep)
        assert not _agree(world.db, world.family_unit, waiting)

    @pytest.mark.parametrize("kind", ["HEADER", "LOT_HEADER", "TRASH"])
    def test_catalog_row_that_is_not_a_work_is_out(self, world, kind):
        keep = _ctx_bare_unit(world, "Работа-строка")
        other = _ctx_bare_unit(world, f"Строка {kind}", cp_kind=kind)
        # Роутер сам помечает такие контексты `NOT_APPLICABLE`; условие «вид
        # строки каталога» проверяется отдельно от него.
        _set(world.db, other, semantic_state="SUGGESTED")

        assert _agree(world.db, world.bare_unit, keep)
        assert not _agree(world.db, world.bare_unit, other)

    def test_to_review_and_position_rows_are_both_in(self, world):
        position = _ctx_bare_unit(world, "Строка POSITION", cp_kind="POSITION")
        review = _ctx_bare_unit(world, "Строка TO_REVIEW", cp_kind="TO_REVIEW")

        assert _agree(world.db, world.bare_unit, position)
        assert _agree(world.db, world.bare_unit, review)

    def test_published_suggestion_of_an_existing_family_takes_the_context_out(self, world):
        keep = _ctx_family_unit(world, "Система без предложения")
        suggested = _ctx_family_unit(world, "Система с предложением")
        _make_system(world.db, keep, world.admin.id)
        _make_system(world.db, suggested, world.admin.id)
        _published(world.db, suggested, family_id=world.family.id)

        assert _agree(world.db, world.family_unit, keep)
        assert not _agree(world.db, world.family_unit, suggested)

    def test_decided_suggestion_of_an_existing_family_does_not_take_it_out(self, world):
        c = _ctx_family_unit(world, "Система, предложение отклонено")
        _make_system(world.db, c, world.admin.id)
        _published(
            world.db, c, family_id=world.family.id, decision="rejected",
            decided_by=world.admin.id,
        )

        assert _agree(world.db, world.family_unit, c)

    def test_unpublished_suggestion_of_an_existing_family_does_not_take_it_out(self, world):
        c = _ctx_family_unit(world, "Система, предложение снято")
        _make_system(world.db, c, world.admin.id)
        _published(world.db, c, family_id=world.family.id, is_published=False)

        assert _agree(world.db, world.family_unit, c)

    def test_system_is_in_scope_even_in_a_unit_with_families(self, world):
        work = _ctx_family_unit(world, "Работа")
        system = _ctx_family_unit(world, "Система")
        _make_system(world.db, system, world.admin.id)

        assert not _agree(world.db, world.family_unit, work)
        assert _agree(world.db, world.family_unit, system)

    def test_pending_new_family_suggestion_puts_a_work_into_scope(self, world):
        plain = _ctx_family_unit(world, "Работа простая")
        new = _ctx_family_unit(world, "Работа с новой семьёй")
        _published(world.db, new, family_id=None, new_family_name="Кладка")

        assert not _agree(world.db, world.family_unit, plain)
        assert _agree(world.db, world.family_unit, new)

    def test_model_answer_system_on_a_work_is_in_scope(self, world):
        c = _ctx_family_unit(world, "Тепловой пункт")
        _published(world.db, c, family_id=None, new_family_name="СИСТЕМА")

        assert _agree(world.db, world.family_unit, c)

    def test_decided_new_family_suggestion_does_not_put_a_work_into_scope(self, world):
        c = _ctx_family_unit(world, "Работа, новая семья отклонена")
        _published(
            world.db, c, family_id=None, new_family_name="Кладка", decision="rejected",
            decided_by=world.admin.id,
        )

        assert not _agree(world.db, world.family_unit, c)

    def test_unpublished_new_family_suggestion_does_not_put_a_work_into_scope(self, world):
        c = _ctx_family_unit(world, "Работа, новая семья снята")
        _published(world.db, c, family_id=None, new_family_name="Кладка", is_published=False)

        assert not _agree(world.db, world.family_unit, c)

    def test_unit_without_active_family_is_bare_but_archived_family_does_not_count(self, world):
        c = _ctx_family_unit(world, "Работа единицы с семьёй")
        assert not _agree(world.db, world.family_unit, c)

        world.db.execute(
            sa.update(WorkFamily).where(WorkFamily.id == world.family.id).values(
                status="archived", archived_at=dt.datetime.now(dt.UTC),
            )
        )
        world.db.expire_all()

        assert _agree(world.db, world.family_unit, c)

    def test_draft_family_does_not_count_as_active(self, world, factories):
        from services.work_families import create_family

        create_family(
            world.db, title="Черновик семьи", unit_name="M3", definition="Определение",
            actor_id=world.admin.id, family_category_id=seed_category_id(world.db),
        )
        c = _ctx_bare_unit(world, "Работа единицы с черновиком")

        assert _agree(world.db, world.bare_unit, c)

    def test_broken_chapter_path_does_not_change_the_scope(self, world):
        keep = _ctx_bare_unit(world, "Путь целый")
        broken = _ctx_bare_unit(world, "Путь сломан", chapters=("Раздел А", "Раздел Б"))
        rows = world.db.execute(
            sa.text(
                "SELECT pi.chapter_item_id AS leaf, parent.id AS root "
                "FROM context_members m JOIN position_items pi ON pi.id = m.position_item_id "
                "JOIN position_items parent ON parent.proposal_id = pi.proposal_id "
                "AND parent.is_chapter AND parent.chapter_item_id IS NULL "
                "WHERE m.context_id = :c"
            ),
            {"c": broken},
        ).one()
        world.db.execute(
            sa.text("UPDATE position_items SET chapter_item_id = :leaf WHERE id = :root"),
            {"leaf": rows.leaf, "root": rows.root},
        )
        world.db.expire_all()
        assert load_request_material(world.db, [broken])[broken].path_broken

        assert _agree(world.db, world.bare_unit, keep)
        assert _agree(world.db, world.bare_unit, broken)

    def test_other_units_contexts_are_not_in_the_scope_of_this_unit(self, world):
        bare = _ctx_bare_unit(world, "Из голой единицы")
        system = _ctx_family_unit(world, "Из единицы с семьёй")
        _make_system(world.db, system, world.admin.id)

        assert discovery_scope(world.db, world.bare_unit).context_ids == frozenset({bare})
        assert discovery_scope(world.db, world.family_unit).context_ids == frozenset({system})

    def test_unit_none_is_a_legal_unit(self, world, factories):
        c, _ = _make(world.db, factories, world.proposal, None, "Без единицы")
        other = _ctx_bare_unit(world, "С единицей")

        scope = discovery_scope(world.db, None)

        assert scope.unit_id is None and scope.unit_code is None
        assert scope.context_ids == frozenset({c})
        assert _agree(world.db, None, c)
        assert other not in scope.context_ids

    def test_active_family_without_unit_takes_the_null_unit_out_of_bare(self, world, factories):
        """Ревью задачи 3: единица «без единицы» — обычная единица и для условия
        «в единице нет активной семьи», и для перечня активных семей охвата
        (сравнение единиц NULL-безопасное)."""
        c, _ = _make(world.db, factories, world.proposal, None, "Работа без единицы")
        assert _agree(world.db, None, c)

        family = _active_family(
            world.db, title="Семья без единицы", unit_name=None, actor_id=world.admin.id
        )

        assert family.unit_id is None, "вход теста: семья без единицы"
        assert not _agree(world.db, None, c)
        assert discovery_scope(world.db, None).active_family_ids == (family.id,)

    def test_is_in_scope_filters_a_mixed_unit_set_and_ignores_unknown_ids(self, world):
        a = _ctx_bare_unit(world, "Голая")
        b = _ctx_family_unit(world, "Работа")

        assert is_in_scope(world.db, [a, b, 999_999_999]) == frozenset({a})
        assert is_in_scope(world.db, []) == frozenset()


# ---------------------------------------------------------------------------
#  Один предикат на охват, блок и тело
# ---------------------------------------------------------------------------

class TestOnePredicate:
    def test_scope_is_in_scope_and_render_agree_on_one_fixture(self, world, factories):
        cats = _leaf_categories(world.db, 2)
        kept = [
            _ctx_bare_unit(world, "Голая А", article_id=cats[0]),
            _ctx_bare_unit(world, "Голая Б", article_id=cats[1]),
        ]
        dropped = [
            _ctx_bare_unit(world, "Заголовок", cp_kind="HEADER"),
            _ctx_bare_unit(world, "Архив"),
            _ctx_bare_unit(world, "Неприменим"),
        ]
        _set(world.db, dropped[0], semantic_state="SUGGESTED")
        _set(world.db, dropped[1], archived_at=dt.datetime.now(dt.UTC))
        _set(world.db, dropped[2], semantic_state="NOT_APPLICABLE")
        other_unit = _ctx_family_unit(world, "Работа другой единицы")

        scope = discovery_scope(world.db, world.bare_unit)
        everyone = [*kept, *dropped, other_unit]
        by_predicate = is_in_scope(world.db, everyone)
        body_user = render_discovery_request(scope, world.db, settings=S).body["messages"][1][
            "content"
        ]
        titles_in_body = {
            line.split(". ", 1)[1].split(" | ")[0]
            for line in body_user.split("ИМЕНА:\n", 1)[1].split("\n")
        }

        assert scope.context_ids == frozenset(kept)
        assert by_predicate == frozenset(kept)
        assert titles_in_body == {"Голая А", "Голая Б"}
        assert {c for n in scope.names for c in n.context_ids} == scope.context_ids


# ---------------------------------------------------------------------------
#  Имена: нумерация, группы, статьи, путь
# ---------------------------------------------------------------------------

class TestNames:
    def test_names_are_numbered_by_bytewise_sort_of_the_title(self, world):
        for title in ("б", "Я", "a", "Ё"):
            _ctx_bare_unit(world, title)

        names = discovery_scope(world.db, world.bare_unit).names

        assert [(n.index, n.title) for n in names] == [
            (1, "a"), (2, "Ё"), (3, "Я"), (4, "б"),
        ]

    def test_one_title_in_two_articles_is_one_name_with_both_contexts(self, world, factories):
        cats = _leaf_categories(world.db, 2)
        first, cp = _make(
            world.db, factories, world.proposal, world.bare_unit, "Общее имя", article_id=cats[0]
        )
        second, _ = _make(
            world.db, factories, world.proposal, world.bare_unit, "Общее имя", cp=cp,
            article_id=cats[1],
        )
        assert first != second, "вход теста: две статьи — два контекста"

        (name,) = discovery_scope(world.db, world.bare_unit).names

        assert name.title == "Общее имя"
        assert set(name.context_ids) == {first, second}
        assert len(name.articles) == 2

    def test_article_is_code_and_title_of_the_classifier_row(self, world):
        (cat,) = _leaf_categories(world.db, 1)
        _ctx_bare_unit(world, "Со статьёй", article_id=cat)
        code, title = world.db.execute(
            sa.select(WorkCategory.code, WorkCategory.title).where(WorkCategory.id == cat)
        ).one()

        (name,) = discovery_scope(world.db, world.bare_unit).names

        assert name.articles == (f"{code} {title}",)

    def test_name_without_article_has_no_articles(self, world):
        _ctx_bare_unit(world, "Без статьи")

        (name,) = discovery_scope(world.db, world.bare_unit).names

        assert name.articles == ()

    def test_at_most_three_articles_per_name_ties_broken_by_title(self, world, factories):
        cats = _leaf_categories(world.db, 4)
        cp = None
        for cat in cats:
            _, cp = _make(
                world.db, factories, world.proposal, world.bare_unit, "Четыре статьи", cp=cp,
                article_id=cat,
            )

        (name,) = discovery_scope(world.db, world.bare_unit).names

        assert len(name.context_ids) == 4
        assert len(name.articles) == 3
        assert list(name.articles) == sorted(name.articles)

    def test_path_is_the_most_frequent_chapter_path_of_the_members(self, world):
        _ctx_bare_unit(world, "С путём", chapters=("Корень", "Лист"))

        (name,) = discovery_scope(world.db, world.bare_unit).names

        assert name.path == "Корень / Лист"

    def test_path_of_a_name_over_two_contexts_counts_members_of_both(self, world, factories):
        cats = _leaf_categories(world.db, 2)
        first, cp = _make(
            world.db, factories, world.proposal, world.bare_unit, "Два контекста",
            article_id=cats[0], chapters=("А-раздел",),
        )
        second, _ = _make(
            world.db, factories, world.proposal, world.bare_unit, "Два контекста", cp=cp,
            article_id=cats[1], chapters=("Я-раздел",),
        )
        leaf = world.db.execute(
            sa.text(
                "SELECT pi.chapter_item_id FROM context_members m "
                "JOIN position_items pi ON pi.id = m.position_item_id WHERE m.context_id = :c"
            ),
            {"c": second},
        ).scalar_one()
        extra = factories.PositionItemFactory.create(
            proposal=world.proposal, is_chapter=False, job_title_in_proposal="Два контекста",
            chapter_item_id=leaf, catalog_position_id=cp.id,
        )
        assert route_position(world.db, position_item_id=extra.id).context_id == second

        (name,) = discovery_scope(world.db, world.bare_unit).names

        assert set(name.context_ids) == {first, second}
        assert name.path.startswith("Я-раздел /")
        assert "А-раздел" not in name.path

    def test_name_without_chapters_has_empty_path(self, world):
        _ctx_bare_unit(world, "Без разделов")

        (name,) = discovery_scope(world.db, world.bare_unit).names

        assert name.path == ""

    def test_path_is_cut_to_two_hundred_characters(self, world):
        _ctx_bare_unit(world, "Длинный путь", chapters=("Я" * 150, "Ю" * 150))

        (name,) = discovery_scope(world.db, world.bare_unit).names

        assert len(name.path) <= 200
        assert name.path.startswith("Я" * 150)

    def test_broken_path_gives_an_empty_path_not_an_error(self, world):
        c = _ctx_bare_unit(world, "Сломанный путь", chapters=("Раздел А", "Раздел Б"))
        rows = world.db.execute(
            sa.text(
                "SELECT pi.chapter_item_id AS leaf, parent.id AS root "
                "FROM context_members m JOIN position_items pi ON pi.id = m.position_item_id "
                "JOIN position_items parent ON parent.proposal_id = pi.proposal_id "
                "AND parent.is_chapter AND parent.chapter_item_id IS NULL "
                "WHERE m.context_id = :c"
            ),
            {"c": c},
        ).one()
        world.db.execute(
            sa.text("UPDATE position_items SET chapter_item_id = :leaf WHERE id = :root"),
            {"leaf": rows.leaf, "root": rows.root},
        )
        world.db.expire_all()
        assert load_request_material(world.db, [c])[c].path_broken

        (name,) = discovery_scope(world.db, world.bare_unit).names

        assert name.path == ""


# ---------------------------------------------------------------------------
#  Охват: справочник и семьи единицы, счётчики
# ---------------------------------------------------------------------------

class TestScopeMaterial:
    def test_active_families_of_the_unit_by_id_and_nothing_else(self, world):
        second = _active_family(
            world.db, title="Вторая", unit_name="M2", actor_id=world.admin.id
        )
        _active_family(world.db, title="Чужая", unit_name="M3", actor_id=world.admin.id)
        _ctx_family_unit(world, "Работа")

        scope = discovery_scope(world.db, world.family_unit)

        assert scope.active_family_ids == (world.family.id, second.id)

    def test_uncategorized_families_are_listed_separately(self, world):
        second = _active_family(
            world.db, title="Вторая", unit_name="M2", actor_id=world.admin.id
        )
        _uncategorize(world.db, second.id)

        scope = discovery_scope(world.db, world.family_unit)

        assert scope.uncategorized_family_ids == (second.id,)
        assert scope.counts.uncategorized_families == 1

    def test_category_ids_are_the_whole_reference_by_id(self, world):
        scope = discovery_scope(world.db, world.family_unit)

        expected = world.db.execute(
            sa.select(FamilyCategory.id).order_by(FamilyCategory.id)
        ).scalars().all()
        assert scope.category_ids == tuple(expected)
        assert len(expected) >= 3

    def test_unit_code_comes_from_the_unit(self, world):
        scope = discovery_scope(world.db, world.family_unit)

        assert scope.unit_id == world.family_unit
        assert scope.unit_code == world.db.execute(
            sa.text("SELECT code FROM units_of_measure WHERE id = :u"), {"u": world.family_unit}
        ).scalar_one()

    def test_counts_split_systems_new_family_and_bare(self, world):
        system = _ctx_family_unit(world, "Система")
        _make_system(world.db, system, world.admin.id)
        new = _ctx_family_unit(world, "Новая")
        _published(world.db, new, family_id=None, new_family_name="Кладка")
        system_with_new = _ctx_family_unit(world, "Система с новой")
        _make_system(world.db, system_with_new, world.admin.id)
        _published(world.db, system_with_new, family_id=None, new_family_name="СИСТЕМА")

        counts = discovery_scope(world.db, world.family_unit).counts

        assert counts == ScopeCounts(
            systems=2, new_family=1, bare=0, names=3, uncategorized_families=0
        )

    def test_counts_bare_in_a_unit_without_families(self, world):
        _ctx_bare_unit(world, "Голая А")
        _ctx_bare_unit(world, "Голая Б")
        system = _ctx_bare_unit(world, "Система")
        _make_system(world.db, system, world.admin.id)

        counts = discovery_scope(world.db, world.bare_unit).counts

        assert (counts.systems, counts.new_family, counts.bare, counts.names) == (1, 0, 2, 3)

    def test_empty_scope_with_uncategorized_families_still_has_material(self, world):
        _uncategorize(world.db, world.family.id)

        scope = discovery_scope(world.db, world.family_unit)

        assert scope.names == () and scope.context_ids == frozenset()
        assert scope.uncategorized_family_ids == (world.family.id,)

    def test_scope_is_a_frozen_dataclass_with_tuple_names(self, world):
        scope = discovery_scope(world.db, world.family_unit)

        assert isinstance(scope, DiscoveryScope)
        assert isinstance(scope.names, tuple)
        assert isinstance(scope.context_ids, frozenset)


# ---------------------------------------------------------------------------
#  Тело на базе
# ---------------------------------------------------------------------------

def _render(db, unit_id):
    return render_discovery_request(discovery_scope(db, unit_id), db, settings=S)


class TestRenderOnTheDatabase:
    def test_same_state_same_hash(self, world):
        _ctx_bare_unit(world, "Голая")

        assert _render(world.db, world.bare_unit) == _render(world.db, world.bare_unit)

    def test_each_state_axis_changes_the_hash(self, world, factories):
        cats = _leaf_categories(world.db, 2)
        base_ctx, cp = _make(
            world.db, factories, world.proposal, world.bare_unit, "Голая", article_id=cats[0]
        )
        family = _active_family(world.db, title="Для оси", unit_name="M3", actor_id=world.admin.id)
        # Единица теперь с семьёй: `base_ctx` вне охвата без системы — делаем системой.
        _make_system(world.db, base_ctx, world.admin.id)
        seen = {_render(world.db, world.bare_unit).request_hash}

        def changed():
            h = _render(world.db, world.bare_unit).request_hash
            assert h not in seen
            seen.add(h)

        # Новый контекст в охвате.
        extra, _ = _make(world.db, factories, world.proposal, world.bare_unit, "Ещё система")
        _make_system(world.db, extra, world.admin.id)
        changed()
        # Статья имени.
        second, _ = _make(
            world.db, factories, world.proposal, world.bare_unit, "Голая", cp=cp,
            article_id=cats[1],
        )
        _make_system(world.db, second, world.admin.id)
        changed()
        # Определение активной семьи.
        world.db.execute(
            sa.update(WorkFamily).where(WorkFamily.id == family.id).values(definition="Иначе")
        )
        world.db.expire_all()
        changed()
        # Имя активной семьи.
        world.db.execute(
            sa.update(WorkFamily).where(WorkFamily.id == family.id).values(title="Иное имя")
        )
        world.db.expire_all()
        changed()
        # Категория справочника: имя, затем определение.
        work_category = seed_category_id(world.db)
        world.db.execute(
            sa.update(FamilyCategory).where(FamilyCategory.id == work_category).values(
                title="Работа иначе"
            )
        )
        world.db.expire_all()
        changed()
        world.db.execute(
            sa.update(FamilyCategory).where(FamilyCategory.id == work_category).values(
                definition="Иное определение"
            )
        )
        world.db.expire_all()
        changed()
        # Семья без категории.
        _uncategorize(world.db, family.id)
        changed()

    def test_system_blocks_carry_categories_and_active_families(self, world):
        _ctx_bare_unit(world, "Голая")
        # единица без семей: блок семей пуст, но заголовок на месте
        blocks = _render(world.db, world.bare_unit).body["messages"][0]["content"]

        assert blocks[1]["text"].startswith("КАТЕГОРИИ СЕМЕЙ:\n")
        assert blocks[2]["text"].startswith(FAMILY_BLOCK_HEADER)

    def test_categories_are_the_seeded_reference_lines(self, world):
        _ctx_bare_unit(world, "Голая")
        rows = world.db.execute(
            sa.select(FamilyCategory.id, FamilyCategory.title, FamilyCategory.definition).order_by(
                FamilyCategory.id
            )
        ).all()

        text = _render(world.db, world.bare_unit).body["messages"][0]["content"][1]["text"]

        assert text == "КАТЕГОРИИ СЕМЕЙ:\n" + "\n".join(
            f"{r.id}. {r.title} — {r.definition}" for r in rows
        )

    def test_active_families_of_the_unit_are_family_lines(self, world):
        c = _ctx_family_unit(world, "Система")
        _make_system(world.db, c, world.admin.id)

        text = _render(world.db, world.family_unit).body["messages"][0]["content"][2]["text"]

        assert text.startswith(FAMILY_BLOCK_HEADER)
        assert text.splitlines()[1].startswith(f"{world.family.id}. Семья пола [")

    def test_uncategorized_family_ids_reach_the_user_text(self, world):
        c = _ctx_family_unit(world, "Система")
        _make_system(world.db, c, world.admin.id)
        _uncategorize(world.db, world.family.id)

        user = _render(world.db, world.family_unit).body["messages"][1]["content"]

        assert f"СЕМЬИ БЕЗ КАТЕГОРИИ: {world.family.id}\n" in user

    def test_empty_scope_renders_a_valid_body_for_a_categories_only_launch(self, world):
        _uncategorize(world.db, world.family.id)

        user = _render(world.db, world.family_unit).body["messages"][1]["content"]

        assert user.endswith("ИМЕНА:\n")

    def test_unit_none_renders(self, world, factories):
        c, _ = _make(world.db, factories, world.proposal, None, "Без единицы")

        rendered = _render(world.db, None)

        assert "ЕДИНИЦА: без единицы" in rendered.body["messages"][1]["content"]
        assert "1. Без единицы | " in rendered.body["messages"][1]["content"]
        assert c in discovery_scope(world.db, None).context_ids


# ---------------------------------------------------------------------------
#  Приватность и состав тела
# ---------------------------------------------------------------------------

class TestBodyContentOnTheDatabase:
    def test_no_price_volume_contractor_or_object_value_reaches_the_body(
        self, world, factories
    ):
        values = {
            "quantity": Decimal("31337.5"),
            "suggested_quantity": Decimal("27182.8"),
            "unit_cost_total": Decimal("424242.42"),
            "total_cost_total": Decimal("161803.39"),
            "unit_cost_materials": Decimal("999111.25"),
            "comment_contractor": "комментарий-подрядчика-секрет",
            "comment_organizer": "комментарий-заказчика-секрет",
        }
        _make(
            world.db, factories, world.proposal, world.bare_unit, "Чистая работа",
            position_values=values,
        )
        contractor = world.proposal.contractor
        contract = world.proposal.lot.estimate.contract
        construction_object = contract.object

        body = json.dumps(_render(world.db, world.bare_unit).body, ensure_ascii=False)

        # Ревью задачи 3: вход теста — строка с заполненными значениями реально в
        # теле (иначе «ничего не утекло» верно на пустом охвате).
        assert "1. Чистая работа | " in body
        for leaked in (
            "31337", "27182", "424242", "161803", "999111", "секрет",
            contractor.title, contractor.inn, contract.contract_number, contract.title,
            construction_object.title, construction_object.address,
            world.proposal.lot.lot_title,
        ):
            assert leaked and leaked not in body, leaked

    def test_privacy_search_covers_the_whole_body_and_attributes_places(self, world, factories):
        contractor = factories.ContractorFactory.create(title="Ромашка")
        ctx, _ = _make(world.db, factories, world.proposal, world.bare_unit, "Кладка у Ромашка")
        _active_family(
            world.db, title="Семья Ромашка", unit_name="M3", actor_id=world.admin.id
        )
        # Единица теперь с семьёй: контекст попадёт в охват как система.
        assert contractor.id is not None
        _make_system(world.db, ctx, world.admin.id)

        found = find_privacy_matches(
            build_privacy_dictionary(world.db), _render(world.db, world.bare_unit)
        )

        places = sorted((m.text, m.where) for m in found)
        assert [p for p in places if p[0] == "ромашка"], places
        assert ("ромашка", "context") in places
        assert any(where.startswith("family:") for _text, where in places)
