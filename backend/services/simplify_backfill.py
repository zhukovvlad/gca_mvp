"""Первый прогон упрощения каталога на накопленных данных (спека упрощения каталога §2.12).

Шаг 1 — склейки в списках, без модели: действующее простое значение открытого главного
параметра, которое `split_candidate` режет на две части и больше, а каждая часть — действующее
простое значение того же параметра (по точной норме или по лемме, как фильтр 1 §2.4), становится
составным на месте: тот же `id`, `composite_kind`, `composite_key`, строки `family_value_parts`;
текст и `value_norm` — по инварианту §2.3 (части в порядке id через « + » или « или »), если норма
свободна, иначе прежний текст остаётся. Контексты и варианты не меняются. Состав, совпавший с
уже существующим или с только что получившим составное значение, сливается в него; части,
схлопнувшиеся в одну, — в эту часть; состав под вето (§2.6) и склейка с частью не из списка
остаются целыми. Запись «Журнала ИИ» `composite` — на каждое превращение на месте.

Шаг 2 — контексты «к делению» (`variant_split_hint = 'path_conflict'`, не архивные, без пометки
«автоделение не трогает», с действующей версией схемы семьи): строки, на которых путь разошёлся,
помечаются «уточняется», а сверка заданий значений ставит (или возвращает из выполненных)
`context_values` тем же путём, что и правка схемы: один отпечаток запроса, один резерв, одна
проверка приватности захватом. Ответ `conflict` сам ставит автоделение (`apply_values`):
здесь автоделение не ставится.

Обе половины считают план читающими запросами; запись идёт теми же действиями, что план, поэтому
`apply=False` печатает то же, что сделает `apply=True`. Функции коммитят не сами: транзакцией
владеет вызывающий.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from config import settings as app_settings
from models import (
    CatalogContext,
    ContextParameterValue,
    FamilyParameter,
    FamilyParameterSchema,
    FamilyParameterValue,
    FamilyValuePart,
    ParameterRole,
    SchemaStatus,
    SemanticJob,
    SemanticJobKind,
    SemanticJobStatus,
    ValueSource,
    WorkFamily,
)
from services.ai_journal import record_action
from services.composite_values import COMPOSITE_JOIN, _active_composite_id, composite_key
from services.human_decisions import match_veto
from services.releasable import _UNFINISHED, load_releasable_material, render_releasable_material
from services.schema_edit import _after_value_list_change, _require_editable
from services.semantic_cost import reserve_for, tariffs_from
from services.semantic_reconcile import (
    NO_CAP,
    _estimate_entries,
    deferred_reconcile,
    load_value_rows,
    preview_values_plan,
    reconcile_context_values,
)
from services.value_filters import _active_simple, _known_part, split_candidate
from services.variant_request import VALUES_PROMPT_VERSION, load_values_material
from services.work_families import WorkFamilyError
from services.work_variants import (
    _lock_contexts_statement,
    _lock_values_statement,
    bump_revision,
    mark_rows_for_refresh,
    merge_parameter_values,
    normalize_value,
)

# ---------------------------------------------------------------------------
#  Шаг 1: склейки в списках
# ---------------------------------------------------------------------------


@dataclass
class Step1Report:
    """Итог шага 1 — `id` значений по исходам. `text_kept` — подмножество `converted`:
    норма составного текста занята другим значением, остался прежний текст."""

    converted: list[int] = field(default_factory=list)
    merged_into_composite: list[int] = field(default_factory=list)
    collapsed: list[int] = field(default_factory=list)
    under_veto: list[int] = field(default_factory=list)
    kept_whole: list[int] = field(default_factory=list)
    text_kept: list[int] = field(default_factory=list)
    skipped_families: list[int] = field(default_factory=list)
    jobs_queued: int = 0
    reserve_usd: Decimal = Decimal("0")

    def extend(self, other: Step1Report) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(other, name)
            if isinstance(value, list):
                getattr(self, name).extend(value)
            else:
                setattr(self, name, getattr(self, name) + value)


@dataclass(frozen=True)
class _Convert:
    value_id: int
    kind: str
    part_ids: tuple[int, ...]
    text: str
    norm: str
    keep_text: bool


@dataclass(frozen=True)
class _MergeInto:
    value_id: int
    target_id: int
    composite: bool


def families_for_backfill(db: Session, family_id: int | None) -> list[int]:
    """Семьи с действующей версией схемы по возрастанию `id`; `family_id` ограничивает
    прогон одной семьёй."""
    stmt = (
        sa.select(WorkFamily.id)
        .where(
            sa.exists().where(
                FamilyParameterSchema.family_id == WorkFamily.id,
                FamilyParameterSchema.status == SchemaStatus.frozen.value,
            )
        )
        .order_by(WorkFamily.id)
    )
    if family_id is not None:
        stmt = stmt.where(WorkFamily.id == family_id)
    return list(db.execute(stmt).scalars())


def _open_main_parameters(db: Session, schema_id: int) -> list[int]:
    return list(
        db.execute(
            sa.select(FamilyParameter.id)
            .where(
                FamilyParameter.schema_id == schema_id,
                FamilyParameter.role == ParameterRole.main.value,
                FamilyParameter.archived_at.is_(None),
                FamilyParameter.value_kind == "exact",
            )
            .order_by(FamilyParameter.ordinal)
        ).scalars()
    )


def _plan_parameter(
    db: Session, parameter_id: int, report: Step1Report
) -> list[_Convert | _MergeInto]:
    """Действия шага 1 по одному параметру; исходы без действия кладутся в `report`."""
    simples = _active_simple(db, parameter_id)
    splits = {row.id: split_candidate(row.value) for row in simples}
    targets = [row for row in simples if splits[row.id] is None]
    texts = {row.id: row.value for row in simples}
    planned: dict[str, int] = {}
    actions: list[_Convert | _MergeInto] = []
    for row in simples:
        split = splits[row.id]
        if split is None:
            continue
        known = [_known_part(db, parameter_id, part, targets) for part in split.parts]
        if any(part_id is None for part_id in known):
            report.kept_whole.append(row.id)
            continue
        parts = tuple(sorted(set(known)))
        if len(parts) == 1:
            actions.append(_MergeInto(row.id, parts[0], composite=False))
            continue
        if match_veto(db, parameter_id=parameter_id, kind=split.kind, part_ids=frozenset(parts)):
            report.under_veto.append(row.id)
            continue
        key = composite_key(split.kind, parts)
        target = _active_composite_id(db, parameter_id, key) or planned.get(key)
        if target is not None:
            actions.append(_MergeInto(row.id, target, composite=True))
            continue
        text = COMPOSITE_JOIN[split.kind].join(texts[part_id] for part_id in parts)
        norm = normalize_value(text)
        taken = db.execute(
            sa.select(FamilyParameterValue.id).where(
                FamilyParameterValue.parameter_id == parameter_id,
                FamilyParameterValue.value_norm == norm,
                FamilyParameterValue.id != row.id,
            )
        ).first()
        actions.append(
            _Convert(row.id, split.kind, parts, text, norm, keep_text=taken is not None)
        )
        planned[key] = row.id
    return actions


def _contexts_of_value(db: Session, parameter_id: int, value_id: int) -> list[int]:
    return list(
        db.execute(
            sa.select(ContextParameterValue.context_id)
            .where(
                ContextParameterValue.parameter_id == parameter_id,
                ContextParameterValue.value_id == value_id,
            )
            .order_by(ContextParameterValue.context_id)
        ).scalars()
    )


def _apply_convert(db: Session, *, family_id: int, parameter_id: int, action: _Convert) -> None:
    value = db.execute(_lock_values_statement([action.value_id])).scalar_one()
    original = value.value
    value.composite_kind = action.kind
    value.composite_key = composite_key(action.kind, action.part_ids)
    if not action.keep_text:
        value.value = action.text
        value.value_norm = action.norm
    db.flush()
    db.add_all(
        FamilyValuePart(value_id=value.id, part_value_id=part_id, parameter_id=parameter_id)
        for part_id in action.part_ids
    )
    db.flush()
    record_action(
        db, kind="composite", family_id=family_id, parameter_id=parameter_id, value_id=value.id,
        payload={
            "text": original, "kind": action.kind, "part_ids": list(action.part_ids),
            "new_part_ids": [], "context_ids": _contexts_of_value(db, parameter_id, value.id),
        },
    )


def backfill_family_values(db: Session, family_id: int, *, apply: bool) -> Step1Report:
    """Шаг 1 по одной семье. Семья не активна, у неё идёт пересборка или нет действующей
    версии — в `skipped_families`. Без `apply` ничего не пишется и замков не берётся."""
    report = Step1Report()
    try:
        schema_id = _require_editable(db, family_id, lock=apply)
    except WorkFamilyError:
        report.skipped_families.append(family_id)
        return report
    plans = [
        (parameter_id, _plan_parameter(db, parameter_id, report))
        for parameter_id in _open_main_parameters(db, schema_id)
    ]
    for _parameter_id, actions in plans:
        for action in actions:
            if isinstance(action, _Convert):
                report.converted.append(action.value_id)
                if action.keep_text:
                    report.text_kept.append(action.value_id)
            elif action.composite:
                report.merged_into_composite.append(action.value_id)
            else:
                report.collapsed.append(action.value_id)
    if not report.converted + report.merged_into_composite + report.collapsed:
        return report

    # Список значений меняется, и `refresh_inputs` ставит платное задание «можно отпустить»
    # (если есть неразмеченный главный параметр): оценка по рендеру до правки.
    job_reserve = _releasable_reserve(db, family_id, schema_id)
    if not apply:
        if job_reserve is not None:
            report.jobs_queued, report.reserve_usd = 1, job_reserve
        return report

    unfinished_before = _unfinished_releasable(db, schema_id)
    with deferred_reconcile(db):
        for parameter_id, actions in plans:
            for action in actions:
                if isinstance(action, _Convert):
                    _apply_convert(db, family_id=family_id, parameter_id=parameter_id, action=action)
                else:
                    merge_parameter_values(
                        db, parameter_id=parameter_id, source_value_id=action.value_id,
                        target_value_id=action.target_id, actor_id=None, cascade=action.composite,
                        keep_origin=True,
                    )
        if report.converted:
            bump_revision(db, schema_id)
            _after_value_list_change(db, family_id, schema_id)
    queued = len(_unfinished_releasable(db, schema_id) - unfinished_before)
    if queued and job_reserve is not None:
        report.jobs_queued, report.reserve_usd = queued, job_reserve * queued
    return report


def _releasable_reserve(db: Session, family_id: int, schema_id: int) -> Decimal | None:
    """Резерв одного задания «можно отпустить» версии по её рендеру сейчас; `None` — задание
    версии не нужно (нет материала или нет неразмеченного главного параметра)."""
    material = load_releasable_material(db, family_id, schema_id)
    if material is None or not material.has_unmarked:
        return None
    rendered = render_releasable_material(material, settings=app_settings)
    return reserve_for(
        db, rendered, tariffs_from(app_settings, SemanticJobKind.releasable_params),
        rendered.body["max_tokens"],
    )


def _unfinished_releasable(db: Session, schema_id: int) -> set[int]:
    return set(
        db.execute(
            sa.select(SemanticJob.id).where(
                SemanticJob.kind == SemanticJobKind.releasable_params.value,
                SemanticJob.schema_id == schema_id,
                SemanticJob.status.in_(_UNFINISHED),
            )
        ).scalars()
    )


# ---------------------------------------------------------------------------
#  Шаг 2: контексты «к делению»
# ---------------------------------------------------------------------------


@dataclass
class Step2Report:
    """Итог шага 2: `candidates` — контексты «к делению» с материалом значений, `queued` —
    контексты, которым ставится или возвращается задание (после `limit`), `free` — из них
    без вызова модели, `reserve_usd` и `cached_usd` — оценка по тарифам `context_values`."""

    candidates: int = 0
    already_asked: int = 0
    queued: list[int] = field(default_factory=list)
    jobs_created: int = 0
    jobs_revived: int = 0
    free: int = 0
    reserve_usd: Decimal = Decimal("0")
    cached_usd: Decimal = Decimal("0")


def _candidate_contexts(db: Session, family_id: int | None) -> list[int]:
    stmt = (
        sa.select(CatalogContext.id)
        .where(
            CatalogContext.variant_split_hint == "path_conflict",
            CatalogContext.archived_at.is_(None),
            CatalogContext.auto_split_blocked_at.is_(None),
        )
        .order_by(CatalogContext.id)
    )
    if family_id is not None:
        stmt = stmt.where(
            sa.func.coalesce(CatalogContext.pending_family_id, CatalogContext.work_family_id)
            == family_id
        )
    return list(db.execute(stmt).scalars())


def _conflict_rows(
    db: Session, context_ids: Sequence[int], schema_of: dict[int, int]
) -> dict[int, dict[int, int]]:
    """`{context_id: {ordinal: parameter_id}}` — строки действующей версии, на которых путь
    разошёлся, по активным параметрам."""
    result: dict[int, dict[int, int]] = {}
    for row in db.execute(
        sa.select(
            ContextParameterValue.context_id, ContextParameterValue.schema_id,
            ContextParameterValue.parameter_id, FamilyParameter.ordinal,
        )
        .join(FamilyParameter, FamilyParameter.id == ContextParameterValue.parameter_id)
        .where(
            ContextParameterValue.context_id.in_(list(context_ids)),
            ContextParameterValue.source == ValueSource.path_conflict.value,
            FamilyParameter.archived_at.is_(None),
        )
    ).all():
        if schema_of.get(row.context_id) == row.schema_id:
            result.setdefault(row.context_id, {})[row.ordinal] = row.parameter_id
    return result


def _answered_with_current_prompt(db: Session, material: dict) -> set[int]:
    """Контексты, чьё последнее выполненное задание значений по действующей версии схемы
    отрисовано текущим промптом значений: они уже отвечены, спрашивать снова незачем."""
    if not material:
        return set()
    latest: dict[int, str] = {}
    for row in db.execute(
        sa.select(SemanticJob.context_id, SemanticJob.schema_id, SemanticJob.prompt_version)
        .where(
            SemanticJob.kind == SemanticJobKind.context_values.value,
            SemanticJob.status == SemanticJobStatus.done.value,
            SemanticJob.context_id.in_(list(material)),
        )
        .order_by(SemanticJob.updated_at, SemanticJob.id)
    ).all():
        if row.schema_id == material[row.context_id].schema_id:
            latest[row.context_id] = row.prompt_version
    return {cid for cid, version in latest.items() if version == str(VALUES_PROMPT_VERSION)}


def backfill_path_conflicts(
    db: Session, *, family_id: int | None, limit: int | None, apply: bool
) -> Step2Report:
    """Шаг 2: перезапрос значений у контекстов «к делению». `limit` ограничивает число
    контекстов, которым ставится задание, — по возрастанию `id`; контекст, у которого
    задание текущего отпечатка уже ждёт или идёт, в набор не входит. Без `apply` — только
    план и оценка."""
    report = Step2Report()
    material = load_values_material(db, _candidate_contexts(db, family_id))
    report.candidates = len(material)
    answered = _answered_with_current_prompt(db, material)
    report.already_asked = len(answered)
    material = {cid: values for cid, values in material.items() if cid not in answered}
    if not material:
        return report
    schema_of = {context_id: values.schema_id for context_id, values in material.items()}
    conflicts = _conflict_rows(db, list(material), schema_of)
    known_rows = load_value_rows(db, material)

    by_schema: dict[int, list[int]] = {}
    for context_id in sorted(conflicts):
        by_schema.setdefault(schema_of[context_id], []).append(context_id)
    entries = []
    for context_ids in by_schema.values():
        rows = {
            context_id: {
                **known_rows.get(context_id, {}), **{o: True for o in conflicts[context_id]},
            }
            for context_id in context_ids
        }
        plan = preview_values_plan(
            db, context_ids, parameters=material[context_ids[0]].parameters, rows=rows
        )
        entries.extend(plan.entries)
    entries.sort(key=lambda entry: entry[0].context_id)
    if limit is not None:
        entries = entries[:limit]

    report.queued = [entry[0].context_id for entry in entries]
    report.free = sum(1 for entry in entries if entry[3])
    report.reserve_usd, report.cached_usd = _estimate_entries(
        db,
        [(fingerprint, rendered) for fingerprint, rendered, _ordinals, _free in entries],
        {fingerprint for fingerprint, _rendered, _ordinals, free in entries if free},
    )
    if not apply or not report.queued:
        return report

    db.execute(_lock_contexts_statement(report.queued)).all()
    contexts_of_parameter: dict[int, list[int]] = {}
    for context_id in report.queued:
        for parameter_id in conflicts[context_id].values():
            contexts_of_parameter.setdefault(parameter_id, []).append(context_id)
    for parameter_id, context_ids in contexts_of_parameter.items():
        mark_rows_for_refresh(db, context_ids, parameter_id)
    done = reconcile_context_values(db, report.queued, cap=NO_CAP, source="operation")
    report.jobs_created, report.jobs_revived = done.created, done.revived
    return report
