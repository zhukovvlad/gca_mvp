"""Правило зоны цепочки разделов и разбор ответа модели (лестница аналогов,
спека `2026-11-07-analog-ladder-design.md` §2.4, §2.5).

Правило — чистая функция названий (без базы): таблицы ключа цепочки и по входу на
каждую альтернативу каждого шаблона §2.4, порядок просмотра «от ближней главы к
корню», остановка на названии с двумя шаблонами. Разбор ответа задания зоны —
тоже без базы: `{zone, confidence, reason}`, всё прочее — ошибка схемы ответа.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from services.semantic_answer import AnswerSchemaError
from services.zone_request import ZoneAnswer, parse_zone_answer
from services.zone_rules import chain_key, normalize_title, rule_zone

# ---------------------------------------------------------------------------
#  Ключ цепочки
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Подземная часть", "подземная часть"),
        ("ПОДЗЕМНАЯ ЧАСТЬ", "подземная часть"),
        ("Объём работ", "объем работ"),
        ("ЁЛКА", "елка"),
        ("ниже отм.", "ниже отм."),
        ("  Подземная   часть  ", "подземная часть"),
        ("Подземная\tчасть\nпаркинг", "подземная часть паркинг"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalize_title_table(raw, expected):
    assert normalize_title(raw) == expected


def test_chain_key_joins_normalized_titles_with_slash():
    assert chain_key(["Подземная часть", "Кровля"]) == "подземная часть / кровля"


def test_chain_key_is_equal_for_case_yo_nbsp_and_spacing_variants():
    a = chain_key(["Подземная  часть", "Объём"])
    b = chain_key([" ПОДЗЕМНАЯ ЧАСТЬ ", "объем "])
    assert a == b == "подземная часть / объем"


def test_chain_key_depends_on_order_of_titles():
    assert chain_key(["А", "Б"]) != chain_key(["Б", "А"])


def test_chain_key_of_empty_chain_is_empty_string():
    assert chain_key([]) == ""


def test_chain_key_keeps_empty_title_in_its_place():
    assert chain_key(["А", "", "Б"]) == "а /  / б"


# ---------------------------------------------------------------------------
#  Правило: по входу на каждую альтернативу каждого шаблона §2.4
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "title",
    [
        "Подземная часть",
        "Подвал",
        "Автостоянка",
        "Работы ниже отм. 0.000",
        "Работы ниже  отм.",
        "Минус 2 уровень",
        "Минус2",
        "Секция 1, -2 эт.",
        "Секция 1, - 3 эт.",
    ],
)
def test_underground_alternatives(title):
    assert rule_zone([title]) == "underground"


@pytest.mark.parametrize(
    "title",
    [
        "Надземная часть",
        "Работы выше отм. 0.000",
        "Выше  отм.",
        "Типовой этаж",
        "Кровля",
        "Чердак",
        "Фасады",
        "Мансардный уровень",
    ],
)
def test_aboveground_alternatives(title):
    assert rule_zone([title]) == "aboveground"


@pytest.mark.parametrize(
    "title",
    [
        "Благоустройство",
        "Работы на территории",
        "Двор",
        "Детская площадка",
        "Озеленение",
        "Дороги и проезды",
        "Подъездная дорога",
        "Проезд",
        "Тротуары",
        "МАФ",
        "Устройство газонов",
        "Наружное освещение",
        "Водоотведение с участка и водоотвод",
        "Покрытие по грунту",
        "Парк",
    ],
)
def test_territory_alternatives(title):
    assert rule_zone([title]) == "territory"


def test_parking_title_matches_two_templates_and_gives_no_answer():
    # «паркинг» несёт и «паркинг» подземного шаблона, и «парк» шаблона территории:
    # правило §2.4 читается буквально, два шаблона — без ответа.
    assert rule_zone(["Паркинг"]) is None


def test_maf_needs_word_boundaries():
    assert rule_zone(["Мафия"]) is None


def test_two_alternatives_of_the_same_template_are_one_template():
    assert rule_zone(["Подземная автостоянка, подвал"]) == "underground"


def test_title_without_any_template_gives_no_answer():
    assert rule_zone(["Общестроительные работы", "Раздел 3"]) is None


def test_empty_chain_gives_no_answer():
    assert rule_zone([]) is None


def test_case_and_yo_do_not_matter():
    assert rule_zone(["ПОДЗЕМНАЯ ЧАСТЬ"]) == "underground"
    assert rule_zone(["Мансарда"]) == "aboveground"


def test_nbsp_inside_a_pattern_is_a_space():
    assert rule_zone(["Работы ниже отм."]) == "underground"
    assert rule_zone(["Наружное освещение"]) == "territory"


# ---------------------------------------------------------------------------
#  Порядок просмотра и остановка
# ---------------------------------------------------------------------------

def test_nearest_chapter_goes_first():
    assert rule_zone(["Подземная часть", "Кровля"]) == "aboveground"


def test_nearest_chapter_goes_first_in_the_other_direction():
    assert rule_zone(["Надземная часть", "Паркинг, подвал"]) is None  # два шаблона в ближней
    assert rule_zone(["Надземная часть", "Подвал"]) == "underground"


def test_scan_goes_up_to_the_root_when_nearest_titles_say_nothing():
    assert rule_zone(["Благоустройство", "Раздел 5", "Прочие работы"]) == "territory"


def test_title_with_two_templates_stops_the_scan_even_if_a_clean_one_is_above():
    assert rule_zone(["Подземная часть", "Кровля и подвал"]) is None


def test_title_with_three_templates_stops_the_scan_too():
    assert rule_zone(["Благоустройство", "Подвал, кровля, двор"]) is None


def test_stop_applies_only_to_the_first_matching_title_not_to_silent_ones():
    # Название без шаблонов просмотр не останавливает.
    assert rule_zone(["Подземная часть", "Раздел 2", "Общее"]) == "underground"


def test_rule_never_gives_whole():
    samples = [
        ["Проектирование"],
        ["Охрана объекта"],
        ["Временные здания и сооружения"],
        ["Гарантийные обязательства", "Общие"],
        ["Весь объект"],
        [],
    ]
    assert [rule_zone(s) for s in samples] == [None] * len(samples)


# ---------------------------------------------------------------------------
#  Разбор ответа задания зоны
# ---------------------------------------------------------------------------

def _raw(**fields):
    base = {"zone": "underground", "confidence": 0.97, "reason": "Подземная часть"}
    base.update(fields)
    return json.dumps(base, ensure_ascii=False)


@pytest.mark.parametrize("zone", ["underground", "aboveground", "territory", "whole", "unknown"])
def test_parse_accepts_each_zone_code_and_unknown(zone):
    answer = parse_zone_answer(_raw(zone=zone))
    assert answer == ZoneAnswer(zone=zone, confidence=Decimal("0.97"), reason="Подземная часть")


def test_parse_accepts_integer_confidence_bounds():
    assert parse_zone_answer(_raw(confidence=0)).confidence == Decimal(0)
    assert parse_zone_answer(_raw(confidence=1)).confidence == Decimal(1)


def test_parse_accepts_fenced_json():
    fenced = "```json\n" + _raw() + "\n```"
    assert parse_zone_answer(fenced).zone == "underground"


def test_parse_strips_reason():
    assert parse_zone_answer(_raw(reason="  Кровля  ")).reason == "Кровля"


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("не json", "not_json"),
        (json.dumps({"confidence": 0.9, "reason": "x"}), "missing_field"),
        (json.dumps({"zone": "underground", "reason": "x"}), "missing_field"),
        (json.dumps({"zone": "underground", "confidence": 0.9}), "missing_field"),
        (_raw(zone="basement"), "bad_type"),
        (_raw(zone=None), "bad_type"),
        (_raw(zone=3), "bad_type"),
        (_raw(confidence=1.01), "bad_confidence"),
        (_raw(confidence=-0.1), "bad_confidence"),
        (_raw(confidence="0.9"), "bad_confidence"),
        (_raw(confidence=True), "bad_confidence"),
        (_raw(reason=""), "empty_reason"),
        (_raw(reason="   "), "empty_reason"),
        (_raw(reason=5), "bad_type"),
        (_raw(reason="я" * 301), "bad_type"),
    ],
)
def test_parse_rejects_answers_off_the_schema(raw, code):
    with pytest.raises(AnswerSchemaError) as caught:
        parse_zone_answer(raw)
    assert caught.value.code == code


def test_parse_accepts_reason_of_exactly_300_chars():
    assert len(parse_zone_answer(_raw(reason="я" * 300)).reason) == 300


def test_parse_rejects_nan_confidence():
    with pytest.raises(AnswerSchemaError) as caught:
        parse_zone_answer('{"zone": "whole", "confidence": NaN, "reason": "x"}')
    assert caught.value.code == "bad_confidence"
