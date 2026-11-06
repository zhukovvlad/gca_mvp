"""Замер новой лестницы на стенде (дизайн 2026-11-01-analog-ladder, раздел 4).

Только чтение gca_dev на 127.0.0.1:5459. Вход — lines.jsonl в текущей папке:
строки КП всех офферов, собранные collect.py (запускать из backend/:
`python ../docs/superpowers/design/2026-11-01-analog-ladder/collect.py lines.jsonl`).
Файл с ценами в гит не кладётся. Приближения: зона — по словам в цепочке
разделов, отпускаемый параметр — только «марка по водонепроницаемости», без ИПЦ.
NOZONE=1 — тот же расчёт без требования зоны.

Порядок как в §3.5–3.6 дизайна: строки договоров дешевле 1 ₽ аналогами не
считаются и отсеиваются ДО выбора первой непустой ступени; выбросы ищутся
одним проходом по исходному набору ступени (каждый аналог против медианы
остальных, от двух аналогов); пустой остаток — строка в «разных единицах».
"""
import os
NOZONE = os.environ.get("NOZONE") == "1"
import psycopg, json, re, collections, statistics as S
from decimal import Decimal as D
c = psycopg.connect("postgresql://postgres@127.0.0.1:5459/gca_dev")
# --- дерево разделов
items = c.execute("select id, chapter_item_id, job_title_in_proposal from position_items").fetchall()
by = {r[0]: r for r in items}
def chain(i):
    out=[by[i][2] or ""]; ch=by[i][1]; n=0
    while ch and ch in by and n<10: out.append(by[ch][2] or ""); ch=by[ch][1]; n+=1
    return out  # от строки вверх
U = re.compile(r"подзем|подвал|паркинг|автостоян|ниже\s*отм|минус\s*\d|-\s*\d\s*эт", re.I)
N = re.compile(r"надзем|выше\s*отм|этаж|кровл|чердак|фасад|мансард", re.I)
T = re.compile(r"благоустр|территор|двор|площадк|озелен|дорог|проезд|тротуар|\bмаф\b|газон|наружн\w* освещ|водоотвод|покрыти\w* по грунту|парк", re.I)
def zone(i):
    if NOZONE: return "*"
    for t in chain(i):
        u, n, tt = bool(U.search(t)), bool(N.search(t)), bool(T.search(t))
        k = u + n + tt
        if k == 1: return "подземная" if u else "надземная" if n else "территория"
        if k > 1: return "?"
    return "не видно"
# --- варианты: значения главных параметров
REL = {p for (p,) in c.execute("select p.id from family_parameters p where p.name ilike '%водонепроница%'").fetchall()}
vals = collections.defaultdict(dict)
for v, p, pname, role, val in c.execute("""select wv.variant_id, wv.parameter_id, p.name_norm, p.role, coalesce(fv.value_norm, 'не уточнено')
   from work_variant_values wv join family_parameters p on p.id=wv.parameter_id left join family_parameter_values fv on fv.id=wv.value_id""").fetchall():
    if role == 'main': vals[v][p] = (pname, val)
fam_of = dict(c.execute("select id, family_id from work_variants").fetchall())
def key_full(v): return (fam_of[v], tuple(sorted(vals[v].values())))
def key_wo_rel(v): return (fam_of[v], tuple(sorted(x for p, x in vals[v].items() if p not in REL)))
# --- история договоров
hist = c.execute("""
with le as (select distinct on (contract_id) id, contract_id from estimates where contract_id is not null order by contract_id, amendment_no desc nulls last)
select pi.id, ct.id, ct.object_id, ct.rate_class_id, ct.signed_date, cc.work_variant_id, pi.unit_cost_total, coalesce(e.vat_rate_base_override, pr.vat_rate)
from le join estimates e on e.id=le.id join contracts ct on ct.id=le.contract_id join lots l on l.estimate_id=e.id join proposals pr on pr.lot_id=l.id
join position_items pi on pi.proposal_id=pr.id join context_members m on m.position_item_id=pi.id join catalog_contexts cc on cc.id=m.context_id
where not pi.is_chapter and cc.work_variant_id is not null and cc.archived_at is null and cc.semantic_kind <> 'SYSTEM'
  and pi.unit_cost_total > 0 and pi.unit_cost_total < 'Infinity' and coalesce(pi.suggested_quantity, pi.quantity) > 0""").fetchall()
H_all = []
for pid, ct, obj, cls, dt, v, price, vat in hist:
    net = float(price) / (1 + float(vat or 0) / 100)
    H_all.append(dict(pid=pid, ct=ct, obj=obj, cls=cls, dt=dt, v=v, k=key_wo_rel(v), z=zone(pid), p=net))
H = [h for h in H_all if h["p"] >= 1]  # символические цены — не аналоги (§3.6)
def pools(rows):
    bv = collections.defaultdict(list); bk = collections.defaultdict(list)
    for h in rows: bv[(key_full(h["v"]), h["z"])].append(h); bk[(h["k"], h["z"])].append(h)
    return bv, bk
byv, byk = pools(H)
byv_all, byk_all = pools(H_all)  # только чтобы сосчитать, у скольких строк фильтр 1 ₽ меняет ступень
def outliers(ps):
    """Один проход по исходному набору: индексы аналогов вне [0,2; 5] к медиане остальных."""
    if len(ps) < 2: return set()
    return {i for i, p in enumerate(ps) if not (0.2 <= p / S.median(ps[:i] + ps[i+1:]) <= 5)}
# --- строки КП последних этапов (из прежнего сбора 4а)
L = [json.loads(x) for x in open("lines.jsonl", encoding="utf-8")]
last = {}
for l in L: last[l["tender"]] = max(last.get(l["tender"], 0), l["round_no"])
L = [l for l in L if l["round_no"] == last[l["tender"]] and l["kind"] == "work"]
tinfo = {}
for off, obj, cls in c.execute("select o.id, t.object_id, t.rate_class_id from offers o join tenders t on t.id=o.tender_id").fetchall(): tinfo[off] = (obj, cls)
ctxv = dict(c.execute("select id, work_variant_id from catalog_contexts").fetchall())
stat = collections.Counter(); money = collections.Counter(); old = collections.Counter(); zst = collections.Counter()
o4 = collections.Counter(); jb = collections.Counter(); ratios = []; low_rows = []; sub1 = 0; examples = []
for l in L:
    v = ctxv.get(l["context_id"])
    if not v or not l.get("kp") or l["state"] not in ("comparable", "unit_mismatch", "no_history"): continue
    kpnet = float(D(l["kp"]["total"]["net"]))
    obj, cls = tinfo[l["offer"]]; z = zone(l["position_item_id"]); zst[z] += 1
    def pick(pool, same_cls):
        return [h for h in pool if h["obj"] != obj and (h["cls"] == cls) == same_cls] if same_cls else [h for h in pool if h["obj"] != obj]
    def first_rung(bv, bk):
        for name, pool, sc in (("1 вариант, своя зона", bv[(key_full(v), z)], True), ("2 без W, своя зона", bk[(key_wo_rel(v), z)], True),
                               ("3 вариант, другой класс", bv[(key_full(v), z)], False), ("4 без W, другой класс", bk[(key_wo_rel(v), z)], False)):
            got = pick(pool, sc)
            if got: return (name, got)
        return None
    r = first_rung(byv, byk)
    r_all = first_rung(byv_all, byk_all)
    name = r[0] if r else "нет аналога"
    if (r_all[0] if r_all else "нет аналога") != name: sub1 += 1
    stat[name] += 1; money[name] += kpnet
    if l["state"] == "unit_mismatch": o4["(для сравнения) в разных единицах по 4а"] += 1
    old[(l["rung"]["level"] if l.get("rung") else "-", name)] += 1
    if "жб" in (l["family"] or "") and "Устройство жб конструкций" == l["family"]: jb[name] += 1
    if r:
        ok1 = [h["p"] for h in r[1]]
        out = outliers(ok1)
        rest = [p for i, p in enumerate(ok1) if i not in out]
        kp_unit = float(D(l["kp"]["unit_price"]["net"]))
        o4[f"строк с аналогами: {'1' if len(ok1) == 1 else '2' if len(ok1) == 2 else '>=3'}"] += 1
        if out: o4["есть выброс (правило 4а: строка ушла бы в разные единицы)"] += 1
        if len(ok1) >= 3 and any(p < S.median(ok1) * 0.2 for i, p in enumerate(ok1) if i in out): o4[">=3 аналогов, есть выброс вниз"] += 1
        if not rest:
            o4[f"разные единицы: выбросы все ({len(ok1)} аналога: {'2' if len(ok1) == 2 else '>=3'})"] += 1
        elif not (0.2 <= kp_unit / S.median(rest) <= 5):
            o4[f"разные единицы: КП вне [0,2; 5] к медиане оставшихся ({'1' if len(ok1) == 1 else '2' if len(ok1) == 2 else '>=3'} аналога)"] += 1
        ps = sorted(ok1)
        ok = ps
        if len(ok) >= 3:
            med = S.median(ok); ratios.append(ok[0] / med)
            if ok[0] < 0.1 * med and len(examples) < 12: examples.append((l["family"], l["variant_label"][:70], round(ok[0], 2), round(med, 2), len(ok)))
tot = sum(stat.values()); tm = sum(money.values())
print("строк КП (работы, вариант, сравнимые по 4а или без истории):", tot)
for k in sorted(stat): print(f"  {k:28s} {stat[k]:6d} {stat[k]/tot:6.1%}  КП {money[k]/1e9:6.1f} млрд {money[k]/tm:6.1%}")
print("зоны строк КП:", dict(zst))
print("жб:", dict(jb))
print("4а ступень -> новая:"); [print("  ", a, b, n) for (a, b), n in sorted(old.items())]
print("строк, у которых фильтр < 1 ₽ меняет ступень:", sub1, "| строк договоров < 1 ₽:", len(H_all) - len(H))
ratios.sort(); n = len(ratios)
print("min/медиана, строк с >=3 аналогами:", n)
for t in (0.05, 0.1, 0.2, 0.3, 0.5): print(f"  min < {t} медианы: {sum(r < t for r in ratios)} ({sum(r < t for r in ratios)/n:.1%})")
print("примеры min < 0,1 медианы:"); [print("  ", e) for e in examples]
zh = collections.Counter(h["z"] for h in H); print("зоны строк договоров:", dict(zh))
bt = collections.Counter()
for l in L:
    v = ctxv.get(l["context_id"])
    if not v or l["state"] not in ("comparable", "unit_mismatch", "no_history"): continue
    bt[(l["tender"], l["family"] == "Устройство жб конструкций")] += 1
print("строк по тендерам (тендер, жб?):", sorted(bt.items()))

print("выбросы и разные единицы:"); [print("  ", k, n) for k, n in sorted(o4.items())]
