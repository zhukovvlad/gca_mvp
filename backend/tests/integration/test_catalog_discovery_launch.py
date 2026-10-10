"""Запуск открытия семей: preview, отказы, блок «Открыть семьи», маршруты
(спека 3б §2.3, §2.12; DoD 8)."""
# ruff: noqa: F811 — `world` — фикстура, импортированная из соседнего набора
from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy import event

import services.family_discovery as fd
from config import settings as app_settings
from crud.discovery import discovery_units
from models import (
    SemanticJob,
    SemanticJobAttempt,
    SemanticReconcileBatch,
    UserRole,
)
from routers import semantic as semantic_router
from services.family_discovery import (
    DISCOVERY_PROMPT_LABEL,
    DiscoveryError,
    discovery_scope,
    launch_discovery,
    preview_discovery,
    render_discovery_request,
)
from services.semantic_cost import tariffs_from
from services.variant_answer import DISCOVERY_RESPONSE_SCHEMA_VERSION
from tests.integration.test_catalog_discovery_scope import (
    _ctx_bare_unit,
    _ctx_family_unit,
    _make,
    _make_system,
    _uncategorize,
    world,  # noqa: F401 — фикстура
)
from tests.integration.test_semantic_queue_api import _make_job

pytestmark = pytest.mark.integration

S = app_settings
BASE = "/api/v1/semantic"


def _jobs(db) -> list[SemanticJob]:
    return list(db.execute(sa.select(SemanticJob).order_by(SemanticJob.id)).scalars())


def _preview(w, unit_id, *, settings=S):
    return preview_discovery(w.db, unit_id=unit_id, settings=settings)


def _launch(w, unit_id, *, preview_hash=None, settings=S):
    if preview_hash is None:
        preview_hash = _preview(w, unit_id, settings=settings).preview_hash
    return launch_discovery(
        w.db, unit_id=unit_id, preview_hash=preview_hash, actor_id=w.admin.id, settings=settings
    )


def _refusal(w, unit_id, *, preview_hash=None, settings=S) -> str:
    """Код отказа запуска; состояние базы после отказа не меняется."""
    before = len(_jobs(w.db))
    with pytest.raises(DiscoveryError) as raised:
        _launch(w, unit_id, preview_hash=preview_hash, settings=settings)
    assert len(_jobs(w.db)) == before, "отказ не должен оставлять задание"
    assert str(raised.value), "у отказа есть текст"
    return raised.value.code


def _held_batch(db, fingerprints) -> SemanticReconcileBatch:
    batch = SemanticReconcileBatch(
        source="mass", held_fingerprints=fingerprints,
        fingerprints_hash=uuid.uuid4().hex, contexts_count=len(fingerprints),
        reserve_estimate_usd=Decimal("1"), cached_estimate_usd=Decimal("1"), status="held",
    )
    db.add(batch)
    db.flush()
    return batch


def _fingerprint(kind, context_id, *, schema_id=None):
    return {
        "kind": kind, "context_id": context_id, "family_id": None, "schema_id": schema_id,
        "request_hash": uuid.uuid4().hex,
    }


# ---------------------------------------------------------------------------
#  Preview
# ---------------------------------------------------------------------------

class TestPreview:
    def test_preview_carries_counts_active_families_and_a_money_estimate(self, world):
        _ctx_bare_unit(world, "Голая А")
        _ctx_bare_unit(world, "Голая Б")

        preview = _preview(world, world.bare_unit)

        assert preview.unit_id == world.bare_unit
        assert preview.counts == discovery_scope(world.db, world.bare_unit).counts
        assert preview.counts.names == 2 and preview.active_families == 0
        assert preview.reserve_usd > preview.expected_cached_usd > 0
        assert len(preview.preview_hash) == 64

    def test_active_families_are_counted(self, world):
        system = _ctx_family_unit(world, "Система")
        _make_system(world.db, system, world.admin.id)

        assert _preview(world, world.family_unit).active_families == 1

    def test_estimate_without_an_observed_prefix_uses_prefix_bytes(self, world):
        _ctx_bare_unit(world, "Голая")
        rendered = render_discovery_request(
            discovery_scope(world.db, world.bare_unit), world.db, settings=S
        )
        # Ревью задачи 3: тарифы открытия отличны от тарифов предложений — по
        # умолчанию они совпадают, и оценка по чужому виду проходила бы.
        distinct = S.model_copy(
            update={
                "SEMANTIC_DISCOVERY_PRICE_INPUT_PER_M": Decimal("4.1"),
                "SEMANTIC_DISCOVERY_PRICE_CACHE_WRITE_PER_M": Decimal("4.2"),
                "SEMANTIC_DISCOVERY_PRICE_CACHE_READ_PER_M": Decimal("4.3"),
                "SEMANTIC_DISCOVERY_PRICE_OUTPUT_PER_M": Decimal("4.4"),
            }
        )
        assert tariffs_from(distinct, "family_discovery") != tariffs_from(
            distinct, "family_suggestion"
        )
        tariffs = tariffs_from(distinct, "family_discovery")
        # Независимый расчёт: байты префикса по тарифу записи кэша, байты строки
        # по тарифу входа, лимит ответа по тарифу выхода, всё на миллион.
        expected_reserve = (
            Decimal(rendered.prefix_bytes) * tariffs.cache_write_per_m
            + Decimal(rendered.user_bytes) * tariffs.input_per_m
            + Decimal(32000) * tariffs.output_per_m
        ) / Decimal(1_000_000)
        expected_cached = (
            Decimal(rendered.prefix_bytes) * tariffs.cache_read_per_m
            + Decimal(rendered.user_bytes) * tariffs.input_per_m
            + Decimal(32000) * tariffs.output_per_m
        ) / Decimal(1_000_000)

        preview = _preview(world, world.bare_unit, settings=distinct)

        assert preview.reserve_usd == expected_reserve
        assert preview.expected_cached_usd == expected_cached

    def test_estimate_with_an_observed_prefix_uses_its_tokens(self, world):
        _ctx_bare_unit(world, "Голая")
        rendered = render_discovery_request(
            discovery_scope(world.db, world.bare_unit), world.db, settings=S
        )
        job = _make_job(world.db, _ctx_bare_unit(world, "Для попытки"))
        world.db.add(
            SemanticJobAttempt(
                job_id=job.id, claim_token=uuid.uuid4(), retry_generation=0,
                started_at=dt.datetime.now(dt.UTC), reserve_usd=Decimal("0.01"),
                prefix_hash=rendered.prefix_hash, privacy_dictionary_hash="d",
                cache_write_tokens=4321, cached_tokens=0,
            )
        )
        world.db.flush()
        # Контекст добавлен после рендера эталона, но префикс (`system`) от
        # состава имён не зависит.
        tariffs = tariffs_from(S, "family_discovery")
        current = render_discovery_request(
            discovery_scope(world.db, world.bare_unit), world.db, settings=S
        )
        assert current.prefix_hash == rendered.prefix_hash

        preview = _preview(world, world.bare_unit)

        expected = (
            Decimal(4321) * tariffs.cache_write_per_m
            + Decimal(current.user_bytes) * tariffs.input_per_m
            + Decimal(32000) * tariffs.output_per_m
        ) / Decimal(1_000_000)
        assert preview.reserve_usd == expected

    def test_hash_is_sha256_of_request_reserve_cached_tariffs_and_formula_version(self, world):
        _ctx_bare_unit(world, "Голая")
        scope = discovery_scope(world.db, world.bare_unit)
        rendered = render_discovery_request(scope, world.db, settings=S)
        preview = _preview(world, world.bare_unit)
        tariffs = tariffs_from(S, "family_discovery")

        expected = hashlib.sha256(
            json.dumps(
                {
                    "request_hash": rendered.request_hash,
                    "reserve_usd": str(preview.reserve_usd),
                    "cached_usd": str(preview.expected_cached_usd),
                    "tariffs": {
                        "family_discovery": {
                            "input_per_m": str(tariffs.input_per_m),
                            "cache_write_per_m": str(tariffs.cache_write_per_m),
                            "cache_read_per_m": str(tariffs.cache_read_per_m),
                            "output_per_m": str(tariffs.output_per_m),
                        }
                    },
                    "reserve_formula_version": 1,
                },
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        assert preview.preview_hash == expected

    def test_hash_changes_with_the_scope(self, world):
        _ctx_bare_unit(world, "Голая А")
        before = _preview(world, world.bare_unit).preview_hash
        _ctx_bare_unit(world, "Голая Б")

        assert _preview(world, world.bare_unit).preview_hash != before

    def test_hash_changes_with_a_discovery_tariff(self, world):
        _ctx_bare_unit(world, "Голая")
        before = _preview(world, world.bare_unit).preview_hash
        pricey = S.model_copy(update={"SEMANTIC_DISCOVERY_PRICE_INPUT_PER_M": Decimal("7")})

        assert _preview(world, world.bare_unit, settings=pricey).preview_hash != before

    def test_hash_changes_with_the_reserve_formula_version(self, world, monkeypatch):
        _ctx_bare_unit(world, "Голая")
        before = _preview(world, world.bare_unit).preview_hash
        monkeypatch.setattr(fd, "RESERVE_FORMULA_VERSION", 2)

        assert _preview(world, world.bare_unit).preview_hash != before

    def test_hash_is_stable_for_one_state(self, world):
        _ctx_bare_unit(world, "Голая")

        assert _preview(world, world.bare_unit) == _preview(world, world.bare_unit)

    def test_preview_of_an_empty_unit_does_not_refuse(self, world):
        preview = _preview(world, world.bare_unit)

        assert preview.counts.names == 0 and len(preview.preview_hash) == 64

    def test_unit_none_is_a_legal_input(self, world):
        _make(world.db, world.factories, world.proposal, None, "Без единицы")

        preview = _preview(world, None)

        assert preview.unit_id is None and preview.counts.names == 1


# ---------------------------------------------------------------------------
#  Запуск
# ---------------------------------------------------------------------------

class TestLaunch:
    def test_launch_creates_a_pending_job_with_the_discovery_columns(self, world):
        _ctx_bare_unit(world, "Голая")
        rendered = render_discovery_request(
            discovery_scope(world.db, world.bare_unit), world.db, settings=S
        )

        job = _launch(world, world.bare_unit)

        assert (job.kind, job.status, job.unit_id) == ("family_discovery", "pending", world.bare_unit)
        assert (job.context_id, job.family_id, job.schema_id, job.batch_id) == (None, None, None, None)
        assert job.request_hash == rendered.request_hash
        assert job.prefix_hash == rendered.prefix_hash
        assert job.input_hash == rendered.input_hash
        assert job.candidates_hash == rendered.candidates_hash
        assert job.prompt_version == "discovery:1" == DISCOVERY_PROMPT_LABEL
        assert job.response_schema_version == DISCOVERY_RESPONSE_SCHEMA_VERSION
        assert job.model_requested == S.SEMANTIC_DISCOVERY_MODEL
        assert job.serialization_version == "1"

    def test_launch_makes_no_batch_and_no_attempt(self, world):
        _ctx_bare_unit(world, "Голая")

        _launch(world, world.bare_unit)

        assert world.db.execute(sa.select(SemanticReconcileBatch)).first() is None
        assert world.db.execute(sa.select(SemanticJobAttempt)).first() is None

    def test_launch_ignores_the_event_cap(self, world, monkeypatch):
        monkeypatch.setattr(app_settings, "SEMANTIC_EVENT_MAX_CONTEXTS", 0)
        monkeypatch.setattr(app_settings, "SEMANTIC_EVENT_MAX_RESERVE_USD", Decimal("0"))
        _ctx_bare_unit(world, "Голая")

        job = _launch(world, world.bare_unit, settings=app_settings)

        assert job.status == "pending"

    def test_empty_scope_with_uncategorized_families_still_launches(self, world):
        _uncategorize(world.db, world.family.id)

        job = _launch(world, world.family_unit)

        assert job.status == "pending"

    def test_unit_none_launches(self, world):
        _make(world.db, world.factories, world.proposal, None, "Без единицы")

        job = _launch(world, None)

        assert job.unit_id is None and job.status == "pending"

    def test_launches_of_two_units_are_independent(self, world):
        _ctx_bare_unit(world, "Голая")
        system = _ctx_family_unit(world, "Система")
        _make_system(world.db, system, world.admin.id)

        first = _launch(world, world.bare_unit)
        second = _launch(world, world.family_unit)

        assert first.id != second.id

    def test_unit_without_a_live_job_launches_after_the_previous_one_is_done(self, world):
        context_id = _ctx_bare_unit(world, "Голая")
        job = _launch(world, world.bare_unit)
        world.db.execute(sa.update(SemanticJob).where(SemanticJob.id == job.id).values(status="done"))
        world.db.expire_all()
        _ctx_bare_unit(world, "Новая строка после открытия")

        second = _launch(world, world.bare_unit)

        assert second.id != job.id and context_id is not None


class TestRefusals:
    @pytest.mark.parametrize("status", ["pending", "running", "privacy_hold"])
    def test_live_discovery_job_of_the_unit_is_in_progress(self, world, status):
        _ctx_bare_unit(world, "Голая")
        job = _launch(world, world.bare_unit)
        values = {"status": status}
        if status == "running":
            values["claim_token"] = uuid.uuid4()
        if status == "privacy_hold":
            values["privacy_matches"] = [{"text": "x", "kind": "contractor", "where": "context"}]
        world.db.execute(sa.update(SemanticJob).where(SemanticJob.id == job.id).values(**values))
        world.db.expire_all()
        _ctx_bare_unit(world, f"Новая строка {status}")

        assert _refusal(world, world.bare_unit) == "discovery_in_progress"

    def test_live_discovery_job_of_another_unit_does_not_block(self, world):
        _ctx_bare_unit(world, "Голая")
        system = _ctx_family_unit(world, "Система")
        _make_system(world.db, system, world.admin.id)
        _launch(world, world.family_unit)

        assert _launch(world, world.bare_unit).status == "pending"

    @pytest.mark.parametrize("status", ["pending", "running"])
    def test_open_suggestion_job_in_the_unit_makes_it_busy(self, world, status):
        context_id = _ctx_bare_unit(world, "Голая")
        values = {"status": status}
        job = _make_job(world.db, context_id, unit_id=world.bare_unit)
        if status == "running":
            values["claim_token"] = uuid.uuid4()
        world.db.execute(sa.update(SemanticJob).where(SemanticJob.id == job.id).values(**values))
        world.db.expire_all()

        assert _refusal(world, world.bare_unit) == "discovery_unit_busy"

    @pytest.mark.parametrize("status", ["done", "error", "cancelled", "privacy_hold"])
    def test_closed_or_held_suggestion_job_does_not_make_the_unit_busy(self, world, status):
        context_id = _ctx_bare_unit(world, "Голая")
        job = _make_job(world.db, context_id, unit_id=world.bare_unit)
        values = {"status": status}
        if status == "cancelled":
            values["cancel_reason"] = "not_applicable"
        if status == "privacy_hold":
            values["privacy_matches"] = [{"text": "x", "kind": "contractor", "where": "context"}]
        world.db.execute(sa.update(SemanticJob).where(SemanticJob.id == job.id).values(**values))
        world.db.expire_all()

        assert _launch(world, world.bare_unit).status == "pending"

    def test_suggestion_job_of_another_unit_does_not_make_it_busy(self, world):
        _ctx_bare_unit(world, "Голая")
        other = _ctx_family_unit(world, "Работа другой единицы")
        _make_job(world.db, other, unit_id=world.family_unit)

        assert _launch(world, world.bare_unit).status == "pending"

    def test_held_batch_touching_the_unit_makes_it_busy(self, world):
        context_id = _ctx_bare_unit(world, "Голая")
        _held_batch(world.db, [_fingerprint("family_suggestion", context_id)])

        assert _refusal(world, world.bare_unit) == "discovery_unit_busy"

    def test_held_batch_of_another_unit_does_not_block(self, world):
        _ctx_bare_unit(world, "Голая")
        other = _ctx_family_unit(world, "Работа другой единицы")
        _held_batch(world.db, [_fingerprint("family_suggestion", other)])

        assert _launch(world, world.bare_unit).status == "pending"

    def test_held_batch_of_context_values_only_does_not_block(self, world):
        context_id = _ctx_bare_unit(world, "Голая")
        _held_batch(world.db, [_fingerprint("context_values", context_id, schema_id=1)])

        assert _launch(world, world.bare_unit).status == "pending"

    def test_empty_scope_and_no_uncategorized_families_is_nothing_to_do(self, world):
        assert _refusal(world, world.bare_unit) == "discovery_nothing_to_do"

    def test_empty_scope_of_a_unit_with_only_categorized_families_is_nothing_to_do(self, world):
        assert _refusal(world, world.family_unit) == "discovery_nothing_to_do"

    def test_more_names_than_the_limit_is_too_many(self, world):
        for n in range(3):
            _ctx_bare_unit(world, f"Имя {n}")
        small = S.model_copy(update={"SEMANTIC_DISCOVERY_MAX_NAMES": 2})

        assert _refusal(world, world.bare_unit, settings=small) == "discovery_too_many_names"

    def test_names_exactly_at_the_limit_launch(self, world):
        for n in range(2):
            _ctx_bare_unit(world, f"Имя {n}")
        small = S.model_copy(update={"SEMANTIC_DISCOVERY_MAX_NAMES": 2})

        assert _launch(world, world.bare_unit, settings=small).status == "pending"

    def test_limit_counts_distinct_names_not_contexts(self, world, monkeypatch):
        from tests.integration.test_catalog_discovery_scope import _leaf_categories

        cats = _leaf_categories(world.db, 2)
        _, cp = _make(
            world.db, world.factories, world.proposal, world.bare_unit, "Общее имя",
            article_id=cats[0],
        )
        _make(
            world.db, world.factories, world.proposal, world.bare_unit, "Общее имя", cp=cp,
            article_id=cats[1],
        )
        one = S.model_copy(update={"SEMANTIC_DISCOVERY_MAX_NAMES": 1})

        assert _launch(world, world.bare_unit, settings=one).status == "pending"

    @pytest.mark.parametrize("status", ["done", "error", "cancelled"])
    def test_same_request_hash_in_any_closed_status_is_input_unchanged(self, world, status):
        _ctx_bare_unit(world, "Голая")
        job = _launch(world, world.bare_unit)
        values = {"status": status}
        if status == "cancelled":
            values["cancel_reason"] = "input_changed"
        world.db.execute(sa.update(SemanticJob).where(SemanticJob.id == job.id).values(**values))
        world.db.expire_all()

        assert _refusal(world, world.bare_unit) == "discovery_input_unchanged"

    def test_changed_input_after_a_closed_job_launches(self, world):
        _ctx_bare_unit(world, "Голая")
        job = _launch(world, world.bare_unit)
        world.db.execute(sa.update(SemanticJob).where(SemanticJob.id == job.id).values(status="done"))
        world.db.expire_all()
        _ctx_bare_unit(world, "Другая")

        assert _launch(world, world.bare_unit).id != job.id

    def test_stale_preview_after_a_scope_change_is_preview_changed(self, world):
        _ctx_bare_unit(world, "Голая А")
        stale = _preview(world, world.bare_unit).preview_hash
        _ctx_bare_unit(world, "Голая Б")

        assert _refusal(world, world.bare_unit, preview_hash=stale) == "preview_changed"

    def test_stale_preview_after_a_tariff_change_is_preview_changed(self, world):
        _ctx_bare_unit(world, "Голая")
        stale = _preview(world, world.bare_unit).preview_hash
        pricey = S.model_copy(update={"SEMANTIC_DISCOVERY_PRICE_OUTPUT_PER_M": Decimal("11")})

        assert _refusal(world, world.bare_unit, preview_hash=stale, settings=pricey) == "preview_changed"

    def test_garbage_preview_hash_is_preview_changed(self, world):
        _ctx_bare_unit(world, "Голая")

        assert _refusal(world, world.bare_unit, preview_hash="0" * 64) == "preview_changed"

    def test_refusal_text_names_the_unit_and_numbers(self, world):
        for n in range(3):
            _ctx_bare_unit(world, f"Имя {n}")
        small = S.model_copy(update={"SEMANTIC_DISCOVERY_MAX_NAMES": 2})

        with pytest.raises(DiscoveryError) as raised:
            _launch(world, world.bare_unit, settings=small)

        text = str(raised.value)
        assert "«M3»" in text and "3 различных" in text and "предела 2" in text

    def test_refusal_text_for_unit_none_says_without_unit(self, world):
        with pytest.raises(DiscoveryError) as raised:
            _launch(world, None)

        assert "«без единицы»" in str(raised.value)

    def test_unique_index_is_the_second_line_for_a_racing_launch(self, world):
        """Ключ живого открытия существует и ловит вторую живую строку единицы.
        Что запуск переводит его срабатывание в код отказа, а не в сырой
        `IntegrityError`, проверяет `TestSecondLineMapsTheCodes`."""
        _ctx_bare_unit(world, "Голая")
        first = _launch(world, world.bare_unit)
        assert first.id
        # Обход проверок «живое открытие»: вставка с другим хэшем, но той же единицей.
        duplicate = SemanticJob(
            kind="family_discovery", status="pending", unit_id=world.bare_unit,
            request_hash="другой-хэш", prompt_version="discovery:1", model_requested="m",
            place_dictionary_version=1, candidates_hash="c", prefix_hash="p", input_hash="i",
            response_schema_version="v", serialization_version="1",
        )
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError) as raised, world.db.begin_nested():
            world.db.add(duplicate)
            world.db.flush()
        assert (
            raised.value.orig.diag.constraint_name == "uq_semantic_jobs_discovery_live"
        )


def _rival_job(unit_id, *, request_hash, status) -> dict:
    """Строка конкурирующего задания открытия для вставки мимо ORM."""
    return dict(
        kind="family_discovery", status=status, unit_id=unit_id, request_hash=request_hash,
        prompt_version="discovery:1", model_requested="m", place_dictionary_version=1,
        candidates_hash="c", prefix_hash="p", input_hash="i", response_schema_version="v",
        serialization_version="1",
    )


class TestSecondLineMapsTheCodes:
    """Ревью задачи 3: обе проверки запуска уже пройдены, а между ними и вставкой
    конкурент вставил своё задание (вставка — в `before_flush` той же транзакции).
    Ключ ловит вставку, и запуск отдаёт тот же код, что и первая линия, а не
    сырой `IntegrityError`."""

    def _race(self, w, rival: dict) -> str:
        preview = _preview(w, w.bare_unit)
        fired: list[bool] = []

        def _before_flush(session, _flush_context, _instances):
            if fired:
                return
            if any(isinstance(o, SemanticJob) for o in session.new):
                fired.append(True)
                session.connection().execute(sa.insert(SemanticJob.__table__).values(**rival))

        event.listen(w.db, "before_flush", _before_flush)
        try:
            with pytest.raises(DiscoveryError) as raised:
                launch_discovery(
                    w.db, unit_id=w.bare_unit, preview_hash=preview.preview_hash,
                    actor_id=w.admin.id, settings=S,
                )
        finally:
            event.remove(w.db, "before_flush", _before_flush)
        assert fired == [True], "вход теста: конкурент вставлен до вставки запуска"
        assert _jobs(w.db) == [], "отказ откатывает и вставку конкурента в той же точке сохранения"
        return raised.value.code

    def test_racing_live_job_of_the_unit_is_in_progress(self, world):
        _ctx_bare_unit(world, "Голая")

        code = self._race(
            world, _rival_job(world.bare_unit, request_hash="конкурент", status="pending")
        )

        assert code == "discovery_in_progress"

    def test_racing_job_with_the_same_input_is_input_unchanged(self, world):
        _ctx_bare_unit(world, "Голая")
        same = render_discovery_request(
            discovery_scope(world.db, world.bare_unit), world.db, settings=S
        ).request_hash

        code = self._race(world, _rival_job(world.bare_unit, request_hash=same, status="done"))

        assert code == "discovery_input_unchanged"


class TestRefusalTextsAreTheSpecification:
    """Ревью задачи 3: тексты отказов запуска — дословно таблица §2.12 спеки 3б."""

    def _text(self, w, unit_id, *, settings=S, preview_hash=None) -> str:
        with pytest.raises(DiscoveryError) as raised:
            _launch(w, unit_id, preview_hash=preview_hash, settings=settings)
        return str(raised.value)

    def test_in_progress(self, world):
        _ctx_bare_unit(world, "Голая")
        _launch(world, world.bare_unit)

        assert self._text(world, world.bare_unit) == (
            "Открытие семей для единицы «M3» уже идёт — дождитесь результата или разберите "
            "задержанное."
        )

    def test_unit_busy(self, world):
        context_id = _ctx_bare_unit(world, "Голая")
        _make_job(world.db, context_id, unit_id=world.bare_unit)

        assert self._text(world, world.bare_unit) == (
            "В единице «M3» идёт перезапрос: 1 заданий предложений ещё не выполнены. Откройте "
            "семьи, когда он закончится, — иначе черновики устареют до прихода."
        )

    def test_nothing_to_do(self, world):
        assert self._text(world, world.bare_unit) == (
            "В единице «M3» нет строк без семьи и семей без категории — открывать нечего."
        )

    def test_too_many_names(self, world):
        for n in range(3):
            _ctx_bare_unit(world, f"Имя {n}")
        small = S.model_copy(update={"SEMANTIC_DISCOVERY_MAX_NAMES": 2})

        assert self._text(world, world.bare_unit, settings=small) == (
            "В единице «M3» 3 различных наименований без семьи — больше предела 2 одного "
            "открытия."
        )

    def test_input_unchanged(self, world):
        _ctx_bare_unit(world, "Голая")
        job = _launch(world, world.bare_unit)
        world.db.execute(sa.update(SemanticJob).where(SemanticJob.id == job.id).values(status="done"))
        world.db.expire_all()

        assert self._text(world, world.bare_unit) == (
            "С прошлого открытия единицы «M3» ничего не изменилось — его черновики и есть "
            "ответ. Правьте, сливайте или отбрасывайте их."
        )


# ---------------------------------------------------------------------------
#  Блок «Открыть семьи»
# ---------------------------------------------------------------------------

class TestDiscoveryUnits:
    def test_row_per_unit_with_scope_and_uncategorized_families(self, world):
        _ctx_bare_unit(world, "Голая А")
        _ctx_bare_unit(world, "Голая Б")
        system = _ctx_family_unit(world, "Система")
        _make_system(world.db, system, world.admin.id)
        _uncategorize(world.db, world.family.id)

        rows = {row["unit_id"]: row for row in discovery_units(world.db, settings=S)}

        assert set(rows) == {world.bare_unit, world.family_unit}
        bare, fam = rows[world.bare_unit], rows[world.family_unit]
        assert (bare["unit_code"], bare["bare"], bare["names"], bare["systems"]) == ("M3", 2, 2, 0)
        assert (fam["unit_code"], fam["systems"], fam["uncategorized_families"]) == ("M2", 1, 1)
        assert fam["active_families"] == 1

    def test_row_numbers_equal_the_scope_of_the_same_unit(self, world):
        _ctx_bare_unit(world, "Голая")
        new = _ctx_family_unit(world, "Работа с новой семьёй")
        from tests.integration.test_semantic_queue_api import _published

        _published(world.db, new, family_id=None, new_family_name="Кладка")

        for row in discovery_units(world.db, settings=S):
            counts = discovery_scope(world.db, row["unit_id"]).counts
            assert (
                row["systems"], row["new_family"], row["bare"], row["names"],
                row["uncategorized_families"],
            ) == (
                counts.systems, counts.new_family, counts.bare, counts.names,
                counts.uncategorized_families,
            )

    def test_unit_without_scope_and_without_uncategorized_families_has_no_row(self, world):
        _ctx_family_unit(world, "Работа без предложения")

        assert discovery_units(world.db, settings=S) == []

    def test_unit_with_only_uncategorized_families_has_a_row_without_names(self, world):
        _uncategorize(world.db, world.family.id)

        (row,) = discovery_units(world.db, settings=S)

        assert (row["unit_id"], row["names"], row["uncategorized_families"]) == (
            world.family_unit, 0, 1,
        )

    def test_unit_none_has_its_own_row(self, world):
        _make(world.db, world.factories, world.proposal, None, "Без единицы")
        _ctx_bare_unit(world, "Голая")

        rows = discovery_units(world.db, settings=S)

        assert [r["unit_id"] for r in rows] == [world.bare_unit, None]
        assert rows[-1]["unit_code"] is None and rows[-1]["names"] == 1

    def test_estimate_equals_the_preview_of_the_unit(self, world):
        _ctx_bare_unit(world, "Голая")

        (row,) = discovery_units(world.db, settings=S)
        preview = _preview(world, world.bare_unit)

        assert row["reserve_usd"] == format(preview.reserve_usd, "f")
        assert row["expected_cached_usd"] == format(preview.expected_cached_usd, "f")

    def test_last_discovery_is_none_before_any_launch(self, world):
        _ctx_bare_unit(world, "Голая")

        (row,) = discovery_units(world.db, settings=S)

        assert row["last_discovery"] is None

    def test_last_discovery_reports_status_time_and_open_drafts(self, world):
        from models import FamilyDraft
        from tests.factories import seed_category_id

        _ctx_bare_unit(world, "Голая")
        job = _launch(world, world.bare_unit)
        world.db.execute(sa.update(SemanticJob).where(SemanticJob.id == job.id).values(status="done"))
        for ordinal, status in ((1, "open"), (2, "open"), (3, "discarded")):
            values = dict(
                job_id=job.id, unit_id=world.bare_unit, ordinal=ordinal, grp="new", title="Т",
                definition="О", family_category_id=seed_category_id(world.db), status=status,
            )
            if status == "discarded":
                values.update(decided_by=world.admin.id, decided_at=dt.datetime.now(dt.UTC))
            world.db.add(FamilyDraft(**values))
        # Ревью задачи 3: открытые группы «в активную семью» и «не работа» —
        # не черновики новых семей и в число не входят.
        world.db.add(
            FamilyDraft(
                job_id=job.id, unit_id=world.bare_unit, ordinal=4, grp="existing",
                existing_family_id=world.family.id, status="open",
            )
        )
        world.db.add(
            FamilyDraft(job_id=job.id, unit_id=world.bare_unit, ordinal=5, grp="not_work", status="open")
        )
        world.db.flush()
        world.db.expire_all()
        _ctx_bare_unit(world, "Голая — новая строка")

        (row,) = discovery_units(world.db, settings=S)

        last = row["last_discovery"]
        assert (last["job_id"], last["status"], last["open_drafts"]) == (job.id, "done", 2)
        assert dt.datetime.fromisoformat(last["at"]).tzinfo is not None

    def test_last_discovery_is_the_latest_job_of_the_unit(self, world):
        context_id = _ctx_bare_unit(world, "Голая")
        first = _launch(world, world.bare_unit)
        world.db.execute(sa.update(SemanticJob).where(SemanticJob.id == first.id).values(status="done"))
        world.db.expire_all()
        _ctx_bare_unit(world, "Ещё")
        second = _launch(world, world.bare_unit)

        (row,) = discovery_units(world.db, settings=S)

        assert row["last_discovery"]["job_id"] == second.id and context_id


# ---------------------------------------------------------------------------
#  Маршруты
# ---------------------------------------------------------------------------

ROUTES = (
    ("GET", f"{BASE}/discovery/units", None),
    ("POST", f"{BASE}/discovery/preview", {"unit_id": None}),
    ("POST", f"{BASE}/discovery", {"unit_id": None, "preview_hash": "x"}),
)


def _detail(response) -> dict:
    body = response.json()["detail"]
    assert isinstance(body, dict), body
    return body


class TestRoutes:
    @pytest.mark.parametrize(("method", "path", "body"), ROUTES)
    def test_member_gets_403(self, client, world, method, path, body):
        client.auth_state["role"] = UserRole.member

        response = client.request(method, path, json=body)

        assert response.status_code == 403

    def test_units_route_returns_the_block(self, client, world):
        _ctx_bare_unit(world, "Голая")

        response = client.get(f"{BASE}/discovery/units")

        assert response.status_code == 200
        (row,) = response.json()["units"]
        assert (row["unit_id"], row["unit_code"], row["names"], row["bare"]) == (
            world.bare_unit, "M3", 1, 1,
        )
        assert row["last_discovery"] is None
        assert Decimal(row["reserve_usd"]) > 0

    def test_preview_route_then_launch_route(self, client, world):
        _ctx_bare_unit(world, "Голая")

        preview = client.post(f"{BASE}/discovery/preview", json={"unit_id": world.bare_unit})
        assert preview.status_code == 200, preview.text
        payload = preview.json()
        assert payload["unit_id"] == world.bare_unit
        assert payload["counts"]["names"] == 1 and payload["active_families"] == 0
        assert len(payload["preview_hash"]) == 64

        launched = client.post(
            f"{BASE}/discovery",
            json={"unit_id": world.bare_unit, "preview_hash": payload["preview_hash"]},
        )

        assert launched.status_code == 200, launched.text
        body = launched.json()
        assert (body["status"], body["unit_id"]) == ("pending", world.bare_unit)
        assert [j.id for j in _jobs(world.db)] == [body["job_id"]]

    def test_unit_null_passes_through_preview_and_launch(self, client, world):
        _make(world.db, world.factories, world.proposal, None, "Без единицы")

        preview = client.post(f"{BASE}/discovery/preview", json={"unit_id": None})
        assert preview.status_code == 200 and preview.json()["unit_id"] is None
        launched = client.post(
            f"{BASE}/discovery",
            json={"unit_id": None, "preview_hash": preview.json()["preview_hash"]},
        )

        assert launched.status_code == 200 and launched.json()["unit_id"] is None

    def test_missing_unit_id_is_422(self, client, world):
        assert client.post(f"{BASE}/discovery/preview", json={}).status_code == 422
        assert client.post(f"{BASE}/discovery", json={"preview_hash": "x"}).status_code == 422

    def _expect(self, client, unit_id, preview_hash, code):
        response = client.post(
            f"{BASE}/discovery", json={"unit_id": unit_id, "preview_hash": preview_hash}
        )
        assert response.status_code == 409, response.text
        detail = _detail(response)
        assert detail["code"] == code
        assert detail["message"]

    def test_in_progress_is_409(self, client, world):
        _ctx_bare_unit(world, "Голая")
        _launch(world, world.bare_unit)

        self._expect(client, world.bare_unit, "x", "discovery_in_progress")

    def test_unit_busy_is_409(self, client, world):
        context_id = _ctx_bare_unit(world, "Голая")
        _make_job(world.db, context_id, unit_id=world.bare_unit)

        self._expect(client, world.bare_unit, "x", "discovery_unit_busy")

    def test_nothing_to_do_is_409(self, client, world):
        self._expect(client, world.bare_unit, "x", "discovery_nothing_to_do")

    def test_too_many_names_is_409(self, client, world, monkeypatch):
        monkeypatch.setattr(app_settings, "SEMANTIC_DISCOVERY_MAX_NAMES", 1)
        _ctx_bare_unit(world, "Имя А")
        _ctx_bare_unit(world, "Имя Б")

        self._expect(client, world.bare_unit, "x", "discovery_too_many_names")

    def test_input_unchanged_is_409(self, client, world):
        _ctx_bare_unit(world, "Голая")
        job = _launch(world, world.bare_unit)
        world.db.execute(sa.update(SemanticJob).where(SemanticJob.id == job.id).values(status="done"))
        world.db.expire_all()

        self._expect(client, world.bare_unit, "x", "discovery_input_unchanged")

    def test_preview_changed_is_409(self, client, world):
        _ctx_bare_unit(world, "Голая")

        self._expect(client, world.bare_unit, "x", "preview_changed")

    def test_refusal_rolls_back_and_leaves_no_job(self, client, world):
        _ctx_bare_unit(world, "Голая")

        client.post(f"{BASE}/discovery", json={"unit_id": world.bare_unit, "preview_hash": "x"})

        assert _jobs(world.db) == []

    @pytest.mark.parametrize(
        "code",
        [
            "discovery_in_progress", "discovery_unit_busy", "discovery_nothing_to_do",
            "discovery_too_many_names", "discovery_input_unchanged", "preview_changed",
        ],
    )
    def test_every_code_is_in_the_status_map_as_409(self, code):
        assert semantic_router._status_for_code(code) == 409

    def test_codes_of_the_service_are_exactly_the_map_entries(self):
        codes = {
            fd.REFUSE_DISCOVERY_IN_PROGRESS, fd.REFUSE_DISCOVERY_UNIT_BUSY,
            fd.REFUSE_DISCOVERY_NOTHING_TO_DO, fd.REFUSE_DISCOVERY_TOO_MANY_NAMES,
            fd.REFUSE_DISCOVERY_INPUT_UNCHANGED, fd.REFUSE_PREVIEW_CHANGED,
        }
        assert codes <= semantic_router._STATUS_CONFLICT
        assert len(codes) == 6
