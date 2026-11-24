"""Прогон «до/после» к фиче «Не аналог» (план, решение 6). Только чтение.

Режим `snapshot OUT.json` — по всем КП базы ответ разбора участника (`build_offer_view`, ряд
индексов — первый по id), у каждой строки-работы проекция: `state`, `no_history_reason`,
`work_rung`, `range`, `lowest`, `freshest`, `contractor`, `analogs_count`, `to_lowest`, плюс
`family_id` (семья строки КП, `KpLine.key.family_id`), `lump` (итог «комплект или ставка»,
`KpLine.is_lump`) и `candidate_ids` (аналоги ступени, `ladder.analogs`; у строк без ступени —
пусто). Их берёт расчёт той же сцены (`_offer_context`), не ответ панели. Сверх строк КП —
`contract_lumps`: `{position_item_id: is_lump}` у всех строк договоров истории подбора всех КП
(`scene.work_history._lines_by_family`); расхождение значений между сценами — в
`contract_lumps_conflicts` (ожидается 0).

Режим `marked OUT.json` — `MARKED` из БД: `{"lines": [{"position_item_id", "family_id"}]}` —
строки договоров из `price_error_marks` и `not_analog_marks` с семьёй из `_lines_by_family`.
Работает после миграции 0030; `MARKED` можно и написать руками.

Режим `compare BEFORE AFTER MARKED [OUT.json]` — сравнивается проекция всех строк любого
состояния. Изменение объяснено, только если (а) `candidate_ids` «до» пересекаются с `MARKED`
(`explained_marked_candidate`) либо (б) семья строки — из `MARKED` и подтверждена смена
решения: `lump` строки «до» != «после» или у кандидата («до» либо «после») `contract_lumps`
«до» != «после» (`explained_family_rate`). Остальное — `unexplained`; пропавшая строка или
оффер — `missing`. Код выхода не 0, если `unexplained` или `missing` не пусты.

Режим `selftest` — восемь контрольных входов в памяти, без БД: печатает `selftest ok 8/8`
либо падает с кодом 1 и номером случая.

Запуск (из `backend/`, база — из `backend/.env`):
    PYTHONIOENCODING=utf-8 uv run python ../docs/superpowers/design/2026-11-23-not-analog/snapshot_lines.py snapshot ../tasks/not-analog-run/before.json
    PYTHONIOENCODING=utf-8 uv run python ../docs/superpowers/design/2026-11-23-not-analog/snapshot_lines.py marked ../tasks/not-analog-run/marked.json
    PYTHONIOENCODING=utf-8 uv run python ../docs/superpowers/design/2026-11-23-not-analog/snapshot_lines.py compare ../tasks/not-analog-run/before.json ../tasks/not-analog-run/after.json ../tasks/not-analog-run/marked.json ../tasks/not-analog-run/compare.json
    PYTHONIOENCODING=utf-8 uv run python ../docs/superpowers/design/2026-11-23-not-analog/snapshot_lines.py selftest
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
    "no_history_reason",
    "work_rung",
    "range",
    "lowest",
    "freshest",
    "contractor",
    "analogs_count",
    "to_lowest",
)
#: Поля снимка сверх ответа панели; в сравнении участвуют все.
EXTRA = ("family_id", "lump", "candidate_ids")
COMPARED = (*PROJECTION, *EXTRA)


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


def _collect_contract_lumps(scene, into: dict[int, bool], conflicts: set[int]) -> None:
    """`{position_item_id: line.lump}` по `_lines_by_family` сцены; разные значения — в `conflicts`."""
    for lines in scene.work_history._lines_by_family.values():  # noqa: SLF001
        for line in lines:
            value = bool(line.lump)
            if line.position_item_id in into and into[line.position_item_id] != value:
                conflicts.add(line.position_item_id)
            into[line.position_item_id] = value


def snapshot(out: str) -> None:
    db = _session()
    import crud.our_price as crud_op  # noqa: PLC0415

    offers: dict[str, dict] = {}
    contract_lumps: dict[int, bool] = {}
    conflicts: set[int] = set()
    errors: Counter = Counter()
    try:
        series_id, triples = _offers(db)
        for ids in triples:
            try:
                view = crud_op.build_offer_view(db, *ids, inflation_series_id=series_id)
                scene, participant, evaluation = crud_op._offer_context(  # noqa: SLF001
                    db, *ids, inflation_series_id=series_id
                )
            except Exception as e:  # noqa: BLE001
                errors[f"{type(e).__name__}:{getattr(e, 'status_code', '')}"] += 1
                db.rollback()
                continue
            _collect_contract_lumps(scene, contract_lumps, conflicts)
            rows = {r.line.position_item_id: r.line for r in scene.kp_lines[participant.estimate_id]}
            found: dict = {}
            walk(view, found)
            lines = {}
            for pid, line in found.items():
                if line.get("kind", "work") != "work":
                    continue
                entry = {k: line.get(k) for k in PROJECTION}
                kp = rows.get(pid)
                entry["family_id"] = kp.key.family_id if kp is not None and kp.key is not None else None
                entry["lump"] = bool(kp.is_lump) if kp is not None else False
                result = evaluation.lines.get(pid)
                ladder = getattr(result, "ladder", None)
                if ladder is not None and ladder.rung_no is not None:
                    entry["candidate_ids"] = sorted(a.position_item_id for a in ladder.analogs)
                else:
                    entry["candidate_ids"] = []
                lines[str(pid)] = entry
            offers[str(ids[2])] = lines
    finally:
        db.rollback()
        db.close()
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "offers": offers,
                "contract_lumps": {str(k): v for k, v in contract_lumps.items()},
                "contract_lumps_conflicts": sorted(conflicts),
                "errors": dict(errors),
            },
            fh,
            ensure_ascii=False,
            default=str,
        )
    print(
        f"offers={len(offers)} lines={sum(len(v) for v in offers.values())} contract_lumps={len(contract_lumps)} "
        f"contract_lumps_conflicts={len(conflicts)} errors={dict(errors)}"
    )


def marked(out: str) -> None:
    """`MARKED` из БД: строки договоров с отметками и их семьи (таблицы миграции 0030)."""
    db = _session()
    import sqlalchemy as sa  # noqa: PLC0415

    import crud.our_price as crud_op  # noqa: PLC0415

    errors: Counter = Counter()
    try:
        ids = {
            r[0]
            for r in db.execute(
                sa.text(
                    "SELECT position_item_id FROM price_error_marks "
                    "UNION SELECT position_item_id FROM not_analog_marks"
                )
            )
        }
        families: dict[int, int] = {}
        series_id, triples = _offers(db)
        for t in triples:
            if ids <= families.keys():
                break
            try:
                scene, _, _ = crud_op._offer_context(db, *t, inflation_series_id=series_id)  # noqa: SLF001
            except Exception as e:  # noqa: BLE001
                errors[f"{type(e).__name__}:{getattr(e, 'status_code', '')}"] += 1
                db.rollback()
                continue
            for family_id, lines in scene.work_history._lines_by_family.items():  # noqa: SLF001
                for line in lines:
                    if line.position_item_id in ids:
                        families[line.position_item_id] = family_id
    finally:
        db.rollback()
        db.close()
    result = [{"position_item_id": p, "family_id": families[p]} for p in sorted(families)]
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"lines": result}, fh)
    print(f"marked={len(ids)} with_family={len(result)} errors={dict(errors)}")


def _proj(line: dict) -> str:
    return json.dumps({k: line.get(k) for k in COMPARED}, sort_keys=True, default=str)


def _changed_fields(b: dict, a: dict) -> dict:
    return {k: [b.get(k), a.get(k)] for k in COMPARED if b.get(k) != a.get(k)}


def diff(before: dict, after: dict, marked_doc: dict) -> dict:
    """Сравнение снимков без файлов. Возвращает счётчики и списки; `unexplained`/`missing` — на выход."""
    marked_ids = {int(m["position_item_id"]) for m in marked_doc["lines"]}
    marked_families = {int(m["family_id"]) for m in marked_doc["lines"]}
    lumps_b = {int(k): v for k, v in before.get("contract_lumps", {}).items()}
    lumps_a = {int(k): v for k, v in after.get("contract_lumps", {}).items()}
    states = {"before": Counter(), "after": Counter()}
    transitions: Counter = Counter()
    explained_candidate: list = []
    explained_family: list = []
    unexplained: list = []
    missing: list = []
    unchanged = 0
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
            if _proj(a) == _proj(b):
                unchanged += 1
                continue
            item = {"offer": offer_id, "position_item_id": pid, "state_before": b["state"]}
            hit = sorted(set(b.get("candidate_ids") or []) & marked_ids)
            if hit:
                explained_candidate.append({**item, "marked_candidates": hit})
                continue
            if b.get("family_id") in marked_families:
                own = b.get("lump") != a.get("lump")
                cands = sorted(
                    c
                    for c in set(b.get("candidate_ids") or []) | set(a.get("candidate_ids") or [])
                    if lumps_b.get(c) != lumps_a.get(c)
                )
                if own or cands:
                    explained_family.append(
                        {**item, "own_lump_changed": own, "candidates_lump_changed": cands}
                    )
                    continue
            unexplained.append({**item, "changed": _changed_fields(b, a)})
    return {
        "states": {k: dict(v) for k, v in states.items()},
        "transitions": dict(sorted(transitions.items(), key=lambda kv: -kv[1])),
        "unchanged": unchanged,
        "explained_marked_candidate": explained_candidate,
        "explained_family_rate": explained_family,
        "unexplained": unexplained,
        "missing": missing,
    }


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def compare(before_path: str, after_path: str, marked_path: str, out: str | None) -> int:
    before, after = _load(before_path), _load(after_path)
    result = diff(before, after, _load(marked_path))
    result = {
        "offers_before": len(before["offers"]),
        "offers_after": len(after["offers"]),
        "errors": {"before": before.get("errors"), "after": after.get("errors")},
        "contract_lumps_conflicts": {
            "before": len(before.get("contract_lumps_conflicts", [])),
            "after": len(after.get("contract_lumps_conflicts", [])),
        },
        **result,
    }
    if out:
        Path(out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    bad = len(result["unexplained"]) + len(result["missing"])
    print(
        f"unchanged={result['unchanged']} explained_marked_candidate={len(result['explained_marked_candidate'])} "
        f"explained_family_rate={len(result['explained_family_rate'])} unexplained={len(result['unexplained'])} "
        f"missing={len(result['missing'])} "
        f"lumps_conflicts={result['contract_lumps_conflicts']['before']}/{result['contract_lumps_conflicts']['after']}"
    )
    return 1 if bad else 0


def _line(**kw) -> dict:
    base = {
        "state": "comparable",
        "no_history_reason": None,
        "work_rung": 1,
        "range": [100, 200],
        "lowest": 100,
        "freshest": 120,
        "contractor": None,
        "analogs_count": 3,
        "to_lowest": 10,
        "family_id": 3,
        "lump": False,
        "candidate_ids": [],
    }
    return {**base, **kw}


def _snap(line_before: dict | None, line_after: dict | None, lumps_b=None, lumps_a=None) -> tuple[dict, dict]:
    def one(line, lumps):
        return {
            "offers": {"1": {"5": line}} if line is not None else {"1": {}},
            "contract_lumps": lumps or {},
        }

    return one(line_before, lumps_b), one(line_after, lumps_a)


def selftest() -> int:
    marked_doc = {"lines": [{"position_item_id": 99, "family_id": 7}]}
    marked_cand = {"lines": [{"position_item_id": 10, "family_id": 7}]}
    cases = []  # (название, before, after, marked, ожидаемое unexplained+missing)

    b, a = _snap(
        _line(state="no_history", no_history_reason="no_contracts"),
        _line(state="no_history", no_history_reason="no_variant_in_history"),
    )
    cases.append(("no_history_reason", b, a, marked_doc, 1))

    b, a = _snap(_line(state="unit_mismatch", analogs_count=2), _line(state="unit_mismatch", analogs_count=1))
    cases.append(("unit_mismatch_count", b, a, marked_doc, 1))

    b, a = _snap(_line(lump=False), _line(lump=True))
    cases.append(("lump_outside_marked", b, a, marked_doc, 1))

    b, a = _snap(_line(), None)
    cases.append(("missing_line", b, a, marked_doc, 1))

    b, a = _snap(_line(family_id=7, lowest=100), _line(family_id=7, lowest=999))
    cases.append(("marked_family_lowest_grows", b, a, marked_doc, 1))

    b, a = _snap(_line(candidate_ids=[10], lowest=100), _line(candidate_ids=[11], lowest=150))
    cases.append(("marked_candidate", b, a, marked_cand, 0))

    b, a = _snap(
        _line(family_id=7, candidate_ids=[20], lowest=100),
        _line(family_id=7, candidate_ids=[20], lowest=150),
        {20: False},
        {20: True},
    )
    cases.append(("candidate_lump_changed", b, a, marked_doc, 0))

    b, a = _snap(_line(family_id=7, lump=False, lowest=100), _line(family_id=7, lump=True, lowest=None))
    cases.append(("own_lump_changed", b, a, marked_doc, 0))

    for n, (name, before, after, mk, expected) in enumerate(cases, 1):
        r = diff(before, after, mk)
        got = len(r["unexplained"]) + len(r["missing"])
        if got != expected:
            print(f"selftest FAIL case {n} ({name}): expected {expected} got {got}")
            return 1
    print(f"selftest ok {len(cases)}/{len(cases)}")
    return 0


def main() -> None:
    mode, *rest = sys.argv[1:]
    if mode == "snapshot":
        snapshot(rest[0])
    elif mode == "marked":
        marked(rest[0])
    elif mode == "compare":
        raise SystemExit(compare(rest[0], rest[1], rest[2], rest[3] if len(rest) > 3 else None))
    elif mode == "selftest":
        raise SystemExit(selftest())
    else:
        raise SystemExit(f"unknown mode {mode}")


if __name__ == "__main__":
    main()
