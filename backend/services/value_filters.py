"""Два фильтра перед записью нового значения главного параметра и разбор ответа
`several` (спека упрощения каталога §2.4).

Порядок для текста, который модель назвала новым значением открытого параметра:
точное совпадение `value_norm` — значение ставится, фильтры не применяются; затем
фильтр 1 (склейка → составное: все части текста уже названы значениями параметра),
затем фильтр 2 (дубль → существующее значение по лемматизированной норме); иначе
значение заводится новым и пишется в «Журнал ИИ» (`value_added`). У закрытого
параметра фильтров нет: точная норма действующего значения либо `off_list`.

Функции пишут в базу (значения, журнал, составные) и ничего не коммитят:
транзакцией владеет вызывающий. Деление текста и сопоставление имён внутри одного
ответа о схеме — чистые функции без базы.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from models import (
    CatalogAutoAction,
    FamilyParameter,
    FamilyParameterSchema,
    FamilyParameterValue,
)
from parser.sanitize_text import normalize_job_title_with_lemmatization
from services.ai_journal import record_action
from services.composite_values import COMPOSITE_JOIN, composite_parts, find_or_create_composite
from services.human_decisions import is_not_duplicate
from services.work_variants import _extend_with_value, canonical_value_id, normalize_value

logger = logging.getLogger(__name__)

# Разделитель — запятая, точка с запятой, косая черта либо слово «и»/«или» между
# пробелами. Запятая и косая черта между цифрами (`3,6 м`, `В12,5`, `1/2"`) —
# часть значения, а не разделитель.
_SEPARATOR_RE = re.compile(r"\s+(?P<word>или|и)\s+|(?P<mark>[,;/])", re.IGNORECASE)

ResolvedAction = Literal["existing", "composite", "equated", "added", "off_list", "none"]


@dataclass(frozen=True)
class SplitCandidate:
    """Текст, разрезанный на части: `all` — части вместе, `any` — одна на выбор."""

    kind: Literal["all", "any"]
    parts: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedValue:
    """Итог разбора текста или ответа `several`.

    `value_id` — значение для строки контекста (`None` у `off_list` и `none`);
    `added_ids` — простые значения, заведённые этим вызовом (расширение списка);
    `text` — текст для строки `off_list`."""

    value_id: int | None
    action: ResolvedAction
    added_ids: tuple[int, ...] = ()
    text: str | None = None


def split_candidate(text: str) -> SplitCandidate | None:
    """Режет текст по `,`, `;`, `/`, « и », « или ». Вид `any` — только когда все
    разделители текста — « или »; смесь « или » с другими разделителями даёт `None`
    (текст остаётся целым). Пустые после обрезки пробелов части отбрасываются; частей
    меньше двух — `None`."""
    pieces: list[str] = []
    separators: list[bool] = []  # True — «или»
    cursor = 0
    for match in _SEPARATOR_RE.finditer(text):
        mark = match.group("mark")
        if (
            mark in (",", "/")
            and match.start() > 0
            and match.end() < len(text)
            and text[match.start() - 1].isdigit()
            and text[match.end()].isdigit()
        ):
            continue
        pieces.append(text[cursor:match.start()])
        separators.append((match.group("word") or "").casefold() == "или")
        cursor = match.end()
    pieces.append(text[cursor:])
    parts = tuple(piece.strip() for piece in pieces if piece.strip())
    if len(parts) < 2:
        return None
    if all(separators):
        return SplitCandidate(kind="any", parts=parts)
    if any(separators):
        return None
    return SplitCandidate(kind="all", parts=parts)


@lru_cache(maxsize=8192)
def lemma_norm(text: str) -> str:
    """Лемматизированная норма текста (`normalize_job_title_with_lemmatization`);
    пустая строка, если нормализация ничего не оставила."""
    return normalize_job_title_with_lemmatization(text) or ""


def assembled_text(combine: str, texts: Sequence[str]) -> str:
    """Текст значений `several`, собранный из элементов: « + » для `all`, « или »
    для `any`."""
    return COMPOSITE_JOIN[combine].join(text.strip() for text in texts)


# ---------------------------------------------------------------------------
#  Сопоставление имён внутри одного ответа о схеме (чистое)
# ---------------------------------------------------------------------------

def glue_in_answer(texts: Sequence[str]) -> dict[int, tuple[Literal["all", "any"], tuple[int, ...]]]:
    """Склейки среди значений ответа о схеме: `{индекс значения: (вид, индексы
    частей)}`. Значение — склейка, если `split_candidate` режет его на части и
    КАЖДАЯ часть названа другим значением этого же ответа, которое само не режется
    (по норме текста или по лемме). Различных частей меньше двух — не склейка."""
    norms = [normalize_value(text) for text in texts]
    splits = [split_candidate(text) for text in texts]
    targets = [index for index, split in enumerate(splits) if split is None]
    lemmas = {index: lemma_norm(texts[index]) for index in targets}
    glue: dict[int, tuple[Literal["all", "any"], tuple[int, ...]]] = {}
    for index, split in enumerate(splits):
        if split is None:
            continue
        found: set[int] = set()
        for part in split.parts:
            part_norm = normalize_value(part)
            exact = [target for target in targets if norms[target] == part_norm]
            if exact:
                found.add(exact[0])
                continue
            part_lemma = lemma_norm(part)
            by_lemma = [target for target in targets if part_lemma and lemmas[target] == part_lemma]
            if len(by_lemma) != 1:
                break
            found.add(by_lemma[0])
        else:
            if len(found) >= 2:
                glue[index] = (split.kind, tuple(sorted(found)))
    return glue


# ---------------------------------------------------------------------------
#  Запись значения
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Scope:
    schema_id: int
    family_id: int
    closed: bool


@dataclass(frozen=True)
class _Exact:
    """Строка параметра с точной нормой, слитая — до канонической."""

    value_id: int
    retired: bool
    composite: bool


@dataclass(frozen=True)
class _Glue:
    """Склейка, разрезанная фильтром 1: все части уже названы значениями параметра
    (различных не меньше двух); составное из них ещё не создано."""

    kind: Literal["all", "any"]
    value_ids: tuple[int, ...]


def _scope(db: Session, parameter_id: int) -> _Scope:
    row = db.execute(
        sa.select(FamilyParameter.schema_id, FamilyParameter.closed, FamilyParameterSchema.family_id)
        .join(FamilyParameterSchema, FamilyParameterSchema.id == FamilyParameter.schema_id)
        .where(FamilyParameter.id == parameter_id)
    ).one()
    return _Scope(schema_id=row.schema_id, family_id=row.family_id, closed=row.closed)


def _exact(db: Session, parameter_id: int, norm: str) -> _Exact | None:
    """Строка параметра с точной нормой; `None` — такой нормы в параметре нет."""
    found = db.execute(
        sa.select(FamilyParameterValue.id).where(
            FamilyParameterValue.parameter_id == parameter_id,
            FamilyParameterValue.value_norm == norm,
        )
    ).scalar_one_or_none()
    if found is None:
        return None
    canonical = canonical_value_id(db, found)
    row = db.execute(
        sa.select(FamilyParameterValue.retired_at, FamilyParameterValue.composite_kind).where(
            FamilyParameterValue.id == canonical
        )
    ).one()
    return _Exact(canonical, retired=row.retired_at is not None, composite=row.composite_kind is not None)


def _active_simple(db: Session, parameter_id: int) -> list[sa.Row]:
    """Действующие простые значения параметра: `id`, `value`."""
    return list(
        db.execute(
            sa.select(FamilyParameterValue.id, FamilyParameterValue.value)
            .where(
                FamilyParameterValue.parameter_id == parameter_id,
                FamilyParameterValue.merged_into_id.is_(None),
                FamilyParameterValue.retired_at.is_(None),
                FamilyParameterValue.composite_kind.is_(None),
            )
            .order_by(FamilyParameterValue.id)
        ).all()
    )


def _lemma_match(
    db: Session, parameter_id: int, text: str, simples: Sequence[sa.Row]
) -> int | None:
    """Единственное действующее простое значение с той же леммой, что у `text`, если
    пара не помечена «не дубль». Совпадения считаются ДО исключения помеченной пары:
    два и больше совпадений, как и единственное помеченное, дают `None`."""
    lemma = lemma_norm(text)
    if not lemma:
        return None
    hits = [row.id for row in simples if lemma_norm(row.value) == lemma]
    if len(hits) != 1:
        return None
    if is_not_duplicate(
        db, parameter_id=parameter_id, text_norm=normalize_value(text), value_id=hits[0]
    ):
        return None
    return hits[0]


def _known_part(
    db: Session, parameter_id: int, part: str, simples: Sequence[sa.Row]
) -> int | None:
    """Действующее простое значение, которым названа часть склейки: по точной норме
    либо по лемме (фильтр 2)."""
    exact = _exact(db, parameter_id, normalize_value(part))
    if exact is not None and not exact.retired and any(row.id == exact.value_id for row in simples):
        return exact.value_id
    return _lemma_match(db, parameter_id, part, simples)


def _extend(
    db: Session, scope: _Scope, parameter_id: int, context_id: int, text: str
) -> ResolvedValue:
    """Значение заводится (или возвращается найденное) через `_extend_with_value`:
    расширение списка пишет `revision`, событие и `value_added`."""
    added: list[int] = []
    value_id = _extend_with_value(
        db, parameter_id=parameter_id, schema_id=scope.schema_id, family_id=scope.family_id,
        context_id=context_id, text=text, added=added,
    )
    if added:
        return ResolvedValue(value_id, "added", added_ids=(value_id,))
    return ResolvedValue(value_id, "existing")


def _revive_composite(db: Session, composite_id: int) -> ResolvedValue:
    """Выведенное составное, названное заново: возвращается из истории, только если
    все его части — действующие простые значения (инвариант составного); иначе ответ по
    параметру `none`. Список для модели не расширяется и `value_added` не пишется:
    модель составных не видит."""
    parts = composite_parts(db, [composite_id]).get(composite_id, ("", ()))[1]
    active = {row.id for row in _active_simple_among(db, parts)}
    if not parts or set(parts) != active:
        logger.info(
            "composite %s is retired and has an unusable part among %s: the answer is none",
            composite_id, list(parts),
        )
        return ResolvedValue(None, "none")
    db.execute(
        sa.update(FamilyParameterValue)
        .where(FamilyParameterValue.id == composite_id)
        .values(retired_at=None)
        .execution_options(synchronize_session=False)
    )
    return ResolvedValue(composite_id, "existing")


def _active_simple_among(db: Session, value_ids: Sequence[int]) -> list[sa.Row]:
    return list(
        db.execute(
            sa.select(FamilyParameterValue.id).where(
                FamilyParameterValue.id.in_(list(value_ids)),
                FamilyParameterValue.merged_into_id.is_(None),
                FamilyParameterValue.retired_at.is_(None),
                FamilyParameterValue.composite_kind.is_(None),
            )
        ).all()
    )


def _combine(
    db: Session, parameter_id: int, kind: str, value_ids: Sequence[int], context_id: int,
    source_text: str, new_part_ids: Sequence[int] = (),
) -> ResolvedValue:
    """Составное из `value_ids`; одна различная часть — простое значение; вето
    разрешается `find_or_create_composite` в значение вето."""
    distinct = sorted(set(value_ids))
    if len(distinct) == 1:
        return ResolvedValue(distinct[0], "existing")
    veto_added: list[int] = []
    value_id = find_or_create_composite(
        db, parameter_id=parameter_id, kind=kind, part_ids=distinct, context_id=context_id,
        source_text=source_text, new_part_ids=[i for i in new_part_ids if i in distinct],
        added=veto_added,
    )
    return ResolvedValue(
        value_id, "composite" if composite_parts(db, [value_id]) else "existing",
        added_ids=tuple(veto_added),
    )


def _resolve_text(
    db: Session, parameter_id: int, text: str, context_id: int
) -> ResolvedValue | _Glue:
    """Порядок спеки §2.4 до создания составного: закрытый параметр; точная норма;
    фильтр 1 (отдаёт `_Glue`, а не составное); фильтр 2; новое значение."""
    text = text.strip()
    scope = _scope(db, parameter_id)
    exact = _exact(db, parameter_id, normalize_value(text))
    if scope.closed:
        if exact is not None and not exact.retired and not exact.composite:
            return ResolvedValue(exact.value_id, "existing")
        return ResolvedValue(None, "off_list", text=text)
    if exact is not None:
        if exact.composite and exact.retired:
            return _revive_composite(db, exact.value_id)
        return _extend(db, scope, parameter_id, context_id, text)

    simples = _active_simple(db, parameter_id)
    split = split_candidate(text)
    if split is not None:
        known = [_known_part(db, parameter_id, part, simples) for part in split.parts]
        if all(value_id is not None for value_id in known):
            distinct = tuple(sorted({i for i in known if i is not None}))
            if len(distinct) == 1:
                return ResolvedValue(distinct[0], "existing")
            return _Glue(kind=split.kind, value_ids=distinct)
    match = _lemma_match(db, parameter_id, text, simples)
    if match is not None:
        record_action(
            db, kind="equated", family_id=scope.family_id, context_id=context_id,
            parameter_id=parameter_id, value_id=match,
            payload={"text": text, "target_value_id": match, "context_ids": [context_id]},
        )
        return ResolvedValue(match, "equated")
    return _extend(db, scope, parameter_id, context_id, text)


def resolve_new_value(
    db: Session, *, parameter_id: int, text: str, context_id: int
) -> ResolvedValue:
    """Значение для текста, который модель назвала новым (спека §2.4).

    Закрытый параметр: точная норма действующего простого значения — оно, иначе
    `off_list`. Открытый: точная норма — значение ставится без фильтров (выведенное
    простое возвращается из истории; выведенное составное — только при действующих
    частях, иначе `none`); затем фильтр 1 (склейка → составное, автодействие
    `composite`), фильтр 2 (дубль → существующее, автодействие `equated`); иначе новое
    простое значение с автодействием `value_added`."""
    resolved = _resolve_text(db, parameter_id, text, context_id)
    if isinstance(resolved, _Glue):
        return _combine(
            db, parameter_id, resolved.kind, resolved.value_ids, context_id,
            source_text=text.strip(),
        )
    return resolved


def _link_parts_to_composite(
    db: Session, composite_id: int, part_ids: Sequence[int], after_action_id: int
) -> None:
    """Записи `value_added`, которые этот же вызов написал для частей составного
    (`id` больше `after_action_id`), получают id составного; прежние записи частей
    остаются как есть."""
    rows = db.execute(
        sa.select(CatalogAutoAction).where(
            CatalogAutoAction.kind == "value_added",
            CatalogAutoAction.value_id.in_(list(part_ids)),
            CatalogAutoAction.id > after_action_id,
        )
    ).scalars()
    for row in rows:
        row.payload = {**row.payload, "from_composite_id": composite_id}
    db.flush()


def resolve_several(
    db: Session, *, parameter_id: int, combine: str, texts: Sequence[str], context_id: int
) -> ResolvedValue:
    """Значение для ответа `several` (спека §2.4): каждый элемент проходит путь
    `new` (точная норма, фильтры) и канонизируется; склейка, разрезанная фильтром 1,
    отдаёт свои части без составного; составное того же вида (элемент или склейка)
    раскрывается в части, другого вида — ответ `none` (причина в лог); повторы id
    снимаются; одна часть — простое значение, две и больше — составное
    (`find_or_create_composite`, вето внутри) — единственное, что создаётся. Закрытый
    параметр и часть не из списка — `off_list` с текстом, собранным из элементов;
    выведенное составное с негодной частью — `none`."""
    watermark = db.execute(
        sa.select(sa.func.coalesce(sa.func.max(CatalogAutoAction.id), 0))
    ).scalar_one()
    resolved = [_resolve_text(db, parameter_id, text, context_id) for text in texts]
    added_ids = tuple(
        i for item in resolved if isinstance(item, ResolvedValue) for i in item.added_ids
    )
    if any(isinstance(item, ResolvedValue) and item.action == "off_list" for item in resolved):
        return ResolvedValue(None, "off_list", text=assembled_text(combine, texts))
    if any(isinstance(item, ResolvedValue) and item.action == "none" for item in resolved):
        return ResolvedValue(None, "none", added_ids=added_ids)

    composites = composite_parts(
        db, [item.value_id for item in resolved if isinstance(item, ResolvedValue) and item.value_id]
    )
    parts: list[int] = []
    for item in resolved:
        if isinstance(item, _Glue):
            kind, ids = item.kind, list(item.value_ids)
        elif item.value_id in composites:
            kind, ids = composites[item.value_id][0], list(composites[item.value_id][1])
        else:
            parts.append(item.value_id)
            continue
        if kind != combine:
            logger.info(
                "several %s: element of kind %s cannot be merged, the answer is written as none",
                combine, kind,
            )
            return ResolvedValue(None, "none", added_ids=added_ids)
        parts.extend(ids)

    result = _combine(
        db, parameter_id, combine, parts, context_id,
        source_text=assembled_text(combine, texts), new_part_ids=added_ids,
    )
    if result.action == "composite" and added_ids:
        _link_parts_to_composite(
            db, result.value_id, [i for i in added_ids if i in parts], watermark
        )
    action = "added" if result.value_id in added_ids and result.action == "existing" else result.action
    return ResolvedValue(result.value_id, action, added_ids=added_ids + result.added_ids)
