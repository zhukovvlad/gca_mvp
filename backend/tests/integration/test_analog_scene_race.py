"""Сметы истории выбираются один раз (ревью Codex к PR лестницы аналогов, P1).

`load_history` выбирает последнюю смету договора и её период; строки работ обязаны браться
из ТЕХ ЖЕ смет. Допсоглашение, закоммиченное между двумя чтениями, не должно подменить
строки под старым периодом и коэффициентом ИПЦ. Барьер стоит ПОСЛЕ чтения `load_history`:
обёртка коммитит допсоглашение другим соединением, когда выбор смет уже сделан.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud import our_price
from crud.our_price import build_round_view
from crud.our_price_load import load_work_history
from models import UserRole, WorkCategory
from tests.integration.test_analog_scene import _estimate_of, _hist, _line
from tests.integration.test_our_price_load import World
from tests.integration.test_our_price_round_api import Stage

pytestmark = pytest.mark.integration

D = Decimal


@pytest.fixture
def own_categories_cleaned(db_engine):
    """Статьи сцены (`Stage`) лежат в справочнике, который чистка committing-тестов не трогает:
    убираются до теста и после него. Фикстура запрашивается раньше сессий, поэтому её
    завершающая часть идёт после очистки доменных таблиц, снявшей ссылки на статьи."""

    def _clean():
        with db_engine.begin() as conn:
            conn.execute(sa.delete(WorkCategory).where(WorkCategory.code.in_(("8801", "8801.1", "8801.1.1", "8802"))))

    _clean()
    yield
    _clean()


@pytest.fixture
def committed(own_categories_cleaned, committing_db, committing_factories):
    admin = committing_factories.UserFactory.create(role=UserRole.admin)
    world = World(committing_db, committing_factories, admin)
    stage = Stage(world, committing_db, committing_factories)
    chain = world.chain(("Раздел А",), "whole")
    contract, base, old = _hist(stage, D("100"), chain=chain)
    rnd = stage.round()
    committing_db.commit()
    return stage, world, chain, contract, old, rnd


def _add_amendment(stage, world, chain, contract):
    _, proposal = world.estimate(contract, amendment_no=1)
    item = _line(stage, proposal, D("999"), chain=chain)
    stage.db.commit()
    return item


class TestLoaderTakesTheGivenEstimates:
    def test_the_given_old_estimate_yields_its_lines_while_a_newer_one_exists(self, committed, committing_session_factory):
        stage, world, chain, contract, old, _ = committed
        with committing_session_factory() as reader:
            given = _estimate_of(reader, stage)
        new = _add_amendment(stage, world, chain, contract)

        with committing_session_factory() as reader:
            lines, _ = load_work_history(reader, estimate_of=given)

        assert {line.position_item_id for line in lines} == {old.id}
        assert new.id not in {line.position_item_id for line in lines}
        assert {line.contract_id for line in lines} == {contract.id}


class TestSceneIsOneSnapshotOfEstimates:
    def test_an_amendment_committed_after_load_history_does_not_replace_the_lines(
        self, committed, committing_session_factory, monkeypatch
    ):
        stage, world, chain, contract, old, rnd = committed
        created: list = []
        real_history = our_price.load_history
        real_work = our_price.load_work_history
        seen: dict = {}

        def history_then_amendment(*args, **kwargs):
            result = real_history(*args, **kwargs)
            created.append(_add_amendment(stage, world, chain, contract))
            return result

        def spy(*args, **kwargs):
            result = real_work(*args, **kwargs)
            seen["lines"] = result[0]
            return result

        monkeypatch.setattr(our_price, "load_history", history_then_amendment)
        monkeypatch.setattr(our_price, "load_work_history", spy)

        with committing_session_factory() as db:
            build_round_view(db, stage.tender.id, rnd.id, inflation_series_id=None)

        (new,) = created
        assert {line.position_item_id for line in seen["lines"]} == {old.id}
        assert new.id not in {line.position_item_id for line in seen["lines"]}
