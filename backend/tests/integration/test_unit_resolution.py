"""Разрешение единиц измерения при импорте и матчинге (AGENTS.md §4).

Единица входит в идентичность каталожной работы, поэтому «не распознали» здесь —
не мелкая неточность, а тихое схлопывание разных расценок в одну.
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from models import UnitAlias, UnitOfMeasure
from services.unit_resolution import NO_UNIT_NORM, UnitResolver

pytestmark = pytest.mark.integration


@pytest.fixture
def resolver(db_session):
    return UnitResolver(db_session)


def all_units(db_session):
    return db_session.execute(sa.select(UnitOfMeasure).order_by(UnitOfMeasure.id)).scalars().all()


class TestCanonicalForms:
    """Все канонические формы справочника обязаны распознаваться.

    Регрессия ревью фазы 4: карта строилась ТОЛЬКО из `unit_aliases`, и коды
    `SET`, `MON`, названия «Метр», «Кв. метр», «Куб. метр», «Штука»,
    «Килограмм», «Литр» алиасов не имеют — валидная единица считалась
    неизвестной и позиция теряла `unit_id`.
    """

    def test_every_code_name_and_symbol_resolves_to_its_own_unit(self, db_session, resolver):
        problems = []
        for unit in all_units(db_session):
            for label, form in (("code", unit.code), ("name", unit.name), ("symbol", unit.symbol)):
                resolved = resolver.resolve(form)
                if resolved.unit_id != unit.id or resolved.unit_norm != unit.code:
                    problems.append(f"{unit.code}.{label}={form!r} → {resolved}")
        assert problems == []

    @pytest.mark.parametrize(
        ("text", "expected_code"),
        [
            ("SET", "SET"),
            ("MON", "MON"),
            ("Килограмм", "KG"),
            ("Кв. метр", "M2"),
            ("Куб. метр", "M3"),
            ("Штука", "PCS"),
            ("Метр", "M"),
            ("Литр", "L"),
        ],
    )
    def test_forms_that_have_no_alias(self, resolver, text, expected_code):
        resolved = resolver.resolve(text)
        assert (resolved.status, resolved.unit_norm) == ("known", expected_code)

    def test_case_and_spacing_do_not_matter(self, resolver):
        for text in ("  КВ. МЕТР ", "кв.  метр", "Кв. Метр."):
            assert resolver.resolve(text).unit_norm == "M2"


class TestAliases:
    def test_alias_still_wins(self, db_session, resolver):
        assert resolver.resolve("кв.м").unit_norm == "M2"
        assert resolver.resolve("м²").unit_norm == "M2"

    def test_alias_overrides_a_canonical_form_of_another_unit(self, db_session):
        """Алиас заведён человеком осознанно и потому авторитетнее названия."""
        m3 = db_session.execute(
            sa.select(UnitOfMeasure).where(UnitOfMeasure.code == "M3")
        ).scalar_one()
        # Название единицы M2 объявляем алиасом M3 — искусственно, но именно так
        # выглядит ручное решение оператора, конфликтующее со справочником.
        db_session.add(UnitAlias(raw_text="кв. метр", unit_id=m3.id))
        db_session.flush()

        assert UnitResolver(db_session).resolve("Кв. метр").unit_norm == "M3"


class TestReverseMapping:
    def test_unit_norm_for_id_covers_every_unit(self, db_session, resolver):
        """Единица без алиасов обязана иметь unit_norm: иначе она станет «без единицы»."""
        for unit in all_units(db_session):
            assert resolver.unit_norm_for_id(unit.id) == unit.code

    def test_unit_without_aliases_keeps_its_norm(self, db_session):
        """Регрессия: обратная карта строилась по алиасам, а не по справочнику."""
        unit = UnitOfMeasure(
            code="HR", name="Час", symbol="ч", dimension="time", to_base_multiplier=1
        )
        db_session.add(unit)
        db_session.flush()
        assert (
            db_session.execute(
                sa.select(sa.func.count()).select_from(UnitAlias).where(UnitAlias.unit_id == unit.id)
            ).scalar_one()
            == 0
        )

        resolver = UnitResolver(db_session)
        assert resolver.unit_norm_for_id(unit.id) == "HR"
        assert resolver.resolve("Час").unit_norm == "HR"

    def test_none_means_no_unit(self, resolver):
        assert resolver.unit_norm_for_id(None) == NO_UNIT_NORM


class TestUnknown:
    def test_unknown_text_is_counted_and_warned_once(self, resolver):
        resolver.resolve("тонно-километр")
        resolver.resolve("тонно-километр")
        resolver.resolve("фунт")

        warnings = resolver.unknown_warnings()
        assert len(warnings) == 2
        assert any("тонно-километр" in w and "позиций: 2" in w for w in warnings)

    @pytest.mark.parametrize("empty", [None, "", "   "])
    def test_absent_unit_is_not_unknown(self, resolver, empty):
        resolved = resolver.resolve(empty)
        assert (resolved.status, resolved.unit_id, resolved.unit_norm) == ("absent", None, "")
        assert resolver.unknown_warnings() == []


class TestCollisions:
    def test_duplicate_canonical_form_does_not_silently_reassign(self, db_session, caplog):
        """Столкновение форм двух единиц — дефект справочника, а не выбор наугад."""
        clash = UnitOfMeasure(
            # symbol совпадает с symbol уже существующей единицы M2.
            code="SQM",
            name="Квадратный метр (дубль)",
            symbol="м²",
            dimension="area",
            to_base_multiplier=1,
        )
        db_session.add(clash)
        db_session.flush()
        m2_id = db_session.execute(
            sa.select(UnitOfMeasure.id).where(UnitOfMeasure.code == "M2")
        ).scalar_one()

        resolved = UnitResolver(db_session).resolve("м²")

        # Побеждает первая по id — существующая M2, а не поздний дубль.
        assert resolved.unit_id == m2_id
        assert resolved.unit_norm == "M2"
