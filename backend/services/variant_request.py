"""Запрос заданий `family_schema` и `context_values`: материал, каноническое
тело и его отпечатки (спека `2026-10-02-catalog-variants-design.md` §2.2, §2.3,
§2.6).

Модуль не исполняет вызов провайдера и не пишет очередь. Раскладка тела та же,
что у `semantic_request.py` (фича «Семантические предложения»):
`messages = [{"role": "system", "content": [блоки]}, {"role": "user", "content":
строка}]`, поэтому `semantic_privacy.find_privacy_matches` читает тела новых
видов без правок — сообщение user проверяется как «context», блоки system — как
«prompt». Тело вида `family_suggestion` здесь не строится и не меняется:
`render_request_for` отдаёт его прежнему `render_context_request`.

Профиль модели (модель, `max_tokens`, рассуждение) и `response_format` входят в
тело, а значит в `request_hash` и `prefix_hash`; версии промптов — только
столбец аудита задания и в хэши не входят. Идентификаторы (`family_id`,
`schema_id`, `context_id`) в тело и хэши не входят: два контекста с одинаковым
наименованием, путями и схемой дают один хэш.
"""
from __future__ import annotations

import copy
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Final

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from config import Settings
from models import (
    CatalogContext,
    CatalogPosition,
    ContextBucket,
    FamilyParameter,
    FamilyParameterSchema,
    FamilyParameterValue,
    FamilySuggestion,
    SchemaStatus,
    SemanticJob,
    SemanticJobKind,
    UnitOfMeasure,
    WorkFamily,
)
from services.semantic_request import (
    _NO_UNIT_DISPLAY,
    SERIALIZATION_VERSION,
    RenderedRequest,
    _sha256_hex,
    load_request_material,
    render_context_request,
)
from services.semantic_rules import PLACE_DICTIONARY_VERSION
from services.variant_answer import SCHEMA_RESPONSE_FORMAT, VALUES_RESPONSE_FORMAT

#: Версии текстов промптов — столбец аудита задания, в хэши не входят (как
#: `semantic_request.PROMPT_VERSION`); смена текста промпта сопровождается
#: инкрементом.
SCHEMA_PROMPT_VERSION = 1
VALUES_PROMPT_VERSION = 1

#: Сколько путей разделов уходит модели вместе с наименованием (спека §2.3).
MAX_PATHS: Final = 10

#: Заголовок блока схемы семьи в `system` вида `context_values`. Не совпадает с
#: `semantic_request.FAMILY_BLOCK_HEADER`: проверка приватности опознаёт блок
#: списка семей по этому заголовку и ждёт в нём строки семей с номером.
SCHEMA_BLOCK_HEADER: Final = "СХЕМА СЕМЬИ:\n"

SCHEMA_PROMPT = """Ты помогаешь вести каталог строительных работ генподрядчика. Семья — тип работы с одной единицей измерения. Внутри семьи строки различаются ЦЕНОВЫМИ ПАРАМЕТРАМИ: признаками, от которых цена за единицу при той же работе меняется существенно (материал, толщина, класс, тип изделия, диаметр, огнестойкость и т. п.).

Тебе дают семью (имя, единицу, определение) и пронумерованный список наименований её строк из смет.

Задача:
1. Выбери от 0 до 3 ценовых параметров этой семьи. Параметр годится, только если (а) он меняет цену за единицу семьи и (б) его значение хотя бы у части строк ПРЯМО читается из наименования. Если таких нет — пустой список параметров.
2. Не параметры: место и помещение (секция, корпус, этаж, квартира, паркинг, МОП, кровля как место), номер или очередь, формулировка и порядок слов, единица измерения, бренд без класса изделия.
3. Для каждого параметра задай закрытый список значений: от 1 до 8, каждое значение непустое, в нормализованной форме. Значение «не указано» всегда допустимо и в список не входит.

Строкам значения не присваивай: нужна только схема — параметры и списки их значений.

Ответ — только JSON без пояснений, по схеме:
{"parameters": [{"ordinal": 1, "name": "<имя параметра>", "values": ["<значение>", ...]}]}
Номера "ordinal" — 1, 2, 3 по порядку, без пропусков. Если подходящих параметров нет — {"parameters": []}."""

VALUES_PROMPT = """Ты помогаешь вести каталог строительных работ генподрядчика. У семьи работ уже ЗАКРЕПЛЕНЫ ценовые параметры и закрытые списки их значений (блок СХЕМА СЕМЬИ).
Тебе дают ОДНУ строку: наименование, единицу, статью и до десяти путей разделов сметы, под которыми эта строка встречается (от самого частого; путь — от верхнего раздела к ближайшему).

Для КАЖДОГО параметра схемы верни ровно один объект:
- значение берётся из наименования, а если там его нет — из путей разделов (ближайший раздел обычно точнее);
- место (секция, корпус, этаж, урбан-блок, помещение как место) значением параметра не является;
- если значение есть в закреплённом списке — "kind": "value" и "value" ровно как в списке;
- если значение явно читается, но в списке его нет — "kind": "new" и "value" — новое значение;
- если разные пути указывают на РАЗНЫЕ значения — "kind": "conflict", "value": null;
- если значения нет ни в наименовании, ни в путях — "kind": "none", "value": null. Не догадывайся по смыслу;
- "source" — откуда прочитано значение: "name" (наименование) или "path" (путь разделов); для "kind" "conflict" и "none" — null;
- пустое значение недопустимо: если значения нет, это "kind": "none", а не пустая строка.

Ответ — только JSON без пояснений:
{"values": [{"ordinal": <номер параметра>, "kind": "value" | "new" | "conflict" | "none", "value": <строка или null>, "source": "name" | "path" | null}]}
По одному объекту на каждый параметр схемы."""


class SubjectNotRenderable(Exception):
    """Предмет задания сейчас не рендерится: нет материала (семьи, контекста,
    семьи у контекста, текущей версии схемы). Устаревшая схема вместо него не
    подставляется."""


# ---------------------------------------------------------------------------
#  Материал
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SchemaRequestMaterial:
    """Вход задания `family_schema` (спека §2.3): семья и наименования строк."""

    family_id: int
    schema_id: int
    title: str
    unit_code: str | None
    definition: str
    names: tuple[str, ...]
    """Наименования строк семьи, отсортированные, без повторов."""


@dataclass(frozen=True)
class SchemaParameterIn:
    """Параметр схемы, отправляемый в запросе значений."""

    ordinal: int
    name: str
    values: tuple[str, ...]
    """Значения по возрастанию `id`, слитые исключены."""


@dataclass(frozen=True)
class ValuesRequestMaterial:
    """Вход задания `context_values` (спека §2.3): строка, её пути и схема."""

    context_id: int
    schema_id: int
    title: str
    unit_code: str | None
    article: str | None
    paths: tuple[str, ...]
    """До десяти путей — в порядке, в каком они уходят модели."""
    parameters: tuple[SchemaParameterIn, ...]


def top_paths(
    path_counts: Sequence[tuple[str, int]], limit: int = MAX_PATHS
) -> tuple[str, ...]:
    """Не больше `limit` самых частых путей: частота по убыванию, при равенстве
    лексикографически, независимо от порядка входа (спека §2.3; единственный
    выбор — как `semantic_request.top_path`, но списком)."""
    ordered = sorted(path_counts, key=lambda pc: (-pc[1], pc[0]))
    return tuple(path for path, _count in ordered[:limit])


def paths_hash_of(paths: Sequence[str]) -> str:
    """sha256 списка путей В ТОМ ПОРЯДКЕ, в каком они уходят модели: перестановка
    приоритетов меняет тело запроса и обязана менять этот хэш (спека §2.3)."""
    return _sha256_hex(list(paths))


def load_schema_material(db: Session, family_id: int, schema_id: int) -> SchemaRequestMaterial:
    """Материал задания `family_schema`: имя, единица, определение семьи и
    отсортированный список наименований её строк без повторов.

    Состав имён (спека §2.3): контекст не архивирован И (`work_family_id = F`
    ИЛИ `pending_family_id = F` ИЛИ (`work_family_id IS NULL` И `pending_family_id IS NULL` И есть
    опубликованное предложение семьи `F` с `decision IS NULL`)). Решения
    `accepted`, `auto_accepted`, `auto_pending`, `accepted_pending` покрыты
    первыми двумя ветвями ровно тогда, когда текущая или ожидаемая семья
    контекста — `F`; `other_family`, `family_created`, `rejected`,
    `auto_superseded` не входят. `schema_id` в материал только переносится.

    Raises:
        SubjectNotRenderable: семьи нет.
    """
    unit = aliased(UnitOfMeasure)
    family = db.execute(
        sa.select(WorkFamily.title, WorkFamily.definition, unit.code.label("unit_code"))
        .outerjoin(unit, unit.id == WorkFamily.unit_id)
        .where(WorkFamily.id == family_id)
    ).one_or_none()
    if family is None:
        raise SubjectNotRenderable(f"семья {family_id} не найдена")

    awaiting_publication = sa.exists().where(
        FamilySuggestion.context_id == CatalogContext.id,
        FamilySuggestion.is_published.is_(True),
        FamilySuggestion.family_id == family_id,
        FamilySuggestion.decision.is_(None),
    )
    titles = db.execute(
        sa.select(CatalogPosition.standard_job_title)
        .select_from(CatalogContext)
        .join(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
        .join(CatalogPosition, CatalogPosition.id == ContextBucket.catalog_position_id)
        .where(
            CatalogContext.archived_at.is_(None),
            sa.or_(
                CatalogContext.work_family_id == family_id,
                CatalogContext.pending_family_id == family_id,
                sa.and_(
                    CatalogContext.work_family_id.is_(None),
                    CatalogContext.pending_family_id.is_(None),
                    awaiting_publication,
                ),
            ),
        )
    ).scalars().all()
    return SchemaRequestMaterial(
        family_id=family_id,
        schema_id=schema_id,
        title=family.title,
        unit_code=family.unit_code,
        definition=family.definition,
        names=tuple(sorted(set(titles))),
    )


def load_values_material(
    db: Session, context_ids: Collection[int]
) -> dict[int, ValuesRequestMaterial]:
    """Материал заданий `context_values` для множества контекстов постоянным
    числом запросов.

    Схема — текущая (`frozen`) версия семьи `COALESCE(pending_family_id,
    work_family_id)` контекста. Контекст без такой семьи или без текущей версии
    в результат не попадает (отсутствие ключа). Наименование, единица и статья —
    те же, что у `semantic_request.load_request_material`; пути — по ВСЕМ
    членствам контекста независимо от `membership_state`, одним и тем же
    `chapter_paths`; членства без раздела (пустой путь) в список не входят.
    Контекст, у которого хотя бы одно членство идёт через цикл в цепочке
    разделов, в результат не попадает (отсутствие ключа): путь не вычислим, и
    контекст неприменим по спеке §2.5.
    """
    ids = list(dict.fromkeys(context_ids))
    if not ids:
        return {}

    family_of = {
        row.id: row.family_id
        for row in db.execute(
            sa.select(
                CatalogContext.id,
                sa.func.coalesce(
                    CatalogContext.pending_family_id, CatalogContext.work_family_id
                ).label("family_id"),
            ).where(CatalogContext.id.in_(ids))
        ).all()
        if row.family_id is not None
    }
    if not family_of:
        return {}

    schema_of_family = {
        row.family_id: row.id
        for row in db.execute(
            sa.select(FamilyParameterSchema.id, FamilyParameterSchema.family_id).where(
                FamilyParameterSchema.family_id.in_(set(family_of.values())),
                FamilyParameterSchema.status == SchemaStatus.frozen.value,
            )
        ).all()
    }
    renderable = [cid for cid, fid in family_of.items() if fid in schema_of_family]
    if not renderable:
        return {}

    schema_ids = set(schema_of_family.values())
    parameters_of: dict[int, list[tuple[int, int, str]]] = {sid: [] for sid in schema_ids}
    for row in db.execute(
        sa.select(
            FamilyParameter.id, FamilyParameter.schema_id, FamilyParameter.ordinal,
            FamilyParameter.name,
        )
        .where(FamilyParameter.schema_id.in_(schema_ids))
        .order_by(FamilyParameter.schema_id, FamilyParameter.ordinal)
    ).all():
        parameters_of[row.schema_id].append((row.id, row.ordinal, row.name))

    parameter_ids = [pid for params in parameters_of.values() for pid, _o, _n in params]
    values_of: dict[int, list[str]] = {pid: [] for pid in parameter_ids}
    if parameter_ids:
        for row in db.execute(
            sa.select(FamilyParameterValue.parameter_id, FamilyParameterValue.value)
            .where(
                FamilyParameterValue.parameter_id.in_(parameter_ids),
                FamilyParameterValue.merged_into_id.is_(None),
            )
            .order_by(FamilyParameterValue.parameter_id, FamilyParameterValue.id)
        ).all():
            values_of[row.parameter_id].append(row.value)

    schemas = {
        sid: tuple(
            SchemaParameterIn(ordinal=ordinal, name=name, values=tuple(values_of[pid]))
            for pid, ordinal, name in params
        )
        for sid, params in parameters_of.items()
    }

    requests = load_request_material(db, renderable)
    result: dict[int, ValuesRequestMaterial] = {}
    for cid in renderable:
        request = requests[cid]
        if request.path_broken:
            continue
        schema_id = schema_of_family[family_of[cid]]
        non_empty = tuple((path, count) for path, count in request.path_counts if path)
        result[cid] = ValuesRequestMaterial(
            context_id=cid,
            schema_id=schema_id,
            title=request.title,
            unit_code=request.unit_code,
            article=request.article,
            paths=top_paths(non_empty),
            parameters=schemas[schema_id],
        )
    return result


# ---------------------------------------------------------------------------
#  Рендер
# ---------------------------------------------------------------------------

def _unit_display(unit_code: str | None) -> str:
    return unit_code if unit_code is not None else _NO_UNIT_DISPLAY


def _schema_user_text(material: SchemaRequestMaterial, names: Sequence[str]) -> str:
    listing = "\n".join(f"{i}. {name}" for i, name in enumerate(names, 1))
    return (
        f"СЕМЬЯ: {material.title}\nЕДИНИЦА: {_unit_display(material.unit_code)}\n"
        f"ОПРЕДЕЛЕНИЕ: {material.definition}\n\nСТРОКИ:\n{listing}"
    )


def _values_user_text(material: ValuesRequestMaterial, paths: Sequence[str]) -> str:
    article = material.article or "(не определена)"
    head = (
        f"СТРОКА\nСтатья: {article}\nНаименование: {material.title}\n"
        f"Единица: {_unit_display(material.unit_code)}\n"
    )
    if not paths:
        return head + "Пути разделов: (разделы не указаны)"
    listing = "\n".join(f"{i}. {path}" for i, path in enumerate(paths, 1))
    return head + f"Пути разделов (от самого частого):\n{listing}"


def _schema_block_text(parameters: Sequence[SchemaParameterIn]) -> str:
    if not parameters:
        return f"{SCHEMA_BLOCK_HEADER}(параметров нет)"
    parts = [
        f"Параметр {p.ordinal}: {p.name}\nЗначения:\n" + "\n".join(f"- {v}" for v in p.values)
        for p in parameters
    ]
    return SCHEMA_BLOCK_HEADER + "\n\n".join(parts)


def _finish(
    *,
    model: str,
    max_tokens: int,
    reasoning_effort: str,
    response_format: dict,
    system_blocks: list[dict],
    user_text: str,
    candidates_snapshot: object,
) -> RenderedRequest:
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "reasoning": {"effort": reasoning_effort},
        "usage": {"include": True},
        "response_format": copy.deepcopy(response_format),
        "messages": [
            {"role": "system", "content": system_blocks},
            {"role": "user", "content": user_text},
        ],
    }
    return RenderedRequest(
        body=body,
        request_hash=_sha256_hex({"serialization_version": SERIALIZATION_VERSION, "body": body}),
        prefix_hash=_sha256_hex(
            {"serialization_version": SERIALIZATION_VERSION, "model": model, "system": system_blocks}
        ),
        candidates_hash=_sha256_hex(candidates_snapshot),
        input_hash=_sha256_hex({"serialization_version": SERIALIZATION_VERSION, "user": user_text}),
        prefix_bytes=sum(len(block["text"].encode("utf-8")) for block in system_blocks),
        user_bytes=len(user_text.encode("utf-8")),
        place_dictionary_version=PLACE_DICTIONARY_VERSION,
    )


def render_schema_request(
    material: SchemaRequestMaterial, *, settings: Settings
) -> RenderedRequest:
    """Тело запроса `family_schema`: system — один блок промпта с меткой кэша;
    user — семья и пронумерованный список наименований. `candidates_hash` —
    sha256 списка имён. Детерминирован, в базу не ходит; имена сортируются
    внутри, поэтому порядок на входе на результат не влияет."""
    names = sorted(material.names)
    return _finish(
        model=settings.SEMANTIC_SCHEMA_MODEL,
        max_tokens=settings.SEMANTIC_SCHEMA_MAX_TOKENS,
        reasoning_effort=settings.SEMANTIC_VARIANTS_REASONING_EFFORT,
        response_format=SCHEMA_RESPONSE_FORMAT,
        system_blocks=[
            {"type": "text", "text": SCHEMA_PROMPT, "cache_control": {"type": "ephemeral"}}
        ],
        user_text=_schema_user_text(material, names),
        candidates_snapshot=names,
    )


def render_values_request(
    material: ValuesRequestMaterial, *, settings: Settings
) -> RenderedRequest:
    """Тело запроса `context_values`: system — блок промпта и блок схемы (метка
    кэша на блоке схемы: префикс общий для всех контекстов версии); user —
    наименование, единица, статья и до десяти путей. `candidates_hash` —
    sha256 схемы. Детерминирован, в базу не ходит; параметры идут по `ordinal`,
    значения — в порядке материала (по `id`), путей берётся не больше
    `MAX_PATHS`."""
    parameters = sorted(material.parameters, key=lambda p: p.ordinal)
    paths = material.paths[:MAX_PATHS]
    return _finish(
        model=settings.SEMANTIC_VALUES_MODEL,
        max_tokens=settings.SEMANTIC_VALUES_MAX_TOKENS,
        reasoning_effort=settings.SEMANTIC_VARIANTS_REASONING_EFFORT,
        response_format=VALUES_RESPONSE_FORMAT,
        system_blocks=[
            {"type": "text", "text": VALUES_PROMPT},
            {
                "type": "text",
                "text": _schema_block_text(parameters),
                "cache_control": {"type": "ephemeral"},
            },
        ],
        user_text=_values_user_text(material, paths),
        candidates_snapshot=[
            {"ordinal": p.ordinal, "name": p.name, "values": list(p.values)} for p in parameters
        ],
    )


def render_request_for(db: Session, job: SemanticJob, *, settings: Settings) -> RenderedRequest:
    """Рендерит ТЕКУЩИЙ материал предмета задания: `family_schema` —
    `load_schema_material(job.family_id, job.schema_id)`; `context_values` —
    `load_values_material([job.context_id])`; `family_suggestion` — путь
    «Семантических предложений» (`load_request_material` +
    `render_context_request`). Совпадение `job.schema_id` с текущей версией
    рендер не проверяет.

    Raises:
        SubjectNotRenderable: у предмета сейчас нет материала.
        ValueError: неизвестный вид задания.
    """
    if job.kind == SemanticJobKind.family_schema.value:
        return render_schema_request(
            load_schema_material(db, job.family_id, job.schema_id), settings=settings
        )
    if job.kind == SemanticJobKind.context_values.value:
        material = load_values_material(db, [job.context_id]).get(job.context_id)
        if material is None:
            raise SubjectNotRenderable(f"у контекста {job.context_id} нет схемы для значений")
        return render_values_request(material, settings=settings)
    if job.kind == SemanticJobKind.family_suggestion.value:
        request = load_request_material(db, [job.context_id]).get(job.context_id)
        if request is None:
            raise SubjectNotRenderable(f"контекст {job.context_id} не найден")
        return render_context_request(request, settings=settings)
    raise ValueError(f"неизвестный вид задания: {job.kind!r}")
