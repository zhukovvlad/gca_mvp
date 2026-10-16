"""Открытие семей: охват единицы и тело запроса (спека 3б §2.3).

Задание «открытие семей» отдаёт модели имена единицы целиком и просит свести их
в черновики семей, отнести к активным семьям, отделить «не работу» и предложить
категории семьям без категории. Модуль решает две вещи: какие контексты в охвате
единицы и каким будет тело запроса.

Охват — ОДИН предикат (`_scope_query`): блок «Открыть семьи», preview, тело
запроса и вердикт результата берут контексты из него, а не из своих копий
(`docs/insights/same-model-for-measurement-and-screen.md`). `discovery_scope`
считает охват единицы, `is_in_scope` — охват произвольного набора контекстов;
оба строят один и тот же запрос и различаются только отбором. Потребители
снимка черновиков (экран, активация «не работы») зовут `is_in_scope` с
`why_in_scope=False`: без условия «почему в охвате», которое меняется от самой
активации (она заводит активные семьи единицы).

Тело строит чистая `build_discovery_request` на заранее собранном материале;
`render_discovery_request` загружает материал по охвату и зовёт её. Это
КАНОНИЧЕСКОЕ тело со всеми именами: по нему (и по отпечатку исполнения) считается
`request_hash`, проверяется приватность и охват. Модели имена уходят порциями
(спека 3б §2.3.1): `render_discovery_chunk` строит тело порции из канонического
тела и охвата, `chunk_bounds` режет номера имён; `render_discovery_cleanup` строит
тело дочистки пропущенных имён (спека 3б §2.3.2); резерв и ожидаемая цена
считаются по охвату и настройкам (`discovery_reserve`, `discovery_expected`). Наружу
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
from services.semantic_cost import RESERVE_FORMULA_VERSION, tariffs_from
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
    DRAFT_DEFINITION_MAX_CHARS,
    DRAFT_TITLE_MAX_CHARS,
    ChunkSent,
    DiscoveryAnswer,
)

#: Версия текста промпта открытия; метка на задании — `discovery:<версия>`.
DISCOVERY_PROMPT_VERSION = 2

#: Текст промпта открытия (спека 3б §2.3, §2.3.1): определение семьи то же, что у
#: предложений; правила — разложить каждое имя порции, свести имена в черновики,
#: не заводить дубль активной семьи и черновика (`draft_ref`), системы свести по
#: типу, затраты с предметом и ценой отнести к семьям категории, «не работа» —
#: только строки без собственного предмета, предложить категорию семьям без неё.
#: Смена текста обязана сопровождаться инкрементом `DISCOVERY_PROMPT_VERSION`.
DISCOVERY_PROMPT = """Ты сметчик-каталогизатор строительной компании-заказчика. У компании есть каталог СЕМЕЙ работ: семья — это тип работы без параметров, бренда и места (например «Устройство пола», «Посадка растений», «Двери»). Внутри семьи строки различаются параметрами (класс бетона, размер, вид растения) — это не мешает им быть одной семьёй. У каждой семьи есть категория из справочника КАТЕГОРИЙ СЕМЕЙ.

Тебе дают единицу измерения, справочник категорий, список активных семей этой единицы, перечень активных семей без категории (только в первой порции), блок «ЧЕРНОВИКИ ЭТОГО ОТКРЫТИЯ» (если он есть) и порцию пронумерованных наименований строк ведомости (со статьями и разделами). Ответь строгим JSON по заданной схеме, без пояснений.

Правила:
- Разложи КАЖДОЕ наименование порции: в группу (новую, активной семьи или продолжающую черновик) либо в not_work. Пропусти номер только тогда, когда не понимаешь, что это за строка. Ответ, в котором разобрана малая часть порции, — неверный ответ.
- Своди наименования в группы: одна группа — одна семья. Новая семья (family_id = null, draft_ref = null): дай имя — тип работы без параметров, бренда и места, определение в одно-два предложения и category_id из справочника категорий.
- Наименования, которые уже описывает активная семья списка, относи к ней: family_id — её номер, остальные поля группы (draft_ref, title, definition, category_id, similar_family_id) — null. Не заводи дубль активной семьи. Одна активная семья — не больше одной группы.
- Если наименование продолжает черновик из блока «ЧЕРНОВИКИ ЭТОГО ОТКРЫТИЯ», укажи его номер в draft_ref (номер после «Ч»), остальные поля группы (family_id, title, definition, category_id, similar_family_id) — null. Не заводи дубль черновика. Во всех прочих группах draft_ref = null.
- Строки-системы и комплекты своди по типу системы (например вентиляция, кондиционирование, отопление, водоснабжение, пожаротушение, слаботочные системы, электроснабжение): одна группа на тип системы, а не одна общая группа. Номер системы, корпус, этаж, бренд и мощность в имя семьи не входят.
- Если новая семья похожа на активную, но не совпадает с ней, укажи номер активной семьи в similar_family_id; иначе null.
- Затраты с собственным предметом и ценой (материалы, перевозка, услуги, комплекты) — семьи той категории, чьё определение им подходит по справочнику, а не «не работа».
- «Не работа» (not_work) — только строки без собственного предмета: примечания, оговорки, заголовки.
- Каждое наименование — не больше чем в одном месте: в одной группе либо в not_work. Используй только номера наименований порции.
- Для каждой активной семьи из перечня «СЕМЬИ БЕЗ КАТЕГОРИИ» предложи категорию из справочника в family_categories; если перечня нет или он «нет», family_categories — пустой список."""

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


def _scope_query(*columns, why_in_scope: bool = True):
    """Запрос, возвращающий `columns` контекстов охвата. Предикат (спека 3б
    §2.3) записан здесь и только здесь:

    контекст не архивирован, имеет членство, не `NOT_APPLICABLE`, без семьи и
    без ожидающей семьи, строка каталога — `TO_REVIEW` или `POSITION`; нет
    опубликованного предложения без решения на существующую семью; и при этом
    он система, либо у него предложение «новая семья», либо в единице его
    строки нет активной семьи. `path_broken` охват не меняет.

    `why_in_scope=False` отбрасывает последнее условие («почему в охвате»):
    остаются условия, которые делают контекст кандидатом на решение человека, а
    не причина попасть в охват. Нужен потребителям снимка черновиков — экрану и
    активации «не работы»: активация сама заводит активные семьи единицы, и по
    полному предикату контексты единицы без семей вышли бы из охвата от одной
    этой активации."""
    has_member = sa.exists().where(ContextMember.context_id == CatalogContext.id)
    unit_has_active_family = sa.exists().where(
        WorkFamily.status == FamilyStatus.active.value,
        WorkFamily.unit_id.is_not_distinct_from(CatalogPosition.unit_id),
    )
    conditions = [
        CatalogContext.archived_at.is_(None),
        has_member,
        CatalogContext.semantic_state != SemanticState.NOT_APPLICABLE.value,
        CatalogContext.work_family_id.is_(None),
        CatalogContext.pending_family_id.is_(None),
        CatalogPosition.kind.in_(_SCOPE_CATALOG_KINDS),
        ~_open_suggestion(existing_family=True),
    ]
    if why_in_scope:
        conditions.append(
            sa.or_(
                CatalogContext.semantic_kind == SemanticKind.SYSTEM.value,
                _open_suggestion(existing_family=False),
                ~unit_has_active_family,
            )
        )
    return (
        sa.select(*columns)
        .select_from(CatalogContext)
        .join(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
        .join(CatalogPosition, CatalogPosition.id == ContextBucket.catalog_position_id)
        .where(*conditions)
    )


def is_in_scope(
    db: Session, context_ids: Iterable[int], *, why_in_scope: bool = True
) -> frozenset[int]:
    """Какие из `context_ids` в охвате своей единицы сейчас — тот же предикат,
    что у `discovery_scope`, для потребителей снимка черновиков.
    `why_in_scope=False` — без условия «почему в охвате» (см. `_scope_query`)."""
    ids = list(dict.fromkeys(context_ids))
    if not ids:
        return frozenset()
    rows = db.execute(
        _scope_query(CatalogContext.id, why_in_scope=why_in_scope).where(CatalogContext.id.in_(ids))
    ).scalars()
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


#: Версия протокола порций; растёт при смене самого способа отправки (формата
#: тела порции, правил сборки), а не настроек.
CHUNK_PROTOCOL = 2


def execution_fingerprint(settings: Settings) -> dict[str, int]:
    """Отпечаток исполнения открытия (спека 3б §2.3.1, §2.3.2): то, от чего
    зависит, как модель получит охват, помимо самого канонического тела. Входит в
    `request_hash`: смена размера порции, предела дочистки или пределов меняет
    идентичность задания."""
    return {
        "chunk_protocol": CHUNK_PROTOCOL,
        "chunk_names": settings.SEMANTIC_DISCOVERY_CHUNK_NAMES,
        "cleanup_names": settings.SEMANTIC_DISCOVERY_CLEANUP_NAMES,
        "max_tokens_per_call": settings.SEMANTIC_DISCOVERY_MAX_TOKENS,
        "max_drafts": settings.SEMANTIC_DISCOVERY_MAX_DRAFTS,
    }


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
        request_hash=_sha256_hex(
            {
                "serialization_version": SERIALIZATION_VERSION,
                "body": body,
                "execution": execution_fingerprint(settings),
            }
        ),
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


# ---------------------------------------------------------------------------
#  Порции (спека 3б §2.3.1)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DraftRef:
    """Черновик, заведённый предыдущими порциями этого открытия: строка блока
    «ЧЕРНОВИКИ ЭТОГО ОТКРЫТИЯ» в теле следующей порции. `number` — номер среди
    новых групп собранного ответа, по нему порция ссылается через `draft_ref`."""

    number: int
    title: str
    definition: str
    category_id: int


#: Заголовок блока черновиков в `user` порции.
DRAFT_BLOCK_HEADER = "ЧЕРНОВИКИ ЭТОГО ОТКРЫТИЯ:\n"


def chunk_bounds(names_count: int, chunk: int) -> list[tuple[int, int]]:
    """Номера имён порций `(a, b)`: с 1, включительно, подряд, по `chunk` имён.
    Порций `⌈N/chunk⌉`; охват без имён («только категории») — одна порция
    `(1, 0)`."""
    if names_count <= 0:
        return [(1, 0)]
    return [(a, min(a + chunk - 1, names_count)) for a in range(1, names_count + 1, chunk)]


def draft_refs_of(answer: DiscoveryAnswer) -> tuple[DraftRef, ...]:
    """Черновики собранного ответа — его новые группы по порядку, с номерами
    `1, 2, …`; блок следующей порции строится из ответа предыдущих."""
    refs: list[DraftRef] = []
    for group in answer.groups:
        if group.family_id is None:
            assert group.title is not None and group.definition is not None
            assert group.category_id is not None
            refs.append(
                DraftRef(len(refs) + 1, group.title, group.definition, group.category_id)
            )
    return tuple(refs)


def chunk_sent_of(
    scope: DiscoveryScope,
    *,
    bounds: tuple[int, int],
    drafts: Sequence[DraftRef],
    first: bool,
) -> ChunkSent:
    """Что уходит модели в порции: множества, по которым разбор её ответа
    проверяет ссылки. Перечень семей без категории — только в порции 1."""
    return ChunkSent(
        numbers=frozenset(range(bounds[0], bounds[1] + 1)),
        draft_numbers=frozenset(d.number for d in drafts),
        active_family_ids=frozenset(scope.active_family_ids),
        uncategorized_family_ids=(
            frozenset(scope.uncategorized_family_ids) if first else frozenset()
        ),
        category_ids=frozenset(scope.category_ids),
    )


def cleanup_sent_of(
    scope: DiscoveryScope, *, numbers: Iterable[int], drafts: Sequence[DraftRef]
) -> ChunkSent:
    """Что уходит модели в дочистке (спека 3б §2.3.2): отправленные номера — номера
    остатка, а не отрезок; перечня семей без категории в дочистке нет, поэтому её
    `family_categories` разбор не принимает."""
    return ChunkSent(
        numbers=frozenset(numbers),
        draft_numbers=frozenset(d.number for d in drafts),
        active_family_ids=frozenset(scope.active_family_ids),
        uncategorized_family_ids=frozenset(),
        category_ids=frozenset(scope.category_ids),
    )


def cleanup_numbers(remainder: Iterable[int], *, settings: Settings) -> list[int]:
    """Номера имён дочистки (спека 3б §2.3.2): первые по возрастанию
    `SEMANTIC_DISCOVERY_CLEANUP_NAMES` номеров остатка; остаток сверх предела
    остаётся остатком. Пусто — дочистки нет: остаток пуст или предел `0`. Охват без
    имён даёт пустой остаток, поэтому «только категории» дочистки не имеет."""
    return sorted(set(remainder))[: settings.SEMANTIC_DISCOVERY_CLEANUP_NAMES]


def _draft_line(draft: DraftRef) -> str:
    title = _collapse_whitespace(draft.title)
    definition = _collapse_whitespace(draft.definition)
    return f"Ч{draft.number}. {title} — {definition} [категория {draft.category_id}]"


def _chunk_user_text(
    scope: DiscoveryScope,
    *,
    bounds: tuple[int, int],
    drafts: Sequence[DraftRef],
    first: bool,
    max_chars: int,
) -> str:
    a, b = bounds
    unit_display = scope.unit_code if scope.unit_code is not None else _NO_UNIT_DISPLAY
    parts = [f"ЕДИНИЦА: {unit_display}"]
    if first:
        uncategorized = (
            ", ".join(str(i) for i in sorted(scope.uncategorized_family_ids)) or _NO_UNCATEGORIZED
        )
        parts.append(f"СЕМЬИ БЕЗ КАТЕГОРИИ: {uncategorized}")
    if drafts:
        lines = "\n".join(_draft_line(d) for d in sorted(drafts, key=lambda d: d.number))
        parts.append(DRAFT_BLOCK_HEADER + lines)
    names = sorted((n for n in scope.names if a <= n.index <= b), key=lambda n: n.index)
    header = f"ИМЕНА {a}–{b} ИЗ {len(scope.names)}:" if scope.names else "ИМЕНА:"
    listing = "\n".join(_name_line(n, max_chars=max_chars) for n in names)
    parts.append(f"{header}\n{listing}")
    return "\n".join(parts)


def render_discovery_chunk(
    canonical: RenderedRequest,
    scope: DiscoveryScope,
    *,
    settings: Settings,
    bounds: tuple[int, int],
    drafts: Sequence[DraftRef],
    first: bool,
) -> dict:
    """Тело вызова одной порции. Чистая функция от канонического тела и охвата, без
    базы: тело, прошедшее проверку приватности при захвате, и тела порций не
    могут разойтись.

    Всё, кроме `user`, — из канонического тела: `system` (те же три блока
    побайтно, поэтому префикс кэшируется со второй порции), модель, температура,
    рассуждение, `usage`, формат ответа и `max_tokens`. `user` — единица;
    перечень семей без категории (только в порции 1); блок черновиков предыдущих
    порций (если они есть); имена порции с глобальными номерами в формате
    канонического тела. Цен, объёмов, подрядчиков, договоров и объектов тело не
    несёт, как и каноническое (`AGENTS.md` §3)."""
    user_text = _chunk_user_text(
        scope,
        bounds=bounds,
        drafts=drafts,
        first=first,
        max_chars=settings.SEMANTIC_DISCOVERY_NAME_MAX_CHARS,
    )
    return _body_with_user(canonical, user_text)


def _body_with_user(canonical: RenderedRequest, user_text: str) -> dict:
    """Тело вызова: всё, кроме `user`, — копия канонического тела."""
    body = {
        key: copy.deepcopy(canonical.body[key])
        for key in (
            "model", "temperature", "max_tokens", "reasoning", "usage", "response_format",
        )
    }
    body["messages"] = [
        copy.deepcopy(canonical.body["messages"][0]),
        {"role": "user", "content": user_text},
    ]
    return body


#: Строка-указание дочистки в `user` (спека 3б §2.3.2).
CLEANUP_INSTRUCTION = (
    "ЭТИ ИМЕНА ПРОПУЩЕНЫ В ПРОШЛЫХ ПОРЦИЯХ. РАЗЛОЖИ КАЖДОЕ: ПРОДОЛЖИ ЧЕРНОВИК "
    "(draft_ref), ОТНЕСИ К АКТИВНОЙ СЕМЬЕ, ЗАВЕДИ НОВУЮ ГРУППУ ИЛИ ОТНЕСИ К not_work."
)


def _cleanup_header(shown: int, total: int) -> str:
    return f"ИМЕНА БЕЗ ГРУППЫ ({shown} ИЗ {total}):"


def _cleanup_user_text(
    unit_code: str | None,
    names: Sequence[DiscoveryName],
    *,
    total: int,
    drafts: Sequence[DraftRef],
    max_chars: int,
) -> str:
    """`user` дочистки: единица; блок черновиков (если они есть); указание;
    заголовок и строки имён с глобальными номерами. Функция
    хранит порядок, в котором получила `names`: тело дочистки передаёт их по
    номеру, граница резерва — по убыванию длины в байтах (порядок число байт не
    меняет)."""
    unit_display = unit_code if unit_code is not None else _NO_UNIT_DISPLAY
    parts = [f"ЕДИНИЦА: {unit_display}"]
    if drafts:
        lines = "\n".join(_draft_line(d) for d in sorted(drafts, key=lambda d: d.number))
        parts.append(DRAFT_BLOCK_HEADER + lines)
    parts.append(CLEANUP_INSTRUCTION)
    listing = "\n".join(_name_line(n, max_chars=max_chars) for n in names)
    parts.append(f"{_cleanup_header(len(names), total)}\n{listing}")
    return "\n".join(parts)


def render_discovery_cleanup(
    canonical: RenderedRequest,
    scope: DiscoveryScope,
    *,
    settings: Settings,
    numbers: Sequence[int],
    drafts: Sequence[DraftRef],
) -> dict:
    """Тело вызова дочистки (спека 3б §2.3.2): чистая функция от канонического
    тела и охвата, без базы, как `render_discovery_chunk`. Всё, кроме `user`, — из
    канонического тела (`system` побайтно тот же). `user` — единица; блок
    черновиков, собранных порциями (все, по номеру); указание; заголовок и строки
    имён `numbers` с глобальными номерами в прежнем формате. Перечня семей без
    категории нет. Цен, объёмов, подрядчиков, договоров и объектов тело не несёт."""
    wanted = set(numbers)
    names = [n for n in scope.names if n.index in wanted]
    user_text = _cleanup_user_text(
        scope.unit_code,
        names,
        total=len(scope.names),
        drafts=drafts,
        max_chars=settings.SEMANTIC_DISCOVERY_NAME_MAX_CHARS,
    )
    return _body_with_user(canonical, user_text)


# ---------------------------------------------------------------------------
#  Резерв и ожидаемая цена открытия
# ---------------------------------------------------------------------------

#: Версия формулы резерва и ожидаемой цены открытия порциями; входит в
#: `preview_hash`. Общая `RESERVE_FORMULA_VERSION` относится к остальным видам.
DISCOVERY_RESERVE_FORMULA_VERSION = 2

_PER_MILLION = Decimal(1_000_000)


def draft_line_max_bytes(max_drafts: int, max_category_id: int) -> int:
    """`L` — верхняя граница байтов строки блока черновиков вместе с переводом
    строки для ЛЮБОГО допустимого текста. Имя режется по знакам до 100, определение
    до 300 (знак «…» входит в этот предел), знак UTF-8 — не более 4 байт, поэтому
    имя — не больше 400 байт, определение — не больше 1 200; остальное в строке
    считается по настоящим байтам формата `_draft_line`: «Ч», номер (наибольший —
    `max_drafts`), «. », « — » (3 байта тире), « [категория » (кириллица по 2
    байта), номер категории (наибольший в охвате), «]», перевод строки. Строка
    берётся из того же формата, по которому строится блок: имя и определение —
    знаки в 4 байта нужной длины."""
    worst = DraftRef(
        number=max_drafts,
        title="\U0001F600" * DRAFT_TITLE_MAX_CHARS,
        definition="\U0001F600" * DRAFT_DEFINITION_MAX_CHARS,
        category_id=max_category_id,
    )
    return len((_draft_line(worst) + "\n").encode("utf-8"))


def _chunk_sizes(scope: DiscoveryScope, settings: Settings) -> list[tuple[int, int]]:
    """На каждую порцию: байты `user` без блока черновиков и верхняя граница
    блока — `min(MAX_DRAFTS, имён в порциях до неё) × L` и заголовок. Блока у
    порции 1 нет."""
    line = draft_line_max_bytes(
        settings.SEMANTIC_DISCOVERY_MAX_DRAFTS, max(scope.category_ids, default=0)
    )
    header = len(DRAFT_BLOCK_HEADER.encode("utf-8"))
    sizes: list[tuple[int, int]] = []
    for a, b in chunk_bounds(len(scope.names), settings.SEMANTIC_DISCOVERY_CHUNK_NAMES):
        user = len(
            _chunk_user_text(
                scope,
                bounds=(a, b),
                drafts=(),
                first=(a == 1),
                max_chars=settings.SEMANTIC_DISCOVERY_NAME_MAX_CHARS,
            ).encode("utf-8")
        )
        possible = min(settings.SEMANTIC_DISCOVERY_MAX_DRAFTS, a - 1)
        sizes.append((user, header + possible * line if possible else 0))
    return sizes


def _cleanup_size(scope: DiscoveryScope, settings: Settings) -> tuple[int, int] | None:
    """Байты `user` дочистки без блока черновиков и верхняя граница блока (спека 3б
    §2.3.2); `None` — дочистки в резерве нет (`N = 0` или предел `0`). Дочистке
    достанется любое подмножество остатка из не более `m = min(CLEANUP_NAMES, N)`
    имён, поэтому `user` считается по `m` самым длинным в байтах строкам имён
    охвата с заголовком наибольшего вида (`m` из `N`); блок — `min(MAX_DRAFTS, N)`
    черновиков (черновиков не больше имён, их число ограничено пределом)."""
    names_count = len(scope.names)
    limit = settings.SEMANTIC_DISCOVERY_CLEANUP_NAMES
    if names_count < 1 or limit < 1:
        return None
    max_chars = settings.SEMANTIC_DISCOVERY_NAME_MAX_CHARS
    longest = sorted(
        scope.names,
        key=lambda n: len(_name_line(n, max_chars=max_chars).encode("utf-8")),
        reverse=True,
    )[:limit]
    user = len(
        _cleanup_user_text(
            scope.unit_code, longest, total=names_count, drafts=(), max_chars=max_chars
        ).encode("utf-8")
    )
    line = draft_line_max_bytes(
        settings.SEMANTIC_DISCOVERY_MAX_DRAFTS, max(scope.category_ids, default=0)
    )
    possible = min(settings.SEMANTIC_DISCOVERY_MAX_DRAFTS, names_count)
    return user, len(DRAFT_BLOCK_HEADER.encode("utf-8")) + possible * line


def reserve_of(scope: DiscoveryScope, rendered: RenderedRequest, settings: Settings) -> Decimal:
    """Резерв всего задания открытия — верхняя граница до первого вызова (спека
    3б §2.3.1): сумма по порциям. Префикс `system` порции — по наибольшему из
    тарифов входа, записи и чтения кэша (вызов несёт `cache_control`, и префикс
    любой порции может быть записан заново, а тарифы настраиваются независимо;
    кэш и наблюдавшиеся токены префикса не предполагаются); плюс байты `user`
    порции без блока черновиков; плюс наибольший блок черновиков; плюс
    `max_tokens` ответа по тарифу выхода. Байты считаются токенами (верхняя
    граница). Плюс один вызов дочистки (спека 3б §2.3.2), если `N >= 1` и
    `CLEANUP_NAMES >= 1`: тот же префикс, `user` по самым длинным именам охвата,
    блок черновиков `min(MAX_DRAFTS, N) × L`. Детерминирован: от охвата,
    канонического тела и настроек."""
    tariffs = tariffs_from(settings, SemanticJobKind.family_discovery)
    prefix_rate = max(tariffs.input_per_m, tariffs.cache_write_per_m, tariffs.cache_read_per_m)
    max_tokens = rendered.body["max_tokens"]
    sizes = _chunk_sizes(scope, settings)
    cleanup = _cleanup_size(scope, settings)
    if cleanup is not None:
        sizes.append(cleanup)
    total = Decimal(0)
    for user, drafts_max in sizes:
        total += (
            Decimal(rendered.prefix_bytes) * prefix_rate
            + Decimal(user + drafts_max) * tariffs.input_per_m
            + Decimal(max_tokens) * tariffs.output_per_m
        )
    return total / _PER_MILLION


def expected_of(scope: DiscoveryScope, rendered: RenderedRequest, settings: Settings) -> Decimal:
    """Ожидаемая цена задания (только для показа, в бюджете не участвует): первая
    порция пишет префикс в кэш (тариф записи), со второй он читается (тариф
    чтения); блок черновиков — половина верхней границы (фактическое число групп
    заранее неизвестно); ответ — `max_tokens`, как у единичных видов. Дочистка (если
    она есть в резерве) — как порция со второй: префикс читается из кэша."""
    tariffs = tariffs_from(settings, SemanticJobKind.family_discovery)
    max_tokens = rendered.body["max_tokens"]
    sizes = _chunk_sizes(scope, settings)
    cleanup = _cleanup_size(scope, settings)
    if cleanup is not None:
        sizes.append(cleanup)
    total = Decimal(0)
    for position, (user, drafts_max) in enumerate(sizes):
        prefix_rate = tariffs.cache_write_per_m if position == 0 else tariffs.cache_read_per_m
        total += (
            Decimal(rendered.prefix_bytes) * prefix_rate
            + (Decimal(user) + Decimal(drafts_max) / 2) * tariffs.input_per_m
            + Decimal(max_tokens) * tariffs.output_per_m
        )
    return total / _PER_MILLION


def discovery_reserve(scope: DiscoveryScope, db: Session, *, settings: Settings) -> Decimal:
    """Резерв задания открытия по охвату и настройкам: тело читается из базы, как
    при preview и захвате, поэтому оба считают одно число (`reserve_of`)."""
    return reserve_of(scope, render_discovery_request(scope, db, settings=settings), settings)


def discovery_expected(scope: DiscoveryScope, db: Session, *, settings: Settings) -> Decimal:
    """Ожидаемая цена задания открытия по охвату и настройкам (`expected_of`)."""
    return expected_of(scope, render_discovery_request(scope, db, settings=settings), settings)


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
    scope: DiscoveryScope, rendered: RenderedRequest, settings: Settings
) -> tuple[Decimal, Decimal]:
    """Резерв всего задания и ожидаемая цена — суммы по порциям (`reserve_of`,
    `expected_of`); в базу не ходит."""
    return reserve_of(scope, rendered, settings), expected_of(scope, rendered, settings)


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
            "discovery_reserve_version": DISCOVERY_RESERVE_FORMULA_VERSION,
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
    reserve, cached = estimate_of(scope, rendered, settings)
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
    ожидаемая цена, тарифы вида, версии формул резерва}`; резерв и цена — суммы
    по порциям. Отказов не бросает:
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
