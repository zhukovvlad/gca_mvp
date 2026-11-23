"""Замер скорости к фиче «строки, похожие на комплект» (спека §9 п. 11). Только чтение.

По трём самым большим КП снимка «до» (`before.json`) — разбор участника (`build_offer_view`), этап
(`build_round_view`) и панель первой сравнимой строки (`build_line_view`) в процессе, без HTTP: по
пять вызовов подряд, в зачёт — медиана вызовов 2–5 (тёплый кэш: у ветки — кэш обычных ставок, срок
по умолчанию). Отдельно у кода ветки — холодный пересчёт обычных ставок (`load_typical_rates`), пять
раз. Код базового коммита и код ветки запускаются этим же скриптом из своих деревьев.

Запуск (из `backend/` нужного дерева, база — из `.env`):
    PYTHONIOENCODING=utf-8 python .../speed.py ../tasks/lump-sum-run/before.json OUT.json
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

import sqlalchemy as sa  # noqa: E402

import crud.our_price as op  # noqa: E402
from database import SessionLocal  # noqa: E402
from models import InflationSeries, Offer, TenderRound  # noqa: E402


def timed(fn, n: int = 5) -> dict:
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return {"first": round(times[0], 3), "warm_median": round(statistics.median(times[1:]), 3)}


def main() -> None:
    before = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(sys.argv[2])
    biggest = sorted(before["offers"].items(), key=lambda kv: -len(kv[1]))[:3]
    db = SessionLocal()
    db.execute(sa.text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
    db.commit()
    result: dict = {"offers": []}
    try:
        series = db.execute(sa.select(InflationSeries.id).order_by(InflationSeries.id)).scalars().first()
        for offer_id, lines in biggest:
            tender_id, round_id = db.execute(
                sa.select(TenderRound.tender_id, TenderRound.id)
                .join(Offer, Offer.round_id == TenderRound.id)
                .where(Offer.id == int(offer_id))
            ).one()
            pid = next(int(p) for p, ln in lines.items() if ln["state"] == "comparable")
            ids = (tender_id, round_id, int(offer_id))
            result["offers"].append(
                {
                    "offer_id": int(offer_id),
                    "lines": len(lines),
                    "offer_view": timed(lambda: op.build_offer_view(db, *ids, inflation_series_id=series)),
                    "round_view": timed(
                        lambda: op.build_round_view(db, tender_id, round_id, inflation_series_id=series)
                    ),
                    "line_view": timed(
                        lambda: op.build_line_view(
                            db, *ids, pid, level=None, scope=None, inflation_series_id=series
                        )
                    ),
                }
            )
        try:
            from crud.lump_sum import load_typical_rates  # noqa: PLC0415
        except ImportError:
            load_typical_rates = None
        if load_typical_rates is not None:
            result["typical_rates_cold"] = timed(lambda: load_typical_rates(db))
    finally:
        db.rollback()
        db.close()
    out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
