"""Матчинг позиций сметы с каталогом работ (AGENTS.md §5, шаг 4).

Каскад для каждой позиции, кроме разделов:

1. `cache_key` в `matching_cache` → hit: привязать, продлить TTL только у
   `source='auto'`; счётчик — по `kind` целевой каталожной строки;
2. точное совпадение `(normalized_job_title, unit_id)` среди `kind='POSITION'` →
   привязать + записать кэш (`source='auto'`);
3. промах: атомарный get-or-create `TO_REVIEW` через
   `INSERT ... ON CONFLICT (sha256(нормализованное название), COALESCE(unit_id,-1))
   DO NOTHING` + повторный SELECT; дальше — по `kind` найденной строки.

**Инвариант (§5):** записи `matching_cache` НИКОГДА не указывают на строки
`kind='TO_REVIEW'`. Кэш пишется только в ветке 2, в не-POSITION ветках шага 3 и
при ручных решениях Review. Это условие безопасности `DELETE` TO_REVIEW-строки
при слиянии — схемой оно не выражается, его держит тест.

**Идентичность работы одна на всю систему** (§4): нормализованное название плюс
единица. Никаких вторых представлений строки: `normalized_job_title` в каталоге,
точное совпадение и `cache_key` считаются одной и той же функцией
`sanitize_text.normalize_job_title_with_lemmatization` и одним и тем же
`unit_norm` (`services.unit_resolution`).

**Хэш в индексе — техника, а не идентичность** (миграция 0003). btree не
индексирует значения длиннее 2704 байт, а в наименование сметы попадают
спецификации на несколько килобайт, поэтому `uq_catalog_positions_norm_hash_unit`
уникален по `(sha256(нормализованное название), COALESCE(unit_id,-1))`. Сравнение
работ по-прежнему идёт по ПОЛНОМУ тексту: каждая выборка по хэшу дополнена
проверкой самой пары, а строка, чей хэш совпал при разном тексте, не принимается —
такой случай поднимает явную ошибку (`_collision_error`), а не склеивает две
работы в одну расценку.

**Отступление от Go-референса** (`entities/manager.go`): в tenders-go ключ кэша
был `sha256(StandardJobTitle)` без единицы, а промах записывал новую каталожную
строку в `draft_catalog_id` как fallback для RAG-воркера. Здесь единица ОБЯЗАНА
входить в ключ, RAG/векторов нет (вне MVP), а промах даёт TO_REVIEW и очередь
ручного матчинга.

Разрешение групп, а не строк: каскад — чистая функция пары (название, единица),
поэтому позиции сначала группируются по паре, и на группу приходится один проход
каскада. На реальной смете это 2576 позиций против ~1100 уникальных пар.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from models import CatalogKind, CatalogPosition, CatalogStatus, MatchingCache, MatchSource, PositionItem
from parser.sanitize_text import normalize_job_title_with_lemmatization
from services.estimate_import import PositionToMatch
from utils import utcnow_aware

log = logging.getLogger(__name__)

#: Версия нормализации наименований. Входит в `cache_key` и в
#: `matching_cache.norm_version`. Инкремент — НЕ «поменял константу»: он требует
#: миграции перевыпуска ключей всех записей `source='manual'` из
#: (job_title_text, unit_text) и пересчёта `catalog_positions.normalized_job_title`
#: (AGENTS.md §4, §11). Меняется вместе с версиями spacy/модели/pymorphy3.
NORM_VERSION = 1

#: TTL автоматических записей кэша (§4). Ручные решения не истекают вовсе.
AUTO_CACHE_TTL_DAYS = 30

#: Kind-ы, к которым позиция привязывается без попадания в очередь Review и без
#: сравнения с нормативами: строка каталога уже размечена человеком как не-работа.
_NON_POSITION_KINDS = (CatalogKind.HEADER.value, CatalogKind.LOT_HEADER.value, CatalogKind.TRASH.value)

#: Сентинел «единицы нет» в уникальном индексе `uq_catalog_positions_norm_hash_unit`.
NO_UNIT_SENTINEL = -1

#: Выражение-арбитр `ON CONFLICT` и ключ выборок по каталогу.
#:
#: `literal_column`, а НЕ обычный `-1`: литерал SQLAlchemy отрендерил бы
#: СВЯЗАННЫЙ ПАРАМЕТР — `coalesce(unit_id, %(coalesce_1)s)`. Пока запрос
#: исполняется без подготовки, PostgreSQL сворачивает параметр в константу и
#: индекс находится; но psycopg3 готовит повторяющийся запрос после
#: `prepare_threshold=5`, а у подготовленного плана параметр остаётся параметром
#: и совпасть с выражением индекса уже не может: «there is no unique or exclusion
#: constraint matching the ON CONFLICT specification». То есть баг проявлялся бы
#: не на маленькой смете, а на настоящей — ровно там, где хуже всего.
#: Индекс `uq_catalog_positions_norm_hash_unit` создан raw SQL в миграции 0003 с
#: литералом -1, поэтому и здесь нужен литерал. То же требование — к аргументам
#: `replace()` в `norm_hash`.
_UNIT_ARBITER = sa.func.coalesce(CatalogPosition.unit_id, sa.literal_column(str(NO_UNIT_SENTINEL)))

#: Аргументы `replace()` перед приведением к `bytea` — тоже литералами, по той же
#: причине, что и сентинел единицы: выражение обязано совпасть с индексным.
#: Escape-строки (`E'…'`), а не обычные литералы: обычный `'\'` означает один
#: символ только при `standard_conforming_strings=on`, при `off` это
#: синтаксическая ошибка. `E'…'` не зависит от настройки и даёт то же дерево
#: выражения, поэтому индекс сопоставляется в любом случае (миграция 0003).
_BACKSLASH = sa.literal_column(r"E'\\'")
_BACKSLASH_DOUBLED = sa.literal_column(r"E'\\\\'")


def norm_hash(value):
    """`sha256` нормализованного названия — выражение индекса 0003.

    Обратные слэши удваиваются ПЕРЕД приведением: `text::bytea` разбирает вход как
    escape-формат bytea, поэтому «C:\\temp» приводится с ошибкой
    `invalid input syntax for type bytea`, а «\\x41» и «\\101» молча дают тот же
    байт, что и «A», — то есть три разные работы получили бы один хэш. После
    удвоения приведение побайтово равно UTF-8-представлению строки; это
    закреплено тестом `test_matching.py::TestNormHash`.

    `convert_to(x,'UTF8')` вместо приведения не годится: она объявлена `stable`, а
    выражение индекса обязано быть `immutable`.
    """
    escaped = sa.func.replace(value, _BACKSLASH, _BACKSLASH_DOUBLED)
    return sa.func.sha256(sa.cast(escaped, BYTEA))


#: Выражение-арбитр `ON CONFLICT` и ключ выборок по каталогу (см. `norm_hash`).
_NORM_HASH = norm_hash(CatalogPosition.normalized_job_title)


def _pair_key(normalized_title: str, unit_id: int | None) -> tuple[str, int]:
    """Пара в том виде, в котором её сравнивает идентичность работы (§4)."""
    return (normalized_title, NO_UNIT_SENTINEL if unit_id is None else unit_id)


def _hash_pair(normalized_title: str, unit_id: int | None):
    """Та же пара для индекса: хэш названия считает СЕРВЕР, не Python.

    Одинаковость `hashlib.sha256` и серверного `sha256` проверена тестом, но
    полагаться на неё в рабочем коде незачем: обе стороны сравнения вычисляет
    PostgreSQL, и вопрос кодировок в продукте просто не возникает.
    """
    return sa.tuple_(
        norm_hash(sa.literal(normalized_title)),
        sa.literal(NO_UNIT_SENTINEL if unit_id is None else unit_id),
    )


def _catalog_pair_filter(pairs: set[tuple[str, int | None]]):
    """Условие «пара есть в списке»: по индексу (хэш) И по полному тексту.

    Первое условие даёт Index Scan по `uq_catalog_positions_norm_hash_unit`,
    второе делает совпадение точным: идентичность работы — полная пара, а не её
    хэш. Без второго условия коллизия sha256 склеила бы две разные работы.
    """
    return sa.and_(
        sa.tuple_(_NORM_HASH, _UNIT_ARBITER).in_(
            [_hash_pair(title, unit_id) for title, unit_id in pairs]
        ),
        sa.tuple_(CatalogPosition.normalized_job_title, _UNIT_ARBITER).in_(
            [_pair_key(title, unit_id) for title, unit_id in pairs]
        ),
    )


def catalog_get_or_create_statement():
    """INSERT ... ON CONFLICT DO NOTHING для get-or-create каталожной строки.

    Вынесено из `_get_or_create_catalog_rows`, чтобы тест мог проверить сам
    отрендеренный SQL: арбитр обязан быть литеральным выражением индекса.
    """
    return pg_insert(CatalogPosition).on_conflict_do_nothing(
        index_elements=[_NORM_HASH, _UNIT_ARBITER]
    )


def cache_key(normalized_title: str, unit_norm: str, norm_version: int = NORM_VERSION) -> str:
    """Ключ кэша матчинга (§4).

    `sha256(norm_version || '|' || normalized_title || '|' || unit_norm)`.
    Единица ОБЯЗАТЕЛЬНА в ключе: без неё «кладка, м2» и «кладка, м3» получили бы
    один ключ и одну расценку.
    """
    payload = f"{norm_version}|{normalized_title}|{unit_norm}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class MatchCounters:
    """Пять счётчиков `import_jobs` (§4). Метрика DoD (§10) считается по ним."""

    positions_total: int = 0
    """Позиции, допущенные к каскаду: is_chapter = false и есть идентичность."""

    matched_cache: int = 0
    matched_exact: int = 0
    matched_nonposition: int = 0
    to_review: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "positions_total": self.positions_total,
            "matched_cache": self.matched_cache,
            "matched_exact": self.matched_exact,
            "matched_nonposition": self.matched_nonposition,
            "to_review": self.to_review,
        }


@dataclass
class MatchOutcome:
    counters: MatchCounters = field(default_factory=MatchCounters)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Group:
    """Уникальная пара (нормализованное название, единица) и её позиции."""

    normalized_title: str
    unit_id: int | None
    unit_norm: str
    display_title: str
    """Исходное наименование для `catalog_positions.standard_job_title`."""
    unit_text: str | None
    """Исходный текст единицы для `matching_cache.unit_text`."""
    position_ids: list[int] = field(default_factory=list)

    @property
    def key(self) -> str:
        return cache_key(self.normalized_title, self.unit_norm)


def match_positions(
    db: Session,
    positions: list[PositionToMatch],
    *,
    now: datetime | None = None,
) -> MatchOutcome:
    """Прогоняет каскад матчинга. Транзакцией управляет вызывающий (сессия B, §5).

    Args:
        db: сессия B — та же, в которой создана смета.
        positions: позиции, допущенные к каскаду (из `ImportOutcome`).
        now: точка отсчёта TTL; по умолчанию — текущее время UTC.

    Returns:
        `MatchOutcome` со счётчиками и предупреждениями.

    Raises:
        NormalizationUnavailableError: модель лемматизации недоступна. Пробрасывается
            наружу намеренно: матчинг обязан дать джобу упасть в `error`, а не
            деградировать до нелемматизированной строки — иначе каталог, кэш и
            матчер разъедутся молча (AGENTS.md §11).
    """
    now = now or utcnow_aware()
    outcome = MatchOutcome()

    groups, unidentifiable = _group_positions(positions)
    if unidentifiable:
        outcome.warnings.append(
            f"Позиций, наименование которых после нормализации становится пустым: "
            f"{unidentifiable}. Они сохранены в смете, но к матчингу не допущены."
        )
    if not groups:
        return outcome

    outcome.counters.positions_total = sum(len(g.position_ids) for g in groups.values())

    resolved: dict[str, int] = {}          # key группы → catalog_position_id
    cache_writes: list[dict] = []          # что дописать в кэш (source='auto')
    ttl_extensions: list[str] = []         # cache_key, которым продлить TTL

    pending = list(groups.values())

    # --- Ветка 1: кэш ------------------------------------------------------
    cached = _fetch_cache(db, [g.key for g in pending], now=now)
    still_pending: list[_Group] = []
    for group in pending:
        hit = cached.get(group.key)
        if hit is None:
            still_pending.append(group)
            continue
        catalog_id, kind, source = hit
        resolved[group.key] = catalog_id
        if source == MatchSource.auto.value:
            ttl_extensions.append(group.key)
        _count(outcome.counters, kind, len(group.position_ids), branch="cache", group=group)

    # --- Ветка 2: точное совпадение среди POSITION -------------------------
    pending = still_pending
    exact = _fetch_exact_positions(db, pending)
    still_pending = []
    for group in pending:
        catalog_id = exact.get((group.normalized_title, group.unit_id))
        if catalog_id is None:
            still_pending.append(group)
            continue
        resolved[group.key] = catalog_id
        cache_writes.append(_cache_row(group, catalog_id, now))
        outcome.counters.matched_exact += len(group.position_ids)

    # --- Ветка 3: атомарный get-or-create TO_REVIEW ------------------------
    pending = still_pending
    if pending:
        created = _get_or_create_catalog_rows(db, pending)
        for group in pending:
            row = created.get((group.normalized_title, group.unit_id))
            if row is None:
                raise _collision_error(group)
            catalog_id, kind = row
            resolved[group.key] = catalog_id
            if kind == CatalogKind.TO_REVIEW.value:
                # Кэш на TO_REVIEW НЕ пишется — инвариант §5.
                outcome.counters.to_review += len(group.position_ids)
            elif kind == CatalogKind.POSITION.value:
                # Строка появилась между ветками 2 и 3 (гонка) — это hit ветки 2.
                cache_writes.append(_cache_row(group, catalog_id, now))
                outcome.counters.matched_exact += len(group.position_ids)
            else:
                cache_writes.append(_cache_row(group, catalog_id, now))
                outcome.counters.matched_nonposition += len(group.position_ids)

    # --- Запись результата -------------------------------------------------
    _extend_auto_ttl(db, ttl_extensions, now=now)
    _write_cache(db, cache_writes)
    _bind_positions(db, groups, resolved)
    # Массовые UPDATE-ы шли мимо identity map (synchronize_session=False, иначе
    # ORM пытался бы синхронизировать тысячи объектов построчно). Сбрасываем
    # закэшированные атрибуты, чтобы дальше в этой же транзакции читалось то,
    # что реально в БД.
    db.expire_all()

    return outcome


# ---------------------------------------------------------------------------
#  Группировка
# ---------------------------------------------------------------------------

def _group_positions(positions: list[PositionToMatch]) -> tuple[dict[str, _Group], int]:
    """Группирует позиции по паре (нормализованное название, единица).

    Ключ группы — пара, а не исходная строка: «Кладка кирпича» и «кладка
    кирпичей» дают одну нормализованную строку, то есть одну работу.

    Разные исходные тексты одной единицы («м2» и «кв.м») попадают в одну группу:
    идентичность задаёт `unit_id`. В `matching_cache.unit_text` тогда сохраняется
    первый встретившийся текст — колонка нужна для перевыпуска ключей при
    инкременте `norm_version`, и любой из алиасов даёт при перевыпуске тот же
    `unit_norm`.
    """
    groups: dict[str, _Group] = {}
    unidentifiable = 0

    for item in positions:
        normalized = normalize_job_title_with_lemmatization(item.job_title)
        if not normalized:
            unidentifiable += 1
            continue

        key = cache_key(normalized, item.unit.unit_norm)
        group = groups.get(key)
        if group is None:
            group = _Group(
                normalized_title=normalized,
                unit_id=item.unit.unit_id,
                unit_norm=item.unit.unit_norm,
                display_title=item.job_title.strip(),
                unit_text=item.unit.raw_text,
            )
            groups[key] = group
        group.position_ids.append(item.position_item_id)

    return groups, unidentifiable


def _count(counters: MatchCounters, kind: str, positions: int, *, branch: str, group: _Group) -> None:
    """Счётчик по `kind` целевой каталожной строки (§5, ветка 1)."""
    if kind == CatalogKind.POSITION.value:
        counters.matched_cache += positions
    elif kind in _NON_POSITION_KINDS:
        counters.matched_nonposition += positions
    else:
        # Инвариант §5 нарушен: кэш указывает на TO_REVIEW. Такая запись делает
        # DELETE при слиянии в Review небезопасным, поэтому это ошибка, а не шум.
        log.error(
            "Инвариант §5 нарушен: запись кэша (ветка %s) указывает на строку kind=%s "
            "для пары (%r, unit_id=%s)",
            branch,
            kind,
            group.normalized_title,
            group.unit_id,
        )
        counters.to_review += positions


# ---------------------------------------------------------------------------
#  Запросы
# ---------------------------------------------------------------------------

def _fetch_cache(
    db: Session, keys: list[str], *, now: datetime
) -> dict[str, tuple[int, str, str]]:
    """Живые записи кэша по списку ключей: key → (catalog_position_id, kind, source).

    Истёкшие `auto`-записи не возвращаются — это промах, и дальше сработает ветка
    2 с перезаписью записи (`ON CONFLICT (cache_key) DO UPDATE`). Ручные записи
    (`expires_at IS NULL`) не истекают никогда (§4).
    """
    if not keys:
        return {}
    rows = db.execute(
        sa.select(
            MatchingCache.cache_key,
            MatchingCache.catalog_position_id,
            CatalogPosition.kind,
            MatchingCache.source,
        )
        .join(CatalogPosition, CatalogPosition.id == MatchingCache.catalog_position_id)
        .where(
            MatchingCache.cache_key.in_(keys),
            MatchingCache.norm_version == NORM_VERSION,
            sa.or_(MatchingCache.expires_at.is_(None), MatchingCache.expires_at > now),
        )
    ).all()
    return {key: (catalog_id, kind, source) for key, catalog_id, kind, source in rows}


def _fetch_exact_positions(
    db: Session, groups: list[_Group]
) -> dict[tuple[str, int | None], int]:
    """Точные совпадения среди `kind='POSITION'` по паре (название, единица)."""
    if not groups:
        return {}
    pairs = {(g.normalized_title, g.unit_id) for g in groups}
    rows = db.execute(
        sa.select(
            CatalogPosition.normalized_job_title, CatalogPosition.unit_id, CatalogPosition.id
        ).where(
            CatalogPosition.kind == CatalogKind.POSITION.value,
            _catalog_pair_filter(pairs),
        )
    ).all()
    return {(title, unit_id): row_id for title, unit_id, row_id in rows}


def _get_or_create_catalog_rows(
    db: Session, groups: list[_Group]
) -> dict[tuple[str, int | None], tuple[int, str]]:
    """Атомарный get-or-create каталожных строк (§5, ветка 3).

    `ON CONFLICT (sha256(...), COALESCE(unit_id, -1)) DO NOTHING` целится ровно в
    `uq_catalog_positions_norm_hash_unit` — индекс по выражению, созданный raw SQL
    в миграции 0003. Повторный SELECT нужен именно из-за `DO NOTHING`: при
    конфликте INSERT не возвращает строку, а нам нужна существующая — с её
    настоящим `kind`. Он же отличает настоящий конфликт от коллизии хэша: строка
    ищется по паре целиком (`_catalog_pair_filter`).

    Два промаха с одинаковой нормализованной парой в одном файле дают ОДНУ
    TO_REVIEW-строку: они попали в одну группу, а на группу приходится одна
    вставка.
    """
    # Сортировка по паре — канонический порядок захвата замков. §4 разрешает
    # параллельный импорт разных допсоглашений (BackgroundTasks выполняет их в
    # потоках), и две executemany-партии, вставляющие пересекающиеся пары в
    # разном порядке, могли бы взаимно заблокироваться на ON CONFLICT. Единый
    # порядок делает это невозможным по построению.
    db.execute(
        catalog_get_or_create_statement(),
        [
            {
                "standard_job_title": g.display_title,
                "normalized_job_title": g.normalized_title,
                "unit_id": g.unit_id,
                "kind": CatalogKind.TO_REVIEW.value,
                # Статус про векторную индексацию (вне MVP, §5) — 'na'.
                "status": CatalogStatus.na.value,
            }
            for g in sorted(groups, key=lambda g: _pair_key(g.normalized_title, g.unit_id))
        ],
    )

    pairs = {(g.normalized_title, g.unit_id) for g in groups}
    rows = db.execute(
        sa.select(
            CatalogPosition.normalized_job_title,
            CatalogPosition.unit_id,
            CatalogPosition.id,
            CatalogPosition.kind,
        ).where(_catalog_pair_filter(pairs))
    ).all()
    return {(title, unit_id): (row_id, kind) for title, unit_id, row_id, kind in rows}


def _collision_error(group: _Group) -> RuntimeError:
    """Хэш названия занят строкой с ДРУГИМ текстом (миграция 0003).

    После `ON CONFLICT DO NOTHING` строка обязана найтись повторным SELECT-ом. Не
    нашлась — значит вставку отбил уникальный индекс по `sha256`, а сравнение по
    полному тексту эту строку не приняло: коллизия sha256. Вероятность
    практически нулевая, но ответ на неё — громкий отказ, а не привязка позиции к
    чужой работе: смета откатится (§5, сессия B), job уйдёт в `error`.
    """
    return RuntimeError(
        "Коллизия sha256 в каталоге работ: хэш нормализованного названия уже занят "
        "строкой с другим текстом, поэтому идентичность работы определить нельзя. "
        f"Пара: ({group.normalized_title[:200]!r}…, unit_id={group.unit_id}). "
        "Смета не сохранена; сообщите об этом — случай требует разбора."
    )


def _cache_row(group: _Group, catalog_position_id: int, now: datetime) -> dict:
    """Автоматическая запись кэша. TTL обязателен: у 'auto' `expires_at` NOT NULL."""
    return {
        "cache_key": group.key,
        "norm_version": NORM_VERSION,
        "job_title_text": group.display_title,
        "unit_text": group.unit_text,
        "catalog_position_id": catalog_position_id,
        "source": MatchSource.auto.value,
        "expires_at": now + timedelta(days=AUTO_CACHE_TTL_DAYS),
    }


def _write_cache(db: Session, rows: list[dict]) -> None:
    """Записывает автоматические записи кэша.

    `DO UPDATE`, а не `DO NOTHING`: по тому же ключу могла лежать ИСТЁКШАЯ
    auto-запись (её ветка 1 не увидела) — её надо освежить. Ручные записи
    (`source='manual'`) сюда не попадают: они дают hit в ветке 1 и никогда не
    истекают, поэтому переписать их автоматике невозможно.

    Партия отсортирована по ключу — канонический порядок захвата замков между
    параллельными импортами разных допсоглашений (§4), как в get-or-create.
    Одиночному UPDATE в `_extend_auto_ttl` порядок так не навяжешь (его выбирает
    планировщик), но и цена дедлока там — abort одной транзакции и job в
    `error`, повторная загрузка законна; порчи данных нет.
    """
    if not rows:
        return
    rows = sorted(rows, key=lambda row: row["cache_key"])
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
    db.execute(stmt, rows)


def _extend_auto_ttl(db: Session, keys: list[str], *, now: datetime) -> None:
    """Продлевает TTL авто-записей при hit (§4). Ручные не трогает — им нечего продлевать."""
    if not keys:
        return
    db.execute(
        sa.update(MatchingCache)
        .where(
            MatchingCache.cache_key.in_(keys),
            MatchingCache.source == MatchSource.auto.value,
        )
        .values(expires_at=now + timedelta(days=AUTO_CACHE_TTL_DAYS))
        .execution_options(synchronize_session=False)
    )


def _bind_positions(db: Session, groups: dict[str, _Group], resolved: dict[str, int]) -> None:
    """Проставляет `position_items.catalog_position_id` по группам."""
    payload = [
        {"id": position_id, "catalog_position_id": resolved[key]}
        for key, group in groups.items()
        if key in resolved
        for position_id in group.position_ids
    ]
    if not payload:
        return
    # ORM bulk UPDATE by primary key: одна executemany-партия на всю смету.
    # Идёт мимо identity map (см. expire_all в match_positions), но это
    # SQLAlchemy-конструкция, а не текстовый SQL, поэтому `updated_at` с его
    # `onupdate` попадает в UPDATE (`docs/phase4-start.md` §7). В рамках одной
    # транзакции значение не изменится: `now()` в PG постоянен внутри транзакции.
    db.execute(sa.update(PositionItem), payload)
