"""Замер к дизайну «Строки-суммы»: сколько строк-работ с объёмом 1 держат сумму, а не ставку.

Только чтение. Запуск из backend/:
    PYTHONIOENCODING=utf-8 uv run python ../docs/superpowers/design/2026-11-22-lump-sum-lines/measure_sums.py OUT.json [SNAPSHOT.json]

SNAPSHOT — снимок ответов экрана «Против нашей цены» по всем КП стенда (формат
tasks/zone-pref-run/after.json: {"offers": {offer_id: {position_item_id: {"state": ...}}}}).
Без него считаются только численности, без «что строка показывает сегодня».

Определения:
- цены — нетто, по базе НДС сметы (перекрытие базы у сметы, иначе ставка НДС из файла,
  как у сравнения): и у строки, и у обычной ставки; строки с неизвестной базой в
  обычную ставку не входят и не распознаются (их и сегодня нет в сравнении);
- вес строки — предложенный объём, при его отсутствии объём заказчика (как у подбора);
- обычная ставка семьи в единице — медиана цены за единицу строк-работ семьи с этой
  каталожной единицей и весом не 1, по всем сметам (договоры и КП), не меньше 3 строк;
  цены меньше 1 ₽ нетто (заглушки) в неё не входят;
- строка-сумма при пороге M/P — вес ровно 1 и цена >= M обычных ставок в мерной единице
  (м, м2, м3, т, кг) или >= P в штучной (шт, компл, мес); обычной ставки нет — сумма,
  если единица мерная, иначе ставка.
"""

import json
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, ".")
import sqlalchemy as sa  # noqa: E402

from database import SessionLocal  # noqa: E402

MEASURE_UNITS = {"M", "M2", "M3", "TON", "KG"}
#: (порог мерных единиц, порог штучных: шт, компл, мес)
THRESHOLDS = ((5, 50), (10, 50), (3, 50), (5, 20), (5, 100))
BUCKETS = (2, 5, 10, 20, 50, 200)

db = SessionLocal()
db.execute(sa.text("SET TRANSACTION READ ONLY"))
rows = db.execute(
    sa.text(
        """
select pi.id, cc.work_family_id fam, cc.work_variant_id var, u.code unit,
       coalesce(pi.suggested_quantity, pi.quantity)::float w, pi.unit_cost_total::float g,
       coalesce(e.vat_rate_base_override, pr.vat_rate)::float base,
       e.contract_id, sc.zone::text zone
from position_items pi
join context_members cm on cm.position_item_id = pi.id
join catalog_contexts cc on cc.id = cm.context_id
left join catalog_positions cp on cp.id = pi.catalog_position_id
left join units_of_measure u on u.id = cp.unit_id
join proposals pr on pr.id = pi.proposal_id
join lots l on l.id = pr.lot_id
join estimates e on e.id = l.estimate_id
left join section_chains sc on sc.id = pi.section_chain_id
where pi.is_chapter = false and cc.archived_at is null and cc.semantic_kind <> 'SYSTEM'
  and cc.work_family_id is not null and pi.unit_cost_total > 0
  and coalesce(pi.suggested_quantity, pi.quantity) > 0
"""
    )
).mappings().all()
rows = [dict(r) for r in rows]
for r in rows:
    r["p"] = None if r["base"] is None else r["g"] * 100 / (100 + r["base"])
known = [r for r in rows if r["p"] is not None]


def one(r) -> bool:
    return abs(r["w"] - 1) <= 1e-9


pool: dict = defaultdict(list)
for r in known:
    # цены-заглушки (0,01 ₽, 1 ₽) в обычную ставку не входят
    if not one(r) and r["p"] >= 1:
        pool[(r["fam"], r["unit"])].append(r["p"])
typical = {k: statistics.median(v) for k, v in pool.items() if len(v) >= 3}
# для сравнения с ред. 3: всё по ценам, как записаны в сметах
pool_g: dict = defaultdict(list)
for r in known:
    if not one(r) and r["g"] >= 1:
        pool_g[(r["fam"], r["unit"])].append(r["g"])
typical_g = {k: statistics.median(v) for k, v in pool_g.items() if len(v) >= 3}


def ratio(r, price="p"):
    t = (typical if price == "p" else typical_g).get((r["fam"], r["unit"]))
    return None if t is None else r[price] / t


def is_sum(r, threshold, price="p") -> bool:
    if not one(r) or r["p"] is None:
        return False
    q = ratio(r, price)
    measure = r["unit"] in MEASURE_UNITS
    if q is None:
        return measure
    return q >= (threshold[0] if measure else threshold[1])


def bucket(q) -> str:
    if q is None:
        return "нет обычной ставки"
    for b in BUCKETS:
        if q < b:
            return f"<{b}"
    return f">={BUCKETS[-1]}"


out: dict = {"lines_total": {"history": 0, "kp": 0}, "unknown_base": {}, "volume_one": {}}
for side, hist in (("history", True), ("kp", False)):
    mine = [r for r in rows if (r["contract_id"] is not None) == hist]
    out["lines_total"][side] = len(mine)
    out["unknown_base"][side] = {
        "lines": sum(1 for r in mine if r["p"] is None),
        "volume_one": sum(1 for r in mine if r["p"] is None and one(r)),
    }
    by_unit: dict = defaultdict(Counter)
    for r in mine:
        if one(r) and r["p"] is not None:
            by_unit[bucket(ratio(r))][r["unit"]] += 1
    out["volume_one"][side] = {b: dict(c) | {"_all": sum(c.values())} for b, c in by_unit.items()}

state = {}
if len(sys.argv) > 2:
    snap = json.load(open(sys.argv[2], encoding="utf-8"))["offers"]
    state = {int(pid): v["state"] for offer in snap.values() for pid, v in offer.items()}

out["thresholds"] = {}
for threshold in THRESHOLDS:
    hs = [r for r in rows if r["contract_id"] is not None and is_sum(r, threshold)]
    ks = [r for r in rows if r["contract_id"] is None and is_sum(r, threshold)]
    entry = {
        "history_sums": len(hs),
        "history_families": len({r["fam"] for r in hs}),
        # кандидаты аналогов — только строки с вариантом и каталожной единицей (спека §1 п. 4)
        "history_applicable": sum(1 for r in hs if r["var"] is not None and r["unit"] is not None),
        "history_zones": dict(Counter(r["zone"] for r in hs)),
        "kp_sums_all_estimates": len(ks),
        # строки, у которых решение «по записанным ценам» (ред. 3) и «нетто» расходится
        "differs_from_recorded_prices": sum(
            1 for r in known if is_sum(r, threshold) != is_sum(r, threshold, price="g")
        ),
    }
    if state:
        shown = [r for r in ks if r["id"] in state]
        entry["kp_sums_in_snapshot"] = len(shown)
        entry["kp_zones_in_snapshot"] = dict(Counter(r["zone"] for r in shown))
        entry["kp_states_today"] = dict(Counter(state[r["id"]] for r in shown).most_common())
        # применимость правила по спеке §1 п. 4: вариант и каталожная единица есть,
        # и строка не получает состояние раньше lump_sum (in_progress, unknown_vat_base,
        # no_price, no_variant); это и есть ожидаемое число lump_sum после фичи
        early = {"in_progress", "unknown_vat_base", "no_price", "no_variant"}
        applicable = [
            r for r in shown if r["var"] is not None and r["unit"] is not None and state[r["id"]] not in early
        ]
        entry["kp_lump_sum_expected"] = len(applicable)
        entry["kp_lump_sum_expected_states_today"] = dict(
            Counter(state[r["id"]] for r in applicable).most_common()
        )
        entry["kp_not_applicable"] = dict(
            Counter(
                "no_variant_today" if state[r["id"]] in early else ("no_variant" if r["var"] is None else "no_unit")
                for r in shown
                if r not in applicable
            ).most_common()
        )
        # сравнение суммы с суммой: часть здания есть у всех, кроме территории;
        # зона не определена — на общую площадь объекта
        fam_contracts: dict = defaultdict(set)
        var_contracts: dict = defaultdict(set)
        for r in hs:
            if r["zone"] != "territory":
                fam_contracts[r["fam"]].add(r["contract_id"])
                var_contracts[(r["fam"], r["var"])].add(r["contract_id"])
        with_part = [r for r in shown if r["zone"] != "territory"]
        found = [r for r in with_part if fam_contracts.get(r["fam"])]
        entry["kp_not_territory"] = len(with_part)
        entry["kp_find_family_sum"] = len(found)
        entry["kp_find_variant_sum"] = sum(1 for r in with_part if var_contracts.get((r["fam"], r["var"])))
        entry["kp_find_family_sum_states_today"] = dict(Counter(state[r["id"]] for r in found).most_common())
    out["thresholds"][f"{threshold[0]}/{threshold[1]}"] = entry

json.dump(out, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("ok")
