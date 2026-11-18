"""Запросы экранов 4а/4б, переписанные ради скорости: результат прежний.

Тесты характеризуют поведение и зелёные и на старой форме запроса, и на новой.
Сцены строятся помощниками `World`; названия выдуманы.
"""

from __future__ import annotations

import contextlib

import pytest
import sqlalchemy as sa

from crud.our_price_load import _conflicted_parameters
from models import ContextParameterValue
from services.context_routing import ChapterStep, chapter_steps
from tests.integration.test_our_price_load import World

pytestmark = pytest.mark.integration


@pytest.fixture
def world(db_session, factories, admin_user):
    return World(db_session, factories, admin_user)


@contextlib.contextmanager
def _capturing_sql(session):
    """Перехватывает каждый SQL-текст сессии через `before_cursor_execute`; точки
    сохранения тестовой обёртки не считаются."""
    statements: list[str] = []

    def _listener(conn, cursor, statement, parameters, context, executemany):
        if not statement.startswith(("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")):
            statements.append(statement)

    connection = session.connection()
    sa.event.listen(connection, "before_cursor_execute", _listener)
    try:
        yield statements
    finally:
        sa.event.remove(connection, "before_cursor_execute", _listener)


def _record(world, context_id, schema, parameter, *, source="path_conflict"):
    # Источник `name` требует значения из списка (check-ограничение), `path_conflict` — без значения.
    value_id = world.value(parameter, "значение") if source == "name" else None
    world.db.add(
        ContextParameterValue(
            context_id=context_id,
            schema_id=schema.id,
            parameter_id=parameter.id,
            value_id=value_id,
            source=source,
        )
    )
    world.db.flush()


def _context(world, proposal, family):
    return world.line(proposal, family=family).context_id


class TestConflictedParameters:
    def test_only_main_active_path_conflict_of_requested_context(self, world):
        _, proposal = world.estimate(world.contract())
        family, schema = world.family()
        main = world.param(schema, 1, "Параметр 1")
        secondary = world.param(schema, 2, "Параметр 2", role="secondary")
        archived = world.param(schema, 3, "Параметр 3", archived=True)
        named = world.param(schema, 4, "Параметр 4")
        ctx = _context(world, proposal, family)
        other = _context(world, proposal, family)
        _record(world, ctx, schema, main)
        _record(world, ctx, schema, secondary)
        _record(world, ctx, schema, archived)
        _record(world, ctx, schema, named, source="name")
        _record(world, other, schema, main)

        result = _conflicted_parameters(world.db, [ctx])

        assert result == {ctx: {schema.id: frozenset({main.id})}}

    def test_main_path_conflict_is_returned(self, world):
        _, proposal = world.estimate(world.contract())
        family, schema = world.family()
        main = world.param(schema, 1, "Параметр 1")
        ctx = _context(world, proposal, family)
        _record(world, ctx, schema, main)

        assert _conflicted_parameters(world.db, [ctx]) == {ctx: {schema.id: frozenset({main.id})}}

    def test_secondary_is_not_returned(self, world):
        _, proposal = world.estimate(world.contract())
        family, schema = world.family()
        secondary = world.param(schema, 1, "Параметр 1", role="secondary")
        ctx = _context(world, proposal, family)
        _record(world, ctx, schema, secondary)

        assert _conflicted_parameters(world.db, [ctx]) == {}

    def test_archived_main_is_not_returned(self, world):
        _, proposal = world.estimate(world.contract())
        family, schema = world.family()
        archived = world.param(schema, 1, "Параметр 1", archived=True)
        ctx = _context(world, proposal, family)
        _record(world, ctx, schema, archived)

        assert _conflicted_parameters(world.db, [ctx]) == {}

    def test_main_with_other_source_is_not_returned(self, world):
        _, proposal = world.estimate(world.contract())
        family, schema = world.family()
        main = world.param(schema, 1, "Параметр 1")
        ctx = _context(world, proposal, family)
        _record(world, ctx, schema, main, source="name")

        assert _conflicted_parameters(world.db, [ctx]) == {}

    def test_context_outside_the_input_is_not_returned(self, world):
        _, proposal = world.estimate(world.contract())
        family, schema = world.family()
        main = world.param(schema, 1, "Параметр 1")
        asked = _context(world, proposal, family)
        not_asked = _context(world, proposal, family)
        _record(world, not_asked, schema, main)

        assert _conflicted_parameters(world.db, [asked]) == {}

    def test_two_mains_of_one_context_share_one_schema_key(self, world):
        _, proposal = world.estimate(world.contract())
        family, schema = world.family()
        first = world.param(schema, 1, "Параметр 1")
        second = world.param(schema, 2, "Параметр 2")
        ctx = _context(world, proposal, family)
        _record(world, ctx, schema, first)
        _record(world, ctx, schema, second)

        assert _conflicted_parameters(world.db, [ctx]) == {ctx: {schema.id: frozenset({first.id, second.id})}}

    def test_conflicts_under_two_schemas_give_two_schema_keys(self, world):
        _, proposal = world.estimate(world.contract())
        family, schema = world.family()
        first = world.param(schema, 1, "Параметр 1")
        newer = world.supersede(family, schema)
        second = world.param(newer, 1, "Параметр 1")
        ctx = _context(world, proposal, family)
        _record(world, ctx, schema, first)
        _record(world, ctx, newer, second)

        assert _conflicted_parameters(world.db, [ctx]) == {
            ctx: {schema.id: frozenset({first.id}), newer.id: frozenset({second.id})}
        }

    def test_duplicates_in_the_input_give_the_same_result(self, world):
        _, proposal = world.estimate(world.contract())
        family, schema = world.family()
        main = world.param(schema, 1, "Параметр 1")
        ctx = _context(world, proposal, family)
        other = _context(world, proposal, family)
        _record(world, ctx, schema, main)
        _record(world, other, schema, main)

        expected = {ctx: {schema.id: frozenset({main.id})}, other: {schema.id: frozenset({main.id})}}
        assert _conflicted_parameters(world.db, [ctx, other]) == expected
        assert _conflicted_parameters(world.db, [ctx, other, ctx, other, ctx]) == expected

    def test_empty_input_gives_empty_dict_without_querying(self, world):
        with _capturing_sql(world.db) as statements:
            result = _conflicted_parameters(world.db, [])

        assert result == {}
        assert statements == []

        # Контроль перехвата: непустой вход на том же соединении виден счётчику, иначе
        # пустой список выше ничего не доказывает.
        with _capturing_sql(world.db) as statements:
            _conflicted_parameters(world.db, [0])

        assert len(statements) == 1


def _chapters_proposal(factories):
    estimate = factories.EstimateFactory.create()
    lot = factories.LotFactory.create(estimate=estimate)
    return factories.ProposalFactory.create(lot=lot)


def _chapter_row(factories, proposal, title, number, *, parent=None):
    kwargs = dict(
        proposal=proposal,
        is_chapter=True,
        job_title_in_proposal=title,
        item_number_in_proposal=number,
        chapter_number_in_proposal="1",
    )
    if parent is not None:
        kwargs["chapter_item_id"] = parent.id
    return factories.PositionItemFactory.create(**kwargs)


def _step(chapter):
    return ChapterStep(chapter.id, chapter.job_title_in_proposal, chapter.item_number_in_proposal)


class TestChapterSteps:
    def test_three_level_chain_and_two_branches_under_common_root(self, db_session, factories):
        proposal = _chapters_proposal(factories)
        root = _chapter_row(factories, proposal, "Раздел А", "1")
        middle = _chapter_row(factories, proposal, "Раздел Б", "1.1", parent=root)
        leaf = _chapter_row(factories, proposal, "Раздел В", "1.1.1", parent=middle)
        other_branch = _chapter_row(factories, proposal, "Раздел Г", "1.2", parent=root)

        result = chapter_steps(db_session, [leaf.id, other_branch.id])

        assert result == {
            leaf.id: (_step(root), _step(middle), _step(leaf)),
            other_branch.id: (_step(root), _step(other_branch)),
        }
        assert result[leaf.id][0] == ChapterStep(root.id, "Раздел А", "1")
        assert result[leaf.id][-1] == ChapterStep(leaf.id, "Раздел В", "1.1.1")

    def test_empty_number_gives_none_and_step_is_kept(self, db_session, factories):
        proposal = _chapters_proposal(factories)
        root = _chapter_row(factories, proposal, "Раздел А", None)
        child = _chapter_row(factories, proposal, "Раздел Б", "1.1", parent=root)

        result = chapter_steps(db_session, [child.id])

        assert result == {child.id: (ChapterStep(root.id, "Раздел А", None), ChapterStep(child.id, "Раздел Б", "1.1"))}
        assert result[child.id][0].number is None

    def test_chapter_without_parent_gives_one_step(self, db_session, factories):
        proposal = _chapters_proposal(factories)
        root = _chapter_row(factories, proposal, "Раздел А", "1")

        assert chapter_steps(db_session, [root.id]) == {root.id: (ChapterStep(root.id, "Раздел А", "1"),)}

    def test_key_absent_from_position_items_is_absent_from_result(self, db_session, factories):
        proposal = _chapters_proposal(factories)
        root = _chapter_row(factories, proposal, "Раздел А", "1")
        child = _chapter_row(factories, proposal, "Раздел Б", "1.1", parent=root)
        missing = 987_654_321

        result = chapter_steps(db_session, [child.id, missing])

        assert result == {child.id: (_step(root), _step(child))}
        assert missing not in result

    def test_duplicates_in_the_input_give_the_same_result(self, db_session, factories):
        proposal = _chapters_proposal(factories)
        root = _chapter_row(factories, proposal, "Раздел А", "1")
        child = _chapter_row(factories, proposal, "Раздел Б", "1.1", parent=root)
        other = _chapter_row(factories, proposal, "Раздел В", "1.2", parent=root)

        expected = {child.id: (_step(root), _step(child)), other.id: (_step(root), _step(other))}
        assert chapter_steps(db_session, [child.id, other.id]) == expected
        assert chapter_steps(db_session, [child.id, other.id, child.id, other.id, child.id]) == expected
