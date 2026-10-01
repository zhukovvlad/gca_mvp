"""Расход: резерв попытки, ожидаемая цена при кэше, суточный расход и потолок
события (задача 5 фичи «Семантические предложения»).

План: `docs/superpowers/plans/2026-09-28-semantic-suggestions.md`, задача 5.
Спека: `docs/superpowers/specs/2026-09-28-semantic-suggestions-design.md`
§2.11 (расход), §2.10 (preview использует `expected_cached_cost`).

`known_prefix_tokens`, `reserve_for`, `expected_cached_cost`, `spent_last_24h`
читают `semantic_job_attempts` — без реальной базы не проверить, поэтому все
тесты здесь интеграционные. `tariffs_from`, `event_cap_from`, `exceeds_cap` —
чистые функции настроек/чисел; собраны в этом же файле, чтобы `-k
semantic_queue` по-прежнему выбирал их все (решение плана 7).

Помощники (`_bucket`, `_context`, `_job`, `_attempt`) — ЛОКАЛЬНАЯ копия
помощников `test_semantic_queue_schema.py`, не импорт (тот же приём в
докстроке `test_semantic_queue_material.py`: наборы помощников тестов проекта
друг у друга не импортируют).
"""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest

from config import Settings
from models import (
    CatalogContext,
    ContextBucket,
    DecisionSource,
    NameRole,
    SemanticJob,
    SemanticJobAttempt,
    SemanticJobStatus,
    SemanticKind,
    SemanticState,
)
from services.semantic_cost import (
    RESERVE_FORMULA_VERSION,
    EventCap,
    Tariffs,
    event_cap_from,
    exceeds_cap,
    expected_cached_cost,
    known_prefix_tokens,
    reserve_for,
    spent_last_24h,
    tariffs_from,
)
from services.semantic_request import RenderedRequest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
#  Помощники (локальная копия)
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _bucket(session, factories) -> ContextBucket:
    cp = factories.CatalogPositionFactory.create()
    b = ContextBucket(catalog_position_id=cp.id, work_category_id=None)
    session.add(b)
    session.flush()
    return b


def _context(session, factories, **overrides) -> CatalogContext:
    bucket = overrides.pop("bucket", None) or _bucket(session, factories)
    defaults = dict(
        bucket_id=bucket.id,
        is_default=False,
        semantic_kind=SemanticKind.WORK.value,
        semantic_kind_source=DecisionSource.rule.value,
        semantic_kind_by=None,
        semantic_kind_at=_now(),
        name_role=NameRole.WORK.value,
        name_role_source=DecisionSource.rule.value,
        name_role_by=None,
        name_role_at=_now(),
        place_dictionary_version=1,
        semantic_state=SemanticState.SUGGESTED.value,
    )
    defaults.update(overrides)
    ctx = CatalogContext(**defaults)
    session.add(ctx)
    session.flush()
    return ctx


def _job(session, factories, context=None, **overrides) -> SemanticJob:
    context = context or _context(session, factories)
    defaults = dict(
        context_id=context.id,
        request_hash=f"req-{_uid()}",
        status=SemanticJobStatus.pending.value,
        cancel_reason=None,
        claim_token=None,
        prompt_version="v1",
        model_requested="gpt-test",
        place_dictionary_version=1,
        candidates_hash=f"cand-{_uid()}",
        prefix_hash=f"prefix-{_uid()}",
        input_hash=f"input-{_uid()}",
        response_schema_version="v1",
        serialization_version="v1",
    )
    defaults.update(overrides)
    job = SemanticJob(**defaults)
    session.add(job)
    session.flush()
    return job


def _attempt(session, factories, job=None, **overrides) -> SemanticJobAttempt:
    job = job or _job(session, factories)
    defaults = dict(
        job_id=job.id,
        claim_token=uuid.uuid4(),
        retry_generation=0,
        started_at=_now(),
        reserve_usd=Decimal("0.10"),
        prefix_hash=f"prefix-{_uid()}",
        privacy_dictionary_hash=f"pdict-{_uid()}",
    )
    defaults.update(overrides)
    attempt = SemanticJobAttempt(**defaults)
    session.add(attempt)
    session.flush()
    return attempt


def _settings(**overrides) -> Settings:
    base = dict(SECRET_KEY="x" * 32)
    base.update(overrides)
    return Settings(**base)


def _rendered(**overrides) -> RenderedRequest:
    defaults = dict(
        body={"max_tokens": 600},
        request_hash="req-hash",
        prefix_hash=f"prefix-{_uid()}",
        candidates_hash="cand-hash",
        input_hash="input-hash",
        prefix_bytes=1000,
        user_bytes=200,
        place_dictionary_version=1,
    )
    defaults.update(overrides)
    return RenderedRequest(**defaults)


# ---------------------------------------------------------------------------
#  tariffs_from / event_cap_from — сборка из настроек
# ---------------------------------------------------------------------------

class TestTariffsFrom:
    def test_maps_all_four_prices_from_settings(self):
        settings = _settings(
            SEMANTIC_PRICE_INPUT_PER_M=Decimal("3"),
            SEMANTIC_PRICE_CACHE_WRITE_PER_M=Decimal("4"),
            SEMANTIC_PRICE_CACHE_READ_PER_M=Decimal("0.5"),
            SEMANTIC_PRICE_OUTPUT_PER_M=Decimal("12"),
        )
        tariffs = tariffs_from(settings)
        assert tariffs == Tariffs(
            input_per_m=Decimal("3"),
            cache_write_per_m=Decimal("4"),
            cache_read_per_m=Decimal("0.5"),
            output_per_m=Decimal("12"),
        )


class TestEventCapFrom:
    def test_maps_contexts_and_reserve_from_settings(self):
        settings = _settings(
            SEMANTIC_EVENT_MAX_CONTEXTS=2500,
            SEMANTIC_EVENT_MAX_RESERVE_USD=Decimal("20"),
        )
        cap = event_cap_from(settings)
        assert cap == EventCap(max_contexts=2500, max_reserve_usd=Decimal("20"))


# ---------------------------------------------------------------------------
#  known_prefix_tokens
# ---------------------------------------------------------------------------

class TestKnownPrefixTokens:
    def test_no_attempts_returns_none(self, db_session, factories):
        assert known_prefix_tokens(db_session, f"prefix-{_uid()}") is None

    def test_all_values_null_returns_none(self, db_session, factories):
        prefix = f"prefix-{_uid()}"
        _attempt(db_session, factories, prefix_hash=prefix, cache_write_tokens=None, cached_tokens=None)
        _attempt(db_session, factories, prefix_hash=prefix, cache_write_tokens=None, cached_tokens=None)
        assert known_prefix_tokens(db_session, prefix) is None

    def test_uses_cache_write_when_cached_is_null(self, db_session, factories):
        prefix = f"prefix-{_uid()}"
        _attempt(db_session, factories, prefix_hash=prefix, cache_write_tokens=777, cached_tokens=None)
        assert known_prefix_tokens(db_session, prefix) == 777

    def test_uses_cached_when_cache_write_is_null(self, db_session, factories):
        prefix = f"prefix-{_uid()}"
        _attempt(db_session, factories, prefix_hash=prefix, cache_write_tokens=None, cached_tokens=888)
        assert known_prefix_tokens(db_session, prefix) == 888

    def test_row_max_picks_cached_when_it_exceeds_cache_write(self, db_session, factories):
        prefix = f"prefix-{_uid()}"
        _attempt(db_session, factories, prefix_hash=prefix, cache_write_tokens=100, cached_tokens=900)
        assert known_prefix_tokens(db_session, prefix) == 900

    def test_row_max_picks_cache_write_when_it_exceeds_cached(self, db_session, factories):
        prefix = f"prefix-{_uid()}"
        _attempt(db_session, factories, prefix_hash=prefix, cache_write_tokens=900, cached_tokens=100)
        assert known_prefix_tokens(db_session, prefix) == 900

    def test_overall_max_across_different_models_and_providers(self, db_session, factories):
        prefix = f"prefix-{_uid()}"
        _attempt(
            db_session, factories, prefix_hash=prefix,
            actual_model="model-a", provider="prov-x",
            cache_write_tokens=100, cached_tokens=None,
        )
        _attempt(
            db_session, factories, prefix_hash=prefix,
            actual_model="model-b", provider="prov-y",
            cache_write_tokens=400, cached_tokens=None,
        )
        _attempt(
            db_session, factories, prefix_hash=prefix,
            actual_model="model-c", provider="prov-z",
            cache_write_tokens=None, cached_tokens=250,
        )
        assert known_prefix_tokens(db_session, prefix) == 400

    def test_other_prefix_hash_does_not_affect_result(self, db_session, factories):
        prefix = f"prefix-{_uid()}"
        other_prefix = f"prefix-{_uid()}"
        _attempt(db_session, factories, prefix_hash=prefix, cache_write_tokens=50, cached_tokens=None)
        _attempt(db_session, factories, prefix_hash=other_prefix, cache_write_tokens=99999, cached_tokens=None)
        assert known_prefix_tokens(db_session, prefix) == 50

    def test_all_zero_observations_returns_none(self, db_session, factories):
        # Провайдер без кэша (fallback маршрутизации, спека §2.11) отвечает
        # 0/0 — это отсутствие наблюдения префикса, а не наблюдение «префикс
        # из 0 токенов».
        prefix = f"prefix-{_uid()}"
        _attempt(db_session, factories, prefix_hash=prefix, cache_write_tokens=0, cached_tokens=0)
        _attempt(db_session, factories, prefix_hash=prefix, cache_write_tokens=0, cached_tokens=0)
        assert known_prefix_tokens(db_session, prefix) is None

    def test_zero_observation_ignored_next_to_real_value(self, db_session, factories):
        prefix = f"prefix-{_uid()}"
        _attempt(db_session, factories, prefix_hash=prefix, cache_write_tokens=0, cached_tokens=0)
        _attempt(db_session, factories, prefix_hash=prefix, cache_write_tokens=8058, cached_tokens=None)
        assert known_prefix_tokens(db_session, prefix) == 8058


# ---------------------------------------------------------------------------
#  reserve_for
# ---------------------------------------------------------------------------

class TestReserveFor:
    def test_known_prefix_uses_observed_tokens_and_cache_write_tariff(self, db_session, factories):
        prefix = f"prefix-{_uid()}"
        _attempt(db_session, factories, prefix_hash=prefix, cache_write_tokens=500, cached_tokens=300)
        tariffs = Tariffs(
            input_per_m=Decimal("2"),
            cache_write_per_m=Decimal("2.5"),
            cache_read_per_m=Decimal("0.2"),
            output_per_m=Decimal("10"),
        )
        # prefix_bytes огромный и НЕ должен использоваться — известный
        # префикс (500) обязан победить.
        rendered = _rendered(prefix_hash=prefix, prefix_bytes=999_999, user_bytes=1000)
        result = reserve_for(db_session, rendered, tariffs, max_tokens=600)
        expected = (
            Decimal(500) * Decimal("2.5") + Decimal(1000) * Decimal("2") + Decimal(600) * Decimal("10")
        ) / Decimal(1_000_000)
        assert expected == Decimal("0.00925")
        assert result == expected

    def test_unknown_prefix_uses_prefix_bytes(self, db_session, factories):
        prefix = f"prefix-{_uid()}"
        tariffs = Tariffs(
            input_per_m=Decimal("2"),
            cache_write_per_m=Decimal("2.5"),
            cache_read_per_m=Decimal("0.2"),
            output_per_m=Decimal("10"),
        )
        rendered = _rendered(prefix_hash=prefix, prefix_bytes=38_000, user_bytes=50)
        result = reserve_for(db_session, rendered, tariffs, max_tokens=600)
        expected = (
            Decimal(38_000) * Decimal("2.5") + Decimal(50) * Decimal("2") + Decimal(600) * Decimal("10")
        ) / Decimal(1_000_000)
        assert result == expected

    def test_result_is_decimal_without_float_drift(self, db_session, factories):
        prefix = f"prefix-{_uid()}"
        tariffs = Tariffs(
            input_per_m=Decimal("0.1"),
            cache_write_per_m=Decimal("0.1"),
            cache_read_per_m=Decimal("0.1"),
            output_per_m=Decimal("0.1"),
        )
        rendered = _rendered(prefix_hash=prefix, prefix_bytes=3, user_bytes=7)
        result = reserve_for(db_session, rendered, tariffs, max_tokens=11)
        # Эталон — литерал, посчитанный Decimal-арифметикой независимо от
        # функции: (3 + 7 + 11) * 0.1 / 1_000_000 = 2.1 / 1_000_000 =
        # 0.0000021 ровно. Тариф 0.1 float не представляет точно — любая
        # примесь float на пути дала бы хвост и сломала точное сравнение.
        assert isinstance(result, Decimal)
        assert result == Decimal("0.0000021")

    def test_max_tokens_argument_not_body_limit(self, db_session, factories):
        # Лимит в теле рендера (600) и лимит попытки (250) различаются —
        # резерв считается по аргументу.
        tariffs = Tariffs(
            input_per_m=Decimal("2"),
            cache_write_per_m=Decimal("2.5"),
            cache_read_per_m=Decimal("0.2"),
            output_per_m=Decimal("10"),
        )
        rendered = _rendered(
            prefix_hash=f"prefix-{_uid()}", prefix_bytes=1000, user_bytes=200, body={"max_tokens": 600}
        )
        result = reserve_for(db_session, rendered, tariffs, max_tokens=250)
        # 1000 × 2.5 + 200 × 2 + 250 × 10 = 5400 → ÷ 10⁶
        assert result == Decimal("0.0054")

    def test_zero_observations_fall_back_to_prefix_bytes(self, db_session, factories):
        # Все наблюдения этого префикса — 0/0 (провайдер без кэша): резерв
        # обязан посчитать префикс байтами, а не занизить его до нуля.
        prefix = f"prefix-{_uid()}"
        _attempt(db_session, factories, prefix_hash=prefix, cache_write_tokens=0, cached_tokens=0)
        tariffs = Tariffs(
            input_per_m=Decimal("2"),
            cache_write_per_m=Decimal("2.5"),
            cache_read_per_m=Decimal("0.2"),
            output_per_m=Decimal("10"),
        )
        rendered = _rendered(prefix_hash=prefix, prefix_bytes=38_000, user_bytes=50)
        result = reserve_for(db_session, rendered, tariffs, max_tokens=600)
        # 38000 × 2.5 + 50 × 2 + 600 × 10 = 101100 -> ÷ 10^6
        assert result == Decimal("0.1011")

    def test_new_observation_changes_reserve_for_same_render(self, db_session, factories):
        # Тот же рендер до и после первого наблюдения токенов префикса: резерв
        # обязан смениться, не меняя набора (на этом держится preview_hash).
        prefix = f"prefix-{_uid()}"
        tariffs = Tariffs(
            input_per_m=Decimal("2"),
            cache_write_per_m=Decimal("2.5"),
            cache_read_per_m=Decimal("0.2"),
            output_per_m=Decimal("10"),
        )
        rendered = _rendered(prefix_hash=prefix, prefix_bytes=38_000, user_bytes=50)
        before = reserve_for(db_session, rendered, tariffs, max_tokens=600)
        _attempt(db_session, factories, prefix_hash=prefix, cache_write_tokens=8058, cached_tokens=None)
        after = reserve_for(db_session, rendered, tariffs, max_tokens=600)
        # 38000 × 2.5 + 50 × 2 + 600 × 10 = 101100; 8058 × 2.5 + 100 + 6000 = 26245
        assert before == Decimal("0.1011")
        assert after == Decimal("0.026245")


# ---------------------------------------------------------------------------
#  expected_cached_cost
# ---------------------------------------------------------------------------

class TestExpectedCachedCost:
    def test_uses_cache_read_tariff_and_body_max_tokens(self, db_session, factories):
        prefix = f"prefix-{_uid()}"
        _attempt(db_session, factories, prefix_hash=prefix, cache_write_tokens=500, cached_tokens=300)
        tariffs = Tariffs(
            input_per_m=Decimal("2"),
            cache_write_per_m=Decimal("2.5"),
            cache_read_per_m=Decimal("0.2"),
            output_per_m=Decimal("10"),
        )
        rendered = _rendered(
            prefix_hash=prefix, prefix_bytes=999_999, user_bytes=1000, body={"max_tokens": 400}
        )
        result = expected_cached_cost(db_session, rendered, tariffs)
        expected = (
            Decimal(500) * Decimal("0.2") + Decimal(1000) * Decimal("2") + Decimal(400) * Decimal("10")
        ) / Decimal(1_000_000)
        assert result == expected
        # Сверка, что тариф чтения кэша (0.2), а не записи (2.5) — результат
        # не совпал бы с формулой reserve_for на тех же числах.
        reserve_style = (
            Decimal(500) * Decimal("2.5") + Decimal(1000) * Decimal("2") + Decimal(400) * Decimal("10")
        ) / Decimal(1_000_000)
        assert result != reserve_style

    def test_unknown_prefix_uses_prefix_bytes(self, db_session, factories):
        tariffs = Tariffs(
            input_per_m=Decimal("2"),
            cache_write_per_m=Decimal("2.5"),
            cache_read_per_m=Decimal("0.2"),
            output_per_m=Decimal("10"),
        )
        rendered = _rendered(
            prefix_hash=f"prefix-{_uid()}", prefix_bytes=38_000, user_bytes=50, body={"max_tokens": 600}
        )
        result = expected_cached_cost(db_session, rendered, tariffs)
        # 38000 × 0.2 + 50 × 2 + 600 × 10 = 13700 → ÷ 10⁶
        assert result == Decimal("0.0137")

    def test_result_is_decimal_without_float_drift(self, db_session, factories):
        tariffs = Tariffs(
            input_per_m=Decimal("0.1"),
            cache_write_per_m=Decimal("0.1"),
            cache_read_per_m=Decimal("0.1"),
            output_per_m=Decimal("0.1"),
        )
        rendered = _rendered(
            prefix_hash=f"prefix-{_uid()}", prefix_bytes=3, user_bytes=7, body={"max_tokens": 11}
        )
        result = expected_cached_cost(db_session, rendered, tariffs)
        # (3 + 7 + 11) × 0.1 ÷ 10⁶ = 0.0000021 ровно; float дал бы хвост.
        assert isinstance(result, Decimal)
        assert result == Decimal("0.0000021")


# ---------------------------------------------------------------------------
#  spent_last_24h
# ---------------------------------------------------------------------------

class TestSpentLast24h:
    def test_empty_window_returns_zero(self, db_session, factories):
        now = _now()
        result = spent_last_24h(db_session, now=now)
        assert isinstance(result, Decimal)
        assert result == Decimal("0")

    def test_sums_all_attempts_in_window(self, db_session, factories):
        now = _now()
        _attempt(
            db_session, factories, started_at=now, finished_at=now,
            cost_usd=Decimal("1.23"), reserve_usd=Decimal("9.99"),
        )
        _attempt(
            db_session, factories, started_at=now - dt.timedelta(hours=2), finished_at=None,
            cost_usd=None, reserve_usd=Decimal("0.05"),
        )
        _attempt(
            db_session, factories, started_at=now - dt.timedelta(hours=3), finished_at=now,
            cost_usd=None, reserve_usd=Decimal("0.07"),
        )
        _attempt(
            db_session, factories, started_at=now - dt.timedelta(hours=30), finished_at=now,
            cost_usd=Decimal("5.00"), reserve_usd=Decimal("5.00"),
        )
        # 1.23 + 0.05 + 0.07; попытка 30 ч назад вне окна.
        assert spent_last_24h(db_session, now=now) == Decimal("1.35")

    def test_window_is_anchored_at_passed_now_not_db_clock(self, db_session, factories):
        # Момент на трое суток раньше часов базы: попытка за час до него — в
        # окне переданного `now`, но вне окна от текущего времени сервера.
        now = _now() - dt.timedelta(days=3)
        _attempt(
            db_session, factories, started_at=now - dt.timedelta(hours=1),
            finished_at=now, cost_usd=Decimal("0.40"),
        )
        assert spent_last_24h(db_session, now=now) == Decimal("0.40")

    def test_completed_attempt_counted_by_cost_usd(self, db_session, factories):
        now = _now()
        _attempt(
            db_session, factories, started_at=now, finished_at=now,
            cost_usd=Decimal("1.23"), reserve_usd=Decimal("9.99"),
        )
        assert spent_last_24h(db_session, now=now) == Decimal("1.23")

    def test_unfinished_attempt_counted_by_reserve_usd(self, db_session, factories):
        now = _now()
        _attempt(
            db_session, factories, started_at=now, finished_at=None,
            cost_usd=None, reserve_usd=Decimal("0.05"),
        )
        assert spent_last_24h(db_session, now=now) == Decimal("0.05")

    def test_finished_without_cost_counted_by_reserve_usd(self, db_session, factories):
        now = _now()
        _attempt(
            db_session, factories, started_at=now, finished_at=now,
            cost_usd=None, reserve_usd=Decimal("0.07"),
        )
        assert spent_last_24h(db_session, now=now) == Decimal("0.07")

    def test_boundary_exactly_24h_included(self, db_session, factories):
        now = _now()
        _attempt(
            db_session, factories, started_at=now - dt.timedelta(hours=24),
            cost_usd=Decimal("2.00"),
        )
        assert spent_last_24h(db_session, now=now) == Decimal("2.00")

    def test_boundary_24h_plus_one_second_excluded(self, db_session, factories):
        now = _now()
        _attempt(
            db_session, factories,
            started_at=now - dt.timedelta(hours=24, seconds=1),
            cost_usd=Decimal("2.00"),
        )
        assert spent_last_24h(db_session, now=now) == Decimal("0")


# ---------------------------------------------------------------------------
#  exceeds_cap
# ---------------------------------------------------------------------------

class TestExceedsCap:
    def test_count_equal_to_cap_is_not_exceeded(self):
        cap = EventCap(max_contexts=3000, max_reserve_usd=Decimal("15"))
        assert exceeds_cap(3000, Decimal("1"), cap) is False

    def test_count_one_over_cap_is_exceeded(self):
        cap = EventCap(max_contexts=3000, max_reserve_usd=Decimal("15"))
        assert exceeds_cap(3001, Decimal("1"), cap) is True

    def test_reserve_equal_to_cap_is_not_exceeded(self):
        cap = EventCap(max_contexts=3000, max_reserve_usd=Decimal("15"))
        assert exceeds_cap(1, Decimal("15"), cap) is False

    def test_reserve_one_cent_over_cap_is_exceeded(self):
        cap = EventCap(max_contexts=3000, max_reserve_usd=Decimal("15"))
        assert exceeds_cap(1, Decimal("15.01"), cap) is True


def test_reserve_formula_version_is_frozen_at_one():
    assert RESERVE_FORMULA_VERSION == 1
