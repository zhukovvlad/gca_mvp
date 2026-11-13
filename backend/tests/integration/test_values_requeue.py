"""Перезапрос значений после изменившегося входа (спека
`2026-11-13-values-requeue-after-input-change-design.md`, §2 и §3).

Сценарии через исполнитель очереди (`process_one`) идут на закоммиченных
сессиях: подставная модель различает задания по контексту в теле запроса, а не
по порядку вызовов. Задание «в работе» другого контекста — `pending` с
`next_attempt_at` в будущем (исполнитель его не захватывает) либо `running`,
не доведённое до записи результата; что выбрано, сказано в тесте.

Мир сценариев — семья с версией схемы из двух параметров. Задания просят
подмножество параметров (так ставит их сверка, когда нужда частичная), поэтому
расширение списка первого параметра меняет тело запроса только тех заданий,
которые его просят.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import uuid
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

import services.semantic_reconcile as reconcile_module
import services.semantic_worker as worker
from models import CatalogContext, ContextParameterValue, SemanticJob
from services.semantic_client import PermanentModelError
from services.semantic_cost import event_cap_from
from services.variant_request import load_values_material, paths_hash_of, render_values_request
from services.work_families import _lock_families_statement
from tests.integration.test_semantic_queue_schema import _batch
from tests.integration.test_work_variants_material import _frozen_schema
from tests.integration.test_work_variants_reconcile import (
    _active_family,
    _attach,
    _context,
    _job,
)
from tests.integration.test_work_variants_schema import _schema, _variant
from tests.integration.test_work_variants_worker import NOW, S, _response, _values_world

pytestmark = pytest.mark.integration

_VALUES = "context_values"
_PARAMS = [(1, "Толщина", ["50 мм", "100 мм"]), (2, "Материал", ["бетон", "кирпич"])]
#: Позже `NOW` исполнителя: задание с таким сроком захват не берёт.
_LATER = dt.datetime(2100, 1, 1, tzinfo=dt.UTC)

#: Где исполнитель и проход глотают исключение в журнал. Проглоченная ошибка
#: проверки конца пачки или прохода выглядела бы как «ничего не поставлено» —
#: ровно то, что утверждают отрицательные половины.
_SWALLOWING = frozenset({"_reconcile_values_of_family", "process_one", "sweep_semantic_queue"})
#: Тесты, которые роняют проверку намеренно и проверяют журнал сами.
_EXPECTS_SWALLOWED = frozenset({"test_a_failing_check_is_logged_and_the_next_family_is_still_checked"})


@pytest.fixture(autouse=True)
def _no_swallowed_errors(request, caplog):
    yield
    if request.node.originalname in _EXPECTS_SWALLOWED:
        return
    swallowed = [
        record.funcName for record in caplog.records
        if record.name == worker.logger.name and record.levelno >= logging.ERROR
        and record.funcName in _SWALLOWING
    ]
    assert swallowed == [], f"исключение проглочено в журнал: {swallowed}"


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _make_world(db, factories, count, *, stale_paths=()):
    """Активная семья с версией из двух параметров и `count` контекстов семьи,
    у каждого вариант с хэшем путей, совпадающим с текущим (нужды нет), кроме
    индексов `stale_paths` — у тех хэш путей варианта расходится."""
    uid = uuid.uuid4().hex[:8]
    family = _active_family(db)
    schema = _frozen_schema(db, factories, family, _PARAMS)
    params = {
        row.ordinal: row.id
        for row in db.execute(
            sa.text("SELECT ordinal, id FROM family_parameters WHERE schema_id = :s"),
            {"s": schema.id},
        )
    }
    values = {
        (row.ordinal, row.value): row.id
        for row in db.execute(
            sa.text(
                "SELECT p.ordinal, v.value, v.id FROM family_parameter_values v "
                "JOIN family_parameters p ON p.id = v.parameter_id WHERE p.schema_id = :s"
            ),
            {"s": schema.id},
        )
    }
    contexts: list[int] = []
    titles: dict[int, str] = {}
    for index in range(count):
        title = f"Plita{index}x{uid}"
        context_id = _context(db, factories, family=family, title=title)
        paths = load_values_material(db, [context_id])[context_id].paths
        _attach(
            db, context_id, _variant(db, family, schema),
            paths_hash="other-paths" if index in stale_paths else paths_hash_of(paths),
        )
        contexts.append(context_id)
        titles[context_id] = title
    return SimpleNamespace(
        family=family, schema=schema, params=params, values=values, contexts=contexts,
        titles=titles,
    )


def _put(db, world, context_id, ordinal, text, *, marked=False):
    """Строка параметра контекста: `text=None` — пустая (`source='none'`)."""
    db.add(
        ContextParameterValue(
            context_id=context_id, schema_id=world.schema.id,
            parameter_id=world.params[ordinal],
            value_id=None if text is None else world.values[(ordinal, text)],
            source="none" if text is None else "name",
            refresh_requested_at=dt.datetime.now(dt.UTC) if marked else None,
        )
    )
    db.flush()


def _fill(db, world, context_id, *, skip=()):
    for ordinal, text in ((1, "50 мм"), (2, "бетон")):
        if ordinal not in skip:
            _put(db, world, context_id, ordinal, text)


def _post(db, world, context_id, ordinals=None, *, status="pending", request_hash=None,
          later=False):
    """Задание значений контекста по ТЕКУЩЕМУ отпечатку подмножества `ordinals`
    (`None` — все параметры) либо по заданному."""
    material = load_values_material(
        db, [context_id], requested_ordinals=ordinals
    )[context_id]
    job = _job(
        db, kind=_VALUES, context_id=context_id, schema_id=world.schema.id, status=status,
        request_hash=request_hash or render_values_request(material, settings=S).request_hash,
        paths_hash=paths_hash_of(material.paths),
    )
    job.requested_ordinals = None if ordinals is None else list(ordinals)
    if status == "error":
        job.last_error_class = "TimeoutError"
    if later:
        job.next_attempt_at = _LATER
    db.flush()
    return job


def _items(*specs) -> str:
    return json.dumps(
        {
            "values": [
                {"ordinal": ordinal, "kind": kind, "value": text, "source": "name"}
                for ordinal, kind, text in specs
            ]
        },
        ensure_ascii=False,
    )


def _listed(ordinal, text):
    return (ordinal, "value", text)


def _new(ordinal, text):
    return (ordinal, "new", text)


class _Then:
    """Действие, которое клиент выполняет посреди вызова модели, и ответ после него."""

    def __init__(self, action, response):
        self.action = action
        self.response = response


class _Client:
    """Клиент модели: ответ выбирается по наименованию контекста в теле запроса.
    Значение сценария — текст ответа, исключение или `_Then`."""

    def __init__(self, script: dict[str, object]):
        self.script = script
        self.calls: list[str] = []

    def complete(self, body, *, timeout_s):
        text = json.dumps(body, ensure_ascii=False)
        matched = [title for title in self.script if title in text]
        assert len(matched) == 1, f"тело запроса не опознано по контексту: {matched}"
        title = matched[0]
        self.calls.append(title)
        item = self.script[title]
        if isinstance(item, _Then):
            item.action()
            item = item.response
        if isinstance(item, BaseException):
            raise item
        return _response(item)


def _run(factory, client) -> bool:
    return worker.process_one(factory, client, settings=S, clock=lambda: NOW)


def _jobs(factory, context_id=None) -> list[SemanticJob]:
    with factory() as db:
        stmt = sa.select(SemanticJob).where(SemanticJob.kind == _VALUES).order_by(SemanticJob.id)
        if context_id is not None:
            stmt = stmt.where(SemanticJob.context_id == context_id)
        return list(db.execute(stmt).scalars().all())


def _statuses(factory, context_id) -> list[str]:
    return [job.status for job in _jobs(factory, context_id)]


def _statuses_in(db, context_ids) -> dict[int, list[str]]:
    """Статусы заданий значений контекстов по возрастанию `id` задания."""
    return {
        context_id: [
            job.status
            for job in db.execute(
                sa.select(SemanticJob)
                .where(SemanticJob.kind == _VALUES, SemanticJob.context_id == context_id)
                .order_by(SemanticJob.id)
            ).scalars()
        ]
        for context_id in context_ids
    }


def _pending_contexts(factory) -> list[int]:
    return sorted(job.context_id for job in _jobs(factory) if job.status == "pending")


def _fresh_hash(factory, context_id, ordinals=None) -> str:
    with factory() as db:
        material = load_values_material(
            db, [context_id], requested_ordinals=ordinals
        )[context_id]
        return render_values_request(material, settings=S).request_hash


def _snapshot_rows(factory, context_id):
    with factory() as db:
        return sorted(
            tuple(row)
            for row in db.execute(
                sa.text(
                    "SELECT parameter_id, value_id, source, off_list_text, "
                    "refresh_requested_at, job_id FROM context_parameter_values "
                    "WHERE context_id = :c"
                ),
                {"c": context_id},
            )
        )


def _make_due(factory, job_id):
    """Срок задания наступил и раньше сроков заданий, которые сверка поставила
    другим видам (захват берёт самый ранний срок)."""
    with factory() as db:
        db.execute(
            sa.text(
                "UPDATE semantic_jobs SET next_attempt_at = now() - interval '30 days' "
                "WHERE id = :j"
            ),
            {"j": job_id},
        )
        db.commit()


def _wave_scene(db, factories):
    """Три контекста: E расширит список первого параметра (задание просит его
    одного), P просит только второй параметр и расширения не коснётся, W — с
    пустым первым параметром и без задания (цель волны). Задание E старше."""
    world = _make_world(db, factories, 3)
    expanding, plain, empty = world.contexts
    _put(db, world, expanding, 2, "бетон")
    _put(db, world, plain, 1, "50 мм")
    _put(db, world, empty, 1, None)
    _put(db, world, empty, 2, "бетон")
    _post(db, world, expanding, (1,))
    _post(db, world, plain, (2,))
    db.commit()
    return world, expanding, plain, empty


# ---------------------------------------------------------------------------
#  Проверки 3 и 4: волна после расширения не теряется
# ---------------------------------------------------------------------------

class TestValuesRequeueWave:
    """Проверка 3 спеки. Расширившее задание не последнее в пачке семьи; второе
    задание просит только второй параметр, поэтому расширение первого не меняет
    его тела и оно применяется без правки отпечатка. Задание второго контекста в
    работе — `pending`, пока исполнитель не дойдёт до него."""

    def test_the_wave_is_posted_once_per_empty_context_when_the_last_job_did_not_expand(
        self, committing_session_factory, committing_db, committing_factories
    ):
        f = committing_session_factory
        world, expanding, plain, empty = _wave_scene(committing_db, committing_factories)
        client = _Client(
            {
                world.titles[expanding]: _items(_new(1, "150 мм")),
                world.titles[plain]: _items(_listed(2, "кирпич")),
            }
        )

        assert _run(f, client) is True
        assert client.calls == [world.titles[expanding]]
        assert _statuses(f, expanding) == ["done"]
        # Отрицательная половина: пока второе задание в работе, волны нет.
        assert _jobs(f, empty) == []

        assert _run(f, client) is True
        assert client.calls == [world.titles[expanding], world.titles[plain]]
        assert _statuses(f, plain) == ["done"], "второе задание должно быть применено"
        assert _statuses(f, expanding) == ["done"]

        wave = _jobs(f, empty)
        assert [job.status for job in wave] == ["pending"], "заданий волны нет"
        assert _pending_contexts(f) == [empty]

    @pytest.mark.parametrize("ending", ["permanent_error", "invalid_answer"])
    def test_the_wave_is_posted_when_the_last_job_ends_with_an_error(
        self, committing_session_factory, committing_db, committing_factories, ending
    ):
        """Проверка 4: последнее задание пачки закончилось постоянной ошибкой
        вызова или невалидным ответом."""
        f = committing_session_factory
        world, expanding, plain, empty = _wave_scene(committing_db, committing_factories)
        last = (
            PermanentModelError("HTTP 400", error_class="http_400")
            if ending == "permanent_error"
            else '{"values": []}'
        )
        client = _Client(
            {world.titles[expanding]: _items(_new(1, "150 мм")), world.titles[plain]: last}
        )

        _run(f, client)
        assert _jobs(f, empty) == []
        _run(f, client)

        assert _statuses(f, plain) == ["error"], "последнее задание должно закончиться ошибкой"
        assert [job.status for job in _jobs(f, empty)] == ["pending"], "заданий волны нет"
        assert _pending_contexts(f) == [empty]


# ---------------------------------------------------------------------------
#  Проверки 1, 2, 6, 7, 10, 12, 13: повтор по нужде контекста
# ---------------------------------------------------------------------------

class TestValuesRequeueAfterCancel:
    def test_cancel_at_write_requeues_and_the_cancel_writes_no_values(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Проверка 1. Задание жертвы захвачено первым; пока модель думает, другой
        контекст расширил список её параметра; ответ отменён `input_changed`."""
        f = committing_session_factory
        world = _make_world(committing_db, committing_factories, 2)
        victim, expander = world.contexts
        _put(committing_db, world, victim, 2, "бетон")
        _put(committing_db, world, expander, 2, "бетон")
        _post(committing_db, world, victim, (1,))
        _post(committing_db, world, expander, (1,))
        committing_db.commit()
        before = _snapshot_rows(f, victim)
        assert before, "вход: у жертвы есть строка, снимок не пуст"
        inner = _Client({world.titles[expander]: _items(_new(1, "150 мм"))})
        outer = _Client(
            {
                world.titles[victim]: _Then(
                    lambda: _run(f, inner), _items(_listed(1, "50 мм"))
                )
            }
        )

        assert _run(f, outer) is True

        assert inner.calls == [world.titles[expander]]
        assert _statuses(f, expander) == ["done"]
        jobs = _jobs(f, victim)
        assert [job.status for job in jobs] == ["cancelled", "pending"]
        old, new = jobs
        assert old.cancel_reason == "input_changed"
        assert new.request_hash == _fresh_hash(f, victim, (1,))
        assert list(new.requested_ordinals) == [1]
        assert _snapshot_rows(f, victim) == before

    def test_cancel_at_claim_requeues(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Проверка 2. Вход задания изменился до захвата; строка параметра у
        контекста есть и помечена «уточняется»."""
        f = committing_session_factory
        world = _make_world(committing_db, committing_factories, 2)
        victim, other = world.contexts
        _put(committing_db, world, victim, 1, "50 мм", marked=True)
        _put(committing_db, world, victim, 2, "бетон")
        _fill(committing_db, world, other)
        stale = _post(committing_db, world, victim, (1,), request_hash="stale-fingerprint")
        committing_db.commit()
        assert [job.status for job in _jobs(f, victim)] == ["pending"], "вход"

        assert _run(f, _Client({})) is False

        jobs = _jobs(f, victim)
        assert [(job.id, job.status) for job in jobs][0] == (stale.id, "cancelled")
        assert [job.status for job in jobs] == ["cancelled", "pending"]
        assert jobs[1].request_hash == _fresh_hash(f, victim, (1,))
        assert _jobs(f, other) == []

    def test_cancel_at_write_without_other_jobs_of_the_family_is_requeued_by_the_same_job(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Проверка 6: пачки нет — задание ставится проверкой после этого же
        задания."""
        f = committing_session_factory
        world = _make_world(committing_db, committing_factories, 1)
        [context_id] = world.contexts
        _put(committing_db, world, context_id, 2, "бетон")
        _post(committing_db, world, context_id, (1,))
        committing_db.commit()

        def change_the_input():
            with f() as db:
                db.execute(
                    sa.text(
                        "UPDATE catalog_positions SET standard_job_title = 'Changed-title' "
                        "WHERE id = (SELECT b.catalog_position_id FROM context_buckets b "
                        "JOIN catalog_contexts c ON c.bucket_id = b.id WHERE c.id = :c)"
                    ),
                    {"c": context_id},
                )
                db.commit()

        client = _Client(
            {world.titles[context_id]: _Then(change_the_input, _items(_listed(1, "50 мм")))}
        )

        assert _run(f, client) is True

        jobs = _jobs(f, context_id)
        assert [job.status for job in jobs] == ["cancelled", "pending"]
        old, new = jobs
        assert old.cancel_reason == "input_changed"
        assert new.request_hash == _fresh_hash(f, context_id, (1,))

    def test_a_new_parameter_without_rows_is_requeued(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Проверка 7 (ночной случай): строки нового параметра у контекста нет,
        помечать нечего; нужда сама ставит повтор по этому параметру."""
        f = committing_session_factory
        world = _make_world(committing_db, committing_factories, 1)
        [context_id] = world.contexts
        _put(committing_db, world, context_id, 1, "50 мм")
        _post(committing_db, world, context_id, None, request_hash="stale-fingerprint")
        committing_db.commit()
        before = _snapshot_rows(f, context_id)

        assert _run(f, _Client({})) is False

        jobs = _jobs(f, context_id)
        assert [job.status for job in jobs] == ["cancelled", "pending"]
        assert list(jobs[1].requested_ordinals) == [2]
        assert jobs[1].request_hash == _fresh_hash(f, context_id, (2,))
        assert _snapshot_rows(f, context_id) == before

    def test_a_candidate_whose_need_is_already_met_gets_no_job(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch
    ):
        """Проверка 10. Задание отменено, но человек уже проставил значения:
        заданий нет; соседний контекст с нуждой получает задание. Два задания
        семьи отменены одним захватом — проверка семьи идёт один раз."""
        f = committing_session_factory
        world = _make_world(committing_db, committing_factories, 2)
        settled, needy = world.contexts
        _fill(committing_db, world, settled)
        _fill(committing_db, world, needy, skip=(1,))
        _post(committing_db, world, settled, None, request_hash="stale-fingerprint")
        _post(committing_db, world, needy, (1,), request_hash="stale-fingerprint")
        committing_db.commit()
        real = worker.reconcile_after_values_batch
        checked: list[int] = []

        def counting(db, *, family_id):
            checked.append(family_id)
            return real(db, family_id=family_id)

        monkeypatch.setattr(worker, "reconcile_after_values_batch", counting)

        _run(f, _Client({}))

        assert _statuses(f, needy) == ["cancelled", "pending"], "вход: нужда даёт задание"
        assert _statuses(f, settled) == ["cancelled"]
        assert checked == [world.family.id]

    def test_an_old_job_of_the_context_does_not_decide_who_is_a_candidate(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Проверка 12. Задание контекста с меньшим `id` (как оживлённое старое)
        отменено `input_changed` при записи, а задание с большим `id` выполнено
        по прежнему отпечатку; строки заполнены, отметок нет, пути варианта
        разошлись с путями контекста. «Последнее задание по `id`» (здесь `done`)
        признаком быть не может: повтор определяет нужда."""
        f = committing_session_factory
        world = _make_world(committing_db, committing_factories, 2, stale_paths=(0,))
        victim, expander = world.contexts
        _fill(committing_db, world, victim)
        _put(committing_db, world, expander, 2, "бетон")
        first = _post(committing_db, world, victim, None)
        _post(committing_db, world, expander, (1,))
        newer = _post(
            committing_db, world, victim, None, status="done", request_hash="older-fingerprint"
        )
        assert first.id < newer.id
        committing_db.commit()
        inner = _Client({world.titles[expander]: _items(_new(1, "150 мм"))})
        outer = _Client(
            {
                world.titles[victim]: _Then(
                    lambda: _run(f, inner),
                    _items(_listed(1, "50 мм"), _listed(2, "бетон")),
                )
            }
        )

        assert _run(f, outer) is True

        assert _statuses(f, expander) == ["done"]
        jobs = _jobs(f, victim)
        assert [job.status for job in jobs] == ["cancelled", "done", "pending"]
        assert jobs[0].id == first.id and jobs[0].cancel_reason == "input_changed"
        assert jobs[2].request_hash == _fresh_hash(f, victim)

    def test_a_discarded_batch_subject_is_not_brought_back(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Проверка 13. Предмет отброшенной человеком пачки с тем же отпечатком
        проверка конца пачки не возвращает; соседний предмет — ставит."""
        f = committing_session_factory
        world = _make_world(committing_db, committing_factories, 2)
        discarded, needy = world.contexts
        for context_id in world.contexts:
            _put(committing_db, world, context_id, 2, "бетон")
            _post(committing_db, world, context_id, (1,), request_hash="stale-fingerprint")
        committing_db.commit()
        fingerprint = reconcile_module.Fingerprint(
            reconcile_module.SemanticJobKind.context_values, discarded, None, world.schema.id,
            _fresh_hash(f, discarded, (1,)),
        )
        _batch(
            committing_db, committing_factories, status="discarded",
            decided_by=committing_factories.UserFactory.create().id,
            decided_at=dt.datetime.now(dt.UTC), held_fingerprints=[fingerprint.as_dict()],
            fingerprints_hash=reconcile_module.fingerprints_hash([fingerprint]),
        )
        committing_db.commit()

        _run(f, _Client({}))

        assert _statuses(f, needy) == ["cancelled", "pending"], "вход: нужда даёт задание"
        assert _statuses(f, discarded) == ["cancelled"]


# ---------------------------------------------------------------------------
#  Проверки 5, 8, 9, 11, 14: барьер пачки
# ---------------------------------------------------------------------------

class TestValuesRequeueBarrier:
    def test_nothing_is_posted_while_a_job_of_the_family_is_pending_then_exactly_one(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Проверка 5, `pending`: пока у семьи есть ждущее задание (срок позже
        захвата), повтора нет; после его завершения — ровно одно задание."""
        f = committing_session_factory
        world = _make_world(committing_db, committing_factories, 2)
        busy, victim = world.contexts
        _put(committing_db, world, busy, 1, "50 мм")
        _put(committing_db, world, victim, 2, "бетон")
        busy_job = _post(committing_db, world, busy, (2,), later=True)
        _post(committing_db, world, victim, (1,), request_hash="stale-fingerprint")
        committing_db.commit()

        _run(f, _Client({}))
        assert _statuses(f, victim) == ["cancelled"], "пока пачка в работе, повтора нет"

        _make_due(f, busy_job.id)
        assert _run(f, _Client({world.titles[busy]: _items(_listed(2, "кирпич"))})) is True

        assert _statuses(f, busy) == ["done"]
        assert _statuses(f, victim) == ["cancelled", "pending"]
        assert _pending_contexts(f) == [victim]

    def test_nothing_is_posted_while_a_job_of_the_family_is_running_then_exactly_one(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Проверка 5, `running`: задание захвачено и не доведено; после выхода
        из работы проверка конца пачки ставит ровно одно задание."""
        f = committing_session_factory
        world = _make_world(committing_db, committing_factories, 2)
        busy, victim = world.contexts
        _put(committing_db, world, busy, 1, "50 мм")
        _put(committing_db, world, victim, 2, "бетон")
        busy_job = _post(committing_db, world, busy, (2,), status="running")
        _post(committing_db, world, victim, (1,), request_hash="stale-fingerprint")
        committing_db.commit()

        _run(f, _Client({}))
        assert _statuses(f, victim) == ["cancelled"], "пока пачка в работе, повтора нет"

        with f() as db:
            db.execute(
                sa.text("UPDATE semantic_jobs SET status = 'done', claim_token = NULL "
                        "WHERE id = :j"),
                {"j": busy_job.id},
            )
            # Завершённое задание оставило значение второго параметра.
            _put(db, world, busy, 2, "кирпич")
            posted = reconcile_module.reconcile_after_values_batch(
                db, family_id=world.family.id
            )
            db.commit()

        assert posted == 1
        assert _statuses(f, victim) == ["cancelled", "pending"]
        assert _pending_contexts(f) == [victim]

    def test_the_periodic_sweep_waits_for_the_batch_and_the_batch_end_requeues(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Проверка 8. Контекст с отменённым заданием и отметкой; у другого
        контекста той же семьи задание в работе: проход повтор не ставит; после
        завершения того задания повтор появляется."""
        f = committing_session_factory
        world = _make_world(committing_db, committing_factories, 2)
        busy, waiting = world.contexts
        _put(committing_db, world, busy, 1, "50 мм")
        _put(committing_db, world, waiting, 1, "50 мм", marked=True)
        _put(committing_db, world, waiting, 2, "бетон")
        busy_job = _post(committing_db, world, busy, (2,), later=True)
        _post(committing_db, world, waiting, (1,), status="cancelled", request_hash="old")
        # Контроль: тот же вход в семье без пачки — проход его ставит, значит
        # проход дошёл до сверки, а пропуск `waiting` избирателен.
        free = _make_world(committing_db, committing_factories, 1)
        [control] = free.contexts
        _put(committing_db, free, control, 1, "50 мм", marked=True)
        _put(committing_db, free, control, 2, "бетон")
        _post(committing_db, free, control, (1,), status="cancelled", request_hash="old")
        committing_db.commit()

        worker.sweep_semantic_queue(f, settings=S)
        assert _statuses(f, control) == ["cancelled", "pending"], "вход: проход сверяет"
        assert _statuses(f, waiting) == ["cancelled"], "проход ставит повтор посреди пачки"
        # Задание контроля исполнитель дальше не берёт: оно не из этой семьи.
        with f() as db:
            db.execute(
                sa.text("UPDATE semantic_jobs SET next_attempt_at = :t WHERE context_id = :c "
                        "AND status = 'pending'"),
                {"t": _LATER, "c": control},
            )
            db.commit()

        _make_due(f, busy_job.id)
        assert _run(f, _Client({world.titles[busy]: _items(_listed(2, "кирпич"))})) is True

        assert _statuses(f, waiting) == ["cancelled", "pending"]
        assert _pending_contexts(f) == sorted([waiting, control])

    def test_a_job_on_privacy_hold_does_not_hold_the_batch(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Проверка 9. Единственное открытое задание семьи — `privacy_hold`:
        пачка не в работе, проверка ставит повторы."""
        f = committing_session_factory
        world = _make_world(committing_db, committing_factories, 2)
        held, victim = world.contexts
        _fill(committing_db, world, held)
        _put(committing_db, world, victim, 2, "бетон")
        _post(committing_db, world, held, (1,), status="privacy_hold")
        _post(committing_db, world, victim, (1,), request_hash="stale-fingerprint")
        committing_db.commit()

        _run(f, _Client({}))

        assert _statuses(f, victim) == ["cancelled", "pending"]
        assert _statuses(f, held) == ["privacy_hold"]

    def test_the_handler_does_not_post_a_repeat_in_the_middle_of_the_batch(
        self, committing_session_factory, committing_db, committing_factories
    ):
        """Проверка 11. Первый ответ расширяет первый параметр (у второго
        контекста его строка пуста и помечается), второй ответ касался только
        второго параметра и успешен, задание третьего ждёт. После второго ответа
        повтора по первому параметру у второго контекста нет; после завершения
        третьего — ровно одно задание."""
        f = committing_session_factory
        world = _make_world(committing_db, committing_factories, 3)
        first, second, third = world.contexts
        _put(committing_db, world, first, 2, "бетон")
        _put(committing_db, world, second, 1, None)
        _put(committing_db, world, third, 1, "50 мм")
        _post(committing_db, world, first, (1,))
        _post(committing_db, world, second, (2,))
        third_job = _post(committing_db, world, third, (2,), later=True)
        committing_db.commit()
        client = _Client(
            {
                world.titles[first]: _items(_new(1, "150 мм")),
                world.titles[second]: _items(_listed(2, "кирпич")),
                world.titles[third]: _items(_listed(2, "кирпич")),
            }
        )

        _run(f, client)
        _run(f, client)

        assert _statuses(f, second) == ["done"], "вход: второе задание применено"
        assert _pending_contexts(f) == [third], "повтор посреди пачки"

        _make_due(f, third_job.id)
        _run(f, client)

        assert _statuses(f, third) == ["done"]
        assert _statuses(f, second) == ["done", "pending"]
        assert _pending_contexts(f) == [second]

    def test_a_failing_check_is_logged_and_the_next_family_is_still_checked(
        self, committing_session_factory, committing_db, committing_factories, monkeypatch,
        caplog,
    ):
        """Проверка 14. Исключение проверки конца пачки не выходит из
        `process_one`; проверка следующей семьи идёт."""
        f = committing_session_factory
        first = _make_world(committing_db, committing_factories, 1)
        second = _make_world(committing_db, committing_factories, 1)
        for world in (first, second):
            [context_id] = world.contexts
            _put(committing_db, world, context_id, 2, "бетон")
            _post(committing_db, world, context_id, (1,), request_hash="stale-fingerprint")
        committing_db.commit()
        real = worker.reconcile_after_values_batch
        families: list[int] = []

        def flaky(db, *, family_id):
            families.append(family_id)
            if len(families) == 1:
                raise RuntimeError("check failed")
            return real(db, family_id=family_id)

        monkeypatch.setattr(worker, "reconcile_after_values_batch", flaky)

        with caplog.at_level(logging.ERROR, logger=worker.logger.name):
            assert _run(f, _Client({})) is False

        assert families == [first.family.id, second.family.id]
        logged = [
            record.exc_info[1] for record in caplog.records
            if record.exc_info and record.funcName == "_reconcile_values_of_family"
        ]
        assert [(type(exc), str(exc)) for exc in logged] == [(RuntimeError, "check failed")]
        assert _statuses(f, first.contexts[0]) == ["cancelled"]
        assert _statuses(f, second.contexts[0]) == ["cancelled", "pending"]


# ---------------------------------------------------------------------------
#  Замок семьи в проверке конца пачки
# ---------------------------------------------------------------------------

_WAIT_S = 20.0


def _family_lock_race(factory, world, *, batch_job_in_a: bool):
    """Сессия A берёт семью `FOR UPDATE` (и, если `batch_job_in_a`, вставляет
    задание значений X, не коммитя); поток B зовёт проверку конца пачки.
    Возвращает `(ждал ли B замок строки семьи до коммита A, результат B, ошибка B)`."""
    x_context = world.contexts[0]
    info: dict[str, object] = {}
    ready = threading.Event()

    def run_b():
        try:
            with factory() as db_b:
                info["pid"] = db_b.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                db_b.execute(sa.text("SET LOCAL lock_timeout = '25s'"))
                ready.set()
                info["result"] = reconcile_module.reconcile_after_values_batch(
                    db_b, family_id=world.family.id
                )
                db_b.commit()
        except BaseException as exc:  # noqa: BLE001 — исключение потока возвращается тесту
            info["error"] = exc
        finally:
            ready.set()

    waited = False
    db_a = factory()
    thread = threading.Thread(target=run_b, daemon=True)
    try:
        pid_a = db_a.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
        db_a.execute(_lock_families_statement([world.family.id], exclusive=True)).all()
        if batch_job_in_a:
            _post(db_a, world, x_context, (1,))
        thread.start()
        assert ready.wait(_WAIT_S), "поток B не стартовал"
        pause = threading.Event()
        elapsed = 0.0
        while elapsed < _WAIT_S and thread.is_alive() and "pid" in info:
            with factory() as observer:
                row = observer.execute(
                    sa.text(
                        "SELECT wait_event_type, :a = ANY(pg_blocking_pids(pid)) AS by_a, query "
                        "FROM pg_stat_activity WHERE pid = :b"
                    ),
                    {"a": pid_a, "b": info["pid"]},
                ).one_or_none()
            if (
                row is not None
                and row.wait_event_type == "Lock"
                and row.by_a
                and "work_families" in row.query
                and "FOR UPDATE" in row.query
            ):
                waited = True
                break
            pause.wait(0.05)
            elapsed += 0.05
    finally:
        db_a.commit()
        db_a.close()
        thread.join(timeout=_WAIT_S)
    assert not thread.is_alive(), "поток B завис"
    return waited, info.get("result"), info.get("error")


class TestValuesRequeueFamilyLock:
    """`reconcile_after_values_batch` берёт семью `FOR UPDATE` ДО чтения пачки:
    проверки двух последних обработчиков семьи идут по очереди. Два разных
    контекста: X — для задания сессии A (заполнен, кандидатом не бывает), Y —
    кандидат с нуждой и без единого задания; всё закоммичено до потока B, так
    что постановку для Y не может погасить ни уникальность заданий, ни таблица
    исходов."""

    def _world(self, db, factories):
        world = _make_world(db, factories, 2)
        x_context, y_context = world.contexts
        _fill(db, world, x_context)
        _put(db, world, y_context, 2, "бетон")
        db.commit()
        return world, y_context

    def test_the_check_waits_for_the_family_lock_and_sees_the_batch_after_it(
        self, committing_session_factory, committing_db, committing_factories
    ):
        f = committing_session_factory
        world, y_context = self._world(committing_db, committing_factories)

        waited, result, error = _family_lock_race(f, world, batch_job_in_a=True)

        assert error is None
        assert waited, "B не встал на замок строки семьи до коммита A"
        assert result == 0
        assert _jobs(f, y_context) == [], "пачка в работе: задания для Y быть не должно"

    def test_without_a_batch_job_the_check_waits_and_then_posts_exactly_one(
        self, committing_session_factory, committing_db, committing_factories
    ):
        f = committing_session_factory
        world, y_context = self._world(committing_db, committing_factories)

        waited, result, error = _family_lock_race(f, world, batch_job_in_a=False)

        assert error is None
        assert waited, "B не встал на замок строки семьи до коммита A"
        assert result == 1
        assert _statuses(f, y_context) == ["pending"]
        assert _jobs(f, world.contexts[0]) == []


# ---------------------------------------------------------------------------
#  Функции пачки: состав «в работе», кандидаты, барьер сверки
# ---------------------------------------------------------------------------

class TestValuesRequeueBatchFunctions:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            ("pending", True), ("running", True), ("privacy_hold", False), ("done", False),
            ("cancelled", False), ("error", False),
        ],
    )
    def test_only_pending_and_running_jobs_hold_the_batch(
        self, db_session, factories, status, expected
    ):
        world = _make_world(db_session, factories, 1)
        other = _make_world(db_session, factories, 1)
        _post(db_session, world, world.contexts[0], (1,), status=status)

        assert reconcile_module.values_batch_in_work(db_session, world.family.id) is expected
        assert reconcile_module.values_batch_in_work(db_session, other.family.id) is False

    def test_candidates_are_the_needy_unarchived_contexts_in_id_order(
        self, db_session, factories
    ):
        world = _make_world(db_session, factories, 5)
        needy_first, settled, archived, needy_marked, other = world.contexts
        _put(db_session, world, needy_first, 2, "бетон")
        _fill(db_session, world, settled)
        _fill(db_session, world, archived, skip=(1,))
        db_session.get(CatalogContext, archived).archived_at = dt.datetime.now(dt.UTC)
        _put(db_session, world, needy_marked, 1, "50 мм", marked=True)
        _put(db_session, world, needy_marked, 2, "бетон")
        _fill(db_session, world, other)

        candidates = reconcile_module.values_batch_candidates(db_session, world.family.id)

        assert candidates == [needy_first, needy_marked]

    def test_a_context_that_needs_only_a_variant_is_a_candidate(self, db_session, factories):
        """Нужда одного `variant_due` (строки всех параметров есть и не помечены,
        хэш путей совпал, `ordinals` пуст): вариант контекста принадлежит прежней
        версии схемы семьи. Отрицательная половина: тот же контекст с вариантом
        действующей версии кандидатом не бывает."""
        world = _make_world(db_session, factories, 2)
        needs_variant, settled = world.contexts
        for context_id in world.contexts:
            _fill(db_session, world, context_id)
        assert reconcile_module.values_batch_candidates(db_session, world.family.id) == []

        older = _schema(db_session, factories, world.family, version=2, status="superseded")
        db_session.get(CatalogContext, needs_variant).work_variant_id = _variant(
            db_session, world.family, older
        ).id
        db_session.flush()

        candidates = reconcile_module.values_batch_candidates(db_session, world.family.id)

        assert candidates == [needs_variant]
        assert settled not in candidates

    @staticmethod
    def _forbid_reconcile(monkeypatch):
        def boom(*args, **kwargs):
            raise AssertionError("сверка не должна вызываться")

        # Проверка конца пачки сверяет ветвью значений; полная сверка — тоже под запретом.
        monkeypatch.setattr(reconcile_module, "reconcile_context_values", boom)
        monkeypatch.setattr(reconcile_module, "reconcile_semantic_jobs", boom)

    def test_no_candidates_means_zero_and_no_reconcile(self, db_session, factories, monkeypatch):
        world = _make_world(db_session, factories, 1)
        _fill(db_session, world, world.contexts[0])
        self._forbid_reconcile(monkeypatch)

        assert reconcile_module.reconcile_after_values_batch(
            db_session, family_id=world.family.id
        ) == 0

    def test_a_batch_in_work_means_zero_and_no_reconcile_despite_a_candidate(
        self, db_session, factories, monkeypatch
    ):
        world = _make_world(db_session, factories, 2)
        busy, needy = world.contexts
        _put(db_session, world, busy, 1, "50 мм")
        _put(db_session, world, needy, 2, "бетон")
        _post(db_session, world, busy, (2,), later=True)
        # Кандидаты есть (у `busy` тоже нужда — её закрывает его задание).
        assert reconcile_module.values_batch_candidates(db_session, world.family.id) == [
            busy, needy,
        ]
        self._forbid_reconcile(monkeypatch)

        assert reconcile_module.reconcile_after_values_batch(
            db_session, family_id=world.family.id
        ) == 0
        assert _statuses_in(db_session, (needy,)) == {needy: []}

    def test_candidates_follow_the_pending_family_before_the_work_family(
        self, db_session, factories
    ):
        """Семья контекста — `coalesce(pending_family_id, work_family_id)`:
        контекст чужой семьи, ожидающий эту, — кандидат этой; контекст этой
        семьи, ожидающий чужую, — кандидат чужой."""
        from tests.integration.test_work_variants_core import _set_pending

        here = _make_world(db_session, factories, 1)
        there = _make_world(db_session, factories, 1)
        [leaving] = here.contexts
        [arriving] = there.contexts
        _fill(db_session, here, leaving)
        _fill(db_session, there, arriving)
        _set_pending(db_session, factories, leaving, there.family)
        _set_pending(db_session, factories, arriving, here.family)

        assert reconcile_module.values_batch_candidates(db_session, here.family.id) == [arriving]
        assert reconcile_module.values_batch_candidates(db_session, there.family.id) == [leaving]

    @staticmethod
    def _barrier_scene(db, factories):
        """`busy` держит пачку; `fresh` — нужда без заданий (создание), `stale` —
        ждущее задание нетекущего отпечатка (отмена и создание), `revivable` —
        отменённое задание ТЕКУЩЕГО отпечатка (оживление)."""
        world = _make_world(db, factories, 4)
        busy, fresh, stale, revivable = world.contexts
        _put(db, world, busy, 1, "50 мм")
        for context_id in (fresh, stale, revivable):
            _put(db, world, context_id, 2, "бетон")
        _post(db, world, busy, (2,), later=True)
        _post(db, world, stale, (1,), request_hash="stale-fingerprint")
        revived = _post(db, world, revivable, (1,), status="cancelled")
        assert revived.cancel_reason == "input_changed", "вход: задание оживляемо"
        return fresh, stale, revivable

    def test_barrier_blocks_creation_and_revival_but_not_cancellation(
        self, db_session, factories
    ):
        """`values_barrier=True`: при пачке в работе контекст не получает
        задание и не оживляет отменённое, а нетекущее ждущее задание
        отменяется, как всегда."""
        fresh, stale, revivable = self._barrier_scene(db_session, factories)

        report = reconcile_module.reconcile_semantic_jobs(
            db_session, [fresh, stale, revivable], cap=event_cap_from(S), source="operation",
            values_barrier=True,
        )

        # `created` считает и задания предложений, которые барьер не трогает.
        assert (report.cancelled, report.revived) == (1, 0)
        assert _statuses_in(db_session, (fresh, stale, revivable)) == {
            fresh: [], stale: ["cancelled"], revivable: ["cancelled"],
        }

    def test_without_the_barrier_the_same_input_posts_the_jobs(self, db_session, factories):
        fresh, stale, revivable = self._barrier_scene(db_session, factories)

        report = reconcile_module.reconcile_semantic_jobs(
            db_session, [fresh, stale, revivable], cap=event_cap_from(S), source="operation"
        )

        assert report.revived == 1
        assert _statuses_in(db_session, (fresh, stale, revivable)) == {
            fresh: ["pending"], stale: ["cancelled", "pending"], revivable: ["pending"],
        }


# ---------------------------------------------------------------------------
#  Захват отмечает семьи заданий значений
# ---------------------------------------------------------------------------

class TestValuesRequeueClaimNotesFamilies:
    @pytest.mark.parametrize("cause", ["input_changed", "not_applicable"])
    def test_a_job_cancelled_by_the_claim_notes_its_family(self, db_session, factories, cause):
        world = _make_world(db_session, factories, 1)
        [context_id] = world.contexts
        _put(db_session, world, context_id, 2, "бетон")
        _post(db_session, world, context_id, (1,), request_hash="stale-fingerprint")
        if cause == "not_applicable":
            db_session.get(CatalogContext, context_id).archived_at = dt.datetime.now(dt.UTC)
        db_session.flush()
        noted: list[int] = []

        claim = worker.claim_next(
            db_session, settings=S, now=NOW, closed_values_families=noted
        )

        assert claim is None
        assert noted == [world.family.id]

    def test_a_job_put_on_privacy_hold_by_the_claim_notes_its_family(
        self, db_session, factories
    ):
        factories.ContractorFactory.create(title="ООО «Ромашка Строй»")
        scene = _values_world(db_session, factories, title="Стяжка Ромашка Строй")
        noted: list[int] = []

        claim = worker.claim_next(
            db_session, settings=S, now=NOW, closed_values_families=noted
        )

        assert claim is None
        db_session.expire_all()
        assert db_session.get(SemanticJob, scene.job.id).status == "privacy_hold"
        assert noted == [scene.family.id]
