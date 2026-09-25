"""Разовый проход по существующему каталогу: `run_backfill`, команда
`backfill-contexts` (спека `2026-09-22-catalog-families-design.md` §2.9;
план, задача 11).

Помощники (`_proposal`, `_position`) — ЛОКАЛЬНАЯ копия набора соседних
файлов семьи тестов: наборы помощников тестов этого проекта друг у друга не
импортируют (докстрока `test_context_routing.py`, `test_work_families.py`).
"""
from __future__ import annotations

import datetime as dt
import uuid

import pytest
import sqlalchemy as sa
from click.testing import CliRunner

import cli
from models import (
    CatalogContext,
    ContextBucket,
    ContextMember,
    DecisionSource,
    MembershipState,
    NameRole,
    PositionItem,
    RoutedBy,
    SemanticEvent,
    SemanticKind,
    SemanticState,
    WorkCategory,
)
from services import semantic_rules as semantic_rules_module
from services.catalog_backfill import BackfillReport, etc_category_share, run_backfill
from services.context_operations import move_members, refresh_membership_states
from services.context_routing import route_position
from services.work_families import confirm_kind, set_name_role

pytestmark = pytest.mark.integration


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _proposal(factories):
    estimate = factories.EstimateFactory.create()
    lot = factories.LotFactory.create(estimate=estimate)
    return factories.ProposalFactory.create(lot=lot)


def _position(factories, proposal, *, title=None, chapter=None):
    """Позиция с ОТДЕЛЬНОЙ каталожной строкой, ещё НЕ маршрутизированная —
    независимость литералов буджетов/контекстов друг от друга (каждая
    позиция теста рождает свою корзину). `title`, если задан, ставится и на
    позицию, и на `catalog_position.standard_job_title` — именно ПОСЛЕДНЕЕ
    поле классифицирует роль имени при создании контекста
    (`_create_default_context`, `context_routing.py`), поэтому тест,
    которому важна роль конкретного имени, обязан управлять ИМ, а не только
    `job_title_in_proposal`."""
    resolved_title = title or f"Работа {_uid()}"
    cp = factories.CatalogPositionFactory.create(standard_job_title=resolved_title)
    kwargs = dict(
        proposal=proposal,
        is_chapter=False,
        job_title_in_proposal=resolved_title,
        catalog_position_id=cp.id,
    )
    if chapter is not None:
        kwargs["chapter_item_id"] = chapter.id
    position = factories.PositionItemFactory.create(**kwargs)
    return position, cp


def _chapter(factories, proposal, *, title, category_id=None):
    kwargs = dict(proposal=proposal, is_chapter=True, job_title_in_proposal=title)
    if category_id is not None:
        kwargs["work_category_id"] = category_id
        kwargs["category_source"] = "file"
    return factories.PositionItemFactory.create(**kwargs)


def _category_id_by_code(db, code: str) -> int:
    return db.execute(sa.select(WorkCategory.id).where(WorkCategory.code == code)).scalar_one()


# ---------------------------------------------------------------------------
#  Имена, заводимые задачей 11 (план, «Interfaces»).
# ---------------------------------------------------------------------------


def test_module_surface_names_from_plan_task_11():
    report = BackfillReport(
        buckets_created=1,
        contexts_created=2,
        members_created=3,
        rows_without_bucket=4,
        by_name_role={"WORK": 5},
        by_semantic_kind={"SYSTEM": 6},
        fields_changed=7,
    )
    assert (
        report.buckets_created,
        report.contexts_created,
        report.members_created,
        report.rows_without_bucket,
        report.by_name_role,
        report.by_semantic_kind,
        report.fields_changed,
    ) == (1, 2, 3, 4, {"WORK": 5}, {"SYSTEM": 6}, 7)


# ---------------------------------------------------------------------------
#  Базовый проход: маршрутизирует всё подходящее, считает сирот.
# ---------------------------------------------------------------------------


class TestRunBackfillBasic:
    def test_routes_all_eligible_positions_and_counts_orphans(
        self, committing_db, committing_factories
    ):
        proposal = _proposal(committing_factories)
        positions = []
        for _ in range(3):
            position, _cp = _position(committing_factories, proposal)
            positions.append(position)
        # Строка-раздел — не работа, членства не имеет, партию не пополняет.
        committing_factories.PositionItemFactory.create(
            proposal=proposal, is_chapter=True, job_title_in_proposal="Раздел"
        )
        # Позиция без catalog_position_id — не сопоставлена, партию не пополняет.
        committing_factories.PositionItemFactory.create(
            proposal=proposal, is_chapter=False, job_title_in_proposal="Не сматчено"
        )
        # Каталожная строка без единой позиции — сирота.
        committing_factories.CatalogPositionFactory.create()
        committing_db.commit()

        report = run_backfill(committing_db, batch_size=500)

        assert report.buckets_created == 3
        assert report.contexts_created == 3
        assert report.members_created == 3
        assert report.rows_without_bucket == 1
        assert report.fields_changed == 0
        assert sum(report.by_semantic_kind.values()) == 3
        assert sum(report.by_name_role.values()) == 3

        for position in positions:
            member = committing_db.get(ContextMember, position.id)
            assert member is not None
            assert member.membership_state == MembershipState.CURRENT.value
            assert member.bucket_id is not None
            assert member.context_id is not None


# ---------------------------------------------------------------------------
#  Идемпотентность: второй прогон на неизменных данных — нулевой отчёт.
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_second_run_on_unchanged_data_reports_all_zero(
        self, committing_db, committing_factories
    ):
        proposal = _proposal(committing_factories)
        for _ in range(4):
            _position(committing_factories, proposal)
        committing_db.commit()

        first = run_backfill(committing_db, batch_size=500)
        assert first.members_created == 4

        member_ids = [
            row[0] for row in committing_db.execute(sa.select(ContextMember.position_item_id))
        ]
        context_ids = [row[0] for row in committing_db.execute(sa.select(CatalogContext.id))]

        def _member_rows():
            return {
                pid: (
                    m.context_id,
                    m.bucket_id,
                    m.routed_by,
                    m.routing_rule_id,
                    m.membership_state,
                    m.conflict_at,
                    m.conflict_from_context_id,
                )
                for pid in member_ids
                for m in [committing_db.get(ContextMember, pid)]
            }

        def _context_rows():
            return {
                cid: (
                    c.bucket_id,
                    c.is_default,
                    c.work_family_id,
                    c.family_source,
                    c.semantic_kind,
                    c.semantic_kind_source,
                    c.semantic_kind_by,
                    c.name_role,
                    c.name_role_source,
                    c.name_role_by,
                    c.name_role_at,
                    c.place_dictionary_version,
                    c.semantic_state,
                    c.comparability_reason,
                    c.archived_at,
                )
                for cid in context_ids
                for c in [committing_db.get(CatalogContext, cid)]
            }

        members_before = _member_rows()
        contexts_before = _context_rows()

        second = run_backfill(committing_db, batch_size=500)
        assert second.buckets_created == 0
        assert second.contexts_created == 0
        assert second.members_created == 0
        assert second.fields_changed == 0
        assert second.rows_without_bucket == first.rows_without_bucket
        assert second.by_name_role == first.by_name_role
        assert second.by_semantic_kind == first.by_semantic_kind

        # сверка СНИМКАМИ строк базы, а не только
        # полями отчёта — план требует «пополям», а не «по отчёту».
        assert _member_rows() == members_before
        assert _context_rows() == contexts_before


# ---------------------------------------------------------------------------
#  Ручные переопределения не переписываются — три поля порознь.
# ---------------------------------------------------------------------------


class TestManualOverridesNotRewritten:
    def test_three_manual_fields_survive_backfill(self, committing_db, committing_factories):
        user = committing_factories.UserFactory.create()
        proposal = _proposal(committing_factories)
        position, _cp = _position(committing_factories, proposal, title="Работа ручная")
        member0 = route_position(committing_db, position_item_id=position.id, origin="import")
        bucket = committing_db.get(ContextBucket, member0.bucket_id)

        now = dt.datetime.now(dt.UTC)
        context2 = CatalogContext(
            bucket_id=bucket.id,
            is_default=False,
            semantic_kind=SemanticKind.WORK.value,
            semantic_kind_source=DecisionSource.rule.value,
            semantic_kind_at=now,
            name_role=NameRole.WORK.value,
            name_role_source=DecisionSource.rule.value,
            name_role_at=now,
            place_dictionary_version=semantic_rules_module.PLACE_DICTIONARY_VERSION,
            semantic_state=SemanticState.SUGGESTED.value,
        )
        committing_db.add(context2)
        committing_db.flush()

        move_members(
            committing_db,
            position_item_ids=[position.id],
            target_context_id=context2.id,
            actor_id=user.id,
            reason="manual",
        )
        confirm_kind(committing_db, context_id=context2.id, kind=None, actor_id=user.id)
        set_name_role(committing_db, context_id=context2.id, role=NameRole.WORK.value, actor_id=user.id)
        committing_db.commit()

        def _member_snapshot():
            m = committing_db.get(ContextMember, position.id)
            return (m.context_id, m.bucket_id, m.routed_by, m.routing_rule_id, m.membership_state)

        def _context_snapshot():
            c = committing_db.get(CatalogContext, context2.id)
            return (
                c.semantic_kind,
                c.semantic_kind_source,
                c.semantic_kind_by,
                c.name_role,
                c.name_role_source,
                c.name_role_by,
                c.place_dictionary_version,
                c.comparability_reason,
            )

        before_member = _member_snapshot()
        before_context = _context_snapshot()
        assert before_member[2] == RoutedBy.manual.value
        assert before_context[1] == DecisionSource.manual.value
        assert before_context[4] == DecisionSource.manual.value

        run_backfill(committing_db, batch_size=500)

        assert _member_snapshot() == before_member
        assert _context_snapshot() == before_context


# ---------------------------------------------------------------------------
#  Смена версии словаря: адресность пересчёта.
# ---------------------------------------------------------------------------


class TestDictionaryVersionRecompute:
    def _stamped_context(self, db, factories, *, name_role_source, version, title, user):
        proposal = _proposal(factories)
        position, _cp = _position(factories, proposal, title=title)
        member = route_position(db, position_item_id=position.id, origin="import")
        context = db.get(CatalogContext, member.context_id)
        context.name_role_source = name_role_source
        context.place_dictionary_version = version
        if name_role_source == DecisionSource.manual.value:
            context.name_role_by = user.id
        db.flush()
        return context

    def test_recompute_addresses_only_stale_rule_sourced_contexts(
        self, committing_db, committing_factories, monkeypatch
    ):
        user = committing_factories.UserFactory.create()
        original_version = semantic_rules_module.PLACE_DICTIONARY_VERSION

        stale_rule = self._stamped_context(
            committing_db,
            committing_factories,
            name_role_source=DecisionSource.rule.value,
            version=original_version,
            title="Работа стейл",
            user=user,
        )
        fresh_rule = self._stamped_context(
            committing_db,
            committing_factories,
            name_role_source=DecisionSource.rule.value,
            version=original_version + 1,
            title="Работа свежая",
            user=user,
        )
        stale_manual = self._stamped_context(
            committing_db,
            committing_factories,
            name_role_source=DecisionSource.manual.value,
            version=original_version,
            title="Работа ручная стейл",
            user=user,
        )
        committing_db.commit()

        def _snapshot(ctx_id):
            c = committing_db.get(CatalogContext, ctx_id)
            return (
                c.name_role,
                c.comparability_reason,
                c.name_role_source,
                c.name_role_by,
                c.place_dictionary_version,
                c.name_role_at,
            )

        before_fresh = _snapshot(fresh_rule.id)
        before_manual = _snapshot(stale_manual.id)

        monkeypatch.setattr(semantic_rules_module, "PLACE_DICTIONARY_VERSION", original_version + 1)

        report = run_backfill(committing_db, batch_size=500)

        assert _snapshot(fresh_rule.id) == before_fresh
        assert _snapshot(stale_manual.id) == before_manual

        stale_after = committing_db.get(CatalogContext, stale_rule.id)
        assert stale_after.place_dictionary_version == original_version + 1
        assert report.fields_changed == 1

        # роль НЕ поменялась (WORK -> WORK) —
        # `name_role_set` не пишется вовсе, пустой факт в журнал не идёт.
        events = (
            committing_db.execute(
                sa.select(SemanticEvent).where(
                    SemanticEvent.context_id == stale_rule.id,
                    SemanticEvent.event_type == "name_role_set",
                )
            )
            .scalars()
            .all()
        )
        assert events == []


# ---------------------------------------------------------------------------
#  пересчёт читает цепочку ПРЕДСТАВИТЕЛЬНОГО
#  членства, а не пустую — иначе `LOCATION_ONLY` под рабочим разделом
#  теряет `work_title` и приобретает `insufficient_description` на первом
#  же инкременте версии, хотя словарь эту строку не касался. Вход
#  «Корпус 1» под «Монтаж витражей».
# ---------------------------------------------------------------------------


class TestRecomputeUsesRepresentativeChapterChain:
    def test_location_only_role_and_reason_survive_version_bump(
        self, committing_db, committing_factories, monkeypatch
    ):
        proposal = _proposal(committing_factories)
        chapter = _chapter(committing_factories, proposal, title="Монтаж витражей")
        position, _cp = _position(committing_factories, proposal, title="Корпус 1", chapter=chapter)
        member = route_position(committing_db, position_item_id=position.id, origin="import")
        context = committing_db.get(CatalogContext, member.context_id)
        assert context.name_role == NameRole.LOCATION_ONLY.value
        assert context.comparability_reason is None  # рабочий раздел найден при создании
        original_version = context.place_dictionary_version
        committing_db.commit()

        monkeypatch.setattr(
            semantic_rules_module, "PLACE_DICTIONARY_VERSION", original_version + 1
        )

        run_backfill(committing_db, batch_size=500)

        committing_db.refresh(context)
        assert context.name_role == NameRole.LOCATION_ONLY.value
        assert context.comparability_reason is None
        assert context.place_dictionary_version == original_version + 1


# ---------------------------------------------------------------------------
#  пересчёт реально меняет роль, когда
#  словарь мест расширили, и пишет `name_role_set`.
# ---------------------------------------------------------------------------


class TestRecomputeChangesRoleAndWritesEvent:
    def test_new_place_token_flips_role_and_writes_name_role_set(
        self, committing_db, committing_factories, monkeypatch
    ):
        proposal = _proposal(committing_factories)
        chapter = _chapter(committing_factories, proposal, title="Монтаж витражей")
        position, _cp = _position(committing_factories, proposal, title="Полигон", chapter=chapter)
        member = route_position(committing_db, position_item_id=position.id, origin="import")
        context = committing_db.get(CatalogContext, member.context_id)
        assert context.name_role == NameRole.WORK.value
        assert context.comparability_reason is None
        original_version = context.place_dictionary_version
        committing_db.commit()

        new_tokens = semantic_rules_module.PLACE_TOKENS | {"полигон"}
        monkeypatch.setattr(semantic_rules_module, "PLACE_TOKENS", new_tokens)
        monkeypatch.setattr(
            semantic_rules_module, "PLACE_DICTIONARY_VERSION", original_version + 1
        )

        report = run_backfill(committing_db, batch_size=500)

        committing_db.refresh(context)
        assert context.name_role == NameRole.LOCATION_ONLY.value
        assert context.comparability_reason is None  # рабочий раздел найден
        assert context.place_dictionary_version == original_version + 1
        # fields_changed считает ПОЛЯ — роль (WORK->LOCATION_ONLY) и
        # версия (original_version -> +1) — ровно два, а не число НАЙДЕННЫХ
        # контекстов (был бы 1 — контекст один).
        assert report.fields_changed == 2

        events = (
            committing_db.execute(
                sa.select(SemanticEvent).where(
                    SemanticEvent.context_id == context.id,
                    SemanticEvent.event_type == "name_role_set",
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert events[0].payload["from"] == NameRole.WORK.value
        assert events[0].payload["to"] == NameRole.LOCATION_ONLY.value
        assert events[0].payload["source"] == DecisionSource.rule.value
        assert events[0].payload["place_dictionary_version"] == original_version + 1


# ---------------------------------------------------------------------------
#  фильтр партии по членству защищает не только
#  ручное решение, но и членство `STALE` (задача 10) — проход не должен
#  молча возвращать его в `CURRENT` в обход принятия переноса оператором.
# ---------------------------------------------------------------------------


class TestStaleMembershipUntouchedByBackfill:
    def test_stale_membership_survives_backfill_unchanged(
        self, committing_db, committing_factories
    ):
        cat_a = _category_id_by_code(committing_db, "2.1")
        cat_b = _category_id_by_code(committing_db, "2.2")

        proposal = _proposal(committing_factories)
        chapter = _chapter(committing_factories, proposal, title="Раздел А", category_id=cat_a)
        position, _cp = _position(committing_factories, proposal, chapter=chapter)
        route_position(committing_db, position_item_id=position.id, origin="import")
        committing_db.commit()

        # Ручной разнос статьи раздела (штатный путь: правка эффективной
        # статьи + сверка `refresh_membership_states`, задача 10).
        chapter_row = committing_db.get(PositionItem, chapter.id)
        chapter_row.work_category_id = cat_b
        chapter_row.category_source = "manual"
        committing_db.flush()

        refresh_report = refresh_membership_states(committing_db, position_item_ids=[position.id])
        assert refresh_report.marked_stale == 1
        committing_db.commit()

        member_before = committing_db.get(ContextMember, position.id)
        assert member_before.membership_state == MembershipState.STALE.value
        before = (
            member_before.context_id,
            member_before.bucket_id,
            member_before.routed_by,
            member_before.membership_state,
        )

        run_backfill(committing_db, batch_size=500)

        member_after = committing_db.get(ContextMember, position.id)
        after = (
            member_after.context_id,
            member_after.bucket_id,
            member_after.routed_by,
            member_after.membership_state,
        )
        assert after == before
        assert member_after.membership_state == MembershipState.STALE.value


# ---------------------------------------------------------------------------
#  архивные контексты вне пересчёта и вне
#  распределений отчёта.
# ---------------------------------------------------------------------------


class TestArchivedContextsExcluded:
    def test_archived_stale_context_not_touched_by_recompute(
        self, committing_db, committing_factories, monkeypatch
    ):
        proposal = _proposal(committing_factories)
        position, _cp = _position(committing_factories, proposal, title="Работа архивная")
        member = route_position(committing_db, position_item_id=position.id, origin="import")
        context = committing_db.get(CatalogContext, member.context_id)
        context.archived_at = dt.datetime.now(dt.UTC)
        committing_db.commit()

        before = (
            context.name_role,
            context.comparability_reason,
            context.place_dictionary_version,
            context.name_role_at,
        )

        monkeypatch.setattr(
            semantic_rules_module, "PLACE_DICTIONARY_VERSION", context.place_dictionary_version + 1
        )

        run_backfill(committing_db, batch_size=500)

        committing_db.refresh(context)
        after = (
            context.name_role,
            context.comparability_reason,
            context.place_dictionary_version,
            context.name_role_at,
        )
        assert after == before

    def test_archived_context_excluded_from_distributions(
        self, committing_db, committing_factories
    ):
        proposal = _proposal(committing_factories)
        position, _cp = _position(committing_factories, proposal)
        member = route_position(committing_db, position_item_id=position.id, origin="import")
        context = committing_db.get(CatalogContext, member.context_id)
        context.archived_at = dt.datetime.now(dt.UTC)
        committing_db.commit()

        report = run_backfill(committing_db, batch_size=500)

        assert sum(report.by_name_role.values()) == 0
        assert sum(report.by_semantic_kind.values()) == 0


# ---------------------------------------------------------------------------
#  предикат «Прочее»/«Прочие» по префиксу
#  «Проч», знаменатель доли — членства, не контексты. Независимые от кода
#  литералы: код категории, не название, подобранное под `LIKE` кода.
# ---------------------------------------------------------------------------


class TestEtcCategoryPredicateAndDenominator:
    def test_prefix_predicate_covers_prochee_and_prochie_with_membership_denominator(
        self, committing_db, committing_factories
    ):
        etc1_id = _category_id_by_code(committing_db, "1.99")  # "Прочее (подготовительные…)"
        etc2_id = _category_id_by_code(committing_db, "6.5.3")  # "Прочие модульные элементы"
        other_id = _category_id_by_code(committing_db, "2.1")  # "Устройство свайного основания"

        proposal = _proposal(committing_factories)
        chapter_etc1 = _chapter(committing_factories, proposal, title="Раздел Прочее", category_id=etc1_id)
        chapter_etc2 = _chapter(committing_factories, proposal, title="Раздел Прочие", category_id=etc2_id)
        chapter_other = _chapter(committing_factories, proposal, title="Раздел Прочее не", category_id=other_id)

        # Одна каталожная строка, ДВЕ позиции — один контекст, два членства
        # (знаменатель доли обязан считать членства, а не контексты).
        shared_cp = committing_factories.CatalogPositionFactory.create()
        shared_position_1 = committing_factories.PositionItemFactory.create(
            proposal=proposal,
            is_chapter=False,
            job_title_in_proposal=f"Работа общая 1 {_uid()}",
            catalog_position_id=shared_cp.id,
            chapter_item_id=chapter_etc1.id,
        )
        shared_position_2 = committing_factories.PositionItemFactory.create(
            proposal=proposal,
            is_chapter=False,
            job_title_in_proposal=f"Работа общая 2 {_uid()}",
            catalog_position_id=shared_cp.id,
            chapter_item_id=chapter_etc1.id,
        )
        etc2_position, _cp2 = _position(committing_factories, proposal, chapter=chapter_etc2)
        other_position, _cp3 = _position(committing_factories, proposal, chapter=chapter_other)
        committing_db.commit()

        route_position(committing_db, position_item_id=shared_position_1.id, origin="import")
        route_position(committing_db, position_item_id=shared_position_2.id, origin="import")
        route_position(committing_db, position_item_id=etc2_position.id, origin="import")
        route_position(committing_db, position_item_id=other_position.id, origin="import")
        committing_db.commit()

        etc_count, total_count = etc_category_share(committing_db)
        assert etc_count == 3
        assert total_count == 4


# ---------------------------------------------------------------------------
#  `context_created.origin == "backfill"` — спека
#  §2.14 требует `origin` в payload, проход обязан передавать своё.
# ---------------------------------------------------------------------------


class TestContextCreatedOriginIsBackfill:
    def test_context_created_event_payload_has_backfill_origin(
        self, committing_db, committing_factories
    ):
        proposal = _proposal(committing_factories)
        position, _cp = _position(committing_factories, proposal)
        committing_db.commit()

        report = run_backfill(committing_db, batch_size=500)
        assert report.contexts_created == 1

        member = committing_db.get(ContextMember, position.id)
        events = (
            committing_db.execute(
                sa.select(SemanticEvent).where(
                    SemanticEvent.context_id == member.context_id,
                    SemanticEvent.event_type == "context_created",
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert events[0].payload["origin"] == "backfill"


# ---------------------------------------------------------------------------
#  Коммит на партию и прерывание: согласованный префикс, повтор без дублей.
#  Партия > 5 (prepare_threshold, docs/insights/batch-larger-than-five.md):
#  8 позиций, batch_size=6 — первая партия несёт 6 (> 5), партий — 2.
# ---------------------------------------------------------------------------


class TestBatchCommitAndInterruption:
    def test_interruption_after_first_batch_leaves_consistent_prefix_and_resumes(
        self, committing_db, committing_factories, committing_session_factory
    ):
        proposal = _proposal(committing_factories)
        positions = []
        for _ in range(8):
            position, _cp = _position(committing_factories, proposal)
            positions.append(position)
        committing_db.commit()

        ordered_ids = sorted(p.id for p in positions)
        expected_first_batch = set(ordered_ids[:6])
        expected_second_batch = set(ordered_ids[6:])

        calls: list[int] = []

        def _progress(n):
            calls.append(n)
            if len(calls) == 1:
                raise RuntimeError("прервано после первой партии")

        with pytest.raises(RuntimeError, match="прервано после первой партии"):
            run_backfill(committing_db, batch_size=6, progress=_progress)

        assert calls == [6]

        # Независимая сессия — видит только реально закоммиченное.
        verify_db = committing_session_factory()
        try:
            member_ids = set(
                verify_db.execute(
                    sa.select(ContextMember.position_item_id).where(
                        ContextMember.position_item_id.in_(ordered_ids)
                    )
                )
                .scalars()
                .all()
            )
            assert member_ids == expected_first_batch
            for pid in expected_first_batch:
                member = verify_db.get(ContextMember, pid)
                assert member.bucket_id is not None
                assert member.context_id is not None
        finally:
            verify_db.close()

        second_report = run_backfill(committing_db, batch_size=6)
        assert second_report.members_created == len(expected_second_batch)
        assert second_report.buckets_created == len(expected_second_batch)
        assert second_report.contexts_created == len(expected_second_batch)

        all_member_ids = list(
            committing_db.execute(
                sa.select(ContextMember.position_item_id).where(
                    ContextMember.position_item_id.in_(ordered_ids)
                )
            )
            .scalars()
            .all()
        )
        assert set(all_member_ids) == set(ordered_ids)
        assert len(all_member_ids) == len(ordered_ids)  # без дублей — PK держит структурно


# ---------------------------------------------------------------------------
#  Доля членств в корзинах статьи «Прочее» (спека §1.8) — диагностика отчёта.
# ---------------------------------------------------------------------------


class TestEtcCategoryShare:
    def test_share_counts_members_in_etc_titled_categories(
        self, committing_db, committing_factories
    ):
        etc_category_id = committing_db.execute(
            sa.select(WorkCategory.id).where(WorkCategory.title.like("Прочее%")).limit(1)
        ).scalar_one()
        proposal = _proposal(committing_factories)
        chapter = committing_factories.PositionItemFactory.create(
            proposal=proposal,
            is_chapter=True,
            job_title_in_proposal="Раздел Прочее",
            work_category_id=etc_category_id,
            category_source="file",
        )
        etc_position, _cp1 = _position(committing_factories, proposal, chapter=chapter)
        other_position, _cp2 = _position(committing_factories, proposal)
        committing_db.commit()

        route_position(committing_db, position_item_id=etc_position.id, origin="import")
        route_position(committing_db, position_item_id=other_position.id, origin="import")
        committing_db.commit()

        etc_count, total_count = etc_category_share(committing_db)
        assert etc_count == 1
        assert total_count == 2


# ---------------------------------------------------------------------------
#  Команда `backfill-contexts`: guard, счастливый путь, печать отчёта.
# ---------------------------------------------------------------------------

REMOTE_URL = (
    "postgresql+psycopg://test_owner:secret-pw@"
    "ep-example-0000.c-3.eu-central-1.aws.neon.tech/neondb"
)


@pytest.fixture
def _unlisted_target_in_dev(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DB_EXTRA_TARGETS", "")
    monkeypatch.delenv("PGHOSTADDR", raising=False)
    monkeypatch.delenv("PGSERVICE", raising=False)
    monkeypatch.delenv("PGPORT", raising=False)
    monkeypatch.delenv("PGHOST", raising=False)
    monkeypatch.delenv("PGDATABASE", raising=False)
    monkeypatch.setattr(cli.settings, "DATABASE_URL", REMOTE_URL, raising=False)


def test_backfill_command_refuses_unlisted_target(_unlisted_target_in_dev, monkeypatch):
    """Guard срабатывает ДО открытия сессии — та же дисциплина, что у
    `seed-work-families` (план, задача 7)."""

    def _explode():
        raise AssertionError("SessionLocal() вызван — guard сработал слишком поздно")

    monkeypatch.setattr(cli, "SessionLocal", _explode)

    result = CliRunner().invoke(cli.cli, ["backfill-contexts"])
    assert result.exit_code != 0
    assert isinstance(result.exception, RuntimeError)
    assert "APP_ENV=dev" in str(result.exception)


def test_backfill_command_passes_guard_when_prod(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    reached = []

    def _record():
        reached.append(True)
        raise RuntimeError("stop-here")

    monkeypatch.setattr(cli, "SessionLocal", _record)
    CliRunner().invoke(cli.cli, ["backfill-contexts"])
    assert reached, "guard не пустил дальше, хотя запись разрешена"


def test_backfill_command_success_path_persists_and_prints_report(
    committing_session_factory, monkeypatch
):
    """Команда реально коммитит (видно из независимой сессии) и печатает
    отчёт целиком, включая отдельную строку про статью «Прочее» (план,
    задача 11)."""
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setattr(cli, "SessionLocal", committing_session_factory)

    from tests import factories as f

    setup_db = committing_session_factory()
    try:
        f._register_session(setup_db)
        etc_category_id = setup_db.execute(
            sa.select(WorkCategory.id).where(WorkCategory.title.like("Прочее%")).limit(1)
        ).scalar_one()
        proposal = _proposal(f)
        chapter = f.PositionItemFactory.create(
            proposal=proposal,
            is_chapter=True,
            job_title_in_proposal="Раздел Прочее",
            work_category_id=etc_category_id,
            category_source="file",
        )
        _position(f, proposal, chapter=chapter)
        _position(f, proposal)
        setup_db.commit()
    finally:
        f._register_session(None)
        setup_db.close()

    result = CliRunner().invoke(cli.cli, ["backfill-contexts"])

    assert result.exit_code == 0, f"команда упала: {result.output}\n{result.exception!r}"
    assert "Корзин создано=2" in result.output
    assert "контекстов создано=2" in result.output
    assert "членств создано=2" in result.output
    assert "Членств в корзинах статьи" in result.output
    assert "1 из 2" in result.output

    verify_db = committing_session_factory()
    try:
        total = verify_db.execute(sa.select(sa.func.count()).select_from(ContextMember)).scalar_one()
        assert total == 2, "членства не видны из независимой сессии после команды"
    finally:
        verify_db.close()
