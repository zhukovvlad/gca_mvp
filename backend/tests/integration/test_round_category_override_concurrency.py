"""Конкуренция раундовой записи (спека этапного разноса §2.4 п.1, §4.2).

Мера, которую даёт `_lock_scope`: второй раундовый писатель ЖДЁТ первого, а не
пересчитывает поверх ещё не закоммиченного набора решений. Это тот же класс
дефекта, ради которого по-сметный сервис берёт `FOR UPDATE` на смету
(докстрока `test_category_override_concurrency.py`), только радиусом в целый
раунд: пересчёт каждой сметы ПОЛНЫЙ, и писатель, прочитавший набор решений до
чужого коммита, материализует дерево без чужого решения. Отказа при этом не
будет — обе транзакции закоммитятся; потеряется не решение, а его
материализация.

Что чем доказывается. Два раздела сцены («14» и «15») намеренно НЕЗАВИСИМЫ:
ни один не предок другого, наборы строк `position_items`, которые трогает
пересчёт каждого, не пересекаются, и конфликта уровня строки между писателями
нет. Поэтому без блокировки смет второй писатель не встаёт на ожидание ВООБЩЕ,
и ассерт об ожидании краснеет именно по факту отсутствия блокировки, а не по
слепому таймауту.

Ожидание проверяется ИМЕННО НАШЕ, а не «хоть чьё-то». Первая редакция этого
файла считала строки `pg_stat_activity` с `wait_event_type = 'Lock'` по всей
базе, и ревью показало на этом ложную зелень: сними цикл блокировок смет и
одновременно понизь замки тендера и раунда до `FOR NO KEY UPDATE` — второй
писатель встанет на строке ТЕНДЕРА, счётчик увидит ожидание, и тест
промолчит. Ровно тот сценарий, про который докстрока и предупреждала, а
защищала его только прозой. Теперь `_wait_until_blocked_by` проверяет три
вещи: кто заблокирован, КЕМ он заблокирован (`pg_blocking_pids` — это должен
быть backend первого писателя) и НА ЧЁМ (в тексте ожидающего запроса —
`FOR UPDATE` по `estimates`). Побочно это закрывает и вторую ложную зелень:
посторонний backend той же базы в ожидании замка — другой прогон pytest, psql
или pgweb — прежде дал бы зелёный.

Предпосылка красного — блокировки тендера и раунда ВЫШЕ цикла по сметам
берутся настоящим `FOR KEY SHARE` (`with_for_update(read=True,
key_share=True)`), совместимым сам с собой. Одно `key_share=True` дало бы
`FOR NO KEY UPDATE`, несовместимый сам с собой (находка внешнего ревью плана
01.09.2026; `docs/insights/verifying-guards.md`, слой 8). Компиляция сверена
оркестратором перед негативной проверкой: `with_for_update(read=True,
key_share=True)` → `FOR KEY SHARE`, `with_for_update(key_share=True)` →
`FOR NO KEY UPDATE`.

Порядок захвата — утверждение о ПОТОКЕ данных, а не о результате
(`docs/insights/data-flow-assertions-for-order.md`): результат один при любом
порядке, различить порядок по нему нельзя. Слушатель на курсоре записывает ВСЕ
три звена цепочки — tender, round, сметы, — потому что шпион на одном звене
оставляет непокрытыми остальные: ревью подняло цикл блокировки смет ВЫШЕ
замков тендера и раунда, и прежняя редакция, следившая только за
`lock_estimate`, осталась зелёной.

`_wait_until_a_backend_blocks` — копия приёма из
`test_category_override_concurrency.py`, а не импорт: тестовые хелперы в этом
репозитории между модулями тестов не делятся.

Тест требует НАСТОЯЩИХ транзакций (транзакционная `db_session` — один
savepoint, двух параллельных транзакций на ней не выразить), поэтому работает
на `committing_session_factory`.
"""
from __future__ import annotations

import threading
import time

import pytest
import sqlalchemy as sa

from models import (
    Estimate,
    EstimateCategoryOverride,
    Lot,
    Offer,
    PositionItem,
    Proposal,
    UserRole,
    WorkCategory,
)
from services import round_category_override as rco
from services.category_resolution import CategoryResolver
from services.round_import import import_round
from services.unit_resolution import UnitResolver
from tests.payloads import position, proposal, round_payload

pytestmark = pytest.mark.integration


def _statement(inn: str, title: str):
    """Ведомость сцены: два независимых раздела, у каждого своя позиция."""
    return proposal(
        [
            position(job_title="SHELL & CORE", is_chapter=True, chapter_number="14", number="1"),
            position(job_title="Работа 14", unit="м2", quantity=1, suggested_quantity=1,
                     unit_cost_total="30.00", total_cost_total="30.00", chapter_ref="14", number="2"),
            position(job_title="Рабочая документация", is_chapter=True, chapter_number="15", number="3"),
            position(job_title="Работа 15", unit="м2", quantity=1, suggested_quantity=1,
                     unit_cost_total="10.00", total_cost_total="10.00", chapter_ref="15", number="4"),
        ],
        inn=inn,
        title=title,
    )


@pytest.fixture
def scene(committing_db, committing_factories, committing_session_factory):
    """Раунд из трёх участников, построенный настоящим `import_round`.

    Ключи разделов и `lot_key` читаются из БД, а не зашиваются литералами:
    именование лотов — деталь импорта, и фикстура `round_scene` соседнего файла
    следует тому же правилу.
    """
    tender = committing_factories.TenderFactory.create()
    rnd = committing_factories.TenderRoundFactory.create(tender=tender, stage_no=1)
    committing_db.flush()
    import_round(
        committing_db,
        tender_round=rnd,
        data=round_payload([
            _statement("7700000001", "ООО А"),
            _statement("7700000002", "ООО Б"),
            _statement("7700000003", "ООО В"),
        ]),
        parser_version="4.0.0",
        import_job_id=None,
        replace=False,
        unit_resolver=UnitResolver(committing_db),
        category_resolver=CategoryResolver.from_db(committing_db),
    )
    first = committing_factories.UserFactory.create(role=UserRole.admin)
    second = committing_factories.UserFactory.create(role=UserRole.admin)
    used_as_parent = sa.select(WorkCategory.parent_id).where(WorkCategory.parent_id.is_not(None))
    cat_a, cat_b = committing_db.execute(
        sa.select(WorkCategory.id)
        .where(WorkCategory.id.not_in(used_as_parent))
        .order_by(WorkCategory.sort_order)
        .limit(2)
    ).scalars().all()
    committing_db.commit()

    ids = committing_db.execute(
        sa.select(Estimate.id)
        .join(Offer, Offer.id == Estimate.offer_id)
        .where(Offer.round_id == rnd.id)
        .order_by(Estimate.id)
    ).scalars().all()
    lot_key = committing_db.execute(
        sa.select(Lot.lot_key).where(Lot.estimate_id == ids[0])
    ).scalar_one()

    def chapter_key(number: str) -> tuple[str, str]:
        key = committing_db.execute(
            sa.select(PositionItem.position_key_in_proposal)
            .join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == ids[0], PositionItem.is_chapter.is_(True),
                   PositionItem.chapter_number_in_proposal == number)
        ).scalar_one()
        return (lot_key, key)

    class Scene:
        session_factory = committing_session_factory
        tender_id = tender.id
        round_id = rnd.id
        estimate_ids = list(ids)
        first_id = first.id
        second_id = second.id
        category_a = cat_a
        category_b = cat_b
        key14 = chapter_key("14")
        key15 = chapter_key("15")

    return Scene()


def _wait_until_blocked_by(session_factory, blocker_pid: int, *, timeout: float = 15.0) -> str | None:
    """Ждёт backend, заблокированного ИМЕННО `blocker_pid`, и отдаёт его запрос.

    Отличие от одноимённого приёма в `test_category_override_concurrency.py`:
    там достаточно было факта «кто-то ждёт», потому что писатель один и замок
    один. Здесь замков три (tender, round, смета), и «кто-то ждёт» — ложная
    зелень: второй писатель, вставший на строке тендера из-за неверно взятого
    замка, засчитался бы как успех. Поэтому спрашивается `pg_blocking_pids()`
    и возвращается текст ожидающего запроса — вызывающая сторона проверяет,
    что ждут на нужном объекте.

    `None` — за отведённое время никто не встал на замок первого писателя.
    """
    deadline = time.monotonic() + timeout
    with session_factory() as probe:
        while time.monotonic() < deadline:
            waiting = probe.execute(
                sa.text(
                    "SELECT query FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                    "AND :blocker = ANY (pg_blocking_pids(pid)) LIMIT 1"
                ),
                {"blocker": blocker_pid},
            ).scalar_one_or_none()
            probe.rollback()
            if waiting is not None:
                return waiting
            time.sleep(0.05)
    return None


def _backend_pid(session) -> int:
    return session.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()


def _lock_trace(session) -> tuple[list[str], object]:
    """Пишет порядок ВСЕХ блокирующих запросов сессии — три звена цепочки §2.4 п.1.

    Шпион на `lock_estimate` покрывает только последнее звено; ревью подняло
    цикл смет выше замков тендера и раунда, и такой шпион остался зелёным.
    Слушатель курсора видит все три.
    """
    trace: list[str] = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        upper = statement.upper()
        if "FOR KEY SHARE" not in upper and "FOR UPDATE" not in upper:
            return
        # Порядок проверок важен: «tender_rounds» содержит подстроку «tenders»
        # только после подмены, поэтому раунд проверяется первым явно.
        if "TENDER_ROUNDS" in upper:
            trace.append("round")
        elif "TENDERS" in upper:
            trace.append("tender")
        elif "ESTIMATES" in upper:
            trace.append("estimate")
        else:  # pragma: no cover — иной блокирующий запрос в сервисе не ожидается
            trace.append(f"other:{statement[:40]}")

    bind = session.get_bind()
    sa.event.listen(bind, "before_cursor_execute", record)
    return trace, (bind, record)


def _stop_lock_trace(handle) -> None:
    bind, record = handle
    sa.event.remove(bind, "before_cursor_execute", record)


def _put(session, scene, key, category, user_id):
    return rco.set_round_override(
        session,
        tender_id=scene.tender_id,
        round_id=scene.round_id,
        lot_key=key[0],
        position_key_in_proposal=key[1],
        work_category_id=category,
        note=None,
        user_id=user_id,
    )


class TestTwoRoundWriters:
    def test_the_second_writer_waits_for_the_first_and_both_land(self, scene):
        outcome: dict[str, object] = {}
        started = threading.Event()

        def second_writer():
            # try/except ОБЪЕМЛЕТ `with`: иначе исключение из `__exit__` сессии
            # (закрытие, откат) уходит мимо, поток тихо падает после
            # `outcome["done"] = True`, и тест остаётся зелёным.
            try:
                with scene.session_factory() as s:
                    started.set()
                    _put(s, scene, scene.key15, scene.category_b, scene.second_id)
                    s.commit()
                outcome["done"] = True
            except Exception as exc:  # pragma: no cover — диагностика падения потока
                outcome["error"] = f"{type(exc).__name__}: {exc}"

        with scene.session_factory() as first:
            _put(first, scene, scene.key14, scene.category_a, scene.first_id)
            first_pid = _backend_pid(first)
            t = threading.Thread(target=second_writer, daemon=True)
            t.start()
            assert started.wait(timeout=10)
            # МЕХАНИЗМ: без FOR UPDATE по сметам второй писатель не встаёт на
            # замок вовсе — разделы независимы, конфликта уровня строки нет.
            # Проверяется НАШЕ ожидание: ждать должны первого писателя и
            # именно на строке сметы, иначе замок держит что-то другое.
            waiting_query = _wait_until_blocked_by(scene.session_factory, first_pid)
            assert waiting_query is not None, f"второй писатель не ждёт первого; поток: {outcome}"
            upper = waiting_query.upper()
            assert "FOR UPDATE" in upper and "ESTIMATES" in upper, (
                f"второй писатель ждёт не на строке сметы: {waiting_query}"
            )
            first.commit()
        t.join(timeout=20)
        assert not t.is_alive() and outcome == {"done": True}, outcome

        with scene.session_factory() as check:
            radius = sa.select(Lot.id).where(Lot.estimate_id.in_(scene.estimate_ids))
            decided = check.execute(
                sa.select(PositionItem.position_key_in_proposal, sa.func.count())
                .join(EstimateCategoryOverride, EstimateCategoryOverride.position_item_id == PositionItem.id)
                .join(Proposal, Proposal.id == PositionItem.proposal_id)
                .where(Proposal.lot_id.in_(radius))
                .group_by(PositionItem.position_key_in_proposal)
            ).all()
            # Оба РЕШЕНИЯ легли во все три сметы. Запрос сужен радиусом раунда:
            # без него он считал бы решения всей базы и покраснел бы от любой
            # чужой сметы в сцене (§4.2 требует baseline у тестов записи).
            assert dict(decided) == {scene.key14[1]: 3, scene.key15[1]: 3}

            # И, отдельно, обе МАТЕРИАЛИЗАЦИИ. Потерянный пересчёт губит не
            # решение — оно остаётся в `estimate_category_overrides`, — а его
            # след в `position_items`; счёт решений выше этого не увидел бы.
            materialized = check.execute(
                sa.select(PositionItem.position_key_in_proposal, PositionItem.work_category_id, sa.func.count())
                .join(Proposal, Proposal.id == PositionItem.proposal_id)
                .where(Proposal.lot_id.in_(radius),
                       PositionItem.position_key_in_proposal.in_([scene.key14[1], scene.key15[1]]))
                .group_by(PositionItem.position_key_in_proposal, PositionItem.work_category_id)
            ).all()
            assert {(k, c): n for k, c, n in materialized} == {
                (scene.key14[1], scene.category_a): 3,
                (scene.key15[1], scene.category_b): 3,
            }

    def test_locks_go_tender_then_round_then_estimates_ascending(self, scene, monkeypatch):
        """Утверждение о ПОРЯДКЕ потока — по ВСЕЙ цепочке §2.4 п.1, а не по
        одному звену. Один тотальный порядок у всех раундовых писателей — то,
        что спасает от взаимоблокировки параллельных PUT по соседним разделам;
        проверить его по результату нельзя, результат одинаков при любом
        порядке.

        Слушатель курсора записывает все блокирующие запросы сессии, шпион на
        `lock_estimate` — только id смет. Нужны оба: первый ловит перестановку
        звеньев (ревью подняло цикл смет ВЫШЕ замков тендера и раунда, и
        прежняя редакция с одним шпионом осталась зелёной), второй — порядок
        внутри последнего звена.

        Границы теста, обе установлены снятием защиты:
        - он ловит ОБРАТНЫЙ порядок смет, но не отсутствие сортировки как
          таковой: без `ORDER BY` индексный доступ и так обычно отдаёт id по
          возрастанию, и удаление сортировки остаётся зелёным;
        - совпадение «возрастание id = порядок создания» здесь не случайность,
          а следствие `TRUNCATE ... RESTART IDENTITY` перед каждым коммитящим
          тестом. Поэтому тест не отличит `ORDER BY Estimate.id` от любого
          другого порядка, коррелирующего с созданием, — от `Offer.id`,
          например. Он стережёт направление, а не выбор ключа сортировки.
        """
        seen: list[int] = []
        real = rco.lock_estimate

        def spy(db, estimate_id):
            seen.append(estimate_id)
            return real(db, estimate_id)

        monkeypatch.setattr(rco, "lock_estimate", spy)
        with scene.session_factory() as s:
            trace, handle = _lock_trace(s)
            try:
                _put(s, scene, scene.key14, scene.category_a, scene.first_id)
            finally:
                _stop_lock_trace(handle)
            s.rollback()
        assert trace == ["tender", "round", "estimate", "estimate", "estimate"]
        assert seen == sorted(scene.estimate_ids)

    def test_clear_takes_the_same_lock_order_as_put(self, scene, monkeypatch):
        """§2.4 п.1 говорит про ВСЕХ раундовых писателей, а не про одного.
        DELETE делит `_lock_scope` с PUT, поэтому риск мал — но «делит» это
        свойство сегодняшнего кода, а не утверждение теста.
        """
        seen: list[int] = []
        real = rco.lock_estimate

        def spy(db, estimate_id):
            seen.append(estimate_id)
            return real(db, estimate_id)

        monkeypatch.setattr(rco, "lock_estimate", spy)
        with scene.session_factory() as s:
            trace, handle = _lock_trace(s)
            try:
                rco.clear_round_override(
                    s, tender_id=scene.tender_id, round_id=scene.round_id,
                    lot_key=scene.key14[0], position_key_in_proposal=scene.key14[1],
                )
            finally:
                _stop_lock_trace(handle)
            s.rollback()
        assert trace == ["tender", "round", "estimate", "estimate", "estimate"]
        assert seen == sorted(scene.estimate_ids)
