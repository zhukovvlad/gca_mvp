"""Задание `section_zone`: тело запроса, постановка и запись ответа (спека лестницы
аналогов `2026-11-07-analog-ladder-design.md` §2.5).

Предмет задания — цепочка разделов (`section_chain_id`). Наружу уходят названия
цепочки сверху вниз и до десяти самых частых наименований строк цепочки с текстом
единицы; цен, объёмов, подрядчиков, договоров и объектов в теле нет (`AGENTS.md`
§3). Название раздела может нести имя объекта — тело проходит
`find_privacy_matches` как у остальных видов.

Отпечаток запроса (`request_hash`) — от ключа цепочки, профиля модели и текста
промпта, а не от наименований строк: цепочка спрашивается один раз, новые строки
под ней повторного вопроса не вызывают. Профиль — как у `family_suggestion`
(`SEMANTIC_MODEL`, без рассуждения), предел ответа — `SEMANTIC_ZONE_MAX_TOKENS`.

Модуль не вызывает провайдера. Запись ответа (`record_zone_result`) зону пишет
только цепочке без зоны: решение человека, правило и прежний ответ модели ответ не
перекрывает (он остаётся только в попытке, К2 плана).
"""
from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from config import Settings
from models import (
    ZONES,
    PositionItem,
    SectionChain,
    SemanticCancelReason,
    SemanticJob,
    SemanticJobKind,
    SemanticJobStatus,
    UnitOfMeasure,
)
from services.semantic_answer import (
    AnswerSchemaError,
    _extract_json_text,
    _parse_confidence,
    _parse_reason,
    _parse_single_object,
)
from services.semantic_reconcile import Fingerprint, _insert_job_rows, _job_row
from services.semantic_request import (
    _NO_UNIT_DISPLAY,
    SERIALIZATION_VERSION,
    RenderedRequest,
    _collapse_whitespace,
    _sha256_hex,
)
from services.semantic_rules import PLACE_DICTIONARY_VERSION

#: Версия текста промпта — столбец аудита задания, в хэши не входит.
ZONE_PROMPT_VERSION = 1

#: Версия формы ответа — столбец аудита задания.
ZONE_RESPONSE_SCHEMA_VERSION = "zone:1"

#: Сколько наименований строк цепочки уходит модели (спека §2.5).
MAX_ROW_NAMES: Final = 10

#: Предел `reason` в ответе (спека §2.5).
MAX_REASON_CHARS: Final = 300

#: Коды ответа: четыре зоны и «не знаю».
ANSWER_ZONES: Final = (*ZONES, "unknown")

ZONE_PROMPT = """Ты сметчик-каталогизатор строительной компании-заказчика. Тебе дают цепочку разделов сметы (сверху вниз, от корня к ближайшему разделу) и самые частые наименования работ из этого раздела.

Вопрос: к какой части объекта относятся работы этого раздела. Части четыре:
- "underground" — подземная часть: подвал, подземный паркинг, работы ниже отметки 0.000, минусовые этажи;
- "aboveground" — надземная часть: этажи здания, кровля, фасады, чердак, мансарда;
- "territory" — территория: благоустройство, дороги, проезды, двор, площадки, озеленение, наружное освещение, малые формы;
- "whole" — весь объект: работы, не привязанные к месту, — проектирование, охрана, гарантии, временные здания и сооружения.

Если по названиям и наименованиям часть определить нельзя, ответь "unknown". Не угадывай.

Ответ — только JSON без пояснений:
{"zone": "underground" | "aboveground" | "territory" | "whole" | "unknown", "confidence": <число от 0 до 1>, "reason": "<краткая причина, не длиннее 300 символов>"}"""


@dataclass(frozen=True)
class ZoneMaterial:
    """Вход задания: ключ и названия цепочки (сверху вниз, как у первой встреченной
    строки) и самые частые наименования строк с единицей."""

    chain_key: str
    titles: tuple[str, ...]
    rows: tuple[tuple[str, str | None], ...]
    """`(наименование, название единицы | None)` — по убыванию частоты по всем
    сметам, при равенстве — по меньшему `id` строки."""


@dataclass(frozen=True)
class ZoneAnswer:
    """Разобранный ответ: код зоны (`unknown` — «не знаю»), уверенность в `[0, 1]` и
    причина (не пустая, не длиннее 300 символов)."""

    zone: str
    confidence: Decimal
    reason: str


ZoneOutcome = Literal["applied", "proposed", "unchanged", "stale"]


# ---------------------------------------------------------------------------
#  Материал и тело запроса
# ---------------------------------------------------------------------------

def load_zone_material(
    db: Session, chain_ids: Collection[int], *, limit: int = MAX_ROW_NAMES
) -> dict[int, ZoneMaterial]:
    """Материал цепочек двумя запросами на любое их число. Наименования сгруппированы
    по паре «наименование, единица»; `limit` самых частых на цепочку, при равенстве —
    по меньшему `id` строки."""
    ids = sorted(set(chain_ids))
    if not ids:
        return {}
    chains = db.execute(
        sa.select(SectionChain.id, SectionChain.chain_key, SectionChain.titles).where(
            SectionChain.id.in_(ids)
        )
    ).all()

    grouped = (
        sa.select(
            PositionItem.section_chain_id.label("chain_id"),
            PositionItem.job_title_in_proposal.label("title"),
            UnitOfMeasure.name.label("unit"),
            sa.func.count().label("cnt"),
            sa.func.min(PositionItem.id).label("first_id"),
        )
        .select_from(PositionItem)
        .outerjoin(UnitOfMeasure, UnitOfMeasure.id == PositionItem.unit_id)
        .where(PositionItem.section_chain_id.in_(ids))
        .group_by(PositionItem.section_chain_id, PositionItem.job_title_in_proposal, UnitOfMeasure.name)
        .subquery()
    )
    ranked = sa.select(
        grouped.c.chain_id,
        grouped.c.title,
        grouped.c.unit,
        sa.func.row_number()
        .over(
            partition_by=grouped.c.chain_id,
            order_by=(grouped.c.cnt.desc(), grouped.c.first_id),
        )
        .label("rn"),
    ).subquery()
    rows_of: dict[int, list[tuple[str, str | None]]] = {chain_id: [] for chain_id in ids}
    for row in db.execute(
        sa.select(ranked.c.chain_id, ranked.c.title, ranked.c.unit)
        .where(ranked.c.rn <= limit)
        .order_by(ranked.c.chain_id, ranked.c.rn)
    ).all():
        rows_of[row.chain_id].append((row.title, row.unit))

    return {
        chain.id: ZoneMaterial(
            chain_key=chain.chain_key,
            titles=tuple(chain.titles),
            rows=tuple(rows_of[chain.id]),
        )
        for chain in chains
    }


def _user_text(material: ZoneMaterial) -> str:
    chain = "\n".join(
        f"{i}. {_collapse_whitespace(title) or '(без названия)'}"
        for i, title in enumerate(material.titles, 1)
    )
    if material.rows:
        rows = "\n".join(
            f"{i}. {_collapse_whitespace(title)} [{unit if unit else _NO_UNIT_DISPLAY}]"
            for i, (title, unit) in enumerate(material.rows, 1)
        )
    else:
        rows = "(строк нет)"
    return (
        f"ЦЕПОЧКА РАЗДЕЛОВ (сверху вниз):\n{chain}\n\n"
        f"НАИМЕНОВАНИЯ РАБОТ РАЗДЕЛА (самые частые):\n{rows}"
    )


def render_zone_material(material: ZoneMaterial, *, settings: Settings) -> RenderedRequest:
    """Каноническое тело запроса и отпечатки. Детерминирован, в базу не ходит.
    `request_hash` не зависит от наименований строк: только от ключа цепочки и
    профиля запроса."""
    system_blocks = [
        {"type": "text", "text": ZONE_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]
    user_text = _user_text(material)
    body = {
        "model": settings.SEMANTIC_MODEL,
        "temperature": 0,
        "max_tokens": settings.SEMANTIC_ZONE_MAX_TOKENS,
        "reasoning": {"enabled": False},
        "usage": {"include": True},
        "messages": [
            {"role": "system", "content": system_blocks},
            {"role": "user", "content": user_text},
        ],
    }
    request_hash = _sha256_hex(
        {
            "serialization_version": SERIALIZATION_VERSION,
            "model": body["model"],
            "max_tokens": body["max_tokens"],
            "reasoning": body["reasoning"],
            "system": system_blocks,
            "chain_key": material.chain_key,
        }
    )
    return RenderedRequest(
        body=body,
        request_hash=request_hash,
        prefix_hash=_sha256_hex(
            {
                "serialization_version": SERIALIZATION_VERSION,
                "model": settings.SEMANTIC_MODEL,
                "system": system_blocks,
            }
        ),
        candidates_hash=_sha256_hex(material.chain_key),
        input_hash=_sha256_hex({"serialization_version": SERIALIZATION_VERSION, "user": user_text}),
        prefix_bytes=sum(len(block["text"].encode("utf-8")) for block in system_blocks),
        user_bytes=len(user_text.encode("utf-8")),
        place_dictionary_version=PLACE_DICTIONARY_VERSION,
    )


def render_zone_request(db: Session, chain_id: int, *, settings: Settings) -> RenderedRequest:
    """Текущее тело запроса задания зоны цепочки. Цепочка существует всегда (внешний
    ключ задания `RESTRICT`)."""
    material = load_zone_material(db, [chain_id])[chain_id]
    return render_zone_material(material, settings=settings)


# ---------------------------------------------------------------------------
#  Постановка
# ---------------------------------------------------------------------------

def render_zone_batch(
    db: Session, chain_ids: Collection[int], *, settings: Settings
) -> list[tuple[int, RenderedRequest]]:
    """Тела запросов цепочек по возрастанию `id`: материал читается один раз на
    весь набор."""
    materials = load_zone_material(db, chain_ids)
    return [
        (chain_id, render_zone_material(materials[chain_id], settings=settings))
        for chain_id in sorted(materials)
    ]


def insert_zone_jobs(db: Session, rendered: Sequence[tuple[int, RenderedRequest]]) -> int:
    """Задания `pending` по готовым телам одним `INSERT ... ON CONFLICT DO NOTHING`
    (арбитр — индекс заданий, тот же, что у остальных видов); строки по возрастанию
    `id` цепочки. Возвращает число созданных."""
    rows = []
    for chain_id, request in sorted(rendered, key=lambda pair: pair[0]):
        fingerprint = Fingerprint(
            SemanticJobKind.section_zone, None, None, None, request.request_hash
        )
        row = _job_row(fingerprint, request, unit_id=None, prompt_version=ZONE_PROMPT_VERSION)
        row["response_schema_version"] = ZONE_RESPONSE_SCHEMA_VERSION
        row["section_chain_id"] = chain_id
        rows.append(row)
    return _insert_job_rows(db, rows)


# ---------------------------------------------------------------------------
#  Ответ модели
# ---------------------------------------------------------------------------

_ANSWER_KEYS: Final = ("zone", "confidence", "reason")


def parse_zone_answer(raw: str) -> ZoneAnswer:
    """Строгий разбор ответа `{zone, confidence, reason}`: обрамление и единственный
    объект — как у остальных видов, затем ключи, код зоны, уверенность в `[0, 1]` и
    причина (непустая, не длиннее 300 символов). Ответ не по схеме — `AnswerSchemaError`."""
    obj = _parse_single_object(_extract_json_text(raw))
    missing = [key for key in _ANSWER_KEYS if key not in obj]
    if missing:
        raise AnswerSchemaError("missing_field", f"нет обязательных полей: {', '.join(missing)}")
    zone = obj["zone"]
    if not isinstance(zone, str) or zone not in ANSWER_ZONES:
        raise AnswerSchemaError("bad_type", "zone вне закрытого списка зон")
    confidence = _parse_confidence(obj["confidence"])
    reason = _parse_reason(obj["reason"])
    if len(reason) > MAX_REASON_CHARS:
        raise AnswerSchemaError("bad_type", f"reason длиннее {MAX_REASON_CHARS} символов")
    return ZoneAnswer(zone=zone, confidence=confidence, reason=reason)


def record_zone_result(
    db: Session, job: SemanticJob, answer: ZoneAnswer, *, settings: Settings
) -> ZoneOutcome:
    """Применяет разобранный ответ к цепочке и закрывает задание; не коммитит.

    Вызывающий владеет захватом и держит замки в порядке «домен раньше задания»:
    цепочка `FOR UPDATE`, затем задание `FOR UPDATE`. Исходы:
    - `stale` — отпечаток запроса уже другой (профиль или промпт сменили): задание
      `cancelled/input_changed`, цепочка не меняется;
    - `unchanged` — у цепочки уже есть зона (человек, правило или прежний ответ):
      цепочка не меняется, ответ остаётся в попытке; задание `done`;
    - `applied` — уверенность не ниже порога и зона не `unknown`: зона с источником
      `model`;
    - `proposed` — иначе: предложение (`proposed_*`), цепочка спорная."""
    assert job.section_chain_id is not None
    chain = db.execute(
        sa.select(SectionChain)
        .where(SectionChain.id == job.section_chain_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()

    current = render_zone_request(db, chain.id, settings=settings)
    job.claim_token = None
    if current.request_hash != job.request_hash:
        job.status = SemanticJobStatus.cancelled.value
        job.cancel_reason = SemanticCancelReason.input_changed.value
        db.flush()
        return "stale"

    job.status = SemanticJobStatus.done.value
    if chain.zone is not None:
        db.flush()
        return "unchanged"
    if answer.zone != "unknown" and answer.confidence >= settings.SEMANTIC_ZONE_AUTO_ACCEPT_THRESHOLD:
        chain.zone = answer.zone
        chain.zone_source = "model"
        chain.proposed_zone = chain.proposed_confidence = chain.proposed_reason = None
        outcome: ZoneOutcome = "applied"
    else:
        chain.proposed_zone = answer.zone
        chain.proposed_confidence = answer.confidence
        chain.proposed_reason = answer.reason
        outcome = "proposed"
    db.flush()
    return outcome
