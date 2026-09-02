"""Применение ручных решений о статье раздела (спека разноса §2.3–2.5).

Решение НЕ применяется точечным `UPDATE` поддерева: оно подмешивается в вход того
же `CategoryResolver`, который работал на импорте, и результат материализуется в
те же колонки. Закон наследования поэтому остаётся в одном месте — это и есть
причина, по которой выбран этот подход, а не прямой обход дерева в БД.

Вход резолвера собирается из `estimate_raw_data.raw_data`, а не из строк БД, хотя
из строк он тоже собрался бы (`item_number_in_proposal`, `chapter_number_in_proposal`,
`smr_article_raw`, `is_chapter`, `job_title_in_proposal` — всё на месте). Причина в
проверяемости: `raw_data` неизменяем, поэтому пересчёт БЕЗ решений обязан дать в
точности то, что записал импорт, и это утверждение исполняется тестом
`test_a_recalculation_without_decisions_reproduces_the_import`. Из строк БД такой
проверки не построить — `smr_article_raw` на не-разделах запрещён констрейнтом,
то есть один класс предупреждений Ф3 по построению невоспроизводим.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from models import (
    Estimate,
    EstimateAdditionalWork,
    EstimateCategoryOverride,
    EstimateRawData,
    Lot,
    PositionItem,
    Proposal,
    WorkCategory,
)
from parser.constants import JSON_KEY_LOTS
from services.additional_works import categories_by_chapter_number, resolve_ref
from services.category_resolution import CategoryResolver, ProposalResolution
from services.estimate_import import extract_positions, extract_single_proposal

ErrorCode = Literal["not_found", "not_a_chapter", "structure_disabled", "mapping_broken"]


class CategoryOverrideError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: ErrorCode = code


@dataclass(frozen=True)
class ApplyResult:
    """Итог одного пересчёта.

    `chapters_manual` — число строк-разделов, чья ЭФФЕКТИВНАЯ статья пришла из
    ручного решения, свои и унаследованные вместе (то же, что считает
    `ResolutionCounters.chapters_manual` в `category_resolution.py`). Это НЕ
    число решений: одно решение на вершине поддерева даёт столько
    `chapters_manual`, сколько разделов в этом поддереве, — число решений
    вызывающий знает по таблице `estimate_category_overrides`.
    """

    chapters_updated: int
    additional_works_updated: int
    chapters_manual: int


def lock_estimate(db: Session, estimate_id: int) -> Estimate:
    """Блокировка строки сметы на весь пересчёт (спека §2.5).

    Держит два случая, оба достижимы при одном worker'е (импорт живёт в
    `BackgroundTasks` со своей сессией): два параллельных разноса и разнос против
    замены сметы. Если замена успела первой, строки уже нет — и это ТОТ ЖЕ
    `not_found`, что для сметы, которой не было изначально: различить два случая
    сервис не может и не должен делать вид, что может (спека §2.5).
    """
    estimate = db.execute(
        sa.select(Estimate).where(Estimate.id == estimate_id).with_for_update()
    ).scalar_one_or_none()
    if estimate is None:
        raise CategoryOverrideError("not_found", f"Смета {estimate_id} не найдена.")
    return estimate


def _require_chapter_of(db: Session, estimate_id: int, position_item_id: int) -> PositionItem:
    """Цель решения — строка-раздел ИМЕННО этой сметы.

    Строка из другой сметы и строка, которой нет вовсе, — один и тот же
    `not_found`: с точки зрения этой сметы обе означают «раздела здесь нет», и
    различать их означало бы утекать чужому вызывающему, какие id заняты в
    ЧУЖИХ сметах. `not_a_chapter` — отдельный код: раздел там есть, но статья
    привязывается только к строкам-разделам (спека §1.3), не к позициям.
    """
    row = db.execute(
        sa.select(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(PositionItem.id == position_item_id, Lot.estimate_id == estimate_id)
    ).scalar_one_or_none()
    if row is None:
        raise CategoryOverrideError(
            "not_found",
            f"Раздел {position_item_id} не найден в смете {estimate_id}: его нет "
            "вовсе, либо он принадлежит другой смете.",
        )
    if not row.is_chapter:
        raise CategoryOverrideError(
            "not_a_chapter",
            f"Строка {position_item_id} не является разделом: статья привязывается "
            "только к строкам-разделам, позиция наследует её от своего раздела "
            "(спека §1.3).",
        )
    return row


def _require_category(db: Session, work_category_id: int) -> None:
    """Существование статьи проверяется ЗДЕСЬ, а не оставляется на FK: нарушение
    FK вылетело бы из `flush` как `IntegrityError` и превратилось бы в `500`,
    тогда как спека §2.7 обещает на несуществующую статью `404`.
    """
    exists = db.execute(
        sa.select(WorkCategory.id).where(WorkCategory.id == work_category_id)
    ).scalar_one_or_none()
    if exists is None:
        raise CategoryOverrideError(
            "not_found", f"Статья {work_category_id} не найдена в классификаторе."
        )


def set_override(
    db: Session,
    *,
    estimate_id: int,
    position_item_id: int,
    work_category_id: int,
    note: str | None,
    user_id: int,
) -> ApplyResult:
    """UPSERT решения и полный пересчёт. Транзакцию ведёт вызывающий.

    Повторный вызов с ТЕМИ ЖЕ `work_category_id` и `note` — no-op: `assigned_by` и
    `assigned_at` сохраняют прежние значения (спека §2.2). «UPSERT идемпотентен по
    построению» верно для строки-результата, но не для того, кто и когда её
    поставил, — а в паспорте для банка автор решения и есть содержательная часть.
    """
    lock_estimate(db, estimate_id)
    _require_chapter_of(db, estimate_id, position_item_id)
    # Существование статьи проверяется ЗДЕСЬ, а не оставляется на FK: нарушение
    # FK вылетел бы из `flush` как `IntegrityError` и превратилось бы в `500`,
    # тогда как спека §2.7 обещает на несуществующую статью `404`.
    _require_category(db, work_category_id)

    existing = db.get(EstimateCategoryOverride, position_item_id)
    if existing is None:
        db.add(
            EstimateCategoryOverride(
                position_item_id=position_item_id,
                work_category_id=work_category_id,
                assigned_by=user_id,
                note=note,
            )
        )
    elif existing.work_category_id != work_category_id or existing.note != note:
        existing.work_category_id = work_category_id
        existing.note = note
        existing.assigned_by = user_id
        existing.assigned_at = sa.func.now()
    # else: ничего не меняем — аудит остаётся прежним.
    db.flush()
    return apply_overrides(db, estimate_id, already_locked=True)


def clear_override(db: Session, *, estimate_id: int, position_item_id: int) -> ApplyResult:
    """Снятие решения — идемпотентно (спека §2.2): решения уже нет — это не
    ошибка, а целевое состояние, до которого вызывающий и хотел добраться.

    Пересчёт выполняется ВСЕГДА, даже когда снимать было нечего: он и есть
    смысл вызова — без него точка снятия не отличалась бы от точки, где
    решения никогда не было, а другие решения этой сметы всё равно могли
    измениться с прошлого пересчёта.
    """
    lock_estimate(db, estimate_id)
    _require_chapter_of(db, estimate_id, position_item_id)

    existing = db.get(EstimateCategoryOverride, position_item_id)
    if existing is not None:
        db.delete(existing)
        db.flush()
    return apply_overrides(db, estimate_id, already_locked=True)


def apply_overrides(db: Session, estimate_id: int, *, already_locked: bool = False) -> ApplyResult:
    """Полный пересчёт статей сметы по действующим решениям (спека §2.3-2.5).

    ВСЕГДА полный: каждое предложение сметы, весь текущий набор решений — а не
    только предложение и решение, ради которых зашли. Отсюда идемпотентность:
    повторный вызов без изменения решений обязан дать тот же результат, а
    снятие решения обязано вернуть раздел (и, если он был кандидатом, строку
    допработ) к тому, что дал бы пересчёт без решений вовсе — то есть записать
    `NULL`, а не оставить прежнее значение молча висеть.

    Второй потребитель — `services/round_category_override.py`: раундовая
    запись пишет решения сама и зовёт этот пересчёт по каждой offer-смете под
    уже взятыми блокировками (`already_locked=True`) — закон материализации
    один на оба маршрута (спека этапного разноса §2.4 п.4).
    """
    if not already_locked:
        lock_estimate(db, estimate_id)

    raw = db.execute(
        sa.select(EstimateRawData.raw_data).where(EstimateRawData.estimate_id == estimate_id)
    ).scalar_one_or_none()
    if raw is None:
        raise CategoryOverrideError(
            "not_found",
            f"У сметы {estimate_id} нет разобранной копии файла — пересчёт невозможен.",
        )
    _require_lot_bijection(db, estimate_id, raw)

    resolver = CategoryResolver.from_db(db)
    chapters_updated = extras_updated = chapters_manual = 0

    for lot_key, proposal_id, positions in _proposals_with_positions(db, estimate_id, raw):
        rows_by_key, ids_by_key = _rows_of(db, proposal_id)
        _require_bijection(lot_key, positions, ids_by_key)

        overrides = _overrides_of(db, ids_by_key)
        resolution = resolver.resolve_proposal(positions, overrides)
        if resolution.structure_disabled and overrides:
            raise CategoryOverrideError(
                "structure_disabled",
                f"Структура разделов предложения «{lot_key}» не определена, поэтому "
                "статьи не привязываются ни к одной его строке. Исправьте нумерацию "
                "разделов и загрузите файл повторно.",
            )

        chapters_manual += resolution.counters.chapters_manual
        chapters_updated += _materialize_chapters(rows_by_key, resolution)
        extras_updated += _materialize_extras(db, proposal_id, positions, resolution)

    db.flush()
    return ApplyResult(chapters_updated, extras_updated, chapters_manual)


def _require_lot_bijection(db: Session, estimate_id: int, raw: dict[str, Any]) -> None:
    """Ключ `raw_data["lots"]` ↔ `lots.lot_key` этой сметы — та же биекция, что
    `_require_bijection` держит на уровне строки предложения, только на уровень
    выше, и по той же причине (спека §2.3, §1.7).

    Сегодня расхождение недостижимо: импорт вставляет ровно один `Lot` на
    ключ лота файла и обратно ничего не удаляет, `uq_lots_estimate_lot_key`
    держит уникальность ключа внутри сметы, а `uq_proposals_lot_id` — ровно
    одно предложение на лот. Но недостижимость сегодня — не повод пропускать
    расхождение молча, если оно всё-таки возникнет: молчание на уровне лота
    вредит РОВНО ТАК ЖЕ, как молчание на уровне строки, только раньше и
    крупнее. Лот, который есть в БД, но пропал из `raw_data`, никогда не
    попадёт в цикл `_proposals_with_positions` — его решение (если оно есть)
    тем не менее флешнется, ничего не материализует и вернёт
    `ApplyResult(0, 0, 0)` КАК УСПЕХ, то есть паспорт, правдоподобный ровно
    настолько, насколько неверный. Зеркальный случай хуже иначе: лот, который
    есть в файле, но исчез из БД, уронил бы `_proposals_with_positions`
    `NoResultFound`-ом — безликим `500` вместо `mapping_broken` с объяснением.
    Проверка здесь, ДО цикла, устраняет оба случая разом.
    """
    file_keys = {str(key) for key in (raw.get(JSON_KEY_LOTS) or {})}
    db_keys = set(
        db.execute(sa.select(Lot.lot_key).where(Lot.estimate_id == estimate_id)).scalars().all()
    )
    only_in_file = file_keys - db_keys
    only_in_db = db_keys - file_keys
    if only_in_file or only_in_db:
        raise CategoryOverrideError(
            "mapping_broken",
            f"Смета {estimate_id}: разобранная копия файла и лоты сметы не "
            f"соответствуют друг другу (только в файле: {sorted(only_in_file)[:5]}; "
            f"только в БД: {sorted(only_in_db)[:5]}). Пересчёт статей отменён.",
        )


def _proposals_with_positions(
    db: Session, estimate_id: int, raw: dict[str, Any]
) -> Iterator[tuple[str, int, dict[str, Any]]]:
    """Лоты сметы из `raw_data`: для каждого — ключ лота, id его единственного
    предложения (`AGENTS.md` §4) и позиции, извлечённые ТЕМ ЖЕ `extract_positions`,
    что импорт (спека §2.3). Ключ лота файла сопоставляется с `lots.lot_key`
    (`UNIQUE (estimate_id, lot_key)`), поэтому лот и его предложение находятся
    однозначно — расходиться им не с чем: биекцию на уровне лота уже проверил
    `_require_lot_bijection` до вызова этой функции, `.scalar_one()` здесь
    поэтому не может упасть `NoResultFound`-ом на нормальном пути.
    """
    for lot_key, lot_content in (raw.get(JSON_KEY_LOTS) or {}).items():
        lot_key = str(lot_key)
        lot_id = db.execute(
            sa.select(Lot.id).where(Lot.estimate_id == estimate_id, Lot.lot_key == lot_key)
        ).scalar_one()
        proposal_id = db.execute(
            sa.select(Proposal.id).where(Proposal.lot_id == lot_id)
        ).scalar_one()
        proposal_data = extract_single_proposal(lot_content)
        yield lot_key, proposal_id, extract_positions(proposal_data)


def _rows_of(db: Session, proposal_id: int) -> tuple[dict[str, PositionItem], dict[str, int]]:
    """Строки предложения из БД, по ключу файла: сами ORM-объекты (материализация
    разделов их будет менять) и отдельно их id (биекция и поиск решений)."""
    rows = db.execute(
        sa.select(PositionItem).where(PositionItem.proposal_id == proposal_id)
    ).scalars().all()
    rows_by_key = {row.position_key_in_proposal: row for row in rows}
    ids_by_key = {key: row.id for key, row in rows_by_key.items()}
    return rows_by_key, ids_by_key


def _overrides_of(db: Session, ids_by_key: dict[str, int]) -> dict[str, int]:
    """Действующие решения предложения — по КЛЮЧУ ФАЙЛА, как того ждёт
    `CategoryResolver.resolve_proposal` (спека §1.6), а не по id строки БД:
    таблица решений хранит id, а резолвер живёт в терминах ключей `raw_data`."""
    if not ids_by_key:
        return {}
    id_to_key = {row_id: key for key, row_id in ids_by_key.items()}
    rows = db.execute(
        sa.select(
            EstimateCategoryOverride.position_item_id, EstimateCategoryOverride.work_category_id
        ).where(EstimateCategoryOverride.position_item_id.in_(ids_by_key.values()))
    ).all()
    return {id_to_key[position_item_id]: category_id for position_item_id, category_id in rows}


def _require_bijection(
    lot_key: str, positions: Mapping[str, Any], ids_by_key: dict[str, int]
) -> None:
    """Ключ `raw_data` ↔ строка БД (спека §2.3, §1.7). Сегодня расхождение
    недостижимо: импорт вставляет строку на каждый ключ и обратно ничего не
    удаляет. Если оно возникло — значит мы чего-то не знаем о смете, и молчать
    об этом нельзя: тихий пропуск дал бы паспорт, правдоподобный ровно настолько,
    насколько неверный.
    """
    only_in_file = set(positions) - set(ids_by_key)
    only_in_db = set(ids_by_key) - set(positions)
    if only_in_file or only_in_db:
        raise CategoryOverrideError(
            "mapping_broken",
            f"Предложение «{lot_key}»: разобранная копия файла и строки сметы не "
            f"соответствуют друг другу (только в файле: {sorted(only_in_file)[:5]}; "
            f"только в БД: {sorted(only_in_db)[:5]}). Пересчёт статей отменён.",
        )


def _materialize_chapters(
    rows_by_key: dict[str, PositionItem], resolution: ProposalResolution
) -> int:
    """Пишет решение резолва в строки-разделы — ТОЛЬКО изменившееся, тогда
    `chapters_updated` честно отвечает «сколько строк поехало», а не «сколько
    просмотрено» (это и держит идемпотентность повторного вызова: второй прогон
    без новых решений обязан вернуть ноль).
    """
    changed = 0
    for key, decision in resolution.rows.items():
        row = rows_by_key.get(key)
        if row is None or not row.is_chapter:
            continue
        if (row.work_category_id, row.category_source) == (
            decision.work_category_id,
            decision.category_source,
        ):
            continue
        row.work_category_id = decision.work_category_id
        row.category_source = decision.category_source
        changed += 1
    return changed


def _materialize_extras(
    db: Session, proposal_id: int, positions: Mapping[str, Any], resolution: ProposalResolution
) -> int:
    """Статья строки допработ выводится из плана резолва (спека §1.4), поэтому
    разнос раздела обязан её пересчитать: множество кандидатов по номеру раздела
    меняется с `{None}` на `{X}`, и `resolve_ref` переезжает с «кандидат без
    статьи» на статью. Состав и суммы строк НЕ перестраиваются — `parse_lines` и
    `build_rows` здесь не зовутся вовсе.
    """
    by_number = categories_by_chapter_number(positions, resolution)
    rows = db.execute(
        sa.select(EstimateAdditionalWork).where(
            EstimateAdditionalWork.proposal_id == proposal_id
        )
    ).scalars().all()
    changed = 0
    for row in rows:
        if row.chapter_ref_raw is None:
            continue  # ck_..._unresolved_ref: без ссылки статьи быть не может
        category_id, _reason = resolve_ref(row.chapter_ref_raw, by_number)
        if row.work_category_id != category_id:
            row.work_category_id = category_id
            changed += 1
    return changed
