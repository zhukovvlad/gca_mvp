"""Кэш дорогих частей сводки очереди «Предложения» (спека
`2026-11-12-semantic-status-cache-design.md` §2.3, §2.4; долг `docs/TECH_DEBT.md` №57).

ЗАВИСИТ ОТ ОДНОГО ПРОЦЕССА uvicorn (AGENTS.md §3): значение живёт в памяти
процесса, и сброс, сделанный в одном процессе, второй не увидит. Ни брокера, ни
внешнего кэша здесь нет и не должно быть.

Правила (все — для срока > 0; при 0 кэш выключен: `get` просто вызывает `compute`,
ничего не запоминает и замка не берёт):

1. Значение действительно, пока с конца его пересчёта прошло меньше срока и после
   начала пересчёта не было сброса.
2. Каждый `invalidate()` увеличивает номер поколения и стирает значение. Пересчёт
   запоминает номер поколения при старте; сменился к концу — результат не
   запоминается (но отдаётся своему вызвавшему).
3. Пересчитывает один вызов за раз. Пока он идёт, вызов с истёкшим значением
   получает его сразу; вызов без значения ждёт и, проснувшись, перечитывает общее
   состояние: нашёл запомненное — берёт, иначе пересчитывает сам.
4. Результат, начатый до сброса, не достаётся пришедшему после: сброс стирает
   значение, а пересчёт, пересёкший сброс, его не запоминает.
5. Исключение в `compute` уходит вызвавшему; признак «идёт пересчёт» снимается,
   ожидающие просыпаются, а значение (в том числе истёкшее) стирается.
6. Значение отдаётся как есть, вызывающие его не изменяют.

Пересчёт идёт вне замка — иначе во время него нельзя было бы отдать истёкшее
значение.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Generic, TypeVar

from config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

_EMPTY = object()


class StatusCache(Generic[T]):
    #: Часы — открытый атрибут: тесты подменяют их у общего экземпляра.
    clock: Callable[[], float]

    def __init__(
        self,
        ttl_seconds: Callable[[], float],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self.clock = clock
        self._cond = threading.Condition(threading.Lock())
        self._value: object = _EMPTY
        self._computed_at = 0.0
        self._generation = 0
        self._computing = False

    def get(self, compute: Callable[[], T]) -> T:
        ttl = self._ttl_seconds()
        if ttl <= 0:
            return compute()
        with self._cond:
            while True:
                if self._value is not _EMPTY:
                    if self.clock() - self._computed_at < ttl or self._computing:
                        return self._value  # type: ignore[return-value]
                elif self._computing:
                    self._cond.wait()
                    continue
                break
            self._computing = True
            generation = self._generation
        # Между снятием замка и `try` — ни одной операции: асинхронное исключение
        # здесь оставило бы признак «идёт пересчёт» навсегда (ревью Task 1).
        try:
            started = time.perf_counter()
            result = compute()
        except BaseException:
            with self._cond:
                self._computing = False
                self._value = _EMPTY
                self._cond.notify_all()
            raise
        with self._cond:
            self._computing = False
            if generation == self._generation:
                self._value = result
                self._computed_at = self.clock()
            self._cond.notify_all()
        logger.info("Пересчёт сводки очереди: %.2f с", time.perf_counter() - started)
        return result

    def invalidate(self) -> None:
        with self._cond:
            self._generation += 1
            self._value = _EMPTY


# Тип значения — `HeavyStatusParts` (crud/semantic_queue.py); не импортируется,
# чтобы не тянуть crud в сервисный модуль.
status_cache: StatusCache = StatusCache(lambda: settings.SEMANTIC_STATUS_CACHE_TTL_S)

# Второй экземпляр: обычные ставки «комплект или ставка» (`services/lump_sum.TypicalRates`, спека
# строк-сумм §2 п. 2). Свой срок, те же точки сброса, что у `status_cache`.
typical_rates_cache: StatusCache = StatusCache(lambda: settings.TYPICAL_RATES_CACHE_TTL_S)

# Третий экземпляр: индекс подозрительных строк договоров (`SuspicionIndex`, спека «Подозрительные
# строки» §1 п. 3, §2 п. 6). Свой срок и свой закрытый список точек сброса: прослойка `main.py`,
# сбрасывающая кэши на любой не-GET, его не трогает.
suspicion_cache: StatusCache = StatusCache(lambda: settings.SUSPICION_CACHE_TTL_S)
