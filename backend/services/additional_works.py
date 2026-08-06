"""Расшивка «Сведений по дополнительным работам» (спека Ф4, `additional-works-design.md`).

Парсер `2.0.0` больше не кладёт агрегатную строку допработ в `positions` — её
сумма живёт расшитой по строкам текста из блока «Дополнительная информация».
Модуль разбирает эти строки, резолвит ссылку на раздел в статью классификатора
и (в Task 4) строит записи `estimate_additional_works`.

Модуль чистый, по образцу `category_resolution.py`: ни `Session`, ни ORM-
объектов на входе, ни записи в БД. Вход — уже готовый JSON парсера и уже
готовый план резолва Ф3 (`ProposalResolution`), выход — данные и тексты
предупреждений, а не побочные эффекты. Поэтому один экземпляр модуля безопасно
обслуживает все предложения сметы, и алгоритм проверяется юнит-тестами без БД
и без xlsx.

Ключевое решение (спека §2.4): ключ блока выбирается ТОЧНЫМ сравнением
нормализованных форм — подстрочный поиск отвергнут явно (спека §3), потому что
молча принял бы незнакомое поле за источник денег: у чужого ключа неизвестен
ни формат значения, ни то, чьи это деньги. Нестрогий шаблон
(`SVEDENIYA_KEY_LOOSE_RE`) в выборе источника денег НЕ участвует — он только
диагностика: если точного ключа нет, а шаблону соответствует что-то другое,
текст не берётся, но выдаётся громкое предупреждение с фактическим ключом.
Форма выгрузки в этом проекте уже менялась однажды (дефект итоговых строк,
спека §1.4), поэтому молча потерять расшивку из-за переименованного поля так
же плохо, как молча принять чужое поле за источник денег.
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from parser.constants import JSON_KEY_CHAPTER_NUMBER
from services.category_resolution import ProposalResolution, RowKind, _examples

#: Ключ блока «Дополнительная информация», в котором лежит расшивка. Замерен
#: во всех четырёх входах и совпадает побайтово — поэтому сравнение ТОЧНОЕ,
#: а не по подстроке: похожий незнакомый ключ не имеет права молча стать
#: денежным источником.
SVEDENIYA_KEY = "Сведения по дополнительным работам"

#: Нестрогий шаблон — ТОЛЬКО чтобы предупредить о похожем ключе, который не
#: совпал точно. В выборе источника денег не участвует.
SVEDENIYA_KEY_LOOSE_RE = re.compile(r"(?i)сведени.*дополнительн")

#: Одно регулярное выражение на строку текста «Сведений» (спека §2.4). Ссылка
#: на раздел НЕОБЯЗАТЕЛЬНА, сумма и «руб» — обязательны. `\u00a0` — неразрывный
#: пробел, записанный escape-последовательностью, а не невидимым байтом в
#: исходнике: `re` разбирает `\uXXXX` при компиляции, независимо от того, что
#: сама строка Python — raw. (`\s` сам по себе тоже покрывает NBSP в Python,
#: но явная escape-последовательность читается в исходнике, а не угадывается.)
LINE_RE = re.compile(
    r"^\s*(?:(?P<ref>\d+(?:\.\d+)*\.?)\s+)?(?P<title>.+?)\s*[-–—]\s*"
    r"(?P<amount>[\d\s\u00a0]+(?:[.,]\d+)?)\s*руб\.?\s*$"
)


@dataclass(frozen=True)
class ParsedLine:
    """Одна разобранная строка текста «Сведений» (спека §2.4)."""

    ordinal: int  # 1-based, в порядке текста, считает только разобранные строки
    ref: str | None  # нормализованный номер: пробелы схлопнуты, завершающая точка снята
    title: str  # обрезан по краям, непустой
    amount: Decimal  # >= 0
    raw_line: str  # исходная строка ровно как в файле


@dataclass(frozen=True)
class AdditionalWorkRow:
    """Будущая строка `estimate_additional_works` (спека §2.3, миграция 0007).

    Собирается матрицей состояний в Task 4 (`build_rows`) — здесь только форма
    данных, чтобы интерфейс модуля был виден целиком уже сейчас.
    """

    ordinal: int
    chapter_ref_raw: str | None
    title: str
    total_amount: Decimal
    work_category_id: int | None
    raw_line: str | None


@dataclass(frozen=True)
class ProposalAdditionalWorks:
    """Итог допработ одного предложения: строки плюс предупреждения (Task 4)."""

    rows: tuple[AdditionalWorkRow, ...]
    warnings: tuple[str, ...]


def _normalize_key(value: Any) -> str:
    """Ключ блока «Дополнительная информация» к сравнимой форме.

    Обрезка и схлопывание пробельных последовательностей делает один проход
    `str.split()` (он же игнорирует пустые куски по краям), затем `casefold` —
    он строже `lower()` для не-ASCII букв. Сравнение и `SVEDENIYA_KEY`, и
    фактического ключа идёт через эту же функцию — норма одна для обеих сторон.
    """
    return " ".join(str(value).split()).casefold()


_SVEDENIYA_KEY_NORM = _normalize_key(SVEDENIYA_KEY)


def svedeniya_text(additional_info: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Текст расшивки и, отдельно, предупреждение о похожем-но-не-том ключе.

    Ключ выбирается ТОЧНЫМ сравнением нормализованных форм с `SVEDENIYA_KEY`.
    Если точного нет, а нестрогому шаблону что-то соответствует, текст НЕ
    берётся, но выдаётся предупреждение с фактическим ключом (спека §2.4,
    §2.9).

    Отсутствие ключа и пустое значение — ОДНО состояние (замер §1.1: в трёх
    файлах из четырёх ключ есть, но пуст), и обе ветки возвращают `(None,
    None)` без разбора, какая именно из них случилась. Разбор нужен только
    тогда, когда точного ключа нет: тогда, и только тогда, стоит посмотреть,
    не подошёл ли похожий.
    """
    for key, value in additional_info.items():
        if _normalize_key(key) == _SVEDENIYA_KEY_NORM:
            text = "" if value is None else str(value)
            return (text, None) if text.strip() else (None, None)

    similar = [key for key in additional_info if SVEDENIYA_KEY_LOOSE_RE.search(str(key))]
    if not similar:
        return None, None
    warning = (
        f"Ключ «{_examples(similar)}» похож на ожидаемый «{SVEDENIYA_KEY}», но не "
        "совпадает с ним точно: текст расшивки допработ не прочитан, его деньги "
        "остались нераспределёнными."
    )
    return None, warning


def _normalize_number(value: Any) -> str:
    """Номер к сравнимой форме: схлопывание пробелов, снятие ОДНОЙ завершающей
    точки (`«3.2.2.»` → `«3.2.2»`).

    Функция ОДНА и используется с ОБЕИХ сторон сравнения — и для ссылки из
    текста «Сведений» (`ParsedLine.ref`), и для номера раздела, взятого из
    payload'а парсера (`categories_by_chapter_number`). Разные нормализации на
    двух концах одного сравнения разошлись бы молча.
    """
    collapsed = " ".join(str(value).split())
    return collapsed[:-1] if collapsed.endswith(".") else collapsed


def parse_lines(text: str) -> tuple[list[ParsedLine], list[str]]:
    """Разбор строк текста «Сведений» построчно (спека §2.4).

    Пустые строки пропускаются и НЕ считаются нечитаемыми: деление на строки
    идёт по `str.splitlines()`, а не по `LINE_RE`, поэтому пустая строка сама
    по себе ничего не портит.

    Строка становится нечитаемой в трёх независимых случаях, и ни один не
    роняет импорт исключением:

    - `LINE_RE` не совпадает вовсе (нет «руб» или между тире и «руб» нет ни
      одного символа из допустимого набора — то есть суммы нет совсем);
    - `LINE_RE` совпадает, но группа `amount` не содержит НИ ОДНОЙ цифры —
      только пробелы/NBSP (шаблон это допускает: `[\\d\\s\\u00a0]+` требует
      один-или-больше символ из набора, а не обязательно цифру). После чистки
      такая сумма превращается в пустую строку, и `Decimal("")` бросает
      `InvalidOperation` — перехватывается здесь, наружу не уходит;
    - название после обрезки пустое.

    Деньги нечитаемой строки не теряются: они восстанавливаются позже через
    нераспределённый остаток (спека §2.6) — этот модуль лишь называет строку
    нечитаемой и возвращает её сырьё.
    """
    parsed: list[ParsedLine] = []
    unreadable: list[str] = []
    ordinal = 0
    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        match = LINE_RE.match(raw_line)
        if match is None:
            unreadable.append(raw_line)
            continue
        title = match.group("title").strip()
        if not title:
            unreadable.append(raw_line)
            continue
        cleaned_amount = "".join(match.group("amount").split()).replace(",", ".")
        try:
            amount = Decimal(cleaned_amount)
        except InvalidOperation:
            unreadable.append(raw_line)
            continue
        raw_ref = match.group("ref")
        ordinal += 1
        parsed.append(
            ParsedLine(
                ordinal=ordinal,
                ref=_normalize_number(raw_ref) if raw_ref else None,
                title=title,
                amount=amount,
                raw_line=raw_line,
            )
        )
    return parsed, unreadable


def categories_by_chapter_number(
    positions: Mapping[str, Any], resolution: ProposalResolution
) -> dict[str, set[int | None]]:
    """Словарь «нормализованный номер раздела → множество статей» (спека §2.5).

    Кандидаты — ТОЛЬКО строки-разделы: `row.kind is RowKind.CHAPTER` берётся
    из уже готового плана резолва Ф3, а не заводится вторым предикатом поверх
    `is_chapter` (Global Constraint плана: ни одного второго предиката для уже
    выраженного понятия). Значение — множество `work_category_id`, оно МОЖЕТ
    содержать `None` (раздел без статьи, например строка лота) — именно это
    делает возможной ветку «кандидат без статьи» в `resolve_ref`. Построение —
    целиком в памяти, по одному предложению: ни одного SQL-join по номеру
    раздела (Global Constraint плана).
    """
    by_number: dict[str, set[int | None]] = defaultdict(set)
    for key, row in resolution.rows.items():
        if row.kind is not RowKind.CHAPTER:
            continue
        number = _normalize_number(positions[key].get(JSON_KEY_CHAPTER_NUMBER))
        by_number[number].add(row.work_category_id)
    return dict(by_number)


def resolve_ref(
    ref: str | None, by_number: Mapping[str, set[int | None]]
) -> tuple[int | None, str | None]:
    """Единогласие кандидатов по номеру раздела (спека §2.5).

    Ссылки нет вовсе — резолва не было, и причины отказа тоже нет (это не то
    же самое, что «нет кандидатов»): различие важно для предупреждений — по
    строке без ссылки отдельного warning'а не заводится.

    ПОРЯДОК ПРОВЕРОК ВАЖЕН: «есть кандидат без статьи» проверяется РАНЬШЕ
    «статьи различаются». Множество вида `{None, 7}` удовлетворяет ОБОИМ
    условиям одновременно — например, когда номер принадлежит и строке лота
    без статьи, и настоящему разделу с ней (fixture Task 6). Поменять порядок
    местами незаметно изменило бы причину отказа в предупреждении.
    """
    if ref is None:
        return None, None
    candidates = by_number.get(ref)
    if not candidates:
        return None, "нет кандидатов"
    if None in candidates:
        return None, "кандидат без статьи"
    if len(candidates) > 1:
        return None, "статьи различаются"
    return next(iter(candidates)), None
