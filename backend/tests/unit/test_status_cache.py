"""Правила кэша сводки очереди (`services/status_cache.py`): срок, поколение, один
пересчёт, ошибка пересчёта (спека `2026-11-12-semantic-status-cache-design.md`
§2.3 и §3, проверки 4–6).

Потоки и события. КАЖДОЕ ожидание ограничено: плагина pytest-timeout в проекте
нет, поэтому тест при снятой защите обязан упасть на утверждении, а не повиснуть.
Узнать, что вызов «ждёт», можно только по тому, что поток не закончился за
короткое время; решающее утверждение — значение, которое вызов вернул.
"""
from __future__ import annotations

import threading
import time

import pytest

from services.status_cache import StatusCache

WAIT = 5.0  # потолок любого ожидания, секунды
SETTLE = 0.3  # сколько ждать, чтобы поток успел дойти до ожидания


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Counter:
    """compute, считающий вызовы и возвращающий очередное значение."""

    def __init__(self, *values):
        self.values = list(values)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.values[min(self.calls, len(self.values)) - 1]


class Gated:
    """compute, который стоит на событии, пока тест его не отпустит."""

    def __init__(self, result):
        self.result = result
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self):
        self.calls += 1
        self.started.set()
        assert self.release.wait(timeout=WAIT), "тест не отпустил пересчёт"
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class Caller:
    """Вызов `cache.get` в потоке-демоне; результат или исключение — в полях."""

    def __init__(self, cache, compute):
        self.value = None
        self.error = None
        self.thread = threading.Thread(target=self._run, args=(cache, compute), daemon=True)
        self.thread.start()

    def _run(self, cache, compute):
        try:
            self.value = cache.get(compute)
        except BaseException as exc:  # noqa: BLE001 — исключение нужно тесту
            self.error = exc

    def alive(self) -> bool:
        return self.thread.is_alive()

    def finish(self) -> None:
        self.thread.join(timeout=WAIT)
        assert not self.thread.is_alive(), "вызов не завершился (завис)"


def make(ttl: float = 300) -> tuple[StatusCache, FakeClock]:
    clock = FakeClock()
    return StatusCache(lambda: ttl, clock), clock


class TestStatusCacheTtl:
    def test_value_is_remembered_within_the_ttl(self):
        cache, clock = make(300)
        compute = Counter("a", "b")

        first = cache.get(compute)
        clock.advance(299)
        second = cache.get(compute)

        assert (first, second, compute.calls) == ("a", "a", 1)

    def test_value_expires_exactly_at_the_ttl(self):
        cache, clock = make(300)
        compute = Counter("a", "b")

        cache.get(compute)
        clock.advance(300)

        assert (cache.get(compute), compute.calls) == ("b", 2)

    def test_ttl_is_read_on_every_get(self):
        ttl = {"value": 300}
        clock = FakeClock()
        cache = StatusCache(lambda: ttl["value"], clock)
        compute = Counter("a", "b")
        cache.get(compute)
        clock.advance(10)

        ttl["value"] = 5

        assert (cache.get(compute), compute.calls) == ("b", 2)

    def test_zero_ttl_computes_every_time_and_remembers_nothing(self):
        cache, _ = make(0)
        compute = Counter("a", "b", "c")

        values = [cache.get(compute) for _ in range(3)]

        assert (values, compute.calls) == (["a", "b", "c"], 3)

    def test_zero_ttl_does_not_wait_for_a_recompute_in_progress(self):
        ttl = {"value": 300}
        cache = StatusCache(lambda: ttl["value"], FakeClock())
        gated = Gated("slow")
        holder = Caller(cache, gated)
        assert gated.started.wait(timeout=WAIT)
        ttl["value"] = 0

        direct = Caller(cache, lambda: "direct")
        direct.finish()

        assert direct.value == "direct"
        gated.release.set()
        holder.finish()

    def test_invalidate_drops_the_value(self):
        cache, _ = make(300)
        compute = Counter("a", "b")
        cache.get(compute)

        cache.invalidate()

        assert (cache.get(compute), compute.calls) == ("b", 2)


class TestStatusCacheGeneration:
    def test_a_recompute_crossing_a_reset_is_not_remembered(self):
        cache, _ = make(300)
        crossing = Gated("old")
        caller = Caller(cache, crossing)
        assert crossing.started.wait(timeout=WAIT)

        cache.invalidate()
        crossing.release.set()
        caller.finish()
        after = Counter("new")

        assert caller.value == "old"  # своему вызвавшему результат отдаётся
        assert (cache.get(after), after.calls) == ("new", 1)

    def test_a_caller_waiting_after_a_reset_recomputes_instead_of_taking_the_old_result(self):
        cache, _ = make(300)
        crossing = Gated("old")
        first = Caller(cache, crossing)
        assert crossing.started.wait(timeout=WAIT)
        cache.invalidate()
        fresh = Counter("new")

        second = Caller(cache, fresh)
        time.sleep(SETTLE)
        assert second.alive(), "вызов после сброса не должен получить результат до конца пересчёта"
        crossing.release.set()
        first.finish()
        second.finish()

        assert (first.value, second.value) == ("old", "new")
        assert fresh.calls == 1
        later = Counter("never")
        assert (cache.get(later), later.calls) == ("new", 0)


class TestStatusCacheSingleRecompute:
    def test_two_concurrent_gets_on_an_empty_cache_compute_once(self):
        cache, _ = make(300)
        gated = Gated("v")
        first = Caller(cache, gated)
        assert gated.started.wait(timeout=WAIT)
        extra = Counter("second-computed")

        second = Caller(cache, extra)
        time.sleep(SETTLE)
        assert second.alive(), "второй вызов обязан ждать пересчёта первого"
        gated.release.set()
        first.finish()
        second.finish()

        assert (first.value, second.value) == ("v", "v")
        assert (gated.calls, extra.calls) == (1, 0)

    def test_an_expired_value_is_served_while_the_recompute_is_in_progress(self):
        cache, clock = make(300)
        cache.get(Counter("a"))
        clock.advance(300)
        gated = Gated("b")
        recomputing = Caller(cache, gated)
        assert gated.started.wait(timeout=WAIT)

        second = Caller(cache, Counter("must-not-run"))
        second.finish()  # вернулся, пока первый пересчёт ещё держится

        assert second.value == "a"
        assert recomputing.alive()
        gated.release.set()
        recomputing.finish()
        assert recomputing.value == "b"
        assert cache.get(Counter("never")) == "b"


class TestStatusCacheComputeError:
    def test_waiter_recovers_after_the_error_and_the_third_call_takes_the_stored_value(self):
        cache, _ = make(300)
        failing = Gated(RuntimeError("boom"))
        first = Caller(cache, failing)
        assert failing.started.wait(timeout=WAIT)
        second_compute = Counter("B")

        second = Caller(cache, second_compute)
        time.sleep(SETTLE)
        assert second.alive()
        failing.release.set()
        first.finish()
        second.finish()
        third_compute = Counter("C")
        # В потоке с потолком: зависший признак «идёт пересчёт» обязан ронять
        # тест, а не вешать прогон.
        third = Caller(cache, third_compute)
        third.finish()

        assert first.error is failing.result
        assert (second.error, second.value) == (None, "B")
        assert second_compute.calls == 1
        assert (third.value, third_compute.calls) == ("B", 0)

    def test_error_after_the_ttl_does_not_leave_the_expired_value(self):
        cache, clock = make(300)
        cache.get(Counter("A"))
        clock.advance(300)

        def boom():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            cache.get(boom)

        # Второй пересчёт запущен и держится; третий вызов должен ЖДАТЬ его,
        # а не получить стёртое истёкшее «A» по правилу выдачи во время пересчёта.
        held = Gated("B")
        second = Caller(cache, held)
        assert held.started.wait(timeout=WAIT)
        third_compute = Counter("never")
        third = Caller(cache, third_compute)
        time.sleep(SETTLE)
        assert third.alive(), "третий вызов вернулся, не дождавшись пересчёта (получил истёкшее)"
        held.release.set()
        second.finish()
        third.finish()

        assert (second.value, third.value) == ("B", "B")
        assert third_compute.calls == 0

    def test_error_in_the_only_call_reaches_that_call_and_leaves_the_cache_usable(self):
        cache, _ = make(300)

        def boom():
            raise ValueError("x")

        with pytest.raises(ValueError):
            cache.get(boom)
        compute = Counter("ok")
        # В потоке с потолком: после ошибки признак «идёт пересчёт» снят, иначе
        # этот вызов ждал бы вечно — тест обязан упасть, а не повиснуть.
        after = Caller(cache, compute)
        after.finish()

        assert (after.error, after.value, compute.calls) == (None, "ok", 1)


class TestTypicalRatesCacheIsASeparateInstance:
    """Второй кэш (обычные ставки «комплект или ставка», спека строк-сумм §2 п. 2): свой срок
    `TYPICAL_RATES_CACHE_TTL_S`, свой экземпляр, сброс одного не стирает другое."""

    def test_is_a_status_cache_distinct_from_the_status_one(self):
        from services.status_cache import status_cache, typical_rates_cache

        assert isinstance(typical_rates_cache, StatusCache)
        assert typical_rates_cache is not status_cache

    def test_uses_its_own_ttl_setting(self, monkeypatch):
        from config import settings
        from services.status_cache import status_cache, typical_rates_cache

        monkeypatch.setattr(settings, "SEMANTIC_STATUS_CACHE_TTL_S", 0)
        monkeypatch.setattr(settings, "TYPICAL_RATES_CACHE_TTL_S", 300)
        typical_rates_cache.invalidate()
        try:
            compute = Counter("a", "b")
            assert typical_rates_cache.get(compute) == "a"
            assert typical_rates_cache.get(compute) == "a"  # срок сводки 0, а ставки держатся
            assert compute.calls == 1
            other = Counter("x", "y")
            assert (status_cache.get(other), status_cache.get(other)) == ("x", "y")  # выключен
        finally:
            typical_rates_cache.invalidate()

    def test_default_ttl_is_five_minutes(self):
        from config import Settings

        assert Settings.model_fields["TYPICAL_RATES_CACHE_TTL_S"].default == 300
