"""Прогон «до/после» к фиче «зона как предпочтение» (спека §7 п. 13). Только чтение.

Режим `snapshot OUT.json [--drop-zoneless-history]` — по всем КП базы ответ разбора участника
(`build_offer_view`, ряд индексов — первый по id, как в `measure_zone.py`), строки-работы, на
строку проекция замера: состояние, ступень, вилка, три цифры, число аналогов, разница, причина
без ступени; сверх проекции — зона строки и семья (для разбора прибыли, в сравнение не входят).
`--drop-zoneless-history` убирает из истории подбора строки договоров без зоны (подменой
`crud.our_price.load_work_history` в памяти процесса) — так из прибыли выделяется доля строк
договоров без зоны. Снимок «до» снимается кодом ветки до задачи 2 (подбор прежний).

Режим `compare BEFORE AFTER [AFTER_NO_ZONELESS] [OUT.json]` — нормализация спеки §7 п. 13
ред. 2: у `work_rung` «после» сравниваются только прежние поля `no`, `released`,
`other_class`, а `zone_scope` проверяется отдельно — у каждой строки, сравнимой «до», ровно
`"own"`; остальные поля проекции, в том числе денежные и источники цифр (`analog_id`,
`contract_id`), — без послаблений. Для несравнимых «до» — счётчики переходов состояний и
прибыль по источникам: чужая зона (`zone_scope == "other"`), строка КП без зоны (`"any"`),
строки договоров без зоны (сравнима в AFTER, но не в AFTER_NO_ZONELESS).

Запуск (из `backend/`, база — из `backend/.env`):
    PYTHONIOENCODING=utf-8 uv run python ../docs/superpowers/design/2026-11-20-zone-as-preference/snapshot_lines.py snapshot ../tasks/zone-pref-run/before.json
    PYTHONIOENCODING=utf-8 uv run python ../docs/superpowers/design/2026-11-20-zone-as-preference/snapshot_lines.py compare ../tasks/zone-pref-run/before.json ../tasks/zone-pref-run/after.json ../tasks/zone-pref-run/after_no_zoneless.json ../tasks/zone-pref-run/compare.json
Снимки содержат цены — класть в `tasks/` (не под гитом). Транзакция READ ONLY, в конце
rollback; файлов, кроме названного вывода, не пишет.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

PROJECTION = (
    "state",
    "work_rung",
    "range",
    "lowest",
    "freshest",
    "contractor",
    "analogs_count",
    "to_lowest",
    "no_history_reason",
)
RUNG_FIELDS = ("no", "released", "other_class")


def walk(v, out: dict) -> None:
    if isinstance(v, dict):
        if "state" in v and "position_item_id" in v:
            out[v["position_item_id"]] = v
        for x in v.values():
            walk(x, out)
    elif isinstance(v, list):
        for x in v:
            walk(x, out)


def snapshot(out: str, *, drop_zoneless: bool) -> None:
    sys.path.insert(0, str(Path.cwd()))
    import sqlalchemy as sa  # noqa: PLC0415

    import crud.our_price as crud_op  # noqa: PLC0415
    from database import SessionLocal  # noqa: PLC0415
    from models import InflationSeries, Offer, TenderRound  # noqa: PLC0415

    orig = crud_op.load_work_history

    def load_without_zoneless(*args, **kw):
        lines, families = orig(*args, **kw)
        return [ln for ln in lines if ln.zone is not None], families

    if drop_zoneless:
        crud_op.load_work_history = load_without_zoneless
    db = SessionLocal()
    db.execute(sa.text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
    db.commit()
    assert db.execute(sa.text("SHOW transaction_read_only")).scalar() == "on"
    offers: dict[str, dict] = {}
    errors: Counter = Counter()
    try:
        series_id = db.execute(sa.select(InflationSeries.id).order_by(InflationSeries.id)).scalars().first()
        triples = db.execute(
            sa.select(TenderRound.tender_id, TenderRound.id, Offer.id)
            .join(Offer, Offer.round_id == TenderRound.id)
            .order_by(Offer.id)
        ).all()
        for ids in triples:
            try:
                view = crud_op.build_offer_view(db, *ids, inflation_series_id=series_id)
            except Exception as e:  # noqa: BLE001
                errors[f"{type(e).__name__}:{getattr(e, 'status_code', '')}"] += 1
                db.rollback()
                continue
            found: dict = {}
            walk(view, found)
            offers[str(ids[2])] = {
                str(pid): {
                    **{k: line.get(k) for k in PROJECTION},
                    "_zone": line.get("zone"),
                    "_family_id": line.get("family_id"),
                }
                for pid, line in found.items()
                if line.get("kind", "work") == "work"
            }
    finally:
        db.rollback()
        db.close()
        crud_op.load_work_history = orig
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(
            {"drop_zoneless_history": drop_zoneless, "offers": offers, "errors": dict(errors)},
            fh,
            ensure_ascii=False,
            default=str,
        )
    print(f"offers={len(offers)} lines={sum(len(v) for v in offers.values())} errors={dict(errors)}")


def _proj(line: dict, *, normalize: bool) -> str:
    p = {k: line.get(k) for k in PROJECTION}
    if normalize and isinstance(p["work_rung"], dict):
        p["work_rung"] = {k: p["work_rung"].get(k) for k in RUNG_FIELDS}
    return json.dumps(p, sort_keys=True, default=str)


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def compare(before_path: str, after_path: str, no_zoneless_path: str | None, out: str | None) -> None:
    before, after = _load(before_path), _load(after_path)
    no_zoneless = _load(no_zoneless_path) if no_zoneless_path else None
    states = {"before": Counter(), "after": Counter()}
    transitions: Counter = Counter()
    gained: Counter = Counter()
    changed: list = []
    scope_not_own: list = []
    missing: list = []
    for offer_id, lines in before["offers"].items():
        a_lines = after["offers"].get(offer_id)
        if a_lines is None:
            missing.append({"offer": offer_id})
            continue
        for pid, b in lines.items():
            a = a_lines.get(pid)
            if a is None:
                missing.append({"offer": offer_id, "position_item_id": pid})
                continue
            states["before"][b["state"]] += 1
            states["after"][a["state"]] += 1
            if b["state"] == "comparable":
                if _proj(a, normalize=True) != _proj(b, normalize=False):
                    changed.append({"offer": offer_id, "position_item_id": pid})
                scope = (a.get("work_rung") or {}).get("zone_scope")
                if scope != "own":
                    scope_not_own.append({"offer": offer_id, "position_item_id": pid, "zone_scope": scope})
                continue
            src = b["state"] if b["state"] != "no_history" else f"no_history/{b.get('no_history_reason')}"
            transitions[f"{src} -> {a['state']}"] += 1
            if a["state"] == "comparable":
                gained[f"scope:{(a.get('work_rung') or {}).get('zone_scope')}"] += 1
                if no_zoneless is not None:
                    nz = no_zoneless["offers"].get(offer_id, {}).get(pid)
                    if nz is None or nz["state"] != "comparable":
                        gained["needs_zoneless_contract_lines"] += 1
    result = {
        "offers_before": len(before["offers"]),
        "offers_after": len(after["offers"]),
        "errors": {"before": before["errors"], "after": after["errors"]},
        "states": {k: dict(v) for k, v in states.items()},
        "comparable_before_changed": len(changed),
        "comparable_before_changed_sample": changed[:20],
        "comparable_before_scope_not_own": len(scope_not_own),
        "comparable_before_scope_not_own_sample": scope_not_own[:20],
        "missing_after": len(missing),
        "missing_after_sample": missing[:20],
        "transitions_of_not_comparable": dict(sorted(transitions.items(), key=lambda kv: -kv[1])),
        "gained_comparable": dict(gained),
    }
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if out:
        Path(out).write_text(text, encoding="utf-8")
    print(text)


def main() -> None:
    mode, *rest = sys.argv[1:]
    if mode == "snapshot":
        snapshot(rest[0], drop_zoneless="--drop-zoneless-history" in rest[1:])
    elif mode == "compare":
        compare(rest[0], rest[1], rest[2] if len(rest) > 2 else None, rest[3] if len(rest) > 3 else None)
    else:
        raise SystemExit(f"unknown mode {mode}")


if __name__ == "__main__":
    main()
