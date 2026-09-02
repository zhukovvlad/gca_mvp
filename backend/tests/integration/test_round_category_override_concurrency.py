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
и `_wait_until_a_backend_blocks` краснеет именно по факту отсутствия
блокировки, а не по слепому таймауту. Проверки после обоих коммитов
фиксируют наблюдаемый результат правильного прогона, а не механизм: приди
пересчёт к тому же результату случайно, они бы промолчали. Мерой служит ассерт
о блокировке.

Предпосылка этого красного — блокировки тендера и раунда ВЫШЕ цикла по сметам
берутся настоящим `FOR KEY SHARE` (`with_for_update(read=True,
key_share=True)`), совместимым сам с собой. Одно `key_share=True` дало бы
`FOR NO KEY UPDATE`, несовместимый сам с собой: второй писатель ждал бы уже на
строке тендера, снятие цикла ничего бы не уронило, и зелёный означал бы
«держит какой-то другой замок», а не «держит наш» (находка внешнего ревью
плана 01.09.2026; `docs/insights/verifying-guards.md`, слой 8). Компиляция
сверена оркестратором перед негативной проверкой:
`with_for_update(read=True, key_share=True)` → `FOR KEY SHARE`,
`with_for_update(key_share=True)` → `FOR NO KEY UPDATE`.

Порядок захвата стережёт ШПИОН на `lock_estimate` — утверждение о ПОТОКЕ
данных, а не о результате (`docs/insights/data-flow-assertions-for-order.md`):
результат один при любом порядке, различить порядок по нему нельзя.

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


def _wait_until_a_backend_blocks(session_factory, *, timeout: float = 15.0) -> bool:
    """Ждёт, пока какой-нибудь backend этой БД не встанет на ожидание замка.

    Так вторая транзакция гарантированно доходит до точки блокировки до того,
    как первая коммитится, — без произвольных пауз «на глазок».
    """
    deadline = time.monotonic() + timeout
    with session_factory() as probe:
        while time.monotonic() < deadline:
            blocked = probe.execute(
                sa.text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            ).scalar_one()
            probe.rollback()
            if blocked:
                return True
            time.sleep(0.05)
    return False


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
            with scene.session_factory() as s:
                started.set()
                try:
                    _put(s, scene, scene.key15, scene.category_b, scene.second_id)
                    s.commit()
                    outcome["done"] = True
                except Exception as exc:  # pragma: no cover — диагностика падения потока
                    s.rollback()
                    outcome["error"] = f"{type(exc).__name__}: {exc}"

        with scene.session_factory() as first:
            _put(first, scene, scene.key14, scene.category_a, scene.first_id)
            t = threading.Thread(target=second_writer, daemon=True)
            t.start()
            assert started.wait(timeout=10)
            # МЕХАНИЗМ: без FOR UPDATE по сметам второй писатель не встаёт на
            # замок вовсе — разделы независимы, конфликта уровня строки нет.
            assert _wait_until_a_backend_blocks(scene.session_factory), "второй писатель не ждёт первого"
            first.commit()
        t.join(timeout=20)
        assert not t.is_alive() and outcome == {"done": True}, outcome

        with scene.session_factory() as check:
            decided = check.execute(
                sa.select(PositionItem.position_key_in_proposal, sa.func.count())
                .join(EstimateCategoryOverride, EstimateCategoryOverride.position_item_id == PositionItem.id)
                .group_by(PositionItem.position_key_in_proposal)
            ).all()
            # Оба решения легли во ВСЕ три сметы: ни один пересчёт не затёр чужое.
            assert dict(decided) == {scene.key14[1]: 3, scene.key15[1]: 3}

    def test_estimates_are_locked_in_ascending_id_order(self, scene, monkeypatch):
        """Утверждение о ПОРЯДКЕ потока: шпион на `lock_estimate` записывает id
        в порядке захвата. Один тотальный порядок у обоих писателей — то, что
        спасает от взаимоблокировки параллельных PUT по соседним разделам
        (§2.4 п.1); проверить его по результату нельзя, результат одинаков при
        любом порядке.

        Граница теста: он ловит ОБРАТНЫЙ порядок, но не отсутствие сортировки
        как таковой — без `ORDER BY` индексный доступ обычно и так отдаёт id по
        возрастанию, и снятие сортировки осталось бы зелёным. Проверено
        оркестратором снятием защиты: `.order_by(Estimate.id.desc())` красит
        этот тест, простое удаление `order_by` — нет.
        """
        seen: list[int] = []
        real = rco.lock_estimate

        def spy(db, estimate_id):
            seen.append(estimate_id)
            return real(db, estimate_id)

        monkeypatch.setattr(rco, "lock_estimate", spy)
        with scene.session_factory() as s:
            _put(s, scene, scene.key14, scene.category_a, scene.first_id)
            s.rollback()
        assert seen == sorted(scene.estimate_ids)
        assert len(seen) == 3
