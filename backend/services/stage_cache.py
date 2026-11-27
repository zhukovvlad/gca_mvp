"""Кэш сцен этапа (спека `2026-11-27-stage-cache-design.md` §2, §6).

ЗАВИСИТ ОТ ОДНОГО ПРОЦЕССА uvicorn (AGENTS.md §3): значение живёт в памяти
процесса, и сброс, сделанный в одном процессе, второй не увидит.

Структура без знания о записях и о crud: ключ — `SceneKey`, значение — готовая
сцена. Поколение растёт на каждом `invalidate()`; сборщик берёт его до сборки и
отдаёт в `put`, и сцена, собранная через сброс, не сохраняется. Сборка идёт вне
замка. `put` сверяет поколение и вставляет в одной критической секции `_lock`;
`get` (с перестановкой порядка), `invalidate` и вытеснение берут тот же замок.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Generic, TypeVar

from config import settings

T = TypeVar("T")

#: тендер, этап, оффер | весь этап, ряд | без ряда
SceneKey = tuple[int, int, int | None, int | None]


class SceneCache(Generic[T]):
    #: Часы — открытый атрибут: тесты подменяют их.
    clock: Callable[[], float]

    def __init__(
        self,
        ttl_seconds: Callable[[], float],
        size: Callable[[], int],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._size = size
        self.clock = clock
        self._lock = threading.Lock()
        self._generation = 0
        #: ключ -> (сцена, момент put); порядок — от давно использованной к недавней
        self._entries: OrderedDict[SceneKey, tuple[T, float]] = OrderedDict()

    def enabled(self) -> bool:
        return self._ttl_seconds() > 0

    def generation(self) -> int:
        with self._lock:
            return self._generation

    def get(self, key: SceneKey) -> T | None:
        ttl = self._ttl_seconds()
        if ttl <= 0:
            return None
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            value, stored_at = entry
            if self.clock() - stored_at >= ttl:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return value

    def put(self, key: SceneKey, value: T, generation: int) -> bool:
        if self._ttl_seconds() <= 0:
            return False
        with self._lock:
            if generation != self._generation:
                return False
            self._entries[key] = (value, self.clock())
            self._entries.move_to_end(key)
            limit = max(1, self._size())
            while len(self._entries) > limit:
                self._entries.popitem(last=False)
            return True

    def invalidate(self) -> None:
        with self._lock:
            self._generation += 1
            self._entries.clear()


stage_scene_cache: SceneCache = SceneCache(
    lambda: settings.STAGE_SCENE_CACHE_TTL_S, lambda: settings.STAGE_SCENE_CACHE_SIZE
)
