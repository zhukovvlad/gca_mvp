"""Генератор макета: диаграмма стоимости НА СТРАНИЦЕ СРАВНЕНИЯ + фильтр по классам.

Макет показывает экран `/compare` целиком — панель управления, диаграмму и
таблицу статей, — потому что диаграмма живёт не отдельной страницей, а над той
же таблицей и от тех же переключателей.

Числа берутся из стенда `gca_dev` тем же `build_comparison`, что кормит
настоящую страницу, — ни одно значение не переписывается руками. Ряды индексов
на стенде настоящие (миграция 0014 применена), иллюстративного нет ничего.

ПОЧЕМУ СНИМКОВ ТРИДЦАТЬ, А НЕ ДЕВЯТЬ. Фильтр по классам — это не показ/скрытие
столбцов на клиенте, а ДРУГАЯ ВЫБОРКА: медиана и отклонения считаются по тем
договорам, что в выборке, и обязаны пересчитаться сервером. Поэтому для каждого
непустого подмножества классов (их семь) снят свой ответ.

Разложение payload повторяет инварианты, а не удобство:
  * `vals` — показываемые суммы: зависят от режима НДС и ряда, НЕ от выборки;
  * `stat` — медианы и отклонения: считаются по нетто, поэтому зависят от ряда
    и выборки, но НЕ от режима показа НДС;
  * `top`  — верх оси: чистая геометрия, единственное, что делит клиент.

Выход: два файла из одного содержимого.
  * standalone HTML для репозитория (конвенция макетов сравнения);
  * контентная версия для публикации артефактом (без doctype/html/head/body).

Запуск:
  cd backend && PYTHONPATH=. .venv/Scripts/python.exe \
    ../docs/superpowers/specs/2026-08-19-comparison-cost-chart-mockup.gen.py \
    ../docs/superpowers/specs/2026-08-19-comparison-cost-chart-mockup.html \
    <путь для артефактной версии>
"""
import datetime as dt
import html
import itertools
import json
import math
import sys
from decimal import Decimal as D, localcontext

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud import comparison as cc
from money.inflation import YearMonth

ENGINE = sa.create_engine("postgresql+psycopg://postgres@localhost:5459/gca_dev")

#: ДВА целевых месяца — вперёд и НАЗАД. Второй нужен по существу, а не для
#: полноты: спека инфляции прямо разрешает цель раньше сметы («коэффициент
#: меньше единицы, отказа нет», DoD 10), и на январе 2025 стенд даёт РАЗНЫЕ
#: ЗНАКИ В ОДНОМ КАДРЕ — 12-СИТ-МР и Д-2026-27002 сжимаются, ВЕР1-ГП и ДГП-3С10
#: растут. Без такого состояния поправку легко нарисовать «только вверх», что и
#: было сделано в первой редакции макета.
MONTHS = [
    {"ym": YearMonth(2026, 8), "v": "2026-08", "dat": "августу 2026"},
    {"ym": YearMonth(2025, 1), "v": "2025-01", "dat": "январю 2025"},
]
DEFAULT_MONTH = "2026-08"
STAND_DATE = "20.08.2026"

#: Единица ДИАГРАММЫ. Таблица сравнения всегда в ₽/м² — это ось экрана; сумма
#: договора отвечает на другой вопрос и живёт только у диаграммы (см. §«Решения»).
INDICATORS = [("sqm", "₽/м²"), ("sum", "Сумма договора")]

#: Порядок и подписи режимов НДС — те же, что `VAT_MODE_LABELS` в `ComparePage`.
VAT_MODES = [("own", "Своя ставка"), ("single", "Единая"), ("net", "Без НДС")]

#: Ставка режима «Единая» задаётся ЯВНО, а не предвыбором: предвыбор зависит от
#: состава выборки, и фильтр по классам молча менял бы ставку показа вместе с
#: набором договоров. На экране она стоит в своём селекторе и в адресе.
SINGLE_RATE = D(20)

SERIES_IDS = [1, 2]

NEUTRAL_BAND, HIGH_BAND = 10, 30

#: Словарь причин неполноты — тот же, что REASON_LABELS в ComparePage.
REASONS = {
    "unpriced_rows": "без цены",
    "not_finite_rows": "с ошибкой",
    "vat_base_unknown": "неизвестна база НДС",
    "display_rate_undefined": "ставка показа не определена",
}

E = html.escape


# ---------------------------------------------------------------------------
#  Форматирование. Всё, что попадёт на экран строкой, считается ЗДЕСЬ, на
#  Decimal: в JS `Decimal` нет, и вторая копия формулы разошлась бы с первой.
#  Числом в payload уезжает только геометрия — высоты столбцов.
# ---------------------------------------------------------------------------

def spaced(v: D) -> str:
    return f"{v:,}".replace(",", " ")


def fmt_value(v, ind):
    """Значение: ₽/м² — целые рубли, сумма договора — миллиарды с двумя знаками."""
    if v is None:
        return None
    if ind == "sqm":
        return spaced(v.quantize(D("1")))
    return spaced((v / D(10) ** 9).quantize(D("0.01"))).replace(".", ",") + " млрд"


def fmt_tick(v, ind):
    if ind == "sqm":
        return spaced(v.quantize(D("1")))
    if v == 0:
        return "0"
    out = f"{(v / D(10) ** 9).quantize(D('0.01')):f}"
    # Хвостовые нули срезаются ТОЛЬКО после точки: у целого «10» срез дал бы «1».
    if "." in out:
        out = out.rstrip("0").rstrip(".")
    return out.replace(".", ",") + " млрд"


def pct(v):
    """Знак у нуля не ставится: «−0 %» читалось бы как снижение."""
    if v is None:
        return None
    r = v.quantize(D("1"))
    if r == 0:
        return "0 %"
    return f"{'+' if r > 0 else '−'}{abs(r)} %"


def tone(v):
    if v is None:
        return "flat"
    mag = abs(v.quantize(D("1")))
    if mag <= NEUTRAL_BAND:
        return "flat"
    return ("up-" if v > 0 else "dn-") + ("hi" if mag > HIGH_BAND else "lo")


def growth(coef):
    """Коэффициент → читаемый уровень: 1.1603 → «+16,0 %»."""
    p = ((coef - 1) * 100).quantize(D("0.1"))
    s = f"{abs(p):f}".replace(".", ",")
    return ("+" if p >= 0 else "−") + s + " %"


def nice_axis(vmax: D):
    """Верх оси и засечки. Ось всегда от НУЛЯ — иначе столбцы врут о разах.

    Число интервалов плавает от 4 до 6: при жёстких четырёх шаг 50 000 не
    подходит к максимуму 203 890, ближайший годный — 100 000, и верхняя
    половина полотна остаётся пустой. Шаг при этом остаётся «круглым».
    """
    if vmax <= 0:
        return D(1), [D(0)]
    mag = D(10) ** int(math.floor(math.log10(float(vmax)))) / 100
    for m in (D(1), D(2), D("2.5"), D(5), D(10), D(20), D(25), D(50), D(100),
              D(200), D(250), D(500), D(1000)):
        step = mag * m
        # Потолок считается ЯВНО. `-(-vmax // step)` здесь не работает:
        # у `Decimal` оператор `//` усекает К НУЛЮ, а не вниз, поэтому для
        # 205 879 при шаге 50 000 он давал 4 вместо 5 — верх оси оказывался
        # НИЖЕ самого высокого столбца, и столбец рисовался на 10 % короче
        # собственной подписи.
        n = int(vmax / step)
        if step * n < vmax:
            n += 1
        if 4 <= n <= 6:
            return step * n, [step * i for i in range(n + 1)]
    return vmax, [D(0), vmax]


# ---------------------------------------------------------------------------
#  Снятие данных со стенда
# ---------------------------------------------------------------------------

with Session(ENGINE) as db, localcontext() as ctx:
    ctx.prec = 34
    all_ids = [r[0] for r in db.execute(sa.text("SELECT id FROM contracts ORDER BY signed_date"))]

    #: Состояния приведения: номинал плюс «ряд × месяц».
    STATES = ["-"] + [f"{sid}@{m['v']}" for sid in SERIES_IDS for m in MONTHS]
    MONTH_BY_V = {m["v"]: m for m in MONTHS}

    def split(state):
        if state == "-":
            return None, None
        sid, mv = state.split("@")
        return int(sid), MONTH_BY_V[mv]["ym"]

    def ask(ids, vat, state):
        sid, ym = split(state)
        return cc.build_comparison(
            db, ids, vat_mode=vat,
            single_rate=SINGLE_RATE if vat == "single" else None,
            inflation_series_id=sid,
            target_month=ym,
        )

    base = ask(all_ids, "net", "-")

    # `rate_class_id` уезжает в адрес страницы, поэтому нужен НАСТОЯЩИЙ id
    # справочника, а не индекс чипа. Берётся со СНИМКА в договоре — там же,
    # где его берёт `_load_columns`.
    class_id_by_contract = dict(db.execute(sa.text(
        "SELECT id, rate_class_id FROM contracts")).all())

    # Колонки — ОТ СТАРЫХ К НОВЫМ. Сервер отдаёт `signed_date DESC` (спека
    # сравнения §2.1): у таблицы свежий договор слева, у диаграммы время идёт
    # слева направо. Разворот здесь, а не в JS, чтобы порядок был один на файл.
    cols = []
    for c in reversed(base["columns"]):
        sd = c["signed_date"]
        sd = dt.date.fromisoformat(sd) if isinstance(sd, str) else sd
        cols.append({
            "id": c["contract_id"],
            "no": c["contract_number"],
            "obj": c["object_title"],
            "contractor": c["contractor_title"],
            "signed": sd,
            "area": c["area_total_sp"],
            "rate": c["composition_caption"],
            "cls": c["rate_class_title"],
            "cid": class_id_by_contract[c["contract_id"]],
        })

    # Классы — в порядке первого появления по времени: это порядок столбцов,
    # и второй порядок для тех же сущностей читателю пришлось бы держать в уме.
    class_titles, class_ids = [], []
    for c in cols:
        if c["cls"] not in class_titles:
            class_titles.append(c["cls"])
            class_ids.append(c["cid"])
    for c in cols:
        c["ci"] = class_titles.index(c["cls"])

    # Все непустые подмножества классов. Ключ — индексы подряд: "0", "02".
    subsets = {}
    for size in range(1, len(class_titles) + 1):
        for combo in itertools.combinations(range(len(class_titles)), size):
            subsets["".join(map(str, combo))] = [c["id"] for c in cols if c["ci"] in combo]
    FULL = "".join(map(str, range(len(class_titles))))

    # Строка без единой суммы во ВСЕЙ выборке в таблицу не попадает: она заняла
    # бы место, ничего не показав. Фильтр может обнулить строку — это другое
    # состояние, и оно рисуется прочерками.
    keep = {r["code"] for r in base["rows"]
            if r["level"] == 1
            and any(c["total"]["net_per_sqm"] is not None for c in r["cells"])}
    rows_ref = [{"code": r["code"], "title": r["title"]}
                for r in base["rows"] if r["level"] == 1 and r["code"] in keep]

    # Состав использованных годов зависит от ЦЕЛИ, а не только от ряда: у января
    # 2025 покрытие другое, чем у августа 2026. Поэтому годы живут отдельной
    # картой по состоянию, а не полем ряда.
    series_meta, syears = [], {}
    for sid in SERIES_IDS:
        for m in MONTHS:
            state = f"{sid}@{m['v']}"
            infl = ask(all_ids, "net", state)["inflation"]
            syears[state] = [{"y": y["year"], "g": growth(y["coefficient"]),
                              "fc": y["is_forecast"], "src": y["source"]}
                             for y in infl["used_years"]]
            if not any(x["id"] == str(sid) for x in series_meta):
                series_meta.append({"id": str(sid), "name": infl["series_name"],
                                    "note": infl["series_note"]})

    # --- ЗНАЧЕНИЯ: зависят от режима НДС и ряда, НЕ от выборки ---------------
    vals, raw, caps, kf = {}, {}, {}, {}
    for vat, _ in VAT_MODES:
        for state in STATES:
            d = ask(all_ids, vat, state)
            key = f"{vat}|{state}"
            caps[key] = d["caption"]
            by_code = {}
            for r in d["rows"]:
                if r["level"] != 1 or r["code"] not in keep:
                    continue
                by_code[r["code"]] = {
                    str(c["contract_id"]): fmt_value(c["total"]["shown_per_sqm"], "sqm")
                    for c in r["cells"]}
            tot = {t["contract_id"]: t["total"] for t in d["totals"]}
            by_code["T"] = {str(cid): {
                "sqm": fmt_value(tot[cid]["shown_per_sqm"], "sqm"),
                "sum": fmt_value(tot[cid]["shown"], "sum"),
                "why": ", ".join(REASONS[x] for x in tot[cid]["incomplete_reasons"])
                       if tot[cid]["shown"] is None else None,
            } for cid in tot}
            vals[key] = by_code
            for ind, _t in INDICATORS:
                field = "shown_per_sqm" if ind == "sqm" else "shown"
                raw[f"{ind}|{key}"] = {
                    str(cid): (float(tot[cid][field]) if tot[cid][field] is not None else None)
                    for cid in tot}
            if state != "-":
                kf[key] = {str(c["contract_id"]): {
                    "k": str(c["inflation_coefficient"].quantize(D("1.0000"))).replace(".", ","),
                    "g": growth(c["inflation_coefficient"]),
                    "dn": c["inflation_coefficient"] < 1,
                } for c in d["columns"] if c.get("inflation_coefficient") is not None}

    # --- МЕДИАНЫ И ОТКЛОНЕНИЯ: по нетто, значит от режима показа НДС не
    #     зависят; от ряда и от ВЫБОРКИ зависят. Отсюда запрос на подмножество.
    stat, medraw = {}, {}
    for skey, ids in subsets.items():
        for state in STATES:
            d = ask(ids, "net", state)
            k = f"{state}|{skey}"
            out = {}
            for r in d["rows"]:
                if r["level"] != 1 or r["code"] not in keep:
                    continue
                out[r["code"]] = {
                    "m": fmt_value(r["medians"]["total"]["value"], "sqm"),
                    "c": {str(c["contract_id"]): {
                        "d": pct(c["total"]["deviation_pct"]),
                        "t": tone(c["total"]["deviation_pct"])} for c in r["cells"]},
                }
            tm = d["totals_medians"]["total"]
            out["T"] = {
                "m": fmt_value(tm["value"], "sqm"),
                "cc": tm["comparable_count"],
                "c": {str(t["contract_id"]): {
                    "d": pct(t["total"]["deviation_pct"]),
                    "t": tone(t["total"]["deviation_pct"])} for t in d["totals"]},
            }
            stat[k] = out
            medraw[k] = float(tm["value"]) if tm["value"] is not None else None

    # --- ВЕРХ ОСИ: единственное, что клиент делит. Считается сразу по
    #     номиналу И по выбранному ряду, чтобы «Привести» РАСТИЛО столбцы,
    #     а не переписывало подписи засечек.
    top = {}
    for ind, _ in INDICATORS:
        for vat, _ in VAT_MODES:
            for state in STATES:
                for skey, ids in subsets.items():
                    cur = f"{ind}|{vat}|{state}"
                    pool = []
                    for cid in ids:
                        for src in (f"{ind}|{vat}|-", cur):
                            v = raw[src][str(cid)]
                            if v is not None:
                                pool.append(D(str(v)))
                    if ind == "sqm" and vat == "net":
                        for s2 in {"-", state}:
                            mr = medraw[f"{s2}|{skey}"]
                            if mr is not None:
                                pool.append(D(str(mr)))
                    t, ticks = nice_axis(max(pool)) if pool else (D(1), [D(0)])
                    assert not pool or max(pool) <= t, (
                        f"верх оси {t} ниже максимума {max(pool)} у {cur}|{skey}")
                    top[f"{cur}|{skey}"] = {
                        "t": float(t),
                        "ticks": [{"p": float(x / t), "l": fmt_tick(x, ind)} for x in ticks],
                    }

PAYLOAD = json.dumps({
    "cols": [{
        "id": str(c["id"]), "no": c["no"], "obj": c["obj"], "co": c["contractor"],
        "signed": c["signed"].strftime("%d.%m.%Y"),
        "area": spaced(c["area"].quantize(D("1"))) if c["area"] else None,
        "rate": c["rate"], "cls": c["cls"], "ci": c["ci"],
    } for c in cols],
    "classes": [{"i": i, "t": t, "id": class_ids[i]} for i, t in enumerate(class_titles)],
    "rows": rows_ref,
    "series": series_meta,
    "vals": vals, "raw": raw, "stat": stat, "medraw": medraw, "top": top,
    "caps": caps, "kf": kf, "syears": syears,
    "months": [{"v": m["v"], "dat": m["dat"]} for m in MONTHS],
    "defMonth": DEFAULT_MONTH,
    "singleRate": str(SINGLE_RATE.quantize(D("1"))),
    "full": FULL,
}, ensure_ascii=False)


# ---------------------------------------------------------------------------
#  Разметка
# ---------------------------------------------------------------------------

def chart_columns():
    return "".join(
        f'<div class="col" data-col="{c["id"]}" tabindex="0" role="button" '
        f'aria-label="{E(c["no"])}"><div class="bar">'
        f'<span class="seg-base"></span>'
        f'<span class="seg-delta" hidden></span>'
        f'<span class="ntick" hidden></span>'
        f'<span class="vlab"></span></div>'
        f'<span class="nodata" hidden></span></div>'
        for c in cols)


def chart_labels():
    return "".join(
        f'<div class="xl" data-xl="{c["id"]}"><span class="xno">{E(c["no"])}</span>'
        f'<span class="xdt">{c["signed"].strftime("%d.%m.%Y")}</span>'
        f'<span class="xcls">{E(c["cls"])}</span>'
        f'<span class="xk" data-xk="{c["id"]}" hidden></span>'
        f'<span class="xdev" data-xdev="{c["id"]}" hidden></span></div>'
        for c in cols)


def table_head():
    # Шапка таблицы идёт от НОВЫХ к старым — так её отдаёт сервер и так работает
    # настоящий экран. Разворачивать её вслед за диаграммой нельзя: это была бы
    # правка существующего экрана ради нового блока.
    return "".join(
        f'<th class="chead" scope="col" data-ch="{c["id"]}"><span class="rh2">'
        f'<span class="cno">{E(c["no"])}</span>'
        f'<span class="cobj">{E(c["obj"])}</span>'
        f'<span class="ccls">{E(c["cls"])}</span>'
        f'<span class="csigned">подписан {c["signed"].strftime("%d.%m.%Y")}</span>'
        f'<span class="kf" data-kfc="{c["id"]}" hidden></span></span></th>'
        for c in reversed(cols))


def table_body():
    out = []
    for i, r in enumerate(rows_ref):
        cells = "".join(
            f'<td class="num" data-cd="{c["id"]}" data-ri="{i}">'
            f'<span class="pmv"></span></td>' for c in reversed(cols))
        out.append(
            f'<tr><th class="rowhead" scope="row"><span class="rh">'
            f'<span class="code">{E(r["code"] or "")}</span>'
            f'<span class="title">{E(r["title"])}</span></span></th>'
            f'<td class="num med" data-med="{i}"></td>{cells}</tr>')
    return "".join(out)


def class_chips():
    return "".join(
        f'<button type="button" class="chip" data-cls="{i}" aria-pressed="true">'
        f'{E(t)} <span class="cnt">{sum(1 for c in cols if c["ci"] == i)}</span></button>'
        for i, t in enumerate(class_titles))


CSS = """
:root {
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
}
@media (prefers-color-scheme:dark) {
  :root:not([data-theme="light"]) {
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
  }
}
:root[data-theme="dark"] {
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
}
* { box-sizing:border-box; }
[hidden] { display:none !important; }
body { margin:0; background:var(--page); color:var(--fg); font-family:var(--font-sans);
  font-size:14px; line-height:1.5; -webkit-font-smoothing:antialiased; }
.wrap { max-width:1280px; margin:0 auto; padding:28px 20px 72px; }
.mockbar { display:flex; flex-wrap:wrap; align-items:center; gap:10px; font-size:12px;
  color:var(--fg2); background:var(--neutral-soft); border:1px solid var(--neutral-bd);
  border-radius:9px; padding:9px 13px; margin-bottom:26px; }
.mockbar strong { color:var(--fg); font-weight:600; }
.mockbar code { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:11px; }
h1 { font-family:var(--font-serif); font-weight:600; font-size:34px; line-height:1.15;
  margin:0 0 8px; letter-spacing:-.01em; text-wrap:balance; }
.sub { color:var(--fg2); margin:0 0 30px; max-width:68ch; }
section { margin-bottom:38px; }
h2 { font-family:var(--font-serif); font-weight:600; font-size:22px; margin:0 0 5px;
  text-wrap:balance; }
.secsub { color:var(--fg2); font-size:13px; margin:0 0 14px; max-width:74ch; }
.card { background:var(--surface); border:1px solid var(--bd-subtle); border-radius:12px; }
.card-pad { padding:15px 17px; }
.controls { display:flex; flex-wrap:wrap; align-items:flex-end; gap:18px; }
.ctl { display:flex; flex-direction:column; gap:5px; }
.ctl-lbl { font-size:10.5px; letter-spacing:.07em; text-transform:uppercase;
  color:var(--fg3); font-weight:600; }
.seg-ctl { display:inline-flex; border:1px solid var(--bd); border-radius:8px; overflow:hidden; }
.seg-ctl button { all:unset; cursor:pointer; padding:6px 12px; font-size:12.5px; color:var(--fg2);
  background:var(--surface); }
.seg-ctl button + button { border-left:1px solid var(--bd-subtle); }
.seg-ctl button[aria-pressed="true"] { background:var(--action); color:var(--action-text);
  font-weight:600; }
.seg-ctl button:focus-visible { outline:2px solid var(--accent); outline-offset:-2px; }
.seg-ctl button:disabled { cursor:not-allowed; color:var(--fg4); background:var(--sunken); }
select, input[type=month] { font:inherit; font-size:12.5px; color:var(--fg);
  background:var(--surface); border:1px solid var(--bd); border-radius:8px; padding:6px 9px; }
select:disabled, input:disabled { color:var(--fg4); background:var(--sunken); }
.btn { all:unset; cursor:pointer; font-size:12.5px; padding:7px 13px; border-radius:8px;
  background:var(--action); color:var(--action-text); font-weight:600; }
.btn:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.chips { display:flex; flex-wrap:wrap; gap:7px; }
.chip { all:unset; cursor:pointer; font-size:12.5px; padding:5px 11px; border-radius:20px;
  border:1px solid var(--bd); color:var(--fg2); background:var(--surface); }
.chip .cnt { font-size:10.5px; color:var(--fg3); }
.chip[aria-pressed="true"] { background:var(--accent-soft); border-color:var(--accent-bd);
  color:var(--accent-text); font-weight:600; }
.chip[aria-pressed="true"] .cnt { color:var(--accent-text); }
.chip:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.group { margin:16px 0 0; border:1px solid var(--accent-bd); border-radius:10px;
  background:var(--accent-soft); padding:12px 15px 14px; }
.group legend { font-size:10.5px; letter-spacing:.07em; text-transform:uppercase;
  color:var(--accent-text); font-weight:700; padding:0 6px; }
.group.plain { background:var(--sunken); border-color:var(--bd-subtle); }
.group.plain legend { color:var(--fg3); }
.levels { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin-top:11px;
  font-size:11.5px; color:var(--accent-text); }
.lv-lbl { font-size:10px; letter-spacing:.07em; text-transform:uppercase; font-weight:700;
  color:var(--accent-text); opacity:.75; }
.yr { background:var(--surface); border:1px solid var(--accent-bd); border-radius:6px;
  padding:1px 7px; }
.yr b { font-weight:600; }
.fc { color:var(--warn-text); background:var(--warn-soft); border:1px solid var(--warn-bd);
  border-radius:5px; padding:0 5px; font-size:10px; }
.levels .meta { color:var(--fg3); }
.axisnote { font-size:12px; color:var(--fg2); margin:13px 0 0; }
.axisnote code { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:11.5px; color:var(--fg2); background:var(--sunken); border:1px solid var(--bd-subtle);
  border-radius:5px; padding:1px 5px; }
.hint { color:var(--fg3); }
.warnbox { font-size:12px; margin:13px 0 0; padding:9px 13px; border-radius:9px;
  background:var(--warn-soft); border:1px solid var(--warn-bd); color:var(--warn-text); }

/* --- диаграмма ------------------------------------------------------------ */
.charthead { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between;
  gap:12px; margin-bottom:10px; }
.charttitle { font-size:13px; font-weight:600; }
.charttitle span { font-weight:400; color:var(--fg3); }
/* Ширина столбца — НИЖНЯЯ граница, а не доля: на выборке из двадцати договоров
   доля превратила бы столбцы в волоски. Полотно после этого листается. */
.chart { position:relative; padding:6px 0 0 104px; --barcol:92px; --plot:330px; }
.unitlab { position:absolute; left:0; top:-13px; font-size:11px; color:var(--fg3);
  font-weight:600; }
/* Жёлоб оси — СНАРУЖИ прокрутки: засечки и подписи медиан остаются на месте,
   как закреплённая первая колонка таблицы (спека сравнения §6). */
.gutter { position:absolute; left:0; top:6px; width:96px; height:var(--plot);
  pointer-events:none; }
.gutter .t, .gutter .gm { position:absolute; right:0; transform:translateY(50%);
  white-space:nowrap; font-size:11px; color:var(--fg3); }
.gutter .gm { font-size:10px; padding:1px 6px; border-radius:6px;
  background:var(--accent-soft); border:1px solid var(--accent-bd);
  color:var(--accent-text); transform:translateY(50%); }
.gutter .gm.ghost { background:var(--sunken); border-color:var(--bd-subtle); color:var(--fg3); }
.plotwrap { overflow-x:auto; overflow-y:hidden; }
.inner { width:max-content; min-width:100%; }
.plot { position:relative; height:var(--plot); border-bottom:1.5px solid var(--bd); }
.glayer { position:absolute; inset:0; pointer-events:none; }
.gl { position:absolute; left:0; right:0; border-top:1px dashed var(--bd-subtle); }
.gl.zero { border-top:0; }
.medline { position:absolute; left:0; right:0; border-top:2px dashed var(--accent);
  transition:bottom .45s cubic-bezier(.4,0,.2,1); }
.medline.ghost { border-top:1px dotted var(--fg4); }
.cols { position:relative; height:100%; display:flex; align-items:flex-end;
  gap:clamp(8px,2.2vw,30px); padding:0 clamp(4px,1.4vw,20px); }
.col { position:relative; flex:1 0 var(--barcol); min-width:var(--barcol); height:100%;
  outline:none; }
.col:focus-visible .bar { outline:2px solid var(--accent); outline-offset:3px; }
.bar { position:absolute; bottom:0; left:50%; transform:translateX(-50%);
  width:min(78px,86%); height:100%; }
/* Сплошная часть заканчивается на ПРИВЕДЁННОМ значении при любом знаке: верх
   сплошного и есть сравниваемое число, поэтому линия медианы с ним в одних
   деньгах и при росте, и при снижении. */
.seg-base { position:absolute; bottom:0; left:0; right:0; background:var(--accent);
  border-radius:4px 4px 0 0; transition:height .45s cubic-bezier(.4,0,.2,1); }
/* Промежуток «номинал ↔ приведённое». При росте — шапка ВНУТРИ столбца, при
   снижении — призрак НАД ним. Штриховка одна: знак читается положением. */
.seg-delta { position:absolute; left:0; right:0; transition:bottom .45s, height .45s;
  background:repeating-linear-gradient(135deg, var(--accent-bd) 0 4px,
    var(--accent-soft) 4px 9px); }
.seg-delta.down { border:1px dashed var(--accent-bd); border-bottom:0;
  border-radius:4px 4px 0 0; }
/* Риска номинала — одним правилом в обе стороны. */
.ntick { position:absolute; left:-3px; right:-3px; height:0;
  border-top:2px solid var(--accent-text); transition:bottom .45s; }
.vlab { position:absolute; left:50%; transform:translateX(-50%); font-size:11.5px;
  font-weight:600; color:var(--fg); white-space:nowrap; transition:bottom .45s;
  text-shadow:0 0 3px var(--surface), 0 0 3px var(--surface), 0 0 3px var(--surface); }
.col:hover .seg-base { background:var(--accent-text); }
.nodata { position:absolute; bottom:6px; left:50%; transform:translateX(-50%);
  white-space:nowrap; font-size:11px; color:var(--fg4); border:1px dashed var(--bd);
  border-radius:7px; padding:2px 8px; background:var(--sunken); }
.xlabs { display:flex; gap:clamp(8px,2.2vw,30px); padding:9px clamp(4px,1.4vw,20px) 0; }
.xl { flex:1 0 var(--barcol); min-width:var(--barcol); display:flex; flex-direction:column;
  align-items:center; gap:2px; text-align:center; }
.xno { font-size:12px; font-weight:600; color:var(--fg); }
.xdt, .xcls { font-size:11px; color:var(--fg3); }
.xk { font-size:10.5px; white-space:nowrap; color:var(--accent-text);
  background:var(--accent-soft); border:1px solid var(--accent-bd);
  border-radius:5px; padding:0 6px; }
.xk.down { background:var(--dn-lo); border-color:var(--dn-hi); color:var(--dn-fg); }
.xdev { font-size:11px; white-space:nowrap; border-radius:6px; padding:0 7px;
  border:1px solid transparent; }
.xdev.flat { color:var(--fg3); }
.xdev.up-lo { background:var(--up-lo); color:var(--up-fg); }
.xdev.up-hi { background:var(--up-hi); color:var(--up-fg); font-weight:600; }
.xdev.dn-lo { background:var(--dn-lo); color:var(--dn-fg); }
.xdev.dn-hi { background:var(--dn-hi); color:var(--dn-fg); font-weight:600; }
.legend { display:flex; flex-wrap:wrap; gap:16px; align-items:center; margin:16px 0 0;
  font-size:12px; color:var(--fg2); }
.legend .li { display:inline-flex; align-items:center; gap:7px; }
.sw { width:14px; height:14px; border-radius:3px; display:inline-block; }
.sw.base { background:var(--accent); }
.sw.up { border:1px solid var(--accent-bd);
  background:repeating-linear-gradient(135deg, var(--accent-bd) 0 4px,
    var(--accent-soft) 4px 9px); }
.sw.nom { height:0; width:18px; border-top:2px solid var(--accent-text); border-radius:0; }
.sw.med { height:0; width:18px; border-top:2px dashed var(--accent); border-radius:0; }
.tip { position:absolute; z-index:5; min-width:216px; background:var(--surface);
  border:1px solid var(--bd); border-radius:10px; padding:9px 11px; font-size:12px;
  box-shadow:0 8px 22px -12px rgba(0,0,0,.45); pointer-events:none; transform:translateX(-50%); }
.tip .tt { font-weight:600; margin-bottom:1px; }
.tip .ts { color:var(--fg3); font-size:11px; margin-bottom:6px; }
.tip dl { display:grid; grid-template-columns:auto auto; gap:2px 14px; margin:0; }
.tip dt { color:var(--fg2); }
.tip dd { margin:0; text-align:right; font-variant-numeric:tabular-nums; }
.medcap { display:none; }

/* --- таблица сравнения ---------------------------------------------------- */
.scroller { overflow-x:auto; border:1px solid var(--bd-subtle); border-radius:12px;
  background:var(--surface); }
table.cmp { border-collapse:separate; border-spacing:0; width:100%; font-size:13px; }
table.cmp th, table.cmp td { border-bottom:1px solid var(--bd-subtle); padding:8px 11px;
  text-align:left; vertical-align:baseline; }
table.cmp thead th { background:var(--sechead); font-weight:600; vertical-align:bottom; }
th.rowhead { position:sticky; left:0; background:var(--surface); z-index:2;
  box-shadow:var(--shadow-sticky); min-width:230px; }
thead th.rowhead { background:var(--sechead); z-index:3; font-size:10.5px;
  letter-spacing:.06em; text-transform:uppercase; color:var(--fg3); }
.rh { display:flex; flex-direction:column; gap:1px; }
.rh .code { font-size:10.5px; color:var(--fg3); font-variant-numeric:tabular-nums; }
.rh .title { font-size:12.5px; }
.rh2 { display:flex; flex-direction:column; gap:1px; min-width:132px; }
.cno { font-size:12.5px; font-weight:600; }
.cobj, .ccls, .csigned { font-size:10.5px; color:var(--fg3); font-weight:400; }
.ccls { color:var(--accent-text); }
.kf { align-self:flex-start; font-size:10px; color:var(--accent-text);
  background:var(--accent-soft); border:1px solid var(--accent-bd); border-radius:5px;
  padding:0 5px; margin-top:2px; }
table.cmp td.num, table.cmp th.num { text-align:right; font-variant-numeric:tabular-nums;
  white-space:nowrap; }
td.med, th.med { background:var(--sunken); font-weight:600; }
.pmv { display:inline-flex; align-items:baseline; gap:7px; justify-content:flex-end; }
.dev { font-size:10.5px; border-radius:5px; padding:0 5px; }
.dev.flat { color:var(--fg3); }
.dev.up-lo { background:var(--up-lo); color:var(--up-fg); }
.dev.up-hi { background:var(--up-hi); color:var(--up-fg); font-weight:600; }
.dev.dn-lo { background:var(--dn-lo); color:var(--dn-fg); }
.dev.dn-hi { background:var(--dn-hi); color:var(--dn-fg); font-weight:600; }
.dash { color:var(--fg4); }

ul.notes { margin:0; padding-left:19px; color:var(--fg2); font-size:13px; }
ul.notes li { margin-bottom:9px; }
ul.notes li:last-child { margin-bottom:0; }
ul.notes b { color:var(--fg); }
ul.notes code { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:11.5px; background:var(--sunken); border:1px solid var(--bd-subtle);
  border-radius:5px; padding:1px 5px; }
.bound { border-left:3px solid var(--info); background:var(--info-soft);
  border-radius:0 8px 8px 0; padding:9px 13px; }
.pill { display:inline-block; font-size:10.5px; border-radius:5px; padding:1px 7px;
  border:1px solid var(--info-bd); background:var(--info-soft); color:var(--info); }

@media (max-width:860px) {
  .chart { padding-left:64px; }
  .gutter { width:56px; }
  .gutter .gm { display:none; }
  .medcap { display:block; }
  .gutter .t, .xk, .xdev, .xdt, .xcls { font-size:10px; }
  .xno { font-size:11px; }
  .vlab { font-size:10.5px; }
  .cols, .xlabs { gap:6px; }
  .chart { --barcol:78px; }
}
"""

JS = r"""
const D = __PAYLOAD__;
const colById = Object.fromEntries(D.cols.map(c => [c.id, c]));
const MONTHS = Object.fromEntries(D.months.map(m => [m.v, m.dat]));

let ind = 'sqm', vat = 'net', sid = '', month = '', on = false;
const picked = new Set(D.classes.map(c => c.i));

const subset = () => [...picked].sort().join('');
// Состояние приведения: `-` либо `<ряд>@<месяц>`.
const state = () => (on && sid && month) ? sid + '@' + month : '-';
const valKey = () => vat + '|' + state();
const nomValKey = () => vat + '|-';
const statKey = () => state() + '|' + subset();
const nomKey = () => '-|' + subset();
const seriesById = (id) => D.series.find(s => s.id === id) || null;
const visible = () => D.cols.filter(c => picked.has(c.ci));

const plot = document.getElementById('plot');
const grid = document.getElementById('grid');
const gutter = document.getElementById('gutter');
const medA = document.getElementById('medA');
const medN = document.getElementById('medN');
const legend = document.getElementById('legend');
const tip = document.getElementById('tip');
const sel = document.getElementById('seriesSel');
const monthInp = document.getElementById('monthInp');
const monthNote = document.getElementById('monthNote');
const onBtn = document.getElementById('onBtn');
const offBtn = document.getElementById('offBtn');

function press(group, value) {
  document.querySelectorAll('[data-' + group + ']').forEach(b =>
    b.setAttribute('aria-pressed', String(b.dataset[group] === value)));
}

function render() {
  const s = seriesById(sel.value);
  const live = Boolean(on && s && month);
  const vk = valKey(), nvk = nomValKey();
  const st = D.stat[statKey()], stN = D.stat[nomKey()];
  // Ось — по ФАКТИЧЕСКИ ПРИМЕНЁННОМУ состоянию. Прежняя редакция считала её по
  // состоянию, «которое наступит», чтобы «Привести» растило столбцы, а не
  // переписывало засечки. Требование снято: у настоящего клиента приведённых
  // чисел до запроса нет, а сравнение «до/после» теперь живёт ВНУТРИ столбца —
  // риска номинала и штрихованный промежуток от масштаба не зависят.
  const axis = D.top[ind + '|' + vat + '|' + state() + '|' + subset()];
  const shown = new Set(visible().map(c => c.id));
  const dat = MONTHS[month] || '';
  const pct = (x) => (x / axis.t * 100).toFixed(4) + '%';

  // --- ось: линии внутри полотна, подписи — в жёлобе СНАРУЖИ прокрутки
  grid.innerHTML = axis.ticks.map(t =>
    '<div class="gl' + (t.p === 0 ? ' zero' : '') + '" style="bottom:' +
    (t.p * 100).toFixed(4) + '%"></div>').join('');
  const gparts = axis.ticks.map(t =>
    '<span class="t" style="bottom:' + (t.p * 100).toFixed(4) + '%">' + t.l + '</span>');
  document.getElementById('unitlab').textContent = ind === 'sqm' ? '₽/м²' : '₽';

  // --- медиана: только там, где ось и медиана в ОДНИХ деньгах
  const medOk = ind === 'sqm' && vat === 'net';
  const mv = medOk ? D.medraw[statKey()] : null;
  const mn = medOk ? D.medraw[nomKey()] : null;
  const line = (el, raw) => {
    if (raw === null || raw === undefined) { el.hidden = true; return false; }
    el.hidden = false;
    el.style.bottom = pct(raw);
    return true;
  };
  if (line(medA, mv))
    gparts.push('<span class="gm" style="bottom:' + pct(mv) + '">медиана ' + st.T.m + '</span>');
  if (line(medN, live ? mn : null))
    gparts.push('<span class="gm ghost" style="bottom:' + pct(mn) + '">номинал ' +
      stN.T.m + '</span>');
  gutter.innerHTML = gparts.join('');

  // --- столбцы
  D.cols.forEach(c => {
    const col = plot.querySelector('[data-col="' + c.id + '"]');
    const xl = document.querySelector('[data-xl="' + c.id + '"]');
    const vis = shown.has(c.id);
    col.hidden = !vis; xl.hidden = !vis;
    if (!vis) return;
    const bar = col.querySelector('.bar'), nd = col.querySelector('.nodata');
    const base = col.querySelector('.seg-base');
    const delta = col.querySelector('.seg-delta');
    const ntick = col.querySelector('.ntick');
    const vlab = col.querySelector('.vlab');
    const nomRaw = D.raw[ind + '|' + nvk][c.id];
    const curRaw = live ? D.raw[ind + '|' + vk][c.id] : nomRaw;
    const val = (live ? D.vals[vk] : D.vals[nvk]).T[c.id][ind];
    if (val === null) {
      bar.hidden = true; nd.hidden = false;
      delta.hidden = true; ntick.hidden = true;
      const why = D.vals[nvk].T[c.id].why;
      nd.textContent = why ? 'нет суммы: ' + why : 'нет суммы';
    } else {
      bar.hidden = false; nd.hidden = true;
      // Сплошная часть — ВСЕГДА приведённое значение: верх сплошного и есть
      // сравниваемое число, поэтому медиана с ним в одних деньгах при любом знаке.
      base.style.height = pct(curRaw);
      vlab.style.bottom = 'calc(' + pct(curRaw) + ' + 5px)';
      vlab.textContent = val;
      const gap = Math.abs(curRaw - nomRaw);
      // Промежуток «номинал ↔ приведённое»: при росте шапка ВНУТРИ столбца, при
      // снижении призрак НАД ним. Порога минимальной высоты НЕТ: сигнал
      // «приведение сработало» несёт риска номинала, а порог заставлял призрак
      // перерастать саму риску — то есть врать о номинале ради заметности.
      delta.hidden = !(live && gap > 0);
      if (!delta.hidden) {
        delta.classList.toggle('down', curRaw < nomRaw);
        delta.style.bottom = pct(Math.min(curRaw, nomRaw));
        delta.style.height = pct(gap);
      }
      ntick.hidden = !live;
      if (live) ntick.style.bottom = pct(nomRaw);
    }
    const k = live ? (D.kf[vk] || {})[c.id] : null;
    const xk = document.querySelector('[data-xk="' + c.id + '"]');
    xk.hidden = !k;
    if (k) { xk.textContent = 'поправка ' + k.g; xk.className = 'xk' + (k.dn ? ' down' : ''); }
    const dv = st.T.c[c.id];
    const xd = document.querySelector('[data-xdev="' + c.id + '"]');
    xd.hidden = !dv || dv.d === null;
    if (dv && dv.d !== null) {
      xd.textContent = 'к медиане ' + dv.d;
      xd.className = 'xdev ' + dv.t;
    }
  });

  // --- легенда: при приведении метки три, и знак объяснён словами
  const items = [];
  if (live) {
    items.push('<span class="li"><span class="sw base"></span>приведено к ' + dat +
      ' по ряду «' + s.name + '»</span>');
    items.push('<span class="li"><span class="sw up"></span>промежуток к номиналу: ' +
      'шапка — рост, призрак над столбцом — снижение</span>');
    items.push('<span class="li"><span class="sw nom"></span>номинал: цены подписания</span>');
  }
  if (mv !== null && mv !== undefined)
    items.push('<span class="li"><span class="sw med"></span>медиана выборки (нетто, ₽/м²)</span>');
  legend.innerHTML = items.join('');
  legend.hidden = items.length === 0;

  // Дубль подписей медиан для узкого экрана: там жёлоб слишком узок для плашек.
  const medcap = document.getElementById('medcap');
  medcap.hidden = mv === null || mv === undefined;
  if (!medcap.hidden) {
    medcap.textContent = 'Медиана выборки — ' + st.T.m + ' ₽/м²' +
      (live && stN.T.m ? '; в номинале — ' + stN.T.m : '');
  }

  // --- подписи под диаграммой
  document.getElementById('caption').textContent = D.caps[vk];
  const note = document.getElementById('mednote');
  if (ind === 'sum') {
    note.hidden = false;
    note.innerHTML = 'Линии медианы нет: медиана выборки существует только на оси ' +
      '<b>₽/м²</b>. Столбцы сумм сравнивают <b>объём договора</b>, а не уровень цены.';
  } else if (vat !== 'net') {
    note.hidden = false;
    note.innerHTML = 'Линии медианы нет: медиана считается по <b>нетто</b>, а столбцы ' +
      'показаны в валовых суммах. Отклонения под столбцами — по-прежнему ' +
      'нетто-отклонения, они от режима показа не зависят.';
  } else note.hidden = true;

  // --- выборка меньше трёх сопоставимых: медианы у сервера нет вовсе
  const thin = document.getElementById('thin');
  thin.hidden = st.T.cc >= 3;
  if (!thin.hidden) {
    thin.textContent = 'Сопоставимых договоров в выборке ' + st.T.cc +
      ' — медианы нет: сервер считает её при трёх и более. Отклонений нет ни на ' +
      'диаграмме, ни в таблице; суммы показаны как есть.';
  }

  // --- таблица
  D.cols.forEach(c => {
    const vis = shown.has(c.id);
    document.querySelector('[data-ch="' + c.id + '"]').hidden = !vis;
    document.querySelectorAll('[data-cd="' + c.id + '"]').forEach(td => { td.hidden = !vis; });
    const kfc = document.querySelector('[data-kfc="' + c.id + '"]');
    const k = live ? (D.kf[vk] || {})[c.id] : null;
    kfc.hidden = !k || !vis;
    if (k) { kfc.textContent = k.g; kfc.title = 'множитель × ' + k.k; }
  });
  D.rows.forEach((r, i) => {
    const row = st[r.code];
    const mcell = document.querySelector('[data-med="' + i + '"]');
    mcell.innerHTML = row && row.m ? row.m : '<span class="dash">—</span>';
    D.cols.forEach(c => {
      const td = document.querySelector('[data-cd="' + c.id + '"][data-ri="' + i + '"]');
      const v = ((live ? D.vals[vk] : D.vals[nvk])[r.code] || {})[c.id];
      const dv = row ? row.c[c.id] : null;
      const span = td.querySelector('.pmv');
      if (v === null || v === undefined) {
        span.innerHTML = '<span class="dash">—</span>';
        return;
      }
      span.innerHTML = v + (dv && dv.d !== null
        ? ' <span class="dev ' + dv.t + '">' + dv.d + '</span>' : '');
    });
  });

  // --- полоса уровней ряда: состав ГОДОВ зависит от цели, поэтому полоса
  // появляется вместе с ответом, то есть при включённом приведении.
  const box = document.getElementById('levels');
  const years = live ? D.syears[sid + '@' + month] : null;
  if (years) {
    box.hidden = false;
    box.innerHTML = '<span class="lv-lbl">Ряд по годам</span>' +
      years.map(y => '<span class="yr">' + y.y + ' <b>' + y.g + '</b>' +
        (y.fc ? ' <span class="fc">прогноз</span>' : '') + '</span>').join('') +
      '<span class="meta">' + (s.note || '') + '</span>';
  } else { box.hidden = true; box.innerHTML = ''; }

  // Ставка показа имеет смысл только в режиме «Единая» — как на экране.
  document.getElementById('rateSel').disabled = vat !== 'single';
  onBtn.disabled = sel.value === '';
  monthInp.disabled = sel.value === '';
  onBtn.setAttribute('aria-pressed', String(on));
  offBtn.setAttribute('aria-pressed', String(!on));

  // --- адрес: `ids` НЕ переписывается. Класс сужает выборку своим параметром,
  // и он же возвращает снятый договор после перезагрузки.
  const params = ['ids=' + D.cols.map(c => c.id).reverse().join(','), 'vat_mode=' + vat];
  if (vat === 'single') params.push('single_rate=' + D.singleRate);
  if (picked.size < D.classes.length) {
    // Порядок id — возрастающий, а не порядок чипов: одна и та же выборка
    // обязана давать один и тот же адрес, иначе ссылка не воспроизводима.
    params.push('rate_class_id=' + D.classes.filter(c => picked.has(c.i))
      .map(c => c.id).sort((x, y) => x - y).join(','));
  }
  if (live) {
    params.push('inflation_series_id=' + sel.value);
    params.push('target_month=' + month);
  }
  document.getElementById('urlbar').textContent = '/compare?' + params.join('&');
}

// --- подсказка столбца
function showTip(cid) {
  const s = seriesById(sel.value), live = Boolean(on && s && month);
  const vk = valKey(), nvk = nomValKey();
  const st = D.stat[statKey()], c = colById[cid];
  const nom = D.vals[nvk].T[cid], cur = D.vals[vk].T[cid];
  const u = ind === 'sqm' ? ' ₽/м²' : '';
  const k = live ? (D.kf[vk] || {})[cid] : null;
  const rows = [['Класс объекта', c.cls],
                ['В ценах подписания', nom[ind] === null ? '—' : nom[ind] + u]];
  if (k) {
    rows.push(['Коэффициент', '× ' + k.k + ' (' + k.g + ')']);
    rows.push([k.dn ? 'Приведено, снижение' : 'Приведено, рост',
               cur[ind] === null ? '—' : cur[ind] + u]);
  }
  const dv = st.T.c[cid];
  rows.push(['Отклонение от медианы', dv && dv.d !== null ? dv.d : '—']);
  rows.push(['Ставка НДС договора', c.rate]);
  if (c.area) rows.push(['Площадь', c.area + ' м²']);
  tip.innerHTML = '<div class="tt">' + c.no + '</div><div class="ts">' + c.obj +
    ' · ' + c.co + ' · подписан ' + c.signed + '</div><dl>' +
    rows.map(r => '<dt>' + r[0] + '</dt><dd>' + r[1] + '</dd>').join('') + '</dl>';
  const col = plot.querySelector('[data-col="' + cid + '"]');
  // Снять `hidden` ДО измерения: у скрытого элемента `offsetParent` === null.
  tip.hidden = false;
  const anchor = document.querySelector('.chart');
  // Якорь — ВЕРХ СПЛОШНОЙ части, а не колонки: колонка высотой во всё полотно,
  // и подсказка уезжала над карточкой, где её обрезало.
  const base = col.querySelector('.seg-base');
  const cb = col.getBoundingClientRect(), mb = base.getBoundingClientRect();
  const pb = anchor.getBoundingClientRect(), half = tip.offsetWidth / 2;
  const want = cb.left - pb.left + cb.width / 2;
  tip.style.left = Math.min(Math.max(want, half + 2), pb.width - half - 2) + 'px';
  const above = mb.top - pb.top - tip.offsetHeight - 10;
  tip.style.top = (above >= 0 ? above : mb.top - pb.top + 12) + 'px';
}

plot.querySelectorAll('.col').forEach(col => {
  const cid = col.dataset.col;
  col.addEventListener('mouseenter', () => showTip(cid));
  col.addEventListener('focus', () => showTip(cid));
  col.addEventListener('mouseleave', () => { tip.hidden = true; });
  col.addEventListener('blur', () => { tip.hidden = true; });
});

document.querySelectorAll('[data-ind]').forEach(b =>
  b.addEventListener('click', () => { ind = b.dataset.ind; press('ind', ind); render(); }));
document.querySelectorAll('[data-vat]').forEach(b =>
  b.addEventListener('click', () => { vat = b.dataset.vat; press('vat', vat); render(); }));
document.querySelectorAll('[data-cls]').forEach(b =>
  b.addEventListener('click', () => {
    const i = +b.dataset.cls;
    // Снять последний класс нельзя: выборка из нуля договоров — не состояние
    // экрана, а отсутствие запроса.
    if (picked.has(i) && picked.size === 1) return;
    if (picked.has(i)) picked.delete(i); else picked.add(i);
    b.setAttribute('aria-pressed', String(picked.has(i)));
    tip.hidden = true;
    render();
  }));
sel.addEventListener('change', () => {
  // Выбор ряда сам числа НЕ меняет — только открывает «Привести» и ось.
  sid = sel.value;
  if (!sid) { on = false; month = ''; monthInp.value = ''; monthNote.hidden = true; }
  render();
});
onBtn.addEventListener('click', () => {
  if (!sel.value) return;
  on = true;
  // Сервер разрешил текущий месяц и вернул его; клиент записывает в поле и URL.
  if (!month) month = D.defMonth;
  monthInp.value = month;
  render();
});
offBtn.addEventListener('click', () => {
  on = false; month = ''; monthInp.value = ''; monthNote.hidden = true; render();
});
monthInp.addEventListener('change', () => {
  // В МАКЕТЕ сосчитаны два месяца, а не любой. Молча откатить поле нельзя:
  // человек увидел бы, что его ввод исчез, и не понял бы почему.
  if (!MONTHS[monthInp.value]) {
    monthNote.hidden = false;
    monthNote.textContent = 'В макете сосчитаны два месяца: 2026-08 — цель позже ' +
      'всех смет, растёт всё; 2025-01 — цель между датами подписания, знаки разные. ' +
      'Настоящий экран принимает любой месяц, поле возвращено к сосчитанному.';
    monthInp.value = month;
    return;
  }
  monthNote.hidden = true;
  month = monthInp.value;
  if (on) render();
});

press('ind', ind); press('vat', vat);
render();
"""


CONTENT = f"""<title>Диаграмма стоимости договоров</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@600&family=Inter:wght@400;600;700&display=swap">
<style>{CSS}</style>

<div class="wrap">

  <div class="mockbar">
    <strong>Макет.</strong>
    <span>Экран <code>/compare</code> целиком. Числа — стенд <code>gca_dev</code> на
    {STAND_DATE}, тем же <code>build_comparison</code>, что кормит настоящую
    страницу.</span>
    <span class="pill">иллюстративного нет ничего</span>
  </div>

  <h1>Сравнение договоров</h1>
  <p class="sub">Диаграмма стоит <b>над таблицей статей</b> и слушается тех же
  переключателей — плюс нового фильтра по классу объекта. Столбцы идут от
  старых договоров к новым; таблица остаётся в своём порядке, от новых к старым,
  как её отдаёт сервер.</p>

  <section>
    <h2>Панель управления</h2>
    <p class="secsub">Фильтр по классу объекта — единственный новый элемент.
    Он не прячет столбцы на клиенте: класс задаёт <b>другую выборку</b>, а
    медиана и отклонения считаются по выборке и обязаны пересчитаться
    сервером.</p>
    <div class="card card-pad">
      <div class="controls">
        <span class="ctl"><span class="ctl-lbl">Показатель</span>
          <span class="seg-ctl" role="group" aria-label="Показатель">
            <button type="button" aria-pressed="true">Итого</button>
            <button type="button" aria-pressed="false" disabled>ДГП</button>
            <button type="button" aria-pressed="false" disabled>ДС</button>
          </span></span>
        <span class="ctl"><span class="ctl-lbl">НДС</span>
          <span class="seg-ctl" role="group" aria-label="Режим НДС">
            {"".join(f'<button type="button" data-vat="{k}" aria-pressed="false">{E(t)}</button>' for k, t in VAT_MODES)}
          </span></span>
        <span class="ctl"><span class="ctl-lbl">Единая ставка</span>
          <select id="rateSel" disabled><option>{SINGLE_RATE} %</option></select></span>
        <button class="btn" type="button" style="margin-left:auto">Выгрузить в Excel</button>
      </div>

      <fieldset class="group plain">
        <legend>Класс объекта — новый фильтр</legend>
        <div class="chips">{class_chips()}</div>
      </fieldset>

      <fieldset class="group">
        <legend>Поправка на инфляцию</legend>
        <div class="controls">
          <span class="ctl"><span class="ctl-lbl">Режим</span>
            <span class="seg-ctl" role="group" aria-label="Приведение">
              <button type="button" id="offBtn" aria-pressed="true">Номинал</button>
              <button type="button" id="onBtn" aria-pressed="false" disabled>Привести</button>
            </span></span>
          <span class="ctl"><span class="ctl-lbl">Ряд индексов</span>
            <select id="seriesSel">
              <option value="">Выберите ряд</option>
              {"".join(f'<option value="{s["id"]}">{E(s["name"])}</option>' for s in series_meta)}
            </select></span>
          <span class="ctl"><span class="ctl-lbl">В ценах</span>
            <input type="month" id="monthInp" value="" disabled></span>
        </div>
        <div id="levels" class="levels" hidden></div>
        <p class="axisnote" id="monthNote" hidden></p>
      </fieldset>

      <p class="axisnote"><span class="hint">Адрес страницы:</span>
      <code id="urlbar"></code></p>
      <p class="axisnote">Корзины <b>ДГП</b> и <b>ДС</b> на стенде неразличимы —
      допсоглашений ноль, «Итого» совпадает с ДГП, — поэтому переключатель
      показан неактивным: рисовать выбор, который ничего не меняет, значит
      обещать разницу, которой в этих данных нет.</p>
    </div>
  </section>

  <section>
    <h2>Диаграмма</h2>
    <p class="secsub">«Итого по договору», столбцы от старых к новым. Диаграмма
    ничего своего не запрашивает: <code>totals</code>, <code>totals_medians</code>
    и коэффициент колонки ответ сравнения уже несёт.</p>
    <div class="card card-pad">
      <div class="charthead">
        <span class="charttitle">Итого по договору
          <span>— в порядке подписания</span></span>
        <span class="ctl" style="flex-direction:row; align-items:center; gap:8px">
          <span class="ctl-lbl">Единица диаграммы</span>
          <span class="seg-ctl" role="group" aria-label="Единица диаграммы">
            {"".join(f'<button type="button" data-ind="{k}" aria-pressed="false">{E(t)}</button>' for k, t in INDICATORS)}
          </span></span>
      </div>
      <div class="chart">
        <span class="unitlab" id="unitlab"></span>
        <div class="gutter" id="gutter"></div>
        <div class="plotwrap">
          <div class="inner">
            <div class="plot" id="plot">
              <div class="glayer">
                <div id="grid"></div>
                <div class="medline ghost" id="medN" hidden></div>
                <div class="medline" id="medA" hidden></div>
              </div>
              <div class="cols">{chart_columns()}</div>
            </div>
            <div class="xlabs">{chart_labels()}</div>
          </div>
        </div>
        <div class="legend" id="legend"></div>
        <p class="axisnote medcap" id="medcap" hidden></p>
        <div class="tip" id="tip" hidden></div>
      </div>
      <p class="axisnote" id="caption"></p>
      <p class="axisnote" id="mednote" hidden></p>
      <p class="warnbox" id="thin" hidden></p>
    </div>
  </section>

  <section>
    <h2>Таблица статей</h2>
    <p class="secsub">Та же таблица, что и сегодня: корневые статьи
    классификатора, медиана по нетто, отклонение в каждой ячейке. Фильтр по
    классам убирает колонку целиком — и медиана строки пересчитывается.</p>
    <div class="scroller">
      <table class="cmp">
        <thead>
          <tr>
            <th class="rowhead" scope="col">Статья классификатора</th>
            <th class="num med" scope="col">Медиана</th>
            {table_head()}
          </tr>
        </thead>
        <tbody>{table_body()}</tbody>
      </table>
    </div>
    <p class="axisnote">Показан только первый уровень дерева — на стенде статей
    трёх уровней 253, и макет остался бы нечитаемым.</p>
  </section>

  <section>
    <h2>Что видно на этих числах</h2>
    <p class="secsub">Стенд оказался удачным: каждый спорный случай виден
    буквально, а не в пересказе.</p>
    <div class="card card-pad">
      <ul class="notes">
        <li><b>Фильтр объясняет главный выброс.</b> «Д-2026-27002» дороже медианы
        на 57 % — и это «Жилые&nbsp;— люкс» против трёх «бизнесов». Снимите «люкс» —
        и разброс схлопывается: вместо «от −15 % до +57 %» остаётся «от −4 % до
        +25 %». Оставьте только «люкс» — и медианы не станет вовсе: сопоставимых
        договоров меньше трёх.
        Ровно за этим фильтр и нужен: он не украшает выборку, а делает её
        сравнимой.</li>
        <li><b>ВЕР1-ГП меняет знак.</b> В номинале договор июля 2024 года дешевле
        медианы на 11 %. Приведённый по Росстату — дешевле на 4 %. Приведённый по
        «Фактической инфляции» — уже <b>дороже</b> на 7 %. Один договор по разные
        стороны медианы в зависимости от ряда; на столбцах это видно сразу, а в
        таблице пришлось бы сличать четыре числа в разных колонках.</li>
        <li><b>Медиана едет вместе со столбцами.</b> Приведение поднимает все
        суммы, поэтому отклонения растут не так, как высота столбцов. При
        включённом приведении показаны <b>обе</b> линии — новая и точечная
        «медиана в номинале»: без второй рост столбцов читался бы как рост
        отклонений.</li>
        <li><b>Разница ставок видна только в валовых режимах.</b>
        «Д-2026-27002» и «12-СИТ-МР» подписаны по 22 %, три остальных — по 20 %.
        В «Без НДС» столбцы сравнимы по построению; в «Своей ставке» два правых
        подрастают, и это не подорожание, а налог. Поэтому подпись состава стоит
        под диаграммой всегда.</li>
        <li><b>ДГП-3С10 не исчезает.</b> У договора мая 2024 года смета есть, а
        цен в строках нет. Столбца нет, но место договора в ряду остаётся, с
        причиной «без цены». Выкинуть его молча значило бы показать выборку из
        пяти договоров как выборку из четырёх.</li>
      </ul>
    </div>
  </section>

  <section>
    <h2>Решения, которые макет предлагает</h2>
    <p class="secsub">Каждое — развилка, где можно было сделать иначе. Здесь то,
    что выбрано, и причина.</p>
    <div class="card card-pad">
      <ul class="notes">
        <li><b>Класс сужает выборку своим параметром, а <code>ids</code> не
        переписывает.</b> Смотрите адрес: <code>ids</code> держит всю выборку, а
        снятые классы уезжают в <code>rate_class_id</code> списком. Переписанный
        <code>ids</code> был бы дешевле в реализации и неверен по сути: после
        перезагрузки снятый договор исчез бы из ссылки насовсем, и чип не смог бы
        его вернуть — помнить было бы нечему. Цена решения названа честно: сегодня
        <code>rate_class_id</code> одиночный и живёт только в форме
        <code>all=1</code>, а формы <code>ids</code> и «фильтр» объявлены
        взаимоисключающими (400). Значит фича требует <b>осознанной ревизии</b>:
        класс перестаёт быть формой выборки и становится сужением любой из них —
        правки в двух роутерах, <code>resolve_selection</code> и
        <code>filtered_contract_ids</code>.</li>
        <li><b>Ответ должен нести номинал и приведённое ОДНОВРЕМЕННО — иначе
        диаграмму не нарисовать.</b> Сегодня приведение накладывается в
        <code>load_rollups</code> ещё до агрегации, и в ответе остаются ТОЛЬКО
        приведённые числа. Восстановить номинал у клиента нельзя: делить на
        коэффициент колонки — это вторая копия денежной формулы в JS, а у «Итого»
        коэффициент вообще может быть неопределён, когда ДГП и ДС индексированы
        по-разному (<code>inflation_coefficient: null</code> плюс отдельная
        разбивка). Значит при включённом приведении ячейки <code>totals</code>
        обязаны нести вложенный <code>nominal</code> — по всем трём корзинам,
        потому что диаграмма следует переключателю корзины. Второй запрос не
        нужен. Отдельным блоком «для диаграммы» это делать нельзя: те же числа
        живут в итоговой строке, а факт живёт в одном месте. Медиана номинала
        нужна тем же порядком — иначе точечную линию рисовать не из чего.</li>
        <li><b>Фильтру нужен серверный facet, иначе чипы нельзя нарисовать после
        перезагрузки.</b> Сузили выборку по классу — в ответе остались только
        уцелевшие договоры, и снятый класс вернуть неоткуда. Поэтому ответ несёт
        <code>available_rate_classes: [{{id, title, count}}]</code>, посчитанный по
        выборке ДО сужения классами, но ПОСЛЕ остальных фильтров. Плюс колонка
        получает <code>rate_class_id</code>: сегодня она несёт только название, а
        адрес собирается из id.</li>
        <li><b>Фильтр — перезапрос, а не скрытие столбцов.</b> Медиана и
        отклонения считаются по выборке, значит их обязан пересчитать сервер;
        мгновенное клиентское скрытие оставило бы на экране медиану от прежнего
        набора договоров.</li>
        <li><b>Большая выборка листается, а не обрезается.</b> Спека сравнения
        требует показывать <b>все</b> договоры выборки — таблица делает это
        горизонтальной прокруткой с закреплённой первой колонкой. Диаграмма
        отвечает тем же: столбцу задаётся минимальная ширина, полотно листается, а
        ось и линия медианы закрепляются слева. Потолок с отказом был бы проще, но
        экран отбирал бы у человека то, что он сам выбрал. Плата: «одним взглядом»
        на двадцати договорах видна часть выборки, а не вся.</li>
        <li><b>Класс берётся со снимка в договоре, а не с объекта.</b>
        <code>rate_class_id</code> записан в договор при его создании, и
        переклассификация объекта прошлое не меняет (§4). Для сравнения это
        единственно верно: договор 2024 года сравнивается в том классе, в котором
        его подписывали, — иначе вчерашняя выборка завтра поехала бы сама.</li>
        <li><b>Снять последний класс нельзя.</b> Выборка из нуля договоров — не
        состояние экрана, а отсутствие запроса; кнопка просто не срабатывает.</li>
        <li><b>Меньше трёх сопоставимых — медианы нет, и это сказано словами.</b>
        Сервер отдаёт медиану при трёх и более
        (<code>comparable_count</code>), а фильтр легко доводит выборку до двух.
        Тогда исчезают линия, плашки отклонений и колонка медианы, а вместо них
        появляется объяснение — не пустое место.</li>
        <li><b>Ось строится по фактически применённому состоянию.</b> В
        номинале верх оси вмещает номинальные столбцы и текущую медиану; при
        приведении — приведённые столбцы, риски номинала, призраки снижения и
        ОБЕ медианы. Выбор ряда сам по себе ось не двигает — как не двигает
        числа и адрес.
        <br>Прежняя редакция держала ось неподвижной, считая её сразу по двум
        состояниям, чтобы «Привести» растило столбцы, а не переписывало засечки.
        Требование снято по двум причинам. Первая — оно нереализуемо: у
        настоящего клиента приведённых чисел до запроса нет, а фоновый
        preview-запрос считал бы состояние и прятал его, оставляя URL
        номинальным. Вторая, и она достаточна сама: сравнение «до/после» теперь
        живёт ВНУТРИ столбца — сплошной верх, риска номинала, штрихованный
        промежуток, — и от масштаба не зависит вовсе. Масштаб перестал быть
        носителем эффекта, а значит и держать его неподвижным больше не за
        чем.</li>
        <li><b>Линия медианы — только там, где она в тех же деньгах, что
        столбцы.</b> Медиана считается по нетто и только в ₽/м² (§10). В «Своей
        ставке» и «Единой» столбцы валовые, и линия нетто-медианы поперёк них
        была бы второй шкалой на одной оси. Пересчитать её в ставку показа на
        клиенте нельзя: в JS нет <code>Decimal</code>, вторая копия денежной
        формулы разойдётся с первой. Отклонения (они нетто и от режима показа не
        зависят) под столбцами остаются.</li>
        <li><b>«Сумма договора» — переключатель диаграммы, а не страницы.</b>
        Таблица сравнения всегда в ₽/м², это ось экрана. Столбцы сумм отвечают на
        другой вопрос (34 млрд против 8,4 млрд — разница в размере объекта), и
        медианы на этой оси не существует. Поэтому переключатель стоит у самой
        диаграммы, а не в общей панели.</li>
        <li><b>Ставка «Единой» задана явно, а не предвыбором.</b> Предвыбор
        зависит от состава выборки — фильтр по классам молча менял бы ставку
        показа вместе с набором договоров. Ставка стоит в своём селекторе и в
        адресе.</li>
        <li><b>Сплошная часть заканчивается на приведённом значении при
        ЛЮБОМ знаке поправки.</b> Спека инфляции прямо разрешает коэффициент
        меньше единицы: цель раньше сметы — законный запрос, отказа нет. Первая
        редакция макета этого не умела — номинал оставался полной высоты, а
        штриховка добавлялась сверху через «максимум из нуля и разности», так что
        при снижении подпись показывала одно, а столбец другое. Теперь правило
        одно: верх сплошного и есть сравниваемое число, поэтому линия медианы с
        ним в одних деньгах в обе стороны. Промежуток «номинал ↔ приведённое»
        штрихуется: при росте это шапка ВНУТРИ столбца, при снижении — призрак
        НАД ним. Штриховка одна, знак читается положением и риской.</li>
        <li><b>Риска номинала рисуется всегда, и она же снимает порог.</b>
        Контурная линия на уровне цен подписания стоит при любом знаке — одним
        правилом, без особого случая. Она заодно решает задачу, под которую в
        первой редакции стоял порог «тоньше 3 px рисуется как 3 px»: при поправке
        в полпроцента столбец почти не двигается, но риска отходит от его верха
        сразу и видимо. Порог убран — он заставлял призрак перерастать саму
        риску, то есть врал о номинале ради заметности.</li>
        <li><b>Знак говорит столбец, а не легенда.</b> На стенде при цели «январь
        2025» знаки РАЗНЫЕ в одном кадре: ВЕР1-ГП и ДГП-3С10 растут, три
        остальных сжимаются. Поэтому легенда говорит «промежуток к номиналу», а
        не «рост»; подпись под столбцом — «поправка +3,7 %» или «−10,2 %»,
        снижение отмечено и цветом плашки.</li>
        <li><b>Пунктирная медиана номинала при включённом приведении.</b>
        Приведение поднимает (или опускает) все суммы вместе с медианой, поэтому
        отклонения меняются не так, как высота столбцов. Вторая линия — точечная,
        подписана «номинал» — показывает, откуда медиана уехала. Без неё
        движение столбцов читалось бы как движение отклонений.</li>
      </ul>
    </div>
  </section>

  <section>
    <h2>Что макет показывает честно</h2>
    <div class="card card-pad">
      <ul class="notes">
        <li><b>Числа настоящие, все.</b> Со стенда снято тридцать ответов: девять
        «режим НДС × ряд» для показываемых сумм и двадцать один «ряд ×
        подмножество классов» для медиан и отклонений. Разложение повторяет
        инварианты: суммы зависят от режима показа, медианы и отклонения — нет,
        зато зависят от выборки. В JS не считается ни одна денежная величина,
        только высоты столбцов.</li>
        <li><b>Чего в макете нет.</b> Второго и третьего уровня дерева статей;
        корзин ДГП/ДС (допсоглашений на стенде ноль); выбора статьи — диаграмма
        строится по «Итого по договору», а не по строке классификатора;
        состояния отказа приведения (ряд на стенде полон по построению).</li>
        <li><b>Форма, а не код.</b> В проекте диаграммы строятся на shadcn
        <code>ui/chart</code> поверх recharts (см. <code>StructureRing</code>);
        здесь столбцы нарисованы вёрсткой, потому что макет обсуждает состав и
        поведение, а не реализацию.</li>
      </ul>
      <p class="axisnote bound" style="margin-top:14px">Спеки у фичи ещё нет —
      макет сделан до гейта 1, чтобы разговор шёл про картинку, а не про её
      описание. Инварианты §10 соблюдены: ось сравнения нетто, медиана и
      отклонения по нетто, состав подписан на самой поверхности.</p>
    </div>
  </section>

</div>

<script>
{JS.replace("__PAYLOAD__", PAYLOAD)}
</script>
"""

STANDALONE = """<!--
  Макет страницы сравнения договоров со столбчатой диаграммой стоимости и
  фильтром по классу объекта (маршрут `/compare`). Сделан ДО гейта 1: спеки у
  фичи ещё нет.

  ДАННЫЕ НАСТОЯЩИЕ — стенд `gca_dev` на """ + STAND_DATE + """: 5 договоров, три
  класса объектов, два заведённых ряда индексов. Со стенда снято тридцать
  ответов `build_comparison` — девять для показываемых сумм и двадцать один для
  медиан и отклонений по каждому непустому подмножеству классов. Иллюстративного
  в файле нет ничего.

  Палитра, шрифтовые стеки и классы таблицы взяты из
  `2026-08-18-inflation-adjustment-mockup.html` без изменений — это тот же экран.

  Пересобрать:
    cd backend && PYTHONPATH=. .venv/Scripts/python.exe \\
      ../docs/superpowers/specs/2026-08-19-comparison-cost-chart-mockup.gen.py \\
      ../docs/superpowers/specs/2026-08-19-comparison-cost-chart-mockup.html \\
      <путь для артефактной версии>
-->
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
""" + CONTENT.replace('<div class="wrap">', '</head>\n<body>\n<div class="wrap">', 1) + """
</body>
</html>
"""

out_repo, out_art = sys.argv[1], sys.argv[2]
with open(out_repo, "w", encoding="utf-8") as fh:
    fh.write(STANDALONE)
with open(out_art, "w", encoding="utf-8") as fh:
    fh.write(CONTENT)
print("договоров:", len(cols), "| классов:", len(class_titles),
      "| подмножеств:", len(subsets), "| строк:", len(rows_ref),
      "| снимков оси:", len(top))
print("repo:", out_repo)
print("artifact:", out_art)
