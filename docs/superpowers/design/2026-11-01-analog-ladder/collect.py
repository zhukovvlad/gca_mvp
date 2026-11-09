"""Строки КП всех офферов через build_offer_view 4а (ИПЦ ряд 1; оффер без даты —
без ИПЦ) в JSONL для measure.py. Запуск из backend/, только чтение."""
import json, sys, time
sys.path.insert(0, ".")
import sqlalchemy as sa
from database import SessionLocal
from crud import our_price as op
db = SessionLocal()
offs = db.execute(sa.text("select o.id, r.id, r.tender_id, r.stage_no from offers o join tender_rounds r on r.id=o.round_id order by o.id")).all()
out = open(sys.argv[1], "w", encoding="utf-8"); t0 = time.time(); errs = 0
for oid, rid, tid, rno in offs:
    infl = True
    try:
        v = op.build_offer_view(db, tid, rid, oid, inflation_series_id=1)
    except Exception:
        infl = False
        db.rollback()
    try:
        v = v if infl else op.build_offer_view(db, tid, rid, oid, inflation_series_id=None)
    except Exception as e:
        errs += 1; print("ERR", oid, type(e).__name__, str(e)[:80]); continue
    for l in v["lines"]:
        out.write(json.dumps({"tender": tid, "round": rid, "round_no": rno, "offer": oid, "infl": infl, **{k: l.get(k) for k in
          ("position_item_id", "context_id", "family", "variant_label", "kind", "state", "kp", "rung", "ours", "to_ours", "range", "manual_rung", "unit_mismatch", "unit")}}, ensure_ascii=False, default=str) + "\n")
print("offers", len(offs), "errs", errs, "sec", round(time.time() - t0))
