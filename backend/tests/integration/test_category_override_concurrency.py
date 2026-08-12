"""Сериализация ручного разноса статей (спека разноса §2.5,
`services/category_override.py`).

Пересчёт всегда ПОЛНЫЙ: каждый вызов читает ВЕСЬ текущий набор решений сметы и
материализует его целиком в `position_items`. Отсюда риск, которого нет у
точечного `UPDATE`: если второй оператор читает набор решений ДО того, как
первый закоммитил свою правку, он резолвит и материализует поверх набора,
которому не хватает первого решения. Отказа при этом не будет — обе
транзакции успешно закоммитятся; потерянным окажется не решение (оно останется
в `estimate_category_overrides`), а его МАТЕРИАЛИЗАЦИЯ в дереве — то есть
именно то, ради чего пересчёт вообще существует. Поэтому строка сметы
блокируется `FOR UPDATE` на весь вызов (`_lock_estimate`): второй оператор
обязан дождаться коммита первого, прежде чем читать набор решений на пересчёт.

Тест требует НАСТОЯЩИХ транзакций (транзакционная `db_session` — один
savepoint, две параллельные транзакции на ней не выразить), поэтому работает
на `committing_session_factory`, как `test_review_concurrency.py`.

`_wait_until_a_backend_blocks` — точная копия приёма оттуда (опрос
`pg_stat_activity.wait_event_type = 'Lock'`, чтобы дождаться НАСТОЯЩЕЙ
блокировки, а не мерить время сна вслепую), а не импорт: тестовые хелперы в
этом репозитории не делятся между модулями тестов (тем же правилом обошёлся
`run_import` в `test_estimate_import.py` — эквивалент завёлся в
`tests/integration/conftest.py`, а не был вынесен для импорта).

Что чем доказывается: без блокировки второй оператор, не дожидаясь коммита
первого, читает набор решений ДО него и, значит, вообще не встаёт на
ожидание — ассерт `_wait_until_a_backend_blocks` красный ИМЕННО в этом случае
и падает именно по факту отсутствия блокировки, а не по таймауту вслепую. Это
и есть проверка МЕХАНИЗМА — то, что даёт именно `FOR UPDATE`, а не что-либо
другое (обычные замки строк `position_items` тут не спасают: два раздела
намеренно НЕЗАВИСИМЫ, ни один не предок другого, и наборы строк, которые
трогает каждый пересчёт, не пересекаются — конфликта на уровне строки нет, и
если он и обнаружится, то только через блокировку сметы). Финальные проверки
после обоих коммитов (обе строки в `estimate_category_overrides`, обе статьи
материализованы во ВСЁ поддерево своего раздела) фиксируют НАБЛЮДАЕМЫЙ
результат корректного (заблокированного) прогона — документируют, что должно
получиться, а не сам механизм: приди `apply_overrides` к тому же результату
каким-то другим путём (без блокировки, но случайно в правильном порядке),
они бы промолчали. Мерой служит именно ассерт о блокировке.
"""
from __future__ import annotations

import threading
import time

import pytest
import sqlalchemy as sa

from models import EstimateCategoryOverride, Lot, PositionItem, Proposal, UserRole, WorkCategory
from services.category_override import set_override
from services.category_resolution import CATEGORY_SOURCE_MANUAL, CategoryResolver
from services.estimate_import import import_estimate
from services.unit_resolution import UnitResolver
from tests.payloads import payload_for, position

pytestmark = pytest.mark.integration


@pytest.fixture
def scene(committing_db, committing_factories, committing_session_factory):
    """Закоммиченная смета с ДВУМЯ НЕЗАВИСИМЫМИ разделами — ни один не предок
    другого, у каждого своё поддерево (раздел + подраздел + позиция), и ни у
    одного нет своей статьи в файле: обе статьи назначаются исключительно
    решениями, конкурирующими в тесте."""
    contract = committing_factories.ContractFactory.create()
    committing_db.flush()
    resolver = UnitResolver(committing_db)
    outcome = import_estimate(
        committing_db,
        contract=contract,
        amendment_no=None,
        data=payload_for(
            contract,
            [
                position(job_title="Раздел А", is_chapter=True, chapter_number="1"),
                position(job_title="Подраздел А.1", is_chapter=True, chapter_number="1.1"),
                position(
                    job_title="Работа А",
                    unit="м2",
                    quantity=1,
                    suggested_quantity=1,
                    unit_cost_total="100.00",
                    total_cost_total="100.00",
                    chapter_ref="1.1",
                    number="3",
                ),
                position(job_title="Раздел Б", is_chapter=True, chapter_number="2"),
                position(job_title="Подраздел Б.1", is_chapter=True, chapter_number="2.1"),
                position(
                    job_title="Работа Б",
                    unit="м2",
                    quantity=1,
                    suggested_quantity=1,
                    unit_cost_total="100.00",
                    total_cost_total="100.00",
                    chapter_ref="2.1",
                    number="6",
                ),
            ],
        ),
        parser_version="1.0.0",
        import_job_id=None,
        replace=False,
        unit_resolver=resolver,
        category_resolver=CategoryResolver.from_db(committing_db),
    )

    admin_first = committing_factories.UserFactory.create(role=UserRole.admin)
    admin_second = committing_factories.UserFactory.create(role=UserRole.admin)

    # Два ЛИСТА классификатора, заведомо разные — чтобы по итоговой статье
    # раздела было видно, чьё решение до него доехало (родитель упёрся бы в
    # work_categories_parent_id_fkey раньше FK решения, тот же урок Ф3).
    used_as_parent = sa.select(WorkCategory.parent_id).where(WorkCategory.parent_id.is_not(None))
    category_a_id, category_b_id = committing_db.execute(
        sa.select(WorkCategory.id)
        .where(WorkCategory.id.not_in(used_as_parent))
        .order_by(WorkCategory.sort_order)
        .limit(2)
    ).scalars().all()

    committing_db.commit()

    chapters = dict(
        committing_db.execute(
            sa.select(PositionItem.job_title_in_proposal, PositionItem.id)
            .join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == outcome.estimate_id, PositionItem.is_chapter.is_(True))
        ).all()
    )

    class Scene:
        session_factory = committing_session_factory
        estimate_id = outcome.estimate_id
        chapter_a_id = chapters["Раздел А"]
        subchapter_a_id = chapters["Подраздел А.1"]
        chapter_b_id = chapters["Раздел Б"]
        subchapter_b_id = chapters["Подраздел Б.1"]
        category_a = category_a_id
        category_b = category_b_id
        admin_first_id = admin_first.id
        admin_second_id = admin_second.id

    return Scene()


def _wait_until_a_backend_blocks(session_factory, *, timeout: float = 15.0) -> bool:
    """Ждёт, пока какой-нибудь backend этой БД не встанет на ожидание замка.

    Так вторая транзакция гарантированно доходит до точки блокировки до того, как
    первая коммитится, — без произвольных пауз «на глазок». Идентична одноимённому
    хелперу `test_review_concurrency.py` (не импортирована оттуда — см. докстринг
    модуля).
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
            probe.rollback()  # не держим свой снимок
            if blocked:
                return True
            time.sleep(0.05)
    return False


class TestConcurrentDecisions:
    """Мера, которую даёт именно `FOR UPDATE` в `_lock_estimate`.

    Первый оператор назначает статью разделу А и НЕ коммитит. Второй, пока
    первый висит незакоммиченным, пытается назначить статью разделу Б той же
    сметы. Без блокировки второй читает набор решений сразу (первого решения
    там ещё нет) и коммитит, ничего не подождав; с блокировкой он обязан
    дождаться коммита первого и лишь тогда пересчитать — с набором решений,
    в который первое уже входит.
    """

    def test_two_concurrent_decisions_on_the_same_estimate_serialize(self, scene):
        outcome: dict[str, object] = {}
        second_started = threading.Event()

        def second_operator():
            with scene.session_factory() as second:
                second_started.set()
                try:
                    set_override(
                        second,
                        estimate_id=scene.estimate_id,
                        position_item_id=scene.chapter_b_id,
                        work_category_id=scene.category_b,
                        note=None,
                        user_id=scene.admin_second_id,
                    )
                    second.commit()
                    outcome["done"] = True
                except Exception as exc:  # pragma: no cover — диагностика
                    second.rollback()
                    outcome["error"] = f"{type(exc).__name__}: {exc}"

        with scene.session_factory() as first:
            set_override(
                first,
                estimate_id=scene.estimate_id,
                position_item_id=scene.chapter_a_id,
                work_category_id=scene.category_a,
                note=None,
                user_id=scene.admin_first_id,
            )
            thread = threading.Thread(target=second_operator, daemon=True)
            thread.start()
            assert second_started.wait(timeout=10)
            # МЕХАНИЗМ: без FOR UPDATE второй оператор не встаёт на ожидание
            # замка вовсе — этот ассерт красный именно и только в этом случае.
            assert _wait_until_a_backend_blocks(scene.session_factory), (
                "второй оператор не встал на ожидание замка — смета не блокируется"
            )
            first.commit()

        thread.join(timeout=20)
        assert not thread.is_alive()
        assert outcome == {"done": True}, outcome

        with scene.session_factory() as check:
            overrides = dict(
                check.execute(
                    sa.select(
                        EstimateCategoryOverride.position_item_id,
                        EstimateCategoryOverride.work_category_id,
                    )
                ).all()
            )
            assert overrides == {
                scene.chapter_a_id: scene.category_a,
                scene.chapter_b_id: scene.category_b,
            }

            rows = {
                row.id: (row.work_category_id, row.category_source)
                for row in check.execute(
                    sa.select(
                        PositionItem.id, PositionItem.work_category_id, PositionItem.category_source
                    ).where(
                        PositionItem.id.in_(
                            [
                                scene.chapter_a_id,
                                scene.subchapter_a_id,
                                scene.chapter_b_id,
                                scene.subchapter_b_id,
                            ]
                        )
                    )
                ).all()
            }
            # Наблюдаемый результат: ОБА поддерева материализованы своей
            # статьёй — не только сам раздел, но и его подраздел (спека §2.4,
            # наследование).
            assert rows[scene.chapter_a_id] == (scene.category_a, CATEGORY_SOURCE_MANUAL)
            assert rows[scene.subchapter_a_id] == (scene.category_a, CATEGORY_SOURCE_MANUAL)
            assert rows[scene.chapter_b_id] == (scene.category_b, CATEGORY_SOURCE_MANUAL)
            assert rows[scene.subchapter_b_id] == (scene.category_b, CATEGORY_SOURCE_MANUAL)
