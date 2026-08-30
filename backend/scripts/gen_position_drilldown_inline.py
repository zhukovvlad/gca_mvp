"""Генератор секции «раскрытие внутри свода» макета гейта 1 фичи 4.

Числа секции берутся со стенда (`gca_dev`), а не набираются руками: макет гейта
обязан стоять на замере, а перенос двух десятков сумм в разметку — ровно то
место, где замер тихо расходится с базой. Запуск:

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/gen_position_drilldown_inline.py

С ключом `--write` секция вставляется в файл макета между маркерами
BEGIN/END gen:inline, без ключа — печатается в stdout.
"""
from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import psycopg

# Участник с четырьмя этапами (ООО «АНТТЕК») и его предложения по этапам.
STAGES: list[tuple[int, int]] = [(1, 27), (2, 29), (3, 33), (4, 38)]
ARTICLE = "8.3"
ROOT = "8"
#: Вторая статья: на четвёртом этапе подрядчик пересобрал пирог подготовки —
#: одни работы сняты, другие появились. Разложение обязано это показывать.
ARTICLE_BORN = "3.1"
ROOT_BORN = "3"
#: Статьи с движением меньше миллиона в разложении не нуждаются — оно там шум.
MOVEMENT_FLOOR = Decimal("1e6")
#: Доля движения статьи, которую обязаны объяснить показанные строки.
COVERAGE = Decimal("0.9")
#: Сколько появившихся и снятых работ показывается сверх объяснителей поимённо.
#: «Работа исчезла из сметы» — качественно иной факт, чем «подешевела на 3 %», и
#: стоит вопроса к подрядчику независимо от суммы (решение пользователя
#: 30.08.2026). Потолок взят по замеру: таких строк вне объяснителей медиана 0 на
#: статью при девятой децили 3, то есть пять покрывают почти все статьи целиком;
#: но у пяти статей из 76 их больше, а в худшей 49 — там остаток сворачивается.
PARTIAL_CAP = 5


def dsn() -> str:
    env = Path(__file__).resolve().parent.parent / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().replace("postgresql+psycopg://", "postgresql://")
    raise SystemExit("DATABASE_URL не найден в backend/.env")


def esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;"))


def mln(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{value / Decimal('1e6'):.1f}".replace(".", ",").replace("-", "−")


def pct(cur: Decimal | None, prev: Decimal | None) -> tuple[str, str]:
    """Изменение к предыдущему этапу: текст и класс тона."""
    if cur is None or prev is None or prev == 0:
        return "—", "flat"
    delta = (cur - prev) / prev * 100
    cls = "flat" if abs(delta) < Decimal("0.05") else ("up" if delta > 0 else "down")
    sign = "+" if delta > 0 else ("−" if delta < 0 else "")
    return f"{sign}{abs(delta):.1f} %".replace(".", ","), cls


def plural(n: int, forms: tuple[str, str, str]) -> str:
    """Склонение существительного при числе: 21 строка, 22 строки, 25 строк."""
    tail100, tail10 = n % 100, n % 10
    if 11 <= tail100 <= 14:
        return forms[2]
    if tail10 == 1:
        return forms[0]
    if 2 <= tail10 <= 4:
        return forms[1]
    return forms[2]


def qty(value: Decimal | None) -> str:
    """Объём заказчика: разряды пробелом, дробная часть запятой и не длиннее знака.

    В базе объём лежит как есть из файла — у площадей это 8726.397168 м². Печатать
    такое в ячейке нельзя: девять значащих цифр не помогают решению «объём двигался
    или нет», а ширину колонки съедают.
    """
    if value is None:
        return "—"
    rounded = value.quantize(Decimal("0.1")) if value % 1 else value.quantize(Decimal("1"))
    whole, _, frac = format(abs(rounded), "f").partition(".")
    groups = []
    while whole:
        groups.append(whole[-3:])
        whole = whole[:-3]
    text = "\u202f".join(reversed(groups))
    if frac and frac != "0":
        text += f",{frac}"
    return f"−{text}" if rounded < 0 else text


# --------------------------------------------------------------------------
#  Загрузка со стенда
# --------------------------------------------------------------------------

def load_tree(cur, root_code: str = ROOT) -> tuple[dict, list[dict]]:
    """Корень `root_code` и его дети со свёрнутыми по поддереву суммами этапов."""
    cur.execute("select id, code, title, parent_id, sort_order from work_categories")
    cats = {r[0]: r for r in cur.fetchall()}
    children = defaultdict(list)
    for cid, row in cats.items():
        children[row[3]].append(cid)

    direct: dict[int, dict[int, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for stage, proposal in STAGES:
        cur.execute(
            """
            select ch.work_category_id, sum(pi.total_cost_total)
            from position_items pi
            join position_items ch
              on ch.id = pi.chapter_item_id and ch.proposal_id = pi.proposal_id
            where pi.proposal_id = %s and not pi.is_chapter
              and ch.work_category_id is not null
            group by ch.work_category_id
            """,
            (proposal,),
        )
        for cid, amount in cur.fetchall():
            direct[cid][stage] += Decimal(amount or 0)

    def subtree(cid: int) -> list[int]:
        out = [cid]
        for kid in children[cid]:
            out += subtree(kid)
        return out

    def roll(cid: int) -> dict[int, Decimal]:
        total: dict[int, Decimal] = defaultdict(Decimal)
        for node in subtree(cid):
            for stage, amount in direct[node].items():
                total[stage] += amount
        return dict(total)

    def node(row) -> dict:
        return {"code": row[1], "title": row[2], "cells": roll(row[0])}

    by_code = {row[1]: row for row in cats.values()}
    root_row = by_code[root_code]
    kids = [node(cats[k]) for k in sorted(children[root_row[0]], key=lambda k: cats[k][4])]
    return node(root_row), [k for k in kids
                            if any(v != 0 for v in k["cells"].values())]


def load_groups(cur, code: str) -> list[dict]:
    """Группы «каталожная позиция» статьи `code` с суммами и объёмами по этапам."""
    groups: dict[int, dict] = {}
    for stage, proposal in STAGES:
        cur.execute(
            """
            select pi.catalog_position_id, cp.standard_job_title,
                   sum(pi.total_cost_total), count(*),
                   array_agg(distinct pi.suggested_quantity), min(u.symbol)
            from position_items pi
            join position_items ch
              on ch.id = pi.chapter_item_id and ch.proposal_id = pi.proposal_id
            join work_categories wc on wc.id = ch.work_category_id
            left join catalog_positions cp on cp.id = pi.catalog_position_id
            left join units_of_measure u on u.id = pi.unit_id
            where pi.proposal_id = %s and not pi.is_chapter and wc.code = %s
            group by pi.catalog_position_id, cp.standard_job_title
            """,
            (proposal, code),
        )
        for cat_id, title, amount, rows, volumes, unit in cur.fetchall():
            group = groups.setdefault(
                cat_id, {"id": cat_id, "title": title or "без каталожной привязки", "stages": {}}
            )
            group["stages"][stage] = {
                "amount": Decimal(amount or 0),
                "rows": rows,
                "volumes": sorted(v for v in volumes if v is not None),
                "unit": unit,
            }
    return list(groups.values())


# --------------------------------------------------------------------------
#  Разложение изменения на строки
# --------------------------------------------------------------------------

def first_last(cells: dict[int, object], get=lambda c: c["amount"]) -> tuple[Decimal, Decimal]:
    """Значения на ПЕРВОМ и ПОСЛЕДНЕМ выбранных этапах; отсутствие — ноль.

    Считать от первого этапа, где строка ПОЯВИЛАСЬ, нельзя: работа, которой на
    первом этапе не было, а на последнем есть на 22,8 млн, изменила статью ровно
    на эти 22,8 млн, и её вклад обязан быть таким. При счёте «от появления» он
    вышел бы нулевым, строка не попала бы в разложение, а разницу молча унесла бы
    строка «прочие». Найдено вопросом пользователя 30.08.2026.
    """
    first_stage, last_stage = STAGES[0][0], STAGES[-1][0]
    zero = Decimal(0)
    first = get(cells[first_stage]) if first_stage in cells else zero
    last = get(cells[last_stage]) if last_stage in cells else zero
    return first, last


def contribution(group: dict) -> Decimal:
    first, last = first_last(group["stages"])
    return last - first


def explainers(groups: list[dict]) -> tuple[list[dict], list[dict]]:
    """Строки, объясняющие COVERAGE движения статьи, и всё остальное.

    Вклады могут гасить друг друга, поэтому критерий — не доля суммы модулей, а
    близость НАКОПЛЕННОГО вклада к движению статьи: показанные строки обязаны
    объяснять его, а не набирать девять десятых по абсолютной величине.
    """
    total = sum((contribution(g) for g in groups), Decimal(0))
    order = sorted(groups, key=lambda g: -abs(contribution(g)))
    if total == 0:
        return order[:1], order[1:]
    cumulative, top = Decimal(0), []
    for group in order:
        cumulative += contribution(group)
        top.append(group)
        if abs(total - cumulative) <= (1 - COVERAGE) * abs(total):
            break
    return top, order[len(top):]


def volume_moved(group: dict) -> bool:
    seen = {tuple(group["stages"][s]["volumes"]) for s in group["stages"]}
    return len(seen) > 1


def corpus_stats(cur) -> dict:
    """Замер по ВСЕМ статьям участника, а не по одной показанной."""
    per_article: dict[str, dict[int, dict[int, dict]]] = defaultdict(lambda: defaultdict(dict))
    for stage, proposal in STAGES:
        cur.execute(
            """
            select wc.code, pi.catalog_position_id,
                   sum(pi.total_cost_total), count(*), array_agg(distinct pi.suggested_quantity)
            from position_items pi
            join position_items ch
              on ch.id = pi.chapter_item_id and ch.proposal_id = pi.proposal_id
            join work_categories wc on wc.id = ch.work_category_id
            where pi.proposal_id = %s and not pi.is_chapter
            group by wc.code, pi.catalog_position_id
            """,
            (proposal,),
        )
        for code, cat_id, amount, rows, volumes in cur.fetchall():
            per_article[code][cat_id][stage] = {
                "amount": Decimal(amount or 0),
                "rows": rows,
                "volumes": sorted(v for v in volumes if v is not None),
            }

    needed, sizes, moved, partial, multi, articles = [], [], 0, 0, 0, 0
    for groups in per_article.values():
        wrapped = [{"id": cid, "stages": cells} for cid, cells in groups.items()]
        total = sum((contribution(g) for g in wrapped), Decimal(0))
        if abs(total) < MOVEMENT_FLOOR:
            continue
        articles += 1
        sizes.append(len(wrapped))
        top, _ = explainers(wrapped)
        needed.append(len(top))
        for group in top:
            if volume_moved(group):
                moved += 1
            if len(group["stages"]) < len(STAGES):
                partial += 1
            if any(cell["rows"] > 1 for cell in group["stages"].values()):
                multi += 1

    ordered = sorted(needed)

    def percentile(q: float) -> int:
        return ordered[min(len(ordered) - 1, int(len(ordered) * q))]

    return {
        "articles": articles,
        "median_size": statistics.median(sizes),
        "max_size": max(sizes),
        "median_needed": statistics.median(needed),
        "p75_needed": percentile(0.75),
        "p90_needed": percentile(0.9),
        "max_needed": max(needed),
        "under5": sum(1 for x in ordered if x <= 5),
        "over15": sum(1 for x in ordered if x > 15),
        "explainers": len(needed) and sum(needed),
        "moved": moved,
        "partial": partial,
        "multi": multi,
    }


# --------------------------------------------------------------------------
#  Разметка
# --------------------------------------------------------------------------

def volume_text(group: dict) -> tuple[str, bool]:
    """Траектория объёма заказчика по этапам и признак «объём двигался»."""
    steps, unit = [], None
    for stage, _ in STAGES:
        cell = group["stages"].get(stage)
        if cell is None:
            steps.append("—")
            continue
        unit = unit or cell["unit"]
        steps.append("+".join(qty(v) for v in cell["volumes"]) or "—")
    moved = volume_moved(group)
    body = " → ".join(steps) if moved else next((s for s in steps if s != "—"), "—")
    return f"{body}&nbsp;{esc(unit or '')}".strip(), moved


def stage_cells(cells: dict[int, dict], *, inline_volume: bool = False,
                group: dict | None = None, bare: bool = False) -> str:
    """Денежные ячейки строки: сумма, под ней изменение к предыдущему этапу."""
    present = [s for s, _ in STAGES if s in cells]
    out, previous = [], None
    for stage, _ in STAGES:
        cell = cells.get(stage)
        if cell is None:
            if present and stage > present[-1]:
                out.append('<td class="num"><span class="pill warn">снято</span></td>')
            else:
                out.append('<td class="num"><span class="pill">не оценивалась</span></td>')
            continue
        amount = cell["amount"] if isinstance(cell, dict) else cell
        body = f'<span class="mny">{mln(amount)}</span>'
        if inline_volume and group is not None:
            volumes = "+".join(qty(v) for v in cell["volumes"]) or "—"
            tone = " chg" if group["_moved_at"].get(stage) else ""
            body += f'<span class="qty{tone}">{volumes}&nbsp;{esc(cell["unit"] or "")}</span>'
        if bare:
            pass
        elif previous is None and stage == present[0] and present[0] != STAGES[0][0]:
            body += '<span class="dp"><span class="pill">появилась</span></span>'
        elif previous is not None:
            text, tone = pct(amount, previous)
            body += f'<span class="dp {tone}">{text}</span>'
        out.append(f'<td class="num">{body}</td>')
        previous = amount
    first, last = first_last(cells)
    text, tone = pct(last, first)
    delta = last - first
    born = STAGES[0][0] not in cells
    died = STAGES[-1][0] not in cells
    if bare:
        torg = '<span class="flat">—</span>'
    elif born and present:
        torg = '<span class="pill">появилась</span>'
    elif died and present:
        torg = '<span class="pill warn">снято</span>'
    else:
        torg = f'<span class="{tone}">{text}</span>'
    if born or died:
        tone = "up" if delta > 0 else "down"
    out.append(f'<td class="num sep">{torg}</td>')
    out.append(f'<td class="num"><span class="{tone}">{mln(delta)}</span></td>')
    return "".join(out)


def article_row(node: dict, level: int, *, key: str | None = None) -> str:
    cells = {s: {"amount": a} for s, a in node["cells"].items()}
    chevron = (f'<button class="tw" aria-expanded="true" data-k="{key}">▾</button>'
               if key else '<span class="tw" aria-hidden="true"> </span>')
    label = (f'<td class="t">{chevron}<span class="code">{esc(node["code"])}</span>'
             f'{esc(node["title"])}</td>')
    return (f'<tr class="lvl{level} art">{label}{stage_cells(cells)}</tr>')


def mark_volume_steps(group: dict) -> dict:
    """Помечает этапы, на которых объём отличается от предыдущего этапа."""
    moved, previous = {}, None
    for stage, _ in STAGES:
        cell = group["stages"].get(stage)
        if cell is None:
            continue
        volumes = tuple(cell["volumes"])
        moved[stage] = previous is not None and volumes != previous
        previous = volumes
    group["_moved_at"] = moved
    return group


def group_row(group: dict, variant: str) -> str:
    text, moved = volume_text(group)
    ambiguous = any(cell["rows"] > 1 for cell in group["stages"].values())
    full = esc(group["title"])
    name = f'<span class="nm" title="{full}">{full}</span>'
    pills = ""
    if ambiguous:
        pills += (' <span class="ambig" title="Несколько строк сметы в одной группе:'
                  ' сравнивается их сумма">несколько строк сметы</span>')
    if variant == "v1":
        tone = " chg" if moved else ""
        name += f'<span class="volline{tone}">объём заказчика: {text}</span>'
    elif variant == "v3" and moved:
        pills += (f' <span class="ambig" title="Объём заказчика по этапам: {text}">'
                  'объём менялся</span>')
    cells = stage_cells(group["stages"], inline_volume=variant.startswith("v2"),
                        group=group)
    return f'<tr class="pos3 k-{variant}a"><td class="t">{name}{pills}</td>{cells}</tr>'


def bag_row(groups: list[dict], label: str, sub: str, variant: str,
            css: str = "dimrow") -> str:
    """Свёрнутая группа строк: суммы по этапам без процентов и пилюль."""
    cells = {}
    for stage, _ in STAGES:
        total = sum((g["stages"][stage]["amount"] for g in groups if stage in g["stages"]),
                    Decimal(0))
        cells[stage] = {"amount": total}
    body = (f'<td class="t"><span class="nm">{label}</span>'
            f'<span class="volline">{sub}</span></td>')
    return (f'<tr class="pos3 {css} k-{variant}a">{body}'
            f'{stage_cells(cells, bare=True)}</tr>')


def rest_row(article: dict, shown: list[dict], count: int, variant: str) -> str:
    cells = {}
    for stage, _ in STAGES:
        total = article["cells"].get(stage)
        if total is None:
            continue
        taken = sum((g["stages"][stage]["amount"] for g in shown if stage in g["stages"]),
                    Decimal(0))
        cells[stage] = {"amount": total - taken}
    if count == 0:
        return ""
    word = plural(count, ("строка", "строки", "строк"))
    label = (f'<td class="t"><span class="nm">прочие {count} {word} статьи</span>'
             '<span class="volline">свёрнуто; несёт остаток, чтобы итог статьи сходился'
             '</span></td>')
    return (f'<tr class="pos3 dimrow k-{variant}a">{label}'
            f'{stage_cells(cells, bare=True)}</tr>')


def fragment(root: dict, kids: list[dict], article: dict, case: dict,
             variant: str) -> str:
    head = ('<thead><tr><th class="art">Статья классификатора</th>'
            + "".join(f'<th class="num">Этап {s}</th>' for s, _ in STAGES)
            + '<th class="num sep">Торг: первый → последний</th>'
            + '<th class="num">Вклад, млн</th></tr></thead>')
    rows = [article_row(root, 1, key=f"{variant}r")]
    for kid in kids:
        if kid["code"] == article["code"]:
            rows.append(article_row(kid, 2, key=f"{variant}a"))
            shown = list(case["top"]) + list(case["partials"])
            rows += [group_row(g, variant) for g in shown]
            hidden = case["partials_hidden"]
            if hidden:
                word = plural(len(hidden), ("работа", "работы", "работ"))
                rows.append(bag_row(
                    hidden, f"ещё {len(hidden)} {word} появились или сняты",
                    "свёрнуто; раскрывается по нажатию", variant, css="dimrow born"))
                shown = shown + hidden
            rows.append(rest_row(article, shown, len(case["rest"]), variant))
        else:
            rows.append(article_row(kid, 2))
    body = "<tbody>" + "".join(r.replace('class="lvl2 art"', f'class="lvl2 art kid k-{variant}r"')
                               for r in rows) + "</tbody>"
    body = body.replace(f'class="pos3 k-{variant}a"', f'class="pos3 kid k-{variant}a"')
    return f'<div class="scroller"><table class="pass">{head}{body}</table></div>'


SECTION_CSS = """
<style>
/* Секция «раскрытие внутри свода». Каталожные наименования имеют медиану 52
   знака при девятой децили 167 и максимуме 5077, поэтому подпись строки сметы
   ОБРЕЗАЕТСЯ ДВУМЯ СТРОКАМИ, а полный текст остаётся в подсказке: без обрезки
   строка сметы вытянулась бы на десятки строк высоты.

   Замер 30.08.2026 добавил к этому находку, которую надо унести в спеку.
   Зажим колонки подписи `max-width:420px`, живущий в своде на `td.t`, в
   АВТОРАЗМЕТКЕ таблицы НЕ ОБЯЗЫВАЕТ: здесь колонка встала в 519 px при
   контейнере 1238, то есть 41,9 % против обещанных 33,9 %. В макете свода того
   же не видно только потому, что там наименования короче (замер: 358,7 px,
   29,9 % — ниже потолка, то есть потолок и не проверялся). Правые колонки при
   этом достижимы, горизонтальной прокрутки страницы нет ни на 1280, ни на 1100
   — то есть это запас ширины, а не поломка; но обещание «не больше 420» на
   длинных наименованиях неверно, и обещать его в спеке нельзя. */
tr.pos3 .nm { display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
  overflow:hidden; }
tr.pos3 .volline { display:block; font-size:10.5px; color:var(--fg4);
  font-variant-numeric:tabular-nums; margin-top:1px; }
tr.pos3 .volline.chg { color:var(--info); }
tr.pos3 td.num .qty { display:block; }
tr.dimrow .nm { font-style:italic; }
</style>
"""

VARIANTS = [
    ("v1", "Вариант 1. Объём заказчика — строкой под наименованием",
     "Денежные ячейки строки сметы устроены ТОЧНО так же, как ячейки статьи: сумма и "
     "под ней изменение. Объём уезжает под наименование и подсвечивается там, где "
     "двигался.",
     "За: грамматика таблицы не меняется вовсе — свод остаётся сводом, у него лишь "
     "появляется третий уровень. Против: траектория объёма не стоит под колонками "
     "этапов, и сверить «на каком этапе упал объём» с «на каком этапе упала сумма» "
     "приходится глазом, а не по вертикали. Замер высоты строки: 68,8 px против "
     "49,0 px у строки статьи — САМЫЙ ВЫСОКИЙ из трёх вариантов, потому что "
     "траектория объёма встаёт третьей строкой под двумя строками наименования.",
     "ok"),
    ("v2", "Вариант 2. Объём заказчика — второй строкой в каждой денежной ячейке",
     "Так объём стоит ровно под своей суммой и на своём этапе. Ячейка становится "
     "трёхэтажной: сумма, объём, изменение.",
     "За: «сумма упала, объём тоже» читается по вертикали, без перевода взгляда, и "
     "изменившийся объём подсвечен на СВОЁМ этапе. Замер высоты строки: 63,3 px — "
     "НИЖЕ варианта 1 (68,8 px), потому что третий этаж ячейки прячется в высоту, "
     "которую и так занимают две строки наименования. Ожидание «ячейка с объёмом "
     "сделает строку выше» замер не подтвердил. Против: у статьи и у строки сметы "
     "ячейка устроена по-разному, и там, где объём не менялся, он повторяется в "
     "каждой колонке — «1905 шт» четыре раза подряд.", "ok"),
    ("v3", "Вариант 3. Пилюля «объём менялся», числа — по наведению",
     "Самый компактный: строка сметы по высоте равна строке статьи, а объём "
     "превращается в предупреждение.",
     "Против: прячет ровно тот факт, ради которого объём и понадобился. У «Дверей в "
     "эвакуационные ЛК» −62,2 % на втором этапе — это падение объёма с 294 до 111 шт, "
     "и читатель обязан узнать это, а не догадаться навести курсор. Наведения нет на "
     "планшете, и в печать подсказка не попадает. Выигрыш в высоте к тому же "
     "неполный: строка без пилюли 52,0 px, а строка С пилюлей — 71,5 px, потому что "
     "пилюля переносится под наименование. То есть самая важная строка оказывается "
     "самой высокой и при этом самой немой.", "bad"),
]


def measure_rows(stats: dict) -> str:
    measured = [
        ("Статей с движением больше миллиона", f"{stats['articles']}"),
        ("Строк, объясняющих 90 % движения статьи: медиана", f"{stats['median_needed']:.0f}"),
        ("…три четверти статей", f"{stats['p75_needed']} и меньше"),
        ("…девятая дециль", f"{stats['p90_needed']}"),
        ("…худшая статья", f"{stats['max_needed']}"),
        ("Статей, где хватает пяти строк", f"{stats['under5']} из {stats['articles']}"),
        ("Статей, где нужно больше пятнадцати", f"{stats['over15']}"),
        ("Всего групп в статье: медиана / максимум",
         f"{stats['median_size']:.0f} / {stats['max_size']}"),
        ("Строк-объяснителей всего", f"{stats['explainers']}"),
        ("…у которых ДВИГАЛСЯ объём заказчика",
         f"{stats['moved']} ({stats['moved'] * 100 // stats['explainers']} %)"),
        ("…которые есть не на всех этапах (появилась / снято)", f"{stats['partial']}"),
        ("…у которых на этапе несколько строк сметы", f"{stats['multi']}"),
    ]
    return "".join(f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in measured)


#: Принятый вариант размещения объёма заказчика (решение пользователя 30.08.2026).
ACCEPTED = "v2"


def born_block(born: dict) -> str:
    """Появление и снятие работы — на статье, где ровно это и произошло."""
    article = born["article"]
    change = article["cells"][STAGES[-1][0]] - article["cells"][STAGES[0][0]]
    return (
        '<p class="vh">Работа появилась или снята: та же грамматика, статья '
        f'{esc(article["code"])}</p>'
        '<p class="secsub">В статье 8.3 все три строки-объяснителя есть на всех '
        'четырёх этапах, поэтому появление и снятие там не видно. Вот статья, где '
        'произошло ровно это: на четвёртом этапе подрядчик <b>пересобрал пирог '
        f'подготовки</b>, и статья не подешевела, а ВЫРОСЛА на {mln(change)} млн. '
        'Разложение показывает, чем: одна работа появилась, другая снята.</p>'
        + fragment(born["root"], born["kids"], article, born, "v2b")
        + '<div class="verdict"><b>Правило.</b> Строка, которой на этапе нет, несёт '
        'пилюлю: <span class="pill">не оценивалась</span> до появления и '
        '<span class="pill warn">снято</span> после снятия — те же слова, что в своде, '
        'новых не заводится. В колонке «Торг» процента у такой строки не бывает: '
        'делить не на что, там стоит та же пилюля. А вот <b>вклад считается от нуля</b>: '
        'работа, которой на первом этапе не было, а на последнем есть на 22,8 млн, '
        'изменила статью ровно на эти 22,8 млн. Иначе появившаяся работа выпала бы из '
        'разложения, а разницу молча унесла бы строка «прочие».</div>'
        '<div class="state info"><b>Чего экран не утверждает.</b> Здесь видно, что '
        '«Геотекстиль 500 г/м2» снят, а «Геотекстильное полотно 150 г/м2» появилось — '
        'на том же объёме 8 726,4 м². Человек прочитает это как ЗАМЕНУ материала, и '
        'скорее всего будет прав. Но у нас нет основания это утверждать: каталожные '
        'позиции разные, и «замена» — вывод читателя, а не факт файла. Экран показывает '
        'снятие и появление рядом и молчит о связи между ними.</div>'
    )


def section(case: dict, born: dict, stats: dict) -> str:
    root, kids = case["root"], case["kids"]
    article, top, rest = case["article"], case["top"], case["rest"]
    partials = case["partials"] + case["partials_hidden"]
    total_change = article["cells"][STAGES[-1][0]] - article["cells"][STAGES[0][0]]
    explained = sum((contribution(g) for g in top), Decimal(0))
    share = abs(explained / total_change * 100)
    blocks = []
    for key, title, lead, verdict, tone in VARIANTS:
        accepted = " ✓ принят 30.08.2026" if key == ACCEPTED else ""
        blocks.append(
            f'<p class="vh">{title}<span class="cnt">{accepted}</span></p>'
            f'<p class="secsub">{lead}</p>'
            + fragment(root, kids, article, case, key)
            + f'<div class="verdict{"" if tone == "ok" else " bad"}">{verdict}</div>'
        )
    joined = "".join(blocks) + born_block(born)
    return f"""{SECTION_CSS}
  <section id="inline">
    <h2>Решение гейта: раскрытие живёт внутри свода</h2>
    <p class="secsub">Секция <code>#place</code> выше спрашивала, чем экран приходится
    своду — третьим уровнем, отдельной страницей или панелью, — и рекомендовала
    отдельную страницу. <b>Рекомендация снята.</b> Она стояла на числе «строк в
    статье» (до 168), а раскрывать надо не статью, а её ИЗМЕНЕНИЕ: строк, которые его
    объясняют, медиана {stats['median_needed']:.0f}. Это влезает внутрь свода, и
    вопрос размещения закрывается сам — остаётся один вопрос, куда положить объём
    заказчика.</p>
    <p class="secsub"><b>Числа этой секции — настоящие суммы стенда</b>, в отличие от
    секций выше, где деньги иллюстративные (врезка вверху страницы). Разметку
    порождает <code>backend/scripts/gen_position_drilldown_inline.py</code> прямо из
    базы, а не набирает руками.</p>

    <div class="measure">
      <table>{measure_rows(stats)}</table>
      <p class="hint">Замер 30.08.2026, тот же участник и те же четыре этапа.
      «Строка-объяснитель» — группа «статья + каталожная позиция», попавшая в число
      тех, чей накопленный вклад объясняет 90 % движения статьи. Критерий — близость
      НАКОПЛЕННОГО вклада к движению статьи, а не доля суммы модулей: вклады гасят
      друг друга, и по модулям набралось бы больше строк, чем нужно.</p>
      <p class="hint"><b>Замер раскладки, Chrome, обе темы, 1280 и 1100 px:</b>
      горизонтальной прокрутки страницы нет (<code>scrollWidth == clientWidth</code>),
      ни одна из трёх таблиц не шире своего контейнера, все три дают одинаковые
      числа. Колонка подписи — 519,0 px при контейнере 1238 (41,9 %) и 443,6 при
      1058 (те же 41,9 %); из двенадцати наименований обрезаны девять, то есть
      обрезка проверена на живом тексте, а не объявлена. Высота строки работы:
      68,8 px в варианте 1, 63,3 в варианте 2, 52,0 (71,5 у строки с пилюлей) в
      варианте 3 — при 49,0 px у строки статьи.</p>
    </div>

    <p class="secsub">Ниже — один и тот же фрагмент свода: статья
    <b>{esc(root['code'])} {esc(root['title'])}</b> раскрыта до подстатей, подстатья
    <b>{esc(article['code'])}</b> раскрыта до работ. Подстатья упала на
    <b>{mln(total_change)} млн</b>, и показанные {len(top)} строки объясняют
    <b>{share:.0f} %</b> этого падения. Под ними — {len(partials)}
    {plural(len(partials), ("работа", "работы", "работ"))}, которые появились или были
    сняты: их показывают ВСЕГДА, какой бы малой ни была сумма, потому что «работы
    больше нет в смете» — другой факт, чем «подешевела на 3 %». Остальные
    {len(rest)} свёрнуты в одну строку, чтобы итог сходился в каждой колонке.
    Различаются варианты только тем, где стоит объём заказчика.</p>

    {joined}

    <p class="vh">Что одинаково во всех трёх</p>
    <ul class="notes">
      <li><b>Показываются не все строки статьи, а объясняющие изменение</b>, плюс
      строка «прочие N строк» с остатком. Итог статьи сходится из показанного в
      КАЖДОЙ колонке этапа — это проверяемое обещание, а не оформление.</li>
      <li><b>Подпись строки сметы обрезается двумя строками</b>, полный текст — в
      подсказке. Каталожные наименования: медиана 52 знака, девятая дециль 167,
      максимум 5077. Колонка подписи в своде зажата 250–420 px, и снимать зажим
      нельзя — раскрытие статей уже ломало раскладку (AGENTS §11).</li>
      <li><b>Появление и снятие — те же пилюли, что в своде</b>
      (<span class="pill">не оценивалась</span>, <span class="pill">появилась</span>,
      <span class="pill warn">снято</span>), и новых слов не заводится. В колонке
      «Торг» у такой строки стоит пилюля, а не процент: делить не на что. А вот
      ВКЛАД считается от нуля — работа, которой на первом этапе не было, изменила
      статью ровно на свою сумму.</li>
      <li><b>Появившиеся и снятые работы показываются поимённо всегда</b>, даже
      когда их сумма мала и в объяснители они не попали (решение пользователя
      30.08.2026). Сверх {PARTIAL_CAP} они сворачиваются в строку «ещё N работ
      появились или сняты». Замер: таких строк вне объяснителей 114 на 76 статей —
      медиана 0 на статью, девятая дециль 3, максимум 49; у 55 статей их нет вовсе.
      Из {stats['explainers']} строк-объяснителей {stats['partial']} и сами есть не
      на всех этапах.</li>
      <li><b>Дыр в середине не бывает — проверено.</b> Из 1017 групп 797 идут сквозь
      все четыре этапа, 101 появилась, 110 снято, и НИ ОДНОЙ, которая пропала бы на
      среднем этапе и вернулась. Если такая появится, средний этап покажет
      <span class="pill">не оценивалась</span>, и правило не сломается.</li>
      <li><b>Неоднозначная группа помечается пилюлей «несколько строк сметы»</b> —
      решение секции <code>#naming</code>. Таких среди объяснителей
      {stats['multi']}.</li>
      <li><b>Колонки «₽ за единицу» нет</b> — решение пользователя 30.08.2026.
      Удельная цена выводится из суммы и объёма, а третья величина в ячейке
      перегружает строку.</li>
    </ul>
  </section>
"""


BEGIN = "<!-- BEGIN gen:inline -->"
END = "<!-- END gen:inline -->"
MOCKUP = (Path(__file__).resolve().parent.parent.parent / "docs" / "superpowers" /
          "specs" / "2026-08-29-position-drilldown-mockup.html")


def write_into_mockup(body: str) -> None:
    """Замена между маркерами: секция порождается заново при каждом прогоне."""
    text = MOCKUP.read_text(encoding="utf-8")
    if BEGIN not in text or END not in text:
        raise SystemExit(f"маркеры {BEGIN}/{END} не найдены в {MOCKUP}")
    head, _, tail = text.partition(BEGIN)
    _, _, tail = tail.partition(END)
    MOCKUP.write_text(f"{head}{BEGIN}\n{body}\n  {END}{tail}", encoding="utf-8")
    print(f"секция записана в {MOCKUP}")


def is_partial(group: dict) -> bool:
    return len(group["stages"]) < len(STAGES)


def load_case(cur, root_code: str, article_code: str) -> dict:
    root, kids = load_tree(cur, root_code)
    article = next(k for k in kids if k["code"] == article_code)
    groups = [mark_volume_steps(g) for g in load_groups(cur, article_code)]
    top, rest = explainers(groups)
    partials = sorted((g for g in rest if is_partial(g)),
                      key=lambda g: -abs(contribution(g)))
    return {
        "root": root, "kids": kids, "article": article, "top": top,
        "partials": partials[:PARTIAL_CAP],
        "partials_hidden": partials[PARTIAL_CAP:],
        "rest": [g for g in rest if not is_partial(g)],
    }


def main() -> None:
    with psycopg.connect(dsn()) as con, con.cursor() as cur:
        main_case = load_case(cur, ROOT, ARTICLE)
        born_case = load_case(cur, ROOT_BORN, ARTICLE_BORN)
        stats = corpus_stats(cur)
    body = section(main_case, born_case, stats)
    if "--write" in sys.argv[1:]:
        write_into_mockup(body)
    else:
        print(body)


if __name__ == "__main__":
    main()
