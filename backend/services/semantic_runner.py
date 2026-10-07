"""Поток-опросчик очереди семантических предложений и восстановление при старте
(задача 11 фичи «Семантические предложения»; спека §2.5).

Один процесс, `SEMANTIC_CONCURRENCY` потоков-демонов: каждый в цикле зовёт
`process_one`, пока очередь не опустеет, затем ждёт интервал простоя. Аренды
(`lease_until`) нет: задание, оставшееся `running` после падения процесса,
возвращает `recover_semantic_jobs` при следующем старте (AGENTS.md §3, §5).
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from config import Settings
from models import SemanticJob, SemanticJobAttempt
from services.semantic_client import ModelClient
from services.semantic_worker import process_one, sweep_semantic_queue
from utils import utcnow_aware

logger = logging.getLogger(__name__)

#: Пауза потока, когда захватывать нечего или захват упал, секунды. Модульная
#: константа: тесты подменяют её, чтобы не ждать.
IDLE_INTERVAL_S = 5.0


def recover_semantic_jobs(db: Session, *, now: datetime) -> int:
    """Возвращает в очередь задания, прерванные остановкой процесса.

    Все `running` → `pending` со снятым `claim_token`; их незакрытые попытки
    закрываются `transient_error` классом `interrupted`. Возвращает число заданий.
    Не коммитит: вызывающий решает границу транзакции."""
    interrupted = list(
        db.execute(
            update(SemanticJob)
            .where(SemanticJob.status == "running")
            .values(status="pending", claim_token=None)
            .returning(SemanticJob.id)
        ).scalars()
    )
    if interrupted:
        db.execute(
            update(SemanticJobAttempt)
            .where(
                SemanticJobAttempt.job_id.in_(interrupted),
                SemanticJobAttempt.finished_at.is_(None),
            )
            .values(finished_at=now, outcome="transient_error", error_class="interrupted")
        )
        logger.warning("Восстановление очереди: возвращено заданий %d", len(interrupted))
    return len(interrupted)


class SemanticRunner:
    """Пул потоков-опросчиков очереди. Одноразовый: после `stop()` запуск заново
    не поддерживается — недождавшиеся потоки ещё могут жить и делить с новыми
    одно событие остановки."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        client: ModelClient,
        *,
        settings: Settings,
        clock: Callable[[], datetime] = utcnow_aware,
    ) -> None:
        self._session_factory = session_factory
        self._client = client
        self._settings = settings
        self._clock = clock
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._started = False
        self._sweep_lock = threading.Lock()
        self._next_sweep_at: datetime | None = None
        self._sweep_cursor = 0

    def start(self) -> None:
        if self._started:
            raise RuntimeError("SemanticRunner уже запускался: повторный start() не поддерживается")
        self._started = True
        for n in range(self._settings.SEMANTIC_CONCURRENCY):
            thread = threading.Thread(
                target=self._loop, name=f"semantic-runner-{n}", daemon=True
            )
            self._threads.append(thread)
            thread.start()

    def stop(self, *, timeout_s: float) -> None:
        """Новых захватов больше нет; текущие задания ждём не дольше `timeout_s`
        суммарно. Потоки — демоны, недождавшиеся выход процесса не держат."""
        self._stop_event.set()
        deadline = time.monotonic() + timeout_s
        for thread in self._threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        if any(t.is_alive() for t in self._threads):
            logger.warning("SemanticRunner: часть потоков не завершилась за %s с", timeout_s)

    def _sweep_if_due(self) -> None:
        """Проход очереди «по кругу» раз в `SEMANTIC_SWEEP_INTERVAL_S` (0 —
        выключен). Первый вызов только назначает срок; проход делает ровно один
        поток, остальные идут дальше. Исключение прохода из цикла не выходит."""
        interval = self._settings.SEMANTIC_SWEEP_INTERVAL_S
        if interval <= 0:
            return
        now = self._clock()
        with self._sweep_lock:
            due = self._next_sweep_at
            if due is None or now < due:
                if due is None:
                    self._next_sweep_at = now + timedelta(seconds=interval)
                return
            self._next_sweep_at = now + timedelta(seconds=interval)
            cursor = self._sweep_cursor
        try:
            self._sweep_cursor = sweep_semantic_queue(
                self._session_factory, settings=self._settings, after_context_id=cursor
            )
        except Exception:  # noqa: BLE001 — упавший проход не должен убить поток
            logger.exception("Опросчик семантической очереди: проход очереди не удался")

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self._sweep_if_due()
            try:
                worked = process_one(
                    self._session_factory,
                    self._client,
                    settings=self._settings,
                    clock=self._clock,
                )
            except Exception:  # noqa: BLE001 — упавший захват не должен убить поток
                logger.exception("Опросчик семантической очереди: сбой цикла")
                worked = False
            if not worked:
                self._stop_event.wait(IDLE_INTERVAL_S)
