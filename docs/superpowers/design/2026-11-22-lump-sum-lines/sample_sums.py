"""Предметная выборка к дизайну «Строки-суммы», ред. 2: строки для ручной разметки «сумма или ставка».

Только чтение. Запуск из backend/:
    PYTHONIOENCODING=utf-8 uv run python ../docs/superpowers/design/2026-11-22-lump-sum-lines/sample_sums.py OUT_DIR

Пишет OUT_DIR/sample.xlsx (для разметки, без ответа правила) и OUT_DIR/key.json (группа
выборки и ответ правила по каждой строке). Выход — коммерческие данные, класть вне гита
(tasks/ игнорируется).

Определения — как в measure_sums.py: вес — предложенный объём, иначе объём заказчика;
обычная ставка — медиана цены строк-работ семьи в той же каталожной единице с весом
не 1 по всем сметам, не меньше 3 строк; правило 10 / 50, без обычной ставки мерная
единица — сумма, штучная — ставка.
"""

import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, ".")
import sqlalchemy as sa  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill  # noqa: E402
from openpyxl.worksheet.datavalidation import DataValidation  # noqa: E402

from database import SessionLocal  # noqa: E402

MEASURE_UNITS = {"M", "M2", "M3", "TON", "KG"}
SEED = 20261122

#: группа выборки → (условие, сколько строк из договоров, сколько из КП)
STRATA = {
    "явная сумма, мерная": (lambda r: r["measure"] and r["q"] is not None and r["q"] >= 200, 3, 3),
    "явная сумма, штучная": (lambda r: not r["measure"] and r["q"] is not None and r["q"] >= 200, 1, 2),
    "у порога, мерная (×5–20)": (lambda r: r["measure"] and r["q"] is not None and 5 <= r["q"] < 20, 3, 4),
    "у порога, штучная (×20–100)": (lambda r: not r["measure"] and r["q"] is not None and 20 <= r["q"] < 100, 3, 3),
    "нет обычной ставки, мерная": (lambda r: r["measure"] and r["q"] is None, 3, 3),
    "нет обычной ставки, штучная": (lambda r: not r["measure"] and r["q"] is None, 1, 2),
    "объём 1, цена обычная (до ×2)": (lambda r: r["q"] is not None and r["q"] < 2, 2, 2),
    "светильники, объём 1": (lambda r: "ветильник" in r["family"], 1, 2),
}

db = SessionLocal()
db.execute(sa.text("SET TRANSACTION READ ONLY"))
rows = [
    dict(r)
    for r in db.execute(
        sa.text(
            """
select pi.id, pi.job_title_in_proposal title, wf.title family, cc.work_family_id fam,
       u.code unit, u.name unit_name,
       coalesce(pi.suggested_quantity, pi.quantity)::float w, pi.unit_cost_total::float p,
       e.contract_id, coalesce(oc.title, ot.title) object, sc.titles sections
from position_items pi
join context_members cm on cm.position_item_id = pi.id
join catalog_contexts cc on cc.id = cm.context_id
join work_families wf on wf.id = cc.work_family_id
left join catalog_positions cp on cp.id = pi.catalog_position_id
left join units_of_measure u on u.id = cp.unit_id
join proposals pr on pr.id = pi.proposal_id
join lots l on l.id = pr.lot_id
join estimates e on e.id = l.estimate_id
left join contracts c on c.id = e.contract_id
left join objects oc on oc.id = c.object_id
left join offers f on f.id = e.offer_id
left join tenders t on t.id = f.tender_id
left join objects ot on ot.id = t.object_id
left join section_chains sc on sc.id = pi.section_chain_id
where pi.is_chapter = false and cc.archived_at is null and cc.semantic_kind <> 'SYSTEM'
  and pi.unit_cost_total > 0 and coalesce(pi.suggested_quantity, pi.quantity) > 0
"""
        )
    ).mappings()
]

pool: dict = defaultdict(list)
for r in rows:
    if abs(r["w"] - 1) > 1e-9:
        pool[(r["fam"], r["unit"])].append(r["p"])
typical = {k: statistics.median(v) for k, v in pool.items() if len(v) >= 3}

ones = []
seen = set()
for r in rows:
    # цены-заглушки (0,01 ₽, 3 ₽ за лифт) — не вопрос «сумма или ставка», в разметку не идут
    if abs(r["w"] - 1) > 1e-9 or r["p"] < 10:
        continue
    key = (r["title"], round(r["p"]))
    if key in seen:
        continue
    seen.add(key)
    t = typical.get((r["fam"], r["unit"]))
    r["typical"] = t
    r["q"] = None if t is None else r["p"] / t
    r["measure"] = r["unit"] in MEASURE_UNITS
    if r["q"] is None:
        r["rule"] = "сумма" if r["measure"] else "ставка"
    else:
        r["rule"] = "сумма" if r["q"] >= (10 if r["measure"] else 50) else "ставка"
    ones.append(r)
ones.sort(key=lambda r: r["id"])

rng = random.Random(SEED)
picked: list = []
taken: set = set()
for name, (cond, n_hist, n_kp) in STRATA.items():
    for hist, n in ((True, n_hist), (False, n_kp)):
        cand = [r for r in ones if cond(r) and (r["contract_id"] is not None) == hist and r["id"] not in taken]
        for r in rng.sample(cand, min(n, len(cand))):
            r["stratum"] = name
            picked.append(r)
            taken.add(r["id"])
rng.shuffle(picked)

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
wb = Workbook()
ws = wb.active
ws.title = "Разметка"
head = [
    "№", "Откуда", "Объект", "Семья каталога", "Наименование строки", "Разделы сметы",
    "Ед.", "Объём", "Цена за ед., ₽", "Обычная ставка семьи в этой ед., ₽",
    "Ваш ответ", "Что покрывает (если сумма)", "Комментарий",
]
ws.append(head)
for i, r in enumerate(picked, 1):
    sections = " / ".join((r["sections"] or [])[-3:])
    ws.append([
        i, "договор" if r["contract_id"] is not None else "КП", r["object"], r["family"], r["title"],
        sections, r["unit_name"] or r["unit"], 1, round(r["p"]),
        None if r["typical"] is None else round(r["typical"]), None, None, None,
    ])
    r["no"] = i
bold = Font(bold=True)
for c in ws[1]:
    c.font = bold
    c.alignment = Alignment(wrap_text=True, vertical="top")
fill = PatternFill("solid", fgColor="FFF4CC")
for row in ws.iter_rows(min_row=2, min_col=11, max_col=13):
    for c in row:
        c.fill = fill
for row in ws.iter_rows(min_row=2):
    for c in row:
        c.alignment = Alignment(wrap_text=True, vertical="top")
for row in ws.iter_rows(min_row=2, min_col=9, max_col=10):
    for c in row:
        c.number_format = "# ##0"
widths = [4, 9, 22, 28, 48, 48, 7, 7, 14, 16, 12, 28, 28]
for col, w in zip("ABCDEFGHIJKLM", widths):
    ws.column_dimensions[col].width = w
ws.freeze_panes = "E2"
dv = DataValidation(type="list", formula1='"сумма,ставка,не знаю"', allow_blank=True)
ws.add_data_validation(dv)
dv.add(f"K2:K{len(picked) + 1}")
wb.save(out / "sample.xlsx")

key = [
    {k: r[k] for k in ("no", "id", "stratum", "rule", "q", "unit", "contract_id")} for r in sorted(picked, key=lambda r: r["no"])
]
json.dump(key, open(out / "key.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(picked), "строк")
