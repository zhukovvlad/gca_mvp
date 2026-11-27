"""Замер кэша подготовленного этапа — критерий 1 дизайна
`docs/superpowers/design/2026-11-27-stage-cache.md` (спека, критерий 10).

Продолжение скрипта PR #90 (`../2026-11-18-our-price-slow-queries/measure.py`): те же вызовы,
те же тендер, этап, оффер и ряд, то же время SQL по событиям движка. Сверх него — тёплый вызов
и последовательность «записать цель → разбор, этап, панель, предпросмотр».

ПИШЕТ В БАЗУ (цель строки), поэтому запускается только на КОПИИ стенда, не на `gca_dev`:
скрипт отказывается работать, если имя базы — `gca_dev`. Запуск из backend/:

    DATABASE_URL=postgresql+psycopg://postgres@localhost:5459/<копия> \
        uv run python ../docs/superpowers/design/2026-11-27-stage-cache/measure_cache.py \
        --rounds 3 --out <файл.json>

Вызовы — функции маршрутов `routers/tenders.py` (с `decimal_json` и транзакцией решения
`_write_decision`, то есть с пометкой записи), каждый на своей сессии, как запрос:

    offer    разбор участника            GET …/offers/{offer}
    stage    экран этапа                 GET …/our-price
    line     панель строки               GET …/offers/{offer}/lines/{line}
    preview  предпросмотр файла          GET …/offers/{offer}/export-preview
    target   запись цели строки          PUT / DELETE …/lines/{line}/target (попеременно)

Фазы:
    cold     перед каждым вызовом оба кэша (сцен и обычных ставок) сброшены;
    warm     все четыре чтения прогреты, каждый вызов — повторный;
    after    круг «записать цель → offer, stage, line, preview».
Сверка `same`: после каждой фазы каждое чтение повторяется при выключенных обоих кэшах
(срок 0), ответы сравниваются побайтно. Печать — только ASCII.
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

from sqlalchemy import event  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from config import settings  # noqa: E402
from crud.common import DomainError  # noqa: E402
from database import SessionLocal, engine  # noqa: E402
from models import User, UserRole  # noqa: E402
from routers import tenders  # noqa: E402
from services.stage_cache import stage_scene_cache  # noqa: E402
from services.status_cache import typical_rates_cache  # noqa: E402

TENDER, ROUND, OFFER, SERIES = 7, 23, 76, 1
READS = ("offer", "stage", "line", "preview")

acc = {"sql_t": 0.0, "sql_n": 0}


@event.listens_for(engine, "before_cursor_execute")
def _before(conn, cursor, statement, params, context, executemany):
    context._t0 = time.perf_counter()


@event.listens_for(engine, "after_cursor_execute")
def _after(conn, cursor, statement, params, context, executemany):
    acc["sql_t"] += time.perf_counter() - context._t0
    acc["sql_n"] += 1


def call(fn):
    """Один «запрос»: своя сессия, закрытие в конце (как `get_db`)."""
    db = SessionLocal()
    acc.update(sql_t=0.0, sql_n=0)
    started = time.perf_counter()
    try:
        result = fn(db)
    finally:
        db.close()
    total = time.perf_counter() - started
    body = result.body if hasattr(result, "body") else result
    return {"total": total, "sql_t": acc["sql_t"], "sql_n": acc["sql_n"]}, body


def reads(line: int) -> dict:
    kw = {"inflation_series_id": SERIES}
    return {
        "offer": lambda db: tenders.our_price_offer(TENDER, ROUND, OFFER, db=db, **kw),
        "stage": lambda db: tenders.our_price_round(TENDER, ROUND, db=db, **kw),
        "line": lambda db: tenders.our_price_line(TENDER, ROUND, OFFER, line, level=None, scope=None, db=db, **kw),
        "preview": lambda db: tenders.our_price_export_preview(TENDER, ROUND, OFFER, db=db, **kw),
    }


def set_caches(on: bool, ttls: tuple[int, int]) -> None:
    settings.STAGE_SCENE_CACHE_TTL_S, settings.TYPICAL_RATES_CACHE_TTL_S = ttls if on else (0, 0)


def reset_caches() -> None:
    typical_rates_cache.invalidate()
    stage_scene_cache.invalidate()


def uncached(fns: dict, ttls: tuple[int, int]) -> dict:
    """Ответы при выключенных обоих кэшах; кэш затем включается обратно (его записи целы)."""
    set_caches(False, ttls)
    try:
        return {name: call(fn)[1] for name, fn in fns.items()}
    finally:
        set_caches(True, ttls)


def pick_line(admin) -> int:
    """Первая сравнимая строка-работа разбора, у которой «самая свежая» записывается."""
    _, body = call(lambda db: tenders.our_price_offer(TENDER, ROUND, OFFER, inflation_series_id=SERIES, db=db))
    view = json.loads(body)
    for row in view["lines"]:
        if row["kind"] == "system" or row["state"] != "comparable" or not row.get("freshest"):
            continue
        try:
            call(lambda db, pid=row["position_item_id"]: tenders.put_line_target(
                TENDER, ROUND, OFFER, pid, body=tenders.LadderTargetPut(kind="freshest"),
                inflation_series_id=SERIES, db=db, current_user=admin))
        except Exception as exc:  # noqa: BLE001 - отказ записи: берём следующую строку
            if isinstance(exc, DomainError) or getattr(exc, "status_code", None):
                continue
            raise
        return row["position_item_id"]
    raise SystemExit("no line accepts target 'freshest'")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    db_name = make_url(settings.DATABASE_URL).database
    if db_name == "gca_dev":
        raise SystemExit("refusing to write into gca_dev: point DATABASE_URL at a copy")
    ttls = (settings.STAGE_SCENE_CACHE_TTL_S or 600, settings.TYPICAL_RATES_CACHE_TTL_S or 300)
    set_caches(True, ttls)

    db = SessionLocal()
    admin = db.query(User).filter(User.role == UserRole.admin, User.is_active.is_(True)).order_by(User.id).first()
    db.close()
    if admin is None:
        raise SystemExit("no active admin in the copy")

    line = pick_line(admin)
    fns = reads(line)
    runs: dict = defaultdict(list)
    same: dict = defaultdict(lambda: True)

    def check(phase: str, got: dict) -> None:
        base = uncached(fns, ttls)
        for name in READS:
            same[f"{phase}/{name}"] = same[f"{phase}/{name}"] and got[name] == base[name]

    for _ in range(args.rounds):
        got = {}
        for name in READS:  # cold: каждый вызов после сброса обоих кэшей
            reset_caches()
            row, got[name] = call(fns[name])
            runs[("cold", name)].append(row)
        check("cold", got)

        for name in READS:  # прогрев всех ключей, затем тёплые вызовы
            call(fns[name])
        got = {}
        for name in READS:
            row, got[name] = call(fns[name])
            runs[("warm", name)].append(row)
        check("warm", got)

    for i in range(args.rounds):  # круг «записать цель → четыре перезапроса»
        for name in READS:
            call(fns[name])
        if i % 2 == 0:
            write = lambda db: tenders.delete_line_target(TENDER, ROUND, OFFER, line, db=db, _=admin)  # noqa: E731
        else:
            write = lambda db: tenders.put_line_target(  # noqa: E731
                TENDER, ROUND, OFFER, line, body=tenders.LadderTargetPut(kind="freshest"),
                inflation_series_id=SERIES, db=db, current_user=admin)
        generation = stage_scene_cache.generation()
        row, _ = call(write)
        runs[("after", "target")].append(row)
        got = {}
        for name in READS:
            row, got[name] = call(fns[name])
            runs[("after", name)].append(row)
        same[f"after/kept_cache_{i}"] = stage_scene_cache.generation() == generation
        check("after", got)

    summary = {"database": db_name, "tender": TENDER, "round": ROUND, "offer": OFFER, "series": SERIES,
               "line": line, "rounds": args.rounds, "ttls": ttls, "same": dict(same), "calls": {}}
    for (phase, name), rows in sorted(runs.items()):
        med = {k: round(statistics.median(r[k] for r in rows), 3) for k in rows[0]}
        summary["calls"][f"{phase}/{name}"] = {
            "median": med, "runs": [{k: round(v, 3) for k, v in r.items()} for r in rows]}
    Path(args.out).write_text(json.dumps(summary, ensure_ascii=True, indent=1), encoding="utf-8")
    for key, value in summary["calls"].items():
        m = value["median"]
        print(f"{key:16} total {m['total']:6.2f}  sql {m['sql_t']:6.2f} ({m['sql_n']:.0f})")
    print("same", json.dumps(summary["same"]))


if __name__ == "__main__":
    main()
