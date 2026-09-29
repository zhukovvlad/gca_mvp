"""Расход семантических предложений: резерв попытки, ожидаемая цена при
попадании в кэш, суточный расход и потолок автоматического события (спека
`2026-09-28-semantic-suggestions-design.md` §2.11, абзац preview в §2.10).

Задача 5 фичи «Семантические предложения»
(`docs/superpowers/plans/2026-09-28-semantic-suggestions.md`). Модуль не
исполняет захват и не пишет очередь — только считает деньги на уже
загруженных данных (`RenderedRequest`, попытки в базе, `Settings`);
потребители — сверка (reconcile), исполнитель захвата (worker) и preview
экрана `admin`.

Тарифы — доллары за миллион токенов (`Settings.SEMANTIC_PRICE_*`), `Decimal`,
не константы кода: маршрутизация провайдера может сменить цену без правки
кода (спека §2.11). Деление на миллион — без округления; округление только
на показе (`AGENTS.md` §3 «деньги»).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from config import Settings
from models import SemanticJobAttempt
from services.semantic_request import RenderedRequest

#: Версия формулы резерва (спека §2.11): растёт при смене самой формулы (не
#: тарифов — они настройка, а не код). Пишется на попытке как аудит, каким
#: расчётом получен резерв.
RESERVE_FORMULA_VERSION = 1

#: Тариф — доллары за МИЛЛИОН токенов; деление на эту константу без
#: округления (округление — только на показе).
_PER_MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Tariffs:
    """Четыре тарифа расхода, доллары за миллион токенов (спека §2.11)."""

    input_per_m: Decimal
    cache_write_per_m: Decimal
    cache_read_per_m: Decimal
    output_per_m: Decimal


@dataclass(frozen=True)
class EventCap:
    """Потолок одного автоматического события сверки (спека §2.11): срабатывает
    то из двух условий, что наступит раньше."""

    max_contexts: int
    max_reserve_usd: Decimal


def tariffs_from(settings: Settings) -> Tariffs:
    """Собирает четыре тарифа из настроек (`SEMANTIC_PRICE_*`)."""
    return Tariffs(
        input_per_m=settings.SEMANTIC_PRICE_INPUT_PER_M,
        cache_write_per_m=settings.SEMANTIC_PRICE_CACHE_WRITE_PER_M,
        cache_read_per_m=settings.SEMANTIC_PRICE_CACHE_READ_PER_M,
        output_per_m=settings.SEMANTIC_PRICE_OUTPUT_PER_M,
    )


def event_cap_from(settings: Settings) -> EventCap:
    """Собирает потолок автоматического события из настроек
    (`SEMANTIC_EVENT_MAX_CONTEXTS`, `SEMANTIC_EVENT_MAX_RESERVE_USD`)."""
    return EventCap(
        max_contexts=settings.SEMANTIC_EVENT_MAX_CONTEXTS,
        max_reserve_usd=settings.SEMANTIC_EVENT_MAX_RESERVE_USD,
    )


def known_prefix_tokens(db: Session, prefix_hash: str) -> int | None:
    """Максимум `cache_write_tokens`/`cached_tokens` по всем попыткам с этим
    `prefix_hash`, по всем наблюдавшимся парам «фактическая модель,
    провайдер» (спека §2.11: провайдер следующего вызова заранее неизвестен —
    fallback маршрутизации). `NULL` игнорируется на обоих уровнях: построчный
    `GREATEST` берёт непустое из пары, агрегатный `MAX` — непустое из строк;
    нет попыток или у всех обе колонки `NULL` — результат `None`. Наблюдение
    0/0 (провайдер без кэша) — тоже отсутствие наблюдения префикса, а не
    наблюдение «префикс из 0 токенов»: `NULLIF(..., 0)` превращает ноль в
    `NULL` до агрегата, поэтому такая строка не занижает резерв. Один запрос,
    отдельно префикс не хранится (решение спеки 7)."""
    return db.execute(
        sa.select(
            sa.func.max(
                sa.func.nullif(
                    sa.func.greatest(
                        SemanticJobAttempt.cache_write_tokens, SemanticJobAttempt.cached_tokens
                    ),
                    0,
                )
            )
        ).where(SemanticJobAttempt.prefix_hash == prefix_hash)
    ).scalar_one()


def reserve_for_known_prefix(
    prefix_tokens: int, rendered: RenderedRequest, tariffs: Tariffs, max_tokens: int
) -> Decimal:
    """Тот же расчёт, что `reserve_for`, но без обращения к базе: токены
    префикса переданы явно (задача 6 — сверка на партии контекстов одной
    единицы делит один и тот же префикс, и узнаёт его токены ОДИН раз на
    `prefix_hash`, а не на контекст). `reserve_for` вызывает именно эту
    функцию — формула резерва живёт в одном месте."""
    return (
        Decimal(prefix_tokens) * tariffs.cache_write_per_m
        + Decimal(rendered.user_bytes) * tariffs.input_per_m
        + Decimal(max_tokens) * tariffs.output_per_m
    ) / _PER_MILLION


def reserve_for(
    db: Session, rendered: RenderedRequest, tariffs: Tariffs, max_tokens: int
) -> Decimal:
    """Резерв попытки — консервативная оценка ДО вызова провайдера (спека
    §2.11): `префикс × тариф записи кэша + строка × тариф входа + ответ ×
    тариф выхода`, где префикс — `known_prefix_tokens` по `prefix_hash`
    рендера или, без наблюдений, `rendered.prefix_bytes` (байты UTF-8).
    `max_tokens` попытки передаётся отдельно от `rendered.body["max_tokens"]`
    — вызывающий волен запросить меньше лимита настроек."""
    known = known_prefix_tokens(db, rendered.prefix_hash)
    prefix_tokens = known if known is not None else rendered.prefix_bytes
    return reserve_for_known_prefix(prefix_tokens, rendered, tariffs, max_tokens)


def expected_cached_cost_known_prefix(
    prefix_tokens: int, rendered: RenderedRequest, tariffs: Tariffs
) -> Decimal:
    """Тот же расчёт, что `expected_cached_cost`, но без обращения к базе —
    см. `reserve_for_known_prefix`; `expected_cached_cost` вызывает именно
    эту функцию."""
    max_tokens = rendered.body["max_tokens"]
    return (
        Decimal(prefix_tokens) * tariffs.cache_read_per_m
        + Decimal(rendered.user_bytes) * tariffs.input_per_m
        + Decimal(max_tokens) * tariffs.output_per_m
    ) / _PER_MILLION


def expected_cached_cost(db: Session, rendered: RenderedRequest, tariffs: Tariffs) -> Decimal:
    """Ожидаемая цена при попадании в кэш — ТОЛЬКО для preview экрана
    (спека §2.10); в потолке события не участвует (спека §2.11: «цена с
    кэшем в потолке не используется — только в preview»). Та же формула, что
    `reserve_for`, но префикс — по тарифу ЧТЕНИЯ кэша (`cache_read_per_m`), а
    не записи, и ответ — `rendered.body["max_tokens"]` (лимит настроек на
    момент рендера, не отдельный аргумент): это оценка СВЕРХУ при попадании
    в кэш, потому что фактический ответ обычно короче лимита, а не оценка
    снизу."""
    known = known_prefix_tokens(db, rendered.prefix_hash)
    prefix_tokens = known if known is not None else rendered.prefix_bytes
    return expected_cached_cost_known_prefix(prefix_tokens, rendered, tariffs)


def spent_last_24h(db: Session, *, now: dt.datetime) -> Decimal:
    """Суточный расход — сумма `cost_usd` завершённых и `reserve_usd`
    незавершённых попыток за скользящее окно `[now - 24h, +inf)` (спека
    §2.11): попытка, не вернувшая стоимость (таймаут, обрыв — `cost_usd IS
    NULL` независимо от `finished_at`), остаётся с резервом — провайдер мог
    её тарифицировать. Граница окна — включительно (`>=`), не строго позже.
    `now` передаётся вызывающим, не `func.now()` (детерминированный тест,
    один момент времени на все точки инварианта). Пустое окно — `Decimal
    ("0")`, не `NULL`."""
    window_start = now - dt.timedelta(hours=24)
    total = db.execute(
        sa.select(
            sa.func.sum(sa.func.coalesce(SemanticJobAttempt.cost_usd, SemanticJobAttempt.reserve_usd))
        ).where(SemanticJobAttempt.started_at >= window_start)
    ).scalar_one()
    return total if total is not None else Decimal("0")


def exceeds_cap(count: int, reserve_total: Decimal, cap: EventCap) -> bool:
    """Потолок одного автоматического события превышен, если число
    контекстов СТРОГО больше `cap.max_contexts` ИЛИ резерв СТРОГО больше
    `cap.max_reserve_usd` (спека §2.11: «что наступит раньше»); значение
    ровно на границе потолком не считается."""
    return count > cap.max_contexts or reserve_total > cap.max_reserve_usd
