"""Кэш сцен этапа (спека `2026-11-27-stage-cache-design.md` §2, §6).

ЗАВИСИТ ОТ ОДНОГО ПРОЦЕССА uvicorn (AGENTS.md §3): значение живёт в памяти
процесса, и сброс, сделанный в одном процессе, второй не увидит.

Структура без знания о записях и о crud: ключ — `SceneKey`, значение — готовая
сцена. Поколение растёт на каждом `invalidate()`; сборщик берёт его до сборки и
отдаёт в `put`, и сцена, собранная через сброс, не сохраняется. Сборка идёт вне
замка. `put` сверяет поколение и вставляет в одной критической секции `_lock`;
`get` (с перестановкой порядка), `invalidate` и вытеснение берут тот же замок.
"""
from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Generic, TypeVar

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import TextClause

from config import settings
from services.status_cache import typical_rates_cache

T = TypeVar("T")

#: тендер, этап, оффер | весь этап, ряд | без ряда
SceneKey = tuple[int, int, int | None, int | None]


class SceneCache(Generic[T]):
    #: Часы — открытый атрибут: тесты подменяют их.
    clock: Callable[[], float]

    def __init__(
        self,
        ttl_seconds: Callable[[], float],
        size: Callable[[], int],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._size = size
        self.clock = clock
        self._lock = threading.Lock()
        self._generation = 0
        #: ключ -> (сцена, момент put); порядок — от давно использованной к недавней
        self._entries: OrderedDict[SceneKey, tuple[T, float]] = OrderedDict()

    def enabled(self) -> bool:
        return self._ttl_seconds() > 0

    def generation(self) -> int:
        with self._lock:
            return self._generation

    def get(self, key: SceneKey) -> T | None:
        ttl = self._ttl_seconds()
        if ttl <= 0:
            return None
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            value, stored_at = entry
            if self.clock() - stored_at >= ttl:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return value

    def put(self, key: SceneKey, value: T, generation: int) -> bool:
        if self._ttl_seconds() <= 0:
            return False
        with self._lock:
            if generation != self._generation:
                return False
            self._entries[key] = (value, self.clock())
            self._entries.move_to_end(key)
            limit = max(1, self._size())
            while len(self._entries) > limit:
                self._entries.popitem(last=False)
            return True

    def invalidate(self) -> None:
        with self._lock:
            self._generation += 1
            self._entries.clear()


stage_scene_cache: SceneCache = SceneCache(
    lambda: settings.STAGE_SCENE_CACHE_TTL_S, lambda: settings.STAGE_SCENE_CACHE_SIZE
)


# ---------------------------------------------------------------------------
#  Отслеживание записей (спека §4): сброс кэшей после коммита вне закрытого списка
# ---------------------------------------------------------------------------

#: Закрытый список: решения по строкам КП и отправленные файлы. Их запись кэш сцен не
#: устаревляет (решения и файлы читаются заново), поэтому помеченная транзакция,
#: записавшая только их, кэш не сбрасывает.
DECISION_TABLES: frozenset[str] = frozenset(
    {"kp_line_agreements", "kp_line_comments", "kp_family_rungs", "kp_line_targets", "kp_sent_files"}
)

#: Опция выполнения `sa.text(...).execution_options(stage_cache_read=True)`: явная пометка
#: «это чтение» для CTE-запроса, чей текст не начинается с SELECT (спека §4.2).
READ_OPTION = "stage_cache_read"

_WRITES_KEY = "_stage_cache_writes"
_MARKED_KEY = "_stage_cache_decision_marked"
#: Запись в неизвестную таблицу — заведомо вне закрытого списка.
_UNKNOWN_TABLE = "?"

# Пробелы и комментарии (`-- ...`, `/* ... */`) в начале текста запроса.
_LEADING_NOISE = re.compile(r"\A(?:\s+|--[^\n]*(?:\n|\Z)|/\*.*?\*/)*", re.DOTALL)
_STARTS_WITH_SELECT = re.compile(r"select\b", re.IGNORECASE)


def _record(session: Session, tables: Any) -> None:
    session.info.setdefault(_WRITES_KEY, set()).update(tables)


def mark_decision_write(session: Session) -> None:
    """Пометить текущую транзакцию как решение по строке или выгрузку (спека §4.3)."""
    session.info[_MARKED_KEY] = True


def is_dirty(session: Session) -> bool:
    """В текущей транзакции записано вне `DECISION_TABLES`, включая неизвестное (спека §5)."""
    return bool(session.info.get(_WRITES_KEY, set()) - DECISION_TABLES)


def _text_is_read(statement: TextClause, execution_options: Any) -> bool:
    if execution_options.get(READ_OPTION):
        return True
    body = statement.text[_LEADING_NOISE.match(statement.text).end():]
    return _STARTS_WITH_SELECT.match(body) is not None


def _flush_tables(session: Session) -> set[str]:
    tables: set[str] = set()
    for obj in (*session.new, *session.dirty, *session.deleted):
        tables.update(table.name for table in inspect(obj).mapper.tables)
    return tables


@event.listens_for(Session, "before_flush")
def _on_before_flush(session: Session, flush_context: Any, instances: Any) -> None:
    tables = _flush_tables(session)
    if tables:
        _record(session, tables)


@event.listens_for(Session, "do_orm_execute")
def _on_orm_execute(state: Any) -> None:
    statement = state.statement
    if state.is_select:
        return
    if isinstance(statement, TextClause):
        if not _text_is_read(statement, state.execution_options):
            _record(state.session, {_UNKNOWN_TABLE})
        return
    if state.is_insert or state.is_update or state.is_delete:
        name = getattr(getattr(statement, "table", None), "name", None)
        _record(state.session, {name or _UNKNOWN_TABLE})
        return
    _record(state.session, {_UNKNOWN_TABLE})


def _should_reset(session: Session) -> bool:
    writes = session.info.get(_WRITES_KEY, set())
    if not writes:
        return False
    return not (session.info.get(_MARKED_KEY) and writes <= DECISION_TABLES)


def _forget(session: Session) -> None:
    session.info.pop(_WRITES_KEY, None)
    session.info.pop(_MARKED_KEY, None)


@event.listens_for(Session, "after_commit")
def _on_after_commit(session: Session) -> None:
    # `after_commit` срабатывает и на RELEASE SAVEPOINT (`begin_nested`): внешний коммит
    # отличается тем, что вложенной транзакции уже нет (проверено на SQLAlchemy 2.0.35).
    if session.in_nested_transaction():
        return
    try:
        if _should_reset(session):
            # Порядок существенен (дизайн, «Правило сброса» п. 4): ставки раньше сцен.
            typical_rates_cache.invalidate()
            stage_scene_cache.invalidate()
    finally:
        _forget(session)


@event.listens_for(Session, "after_rollback")
def _on_after_rollback(session: Session) -> None:
    # Откат точки сохранения записанного не забывает: лишний сброс дешевле пропущенного.
    if session.in_nested_transaction():
        return
    _forget(session)
