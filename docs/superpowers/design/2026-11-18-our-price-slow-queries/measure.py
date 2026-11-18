"""Замер «до / после» двух медленных запросов экранов 4а/4б — артефакт дизайна
`docs/superpowers/design/2026-11-18-our-price-slow-queries.md`.

Только чтение: каждый вызов в своей транзакции с откатом, записи нет. «После» —
прототипы двух запросов, подставленные на время замера вместо функций модулей;
код приложения не меняется.

Запуск из backend/ (база — DATABASE_URL из backend/.env):

    uv run python ../docs/superpowers/design/2026-11-18-our-price-slow-queries/measure.py \
        --rounds 3 --out <файл.json> --plans <каталог>

Печать — только ASCII. Каждый вызов (разбор с ИПЦ, панель строки, файл подрядчику)
измеряется по кругам попеременно «до» и «после», чтобы нагрузка машины делилась
поровну. В каждом замере: общее время, время и число всех SQL, время и число двух
запросов отдельно (по тексту запроса).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, ".")

import sqlalchemy as sa  # noqa: E402
from sqlalchemy import event  # noqa: E402

from crud import kp_sent_files, our_price, our_price_load  # noqa: E402
from database import SessionLocal, engine  # noqa: E402
from services import context_routing  # noqa: E402
from services.context_routing import RoutingError  # noqa: E402

TENDER, ROUND, OFFER, SERIES = 7, 23, 76, 1

CHAIN_AFTER = """
WITH RECURSIVE chain(start_id, current_id, next_id, title, number, depth) AS (
    SELECT pi.id, pi.id, pi.chapter_item_id, pi.job_title_in_proposal, pi.item_number_in_proposal, 0
    FROM position_items pi
    WHERE pi.id = ANY(:ids)
    UNION ALL
    SELECT chain.start_id, p.id, p.chapter_item_id, p.job_title_in_proposal, p.item_number_in_proposal,
           chain.depth + 1
    FROM chain
    CROSS JOIN LATERAL (
        SELECT q.id, q.chapter_item_id, q.job_title_in_proposal, q.item_number_in_proposal
        FROM position_items q
        WHERE q.id = chain.next_id
        OFFSET 0
    ) p
)
CYCLE current_id SET is_cycle USING visited_path
SELECT start_id, current_id, depth, title, number, is_cycle
FROM chain
ORDER BY start_id, depth
"""

CONFLICT_AFTER = """
WITH cpv AS MATERIALIZED (
    SELECT context_id, parameter_id
    FROM context_parameter_values
    WHERE context_id = ANY(:ids) AND source = 'path_conflict'
)
SELECT cpv.context_id, fp.schema_id, fp.id
FROM cpv JOIN family_parameters fp ON fp.id = cpv.parameter_id
WHERE fp.role = 'main' AND fp.archived_at IS NULL
"""

ORIGINAL = {
    "chain": context_routing._chapter_chain_rows,
    "conflict": our_price_load._conflicted_parameters,
}


def chain_after(db, chapter_item_ids):
    ids = list(dict.fromkeys(chapter_item_ids))
    if not ids:
        return []
    rows = db.execute(
        sa.text(CHAIN_AFTER).bindparams(sa.bindparam("ids", value=ids, type_=sa.ARRAY(sa.BigInteger)))
    ).all()
    for row in rows:
        if row.is_cycle:
            raise RoutingError(f"cycle at {row.start_id}")
    return rows


def conflict_after(db, context_ids):
    ids = sorted(set(context_ids))
    if not ids:
        return {}
    grouped = defaultdict(lambda: defaultdict(set))
    for context_id, schema_id, parameter_id in db.execute(
        sa.text(CONFLICT_AFTER).bindparams(sa.bindparam("ids", value=ids, type_=sa.ARRAY(sa.BigInteger)))
    ).all():
        grouped[context_id][schema_id].add(parameter_id)
    return {c: {s: frozenset(p) for s, p in by.items()} for c, by in grouped.items()}


def use(variant: str) -> None:
    if variant == "before":
        context_routing._chapter_chain_rows = ORIGINAL["chain"]
        our_price_load._conflicted_parameters = ORIGINAL["conflict"]
    else:
        context_routing._chapter_chain_rows = chain_after
        our_price_load._conflicted_parameters = conflict_after


def kind_of(statement: str) -> str | None:
    if "WITH RECURSIVE chain" in statement:
        return "chain"
    if "path_conflict" in statement or ("family_parameters.schema_id" in statement
                                        and "context_parameter_values.source" in statement):
        return "conflict"
    return None


acc: dict = {"variant": "before", "sql_t": 0.0, "sql_n": 0, "chain_t": 0.0, "chain_n": 0,
              "conflict_t": 0.0, "conflict_n": 0}


@event.listens_for(engine, "before_cursor_execute")
def _before(conn, cursor, statement, params, context, executemany):
    context._t0 = time.perf_counter()


@event.listens_for(engine, "after_cursor_execute")
def _after(conn, cursor, statement, params, context, executemany):
    took = time.perf_counter() - context._t0
    acc["sql_t"] += took
    acc["sql_n"] += 1
    kind = kind_of(statement)
    if kind is not None:
        acc[kind + "_t"] += took
        acc[kind + "_n"] += 1
        acc.setdefault("captured", {}).setdefault((acc["variant"], kind), (statement, params))


def measure(variant: str, fn) -> dict:
    use(variant)
    acc.update(variant=variant, sql_t=0.0, sql_n=0, chain_t=0.0, chain_n=0, conflict_t=0.0, conflict_n=0)
    started = time.perf_counter()
    result = fn()
    total = time.perf_counter() - started
    return {"total": total, **{k: acc[k] for k in ("sql_t", "sql_n", "chain_t", "chain_n", "conflict_t", "conflict_n")},
            "_result": result}


def comparable(result):
    """Сравнимый вид результата: ответ-словарь как есть, файл — его строки."""
    if isinstance(result, tuple):
        return repr(result)
    return json.dumps(result, sort_keys=True, default=str)


def explain(db, plans_dir: Path) -> None:
    conn = db.connection().connection.driver_connection
    plans_dir.mkdir(parents=True, exist_ok=True)
    for (variant, kind), (statement, params) in sorted(acc.get("captured", {}).items()):
        with conn.cursor() as cur:
            cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + statement, params)
            text = "\n".join(line for (line,) in cur.fetchall())
        conn.rollback()
        # Списки идентификаторов в плане заменяются их числом — они длинные и ничего не говорят.
        import re

        text = re.sub(r"'\{[0-9,]+\}'", lambda m: f"'{{...{m.group(0).count(',') + 1} ids}}'", text)
        (plans_dir / f"{kind}-{variant}.txt").write_text(text + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--out", required=True)
    parser.add_argument("--plans", required=True)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        db.execute(sa.text("SET TRANSACTION READ ONLY"))
        view = our_price.build_offer_view(db, TENDER, ROUND, OFFER, inflation_series_id=SERIES)
        line = next(row for row in view["lines"] if row.get("family") and row.get("rung"))["position_item_id"]
        db.rollback()

        calls = {
            "offer": lambda: our_price.build_offer_view(db, TENDER, ROUND, OFFER, inflation_series_id=SERIES),
            "line": lambda: our_price.build_line_view(
                db, TENDER, ROUND, OFFER, line, level=None, scope=None, inflation_series_id=SERIES),
            "file": lambda: kp_sent_files.build_file(db, TENDER, ROUND, OFFER, inflation_series_id=SERIES),
        }
        runs: dict = defaultdict(list)
        same: dict = {}
        for _ in range(args.rounds):
            for name, fn in calls.items():
                results = {}
                for variant in ("before", "after"):
                    db.execute(sa.text("SET TRANSACTION READ ONLY"))
                    row = measure(variant, fn)
                    db.rollback()
                    results[variant] = comparable(row.pop("_result"))
                    runs[(name, variant)].append(row)
                same[name] = same.get(name, True) and results["before"] == results["after"]
        use("before")
        explain(db, Path(args.plans))
    finally:
        use("before")
        db.rollback()
        db.close()

    summary = {"tender": TENDER, "round": ROUND, "offer": OFFER, "series": SERIES, "line": line,
               "rounds": args.rounds, "same_response": same, "calls": {}}
    for (name, variant), rows in sorted(runs.items()):
        med = {k: round(statistics.median(r[k] for r in rows), 3) for k in rows[0]}
        summary["calls"][f"{name}/{variant}"] = {"median": med, "runs": [{k: round(v, 3) for k, v in r.items()} for r in rows]}
    Path(args.out).write_text(json.dumps(summary, ensure_ascii=True, indent=1), encoding="utf-8")
    for key, value in summary["calls"].items():
        m = value["median"]
        print(f"{key:14} total {m['total']:6.2f}  sql {m['sql_t']:6.2f} ({m['sql_n']:.0f})  "
              f"chain {m['chain_t']:5.2f} ({m['chain_n']:.0f})  conflict {m['conflict_t']:5.2f} ({m['conflict_n']:.0f})")
    print("same_response", same)


if __name__ == "__main__":
    main()
