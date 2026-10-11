"""Справочник категорий семей и категория у семьи (спека
`2026-10-09-catalog-discovery-design.md` §2.9, §2.12; решения 10, 17, 20):
сервис (`services/family_categories.py`), категория в `create_family`,
`update_family`, `activate_family`, `create_family_from_suggestion`, строка семьи
и фильтр, маршруты справочника и их права.

Отказ проверяется КОДОМ (`exc.code`), статусом ответа и неизменённым состоянием
базы, а не подстрокой текста; тексты отказов §2.12 сверяются дословно отдельными
входами. Гонки идут на настоящих `commit` (`committing_*`): синхронизация —
событием ПОСЛЕ чтения (`after_cursor_execute`) и наблюдением ожидания замка в
`pg_stat_activity`, «дошёл до барьера» и «ждёт замок» проверяются как предусловия
с понятным сообщением; пауза перед записью семьи стоит ДО `INSERT` — иначе
неявный `FOR KEY SHARE` внешнего ключа замаскировал бы отсутствие `FOR SHARE`
категории (`docs/pitfalls/db.md`).
"""
from __future__ import annotations

import contextlib
import itertools
import threading
import time

import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.dialects import postgresql

import crud.semantic as crud_semantic
import services.family_categories as family_categories_module
from models import (
    FamilyCategory,
    FamilyCategoryProposal,
    FamilyDraft,
    FamilyStatus,
    FamilySuggestion,
    SemanticEvent,
    WorkFamily,
)
from routers.semantic import _status_for_code
from services.family_categories import (
    REFUSE_CATEGORY_BLANK_DEFINITION,
    REFUSE_CATEGORY_BLANK_TITLE,
    REFUSE_CATEGORY_DUPLICATE,
    REFUSE_CATEGORY_IN_USE,
    REFUSE_CATEGORY_NOT_FOUND,
    _lock_categories_statement,
    create_category,
    delete_category,
    lock_categories,
    require_category,
    update_category,
)
from services.semantic_decisions import create_family_from_suggestion
from services.work_families import (
    REFUSE_ACTIVATE_WITHOUT_CATEGORY,
    REFUSE_ACTIVATE_WITHOUT_DEFINITION,
    REFUSE_CLEAR_CATEGORY_ACTIVE,
    REFUSE_UPDATE_ARCHIVED,
    UNSET,
    WorkFamilyError,
    activate_family,
    archive_family,
    create_family,
    update_family,
)
from tests.integration.test_catalog_discovery_schema import (
    _category,
    _discovery_job,
    _draft,
    _proposal,
)
from tests.integration.test_semantic_queue_decisions import _fresh, _published, _scene

pytestmark = pytest.mark.integration

BASE = "/api/v1/semantic"
_COUNTER = itertools.count(1)

#: Тексты отказов §2.12 — независимые литералы, не читают код.
_TEXT_BLANK_TITLE = "У категории должно быть имя — по определению модель выбирает категорию."
_TEXT_BLANK_DEFINITION = (
    "У категории должно быть определение — по определению модель выбирает категорию."
)
_TEXT_NO_CATEGORY = "Сначала выберите категорию семьи."
_TEXT_CLEAR_ACTIVE = "У активной семьи категорию можно сменить, но не снять."


def _unique(prefix: str) -> str:
    return f"{prefix} {next(_COUNTER)}-{time.monotonic_ns()}"


def _user_id(factories) -> int:
    return factories.UserFactory.create().id


def _operator_category(db, factories, **overrides) -> FamilyCategory:
    """Категория оператора (автор есть, ключа нет)."""
    return _category(db, created_by=_user_id(factories), **overrides)


def _work_id(db) -> int:
    return db.query(FamilyCategory).filter_by(seed_key="work").one().id


def _family(db, actor_id, *, title=None, category_id=None, definition="Определение", unit_name=None):
    return create_family(
        db, title=title or _unique("Семья"), unit_name=unit_name, definition=definition,
        actor_id=actor_id, family_category_id=category_id,
    )


def _count(db, model) -> int:
    return db.execute(sa.select(sa.func.count()).select_from(model)).scalar_one()


def _events(db, family_id, event_type):
    db.expire_all()
    return (
        db.query(SemanticEvent)
        .filter_by(family_id=family_id, event_type=event_type)
        .order_by(SemanticEvent.id)
        .all()
    )


# ---------------------------------------------------------------------------
#  Создание категории
# ---------------------------------------------------------------------------

class TestCreateCategory:
    def test_creates_an_operator_category(self, db_session, factories):
        actor = _user_id(factories)
        category = create_category(
            db_session, title="Проектирование", definition="Проектные работы.", actor_id=actor
        )
        stored = db_session.get(FamilyCategory, category.id)
        assert (stored.seed_key, stored.created_by, stored.title, stored.definition) == (
            None, actor, "Проектирование", "Проектные работы.",
        )

    def test_title_and_definition_are_stored_without_edge_spaces(self, db_session, factories):
        category = create_category(
            db_session, title="  Охрана \t", definition="\n Охрана объекта  ",
            actor_id=_user_id(factories),
        )
        assert (category.title, category.definition) == ("Охрана", "Охрана объекта")

    @pytest.mark.parametrize("title", ["", "   ", "\t\n"])
    def test_blank_title_is_refused_with_its_code_and_nothing_is_written(
        self, db_session, factories, title
    ):
        before = _count(db_session, FamilyCategory)
        with pytest.raises(WorkFamilyError) as exc:
            create_category(
                db_session, title=title, definition="Определение", actor_id=_user_id(factories)
            )
        assert exc.value.code == REFUSE_CATEGORY_BLANK_TITLE
        assert str(exc.value) == _TEXT_BLANK_TITLE
        assert _count(db_session, FamilyCategory) == before

    @pytest.mark.parametrize("definition", ["", "   ", "\t\n"])
    def test_blank_definition_is_refused_with_its_code_and_nothing_is_written(
        self, db_session, factories, definition
    ):
        before = _count(db_session, FamilyCategory)
        with pytest.raises(WorkFamilyError) as exc:
            create_category(
                db_session, title=_unique("Имя"), definition=definition,
                actor_id=_user_id(factories),
            )
        assert exc.value.code == REFUSE_CATEGORY_BLANK_DEFINITION
        assert str(exc.value) == _TEXT_BLANK_DEFINITION
        assert _count(db_session, FamilyCategory) == before

    def test_both_blank_reports_the_title_first(self, db_session, factories):
        with pytest.raises(WorkFamilyError) as exc:
            create_category(db_session, title=" ", definition=" ", actor_id=_user_id(factories))
        assert exc.value.code == REFUSE_CATEGORY_BLANK_TITLE

    @pytest.mark.parametrize("title", ["Работа", "работа", "  РАБОТА  ", "рАбОтА\t"])
    def test_duplicate_of_a_seed_title_ignoring_case_and_edge_spaces_is_refused(
        self, db_session, factories, title
    ):
        before = _count(db_session, FamilyCategory)
        with pytest.raises(WorkFamilyError) as exc:
            create_category(
                db_session, title=title, definition="Другое определение",
                actor_id=_user_id(factories),
            )
        assert exc.value.code == REFUSE_CATEGORY_DUPLICATE
        assert str(exc.value) == f"Категория «{title.strip()}» уже есть."
        assert _count(db_session, FamilyCategory) == before

    def test_duplicate_of_an_operator_category_is_refused(self, db_session, factories):
        existing = _operator_category(db_session, factories, title="Своя категория")
        with pytest.raises(WorkFamilyError) as exc:
            create_category(
                db_session, title=" СВОЯ категория ", definition="О", actor_id=_user_id(factories)
            )
        assert exc.value.code == REFUSE_CATEGORY_DUPLICATE
        assert db_session.get(FamilyCategory, existing.id).title == "Своя категория"

    def test_a_title_that_only_looks_similar_passes(self, db_session, factories):
        assert create_category(
            db_session, title="Работа 2", definition="О", actor_id=_user_id(factories)
        ).id


# ---------------------------------------------------------------------------
#  Правка категории
# ---------------------------------------------------------------------------

class TestUpdateCategory:
    def test_renames_and_redefines(self, db_session, factories):
        category = _operator_category(db_session, factories)
        updated = update_category(
            db_session, category_id=category.id, title=" Новое имя ", definition=" Новое определение ",
            actor_id=_user_id(factories),
        )
        assert (updated.title, updated.definition) == ("Новое имя", "Новое определение")

    def test_unset_fields_are_left_alone(self, db_session, factories):
        category = _operator_category(db_session, factories, title="Имя", definition="Определение")
        update_category(db_session, category_id=category.id, definition="Другое", actor_id=1)
        update_category(db_session, category_id=category.id, title="Другое имя", actor_id=1)
        stored = _fresh(db_session, FamilyCategory, category.id)
        assert (stored.title, stored.definition) == ("Другое имя", "Другое")

    def test_nothing_passed_changes_nothing(self, db_session, factories):
        category = _operator_category(db_session, factories, title="Имя", definition="Определение")
        update_category(db_session, category_id=category.id, actor_id=1)
        stored = _fresh(db_session, FamilyCategory, category.id)
        assert (stored.title, stored.definition) == ("Имя", "Определение")

    def test_a_seed_category_is_renamed_and_keeps_its_key(self, db_session):
        work = db_session.query(FamilyCategory).filter_by(seed_key="work").one()
        update_category(db_session, category_id=work.id, title="Работы и материалы", actor_id=1)
        stored = _fresh(db_session, FamilyCategory, work.id)
        assert (stored.title, stored.seed_key) == ("Работы и материалы", "work")

    @pytest.mark.parametrize("title", ["", "   ", "\t"])
    def test_blank_title_is_refused_and_state_is_kept(self, db_session, factories, title):
        category = _operator_category(db_session, factories, title="Имя")
        with pytest.raises(WorkFamilyError) as exc:
            update_category(db_session, category_id=category.id, title=title, actor_id=1)
        assert exc.value.code == REFUSE_CATEGORY_BLANK_TITLE
        assert _fresh(db_session, FamilyCategory, category.id).title == "Имя"

    @pytest.mark.parametrize("definition", ["", "   ", "\t"])
    def test_blank_definition_is_refused_and_state_is_kept(self, db_session, factories, definition):
        category = _operator_category(db_session, factories, definition="Определение")
        with pytest.raises(WorkFamilyError) as exc:
            update_category(db_session, category_id=category.id, definition=definition, actor_id=1)
        assert exc.value.code == REFUSE_CATEGORY_BLANK_DEFINITION
        assert _fresh(db_session, FamilyCategory, category.id).definition == "Определение"

    def test_none_is_not_a_value(self, db_session, factories):
        category = _operator_category(db_session, factories)
        with pytest.raises(WorkFamilyError) as exc:
            update_category(db_session, category_id=category.id, title=None, actor_id=1)
        assert exc.value.code == REFUSE_CATEGORY_BLANK_TITLE

    @pytest.mark.parametrize("title", ["Работа", "  РАБОТА ", "работа"])
    def test_rename_to_another_categorys_title_is_a_duplicate(self, db_session, factories, title):
        category = _operator_category(db_session, factories, title="Имя")
        with pytest.raises(WorkFamilyError) as exc:
            update_category(db_session, category_id=category.id, title=title, actor_id=1)
        assert exc.value.code == REFUSE_CATEGORY_DUPLICATE
        assert _fresh(db_session, FamilyCategory, category.id).title == "Имя"

    def test_rename_to_its_own_title_in_another_case_passes(self, db_session):
        work = db_session.query(FamilyCategory).filter_by(seed_key="work").one()
        updated = update_category(db_session, category_id=work.id, title="  РАБОТА ", actor_id=1)
        assert updated.title == "РАБОТА"

    def test_missing_category_is_refused_with_its_id(self, db_session):
        with pytest.raises(WorkFamilyError) as exc:
            update_category(db_session, category_id=999_999_999, title="Имя", actor_id=1)
        assert exc.value.code == REFUSE_CATEGORY_NOT_FOUND
        assert exc.value.category_id == 999_999_999

    def test_a_concurrent_rename_to_a_taken_title_is_a_duplicate_through_the_key(
        self, db_session, factories, monkeypatch
    ):
        """Вторая линия: проверка дубля не увидела занятое имя (его заняла
        параллельная транзакция) — ключ `uq_family_categories_title` отвергает
        запись, и отказ тот же код, что у проверки, а не сырой `IntegrityError`."""
        category = _operator_category(db_session, factories, title="Имя")
        monkeypatch.setattr(family_categories_module, "_duplicate_exists", lambda *a, **k: False)
        with pytest.raises(WorkFamilyError) as exc:
            update_category(db_session, category_id=category.id, title=" работа ", actor_id=1)
        assert exc.value.code == REFUSE_CATEGORY_DUPLICATE
        assert _fresh(db_session, FamilyCategory, category.id).title == "Имя"

    def test_takes_the_category_for_update_and_nothing_else(self, db_session, factories):
        """Решение 20: правка категории берёт её `FOR UPDATE` и больше ни одного
        замка."""
        category = _operator_category(db_session, factories)
        sent: list[str] = []
        connection = db_session.connection()

        def _capture(conn, cursor, statement, *args):
            sent.append(" ".join(statement.split()))

        event.listen(connection, "before_cursor_execute", _capture)
        try:
            update_category(
                db_session, category_id=category.id, title=_unique("Имя"), actor_id=1
            )
        finally:
            event.remove(connection, "before_cursor_execute", _capture)
        locking = [s for s in sent if " FOR UPDATE" in s or " FOR SHARE" in s]
        assert len(locking) == 1, locking
        assert "FROM family_categories" in locking[0] and locking[0].endswith("FOR UPDATE")

    def test_missing_category_with_a_blank_title_reports_the_title_first(self, db_session):
        with pytest.raises(WorkFamilyError) as exc:
            update_category(db_session, category_id=999_999_999, title=" ", actor_id=1)
        assert exc.value.code == REFUSE_CATEGORY_BLANK_TITLE


# ---------------------------------------------------------------------------
#  Блокировки справочника
# ---------------------------------------------------------------------------

def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


class TestLockCategories:
    def test_shared_lock_statement_is_for_share_in_id_order(self):
        sql = _sql(_lock_categories_statement([3, 1], exclusive=False))
        assert "FOR SHARE" in sql and "FOR UPDATE" not in sql
        assert "ORDER BY family_categories.id" in sql
        assert "IN" in sql

    def test_exclusive_lock_statement_is_for_update_in_id_order(self):
        sql = _sql(_lock_categories_statement([3, 1], exclusive=True))
        assert "FOR UPDATE" in sql and "FOR SHARE" not in sql
        assert "ORDER BY family_categories.id" in sql

    def test_none_locks_all_categories_without_a_filter(self):
        sql = _sql(_lock_categories_statement(None, exclusive=False))
        assert "FOR SHARE" in sql
        assert "WHERE" not in sql
        assert "ORDER BY family_categories.id" in sql

    def test_none_shared_runs_on_the_live_schema(self, db_session):
        lock_categories(db_session, None, exclusive=False)

    def test_an_empty_list_is_a_no_op_that_sends_nothing(self, db_session):
        sent = []
        connection = db_session.connection()

        def _capture(conn, cursor, statement, *args):
            sent.append(statement)

        event.listen(connection, "before_cursor_execute", _capture)
        try:
            lock_categories(db_session, [], exclusive=True)
        finally:
            event.remove(connection, "before_cursor_execute", _capture)
        assert sent == []

    def test_a_missing_id_is_skipped_by_the_lock_and_found_by_require(self, db_session):
        lock_categories(db_session, [999_999_999], exclusive=True)
        with pytest.raises(WorkFamilyError) as exc:
            require_category(db_session, 999_999_999)
        assert exc.value.code == REFUSE_CATEGORY_NOT_FOUND

    def test_require_returns_the_row(self, db_session):
        work = db_session.query(FamilyCategory).filter_by(seed_key="work").one()
        assert require_category(db_session, work.id).id == work.id
        assert require_category(db_session, work.id, exclusive=True).id == work.id


# ---------------------------------------------------------------------------
#  Удаление категории
# ---------------------------------------------------------------------------

class TestDeleteCategory:
    def test_empty_category_is_deleted(self, db_session, factories):
        category = _operator_category(db_session, factories)
        delete_category(db_session, category_id=category.id, actor_id=1)
        db_session.expire_all()
        assert db_session.get(FamilyCategory, category.id) is None

    def test_an_empty_seed_category_is_deleted_too(self, db_session):
        engineering = db_session.query(FamilyCategory).filter_by(seed_key="engineering_system").one()
        delete_category(db_session, category_id=engineering.id, actor_id=1)
        db_session.expire_all()
        assert db_session.get(FamilyCategory, engineering.id) is None

    def test_category_with_families_is_refused_with_their_count_and_nothing_is_deleted(
        self, db_session, factories
    ):
        actor = _user_id(factories)
        category = _operator_category(db_session, factories, title="Занятая")
        families = [_family(db_session, actor, category_id=category.id) for _ in range(3)]
        archived = _family(db_session, actor, category_id=category.id)
        archived.status = FamilyStatus.archived.value
        db_session.flush()
        draft_job = _discovery_job(db_session, factories)
        draft = _draft(db_session, draft_job, family_category_id=category.id)

        with pytest.raises(WorkFamilyError) as exc:
            delete_category(db_session, category_id=category.id, actor_id=actor)

        assert exc.value.code == REFUSE_CATEGORY_IN_USE
        assert exc.value.family_count == 4
        assert str(exc.value) == "Категорию «Занятая» носят 4 семей — удалить можно только пустую."
        db_session.expire_all()
        assert db_session.get(FamilyCategory, category.id) is not None
        assert {f.family_category_id for f in families} == {category.id}
        assert db_session.get(FamilyDraft, draft.id).family_category_id == category.id

    def test_drafts_lose_the_category_and_its_proposals_go_while_others_stay(
        self, db_session, factories
    ):
        doomed = _operator_category(db_session, factories, title="Удаляемая")
        kept = _operator_category(db_session, factories, title="Остающаяся")
        job = _discovery_job(db_session, factories)
        doomed_drafts = [_draft(db_session, job, family_category_id=doomed.id) for _ in range(2)]
        kept_draft = _draft(db_session, job, family_category_id=kept.id)
        no_category_draft = _draft(db_session, job)
        proposals = [
            _proposal(db_session, job, _plain_family(db_session), doomed),
            _proposal(db_session, job, _plain_family(db_session), kept),
        ]

        delete_category(db_session, category_id=doomed.id, actor_id=1)

        db_session.expire_all()
        assert db_session.get(FamilyCategory, doomed.id) is None
        assert [db_session.get(FamilyDraft, d.id).family_category_id for d in doomed_drafts] == [
            None, None,
        ]
        assert db_session.get(FamilyDraft, kept_draft.id).family_category_id == kept.id
        assert db_session.get(FamilyDraft, no_category_draft.id).family_category_id is None
        remaining = db_session.query(FamilyCategoryProposal).filter_by(job_id=job.id).all()
        assert [(p.family_id, p.family_category_id) for p in remaining] == [
            (proposals[1].family_id, kept.id)
        ]

    def test_missing_category_is_refused(self, db_session):
        with pytest.raises(WorkFamilyError) as exc:
            delete_category(db_session, category_id=999_999_999, actor_id=1)
        assert exc.value.code == REFUSE_CATEGORY_NOT_FOUND

    def test_statements_run_in_the_order_of_the_spec(self, db_session, factories):
        """Спека §2.9: категория `FOR UPDATE` → число семей → черновики `FOR UPDATE` и
        их правка → предложения `FOR UPDATE` и их удаление → `DELETE` категории."""
        category = _operator_category(db_session, factories)
        job = _discovery_job(db_session, factories)
        _draft(db_session, job, family_category_id=category.id)
        _proposal(db_session, job, _plain_family(db_session), category)

        sent: list[str] = []
        connection = db_session.connection()

        def _capture(conn, cursor, statement, *args):
            sent.append(" ".join(statement.split()))

        event.listen(connection, "before_cursor_execute", _capture)
        try:
            delete_category(db_session, category_id=category.id, actor_id=1)
        finally:
            event.remove(connection, "before_cursor_execute", _capture)

        def first(*needles):
            for index, statement in enumerate(sent):
                if all(needle in statement for needle in needles):
                    return index
            raise AssertionError(f"нет запроса с {needles}: {sent}")

        order = [
            first("FROM family_categories", "ORDER BY family_categories.id FOR UPDATE"),
            first("count(work_families.id)", "FROM work_families"),
            first("FROM family_drafts", "ORDER BY family_drafts.id FOR UPDATE"),
            first("UPDATE family_drafts SET family_category_id"),
            first(
                "FROM family_category_proposals",
                "ORDER BY family_category_proposals.job_id, family_category_proposals.family_id "
                "FOR UPDATE",
            ),
            first("DELETE FROM family_category_proposals"),
            first("DELETE FROM family_categories"),
        ]
        assert order == sorted(order) and len(set(order)) == 7
        # Замок категории — исключительный и единственный на справочнике: FOR SHARE
        # той же транзакции здесь означал бы повышение режима.
        assert not any("FROM family_categories" in s and "FOR SHARE" in s for s in sent)

    def test_the_in_use_text_names_the_current_title_not_a_stale_one(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """Замок категории перечитывает строку (`populate_existing`): транзакция, уже
        державшая объект категории, после переименования другой транзакцией называет
        в отказе новое имя, а не то, что лежит в её карте объектов."""
        actor, category_id = _committed_actor_and_category(
            committing_db, committing_factories, _unique("Старое имя")
        )
        create_family(
            committing_db, title=_unique("Семья"), unit_name=None, definition="О",
            actor_id=actor, family_category_id=category_id,
        )
        committing_db.commit()
        new_title = _unique("Новое имя")
        holder = committing_session_factory()
        renamer = committing_session_factory()
        try:
            # Ссылка держит объект в карте сессии: без неё сборщик мусора выбросил бы
            # его, и следующее чтение было бы свежим само по себе.
            stale = holder.get(FamilyCategory, category_id)
            assert stale.title != new_title
            update_category(renamer, category_id=category_id, title=new_title, actor_id=actor)
            renamer.commit()
            with pytest.raises(WorkFamilyError) as exc:
                delete_category(holder, category_id=category_id, actor_id=actor)
        finally:
            holder.rollback()
            holder.close()
            renamer.close()
        assert exc.value.code == REFUSE_CATEGORY_IN_USE
        assert f"«{new_title}»" in str(exc.value)

    def test_a_family_that_slipped_in_gives_category_in_use_not_an_integrity_error(
        self, db_session, factories, monkeypatch
    ):
        """Ключ `RESTRICT` страхует гонку: проверка числа семей видит ноль, а семья со
        ссылкой уже есть — отказ тот же код, что у проверки, с настоящим числом."""
        actor = _user_id(factories)
        category = _operator_category(db_session, factories, title="Гонка")
        _family(db_session, actor, category_id=category.id)
        real_count = family_categories_module._family_count
        calls = {"n": 0}

        def _stale_then_real(db, category_id):
            calls["n"] += 1
            return 0 if calls["n"] == 1 else real_count(db, category_id)

        monkeypatch.setattr(family_categories_module, "_family_count", _stale_then_real)

        with pytest.raises(WorkFamilyError) as exc:
            delete_category(db_session, category_id=category.id, actor_id=actor)

        assert exc.value.code == REFUSE_CATEGORY_IN_USE
        assert exc.value.family_count == 1
        db_session.expire_all()
        assert db_session.get(FamilyCategory, category.id) is not None


def _plain_family(db) -> WorkFamily:
    """Семья без пользователя-автора (строка seed): годится как цель предложения."""
    family = WorkFamily(
        title=_unique("Семья"), status=FamilyStatus.draft.value, seed_key=_unique("seed"),
    )
    db.add(family)
    db.flush()
    return family


# ---------------------------------------------------------------------------
#  Категория у семьи: create_family, update_family, activate_family
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _captured_sql(db):
    """Тексты запросов сессии в порядке отправки, пробелы схлопнуты."""
    sent: list[str] = []
    connection = db.connection()

    def _capture(conn, cursor, statement, *args):
        sent.append(" ".join(statement.split()))

    event.listen(connection, "before_cursor_execute", _capture)
    try:
        yield sent
    finally:
        event.remove(connection, "before_cursor_execute", _capture)


def _index(sent: list[str], *needles: str) -> int:
    for index, statement in enumerate(sent):
        if all(needle in statement for needle in needles):
            return index
    raise AssertionError(f"нет запроса с {needles}: {sent}")


def _is_category_share_lock(statement: str) -> bool:
    return "FROM family_categories" in statement and statement.endswith("FOR SHARE")


class TestCategoryLockComesFirst:
    """Решение 20 и §2.9: ссылающийся на категорию берёт её `FOR SHARE` раньше своих
    строк семей (и контекстов) — первый замок транзакции, а не после записи."""

    def test_create_family_locks_the_category_before_writing_the_family(
        self, db_session, factories
    ):
        actor = _user_id(factories)
        with _captured_sql(db_session) as sent:
            _family(db_session, actor, category_id=_work_id(db_session))
        assert _index(sent, "FROM family_categories", "FOR SHARE") < _index(
            sent, "INSERT INTO work_families"
        )

    def test_update_family_locks_the_category_before_the_family(self, db_session, factories):
        actor = _user_id(factories)
        family = _family(db_session, actor)
        work = _work_id(db_session)
        with _captured_sql(db_session) as sent:
            update_family(db_session, family_id=family.id, family_category_id=work, actor_id=actor)
        locking = [s for s in sent if s.endswith(("FOR SHARE", "FOR UPDATE"))]
        assert locking and _is_category_share_lock(locking[0]), locking
        assert any("FROM work_families" in s and s.endswith("FOR UPDATE") for s in locking[1:])

    def test_create_family_from_suggestion_locks_the_category_first(self, db_session, factories):
        scene = _scene(db_session, factories)
        suggestion = _published(db_session, scene.context_ids[0], family_id=None)
        work = _work_id(db_session)
        with _captured_sql(db_session) as sent:
            create_family_from_suggestion(
                db_session, suggestion_id=suggestion.id, title=_unique("Семья"),
                definition="О", family_category_id=work, actor_id=scene.user.id,
            )
        locking = [s for s in sent if s.endswith(("FOR SHARE", "FOR UPDATE"))]
        assert len(locking) > 1 and _is_category_share_lock(locking[0]), locking


class TestCreateFamilyWithCategory:
    def test_category_is_stored_and_the_event_is_unchanged(self, db_session, factories):
        actor = _user_id(factories)
        family = _family(db_session, actor, category_id=_work_id(db_session), unit_name="M2")
        assert _fresh(db_session, WorkFamily, family.id).family_category_id == _work_id(db_session)
        (created,) = _events(db_session, family.id, "family_created")
        assert created.payload == {"title": family.title, "unit": "M2", "origin": "operator"}

    def test_category_is_optional(self, db_session, factories):
        assert _family(db_session, _user_id(factories)).family_category_id is None

    def test_missing_category_is_refused_and_no_family_is_written(self, db_session, factories):
        before = _count(db_session, WorkFamily)
        with pytest.raises(WorkFamilyError) as exc:
            _family(db_session, _user_id(factories), category_id=999_999_999)
        assert exc.value.code == REFUSE_CATEGORY_NOT_FOUND
        assert _count(db_session, WorkFamily) == before

    def test_discovery_origin_is_written_to_the_event(self, db_session, factories):
        family = create_family(
            db_session, title=_unique("Из открытия"), unit_name=None, definition="О",
            actor_id=_user_id(factories), family_category_id=_work_id(db_session),
            origin="discovery",
        )
        (created,) = _events(db_session, family.id, "family_created")
        assert created.payload["origin"] == "discovery"

    @pytest.mark.parametrize("origin", ["seed", "import", ""])
    def test_origin_outside_operator_and_discovery_is_refused_before_any_write(
        self, db_session, factories, origin
    ):
        """`seed` входит в закрытое множество журнала, но семью оператора им метить
        нельзя: отказ до записи, а не событие с чужим происхождением."""
        before = _count(db_session, WorkFamily)
        events_before = _count(db_session, SemanticEvent)
        with pytest.raises(ValueError):
            create_family(
                db_session, title=_unique("Чужое происхождение"), unit_name=None, definition="О",
                actor_id=_user_id(factories), origin=origin,
            )
        assert _count(db_session, WorkFamily) == before
        assert _count(db_session, SemanticEvent) == events_before


class TestUpdateFamilyCategory:
    def test_change_writes_one_changed_element_with_from_and_to(self, db_session, factories):
        actor = _user_id(factories)
        work, costs = _work_id(db_session), db_session.query(FamilyCategory).filter_by(
            seed_key="costs_services"
        ).one().id
        family = _family(db_session, actor)

        update_family(db_session, family_id=family.id, family_category_id=work, actor_id=actor)
        update_family(db_session, family_id=family.id, family_category_id=costs, actor_id=actor)

        updated = _events(db_session, family.id, "family_updated")
        assert [event.payload for event in updated] == [
            {"changed": [{"field": "family_category_id", "from": None, "to": work}]},
            {"changed": [{"field": "family_category_id", "from": work, "to": costs}]},
        ]
        assert _fresh(db_session, WorkFamily, family.id).family_category_id == costs

    def test_the_same_category_writes_no_event(self, db_session, factories):
        actor = _user_id(factories)
        family = _family(db_session, actor, category_id=_work_id(db_session))
        update_family(
            db_session, family_id=family.id, family_category_id=_work_id(db_session), actor_id=actor
        )
        assert _events(db_session, family.id, "family_updated") == []

    def test_category_and_title_together_give_both_elements(self, db_session, factories):
        actor = _user_id(factories)
        family = _family(db_session, actor, title="Старое")
        update_family(
            db_session, family_id=family.id, title="Новое",
            family_category_id=_work_id(db_session), actor_id=actor,
        )
        (updated,) = _events(db_session, family.id, "family_updated")
        assert [item["field"] for item in updated.payload["changed"]] == [
            "title", "family_category_id",
        ]

    def test_category_is_left_alone_when_not_passed(self, db_session, factories):
        actor = _user_id(factories)
        family = _family(db_session, actor, category_id=_work_id(db_session))
        update_family(db_session, family_id=family.id, title="Новое имя", actor_id=actor)
        assert _fresh(db_session, WorkFamily, family.id).family_category_id == _work_id(db_session)

    def test_unset_title_is_also_not_a_change(self, db_session, factories):
        actor = _user_id(factories)
        family = _family(db_session, actor, title="Имя")
        update_family(
            db_session, family_id=family.id, title=UNSET,
            family_category_id=_work_id(db_session), actor_id=actor,
        )
        assert _fresh(db_session, WorkFamily, family.id).title == "Имя"

    def test_none_on_a_draft_clears_the_category_and_is_logged(self, db_session, factories):
        actor = _user_id(factories)
        family = _family(db_session, actor, category_id=_work_id(db_session))
        update_family(db_session, family_id=family.id, family_category_id=None, actor_id=actor)
        assert _fresh(db_session, WorkFamily, family.id).family_category_id is None
        (updated,) = _events(db_session, family.id, "family_updated")
        assert updated.payload == {
            "changed": [
                {"field": "family_category_id", "from": _work_id(db_session), "to": None}
            ]
        }

    def test_none_on_an_active_family_is_refused_and_nothing_changes(self, db_session, factories):
        actor = _user_id(factories)
        family = _family(db_session, actor, category_id=_work_id(db_session))
        activate_family(db_session, family_id=family.id, actor_id=actor)
        with pytest.raises(WorkFamilyError) as exc:
            update_family(db_session, family_id=family.id, family_category_id=None, actor_id=actor)
        assert exc.value.code == REFUSE_CLEAR_CATEGORY_ACTIVE
        assert str(exc.value) == _TEXT_CLEAR_ACTIVE
        assert _fresh(db_session, WorkFamily, family.id).family_category_id == _work_id(db_session)
        assert _events(db_session, family.id, "family_updated") == []

    def test_none_on_an_active_family_without_category_is_refused_too(self, db_session, factories):
        """176 ранее активных семей без категории: «снять» им тоже нельзя."""
        actor = _user_id(factories)
        family = _family(db_session, actor)
        db_session.execute(
            sa.update(WorkFamily).where(WorkFamily.id == family.id).values(
                status="active", activated_by=actor, activated_at=sa.func.now()
            )
        )
        db_session.expire_all()
        with pytest.raises(WorkFamilyError) as exc:
            update_family(db_session, family_id=family.id, family_category_id=None, actor_id=actor)
        assert exc.value.code == REFUSE_CLEAR_CATEGORY_ACTIVE

    def test_change_on_an_active_family_passes(self, db_session, factories):
        actor = _user_id(factories)
        family = _family(db_session, actor, category_id=_work_id(db_session))
        activate_family(db_session, family_id=family.id, actor_id=actor)
        costs = db_session.query(FamilyCategory).filter_by(seed_key="costs_services").one().id
        update_family(db_session, family_id=family.id, family_category_id=costs, actor_id=actor)
        assert _fresh(db_session, WorkFamily, family.id).family_category_id == costs

    def test_missing_category_is_refused_and_the_family_is_unchanged(self, db_session, factories):
        actor = _user_id(factories)
        family = _family(db_session, actor, title="Имя")
        with pytest.raises(WorkFamilyError) as exc:
            update_family(
                db_session, family_id=family.id, title="Другое", family_category_id=999_999_999,
                actor_id=actor,
            )
        assert exc.value.code == REFUSE_CATEGORY_NOT_FOUND
        stored = _fresh(db_session, WorkFamily, family.id)
        assert (stored.title, stored.family_category_id) == ("Имя", None)

    def test_archived_family_keeps_its_own_refusal(self, db_session, factories):
        actor = _user_id(factories)
        family = _family(db_session, actor)
        archive_family(db_session, family_id=family.id, actor_id=actor)
        with pytest.raises(WorkFamilyError) as exc:
            update_family(
                db_session, family_id=family.id, family_category_id=_work_id(db_session),
                actor_id=actor,
            )
        assert exc.value.code == REFUSE_UPDATE_ARCHIVED


class TestActivateFamilyNeedsCategory:
    def test_family_without_category_is_refused_and_stays_a_draft(self, db_session, factories):
        actor = _user_id(factories)
        family = _family(db_session, actor)
        with pytest.raises(WorkFamilyError) as exc:
            activate_family(db_session, family_id=family.id, actor_id=actor)
        assert exc.value.code == REFUSE_ACTIVATE_WITHOUT_CATEGORY
        assert str(exc.value) == _TEXT_NO_CATEGORY
        stored = _fresh(db_session, WorkFamily, family.id)
        assert (stored.status, stored.activated_by) == (FamilyStatus.draft.value, None)
        assert _events(db_session, family.id, "family_activated") == []

    def test_family_with_category_is_activated_and_keeps_it(self, db_session, factories):
        actor = _user_id(factories)
        family = _family(db_session, actor, category_id=_work_id(db_session))
        activated = activate_family(db_session, family_id=family.id, actor_id=actor)
        assert (activated.status, activated.family_category_id) == (
            FamilyStatus.active.value, _work_id(db_session),
        )

    def test_missing_definition_is_reported_before_the_category(self, db_session, factories):
        actor = _user_id(factories)
        family = _family(db_session, actor, definition=None)
        with pytest.raises(WorkFamilyError) as exc:
            activate_family(db_session, family_id=family.id, actor_id=actor)
        assert exc.value.code == REFUSE_ACTIVATE_WITHOUT_DEFINITION

    def test_category_set_afterwards_lets_the_activation_through(self, db_session, factories):
        actor = _user_id(factories)
        family = _family(db_session, actor)
        with pytest.raises(WorkFamilyError):
            activate_family(db_session, family_id=family.id, actor_id=actor)
        update_family(
            db_session, family_id=family.id, family_category_id=_work_id(db_session), actor_id=actor
        )
        assert activate_family(db_session, family_id=family.id, actor_id=actor).status == "active"


class TestCreateFamilyFromSuggestionNeedsCategory:
    def test_family_is_created_active_with_the_chosen_category(self, db_session, factories):
        scene = _scene(db_session, factories)
        suggestion = _published(db_session, scene.context_ids[0], family_id=None)
        costs = db_session.query(FamilyCategory).filter_by(seed_key="costs_services").one().id

        family_id = create_family_from_suggestion(
            db_session, suggestion_id=suggestion.id, title="Гарантия", definition="О гарантии",
            family_category_id=costs, actor_id=scene.user.id,
        )

        family = _fresh(db_session, WorkFamily, family_id)
        assert (family.status, family.family_category_id) == ("active", costs)

    def test_missing_category_is_refused_before_anything_is_written(self, db_session, factories):
        scene = _scene(db_session, factories)
        suggestion = _published(db_session, scene.context_ids[0], family_id=None)
        before = _count(db_session, WorkFamily)

        with pytest.raises(WorkFamilyError) as exc:
            create_family_from_suggestion(
                db_session, suggestion_id=suggestion.id, title="Новая", definition="О",
                family_category_id=999_999_999, actor_id=scene.user.id,
            )

        assert exc.value.code == REFUSE_CATEGORY_NOT_FOUND
        assert _count(db_session, WorkFamily) == before
        assert _fresh(db_session, FamilySuggestion, suggestion.id).decision is None


# ---------------------------------------------------------------------------
#  Строка семьи и фильтр по категории
# ---------------------------------------------------------------------------

class TestFamilyRowAndFilter:
    def _three(self, db_session, factories):
        actor = _user_id(factories)
        other = _operator_category(db_session, factories, title="Другая")
        with_work = _family(db_session, actor, category_id=_work_id(db_session))
        with_other = _family(db_session, actor, category_id=other.id)
        without = _family(db_session, actor)
        return with_work, with_other, without, other

    def _ids(self, db_session, **kwargs):
        rows = crud_semantic.list_families(db_session, status=None, unit_id=None, **kwargs)
        return {row["id"] for row in rows}

    def test_row_carries_the_category_id_and_title(self, db_session, factories):
        with_work, _, without, _ = self._three(db_session, factories)
        rows = {r["id"]: r for r in crud_semantic.list_families(db_session, status=None, unit_id=None)}
        assert (rows[with_work.id]["family_category_id"], rows[with_work.id]["family_category_title"]) == (
            _work_id(db_session), "Работа",
        )
        assert (rows[without.id]["family_category_id"], rows[without.id]["family_category_title"]) == (
            None, None,
        )

    def test_get_family_row_has_the_same_keys_as_the_list_row(self, db_session, factories):
        with_work, *_ = self._three(db_session, factories)
        single = crud_semantic.get_family_row(db_session, family_id=with_work.id)
        listed = next(
            r for r in crud_semantic.list_families(db_session, status=None, unit_id=None)
            if r["id"] == with_work.id
        )
        assert single.keys() == listed.keys()
        assert {"family_category_id", "family_category_title"} <= single.keys()

    def test_filter_none_returns_only_families_without_a_category(self, db_session, factories):
        with_work, with_other, without, _ = self._three(db_session, factories)
        ids = self._ids(db_session, family_category_id="none")
        assert without.id in ids
        assert with_work.id not in ids and with_other.id not in ids

    def test_filter_by_id_returns_only_that_category(self, db_session, factories):
        with_work, with_other, without, other = self._three(db_session, factories)
        assert self._ids(db_session, family_category_id=other.id) == {with_other.id}
        ids = self._ids(db_session, family_category_id=_work_id(db_session))
        assert with_work.id in ids and with_other.id not in ids and without.id not in ids

    def test_no_filter_returns_all_three(self, db_session, factories):
        with_work, with_other, without, _ = self._three(db_session, factories)
        assert {with_work.id, with_other.id, without.id} <= self._ids(db_session)

    def test_filter_combines_with_status(self, db_session, factories):
        with_work, _, without, _ = self._three(db_session, factories)
        activate_family(db_session, family_id=with_work.id, actor_id=_user_id(factories))
        rows = crud_semantic.list_families(
            db_session, status="active", unit_id=None, family_category_id=_work_id(db_session)
        )
        assert with_work.id in {row["id"] for row in rows}
        assert without.id not in {row["id"] for row in rows}

    def test_category_list_rows_count_families_of_every_status(self, db_session, factories):
        actor = _user_id(factories)
        category = _operator_category(db_session, factories)
        _family(db_session, actor, category_id=category.id)
        archived = _family(db_session, actor, category_id=category.id)
        archived.status = "archived"
        db_session.flush()
        rows = {row["id"]: row for row in crud_semantic.list_family_categories(db_session)}
        assert rows[category.id]["family_count"] == 2
        assert rows[_work_id(db_session)]["family_count"] == 0
        assert set(rows[category.id]) == {"id", "title", "definition", "seed_key", "family_count"}


# ---------------------------------------------------------------------------
#  Маршруты: права, статусы, тексты
# ---------------------------------------------------------------------------

_CATEGORY_ROUTES = [
    ("GET", f"{BASE}/family-categories", None),
    ("POST", f"{BASE}/family-categories", {"title": "Х", "definition": "Х"}),
    ("PATCH", f"{BASE}/family-categories/1", {"title": "Х"}),
    ("DELETE", f"{BASE}/family-categories/1", None),
]
_CHANGED_FAMILY_ROUTES = [
    ("POST", f"{BASE}/families", {"title": "Х", "family_category_id": 1}),
    ("PATCH", f"{BASE}/families/1", {"family_category_id": 1}),
    ("POST", f"{BASE}/families/1/activate", None),
    ("GET", f"{BASE}/families?family_category_id=none", None),
    ("POST", f"{BASE}/suggestions/1/create-family",
     {"title": "Х", "definition": "Х", "family_category_id": 1}),
]


class TestRoutePermissions:
    @pytest.mark.parametrize(("method", "path", "body"), _CATEGORY_ROUTES + _CHANGED_FAMILY_ROUTES)
    def test_member_is_rejected_with_403(self, member_client, method, path, body):
        response = member_client.request(method, path, json=body)
        assert response.status_code == 403, f"{method} {path} -> {response.status_code}"

    @pytest.mark.parametrize(("method", "path", "body"), _CATEGORY_ROUTES + _CHANGED_FAMILY_ROUTES)
    def test_anonymous_is_rejected(self, anon_client, method, path, body):
        response = anon_client.request(method, path, json=body)
        assert response.status_code in (401, 403), f"{method} {path} -> {response.status_code}"


class TestStatusMap:
    """Каждый новый код — в карте статусов (таблица спеки §2.12), ни один не падает в
    `AssertionError`."""

    @pytest.mark.parametrize(
        ("code", "status"),
        [
            ("category_not_found", 409),
            ("category_in_use", 409),
            ("category_duplicate", 409),
            ("category_blank_title", 422),
            ("category_blank_definition", 422),
            ("activate_without_category", 422),
            ("clear_category_active", 422),
        ],
    )
    def test_code_has_the_status_of_the_spec_table(self, code, status):
        assert _status_for_code(code) == status

    def test_unknown_code_still_fails_loudly(self):
        with pytest.raises(AssertionError):
            _status_for_code("category_unknown")


def _detail(response) -> dict:
    return response.json()["detail"]


class TestCategoryRoutes:
    def test_list_returns_the_seeds_with_family_counts(self, admin_client, db_session):
        db_session.commit()
        response = admin_client.get(f"{BASE}/family-categories")
        assert response.status_code == 200
        items = response.json()["items"]
        assert [item["seed_key"] for item in items[:3]] == ["work", "engineering_system", "costs_services"]
        assert all(set(item) == {"id", "title", "definition", "seed_key", "family_count"} for item in items)
        assert all(item["family_count"] == 0 for item in items)

    def test_list_counts_families_per_category(self, admin_client, db_session):
        family_id = admin_client.post(
            f"{BASE}/families",
            json={"title": _unique("Семья"), "family_category_id": _work_id(db_session)},
        ).json()["id"]
        items = admin_client.get(f"{BASE}/family-categories").json()["items"]
        counts = {item["seed_key"]: item["family_count"] for item in items}
        assert counts["work"] == 1 and counts["costs_services"] == 0
        assert family_id

    def test_create_answers_201_with_the_row_and_zero_families(self, admin_client, db_session):
        response = admin_client.post(
            f"{BASE}/family-categories",
            json={"title": " Проектирование ", "definition": " Проектные работы. "},
        )
        assert response.status_code == 201
        body = response.json()
        assert (body["title"], body["definition"], body["seed_key"], body["family_count"]) == (
            "Проектирование", "Проектные работы.", None, 0,
        )
        db_session.expire_all()
        stored = db_session.get(FamilyCategory, body["id"])
        assert stored.created_by == admin_client.user.id

    def test_create_duplicate_is_409_with_the_spec_text(self, admin_client, db_session):
        db_session.commit()
        before = _count(db_session, FamilyCategory)
        response = admin_client.post(
            f"{BASE}/family-categories", json={"title": " РАБОТА ", "definition": "О"}
        )
        assert response.status_code == 409
        detail = _detail(response)
        assert (detail["code"], detail["message"]) == (
            "category_duplicate", "Категория «РАБОТА» уже есть.",
        )
        assert _count(db_session, FamilyCategory) == before

    def test_create_blank_title_is_422_with_the_spec_text(self, admin_client, db_session):
        db_session.commit()
        response = admin_client.post(
            f"{BASE}/family-categories", json={"title": "  ", "definition": "О"}
        )
        assert response.status_code == 422
        assert _detail(response) == {"code": "category_blank_title", "message": _TEXT_BLANK_TITLE}

    def test_create_blank_definition_is_422_with_the_spec_text(self, admin_client, db_session):
        db_session.commit()
        response = admin_client.post(
            f"{BASE}/family-categories", json={"title": "Имя", "definition": ""}
        )
        assert response.status_code == 422
        assert _detail(response) == {
            "code": "category_blank_definition", "message": _TEXT_BLANK_DEFINITION,
        }

    def test_create_without_a_field_is_a_plain_422(self, admin_client, db_session):
        db_session.commit()
        assert admin_client.post(f"{BASE}/family-categories", json={"title": "Имя"}).status_code == 422

    def test_patch_changes_the_fields_and_answers_the_row(self, admin_client, db_session, factories):
        category = _operator_category(db_session, factories, title="Старое")
        db_session.commit()
        response = admin_client.patch(
            f"{BASE}/family-categories/{category.id}",
            json={"title": "Новое", "definition": "Новое определение"},
        )
        assert response.status_code == 200
        assert (response.json()["title"], response.json()["definition"]) == ("Новое", "Новое определение")

    def test_patch_one_field_keeps_the_other(self, admin_client, db_session, factories):
        category = _operator_category(db_session, factories, title="Имя", definition="Определение")
        db_session.commit()
        response = admin_client.patch(
            f"{BASE}/family-categories/{category.id}", json={"definition": "Другое"}
        )
        assert (response.json()["title"], response.json()["definition"]) == ("Имя", "Другое")

    def test_patch_missing_category_is_409_category_not_found(self, admin_client, db_session):
        db_session.commit()
        response = admin_client.patch(f"{BASE}/family-categories/999999999", json={"title": "Имя"})
        assert response.status_code == 409
        assert _detail(response)["code"] == "category_not_found"
        assert _detail(response)["category_id"] == 999999999
        # Текст §2.12 дословно вокруг подстановки «…».
        message = _detail(response)["message"]
        assert message == "Категории «№ 999999999» больше нет — её удалили. Выберите другую."
        assert message.startswith("Категории «")
        assert message.endswith("» больше нет — её удалили. Выберите другую.")

    def test_patch_duplicate_title_is_409(self, admin_client, db_session, factories):
        category = _operator_category(db_session, factories, title="Имя")
        db_session.commit()
        response = admin_client.patch(
            f"{BASE}/family-categories/{category.id}", json={"title": "работа"}
        )
        assert response.status_code == 409
        assert _detail(response)["code"] == "category_duplicate"

    def test_patch_rename_race_through_the_key_is_409_not_500(
        self, admin_client, db_session, factories, monkeypatch
    ):
        """Вторая линия дубля на маршруте: проверка имени пропустила занятое имя
        (гонка), ключ `uq_family_categories_title` ловит его внутри точки сохранения —
        ответ `409 category_duplicate`, а не `500`, и имя прежнее."""
        category = _operator_category(db_session, factories, title="Прежнее")
        db_session.commit()
        monkeypatch.setattr(family_categories_module, "_duplicate_exists", lambda *a, **k: False)
        response = admin_client.patch(
            f"{BASE}/family-categories/{category.id}", json={"title": " РАБОТА "}
        )
        assert response.status_code == 409
        assert _detail(response)["code"] == "category_duplicate"
        db_session.expire_all()
        assert db_session.get(FamilyCategory, category.id).title == "Прежнее"

    def test_patch_blank_values_are_422(self, admin_client, db_session, factories):
        category = _operator_category(db_session, factories)
        db_session.commit()
        blank_title = admin_client.patch(f"{BASE}/family-categories/{category.id}", json={"title": " "})
        assert (blank_title.status_code, _detail(blank_title)["code"]) == (422, "category_blank_title")
        blank_definition = admin_client.patch(
            f"{BASE}/family-categories/{category.id}", json={"definition": ""}
        )
        assert (blank_definition.status_code, _detail(blank_definition)["code"]) == (
            422, "category_blank_definition",
        )

    def test_delete_empty_category_is_204_and_the_row_is_gone(self, admin_client, db_session, factories):
        category = _operator_category(db_session, factories)
        db_session.commit()
        response = admin_client.delete(f"{BASE}/family-categories/{category.id}")
        assert response.status_code == 204
        db_session.expire_all()
        assert db_session.get(FamilyCategory, category.id) is None

    def test_delete_category_with_families_is_409_category_in_use_with_the_count(
        self, admin_client, db_session, factories
    ):
        category = _operator_category(db_session, factories, title="Занятая")
        actor = _user_id(factories)
        for _ in range(2):
            _family(db_session, actor, category_id=category.id)
        db_session.commit()

        response = admin_client.delete(f"{BASE}/family-categories/{category.id}")

        assert response.status_code == 409
        detail = _detail(response)
        assert detail["code"] == "category_in_use"
        assert detail["family_count"] == 2
        assert detail["message"] == "Категорию «Занятая» носят 2 семей — удалить можно только пустую."
        db_session.expire_all()
        assert db_session.get(FamilyCategory, category.id) is not None

    def test_delete_missing_category_is_409_category_not_found(self, admin_client, db_session):
        db_session.commit()
        response = admin_client.delete(f"{BASE}/family-categories/999999999")
        assert response.status_code == 409
        assert _detail(response)["code"] == "category_not_found"


class TestFamilyRoutesWithCategory:
    def test_create_family_with_category_answers_the_category(self, admin_client, db_session):
        response = admin_client.post(
            f"{BASE}/families",
            json={"title": _unique("Семья"), "family_category_id": _work_id(db_session)},
        )
        assert response.status_code == 201
        assert (response.json()["family_category_id"], response.json()["family_category_title"]) == (
            _work_id(db_session), "Работа",
        )

    def test_create_family_with_missing_category_is_409_and_writes_nothing(
        self, admin_client, db_session
    ):
        db_session.commit()
        before = _count(db_session, WorkFamily)
        response = admin_client.post(
            f"{BASE}/families", json={"title": _unique("Семья"), "family_category_id": 999999999}
        )
        assert response.status_code == 409
        assert _detail(response)["code"] == "category_not_found"
        assert _count(db_session, WorkFamily) == before

    def test_patch_sets_then_activation_passes(self, admin_client, db_session):
        created = admin_client.post(
            f"{BASE}/families", json={"title": _unique("Семья"), "definition": "Определение"}
        ).json()
        assert created["family_category_id"] is None

        patched = admin_client.patch(
            f"{BASE}/families/{created['id']}",
            json={"family_category_id": _work_id(db_session)},
        )
        assert patched.status_code == 200
        assert patched.json()["family_category_title"] == "Работа"
        assert admin_client.post(f"{BASE}/families/{created['id']}/activate").status_code == 200

    def test_activation_without_category_is_422_with_the_spec_text(self, admin_client, db_session):
        created = admin_client.post(
            f"{BASE}/families", json={"title": _unique("Семья"), "definition": "Определение"}
        ).json()
        db_session.commit()
        response = admin_client.post(f"{BASE}/families/{created['id']}/activate")
        assert response.status_code == 422
        detail = _detail(response)
        assert (detail["code"], detail["message"], detail["family_id"]) == (
            "activate_without_category", _TEXT_NO_CATEGORY, created["id"],
        )
        db_session.expire_all()
        assert db_session.get(WorkFamily, created["id"]).status == "draft"

    def test_patch_null_category_on_an_active_family_is_422(self, admin_client, db_session):
        created = admin_client.post(
            f"{BASE}/families",
            json={
                "title": _unique("Семья"), "definition": "Определение",
                "family_category_id": _work_id(db_session),
            },
        ).json()
        assert admin_client.post(f"{BASE}/families/{created['id']}/activate").status_code == 200
        db_session.commit()

        response = admin_client.patch(
            f"{BASE}/families/{created['id']}", json={"family_category_id": None}
        )

        assert response.status_code == 422
        assert _detail(response)["code"] == "clear_category_active"
        assert _detail(response)["message"] == _TEXT_CLEAR_ACTIVE
        db_session.expire_all()
        assert db_session.get(WorkFamily, created["id"]).family_category_id == _work_id(db_session)

    def test_patch_without_the_field_does_not_touch_the_category(self, admin_client, db_session):
        created = admin_client.post(
            f"{BASE}/families",
            json={"title": _unique("Семья"), "family_category_id": _work_id(db_session)},
        ).json()
        patched = admin_client.patch(
            f"{BASE}/families/{created['id']}", json={"title": _unique("Новое имя")}
        )
        assert patched.json()["family_category_id"] == _work_id(db_session)

    def test_patch_null_category_on_a_draft_clears_it(self, admin_client, db_session):
        created = admin_client.post(
            f"{BASE}/families",
            json={"title": _unique("Семья"), "family_category_id": _work_id(db_session)},
        ).json()
        patched = admin_client.patch(
            f"{BASE}/families/{created['id']}", json={"family_category_id": None}
        )
        assert patched.status_code == 200
        assert patched.json()["family_category_id"] is None

    def test_list_filter_none_and_by_id(self, admin_client, db_session):
        with_category = admin_client.post(
            f"{BASE}/families",
            json={"title": _unique("С категорией"), "family_category_id": _work_id(db_session)},
        ).json()["id"]
        without = admin_client.post(
            f"{BASE}/families", json={"title": _unique("Без категории")}
        ).json()["id"]

        none_ids = {row["id"] for row in admin_client.get(f"{BASE}/families?family_category_id=none").json()["items"]}
        by_id = {
            row["id"]
            for row in admin_client.get(
                f"{BASE}/families?family_category_id={_work_id(db_session)}"
            ).json()["items"]
        }
        assert without in none_ids and with_category not in none_ids
        assert with_category in by_id and without not in by_id

    def test_list_filter_with_garbage_is_422(self, admin_client):
        assert admin_client.get(f"{BASE}/families?family_category_id=abc").status_code == 422


class TestCreateFamilyFromSuggestionRoute:
    def test_category_is_required(self, admin_client, db_session, factories):
        scene = _scene(db_session, factories)
        suggestion = _published(db_session, scene.context_ids[0], family_id=None)
        db_session.commit()
        response = admin_client.post(
            f"{BASE}/suggestions/{suggestion.id}/create-family",
            json={"title": "Новая", "definition": "Определение"},
        )
        assert response.status_code == 422
        db_session.expire_all()
        assert db_session.get(FamilySuggestion, suggestion.id).decision is None

    def test_with_a_category_the_family_is_active_with_it(self, admin_client, db_session, factories):
        scene = _scene(db_session, factories)
        suggestion = _published(db_session, scene.context_ids[0], family_id=None)
        db_session.commit()
        response = admin_client.post(
            f"{BASE}/suggestions/{suggestion.id}/create-family",
            json={
                "title": "Новая", "definition": "Определение",
                "family_category_id": _work_id(db_session),
            },
        )
        assert response.status_code == 200
        db_session.expire_all()
        family = db_session.get(WorkFamily, response.json()["family_id"])
        assert (family.status, family.family_category_id) == ("active", _work_id(db_session))

    def test_deleted_category_is_409_and_writes_nothing(self, admin_client, db_session, factories):
        scene = _scene(db_session, factories)
        suggestion = _published(db_session, scene.context_ids[0], family_id=None)
        db_session.commit()
        before = _count(db_session, WorkFamily)
        response = admin_client.post(
            f"{BASE}/suggestions/{suggestion.id}/create-family",
            json={"title": "Новая", "definition": "О", "family_category_id": 999999999},
        )
        assert response.status_code == 409
        assert _detail(response)["code"] == "category_not_found"
        assert _count(db_session, WorkFamily) == before


# ---------------------------------------------------------------------------
#  Гонки на настоящих commit
# ---------------------------------------------------------------------------

_TIMEOUT = 30.0


class _Worker:
    """Поток с результатом: исключение сохраняется, зависание ловит `join` с таймаутом."""

    def __init__(self, target):
        self.error: BaseException | None = None
        self.done = False
        self._thread = threading.Thread(target=self._run, args=(target,), daemon=True)

    def _run(self, target):
        try:
            target()
        except BaseException as exc:  # noqa: BLE001 — исход гонки читает тест
            self.error = exc
        finally:
            self.done = True

    def start(self):
        self._thread.start()

    def join(self):
        self._thread.join(_TIMEOUT)
        assert not self._thread.is_alive(), "поток завис — замок не отпущен или тупик"


def _settle(monitor, *entries) -> None:
    """Уборка гонки и при провале предусловия (без утверждений): поток, который
    закончил, откатывает свою транзакцию — это отпускает замки, которых может ждать
    соседний; поток, так и не закончивший к сроку, снимается `pg_terminate_backend`.
    Без этого неподтверждённая транзакция одного потока держит другой на замке, и
    очистка таблиц после теста зависает. Элемент — `(worker, session, pid)`."""
    pending = list(entries)
    deadline = time.monotonic() + _TIMEOUT
    while pending and time.monotonic() < deadline:
        for entry in list(pending):
            worker, session, _pid_ = entry
            worker._thread.join(0.2)
            if not worker._thread.is_alive():
                with contextlib.suppress(Exception):
                    session.rollback()
                with contextlib.suppress(Exception):
                    session.close()
                pending.remove(entry)
    for worker, session, pid in pending:
        monitor.execute(sa.text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        worker._thread.join(_TIMEOUT)
        with contextlib.suppress(Exception):
            session.close()


def _wait_lock_wait(monitor, pid: int) -> bool:
    """Ждёт, пока бэкенд `pid` встанет в ожидание замка (наблюдение, не пауза)."""
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        wait = monitor.execute(
            sa.text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"), {"pid": pid}
        ).scalar()
        if wait == "Lock":
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def monitor(db_engine):
    connection = db_engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        yield connection
    finally:
        connection.close()


def _pid(session) -> int:
    return session.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()


def _committed_actor_and_category(committing_db, committing_factories, title):
    actor = committing_factories.UserFactory.create()
    category = _category(committing_db, created_by=actor.id, title=title)
    committing_db.commit()
    return actor.id, category.id


class TestCategoryRaces:
    def test_two_concurrent_creates_of_one_title_give_category_duplicate_to_the_second(
        self, committing_db, committing_factories, committing_session_factory, monitor
    ):
        actor = committing_factories.UserFactory.create().id
        committing_db.commit()
        title = _unique("Гонка имени")
        first = committing_session_factory()
        second = committing_session_factory()
        second_pid = _pid(second)
        checked = threading.Event()

        def _after_read(conn, cursor, statement, *args):
            if "lower(btrim" in statement and "FROM family_categories" in statement:
                checked.set()

        event.listen(second.connection(), "after_cursor_execute", _after_read)
        try:
            create_category(first, title=title, definition="Первая", actor_id=actor)
            worker = _Worker(
                lambda: create_category(second, title=title, definition="Вторая", actor_id=actor)
            )
            worker.start()
            assert checked.wait(_TIMEOUT), "вторая транзакция не дошла до проверки дубля"
            assert _wait_lock_wait(monitor, second_pid), (
                "вторая транзакция не встала на ключ имени: проверка дубля её не удержала бы"
            )
            first.commit()
            worker.join()
        finally:
            first.close()
            second.rollback()
            second.close()

        assert isinstance(worker.error, WorkFamilyError), repr(worker.error)
        assert worker.error.code == REFUSE_CATEGORY_DUPLICATE
        with committing_session_factory() as probe:
            assert probe.query(FamilyCategory).filter_by(title=title).count() == 1

    def test_create_family_first_then_delete_gives_category_in_use(
        self, committing_db, committing_factories, committing_session_factory, monitor
    ):
        actor, category_id = _committed_actor_and_category(
            committing_db, committing_factories, _unique("Для семьи")
        )
        creator = committing_session_factory()
        deleter = committing_session_factory()
        deleter_pid = _pid(deleter)
        creator_pid = _pid(creator)
        at_insert = threading.Event()
        release = threading.Event()

        def _pause_before_family_insert(conn, cursor, statement, *args):
            if statement.lstrip().startswith("INSERT INTO work_families"):
                at_insert.set()
                release.wait(_TIMEOUT)

        event.listen(creator.connection(), "before_cursor_execute", _pause_before_family_insert)

        def _create():
            create_family(
                creator, title=_unique("Семья гонки"), unit_name=None, definition="О",
                actor_id=actor, family_category_id=category_id,
            )
            creator.commit()

        creating = _Worker(_create)
        deleting = _Worker(lambda: delete_category(deleter, category_id=category_id, actor_id=actor))
        try:
            creating.start()
            assert at_insert.wait(_TIMEOUT), "create_family не дошёл до записи семьи"
            deleting.start()
            assert _wait_lock_wait(monitor, deleter_pid), (
                "удаление не ждёт замок категории: create_family не взял её FOR SHARE "
                "до записи семьи"
            )
        finally:
            # Уборка и при провале предусловия: без защиты удаляющий держит
            # неподтверждённый DELETE, а создающий ждёт его на ключе — откат удаляющего
            # отпускает обоих, и очистка таблиц после теста не зависает.
            release.set()
            _settle(monitor, (deleting, deleter, deleter_pid), (creating, creator, creator_pid))
        creating.join()
        deleting.join()

        assert creating.error is None, repr(creating.error)
        assert isinstance(deleting.error, WorkFamilyError), repr(deleting.error)
        assert deleting.error.code == REFUSE_CATEGORY_IN_USE
        assert deleting.error.family_count == 1
        with committing_session_factory() as probe:
            assert probe.get(FamilyCategory, category_id) is not None
            assert probe.query(WorkFamily).filter_by(family_category_id=category_id).count() == 1

    def test_delete_first_then_create_family_gives_category_not_found(
        self, committing_db, committing_factories, committing_session_factory, monitor
    ):
        actor, category_id = _committed_actor_and_category(
            committing_db, committing_factories, _unique("Для удаления")
        )
        deleter = committing_session_factory()
        creator = committing_session_factory()
        creator_pid = _pid(creator)
        deleter_pid = _pid(deleter)
        at_delete = threading.Event()
        release = threading.Event()
        title = _unique("Семья после удаления")

        def _pause_before_delete(conn, cursor, statement, *args):
            if statement.lstrip().startswith("DELETE FROM family_categories"):
                at_delete.set()
                release.wait(_TIMEOUT)

        event.listen(deleter.connection(), "before_cursor_execute", _pause_before_delete)

        def _delete():
            delete_category(deleter, category_id=category_id, actor_id=actor)
            deleter.commit()

        deleting = _Worker(_delete)
        creating = _Worker(
            lambda: create_family(
                creator, title=title, unit_name=None, definition="О", actor_id=actor,
                family_category_id=category_id,
            )
        )
        try:
            deleting.start()
            assert at_delete.wait(_TIMEOUT), "удаление не дошло до DELETE"
            creating.start()
            assert _wait_lock_wait(monitor, creator_pid), (
                "create_family не ждёт замок категории: он не берёт её FOR SHARE и пошёл "
                "мимо удаляющего"
            )
        finally:
            release.set()
            _settle(monitor, (deleting, deleter, deleter_pid), (creating, creator, creator_pid))
        deleting.join()
        creating.join()

        assert deleting.error is None, repr(deleting.error)
        assert isinstance(creating.error, WorkFamilyError), repr(creating.error)
        assert creating.error.code == REFUSE_CATEGORY_NOT_FOUND
        with committing_session_factory() as probe:
            assert probe.get(FamilyCategory, category_id) is None
            assert probe.query(WorkFamily).filter_by(title=title).count() == 0
