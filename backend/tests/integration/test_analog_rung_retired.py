"""Ручной ступени работы больше нет (миграция 0024; спека §2.17, Task 1).

`load_decisions` не несёт ручных ступеней работ (у `saved` нет `work`) и делает три `SELECT`; `PUT …/rung` у строки-работы —
422 `rung_not_applicable`, у системной строки — как в 4б; `DELETE …/rung` у работы — 204 без
действия. Подробные тесты маршрутов — `test_kp_file_mutations.py` и `test_kp_file_scene.py`;
здесь — заявления брифа задачи, по одному входу на каждое.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud import kp_decisions as kd
from crud.kp_decisions import CODE_RUNG_NOT_APPLICABLE
from models import KpFamilyRung
from tests.integration.test_kp_file_scene import _agree, _comment, _family_rung, _system_scene, _user, _work_scene
from tests.integration.test_our_price_load import World
from tests.integration.test_our_price_round_api import Stage, count_statements

pytestmark = pytest.mark.integration

D = Decimal


@pytest.fixture
def world(db_session, factories, admin_user):
    return World(db_session, factories, admin_user)


@pytest.fixture
def stage(world, db_session, factories):
    return Stage(world, db_session, factories)


@pytest.fixture
def admin_client(admin_client, db_session):
    """Закрепляет сцену перед каждым запросом: `rollback` роутера откатывает транзакцию
    сессии, а в проде сцена к запросу уже закоммичена."""
    send = admin_client.request

    def request(*args, **kwargs):
        db_session.commit()
        return send(*args, **kwargs)

    admin_client.request = request
    return admin_client


def _rung_url(stage, rnd, part, item) -> str:
    return (
        f"/api/v1/tenders/{stage.tender.id}/rounds/{rnd.id}"
        f"/our-price/offers/{part.offer.id}/lines/{item.id}/rung"
    )


def _family_rung_rows(db, estimate):
    return db.execute(
        sa.select(KpFamilyRung.work_family_id, KpFamilyRung.level, KpFamilyRung.scope).where(
            KpFamilyRung.estimate_id == estimate.id
        )
    ).all()


def _code(response) -> str:
    return response.json()["detail"]["code"]


def test_the_code_constant():
    assert CODE_RUNG_NOT_APPLICABLE == "rung_not_applicable"


def test_load_decisions_gives_an_empty_work_map_next_to_other_decisions(stage, db_session, factories):
    rnd, part, item = _work_scene(stage)
    user = _user(factories)
    _agree(db_session, item, D("90"), user)
    _comment(db_session, item, "Текст", user)
    _family_rung(db_session, part.estimate, stage.family, "f", "all", user)

    found = kd.load_decisions(db_session, [part.estimate.id])[part.estimate.id]

    assert not hasattr(found.saved, "work")
    assert set(found.saved.family) == {stage.family.id}
    assert set(found.agreements) == {item.id} and set(found.comments) == {item.id}


def test_load_decisions_makes_three_selects(stage, db_session):
    _, part, _ = _work_scene(stage)
    estimate_ids = [part.estimate.id]

    with count_statements(db_session) as seen:
        kd.load_decisions(db_session, estimate_ids)

    assert len(seen) == 3


def test_put_rung_on_a_work_line_is_422_rung_not_applicable_and_writes_nothing(
    admin_client, stage, db_session
):
    rnd, part, item = _work_scene(stage)

    response = admin_client.put(_rung_url(stage, rnd, part, item), json={"level": "v", "scope": "cls"})

    assert (response.status_code, _code(response)) == (422, "rung_not_applicable")
    assert _family_rung_rows(db_session, part.estimate) == []


def test_put_rung_on_a_system_line_is_as_in_4b(admin_client, stage, db_session):
    rnd, first, _second, item, _other = _system_scene(stage)

    response = admin_client.put(_rung_url(stage, rnd, first, item), json={"level": "f", "scope": "all"})

    assert response.status_code == 204
    assert _family_rung_rows(db_session, first.estimate) == [(stage.family.id, "f", "all")]


def test_delete_rung_on_a_work_line_is_204_without_action(admin_client, stage, db_session, factories):
    rnd, part, item = _work_scene(stage)
    _family_rung(db_session, part.estimate, stage.family, "f", "all", _user(factories))

    response = admin_client.delete(_rung_url(stage, rnd, part, item))

    assert response.status_code == 204
    assert _family_rung_rows(db_session, part.estimate) == [(stage.family.id, "f", "all")]
