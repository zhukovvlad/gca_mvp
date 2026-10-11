"""Открытие порциями на базе (спека 3б §2.3.1): собранный из порций ответ
проходит прежний `apply_discovery` без нарушения ограничений.

Цикл порций опросчика (вызовы, запись попытки, сбой порции) — в
`test_catalog_discovery_worker.py`; здесь порции разбираются и собираются теми же
функциями, что вызывает опросчик, на настоящих охвате и теле."""
# ruff: noqa: F811 — `world` — фикстура, импортированная из соседнего набора
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest
import sqlalchemy as sa

import services.semantic_worker as worker
from config import settings as app_settings
from models import FamilyCategoryProposal, FamilyDraft, FamilyDraftMember, SemanticJob
from services.discovery_result import DiscoveryOutcome, apply_discovery
from services.family_discovery import (
    chunk_bounds,
    chunk_sent_of,
    discovery_scope,
    draft_refs_of,
    launch_discovery,
    preview_discovery,
    render_discovery_chunk,
)
from services.variant_answer import merge_chunks, parse_discovery_chunk
from tests.factories import seed_category_id
from tests.integration.test_catalog_discovery_scope import (
    _ctx_bare_unit,
    _make_system,
    _uncategorize,
    world,  # noqa: F401 — фикстура
)
from tests.integration.test_catalog_discovery_worker import (
    _answer,
    _attempt,
    _bare_world,
    _claim,
    _group,
    _launch,
    _response,
    _SameSession,
    _ScriptedClient,
)
from tests.integration.test_semantic_queue_api import _active_family

pytestmark = pytest.mark.integration

NOW = dt.datetime(2099, 1, 1, 12, 0, tzinfo=dt.UTC)

#: Порция в три имени: восемь имён — порции 1–3, 4–6, 7–8.
S = app_settings.model_copy(
    update={
        "SEMANTIC_DISCOVERY_CHUNK_NAMES": 3,
        "SEMANTIC_DISCOVERY_MAX_DRAFTS": 150,
        "SEMANTIC_AUTO_ACCEPT_THRESHOLD": None,
        "SEMANTIC_SYSTEM_AUTO_ACCEPT_THRESHOLD": None,
        "SEMANTIC_DAILY_BUDGET_USD": Decimal("30"),
    }
)


#: Размер порции по умолчанию (100): 250 имён — три порции.
W_S = S.model_copy(update={"SEMANTIC_DISCOVERY_CHUNK_NAMES": 100})


def _new(names, *, title, category_id, definition="Определение"):
    return {
        "family_id": None, "draft_ref": None, "title": title, "definition": definition,
        "category_id": category_id, "similar_family_id": None, "names": names,
    }


def _existing(family_id, names):
    return {
        "family_id": family_id, "draft_ref": None, "title": None, "definition": None,
        "category_id": None, "similar_family_id": None, "names": names,
    }


def _ref(draft_ref, names):
    return {
        "family_id": None, "draft_ref": draft_ref, "title": None, "definition": None,
        "category_id": None, "similar_family_id": None, "names": names,
    }


def _raw(groups, not_work=(), family_categories=()):
    return json.dumps(
        {
            "groups": groups,
            "not_work": list(not_work),
            "family_categories": [
                {"family_id": f, "category_id": c} for f, c in family_categories
            ],
        },
        ensure_ascii=False,
    )


def _drafts(db, job_id):
    db.expire_all()
    return list(
        db.execute(
            sa.select(FamilyDraft).where(FamilyDraft.job_id == job_id).order_by(FamilyDraft.ordinal)
        ).scalars()
    )


def _members(db, draft_id) -> list[int]:
    return sorted(
        db.execute(
            sa.select(FamilyDraftMember.context_id).where(FamilyDraftMember.draft_id == draft_id)
        ).scalars()
    )


class TestAssembledAnswerPassesApplyDiscovery:
    def test_three_chunks_become_the_drafts_of_the_answer(self, world):
        category = seed_category_id(world.db)
        family = _active_family(
            world.db, title="Семья кладки", unit_name="M3", actor_id=world.admin.id
        )
        _uncategorize(world.db, family.id)
        # Единица с активной семьёй: контексты остаются в охвате как системы.
        contexts = [_ctx_bare_unit(world, f"Имя {n}") for n in range(1, 9)]
        for context_id in contexts:
            _make_system(world.db, context_id, world.admin.id)
        preview = preview_discovery(world.db, unit_id=world.bare_unit, settings=S)
        job = launch_discovery(
            world.db, unit_id=world.bare_unit, preview_hash=preview.preview_hash,
            actor_id=world.admin.id, settings=S,
        )
        claim = worker.claim_next(world.db, settings=S, now=NOW)
        assert claim is not None, "вход теста: задание должно быть захвачено"
        scope = discovery_scope(world.db, world.bare_unit)
        assert len(scope.names) == 8, "вход теста: восемь имён в охвате"
        bounds = chunk_bounds(len(scope.names), S.SEMANTIC_DISCOVERY_CHUNK_NAMES)
        assert bounds == [(1, 3), (4, 6), (7, 8)]
        long_title = "Ж" * 150
        raws = [
            # порция 1: новый черновик, имена активной семьи, «не работа», категория
            _raw(
                [_new([1], title="Кладка", category_id=category), _existing(family.id, [2])],
                not_work=[3], family_categories=[(family.id, category)],
            ),
            # порция 2: продолжение черновика 1 по ссылке, одноимённая новая группа
            # (дописывается в тот же черновик), снова активная семья
            _raw([_ref(1, [4]), _new([5], title="КЛАДКА", category_id=category),
                  _existing(family.id, [6])]),
            # порция 3: длинное имя (режется), семья не из отправленных (остаток)
            _raw([_new([7], title=long_title, category_id=category),
                  _existing(987654, [8])]),
        ]

        chunks = []
        for number, (a, b) in enumerate(bounds):
            drafts = draft_refs_of(merge_chunks(chunks, len(scope.names), max_drafts=150))
            sent = chunk_sent_of(scope, bounds=(a, b), drafts=drafts, first=number == 0)
            body = render_discovery_chunk(
                claim.rendered, scope, settings=S, bounds=(a, b), drafts=drafts,
                first=number == 0,
            )
            assert f"ИМЕНА {a}–{b} ИЗ 8:" in body["messages"][1]["content"]
            chunks.append(parse_discovery_chunk(raws[number], sent))
        answer = merge_chunks(chunks, len(scope.names), max_drafts=150)

        outcome = apply_discovery(
            world.db, job_id=job.id, claim_token=claim.claim_token, answer=answer, settings=S
        )

        assert outcome == DiscoveryOutcome(
            applied=True, unapplied_reason=None, drafts_created=4, superseded_drafts=0,
            unassigned=1,
        )
        stored = world.db.get(SemanticJob, job.id)
        assert stored.status == "done"
        kladka, existing, painting, trash = _drafts(world.db, job.id)
        assert [(d.grp, d.ordinal) for d in (kladka, existing, painting, trash)] == [
            ("new", 1), ("existing", 2), ("new", 3), ("not_work", 4),
        ]
        assert kladka.title == "Кладка" and kladka.family_category_id == category
        assert _members(world.db, kladka.id) == sorted([contexts[0], contexts[3], contexts[4]])
        assert existing.existing_family_id == family.id
        assert _members(world.db, existing.id) == sorted([contexts[1], contexts[5]])
        assert painting.title == "Ж" * 99 + "…"
        assert _members(world.db, painting.id) == [contexts[6]]
        assert _members(world.db, trash.id) == [contexts[2]]
        # Имя 8 — ссылка на семью не из отправленных: в остатке, ни в одной группе.
        placed = {
            c for d in (kladka, existing, painting, trash) for c in _members(world.db, d.id)
        }
        assert contexts[7] not in placed
        (proposal,) = world.db.execute(sa.select(FamilyCategoryProposal)).scalars().all()
        assert (proposal.job_id, proposal.family_id, proposal.family_category_id) == (
            job.id, family.id, category,
        )


class TestFamilyCategoriesOnlyFromTheFirstChunk:
    """Ревью задачи 8: перечень «без категории» уходит только в порции 1, и пара из
    ответа следующей порции не становится предложением категории — проверено на
    цикле опросчика, а не только на разборе одной порции."""

    def _scene(self, world, category):
        family = _active_family(
            world.db, title="Семья кладки", unit_name="M3", actor_id=world.admin.id
        )
        _uncategorize(world.db, family.id)
        for n in range(1, 5):
            _make_system(world.db, _ctx_bare_unit(world, f"Имя {n}"), world.admin.id)
        two = S.model_copy(update={"SEMANTIC_DISCOVERY_CHUNK_NAMES": 2})
        job = _launch(world.db, world.bare_unit, admin_id=world.admin.id, settings=two)
        return family, two, job, _claim(world.db, settings=two)

    def _run(self, world, two, claim, first, second):
        client = _ScriptedClient(_response(first), _response(second))
        worker._run_claim(_SameSession(world.db), client, claim, settings=two, clock=lambda: NOW)
        return client

    def test_family_categories_of_a_later_chunk_are_ignored(self, world):
        category = seed_category_id(world.db)
        family, two, job, claim = self._scene(world, category)

        client = self._run(
            world, two, claim,
            _answer([_group([1, 2], category_id=category, title="Кладка")]),
            _answer([_group([3, 4], draft_ref=1)], family_categories=[(family.id, category)]),
        )

        assert "СЕМЬИ БЕЗ КАТЕГОРИИ" not in client.bodies[1]["messages"][1]["content"]
        stored = world.db.get(SemanticJob, job.id)
        world.db.refresh(stored)
        assert stored.status == "done"
        assert world.db.execute(sa.select(FamilyCategoryProposal)).scalars().all() == []

    def test_family_categories_of_the_first_chunk_become_a_proposal(self, world):
        category = seed_category_id(world.db)
        family, two, job, claim = self._scene(world, category)

        self._run(
            world, two, claim,
            _answer([_group([1, 2], category_id=category, title="Кладка")],
                    family_categories=[(family.id, category)]),
            _answer([_group([3, 4], draft_ref=1)]),
        )

        (proposal,) = world.db.execute(sa.select(FamilyCategoryProposal)).scalars().all()
        assert (proposal.job_id, proposal.family_id) == (job.id, family.id)


class TestEndToEndThreeChunks:
    def test_250_names_one_draft_collects_three_chunks_and_the_rest_is_wrong_references(
        self, world
    ):
        category = seed_category_id(world.db)
        contexts = _bare_world(world, titles=tuple(f"Имя {n:03d}" for n in range(1, 251)))
        job = _launch(world.db, world.bare_unit, admin_id=world.admin.id, settings=W_S)
        claim = _claim(world.db, settings=W_S)
        client = _ScriptedClient(
            # порция 1 (1–100): новый черновик и «не работа»
            _response(_answer(
                [_group(list(range(1, 91)), category_id=category, title="Кладка")],
                not_work=list(range(91, 101)),
            )),
            # порция 2 (101–200): продолжение черновика 1, ссылка на несуществующий
            # черновик 5 (имена 191–195 — в остаток), «не работа»
            _response(_answer(
                [_group(list(range(101, 191)), draft_ref=1), _group(list(range(191, 196)), draft_ref=5)],
                not_work=list(range(196, 201)),
            )),
            # порция 3 (201–250): продолжение черновика 1, семья не из отправленных
            # (имена 246–250 — в остаток)
            _response(_answer(
                [_group(list(range(201, 246)), draft_ref=1),
                 _group(list(range(246, 251)), family_id=987654)],
            )),
        )

        worker._run_claim(_SameSession(world.db), client, claim, settings=W_S, clock=lambda: NOW)

        assert len(client.bodies) == 3
        users = [b["messages"][1]["content"] for b in client.bodies]
        assert "ИМЕНА 1–100 ИЗ 250:" in users[0]
        assert "ИМЕНА 101–200 ИЗ 250:" in users[1] and "Ч1. Кладка" in users[1]
        assert "ИМЕНА 201–250 ИЗ 250:" in users[2] and "Ч1. Кладка" in users[2]
        stored = world.db.get(SemanticJob, job.id)
        world.db.refresh(stored)
        assert stored.status == "done"
        kladka, trash = _drafts(world.db, job.id)
        assert (kladka.grp, kladka.title, trash.grp) == ("new", "Кладка", "not_work")
        expected = (
            [contexts[n - 1] for n in range(1, 91)]
            + [contexts[n - 1] for n in range(101, 191)]
            + [contexts[n - 1] for n in range(201, 246)]
        )
        assert _members(world.db, kladka.id) == sorted(expected)
        assert _members(world.db, trash.id) == sorted(
            [contexts[n - 1] for n in list(range(91, 101)) + list(range(196, 201))]
        )
        placed = set(_members(world.db, kladka.id)) | set(_members(world.db, trash.id))
        assert set(contexts) - placed == {
            contexts[n - 1] for n in list(range(191, 196)) + list(range(246, 251))
        }
        attempt = _attempt(world.db, job.id)
        assert attempt.outcome == "ok" and attempt.cost_usd == Decimal("0.03")
