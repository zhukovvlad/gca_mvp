"""Открытие семей: охват единицы и тело запроса (спека 3б §2.3).

Задание «открытие семей» отдаёт модели имена единицы целиком и просит свести их
в черновики семей, отнести к активным семьям, отделить «не работу» и предложить
категории семьям без категории. Модуль решает две вещи: какие контексты в охвате
единицы и каким будет тело запроса.

Охват — ОДИН предикат (`_scope_query`): блок «Открыть семьи», preview, тело
запроса и вердикт результата берут контексты из него, а не из своих копий
(`docs/insights/same-model-for-measurement-and-screen.md`). `discovery_scope`
считает охват единицы, `is_in_scope` — охват произвольного набора контекстов;
оба строят один и тот же запрос и различаются только отбором.

Тело строит чистая `build_discovery_request` на заранее собранном материале;
`render_discovery_request` загружает материал по охвату и зовёт её. Наружу
уходят только наименования, статьи, пути разделов, единица, активные семьи
(имя, определение) и справочник категорий: цены, объёмы, подрядчики, договоры и
объекты в тело не входят (`AGENTS.md` §3).
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from config import Settings
from models import (
    CatalogContext,
    CatalogKind,
    CatalogPosition,
    ContextBucket,
    ContextMember,
    FamilyCategory,
    FamilyStatus,
    FamilySuggestion,
    ReconcileBatchStatus,
    SemanticJob,
    SemanticJobKind,
    SemanticJobStatus,
    SemanticKind,
    SemanticReconcileBatch,
    SemanticState,
    UnitOfMeasure,
    WorkFamily,
)
from services.semantic_cost import (
    RESERVE_FORMULA_VERSION,
    expected_cached_cost_known_prefix,
    known_prefix_tokens,
    reserve_for_known_prefix,
    tariffs_from,
)
from services.semantic_reconcile import _open_suggestion_state
from services.semantic_request import (
    FAMILY_BLOCK_HEADER,
    SERIALIZATION_VERSION,
    CandidateFamily,
    RenderedRequest,
    _collapse_whitespace,
    _sha256_hex,
    family_line,
    load_request_material,
    top_path,
)
from services.semantic_rules import PLACE_DICTIONARY_VERSION
from services.variant_answer import (
    DISCOVERY_RESPONSE_FORMAT,
    DISCOVERY_RESPONSE_SCHEMA_VERSION,
    DiscoverySent,
)

#: Версия текста промпта открытия; метка на задании — `discovery:<версия>`.
DISCOVERY_PROMPT_VERSION = 1

#: Текст промпта открытия (спека 3б §2.3): определение семьи то же, что у
#: предложений; правила — свести имена в черновики, не заводить дубль активной
#: семьи, затраты с предметом и ценой отнести к семьям категории, «не работа» —
#: только строки без собственного предмета, предложить категорию семьям без неё.
#: Смена текста обязана сопровождаться инкрементом `DISCOVERY_PROMPT_VERSION`.
DISCOVERY_PROMPT = """Ты сметчик-каталогизатор строительной компании-заказчика. У компании есть каталог СЕМЕЙ работ: семья — это тип работы без параметров, бренда и места (например «Устройство пола», «Посадка растений», «Двери»). Внутри семьи строки различаются параметрами (класс бетона, размер, вид растения) — это не мешает им быть одной семьёй. У каждой семьи есть категория из справочника КАТЕГОРИЙ СЕМЕЙ.

Тебе дают единицу измерения, справочник категорий, список активных семей этой единицы, перечень активных семей без категории и пронумерованные наименования строк ведомости (с статьями и разделами). Ответь строгим JSON по заданной схеме, без пояснений.

Правила:
- Своди наименования в группы: одна группа — одна семья. Новая семья (family_id = null): дай имя — тип работы без параметров, бренда и места, определение в одно-два предложения и category_id из справочника категорий.
- Наименования, которые уже описывает активная семья списка, относи к ней: family_id — её номер, остальные поля группы (title, definition, category_id, similar_family_id) — null. Не заводи дубль активной семьи. Одна активная семья — не больше одной группы.
- Если новая семья похожа на активную, но не совпадает с ней, укажи номер активной семьи в similar_family_id; иначе null.
- Затраты с собственным предметом и ценой (материалы, перевозка, услуги, комплекты) — семьи той категории, чьё определение им подходит по справочнику, а не «не работа».
- «Не работа» (not_work) — только строки без собственного предмета: примечания, оговорки, заголовки.
- Каждое наименование — не больше чем в одном месте: в одной группе либо в not_work. Используй только номера наименований из перечня.
- Для каждой активной семьи из перечня «СЕМЬИ БЕЗ КАТЕГОРИИ» предложи категорию из справочника в family_categories."""

#: Блок справочника категорий в `system` (спека 3б §2.3).
CATEGORY_BLOCK_HEADER = "КАТЕГОРИИ СЕМЕЙ:\n"

#: Сколько статей показывается у имени, и предельная длина пути в строке имени.
MAX_ARTICLES_PER_NAME = 3
PATH_MAX_CHARS = 200

#: Пометки для пустых полей строки имени в теле.
_NO_UNIT_DISPLAY = "без единицы"
_NO_ARTICLES = "(не определены)"
_NO_PATH = "(не указаны)"
_NO_UNCATEGORIZED = "нет"

_SCOPE_CATALOG_KINDS = (CatalogKind.TO_REVIEW.value, CatalogKind.POSITION.value)


# ---------------------------------------------------------------------------
#  Структуры
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DiscoveryName:
    """Одна строка тела запроса: имя и все контексты охвата с этим наименованием
    (контексты разных статей и корзин с тем же наименованием — одна строка)."""

    index: int
    title: str
    context_ids: tuple[int, ...]
    articles: tuple[str, ...]
    path: str


@dataclass(frozen=True)
class ScopeCounts:
    """Состав охвата по контекстам (взаимоисключающе, в порядке убывания
    приоритета): системы; работы с предложением «новая семья»; «голые» — прочие
    (единица без активных семей). Плюс число имён и число активных семей единицы
    без категории."""

    systems: int
    new_family: int
    bare: int
    names: int
    uncategorized_families: int


@dataclass(frozen=True)
class DiscoveryScope:
    unit_id: int | None
    unit_code: str | None
    names: tuple[DiscoveryName, ...]
    context_ids: frozenset[int]
    active_family_ids: tuple[int, ...]
    uncategorized_family_ids: tuple[int, ...]
    category_ids: tuple[int, ...]
    counts: ScopeCounts


@dataclass(frozen=True)
class DiscoveryCategory:
    """Строка справочника категорий в теле запроса."""

    id: int
    title: str
    definition: str


# ---------------------------------------------------------------------------
#  Охват: один предикат
# ---------------------------------------------------------------------------

def _open_suggestion(*, existing_family: bool):
    """Опубликованное предложение контекста без решения: на существующую семью
    или «новая семья» (`family_id IS NULL`)."""
    family_condition = (
        FamilySuggestion.family_id.is_not(None) if existing_family else FamilySuggestion.family_id.is_(None)
    )
    return sa.exists().where(
        FamilySuggestion.context_id == CatalogContext.id,
        FamilySuggestion.is_published.is_(True),
        FamilySuggestion.decision.is_(None),
        family_condition,
    )


def _scope_query(*columns):
    """Запрос, возвращающий `columns` контекстов охвата. Предикат (спека 3б
    §2.3) записан здесь и только здесь:

    контекст не архивирован, имеет членство, не `NOT_APPLICABLE`, без семьи и
    без ожидающей семьи, строка каталога — `TO_REVIEW` или `POSITION`; нет
    опубликованного предложения без решения на существующую семью; и при этом
    он система, либо у него предложение «новая семья», либо в единице его
    строки нет активной семьи. `path_broken` охват не меняет."""
    has_member = sa.exists().where(ContextMember.context_id == CatalogContext.id)
    unit_has_active_family = sa.exists().where(
        WorkFamily.status == FamilyStatus.active.value,
        WorkFamily.unit_id.is_not_distinct_from(CatalogPosition.unit_id),
    )
    return (
        sa.select(*columns)
        .select_from(CatalogContext)
        .join(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
        .join(CatalogPosition, CatalogPosition.id == ContextBucket.catalog_position_id)
        .where(
            CatalogContext.archived_at.is_(None),
            has_member,
            CatalogContext.semantic_state != SemanticState.NOT_APPLICABLE.value,
            CatalogContext.work_family_id.is_(None),
            CatalogContext.pending_family_id.is_(None),
            CatalogPosition.kind.in_(_SCOPE_CATALOG_KINDS),
            ~_open_suggestion(existing_family=True),
            sa.or_(
                CatalogContext.semantic_kind == SemanticKind.SYSTEM.value,
                _open_suggestion(existing_family=False),
                ~unit_has_active_family,
            ),
        )
    )


def is_in_scope(db: Session, context_ids: Iterable[int]) -> frozenset[int]:
    """Какие из `context_ids` в охвате своей единицы сейчас — тот же предикат,
    что у `discovery_scope`, для потребителей снимка черновиков."""
    ids = list(dict.fromkeys(context_ids))
    if not ids:
        return frozenset()
    rows = db.execute(_scope_query(CatalogContext.id).where(CatalogContext.id.in_(ids))).scalars()
    return frozenset(rows)


def _cut(text: str, limit: int) -> str:
    """Не длиннее `limit` знаков: длиннее — режется, последний знак «…»."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _top_articles(counts: Mapping[str, int]) -> tuple[str, ...]:
    """Не больше трёх статей: по числу контекстов (больше — раньше), затем по
    названию."""
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return tuple(article for article, _count in ordered[:MAX_ARTICLES_PER_NAME])


def discovery_scope(db: Session, unit_id: int | None) -> DiscoveryScope:
    """Охват единицы `unit_id` (`None` — единица «без единицы»): контексты,
    имена с номерами `1..N`, активные семьи единицы, справочник и счётчики."""
    rows = db.execute(
        _scope_query(
            CatalogContext.id,
            CatalogContext.semantic_kind,
            CatalogPosition.standard_job_title,
            _open_suggestion(existing_family=False).label("has_new_family"),
        )
        .where(CatalogPosition.unit_id.is_not_distinct_from(unit_id))
        .order_by(CatalogContext.id)
    ).all()
    context_ids = frozenset(row.id for row in rows)
    materials = load_request_material(db, context_ids)

    by_title: dict[str, list[int]] = {}
    for row in rows:
        by_title.setdefault(row.standard_job_title, []).append(row.id)

    names: list[DiscoveryName] = []
    for index, title in enumerate(sorted(by_title, key=lambda t: t.encode("utf-8")), start=1):
        ids = by_title[title]
        paths: Counter[str] = Counter()
        articles: Counter[str] = Counter()
        for context_id in ids:
            material = materials[context_id]
            for path, count in material.path_counts:
                paths[path] += count
            if material.article is not None:
                articles[material.article] += 1
        path = top_path(tuple(paths.items())) if paths else ""
        names.append(
            DiscoveryName(
                index=index,
                title=title,
                context_ids=tuple(ids),
                articles=_top_articles(articles),
                path=_cut(path, PATH_MAX_CHARS),
            )
        )

    families = db.execute(
        sa.select(WorkFamily.id, WorkFamily.family_category_id)
        .where(
            WorkFamily.status == FamilyStatus.active.value,
            WorkFamily.unit_id.is_not_distinct_from(unit_id),
        )
        .order_by(WorkFamily.id)
    ).all()
    uncategorized = tuple(f.id for f in families if f.family_category_id is None)
    category_ids = tuple(
        db.execute(sa.select(FamilyCategory.id).order_by(FamilyCategory.id)).scalars()
    )
    unit_code = (
        None
        if unit_id is None
        else db.execute(
            sa.select(UnitOfMeasure.code).where(UnitOfMeasure.id == unit_id)
        ).scalar_one_or_none()
    )

    systems = sum(1 for r in rows if r.semantic_kind == SemanticKind.SYSTEM.value)
    new_family = sum(
        1 for r in rows if r.semantic_kind != SemanticKind.SYSTEM.value and r.has_new_family
    )
    counts = ScopeCounts(
        systems=systems,
        new_family=new_family,
        bare=len(rows) - systems - new_family,
        names=len(names),
        uncategorized_families=len(uncategorized),
    )
    return DiscoveryScope(
        unit_id=unit_id,
        unit_code=unit_code,
        names=tuple(names),
        context_ids=context_ids,
        active_family_ids=tuple(f.id for f in families),
        uncategorized_family_ids=uncategorized,
        category_ids=category_ids,
        counts=counts,
    )


# ---------------------------------------------------------------------------
#  Тело запроса
# ---------------------------------------------------------------------------

def _name_line(name: DiscoveryName, *, max_chars: int) -> str:
    title = _cut(_collapse_whitespace(name.title), max_chars)
    articles = "; ".join(name.articles[:MAX_ARTICLES_PER_NAME]) or _NO_ARTICLES
    path = _cut(_collapse_whitespace(name.path), PATH_MAX_CHARS) or _NO_PATH
    return f"{name.index}. {title} | статьи: {articles} | разделы: {path}"


def _user_text(
    unit_code: str | None,
    names: Sequence[DiscoveryName],
    uncategorized_family_ids: Iterable[int],
    *,
    max_chars: int,
) -> str:
    unit_display = unit_code if unit_code is not None else _NO_UNIT_DISPLAY
    uncategorized = ", ".join(str(i) for i in sorted(uncategorized_family_ids)) or _NO_UNCATEGORIZED
    listing = "\n".join(_name_line(n, max_chars=max_chars) for n in sorted(names, key=lambda n: n.index))
    return (
        f"ЕДИНИЦА: {unit_display}\nСЕМЬИ БЕЗ КАТЕГОРИИ: {uncategorized}\nИМЕНА:\n{listing}"
    )


def build_discovery_request(
    *,
    unit_code: str | None,
    names: Sequence[DiscoveryName],
    families: Sequence[CandidateFamily],
    categories: Sequence[DiscoveryCategory],
    uncategorized_family_ids: Iterable[int],
    settings: Settings,
) -> RenderedRequest:
    """Каноническое тело запроса открытия и его отпечатки (спека 3б §2.3).

    `system` — три блока: промпт; справочник категорий; активные семьи единицы
    строками `family_line` под `FAMILY_BLOCK_HEADER` (метка кэша на последнем
    блоке: кэшируется весь префикс). Справочник вынесен из блока семей: проверка
    приватности разбирает блок под заголовком построчно, и строка категории
    стала бы строкой семьи с чужим номером. `user` — единица, перечень семей без
    категории и пронумерованные имена. Детерминирован, в базу не ходит: порядок
    семей, категорий и перечня на входе результат не меняет."""
    ordered_families = tuple(sorted(families, key=lambda f: f.id))
    ordered_categories = tuple(sorted(categories, key=lambda c: c.id))
    user_text = _user_text(
        unit_code,
        names,
        uncategorized_family_ids,
        max_chars=settings.SEMANTIC_DISCOVERY_NAME_MAX_CHARS,
    )
    categories_text = CATEGORY_BLOCK_HEADER + "\n".join(
        f"{c.id}. {_collapse_whitespace(c.title)} — {_collapse_whitespace(c.definition)}"
        for c in ordered_categories
    )
    families_text = FAMILY_BLOCK_HEADER + "\n".join(family_line(f) for f in ordered_families)
    system_blocks: list[dict] = [
        {"type": "text", "text": DISCOVERY_PROMPT},
        {"type": "text", "text": categories_text},
        {"type": "text", "text": families_text, "cache_control": {"type": "ephemeral"}},
    ]
    body = {
        "model": settings.SEMANTIC_DISCOVERY_MODEL,
        "temperature": 0,
        "max_tokens": settings.SEMANTIC_DISCOVERY_MAX_TOKENS,
        "reasoning": {"effort": settings.SEMANTIC_DISCOVERY_REASONING_EFFORT},
        "usage": {"include": True},
        "response_format": copy.deepcopy(DISCOVERY_RESPONSE_FORMAT),
        "messages": [
            {"role": "system", "content": system_blocks},
            {"role": "user", "content": user_text},
        ],
    }
    candidates_snapshot = {
        "families": [
            {"id": f.id, "title": f.title, "unit_code": f.unit_code, "definition": f.definition}
            for f in ordered_families
        ],
        "categories": [
            {"id": c.id, "title": c.title, "definition": c.definition} for c in ordered_categories
        ],
    }
    return RenderedRequest(
        body=body,
        request_hash=_sha256_hex({"serialization_version": SERIALIZATION_VERSION, "body": body}),
        prefix_hash=_sha256_hex(
            {
                "serialization_version": SERIALIZATION_VERSION,
                "model": settings.SEMANTIC_DISCOVERY_MODEL,
                "system": system_blocks,
            }
        ),
        candidates_hash=_sha256_hex(candidates_snapshot),
        input_hash=_sha256_hex({"serialization_version": SERIALIZATION_VERSION, "user": user_text}),
        prefix_bytes=sum(len(block["text"].encode("utf-8")) for block in system_blocks),
        user_bytes=len(user_text.encode("utf-8")),
        place_dictionary_version=PLACE_DICTIONARY_VERSION,
    )


def render_discovery_request(
    scope: DiscoveryScope, db: Session, *, settings: Settings
) -> RenderedRequest:
    """Тело запроса по готовому охвату: семьи и справочник читаются из базы по
    id охвата, имена и перечень «без категории» берутся из охвата."""
    unit = aliased(UnitOfMeasure)
    family_rows = db.execute(
        sa.select(WorkFamily.id, WorkFamily.title, WorkFamily.definition, unit.code)
        .outerjoin(unit, unit.id == WorkFamily.unit_id)
        .where(WorkFamily.id.in_(scope.active_family_ids))
        .order_by(WorkFamily.id)
    ).all()
    category_rows = db.execute(
        sa.select(FamilyCategory.id, FamilyCategory.title, FamilyCategory.definition)
        .where(FamilyCategory.id.in_(scope.category_ids))
        .order_by(FamilyCategory.id)
    ).all()
    return build_discovery_request(
        unit_code=scope.unit_code,
        names=scope.names,
        families=[
            CandidateFamily(id=r.id, title=r.title, unit_code=r.code, definition=r.definition)
            for r in family_rows
        ],
        categories=[
            DiscoveryCategory(id=r.id, title=r.title, definition=r.definition)
            for r in category_rows
        ],
        uncategorized_family_ids=scope.uncategorized_family_ids,
        settings=settings,
    )


def sent_of(scope: DiscoveryScope) -> DiscoverySent:
    """Что уходит модели по этому охвату — множества, по которым разбор ответа
    проверяет ссылки."""
    return DiscoverySent(
        names_count=len(scope.names),
        active_family_ids=frozenset(scope.active_family_ids),
        uncategorized_family_ids=frozenset(scope.uncategorized_family_ids),
        category_ids=frozenset(scope.category_ids),
    )


# ---------------------------------------------------------------------------
#  Preview и запуск
# ---------------------------------------------------------------------------

REFUSE_DISCOVERY_IN_PROGRESS = "discovery_in_progress"
REFUSE_DISCOVERY_UNIT_BUSY = "discovery_unit_busy"
REFUSE_DISCOVERY_NOTHING_TO_DO = "discovery_nothing_to_do"
REFUSE_DISCOVERY_TOO_MANY_NAMES = "discovery_too_many_names"
REFUSE_DISCOVERY_INPUT_UNCHANGED = "discovery_input_unchanged"
#: Существующий код протокола preview (`semantic_decisions.CODE_PREVIEW_CHANGED`).
REFUSE_PREVIEW_CHANGED = "preview_changed"

#: Метка версии промпта на задании открытия.
DISCOVERY_PROMPT_LABEL = f"discovery:{DISCOVERY_PROMPT_VERSION}"

_LIVE_STATUSES = (
    SemanticJobStatus.pending.value,
    SemanticJobStatus.running.value,
    SemanticJobStatus.privacy_hold.value,
)
_UQ_DISCOVERY_LIVE = "uq_semantic_jobs_discovery_live"
_UQ_SUBJECT_REQUEST = "uq_semantic_jobs_subject_request_hash"


class DiscoveryError(Exception):
    """Отказ запуска открытия: `code` — машинный ключ, текст — для оператора
    (спека 3б §2.12). Всё `409`; HTTP-слой берёт статус из карты кодов."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DiscoveryPreview:
    unit_id: int | None
    counts: ScopeCounts
    active_families: int
    reserve_usd: Decimal
    expected_cached_usd: Decimal
    preview_hash: str


def _unit_label(scope: DiscoveryScope) -> str:
    return scope.unit_code if scope.unit_code is not None else _NO_UNIT_DISPLAY


def _tariffs_payload(settings: Settings) -> dict[str, str]:
    tariffs = tariffs_from(settings, SemanticJobKind.family_discovery)
    return {
        "input_per_m": str(tariffs.input_per_m),
        "cache_write_per_m": str(tariffs.cache_write_per_m),
        "cache_read_per_m": str(tariffs.cache_read_per_m),
        "output_per_m": str(tariffs.output_per_m),
    }


def estimate_of(
    db: Session, rendered: RenderedRequest, settings: Settings
) -> tuple[Decimal, Decimal]:
    """Резерв и ожидаемая цена при попадании в кэш — формула резерва предложений
    с тарифами открытия; у наблюдавшегося префикса — его токены, иначе байты."""
    tariffs = tariffs_from(settings, SemanticJobKind.family_discovery)
    known = known_prefix_tokens(db, rendered.prefix_hash)
    prefix_tokens = known if known is not None else rendered.prefix_bytes
    reserve = reserve_for_known_prefix(
        prefix_tokens, rendered, tariffs, rendered.body["max_tokens"]
    )
    cached = expected_cached_cost_known_prefix(prefix_tokens, rendered, tariffs)
    return reserve, cached


def _preview_hash(
    rendered: RenderedRequest, reserve: Decimal, cached: Decimal, settings: Settings
) -> str:
    canonical = json.dumps(
        {
            "request_hash": rendered.request_hash,
            "reserve_usd": str(reserve),
            "cached_usd": str(cached),
            "tariffs": {SemanticJobKind.family_discovery.value: _tariffs_payload(settings)},
            "reserve_formula_version": RESERVE_FORMULA_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _preview_of(
    db: Session, scope: DiscoveryScope, settings: Settings
) -> tuple[DiscoveryPreview, RenderedRequest]:
    rendered = render_discovery_request(scope, db, settings=settings)
    reserve, cached = estimate_of(db, rendered, settings)
    preview = DiscoveryPreview(
        unit_id=scope.unit_id,
        counts=scope.counts,
        active_families=len(scope.active_family_ids),
        reserve_usd=reserve,
        expected_cached_usd=cached,
        preview_hash=_preview_hash(rendered, reserve, cached, settings),
    )
    return preview, rendered


def preview_discovery(
    db: Session, *, unit_id: int | None, settings: Settings
) -> DiscoveryPreview:
    """Оценка открытия единицы и `preview_hash`: sha256 `{request_hash, резерв,
    ожидаемая цена, тарифы вида, версия формулы резерва}`. Отказов не бросает:
    их решает запуск по состоянию на свой момент."""
    return _preview_of(db, discovery_scope(db, unit_id), settings)[0]


def _held_suggestion_contexts_in_unit(db: Session, unit_id: int | None) -> int:
    """Контексты единицы в отпечатках предложений удержанных пачек."""
    held_contexts: set[int] = set()
    for (held,) in db.execute(
        sa.select(SemanticReconcileBatch.held_fingerprints).where(
            SemanticReconcileBatch.status == ReconcileBatchStatus.held.value
        )
    ):
        held_contexts |= {
            element["context_id"]
            for element in held
            if isinstance(element, dict)
            and element.get("kind") == SemanticJobKind.family_suggestion.value
        }
    if not held_contexts:
        return 0
    return db.execute(
        sa.select(sa.func.count(CatalogContext.id))
        .select_from(CatalogContext)
        .join(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
        .join(CatalogPosition, CatalogPosition.id == ContextBucket.catalog_position_id)
        .where(
            CatalogContext.id.in_(held_contexts),
            CatalogPosition.unit_id.is_not_distinct_from(unit_id),
        )
    ).scalar_one()


def _busy_count(db: Session, unit_id: int | None) -> int:
    running = db.execute(
        sa.select(sa.func.count(SemanticJob.id)).where(
            SemanticJob.kind == SemanticJobKind.family_suggestion.value,
            SemanticJob.status.in_(
                (SemanticJobStatus.pending.value, SemanticJobStatus.running.value)
            ),
            SemanticJob.unit_id.is_not_distinct_from(unit_id),
        )
    ).scalar_one()
    return running + _held_suggestion_contexts_in_unit(db, unit_id)


def launch_discovery(
    db: Session,
    *,
    unit_id: int | None,
    preview_hash: str,
    actor_id: int,
    settings: Settings,
) -> SemanticJob:
    """Создаёт задание `family_discovery` в `pending` (спека 3б §2.3): вне
    потолка события, без пачки. Отказы по порядку: живое открытие единицы;
    перезапрос единицы ещё идёт; открывать нечего; имён больше предела; тот же
    вход уже открывали; `preview_hash` устарел. Охват пуст, а семьи без категории
    есть — запуск проходит («только категории»). Частичный UNIQUE живого
    открытия и ключ входа — вторая линия: гонка двух запусков даёт те же коды.
    `actor_id` в задание не пишется: у задания нет автора, решение оператора
    видно по самому факту запуска.

    Raises:
        DiscoveryError: код из `REFUSE_DISCOVERY_*` или `preview_changed`.
    """
    scope = discovery_scope(db, unit_id)
    label = _unit_label(scope)
    unit_clause = SemanticJob.unit_id.is_not_distinct_from(unit_id)
    kind = SemanticJobKind.family_discovery.value
    in_progress = DiscoveryError(
        REFUSE_DISCOVERY_IN_PROGRESS,
        f"Открытие семей для единицы «{label}» уже идёт — дождитесь результата или "
        "разберите задержанное.",
    )
    unchanged = DiscoveryError(
        REFUSE_DISCOVERY_INPUT_UNCHANGED,
        f"С прошлого открытия единицы «{label}» ничего не изменилось — его черновики и "
        "есть ответ. Правьте, сливайте или отбрасывайте их.",
    )

    live = db.execute(
        sa.select(SemanticJob.id)
        .where(SemanticJob.kind == kind, SemanticJob.status.in_(_LIVE_STATUSES), unit_clause)
        .limit(1)
    ).first()
    if live is not None:
        raise in_progress
    if unit_id in _open_suggestion_state(db)[1]:
        raise DiscoveryError(
            REFUSE_DISCOVERY_UNIT_BUSY,
            f"В единице «{label}» идёт перезапрос: {_busy_count(db, unit_id)} заданий "
            "предложений ещё не выполнены. Откройте семьи, когда он закончится, — иначе "
            "черновики устареют до прихода.",
        )
    if not scope.names and not scope.uncategorized_family_ids:
        raise DiscoveryError(
            REFUSE_DISCOVERY_NOTHING_TO_DO,
            f"В единице «{label}» нет строк без семьи и семей без категории — открывать "
            "нечего.",
        )
    if len(scope.names) > settings.SEMANTIC_DISCOVERY_MAX_NAMES:
        raise DiscoveryError(
            REFUSE_DISCOVERY_TOO_MANY_NAMES,
            f"В единице «{label}» {len(scope.names)} различных наименований без семьи — "
            f"больше предела {settings.SEMANTIC_DISCOVERY_MAX_NAMES} одного открытия.",
        )

    preview, rendered = _preview_of(db, scope, settings)
    already = db.execute(
        sa.select(SemanticJob.id)
        .where(SemanticJob.kind == kind, SemanticJob.request_hash == rendered.request_hash)
        .limit(1)
    ).first()
    if already is not None:
        raise unchanged
    if preview.preview_hash != preview_hash:
        raise DiscoveryError(
            REFUSE_PREVIEW_CHANGED,
            "Оценка устарела: охват единицы изменился после показа. Посмотрите оценку заново.",
        )

    job = SemanticJob(
        kind=kind,
        context_id=None,
        family_id=None,
        schema_id=None,
        request_hash=rendered.request_hash,
        status=SemanticJobStatus.pending.value,
        unit_id=unit_id,
        prompt_version=DISCOVERY_PROMPT_LABEL,
        model_requested=rendered.body["model"],
        place_dictionary_version=rendered.place_dictionary_version,
        candidates_hash=rendered.candidates_hash,
        prefix_hash=rendered.prefix_hash,
        input_hash=rendered.input_hash,
        response_schema_version=DISCOVERY_RESPONSE_SCHEMA_VERSION,
        serialization_version=str(SERIALIZATION_VERSION),
    )
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
    except IntegrityError as exc:
        name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if name == _UQ_DISCOVERY_LIVE:
            raise in_progress from exc
        if name == _UQ_SUBJECT_REQUEST:
            raise unchanged from exc
        raise
    return job
