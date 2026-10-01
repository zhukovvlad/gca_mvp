"""Запрос к модели семантических предложений: материал, вход контекста,
предикат применимости, каноническое тело и его отпечатки (спека
`2026-09-28-semantic-suggestions-design.md` §2.2, §2.7, §2.10, §2.11).

Задача 2 фичи «Семантические предложения»
(`docs/superpowers/plans/2026-09-28-semantic-suggestions.md`). Модуль не
исполняет вызов провайдера и не пишет очередь — только строит материал и
каноническое тело; потребители — разбор ответа модели, проверка приватности
перед отправкой, расчёт стоимости, сверка (reconcile) и исполнитель захвата
(worker), каждый напрямую на этом модуле.

Раскладка тела и текст промпта — те же, что мерились в пилоте (`tasks/catalog-
pilot-2026-09-18/exp_family_assign.py`, `feature2-measure/cache_probe.py`):
`system` = [блок промпта; блок списка семей единицы, несущий метку кэша];
`user` = строка варианта A (один самый частый путь разделов среди членов
контекста). `render_context_request` в базу не ходит — работает на заранее
загруженном `ContextRequestMaterial` (`load_request_material`).

`PLACE_DICTIONARY_VERSION` и `name_role` контекста в тело не входят и ни на
один отпечаток не влияют (спека §2.10): словарь мест — не часть конфигурации
запроса, а `RenderedRequest.place_dictionary_version` — только аудиторская
колонка, читаемая на момент рендера.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from config import Settings
from models import (
    CatalogContext,
    CatalogPosition,
    ContextBucket,
    ContextMember,
    FamilyStatus,
    PositionItem,
    SemanticKind,
    SemanticState,
    UnitOfMeasure,
    WorkCategory,
    WorkFamily,
)
from services.context_routing import RoutingError, chapter_paths
from services.semantic_rules import PLACE_DICTIONARY_VERSION

#: Версия текста промпта, переносимого константой (спека §2.2): промпт ниже —
#: побайтная копия текста `SYSTEM` из `exp_family_assign.py`, замеренного в
#: пилоте; смена текста промпта обязана сопровождаться инкрементом.
PROMPT_VERSION = 1

#: Версия канонической сериализации тела и хэшей (спека §2.2). Растёт при
#: смене САМОЙ раскладки (порядок ключей структуры хэша, состав полей), а не
#: содержимого промпта или модели — те уже отражены в теле и в
#: `PROMPT_VERSION`.
SERIALIZATION_VERSION = 1

#: Ось «флаг рассуждения», которой нет в `Settings`:
#: модульная константа, тесты меняют её `monkeypatch`-ем, а не аргументом
#: функции — рассуждение отключено безусловно на весь MVP.
REASONING_ENABLED = False

#: Ось «место метки кэша», которой нет в `Settings`: индекс блока списка
#: `system`, несущего `cache_control` (0 — блок промпта, 1 — блок списка
#: семей единицы). Кэшируемый префикс — ОБА блока (промпт + список семей,
#: спека §2.2: «первые два блока — кэшируемый префикс, общий для всех
#: контекстов единицы»); метка `cache_control` ставится на ПОСЛЕДНЕМ блоке
#: префикса (список семей), потому что провайдер кэширует всё до метки
#: включительно — одной меткой на конце охвачен и промпт перед ней.
CACHE_CONTROL_BLOCK_INDEX = 1

#: Заголовок блока списка семей (спека §2.3, решение задачи 4): используется и
#: здесь в рендере, и в `services/semantic_privacy.py` при проверке перед
#: отправкой — блок семей опознаётся по тексту, начинающемуся с этой строки, а
#: не по индексу блока в `system`.
FAMILY_BLOCK_HEADER = "СПИСОК СЕМЕЙ:\n"

#: Текст промпта — ПОБАЙТНАЯ копия `SYSTEM` из `tasks/catalog-pilot-2026-09-18/
#: exp_family_assign.py` (замер, по которому получена вся раскладка запроса).
SEMANTIC_PROMPT = """Ты сметчик-каталогизатор строительной компании-заказчика. У компании есть каталог СЕМЕЙ работ: семья — это тип работы без параметров, бренда и места (например «Устройство пола», «Посадка растений», «Двери»). Внутри семьи строки различаются параметрами (класс бетона, размер, вид растения) — это не мешает им быть одной семьёй.

Тебе дают одну строку ведомости (разделы, статья, наименование, единица) и список семей. Ответь строгим JSON без пояснений:
{"family_id": <номер из списка или 0>, "new_family_name": "<имя, если family_id = 0, иначе null>", "confidence": 0.0-1.0, "reason": "<одно предложение>"}

Правила:
- family_id = 0 только если НИ ОДНА семья списка не описывает этот тип работы. Не заводи новую семью ради параметров, бренда или места.
- Семья должна совпадать по единице измерения (в скобках); при расхождении единицы предпочитай 0 или другую семью.
- Строка, называющая систему или часть объекта целиком в «компл» (тепловой пункт, АСУД, кабельные линии), в семьи работ не входит: family_id = 0, new_family_name = "СИСТЕМА".
- confidence — насколько ты уверен именно в этом family_id."""

#: Отображение единицы, когда её нет (спека §2.2): контексты и семьи без
#: единицы (`unit_id IS NULL`) существуют, и это законное значение, а не дыра
#: в данных — сентинел только для РЕНДЕРА, `unit_code` полей остаётся `None`.
_NO_UNIT_DISPLAY = "без единицы"

_WHITESPACE_RE = re.compile(r"\s+")


def _collapse_whitespace(text: str) -> str:
    """Любая серия пробельных символов (включая переводы строк) — один
    пробел, края обрезаны. Нужна тексту семьи (см. `family_line`), чтобы
    строка семьи занимала РОВНО одну строку блока — задача 4 (проверка перед
    отправкой) приписывает совпадение семье по этой строке."""
    return _WHITESPACE_RE.sub(" ", text).strip()


@dataclass(frozen=True)
class CandidateFamily:
    """Одна активная семья единицы, отправляемая в списке кандидатов (спека
    §2.2)."""

    id: int
    title: str
    unit_code: str | None
    definition: str


@dataclass(frozen=True)
class ContextRequestMaterial:
    """Заранее загруженный материал для рендера тела запроса контекста (спека
    §2.2, §2.10). `render_context_request` и `is_applicable` работают ТОЛЬКО
    на этой структуре — сами в базу не ходят."""

    context_id: int
    unit_id: int | None
    unit_code: str | None
    title: str
    article: str | None
    path_counts: tuple[tuple[str, int], ...]
    member_count: int
    archived: bool
    semantic_state: str
    semantic_kind: str
    work_family_id: int | None
    candidates: tuple[CandidateFamily, ...]
    path_broken: bool = False
    """Путь разделов какого-то члена контекста не строится (цикл в цепочке
    разделов): контекст неприменим, `path_counts` пуст."""


@dataclass(frozen=True)
class RenderedRequest:
    """Каноническое тело запроса и его отпечатки (спека §2.2)."""

    body: dict
    request_hash: str
    prefix_hash: str
    candidates_hash: str
    input_hash: str
    prefix_bytes: int
    user_bytes: int
    place_dictionary_version: int


def family_line(candidate: CandidateFamily) -> str:
    """Строка ОДНОЙ семьи блока кандидатов (спека §2.2): `"{id}. {имя}
    [{единица}] — {определение}"`, ровно одна строка на семью. Имя и
    определение проходят через `_collapse_whitespace` — задача 4
    (проверка перед отправкой) опирается на то, что строка семьи начинается
    с `f"{id}. "` и не переносится."""
    unit_display = candidate.unit_code if candidate.unit_code is not None else _NO_UNIT_DISPLAY
    title = _collapse_whitespace(candidate.title)
    definition = _collapse_whitespace(candidate.definition)
    return f"{candidate.id}. {title} [{unit_display}] — {definition}"


def _candidates_block(candidates: Sequence[CandidateFamily]) -> str:
    """Один блок кандидатов, по строке на семью. Порядок — забота вызывающего
    (`render_context_request` сортирует по `id` один раз, до вызова этой
    функции); единственная точка сортировки — там, не здесь."""
    return "\n".join(family_line(c) for c in candidates)


def _canonical_bytes(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _sha256_hex(obj: object) -> str:
    return hashlib.sha256(_canonical_bytes(obj)).hexdigest()


# ---------------------------------------------------------------------------
#  top_path — вариант A замера (спека §2.2)
# ---------------------------------------------------------------------------

def top_path(path_counts: Sequence[tuple[str, int]]) -> str:
    """Один самый частый путь разделов: частота по убыванию, при равенстве —
    лексикографически меньший путь, независимо от порядка входа (спека
    §2.2). Отпечаток контекста опирается на устойчивость этого выбора —
    перестановка того же набора обязана давать тот же путь."""
    return min(path_counts, key=lambda pc: (-pc[1], pc[0]))[0]


# ---------------------------------------------------------------------------
#  is_applicable — предикат применимости (спека §2.7)
# ---------------------------------------------------------------------------

def is_applicable(material: ContextRequestMaterial) -> bool:
    """Контекст получает задания, только если ОДНОВРЕМЕННО (спека §2.7): не
    архивирован и имеет хотя бы одно членство; `semantic_state <>
    'NOT_APPLICABLE'`; `semantic_kind <> 'SYSTEM'`; `work_family_id IS NULL`;
    у единицы контекста есть хотя бы одна активная семья. В базу не ходит —
    решает по уже загруженному `material` (кандидаты — его часть)."""
    if material.archived:
        return False
    if material.member_count == 0:
        return False
    if material.semantic_state == SemanticState.NOT_APPLICABLE.value:
        return False
    if material.semantic_kind == SemanticKind.SYSTEM.value:
        return False
    if material.work_family_id is not None:
        return False
    if material.path_broken:
        return False
    return bool(material.candidates)


# ---------------------------------------------------------------------------
#  load_request_material — пакетная загрузка (спека §2.2, §2.10)
# ---------------------------------------------------------------------------

def load_request_material(
    db: Session, context_ids: Collection[int]
) -> dict[int, ContextRequestMaterial]:
    """Загружает материал запроса для МНОЖЕСТВА контекстов постоянным числом
    запросов (не растущим с числом контекстов): контексты с корзиной,
    строкой каталога, единицей и статьёй — одним запросом; число членств —
    отдельным агрегатом, пары «раздел членства → число» — ещё одним; пути
    разделов — одним вызовом `chapter_paths` (на цикле в цепочке разделов —
    затем по одному разделу, чтобы найти испорченные: такие контексты получают
    `path_broken`); кандидаты — одним запросом по всем единицам набора сразу.

    Путь считается по ВСЕМ членствам контекста, включая `STALE` (спека §2.2,
    решение спеки 5): `STALE` — членство устарело после разноса статьи, а не
    «позиции нет», раздел позиции — такой же факт о написании.

    Отсутствующие `context_id` (контекст не найден) в результат не попадают.
    """
    ids = list(dict.fromkeys(context_ids))
    if not ids:
        return {}

    unit = aliased(UnitOfMeasure)
    rows = db.execute(
        sa.select(
            CatalogContext.id.label("context_id"),
            CatalogContext.archived_at,
            CatalogContext.semantic_state,
            CatalogContext.semantic_kind,
            CatalogContext.work_family_id,
            CatalogPosition.unit_id,
            unit.code.label("unit_code"),
            CatalogPosition.standard_job_title.label("title"),
            WorkCategory.code.label("category_code"),
            WorkCategory.title.label("category_title"),
        )
        .select_from(CatalogContext)
        .join(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
        .join(CatalogPosition, CatalogPosition.id == ContextBucket.catalog_position_id)
        .outerjoin(unit, unit.id == CatalogPosition.unit_id)
        .outerjoin(WorkCategory, WorkCategory.id == ContextBucket.work_category_id)
        .where(CatalogContext.id.in_(ids))
    ).all()
    if not rows:
        return {}

    member_counts = dict(
        db.execute(
            sa.select(ContextMember.context_id, sa.func.count().label("cnt"))
            .where(ContextMember.context_id.in_(ids))
            .group_by(ContextMember.context_id)
        ).all()
    )

    path_rows = db.execute(
        sa.select(
            ContextMember.context_id,
            PositionItem.chapter_item_id,
            sa.func.count().label("cnt"),
        )
        .select_from(ContextMember)
        .join(PositionItem, PositionItem.id == ContextMember.position_item_id)
        .where(ContextMember.context_id.in_(ids))
        .group_by(ContextMember.context_id, PositionItem.chapter_item_id)
    ).all()

    chapter_ids = {row.chapter_item_id for row in path_rows if row.chapter_item_id is not None}
    broken_chapter_ids: set[int] = set()
    try:
        paths_top_down = chapter_paths(db, chapter_ids)
    except RoutingError:
        # Цикл в цепочке разделов: данные, которые допускает фича 1. Виноватые
        # разделы ищутся по одному — только на этом пути, обычный остаётся
        # одним обращением.
        paths_top_down = {}
        for chapter_id in chapter_ids:
            try:
                paths_top_down.update(chapter_paths(db, [chapter_id]))
            except RoutingError:
                broken_chapter_ids.add(chapter_id)

    broken_context_ids = {
        row.context_id for row in path_rows if row.chapter_item_id in broken_chapter_ids
    }

    path_by_context: dict[int, dict[str, int]] = {}
    for row in path_rows:
        if row.context_id in broken_context_ids:
            continue
        path_str = (
            ""
            if row.chapter_item_id is None
            else " / ".join(paths_top_down.get(row.chapter_item_id, ()))
        )
        bucket = path_by_context.setdefault(row.context_id, {})
        bucket[path_str] = bucket.get(path_str, 0) + row.cnt

    # `rows` уже проверен непустым выше, поэтому `unit_ids_present` тоже
    # непусто и `conditions` ниже всегда содержит хотя бы одно условие
    # (обычные единицы и/или "без единицы") — запрос кандидатов не охраняется
    # отдельным `if`.
    unit_ids_present = {row.unit_id for row in rows}
    non_null_unit_ids = [u for u in unit_ids_present if u is not None]
    conditions = []
    if non_null_unit_ids:
        conditions.append(WorkFamily.unit_id.in_(non_null_unit_ids))
    if None in unit_ids_present:
        conditions.append(WorkFamily.unit_id.is_(None))

    fam_unit = aliased(UnitOfMeasure)
    candidate_rows = db.execute(
        sa.select(
            WorkFamily.id,
            WorkFamily.title,
            WorkFamily.unit_id,
            WorkFamily.definition,
            fam_unit.code.label("unit_code"),
        )
        .outerjoin(fam_unit, fam_unit.id == WorkFamily.unit_id)
        .where(WorkFamily.status == FamilyStatus.active.value, sa.or_(*conditions))
        .order_by(WorkFamily.id)
    ).all()

    candidates_by_unit: dict[int | None, list[CandidateFamily]] = {}
    for row in candidate_rows:
        candidates_by_unit.setdefault(row.unit_id, []).append(
            CandidateFamily(
                id=row.id, title=row.title, unit_code=row.unit_code, definition=row.definition
            )
        )

    result: dict[int, ContextRequestMaterial] = {}
    for row in rows:
        cid = row.context_id
        article = (
            f"{row.category_code} {row.category_title}" if row.category_code is not None else None
        )
        path_counts = tuple(path_by_context.get(cid, {}).items())
        candidates = tuple(candidates_by_unit.get(row.unit_id, []))
        result[cid] = ContextRequestMaterial(
            context_id=cid,
            unit_id=row.unit_id,
            unit_code=row.unit_code,
            title=row.title,
            article=article,
            path_counts=path_counts,
            member_count=member_counts.get(cid, 0),
            archived=row.archived_at is not None,
            semantic_state=row.semantic_state,
            semantic_kind=row.semantic_kind,
            work_family_id=row.work_family_id,
            candidates=candidates,
            path_broken=cid in broken_context_ids,
        )
    return result


# ---------------------------------------------------------------------------
#  render_context_request — каноническое тело и отпечатки (спека §2.2)
# ---------------------------------------------------------------------------

def _user_text(material: ContextRequestMaterial) -> str:
    """Пользовательское сообщение запроса — строка варианта A (спека §2.2)."""
    path = top_path(material.path_counts) if material.path_counts else ""
    path_display = path or "(разделы не указаны)"
    article_display = material.article or "(не определена)"
    unit_display = material.unit_code if material.unit_code is not None else _NO_UNIT_DISPLAY
    return (
        f"СТРОКА\nРазделы: {path_display}\nСтатья: {article_display}\n"
        f"Наименование: {material.title}\nЕдиница: {unit_display}"
    )


def _build_body(
    ordered_candidates: Sequence[CandidateFamily], user_text: str, *, settings: Settings
) -> tuple[dict, list[dict]]:
    """Тело запроса и блоки `system`; кандидаты уже упорядочены по `id`."""
    family_block = _candidates_block(ordered_candidates)
    system_blocks: list[dict] = [
        {"type": "text", "text": SEMANTIC_PROMPT},
        {"type": "text", "text": f"{FAMILY_BLOCK_HEADER}{family_block}"},
    ]
    system_blocks[CACHE_CONTROL_BLOCK_INDEX]["cache_control"] = {"type": "ephemeral"}
    body = {
        "model": settings.SEMANTIC_MODEL,
        "temperature": 0,
        "max_tokens": settings.SEMANTIC_MAX_TOKENS,
        "reasoning": {"enabled": REASONING_ENABLED},
        "usage": {"include": True},
        "messages": [
            {"role": "system", "content": system_blocks},
            {"role": "user", "content": user_text},
        ],
    }
    return body, system_blocks


def render_context_request(material: ContextRequestMaterial, *, settings: Settings) -> RenderedRequest:
    """Строит каноническое тело запроса — ровно то, что уйдёт провайдеру — и
    его отпечатки (спека §2.2). Детерминирован; не ходит в базу; снимок
    кандидатов сортируется по `id` внутри, поэтому порядок кандидатов на
    входе на результат не влияет."""
    ordered_candidates = tuple(sorted(material.candidates, key=lambda c: c.id))
    user_text = _user_text(material)
    body, system_blocks = _build_body(ordered_candidates, user_text, settings=settings)

    request_hash = _sha256_hex({"serialization_version": SERIALIZATION_VERSION, "body": body})
    prefix_hash = _sha256_hex(
        {
            "serialization_version": SERIALIZATION_VERSION,
            "model": settings.SEMANTIC_MODEL,
            "system": system_blocks,
        }
    )
    candidates_snapshot = [
        {"id": c.id, "title": c.title, "unit_code": c.unit_code, "definition": c.definition}
        for c in ordered_candidates
    ]
    candidates_hash = _sha256_hex(candidates_snapshot)
    input_hash = _sha256_hex({"serialization_version": SERIALIZATION_VERSION, "user": user_text})

    prefix_bytes = sum(len(block["text"].encode("utf-8")) for block in system_blocks)
    user_bytes = len(user_text.encode("utf-8"))

    return RenderedRequest(
        body=body,
        request_hash=request_hash,
        prefix_hash=prefix_hash,
        candidates_hash=candidates_hash,
        input_hash=input_hash,
        prefix_bytes=prefix_bytes,
        user_bytes=user_bytes,
        place_dictionary_version=PLACE_DICTIONARY_VERSION,
    )


# ---------------------------------------------------------------------------
#  Быстрый расчёт `request_hash` для контекстов одной единицы
# ---------------------------------------------------------------------------

#: Метка на месте пользовательского сообщения при разборе канонического тела на
#: «до» и «после»; в промпте и в именах семей не встречается.
_USER_TEXT_SENTINEL = "\x01user-text-sentinel\x01"


class RequestHasher:
    """`request_hash` контекстов одной единицы без полного рендера на каждый.

    Тело запроса у всех контекстов единицы отличается только пользовательским
    сообщением, а хэш — SHA-256 канонического JSON, где сообщение лежит значением
    строки. Поэтому каноническое тело с меткой вместо сообщения режется по метке
    на две половины один раз на единицу, а хэш контекста — SHA-256 от «первая
    половина + JSON-строка сообщения + вторая половина». Результат побайтно равен
    `render_context_request(...).request_hash`; список семей при этом не
    рендерится и не сериализуется заново для каждого контекста.

    Экземпляр строится по материалу любого контекста единицы: кандидаты у всех
    контекстов единицы одни и те же.
    """

    def __init__(self, template: ContextRequestMaterial, *, settings: Settings) -> None:
        ordered = tuple(sorted(template.candidates, key=lambda c: c.id))
        body, _ = _build_body(ordered, _USER_TEXT_SENTINEL, settings=settings)
        canonical = _canonical_bytes({"serialization_version": SERIALIZATION_VERSION, "body": body})
        marker = json.dumps(_USER_TEXT_SENTINEL, ensure_ascii=False).encode("utf-8")
        # Метка — целое JSON-значение в кавычках: она есть в теле ровно один раз, на
        # месте сообщения (промпт и блок семей целиком ей равняться не могут).
        head, _marker, tail = canonical.partition(marker)
        self._split = (head, tail)

    def request_hash(self, material: ContextRequestMaterial) -> str:
        head, tail = self._split
        user_json = json.dumps(_user_text(material), ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(head + user_json + tail).hexdigest()
