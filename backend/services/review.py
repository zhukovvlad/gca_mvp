"""Ручной матчинг: применение решений оператора (AGENTS.md §5, «Ручной матчинг»).

Здесь только транзакционные операции над каталогом и кэшем. Эндпоинты и экран
Review — фаза 5 (§9.5); сервис нужен уже в фазе 4, потому что на нём держатся два
обязательных теста: «слияние TO_REVIEW удаляет строку и не оставляет кэш-записей
на TO_REVIEW» — инвариант `AGENTS.md` §5, где он и записан со словами «закрепить
тестом», — и «ручное решение переживает истечение auto-TTL», пункт DoD §10.

Решение применяется ко ВСЕМ `position_items`, ссылающимся на данную
TO_REVIEW-строку, — в этом смысл очереди: оператор разбирает работу, а не строку
конкретной сметы.

Два вида решений:

* **слить с существующей POSITION** — ссылки переносятся, TO_REVIEW-строка
  удаляется. Удаление обязательно: пока она есть, она занимает свою
  нормализованную пару и перехватывала бы будущий get-or-create;
* **утвердить как POSITION / пометить HEADER, TRASH** — меняется `kind` той же
  строки, удаления нет.

В обоих случаях пишется запись `matching_cache` с `source='manual'` и
`expires_at = NULL`: ручные решения не истекают (§4).

**Задача 9 (спека §2.8 «Слияние в Review»):
членства `routed_by='manual'` исходной корзины при слиянии перемаршрутизируются
в корзину цели ОБЫЧНОЙ маршрутизацией (`_route_member_into_bucket`), а не
остаются в своём контексте.** Внутри ЖИВОЙ корзины «ручное решение сильнее
правила» (спека §2.4) — короткое замыкание `route_position`. Здесь корзина
ЦЕЛИКОМ упраздняется слиянием: контекст, которому был адресован ручной выбор,
архивируется и перевешивается на корзину цели, — оставаться членством АРХИВНОГО
контекста, из которого маршрутизация больше не читает, нельзя. Спека §2.8
требует перенести «в неё [корзину цели] обычной маршрутизацией цели» БЕЗ
исключения для `manual`, поэтому решение принято как чтение буквы спеки, а не
как отступление от нее. Такое членство теряет `routed_by='manual'` (получает
`rule`/`default` от маршрутизации цели) и считается в `members_moved`
(`reason='review_merge'`) наравне с остальными; конфликт решений (§2.8) —
единственная защита, если старый и новый контекст расходятся по семье/виду.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

import services.context_routing as context_routing_module
from models import (
    CatalogContext,
    CatalogKind,
    CatalogPosition,
    ContextBucket,
    ContextMember,
    ContextRoutingRule,
    DecisionSource,
    MatchingCache,
    MatchSource,
    PositionItem,
    RoutedBy,
    SemanticState,
    WorkFamily,
)
from services.context_routing import chapter_context, evaluate_predicate, lock_buckets
from services.matching import NORM_VERSION, cache_key
from services.semantic_events import record_event
from services.unit_resolution import UnitResolver

log = logging.getLogger(__name__)

#: Kind-ы, которые оператор может поставить строке напрямую (§5).
MANUAL_KINDS = (CatalogKind.POSITION.value, CatalogKind.HEADER.value, CatalogKind.TRASH.value)


class ReviewError(Exception):
    """Решение оператора неприменимо. Текст показывается человеку."""


def _lock_rows(db: Session, ids: list[int]) -> dict[int, CatalogPosition]:
    """Блокирует каталожные строки `FOR UPDATE` и возвращает их по id.

    Решения оператора обязаны быть сериализованы: без блокировки два оператора
    сливают одну и ту же TO_REVIEW-строку с разными целями, ссылки уходят к
    первой цели, а ручную запись кэша перетирает вторая — то есть очередь и кэш
    расходятся молча.

    Строки блокируются ОДНИМ запросом с `ORDER BY id`: единый порядок захвата
    исключает взаимную блокировку двух решений, работающих с той же парой строк
    в обратном порядке.

    Проверка состояния идёт ПОСЛЕ захвата — в этом и смысл: проигравший ждёт
    коммита победителя и затем видит настоящее состояние (строки уже нет либо у
    неё другой `kind`), а не то, что было до его ожидания.

    **`populate_existing=True` здесь обязателен, и это не перестраховка.** Если
    строка уже загружена в эту сессию (так делал HTTP-слой фазы 5, проверяя
    существование через `db.get`), SQLAlchemy вернёт объект из identity map, НЕ
    обновляя его атрибуты, — и `FOR UPDATE` окажется бесполезен: сам SELECT
    прочитает свежие данные, а `_require_kind` проверит устаревший `kind` из
    кэша. Замер (фаза 5): после коммита `TO_REVIEW →
    POSITION` другой сессией повторный `SELECT ... FOR UPDATE` без этой опции
    вернул `kind=TO_REVIEW`, с ней — `POSITION`; в БД лежал `POSITION`. То есть
    два оператора **смогли бы** перезаписать решения друг друга вопреки блокировке.

    Именно «смогли бы», а не «перезаписывали»: на путях, которыми фаза 5 была
    сдана, объект успевал уйти сборщику мусора (identity map держит слабые ссылки,
    а роутер результат `db.get` отбрасывал), поэтому потери решения не происходило.
    Дефект был латентным — корректность держалась на времени сборки мусора, а не на
    коде, и активировался бы от одного `row = _require_exists(...)`. Подробнее —
    `docs/phase5-crud-review.md` §10.1; механизм закреплён тестом
    `test_lock_refreshes_a_row_already_loaded_in_the_session`, который удерживает
    ссылку сам.
    """
    rows = (
        db.execute(
            sa.select(CatalogPosition)
            .where(CatalogPosition.id.in_(ids))
            .order_by(CatalogPosition.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        .scalars()
        .all()
    )
    return {row.id: row for row in rows}


def _require_kind(rows: dict[int, CatalogPosition], catalog_position_id: int, expected: str) -> CatalogPosition:
    row = rows.get(catalog_position_id)
    if row is None:
        raise ReviewError(
            f"Каталожная строка {catalog_position_id} не найдена — возможно, её уже "
            "обработал другой оператор."
        )
    if row.kind != expected:
        raise ReviewError(
            f"Каталожная строка {catalog_position_id} имеет kind={row.kind}, "
            f"а операция применима к {expected}."
        )
    return row


def _write_manual_cache(
    db: Session, source_row: CatalogPosition, target_id: int, resolver: UnitResolver
) -> str:
    """Ручная запись кэша для нормализованной пары решённой строки.

    `DO UPDATE`: по этому ключу могла остаться ИСТЁКШАЯ auto-запись (живая дала бы
    hit в ветке 1, и TO_REVIEW-строки просто не возникло бы). После апдейта
    запись становится бессрочной — CHECK `ck_matching_cache_ttl_by_source`
    требует `expires_at IS NULL` ровно для `manual`.
    """
    key = cache_key(
        source_row.normalized_job_title, resolver.unit_norm_for_id(source_row.unit_id)
    )
    stmt = pg_insert(MatchingCache)
    stmt = stmt.on_conflict_do_update(
        index_elements=[MatchingCache.cache_key],
        set_={
            "catalog_position_id": stmt.excluded.catalog_position_id,
            "job_title_text": stmt.excluded.job_title_text,
            "unit_text": stmt.excluded.unit_text,
            "source": stmt.excluded.source,
            "expires_at": stmt.excluded.expires_at,
            "norm_version": stmt.excluded.norm_version,
            "updated_at": sa.func.now(),
        },
    )
    db.execute(
        stmt,
        {
            "cache_key": key,
            "norm_version": NORM_VERSION,
            "job_title_text": source_row.standard_job_title,
            # Исходного текста единицы из файла у ручного решения нет — сохраняем
            # каноническое имя. Перевыпуск ключей (§4) от этого не страдает:
            # нормализация канонического имени даёт его же.
            "unit_text": resolver.unit_norm_for_id(source_row.unit_id) or None,
            "catalog_position_id": target_id,
            "source": MatchSource.manual.value,
            "expires_at": None,
        },
    )
    return key


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class MergeOutcome:
    """Итог слияния в Review (спека §2.8): `moved_positions` — как раньше,
    `warnings` — новое поле, пустое в подавляющем большинстве слияний
    (конфликт требует двух ПРИНЯТЫХ решений на одну работу)."""

    moved_positions: int
    warnings: list[str]


def _family_title(db: Session, family_id: int) -> str:
    family = db.get(WorkFamily, family_id)
    return family.title if family is not None else f"#{family_id}"


def _conflict_warning(
    db: Session, *, source_context: CatalogContext, target_context: CatalogContext
) -> str | None:
    """Сравнивает исходный и целевой контексты по правилу конфликта слияния
    (спека §2.8): разные НАЗНАЧЕННЫЕ семьи ЛИБО разные ПОДТВЕРЖДЁННЫЕ
    (`semantic_kind_source='manual'`) виды. Возвращает готовый текст
    предупреждения либо `None`, если хотя бы одна из осей не расходится.

    Правило — через ИЛИ (спека §2.5, §2.8): семейная ветка не требует
    `manual`-подтверждения (в фиче 1 семья назначается только оператором,
    `family_source` всегда `'manual'`), а видовая ветка требует его явно —
    неподтверждённый (`source='rule'`) вид не расходится, он просто ещё не
    решён."""
    if (
        source_context.work_family_id is not None
        and target_context.work_family_id is not None
        and source_context.work_family_id != target_context.work_family_id
    ):
        return (
            "Слияние свело разные семьи работ: «"
            f"{_family_title(db, source_context.work_family_id)}» и «"
            f"{_family_title(db, target_context.work_family_id)}»."
        )
    if (
        source_context.semantic_kind_source == DecisionSource.manual.value
        and target_context.semantic_kind_source == DecisionSource.manual.value
        and source_context.semantic_kind != target_context.semantic_kind
    ):
        return (
            "Слияние свело разные подтверждённые виды работы: "
            f"{source_context.semantic_kind} и {target_context.semantic_kind}."
        )
    return None


def _resolve_target_bucket(
    db: Session, *, target_catalog_position: CatalogPosition, work_category_id: int | None,
    source_bucket_id: int,
) -> tuple[ContextBucket, bool]:
    """Get-or-create корзины цели с той же эффективной статьёй, что у корзины
    источника (спека §2.8) — заводит и её контекст по умолчанию, если корзина
    только что создана, `origin='review_merge'` (обязательное условие §2.14).

    Цепочка разделов для НОВОГО контекста — цепочка ПРЕДСТАВИТЕЛЬНОГО членства
    корзины источника (членства с наименьшим `position_item_id` среди
    переносимых слиянием — тот же приём, что
    `catalog_backfill._representative_position_id`), а не пустая: у группы
    перенесённых членств действительно нет ОДНОЙ образцовой цепочки, но взять
    чью-то, а не никакую, существенно для `LOCATION_ONLY`-имени под рабочим
    разделом — на пустой цепочке роль пересчиталась бы в
    `insufficient_description`, хотя состав описан не хуже, чем был у
    источника (тот же дефект, что нашла и исправила задача 11 для пересчёта
    словаря). Корзина источника без единого членства — законный редкий
    случай, тогда цепочка остаётся пустой (взять представителя не у кого).
    """
    representative_id = db.execute(
        sa.select(sa.func.min(ContextMember.position_item_id))
        .select_from(ContextMember)
        .join(CatalogContext, CatalogContext.id == ContextMember.context_id)
        .where(CatalogContext.bucket_id == source_bucket_id)
    ).scalar_one()
    chain = chapter_context(db, representative_id).chain if representative_id is not None else ()

    bucket, created, _context_created = context_routing_module._get_or_create_bucket_with_default_context(
        db,
        catalog_position=target_catalog_position,
        work_category_id=work_category_id,
        chain=chain,
        origin="review_merge",
    )
    return bucket, created


def _route_member_into_bucket(db: Session, *, member: ContextMember, target_bucket: ContextBucket) -> int:
    """Решает целевой контекст членства ВНУТРИ `target_bucket` — то же
    правило, что `_apply_routing` (правило по возрастанию `ordinal`, иначе
    действующее умолчание), — и записывает решение на `member` (`context_id`,
    `bucket_id`, `routed_by`, `routing_rule_id`). Возвращает новый `context_id`.

    Используется для ЛЮБОГО членства переносимой корзины, независимо от
    `routed_by`: корзина, в которой оно стояло, ЦЕЛИКОМ упраздняется слиянием
    (её собственный контекст архивируется и перевешивается на корзину цели,
    спека §2.8), поэтому решение — правило, умолчание или прежний ручной выбор
    — воспроизводится заново теми же двумя шагами, какими воспользовалась бы
    обычная маршрутизация, уже В КОРЗИНЕ ЦЕЛИ (`target_bucket` — та самая
    корзина, что уже взята под замок вызывающим `reconcile_contexts`; общая
    маршрутизация по ТЕКУЩЕЙ эффективной статье позиции сюда не годится — она
    вправе выбрать СОВСЕМ ДРУГУЮ корзину, вне набора локов слияния и в обход
    §2.8, требующего переноса ровно в корзину цели той же статьи, что у
    источника). `membership_state` эта функция не трогает — устаревшее
    (`STALE`) членство источника остаётся `STALE` и в корзине цели: перенос
    по новой статье — отдельное решение оператора (`accept_transfer`), не
    следствие слияния."""
    rules = (
        db.execute(
            sa.select(ContextRoutingRule)
            .where(ContextRoutingRule.bucket_id == target_bucket.id)
            .order_by(ContextRoutingRule.ordinal)
        )
        .scalars()
        .all()
    )
    chapters = chapter_context(db, member.position_item_id)
    matched_rule = next(
        (rule for rule in rules if evaluate_predicate(rule.predicate, chapters)), None
    )
    if matched_rule is not None:
        member.context_id = matched_rule.context_id
        member.routed_by = RoutedBy.rule.value
        member.routing_rule_id = matched_rule.id
    else:
        member.context_id = context_routing_module._live_default_context(db, target_bucket.id).id
        member.routed_by = RoutedBy.default.value
        member.routing_rule_id = None
    member.bucket_id = target_bucket.id
    db.flush()
    return member.context_id


def _transfer_members(
    db: Session, *, contexts: list[CatalogContext], target_bucket: ContextBucket
) -> tuple[dict[tuple[int, int], int], list[str]]:
    """Переносит ВСЕ членства контекстов `contexts` (корзины источника) в
    `target_bucket` — «обычной маршрутизацией цели» (спека §2.8): ОБА пути,
    `manual` и не-`manual`, идут через `_route_member_into_bucket` (см. её
    докстроку) — членство остаётся тем же фактом, каким бы `routed_by` оно ни
    несло, и маршрутизируется ВНУТРИ уже определённой и заблокированной
    `target_bucket`, а не по текущей эффективной статье позиции заново.
    Помечает конфликтные членства (`conflict_at`/`conflict_from_context_id`,
    спека §2.3, §2.8) по правилу `_conflict_warning`.

    Возвращает `(moved_counts, warnings)`: `moved_counts` — счёт перенесённых
    членств по паре `(new_context_id, from_context_id)`, ключ для события
    `members_moved` (предмет — приёмник, спека §2.14); `warnings` — тексты
    конфликтов слияния (спека §2.8), ОДИН на пару (исходный, целевой)
    контекст — конфликт называет расхождение РЕШЕНИЙ ДВУХ КОНТЕКСТОВ, а не
    отдельных членств: 40 позиций одной пары дали бы 40 одинаковых строк без
    дедупликации, хотя расходятся ровно два контекста, а не сорок пар.
    `conflict_at`/`conflict_from_context_id` при этом ставятся на КАЖДОЕ
    конфликтное членство — дедупликация только текста предупреждения.
    """
    context_ids = [context.id for context in contexts]
    members = (
        db.execute(sa.select(ContextMember).where(ContextMember.context_id.in_(context_ids)))
        .scalars()
        .all()
        if context_ids
        else []
    )
    original_context_by_member = {member.position_item_id: member.context_id for member in members}

    moved_counts: dict[tuple[int, int], int] = {}
    warnings_by_pair: dict[tuple[int, int], str] = {}
    now = _now()

    for member in members:
        from_context_id = original_context_by_member[member.position_item_id]
        new_context_id = _route_member_into_bucket(db, member=member, target_bucket=target_bucket)

        moved_counts[(new_context_id, from_context_id)] = (
            moved_counts.get((new_context_id, from_context_id), 0) + 1
        )

        source_ctx = db.get(CatalogContext, from_context_id)
        target_ctx = db.get(CatalogContext, new_context_id)
        warning = _conflict_warning(db, source_context=source_ctx, target_context=target_ctx)
        if warning is not None:
            member_row = db.get(ContextMember, member.position_item_id)
            member_row.conflict_at = now
            member_row.conflict_from_context_id = from_context_id
            db.flush()
            warnings_by_pair.setdefault((from_context_id, new_context_id), warning)

    return moved_counts, list(warnings_by_pair.values())


def _archive_contexts(db: Session, *, contexts: list[CatalogContext]) -> None:
    """Архивирует контексты исходной корзины, кроме уже архивных (спека
    §2.8) — `context_archived(reason='review_merge')` на каждый. Вызывается
    ПЕРЕД `_reweigh_contexts`: перевешенный ДЕЙСТВУЮЩИЙ (`archived_at IS
    NULL`) контекст по умолчанию столкнулся бы с действующим умолчанием
    корзины цели по частичному уникальному индексу
    `uq_catalog_contexts_default_per_bucket` — снятие этого порядка обязано
    упасть именно на нём (`TestArchiveBeforeReweighOrder`)."""
    now = _now()
    for context in contexts:
        if context.archived_at is None:
            context.archived_at = now
            db.flush()
            record_event(
                db,
                event_type="context_archived",
                context_id=context.id,
                payload={"reason": "review_merge"},
            )


def _reweigh_contexts(db: Session, *, contexts: list[CatalogContext], target_bucket_id: int) -> None:
    """Перевешивает `bucket_id` ВСЕХ контекстов исходной корзины на корзину
    цели (спека §2.8) — контекст со своим журналом переживает слияние, а не
    удаляется. Вызывается ПОСЛЕ `_transfer_members` (все членства уже сняты с
    этих контекстов) и ПОСЛЕ их архивирования (частичный уникальный индекс
    `is_default AND archived_at IS NULL` иначе столкнул бы перевешенное
    умолчание с действующим умолчанием корзины цели)."""
    for context in contexts:
        context.bucket_id = target_bucket_id
    db.flush()


def _drop_routing_rules(db: Session, *, bucket_id: int) -> int:
    """Считает и удаляет правила маршрутизации исходной корзины, ВОЗВРАЩАЯ
    их число (спека §2.8, §2.14: событие `routing_rules_dropped` пишется
    только при `count > 0`). Вызывается ДО `_reweigh_contexts` — не после и
    не полагаясь на каскад `context_routing_rules.bucket_id ON DELETE
    CASCADE` при удалении корзины: у правила ТОЖЕ составной FK
    `(bucket_id, context_id) → catalog_contexts (bucket_id, id)`
    (`fk_context_routing_rules_bucket_context`), и `_reweigh_contexts`,
    меняющая `bucket_id` контекста, упала бы на этом ограничении, пока
    правило ещё ссылается на СТАРУЮ пару (найдено прогоном
    `TestRoutingRulesDropped`, постоянным)."""
    rule_count = db.execute(
        sa.select(sa.func.count())
        .select_from(ContextRoutingRule)
        .where(ContextRoutingRule.bucket_id == bucket_id)
    ).scalar_one()
    if rule_count > 0:
        db.execute(sa.delete(ContextRoutingRule).where(ContextRoutingRule.bucket_id == bucket_id))
    return rule_count


def _delete_emptied_bucket(db: Session, *, bucket_id: int) -> None:
    """Удаляет опустевшую корзину источника (спека §2.8) — последнее звено
    цепочки `RESTRICT`. Правила уже удалены `_drop_routing_rules` раньше по
    протоколу; к моменту этого вызова корзина не несёт ничего, кроме себя."""
    db.execute(sa.delete(ContextBucket).where(ContextBucket.id == bucket_id))


def reconcile_contexts(
    db: Session, *, source_position_id: int, target_position_id: int
) -> list[str]:
    """Сводит корзины, контексты и членства исходной TO_REVIEW-строки в
    корзины целевой POSITION (спека §2.8, «Слияние в Review»). Шаг 3
    `merge_into_position_outcome`, встаёт ПЕРЕД `DELETE` каталожной строки
    (шаг 4) и ПОСЛЕ `UPDATE position_items`/записи `matching_cache` (шаги 1,
    2) — важно, что `position_items.catalog_position_id` уже указывает на
    цель к этому моменту: `chapter_context`, которую читает
    `_route_member_into_bucket` при подборе правила внутри корзины цели,
    разрешает цепочку разделов позиции именно по нему.

    Для каждой корзины источника: находит либо заводит корзину цели с той же
    эффективной статьёй (`_resolve_target_bucket`); переносит её членства
    (`_transfer_members`); архивирует контексты источника (кроме уже
    архивных) и перевешивает ВСЕ (`_reweigh_contexts`) — порядок «архивировать,
    потом перевесить» существен (см. докстроку `_reweigh_contexts`); отбрасывает
    правила маршрутизации источника с записью в журнал; удаляет опустевшую
    корзину (`_delete_emptied_bucket`).

    Возвращает список текстовых предупреждений о конфликте семантических
    решений — пустой, если конфликтов не было ни у одной корзины источника.
    """
    source_buckets = (
        db.execute(
            sa.select(ContextBucket)
            .where(ContextBucket.catalog_position_id == source_position_id)
            .order_by(ContextBucket.id)
        )
        .scalars()
        .all()
    )
    if not source_buckets:
        return []

    target_catalog_position = db.get(CatalogPosition, target_position_id)

    # Разрешаем/заводим корзины цели ДО блокировки: `work_category_id`
    # корзины — неизменяемое поле (тот же приём, что `context.bucket_id` в
    # `context_operations.py`), безопасно читать до лока. Только что
    # созданную корзину блокировать не нужно — её ещё никто не видит (спека
    # §2.8); это же решает, какие id войдут в набор локов ниже.
    resolved: list[tuple[ContextBucket, ContextBucket, bool]] = []
    for source_bucket in source_buckets:
        target_bucket, target_created = _resolve_target_bucket(
            db,
            target_catalog_position=target_catalog_position,
            work_category_id=source_bucket.work_category_id,
            source_bucket_id=source_bucket.id,
        )
        resolved.append((source_bucket, target_bucket, target_created))

    lock_ids = sorted(
        {source_bucket.id for source_bucket, _target_bucket, _created in resolved}
        | {
            target_bucket.id
            for _source_bucket, target_bucket, created in resolved
            if not created
        }
    )
    lock_buckets(db, lock_ids, exclusive=True)
    db.expire_all()  # см. docs/pitfalls/db.md — иначе следующий db.get вернёт кэш

    warnings: list[str] = []
    for source_bucket, target_bucket, _target_created in resolved:
        contexts = (
            db.execute(sa.select(CatalogContext).where(CatalogContext.bucket_id == source_bucket.id))
            .scalars()
            .all()
        )
        # Предмет `routing_rules_dropped` — действующий контекст по
        # умолчанию исходной корзины ДО слияния (спека §2.14) — читаем
        # ДО архивирования.
        live_default = context_routing_module._live_default_context(db, source_bucket.id)

        _archive_contexts(db, contexts=contexts)

        moved_counts, member_warnings = _transfer_members(
            db, contexts=contexts, target_bucket=target_bucket
        )
        warnings.extend(member_warnings)

        for (new_context_id, from_context_id), count in moved_counts.items():
            record_event(
                db,
                event_type="members_moved",
                context_id=new_context_id,
                payload={
                    "from_context_id": from_context_id,
                    "moved_members": count,
                    "reason": "review_merge",
                },
            )

        rule_count = _drop_routing_rules(db, bucket_id=source_bucket.id)

        _reweigh_contexts(db, contexts=contexts, target_bucket_id=target_bucket.id)

        if rule_count > 0:
            record_event(
                db,
                event_type="routing_rules_dropped",
                context_id=live_default.id,
                payload={"bucket_id": source_bucket.id, "count": rule_count, "reason": "review_merge"},
            )

        _delete_emptied_bucket(db, bucket_id=source_bucket.id)

    # `_delete_emptied_bucket` — Core-style `DELETE`, не синхронизирующий
    # identity map сессии: без этого `db.get(ContextBucket, ...)` вызывающего
    # кода вернул бы закэшированный, уже удалённый объект вместо `None`. Тот
    # же приём, что финальный `db.expire_all()` в `merge_into_position_outcome`
    # после её собственного `DELETE`.
    db.expire_all()

    return warnings


def merge_into_position_outcome(
    db: Session, *, to_review_id: int, target_id: int, resolver: UnitResolver | None = None
) -> MergeOutcome:
    """Сливает TO_REVIEW-строку с существующей POSITION. Возвращает `MergeOutcome`.

    Порядок операций — из §5 и спеки §2.8, и он существен:

    1. `UPDATE position_items` — перенос всех ссылок на цель;
    2. запись `matching_cache` (`manual`, бессрочно);
    3. `reconcile_contexts` — сводит корзины/контексты/членства источника в
       корзины цели (спека §2.8, задача 9 плана «Семьи и контексты»);
    4. `DELETE` TO_REVIEW-строки.

    Шаг 4 возможен только после шагов 1 и 3: FK `position_items.catalog_position_id`
    и цепочка `context_buckets → catalog_contexts → context_members` объявлены
    БЕЗ каскада, поэтому забытая ссылка не даст удалить строку — БД сама не
    пустит. А безопасность шага 4 для кэша обеспечена инвариантом «кэш никогда
    не указывает на TO_REVIEW»: иначе `ON DELETE CASCADE` у
    `matching_cache.catalog_position_id` молча снёс бы чужие записи.

    Args:
        db: сессия; транзакцией управляет вызывающий.
        to_review_id: строка очереди Review.
        target_id: каталожная POSITION, с которой сливаем.
        resolver: готовый резолвер единиц; передаётся при пакетной обработке
            очереди, чтобы не перечитывать справочник на каждое решение.

    Raises:
        ReviewError: не тот `kind` у источника или цели, либо слияние с собой.
            Проигравший гонку получает именно её: пока он ждал блокировку,
            строка была слита и удалена.
    """
    if to_review_id == target_id:
        raise ReviewError("Нельзя слить строку с собой.")

    locked = _lock_rows(db, [to_review_id, target_id])
    source = _require_kind(locked, to_review_id, CatalogKind.TO_REVIEW.value)
    _require_kind(locked, target_id, CatalogKind.POSITION.value)
    resolver = resolver or UnitResolver(db)

    moved = db.execute(
        sa.update(PositionItem)
        .where(PositionItem.catalog_position_id == to_review_id)
        .values(catalog_position_id=target_id)
        .execution_options(synchronize_session=False)
    ).rowcount

    _write_manual_cache(db, source, target_id, resolver)

    warnings = reconcile_contexts(
        db, source_position_id=to_review_id, target_position_id=target_id
    )

    deleted = db.execute(
        sa.delete(CatalogPosition).where(CatalogPosition.id == to_review_id)
    ).rowcount
    if deleted != 1:
        # Строка была под нашим FOR UPDATE, так что исчезнуть она не могла. Если
        # всё же исчезла — решение применено не к тому, что мы прочитали, и
        # коммитить его нельзя.
        raise ReviewError(
            f"Каталожная строка {to_review_id} исчезла во время слияния; решение отменено."
        )
    db.expire_all()

    log.info(
        "Review: строка %d слита с POSITION %d, перенесено позиций: %d",
        to_review_id,
        target_id,
        moved,
    )
    return MergeOutcome(moved_positions=moved or 0, warnings=warnings)


def merge_into_position(
    db: Session, *, to_review_id: int, target_id: int, resolver: UnitResolver | None = None
) -> int:
    """Совместимая обёртка над `merge_into_position_outcome`: сигнатура и
    возврат `int` — БЕЗ изменений, существующие утверждения
    `test_matching.py`/`test_review_concurrency.py` (`assert moved == 1` и
    т.п.) читают ровно этот контракт."""
    return merge_into_position_outcome(
        db, to_review_id=to_review_id, target_id=target_id, resolver=resolver
    ).moved_positions


#: `set_kind`-исходы, после которых строка перестаёт быть работой (спека
#: §2.5, таблица переходов): контексты строки переводятся в `NOT_APPLICABLE`.
_NOT_APPLICABLE_MANUAL_KINDS = frozenset({CatalogKind.HEADER.value, CatalogKind.TRASH.value})


def _lock_contexts_for_update(db: Session, context_ids: list[int]) -> None:
    """`FOR UPDATE` на `catalog_contexts` по возрастанию `id` — тот же
    примитив, что `services.work_families._lock_contexts` (её берут
    `confirm_kind`/`unconfirm_kind`). Без общего лока подтверждение вида,
    идущее параллельно с `set_kind(HEADER|TRASH)`, могло бы записать
    `semantic_kind`/`semantic_state='CONFIRMED'` уже ПОСЛЕ того, как эта
    функция прочитала контекст, и терминальность `NOT_APPLICABLE` (спека
    §2.5) была бы потеряна молча — обе стороны пишут одну и ту же строку без
    общей сериализации. Пустой список — no-op."""
    if not context_ids:
        return
    db.execute(
        sa.select(CatalogContext.id)
        .where(CatalogContext.id.in_(context_ids))
        .order_by(CatalogContext.id)
        .with_for_update()
    ).all()


def _mark_contexts_not_applicable(db: Session, *, catalog_position_id: int) -> None:
    """`set_kind(HEADER|TRASH)` → все контексты строки, во ВСЕХ её корзинах,
    `semantic_state='NOT_APPLICABLE'` (спека §2.5, таблица переходов).
    Членства не трогает — позиции никуда не делись, они просто перестают
    попадать в семантические поверхности. Событие по этому переходу спека не
    называет (`kind_set` — о виде WORK/SYSTEM, не о `semantic_state`) —
    поэтому здесь ничего не пишется в журнал.

    Контексты берутся `FOR UPDATE` (`_lock_contexts_for_update`) ДО записи
    `NOT_APPLICABLE`, перечитываются `expire_all()` после лока — тот же
    протокол «лок → перечитывание», что у операций `context_operations.py`,
    приложенный здесь к сериализации против `confirm_kind`/`unconfirm_kind`.
    """
    contexts = (
        db.execute(
            sa.select(CatalogContext)
            .join(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
            .where(ContextBucket.catalog_position_id == catalog_position_id)
        )
        .scalars()
        .all()
    )
    if not contexts:
        return
    context_ids = sorted(context.id for context in contexts)
    _lock_contexts_for_update(db, context_ids)
    db.expire_all()
    for context in contexts:
        if context.semantic_state != SemanticState.NOT_APPLICABLE.value:
            context.semantic_state = SemanticState.NOT_APPLICABLE.value
    db.flush()


def set_kind(
    db: Session, *, to_review_id: int, kind: str, resolver: UnitResolver | None = None
) -> CatalogPosition:
    """Утверждает TO_REVIEW-строку как POSITION либо помечает HEADER/TRASH.

    Строка не удаляется: её нормализованная пара остаётся за ней, и следующий
    get-or-create той же пары найдёт уже размеченную строку.

    `HEADER`/`TRASH` переводят контексты строки (во всех её корзинах, если
    они уже существуют) в `semantic_state='NOT_APPLICABLE'` (спека §2.5,
    §2.8) — членства целы, но семантические поверхности эти позиции больше не
    видят. `POSITION` контексты не трогает ни одним полем (спека §2.5, DoD 10).

    Raises:
        ReviewError: недопустимый `kind` или строка не в очереди Review.
    """
    if kind not in MANUAL_KINDS:
        allowed = ", ".join(MANUAL_KINDS)
        raise ReviewError(f"Недопустимый kind «{kind}»; оператор может ставить: {allowed}.")

    row = _require_kind(
        _lock_rows(db, [to_review_id]), to_review_id, CatalogKind.TO_REVIEW.value
    )
    resolver = resolver or UnitResolver(db)

    row.kind = kind
    db.flush()

    if kind in _NOT_APPLICABLE_MANUAL_KINDS:
        _mark_contexts_not_applicable(db, catalog_position_id=row.id)

    _write_manual_cache(db, row, row.id, resolver)

    log.info("Review: строке %d поставлен kind=%s", to_review_id, kind)
    return row
