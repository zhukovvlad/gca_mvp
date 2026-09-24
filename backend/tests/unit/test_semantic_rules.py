"""Тесты правил `services/semantic_rules.py` (задача 3 фичи «Семьи и контексты»).

План: `docs/superpowers/plans/2026-09-22-catalog-families.md`, задача 3.
Спека: `docs/superpowers/specs/2026-09-22-catalog-families-design.md` §1.9,
§1.14, §2.6.

Эталон 101 строки (`backend/tests/data/semantic_reference_101.json`) —
решение плана 1 (обезличенные тестовые данные под гитом, `AGENTS.md` §9.3);
собран заново скриптом `tasks/catalog-families-work/build_semantic_reference_101.py`
из `tasks/catalog-pilot-2026-09-18/razmetka-101.xlsx` (файл вне гита,
существует на одной машине).

Входы для роли имени (`LOCATION_ONLY`, `GENERIC_WORK`, смешанное имя) — НЕ из
101-эталона (там роль имени не размечена вовсе), а литералом из другой книги,
`razmetka-peresklejka.xlsx`, лист «А межстатейный» (наименования, единицы и
РЕАЛЬНЫЕ цепочки разделов — заголовки разделов, написанные заказчиком, без цен
и подрядчиков): семь строк-мест «Секция 1»…«Секция 6», «Паркинг» и четыре
решённых `GENERIC_WORK` — «Светильники», «Полы:», «Стены:», «Потолок:»,
решения пользователя от 22.09.2026 записаны в комментариях той книги.
Смешанное имя «Корпус 1 - Экран декоративный…» в обеих книгах НЕ найдено (обе
проверены `grep`-подобным поиском подстроки «Экран»+«декоративн» по всем
листам) — использована ровно та формулировка, что дана в спеке §1.5/§2.6, без
додумывания продолжения; это явное отклонение.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from crud.units import ALIASES_SEED, normalize_unit_key
from models import ComparabilityReason, NameRole, SemanticKind
from services import semantic_rules
from services.semantic_rules import (
    GENERIC_WORK_TOKENS,
    PLACE_DICTIONARY_VERSION,
    PLACE_TOKENS,
    SYSTEM_UNIT_NORM,
    NameRoleOutcome,
    classify_kind,
    classify_name_role,
)
from services.unit_resolution import NO_UNIT_NORM

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
REFERENCE_101_PATH = DATA_DIR / "semantic_reference_101.json"


def _load_reference_101() -> list[dict]:
    with open(REFERENCE_101_PATH, encoding="utf-8") as f:
        return json.load(f)


REFERENCE_101 = _load_reference_101()


def test_module_surface_names_from_plan_task_3():
    """Имена, заводимые задачей 3 (план, раздел «Имена»), существуют и несут
    ожидаемые роли — не только используются в тестах ниже по назначению."""
    assert isinstance(PLACE_DICTIONARY_VERSION, int) and PLACE_DICTIONARY_VERSION >= 1
    assert "светильник" in GENERIC_WORK_TOKENS
    assert "паркинг" in PLACE_TOKENS
    sample_outcome = classify_name_role("Полы:", chapter_chain=())
    assert isinstance(sample_outcome, NameRoleOutcome)


def _unit_norm(raw_unit: str) -> str:
    """Единица эталона → `unit_norm` тем же ключом, что и рантайм
    (`normalize_unit_key`), но по статическому `ALIASES_SEED`
    (`crud/units.py`) — засеянному словарю, а не по строке живой таблицы:
    модуль без БД, и здесь её нет тоже.
    """
    key = normalize_unit_key(raw_unit)
    return ALIASES_SEED[key]


# ---------------------------------------------------------------------------
# Утверждение 1: правило вида на эталоне 101 строки — ровно 98 совпадений,
# три исключения названы поимённо и по природе (спека §1.14).
# ---------------------------------------------------------------------------

#: Ключ — (title, unit): «Оборудование службы безопасности паркинга» в эталоне
#: встречается ДВАЖДЫ (строки листа 60 и 96, id 388 и 1188) с разными
#: единицами — «шт» (система/место, исключение) и «компл» (система/место,
#: правило совпадает). Различитель обязан включать единицу, иначе тест поймал
#: бы не ту строку.
_EXCEPTION_TITLES_BY_NATURE = {
    ("Стены толщ. 200мм", "м³"): "вид неизвестен человеку (строка размечена «непонятно»)",
    ("Оборудование службы безопасности паркинга", "шт"): (
        "единица «шт» в смете — опечатка входа, по сути комплект"
    ),
    ("Подключение блоков к КЛ ЭОМ, прокладка управляющих КЛ", "компл"): (
        "комплект означает состав работы, а не объект целиком"
    ),
}


def test_kind_rule_gives_exactly_98_of_101_matches():
    matches = 0
    mismatched_keys = set()
    for record in REFERENCE_101:
        unit_norm = _unit_norm(record["unit"])
        if classify_kind(unit_norm) == record["kind"]:
            matches += 1
        else:
            mismatched_keys.add((record["title"], record["unit"]))

    assert len(REFERENCE_101) == 101
    assert matches == 98
    assert mismatched_keys == set(_EXCEPTION_TITLES_BY_NATURE)


@pytest.mark.parametrize(
    "title,unit,nature",
    [(t, u, nature) for (t, u), nature in sorted(_EXCEPTION_TITLES_BY_NATURE.items())],
)
def test_kind_rule_exception_is_named_and_marked_in_fixture(title, unit, nature):
    matching = [r for r in REFERENCE_101 if r["title"] == title and r["unit"] == unit]
    assert len(matching) == 1, (
        f"строка {title!r}/{unit!r} должна встречаться в эталоне ровно один раз"
    )
    record = matching[0]
    unit_norm = _unit_norm(record["unit"])
    assert classify_kind(unit_norm) != record["kind"], (
        f"строка {title!r}/{unit!r} — исключение правила вида; правило не должно совпасть с эталоном"
    )
    assert record.get("exception"), f"строка {title!r}/{unit!r} обязана нести пометку исключения"


@pytest.mark.parametrize(
    "record", REFERENCE_101, ids=[r["title"][:40] for r in REFERENCE_101]
)
def test_kind_rule_over_full_reference_101(record):
    """Один параметризованный вход на все 101 эталонных строки (план, задача 3)."""
    unit_norm = _unit_norm(record["unit"])
    rule_kind = classify_kind(unit_norm)
    assert rule_kind in {SemanticKind.WORK.value, SemanticKind.SYSTEM.value}
    if "exception" in record:
        assert rule_kind != record["kind"]
    else:
        assert rule_kind == record["kind"]


# ---------------------------------------------------------------------------
# Утверждение 2: classify_kind ставит SYSTEM тогда и только тогда, когда
# unit_norm == SYSTEM_UNIT_NORM — обе стороны плюс пустой NO_UNIT_NORM.
# ---------------------------------------------------------------------------


def test_classify_kind_system_unit_norm_gives_system():
    assert SYSTEM_UNIT_NORM == "SET"
    assert classify_kind(SYSTEM_UNIT_NORM) == SemanticKind.SYSTEM.value


def test_classify_kind_non_system_unit_norm_gives_work():
    assert classify_kind("PCS") == SemanticKind.WORK.value


def test_classify_kind_no_unit_norm_gives_work():
    assert NO_UNIT_NORM == ""
    assert NO_UNIT_NORM != SYSTEM_UNIT_NORM
    assert classify_kind(NO_UNIT_NORM) == SemanticKind.WORK.value


# ---------------------------------------------------------------------------
# Утверждение 3: семь чистых LOCATION_ONLY получают work_title из ближайшего
# РАБОЧЕГО раздела цепочки; LOCATION_ONLY без рабочего раздела над строкой —
# insufficient_description и пустой work_title.
#
# Источник: razmetka-peresklejka.xlsx, лист «А межстатейный», статьи
# 5.2/5.3/5.99. Строки листа НЕ идут в порядке номеров секций (прежняя
# формулировка «74, 77, 80, 83, 86, 89 (Секция
# 1..6)» читалась как последовательное соответствие, а оно не такое):
# 74 → Секция 1, 77 → Секция 3, 80 → Секция 4, 83 → Секция 5, 86 → Секция 6,
# 89 → Секция 2, 110 → Паркинг — пары «строка листа → title» ниже даны явно
# в `_CLEAN_LOCATION_ONLY_ROWS`, а не подразумеваются порядком перечисления.
# Цепочка сшита из первого перечисленного варианта столбца «Цепочки разделов
# в этой статье (верх → низ)», развёрнута к порядку «ближайший → корень»
# (тот же порядок, что ChapterContext.chain в задаче 4).
# ---------------------------------------------------------------------------

_WORKING_CHAPTER = "Общестроительные работы - перегородки и стены"
_ROOT_CHAPTER = "ФИКСИРОВАННАЯ ЧАСТЬ"

_CLEAN_LOCATION_ONLY_ROWS = [
    ("Секция 1", ("Урбан блок 4", _WORKING_CHAPTER, _ROOT_CHAPTER)),
    ("Секция 2", ("Урбан блок 4", _WORKING_CHAPTER, _ROOT_CHAPTER)),
    ("Секция 3", ("Урбан блок 5", _WORKING_CHAPTER, _ROOT_CHAPTER)),
    ("Секция 4", ("Урбан блок 3", _WORKING_CHAPTER, _ROOT_CHAPTER)),
    ("Секция 5", ("Урбан блок 3", _WORKING_CHAPTER, _ROOT_CHAPTER)),
    ("Секция 6", ("Урбан блок 4", _WORKING_CHAPTER, _ROOT_CHAPTER)),
    ("Паркинг", ("Урбан блок 3", _WORKING_CHAPTER, _ROOT_CHAPTER)),
]

assert len(_CLEAN_LOCATION_ONLY_ROWS) == 7


@pytest.mark.parametrize("title,chapter_chain", _CLEAN_LOCATION_ONLY_ROWS)
def test_location_only_takes_work_title_from_nearest_working_chapter(title, chapter_chain):
    outcome = classify_name_role(title, chapter_chain=chapter_chain)
    assert outcome.role == NameRole.LOCATION_ONLY.value
    assert outcome.location == title
    assert outcome.work_title == _WORKING_CHAPTER
    assert outcome.comparability_reason is None


def test_location_only_without_working_chapter_is_insufficient_description():
    """Синтетический вход (не из корпуса): цепочка целиком из словарных мест,
    рабочего раздела над строкой нет ни на одном уровне.
    """
    outcome = classify_name_role(
        "Секция 7", chapter_chain=("Урбан блок 2", "Корпус 3", "Паркинг")
    )
    assert outcome.role == NameRole.LOCATION_ONLY.value
    assert outcome.location == "Секция 7"
    assert outcome.work_title is None
    assert outcome.comparability_reason == ComparabilityReason.insufficient_description.value


# ---------------------------------------------------------------------------
# «Рабочий раздел» —
# НЕ место, НЕ род изделия/поверхности без состава и НЕ «Прочее» (спека §1.10
# называет «Прочее» в числе начал разделов, которые не называют работу, наряду
# со «Стены», «Пол», «Секция» — те уже покрыты словарями). Все три синтетических
# входа ниже: не из корпуса, названы явно.
# ---------------------------------------------------------------------------


def test_location_only_skips_prochee_chapter_and_takes_next_working():
    outcome = classify_name_role(
        "Секция 1", chapter_chain=("Прочее", _WORKING_CHAPTER, _ROOT_CHAPTER)
    )
    assert outcome.role == NameRole.LOCATION_ONLY.value
    assert outcome.work_title == _WORKING_CHAPTER
    assert outcome.comparability_reason is None


def test_location_only_skips_generic_work_chapter_and_takes_next_working():
    outcome = classify_name_role(
        "Секция 1", chapter_chain=("Стены:", _WORKING_CHAPTER, _ROOT_CHAPTER)
    )
    assert outcome.role == NameRole.LOCATION_ONLY.value
    assert outcome.work_title == _WORKING_CHAPTER
    assert outcome.comparability_reason is None


def test_location_only_all_place_generic_prochee_chain_is_insufficient_description():
    """Цепочка смешивает все три НЕ-рабочих класса разом (место, род изделия,
    «Прочее») — ни один уровень не «рабочий», значит work_title пуст.
    """
    outcome = classify_name_role(
        "Секция 1", chapter_chain=("Урбан блок 2", "Прочее", "Стены:")
    )
    assert outcome.role == NameRole.LOCATION_ONLY.value
    assert outcome.work_title is None
    assert outcome.comparability_reason == ComparabilityReason.insufficient_description.value


# ---------------------------------------------------------------------------
# Индекс экземпляра
# места — цифры и/или не более одной буквы, СПЕРЕДИ ИЛИ СЗАДИ словарного
# слова. По одному входу на форму, плюс по одному на непроверенные ранее
# словарные слова (этап, уровень, этаж, зона — «секция», «корпус», «паркинг»
# и «урбан блок» уже покрыты выше), плюс негатив: словарное слово места, за
# которым идёт НЕ индекс, LOCATION_ONLY не даёт.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "3 этаж",
        "Зона А1",
        "Секция 1А",
        "Корпус Б",
        "Секция-1",
    ],
    ids=[
        "leading_digit_index",
        "letter_digit_glued_index",
        "digit_letter_glued_index",
        "trailing_single_letter_index",
        "hyphen_glued_index_no_spaces",
    ],
)
def test_place_index_forms_give_location_only(title):
    outcome = classify_name_role(title, chapter_chain=())
    assert outcome.role == NameRole.LOCATION_ONLY.value
    assert outcome.location == title


@pytest.mark.parametrize(
    "title",
    ["Этап 1", "Уровень 2", "Этаж 3", "Зона 4"],
    ids=["etap", "uroven", "etazh", "zona"],
)
def test_place_tokens_not_yet_exercised_give_location_only(title):
    """«Секция», «корпус», «паркинг», «урбан блок» уже проверены в других
    утверждениях этого файла; здесь — оставшиеся четыре слова словаря."""
    outcome = classify_name_role(title, chapter_chain=())
    assert outcome.role == NameRole.LOCATION_ONLY.value


def test_place_word_followed_by_non_index_word_stays_work():
    """Негатив: «Секция» — словарное слово места, но за ним идёт НЕ индекс
    («Достоевского» — многобуквенное слово, не цифра и не одна буква) — вся
    фраза целиком словарной не считается, роль остаётся WORK."""
    outcome = classify_name_role("Секция Достоевского", chapter_chain=())
    assert outcome.role == NameRole.WORK.value
    assert outcome.role != NameRole.LOCATION_ONLY.value


def test_underscore_glued_token_is_not_an_index_stays_work():
    """«1_2» — условие «токен целиком из цифр и не более чем одной буквы»
    живо и ловит `_` (не цифра и не буква) как посторонний символ: токен не
    режется на части («_» не входит в `_TOKEN_SPLIT_RE`), а как ЕДИНЫЙ токен
    не проходит `_is_index_token` — вся фраза «секция 1_2» словарной не
    считается, роль остаётся WORK."""
    outcome = classify_name_role("Секция 1_2", chapter_chain=())
    assert outcome.role == NameRole.WORK.value
    assert outcome.role != NameRole.LOCATION_ONLY.value


def test_hyphenated_place_word_is_recognized_as_place():
    """«Урбан-блок 4» (дефис без пробелов, слитно) — та же словарная
    фраза «урбан блок», что и раздельное написание: лемматизация spaCy режет
    дефис между двумя буквенными словами и отбрасывает его как пунктуацию
    (см. докстроку `PLACE_TOKENS`), поэтому обе формы дают LOCATION_ONLY."""
    outcome = classify_name_role("Урбан-блок 4", chapter_chain=())
    assert outcome.role == NameRole.LOCATION_ONLY.value
    assert outcome.location == "Урбан-блок 4"


# ---------------------------------------------------------------------------
# Утверждение 4: смешанное имя разводится, проверяются ОБА выхода.
# ---------------------------------------------------------------------------


def test_mixed_name_splits_location_and_work_remainder():
    outcome = classify_name_role("Корпус 1 - Экран декоративный", chapter_chain=())
    assert outcome.role == NameRole.LOCATION_ONLY.value
    assert outcome.location == "Корпус 1"
    assert outcome.work_title == "Экран декоративный"
    assert outcome.comparability_reason is None


# Три написания разделителя смешанного имени —
# дефис, короткое тире (–, U+2013), длинное тире (—, U+2014) — по одному входу.
@pytest.mark.parametrize(
    "dash", ["-", "–", "—"], ids=["hyphen", "en_dash", "em_dash"]
)
def test_mixed_name_separator_accepts_hyphen_en_dash_em_dash(dash):
    title = f"Корпус 1 {dash} Экран декоративный"
    outcome = classify_name_role(title, chapter_chain=())
    assert outcome.role == NameRole.LOCATION_ONLY.value
    assert outcome.location == "Корпус 1"
    assert outcome.work_title == "Экран декоративный"


# Разделитель есть, а остаток после него пуст.
# Это условие «непустой остаток» (`remainder.strip()`) не пропускает в ветку
# смешанного имени — вход уходит в проверку «чистое место»: висящий дефис —
# пунктуация, лемматизация съедает его целиком, весь текст сводится к
# «корпус» ЦЕЛИКОМ. Итог определён и закреплён этим тестом: LOCATION_ONLY,
# `location` сохраняет исходное написание С висящим дефисом (`str.strip()`
# убирает только пробелы, не дефис).
def test_mixed_name_separator_with_empty_remainder_is_pure_location_only():
    outcome = classify_name_role("Корпус 1 - ", chapter_chain=())
    assert outcome.role == NameRole.LOCATION_ONLY.value
    assert outcome.location == "Корпус 1 -"
    assert outcome.work_title is None
    assert outcome.comparability_reason == ComparabilityReason.insufficient_description.value


# ---------------------------------------------------------------------------
# Остаток
# смешанного имени из ОДНОГО ГОЛОГО ИНДЕКСА («2», «1») именем работы не
# считается — иначе «Секция 1 - 2» разводилась бы на location «Секция 1» +
# work «2», хотя «2» никакую работу не называет. Оба входа целиком сводятся
# к словарному месту («секция»/«уровень» — оба числа отброшены `_place_phrase`
# как индекс с хвоста), значит уходят в ветку «чистое место» — `location`
# сохраняет исходную строку целиком (с разделителем и вторым числом внутри),
# `work_title`/`comparability_reason` определяются цепочкой, как у любого
# чистого места (здесь цепочка пуста → рабочего раздела нет →
# insufficient_description).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title", ["Секция 1 - 2", "Уровень - 1"], ids=["sektsiya_1_dash_2", "uroven_dash_1"]
)
def test_mixed_name_bare_index_remainder_stays_clean_place(title):
    outcome = classify_name_role(title, chapter_chain=())
    assert outcome.role == NameRole.LOCATION_ONLY.value
    assert outcome.location == title
    assert outcome.work_title is None
    assert outcome.comparability_reason == ComparabilityReason.insufficient_description.value


# ---------------------------------------------------------------------------
# Утверждение 5: четыре решённых GENERIC_WORK — insufficient_description,
# роль именно GENERIC_WORK, а не LOCATION_ONLY.
#
# Источник: razmetka-peresklejka.xlsx, лист «А межстатейный», строки 64
# (Светильники), 180 (Полы:), 185 (Стены:), 190 (Потолок:); комментарии этих
# строк несут решение пользователя от 22.09.2026. Цепочка — первый вариант
# столбца «Цепочки разделов…», развёрнутый к порядку «ближайший → корень»;
# для GENERIC_WORK на исход не влияет (comparability_reason безусловна), но
# передаётся настоящая, а не придуманная.
# ---------------------------------------------------------------------------

_GENERIC_WORK_CHAIN_SVETILNIKI = (
    "Кладовые (согласно приложения к стандартам по организации кладовых помещений)",
    "Отделка подземной части",
    "Отделка паркинга, технических помещений, МОП, двери, ворота и шлагбаумы в соответствии с дизайн-проектом",
)
_GENERIC_WORK_CHAIN_POL_STENA_POTOLOK = (
    "Технические и инженерные помещения подземной части (в соответствии с ТЗ, "
    "с учетом нормативных требований по шумо/виброизоляции и требований тома "
    "ООС1.1/1.2 и ПБ",
    "Отделка подземной части",
    "Отделка паркинга, технических помещений, МОП, двери, ворота и шлагбаумы в соответствии с дизайн-проектом",
)

_GENERIC_WORK_ROWS = [
    ("Светильники", _GENERIC_WORK_CHAIN_SVETILNIKI),
    ("Полы:", _GENERIC_WORK_CHAIN_POL_STENA_POTOLOK),
    ("Стены:", _GENERIC_WORK_CHAIN_POL_STENA_POTOLOK),
    ("Потолок:", _GENERIC_WORK_CHAIN_POL_STENA_POTOLOK),
]

assert len(_GENERIC_WORK_ROWS) == 4


@pytest.mark.parametrize("title,chapter_chain", _GENERIC_WORK_ROWS)
def test_generic_work_is_insufficient_description_not_location_only(title, chapter_chain):
    outcome = classify_name_role(title, chapter_chain=chapter_chain)
    assert outcome.role == NameRole.GENERIC_WORK.value
    assert outcome.role != NameRole.LOCATION_ONLY.value
    assert outcome.comparability_reason == ComparabilityReason.insufficient_description.value
    # Имя остаётся названием работы на уровне семьи (спека §1.6) — как
    # определяет реализация, `work_title == title`, `location is None`.
    assert outcome.work_title == title
    assert outcome.location is None


# «Согласно ДП» — пятая
# словарная форма GENERIC_WORK, спека §2.6 называет её прямо (спека сильнее
# плана в перечне примеров). Как САМОСТОЯТЕЛЬНОЕ наименование строки ни в
# `razmetka-101.xlsx`, ни в `razmetka-peresklejka.xlsx` не встречается — там
# это фраза ВНУТРИ более длинных наименований и комментариев («в тч плинтус
# и подстилающие слои, согласно ДП», «Работа без описания состава («согласно
# ДП»)», razmetka-101.xlsx, лист «Разметка»). Вход — дословная формулировка
# спеки, как и для смешанного имени в утверждении 4; цепочка пуста —
# GENERIC_WORK от неё не зависит (см. выше).
def test_generic_work_soglasno_dp_is_insufficient_description():
    outcome = classify_name_role("согласно ДП", chapter_chain=())
    assert outcome.role == NameRole.GENERIC_WORK.value
    assert outcome.comparability_reason == ComparabilityReason.insufficient_description.value
    assert outcome.work_title == "согласно ДП"
    assert outcome.location is None


# ---------------------------------------------------------------------------
# Утверждение 6: негативный вход на словарь — форма места, которой в
# PLACE_TOKENS нет, обязана дать WORK, а не LOCATION_ONLY.
# ---------------------------------------------------------------------------


def test_place_form_outside_dictionary_gives_work_not_location_only():
    assert "крыло" not in PLACE_TOKENS
    outcome = classify_name_role("Крыло 1", chapter_chain=())
    assert outcome.role == NameRole.WORK.value
    assert outcome.role != NameRole.LOCATION_ONLY.value
    assert outcome.location is None
    assert outcome.comparability_reason is None
    # Как определяет реализация, WORK хранит имя работы в work_title как
    # есть (`title.strip()`), location остаётся None.
    assert outcome.work_title == "Крыло 1"


# Пустой title — реально достижимый путь до
# фолбэка `_normalize`: `normalize_job_title_with_lemmatization("")` отдаёт
# `None` (basic_clean_job_title возвращает `None` на пустой строке), и
# `_normalize` подставляет `""` вместо падения. Без этого входа фолбэк
# (`... or ""`) не исполняется НИ РАЗУ ни одним тестом модуля.
def test_empty_title_covers_normalize_fallback_and_gives_work():
    outcome = classify_name_role("", chapter_chain=())
    assert outcome.role == NameRole.WORK.value
    assert outcome.location is None
    assert outcome.work_title == ""
    assert outcome.comparability_reason is None


# ---------------------------------------------------------------------------
# Ни одна из 101 эталонной строки не смеет
# выйти LOCATION_ONLY/GENERIC_WORK — пользователь разметил все 101 строки
# работой либо системой/местом (вид) и ни одну не отметил голым именем места
# или родом изделия без состава (роль имени в этом эталоне не размечена
# вовсе, но кандидатов на такую роль пользователь тоже не помечал). Без этого
# теста два условия можно молча ослабить, и никакой другой тест этого не
# заметит:
#   - охрана «префикс смешанного имени — место»
#     (`_place_phrase(prefix) is not None` в ветке смешанного имени
#     `classify_name_role`, `services/semantic_rules.py`) без себя
#     превращает ЛЮБОЕ имя с « - » в LOCATION_ONLY; в эталоне 9 таких имён,
#     например `Геотекстиль "Дорнит - 200"` (строка 2 листа «Разметка»);
#   - «фраза места сверяется ЦЕЛИКОМ» без охраны (пословный матчер прошёл бы
#     тоже) даёт LOCATION_ONLY любому имени со словом из PLACE_TOKENS внутри
#     — «Оборудование службы безопасности паркинга» содержит «паркинга».
# Перед тем как писать этот тест, распределение ролей по всем 101 строкам на
# ТЕКУЩЕЙ реализации проверено явно: 101 WORK, 0 иных —
# поэтому тест пишется как утверждение, а не подгоняется под найденный баг.
# ---------------------------------------------------------------------------

_MIXED_NAME_LOOKALIKE_TITLE = 'Геотекстиль "Дорнит - 200"'
_PLACE_WORD_SUBSTRING_TITLE = "Оборудование службы безопасности паркинга"


def test_h1_reference_101_has_no_location_only_or_generic_work_names():
    assert any(r["title"] == _MIXED_NAME_LOOKALIKE_TITLE for r in REFERENCE_101), (
        "свидетель-строка с « - » должна реально быть в эталоне"
    )
    assert any(r["title"] == _PLACE_WORD_SUBSTRING_TITLE for r in REFERENCE_101), (
        "свидетель-строка со словом словаря внутри должна реально быть в эталоне"
    )
    for record in REFERENCE_101:
        outcome = classify_name_role(record["title"], chapter_chain=())
        assert outcome.role == NameRole.WORK.value, (
            f"строка {record['title']!r} эталона размечена пользователем как "
            f"вид {record['kind']!r}, не как голое имя места или рода изделия "
            f"— роль обязана остаться WORK, а не {outcome.role!r}"
        )


@pytest.mark.parametrize(
    "title", [_MIXED_NAME_LOOKALIKE_TITLE, _PLACE_WORD_SUBSTRING_TITLE]
)
def test_h1_named_witnesses_stay_work(title):
    outcome = classify_name_role(title, chapter_chain=())
    assert outcome.role == NameRole.WORK.value
    assert outcome.location is None


# ---------------------------------------------------------------------------
# Утверждение 7: модуль не импортирует Session и не обращается к БД ни одной
# строкой — проверено по AST исходного файла, а не чтением. Белый список
# заодно доказывает и более широкое:
# модуль не подключает вообще ничего из проекта, кроме `models` и
# `parser.sanitize_text`, — в частности, не трогает `services/matching.py`
# (Global Constraints плана: каскад матчинга этой задачей не трогается).
# ---------------------------------------------------------------------------


#: Проверка переведена с чёрного списка
#: (перечислить запрещённые имена) на БЕЛЫЙ — перечислить допустимые, а
#: остальное отвергать автоматически. Чёрный список ловит только то, что в
#: него вписали (прошлый раз — `sqlalchemy`/`crud`/`database`/`Session`), и
#: пропустил бы молча любой другой посторонний импорт, например
#: `services.matching` — тот самый каскад матчинга, который задача явно не
#: трогает (Global Constraints плана). Белый список — стандартная библиотека
#: (`sys.stdlib_module_names`, включает `re`, `dataclasses`, `__future__`) и
#: РОВНО те два модуля проекта, которые `semantic_rules.py` реально
#: использует: `models` (перечисления, задача 1) и `parser.sanitize_text`
#: (нормализация, спека §2.4). Любой третий модуль проекта — красит тест.
_ALLOWED_PROJECT_MODULES = frozenset({"models", "parser.sanitize_text"})


def _imported_modules_of(source_path: Path) -> set[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def test_module_only_imports_stdlib_models_and_sanitize_text():
    imported_modules = _imported_modules_of(Path(semantic_rules.__file__))
    stdlib = sys.stdlib_module_names

    disallowed = {
        m
        for m in imported_modules
        if m not in _ALLOWED_PROJECT_MODULES and m.split(".")[0] not in stdlib
    }
    assert not disallowed, (
        f"импорты вне белого списка (стандартная библиотека + "
        f"{sorted(_ALLOWED_PROJECT_MODULES)}): {sorted(disallowed)!r}"
    )


# ---------------------------------------------------------------------------
# Утверждение 8: контракт фикстуры 101 — белый список, обе стороны, плюс
# негативный вход на саму проверку (запись с посторонним ключом обязана её
# покрасить — иначе проверка неотличима от чёрного списка).
# ---------------------------------------------------------------------------

REQUIRED_KEYS_101 = frozenset({"title", "unit", "kind"})
OPTIONAL_KEYS_101 = frozenset({"name_role", "category", "exception"})
ALLOWED_KEYS_101 = REQUIRED_KEYS_101 | OPTIONAL_KEYS_101


def _fixture_row_is_valid(row: dict) -> bool:
    keys = set(row.keys())
    return REQUIRED_KEYS_101.issubset(keys) and keys.issubset(ALLOWED_KEYS_101)


def test_fixture_101_has_101_records():
    assert len(REFERENCE_101) == 101


@pytest.mark.parametrize(
    "record", REFERENCE_101, ids=[r["title"][:40] for r in REFERENCE_101]
)
def test_fixture_101_record_matches_whitelist_both_directions(record):
    assert _fixture_row_is_valid(record), (
        f"запись {record!r} не проходит контракт белого списка "
        f"(обязательные {sorted(REQUIRED_KEYS_101)}, допустимые {sorted(ALLOWED_KEYS_101)})"
    )


def test_fixture_101_whitelist_rejects_unknown_key():
    """Негативный вход на саму проверку: посторонний ключ обязан её покрасить."""
    mutated = {**REFERENCE_101[0], "project_code": "X-1"}
    assert not _fixture_row_is_valid(mutated)


def test_fixture_101_whitelist_rejects_missing_required_key():
    mutated = dict(REFERENCE_101[0])
    del mutated["unit"]
    assert not _fixture_row_is_valid(mutated)


def test_fixture_101_category_is_classifier_code_not_stand_id():
    """Статья адресуется кодом классификатора («11.1», «13.1»…), а не
    стендовым первичным ключом (`id` листа «Разметка», трёх- и
    пятизначные числа) и не номером строки.
    """
    for record in REFERENCE_101:
        if "category" not in record:
            continue
        category = record["category"]
        assert isinstance(category, str)
        assert re.fullmatch(r"\d+(\.\d+)*", category), (
            f"код статьи {category!r} должен быть точечной нумерацией классификатора"
        )


# ---------------------------------------------------------------------------
# `category` обязан быть кодом из
# WORK_CATEGORIES_SEED миграции 0005 — внешний оракул, а не только формат
# «точки между цифрами» (тот формат прошла бы и выдуманная строка). Миграция
# грузится по пути через importlib (тот же приём, что parity-тесты задачи 1,
# `tests/integration/test_semantic_schema.py`), а не импортом модуля: у файла
# миграции нет пакетного имени, обычный import его не найдёт.
# ---------------------------------------------------------------------------


def _load_work_categories_seed() -> frozenset[str]:
    glob_pattern = "alembic/versions/*0005-work_categories.py"
    path = next(Path(__file__).resolve().parents[2].glob(glob_pattern), None)
    if path is None:
        raise FileNotFoundError(
            f"миграция 0005 не найдена по шаблону {glob_pattern!r} "
            "относительно backend/ — оракул category сверить не с чем"
        )
    spec = importlib.util.spec_from_file_location("_migration_0005_task3", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return frozenset(code for code, _title in module.WORK_CATEGORIES_SEED)


WORK_CATEGORIES_SEED_CODES = _load_work_categories_seed()


def test_work_categories_seed_loaded_and_nonempty():
    assert len(WORK_CATEGORIES_SEED_CODES) == 362


def test_fixture_101_category_belongs_to_work_categories_seed():
    for record in REFERENCE_101:
        if "category" not in record:
            continue
        assert record["category"] in WORK_CATEGORIES_SEED_CODES, (
            f"код статьи {record['category']!r} (строка {record['title']!r}) "
            "не найден в WORK_CATEGORIES_SEED миграции 0005"
        )


def test_fixture_101_category_oracle_rejects_stand_primary_key():
    """Негативный вход: стендовый `id` листа «Разметка» («26823» — строка 1)
    форматом «точки между цифрами» прошёл бы (это просто цифры без точек —
    `re.fullmatch(r"\\d+(\\.\\d+)*", ...)` его пропускает), но оракулом
    WORK_CATEGORIES_SEED обязан быть отвергнут: PK стенда — не код
    классификатора ни одной статьи.
    """
    stand_pk = "26823"
    assert re.fullmatch(r"\d+(\.\d+)*", stand_pk)
    assert stand_pk not in WORK_CATEGORIES_SEED_CODES

