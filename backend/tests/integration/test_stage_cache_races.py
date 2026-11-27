"""Чередования и грязная транзакция кэша сцен (спека `2026-11-27-stage-cache-design.md` §2, §5, §7 п. 6, 6а;
дизайн, критерий 4).

Сборки и коммиты идут в разных потоках, у каждого своя сессия `committing_session_factory` с настоящими
коммитами. Порядок событий задают `threading.Event`: подмены `_load_head`, `_build_scene` и `invalidate`
вызывают оригинал и встают на паузу ПОСЛЕ него. Событие перед запросом выровняло бы только момент отправки,
а не снимок данных. Каждое ожидание ограничено по времени и роняет тест понятным текстом: сломанное
предусловие не должно выглядеть продуктовым падением. «Сборка без кэша» — оба кэша (сцен и ставок) на нуле.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

import crud.our_price as our_price
from config import settings
from crud.lump_sum import typical_rates
from crud.our_price import _scene_for, build_round_view
from models import KpLineTarget, ObjectModel, PositionItem, UserRole, WorkCategory
from services.stage_cache import is_dirty, stage_scene_cache
from services.status_cache import typical_rates_cache
from tests.conftest import _truncate_domain_tables
from tests.integration.test_our_price_load import World
from tests.integration.test_our_price_round_api import Stage
from tests.integration.test_stage_cache_api import Caches

pytestmark = pytest.mark.integration

D = Decimal

#: Предел любого ожидания события или потока, секунды.
WAIT_S = 10
NEW_AREA = D("2500")
CONTRACT_PRICE_RAISED = D("1000")


# ---------------------------------------------------------------------------
#  Потоки и паузы
# ---------------------------------------------------------------------------


class Gate:
    """Пауза рабочего потока: он встаёт (`reached`) и ждёт `release()` от теста."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.reached = threading.Event()
        self.go = threading.Event()

    def pause(self) -> None:
        self.reached.set()
        if not self.go.wait(WAIT_S):
            raise AssertionError(f"{self.name}: тест не отпустил поток с паузы")

    def wait_reached(self, message: str) -> None:
        if not self.reached.wait(WAIT_S):
            pytest.fail(message)

    def release(self) -> None:
        self.go.set()


class Worker(threading.Thread):
    """Поток со своей сессией; исключение потока всплывает в `finish`."""

    def __init__(self, name: str, factory, work) -> None:
        super().__init__(name=name, daemon=True)
        self._factory = factory
        self._work = work
        self.result = None
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            with self._factory() as session:
                self.result = self._work(session)
        except BaseException as exc:  # noqa: BLE001 — передаётся в тест
            self.error = exc

    def finish(self):
        self.join(WAIT_S)
        if self.is_alive():
            pytest.fail(f"поток {self.name} не завершился за {WAIT_S} с")
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def gates():
    """Все паузы теста отпускаются в конце, чтобы упавший тест не оставил висящие потоки."""
    created: list[Gate] = []

    def make(name: str) -> Gate:
        gate = Gate(name)
        created.append(gate)
        return gate

    yield make
    for gate in created:
        gate.release()


def _in_thread(name: str) -> bool:
    return threading.current_thread().name == name


@pytest.fixture
def caches(monkeypatch):
    return Caches(monkeypatch)


@pytest.fixture
def builds(monkeypatch):
    """Счётчик холодных сборок: шпион над `_build_scene` (для всех потоков)."""
    box = SimpleNamespace(n=0)
    real = our_price._build_scene

    def spy(*args, **kwargs):
        box.n += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(our_price, "_build_scene", spy)
    return box


@contextmanager
def uncached(monkeypatch):
    """Оба кэша выключены, но записанное в них не стирается: сборка «без кэша» посреди теста."""
    saved = (settings.STAGE_SCENE_CACHE_TTL_S, settings.TYPICAL_RATES_CACHE_TTL_S)
    monkeypatch.setattr(settings, "STAGE_SCENE_CACHE_TTL_S", 0)
    monkeypatch.setattr(settings, "TYPICAL_RATES_CACHE_TTL_S", 0)
    try:
        yield
    finally:
        monkeypatch.setattr(settings, "STAGE_SCENE_CACHE_TTL_S", saved[0])
        monkeypatch.setattr(settings, "TYPICAL_RATES_CACHE_TTL_S", saved[1])


# ---------------------------------------------------------------------------
#  Сцена с настоящими коммитами: три строки договора по 100 и строка КП за 1000 с весом 1
# ---------------------------------------------------------------------------


@dataclass
class Fixture:
    tender_id: int
    round_id: int
    offer_id: int
    estimate_id: int
    kp_item_id: int
    object_id: int
    contract_item_ids: list[int]
    admin_id: int

    @property
    def stage_key(self):
        return (self.tender_id, self.round_id, None, None)


_STAGE_CATEGORY_CODES = ("8801", "8801.1", "8801.1.1", "8802")


def _drop_stage_categories(engine) -> None:
    with engine.begin() as conn:
        conn.execute(sa.delete(WorkCategory).where(WorkCategory.code.in_(_STAGE_CATEGORY_CODES)))


@pytest.fixture
def stage_categories_cleanup(db_engine, committing_session_factory):
    """`Stage` заводит статьи справочника, а чистка доменных таблиц справочник не трогает: без этого
    следующий тест (в том числе в других файлах) упрётся в уникальный код статьи."""
    _drop_stage_categories(db_engine)
    yield
    _truncate_domain_tables(db_engine)
    _drop_stage_categories(db_engine)


@pytest.fixture
def fx(committing_db, committing_factories, stage_categories_cleanup) -> Fixture:
    f = committing_factories
    admin = f.UserFactory.create(role=UserRole.admin)
    world = World(committing_db, f, admin)
    stage = Stage(world, committing_db, f)
    rnd = stage.round()
    part = stage.participant(rnd, "Подрядчик А")
    kp = stage.kp(part, D("1000"), qty=D("1"))  # вес 1: только такая строка бывает комплектом
    contract_items: list[int] = []
    for _ in range(3):
        _, _, proposal = stage.history(D("100"))
        contract_items.extend(
            committing_db.execute(sa.select(PositionItem.id).where(PositionItem.proposal_id == proposal.id)).scalars()
        )
    committing_db.commit()
    result = Fixture(
        tender_id=stage.tender.id,
        round_id=rnd.id,
        offer_id=part.offer.id,
        estimate_id=part.estimate.id,
        kp_item_id=kp.id,
        object_id=stage.tender.object_id,
        contract_item_ids=contract_items,
        admin_id=admin.id,
    )
    committing_db.close()
    stage_scene_cache.invalidate()
    typical_rates_cache.invalidate()
    return result


def _raise_contract_prices(session, fx: Fixture) -> None:
    """Запись вне закрытого списка, меняющая обычные ставки: цена строк договора 100 -> 1000."""
    session.execute(
        sa.update(PositionItem)
        .where(PositionItem.id.in_(fx.contract_item_ids))
        .values(unit_cost_total=CONTRACT_PRICE_RAISED)
    )


def _change_area(session, fx: Fixture) -> None:
    session.execute(sa.update(ObjectModel).where(ObjectModel.id == fx.object_id).values(area_aboveground_sp=NEW_AREA))


def _scene(session, fx: Fixture):
    return _scene_for(session, fx.tender_id, fx.round_id, None, inflation_series_id=None)


def _view(session, fx: Fixture) -> dict:
    return build_round_view(session, fx.tender_id, fx.round_id, inflation_series_id=None)


def lump_flags(scene) -> dict[int, bool]:
    return {
        row.line.position_item_id: bool(row.line.lump is not None and row.line.lump.is_lump)
        for rows in scene.kp_lines.values()
        for row in rows
    }


def _fresh(factory, fn):
    with factory() as session:
        return fn(session)


def _rates_cache_is_empty() -> bool:
    """Пуст ли кэш ставок: пробный вызов считает сам; пробное значение сразу стирается."""
    marker = object()
    try:
        return typical_rates_cache.get(lambda: marker) is marker
    finally:
        typical_rates_cache.invalidate()


def test_races_fixture_scene_is_a_lump_until_the_contract_prices_rise(committing_session_factory, fx, caches) -> None:
    """Предусловие всех тестов: строка КП — комплект при ставке 100 и перестаёт им быть при ставке 1000."""
    caches.off()
    assert lump_flags(_fresh(committing_session_factory, lambda s: _scene(s, fx))) == {fx.kp_item_id: True}
    with committing_session_factory() as s:
        _raise_contract_prices(s, fx)
        s.commit()
    assert lump_flags(_fresh(committing_session_factory, lambda s: _scene(s, fx))) == {fx.kp_item_id: False}


# ---------------------------------------------------------------------------
#  Чередования потоков
# ---------------------------------------------------------------------------


def test_build_that_overlaps_a_commit_outside_the_list_is_not_stored(
    committing_session_factory, fx, caches, builds, gates, monkeypatch
) -> None:
    caches.on()
    paused = gates("после _build_scene")
    real = our_price._build_scene

    def build_then_pause(*args, **kwargs):
        scene = real(*args, **kwargs)
        if _in_thread("assembler"):
            paused.pause()
        return scene

    monkeypatch.setattr(our_price, "_build_scene", build_then_pause)
    worker = Worker("assembler", committing_session_factory, lambda s: _scene(s, fx))
    worker.start()
    paused.wait_reached("поток сборки не дошёл до паузы после _build_scene")
    with committing_session_factory() as other:
        _change_area(other, fx)
        other.commit()
    paused.release()
    worker.finish()

    assert stage_scene_cache.get(fx.stage_key) is None, "сборка, пересёкшая коммит вне списка, сохранена"
    before = builds.n
    _fresh(committing_session_factory, lambda s: _scene(s, fx))
    assert builds.n == before + 1


def test_pause_after_the_head_while_another_thread_changes_the_area_gives_the_uncached_answer(
    committing_session_factory, fx, caches, monkeypatch, gates
) -> None:
    caches.on()
    paused = gates("после _load_head")
    real = our_price._load_head

    def head_then_pause(*args, **kwargs):
        head = real(*args, **kwargs)
        if _in_thread("assembler"):
            paused.pause()
        return head

    monkeypatch.setattr(our_price, "_load_head", head_then_pause)
    worker = Worker("assembler", committing_session_factory, lambda s: _view(s, fx))
    worker.start()
    paused.wait_reached("поток сборки не дошёл до паузы после _load_head")
    with committing_session_factory() as other:
        _change_area(other, fx)
        other.commit()
    paused.release()
    stale = worker.finish()

    cached = _fresh(committing_session_factory, lambda s: _view(s, fx))
    caches.off()
    uncached_view = _fresh(committing_session_factory, lambda s: _view(s, fx))

    assert stale != uncached_view, "площадь не отразилась в ответе: тест пуст"
    assert cached == uncached_view


def test_commit_that_changes_typical_rates_without_the_middleware_gives_the_uncached_classification(
    committing_session_factory, fx, caches, monkeypatch
) -> None:
    caches.on()
    warm = _fresh(committing_session_factory, lambda s: _scene(s, fx))
    assert lump_flags(warm) == {fx.kp_item_id: True}
    _fresh(committing_session_factory, lambda s: typical_rates(s))  # кэш ставок прогрет перед коммитом

    with committing_session_factory() as writer:  # обычная сессия: прослойки `main.py` здесь нет
        _raise_contract_prices(writer, fx)
        writer.commit()

    right_after = _fresh(committing_session_factory, lambda s: _scene(s, fx))
    caches.off()
    expected = _fresh(committing_session_factory, lambda s: _scene(s, fx))

    assert lump_flags(expected) == {fx.kp_item_id: False}
    assert lump_flags(right_after) == lump_flags(expected)


def test_pause_between_the_rates_reset_and_the_scene_reset_does_not_store_the_overlapping_build(
    committing_session_factory, fx, caches, monkeypatch, gates
) -> None:
    caches.on()
    _fresh(committing_session_factory, lambda s: typical_rates(s))  # кэш ставок тёплый: ставка 100
    order: list[str] = []
    between = gates("между сбросами")
    built = gates("после _build_scene")

    def wrap_invalidate(name: str, cache):
        real = cache.invalidate

        def invalidate_then_pause():
            real()
            order.append(name)
            if _in_thread("committer") and len(order) == 1:
                between.pause()

        monkeypatch.setattr(cache, "invalidate", invalidate_then_pause)

    wrap_invalidate("rates", typical_rates_cache)
    wrap_invalidate("scene", stage_scene_cache)
    real_build = our_price._build_scene

    def build_then_pause(*args, **kwargs):
        scene = real_build(*args, **kwargs)
        if _in_thread("assembler"):
            built.pause()
        return scene

    monkeypatch.setattr(our_price, "_build_scene", build_then_pause)

    def commit_prices(session):
        _raise_contract_prices(session, fx)
        session.commit()

    committer = Worker("committer", committing_session_factory, commit_prices)
    committer.start()
    between.wait_reached("поток коммита не дошёл до паузы между двумя сбросами")
    assembler = Worker("assembler", committing_session_factory, lambda s: _scene(s, fx))
    assembler.start()
    built.wait_reached("поток сборки не дошёл до паузы после _build_scene")
    between.release()
    committer.finish()
    built.release()
    assembler.finish()

    assert order == ["rates", "scene"], "порядок сбросов нарушен: ставки обязаны сбрасываться раньше сцен"
    assert stage_scene_cache.get(fx.stage_key) is None, "сборка, начатая между сбросами, сохранена"
    again = _fresh(committing_session_factory, lambda s: _scene(s, fx))
    caches.off()
    expected = _fresh(committing_session_factory, lambda s: _scene(s, fx))
    assert lump_flags(again) == lump_flags(expected) == {fx.kp_item_id: False}


# ---------------------------------------------------------------------------
#  Грязная транзакция (спека §5, критерии 6 и 6а)
# ---------------------------------------------------------------------------


def test_dirty_transaction_neither_reads_nor_stores_the_scene_and_the_next_answer_is_uncached(
    committing_session_factory, fx, caches, builds
) -> None:
    caches.on()
    warmed = _fresh(committing_session_factory, lambda s: _scene(s, fx))
    assert stage_scene_cache.get(fx.stage_key) is warmed
    old_area = warmed.head.area
    before = builds.n

    with committing_session_factory() as session:
        _change_area(session, fx)
        assert is_dirty(session)
        own = _scene(session, fx)
        assert builds.n == before + 1, "грязная транзакция получила сцену из кэша"
        assert own.head.area != old_area, "грязная транзакция не увидела собственную запись"
        assert stage_scene_cache.get(fx.stage_key) is warmed, "сцена грязной транзакции попала в кэш"
        session.rollback()

    answer = _fresh(committing_session_factory, lambda s: _view(s, fx))
    caches.off()
    assert answer == _fresh(committing_session_factory, lambda s: _view(s, fx))


def test_dirty_build_into_an_empty_scene_cache_is_not_stored_and_the_next_answer_is_uncached(
    committing_session_factory, fx, caches
) -> None:
    caches.on()
    with committing_session_factory() as session:
        _change_area(session, fx)
        own = _scene(session, fx)
        assert own.head.area == NEW_AREA
        assert stage_scene_cache.get(fx.stage_key) is None, "сцена грязной транзакции сохранена"
        session.rollback()

    answer = _fresh(committing_session_factory, lambda s: _view(s, fx))
    caches.off()
    assert answer == _fresh(committing_session_factory, lambda s: _view(s, fx))


def test_dirty_transaction_with_a_warm_rates_cache_classifies_by_its_own_data(
    committing_session_factory, fx, caches, monkeypatch
) -> None:
    caches.on()
    _fresh(committing_session_factory, lambda s: typical_rates(s))  # ставки тёплые: 100, строка КП — комплект

    with committing_session_factory() as session:
        _raise_contract_prices(session, fx)
        assert is_dirty(session)
        own = lump_flags(_scene(session, fx))
        with uncached(monkeypatch):
            expected = lump_flags(_scene(session, fx))
        session.rollback()

    assert expected == {fx.kp_item_id: False}, "своя запись не изменила классификацию: тест пуст"
    assert own == expected, "грязная транзакция взяла устаревшие ставки из тёплого кэша"
    clean = _fresh(committing_session_factory, lambda s: lump_flags(_scene(s, fx)))
    assert clean == {fx.kp_item_id: True}, "откат оставил в кэше ставок чужие данные"


def test_dirty_build_with_an_empty_rates_cache_leaves_it_empty_and_the_next_build_is_uncached(
    committing_session_factory, fx, caches, monkeypatch
) -> None:
    caches.on()
    assert _rates_cache_is_empty()

    with committing_session_factory() as session:
        _raise_contract_prices(session, fx)
        assert lump_flags(_scene(session, fx)) == {fx.kp_item_id: False}
        assert _rates_cache_is_empty(), "грязная сборка заполнила кэш ставок своими данными"
        session.rollback()

    after = _fresh(committing_session_factory, lambda s: lump_flags(_scene(s, fx)))
    with uncached(monkeypatch):
        expected = _fresh(committing_session_factory, lambda s: lump_flags(_scene(s, fx)))
    assert expected == {fx.kp_item_id: True}
    assert after == expected


def test_transaction_that_wrote_only_a_list_table_is_not_dirty_and_rereads_its_own_target(
    committing_session_factory, fx, caches, builds
) -> None:
    caches.on()
    _fresh(committing_session_factory, lambda s: _scene(s, fx))
    before = builds.n

    with committing_session_factory() as session:
        session.add(KpLineTarget(position_item_id=fx.kp_item_id, kind="freshest", set_by=fx.admin_id))
        session.flush()
        assert not is_dirty(session)
        own = _scene(session, fx)
        assert builds.n == before, "транзакция, записавшая только цель строки, ушла в сборку"
        assert fx.kp_item_id in own.decisions[fx.estimate_id].targets
        session.rollback()

    assert fx.kp_item_id not in _fresh(committing_session_factory, lambda s: _scene(s, fx)).decisions[fx.estimate_id].targets
