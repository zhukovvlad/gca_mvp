"""Разовый проход по существующему каталогу (спека §2.9; план, задача 11).

Строит членство «позиция → контекст» для строк сметы, которые уже лежат в
базе (импортированы до появления семантического контура), тем же правилом,
что маршрутизация на импорте (`services/context_routing.route_position`,
задача 4): «написание × эффективная статья». Отдельная команда, а не тело
миграции — три причины (спека §2.9):

* объём (сотни тысяч позиций на стенде) — партия с прогрессом и
  возможностью прерваться, а не одна транзакция;
* зависимость от кода приложения (`classify_name_role`, словарь мест) —
  миграция, импортирующая приложение, ломается на первом же расхождении
  версий;
* повторяемость — после смены `PLACE_DICTIONARY_VERSION` проход запускают
  снова, и он адресуется контекстам прежней версии, а не всему каталогу.

**Транзакционная граница** (решение плана 12): коммит на КАЖДУЮ завершённую
партию, не на весь проход разом — иначе прерывание откатывает сделанное
целиком, и обещание сохраняемого прогресса ложно на большом каталоге.
Прерванный проход оставляет каталог согласованным ЧАСТИЧНО (у каждой
ОБРАБОТАННОЙ позиции есть корзина, контекст и членство), и это нормальное
состояние, из которого выходят повторным запуском.

**Партия и повторный запуск** (решение исполнителя): партия — позиции
(`is_chapter = false`, `catalog_position_id` задан), у которых ЕЩЁ НЕТ
членства, в порядке `position_items.id`. Позиция с ЛЮБЫМ существующим
членством (`default`/`rule`/`manual`) повторным проходом не выбирается вовсе
— команда одноразовая по СМЫСЛУ (маршрутизирует то, что накопилось до
семантического контура), а не сверяющая заново то, что уже маршрутизировано
штатным путём (импортом либо операцией). Тот же фильтр защищает ДВЕ разные
вещи, и обе — своим входом теста: ручные переопределения (`routed_by =
'manual'`) — фильтр их не перечитывает вообще, хотя `_apply_routing` (задача
4) защитила бы их и сама; и членство `STALE` (задача 10, разнос статьи после
маршрутизации) — БЕЗ этого фильтра проход молча вернул бы такое членство в
`CURRENT` в корзину новой эффективной статьи, в обход штатного принятия
переноса оператором (`accept_transfer`, задача 10). Второе — настоящая
причина, по которой фильтр существует: без `STALE` защита `manual` сама по
себе не оправдала бы отдельный фильтр партии (её несла бы одна
`_apply_routing`).

**Пересчёт после смены версии словаря мест** — ВТОРАЯ, независимая от партий
позиций, стадия прохода: контексты с `place_dictionary_version` СТАРШЕ
текущей константы и `name_role_source = 'rule'` пересчитываются. Контексты с
`name_role_source = 'manual'` этим пересчётом не адресуются вовсе — запрос их
не находит (`docs/insights/claimed-property-needs-its-own-input.md`:
адресность проверяется входом, где НЕТ основания трогать строку, а не только
входом, где основание есть). Архивные контексты (`archived_at IS NOT NULL`)
тоже не адресуются — они не действующие, править им нечего.

`classify_name_role` зовётся с цепочкой разделов ПРЕДСТАВИТЕЛЬНОГО членства
контекста — членства с НАИМЕНЬШИМ `position_item_id` (`chapter_context` той
же функцией, что и при создании, `services/context_routing.py`), а НЕ с
пустой цепочкой. У контекста по умолчанию цепочка при СОЗДАНИИ была — её
дала позиция, создавшая корзину (`_create_default_context`,
`context_routing.py:406`), и пересчёт обязан читать чью-то цепочку ТЕМ ЖЕ
способом, иначе `LOCATION_ONLY` под рабочим разделом (`comparability_reason
= None` при создании — работа взята из раздела) при пустой цепочке
пересчитывался бы в `insufficient_description`, хотя словарь эту строку не
касался. Контекст БЕЗ единого членства (например, разделённый операцией
задачи 6 без единого оставшегося членства по умолчанию) пересчётом не
трогается вовсе — цепочку читать не у кого, версия и роль остаются как
есть; это учитывается счётчиком ВНУТРИ функции (см. `_recompute_stale_
name_roles`), но не полем `BackfillReport` — контракт задачи 11
(Interfaces плана) фиксирует ровно семь полей.

`fields_changed` считает ПОЛЯ, а не контексты: `place_dictionary_version`
меняется всегда (запрос находит контекст именно потому, что версия старше),
`name_role`/`comparability_reason` — только если пересчёт реально дал другое
значение. Когда роль меняется — пишется `name_role_set` (`from`, `to`,
`source='rule'`, `place_dictionary_version`, спека §2.14): смена роли
правилом после создания контекста возможна только в этом пересчёте, и её
история не должна пропадать молча. Когда роль не меняется (обычный случай —
дословарь не тронул большинство контекстов) — событие не пишется: пустой
факт «ничего не изменилось» в журнал не идёт (тот же принцип, что и у
`update_family`/`confirm_kind` в `services/work_families.py`).

Модуль импортирует `services.semantic_rules` ЦЕЛИКОМ (`import ... as
semantic_rules`), а не имя `PLACE_DICTIONARY_VERSION` по отдельности: тест
адресности подменяет константу монкипатчем НА МОДУЛЕ, и чтение через
атрибут модуля видит подмену при каждом обращении, тогда как `from X import
Y` связал бы имя один раз при импорте и подмену не увидел бы (тот же приём
нужен и `services/context_routing.py`/`services/work_families.py`, но они
вне файлов этой задачи — их защищает `route_position`, тестами задачи 4).
`classify_name_role` читает `PLACE_TOKENS`/`GENERIC_WORK_TOKENS` тем же
способом — своими же модульными глобалями `services/semantic_rules.py`, и
тест смены словаря подменяет их напрямую на модуле, не на имени.

`etc_category_share` — диагностика доли членств в «catch-all» статьях
классификатора (спека §1.8): статья опознаётся ПО ИМЕНИ, предикат —
`title LIKE 'Проч%'`. Префикс «Проч», а не «Прочее»: в справочнике
классификатора (миграция `2026_08_05_0005-work_categories.py`) есть статьи,
названные и «Прочее …», и «Прочие …» (`6.5.3` «Прочие модульные элементы»,
`18.99` «Прочие наружные сети»), и обе формы — «catch-all» статья по
смыслу. Код статьи признаком не служит: `.99` не гарантирует такого
названия, а статьи без `.99` (`6.1.3`, `7.4.8`) его несут. Это чтение уровня ОТЧЁТА, а не правило маршрутизации: сама маршрутизация
«Прочее»/«Прочие» не упоминает нигде (спека §1.8, довод «за», а не
«против» — «Прочее» полноценная статья). Знаменатель доли — ВСЕ членства
(`context_members`), а не число контекстов: несколько позиций сметы могут
разделять один контекст (одна и та же каталожная строка × статья), и доля
— о позициях, а не о контекстах.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from models import (
    CatalogContext,
    CatalogPosition,
    ContextBucket,
    ContextMember,
    DecisionSource,
    PositionItem,
    WorkCategory,
)
from services import semantic_rules
from services.context_routing import chapter_context, route_position
from services.semantic_events import record_event
from services.semantic_rules import classify_name_role


@dataclass(frozen=True)
class BackfillReport:
    buckets_created: int
    contexts_created: int
    members_created: int
    rows_without_bucket: int
    by_name_role: dict[str, int]
    by_semantic_kind: dict[str, int]
    fields_changed: int


def _now() -> datetime:
    return datetime.now(UTC)


def _count(db: Session, model) -> int:
    return db.execute(sa.select(sa.func.count()).select_from(model)).scalar_one()


def _chunks(items: list[int], size: int) -> Iterator[list[int]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _eligible_position_ids(db: Session) -> list[int]:
    """Позиции без существующего членства, в порядке `id` (см. докстроку
    модуля: позиция с ЛЮБЫМ членством — `default`/`rule`/`manual`/`STALE` —
    партией не выбирается вовсе)."""
    already_member = sa.select(ContextMember.position_item_id)
    stmt = (
        sa.select(PositionItem.id)
        .where(
            PositionItem.is_chapter.is_(False),
            PositionItem.catalog_position_id.is_not(None),
            PositionItem.id.not_in(already_member),
        )
        .order_by(PositionItem.id)
    )
    return list(db.execute(stmt).scalars().all())


def _route_batch(db: Session, position_item_ids: list[int]) -> None:
    for position_item_id in position_item_ids:
        route_position(db, position_item_id=position_item_id, origin="backfill")


def _representative_position_id(db: Session, context_id: int) -> int | None:
    """Позиция с наименьшим `position_item_id` среди ДЕЙСТВУЮЩИХ (не
    обязательно `CURRENT` — просто существующих) членств контекста, либо
    `None`, если членств нет вовсе (см. докстроку модуля)."""
    return db.execute(
        sa.select(sa.func.min(ContextMember.position_item_id)).where(
            ContextMember.context_id == context_id
        )
    ).scalar_one()


def _recompute_stale_name_roles(db: Session) -> tuple[int, int]:
    """Пересчитывает роль имени контекстов прежней версии словаря (см.
    докстроку модуля). Возвращает `(fields_changed, skipped_without_members)`
    — второе число не идёт в `BackfillReport` (контракт задачи 11
    фиксирован), но не теряется молча внутри функции."""
    stale = (
        db.execute(
            sa.select(CatalogContext)
            .where(
                CatalogContext.place_dictionary_version < semantic_rules.PLACE_DICTIONARY_VERSION,
                CatalogContext.name_role_source == DecisionSource.rule.value,
                CatalogContext.archived_at.is_(None),
            )
            .order_by(CatalogContext.id)
        )
        .scalars()
        .all()
    )
    if not stale:
        return 0, 0

    now = _now()
    fields_changed = 0
    skipped_without_members = 0
    new_version = semantic_rules.PLACE_DICTIONARY_VERSION

    for context in stale:
        representative_id = _representative_position_id(db, context.id)
        if representative_id is None:
            skipped_without_members += 1
            continue

        chapters = chapter_context(db, representative_id)
        catalog_position = context.bucket.catalog_position
        outcome = classify_name_role(catalog_position.standard_job_title, chapter_chain=chapters.chain)

        old_role = context.name_role
        old_reason = context.comparability_reason
        old_version = context.place_dictionary_version

        if outcome.role != old_role:
            fields_changed += 1
        if outcome.comparability_reason != old_reason:
            fields_changed += 1
        if new_version != old_version:
            fields_changed += 1

        context.name_role = outcome.role
        context.comparability_reason = outcome.comparability_reason
        context.name_role_at = now
        context.place_dictionary_version = new_version

        if outcome.role != old_role:
            record_event(
                db,
                event_type="name_role_set",
                context_id=context.id,
                payload={
                    "from": old_role,
                    "to": outcome.role,
                    "source": DecisionSource.rule.value,
                    "place_dictionary_version": new_version,
                },
            )

    db.flush()
    return fields_changed, skipped_without_members


def _rows_without_bucket(db: Session) -> int:
    """Каталожные строки, на которые не ссылается ни одна корзина — значит,
    ни одна строка сметы (спека §1.3: «следы заменённых смет»)."""
    has_bucket = sa.select(ContextBucket.catalog_position_id)
    return db.execute(
        sa.select(sa.func.count())
        .select_from(CatalogPosition)
        .where(CatalogPosition.id.not_in(has_bucket))
    ).scalar_one()


def _distribution(db: Session, column) -> dict[str, int]:
    rows = db.execute(
        sa.select(column, sa.func.count())
        .select_from(CatalogContext)
        .where(CatalogContext.archived_at.is_(None))
        .group_by(column)
    ).all()
    return {value: count for value, count in rows}


def run_backfill(
    db: Session, *, batch_size: int = 500, progress: Callable[[int], None] | None = None
) -> BackfillReport:
    """Маршрутизирует все ещё не обработанные строки сметы партиями,
    коммитя КАЖДУЮ завершённую партию (см. докстроку модуля), затем
    пересчитывает контексты прежней версии словаря мест. Идемпотентна:
    второй запуск на неизменных данных не создаёт ни одной строки и не
    меняет ни одного поля.
    """
    buckets_before = _count(db, ContextBucket)
    contexts_before = _count(db, CatalogContext)
    members_before = _count(db, ContextMember)

    eligible_ids = _eligible_position_ids(db)
    processed = 0
    for batch_ids in _chunks(eligible_ids, batch_size):
        _route_batch(db, batch_ids)
        db.commit()
        processed += len(batch_ids)
        if progress is not None:
            progress(processed)

    fields_changed, _skipped_without_members = _recompute_stale_name_roles(db)
    db.commit()

    buckets_after = _count(db, ContextBucket)
    contexts_after = _count(db, CatalogContext)
    members_after = _count(db, ContextMember)

    return BackfillReport(
        buckets_created=buckets_after - buckets_before,
        contexts_created=contexts_after - contexts_before,
        members_created=members_after - members_before,
        rows_without_bucket=_rows_without_bucket(db),
        by_name_role=_distribution(db, CatalogContext.name_role),
        by_semantic_kind=_distribution(db, CatalogContext.semantic_kind),
        fields_changed=fields_changed,
    )


def etc_category_share(db: Session) -> tuple[int, int]:
    """Членств в корзинах статей «Прочее»/«Прочие» и всего членств (спека
    §1.8). Статья опознаётся по имени: см. докстроку модуля."""
    etc_category_ids = sa.select(WorkCategory.id).where(WorkCategory.title.like("Проч%"))
    etc_count = db.execute(
        sa.select(sa.func.count())
        .select_from(ContextMember)
        .join(ContextBucket, ContextBucket.id == ContextMember.bucket_id)
        .where(ContextBucket.work_category_id.in_(etc_category_ids))
    ).scalar_one()
    total_count = _count(db, ContextMember)
    return etc_count, total_count
