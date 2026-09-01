"""Чистый агрегатор состояний этапного разноса (спека этапного разноса §2.2).

Состояние логического раздела — агрегат по ПОЛНОМУ вектору решения
`(work_category_id, note, assigned_by, assigned_at)` всех offer-смет раунда:
без сравнения аудита раздел с единой статьёй и разошедшимися авторами исчез бы
из обоих блоков экрана (находка ревью гейта 1). Разбиение исчерпывающее:
`unassigned` / `partial` / `conflict` / `resolved`.

Модуль без `Session`: вход собирает `crud/round_unallocated.py`.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar

STATE_UNASSIGNED = "unassigned"
STATE_PARTIAL = "partial"
STATE_CONFLICT = "conflict"
STATE_RESOLVED = "resolved"
PENDING_STATES = (STATE_UNASSIGNED, STATE_PARTIAL, STATE_CONFLICT)

SectionKey = tuple[str, str]


@dataclass(frozen=True)
class Vector:
    work_category_id: int
    note: str | None
    assigned_by: int
    assigned_at: datetime


@dataclass(frozen=True)
class Classification:
    state: str
    assigned: int
    total: int
    categories: tuple[int, ...]
    notes: tuple[str | None, ...]
    audit_differs: bool


@dataclass(frozen=True)
class ChapterNode:
    key: SectionKey
    file_parent: SectionKey | None
    number: str | None
    title: str
    smr_article_raw: str | None
    rows: int


@dataclass(frozen=True)
class SectionAggregate:
    node: ChapterNode
    parent_key: SectionKey | None
    depth: int
    classification: Classification
    vectors: tuple[Vector | None, ...]


T = TypeVar("T")


def _distinct(values: Iterable[T]) -> tuple[T, ...]:
    seen: list = []
    for v in values:
        if v not in seen:
            seen.append(v)
    return tuple(seen)


def classify(vectors: Sequence[Vector | None]) -> Classification:
    """Четыре состояния §2.2. `vectors` — по одной позиции на offer-смету
    радиуса, порядок `estimate_id ASC`; пустой радиус — ошибка контракта, а не
    состояние: у раунда без offer-смет агрегатора не бывает (§1.5, 404)."""
    if not vectors:
        raise ValueError("радиус агрегатора пуст: у раунда нет offer-смет")
    present = [v for v in vectors if v is not None]
    total, assigned = len(vectors), len(present)
    categories = _distinct(v.work_category_id for v in present)
    notes = _distinct(v.note for v in present)
    audit_differs = len({(v.assigned_by, v.assigned_at) for v in present}) > 1
    if assigned == 0:
        state = STATE_UNASSIGNED
    elif assigned < total:
        state = STATE_PARTIAL
    elif len(set(present)) == 1:
        state = STATE_RESOLVED
    else:
        state = STATE_CONFLICT
    return Classification(state, assigned, total, categories, notes, audit_differs)


def input_set(
    nodes: Sequence[ChapterNode],
    missing_article: Mapping[SectionKey, bool],
    vectors: Mapping[SectionKey, Sequence[Vector | None]],
) -> list[ChapterNode]:
    """§2.2: эффективной статьи нет хотя бы в одной смете ИЛИ есть хотя бы
    один override. Порядок `nodes` (файловый) сохраняется.

    Ключ, отсутствующий в `missing_article`, читается как «статья есть»
    (`.get(k, False)`): умолчание превращает «неизвестно» в решение. Внутри
    чистого модуля с документированным входом это законно, но должно быть
    названо явно, а не подразумеваться.
    """
    return [
        n for n in nodes
        if missing_article.get(n.key, False) or any(v is not None for v in vectors.get(n.key, ()))
    ]


def aggregate(
    nodes: Sequence[ChapterNode],
    missing_article: Mapping[SectionKey, bool],
    vectors: Mapping[SectionKey, Sequence[Vector | None]],
) -> list[SectionAggregate]:
    """Входное множество, переподвешивание, глубина и классификация — в файловом
    порядке `nodes`.

    Родитель внутри входного множества — ближайший файловый предок из
    множества; предок ВНЕ множества — законный промежуток цепочки, поэтому
    `nodes` обязаны нести все разделы сметы. Узел со своим `smr_article_raw`
    — всегда корень своего кусочка (тот же предикат, что у
    `_unallocated_sections` паспорта: решение на предке до узла с собственным
    утверждением не доходит — правило Ф3 «утверждение файла сильнее
    наследования», — и вложенность обещала бы неправду).

    Предпосылка: `vectors` обязан нести полноразмерный список — один слот на
    offer-смету радиуса, порядок `estimate_id ASC` — для КАЖДОГО узла
    входного множества (§2.2); нехватка ключа даёт голый `KeyError` при
    построении `SectionAggregate` (`vectors[n.key]`), тогда как `input_set`
    тот же пропуск терпит (`vectors.get(n.key, ())`) — асимметрия, а не
    гарантия.
    """
    selected = input_set(nodes, missing_article, vectors)
    file_parent = {n.key: n.file_parent for n in nodes}
    in_set = {n.key for n in selected}
    parent_of: dict[SectionKey, SectionKey | None] = {}
    for n in selected:
        if n.smr_article_raw is not None:
            parent_of[n.key] = None
            continue
        p = file_parent[n.key]
        while p is not None and p not in in_set:
            p = file_parent.get(p)
        parent_of[n.key] = p
    depth: dict[SectionKey, int] = {}

    def _depth(k: SectionKey) -> int:
        if k not in depth:
            p = parent_of[k]
            depth[k] = 0 if p is None else _depth(p) + 1
        return depth[k]

    return [
        SectionAggregate(
            node=n, parent_key=parent_of[n.key], depth=_depth(n.key),
            classification=classify(vectors[n.key]), vectors=tuple(vectors[n.key]),
        )
        for n in selected
    ]
