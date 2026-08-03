"""Разрешение единиц измерения при импорте и матчинге (AGENTS.md §4).

Единица входит в идентичность каталожной работы, поэтому «не распознали» здесь —
не мелкая неточность, а тихое схлопывание разных расценок в одну.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
import sqlalchemy as sa

import services.unit_resolution as unit_resolution
from models import UnitAlias, UnitOfMeasure
from services.unit_resolution import NO_UNIT_NORM, UnitResolver

pytestmark = pytest.mark.integration


@contextmanager
def captured_warnings(logger: logging.Logger) -> Iterator[list[str]]:
    """Собирает WARNING конкретного логгера, минуя root-хендлеры.

    `caplog` в этом проекте ненадёжен: `setup_logging()` делает
    `root.handlers.clear()` (`docs/phase3-parser.md` §4.4). Тот же приём, что в
    тестах парсера.
    """
    messages: list[str] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    handler = _Collector(level=logging.WARNING)
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        yield messages
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


@pytest.fixture
def resolver(db_session):
    return UnitResolver(db_session)


def all_units(db_session):
    return db_session.execute(sa.select(UnitOfMeasure).order_by(UnitOfMeasure.id)).scalars().all()


def unit_by_code(db_session, code: str) -> UnitOfMeasure:
    return db_session.execute(
        sa.select(UnitOfMeasure).where(UnitOfMeasure.code == code)
    ).scalar_one()


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
    def test_aliases_are_recognized(self, resolver):
        assert resolver.resolve("кв.м").unit_norm == "M2"
        assert resolver.resolve("м²").unit_norm == "M2"

    def test_alias_repeating_own_canonical_form_is_not_a_conflict(self, db_session):
        """«м2» при символе «м²» — та же форма той же единицы, а не спор."""
        m2 = unit_by_code(db_session, "M2")
        db_session.add(UnitAlias(raw_text="М2", unit_id=m2.id))
        db_session.flush()

        with captured_warnings(unit_resolution.log) as messages:
            resolved = UnitResolver(db_session).resolve("м2")

        assert resolved.unit_norm == "M2"
        assert messages == []


class TestConflictPolicy:
    """Первая заявка на форму побеждает; конфликтующая отклоняется и логируется.

    Порядок регистрации детерминирован — канонические формы, затем алиасы, —
    поэтому каноническая форма всегда сильнее алиаса чужой единицы.
    """

    def test_alias_cannot_steal_a_canonical_form_of_another_unit(self, db_session):
        """Иначе M2 стала бы недостижима по собственному имени.

        Позиции в квадратных метрах молча уехали бы в кубические, а `unit_id`
        входит в идентичность каталожной работы (AGENTS.md §4).
        """
        m2, m3 = unit_by_code(db_session, "M2"), unit_by_code(db_session, "M3")
        db_session.add(UnitAlias(raw_text="кв. метр", unit_id=m3.id))
        db_session.flush()

        with captured_warnings(unit_resolution.log) as messages:
            resolved = UnitResolver(db_session).resolve("Кв. метр")

        assert (resolved.unit_id, resolved.unit_norm) == (m2.id, "M2")
        assert len(messages) == 1
        assert "кв. метр" in messages[0]
        assert "M3" in messages[0] and "M2" in messages[0]

    def test_the_stolen_alias_does_not_break_its_own_unit(self, db_session):
        """Отклоняется только конфликтующая форма, а не сам алиас как запись."""
        m3 = unit_by_code(db_session, "M3")
        db_session.add(UnitAlias(raw_text="кв. метр", unit_id=m3.id))
        db_session.flush()

        resolver = UnitResolver(db_session)
        assert resolver.resolve("м3").unit_norm == "M3"
        assert resolver.unit_norm_for_id(m3.id) == "M3"

    def test_two_aliases_differing_only_by_case_do_not_silently_swap(self, db_session):
        """`unit_aliases.raw_text` уникален по СЫРОМУ тексту, не по ключу."""
        m2, m3 = unit_by_code(db_session, "M2"), unit_by_code(db_session, "M3")
        db_session.add(UnitAlias(raw_text="квм", unit_id=m2.id))
        db_session.flush()
        db_session.add(UnitAlias(raw_text="КВМ", unit_id=m3.id))
        db_session.flush()

        with captured_warnings(unit_resolution.log) as messages:
            resolved = UnitResolver(db_session).resolve("КВМ")

        # Побеждает первая по id заявка — M2, а не поздний дубль.
        assert (resolved.unit_id, resolved.unit_norm) == (m2.id, "M2")
        assert len(messages) == 1

    def test_canonical_forms_survive_any_alias_table(self, db_session):
        """Обещание «каждая code/name/symbol резолвится в свою единицу» — безусловное."""
        m3 = unit_by_code(db_session, "M3")
        for stolen in ("Кв. метр", "M2", "м²", "Штука", "PCS"):
            db_session.add(UnitAlias(raw_text=stolen, unit_id=m3.id))
        db_session.flush()

        resolver = UnitResolver(db_session)
        problems = [
            f"{unit.code}: {form!r} → {resolver.resolve(form).unit_norm}"
            for unit in all_units(db_session)
            for form in (unit.code, unit.name, unit.symbol)
            if resolver.resolve(form).unit_id != unit.id
        ]
        assert problems == []


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


    def test_duplicate_canonical_form_does_not_silently_reassign(self, db_session):
        """Дубль в самом справочнике — тот же класс дефекта и то же правило."""
        db_session.add(
            UnitOfMeasure(
                # symbol совпадает с symbol уже существующей единицы M2.
                code="SQM",
                name="Квадратный метр (дубль)",
                symbol="м²",
                dimension="area",
                to_base_multiplier=1,
            )
        )
        db_session.flush()
        m2 = unit_by_code(db_session, "M2")

        with captured_warnings(unit_resolution.log) as messages:
            resolved = UnitResolver(db_session).resolve("м²")

        # Побеждает первая по id — существующая M2, а не поздний дубль.
        assert (resolved.unit_id, resolved.unit_norm) == (m2.id, "M2")
        assert len(messages) == 1
        assert "SQM" in messages[0]
