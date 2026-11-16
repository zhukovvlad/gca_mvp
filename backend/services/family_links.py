"""Связи семей работ: проверка, запись, состояние при чтении (спека справочных
аналогов §2.6, §2.16).

Связь — пара семей с правилом пересчёта между их единицами. Пара хранится по
возрастанию `id`; множитель `constant` и его направление (`factor_from_id`)
хранятся как ввёл человек и **никогда не обращаются**: обратное значение считается
только для показа (`link_view`). Единицы сторон запоминаются при записи; связь
действует, пока обе семьи активны и их единицы равны запомненным. Состояние
вычисляется при каждом чтении и в базу не пишется.

Отказы — `DomainError` с кодом: `422 link_invalid` (поле `reason`), `409 link_exists`,
`404 family_not_found` / `link_not_found`.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Final, Literal

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from crud.common import DomainError, iso
from models import (
    FamilyLink,
    FamilyParameter,
    FamilyParameterSchema,
    FamilyStatus,
    ParameterValueKind,
    SchemaStatus,
    UnitOfMeasure,
    User,
    WorkFamily,
)
from services.price_basis import _CONTEXT
from services.reference_math import UNIT_PAIRS, LinkKind, format_factor

CODE_LINK_INVALID: Final = "link_invalid"
CODE_LINK_EXISTS: Final = "link_exists"
CODE_FAMILY_NOT_FOUND: Final = "family_not_found"
CODE_LINK_NOT_FOUND: Final = "link_not_found"

#: Причины `422 link_invalid` (поле `reason`, спека §2.16); `pair_changed` — `PUT` назвал
#: другую пару семей.
REASONS: Final = (
    "same_family",
    "family_inactive",
    "units_mismatch",
    "param_invalid",
    "params_missing",
    "constant_invalid",
    "pair_changed",
)

#: Предел основания `constant`, знаков (в базе его ограничения нет).
BASIS_MAX: Final = 300
#: Множитель — десятичная запись без порядка и знака; длина ограничена, чтобы число
#: помещалось в `numeric`.
_FACTOR_RE: Final = re.compile(r"\d{1,30}(?:\.\d{1,30})?")

LinkInactiveReason = Literal["family_inactive", "units_changed", "no_live_side"]


class LinkInput(BaseModel):
    """Тело записи связи. `a`/`b` — стороны как их назвал человек; сервер упорядочивает
    пару по `id`, переставляя параметры вместе с семьями. `factor` — десятичная строка."""

    model_config = ConfigDict(extra="forbid")

    family_a_id: int
    family_b_id: int
    kind: LinkKind
    param_a_id: int | None = None
    param_b_id: int | None = None
    factor_from_family_id: int | None = None
    factor: str | None = None
    basis: str | None = None


@dataclass(frozen=True)
class SideInfo:
    """Сторона связи для показа: семья, её единица и параметр."""

    family_id: int
    title: str
    unit: str | None  # symbol
    param_id: int | None
    param_name: str | None


@dataclass(frozen=True)
class LinkState:
    link: FamilyLink
    active: bool
    inactive_reason: LinkInactiveReason | None
    live_lo: bool  # параметр стороны активен и на действующей версии
    live_hi: bool
    lo: SideInfo | None = None
    hi: SideInfo | None = None
    created_by: dict | None = None
    updated_by: dict | None = None


# ---------------------------------------------------------------------------
#  Отказы
# ---------------------------------------------------------------------------

def _invalid(reason: str, message: str) -> DomainError:
    return DomainError(422, message, code=CODE_LINK_INVALID, context={"reason": reason})


def _no_family(family_id: int) -> DomainError:
    return DomainError(
        404, f"семья {family_id} не найдена", code=CODE_FAMILY_NOT_FOUND,
        context={"family_id": family_id},
    )


def _no_link(link_id: int) -> DomainError:
    return DomainError(
        404, f"связь {link_id} не найдена", code=CODE_LINK_NOT_FOUND, context={"link_id": link_id}
    )


# ---------------------------------------------------------------------------
#  Чтение
# ---------------------------------------------------------------------------

def links_of_families(db: Session, family_ids: Collection[int]) -> list[LinkState]:
    """Связи, у которых хотя бы одна сторона среди `family_ids`, по возрастанию `id`, с
    состоянием на момент чтения. Запросов — константа, сколько бы связей ни было."""
    ids = sorted(set(family_ids))
    if not ids:
        return []
    links = list(
        db.execute(
            sa.select(FamilyLink)
            .where(sa.or_(FamilyLink.family_lo_id.in_(ids), FamilyLink.family_hi_id.in_(ids)))
            .order_by(FamilyLink.id)
            .execution_options(populate_existing=True)
        ).scalars()
    )
    if not links:
        return []

    side_ids = {fid for link in links for fid in (link.family_lo_id, link.family_hi_id)}
    families = {
        row.id: row
        for row in db.execute(
            sa.select(WorkFamily.id, WorkFamily.title, WorkFamily.status, WorkFamily.unit_id)
            .where(WorkFamily.id.in_(side_ids))
        )
    }
    current_schema = dict(
        db.execute(
            sa.select(FamilyParameterSchema.family_id, FamilyParameterSchema.id).where(
                FamilyParameterSchema.family_id.in_(side_ids),
                FamilyParameterSchema.status == SchemaStatus.frozen.value,
            )
        ).all()
    )
    param_ids = {
        pid for link in links for pid in (link.param_lo_id, link.param_hi_id) if pid is not None
    }
    params = {
        row.id: row
        for row in db.execute(
            sa.select(
                FamilyParameter.id, FamilyParameter.name, FamilyParameter.schema_id,
                FamilyParameter.archived_at,
            ).where(FamilyParameter.id.in_(param_ids))
        )
    } if param_ids else {}
    unit_ids = {f.unit_id for f in families.values() if f.unit_id is not None}
    symbols = dict(
        db.execute(
            sa.select(UnitOfMeasure.id, UnitOfMeasure.symbol).where(UnitOfMeasure.id.in_(unit_ids))
        ).all()
    ) if unit_ids else {}
    user_ids = {uid for link in links for uid in (link.created_by, link.updated_by)}
    emails = dict(
        db.execute(sa.select(User.id, User.email).where(User.id.in_(user_ids))).all()
    )

    def live(family_id: int, param_id: int | None) -> bool:
        param = params.get(param_id) if param_id is not None else None
        return (
            param is not None
            and param.archived_at is None
            and param.schema_id == current_schema.get(family_id)
        )

    def side(family_id: int, param_id: int | None) -> SideInfo:
        family = families[family_id]
        param = params.get(param_id) if param_id is not None else None
        return SideInfo(
            family_id=family_id,
            title=family.title,
            unit=symbols.get(family.unit_id),
            param_id=param_id,
            param_name=None if param is None else param.name,
        )

    def author(user_id: int) -> dict:
        return {"id": user_id, "email": emails.get(user_id)}

    states: list[LinkState] = []
    for link in links:
        lo, hi = families[link.family_lo_id], families[link.family_hi_id]
        live_lo, live_hi = live(lo.id, link.param_lo_id), live(hi.id, link.param_hi_id)
        reason: LinkInactiveReason | None = None
        if lo.status != FamilyStatus.active.value or hi.status != FamilyStatus.active.value:
            reason = "family_inactive"
        elif lo.unit_id != link.unit_lo_id or hi.unit_id != link.unit_hi_id:
            reason = "units_changed"
        elif link.kind != "constant" and not (live_lo or live_hi):
            reason = "no_live_side"
        states.append(
            LinkState(
                link=link, active=reason is None, inactive_reason=reason,
                live_lo=live_lo, live_hi=live_hi,
                lo=side(lo.id, link.param_lo_id), hi=side(hi.id, link.param_hi_id),
                created_by=author(link.created_by), updated_by=author(link.updated_by),
            )
        )
    return states


def _factor_text(value: Decimal) -> str:
    return format(value, "f")


def _shown(link: FamilyLink, from_family_id: int) -> dict | None:
    """Равенство `constant`, развёрнутое от карточки: хранимый множитель, если левая часть
    — эта семья, иначе обратный; печатная форма §2.8, «≈» — округление изменило число."""
    if link.kind != "constant" or link.factor is None:
        return None
    if link.factor_from_id == from_family_id:
        number = link.factor
    else:
        with localcontext(_CONTEXT):
            number = Decimal(1) / link.factor
    text, approximate = format_factor(number)
    return {"from_family_id": from_family_id, "factor": ("≈" if approximate else "") + text}


def link_view(state: LinkState, *, from_family_id: int) -> dict:
    """Форма `GET /families/{id}/links` (§2.16), развёрнутая от `from_family_id`.

    `factor` и `factor_from_family_id` — как хранятся; `shown` — для показа. `stale_sides`
    — стороны («this» — эта семья, «other» — другая), у которых параметр задан, но не
    жив."""
    link = state.link
    if state.lo is None or state.hi is None:
        raise ValueError("состояние связи собрано без сторон")
    if from_family_id == link.family_lo_id:
        this, other, live_this, live_other = state.lo, state.hi, state.live_lo, state.live_hi
    elif from_family_id == link.family_hi_id:
        this, other, live_this, live_other = state.hi, state.lo, state.live_hi, state.live_lo
    else:
        raise ValueError(f"семья {from_family_id} не сторона связи {link.id}")

    def param(info: SideInfo) -> dict | None:
        return None if info.param_id is None else {"id": info.param_id, "name": info.param_name}

    stale = [
        name
        for name, info, is_live in (("this", this, live_this), ("other", other, live_other))
        if info.param_id is not None and not is_live
    ]
    return {
        "id": link.id,
        "other_family": {"id": other.family_id, "title": other.title, "unit": other.unit},
        "kind": link.kind,
        "this_param": param(this),
        "other_param": param(other),
        "factor_from_family_id": link.factor_from_id,
        "factor": None if link.factor is None else _factor_text(link.factor),
        "basis": link.basis,
        "shown": _shown(link, from_family_id),
        "state": "active" if state.active else "inactive",
        "inactive_reason": state.inactive_reason,
        "stale_sides": stale,
        "created_by": state.created_by,
        "created_at": iso(link.created_at),
        "updated_by": state.updated_by,
        "updated_at": iso(link.updated_at),
    }


def family_links_view(db: Session, family_id: int) -> list[dict]:
    """Связи семьи, развёрнутые от неё. `404 family_not_found` — нет семьи."""
    if db.get(WorkFamily, family_id) is None:
        raise _no_family(family_id)
    return [
        link_view(state, from_family_id=family_id) for state in links_of_families(db, [family_id])
    ]


# ---------------------------------------------------------------------------
#  Проверка записи
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Checked:
    family_lo_id: int
    family_hi_id: int
    unit_lo_id: int
    unit_hi_id: int
    kind: str
    param_lo_id: int | None
    param_hi_id: int | None
    factor_from_id: int | None
    factor: Decimal | None
    basis: str | None


def _parse_factor(text: str | None) -> Decimal:
    if text is None or _FACTOR_RE.fullmatch(text.strip()) is None:
        raise _invalid("constant_invalid", "множитель — десятичное число, больше нуля")
    value = Decimal(text.strip())
    if value <= 0:
        raise _invalid("constant_invalid", "множитель — десятичное число, больше нуля")
    return value


def _own_param_is_valid(db: Session, family_id: int, param_id: int) -> bool:
    """Параметр — свой у семьи: активный, точных значений, на действующей версии схемы."""
    return (
        db.execute(
            sa.select(FamilyParameter.id)
            .join(FamilyParameterSchema, FamilyParameterSchema.id == FamilyParameter.schema_id)
            .where(
                FamilyParameter.id == param_id,
                FamilyParameter.archived_at.is_(None),
                FamilyParameter.value_kind == ParameterValueKind.exact.value,
                FamilyParameterSchema.family_id == family_id,
                FamilyParameterSchema.status == SchemaStatus.frozen.value,
            )
        ).first()
        is not None
    )


def _validate(
    db: Session, body: LinkInput, *, expected_pair: tuple[int, int] | None = None
) -> _Checked:
    """Порядок отказов: `same_family`, `pair_changed`, `family_inactive`, `units_mismatch`,
    затем `param_invalid`/`params_missing` у видов с параметром и `constant_invalid`."""
    a_id, b_id = body.family_a_id, body.family_b_id
    if a_id == b_id:
        raise _invalid("same_family", "связь держит две разные семьи")
    if expected_pair is not None and set(expected_pair) != {a_id, b_id}:
        raise _invalid("pair_changed", "пара семей связи не меняется: заведите новую связь")

    # Обе семьи — под `FOR SHARE` по возрастанию `id`: активность и единица не уйдут между
    # проверкой и записью (смена единицы и архивирование берут `FOR UPDATE`).
    families = {
        family.id: family
        for family in db.execute(
            sa.select(WorkFamily)
            .where(WorkFamily.id.in_((a_id, b_id)))
            .order_by(WorkFamily.id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        ).scalars()
    }
    for family_id in (a_id, b_id):
        if family_id not in families:
            raise _no_family(family_id)
    a, b = families[a_id], families[b_id]
    if a.status != FamilyStatus.active.value or b.status != FamilyStatus.active.value:
        raise _invalid("family_inactive", "связь заводится между активными семьями")

    kind = body.kind
    unit_ids = {u for u in (a.unit_id, b.unit_id) if u is not None}
    symbols = dict(
        db.execute(
            sa.select(UnitOfMeasure.id, UnitOfMeasure.symbol).where(UnitOfMeasure.id.in_(unit_ids))
        ).all()
    ) if unit_ids else {}
    if a.unit_id is None or b.unit_id is None:
        raise _invalid("units_mismatch", "у обеих семей должна быть единица")
    if kind == "constant":
        if a.unit_id == b.unit_id:
            raise _invalid("units_mismatch", "у постоянной связи единицы семей различны")
    elif {symbols[a.unit_id], symbols[b.unit_id]} != set(UNIT_PAIRS[kind]):
        raise _invalid(
            "units_mismatch",
            f"вид «{kind}» связывает единицы {UNIT_PAIRS[kind][0]} и {UNIT_PAIRS[kind][1]}",
        )

    factor: Decimal | None = None
    basis: str | None = None
    factor_from_id: int | None = None
    if kind == "constant":
        if body.param_a_id is not None or body.param_b_id is not None:
            raise _invalid("constant_invalid", "у постоянной связи параметров нет")
        factor = _parse_factor(body.factor)
        basis = (body.basis or "").strip()
        if not basis:
            raise _invalid("constant_invalid", "у постоянной связи нужно основание")
        if len(basis) > BASIS_MAX:
            raise _invalid("constant_invalid", f"основание — не более {BASIS_MAX} знаков")
        if body.factor_from_family_id not in (a_id, b_id):
            raise _invalid("constant_invalid", "левая часть равенства — одна из двух семей пары")
        factor_from_id = body.factor_from_family_id
    else:
        for family_id, param_id in ((a_id, body.param_a_id), (b_id, body.param_b_id)):
            if param_id is not None and not _own_param_is_valid(db, family_id, param_id):
                raise _invalid(
                    "param_invalid",
                    f"параметр {param_id} — не действующий параметр точных значений семьи "
                    f"{family_id}",
                )
        if body.param_a_id is None and body.param_b_id is None:
            raise _invalid("params_missing", "нужен параметр хотя бы одной стороны")
        if (
            body.factor is not None
            or body.basis is not None
            or body.factor_from_family_id is not None
        ):
            raise _invalid(
                "constant_invalid", "множитель, основание и его направление — только у постоянной связи"
            )

    if a_id < b_id:
        lo_id, hi_id, lo, hi = a_id, b_id, a, b
        param_lo, param_hi = body.param_a_id, body.param_b_id
    else:
        lo_id, hi_id, lo, hi = b_id, a_id, b, a
        param_lo, param_hi = body.param_b_id, body.param_a_id
    return _Checked(
        family_lo_id=lo_id, family_hi_id=hi_id,
        unit_lo_id=lo.unit_id, unit_hi_id=hi.unit_id,
        kind=kind, param_lo_id=param_lo, param_hi_id=param_hi,
        factor_from_id=factor_from_id, factor=factor, basis=basis,
    )


def _apply(link: FamilyLink, checked: _Checked) -> None:
    link.unit_lo_id = checked.unit_lo_id
    link.unit_hi_id = checked.unit_hi_id
    link.kind = checked.kind
    link.param_lo_id = checked.param_lo_id
    link.param_hi_id = checked.param_hi_id
    link.factor_from_id = checked.factor_from_id
    link.factor = checked.factor
    link.basis = checked.basis


# ---------------------------------------------------------------------------
#  Запись
# ---------------------------------------------------------------------------

def _link_exists(checked: _Checked) -> DomainError:
    return DomainError(
        409, "связь этой пары семей уже есть", code=CODE_LINK_EXISTS,
        context={"family_lo_id": checked.family_lo_id, "family_hi_id": checked.family_hi_id},
    )


def create_link(db: Session, *, body: LinkInput, actor_id: int) -> int:
    checked = _validate(db, body)
    exists = db.execute(
        sa.select(FamilyLink.id).where(
            FamilyLink.family_lo_id == checked.family_lo_id,
            FamilyLink.family_hi_id == checked.family_hi_id,
        )
    ).first()
    if exists is not None:
        raise _link_exists(checked)
    link = FamilyLink(
        family_lo_id=checked.family_lo_id, family_hi_id=checked.family_hi_id,
        created_by=actor_id, updated_by=actor_id,
        unit_lo_id=checked.unit_lo_id, unit_hi_id=checked.unit_hi_id,
    )
    _apply(link, checked)
    try:
        with db.begin_nested():
            db.add(link)
            db.flush()
    except IntegrityError as exc:
        if "uq_family_links_pair" not in str(exc.orig):
            raise
        raise _link_exists(checked) from exc
    return link.id


def update_link(db: Session, link_id: int, *, body: LinkInput, actor_id: int) -> None:
    """Правка той же пары (в любом порядке `a`, `b`): перезапоминает текущие единицы семей
    и заново проверяет их по виду. `factor` и `factor_from_id` пишутся как пришли."""
    link = db.execute(
        sa.select(FamilyLink)
        .where(FamilyLink.id == link_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if link is None:
        raise _no_link(link_id)
    checked = _validate(db, body, expected_pair=(link.family_lo_id, link.family_hi_id))
    _apply(link, checked)
    link.updated_by = actor_id
    db.flush()


def delete_link(db: Session, link_id: int) -> None:
    """Цели со ссылкой на связь получают `ref_link_id = NULL` (внешний ключ `SET NULL`),
    снимок цели остаётся."""
    deleted = db.execute(
        sa.delete(FamilyLink).where(FamilyLink.id == link_id).returning(FamilyLink.id)
    ).first()
    if deleted is None:
        raise _no_link(link_id)
