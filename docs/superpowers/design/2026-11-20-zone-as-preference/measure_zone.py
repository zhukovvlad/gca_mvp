"""Замер к дизайну «зона как предпочтение» (2026-11-20). Только чтение.

Часть 1 — экран «Против нашей цены» по всем КП базы, трижды: как сегодня, по варианту 1
дизайна (§4 п. 1–2) и по варианту 1 с флажком «зона обязательна» у семей `STRICT_FAMILIES`
(для них — как сегодня), подменой в памяти процесса двух функций расчёта:
  * `services.our_price.ladder_for` — если в своей зоне ни одна ступень ничего не нашла,
    та же лестница по всем зонам вместе (своя в выборке пуста на всех ступенях, поэтому
    «все зоны» здесь то же, что «все другие»);
  * `crud.our_price.evaluate_offer` — строка-работа без зоны и не «разбирает» (`in_progress`
    ложно: пауза провайдера и остановка уже дают ложь) получает зону-заглушку «все зоны».
Сравниваются ответы разбора участника построчно. Для строк, сравнимых сегодня, проекция
(состояние, ступень, вилка, три цифры, число аналогов, разница) обязана совпасть; для
остальных считается, в какое состояние строка перешла.

Часть 2 — строки последних смет договоров истории (как их грузит подбор: только с зоной).
Группа — один договор, один вариант работы, одна единица. Пара — две строки группы с ценой;
отношение пары — большая цена за единицу к меньшей (валовые, но внутри одного договора база
НДС одна, отношение то же, что у нетто). Пары делятся на «одна зона» и «разные зоны».
Распределения — по парам и по группам (у группы — медиана отношений её пар).

Часть 3 — сколько строк смет договоров истории отсекает условие «у цепочки есть зона»: те же
условия, что у `load_work_history`, кроме зоны; цепочки может не быть вовсе.

Запуск (из `backend/`, база — из `backend/.env`):
    PYTHONIOENCODING=utf-8 uv run python ../docs/superpowers/design/2026-11-20-zone-as-preference/measure_zone.py OUT.json
Скрипт импортирует приложение: он воспроизводит расчёт подбора, а не читает таблицы.
Транзакция READ ONLY, в конце rollback; файлов, кроме OUT.json, не пишет.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import statistics
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

import sqlalchemy as sa  # noqa: E402

import crud.our_price as crud_op  # noqa: E402
import services.our_price as svc_op  # noqa: E402
from crud.our_price_load import load_history, load_work_history  # noqa: E402
from database import SessionLocal  # noqa: E402
from models import InflationSeries, Offer, TenderRound, WorkFamily  # noqa: E402
from services.analog_ladder import WorkHistory  # noqa: E402

ANY = "any"  # зона-заглушка «все зоны»
#: Стартовый список флажка «зона обязательна» (дизайн §4 п. 3): семьи, где по части 2 у
#: межзонных групп медиана отношения > 1,10 при не меньше 4 группах.
STRICT_FAMILIES = (
    "Двери",
    "Облицовка керамогранитом",
    "Покраска стен",
    "Вывоз и утилизация грунта",
    "Светильники",
    "Монтаж воздуховодов вентиляции",
    "Изоляция воздуховодов",
)
strict_ids: set[int] = set()
use_strict = False
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

ORIG_LADDER_FOR = svc_op.ladder_for
ORIG_EVALUATE_OFFER = crud_op.evaluate_offer
_merged: dict[int, WorkHistory] = {}
fallback_used: Counter = Counter()
gained_families: Counter = Counter()


def merged_of(history: WorkHistory) -> WorkHistory:
    """Та же история, у всех строк зона — заглушка: лестница по всем зонам вместе."""
    found = _merged.get(id(history))
    if found is None:
        lines = [
            dataclasses.replace(ln, zone=ANY)
            for family in list(history._lines_by_family)
            for ln in history.lines_of(family)
        ]
        found = _merged[id(history)] = WorkHistory(lines, history.families, history.contracts, factors=history.factors)
    return found


def ladder_for_v1(kp, history, **kw):
    if use_strict and kp.family_id in strict_ids:
        return ORIG_LADDER_FOR(kp, history, **kw)
    if kp.zone != ANY:
        own = ORIG_LADDER_FOR(kp, history, **kw)
        if own.rung_no is not None:
            return own
    any_zone = ORIG_LADDER_FOR(dataclasses.replace(kp, zone=ANY), merged_of(history), **kw)
    if any_zone.rung_no is None:
        return ORIG_LADDER_FOR(kp, history, **kw) if kp.zone != ANY else any_zone
    fallback_used[("strict:" if use_strict else "") + ("no_zone" if kp.zone == ANY else "other_zone")] += 1
    gained_families[(use_strict, kp.family_id)] += 1
    return any_zone


def evaluate_offer_v1(lines, *args, **kw):
    patched = [
        dataclasses.replace(ln, zone=ANY)
        if ln.zone is None
        and not ln.in_progress
        and not ln.is_system
        and ln.key is not None
        and not (use_strict and ln.key.family_id in strict_ids)
        else ln
        for ln in lines
    ]
    return ORIG_EVALUATE_OFFER(patched, *args, **kw)


def walk(v, out):
    if isinstance(v, dict):
        if "state" in v and "position_item_id" in v:
            out[v["position_item_id"]] = v
        for x in v.values():
            walk(x, out)
    elif isinstance(v, list):
        for x in v:
            walk(x, out)


def work_lines(view) -> dict:
    found: dict = {}
    walk(view, found)
    return {k: v for k, v in found.items() if v.get("kind", "work") == "work"}


def project(line: dict) -> str:
    return json.dumps({k: line.get(k) for k in PROJECTION}, sort_keys=True, default=str)


def run_view(db, ids, series_id, *, patched: bool, strict: bool) -> dict:
    global use_strict
    use_strict = strict
    _merged.clear()
    if patched:
        svc_op.ladder_for, crud_op.evaluate_offer = ladder_for_v1, evaluate_offer_v1
    try:
        return work_lines(crud_op.build_offer_view(db, *ids, inflation_series_id=series_id))
    finally:
        svc_op.ladder_for, crud_op.evaluate_offer = ORIG_LADDER_FOR, ORIG_EVALUATE_OFFER


def part1(db) -> dict:
    series_id = db.execute(sa.select(InflationSeries.id).order_by(InflationSeries.id)).scalars().first()
    strict_ids.update(
        db.execute(sa.select(WorkFamily.id).where(WorkFamily.title.in_(STRICT_FAMILIES))).scalars()
    )
    pairs = db.execute(
        sa.select(TenderRound.tender_id, TenderRound.id, Offer.id).join(Offer, Offer.round_id == TenderRound.id)
    ).all()
    errors: Counter = Counter()
    states: dict[str, Counter] = {m: Counter() for m in ("before", "v1", "v1_strict")}
    transitions: dict[str, Counter] = {m: Counter() for m in ("v1", "v1_strict")}
    changed: dict[str, list] = {m: [] for m in ("v1", "v1_strict")}
    offers_ok = 0
    for ids in pairs:
        try:
            views = {
                "before": run_view(db, ids, series_id, patched=False, strict=False),
                "v1": run_view(db, ids, series_id, patched=True, strict=False),
                "v1_strict": run_view(db, ids, series_id, patched=True, strict=True),
            }
        except Exception as e:  # noqa: BLE001
            errors[f"{type(e).__name__}:{getattr(e, 'status_code', '')}"] += 1
            db.rollback()
            continue
        offers_ok += 1
        before = views["before"]
        for pid, b in before.items():
            states["before"][b["state"]] += 1
            for mode in ("v1", "v1_strict"):
                a = views[mode][pid]
                states[mode][a["state"]] += 1
                if b["state"] == "comparable":
                    if project(a) != project(b):
                        changed[mode].append({"offer": ids[2], "position_item_id": pid})
                    continue
                src = b["state"] if b["state"] != "no_history" else f"no_history/{b.get('no_history_reason')}"
                transitions[mode][f"{src} -> {a['state']}"] += 1
    titles = dict(db.execute(sa.select(WorkFamily.id, WorkFamily.title)).all())
    gained = Counter()
    for (strict, family_id), n in gained_families.items():
        if not strict:
            gained[titles.get(family_id, str(family_id))] += n
    return {
        "offers": len(pairs),
        "offers_ok": offers_ok,
        "offers_err": dict(errors),
        "strict_families_found": sorted(titles[i] for i in strict_ids),
        "states": {m: dict(c) for m, c in states.items()},
        "transitions_of_not_comparable": {
            m: dict(sorted(c.items(), key=lambda kv: -kv[1])) for m, c in transitions.items()
        },
        "comparable_before_changed": {m: len(v) for m, v in changed.items()},
        "comparable_before_changed_sample": {m: v[:20] for m, v in changed.items()},
        "fallback_rungs_found": dict(fallback_used),
        "fallback_by_family_v1_top": gained.most_common(25),
    }


def part3(db) -> dict:
    """Строки смет договоров истории, которые подбор отсекает только из-за зоны цепочки."""
    from crud.our_price_load import _WEIGHT, _price_ok, _weight_ok  # noqa: PLC0415
    from models import (  # noqa: PLC0415
        CatalogContext,
        ContextMember,
        Estimate,
        Lot,
        PositionItem,
        Proposal,
        SectionChain,
    )

    contracts, _ = load_history(db, exclude_object_id=-1)
    estimate_ids = [c.estimate_id for c in contracts]
    q = (
        sa.select(
            sa.func.count(),
            sa.func.count().filter(PositionItem.section_chain_id.is_(None)),
            sa.func.count().filter(SectionChain.id.is_not(None) & SectionChain.zone.is_(None)),
        )
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .join(Estimate, Estimate.id == Lot.estimate_id)
        .outerjoin(SectionChain, SectionChain.id == PositionItem.section_chain_id)
        .join(ContextMember, ContextMember.position_item_id == PositionItem.id)
        .join(CatalogContext, CatalogContext.id == ContextMember.context_id)
        .where(
            Estimate.id.in_(estimate_ids),
            PositionItem.is_chapter.is_(False),
            CatalogContext.work_variant_id.is_not(None),
            CatalogContext.archived_at.is_(None),
            sa.not_(CatalogContext.semantic_kind == "SYSTEM"),
            _price_ok(PositionItem.unit_cost_total),
            _weight_ok(_WEIGHT),
        )
    )
    total, no_chain, no_zone = db.execute(q).one()
    return {"history_work_lines_any_zone": total, "without_chain": no_chain, "chain_without_zone": no_zone}


def dist(xs: list[float]) -> dict:
    xs = sorted(xs)
    if not xs:
        return {"n": 0}

    def q(p: float) -> float:
        return round(xs[min(len(xs) - 1, int(p * len(xs)))], 3)

    n = len(xs)
    return {
        "n": n,
        "share_equal": round(sum(1 for x in xs if x <= 1.0001) / n, 3),
        "share_le_1.05": round(sum(1 for x in xs if x <= 1.05) / n, 3),
        "share_le_1.10": round(sum(1 for x in xs if x <= 1.10) / n, 3),
        "share_gt_1.25": round(sum(1 for x in xs if x > 1.25) / n, 3),
        "p50": q(0.5),
        "p75": q(0.75),
        "p90": q(0.9),
        "max": round(xs[-1], 3),
    }


def part2(db) -> dict:
    contracts, _ = load_history(db, exclude_object_id=-1)
    estimate_of = {c.contract_id: c.estimate_id for c in contracts}
    lines, _ = load_work_history(db, estimate_of=estimate_of)
    titles = dict(db.execute(sa.select(WorkFamily.id, WorkFamily.title)).all())
    groups: dict = defaultdict(list)
    for ln in lines:
        if ln.unit_price is not None and Decimal(ln.unit_price) > 0:
            groups[(ln.contract_id, ln.variant_id, ln.unit_id)].append(ln)
    pair_same: list[float] = []
    pair_cross: list[float] = []
    group_same: list[float] = []
    group_cross: list[float] = []
    fam_cross: dict = defaultdict(list)
    for members in groups.values():
        same, cross = [], []
        for x, y in itertools.combinations(members, 2):
            px, py = Decimal(x.unit_price), Decimal(y.unit_price)
            r = float(max(px, py) / min(px, py))
            (same if x.zone == y.zone else cross).append(r)
        pair_same += same
        pair_cross += cross
        if same:
            group_same.append(statistics.median(same))
        if cross:
            group_cross.append(statistics.median(cross))
            fam_cross[members[0].family_id].append(statistics.median(cross))
    by_family = sorted(fam_cross.items(), key=lambda kv: -len(kv[1]))
    return {
        "contracts": len(contracts),
        "history_lines_with_zone": len(lines),
        "zone_counts": dict(Counter(ln.zone for ln in lines)),
        "groups_with_pairs": sum(1 for m in groups.values() if len(m) >= 2),
        "pairs_same_zone": dist(pair_same),
        "pairs_cross_zone": dist(pair_cross),
        "groups_same_zone_median": dist(group_same),
        "groups_cross_zone_median": dist(group_cross),
        "cross_zone_by_family": [
            {
                "family": titles.get(f, str(f)),
                "groups": len(rs),
                "median": round(statistics.median(rs), 3),
                "max": round(max(rs), 3),
                "groups_gt_1.10": sum(1 for r in rs if r > 1.10),
            }
            for f, rs in by_family
        ],
    }


def main() -> None:
    out = sys.argv[1]
    db = SessionLocal()
    db.execute(sa.text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
    db.commit()
    assert db.execute(sa.text("SHOW transaction_read_only")).scalar() == "on"
    try:
        result = {"part1": part1(db), "part2": part2(db), "part3": part3(db)}
    finally:
        db.rollback()
        db.close()
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1, default=str)
    print("done")


if __name__ == "__main__":
    main()
