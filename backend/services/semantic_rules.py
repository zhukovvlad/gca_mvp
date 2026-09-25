"""Семантические правила: вид строки, роль имени, словарь мест с версией.

Задача 3 фичи «Семьи и контексты»
(`docs/superpowers/plans/2026-09-22-catalog-families.md`,
`docs/superpowers/specs/2026-09-22-catalog-families-design.md` §1.9, §1.14,
§2.6). Модуль — чистые функции: НИКАКОГО обращения к БД, `Session` не
импортируется. Правило обязано быть исполнимым на строке из файла, иначе
разовый проход задачи 11 не сможет прогнать его на 100 335 позициях без
кластера.

Нормализация текста — вызовом `normalize_job_title_with_lemmatization`
(`backend/parser/sanitize_text.py`), а не переизобретением: второго
представления строки в проекте не заводится (спека §2.4).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from models import ComparabilityReason, NameRole, SemanticKind
from parser.sanitize_text import normalize_job_title_with_lemmatization

#: Версия словаря признаков места — аналог `NORM_VERSION`
#: (`services/matching.py:67`, приём «константа рядом со своим правилом»).
#: Инкремент — смена состава `PLACE_TOKENS`/`GENERIC_WORK_TOKENS`; версия,
#: которой поставлена роль, ложится на контекст (спека §2.6), поэтому смена
#: словаря обнаружима запросом, и пересчёт адресуется контекстам с прежней
#: версией, а не всему каталогу.
PLACE_DICTIONARY_VERSION = 1

#: Признаки места (спека §2.6): секция, корпус, этап, урбан-блок, паркинг,
#: уровень, этаж, зона. Каждая запись — уже ЛЕММАТИЗИРОВАННАЯ форма, как её
#: возвращает `normalize_job_title_with_lemmatization`. «Урбан-блок» хранится
#: как фраза из двух слов: дефис внутри слова НЕ переживает лемматизацию —
#: токенизатор spaCy делит дефисное слово на два токена и отбрасывает сам
#: дефис как пунктуацию («урбан-блок» → «урбан» + «блок»), поэтому у
#: `normalize_job_title_with_lemmatization("Урбан-блок 4")` результат
#: — «урбан блок 4» (пробелом, без дефиса).
#:
#: Проверяется ЦЕЛОЙ ФРАЗОЙ (после отбрасывания числового/буквенного индекса
#: экземпляра места — см. `_place_phrase`), а не членством отдельных токенов:
#: иначе одинокое «блок» ловило бы «блок питания» и подобные несвязанные
#: имена, у которых слово «блок» не значит место.
PLACE_TOKENS: frozenset[str] = frozenset(
    {
        "секция",
        "корпус",
        "этап",
        "урбан блок",
        "паркинг",
        "уровень",
        "этаж",
        "зона",
    }
)

#: Признаки рода изделия/поверхности работы без состава (спека §1.6, §2.6).
#: Каждая запись — лемматизированная форма, как её отдаёт
#: `normalize_job_title_with_lemmatization`
#: («Полы:» → «пол», «Стены:» → «стена», «Потолок:» → «потолок»,
#: «Светильники» → «светильник», «согласно ДП» → «согласно дп» — пятый
#: пример спеки §2.6, спека сильнее плана в перечне примеров).
GENERIC_WORK_TOKENS: frozenset[str] = frozenset(
    {"пол", "стена", "потолок", "светильник", "согласно дп"}
)

#: Разделы, которые сами по себе не входят ни в `PLACE_TOKENS`, ни в
#: `GENERIC_WORK_TOKENS`, но работу не называют. «Прочее» — спека §1.10:
#: раздел назван в числе начал, которые не называют работу (наряду со
#: «Стены», «Пол», «Секция» — те уже покрыты словарями выше). Раздел,
#: СВОДИМЫЙ ЦЕЛИКОМ к слову из этого множества, «рабочим» не считается (см.
#: `_is_working_chapter`).
NON_WORK_CHAPTER_TOKENS: frozenset[str] = frozenset({"прочее"})

#: Нормализованная единица «компл»: `crud/units.py` —
#: `ALIASES_SEED[normalize_unit_key("компл")] == "SET"`, тот же код несёт
#: `UNITS_SEED` (справочник статический, без БД). Правило вида: `SYSTEM` тогда
#: и только тогда, когда `unit_norm == SYSTEM_UNIT_NORM`.
SYSTEM_UNIT_NORM = "SET"

#: Разбивает лемматизированный текст на токены ПО ПРОБЕЛАМ И ДЕФИСАМ разом:
#: «секция-1» (дефис без пробелов — spaCy не делит такой токен на письме,
#: в отличие от «урбан-блок», где дефис между двумя буквенными словами spaCy
#: отбрасывает при токенизации) иначе остался бы одним неразрезанным токеном
#: «секция-1», и индекс «1» не нашёлся бы для отбрасывания.
_TOKEN_SPLIT_RE = re.compile(r"[\s-]+")

#: Разделитель смешанного имени «место — работа» («Корпус 1 - Экран
#: декоративный…», спека §1.5, §2.6): дефис, короткое или длинное тире, в
#: отдельных пробелах с обеих сторон. Разделитель внутри одного слова
#: («урбан-блок») этот шаблон не находит — там пробелов вокруг дефиса нет.
_MIXED_NAME_SEP_RE = re.compile(r" [-–—] ")


def _is_index_token(token: str) -> bool:
    """Индекс конкретного экземпляра места — не словарное слово.

    Состоит ТОЛЬКО из цифр и/или не более чем одной буквы, в любом порядке:
    «1», «1а» (Секция 1А), «а1» (Зона А1), «а»/«б» (литера зоны или корпуса,
    «Корпус Б»). Ведущий минус («Уровень -1») до этой функции не доходит:
    `_TOKEN_SPLIT_RE` режет и по дефису тоже, поэтому минус потребляется как
    разделитель между токенами, а не остаётся частью токена — вызывающий код
    (`_place_phrase`) никогда не передаёт сюда токен с ведущим «-» и не
    передаёт пустую строку (пустые токены отфильтрованы до вызова). Токен
    «1_2» («Секция 1_2») индексом не считается: `_` — не цифра и не буква,
    а `digits + letters == len(token)` требует, чтобы токен целиком состоял
    только из них. Многобуквенный токен («этаж», «достоевского») индексом не
    считается ни при каком числе цифр рядом — это исключает вход «Секция
    Достоевского» из зачёта: слово после места должно остаться словом, а не
    индексом.
    """
    letters = sum(ch.isalpha() for ch in token)
    digits = sum(ch.isdigit() for ch in token)
    return letters <= 1 and digits + letters == len(token)


@dataclass(frozen=True)
class NameRoleOutcome:
    """Результат классификации роли имени контекста (спека §2.6)."""

    role: str
    """`NameRole`: `WORK`, `LOCATION_ONLY` либо `GENERIC_WORK`."""

    location: str | None
    """Префикс места у `LOCATION_ONLY` (чистого либо смешанного имени);
    `None` у `WORK` и `GENERIC_WORK`."""

    work_title: str | None
    """Имя работы: остаток смешанного имени, имя ближайшего рабочего раздела
    цепочки, само наименование строки (`WORK`/`GENERIC_WORK`) либо `None`,
    если рабочего раздела над чистым `LOCATION_ONLY` не нашлось."""

    comparability_reason: str | None
    """`ComparabilityReason` либо `None`, если строка сравнима."""


def _normalize(text: str) -> str:
    """Лемматизированная форма текста; пустая строка вместо `None`."""
    return normalize_job_title_with_lemmatization(text) or ""


def _place_phrase(text: str) -> str | None:
    """Словарная фраза места, если ВЕСЬ текст сводится к ней, иначе `None`.

    Лемматизирует текст, разбивает на токены по пробелам и дефисам
    (`_TOKEN_SPLIT_RE`), затем отбрасывает индекс экземпляра (`_is_index_token`)
    С ЛЮБОГО КОНЦА — с начала («3 этаж» → «этаж») и с конца («Секция 1» →
    «секция», «Зона А1» → «зона», «Секция-1» → «секция»). Остаток сверяется с
    `PLACE_TOKENS` ЦЕЛИКОМ, а не по отдельным токенам — иначе одинокое «блок»
    из «урбан блок» ловило бы «блок питания», а «паркинг» внутри «Оборудование
    службы безопасности паркинга» превращал бы работу в место.
    """
    tokens = [t for t in _TOKEN_SPLIT_RE.split(_normalize(text)) if t]
    while tokens and _is_index_token(tokens[0]):
        tokens.pop(0)
    while tokens and _is_index_token(tokens[-1]):
        tokens.pop()
    phrase = " ".join(tokens)
    return phrase if phrase and phrase in PLACE_TOKENS else None


def _remainder_has_work_word(remainder: str) -> bool:
    """True, если в остатке смешанного имени есть хоть одно слово, которое
    НЕ индекс экземпляра места.

    Голый индекс остатком-работой не считается: «Секция 1 - 2» и «Уровень -
    1» целиком сводятся к месту (весь текст — «секция»/«уровень» плюс два
    отброшенных с хвоста индекса), а не к «месту 1» с работой «2»/«1» — «2»
    и «1» сами по себе имя работы не называют.
    """
    tokens = [t for t in _TOKEN_SPLIT_RE.split(_normalize(remainder)) if t]
    return any(not _is_index_token(t) for t in tokens)


def _is_generic_work(text: str) -> bool:
    """True, если весь текст — ровно одна словарная форма рода изделия/поверхности."""
    return _normalize(text) in GENERIC_WORK_TOKENS


def _is_working_chapter(chapter: str) -> bool:
    """«Рабочий» раздел (спека §2.6): называет операцию или изделие, а не
    место — определён здесь ЧЕРЕЗ ИСКЛЮЧЕНИЕ, тем же словарным аппаратом, что
    и роль имени, а не более глубоким разбором текста раздела:

    - НЕ место (`_place_phrase` не даёт результата);
    - НЕ род изделия/поверхности без состава (`_is_generic_work`) — раздел
      «Стены:» не работа, хоть и не место;
    - НЕ «Прочее» (`NON_WORK_CHAPTER_TOKENS`, спека §1.10) — раздел, прямо
      названный спекой в числе не называющих работу.
    """
    if _place_phrase(chapter) is not None:
        return False
    if _is_generic_work(chapter):
        return False
    return _normalize(chapter) not in NON_WORK_CHAPTER_TOKENS


def classify_kind(unit_norm: str) -> str:
    """Вид строки (`SemanticKind`) по нормализованной единице (спека §1.9, §2.6).

    Правило: `SYSTEM`, если и только если `unit_norm == SYSTEM_UNIT_NORM`
    (компл → `SET`), иначе `WORK`. На эталоне 101 строки
    (`backend/tests/data/semantic_reference_101.json`) даёт 98 совпадений из
    101; три названных исключения — решения человека о конкретной строке, а
    не промах правила (спека §1.14). Правило никогда не возвращает `UNKNOWN`
    — эту отметку ставит оператор вручную для строки, вид которой неизвестен
    ему самому.

    Args:
        unit_norm: нормализованный код единицы (`ResolvedUnit.unit_norm`,
            `backend/services/unit_resolution.py`) — НЕ исходный текст
            единицы из файла.
    """
    if unit_norm == SYSTEM_UNIT_NORM:
        return SemanticKind.SYSTEM.value
    return SemanticKind.WORK.value


def classify_name_role(title: str, *, chapter_chain: tuple[str, ...]) -> NameRoleOutcome:
    """Роль имени строки (`NameRole`) по словарю мест и цепочке разделов (спека §2.6).

    Порядок проверки:

    1. **Смешанное имя** («Корпус 1 - Экран декоративный…»): если в исходном
       (нелемматизированном) `title` есть разделитель «пробел-дефис/тире-
       пробел» (`_MIXED_NAME_SEP_RE`: `-`, `–`, `—`), текст до него целиком
       сводится к словарной фразе места (`_place_phrase`), а после разделителя
       остаётся непустой текст, В КОТОРОМ ЕСТЬ ХОТЯ БЫ ОДНО НЕ-ИНДЕКСНОЕ
       СЛОВО (`_remainder_has_work_word`), — имя разводится: `location` —
       префикс, `work_title` — остаток. `comparability_reason = None`: работа
       уже названа в самом имени, дополнительных сведений не требуется.
       Разделитель ищется в СЫРОМ тексте, а не в лемматизированном: лемматизация
       съедает одиночный дефис/тире в пробелах (пунктуация, `token.is_punct`), и
       восстановить границу разреза по нормализованной строке было бы нечем.
       Пустой остаток («Корпус 1 - ») и остаток из одного голого индекса
       («Секция 1 - 2» → остаток «2», «Уровень - 1» → остаток «1») в это
       условие не проходят — такой вход уходит в проверку 2 как обычное
       чистое имя: голый индекс работой не является, а весь текст целиком
       (включая дефис/тире — пунктуацию, которую лемматизация съедает, — и
       оба числа, которые `_place_phrase` отбрасывает как индекс с хвоста)
       сводится к словарной фразе места.
    2. **Чистое место**: если весь `title` целиком сводится к словарной фразе
       места — `location = title`. Работа берётся из БЛИЖАЙШЕГО (первого по
       `chapter_chain`, упорядоченной от ближайшего раздела к корню — тот же
       порядок, что задаёт `ChapterContext.chain` в задаче 4,
       `services/context_routing.py`) раздела, который «рабочий»
       (`_is_working_chapter`: не место, не род изделия без состава и не
       «Прочее» — спека §1.10, §2.6). Если такого раздела в цепочке нет —
       `work_title = None`, `comparability_reason = insufficient_description`.
    3. **Род изделия без состава** («Светильники», «Полы:», «Стены:»,
       «Потолок:», «согласно ДП»): если весь `title` — ровно одна словарная
       форма из `GENERIC_WORK_TOKENS`, — `role = GENERIC_WORK`, `location =
       None`, `work_title = title` (имя остаётся названием работы на уровне
       семьи, спека §1.6), `comparability_reason = insufficient_description`
       — безусловно: неизвестно на уровне варианта, одна это работа или
       несколько.
    4. Иначе — `role = WORK`, `location = None`, `work_title = title`,
       `comparability_reason = None`.

    Args:
        title: исходное наименование строки, БЕЗ предварительной
            нормализации вызывающим кодом — нормализация происходит внутри.
        chapter_chain: цепочка разделов позиции от БЛИЖАЙШЕГО раздела к
            корню (`chapter_chain[0]` — ближайший раздел над строкой,
            `chapter_chain[-1]` — корень); тот же порядок, что
            `ChapterContext.chain` в задаче 4.
    """
    sep_match = _MIXED_NAME_SEP_RE.search(title)
    if sep_match:
        prefix, remainder = title[: sep_match.start()], title[sep_match.end() :]
        if (
            remainder.strip()
            and _place_phrase(prefix) is not None
            and _remainder_has_work_word(remainder)
        ):
            return NameRoleOutcome(
                role=NameRole.LOCATION_ONLY.value,
                location=prefix.strip(),
                work_title=remainder.strip(),
                comparability_reason=None,
            )

    if _place_phrase(title) is not None:
        nearest_work_chapter = next(
            (chapter for chapter in chapter_chain if _is_working_chapter(chapter)),
            None,
        )
        if nearest_work_chapter is not None:
            return NameRoleOutcome(
                role=NameRole.LOCATION_ONLY.value,
                location=title.strip(),
                work_title=nearest_work_chapter,
                comparability_reason=None,
            )
        return NameRoleOutcome(
            role=NameRole.LOCATION_ONLY.value,
            location=title.strip(),
            work_title=None,
            comparability_reason=ComparabilityReason.insufficient_description.value,
        )

    if _is_generic_work(title):
        return NameRoleOutcome(
            role=NameRole.GENERIC_WORK.value,
            location=None,
            work_title=title.strip(),
            comparability_reason=ComparabilityReason.insufficient_description.value,
        )

    return NameRoleOutcome(
        role=NameRole.WORK.value,
        location=None,
        work_title=title.strip(),
        comparability_reason=None,
    )
