"""Прогон «до/после» к фиче «строки, похожие на комплект» (спека §9 п. 15). Только чтение.

Режим `snapshot OUT.json` — по всем КП базы ответ разбора участника (`build_offer_view`, ряд
индексов — первый по id, как в замере), строки-работы, на строку проекция спеки §9 п. 15:
состояние, ступень, вилка, три цифры, число аналогов, разница, причина без ступени. Сверх
проекции — `candidate_ids`: строки договоров выбранной ступени (аналоги и выбросы — те же, что
панель покажет в `analogs[].analog_id`), только у строк «Сравнимо» и «Разные единицы»; их
берёт расчёт той же сцены (`_offer_context`), не ответ панели, — чтобы не строить панель на
каждую строку. Снимок «до» снимается кодом ветки до задачи 3 (сравнение прежнее).

Режим `lumps OUT.json` — выгрузка кодом ветки (после задачи 3) идентификаторов строк договоров,
которые сегодня комплекты (`WorkHistoryLine.lump`), по истории подбора всех КП базы.

Режим `compare BEFORE AFTER LUMPS [OUT.json]`:
- счётчик переходов состояний и число строк, ставших `lump_sum`, с разбивкой по состоянию «до»
  (ожидание спеки: 1 135 = 524 `unit_mismatch` + 389 `no_history` + 179 `comparable` + 43
  `no_zone`, допуск — импорт после снимка);
- строки `no_variant` «до» — «после» те же `no_variant` с той же проекцией (68 совпавших с
  правилом среди них);
- у прочих строк, сравнимых «до» и не ставших `lump_sum», проекция совпадает полностью, кроме
  строк, чьи `candidate_ids` «до» пересекаются с LUMPS, — они печатаются отдельно, с тем, какие
  комплекты были среди кандидатов.

Запуск (из `backend/`, база — из `backend/.env`):
    PYTHONIOENCODING=utf-8 uv run python ../docs/superpowers/design/2026-11-22-lump-sum-lines/snapshot_lines.py snapshot ../tasks/lump-sum-run/before.json
    PYTHONIOENCODING=utf-8 uv run python ../docs/superpowers/design/2026-11-22-lump-sum-lines/snapshot_lines.py lumps ../tasks/lump-sum-run/lumps.json
    PYTHONIOENCODING=utf-8 uv run python ../docs/superpowers/design/2026-11-22-lump-sum-lines/snapshot_lines.py compare ../tasks/lump-sum-run/before.json ../tasks/lump-sum-run/after.json ../tasks/lump-sum-run/lumps.json ../tasks/lump-sum-run/compare.json
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
CANDIDATE_STATES = ("comparable", "unit_mismatch")


def walk(v, out: dict) -> None:
    if isinstance(v, dict):
        if "state" in v and "position_item_id" in v:
            out[v["position_item_id"]] = v
        for x in v.values():
            walk(x, out)
    elif isinstance(v, list):
        for x in v:
            walk(x, out)


def _session():
    sys.path.insert(0, str(Path.cwd()))
    import sqlalchemy as sa  # noqa: PLC0415

    from database import SessionLocal  # noqa: PLC0415

    db = SessionLocal()
    db.execute(sa.text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
    db.commit()
    assert db.execute(sa.text("SHOW transaction_read_only")).scalar() == "on"
    return db


def _offers(db) -> tuple[int | None, list]:
    import sqlalchemy as sa  # noqa: PLC0415

    from models import InflationSeries, Offer, TenderRound  # noqa: PLC0415

    series_id = db.execute(sa.select(InflationSeries.id).order_by(InflationSeries.id)).scalars().first()
    triples = db.execute(
        sa.select(TenderRound.tender_id, TenderRound.id, Offer.id)
        .join(Offer, Offer.round_id == TenderRound.id)
        .order_by(Offer.id)
    ).all()
    return series_id, triples


def snapshot(out: str) -> None:
    db = _session()
    import crud.our_price as crud_op  # noqa: PLC0415

    offers: dict[str, dict] = {}
    errors: Counter = Counter()
    try:
        series_id, triples = _offers(db)
        for ids in triples:
            try:
                view = crud_op.build_offer_view(db, *ids, inflation_series_id=series_id)
                _, _, evaluation = crud_op._offer_context(db, *ids, inflation_series_id=series_id)  # noqa: SLF001
            except Exception as e:  # noqa: BLE001
                errors[f"{type(e).__name__}:{getattr(e, 'status_code', '')}"] += 1
                db.rollback()
                continue
            found: dict = {}
            walk(view, found)
            lines = {}
            for pid, line in found.items():
                if line.get("kind", "work") != "work":
                    continue
                entry = {k: line.get(k) for k in PROJECTION}
                result = evaluation.lines.get(pid)
                ladder = getattr(result, "ladder", None)
                if line.get("state") in CANDIDATE_STATES and ladder is not None and ladder.rung_no is not None:
                    entry["candidate_ids"] = sorted(a.position_item_id for a in ladder.analogs)
                else:
                    entry["candidate_ids"] = []
                lines[str(pid)] = entry
            offers[str(ids[2])] = lines
    finally:
        db.rollback()
        db.close()
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"offers": offers, "errors": dict(errors)}, fh, ensure_ascii=False, default=str)
    print(f"offers={len(offers)} lines={sum(len(v) for v in offers.values())} errors={dict(errors)}")


def lumps(out: str) -> None:
    db = _session()
    import crud.our_price as crud_op  # noqa: PLC0415

    found: set[int] = set()
    errors: Counter = Counter()
    try:
        series_id, triples = _offers(db)
        for ids in triples:
            try:
                scene, _, _ = crud_op._offer_context(db, *ids, inflation_series_id=series_id)  # noqa: SLF001
            except Exception as e:  # noqa: BLE001
                errors[f"{type(e).__name__}:{getattr(e, 'status_code', '')}"] += 1
                db.rollback()
                continue
            by_family = scene.work_history._lines_by_family  # noqa: SLF001
            found.update(line.position_item_id for lines in by_family.values() for line in lines if line.lump)
    finally:
        db.rollback()
        db.close()
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"lumps": sorted(found), "errors": dict(errors)}, fh)
    print(f"lumps={len(found)} errors={dict(errors)}")


def _proj(line: dict) -> str:
    return json.dumps({k: line.get(k) for k in PROJECTION}, sort_keys=True, default=str)


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def compare(before_path: str, after_path: str, lumps_path: str, out: str | None) -> None:
    before, after = _load(before_path), _load(after_path)
    lump_ids = set(_load(lumps_path)["lumps"])
    states = {"before": Counter(), "after": Counter()}
    transitions: Counter = Counter()
    became_lump: Counter = Counter()
    no_variant_changed: list = []
    changed_unexplained: list = []
    changed_lump_candidates: list = []
    unchanged_with_lump_candidates = 0
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
            if a["state"] != b["state"]:
                transitions[f"{b['state']} -> {a['state']}"] += 1
            if a["state"] == "lump_sum":
                became_lump[b["state"]] += 1
                continue
            if b["state"] == "no_variant" and _proj(a) != _proj(b):
                no_variant_changed.append({"offer": offer_id, "position_item_id": pid, "after": a["state"]})
            if b["state"] != "comparable":
                continue
            hit = sorted(set(b.get("candidate_ids") or []) & lump_ids)
            if _proj(a) == _proj(b):
                unchanged_with_lump_candidates += bool(hit)
                continue
            item = {"offer": offer_id, "position_item_id": pid, "after_state": a["state"]}
            if hit:
                changed_lump_candidates.append({**item, "lump_candidates": hit})
            else:
                changed_unexplained.append(item)
    result = {
        "offers_before": len(before["offers"]),
        "offers_after": len(after["offers"]),
        "errors": {"before": before["errors"], "after": after["errors"]},
        "lump_ids_total": len(lump_ids),
        "states": {k: dict(v) for k, v in states.items()},
        "became_lump_sum": sum(became_lump.values()),
        "became_lump_sum_by_before_state": dict(became_lump),
        "no_variant_before": states["before"].get("no_variant", 0),
        "no_variant_changed": len(no_variant_changed),
        "no_variant_changed_sample": no_variant_changed[:20],
        "comparable_changed_unexplained": len(changed_unexplained),
        "comparable_changed_unexplained_sample": changed_unexplained[:50],
        "comparable_changed_lump_among_candidates": len(changed_lump_candidates),
        "comparable_changed_lump_among_candidates_list": changed_lump_candidates,
        "comparable_unchanged_with_lump_candidates": unchanged_with_lump_candidates,
        "missing_after": len(missing),
        "missing_after_sample": missing[:20],
        "transitions": dict(sorted(transitions.items(), key=lambda kv: -kv[1])),
    }
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if out:
        Path(out).write_text(text, encoding="utf-8")
    print(
        f"became_lump_sum={result['became_lump_sum']} no_variant_changed={result['no_variant_changed']} "
        f"unexplained={result['comparable_changed_unexplained']} "
        f"lump_candidates={result['comparable_changed_lump_among_candidates']} missing={result['missing_after']}"
    )


def main() -> None:
    mode, *rest = sys.argv[1:]
    if mode == "snapshot":
        snapshot(rest[0])
    elif mode == "lumps":
        lumps(rest[0])
    elif mode == "compare":
        compare(rest[0], rest[1], rest[2], rest[3] if len(rest) > 3 else None)
    else:
        raise SystemExit(f"unknown mode {mode}")


if __name__ == "__main__":
    main()
