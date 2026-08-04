"""Ручной матчинг: применение решений оператора (AGENTS.md §5, «Ручной матчинг»).

Здесь только транзакционные операции над каталогом и кэшем. Эндпоинты и экран
Review — фаза 5 (§9.5); сервис нужен уже в фазе 4, потому что на нём держатся два
обязательных теста §9.4: «слияние TO_REVIEW удаляет строку и не оставляет
кэш-записей на TO_REVIEW» и «ручное решение переживает истечение auto-TTL».

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
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from models import CatalogKind, CatalogPosition, MatchingCache, MatchSource, PositionItem
from services.matching import NORM_VERSION, cache_key
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
    кэша. Замер (фаза 5, разбор внешнего ревью): после коммита `TO_REVIEW →
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


def merge_into_position(
    db: Session, *, to_review_id: int, target_id: int, resolver: UnitResolver | None = None
) -> int:
    """Сливает TO_REVIEW-строку с существующей POSITION. Возвращает число позиций.

    Порядок операций — из §5 и он существен:

    1. `UPDATE position_items` — перенос всех ссылок на цель;
    2. запись `matching_cache` (`manual`, бессрочно);
    3. `DELETE` TO_REVIEW-строки.

    Шаг 3 возможен только после шага 1: FK `position_items.catalog_position_id`
    объявлен БЕЗ каскада, поэтому забытая ссылка не даст удалить строку — БД
    сама не пустит. А безопасность шага 3 для кэша обеспечена инвариантом
    «кэш никогда не указывает на TO_REVIEW»: иначе `ON DELETE CASCADE` у
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
    return moved or 0


def set_kind(
    db: Session, *, to_review_id: int, kind: str, resolver: UnitResolver | None = None
) -> CatalogPosition:
    """Утверждает TO_REVIEW-строку как POSITION либо помечает HEADER/TRASH.

    Строка не удаляется: её нормализованная пара остаётся за ней, и следующий
    get-or-create той же пары найдёт уже размеченную строку.

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

    _write_manual_cache(db, row, row.id, resolver)

    log.info("Review: строке %d поставлен kind=%s", to_review_id, kind)
    return row
