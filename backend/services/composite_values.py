"""Составное значение главного параметра семьи (спека упрощения каталога §2.3):
«плиты + балки» (`all`, вместе) или «Knauf или Gyproc» (`any`, или).

Составное — строка `family_parameter_values` с `composite_kind` и `composite_key`
`<вид>:<id,id,…>` (id частей по возрастанию); части — строки `family_value_parts`.
Части — простые действующие значения того же параметра; составного из составных не
бывает; одно действующее составное на `(параметр, вид, состав)`. Текст собирается из
частей в порядке id; `value_norm` — от этого текста. Модель составное не видит
никогда: запросы списков значений отбирают `composite_kind IS NULL`.

Функции ничего не коммитят: транзакцией владеет вызывающий.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

import sqlalchemy as sa
from sqlalchemy.orm import Session

from models import (
    COMPOSITE_KINDS,
    FamilyParameter,
    FamilyParameterSchema,
    FamilyParameterValue,
    FamilyValuePart,
    ValueOrigin,
)
from services.ai_journal import record_action
from services.human_decisions import match_veto
from services.semantic_events import record_event
from services.work_variants import (
    bump_revision,
    canonical_value_id,
    merge_parameter_values,
    normalize_value,
)

#: Связка частей в тексте составного значения.
COMPOSITE_JOIN = {"all": " + ", "any": " или "}

REFUSE_MERGE_COMPOSITE_MISMATCH = "merge_values_composite_mismatch"
REFUSE_SCHEMA_COMPOSITE_TEXT = "schema_composite_text"


def composite_key(kind: str, part_ids: Iterable[int]) -> str:
    """Ключ состава: вид и id частей по возрастанию, повторы схлопнуты."""
    return f"{kind}:{','.join(str(part) for part in sorted(set(part_ids)))}"


def _active_composite_id(db: Session, parameter_id: int, key: str) -> int | None:
    return db.execute(
        sa.select(FamilyParameterValue.id).where(
            FamilyParameterValue.parameter_id == parameter_id,
            FamilyParameterValue.composite_key == key,
            FamilyParameterValue.merged_into_id.is_(None),
            FamilyParameterValue.retired_at.is_(None),
        )
    ).scalar_one_or_none()


def _checked_parts(db: Session, parameter_id: int, part_ids: Sequence[int]) -> list[int]:
    """Канонические id частей по возрастанию. Слитая часть заменяется канонической.

    Raises:
        ValueError: часть другого параметра, составная или выведенная; различных
            частей меньше двух.
    """
    canonical = sorted({canonical_value_id(db, part_id) for part_id in part_ids})
    rows = {
        row.id: row
        for row in db.execute(
            sa.select(
                FamilyParameterValue.id, FamilyParameterValue.parameter_id,
                FamilyParameterValue.composite_kind, FamilyParameterValue.retired_at,
            ).where(FamilyParameterValue.id.in_(canonical))
        ).all()
    }
    for part_id in canonical:
        row = rows[part_id]
        if row.parameter_id != parameter_id:
            raise ValueError(f"часть {part_id} не из параметра {parameter_id}")
        if row.composite_kind is not None:
            raise ValueError(f"часть {part_id} составная: составное из составных не бывает")
        if row.retired_at is not None:
            raise ValueError(f"часть {part_id} выведена из списка")
    if len(canonical) < 2:
        raise ValueError("у составного значения меньше двух различных частей")
    return canonical


def _create_veto_value(
    db: Session, parameter_id: int, value_norm: str, source_text: str | None
) -> int:
    """Значение-замена вето, которого в параметре ещё нет: простое, `origin = 'manual'`,
    с исходным текстом, если его норма равна норме значения, иначе с самой нормой."""
    text = (
        source_text.strip()
        if source_text is not None and normalize_value(source_text) == value_norm
        else value_norm
    )
    row = FamilyParameterValue(
        parameter_id=parameter_id, value=text, value_norm=value_norm,
        origin=ValueOrigin.manual.value,
    )
    db.add(row)
    db.flush()
    return row.id


def find_or_create_composite(
    db: Session, *, parameter_id: int, kind: str, part_ids: Sequence[int],
    context_id: int | None, source_text: str | None, new_part_ids: Sequence[int] = (),
) -> int:
    """Значение для состава `part_ids` вида `kind`: id простого значения-вето, либо
    действующего составного, либо только что созданного.

    Порядок: инварианты частей; вето состава (спека §2.6) — оно сильнее всего,
    составное тогда не создаётся и журнал не пишется; существующее составное по
    ключу; иначе создание (`origin = 'extension'`), расширение списка
    (`revision` версии, событие `family_schema_value_added`) и одна запись журнала
    `composite`. `new_part_ids` — части, заведённые тем же ответом, `source_text` —
    исходный текст склейки. Текст составного совпал с нормой уже существующего
    простого действующего значения — возвращается оно (точное совпадение).

    Raises:
        ValueError: неизвестный вид; часть чужого параметра, составная или
            выведенная; различных частей меньше двух; норма текста занята значением,
            которое возвращать нельзя (составное или выведенное).
    """
    if kind not in COMPOSITE_KINDS:
        raise ValueError(f"неизвестный вид составного значения {kind!r}")
    parts = _checked_parts(db, parameter_id, part_ids)
    match = match_veto(db, parameter_id=parameter_id, kind=kind, part_ids=frozenset(parts))
    if match is not None:
        return match.value_id if match.value_id is not None else _create_veto_value(
            db, parameter_id, match.value_norm, source_text
        )
    key = composite_key(kind, parts)
    existing = _active_composite_id(db, parameter_id, key)
    if existing is not None:
        return existing

    texts = dict(
        db.execute(
            sa.select(FamilyParameterValue.id, FamilyParameterValue.value).where(
                FamilyParameterValue.id.in_(parts)
            )
        ).all()
    )
    text = COMPOSITE_JOIN[kind].join(texts[part_id] for part_id in parts)
    norm = normalize_value(text)
    clash = db.execute(
        sa.select(FamilyParameterValue.id).where(
            FamilyParameterValue.parameter_id == parameter_id,
            FamilyParameterValue.value_norm == norm,
        )
    ).scalar_one_or_none()
    if clash is not None:
        clash_id = canonical_value_id(db, clash)
        simple = db.execute(
            sa.select(FamilyParameterValue.composite_kind, FamilyParameterValue.retired_at).where(
                FamilyParameterValue.id == clash_id
            )
        ).one()
        if simple.composite_kind is None and simple.retired_at is None:
            return clash_id
        raise ValueError(f"текст составного значения занят значением {clash_id}")

    row = FamilyParameterValue(
        parameter_id=parameter_id, value=text, value_norm=norm,
        origin=ValueOrigin.extension.value, composite_kind=kind, composite_key=key,
    )
    db.add(row)
    db.flush()
    db.add_all(
        FamilyValuePart(value_id=row.id, part_value_id=part_id, parameter_id=parameter_id)
        for part_id in parts
    )
    db.flush()
    schema_id, family_id = db.execute(
        sa.select(FamilyParameter.schema_id, FamilyParameterSchema.family_id)
        .join(FamilyParameterSchema, FamilyParameterSchema.id == FamilyParameter.schema_id)
        .where(FamilyParameter.id == parameter_id)
    ).one()
    bump_revision(db, schema_id)
    record_event(
        db, event_type="family_schema_value_added", family_id=family_id,
        payload={
            "parameter_id": parameter_id, "value_id": row.id, "value": text,
            "origin": ValueOrigin.extension.value, "context_id": context_id,
        },
    )
    record_action(
        db, kind="composite", family_id=family_id, context_id=context_id,
        parameter_id=parameter_id, value_id=row.id,
        payload={
            "text": source_text if source_text is not None else text, "kind": kind,
            "part_ids": parts, "new_part_ids": list(new_part_ids),
            "context_ids": [] if context_id is None else [context_id],
        },
    )
    return row.id


def composite_parts(
    db: Session, value_ids: Iterable[int]
) -> dict[int, tuple[str, tuple[int, ...]]]:
    """`{id составного: (вид, id частей по возрастанию)}` среди `value_ids`; простые
    значения в результат не входят. Один запрос."""
    ids = list(set(value_ids))
    if not ids:
        return {}
    result: dict[int, tuple[str, list[int]]] = {}
    for row in db.execute(
        sa.select(
            FamilyParameterValue.id, FamilyParameterValue.composite_kind,
            FamilyValuePart.part_value_id,
        )
        .join(FamilyValuePart, FamilyValuePart.value_id == FamilyParameterValue.id)
        .where(FamilyParameterValue.id.in_(ids), FamilyParameterValue.composite_kind.is_not(None))
        .order_by(FamilyParameterValue.id, FamilyValuePart.part_value_id)
    ).all():
        result.setdefault(row.id, (row.composite_kind, []))[1].append(row.part_value_id)
    return {value_id: (kind, tuple(parts)) for value_id, (kind, parts) in result.items()}


def rebuild_after_part_merge(
    db: Session, *, parameter_id: int, source_id: int, canonical_id: int, actor_id: int | None
) -> None:
    """Слияние части `source_id` в `canonical_id` (спека §2.3, место 2): в каждом
    действующем составном, где была `source_id`, часть заменяется на `canonical_id`.
    Частей стало меньше двух (слили одну часть с другой) — составное сливается в
    оставшееся простое; новый ключ уже занят другим составным — составное сливается в
    него; иначе состав, текст и ключ переписываются на месте, `value_id` остаётся.
    Слияния идут через `merge_parameter_values` в той же транзакции."""
    composites = db.execute(
        sa.select(FamilyParameterValue)
        .join(FamilyValuePart, FamilyValuePart.value_id == FamilyParameterValue.id)
        .where(
            FamilyParameterValue.parameter_id == parameter_id,
            FamilyValuePart.part_value_id == source_id,
            FamilyParameterValue.merged_into_id.is_(None),
            FamilyParameterValue.retired_at.is_(None),
        )
        .order_by(FamilyParameterValue.id)
        .execution_options(populate_existing=True)
    ).scalars().all()
    for composite in composites:
        old_parts = db.execute(
            sa.select(FamilyValuePart.part_value_id).where(FamilyValuePart.value_id == composite.id)
        ).scalars().all()
        new_parts = sorted({canonical_id if part == source_id else part for part in old_parts})
        if len(new_parts) < 2:
            merge_parameter_values(
                db, parameter_id=parameter_id, source_value_id=composite.id,
                target_value_id=new_parts[0], actor_id=actor_id, cascade=True,
            )
            continue
        key = composite_key(composite.composite_kind, new_parts)
        taken = _active_composite_id(db, parameter_id, key)
        if taken is not None:
            merge_parameter_values(
                db, parameter_id=parameter_id, source_value_id=composite.id,
                target_value_id=taken, actor_id=actor_id, cascade=True,
            )
            continue
        texts = dict(
            db.execute(
                sa.select(FamilyParameterValue.id, FamilyParameterValue.value).where(
                    FamilyParameterValue.id.in_(new_parts)
                )
            ).all()
        )
        text = COMPOSITE_JOIN[composite.composite_kind].join(texts[part] for part in new_parts)
        norm = normalize_value(text)
        db.execute(
            sa.delete(FamilyValuePart).where(
                FamilyValuePart.value_id == composite.id,
                FamilyValuePart.part_value_id == source_id,
            )
        )
        if canonical_id not in old_parts:
            db.add(
                FamilyValuePart(
                    value_id=composite.id, part_value_id=canonical_id, parameter_id=parameter_id
                )
            )
        composite.composite_key = key
        # Норма нового текста занята посторонней строкой — прежний текст остаётся:
        # `(parameter_id, value_norm)` уникален, а состав определяет ключ.
        occupied = db.execute(
            sa.select(FamilyParameterValue.id).where(
                FamilyParameterValue.parameter_id == parameter_id,
                FamilyParameterValue.value_norm == norm,
                FamilyParameterValue.id != composite.id,
            )
        ).first()
        if occupied is None:
            composite.value = text
            composite.value_norm = norm
        db.flush()
