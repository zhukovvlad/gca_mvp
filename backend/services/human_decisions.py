"""Решения человека над составными значениями и приравниванием (спека упрощения
каталога §2.6): вето состава и «не дубль».

Решение записано текстом, а не id: семья, `name_norm` параметра и `value_norm`
значений. `id` значений меняются с каждой версией схемы, нормы действующих значений —
нет. Решение разрешается при каждой проверке по нормам через `canonical_value_id`;
слияние значения переписывает нормы решений в той же транзакции
(`rewrite_after_merge`), слияние семей переносит решения на цель
(`transfer_family`).

Функции ничего не коммитят: транзакцией владеет вызывающий.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased

from models import (
    COMPOSITE_KINDS,
    CompositeVeto,
    CompositeVetoPart,
    FamilyParameter,
    FamilyParameterSchema,
    FamilyParameterValue,
    ValueNotDuplicate,
)
from services.work_variants import canonical_value_id as canonical_of

logger = logging.getLogger(__name__)


def _parameter_scope(db: Session, parameter_id: int) -> tuple[int, str]:
    """`(family_id, name_norm)` параметра: ключ, по которому решения живут между
    версиями схемы."""
    row = db.execute(
        sa.select(FamilyParameterSchema.family_id, FamilyParameter.name_norm)
        .join(FamilyParameterSchema, FamilyParameterSchema.id == FamilyParameter.schema_id)
        .where(FamilyParameter.id == parameter_id)
    ).one()
    return row.family_id, row.name_norm


def _value_row(db: Session, value_id: int) -> FamilyParameterValue:
    return db.execute(
        sa.select(FamilyParameterValue)
        .where(FamilyParameterValue.id == value_id)
        .execution_options(populate_existing=True)
    ).scalar_one()


def _id_by_norm(db: Session, parameter_id: int, value_norm: str) -> int | None:
    """Строка значения параметра с такой нормой (слитая тоже), без канонизации."""
    return db.execute(
        sa.select(FamilyParameterValue.id).where(
            FamilyParameterValue.parameter_id == parameter_id,
            FamilyParameterValue.value_norm == value_norm,
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
#  Вето состава
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VetoMatch:
    """Совпавшее вето: норма значения-замены и его id в параметре (`None` — значения
    ещё нет, вызывающий заводит его с исходным текстом)."""

    value_norm: str
    value_id: int | None


def match_veto(
    db: Session, *, parameter_id: int, kind: str, part_ids: frozenset[int]
) -> VetoMatch | None:
    """Вето, совпавшее с составом `part_ids`, либо `None`.

    Вето той же семьи, с `name_norm` этого параметра и тем же видом: каждая
    `part_norm` -> строка значения параметра с этой нормой (слитая тоже) ->
    `canonical_value_id`. Вето совпадает с запросом, если множество канонических
    id его частей равно запрошенному. Части, не нашедшиеся в параметре, и части,
    схлопнувшиеся меньше чем в две, — вето молчит; выведенное из списка значение
    замены — тоже. Коллизия (несколько вето совпали с разными значениями) —
    выигрывает позднее (`set_at`, затем `id`), она пишется в лог.
    """
    family_id, name_norm = _parameter_scope(db, parameter_id)
    vetoes = db.execute(
        sa.select(CompositeVeto)
        .where(
            CompositeVeto.family_id == family_id,
            CompositeVeto.parameter_name_norm == name_norm,
            CompositeVeto.kind == kind,
        )
        .order_by(CompositeVeto.set_at.desc(), CompositeVeto.id.desc())
        .execution_options(populate_existing=True)
    ).scalars().all()
    if not vetoes:
        return None
    requested = {canonical_of(db, value_id) for value_id in part_ids}
    if len(requested) < 2:
        return None
    norms_of: dict[int, list[str]] = {veto.id: [] for veto in vetoes}
    for row in db.execute(
        sa.select(CompositeVetoPart.veto_id, CompositeVetoPart.part_norm).where(
            CompositeVetoPart.veto_id.in_(list(norms_of))
        )
    ).all():
        norms_of[row.veto_id].append(row.part_norm)

    matches: list[tuple[int, VetoMatch]] = []
    for veto in vetoes:
        resolved: set[int] = set()
        for norm in norms_of[veto.id]:
            found = _id_by_norm(db, parameter_id, norm)
            if found is None:
                break
            resolved.add(canonical_of(db, found))
        else:
            if resolved == requested:
                target = _id_by_norm(db, parameter_id, veto.value_norm)
                canonical = None if target is None else canonical_of(db, target)
                if canonical is not None and _value_row(db, canonical).retired_at is not None:
                    continue
                matches.append((veto.id, VetoMatch(veto.value_norm, canonical)))
    if not matches:
        return None
    chosen_id, chosen = matches[0]
    if len({match.value_norm if match.value_id is None else match.value_id for _, match in matches}) > 1:
        logger.warning(
            "composite veto collision: parameter %s, vetoes %s, winner %s",
            parameter_id, [veto_id for veto_id, _ in matches], chosen_id,
        )
    return chosen


def resolve_veto(
    db: Session, *, parameter_id: int, kind: str, part_ids: frozenset[int]
) -> int | None:
    """Значение V, которым вето заменяет состав `part_ids`, либо `None`, если вето
    нет, оно молчит или V в параметре ещё нет (его заводит
    `composite_values.find_or_create_composite`)."""
    match = match_veto(db, parameter_id=parameter_id, kind=kind, part_ids=part_ids)
    return None if match is None else match.value_id


def add_veto(
    db: Session, *, parameter_id: int, kind: str, part_ids: Sequence[int], value_id: int,
    user_id: int,
) -> int:
    """Вето «этот состав — одно простое значение `value_id`»: нормы частей и
    значения записываются текстом.

    Raises:
        ValueError: неизвестный вид; значение или часть составные либо чужого
            параметра; различных частей меньше двух.
    """
    if kind not in COMPOSITE_KINDS:
        raise ValueError(f"неизвестный вид составного значения {kind!r}")
    family_id, name_norm = _parameter_scope(db, parameter_id)
    target = _value_row(db, canonical_of(db, value_id))
    if target.parameter_id != parameter_id or target.composite_kind is not None:
        raise ValueError("значение вето — простое значение того же параметра")
    part_norms: set[str] = set()
    for part_id in part_ids:
        part = _value_row(db, canonical_of(db, part_id))
        if part.parameter_id != parameter_id or part.composite_kind is not None:
            raise ValueError("часть вето — простое значение того же параметра")
        part_norms.add(part.value_norm)
    if len(part_norms) < 2:
        raise ValueError("у вето меньше двух различных частей")
    veto = CompositeVeto(
        family_id=family_id, parameter_name_norm=name_norm, kind=kind,
        value_norm=target.value_norm, set_by=user_id,
    )
    db.add(veto)
    db.flush()
    db.add_all(CompositeVetoPart(veto_id=veto.id, part_norm=norm) for norm in sorted(part_norms))
    db.flush()
    return veto.id


# ---------------------------------------------------------------------------
#  «Не дубль»
# ---------------------------------------------------------------------------

def is_not_duplicate(
    db: Session, *, parameter_id: int, text_norm: str, value_id: int
) -> bool:
    """Запрещено ли приравнивать текст `text_norm` к значению `value_id`: решение
    разрешается по норме через `canonical_value_id`, поэтому оно переживает слияние
    значения и новую версию схемы."""
    family_id, name_norm = _parameter_scope(db, parameter_id)
    target = canonical_of(db, value_id)
    norms = db.execute(
        sa.select(ValueNotDuplicate.value_norm).where(
            ValueNotDuplicate.family_id == family_id,
            ValueNotDuplicate.parameter_name_norm == name_norm,
            ValueNotDuplicate.text_norm == text_norm,
        )
    ).scalars().all()
    for norm in norms:
        found = _id_by_norm(db, parameter_id, norm)
        if found is not None and canonical_of(db, found) == target:
            return True
    return False


def add_not_duplicate(
    db: Session, *, parameter_id: int, text_norm: str, value_id: int, user_id: int
) -> int:
    """Решение «текст не приравнивать к значению»; повтор того же решения
    возвращает прежнюю запись."""
    family_id, name_norm = _parameter_scope(db, parameter_id)
    value_norm = _value_row(db, canonical_of(db, value_id)).value_norm
    db.execute(
        pg_insert(ValueNotDuplicate)
        .values(
            family_id=family_id, parameter_name_norm=name_norm, text_norm=text_norm,
            value_norm=value_norm, set_by=user_id,
        )
        .on_conflict_do_nothing(
            constraint="uq_value_not_duplicates_family_param_text_value"
        )
    )
    return db.execute(
        sa.select(ValueNotDuplicate.id).where(
            ValueNotDuplicate.family_id == family_id,
            ValueNotDuplicate.parameter_name_norm == name_norm,
            ValueNotDuplicate.text_norm == text_norm,
            ValueNotDuplicate.value_norm == value_norm,
        )
    ).scalar_one()


# ---------------------------------------------------------------------------
#  Слияние значения и слияние семей
# ---------------------------------------------------------------------------

def rewrite_after_merge(
    db: Session, *, parameter_id: int, source_value_id: int, canonical_value_id: int
) -> None:
    """В решениях семьи с `name_norm` этого параметра норма слитого значения
    заменяется нормой канонического: `composite_veto_parts.part_norm`,
    `composite_vetoes.value_norm`, `value_not_duplicates.value_norm`. Совпавшие
    после переписи части одного вето и пары «не дубль» схлопываются в одну строку."""
    family_id, name_norm = _parameter_scope(db, parameter_id)
    source_norm = _value_row(db, source_value_id).value_norm
    canonical_norm = _value_row(db, canonical_value_id).value_norm
    veto_ids = sa.select(CompositeVeto.id).where(
        CompositeVeto.family_id == family_id,
        CompositeVeto.parameter_name_norm == name_norm,
    )
    sibling = aliased(CompositeVetoPart)
    db.execute(
        sa.delete(CompositeVetoPart).where(
            CompositeVetoPart.veto_id.in_(veto_ids),
            CompositeVetoPart.part_norm == source_norm,
            sa.exists().where(
                sibling.veto_id == CompositeVetoPart.veto_id,
                sibling.part_norm == canonical_norm,
            ),
        )
        .execution_options(synchronize_session=False)
    )
    db.execute(
        sa.update(CompositeVetoPart)
        .where(CompositeVetoPart.veto_id.in_(veto_ids), CompositeVetoPart.part_norm == source_norm)
        .values(part_norm=canonical_norm)
        .execution_options(synchronize_session=False)
    )
    db.execute(
        sa.update(CompositeVeto)
        .where(
            CompositeVeto.family_id == family_id,
            CompositeVeto.parameter_name_norm == name_norm,
            CompositeVeto.value_norm == source_norm,
        )
        .values(value_norm=canonical_norm)
        .execution_options(synchronize_session=False)
    )
    twin = aliased(ValueNotDuplicate)
    db.execute(
        sa.delete(ValueNotDuplicate).where(
            ValueNotDuplicate.family_id == family_id,
            ValueNotDuplicate.parameter_name_norm == name_norm,
            ValueNotDuplicate.value_norm == source_norm,
            sa.exists().where(
                twin.family_id == family_id,
                twin.parameter_name_norm == name_norm,
                twin.text_norm == ValueNotDuplicate.text_norm,
                twin.value_norm == canonical_norm,
            ),
        )
        .execution_options(synchronize_session=False)
    )
    db.execute(
        sa.update(ValueNotDuplicate)
        .where(
            ValueNotDuplicate.family_id == family_id,
            ValueNotDuplicate.parameter_name_norm == name_norm,
            ValueNotDuplicate.value_norm == source_norm,
        )
        .values(value_norm=canonical_norm)
        .execution_options(synchronize_session=False)
    )
    db.flush()


def transfer_family(db: Session, *, source_family_id: int, target_family_id: int) -> None:
    """Слияние семей: решения семьи-источника переходят на семью-цель. «Не дубль»,
    уже стоящий у цели, второй раз не заводится."""
    twin = aliased(ValueNotDuplicate)
    db.execute(
        sa.delete(ValueNotDuplicate).where(
            ValueNotDuplicate.family_id == source_family_id,
            sa.exists().where(
                twin.family_id == target_family_id,
                twin.parameter_name_norm == ValueNotDuplicate.parameter_name_norm,
                twin.text_norm == ValueNotDuplicate.text_norm,
                twin.value_norm == ValueNotDuplicate.value_norm,
            ),
        )
        .execution_options(synchronize_session=False)
    )
    db.execute(
        sa.update(ValueNotDuplicate)
        .where(ValueNotDuplicate.family_id == source_family_id)
        .values(family_id=target_family_id)
        .execution_options(synchronize_session=False)
    )
    db.execute(
        sa.update(CompositeVeto)
        .where(CompositeVeto.family_id == source_family_id)
        .values(family_id=target_family_id)
        .execution_options(synchronize_session=False)
    )
    db.flush()
