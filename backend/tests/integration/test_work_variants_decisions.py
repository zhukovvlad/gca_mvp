"""Решения человека по предложениям идут через `request_family_change`, а их
блокировки берёт общий помощник захвата (спека
`2026-10-02-catalog-variants-design.md` §2.5, §2.6).

Маршруты `/suggestions/confirm`, `/suggestions/{id}/other-family`,
`/suggestions/{id}/create-family`: контекст с вариантом получает ожидание, без
варианта — назначение сразу и прежние решения. Гонки двух сессий проверяют
помощник захвата: барьер ПОСЛЕ чтения (`after_cursor_execute`, результат уже
у клиента), жёсткие таймауты у каждого потока и ожидания; снятая защита
краснеет ошибкой БД или `409`, а не зависанием.
"""
from __future__ import annotations

import re
import threading
import time
from decimal import Decimal
from unittest import mock

import pytest
import sqlalchemy as sa
from sqlalchemy import event

import services.semantic_decisions as decisions_module
from models import CatalogContext, FamilySuggestion, WorkFamily
from services.semantic_decisions import (
    ConfirmReport,
    DecisionConflict,
    assign_other_family,
    confirm_suggestions,
    create_family_from_suggestion,
    reject_suggestion,
)
from services.work_families import assign_family
from services.work_variants import apply_values
from tests.factories import seed_category_id
from tests.integration.test_semantic_queue_decisions import _active_family, _fresh
from tests.integration.test_work_families import (
    _RELEASE_TIMEOUT,
    _WITNESS_TIMEOUT,
    _backend_blocked_once,
    _terminate_backend,
)
from tests.integration.test_work_variants_concurrency import _Scene
from tests.integration.test_work_variants_core import _answer, _events
from tests.integration.test_work_variants_family_change import (
    _bind_source,
    _current_schema,
    _decision,
    _pending_columns,
    _pending_events,
    _publish,
    _two_families,
    _with_variant,
)
from tests.integration.test_work_variants_material import _settings

pytestmark = pytest.mark.integration

_LOCK_TIMEOUT = "20s"


def _family_by_name(db, title):
    return db.execute(sa.select(WorkFamily).where(WorkFamily.title == title)).scalar_one()


def _ctx(db, context_id) -> CatalogContext:
    return _fresh(db, CatalogContext, context_id)


# ---------------------------------------------------------------------------
#  Маршруты решений
# ---------------------------------------------------------------------------

class TestConfirmRoute:
    def test_a_context_with_a_variant_gets_a_pending_family_and_accepted_pending(
        self, db_session, factories
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        variant = _with_variant(db_session, scene, context_id, scene.family)
        suggestion = _publish(db_session, context_id, family_id=scene.family_b.id)

        report = confirm_suggestions(
            db_session, suggestion_ids=[suggestion.id], actor_id=scene.user.id
        )

        assert report == ConfirmReport(confirmed=[suggestion.id], skipped=[])
        context = _ctx(db_session, context_id)
        assert (context.work_family_id, context.work_variant_id) == (scene.family.id, variant.id)
        assert _pending_columns(context) == (
            scene.family_b.id, "suggestion", suggestion.id, None, None,
        )
        assert _decision(db_session, suggestion.id) == ("accepted_pending", scene.user.id)
        [(name, payload)] = _pending_events(db_session, context_id)
        assert (name, payload["suggestion_id"]) == ("set", suggestion.id)
        assert _events(db_session, "context_family_assigned", context_id=context_id) == []

    def test_a_context_without_a_variant_is_assigned_at_once_and_accepted(
        self, db_session, factories
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _bind_source(db_session, scene, context_id, scene.family)
        suggestion = _publish(db_session, context_id, family_id=scene.family_b.id)

        report = confirm_suggestions(
            db_session, suggestion_ids=[suggestion.id], actor_id=scene.user.id
        )

        assert report.confirmed == [suggestion.id]
        context = _ctx(db_session, context_id)
        assert (context.work_family_id, context.family_source) == (scene.family_b.id, "suggestion")
        assert _pending_columns(context) == (None, None, None, None, None)
        assert _decision(db_session, suggestion.id) == ("accepted", scene.user.id)

    def test_confirming_the_current_family_of_a_variant_context_is_accepted_not_pending(
        self, db_session, factories
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)
        suggestion = _publish(db_session, context_id, family_id=scene.family.id)

        confirm_suggestions(db_session, suggestion_ids=[suggestion.id], actor_id=scene.user.id)

        assert _decision(db_session, suggestion.id) == ("accepted", scene.user.id)
        assert _pending_columns(_ctx(db_session, context_id)) == (None, None, None, None, None)

    def test_a_group_mixes_a_pending_family_and_an_immediate_change(self, db_session, factories):
        scene = _two_families(db_session, factories, titles=("Пол А", "Пол Б"))
        with_variant, without = scene.context_ids
        _with_variant(db_session, scene, with_variant, scene.family)
        _bind_source(db_session, scene, without, scene.family)
        first = _publish(db_session, with_variant, family_id=scene.family_b.id)
        second = _publish(db_session, without, family_id=scene.family_b.id)

        report = confirm_suggestions(
            db_session, suggestion_ids=[first.id, second.id], actor_id=scene.user.id
        )

        assert sorted(report.confirmed) == sorted([first.id, second.id])
        assert _decision(db_session, first.id)[0] == "accepted_pending"
        assert _decision(db_session, second.id)[0] == "accepted"
        assert _ctx(db_session, with_variant).pending_family_id == scene.family_b.id
        assert _ctx(db_session, without).work_family_id == scene.family_b.id

    def test_the_confirmation_goes_through_request_family_change(self, db_session, factories):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        suggestion = _publish(db_session, context_id, family_id=scene.family_b.id)

        with mock.patch.object(
            decisions_module, "request_family_change", wraps=decisions_module.request_family_change
        ) as spy:
            confirm_suggestions(db_session, suggestion_ids=[suggestion.id], actor_id=scene.user.id)

        assert spy.call_count == 1
        assert spy.call_args.kwargs["source"] == "suggestion"

    def test_a_pending_family_is_applied_by_the_values_and_the_decision_becomes_accepted(
        self, db_session, factories
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _with_variant(db_session, scene, context_id, scene.family)
        suggestion = _publish(db_session, context_id, family_id=scene.family_b.id)
        confirm_suggestions(db_session, suggestion_ids=[suggestion.id], actor_id=scene.user.id)

        outcome = apply_values(
            db_session, context_id=context_id,
            schema_id=_current_schema(db_session, scene.family_b).id, answer=_answer(),
            paths_hash="paths-1", guard=None, settings=_settings(),
        )

        assert outcome.applied and outcome.family_switched
        assert _decision(db_session, suggestion.id) == ("accepted", scene.user.id)
        context = _ctx(db_session, context_id)
        assert (context.work_family_id, context.family_source) == (scene.family_b.id, "suggestion")


class TestOtherFamilyRoute:
    def test_a_context_with_a_variant_gets_a_manual_pending_family(self, db_session, factories):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        variant = _with_variant(db_session, scene, context_id, scene.family)
        suggestion = _publish(db_session, context_id, family_id=scene.family_b.id)

        assign_other_family(
            db_session, suggestion_id=suggestion.id, family_id=scene.family_c.id,
            actor_id=scene.user.id,
        )

        context = _ctx(db_session, context_id)
        assert (context.work_family_id, context.work_variant_id) == (scene.family.id, variant.id)
        assert _pending_columns(context) == (
            scene.family_c.id, "manual", None, scene.user.id, None,
        )
        assert _decision(db_session, suggestion.id) == ("other_family", scene.user.id)

    def test_a_context_without_a_variant_is_assigned_manually_at_once(self, db_session, factories):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        _bind_source(db_session, scene, context_id, scene.family)
        suggestion = _publish(db_session, context_id, family_id=scene.family_b.id)

        assign_other_family(
            db_session, suggestion_id=suggestion.id, family_id=scene.family_c.id,
            actor_id=scene.user.id,
        )

        context = _ctx(db_session, context_id)
        assert (context.work_family_id, context.family_source, context.family_by) == (
            scene.family_c.id, "manual", scene.user.id,
        )
        assert _decision(db_session, suggestion.id) == ("other_family", scene.user.id)

    def test_the_other_family_goes_through_request_family_change(self, db_session, factories):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        suggestion = _publish(db_session, context_id, family_id=scene.family_b.id)

        with mock.patch.object(
            decisions_module, "request_family_change", wraps=decisions_module.request_family_change
        ) as spy:
            assign_other_family(
                db_session, suggestion_id=suggestion.id, family_id=scene.family_c.id,
                actor_id=scene.user.id,
            )

        assert spy.call_count == 1
        assert spy.call_args.kwargs["source"] == "manual"


class TestCreateFamilyRoute:
    def test_a_context_with_a_variant_gets_a_pending_family_on_the_new_one(
        self, db_session, factories
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        variant = _with_variant(db_session, scene, context_id, scene.family)
        suggestion = _publish(db_session, context_id, family_id=None)

        new_id = create_family_from_suggestion(
            db_session, suggestion_id=suggestion.id, title="Новая семья пола",
            definition="Определение новой семьи", actor_id=scene.user.id,
            family_category_id=seed_category_id(db_session),
        )

        context = _ctx(db_session, context_id)
        assert (context.work_family_id, context.work_variant_id) == (scene.family.id, variant.id)
        assert _pending_columns(context) == (new_id, "suggestion", suggestion.id, None, None)
        assert _decision(db_session, suggestion.id) == ("family_created", scene.user.id)
        assert db_session.get(WorkFamily, new_id).status == "active"

    def test_a_context_without_a_variant_is_assigned_to_the_new_family_at_once(
        self, db_session, factories
    ):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        suggestion = _publish(db_session, context_id, family_id=None)

        new_id = create_family_from_suggestion(
            db_session, suggestion_id=suggestion.id, title="Новая семья пола",
            definition="Определение новой семьи", actor_id=scene.user.id,
            family_category_id=seed_category_id(db_session),
        )

        context = _ctx(db_session, context_id)
        assert (context.work_family_id, context.family_source) == (new_id, "suggestion")
        assert _pending_columns(context) == (None, None, None, None, None)
        assert _decision(db_session, suggestion.id) == ("family_created", scene.user.id)

    def test_the_creation_goes_through_request_family_change(self, db_session, factories):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        suggestion = _publish(db_session, context_id, family_id=None)

        with mock.patch.object(
            decisions_module, "request_family_change", wraps=decisions_module.request_family_change
        ) as spy:
            create_family_from_suggestion(
                db_session, suggestion_id=suggestion.id, title="Новая семья пола",
                definition="Определение новой семьи", actor_id=scene.user.id,
                family_category_id=seed_category_id(db_session),
            )

        assert spy.call_count == 1
        assert spy.call_args.kwargs["suggestion_id"] == suggestion.id


class TestRejectKeepsTheFamily:
    def test_the_rejection_of_a_variant_context_changes_no_family(self, db_session, factories):
        scene = _two_families(db_session, factories)
        context_id = scene.context_ids[0]
        variant = _with_variant(db_session, scene, context_id, scene.family)
        suggestion = _publish(db_session, context_id, family_id=scene.family_b.id)

        reject_suggestion(db_session, suggestion_id=suggestion.id, actor_id=scene.user.id)

        context = _ctx(db_session, context_id)
        assert (context.work_family_id, context.work_variant_id) == (scene.family.id, variant.id)
        assert _pending_columns(context) == (None, None, None, None, None)
        assert _decision(db_session, suggestion.id) == ("rejected", scene.user.id)


# ---------------------------------------------------------------------------
#  Помощник захвата: порядок блокировок группы
# ---------------------------------------------------------------------------

def _lock_trace(db, action):
    """`[(таблица, режим, значения параметров, текст)]` каждого `SELECT ... FOR UPDATE |
    FOR SHARE`, ушедшего в базу, в порядке исполнения: проверяется SQL, а не
    намерение вызова (`docs/pitfalls/db.md`)."""
    seen: list[tuple[str, str, tuple, str]] = []
    engine = db.get_bind().engine

    def _listener(conn, cursor, statement, parameters, context, executemany):
        text = " ".join(statement.split())
        if " FOR UPDATE" in text or " FOR SHARE" in text:
            table = re.search(r" FROM (\w+)", text).group(1)
            values = (
                tuple(parameters.values()) if isinstance(parameters, dict) else tuple(parameters)
            )
            seen.append((table, "SHARE" if " FOR SHARE" in text else "UPDATE", values, text))

    event.listen(engine, "before_cursor_execute", _listener)
    try:
        action()
    finally:
        event.remove(engine, "before_cursor_execute", _listener)
    return seen


class TestGroupLockOrder:
    def test_a_group_locks_all_families_then_all_contexts_each_in_ascending_id_order(
        self, db_session, factories
    ):
        scene = _two_families(db_session, factories, titles=("Пол А", "Пол Б", "Пол В"))
        a, b, c = scene.context_ids
        _bind_source(db_session, scene, a, scene.family_c)
        _bind_source(db_session, scene, b, scene.family)
        suggestions = [
            _publish(db_session, c, family_id=scene.family_c.id),
            _publish(db_session, b, family_id=scene.family_b.id),
            _publish(db_session, a, family_id=scene.family_b.id),
        ]
        ids = [s.id for s in suggestions]

        trace = _lock_trace(
            db_session,
            lambda: confirm_suggestions(db_session, suggestion_ids=ids, actor_id=scene.user.id),
        )

        family_ids = sorted({scene.family.id, scene.family_b.id, scene.family_c.id})
        assert trace[0][:2] == ("work_families", "SHARE")
        assert sorted(trace[0][2]) == family_ids
        assert "ORDER BY work_families.id" in trace[0][3]
        assert trace[1][:2] == ("catalog_contexts", "UPDATE")
        assert sorted(trace[1][2]) == sorted([a, b, c])
        assert "ORDER BY catalog_contexts.id" in trace[1][3]
        assert trace[2][:2] == ("family_suggestions", "UPDATE")
        # Один захват на группу: больше ни одной блокировки семей до контекстов.
        assert [t[0] for t in trace[:2]] == ["work_families", "catalog_contexts"]


# ---------------------------------------------------------------------------
#  Помощник захвата: гонки
# ---------------------------------------------------------------------------

def _is_pairs_read(statement: str) -> bool:
    """Чтение семей контекстов без блокировок (первый шаг захвата)."""
    return (
        re.search(r"catalog_contexts\.pending_family_id\s+FROM catalog_contexts", statement)
        is not None
        and "FOR " not in statement
    )


def _is_context_lock(statement: str) -> bool:
    return "FROM catalog_contexts" in statement and "FOR UPDATE" in statement


class _Gates:
    """Барьеры потока по счётчику событий: `readN` — после N-го чтения пар
    семей (каждая попытка читает их дважды: до блокировок и под ними, поэтому
    чтение до блокировок второй попытки — `read3`); `lockN` — после N-й
    блокировки контекстов. Поток встаёт ПОСЛЕ получения результата и ждёт `go`."""

    def __init__(self, *names: str) -> None:
        self.reached = {name: threading.Event() for name in names}
        self.go = {name: threading.Event() for name in names}
        self._reads = 0
        self._locks = 0

    def listener(self, conn, cursor, statement, parameters, context, executemany) -> None:
        name = None
        if _is_pairs_read(statement):
            self._reads += 1
            name = f"read{self._reads}"
        elif _is_context_lock(statement):
            self._locks += 1
            name = f"lock{self._locks}"
        if name in self.reached:
            self.reached[name].set()
            if not self.go[name].wait(timeout=_RELEASE_TIMEOUT):
                raise AssertionError(f"{name}: release не пришёл вовремя")

    def release_all(self) -> None:
        for gate in self.go.values():
            gate.set()


class _DecisionRun:
    """Решение в потоке: сессия остаётся открытой после ответа, пока тест не
    снимет свои наблюдения (`finish_probe`) — так проверяется, что блокировки
    сняты откатом к точке сохранения, а не коммитом."""

    def __init__(self, factory, gates: _Gates, work, *, name="decision") -> None:
        self.factory = factory
        self.gates = gates
        self.work = work
        self.name = name
        self.pid: int | None = None
        self.outcome: object = None
        self.error: str | None = None
        self.responded = threading.Event()
        self.finish_probe = threading.Event()
        self.thread = threading.Thread(target=self._body, name=name, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _body(self) -> None:
        try:
            with self.factory() as db:
                db.execute(sa.text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
                self.pid = db.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                event.listen(db.connection(), "after_cursor_execute", self.gates.listener)
                try:
                    self.outcome = self.work(db)
                except DecisionConflict as exc:
                    self.outcome = f"conflict:{exc.code}"
                self.responded.set()
                self.finish_probe.wait(timeout=_RELEASE_TIMEOUT)
                db.commit()
        except Exception as exc:  # noqa: BLE001 — DeadlockDetected и прочее
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.responded.set()

    def stop(self, factory) -> None:
        self.gates.release_all()
        self.finish_probe.set()
        self.thread.join(timeout=_RELEASE_TIMEOUT)
        if self.thread.is_alive():
            _terminate_backend(factory, self.pid)
            self.thread.join(timeout=_WITNESS_TIMEOUT)


def _barrier_scene(db, factories, *, extra_titles=()):
    """Контекст K на семье A без варианта; семьи B (цель решения), C и E;
    предложение на семью B. Все семьи заводятся ДО предложения: отпечаток
    контекста зависит от списка кандидатов."""
    scene = _two_families(db, factories, titles=("Устройство пола", *extra_titles))
    scene.family_e = _active_family(db, title="Семья E", unit_name="M2", actor_id=scene.user.id)
    context_id = scene.context_ids[0]
    _bind_source(db, scene, context_id, scene.family)
    scene.suggestion = _publish(db, context_id, family_id=scene.family_b.id)
    db.commit()
    return scene


def _change_family(factory, context_id, family_id, user_id):
    with factory() as other:
        assign_family(other, context_id=context_id, family_id=family_id, actor_id=user_id)
        other.commit()


def _decide(scene, user_id):
    suggestion_id, family_id = scene.suggestion.id, scene.family_b.id

    def work(db):
        assign_other_family(
            db, suggestion_id=suggestion_id, family_id=family_id, actor_id=user_id
        )
        return "decided"

    return work


class TestDecisionVersusValuesHandler:
    def test_a_confirmation_in_parallel_with_the_handler_holding_both_families_has_no_deadlock(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Подтверждение A -> B на контексте с вариантом параллельно с
        обработчиком значений, берущим семьи `FOR UPDATE`. Решение держит ВСЕ
        свои семьи `FOR SHARE` до контекста: обработчик встаёт на первой же,
        а не между ними. Решение, взявшее одну целевую семью и добравшее
        текущую при удержании контекста, замыкает цикл."""
        db, factories = committing_db, committing_factories
        scene = _two_families(db, factories)
        context_id = scene.context_ids[0]
        _with_variant(db, scene, context_id, scene.family)
        context = _ctx(db, context_id)
        context.pending_family_id = scene.family_b.id
        context.pending_family_source = "manual"
        context.pending_by = scene.user.id
        context.pending_at = sa.func.now()
        suggestion = _publish(db, context_id, family_id=scene.family_b.id)
        scene.suggestion = suggestion
        db.commit()
        assert scene.family.id < scene.family_b.id
        schema_b_id = _current_schema(db, scene.family_b).id
        gates = _Gates("lock1")
        run = _DecisionRun(committing_session_factory, gates, _decide(scene, scene.user.id))
        handler = _Scene(committing_session_factory)

        def apply(session):
            return apply_values(
                session, context_id=context_id, schema_id=schema_b_id, answer=_answer(),
                paths_hash="paths-1", guard=None, settings=_settings(),
            )

        try:
            run.start()
            assert gates.reached["lock1"].wait(timeout=_WITNESS_TIMEOUT), "решение не взяло контекст"
            handler.spawn("handler", apply)
            assert handler.wait_pid("handler")
            blocked = False
            deadline = time.monotonic() + _WITNESS_TIMEOUT
            while time.monotonic() < deadline and not blocked:
                blocked = _backend_blocked_once(
                    committing_session_factory, pid=handler.pids["handler"], contains="work_families"
                )
                time.sleep(0.05)
            gates.release_all()
            run.finish_probe.set()
            run.thread.join(timeout=_RELEASE_TIMEOUT)
        finally:
            run.stop(committing_session_factory)
            handler.finish()

        handler.assert_clean()
        assert run.error is None, run.error
        assert not run.thread.is_alive()
        assert blocked, "обработчик не встал на семью, которую держит решение"
        assert run.outcome == "decided"


class TestFamilyChangedBetweenReadAndLock:
    def test_the_decision_rolls_back_to_the_savepoint_and_retakes_everything(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Семья контекста сменилась между чтением и блокировкой: решение
        держит прежний набор и контекст, обработчик значений уже взял новую
        семью `FOR UPDATE` и ждёт контекст. После отпускания решение
        откатывается к точке сохранения и берёт всё заново: обработчик
        заканчивается, повтор берёт новую семью и контекст следом. Откат
        заменён повтором поверх удерживаемых блокировок — цикл."""
        scene = _barrier_scene(committing_db, committing_factories)
        context_id = scene.context_ids[0]
        schema_c_id = _current_schema(committing_db, scene.family_c).id
        family_c_id, user_id = scene.family_c.id, scene.user.id
        gates = _Gates("read1", "lock1")
        run = _DecisionRun(committing_session_factory, gates, _decide(scene, user_id))
        handler = _Scene(committing_session_factory)

        def apply(session):
            return apply_values(
                session, context_id=context_id, schema_id=schema_c_id, answer=_answer(),
                paths_hash="paths-1", guard=None, settings=_settings(),
            )

        settled = None
        try:
            run.start()
            assert gates.reached["read1"].wait(timeout=_WITNESS_TIMEOUT), "нет первого чтения"
            _change_family(committing_session_factory, context_id, family_c_id, user_id)
            gates.go["read1"].set()
            assert gates.reached["lock1"].wait(timeout=_WITNESS_TIMEOUT), "решение не взяло контекст"
            handler.spawn("handler", apply)
            settled = handler.wait_settled("handler", contains="catalog_contexts")
            gates.go["lock1"].set()
            run.finish_probe.set()
            run.thread.join(timeout=_RELEASE_TIMEOUT)
        finally:
            run.stop(committing_session_factory)
            handler.finish()

        handler.assert_clean()
        assert run.error is None, run.error
        assert not run.thread.is_alive()
        assert settled == "blocked", f"обработчик не встал на контекст: {settled}"
        assert run.outcome in ("decided", "conflict:suggestion_changed")
        assert handler.results["handler"].applied is True


class TestFamilyChangedAgainAfterTheRetry:
    def _changes_twice(self, factory, scene, context_id, gates):
        """Между чтением и блокировкой каждой из двух попыток семья контекста
        меняется параллельной сессией: после чтения первой попытки (`read1`) и
        после чтения второй, до её блокировок (`read3`)."""
        assert gates.reached["read1"].wait(timeout=_WITNESS_TIMEOUT), "нет первого чтения"
        _change_family(factory, context_id, scene.family_c.id, scene.user.id)
        gates.go["read1"].set()
        assert gates.reached["read3"].wait(timeout=_WITNESS_TIMEOUT), "нет чтения второй попытки"
        _change_family(factory, context_id, scene.family_e.id, scene.user.id)
        gates.go["read3"].set()

    def test_a_single_decision_answers_409_records_nothing_and_releases_the_locks(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _barrier_scene(committing_db, committing_factories)
        context_id = scene.context_ids[0]
        gates = _Gates("read1", "read3")
        run = _DecisionRun(committing_session_factory, gates, _decide(scene, scene.user.id))
        free = None
        try:
            run.start()
            self._changes_twice(committing_session_factory, scene, context_id, gates)
            assert run.responded.wait(timeout=_WITNESS_TIMEOUT), "решение не ответило"
            with committing_session_factory() as probe:
                probe.execute(sa.text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
                try:
                    probe.execute(
                        sa.text(
                            "SELECT id FROM catalog_contexts WHERE id = :id FOR UPDATE NOWAIT"
                        ),
                        {"id": context_id},
                    )
                    probe.execute(
                        sa.text(
                            "SELECT id FROM work_families WHERE id = ANY(:ids) FOR UPDATE NOWAIT"
                        ),
                        {"ids": [scene.family.id, scene.family_b.id, scene.family_c.id]},
                    )
                    free = True
                except sa.exc.OperationalError:
                    free = False
                finally:
                    probe.rollback()
        finally:
            run.stop(committing_session_factory)

        assert run.error is None, run.error
        assert run.outcome == "conflict:suggestion_changed"
        assert free is True, "блокировки попытки не сняты после 409"
        with committing_session_factory() as probe:
            suggestion = probe.get(FamilySuggestion, scene.suggestion.id)
            assert suggestion.decision is None
            assert probe.get(CatalogContext, context_id).work_family_id == scene.family_e.id

    def test_a_group_skips_the_suggestion_of_the_unstable_context_and_confirms_the_rest(
        self, committing_session_factory, committing_db, committing_factories
    ):
        db, factories = committing_db, committing_factories
        scene = _two_families(db, factories, titles=("Пол А", "Пол Б"))
        scene.family_e = _active_family(db, title="Семья E", unit_name="M2", actor_id=scene.user.id)
        first, second = scene.context_ids
        _bind_source(db, scene, first, scene.family)
        _bind_source(db, scene, second, scene.family)
        unstable = _publish(db, first, family_id=scene.family_b.id)
        stable = _publish(db, second, family_id=scene.family_b.id)
        db.commit()
        gates = _Gates("read1", "read3")

        def work(session):
            return confirm_suggestions(
                session, suggestion_ids=[unstable.id, stable.id], actor_id=scene.user.id
            )

        run = _DecisionRun(committing_session_factory, gates, work)
        try:
            run.start()
            self._changes_twice(committing_session_factory, scene, first, gates)
            run.finish_probe.set()
            assert run.responded.wait(timeout=_WITNESS_TIMEOUT), "группа не ответила"
            run.thread.join(timeout=_RELEASE_TIMEOUT)
        finally:
            run.stop(committing_session_factory)

        assert run.error is None, run.error
        assert not run.thread.is_alive()
        assert run.outcome == ConfirmReport(confirmed=[stable.id], skipped=[unstable.id])
        with committing_session_factory() as probe:
            assert probe.get(FamilySuggestion, unstable.id).decision is None
            assert probe.get(FamilySuggestion, stable.id).decision == "accepted"
            assert probe.get(CatalogContext, second).work_family_id == scene.family_b.id
            assert probe.get(CatalogContext, first).work_family_id == scene.family_e.id


# ---------------------------------------------------------------------------
#  Ревью задачи 8: повтор захвата, правила при нестабильной семье, группа
# ---------------------------------------------------------------------------

class TestOneChangeBetweenReadAndLockIsRetaken:
    def test_a_single_change_is_retaken_by_the_retry_and_the_decision_succeeds(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Семья контекста сменилась ровно один раз между чтением и блокировкой:
        повтор перечитывает и перезахватывает, решение записывается. Без
        повтора та же смена давала бы `409` — повтор и есть то, что отличает
        один сдвиг от двух."""
        scene = _barrier_scene(committing_db, committing_factories)
        context_id = scene.context_ids[0]
        gates = _Gates("read1")
        run = _DecisionRun(committing_session_factory, gates, _decide(scene, scene.user.id))
        try:
            run.start()
            assert gates.reached["read1"].wait(timeout=_WITNESS_TIMEOUT), "нет первого чтения"
            _change_family(committing_session_factory, context_id, scene.family_c.id, scene.user.id)
            gates.go["read1"].set()
            run.finish_probe.set()
            assert run.responded.wait(timeout=_WITNESS_TIMEOUT), "решение не ответило"
            run.thread.join(timeout=_RELEASE_TIMEOUT)
        finally:
            run.stop(committing_session_factory)

        assert run.error is None, run.error
        assert run.outcome == "decided"
        assert gates._reads >= 3, "повтор не перечитал семьи"
        with committing_session_factory() as probe:
            assert probe.get(FamilySuggestion, scene.suggestion.id).decision == "other_family"
            context = probe.get(CatalogContext, context_id)
            assert (context.work_family_id, context.family_source) == (scene.family_b.id, "manual")


class TestRulesAgainstAFamilyChangedTwice:
    def test_the_rules_leave_the_suggestion_untouched_and_raise_nothing(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Правила публикации на том же помощнике захвата: семья контекста
        сменилась и после повтора — правило ничего не пишет и не падает
        (`None`), предложение остаётся опубликованным человеку."""
        from services.family_change import Thresholds, apply_publication_rules

        db, factories = committing_db, committing_factories
        scene = _two_families(db, factories)
        scene.family_e = _active_family(db, title="Семья E", unit_name="M2", actor_id=scene.user.id)
        context_id = scene.context_ids[0]
        suggestion = _publish(db, context_id, family_id=scene.family_b.id)
        db.commit()
        suggestion_id = suggestion.id
        gates = _Gates("read1", "read3")
        run = _DecisionRun(
            committing_session_factory, gates,
            lambda session: apply_publication_rules(
                session, suggestion_id=suggestion_id, thresholds=Thresholds(Decimal("0.80"), None)
            ),
            name="rules",
        )
        try:
            run.start()
            assert gates.reached["read1"].wait(timeout=_WITNESS_TIMEOUT), "нет первого чтения"
            _change_family(committing_session_factory, context_id, scene.family_c.id, scene.user.id)
            gates.go["read1"].set()
            assert gates.reached["read3"].wait(timeout=_WITNESS_TIMEOUT), "нет чтения повтора"
            _change_family(committing_session_factory, context_id, scene.family_e.id, scene.user.id)
            gates.go["read3"].set()
            run.finish_probe.set()
            assert run.responded.wait(timeout=_WITNESS_TIMEOUT), "правила не ответили"
            run.thread.join(timeout=_RELEASE_TIMEOUT)
        finally:
            run.stop(committing_session_factory)

        assert run.error is None, run.error
        assert run.outcome is None
        with committing_session_factory() as probe:
            row = probe.get(FamilySuggestion, suggestion_id)
            assert (row.decision, row.is_published) == (None, True)
            assert probe.get(CatalogContext, context_id).work_family_id == scene.family_e.id


class _SuggestionGates(_Gates):
    """`_Gates` плюс барьер `sugN` — после N-й блокировки предложения."""

    def __init__(self, *names: str) -> None:
        super().__init__(*names)
        self._suggestions = 0

    def listener(self, conn, cursor, statement, parameters, context, executemany) -> None:
        if "FROM family_suggestions" in statement and "FOR UPDATE" in statement:
            self._suggestions += 1
            name = f"sug{self._suggestions}"
            if name in self.reached:
                self.reached[name].set()
                if not self.go[name].wait(timeout=_RELEASE_TIMEOUT):
                    raise AssertionError(f"{name}: release не пришёл вовремя")
            return
        super().listener(conn, cursor, statement, parameters, context, executemany)


class TestGroupKeepsItsLocksAfterAnUnstableContext:
    def test_the_rest_of_the_group_is_rechecked_under_its_context_lock(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Группа не устоялась на одном контексте и после повтора: этот контекст
        пропускается, а прочие перепроверяются ПОД своими блокировками
        контекстов — захват группы не снимается из-за чужого контекста."""
        db, factories = committing_db, committing_factories
        scene = _two_families(db, factories, titles=("Пол А", "Пол Б"))
        scene.family_e = _active_family(db, title="Семья E", unit_name="M2", actor_id=scene.user.id)
        first, second = scene.context_ids
        _bind_source(db, scene, first, scene.family)
        _bind_source(db, scene, second, scene.family)
        unstable = _publish(db, first, family_id=scene.family_b.id)
        stable = _publish(db, second, family_id=scene.family_b.id)
        db.commit()
        gates = _SuggestionGates("read1", "read3", "sug1")

        def work(session):
            return confirm_suggestions(
                session, suggestion_ids=[unstable.id, stable.id], actor_id=scene.user.id
            )

        run = _DecisionRun(committing_session_factory, gates, work)
        held = None
        try:
            run.start()
            TestFamilyChangedAgainAfterTheRetry()._changes_twice(
                committing_session_factory, scene, first, gates
            )
            assert gates.reached["sug1"].wait(timeout=_WITNESS_TIMEOUT), "нет перепроверки"
            with committing_session_factory() as probe:
                try:
                    probe.execute(
                        sa.text("SELECT id FROM catalog_contexts WHERE id = :id FOR UPDATE NOWAIT"),
                        {"id": second},
                    )
                    held = False
                except sa.exc.OperationalError:
                    held = True
                finally:
                    probe.rollback()
            gates.release_all()
            run.finish_probe.set()
            assert run.responded.wait(timeout=_WITNESS_TIMEOUT), "группа не ответила"
            run.thread.join(timeout=_RELEASE_TIMEOUT)
        finally:
            run.stop(committing_session_factory)

        assert run.error is None, run.error
        assert held is True, "перепроверка прочих предложений группы идёт без блокировки контекста"
        assert run.outcome == ConfirmReport(confirmed=[stable.id], skipped=[unstable.id])
