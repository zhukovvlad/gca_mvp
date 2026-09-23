"""Корзины, контексты и маршрутизация позиции (спека §2.1–§2.5, §2.8).

Задача 4 фичи «Семьи и контексты»
(`docs/superpowers/plans/2026-09-22-catalog-families.md`).

Три звена (спека §2.1): **написание** (`catalog_positions`, не трогается),
**корзина** (`context_buckets`, идемпотентная точка входа «написание ×
эффективная статья», решений не несёт) и **контекст** (`catalog_contexts`,
устойчивая группа с принятым семантическим решением). **Членство**
(`context_members`) — явная строка «позиция → контекст», ровно одна на
позицию.

Эффективная статья не вычисляется заново (спека §1.7): она уже
материализована на строке-разделе позиции (`work_category_id`,
`category_source`), включая ручной разнос через `CategoryResolver`.
`chapter_context()` читает её у БЛИЖАЙШЕГО раздела цепочки
(`position_items.chapter_item_id`), а не у самой позиции — у позиции своей
статьи нет по построению (`ck_position_items_article_only_on_chapters`).

Нормализация текста раздела для сравнения в правилах — ТОЙ ЖЕ функцией,
что нормализует каталог (`normalize_job_title_with_lemmatization`), и
происходит НА ЛЕТУ в `evaluate_predicate`: `ChapterContext.chain` хранит
СЫРОЙ (нелемматизированный) текст раздела — второго (нормализованного)
представления строки модуль не заводит (спека §2.4, «вторых представлений
строки в проекте не заводится»). Сырой текст обязателен и по другой причине:
`chapter_chain`, передаваемый в `classify_name_role` (задача 3), нормализует
текст раздела САМ внутри себя (`_place_phrase`/`_is_working_chapter`), и
докстрока `classify_name_role` явно называет `ChapterContext.chain` тем же
порядком, что её собственный параметр `chapter_chain` — предварительная
нормализация тут была бы лишним, ничем не защищённым шагом.

Единица блокировки — корзина (спека §2.8, таблица): маршрутизация на импорте
берёт `FOR SHARE` на КАЖДУЮ существующую корзину, в которую маршрутизирует;
вновь созданную корзину блокировать не нужно — её ещё никто не видит.
`FOR SHARE` совместим сам с собой (хот-путь импорта не сериализуется) и
несовместим с `FOR UPDATE` (разделить/слить/перенести/архивировать —
задача 6, здесь не вызывается, но `lock_buckets(exclusive=True)` — тот же
примитив по контракту задачи 4).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypedDict

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from models import (
    CatalogContext,
    CatalogKind,
    CatalogPosition,
    ContextBucket,
    ContextMember,
    ContextRoutingRule,
    DecisionSource,
    Lot,
    MembershipState,
    PositionItem,
    Proposal,
    RoutedBy,
    SemanticState,
)
from parser.sanitize_text import normalize_job_title_with_lemmatization
from services.semantic_events import EVENT_ENUM_VALUES, record_event
from services.semantic_rules import PLACE_DICTIONARY_VERSION, classify_kind, classify_name_role
from services.unit_resolution import NO_UNIT_NORM


class RoutingError(Exception):
    """Отказ маршрутизации: дыра в данных (цикл разделов, корзина без
    действующего контекста по умолчанию, позиция без идентичности) — это
    доменный отказ, а не повод для тихой подстановки (спека §2.4)."""


#: Тот же приём, что `COALESCE(unit_id, -1)` в каталоге (спека §2.2): «нет
#: статьи» — законное, СРАВНИМОЕ значение ключа корзины, а не NULL, с которым
#: PostgreSQL не совпадает сам с собой. Сентинел — только КЛЮЧ выражения
#: `COALESCE(work_category_id, -1)`, а не хранимое значение: без статьи
#: `context_buckets.work_category_id` остаётся `NULL`.
NO_CATEGORY_SENTINEL = -1

#: Три вида предиката правила маршрутизации, все — о цепочке разделов (спека
#: §2.4): внутри одной корзины написание и эффективная статья по построению
#: одинаковы, различает контексты только цепочка (спека §1.10).
PREDICATE_NEAREST_CHAPTER_EQUALS = "nearest_chapter_equals"
PREDICATE_CHAPTER_CHAIN_CONTAINS = "chapter_chain_contains"
PREDICATE_CHAPTER_LEVEL_EQUALS = "chapter_level_equals"

PREDICATE_KINDS: frozenset[str] = frozenset(
    {
        PREDICATE_NEAREST_CHAPTER_EQUALS,
        PREDICATE_CHAPTER_CHAIN_CONTAINS,
        PREDICATE_CHAPTER_LEVEL_EQUALS,
    }
)


class RulePredicate(TypedDict, total=False):
    """Форма `context_routing_rules.predicate` (JSONB). `value` — сырой текст
    раздела (сравнивается нормализованным в `evaluate_predicate`); `level` —
    только у `chapter_level_equals`, индекс в `ChapterContext.chain` от
    ближайшего раздела (0) к корню."""

    kind: str
    value: str
    level: int


@dataclass(frozen=True)
class ChapterContext:
    """Цепочка разделов позиции: от ближайшего к корню (спека §1.10).

    `chain` — СЫРОЙ текст разделов (`job_title_in_proposal`), НЕ
    нормализованный: второго представления строки модуль не заводит (см.
    докстринг модуля). `nearest` — `chain[0]` либо `None`, если у позиции нет
    раздела вовсе. `category_id`/`category_source` — эффективная статья и её
    происхождение с БЛИЖАЙШЕГО раздела (спека §1.7): она уже материализована
    там с учётом наследования и ручного разноса, вычислять её заново не
    нужно.
    """

    chain: tuple[str, ...]
    nearest: str | None
    category_id: int | None
    category_source: str | None


@dataclass(frozen=True)
class RoutingOutcome:
    """Итог пакетной маршрутизации (`route_positions`). Два разных факта «нет
    членства» — РАЗНЫЕ счётчики (спека §2.2, §2.4;
    `docs/insights/one-value-two-states.md`): строка-раздел (не работа) и
    позиция без идентичности каталога — не одно и то же «пропущено»."""

    buckets_created: int
    contexts_created: int
    members_created: int
    members_skipped_chapters: int
    members_skipped_unmatched: int


def _now() -> datetime:
    return datetime.now(UTC)


#: Допустимые значения `origin` — то же множество, что `EVENT_ENUM_VALUES`
#: задачи 2 для `context_created.origin` (задача 2, `services/semantic_events.py`).
#: Решение оркестратора: проверяется НА ВХОДЕ обеих публичных функций,
#: ВСЕГДА — не только когда маршрутизация реально заводит новый контекст.
#: Без этого невалидный `origin` на УЖЕ существующей корзине тихо
#: проглатывался: ничего не пишется в журнал, и отказа тоже нет.
_VALID_ORIGINS: frozenset[str] = EVENT_ENUM_VALUES[("context_created", "origin")]


def _validate_origin(origin: str) -> None:
    if origin not in _VALID_ORIGINS:
        raise RoutingError(
            f"недопустимое значение origin: {origin!r}; допустимые значения: "
            f"{sorted(_VALID_ORIGINS)}"
        )


def _normalize_chapter_text(text: str) -> str:
    """Лемматизированная форма текста раздела для сравнения в правилах —
    ТОЙ ЖЕ функцией, что нормализует каталог (спека §2.4). Пустая строка
    вместо `None`, чтобы сравнение двух «ничего не давших» нормализаций не
    считалось совпадением с пустым `value` правила по случайности типов."""
    return normalize_job_title_with_lemmatization(text) or ""


# ---------------------------------------------------------------------------
#  Цепочка разделов и эффективная статья
# ---------------------------------------------------------------------------

def chapter_context(db: Session, position_item_id: int) -> ChapterContext:
    """Строит цепочку разделов позиции, следуя `chapter_item_id` от позиции к
    корню (спека §1.10, §2.4).

    Позиция без раздела (`chapter_item_id IS NULL`) даёт пустую цепочку и
    `category_id = None` — это ОДИН из двух разных путей к «нет статьи»
    (спека §2.2); второй — раздел без статьи, дальше по коду.

    Raises:
        RoutingError: позиция не найдена, либо `chapter_item_id` зациклен
            (найден узел, уже встречавшийся в этой же цепочке) — защита от
            бесконечного цикла, а не молчаливый обрыв на первом повторе.
            Раздел цепочки сам по себе не может «не найтись»: составной FK
            `fk_position_items_chapter` (`ON DELETE RESTRICT`) гарантирует,
            что непустое `chapter_item_id` ссылается на существующую строку
            той же сметы — цикл ловится отдельно, ДО того, как код дошёл бы
            до чтения несуществующей строки.
    """
    position = db.get(PositionItem, position_item_id)
    if position is None:
        raise RoutingError(f"позиция {position_item_id} не найдена")

    chain: list[str] = []
    nearest_row: PositionItem | None = None
    visited: set[int] = set()
    current_id = position.chapter_item_id
    while current_id is not None:
        if current_id in visited:
            raise RoutingError(
                f"позиция {position_item_id}: цикл в chapter_item_id — раздел "
                f"{current_id} уже встречался в цепочке"
            )
        visited.add(current_id)
        row = db.get(PositionItem, current_id)
        if nearest_row is None:
            nearest_row = row
        chain.append(row.job_title_in_proposal)
        current_id = row.chapter_item_id

    if nearest_row is None:
        return ChapterContext(chain=(), nearest=None, category_id=None, category_source=None)

    return ChapterContext(
        chain=tuple(chain),
        nearest=chain[0],
        category_id=nearest_row.work_category_id,
        category_source=nearest_row.category_source,
    )


def effective_category_id(db: Session, position_item_id: int) -> int | None:
    """Эффективная статья позиции — статья её БЛИЖАЙШЕГО раздела (спека §1.7,
    §2.2), не что-либо, читаемое у самой позиции: у позиции своей статьи нет
    по построению (`ck_position_items_article_only_on_chapters`)."""
    return chapter_context(db, position_item_id).category_id


# ---------------------------------------------------------------------------
#  Корзина: get-or-create и блокировка
# ---------------------------------------------------------------------------

def _bucket_conflict_arbiter():
    """Арбитр `ON CONFLICT` — ТО ЖЕ выражение, что уникальный индекс миграции
    0017 (`uq_context_buckets_position_category`): `literal_column`, а не
    обычный Python-литерал, иначе psycopg3 после `prepare_threshold=5`
    перестанет находить индекс у подготовленного плана
    (`docs/insights/batch-larger-than-five.md`, тот же приём, что
    `services/matching.py::_UNIT_ARBITER`)."""
    return sa.func.coalesce(
        ContextBucket.work_category_id, sa.literal_column(str(NO_CATEGORY_SENTINEL))
    )


def get_or_create_bucket(
    db: Session, *, catalog_position_id: int, work_category_id: int | None
) -> tuple[ContextBucket, bool]:
    """Идемпотентный get-or-create корзины «написание × эффективная статья»
    (спека §2.1, §2.4): `INSERT ... ON CONFLICT DO NOTHING` + повторный
    `SELECT`, тот же приём, что get-or-create каталога (`services/matching.py`).

    Возвращает `(bucket, created)`. `created=True` — ТОЛЬКО у сессии, чья
    вставка реально создала строку (`ON CONFLICT DO NOTHING` вернул строку
    через `RETURNING`); проигравшая сессия переселектит существующую корзину
    и обязана НЕ заводить второй контекст по умолчанию — это решает
    вызывающий код (`_get_or_create_bucket_with_default_context`), а не эта
    функция: она только про корзину, решений не несёт (спека §2.1).
    """
    stmt = (
        pg_insert(ContextBucket)
        .values(catalog_position_id=catalog_position_id, work_category_id=work_category_id)
        .on_conflict_do_nothing(
            index_elements=[ContextBucket.catalog_position_id, _bucket_conflict_arbiter()]
        )
        .returning(ContextBucket.id)
    )
    row = db.execute(stmt).first()
    if row is not None:
        bucket = db.get(ContextBucket, row.id)
        return bucket, True

    # `Column == None` в SQLAlchemy сам компилируется в `IS NULL`
    # (`ColumnOperators.__eq__` перехватывает `None` особо), поэтому отдельной
    # ветки через `.is_(None)` для случая «нет статьи» не нужно — это был бы
    # тот же самый запрос другими словами.
    bucket = db.execute(
        sa.select(ContextBucket).where(
            ContextBucket.catalog_position_id == catalog_position_id,
            ContextBucket.work_category_id == work_category_id,
        )
    ).scalar_one()
    return bucket, False


def _lock_statement(bucket_ids: list[int], *, exclusive: bool):
    """Запрос локов — отдельно от исполнения, чтобы тест мог скомпилировать
    его в SQL и проверить РЕЖИМ (`FOR SHARE` vs `FOR UPDATE`), а не намерение
    (`docs/pitfalls/db.md`: `with_for_update(read=True)` — это `FOR SHARE`,
    `key_share=True` — это `FOR NO KEY UPDATE`, а не `FOR KEY SHARE`).
    `ORDER BY id` — порядок захвата по возрастанию (спека §2.8): единый
    порядок делает встречную блокировку невозможной по построению (тот же
    приём, что сортировка пар в `services/matching.py`)."""
    stmt = sa.select(ContextBucket.id).where(ContextBucket.id.in_(bucket_ids)).order_by(
        ContextBucket.id
    )
    return stmt.with_for_update() if exclusive else stmt.with_for_update(read=True)


def lock_buckets(db: Session, bucket_ids: list[int], *, exclusive: bool) -> None:
    """Блокирует существующие корзины по возрастанию `id` (спека §2.8).

    `exclusive=False` — `FOR SHARE` (маршрутизация на импорте: совместима
    сама с собой, хот-путь не сериализуется). `exclusive=True` — `FOR UPDATE`
    (разделить/слить/перенести/архивировать — задача 6; здесь не
    вызывается, но контракт задачи 4). Пустой список — no-op: захватывать
    нечего.

    Raises:
        RoutingError: среди `bucket_ids` есть значения, которых нет в
            `context_buckets` (решение оркестратора) — называет ИМЕННО
            отсутствующие id, а не притворяется, что заблокировала то, чего
            не нашла.
    """
    if not bucket_ids:
        return
    rows = db.execute(_lock_statement(bucket_ids, exclusive=exclusive)).all()
    locked_ids = {row.id for row in rows}
    missing = sorted(set(bucket_ids) - locked_ids)
    if missing:
        raise RoutingError(f"корзины не найдены для блокировки: {missing}")


# ---------------------------------------------------------------------------
#  Правило маршрутизации
# ---------------------------------------------------------------------------

def evaluate_predicate(predicate: RulePredicate, chapters: ChapterContext) -> bool:
    """Проверяет типизированный предикат правила против цепочки разделов
    позиции (спека §2.4). Сравнение — по нормализованному тексту, той же
    функцией, что нормализует каталог (см. докстринг модуля).

    Raises:
        RoutingError: неизвестный вид предиката либо предикат без обязательного
            для своего вида ключа (`value`/`level`) — предикат типизирован и
            закрыт, а не «что дали, то и сравнили».
    """
    kind = predicate.get("kind")
    if kind not in PREDICATE_KINDS:
        raise RoutingError(f"неизвестный вид предиката маршрутизации: {kind!r}")

    if "value" not in predicate:
        raise RoutingError(f"предикат {kind!r} без обязательного ключа 'value'")
    target = _normalize_chapter_text(predicate["value"])

    if kind == PREDICATE_NEAREST_CHAPTER_EQUALS:
        if chapters.nearest is None:
            return False
        return _normalize_chapter_text(chapters.nearest) == target

    if kind == PREDICATE_CHAPTER_CHAIN_CONTAINS:
        return any(_normalize_chapter_text(chapter) == target for chapter in chapters.chain)

    # PREDICATE_CHAPTER_LEVEL_EQUALS
    if "level" not in predicate:
        raise RoutingError(f"предикат {kind!r} без обязательного ключа 'level'")
    level = predicate["level"]
    if level < 0 or level >= len(chapters.chain):
        return False
    return _normalize_chapter_text(chapters.chain[level]) == target


# ---------------------------------------------------------------------------
#  Создание корзины + контекста по умолчанию (спека §2.3, §2.5, §2.14)
# ---------------------------------------------------------------------------

#: Строки этих видов каталога рождают контекст сразу `NOT_APPLICABLE`: это
#: не работа, решение о виде работы для них бессмысленно (спека §2.5,
#: таблица переходов).
_NOT_APPLICABLE_CATALOG_KINDS = frozenset(
    {CatalogKind.HEADER.value, CatalogKind.LOT_HEADER.value, CatalogKind.TRASH.value}
)


def _unit_norm_of(catalog_position: CatalogPosition) -> str:
    if catalog_position.unit_id is None:
        return NO_UNIT_NORM
    return catalog_position.unit.code


def _create_default_context(
    db: Session,
    *,
    bucket: ContextBucket,
    catalog_position: CatalogPosition,
    chain: tuple[str, ...],
    origin: str,
) -> CatalogContext:
    """Создаёт контекст по умолчанию новой корзины (спека §2.3, §2.5, §2.14).

    Вид и роль имени — правилами задачи 3, `source='rule'`, автора нет
    (решение ещё не человеческое). `semantic_state` — `NOT_APPLICABLE` для
    `HEADER`/`LOT_HEADER`/`TRASH`, иначе `SUGGESTED` (таблица переходов,
    спека §2.5) — НЕЗАВИСИМО от этого вид и роль всё равно вычисляются
    правилом: ось вида и факт «есть решение оператора» разные вещи (§2.5).
    Пишет `context_created` с `bucket_id` и `origin` — обязательное условие
    задачи (спека §2.14).
    """
    now = _now()
    unit_norm = _unit_norm_of(catalog_position)
    semantic_kind = classify_kind(unit_norm)
    role_outcome = classify_name_role(catalog_position.standard_job_title, chapter_chain=chain)
    semantic_state = (
        SemanticState.NOT_APPLICABLE.value
        if catalog_position.kind in _NOT_APPLICABLE_CATALOG_KINDS
        else SemanticState.SUGGESTED.value
    )

    context = CatalogContext(
        bucket_id=bucket.id,
        is_default=True,
        semantic_kind=semantic_kind,
        semantic_kind_source=DecisionSource.rule.value,
        semantic_kind_by=None,
        semantic_kind_at=now,
        name_role=role_outcome.role,
        name_role_source=DecisionSource.rule.value,
        name_role_by=None,
        name_role_at=now,
        place_dictionary_version=PLACE_DICTIONARY_VERSION,
        semantic_state=semantic_state,
        comparability_reason=role_outcome.comparability_reason,
    )
    db.add(context)
    db.flush()
    record_event(
        db,
        event_type="context_created",
        context_id=context.id,
        payload={"bucket_id": bucket.id, "origin": origin},
    )
    return context


def _get_or_create_bucket_with_default_context(
    db: Session,
    *,
    catalog_position: CatalogPosition,
    work_category_id: int | None,
    chain: tuple[str, ...],
    origin: str,
) -> tuple[ContextBucket, bool, bool]:
    """Get-or-create корзины плюс, ТОЛЬКО если корзину создала эта сессия, её
    контекст по умолчанию — в ОДНОЙ транзакции (решение оркестратора: «новая
    корзина рождается со своим контекстом по умолчанию, заводит его только
    сессия, чья вставка реально создала строку»). Возвращает
    `(bucket, bucket_created, context_created)`.
    """
    bucket, bucket_created = get_or_create_bucket(
        db, catalog_position_id=catalog_position.id, work_category_id=work_category_id
    )
    if not bucket_created:
        return bucket, False, False
    _create_default_context(
        db, bucket=bucket, catalog_position=catalog_position, chain=chain, origin=origin
    )
    return bucket, True, True


def _live_default_context(db: Session, bucket_id: int) -> CatalogContext:
    """Действующий контекст по умолчанию корзины. Отсутствие — доменный
    отказ, называющий корзину (спека §2.4): половину инварианта «не менее
    одного» схема не выражает (частичный уникальный индекс держит только «не
    более одного»), и это признаётся явно, а не маскируется тихой
    подстановкой."""
    context = db.execute(
        sa.select(CatalogContext).where(
            CatalogContext.bucket_id == bucket_id,
            CatalogContext.is_default.is_(True),
            CatalogContext.archived_at.is_(None),
        )
    ).scalar_one_or_none()
    if context is None:
        raise RoutingError(
            f"у корзины {bucket_id} нет действующего контекста по умолчанию — "
            "маршрутизация отказывает, а не подставляет случайный контекст"
        )
    return context


# ---------------------------------------------------------------------------
#  Маршрутизация членства
# ---------------------------------------------------------------------------

def _apply_routing(
    db: Session, *, position: PositionItem, bucket: ContextBucket, chapters: ChapterContext
) -> tuple[ContextMember, bool]:
    """Маршрутизирует ОДНУ позицию в уже разрешённую (и, если существовала —
    заблокированную) корзину. Возвращает `(member, created)`.

    `routed_by='manual'` переживает повторную маршрутизацию БЕЗ ИСКЛЮЧЕНИЙ
    (спека §2.4: «ручное решение сильнее правила») — правило корзины дальше
    даже не читается.
    """
    existing = db.get(ContextMember, position.id)
    if existing is not None and existing.routed_by == RoutedBy.manual.value:
        return existing, False

    rules = (
        db.execute(
            sa.select(ContextRoutingRule)
            .where(ContextRoutingRule.bucket_id == bucket.id)
            .order_by(ContextRoutingRule.ordinal)
        )
        .scalars()
        .all()
    )

    matched_rule: ContextRoutingRule | None = None
    for rule in rules:
        if evaluate_predicate(rule.predicate, chapters):
            matched_rule = rule
            break

    if matched_rule is not None:
        context_id = matched_rule.context_id
        routed_by = RoutedBy.rule.value
        routing_rule_id = matched_rule.id
    else:
        context_id = _live_default_context(db, bucket.id).id
        routed_by = RoutedBy.default.value
        routing_rule_id = None

    if existing is None:
        member = ContextMember(
            position_item_id=position.id,
            context_id=context_id,
            bucket_id=bucket.id,
            membership_state=MembershipState.CURRENT.value,
            routed_by=routed_by,
            routing_rule_id=routing_rule_id,
        )
        db.add(member)
        db.flush()
        return member, True

    changed = (
        existing.context_id != context_id
        or existing.bucket_id != bucket.id
        or existing.routed_by != routed_by
        or existing.routing_rule_id != routing_rule_id
        or existing.membership_state != MembershipState.CURRENT.value
    )
    if changed:
        existing.context_id = context_id
        existing.bucket_id = bucket.id
        existing.routed_by = routed_by
        existing.routing_rule_id = routing_rule_id
        existing.membership_state = MembershipState.CURRENT.value
        db.flush()
    return existing, False


def route_position(
    db: Session, *, position_item_id: int, origin: str = "import"
) -> ContextMember:
    """Маршрутизирует одну позицию (спека §2.4). `origin` — решение
    оркестратора: значение, записываемое в `context_created.origin`, если
    маршрутизация заводит новую корзину (валидные значения —
    `EVENT_ENUM_VALUES[("context_created", "origin")]`; задача 11 передаёт
    `"backfill"`).

    Raises:
        RoutingError: `origin` не входит в допустимое множество (проверяется
            ПЕРВЫМ, всегда — решение оркестратора), позиция не найдена, это
            строка-раздел (членства не имеет, спека §2.2), позиция не
            сопоставлена с каталогом, либо корзина существует без
            действующего контекста по умолчанию.
    """
    _validate_origin(origin)
    position = db.get(PositionItem, position_item_id)
    if position is None:
        raise RoutingError(f"позиция {position_item_id} не найдена")
    if position.is_chapter:
        raise RoutingError(
            f"позиция {position_item_id} — строка-раздел, членства не имеет"
        )
    if position.catalog_position_id is None:
        raise RoutingError(
            f"позиция {position_item_id} не сопоставлена с каталогом "
            "(catalog_position_id пуст)"
        )

    catalog_position = db.get(CatalogPosition, position.catalog_position_id)
    chapters = chapter_context(db, position_item_id)

    bucket, bucket_created, _context_created = _get_or_create_bucket_with_default_context(
        db,
        catalog_position=catalog_position,
        work_category_id=chapters.category_id,
        chain=chapters.chain,
        origin=origin,
    )
    if not bucket_created:
        lock_buckets(db, [bucket.id], exclusive=False)

    member, _created = _apply_routing(db, position=position, bucket=bucket, chapters=chapters)
    return member


def route_positions(
    db: Session, *, estimate_ids: list[int], origin: str = "import"
) -> RoutingOutcome:
    """Маршрутизирует все позиции указанных смет (спека §2.9: на импорте —
    все позиции сметы, между `match_positions` и `finalize_done`, в сессии B).

    Порядок захвата локов (решение оркестратора): СНАЧАЛА разрешаются (и при
    необходимости создаются) корзины для ВСЕХ позиций партии, ЗАТЕМ берётся
    `FOR SHARE` на все СУЩЕСТВУЮЩИЕ корзины разом, по возрастанию `id`, и
    только ПОСЛЕ этого позиции маршрутизируются. Захват лока по одной корзине
    за позицию нарушил бы порядок по `id`, если позиции ссылаются на корзины
    вразнобой.

    Raises:
        RoutingError: `origin` не входит в допустимое множество — проверяется
            ПЕРВЫМ, всегда, даже если `estimate_ids` пуст (решение
            оркестратора); либо любой из отказов `route_position`,
            всплывший из `_apply_routing`/`chapter_context`.
    """
    _validate_origin(origin)
    if not estimate_ids:
        return RoutingOutcome(
            buckets_created=0,
            contexts_created=0,
            members_created=0,
            members_skipped_chapters=0,
            members_skipped_unmatched=0,
        )

    positions = (
        db.execute(
            sa.select(PositionItem)
            .join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id.in_(estimate_ids))
        )
        .scalars()
        .all()
    )

    members_skipped_chapters = 0
    members_skipped_unmatched = 0
    eligible: list[PositionItem] = []
    for position in positions:
        if position.is_chapter:
            members_skipped_chapters += 1
            continue
        if position.catalog_position_id is None:
            members_skipped_unmatched += 1
            continue
        eligible.append(position)

    buckets_created = 0
    contexts_created = 0
    members_created = 0

    resolved: list[tuple[PositionItem, ChapterContext, ContextBucket]] = []
    existing_bucket_ids: set[int] = set()
    for position in eligible:
        catalog_position = db.get(CatalogPosition, position.catalog_position_id)
        chapters = chapter_context(db, position.id)
        bucket, bucket_created, context_created = _get_or_create_bucket_with_default_context(
            db,
            catalog_position=catalog_position,
            work_category_id=chapters.category_id,
            chain=chapters.chain,
            origin=origin,
        )
        if bucket_created:
            buckets_created += 1
        if context_created:
            contexts_created += 1
        if not bucket_created:
            existing_bucket_ids.add(bucket.id)
        resolved.append((position, chapters, bucket))

    # `_lock_statement` сортирует по возрастанию `id` уже В ЗАПРОСЕ
    # (`ORDER BY`) — сортировать список id ещё и в Python было бы тем же
    # порядком другими словами.
    lock_buckets(db, list(existing_bucket_ids), exclusive=False)

    for position, chapters, bucket in resolved:
        _member, created = _apply_routing(db, position=position, bucket=bucket, chapters=chapters)
        if created:
            members_created += 1

    return RoutingOutcome(
        buckets_created=buckets_created,
        contexts_created=contexts_created,
        members_created=members_created,
        members_skipped_chapters=members_skipped_chapters,
        members_skipped_unmatched=members_skipped_unmatched,
    )
