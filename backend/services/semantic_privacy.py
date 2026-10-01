"""Проверка перед отправкой: словарь ПДн и поиск совпадений в теле запроса
(спека `2026-09-28-semantic-suggestions-design.md` §2.3, §1.7).

Задача 4 фичи «Семантические предложения»
(`docs/superpowers/plans/2026-09-28-semantic-suggestions.md`). Вход не
переписывается (решение 24.09): словарь и поиск совпадений только
ОБНАРУЖИВАЮТ запрещённое — задание, содержащее совпадение, уходит в
`privacy_hold` без отправки (потребитель — исполнитель захвата, worker,
задача 8; решения `admin` — задача 10/12).

Словарь строится из базы при каждой попытке захвата (`build_privacy_dictionary`)
и держит полные имена объектов и подрядчиков (без организационной формы и
кавычек, спека §2.3) и номера договоров и тендеров как есть, только с
приведением пробелов и регистра. Производных слов нет — измерение 24.09
(спека §1.7) показало, что они дают только ложные срабатывания.

`find_privacy_matches` проверяет ВСЁ тело, которое уйдёт провайдеру: строку
контекста (`where = "context"`), каждую строку семьи блока кандидатов
(`where = "family:<id>"`, id — из начала строки, контракт `family_line`) и всё
остальное содержимое `system` — текст промпта и заголовок блока семей
(`where = "prompt"`, решение задачи 4): промпт тоже часть тела, но ни к
строке контекста, ни к конкретной семье совпадение в нём не приписать.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from models import Contract, Contractor, ObjectModel, Tender
from services import semantic_request
from services.semantic_request import RenderedRequest

#: Организационные формы и приставки, снимаемые `normalize_org_name` (спека
#: §2.3, решение задачи 4): только целыми словами без учёта регистра — форма,
#: вложенная в слово (`АОРТА`), не снимается.
_ORG_FORMS_CASEFOLD = frozenset(
    form.casefold() for form in ("ООО", "АО", "ПАО", "ЗАО", "ОАО", "ИП", "ТОО", "LLP", "ГК", "СЗ")
)

#: Кавычки всех видов (и апостроф) — заменяются ПРОБЕЛОМ, а не удаляются:
#: `ООО«Ромашка»` без пробела перед кавычкой иначе давал
#: бы слипшийся токен `ооoромашка`, в котором форма уже не отдельное слово и
#: не снимается, а `O'Brien` без замены остаётся одним токеном и не находится
#: как два слова в тексте. Символы, а не пары, поэтому незакрытая или
#: разнотипная пара тоже заменяется.
_QUOTE_CHARS = "«»“”„\"'‘’"
_QUOTE_TO_SPACE = {ord(ch): " " for ch in _QUOTE_CHARS}

_WHITESPACE_RE = re.compile(r"\s+")

#: Слово имени — прогон букв/цифр/подчёркиваний; всё прочее (пробел, кавычка,
#: запятая, скобка, дефис) разделяет слова.
_WORD_RUN_RE = re.compile(r"\w+")


def _replace_quotes_with_space(text: str) -> str:
    """Общий первый шаг номеров и поиска: та же замена
    кавычек пробелом применяется и к номеру договора или тендера в словаре
    (`_normalize_plain`; имена объектов и подрядчиков `normalize_org_name` делит
    по любому небуквенному символу, кавычки там — разделитель), и к тексту тела,
    который запись ищет (`_scan`) — иначе имя с апострофом
    или слитной кавычкой держится словарём как несколько слов, а текст под
    поиском видит их слипшимися, и `\\s+` между словами записи не совпадает
    ни с чем."""
    return text.translate(_QUOTE_TO_SPACE)


def normalize_org_name(title: str) -> str:
    """Полное имя объекта или подрядчика → каноническая форма словаря (спека
    §2.3): имя режется на слова по ЛЮБОМУ не-словесному символу (пробел,
    кавычка, запятая, скобка, дефис), форма ООО/АО/ПАО/ЗАО/ОАО/ИП/ТОО/LLP или
    приставка ГК/СЗ отбрасывается, только если она — слово целиком (без учёта
    регистра), остальные слова склеиваются одним пробелом. `ООО «Каркас
    Монолит»` → `каркас монолит`, `Ромашка, ТОО` → `ромашка`; форма, вложенная
    в слово (`АОРТА`, `АО1`), не снимается."""
    kept_tokens = [
        token for token in _WORD_RUN_RE.findall(title) if token.casefold() not in _ORG_FORMS_CASEFOLD
    ]
    return " ".join(kept_tokens).casefold()


def _normalize_plain(text: str) -> str:
    """Номера договоров и тендеров (спека §2.3): НЕ через `normalize_org_name`
    (в номере нет организационной формы, а дефисы и цифры номера — часть
    значения, а не текста для отсечения) — но кавычки/апостроф заменяются ТЕМ
    ЖЕ пробелом, что и в `normalize_org_name`: без
    этого шага номер с кавычкой в записи словаря не совпал бы с тем же
    номером, написанным в тексте, — искомый текст в `_scan` уже проходит эту
    замену, а запись словаря обязана быть построена той же нормализацией.
    После неё — свёртка пробелов, обрезка краёв, регистр."""
    despaced = _replace_quotes_with_space(text)
    collapsed = _WHITESPACE_RE.sub(" ", despaced).strip()
    return collapsed.casefold()


@dataclass(frozen=True)
class PrivacyEntry:
    """Одна запись словаря — нормализованный текст и его источник (спека
    §2.3)."""

    kind: str  # object | contractor | contract | tender
    text: str  # нормализованная форма


@dataclass(frozen=True)
class PrivacyDictionary:
    """Словарь на момент захвата: записи отсортированы по `(kind, text)`,
    `digest` — sha256 канонической сериализации этого же отсортированного
    списка пар (решение задачи 4: не зависит от порядка строк в базе, меняется
    при добавлении подрядчика)."""

    entries: tuple[PrivacyEntry, ...]
    digest: str


@dataclass(frozen=True)
class PrivacyMatch:
    """Одно совпадение словаря с телом запроса (спека §2.3, решение задачи 4):
    `text` — нормализованный текст записи словаря (стабилен между захватом и
    решением `admin`), `where` — место совпадения."""

    text: str
    kind: str
    where: str  # "context" | "family:<id>" | "prompt"


def _dictionary_digest(pairs: list[tuple[str, str]]) -> str:
    payload = [[kind, text] for kind, text in pairs]
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_privacy_dictionary(db: Session) -> PrivacyDictionary:
    """Строит словарь по четырём источникам — по одному запросу на источник
    (решение задачи 4: не растёт с числом строк, без запроса на строку).
    Запись, ставшая пустой после нормализации, в словарь не входит; дубли
    `(kind, text)` (в том числе между источниками) сливаются в одну запись."""
    object_titles = db.execute(sa.select(ObjectModel.title)).scalars().all()
    contractor_titles = db.execute(sa.select(Contractor.title)).scalars().all()
    contract_numbers = db.execute(sa.select(Contract.contract_number)).scalars().all()
    tender_numbers = db.execute(sa.select(Tender.tender_number)).scalars().all()

    pairs: set[tuple[str, str]] = set()
    for title in object_titles:
        text = normalize_org_name(title)
        if text:
            pairs.add(("object", text))
    for title in contractor_titles:
        text = normalize_org_name(title)
        if text:
            pairs.add(("contractor", text))
    for number in contract_numbers:
        text = _normalize_plain(number)
        if text:
            pairs.add(("contract", text))
    for number in tender_numbers:
        text = _normalize_plain(number)
        if text:
            pairs.add(("tender", text))

    ordered_pairs = sorted(pairs)
    entries = tuple(PrivacyEntry(kind=kind, text=text) for kind, text in ordered_pairs)
    return PrivacyDictionary(entries=entries, digest=_dictionary_digest(ordered_pairs))


#: Строка семьи блока кандидатов начинается с `f"{id}. "` (контракт
#: `services.semantic_request.family_line`) — id читается с начала строки.
_FAMILY_LINE_ID_RE = re.compile(r"^(\d+)\. ")


#: Между соседними словами имени допустим любой не-словесный промежуток и
#: организационные формы целыми токенами: `normalize_org_name` снимает формы
#: в любом месте имени и считает разделителем любой не-словесный символ, а в
#: тексте остаются и формы, и знаки (`Ромашка (ООО), Сервис`). `ООО1` формой не
#: считается: после формы обязан идти не-словесный символ.
_ORG_FORMS_ALTERNATION = "|".join(re.escape(form) for form in sorted(_ORG_FORMS_CASEFOLD))
_WORD_GAP = rf"\W+(?:(?:{_ORG_FORMS_ALTERNATION})\W+)*"

#: Записи имён (объекты, подрядчики) ищутся с пропуском форм и знаков;
#: номера договоров и тендеров — буквально, слова через `\s+`.
_ORG_KINDS = frozenset({"object", "contractor"})


def _entry_pattern(entry_text: str, kind: str) -> re.Pattern[str]:
    """Регулярное выражение одной записи словаря, по границе слова без учёта
    регистра (спека §2.3) — `(?<!\\w)`/`(?!\\w)` по обе стороны всей фразы.
    Имя (`object`, `contractor`): слова записи через `_WORD_GAP`. Номер
    (`contract`, `tender`): слова через `\\s+`, формы и знаки в номере — часть
    значения. Кавычки вокруг фразы совпадению не мешают: это не символы `\\w`."""
    if kind in _ORG_KINDS:
        words = _WORD_RUN_RE.findall(entry_text)
        gap = _WORD_GAP
    else:
        words = entry_text.split(" ")
        gap = r"\s+"
    body = gap.join(re.escape(word) for word in words)
    return re.compile(rf"(?<!\w){body}(?!\w)")


def _matches_any(entry: PrivacyEntry, prepared_texts: tuple[str, ...]) -> bool:
    pattern = _entry_pattern(entry.text, entry.kind)
    return any(pattern.search(text) is not None for text in prepared_texts)


def _scan(
    dictionary: PrivacyDictionary, texts: tuple[str, ...], where: str
) -> list[PrivacyMatch]:
    """Одно совпадение на запись словаря, найденную хотя бы в одном из
    `texts` — порядок результата уже верен, потому что `dictionary.entries`
    отсортирован по `(kind, text)` и перебирается по порядку РОВНО один раз.
    Текст перед поиском проходит ТУ ЖЕ замену кавычек на пробел
    (`_replace_quotes_with_space`), что и запись словаря — иначе имя с
    апострофом или слитной кавычкой в тексте не совпадёт с записью, разбитой
    на несколько слов через `\\s+`."""
    prepared = tuple(_replace_quotes_with_space(text).casefold() for text in texts)
    return [
        PrivacyMatch(text=entry.text, kind=entry.kind, where=where)
        for entry in dictionary.entries
        if _matches_any(entry, prepared)
    ]


def find_privacy_matches(
    dictionary: PrivacyDictionary, rendered: RenderedRequest
) -> tuple[PrivacyMatch, ...]:
    """Совпадения словаря во ВСЁМ теле, которое уйдёт провайдеру (спека §2.3).
    Блок семей опознаётся по тексту, начинающемуся с
    `semantic_request.FAMILY_BLOCK_HEADER`, а не по индексу блока `system`
    (решение задачи 4) — правка рендера (задача 2) сама уже использует ту же
    константу; читается через МОДУЛЬ, а не отдельным импортом имени, чтобы
    заголовок оставался ровно тем же значением, что видел рендер. Результат
    упорядочен: `"context"`, затем `"prompt"`, затем семьи по возрастанию
    числового id; внутри места — по `(kind, text)` (решение задачи 4)."""
    header = semantic_request.FAMILY_BLOCK_HEADER
    system_blocks = rendered.body["messages"][0]["content"]
    user_text = rendered.body["messages"][1]["content"]

    prompt_texts: list[str] = []
    family_lines: dict[int, str] = {}

    for block in system_blocks:
        text = block.get("text", "")
        if text.startswith(header):
            # Заголовок блока семей — текст промпта в широком смысле (спека
            # §2.3): часть system, которую ни к строке, ни к семье не
            # приписать. Строки семей идут ПОСЛЕ заголовка, каждая — отдельно.
            prompt_texts.append(header)
            rest = text[len(header):]
            for line in rest.split("\n"):
                if not line:
                    continue
                id_match = _FAMILY_LINE_ID_RE.match(line)
                if id_match is None:
                    raise ValueError("строка семьи обязана начинаться с id (family_line)")
                family_lines[int(id_match.group(1))] = line
        else:
            prompt_texts.append(text)

    result: list[PrivacyMatch] = _scan(dictionary, (user_text,), "context")
    result.extend(_scan(dictionary, tuple(prompt_texts), "prompt"))
    for family_id in sorted(family_lines):
        result.extend(_scan(dictionary, (family_lines[family_id],), f"family:{family_id}"))

    return tuple(result)
