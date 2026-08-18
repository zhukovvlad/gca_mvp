"""Генератор макета экрана сравнения с поправкой на инфляцию.

Числа берутся из стенда `gca_dev` через тот же `build_comparison`, что кормит
настоящую страницу, — ни одно значение не переписывается руками. Коэффициенты
ряда ИЛЛЮСТРАТИВНЫ (ряда в системе нет) и помечены на макете.

Выход: два файла из одного содержимого.
  * standalone HTML для репозитория (конвенция макета сравнения);
  * контентная версия для публикации артефактом (без doctype/html/head/body).
"""
import datetime as dt
import html
import json
import sys
from decimal import Decimal as D, localcontext
from fractions import Fraction as F

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud import comparison as cc

ENGINE = sa.create_engine("postgresql+psycopg://postgres@localhost:5459/gca_dev")

TARGET = (2026, 8)
TARGET_LABEL = "август 2026"

#: ДВА ряда с РАЗНЫМИ коэффициентами. Одинаковые значения были бы ложью того же
#: рода, что захардкоженная подпись: селектор менял бы название, не меняя чисел.
#: Значения иллюстративны — ряда в схеме нет, его заводит миграция 0014.
SERIES = [
    {
        "id": 1,
        "name": "Росстат, ИПЦ, декабрь к декабрю",
        # `note` — примечание РЯДА, его и показывает экран сравнения.
        "note": "официальная публикация, по РФ",
        # `src` — источник ГОДА, он остаётся в БД и печатается на листе.
        "src": {2024: "бюллетень 01.2025", 2025: "бюллетень 01.2026",
                2026: "оценка на 08.2026"},
        "updated": "12.01.2026",
        "k": {2024: (D("1.075"), False), 2025: (D("1.083"), False), 2026: (D("1.060"), True)},
    },
    {
        "id": 2,
        "name": "Внутренняя оценка ПЭО",
        "note": "смета строительных ресурсов",
        "src": {2024: "расчёт ПЭО 02.2025", 2025: "расчёт ПЭО 02.2026",
                2026: "прогноз ПЭО 08.2026"},
        "updated": "04.08.2026",
        "k": {2024: (D("1.112"), False), 2025: (D("1.124"), False), 2026: (D("1.090"), True)},
    },
]

NEUTRAL_BAND, HIGH_BAND = 10, 30


def expo(y, m, years):
    e = {t: F(1) for t in sorted(years) if t < y}
    e[y] = F(m, 12)
    return e


def required_years(src, years):
    """Годы с НЕНУЛЕВЫМ показателем степени — §2.5, не сплошной диапазон."""
    a, b = expo(*src, years), expo(*TARGET, years)
    return {t: b.get(t, F(0)) - a.get(t, F(0))
            for t in sorted(set(a) | set(b))
            if b.get(t, F(0)) - a.get(t, F(0)) != 0}


def factor(src, series):
    out = D(1)
    for t, d in required_years(src, series["k"]).items():
        out *= series["k"][t][0] ** (D(d.numerator) / D(d.denominator))
    return out


def growth(coef):
    """Коэффициент → читаемый уровень: 1.083 → «+8,3 %»."""
    p = ((coef - 1) * 100).quantize(D("0.1"))
    return f"{'+' if p > 0 else ''}{p} %".replace(".", ",")


def median_of(vals):
    vs = sorted(v for v in vals if v is not None and v > 0)
    if len(vs) < 3:
        return None
    mid = len(vs) // 2
    return vs[mid] if len(vs) % 2 else (vs[mid - 1] + vs[mid]) / D(2)


def money(v):
    if v is None:
        return None
    q = v.quantize(D("1"))
    return f"{q:,}".replace(",", " ")


def pct(v):
    """Знак у нуля не ставится: ячейка «−0 %» читалась бы как снижение."""
    if v is None:
        return None
    r = v.quantize(D("1"))
    if r == 0:
        return "0 %"
    return f"{'+' if r > 0 else ''}{r} %"


def tone(v):
    if v is None:
        return "flat"
    mag = abs(v.quantize(D("1")))
    if mag <= NEUTRAL_BAND:
        return "flat"
    hi = mag > HIGH_BAND
    up = v > 0
    return ("up-" if up else "dn-") + ("hi" if hi else "lo")


with Session(ENGINE) as db, localcontext() as ctx:
    ctx.prec = 34
    ids = [r[0] for r in db.execute(sa.text("SELECT id FROM contracts ORDER BY signed_date"))]
    data = cc.build_comparison(db, ids, vat_mode=cc.VAT_MODE_NET)

    cols = []
    for c in data["columns"]:
        sd = c["signed_date"]
        sd = dt.date.fromisoformat(sd) if isinstance(sd, str) else sd
        cols.append({
            "id": c["contract_id"],
            "no": c["contract_number"],
            "obj": c["object_title"],
            "signed": sd,
            "area": c.get("area_total_sp"),
            "f": {s["id"]: factor((sd.year, sd.month), s) for s in SERIES},
        })

    def pack(vals, med):
        out = {}
        for c in cols:
            v = vals.get(c["id"])
            dv = ((v / med - 1) * 100) if (v and med and med > 0 and v > 0) else None
            out[str(c["id"])] = {"v": money(v), "d": pct(dv), "t": tone(dv)}
        return out

    rows = []
    for row in data["rows"]:
        if row["level"] != 1:
            continue
        nom = {cell["contract_id"]: cell["total"]["net_per_sqm"] for cell in row["cells"]}
        if not any(v is not None for v in nom.values()):
            continue
        mn = median_of(nom.values())
        adj, med_adj = {}, {}
        for s in SERIES:
            a = {c["id"]: (nom[c["id"]] * c["f"][s["id"]] if nom.get(c["id"]) is not None else None)
                 for c in cols}
            m = median_of(a.values())
            adj[str(s["id"])] = pack(a, m)
            med_adj[str(s["id"])] = money(m)
        rows.append({
            "code": row["code"], "title": row["title"],
            "nom": pack(nom, mn), "adj": adj,
            "mn": money(mn), "ma": med_adj,
        })

# Требуемые годы — объединение по всем сметам выборки (§2.5).
REQ_YEARS = sorted({y for c in cols for y in required_years((c["signed"].year, c["signed"].month),
                                                            SERIES[0]["k"])})

PAYLOAD = json.dumps({
    "cols": [{"id": c["id"], "no": c["no"]} for c in cols],
    "rows": rows,
    "series": [{
        "id": str(s["id"]),
        "name": s["name"],
        "note": s["note"],
        "updated": s["updated"],
        "years": [{"y": y, "g": growth(s["k"][y][0]), "fc": s["k"][y][1]} for y in REQ_YEARS],
        "factors": {str(c["id"]): {"g": growth(c["f"][s["id"]]),
                                  "k": str(c["f"][s["id"]].quantize(D("1.0000")))}
                    for c in cols},
    } for s in SERIES],
}, ensure_ascii=False)

E = html.escape


def col_head():
    out = []
    for c in cols:
        area = f"{c['area']:,}".replace(",", " ") if c["area"] else "площадь не задана"
        out.append(
            f'<th class="chead" scope="col"><span class="rh2"><span class="cno">{E(c["no"])}</span>'
            f'<span class="cobj">{E(c["obj"])}</span>'
            f'<span class="csigned">подписан {c["signed"].strftime("%d.%m.%Y")}</span>'
            f'<span class="area">{area} м²</span>'
            f'<span class="kf" data-kf="{c["id"]}"></span></span></th>')
    return "".join(out)


def body_rows():
    out = []
    for i, r in enumerate(rows):
        cells = "".join(
            f'<td class="num" data-cell="{r["nom"][str(c["id"])] is not None and 1 or 1}" '
            f'data-cid="{c["id"]}" data-ri="{i}"><span class="pmv"></span></td>'
            for c in cols)
        out.append(
            f'<tr><th class="rowhead" scope="row"><span class="rh">'
            f'<span class="code">{E(r["code"] or "")}</span>'
            f'<span class="title">{E(r["title"])}</span></span></th>'
            f'<td class="num med" data-med="{i}"></td>{cells}</tr>')
    return "".join(out)


CONTENT = f"""<title>Приведение к ценам месяца</title>
<style>
:root {{
  --page:#F4F2EC; --surface:#FFFFFF; --sunken:#F7F6F2; --hover:#FAFAF7; --sechead:#FAFAF7;
  --fg:#1F2128; --fg2:#5A5D66; --fg3:#8E8B82; --fg4:#B5B2A8;
  --bd-subtle:rgba(0,0,0,.08); --bd:rgba(0,0,0,.14);
  --accent:#5F8568; --accent-soft:#E8F0EA; --accent-bd:#C9D9CD; --accent-text:#3D5443;
  --action:#2D3A30; --action-text:#F7F6F2;
  --warn:#B5642E; --warn-soft:#FAF1E1; --warn-bd:#E8C8A8; --warn-text:#6B3915;
  --neutral-soft:#EFEEE6; --neutral-bd:#D8D4C8; --neutral-text:#5F5E5A;
  --info:#4A7290; --info-soft:#E5EEF4; --info-bd:#C5D7E2;
  --up-lo:#F3E3D5; --up-hi:#E8C8A8; --up-fg:#6B3915;
  --dn-lo:#DCE8E0; --dn-hi:#C9D9CD; --dn-fg:#2F4A38;
  --shadow-sticky:6px 0 10px -8px rgba(0,0,0,.28);
  --font-sans:"Inter","Geist","Segoe UI",Roboto,-apple-system,sans-serif;
  --font-serif:"Cormorant Garamond","Source Serif Pro",Georgia,serif;
}}
@media (prefers-color-scheme:dark) {{
  :root:not([data-theme="light"]) {{
    --page:#1A1D24; --surface:#232730; --sunken:#1F232B; --hover:#262B35; --sechead:#1C1F26;
    --fg:#EDEAE0; --fg2:#B5B2A8; --fg3:#8E8B82; --fg4:#6E6B65;
    --bd-subtle:#2D323D; --bd:#3A4148;
    --accent:#8FAB91; --accent-soft:#2A352D; --accent-bd:#3A4A3D; --accent-text:#B8C4BB;
    --action:#B8C4BB; --action-text:#1A1D24;
    --warn:#D08A50; --warn-soft:#33261A; --warn-bd:#4A3826; --warn-text:#E8C8A8;
    --neutral-soft:#262B35; --neutral-bd:#3A4148; --neutral-text:#B5B2A8;
    --info:#7BA3C0; --info-soft:#1E2A33; --info-bd:#33454F;
    --up-lo:#3A2A1C; --up-hi:#4E3620; --up-fg:#E8C8A8;
    --dn-lo:#25302A; --dn-hi:#2F4038; --dn-fg:#B8C4BB;
    --shadow-sticky:6px 0 10px -8px rgba(0,0,0,.6);
  }}
}}
:root[data-theme="dark"] {{
  --page:#1A1D24; --surface:#232730; --sunken:#1F232B; --hover:#262B35; --sechead:#1C1F26;
  --fg:#EDEAE0; --fg2:#B5B2A8; --fg3:#8E8B82; --fg4:#6E6B65;
  --bd-subtle:#2D323D; --bd:#3A4148;
  --accent:#8FAB91; --accent-soft:#2A352D; --accent-bd:#3A4A3D; --accent-text:#B8C4BB;
  --action:#B8C4BB; --action-text:#1A1D24;
  --warn:#D08A50; --warn-soft:#33261A; --warn-bd:#4A3826; --warn-text:#E8C8A8;
  --neutral-soft:#262B35; --neutral-bd:#3A4148; --neutral-text:#B5B2A8;
  --info:#7BA3C0; --info-soft:#1E2A33; --info-bd:#33454F;
  --up-lo:#3A2A1C; --up-hi:#4E3620; --up-fg:#E8C8A8;
  --dn-lo:#25302A; --dn-hi:#2F4038; --dn-fg:#B8C4BB;
  --shadow-sticky:6px 0 10px -8px rgba(0,0,0,.6);
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--page); color:var(--fg); font-family:var(--font-sans);
  font-size:14px; line-height:1.5; -webkit-font-smoothing:antialiased; }}
.wrap {{ max-width:1280px; margin:0 auto; padding:28px 20px 72px; }}
.mockbar {{ display:flex; flex-wrap:wrap; align-items:center; gap:10px; font-size:12px;
  color:var(--fg2); background:var(--neutral-soft); border:1px solid var(--neutral-bd);
  border-radius:9px; padding:9px 13px; margin-bottom:26px; }}
.mockbar strong {{ color:var(--fg); font-weight:600; }}
h1 {{ font-family:var(--font-serif); font-weight:600; font-size:34px; line-height:1.15;
  margin:0 0 8px; letter-spacing:-.01em; text-wrap:balance; }}
.sub {{ color:var(--fg2); margin:0 0 34px; max-width:66ch; }}
section {{ margin-bottom:40px; }}
h2 {{ font-family:var(--font-serif); font-weight:600; font-size:22px; margin:0 0 5px;
  text-wrap:balance; }}
.secsub {{ color:var(--fg2); font-size:13px; margin:0 0 14px; max-width:72ch; }}
.card {{ background:var(--surface); border:1px solid var(--bd-subtle); border-radius:12px; }}
.card-pad {{ padding:15px 17px; }}
.controls {{ display:flex; flex-wrap:wrap; align-items:flex-end; gap:18px; }}
.ctl {{ display:flex; flex-direction:column; gap:5px; }}
.ctl-lbl {{ font-size:10.5px; letter-spacing:.07em; text-transform:uppercase;
  color:var(--fg3); font-weight:600; }}
.seg {{ display:inline-flex; border:1px solid var(--bd); border-radius:8px; overflow:hidden; }}
.seg button {{ all:unset; cursor:pointer; padding:6px 12px; font-size:12.5px; color:var(--fg2);
  background:var(--surface); }}
.seg button + button {{ border-left:1px solid var(--bd-subtle); }}
.seg button[aria-pressed="true"] {{ background:var(--action); color:var(--action-text);
  font-weight:600; }}
.seg button:focus-visible {{ outline:2px solid var(--accent); outline-offset:-2px; }}
.seg button:disabled {{ cursor:not-allowed; color:var(--fg4); background:var(--sunken); }}
.jsonbox {{ margin:11px 0 0; padding:10px 13px; background:var(--sunken);
  border:1px solid var(--bd-subtle); border-radius:9px; overflow-x:auto;
  font-size:12px; line-height:1.45; }}
.jsonbox code {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  color:var(--fg2); white-space:pre; }}
#urlbar {{ font-size:11.5px; }}
.group {{ margin:16px 0 0; border:1px solid var(--accent-bd); border-radius:10px;
  background:var(--accent-soft); padding:12px 15px 14px; }}
.group legend {{ font-size:10.5px; letter-spacing:.07em; text-transform:uppercase;
  color:var(--accent-text); font-weight:700; padding:0 6px; }}
.group .ctl-lbl {{ color:var(--accent-text); opacity:.85; }}
.levels {{ display:flex; flex-wrap:wrap; align-items:baseline; gap:6px 14px;
  margin-top:13px; padding-top:11px; border-top:1px solid var(--accent-bd);
  font-size:12.5px; color:var(--accent-text); }}
/* `display:flex` перебивает браузерное `[hidden]{{display:none}}` — без этой
   строки пустая полоса всё равно занимает место бордюром и отступом. */
.levels[hidden] {{ display:none; }}
.btn.sm {{ padding:3px 10px; font-size:11.5px; }}
.btn:disabled {{ cursor:not-allowed; color:var(--fg4); background:var(--sunken); }}
/* Приближение shadcn-диалога проекта (`components/ui/dialog.tsx` на @base-ui):
   затемнение, карточка по центру, шапка с заголовком и описанием, подвал с
   кнопками справа. Здесь это нативный <dialog> — макет не тянет React. */
.dlg {{ border:none; padding:0; background:transparent; max-width:min(680px, calc(100vw - 32px));
  width:100%; color:var(--fg); }}
.dlg::backdrop {{ background:rgba(20,22,28,.55); }}
.dlgform {{ background:var(--surface); border:1px solid var(--bd); border-radius:14px;
  box-shadow:0 18px 48px -16px rgba(0,0,0,.4); display:flex; flex-direction:column;
  max-height:calc(100vh - 64px); }}
.dlghead {{ display:flex; align-items:flex-start; gap:14px; padding:18px 20px 14px;
  border-bottom:1px solid var(--bd-subtle); }}
.dlghead h4 {{ font-family:var(--font-serif); font-size:19px; font-weight:600; margin:0 0 4px; }}
.dlgsub {{ margin:0; font-size:12.5px; color:var(--fg2); max-width:60ch; }}
.xbtn {{ all:unset; cursor:pointer; margin-left:auto; color:var(--fg3); font-size:15px;
  line-height:1; padding:3px 6px; border-radius:6px; }}
.xbtn:hover {{ color:var(--fg); background:var(--hover); }}
.xbtn:focus-visible {{ outline:2px solid var(--accent); outline-offset:1px; }}
.dlgbody {{ padding:16px 20px; overflow-y:auto; display:flex; flex-direction:column; gap:14px; }}
.dlgfoot {{ display:flex; justify-content:flex-end; gap:9px; padding:13px 20px;
  border-top:1px solid var(--bd-subtle); background:var(--sunken);
  border-radius:0 0 14px 14px; }}
.dlgyears input[type=text] {{ width:100%; }}
.dlgyears .kin {{ max-width:110px; font-variant-numeric:tabular-nums; }}
.dlgyears td {{ vertical-align:middle; }}
.levels .lv-lbl {{ font-size:10.5px; letter-spacing:.07em; text-transform:uppercase;
  font-weight:700; opacity:.8; }}
.yr {{ font-variant-numeric:tabular-nums; white-space:nowrap; }}
.yr b {{ font-weight:600; }}
.yr .fc {{ font-size:10.5px; text-transform:uppercase; letter-spacing:.05em;
  opacity:.75; }}
.levels .meta {{ margin-left:auto; font-size:11.5px; opacity:.8; white-space:nowrap; }}
select, input[type=text], input[type=month] {{ font:inherit; font-size:12.5px; color:var(--fg);
  background:var(--surface); border:1px solid var(--bd); border-radius:8px; padding:6px 9px; }}
select:disabled, input:disabled {{ color:var(--fg4); background:var(--sunken); }}
.btn {{ all:unset; cursor:pointer; padding:6px 13px; border:1px solid var(--bd);
  border-radius:8px; font-size:12.5px; color:var(--fg2); background:var(--surface); }}
.btn.primary {{ background:var(--action); color:var(--action-text); border-color:var(--action);
  font-weight:600; }}
.btn:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; }}
.axisnote {{ font-size:12.5px; color:var(--fg2); margin:11px 0 0; }}
.axisnote b {{ color:var(--fg); font-weight:600; }}
.pill {{ display:inline-block; font-size:11px; padding:1px 7px; border-radius:999px;
  border:1px solid var(--accent-bd); background:var(--accent-soft); color:var(--accent-text); }}
.pill.fc {{ border-color:var(--warn-bd); background:var(--warn-soft); color:var(--warn-text); }}
.pill.arch {{ border-color:var(--neutral-bd); background:var(--neutral-soft);
  color:var(--neutral-text); }}
.banner {{ display:flex; gap:11px; align-items:flex-start; border:1px solid var(--warn-bd);
  background:var(--warn-soft); color:var(--warn-text); border-radius:10px; padding:11px 14px;
  font-size:13px; }}
.banner .bi {{ font-weight:700; flex:0 0 auto; }}
.banner code {{ background:rgba(0,0,0,.06); padding:0 4px; border-radius:3px; }}
.scroller {{ overflow-x:auto; border:1px solid var(--bd-subtle); border-radius:12px;
  background:var(--surface); }}
table.cmp {{ border-collapse:separate; border-spacing:0; font-size:13px; min-width:100%; }}
table.cmp th, table.cmp td {{ padding:7px 12px; }}
table.cmp thead th {{ background:var(--sechead); vertical-align:top; text-align:left;
  border-bottom:1px solid var(--bd-subtle); }}
.chead {{ border-left:1px solid var(--bd-subtle); }}
.rh2 {{ display:flex; flex-direction:column; gap:1px; }}
.cno {{ font-weight:600; font-size:13.5px; white-space:nowrap; }}
.cobj, .csigned, .area {{ font-size:12px; color:var(--fg2); white-space:nowrap; }}
.csigned, .area {{ color:var(--fg3); font-size:11.5px; }}
.area {{ font-variant-numeric:tabular-nums; }}
.kf {{ font-size:11.5px; font-variant-numeric:tabular-nums; color:var(--accent-text);
  background:var(--accent-soft); border:1px solid var(--accent-bd); border-radius:4px;
  padding:0 5px; margin-top:4px; align-self:flex-start; white-space:nowrap; }}
th.rowhead {{ position:sticky; left:0; z-index:2; background:var(--surface); text-align:left;
  font-weight:400; min-width:330px; max-width:330px; box-shadow:var(--shadow-sticky); }}
thead th.rowhead {{ background:var(--sechead); z-index:4; }}
.rh {{ display:flex; align-items:baseline; gap:7px; }}
.code {{ color:var(--fg3); font-size:11.5px; font-variant-numeric:tabular-nums; min-width:22px; }}
.title {{ color:var(--fg); font-weight:500; }}
table.cmp tbody td, table.cmp tbody th.rowhead {{ border-bottom:1px solid var(--bd-subtle); }}
table.cmp tbody tr:hover td, table.cmp tbody tr:hover th.rowhead {{ background:var(--hover); }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
.num.med {{ color:var(--fg2); border-left:1px solid var(--bd-subtle);
  background:var(--sunken); }}
td.num[data-cid] {{ border-left:1px solid var(--bd-subtle); }}
.dev {{ display:inline-block; margin-left:7px; font-size:11px; padding:1px 5px; border-radius:4px;
  font-variant-numeric:tabular-nums; }}
.dev.flat {{ color:var(--fg4); }}
.dev.up-lo {{ background:var(--up-lo); color:var(--up-fg); }}
.dev.up-hi {{ background:var(--up-hi); color:var(--up-fg); font-weight:600; }}
.dev.dn-lo {{ background:var(--dn-lo); color:var(--dn-fg); }}
.dev.dn-hi {{ background:var(--dn-hi); color:var(--dn-fg); font-weight:600; }}
.dash {{ color:var(--fg4); }}
.grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
@media (max-width:920px) {{ .grid2 {{ grid-template-columns:1fr; }} }}
h3 {{ font-size:11.5px; letter-spacing:.08em; text-transform:uppercase; color:var(--fg3);
  margin:0 0 9px; font-weight:600; }}
ul.tight {{ margin:0; padding-left:18px; color:var(--fg2); }}
ul.tight li {{ margin-bottom:6px; }}
ul.tight b {{ color:var(--fg); font-weight:600; }}
table.mini {{ border-collapse:collapse; width:100%; font-size:12.5px; }}
table.mini th {{ text-align:left; font-size:10.5px; letter-spacing:.06em; text-transform:uppercase;
  color:var(--fg3); font-weight:600; padding:0 10px 6px 0; }}
table.mini td {{ padding:6px 10px 6px 0; border-top:1px solid var(--bd-subtle);
  font-variant-numeric:tabular-nums; }}
table.mini td.wide {{ font-variant-numeric:normal; }}
.decode {{ font-size:12px; margin-top:5px; }}
.decode.ok {{ color:var(--accent-text); }}
.decode.bad {{ color:var(--warn-text); font-weight:600; }}
.field {{ display:flex; flex-direction:column; gap:3px; }}
.hint {{ font-size:11.5px; color:var(--fg3); }}
.bound {{ border-left:3px solid var(--warn); padding-left:13px; }}
@media (prefers-reduced-motion:reduce) {{ * {{ transition:none !important; }} }}
</style>

<div class="wrap">

  <div class="mockbar">
    <strong>Макет.</strong>
    <span>Числа — стенд <code>gca_dev</code> на 18.08.2026, тем же
    <code>build_comparison</code>, что кормит настоящую страницу.</span>
    <span class="pill fc">коэффициенты ряда иллюстративны</span>
  </div>

  <h1>Приведение к ценам месяца</h1>
  <p class="sub">Как поправка на инфляцию ложится на существующий экран сравнения
  договоров. Спека — <code>2026-08-18-inflation-adjustment-design.md</code>; макет
  не имеет права с ней расходиться.</p>

  <section>
    <h2>Переключатель и подпись</h2>
    <p class="secsub">Приведение — <b>отдельная группа</b> элементов управления, а
    не три поля вперемешку с существующими: ряд без режима и месяца ничего не
    значит, и стоять он должен внутри группы, а не между «НДС» и «Инфляцией». По
    умолчанию приведение выключено, и страница отвечает как до фичи — посимвольно.</p>
    <div class="card card-pad">
      <div class="controls">
        <span class="ctl"><span class="ctl-lbl">Показатель</span>
          <span class="seg" role="group" aria-label="Показатель">
            <button type="button" aria-pressed="false">Сумма</button>
            <button type="button" aria-pressed="true">₽/м²</button>
          </span></span>
        <span class="ctl"><span class="ctl-lbl">НДС</span>
          <span class="seg" role="group" aria-label="Режим НДС">
            <button type="button" aria-pressed="false">Своя ставка</button>
            <button type="button" aria-pressed="false">Единая</button>
            <button type="button" aria-pressed="true">Без НДС</button>
          </span></span>
        <button class="btn" type="button" style="margin-left:auto">Выгрузить в Excel</button>
      </div>

      <fieldset class="group">
        <legend>Поправка на инфляцию</legend>
        <div class="controls">
          <span class="ctl"><span class="ctl-lbl">Режим</span>
            <span class="seg" role="group" aria-label="Приведение">
              <button type="button" id="offBtn" aria-pressed="true">Номинал</button>
              <button type="button" id="onBtn" aria-pressed="false" disabled>Привести</button>
            </span></span>
          <span class="ctl"><span class="ctl-lbl">Ряд индексов</span>
            <select id="seriesSel">
              <option value="">Выберите ряд</option>
              {"".join(f'<option value="{s["id"]}">{E(s["name"])}</option>' for s in SERIES)}
            </select></span>
          <span class="ctl"><span class="ctl-lbl">В ценах</span>
            <input type="month" id="monthInp" value="" disabled></span>
        </div>
        <div id="levels" class="levels" hidden></div>
      </fieldset>

      <p class="axisnote" id="axisnote"></p>
      <p class="axisnote" style="margin-top:6px"><span class="hint">Адрес
      страницы:</span> <code id="urlbar"></code></p>
    </div>
    <p class="axisnote bound" style="margin-top:12px">Умолчательного ряда
    <b>нет</b>, и это не придирка: на нём держится ответ <code>400</code> для
    месяца без ряда (§2.12). Поэтому «Привести» недоступно, пока ряд не выбран, а
    сам выбор ряда числа ещё не меняет. Поле месяца до первого ответа сервера
    <b>пусто</b> — текущий месяц определяет сервер в названной таймзоне, клиенту
    это запрещено (§2.7).</p>
  </section>

  <section>
    <h2>Таблица</h2>
    <p class="secsub">Выберите ряд выше, затем нажмите «Привести» — колонки,
    медиана и подсветка пересчитываются. Коэффициент каждого договора показан в
    его шапке: он свой у каждой сметы, а не один на выборку.</p>
    <div class="scroller">
      <table class="cmp">
        <thead>
          <tr>
            <th class="rowhead" scope="col">Статья классификатора</th>
            <th class="num med" scope="col">Медиана</th>
            {col_head()}
          </tr>
        </thead>
        <tbody id="tb">{body_rows()}</tbody>
      </table>
    </div>
    <p class="axisnote" style="margin-top:11px">17 корневых статей с данными.
    Дерево трёхуровневое — здесь показан только первый уровень, чтобы макет
    оставался читаемым.</p>
  </section>

  <section>
    <h2>Что видно на этих числах</h2>
    <p class="secsub">Главное — не то, что суммы выросли, а то, что меняется
    порядок договоров относительно медианы.</p>
    <div class="grid2">
      <div class="card card-pad">
        <h3>Смена знака отклонения</h3>
        <p style="margin:0 0 9px; color:var(--fg2); font-size:13px">Статья
        <b>«ВИС — Электрические и слаботочные системы»</b>: два договора
        меняются местами относительно медианы.</p>
        <table class="mini">
          <thead><tr><th class="wide">Договор</th><th>Номинал</th><th>Приведено</th></tr></thead>
          <tbody>
            <tr><td class="wide">12-СИТ-МР <span class="hint">июль 2026</span></td>
                <td><span class="dev flat">0 %</span></td>
                <td><span class="dev flat">−5 %</span></td></tr>
            <tr><td class="wide">СДП-1-МР <span class="hint">февраль 2025</span></td>
                <td><span class="dev flat">0 %</span></td>
                <td><span class="dev flat">+5 %</span></td></tr>
          </tbody>
        </table>
        <p class="axisnote" style="margin-top:9px">Сегодня страница показывает
        договор 2026 года чуть дороже медианы, а 2025 года — чуть дешевле. После
        приведения наоборот. Точные величины: +0,4 % → −4,7 % и −0,4 % → +4,7 %.</p>
      </div>
      <div class="card card-pad">
        <h3>Размер сдвига по статьям</h3>
        <p style="margin:0 0 9px; color:var(--fg2); font-size:13px">Максимальное
        изменение отклонения внутри статьи, п.п.</p>
        <table class="mini">
          <thead><tr><th class="wide">Статья</th><th>Сдвиг</th></tr></thead>
          <tbody>
            <tr><td class="wide">Лифты, подъемники</td><td>58,2</td></tr>
            <tr><td class="wide">ТХ</td><td>30,3</td></tr>
            <tr><td class="wide">Устройство гидроизоляции подземной части</td><td>21,8</td></tr>
            <tr><td class="wide">Отделочные работы</td><td>21,1</td></tr>
            <tr><td class="wide">ВИС — механические системы</td><td>19,9</td></tr>
          </tbody>
        </table>
        <p class="axisnote" style="margin-top:9px">Разброс подписания на стенде —
        28.05.2024 … 06.07.2026. Коэффициенты приведения к августу 2026 идут от
        1.0049 до 1.1744, то есть самый ранний договор дорожает на 17,4 %.</p>
      </div>
    </div>
  </section>

  <section>
    <h2>Отказ вместо половинчатого приведения</h2>
    <p class="secsub">Если в выбранном ряду не хватает года, приведение не
    включается вовсе. Половинчатой таблицы не бывает: погасить колонку самого
    раннего договора значило бы убрать ровно тот договор, ради которого
    приведение включали.</p>
    <div class="card card-pad">
      <div class="banner">
        <span class="bi">!</span>
        <span><b>Приведение не применено.</b> В ряду «{E(SERIES[0]["name"])}» нет
        коэффициентов за <b>2024, 2025</b>. Показаны номинальные суммы — рубли
        разных лет. Заполните недостающие годы в разделе «Нормативы → Индексы
        инфляции».<br>
        <span class="hint">Ответ API — <code>422</code>:</span></span>
      </div>
      <pre class="jsonbox"><code>{{
 "detail": {{
  "code": "missing_inflation_years",
  "message": "Не заданы коэффициенты за годы: 2024, 2025.",
  "missing_years": [2024, 2025]
 }}
}}</code></pre>
      <p class="axisnote">Параметры остались в адресе:
      <code>?inflation_series_id=1&amp;target_month=2026-08</code> — видно, что
      именно не сработало, и недостающие годы можно завести не угадывая.</p>
      <p class="axisnote"><b>Выгрузка отвечает тем же структурированным
      <code>422</code>; файла не возникает вовсе</b> — «отказ на листе Excel»
      невозможен, потому что при отказе лист не собирается. Интерфейс показывает
      то же сообщение, что экран. Печать использованных коэффициентов (§2.10)
      относится только к успешной выгрузке.</p>
      <p class="axisnote">Второй код — <code>amendment_date_missing</code>, когда
      у допсоглашения нет собственной даты: дату базового договора подставлять
      нельзя.</p>
    </div>
  </section>

  <section>
    <h2>Экран ряда индексов</h2>
    <p class="secsub">Раздел «Нормативы», право <code>admin</code>. Справочник
    создаётся пустым — безымянных «официального» и «неофициального» рядов не
    бывает, название несёт конкретный показатель.</p>
    <div class="card card-pad">
      <h3>Ряды</h3>
      <table class="mini">
        <thead><tr><th class="wide">Название</th><th>Годы</th><th>Состояние</th><th></th></tr></thead>
        <tbody>
          <tr><td class="wide"><b>{E(SERIES[0]["name"])}</b>
              <span class="hint">{E(SERIES[0]["note"])}</span></td>
              <td>2024–2026</td><td><span class="pill">активен</span></td>
              <td><button class="btn sm editBtn" type="button">Изменить</button></td></tr>
          <tr><td class="wide">{E(SERIES[1]["name"])}
              <span class="hint">{E(SERIES[1]["note"])}</span></td>
              <td>2024–2026</td><td><span class="pill">активен</span></td>
              <td><button class="btn sm editBtn" type="button">Изменить</button></td></tr>
          <tr><td class="wide">ИПЦ, среднегодовой <span class="hint">не подходит
              формуле</span></td><td>2024–2025</td>
              <td><span class="pill arch">в архиве</span></td>
              <td><button class="btn sm" type="button" disabled
                  title="архивный ряд правке недоступен — сначала вернуть в активные">Изменить</button></td></tr>
        </tbody>
      </table>
      <p class="axisnote">Правка — <b>модальным окном</b>, как уже сделано на этом
      экране для ставок (`RateStandardFormDialog`, `ReapproveDialog`). Нажмите
      «Изменить» у первого ряда. Архивный ряд читается по старой ссылке, но в
      выборе не предлагается и правке недоступен — <code>409</code>, пока не
      вернут в активные.</p>
    </div>

    <dialog id="dlg" class="dlg">
      <form method="dialog" class="dlgform">
        <div class="dlghead">
          <div>
            <h4>Ряд индексов инфляции</h4>
            <p class="dlgsub">Коэффициенты — декабрь к декабрю. Значения правятся
            на месте: версий у ряда нет, поэтому правка меняет числа на уже
            открытых сравнениях, а дата правки видна на поверхности.</p>
          </div>
          <button class="xbtn" type="submit" value="cancel" aria-label="Закрыть">✕</button>
        </div>

        <div class="dlgbody">
          <div class="field">
            <label class="ctl-lbl" for="f-name">Название</label>
            <input id="f-name" type="text" value="{E(SERIES[0]["name"])}">
            <span class="hint">Несёт конкретный показатель, а не «официальный»:
            читатель обязан понять, чем приведены числа.</span>
          </div>
          <div class="field">
            <label class="ctl-lbl" for="f-note">Примечание</label>
            <input id="f-note" type="text" value="{E(SERIES[0]["note"])}">
            <span class="hint">Показывается на экране сравнения рядом с уровнями.</span>
          </div>

          <table class="mini dlgyears">
            <thead><tr><th>Год</th><th>Коэффициент</th><th>Уровень</th>
              <th class="wide">Источник</th><th>Прогноз</th></tr></thead>
            <tbody>
              {"".join(
                f'<tr><td>{y}</td>'
                f'<td><input class="kin" type="text" inputmode="decimal" '
                f'value="{SERIES[0]["k"][y][0].quantize(D("1.0000"))}"></td>'
                f'<td class="decode ok" data-dec>{growth(SERIES[0]["k"][y][0])}</td>'
                f'<td class="wide"><input type="text" value="{E(SERIES[0]["src"][y])}"></td>'
                f'<td style="text-align:center"><input type="checkbox" '
                f'{"checked" if SERIES[0]["k"][y][1] else ""}></td></tr>'
                for y in REQ_YEARS)}
            </tbody>
          </table>
          <p class="hint">Источник обязателен у каждого года: на вопрос «откуда
          8,3 %» надо чем-то отвечать (§1 п. 4). На экране сравнения он не
          показывается — там достаточно примечания ряда, — но печатается на листе
          выгрузки.</p>

          <div class="field bound">
            <label class="ctl-lbl" for="f-bad">Проверьте: тот же ввод, если спутать
            коэффициент с приростом</label>
            <input id="f-bad" class="kin" type="text" inputmode="decimal" value="0.083">
            <span class="decode bad" data-dec>Снижение 91,7 %</span>
            <span class="hint">Расшифровка и есть защита: схема приняла бы
            <code>0.083</code> — условие только «больше нуля», — и посчитала бы
            дефляцию на 91,7 % молча. Искусственного диапазона нет: порог отверг
            бы законный год высокой инфляции.</span>
          </div>
        </div>

        <div class="dlgfoot">
          <button class="btn" type="submit" value="cancel">Отмена</button>
          <button class="btn primary" type="submit" value="save">Сохранить</button>
        </div>
      </form>
    </dialog>
  </section>

  <section>
    <h2>Что макет показывает честно</h2>
    <div class="grid2">
      <div class="card card-pad">
        <h3>Настоящее</h3>
        <ul class="tight">
          <li><b>Все суммы, площади и отклонения</b> — стенд <code>gca_dev</code>,
          пять договоров, тот же агрегат, что у страницы.</li>
          <li><b>Даты подписания</b> и выведенные из них коэффициенты.</li>
          <li><b>Пороги подсветки</b> — ≤10 % нейтрально, 10–30 % и свыше 30 %
          две ступени, как в <code>deviationTone.ts</code>.</li>
          <li><b>Ось нетто</b>: медиана и отклонения считаются по нетто в любом
          режиме показа.</li>
        </ul>
      </div>
      <div class="card card-pad">
        <h3>Иллюстративное</h3>
        <ul class="tight">
          <li><b>Коэффициенты обоих рядов.</b> «Росстат» —
          {" / ".join(growth(SERIES[0]["k"][y][0]) for y in REQ_YEARS)}; «ПЭО» —
          {" / ".join(growth(SERIES[1]["k"][y][0]) for y in REQ_YEARS)}. Ряда в
          системе нет, его заводит миграция 0014; числа взяты для примера и
          помечены. Значения у рядов РАЗНЫЕ намеренно: одинаковые означали бы, что
          селектор меняет подпись, не меняя чисел.</li>
          <li><b>Названия рядов</b> — образцы формулировок, а не заведённые
          записи.</li>
          <li><b>Корзины ДГП / ДС / Итого</b> не показаны: допсоглашений на
          стенде ноль, и приведение на смету на этих данных численно совпадает с
          приведением на договор.</li>
          <li><b>Второй и третий уровень дерева</b> статей свёрнут.</li>
        </ul>
      </div>
    </div>
  </section>
</div>

<script>
const DATA = {PAYLOAD};
const NOTE_OFF = 'Ось сравнения — <b>нетто</b>. Приведение выключено: суммы в рублях года подписания каждого договора.';
// Подпись СТРОИТСЯ из выбранного ряда, а не зашита: захардкоженное название
// утверждало бы неправду о том, каким индексом построены числа.
const noteOn = (s) => 'Ось сравнения — <b>нетто</b>, цены приведены к <b>{TARGET_LABEL}</b> по ряду «' +
  s.name + '»' + (s.years.some(y => y.fc) ? ', ' + s.years.filter(y => y.fc).map(y => y.y).join(' и ') +
  ' год — прогноз' : '') + '.';
// Месяц, который вернул бы СЕРВЕР. Клиент его не вычисляет: до ответа поле пусто.
const SERVER_MONTH = '2026-08';
let on = false;
const seriesById = (id) => DATA.series.find(s => s.id === id) || null;

const sel = document.getElementById('seriesSel');
const monthInp = document.getElementById('monthInp');
const onBtn = document.getElementById('onBtn');
const offBtn = document.getElementById('offBtn');

function render() {{
  const s = seriesById(sel.value);
  document.querySelectorAll('td[data-cell]').forEach(td => {{
    const r = DATA.rows[+td.dataset.ri];
    const c = on && s ? r.adj[s.id][td.dataset.cid] : r.nom[td.dataset.cid];
    const span = td.querySelector('.pmv');
    if (!c || c.v === null) {{ span.innerHTML = '<span class="dash">—</span>'; return; }}
    const dev = c.d === null ? '' : ' <span class="dev ' + c.t + '">' + c.d.replace('-', '\\u2212') + '</span>';
    span.innerHTML = c.v + dev;
  }});
  document.querySelectorAll('td[data-med]').forEach(td => {{
    const r = DATA.rows[+td.dataset.med];
    const v = on && s ? r.ma[s.id] : r.mn;
    td.textContent = v === null ? '—' : v;
  }});
  // Коэффициент колонки — УРОВЕНЬ в процентах: его и читает человек.
  // Сам множитель остаётся в подсказке, чтобы арифметика была проверяема.
  document.querySelectorAll('[data-kf]').forEach(el => {{
    if (!on || !s) {{ el.hidden = true; el.textContent = ''; el.removeAttribute('title'); return; }}
    const f = s.factors[el.dataset.kf];
    el.hidden = false;
    el.textContent = f.g;
    el.title = 'множитель × ' + f.k;
  }});
  document.getElementById('axisnote').innerHTML = on && s ? noteOn(s) : NOTE_OFF;

  // Уровень инфляции по годам — иначе с экрана не понять, из чего вышла поправка.
  const box = document.getElementById('levels');
  if (s) {{
    box.hidden = false;
    box.innerHTML = '<span class="lv-lbl">Ряд по годам</span>' +
      s.years.map(y => '<span class="yr">' + y.y + ' <b>' + y.g + '</b>' +
        (y.fc ? ' <span class="fc">прогноз</span>' : '') + '</span>').join('') +
      '<span class="meta">' + (s.note ? s.note + ' · ' : '') + 'правлен ' + s.updated + '</span>';
  }} else {{
    box.hidden = true;
    box.innerHTML = '';
  }}

  const hasSeries = sel.value !== '';
  onBtn.setAttribute('aria-pressed', String(on));
  offBtn.setAttribute('aria-pressed', String(!on));
  // «Привести» недоступно без ряда: умолчательного ряда не существует.
  onBtn.disabled = !hasSeries;
  // Месяц имеет смысл только вместе с рядом (иначе сервер ответил бы 400).
  monthInp.disabled = !hasSeries;

  const params = [];
  if (on) {{
    params.push('inflation_series_id=' + sel.value);
    if (monthInp.value) params.push('target_month=' + monthInp.value);
  }}
  document.getElementById('urlbar').textContent =
    '/compare?ids=5,6,4,11,10&vat_mode=net' + (params.length ? '&' + params.join('&') : '');
}}

sel.addEventListener('change', () => {{
  // Выбор ряда сам числа НЕ меняет — только открывает «Привести».
  if (!sel.value) {{ on = false; monthInp.value = ''; }}
  render();
}});
onBtn.addEventListener('click', () => {{
  if (!sel.value) return;
  on = true;
  // Сервер разрешил текущий месяц и вернул его; клиент записывает в поле и URL.
  if (!monthInp.value) monthInp.value = SERVER_MONTH;
  render();
}});
offBtn.addEventListener('click', () => {{
  on = false;
  // Возврат к номиналу убирает инфляционные параметры из адреса.
  monthInp.value = '';
  render();
}});
monthInp.addEventListener('change', () => {{ if (on) render(); }});

// --- модальное окно правки ряда
const dlg = document.getElementById('dlg');
// Правятся ОБА ряда — и официальный, и кастомный: право у admin одно на все ряды.
document.querySelectorAll('.editBtn').forEach(b =>
  b.addEventListener('click', () => dlg.showModal()));

// Живая расшифровка коэффициента внутри диалога: 1.083 -> «Рост 8,3 %».
// Считается по ВВЕДЁННОМУ значению, а не по сохранённому, — иначе она не защита.
function decode(input) {{
  const cell = input.closest('tr')
    ? input.closest('tr').querySelector('[data-dec]')
    : input.parentElement.querySelector('[data-dec]');
  if (!cell) return;
  const raw = input.value.trim().replace(',', '.');
  const v = Number(raw);
  if (!raw || !isFinite(v) || v <= 0) {{
    cell.textContent = '—';
    cell.className = 'decode';
    return;
  }}
  const p = (v - 1) * 100;
  const abs = Math.abs(p).toFixed(1).replace('.', ',');
  cell.textContent = (p >= 0 ? 'Рост ' : 'Снижение ') + abs + ' %';
  // Красным помечается не «снижение», а невероятный уровень: спутанный прирост
  // даёт минус девяносто процентов, и это должно бросаться в глаза.
  cell.className = 'decode ' + (p < -50 || p > 100 ? 'bad' : 'ok');
}}
document.querySelectorAll('.kin').forEach(inp => {{
  inp.addEventListener('input', () => decode(inp));
  decode(inp);
}});

render();
</script>
"""

STANDALONE = f"""<!--
  Макет экрана сравнения с поправкой на инфляцию (маршрут `/compare`), гейт 2
  фичи `feat/inflation-adjustment`. Заведён 2026-08-18.

  ЧТО ЭТО. Форма, в которой поправка ложится на существующий экран. Спека, от
  которой макет не имеет права расходиться:
  `2026-08-18-inflation-adjustment-design.md`.

  ДАННЫЕ НАСТОЯЩИЕ — стенд `gca_dev` на 18.08.2026: 5 договоров, 17 корневых
  статей с данными, суммы и отклонения получены тем же `build_comparison`, что
  кормит страницу. Макет СГЕНЕРИРОВАН скриптом из базы: ни одно число не
  переписано руками, потому что именно на переписывании числа и расходятся.

  ЧТО ИЛЛЮСТРАТИВНО И ПОМЕЧЕНО НА САМОМ МАКЕТЕ: коэффициенты ряда
  (1.075 / 1.083 / 1.060) — ряда в схеме ещё нет, его заводит миграция 0014;
  названия рядов; состояние отказа собрано вручную, потому что на стенде ряд
  полон по построению.

  ЧЕГО В МАКЕТЕ НЕТ: корзин ДГП/ДС/Итого (допсоглашений ноль, и приведение на
  смету на этих данных совпадает с приведением на договор), второго и третьего
  уровня дерева статей.

  Палитра, шрифтовые стеки и классы таблицы взяты из
  `2026-08-17-contract-comparison-mockup.html` без изменений — это тот же экран.
-->
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{CONTENT}
</body>
</html>
"""

out_repo, out_art = sys.argv[1], sys.argv[2]
with open(out_repo, "w", encoding="utf-8") as fh:
    fh.write(STANDALONE.replace("<title>", "<title>").replace("</style>\n\n<div", "</style>\n</head>\n<body>\n<div"))
with open(out_art, "w", encoding="utf-8") as fh:
    fh.write(CONTENT)
print("rows:", len(rows), "cols:", len(cols))
print("repo:", out_repo)
print("artifact:", out_art)
