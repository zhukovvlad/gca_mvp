"""Отслеживание записей кэша сцен (спека `2026-11-27-stage-cache-design.md` §4, §5, §8).

Все тесты — на сессиях тестового движка из `tests/conftest.py` с настоящими коммитами
(`committing_session_factory`): транзакционная `db_session` держит внешнюю транзакцию на
соединении, и коммит в ней другим сессиям не виден.
"""
from __future__ import annotations

import threading
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import settings
from crud.common import DomainError
from models import Contractor, KpLineAgreement, KpSentFile
from services.context_routing import chapter_steps
from services.stage_cache import (
    DECISION_TABLES,
    READ_OPTION,
    is_dirty,
    mark_decision_write,
    stage_scene_cache,
)
from services.status_cache import typical_rates_cache


@pytest.fixture
def world(committing_db, committing_factories):
    """Пользователь, КП с цепочкой разделов; всё закоммичено, кэши сброшены."""
    f = committing_factories
    user = f.UserFactory.create()
    estimate = f.EstimateFactory.create()
    proposal = f.ProposalFactory.create(lot=f.LotFactory.create(estimate=estimate))
    root = f.PositionItemFactory.create(
        proposal=proposal, is_chapter=True, job_title_in_proposal="Раздел А",
        item_number_in_proposal="1", chapter_number_in_proposal="1",
    )
    chapter = f.PositionItemFactory.create(
        proposal=proposal, is_chapter=True, job_title_in_proposal="Раздел Б",
        item_number_in_proposal="1.1", chapter_number_in_proposal="1", chapter_item_id=root.id,
    )
    committing_db.commit()
    ids = {"user": user.id, "root": root.id, "chapter": chapter.id}
    committing_db.close()
    stage_scene_cache.invalidate()
    typical_rates_cache.invalidate()
    return ids


@pytest.fixture
def rates_warm(monkeypatch):
    """Кэш ставок включён и тёплый: сброс виден по тому, что `get` пересчитывает."""
    monkeypatch.setattr(settings, "TYPICAL_RATES_CACHE_TTL_S", 600)
    typical_rates_cache.invalidate()
    typical_rates_cache.get(lambda: "warm")
    yield
    typical_rates_cache.invalidate()


def _rates_were_reset() -> bool:
    return typical_rates_cache.get(lambda: "recomputed") == "recomputed"


class _Watch:
    """Поколение кэша сцен до действия."""

    def __init__(self) -> None:
        self.generation = stage_scene_cache.generation()

    @property
    def scene_reset(self) -> bool:
        return stage_scene_cache.generation() != self.generation


def _contractor(inn: str) -> Contractor:
    return Contractor(title="Т", inn=inn, address="а", accreditation="да")


def _decision_pg_insert(world):
    return pg_insert(KpLineAgreement).values(
        position_item_id=world["chapter"], net_price=Decimal("1"), set_by=world["user"]
    )


class TestResetOutsideClosedList:
    def test_orm_add_outside_list_resets_both_caches(self, committing_db, world, rates_warm):
        watch = _Watch()
        committing_db.add(_contractor("7700000001"))
        committing_db.commit()

        assert watch.scene_reset
        assert _rates_were_reset()

    def test_orm_attribute_change_outside_list_resets(self, committing_db, world):
        contractor = _contractor("7700000002")
        committing_db.add(contractor)
        committing_db.commit()
        watch = _Watch()

        contractor.title = "Другое"
        committing_db.commit()

        assert watch.scene_reset

    def test_orm_delete_outside_list_resets(self, committing_db, world):
        contractor = _contractor("7700000003")
        committing_db.add(contractor)
        committing_db.commit()
        watch = _Watch()

        committing_db.delete(contractor)
        committing_db.commit()

        assert watch.scene_reset

    def test_unmarked_pg_insert_to_list_table_resets(self, committing_db, world):
        watch = _Watch()
        committing_db.execute(_decision_pg_insert(world).on_conflict_do_nothing())
        committing_db.commit()

        assert watch.scene_reset

    def test_unmarked_delete_in_list_table_resets(self, committing_db, world):
        watch = _Watch()
        committing_db.execute(sa.delete(KpLineAgreement))
        committing_db.commit()

        assert watch.scene_reset

    def test_marked_pg_insert_and_delete_in_list_do_not_reset(self, committing_db, world, rates_warm):
        watch = _Watch()
        mark_decision_write(committing_db)
        committing_db.execute(_decision_pg_insert(world).on_conflict_do_nothing())
        committing_db.execute(sa.delete(KpLineAgreement))
        committing_db.commit()

        assert not watch.scene_reset
        assert not _rates_were_reset()

    def test_marked_transaction_with_list_and_other_table_resets(self, committing_db, world):
        watch = _Watch()
        mark_decision_write(committing_db)
        committing_db.execute(sa.delete(KpLineAgreement))
        committing_db.add(_contractor("7700000004"))
        committing_db.commit()

        assert watch.scene_reset

    def test_marked_transaction_without_writes_does_not_reset(self, committing_db, world):
        watch = _Watch()
        mark_decision_write(committing_db)
        committing_db.commit()

        assert not watch.scene_reset


class TestRawSql:
    @pytest.mark.parametrize(
        "sql",
        ["SELECT 1", "  \n select 1", "-- комментарий\nSELECT 1", "/* блок */ -- ещё\n  SELECT 1"],
    )
    def test_select_text_is_not_a_write(self, committing_db, world, sql):
        watch = _Watch()
        committing_db.execute(sa.text(sql))

        assert not is_dirty(committing_db)
        committing_db.commit()
        assert not watch.scene_reset

    def test_update_text_resets(self, committing_db, world):
        watch = _Watch()
        committing_db.execute(sa.text("UPDATE contractors SET title = title WHERE false"))
        committing_db.commit()

        assert watch.scene_reset

    def test_unmarked_with_select_text_resets(self, committing_db, world):
        watch = _Watch()
        committing_db.execute(sa.text("WITH c AS (SELECT 1 AS x) SELECT x FROM c"))
        committing_db.commit()

        assert watch.scene_reset

    def test_read_marked_with_select_text_does_not_reset(self, committing_db, world):
        watch = _Watch()
        stmt = sa.text("WITH c AS (SELECT 1 AS x) SELECT x FROM c").execution_options(**{READ_OPTION: True})
        committing_db.execute(stmt)
        committing_db.commit()

        assert not watch.scene_reset

    def test_select_for_update_and_key_share_are_not_writes(self, committing_db, world):
        watch = _Watch()
        stmt = sa.select(KpLineAgreement.position_item_id)
        committing_db.execute(stmt.with_for_update())
        committing_db.execute(stmt.with_for_update(key_share=True))

        assert not is_dirty(committing_db)
        committing_db.commit()
        assert not watch.scene_reset

    def test_textual_select_with_update_returning_resets(self, committing_db, world):
        watch = _Watch()
        stmt = sa.text("UPDATE contractors SET title = title WHERE false RETURNING id").columns(
            sa.column("id", sa.BigInteger)
        )
        committing_db.execute(stmt)

        assert is_dirty(committing_db)
        committing_db.commit()
        assert watch.scene_reset

    def test_textual_select_with_select_text_does_not_reset(self, committing_db, world):
        watch = _Watch()
        committing_db.execute(sa.text("SELECT id FROM contractors").columns(sa.column("id", sa.BigInteger)))

        assert not is_dirty(committing_db)
        committing_db.commit()
        assert not watch.scene_reset

    def test_textual_select_with_cte_needs_read_option(self, committing_db, world):
        watch = _Watch()
        text = "WITH c AS (SELECT 1 AS x) SELECT x FROM c"
        columns = sa.column("x", sa.Integer)
        committing_db.execute(sa.text(text).columns(columns).execution_options(**{READ_OPTION: True}))
        assert not is_dirty(committing_db)
        committing_db.execute(sa.text(text).columns(columns))
        committing_db.commit()

        assert watch.scene_reset


class TestOrderAndVisibility:
    def test_rates_cache_is_reset_before_scene_cache(self, committing_db, world, rates_warm, monkeypatch):
        seen: list[bool] = []
        real = stage_scene_cache.invalidate

        def spy() -> None:
            seen.append(_rates_were_reset())
            real()

        monkeypatch.setattr(stage_scene_cache, "invalidate", spy)
        committing_db.add(_contractor("7700000005"))
        committing_db.commit()

        assert seen == [True]

    def test_write_is_visible_to_another_session_at_invalidate_time(
        self, committing_db, committing_session_factory, world, monkeypatch
    ):
        seen: list[int] = []
        real = stage_scene_cache.invalidate

        def spy() -> None:
            other = committing_session_factory()
            try:
                seen.append(
                    other.scalar(sa.select(sa.func.count()).select_from(Contractor).where(Contractor.inn == "7700000006"))
                )
            finally:
                other.close()
            real()

        monkeypatch.setattr(stage_scene_cache, "invalidate", spy)
        committing_db.add(_contractor("7700000006"))
        committing_db.commit()

        assert seen == [1]


class TestRollbackAndSavepoints:
    def test_rollback_after_outside_write_does_not_reset_and_cleans_state(self, committing_db, world):
        watch = _Watch()
        mark_decision_write(committing_db)
        committing_db.add(_contractor("7700000007"))
        committing_db.flush()
        assert is_dirty(committing_db)

        committing_db.rollback()

        assert not watch.scene_reset
        assert not is_dirty(committing_db)
        # Пометка снята: следующая транзакция чистая, непомеченная запись в список сбрасывает.
        committing_db.execute(sa.delete(KpLineAgreement))
        committing_db.commit()
        assert watch.scene_reset

    def test_close_forgets_writes_and_mark(self, committing_db, world):
        watch = _Watch()
        mark_decision_write(committing_db)
        committing_db.add(_contractor("7700000012"))
        committing_db.flush()
        assert is_dirty(committing_db)

        committing_db.close()

        assert not watch.scene_reset
        assert not is_dirty(committing_db)
        # Пометка снята: непомеченная запись в список на той же сессии сбрасывает.
        committing_db.execute(sa.delete(KpLineAgreement))
        committing_db.commit()
        assert watch.scene_reset

    def test_savepoint_rollback_then_outer_commit_resets(self, committing_db, world):
        watch = _Watch()
        nested = committing_db.begin_nested()
        committing_db.add(_contractor("7700000008"))
        committing_db.flush()
        nested.rollback()
        assert is_dirty(committing_db)  # запись откатом точки сохранения не забыта

        committing_db.commit()

        assert watch.scene_reset

    def test_savepoint_release_without_outer_commit_does_not_reset(self, committing_db, world):
        watch = _Watch()
        with committing_db.begin_nested():
            committing_db.add(_contractor("7700000009"))
            committing_db.flush()
        # RELEASE SAVEPOINT прошёл, внешняя транзакция открыта, записанное не забыто
        assert committing_db.in_transaction()
        assert not watch.scene_reset
        assert is_dirty(committing_db)

        committing_db.commit()

        assert watch.scene_reset

    def test_outer_rollback_with_open_savepoint_forgets_writes_and_mark(self, committing_db, world):
        """`rollback()` внутри открытой точки сохранения откатывает и внешнюю транзакцию:
        записанное и пометка забыты, сброса нет, следующая транзакция чистая."""
        watch = _Watch()
        mark_decision_write(committing_db)
        committing_db.begin_nested()
        committing_db.add(_contractor("7700000012"))
        committing_db.flush()
        assert committing_db.in_nested_transaction()

        committing_db.rollback()

        assert not committing_db.in_transaction()
        assert not watch.scene_reset
        assert not is_dirty(committing_db)
        committing_db.execute(sa.delete(KpLineAgreement))
        committing_db.commit()
        assert watch.scene_reset

    def test_outer_commit_from_open_savepoint_resets_after_commit(
        self, committing_db, committing_session_factory, world, monkeypatch
    ):
        """`commit()` внутри открытой точки сохранения: RELEASE, затем коммит внешней —
        сброс ровно один, и в его момент запись видна другой сессии."""
        seen: list[int] = []
        real = stage_scene_cache.invalidate

        def spy() -> None:
            other = committing_session_factory()
            try:
                seen.append(
                    other.scalar(sa.select(sa.func.count()).select_from(Contractor).where(Contractor.inn == "7700000013"))
                )
            finally:
                other.close()
            real()

        monkeypatch.setattr(stage_scene_cache, "invalidate", spy)
        committing_db.begin_nested()
        committing_db.add(_contractor("7700000013"))
        committing_db.flush()

        committing_db.commit()

        assert seen == [1]
        assert not is_dirty(committing_db)

    def test_decision_failing_with_domain_error_after_list_write_does_not_reset(self, committing_db, world):
        """Шаблон `routers/tenders.py: _write_decision`: пометка, запись, отказ, rollback."""
        watch = _Watch()

        def write() -> None:
            mark_decision_write(committing_db)
            committing_db.execute(_decision_pg_insert(world).on_conflict_do_nothing())
            raise DomainError(422, "отказ")

        with pytest.raises(DomainError):
            try:
                write()
                committing_db.commit()
            except DomainError:
                committing_db.rollback()
                raise

        assert not watch.scene_reset
        assert not is_dirty(committing_db)
        committing_db.execute(sa.delete(KpLineAgreement))
        committing_db.commit()
        assert watch.scene_reset


class TestIsDirty:
    def test_true_after_flush_outside_list(self, committing_db, world):
        committing_db.add(_contractor("7700000010"))
        committing_db.flush()

        assert is_dirty(committing_db)

    def test_true_after_text_write(self, committing_db, world):
        committing_db.execute(sa.text("UPDATE contractors SET title = title WHERE false"))

        assert is_dirty(committing_db)

    def test_false_after_list_table_write_only(self, committing_db, world):
        mark_decision_write(committing_db)
        committing_db.execute(_decision_pg_insert(world).on_conflict_do_nothing())
        committing_db.execute(sa.delete(KpSentFile))

        assert not is_dirty(committing_db)

    def test_false_after_reads_only(self, committing_db, world):
        committing_db.execute(sa.select(sa.func.count()).select_from(Contractor))
        committing_db.execute(sa.text("SELECT 1"))

        assert not is_dirty(committing_db)

    def test_list_is_exactly_the_five_tables(self):
        expected = {"kp_line_agreements", "kp_line_comments", "kp_family_rungs", "kp_line_targets", "kp_sent_files"}
        assert expected == DECISION_TABLES


class TestOtherThread:
    def test_commit_from_another_thread_with_own_session_resets_same_cache(
        self, committing_session_factory, world
    ):
        watch = _Watch()
        errors: list[BaseException] = []

        def work() -> None:
            session = committing_session_factory()
            try:
                session.add(_contractor("7700000011"))
                session.commit()
            except BaseException as exc:  # передаём в основной поток
                errors.append(exc)
            finally:
                session.close()

        thread = threading.Thread(target=work)
        thread.start()
        thread.join()

        assert errors == []
        assert watch.scene_reset


class TestChapterStepsReadMark:
    def test_chapter_steps_in_marked_transaction_with_sent_file_commit_does_not_reset(
        self, committing_db, committing_factories, world
    ):
        """Шаблон `export_file`: пометка, чтение пути разделов, `db.add(KpSentFile)`, коммит.

        Файл пишется через unit of work, как в `crud/kp_sent_files.py`, и строка реально
        закоммичена: проверяется путь `before_flush`, а не только `do_orm_execute`.
        """
        offer_id = committing_factories.OfferFactory.create().id
        committing_db.commit()
        watch = _Watch()

        mark_decision_write(committing_db)
        steps = chapter_steps(committing_db, [world["chapter"]])
        assert len(steps[world["chapter"]]) == 2  # непустой путь: рекурсивный запрос выполнен
        committing_db.add(
            KpSentFile(
                offer_id=offer_id, file_key="stage-cache-chapter", filename="КП.xlsx",
                file_sha256="0" * 64, size_bytes=1, vat_rate=Decimal("20"),
                level_label="Уровень цен КП", created_by=world["user"],
            )
        )
        committing_db.commit()

        assert committing_db.scalar(sa.select(sa.func.count()).select_from(KpSentFile)) == 1
        assert not watch.scene_reset
