"""Исполнитель очереди: захват, запись результата, повторы, предохранитель
(задача 10 фичи «Семантические предложения»).

План: `docs/superpowers/plans/2026-09-28-semantic-suggestions.md`, задача 10.
Спека: `docs/superpowers/specs/2026-09-28-semantic-suggestions-design.md`
§2.5 (захват, вызов, запись), §2.6 (повторы), §2.8 (публикация), §2.11
(резерв, суточный бюджет, предохранитель).

Один тест — одно строго отличающееся свойство: каждая ветвь захвата и каждый
исход записи — отдельный вход (`docs/insights/claimed-property-needs-its-own-
input.md`). Помощники — ЛОКАЛЬНАЯ копия помощников соседних наборов, не импорт.

Гонка бюджета (`TestBudgetRace`) — четыре настоящие сессии, и чередование в
них ПРИНУДИТЕЛЬНОЕ: каждая сессия останавливается барьером перед чтением
суточного расхода, то есть все четыре успевают увидеть один и тот же остаток.
Без этого проверка «блокировка строки состояния сериализует захваты» проходила
бы зелёной и без блокировки: одна сессия успевает закоммитить попытку раньше,
чем следующая читает расход. Парный тест снимает блокировку и требует, чтобы
прошли все четыре, — он доказывает, что барьер действительно даёт гонку.
Прогон потоков ограничен жёстким таймаутом.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import threading
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

import services.semantic_worker as worker
from config import settings as app_settings
from models import (
    CatalogContext,
    CatalogPosition,
    FamilySuggestion,
    SemanticJob,
    SemanticJobAttempt,
    SemanticWorkerState,
)
from services.context_routing import route_position
from services.semantic_client import (
    ModelResponse,
    PermanentModelError,
    TransientModelError,
)
from services.semantic_cost import spent_last_24h
from services.semantic_privacy import build_privacy_dictionary
from services.semantic_request import load_request_material, render_context_request
from services.semantic_worker import (
    claim_next,
    process_one,
    record_failure,
    record_result,
    serialize_privacy_matches,
)
from services.work_families import activate_family, assign_family, create_family

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 9, 29, 12, 0, tzinfo=dt.UTC)
_JOIN_TIMEOUT = 30.0

#: Настройки с явными тарифами и лимитами: тест считает резерв и бюджет
#: литералами, а не тем, что лежит в окружении.
S = app_settings.model_copy(
    update={
        "SEMANTIC_MAX_TOKENS": 600,
        "SEMANTIC_MAX_ATTEMPTS": 3,
        "SEMANTIC_CALL_TIMEOUT_S": 120,
        "SEMANTIC_DAILY_BUDGET_USD": Decimal("30"),
        "SEMANTIC_PRICE_INPUT_PER_M": Decimal("2"),
        "SEMANTIC_PRICE_CACHE_WRITE_PER_M": Decimal("2.5"),
        "SEMANTIC_PRICE_CACHE_READ_PER_M": Decimal("0.2"),
        "SEMANTIC_PRICE_OUTPUT_PER_M": Decimal("10"),
    }
)


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _proposal(factories):
    estimate = factories.EstimateFactory.create()
    lot = factories.LotFactory.create(estimate=estimate)
    return factories.ProposalFactory.create(lot=lot)


def _unit_id(db, code):
    from services.unit_resolution import UnitResolver

    return UnitResolver(db).resolve(code).unit_id


def _active_family(db, *, title, unit_name, actor_id):
    fam = create_family(
        db, title=title, unit_name=unit_name, definition="Определение семьи", actor_id=actor_id
    )
    return activate_family(db, family_id=fam.id, actor_id=actor_id)


def _simple_context(db, factories, proposal, *, unit_id, title) -> int:
    cp = factories.CatalogPositionFactory.create(unit_id=unit_id, standard_job_title=title)
    position = factories.PositionItemFactory.create(
        proposal=proposal, is_chapter=False, job_title_in_proposal=title, catalog_position_id=cp.id
    )
    return route_position(db, position_item_id=position.id).context_id


def _rendered(db, context_id, settings=S):
    material = load_request_material(db, [context_id])[context_id]
    return render_context_request(material, settings=settings)


def _job(
    db,
    context_id,
    *,
    request_hash=None,
    unit_id=None,
    next_attempt_at=None,
    status="pending",
    claim_token=None,
    privacy_released_matches=None,
    attempts_in_generation=0,
    settings=S,
) -> SemanticJob:
    """Задание на ТЕКУЩИЙ отпечаток контекста (или на заданный)."""
    rendered = _rendered(db, context_id, settings)
    job = SemanticJob(
        context_id=context_id,
        request_hash=request_hash or rendered.request_hash,
        status=status,
        claim_token=claim_token,
        unit_id=unit_id,
        next_attempt_at=next_attempt_at or NOW - dt.timedelta(minutes=1),
        privacy_released_matches=privacy_released_matches,
        attempts_in_generation=attempts_in_generation,
        prompt_version="1",
        model_requested=settings.SEMANTIC_MODEL,
        place_dictionary_version=rendered.place_dictionary_version,
        candidates_hash=rendered.candidates_hash,
        prefix_hash=rendered.prefix_hash,
        input_hash=rendered.input_hash,
        response_schema_version="1",
        serialization_version="1",
    )
    db.add(job)
    db.flush()
    return job


class _Scene:
    pass


def _scene(db, factories, *, titles=("Устройство пола",), unit="M2", with_family=True) -> _Scene:
    """Активная семья единицы и по контексту на каждое название; у каждого
    контекста — задание `pending` на текущий отпечаток."""
    scene = _Scene()
    scene.user = factories.UserFactory.create()
    scene.unit_id = _unit_id(db, unit)
    scene.family = (
        _active_family(db, title="Семья пола", unit_name=unit, actor_id=scene.user.id)
        if with_family
        else None
    )
    proposal = _proposal(factories)
    scene.context_ids = [
        _simple_context(db, factories, proposal, unit_id=scene.unit_id, title=t) for t in titles
    ]
    scene.jobs = [_job(db, cid, unit_id=scene.unit_id) for cid in scene.context_ids]
    return scene


def _reload(db, job_id) -> SemanticJob:
    db.expire_all()
    return db.get(SemanticJob, job_id)


def _attempts(db, job_id):
    db.expire_all()
    return list(
        db.execute(
            sa.select(SemanticJobAttempt)
            .where(SemanticJobAttempt.job_id == job_id)
            .order_by(SemanticJobAttempt.id)
        ).scalars()
    )


def _suggestions(db, context_id):
    db.expire_all()
    return list(
        db.execute(
            sa.select(FamilySuggestion)
            .where(FamilySuggestion.context_id == context_id)
            .order_by(FamilySuggestion.id)
        ).scalars()
    )


def _state(db) -> SemanticWorkerState:
    db.expire_all()
    return db.get(SemanticWorkerState, 1)


def _pause(db, context_id):
    """Остановка захвата прежней попыткой другого задания: пара «остановлен,
    попытка» обязательна, а у проверяемого задания попыток быть не должно."""
    old = SemanticJob(
        context_id=context_id, request_hash="pause-hash", status="done", prompt_version="1",
        model_requested="m", place_dictionary_version=1, candidates_hash="c", prefix_hash="p",
        input_hash="i", response_schema_version="1", serialization_version="1",
    )
    db.add(old)
    db.flush()
    attempt = SemanticJobAttempt(
        job_id=old.id, claim_token=uuid.uuid4(), retry_generation=0, started_at=NOW,
        reserve_usd=Decimal("0.01"), prefix_hash="p", privacy_dictionary_hash="d",
    )
    db.add(attempt)
    db.flush()
    db.execute(
        sa.text(
            "UPDATE semantic_worker_state SET claim_paused = true, paused_reason = 'manual', "
            "paused_at = :at, paused_attempt_id = :aid WHERE id = 1"
        ),
        {"at": NOW, "aid": attempt.id},
    )


def _answer(family_id: int, confidence="0.9", reason="проверочная причина") -> str:
    return (
        f'{{"family_id": {family_id}, "new_family_name": null, '
        f'"confidence": {confidence}, "reason": "{reason}"}}'
    )


def _response(content, *, cost=Decimal("0.01"), model="anthropic/claude-test", provider="P1"):
    return ModelResponse(
        content=content,
        actual_model=model,
        provider=provider,
        prompt_tokens=8274,
        completion_tokens=156,
        cache_write_tokens=8058,
        cached_tokens=0,
        cost_usd=cost,
    )


def _expected_reserve(rendered) -> Decimal:
    """Резерв при тарифах `S`, префикс — байты (наблюдений нет)."""
    return (
        Decimal(rendered.prefix_bytes) * Decimal("2.5")
        + Decimal(rendered.user_bytes) * Decimal("2")
        + Decimal(600) * Decimal("10")
    ) / Decimal(1_000_000)


def _old_published(db, context_id) -> FamilySuggestion:
    """Прежнее опубликованное предложение контекста — от другого задания."""
    old = SemanticJob(
        context_id=context_id, request_hash="old-hash", status="done", prompt_version="1",
        model_requested="m", place_dictionary_version=1, candidates_hash="c", prefix_hash="p",
        input_hash="i", response_schema_version="1", serialization_version="1",
    )
    db.add(old)
    db.flush()
    attempt = SemanticJobAttempt(
        job_id=old.id, claim_token=uuid.uuid4(), retry_generation=0, started_at=NOW,
        reserve_usd=Decimal("0.01"), prefix_hash="p", privacy_dictionary_hash="d",
    )
    db.add(attempt)
    db.flush()
    suggestion = FamilySuggestion(
        context_id=context_id, job_id=old.id, attempt_id=attempt.id, request_hash="old-hash",
        candidates_hash="c", candidates_snapshot=[], family_id=None, new_family_name="Старая",
        confidence=Decimal("0.5"), reason="старая", is_published=True,
    )
    db.add(suggestion)
    db.flush()
    return suggestion


def _claim_one(db, settings=S, now=NOW):
    claim = claim_next(db, settings=settings, now=now)
    assert claim is not None
    return claim


# ---------------------------------------------------------------------------
#  Захват: ветви спеки §2.5 по порядку
# ---------------------------------------------------------------------------

class TestClaimBranches:
    def test_paused_worker_claims_nothing_and_leaves_the_job_untouched(self, db_session, factories):
        scene = _scene(db_session, factories)
        _pause(db_session, scene.context_ids[0])

        assert claim_next(db_session, settings=S, now=NOW) is None

        job = _reload(db_session, scene.jobs[0].id)
        assert (job.status, job.attempts_in_generation, job.claim_token) == ("pending", 0, None)
        assert _attempts(db_session, job.id) == []

    def test_inapplicable_context_is_cancelled_and_the_next_job_is_claimed(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        # Контекст единицы без активных семей неприменим; его задание идёт первым.
        m3 = _unit_id(db_session, "M3")
        dead_context = _simple_context(
            db_session, factories, _proposal(factories), unit_id=m3, title="Без семей"
        )
        dead = _job(db_session, dead_context, unit_id=1)
        scene.jobs[0].unit_id = 2
        db_session.flush()

        claim = _claim_one(db_session)

        dead = _reload(db_session, dead.id)
        assert (dead.status, dead.cancel_reason) == ("cancelled", "not_applicable")
        assert _attempts(db_session, dead.id) == []
        assert claim.job_id == scene.jobs[0].id

    def test_chapter_cycle_context_is_cancelled_not_applicable_and_never_rendered(
        self, db_session, factories, monkeypatch
    ):
        user = factories.UserFactory.create()
        _active_family(db_session, title="Семья цикла", unit_name="M2", actor_id=user.id)
        proposal = _proposal(factories)
        cp = factories.CatalogPositionFactory.create(
            unit_id=_unit_id(db_session, "M2"), standard_job_title="Цикл разделов"
        )
        outer = factories.PositionItemFactory.create(
            proposal=proposal, is_chapter=True, job_title_in_proposal="Внешний",
            chapter_number_in_proposal="1",
        )
        inner = factories.PositionItemFactory.create(
            proposal=proposal, is_chapter=True, job_title_in_proposal="Внутренний",
            chapter_item_id=outer.id,
        )
        item = factories.PositionItemFactory.create(
            proposal=proposal, is_chapter=False, job_title_in_proposal="Цикл разделов",
            catalog_position_id=cp.id, chapter_item_id=inner.id,
        )
        context_id = route_position(db_session, position_item_id=item.id).context_id
        job = _job(db_session, context_id)
        outer.chapter_item_id = inner.id
        db_session.flush()
        db_session.expire_all()
        assert load_request_material(db_session, [context_id])[context_id].path_broken is True

        def _no_render(*args, **kwargs):
            raise AssertionError("рендер неприменимого контекста")

        monkeypatch.setattr(worker, "render_context_request", _no_render)

        assert claim_next(db_session, settings=S, now=NOW) is None

        job = _reload(db_session, job.id)
        assert (job.status, job.cancel_reason) == ("cancelled", "not_applicable")

    def test_changed_fingerprint_is_cancelled_input_changed_without_a_new_job(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        stale = _job(db_session, scene.context_ids[0], request_hash="устарело", unit_id=1)
        scene.jobs[0].status = "done"
        db_session.flush()

        assert claim_next(db_session, settings=S, now=NOW) is None

        stale = _reload(db_session, stale.id)
        assert (stale.status, stale.cancel_reason) == ("cancelled", "input_changed")
        assert _attempts(db_session, stale.id) == []
        count = db_session.execute(
            sa.select(sa.func.count()).select_from(SemanticJob).where(
                SemanticJob.context_id == scene.context_ids[0]
            )
        ).scalar_one()
        assert count == 2

    def test_privacy_match_puts_the_job_on_hold_with_the_set(self, db_session, factories):
        factories.ContractorFactory.create(title="ООО «Ромашка Строй»")
        scene = _scene(db_session, factories, titles=("Кладка Ромашка Строй стен",))

        assert claim_next(db_session, settings=S, now=NOW) is None

        job = _reload(db_session, scene.jobs[0].id)
        assert job.status == "privacy_hold"
        assert job.privacy_matches == [
            {"text": "ромашка строй", "kind": "contractor", "where": "context"}
        ]
        assert job.claim_token is None
        assert _attempts(db_session, job.id) == []

    def test_org_form_inside_the_name_in_the_text_still_puts_the_job_on_hold(
        self, db_session, factories
    ):
        factories.ContractorFactory.create(title="Ромашка ООО Сервис")
        scene = _scene(db_session, factories, titles=("Кладка Ромашка ООО Сервис стен",))

        assert claim_next(db_session, settings=S, now=NOW) is None

        job = _reload(db_session, scene.jobs[0].id)
        assert job.status == "privacy_hold"
        assert job.privacy_matches == [
            {"text": "ромашка сервис", "kind": "contractor", "where": "context"}
        ]

    def test_underscore_and_digit_name_written_with_spaces_still_puts_the_job_on_hold(
        self, db_session, factories
    ):
        factories.ContractorFactory.create(title="Ромашка_Сервис ЖК1")
        scene = _scene(db_session, factories, titles=("Кладка Ромашка Сервис ЖК 1 стен",))

        assert claim_next(db_session, settings=S, now=NOW) is None

        job = _reload(db_session, scene.jobs[0].id)
        assert job.status == "privacy_hold"
        assert job.privacy_matches == [
            {"text": "ромашка сервис жк 1", "kind": "contractor", "where": "context"}
        ]

    def test_punctuation_glued_to_a_form_in_the_title_still_puts_the_job_on_hold(
        self, db_session, factories
    ):
        factories.ContractorFactory.create(title="Ромашка, ТОО")
        scene = _scene(db_session, factories, titles=("Кладка Ромашка стен",))

        assert claim_next(db_session, settings=S, now=NOW) is None

        job = _reload(db_session, scene.jobs[0].id)
        assert job.status == "privacy_hold"
        assert job.privacy_matches == [
            {"text": "ромашка", "kind": "contractor", "where": "context"}
        ]

    def test_match_set_equal_to_the_released_set_is_claimed(self, db_session, factories):
        factories.ContractorFactory.create(title="ООО «Ромашка Строй»")
        scene = _scene(db_session, factories, titles=("Кладка Ромашка Строй стен",))
        scene.jobs[0].privacy_released_matches = [
            {"text": "ромашка строй", "kind": "contractor", "where": "context"}
        ]
        db_session.flush()

        claim = _claim_one(db_session)

        assert claim.job_id == scene.jobs[0].id
        assert _reload(db_session, claim.job_id).status == "running"

    def test_match_set_different_from_the_released_set_is_held(self, db_session, factories):
        factories.ContractorFactory.create(title="ООО «Ромашка Строй»")
        scene = _scene(db_session, factories, titles=("Кладка Ромашка Строй стен",))
        scene.jobs[0].privacy_released_matches = [
            {"text": "ромашка строй", "kind": "contractor", "where": "prompt"}
        ]
        db_session.flush()

        assert claim_next(db_session, settings=S, now=NOW) is None

        assert _reload(db_session, scene.jobs[0].id).status == "privacy_hold"

    def test_serialization_is_a_list_of_text_kind_where_in_finder_order(
        self, db_session, factories
    ):
        factories.ContractorFactory.create(title="ООО «Ромашка Строй»")
        factories.ObjectFactory.create(title="Школа Лотос")
        scene = _scene(db_session, factories, titles=("Школа Лотос и Ромашка Строй",))
        rendered = _rendered(db_session, scene.context_ids[0])
        from services.semantic_privacy import find_privacy_matches

        found = find_privacy_matches(build_privacy_dictionary(db_session), rendered)

        assert serialize_privacy_matches(found) == [
            {"text": "ромашка строй", "kind": "contractor", "where": "context"},
            {"text": "школа лотос", "kind": "object", "where": "context"},
        ]

    def test_privacy_dictionary_is_built_once_per_claim_call(
        self, db_session, factories, monkeypatch
    ):
        factories.ContractorFactory.create(title="ООО «Ромашка Строй»")
        scene = _scene(
            db_session, factories, titles=("Кладка Ромашка Строй", "Чистая работа")
        )
        scene.jobs[0].next_attempt_at = NOW - dt.timedelta(hours=2)
        db_session.flush()
        calls = []
        original = worker.build_privacy_dictionary

        def _counting(db):
            calls.append(1)
            return original(db)

        monkeypatch.setattr(worker, "build_privacy_dictionary", _counting)

        claim = _claim_one(db_session)

        assert claim.job_id == scene.jobs[1].id
        assert _reload(db_session, scene.jobs[0].id).status == "privacy_hold"
        assert len(calls) == 1

    def test_budget_exceeded_leaves_the_job_pending_without_attempt_or_counters(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        reserve = _expected_reserve(_rendered(db_session, scene.context_ids[0]))
        tight = S.model_copy(update={"SEMANTIC_DAILY_BUDGET_USD": reserve - Decimal("0.000001")})

        assert claim_next(db_session, settings=tight, now=NOW) is None

        job = _reload(db_session, scene.jobs[0].id)
        assert (job.status, job.attempts_in_generation, job.claim_token) == ("pending", 0, None)
        assert _attempts(db_session, job.id) == []

    def test_budget_exactly_equal_to_the_reserve_still_claims(self, db_session, factories):
        scene = _scene(db_session, factories)
        reserve = _expected_reserve(_rendered(db_session, scene.context_ids[0]))
        exact = S.model_copy(update={"SEMANTIC_DAILY_BUDGET_USD": reserve})

        assert claim_next(db_session, settings=exact, now=NOW) is not None

    def test_spent_in_the_last_day_counts_against_the_budget(self, db_session, factories):
        scene = _scene(db_session, factories, titles=("Первая", "Вторая"))
        rendered = _rendered(db_session, scene.context_ids[0])
        reserve = _expected_reserve(rendered)
        budget = S.model_copy(update={"SEMANTIC_DAILY_BUDGET_USD": reserve * Decimal("1.5")})

        first = claim_next(db_session, settings=budget, now=NOW)
        second = claim_next(db_session, settings=budget, now=NOW)

        assert first is not None
        assert second is None
        assert _reload(db_session, scene.jobs[1].id).status == "pending"

    def test_claim_makes_the_job_running_and_writes_the_attempt(self, db_session, factories):
        scene = _scene(db_session, factories)
        # Поколение не нулевое: попытка обязана нести поколение задания, а не
        # значение по умолчанию.
        scene.jobs[0].retry_generation = 2
        db_session.flush()
        rendered = _rendered(db_session, scene.context_ids[0])

        claim = _claim_one(db_session)

        job = _reload(db_session, scene.jobs[0].id)
        assert (job.status, job.attempts_in_generation) == ("running", 1)
        assert job.claim_token == claim.claim_token
        assert claim.job_id == job.id
        assert claim.rendered == rendered
        assert [c.id for c in claim.candidates] == [scene.family.id]
        (attempt,) = _attempts(db_session, job.id)
        assert attempt.id == claim.attempt_id
        assert attempt.claim_token == claim.claim_token
        assert attempt.retry_generation == 2
        assert attempt.started_at == NOW
        assert attempt.reserve_usd == _expected_reserve(rendered)
        assert attempt.prefix_hash == rendered.prefix_hash
        assert attempt.privacy_dictionary_hash == build_privacy_dictionary(db_session).digest
        assert attempt.finished_at is None and attempt.outcome is None
        assert attempt.reserve_exceeded is False

    def test_job_not_due_yet_is_not_claimed(self, db_session, factories):
        scene = _scene(db_session, factories)
        scene.jobs[0].next_attempt_at = NOW + dt.timedelta(seconds=1)
        db_session.flush()

        assert claim_next(db_session, settings=S, now=NOW) is None

    def test_job_due_exactly_now_is_claimed(self, db_session, factories):
        scene = _scene(db_session, factories)
        scene.jobs[0].next_attempt_at = NOW
        db_session.flush()

        assert claim_next(db_session, settings=S, now=NOW) is not None

    def test_running_job_is_not_claimed_twice(self, db_session, factories):
        _scene(db_session, factories)
        _claim_one(db_session)

        assert claim_next(db_session, settings=S, now=NOW) is None


class TestClaimOrder:
    def test_order_is_unit_then_next_attempt_then_id_with_null_unit_last(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, titles=("А", "Б", "В", "Г", "Д"))
        a, b, c, d, e = scene.jobs
        early = NOW - dt.timedelta(hours=3)
        late = NOW - dt.timedelta(hours=1)
        # Ожидаемый порядок: c (единица 1, раньше), затем b и d (единица 1,
        # позже), затем a (единица 2), затем e (единица NULL).
        a.unit_id, a.next_attempt_at = 2, early
        b.unit_id, b.next_attempt_at = 1, late
        c.unit_id, c.next_attempt_at = 1, early
        d.unit_id, d.next_attempt_at = 1, late
        e.unit_id, e.next_attempt_at = None, early
        db_session.flush()
        # b и d равны по unit и next_attempt_at: решает id. У b id меньше, но
        # его строка переписана последней (смена ключа — не HOT-обновление,
        # новая версия и новая запись индекса ложатся в конец): физический
        # порядок (d раньше b) расходится с порядком id, и без `id` в
        # сортировке ничья решилась бы в пользу d.
        b_id = -b.id
        db_session.execute(
            sa.text("UPDATE semantic_jobs SET id = :new WHERE id = :old"),
            {"new": b_id, "old": b.id},
        )
        db_session.expunge(b)
        expected = [c.id, b_id, d.id, a.id, e.id]

        got = [_claim_one(db_session).job_id for _ in range(5)]

        assert got == expected


class TestClaimStepOrder:
    """Шаги захвата идут строго по порядку спеки §2.5: у каждой пары соседних
    шагов свой вход, на котором перестановка меняет исход."""

    def test_paused_worker_does_not_cancel_even_an_inapplicable_job(self, db_session, factories):
        m3 = _unit_id(db_session, "M3")
        # Единица без активных семей: контекст неприменим, но захват остановлен
        # ещё до выбора задания.
        context_id = _simple_context(
            db_session, factories, _proposal(factories), unit_id=m3, title="Без семей"
        )
        job = _job(db_session, context_id)
        _pause(db_session, context_id)

        assert claim_next(db_session, settings=S, now=NOW) is None

        job = _reload(db_session, job.id)
        assert (job.status, job.cancel_reason) == ("pending", None)

    def test_changed_fingerprint_is_cancelled_before_the_privacy_check(
        self, db_session, factories
    ):
        factories.ContractorFactory.create(title="ООО «Ромашка Строй»")
        scene = _scene(db_session, factories, titles=("Кладка Ромашка Строй стен",))
        stale = _job(db_session, scene.context_ids[0], request_hash="устарело")
        scene.jobs[0].status = "done"
        db_session.flush()

        assert claim_next(db_session, settings=S, now=NOW) is None

        stale = _reload(db_session, stale.id)
        assert (stale.status, stale.cancel_reason, stale.privacy_matches) == (
            "cancelled", "input_changed", None,
        )

    def test_privacy_match_is_held_even_when_the_budget_is_exhausted(
        self, db_session, factories
    ):
        factories.ContractorFactory.create(title="ООО «Ромашка Строй»")
        scene = _scene(db_session, factories, titles=("Кладка Ромашка Строй стен",))
        no_budget = S.model_copy(update={"SEMANTIC_DAILY_BUDGET_USD": Decimal("0")})

        assert claim_next(db_session, settings=no_budget, now=NOW) is None

        assert _reload(db_session, scene.jobs[0].id).status == "privacy_hold"

    def test_changed_fingerprint_is_cancelled_even_when_the_budget_is_exhausted(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        stale = _job(db_session, scene.context_ids[0], request_hash="устарело")
        scene.jobs[0].status = "done"
        db_session.flush()
        no_budget = S.model_copy(update={"SEMANTIC_DAILY_BUDGET_USD": Decimal("0")})

        assert claim_next(db_session, settings=no_budget, now=NOW) is None

        stale = _reload(db_session, stale.id)
        assert (stale.status, stale.cancel_reason) == ("cancelled", "input_changed")

    def test_each_job_is_taken_at_most_once_per_claim(self, db_session, factories, monkeypatch):
        # Отменённое и задержанное задание обязаны уйти из выборки того же
        # захвата; повтор здесь — бесконечный цикл, поэтому помощник падает
        # на первом же повторе, а не ждёт таймаута.
        factories.ContractorFactory.create(title="ООО «Ромашка Строй»")
        scene = _scene(db_session, factories, titles=("Кладка Ромашка Строй", "Чистая работа"))
        m3 = _unit_id(db_session, "M3")
        dead_context = _simple_context(
            db_session, factories, _proposal(factories), unit_id=m3, title="Без семей"
        )
        _job(db_session, dead_context, unit_id=0)
        stale = _job(db_session, scene.context_ids[1], request_hash="устарело", unit_id=0)
        scene.jobs[0].unit_id = 1
        scene.jobs[1].unit_id = 2
        db_session.flush()
        seen: list[int] = []
        original = worker._next_pending_job

        def _once(db, *, now):
            job = original(db, now=now)
            if job is not None:
                assert job.id not in seen, f"задание {job.id} взято повторно"
                seen.append(job.id)
            return job

        monkeypatch.setattr(worker, "_next_pending_job", _once)

        claim = _claim_one(db_session)

        assert claim.job_id == scene.jobs[1].id
        assert _reload(db_session, stale.id).status == "cancelled"
        assert _reload(db_session, scene.jobs[0].id).status == "privacy_hold"
        assert len(seen) == 4


class TestLockedReadsRefreshHeldObjects:
    """Вызывающий передаёт свою сессию, и строка может уже лежать в ней
    загруженной: блокирующее чтение обязано вернуть состояние базы, а не
    удержанный в сессии объект (`populate_existing`)."""

    def test_claim_sees_a_pause_written_after_the_state_row_was_loaded(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        held = db_session.get(SemanticWorkerState, 1)
        assert held.claim_paused is False
        _pause(db_session, scene.context_ids[0])

        assert claim_next(db_session, settings=S, now=NOW) is None

    def test_claim_increments_the_counter_the_base_holds_not_the_held_one(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        held = scene.jobs[0]
        assert held.attempts_in_generation == 0
        db_session.execute(
            sa.text("UPDATE semantic_jobs SET attempts_in_generation = 2 WHERE id = :id"),
            {"id": held.id},
        )

        _claim_one(db_session)

        assert _reload(db_session, held.id).attempts_in_generation == 3

    def test_record_result_sees_a_takeover_written_after_the_job_was_loaded(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        claim = _claim_one(db_session)
        held = db_session.get(SemanticJob, claim.job_id)
        assert held.claim_token == claim.claim_token
        db_session.execute(
            sa.text("UPDATE semantic_jobs SET claim_token = :t WHERE id = :id"),
            {"t": uuid.uuid4(), "id": claim.job_id},
        )

        record_result(db_session, claim, _response(_answer(scene.family.id)), now=NOW, settings=S)

        assert _attempts(db_session, claim.job_id)[0].outcome == "lost_claim"
        assert _reload(db_session, claim.job_id).status == "running"

    def test_failure_does_not_rewrite_an_attempt_closed_after_it_was_loaded(
        self, db_session, factories
    ):
        _scene(db_session, factories)
        claim = _claim_one(db_session)
        held = db_session.get(SemanticJobAttempt, claim.attempt_id)
        assert held.finished_at is None
        db_session.execute(
            sa.text(
                "UPDATE semantic_job_attempts SET finished_at = :at, outcome = 'ok', "
                "cost_usd = 0.005 WHERE id = :id"
            ),
            {"at": NOW, "id": claim.attempt_id},
        )
        db_session.execute(
            sa.text("UPDATE semantic_jobs SET status = 'done', claim_token = NULL WHERE id = :id"),
            {"id": claim.job_id},
        )

        _fail(db_session, claim, TransientModelError("подтверждение потеряно"))

        (attempt,) = _attempts(db_session, claim.job_id)
        assert (attempt.outcome, attempt.error_class) == ("ok", None)


def _lock_waiters(db) -> int:
    return db.execute(
        sa.text(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE wait_event_type = 'Lock' AND datname = current_database()"
        )
    ).scalar_one()


class TestClaimSkipsLockedJobs:
    def test_a_job_locked_by_another_transaction_is_skipped_not_waited_for(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _committed_scene(
            committing_db, committing_factories, titles=("Первая работа", "Вторая работа")
        )
        holder = committing_session_factory()
        claimer = committing_session_factory()
        try:
            holder.execute(
                sa.text("SELECT id FROM semantic_jobs WHERE id = :id FOR UPDATE"),
                {"id": scene.jobs[0].id},
            )
            # Ожидание замка превратилось бы в ошибку, а не в зависший прогон.
            claimer.execute(sa.text("SET LOCAL lock_timeout = '3s'"))

            claim = claim_next(claimer, settings=S, now=NOW)
        finally:
            holder.rollback()
            holder.close()
            claimer.rollback()
            claimer.close()

        assert claim is not None and claim.job_id == scene.jobs[1].id


# ---------------------------------------------------------------------------
#  Гонка бюджета
# ---------------------------------------------------------------------------

def _race(
    committing_session_factory, committing_db, committing_factories, monkeypatch, *,
    lock, barrier_timeout,
):
    user = committing_factories.UserFactory.create()
    unit_id = _unit_id(committing_db, "M2")
    _active_family(committing_db, title="Семья гонки", unit_name="M2", actor_id=user.id)
    proposal = _proposal(committing_factories)
    # Названия одной длины в байтах: резерв всех четырёх заданий одинаков.
    for title in ("Гонка 1", "Гонка 2", "Гонка 3", "Гонка 4"):
        context_id = _simple_context(
            committing_db, committing_factories, proposal, unit_id=unit_id, title=title
        )
        _job(committing_db, context_id, unit_id=unit_id)
    committing_db.commit()
    first = committing_db.execute(sa.select(SemanticJob.context_id).order_by(SemanticJob.id)).first()
    reserve = _expected_reserve(_rendered(committing_db, first[0]))
    budget_settings = S.model_copy(
        update={"SEMANTIC_DAILY_BUDGET_USD": reserve * Decimal("1.5")}
    )

    if not lock:
        def _unlocked(db):
            return db.execute(
                sa.select(SemanticWorkerState)
                .where(SemanticWorkerState.id == 1)
                .execution_options(populate_existing=True)
            ).scalar_one()

        monkeypatch.setattr(worker, "_lock_worker_state", _unlocked)

    barrier = threading.Barrier(4)
    # «Дошёл» и «прошёл вместе» — разные вещи: барьер, сломавшийся по таймауту,
    # отпускает потоки поодиночке, и проверка бюджета перестаёт быть одновременной.
    reached: list[str] = []
    passed_together: list[str] = []
    claims: dict[str, object] = {}
    errors: dict[str, BaseException] = {}
    names = {f"race-{i}" for i in range(4)}

    # Барьер стоит ПОСЛЕ чтения расхода, а не перед ним: иначе поток, отпущенный
    # барьером, но запоздавший с запросом, прочитал бы уже закоммиченную чужую
    # попытку, и проверки бюджета снова перестали бы быть одновременными.
    def _hold_after_spent_read(conn, cursor, statement, parameters, context, executemany):
        text = statement.lower()
        if (
            threading.current_thread().name in names
            and "sum(" in text
            and "semantic_job_attempts" in text
        ):
            reached.append(threading.current_thread().name)
            with contextlib.suppress(threading.BrokenBarrierError):
                barrier.wait(timeout=barrier_timeout)
                passed_together.append(threading.current_thread().name)

    engine = committing_db.get_bind()
    sa.event.listen(engine, "after_cursor_execute", _hold_after_spent_read)

    def run() -> None:
        db = committing_session_factory()
        try:
            claims[threading.current_thread().name] = claim_next(
                db, settings=budget_settings, now=NOW
            )
        except BaseException as exc:  # noqa: BLE001 — поток обязан не потерять исключение
            db.rollback()
            errors[threading.current_thread().name] = exc
        finally:
            db.close()

    threads = [threading.Thread(target=run, name=f"race-{i}") for i in range(4)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=_JOIN_TIMEOUT)
    finally:
        sa.event.remove(engine, "after_cursor_execute", _hold_after_spent_read)

    assert not any(t.is_alive() for t in threads), "поток гонки завис за отведённый таймаут"
    assert not errors, errors
    won = [c for c in claims.values() if c is not None]
    attempts = committing_db.execute(
        sa.select(sa.func.count()).select_from(SemanticJobAttempt)
    ).scalar_one()
    return len(won), attempts, len(reached), len(passed_together)


class TestBudgetRace:
    def test_four_parallel_claims_with_room_for_one_attempt_make_exactly_one(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch
    ):
        # Первый поток держит замок состояния у барьера, остальные ждут замок, так что
        # барьер из четырёх сломается по короткому таймауту — это цена теста.
        won, attempts, reached, passed = _race(
            committing_session_factory, committing_db, committing_factories, monkeypatch,
            lock=True, barrier_timeout=3,
        )

        # Без `reached` равенство `passed == 0` держалось бы и при хуке, который
        # ни разу не сработал.
        assert reached == 4
        assert passed == 0, "барьер не должен был пропустить потоки вместе: замок их разводит"
        assert (won, attempts) == (1, 1)

    def test_without_the_state_row_lock_all_four_pass_the_budget_check(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch
    ):
        # Без замка все четыре обязаны встретиться у барьера; таймаут с запасом на
        # просевший раннер, но меньше `_JOIN_TIMEOUT`.
        won, attempts, reached, passed = _race(
            committing_session_factory, committing_db, committing_factories, monkeypatch,
            lock=False, barrier_timeout=20,
        )

        assert reached == 4
        assert passed == 4, (
            "барьер сломался до прихода всех четырёх потоков: проверки бюджета "
            f"не были одновременными (прошли вместе {passed} из 4)"
        )
        assert (won, attempts) == (4, 4)


# ---------------------------------------------------------------------------
#  record_result
# ---------------------------------------------------------------------------

class TestRecordResultPublication:
    def test_valid_answer_on_the_current_fingerprint_is_published(self, db_session, factories):
        scene = _scene(db_session, factories)
        claim = _claim_one(db_session)

        record_result(
            db_session, claim, _response(_answer(scene.family.id, "0.75")), now=NOW, settings=S
        )

        job = _reload(db_session, claim.job_id)
        (suggestion,) = _suggestions(db_session, scene.context_ids[0])
        assert suggestion.is_published is True and suggestion.unpublished_reason is None
        assert (job.status, job.claim_token, job.result_suggestion_id) == (
            "done", None, suggestion.id,
        )
        assert suggestion.family_id == scene.family.id
        assert suggestion.new_family_name is None
        assert suggestion.confidence == Decimal("0.75")
        assert suggestion.reason == "проверочная причина"
        assert suggestion.job_id == job.id and suggestion.attempt_id == claim.attempt_id
        assert suggestion.request_hash == claim.rendered.request_hash
        assert suggestion.candidates_hash == claim.rendered.candidates_hash
        assert suggestion.candidates_snapshot == [
            {
                "id": scene.family.id,
                "title": "Семья пола",
                "unit_code": "M2",
                "definition": "Определение семьи",
            }
        ]

    def test_new_family_answer_stores_the_name_and_no_family(self, db_session, factories):
        scene = _scene(db_session, factories)
        claim = _claim_one(db_session)
        content = (
            '{"family_id": 0, "new_family_name": "Новая семья", "confidence": 0.4, '
            '"reason": "нет подходящей"}'
        )

        record_result(db_session, claim, _response(content), now=NOW, settings=S)

        (suggestion,) = _suggestions(db_session, scene.context_ids[0])
        assert (suggestion.family_id, suggestion.new_family_name) == (None, "Новая семья")
        assert suggestion.is_published is True

    def test_prior_published_suggestion_of_the_context_is_unpublished(self, db_session, factories):
        scene = _scene(db_session, factories)
        old = _old_published(db_session, scene.context_ids[0])
        claim = _claim_one(db_session)

        record_result(db_session, claim, _response(_answer(scene.family.id)), now=NOW, settings=S)

        db_session.expire_all()
        old = db_session.get(FamilySuggestion, old.id)
        assert (old.is_published, old.unpublished_reason) == (False, "stale_fingerprint")
        published = [s for s in _suggestions(db_session, scene.context_ids[0]) if s.is_published]
        assert len(published) == 1 and published[0].id != old.id

    def test_published_suggestion_of_another_context_is_left_alone(self, db_session, factories):
        scene = _scene(db_session, factories, titles=("Первая", "Вторая"))
        other = _old_published(db_session, scene.context_ids[1])
        claim = _claim_one(db_session)
        assert claim.job_id == scene.jobs[0].id

        record_result(db_session, claim, _response(_answer(scene.family.id)), now=NOW, settings=S)

        db_session.expire_all()
        other = db_session.get(FamilySuggestion, other.id)
        assert (other.is_published, other.unpublished_reason) == (True, None)

    def test_actual_cost_replaces_the_reserve_in_the_daily_total(self, db_session, factories):
        scene = _scene(db_session, factories)
        claim = _claim_one(db_session)
        reserve = _attempts(db_session, claim.job_id)[0].reserve_usd
        assert spent_last_24h(db_session, now=NOW) == reserve

        record_result(
            db_session, claim,
            _response(_answer(scene.family.id), cost=Decimal("0.005")), now=NOW, settings=S,
        )

        assert spent_last_24h(db_session, now=NOW) == Decimal("0.005")

    def test_attempt_is_closed_ok_with_the_response_data(self, db_session, factories):
        scene = _scene(db_session, factories)
        claim = _claim_one(db_session)
        content = _answer(scene.family.id)

        record_result(
            db_session, claim, _response(content, cost=Decimal("0.022137")), now=NOW, settings=S
        )

        (attempt,) = _attempts(db_session, claim.job_id)
        assert attempt.outcome == "ok"
        assert attempt.finished_at == NOW
        assert attempt.raw_response == content
        assert (attempt.actual_model, attempt.provider) == ("anthropic/claude-test", "P1")
        assert (
            attempt.prompt_tokens, attempt.completion_tokens,
            attempt.cache_write_tokens, attempt.cached_tokens,
        ) == (8274, 156, 8058, 0)
        assert attempt.cost_usd == Decimal("0.022137")
        assert attempt.validation_error is None

    def test_answer_on_a_changed_fingerprint_is_stored_unpublished_and_evicts_nothing(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        old = _old_published(db_session, scene.context_ids[0])
        claim = _claim_one(db_session)
        db_session.execute(
            sa.update(CatalogPosition).values(standard_job_title="Переименованная работа")
        )
        db_session.expire_all()

        record_result(db_session, claim, _response(_answer(scene.family.id)), now=NOW, settings=S)

        job = _reload(db_session, claim.job_id)
        new = [s for s in _suggestions(db_session, scene.context_ids[0]) if s.id != old.id]
        assert len(new) == 1
        assert (new[0].is_published, new[0].unpublished_reason) == (False, "stale_fingerprint")
        db_session.expire_all()
        assert db_session.get(FamilySuggestion, old.id).is_published is True
        assert (job.status, job.result_suggestion_id) == ("done", new[0].id)

    def test_answer_for_a_context_that_stopped_being_applicable_is_not_published(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        old = _old_published(db_session, scene.context_ids[0])
        claim = _claim_one(db_session)
        db_session.execute(sa.update(CatalogContext).values(archived_at=NOW))
        db_session.expire_all()

        record_result(db_session, claim, _response(_answer(scene.family.id)), now=NOW, settings=S)

        new = [s for s in _suggestions(db_session, scene.context_ids[0]) if s.id != old.id]
        assert len(new) == 1
        assert (new[0].is_published, new[0].unpublished_reason) == (
            False, "context_not_applicable",
        )
        db_session.expire_all()
        assert db_session.get(FamilySuggestion, old.id).is_published is True
        assert _reload(db_session, claim.job_id).status == "done"


class TestRecordResultLostClaim:
    def _take_over(self, db, job_id):
        db.execute(
            sa.text("UPDATE semantic_jobs SET claim_token = :t WHERE id = :id"),
            {"t": uuid.uuid4(), "id": job_id},
        )

    def test_changed_claim_token_writes_lost_claim_and_leaves_the_job(self, db_session, factories):
        scene = _scene(db_session, factories)
        claim = _claim_one(db_session)
        self._take_over(db_session, claim.job_id)

        record_result(
            db_session, claim,
            _response(_answer(scene.family.id), cost=Decimal("0.004")), now=NOW, settings=S,
        )

        job = _reload(db_session, claim.job_id)
        assert job.status == "running" and job.claim_token != claim.claim_token
        assert job.result_suggestion_id is None
        (attempt,) = _attempts(db_session, claim.job_id)
        assert attempt.outcome == "lost_claim"
        assert attempt.finished_at == NOW
        assert attempt.cost_usd == Decimal("0.004")
        assert (attempt.prompt_tokens, attempt.completion_tokens) == (8274, 156)
        (suggestion,) = _suggestions(db_session, scene.context_ids[0])
        assert (suggestion.is_published, suggestion.unpublished_reason) == (False, "lost_claim")

    def test_job_no_longer_running_is_a_lost_claim_too(self, db_session, factories):
        scene = _scene(db_session, factories)
        claim = _claim_one(db_session)
        db_session.execute(
            sa.text(
                "UPDATE semantic_jobs SET status = 'cancelled', cancel_reason = 'input_changed', "
                "claim_token = NULL WHERE id = :id"
            ),
            {"id": claim.job_id},
        )

        record_result(db_session, claim, _response(_answer(scene.family.id)), now=NOW, settings=S)

        job = _reload(db_session, claim.job_id)
        assert (job.status, job.cancel_reason) == ("cancelled", "input_changed")
        assert _attempts(db_session, claim.job_id)[0].outcome == "lost_claim"
        (suggestion,) = _suggestions(db_session, scene.context_ids[0])
        assert suggestion.unpublished_reason == "lost_claim"

    def test_lost_claim_does_not_evict_the_published_suggestion(self, db_session, factories):
        scene = _scene(db_session, factories)
        old = _old_published(db_session, scene.context_ids[0])
        claim = _claim_one(db_session)
        self._take_over(db_session, claim.job_id)

        record_result(db_session, claim, _response(_answer(scene.family.id)), now=NOW, settings=S)

        db_session.expire_all()
        assert db_session.get(FamilySuggestion, old.id).is_published is True

    def test_lost_claim_with_an_invalid_answer_writes_no_suggestion(self, db_session, factories):
        scene = _scene(db_session, factories)
        claim = _claim_one(db_session)
        self._take_over(db_session, claim.job_id)

        record_result(db_session, claim, _response("не json"), now=NOW, settings=S)

        assert _attempts(db_session, claim.job_id)[0].outcome == "lost_claim"
        assert _suggestions(db_session, scene.context_ids[0]) == []
        assert _reload(db_session, claim.job_id).status == "running"


class TestRecordResultSchemaError:
    def _bad(self, db_session, factories, content):
        scene = _scene(db_session, factories)
        claim = _claim_one(db_session)
        record_result(db_session, claim, _response(content), now=NOW, settings=S)
        return scene, claim

    def test_text_that_is_not_json_writes_a_schema_error_attempt(self, db_session, factories):
        scene, claim = self._bad(db_session, factories, "Это не JSON")

        (attempt,) = _attempts(db_session, claim.job_id)
        assert attempt.outcome == "schema_error"
        assert attempt.raw_response == "Это не JSON"
        assert attempt.validation_error == (
            "ответ не разбирается как JSON: Expecting value: line 1 column 1 (char 0)"
        )
        assert (attempt.actual_model, attempt.provider) == ("anthropic/claude-test", "P1")
        assert (attempt.prompt_tokens, attempt.completion_tokens) == (8274, 156)
        assert attempt.cost_usd == Decimal("0.01")
        assert attempt.finished_at == NOW

    def test_unknown_family_writes_the_schema_detail(self, db_session, factories):
        scene, claim = self._bad(db_session, factories, _answer(987654))

        (attempt,) = _attempts(db_session, claim.job_id)
        assert attempt.outcome == "schema_error"
        assert attempt.validation_error == (
            "family_id 987654 не входит в переданный список кандидатов"
        )

    def test_job_goes_to_error_without_retry_and_no_suggestion(self, db_session, factories):
        scene, claim = self._bad(db_session, factories, "Это не JSON")

        job = _reload(db_session, claim.job_id)
        assert (job.status, job.claim_token, job.last_error_class) == (
            "error", None, "schema_error",
        )
        assert job.attempts_in_generation == 1
        assert _suggestions(db_session, scene.context_ids[0]) == []
        assert claim_next(db_session, settings=S, now=NOW + dt.timedelta(days=1)) is None


class TestFuse:
    def _paused(self, db):
        state = _state(db)
        return (state.claim_paused, state.paused_reason, state.paused_attempt_id, state.paused_at)

    def test_cost_above_the_reserve_marks_the_attempt_and_stops_the_worker(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories, titles=("Первая", "Вторая"))
        claim = _claim_one(db_session)
        # Время остановки — момент записи, а не начала попытки.
        later = NOW + dt.timedelta(minutes=1)

        record_result(
            db_session, claim,
            _response(_answer(scene.family.id), cost=Decimal("1")), now=later, settings=S,
        )

        (attempt,) = _attempts(db_session, claim.job_id)
        assert attempt.reserve_exceeded is True
        assert self._paused(db_session) == (
            True, "reserve_exceeded", claim.attempt_id, later,
        )
        assert claim_next(db_session, settings=S, now=later) is None
        assert _reload(db_session, scene.jobs[1].id).status == "pending"

    def test_cost_equal_to_the_reserve_does_not_trip_the_fuse(self, db_session, factories):
        scene = _scene(db_session, factories)
        claim = _claim_one(db_session)
        reserve = _attempts(db_session, claim.job_id)[0].reserve_usd

        record_result(
            db_session, claim, _response(_answer(scene.family.id), cost=reserve), now=NOW, settings=S
        )

        assert _attempts(db_session, claim.job_id)[0].reserve_exceeded is False
        assert self._paused(db_session)[0] is False

    def test_unknown_cost_is_not_compared_and_the_attempt_keeps_its_reserve(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        claim = _claim_one(db_session)
        reserve = _attempts(db_session, claim.job_id)[0].reserve_usd

        record_result(
            db_session, claim, _response(_answer(scene.family.id), cost=None), now=NOW, settings=S
        )

        (attempt,) = _attempts(db_session, claim.job_id)
        assert attempt.cost_usd is None and attempt.reserve_exceeded is False
        assert self._paused(db_session)[0] is False
        assert spent_last_24h(db_session, now=NOW) == reserve

    def test_schema_error_attempt_with_cost_above_the_reserve_trips_the_fuse(
        self, db_session, factories
    ):
        _scene(db_session, factories)
        claim = _claim_one(db_session)

        record_result(db_session, claim, _response("не json", cost=Decimal("1")), now=NOW, settings=S)

        assert _attempts(db_session, claim.job_id)[0].reserve_exceeded is True
        assert self._paused(db_session)[:3] == (True, "reserve_exceeded", claim.attempt_id)

    def test_lost_claim_attempt_with_cost_above_the_reserve_trips_the_fuse(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        claim = _claim_one(db_session)
        db_session.execute(
            sa.text("UPDATE semantic_jobs SET claim_token = :t WHERE id = :id"),
            {"t": uuid.uuid4(), "id": claim.job_id},
        )

        record_result(
            db_session, claim,
            _response(_answer(scene.family.id), cost=Decimal("1")), now=NOW, settings=S,
        )

        assert _attempts(db_session, claim.job_id)[0].reserve_exceeded is True
        assert self._paused(db_session)[:3] == (True, "reserve_exceeded", claim.attempt_id)

    def test_a_second_overrun_keeps_the_first_stop(self, db_session, factories):
        scene = _scene(db_session, factories, titles=("Первая", "Вторая"))
        first = _claim_one(db_session)
        second = _claim_one(db_session)
        later = NOW + dt.timedelta(minutes=5)

        record_result(
            db_session, first,
            _response(_answer(scene.family.id), cost=Decimal("1")), now=NOW, settings=S,
        )
        record_result(
            db_session, second,
            _response(_answer(scene.family.id), cost=Decimal("2")), now=later, settings=S,
        )

        assert _attempts(db_session, second.job_id)[0].reserve_exceeded is True
        assert self._paused(db_session) == (True, "reserve_exceeded", first.attempt_id, NOW)


# ---------------------------------------------------------------------------
#  record_failure
# ---------------------------------------------------------------------------

def _fail(db, claim, error, *, now=NOW, rng=lambda: 0.0):
    record_failure(db, claim, error, now=now, settings=S, rng=rng)


class TestRecordFailure:
    def test_transient_error_returns_the_job_to_pending_with_a_delay(self, db_session, factories):
        _scene(db_session, factories)
        claim = _claim_one(db_session)

        _fail(db_session, claim, TransientModelError("HTTP 503", error_class="http_503"))

        job = _reload(db_session, claim.job_id)
        assert (job.status, job.claim_token, job.last_error_class) == ("pending", None, "http_503")
        assert job.next_attempt_at == NOW + dt.timedelta(seconds=30)
        assert job.attempts_in_generation == 1
        (attempt,) = _attempts(db_session, claim.job_id)
        assert attempt.outcome == "transient_error"
        assert (attempt.error_class, attempt.error_text) == ("http_503", "HTTP 503")
        assert attempt.finished_at == NOW
        assert attempt.cost_usd is None

    def test_jitter_is_added_within_half_of_the_base(self, db_session, factories):
        _scene(db_session, factories)
        claim = _claim_one(db_session)

        _fail(db_session, claim, TransientModelError("x"), rng=lambda: 0.5)

        assert _reload(db_session, claim.job_id).next_attempt_at == NOW + dt.timedelta(seconds=37.5)

    def test_delay_grows_strictly_from_attempt_to_attempt_at_any_jitter(
        self, db_session, factories
    ):
        _scene(db_session, factories)
        delays = []
        now = NOW
        # Самый неудачный разброс: максимальный у ранней попытки, нулевой у поздней.
        for rng in (lambda: 0.999999, lambda: 0.0):
            claim = _claim_one(db_session, now=now)
            _fail(db_session, claim, TransientModelError("x"), now=now, rng=rng)
            due = _reload(db_session, claim.job_id).next_attempt_at
            delays.append(due - now)
            now = due

        assert delays[0] < delays[1]
        assert delays[1] == dt.timedelta(seconds=60)

    def test_attempts_exhausted_in_the_generation_end_in_error(self, db_session, factories):
        _scene(db_session, factories)
        now = NOW
        statuses = []
        for _ in range(3):
            claim = _claim_one(db_session, now=now)
            _fail(db_session, claim, TransientModelError("x"), now=now)
            job = _reload(db_session, claim.job_id)
            statuses.append(job.status)
            now = job.next_attempt_at

        assert statuses == ["pending", "pending", "error"]
        assert job.claim_token is None and job.attempts_in_generation == 3

    def test_permanent_error_ends_in_error_at_once(self, db_session, factories):
        _scene(db_session, factories)
        claim = _claim_one(db_session)

        _fail(db_session, claim, PermanentModelError("HTTP 401", error_class="http_401"))

        job = _reload(db_session, claim.job_id)
        assert (job.status, job.claim_token, job.last_error_class) == ("error", None, "http_401")
        assert _attempts(db_session, claim.job_id)[0].outcome == "permanent_error"

    def test_attempt_without_cost_stays_with_its_reserve(self, db_session, factories):
        _scene(db_session, factories)
        claim = _claim_one(db_session)
        reserve = _attempts(db_session, claim.job_id)[0].reserve_usd

        _fail(db_session, claim, TransientModelError("таймаут"))

        assert spent_last_24h(db_session, now=NOW) == reserve

    def test_error_class_defaults_to_the_type_name(self, db_session, factories):
        _scene(db_session, factories)
        claim = _claim_one(db_session)

        _fail(db_session, claim, TransientModelError("без метки"))

        assert _attempts(db_session, claim.job_id)[0].error_class == "TransientModelError"

    def test_failure_after_a_committed_result_does_not_rewrite_the_closed_attempt(
        self, db_session, factories
    ):
        scene = _scene(db_session, factories)
        claim = _claim_one(db_session)
        record_result(
            db_session, claim,
            _response(_answer(scene.family.id), cost=Decimal("0.005")), now=NOW, settings=S,
        )

        _fail(db_session, claim, TransientModelError("подтверждение потеряно"))

        (attempt,) = _attempts(db_session, claim.job_id)
        assert attempt.outcome == "ok"
        assert attempt.cost_usd == Decimal("0.005")
        assert attempt.error_class is None and attempt.error_text is None
        job = _reload(db_session, claim.job_id)
        assert (job.status, job.claim_token) == ("done", None)

    def test_lost_claim_closes_the_attempt_and_leaves_the_job(self, db_session, factories):
        _scene(db_session, factories)
        claim = _claim_one(db_session)
        db_session.execute(
            sa.text("UPDATE semantic_jobs SET claim_token = :t WHERE id = :id"),
            {"t": uuid.uuid4(), "id": claim.job_id},
        )

        _fail(db_session, claim, TransientModelError("x"))

        job = _reload(db_session, claim.job_id)
        assert job.status == "running" and job.claim_token != claim.claim_token
        assert _attempts(db_session, claim.job_id)[0].outcome == "lost_claim"


# ---------------------------------------------------------------------------
#  process_one (настоящие коммиты)
# ---------------------------------------------------------------------------

class _FakeClient:
    """Клиент модели: сценарий по очереди — ответ или исключение."""

    def __init__(self, *script, probe=None):
        self.script = list(script)
        self.calls: list[tuple[dict, float]] = []
        self.probe = probe

    def complete(self, body, *, timeout_s):
        self.calls.append((body, timeout_s))
        if self.probe is not None:
            self.probe()
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _committed_scene(committing_db, committing_factories, **kwargs):
    scene = _scene(committing_db, committing_factories, **kwargs)
    committing_db.commit()
    return scene


def _clock():
    return NOW


class TestProcessOne:
    def test_no_job_returns_false_and_does_not_call_the_model(
        self, committing_session_factory, committing_db
    ):
        client = _FakeClient()

        assert process_one(committing_session_factory, client, settings=S, clock=_clock) is False
        assert client.calls == []

    def test_success_calls_the_model_with_the_body_and_timeout_and_records_the_result(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _committed_scene(committing_db, committing_factories)
        body = _rendered(committing_db, scene.context_ids[0]).body
        client = _FakeClient(_response(_answer(scene.family.id)))

        assert process_one(committing_session_factory, client, settings=S, clock=_clock) is True

        assert client.calls == [(body, 120)]
        committing_db.expire_all()
        assert committing_db.get(SemanticJob, scene.jobs[0].id).status == "done"
        (suggestion,) = _suggestions(committing_db, scene.context_ids[0])
        assert suggestion.is_published is True

    def test_no_transaction_is_open_during_the_model_call(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _committed_scene(committing_db, committing_factories)
        job_id = scene.jobs[0].id
        observed: dict[str, object] = {}

        def probe():
            other = committing_session_factory()
            try:
                observed["status"] = other.execute(
                    sa.text("SELECT status FROM semantic_jobs WHERE id = :id"), {"id": job_id}
                ).scalar_one()
                # Замки строк задания и состояния свободны: NOWAIT упал бы, будь
                # транзакция захвата или записи открыта. Отказ пишется в
                # наблюдение, а не бросается: исключение из клиента исполнитель
                # записал бы неудачей и ждал бы тот же замок — прогон повис бы.
                try:
                    other.execute(
                        sa.text("SELECT id FROM semantic_jobs WHERE id = :id FOR UPDATE NOWAIT"),
                        {"id": job_id},
                    )
                    other.execute(
                        sa.text(
                            "SELECT id FROM semantic_worker_state WHERE id = 1 FOR UPDATE NOWAIT"
                        )
                    )
                    observed["free"] = True
                except sa.exc.OperationalError:
                    observed["free"] = False
            finally:
                other.rollback()
                other.close()

        client = _FakeClient(_response(_answer(scene.family.id)), probe=probe)

        process_one(committing_session_factory, client, settings=S, clock=_clock)

        assert observed == {"status": "running", "free": True}

    def test_transient_error_returns_the_job_to_pending(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _committed_scene(committing_db, committing_factories)
        client = _FakeClient(TransientModelError("HTTP 503", error_class="http_503"))

        assert process_one(committing_session_factory, client, settings=S, clock=_clock) is True

        committing_db.expire_all()
        job = committing_db.get(SemanticJob, scene.jobs[0].id)
        assert (job.status, job.last_error_class) == ("pending", "http_503")

    def test_permanent_error_puts_the_job_into_error(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _committed_scene(committing_db, committing_factories)
        client = _FakeClient(PermanentModelError("HTTP 400", error_class="http_400"))

        assert process_one(committing_session_factory, client, settings=S, clock=_clock) is True

        committing_db.expire_all()
        assert committing_db.get(SemanticJob, scene.jobs[0].id).status == "error"

    def test_any_other_client_exception_is_transient_with_the_type_name(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _committed_scene(committing_db, committing_factories)
        client = _FakeClient(RuntimeError("что-то сломалось"))

        assert process_one(committing_session_factory, client, settings=S, clock=_clock) is True

        committing_db.expire_all()
        job = committing_db.get(SemanticJob, scene.jobs[0].id)
        assert (job.status, job.last_error_class) == ("pending", "RuntimeError")
        (attempt,) = _attempts(committing_db, job.id)
        assert (attempt.outcome, attempt.error_class) == ("transient_error", "RuntimeError")

    def test_exception_while_recording_the_result_closes_the_attempt_as_transient(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch
    ):
        scene = _committed_scene(committing_db, committing_factories)
        client = _FakeClient(_response(_answer(scene.family.id)))

        def _broken(*args, **kwargs):
            raise ValueError("запись упала")

        monkeypatch.setattr(worker, "record_result", _broken)

        assert process_one(committing_session_factory, client, settings=S, clock=_clock) is True

        committing_db.expire_all()
        job = committing_db.get(SemanticJob, scene.jobs[0].id)
        assert (job.status, job.claim_token, job.last_error_class) == ("pending", None, "ValueError")
        (attempt,) = _attempts(committing_db, job.id)
        assert (attempt.outcome, attempt.error_class) == ("transient_error", "ValueError")

    def test_a_failing_job_does_not_stop_the_next_one(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _committed_scene(
            committing_db, committing_factories, titles=("Первая работа", "Вторая работа")
        )
        client = _FakeClient(RuntimeError("сбой"), _response(_answer(scene.family.id)))

        first = process_one(committing_session_factory, client, settings=S, clock=_clock)
        second = process_one(committing_session_factory, client, settings=S, clock=_clock)

        assert (first, second) == (True, True)
        committing_db.expire_all()
        statuses = [committing_db.get(SemanticJob, j.id).status for j in scene.jobs]
        assert sorted(statuses) == ["done", "pending"]

    def test_an_error_after_the_claim_never_escapes_even_when_closing_the_attempt_fails(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch
    ):
        _committed_scene(committing_db, committing_factories)
        client = _FakeClient(RuntimeError("сбой"))

        def _broken(*args, **kwargs):
            raise ValueError("и закрыть не вышло")

        monkeypatch.setattr(worker, "record_failure", _broken)

        assert process_one(committing_session_factory, client, settings=S, clock=_clock) is True


# ---------------------------------------------------------------------------
#  Условная запись и предохранитель под параллельными транзакциями
# ---------------------------------------------------------------------------

class TestConcurrentWrites:
    def test_takeover_committed_while_the_result_waits_is_a_lost_claim(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _committed_scene(committing_db, committing_factories)
        claim = claim_next(committing_db, settings=S, now=NOW)
        assert claim is not None
        response = _response(_answer(scene.family.id))
        new_token = uuid.uuid4()
        taker = committing_session_factory()
        taker.execute(
            sa.text("UPDATE semantic_jobs SET claim_token = :t WHERE id = :id"),
            {"t": new_token, "id": claim.job_id},
        )
        errors: list[BaseException] = []

        def write() -> None:
            db = committing_session_factory()
            try:
                record_result(db, claim, response, now=NOW, settings=S)
            except BaseException as exc:  # noqa: BLE001 — поток обязан не потерять исключение
                db.rollback()
                errors.append(exc)
            finally:
                db.close()

        writer = threading.Thread(target=write, name="takeover-writer")
        watcher = committing_session_factory()
        try:
            writer.start()
            # Запись дошла до замка строки задания, удержанного перехватом.
            deadline = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=10)
            waited = 0
            while waited == 0 and dt.datetime.now(dt.UTC) < deadline:
                waited = _lock_waiters(watcher)
                watcher.rollback()
            taker.commit()
            writer.join(timeout=_JOIN_TIMEOUT)
        finally:
            taker.rollback()
            taker.close()
            watcher.close()

        assert not writer.is_alive(), "запись зависла за отведённый таймаут"
        assert not errors, errors
        # Без ожидания перехват закоммитился бы раньше чтения записи — и тест
        # прошёл бы при любом замке.
        assert waited > 0, "запись не дошла до замка перехвата"
        committing_db.expire_all()
        job = committing_db.get(SemanticJob, claim.job_id)
        assert (job.status, job.claim_token) == ("running", new_token)
        assert _attempts(committing_db, claim.job_id)[0].outcome == "lost_claim"

    def test_two_overruns_at_once_write_the_stop_only_once(
        self, committing_session_factory, committing_db, committing_factories
    ):
        scene = _committed_scene(
            committing_db, committing_factories, titles=("Первая работа", "Вторая работа")
        )
        first = claim_next(committing_db, settings=S, now=NOW)
        second = claim_next(committing_db, settings=S, now=NOW)
        assert first is not None and second is not None
        # Объекты сессии теста в потоки не передаются: ленивая загрузка из двух
        # потоков в одной сессии запрещена.
        family_id = scene.family.id
        names = {"fuse-0", "fuse-1"}
        barrier = threading.Barrier(2)
        state_writes: list[str] = []
        errors: dict[str, BaseException] = {}

        def _after(conn, cursor, statement, parameters, context, executemany):
            text = statement.lower()
            if threading.current_thread().name in names and "from semantic_worker_state" in text:
                # Оба предохранителя прочли состояние раньше, чем любой записал.
                with contextlib.suppress(threading.BrokenBarrierError):
                    barrier.wait(timeout=3)

        def _before(conn, cursor, statement, parameters, context, executemany):
            text = statement.lower()
            if threading.current_thread().name in names and text.startswith(
                "update semantic_worker_state"
            ):
                state_writes.append(threading.current_thread().name)

        def run(claim, now) -> None:
            db = committing_session_factory()
            try:
                record_result(
                    db, claim, _response(_answer(family_id), cost=Decimal("1")),
                    now=now, settings=S,
                )
            except BaseException as exc:  # noqa: BLE001 — поток обязан не потерять исключение
                db.rollback()
                errors[threading.current_thread().name] = exc
            finally:
                db.close()

        engine = committing_db.get_bind()
        sa.event.listen(engine, "after_cursor_execute", _after)
        sa.event.listen(engine, "before_cursor_execute", _before)
        threads = [
            threading.Thread(target=run, args=(first, NOW), name="fuse-0"),
            threading.Thread(target=run, args=(second, NOW + dt.timedelta(minutes=5)), name="fuse-1"),
        ]
        try:
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=_JOIN_TIMEOUT)
        finally:
            sa.event.remove(engine, "after_cursor_execute", _after)
            sa.event.remove(engine, "before_cursor_execute", _before)

        assert not any(t.is_alive() for t in threads), "поток завис за отведённый таймаут"
        assert not errors, errors
        assert len(state_writes) == 1
        state = _state(committing_db)
        assert state.claim_paused is True
        assert state.paused_attempt_id in {first.attempt_id, second.attempt_id}


class TestRecordResultAgainstDecision:
    def test_assigning_a_family_waits_for_the_context_of_a_recording_and_both_finish(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch
    ):
        """`record_result` берёт ключевой замок на контекст ДО замка задания
        (порядок «домен -> задание»); назначение семьи держит контекст. Пока
        запись стоит с контекстом и заданием, назначение ждёт контекст, а не
        задание; цикла нет, и после возобновления записи заканчиваются обе."""
        scene = _scene(committing_db, committing_factories)
        committing_db.commit()
        context_id, family_id, user_id = scene.context_ids[0], scene.family.id, scene.user.id
        with committing_session_factory() as claim_db:
            claim = _claim_one(claim_db)
            claim_db.commit()

        locked, resume = threading.Event(), threading.Event()
        real_load_attempt = worker._load_attempt

        def _paused_load_attempt(db, attempt_id):
            locked.set()  # задание уже заблокировано `_lock_job`
            resume.wait(timeout=20)
            return real_load_attempt(db, attempt_id)

        monkeypatch.setattr(worker, "_load_attempt", _paused_load_attempt)
        outcome: dict[str, str] = {}

        def _record() -> None:
            db = committing_session_factory()
            try:
                db.execute(sa.text("SET LOCAL lock_timeout = '20s'"))
                record_result(db, claim, _response(_answer(family_id)), now=NOW, settings=S)
                outcome["record"] = "ok"
            except BaseException as exc:  # noqa: BLE001 — поток обязан не потерять исключение
                db.rollback()
                outcome["record"] = repr(exc)
            finally:
                db.close()

        decider_pid: list[int] = []

        def _decide() -> None:
            db = committing_session_factory()
            try:
                db.execute(sa.text("SET LOCAL lock_timeout = '20s'"))
                decider_pid.append(db.execute(sa.text("SELECT pg_backend_pid()")).scalar_one())
                assign_family(db, context_id=context_id, family_id=family_id, actor_id=user_id)
                db.commit()
                outcome["decision"] = "ok"
            except BaseException as exc:  # noqa: BLE001
                db.rollback()
                outcome["decision"] = repr(exc)
            finally:
                db.close()

        recorder = threading.Thread(target=_record, name="record-result")
        decider = threading.Thread(target=_decide, name="assign-family")
        try:
            recorder.start()
            assert locked.wait(timeout=15), "запись результата не дошла до замка задания"
            decider.start()
            decider.join(timeout=3)
            waited_while_recording_is_paused = decider.is_alive()
            # Где именно ждёт назначение: на замке контекста, а не задания
            # (ревью задачи 8, круг 1 — докстрока это утверждает).
            from tests.integration.test_work_families import _wait_until_backend_blocks

            waits_on_the_context = bool(decider_pid) and _wait_until_backend_blocks(
                committing_session_factory, pid=decider_pid[0], contains="FROM catalog_contexts",
                timeout=5,
            )
        finally:
            resume.set()
            recorder.join(timeout=_JOIN_TIMEOUT)
            decider.join(timeout=_JOIN_TIMEOUT)

        assert not recorder.is_alive() and not decider.is_alive(), "поток завис за таймаут"
        assert waited_while_recording_is_paused, "запись не держала контекст до замка задания"
        assert waits_on_the_context, "назначение ждало не контекст"
        assert outcome == {"decision": "ok", "record": "ok"}
