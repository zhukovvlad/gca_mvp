"""Ядро варианта работы: значения закрытого списка, варианты, заморозка схемы и
обработчик результата `context_values` с промоушеном строки каталога (спека
`2026-10-02-catalog-variants-design.md` §2.2, §2.4, §2.6, §2.10).

Порядок блокировок один на всю фичу (спека §2.6): строка каталога -> семьи по
возрастанию `id` -> версия схемы -> прежний вариант -> контекст -> задание
очереди. Задание берётся последним, как у сверки (`_lock_jobs` под доменными
блокировками): обратный порядок с ней замыкается в цикл. Все блокировки
`apply_values` и `freeze_schema` берёт один внутренний шаг на функцию
(`_acquire_domain_locks`, `_acquire_schema_locks`), до него идёт только чтение
без блокировок: что именно блокировать. Каждый оператор блокировки собран
отдельной функцией `*_statement`, чтобы тест компилировал SQL и проверял режим
(`FOR UPDATE` против `FOR SHARE`), а не намерение вызова.

Вердикт публикации (захват ещё наш, предмет применим, отпечаток текущий)
выносится ПОД блокировками: иначе между проверкой и записью другая транзакция
сменила бы ожидание или схему, и устаревший ответ применился бы. `lost_claim`
не трогает ничего: ни домен, ни задание нового владельца. `stale_fingerprint` и
`not_applicable` при своём захвате закрывают задание отменой; домен остаётся
как был.

Функции ничего не коммитят: транзакцией владеет вызывающий. Любое исключение
после взятия блокировок оставляет записи вызывающему на откат.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from config import Settings
from models import (
    CatalogContext,
    CatalogKind,
    CatalogPosition,
    ContextBucket,
    ContextMember,
    ContextParameterValue,
    FamilyParameter,
    FamilyParameterSchema,
    FamilyParameterValue,
    FamilyStatus,
    FamilySuggestion,
    SchemaStatus,
    SemanticCancelReason,
    SemanticJob,
    SemanticJobKind,
    SemanticJobStatus,
    SemanticKind,
    SemanticState,
    ValueOrigin,
    ValueSource,
    VariantStatus,
    WorkFamily,
    WorkVariant,
    WorkVariantValue,
)
from services.semantic_answer import AnswerSchemaError
from services.semantic_events import record_event
from services.semantic_privacy import _replace_quotes_with_space
from services.variant_answer import SchemaAnswer, ValuesAnswer
from services.variant_request import SubjectNotRenderable, paths_hash_of, render_request_for
from services.work_families import _lock_families

UnappliedReason = Literal["lost_claim", "stale_fingerprint", "not_applicable"]

_WHITESPACE_RE = re.compile(r"\s+")

#: Виды строки каталога, к которым применяются значения: `HEADER` и `TRASH`
#: не работа, `LOT_HEADER` — заголовок лота.
_APPLICABLE_CATALOG_KINDS = (CatalogKind.TO_REVIEW.value, CatalogKind.POSITION.value)


def _now() -> datetime:
    return datetime.now(UTC)


def _schema_error(code: str, detail: str) -> AnswerSchemaError:
    code_any: Any = code
    return AnswerSchemaError(code_any, detail)


# ---------------------------------------------------------------------------
#  Значения и варианты
# ---------------------------------------------------------------------------

def normalize_value(text: str) -> str:
    """Каноническая форма значения списка (спека §2.4): кавычки заменены
    пробелом (как в словаре приватности), нижний регистр, `ё` -> `е`,
    схлопнутые пробелы, без крайних пробелов."""
    folded = _replace_quotes_with_space(text).casefold().replace("ё", "е")
    return _WHITESPACE_RE.sub(" ", folded).strip()


def values_key_of(value_ids_by_ordinal: Mapping[int, int | None]) -> str:
    """Ключ набора значений: `"1=17|2=?|3=42"` — `ordinal` по возрастанию,
    `?` вместо пустого значения; для схемы без параметров — пустая строка."""
    return "|".join(
        f"{ordinal}={'?' if value_id is None else value_id}"
        for ordinal, value_id in sorted(value_ids_by_ordinal.items())
    )


def canonical_value_id(db: Session, value_id: int) -> int:
    """Идёт по цепочке `merged_into_id` до значения, которое никуда не слито."""
    seen = {value_id}
    current = value_id
    while True:
        target = db.execute(
            sa.select(FamilyParameterValue.merged_into_id).where(
                FamilyParameterValue.id == current
            )
        ).scalar_one()
        if target is None:
            return current
        if target in seen:
            raise ValueError(f"цепочка слияния значения {value_id} замкнута")
        seen.add(target)
        current = target


def get_or_create_value(
    db: Session, *, parameter_id: int, text: str, origin: ValueOrigin
) -> tuple[int, bool]:
    """Значение параметра по тексту: `(канонический value_id, создано ли)`.

    Вставка `ON CONFLICT (parameter_id, value_norm) DO NOTHING RETURNING`
    атомарна при параллельных обработчиках одной семьи; при конфликте читается
    существующая строка, и результат всегда разрешается по цепочке слияния
    до канонического значения: слитый синоним, названный заново, не
    воскресает.

    Raises:
        AnswerSchemaError: текст пуст после нормализации (`value_norm <> ''`
            держит и схема, но отказ обязан быть схемной ошибкой, а не сырой
            ошибкой базы).
    """
    norm = normalize_value(text)
    if not norm:
        raise _schema_error("empty_value", "значение пусто после нормализации")
    inserted = db.execute(
        pg_insert(FamilyParameterValue)
        .values(
            parameter_id=parameter_id, value=text.strip(), value_norm=norm, origin=origin.value
        )
        .on_conflict_do_nothing(index_elements=["parameter_id", "value_norm"])
        .returning(FamilyParameterValue.id)
    ).scalar_one_or_none()
    if inserted is not None:
        return inserted, True
    existing = db.execute(
        sa.select(FamilyParameterValue.id).where(
            FamilyParameterValue.parameter_id == parameter_id,
            FamilyParameterValue.value_norm == norm,
        )
    ).scalar_one()
    return canonical_value_id(db, existing), False


def _lock_variant_statement(variant_id: int):
    return (
        sa.select(WorkVariant)
        .where(WorkVariant.id == variant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _lock_variant_by_key_statement(schema_id: int, values_key: str):
    return (
        sa.select(WorkVariant)
        .where(WorkVariant.schema_id == schema_id, WorkVariant.values_key == values_key)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _variant_count_statement(variant_id: int):
    return (
        sa.select(sa.func.count())
        .select_from(CatalogContext)
        .where(CatalogContext.work_variant_id == variant_id)
    )


@dataclass(frozen=True)
class _VariantResult:
    variant: WorkVariant
    created: bool
    reactivated: bool


def _parameter_ids_by_ordinal(db: Session, schema_id: int) -> dict[int, int]:
    return {
        row.ordinal: row.id
        for row in db.execute(
            sa.select(FamilyParameter.id, FamilyParameter.ordinal).where(
                FamilyParameter.schema_id == schema_id
            )
        ).all()
    }


def _resolve_variant(
    db: Session, *, family_id: int, schema_id: int,
    value_ids_by_ordinal: Mapping[int, int | None],
) -> _VariantResult:
    parameter_ids = _parameter_ids_by_ordinal(db, schema_id)
    if set(parameter_ids) != set(value_ids_by_ordinal):
        raise ValueError(
            f"ordinal набора значений {sorted(value_ids_by_ordinal)} не совпадают с "
            f"параметрами схемы {sorted(parameter_ids)}"
        )
    key = values_key_of(value_ids_by_ordinal)
    inserted = db.execute(
        pg_insert(WorkVariant)
        .values(
            family_id=family_id, schema_id=schema_id, values_key=key,
            status=VariantStatus.active.value,
        )
        .on_conflict_do_nothing(index_elements=["schema_id", "values_key"])
        .returning(WorkVariant.id)
    ).scalar_one_or_none()
    created = inserted is not None
    if created:
        rows = [
            {
                "variant_id": inserted, "schema_id": schema_id,
                "parameter_id": parameter_ids[ordinal], "value_id": value_id,
            }
            for ordinal, value_id in value_ids_by_ordinal.items()
        ]
        if rows:
            db.execute(sa.insert(WorkVariantValue), rows)
    # Блокировка строки варианта сериализует «последний ушёл» и «новый пришёл»
    # (`archive_variant_if_empty`): без неё чтение статуса здесь и подсчёт
    # контекстов там не видели бы друг друга.
    variant = db.execute(_lock_variant_by_key_statement(schema_id, key)).scalar_one()
    seen = {variant.id}
    while variant.merged_into_id is not None:
        target_id = variant.merged_into_id
        if target_id in seen:
            raise ValueError(f"цепочка слияния варианта {variant.id} замкнута")
        seen.add(target_id)
        variant = db.execute(_lock_variant_statement(target_id)).scalar_one()
    reactivated = False
    if variant.status == VariantStatus.archived.value:
        variant.status = VariantStatus.active.value
        variant.archived_at = None
        db.flush()
        reactivated = True
    return _VariantResult(variant=variant, created=created, reactivated=reactivated)


def get_or_create_variant(
    db: Session, *, family_id: int, schema_id: int,
    value_ids_by_ordinal: Mapping[int, int | None],
) -> tuple[WorkVariant, bool]:
    """Вариант с таким набором значений в версии схемы: `(вариант, создан ли)`.

    Одинаковый набор — один вариант (`UNIQUE (schema_id, values_key)`, и
    архивный тоже). Вставка `ON CONFLICT DO NOTHING`, затем строка берётся
    `FOR UPDATE`: архивный неслитый вариант возвращается в `active`, слитый
    заменяется целью слияния (к цели то же правило). Вариант возвращается
    заблокированным до конца транзакции.

    Raises:
        ValueError: `ordinal` набора не совпадают с параметрами версии.
    """
    result = _resolve_variant(
        db, family_id=family_id, schema_id=schema_id, value_ids_by_ordinal=value_ids_by_ordinal
    )
    return result.variant, result.created


def archive_variant_if_empty(db: Session, variant_id: int) -> bool:
    """Архивирует вариант, на котором не осталось контекстов; `True`, если
    заархивирован этим вызовом.

    Вариант берётся `FOR UPDATE`, и подсчёт идёт под этой блокировкой: она
    сериализует два потока, уводящих последние контексты, и поток, который
    приводит новый контекст на тот же вариант (`get_or_create_variant` держит
    ту же строку). Без неё каждый поток видел бы чужую незафиксированную ссылку
    и оставил бы активный вариант без контекстов либо архивировал бы
    вариант, на который уже пришёл контекст."""
    variant = db.execute(_lock_variant_statement(variant_id)).scalar_one()
    if variant.status == VariantStatus.archived.value:
        return False
    if db.execute(_variant_count_statement(variant_id)).scalar_one() > 0:
        return False
    variant.status = VariantStatus.archived.value
    variant.archived_at = _now()
    db.flush()
    return True


# ---------------------------------------------------------------------------
#  Охрана задания и исходы
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class JobGuard:
    """Что известно писателю о задании, чей результат он применяет: сам
    захват и отпечаток запроса, на который пришёл ответ."""

    job_id: int
    claim_token: UUID
    expected_request_hash: str


@dataclass(frozen=True)
class FreezeOutcome:
    applied: bool
    unapplied_reason: UnappliedReason | None
    schema_id: int


@dataclass(frozen=True)
class ApplyValuesOutcome:
    applied: bool
    unapplied_reason: UnappliedReason | None
    variant_id: int | None
    previous_variant_id: int | None
    promoted: bool
    family_switched: bool
    values_added: tuple[int, ...]
    previous_archived: bool


def _unapplied_values(reason: UnappliedReason) -> ApplyValuesOutcome:
    return ApplyValuesOutcome(
        applied=False, unapplied_reason=reason, variant_id=None, previous_variant_id=None,
        promoted=False, family_switched=False, values_added=(), previous_archived=False,
    )


def _lock_job_statement(job_id: int):
    return (
        sa.select(SemanticJob)
        .where(SemanticJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _close_job_unapplied(job: SemanticJob, reason: UnappliedReason) -> None:
    """Своё задание, результат которого не применён: закрыто отменой. Чужое
    (`lost_claim`) не трогается."""
    if reason == "stale_fingerprint":
        cancel = SemanticCancelReason.input_changed
    else:
        cancel = SemanticCancelReason.not_applicable
    job.status = SemanticJobStatus.cancelled.value
    job.cancel_reason = cancel.value
    job.claim_token = None


def _owns(job: SemanticJob, guard: JobGuard) -> bool:
    # Токен непуст ровно при `running` (CHECK пары статуса и токена), поэтому
    # совпавший токен уже означает статус `running`.
    return job.claim_token == guard.claim_token


# ---------------------------------------------------------------------------
#  Заморозка схемы
# ---------------------------------------------------------------------------

def _lock_schema_statement(schema_id: int, *, share: bool):
    stmt = (
        sa.select(FamilyParameterSchema)
        .where(FamilyParameterSchema.id == schema_id)
        .execution_options(populate_existing=True)
    )
    return stmt.with_for_update(read=True) if share else stmt.with_for_update()


def _lock_current_schema_statement(family_id: int):
    return (
        sa.select(FamilyParameterSchema)
        .where(
            FamilyParameterSchema.family_id == family_id,
            FamilyParameterSchema.status == SchemaStatus.frozen.value,
        )
        .with_for_update()
    )


def _check_schema_answer(answer: SchemaAnswer) -> None:
    for parameter in answer.parameters:
        if not normalize_value(parameter.name):
            raise _schema_error(
                "empty_value", f"имя параметра {parameter.ordinal} пусто после нормализации"
            )
        for value in parameter.values:
            if not normalize_value(value):
                raise _schema_error(
                    "empty_value",
                    f"значение параметра {parameter.ordinal} пусто после нормализации",
                )


def _write_frozen_schema(
    db: Session, *, schema: FamilyParameterSchema, current: FamilyParameterSchema | None,
    answer: SchemaAnswer, job_id: int | None,
) -> None:
    """Общая часть заморозки: параметры и значения с `origin='schema'`, версия
    `building` -> `frozen`, прежняя текущая -> `superseded` (заморозка
    сохраняется), событие. Вызывающий уже взял блокировки и вынес вердикт."""
    now = _now()
    described: list[dict[str, object]] = []
    for parameter in answer.parameters:
        row = FamilyParameter(
            schema_id=schema.id, ordinal=parameter.ordinal, name=parameter.name.strip(),
            name_norm=normalize_value(parameter.name),
        )
        db.add(row)
        db.flush()
        seen: set[str] = set()
        for text in parameter.values:
            norm = normalize_value(text)
            if norm in seen:
                continue  # дубль в ответе схлопывается: первое по порядку
            seen.add(norm)
            db.add(
                FamilyParameterValue(
                    parameter_id=row.id, value=text.strip(), value_norm=norm,
                    origin=ValueOrigin.schema.value,
                )
            )
        described.append({"name": row.name, "values": len(seen)})
    db.flush()

    # Прежняя текущая версия уходит раньше: частичный уникальный индекс
    # допускает одну `frozen` на семью, и проверяется он сразу.
    if current is not None:
        current.status = SchemaStatus.superseded.value
        current.superseded_at = now
        db.flush()
    schema.status = SchemaStatus.frozen.value
    schema.frozen_at = now
    db.flush()
    record_event(
        db,
        event_type="family_schema_frozen",
        family_id=schema.family_id,
        payload={
            "schema_id": schema.id, "version": schema.version, "origin": schema.origin,
            "parameters": described, "job_id": job_id,
        },
    )


def _acquire_schema_locks(
    db: Session, *, schema_id: int, family_id: int, job_id: int | None
) -> tuple[FamilyParameterSchema, FamilyParameterSchema | None, SemanticJob | None]:
    """Семья `FOR UPDATE` -> версия `building` `FOR UPDATE` -> текущая `frozen`
    `FOR UPDATE` -> задание. Возвращает версию, текущую версию и задание."""
    _lock_families(db, [family_id], exclusive=True)
    schema = db.execute(_lock_schema_statement(schema_id, share=False)).scalar_one()
    current = db.execute(_lock_current_schema_statement(family_id)).scalar_one_or_none()
    job = db.execute(_lock_job_statement(job_id)).scalar_one() if job_id is not None else None
    return schema, current, job


def freeze_schema(
    db: Session, *, schema_id: int, answer: SchemaAnswer, guard: JobGuard | None,
    settings: Settings,
) -> FreezeOutcome:
    """Замораживает версию `building` по ответу `family_schema` (спека §2.6).

    Вердикт под блокировками: семья `active`, версия `building`, захват ещё
    наш, отпечаток текущий. Повторная заморозка уже `frozen`-версии —
    `not_applicable`. `guard=None` — вызов без задания (ручные версии), тогда
    вердикт — только состояние семьи и версии.

    Raises:
        AnswerSchemaError: имя или значение пусто после нормализации — до любой
            записи.
        ValueError: задание охраны не относится к этой версии схемы.
    """
    row = db.execute(
        sa.select(FamilyParameterSchema.family_id).where(FamilyParameterSchema.id == schema_id)
    ).one_or_none()
    if row is None:
        raise ValueError(f"версия схемы {schema_id} не найдена")
    family_id = row.family_id
    if guard is not None:
        subject = db.execute(
            sa.select(SemanticJob.kind, SemanticJob.family_id, SemanticJob.schema_id).where(
                SemanticJob.id == guard.job_id
            )
        ).one()
        if (subject.kind, subject.family_id, subject.schema_id) != (
            SemanticJobKind.family_schema.value, family_id, schema_id,
        ):
            raise ValueError(f"задание {guard.job_id} не относится к версии схемы {schema_id}")

    schema, current, job = _acquire_schema_locks(
        db, schema_id=schema_id, family_id=family_id,
        job_id=guard.job_id if guard is not None else None,
    )

    reason: UnappliedReason | None = None
    if job is not None and not _owns(job, guard):  # type: ignore[arg-type]
        reason = "lost_claim"
    else:
        family_status = db.execute(
            sa.select(WorkFamily.status).where(WorkFamily.id == family_id)
        ).scalar_one()
        if family_status != FamilyStatus.active.value or schema.status != SchemaStatus.building.value:
            reason = "not_applicable"
        elif job is not None:
            rendered = render_request_for(db, job, settings=settings)
            if rendered.request_hash != guard.expected_request_hash:  # type: ignore[union-attr]
                reason = "stale_fingerprint"
    if reason is not None:
        if job is not None and reason != "lost_claim":
            _close_job_unapplied(job, reason)
            db.flush()
        return FreezeOutcome(applied=False, unapplied_reason=reason, schema_id=schema_id)

    _check_schema_answer(answer)
    _write_frozen_schema(
        db, schema=schema, current=current, answer=answer,
        job_id=job.id if job is not None else None,
    )
    if job is not None:
        job.status = SemanticJobStatus.done.value
        job.claim_token = None
        db.flush()
    return FreezeOutcome(applied=True, unapplied_reason=None, schema_id=schema_id)


# ---------------------------------------------------------------------------
#  Обработка результата context_values
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Subject:
    """Что нужно блокировать: читается без блокировок до них и сверяется после."""

    catalog_position_id: int
    work_family_id: int | None
    pending_family_id: int | None
    work_variant_id: int | None


@dataclass(frozen=True)
class _Locked:
    subject: _Subject
    stale: bool
    context: CatalogContext
    catalog: CatalogPosition
    schema: FamilyParameterSchema | None
    job: SemanticJob | None


def _read_subject(db: Session, context_id: int) -> _Subject:
    row = db.execute(
        sa.select(
            ContextBucket.catalog_position_id,
            CatalogContext.work_family_id,
            CatalogContext.pending_family_id,
            CatalogContext.work_variant_id,
        )
        .join(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
        .where(CatalogContext.id == context_id)
    ).one_or_none()
    if row is None:
        raise ValueError(f"контекст {context_id} не найден")
    return _Subject(*row)


def _lock_catalog_row_statement(catalog_position_id: int):
    return (
        sa.select(CatalogPosition)
        .where(CatalogPosition.id == catalog_position_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _lock_context_statement(context_id: int):
    return (
        sa.select(CatalogContext)
        .where(CatalogContext.id == context_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _acquire_domain_locks(
    db: Session, *, context_id: int, schema_id: int, job_id: int | None
) -> _Locked:
    """Шаг (0) спеки §2.6: строка каталога `FOR UPDATE` -> семьи (текущая и
    ожидаемая) `FOR UPDATE` по возрастанию `id` -> версия схемы `FOR SHARE` ->
    прежний вариант `FOR UPDATE` -> контекст `FOR UPDATE` -> задание
    `FOR UPDATE`.

    Блокируется то, что прочитано до блокировок; после них чтение повторяется,
    и если контекст успел сменить строку каталога, семью, ожидание или вариант,
    результат помечается устаревшим (`stale`): полный перезахват здесь не
    нужен — отпечаток запроса всё равно разошёлся бы."""
    subject = _read_subject(db, context_id)
    catalog = db.execute(_lock_catalog_row_statement(subject.catalog_position_id)).scalar_one()
    family_ids = sorted(
        {fid for fid in (subject.work_family_id, subject.pending_family_id) if fid is not None}
    )
    _lock_families(db, family_ids, exclusive=True)
    schema = db.execute(_lock_schema_statement(schema_id, share=True)).scalar_one_or_none()
    if subject.work_variant_id is not None:
        db.execute(_lock_variant_statement(subject.work_variant_id)).all()
    context = db.execute(_lock_context_statement(context_id)).scalar_one()
    job = db.execute(_lock_job_statement(job_id)).scalar_one() if job_id is not None else None
    stale = _read_subject(db, context_id) != subject
    return _Locked(
        subject=subject, stale=stale, context=context, catalog=catalog, schema=schema, job=job
    )


def _is_applicable(db: Session, locked: _Locked) -> bool:
    """Применимость под блокировками: контекст не архивирован, есть членства,
    не `NOT_APPLICABLE`, не `SYSTEM`, строка каталога `TO_REVIEW` или
    `POSITION`, у контекста есть семья или ожидание, а `schema_id` — текущая
    версия этой семьи."""
    context = locked.context
    if context.archived_at is not None:
        return False
    if context.semantic_state == SemanticState.NOT_APPLICABLE.value:
        return False
    if context.semantic_kind == SemanticKind.SYSTEM.value:
        return False
    if locked.catalog.kind not in _APPLICABLE_CATALOG_KINDS:
        return False
    family_id = (
        context.pending_family_id
        if context.pending_family_id is not None
        else context.work_family_id
    )
    schema = locked.schema
    if (
        schema is None
        or schema.family_id != family_id
        or schema.status != SchemaStatus.frozen.value
    ):
        return False
    members = db.execute(
        sa.select(sa.func.count())
        .select_from(ContextMember)
        .where(ContextMember.context_id == context.id)
    ).scalar_one()
    return members > 0


def _values_verdict(
    db: Session, locked: _Locked, *, context_id: int, schema_id: int, guard: JobGuard | None,
    settings: Settings,
) -> UnappliedReason | None:
    """Шаг (1): `None` — результат применяется, иначе причина, по которой нет."""
    if guard is not None and not _owns(locked.job, guard):  # type: ignore[arg-type]
        return "lost_claim"
    if locked.stale:
        return "stale_fingerprint"
    if guard is not None and locked.job.schema_id != schema_id:  # type: ignore[union-attr]
        return "stale_fingerprint"
    if not _is_applicable(db, locked):
        return "not_applicable"
    if guard is not None:
        try:
            rendered = render_request_for(db, locked.job, settings=settings)  # type: ignore[arg-type]
        except SubjectNotRenderable:
            return "not_applicable"
        if rendered.request_hash != guard.expected_request_hash:
            return "stale_fingerprint"
    return None


def _listed_value_id(db: Session, *, parameter_id: int, text: str) -> int:
    """Значение списка параметра по точному тексту (слитые исключены)."""
    found = db.execute(
        sa.select(FamilyParameterValue.id).where(
            FamilyParameterValue.parameter_id == parameter_id,
            FamilyParameterValue.value == text,
            FamilyParameterValue.merged_into_id.is_(None),
        )
    ).scalar_one_or_none()
    if found is None:
        raise _schema_error("value_not_in_list", "значение не из списка параметра")
    return found


def _extend_with_value(
    db: Session, *, parameter_id: int, family_id: int, context_id: int, text: str,
    added: list[int],
) -> int:
    value_id, created = get_or_create_value(
        db, parameter_id=parameter_id, text=text, origin=ValueOrigin.extension
    )
    if created:
        added.append(value_id)
        record_event(
            db,
            event_type="family_schema_value_added",
            family_id=family_id,
            payload={
                "parameter_id": parameter_id, "value_id": value_id, "value": text.strip(),
                "origin": ValueOrigin.extension.value, "context_id": context_id,
            },
        )
    return value_id


def _switch_pending_family(db: Session, context: CatalogContext) -> int | None:
    """Шаг (5), ядро: ожидающая семья становится текущей, ожидание очищается,
    пишется `context_family_assigned`. Возвращает прежнюю семью."""
    previous_family_id = context.work_family_id
    payload: dict[str, object] = {
        "from_family_id": previous_family_id,
        "to_family_id": context.pending_family_id,
        "source": context.pending_family_source,
    }
    source = context.pending_family_source
    if source in ("suggestion", "auto_suggestion"):
        payload["suggestion_id"] = context.pending_suggestion_id
    if source == "auto_suggestion":
        suggestion = db.get(FamilySuggestion, context.pending_suggestion_id)
        payload["threshold"] = str(context.pending_threshold)
        payload["confidence"] = str(suggestion.confidence)
    actor_id = context.pending_by
    context.work_family_id = context.pending_family_id
    context.family_source = source
    context.family_by = context.pending_by
    context.family_at = _now()
    context.pending_family_id = None
    context.pending_family_source = None
    context.pending_suggestion_id = None
    context.pending_by = None
    context.pending_threshold = None
    context.pending_at = None
    db.flush()
    record_event(
        db, event_type="context_family_assigned", context_id=context.id, actor_id=actor_id,
        payload=payload,
    )
    return previous_family_id


def apply_values(
    db: Session, *, context_id: int, schema_id: int, answer: ValuesAnswer, paths_hash: str,
    guard: JobGuard | None, settings: Settings,
) -> ApplyValuesOutcome:
    """Применяет результат `context_values` одной транзакцией (спека §2.6, шаги
    (0)-(8)); коммит за вызывающим.

    (0) блокировки в порядке модуля; (1) вердикт публикации; (1а) состав
    `ordinal` ответа равен составу параметров версии; (2) значения `new`
    вставляются, найденное разрешается до канонического; (3) все значения
    контекста заменяются полным набором; (4) вариант по ключу набора; (5)
    переключение ожидающей семьи, вариант и подсказка о расхождении путей
    записываются контексту; (6) строка `TO_REVIEW` становится `POSITION`; (7)
    события и задание `done`; (8) прежний вариант архивируется, если на нём
    не осталось контекстов. Волна перезапроса после расширений списка сюда не
    входит.

    Raises:
        AnswerSchemaError: ответ не согласуется со схемой (состав `ordinal`,
            значение не из списка, пустое после нормализации) — до любой
            записи; вызывающий откатывает транзакцию.
        ValueError: задание охраны не относится к контексту.
    """
    if guard is not None:
        subject = db.execute(
            sa.select(SemanticJob.kind, SemanticJob.context_id).where(
                SemanticJob.id == guard.job_id
            )
        ).one()
        if (subject.kind, subject.context_id) != (
            SemanticJobKind.context_values.value, context_id,
        ):
            raise ValueError(f"задание {guard.job_id} не относится к контексту {context_id}")

    locked = _acquire_domain_locks(
        db, context_id=context_id, schema_id=schema_id,
        job_id=guard.job_id if guard is not None else None,
    )
    reason = _values_verdict(
        db, locked, context_id=context_id, schema_id=schema_id, guard=guard, settings=settings
    )
    if reason is not None:
        if locked.job is not None and reason != "lost_claim":
            _close_job_unapplied(locked.job, reason)
            db.flush()
        return _unapplied_values(reason)

    context = locked.context
    schema = locked.schema
    family_id = (
        context.pending_family_id
        if context.pending_family_id is not None
        else context.work_family_id
    )
    parameter_ids = _parameter_ids_by_ordinal(db, schema_id)
    items = {item.ordinal: item for item in answer.items}
    if len(items) != len(answer.items) or set(items) != set(parameter_ids):
        raise _schema_error(
            "bad_ordinal",
            f"ordinal ответа {sorted(i.ordinal for i in answer.items)} не совпадают с "
            f"параметрами схемы {sorted(parameter_ids)}",
        )
    listed: dict[int, int] = {}
    for item in answer.items:
        if item.kind == "new" and not normalize_value(item.value or ""):
            raise _schema_error(
                "empty_value", f"ordinal {item.ordinal}: значение пусто после нормализации"
            )
        if item.kind == "value":
            listed[item.ordinal] = _listed_value_id(
                db, parameter_id=parameter_ids[item.ordinal], text=item.value or ""
            )

    # (2) значения
    added: list[int] = []
    value_ids: dict[int, int | None] = {}
    sources: dict[int, str] = {}
    for ordinal in sorted(items):
        item = items[ordinal]
        if item.kind in ("value", "new"):
            value_ids[ordinal] = (
                listed[ordinal]
                if item.kind == "value"
                else _extend_with_value(
                    db, parameter_id=parameter_ids[ordinal], family_id=family_id,
                    context_id=context_id, text=item.value or "", added=added,
                )
            )
            sources[ordinal] = item.source or ""
        else:
            value_ids[ordinal] = None
            sources[ordinal] = (
                ValueSource.path_conflict.value if item.kind == "conflict"
                else ValueSource.none.value
            )

    # (3) полный набор значений контекста
    db.execute(
        sa.delete(ContextParameterValue).where(ContextParameterValue.context_id == context_id)
    )
    rows = [
        {
            "context_id": context_id, "schema_id": schema_id,
            "parameter_id": parameter_ids[ordinal], "value_id": value_ids[ordinal],
            "source": sources[ordinal], "job_id": guard.job_id if guard is not None else None,
        }
        for ordinal in sorted(items)
    ]
    if rows:
        db.execute(sa.insert(ContextParameterValue), rows)

    # (4) вариант
    previous_variant_id = context.work_variant_id
    resolved = _resolve_variant(
        db, family_id=family_id, schema_id=schema_id, value_ids_by_ordinal=value_ids
    )
    variant = resolved.variant

    # (5) ожидание, вариант, подсказка
    switched = context.pending_family_id is not None
    if switched:
        _switch_pending_family(db, context)
    context.work_variant_id = variant.id
    context.variant_at = _now()
    # Схема без параметров: путей у варианта нет, хэш — константа пустого списка.
    context.variant_paths_hash = paths_hash if parameter_ids else paths_hash_of(())
    context.variant_split_hint = (
        "path_conflict" if any(i.kind == "conflict" for i in items.values()) else None
    )
    db.flush()

    # (6) промоушен
    promoted = locked.catalog.kind == CatalogKind.TO_REVIEW.value
    if promoted:
        locked.catalog.kind = CatalogKind.POSITION.value
        db.flush()

    # (7) события и задание
    record_event(
        db,
        event_type="context_variant_assigned",
        context_id=context_id,
        payload={
            "from_variant_id": previous_variant_id,
            "to_variant_id": variant.id,
            "schema_version": schema.version,
            "values": {
                str(ordinal): {"value_id": value_ids[ordinal], "source": sources[ordinal]}
                for ordinal in sorted(items)
            },
            "promoted": promoted,
            "reactivated_variant": resolved.reactivated,
        },
    )
    if locked.job is not None:
        locked.job.status = SemanticJobStatus.done.value
        locked.job.claim_token = None
        db.flush()

    # (8) прежний вариант
    previous_archived = False
    if previous_variant_id is not None:
        previous_archived = archive_variant_if_empty(db, previous_variant_id)

    return ApplyValuesOutcome(
        applied=True, unapplied_reason=None, variant_id=variant.id,
        previous_variant_id=previous_variant_id, promoted=promoted, family_switched=switched,
        values_added=tuple(added), previous_archived=previous_archived,
    )


__all__: Sequence[str] = (
    "ApplyValuesOutcome",
    "FreezeOutcome",
    "JobGuard",
    "UnappliedReason",
    "apply_values",
    "archive_variant_if_empty",
    "canonical_value_id",
    "freeze_schema",
    "get_or_create_value",
    "get_or_create_variant",
    "normalize_value",
    "values_key_of",
)
