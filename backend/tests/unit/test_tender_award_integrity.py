"""Переводчик нарушений ключей: значение словаря — текст или пара (код, текст).

Без базы: `IntegrityError` с подставным `orig`, сессия — подделка, считающая откаты.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from crud.common import DomainError, translating_integrity


class _FakeSession:
    def __init__(self) -> None:
        self.rollbacks = 0

    def rollback(self) -> None:
        self.rollbacks += 1


def _violation(constraint: str) -> IntegrityError:
    return IntegrityError("INSERT ...", {}, Exception(f'violates constraint "{constraint}"'))


def test_plain_string_gives_409_without_code():
    db = _FakeSession()
    with (
        pytest.raises(DomainError) as info,
        translating_integrity(db, {"uq_known": "Уже занято."}),
    ):
        raise _violation("uq_known")
    assert info.value.status_code == 409
    assert info.value.detail == "Уже занято."
    assert info.value.code is None
    assert db.rollbacks == 1


def test_pair_gives_409_with_code_and_text():
    db = _FakeSession()
    with (
        pytest.raises(DomainError) as info,
        translating_integrity(db, {"uq_known": ("some_code", "Текст отказа.")}),
    ):
        raise _violation("uq_known")
    assert info.value.status_code == 409
    assert info.value.detail == "Текст отказа."
    assert info.value.code == "some_code"
    assert db.rollbacks == 1


def test_pair_and_string_in_one_mapping_do_not_mix():
    db = _FakeSession()
    messages = {"uq_plain": "Простой.", "uq_coded": ("coded", "С кодом.")}
    with (
        pytest.raises(DomainError) as plain,
        translating_integrity(db, messages),
    ):
        raise _violation("uq_plain")
    with (
        pytest.raises(DomainError) as coded,
        translating_integrity(db, messages),
    ):
        raise _violation("uq_coded")
    assert (plain.value.code, plain.value.detail) == (None, "Простой.")
    assert (coded.value.code, coded.value.detail) == ("coded", "С кодом.")


def test_two_character_string_is_text_not_pair():
    # Пара опознаётся по типу, а не по длине: строка из двух символов
    # распаковалась бы в (код, текст) по одному символу.
    db = _FakeSession()
    with (
        pytest.raises(DomainError) as info,
        translating_integrity(db, {"uq_known": "Ок"}),
    ):
        raise _violation("uq_known")
    assert (info.value.code, info.value.detail) == (None, "Ок")
    assert db.rollbacks == 1


def test_foreign_constraint_is_reraised_as_integrity_error():
    db = _FakeSession()
    with (
        pytest.raises(IntegrityError),
        translating_integrity(db, {"uq_known": ("some_code", "Текст.")}),
    ):
        raise _violation("uq_other")
    assert db.rollbacks == 1


def test_no_violation_does_not_roll_back():
    db = _FakeSession()
    with translating_integrity(db, {"uq_known": ("some_code", "Текст.")}):
        pass
    assert db.rollbacks == 0
