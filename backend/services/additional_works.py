"""Расшивка «Сведений по дополнительным работам» (спека Ф4, `additional-works-design.md`).

Парсер `2.0.0` больше не кладёт агрегатную строку допработ в `positions` — её
сумма живёт расшитой по строкам текста из блока «Дополнительная информация».
Модуль разбирает эти строки, резолвит ссылку на раздел в статью классификатора
и строит записи `estimate_additional_works`: `decide_owner` решает, какому
предложению сметы принадлежит расшивка (спека §2.2), а `build_rows` применяет
матрицу состояний одного предложения (спека §2.6).

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

from parser.constants import (
    JSON_KEY_CHAPTER_NUMBER,
    JSON_KEY_CONTRACTOR_ADDITIONAL_INFO,
    JSON_KEY_CONTRACTOR_ADDITIONAL_WORKS,
    JSON_KEY_CONTRACTOR_ITEMS,
    JSON_KEY_JOB_TITLE,
    JSON_KEY_LOTS,
    JSON_KEY_PROPOSALS,
    JSON_KEY_TOTAL,
    JSON_KEY_TOTAL_COST,
)
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

    Собирается матрицей состояний (`build_rows`, спека §2.6) — здесь только
    форма данных, которую строка получит перед записью в БД (Task 5).
    """

    ordinal: int
    chapter_ref_raw: str | None
    title: str
    total_amount: Decimal
    work_category_id: int | None
    raw_line: str | None


@dataclass(frozen=True)
class ProposalAdditionalWorks:
    """Итог допработ одного предложения: строки плюс предупреждения (`build_rows`,
    спека §2.6). Предупреждения здесь — ТОЛЬКО уровня предложения; предупреждения
    уровня сметы (владелец, «Сведения без строки») возвращает `decide_owner`."""

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


#: Причины отказа `resolve_ref` (спека §2.5). Именованы отдельно (спека
#: этапного разноса §2.5), чтобы `crud/round_unallocated.py` сравнивал с
#: именем, а не ретипировал строку: поведение `resolve_ref` не меняется ни на
#: символ, тексты — те же, что были литералами здесь.
REASON_NO_CANDIDATES = "нет кандидатов"
REASON_CANDIDATE_WITHOUT_ARTICLE = "кандидат без статьи"
REASON_AMBIGUOUS = "статьи различаются"


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
        return None, REASON_NO_CANDIDATES
    if None in candidates:
        return None, REASON_CANDIDATE_WITHOUT_ARTICLE
    if len(candidates) > 1:
        return None, REASON_AMBIGUOUS
    return next(iter(candidates)), None


@dataclass(frozen=True)
class OwnerDecision:
    """Кто из предложений сметы владеет текстом «Сведений» (спека §2.2).

    Текст «Сведений» — факт УРОВНЯ ЛИСТА, а не предложения (спека §1.5 факт 2):
    `get_additional_info` ищет первый маркер «Дополнительная информация» по
    всему листу и зовётся один раз на лот, поэтому у сметы с несколькими лотами
    ВСЕ предложения получают побайтово ОДИН И ТОТ ЖЕ текст. Расшить его в каждом
    предложении по отдельности значило бы задвоить (или растроить...) деньги
    между предложениями — тот же дефект двойного счёта, ради снятия которого
    феча и написана, только с другой стороны. Поэтому владелец решается ПО ВСЕЙ
    СМЕТЕ, ДО того, как какое-либо предложение начнёт строить записи.

    В проекте ровно одно предложение на лот (`AGENTS.md` §4), поэтому решение
    называет ключ ЛОТА, а не предложения, — это одно и то же в этом инварианте.

    Здесь НЕ решается: разбор текста на строки (`parse_lines`), резолв ссылки в
    статью (`resolve_ref` / `categories_by_chapter_number`) и сама запись строк
    `estimate_additional_works` (`build_rows`) — только то, чьему предложению
    разрешено применить расшивку к своему `T`.
    """

    owner_lot_key: str | None
    """Ключ лота-владельца, либо `None` — однозначного владельца нет (ни одной
    агрегатной строки в смете, или их несколько)."""

    lot_keys_with_row: tuple[str, ...]
    """Ключи лотов, чьё единственное предложение (`AGENTS.md` §4) несёт
    агрегатную строку допработ. Длина 0, 1 или больше — все три отличимы от
    «владелец есть», и вызывающий код обязан их различать сам."""

    warnings: tuple[str, ...]
    """Предупреждения УРОВНЯ СМЕТЫ (спека §2.9: «одно на смету»): похожий-но-не-
    точный ключ «Сведений» (не больше одного, хотя ключ одинаков у каждого
    предложения — дедуплицируется здесь), «„Сведения“ есть, агрегатной строки
    нет» и «владелец неоднозначен». Предупреждения УРОВНЯ ПРЕДЛОЖЕНИЯ (резолв
    ссылки, остаток, `P > T`, «допработы не расшиты») сюда не попадают — их
    возвращает `build_rows`.
    """


def _extract_total(additional_works: Mapping[str, Any]) -> Decimal | None:
    """`T` агрегатной строки как `Decimal`, либо `None` — суммы нет или её
    нельзя прочитать.

    Вход — десятичная СТРОКА (или `None`) в `total_cost.total` (`AGENTS.md` §3:
    деньги — `Decimal` end-to-end, `float` не участвует нигде). Если строка есть,
    но `Decimal` бросает `InvalidOperation`, исключение НЕ уходит наружу — такое
    значение сливается с состоянием «`T` пусто» матрицы §2.6: агрегатная строка
    с нечитаемой суммой ведёт себя как строка без суммы вовсе, а не роняет импорт.

    **Нечисловые `Decimal` отсекаются отдельной проверкой, а не `except`.**
    `Decimal("NaN")` и `Decimal("Infinity")` конструируются БЕЗ исключения, то
    есть один `except InvalidOperation` этот случай не ловит, а `money_to_json`
    пропускает нечисловой ТЕКСТ ячейки как есть (`parse_contractor_row.py`:
    `float`-ветка гасит `nan`/`inf`, строковая — нет). Дальше расходятся два
    молчаливых пути, и оба замерены: при пустых «Сведениях» запись с `NaN`
    доходит до БД — PostgreSQL считает `'NaN'::numeric >= 0` ИСТИНОЙ, поэтому
    `ck_..._total_amount` её пропускает, и каждая последующая `SUM` по таблице
    становится `NaN`; при непустых — сравнение `parsed_sum > total` бросает
    `InvalidOperation` уже наружу. Отрицательное `T` — граница другого рода: оно
    упирается в CHECK громко (devlog, граница 5), а нечисловое проходило бы тихо.
    """
    raw = additional_works[JSON_KEY_TOTAL_COST][JSON_KEY_TOTAL]
    if raw is None:
        return None
    try:
        total = Decimal(str(raw))
    except InvalidOperation:
        return None
    return total if total.is_finite() else None


def decide_owner(data: Mapping[str, Any]) -> OwnerDecision:
    """Владелец «Сведений» — по всей смете, четыре строки таблицы (спека §2.2).

    Текст «Сведений» читается ОДИН РАЗ — с первого лота сметы, потому что он
    байтово идентичен у всех (спека §1.5 факт 2) — и используется только для
    двух вещей: (а) похожий-но-не-точный ключ даёт предупреждение `svedeniya_text`
    РОВНО ОДИН раз на смету, а не по разу на предложение (иначе одинаковый
    неточный ключ дал бы N одинаковых предупреждений); (б) при нуле владельцев —
    сказать, что непустой текст называет деньги, которых нет в таблице позиций.

    Не проверяет и не резолвит ничего внутри строк «Сведений» — это `build_rows`.
    Не читает `positions`/`resolution` вовсе: решение владельца зависит только от
    того, у скольких предложений сметы ЕСТЬ агрегатная строка, и от текста
    «Сведений» — не от содержимого позиций.
    """
    lots = data[JSON_KEY_LOTS]
    lot_keys_with_row: list[str] = []
    rows_by_lot: dict[str, Mapping[str, Any]] = {}
    additional_info: Mapping[str, Any] | None = None

    for lot_key, one_lot in lots.items():
        # Ровно одно предложение на лот (AGENTS.md §4) — лот и предложение с
        # агрегатной строкой здесь одно и то же понятие.
        proposal_data = next(iter(one_lot[JSON_KEY_PROPOSALS].values()))
        if additional_info is None:
            additional_info = proposal_data[JSON_KEY_CONTRACTOR_ADDITIONAL_INFO]
        aggregate_row = proposal_data[JSON_KEY_CONTRACTOR_ITEMS][JSON_KEY_CONTRACTOR_ADDITIONAL_WORKS]
        if aggregate_row is not None:
            lot_keys_with_row.append(lot_key)
            rows_by_lot[lot_key] = aggregate_row

    text, key_warning = svedeniya_text(additional_info if additional_info is not None else {})
    warnings: list[str] = [key_warning] if key_warning else []

    if len(lot_keys_with_row) == 1:
        return OwnerDecision(
            owner_lot_key=lot_keys_with_row[0],
            lot_keys_with_row=tuple(lot_keys_with_row),
            warnings=tuple(warnings),
        )

    if not lot_keys_with_row:
        if text:
            # Пустые строки текста, как и в parse_lines, не считаются: считаем
            # только непустые после обрезки, чтобы число в предупреждении
            # совпадало с тем, что реально попробует разобрать build_rows,
            # будь у этой сметы владелец.
            line_count = sum(1 for raw_line in text.splitlines() if raw_line.strip())
            warnings.append(
                f"«{SVEDENIYA_KEY}» заполнены ({line_count} стр.), но агрегатной строки "
                "допработ нет ни у одного предложения этой сметы: текст называет деньги, "
                "которых нет в таблице позиций, поэтому расшивка не создаёт ни одной записи."
            )
        # «Сведения» пусты (или ключа нет) и владельца нет — прежнее валидное
        # состояние (спека §1.1): молчим, это НЕ повод предупреждать.
        return OwnerDecision(owner_lot_key=None, lot_keys_with_row=(), warnings=tuple(warnings))

    # Два и более предложения с агрегатной строкой: неоднозначна ТОЛЬКО
    # аналитическая разбивка — какому из них применить общий текст. Сама сумма
    # каждого предложения принадлежит ему однозначно, границами его лота (спека
    # §2.2), поэтому отказа нет — только громкое предупреждение.
    with_total: list[tuple[str, Decimal]] = []
    without_total: list[str] = []
    for lot_key in lot_keys_with_row:
        total = _extract_total(rows_by_lot[lot_key])
        if total is None:
            without_total.append(lot_key)
        else:
            with_total.append((lot_key, total))

    totals_part = "; ".join(f"«{key}»: T={total}" for key, total in with_total)
    without_part = (
        f" Без суммы (T пусто, для них запись не создаётся): {_examples(without_total)}."
        if without_total
        else ""
    )
    warnings.append(
        f"Владелец «{SVEDENIYA_KEY}» неоднозначен: агрегатная строка допработ есть у "
        f"{len(lot_keys_with_row)} предложений этой сметы ({totals_part}). Агрегатные "
        "суммы учтены полностью, каждое предложение получит нераспределённую запись "
        "на свой T; расшивка не применена из-за неоднозначного владельца общего "
        "текста." + without_part
    )
    return OwnerDecision(owner_lot_key=None, lot_keys_with_row=tuple(lot_keys_with_row), warnings=tuple(warnings))


def build_rows(
    *,
    additional_works: Mapping[str, Any] | None,
    svedeniya: str | None,
    resolution: ProposalResolution,
    positions: Mapping[str, Any],
    is_owner: bool,
    lot_key: str,
) -> ProposalAdditionalWorks:
    """Матрица состояний одного предложения (спека §2.6), владелец уже известен
    (`decide_owner`, спека §2.2).

    `is_owner=False` — предложение оказалось ОДНИМ ИЗ НЕСКОЛЬКИХ владельцев в
    неоднозначном случае §2.2: общий текст «Сведений» этому предложению не
    принадлежит, и он трактуется как ОТСУТСТВУЮЩИЙ вне зависимости от того, что в
    нём на самом деле написано (даже если непуст). Предупреждение «допработы не
    расшиты» при этом НЕ выдаётся: причину уже назвал `decide_owner` ОДНИМ
    предупреждением на смету — N предупреждений по числу предложений повторяли
    бы один и тот же факт, а спека прямо требует «одно на смету» (§2.9).

    Инвариант, ради которого написана вся функция: `sum(row.total_amount for row
    in result.rows) == T`, либо записей нет вовсе. Он не проверяется здесь кодом
    отдельной строкой — он ВЫПОЛНЯЕТСЯ построением: `T` читается один раз, и на
    каждой ветке сумма возвращаемых записей равна ровно ему (или записей нет).

    Не резолвит «Сведения без строки» (это `decide_owner`, sheet-level) и не
    решает, какое предложение — владелец (это тоже `decide_owner`) — только то,
    какие записи получит ЭТО ОДНО предложение при уже известном `is_owner`.

    `lot_key` нужен ровно одному предупреждению — «агрегатная строка без суммы»:
    спека §2.9 требует, чтобы оно называло предложение. В смете с двумя лотами
    предупреждения лежат одним плоским списком, и без ключа непонятно, о чьей
    строке речь. Параметр обязателен, а не «по умолчанию None»: предупреждение,
    которое иногда называет предложение, а иногда нет, — это два разных
    сообщения под одним именем.
    """
    if additional_works is None:
        # Строки нет вовсе: предупреждение «„Сведения“ есть, строки нет»
        # принадлежит смете (decide_owner), не предложению (спека §2.2, §2.6) —
        # здесь тишина в обоих случаях: и пустые «Сведения», и непустые.
        return ProposalAdditionalWorks(rows=(), warnings=())

    job_title = additional_works[JSON_KEY_JOB_TITLE]
    total = _extract_total(additional_works)
    if total is None:
        return ProposalAdditionalWorks(
            rows=(),
            warnings=(
                f"Агрегатная строка «{job_title}» предложения «{lot_key}» не несёт "
                "суммы (total_cost.total пусто либо не читается как число): это "
                "предложение не получит ни одной записи допработ.",
            ),
        )

    effective_text = svedeniya if is_owner else None
    if not effective_text:
        row = AdditionalWorkRow(
            ordinal=1,
            chapter_ref_raw=None,
            title=job_title,
            total_amount=total,
            work_category_id=None,
            raw_line=None,
        )
        if is_owner:
            warnings: tuple[str, ...] = (
                f"«{SVEDENIYA_KEY}» пусты, а агрегатная строка «{job_title}» есть: вся "
                f"сумма {total} осталась нераспределённой, расшивка не применена.",
            )
        else:
            warnings = ()
        return ProposalAdditionalWorks(rows=(row,), warnings=warnings)

    parsed, unreadable = parse_lines(effective_text)
    parsed_sum = sum((line.amount for line in parsed), Decimal("0"))

    if parsed_sum > total:
        # Расшивка противоречит контрольной сумме — доверяем агрегатной строке:
        # она авторитетна и входит в ИТОГО файла, разобранные строки НЕ
        # сохраняются (спека §2.6; отвергнутая альтернатива §3 — хранить остаток
        # отрицательным потребовала бы снять CHECK total_amount >= 0).
        row = AdditionalWorkRow(
            ordinal=1,
            chapter_ref_raw=None,
            title=job_title,
            total_amount=total,
            work_category_id=None,
            raw_line=None,
        )
        warning = (
            f"Разобранные строки «{SVEDENIYA_KEY}» дают {parsed_sum}, что БОЛЬШЕ суммы "
            f"агрегатной строки {total}. Расшивка противоречит контрольной сумме — "
            f"записана сумма агрегатной строки {total}, разобранные строки не сохранены."
        )
        return ProposalAdditionalWorks(rows=(row,), warnings=(warning,))

    by_number = categories_by_chapter_number(positions, resolution)
    rows: list[AdditionalWorkRow] = []
    no_ref_lines: list[ParsedLine] = []
    unresolved: list[tuple[str, str, str]] = []
    for line in parsed:
        category_id: int | None = None
        if line.ref is None:
            no_ref_lines.append(line)
        else:
            category_id, reason = resolve_ref(line.ref, by_number)
            if reason is not None:
                unresolved.append((line.ref, line.title, reason))
        rows.append(
            AdditionalWorkRow(
                ordinal=line.ordinal,
                chapter_ref_raw=line.ref,
                title=line.title,
                total_amount=line.amount,
                work_category_id=category_id,
                raw_line=line.raw_line,
            )
        )

    warnings_list: list[str] = []
    if unreadable:
        warnings_list.append(
            f"Нечитаемых строк «{SVEDENIYA_KEY}» ({len(unreadable)}): {_examples(unreadable)}. "
            "Их деньги не потеряны — они войдут в нераспределённый остаток по разности."
        )
    if no_ref_lines:
        warnings_list.append(
            f"Строк без ссылки на раздел ({len(no_ref_lines)}): "
            f"{_examples([line.raw_line for line in no_ref_lines])}. Статья не резолвится "
            "без ссылки; сумма строки при этом сохранена, но без привязки."
        )
    if unresolved:
        places = [f"«{ref}» («{title[:60]}»): {reason}" for ref, title, reason in unresolved]
        warnings_list.append(f"Ссылка не разрешилась в статью ({len(unresolved)}): {_examples(places)}.")

    remainder = total - parsed_sum
    if remainder > 0:
        # N+1, либо 1, если разобранных строк не было вовсе (спека §2.6): весь
        # текст был нечитаем, но деньги агрегатной строки всё равно не теряются.
        last_ordinal = parsed[-1].ordinal + 1 if parsed else 1
        rows.append(
            AdditionalWorkRow(
                ordinal=last_ordinal,
                chapter_ref_raw=None,
                title=job_title,
                total_amount=remainder,
                work_category_id=None,
                raw_line=None,
            )
        )
        warnings_list.append(
            f"Нераспределённый остаток {remainder} = {total} (агрегатная строка) − "
            f"{parsed_sum} (сумма разобранных строк «{SVEDENIYA_KEY}»)."
        )

    return ProposalAdditionalWorks(rows=tuple(rows), warnings=tuple(warnings_list))
