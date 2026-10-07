"""Гонки ядра варианта работы на двух сессиях с настоящими commit-ами (спека
`2026-10-02-catalog-variants-design.md` §2.6).

Тест гонки доказывает защиту, только если без защиты он краснеет, а не
зависает: у каждого потока `SET LOCAL lock_timeout`, у каждого ожидания
таймаут, у каждого `join` проверка `is_alive()` с принудительным обрывом
backend-а. Синхронизация барьером ПОСЛЕ чтения (`after_cursor_execute`, ответ
уже у клиента), а не сном; «поток дошёл до паузы» и «поток встал на замке» —
разные исходы одного ожидания и считаются раздельно.

Гонки блокировки варианта и `ON CONFLICT` проверяются на публичных функциях,
которым защита принадлежит (`archive_variant_if_empty`,
`get_or_create_variant`, `get_or_create_value`), а не через `apply_values`:
внутри обработчика одной семьи сериализованы замком семьи, и снятие замка
варианта там ничего бы не уронило. Гонка вердикта — на самих
`apply_values` и `freeze_schema`.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from unittest import mock

import pytest
import sqlalchemy as sa
from sqlalchemy import event

from models import (
    CatalogContext,
    FamilyParameterSchema,
    FamilyParameterValue,
    SemanticJob,
    ValueOrigin,
    WorkVariant,
)
from services import work_variants as work_variants_module
from services.variant_request import load_values_material, paths_hash_of
from services.work_variants import (
    apply_values,
    archive_variant_if_empty,
    freeze_schema,
    get_or_create_value,
    get_or_create_variant,
)
from tests.integration.test_work_families import (
    _RELEASE_TIMEOUT,
    _WITNESS_TIMEOUT,
    _backend_blocked_once,
    _terminate_backend,
)
from tests.integration.test_work_variants_core import (
    _answer,
    _attach_variant,
    _freeze_world,
    _guarded,
    _named,
    _schema_job,
    _schema_snapshot,
    _snapshot,
    _world,
)
from tests.integration.test_work_variants_material import (
    _bind,
    _chain_context,
    _settings,
    _uid,
)
from tests.integration.test_work_variants_schema import (
    _family,
    _param,
    _schema,
    _variant,
)

pytestmark = pytest.mark.integration

_LOCK_TIMEOUT = "20s"


class _Scene:
    """Потоки одной гонки: пауза и освобождение на каждое имя, результаты,
    ошибки и pid backend-а для обрыва зависшего."""

    def __init__(self, session_factory) -> None:
        self.factory = session_factory
        self.pending: dict[str, threading.Event] = {}
        self.release: dict[str, threading.Event] = {}
        self.done: dict[str, threading.Event] = {}
        self.threads: dict[str, threading.Thread] = {}
        self.pids: dict[str, int] = {}
        self.results: dict[str, object] = {}
        self.errors: list[str] = []

    def spawn(
        self, name: str, work: Callable, *, pause_on: Callable[[str], bool] | None = None
    ) -> None:
        self.pending[name] = threading.Event()
        self.release[name] = threading.Event()
        self.done[name] = threading.Event()

        def body() -> None:
            try:
                with self.factory() as db:
                    db.execute(sa.text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
                    self.pids[name] = db.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                    if pause_on is not None:
                        self._install_pause(db, name, pause_on)
                    self.results[name] = work(db)
                    db.commit()
            except Exception as exc:  # noqa: BLE001 — DeadlockDetected, IntegrityError и прочее
                self.errors.append(f"{name}: {type(exc).__name__}: {exc}")
            finally:
                self.done[name].set()

        thread = threading.Thread(target=body, name=name, daemon=True)
        self.threads[name] = thread
        thread.start()

    def _install_pause(self, db, name: str, predicate: Callable[[str], bool]) -> None:
        def _listener(conn, cursor, statement, parameters, context, executemany):
            if not self.pending[name].is_set() and predicate(statement):
                self.pending[name].set()
                if not self.release[name].wait(timeout=_RELEASE_TIMEOUT):
                    raise AssertionError(f"{name}: release не пришёл вовремя")

        event.listen(db.connection(), "after_cursor_execute", _listener)

    def wait_pid(self, name: str) -> bool:
        deadline = time.monotonic() + _WITNESS_TIMEOUT
        while time.monotonic() < deadline:
            if name in self.pids:
                return True
            time.sleep(0.02)
        return False

    def wait_settled(self, name: str, *, contains: str) -> str:
        """Ждёт, пока поток встанет на паузу (`paused`), закончится (`done`) или
        станет ждать замок запросом с `contains` (`blocked`); `timeout` — ни то
        ни другое. Выходит сразу, как только верно любое условие."""
        assert self.wait_pid(name), f"{name}: pid не получен"
        deadline = time.monotonic() + _WITNESS_TIMEOUT
        while time.monotonic() < deadline:
            if self.pending[name].is_set():
                return "paused"
            if self.done[name].is_set():
                return "done"
            if _backend_blocked_once(self.factory, pid=self.pids[name], contains=contains):
                return "blocked"
            time.sleep(0.05)
        return "timeout"

    def wait_paused(self, name: str) -> bool:
        return self.pending[name].wait(timeout=_WITNESS_TIMEOUT)

    def finish(self) -> None:
        """Освобождает всех, ждёт потоки с таймаутом и обрывает зависшие."""
        for name in self.release:
            self.release[name].set()
        for name, thread in self.threads.items():
            thread.join(timeout=_RELEASE_TIMEOUT)
            if thread.is_alive():
                _terminate_backend(self.factory, self.pids.get(name))
                thread.join(timeout=_WITNESS_TIMEOUT)

    def assert_clean(self) -> None:
        assert not self.errors, self.errors
        for name, thread in self.threads.items():
            assert not thread.is_alive(), f"поток {name} завис"


def _is_variant_count(statement: str) -> bool:
    return (
        "count(" in statement
        and "FROM catalog_contexts" in statement
        and "catalog_contexts.work_variant_id" in statement
    )


def _is_variant_select_by_key(statement: str) -> bool:
    return (
        statement.lstrip().startswith("SELECT")
        and "FROM work_variants" in statement
        and "work_variants.values_key" in statement
    )


def _is_value_insert(statement: str) -> bool:
    return statement.lstrip().startswith("INSERT INTO family_parameter_values")


def _detach(context_id: int):
    """Контекст уходит с варианта: все четыре колонки варианта снимаются
    разом (CHECK-пары)."""

    def _do(db) -> None:
        context = db.get(CatalogContext, context_id)
        context.work_variant_id = None
        context.variant_at = None
        context.variant_paths_hash = None
        context.variant_split_hint = None
        db.flush()

    return _do


def _two_contexts_on_one_variant(db, factories):
    world = _world(db, factories)
    variant = _variant(db, world.family, world.schema, values_key="shared-key")
    second, _ = _chain_context(
        db, factories, title=f"Вторая строка {_uid()}", path_specs=[((), 1)]
    )
    _bind(db, factories, second, family=world.family)
    _attach_variant(db, world.context_id, variant)
    _attach_variant(db, second, variant)
    db.commit()
    return world, variant, second


def _final_variant(session_factory, variant_id: int) -> tuple[str, int]:
    """`(статус, число контекстов на нём)` свежей сессией."""
    with session_factory() as db:
        status = db.execute(
            sa.select(WorkVariant.status).where(WorkVariant.id == variant_id)
        ).scalar_one()
        count = db.execute(
            sa.select(sa.func.count()).select_from(CatalogContext).where(
                CatalogContext.work_variant_id == variant_id
            )
        ).scalar_one()
        return status, count


# ---------------------------------------------------------------------------
#  Блокировка варианта
# ---------------------------------------------------------------------------

class TestLastTwoContextsLeaveInParallel:
    def test_variant_is_archived_when_both_last_contexts_leave_at_once(
        self, committing_db, committing_factories, committing_session_factory
    ):
        world, variant, second = _two_contexts_on_one_variant(committing_db, committing_factories)
        scene = _Scene(committing_session_factory)

        def leave_and_archive(context_id):
            def work(db):
                _detach(context_id)(db)
                return archive_variant_if_empty(db, variant.id)

            return work

        try:
            scene.spawn("a", leave_and_archive(world.context_id), pause_on=_is_variant_count)
            assert scene.wait_paused("a"), "A не дошла до чтения подсчёта"
            scene.spawn("b", leave_and_archive(second), pause_on=_is_variant_count)
            settled = scene.wait_settled("b", contains="work_variants")
        finally:
            scene.finish()

        scene.assert_clean()
        # Без защиты оба потока видят чужую незафиксированную ссылку и не
        # архивируют; с защитой B ждёт замок и считает после коммита A.
        assert settled == "blocked", f"B не встала на замок варианта: {settled}"
        assert _final_variant(committing_session_factory, variant.id) == ("archived", 0)
        assert sorted(bool(v) for v in scene.results.values()) == [False, True]


class TestLastLeavesNewArrives:
    def test_variant_stays_active_with_the_arriving_context(
        self, committing_db, committing_factories, committing_session_factory
    ):
        db, factories = committing_db, committing_factories
        world = _world(db, factories)
        parameters = world.parameters
        mapping = {1: world.values[(1, "50 мм")], 2: world.values[(2, "бетон")]}
        key = f"1={mapping[1]}|2={mapping[2]}"
        variant = _variant(db, world.family, world.schema, values_key=key)
        arriving, _ = _chain_context(
            db, factories, title=f"Приходящая строка {_uid()}", path_specs=[((), 1)]
        )
        _bind(db, factories, arriving, family=world.family)
        _attach_variant(db, world.context_id, variant)
        db.commit()
        assert set(parameters) == {1, 2}
        scene = _Scene(committing_session_factory)

        def arrive(db):
            found, created = get_or_create_variant(
                db, family_id=world.family.id, schema_id=world.schema.id,
                value_ids_by_ordinal=mapping,
            )
            context = db.get(CatalogContext, arriving)
            context.work_variant_id = found.id
            context.variant_at = sa.func.now()
            context.variant_paths_hash = "arrived"
            db.flush()
            return found.id, created

        def leave_and_archive(db):
            _detach(world.context_id)(db)
            return archive_variant_if_empty(db, variant.id)

        try:
            scene.spawn("b", arrive, pause_on=_is_variant_select_by_key)
            assert scene.wait_paused("b"), "B не дошла до паузы после чтения варианта"
            scene.spawn("a", leave_and_archive)
            settled = scene.wait_settled("a", contains="work_variants")
        finally:
            scene.finish()

        scene.assert_clean()
        # С защитой A встаёт на замок варианта, который держит B; без защиты A
        # успевает заархивировать вариант, и B приводит контекст на архивный.
        assert settled == "blocked", f"A не встала на замок варианта: {settled}"
        assert scene.results["b"] == (variant.id, False)
        assert scene.results["a"] is False
        assert _final_variant(committing_session_factory, variant.id) == ("active", 1)


# ---------------------------------------------------------------------------
#  Атомарное расширение списка
# ---------------------------------------------------------------------------

class TestParallelNewValue:
    def test_one_row_and_one_creator_for_the_same_value_in_two_sessions(
        self, committing_db, committing_factories, committing_session_factory
    ):
        schema = _schema(committing_db, committing_factories)
        parameter = _param(committing_db, schema, 1, "Толщина")
        committing_db.commit()
        scene = _Scene(committing_session_factory)

        def add(text):
            def work(db):
                return get_or_create_value(
                    db, parameter_id=parameter.id, text=text, origin=ValueOrigin.extension
                )

            return work

        try:
            scene.spawn("a", add("75 мм"), pause_on=_is_value_insert)
            assert scene.wait_paused("a"), "A не дошла до паузы после вставки"
            scene.spawn("b", add("75 ММ"))
            settled = scene.wait_settled("b", contains="family_parameter_values")
        finally:
            scene.finish()

        scene.assert_clean()
        # B встаёт на вставку A и после её коммита находит строку, а не падает
        # на уникальном ключе.
        assert settled == "blocked", f"B не встала на вставку A: {settled}"
        with committing_session_factory() as db:
            rows = db.execute(
                sa.select(FamilyParameterValue.id).where(
                    FamilyParameterValue.parameter_id == parameter.id
                )
            ).scalars().all()
        assert len(rows) == 1
        assert scene.results["a"] == (rows[0], True)
        assert scene.results["b"] == (rows[0], False)


# ---------------------------------------------------------------------------
#  Гонка вердикта: условие проверяется под блокировками
# ---------------------------------------------------------------------------

def _barrier_before(module, name: str, scene: _Scene, thread_name: str):
    """Подмена внутреннего шага блокировок: ПЕРВЫЙ вызов потока `thread_name`
    встаёт на паузу ДО настоящего вызова, остальные идут насквозь."""
    original = getattr(module, name)

    def _wrapped(*args, **kwargs):
        if (
            threading.current_thread().name == thread_name
            and not scene.pending[thread_name].is_set()
        ):
            scene.pending[thread_name].set()
            assert scene.release[thread_name].wait(timeout=_RELEASE_TIMEOUT), (
                f"{thread_name}: release не пришёл вовремя"
            )
        return original(*args, **kwargs)

    return mock.patch.object(module, name, side_effect=_wrapped)


def _values_race_world(db, factories):
    world = _world(db, factories)
    job, guard = _guarded(db, context_id=world.context_id, schema_id=world.schema.id)
    db.commit()
    return world, job, guard


def _run_values_race(factory, world, guard, concurrent_change) -> tuple[_Scene, dict, dict]:
    """A строит охрану и встаёт на барьере в начале `apply_values`; пока она
    стоит, `concurrent_change` меняет состояние и коммитит; A продолжает.
    Возвращает сцену и снимки домена после изменения и после A."""
    scene = _Scene(factory)

    def work(db):
        return apply_values(
            db, context_id=world.context_id, schema_id=world.schema.id,
            answer=_answer(_named(1, "50 мм"), _named(2, "бетон")), paths_hash="paths-a",
            guard=guard, settings=_settings(),
        )

    with _barrier_before(work_variants_module, "_acquire_domain_locks", scene, "a"):
        try:
            scene.spawn("a", work)
            assert scene.wait_paused("a"), "A не дошла до барьера"
            with factory() as other:
                concurrent_change(other)
                other.commit()
            with factory() as probe:
                after_change = _snapshot(probe, world)
        finally:
            scene.finish()
    scene.assert_clean()
    with factory() as probe:
        after_a = _snapshot(probe, world)
    return scene, after_change, after_a


class TestApplyValuesVerdictUnderLocks:
    def test_without_a_concurrent_change_the_same_flow_is_applied(
        self, committing_db, committing_factories, committing_session_factory
    ):
        world, _job, guard = _values_race_world(committing_db, committing_factories)
        scene, _, _ = _run_values_race(
            committing_session_factory, world, guard, lambda other: None
        )
        assert scene.results["a"].applied is True

    def test_value_list_extended_while_a_waits_makes_the_answer_stale(
        self, committing_db, committing_factories, committing_session_factory
    ):
        world, job, guard = _values_race_world(committing_db, committing_factories)

        def extend(other):
            other.add(
                FamilyParameterValue(
                    parameter_id=world.parameters[1], value="200 мм", value_norm="200 мм",
                    origin="extension",
                )
            )

        scene, after_change, after_a = _run_values_race(
            committing_session_factory, world, guard, extend
        )

        outcome = scene.results["a"]
        assert (outcome.applied, outcome.unapplied_reason) == (False, "stale_fingerprint")
        assert after_a == after_change
        with committing_session_factory() as db:
            job_row = db.get(SemanticJob, job.id)
            assert (job_row.status, job_row.cancel_reason, job_row.claim_token) == (
                "cancelled", "input_changed", None,
            )

    def test_pending_family_set_while_a_waits_makes_the_subject_not_applicable(
        self, committing_db, committing_factories, committing_session_factory
    ):
        world, job, guard = _values_race_world(committing_db, committing_factories)
        other_family = _family(committing_db, definition="Другая")
        user_id = committing_factories.UserFactory.create().id
        committing_db.commit()

        def set_pending(other):
            context = other.get(CatalogContext, world.context_id)
            context.pending_family_id = other_family.id
            context.pending_family_source = "manual"
            context.pending_by = user_id
            context.pending_at = sa.func.now()

        scene, after_change, after_a = _run_values_race(
            committing_session_factory, world, guard, set_pending
        )

        # Исход однозначен: шаг блокировок читает предмет уже после коммита B,
        # ожидаемая семья — другая, а версия схемы — не её текущая. Допуск
        # «stale_fingerprint или not_applicable» прятал бы, какая проверка
        # сработала (ревью задачи 5).
        outcome = scene.results["a"]
        assert (outcome.applied, outcome.unapplied_reason) == (False, "not_applicable")
        assert after_a == after_change
        with committing_session_factory() as db:
            job_row = db.get(SemanticJob, job.id)
            assert (job_row.status, job_row.cancel_reason, job_row.claim_token) == (
                "cancelled", "not_applicable", None,
            )


class TestFreezeSchemaVerdictUnderLocks:
    def _run(self, factory, world, guard, concurrent_change):
        scene = _Scene(factory)

        def work(db):
            return freeze_schema(
                db, schema_id=world.building.id,
                answer=_freeze_answer(), guard=guard, settings=_settings(),
            )

        with _barrier_before(work_variants_module, "_acquire_schema_locks", scene, "a"):
            try:
                scene.spawn("a", work)
                assert scene.wait_paused("a"), "A не дошла до барьера"
                with factory() as other:
                    concurrent_change(other)
                    other.commit()
                with factory() as probe:
                    after_change = _schema_snapshot(probe)
            finally:
                scene.finish()
        scene.assert_clean()
        with factory() as probe:
            after_a = _schema_snapshot(probe)
        return scene, after_change, after_a

    def test_without_a_concurrent_change_the_same_flow_is_applied(
        self, committing_db, committing_factories, committing_session_factory
    ):
        world = _freeze_world(committing_db, committing_factories)
        _job, guard = _schema_job(committing_db, world)
        committing_db.commit()
        scene, _, _ = self._run(committing_session_factory, world, guard, lambda other: None)
        assert scene.results["a"].applied is True

    def test_family_renamed_while_a_waits_makes_the_answer_stale(
        self, committing_db, committing_factories, committing_session_factory
    ):
        world = _freeze_world(committing_db, committing_factories)
        job, guard = _schema_job(committing_db, world)
        committing_db.commit()

        def rename(other):
            from models import WorkFamily

            other.get(WorkFamily, world.family.id).title = "Совсем другое имя"

        scene, after_change, after_a = self._run(
            committing_session_factory, world, guard, rename
        )

        outcome = scene.results["a"]
        assert (outcome.applied, outcome.unapplied_reason) == (False, "stale_fingerprint")
        assert after_a == after_change
        with committing_session_factory() as db:
            job_row = db.get(SemanticJob, job.id)
            assert (job_row.status, job_row.cancel_reason) == ("cancelled", "input_changed")

    def test_version_frozen_by_another_session_while_a_waits_is_not_applicable(
        self, committing_db, committing_factories, committing_session_factory
    ):
        world = _freeze_world(committing_db, committing_factories)
        _job, guard = _schema_job(committing_db, world)
        committing_db.commit()

        def freeze_other_way(other):
            row = other.get(FamilyParameterSchema, world.building.id)
            row.status = "superseded"
            row.frozen_at = sa.func.now()
            row.superseded_at = sa.func.now()

        scene, after_change, after_a = self._run(
            committing_session_factory, world, guard, freeze_other_way
        )

        outcome = scene.results["a"]
        assert (outcome.applied, outcome.unapplied_reason) == (False, "not_applicable")
        assert after_a == after_change


def _freeze_answer():
    from services.variant_answer import SchemaAnswer
    from services.variant_request import SchemaParameterIn

    return SchemaAnswer(
        parameters=(SchemaParameterIn(ordinal=1, name="Толщина", values=("50 мм", "100 мм")),)
    )


class TestFreezeSchemaDuplicateResultUnderLocks:
    """Дубль результата той же версии (Review Focus 5): пока A стоит на
    барьере, B настоящим `freeze_schema` замораживает ту же версию и
    коммитит. A под блокировками видит версию уже `frozen` — исход
    `not_applicable`, версия одна, параметры записаны один раз."""

    def test_same_version_frozen_by_another_session_while_a_waits(
        self, committing_db, committing_factories, committing_session_factory
    ):
        world = _freeze_world(committing_db, committing_factories)
        job, guard = _schema_job(committing_db, world)
        committing_db.commit()
        scene = _Scene(committing_session_factory)

        def work(db):
            # Вызывающий уже держит версию в сессии (читал её до записи):
            # блокирующее чтение обязано её обновить.
            held = db.get(FamilyParameterSchema, world.building.id)  # noqa: F841 — держать ссылку
            return freeze_schema(
                db, schema_id=world.building.id, answer=_freeze_answer(), guard=guard,
                settings=_settings(),
            )

        with _barrier_before(work_variants_module, "_acquire_schema_locks", scene, "a"):
            try:
                scene.spawn("a", work)
                assert scene.wait_paused("a"), "A не дошла до барьера"
                with committing_session_factory() as other:
                    assert freeze_schema(
                        other, schema_id=world.building.id, answer=_freeze_answer(),
                        guard=None, settings=_settings(),
                    ).applied is True
                    other.commit()
                with committing_session_factory() as probe:
                    after_change = _schema_snapshot(probe)
            finally:
                scene.finish()
        scene.assert_clean()

        outcome = scene.results["a"]
        assert (outcome.applied, outcome.unapplied_reason) == (False, "not_applicable")
        with committing_session_factory() as probe:
            assert _schema_snapshot(probe) == after_change
            frozen = probe.execute(
                sa.select(FamilyParameterSchema.id).where(
                    FamilyParameterSchema.family_id == world.family.id,
                    FamilyParameterSchema.status == "frozen",
                )
            ).scalars().all()
            assert frozen == [world.building.id]
            job_row = probe.get(SemanticJob, job.id)
            assert (job_row.status, job_row.cancel_reason, job_row.claim_token) == (
                "cancelled", "not_applicable", None,
            )


# ---------------------------------------------------------------------------
#  Блокирующее чтение обновляет строки, которые сессия уже держит
# ---------------------------------------------------------------------------

def _reclaim(job_id):
    """Задание повторно захвачено другим обработчиком: новый токен, `running`."""
    import uuid

    token = uuid.uuid4()

    def _do(other):
        other.get(SemanticJob, job_id).claim_token = token

    return token, _do


class TestLockingReadsRefreshRowsTheSessionAlreadyHolds:
    """Вызывающий мог прочитать строки до записи (рендер, построение охраны):
    сессия A держит их в карте идентичности, другая сессия меняет и коммитит,
    затем A зовёт функцию. Решение обязано опираться на строку, прочитанную
    под блокировкой, а не на устаревшую копию (`populate_existing` у каждого
    блокирующего оператора) — ревью задачи 5. Ссылка на прочитанную строку
    держится до вызова: карта идентичности сессии слабая, и отпущенный объект
    не оставил бы в ней устаревшей копии."""

    def _values_case(self, factory, db, factories, preload, change, *, guarded=True):
        world = _world(db, factories)
        job, guard = _guarded(db, context_id=world.context_id, schema_id=world.schema.id)
        db.commit()
        with factory() as a:
            held = preload(a, world, job)  # noqa: F841 — карта идентичности слабая
            with factory() as other:
                change(other, world, job)
                other.commit()
            outcome = apply_values(
                a, context_id=world.context_id, schema_id=world.schema.id,
                answer=_answer(_named(1, "50 мм"), _named(2, "бетон")), paths_hash="p",
                guard=guard if guarded else None, settings=_settings(),
            )
            a.commit()
        return world, job, guard, outcome

    def test_reclaimed_job_is_lost_claim_and_the_new_owner_result_applies(
        self, committing_db, committing_factories, committing_session_factory
    ):
        token_holder: dict = {}

        def preload(a, world, job):
            return a.get(SemanticJob, job.id)

        def change(other, world, job):
            token, do = _reclaim(job.id)
            token_holder["token"] = token
            do(other)

        world, job, guard, outcome = self._values_case(
            committing_session_factory, committing_db, committing_factories, preload, change
        )
        assert (outcome.applied, outcome.unapplied_reason) == (False, "lost_claim")
        with committing_session_factory() as probe:
            row = probe.get(SemanticJob, job.id)
            assert (row.status, row.claim_token, row.result_suggestion_id) == (
                "running", token_holder["token"], None,
            )
            assert probe.get(CatalogContext, world.context_id).work_variant_id is None
        # Результат нового владельца применяется.
        from services.work_variants import JobGuard

        owner = JobGuard(
            job_id=job.id, claim_token=token_holder["token"],
            expected_request_hash=guard.expected_request_hash,
        )
        with committing_session_factory() as b:
            applied = apply_values(
                b, context_id=world.context_id, schema_id=world.schema.id,
                answer=_answer(_named(1, "50 мм"), _named(2, "бетон")),
                paths_hash=paths_hash_of(
                    load_values_material(b, [world.context_id])[world.context_id].paths
                ),
                guard=owner, settings=_settings(),
            )
            b.commit()
        assert applied.applied is True
        with committing_session_factory() as probe:
            row = probe.get(SemanticJob, job.id)
            assert (row.status, row.claim_token) == ("done", None)

    def test_catalog_row_marked_header_meanwhile_is_not_promoted(
        self, committing_db, committing_factories, committing_session_factory
    ):
        from models import CatalogPosition

        def preload(a, world, job):
            row = a.get(CatalogPosition, world.catalog_id)
            assert row.kind == "TO_REVIEW"
            return row

        def change(other, world, job):
            other.get(CatalogPosition, world.catalog_id).kind = "HEADER"

        world, job, _guard, outcome = self._values_case(
            committing_session_factory, committing_db, committing_factories, preload, change
        )
        assert (outcome.applied, outcome.unapplied_reason) == (False, "not_applicable")
        with committing_session_factory() as probe:
            assert probe.get(CatalogPosition, world.catalog_id).kind == "HEADER"
            assert probe.get(SemanticJob, job.id).cancel_reason == "not_applicable"

    def test_context_marked_not_work_meanwhile_is_not_applicable(
        self, committing_db, committing_factories, committing_session_factory
    ):
        def preload(a, world, job):
            return a.get(CatalogContext, world.context_id)

        def change(other, world, job):
            other.get(CatalogContext, world.context_id).semantic_state = "NOT_APPLICABLE"

        world, job, _guard, outcome = self._values_case(
            committing_session_factory, committing_db, committing_factories, preload, change
        )
        assert (outcome.applied, outcome.unapplied_reason) == (False, "not_applicable")
        with committing_session_factory() as probe:
            assert probe.get(CatalogContext, world.context_id).work_variant_id is None

    def test_schema_version_superseded_meanwhile_is_not_applicable(
        self, committing_db, committing_factories, committing_session_factory
    ):
        def preload(a, world, job):
            return a.get(FamilyParameterSchema, world.schema.id)

        def change(other, world, job):
            row = other.get(FamilyParameterSchema, world.schema.id)
            row.status = "superseded"
            row.superseded_at = sa.func.now()

        # Без охраны: с ней ту же версию отсёк бы рендер отпечатка (у семьи
        # нет текущей версии — `SubjectNotRenderable`), и устаревшая копия
        # версии в сессии не была бы видна.
        world, _job, _guard, outcome = self._values_case(
            committing_session_factory, committing_db, committing_factories, preload, change,
            guarded=False,
        )
        assert (outcome.applied, outcome.unapplied_reason) == (False, "not_applicable")
        with committing_session_factory() as probe:
            assert probe.get(CatalogContext, world.context_id).work_variant_id is None

    def test_reclaimed_schema_job_is_lost_claim(
        self, committing_db, committing_factories, committing_session_factory
    ):
        world = _freeze_world(committing_db, committing_factories)
        job, guard = _schema_job(committing_db, world)
        committing_db.commit()
        token, do = _reclaim(job.id)
        with committing_session_factory() as a:
            held = a.get(SemanticJob, job.id)  # noqa: F841 — держать ссылку
            with committing_session_factory() as other:
                do(other)
                other.commit()
            outcome = freeze_schema(
                a, schema_id=world.building.id, answer=_freeze_answer(), guard=guard,
                settings=_settings(),
            )
            a.commit()
        assert (outcome.applied, outcome.unapplied_reason) == (False, "lost_claim")
        with committing_session_factory() as probe:
            row = probe.get(SemanticJob, job.id)
            assert (row.status, row.claim_token) == ("running", token)
            assert probe.get(FamilyParameterSchema, world.building.id).status == "building"

    def test_variant_archived_meanwhile_is_reactivated_by_the_arriving_set(
        self, committing_db, committing_factories, committing_session_factory
    ):
        db, factories = committing_db, committing_factories
        world = _world(db, factories)
        mapping = {1: world.values[(1, "50 мм")], 2: world.values[(2, "бетон")]}
        variant = _variant(db, world.family, world.schema, values_key=f"1={mapping[1]}|2={mapping[2]}")
        db.commit()
        with committing_session_factory() as a:
            held = a.get(WorkVariant, variant.id)  # карта идентичности слабая: держать ссылку
            assert held.status == "active"
            with committing_session_factory() as other:
                assert archive_variant_if_empty(other, variant.id) is True
                other.commit()
            found, created = get_or_create_variant(
                a, family_id=world.family.id, schema_id=world.schema.id,
                value_ids_by_ordinal=mapping,
            )
            assert (found.id, created) == (variant.id, False)
            a.commit()
        assert _final_variant(committing_session_factory, variant.id)[0] == "active"

    def test_variant_archived_meanwhile_is_not_archived_twice(
        self, committing_db, committing_factories, committing_session_factory
    ):
        db, factories = committing_db, committing_factories
        world = _world(db, factories)
        variant = _variant(db, world.family, world.schema, values_key="once")
        db.commit()
        with committing_session_factory() as a:
            held = a.get(WorkVariant, variant.id)  # карта идентичности слабая: держать ссылку
            assert held.status == "active"
            with committing_session_factory() as other:
                assert archive_variant_if_empty(other, variant.id) is True
                other.commit()
            with committing_session_factory() as probe:
                first_archived_at = probe.get(WorkVariant, variant.id).archived_at
            assert archive_variant_if_empty(a, variant.id) is False
            a.commit()
        with committing_session_factory() as probe:
            assert probe.get(WorkVariant, variant.id).archived_at == first_archived_at

