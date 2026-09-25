"""Протокол блокировки корзины: операции оператора против маршрутизации на
импорте, друг против друга, и перечитывание предусловий после блокировки
(спека `2026-09-22-catalog-families-design.md` §2.8; план, задача 6).

Три двухсессионных входа протокола, каждый — в ОБОИХ порядках, где план
этого требует:

* **импорт против архивирования** (`TestImportVsArchive`, одиночная позиция
  через `route_position`, И `TestImportBatchVsArchive`, партия через
  `route_positions` — РЕАЛЬНЫЙ путь продакшена, `services/import_pipeline.py`
  зовёт именно её с `estimate_ids`, `route_position` там не вызывается ни
  разу; исходный набор гонял только `route_position`, и
  дыра «`route_positions` берёт свой собственный `FOR SHARE` в другом месте
  кода» осталась бы непроверенной) — маршрутизация берёт `FOR SHARE`, архивирование — `FOR UPDATE`;
  непустого архивного контекста не возникает ни в одном порядке ни у одной
  из двух функций;
* **появление входящего правила против архивирования**
  (`TestMergeVsArchive`) — задача 6 не заводит отдельной операции «создать
  правило» (план, Task 6: «Операций «снять» и «переставить правило» план
  не заводит»), поэтому вход построен на операции, которую задача 6
  ДЕЙСТВИТЕЛЬНО владеет и которая заводит НОВОЕ входящее правило на
  существующем контексте — `merge_contexts` (правило источника переводится
  на цель, спека §2.4);
* **перечитывание после блокировки** (`TestArchiveRereadsAfterLock`) —
  без потоков: вторая сессия коммитит изменение МЕЖДУ моментом, когда
  `archive_context` первый раз (до лока) прочитала строку контекста, и
  моментом, когда она берёт лок; перечитывание обязано увидеть коммит
  второй сессии.

Свидетель ожидания замка — `pg_blocking_pids()` конкретного backend'а и
ТЕКСТ его запроса (не общий счётчик и не подстрока имени таблицы) — тот же
приём, что `test_context_routing.py::_wait_until_backend_blocks` (копия, не
импорт: наборы помощников тестов в этом репозитории друг у друга не
импортируют).

Потоки освобождаются и дожидаются в `finally` — упавший ассерт не должен
оставлять держателя лока живым до `TRUNCATE` следующего теста.
"""
from __future__ import annotations

import datetime as dt
import threading
import time
from unittest import mock

import pytest
import sqlalchemy as sa
from sqlalchemy import event

import services.context_operations as context_operations_module
import services.context_routing as context_routing_module
from models import (
    CatalogContext,
    ContextBucket,
    ContextMember,
    DecisionSource,
    MembershipState,
    NameRole,
    PositionItem,
    SemanticKind,
    SemanticState,
)
from services.context_operations import (
    REFUSE_CONTEXT_ARCHIVED,
    REFUSE_CONTEXT_NOT_EMPTY,
    REFUSE_DEFAULT_WITHOUT_SUCCESSOR,
    REFUSE_INCOMING_RULES,
    REFUSE_INVALID_MEMBERSHIP,
    ContextOperationError,
    accept_transfer,
    archive_context,
    merge_contexts,
    move_members,
    split_context,
    transfer_proposal,
)
from services.context_routing import (
    PREDICATE_CHAPTER_CHAIN_CONTAINS,
    route_position,
    route_positions,
)

pytestmark = pytest.mark.integration

_LOCK_WAIT_TIMEOUT = 30.0  # окно — 30 с, не 5
_JOIN_TIMEOUT = 30.0

# ИНВАРИАНТ: в порядке 1
# `TestImportVsArchive`/`TestImportBatchVsArchive` окно свидетеля ОБЯЗАНО
# быть короче окна ожидания A освобождения. Свидетель
# нефатален, но окна остались 30 с (свидетель, через
# `_LOCK_WAIT_TIMEOUT`) и 15 с (A) — при снятом `FOR SHARE` события идут
# так: B не блокируется и коммитит сразу; главный поток сидит в свидетеле
# 30 с; через 15 с A бросает `AssertionError` внутри `patched_record_event`
# и её ТРАНЗАКЦИЯ ОТКАТЫВАЕТСЯ. Архивирования не происходит вовсе, и тест
# падает на `assert not errors`, а не на заявленном исходе плана (проверено
# снятием `FOR SHARE`). Единственная
# защита от этого — ЯВНЫЙ порядок окон: свидетель короче отпуска A.
_RACE_WITNESS_TIMEOUT = 10.0
_A_RELEASE_TIMEOUT = 30.0
assert _RACE_WITNESS_TIMEOUT < _A_RELEASE_TIMEOUT


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _proposal(factories):
    estimate = factories.EstimateFactory.create()
    lot = factories.LotFactory.create(estimate=estimate)
    return factories.ProposalFactory.create(lot=lot), estimate


def _chapter(factories, proposal, *, title="Раздел", chapter_number="1"):
    return factories.PositionItemFactory.create(
        proposal=proposal,
        is_chapter=True,
        job_title_in_proposal=title,
        chapter_number_in_proposal=chapter_number,
    )


def _position(factories, proposal, *, chapter=None, title="Работа", catalog_position=None):
    kwargs = dict(proposal=proposal, is_chapter=False, job_title_in_proposal=title)
    if chapter is not None:
        kwargs["chapter_item_id"] = chapter.id
    if catalog_position is not None:
        kwargs["catalog_position_id"] = catalog_position.id
    return factories.PositionItemFactory.create(**kwargs)


def _context(session, bucket, *, is_default=False, **overrides) -> CatalogContext:
    defaults = dict(
        bucket_id=bucket.id,
        is_default=is_default,
        semantic_kind=SemanticKind.WORK.value,
        semantic_kind_source=DecisionSource.rule.value,
        semantic_kind_at=_now(),
        name_role=NameRole.WORK.value,
        name_role_source=DecisionSource.rule.value,
        name_role_at=_now(),
        place_dictionary_version=1,
        semantic_state=SemanticState.SUGGESTED.value,
    )
    defaults.update(overrides)
    ctx = CatalogContext(**defaults)
    session.add(ctx)
    session.flush()
    return ctx


def _wait_until_backend_blocks(
    probe_factory, *, pid: int, contains: str, timeout: float = _LOCK_WAIT_TIMEOUT
) -> bool:
    """Копия `test_context_routing.py::_wait_until_backend_blocks` —
    свидетель через `pg_blocking_pids(pid)` КОНКРЕТНОГО backend'а и текст
    ЕГО запроса, не общий счётчик ожидающих (`docs/pitfalls/db.md`:
    «ассерт зелен и когда ждёт посторонний backend»)."""
    deadline = time.monotonic() + timeout
    with probe_factory() as probe:
        while time.monotonic() < deadline:
            row = probe.execute(
                sa.text(
                    "SELECT cardinality(pg_blocking_pids(:pid)) > 0 AS blocked, "
                    "(SELECT query FROM pg_stat_activity WHERE pid = :pid) AS query"
                ),
                {"pid": pid},
            ).one()
            probe.rollback()
            if row.blocked and contains in (row.query or ""):
                return True
            time.sleep(0.05)
    return False


def _wait_for_pid(pid_holder: dict[str, int], key: str, *, timeout: float = 10.0) -> int:
    """Ждёт, пока фоновый поток не запишет свой `pg_backend_pid()` в общий
    словарь (поток пишет его ДО входа в защищаемую операцию — гонки с самим
    чтением здесь нет, нужно только дождаться записи)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if key in pid_holder:
            return pid_holder[key]
        time.sleep(0.02)
    raise AssertionError(f"{key} не появился в pid_holder за {timeout} с")


def _make_empty_default_bucket(committing_db, committing_factories):
    """Корзина с ЕДИНСТВЕННЫМ (default) контекстом, пустым — стартовая
    точка обеих гоночных сцен. Первая маршрутизация неизбежно создаёт
    членство в новом default-контексте (спека §2.4); членство удаляется
    прямой правкой, чтобы контекст стал пустым для архивирования."""
    proposal, _ = _proposal(committing_factories)
    cp = committing_factories.CatalogPositionFactory.create()
    position = _position(committing_factories, proposal, catalog_position=cp)
    committing_db.commit()

    member = route_position(committing_db, position_item_id=position.id)
    bucket_id = member.bucket_id
    default_ctx_id = member.context_id
    committing_db.delete(member)
    committing_db.commit()

    return bucket_id, default_ctx_id, cp


# ---------------------------------------------------------------------------
#  Импорт против архивирования — оба порядка (спека §2.8; план, Task 6)
# ---------------------------------------------------------------------------

class TestImportVsArchive:
    def test_archive_wins_the_race_new_import_lands_in_the_successor(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """Порядок 1: A архивирует пустой default-контекст первой. B
        (маршрутизация новой позиции той же корзины) обязана дождаться
        коммита A и уйти в СВЕЖИЙ действующий default, не в архивный."""
        bucket_id, default_ctx_id, cp = _make_empty_default_bucket(
            committing_db, committing_factories
        )
        proposal2, _ = _proposal(committing_factories)
        successor = _context(committing_db, committing_db.get(ContextBucket, bucket_id))
        committing_db.commit()
        successor_id = successor.id

        new_position = _position(committing_factories, proposal2, catalog_position=cp)
        committing_db.commit()

        a_paused = threading.Event()
        a_release = threading.Event()
        errors: list[Exception] = []

        original_record_event = context_operations_module.record_event

        def patched_record_event(db, *, event_type, **kwargs):
            if event_type == "context_archived":
                a_paused.set()
                # ИНВАРИАНТ: этот таймаут ОБЯЗАН быть длиннее
                # `_RACE_WITNESS_TIMEOUT` ниже — см. комментарий у
                # определения констант.
                assert a_release.wait(timeout=_A_RELEASE_TIMEOUT), "release A не пришёл вовремя"
            return original_record_event(db, event_type=event_type, **kwargs)

        def thread_a():
            try:
                with committing_session_factory() as dbA:
                    archive_context(
                        dbA, context_id=default_ctx_id, new_default_context_id=successor_id,
                        actor_id=None,
                    )
                    dbA.commit()
            except Exception as exc:  # pragma: no cover — диагностика
                errors.append(exc)

        b_result: dict[str, object] = {}
        pid_holder: dict[str, int] = {}
        b_started = threading.Event()

        def thread_b():
            try:
                with committing_session_factory() as dbB:
                    pid_holder["pid"] = dbB.execute(
                        sa.text("SELECT pg_backend_pid()")
                    ).scalar_one()
                    b_started.set()
                    member = route_position(dbB, position_item_id=new_position.id)
                    dbB.commit()
                    b_result["context_id"] = member.context_id
                    b_result["routed_by"] = member.routed_by
            except Exception as exc:  # pragma: no cover — диагностика
                errors.append(exc)

        ta = threading.Thread(target=thread_a, daemon=True)
        tb = threading.Thread(target=thread_b, daemon=True)
        blocked = False
        try:
            with mock.patch.object(
                context_operations_module, "record_event", side_effect=patched_record_event
            ):
                ta.start()
                assert a_paused.wait(timeout=10), "A не встала на паузу перед коммитом"

                tb.start()
                assert b_started.wait(timeout=10)

                # Свидетель НЕ фатален здесь: результат только ЗАПИСЫВАЕТСЯ.
                # Окно свидетеля —
                # `_RACE_WITNESS_TIMEOUT` (10 с), КОРОЧЕ `_A_RELEASE_TIMEOUT`
                # (30 с) выше: иначе при снятом `FOR SHARE` (B не
                # блокируется вовсе) A бросает `AssertionError` и
                # ОТКАТЫВАЕТСЯ раньше, чем этот опрос закончится, и тест
                # падает на `assert not errors`, а не на исходе плана.
                blocked = _wait_until_backend_blocks(
                    committing_session_factory, pid=pid_holder["pid"], contains="FOR SHARE",
                    timeout=_RACE_WITNESS_TIMEOUT,
                )

                a_release.set()
                ta.join(timeout=_JOIN_TIMEOUT)
                tb.join(timeout=_JOIN_TIMEOUT)
        finally:
            a_release.set()
            ta.join(timeout=_JOIN_TIMEOUT)
            tb.join(timeout=_JOIN_TIMEOUT)

        assert not errors, errors
        assert not ta.is_alive()
        assert not tb.is_alive()

        archived = committing_db.get(CatalogContext, default_ctx_id)
        committing_db.refresh(archived)
        member_count = committing_db.execute(
            sa.select(sa.func.count())
            .select_from(ContextMember)
            .where(ContextMember.context_id == default_ctx_id)
        ).scalar_one()
        # Исход плана — ПЕРВЫМ: непустой архивный контекст не возникает.
        # Снятие `FOR SHARE` обязано красить ИМЕННО эти ассерты.
        assert archived.archived_at is not None
        assert member_count == 0  # непустого архивного контекста не возникло
        assert b_result["context_id"] == successor_id
        assert b_result["routed_by"] == "default"

        # Свидетель — ВТОРЫМ: различает МЕХАНИЗМ (B реально ждала лок), а не
        # просто «исход совпал по расписанию».
        assert blocked, "B (маршрутизация) не встала в очередь на FOR UPDATE архивирования"

    def test_import_wins_the_race_archive_refuses_on_reread(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """Порядок 2: B (маршрутизация) берёт `FOR SHARE` первой и
        держит её, пока A ждёт `FOR UPDATE`. Освобождённая B успевает
        вставить членство и закоммитить; A, получив лок, обязана
        ПЕРЕЧИТАТЬ число членств и отказать `REFUSE_CONTEXT_NOT_EMPTY` —
        архивный непустой контекст не возникает и в этом порядке."""
        bucket_id, default_ctx_id, cp = _make_empty_default_bucket(
            committing_db, committing_factories
        )
        proposal2, _ = _proposal(committing_factories)
        successor = _context(committing_db, committing_db.get(ContextBucket, bucket_id))
        committing_db.commit()
        successor_id = successor.id

        new_position = _position(committing_factories, proposal2, catalog_position=cp)
        committing_db.commit()

        b_locked = threading.Event()
        b_release = threading.Event()
        errors: list[Exception] = []
        pid_holder: dict[str, int] = {}
        b_result: dict[str, object] = {}
        a_result: dict[str, object] = {}

        original_apply_routing = context_routing_module._apply_routing

        def patched_apply_routing(db, *, position, bucket, chapters):
            if position.id == new_position.id:
                b_locked.set()
                assert b_release.wait(timeout=15), "release B не пришёл вовремя"
            return original_apply_routing(db, position=position, bucket=bucket, chapters=chapters)

        def thread_b():
            try:
                with committing_session_factory() as dbB:
                    pid_holder["b_pid"] = dbB.execute(
                        sa.text("SELECT pg_backend_pid()")
                    ).scalar_one()
                    member = route_position(dbB, position_item_id=new_position.id)
                    dbB.commit()
                    b_result["context_id"] = member.context_id
            except Exception as exc:  # pragma: no cover — диагностика
                errors.append(exc)

        def thread_a():
            try:
                with committing_session_factory() as dbA:
                    pid_holder["a_pid"] = dbA.execute(
                        sa.text("SELECT pg_backend_pid()")
                    ).scalar_one()
                    archive_context(
                        dbA, context_id=default_ctx_id, new_default_context_id=successor_id,
                        actor_id=None,
                    )
                    dbA.commit()
                    a_result["archived"] = True
            except ContextOperationError as exc:
                a_result["error_code"] = exc.code
            except Exception as exc:  # pragma: no cover — диагностика
                errors.append(exc)

        tb = threading.Thread(target=thread_b, daemon=True)
        ta = threading.Thread(target=thread_a, daemon=True)
        try:
            with mock.patch.object(
                context_routing_module, "_apply_routing", side_effect=patched_apply_routing
            ):
                tb.start()
                assert b_locked.wait(timeout=10), "B не встала на паузу, держа FOR SHARE"

                # A стартует ПОСЛЕ того, как B гарантированно держит FOR
                # SHARE незакоммиченной транзакцией: A.pg_backend_pid()
                # пишется до вызова archive_context, поэтому свидетель ниже
                # видит корректный pid независимо от того, как быстро A
                # дойдёт до lock_buckets.
                ta.start()

                assert _wait_until_backend_blocks(
                    committing_session_factory,
                    pid=_wait_for_pid(pid_holder, "a_pid"),
                    contains="FOR UPDATE",
                ), "A (архивирование) не встала в очередь на лок, который держит B"

                b_release.set()
                tb.join(timeout=_JOIN_TIMEOUT)
                ta.join(timeout=_JOIN_TIMEOUT)
        finally:
            b_release.set()
            tb.join(timeout=_JOIN_TIMEOUT)
            ta.join(timeout=_JOIN_TIMEOUT)

        assert not errors, errors
        assert not tb.is_alive()
        assert not ta.is_alive()

        assert a_result.get("error_code") == REFUSE_CONTEXT_NOT_EMPTY
        assert "archived" not in a_result

        context = committing_db.get(CatalogContext, default_ctx_id)
        committing_db.refresh(context)
        assert context.archived_at is None  # архивирование НЕ прошло
        member_count = committing_db.execute(
            sa.select(sa.func.count())
            .select_from(ContextMember)
            .where(ContextMember.context_id == default_ctx_id)
        ).scalar_one()
        assert member_count == 1
        assert b_result["context_id"] == default_ctx_id


# ---------------------------------------------------------------------------
#  Импорт против архивирования — та же гонка, но через РЕАЛЬНЫЙ путь
#  продакшена: `route_positions(estimate_ids=...)`, а не `route_position`.
#  `services/import_pipeline.py` зовёт именно `route_positions` — у неё
#  СВОЙ вызов `lock_buckets(db, list(existing_bucket_ids), exclusive=False)`
#  (context_routing.py, отдельная строка от `route_position`), и утверждения
#  выше его не касаются вовсе.
# ---------------------------------------------------------------------------

class TestImportBatchVsArchive:
    def test_archive_wins_the_race_batch_import_lands_in_the_successor(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """Порядок 1: A архивирует пустой default-контекст первой. B —
        `route_positions` (партия из одной сметы) той же корзины — обязана
        дождаться коммита A на `FOR SHARE`, взятом ВНУТРИ `route_positions`,
        и уйти в свежий действующий default, не в архивный."""
        bucket_id, default_ctx_id, cp = _make_empty_default_bucket(
            committing_db, committing_factories
        )
        proposal2, estimate2 = _proposal(committing_factories)
        successor = _context(committing_db, committing_db.get(ContextBucket, bucket_id))
        committing_db.commit()
        successor_id = successor.id

        new_position = _position(committing_factories, proposal2, catalog_position=cp)
        committing_db.commit()

        a_paused = threading.Event()
        a_release = threading.Event()
        errors: list[Exception] = []

        original_record_event = context_operations_module.record_event

        def patched_record_event(db, *, event_type, **kwargs):
            if event_type == "context_archived":
                a_paused.set()
                # ИНВАРИАНТ: длиннее `_RACE_WITNESS_TIMEOUT` ниже —
                # см. комментарий у определения констант.
                assert a_release.wait(timeout=_A_RELEASE_TIMEOUT), "release A не пришёл вовремя"
            return original_record_event(db, event_type=event_type, **kwargs)

        def thread_a():
            try:
                with committing_session_factory() as dbA:
                    archive_context(
                        dbA, context_id=default_ctx_id, new_default_context_id=successor_id,
                        actor_id=None,
                    )
                    dbA.commit()
            except Exception as exc:  # pragma: no cover — диагностика
                errors.append(exc)

        pid_holder: dict[str, int] = {}
        b_started = threading.Event()
        b_result: dict[str, object] = {}

        def thread_b():
            try:
                with committing_session_factory() as dbB:
                    pid_holder["pid"] = dbB.execute(
                        sa.text("SELECT pg_backend_pid()")
                    ).scalar_one()
                    b_started.set()
                    route_positions(dbB, estimate_ids=[estimate2.id])
                    dbB.commit()
                    member = dbB.execute(
                        sa.select(ContextMember).where(
                            ContextMember.position_item_id == new_position.id
                        )
                    ).scalar_one()
                    b_result["context_id"] = member.context_id
                    b_result["routed_by"] = member.routed_by
            except Exception as exc:  # pragma: no cover — диагностика
                errors.append(exc)

        ta = threading.Thread(target=thread_a, daemon=True)
        tb = threading.Thread(target=thread_b, daemon=True)
        blocked = False
        try:
            with mock.patch.object(
                context_operations_module, "record_event", side_effect=patched_record_event
            ):
                ta.start()
                assert a_paused.wait(timeout=10), "A не встала на паузу перед коммитом"

                tb.start()
                assert b_started.wait(timeout=10)

                # Свидетель НЕ фатален здесь — только записывается; исход
                # проверяется первым, ниже. Окно — `_RACE_WITNESS_TIMEOUT`
                # (10 с), КОРОЧЕ
                # `_A_RELEASE_TIMEOUT` (30 с) выше: иначе при снятом
                # `FOR SHARE` A бросает `AssertionError` и откатывается
                # раньше, чем этот опрос закончится, и тест падает на
                # `assert not errors`, а не на исходе.
                blocked = _wait_until_backend_blocks(
                    committing_session_factory, pid=pid_holder["pid"], contains="FOR SHARE",
                    timeout=_RACE_WITNESS_TIMEOUT,
                )

                a_release.set()
                ta.join(timeout=_JOIN_TIMEOUT)
                tb.join(timeout=_JOIN_TIMEOUT)
        finally:
            a_release.set()
            ta.join(timeout=_JOIN_TIMEOUT)
            tb.join(timeout=_JOIN_TIMEOUT)

        assert not errors, errors
        assert not ta.is_alive()
        assert not tb.is_alive()

        archived = committing_db.get(CatalogContext, default_ctx_id)
        committing_db.refresh(archived)
        member_count = committing_db.execute(
            sa.select(sa.func.count())
            .select_from(ContextMember)
            .where(ContextMember.context_id == default_ctx_id)
        ).scalar_one()
        # Исход плана — ПЕРВЫМ, свидетель — вторым.
        assert archived.archived_at is not None
        assert member_count == 0  # непустого архивного контекста не возникло
        assert b_result["context_id"] == successor_id
        assert b_result["routed_by"] == "default"

        assert blocked, "B (route_positions) не встала в очередь на FOR UPDATE архивирования"

    def test_batch_import_wins_the_race_archive_refuses_on_reread(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """Порядок 2: B (`route_positions`) берёт `FOR SHARE` первой (её
        СОБСТВЕННЫЙ вызов `lock_buckets`, не тот, что у `route_position`) и
        держит её, пока A ждёт `FOR UPDATE`. Освобождённая B успевает
        вставить членство и закоммитить; A, получив лок, обязана
        ПЕРЕЧИТАТЬ число членств и отказать `REFUSE_CONTEXT_NOT_EMPTY`."""
        bucket_id, default_ctx_id, cp = _make_empty_default_bucket(
            committing_db, committing_factories
        )
        proposal2, estimate2 = _proposal(committing_factories)
        successor = _context(committing_db, committing_db.get(ContextBucket, bucket_id))
        committing_db.commit()
        successor_id = successor.id

        new_position = _position(committing_factories, proposal2, catalog_position=cp)
        committing_db.commit()

        b_locked = threading.Event()
        b_release = threading.Event()
        errors: list[Exception] = []
        pid_holder: dict[str, int] = {}
        b_result: dict[str, object] = {}
        a_result: dict[str, object] = {}

        original_apply_routing = context_routing_module._apply_routing

        def patched_apply_routing(db, *, position, bucket, chapters):
            if position.id == new_position.id:
                b_locked.set()
                assert b_release.wait(timeout=15), "release B не пришёл вовремя"
            return original_apply_routing(db, position=position, bucket=bucket, chapters=chapters)

        def thread_b():
            try:
                with committing_session_factory() as dbB:
                    pid_holder["b_pid"] = dbB.execute(
                        sa.text("SELECT pg_backend_pid()")
                    ).scalar_one()
                    route_positions(dbB, estimate_ids=[estimate2.id])
                    dbB.commit()
                    member = dbB.execute(
                        sa.select(ContextMember).where(
                            ContextMember.position_item_id == new_position.id
                        )
                    ).scalar_one()
                    b_result["context_id"] = member.context_id
            except Exception as exc:  # pragma: no cover — диагностика
                errors.append(exc)

        def thread_a():
            try:
                with committing_session_factory() as dbA:
                    pid_holder["a_pid"] = dbA.execute(
                        sa.text("SELECT pg_backend_pid()")
                    ).scalar_one()
                    archive_context(
                        dbA, context_id=default_ctx_id, new_default_context_id=successor_id,
                        actor_id=None,
                    )
                    dbA.commit()
                    a_result["archived"] = True
            except ContextOperationError as exc:
                a_result["error_code"] = exc.code
            except Exception as exc:  # pragma: no cover — диагностика
                errors.append(exc)

        tb = threading.Thread(target=thread_b, daemon=True)
        ta = threading.Thread(target=thread_a, daemon=True)
        try:
            with mock.patch.object(
                context_routing_module, "_apply_routing", side_effect=patched_apply_routing
            ):
                tb.start()
                assert b_locked.wait(timeout=10), "B не встала на паузу, держа FOR SHARE"

                ta.start()

                assert _wait_until_backend_blocks(
                    committing_session_factory,
                    pid=_wait_for_pid(pid_holder, "a_pid"),
                    contains="FOR UPDATE",
                ), "A (архивирование) не встала в очередь на лок, который держит B"

                b_release.set()
                tb.join(timeout=_JOIN_TIMEOUT)
                ta.join(timeout=_JOIN_TIMEOUT)
        finally:
            b_release.set()
            tb.join(timeout=_JOIN_TIMEOUT)
            ta.join(timeout=_JOIN_TIMEOUT)

        assert not errors, errors
        assert not tb.is_alive()
        assert not ta.is_alive()

        assert a_result.get("error_code") == REFUSE_CONTEXT_NOT_EMPTY
        assert "archived" not in a_result

        context = committing_db.get(CatalogContext, default_ctx_id)
        committing_db.refresh(context)
        assert context.archived_at is None  # архивирование НЕ прошло
        member_count = committing_db.execute(
            sa.select(sa.func.count())
            .select_from(ContextMember)
            .where(ContextMember.context_id == default_ctx_id)
        ).scalar_one()
        assert member_count == 1
        assert b_result["context_id"] == default_ctx_id


# ---------------------------------------------------------------------------
#  Появление входящего правила против архивирования — оба порядка
#  (предусловие 2, спека §2.8; см. докстроку модуля)
# ---------------------------------------------------------------------------

def _make_bucket_with_rule_source_and_empty_target(committing_db, committing_factories):
    """Корзина с ТРЕМЯ контекстами: `keep_default` (несёт исходное
    членство, остаётся действующим умолчанием — архивирование в этом файле
    его не касается), `rule_source` (несёт ОДНО входящее правило,
    источник слияния) и `archive_target` (пустой, без правил — то, что
    архивируется в этих тестах, и КУДА `merge_contexts` переводит правило
    источника)."""
    proposal, _ = _proposal(committing_factories)
    cp = committing_factories.CatalogPositionFactory.create()
    position = _position(committing_factories, proposal, catalog_position=cp)
    committing_db.commit()

    member = route_position(committing_db, position_item_id=position.id)
    bucket = committing_db.get(ContextBucket, member.bucket_id)
    keep_default_id = member.context_id

    rule_source = _context(committing_db, bucket, is_default=False)
    archive_target = _context(committing_db, bucket, is_default=False)
    user = committing_factories.UserFactory.create()
    from models import ContextRoutingRule

    rule = ContextRoutingRule(
        bucket_id=bucket.id,
        ordinal=1,
        predicate={"kind": PREDICATE_CHAPTER_CHAIN_CONTAINS, "value": "неважно"},
        context_id=rule_source.id,
        created_by=user.id,
    )
    committing_db.add(rule)
    committing_db.commit()

    return bucket.id, keep_default_id, rule_source.id, archive_target.id


class TestMergeVsArchive:
    def test_archive_wins_the_race_merge_then_refuses_on_archived_target(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """Порядок 1: A архивирует `archive_target` (пустой, без правил)
        первой. B (`merge_contexts(rule_source -> archive_target)`) обязана
        дождаться коммита A и, перечитав ЦЕЛЬ слияния под своим же локом,
        отказать `REFUSE_CONTEXT_ARCHIVED` — правило источника НЕ
        переезжает в архивный контекст ни в одном порядке."""
        bucket_id, keep_default_id, rule_source_id, archive_target_id = (
            _make_bucket_with_rule_source_and_empty_target(committing_db, committing_factories)
        )

        a_paused = threading.Event()
        a_release = threading.Event()
        errors: list[Exception] = []

        original_record_event = context_operations_module.record_event

        def patched_record_event(db, *, event_type, **kwargs):
            if event_type == "context_archived":
                a_paused.set()
                assert a_release.wait(timeout=15), "release A не пришёл вовремя"
            return original_record_event(db, event_type=event_type, **kwargs)

        def thread_a():
            try:
                with committing_session_factory() as dbA:
                    archive_context(
                        dbA, context_id=archive_target_id, new_default_context_id=None,
                        actor_id=None,
                    )
                    dbA.commit()
            except Exception as exc:  # pragma: no cover — диагностика
                errors.append(exc)

        pid_holder: dict[str, int] = {}
        b_started = threading.Event()
        b_result: dict[str, object] = {}

        def thread_b():
            try:
                with committing_session_factory() as dbB:
                    pid_holder["pid"] = dbB.execute(
                        sa.text("SELECT pg_backend_pid()")
                    ).scalar_one()
                    b_started.set()
                    merge_contexts(
                        dbB, source_context_id=rule_source_id,
                        target_context_id=archive_target_id, actor_id=None,
                    )
                    dbB.commit()
                    b_result["merged"] = True
            except ContextOperationError as exc:
                b_result["error_code"] = exc.code
            except Exception as exc:  # pragma: no cover — диагностика
                errors.append(exc)

        ta = threading.Thread(target=thread_a, daemon=True)
        tb = threading.Thread(target=thread_b, daemon=True)
        try:
            with mock.patch.object(
                context_operations_module, "record_event", side_effect=patched_record_event
            ):
                ta.start()
                assert a_paused.wait(timeout=10), "A не встала на паузу перед коммитом"

                tb.start()
                assert b_started.wait(timeout=10)

                assert _wait_until_backend_blocks(
                    committing_session_factory, pid=pid_holder["pid"], contains="FOR UPDATE"
                ), "B (слияние) не встала в очередь на лок, который держит A"

                a_release.set()
                ta.join(timeout=_JOIN_TIMEOUT)
                tb.join(timeout=_JOIN_TIMEOUT)
        finally:
            a_release.set()
            ta.join(timeout=_JOIN_TIMEOUT)
            tb.join(timeout=_JOIN_TIMEOUT)

        assert not errors, errors
        assert not ta.is_alive()
        assert not tb.is_alive()

        assert b_result.get("error_code") == REFUSE_CONTEXT_ARCHIVED
        assert "merged" not in b_result

        # Сверка по всей базе: ни одно правило не ведёт в архивный контекст.
        dangling = committing_db.execute(
            sa.text(
                "SELECT count(*) FROM context_routing_rules r "
                "JOIN catalog_contexts c ON c.id = r.context_id "
                "WHERE c.archived_at IS NOT NULL"
            )
        ).scalar_one()
        assert dangling == 0

    def test_merge_wins_the_race_archive_then_refuses_on_incoming_rule(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """Порядок 2: B (`merge_contexts`) берёт `FOR UPDATE` первой и
        переводит правило источника на `archive_target`, закоммитив это ДО
        того, как A получает лок. A, перечитав число входящих правил под
        своим локом, обязана отказать `REFUSE_INCOMING_RULES` — контекст с
        только что появившимся правилом не архивируется."""
        bucket_id, keep_default_id, rule_source_id, archive_target_id = (
            _make_bucket_with_rule_source_and_empty_target(committing_db, committing_factories)
        )

        b_paused = threading.Event()
        b_release = threading.Event()
        errors: list[Exception] = []

        original_record_event = context_operations_module.record_event

        def patched_record_event(db, *, event_type, **kwargs):
            if event_type == "context_merged":
                b_paused.set()
                assert b_release.wait(timeout=15), "release B не пришёл вовремя"
            return original_record_event(db, event_type=event_type, **kwargs)

        def thread_b():
            try:
                with committing_session_factory() as dbB:
                    merge_contexts(
                        dbB, source_context_id=rule_source_id,
                        target_context_id=archive_target_id, actor_id=None,
                    )
                    dbB.commit()
            except Exception as exc:  # pragma: no cover — диагностика
                errors.append(exc)

        pid_holder: dict[str, int] = {}
        a_result: dict[str, object] = {}

        def thread_a():
            try:
                with committing_session_factory() as dbA:
                    pid_holder["pid"] = dbA.execute(
                        sa.text("SELECT pg_backend_pid()")
                    ).scalar_one()
                    archive_context(
                        dbA, context_id=archive_target_id, new_default_context_id=None,
                        actor_id=None,
                    )
                    dbA.commit()
                    a_result["archived"] = True
            except ContextOperationError as exc:
                a_result["error_code"] = exc.code
            except Exception as exc:  # pragma: no cover — диагностика
                errors.append(exc)

        tb = threading.Thread(target=thread_b, daemon=True)
        ta = threading.Thread(target=thread_a, daemon=True)
        try:
            with mock.patch.object(
                context_operations_module, "record_event", side_effect=patched_record_event
            ):
                tb.start()
                assert b_paused.wait(timeout=10), "B не встала на паузу перед коммитом"

                ta.start()
                assert _wait_until_backend_blocks(
                    committing_session_factory,
                    pid=_wait_for_pid(pid_holder, "pid"),
                    contains="FOR UPDATE",
                ), "A (архивирование) не встала в очередь на лок, который держит B"

                b_release.set()
                tb.join(timeout=_JOIN_TIMEOUT)
                ta.join(timeout=_JOIN_TIMEOUT)
        finally:
            b_release.set()
            tb.join(timeout=_JOIN_TIMEOUT)
            ta.join(timeout=_JOIN_TIMEOUT)

        assert not errors, errors
        assert not tb.is_alive()
        assert not ta.is_alive()

        assert a_result.get("error_code") == REFUSE_INCOMING_RULES
        assert "archived" not in a_result

        context = committing_db.get(CatalogContext, archive_target_id)
        committing_db.refresh(context)
        assert context.archived_at is None

        dangling = committing_db.execute(
            sa.text(
                "SELECT count(*) FROM context_routing_rules r "
                "JOIN catalog_contexts c ON c.id = r.context_id "
                "WHERE c.archived_at IS NOT NULL"
            )
        ).scalar_one()
        assert dangling == 0


# ---------------------------------------------------------------------------
#  `accept_transfer` против `move_members` на ОДНОЙ корзине — без дедлока
# ---------------------------------------------------------------------------

class TestAcceptTransferVsMoveMembersNoDeadlock:
    """`accept_transfer` и `move_members`, взявшиеся за ОДНО и то же
    устаревшее членство X одной корзины, обязаны разойтись без дедлока: обе
    операции берут корзину ПЕРВОЙ (общий порядок ветки — корзина раньше
    членства), значит при столкновении одна просто встаёт в очередь на
    корзину, вторая заканчивает и отпускает оба лока, очередь освобождается.

    Пауза ставится ПОСЛЕ ПЕРВОГО реально взятого лока `accept_transfer` —
    БЕЗ ПРЕДПОЛОЖЕНИЯ, что это корзина: слушатель на самой сессии A ловит
    строчный `FOR UPDATE` на `context_members`, а обёртка
    `lock_buckets` ловит лок корзины — сработает ровно один из двух,
    смотря какой лок код реально берёт первым. Тест ничего не знает заранее
    о порядке — тем и годится как регрессия: если порядок внутри
    `accept_transfer` перевернуть обратно (членство раньше корзины), B
    успевает захватить корзину, пока A ещё держит только членство, и на
    втором локе каждой стороны образуется цикл — настоящий `DeadlockDetected`
    от PostgreSQL, `assert not errors` ниже это ловит (проверено снятием
    защиты — порядок временно возвращён, тест сам падает на этом ассерте, не
    на догадке)."""

    def test_accept_transfer_and_move_members_on_the_same_membership_finish_without_deadlock(
        self, committing_db, committing_factories, committing_session_factory
    ):
        user = committing_factories.UserFactory.create()
        committing_db.commit()

        proposal, _ = _proposal(committing_factories)
        cp = committing_factories.CatalogPositionFactory.create()
        item_x = _position(committing_factories, proposal, catalog_position=cp, title="X")
        committing_db.commit()

        member_x = route_position(committing_db, position_item_id=item_x.id)
        bucket_id = member_x.bucket_id
        default_context_id = member_x.context_id
        committing_db.commit()

        # Членство X помечено устаревшим НАПРЯМУЮ, без реального разноса
        # статьи — эффективная статья (`None`) остаётся той же, что у
        # текущей корзины, поэтому предложение переноса указывает на ТУ ЖЕ
        # корзину: `accept_transfer` берёт под лок ровно `bucket_id` — ту же
        # единственную корзину, что и `move_members` (это и есть сцена,
        # нужная тесту, — обе операции конкурируют за ОДНУ корзину).
        member_x_row = committing_db.get(ContextMember, item_x.id)
        member_x_row.membership_state = MembershipState.STALE.value
        committing_db.commit()

        proposal_x = transfer_proposal(committing_db, position_item_id=item_x.id)
        assert proposal_x is not None
        assert proposal_x.proposed_bucket_id == bucket_id
        expected_category_id = proposal_x.effective_category_id

        pause_armed = {"value": True}
        first_lock_paused = threading.Event()
        first_lock_release = threading.Event()
        errors: list[str] = []
        a_result: dict[str, object] = {}
        b_result: dict[str, object] = {}
        pid_holder: dict[str, int] = {}

        def _pause_once():
            if pause_armed["value"]:
                pause_armed["value"] = False
                first_lock_paused.set()
                assert first_lock_release.wait(timeout=_A_RELEASE_TIMEOUT), (
                    "release A не пришёл вовремя"
                )

        original_lock_buckets = context_operations_module.lock_buckets

        def patched_lock_buckets(db, bucket_ids, *, exclusive):
            result = original_lock_buckets(db, bucket_ids, exclusive=exclusive)
            _pause_once()
            return result

        def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            if "context_members" in statement and "FOR UPDATE" in statement:
                _pause_once()

        def thread_a():
            try:
                with committing_session_factory() as dbA:
                    pid_holder["a"] = dbA.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                    conn = dbA.connection()
                    event.listen(conn, "after_cursor_execute", _after_cursor_execute)
                    try:
                        accept_transfer(
                            dbA, position_item_id=item_x.id,
                            expected_category_id=expected_category_id, actor_id=user.id,
                        )
                    finally:
                        event.remove(conn, "after_cursor_execute", _after_cursor_execute)
                    dbA.commit()
                    a_result["transferred"] = True
            except Exception as exc:  # noqa: BLE001 — ловим DeadlockDetected и прочее
                errors.append(f"a: {type(exc).__name__}: {exc}")

        def thread_b():
            try:
                with committing_session_factory() as dbB:
                    pid_holder["b"] = dbB.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                    move_members(
                        dbB, position_item_ids=[item_x.id], target_context_id=default_context_id,
                        actor_id=user.id, reason="manual",
                    )
                    dbB.commit()
                    b_result["moved"] = True
            except Exception as exc:  # noqa: BLE001
                errors.append(f"b: {type(exc).__name__}: {exc}")

        ta = threading.Thread(target=thread_a, name="a", daemon=True)
        tb = threading.Thread(target=thread_b, name="b", daemon=True)
        try:
            with mock.patch.object(
                context_operations_module, "lock_buckets", side_effect=patched_lock_buckets
            ):
                ta.start()
                assert first_lock_paused.wait(timeout=_LOCK_WAIT_TIMEOUT), (
                    "A не встала на паузу после своего первого лока"
                )

                tb.start()
                assert _wait_for_pid(pid_holder, "b", timeout=_LOCK_WAIT_TIMEOUT)
                assert _wait_until_backend_blocks(
                    committing_session_factory, pid=pid_holder["b"], contains="",
                    timeout=_LOCK_WAIT_TIMEOUT,
                ), "B не встала в очередь на лок, который держит A"

                first_lock_release.set()
                ta.join(timeout=_JOIN_TIMEOUT)
                tb.join(timeout=_JOIN_TIMEOUT)
        finally:
            first_lock_release.set()
            ta.join(timeout=_JOIN_TIMEOUT)
            tb.join(timeout=_JOIN_TIMEOUT)

        assert not errors, errors
        assert not ta.is_alive()
        assert not tb.is_alive()
        assert a_result.get("transferred") is True
        assert b_result.get("moved") is True


# ---------------------------------------------------------------------------
#  Перечитывание после блокировки — БЕЗ потоков (спека §2.8; план, Task 6)
# ---------------------------------------------------------------------------

class TestArchiveRereadsAfterLock:
    def test_is_default_read_before_the_lock_does_not_leak_into_the_precondition(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """`committing_db` уже прочитала `candidate` (identity map
        закэшировала `is_default=False`) РАНЬШЕ, чем ДРУГАЯ, ОТДЕЛЬНАЯ
        сессия сделала `candidate` действующим умолчанием корзины и
        закоммитила это. `archive_context`, вызванная на `committing_db`
        ПОСЛЕ внешнего коммита, обязана увидеть СВЕЖЕЕ `is_default=True`
        (через перечитывание после `lock_buckets`, не через кэш первого
        чтения) и потребовать `new_default_context_id`
        (`REFUSE_DEFAULT_WITHOUT_SUCCESSOR`). Без потоков — чистая
        последовательность двух РЕАЛЬНЫХ (коммитящих) сессий; настоящая
        конкурентность здесь не нужна, нужна только сама СТАЛОСТЬ
        identity map (RED-прогон со снятым
        `db.expire_all()`)."""
        proposal, _ = _proposal(committing_factories)
        cp = committing_factories.CatalogPositionFactory.create()
        position = _position(committing_factories, proposal, catalog_position=cp)
        committing_db.commit()

        member = route_position(committing_db, position_item_id=position.id)
        bucket_id = member.bucket_id
        committing_db.delete(member)
        committing_db.commit()

        candidate = _context(committing_db, committing_db.get(ContextBucket, bucket_id))
        committing_db.commit()
        candidate_id = candidate.id

        # ПЕРВОЕ чтение `candidate` ЭТОЙ сессией — до каких-либо внешних
        # изменений. Прогревает identity map ровно так, как это сделал бы
        # собственный первый `db.get` внутри `archive_context`.
        primed = committing_db.get(CatalogContext, candidate_id)
        assert primed.is_default is False

        # ОТДЕЛЬНАЯ, независимая сессия делает `candidate` умолчанием
        # корзины и коммитит: снимает флаг со СТАРОГО умолчания (оставшегося
        # от `route_position` выше — его членство удалено, но флаг никто не
        # снимал), затем ставит новому — тот же порядок, которого требует
        # частичный уникальный индекс.
        with committing_session_factory() as other:
            old_default = other.execute(
                sa.select(CatalogContext).where(
                    CatalogContext.bucket_id == bucket_id, CatalogContext.is_default.is_(True)
                )
            ).scalar_one()
            old_default.is_default = False
            other.flush()
            other_ctx = other.get(CatalogContext, candidate_id)
            other_ctx.is_default = True
            other.commit()

        with pytest.raises(ContextOperationError) as excinfo:
            archive_context(
                committing_db, context_id=candidate_id, new_default_context_id=None,
                actor_id=None,
            )
        assert excinfo.value.code == REFUSE_DEFAULT_WITHOUT_SUCCESSOR


class TestSplitContextRereadsAfterLock:
    """`split_context` тоже вызывает
    `db.expire_all()` после `lock_buckets` — без перечитывания
    снятие `expire_all()`
    проходило бы весь набор незамеченным. Тот же
    приём, что `TestArchiveRereadsAfterLock`: без потоков, вторая
    (настоящая) сессия архивирует контекст-ИСТОЧНИК разделения МЕЖДУ
    первым чтением этой сессии и локом."""

    def test_source_archived_between_first_read_and_lock_is_caught(
        self, committing_db, committing_factories, committing_session_factory
    ):
        proposal, _ = _proposal(committing_factories)
        cp = committing_factories.CatalogPositionFactory.create()
        position = _position(committing_factories, proposal, catalog_position=cp)
        committing_db.commit()

        member = route_position(committing_db, position_item_id=position.id)
        source_id = member.context_id
        committing_db.commit()

        # ПЕРВОЕ чтение источника ЭТОЙ сессией — до каких-либо внешних
        # изменений. Прогревает identity map ровно так, как это делает
        # собственный первый `db.get` внутри `split_context`.
        primed = committing_db.get(CatalogContext, source_id)
        assert primed.archived_at is None

        # ОТДЕЛЬНАЯ, независимая сессия архивирует источник НАПРЯМУЮ (минуя
        # `archive_context` — тот отказал бы на непустом контексте с
        # действующим умолчанием: сцена здесь про перечитывание, не про
        # предусловия архивирования) и коммитит — та же техника прямой
        # правки строки, что `test_archived_context_is_refused`
        # (`test_context_operations.py`).
        with committing_session_factory() as other:
            other_ctx = other.get(CatalogContext, source_id)
            other_ctx.is_default = False
            other_ctx.archived_at = _now()
            other.commit()

        with pytest.raises(ContextOperationError) as excinfo:
            split_context(
                committing_db, context_id=source_id, position_item_ids=[position.id],
                rule=None, actor_id=None,
            )
        assert excinfo.value.code == REFUSE_CONTEXT_ARCHIVED


class TestMoveMembersRereadsAfterLock:
    """То же для `move_members` — цель
    переноса архивируется ДРУГОЙ сессией между первым чтением этой сессии
    и локом."""

    def test_target_archived_between_first_read_and_lock_is_caught(
        self, committing_db, committing_factories, committing_session_factory
    ):
        proposal, _ = _proposal(committing_factories)
        cp = committing_factories.CatalogPositionFactory.create()
        position = _position(committing_factories, proposal, catalog_position=cp)
        committing_db.commit()

        member = route_position(committing_db, position_item_id=position.id)
        bucket = committing_db.get(ContextBucket, member.bucket_id)
        committing_db.commit()

        target = _context(committing_db, bucket, is_default=False)
        committing_db.commit()
        target_id = target.id

        # ПЕРВОЕ чтение цели ЭТОЙ сессией — прогревает identity map так же,
        # как собственный первый `db.get` внутри `move_members`.
        primed = committing_db.get(CatalogContext, target_id)
        assert primed.archived_at is None

        with committing_session_factory() as other:
            other_ctx = other.get(CatalogContext, target_id)
            other_ctx.archived_at = _now()
            other.commit()

        with pytest.raises(ContextOperationError) as excinfo:
            move_members(
                committing_db, position_item_ids=[position.id], target_context_id=target_id,
                actor_id=None, reason="manual",
            )
        assert excinfo.value.code == REFUSE_CONTEXT_ARCHIVED

    def test_member_vanishing_between_the_lock_and_the_reread_is_refused_not_keyerror(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """Членство может уйти КАСКАДОМ
        (замена/удаление сметы, спека §2.8) уже ПОСЛЕ того, как
        `move_members` взяла лок корзины — удаление позиции само в протокол
        блокировки корзины не входит и `FOR UPDATE` его не задерживает. Без
        повторной проверки `missing` ПОСЛЕ перечитывания код упал бы
        `KeyError` в цикле переноса вместо доменного `ContextOperationError`.

        Сценарий требует НАСТОЯЩЕГО потока: членство обязано быть НА МЕСТЕ
        для ПЕРВОГО (до-локового) чтения `move_members` — иначе сработал бы
        более ранний отказ («до-локовый» `missing`), а не тот, что стережёт
        именно ПОВТОРНУЮ проверку. Пауза — через монки-патч
        `context_operations_module.lock_buckets`: настоящий лок берётся
        первым (вызовом оригинала), пауза — сразу ПОСЛЕ него."""
        proposal, _ = _proposal(committing_factories)
        cp = committing_factories.CatalogPositionFactory.create()
        position = _position(committing_factories, proposal, catalog_position=cp)
        committing_db.commit()

        member = route_position(committing_db, position_item_id=position.id)
        bucket = committing_db.get(ContextBucket, member.bucket_id)
        committing_db.commit()

        target = _context(committing_db, bucket, is_default=False)
        committing_db.commit()
        target_id = target.id

        locked = threading.Event()
        release = threading.Event()
        errors: list[Exception] = []
        result: dict[str, object] = {}

        original_lock_buckets = context_operations_module.lock_buckets

        def patched_lock_buckets(db, bucket_ids, *, exclusive):
            original_lock_buckets(db, bucket_ids, exclusive=exclusive)
            locked.set()
            assert release.wait(timeout=15), "release не пришёл вовремя"

        def worker():
            try:
                with committing_session_factory() as dbW:
                    move_members(
                        dbW, position_item_ids=[position.id], target_context_id=target_id,
                        actor_id=None, reason="manual",
                    )
                    dbW.commit()
                    result["ok"] = True
            except ContextOperationError as exc:
                result["error_code"] = exc.code
            except Exception as exc:  # pragma: no cover — диагностика
                errors.append(exc)

        worker_thread = threading.Thread(target=worker, daemon=True)
        try:
            with mock.patch.object(
                context_operations_module, "lock_buckets", side_effect=patched_lock_buckets
            ):
                worker_thread.start()
                assert locked.wait(timeout=10), "поток не встал на паузу после лока"

                # Позиция удаляется ДРУГОЙ сессией, ПОКА worker держит лок
                # корзины (удаление позиции с ним не контендит — оно не
                # входит в протокол блокировки).
                with committing_session_factory() as other:
                    other_position = other.get(PositionItem, position.id)
                    other.delete(other_position)
                    other.commit()

                release.set()
                worker_thread.join(timeout=_JOIN_TIMEOUT)
        finally:
            release.set()
            worker_thread.join(timeout=_JOIN_TIMEOUT)

        assert not errors, errors
        assert not worker_thread.is_alive()
        assert result.get("error_code") == REFUSE_INVALID_MEMBERSHIP
        assert "ok" not in result
