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
    #: ПОЛНОЕ файловое поддерево узла — подпись `manual[]` (§2.3): учётная
    #: запись «сколько лежит под этим решением по факту файла».
    rows: int
    #: Прямые не-раздельные строки САМОГО узла — вход свёртки достижимости
    #: (§2.2). Не выводится наружу: наружу идёт `SectionAggregate.
    #: reachable_rows`, свёртка этого поля по наследующей части поддерева.
    own_rows: int


@dataclass(frozen=True)
class SectionAggregate:
    node: ChapterNode
    parent_key: SectionKey | None
    depth: int
    classification: Classification
    vectors: tuple[Vector | None, ...]
    #: Достижимые строки §2.2 — сколько строк сдвинет решение на этом узле;
    #: подпись `sections[]` и одновременно условие показа (ОДНА формула, а не
    #: две: невидимая на экране величина в условии и прятала фантомы).
    reachable_rows: int


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


def _inherits(key: SectionKey, node: ChapterNode, missing_article: Mapping[SectionKey, bool]) -> bool:
    """Наследует ли узел статью предка — ТОТ ЖЕ предикат, что у
    `_unallocated_sections._inherits` паспорта, переведённый на радиус раунда:
    эффективной статьи нет (здесь — «хотя бы в одной offer-смете», §2.2) И
    своего файлового утверждения нет.

    Второй конъюнкт — правило Ф3 «утверждение файла сильнее наследования»:
    ребёнок с любым СВОИМ утверждением не наследует ничего, даже если это
    утверждение не дало ему статьи (нечитаемый префикс или код вне
    справочника). Он сам кандидат на ручное решение, но решение ВЫШЕ него его
    не тронет.
    """
    return missing_article.get(key, False) and node.smr_article_raw is None


def reachable_rows(
    nodes: Sequence[ChapterNode],
    missing_article: Mapping[SectionKey, bool],
) -> dict[SectionKey, int]:
    """Достижимые строки §2.2 на каждый узел: свои прямые строки плюс то же
    рекурсивно по НАСЛЕДУЮЩИМ детям (`_inherits`). Закон тот же, что у
    `_unallocated_sections._unallocated_fold` паспорта, и по той же причине:
    расчёт, разошедшийся с паспортом, и есть дефект, который эта функция
    заводится закрыть.

    Свои прямые строки узла входят ВСЕГДА, независимо от его собственного
    `smr_article_raw`: решение на узле сильнее его же файлового утверждения.
    Блокирующий ребёнок обрывает обход НА СЕБЕ — его поддерево целиком вне
    свёртки предка, потому что цепочка наследования прервана выше него.

    Считается по ВСЕМ `nodes`, а не по входному множеству: наследующий ребёнок
    в множество попадает по построению (`_inherits` требует отсутствия статьи,
    а это первый дизъюнкт §2.2), но полагаться на этот порядок незачем.
    """
    children: dict[SectionKey, list[ChapterNode]] = {}
    for n in nodes:
        if n.file_parent is not None:
            children.setdefault(n.file_parent, []).append(n)
    cache: dict[SectionKey, int] = {}

    def _fold(n: ChapterNode) -> int:
        if n.key not in cache:
            cache[n.key] = n.own_rows + sum(
                _fold(c) for c in children.get(n.key, ()) if _inherits(c.key, c, missing_article)
            )
        return cache[n.key]

    return {n.key: _fold(n) for n in nodes}


def input_set(
    nodes: Sequence[ChapterNode],
    missing_article: Mapping[SectionKey, bool],
    vectors: Mapping[SectionKey, Sequence[Vector | None]],
    reach: Mapping[SectionKey, int] | None = None,
) -> list[ChapterNode]:
    """§2.2: (эффективной статьи нет хотя бы в одной смете И решение на узле
    достигает хотя бы одной строки) ИЛИ есть хотя бы один override. Порядок
    `nodes` (файловый) сохраняется.

    **Достижимость правит только первый дизъюнкт.** Узел, на котором решение
    уже стоит, остаётся видимым при любой достижимости — иначе решение нельзя
    было бы снять; так же устроен и паспорт, чей `_manual_assignments` границу
    §5.2 не применяет. Реализация, применившая фильтр к дизъюнкции целиком,
    прошла бы все тесты состояний и заперла бы ручное решение навсегда.

    `reach` — уже посчитанная свёртка (`reachable_rows`); `None` считает её на
    месте. Параметр существует, чтобы `aggregate` не считал одну и ту же
    свёртку дважды, а не как точка расширения.

    Ключ, отсутствующий в `missing_article`, читается как «статья есть»
    (`.get(k, False)`): умолчание превращает «неизвестно» в решение. Внутри
    чистого модуля с документированным входом это законно, но должно быть
    названо явно, а не подразумеваться.
    """
    reach = reachable_rows(nodes, missing_article) if reach is None else reach
    return [
        n for n in nodes
        if (missing_article.get(n.key, False) and reach.get(n.key, 0) > 0)
        or any(v is not None for v in vectors.get(n.key, ()))
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
    reach = reachable_rows(nodes, missing_article)
    selected = input_set(nodes, missing_article, vectors, reach)
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
            reachable_rows=reach[n.key],
        )
        for n in selected
    ]
