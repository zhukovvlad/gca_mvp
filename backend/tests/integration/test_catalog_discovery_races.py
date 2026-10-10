"""Порядок блокировок и гонки обработки ответа открытия (спека 3б §2.3, решения
20 и 21; DoD 7).

Сторож потока данных пишет порядок захвата блокировок `apply_discovery` и
сверяет его с литералом (`docs/insights/data-flow-assertions-for-order.md`).
Гонки идут на настоящих сессиях с `commit`: обработка встаёт на барьер ПОСЛЕ
шага 5 (повторный рендер тела уже прочитан), вторая сессия делает своё с
`SET LOCAL lock_timeout`. Строки, взятые обработкой на шагах 1 и 3, вторая
сторона ждёт до `commit` обработки; новые строки и правки без блокировки
строки проходят сразу, и черновики остаются снимком.

Каждое ожидание ограничено таймаутом, потоки — `join(timeout=...)` и снятием
бэкенда; `sleep` синхронизацией не служит."""
# ruff: noqa: F811 — `world` — фикстура, импортированная из соседнего набора
from __future__ import annotations

import re
import threading
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

import services.semantic_worker as worker
from models import (
    CatalogContext,
    FamilyCategory,
    FamilyDraft,
    FamilyDraftMember,
    SemanticJob,
    WorkFamily,
)
from services.context_operations import archive_context, move_members, split_context
from services.discovery_result import apply_discovery
from services.family_categories import create_category, delete_category, update_category
from services.family_discovery import (
    discovery_scope,
    is_in_scope,
    launch_discovery,
    preview_discovery,
)
from services.variant_answer import parse_discovery_answer
from services.work_families import (
    _lock_families,
    activate_family,
    archive_family,
    assign_family,
    create_family,
    update_family,
)
from tests.factories import seed_category_id
from tests.integration.test_catalog_discovery_scope import (
    _ctx_bare_unit,
    _make,
    _make_system,
    _uncategorize,
    world,  # noqa: F401 — фикстура
)
from tests.integration.test_catalog_discovery_worker import (
    NOW,
    S,
    _answer,
    _claim,
    _group,
    _launch,
)
from tests.integration.test_semantic_queue_api import (
    _active_family,
    _proposal,
    _published,
    _unit_id,
)

pytestmark = pytest.mark.integration

#: Предел любого ожидания, секунды.
_T = 20.0
#: Таймаут блокировки второй стороны: «ждёт» — получает отказ за это время.
_WAIT_MS = 1200
#: Таймаут блокировки там, где вторая сторона обязана пройти сразу.
_PASS_MS = 6000


# ---------------------------------------------------------------------------
#  Сторож порядка блокировок
# ---------------------------------------------------------------------------

_LOCK_RE = re.compile(r"\bFOR (KEY SHARE|SHARE|UPDATE)\b")
_FROM_RE = re.compile(r"\bFROM (\w+)")


def _lock_order(statements: list[str]) -> list[tuple[str, str]]:
    order = []
    for statement in statements:
        lock = _LOCK_RE.search(statement)
        table = _FROM_RE.search(statement)
        if lock and table:
            order.append((table.group(1), lock.group(1)))
    return order


class TestLockOrderSentinel:
    def _statements(self, w):
        _active_family(w.db, title="Семья единицы", unit_name="M3", actor_id=w.admin.id)
        contexts = [_ctx_bare_unit(w, t) for t in ("Имя А", "Имя Б")]
        for context_id in contexts:
            _make_system(w.db, context_id, w.admin.id)
        _launch(w.db, w.bare_unit, admin_id=w.admin.id)
        claim = _claim(w.db)
        answer = parse_discovery_answer(
            _answer([_group([1, 2], category_id=seed_category_id(w.db))]), claim.sent
        )
        statements: list[str] = []

        def _listener(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        connection = w.db.connection()
        event.listen(connection, "before_cursor_execute", _listener)
        try:
            apply_discovery(
                w.db, job_id=claim.job_id, claim_token=claim.claim_token, answer=answer,
                settings=S,
            )
        finally:
            event.remove(connection, "before_cursor_execute", _listener)
        return statements

    def test_locks_are_taken_in_the_order_of_the_specification(self, world):
        order = _lock_order(self._statements(world))

        assert order == [
            ("family_categories", "SHARE"),
            ("family_drafts", "UPDATE"),
            ("family_category_proposals", "UPDATE"),
            ("work_families", "KEY SHARE"),
            ("catalog_contexts", "KEY SHARE"),
            ("semantic_jobs", "UPDATE"),
        ]

    def test_no_write_precedes_the_job_lock_and_no_category_lock_is_upgraded(self, world):
        statements = self._statements(world)

        job_lock = next(
            i for i, s in enumerate(statements)
            if "semantic_jobs" in s and _LOCK_RE.search(s) and s.lstrip().upper().startswith("SELECT")
        )
        writes = [
            i for i, s in enumerate(statements)
            if re.match(r"\s*(INSERT|UPDATE|DELETE)\b", s)
        ]
        assert writes, "вход теста: обработка что-то пишет"
        assert min(writes) > job_lock
        assert not any(
            "family_categories" in s and "FOR UPDATE" in s for s in statements
        ), "повышение режима замка категории запрещено"


# ---------------------------------------------------------------------------
#  Сцена на настоящих сессиях
# ---------------------------------------------------------------------------

class Scene:
    """Единица M3: активные семьи F1 (похожая), F2 (группа), F3 (без категории);
    три контекста-системы C1..C3 в охвате; задание захвачено, ответ разобран."""


def _is_lock_not_available(exc: OperationalError) -> bool:
    return getattr(exc.orig, "sqlstate", None) == "55P03"


@pytest.fixture
def scene(committing_db, committing_factories, committing_session_factory):
    cdb, cf = committing_db, committing_factories
    s = Scene()
    s.factory = committing_session_factory
    s.cdb = cdb
    s.cf = cf
    s.admin = cf.UserFactory.create()
    s.unit = _unit_id(cdb, "M3")
    s.f1 = _active_family(cdb, title="Семья Один", unit_name="M3", actor_id=s.admin.id)
    s.f2 = _active_family(cdb, title="Семья Два", unit_name="M3", actor_id=s.admin.id)
    s.f3 = _active_family(cdb, title="Семья Три", unit_name="M3", actor_id=s.admin.id)
    _uncategorize(cdb, s.f3.id)
    s.work = seed_category_id(cdb)
    proposal = _proposal(cf)
    s.proposal = proposal
    s.c1, _ = _make(cdb, cf, proposal, s.unit, "Имя А")
    s.c2, cp2 = _make(cdb, cf, proposal, s.unit, "Имя Б")
    s.c3, _ = _make(cdb, cf, proposal, s.unit, "Имя В")
    # Второй член в контексте C2 и разделение: у C2 появляется соседний контекст T
    # той же корзины, куда штатно переносят членства перед архивированием.
    second, _ = _make(cdb, cf, proposal, s.unit, "Имя Б", cp=cp2)
    assert second == s.c2
    member_ids = cdb.execute(
        sa.text("SELECT position_item_id FROM context_members WHERE context_id = :c ORDER BY 1"),
        {"c": s.c2},
    ).scalars().all()
    assert len(member_ids) == 2
    s.p_keep, s.p_move = member_ids
    s.split = split_context(
        cdb, context_id=s.c2, position_item_ids=[s.p_move], rule=None, actor_id=s.admin.id
    )
    for context_id in (s.c1, s.c2, s.c3):
        _make_system(cdb, context_id, s.admin.id)
    # Единица с семьями: маршрутизация поставила задания предложений; перезапрос
    # единицы закончен — иначе запуск открытия отказал бы `discovery_unit_busy`.
    cdb.execute(
        sa.text(
            "UPDATE semantic_jobs SET status = 'cancelled', cancel_reason = 'not_applicable' "
            "WHERE kind = 'family_suggestion' AND status IN ('pending', 'running')"
        )
    )
    cdb.commit()
    s.target_context = s.split.new_context_id
    # Задание, захват и разобранный ответ.
    preview = preview_discovery(cdb, unit_id=s.unit, settings=S)
    job = launch_discovery(
        cdb, unit_id=s.unit, preview_hash=preview.preview_hash, actor_id=s.admin.id, settings=S
    )
    cdb.commit()
    s.job_id = job.id
    claim = worker.claim_next(cdb, settings=S, now=NOW)
    assert claim is not None and claim.job_id == job.id
    s.claim = claim
    s.answer = parse_discovery_answer(
        _answer(
            [
                _group([1], category_id=s.work, similar=s.f1.id),
                _group([2], family_id=s.f2.id),
            ],
            not_work=[3],
            family_categories=[(s.f3.id, s.work)],
        ),
        claim.sent,
    )
    return s


class _Apply:
    """`apply_discovery` в своей сессии: встаёт на барьер ПОСЛЕ шага 5 (первый
    запрос рендера, читающий справочник), пока не придёт `release`."""

    def __init__(self, scene, *, barrier=True):
        self.scene = scene
        self.barrier = barrier
        self.paused = threading.Event()
        self.release = threading.Event()
        self.started = threading.Event()
        self.outcome = None
        self.error: str | None = None
        self.pid: int | None = None
        self._fired = False
        self.thread = threading.Thread(target=self._run, name="apply", daemon=True)

    def _hook(self, conn, cursor, statement, parameters, context, executemany):
        # «После шага 5»: запрос рендера, читающий имена и определения
        # категорий, без блокировки (запрос шага 1 их не читает).
        if self._fired or not self.barrier:
            return
        if "family_categories.title" in statement and " FOR " not in statement.upper():
            self._fired = True
            self.paused.set()
            if not self.release.wait(_T):
                raise TimeoutError("release не пришёл вовремя")

    def _run(self):
        try:
            with self.scene.factory() as db:
                self.pid = db.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                self.started.set()
                connection = db.connection()
                event.listen(connection, "after_cursor_execute", self._hook)
                try:
                    self.outcome = apply_discovery(
                        db, job_id=self.scene.job_id, claim_token=self.scene.claim.claim_token,
                        answer=self.scene.answer, settings=S,
                    )
                    db.commit()
                finally:
                    event.remove(connection, "after_cursor_execute", self._hook)
        except Exception as exc:  # noqa: BLE001 — исход фиксируется, поток не падает
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.started.set()

    def start(self):
        self.thread.start()
        assert self.started.wait(_T), "поток обработки не стартовал"

    def wait_paused(self):
        assert self.paused.wait(_T), (
            f"обработка не дошла до барьера после шага 5 (ошибка: {self.error})"
        )

    def finish(self):
        self.release.set()
        self.thread.join(timeout=_T)
        if self.thread.is_alive():
            _terminate(self.scene.factory, self.pid)
            self.thread.join(timeout=5)
        assert not self.thread.is_alive(), "поток обработки завис"


def _terminate(factory, pid):
    if pid is None:
        return
    with factory() as db:
        db.execute(sa.text("SELECT pg_terminate_backend(:p)"), {"p": pid})
        db.commit()


def _on_barrier(scene, during):
    """Обработка на барьере после шага 5; `during(apply)` — действия второй
    стороны; затем обработка освобождается и дожидается."""
    apply = _Apply(scene)
    apply.start()
    try:
        apply.wait_paused()
        during(apply)
    finally:
        apply.finish()
    assert apply.error is None, apply.error
    return apply


def _in_session(scene, operation, *, timeout_ms):
    with scene.factory() as db:
        db.execute(sa.text(f"SET LOCAL lock_timeout = '{timeout_ms}ms'"))
        operation(db)
        db.commit()


def _members_of_drafts(scene) -> set[int]:
    with scene.factory() as db:
        return set(
            db.execute(
                sa.select(FamilyDraftMember.context_id).where(
                    FamilyDraftMember.job_id == scene.job_id
                )
            ).scalars()
        )


def _drafts_written(scene) -> int:
    with scene.factory() as db:
        return db.execute(
            sa.select(sa.func.count(FamilyDraft.id)).where(FamilyDraft.job_id == scene.job_id)
        ).scalar_one()


# ---------------------------------------------------------------------------
#  Снимок после шага 5: что проходит сразу
# ---------------------------------------------------------------------------

class TestSnapshotNewRowsPassThrough:
    def test_import_of_a_new_context_passes_and_stays_out_of_the_drafts(self, scene):
        created: dict[str, int] = {}

        def during(apply):
            def op(db):
                factories = scene.cf
                factories._register_session(db)
                try:
                    context_id, _ = _make(db, factories, _proposal(factories), scene.unit, "Имя Г — импорт")
                    _make_system(db, context_id, scene.admin.id)
                    created["id"] = context_id
                finally:
                    factories._register_session(scene.cdb)

            _in_session(scene, op, timeout_ms=_PASS_MS)

        _on_barrier(scene, during)

        assert _drafts_written(scene) == 3
        assert created["id"] not in _members_of_drafts(scene)
        assert _members_of_drafts(scene) == {scene.c1, scene.c2, scene.c3}
        with scene.factory() as db:
            assert created["id"] in is_in_scope(db, [created["id"]])
            assert created["id"] in discovery_scope(db, scene.unit).context_ids

    def test_activation_of_a_family_with_the_draft_name_passes(self, scene):
        def during(apply):
            def op(db):
                family = create_family(
                    db, title="Новая семья", unit_name="M3", definition="Определение",
                    actor_id=scene.admin.id, family_category_id=scene.work,
                )
                activate_family(db, family_id=family.id, actor_id=scene.admin.id)

            _in_session(scene, op, timeout_ms=_PASS_MS)

        _on_barrier(scene, during)

        with scene.factory() as db:
            titles = db.execute(
                sa.select(FamilyDraft.title).where(
                    FamilyDraft.job_id == scene.job_id, FamilyDraft.grp == "new"
                )
            ).scalars().all()
            active = db.execute(
                sa.select(WorkFamily.title).where(
                    WorkFamily.status == "active", WorkFamily.title == "Новая семья"
                )
            ).scalars().all()
        assert titles == ["Новая семья"] and active == ["Новая семья"]

    def test_publication_of_an_existing_family_suggestion_passes_and_removes_the_member(
        self, scene
    ):
        def during(apply):
            def op(db):
                _published(db, scene.c1, family_id=scene.f1.id)

            _in_session(scene, op, timeout_ms=_PASS_MS)

        _on_barrier(scene, during)

        assert _drafts_written(scene) == 3
        with scene.factory() as db:
            assert is_in_scope(db, [scene.c1, scene.c2, scene.c3]) == {scene.c2, scene.c3}

    def test_member_archived_by_the_regular_operations_passes_and_leaves_the_scope(self, scene):
        def during(apply):
            def op(db):
                move_members(
                    db, position_item_ids=[scene.p_keep], target_context_id=scene.target_context,
                    actor_id=scene.admin.id, reason="manual",
                )
                bucket_default = db.execute(
                    sa.select(CatalogContext.is_default).where(CatalogContext.id == scene.c2)
                ).scalar_one()
                archive_context(
                    db, context_id=scene.c2,
                    new_default_context_id=scene.target_context if bucket_default else None,
                    actor_id=scene.admin.id,
                )

            _in_session(scene, op, timeout_ms=_PASS_MS)

        _on_barrier(scene, during)

        assert _drafts_written(scene) == 3
        assert scene.c2 in _members_of_drafts(scene)
        # DoD 7: контекст, получивший членства, в черновиках не появляется.
        assert scene.target_context not in _members_of_drafts(scene)
        with scene.factory() as db:
            assert is_in_scope(db, [scene.c1, scene.c2, scene.c3]) == {scene.c1, scene.c3}


# ---------------------------------------------------------------------------
#  Снимок после шага 5: что ждёт commit обработки
# ---------------------------------------------------------------------------

class TestSnapshotLockedRowsWait:
    def _waits_then_passes(self, scene, operation, check):
        refused: list[bool] = []

        def during(apply):
            try:
                _in_session(scene, operation, timeout_ms=_WAIT_MS)
            except OperationalError as exc:
                refused.append(_is_lock_not_available(exc))
            else:
                refused.append(False)

        apply = _on_barrier(scene, during)
        assert refused == [True], "вторая сторона должна ждать commit обработки"
        assert apply.outcome is not None and apply.outcome.applied
        # После commit обработки то же действие проходит.
        _in_session(scene, operation, timeout_ms=_PASS_MS)
        check()

    def test_assignment_of_a_family_to_a_member_waits(self, scene):
        def op(db):
            assign_family(db, context_id=scene.c1, family_id=scene.f1.id, actor_id=scene.admin.id)

        def check():
            with scene.factory() as db:
                assert db.get(CatalogContext, scene.c1).work_family_id == scene.f1.id

        self._waits_then_passes(scene, op, check)

    def test_update_of_a_family_category_waits(self, scene):
        other = {}

        def op(db):
            if "id" not in other:
                other["id"] = db.execute(
                    sa.select(FamilyCategory.id).where(FamilyCategory.id != scene.work).limit(1)
                ).scalar_one()
            update_family(
                db, family_id=scene.f2.id, family_category_id=other["id"], actor_id=scene.admin.id
            )

        def check():
            with scene.factory() as db:
                assert db.get(WorkFamily, scene.f2.id).family_category_id == other["id"]

        self._waits_then_passes(scene, op, check)

    def test_archiving_the_family_of_a_category_proposal_waits(self, scene):
        def op(db):
            archive_family(db, family_id=scene.f3.id, actor_id=scene.admin.id)

        def check():
            with scene.factory() as db:
                assert db.get(WorkFamily, scene.f3.id).status == "archived"

        self._waits_then_passes(scene, op, check)

    def test_archiving_the_similar_family_waits(self, scene):
        def op(db):
            archive_family(db, family_id=scene.f1.id, actor_id=scene.admin.id)

        def check():
            with scene.factory() as db:
                assert db.get(WorkFamily, scene.f1.id).status == "archived"

        self._waits_then_passes(scene, op, check)

    def test_renaming_a_category_waits(self, scene):
        def op(db):
            update_category(
                db, category_id=scene.work, title="Работа (переименована)", actor_id=scene.admin.id
            )

        def check():
            with scene.factory() as db:
                assert db.get(FamilyCategory, scene.work).title == "Работа (переименована)"

        self._waits_then_passes(scene, op, check)


# ---------------------------------------------------------------------------
#  Гонки без deadlock и без потерянной записи
# ---------------------------------------------------------------------------

def _wait_blocked_or_done(factory, pid, thread, *, timeout=6.0) -> bool:
    """Backend `pid` ждёт чужую блокировку (или поток уже закончил). Опрос
    `pg_stat_activity`, не `sleep` как синхронизация: выход — сразу по условию."""
    deadline = threading.Event()
    waited = 0.0
    while waited < timeout:
        with factory() as db:
            state = db.execute(
                sa.text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :p"), {"p": pid}
            ).scalar_one_or_none()
        if state == "Lock" or not thread.is_alive():
            return True
        deadline.wait(0.05)
        waited += 0.05
    return False


def _old_open_draft(scene, *, category_id) -> int:
    """Открытый черновик прежнего открытия той же единицы."""
    with scene.factory() as db:
        old = SemanticJob(
            kind="family_discovery", status="done", unit_id=scene.unit,
            request_hash=f"old-{uuid.uuid4().hex}", prompt_version="discovery:1",
            model_requested="m", place_dictionary_version=1, candidates_hash="c",
            prefix_hash="p", input_hash="i", response_schema_version="v",
            serialization_version="1",
        )
        db.add(old)
        db.flush()
        draft = FamilyDraft(
            job_id=old.id, unit_id=scene.unit, ordinal=1, grp="new", title="Старый черновик",
            definition="Определение", family_category_id=category_id, status="open",
        )
        db.add(draft)
        db.flush()
        draft_id = draft.id
        db.commit()
    return draft_id


class TestRaces:
    def test_answer_vs_draft_edit_finishes_without_deadlock_and_loses_no_write(self, scene):
        """Правка черновика держит категории `FOR SHARE`, черновик `FOR UPDATE`, затем
        хочет семью `FOR UPDATE` (порядок активации). Обработка без `FOR UPDATE`
        черновиков (шаг 2) успела бы взять семью `FOR KEY SHARE` раньше и замкнула
        бы цикл."""
        draft_id = _old_open_draft(scene, category_id=scene.work)
        editor_locked = threading.Event()
        editor_go = threading.Event()
        editor: dict[str, object] = {"pid": None, "error": None}

        def edit():
            try:
                with scene.factory() as db:
                    editor["pid"] = db.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                    db.execute(
                        sa.select(FamilyCategory.id).order_by(FamilyCategory.id).with_for_update(read=True)
                    ).all()
                    db.execute(
                        sa.select(FamilyDraft.id).where(FamilyDraft.id == draft_id).with_for_update()
                    ).all()
                    editor_locked.set()
                    if not editor_go.wait(_T):
                        raise TimeoutError("go не пришёл")
                    _lock_families(db, [scene.f1.id], exclusive=True)
                    db.execute(
                        sa.update(FamilyDraft).where(FamilyDraft.id == draft_id).values(
                            title="Правка человека"
                        )
                    )
                    db.commit()
            except Exception as exc:  # noqa: BLE001
                editor["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                editor_locked.set()

        te = threading.Thread(target=edit, name="editor", daemon=True)
        apply = _Apply(scene, barrier=False)
        te.start()
        try:
            assert editor_locked.wait(_T), "правка не взяла замки"
            apply.start()
            blocked = _wait_blocked_or_done(scene.factory, apply.pid, apply.thread)
            editor_go.set()
        finally:
            editor_go.set()
            apply.finish()
            te.join(timeout=_T)
            if te.is_alive():
                _terminate(scene.factory, editor["pid"])
                te.join(timeout=5)

        assert blocked, "обработка должна ждать правку, а не обгонять её"
        assert apply.error is None, apply.error
        assert editor["error"] is None, editor["error"]
        assert not te.is_alive()
        with scene.factory() as db:
            old = db.get(FamilyDraft, draft_id)
            assert (old.title, old.status) == ("Правка человека", "superseded")
        assert apply.outcome.applied and apply.outcome.superseded_drafts == 1

    def test_answer_vs_category_delete_finishes_without_deadlock_and_loses_no_write(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """Удаление категории берёт её `FOR UPDATE`, затем черновики с ней. Ответ,
        использовавший эту категорию, при `FOR SHARE` всех категорий (шаг 1) ждёт
        удаление, видит новый справочник на шаге 5 и уходит в `stale_fingerprint`;
        без шага 1 он взял бы черновик первым и замкнул бы цикл через внешний ключ."""
        cdb, cf = committing_db, committing_factories
        admin = cf.UserFactory.create()
        unit = _unit_id(cdb, "M3")
        _active_family(cdb, title="Семья единицы", unit_name="M3", actor_id=admin.id)
        context_id, _ = _make(cdb, cf, _proposal(cf), unit, "Имя А")
        _make_system(cdb, context_id, admin.id)
        extra = create_category(cdb, title="Временная", definition="Для гонки", actor_id=admin.id)
        cdb.commit()
        scene = Scene()
        scene.factory, scene.cdb, scene.cf, scene.unit, scene.admin = (
            committing_session_factory, cdb, cf, unit, admin,
        )
        preview = preview_discovery(cdb, unit_id=unit, settings=S)
        job = launch_discovery(
            cdb, unit_id=unit, preview_hash=preview.preview_hash, actor_id=admin.id, settings=S
        )
        cdb.commit()
        scene.job_id = job.id
        scene.claim = worker.claim_next(cdb, settings=S, now=NOW)
        assert scene.claim is not None
        scene.answer = parse_discovery_answer(
            _answer([_group([1], category_id=extra.id)]), scene.claim.sent
        )
        draft_id = _old_open_draft(scene, category_id=extra.id)

        deleter_paused = threading.Event()
        deleter_go = threading.Event()
        deleter: dict[str, object] = {"pid": None, "error": None}

        def delete():
            try:
                with scene.factory() as db:
                    deleter["pid"] = db.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                    connection = db.connection()

                    def _hook(conn, cursor, statement, parameters, context, executemany):
                        if "family_drafts" in statement and " FOR UPDATE" in statement.upper():
                            deleter_paused.set()
                            if not deleter_go.wait(_T):
                                raise TimeoutError("go не пришёл")

                    event.listen(connection, "before_cursor_execute", _hook)
                    try:
                        delete_category(db, category_id=extra.id, actor_id=admin.id)
                        db.commit()
                    finally:
                        event.remove(connection, "before_cursor_execute", _hook)
            except Exception as exc:  # noqa: BLE001
                deleter["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                deleter_paused.set()

        td = threading.Thread(target=delete, name="deleter", daemon=True)
        apply = _Apply(scene, barrier=False)
        td.start()
        try:
            assert deleter_paused.wait(_T), "удаление не дошло до замка черновиков"
            apply.start()
            blocked = _wait_blocked_or_done(scene.factory, apply.pid, apply.thread)
            deleter_go.set()
        finally:
            deleter_go.set()
            apply.finish()
            td.join(timeout=_T)
            if td.is_alive():
                _terminate(scene.factory, deleter["pid"])
                td.join(timeout=5)

        assert blocked, "обработка должна ждать удаление категории"
        assert apply.error is None, apply.error
        assert deleter["error"] is None, deleter["error"]
        assert not td.is_alive()
        assert apply.outcome is not None
        assert (apply.outcome.applied, apply.outcome.unapplied_reason) == (False, "stale_fingerprint")
        with scene.factory() as db:
            old = db.get(FamilyDraft, draft_id)
            assert old.family_category_id is None and old.status == "open"
            assert db.get(FamilyCategory, extra.id) is None
            assert (
                db.execute(
                    sa.select(sa.func.count(FamilyDraft.id)).where(FamilyDraft.job_id == job.id)
                ).scalar_one()
                == 0
            )
            refreshed = db.get(SemanticJob, job.id)
            assert (refreshed.status, refreshed.cancel_reason) == ("cancelled", "input_changed")
