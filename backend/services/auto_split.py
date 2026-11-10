"""Автоделение контекстов «к делению» по разделам сметы, его отмена и «поправить»
(спека упрощения каталога §2.5, §2.7).

Вход — контекст с подсказкой `variant_split_hint = 'path_conflict'`: по путям его членов модель
не смогла назвать одно значение главных параметров. Делит он по первому уровню цепочки
разделов, на котором члены расходятся (`plan_groups`): для каждой группы модель спрашивается
отдельно по её путям, группы с одинаковыми значениями сливаются, остальные получают свои
контексты и правило маршрутизации по заголовку (`choose_rule`).

Деление идёт только заданием очереди `auto_split`. Вызов модели стоит вне транзакции, запись
(`auto_split_context`) — одна транзакция под замком корзины и строкой задания `FOR UPDATE`;
перед любой записью две проверки: задание всё ещё наше и отпечаток входа
(`auto_split_fingerprint`) тот же, что при захвате. Отпечаток — sha256 от членов контекста с
`routed_by` и правилом, нормализованной цепочки разделов каждого, семьи, варианта, подсказки,
пометки и архивации контекста, действующей версии схемы семьи (id и `revision`), строк
значений контекста и `request_hash` запроса каждой группы. Устаревший ответ не пишет ничего.

Система действует от имени `actor_id = NULL`: правила и события без автора. Автодействие
пишется в «Журнал ИИ» (`services/ai_journal.py`) той же транзакцией.

Отмена (`undo_split`) берёт нынешних членов всех частей, снимает только правила записи и
возвращает контекст в «конфликт путей». Она недоступна после ручной правки частей
(`split_lock`). «Поправить» — та же отмена без пометки «автоделение не трогает» и новое
задание с заданным уровнем.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from config import Settings
from config import settings as app_settings
from models import (
    CatalogAutoAction,
    CatalogContext,
    CatalogPosition,
    ContextBucket,
    ContextMember,
    ContextParameterValue,
    ContextRoutingRule,
    FamilyParameter,
    FamilyParameterSchema,
    PositionItem,
    RoutedBy,
    SchemaStatus,
    SemanticCancelReason,
    SemanticEvent,
    SemanticJob,
    SemanticJobKind,
    SemanticJobStatus,
    User,
)
from parser.sanitize_text import normalize_job_title_with_lemmatization
from services.ai_journal import record_action
from services.context_operations import merge_contexts, split_context
from services.context_routing import (
    PREDICATE_CHAPTER_CHAIN_CONTAINS,
    PREDICATE_NEAREST_CHAPTER_EQUALS,
    ChapterContext,
    RoutingError,
    RulePredicate,
    chapter_context,
    chapter_paths,
    evaluate_predicate,
    lock_buckets,
)
from services.semantic_answer import AnswerSchemaError
from services.semantic_reconcile import Fingerprint, _job_conflict_arbiter, _job_row
from services.semantic_request import RenderedRequest
from services.variant_answer import ValueItem, ValuesAnswer
from services.variant_request import (
    VALUES_PROMPT_VERSION,
    SchemaParameterIn,
    load_values_material,
    paths_hash_of,
    render_values_request,
    top_paths,
)
from services.work_variants import (
    JobGuard,
    _acquire_domain_locks,
    _is_applicable,
    _lock_catalog_row_statement,
    _lock_job_statement,
    _owns,
    active_main_ordinals,
    apply_values,
    normalize_value,
)

logger = logging.getLogger(__name__)

#: Ответ модели по одному параметру группы.
ValueAnswer = ValueItem

#: События, которые делает человек; после записи деления на любой из частей они
#: закрывают отмену (спека §2.5).
LOCK_EVENT_TYPES = (
    "members_moved",
    "context_split",
    "context_merged",
    "context_archived",
    "context_variant_assigned",
    "context_family_assigned",
)

#: Разделитель звеньев пути в запросе модели (как у `load_request_material`).
_PATH_SEPARATOR = " / "


# ---------------------------------------------------------------------------
#  Группы и правило — чистая часть
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SplitGroup:
    """Группа членов с одним заголовком на уровне расхождения. Пустой `title` — группа
    «прочее»: цепочка члена короче уровня."""

    title: str
    member_ids: tuple[int, ...]


@dataclass(frozen=True)
class SplitPlan:
    """`level` — индекс звена цепочки от корня (с нуля). Первая группа остаётся в исходном
    контексте: «прочее», если оно есть, иначе группа с наименьшим id члена."""

    level: int
    groups: tuple[SplitGroup, ...]


def _norm(title: str) -> str:
    return normalize_job_title_with_lemmatization(title) or ""


def _heading(normalized: Sequence[str], level: int) -> str | None:
    """Нормализованный заголовок на уровне; `None` — цепочка короче или заголовок пуст."""
    if level < len(normalized) and normalized[level]:
        return normalized[level]
    return None


def plan_groups(
    paths: Mapping[int, Sequence[str]], *, level: int | None = None
) -> SplitPlan | None:
    """Группы членов по заголовку на уровне расхождения; `None` — делить нечего.

    `paths` — путь каждого члена от корня до его раздела. Уровень расхождения — первый
    индекс от корня, на котором различаются нормализованные заголовки тех членов, чья цепочка
    до него доходит; `level` задаёт его явно («поправить»). Члены с цепочкой короче уровня —
    группа «прочее». Деления нет, если групп меньше двух, а также если «прочее» стоит рядом с
    единственным заголовком.
    """
    if len(paths) < 2:
        return None
    normalized = {member_id: tuple(_norm(title) for title in path) for member_id, path in paths.items()}
    if level is None:
        depth = max(len(chain) for chain in normalized.values())
        level = next(
            (
                index
                for index in range(depth)
                if len({h for chain in normalized.values() if (h := _heading(chain, index))}) > 1
            ),
            None,
        )
        if level is None:
            return None

    by_heading: dict[str, list[int]] = {}
    rest: list[int] = []
    for member_id in sorted(paths):
        heading = _heading(normalized[member_id], level)
        if heading is None:
            rest.append(member_id)
        else:
            by_heading.setdefault(heading, []).append(member_id)
    if len(by_heading) < 2:
        return None
    groups = [
        SplitGroup(title=paths[ids[0]][level], member_ids=tuple(ids))
        for ids in by_heading.values()
    ]
    if rest:
        groups.insert(0, SplitGroup(title="", member_ids=tuple(rest)))
    return SplitPlan(level=level, groups=tuple(groups))


def choose_rule(
    group: SplitGroup,
    others: Sequence[SplitGroup],
    chains: Mapping[int, ChapterContext],
    earlier_rules: Sequence[RulePredicate],
) -> RulePredicate | None:
    """Правило маршрутизации, которое ведёт в часть ровно члены `group`; `None` — такого нет.

    Заголовок группы — ближайший раздел каждого её члена → `nearest_chapter_equals`, иначе
    `chapter_chain_contains`. Уровень от корня здесь не годится: `chapter_level_equals`
    считает от ближайшего раздела, и у цепочек разной глубины один уровень от корня — разные
    уровни от ближайшего. Правило принято, только если настоящий `evaluate_predicate` покрывает
    всех членов группы (раньше правил корзины, `earlier_rules`, их никто не перехватывает) и не
    срабатывает ни на одном члене `others`: иначе перемаршрутизация перетащила бы чужие строки.
    """
    title = _norm(group.title)
    if not title:
        return None
    nearest = all(
        chains[member_id].nearest is not None and _norm(chains[member_id].nearest) == title
        for member_id in group.member_ids
    )
    rule: RulePredicate = {
        "kind": PREDICATE_NEAREST_CHAPTER_EQUALS if nearest else PREDICATE_CHAPTER_CHAIN_CONTAINS,
        "value": group.title,
    }
    for member_id in group.member_ids:
        chapters = chains[member_id]
        if any(evaluate_predicate(earlier, chapters) for earlier in earlier_rules):
            return None
        if not evaluate_predicate(rule, chapters):
            return None
    for other in others:
        if any(evaluate_predicate(rule, chains[member_id]) for member_id in other.member_ids):
            return None
    return rule


# ---------------------------------------------------------------------------
#  Чтение: пути членов, запросы групп, отпечаток
# ---------------------------------------------------------------------------

def load_member_paths(db: Session, context_id: int) -> dict[int, tuple[str, ...]]:
    """Путь каждого члена контекста от корня до его раздела; без раздела — пустой.

    Raises:
        RoutingError: цикл в цепочке разделов.
    """
    rows = db.execute(
        sa.select(ContextMember.position_item_id, PositionItem.chapter_item_id)
        .join(PositionItem, PositionItem.id == ContextMember.position_item_id)
        .where(ContextMember.context_id == context_id)
    ).all()
    by_chapter = chapter_paths(db, {chapter_id for _, chapter_id in rows if chapter_id is not None})
    return {
        member_id: (by_chapter[chapter_id] if chapter_id is not None else ())
        for member_id, chapter_id in rows
    }


def plan_for_context(
    db: Session, context_id: int, *, level: int | None = None
) -> SplitPlan | None:
    """План деления по нынешним членам контекста."""
    return plan_groups(load_member_paths(db, context_id), level=level)


def group_requests(
    db: Session, context_id: int, plan: SplitPlan, *, settings: Settings,
    paths: Mapping[int, Sequence[str]] | None = None,
) -> list[RenderedRequest] | None:
    """Запрос значений для каждой группы плана: тот же рендер, что у контекста, на путях
    только этой группы. `None` — у контекста нет материала (нет семьи или схемы)."""
    material = load_values_material(db, [context_id]).get(context_id)
    if material is None:
        return None
    paths = paths if paths is not None else load_member_paths(db, context_id)
    requests = []
    for group in plan.groups:
        counts = Counter(
            _PATH_SEPARATOR.join(paths[member_id])
            for member_id in group.member_ids
            if paths.get(member_id)
        )
        requests.append(
            render_values_request(
                replace(material, paths=top_paths(tuple(counts.items()))), settings=settings
            )
        )
    return requests


def _canonical_hash(obj: object) -> str:
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def auto_split_fingerprint(
    db: Session, context_id: int, *, group_request_hashes: Sequence[str]
) -> str:
    """Полный отпечаток входа автоделения (см. докстроку модуля). Читает без блокировок:
    писатель вызывает её под своими замками."""
    context = db.execute(
        sa.select(
            CatalogContext.work_family_id, CatalogContext.work_variant_id,
            CatalogContext.variant_split_hint, CatalogContext.auto_split_blocked_at,
            CatalogContext.archived_at,
        ).where(CatalogContext.id == context_id)
    ).one()
    members = db.execute(
        sa.select(
            ContextMember.position_item_id, ContextMember.routed_by,
            ContextMember.routing_rule_id, PositionItem.chapter_item_id,
        )
        .join(PositionItem, PositionItem.id == ContextMember.position_item_id)
        .where(ContextMember.context_id == context_id)
        .order_by(ContextMember.position_item_id)
    ).all()
    by_chapter = chapter_paths(db, {row.chapter_item_id for row in members if row.chapter_item_id})
    normalized: dict[str, str] = {}

    def _chain(chapter_id: int | None) -> list[str]:
        titles = by_chapter[chapter_id] if chapter_id is not None else ()
        return [normalized.setdefault(title, _norm(title)) for title in titles]

    schema = (
        db.execute(
            sa.select(FamilyParameterSchema.id, FamilyParameterSchema.revision).where(
                FamilyParameterSchema.family_id == context.work_family_id,
                FamilyParameterSchema.status == SchemaStatus.frozen.value,
            )
        ).one_or_none()
        if context.work_family_id is not None
        else None
    )
    rows = db.execute(
        sa.select(
            ContextParameterValue.parameter_id, ContextParameterValue.value_id,
            ContextParameterValue.source, ContextParameterValue.off_list_text,
        )
        .where(ContextParameterValue.context_id == context_id)
        .order_by(ContextParameterValue.parameter_id)
    ).all()
    return _canonical_hash(
        {
            "members": [
                [row.position_item_id, row.routed_by, row.routing_rule_id, _chain(row.chapter_item_id)]
                for row in members
            ],
            "context": [
                context.work_family_id, context.work_variant_id, context.variant_split_hint,
                _iso(context.auto_split_blocked_at), _iso(context.archived_at),
            ],
            "schema": [schema.id, schema.revision] if schema is not None else None,
            "values": [[r.parameter_id, r.value_id, r.source, r.off_list_text] for r in rows],
            "groups": list(group_request_hashes),
        }
    )


def current_fingerprint(
    db: Session, context_id: int, plan: SplitPlan, *, settings: Settings
) -> str | None:
    """Отпечаток входа плана по нынешним данным; `None` — запросы групп не собрать."""
    requests = group_requests(db, context_id, plan, settings=settings)
    if requests is None:
        return None
    return auto_split_fingerprint(
        db, context_id, group_request_hashes=[request.request_hash for request in requests]
    )


@dataclass(frozen=True)
class _Prepared:
    plan: SplitPlan
    requests: tuple[RenderedRequest, ...]
    parameters: tuple[SchemaParameterIn, ...]
    rendered: RenderedRequest
    unit_id: int | None


def _aggregate(requests: Sequence[RenderedRequest], fingerprint: str) -> RenderedRequest:
    """Один запрос задания: тело первой группы с текстами всех групп и пределом ответа по
    числу вызовов. Для проверки приватности и резерва (оба читают тело) он покрывает все
    вызовы; `request_hash` — отпечаток входа."""
    first = requests[0]
    body = copy.deepcopy(first.body)
    user_text = "\n\n".join(request.body["messages"][1]["content"] for request in requests)
    body["messages"][1]["content"] = user_text
    body["max_tokens"] = first.body["max_tokens"] * len(requests)
    return RenderedRequest(
        body=body,
        request_hash=fingerprint,
        prefix_hash=first.prefix_hash,
        candidates_hash=first.candidates_hash,
        input_hash=hashlib.sha256(user_text.encode("utf-8")).hexdigest(),
        prefix_bytes=first.prefix_bytes,
        user_bytes=sum(request.user_bytes for request in requests),
        place_dictionary_version=first.place_dictionary_version,
    )


def prepare_auto_split(
    db: Session, context_id: int, *, level: int | None, settings: Settings
) -> _Prepared | None:
    """Что нужно задания: план, запросы групп и сводный запрос. `None` — предмет неприменим:
    контекста нет или он архивирован, подсказки «к делению» нет, стоит пометка «автоделение не
    трогает», нет семьи (или она ожидается) либо схемы, среди членов есть перенесённый
    вручную (его деление не переписывает), цепочка разделов зациклена либо делить нечего."""
    context = db.execute(
        sa.select(
            CatalogContext.archived_at, CatalogContext.variant_split_hint,
            CatalogContext.auto_split_blocked_at, CatalogContext.work_family_id,
            CatalogContext.pending_family_id, ContextBucket.catalog_position_id,
        )
        .join(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
        .where(CatalogContext.id == context_id)
    ).one_or_none()
    if (
        context is None
        or context.archived_at is not None
        or context.variant_split_hint != "path_conflict"
        or context.auto_split_blocked_at is not None
        or context.work_family_id is None
        or context.pending_family_id is not None
    ):
        return None
    manual = db.execute(
        sa.select(
            sa.exists().where(
                ContextMember.context_id == context_id,
                ContextMember.routed_by == RoutedBy.manual.value,
            )
        )
    ).scalar_one()
    if manual:
        return None
    try:
        paths = load_member_paths(db, context_id)
    except RoutingError:
        return None
    plan = plan_groups(paths, level=level)
    if plan is None:
        return None
    material = load_values_material(db, [context_id]).get(context_id)
    if material is None:
        return None
    requests = group_requests(db, context_id, plan, settings=settings, paths=paths)
    assert requests is not None  # материал есть — запросы собираются
    fingerprint = auto_split_fingerprint(
        db, context_id, group_request_hashes=[request.request_hash for request in requests]
    )
    unit_id = db.execute(
        sa.select(CatalogPosition.unit_id).where(CatalogPosition.id == context.catalog_position_id)
    ).scalar_one()
    return _Prepared(
        plan=plan, requests=tuple(requests), parameters=material.parameters,
        rendered=_aggregate(requests, fingerprint), unit_id=unit_id,
    )


def render_auto_split(db: Session, job: SemanticJob, *, settings: Settings) -> RenderedRequest | None:
    """Сводный запрос задания по нынешним данным; `None` — предмет неприменим."""
    prepared = prepare_auto_split(db, job.context_id, level=job.split_level, settings=settings)
    return prepared.rendered if prepared is not None else None


# ---------------------------------------------------------------------------
#  Очередь
# ---------------------------------------------------------------------------

_LIVE_STATUSES = (
    SemanticJobStatus.pending.value,
    SemanticJobStatus.running.value,
    SemanticJobStatus.privacy_hold.value,
)
_FINISHED_STATUSES = (
    SemanticJobStatus.done.value,
    SemanticJobStatus.cancelled.value,
    SemanticJobStatus.error.value,
)


def cancel_live_auto_split_jobs(db: Session, context_id: int) -> int:
    """Открытые задания `auto_split` контекста закрываются как неприменимые; выполняющееся
    теряет захват, и его результат отбрасывается. Вызывающий держит замок контекста; задания
    берутся `FOR UPDATE` по `id`, как их берёт сверка."""
    job_ids = db.execute(
        sa.select(SemanticJob.id)
        .where(
            SemanticJob.kind == SemanticJobKind.auto_split.value,
            SemanticJob.context_id == context_id,
            SemanticJob.status.in_(_LIVE_STATUSES),
        )
        .order_by(SemanticJob.id)
        .with_for_update()
    ).scalars().all()
    if not job_ids:
        return 0
    db.execute(
        sa.update(SemanticJob)
        .where(SemanticJob.id.in_(job_ids))
        .values(
            status=SemanticJobStatus.cancelled.value,
            cancel_reason=SemanticCancelReason.not_applicable.value,
            claim_token=None,
        )
    )
    db.flush()
    return len(job_ids)


def enqueue_auto_split(
    db: Session, *, context_id: int, settings: Settings, level: int | None = None,
    revive: bool = False,
) -> int | None:
    """Ставит задание `auto_split` контекста; `id` задания либо `None` — предмет неприменим
    или задание этого отпечатка уже есть. `revive` — вернуть в `pending` законченное задание
    того же отпечатка («поправить» даёт тот же вход, что уже разбирали): иначе уникальный
    ключ не дал бы поставить его снова. Поставленный вызывающим замок контекста держится."""
    prepared = prepare_auto_split(db, context_id, level=level, settings=settings)
    if prepared is None:
        return None
    fingerprint = Fingerprint(
        SemanticJobKind.auto_split, context_id, None, None, prepared.rendered.request_hash
    )
    row = _job_row(
        fingerprint, prepared.rendered, unit_id=prepared.unit_id,
        prompt_version=VALUES_PROMPT_VERSION,
    )
    row["split_level"] = level
    created = db.execute(
        pg_insert(SemanticJob)
        .on_conflict_do_nothing(index_elements=_job_conflict_arbiter())
        .returning(SemanticJob.id),
        [row],
    ).scalars().all()
    if created:
        return created[0]
    if not revive:
        return None
    existing = db.execute(
        sa.select(SemanticJob)
        .where(
            SemanticJob.kind == SemanticJobKind.auto_split.value,
            SemanticJob.context_id == context_id,
            SemanticJob.family_id.is_(None),
            SemanticJob.schema_id.is_(None),
            SemanticJob.section_chain_id.is_(None),
            SemanticJob.request_hash == prepared.rendered.request_hash,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    existing.split_level = level
    if existing.status in _FINISHED_STATUSES:
        existing.status = SemanticJobStatus.pending.value
        existing.cancel_reason = None
        existing.claim_token = None
        existing.retry_generation = existing.retry_generation + 1
        existing.attempts_in_generation = 0
        existing.next_attempt_at = sa.func.now()
        existing.last_error_class = None
        existing.privacy_released_matches = None
    db.flush()
    return existing.id


def schedule_auto_split(db: Session, *, context_id: int, settings: Settings) -> None:
    """Вызывается `apply_values` после того, как контексту поставлен вариант и пересчитана
    подсказка: «к делению» без пометки «автоделение не трогает» — ставит задание; с пометкой
    или без подсказки — открытые задания снимает."""
    context = db.execute(
        sa.select(CatalogContext.variant_split_hint, CatalogContext.auto_split_blocked_at)
        .where(CatalogContext.id == context_id)
    ).one()
    if context.variant_split_hint == "path_conflict" and context.auto_split_blocked_at is None:
        enqueue_auto_split(db, context_id=context_id, settings=settings)
    else:
        cancel_live_auto_split_jobs(db, context_id)


# ---------------------------------------------------------------------------
#  Запись
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AutoSplitOutcome:
    """`split` — контекст поделён, `action_id` — запись журнала; `resolved` — после слияния
    групп осталась одна, значения поставлены, делить не пришлось; `not_split` — правила хоть
    одной группы нет (`reason`), контекст остался «к делению»; `input_changed` — вход
    изменился после вызова модели; `lost_claim` — задание уже не наше. Два последних ничего не
    записали."""

    status: Literal["split", "resolved", "not_split", "input_changed", "lost_claim"]
    action_id: int | None
    reason: str | None


AskGroupValues = Callable[[int, SplitGroup], Mapping[int, ValueAnswer]]


def _lock_catalog_then_bucket(db: Session, bucket_id: int) -> None:
    """Строка каталога корзины `FOR UPDATE`, затем сама корзина: порядок слияния в Review и
    `apply_values` (каталог -> корзина -> семья -> схема -> вариант -> контекст), встречный
    порядок дал бы взаимную блокировку."""
    catalog_position_id = db.execute(
        sa.select(ContextBucket.catalog_position_id).where(ContextBucket.id == bucket_id)
    ).scalar_one()
    db.execute(_lock_catalog_row_statement(catalog_position_id)).scalar_one()
    lock_buckets(db, [bucket_id], exclusive=True)


def _close_job(job: SemanticJob, status: SemanticJobStatus, cancel: SemanticCancelReason | None = None) -> None:
    job.status = status.value
    job.cancel_reason = cancel.value if cancel is not None else None
    job.claim_token = None


def _item_key(item: ValueItem) -> tuple:
    """Значение главного параметра в ответе: вид, нормализованный текст, части и их связка."""
    return (
        item.kind,
        normalize_value(item.value) if item.value is not None else None,
        tuple(sorted(normalize_value(v) for v in item.values)) if item.values else None,
        item.combine,
    )


def _from_path(item: ValueItem) -> ValueItem:
    """Значения группы выведены из путей: источник `path`; конфликт и «нет значения» —
    без источника."""
    if item.kind in ("value", "new", "several"):
        return replace(item, source="path")
    return item


def _answer_for(group_answers: Mapping[int, ValueItem], ordinals: Collection[int]) -> ValuesAnswer:
    return ValuesAnswer(items=tuple(_from_path(group_answers[o]) for o in sorted(ordinals)))


def _active_ordinals(db: Session, schema_id: int) -> set[int]:
    return set(
        db.execute(
            sa.select(FamilyParameter.ordinal).where(
                FamilyParameter.schema_id == schema_id, FamilyParameter.archived_at.is_(None)
            )
        ).scalars()
    )


def _inherit_family(db: Session, source_id: int, target_id: int) -> None:
    """Часть наследует решение исходного контекста: семью с происхождением и состояние."""
    source = db.get(CatalogContext, source_id)
    target = db.get(CatalogContext, target_id)
    target.work_family_id = source.work_family_id
    target.family_source = source.family_source
    target.family_by = source.family_by
    target.family_at = source.family_at
    target.semantic_state = source.semantic_state
    db.flush()


def _add_rule(
    db: Session, *, bucket_id: int, predicate: RulePredicate, context_id: int,
    member_ids: Sequence[int],
) -> int:
    """Ещё одно правило той же части (слитая группа с другим заголовком) и перенос под него
    своих членов."""
    ordinal = (
        db.execute(
            sa.select(sa.func.max(ContextRoutingRule.ordinal)).where(
                ContextRoutingRule.bucket_id == bucket_id
            )
        ).scalar_one()
        or 0
    )
    rule = ContextRoutingRule(
        bucket_id=bucket_id, ordinal=ordinal + 1, predicate=predicate, context_id=context_id,
        created_by=None,
    )
    db.add(rule)
    db.flush()
    db.execute(
        sa.update(ContextMember)
        .where(ContextMember.position_item_id.in_(list(member_ids)))
        .values(context_id=context_id, routed_by=RoutedBy.rule.value, routing_rule_id=rule.id)
    )
    db.flush()
    return rule.id


def _apply_group_values(
    db: Session, *, context_id: int, schema_id: int, answer: ValuesAnswer, settings: Settings
) -> None:
    paths = load_values_material(db, [context_id])[context_id].paths
    outcome = apply_values(
        db, context_id=context_id, schema_id=schema_id, answer=answer,
        paths_hash=paths_hash_of(paths), guard=None, settings=settings, queue_auto_split=False,
    )
    if not outcome.applied:
        raise ValueError(
            f"значения группы не применены к контексту {context_id}: {outcome.unapplied_reason}"
        )


def _value_ids(db: Session, context_id: int) -> list[int]:
    return list(
        db.execute(
            sa.select(ContextParameterValue.value_id)
            .where(
                ContextParameterValue.context_id == context_id,
                ContextParameterValue.value_id.is_not(None),
            )
            .order_by(ContextParameterValue.parameter_id)
        ).scalars()
    )


def auto_split_context(
    db: Session, *, context_id: int, answers: Mapping[int, Mapping[int, ValueAnswer]],
    plan: SplitPlan, guard: JobGuard, settings: Settings | None = None,
) -> AutoSplitOutcome:
    """Применяет ответы модели по группам плана одной транзакцией; коммит за вызывающим.

    `answers[i]` — ответ по группе `plan.groups[i]`: `{ordinal: ValueItem}` по всем активным
    параметрам. Замки: корзина `FOR UPDATE`, затем домен в порядке `apply_values` и строка
    задания `FOR UPDATE`. До любой записи две проверки: задание ещё наше (`lost_claim`) и
    отпечаток входа тот же, что при захвате (`input_changed`, задание закрыто отменой).

    Raises:
        AnswerSchemaError: в ответе нет значения активного параметра — до любой записи.
    """
    settings = settings if settings is not None else app_settings
    subject = db.execute(
        sa.select(
            CatalogContext.bucket_id,
            sa.func.coalesce(CatalogContext.pending_family_id, CatalogContext.work_family_id)
            .label("family_id"),
        ).where(CatalogContext.id == context_id)
    ).one()
    bucket_id = subject.bucket_id
    schema_id = db.execute(
        sa.select(FamilyParameterSchema.id).where(
            FamilyParameterSchema.family_id == subject.family_id,
            FamilyParameterSchema.status == SchemaStatus.frozen.value,
        )
    ).scalar_one_or_none()
    _lock_catalog_then_bucket(db, bucket_id)
    if schema_id is None:
        job = db.execute(_lock_job_statement(guard.job_id)).scalar_one()
        if not _owns(job, guard):
            return AutoSplitOutcome("lost_claim", None, None)
        _close_job(job, SemanticJobStatus.cancelled, SemanticCancelReason.not_applicable)
        db.flush()
        return AutoSplitOutcome("input_changed", None, "not_applicable")

    locked = _acquire_domain_locks(
        db, context_id=context_id, schema_id=schema_id, job_id=guard.job_id
    )
    job = locked.job
    if not _owns(job, guard):  # type: ignore[arg-type]
        return AutoSplitOutcome("lost_claim", None, None)
    context = locked.context
    if (
        locked.stale
        or context.pending_family_id is not None
        or current_fingerprint(db, context_id, plan, settings=settings) != guard.expected_request_hash
    ):
        _close_job(job, SemanticJobStatus.cancelled, SemanticCancelReason.input_changed)  # type: ignore[arg-type]
        db.flush()
        return AutoSplitOutcome("input_changed", None, "input_changed")
    if not _is_applicable(db, locked):
        _close_job(job, SemanticJobStatus.cancelled, SemanticCancelReason.not_applicable)  # type: ignore[arg-type]
        db.flush()
        return AutoSplitOutcome("input_changed", None, "not_applicable")

    active = _active_ordinals(db, schema_id)
    if len(answers) != len(plan.groups):
        raise ValueError(f"ответов {len(answers)}, а групп {len(plan.groups)}")
    for index in range(len(plan.groups)):
        missing = active - set(answers[index])
        if missing:
            raise AnswerSchemaError(
                "bad_ordinal",
                f"в ответе по группе «{plan.groups[index].title}» нет параметров {sorted(missing)}",
            )

    # Группы с одинаковыми значениями главных параметров сливаются (первая остаётся первой).
    main = sorted(active_main_ordinals(db, schema_id))
    clusters: dict[tuple, list[int]] = {}
    for index in range(len(plan.groups)):
        key = tuple(_item_key(answers[index][ordinal]) for ordinal in main)
        clusters.setdefault(key, []).append(index)
    merged = list(clusters.values())

    if len(merged) == 1:
        _apply_group_values(
            db, context_id=context_id, schema_id=schema_id,
            answer=_answer_for(answers[0], active), settings=settings,
        )
        _clear_hint(db, [context_id])
        _close_job(job, SemanticJobStatus.done)  # type: ignore[arg-type]
        db.flush()
        return AutoSplitOutcome("resolved", None, None)

    rules = _choose_rules(db, context_id=context_id, bucket_id=bucket_id, plan=plan, merged=merged)
    if rules is None:
        logger.info("Контекст %s не делится: для группы нет правила маршрутизации", context_id)
        _close_job(job, SemanticJobStatus.done)  # type: ignore[arg-type]
        db.flush()
        return AutoSplitOutcome("not_split", None, "no_rule")

    with db.begin_nested():
        parts: list[tuple[int, list[int]]] = [(context_id, merged[0])]
        rule_ids: list[int] = []
        for cluster in merged[1:]:
            first = plan.groups[cluster[0]]
            result = split_context(
                db, context_id=context_id, position_item_ids=list(first.member_ids),
                rule=rules[cluster[0]], actor_id=None,
            )
            rule_ids.append(result.rule_id)
            _inherit_family(db, context_id, result.new_context_id)
            for extra in cluster[1:]:
                rule_ids.append(
                    _add_rule(
                        db, bucket_id=bucket_id, predicate=rules[extra],
                        context_id=result.new_context_id,
                        member_ids=plan.groups[extra].member_ids,
                    )
                )
            parts.append((result.new_context_id, cluster))
        for part_id, cluster in parts:
            _apply_group_values(
                db, context_id=part_id, schema_id=schema_id,
                answer=_answer_for(answers[cluster[0]], active), settings=settings,
            )
        _clear_hint(db, [part_id for part_id, _ in parts])
        groups = [
            {
                "context_id": part_id,
                "title": " + ".join(
                    plan.groups[index].title for index in cluster if plan.groups[index].title
                ),
                "value_ids": _value_ids(db, part_id),
                "members": sorted(
                    member_id for index in cluster for member_id in plan.groups[index].member_ids
                ),
            }
            for part_id, cluster in parts
        ]
        action_id = record_action(
            db, kind="split", family_id=subject.family_id, context_id=context_id,
            payload={"level": plan.level, "groups": groups, "rule_ids": rule_ids},
        )
    job = db.execute(_lock_job_statement(guard.job_id)).scalar_one()
    _close_job(job, SemanticJobStatus.done)
    db.flush()
    return AutoSplitOutcome("split", action_id, None)


def _clear_hint(db: Session, context_ids: Sequence[int]) -> None:
    db.execute(
        sa.update(CatalogContext)
        .where(CatalogContext.id.in_(list(context_ids)))
        .values(variant_split_hint=None)
    )
    db.flush()
    db.expire_all()


def _choose_rules(
    db: Session, *, context_id: int, bucket_id: int, plan: SplitPlan, merged: list[list[int]]
) -> dict[int, RulePredicate] | None:
    """Правило каждой группы частей (не первой кучи); `None` — хоть у одной его нет."""
    earlier = [
        rule_predicate
        for rule_predicate in db.execute(
            sa.select(ContextRoutingRule.predicate)
            .where(ContextRoutingRule.bucket_id == bucket_id)
            .order_by(ContextRoutingRule.ordinal)
        ).scalars()
    ]
    chains = {
        member_id: chapter_context(db, member_id)
        for group in plan.groups
        for member_id in group.member_ids
    }
    rules: dict[int, RulePredicate] = {}
    for cluster in merged[1:]:
        others = [plan.groups[i] for other in merged if other is not cluster for i in other]
        for index in cluster:
            rule = choose_rule(plan.groups[index], others, chains, earlier)
            if rule is None:
                return None
            rules[index] = rule
    return rules


# ---------------------------------------------------------------------------
#  Ветка воркера
# ---------------------------------------------------------------------------

def run_auto_split_job(
    db: Session, *, claim, ask: AskGroupValues, settings: Settings | None = None,
    finish: Callable[[Session, AutoSplitOutcome], None] | None = None,
) -> AutoSplitOutcome:
    """Выполняет захваченное задание: читает план, коммитит (модель вызывается вне
    транзакции), спрашивает каждую группу через `ask`, записывает `auto_split_context`
    и коммитит. `finish` вызывается с исходом до коммита — в той же транзакции, что запись.
    Ошибки `ask` и схемные ошибки ответа идут наружу: транзакцию закрывает вызывающий."""
    settings = settings if settings is not None else app_settings
    job = db.execute(
        sa.select(SemanticJob.context_id, SemanticJob.split_level).where(
            SemanticJob.id == claim.job_id
        )
    ).one()
    guard = JobGuard(
        job_id=claim.job_id, claim_token=claim.claim_token,
        expected_request_hash=claim.rendered.request_hash,
    )
    prepared = prepare_auto_split(db, job.context_id, level=job.split_level, settings=settings)
    db.commit()
    if prepared is None:
        outcome = _close_not_applicable(db, guard)
    else:
        answers = {
            index: dict(ask(job.context_id, group)) for index, group in enumerate(prepared.plan.groups)
        }
        outcome = auto_split_context(
            db, context_id=job.context_id, answers=answers, plan=prepared.plan, guard=guard,
            settings=settings,
        )
    if finish is not None:
        finish(db, outcome)
    db.commit()
    return outcome


def close_auto_split_job(
    db: Session, guard: JobGuard,
    reason: SemanticCancelReason = SemanticCancelReason.not_applicable,
) -> AutoSplitOutcome:
    """Закрывает своё задание отменой без записи в домен; чужое (`lost_claim`) не трогает."""
    job = db.execute(_lock_job_statement(guard.job_id)).scalar_one()
    if not _owns(job, guard):
        return AutoSplitOutcome("lost_claim", None, None)
    _close_job(job, SemanticJobStatus.cancelled, reason)
    db.flush()
    return AutoSplitOutcome("input_changed", None, reason.value)


def _close_not_applicable(db: Session, guard: JobGuard) -> AutoSplitOutcome:
    return close_auto_split_job(db, guard)


# ---------------------------------------------------------------------------
#  Замок отмены и части записи
# ---------------------------------------------------------------------------

def _part_ids(payload: Mapping) -> list[int]:
    return [group["context_id"] for group in payload.get("groups", [])]


def split_locks(
    db: Session, entries: Sequence[tuple[int, datetime, Sequence[int]]]
) -> dict[int, dict]:
    """Замки отмены нескольких записей одним набором запросов: `entries` — `(id записи,
    created_at, id частей)`. В ответе только записи с замком; значение — `{event_type,
    actor_email, at}`, у замка без события (ручной член, архивная часть) — с пустыми полями.

    Замок: у любой части после записи есть событие человека из `LOCK_EVENT_TYPES`, либо член
    части с `routed_by = 'manual'`, либо часть архивирована (спека §2.5)."""
    if not entries:
        return {}
    every_part = sorted({part for _id, _at, parts in entries for part in parts})
    earliest = min(created_at for _id, created_at, _parts in entries)
    events = db.execute(
        sa.select(
            SemanticEvent.context_id, SemanticEvent.event_type, SemanticEvent.created_at,
            User.email,
        )
        .join(User, User.id == SemanticEvent.actor_id)
        .where(
            SemanticEvent.context_id.in_(every_part),
            SemanticEvent.actor_id.is_not(None),
            SemanticEvent.event_type.in_(LOCK_EVENT_TYPES),
            SemanticEvent.created_at >= earliest,
        )
        .order_by(SemanticEvent.created_at, SemanticEvent.id)
    ).all()
    manual = set(
        db.execute(
            sa.select(ContextMember.context_id)
            .where(
                ContextMember.context_id.in_(every_part),
                ContextMember.routed_by == RoutedBy.manual.value,
            )
            .distinct()
        ).scalars()
    )
    archived = set(
        db.execute(
            sa.select(CatalogContext.id).where(
                CatalogContext.id.in_(every_part), CatalogContext.archived_at.is_not(None)
            )
        ).scalars()
    )
    locks: dict[int, dict] = {}
    for action_id, created_at, parts in entries:
        found = next(
            (
                event for event in events
                if event.context_id in parts and event.created_at >= created_at
            ),
            None,
        )
        if found is not None:
            locks[action_id] = {
                "event_type": found.event_type, "actor_email": found.email,
                "at": found.created_at.isoformat(),
            }
        elif manual.intersection(parts) or archived.intersection(parts):
            locks[action_id] = {"event_type": None, "actor_email": None, "at": None}
    return locks


def _action(db: Session, action_id: int) -> CatalogAutoAction:
    row = db.get(CatalogAutoAction, action_id)
    if row is None or row.kind != "split":
        raise ValueError(f"запись {action_id} не является делением")
    return row


def split_lock(db: Session, action_id: int) -> dict | None:
    """Замок отмены записи `split` (см. `split_locks`); `None` — отмена доступна."""
    row = _action(db, action_id)
    return split_locks(db, [(row.id, row.created_at, _part_ids(row.payload))]).get(row.id)


def split_parts(db: Session, payload: Mapping) -> list[dict]:
    """Части записи с нынешними числами: сколько членов в каждой сейчас и сколько станет у
    неё после отмены (все нынешние члены уходят в исходный контекст)."""
    groups = payload.get("groups", [])
    counts = dict(
        db.execute(
            sa.select(ContextMember.context_id, sa.func.count())
            .where(ContextMember.context_id.in_(_part_ids(payload)))
            .group_by(ContextMember.context_id)
        ).all()
    )
    total = sum(counts.values())
    return [
        {
            "context_id": group["context_id"],
            "title": group["title"],
            "members_now": counts.get(group["context_id"], 0),
            "members_after": total if index == 0 else 0,
        }
        for index, group in enumerate(groups)
    ]


def can_queue_after_undo(db: Session, action_id: int) -> bool:
    """Сможет ли исходный контекст записи принять новое задание после отмены: не архивирован,
    есть семья без ожидания и действующая схема, пути членов вычислимы."""
    row = _action(db, action_id)
    context = db.execute(
        sa.select(
            CatalogContext.archived_at, CatalogContext.work_family_id,
            CatalogContext.pending_family_id,
        ).where(CatalogContext.id == row.context_id)
    ).one()
    if (
        context.archived_at is not None
        or context.work_family_id is None
        or context.pending_family_id is not None
    ):
        return False
    has_schema = db.execute(
        sa.select(
            sa.exists().where(
                FamilyParameterSchema.family_id == context.work_family_id,
                FamilyParameterSchema.status == SchemaStatus.frozen.value,
            )
        )
    ).scalar_one()
    if not has_schema:
        return False
    try:
        for part_id in _part_ids(row.payload):
            load_member_paths(db, part_id)
    except RoutingError:
        return False
    return True


def resplit_has_groups(db: Session, action_id: int, level: int) -> bool:
    """Дал бы заданный уровень группы по нынешним членам всех частей записи."""
    row = _action(db, action_id)
    paths: dict[int, tuple[str, ...]] = {}
    for part_id in _part_ids(row.payload):
        paths.update(load_member_paths(db, part_id))
    return plan_groups(paths, level=level) is not None


# ---------------------------------------------------------------------------
#  Отмена
# ---------------------------------------------------------------------------

class SplitUndoLocked(Exception):
    """Отмена недоступна: части правили вручную после деления."""

    def __init__(self, locked_by: dict) -> None:
        super().__init__("части правили вручную после деления")
        self.locked_by = locked_by


class SplitUndoNotApplicable(Exception):
    """Запись устарела: семьи или схемы контекста уже нет."""


def undo_split(
    db: Session, action_id: int, *, user_id: int, block: bool = True,
    settings: Settings | None = None,
) -> None:
    """Отменяет автоделение: нынешние члены всех частей сливаются в исходный контекст, снимаются
    только правила записи, контексту возвращается «конфликт путей» (значения главных
    параметров `path_conflict`, вариант по правилам вариантов, подсказка «к делению»), открытые
    задания автоделения снимаются. `block` ставит пометку «автоделение не трогает» от имени
    `user_id`; «поправить» её не ставит. Запись помечает отменённой вызывающий.

    Строка каталога и корзина берутся `FOR UPDATE` раньше проверки замка: ручной перенос не проскочит между
    проверкой и слиянием. Коммит за вызывающим; отказ откатывает вызывающий.

    Raises:
        SplitUndoLocked: замок отмены (`split_lock`).
        SplitUndoNotApplicable: у контекста нет семьи или действующей схемы.
    """
    settings = settings if settings is not None else app_settings
    row = _action(db, action_id)
    original_id = row.context_id
    bucket_id = db.execute(
        sa.select(CatalogContext.bucket_id).where(CatalogContext.id == original_id)
    ).scalar_one()
    _lock_catalog_then_bucket(db, bucket_id)
    locked_by = split_lock(db, action_id)
    if locked_by is not None:
        raise SplitUndoLocked(locked_by)

    family_id = db.execute(
        sa.select(CatalogContext.work_family_id).where(CatalogContext.id == original_id)
    ).scalar_one()
    schema_id = (
        db.execute(
            sa.select(FamilyParameterSchema.id).where(
                FamilyParameterSchema.family_id == family_id,
                FamilyParameterSchema.status == SchemaStatus.frozen.value,
            )
        ).scalar_one_or_none()
        if family_id is not None
        else None
    )
    if schema_id is None:
        raise SplitUndoNotApplicable(f"у контекста {original_id} нет семьи или действующей схемы")

    rule_ids = list(row.payload.get("rule_ids", []))
    for part_id in _part_ids(row.payload)[1:]:
        merge_contexts(
            db, source_context_id=part_id, target_context_id=original_id, actor_id=user_id
        )
    # Члены, шедшие по правилам записи, остаются без правила: маршрут «по умолчанию».
    db.execute(
        sa.update(ContextMember)
        .where(ContextMember.routing_rule_id.in_(rule_ids))
        .values(routed_by=RoutedBy.default.value, routing_rule_id=None)
    )
    db.execute(sa.delete(ContextRoutingRule).where(ContextRoutingRule.id.in_(rule_ids)))
    db.flush()

    if block:
        db.execute(
            sa.update(CatalogContext)
            .where(CatalogContext.id == original_id)
            .values(auto_split_blocked_at=datetime.now(UTC), auto_split_blocked_by=user_id)
        )
        db.flush()
    cancel_live_auto_split_jobs(db, original_id)

    main = sorted(active_main_ordinals(db, schema_id))
    paths = load_values_material(db, [original_id])[original_id].paths
    outcome = apply_values(
        db, context_id=original_id, schema_id=schema_id,
        answer=ValuesAnswer(items=tuple(ValueItem(o, "conflict", None, None) for o in main)),
        requested_ordinals=tuple(main), paths_hash=paths_hash_of(paths), guard=None,
        settings=settings, queue_auto_split=False,
    )
    if not outcome.applied:
        raise SplitUndoNotApplicable(
            f"значения контекста {original_id} не применены: {outcome.unapplied_reason}"
        )
    db.execute(
        sa.update(CatalogContext)
        .where(CatalogContext.id == original_id, CatalogContext.work_variant_id.is_not(None))
        .values(variant_split_hint="path_conflict")
    )
    db.flush()
    db.expire_all()
