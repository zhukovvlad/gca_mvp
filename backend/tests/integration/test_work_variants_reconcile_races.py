"""Гонки сверки очереди и записи результата с операциями, держащими строки
`FOR UPDATE`.

Сцены. Заморозка схемы против слияния соседней семьи без схемы: сверка не
ждёт семью, которую держит другая операция (`FOR KEY SHARE SKIP LOCKED`), и
занятую семью заводит позднейшая сверка. Версия `building`, рождённая сверкой
у семьи, которую архивируют: итог законен и убирается захватом и
`cancel_schema_build`. Запись результата предложения против операции,
отменяющей то же задание, и против слияния семьи ответа: семья ответа и
контекст берутся `FOR KEY SHARE` до замка задания, поэтому цикла «задание ->
строка» нет. Глобальная пометка строки против маршрутизации импорта:
контекст, рождённый в окне пометки, рождается неприменимым.

Синхронизация — барьер ПОСЛЕ чтения (`after_cursor_execute`: результат уже у
клиента), а не сон; «поток встал на замке» доказывается `pg_blocking_pids` и
текстом ожидающего запроса; у каждого потока `SET LOCAL lock_timeout`, у
каждого `join` — таймаут и обрыв зависшего backend-а, поэтому без защиты тест
краснеет, а не виснет.
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

import services.semantic_worker as worker
from models import (
    CatalogContext,
    ContextBucket,
    FamilyParameterSchema,
    FamilySuggestion,
    SemanticJob,
    WorkFamily,
)
from services.review import set_position_kind_global
from services.semantic_reconcile import reconcile_or_defer
from services.semantic_worker import record_result
from services.work_families import archive_family, assign_family, merge_families
from services.work_variants import (
    apply_values,
    cancel_schema_build,
    freeze_schema,
    mark_context_not_work,
)
from tests.integration.test_semantic_queue_worker import (
    NOW,
    S,
    _active_family,
    _claim_one,
    _job,
    _old_published,
    _response,
    _scene,
)
from tests.integration.test_semantic_queue_worker import _answer as _suggestion_answer
from tests.integration.test_work_variants_concurrency import _freeze_answer, _Scene
from tests.integration.test_work_variants_core import _freeze_world
from tests.integration.test_work_variants_material import _proposal, _settings, _uid
from tests.integration.test_work_variants_schema import _family, _schema

pytestmark = pytest.mark.integration


def _is_job_row_lock(statement: str) -> bool:
    """Замок задания обработчиком результата: полная строка задания по `id`."""
    head = statement.split("FROM", 1)[0]
    return (
        "FROM semantic_jobs" in statement
        and "semantic_jobs.kind" in head
        and statement.rstrip().endswith("FOR UPDATE")
    )


def _is_next_version_read(statement: str) -> bool:
    return "max(family_parameter_schemas.version)" in statement


def _state(factory):
    """Итог гонки свежей сессией: статусы семей, версии схем, задания."""
    with factory() as probe:
        families = dict(probe.execute(sa.select(WorkFamily.id, WorkFamily.status)).all())
        schemas = probe.execute(
            sa.select(
                FamilyParameterSchema.family_id, FamilyParameterSchema.version,
                FamilyParameterSchema.status,
            ).order_by(FamilyParameterSchema.id)
        ).all()
        jobs = probe.execute(
            sa.select(SemanticJob.kind, SemanticJob.family_id, SemanticJob.status).order_by(
                SemanticJob.id
            )
        ).all()
    return families, [tuple(row) for row in schemas], [tuple(row) for row in jobs]


def _quiet_unit(db, context_id):
    """Сверка контекста и перевод заданий предложений в `done`: единица тиха,
    и сверка вправе завести версию схемы семье без схемы."""
    reconcile_or_defer(db, [context_id])
    db.execute(
        sa.update(SemanticJob)
        .where(SemanticJob.kind == "family_suggestion")
        .values(status="done", cancel_reason=None)
    )


def _is_family_lock(statement: str) -> bool:
    return (
        statement.lstrip().startswith("SELECT work_families.id")
        and "FOR UPDATE" in statement
        and "KEY SHARE" not in statement
    )


class TestFreezeAgainstMergeOfASiblingFamily:
    def test_freeze_and_merge_over_the_same_two_families_end_without_deadlock(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """A замораживает схему семьи G (держит G `FOR UPDATE`; сверка хочет
        завести `building` семье-соседке E без схемы той же единицы); B сливает E
        в G: E (меньший `id`) берётся первой, затем ждёт G. Сверка не ждёт
        семью, которую держит другая операция, — цикла нет."""
        db = committing_db
        empty = _family(db, status="active", definition="Пустая семья")
        world = _freeze_world(db, committing_factories)
        actor = committing_factories.UserFactory.create().id
        _quiet_unit(db, world.context_id)
        db.commit()
        assert empty.id < world.family.id
        scene = _Scene(committing_session_factory)

        def freeze(session):
            return freeze_schema(
                session, schema_id=world.building.id, answer=_freeze_answer(), guard=None,
                settings=_settings(),
            )

        def merge(session):
            return merge_families(
                session, source_family_id=empty.id, target_family_id=world.family.id,
                actor_id=actor,
            )

        try:
            scene.spawn("a", freeze, pause_on=_is_family_lock)
            assert scene.wait_paused("a"), "A не взяла семью"
            scene.spawn("b", merge)
            settled = scene.wait_settled("b", contains="work_families")
            scene.release["a"].set()
        finally:
            scene.finish()

        scene.assert_clean()
        assert settled == "blocked", "B не встала на замке семьи, который держит A"
        families, schemas, _jobs = _state(committing_session_factory)
        assert families[empty.id] == "archived"
        assert all(family_id != empty.id for family_id, _version, _status in schemas)


class TestLockedFamilyGetsItsVersionLater:
    def test_a_locked_family_is_skipped_and_a_later_reconcile_creates_the_version(
        self, committing_db, committing_factories, committing_session_factory
    ):
        db = committing_db
        empty = _family(db, status="active", definition="Пустая семья")
        world = _freeze_world(db, committing_factories)
        _quiet_unit(db, world.context_id)
        db.commit()

        with committing_session_factory() as holder:
            holder.execute(sa.text("SET LOCAL lock_timeout = '20s'"))
            holder.execute(
                sa.select(WorkFamily.id).where(WorkFamily.id == empty.id).with_for_update()
            ).all()
            with committing_session_factory() as reconciler:
                reconciler.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
                reconcile_or_defer(reconciler, [world.context_id])
                reconciler.commit()
            _families, schemas, jobs = _state(committing_session_factory)
            assert all(family_id != empty.id for family_id, _v, _s in schemas)
            assert ("family_schema", empty.id, "pending") not in jobs
            holder.rollback()

        with committing_session_factory() as reconciler:
            reconcile_or_defer(reconciler, [world.context_id])
            reconciler.commit()
        _families, schemas, jobs = _state(committing_session_factory)
        assert (empty.id, 1, "building") in schemas
        assert ("family_schema", empty.id, "pending") in jobs


def _stale_the_sibling_job(db, sibling_id):
    job_id = db.execute(
        sa.select(SemanticJob.id).where(
            SemanticJob.kind == "family_schema", SemanticJob.family_id == sibling_id
        )
    ).scalar_one()
    db.execute(
        sa.update(SemanticJob)
        .where(SemanticJob.id == job_id)
        .values(request_hash="stale-hash", status="pending", cancel_reason=None)
    )


class TestSchemaJobForASiblingWithABuildingVersion:
    """Вставка нового задания `family_schema` берёт неявный `FOR KEY SHARE` на
    семью по внешнему ключу `semantic_jobs.family_id`; семью, занятую другой
    операцией, сверка пропускает — цикла нет."""

    def _scene(self, db, factories):
        sibling = _family(db, status="active", definition="Соседка с версией")
        _schema(db, factories, sibling, version=1, status="building")
        world = _freeze_world(db, factories)
        return sibling, world

    def _stale(self, db, sibling, world):
        _quiet_unit(db, world.context_id)
        db.flush()
        _stale_the_sibling_job(db, sibling.id)
        db.commit()
        assert sibling.id < world.family.id

    def _run(self, factory, world, second):
        scene = _Scene(factory)

        def freeze(session):
            return freeze_schema(
                session, schema_id=world.building.id, answer=_freeze_answer(), guard=None,
                settings=_settings(),
            )

        try:
            scene.spawn("a", freeze, pause_on=_is_family_lock)
            assert scene.wait_paused("a"), "A не взяла семью"
            scene.spawn("b", second)
            settled = scene.wait_settled("b", contains="work_families")
            scene.release["a"].set()
        finally:
            scene.finish()
        # Слияние вправе отказать доменной ошибкой (у соседки идёт пересборка);
        # цикл ожидания — `DeadlockDetected` и прочие сбои — недопустим.
        # Заморозка A обязана пройти: доменный отказ допустим только у B.
        unexpected = [e for e in scene.errors if not e.startswith("b: WorkFamilyError:")]
        assert not unexpected, unexpected
        for name, thread in scene.threads.items():
            assert not thread.is_alive(), f"поток {name} завис"
        assert settled == "blocked", "B не встала на замке семьи, который держит A"

    def test_freeze_against_a_merge_of_a_sibling_with_a_building_version(
        self, committing_db, committing_factories, committing_session_factory
    ):
        db = committing_db
        sibling, world = self._scene(db, committing_factories)
        actor = committing_factories.UserFactory.create().id
        self._stale(db, sibling, world)

        def merge(session):
            return merge_families(
                session, source_family_id=sibling.id, target_family_id=world.family.id,
                actor_id=actor,
            )

        self._run(committing_session_factory, world, merge)

    def test_freeze_against_a_values_handler_holding_the_sibling_then_the_target(
        self, committing_db, committing_factories, committing_session_factory
    ):
        from tests.integration.test_work_variants_core import _default_answer
        from tests.integration.test_work_variants_material import _bind
        from tests.integration.test_work_variants_schema_life import _second_context

        db = committing_db
        sibling, world = self._scene(db, committing_factories)
        pending_context = _second_context(db, committing_factories, SimpleNamespace(family=sibling))
        _bind(db, committing_factories, pending_context, pending=world.family)
        self._stale(db, sibling, world)

        def values(session):
            return apply_values(
                session, context_id=pending_context, schema_id=world.current.id,
                answer=_default_answer(), paths_hash="p", guard=None, settings=_settings(),
            )

        self._run(committing_session_factory, world, values)


def _is_lock_jobs(statement: str) -> bool:
    """Замок сверки на задания, которые она меняет (`_lock_jobs`)."""
    return (
        statement.lstrip().startswith("SELECT semantic_jobs.id")
        and "FROM semantic_jobs" in statement
        and statement.rstrip().endswith("FOR UPDATE")
    )


class TestReconcileSkipsAParentHeldByAnOperationWaitingForItsJob:
    """Сверка R без доменных замков (как у импорта) уже держит устаревшее
    задание контекста (`_lock_jobs`) и ставит вместо него новое; операция X
    держит родителя новой строки задания `FOR UPDATE` и ждёт замок того же
    задания. Вставка неявно берёт `FOR KEY SHARE` на родителя: без пропуска
    занятого родителя R ждала бы X, а X — R. Родитель здесь — контекст
    (глобальная пометка строки) и текущая версия схемы (косметическая правка
    схемы, контекст не берёт): семья в этих сценах ни при чём."""

    def _run(self, factory, context_id, operation, *, settled_on):
        scene = _Scene(factory)
        try:
            scene.spawn(
                "r", lambda session: reconcile_or_defer(session, [context_id]),
                pause_on=_is_lock_jobs,
            )
            assert scene.wait_paused("r"), "R не взяла задания"
            scene.spawn("x", operation)
            settled = scene.wait_settled("x", contains=settled_on)
            scene.release["r"].set()
        finally:
            scene.finish()
        scene.assert_clean()
        assert settled == "blocked", "X не встала на замке задания, который держит R"

    def test_a_context_held_by_a_global_mark(
        self, committing_db, committing_factories, committing_session_factory
    ):
        db = committing_db
        scene_data = _scene(db, committing_factories)
        context_id, family_id = scene_data.context_ids[0], scene_data.family.id
        assign_family(
            db, context_id=context_id, family_id=family_id, actor_id=scene_data.user.id
        )
        db.execute(sa.delete(SemanticJob))
        stale = _job(db, context_id, unit_id=scene_data.unit_id, request_hash="stale-hash")
        position_id = db.execute(
            sa.select(ContextBucket.catalog_position_id)
            .join(CatalogContext, CatalogContext.bucket_id == ContextBucket.id)
            .where(CatalogContext.id == context_id)
        ).scalar_one()
        db.commit()
        stale_id, actor = stale.id, scene_data.user.id

        def mark_row(session):
            return set_position_kind_global(
                session, position_id=position_id, kind="HEADER", actor_id=actor
            )

        self._run(committing_session_factory, context_id, mark_row, settled_on="semantic_jobs")
        with committing_session_factory() as probe:
            assert probe.get(SemanticJob, stale_id).status == "cancelled"
            live = probe.execute(
                sa.select(sa.func.count()).select_from(SemanticJob).where(
                    SemanticJob.context_id == context_id, SemanticJob.status == "pending"
                )
            ).scalar_one()
        assert live == 0, "у контекста строки HEADER осталось задание"

    def _stale_values_world(self, db, factories):
        """Семья с текущей версией и контекст; задание значений контекста
        устарело (другой `request_hash`), задания предложений `done`."""
        from tests.integration.test_work_variants_schema_life import _active_world

        world = _active_world(db, factories)
        reconcile_or_defer(db, [world.context_id])
        [values_job] = db.execute(
            sa.select(SemanticJob).where(
                SemanticJob.kind == "context_values", SemanticJob.context_id == world.context_id
            )
        ).scalars().all()
        db.execute(
            sa.update(SemanticJob)
            .where(SemanticJob.kind == "family_suggestion")
            .values(status="done", cancel_reason=None)
        )
        values_job.request_hash = "stale-hash"
        return world, values_job

    def test_a_context_held_by_a_mark_not_work_for_a_values_job(
        self, committing_db, committing_factories, committing_session_factory
    ):
        db = committing_db
        world, values_job = self._stale_values_world(db, committing_factories)
        actor = committing_factories.UserFactory.create().id
        db.commit()
        stale_id, context_id = values_job.id, world.context_id

        def mark_context(session):
            return mark_context_not_work(session, context_id=context_id, actor_id=actor)

        self._run(committing_session_factory, context_id, mark_context, settled_on="semantic_jobs")
        with committing_session_factory() as probe:
            assert probe.get(SemanticJob, stale_id).status == "cancelled"
            live = probe.execute(
                sa.select(sa.func.count()).select_from(SemanticJob).where(
                    SemanticJob.context_id == context_id, SemanticJob.status == "pending"
                )
            ).scalar_one()
        assert live == 0, "у контекста «не работа» осталось задание"

    def test_a_current_schema_held_by_a_cosmetic_edit(
        self, committing_db, committing_factories, committing_session_factory
    ):
        from services.work_variants import ParameterEdit, update_schema
        from tests.integration.test_work_variants_schema_life import _same_edits

        db = committing_db
        world, values_job = self._stale_values_world(db, committing_factories)
        actor = committing_factories.UserFactory.create().id
        db.commit()
        stale_id, family_id, context_id = values_job.id, world.family.id, world.context_id
        edits = _same_edits()
        edits[0] = ParameterEdit(1, edits[0].name.upper(), edits[0].values)

        def cosmetic(session):
            return update_schema(session, family_id=family_id, parameters=edits, actor_id=actor)

        self._run(committing_session_factory, context_id, cosmetic, settled_on="semantic_jobs")
        with committing_session_factory() as probe:
            assert probe.get(SemanticJob, stale_id).status == "cancelled"
            live = probe.execute(
                sa.select(sa.func.count()).select_from(SemanticJob).where(
                    SemanticJob.kind == "context_values",
                    SemanticJob.context_id == context_id,
                    SemanticJob.status == "pending",
                )
            ).scalar_one()
        assert live == 1, "после правки у контекста ровно одно задание значений по новому тексту"


class TestBuildingVersionAgainstArchive:
    def test_a_building_born_after_the_archive_is_cleaned_by_the_claim_and_the_cancel(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """Сверка (A) планирует версию `building` активной семье E без схемы;
        на чтении следующего номера версии E архивируют (B): архивирование ждёт
        коммита сверки, которая держит E `FOR KEY SHARE`. Архивирование `building` не запрещает, поэтому итог — законное
        состояние: архивная семья с версией `building` и заданием `pending`.
        Задание снимает захват (семья не `active`), версию —
        `cancel_schema_build`, который для архивной семьи разрешён."""
        db = committing_db
        empty = _family(db, status="active", definition="Пустая семья")
        world = _freeze_world(db, committing_factories)
        actor = committing_factories.UserFactory.create().id
        _quiet_unit(db, world.context_id)
        db.execute(sa.update(SemanticJob).values(status="done", cancel_reason=None))
        db.commit()
        scene = _Scene(committing_session_factory)

        def reconcile(session):
            return reconcile_or_defer(session, [world.context_id])

        def archive(session):
            return archive_family(session, family_id=empty.id, actor_id=actor)

        try:
            scene.spawn("a", reconcile, pause_on=_is_next_version_read)
            assert scene.wait_paused("a"), "A не дошла до вставки версии"
            scene.spawn("b", archive)
            # Сверка уже держит семью `FOR KEY SHARE`: архивирование ждёт её коммита.
            assert scene.wait_settled("b", contains="work_families") == "blocked"
            scene.release["a"].set()
            assert scene.done["b"].wait(timeout=30), "архивирование не закончилось"
        finally:
            scene.finish()

        scene.assert_clean()
        families, schemas, jobs = _state(committing_session_factory)
        assert families[empty.id] == "archived"
        assert (empty.id, 1, "building") in schemas
        assert ("family_schema", empty.id, "pending") in jobs

        # Захват видит единственное готовое задание — семьи `E` — и снимает его.
        with committing_session_factory() as cleanup:
            cleanup.execute(
                sa.update(SemanticJob)
                .where(SemanticJob.kind != "family_schema")
                .values(status="done", cancel_reason=None)
            )
            cleanup.commit()
            claim = worker.claim_next(
                cleanup, settings=S, now=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=5)
            )
            cleanup.commit()
        _families, schemas, jobs = _state(committing_session_factory)
        assert claim is None
        assert ("family_schema", empty.id, "cancelled") in jobs
        assert (empty.id, 1, "building") in schemas

        with committing_session_factory() as cleanup:
            cancel_schema_build(cleanup, family_id=empty.id, actor_id=actor)
            cleanup.commit()
        _families, schemas, _jobs = _state(committing_session_factory)
        assert (empty.id, 1, "cancelled") in schemas


def _is_jobs_of_contexts_read(statement: str) -> bool:
    """Чтение заданий набора контекстов, с которого сверка строит план."""
    return (
        statement.lstrip().startswith("SELECT")
        and "FROM semantic_jobs" in statement
        and "semantic_jobs.context_id IN" in statement
        and "FOR UPDATE" not in statement
    )


class TestRecordResultAgainstAnOperationCancellingItsJob:
    """Сверка операции B прочитала задание контекста как `pending` и собирается
    его отменить (контекст становится неприменим), а захват A успел его взять и
    дошёл до записи результата с семьёй ответа F. Строки F и контекста A берёт
    `FOR KEY SHARE` ДО замка задания, поэтому ждёт B, ничего не держа; без
    предварительных замков A держала бы задание, а вставка предложения встала
    бы на `FOR KEY SHARE` строки, которую держит B, ожидающая задание, — цикл."""

    def _race(self, factory, db, factories, operation, *, waits_on):
        scene_data = _scene(db, factories)
        context_id, family_id = scene_data.context_ids[0], scene_data.family.id
        assign_family(
            db, context_id=context_id, family_id=family_id, actor_id=scene_data.user.id
        )
        db.execute(sa.delete(SemanticJob))
        job = _job(db, context_id, unit_id=scene_data.unit_id)
        position_id = db.execute(
            sa.select(ContextBucket.catalog_position_id)
            .join(CatalogContext, CatalogContext.bucket_id == ContextBucket.id)
            .where(CatalogContext.id == context_id)
        ).scalar_one()
        db.commit()
        scene = _Scene(factory)
        holder: dict = {}

        def record(session):
            return record_result(
                session, holder["claim"], _response(_suggestion_answer(family_id)),
                now=NOW, settings=S,
            )

        try:
            scene.spawn(
                "b", lambda session: operation(session, context_id, position_id, scene_data.user.id),
                pause_on=_is_jobs_of_contexts_read,
            )
            assert scene.wait_paused("b"), "B не прочитала задания контекста"
            with factory() as claim_db:
                holder["claim"] = _claim_one(claim_db)
                claim_db.commit()
            assert holder["claim"].job_id == job.id
            scene.spawn("a", record, pause_on=_is_job_row_lock)
            settled = scene.wait_settled("a", contains=waits_on)
            scene.release["b"].set()
            scene.release["a"].set()
        finally:
            scene.finish()
        scene.assert_clean()
        assert settled == "blocked", f"A не встала на {waits_on}, которые держит B"
        with factory() as probe:
            assert probe.get(SemanticJob, job.id).status == "done"
            published = probe.execute(
                sa.select(sa.func.count()).select_from(FamilySuggestion).where(
                    FamilySuggestion.context_id == context_id, FamilySuggestion.is_published
                )
            ).scalar_one()
        assert published == 0, "предложение осталось опубликованным у контекста «не работа»"

    def test_a_global_mark_holding_the_family_for_update_makes_the_result_wait_for_the_family(
        self, committing_db, committing_factories, committing_session_factory
    ):
        def mark_row(session, _context_id, position_id, actor_id):
            return set_position_kind_global(
                session, position_id=position_id, kind="HEADER", actor_id=actor_id
            )

        self._race(
            committing_session_factory, committing_db, committing_factories, mark_row,
            waits_on="work_families",
        )

    def test_a_mark_not_work_holding_the_context_makes_the_result_wait_for_the_context(
        self, committing_db, committing_factories, committing_session_factory
    ):
        def mark_context(session, context_id, _position_id, actor_id):
            return mark_context_not_work(session, context_id=context_id, actor_id=actor_id)

        self._race(
            committing_session_factory, committing_db, committing_factories, mark_context,
            waits_on="catalog_contexts",
        )


class TestRecordResultAgainstAMergeOfTheAnswerFamily:
    def test_the_result_waits_for_the_answer_family_before_it_touches_the_suggestions(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """Слияние B держит семью ответа F `FOR UPDATE` и затем берёт
        `FOR UPDATE` предложения F — среди них опубликованное предложение
        контекста A. Запись результата A с ответом F берёт F `FOR KEY SHARE` до
        задания и до снятия публикации, поэтому ждёт B, ничего не держа. Без
        предварительного замка семьи A сняла бы публикацию (замок строки
        предложения) и встала бы на внешнем ключе F при вставке, а B — на этой
        строке: цикл. Контекст B не держит, поэтому замок контекста здесь не
        защищает, в отличие от сцен с глобальной пометкой и «не работой»."""
        db = committing_db
        scene_data = _scene(db, committing_factories)
        context_id, family_id = scene_data.context_ids[0], scene_data.family.id
        target = _active_family(
            db, title=f"Семья-цель {_uid()}", unit_name="M2", actor_id=scene_data.user.id
        )
        # Цель меняет список кандидатов: задание ставится на отпечаток после неё.
        db.execute(sa.delete(SemanticJob))
        job = _job(db, context_id, unit_id=scene_data.unit_id)
        old = _old_published(db, context_id)
        old.family_id, old.new_family_name = family_id, None
        db.commit()
        target_id, old_id, job_id = target.id, old.id, job.id
        factory = committing_session_factory
        scene = _Scene(factory)
        holder: dict = {}

        def merge(session):
            return merge_families(
                session, source_family_id=family_id, target_family_id=target_id,
                actor_id=scene_data.user.id,
            )

        def record(session):
            return record_result(
                session, holder["claim"], _response(_suggestion_answer(family_id)),
                now=NOW, settings=S,
            )

        try:
            scene.spawn("b", merge, pause_on=_is_family_lock)
            assert scene.wait_paused("b"), "B не взяла семьи"
            with factory() as claim_db:
                holder["claim"] = _claim_one(claim_db)
                claim_db.commit()
            assert holder["claim"].job_id == job_id
            scene.spawn("a", record)
            settled = scene.wait_settled("a", contains="work_families")
            scene.release["b"].set()
        finally:
            scene.finish()
        scene.assert_clean()
        assert settled == "blocked", "A не встала на семье ответа, которую держит B"
        with factory() as probe:
            assert probe.get(SemanticJob, job_id).status == "done"
            assert probe.get(FamilySuggestion, old_id).family_id == target_id
            assert probe.get(WorkFamily, family_id).status == "archived"


def _is_row_kind_update(statement: str) -> bool:
    return statement.lstrip().startswith("UPDATE catalog_positions SET kind")


class TestGlobalMarkAgainstImportRouting:
    def test_a_context_born_by_the_import_during_the_mark_is_born_not_applicable(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """Строка каталога без контекстов; пометка (A) держит её `FOR UPDATE` с
        записанным `kind`, а маршрутизация импорта (B) заводит корзину и
        контекст по умолчанию. B встаёт на замке строки и после коммита A
        перечитывает `kind`: контекст рождается `NOT_APPLICABLE`, применимого
        контекста у строки `HEADER` не остаётся и сверять нечего."""
        from services.context_routing import route_position

        db = committing_db
        catalog = committing_factories.CatalogPositionFactory.create(
            standard_job_title=f"Стяжка {_uid()}", unit_id=None
        )
        position = committing_factories.PositionItemFactory.create(
            proposal=_proposal(committing_factories), is_chapter=False,
            job_title_in_proposal="Стяжка", catalog_position_id=catalog.id,
        )
        actor = committing_factories.UserFactory.create().id
        catalog_id, position_item_id = catalog.id, position.id
        db.commit()
        scene = _Scene(committing_session_factory)

        def mark(session):
            return set_position_kind_global(
                session, position_id=catalog_id, kind="HEADER", actor_id=actor
            )

        def route(session):
            return route_position(session, position_item_id=position_item_id)

        try:
            scene.spawn("a", mark, pause_on=_is_row_kind_update)
            assert scene.wait_paused("a"), "A не записала kind строки"
            scene.spawn("b", route)
            settled = scene.wait_settled("b", contains="INSERT INTO context_buckets")
            scene.release["a"].set()
        finally:
            scene.finish()

        scene.assert_clean()
        assert settled == "blocked", "B не встала на вставке корзины (внешний ключ на строку, которую держит A)"
        with committing_session_factory() as probe:
            states = probe.execute(
                sa.select(CatalogContext.semantic_state)
                .join(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
                .where(ContextBucket.catalog_position_id == catalog_id)
            ).scalars().all()
        assert states == ["NOT_APPLICABLE"]
