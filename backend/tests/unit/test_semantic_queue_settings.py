"""Числовые настройки опросчика семантических заданий: неверное значение — ошибка
загрузки `Settings`, а не молчаливо неработающая очередь (например, ноль потоков)."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from config import Settings

_KEY = "k" * 32


def _load(**kw):
    return Settings(_env_file=None, SECRET_KEY=_KEY, **kw)


def _assert_rejected_only(name, excinfo):
    # Отказ обязан прийти от проверяемой настройки: неверное значение соседней
    # (например, SECRET_KEY) дало бы тот же ValidationError.
    assert [err["loc"] for err in excinfo.value.errors()] == [(name,)]


# Имена настроек по нижней границе.
_INT_AT_LEAST_1 = [
    "SEMANTIC_CONCURRENCY",
    "SEMANTIC_MAX_TOKENS",
    "SEMANTIC_CALL_TIMEOUT_S",
    "SEMANTIC_MAX_ATTEMPTS",
]
_INT_AT_LEAST_0 = [
    "SEMANTIC_SHUTDOWN_WAIT_S",
    "SEMANTIC_EVENT_MAX_CONTEXTS",
    "SEMANTIC_SWEEP_INTERVAL_S",
]
_DECIMAL_AT_LEAST_0 = [
    "SEMANTIC_PRICE_INPUT_PER_M",
    "SEMANTIC_PRICE_CACHE_WRITE_PER_M",
    "SEMANTIC_PRICE_CACHE_READ_PER_M",
    "SEMANTIC_PRICE_OUTPUT_PER_M",
    "SEMANTIC_DAILY_BUDGET_USD",
    "SEMANTIC_EVENT_MAX_RESERVE_USD",
]


@pytest.mark.parametrize("name", _INT_AT_LEAST_1)
@pytest.mark.parametrize("bad", [0, -1])
def test_semantic_queue_settings_int_below_one_rejected(name, bad):
    with pytest.raises(ValidationError) as excinfo:
        _load(**{name: bad})
    _assert_rejected_only(name, excinfo)


@pytest.mark.parametrize("name", _INT_AT_LEAST_1)
def test_semantic_queue_settings_int_one_accepted(name):
    assert getattr(_load(**{name: 1}), name) == 1


@pytest.mark.parametrize("name", _INT_AT_LEAST_0)
def test_semantic_queue_settings_int_negative_rejected(name):
    with pytest.raises(ValidationError) as excinfo:
        _load(**{name: -1})
    _assert_rejected_only(name, excinfo)


@pytest.mark.parametrize("name", _INT_AT_LEAST_0)
def test_semantic_queue_settings_int_zero_accepted(name):
    assert getattr(_load(**{name: 0}), name) == 0


@pytest.mark.parametrize("name", _DECIMAL_AT_LEAST_0)
def test_semantic_queue_settings_decimal_negative_rejected(name):
    with pytest.raises(ValidationError) as excinfo:
        _load(**{name: Decimal("-0.01")})
    _assert_rejected_only(name, excinfo)


@pytest.mark.parametrize("name", _DECIMAL_AT_LEAST_0)
def test_semantic_queue_settings_decimal_zero_accepted(name):
    assert getattr(_load(**{name: Decimal("0")}), name) == Decimal("0")


def test_semantic_queue_settings_concurrency_zero_from_environment(monkeypatch):
    monkeypatch.setenv("SEMANTIC_CONCURRENCY", "0")
    monkeypatch.setenv("RUN_SEMANTIC_WORKER", "true")
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, SECRET_KEY=_KEY)
    _assert_rejected_only("SEMANTIC_CONCURRENCY", excinfo)


def test_semantic_queue_settings_sweep_interval_defaults_to_five_minutes():
    assert _load().SEMANTIC_SWEEP_INTERVAL_S == 300
