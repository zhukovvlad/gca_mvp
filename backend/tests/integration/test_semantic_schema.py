"""Схема семантического контура (план фичи «Семьи и контексты», задача 1;
спека `2026-09-22-catalog-families-design.md` §2.3, §6 «Схема»): каждый CHECK,
составной FK и частичный индекс — доказан пробоем, `INSERT` с ожиданием
`IntegrityError`, по одному нарушенному ограничению на вход.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import os
import re
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from models import (
    CK_CONTEXT_FAMILY_PROVENANCE,
    CK_CONTEXT_KIND_SOURCE_PAIR,
    CK_CONTEXT_NAME_ROLE_SOURCE_PAIR,
    CK_EVENT_ONE_SUBJECT,
    CK_EVENT_PAYLOAD_NOT_EMPTY,
    CK_EVENT_SUBJECT_BY_TYPE,
    CK_FAMILY_ACTIVATION_PAIR,
    CK_FAMILY_ACTIVE_NEEDS_DEFINITION,
    CK_FAMILY_AUTHOR_IFF_NOT_SEED,
    CK_MEMBER_CONFLICT_PAIR,
    CK_MEMBER_RULE_PAIR,
    COMPARABILITY_REASONS,
    DECISION_SOURCES,
    FAMILY_SOURCES,
    FAMILY_STATUSES,
    MEMBERSHIP_STATES,
    NAME_ROLES,
    ROUTED_BY_VALUES,
    SEMANTIC_EVENT_TYPES_SQL,
    SEMANTIC_KINDS,
    SEMANTIC_STATES,
    CatalogContext,
    ContextBucket,
    ContextMember,
    ContextRoutingRule,
    DecisionSource,
    FamilySource,
    FamilyStatus,
    MembershipState,
    NameRole,
    RoutedBy,
    SemanticEvent,
    SemanticKind,
    SemanticState,
    WorkFamily,
)
from tests.integration.test_schema_constraints import rejected

pytestmark = pytest.mark.integration


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _family(session, factories, **overrides) -> WorkFamily:
    defaults = dict(
        title=f"Семья {_uid()}", status=FamilyStatus.draft.value, seed_key=f"seed-{_uid()}"
    )
    defaults.update(overrides)
    fam = WorkFamily(**defaults)
    session.add(fam)
    session.flush()
    return fam


def _bucket(session, factories, catalog_position=None, work_category_id=None) -> ContextBucket:
    cp = catalog_position or factories.CatalogPositionFactory.create()
    b = ContextBucket(catalog_position_id=cp.id, work_category_id=work_category_id)
    session.add(b)
    session.flush()
    return b


def _context(session, factories, bucket=None, **overrides) -> CatalogContext:
    bucket = bucket or _bucket(session, factories)
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


def _position_item(session, factories, **overrides):
    return factories.PositionItemFactory.create(**overrides)


def _member(session, factories, position_item=None, context=None, **overrides) -> ContextMember:
    position_item = position_item or _position_item(session, factories)
    context = context or _context(session, factories)
    defaults = dict(
        position_item_id=position_item.id,
        context_id=context.id,
        bucket_id=context.bucket_id,
        membership_state=MembershipState.CURRENT.value,
        routed_by=RoutedBy.default.value,
    )
    defaults.update(overrides)
    member = ContextMember(**defaults)
    session.add(member)
    session.flush()
    return member


def _migration_0017():
    path = next(
        Path(__file__).resolve().parents[2].glob("alembic/versions/*0017-semantic_contour.py")
    )
    spec = importlib.util.spec_from_file_location("_migration_0017", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _live_check_expr(session, table: str, constraint_name: str) -> str:
    """Текст `CHECK`, который РЕАЛЬНО лежит в БД (`pg_get_constraintdef`), а
    не Python-константа `models.py`/миграции.

    Доработка F1: константу можно оставить множеством, подменив предикат
    ПРЯМО в вызове `CheckConstraint(...)` миграции литералом-префиксом — обе
    стороны parity останутся текстово равны (обе не тронуты), а вставленный в
    БД `CHECK` при этом будет совсем другим. Свидетель обязан читать именно
    ЭТОТ текст, а не константу, иначе такая подмена не красит ни одного
    теста (найдено ревью доработки 2).

    PostgreSQL нормализует `IN (...)` в `= ANY (ARRAY[...])` — регэксп
    структурной проверки на этом тексте другой, чем на константе (см.
    `test_family_type_set_matches_independent_literal_from_db_text`).
    """
    raw = session.execute(
        sa.text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = :name AND conrelid = CAST(:table_name AS regclass)"
        ),
        {"name": constraint_name, "table_name": table},
    ).scalar_one()
    match = re.match(r"^CHECK \((.*)\)$", raw)
    assert match is not None, f"Неожиданная форма pg_get_constraintdef: {raw!r}"
    return match.group(1)


# ---------------------------------------------------------------------------
#  1. Шесть таблиц существуют — по одной проверке на таблицу (позитивный вход)
# ---------------------------------------------------------------------------

class TestTablesExist:
    def test_work_family_row(self, db_session, factories):
        fam = _family(db_session, factories, definition=None)
        assert fam.id is not None

    def test_context_bucket_row(self, db_session, factories):
        bucket = _bucket(db_session, factories)
        assert bucket.id is not None

    def test_catalog_context_row(self, db_session, factories):
        ctx = _context(db_session, factories)
        assert ctx.id is not None

    def test_context_routing_rule_row(self, db_session, factories):
        bucket = _bucket(db_session, factories)
        ctx = _context(db_session, factories, bucket=bucket)
        user = factories.UserFactory.create()
        rule = ContextRoutingRule(
            bucket_id=bucket.id, ordinal=1, predicate={"type": "always"},
            context_id=ctx.id, created_by=user.id,
        )
        db_session.add(rule)
        db_session.flush()
        assert rule.id is not None

    def test_context_member_row(self, db_session, factories):
        member = _member(db_session, factories)
        assert member.position_item_id is not None

    def test_semantic_event_row(self, db_session, factories):
        ctx = _context(db_session, factories)
        event = SemanticEvent(
            context_id=ctx.id, event_type="context_created",
            payload={"bucket_id": ctx.bucket_id, "origin": "import"},
        )
        db_session.add(event)
        db_session.flush()
        assert event.id is not None


# ---------------------------------------------------------------------------
#  2. Обе стороны каждой равносильности — предъявлены порознь
# ---------------------------------------------------------------------------

class TestBothSidesOfEquivalences:
    def test_routed_by_rule_without_rule_id_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_context_members_rule_pair"'):
            _member(db_session, factories, routed_by=RoutedBy.rule.value, routing_rule_id=None)

    def test_routing_rule_id_with_routed_by_default_rejected(self, db_session, factories):
        bucket = _bucket(db_session, factories)
        ctx = _context(db_session, factories, bucket=bucket)
        user = factories.UserFactory.create()
        rule = ContextRoutingRule(
            bucket_id=bucket.id, ordinal=1, predicate={"type": "always"},
            context_id=ctx.id, created_by=user.id,
        )
        db_session.add(rule)
        db_session.flush()
        with rejected(db_session, contains='"ck_context_members_rule_pair"'):
            _member(
                db_session, factories, context=ctx,
                routed_by=RoutedBy.default.value, routing_rule_id=rule.id,
            )

    def test_kind_source_manual_without_author_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_catalog_contexts_kind_source_pair"'):
            _context(
                db_session, factories,
                semantic_kind_source=DecisionSource.manual.value, semantic_kind_by=None,
            )

    def test_kind_source_by_with_rule_rejected(self, db_session, factories):
        user = factories.UserFactory.create()
        with rejected(db_session, contains='"ck_catalog_contexts_kind_source_pair"'):
            _context(
                db_session, factories,
                semantic_kind_source=DecisionSource.rule.value, semantic_kind_by=user.id,
            )

    def test_name_role_source_manual_without_author_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_catalog_contexts_name_role_source_pair"'):
            _context(
                db_session, factories,
                name_role_source=DecisionSource.manual.value, name_role_by=None,
            )

    def test_name_role_source_by_with_rule_rejected(self, db_session, factories):
        user = factories.UserFactory.create()
        with rejected(db_session, contains='"ck_catalog_contexts_name_role_source_pair"'):
            _context(
                db_session, factories,
                name_role_source=DecisionSource.rule.value, name_role_by=user.id,
            )

    def test_seed_key_with_created_by_rejected(self, db_session, factories):
        user = factories.UserFactory.create()
        with rejected(db_session, contains='"ck_work_families_author_iff_not_seed"'):
            _family(db_session, factories, seed_key=f"dup-{_uid()}", created_by=user.id)

    def test_empty_created_by_without_seed_key_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_work_families_author_iff_not_seed"'):
            _family(db_session, factories, seed_key=None, created_by=None)

    def test_conflict_at_without_source_context_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_context_members_conflict_pair"'):
            _member(db_session, factories, conflict_at=_now(), conflict_from_context_id=None)

    def test_conflict_source_context_without_at_rejected(self, db_session, factories):
        other_ctx = _context(db_session, factories)
        with rejected(db_session, contains='"ck_context_members_conflict_pair"'):
            _member(
                db_session, factories, conflict_at=None, conflict_from_context_id=other_ctx.id
            )

    def test_activated_at_without_activated_by_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_work_families_activation_pair"'):
            _family(db_session, factories, activated_at=_now(), activated_by=None)

    def test_activated_by_without_activated_at_rejected(self, db_session, factories):
        user = factories.UserFactory.create()
        with rejected(db_session, contains='"ck_work_families_activation_pair"'):
            _family(db_session, factories, activated_by=user.id, activated_at=None)


# ---------------------------------------------------------------------------
#  3. Происхождение семьи контекста — четыре входа, четвёртый обязателен
# ---------------------------------------------------------------------------

class TestFamilyProvenance:
    def test_assigned_family_without_provenance_rejected(self, db_session, factories):
        fam = _family(db_session, factories)
        with rejected(db_session, contains='"ck_catalog_contexts_family_provenance"'):
            _context(
                db_session, factories,
                work_family_id=fam.id, family_source=None, family_at=None, family_by=None,
            )

    def test_provenance_without_family_rejected(self, db_session, factories):
        user = factories.UserFactory.create()
        with rejected(db_session, contains='"ck_catalog_contexts_family_provenance"'):
            _context(
                db_session, factories,
                work_family_id=None, family_source=FamilySource.manual.value,
                family_at=_now(), family_by=user.id,
            )

    def test_manual_without_author_rejected(self, db_session, factories):
        fam = _family(db_session, factories)
        with rejected(db_session, contains='"ck_catalog_contexts_family_provenance"'):
            _context(
                db_session, factories,
                work_family_id=fam.id, family_source=FamilySource.manual.value,
                family_at=_now(), family_by=None,
            )

    def test_lone_family_by_with_three_empty_fields_rejected(self, db_session, factories):
        """Регрессия трёхзначной логики (`docs/pitfalls/db.md`): пара
        `num_nonnulls(work_family_id, family_source, family_at) IN (0, 3)` плюс
        `(family_source='manual') = (family_by IS NOT NULL)` ПРОПУСКАЛА эту
        строку — при пустых первых трёх полях `num_nonnulls` даёт 0 (ветвь
        верна), а `NULL = 'manual'` даёт `NULL`, не `FALSE`. Тотальный
        предикат из двух полных ветвей эту же строку отвергает."""
        user = factories.UserFactory.create()
        with rejected(db_session, contains='"ck_catalog_contexts_family_provenance"'):
            _context(
                db_session, factories,
                work_family_id=None, family_source=None, family_at=None, family_by=user.id,
            )


# ---------------------------------------------------------------------------
#  4. Границы: четыре обязаны ПРОХОДИТЬ, их отказывающие пары — рядом
# ---------------------------------------------------------------------------

class TestBoundaries:
    def test_active_family_without_definition_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_work_families_active_needs_definition"'):
            _family(db_session, factories, status=FamilyStatus.active.value, definition=None)

    def test_two_active_families_same_name_and_unit_rejected(self, db_session, factories):
        title = f"Штукатурка стен {_uid()}"
        _family(
            db_session, factories, status=FamilyStatus.active.value,
            definition="Определение А", title=title, unit_id=None,
        )
        with rejected(db_session, contains='"uq_work_families_active_name_unit"'):
            _family(
                db_session, factories, status=FamilyStatus.active.value,
                definition="Определение Б", title=title, unit_id=None,
            )

    def test_same_name_and_unit_in_draft_and_archived_pass(self, db_session, factories):
        """Граница частичности: коллизия имени+единицы держится только среди
        `active` строк — черновик и архив её не образуют."""
        title = f"Штукатурка стен {_uid()}"
        f1 = _family(db_session, factories, status=FamilyStatus.draft.value, title=title, unit_id=None)
        f2 = _family(
            db_session, factories, status=FamilyStatus.archived.value, title=title, unit_id=None
        )
        assert f1.id != f2.id

    def test_two_manual_families_with_null_seed_key_pass(self, db_session, factories):
        """NULL-ы в UNIQUE(seed_key) различны — ручные семьи не сталкиваются."""
        u1 = factories.UserFactory.create()
        u2 = factories.UserFactory.create()
        f1 = _family(db_session, factories, seed_key=None, created_by=u1.id)
        f2 = _family(db_session, factories, seed_key=None, created_by=u2.id)
        assert f1.id != f2.id

    def test_two_seed_records_with_same_seed_key_rejected(self, db_session, factories):
        key = f"seed-dup-{_uid()}"
        _family(db_session, factories, seed_key=key, created_by=None)
        with rejected(db_session, contains='"uq_work_families_seed_key"'):
            _family(db_session, factories, seed_key=key, created_by=None)

    def test_archived_default_coexists_with_live_default(self, db_session, factories):
        """Половина `archived_at IS NULL` в предикате частичного индекса:
        без неё второй `INSERT` упал бы, и тест покраснел бы в обратную сторону."""
        bucket = _bucket(db_session, factories)
        live = _context(db_session, factories, bucket=bucket, is_default=True, archived_at=None)
        archived = _context(
            db_session, factories, bucket=bucket, is_default=True, archived_at=_now()
        )
        assert live.id != archived.id

    def test_two_live_defaults_in_one_bucket_rejected(self, db_session, factories):
        bucket = _bucket(db_session, factories)
        _context(db_session, factories, bucket=bucket, is_default=True, archived_at=None)
        with rejected(db_session, contains='"uq_catalog_contexts_default_per_bucket"'):
            _context(db_session, factories, bucket=bucket, is_default=True, archived_at=None)

    def test_two_buckets_without_category_for_same_position_rejected(self, db_session, factories):
        """«Отсутствие статьи — своя корзина, одна на строку» (спека §2.2):
        `COALESCE(work_category_id,-1)` делает вторую безстатейную корзину той
        же каталожной строки дублем, а не отдельной записью — без COALESCE
        PostgreSQL не увидел бы совпадения `NULL` с `NULL`, и обычный `UNIQUE`
        это пропустил бы."""
        cp = factories.CatalogPositionFactory.create()
        _bucket(db_session, factories, catalog_position=cp, work_category_id=None)
        with rejected(db_session, contains='"uq_context_buckets_position_category"'):
            _bucket(db_session, factories, catalog_position=cp, work_category_id=None)

    def test_two_buckets_without_category_for_different_positions_pass(self, db_session, factories):
        """Составной ключ (позиция, COALESCE(статья,-1)), а не голый
        COALESCE(статья,-1): безстатейные корзины двух РАЗНЫХ строк не
        сталкиваются."""
        cp1 = factories.CatalogPositionFactory.create()
        cp2 = factories.CatalogPositionFactory.create()
        b1 = _bucket(db_session, factories, catalog_position=cp1, work_category_id=None)
        b2 = _bucket(db_session, factories, catalog_position=cp2, work_category_id=None)
        assert b1.id != b2.id


# ---------------------------------------------------------------------------
#  4b. Доработка R4: пробой на каждый CHECK, у которого пробоя не было
# ---------------------------------------------------------------------------

class TestRemainingBreaches:
    """Каждый вход — пробой РОВНО ОДНОГО ограничения, по имени."""

    def test_blank_title_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_work_families_title_not_blank"'):
            _family(db_session, factories, title="   ")

    def test_unknown_event_type_rejected(self, db_session, factories):
        ctx = _context(db_session, factories)
        with rejected(db_session, contains='"ck_semantic_events_event_type"'):
            event = SemanticEvent(
                context_id=ctx.id, family_id=None, event_type="totally_unknown_type",
                payload={"bucket_id": ctx.bucket_id, "origin": "import"},
            )
            db_session.add(event)
            db_session.flush()

    def test_semantic_state_pending_rejected(self, db_session, factories):
        """`PENDING` не объявлен значением `SemanticState` (Global Constraints
        плана): вход здесь доказывает, что схема его и не пропустит, а не
        только то, что Python-класс его не перечисляет."""
        with rejected(db_session, contains='"ck_catalog_contexts_semantic_state"'):
            _context(db_session, factories, semantic_state="PENDING")

    def test_non_object_payload_rejected(self, db_session, factories):
        ctx = _context(db_session, factories)
        with rejected(db_session, contains='"ck_semantic_events_payload_not_empty"'):
            event = SemanticEvent(
                context_id=ctx.id, family_id=None, event_type="context_created",
                payload=[1, 2, 3],
            )
            db_session.add(event)
            db_session.flush()

    def test_family_status_out_of_set_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_work_families_status"'):
            _family(db_session, factories, status="bogus")

    def test_semantic_kind_out_of_set_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_catalog_contexts_semantic_kind"'):
            _context(db_session, factories, semantic_kind="INVALID")

    def test_semantic_kind_source_out_of_set_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_catalog_contexts_semantic_kind_source"'):
            _context(db_session, factories, semantic_kind_source="invalid")

    def test_name_role_out_of_set_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_catalog_contexts_name_role"'):
            _context(db_session, factories, name_role="INVALID")

    def test_name_role_source_out_of_set_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_catalog_contexts_name_role_source"'):
            _context(db_session, factories, name_role_source="invalid")

    def test_comparability_reason_out_of_set_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_catalog_contexts_comparability_reason"'):
            _context(db_session, factories, comparability_reason="bogus")

    def test_family_source_out_of_set_rejected(self, db_session, factories):
        fam = _family(db_session, factories)
        with rejected(db_session, contains='"ck_catalog_contexts_family_source"'):
            _context(
                db_session, factories,
                work_family_id=fam.id, family_source="bogus", family_at=_now(), family_by=None,
            )

    def test_membership_state_out_of_set_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_context_members_membership_state"'):
            _member(db_session, factories, membership_state="bogus")

    def test_routed_by_out_of_set_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_context_members_routed_by"'):
            _member(db_session, factories, routed_by="bogus")


# ---------------------------------------------------------------------------
#  5. Составные FK — пробиваются прямо; PK членства — тоже
# ---------------------------------------------------------------------------

class TestCompositeForeignKeys:
    def test_member_pointing_at_context_of_foreign_bucket_rejected(self, db_session, factories):
        bucket_a = _bucket(db_session, factories)
        bucket_b = _bucket(db_session, factories)
        ctx_b = _context(db_session, factories, bucket=bucket_b)
        with rejected(db_session, contains='"fk_context_members_bucket_context"'):
            _member(db_session, factories, context=ctx_b, bucket_id=bucket_a.id)

    def test_routing_rule_pointing_at_context_of_foreign_bucket_rejected(self, db_session, factories):
        bucket_a = _bucket(db_session, factories)
        bucket_b = _bucket(db_session, factories)
        ctx_b = _context(db_session, factories, bucket=bucket_b)
        user = factories.UserFactory.create()
        with rejected(db_session, contains='"fk_context_routing_rules_bucket_context"'):
            rule = ContextRoutingRule(
                bucket_id=bucket_a.id, ordinal=1, predicate={"type": "always"},
                context_id=ctx_b.id, created_by=user.id,
            )
            db_session.add(rule)
            db_session.flush()

    def test_second_membership_for_same_position_rejected(self, db_session, factories):
        position_item = _position_item(db_session, factories)
        ctx1 = _context(db_session, factories)
        ctx2 = _context(db_session, factories)
        _member(db_session, factories, position_item=position_item, context=ctx1)
        with rejected(db_session, contains='"pk_context_members"'):
            _member(db_session, factories, position_item=position_item, context=ctx2)


# ---------------------------------------------------------------------------
#  6. Журнал: ровно один предмет, тип ↔ предмет, payload непуст
# ---------------------------------------------------------------------------

class TestJournal:
    def test_event_with_two_subjects_rejected(self, db_session, factories):
        ctx = _context(db_session, factories)
        fam = _family(db_session, factories)
        with rejected(db_session, contains='"ck_semantic_events_one_subject"'):
            event = SemanticEvent(
                context_id=ctx.id, family_id=fam.id, event_type="family_created",
                payload={"title": "x", "unit": "m", "origin": "seed"},
            )
            db_session.add(event)
            db_session.flush()

    def test_event_without_subject_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_semantic_events_one_subject"'):
            event = SemanticEvent(
                context_id=None, family_id=None, event_type="context_created",
                payload={"bucket_id": 1, "origin": "import"},
            )
            db_session.add(event)
            db_session.flush()

    def test_family_event_on_context_rejected(self, db_session, factories):
        ctx = _context(db_session, factories)
        with rejected(db_session, contains='"ck_semantic_events_subject_by_type"'):
            event = SemanticEvent(
                context_id=ctx.id, family_id=None, event_type="family_created",
                payload={"title": "x", "unit": "m", "origin": "seed"},
            )
            db_session.add(event)
            db_session.flush()

    def test_context_event_on_family_rejected(self, db_session, factories):
        fam = _family(db_session, factories)
        with rejected(db_session, contains='"ck_semantic_events_subject_by_type"'):
            event = SemanticEvent(
                context_id=None, family_id=fam.id, event_type="context_created",
                payload={"bucket_id": 1, "origin": "import"},
            )
            db_session.add(event)
            db_session.flush()

    def test_empty_payload_rejected(self, db_session, factories):
        ctx = _context(db_session, factories)
        with rejected(db_session, contains='"ck_semantic_events_payload_not_empty"'):
            event = SemanticEvent(
                context_id=ctx.id, family_id=None, event_type="context_created", payload={}
            )
            db_session.add(event)
            db_session.flush()

    def test_context_family_assigned_with_family_id_rejected(self, db_session, factories):
        fam = _family(db_session, factories)
        with rejected(db_session, contains='"ck_semantic_events_subject_by_type"'):
            event = SemanticEvent(
                context_id=None, family_id=fam.id, event_type="context_family_assigned",
                payload={"from_family_id": None, "to_family_id": fam.id, "source": "manual"},
            )
            db_session.add(event)
            db_session.flush()

    def test_context_family_assigned_with_context_id_passes(self, db_session, factories):
        """Вход уровня ТАБЛИЦЫ: `context_family_assigned` с `context_id`
        (предмет — контекст) проходит. После переименования события (спека
        §2.14, `family_assigned` → `context_family_assigned`) префиксный
        предикат `left(event_type,7)='family_'` и предикат по явному
        множеству типов дают на ЭТОЙ строке ОДИНАКОВЫЙ ответ — имя события
        больше не начинается с `family_`, и оба предиката согласны. Разница
        между ними видна не здесь, а на историческом имени `family_assigned`,
        разобранном СТАНДАЛОНОМ, вне таблицы и вне закрытого списка
        `event_type` — см. `TestEventSubjectByTypeExpression` ниже."""
        ctx = _context(db_session, factories)
        event = SemanticEvent(
            context_id=ctx.id, family_id=None, event_type="context_family_assigned",
            payload={"from_family_id": None, "to_family_id": 1, "source": "manual"},
        )
        db_session.add(event)
        db_session.flush()
        assert event.id is not None


# ---------------------------------------------------------------------------
#  6b. CK_EVENT_SUBJECT_BY_TYPE — вычислен СТАНДАЛОНОМ, вне таблицы (R1)
# ---------------------------------------------------------------------------

class TestEventSubjectByTypeExpression:
    """Доработка R1 (и F1 доработки 2): доказывает, что ограничение,
    РЕАЛЬНО стоящее в БД под именем `ck_semantic_events_subject_by_type`,
    держится на ЯВНОМ МНОЖЕСТВЕ пяти типов, а не на префиксе `family_%`, —
    поведенчески, а не по тексту Python-константы.

    F1: читать текст из `models.CK_EVENT_SUBJECT_BY_TYPE`/константы миграции
    было ДЫРОЙ — константу можно оставить нетронутой, подменив предикат прямо
    в вызове `CheckConstraint(...)`, и это ничего не покрасит. Здесь
    выражение читается из `pg_get_constraintdef` (`_live_check_expr`) —
    именно то, что PostgreSQL реально проверяет на вставке.

    Вычисляется ВНЕ таблицы, над произвольной строкой `VALUES`: закрытый
    список `event_type` (`ck_semantic_events_event_type`) — ОТДЕЛЬНОЕ
    ограничение и отверг бы исторический тип `'family_assigned'` раньше, чем
    дело дойдёт до предиката «тип↔предмет», так что на вставках в таблицу
    свидетеля не построить (см. докстринг
    `test_context_family_assigned_with_context_id_passes` выше).
    """

    def _eval(self, db_session, event_type: str, family_id, context_id) -> bool:
        live_expr = _live_check_expr(
            db_session, "semantic_events", "ck_semantic_events_subject_by_type"
        )
        sql = sa.text(
            f"SELECT ({live_expr}) FROM (VALUES "
            "(CAST(:event_type AS text), CAST(:family_id AS bigint), "
            "CAST(:context_id AS bigint))) AS t(event_type, family_id, context_id)"
        )
        return db_session.execute(
            sql, {"event_type": event_type, "family_id": family_id, "context_id": context_id}
        ).scalar_one()

    def test_historical_family_assigned_with_context_subject_passes(self, db_session):
        """Свидетель против префикса. Историческое имя `family_assigned` (до
        переименования в `context_family_assigned`, спека §2.14) — предмет
        этого события ВСЕГДА был контекст, не семья: `family_id` пуст,
        `context_id` заполнен.

        Явное множество ИЗ ПЯТИ типов не содержит `'family_assigned'` вовсе
        (в нём только `family_created/updated/activated/archived/merged`) —
        левая часть равносильности `FALSE`, правая (`family_id IS NOT NULL`)
        тоже `FALSE`, `FALSE = FALSE` даёт `TRUE`: предикат эту строку
        пропускает, как и должен.

        Префиксный предикат `left(event_type,7) = 'family_'` на ЭТОЙ ЖЕ
        строке дал бы `TRUE` (имя начинается с `family_`) — `TRUE = FALSE`
        даёт `FALSE`, то есть отверг бы корректную запись. Это и есть дефект,
        из-за которого событие переименовали в `context_family_assigned`
        (спека §2.14), а предикат переписали на явное множество."""
        assert self._eval(db_session, "family_assigned", None, 5) is True

    def test_family_created_with_family_id_passes(self, db_session):
        assert self._eval(db_session, "family_created", 7, None) is True

    def test_family_created_with_only_context_id_fails(self, db_session):
        assert self._eval(db_session, "family_created", None, 5) is False

    def test_family_type_set_matches_independent_literal(self):
        """Структурная проверка на КОНСТАНТЕ `models.py`/миграции: множество
        типов, разобранное ИЗ ТЕКСТА `CK_EVENT_SUBJECT_BY_TYPE`, равно
        независимо написанному здесь литералу — сверщик не должен делить
        генератор с предикатом, который проверяет
        (`docs/insights/verifier-sharing-predicate-with-generator.md`)."""
        m = _migration_0017()
        match = re.search(r"event_type IN \(([^)]*)\)", m.CK_EVENT_SUBJECT_BY_TYPE)
        assert match is not None
        parsed = {piece.strip().strip("'") for piece in match.group(1).split(",")}
        expected_family_types = {
            "family_created", "family_updated", "family_activated",
            "family_archived", "family_merged",
        }
        assert parsed == expected_family_types

    def test_family_type_set_matches_independent_literal_from_db_text(self, db_session):
        """Та же структурная проверка, но на тексте, который РЕАЛЬНО лежит в
        БД (доработка F1): PostgreSQL нормализует `IN (...)` в
        `= ANY (ARRAY[...])`, поэтому регэксп здесь другой, чем у
        константы — и это отдельный источник, независимый от того, что
        написано в исходниках."""
        live_expr = _live_check_expr(
            db_session, "semantic_events", "ck_semantic_events_subject_by_type"
        )
        match = re.search(r"ARRAY\[([^\]]*)\]", live_expr)
        assert match is not None
        parsed = {
            piece.strip().removesuffix("::text").strip("'")
            for piece in match.group(1).split(",")
        }
        expected_family_types = {
            "family_created", "family_updated", "family_activated",
            "family_archived", "family_merged",
        }
        assert parsed == expected_family_types


# ---------------------------------------------------------------------------
#  7. downgrade — четыре счётчика по отдельности плюс чистая база
# ---------------------------------------------------------------------------

class TestDowngradeRefusalLogic:
    """Доработка R2(a): `_downgrade_refusal` проверена ЧИСТОЙ ЛОГИКОЙ, на
    вручную построенных словарях, без обращения к БД. Мутация, которая
    заставляет отказ смотреть только на часть из четырёх ключей (например,
    только `work_families`/`catalog_contexts`), маскировалась бы в
    live-тестах ниже, потому что там `context_members`/`semantic_events`
    никогда не бывают положительными в одиночку — членство/событие в тестах
    всегда тянет за собой свой контекст. Здесь каждый счётчик — единственный
    положительный, и значение (7) — отличительное, не похожее на количество
    строк, которое могла бы вернуть накопленная фикстура."""

    _ZERO = {"work_families": 0, "catalog_contexts": 0, "context_members": 0, "semantic_events": 0}
    _PHRASE_BY_KEY = {
        "work_families": "семей — 7",
        "catalog_contexts": "контекстов — 7",
        "context_members": "членств — 7",
        "semantic_events": "событий журнала — 7",
    }

    @pytest.mark.parametrize(
        "key", ["work_families", "catalog_contexts", "context_members", "semantic_events"]
    )
    def test_each_counter_alone_blocks_and_is_named(self, key):
        m = _migration_0017()
        blockers = dict(self._ZERO)
        blockers[key] = 7
        refusal = m._downgrade_refusal(blockers)
        assert refusal is not None
        assert self._PHRASE_BY_KEY[key] in refusal

    def test_all_zero_blockers_pass(self):
        m = _migration_0017()
        assert m._downgrade_refusal(dict(self._ZERO)) is None


class TestDowngradeBlockers:
    """Доработка R2(b): живые тесты сверяют ЦЕЛИКОМ словарь `blockers`, и
    данные построены так, чтобы счётчики РАЗЛИЧАЛИСЬ между собой — запрос,
    считающий не ту таблицу (например, `context_members`, реализованный как
    `SELECT count(*) FROM catalog_contexts`), обязан покраснеть даже когда
    оба счётчика одновременно положительны."""

    def test_clean_schema_does_not_block(self, db_session):
        m = _migration_0017()
        assert m._downgrade_refusal(m._downgrade_blockers(db_session.connection())) is None

    def test_work_families_alone_blocks_and_is_named(self, db_session, factories):
        for _ in range(3):
            _family(db_session, factories)
        db_session.flush()
        m = _migration_0017()
        blockers = m._downgrade_blockers(db_session.connection())
        assert blockers == {
            "work_families": 3, "catalog_contexts": 0, "context_members": 0, "semantic_events": 0,
        }
        assert "семей — 3" in m._downgrade_refusal(blockers)

    def test_catalog_contexts_counted_by_their_own_table(self, db_session, factories):
        _context(db_session, factories)
        _context(db_session, factories)
        db_session.flush()
        m = _migration_0017()
        blockers = m._downgrade_blockers(db_session.connection())
        assert blockers == {
            "work_families": 0, "catalog_contexts": 2, "context_members": 0, "semantic_events": 0,
        }
        assert "контекстов — 2" in m._downgrade_refusal(blockers)

    def test_context_members_counted_by_their_own_table_not_contexts(self, db_session, factories):
        """Один контекст, ДВА членства на нём: `catalog_contexts` = 1,
        `context_members` = 2. Запрос, случайно считающий `context_members`
        по `catalog_contexts` (или наоборот), здесь даст 1 = 1 — это и есть
        различающий вход, который равенство целого словаря обязано поймать."""
        ctx = _context(db_session, factories)
        _member(db_session, factories, position_item=_position_item(db_session, factories), context=ctx)
        _member(db_session, factories, position_item=_position_item(db_session, factories), context=ctx)
        db_session.flush()
        m = _migration_0017()
        blockers = m._downgrade_blockers(db_session.connection())
        assert blockers == {
            "work_families": 0, "catalog_contexts": 1, "context_members": 2, "semantic_events": 0,
        }
        assert "членств — 2" in m._downgrade_refusal(blockers)

    def test_semantic_events_counted_by_their_own_table_not_contexts(self, db_session, factories):
        """Один контекст, ДВА события на нём: `catalog_contexts` = 1,
        `semantic_events` = 2 — та же различающая форма, что у членств выше."""
        ctx = _context(db_session, factories)
        for _ in range(2):
            db_session.add(
                SemanticEvent(
                    context_id=ctx.id, event_type="context_created",
                    payload={"bucket_id": ctx.bucket_id, "origin": "import"},
                )
            )
        db_session.flush()
        m = _migration_0017()
        blockers = m._downgrade_blockers(db_session.connection())
        assert blockers == {
            "work_families": 0, "catalog_contexts": 1, "context_members": 0, "semantic_events": 2,
        }
        assert "событий журнала — 2" in m._downgrade_refusal(blockers)


class TestDowngradeFifthInputReal:
    """Доработка R6: пятый вход `downgrade` — РЕАЛЬНЫЙ прогон `alembic
    upgrade head` → `downgrade 0016` → `upgrade head` на ОТДЕЛЬНОЙ
    scratch-базе, которую тест сам создаёт и сам удаляет. Сессионная тестовая
    БД (`db_engine`, накатанная один раз на весь прогон) не трогается вовсе —
    `downgrade` на ней сломал бы все остальные тесты файла.

    Создание базы — тем же приёмом, что `db_engine` в `conftest.py`
    (`_create_worker_database`, барьер `ensure_mutation_allowed`); имя
    оканчивается на `_test`, чтобы не выделяться среди баз кластера.
    """

    def test_downgrade_then_upgrade_round_trip_on_clean_scratch_db(self):
        test_url = os.environ.get("TEST_DATABASE_URL")
        if not test_url:
            pytest.skip("TEST_DATABASE_URL не задан")

        import psycopg
        from psycopg import sql
        from sqlalchemy.engine import make_url

        from db_guard import ensure_mutation_allowed
        from tests.conftest import _create_worker_database

        parsed = make_url(test_url)
        base = (parsed.database or "gca").removesuffix("_test") or "gca"
        scratch_name = f"{base}_scratch_{_uid()}_test"
        scratch_url = parsed.set(database=scratch_name).render_as_string(hide_password=False)

        ensure_mutation_allowed(scratch_url, "test_semantic_schema scratch db (R6)")
        _create_worker_database(scratch_url)

        try:
            from alembic import command
            from alembic.config import Config

            backend_root = Path(__file__).resolve().parents[2]
            cfg = Config(str(backend_root / "alembic.ini"))
            cfg.set_main_option("script_location", str(backend_root / "alembic"))
            cfg.set_main_option("sqlalchemy.url", scratch_url)

            environ_snapshot = dict(os.environ)
            try:
                command.upgrade(cfg, "head")

                command.downgrade(cfg, "0016")
                # F3 доработки 2: сам downgrade обязан быть под замком — убрать
                # вызов command.downgrade выше и тест обязан покраснеть здесь,
                # потому что шесть таблиц НЕ исчезли бы.
                gone_engine = sa.create_engine(scratch_url)
                try:
                    with gone_engine.connect() as conn:
                        six_tables_gone = conn.execute(
                            sa.text(
                                "SELECT to_regclass('public.work_families') IS NULL "
                                "AND to_regclass('public.context_buckets') IS NULL "
                                "AND to_regclass('public.catalog_contexts') IS NULL "
                                "AND to_regclass('public.context_routing_rules') IS NULL "
                                "AND to_regclass('public.context_members') IS NULL "
                                "AND to_regclass('public.semantic_events') IS NULL"
                            )
                        ).scalar_one()
                finally:
                    gone_engine.dispose()
                assert six_tables_gone is True

                command.upgrade(cfg, "head")
            finally:
                os.environ.clear()
                os.environ.update(environ_snapshot)

            check_engine = sa.create_engine(scratch_url)
            try:
                with check_engine.connect() as conn:
                    six_tables_exist = conn.execute(
                        sa.text(
                            "SELECT to_regclass('public.work_families') IS NOT NULL "
                            "AND to_regclass('public.context_buckets') IS NOT NULL "
                            "AND to_regclass('public.catalog_contexts') IS NOT NULL "
                            "AND to_regclass('public.context_routing_rules') IS NOT NULL "
                            "AND to_regclass('public.context_members') IS NOT NULL "
                            "AND to_regclass('public.semantic_events') IS NOT NULL"
                        )
                    ).scalar_one()
            finally:
                check_engine.dispose()
            assert six_tables_exist is True
        finally:
            with psycopg.connect(
                host=parsed.host, port=parsed.port, user=parsed.username,
                password=parsed.password, dbname="postgres", autocommit=True,
            ) as conn:
                conn.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(scratch_name)
                    )
                )


# ---------------------------------------------------------------------------
#  Частичные индексы context_members — предикат закреплён буквально (R7)
# ---------------------------------------------------------------------------

class TestPartialIndexPredicates:
    """`alembic check` видит только СУЩЕСТВОВАНИЕ индекса, не его `WHERE`
    (тот же принцип, что у `CHECK`/`Computed` — `docs/pitfalls/db.md`).
    Предикат каждого частичного индекса `context_members` закреплён здесь
    буквально, через `pg_indexes.indexdef`."""

    @pytest.mark.parametrize(
        ("index_name", "predicate_fragment"),
        [
            ("idx_context_members_context_id_stale", "membership_state = 'STALE'"),
            ("idx_context_members_context_id_conflict", "conflict_at IS NOT NULL"),
        ],
    )
    def test_partial_index_predicate_is_pinned(self, db_session, index_name, predicate_fragment):
        indexdef = db_session.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
            {"name": index_name},
        ).scalar_one()
        assert predicate_fragment in indexdef

    def test_plain_sibling_index_has_no_where_clause(self, db_session):
        """`idx_context_members_context_id` — обычный индекс, без `WHERE`;
        граница отличает его от двух частичных соседей выше."""
        indexdef = db_session.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
            {"name": "idx_context_members_context_id"},
        ).scalar_one()
        assert "WHERE" not in indexdef.upper()


# ---------------------------------------------------------------------------
#  8. Parity: CHECK-константы миграции 0017 побуквенно равны models.py
# ---------------------------------------------------------------------------

class TestParityWithMigration:
    """R3: parity покрывает КАЖДЫЙ CHECK шести таблиц, а не только одиннадцать
    именованных `CK_*` — десять списков `IN (...)` (статусы, виды, источники,
    роли, причина несравнимости, состояния членства/маршрутизации, закрытый
    список событий) тоже CHECK, и до доработки ничто не ловило их расхождение
    между `models.py` и миграцией."""

    #: Доработка F5: `ids=` — имя константы, а не длинная строка значения, чтобы
    #: упавший случай назывался в отчёте по имени.
    _CASES = [
        (CK_FAMILY_ACTIVE_NEEDS_DEFINITION, "CK_FAMILY_ACTIVE_NEEDS_DEFINITION"),
        (CK_FAMILY_ACTIVATION_PAIR, "CK_FAMILY_ACTIVATION_PAIR"),
        (CK_FAMILY_AUTHOR_IFF_NOT_SEED, "CK_FAMILY_AUTHOR_IFF_NOT_SEED"),
        (CK_CONTEXT_FAMILY_PROVENANCE, "CK_CONTEXT_FAMILY_PROVENANCE"),
        (CK_CONTEXT_KIND_SOURCE_PAIR, "CK_CONTEXT_KIND_SOURCE_PAIR"),
        (CK_CONTEXT_NAME_ROLE_SOURCE_PAIR, "CK_CONTEXT_NAME_ROLE_SOURCE_PAIR"),
        (CK_MEMBER_RULE_PAIR, "CK_MEMBER_RULE_PAIR"),
        (CK_MEMBER_CONFLICT_PAIR, "CK_MEMBER_CONFLICT_PAIR"),
        (CK_EVENT_ONE_SUBJECT, "CK_EVENT_ONE_SUBJECT"),
        (CK_EVENT_SUBJECT_BY_TYPE, "CK_EVENT_SUBJECT_BY_TYPE"),
        (CK_EVENT_PAYLOAD_NOT_EMPTY, "CK_EVENT_PAYLOAD_NOT_EMPTY"),
        (FAMILY_STATUSES, "FAMILY_STATUSES"),
        (SEMANTIC_KINDS, "SEMANTIC_KINDS"),
        (NAME_ROLES, "NAME_ROLES"),
        (SEMANTIC_STATES, "SEMANTIC_STATES"),
        (MEMBERSHIP_STATES, "MEMBERSHIP_STATES"),
        (DECISION_SOURCES, "DECISION_SOURCES"),
        (FAMILY_SOURCES, "FAMILY_SOURCES"),
        (ROUTED_BY_VALUES, "ROUTED_BY_VALUES"),
        (COMPARABILITY_REASONS, "COMPARABILITY_REASONS"),
        (SEMANTIC_EVENT_TYPES_SQL, "SEMANTIC_EVENT_TYPES"),
    ]

    @pytest.mark.parametrize(
        ("model_expr", "migration_name"), _CASES, ids=[name for _, name in _CASES],
    )
    def test_check_expressions_match(self, model_expr, migration_name):
        assert model_expr == getattr(_migration_0017(), migration_name)


# ---------------------------------------------------------------------------
#  9. Все шесть таблиц — явно в _DOMAIN_TABLES
# ---------------------------------------------------------------------------

class TestDomainTablesCleanup:
    def test_all_six_tables_listed_explicitly(self):
        from tests.conftest import _DOMAIN_TABLES

        for table in (
            "work_families", "context_buckets", "catalog_contexts",
            "context_routing_rules", "context_members", "semantic_events",
        ):
            assert table in _DOMAIN_TABLES

    def test_work_families_cleared_by_domain_tables_truncate(
        self, db_engine, committing_session_factory
    ):
        """`work_families` каскадом НЕ очистится — на неё ссылается
        `catalog_contexts`, а не наоборот, и `TRUNCATE catalog_positions
        CASCADE` до неё не доходит. Убрать `work_families` из `_DOMAIN_TABLES`
        — и этот тест обязан покраснеть."""
        session = committing_session_factory()
        session.add(
            WorkFamily(
                title=f"Уборка после теста {_uid()}", status=FamilyStatus.draft.value,
                seed_key=f"cleanup-{_uid()}",
            )
        )
        session.commit()
        session.close()

        from tests.conftest import _truncate_domain_tables

        _truncate_domain_tables(db_engine)

        with db_engine.connect() as conn:
            count = conn.execute(sa.text("SELECT count(*) FROM work_families")).scalar_one()
        assert count == 0
