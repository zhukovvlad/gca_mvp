"""Цепочки разделов и зона (спека лестницы аналогов `2026-11-07-analog-ladder-design.md`
§2.4, §2.17, §2.19).

Зона — свойство цепочки разделов, а не строки: решение принимается один раз на ключ
цепочки и действует на все сметы. Здесь:

- `assign_section_chains` — шаг импорта сессии B (между `match_positions` и
  `route_positions`): цепочки строк сметы, правило и задания `section_zone` для
  цепочек, СОЗДАННЫХ этим вызовом. Цепочку с зоной не трогает; счётчики `import_jobs`
  не расширяет (как членства, `AGENTS.md` §5);
- `backfill_chains` — команда `zones-backfill`: то же для строк без цепочки, пересчёт
  правила, постановка заданий; идемпотентна;
- `list_chains` / `decide_zone` — очередь «Зоны» вкладки «Предложения» и решение
  администратора (источник `human`, побеждает правило и модель).

Функции мутации не коммитят (коммит за роутером), кроме `backfill_chains`: она идёт
пачками и коммитит каждую (как `run_backfill`).
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from config import Settings
from config import settings as default_settings
from crud.common import DomainError, iso
from crud.our_price_load import _claim_paused
from models import (
    ZONES,
    Estimate,
    Lot,
    PositionItem,
    Proposal,
    SectionChain,
    SemanticAttemptOutcome,
    SemanticJob,
    SemanticJobAttempt,
    SemanticJobKind,
    SemanticJobStatus,
    SemanticWorkerState,
)
from services.context_routing import chapter_paths
from services.semantic_answer import AnswerSchemaError
from services.semantic_cost import known_prefix_tokens, reserve_for_known_prefix, tariffs_from
from services.zone_request import (
    insert_zone_jobs,
    load_zone_material,
    parse_zone_answer,
    render_zone_batch,
)
from services.zone_rules import chain_key, normalize_title, rule_zone

ChainFilter = Literal["disputed", "undetermined", "rule", "model", "human", "all"]

#: Размер страницы очереди «Зоны».
PAGE_SIZE = 50

#: Сколько наименований строк показывает очередь «Зоны» у цепочки.
SAMPLE_TITLES = 3

_CODE_INVALID_ZONE = "invalid_zone"


@dataclass(frozen=True)
class ChainsOutcome:
    """Итог шага импорта: цепочек создано, строк привязано, заданий поставлено."""

    created: int
    assigned: int
    enqueued: int


@dataclass(frozen=True)
class BackfillReport:
    """Итог команды: цепочек создано, строк привязано, зон поставлено правилом
    (цепочкам без зоны), зон пересчитано правилом (`--reapply-rule`), заданий
    поставлено и оценка их резерва, доллары."""

    chains_created: int
    rows_assigned: int
    rule_set: int
    rule_changed: int
    enqueued: int
    enqueue_estimate_usd: Decimal


# ---------------------------------------------------------------------------
#  Присвоение цепочек
# ---------------------------------------------------------------------------

_ASSIGN_ROWS = sa.text(
    "UPDATE position_items SET section_chain_id = :chain_id "
    "WHERE id = ANY(:ids) AND section_chain_id IS NULL"
).bindparams(sa.bindparam("ids", type_=sa.ARRAY(sa.BigInteger)))


def _assign_rows(
    db: Session, pairs: Sequence[tuple[int, int]]
) -> tuple[list[int], int]:
    """Привязывает строки `(id строки, id ближней главы)` к цепочкам; цепочки
    создаются `INSERT ... ON CONFLICT (chain_key) DO NOTHING`. Возвращает `id`
    созданных (не найденных) цепочек и число привязанных строк. Названия цепочки —
    как у первой встреченной строки."""
    if not pairs:
        return [], 0
    paths = chapter_paths(db, {chapter_id for _, chapter_id in pairs})

    titles_of: dict[str, tuple[str, ...]] = {}
    items_of: dict[str, list[int]] = {}
    for item_id, chapter_id in sorted(pairs):
        titles = tuple(title or "" for title in paths[chapter_id])
        key = chain_key(titles)
        titles_of.setdefault(key, titles)
        items_of.setdefault(key, []).append(item_id)

    # Вставка по возрастанию ключа: две параллельные постановки ждут друг друга на
    # уникальном индексе в одном порядке.
    created = db.execute(
        pg_insert(SectionChain)
        .on_conflict_do_nothing(index_elements=[sa.func.md5(SectionChain.chain_key)])
        .returning(SectionChain.id),
        [{"chain_key": key, "titles": list(titles_of[key])} for key in sorted(titles_of)],
    ).scalars().all()
    chain_ids = dict(
        db.execute(
            sa.select(SectionChain.chain_key, SectionChain.id).where(
                SectionChain.chain_key.in_(list(titles_of))
            )
        ).all()
    )
    assigned = 0
    for key in sorted(items_of):
        assigned += db.execute(
            _ASSIGN_ROWS, {"chain_id": chain_ids[key], "ids": items_of[key]}
        ).rowcount
    return list(created), assigned


def _set_rule_zones(db: Session, chains: Sequence[tuple[int, Sequence[str]]]) -> int:
    """Правило для цепочек `(id, названия)`, у которых нет ни зоны, ни предложения;
    возвращает число поставленных зон. Условие `zone IS NULL` в самом `UPDATE`: зону,
    поставленную параллельно (человеком, моделью), правило не перекроет."""
    by_zone: dict[str, list[int]] = {}
    for chain_id, titles in chains:
        zone = rule_zone(titles)
        if zone is not None:
            by_zone.setdefault(zone, []).append(chain_id)
    updated = 0
    for zone, ids in sorted(by_zone.items()):
        updated += db.execute(
            sa.update(SectionChain)
            .where(
                SectionChain.id.in_(ids),
                SectionChain.zone.is_(None),
                SectionChain.proposed_zone.is_(None),
            )
            .values(zone=zone, zone_source="rule")
        ).rowcount
    return updated


def _undecided_ids(db: Session, chain_ids: Sequence[int]) -> list[int]:
    """Из `chain_ids` — цепочки без зоны и без предложения."""
    if not chain_ids:
        return []
    return list(
        db.execute(
            sa.select(SectionChain.id)
            .where(
                SectionChain.id.in_(list(chain_ids)),
                SectionChain.zone.is_(None),
                SectionChain.proposed_zone.is_(None),
            )
            .order_by(SectionChain.id)
        ).scalars()
    )


def _titles_of_chains(db: Session, chain_ids: Sequence[int]) -> list[tuple[int, list[str]]]:
    if not chain_ids:
        return []
    return [
        (row.id, row.titles)
        for row in db.execute(
            sa.select(SectionChain.id, SectionChain.titles)
            .where(SectionChain.id.in_(list(chain_ids)))
            .order_by(SectionChain.id)
        ).all()
    ]


def assign_section_chains(
    db: Session, estimate_ids: Sequence[int], *, settings: Settings = default_settings
) -> ChainsOutcome:
    """Шаг импорта: строки (не главы) смет без цепочки получают `section_chain_id`.
    Правило и задание `section_zone` — только для цепочек, созданных этим вызовом:
    найденную цепочку (с зоной любого источника, с предложением, спорную) вызов не
    меняет. Не коммитит; отказ (`RoutingError` — цикл в разделах) откатывает вызывающий."""
    ids = list(dict.fromkeys(estimate_ids))
    if not ids:
        return ChainsOutcome(0, 0, 0)
    pairs = [
        (row.id, row.chapter_item_id)
        for row in db.execute(
            sa.select(PositionItem.id, PositionItem.chapter_item_id)
            .join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(
                Lot.estimate_id.in_(ids),
                PositionItem.is_chapter.is_(False),
                PositionItem.chapter_item_id.is_not(None),
                PositionItem.section_chain_id.is_(None),
            )
            .order_by(PositionItem.id)
        ).all()
    ]
    created, assigned = _assign_rows(db, pairs)
    _set_rule_zones(db, _titles_of_chains(db, created))
    undecided = _undecided_ids(db, created)
    enqueued = insert_zone_jobs(db, render_zone_batch(db, undecided, settings=settings))
    return ChainsOutcome(created=len(created), assigned=assigned, enqueued=enqueued)


# ---------------------------------------------------------------------------
#  Команда заполнения
# ---------------------------------------------------------------------------

def _rows_without_chain(db: Session, *, after: int, limit: int) -> list[tuple[int, int]]:
    return [
        (row.id, row.chapter_item_id)
        for row in db.execute(
            sa.select(PositionItem.id, PositionItem.chapter_item_id)
            .where(
                PositionItem.id > after,
                PositionItem.is_chapter.is_(False),
                PositionItem.chapter_item_id.is_not(None),
                PositionItem.section_chain_id.is_(None),
            )
            .order_by(PositionItem.id)
            .limit(limit)
        ).all()
    ]


def _reapply_rule(db: Session) -> int:
    """Пересчёт цепочек с источником `rule`: зона по правилу, а если правило больше
    не отвечает, — без зоны. Плюс нерешённые цепочки с предложением модели: если
    правило теперь отвечает, зона ставится правилом, предложение снимается (правило
    стоит выше модели, как у новой цепочки). Цепочки с зоной `model` и `human` не
    трогаются (условия — в самих `UPDATE`). Возвращает число изменённых."""
    by_zone: dict[str | None, list[int]] = {}
    for row in db.execute(
        sa.select(SectionChain.id, SectionChain.titles, SectionChain.zone)
        .where(SectionChain.zone_source == "rule")
        .order_by(SectionChain.id)
    ).all():
        zone = rule_zone(row.titles)
        if zone != row.zone:
            by_zone.setdefault(zone, []).append(row.id)
    changed = 0
    for zone, ids in sorted(by_zone.items(), key=lambda pair: pair[0] or ""):
        values = (
            dict(zone=zone) if zone is not None else dict(zone=None, zone_source=None)
        )
        changed += db.execute(
            sa.update(SectionChain)
            .where(SectionChain.id.in_(ids), SectionChain.zone_source == "rule")
            .values(**values)
        ).rowcount
    proposed_by_zone: dict[str, list[int]] = {}
    for row in db.execute(
        sa.select(SectionChain.id, SectionChain.titles)
        .where(SectionChain.zone.is_(None), SectionChain.proposed_zone.is_not(None))
        .order_by(SectionChain.id)
    ).all():
        zone = rule_zone(row.titles)
        if zone is not None:
            proposed_by_zone.setdefault(zone, []).append(row.id)
    for zone, ids in sorted(proposed_by_zone.items()):
        changed += db.execute(
            sa.update(SectionChain)
            .where(
                SectionChain.id.in_(ids),
                SectionChain.zone.is_(None),
                SectionChain.proposed_zone.is_not(None),
            )
            .values(
                zone=zone, zone_source="rule",
                proposed_zone=None, proposed_confidence=None, proposed_reason=None,
            )
        ).rowcount
    return changed


def _chains_to_enqueue(db: Session) -> list[int]:
    """Цепочки без зоны, без предложения и без задания `section_zone`."""
    has_job = (
        sa.select(sa.literal(1))
        .select_from(SemanticJob)
        .where(
            SemanticJob.kind == SemanticJobKind.section_zone.value,
            SemanticJob.section_chain_id == SectionChain.id,
        )
        .exists()
    )
    return list(
        db.execute(
            sa.select(SectionChain.id)
            .where(SectionChain.zone.is_(None), SectionChain.proposed_zone.is_(None), ~has_job)
            .order_by(SectionChain.id)
        ).scalars()
    )


def _estimate_reserve(db: Session, rendered: Sequence, settings: Settings) -> Decimal:
    """Сумма резервов заданий тарифами вида (токены префикса — наблюдавшиеся по
    `prefix_hash` или байты текста, как при захвате)."""
    tariffs = tariffs_from(settings, SemanticJobKind.section_zone)
    known: dict[str, int | None] = {}
    total = Decimal(0)
    for _chain_id, request in rendered:
        if request.prefix_hash not in known:
            known[request.prefix_hash] = known_prefix_tokens(db, request.prefix_hash)
        prefix = known[request.prefix_hash]
        total += reserve_for_known_prefix(
            prefix if prefix is not None else request.prefix_bytes,
            request,
            tariffs,
            request.body["max_tokens"],
        )
    return total


def backfill_chains(
    db: Session,
    *,
    enqueue: bool,
    reapply_rule: bool,
    batch: int = 500,
    before_enqueue: Callable[[int, Decimal], None] | None = None,
    settings: Settings = default_settings,
) -> BackfillReport:
    """Команда `zones-backfill`: цепочки для строк без `section_chain_id` (пачками по
    `batch` строк, коммит на пачку); пересчёт правила у цепочек с источником `rule`
    (`reapply_rule`); правило для цепочек без зоны и без предложения; задания
    `section_zone` для цепочек без зоны, без предложения и без задания (`enqueue`).
    Перед постановкой зовёт `before_enqueue(число заданий, оценка резерва)` — команда
    печатает счёт и оценку до того, как задания появятся. Идемпотентна: на неизменных
    данных второй запуск не создаёт и не ставит ничего."""
    created_total = assigned_total = 0
    last_id = 0
    while True:
        pairs = _rows_without_chain(db, after=last_id, limit=batch)
        if not pairs:
            break
        last_id = pairs[-1][0]
        created, assigned = _assign_rows(db, pairs)
        created_total += len(created)
        assigned_total += assigned
        db.commit()

    rule_changed = _reapply_rule(db) if reapply_rule else 0
    undecided = [
        row.id
        for row in db.execute(
            sa.select(SectionChain.id).where(
                SectionChain.zone.is_(None), SectionChain.proposed_zone.is_(None)
            )
        ).all()
    ]
    rule_set = _set_rule_zones(db, _titles_of_chains(db, undecided))
    db.commit()

    enqueued = 0
    estimate = Decimal(0)
    if enqueue:
        rendered: list = []
        pending = _chains_to_enqueue(db)
        for start in range(0, len(pending), batch):
            rendered.extend(render_zone_batch(db, pending[start:start + batch], settings=settings))
        estimate = _estimate_reserve(db, rendered, settings)
        if before_enqueue is not None:
            before_enqueue(len(rendered), estimate)
        for start in range(0, len(rendered), batch):
            enqueued += insert_zone_jobs(db, rendered[start:start + batch])
            db.commit()

    return BackfillReport(
        chains_created=created_total,
        rows_assigned=assigned_total,
        rule_set=rule_set,
        rule_changed=rule_changed,
        enqueued=enqueued,
        enqueue_estimate_usd=estimate,
    )


# ---------------------------------------------------------------------------
#  Очередь «Зоны»
# ---------------------------------------------------------------------------

#: Цепочки, которые массовое принятие переводит в решение человека: зоны нет, у модели
#: конкретный ответ (`unknown` — «не знаю» — остаётся в очереди).
_ACCEPTABLE = (
    SectionChain.zone.is_(None),
    SectionChain.proposed_zone.is_not(None),
    SectionChain.proposed_zone != "unknown",
)

#: Размер пачки коммита массового принятия зон.
ACCEPT_ALL_BATCH = 100


def _filter_condition(flt: str):
    if flt == "disputed":
        return sa.and_(SectionChain.zone.is_(None), SectionChain.proposed_zone.is_not(None))
    if flt == "undetermined":
        return sa.and_(SectionChain.zone.is_(None), SectionChain.proposed_zone.is_(None))
    if flt in ("rule", "model", "human"):
        return SectionChain.zone_source == flt
    if flt == "all":
        return sa.true()
    raise ValueError(f"неизвестный фильтр очереди зон: {flt!r}")


def _rows_of(db: Session, chain_ids: Sequence[int]) -> dict[int, tuple[int, int]]:
    """Строки цепочек во всех сметах: `id` цепочки -> (строк КП, строк договоров).
    Цепочки без строк в словаре нет. Один запрос на очередь «Зоны» и на панель строки."""
    return {
        row.chain_id: (row.kp, row.contracts)
        for row in db.execute(
            sa.select(
                PositionItem.section_chain_id.label("chain_id"),
                sa.func.count().filter(Estimate.offer_id.is_not(None)).label("kp"),
                sa.func.count().filter(Estimate.contract_id.is_not(None)).label("contracts"),
            )
            .select_from(PositionItem)
            .join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .join(Estimate, Estimate.id == Lot.estimate_id)
            .where(PositionItem.section_chain_id.in_(chain_ids))
            .group_by(PositionItem.section_chain_id)
        ).all()
    }


def _chain_items(db: Session, chains: Sequence[SectionChain]) -> list[dict]:
    """Элементы очереди «Зоны» для цепочек: одна форма на список и на одну цепочку."""
    ids = [chain.id for chain in chains]
    job_status: dict[int, str] = {}
    for chain_id, status in db.execute(
        sa.select(SemanticJob.section_chain_id, SemanticJob.status)
        .where(
            SemanticJob.kind == SemanticJobKind.section_zone.value,
            SemanticJob.section_chain_id.in_(ids),
        )
        .order_by(SemanticJob.section_chain_id, SemanticJob.id)
    ).all():
        job_status[chain_id] = status  # последнее задание цепочки — с наибольшим id
    rows_of = _rows_of(db, ids)
    samples = load_zone_material(db, ids, limit=SAMPLE_TITLES)
    return [
        {
            "id": chain.id,
            "titles": list(chain.titles),
            "zone": chain.zone,
            "zone_source": chain.zone_source,
            "proposed_zone": chain.proposed_zone,
            "proposed_confidence": (
                str(chain.proposed_confidence)
                if chain.proposed_confidence is not None else None
            ),
            "proposed_reason": chain.proposed_reason,
            "job_status": job_status.get(chain.id),
            "rows_kp": rows_of.get(chain.id, (0, 0))[0],
            "rows_contracts": rows_of.get(chain.id, (0, 0))[1],
            "sample_titles": [title for title, _unit in samples[chain.id].rows],
        }
        for chain in chains
    ]


_LIVE_JOB_STATUSES = (SemanticJobStatus.pending.value, SemanticJobStatus.running.value)
_STUCK_JOB_STATUSES = (SemanticJobStatus.error.value, SemanticJobStatus.privacy_hold.value)


def _zone_model_state(db: Session, chain: SectionChain) -> str:
    """Состояние модели у цепочки без зоны: первое подходящее сверху (спека зоны в панели
    строки §1). Порядок повторяет правило строки: живое задание без остановки — «разбирает»;
    спорная, застрявшая или живая при остановке — «ждёт решения»."""
    statuses = set(
        db.execute(
            sa.select(SemanticJob.status).where(
                SemanticJob.kind == SemanticJobKind.section_zone.value,
                SemanticJob.section_chain_id == chain.id,
                SemanticJob.status.in_([*_LIVE_JOB_STATUSES, *_STUCK_JOB_STATUSES]),
            )
        ).scalars()
    )
    if statuses.intersection(_LIVE_JOB_STATUSES):
        if _claim_paused(db):
            return "stopped"
        paused_until = db.execute(
            sa.select(SemanticWorkerState.budget_paused_until)
        ).scalar_one_or_none()
        if paused_until is not None and paused_until > dt.datetime.now(dt.UTC):
            return "paused"
        return "running"
    if chain.proposed_zone == "unknown":
        return "unknown"
    if chain.proposed_zone is not None:
        return "proposed"
    if statuses.intersection(_STUCK_JOB_STATUSES):
        return "failed"
    return "none"


def _logged_zone_answer(db: Session, chain_id: int) -> dict | None:
    """Ответ модели из журнала вызовов: разбор `raw_response` последней по `id` попытки
    `ok` среди всех заданий `section_zone` цепочки. Попытки нет или ответ не по схеме —
    `None`, без ошибки: предыдущую попытку взамен не берём."""
    raw = db.execute(
        sa.select(SemanticJobAttempt.raw_response)
        .join(SemanticJob, SemanticJob.id == SemanticJobAttempt.job_id)
        .where(
            SemanticJob.kind == SemanticJobKind.section_zone.value,
            SemanticJob.section_chain_id == chain_id,
            SemanticJobAttempt.outcome == SemanticAttemptOutcome.ok.value,
        )
        .order_by(SemanticJobAttempt.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if raw is None:
        return None
    try:
        answer = parse_zone_answer(raw)
    except AnswerSchemaError:
        return None
    return {"zone": answer.zone, "confidence": str(answer.confidence), "reason": answer.reason}


def zone_chain_info(db: Session, chain_id: int, *, settings: Settings = default_settings) -> dict:
    """Блок «Зона места» панели строки КП (спека зоны в панели строки §1): цепочка, зона и её
    источник, охват, состояние модели и её ответ. Только читает. Нет такой цепочки — 404."""
    chain = db.get(SectionChain, chain_id)
    if chain is None:
        raise DomainError(404, f"Цепочка разделов {chain_id} не найдена.")
    kp, contracts = _rows_of(db, [chain.id]).get(chain.id, (0, 0))
    if chain.zone is None:
        model_state: str | None = _zone_model_state(db, chain)
        model_answer = (
            {
                "zone": chain.proposed_zone,
                "confidence": str(chain.proposed_confidence),
                "reason": chain.proposed_reason,
            }
            if chain.proposed_zone is not None and chain.proposed_confidence is not None
            else None
        )
    else:
        model_state = None
        model_answer = _logged_zone_answer(db, chain.id)
    return {
        "id": chain.id,
        "titles": list(chain.titles),
        "zone": chain.zone,
        "zone_source": chain.zone_source,
        "decided_at": iso(chain.decided_at) if chain.zone_source == "human" else None,
        "rows_kp": kp,
        "rows_contracts": contracts,
        "model_state": model_state,
        "model_answer": model_answer,
        "threshold": str(settings.SEMANTIC_ZONE_AUTO_ACCEPT_THRESHOLD),
    }


def list_chains(
    db: Session, *, filter: ChainFilter, q: str | None, page: int
) -> dict:  # noqa: A002 — имя параметра задано интерфейсом
    """Страница очереди «Зоны»: `{items, total}`. Поиск `q` — по ключу цепочки (запрос
    нормализуется как ключ, совпадение по подстроке буквально, без шаблонов `LIKE`)."""
    conditions = [_filter_condition(filter)]
    if q is not None and normalize_title(q):
        conditions.append(sa.func.strpos(SectionChain.chain_key, normalize_title(q)) > 0)
    stmt = sa.select(SectionChain).where(*conditions)
    total = db.execute(
        sa.select(sa.func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()
    page = max(1, page)
    chains = db.execute(
        stmt.order_by(SectionChain.id).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    ).scalars().all()
    acceptable_total = db.execute(
        sa.select(sa.func.count()).select_from(SectionChain).where(*_ACCEPTABLE)
    ).scalar_one()
    if not chains:
        return {"items": [], "total": total, "acceptable_total": acceptable_total}

    return {
        "items": _chain_items(db, chains),
        "total": total,
        # Сколько спорных цепочек «Принять все предложения модели» примет (без «не знаю»).
        "acceptable_total": acceptable_total,
    }


def get_chain(db: Session, chain_id: int) -> dict:
    """Одна цепочка в той же форме, что элемент `list_chains`; нет такой — 404."""
    chain = db.get(SectionChain, chain_id)
    if chain is None:
        raise DomainError(404, f"Цепочка разделов {chain_id} не найдена.")
    return _chain_items(db, [chain])[0]


def decide_zone(db: Session, chain_id: int, zone: str, *, actor_id: int) -> None:
    """Решение администратора: зона цепочки с источником `human`, предложение модели
    снято. Побеждает правило и модель; ничем не пересчитывается. Не коммитит."""
    if zone not in ZONES:
        raise DomainError(
            422,
            f"Зона {zone!r} не из списка: {', '.join(ZONES)}.",
            code=_CODE_INVALID_ZONE,
        )
    chain = db.execute(
        sa.select(SectionChain)
        .where(SectionChain.id == chain_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if chain is None:
        raise DomainError(404, f"Цепочка разделов {chain_id} не найдена.")
    chain.zone = zone
    chain.zone_source = "human"
    chain.decided_by = actor_id
    chain.decided_at = dt.datetime.now(dt.UTC)
    chain.proposed_zone = chain.proposed_confidence = chain.proposed_reason = None
    db.flush()


def accept_all_zones(db: Session, *, actor_id: int) -> dict[str, int]:
    """Принять все предложения модели по зонам: каждое — решение человека (`decide_zone`).
    Цепочки по возрастанию id; перед записью строка перечитывается под `FOR UPDATE`, и если
    зона уже стоит или предложение пропало либо стало «не знаю» — цепочка пропускается.
    Коммитит сама пачками по `ACCEPT_ALL_BATCH`: одна транзакция на все цепочки держала бы
    замки и откатывалась бы целиком. Возвращает `{accepted, skipped}`."""
    ids = list(
        db.execute(sa.select(SectionChain.id).where(*_ACCEPTABLE).order_by(SectionChain.id))
        .scalars()
    )
    accepted = skipped = 0
    try:
        for start in range(0, len(ids), ACCEPT_ALL_BATCH):
            for chain_id in ids[start:start + ACCEPT_ALL_BATCH]:
                chain = db.execute(
                    sa.select(SectionChain)
                    .where(SectionChain.id == chain_id, *_ACCEPTABLE)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                ).scalar_one_or_none()
                if chain is None:
                    skipped += 1
                    continue
                decide_zone(db, chain_id, chain.proposed_zone, actor_id=actor_id)
                accepted += 1
            db.commit()
    except Exception:
        db.rollback()
        raise
    return {"accepted": accepted, "skipped": skipped}
