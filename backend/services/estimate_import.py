"""Импорт разобранной сметы в доменные таблицы (AGENTS.md §5, шаг 3).

Порт `ImportFullTender` из `tenders-go@121718bf45df`
(`cmd/internal/services/importer/`): сохранён обход тендер → лоты → предложения →
позиции/итоги → raw JSON и упорство в откате ВСЕЙ транзакции при любой ошибке.

Отступления от Go-референса (все продиктованы AGENTS.md, разбор —
`docs/phase4-import.md` §3):

1. `GetOrCreateObject/Contractor/Executor` не переносятся: источник истины —
   карточка договора (§3). Реквизиты из шапки XLSX только сверяются с карточкой,
   расхождения уходят в `import_jobs.warnings`.
2. UPSERT заменён на INSERT: повторную загрузку регулирует эндпоинт (§5 —
   идемпотентность по sha256 / 409 / replace), поэтому внутри транзакции смета
   либо новая, либо только что удалённая replace-ом.
3. Baseline-предложение не импортируется: в схеме ровно одно предложение на лот
   (`uq_proposals_lot_id`), а в сметах ГП baseline — заглушка парсера.
4. Матчинга здесь нет: он отдельным сервисом (`services.matching`) в ТОЙ ЖЕ
   транзакции (§5, шаг 4).

Функция чистая относительно HTTP: на входе — `ParseResult.data` и карточка
договора, на выходе — строки БД. Тестируется без FastAPI.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from models import (
    Contract,
    Estimate,
    EstimateAdditionalWork,
    EstimateRawData,
    Lot,
    PositionItem,
    Proposal,
    ProposalAdditionalInfo,
    ProposalSummaryLine,
)
from parser.constants import (
    JSON_KEY_ADDITIONAL_WORKS_SOURCE_ROW,
    JSON_KEY_BASELINE_PROPOSAL,
    JSON_KEY_CHAPTER_NUMBER,
    JSON_KEY_CHAPTER_REF,
    JSON_KEY_COMMENT_CONTRACTOR,
    JSON_KEY_COMMENT_ORGANIZER,
    JSON_KEY_CONTRACTOR_ACCREDITATION,
    JSON_KEY_CONTRACTOR_ADDITIONAL_INFO,
    JSON_KEY_CONTRACTOR_ADDITIONAL_WORKS,
    JSON_KEY_CONTRACTOR_ADDRESS,
    JSON_KEY_CONTRACTOR_COORDINATE,
    JSON_KEY_CONTRACTOR_HEIGHT,
    JSON_KEY_CONTRACTOR_INN,
    JSON_KEY_CONTRACTOR_ITEMS,
    JSON_KEY_CONTRACTOR_POSITIONS,
    JSON_KEY_CONTRACTOR_SUMMARY,
    JSON_KEY_CONTRACTOR_TITLE,
    JSON_KEY_CONTRACTOR_WIDTH,
    JSON_KEY_EXECUTOR,
    JSON_KEY_EXECUTOR_DATE,
    JSON_KEY_INDIRECT_COSTS,
    JSON_KEY_IS_CHAPTER,
    JSON_KEY_JOB_TITLE,
    JSON_KEY_LOT_TITLE,
    JSON_KEY_LOTS,
    JSON_KEY_MATERIALS,
    JSON_KEY_NUMBER,
    JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST,
    JSON_KEY_PROPOSALS,
    JSON_KEY_QUANTITY,
    JSON_KEY_SUGGESTED_QUANTITY,
    JSON_KEY_TENDER_ADDRESS,
    JSON_KEY_TENDER_OBJECT,
    JSON_KEY_TENDER_TITLE,
    JSON_KEY_TOTAL,
    JSON_KEY_TOTAL_COST,
    JSON_KEY_UNIT,
    JSON_KEY_UNIT_COST,
    JSON_KEY_WORKS,
    TABLE_PARSE_ADDITIONAL_WORKS_TITLE,
)
from parser.postprocess import BASELINE_MISSING_TITLE
from parser.sheet import normalized_cell_text
from services.additional_works import build_rows, decide_owner, svedeniya_text
from services.category_resolution import (
    CategoryResolutionContractError,
    CategoryResolver,
    ProposalResolution,
    RowKind,
)
from services.unit_resolution import ResolvedUnit, UnitResolver

log = logging.getLogger(__name__)

#: Максимум однотипных предупреждений о нечитаемых значениях, попадающих в job.
#: Дальше — одна агрегирующая строка: смета на 2,5 тыс. позиций иначе утопит
#: остальные предупреждения.
MAX_VALUE_WARNINGS = 10

#: Длина наименования, после которой это, скорее всего, спецификация целиком, а не
#: название работы: в реальном файле фазы 0 в поле оказалось 5077 символов. Импорт
#: не блокируется — работа могла быть описана и так, — но человеку об этом стоит
#: сказать: дальше такая строка либо станет отдельной работой в каталоге, либо
#: совпадёт с существующей, и оба исхода стоит проверить глазами.
LONG_JOB_TITLE_CHARS = 1000

#: Сколько примеров длинных наименований показать в предупреждении и сколько
#: символов от каждого. Полный текст в `warnings` не пишется: это несколько
#: килобайт на позицию, история загрузок превратилась бы в свалку.
MAX_LONG_TITLE_EXAMPLES = 10
LONG_TITLE_PREVIEW_CHARS = 200


class EstimateImportError(Exception):
    """Файл разобран парсером, но импортировать его нельзя.

    Текст попадает в `import_jobs.error_text` и показывается человеку, поэтому
    обязан объяснять причину, а не называть исключение.
    """


@dataclass(frozen=True)
class LongTitle:
    """Позиция со слишком длинным наименованием — для предупреждения (§5).

    `lot_key` нужен именно для поиска: нумерация позиций начинается заново в каждом
    лоте, поэтому в смете из нескольких лотов «№1» без лота неоднозначен. В
    предупреждении лот показывается, когда лотов в СМЕТЕ больше одного, — не когда
    длинные названия нашлись в нескольких (см. `_long_title_warning`).

    Превью, а не полный текст: наименований по несколько килобайт может быть много.
    """

    lot_key: str
    number: str
    length: int
    preview: str


@dataclass(frozen=True)
class PositionToMatch:
    """Позиция, допущенная к каскаду матчинга (§5, шаг 4).

    Передаётся из импорта в матчинг напрямую, а не вычитывается из БД заново:
    `matching_cache.unit_text` обязан хранить ИСХОДНЫЙ текст единицы (он нужен
    для перевыпуска ключей при инкременте `norm_version`, §4), а в
    `position_items` остаётся только разрешённый `unit_id`.
    """

    position_item_id: int
    job_title: str
    unit: ResolvedUnit


@dataclass
class ImportOutcome:
    """Результат импорта одной сметы."""

    estimate_id: int
    positions_to_match: list[PositionToMatch] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    replaced_estimate_id: int | None = None
    positions_total: int = 0
    """Всего строк position_items (включая разделы)."""


# ---------------------------------------------------------------------------
#  Преобразование значений парсера в доменные типы
# ---------------------------------------------------------------------------

def _money(value: Any, problems: list[str], where: str) -> Decimal | None:
    """Денежное значение парсера → `Decimal`.

    Парсер отдаёт деньги десятичными строками либо `null`, все литералы ошибок
    Excel уже погашены (`docs/phase3-parser.md` §2.6), поэтому штатный путь —
    `Decimal(value)` без всякой обработки.

    Нечисловой мусор в денежной колонке (произвольный текст) парсер пропускает
    как есть — здесь он становится `NULL` с предупреждением. Ронять импорт
    2,5-тысячной сметы из-за одной ячейки нельзя, а `NULL` — штатное
    представление «стоимости нет» (§3).
    """
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        problems.append(f"{where}: значение «{value}» не число, записано NULL")
        return None


def _quantity(value: Any, problems: list[str], where: str) -> Decimal | None:
    """Количество парсера → `Decimal`.

    Количества, в отличие от денег, приходят числами (int/float) — контракт
    парсера. Конверсия ТОЛЬКО через `str()`: `Decimal(0.1)` дал бы двоичный
    хвост, ровно то, от чего защищается §3 (`docs/phase4-start.md` §5).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        # Decimal(True) == 1 молча; «истина» — не количество.
        problems.append(f"{where}: логическое значение вместо количества, записано NULL")
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        problems.append(f"{where}: значение «{value}» не число, записано NULL")
        return None


def _text(value: Any) -> str | None:
    """Значение ячейки → текст либо None (пустая строка тоже None)."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _cost_block(block: Any) -> dict[str, Any]:
    return block if isinstance(block, dict) else {}


def _parse_iso_date(value: Any) -> dt.date | None:
    """ISO-строка парсера → `date`. Парсер переводит даты в ISO (§2.6 фазы 3)."""
    if value is None:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
#  Сверка шапки файла с карточкой договора (§3)
# ---------------------------------------------------------------------------

def _loose(value: Any) -> str:
    """Сравнимая форма названия/адреса.

    Сверка реквизитов должна ловить содержательные расхождения, а не разную
    пунктуацию: карточку заводит человек, шапку файла — выгрузка. Точки и запятые
    убираются, регистр гасится, пробелы схлопываются.
    """
    text = str(value or "").replace(".", " ").replace(",", " ").replace("«", " ").replace("»", " ")
    text = text.replace('"', " ").lower()
    return " ".join(text.split())


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def compare_header_with_contract(
    data: dict[str, Any], contract: Contract, proposal_data: dict[str, Any] | None
) -> list[str]:
    """Сверяет реквизиты из шапки XLSX с карточкой договора.

    Источник истины — карточка (§3): из файла ничего не апсертится. Расхождения
    не блокируют импорт, они уходят в `import_jobs.warnings`.
    """
    warnings: list[str] = []

    def mismatch(label: str, in_file: Any, in_card: Any) -> None:
        warnings.append(
            f"{label} в файле («{in_file}») не совпадает с карточкой договора («{in_card}»). "
            "Импортировано по карточке — она источник истины."
        )

    file_object = _text(data.get(JSON_KEY_TENDER_OBJECT))
    if file_object and _loose(file_object) != _loose(contract.object.title):
        mismatch("Объект", file_object, contract.object.title)

    file_address = _text(data.get(JSON_KEY_TENDER_ADDRESS))
    if file_address and _loose(file_address) != _loose(contract.object.address):
        mismatch("Адрес объекта", file_address, contract.object.address)

    if proposal_data:
        file_contractor = _text(proposal_data.get(JSON_KEY_CONTRACTOR_TITLE))
        if file_contractor and _loose(file_contractor) != _loose(contract.contractor.title):
            mismatch("Подрядчик", file_contractor, contract.contractor.title)

        file_inn = _digits(proposal_data.get(JSON_KEY_CONTRACTOR_INN))
        card_inn = _digits(contract.contractor.inn)
        if file_inn and card_inn and file_inn != card_inn:
            mismatch("ИНН подрядчика", file_inn, contract.contractor.inn)

    return warnings


# ---------------------------------------------------------------------------
#  Валидация структуры (решения фазы 4, docs/phase4-import.md §2)
# ---------------------------------------------------------------------------

def _validate_payload(data: dict[str, Any]) -> None:
    """Отвергает разобранные файлы, которые нельзя импортировать.

    Единственная причина отказа — несколько подрядчиков в лоте. §4 фиксирует
    «ровно одно предложение на лот» (и `uq_proposals_lot_id` это закрепляет), а
    какой из блоков договорной — из файла неизвестно. Молча импортировать первого
    значит выбрать за человека вслепую.
    """
    lots = data.get(JSON_KEY_LOTS)
    if not isinstance(lots, dict) or not lots:
        raise EstimateImportError(
            "В разобранном файле нет ни одного лота — импортировать нечего."
        )

    for lot_key, lot in lots.items():
        proposals = (lot or {}).get(JSON_KEY_PROPOSALS) or {}
        if len(proposals) == 0:
            raise EstimateImportError(
                f"В лоте «{lot_key}» нет предложения подрядчика: смета без расценок "
                "подрядчика не импортируется."
            )
        if len(proposals) > 1:
            titles = ", ".join(
                f"«{(p or {}).get(JSON_KEY_CONTRACTOR_TITLE)}»" for p in proposals.values()
            )
            raise EstimateImportError(
                f"Смета ГП с несколькими подрядчиками не поддерживается: в лоте «{lot_key}» "
                f"найдено {len(proposals)} блоков ({titles}). Договор ГП заключён с одним "
                "подрядчиком, и какой из блоков договорной — из файла не определить. "
                "Загрузите смету с одним блоком подрядчика."
            )


# ---------------------------------------------------------------------------
#  Импорт
# ---------------------------------------------------------------------------

def find_estimate(db: Session, contract_id: int, amendment_no: int | None) -> Estimate | None:
    """Смета договора по номеру допсоглашения (NULL = исходная)."""
    condition = (
        Estimate.amendment_no.is_(None)
        if amendment_no is None
        else Estimate.amendment_no == amendment_no
    )
    return db.execute(
        select(Estimate).where(Estimate.contract_id == contract_id, condition)
    ).scalar_one_or_none()


def import_estimate(
    db: Session,
    *,
    contract: Contract,
    amendment_no: int | None,
    data: dict[str, Any],
    parser_version: str,
    import_job_id: int | None,
    replace: bool,
    unit_resolver: UnitResolver,
    category_resolver: CategoryResolver,
) -> ImportOutcome:
    """Переносит разобранную смету в БД. Транзакцией управляет вызывающий.

    Ни одна ошибка не «частичная»: сервис только добавляет строки в открытую
    транзакцию, и её откат вызывающей стороной снимает всё сразу (§5).

    Args:
        db: сессия B (домен + финальный статус), транзакция уже открыта.
        contract: карточка договора — источник истины по объекту и подрядчику.
        amendment_no: номер допсоглашения; None — исходная смета.
        data: `ParseResult.data`.
        parser_version: `ParseResult.parser_version`.
        import_job_id: задание, которым загружена смета.
        replace: удалить существующую смету этой пары перед импортом (§5, правило 3).
        unit_resolver: разрешение единиц (общее с матчингом).
        category_resolver: резолв статьи по структуре файла (§Ф3).

    Returns:
        `ImportOutcome` с id сметы, списком позиций для матчинга и warnings.

    Raises:
        EstimateImportError: файл структурно не годится для импорта.
    """
    _validate_payload(data)

    warnings: list[str] = []
    value_problems: list[str] = []
    # Длинные наименования собираются по всем лотам и дают ОДНО предупреждение
    # на смету (см. `_long_title_warning`).
    long_titles: list[LongTitle] = []

    # Владелец «Сведений по дополнительным работам» — предпасс ПО ВСЕЙ СМЕТЕ,
    # ДО цикла по лотам (спека Ф4 §2.2, §2.8 п.4): текст побайтово одинаков у
    # всех предложений сметы (спека §1.5 факт 2), поэтому решение «чьи это
    # деньги» обязано быть фактом уровня сметы, а не предложения — иначе
    # расшивка задвоилась бы между предложениями. Предупреждения владельца
    # уходят в аккумулятор ОДИН раз, здесь же.
    owner = decide_owner(data)
    warnings.extend(owner.warnings)

    replaced_id = _replace_existing(db, contract.id, amendment_no, replace, warnings)

    estimate = Estimate(
        contract_id=contract.id,
        amendment_no=amendment_no,
        title=_text(data.get(JSON_KEY_TENDER_TITLE)),
        data_prepared_on_date=_prepared_date(data, warnings),
        import_job_id=import_job_id,
    )
    db.add(estimate)
    db.flush()

    db.add(
        EstimateRawData(
            estimate_id=estimate.id,
            raw_data=data,
            parser_version=parser_version,
        )
    )

    positions_to_match: list[PositionToMatch] = []
    positions_total = 0
    priced_seen = False
    lots_imported = 0

    for lot_key, lot_content in (data.get(JSON_KEY_LOTS) or {}).items():
        lots_imported += 1
        lot = Lot(
            estimate_id=estimate.id,
            lot_key=str(lot_key),
            lot_title=_text((lot_content or {}).get(JSON_KEY_LOT_TITLE)) or str(lot_key),
        )
        db.add(lot)
        db.flush()

        _warn_on_unexpected_baseline(lot_content, lot_key, warnings)

        proposal_data = next(iter((lot_content or {}).get(JSON_KEY_PROPOSALS).values()))
        warnings.extend(compare_header_with_contract(data, contract, proposal_data))
        _log_ignored_contractor_details(proposal_data)

        proposal = Proposal(
            lot_id=lot.id,
            # Подрядчик берётся из карточки договора, а не из файла (§3).
            contractor_id=contract.contractor_id,
            is_baseline=False,
            contractor_coordinate=_text(proposal_data.get(JSON_KEY_CONTRACTOR_COORDINATE)),
            contractor_width=_int_or_none(proposal_data.get(JSON_KEY_CONTRACTOR_WIDTH)),
            contractor_height=_int_or_none(proposal_data.get(JSON_KEY_CONTRACTOR_HEIGHT)),
        )
        db.add(proposal)
        db.flush()

        _import_additional_info(db, proposal.id, proposal_data)
        _import_summary(db, proposal.id, proposal_data, value_problems)

        # План резолва Ф3 строится и резолвится РОВНО ОДИН РАЗ на предложение
        # (Global Constraint плана; спека §1.5 факт 3, §2.8 п.1) и передаётся
        # обоим потребителям — материализации позиций и допработ. Второй
        # независимый вызов задвоил бы ВСЕ предупреждения Ф3, а не только
        # категорийные. `_extract_positions` — единственный предикат «это не
        # словарь» (раньше он дублировался и здесь, и внутри `_import_positions`).
        positions = _extract_positions(proposal_data)
        try:
            resolution: ProposalResolution = category_resolver.resolve_proposal(positions)
        except CategoryResolutionContractError as exc:
            raise EstimateImportError(f"Смету нельзя импортировать: {exc}") from exc
        warnings.extend(resolution.warnings)

        # Гейт формы 1.1.0 (спека §2.7) — сразу после плана, ДО `add_all`
        # позиций (спека §2.8 п.2): план уже несёт классификацию «вне
        # структуры», то есть всё нужное для гейта уже известно. Проверка ДО
        # вставки — решение о цене (не вставлять полторы-две тысячи строк,
        # которые всё равно уедут в откат), а не о корректности: транзакция
        # одна, и порядок на неё не влияет.
        _reject_stale_1_1_0_shape(positions, resolution, proposal_data)

        lot_positions, lot_to_match, lot_priced = _import_positions(
            db,
            proposal_id=proposal.id,
            positions=positions,
            resolution=resolution,
            unit_resolver=unit_resolver,
            value_problems=value_problems,
            warnings=warnings,
            long_titles=long_titles,
            lot_key=str(lot_key),
        )
        positions_total += lot_positions
        positions_to_match.extend(lot_to_match)
        priced_seen = priced_seen or lot_priced

        # Допработы — ПОСЛЕ позиций, в той же транзакции сессии B (спека §2.8
        # п.4): владелец уже решён предпассом выше, план резолва и позиции уже
        # готовы для резолва ссылки на раздел (спека §2.5).
        _import_additional_works(
            db,
            proposal_id=proposal.id,
            proposal_data=proposal_data,
            positions=positions,
            resolution=resolution,
            is_owner=lot_key == owner.owner_lot_key,
            lot_key=str(lot_key),
            warnings=warnings,
        )

    warnings.extend(unit_resolver.unknown_warnings())
    warnings.extend(_squash(value_problems))
    if long_titles:
        # Лот показывается по фактической многолотовости СМЕТЫ, а не по числу
        # лотов с длинными названиями: если длинная позиция одна, а лотов три,
        # искать «№1» всё равно придётся во всех трёх.
        warnings.append(_long_title_warning(long_titles, with_lot=lots_imported > 1))

    # Эвристика «формулы без кэша» (решение фазы 4, §2.2 отчёта): файл, сохранённый
    # без пересчёта, при data_only=True даёт сплошные NULL-стоимости и НОЛЬ
    # предупреждений парсера — то есть выглядит как успешный импорт пустой сметы.
    if positions_total and not priced_seen:
        warnings.append(
            f"Позиции есть ({positions_total}), но ни у одной не заполнена ни одна "
            "денежная колонка. Вероятная причина — файл сохранён без пересчёта формул: "
            "в нём нет кэшированных значений, и стоимости прочитать неоткуда. "
            "Откройте файл в Excel, пересчитайте (F9), сохраните и загрузите повторно."
        )

    return ImportOutcome(
        estimate_id=estimate.id,
        positions_to_match=positions_to_match,
        warnings=warnings,
        replaced_estimate_id=replaced_id,
        positions_total=positions_total,
    )


def _replace_existing(
    db: Session,
    contract_id: int,
    amendment_no: int | None,
    replace: bool,
    warnings: list[str],
) -> int | None:
    """Удаляет существующую смету пары (§5, правило 3).

    Удаление — Core DELETE, а не ORM: каскады объявлены в схеме (lots →
    proposals → position_items, estimate_raw_data), и ORM-каскад лишь вычитал бы
    в память тысячи строк, чтобы удалить их по одной.

    Старые `import_jobs` и их файлы НЕ удаляются — это аудит (§5).
    """
    if not replace:
        return None

    row = db.execute(
        select(Estimate.id, Estimate.created_at).where(
            Estimate.contract_id == contract_id,
            Estimate.amendment_no.is_(None)
            if amendment_no is None
            else Estimate.amendment_no == amendment_no,
        )
    ).one_or_none()
    if row is None:
        return None

    old_id, created_at = row
    db.execute(delete(Estimate).where(Estimate.id == old_id))
    warnings.append(
        f"Заменена смета estimate_id={old_id} от {created_at.date().isoformat()}. "
        "Прежние задания импорта и их файлы сохранены как аудит."
    )
    return old_id


def _prepared_date(data: dict[str, Any], warnings: list[str]) -> dt.date | None:
    """Дата составления из блока исполнителя.

    В сметах ГП блока исполнителя нет, и NULL здесь — норма
    (`docs/phase3-parser.md` §4.1): датой сравнения с нормативом станет
    `contracts.signed_date` — штатный фолбэк §4.
    """
    executor = data.get(JSON_KEY_EXECUTOR) or {}
    raw = executor.get(JSON_KEY_EXECUTOR_DATE)
    if raw is None:
        return None
    parsed = _parse_iso_date(raw)
    if parsed is None:
        warnings.append(
            f"Дата составления «{raw}» не распознана; сравнение с нормативом пойдёт "
            "по дате подписания договора."
        )
    return parsed


def _warn_on_unexpected_baseline(lot_content: Any, lot_key: str, warnings: list[str]) -> None:
    """Baseline не импортируется никогда; неожиданный непустой baseline — warning.

    Парсер всегда подставляет заглушку `BASELINE_MISSING_TITLE`. Другой заголовок
    означает, что в файле есть колонка расчётной стоимости, то есть это не смета
    ГП в ожидаемом виде (`docs/phase4-start.md` §3.4).
    """
    baseline = (lot_content or {}).get(JSON_KEY_BASELINE_PROPOSAL) or {}
    title = _text(baseline.get(JSON_KEY_CONTRACTOR_TITLE))
    if title and title != BASELINE_MISSING_TITLE:
        warnings.append(
            f"В лоте «{lot_key}» найдено предложение расчётной стоимости («{title}»). "
            "В сметах ГП baseline отсутствует, и он не импортируется — проверьте, "
            "что загружена смета к договору, а не тендерная таблица."
        )


def _log_ignored_contractor_details(proposal_data: dict[str, Any]) -> None:
    """Реквизиты подрядчика из файла не сохраняются — только сверяются (§3).

    Адрес и аккредитация из шапки не сверяются с карточкой намеренно: в карточке
    они заводятся человеком в свободной форме, и расхождение здесь — норма, а не
    сигнал. Полностью они остаются в `estimate_raw_data.raw_data`.
    """
    address = _text(proposal_data.get(JSON_KEY_CONTRACTOR_ADDRESS))
    accreditation = _text(proposal_data.get(JSON_KEY_CONTRACTOR_ACCREDITATION))
    if address or accreditation:
        log.debug(
            "Реквизиты подрядчика из файла игнорируются (источник истины — карточка): "
            "адрес=%r, аккредитация=%r",
            address,
            accreditation,
        )


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _import_additional_info(db: Session, proposal_id: int, proposal_data: dict[str, Any]) -> None:
    info = proposal_data.get(JSON_KEY_CONTRACTOR_ADDITIONAL_INFO) or {}
    if not isinstance(info, dict):
        return
    for key, value in info.items():
        db.add(
            ProposalAdditionalInfo(
                proposal_id=proposal_id,
                info_key=str(key),
                info_value=_text(value),
            )
        )


def _import_summary(
    db: Session, proposal_id: int, proposal_data: dict[str, Any], value_problems: list[str]
) -> None:
    """Итоговые строки предложения. Как в Go: суммы берутся из блока `total_cost`."""
    items = proposal_data.get(JSON_KEY_CONTRACTOR_ITEMS) or {}
    summary = items.get(JSON_KEY_CONTRACTOR_SUMMARY) or {}
    if not isinstance(summary, dict):
        return

    for summary_key, line in summary.items():
        if not isinstance(line, dict):
            continue
        total = _cost_block(line.get(JSON_KEY_TOTAL_COST))
        where = f"итог «{summary_key}»"
        db.add(
            ProposalSummaryLine(
                proposal_id=proposal_id,
                summary_key=str(summary_key),
                job_title=str(line.get(JSON_KEY_JOB_TITLE) or summary_key),
                materials_cost=_money(total.get(JSON_KEY_MATERIALS), value_problems, where),
                works_cost=_money(total.get(JSON_KEY_WORKS), value_problems, where),
                indirect_costs_cost=_money(
                    total.get(JSON_KEY_INDIRECT_COSTS), value_problems, where
                ),
                total_cost=_money(total.get(JSON_KEY_TOTAL), value_problems, where),
            )
        )


def _extract_positions(proposal_data: dict[str, Any]) -> dict[str, Any]:
    """`contractor_items.positions`, либо `{}` — единственный предикат «это не
    вывод парсера» (Global Constraint плана Task 5: ни одного второго предиката
    для уже выраженного понятия). Раньше эта же проверка дублировалась внутри
    `_import_positions`, ДО вызова резолвера; теперь план резолва и материализация
    позиций потребляют один и тот же результат этой функции.
    """
    items = proposal_data.get(JSON_KEY_CONTRACTOR_ITEMS) or {}
    positions = items.get(JSON_KEY_CONTRACTOR_POSITIONS) or {}
    return positions if isinstance(positions, dict) else {}


def _reject_stale_1_1_0_shape(
    positions: dict[str, Any],
    resolution: ProposalResolution,
    proposal_data: dict[str, Any],
) -> None:
    """Гейт против формы парсера 1.1.0 (спека Ф4 §2.7).

    Парсер `1.1.0` держал агрегатную строку допработ И в `positions`, И в
    `additional_works` — такой payload, попав в импорт Ф4, дал бы двойной счёт
    (`raw_data` неизменяем, поэтому уже загруженные старые сметы этот путь не
    проходят, спека §2.11; здесь — защита от повторной загрузки такого файла
    через новый импорт).

    Ищет строку-КОПИЮ, все четыре условия обязательны разом:
      - `RowKind.OUTSIDE_STRUCTURE` — готовый предикат Ф3 «пустые A и B»; второй
        предикат пустоты здесь НЕ заводится (Global Constraint плана), и
        `is_chapter=false` этим `kind` уже подразумевается;
      - нормализованное название равно `TABLE_PARSE_ADDITIONAL_WORKS_TITLE`;
      - денежный блок строки побайтово равен `additional_works` (все его ключи,
        кроме `JSON_KEY_JOB_TITLE` и `JSON_KEY_ADDITIONAL_WORKS_SOURCE_ROW`).

    Строка с тем же названием, но ДРУГИМИ деньгами, — не копия: она уходит
    штатным путём Ф3 «вне структуры» со своим предупреждением, отвергать смету
    из-за неё было бы ложным отказом (спека §3).

    Ветвления по `parser_version` нет: гейт смотрит на форму данных, версия
    остаётся аудитом (спека §2.7). Проверка ДО вставки позиций — решение о
    цене (не тратить вставку полутора-двух тысяч строк, которые всё равно
    уедут в откат), не о корректности: транзакция одна.
    """
    items = proposal_data.get(JSON_KEY_CONTRACTOR_ITEMS) or {}
    aggregate_row = items.get(JSON_KEY_CONTRACTOR_ADDITIONAL_WORKS)
    if not isinstance(aggregate_row, dict):
        return

    money_keys = [
        key
        for key in aggregate_row
        if key not in (JSON_KEY_JOB_TITLE, JSON_KEY_ADDITIONAL_WORKS_SOURCE_ROW)
    ]
    expected_title = TABLE_PARSE_ADDITIONAL_WORKS_TITLE.casefold()

    for position_key, raw_position in positions.items():
        if not isinstance(raw_position, dict):
            continue
        decision = resolution.rows.get(str(position_key))
        if decision is None or decision.kind is not RowKind.OUTSIDE_STRUCTURE:
            continue
        # Нормализация — ТА ЖЕ, которой строку распознал парсер
        # (`get_lot_positions`: `normalized_cell_text` + `casefold`), а не
        # `_text`, который только обрезает края. Разница не косметическая:
        # `normalized_cell_text` схлопывает ВНУТРЕННИЕ пробельные
        # последовательности, поэтому название с двойным пробелом между словами
        # парсер 1.1.0 распознал бы и положил в оба места, а гейт с `_text` такую
        # копию пропустил бы — то есть двойной счёт прошёл бы ровно через ту
        # защиту, которая от него поставлена (спека §2.7: «нормализованное
        # название», нормализатор в проекте один).
        title = normalized_cell_text(raw_position.get(JSON_KEY_JOB_TITLE))
        if title.casefold() != expected_title:
            continue
        if all(raw_position.get(key) == aggregate_row.get(key) for key in money_keys):
            raise EstimateImportError(
                f"Позиция «{position_key}» имеет форму парсера 1.1.0: агрегатная "
                "строка допработ сохранена и среди позиций, и в additional_works "
                "с теми же деньгами — так смета была бы посчитана дважды. Смету "
                "нужно разобрать заново текущим парсером — загрузите файл повторно."
            )


def _import_additional_works(
    db: Session,
    *,
    proposal_id: int,
    proposal_data: dict[str, Any],
    positions: dict[str, Any],
    resolution: ProposalResolution,
    is_owner: bool,
    lot_key: str,
    warnings: list[str],
) -> None:
    """Материализует `estimate_additional_works` предложения (спека Ф4 §2.8 п.4).

    Вызывается ПОСЛЕ материализации позиций, в той же транзакции сессии B: план
    резолва и позиции уже готовы, `build_rows` резолвит ссылку на раздел по ним
    (спека §2.5). Владелец (`is_owner`) уже решён предпассом `decide_owner` по
    всей смете (спека §2.2), до цикла по лотам.

    Текст «Сведений» перечитывается через `svedeniya_text` для ЭТОГО предложения;
    предупреждение о похожем-но-не-точном ключе (второй элемент кортежа) здесь
    НЕ добавляется повторно — оно уже выдано РОВНО один раз на смету через
    `decide_owner` (спека §2.9; §1.5 факт 2: текст побайтово одинаков у всех
    предложений сметы, поэтому повторный вызов даёт тот же текст, но не должен
    давать второе предупреждение).
    """
    items = proposal_data.get(JSON_KEY_CONTRACTOR_ITEMS) or {}
    aggregate_row = items.get(JSON_KEY_CONTRACTOR_ADDITIONAL_WORKS)
    additional_info = proposal_data.get(JSON_KEY_CONTRACTOR_ADDITIONAL_INFO)
    text, _key_warning = svedeniya_text(additional_info if isinstance(additional_info, dict) else {})

    result = build_rows(
        additional_works=aggregate_row if isinstance(aggregate_row, dict) else None,
        svedeniya=text,
        resolution=resolution,
        positions=positions,
        is_owner=is_owner,
        lot_key=lot_key,
    )
    warnings.extend(result.warnings)
    # `db.flush()` обязателен: это последняя запись в БД на предложение (после
    # неё в цикле начинается либо следующий лот, либо возврат из
    # `import_estimate`), а тестовая сессия сконфигурирована с `autoflush=False`
    # (`tests/conftest.py`) — без явного flush строки остались бы в identity
    # map и были бы не видны последующему `SELECT` в той же транзакции.
    db.add_all(
        EstimateAdditionalWork(
            proposal_id=proposal_id,
            ordinal=row.ordinal,
            chapter_ref_raw=row.chapter_ref_raw,
            title=row.title,
            total_amount=row.total_amount,
            work_category_id=row.work_category_id,
            raw_line=row.raw_line,
        )
        for row in result.rows
    )
    db.flush()


def _import_positions(
    db: Session,
    *,
    proposal_id: int,
    positions: dict[str, Any],
    resolution: ProposalResolution,
    unit_resolver: UnitResolver,
    value_problems: list[str],
    warnings: list[str],
    long_titles: list[LongTitle],
    lot_key: str,
) -> tuple[int, list[PositionToMatch], bool]:
    """Строки сметы. Возвращает (сколько строк, что матчить, есть ли деньги).

    `positions` и `resolution` — уже готовы (план резолва строится и резолвится
    РОВНО ОДИН РАЗ в `import_estimate`, а не здесь: второй независимый вызов
    задвоил бы ВСЕ предупреждения Ф3, спека §1.5 факт 3). Guard «не словарь» для
    `positions` тоже больше не дублируется здесь — единственный предикат об этом
    теперь `_extract_positions`.

    `long_titles` — аккумулятор на ВСЮ смету, а не на лот: функция вызывается по
    одному разу на лот, и складывай предупреждение внутри — файл с тремя лотами
    получил бы три почти одинаковых предупреждения и до десяти примеров в каждом.
    Собирается так же, как `value_problems`.
    """
    rows: list[PositionItem] = []
    to_match_source: list[tuple[PositionItem, str, ResolvedUnit]] = []
    priced_seen = False
    untitled = 0

    for position_key, raw_position in positions.items():
        if not isinstance(raw_position, dict):
            continue

        where = f"позиция «{position_key}»"
        unit_cost = _cost_block(raw_position.get(JSON_KEY_UNIT_COST))
        total_cost = _cost_block(raw_position.get(JSON_KEY_TOTAL_COST))
        unit = unit_resolver.resolve(raw_position.get(JSON_KEY_UNIT))
        is_chapter = bool(raw_position.get(JSON_KEY_IS_CHAPTER))
        job_title = _text(raw_position.get(JSON_KEY_JOB_TITLE))
        decision = resolution.rows[str(position_key)]

        item = PositionItem(
            proposal_id=proposal_id,
            catalog_position_id=None,  # проставит матчинг (§5, шаг 4)
            position_key_in_proposal=str(position_key),
            comment_organizer=_text(raw_position.get(JSON_KEY_COMMENT_ORGANIZER)),
            comment_contractor=_text(raw_position.get(JSON_KEY_COMMENT_CONTRACTOR)),
            item_number_in_proposal=_text(raw_position.get(JSON_KEY_NUMBER)),
            chapter_number_in_proposal=_text(raw_position.get(JSON_KEY_CHAPTER_NUMBER)),
            # NOT NULL: пустое название сохраняем пустой строкой, но к матчингу
            # такая строка не допускается — идентичности работы у неё нет.
            job_title_in_proposal=job_title or "",
            unit_id=unit.unit_id,
            quantity=_quantity(raw_position.get(JSON_KEY_QUANTITY), value_problems, where),
            suggested_quantity=_quantity(
                raw_position.get(JSON_KEY_SUGGESTED_QUANTITY), value_problems, where
            ),
            total_cost_for_organizer_quantity=_money(
                raw_position.get(JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST), value_problems, where
            ),
            unit_cost_materials=_money(unit_cost.get(JSON_KEY_MATERIALS), value_problems, where),
            unit_cost_works=_money(unit_cost.get(JSON_KEY_WORKS), value_problems, where),
            unit_cost_indirect_costs=_money(
                unit_cost.get(JSON_KEY_INDIRECT_COSTS), value_problems, where
            ),
            unit_cost_total=_money(unit_cost.get(JSON_KEY_TOTAL), value_problems, where),
            total_cost_materials=_money(total_cost.get(JSON_KEY_MATERIALS), value_problems, where),
            total_cost_works=_money(total_cost.get(JSON_KEY_WORKS), value_problems, where),
            total_cost_indirect_costs=_money(
                total_cost.get(JSON_KEY_INDIRECT_COSTS), value_problems, where
            ),
            total_cost_total=_money(total_cost.get(JSON_KEY_TOTAL), value_problems, where),
            # В сметах ГП baseline нет, поле остаётся NULL (§4).
            deviation_from_baseline_cost=None,
            is_chapter=is_chapter,
            chapter_ref_in_proposal=_text(raw_position.get(JSON_KEY_CHAPTER_REF)),
            smr_article_raw=decision.smr_article_raw,
            work_category_id=decision.work_category_id,
            category_source=decision.category_source,
        )
        rows.append(item)

        # Считаем по всем строкам с наименованием, включая разделы: подозрительна
        # сама длина поля, а не то, попадёт ли строка в каскад матчинга. В
        # аккумулятор кладём уже превью, а не текст: полных наименований по
        # несколько килобайт может быть много, а в предупреждение попадёт начало.
        if job_title is not None and len(job_title) > LONG_JOB_TITLE_CHARS:
            long_titles.append(
                LongTitle(
                    lot_key=lot_key,
                    number=item.item_number_in_proposal or str(position_key),
                    length=len(job_title),
                    preview=_preview(job_title),
                )
            )

        if any(
            value is not None
            for value in (
                item.unit_cost_total,
                item.total_cost_total,
                item.unit_cost_materials,
                item.unit_cost_works,
                item.unit_cost_indirect_costs,
                item.total_cost_materials,
                item.total_cost_works,
                item.total_cost_indirect_costs,
            )
        ):
            priced_seen = True

        # К каскаду допускаются все строки, кроме разделов (§5, шаг 4) и кроме
        # строк без названия — у них нет идентичности работы.
        if is_chapter:
            continue
        if job_title is None:
            untitled += 1
            continue
        to_match_source.append((item, job_title, unit))

    db.add_all(rows)
    db.flush()

    # Второй проход: self-FK можно проставить только когда id уже есть. Поля статьи
    # здесь НЕ переписываются — они проставлены при создании строк. UPDATE затрагивает
    # chapter_item_id и updated_at, но updated_at берёт now() = время начала
    # транзакции, поэтому наблюдаемого временного следа после commit не остаётся.
    by_key = {item.position_key_in_proposal: item for item in rows}
    for item in rows:
        parent_key = resolution.rows[item.position_key_in_proposal].parent_position_key
        if parent_key is not None:
            item.chapter_item_id = by_key[parent_key].id
    db.flush()

    if untitled:
        warnings.append(
            f"Позиций без наименования работы: {untitled}. Они сохранены в смете, но "
            "к матчингу не допущены и в метрику матчинга не входят — сопоставлять "
            "нечего."
        )


    to_match = [
        PositionToMatch(position_item_id=item.id, job_title=title, unit=unit)
        for item, title, unit in to_match_source
    ]
    return len(rows), to_match, priced_seen


def _long_title_warning(found: list[LongTitle], *, with_lot: bool) -> str:
    """Одно агрегированное предупреждение о слишком длинных наименованиях.

    Одна строка на всю смету, а не поток и не по одной на лот: такие позиции идут
    пачками (спецификация растянута на несколько строк), и по предупреждению на
    каждую история загрузок стала бы нечитаемой. В предупреждении — сколько их,
    номера, длины и начало текста; полное наименование остаётся в смете.

    Про Review не обещаем ничего: в очередь попадут только позиции, чья каталожная
    строка получила `kind='TO_REVIEW'`, — раздел (`is_chapter`) туда не попадёт
    вовсе, а совпавшая с существующей POSITION строка уйдёт в матч.

    Лот в примерах показывается только у многолотовой сметы (`with_lot` решает
    вызывающий): нумерация позиций начинается заново в каждом лоте, и без лота два
    «№1» не различить, — а у единственного лота это просто шум. Считать лоты по
    самим примерам нельзя: одна длинная позиция в смете из трёх лотов дала бы
    «один лот», хотя искать её пришлось бы во всех трёх.
    """
    examples = "; ".join(
        (f"лот {item.lot_key}, " if with_lot else "")
        + f"№{item.number} — {item.length} симв.: «{item.preview}»"
        for item in found[:MAX_LONG_TITLE_EXAMPLES]
    )
    hidden = len(found) - MAX_LONG_TITLE_EXAMPLES
    tail = f"; …и ещё {hidden}" if hidden > 0 else ""
    return (
        f"Наименование работы длиннее {LONG_JOB_TITLE_CHARS} символов — таких "
        f"позиций: {len(found)}; возможно, в поле попала спецификация. Импорт "
        f"продолжен; проверьте указанные позиции. Примеры: {examples}{tail}."
    )


def _preview(title: str) -> str:
    """Начало наименования одной строкой: переносы и табуляции — в пробел."""
    flat = " ".join(title.split())
    if len(flat) <= LONG_TITLE_PREVIEW_CHARS:
        return flat
    return flat[:LONG_TITLE_PREVIEW_CHARS].rstrip() + "…"


def _squash(problems: list[str]) -> list[str]:
    """Ограничивает поток однотипных предупреждений о значениях."""
    if not problems:
        return []
    if len(problems) <= MAX_VALUE_WARNINGS:
        return list(problems)
    hidden = len(problems) - MAX_VALUE_WARNINGS
    return [
        *problems[:MAX_VALUE_WARNINGS],
        f"…и ещё {hidden} подобных значений записаны как NULL.",
    ]
