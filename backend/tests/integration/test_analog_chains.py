"""Цепочки разделов и зона: присвоение, шаг импорта, команда заполнения, очередь
«Зоны» и решение человека (лестница аналогов, спека §2.4, §2.17, §2.19).

`assign_section_chains`: строки (не главы) получают `section_chain_id`; правило и
задание `section_zone` — только для цепочек, созданных этим вызовом; цепочку с
зоной `human` вызов не меняет; счётчики `import_jobs` не трогает. Шаг стоит в
транзакции сессии B между `match_positions` и `route_positions`; отказ шага
откатывает импорт целиком. `backfill_chains` идемпотентна. Названия разделов —
общие («Подземная часть», «Кровля»), настоящих названий объектов здесь нет.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import random
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

import crud.section_chains as chains_crud
from config import settings as app_settings
from crud.common import DomainError, iso
from crud.section_chains import (
    assign_section_chains,
    backfill_chains,
    decide_zone,
    get_chain,
    list_chains,
    zone_chain_info,
)
from models import (
    ImportJob,
    ImportJobStatus,
    PositionItem,
    SectionChain,
    SemanticJob,
    SemanticJobAttempt,
    SemanticJobKind,
    SemanticJobStatus,
    SemanticWorkerState,
)
from services.context_routing import RoutingError
from services.zone_request import render_zone_request
from services.zone_rules import chain_key
from tests.integration.test_import_pipeline import fake_parse, job_env  # noqa: F401
from tests.integration.test_our_price_signal import _pause
from tests.payloads import payload_for, position

pytestmark = pytest.mark.integration

S = app_settings


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _estimate(factories, *, kp: bool = False):
    if kp:
        estimate = factories.EstimateFactory.create(
            contract=None, offer=factories.OfferFactory.create()
        )
    else:
        estimate = factories.EstimateFactory.create()
    lot = factories.LotFactory.create(estimate=estimate)
    proposal = factories.ProposalFactory.create(lot=lot)
    return estimate, proposal


def _chapters(factories, proposal, titles):
    """Цепочка глав сверху вниз; возвращает ближнюю главу (или `None`)."""
    parent = None
    for title in titles:
        parent = factories.PositionItemFactory.create(
            proposal=proposal, is_chapter=True, job_title_in_proposal=title,
            chapter_item_id=parent.id if parent else None,
            unit_cost_total=None, total_cost_total=None, quantity=None, suggested_quantity=None,
        )
    return parent


def _row(factories, proposal, title, chapter=None, **kwargs):
    return factories.PositionItemFactory.create(
        proposal=proposal, is_chapter=False, job_title_in_proposal=title,
        chapter_item_id=chapter.id if chapter else None, **kwargs,
    )


def _scene(factories, titles, *rows, kp=False):
    """Смета с цепочкой глав `titles` и строками `rows` под ближней главой."""
    estimate, proposal = _estimate(factories, kp=kp)
    chapter = _chapters(factories, proposal, titles)
    items = [_row(factories, proposal, name, chapter) for name in (rows or ("Работа",))]
    return estimate, proposal, chapter, items


def _chain_of(db, item) -> SectionChain | None:
    db.expire_all()
    chain_id = db.execute(
        sa.select(PositionItem.section_chain_id).where(PositionItem.id == item.id)
    ).scalar_one()
    return db.get(SectionChain, chain_id) if chain_id is not None else None


def _zone_jobs(db):
    db.expire_all()
    return db.execute(
        sa.select(SemanticJob)
        .where(SemanticJob.kind == SemanticJobKind.section_zone.value)
        .order_by(SemanticJob.id)
    ).scalars().all()


def _chain_count(db) -> int:
    return db.execute(sa.select(sa.func.count()).select_from(SectionChain)).scalar_one()


# ---------------------------------------------------------------------------
#  assign_section_chains
# ---------------------------------------------------------------------------

class TestAssign:
    def test_rows_get_chain_and_chapters_do_not(self, db_session, factories):
        estimate, proposal, chapter, items = _scene(
            factories, ["Раздел 3", "Работы"], "Стяжка", "Шпаклёвка"
        )

        outcome = assign_section_chains(db_session, [estimate.id])

        chains = {_chain_of(db_session, item).id for item in items}
        assert len(chains) == 1
        assert outcome.assigned == 2
        chapter_chain = db_session.execute(
            sa.select(PositionItem.section_chain_id).where(PositionItem.is_chapter.is_(True))
        ).scalars().all()
        assert chapter_chain == [None, None]

    def test_chain_key_far_beyond_the_btree_limit_is_assigned_and_found_again(
        self, db_session, factories
    ):
        # 6 глав по 700 кириллических символов: ключ ~8,4 КБ, btree держит ~2,7 КБ.
        # Текст несжимаемый (btree сжимает повторы перед проверкой предела).
        rng = random.Random(24)
        titles = [
            "".join(chr(rng.randint(0x430, 0x44F)) for _ in range(700)) for _ in range(6)
        ]
        assert len(chain_key(titles).encode("utf-8")) > 8000
        first, _, _, items1 = _scene(factories, titles)
        second, _, _, items2 = _scene(factories, titles)

        out1 = assign_section_chains(db_session, [first.id])
        out2 = assign_section_chains(db_session, [second.id])

        assert (out1.created, out2.created) == (1, 0)
        assert _chain_of(db_session, items1[0]).id == _chain_of(db_session, items2[0]).id
        assert _chain_count(db_session) == 1

    def test_titles_are_stored_top_down_with_original_spelling(self, db_session, factories):
        estimate, _, _, items = _scene(factories, ["Корпус А", "Подземная  Часть"])

        assign_section_chains(db_session, [estimate.id])

        chain = _chain_of(db_session, items[0])
        assert chain.titles == ["Корпус А", "Подземная  Часть"]
        assert chain.chain_key == "корпус а / подземная часть"

    def test_outcome_counts_created_assigned_enqueued(self, db_session, factories):
        estimate, proposal = _estimate(factories)
        for titles in (["Подземная часть"], ["Раздел без шаблонов"], ["Другой раздел"]):
            chapter = _chapters(factories, proposal, titles)
            _row(factories, proposal, "Работа 1", chapter)
            _row(factories, proposal, "Работа 2", chapter)

        outcome = assign_section_chains(db_session, [estimate.id])

        assert (outcome.created, outcome.assigned, outcome.enqueued) == (3, 6, 2)

    def test_rule_sets_zone_with_source_rule_and_no_job(self, db_session, factories):
        estimate, _, _, items = _scene(factories, ["Подземная часть"])

        assign_section_chains(db_session, [estimate.id])

        chain = _chain_of(db_session, items[0])
        assert (chain.zone, chain.zone_source) == ("underground", "rule")
        assert chain.decided_by is None
        assert _zone_jobs(db_session) == []

    def test_chain_without_rule_answer_gets_a_pending_job(self, db_session, factories):
        estimate, _, _, items = _scene(factories, ["Раздел без шаблонов"])

        outcome = assign_section_chains(db_session, [estimate.id])

        chain = _chain_of(db_session, items[0])
        assert chain.zone is None and chain.proposed_zone is None
        (job,) = _zone_jobs(db_session)
        assert outcome.enqueued == 1
        assert job.section_chain_id == chain.id
        assert job.status == SemanticJobStatus.pending.value
        assert (job.context_id, job.family_id, job.schema_id, job.unit_id) == (None,) * 4
        assert job.request_hash == render_zone_request(db_session, chain.id, settings=S).request_hash
        assert job.model_requested == S.SEMANTIC_MODEL

    def test_second_estimate_with_the_same_chain_finds_it(self, db_session, factories):
        first, _, _, items1 = _scene(factories, ["Раздел без шаблонов"])
        assign_section_chains(db_session, [first.id])
        second, _, _, items2 = _scene(factories, ["Раздел без шаблонов"])

        outcome = assign_section_chains(db_session, [second.id])

        assert (outcome.created, outcome.assigned, outcome.enqueued) == (0, 1, 0)
        assert _chain_of(db_session, items1[0]).id == _chain_of(db_session, items2[0]).id
        assert _chain_count(db_session) == 1
        assert len(_zone_jobs(db_session)) == 1

    def test_case_and_spacing_variants_are_one_chain(self, db_session, factories):
        first, _, _, items1 = _scene(factories, ["Корпус А", "Прочие  работы"])
        second, _, _, items2 = _scene(factories, ["КОРПУС А", "прочие работы "])

        assign_section_chains(db_session, [first.id, second.id])

        assert _chain_of(db_session, items1[0]).id == _chain_of(db_session, items2[0]).id
        assert _chain_count(db_session) == 1

    def test_human_chain_is_not_changed_by_a_later_assignment(self, db_session, factories):
        user = factories.UserFactory.create()
        estimate, _, _, items = _scene(factories, ["Подземная часть"])
        human = SectionChain(
            chain_key=chain_key(["Подземная часть"]), titles=["Подземная часть"],
            zone="whole", zone_source="human", decided_by=user.id,
            decided_at=sa.func.now(),
        )
        db_session.add(human)
        db_session.flush()

        outcome = assign_section_chains(db_session, [estimate.id])

        db_session.refresh(human)
        assert (human.zone, human.zone_source, human.decided_by) == ("whole", "human", user.id)
        assert _chain_of(db_session, items[0]).id == human.id
        assert (outcome.created, outcome.enqueued) == (0, 0)
        assert _zone_jobs(db_session) == []

    def test_existing_chain_without_zone_does_not_get_the_rule_again(self, db_session, factories):
        # Правило работает только для созданных этим вызовом цепочек: цепочка без
        # зоны и без предложения, найденная повторно, правилом не трогается.
        estimate, _, _, items = _scene(factories, ["Подземная часть"])
        existing = SectionChain(
            chain_key=chain_key(["Подземная часть"]), titles=["Подземная часть"]
        )
        db_session.add(existing)
        db_session.flush()

        assign_section_chains(db_session, [estimate.id])

        db_session.refresh(existing)
        assert existing.zone is None
        assert _zone_jobs(db_session) == []

    def test_rows_without_a_chapter_get_no_chain(self, db_session, factories):
        estimate, proposal = _estimate(factories)
        row = _row(factories, proposal, "Работа без раздела", None)

        outcome = assign_section_chains(db_session, [estimate.id])

        assert _chain_of(db_session, row) is None
        assert (outcome.created, outcome.assigned, outcome.enqueued) == (0, 0, 0)

    def test_already_assigned_rows_are_left_alone(self, db_session, factories):
        estimate, _, _, items = _scene(factories, ["Подземная часть"])
        assign_section_chains(db_session, [estimate.id])

        outcome = assign_section_chains(db_session, [estimate.id])

        assert (outcome.created, outcome.assigned, outcome.enqueued) == (0, 0, 0)

    def test_empty_estimate_list_does_nothing(self, db_session, factories):
        _scene(factories, ["Подземная часть"])

        outcome = assign_section_chains(db_session, [])

        assert dataclasses.astuple(outcome) == (0, 0, 0)
        assert _chain_count(db_session) == 0

    def test_only_rows_of_the_given_estimates_are_assigned(self, db_session, factories):
        mine, _, _, mine_items = _scene(factories, ["Подземная часть"])
        _, _, _, other_items = _scene(factories, ["Подземная часть"])

        assign_section_chains(db_session, [mine.id])

        assert _chain_of(db_session, mine_items[0]) is not None
        assert _chain_of(db_session, other_items[0]) is None

    def test_import_jobs_row_is_not_touched(self, db_session, factories):
        job = factories.ImportJobFactory.create(status=ImportJobStatus.pending.value)
        estimate, _, _, _ = _scene(factories, ["Раздел без шаблонов"])
        db_session.flush()
        columns = [c.name for c in ImportJob.__table__.columns]
        before = db_session.execute(sa.select(ImportJob).where(ImportJob.id == job.id)).one()

        assign_section_chains(db_session, [estimate.id])

        db_session.expire_all()
        after = db_session.execute(sa.select(ImportJob).where(ImportJob.id == job.id)).one()
        assert [getattr(after[0], c) for c in columns] == [getattr(before[0], c) for c in columns]

    def test_chapter_cycle_is_a_routing_error(self, db_session, factories):
        estimate, proposal = _estimate(factories)
        a = _chapters(factories, proposal, ["А"])
        b = factories.PositionItemFactory.create(
            proposal=proposal, is_chapter=True, job_title_in_proposal="Б",
            chapter_item_id=a.id, unit_cost_total=None, total_cost_total=None,
        )
        _row(factories, proposal, "Работа", b)
        db_session.flush()
        db_session.execute(
            sa.update(PositionItem).where(PositionItem.id == a.id).values(chapter_item_id=b.id)
        )

        with pytest.raises(RoutingError):
            assign_section_chains(db_session, [estimate.id])


# ---------------------------------------------------------------------------
#  Шаг импорта
# ---------------------------------------------------------------------------

def _payload(contract, *rows):
    return payload_for(contract, list(rows))


class TestImportStep:
    def test_import_creates_chain_and_assigns_rows(self, job_env):  # noqa: F811
        job = job_env.run(
            _payload(
                job_env.contract,
                position(job_title="Подземная часть", is_chapter=True, chapter_number="1"),
                position(job_title="Устройство стяжки", unit="м2", unit_cost_total="100", number="2"),
            )
        )

        assert job.status == ImportJobStatus.done.value
        job_env.db.expire_all()
        row = job_env.db.execute(
            sa.select(PositionItem).where(PositionItem.is_chapter.is_(False))
        ).scalar_one()
        assert row.section_chain_id is not None
        chain = job_env.db.get(SectionChain, row.section_chain_id)
        assert (chain.zone, chain.zone_source) == ("underground", "rule")

    def test_import_queues_a_zone_job_for_a_chain_the_rule_does_not_decide(self, job_env):  # noqa: F811
        job_env.run(
            _payload(
                job_env.contract,
                position(job_title="Раздел без шаблонов", is_chapter=True, chapter_number="1"),
                position(job_title="Устройство стяжки", unit="м2", unit_cost_total="100", number="2"),
            )
        )

        (zone_job,) = _zone_jobs(job_env.db)
        assert zone_job.status == SemanticJobStatus.pending.value

    def test_step_failure_rolls_back_the_whole_import(self, job_env, monkeypatch):  # noqa: F811
        from services import import_pipeline as pipeline_module

        real = pipeline_module.assign_section_chains

        def boom(db, estimate_ids, **kwargs):
            real(db, estimate_ids, **kwargs)
            assert _chain_count(db) > 0, "обёртка вызвана раньше, чем цепочки появились"
            raise RoutingError("инъекция отказа шага цепочек")

        monkeypatch.setattr(pipeline_module, "assign_section_chains", boom)

        job = job_env.run(
            _payload(
                job_env.contract,
                position(job_title="Раздел без шаблонов", is_chapter=True, chapter_number="1"),
                position(job_title="Устройство стяжки", unit="м2", unit_cost_total="100", number="2"),
            )
        )

        assert job.status == ImportJobStatus.error.value
        assert job_env.estimates() == []
        job_env.db.expire_all()
        assert _chain_count(job_env.db) == 0
        assert _zone_jobs(job_env.db) == []

    def test_step_runs_after_matching_and_before_routing(self, job_env, monkeypatch):  # noqa: F811
        from services import import_pipeline as pipeline_module

        seen = {}
        real = pipeline_module.assign_section_chains

        def spy(db, estimate_ids, **kwargs):
            seen["unmatched"] = db.execute(
                sa.select(sa.func.count()).select_from(PositionItem).where(
                    PositionItem.is_chapter.is_(False), PositionItem.catalog_position_id.is_(None)
                )
            ).scalar_one()
            seen["members"] = db.execute(sa.text("SELECT count(*) FROM context_members")).scalar_one()
            return real(db, estimate_ids, **kwargs)

        monkeypatch.setattr(pipeline_module, "assign_section_chains", spy)

        job_env.run(
            _payload(
                job_env.contract,
                position(job_title="Раздел 1", is_chapter=True, chapter_number="1"),
                position(job_title="Устройство стяжки", unit="м2", unit_cost_total="100", number="2"),
            )
        )

        assert seen == {"unmatched": 0, "members": 0}

    def test_import_counters_stay_the_five_known_ones(self, job_env):  # noqa: F811
        job = job_env.run(
            _payload(
                job_env.contract,
                position(job_title="Раздел без шаблонов", is_chapter=True, chapter_number="1"),
                position(job_title="Устройство стяжки", unit="м2", unit_cost_total="100", number="2"),
            )
        )

        assert (job.positions_total, job.to_review) == (1, 1)
        assert job.matched_cache == job.matched_exact == job.matched_nonposition == 0


# ---------------------------------------------------------------------------
#  backfill_chains
# ---------------------------------------------------------------------------

class TestBackfill:
    def test_assigns_rows_without_chain_and_applies_rule(self, db_session, factories):
        _, _, _, under = _scene(factories, ["Подземная часть"])
        _, _, _, plain = _scene(factories, ["Раздел без шаблонов"])

        report = backfill_chains(db_session, enqueue=False, reapply_rule=False)

        assert report.chains_created == 2
        assert report.rows_assigned == 2
        assert report.rule_set == 1
        assert _chain_of(db_session, under[0]).zone == "underground"
        assert _chain_of(db_session, plain[0]).zone is None
        assert _zone_jobs(db_session) == []

    def test_second_run_creates_and_enqueues_nothing(self, db_session, factories):
        _scene(factories, ["Раздел без шаблонов"])
        first = backfill_chains(db_session, enqueue=True, reapply_rule=False)

        second = backfill_chains(db_session, enqueue=True, reapply_rule=True)

        assert (first.chains_created, first.enqueued) == (1, 1)
        assert (second.chains_created, second.rows_assigned, second.rule_set, second.rule_changed,
                second.enqueued) == (0, 0, 0, 0, 0)
        assert len(_zone_jobs(db_session)) == 1

    def test_batches_cover_all_rows(self, db_session, factories):
        estimate, proposal = _estimate(factories)
        chapter = _chapters(factories, proposal, ["Раздел без шаблонов"])
        items = [_row(factories, proposal, f"Работа {i}", chapter) for i in range(7)]

        report = backfill_chains(db_session, enqueue=False, reapply_rule=False, batch=3)

        assert report.rows_assigned == 7
        assert all(_chain_of(db_session, item) is not None for item in items)

    def test_enqueue_covers_chains_without_zone_proposal_and_job(self, db_session, factories):
        _scene(factories, ["Раздел без шаблонов"])
        _scene(factories, ["Подземная часть"])
        proposed = SectionChain(
            chain_key="с предложением", titles=["С предложением"],
            proposed_zone="whole", proposed_confidence=Decimal("0.5"), proposed_reason="r",
        )
        db_session.add(proposed)
        db_session.flush()

        report = backfill_chains(db_session, enqueue=True, reapply_rule=False)

        jobs = _zone_jobs(db_session)
        assert report.enqueued == 1 and len(jobs) == 1
        assert jobs[0].section_chain_id != proposed.id

    def test_without_enqueue_flag_no_jobs_are_queued(self, db_session, factories):
        _scene(factories, ["Раздел без шаблонов"])

        report = backfill_chains(db_session, enqueue=False, reapply_rule=False)

        assert report.enqueued == 0
        assert _zone_jobs(db_session) == []

    def test_estimate_is_reported_before_jobs_are_queued(self, db_session, factories):
        _scene(factories, ["Раздел без шаблонов"])
        _scene(factories, ["Другой раздел без шаблонов"])
        seen = []

        def before(count, estimate):
            seen.append((count, estimate, len(_zone_jobs(db_session))))

        report = backfill_chains(
            db_session, enqueue=True, reapply_rule=False, before_enqueue=before
        )

        assert len(seen) == 1
        count, estimate, jobs_at_that_moment = seen[0]
        assert (count, jobs_at_that_moment) == (2, 0)
        assert estimate > 0
        assert report.enqueue_estimate_usd == estimate

    def test_reapply_rule_recomputes_only_rule_chains(self, db_session, factories, monkeypatch):
        user = factories.UserFactory.create()
        _, _, _, rule_items = _scene(factories, ["Подземная часть"])
        backfill_chains(db_session, enqueue=False, reapply_rule=False)
        model = SectionChain(
            chain_key="модель", titles=["Модель"], zone="whole", zone_source="model"
        )
        human = SectionChain(
            chain_key="человек", titles=["Человек"], zone="whole", zone_source="human",
            decided_by=user.id, decided_at=sa.func.now(),
        )
        db_session.add_all([model, human])
        db_session.flush()
        # Шаблоны в коде «поправили»: теперь правило отвечает по-другому на всё.
        monkeypatch.setattr(chains_crud, "rule_zone", lambda titles: "territory")

        report = backfill_chains(db_session, enqueue=False, reapply_rule=True)

        db_session.refresh(model)
        db_session.refresh(human)
        assert _chain_of(db_session, rule_items[0]).zone == "territory"
        assert _chain_of(db_session, rule_items[0]).zone_source == "rule"
        assert (model.zone, model.zone_source) == ("whole", "model")
        assert (human.zone, human.zone_source) == ("whole", "human")
        assert report.rule_changed == 1

    def test_reapply_rule_clears_zone_when_rule_no_longer_answers(
        self, db_session, factories, monkeypatch
    ):
        _, _, _, items = _scene(factories, ["Подземная часть"])
        backfill_chains(db_session, enqueue=False, reapply_rule=False)
        monkeypatch.setattr(chains_crud, "rule_zone", lambda titles: None)

        report = backfill_chains(db_session, enqueue=True, reapply_rule=True)

        chain = _chain_of(db_session, items[0])
        assert (chain.zone, chain.zone_source) == (None, None)
        assert report.rule_changed == 1
        assert report.enqueued == 1

    def test_reapply_rule_sets_rule_zone_over_a_pending_model_proposal(
        self, db_session, factories, monkeypatch
    ):
        # Стенд 09.11.2026: правило не знало «котлован», модель предложила «подземная»
        # ниже порога, цепочка ждала человека. После правки шаблонов `--reapply-rule`
        # ставит зону правилом и снимает предложение; цепочку без ответа правила и
        # решения человека и модели не трогает.
        user = factories.UserFactory.create()
        _, _, _, items = _scene(factories, ["Устройство котлована", "Земляные работы"])
        _, _, _, silent_items = _scene(factories, ["Общестроительные работы"])
        monkeypatch.setattr(chains_crud, "rule_zone", lambda titles: None)
        backfill_chains(db_session, enqueue=False, reapply_rule=False)
        for item, zone in ((items[0], "underground"), (silent_items[0], "aboveground")):
            chain = _chain_of(db_session, item)
            chain.proposed_zone, chain.proposed_confidence, chain.proposed_reason = zone, 0.85, "ниже 0.000"
            db_session.flush()  # `_chain_of` сбрасывает сессию: несохранённое предложение пропало бы
        human = SectionChain(
            chain_key="котлован человек", titles=["Устройство котлована"], zone="whole",
            zone_source="human", decided_by=user.id, decided_at=sa.func.now(),
        )
        model = SectionChain(
            chain_key="котлован модель", titles=["Устройство котлована"], zone="territory",
            zone_source="model",
        )
        db_session.add_all([human, model])
        db_session.flush()
        monkeypatch.undo()

        report = backfill_chains(db_session, enqueue=False, reapply_rule=True)

        chain = _chain_of(db_session, items[0])
        assert (chain.zone, chain.zone_source) == ("underground", "rule")
        assert (chain.proposed_zone, chain.proposed_confidence, chain.proposed_reason) == (None, None, None)
        silent = _chain_of(db_session, silent_items[0])
        assert (silent.zone, silent.proposed_zone) == (None, "aboveground")
        db_session.refresh(human)
        db_session.refresh(model)
        assert (human.zone, human.zone_source) == ("whole", "human")
        assert (model.zone, model.zone_source) == ("territory", "model")
        assert report.rule_changed == 1

    def test_without_reapply_flag_rule_chains_stay(self, db_session, factories, monkeypatch):
        _, _, _, items = _scene(factories, ["Подземная часть"])
        backfill_chains(db_session, enqueue=False, reapply_rule=False)
        monkeypatch.setattr(chains_crud, "rule_zone", lambda titles: "territory")

        backfill_chains(db_session, enqueue=False, reapply_rule=False)

        assert _chain_of(db_session, items[0]).zone == "underground"

    def test_rule_is_applied_to_existing_chains_without_zone_and_proposal(
        self, db_session, factories
    ):
        _, _, _, items = _scene(factories, ["Подземная часть"])
        backfill_chains(db_session, enqueue=False, reapply_rule=False)
        chain = _chain_of(db_session, items[0])
        chain.zone = None
        chain.zone_source = None
        db_session.flush()

        report = backfill_chains(db_session, enqueue=False, reapply_rule=False)

        assert report.rule_set == 1
        assert _chain_of(db_session, items[0]).zone == "underground"

    def test_rule_is_not_applied_to_a_chain_with_a_model_proposal(self, db_session, factories):
        proposed = SectionChain(
            chain_key=chain_key(["Подземная часть"]), titles=["Подземная часть"],
            proposed_zone="unknown", proposed_confidence=Decimal("0.4"), proposed_reason="r",
        )
        db_session.add(proposed)
        db_session.flush()

        report = backfill_chains(db_session, enqueue=False, reapply_rule=False)

        db_session.refresh(proposed)
        assert proposed.zone is None
        assert report.rule_set == 0


# ---------------------------------------------------------------------------
#  list_chains
# ---------------------------------------------------------------------------

def _chain(db, key, *, titles=None, **fields) -> SectionChain:
    row = SectionChain(chain_key=key, titles=titles or [key], **fields)
    db.add(row)
    db.flush()
    return row


class TestListChains:
    def _seed(self, db, factories):
        user = factories.UserFactory.create()
        rule = _chain(db, "правило", zone="underground", zone_source="rule")
        model = _chain(db, "модель", zone="whole", zone_source="model")
        human = _chain(
            db, "человек", zone="territory", zone_source="human", decided_by=user.id,
            decided_at=sa.func.now(),
        )
        disputed = _chain(
            db, "спорная", proposed_zone="whole", proposed_confidence=Decimal("0.6"),
            proposed_reason="Причина",
        )
        unknown = _chain(
            db, "не знаю", proposed_zone="unknown", proposed_confidence=Decimal("0.9"),
            proposed_reason="Не ясно",
        )
        undetermined = _chain(db, "не определена")
        return dict(rule=rule, model=model, human=human, disputed=disputed, unknown=unknown,
                    undetermined=undetermined)

    def _ids(self, result):
        return [item["id"] for item in result["items"]]

    @pytest.mark.parametrize(
        ("flt", "expected"),
        [
            ("disputed", {"disputed", "unknown"}),
            ("undetermined", {"undetermined"}),
            ("rule", {"rule"}),
            ("model", {"model"}),
            ("human", {"human"}),
            ("all", {"rule", "model", "human", "disputed", "unknown", "undetermined"}),
        ],
    )
    def test_filters(self, db_session, factories, flt, expected):
        rows = self._seed(db_session, factories)

        result = list_chains(db_session, filter=flt, q=None, page=1)

        assert set(self._ids(result)) == {rows[name].id for name in expected}
        assert result["total"] == len(expected)

    def test_item_fields(self, db_session, factories):
        rows = self._seed(db_session, factories)

        result = list_chains(db_session, filter="disputed", q=None, page=1)

        item = next(i for i in result["items"] if i["id"] == rows["disputed"].id)
        assert item["titles"] == ["спорная"]
        assert item["zone"] is None and item["zone_source"] is None
        assert item["proposed_zone"] == "whole"
        assert item["proposed_confidence"] == "0.6"
        assert item["proposed_reason"] == "Причина"
        assert set(item) == {
            "id", "titles", "zone", "zone_source", "proposed_zone", "proposed_confidence",
            "proposed_reason", "job_status", "rows_kp", "rows_contracts", "sample_titles",
        }

    def test_search_by_chain_key_is_normalized_and_literal(self, db_session, factories):
        _chain(db_session, "подземная часть / кровля")
        _chain(db_session, "подземная часть / 100% бетон")
        _chain(db_session, "надземная часть")

        by_word = list_chains(db_session, filter="all", q="  ПОДЗЕМНАЯ  часть", page=1)
        by_percent = list_chains(db_session, filter="all", q="100%", page=1)
        by_wildcard = list_chains(db_session, filter="all", q="_", page=1)

        assert by_word["total"] == 2
        assert by_percent["total"] == 1
        assert by_wildcard["total"] == 0

    def test_pages_split_the_result_and_total_is_whole(self, db_session, factories):
        for i in range(55):
            _chain(db_session, f"цепочка {i:03d}")

        first = list_chains(db_session, filter="all", q=None, page=1)
        second = list_chains(db_session, filter="all", q=None, page=2)

        assert first["total"] == second["total"] == 55
        assert len(first["items"]) == 50 and len(second["items"]) == 5
        assert set(self._ids(first)).isdisjoint(self._ids(second))

    def test_rows_split_between_kp_and_contracts(self, db_session, factories):
        _scene(factories, ["Раздел без шаблонов"], "Р1", "Р2", kp=True)
        _scene(factories, ["Раздел без шаблонов"], "Р3")
        backfill_chains(db_session, enqueue=False, reapply_rule=False)

        result = list_chains(db_session, filter="all", q=None, page=1)

        (item,) = result["items"]
        assert (item["rows_kp"], item["rows_contracts"]) == (2, 1)

    def test_job_status_is_the_latest_zone_job(self, db_session, factories):
        _scene(factories, ["Раздел без шаблонов"])
        backfill_chains(db_session, enqueue=True, reapply_rule=False)
        (job,) = _zone_jobs(db_session)
        job.status = SemanticJobStatus.error.value
        db_session.flush()

        (item,) = list_chains(db_session, filter="all", q=None, page=1)["items"]

        assert item["job_status"] == "error"

    def test_chain_without_job_has_null_job_status(self, db_session, factories):
        _chain(db_session, "без задания")

        (item,) = list_chains(db_session, filter="all", q=None, page=1)["items"]

        assert item["job_status"] is None

    def test_sample_titles_are_the_most_frequent_row_names(self, db_session, factories):
        estimate, proposal = _estimate(factories)
        chapter = _chapters(factories, proposal, ["Раздел без шаблонов"])
        for name in ("Редкая", "Частая", "Частая", "Частая", "Средняя", "Средняя", "Четвёртая"):
            _row(factories, proposal, name, chapter)
        backfill_chains(db_session, enqueue=False, reapply_rule=False)

        (item,) = list_chains(db_session, filter="all", q=None, page=1)["items"]

        assert item["sample_titles"] == ["Частая", "Средняя", "Редкая"]


# ---------------------------------------------------------------------------
#  get_chain и маршрут одной цепочки
# ---------------------------------------------------------------------------

CHAIN_URL = "/api/v1/semantic/section-chains/{}"


class TestGetChain:
    def _rich_chain(self, db, factories) -> SectionChain:
        """Цепочка со спорным предложением, заданием, строками КП и договоров."""
        titles = ["Раздел без шаблонов", "Подраздел"]
        _scene(factories, titles, "Р1", "Р2", kp=True)
        _scene(factories, titles, "Р3")
        backfill_chains(db, enqueue=True, reapply_rule=False)
        (chain,) = db.execute(sa.select(SectionChain)).scalars().all()
        chain.proposed_zone = "underground"
        chain.proposed_confidence = Decimal("0.85")
        chain.proposed_reason = "Причина"
        db.flush()
        return chain

    def test_equals_the_list_item_in_all_fields(self, db_session, factories):
        chain = self._rich_chain(db_session, factories)
        _chain(db_session, "соседняя")

        one = get_chain(db_session, chain.id)

        (listed,) = (
            item for item in list_chains(db_session, filter="all", q=None, page=1)["items"]
            if item["id"] == chain.id
        )
        assert one == listed
        assert (one["rows_kp"], one["rows_contracts"]) == (2, 1)
        assert one["proposed_confidence"] == "0.85"
        assert one["job_status"] is not None and one["sample_titles"]

    def test_unknown_chain_is_404(self, db_session):
        with pytest.raises(DomainError) as caught:
            get_chain(db_session, 987654321)

        assert caught.value.status_code == 404

    def test_route_admin_gets_the_list_item_shape(self, admin_client, db_session, factories):
        chain = self._rich_chain(db_session, factories)
        db_session.commit()

        response = admin_client.get(CHAIN_URL.format(chain.id))

        assert response.status_code == 200
        (listed,) = (
            item for item in admin_client.get(
                "/api/v1/semantic/section-chains", params={"filter": "all"}
            ).json()["items"] if item["id"] == chain.id
        )
        assert response.json() == listed

    def test_route_unknown_chain_is_404(self, admin_client):
        assert admin_client.get(CHAIN_URL.format(987654321)).status_code == 404

    def test_route_member_is_refused(self, member_client, db_session, factories):
        chain = self._rich_chain(db_session, factories)
        db_session.commit()

        assert member_client.get(CHAIN_URL.format(chain.id)).status_code == 403

    def test_route_anonymous_is_refused(self, anon_client, db_session, factories):
        chain = self._rich_chain(db_session, factories)
        db_session.commit()

        assert anon_client.get(CHAIN_URL.format(chain.id)).status_code in (401, 403)


# ---------------------------------------------------------------------------
#  zone_chain_info: блок «Зона места» панели строки
# ---------------------------------------------------------------------------

def _zone_job(db, chain, status="pending") -> SemanticJob:
    """Задание `section_zone` цепочки в заданном статусе (парные поля статуса — как у очереди)."""
    job = SemanticJob(
        kind=SemanticJobKind.section_zone.value,
        section_chain_id=chain.id,
        request_hash=uuid.uuid4().hex,
        status=status,
        claim_token=uuid.uuid4() if status == "running" else None,
        cancel_reason="input_changed" if status == "cancelled" else None,
        privacy_matches=(
            [{"text": "иное имя", "kind": "contractor", "where": "context"}]
            if status == "privacy_hold" else None
        ),
        prompt_version="1",
        model_requested="m",
        place_dictionary_version=1,
        candidates_hash="c",
        prefix_hash="p",
        input_hash="i",
        response_schema_version="1",
        serialization_version="1",
    )
    db.add(job)
    db.flush()
    return job


def _attempt(db, job, *, outcome="ok", raw=None) -> SemanticJobAttempt:
    attempt = SemanticJobAttempt(
        job_id=job.id,
        claim_token=uuid.uuid4(),
        retry_generation=0,
        started_at=dt.datetime.now(dt.UTC),
        finished_at=dt.datetime.now(dt.UTC),
        outcome=outcome,
        raw_response=raw,
        reserve_usd=0,
        prefix_hash="p",
        privacy_dictionary_hash="d",
    )
    db.add(attempt)
    db.flush()
    return attempt


def _answer(zone, confidence, reason) -> str:
    return json.dumps({"zone": zone, "confidence": confidence, "reason": reason}, ensure_ascii=False)


def _budget_pause(db, job, *, until):
    """Пауза провайдера по времени до `until`; парные поля требуют попытку-причину."""
    attempt = _attempt(db, job, outcome="budget_refused")
    db.execute(
        sa.update(SemanticWorkerState)
        .where(SemanticWorkerState.id == 1)
        .values(
            budget_paused_until=until,
            budget_pause_attempt_id=attempt.id,
            budget_pause_source="provider_credits",
        )
    )
    db.flush()


class TestZoneChainInfo:
    FORM = {
        "id", "titles", "zone", "zone_source", "decided_at", "rows_kp", "rows_contracts",
        "model_state", "model_answer", "threshold",
    }

    def _undecided(self, db, key="цепочка", **fields) -> SectionChain:
        return _chain(db, key, titles=["Раздел", "Подраздел"], **fields)

    def test_form_is_exactly_the_spec_shape(self, db_session, factories):
        chain = self._undecided(db_session)

        info = zone_chain_info(db_session, chain.id)

        assert set(info) == self.FORM
        assert info["id"] == chain.id
        assert info["titles"] == ["Раздел", "Подраздел"]
        assert info["threshold"] == "0.95"

    def test_unknown_chain_is_404(self, db_session):
        with pytest.raises(DomainError) as caught:
            zone_chain_info(db_session, 987654321)

        assert caught.value.status_code == 404

    def test_rows_equal_the_queue_element(self, db_session, factories):
        titles = ["Раздел без шаблонов", "Подраздел"]
        _scene(factories, titles, "Р1", "Р2", kp=True)
        _scene(factories, titles, "Р3")
        backfill_chains(db_session, enqueue=False, reapply_rule=False)
        (chain,) = db_session.execute(sa.select(SectionChain)).scalars().all()

        info = zone_chain_info(db_session, chain.id)

        (listed,) = list_chains(db_session, filter="all", q=None, page=1)["items"]
        assert (info["rows_kp"], info["rows_contracts"]) == (2, 1)
        assert (info["rows_kp"], info["rows_contracts"]) == (
            listed["rows_kp"], listed["rows_contracts"]
        )

    def test_chain_without_rows_has_zero_counts(self, db_session, factories):
        chain = self._undecided(db_session)

        info = zone_chain_info(db_session, chain.id)

        assert (info["rows_kp"], info["rows_contracts"]) == (0, 0)

    # -- model_state: по фикстуре на каждое значение, в порядке спеки ---------

    def test_live_job_is_running(self, db_session, factories):
        chain = self._undecided(db_session)
        _zone_job(db_session, chain, "pending")

        info = zone_chain_info(db_session, chain.id)

        assert info["model_state"] == "running"
        assert info["zone"] is None and info["zone_source"] is None
        assert info["model_answer"] is None

    def test_a_job_in_work_is_running_too(self, db_session, factories):
        chain = self._undecided(db_session)
        _zone_job(db_session, chain, "running")

        assert zone_chain_info(db_session, chain.id)["model_state"] == "running"

    def test_a_disputed_proposal_with_a_live_job_is_running_not_proposed(
        self, db_session, factories
    ):
        chain = self._undecided(
            db_session, proposed_zone="whole", proposed_confidence=Decimal("0.6"),
            proposed_reason="Причина",
        )
        _zone_job(db_session, chain, "pending")

        info = zone_chain_info(db_session, chain.id)

        assert info["model_state"] == "running"
        assert info["model_answer"]["zone"] == "whole"

    def test_the_same_disputed_proposal_without_a_job_is_proposed(self, db_session, factories):
        chain = self._undecided(
            db_session, proposed_zone="whole", proposed_confidence=Decimal("0.6"),
            proposed_reason="Причина",
        )

        info = zone_chain_info(db_session, chain.id)

        assert info["model_state"] == "proposed"
        assert info["model_answer"] == {
            "zone": "whole", "confidence": "0.6", "reason": "Причина"
        }

    def test_claim_paused_with_a_live_job_is_stopped(self, db_session, factories):
        chain = self._undecided(db_session)
        _zone_job(db_session, chain, "pending")
        _pause(db_session)

        assert zone_chain_info(db_session, chain.id)["model_state"] == "stopped"

    def test_claim_paused_without_a_live_job_is_not_stopped(self, db_session, factories):
        chain = self._undecided(db_session)
        _pause(db_session)

        assert zone_chain_info(db_session, chain.id)["model_state"] == "none"

    def test_disputed_proposal_with_stopped_claim_and_a_live_job_is_stopped(
        self, db_session, factories
    ):
        chain = self._undecided(
            db_session, proposed_zone="whole", proposed_confidence=Decimal("0.6"),
            proposed_reason="Причина",
        )
        _zone_job(db_session, chain, "pending")
        _pause(db_session)

        assert zone_chain_info(db_session, chain.id)["model_state"] == "stopped"

    def test_budget_pause_in_the_future_with_a_live_job_is_paused(self, db_session, factories):
        chain = self._undecided(db_session)
        job = _zone_job(db_session, chain, "pending")
        _budget_pause(
            db_session, job, until=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
        )

        assert zone_chain_info(db_session, chain.id)["model_state"] == "paused"

    def test_budget_pause_in_the_past_with_a_live_job_is_running(self, db_session, factories):
        chain = self._undecided(db_session)
        job = _zone_job(db_session, chain, "pending")
        _budget_pause(
            db_session, job, until=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
        )

        assert zone_chain_info(db_session, chain.id)["model_state"] == "running"

    def test_stop_and_budget_pause_together_are_stopped(self, db_session, factories):
        chain = self._undecided(db_session)
        job = _zone_job(db_session, chain, "pending")
        _pause(db_session)
        _budget_pause(
            db_session, job, until=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
        )

        assert zone_chain_info(db_session, chain.id)["model_state"] == "stopped"

    def test_budget_pause_without_a_live_job_is_not_paused(self, db_session, factories):
        chain = self._undecided(db_session)
        job = _zone_job(db_session, chain, "done")
        _budget_pause(
            db_session, job, until=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
        )

        assert zone_chain_info(db_session, chain.id)["model_state"] == "none"

    def test_unknown_answer_keeps_a_high_confidence_as_a_string(self, db_session, factories):
        chain = self._undecided(
            db_session, proposed_zone="unknown", proposed_confidence=Decimal("0.97"),
            proposed_reason="Не ясно",
        )

        info = zone_chain_info(db_session, chain.id)

        assert info["model_state"] == "unknown"
        assert info["model_answer"] == {
            "zone": "unknown", "confidence": "0.97", "reason": "Не ясно"
        }

    @pytest.mark.parametrize("status", ["error", "privacy_hold"])
    def test_a_stuck_job_without_a_proposal_is_failed(self, db_session, factories, status):
        chain = self._undecided(db_session)
        _zone_job(db_session, chain, status)

        info = zone_chain_info(db_session, chain.id)

        assert info["model_state"] == "failed"
        assert info["model_answer"] is None

    def test_a_proposal_wins_over_a_stuck_job(self, db_session, factories):
        chain = self._undecided(
            db_session, proposed_zone="whole", proposed_confidence=Decimal("0.6"),
            proposed_reason="Причина",
        )
        _zone_job(db_session, chain, "error")

        assert zone_chain_info(db_session, chain.id)["model_state"] == "proposed"

    @pytest.mark.parametrize("status", [None, "done", "cancelled"])
    def test_nothing_live_and_nothing_proposed_is_none(self, db_session, factories, status):
        chain = self._undecided(db_session)
        if status is not None:
            _zone_job(db_session, chain, status)

        info = zone_chain_info(db_session, chain.id)

        assert info["model_state"] == "none"
        assert info["model_answer"] is None

    def test_a_job_of_another_chain_does_not_count(self, db_session, factories):
        chain = self._undecided(db_session)
        other = self._undecided(db_session, key="другая")
        _zone_job(db_session, other, "pending")

        assert zone_chain_info(db_session, chain.id)["model_state"] == "none"

    # -- цепочка с зоной ---------------------------------------------------------

    def _human(self, db, factories, key="человек", **fields) -> SectionChain:
        user = factories.UserFactory.create()
        return _chain(
            db, key, titles=["Раздел"], zone="underground", zone_source="human",
            decided_by=user.id, decided_at=dt.datetime(2026, 11, 19, 1, 24, tzinfo=dt.UTC),
            **fields,
        )

    def test_human_chain_has_no_model_state_and_a_decision_moment(self, db_session, factories):
        chain = self._human(db_session, factories)
        _zone_job(db_session, chain, "pending")

        info = zone_chain_info(db_session, chain.id)

        assert info["zone"] == "underground" and info["zone_source"] == "human"
        assert info["model_state"] is None
        assert info["decided_at"] == iso(chain.decided_at)
        assert info["decided_at"] is not None

    def test_model_answer_is_the_last_ok_attempt_by_id(self, db_session, factories):
        chain = self._human(db_session, factories)
        job = _zone_job(db_session, chain, "done")
        _attempt(db_session, job, raw=_answer("aboveground", 0.4, "Первая"))
        _attempt(db_session, job, raw=_answer("underground", 0.85, "Вторая"))
        _attempt(db_session, job, outcome="schema_error", raw=_answer("whole", 0.99, "Не ok"))

        info = zone_chain_info(db_session, chain.id)

        assert info["model_answer"] == {
            "zone": "underground", "confidence": "0.85", "reason": "Вторая"
        }

    def test_model_answer_spans_all_jobs_of_the_chain(self, db_session, factories):
        chain = self._human(db_session, factories)
        other = self._human(db_session, factories, key="соседняя")
        old = _zone_job(db_session, chain, "done")
        _attempt(db_session, old, raw=_answer("whole", 0.7, "Старое задание"))
        _attempt(db_session, _zone_job(db_session, other, "done"), raw=_answer("territory", 0.9, "Чужое"))

        info = zone_chain_info(db_session, chain.id)

        assert info["model_answer"]["reason"] == "Старое задание"

    def test_model_may_say_unknown_on_a_decided_chain(self, db_session, factories):
        chain = self._human(db_session, factories)
        job = _zone_job(db_session, chain, "done")
        _attempt(db_session, job, raw=_answer("unknown", 0.97, "Не ясно"))

        info = zone_chain_info(db_session, chain.id)

        assert info["model_answer"] == {
            "zone": "unknown", "confidence": "0.97", "reason": "Не ясно"
        }

    @pytest.mark.parametrize("raw", ["не json", _answer("nowhere", 0.5, "Причина"), None])
    def test_an_unparsable_last_answer_is_null_not_an_error(self, db_session, factories, raw):
        chain = self._human(db_session, factories)
        job = _zone_job(db_session, chain, "done")
        _attempt(db_session, job, raw=_answer("whole", 0.9, "Прежняя, разбирается"))
        _attempt(db_session, job, raw=raw)

        info = zone_chain_info(db_session, chain.id)

        assert info["model_answer"] is None

    def test_decided_chain_without_attempts_has_no_answer(self, db_session, factories):
        chain = self._human(db_session, factories)
        _zone_job(db_session, chain, "done")

        assert zone_chain_info(db_session, chain.id)["model_answer"] is None

    def test_rule_chain_without_jobs_has_nothing_from_the_model(self, db_session, factories):
        chain = _chain(
            db_session, "правило", titles=["Раздел"], zone="whole", zone_source="rule"
        )

        info = zone_chain_info(db_session, chain.id)

        assert info["zone_source"] == "rule"
        assert info["model_answer"] is None and info["model_state"] is None
        assert info["decided_at"] is None

    def test_model_chain_shows_the_answer_that_set_the_zone(self, db_session, factories):
        chain = _chain(
            db_session, "модель", titles=["Раздел"], zone="whole", zone_source="model"
        )
        job = _zone_job(db_session, chain, "done")
        _attempt(db_session, job, raw=_answer("whole", 0.97, "Причина"))

        info = zone_chain_info(db_session, chain.id)

        assert info["zone_source"] == "model" and info["decided_at"] is None
        assert info["model_answer"] == {"zone": "whole", "confidence": "0.97", "reason": "Причина"}


# ---------------------------------------------------------------------------
#  decide_zone
# ---------------------------------------------------------------------------

class TestDecideZone:
    def test_human_decision_sets_zone_source_author_and_clears_proposal(
        self, db_session, factories
    ):
        user = factories.UserFactory.create()
        chain = _chain(
            db_session, "спорная", proposed_zone="whole", proposed_confidence=Decimal("0.6"),
            proposed_reason="Причина",
        )

        decide_zone(db_session, chain.id, "territory", actor_id=user.id)

        db_session.refresh(chain)
        assert (chain.zone, chain.zone_source) == ("territory", "human")
        assert chain.decided_by == user.id and chain.decided_at is not None
        assert (chain.proposed_zone, chain.proposed_confidence, chain.proposed_reason) == (
            None, None, None,
        )

    @pytest.mark.parametrize(
        "fields",
        [
            dict(zone="underground", zone_source="rule"),
            dict(zone="aboveground", zone_source="model"),
        ],
    )
    def test_human_decision_overrides_rule_and_model(self, db_session, factories, fields):
        user = factories.UserFactory.create()
        chain = _chain(db_session, "ключ", **fields)

        decide_zone(db_session, chain.id, "whole", actor_id=user.id)

        db_session.refresh(chain)
        assert (chain.zone, chain.zone_source) == ("whole", "human")

    def test_a_later_human_decision_replaces_the_earlier_one(self, db_session, factories):
        first = factories.UserFactory.create()
        second = factories.UserFactory.create()
        chain = _chain(db_session, "ключ")
        decide_zone(db_session, chain.id, "whole", actor_id=first.id)

        decide_zone(db_session, chain.id, "underground", actor_id=second.id)

        db_session.refresh(chain)
        assert (chain.zone, chain.decided_by) == ("underground", second.id)

    @pytest.mark.parametrize("zone", ["unknown", "basement", "", "UNDERGROUND"])
    def test_invalid_zone_is_422_invalid_zone_and_changes_nothing(
        self, db_session, factories, zone
    ):
        user = factories.UserFactory.create()
        chain = _chain(db_session, "ключ")

        with pytest.raises(DomainError) as caught:
            decide_zone(db_session, chain.id, zone, actor_id=user.id)

        assert (caught.value.status_code, caught.value.code) == (422, "invalid_zone")
        db_session.refresh(chain)
        assert chain.zone is None and chain.zone_source is None

    def test_unknown_chain_is_404(self, db_session, factories):
        user = factories.UserFactory.create()

        with pytest.raises(DomainError) as caught:
            decide_zone(db_session, 987654321, "whole", actor_id=user.id)

        assert caught.value.status_code == 404


# ---------------------------------------------------------------------------
#  Массовое принятие зон
# ---------------------------------------------------------------------------

ACCEPT_ALL_URL = "/api/v1/semantic/section-chains/accept-all"


class TestAcceptAllZones:
    def _seed(self, db, factories):
        user = factories.UserFactory.create()
        return dict(
            concrete=_chain(db, "а", proposed_zone="underground", proposed_confidence=Decimal("0.6")),
            other=_chain(db, "б", proposed_zone="whole", proposed_confidence=Decimal("0.7")),
            unknown=_chain(db, "в", proposed_zone="unknown", proposed_confidence=Decimal("0.5")),
            human=_chain(
                db, "г", zone="territory", zone_source="human", decided_by=user.id,
                decided_at=dt.datetime.now(dt.UTC),
            ),
            model=_chain(db, "д", zone="whole", zone_source="model"),
            rule=_chain(db, "е", zone="aboveground", zone_source="rule"),
            empty=_chain(db, "ж"),
        )

    def test_accepts_only_concrete_proposals_as_human_decisions(self, admin_client, db_session, factories):
        chains = self._seed(db_session, factories)
        db_session.commit()

        response = admin_client.post(ACCEPT_ALL_URL)

        assert response.status_code == 200
        assert response.json() == {"accepted": 2, "skipped": 0}
        db_session.expire_all()
        for key, zone in (("concrete", "underground"), ("other", "whole")):
            chain = db_session.get(SectionChain, chains[key].id)
            assert (chain.zone, chain.zone_source) == (zone, "human")
            assert chain.decided_by is not None and chain.decided_at is not None
            assert chain.proposed_zone is None and chain.proposed_confidence is None
        unknown = db_session.get(SectionChain, chains["unknown"].id)
        assert (unknown.zone, unknown.proposed_zone) == (None, "unknown")
        for key, zone, source in (
            ("human", "territory", "human"), ("model", "whole", "model"),
            ("rule", "aboveground", "rule"), ("empty", None, None),
        ):
            chain = db_session.get(SectionChain, chains[key].id)
            assert (chain.zone, chain.zone_source) == (zone, source)

    def test_list_reports_acceptable_total_without_unknown(self, admin_client, db_session, factories):
        self._seed(db_session, factories)
        db_session.commit()

        body = admin_client.get("/api/v1/semantic/section-chains").json()

        assert body["acceptable_total"] == 2
        assert body["total"] == 3  # спорные: две конкретные и «не знаю»

    def test_a_chain_decided_in_parallel_is_skipped(self, db_session, factories, monkeypatch):
        chains = self._seed(db_session, factories)
        db_session.commit()
        user = factories.UserFactory.create()
        monkeypatch.setattr(chains_crud, "ACCEPT_ALL_BATCH", 1)
        real = chains_crud.decide_zone
        calls = []

        def decide_and_race(db, chain_id, zone, *, actor_id):
            real(db, chain_id, zone, actor_id=actor_id)
            if not calls:  # параллельный человек решил вторую цепочку после выборки id
                db.execute(
                    sa.update(SectionChain).where(SectionChain.id == chains["other"].id)
                    .values(
                        zone="territory", zone_source="human", proposed_zone=None,
                        proposed_confidence=None,
                        decided_by=user.id, decided_at=dt.datetime.now(dt.UTC),
                    )
                )
            calls.append(chain_id)

        monkeypatch.setattr(chains_crud, "decide_zone", decide_and_race)

        report = chains_crud.accept_all_zones(db_session, actor_id=user.id)

        assert report == {"accepted": 1, "skipped": 1}
        db_session.expire_all()
        assert db_session.get(SectionChain, chains["other"].id).zone == "territory"

    def test_member_is_refused(self, member_client, db_session, factories):
        chains = self._seed(db_session, factories)
        db_session.commit()

        assert member_client.post(ACCEPT_ALL_URL).status_code == 403
        db_session.expire_all()
        assert db_session.get(SectionChain, chains["concrete"].id).zone is None

    def test_empty_queue(self, admin_client):
        assert admin_client.post(ACCEPT_ALL_URL).json() == {"accepted": 0, "skipped": 0}
