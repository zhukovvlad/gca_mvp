"""Поток-опросчик очереди, восстановление и `lifespan` (задача 11 фичи
«Семантические предложения»; спека §2.5).

Настоящие потоки на `committing_session_factory` и фейковый клиент модели, без
сети. Каждое ожидание ограничено таймаутом: зависший поток не должен вешать
прогон. Помощники — локальная копия помощников соседних наборов, не импорт.
"""
from __future__ import annotations

import datetime as dt
import threading
import time
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

import services.semantic_runner as runner_module
from config import settings as app_settings
from models import (
    ImportJob,
    ImportJobStatus,
    SemanticJob,
    SemanticJobAttempt,
)
from services.context_routing import route_position
from services.maintenance import run_startup_maintenance
from services.semantic_client import ModelResponse
from services.semantic_request import load_request_material, render_context_request
from services.semantic_runner import SemanticRunner, recover_semantic_jobs
from services.work_families import activate_family, create_family

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 9, 29, 12, 0, tzinfo=dt.UTC)
_WAIT_S = 30.0


def _settings(**over):
    return app_settings.model_copy(
        update={
            "SEMANTIC_AUTO_ACCEPT_THRESHOLD": None,
            "SEMANTIC_CONCURRENCY": 2,
            "SEMANTIC_CALL_TIMEOUT_S": 5,
            "SEMANTIC_DAILY_BUDGET_USD": Decimal("1000"),
            **over,
        }
    )


def _threads(prefix="semantic-runner"):
    return [t for t in threading.enumerate() if t.name.startswith(prefix)]


def _wait_until(predicate, timeout=_WAIT_S):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _answer(family_id: int) -> str:
    return (
        f'{{"family_id": {family_id}, "new_family_name": null, '
        '"confidence": 0.9, "reason": "проверочная причина"}'
    )


class _Client:
    """Фейковый клиент: считает вызовы и одновременные вызовы под замком."""

    def __init__(
        self,
        family_id,
        *,
        hold_s=0.0,
        gate: threading.Event | None = None,
        barrier: threading.Barrier | None = None,
    ):
        self.family_id = family_id
        self.hold_s = hold_s
        self.gate = gate
        self.barrier = barrier
        self.lock = threading.Lock()
        self.calls = 0
        self.active = 0
        self.max_active = 0

    def complete(self, body, *, timeout_s):
        with self.lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.gate is not None:
                self.gate.wait(_WAIT_S)
            if self.barrier is not None:
                # Вызов не завершается, пока столько же вызовов не идут разом:
                # перекрытие принуждено, а не оставлено планировщику.
                self.barrier.wait()
            time.sleep(self.hold_s)
            return ModelResponse(
                content=_answer(self.family_id),
                actual_model="anthropic/claude-test",
                provider="P1",
                prompt_tokens=100,
                completion_tokens=10,
                cache_write_tokens=0,
                cached_tokens=0,
                cost_usd=Decimal("0.01"),
            )
        finally:
            with self.lock:
                self.active -= 1


def _scene(db, factories, count):
    """Активная семья единицы и `count` контекстов с заданиями `pending`."""
    from services.unit_resolution import UnitResolver

    user = factories.UserFactory.create()
    unit_id = UnitResolver(db).resolve("M2").unit_id
    fam = create_family(
        db, title="Семья пола", unit_name="M2", definition="Определение", actor_id=user.id
    )
    family = activate_family(db, family_id=fam.id, actor_id=user.id)
    estimate = factories.EstimateFactory.create()
    proposal = factories.ProposalFactory.create(lot=factories.LotFactory.create(estimate=estimate))
    job_ids = []
    for n in range(count):
        title = f"Устройство пола {n}"
        cp = factories.CatalogPositionFactory.create(unit_id=unit_id, standard_job_title=title)
        position = factories.PositionItemFactory.create(
            proposal=proposal, is_chapter=False, job_title_in_proposal=title,
            catalog_position_id=cp.id,
        )
        context_id = route_position(db, position_item_id=position.id).context_id
        rendered = render_context_request(
            load_request_material(db, [context_id])[context_id], settings=app_settings
        )
        job = SemanticJob(
            context_id=context_id, request_hash=rendered.request_hash, status="pending",
            unit_id=unit_id, next_attempt_at=NOW, prompt_version="1",
            model_requested=app_settings.SEMANTIC_MODEL,
            place_dictionary_version=rendered.place_dictionary_version,
            candidates_hash=rendered.candidates_hash, prefix_hash=rendered.prefix_hash,
            input_hash=rendered.input_hash, response_schema_version="1",
            serialization_version="1",
        )
        db.add(job)
        db.flush()
        job_ids.append(job.id)
    db.commit()
    return family, job_ids


def _statuses(factory, job_ids):
    with factory() as db:
        rows = db.execute(
            sa.select(SemanticJob.id, SemanticJob.status).where(SemanticJob.id.in_(job_ids))
        ).all()
    return dict(rows)


def _running_job_with_attempt(db, context_id, *, finished=False):
    """Задание `running` с попыткой; `finished` — попытка уже закрыта."""
    token = uuid.uuid4()
    job = SemanticJob(
        context_id=context_id, request_hash=f"h-{uuid.uuid4()}", status="running",
        claim_token=token, prompt_version="1", model_requested="m",
        place_dictionary_version=1, candidates_hash="c", prefix_hash="p", input_hash="i",
        response_schema_version="1", serialization_version="1",
    )
    db.add(job)
    db.flush()
    attempt = SemanticJobAttempt(
        job_id=job.id, claim_token=token, retry_generation=0, started_at=NOW,
        reserve_usd=Decimal("0.01"), prefix_hash="p", privacy_dictionary_hash="d",
        finished_at=NOW if finished else None,
        outcome="ok" if finished else None,
    )
    db.add(attempt)
    db.flush()
    return job, attempt


@pytest.fixture
def stop_runners():
    runners = []
    yield runners
    for r in runners:
        r.stop(timeout_s=10)


# ---------------------------------------------------------------------------
#  Восстановление
# ---------------------------------------------------------------------------

class TestRecoverSemanticJobs:
    def test_running_jobs_return_to_pending_without_claim_token(
        self, committing_db, committing_factories
    ):
        _, job_ids = _scene(committing_db, committing_factories, 2)
        ctx = committing_db.get(SemanticJob, job_ids[0]).context_id
        job, _ = _running_job_with_attempt(committing_db, ctx)
        committing_db.commit()

        recover_semantic_jobs(committing_db, now=NOW)
        committing_db.commit()

        committing_db.expire_all()
        row = committing_db.get(SemanticJob, job.id)
        assert (row.status, row.claim_token) == ("pending", None)

    def test_open_attempt_is_closed_as_interrupted_transient_error(
        self, committing_db, committing_factories
    ):
        _, job_ids = _scene(committing_db, committing_factories, 1)
        ctx = committing_db.get(SemanticJob, job_ids[0]).context_id
        _, attempt = _running_job_with_attempt(committing_db, ctx)
        committing_db.commit()
        later = NOW + dt.timedelta(hours=1)

        recover_semantic_jobs(committing_db, now=later)
        committing_db.commit()

        committing_db.expire_all()
        row = committing_db.get(SemanticJobAttempt, attempt.id)
        assert (row.finished_at, row.outcome, row.error_class) == (
            later, "transient_error", "interrupted",
        )

    def test_returns_the_number_of_running_jobs_only(self, committing_db, committing_factories):
        _, job_ids = _scene(committing_db, committing_factories, 3)
        contexts = [committing_db.get(SemanticJob, j).context_id for j in job_ids]
        _running_job_with_attempt(committing_db, contexts[0])
        _running_job_with_attempt(committing_db, contexts[1])
        committing_db.commit()

        # Три задания `pending` из сцены и два `running`: считаются только вторые.
        assert recover_semantic_jobs(committing_db, now=NOW) == 2

    def test_pending_job_and_closed_attempt_are_left_alone(
        self, committing_db, committing_factories
    ):
        _, job_ids = _scene(committing_db, committing_factories, 2)
        ctx = committing_db.get(SemanticJob, job_ids[0]).context_id
        # Закрытая попытка у `pending`-задания (повтор после ошибки).
        old = SemanticJobAttempt(
            job_id=job_ids[0], claim_token=uuid.uuid4(), retry_generation=0, started_at=NOW,
            finished_at=NOW, outcome="transient_error", error_class="Timeout",
            reserve_usd=Decimal("0.01"), prefix_hash="p", privacy_dictionary_hash="d",
        )
        committing_db.add(old)
        committing_db.flush()
        running, closed_attempt = _running_job_with_attempt(committing_db, ctx, finished=True)
        committing_db.commit()

        assert recover_semantic_jobs(committing_db, now=NOW + dt.timedelta(hours=1)) == 1
        committing_db.commit()

        committing_db.expire_all()
        assert committing_db.get(SemanticJobAttempt, old.id).error_class == "Timeout"
        untouched = committing_db.get(SemanticJobAttempt, closed_attempt.id)
        assert (untouched.finished_at, untouched.outcome, untouched.error_class) == (
            NOW, "ok", None,
        )
        assert committing_db.get(SemanticJob, job_ids[1]).status == "pending"

    def test_jobs_in_other_statuses_and_their_open_attempts_are_left_alone(
        self, committing_db, committing_factories
    ):
        _, job_ids = _scene(committing_db, committing_factories, 1)
        ctx = committing_db.get(SemanticJob, job_ids[0]).context_id
        # Задание не `running`, но с незакрытой попыткой: восстановление касается
        # только прерванных заданий, чужие попытки и статусы не трогает.
        other, other_attempt = _running_job_with_attempt(committing_db, ctx)
        other.status, other.claim_token = "error", None
        _running_job_with_attempt(committing_db, ctx)  # чтобы восстанавливать было что
        committing_db.commit()

        assert recover_semantic_jobs(committing_db, now=NOW + dt.timedelta(hours=1)) == 1
        committing_db.commit()

        committing_db.expire_all()
        assert committing_db.get(SemanticJob, other.id).status == "error"
        untouched = committing_db.get(SemanticJobAttempt, other_attempt.id)
        assert (untouched.finished_at, untouched.outcome, untouched.error_class) == (
            None, None, None,
        )

    def test_startup_recovery_covers_both_kinds_in_one_call(
        self, committing_session_factory, committing_db, committing_factories, tmp_storage
    ):
        _, job_ids = _scene(committing_db, committing_factories, 1)
        ctx = committing_db.get(SemanticJob, job_ids[0]).context_id
        job, _ = _running_job_with_attempt(committing_db, ctx)
        stuck = committing_factories.ImportJobFactory.create(status=ImportJobStatus.parsing.value)
        committing_db.commit()
        job_id, stuck_id = job.id, stuck.id

        result = run_startup_maintenance(
            committing_session_factory, tmp_storage, retention_days=30
        )

        assert result == (1, 0)  # тип и смысл прежние: заданий импорта, файлов
        with committing_session_factory() as fresh:
            assert fresh.get(SemanticJob, job_id).status == "pending"
            assert fresh.get(ImportJob, stuck_id).status == ImportJobStatus.error.value

    def test_semantic_recovery_failure_fails_startup_and_rolls_back_import_recovery(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch
    ):
        stuck = committing_factories.ImportJobFactory.create(status=ImportJobStatus.parsing.value)
        committing_db.commit()
        stuck_id = stuck.id
        monkeypatch.setattr(
            "services.maintenance.recover_semantic_jobs",
            lambda _db, *, now: (_ for _ in ()).throw(OSError("очередь недоступна")),
        )

        with pytest.raises(OSError, match="очередь недоступна"):
            run_startup_maintenance(committing_session_factory, object(), retention_days=30)

        with committing_session_factory() as fresh:
            assert fresh.get(ImportJob, stuck_id).status == ImportJobStatus.parsing.value


# ---------------------------------------------------------------------------
#  Поток-опросчик
# ---------------------------------------------------------------------------

class TestSemanticRunner:
    def test_processes_the_queue_until_it_is_empty(
        self, committing_session_factory, committing_db, committing_factories, stop_runners
    ):
        family, job_ids = _scene(committing_db, committing_factories, 5)
        client = _Client(family.id)
        # Внедрённые часы доходят до исполнителя: время попыток — их, а не системное.
        clock_at = NOW + dt.timedelta(minutes=7)
        runner = SemanticRunner(
            committing_session_factory, client, settings=_settings(), clock=lambda: clock_at
        )
        stop_runners.append(runner)
        runner.start()

        assert _wait_until(
            lambda: set(_statuses(committing_session_factory, job_ids).values()) == {"done"}
        )
        assert client.calls == 5
        with committing_session_factory() as fresh:
            started = fresh.execute(
                sa.select(SemanticJobAttempt.started_at).where(
                    SemanticJobAttempt.job_id.in_(job_ids)
                )
            ).scalars().all()
        assert started == [clock_at] * 5

    def test_parallel_calls_never_exceed_the_concurrency_and_reach_it(
        self, committing_session_factory, committing_db, committing_factories, stop_runners
    ):
        family, job_ids = _scene(committing_db, committing_factories, 9)
        # Три вызова обязаны сойтись разом (барьер), иначе ни один не завершится;
        # удержание после барьера оставляет окно, в которое лишний поток был бы виден.
        client = _Client(family.id, hold_s=0.2, barrier=threading.Barrier(3, timeout=10))
        runner = SemanticRunner(
            committing_session_factory, client,
            settings=_settings(SEMANTIC_CONCURRENCY=3),
        )
        stop_runners.append(runner)
        runner.start()

        assert _wait_until(
            lambda: set(_statuses(committing_session_factory, job_ids).values()) == {"done"}
        )
        assert client.max_active == 3

    def test_stop_takes_no_new_jobs_and_returns_within_the_budget(
        self, committing_session_factory, committing_db, committing_factories, stop_runners
    ):
        family, job_ids = _scene(committing_db, committing_factories, 3)
        gate = threading.Event()
        client = _Client(family.id, gate=gate)
        runner = SemanticRunner(
            committing_session_factory, client,
            settings=_settings(SEMANTIC_CONCURRENCY=1),
        )
        stop_runners.append(runner)
        try:
            runner.start()
            assert all(t.daemon for t in _threads())  # зависший вызов не держит выход
            assert _wait_until(lambda: client.calls == 1)

            started = time.monotonic()
            runner.stop(timeout_s=0.3)
            elapsed = time.monotonic() - started
            assert elapsed < 5  # поток занят вызовом, но stop не ждёт дольше бюджета
            assert _threads(), "поток ещё занят вызовом"
        finally:
            gate.set()  # вызов завершается, но новых захватов уже нет

        assert _wait_until(lambda: not _threads())
        statuses = _statuses(committing_session_factory, job_ids)
        assert client.calls == 1
        assert sorted(statuses.values()) == ["done", "pending", "pending"]

    def test_stop_budget_is_shared_by_all_busy_threads(
        self, committing_session_factory, committing_db, committing_factories, stop_runners
    ):
        family, _ = _scene(committing_db, committing_factories, 3)
        gate = threading.Event()
        client = _Client(family.id, gate=gate)
        runner = SemanticRunner(
            committing_session_factory, client,
            settings=_settings(SEMANTIC_CONCURRENCY=3),
        )
        stop_runners.append(runner)
        try:
            runner.start()
            assert _wait_until(lambda: client.calls == 3)

            started = time.monotonic()
            runner.stop(timeout_s=1.0)
            elapsed = time.monotonic() - started
            # Бюджет общий: три занятых потока не превращают секунду в три.
            assert elapsed < 2.5
        finally:
            gate.set()
        assert _wait_until(lambda: not _threads())

    def test_start_after_a_clean_stop_is_an_error_too(
        self, committing_session_factory, monkeypatch, stop_runners
    ):
        monkeypatch.setattr(runner_module, "IDLE_INTERVAL_S", 0.05)
        runner = SemanticRunner(
            committing_session_factory, _Client(0), settings=_settings(SEMANTIC_CONCURRENCY=1)
        )
        stop_runners.append(runner)
        runner.start()
        runner.stop(timeout_s=10)
        assert _threads() == []  # остановка чистая: живых потоков нет

        # Одноразовость не зависит от того, успели ли потоки завершиться.
        with pytest.raises(RuntimeError):
            runner.start()
        assert _threads() == []

    def test_a_second_stop_waits_for_threads_the_first_one_left_behind(
        self, committing_session_factory, committing_db, committing_factories, stop_runners
    ):
        family, job_ids = _scene(committing_db, committing_factories, 1)
        gate = threading.Event()
        client = _Client(family.id, gate=gate)
        runner = SemanticRunner(
            committing_session_factory, client, settings=_settings(SEMANTIC_CONCURRENCY=1)
        )
        stop_runners.append(runner)
        release = threading.Timer(0.5, gate.set)
        try:
            runner.start()
            assert _wait_until(lambda: client.calls == 1)
            runner.stop(timeout_s=0.1)  # бюджет вышел: поток ещё в вызове
            assert _threads()

            release.start()
            runner.stop(timeout_s=10)

            # Повторная остановка дождалась оставленного потока (на это опирается
            # и фикстура `stop_runners` перед очисткой таблиц).
            assert _threads() == []
            assert _statuses(committing_session_factory, job_ids) == {job_ids[0]: "done"}
        finally:
            release.cancel()
            gate.set()

    def test_stop_waits_for_the_call_in_flight(
        self, committing_session_factory, committing_db, committing_factories, stop_runners
    ):
        family, job_ids = _scene(committing_db, committing_factories, 1)
        client = _Client(family.id, hold_s=0.5)
        runner = SemanticRunner(
            committing_session_factory, client,
            settings=_settings(SEMANTIC_CONCURRENCY=1),
        )
        stop_runners.append(runner)
        runner.start()
        assert _wait_until(lambda: client.calls == 1)

        runner.stop(timeout_s=10)

        # Вызов укладывается в бюджет — к возврату stop он записан, поток завершён.
        assert _threads() == []
        assert _statuses(committing_session_factory, job_ids) == {job_ids[0]: "done"}

    def test_start_twice_without_stop_is_an_error(
        self, committing_session_factory, stop_runners
    ):
        runner = SemanticRunner(committing_session_factory, _Client(0), settings=_settings())
        stop_runners.append(runner)
        runner.start()

        with pytest.raises(RuntimeError):
            runner.start()

    def test_start_after_stop_is_an_error_and_starts_no_threads(
        self, committing_session_factory, committing_db, committing_factories, stop_runners
    ):
        family, _ = _scene(committing_db, committing_factories, 2)
        gate = threading.Event()
        client = _Client(family.id, gate=gate)
        runner = SemanticRunner(
            committing_session_factory, client, settings=_settings(SEMANTIC_CONCURRENCY=2)
        )
        stop_runners.append(runner)
        try:
            runner.start()
            assert _wait_until(lambda: client.calls == 2)
            runner.stop(timeout_s=0.2)  # бюджет вышел: оба потока ещё заняты вызовом
            before = _threads()
            assert len(before) == 2

            with pytest.raises(RuntimeError):
                runner.start()

            assert _threads() == before
        finally:
            gate.set()
        assert _wait_until(lambda: not _threads())

    def test_a_failing_process_one_is_logged_and_retried_only_after_the_idle_interval(
        self, committing_session_factory, monkeypatch, stop_runners
    ):
        monkeypatch.setattr(runner_module, "IDLE_INTERVAL_S", 0.2)
        calls = []

        def _boom(*args, **kwargs):
            calls.append(time.monotonic())
            raise RuntimeError("захват упал")

        monkeypatch.setattr(runner_module, "process_one", _boom)
        runner = SemanticRunner(
            committing_session_factory, _Client(0),
            settings=_settings(SEMANTIC_CONCURRENCY=1),
        )
        stop_runners.append(runner)
        runner.start()

        time.sleep(1.0)
        runner.stop(timeout_s=5)

        # Поток пережил исключение (вызовов больше одного) и не крутится вхолостую:
        # за секунду при паузе 0.2 с их считанные единицы, а не тысячи.
        assert 2 <= len(calls) <= 8
        assert not _threads()

    def test_an_empty_queue_waits_the_idle_interval_between_polls(
        self, committing_session_factory, monkeypatch, stop_runners
    ):
        monkeypatch.setattr(runner_module, "IDLE_INTERVAL_S", 0.2)
        calls = []

        def _empty(*args, **kwargs):
            calls.append(time.monotonic())
            return False

        monkeypatch.setattr(runner_module, "process_one", _empty)
        runner = SemanticRunner(
            committing_session_factory, _Client(0),
            settings=_settings(SEMANTIC_CONCURRENCY=1),
        )
        stop_runners.append(runner)
        runner.start()

        time.sleep(1.0)
        runner.stop(timeout_s=5)

        assert 2 <= len(calls) <= 8


# ---------------------------------------------------------------------------
#  lifespan
# ---------------------------------------------------------------------------

class TestLifespan:
    def _patch(
        self, monkeypatch, factory, *, enabled, api_key="k", made=None, client=None,
        maintenance=True,
    ):
        import main

        monkeypatch.setattr(main.settings, "RUN_SEMANTIC_WORKER", enabled)
        # Обслуживание при старте включено (опросчик без него не поднимается), но
        # само оно заглушено: оно ходило бы в хранилище приложения.
        monkeypatch.setattr(main.settings, "RUN_STARTUP_MAINTENANCE", maintenance)
        monkeypatch.setattr(main, "run_startup_maintenance", lambda *a, **k: (0, 0))
        monkeypatch.setattr(main.settings, "OPENROUTER_API_KEY", api_key)
        monkeypatch.setattr(main.settings, "SEMANTIC_CONCURRENCY", 2)
        monkeypatch.setattr(main.settings, "SEMANTIC_CALL_TIMEOUT_S", 5)
        monkeypatch.setattr(main.settings, "SEMANTIC_SHUTDOWN_WAIT_S", 7)
        monkeypatch.setattr(main.settings, "SEMANTIC_DAILY_BUDGET_USD", Decimal("1000"))
        monkeypatch.setattr(main, "SessionLocal", factory)

        def _make(cfg):
            if made is not None:
                made.append(cfg)
            return client

        monkeypatch.setattr(main, "_make_semantic_client", _make)

    def test_disabled_worker_creates_no_client_and_no_threads(
        self, committing_session_factory, monkeypatch
    ):
        from fastapi.testclient import TestClient

        from main import app

        made = []
        self._patch(monkeypatch, committing_session_factory, enabled=False, made=made)

        with TestClient(app):
            assert _threads() == []
        assert made == []

    def test_enabled_worker_starts_processes_and_stops_on_exit(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch
    ):
        from fastapi.testclient import TestClient

        import main

        app = main.app
        family, job_ids = _scene(committing_db, committing_factories, 2)
        client = _Client(family.id)
        made = []
        self._patch(
            monkeypatch, committing_session_factory, enabled=True, made=made, client=client
        )
        budgets = []
        real_stop = main.SemanticRunner.stop

        def _spy_stop(runner, *, timeout_s):
            budgets.append(timeout_s)
            real_stop(runner, timeout_s=timeout_s)

        monkeypatch.setattr(main.SemanticRunner, "stop", _spy_stop)

        with TestClient(app):
            assert len(_threads()) == 2
            assert _wait_until(
                lambda: set(_statuses(committing_session_factory, job_ids).values()) == {"done"}
            )
        assert len(made) == 1
        # Ожидание на выходе — своя настройка, а не таймаут вызова модели (5 с).
        assert budgets == [7]
        assert _wait_until(lambda: not _threads())

    def test_default_client_factory_builds_openrouter_with_the_configured_key(self):
        import main
        from services.semantic_client import OpenRouterClient

        client = main._make_semantic_client(_settings(OPENROUTER_API_KEY="sk-test-key"))
        try:
            assert isinstance(client, OpenRouterClient)
            assert client._headers["Authorization"] == "Bearer sk-test-key"
        finally:
            client.close()

    def test_the_test_session_runs_with_the_worker_disabled(self):
        # В тестах `TestClient(app)` поднимает lifespan на реальном engine; включённый
        # из `.env` опросчик ходил бы в сеть с настоящим ключом.
        assert app_settings.RUN_SEMANTIC_WORKER is False

    def test_enabled_worker_without_api_key_fails_startup(
        self, committing_session_factory, monkeypatch
    ):
        from fastapi.testclient import TestClient

        from main import app

        made = []
        self._patch(
            monkeypatch, committing_session_factory, enabled=True, api_key="", made=made
        )

        with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"), TestClient(app):
            pass  # pragma: no cover — до тела дело не доходит
        assert made == []
        assert _threads() == []

    def test_enabled_worker_closes_the_client_after_the_runner_has_stopped(
        self, committing_session_factory, monkeypatch
    ):
        from fastapi.testclient import TestClient

        import main

        events = []

        class _ClosableClient(_Client):
            def close(self):
                events.append(("close", len(_threads())))

        real_stop = main.SemanticRunner.stop

        def _spy_stop(runner, *, timeout_s):
            real_stop(runner, timeout_s=timeout_s)
            events.append(("stopped", len(_threads())))

        monkeypatch.setattr(main.SemanticRunner, "stop", _spy_stop)
        self._patch(
            monkeypatch, committing_session_factory, enabled=True, client=_ClosableClient(0)
        )

        with TestClient(main.app):
            assert events == []

        # Клиент закрыт ПОСЛЕ остановки потоков: закрывать его под работающим
        # вызовом нельзя.
        assert events == [("stopped", 0), ("close", 0)]

    def test_client_is_closed_even_when_stopping_the_runner_fails(
        self, committing_session_factory, monkeypatch
    ):
        from fastapi.testclient import TestClient

        import main

        closed = []

        class _ClosableClient(_Client):
            def close(self):
                closed.append(True)

        real_stop = main.SemanticRunner.stop

        def _failing_stop(runner, *, timeout_s):
            real_stop(runner, timeout_s=timeout_s)  # потоки не оставляем живыми
            raise RuntimeError("остановка упала")

        monkeypatch.setattr(main.SemanticRunner, "stop", _failing_stop)
        self._patch(
            monkeypatch, committing_session_factory, enabled=True, client=_ClosableClient(0)
        )

        with pytest.raises(RuntimeError, match="остановка упала"), TestClient(main.app):
            pass

        assert closed == [True]

    def test_enabled_worker_without_startup_maintenance_fails_startup(
        self, committing_session_factory, monkeypatch
    ):
        """Без восстановления при старте прерванные `running` остались бы
        навсегда: ключ задан, отличие от запускающегося случая — только флаг."""
        from fastapi.testclient import TestClient

        from main import app

        made = []
        self._patch(
            monkeypatch, committing_session_factory, enabled=True, made=made, maintenance=False
        )

        with pytest.raises(RuntimeError, match="RUN_STARTUP_MAINTENANCE"), TestClient(app):
            pass  # pragma: no cover — до тела дело не доходит
        assert made == []
        assert _threads() == []

    def test_disabled_worker_does_not_require_startup_maintenance(
        self, committing_session_factory, monkeypatch
    ):
        from fastapi.testclient import TestClient

        from main import app

        self._patch(monkeypatch, committing_session_factory, enabled=False, maintenance=False)

        with TestClient(app):
            assert _threads() == []
