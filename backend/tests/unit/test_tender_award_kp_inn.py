"""ИНН КП, снимаемый с разбора при отметке победителя (спека Б2 §2.4, §1.7).

Без базы: чистая функция над JSON проекции участника.
"""

from __future__ import annotations

from parser.constants import JSON_KEY_CONTRACTOR_INN, JSON_KEY_LOTS, JSON_KEY_PROPOSALS
from services.round_import import kp_inn_of
from tests.payloads import position, proposal, round_payload


def _payload(*, lots: int, inn: str = "7700000001") -> dict:
    block = proposal(
        [position(job_title="Работа", unit="м2", unit_cost_total="10", total_cost_total="10")],
        title="ООО А", inn=inn,
    )
    return round_payload([block], lots=lots)


def _set_inn(payload: dict, lot_key: str, value) -> None:
    for block in payload[JSON_KEY_LOTS][lot_key][JSON_KEY_PROPOSALS].values():
        block[JSON_KEY_CONTRACTOR_INN] = value


def test_one_inn_written_differently_in_two_lots_is_one_value():
    payload = _payload(lots=2)
    _set_inn(payload, "lot_1", "77 0000 0001")
    _set_inn(payload, "lot_2", "7700000001")
    assert kp_inn_of(payload) == "7700000001"


def test_two_different_inns_give_none():
    payload = _payload(lots=2)
    _set_inn(payload, "lot_1", "7700000001")
    _set_inn(payload, "lot_2", "7700000002")
    assert kp_inn_of(payload) is None


def test_no_inn_at_all_gives_none():
    payload = _payload(lots=2)
    _set_inn(payload, "lot_1", "")
    _set_inn(payload, "lot_2", None)
    assert kp_inn_of(payload) is None


def test_one_empty_and_one_filled_inn_gives_the_filled_one():
    payload = _payload(lots=2)
    _set_inn(payload, "lot_1", "")
    _set_inn(payload, "lot_2", "7700000001")
    assert kp_inn_of(payload) == "7700000001"


def test_payload_without_lots_gives_none():
    assert kp_inn_of({}) is None
    assert kp_inn_of({JSON_KEY_LOTS: {}}) is None
