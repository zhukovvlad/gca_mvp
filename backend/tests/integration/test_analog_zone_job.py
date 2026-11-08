"""Задание модели `section_zone`: тело запроса, захват, запись ответа, тарифы,
очередь «Зоны» и команда `zones-backfill` (лестница аналогов, спека §2.5, §2.17,
§2.19).

Модель подменена клиентом: настоящих вызовов нет. Решение человека побеждает всё
(К2): ответ модели после решения человека остаётся только в попытке и цепочку не
меняет. Названия разделов общие; в теле запроса нет цены, объёма, подрядчика и
номера договора (проверка по вхождению подстрок, не по числу блоков).
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest
import sqlalchemy as sa
from click.testing import CliRunner

import cli
import services.semantic_worker as worker
from config import settings as app_settings
from crud.section_chains import assign_section_chains, decide_zone
from models import (
    SectionChain,
    SemanticAttemptOutcome,
    SemanticCancelReason,
    SemanticJob,
    SemanticJobAttempt,
    SemanticJobKind,
    SemanticJobStatus,
    UserRole,
)
from services.semantic_client import ModelResponse, TransientModelError
from services.semantic_cost import reserve_for_known_prefix, tariffs_from
from services.semantic_worker import claim_next, process_one, record_result, render_job_request
from services.zone_request import render_zone_request
from tests.integration.test_analog_chains import (
    _chain_of,
    _chapters,
    _estimate,
    _row,
    _scene,
    _zone_jobs,
)

pytestmark = pytest.mark.integration

S = app_settings.model_copy(
    update={
        "SEMANTIC_ZONE_AUTO_ACCEPT_THRESHOLD": Decimal("0.95"),
        "SEMANTIC_ZONE_MAX_TOKENS": 300,
        "SEMANTIC_DAILY_BUDGET_USD": Decimal("30"),
        "SEMANTIC_MAX_ATTEMPTS": 3,
    }
)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC) + dt.timedelta(minutes=5)


def _answer(zone="underground", confidence=0.97, reason="Подземная часть") -> str:
    return json.dumps({"zone": zone, "confidence": confidence, "reason": reason}, ensure_ascii=False)


def _response(content: str) -> ModelResponse:
    return ModelResponse(
        content=content, actual_model="anthropic/claude-test", provider="P1",
        prompt_tokens=700, completion_tokens=90, cache_write_tokens=0, cached_tokens=0,
        cost_usd=Decimal("0.002"),
    )


def _queued(db, factories, titles=("Раздел без шаблонов",), *rows):
    """Смета с цепочкой, которую правило не решает; возвращает (цепочка, задание)."""
    estimate, _, _, items = _scene(factories, list(titles), *rows)
    assign_section_chains(db, [estimate.id])
    chain = _chain_of(db, items[0])
    (job,) = _zone_jobs(db)
    return chain, job


def _claim(db, settings=S):
    claim = claim_next(db, settings=settings, now=_now())
    assert claim is not None, "захвата нет"
    return claim


def _fresh(db, model, pk):
    db.expire_all()
    return db.get(model, pk)


def _attempt(db, job_id) -> SemanticJobAttempt:
    db.expire_all()
    return db.execute(
        sa.select(SemanticJobAttempt).where(SemanticJobAttempt.job_id == job_id)
    ).scalar_one()


# ---------------------------------------------------------------------------
#  Настройки и тарифы
# ---------------------------------------------------------------------------

class TestSettingsAndTariffs:
    def test_new_settings_have_the_agreed_defaults(self):
        fresh = type(app_settings)(_env_file=None)
        threshold, max_tokens = fresh.SEMANTIC_ZONE_AUTO_ACCEPT_THRESHOLD, fresh.SEMANTIC_ZONE_MAX_TOKENS
        assert (threshold, max_tokens) == (Decimal("0.95"), 300)

    def test_section_zone_uses_the_family_suggestion_tariffs(self):
        custom = S.model_copy(
            update={
                "SEMANTIC_PRICE_INPUT_PER_M": Decimal("11"),
                "SEMANTIC_PRICE_CACHE_WRITE_PER_M": Decimal("12"),
                "SEMANTIC_PRICE_CACHE_READ_PER_M": Decimal("13"),
                "SEMANTIC_PRICE_OUTPUT_PER_M": Decimal("14"),
                "SEMANTIC_SCHEMA_PRICE_INPUT_PER_M": Decimal("1"),
            }
        )

        tariffs = tariffs_from(custom, SemanticJobKind.section_zone)

        assert (tariffs.input_per_m, tariffs.cache_write_per_m, tariffs.cache_read_per_m,
                tariffs.output_per_m) == (Decimal(11), Decimal(12), Decimal(13), Decimal(14))

    def test_releasable_params_still_has_no_silent_tariff(self):
        with pytest.raises(ValueError):
            tariffs_from(S, SemanticJobKind.releasable_params)


# ---------------------------------------------------------------------------
#  Тело запроса
# ---------------------------------------------------------------------------

class TestRender:
    def test_body_profile_is_the_family_suggestion_model_without_reasoning(
        self, db_session, factories
    ):
        chain, _ = _queued(db_session, factories)
        custom = S.model_copy(update={"SEMANTIC_ZONE_MAX_TOKENS": 123})

        body = render_zone_request(db_session, chain.id, settings=custom).body

        assert body["model"] == custom.SEMANTIC_MODEL
        assert body["max_tokens"] == 123
        assert body["reasoning"] == {"enabled": False}
        assert body["temperature"] == 0

    def test_user_text_has_chain_titles_top_down_and_row_names_with_unit(
        self, db_session, factories
    ):
        from services.unit_resolution import UnitResolver

        estimate, proposal = _estimate(factories)
        chapter = _chapters(factories, proposal, ["Корпус А", "Раздел без шаблонов"])
        unit_id = UnitResolver(db_session).resolve("м2").unit_id
        row = _row(factories, proposal, "Устройство стяжки", chapter, unit_id=unit_id)
        assign_section_chains(db_session, [estimate.id])
        chain = _chain_of(db_session, row)
        unit_name = db_session.execute(
            sa.text("select name from units_of_measure where id = :i"), {"i": unit_id}
        ).scalar_one()

        text = render_zone_request(db_session, chain.id, settings=S).body["messages"][1]["content"]

        assert text.index("Корпус А") < text.index("Раздел без шаблонов") < text.index(
            "Устройство стяжки"
        )
        assert f"Устройство стяжки [{unit_name}]" in text

    def test_no_price_volume_contractor_or_contract_number_in_the_body(
        self, db_session, factories
    ):
        contractor = factories.ContractorFactory.create(title="Подрядчик Секретный Омега")
        contract = factories.ContractFactory.create(
            contractor=contractor, contract_number="ДГП-777888"
        )
        estimate = factories.EstimateFactory.create(contract=contract)
        lot = factories.LotFactory.create(estimate=estimate)
        proposal = factories.ProposalFactory.create(lot=lot)
        chapter = _chapters(factories, proposal, ["Раздел без шаблонов"])
        _row(
            factories, proposal, "Устройство стяжки", chapter,
            unit_cost_total=Decimal("1234567.89"), total_cost_total=Decimal("7654321.11"),
            quantity=Decimal("98765.4"), suggested_quantity=Decimal("45678.9"),
        )
        assign_section_chains(db_session, [estimate.id])
        (job,) = _zone_jobs(db_session)

        body = render_zone_request(db_session, job.section_chain_id, settings=S).body

        dumped = json.dumps(body, ensure_ascii=False)
        for forbidden in ("1234567", "7654321", "98765", "45678", "Секретный", "777888"):
            assert forbidden not in dumped

    def test_at_most_ten_most_frequent_names_ties_by_smaller_id(self, db_session, factories):
        estimate, proposal = _estimate(factories)
        chapter = _chapters(factories, proposal, ["Раздел без шаблонов"])
        # «Частая» — 3 раза; остальные 11 имён — по одному, в порядке создания.
        for _ in range(3):
            _row(factories, proposal, "Частая", chapter)
        singles = [f"Одиночная {i:02d}" for i in range(11)]
        for name in singles:
            _row(factories, proposal, name, chapter)
        assign_section_chains(db_session, [estimate.id])
        (job,) = _zone_jobs(db_session)

        text = render_zone_request(
            db_session, job.section_chain_id, settings=S
        ).body["messages"][1]["content"]

        assert "Частая" in text
        assert text.count("Одиночная") == 9
        assert "Одиночная 08" in text and "Одиночная 09" not in text and "Одиночная 10" not in text

    def test_names_are_counted_across_estimates(self, db_session, factories):
        first, p1 = _estimate(factories)
        ch1 = _chapters(factories, p1, ["Раздел без шаблонов"])
        second, p2 = _estimate(factories, kp=True)
        ch2 = _chapters(factories, p2, ["Раздел без шаблонов"])
        for _ in range(2):
            _row(factories, p1, "Общая", ch1)
        _row(factories, p2, "Общая", ch2)
        for i in range(10):
            _row(factories, p1, f"Одиночная {i:02d}", ch1)
        assign_section_chains(db_session, [first.id, second.id])
        (job,) = _zone_jobs(db_session)

        text = render_zone_request(
            db_session, job.section_chain_id, settings=S
        ).body["messages"][1]["content"]

        assert "Общая" in text

    def test_new_rows_under_the_chain_do_not_change_the_fingerprint(self, db_session, factories):
        estimate, proposal = _estimate(factories)
        chapter = _chapters(factories, proposal, ["Раздел без шаблонов"])
        _row(factories, proposal, "Первая", chapter)
        assign_section_chains(db_session, [estimate.id])
        (job,) = _zone_jobs(db_session)
        before = render_zone_request(db_session, job.section_chain_id, settings=S)

        _row(factories, proposal, "Совсем новая строка", chapter)
        db_session.execute(
            sa.text("update position_items set section_chain_id = :c where section_chain_id is null"
                    " and is_chapter = false"),
            {"c": job.section_chain_id},
        )
        after = render_zone_request(db_session, job.section_chain_id, settings=S)

        assert before.body != after.body
        assert before.request_hash == after.request_hash == job.request_hash

    def test_other_chain_has_other_fingerprint(self, db_session, factories):
        first, _ = _queued(db_session, factories, ("Раздел без шаблонов",))
        estimate, _, _, items = _scene(factories, ["Другой раздел без шаблонов"])
        assign_section_chains(db_session, [estimate.id])
        second = _chain_of(db_session, items[0])

        a = render_zone_request(db_session, first.id, settings=S).request_hash
        b = render_zone_request(db_session, second.id, settings=S).request_hash

        assert a != b

    @pytest.mark.parametrize(
        "change",
        [
            {"SEMANTIC_MODEL": "anthropic/claude-other"},
            {"SEMANTIC_ZONE_MAX_TOKENS": 301},
        ],
    )
    def test_profile_changes_change_the_fingerprint(self, db_session, factories, change):
        chain, _ = _queued(db_session, factories)

        base = render_zone_request(db_session, chain.id, settings=S).request_hash
        changed = render_zone_request(
            db_session, chain.id, settings=S.model_copy(update=change)
        ).request_hash

        assert base != changed

    def test_job_is_cancelled_not_applicable_when_chain_already_has_zone(
        self, db_session, factories
    ):
        chain, job = _queued(db_session, factories)
        user = factories.UserFactory.create()
        decide_zone(db_session, chain.id, "whole", actor_id=user.id)

        render = render_job_request(db_session, job, settings=S)

        assert render.rendered is None
        assert render.cancel_reason == SemanticCancelReason.not_applicable


# ---------------------------------------------------------------------------
#  Захват: приватность, бюджет, резерв
# ---------------------------------------------------------------------------

class TestClaim:
    def test_claim_reserves_by_the_section_zone_tariffs(self, db_session, factories):
        custom = S.model_copy(
            update={
                "SEMANTIC_PRICE_INPUT_PER_M": Decimal("3"),
                "SEMANTIC_PRICE_CACHE_WRITE_PER_M": Decimal("5"),
                "SEMANTIC_PRICE_OUTPUT_PER_M": Decimal("700"),
                "SEMANTIC_SCHEMA_PRICE_OUTPUT_PER_M": Decimal("1"),
                "SEMANTIC_VALUES_PRICE_OUTPUT_PER_M": Decimal("1"),
                "SEMANTIC_DISCOVERY_PRICE_OUTPUT_PER_M": Decimal("1"),
            }
        )
        chain, job = _queued(db_session, factories)
        rendered = render_zone_request(db_session, chain.id, settings=custom)

        _claim(db_session, custom)

        attempt = _attempt(db_session, job.id)
        expected = reserve_for_known_prefix(
            rendered.prefix_bytes, rendered, tariffs_from(custom, SemanticJobKind.section_zone), 300
        )
        assert attempt.reserve_usd == expected
        assert expected > Decimal("0.2")  # тариф выхода 700 $/M × 300 — именно он

    def test_privacy_match_in_chain_title_holds_the_job(self, db_session, factories):
        factories.ContractorFactory.create(title="ООО «Ромашка Строй»")
        chain, job = _queued(
            db_session, factories, ("Корпус Ромашка Строй, раздел без шаблонов",)
        )

        claim = claim_next(db_session, settings=S, now=_now())

        assert claim is None
        held = _fresh(db_session, SemanticJob, job.id)
        assert held.status == SemanticJobStatus.privacy_hold.value
        assert held.privacy_matches
        assert _fresh(db_session, SectionChain, chain.id).zone is None

    def test_claim_cancels_the_job_when_chain_got_a_zone_meanwhile(self, db_session, factories):
        chain, job = _queued(db_session, factories)
        user = factories.UserFactory.create()
        decide_zone(db_session, chain.id, "whole", actor_id=user.id)

        claim = claim_next(db_session, settings=S, now=_now())

        assert claim is None
        cancelled = _fresh(db_session, SemanticJob, job.id)
        assert cancelled.status == SemanticJobStatus.cancelled.value
        assert cancelled.cancel_reason == SemanticCancelReason.not_applicable.value

    def test_claim_returns_the_zone_kind_with_the_rendered_body(self, db_session, factories):
        chain, job = _queued(db_session, factories)

        claim = _claim(db_session)

        assert claim.kind == SemanticJobKind.section_zone
        assert claim.rendered.request_hash == job.request_hash


# ---------------------------------------------------------------------------
#  Запись ответа
# ---------------------------------------------------------------------------

class TestRecord:
    def _record(self, db, claim, content, settings=S):
        return record_result(db, claim, _response(content), now=_now(), settings=settings)

    @pytest.mark.parametrize(
        ("confidence", "zone", "accepted"),
        [
            (0.95, "underground", True),     # ровно порог
            (0.99, "territory", True),
            (0.949, "underground", False),   # по другую сторону порога
            (0.5, "whole", False),
            (0.99, "unknown", False),        # «не знаю» — предложение при любой уверенности
        ],
    )
    def test_acceptance_on_both_sides_of_the_threshold(
        self, db_session, factories, confidence, zone, accepted
    ):
        chain, job = _queued(db_session, factories)
        claim = _claim(db_session)

        self._record(db_session, claim, _answer(zone, confidence, "Причина"))

        chain = _fresh(db_session, SectionChain, chain.id)
        job = _fresh(db_session, SemanticJob, job.id)
        assert job.status == SemanticJobStatus.done.value and job.claim_token is None
        if accepted:
            assert (chain.zone, chain.zone_source) == (zone, "model")
            assert chain.proposed_zone is None and chain.decided_by is None
        else:
            assert chain.zone is None and chain.zone_source is None
            assert chain.proposed_zone == zone
            assert chain.proposed_confidence == Decimal(str(confidence))
            assert chain.proposed_reason == "Причина"

    def test_attempt_keeps_the_raw_answer_and_closes_ok(self, db_session, factories):
        chain, job = _queued(db_session, factories)
        claim = _claim(db_session)
        content = _answer()

        self._record(db_session, claim, content)

        attempt = _attempt(db_session, job.id)
        assert attempt.outcome == SemanticAttemptOutcome.ok.value
        assert attempt.raw_response == content
        assert attempt.finished_at is not None

    def test_human_decision_before_the_answer_wins_and_answer_stays_in_the_attempt(
        self, db_session, factories
    ):
        chain, job = _queued(db_session, factories)
        claim = _claim(db_session)
        user = factories.UserFactory.create()
        decide_zone(db_session, chain.id, "whole", actor_id=user.id)
        content = _answer("underground", 0.99, "Модель уверена")

        self._record(db_session, claim, content)

        chain = _fresh(db_session, SectionChain, chain.id)
        assert (chain.zone, chain.zone_source, chain.decided_by) == ("whole", "human", user.id)
        assert chain.proposed_zone is None
        attempt = _attempt(db_session, job.id)
        assert attempt.raw_response == content
        assert attempt.outcome == SemanticAttemptOutcome.ok.value
        assert _fresh(db_session, SemanticJob, job.id).status == SemanticJobStatus.done.value

    def test_low_confidence_answer_after_human_decision_leaves_no_proposal(
        self, db_session, factories
    ):
        chain, job = _queued(db_session, factories)
        claim = _claim(db_session)
        user = factories.UserFactory.create()
        decide_zone(db_session, chain.id, "whole", actor_id=user.id)

        self._record(db_session, claim, _answer("territory", 0.3, "Сомневаюсь"))

        chain = _fresh(db_session, SectionChain, chain.id)
        assert chain.zone == "whole" and chain.proposed_zone is None

    def test_zone_set_by_someone_else_meanwhile_is_not_overwritten(self, db_session, factories):
        chain, job = _queued(db_session, factories)
        claim = _claim(db_session)
        db_session.execute(
            sa.update(SectionChain).where(SectionChain.id == chain.id)
            .values(zone="aboveground", zone_source="rule")
        )

        self._record(db_session, claim, _answer("underground", 0.99))

        chain = _fresh(db_session, SectionChain, chain.id)
        assert (chain.zone, chain.zone_source) == ("aboveground", "rule")

    @pytest.mark.parametrize(
        "content",
        ["не json", json.dumps({"zone": "basement", "confidence": 0.9, "reason": "x"}),
         json.dumps({"zone": "whole", "confidence": 1.5, "reason": "x"}),
         json.dumps({"zone": "whole", "confidence": 0.9, "reason": " "})],
    )
    def test_answer_off_the_schema_is_an_attempt_error_and_changes_no_chain(
        self, db_session, factories, content
    ):
        chain, job = _queued(db_session, factories)
        claim = _claim(db_session)

        self._record(db_session, claim, content)

        job = _fresh(db_session, SemanticJob, job.id)
        assert job.status == SemanticJobStatus.error.value
        assert job.last_error_class == "schema_error"
        attempt = _attempt(db_session, job.id)
        assert attempt.outcome == SemanticAttemptOutcome.schema_error.value
        assert attempt.validation_error
        chain = _fresh(db_session, SectionChain, chain.id)
        assert chain.zone is None and chain.proposed_zone is None

    def test_lost_claim_writes_the_attempt_only(self, db_session, factories):
        chain, job = _queued(db_session, factories)
        claim = _claim(db_session)
        db_session.execute(
            sa.update(SemanticJob).where(SemanticJob.id == job.id)
            .values(status="pending", claim_token=None)
        )

        self._record(db_session, claim, _answer())

        chain = _fresh(db_session, SectionChain, chain.id)
        assert chain.zone is None and chain.proposed_zone is None
        assert _attempt(db_session, job.id).outcome == SemanticAttemptOutcome.lost_claim.value
        assert _fresh(db_session, SemanticJob, job.id).status == SemanticJobStatus.pending.value

    def test_changed_request_fingerprint_cancels_the_job_and_changes_no_chain(
        self, db_session, factories
    ):
        chain, job = _queued(db_session, factories)
        claim = _claim(db_session)
        other = S.model_copy(update={"SEMANTIC_ZONE_MAX_TOKENS": 301})

        self._record(db_session, claim, _answer("underground", 0.99), settings=other)

        job = _fresh(db_session, SemanticJob, job.id)
        assert job.status == SemanticJobStatus.cancelled.value
        assert job.cancel_reason == SemanticCancelReason.input_changed.value
        assert _fresh(db_session, SectionChain, chain.id).zone is None


# ---------------------------------------------------------------------------
#  Очередь от начала до конца
# ---------------------------------------------------------------------------

class _Client:
    def __init__(self, content=None, error=None):
        self.content, self.error, self.calls = content, error, []

    def complete(self, body, *, timeout_s):
        self.calls.append(body)
        if self.error is not None:
            raise self.error
        return _response(self.content)


class TestProcessOne:
    def _scene(self, committing_db, committing_factories):
        estimate, _, _, items = _scene(committing_factories, ["Раздел без шаблонов"])
        assign_section_chains(committing_db, [estimate.id])
        committing_db.commit()
        return _chain_of(committing_db, items[0])

    def test_confident_answer_sets_zone_through_the_real_loop(
        self, committing_db, committing_factories, committing_session_factory
    ):
        chain = self._scene(committing_db, committing_factories)
        client = _Client(_answer("aboveground", 0.99))

        ran = process_one(
            committing_session_factory, client, settings=S, clock=_now
        )

        assert ran is True and len(client.calls) == 1
        updated = _fresh(committing_db, SectionChain, chain.id)
        assert (updated.zone, updated.zone_source) == ("aboveground", "model")
        assert process_one(committing_session_factory, client, settings=S, clock=_now) is False

    def test_transient_failure_retries_and_changes_no_chain(
        self, committing_db, committing_factories, committing_session_factory
    ):
        chain = self._scene(committing_db, committing_factories)
        client = _Client(error=TransientModelError("сеть", error_class="Net"))

        process_one(committing_session_factory, client, settings=S, clock=_now)

        (job,) = _zone_jobs(committing_db)
        assert job.status == SemanticJobStatus.pending.value
        assert _fresh(committing_db, SectionChain, chain.id).zone is None

    def test_module_exports_are_wired_into_the_worker(self):
        assert worker.render_job_request is render_job_request


# ---------------------------------------------------------------------------
#  Маршруты
# ---------------------------------------------------------------------------

BASE = "/api/v1/semantic"


def _chains(db, **specs):
    out = {}
    for name, fields in specs.items():
        row = SectionChain(chain_key=name, titles=[name], **fields)
        db.add(row)
        out[name] = row
    # Коммит: роутер при отказе откатывает сессию, и несохранённые строки теста
    # ушли бы вместе с откатом.
    db.commit()
    return out


class TestRoutes:
    def test_list_returns_disputed_by_default(self, admin_client, db_session):
        rows = _chains(
            db_session,
            спорная=dict(proposed_zone="whole", proposed_confidence=Decimal("0.6"),
                         proposed_reason="Причина"),
            правило=dict(zone="underground", zone_source="rule"),
        )

        response = admin_client.get(f"{BASE}/section-chains")

        assert response.status_code == 200
        body = response.json()
        assert [i["id"] for i in body["items"]] == [rows["спорная"].id]
        assert body["total"] == 1
        assert body["items"][0]["proposed_confidence"] == "0.6"

    def test_list_filter_search_and_page_params(self, admin_client, db_session):
        rows = _chains(
            db_session,
            alpha=dict(zone="underground", zone_source="rule"),
            beta=dict(zone="whole", zone_source="model"),
        )

        by_filter = admin_client.get(f"{BASE}/section-chains", params={"filter": "model"}).json()
        by_query = admin_client.get(
            f"{BASE}/section-chains", params={"filter": "all", "q": "ALP"}
        ).json()
        page_two = admin_client.get(
            f"{BASE}/section-chains", params={"filter": "all", "page": 2}
        ).json()

        assert [i["id"] for i in by_filter["items"]] == [rows["beta"].id]
        assert [i["id"] for i in by_query["items"]] == [rows["alpha"].id]
        assert page_two["items"] == [] and page_two["total"] == 2

    def test_list_rejects_unknown_filter(self, admin_client):
        assert admin_client.get(f"{BASE}/section-chains", params={"filter": "x"}).status_code == 422

    def test_put_zone_is_a_human_decision_and_204(self, admin_client, db_session):
        row = _chains(
            db_session,
            спорная=dict(proposed_zone="whole", proposed_confidence=Decimal("0.6"),
                         proposed_reason="Причина"),
        )["спорная"]

        response = admin_client.put(
            f"{BASE}/section-chains/{row.id}/zone", json={"zone": "territory"}
        )

        assert response.status_code == 204
        db_session.refresh(row)
        assert (row.zone, row.zone_source, row.decided_by) == (
            "territory", "human", admin_client.user.id,
        )
        assert row.proposed_zone is None

    @pytest.mark.parametrize("zone", ["unknown", "basement", ""])
    def test_put_invalid_zone_is_422_invalid_zone(self, admin_client, db_session, zone):
        row = _chains(db_session, ключ=dict())["ключ"]

        response = admin_client.put(f"{BASE}/section-chains/{row.id}/zone", json={"zone": zone})

        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "invalid_zone"
        db_session.refresh(row)
        assert row.zone is None

    def test_put_unknown_chain_is_404(self, admin_client):
        response = admin_client.put(f"{BASE}/section-chains/987654321/zone", json={"zone": "whole"})
        assert response.status_code == 404

    def test_put_without_zone_field_is_422(self, admin_client, db_session):
        row = _chains(db_session, ключ=dict())["ключ"]
        assert admin_client.put(f"{BASE}/section-chains/{row.id}/zone", json={}).status_code == 422

    def test_member_is_refused_on_both_routes(self, member_client, db_session):
        row = _chains(db_session, ключ=dict())["ключ"]

        assert member_client.get(f"{BASE}/section-chains").status_code == 403
        assert member_client.put(
            f"{BASE}/section-chains/{row.id}/zone", json={"zone": "whole"}
        ).status_code == 403
        db_session.refresh(row)
        assert row.zone is None

    def test_status_counts_disputed_zones(self, admin_client, db_session):
        _chains(
            db_session,
            а=dict(proposed_zone="whole", proposed_confidence=Decimal("0.6"), proposed_reason="r"),
            б=dict(proposed_zone="unknown", proposed_confidence=Decimal("0.9"), proposed_reason="r"),
            в=dict(),
            г=dict(zone="whole", zone_source="model"),
        )

        body = admin_client.get(f"{BASE}/status").json()

        assert body["zones_disputed"] == 2

    def test_jobs_list_shows_a_failed_zone_job_by_chain_titles(
        self, admin_client, db_session, factories
    ):
        chain, job = _queued(db_session, factories, ("Корпус А", "Раздел без шаблонов"))
        db_session.execute(
            sa.update(SemanticJob).where(SemanticJob.id == job.id)
            .values(status="error", last_error_class="X")
        )

        response = admin_client.get(f"{BASE}/jobs", params={"status": "error"})

        assert response.status_code == 200
        (item,) = response.json()["items"]
        assert item["kind"] == "section_zone"
        assert "Корпус А" in item["title"] and "Раздел без шаблонов" in item["title"]

    def test_put_commits_on_success(self, committing_client, committing_db, committing_factories):
        from auth import get_current_user
        from main import app

        admin = committing_factories.UserFactory.create(role=UserRole.admin)
        row = SectionChain(chain_key="ключ", titles=["Ключ"])
        committing_db.add(row)
        committing_db.commit()
        app.dependency_overrides[get_current_user] = lambda: admin

        response = committing_client.put(
            f"{BASE}/section-chains/{row.id}/zone", json={"zone": "whole"}
        )

        assert response.status_code == 204
        committing_db.expire_all()
        stored = committing_db.get(SectionChain, row.id)
        assert (stored.zone, stored.zone_source) == ("whole", "human")

    def test_put_rolls_back_on_refusal(self, committing_client, committing_db, committing_factories):
        from auth import get_current_user
        from main import app

        admin = committing_factories.UserFactory.create(role=UserRole.admin)
        row = SectionChain(chain_key="ключ", titles=["Ключ"])
        committing_db.add(row)
        committing_db.commit()
        app.dependency_overrides[get_current_user] = lambda: admin

        response = committing_client.put(
            f"{BASE}/section-chains/{row.id}/zone", json={"zone": "unknown"}
        )

        assert response.status_code == 422
        committing_db.expire_all()
        assert committing_db.get(SectionChain, row.id).zone is None


# ---------------------------------------------------------------------------
#  Команда zones-backfill
# ---------------------------------------------------------------------------

class TestZonesBackfillCommand:
    def _seed(self, committing_db, committing_factories):
        _scene(committing_factories, ["Раздел без шаблонов"], "Р1", "Р2")
        _scene(committing_factories, ["Подземная часть"], "Р3")
        committing_db.commit()

    def _run(self, committing_session_factory, monkeypatch, *args):
        monkeypatch.setenv("APP_ENV", "prod")
        monkeypatch.setattr(cli, "SessionLocal", committing_session_factory)
        return CliRunner().invoke(cli.cli, ["zones-backfill", *args])

    def test_without_flags_it_assigns_chains_and_applies_the_rule_only(
        self, committing_db, committing_factories, committing_session_factory, monkeypatch
    ):
        self._seed(committing_db, committing_factories)

        result = self._run(committing_session_factory, monkeypatch)

        assert result.exit_code == 0, f"{result.output}\n{result.exception!r}"
        assert "Цепочек создано=2" in result.output
        assert "строк привязано=3" in result.output
        committing_db.expire_all()
        assert _zone_jobs(committing_db) == []
        zones = committing_db.execute(
            sa.select(SectionChain.zone).order_by(SectionChain.id)
        ).scalars().all()
        assert sorted(z or "" for z in zones) == ["", "underground"]

    def test_enqueue_prints_the_estimate_before_queueing_and_queues(
        self, committing_db, committing_factories, committing_session_factory, monkeypatch
    ):
        self._seed(committing_db, committing_factories)

        result = self._run(committing_session_factory, monkeypatch, "--enqueue")

        assert result.exit_code == 0, f"{result.output}\n{result.exception!r}"
        assert "К постановке заданий зоны: 1" in result.output
        assert "оценка резерва" in result.output
        assert result.output.index("К постановке") < result.output.index("Заданий зоны поставлено=1")
        assert len(_zone_jobs(committing_db)) == 1

    def test_second_run_is_idempotent(
        self, committing_db, committing_factories, committing_session_factory, monkeypatch
    ):
        self._seed(committing_db, committing_factories)
        self._run(committing_session_factory, monkeypatch, "--enqueue")

        result = self._run(committing_session_factory, monkeypatch, "--enqueue", "--reapply-rule")

        assert result.exit_code == 0, f"{result.output}\n{result.exception!r}"
        assert "Цепочек создано=0" in result.output
        assert "Заданий зоны поставлено=0" in result.output
        assert len(_zone_jobs(committing_db)) == 1

    def test_command_checks_the_mutation_guard_first(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "prod")
        reached = []

        def _record():
            reached.append(True)
            raise RuntimeError("stop-here")

        monkeypatch.setattr(cli, "SessionLocal", _record)
        CliRunner().invoke(cli.cli, ["zones-backfill"])

        assert reached
