"""Кэш сцен этапа (спека `2026-11-27-stage-cache-design.md` §2, §6)."""
from __future__ import annotations

import threading

import pytest

from services.stage_cache import SceneCache


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(ttl: float = 600, size: int = 8) -> tuple[SceneCache, FakeClock]:
    clock = FakeClock()
    return SceneCache(lambda: ttl, lambda: size, clock=clock), clock


def test_put_then_get_returns_same_value():
    cache, _ = make()
    value = object()
    assert cache.put((1, 2, None, None), value, cache.generation()) is True
    assert cache.get((1, 2, None, None)) is value


def test_keys_differing_by_one_element_are_distinct():
    cache, _ = make()
    cache.put((1, 2, None, None), "a", cache.generation())
    assert cache.get((1, 2, 5, None)) is None
    assert cache.get((1, 2, None, 5)) is None
    assert cache.get((1, 3, None, None)) is None
    assert cache.get((2, 2, None, None)) is None


def test_put_with_stale_generation_is_rejected():
    cache, _ = make()
    gen = cache.generation()
    cache.invalidate()
    assert cache.put((1, 2, None, None), "a", gen) is False
    assert cache.get((1, 2, None, None)) is None


def test_invalidate_clears_everything_and_bumps_generation():
    cache, _ = make()
    gen = cache.generation()
    cache.put((1, 2, None, None), "a", gen)
    cache.put((1, 2, 7, 9), "b", gen)
    cache.invalidate()
    assert cache.get((1, 2, None, None)) is None
    assert cache.get((1, 2, 7, 9)) is None
    assert cache.generation() == gen + 1


def test_ttl_counts_from_put_not_from_get():
    cache, clock = make(ttl=600)
    key = (1, 2, None, None)
    cache.put(key, "a", cache.generation())
    clock.now += 599
    assert cache.get(key) == "a"  # чтение не продлевает срок
    clock.now += 1  # 600 с от put
    assert cache.get(key) is None
    assert key not in cache._entries


def test_size_evicts_least_recently_used():
    cache, _ = make(size=8)
    gen = cache.generation()
    for i in range(8):
        cache.put((1, i, None, None), i, gen)
    cache.put((1, 8, None, None), 8, gen)
    assert cache.get((1, 0, None, None)) is None
    assert cache.get((1, 1, None, None)) == 1


def test_get_refreshes_position_before_eviction():
    cache, _ = make(size=8)
    gen = cache.generation()
    for i in range(8):
        cache.put((1, i, None, None), i, gen)
    assert cache.get((1, 0, None, None)) == 0
    cache.put((1, 8, None, None), 8, gen)
    assert cache.get((1, 0, None, None)) == 0
    assert cache.get((1, 1, None, None)) is None


def test_zero_ttl_disables_cache():
    cache, _ = make(ttl=0)
    assert cache.enabled() is False
    cache.put((1, 2, None, None), "a", cache.generation())
    assert cache.get((1, 2, None, None)) is None
    assert len(cache._entries) == 0


def test_enabled_with_positive_ttl():
    cache, _ = make(ttl=1)
    assert cache.enabled() is True


class _InterleavingLock:
    """Обёртка над настоящим замком: при первом release потоком put запускает
    invalidate() в другом потоке и ждёт его конца."""

    def __init__(self, cache: SceneCache, put_thread_id_box: list[int]) -> None:
        self._real = threading.Lock()
        self._cache = cache
        self._box = put_thread_id_box
        self._fired = False
        self.reset_done = False

    def acquire(self, *args, **kwargs):
        return self._real.acquire(*args, **kwargs)

    def release(self) -> None:
        self._real.release()
        if self._fired or threading.get_ident() != self._box[0]:
            return
        self._fired = True
        t = threading.Thread(target=self._cache.invalidate)
        t.start()
        t.join(timeout=2)
        if t.is_alive():
            pytest.fail("сброс не завершился")
        self.reset_done = True

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def test_put_checks_generation_and_inserts_in_one_critical_section():
    cache, _ = make()
    box = [0]
    lock = _InterleavingLock(cache, box)
    cache._lock = lock  # type: ignore[assignment]
    key = (1, 2, None, None)
    gen = cache.generation()  # захват, отпускание — до включения слежения
    box[0] = threading.get_ident()
    lock._fired = False
    assert cache.put(key, "a", gen) is True
    assert lock.reset_done
    assert cache.get(key) is None
    assert cache.generation() == gen + 1
