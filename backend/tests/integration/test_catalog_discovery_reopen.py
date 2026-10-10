"""«Вернуть в разбор» (спека 3б §2.8, решения 12, 13; DoD 11).

Контекст «не работа» человека возвращается в разбор; контекст, чью строку
каталога разметили в Review, — нет. Один предикат `is_reopenable` решает за
`reopen_context` и за карточку контекста; параллельные возврат и глобальная
пометка той же строки не замыкаются в deadlock и не оставляют возвращённый
контекст на размеченной строке."""
# ruff: noqa: F811 — `world` — фикстура, импортированная из соседнего набора
from __future__ import annotations

import datetime as dt
import threading

import pytest
import sqlalchemy as sa
from sqlalchemy import event

from crud.semantic import context_card
from models import CatalogContext, CatalogPosition, SemanticEvent, SemanticJob
from services.review import set_position_kind_global
from services.work_families import WorkFamilyError, assign_family
from services.work_variants import is_reopenable, mark_context_not_work, reopen_context
from tests.integration.test_catalog_discovery_scope import (
    _ctx_family_unit,
    _make,
    _make_system,
    _set,
    world,  # noqa: F401 — фикстура
)
from tests.integration.test_semantic_queue_api import _active_family, _proposal, _unit_id

pytestmark = pytest.mark.integration

#: Предел любого ожидания, секунды.
_T = 20.0


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _refusal(code, call):
    with pytest.raises(WorkFamilyError) as exc:
        call()
    assert exc.value.code == code, f"ожидался {code}, получен {exc.value.code}: {exc.value}"


def _state(db, context_id) -> str:
    db.expire_all()
    return db.get(CatalogContext, context_id).semantic_state


def _events(db, context_id, event_type) -> list[SemanticEvent]:
    return list(
        db.execute(
            sa.select(SemanticEvent)
            .where(SemanticEvent.context_id == context_id, SemanticEvent.event_type == event_type)
            .order_by(SemanticEvent.id)
        ).scalars()
    )


def _pending_suggestion_jobs(db, context_id) -> int:
    return db.execute(
        sa.select(sa.func.count(SemanticJob.id)).where(
            SemanticJob.context_id == context_id,
            SemanticJob.kind == "family_suggestion",
            SemanticJob.status == "pending",
        )
    ).scalar_one()


def _not_work_by_human(w, title, *, kind_by="rule"):
    """Контекст единицы с семьёй, отмеченный «не работа» человеком;
    `kind_by="manual"` — его вид тоже поставил человек."""
    context_id = _ctx_family_unit(w, title)
    if kind_by == "manual":
        _make_system(w.db, context_id, w.admin.id)
    mark_context_not_work(w.db, context_id=context_id, actor_id=w.admin.id)
    w.db.expire_all()
    return context_id


# ---------------------------------------------------------------------------
#  Возврат
# ---------------------------------------------------------------------------

class TestReopen:
    def test_kind_by_rule_returns_as_suggested(self, world):
        context_id = _not_work_by_human(world, "Правило")
        assert _state(world.db, context_id) == "NOT_APPLICABLE"

        reopen_context(world.db, context_id=context_id, actor_id=world.admin.id)

        assert _state(world.db, context_id) == "SUGGESTED"

    def test_kind_by_a_human_returns_as_confirmed(self, world):
        context_id = _not_work_by_human(world, "Человек", kind_by="manual")

        reopen_context(world.db, context_id=context_id, actor_id=world.admin.id)

        assert _state(world.db, context_id) == "CONFIRMED"

    def test_family_variant_and_values_stay_empty(self, world):
        context_id = _not_work_by_human(world, "Пустой")

        context = reopen_context(world.db, context_id=context_id, actor_id=world.admin.id)

        assert (context.work_family_id, context.pending_family_id, context.work_variant_id) == (
            None, None, None,
        )
        assert context.family_source is None
        assert context.archived_at is None

    @pytest.mark.parametrize(("kind_by", "to_state"), [("rule", "SUGGESTED"), ("manual", "CONFIRMED")])
    def test_event_names_the_author_and_both_states(self, world, kind_by, to_state):
        context_id = _not_work_by_human(world, "Событие", kind_by=kind_by)

        reopen_context(world.db, context_id=context_id, actor_id=world.admin.id)

        (reopened,) = _events(world.db, context_id, "context_reopened")
        assert reopened.actor_id == world.admin.id
        assert reopened.payload == {"from_state": "NOT_APPLICABLE", "to_state": to_state}

    def test_suggestion_job_is_queued_by_the_same_transaction(self, world):
        context_id = _not_work_by_human(world, "Задание")
        assert _pending_suggestion_jobs(world.db, context_id) == 0

        reopen_context(world.db, context_id=context_id, actor_id=world.admin.id)

        assert _pending_suggestion_jobs(world.db, context_id) == 1

    def test_unit_without_a_family_gets_no_job_but_returns(self, world):
        # Единица без активных семей: контекст применим не к чему — задания нет.
        context_id, _ = _make(world.db, world.factories, world.proposal, world.bare_unit, "Голая")
        mark_context_not_work(world.db, context_id=context_id, actor_id=world.admin.id)

        reopen_context(world.db, context_id=context_id, actor_id=world.admin.id)

        assert _state(world.db, context_id) == "SUGGESTED"
        assert _pending_suggestion_jobs(world.db, context_id) == 0


# ---------------------------------------------------------------------------
#  Отказы
# ---------------------------------------------------------------------------

class TestRefusals:
    def test_suggested_context_is_not_reopenable_by_state(self, world):
        context_id = _ctx_family_unit(world, "Предложенный")

        _refusal(
            "context_not_reopenable_state",
            lambda: reopen_context(world.db, context_id=context_id, actor_id=world.admin.id),
        )
        assert _state(world.db, context_id) == "SUGGESTED"
        assert _events(world.db, context_id, "context_reopened") == []

    def test_confirmed_context_is_not_reopenable_by_state(self, world):
        context_id = _ctx_family_unit(world, "Подтверждённый")
        _make_system(world.db, context_id, world.admin.id)
        _set(world.db, context_id, semantic_state="CONFIRMED")

        _refusal(
            "context_not_reopenable_state",
            lambda: reopen_context(world.db, context_id=context_id, actor_id=world.admin.id),
        )

    def test_archived_context_is_refused(self, world):
        context_id = _not_work_by_human(world, "Архивный")
        _set(world.db, context_id, archived_at=dt.datetime.now(dt.UTC))

        _refusal(
            "context_archived",
            lambda: reopen_context(world.db, context_id=context_id, actor_id=world.admin.id),
        )
        assert _state(world.db, context_id) == "NOT_APPLICABLE"

    def test_missing_context_is_context_not_found(self, world):
        _refusal(
            "context_not_found",
            lambda: reopen_context(world.db, context_id=987654, actor_id=world.admin.id),
        )

    @pytest.mark.parametrize("kind", ["HEADER", "LOT_HEADER", "TRASH"])
    def test_context_born_on_a_review_row_is_refused_by_position(self, world, kind):
        context_id, _ = _make(
            world.db, world.factories, world.proposal, world.family_unit, f"Строка {kind}",
            cp_kind=kind,
        )
        assert _state(world.db, context_id) == "NOT_APPLICABLE"

        _refusal(
            "context_not_applicable_by_position",
            lambda: reopen_context(world.db, context_id=context_id, actor_id=world.admin.id),
        )
        assert _state(world.db, context_id) == "NOT_APPLICABLE"
        assert _events(world.db, context_id, "context_reopened") == []

    @pytest.mark.parametrize("kind", ["HEADER", "TRASH"])
    def test_human_not_work_whose_row_was_marked_globally_is_refused_by_position(
        self, world, kind
    ):
        context_id = _not_work_by_human(world, f"Позже {kind}")
        position_id = world.db.execute(
            sa.text(
                "SELECT b.catalog_position_id FROM catalog_contexts c "
                "JOIN context_buckets b ON b.id = c.bucket_id WHERE c.id = :c"
            ),
            {"c": context_id},
        ).scalar_one()
        # До пометки возврат возможен: вход, где правило срабатывает.
        assert is_reopenable(
            semantic_state="NOT_APPLICABLE", catalog_kind="POSITION", archived=False
        )
        set_position_kind_global(
            world.db, position_id=position_id, kind=kind, actor_id=world.admin.id
        )
        world.db.expire_all()

        _refusal(
            "context_not_applicable_by_position",
            lambda: reopen_context(world.db, context_id=context_id, actor_id=world.admin.id),
        )

    def test_refusal_leaves_no_pending_job_and_no_event(self, world):
        context_id, _ = _make(
            world.db, world.factories, world.proposal, world.family_unit, "Заголовок",
            cp_kind="HEADER",
        )

        _refusal(
            "context_not_applicable_by_position",
            lambda: reopen_context(world.db, context_id=context_id, actor_id=world.admin.id),
        )

        assert _pending_suggestion_jobs(world.db, context_id) == 0
        assert _events(world.db, context_id, "context_reopened") == []


# ---------------------------------------------------------------------------
#  Один предикат: таблица входов против обоих потребителей
# ---------------------------------------------------------------------------

_STATES = ("SUGGESTED", "CONFIRMED", "NOT_APPLICABLE")
_KINDS = ("TO_REVIEW", "POSITION", "HEADER", "LOT_HEADER", "TRASH")


def _expected(state, kind, archived):
    """Независимый эталон: (возвращается ли, код отказа)."""
    if archived:
        return False, "context_archived"
    if state != "NOT_APPLICABLE":
        return False, "context_not_reopenable_state"
    if kind in ("HEADER", "LOT_HEADER", "TRASH"):
        return False, "context_not_applicable_by_position"
    return True, None


class TestOnePredicate:
    @pytest.mark.parametrize("archived", [False, True])
    @pytest.mark.parametrize("kind", _KINDS)
    @pytest.mark.parametrize("state", _STATES)
    def test_predicate_reopen_and_card_agree_on_every_input(self, world, state, kind, archived):
        context_id, _ = _make(
            world.db, world.factories, world.proposal, world.family_unit,
            f"{state}-{kind}-{archived}", cp_kind=kind,
        )
        _set(world.db, context_id, semantic_state=state)
        if archived:
            _set(world.db, context_id, archived_at=dt.datetime.now(dt.UTC))
        allowed, code = _expected(state, kind, archived)

        assert is_reopenable(semantic_state=state, catalog_kind=kind, archived=archived) is allowed
        card = context_card(world.db, context_id=context_id)
        assert (card["reopenable"], card["catalog_kind"]) == (allowed, kind)
        if allowed:
            reopen_context(world.db, context_id=context_id, actor_id=world.admin.id)
            assert _state(world.db, context_id) in ("SUGGESTED", "CONFIRMED")
        else:
            _refusal(
                code,
                lambda: reopen_context(world.db, context_id=context_id, actor_id=world.admin.id),
            )
            assert _state(world.db, context_id) == state


class TestCard:
    def test_card_of_a_human_not_work_is_reopenable_and_shows_the_row_kind(self, world):
        context_id = _not_work_by_human(world, "Карточка")

        card = context_card(world.db, context_id=context_id)

        assert card["reopenable"] is True and card["catalog_kind"] == "POSITION"

    def test_card_of_a_working_context_is_not_reopenable(self, world):
        context_id = _ctx_family_unit(world, "Рабочий")

        card = context_card(world.db, context_id=context_id)

        assert card["reopenable"] is False and card["catalog_kind"] == "POSITION"

    def test_card_after_the_return_is_not_reopenable(self, world):
        context_id = _not_work_by_human(world, "После возврата")
        reopen_context(world.db, context_id=context_id, actor_id=world.admin.id)

        assert context_card(world.db, context_id=context_id)["reopenable"] is False

    def test_card_of_a_context_with_a_family_still_follows_the_predicate(self, world):
        # Контекст «не работа», которому позже назначили семью, по-прежнему возвращаем.
        context_id = _not_work_by_human(world, "С семьёй")
        assign_family(
            world.db, context_id=context_id, family_id=world.family.id, actor_id=world.admin.id
        )
        world.db.expire_all()

        card = context_card(world.db, context_id=context_id)

        assert card["semantic_state"] == "NOT_APPLICABLE" and card["reopenable"] is True


# ---------------------------------------------------------------------------
#  Гонка: возврат ↔ глобальная пометка той же строки
# ---------------------------------------------------------------------------

def _terminate(factory, pid):
    if pid is None:
        return
    with factory() as db:
        db.execute(sa.text("SELECT pg_terminate_backend(:p)"), {"p": pid})
        db.commit()


def _waits_for_a_lock(factory, pid, thread, *, timeout=8.0) -> bool:
    """Backend `pid` ждёт чужую блокировку. Опрос `pg_stat_activity` без `sleep`
    как синхронизации: выход — по условию либо по окончанию потока."""
    pause = threading.Event()
    waited = 0.0
    while waited < timeout and thread.is_alive():
        with factory() as db:
            state = db.execute(
                sa.text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :p"), {"p": pid}
            ).scalar_one_or_none()
        if state == "Lock":
            return True
        pause.wait(0.05)
        waited += 0.05
    return False


class TestRaceWithGlobalMark:
    def test_reopen_and_global_mark_of_the_same_row_leave_no_reopened_context_on_a_marked_row(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """Возврат встаёт ПОСЛЕ чтения строки каталога; глобальная пометка той же
        строки идёт, пока он стоит. Строка под `FOR SHARE` держит пометку до конца
        возврата; без замка пометка успевала бы раньше, а возврат дочитывал бы вид
        `POSITION` и ставил контекст в `SUGGESTED` на строке `HEADER`."""
        cdb, cf, factory = committing_db, committing_factories, committing_session_factory
        admin = cf.UserFactory.create()
        unit = _unit_id(cdb, "M2")
        _active_family(cdb, title="Семья пола", unit_name="M2", actor_id=admin.id)
        context_id, _ = _make(cdb, cf, _proposal(cf), unit, "Гонка возврата")
        mark_context_not_work(cdb, context_id=context_id, actor_id=admin.id)
        position_id = cdb.execute(
            sa.text(
                "SELECT b.catalog_position_id FROM catalog_contexts c "
                "JOIN context_buckets b ON b.id = c.bucket_id WHERE c.id = :c"
            ),
            {"c": context_id},
        ).scalar_one()
        cdb.commit()

        read_row = threading.Event()
        go = threading.Event()
        reopener: dict[str, object] = {"pid": None, "error": None}
        marker: dict[str, object] = {"pid": None, "error": None}

        def reopen():
            try:
                with factory() as db:
                    reopener["pid"] = db.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                    connection = db.connection()

                    def _hook(conn, cursor, statement, parameters, context, executemany):
                        # После чтения строки каталога (с замком или без него).
                        if "FROM catalog_positions" in statement and not read_row.is_set():
                            read_row.set()
                            if not go.wait(_T):
                                raise TimeoutError("go не пришёл")

                    event.listen(connection, "after_cursor_execute", _hook)
                    try:
                        reopen_context(db, context_id=context_id, actor_id=admin.id)
                        db.commit()
                    finally:
                        event.remove(connection, "after_cursor_execute", _hook)
            except Exception as exc:  # noqa: BLE001 — исход фиксируется
                reopener["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                read_row.set()

        def mark():
            try:
                with factory() as db:
                    marker["pid"] = db.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                    db.execute(sa.text("SET LOCAL lock_timeout = '15000ms'"))
                    set_position_kind_global(
                        db, position_id=position_id, kind="HEADER", actor_id=admin.id
                    )
                    db.commit()
            except Exception as exc:  # noqa: BLE001
                marker["error"] = f"{type(exc).__name__}: {exc}"

        t_reopen = threading.Thread(target=reopen, name="reopen", daemon=True)
        t_mark = threading.Thread(target=mark, name="mark", daemon=True)
        t_reopen.start()
        blocked = False
        try:
            assert read_row.wait(_T), "возврат не дошёл до чтения строки каталога"
            t_mark.start()
            pause = threading.Event()
            for _ in range(int(_T / 0.02)):
                if marker["pid"] is not None or not t_mark.is_alive():
                    break
                pause.wait(0.02)
            assert marker["pid"] is not None, f"поток пометки не стартовал: {marker['error']}"
            blocked = _waits_for_a_lock(factory, marker["pid"], t_mark)
        finally:
            go.set()
            for thread, state in ((t_reopen, reopener), (t_mark, marker)):
                thread.join(timeout=_T)
                if thread.is_alive():
                    _terminate(factory, state["pid"])
                    thread.join(timeout=5)

        assert not t_reopen.is_alive() and not t_mark.is_alive(), "поток завис"
        assert reopener["error"] is None, reopener["error"]
        assert marker["error"] is None, marker["error"]
        with factory() as db:
            kind = db.get(CatalogPosition, position_id).kind
            state = db.get(CatalogContext, context_id).semantic_state
            reopened = _events(db, context_id, "context_reopened")
            marked = [
                e for e in _events(db, context_id, "context_not_work")
                if e.payload["reason"] == "position_kind"
            ]
        # Инвариант равносильности §1.5: строка размечена — контекст «не работа».
        assert kind == "HEADER"
        assert state == "NOT_APPLICABLE", "возвращённый контекст остался на размеченной строке"
        # Допустимый исход один: возврат прошёл раньше и пометка вернула контекст в «не работа».
        assert len(reopened) == 1 and len(marked) == 1
        assert blocked, "пометка должна ждать замок строки, а не идти мимо возврата"


class TestRaceOfTwoReopens:
    def test_second_reopen_of_the_same_context_waits_and_is_refused(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """Возврат берёт контекст `FOR UPDATE` (`acquire_family_locks`) и только затем
        читает его состояние. Второй возврат того же контекста (двойное нажатие, две
        вкладки) ждёт первый и после его `commit` видит уже не `NOT_APPLICABLE` —
        отказ `context_not_reopenable_state`. Без замка контекста оба прочли бы
        `NOT_APPLICABLE`, и контекст вернули бы дважды: два события `context_reopened`."""
        cdb, cf, factory = committing_db, committing_factories, committing_session_factory
        admin = cf.UserFactory.create()
        unit = _unit_id(cdb, "M2")
        _active_family(cdb, title="Семья пола", unit_name="M2", actor_id=admin.id)
        context_id, _ = _make(cdb, cf, _proposal(cf), unit, "Двойной возврат")
        mark_context_not_work(cdb, context_id=context_id, actor_id=admin.id)
        cdb.commit()

        read_state = threading.Event()
        go = threading.Event()
        first: dict[str, object] = {"pid": None, "error": None}
        second: dict[str, object] = {"pid": None, "error": None, "code": None}

        def reopen_first():
            try:
                with factory() as db:
                    first["pid"] = db.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                    connection = db.connection()

                    def _hook(conn, cursor, statement, parameters, context, executemany):
                        # Состояние контекста прочитано (полная строка, без блокировки).
                        if (
                            "catalog_contexts.semantic_kind_source" in statement
                            and " FOR " not in statement.upper()
                            and not read_state.is_set()
                        ):
                            read_state.set()
                            if not go.wait(_T):
                                raise TimeoutError("go не пришёл")

                    event.listen(connection, "after_cursor_execute", _hook)
                    try:
                        reopen_context(db, context_id=context_id, actor_id=admin.id)
                        db.commit()
                    finally:
                        event.remove(connection, "after_cursor_execute", _hook)
            except Exception as exc:  # noqa: BLE001 — исход фиксируется
                first["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                read_state.set()

        def reopen_second():
            try:
                with factory() as db:
                    second["pid"] = db.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                    try:
                        reopen_context(db, context_id=context_id, actor_id=admin.id)
                        db.commit()
                    except WorkFamilyError as exc:
                        db.rollback()
                        second["code"] = exc.code
            except Exception as exc:  # noqa: BLE001
                second["error"] = f"{type(exc).__name__}: {exc}"

        t_first = threading.Thread(target=reopen_first, name="reopen-1", daemon=True)
        t_second = threading.Thread(target=reopen_second, name="reopen-2", daemon=True)
        t_first.start()
        blocked = False
        try:
            assert read_state.wait(_T), "первый возврат не дошёл до чтения состояния"
            t_second.start()
            pause = threading.Event()
            for _ in range(int(_T / 0.02)):
                if second["pid"] is not None or not t_second.is_alive():
                    break
                pause.wait(0.02)
            assert second["pid"] is not None, f"второй возврат не стартовал: {second['error']}"
            blocked = _waits_for_a_lock(factory, second["pid"], t_second)
        finally:
            go.set()
            for thread, state in ((t_first, first), (t_second, second)):
                thread.join(timeout=_T)
                if thread.is_alive():
                    _terminate(factory, state["pid"])
                    thread.join(timeout=5)

        assert not t_first.is_alive() and not t_second.is_alive(), "поток завис"
        assert first["error"] is None, first["error"]
        assert second["error"] is None, second["error"]
        assert blocked, "второй возврат должен ждать замок контекста, а не идти мимо первого"
        assert second["code"] == "context_not_reopenable_state"
        with factory() as db:
            assert db.get(CatalogContext, context_id).semantic_state == "SUGGESTED"
            assert len(_events(db, context_id, "context_reopened")) == 1
