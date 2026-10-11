"""Точки инварианта очереди семантических предложений: импорт, замена,
удаления (задача 8 фичи «Семантические предложения»).

План: `docs/superpowers/plans/2026-09-28-semantic-suggestions.md`, задача 8.
Спека: `docs/superpowers/specs/2026-09-28-semantic-suggestions-design.md`
§2.7 (перечень операций и решение о точке импорта), §2.11 (потолок события).

Каждый тест точки строится входом, который краснеет, если вызов сверки в
ЭТОЙ точке снят: без сверки задания остаются такими, какими их оставила
предыдущая операция. Помощники — локальные: наборы помощников тестов проекта
друг у друга не импортируют.
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from config import settings
from crud import contracts as crud_contracts
from crud import tenders as crud_tenders
from models import (
    ContextMember,
    Contract,
    Estimate,
    ImportJob,
    ImportJobStatus,
    OfferPackage,
    PositionItem,
    SemanticCancelReason,
    SemanticJob,
    SemanticJobStatus,
    SemanticReconcileBatch,
    Tender,
    TenderRound,
)
from parser import ParseResult
from services import import_pipeline
from services.context_routing import route_position
from services.semantic_reconcile import (
    NO_CAP,
    contexts_of_estimates,
    contexts_of_positions,
    reconcile_semantic_jobs,
)
from services.semantic_request import load_request_material, render_context_request
from services.unit_resolution import UnitResolver
from services.work_families import activate_family, create_family
from tests.factories import seed_category_id
from tests.payloads import baseline_proposal_block, payload_for, position, proposal, round_payload

pytestmark = pytest.mark.integration

_PENDING = SemanticJobStatus.pending.value
_CANCELLED = SemanticJobStatus.cancelled.value
_NOT_APPLICABLE = SemanticCancelReason.not_applicable.value
_INPUT_CHANGED = SemanticCancelReason.input_changed.value


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _fake_parse(payload):
    def _parse(handle):
        handle.read()
        return ParseResult(data=payload, parser_version="1.0.0", warnings=[])

    return _parse


def _active_family(db, *, title, unit_name, actor_id):
    fam = create_family(
        db, title=title, unit_name=unit_name, definition="Определение семьи", actor_id=actor_id,
        family_category_id=seed_category_id(db),
    )
    return activate_family(db, family_id=fam.id, actor_id=actor_id)


class _Env:
    def __init__(self, db, factories, storage, session_factory):
        self.db = db
        self.factories = factories
        self.storage = storage
        self.session_factory = session_factory
        self.contract = factories.ContractFactory.create()
        self.user = factories.UserFactory.create()
        db.flush()
        _active_family(db, title="Семья стяжек", unit_name="M2", actor_id=self.user.id)
        db.commit()

    def new_job(self, *, contract=None, round_id=None, amendment_no=None):
        key = self.storage.save(b"PK\x03\x04not-a-real-xlsx")
        if round_id is not None:
            job = self.factories.ImportJobFactory.create(
                contract=None, round_id=round_id, file_key=key, status=ImportJobStatus.pending.value
            )
        else:
            job = self.factories.ImportJobFactory.create(
                contract=contract or self.contract,
                amendment_no=amendment_no,
                file_key=key,
                status=ImportJobStatus.pending.value,
            )
        self.db.commit()
        return job

    def run(self, payload, *, job, **kwargs):
        import_pipeline.run_import_job(
            job.id,
            session_factory=self.session_factory,
            storage=self.storage,
            parse=_fake_parse(payload),
            **kwargs,
        )
        self.db.expire_all()
        return self.db.get(ImportJob, job.id)


@pytest.fixture
def env(committing_db, committing_factories, tmp_storage, committing_session_factory):
    return _Env(committing_db, committing_factories, tmp_storage, committing_session_factory)


def _context_of(db, title) -> int:
    """Контекст позиции с этим названием (в тесте название уникально)."""
    db.expire_all()
    return db.execute(
        sa.select(ContextMember.context_id)
        .join(PositionItem, PositionItem.id == ContextMember.position_item_id)
        .where(PositionItem.job_title_in_proposal == title)
    ).scalars().first()


def _jobs(db, context_id) -> list[SemanticJob]:
    db.expire_all()
    return list(
        db.execute(
            sa.select(SemanticJob).where(SemanticJob.context_id == context_id).order_by(SemanticJob.id)
        ).scalars()
    )


def _current_hash(db, context_id) -> str:
    material = load_request_material(db, [context_id])[context_id]
    return render_context_request(material, settings=settings).request_hash


def _rows(title, chapter=None, cost="100", number="2"):
    """Смета из одной работы; с `chapter` работа лежит в разделе с этим названием."""
    if chapter is None:
        return [position(job_title=title, unit="м2", unit_cost_total=cost, number=number)]
    return [
        position(job_title=chapter, is_chapter=True, chapter_number="1", number="1"),
        position(job_title=title, unit="м2", unit_cost_total=cost, number="1.1"),
    ]


def _count(db, model) -> int:
    db.expire_all()
    return db.execute(sa.select(sa.func.count()).select_from(model)).scalar_one()


# ---------------------------------------------------------------------------
#  Импорт сметы договора
# ---------------------------------------------------------------------------

class TestImportPoint:
    def test_context_born_only_by_this_imports_routing_gets_pending(self, env):
        """До импорта контекстов нет вовсе: они появляются маршрутизацией этого
        импорта. Сверка внутри `import_estimate` (до `route_positions`) их не
        увидела бы — заданий не было бы."""
        assert _count(env.db, ContextMember) == 0

        job = env.run(payload_for(env.contract, _rows("Устройство стяжки")), job=env.new_job())

        assert job.status == ImportJobStatus.done.value
        ctx = _context_of(env.db, "Устройство стяжки")
        rows = _jobs(env.db, ctx)
        assert [(j.status, j.cancel_reason) for j in rows] == [(_PENDING, None)]
        assert rows[0].request_hash == _current_hash(env.db, ctx)

    def test_import_over_the_event_cap_holds_the_batch_and_finishes_done(self, env, monkeypatch):
        """Потолок события: пачка `held` с `import_job_id`, импорт `done`,
        счётчики те же, что у такого же импорта без потолка."""
        titles_free = ["Кладка А", "Кладка Б", "Кладка В"]
        titles_capped = ["Заливка А", "Заливка Б", "Заливка В"]

        def rows_for(titles):
            return [_rows(t, number=str(i + 2))[0] for i, t in enumerate(titles)]

        free_job = env.run(payload_for(env.contract, rows_for(titles_free)), job=env.new_job())
        assert free_job.status == ImportJobStatus.done.value

        monkeypatch.setattr(settings, "SEMANTIC_EVENT_MAX_CONTEXTS", 2)
        other = env.factories.ContractFactory.create()
        env.db.commit()
        capped_job = env.run(
            payload_for(other, rows_for(titles_capped)), job=env.new_job(contract=other)
        )

        assert capped_job.status == ImportJobStatus.done.value
        counters = ("positions_total", "matched_cache", "matched_exact", "matched_nonposition", "to_review")
        assert [getattr(capped_job, c) for c in counters] == [getattr(free_job, c) for c in counters]
        assert capped_job.warnings == free_job.warnings
        batches = env.db.execute(sa.select(SemanticReconcileBatch)).scalars().all()
        assert [(b.import_job_id, b.status, b.source, b.contexts_count) for b in batches] == [
            (capped_job.id, "held", "import", 3)
        ]
        for title in titles_capped:
            assert _jobs(env.db, _context_of(env.db, title)) == []
        for title in titles_free:
            assert len(_jobs(env.db, _context_of(env.db, title))) == 1

    def test_exception_inside_the_reconcile_rolls_the_import_back(self, env, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("сверка упала")

        monkeypatch.setattr(import_pipeline, "reconcile_semantic_jobs", boom)

        job = env.run(payload_for(env.contract, _rows("Устройство стяжки")), job=env.new_job())

        assert job.status == ImportJobStatus.error.value
        assert _count(env.db, Estimate) == 0
        assert _count(env.db, ContextMember) == 0
        assert _count(env.db, SemanticJob) == 0


# ---------------------------------------------------------------------------
#  Замена сметы
# ---------------------------------------------------------------------------

class TestReplaceEstimatePoint:
    """Замена сметы договора: `estimate_import.import_estimate` собирает контексты
    вытесненной сметы до её удаления, `run_import_job` сверяет их после маршрутизации."""

    def test_context_whose_positions_were_only_in_the_replaced_estimate_is_cancelled(self, env):
        first = env.run(payload_for(env.contract, _rows("Устройство стяжки")), job=env.new_job())
        assert first.status == ImportJobStatus.done.value
        old_ctx = _context_of(env.db, "Устройство стяжки")
        assert [j.status for j in _jobs(env.db, old_ctx)] == [_PENDING]

        second = env.run(
            payload_for(env.contract, _rows("Кладка стен")), job=env.new_job(), replace=True
        )

        assert second.status == ImportJobStatus.done.value
        # позиции старого контекста удалены каскадом, членств у контекста нет
        assert [(j.status, j.cancel_reason) for j in _jobs(env.db, old_ctx)] == [
            (_CANCELLED, _NOT_APPLICABLE)
        ]
        assert [j.status for j in _jobs(env.db, _context_of(env.db, "Кладка стен"))] == [_PENDING]

    def test_context_whose_most_frequent_path_changed_gets_a_new_pending(self, env):
        first = env.run(
            payload_for(env.contract, _rows("Устройство стяжки", "Раздел А")), job=env.new_job()
        )
        assert first.status == ImportJobStatus.done.value
        ctx = _context_of(env.db, "Устройство стяжки")
        (old_job,) = _jobs(env.db, ctx)
        assert old_job.status == _PENDING

        second = env.run(
            payload_for(env.contract, _rows("Устройство стяжки", "Раздел Б")),
            job=env.new_job(),
            replace=True,
        )

        assert second.status == ImportJobStatus.done.value
        assert _context_of(env.db, "Устройство стяжки") == ctx
        rows = _jobs(env.db, ctx)
        assert len(rows) == 2
        old, new = rows
        assert (old.status, old.cancel_reason) == (_CANCELLED, _INPUT_CHANGED)
        assert new.status == _PENDING
        assert new.request_hash == _current_hash(env.db, ctx)
        assert new.request_hash != old.request_hash

    def test_replacing_an_amendment_cancels_the_amendments_context_only(self, env):
        """Вытесняется смета ТОЙ ЖЕ пары «договор, допсоглашение»: контекст,
        бывший только в допсоглашении, отменён, контекст основной сметы не тронут."""
        env.run(payload_for(env.contract, _rows("Устройство стяжки")), job=env.new_job())
        env.run(payload_for(env.contract, _rows("Кладка стен")), job=env.new_job(amendment_no=1))
        base_ctx = _context_of(env.db, "Устройство стяжки")
        amendment_ctx = _context_of(env.db, "Кладка стен")
        (base_job,) = _jobs(env.db, base_ctx)
        assert [j.status for j in _jobs(env.db, amendment_ctx)] == [_PENDING]

        third = env.run(
            payload_for(env.contract, _rows("Штукатурка стен")),
            job=env.new_job(amendment_no=1),
            replace=True,
        )

        assert third.status == ImportJobStatus.done.value
        assert [(j.status, j.cancel_reason) for j in _jobs(env.db, amendment_ctx)] == [
            (_CANCELLED, _NOT_APPLICABLE)
        ]
        assert [(j.id, j.status) for j in _jobs(env.db, base_ctx)] == [(base_job.id, _PENDING)]
        assert [j.status for j in _jobs(env.db, _context_of(env.db, "Штукатурка стен"))] == [_PENDING]


# ---------------------------------------------------------------------------
#  Замена раунда
# ---------------------------------------------------------------------------

def _round_payload(rows):
    return round_payload(
        [proposal(rows, title="ООО А", inn="7700000001")],
        baseline=baseline_proposal_block(rows),
    )


class TestReplaceRoundPoint:
    """Замена раунда: `round_import.import_round` собирает контексты вытесненных смет
    до удаления, `run_import_job` сверяет их после маршрутизации."""

    def _round(self, env):
        rnd = env.factories.TenderRoundFactory.create()
        env.db.commit()
        return rnd

    def test_round_file_pends_and_replace_cancels_the_emptied_context(self, env):
        rnd = self._round(env)
        first = env.run(_round_payload(_rows("Устройство стяжки")), job=env.new_job(round_id=rnd.id))
        assert first.status == ImportJobStatus.done.value
        old_ctx = _context_of(env.db, "Устройство стяжки")
        assert [j.status for j in _jobs(env.db, old_ctx)] == [_PENDING]

        second = env.run(
            _round_payload(_rows("Кладка стен")), job=env.new_job(round_id=rnd.id), replace=True
        )

        assert second.status == ImportJobStatus.done.value
        assert [(j.status, j.cancel_reason) for j in _jobs(env.db, old_ctx)] == [
            (_CANCELLED, _NOT_APPLICABLE)
        ]
        assert [j.status for j in _jobs(env.db, _context_of(env.db, "Кладка стен"))] == [_PENDING]

    def test_round_replace_with_a_changed_path_replaces_the_pending(self, env):
        rnd = self._round(env)
        env.run(_round_payload(_rows("Устройство стяжки", "Раздел А")), job=env.new_job(round_id=rnd.id))
        ctx = _context_of(env.db, "Устройство стяжки")
        (old_job,) = _jobs(env.db, ctx)
        assert old_job.status == _PENDING

        second = env.run(
            _round_payload(_rows("Устройство стяжки", "Раздел Б")),
            job=env.new_job(round_id=rnd.id),
            replace=True,
        )

        assert second.status == ImportJobStatus.done.value
        old, new = _jobs(env.db, ctx)
        assert (old.status, old.cancel_reason) == (_CANCELLED, _INPUT_CHANGED)
        assert (new.status, new.request_hash) == (_PENDING, _current_hash(env.db, ctx))


# ---------------------------------------------------------------------------
#  Удаления: договор, тендер, раунд, участник
# ---------------------------------------------------------------------------

class _Grid:
    """Корпус удалений: контексты строятся напрямую фабриками и `route_position`,
    задания создаются сверкой без потолка и коммитятся — то состояние очереди,
    которое застаёт удаление."""

    def __init__(self, db, factories):
        self.db = db
        self.factories = factories
        self.unit_id = UnitResolver(db).resolve("M2").unit_id
        user = factories.UserFactory.create()
        db.flush()
        _active_family(db, title="Семья стяжек", unit_name="M2", actor_id=user.id)
        self.catalog: dict[str, int] = {}

    def add_position(self, estimate, title, *, chapter=None) -> int:
        """Позиция в новом лоте-предложении сметы; позиции одного названия делят
        каталожную строку, а значит и контекст. Возвращает контекст."""
        lot = self.factories.LotFactory.create(estimate=estimate)
        prop = self.factories.ProposalFactory.create(lot=lot)
        if title not in self.catalog:
            self.catalog[title] = self.factories.CatalogPositionFactory.create(
                unit_id=self.unit_id, standard_job_title=title
            ).id
        chapter_item_id = None
        if chapter is not None:
            chapter_item_id = self.factories.PositionItemFactory.create(
                proposal=prop, is_chapter=True, job_title_in_proposal=chapter
            ).id
        item = self.factories.PositionItemFactory.create(
            proposal=prop,
            is_chapter=False,
            job_title_in_proposal=title,
            catalog_position_id=self.catalog[title],
            chapter_item_id=chapter_item_id,
        )
        return route_position(self.db, position_item_id=item.id).context_id

    def settle(self, *context_ids):
        """Постановка заданий по текущему состоянию и коммит."""
        reconcile_semantic_jobs(self.db, set(context_ids), cap=NO_CAP, source="operation")
        self.db.commit()

    def tender_with_offer_estimate(self):
        tender = self.factories.TenderFactory.create()
        rnd = self.factories.TenderRoundFactory.create(tender=tender, stage_no=1)
        self.db.flush()
        offer, estimate = self.offer_estimate(rnd)
        return tender, rnd, offer, estimate

    def offer_estimate(self, rnd, *, package=None):
        """Предложение участника в раунде и его смета; без `package` — новый участник."""
        extra = {} if package is None else {"package": package}
        offer = self.factories.OfferFactory.create(round=rnd, **extra)
        self.db.flush()
        estimate = self.factories.EstimateFactory.create(contract=None, offer_id=offer.id)
        self.db.flush()
        return offer, estimate

    def path_split(self, big, small) -> int:
        """Контекст из двух позиций `big` под «Раздел А» и одной позиции `small` под
        «Раздел Б»: самый частый путь — «Раздел А», пока `big` существует."""
        ctx = self.add_position(big, "Устройство стяжки", chapter="Раздел А")
        assert self.add_position(big, "Устройство стяжки", chapter="Раздел А") == ctx
        assert self.add_position(small, "Устройство стяжки", chapter="Раздел Б") == ctx
        self.settle(ctx)
        return ctx


def _assert_new_pending_after_path_change(db, ctx, old_hash):
    old, new = _jobs(db, ctx)
    assert (old.status, old.cancel_reason, old.request_hash) == (_CANCELLED, _INPUT_CHANGED, old_hash)
    assert (new.status, new.request_hash) == (_PENDING, _current_hash(db, ctx))
    assert new.request_hash != old_hash


def _assert_held_after_path_change(db, ctx, old_hash):
    """Потолок события при удалении: старое задание отменено (отмена денег не
    стоит), нового нет, вместо него — пачка `held` источника `operation`."""
    (old,) = _jobs(db, ctx)
    assert (old.status, old.cancel_reason, old.request_hash) == (_CANCELLED, _INPUT_CHANGED, old_hash)
    batches = db.execute(sa.select(SemanticReconcileBatch)).scalars().all()
    assert [(b.status, b.source, b.import_job_id, b.contexts_count) for b in batches] == [
        ("held", "operation", None, 1)
    ]


@pytest.fixture
def grid(committing_db, committing_factories):
    return _Grid(committing_db, committing_factories)


def _boom(*args, **kwargs):
    raise RuntimeError("сверка упала")


class TestDeleteContractPoint:
    def test_context_emptied_by_the_cascade_is_cancelled(self, grid):
        estimate = grid.factories.EstimateFactory.create()
        grid.db.flush()
        ctx = grid.add_position(estimate, "Устройство стяжки")
        grid.settle(ctx)
        assert [j.status for j in _jobs(grid.db, ctx)] == [_PENDING]

        crud_contracts.delete_contract(grid.db, estimate.contract_id)

        assert [(j.status, j.cancel_reason) for j in _jobs(grid.db, ctx)] == [
            (_CANCELLED, _NOT_APPLICABLE)
        ]

    def test_context_whose_most_frequent_path_changed_gets_a_new_pending(self, grid):
        big = grid.factories.EstimateFactory.create()
        small = grid.factories.EstimateFactory.create()
        grid.db.flush()
        ctx = grid.add_position(big, "Устройство стяжки", chapter="Раздел А")
        assert grid.add_position(big, "Устройство стяжки", chapter="Раздел А") == ctx
        assert grid.add_position(small, "Устройство стяжки", chapter="Раздел Б") == ctx
        grid.settle(ctx)
        (old_job,) = _jobs(grid.db, ctx)
        assert old_job.status == _PENDING

        crud_contracts.delete_contract(grid.db, big.contract_id)

        old, new = _jobs(grid.db, ctx)
        assert (old.status, old.cancel_reason) == (_CANCELLED, _INPUT_CHANGED)
        assert (new.status, new.request_hash) == (_PENDING, _current_hash(grid.db, ctx))
        assert new.request_hash != old.request_hash

    def test_exception_inside_the_reconcile_keeps_the_contract(self, grid, monkeypatch):
        estimate = grid.factories.EstimateFactory.create()
        grid.db.flush()
        contract_id = estimate.contract_id
        ctx = grid.add_position(estimate, "Устройство стяжки")
        grid.settle(ctx)
        monkeypatch.setattr(crud_contracts, "reconcile_semantic_jobs", _boom)

        with pytest.raises(RuntimeError):
            crud_contracts.delete_contract(grid.db, contract_id)
        grid.db.rollback()

        assert grid.db.get(Contract, contract_id) is not None
        assert _count(grid.db, ContextMember) == 1
        assert [j.status for j in _jobs(grid.db, ctx)] == [_PENDING]

    def test_deletion_over_the_event_cap_holds_the_new_job(self, grid, monkeypatch):
        big = grid.factories.EstimateFactory.create()
        small = grid.factories.EstimateFactory.create()
        grid.db.flush()
        ctx = grid.path_split(big, small)
        (old_job,) = _jobs(grid.db, ctx)
        monkeypatch.setattr(settings, "SEMANTIC_EVENT_MAX_CONTEXTS", 0)

        crud_contracts.delete_contract(grid.db, big.contract_id)

        _assert_held_after_path_change(grid.db, ctx, old_job.request_hash)


class TestDeleteTenderPoint:
    def test_contexts_of_offer_owned_estimates_are_cancelled(self, grid):
        tender, _rnd, _offer, estimate = grid.tender_with_offer_estimate()
        ctx = grid.add_position(estimate, "Устройство стяжки")
        grid.settle(ctx)
        assert [j.status for j in _jobs(grid.db, ctx)] == [_PENDING]

        crud_tenders.delete_tender(grid.db, tender.id)

        assert [(j.status, j.cancel_reason) for j in _jobs(grid.db, ctx)] == [
            (_CANCELLED, _NOT_APPLICABLE)
        ]

    def test_context_whose_most_frequent_path_changed_gets_a_new_pending(self, grid):
        tender, _rnd, _offer, estimate = grid.tender_with_offer_estimate()
        contract_estimate = grid.factories.EstimateFactory.create()
        grid.db.flush()
        ctx = grid.path_split(estimate, contract_estimate)
        (old_job,) = _jobs(grid.db, ctx)

        crud_tenders.delete_tender(grid.db, tender.id)

        _assert_new_pending_after_path_change(grid.db, ctx, old_job.request_hash)

    def test_exception_inside_the_reconcile_keeps_the_tender(self, grid, monkeypatch):
        tender, _rnd, _offer, estimate = grid.tender_with_offer_estimate()
        tender_id = tender.id
        ctx = grid.add_position(estimate, "Устройство стяжки")
        grid.settle(ctx)
        monkeypatch.setattr(crud_tenders, "reconcile_semantic_jobs", _boom)

        with pytest.raises(RuntimeError):
            crud_tenders.delete_tender(grid.db, tender_id)
        grid.db.rollback()

        assert grid.db.get(Tender, tender_id) is not None
        assert _count(grid.db, ContextMember) == 1
        assert [j.status for j in _jobs(grid.db, ctx)] == [_PENDING]


class TestDeleteRoundPoint:
    def test_contexts_of_offer_owned_and_round_owned_estimates_are_cancelled(self, grid):
        tender, rnd, _offer, offer_estimate = grid.tender_with_offer_estimate()
        baseline = grid.factories.EstimateFactory.create(contract=None, round_id=rnd.id)
        grid.db.flush()
        ctx_offer = grid.add_position(offer_estimate, "Устройство стяжки")
        ctx_baseline = grid.add_position(baseline, "Кладка стен")
        assert ctx_offer != ctx_baseline
        grid.settle(ctx_offer, ctx_baseline)

        crud_tenders.delete_round(grid.db, tender.id, rnd.id)

        for ctx in (ctx_offer, ctx_baseline):
            assert [(j.status, j.cancel_reason) for j in _jobs(grid.db, ctx)] == [
                (_CANCELLED, _NOT_APPLICABLE)
            ]

    def test_context_kept_by_another_round_stays_pending(self, grid):
        tender, rnd, offer, estimate = grid.tender_with_offer_estimate()
        other_round = grid.factories.TenderRoundFactory.create(tender=tender, stage_no=2)
        grid.db.flush()
        other_offer = grid.factories.OfferFactory.create(round=other_round, package=offer.package)
        grid.db.flush()
        other_estimate = grid.factories.EstimateFactory.create(contract=None, offer_id=other_offer.id)
        grid.db.flush()
        ctx = grid.add_position(estimate, "Устройство стяжки")
        assert grid.add_position(other_estimate, "Устройство стяжки") == ctx
        grid.settle(ctx)
        (before,) = _jobs(grid.db, ctx)

        crud_tenders.delete_round(grid.db, tender.id, rnd.id)

        (after,) = _jobs(grid.db, ctx)
        assert (after.id, after.status, after.request_hash) == (
            before.id, _PENDING, before.request_hash
        )

    def test_context_whose_most_frequent_path_changed_gets_a_new_pending(self, grid):
        tender, rnd, offer, estimate = grid.tender_with_offer_estimate()
        other_round = grid.factories.TenderRoundFactory.create(tender=tender, stage_no=2)
        grid.db.flush()
        _other_offer, other_estimate = grid.offer_estimate(other_round, package=offer.package)
        ctx = grid.path_split(estimate, other_estimate)
        (old_job,) = _jobs(grid.db, ctx)

        crud_tenders.delete_round(grid.db, tender.id, rnd.id)

        _assert_new_pending_after_path_change(grid.db, ctx, old_job.request_hash)

    def test_exception_inside_the_reconcile_keeps_the_round(self, grid, monkeypatch):
        tender, rnd, _offer, estimate = grid.tender_with_offer_estimate()
        tender_id, round_id = tender.id, rnd.id
        ctx = grid.add_position(estimate, "Устройство стяжки")
        grid.settle(ctx)
        monkeypatch.setattr(crud_tenders, "reconcile_semantic_jobs", _boom)

        with pytest.raises(RuntimeError):
            crud_tenders.delete_round(grid.db, tender_id, round_id)
        grid.db.rollback()

        assert grid.db.get(TenderRound, round_id) is not None
        assert _count(grid.db, ContextMember) == 1
        assert [j.status for j in _jobs(grid.db, ctx)] == [_PENDING]


class TestDeleteParticipantPoint:
    def test_context_of_the_participants_estimates_is_cancelled(self, grid):
        tender, _rnd, offer, estimate = grid.tender_with_offer_estimate()
        ctx = grid.add_position(estimate, "Устройство стяжки")
        grid.settle(ctx)
        assert [j.status for j in _jobs(grid.db, ctx)] == [_PENDING]
        token = crud_tenders.participant_deletion_preview(grid.db, tender.id, offer.package_id)[
            "confirmation_token"
        ]

        crud_tenders.delete_participant(
            grid.db, tender.id, offer.package_id, confirmation_token=token
        )

        assert [(j.status, j.cancel_reason) for j in _jobs(grid.db, ctx)] == [
            (_CANCELLED, _NOT_APPLICABLE)
        ]

    def _delete(self, grid, tender, package_id):
        token = crud_tenders.participant_deletion_preview(grid.db, tender.id, package_id)[
            "confirmation_token"
        ]
        crud_tenders.delete_participant(grid.db, tender.id, package_id, confirmation_token=token)

    def test_context_whose_most_frequent_path_changed_gets_a_new_pending(self, grid):
        tender, rnd, offer, estimate = grid.tender_with_offer_estimate()
        _other_offer, other_estimate = grid.offer_estimate(rnd)
        ctx = grid.path_split(estimate, other_estimate)
        (old_job,) = _jobs(grid.db, ctx)

        self._delete(grid, tender, offer.package_id)

        _assert_new_pending_after_path_change(grid.db, ctx, old_job.request_hash)

    def test_deletion_over_the_event_cap_holds_the_new_job(self, grid, monkeypatch):
        tender, rnd, offer, estimate = grid.tender_with_offer_estimate()
        _other_offer, other_estimate = grid.offer_estimate(rnd)
        ctx = grid.path_split(estimate, other_estimate)
        (old_job,) = _jobs(grid.db, ctx)
        monkeypatch.setattr(settings, "SEMANTIC_EVENT_MAX_CONTEXTS", 0)

        self._delete(grid, tender, offer.package_id)

        _assert_held_after_path_change(grid.db, ctx, old_job.request_hash)

    def test_exception_inside_the_reconcile_keeps_the_participant(self, grid, monkeypatch):
        tender, _rnd, offer, estimate = grid.tender_with_offer_estimate()
        package_id = offer.package_id
        ctx = grid.add_position(estimate, "Устройство стяжки")
        grid.settle(ctx)
        monkeypatch.setattr(crud_tenders, "reconcile_semantic_jobs", _boom)

        with pytest.raises(RuntimeError):
            self._delete(grid, tender, package_id)
        grid.db.rollback()

        assert grid.db.get(OfferPackage, package_id) is not None
        assert _count(grid.db, ContextMember) == 1
        assert [j.status for j in _jobs(grid.db, ctx)] == [_PENDING]


# ---------------------------------------------------------------------------
#  Сбор затронутых контекстов
# ---------------------------------------------------------------------------

class TestAffectedContexts:
    def test_empty_input_answers_without_touching_the_database(self):
        assert contexts_of_positions(None, []) == set()
        assert contexts_of_estimates(None, []) == set()
        assert crud_tenders._estimate_ids_of_rounds(None, []) == []

    def test_contexts_are_those_of_the_given_positions_and_estimates_only(self, grid):
        first = grid.factories.EstimateFactory.create()
        second = grid.factories.EstimateFactory.create()
        grid.db.flush()
        screed = grid.add_position(first, "Устройство стяжки")
        masonry = grid.add_position(first, "Кладка стен")
        plaster = grid.add_position(second, "Штукатурка стен")
        assert len({screed, masonry, plaster}) == 3
        screed_item = grid.db.execute(
            sa.select(PositionItem.id).where(PositionItem.job_title_in_proposal == "Устройство стяжки")
        ).scalar_one()

        assert contexts_of_positions(grid.db, [screed_item]) == {screed}
        assert contexts_of_estimates(grid.db, [first.id]) == {screed, masonry}
        assert contexts_of_estimates(grid.db, [first.id, second.id]) == {screed, masonry, plaster}
