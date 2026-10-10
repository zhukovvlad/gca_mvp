"""Схема открытия семей и категорий (миграция 0021; спека
`2026-10-09-catalog-discovery-design.md` §2.2): каждое ограничение доказано
пробоем — `INSERT`/`UPDATE` с ожиданием `IntegrityError`, по одному нарушенному
ограничению на вход, имя ограничения — по `diag.constraint_name` драйвера, и
соседним допустимым входом. `upgrade` на заданиях всех трёх прежних видов и
`downgrade` по каждому из шести носителей — на отдельной scratch-базе
(`@pytest.mark.migration_roundtrip`).

Порядок проверки CHECK в PostgreSQL — по имени ограничения (алфавит): когда
вход нарушает два ограничения разом, отказ приходит от первого по алфавиту;
такие входы названы в тесте и допускают оба имени.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import itertools
import re
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from models import (
    CATEGORY_PROPOSAL_STATUSES,
    CK_DRAFT_ACTIVATED_PAIR,
    CK_DRAFT_DECIDED_AT_PAIR,
    CK_DRAFT_DECIDED_BY_PAIR,
    CK_DRAFT_DECISION_ONLY_NEW,
    CK_DRAFT_EDITED_PAIR,
    CK_DRAFT_MERGE_TARGET_AT_MOST_ONE,
    CK_DRAFT_MERGED_PAIR,
    CK_DRAFT_NOT_SELF_MERGED,
    CK_DRAFT_SHAPE,
    CK_FAMILY_CATEGORY_DEFINITION_NOT_BLANK,
    CK_FAMILY_CATEGORY_TITLE_NOT_BLANK,
    CK_PROPOSAL_APPLIED_PAIR,
    CK_PROPOSAL_DECIDED_AT_PAIR,
    CK_SEMANTIC_JOBS_CONTEXT_SUBJECT,
    CK_SEMANTIC_JOBS_DISCOVERY_SUBJECT,
    CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND,
    DRAFT_GROUPS,
    DRAFT_STATUSES,
    FAMILY_CATEGORY_SEED_KEYS,
    SEMANTIC_EVENT_TYPES_SQL,
    SEMANTIC_JOB_KINDS,
    CategoryProposalStatus,
    DraftGroup,
    DraftStatus,
    FamilyCategory,
    FamilyCategoryProposal,
    FamilyDraft,
    FamilyDraftMember,
    SemanticEvent,
    SemanticJobKind,
    UnitOfMeasure,
    WorkFamily,
)
from tests.integration.test_semantic_queue_schema import _context, _job
from tests.integration.test_work_variants_schema import (
    _event,
    _family,
    _live_constraint_names,
    _scratch_alembic,
    _uid,
)

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
_ORDINALS = itertools.count(1)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _load_migration_0021():
    import importlib.util

    path = next(BACKEND_ROOT.glob("alembic/versions/*0021-catalog_discovery.py"))
    spec = importlib.util.spec_from_file_location("_migration_0021_schema_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def rejected_by(session, constraint: str | set[str]):
    """Блок обязан закончиться `IntegrityError` от названного ограничения — имя
    берётся из `diag.constraint_name`, а не из текста сообщения."""
    allowed = {constraint} if isinstance(constraint, str) else set(constraint)
    with pytest.raises(IntegrityError) as exc, session.begin_nested():
        yield
        session.flush()
    assert exc.value.orig.diag.constraint_name in allowed, (
        f"нарушено {exc.value.orig.diag.constraint_name!r}, ожидалось одно из {sorted(allowed)}"
    )


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _user_id(factories) -> int:
    return factories.UserFactory.create().id


def _category(session, **overrides) -> FamilyCategory:
    """Допустимая категория: по умолчанию «заведённая миграцией» (есть ключ, нет
    автора); с `created_by` и без `seed_key` — категория оператора. Тест нарушения
    переопределяет ровно одно поле."""
    defaults = dict(title=f"Категория {_uid()}", definition="Определение категории")
    if overrides.get("created_by") is None:
        defaults["seed_key"] = f"seed-{_uid()}"
    else:
        defaults["seed_key"] = None
    defaults.update(overrides)
    category = FamilyCategory(**defaults)
    session.add(category)
    session.flush()
    return category


def _seed_category(session, seed_key: str = "work") -> FamilyCategory:
    return session.query(FamilyCategory).filter_by(seed_key=seed_key).one()


def _unit_ids(session, count: int) -> list[int]:
    rows = session.execute(sa.select(UnitOfMeasure.id).order_by(UnitOfMeasure.id).limit(count)).all()
    return [row[0] for row in rows]


def _discovery_job(session, factories, *, status="pending", unit_id=None, **overrides):
    """Открытие единицы: ни контекста, ни семьи. Поля статуса, которых требуют
    его равносильности, подставляются по статусу."""
    defaults = dict(
        kind="family_discovery", context_id=None, family_id=None, schema_id=None,
        unit_id=unit_id, status=status,
    )
    if status == "running":
        defaults["claim_token"] = uuid.uuid4()
    if status == "cancelled":
        defaults["cancel_reason"] = "input_changed"
    if status == "privacy_hold":
        defaults["privacy_matches"] = [{"text": "x", "kind": "contractor", "where": "context"}]
    defaults.update(overrides)
    return _job(session, factories, **defaults)


def _draft(session, job, *, grp="new", status="open", **overrides) -> FamilyDraft:
    """Допустимая группа заданного вида; тест нарушения переопределяет ровно одно
    поле."""
    defaults = dict(job_id=job.id, ordinal=next(_ORDINALS), grp=grp, status=status)
    if grp == "new":
        defaults.update(title=f"Семья {_uid()}", definition="Определение семьи")
    elif grp == "existing":
        defaults.update(existing_family_id=_family(session).id)
    defaults.update(overrides)
    draft = FamilyDraft(**defaults)
    session.add(draft)
    session.flush()
    return draft


def _decided(factories, **extra) -> dict:
    """Автор и время решения — пара, которой требует статус решения."""
    return dict(decided_by=_user_id(factories), decided_at=_now(), **extra)


def _member(session, draft, context, *, name_index=0) -> FamilyDraftMember:
    member = FamilyDraftMember(
        draft_id=draft.id, job_id=draft.job_id, context_id=context.id, name_index=name_index
    )
    session.add(member)
    session.flush()
    return member


def _proposal(session, job, family, category, *, status="open", **overrides):
    defaults = dict(
        job_id=job.id, family_id=family.id, family_category_id=category.id, status=status
    )
    defaults.update(overrides)
    proposal = FamilyCategoryProposal(**defaults)
    session.add(proposal)
    session.flush()
    return proposal


# ---------------------------------------------------------------------------
#  1. Справочник категорий: три строки миграции и его ограничения
# ---------------------------------------------------------------------------

class TestSeededCategories:
    def test_seed_keys_constant_equals_independent_literal(self):
        assert FAMILY_CATEGORY_SEED_KEYS == ("work", "engineering_system", "costs_services")

    def test_exactly_the_three_seed_rows_exist(self, db_session):
        rows = db_session.execute(
            sa.select(FamilyCategory.seed_key, FamilyCategory.title).order_by(FamilyCategory.id)
        ).all()
        assert [row.seed_key for row in rows] == ["work", "engineering_system", "costs_services"]
        assert [row.title for row in rows] == [
            "Работа", "Инженерная система", "Затраты и услуги",
        ]

    def test_seed_rows_have_definitions_and_no_author(self, db_session):
        for category in db_session.query(FamilyCategory).all():
            assert category.definition.strip() != ""
            assert category.created_by is None

    def test_costs_definition_is_the_mockup_text(self, db_session):
        definition = _seed_category(db_session, "costs_services").definition
        assert definition.startswith("Не работа на объекте, а обеспечение и сопровождение")
        assert definition.endswith("временные здания, документация.")


class TestCategoryConstraints:
    @pytest.mark.parametrize("title", ["", " ", "   "])
    def test_blank_title_rejected(self, db_session, title):
        with rejected_by(db_session, "ck_family_categories_title_not_blank"):
            _category(db_session, title=title)

    @pytest.mark.parametrize("definition", ["", " ", "   "])
    def test_blank_definition_rejected(self, db_session, definition):
        with rejected_by(db_session, "ck_family_categories_definition_not_blank"):
            _category(db_session, definition=definition)

    def test_non_blank_title_and_definition_pass(self, db_session):
        assert _category(db_session, title="  Х  ", definition=" . ").id is not None

    @pytest.mark.parametrize("title", ["работа", "РАБОТА", "  Работа  ", " рАбОтА "])
    def test_duplicate_title_ignoring_case_and_edge_spaces_rejected(self, db_session, title):
        with rejected_by(db_session, "uq_family_categories_title"):
            _category(db_session, title=title)

    def test_title_differing_inside_passes(self, db_session):
        assert _category(db_session, title="Ра бота").id is not None

    def test_category_without_seed_key_and_without_author_rejected(self, db_session):
        with rejected_by(db_session, "ck_family_categories_author_iff_not_seed"):
            _category(db_session, seed_key=None, created_by=None)

    def test_category_with_seed_key_and_author_rejected(self, db_session, factories):
        with rejected_by(db_session, "ck_family_categories_author_iff_not_seed"):
            _category(db_session, seed_key=f"seed-{_uid()}", created_by=_user_id(factories))

    def test_seed_category_without_author_passes(self, db_session):
        assert _category(db_session, seed_key=f"seed-{_uid()}", created_by=None).id is not None

    def test_operator_category_with_author_passes(self, db_session, factories):
        assert _category(db_session, seed_key=None, created_by=_user_id(factories)).id is not None

    def test_duplicate_seed_key_rejected(self, db_session):
        with rejected_by(db_session, "uq_family_categories_seed_key"):
            _category(db_session, seed_key="work", created_by=None)

    def test_delete_category_with_family_is_restricted(self, db_session):
        category = _seed_category(db_session, "work")
        _family(db_session, family_category_id=category.id)
        with rejected_by(db_session, "fk_work_families_family_category_id"):
            db_session.execute(sa.delete(FamilyCategory).where(FamilyCategory.id == category.id))

    def test_family_without_category_passes(self, db_session):
        assert _family(db_session).family_category_id is None

    def test_family_with_missing_category_rejected(self, db_session):
        with rejected_by(db_session, "fk_work_families_family_category_id"):
            _family(db_session, family_category_id=999_999_999)

    def test_active_family_without_category_passes_there_is_no_check(self, db_session):
        """Решение 10: CHECK-а «активна ⟹ категория» нет — у ранее активных
        семей поле пусто до прохода разметки."""
        names = _live_constraint_names(db_session, "work_families", "c")
        assert not any("category" in name for name in names)


# ---------------------------------------------------------------------------
#  2. Задания: четвёртый вид, предмет и ключ живого открытия
# ---------------------------------------------------------------------------

class TestDiscoveryJobKind:
    def test_discovery_job_without_context_and_family_passes(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        assert (job.kind, job.context_id, job.family_id) == ("family_discovery", None, None)

    def test_discovery_job_with_context_rejected(self, db_session, factories):
        """Нарушает и предмет-контекст, и предмет открытия; по алфавиту первым
        срабатывает `ck_semantic_jobs_context_subject`."""
        context = _context(db_session, factories)
        with rejected_by(
            db_session, {"ck_semantic_jobs_context_subject", "ck_semantic_jobs_discovery_subject"}
        ):
            _discovery_job(db_session, factories, context_id=context.id)

    def test_discovery_job_with_family_rejected(self, db_session, factories):
        """Нарушает предмет открытия и предмет схемы; по алфавиту первым —
        `ck_semantic_jobs_discovery_subject`."""
        family = _family(db_session)
        with rejected_by(
            db_session, {"ck_semantic_jobs_discovery_subject", "ck_semantic_jobs_schema_subject"}
        ):
            _discovery_job(db_session, factories, family_id=family.id)

    def test_discovery_subject_expression_is_what_forbids_context_and_family(self, db_session):
        """Предикат сам по себе: открытие без предмета — истина, с любым из двух —
        ложь, прочие виды ограничением не затрагиваются; ни одна ветвь не даёт NULL."""
        rows = db_session.execute(
            sa.text(
                "SELECT k, c, f, (k <> 'family_discovery' OR (c IS NULL AND f IS NULL)) "
                "FROM (VALUES ('family_discovery', NULL::int, NULL::int), "
                "('family_discovery', 1, NULL), ('family_discovery', NULL, 1), "
                "('family_discovery', 1, 1), ('family_suggestion', 1, 1), "
                "('family_schema', NULL, 1)) AS t(k, c, f)"
            )
        ).all()
        assert [row[3] for row in rows] == [True, False, False, False, True, True]

    def test_discovery_job_with_schema_rejected(self, db_session, factories):
        """Версия схемы бывает только у `family_schema` и `context_values`."""
        with rejected_by(db_session, "ck_semantic_jobs_schema_id_by_kind"):
            _discovery_job(db_session, factories, schema_id=1)

    def test_suggestion_job_without_context_rejected(self, db_session, factories):
        with rejected_by(db_session, "ck_semantic_jobs_context_subject"):
            _job(db_session, factories, kind="family_suggestion", context_id=None)

    def test_context_values_job_without_context_rejected(self, db_session, factories):
        with rejected_by(db_session, "ck_semantic_jobs_context_subject"):
            _job(db_session, factories, kind="context_values", context_id=None, schema_id=1)

    def test_context_values_job_without_schema_rejected(self, db_session, factories):
        with rejected_by(db_session, "ck_semantic_jobs_schema_id_by_kind"):
            _job(db_session, factories, kind="context_values")

    def test_schema_job_without_schema_id_rejected(self, db_session, factories):
        family = _family(db_session)
        with rejected_by(db_session, "ck_semantic_jobs_schema_id_by_kind"):
            _job(
                db_session, factories, kind="family_schema", context_id=None, family_id=family.id,
            )

    def test_suggestion_job_with_context_passes(self, db_session, factories):
        assert _job(db_session, factories).kind == "family_suggestion"

    def test_unknown_kind_rejected(self, db_session, factories):
        with rejected_by(db_session, "ck_semantic_jobs_kind"):
            _job(db_session, factories, kind="family_discover", context_id=None)

    def test_kind_enum_has_the_four_kinds(self):
        assert {kind.value for kind in SemanticJobKind} == {
            "family_suggestion", "family_schema", "context_values", "family_discovery",
        }


class TestDiscoveryLiveKey:
    """`UNIQUE (COALESCE(unit_id, -1)) WHERE kind = 'family_discovery' AND status
    IN ('pending', 'running', 'privacy_hold')`."""

    _LIVE = ["pending", "running", "privacy_hold"]
    _TERMINAL = ["done", "error", "cancelled"]

    @pytest.mark.parametrize("second", _LIVE)
    @pytest.mark.parametrize("first", _LIVE)
    def test_second_live_discovery_of_one_unit_rejected(self, db_session, factories, first, second):
        unit_id = _unit_ids(db_session, 1)[0]
        _discovery_job(db_session, factories, status=first, unit_id=unit_id)
        with rejected_by(db_session, "uq_semantic_jobs_discovery_live"):
            _discovery_job(db_session, factories, status=second, unit_id=unit_id)

    @pytest.mark.parametrize("second", _LIVE)
    def test_second_live_discovery_of_the_null_unit_rejected(self, db_session, factories, second):
        """«Без единицы» — законная единица: два `NULL` не считаются разными."""
        _discovery_job(db_session, factories, unit_id=None)
        with rejected_by(db_session, "uq_semantic_jobs_discovery_live"):
            _discovery_job(db_session, factories, status=second, unit_id=None)

    def test_live_discovery_of_other_unit_passes(self, db_session, factories):
        first, second = _unit_ids(db_session, 2)
        _discovery_job(db_session, factories, unit_id=first)
        assert _discovery_job(db_session, factories, unit_id=second).id is not None

    def test_live_discovery_of_the_null_unit_and_of_a_real_unit_pass_together(
        self, db_session, factories
    ):
        _discovery_job(db_session, factories, unit_id=None)
        assert _discovery_job(db_session, factories, unit_id=_unit_ids(db_session, 1)[0]).id

    @pytest.mark.parametrize("terminal", _TERMINAL)
    @pytest.mark.parametrize("unit_is_null", [False, True])
    def test_after_the_first_finishes_the_second_passes(
        self, db_session, factories, terminal, unit_is_null
    ):
        unit_id = None if unit_is_null else _unit_ids(db_session, 1)[0]
        _discovery_job(db_session, factories, status=terminal, unit_id=unit_id)
        assert _discovery_job(db_session, factories, unit_id=unit_id).id is not None

    def test_many_finished_discoveries_of_one_unit_pass(self, db_session, factories):
        unit_id = _unit_ids(db_session, 1)[0]
        for status in self._TERMINAL:
            _discovery_job(db_session, factories, status=status, unit_id=unit_id)

    def test_suggestion_job_of_the_same_unit_does_not_conflict(self, db_session, factories):
        unit_id = _unit_ids(db_session, 1)[0]
        _job(db_session, factories, unit_id=unit_id)
        assert _discovery_job(db_session, factories, unit_id=unit_id).id is not None

    def test_finishing_the_live_discovery_frees_the_unit(self, db_session, factories):
        unit_id = _unit_ids(db_session, 1)[0]
        first = _discovery_job(db_session, factories, unit_id=unit_id)
        first.status = "done"
        db_session.flush()
        assert _discovery_job(db_session, factories, unit_id=unit_id).id is not None


# ---------------------------------------------------------------------------
#  3. Группа ответа: форма, решения, слияние
# ---------------------------------------------------------------------------

class TestDraftShape:
    @pytest.mark.parametrize("grp", ["new", "existing", "not_work"])
    def test_group_of_each_kind_passes(self, db_session, factories, grp):
        job = _discovery_job(db_session, factories)
        assert _draft(db_session, job, grp=grp).grp == grp

    def test_new_group_with_category_and_similar_family_passes(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        draft = _draft(
            db_session, job, family_category_id=_seed_category(db_session).id,
            similar_family_id=_family(db_session).id,
        )
        assert (draft.family_category_id, draft.similar_family_id) != (None, None)

    def test_new_group_without_category_passes(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        assert _draft(db_session, job).family_category_id is None

    @pytest.mark.parametrize(
        "overrides",
        [
            {"title": None},
            {"definition": None},
            {"title": ""},
            {"title": "   "},
            {"definition": ""},
            {"definition": "   "},
        ],
        ids=["title-null", "definition-null", "title-empty", "title-blank",
             "definition-empty", "definition-blank"],
    )
    def test_new_group_without_name_or_definition_rejected(
        self, db_session, factories, overrides
    ):
        job = _discovery_job(db_session, factories)
        with rejected_by(db_session, "ck_family_drafts_shape"):
            _draft(db_session, job, **overrides)

    def test_new_group_with_existing_family_rejected(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        with rejected_by(db_session, "ck_family_drafts_shape"):
            _draft(db_session, job, existing_family_id=_family(db_session).id)

    @pytest.mark.parametrize(
        "extra",
        ["title", "definition", "family_category_id", "similar_family_id"],
    )
    def test_existing_group_with_extra_field_rejected(self, db_session, factories, extra):
        job = _discovery_job(db_session, factories)
        values = {
            "title": "Имя", "definition": "Определение",
            "family_category_id": _seed_category(db_session).id,
            "similar_family_id": _family(db_session).id,
        }
        with rejected_by(db_session, "ck_family_drafts_shape"):
            _draft(db_session, job, grp="existing", **{extra: values[extra]})

    def test_existing_group_without_family_rejected(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        with rejected_by(db_session, "ck_family_drafts_shape"):
            _draft(db_session, job, grp="existing", existing_family_id=None)

    @pytest.mark.parametrize(
        "extra",
        ["title", "definition", "family_category_id", "existing_family_id", "similar_family_id"],
    )
    def test_not_work_group_with_any_field_rejected(self, db_session, factories, extra):
        job = _discovery_job(db_session, factories)
        values = {
            "title": "Имя", "definition": "Определение",
            "family_category_id": _seed_category(db_session).id,
            "existing_family_id": _family(db_session).id,
            "similar_family_id": _family(db_session).id,
        }
        with rejected_by(db_session, "ck_family_drafts_shape"):
            _draft(db_session, job, grp="not_work", **{extra: values[extra]})

    def test_unknown_group_rejected(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        with rejected_by(db_session, "ck_family_drafts_grp"):
            _draft(db_session, job, grp="bogus", title="Имя", definition="Определение")

    def test_unknown_status_rejected(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        with rejected_by(db_session, "ck_family_drafts_status"):
            _draft(db_session, job, status="bogus")

    def test_shape_expression_is_total_for_every_group(self, db_session):
        """Ни одна ветвь предиката не даёт NULL: для имени NULL ветвь `new` — FALSE."""
        rows = db_session.execute(
            sa.text(
                "SELECT grp, ok FROM (SELECT grp, "
                "(" + CK_DRAFT_SHAPE + ") AS ok FROM (VALUES "
                "('new', NULL::text, 'd', NULL::bigint, NULL::bigint, NULL::bigint), "
                "('new', 't', NULL, NULL, NULL, NULL), "
                "('existing', NULL, NULL, NULL, NULL, NULL), "
                "('not_work', NULL, NULL, NULL, 1, NULL)"
                ") AS d(grp, title, definition, family_category_id, existing_family_id, "
                "similar_family_id)) s"
            )
        ).all()
        assert [row.ok for row in rows] == [False, False, False, False]

    def test_dropping_title_is_not_null_lets_a_null_title_through(self, db_session, factories):
        """Чем держится ветвь `new`: без `title IS NOT NULL` выражение
        `btrim(NULL) <> ''` даёт NULL, и CHECK пропускает новый черновик без имени.
        Ограничение подменяется внутри транзакции теста и откатывается вместе с ней."""
        weakened = CK_DRAFT_SHAPE.replace("title IS NOT NULL AND ", "", 1)
        assert weakened != CK_DRAFT_SHAPE
        job = _discovery_job(db_session, factories)
        db_session.execute(sa.text("ALTER TABLE family_drafts DROP CONSTRAINT ck_family_drafts_shape"))
        db_session.execute(
            sa.text(f"ALTER TABLE family_drafts ADD CONSTRAINT ck_family_drafts_shape CHECK ({weakened})")
        )
        assert _draft(db_session, job, title=None).title is None


class TestDraftDecisionColumns:
    def test_activated_without_family_rejected(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        with rejected_by(db_session, "ck_family_drafts_activated_pair"):
            _draft(db_session, job, status="activated", **_decided(factories))

    def test_activated_with_family_passes(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        draft = _draft(
            db_session, job, status="activated",
            activated_family_id=_family(db_session).id, **_decided(factories),
        )
        assert draft.activated_family_id is not None

    def test_open_with_activated_family_rejected(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        with rejected_by(db_session, "ck_family_drafts_activated_pair"):
            _draft(db_session, job, activated_family_id=_family(db_session).id)

    def test_merged_into_family_passes(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        draft = _draft(
            db_session, job, status="merged", merged_into_family_id=_family(db_session).id,
            **_decided(factories),
        )
        assert draft.merged_into_family_id is not None

    def test_merged_into_draft_of_the_same_job_passes(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        target = _draft(db_session, job)
        merged = _draft(
            db_session, job, status="merged", merged_into_draft_id=target.id, **_decided(factories)
        )
        assert merged.merged_into_draft_id == target.id

    def test_merged_without_target_rejected(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        with rejected_by(db_session, "ck_family_drafts_merged_pair"):
            _draft(db_session, job, status="merged", **_decided(factories))

    def test_open_with_a_target_rejected(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        with rejected_by(db_session, "ck_family_drafts_merged_pair"):
            _draft(db_session, job, merged_into_family_id=_family(db_session).id)

    def test_two_targets_rejected(self, db_session, factories):
        """Статус `open` не требует цели, поэтому нарушено ровно ограничение «не
        больше одной цели»."""
        job = _discovery_job(db_session, factories)
        target = _draft(db_session, job)
        with rejected_by(db_session, "ck_family_drafts_merge_target_at_most_one"):
            _draft(
                db_session, job, merged_into_draft_id=target.id,
                merged_into_family_id=_family(db_session).id,
            )

    def test_merge_into_itself_rejected(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        draft = _draft(db_session, job, status="merged", merged_into_family_id=_family(db_session).id,
                       **_decided(factories))
        with rejected_by(db_session, "ck_family_drafts_not_self_merged"):
            db_session.execute(
                sa.text(
                    "UPDATE family_drafts SET merged_into_family_id = NULL, "
                    "merged_into_draft_id = id WHERE id = :id"
                ),
                {"id": draft.id},
            )

    def test_merge_into_a_draft_of_another_job_rejected(self, db_session, factories):
        first = _discovery_job(db_session, factories, unit_id=None)
        second = _discovery_job(db_session, factories, status="done", unit_id=None)
        target = _draft(db_session, second)
        with rejected_by(db_session, "fk_family_drafts_merged_into_job"):
            _draft(
                db_session, first, status="merged", merged_into_draft_id=target.id,
                **_decided(factories),
            )

    @pytest.mark.parametrize("grp", ["existing", "not_work"])
    @pytest.mark.parametrize("status", ["activated", "merged", "discarded"])
    def test_decision_on_existing_or_not_work_group_rejected(
        self, db_session, factories, grp, status
    ):
        job = _discovery_job(db_session, factories)
        extra = {}
        if status == "activated":
            extra["activated_family_id"] = _family(db_session).id
        if status == "merged":
            extra["merged_into_family_id"] = _family(db_session).id
        with rejected_by(db_session, "ck_family_drafts_decision_only_new"):
            _draft(db_session, job, grp=grp, status=status, **_decided(factories), **extra)

    @pytest.mark.parametrize("grp", ["existing", "not_work"])
    @pytest.mark.parametrize("status", ["open", "superseded"])
    def test_existing_or_not_work_group_stays_open_or_is_superseded(
        self, db_session, factories, grp, status
    ):
        job = _discovery_job(db_session, factories)
        assert _draft(db_session, job, grp=grp, status=status).status == status

    @pytest.mark.parametrize("status", ["open", "superseded"])
    def test_undecided_new_group_passes(self, db_session, factories, status):
        job = _discovery_job(db_session, factories)
        assert _draft(db_session, job, status=status).decided_by is None

    def test_discarded_new_group_with_author_passes(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        assert _draft(db_session, job, status="discarded", **_decided(factories)).decided_by

    @pytest.mark.parametrize("status", ["discarded", "activated", "merged"])
    def test_decision_without_author_rejected(self, db_session, factories, status):
        job = _discovery_job(db_session, factories)
        extra = {}
        if status == "activated":
            extra["activated_family_id"] = _family(db_session).id
        if status == "merged":
            extra["merged_into_family_id"] = _family(db_session).id
        with rejected_by(db_session, "ck_family_drafts_decided_by_pair"):
            _draft(db_session, job, status=status, **extra)

    @pytest.mark.parametrize("status", ["open", "superseded"])
    def test_author_on_undecided_group_rejected(self, db_session, factories, status):
        job = _discovery_job(db_session, factories)
        with rejected_by(db_session, "ck_family_drafts_decided_by_pair"):
            _draft(db_session, job, status=status, **_decided(factories))

    def test_decision_time_without_author_rejected(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        with rejected_by(db_session, "ck_family_drafts_decided_at_pair"):
            _draft(db_session, job, decided_at=_now())

    def test_author_without_decision_time_rejected(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        with rejected_by(db_session, "ck_family_drafts_decided_at_pair"):
            _draft(db_session, job, status="discarded", decided_by=_user_id(factories))

    def test_editor_without_edit_time_rejected(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        with rejected_by(db_session, "ck_family_drafts_edited_pair"):
            _draft(db_session, job, edited_by=_user_id(factories))

    def test_edit_time_without_editor_rejected(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        with rejected_by(db_session, "ck_family_drafts_edited_pair"):
            _draft(db_session, job, edited_at=_now())

    def test_edited_group_with_both_passes(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        assert _draft(db_session, job, edited_by=_user_id(factories), edited_at=_now()).edited_by


class TestDraftKeys:
    def test_one_not_work_group_per_job(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        _draft(db_session, job, grp="not_work")
        with rejected_by(db_session, "uq_family_drafts_not_work_per_job"):
            _draft(db_session, job, grp="not_work")

    def test_not_work_group_in_another_job_passes(self, db_session, factories):
        first = _discovery_job(db_session, factories, status="done")
        second = _discovery_job(db_session, factories)
        _draft(db_session, first, grp="not_work")
        assert _draft(db_session, second, grp="not_work").id is not None

    def test_many_new_and_existing_groups_per_job_pass(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        for grp in ("new", "new", "existing", "existing", "not_work"):
            _draft(db_session, job, grp=grp)

    def test_ordinal_unique_within_a_job(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        _draft(db_session, job, ordinal=1)
        with rejected_by(db_session, "uq_family_drafts_job_ordinal"):
            _draft(db_session, job, ordinal=1)

    def test_same_ordinal_in_another_job_passes(self, db_session, factories):
        first = _discovery_job(db_session, factories, status="done")
        second = _discovery_job(db_session, factories)
        _draft(db_session, first, ordinal=1)
        assert _draft(db_session, second, ordinal=1).id is not None

    def test_unit_may_be_null_and_must_exist_when_set(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        assert _draft(db_session, job, unit_id=None).unit_id is None
        assert _draft(db_session, job, unit_id=_unit_ids(db_session, 1)[0]).unit_id is not None
        with rejected_by(db_session, "fk_family_drafts_unit_id"):
            _draft(db_session, job, unit_id=999_999)

    def test_job_must_exist(self, db_session):
        with rejected_by(db_session, "fk_family_drafts_job_id"):
            db_session.add(
                FamilyDraft(
                    job_id=999_999_999, ordinal=1, grp="not_work", status="open",
                )
            )

    def test_category_key_set_null_is_the_safety_net(self, db_session, factories):
        """Штатно удаление категории снимает её с черновиков команда (`delete_category`);
        `SET NULL` ключа — страховка, и она не нарушает форму группы."""
        category = _category(db_session, created_by=_user_id(factories))
        job = _discovery_job(db_session, factories)
        draft = _draft(db_session, job, family_category_id=category.id)
        db_session.execute(sa.delete(FamilyCategory).where(FamilyCategory.id == category.id))
        db_session.expire_all()
        assert db_session.get(FamilyDraft, draft.id).family_category_id is None

    @pytest.mark.parametrize(
        "column",
        ["existing_family_id", "similar_family_id", "activated_family_id", "merged_into_family_id"],
    )
    def test_family_foreign_keys_exist(self, db_session, factories, column):
        job = _discovery_job(db_session, factories)
        extra = {}
        if column == "existing_family_id":
            extra.update(grp="existing")
        if column == "activated_family_id":
            extra.update(status="activated", **_decided(factories))
        if column == "merged_into_family_id":
            extra.update(status="merged", **_decided(factories))
        with rejected_by(db_session, f"fk_family_drafts_{column}"):
            _draft(db_session, job, **{column: 999_999_999}, **extra)


# ---------------------------------------------------------------------------
#  4. Члены групп
# ---------------------------------------------------------------------------

class TestDraftMembers:
    def test_member_of_a_draft_passes(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        draft = _draft(db_session, job)
        member = _member(db_session, draft, _context(db_session, factories))
        assert (member.draft_id, member.job_id) == (draft.id, job.id)

    def test_member_with_job_of_another_open_discovery_rejected(self, db_session, factories):
        first = _discovery_job(db_session, factories, status="done")
        second = _discovery_job(db_session, factories)
        draft = _draft(db_session, first)
        context = _context(db_session, factories)
        with rejected_by(db_session, "fk_family_draft_members_draft_job"):
            db_session.add(
                FamilyDraftMember(
                    draft_id=draft.id, job_id=second.id, context_id=context.id, name_index=0
                )
            )

    def test_context_in_two_groups_of_one_job_rejected(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        first, second = _draft(db_session, job), _draft(db_session, job)
        context = _context(db_session, factories)
        _member(db_session, first, context)
        with rejected_by(db_session, "uq_family_draft_members_job_context"):
            _member(db_session, second, context)

    def test_same_context_in_groups_of_two_jobs_passes(self, db_session, factories):
        first = _discovery_job(db_session, factories, status="done")
        second = _discovery_job(db_session, factories)
        context = _context(db_session, factories)
        _member(db_session, _draft(db_session, first), context)
        assert _member(db_session, _draft(db_session, second), context).context_id == context.id

    def test_same_context_twice_in_one_group_rejected_by_primary_key(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        draft = _draft(db_session, job)
        context = _context(db_session, factories)
        _member(db_session, draft, context)
        with rejected_by(db_session, {"pk_family_draft_members", "uq_family_draft_members_job_context"}):
            _member(db_session, draft, context)

    def test_two_contexts_in_one_group_pass(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        draft = _draft(db_session, job)
        for index in range(2):
            _member(db_session, draft, _context(db_session, factories), name_index=index)

    def test_member_with_missing_context_rejected(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        draft = _draft(db_session, job)
        with rejected_by(db_session, "fk_family_draft_members_context_id"):
            db_session.add(
                FamilyDraftMember(
                    draft_id=draft.id, job_id=job.id, context_id=999_999_999, name_index=0
                )
            )

    def test_deleting_a_draft_deletes_its_members(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        draft = _draft(db_session, job)
        _member(db_session, draft, _context(db_session, factories))
        db_session.execute(sa.delete(FamilyDraft).where(FamilyDraft.id == draft.id))
        assert db_session.query(FamilyDraftMember).filter_by(draft_id=draft.id).count() == 0

    def test_deleting_a_context_of_a_member_is_restricted(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        draft = _draft(db_session, job)
        context = _context(db_session, factories)
        _member(db_session, draft, context)
        with rejected_by(db_session, "fk_family_draft_members_context_id"):
            db_session.execute(sa.text("DELETE FROM catalog_contexts WHERE id = :id"), {"id": context.id})


# ---------------------------------------------------------------------------
#  5. Предложения категории активной семье
# ---------------------------------------------------------------------------

class TestCategoryProposals:
    def test_open_proposal_passes(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        proposal = _proposal(db_session, job, _family(db_session), _seed_category(db_session))
        assert proposal.status == "open"

    def test_applied_proposal_with_author_passes(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        proposal = _proposal(
            db_session, job, _family(db_session), _seed_category(db_session),
            status="applied", **_decided(factories),
        )
        assert proposal.decided_by is not None

    def test_superseded_proposal_without_author_passes(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        assert _proposal(
            db_session, job, _family(db_session), _seed_category(db_session), status="superseded"
        ).status == "superseded"

    def test_applied_without_author_rejected(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        with rejected_by(db_session, "ck_family_category_proposals_applied_pair"):
            _proposal(
                db_session, job, _family(db_session), _seed_category(db_session),
                status="applied", decided_at=_now(),
            )

    @pytest.mark.parametrize("status", ["open", "superseded"])
    def test_author_on_unapplied_rejected(self, db_session, factories, status):
        job = _discovery_job(db_session, factories)
        with rejected_by(db_session, "ck_family_category_proposals_applied_pair"):
            _proposal(
                db_session, job, _family(db_session), _seed_category(db_session),
                status=status, **_decided(factories),
            )

    def test_author_without_time_rejected(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        with rejected_by(db_session, "ck_family_category_proposals_decided_at_pair"):
            _proposal(
                db_session, job, _family(db_session), _seed_category(db_session),
                status="applied", decided_by=_user_id(factories),
            )

    def test_time_without_author_rejected(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        with rejected_by(db_session, "ck_family_category_proposals_decided_at_pair"):
            _proposal(
                db_session, job, _family(db_session), _seed_category(db_session),
                decided_at=_now(),
            )

    def test_unknown_status_rejected(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        with rejected_by(db_session, "ck_family_category_proposals_status"):
            _proposal(db_session, job, _family(db_session), _seed_category(db_session), status="bogus")

    def test_one_proposal_per_job_and_family(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        family = _family(db_session)
        _proposal(db_session, job, family, _seed_category(db_session, "work"))
        with rejected_by(db_session, "pk_family_category_proposals"):
            _proposal(db_session, job, family, _seed_category(db_session, "costs_services"))

    def test_same_family_in_another_job_passes(self, db_session, factories):
        first = _discovery_job(db_session, factories, status="done")
        second = _discovery_job(db_session, factories)
        family = _family(db_session)
        _proposal(db_session, first, family, _seed_category(db_session))
        assert _proposal(db_session, second, family, _seed_category(db_session)).job_id == second.id

    def test_deleting_a_category_deletes_its_proposals(self, db_session, factories):
        category = _category(db_session, created_by=_user_id(factories))
        job = _discovery_job(db_session, factories)
        family = _family(db_session)
        _proposal(db_session, job, family, category)
        db_session.execute(sa.delete(FamilyCategory).where(FamilyCategory.id == category.id))
        assert db_session.query(FamilyCategoryProposal).filter_by(job_id=job.id).count() == 0

    def test_deleting_a_family_with_a_proposal_is_restricted(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        family = _family(db_session)
        _proposal(db_session, job, family, _seed_category(db_session))
        with rejected_by(db_session, "fk_family_category_proposals_family_id"):
            db_session.execute(sa.delete(WorkFamily).where(WorkFamily.id == family.id))


# ---------------------------------------------------------------------------
#  6. Журнал: тип `context_reopened`
# ---------------------------------------------------------------------------

class TestJournalContextReopened:
    def test_event_with_context_subject_passes(self, db_session, factories):
        context = _context(db_session, factories)
        assert _event(db_session, "context_reopened", context_id=context.id).id is not None

    def test_event_with_family_subject_rejected(self, db_session):
        family = _family(db_session)
        with rejected_by(db_session, "ck_semantic_events_subject_by_type"):
            _event(db_session, "context_reopened", family_id=family.id)

    def test_neighbouring_unknown_type_still_rejected(self, db_session, factories):
        context = _context(db_session, factories)
        with rejected_by(db_session, "ck_semantic_events_event_type"):
            _event(db_session, "context_reopen", context_id=context.id)

    def test_event_row_is_a_semantic_event(self, db_session, factories):
        context = _context(db_session, factories)
        event = _event(db_session, "context_reopened", context_id=context.id)
        assert isinstance(event, SemanticEvent)


# ---------------------------------------------------------------------------
#  7. Сырые индексы: наличие в базе и регистрация в alembic/env.py
# ---------------------------------------------------------------------------

class TestRawIndexes:
    _EXPECTED = {
        "uq_family_categories_title": ("family_categories", "(lower(btrim(title)))"),
        "uq_semantic_jobs_discovery_live": (
            "semantic_jobs", "WHERE ((kind = 'family_discovery'::text) AND (status = ANY",
        ),
        "uq_family_drafts_not_work_per_job": (
            "family_drafts", "(job_id) WHERE (grp = 'not_work'::text)",
        ),
    }

    @pytest.mark.parametrize("name", sorted(_EXPECTED))
    def test_index_exists_and_is_unique(self, db_session, name):
        table, fragment = self._EXPECTED[name]
        definition = db_session.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE indexname = :n AND tablename = :t"),
            {"n": name, "t": table},
        ).scalar_one()
        assert definition.startswith("CREATE UNIQUE INDEX")
        assert fragment in definition

    def test_live_index_names_exactly_the_three_live_statuses(self, db_session):
        definition = db_session.execute(
            sa.text(
                "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_semantic_jobs_discovery_live'"
            )
        ).scalar_one()
        statuses = set(re.findall(r"'(\w+)'::text", definition.split("status")[1]))
        assert statuses == {"pending", "running", "privacy_hold"}

    @pytest.mark.parametrize("name", sorted(_EXPECTED))
    def test_index_is_registered_in_env(self, name):
        env_text = (BACKEND_ROOT / "alembic" / "env.py").read_bytes().decode("utf-8")
        assert f'"{name}"' in env_text


# ---------------------------------------------------------------------------
#  8. Parity: литералы миграции = models.py = независимый литерал
# ---------------------------------------------------------------------------

class TestParityWithMigration:
    _IN_CASES = [
        (SEMANTIC_JOB_KINDS, "SEMANTIC_JOB_KINDS",
         {"family_suggestion", "family_schema", "context_values", "family_discovery"}),
        (DRAFT_GROUPS, "DRAFT_GROUPS", {"new", "existing", "not_work"}),
        (DRAFT_STATUSES, "DRAFT_STATUSES",
         {"open", "activated", "merged", "discarded", "superseded"}),
        (CATEGORY_PROPOSAL_STATUSES, "CATEGORY_PROPOSAL_STATUSES",
         {"open", "applied", "superseded"}),
        (SEMANTIC_EVENT_TYPES_SQL, "SEMANTIC_EVENT_TYPES_SQL", {
            "context_created", "context_split", "context_merged", "members_moved",
            "members_marked_stale", "kind_set", "name_role_set", "context_family_assigned",
            "context_archived", "routing_rules_dropped", "family_created", "family_updated",
            "family_activated", "family_archived", "family_merged",
            "context_variant_assigned", "context_family_pending", "context_not_work",
            "family_schema_frozen", "family_schema_value_added", "family_variants_merged",
            "context_reopened",
        }),
    ]

    @pytest.mark.parametrize(
        ("model_expr", "name", "independent"), _IN_CASES, ids=[case[1] for case in _IN_CASES]
    )
    def test_in_list_matches_migration_and_independent_literal(self, model_expr, name, independent):
        assert model_expr == getattr(_load_migration_0021(), name)
        parsed = {piece.strip().strip("'") for piece in model_expr.split(",")}
        assert parsed == independent

    def test_enum_members_equal_the_lists(self):
        assert {m.value for m in DraftGroup} == {"new", "existing", "not_work"}
        assert {m.value for m in DraftStatus} == {
            "open", "activated", "merged", "discarded", "superseded",
        }
        assert {m.value for m in CategoryProposalStatus} == {"open", "applied", "superseded"}

    _CK_CASES = [
        (CK_FAMILY_CATEGORY_TITLE_NOT_BLANK, "CK_FAMILY_CATEGORY_TITLE_NOT_BLANK",
         "btrim(title) <> ''"),
        (CK_FAMILY_CATEGORY_DEFINITION_NOT_BLANK, "CK_FAMILY_CATEGORY_DEFINITION_NOT_BLANK",
         "btrim(definition) <> ''"),
        (CK_SEMANTIC_JOBS_CONTEXT_SUBJECT, "CK_SEMANTIC_JOBS_CONTEXT_SUBJECT",
         "(kind IN ('family_suggestion', 'context_values')) = (context_id IS NOT NULL)"),
        (CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND, "CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND",
         "(kind IN ('family_schema', 'context_values')) = (schema_id IS NOT NULL)"),
        (CK_SEMANTIC_JOBS_DISCOVERY_SUBJECT, "CK_SEMANTIC_JOBS_DISCOVERY_SUBJECT",
         "kind <> 'family_discovery' OR (context_id IS NULL AND family_id IS NULL)"),
        (
            CK_DRAFT_SHAPE, "CK_DRAFT_SHAPE",
            "(grp = 'new' AND title IS NOT NULL AND definition IS NOT NULL "
            "AND btrim(title) <> '' AND btrim(definition) <> '' "
            "AND existing_family_id IS NULL) "
            "OR (grp = 'existing' AND existing_family_id IS NOT NULL AND title IS NULL "
            "AND definition IS NULL AND family_category_id IS NULL AND similar_family_id IS NULL) "
            "OR (grp = 'not_work' AND title IS NULL AND definition IS NULL "
            "AND family_category_id IS NULL AND existing_family_id IS NULL "
            "AND similar_family_id IS NULL)",
        ),
        (CK_DRAFT_ACTIVATED_PAIR, "CK_DRAFT_ACTIVATED_PAIR",
         "(status = 'activated') = (activated_family_id IS NOT NULL)"),
        (CK_DRAFT_MERGED_PAIR, "CK_DRAFT_MERGED_PAIR",
         "(status = 'merged') = (num_nonnulls(merged_into_draft_id, merged_into_family_id) = 1)"),
        (CK_DRAFT_MERGE_TARGET_AT_MOST_ONE, "CK_DRAFT_MERGE_TARGET_AT_MOST_ONE",
         "num_nonnulls(merged_into_draft_id, merged_into_family_id) <= 1"),
        (CK_DRAFT_NOT_SELF_MERGED, "CK_DRAFT_NOT_SELF_MERGED",
         "merged_into_draft_id IS NULL OR merged_into_draft_id <> id"),
        (CK_DRAFT_DECISION_ONLY_NEW, "CK_DRAFT_DECISION_ONLY_NEW",
         "grp = 'new' OR status IN ('open', 'superseded')"),
        (CK_DRAFT_DECIDED_BY_PAIR, "CK_DRAFT_DECIDED_BY_PAIR",
         "(status IN ('activated', 'merged', 'discarded')) = (decided_by IS NOT NULL)"),
        (CK_DRAFT_DECIDED_AT_PAIR, "CK_DRAFT_DECIDED_AT_PAIR",
         "(decided_by IS NULL) = (decided_at IS NULL)"),
        (CK_DRAFT_EDITED_PAIR, "CK_DRAFT_EDITED_PAIR", "(edited_by IS NULL) = (edited_at IS NULL)"),
        (CK_PROPOSAL_APPLIED_PAIR, "CK_PROPOSAL_APPLIED_PAIR",
         "(status = 'applied') = (decided_by IS NOT NULL)"),
        (CK_PROPOSAL_DECIDED_AT_PAIR, "CK_PROPOSAL_DECIDED_AT_PAIR",
         "(decided_by IS NULL) = (decided_at IS NULL)"),
    ]

    @pytest.mark.parametrize(
        ("model_expr", "name", "independent"), _CK_CASES, ids=[case[1] for case in _CK_CASES]
    )
    def test_check_expression_matches_migration_and_independent_literal(
        self, model_expr, name, independent
    ):
        assert model_expr == getattr(_load_migration_0021(), name)
        assert model_expr == independent

    def test_every_check_of_the_new_tables_is_named_and_present(self, db_session):
        expected = {
            "family_categories": {
                "ck_family_categories_title_not_blank", "ck_family_categories_definition_not_blank",
                "ck_family_categories_author_iff_not_seed",
            },
            "family_drafts": {
                "ck_family_drafts_grp", "ck_family_drafts_status", "ck_family_drafts_shape",
                "ck_family_drafts_activated_pair", "ck_family_drafts_merged_pair",
                "ck_family_drafts_merge_target_at_most_one", "ck_family_drafts_not_self_merged",
                "ck_family_drafts_decision_only_new", "ck_family_drafts_decided_by_pair",
                "ck_family_drafts_decided_at_pair", "ck_family_drafts_edited_pair",
            },
            "family_category_proposals": {
                "ck_family_category_proposals_status",
                "ck_family_category_proposals_applied_pair",
                "ck_family_category_proposals_decided_at_pair",
            },
        }
        for table, names in expected.items():
            assert _live_constraint_names(db_session, table, "c") == names, table

    #: Ключи четырёх новых таблиц и колонки категории — состав и ПОРЯДОК колонок
    #: составных ключей, цель и `ON DELETE`, как их печатает `pg_get_constraintdef`;
    #: независимый литерал по DDL спеки §2.2 (у ссылок на `users`, где спека действие
    #: не называет, — `RESTRICT` по соглашению схемы). `alembic check` действие
    #: `ON DELETE` и порядок колонок живой базы с миграцией не сверяет — сверяем сами.
    _KEYS = {
        "family_categories": {
            "pk_family_categories": "PRIMARY KEY (id)",
            "uq_family_categories_seed_key": "UNIQUE (seed_key)",
            "fk_family_categories_created_by":
                "FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE RESTRICT",
        },
        "work_families": {
            "fk_work_families_family_category_id":
                "FOREIGN KEY (family_category_id) REFERENCES family_categories(id) "
                "ON DELETE RESTRICT",
        },
        "family_drafts": {
            "pk_family_drafts": "PRIMARY KEY (id)",
            "uq_family_drafts_job_ordinal": "UNIQUE (job_id, ordinal)",
            "uq_family_drafts_id_job": "UNIQUE (id, job_id)",
            "fk_family_drafts_job_id":
                "FOREIGN KEY (job_id) REFERENCES semantic_jobs(id) ON DELETE RESTRICT",
            "fk_family_drafts_unit_id":
                "FOREIGN KEY (unit_id) REFERENCES units_of_measure(id) ON DELETE RESTRICT",
            "fk_family_drafts_family_category_id":
                "FOREIGN KEY (family_category_id) REFERENCES family_categories(id) "
                "ON DELETE SET NULL",
            "fk_family_drafts_existing_family_id":
                "FOREIGN KEY (existing_family_id) REFERENCES work_families(id) ON DELETE RESTRICT",
            "fk_family_drafts_similar_family_id":
                "FOREIGN KEY (similar_family_id) REFERENCES work_families(id) ON DELETE RESTRICT",
            "fk_family_drafts_activated_family_id":
                "FOREIGN KEY (activated_family_id) REFERENCES work_families(id) "
                "ON DELETE RESTRICT",
            "fk_family_drafts_merged_into_family_id":
                "FOREIGN KEY (merged_into_family_id) REFERENCES work_families(id) "
                "ON DELETE RESTRICT",
            "fk_family_drafts_merged_into_job":
                "FOREIGN KEY (merged_into_draft_id, job_id) REFERENCES family_drafts(id, job_id) "
                "ON DELETE RESTRICT",
            "fk_family_drafts_edited_by":
                "FOREIGN KEY (edited_by) REFERENCES users(id) ON DELETE RESTRICT",
            "fk_family_drafts_decided_by":
                "FOREIGN KEY (decided_by) REFERENCES users(id) ON DELETE RESTRICT",
        },
        "family_draft_members": {
            "pk_family_draft_members": "PRIMARY KEY (draft_id, context_id)",
            "uq_family_draft_members_job_context": "UNIQUE (job_id, context_id)",
            "fk_family_draft_members_draft_job":
                "FOREIGN KEY (draft_id, job_id) REFERENCES family_drafts(id, job_id) "
                "ON DELETE CASCADE",
            "fk_family_draft_members_context_id":
                "FOREIGN KEY (context_id) REFERENCES catalog_contexts(id) ON DELETE RESTRICT",
        },
        "family_category_proposals": {
            "pk_family_category_proposals": "PRIMARY KEY (job_id, family_id)",
            "fk_family_category_proposals_job_id":
                "FOREIGN KEY (job_id) REFERENCES semantic_jobs(id) ON DELETE RESTRICT",
            "fk_family_category_proposals_family_id":
                "FOREIGN KEY (family_id) REFERENCES work_families(id) ON DELETE RESTRICT",
            "fk_family_category_proposals_family_category_id":
                "FOREIGN KEY (family_category_id) REFERENCES family_categories(id) "
                "ON DELETE CASCADE",
            "fk_family_category_proposals_decided_by":
                "FOREIGN KEY (decided_by) REFERENCES users(id) ON DELETE RESTRICT",
        },
    }

    @pytest.mark.parametrize("table", sorted(_KEYS))
    def test_keys_and_foreign_keys_match_the_spec(self, db_session, table):
        rows = db_session.execute(
            sa.text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = CAST(:t AS regclass) AND contype IN ('p', 'u', 'f')"
            ),
            {"t": table},
        ).all()
        live = dict(rows)
        if table == "work_families":
            live = {name: d for name, d in live.items() if "family_categor" in name}
        assert live == self._KEYS[table]

    def test_semantic_jobs_has_the_discovery_subject_check_and_the_rewritten_ones(self, db_session):
        names = _live_constraint_names(db_session, "semantic_jobs", "c")
        assert {
            "ck_semantic_jobs_discovery_subject", "ck_semantic_jobs_context_subject",
            "ck_semantic_jobs_schema_id_by_kind", "ck_semantic_jobs_schema_subject",
            "ck_semantic_jobs_result_suggestion_kind", "ck_semantic_jobs_kind",
        } <= names


# ---------------------------------------------------------------------------
#  9. Четыре таблицы — в _DOMAIN_TABLES, категории пересеваются после очистки
# ---------------------------------------------------------------------------

_NEW_TABLES = (
    "family_categories", "family_drafts", "family_draft_members", "family_category_proposals",
)


class TestDomainTablesAndSeedFixture:
    def test_all_four_tables_listed_explicitly(self):
        from tests.conftest import _DOMAIN_TABLES

        for table in _NEW_TABLES:
            assert table in _DOMAIN_TABLES

    def test_work_category_fixture_returns_the_seed_row_id(self, db_session, work_category_id):
        assert work_category_id == db_session.execute(
            sa.text("SELECT id FROM family_categories WHERE seed_key = 'work'")
        ).scalar_one()

    def test_truncate_clears_the_new_tables_and_reseeds_the_three_categories(
        self, db_engine, committing_db, committing_factories
    ):
        from tests.conftest import _truncate_domain_tables

        session = committing_db
        custom = _category(session, created_by=_user_id(committing_factories))
        job = _discovery_job(session, committing_factories)
        draft = _draft(session, job, family_category_id=custom.id)
        _member(session, draft, _context(session, committing_factories))
        _proposal(session, job, _family(session), custom)
        session.commit()
        session.close()

        with db_engine.connect() as conn:
            for table in _NEW_TABLES:
                assert conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() >= 1

        _truncate_domain_tables(db_engine)

        with db_engine.connect() as conn:
            counts = {
                table: conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
                for table in _NEW_TABLES
            }
            seeds = conn.execute(
                sa.text("SELECT seed_key, created_by FROM family_categories ORDER BY id")
            ).all()
        assert counts == {
            "family_categories": 3, "family_drafts": 0, "family_draft_members": 0,
            "family_category_proposals": 0,
        }
        assert [row.seed_key for row in seeds] == list(FAMILY_CATEGORY_SEED_KEYS)
        assert all(row.created_by is None for row in seeds)


# ---------------------------------------------------------------------------
#  10. downgrade: шесть носителей — чистая логика и счётчики на живой схеме
# ---------------------------------------------------------------------------

_CARRIERS = {
    "family_drafts": "черновиков открытия — 5",
    "family_category_proposals": "предложений категорий — 5",
    "semantic_jobs_discovery": "заданий открытия — 5",
    "work_families_with_category": "семей с категорией — 5",
    "semantic_events_reopened": "событий context_reopened — 5",
    "family_categories_custom": "категорий без seed_key — 5",
}


class TestDowngradeRefusalLogic:
    _ZERO = {key: 0 for key in _CARRIERS}

    def test_carriers_are_six(self):
        assert len(_CARRIERS) == 6

    @pytest.mark.parametrize("key", sorted(_CARRIERS))
    def test_each_counter_alone_blocks_and_is_named(self, key):
        blockers = dict(self._ZERO)
        blockers[key] = 5
        refusal = _load_migration_0021()._downgrade_refusal(blockers)
        assert refusal is not None
        assert _CARRIERS[key] in refusal
        for other, phrase in _CARRIERS.items():
            if other != key:
                assert phrase not in refusal

    def test_all_zero_blockers_pass(self):
        assert _load_migration_0021()._downgrade_refusal(dict(self._ZERO)) is None


class TestDowngradeBlockersLive:
    """Каждый носитель — свой вход на живой схеме: считается ровно его счётчик,
    остальные нули, отказ называет носитель."""

    def _blockers(self, db_session):
        migration = _load_migration_0021()
        blockers = migration._downgrade_blockers(db_session.connection())
        return blockers, migration._downgrade_refusal(blockers)

    def _assert_only(self, db_session, key, expected=1):
        blockers, refusal = self._blockers(db_session)
        assert blockers == {k: (expected if k == key else 0) for k in _CARRIERS}
        assert refusal is not None
        assert _CARRIERS[key].replace("5", str(expected)) in refusal

    def test_clean_schema_does_not_block(self, db_session):
        """Три категории миграции — не носитель: они уходят вместе с таблицей."""
        blockers, refusal = self._blockers(db_session)
        assert blockers == {key: 0 for key in _CARRIERS}
        assert refusal is None

    def test_drafts_carrier(self, db_session, factories):
        job = _discovery_job(db_session, factories)
        _draft(db_session, job)
        blockers, _ = self._blockers(db_session)
        assert blockers["family_drafts"] == 1
        assert blockers["semantic_jobs_discovery"] == 1

    def test_proposals_carrier(self, db_session, factories):
        """Предложение без черновиков: задание открытия при нём есть всегда, и оба
        счётчика называют своё."""
        job = _discovery_job(db_session, factories)
        _proposal(db_session, job, _family(db_session), _seed_category(db_session))
        blockers, refusal = self._blockers(db_session)
        assert blockers["family_category_proposals"] == 1
        assert blockers["family_drafts"] == 0
        assert "предложений категорий — 1" in refusal

    def test_discovery_job_carrier(self, db_session, factories):
        _discovery_job(db_session, factories)
        self._assert_only(db_session, "semantic_jobs_discovery")

    def test_other_kind_jobs_do_not_block(self, db_session, factories):
        _job(db_session, factories)
        blockers, refusal = self._blockers(db_session)
        assert blockers["semantic_jobs_discovery"] == 0
        assert refusal is None

    def test_family_with_category_carrier(self, db_session):
        _family(db_session, family_category_id=_seed_category(db_session).id)
        self._assert_only(db_session, "work_families_with_category")

    def test_family_without_category_does_not_block(self, db_session):
        _family(db_session)
        blockers, refusal = self._blockers(db_session)
        assert blockers["work_families_with_category"] == 0
        assert refusal is None

    def test_reopened_event_carrier(self, db_session, factories):
        _event(db_session, "context_reopened", context_id=_context(db_session, factories).id)
        self._assert_only(db_session, "semantic_events_reopened")

    def test_older_event_does_not_block(self, db_session, factories):
        _event(db_session, "context_created", context_id=_context(db_session, factories).id)
        blockers, refusal = self._blockers(db_session)
        assert blockers["semantic_events_reopened"] == 0
        assert refusal is None

    def test_custom_category_carrier(self, db_session, factories):
        _category(db_session, created_by=_user_id(factories))
        self._assert_only(db_session, "family_categories_custom")

    def test_counters_count_rows_not_kinds(self, db_session, factories):
        for _ in range(2):
            _category(db_session, created_by=_user_id(factories))
        self._assert_only(db_session, "family_categories_custom", expected=2)


# ---------------------------------------------------------------------------
#  11. Живые прогоны alembic на scratch-базе
# ---------------------------------------------------------------------------

def _seed_three_job_kinds(conn) -> dict:
    """Задания всех трёх прежних видов в схеме 0020: предложение, схема семьи,
    значения контекста."""
    position_id = conn.execute(
        sa.text(
            "INSERT INTO catalog_positions "
            "(standard_job_title, normalized_job_title, kind, status) "
            "VALUES ('Работа (scratch)', 'работа (scratch)', 'POSITION', 'na') RETURNING id"
        )
    ).scalar_one()
    bucket_id = conn.execute(
        sa.text("INSERT INTO context_buckets (catalog_position_id) VALUES (:cp) RETURNING id"),
        {"cp": position_id},
    ).scalar_one()
    context_ids = [
        conn.execute(
            sa.text(
                "INSERT INTO catalog_contexts "
                "(bucket_id, semantic_kind, semantic_kind_source, semantic_kind_at, "
                " name_role, name_role_source, name_role_at, place_dictionary_version, "
                " semantic_state) "
                "VALUES (:b, 'WORK', 'rule', now(), 'WORK', 'rule', now(), 1, 'SUGGESTED') "
                "RETURNING id"
            ),
            {"b": bucket_id},
        ).scalar_one()
        for _ in range(2)
    ]
    family_id = conn.execute(
        sa.text(
            "INSERT INTO work_families (title, status, seed_key) "
            "VALUES ('Семья (scratch)', 'draft', :k) RETURNING id"
        ),
        {"k": f"scratch-{_uid()}"},
    ).scalar_one()
    schema_ids = {}
    for status in ("frozen", "building"):
        schema_ids[status] = conn.execute(
            sa.text(
                "INSERT INTO family_parameter_schemas (family_id, version, status, origin, frozen_at) "
                "VALUES (:f, :v, :s, 'model', CASE WHEN :s = 'frozen' THEN now() END) RETURNING id"
            ),
            {"f": family_id, "v": 1 if status == "frozen" else 2, "s": status},
        ).scalar_one()
    columns = (
        "kind, context_id, family_id, schema_id, request_hash, status, prompt_version, "
        "model_requested, place_dictionary_version, candidates_hash, prefix_hash, input_hash, "
        "response_schema_version, serialization_version"
    )
    rows = (
        ("family_suggestion", context_ids[0], None, None, "job-suggestion"),
        ("family_schema", None, family_id, schema_ids["building"], "job-schema"),
        ("context_values", context_ids[1], family_id, schema_ids["frozen"], "job-values"),
    )
    for kind, context_id, fam, schema, request_hash in rows:
        conn.execute(
            sa.text(
                f"INSERT INTO semantic_jobs ({columns}) VALUES "
                "(:k, :c, :f, :s, :h, 'pending', '1', 'm', 1, 'c', 'p', 'i', '1', '1')"
            ),
            {"k": kind, "c": context_id, "f": fam, "s": schema, "h": request_hash},
        )
    return {"family": family_id}


def _jobs_snapshot(conn):
    return conn.execute(sa.text("SELECT * FROM semantic_jobs ORDER BY id")).all()


@pytest.mark.migration_roundtrip
class TestUpgradeAndDowngrade:
    def test_upgrade_keeps_every_job_of_the_three_kinds_and_seeds_the_categories(self):
        with _scratch_alembic("0021 upgrade on jobs") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0020")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.begin() as conn:
                    _seed_three_job_kinds(conn)
                with engine.connect() as conn:
                    before = _jobs_snapshot(conn)
                assert [row.kind for row in before] == [
                    "family_suggestion", "family_schema", "context_values",
                ]

                command.upgrade(cfg, "head")

                with engine.connect() as conn:
                    after = _jobs_snapshot(conn)
                    categories = conn.execute(
                        sa.text(
                            "SELECT seed_key, definition, created_by FROM family_categories "
                            "ORDER BY id"
                        )
                    ).all()
                    family_category = conn.execute(
                        sa.text("SELECT family_category_id FROM work_families")
                    ).scalar_one()
                assert after == before
                assert [row.seed_key for row in categories] == list(FAMILY_CATEGORY_SEED_KEYS)
                assert all(row.definition.strip() for row in categories)
                assert all(row.created_by is None for row in categories)
                assert family_category is None
            finally:
                engine.dispose()

    def test_downgrade_and_upgrade_again_on_empty_database(self):
        with _scratch_alembic("0021 empty round trip") as (command, cfg, scratch_url):
            command.upgrade(cfg, "head")
            command.downgrade(cfg, "0020")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.connect() as conn:
                    left = conn.execute(
                        sa.text(
                            "SELECT count(*) FROM information_schema.tables WHERE table_name IN "
                            "('family_categories', 'family_drafts', 'family_draft_members', "
                            " 'family_category_proposals')"
                        )
                    ).scalar_one()
                    column = conn.execute(
                        sa.text(
                            "SELECT count(*) FROM information_schema.columns WHERE "
                            "table_name = 'work_families' AND column_name = 'family_category_id'"
                        )
                    ).scalar_one()
                    indexes = conn.execute(
                        sa.text(
                            "SELECT count(*) FROM pg_indexes WHERE indexname IN "
                            "('uq_family_categories_title', 'uq_semantic_jobs_discovery_live', "
                            " 'uq_family_drafts_not_work_per_job')"
                        )
                    ).scalar_one()
                    kind_check = conn.execute(
                        sa.text(
                            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                            "WHERE conname = 'ck_semantic_jobs_kind'"
                        )
                    ).scalar_one()
                    context_subject = conn.execute(
                        sa.text(
                            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                            "WHERE conname = 'ck_semantic_jobs_context_subject'"
                        )
                    ).scalar_one()
                    event_check = conn.execute(
                        sa.text(
                            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                            "WHERE conname = 'ck_semantic_events_event_type'"
                        )
                    ).scalar_one()
                    discovery_check = conn.execute(
                        sa.text(
                            "SELECT count(*) FROM pg_constraint "
                            "WHERE conname = 'ck_semantic_jobs_discovery_subject'"
                        )
                    ).scalar_one()
                assert (left, column, indexes, discovery_check) == (0, 0, 0, 0)
                assert "family_discovery" not in kind_check
                assert "context_values" not in context_subject
                assert "context_reopened" not in event_check

                command.upgrade(cfg, "head")
                with engine.connect() as conn:
                    tables = conn.execute(
                        sa.text(
                            "SELECT count(*) FROM information_schema.tables WHERE table_name IN "
                            "('family_categories', 'family_drafts', 'family_draft_members', "
                            " 'family_category_proposals')"
                        )
                    ).scalar_one()
                    seeds = conn.execute(sa.text("SELECT count(*) FROM family_categories")).scalar_one()
                assert (tables, seeds) == (4, 3)
            finally:
                engine.dispose()

    def test_downgrade_restores_every_constraint_and_index_of_0020(self):
        """Оракул — сама схема 0020, снятая до `upgrade`, а не литералы миграции:
        после `upgrade head` → `downgrade 0020` ограничения и индексы `semantic_jobs`,
        `semantic_events` и `work_families` равны снятым побуквенно (выражения CHECK
        заданий, список типов журнала, отсутствие колонки категории и ключа живого
        открытия)."""
        def snapshot(conn) -> dict:
            constraints = conn.execute(
                sa.text(
                    "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) "
                    "FROM pg_constraint WHERE conrelid IN "
                    "('semantic_jobs'::regclass, 'semantic_events'::regclass, "
                    " 'work_families'::regclass)"
                )
            ).all()
            indexes = conn.execute(
                sa.text(
                    "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE tablename IN "
                    "('semantic_jobs', 'semantic_events', 'work_families')"
                )
            ).all()
            columns = conn.execute(
                sa.text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_name IN ('semantic_jobs', 'semantic_events', 'work_families')"
                )
            ).all()
            return {
                "constraints": set(constraints), "indexes": set(indexes), "columns": set(columns),
            }

        with _scratch_alembic("0021 restores 0020") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0020")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.connect() as conn:
                    before = snapshot(conn)
                command.upgrade(cfg, "head")
                with engine.connect() as conn:
                    upgraded = snapshot(conn)
                assert upgraded != before  # предусловие: upgrade действительно менял эти таблицы
                command.downgrade(cfg, "0020")
                with engine.connect() as conn:
                    after = snapshot(conn)
            finally:
                engine.dispose()
        assert after == before

    @pytest.mark.parametrize("carrier", sorted(_CARRIERS))
    def test_downgrade_refused_by_each_carrier_alone(self, carrier):
        """Носитель создаётся на настоящей схеме `head` и один держит откат: отказ —
        `RuntimeError` с диагностикой, схема остаётся нетронутой."""
        from sqlalchemy.orm import Session

        from tests import factories as f

        with _scratch_alembic(f"0021 downgrade refusal {carrier}") as (command, cfg, scratch_url):
            command.upgrade(cfg, "head")
            engine = sa.create_engine(scratch_url)
            try:
                with Session(engine) as session:
                    f._register_session(session)
                    try:
                        _create_carrier(session, carrier)
                        session.commit()
                    finally:
                        f._register_session(None)

                with pytest.raises(RuntimeError, match="Откат 0021 невозможен") as exc:
                    command.downgrade(cfg, "0020")

                assert _CARRIERS[carrier].replace("5", "1") in str(exc.value)
                with engine.connect() as conn:
                    still = conn.execute(
                        sa.text(
                            "SELECT count(*) FROM pg_indexes "
                            "WHERE indexname = 'uq_semantic_jobs_discovery_live'"
                        )
                    ).scalar_one()
                    tables = conn.execute(
                        sa.text(
                            "SELECT count(*) FROM information_schema.tables "
                            "WHERE table_name = 'family_drafts'"
                        )
                    ).scalar_one()
                assert (still, tables) == (1, 1)
            finally:
                engine.dispose()


def _create_carrier(session, carrier: str) -> None:
    """Ровно один носитель downgrade на схеме `head` (категории миграции уже есть)."""
    from tests import factories as f

    if carrier == "family_drafts":
        # Задание открытия неизбежно при черновике, но оно уходит в счёт отдельным ключом:
        # откат отказывает в любом случае, а фраза названного носителя присутствует.
        job = _discovery_job(session, f, status="done")
        _draft(session, job)
    elif carrier == "family_category_proposals":
        job = _discovery_job(session, f, status="done")
        _proposal(session, job, _family(session), _seed_category(session))
    elif carrier == "semantic_jobs_discovery":
        _discovery_job(session, f)
    elif carrier == "work_families_with_category":
        _family(session, family_category_id=_seed_category(session).id)
    elif carrier == "semantic_events_reopened":
        _event(session, "context_reopened", context_id=_context(session, f).id)
    elif carrier == "family_categories_custom":
        _category(session, created_by=_user_id(f))
    else:  # pragma: no cover
        raise AssertionError(carrier)
