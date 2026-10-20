"""Правка схемы семьи на месте: план, предпросмотр, применение (спека
`2026-10-19-catalog-parameters-design.md` §2.5–§2.7).

Правка не заводит версию: все изменения пишутся в действующую `frozen` версию,
`revision` растёт на единицу за правку. Тело — полное желаемое состояние
активных параметров (`ParameterDraft`); существующий активный параметр, которого
в теле нет, архивируется, новый (`ordinal = None`) получает `max(ordinal) + 1`
по всем параметрам версии, архивным тоже (§2.2).

Предпросмотр и применение строят ОДИН план (`_build_plan`): сводка
(`EditSummary`) вычисляется из плана, а применение исполняет тот же план, а не
повторяет вычисление. Предпросмотр читает без блокировок и ничего не пишет;
числа сводки — что сделает сверка очереди после правки (`preview_values_plan`,
`project_recompute`), а не оценка «на глаз».

Порядок блокировок (`services/work_variants.py`, §2.5): семья `FOR UPDATE`
первой -> действующая версия `FOR UPDATE` -> параметры и значения по `id` ->
контексты по `id` (строки значений) -> варианты (пересчёт) -> задания
(сверка) последними. `apply_schema_edit` сам берёт замки, перечитывает
`revision` и состояние под ними, строит план заново и сверяет `preview_hash`:
параллельная правка, не сдвинувшая чисел сводки, всё равно меняет `revision`.

Функции ничего не коммитят: транзакцией владеет вызывающий.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from config import settings
from models import (
    CatalogContext,
    ContextParameterValue,
    FamilyParameter,
    FamilyParameterSchema,
    FamilyParameterValue,
    ParameterOrigin,
    ParameterRole,
    ParameterValueKind,
    SchemaStatus,
    SemanticJobKind,
    ValueOrigin,
    ValueSource,
    WorkFamily,
)
from services.parameter_ranges import build_range_values
from services.semantic_cost import RESERVE_FORMULA_VERSION, tariffs_from
from services.semantic_events import record_event
from services.semantic_reconcile import (
    NO_CAP,
    _estimate_entries,
    preview_values_plan,
    reconcile_context_values,
)
from services.variant_request import SchemaParameterIn
from services.work_families import (
    REFUSE_FAMILY_NOT_ACTIVE,
    REFUSE_FAMILY_NOT_FOUND,
    WorkFamilyError,
)
from services.work_variants import (
    REFUSE_SCHEMA_BAD_ORDINALS,
    REFUSE_SCHEMA_BLANK,
    REFUSE_SCHEMA_BUILDING,
    REFUSE_SCHEMA_NO_CURRENT,
    REFUSE_SCHEMA_PARAMETER_RENAMED,
    REFUSE_SCHEMA_VALUE_REMOVED,
    _lock_contexts_statement,
    _lock_current_schema_statement,
    _lock_family_for_schema_life,
    answered_main_rows,
    bump_revision,
    mark_rows_for_refresh,
    normalize_value,
    project_recompute,
    recompute_candidates,
    recompute_variants,
)

REFUSE_SCHEMA_TOO_MANY_PARAMETERS = "schema_too_many_parameters"
REFUSE_SCHEMA_DUPLICATE_PARAMETER = "schema_duplicate_parameter"
REFUSE_SCHEMA_BAD_RANGE = "schema_bad_range"
REFUSE_SCHEMA_RANGE_TOO_MANY_VALUES = "schema_range_too_many_values"
REFUSE_SCHEMA_ORDINALS_EXHAUSTED = "schema_ordinals_exhausted"
#: Тот же код, что у предпросмотров пересборки и автопринятия.
REFUSE_PREVIEW_CHANGED = "preview_changed"

#: Не больше десяти активных параметров на семью (спека 3в §2.2).
MAX_ACTIVE_PARAMETERS = 10
#: Номер параметра внутри версии — `1..100` (`ck_family_parameters_ordinal_range`).
MAX_ORDINAL = 100
#: Внутренних значений диапазона не больше двадцати восьми (спека 3в §2.2).
MAX_RANGE_INNER_VALUES = 28


# ---------------------------------------------------------------------------
#  Тело правки и сводка
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RangeSpec:
    """Диапазон параметра: от `start` до `stop` с шагом `step`."""

    start: Decimal
    stop: Decimal
    step: Decimal


@dataclass(frozen=True)
class ParameterDraft:
    """Параметр в теле правки. `ordinal = None` — новый параметр; `values` — для
    `exact`, `range` — для `range`."""

    ordinal: int | None
    name: str
    role: ParameterRole
    value_kind: ParameterValueKind
    closed: bool
    values: tuple[str, ...]
    range: RangeSpec | None


@dataclass(frozen=True)
class EditSummary:
    """Следствия правки (спека 3в §2.5). `asked_by_parameter` — `(номер, имя,
    число контекстов)`: о каких параметрах и у скольких контекстов сверка
    спросит модель; у нового параметра номер — тот, что он получит.
    `value_moves` и `retired` — переносы и выведенные значения перестройки
    (§2.6)."""

    variants_before: int
    variants_after_now: int
    variants_pending_answers: bool
    asked_by_parameter: tuple[tuple[int, str, int], ...]
    reserve_usd: Decimal
    expected_cached_usd: Decimal
    value_moves: tuple[tuple[str, str, str, int], ...]
    retired: tuple[tuple[str, str, int], ...]
    refreshing_contexts: int
    repriced_pending_jobs: int
    preview_hash: str


# ---------------------------------------------------------------------------
#  Состояние версии, прочитанное для плана
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _ParameterRow:
    id: int
    ordinal: int
    name: str
    name_norm: str
    role: str
    value_kind: str
    closed: bool
    range_from: Decimal | None
    range_to: Decimal | None
    range_step: Decimal | None
    origin: str
    archived: bool


@dataclass(frozen=True)
class _ValueRow:
    id: int
    parameter_id: int
    value: str
    value_norm: str
    merged: bool
    retired: bool

    @property
    def active(self) -> bool:
        return not self.merged and not self.retired


@dataclass(frozen=True)
class _RowState:
    """Строка значения контекста действующей версии."""

    context_id: int
    parameter_id: int
    ordinal: int
    value_id: int | None
    source: str
    off_list_text: str | None
    marked: bool
    context_live: bool


@dataclass(frozen=True)
class _State:
    family_id: int
    schema_id: int
    revision: int
    parameters: dict[int, _ParameterRow]
    """По `id`, архивные тоже."""
    values: dict[int, list[_ValueRow]]
    """Значения по `id` параметра, по возрастанию `id`."""
    rows: list[_RowState]
    context_ids: list[int]


def _read_state(db: Session, family_id: int, schema_id: int, revision: int) -> _State:
    parameters = {
        row.id: _ParameterRow(
            id=row.id, ordinal=row.ordinal, name=row.name, name_norm=row.name_norm,
            role=row.role, value_kind=row.value_kind, closed=row.closed,
            range_from=row.range_from, range_to=row.range_to, range_step=row.range_step,
            origin=row.origin, archived=row.archived_at is not None,
        )
        for row in db.execute(
            sa.select(
                FamilyParameter.id, FamilyParameter.ordinal, FamilyParameter.name,
                FamilyParameter.name_norm, FamilyParameter.role, FamilyParameter.value_kind,
                FamilyParameter.closed, FamilyParameter.range_from, FamilyParameter.range_to,
                FamilyParameter.range_step, FamilyParameter.origin, FamilyParameter.archived_at,
            )
            .where(FamilyParameter.schema_id == schema_id)
            .order_by(FamilyParameter.ordinal)
        ).all()
    }
    values: dict[int, list[_ValueRow]] = {parameter_id: [] for parameter_id in parameters}
    if parameters:
        for row in db.execute(
            sa.select(
                FamilyParameterValue.id, FamilyParameterValue.parameter_id,
                FamilyParameterValue.value, FamilyParameterValue.value_norm,
                FamilyParameterValue.merged_into_id, FamilyParameterValue.retired_at,
            )
            .where(FamilyParameterValue.parameter_id.in_(list(parameters)))
            .order_by(FamilyParameterValue.id)
        ).all():
            values[row.parameter_id].append(
                _ValueRow(
                    id=row.id, parameter_id=row.parameter_id, value=row.value,
                    value_norm=row.value_norm, merged=row.merged_into_id is not None,
                    retired=row.retired_at is not None,
                )
            )
    family_context = sa.func.coalesce(
        CatalogContext.pending_family_id, CatalogContext.work_family_id
    )
    context_ids = list(
        db.execute(
            sa.select(CatalogContext.id).where(family_context == family_id).order_by(CatalogContext.id)
        ).scalars()
    )
    rows = [
        _RowState(
            context_id=row.context_id, parameter_id=row.parameter_id, ordinal=row.ordinal,
            value_id=row.value_id, source=row.source, off_list_text=row.off_list_text,
            marked=row.marked, context_live=row.live,
        )
        for row in db.execute(
            sa.select(
                ContextParameterValue.context_id, ContextParameterValue.parameter_id,
                FamilyParameter.ordinal, ContextParameterValue.value_id,
                ContextParameterValue.source, ContextParameterValue.off_list_text,
                ContextParameterValue.refresh_requested_at.is_not(None).label("marked"),
                CatalogContext.archived_at.is_(None).label("live"),
            )
            .join(FamilyParameter, FamilyParameter.id == ContextParameterValue.parameter_id)
            .join(CatalogContext, CatalogContext.id == ContextParameterValue.context_id)
            .where(
                ContextParameterValue.schema_id == schema_id, family_context == family_id
            )
            .order_by(ContextParameterValue.context_id, FamilyParameter.ordinal)
        ).all()
    ]
    return _State(
        family_id=family_id, schema_id=schema_id, revision=revision, parameters=parameters,
        values=values, rows=rows, context_ids=context_ids,
    )


# ---------------------------------------------------------------------------
#  Проверки тела
# ---------------------------------------------------------------------------

#: Границы диапазона: по модулю меньше 10^9 и не больше шести знаков после запятой
#: (в колонке `numeric` и в подписях значений остаются точными).
_RANGE_LIMIT = Decimal(10) ** 9
_RANGE_MAX_FRACTION_DIGITS = 6


def _fraction_digits(number: Decimal) -> int:
    """Число знаков после запятой без хвостовых нулей — целочисленной арифметикой
    по `as_tuple()`: ни одной операции `Decimal`, которая округляла бы длинное
    число до точности контекста."""
    sign, digits, exponent = number.as_tuple()
    if not any(digits):
        return 0
    trailing_zeros = len(digits) - len(tuple(_strip_trailing_zeros(digits)))
    return max(0, -(exponent + trailing_zeros))


def _strip_trailing_zeros(digits: tuple[int, ...]) -> tuple[int, ...]:
    end = len(digits)
    while end > 0 and digits[end - 1] == 0:
        end -= 1
    return digits[:end]


def _is_range_number(number: Decimal) -> bool:
    """Конечное число, строго меньше 10^9 по модулю (сравнение `Decimal` точное), не
    более шести знаков после запятой."""
    if not number.is_finite() or not -_RANGE_LIMIT < number < _RANGE_LIMIT:
        return False
    return _fraction_digits(number) <= _RANGE_MAX_FRACTION_DIGITS


def _canonical(number: Decimal) -> Decimal:
    """Каноническая запись конечного числа: цифры без хвостовых нулей и показатель,
    сдвинутый на их число, — целочисленной арифметикой по `as_tuple()`; нуль (и
    `-0`) — `Decimal(0)`. Значение не меняется, а запись не зависит от того, как
    число написано: `0E-100000`, `1.` с двадцатью тысячами нулей и `-0` дают `0`,
    `1` и `0`. У числа, прошедшего `_is_range_number`, масштаб записи не больше
    шести."""
    sign, digits, exponent = number.as_tuple()
    significant = _strip_trailing_zeros(digits)
    if not significant:
        return Decimal(0)
    assert isinstance(exponent, int)
    return Decimal((sign, significant, exponent + len(digits) - len(significant)))


def _checked_range(spec: RangeSpec | None, name: str) -> RangeSpec:
    """Диапазон параметра: каждая граница и шаг — конечное число меньше 10^9 по
    модулю не более чем с шестью знаками после запятой. Возвращает диапазон из
    канонических записей (`_canonical`): дальше — в хэш, колонки, значения и
    подписи — уходят только они, а не запись тела, масштаб которой может быть
    больше, чем принимает `numeric`. При таких границах арифметика ниже точна в
    точности по умолчанию."""
    if spec is None:
        raise WorkFamilyError(
            REFUSE_SCHEMA_BAD_RANGE, f"параметр «{name}»: не заданы границы диапазона"
        )
    if not all(_is_range_number(n) for n in (spec.start, spec.stop, spec.step)):
        raise WorkFamilyError(
            REFUSE_SCHEMA_BAD_RANGE,
            f"параметр «{name}»: граница или шаг — не число меньше 10^9 с шестью знаками "
            "после запятой",
        )
    spec = RangeSpec(
        start=_canonical(spec.start), stop=_canonical(spec.stop), step=_canonical(spec.step)
    )
    if spec.start >= spec.stop:
        raise WorkFamilyError(
            REFUSE_SCHEMA_BAD_RANGE, f"параметр «{name}»: начало диапазона не меньше конца"
        )
    if spec.step <= 0:
        raise WorkFamilyError(
            REFUSE_SCHEMA_BAD_RANGE, f"параметр «{name}»: шаг диапазона не положителен"
        )
    span = spec.stop - spec.start
    if span % spec.step != 0:
        raise WorkFamilyError(
            REFUSE_SCHEMA_BAD_RANGE,
            f"параметр «{name}»: диапазон не делится на целое число шагов",
        )
    if span / spec.step > MAX_RANGE_INNER_VALUES:
        raise WorkFamilyError(
            REFUSE_SCHEMA_RANGE_TOO_MANY_VALUES,
            f"параметр «{name}»: больше {MAX_RANGE_INNER_VALUES} внутренних значений диапазона",
        )
    return spec


def _checked_drafts(drafts: Sequence[ParameterDraft]) -> list[ParameterDraft]:
    """Проверки тела, не требующие версии: пустые имена и значения, дубли имён,
    предел активных параметров, диапазоны. Возвращает черновики с обрезанными
    именами; `closed` диапазона — всегда `True` (спека 3в §2.5); диапазон — в
    канонической записи (`_checked_range`), у точного параметра — `None`
    (границы в его теле не проверяются и никуда не уходят)."""
    checked: list[ParameterDraft] = []
    for draft in drafts:
        if not normalize_value(draft.name):
            raise WorkFamilyError(REFUSE_SCHEMA_BLANK, "имя параметра пусто")
        if draft.value_kind == ParameterValueKind.exact and any(
            not normalize_value(value) for value in draft.values
        ):
            raise WorkFamilyError(
                REFUSE_SCHEMA_BLANK, f"значение параметра «{draft.name.strip()}» пусто"
            )
        checked.append(
            ParameterDraft(
                ordinal=draft.ordinal, name=draft.name.strip(), role=draft.role,
                value_kind=draft.value_kind,
                closed=True if draft.value_kind == ParameterValueKind.range else draft.closed,
                values=tuple(draft.values), range=draft.range,
            )
        )
    names = [normalize_value(draft.name) for draft in checked]
    if len(set(names)) != len(names):
        raise WorkFamilyError(
            REFUSE_SCHEMA_DUPLICATE_PARAMETER, "в теле два параметра с одним именем"
        )
    if len(checked) > MAX_ACTIVE_PARAMETERS:
        raise WorkFamilyError(
            REFUSE_SCHEMA_TOO_MANY_PARAMETERS,
            f"активных параметров не больше {MAX_ACTIVE_PARAMETERS}: в теле {len(checked)}",
        )
    return [
        replace(
            draft,
            range=_checked_range(draft.range, draft.name)
            if draft.value_kind == ParameterValueKind.range
            else None,
        )
        for draft in checked
    ]


# ---------------------------------------------------------------------------
#  План
# ---------------------------------------------------------------------------

@dataclass
class _NewValue:
    text: str
    norm: str
    lo: Decimal | None = None
    hi: Decimal | None = None
    #: Выведенное значение с этой нормой возвращается из истории.
    revive_id: int | None = None
    #: Значение, которое строит система (диапазон), — события `value_added` нет.
    built: bool = False


@dataclass
class _ParameterPlan:
    ordinal: int
    #: `None` — параметр заводится этой правкой.
    parameter_id: int | None
    draft: ParameterDraft
    existing: _ParameterRow | None
    rename_to: str | None = None
    role_to: str | None = None
    closed_to: bool | None = None
    value_renames: list[tuple[int, str]] = field(default_factory=list)
    new_values: list[_NewValue] = field(default_factory=list)

    @property
    def touched(self) -> bool:
        return bool(
            self.rename_to is not None or self.role_to is not None
            or self.closed_to is not None or self.value_renames or self.new_values
        )


@dataclass
class _RowPlan:
    """Что правка делает со строками значений параметра при добавлении
    значений точного списка (спека 3в §2.5, §2.7)."""

    parameter_id: int
    ordinal: int
    #: `{норма значения: id существующего}`, ссылка на новое — `None` (создаётся).
    resolve: list[tuple[int, str]] = field(default_factory=list)
    """`(context_id, норма значения)` строк `off_list` с текстом той же нормы."""
    mark: list[int] = field(default_factory=list)
    """Контексты строк `none` и `off_list`, которые станут «уточняется»."""


@dataclass
class _EditPlan:
    state: _State
    parameters: list[_ParameterPlan]
    archived: list[_ParameterRow]
    row_plans: list[_RowPlan]
    #: Контексты, чьи строки правка меняет (отметка и значение).
    touched_contexts: list[int]
    recompute_needed: bool
    summary: EditSummary
    changes: list[dict[str, Any]]


def _norm_decimal(number: Decimal) -> str:
    return format(number.normalize(), "f")


def _same_range(row: _ParameterRow, spec: RangeSpec) -> bool:
    return (
        row.range_from is not None
        and row.range_from == spec.start
        and row.range_to == spec.stop
        and row.range_step == spec.step
    )


def _plan_parameter(
    state: _State, draft: ParameterDraft, active_by_ordinal: dict[int, _ParameterRow]
) -> _ParameterPlan:
    """Что правка делает с одним параметром тела: проверки существующего и
    разность с ним."""
    if draft.ordinal is None:
        plan = _ParameterPlan(ordinal=0, parameter_id=None, draft=draft, existing=None)
        if draft.value_kind == ParameterValueKind.range:
            assert draft.range is not None
            for built in build_range_values(draft.range.start, draft.range.stop, draft.range.step):
                plan.new_values.append(
                    _NewValue(
                        text=built.label, norm=normalize_value(built.label), lo=built.lo,
                        hi=built.hi, built=True,
                    )
                )
        else:
            seen: set[str] = set()
            for text in draft.values:
                norm = normalize_value(text)
                if norm not in seen:
                    seen.add(norm)
                    plan.new_values.append(_NewValue(text=text.strip(), norm=norm))
        return plan

    existing = active_by_ordinal[draft.ordinal]
    if normalize_value(draft.name) != existing.name_norm:
        raise WorkFamilyError(
            REFUSE_SCHEMA_PARAMETER_RENAMED,
            f"параметр {draft.ordinal}: смысловое переименование — новый параметр, "
            "а не правка имени",
            ordinal=draft.ordinal,
        )
    plan = _ParameterPlan(
        ordinal=existing.ordinal, parameter_id=existing.id, draft=draft, existing=existing
    )
    if draft.name != existing.name:
        plan.rename_to = draft.name
    if draft.role.value != existing.role:
        plan.role_to = draft.role.value
    if draft.value_kind.value != existing.value_kind:
        raise NotImplementedError("перестройка значений параметра — спека 3в §2.6")
    if existing.value_kind == ParameterValueKind.range.value:
        assert draft.range is not None
        if not _same_range(existing, draft.range):
            raise NotImplementedError("перестройка значений параметра — спека 3в §2.6")
        return plan

    if draft.closed != existing.closed:
        plan.closed_to = draft.closed
    values = state.values[existing.id]
    by_norm = {value.value_norm: value for value in values}
    named: dict[str, str] = {}
    for text in draft.values:
        named.setdefault(normalize_value(text), text.strip())
    removed = [value for value in values if value.active and value.value_norm not in named]
    if removed:
        raise WorkFamilyError(
            REFUSE_SCHEMA_VALUE_REMOVED,
            f"параметр {draft.ordinal}: значение удалить нельзя — только слить с другим",
            ordinal=draft.ordinal,
        )
    for norm, text in named.items():
        found = by_norm.get(norm)
        if found is None:
            plan.new_values.append(_NewValue(text=text, norm=norm))
        elif found.merged:
            continue  # слитый синоним назван заново: каноническое значение уже в списке
        elif found.retired:
            plan.new_values.append(_NewValue(text=text, norm=norm, revive_id=found.id))
        elif found.value != text:
            plan.value_renames.append((found.id, text))
    return plan


def _plan_rows(state: _State, plans: list[_ParameterPlan]) -> list[_RowPlan]:
    """Строки значений существующих точных параметров, которым правка добавила
    значение: `off_list` с текстом той же нормы получает значение сразу, остальные
    `none` и `off_list` — «уточняется» (спека 3в §2.5, §2.7)."""
    result: list[_RowPlan] = []
    for plan in plans:
        if plan.existing is None or not plan.new_values:
            continue
        added = {value.norm for value in plan.new_values}
        row_plan = _RowPlan(parameter_id=plan.existing.id, ordinal=plan.existing.ordinal)
        for row in state.rows:
            if row.parameter_id != plan.existing.id or not row.context_live:
                continue
            if row.source == ValueSource.off_list.value and row.off_list_text is not None:
                norm = normalize_value(row.off_list_text)
                if norm in added:
                    row_plan.resolve.append((row.context_id, norm))
                    continue
            if (
                row.source in (ValueSource.none.value, ValueSource.off_list.value)
                and not row.marked
            ):
                row_plan.mark.append(row.context_id)
        if row_plan.resolve or row_plan.mark:
            result.append(row_plan)
    return result


def _tariffs() -> dict[str, str]:
    tariffs = tariffs_from(settings, SemanticJobKind.context_values)
    return {
        "input_per_m": str(tariffs.input_per_m),
        "cache_write_per_m": str(tariffs.cache_write_per_m),
        "cache_read_per_m": str(tariffs.cache_read_per_m),
        "output_per_m": str(tariffs.output_per_m),
    }


def _body_canon(drafts: Sequence[ParameterDraft]) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": draft.ordinal,
            "name": draft.name,
            "name_norm": normalize_value(draft.name),
            "role": draft.role.value,
            "value_kind": draft.value_kind.value,
            "closed": draft.closed,
            "values": sorted(
                (normalize_value(value), value.strip()) for value in draft.values
            ),
            "range": None
            if draft.range is None
            else [_norm_decimal(n) for n in (draft.range.start, draft.range.stop, draft.range.step)],
        }
        for draft in drafts
    ]


def _hash_of(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_plan(
    db: Session, state: _State, family_id: int, drafts: Sequence[ParameterDraft]
) -> _EditPlan:
    checked = _checked_drafts(drafts)
    active_by_ordinal = {p.ordinal: p for p in state.parameters.values() if not p.archived}
    ordinals = [draft.ordinal for draft in checked if draft.ordinal is not None]
    if len(set(ordinals)) != len(ordinals) or any(o not in active_by_ordinal for o in ordinals):
        raise WorkFamilyError(
            REFUSE_SCHEMA_BAD_ORDINALS,
            f"номера параметров {sorted(ordinals)}: нужны различные номера активных "
            "параметров версии",
        )
    new_count = sum(1 for draft in checked if draft.ordinal is None)
    top = max((p.ordinal for p in state.parameters.values()), default=0)
    if top + new_count > MAX_ORDINAL:
        raise WorkFamilyError(
            REFUSE_SCHEMA_ORDINALS_EXHAUSTED,
            f"номера параметров версии исчерпаны ({top} из {MAX_ORDINAL}): "
            "пересоберите схему",
        )

    plans = [_plan_parameter(state, draft, active_by_ordinal) for draft in checked]
    next_ordinal = top
    for plan in plans:
        if plan.parameter_id is None:
            next_ordinal += 1
            plan.ordinal = next_ordinal
    kept = {plan.parameter_id for plan in plans if plan.parameter_id is not None}
    archived = [
        p for p in sorted(active_by_ordinal.values(), key=lambda p: p.ordinal) if p.id not in kept
    ]
    row_plans = _plan_rows(state, plans)

    # Строки значений после правки: `{context_id: {ordinal: помечена ли}}`.
    rows_after: dict[int, dict[int, bool]] = {}
    for row in state.rows:
        rows_after.setdefault(row.context_id, {})[row.ordinal] = row.marked
    for row_plan in row_plans:
        for context_id, _norm in row_plan.resolve:
            rows_after[context_id][row_plan.ordinal] = False
        for context_id in row_plan.mark:
            rows_after[context_id][row_plan.ordinal] = True
    touched_contexts = sorted(
        {c for rp in row_plans for c, _n in rp.resolve} | {c for rp in row_plans for c in rp.mark}
    )

    # Активные параметры после правки — в том виде, в каком их увидит запрос.
    parameters_after: list[SchemaParameterIn] = []
    for plan in sorted(plans, key=lambda p: p.ordinal):
        if plan.existing is not None:
            renamed = dict(plan.value_renames)
            ordered = [
                (value.id, renamed.get(value.id, value.value))
                for value in state.values[plan.existing.id]
                if value.active
            ] + [(v.revive_id, v.text) for v in plan.new_values if v.revive_id is not None]
            texts = [text for _id, text in sorted(ordered)]
            texts += [v.text for v in plan.new_values if v.revive_id is None]
            kind = plan.existing.value_kind
        else:
            texts = [v.text for v in plan.new_values]
            kind = plan.draft.value_kind.value
        parameters_after.append(
            SchemaParameterIn(
                ordinal=plan.ordinal, name=plan.draft.name, values=tuple(texts), value_kind=kind
            )
        )

    values_plan = preview_values_plan(
        db, state.context_ids, parameters=parameters_after, rows=rows_after
    )
    name_of = {p.ordinal: p.name for p in parameters_after}
    asked: dict[int, int] = {}
    for _fingerprint, _rendered, ordinals_asked, free in values_plan.entries:
        if free:
            continue
        for ordinal in ordinals_asked:
            asked[ordinal] = asked.get(ordinal, 0) + 1
    reserve, cached = _estimate_entries(
        db,
        [(fingerprint, rendered) for fingerprint, rendered, _o, _free in values_plan.entries],
        {fingerprint for fingerprint, _r, _o, free in values_plan.entries if free},
    )

    main_after = {plan.ordinal for plan in plans if plan.draft.role == ParameterRole.main}
    main_before = {
        p.ordinal for p in state.parameters.values() if not p.archived and p.role == "main"
    }
    resolved_main = any(rp.resolve and rp.ordinal in main_after for rp in row_plans)
    recompute_needed = main_after != main_before or resolved_main

    # Пересчёт вариантов: строки главных параметров после правки. У нового
    # главного параметра строк нет — контексты ждут ответа; значение, которое
    # правка создаёт и сразу ставит строке, ещё без `id`, и ему выдаётся
    # отрицательный номер: ключ с ним не совпадёт ни с одним существующим.
    plan_of_parameter = {p.parameter_id: p for p in plans if p.parameter_id is not None}
    ordinal_of_parameter = {
        parameter_id: p.ordinal
        for parameter_id, p in plan_of_parameter.items()
        if p.ordinal in main_after
    }
    previous_of = recompute_candidates(db, family_id=family_id, schema_id=state.schema_id)
    answered: dict[int, dict[int, int | None]] = {
        context_id: {ordinal_of_parameter[pid]: vid for pid, vid in rows.items()}
        for context_id, rows in answered_main_rows(
            db, list(previous_of), list(ordinal_of_parameter)
        ).items()
    }
    placeholder = -1
    for row_plan in row_plans:
        if row_plan.ordinal not in main_after:
            continue
        added = {v.norm: v for v in plan_of_parameter[row_plan.parameter_id].new_values}
        for context_id, norm in row_plan.resolve:
            if context_id not in answered:
                continue
            value_id = added[norm].revive_id
            if value_id is None:
                value_id = placeholder
                placeholder -= 1
            answered[context_id][row_plan.ordinal] = value_id
    projected = project_recompute(
        db, schema_id=state.schema_id, previous_of=previous_of, main_ordinals=main_after,
        answered=answered,
    )
    variants_before = projected.variants_before
    variants_after = projected.variants_after
    waiting = projected.waiting_contexts

    marked = _marked_contexts(row_plans)
    summary_numbers = {
        "variants_before": variants_before,
        "variants_after_now": variants_after,
        "variants_pending_answers": waiting > 0,
        "asked": sorted(asked.items()),
        "reserve": str(reserve),
        "cached": str(cached),
        "refreshing": len(marked),
        "repriced": len(values_plan.cancelled),
    }
    # Строки каждого параметра, которого правка касается (имя, роль, значения,
    # «только из списка», архивирование): их смена между вызовами меняет хэш,
    # даже когда числа сводки те же.
    affected = {p.parameter_id for p in plans if p.touched and p.parameter_id is not None}
    affected |= {p.id for p in archived}
    digest_rows = sorted(
        (row.context_id, row.parameter_id, row.value_id, row.source, row.marked)
        for row in state.rows
        if row.parameter_id in affected
    )
    preview_hash = _hash_of(
        {
            "family_id": family_id,
            "schema_id": state.schema_id,
            "revision": state.revision,
            "body": _body_canon(checked),
            "summary": summary_numbers,
            "rows": digest_rows,
            "jobs": sorted(
                (fp.context_id, fp.request_hash, list(asked_o), free)
                for fp, _r, asked_o, free in values_plan.entries
            ),
            "tariffs": _tariffs(),
            "reserve_formula_version": RESERVE_FORMULA_VERSION,
        }
    )
    summary = EditSummary(
        variants_before=variants_before,
        variants_after_now=variants_after,
        variants_pending_answers=waiting > 0,
        asked_by_parameter=tuple(
            (ordinal, name_of[ordinal], count) for ordinal, count in sorted(asked.items())
        ),
        reserve_usd=reserve,
        expected_cached_usd=cached,
        value_moves=(),
        retired=(),
        refreshing_contexts=len(marked),
        repriced_pending_jobs=len(values_plan.cancelled),
        preview_hash=preview_hash,
    )
    return _EditPlan(
        state=state, parameters=plans, archived=archived, row_plans=row_plans,
        touched_contexts=touched_contexts, recompute_needed=recompute_needed,
        summary=summary, changes=_changes_of(plans, archived),
    )


def _marked_contexts(row_plans: Sequence[_RowPlan]) -> set[int]:
    """Контексты, чьи строки правка помечает «уточняется»."""
    return {context_id for row_plan in row_plans for context_id in row_plan.mark}


def _changes_of(
    plans: Sequence[_ParameterPlan], archived: Sequence[_ParameterRow]
) -> list[dict[str, Any]]:
    """Перечень изменений по параметрам для события (спека 3в §2.5)."""
    changes: list[dict[str, Any]] = []
    for plan in plans:
        if plan.parameter_id is None:
            change: dict[str, Any] = {
                "ordinal": plan.ordinal, "name": plan.draft.name, "added": True,
                "role": plan.draft.role.value, "value_kind": plan.draft.value_kind.value,
                "closed": plan.draft.closed,
            }
            if plan.draft.range is not None:
                change["range"] = {
                    "start": _norm_decimal(plan.draft.range.start),
                    "stop": _norm_decimal(plan.draft.range.stop),
                    "step": _norm_decimal(plan.draft.range.step),
                }
            else:
                change["values_added"] = [value.text for value in plan.new_values]
            changes.append(change)
            continue
        if not plan.touched:
            continue
        change = {"ordinal": plan.ordinal, "name": plan.draft.name}
        if plan.rename_to is not None:
            change["renamed"] = {"from": plan.existing.name, "to": plan.rename_to}
        if plan.role_to is not None:
            change["role"] = {"from": plan.existing.role, "to": plan.role_to}
        if plan.closed_to is not None:
            change["closed"] = {"from": plan.existing.closed, "to": plan.closed_to}
        if plan.value_renames:
            change["values_renamed"] = [text for _id, text in plan.value_renames]
        if plan.new_values:
            change["values_added"] = [
                value.text for value in plan.new_values if value.revive_id is None
            ]
            revived = [value.text for value in plan.new_values if value.revive_id is not None]
            if revived:
                change["values_revived"] = revived
        changes.append(change)
    for row in archived:
        changes.append({"ordinal": row.ordinal, "name": row.name, "archived": True})
    return changes


# ---------------------------------------------------------------------------
#  Вход: предпросмотр и применение
# ---------------------------------------------------------------------------

def _require_editable(db: Session, family_id: int, *, lock: bool) -> int:
    """Семья активна, пересборки нет, действующая версия есть; возвращает её
    `id`. С `lock` — семья `FOR UPDATE` первой и версия `FOR UPDATE` следом."""
    if lock:
        _lock_family_for_schema_life(db, family_id, require_active=True)
    else:
        status = db.execute(
            sa.select(WorkFamily.status).where(WorkFamily.id == family_id)
        ).scalar_one_or_none()
        if status is None:
            raise WorkFamilyError(
                REFUSE_FAMILY_NOT_FOUND, f"семья {family_id} не найдена", family_id=family_id
            )
        if status != "active":
            raise WorkFamilyError(
                REFUSE_FAMILY_NOT_ACTIVE, f"семья {family_id} не активна", family_id=family_id
            )
    if db.execute(
        sa.select(FamilyParameterSchema.id)
        .where(
            FamilyParameterSchema.family_id == family_id,
            FamilyParameterSchema.status == SchemaStatus.building.value,
        )
        .limit(1)
    ).first() is not None:
        raise WorkFamilyError(
            REFUSE_SCHEMA_BUILDING,
            f"у семьи {family_id} идёт пересборка схемы: сначала отмените её",
            family_id=family_id,
        )
    if lock:
        current = db.execute(_lock_current_schema_statement(family_id)).scalar_one_or_none()
        schema_id = None if current is None else current.id
    else:
        schema_id = db.execute(
            sa.select(FamilyParameterSchema.id).where(
                FamilyParameterSchema.family_id == family_id,
                FamilyParameterSchema.status == SchemaStatus.frozen.value,
            )
        ).scalar_one_or_none()
    if schema_id is None:
        raise WorkFamilyError(
            REFUSE_SCHEMA_NO_CURRENT, f"у семьи {family_id} нет текущей схемы", family_id=family_id
        )
    return schema_id


def _read_plan(
    db: Session, *, family_id: int, schema_id: int, drafts: Sequence[ParameterDraft]
) -> _EditPlan:
    # `revision` читается колонкой, а не сущностью: сущность могла остаться в
    # сессии со старым значением, колонка после замка — свежее.
    revision = db.execute(
        sa.select(FamilyParameterSchema.revision).where(FamilyParameterSchema.id == schema_id)
    ).scalar_one()
    state = _read_state(db, family_id, schema_id, revision)
    return _build_plan(db, state, family_id, drafts)


def preview_schema_edit(
    db: Session, *, family_id: int, drafts: Sequence[ParameterDraft]
) -> EditSummary:
    """Следствия правки и `preview_hash` — без блокировок и без записи.

    Raises:
        WorkFamilyError: семьи нет или она не активна; у семьи пересборка
            (`schema_building`) или нет текущей версии; любая проверка тела.
    """
    schema_id = _require_editable(db, family_id, lock=False)
    return _read_plan(db, family_id=family_id, schema_id=schema_id, drafts=drafts).summary


def apply_schema_edit(
    db: Session, *, family_id: int, drafts: Sequence[ParameterDraft], preview_hash: str,
    actor_id: int,
) -> EditSummary:
    """Правка схемы на месте (спека 3в §2.5). Семья и действующая версия
    берутся `FOR UPDATE`, `revision` и состояние перечитываются под замками,
    план строится заново и сверяется с `preview_hash`; задания правки ставятся
    без потолка события (стоимость человек видел).

    Raises:
        WorkFamilyError: проверки тела и состояния (см. `preview_schema_edit`);
            `preview_changed` — вход предпросмотра изменился.
    """
    schema_id = _require_editable(db, family_id, lock=True)
    plan = _read_plan(db, family_id=family_id, schema_id=schema_id, drafts=drafts)
    if plan.summary.preview_hash != preview_hash:
        raise WorkFamilyError(
            REFUSE_PREVIEW_CHANGED, "схема или очередь изменились после предпросмотра",
            family_id=family_id,
        )
    if not plan.changes and not plan.row_plans:
        return plan.summary

    _write_plan(db, plan, family_id=family_id, schema_id=schema_id, actor_id=actor_id)
    return plan.summary


def _lock_parameters_statement(schema_id: int):
    return (
        sa.select(FamilyParameter.id)
        .where(FamilyParameter.schema_id == schema_id)
        .order_by(FamilyParameter.id)
        .with_for_update()
    )


def _lock_values_of_statement(parameter_ids: Sequence[int]):
    return (
        sa.select(FamilyParameterValue.id)
        .where(FamilyParameterValue.parameter_id.in_(list(parameter_ids)))
        .order_by(FamilyParameterValue.id)
        .with_for_update()
    )


def _write_plan(
    db: Session, plan: _EditPlan, *, family_id: int, schema_id: int, actor_id: int
) -> None:
    now = datetime.now(UTC)
    db.execute(_lock_parameters_statement(schema_id)).all()
    touched_exact = [
        p.parameter_id for p in plan.parameters
        if p.parameter_id is not None and (p.new_values or p.value_renames)
    ]
    if touched_exact:
        db.execute(_lock_values_of_statement(touched_exact)).all()

    added_events: list[tuple[int, int, str]] = []
    value_ids: dict[tuple[int, str], int] = {}
    for item in plan.parameters:
        if item.parameter_id is None:
            row = FamilyParameter(
                schema_id=schema_id, ordinal=item.ordinal, name=item.draft.name,
                name_norm=normalize_value(item.draft.name), role=item.draft.role.value,
                value_kind=item.draft.value_kind.value, closed=item.draft.closed,
                range_from=item.draft.range.start if item.draft.range else None,
                range_to=item.draft.range.stop if item.draft.range else None,
                range_step=item.draft.range.step if item.draft.range else None,
                origin=ParameterOrigin.human.value,
            )
            db.add(row)
            db.flush()
            parameter_id = row.id
        else:
            parameter_id = item.parameter_id
            if item.touched:
                values: dict[str, Any] = {}
                if item.rename_to is not None:
                    values["name"] = item.rename_to
                if item.role_to is not None:
                    values["role"] = item.role_to
                if item.closed_to is not None:
                    values["closed"] = item.closed_to
                if item.existing.origin == ParameterOrigin.model.value:
                    values["origin"] = ParameterOrigin.human.value
                db.execute(
                    sa.update(FamilyParameter)
                    .where(FamilyParameter.id == parameter_id)
                    .values(**values)
                    .execution_options(synchronize_session=False)
                )
        for value_id, text in item.value_renames:
            db.execute(
                sa.update(FamilyParameterValue)
                .where(FamilyParameterValue.id == value_id)
                .values(value=text)
                .execution_options(synchronize_session=False)
            )
        for new_value in item.new_values:
            if new_value.revive_id is not None:
                db.execute(
                    sa.update(FamilyParameterValue)
                    .where(FamilyParameterValue.id == new_value.revive_id)
                    .values(retired_at=None, value=new_value.text)
                    .execution_options(synchronize_session=False)
                )
                value_id = new_value.revive_id
            else:
                created = FamilyParameterValue(
                    parameter_id=parameter_id, value=new_value.text, value_norm=new_value.norm,
                    origin=ValueOrigin.manual.value, range_lo=new_value.lo, range_hi=new_value.hi,
                )
                db.add(created)
                db.flush()
                value_id = created.id
            value_ids[(parameter_id, new_value.norm)] = value_id
            if not new_value.built:
                added_events.append((parameter_id, value_id, new_value.text))
    for row in plan.archived:
        db.execute(
            sa.update(FamilyParameter)
            .where(FamilyParameter.id == row.id)
            .values(archived_at=now)
            .execution_options(synchronize_session=False)
        )
    db.flush()

    # Строки значений: контексты по `id`, затем отметка и значение.
    if plan.touched_contexts:
        db.execute(_lock_contexts_statement(plan.touched_contexts)).all()
    for row_plan in plan.row_plans:
        for context_id, norm in row_plan.resolve:
            db.execute(
                sa.update(ContextParameterValue)
                .where(
                    ContextParameterValue.context_id == context_id,
                    ContextParameterValue.parameter_id == row_plan.parameter_id,
                )
                .values(
                    value_id=value_ids[(row_plan.parameter_id, norm)],
                    source=ValueSource.manual.value, off_list_text=None,
                    refresh_requested_at=None,
                )
                .execution_options(synchronize_session=False)
            )
        if row_plan.mark:
            mark_rows_for_refresh(db, row_plan.mark, row_plan.parameter_id)
    db.flush()

    variants_after = plan.summary.variants_after_now
    if plan.recompute_needed:
        variants_after = recompute_variants(
            db, family_id=family_id, schema_id=schema_id
        ).variants_after
    revision = bump_revision(db, schema_id)
    report = reconcile_context_values(
        db, plan.state.context_ids, cap=NO_CAP, source="operation"
    )
    for parameter_id, value_id, text in added_events:
        record_event(
            db, event_type="family_schema_value_added", family_id=family_id, actor_id=actor_id,
            payload={
                "parameter_id": parameter_id, "value_id": value_id, "value": text,
                "origin": ValueOrigin.manual.value, "context_id": None,
            },
        )
    record_event(
        db, event_type="family_schema_edited", family_id=family_id, actor_id=actor_id,
        payload={
            "schema_id": schema_id, "revision": revision, "changes": plan.changes,
            "variants_before": plan.summary.variants_before, "variants_after": variants_after,
            "jobs_enqueued": report.created + report.revived,
        },
    )


__all__: Sequence[str] = (
    "MAX_ACTIVE_PARAMETERS",
    "EditSummary",
    "ParameterDraft",
    "RangeSpec",
    "apply_schema_edit",
    "preview_schema_edit",
)
