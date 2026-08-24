"""Классификация единиц измерения правилом ставки статьи — проверка по таблице
(спека 2026-08-24-passport-volumes-design.md §2.5, §2.6; план, задача 1).
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from models import UnitOfMeasure
from services.article_rates import NON_SCALABLE_UNIT_CODES, SCALABLE_UNIT_CODES

pytestmark = pytest.mark.integration


def test_every_unit_of_the_reference_is_classified_for_scalability(db_session):
    """DoD 11: список неклассифицированных пуст — по ТАБЛИЦЕ, не по сиду.

    Новая единица в справочнике роняет этот тест, а не получает поведение по
    умолчанию: `is_scalable_unit` реализована исключением (§2.5), поэтому без
    этого теста «Литр-2» молча стал бы масштабируемым.
    """
    codes = set(db_session.execute(sa.select(UnitOfMeasure.code)).scalars().all())
    assert codes == SCALABLE_UNIT_CODES | NON_SCALABLE_UNIT_CODES
    assert len(codes) == 9
