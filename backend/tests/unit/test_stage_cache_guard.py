"""Страж путей записи, которых отслеживание кэша сцен не видит (спека
`2026-11-27-stage-cache-design.md` §4.1).

Отслеживание ловит ORM-flush и `Session.execute` с Insert/Update/Delete и `sa.text`. Не
ловятся: массовые `bulk_*` сессии, запись мимо сессии (`exec_driver_sql`,
`connection().execute`) и DML внутри CTE выборки. Обход каждой выборки ради них слишком
дорог на сборке сцены, поэтому в коде приложения таких путей быть не должно — этот тест
падает, когда появится первый. Без БД.
"""
from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]

_DIRS = ("crud", "services", "routers", "parser", "money", "seeds")
_FORBIDDEN = re.compile(
    r"bulk_save_objects|bulk_insert_mappings|bulk_update_mappings|exec_driver_sql|\.connection\(\)\.execute"
)
_CTE_CALL = re.compile(r"\.cte\(")
_DML_IN_WINDOW = re.compile(r"\b(?:insert|update|delete)\(|\.returning\(")


def _dml_cte_hits(text: str) -> list[int]:
    """Позиции `.cte(`, перед которыми в том же абзаце (до пустой строки) стоит DML.

    Эвристика без разбора кода: окно в 800 знаков назад, обрезанное по последней пустой
    строке. Ложное срабатывание лечится правкой кода; цепочку
    `update(...).returning(...).cte(` она не пропускает."""
    hits = []
    for match in _CTE_CALL.finditer(text):
        window = text[max(0, match.start() - 800):match.start()]
        window = window.rsplit("\n\n", 1)[-1]
        if _DML_IN_WINDOW.search(window):
            hits.append(match.start())
    return hits


def _application_files() -> list[Path]:
    files = list(BACKEND.glob("*.py"))
    for name in _DIRS:
        files.extend((BACKEND / name).rglob("*.py"))
    # модуль отслеживания описывает эти пути словами
    return [p for p in files if p != BACKEND / "services" / "stage_cache.py"]


def test_application_code_has_no_unseen_write_paths():
    offenders: list[str] = []
    for path in _application_files():
        text = path.read_text(encoding="utf-8")
        positions = [m.start() for m in _FORBIDDEN.finditer(text)] + _dml_cte_hits(text)
        for pos in positions:
            offenders.append(f"{path.relative_to(BACKEND)}:{text.count(chr(10), 0, pos) + 1}")
    assert not offenders, (
        "Путь записи, который отслеживание кэша сцен не видит (спека кэша сцен §4.1): "
        "расширьте отслеживание или уберите путь.\n" + "\n".join(offenders)
    )


def test_guard_patterns_detect_known_shapes():
    assert _FORBIDDEN.search("session.bulk_insert_mappings(M, rows)")
    assert _FORBIDDEN.search("db.connection().execute(stmt)")
    assert _FORBIDDEN.search("conn.exec_driver_sql('x')")
    assert _dml_cte_hits("sa.update(T).values(a=1).returning(T.id).cte('u')")
    assert _dml_cte_hits("x = insert(T).values(a=1)\n.cte('i')")
    assert not _dml_cte_hits("sa.select(T.id).cte('c')")
    assert not _dml_cte_hits("a = update(T)\n\nb = sa.select(T.id).cte('c')")
