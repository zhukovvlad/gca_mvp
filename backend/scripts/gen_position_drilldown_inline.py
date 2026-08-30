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
#: одни работы исчезли из файла, другие появились. Разложение обязано это
#: показывать, и «исчезла» здесь не синоним «снята» (см. STATE_LABEL).
#: Ключи псевдогрупп: у допработ и у строк без каталожной привязки нет
#: каталожной позиции, поэтому ключ группы (§2.2) их не берёт, а показать их
#: обязаны — иначе их деньги выпадут из сходимости.
#:
#: Допработы перечисляются ПОСТРОЧНО, как в паспорте (`_extras_by_category`),
#: и ключ у них — НАИМЕНОВАНИЕ: `chapter_ref_raw` в ключ не годится, он NULL у
#: нераспределённой записи по конструкции таблицы (`ck_..._unresolved_ref`), а
#: ключ, который бывает пустым, — не ключ. Одно наименование дважды в одной
#: статье одного предложения встречается (2 случая по базе) и обрабатывается тем
#: же правилом, что дубли каталожной позиции: пилюля «несколько строк сметы».
EXTRA_KEY = "additional_works"
UNMATCHED_KEY = "unmatched"
#: Порядок видов строк при равном по модулю вкладе: сначала работы, затем
#: допработы, затем непривязанные строки. Ничья по модулю вклада возможна
#: (нули, симметричные суммы), и без полного ключа порядок зависел бы от
#: порядка выдачи БД. Сравнивать id СТРОКОЙ нельзя: "10" встанет раньше "2".
KIND_RANK = {EXTRA_KEY: 1, UNMATCHED_KEY: 2}
ARTICLE_BORN = "3.1"
ROOT_BORN = "3"
#: Статьи с движением меньше миллиона в разложении не нуждаются — оно там шум.
MOVEMENT_FLOOR = Decimal("1e6")
#: Доля движения статьи, которую обязаны объяснить показанные строки.
COVERAGE = Decimal("0.9")
#: Сколько появившихся и исчезнувших работ показывается сверх объяснителей
#: поимённо. «Работы больше нет в файле» — качественно иной факт, чем
#: «подешевела на 3 %», и
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
        cur.execute(
            """
            select aw.work_category_id, sum(aw.total_amount)
            from estimate_additional_works aw
            where aw.proposal_id = %s and aw.work_category_id is not null
            group by aw.work_category_id
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
            key = UNMATCHED_KEY if cat_id is None else cat_id
            group = groups.setdefault(
                key,
                {"id": key, "title": title or "Строки без каталожной привязки",
                 "unmatched": cat_id is None, "stages": {}},
            )
            group["stages"][stage] = {
                "amount": Decimal(amount or 0),
                "rows": rows,
                "volumes": sorted(v for v in volumes if v is not None),
                "unit": unit,
            }
        cur.execute(
            """
            select aw.title, sum(aw.total_amount), count(*)
            from estimate_additional_works aw
            join work_categories wc on wc.id = aw.work_category_id
            where aw.proposal_id = %s and wc.code = %s
            group by aw.title
            """,
            (proposal, code),
        )
        for title, amount, rows in cur.fetchall():
            key = (EXTRA_KEY, title)
            group = groups.setdefault(
                key, {"id": key, "title": title, "extra": True, "stages": {}}
            )
            group["stages"][stage] = {
                "amount": Decimal(amount or 0), "rows": rows, "volumes": [], "unit": None,
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


def sort_key(group: dict) -> tuple[Decimal, int, int, str]:
    """Полный ключ порядка строк разложения — см. `KIND_RANK`."""
    key = group["id"]
    kind = key[0] if isinstance(key, tuple) else key
    rank = KIND_RANK.get(kind, 0)
    catalog_id = key if isinstance(key, int) else 0
    title = key[1] if isinstance(key, tuple) else ""
    return (-abs(contribution(group)), rank, catalog_id, title)


def explainers(groups: list[dict]) -> tuple[list[dict], list[dict]]:
    """Строки, объясняющие COVERAGE движения статьи, и всё остальное.

    Условие отбора буквально:

        abs(article_delta - cumulative) <= (1 - COVERAGE) * abs(article_delta)

    проверяется ПОСЛЕ добавления очередной группы. Вклады гасят друг друга,
    поэтому критерий — близость НАКОПЛЕННОГО вклада к движению статьи, а не доля
    суммы модулей: по модулям набирается больше строк, которые ничего не
    объясняют.

    `article_delta == 0` — законный случай (статья не сдвинулась, хотя внутри
    что-то менялось), и объяснять там нечего: пустой набор УЖЕ удовлетворяет
    условию. Тогда экран несёт только появления с исчезновениями и остаток.
    Прежняя редакция принудительно показывала одну строку — самую крупную по
    модулю, — то есть объявляла объяснителем строку, которая ничего не объясняет
    (замечание внешнего ревью 30.08.2026).

    Ничья по модулю вклада разводится идентификатором каталожной позиции: без
    этого порядок строк зависел бы от порядка выдачи БД.
    """
    total = sum((contribution(g) for g in groups), Decimal(0))
    order = sorted(groups, key=sort_key)
    if total == 0:
        return [], order
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
    """Замер по ВСЕМ статьям участника, а не по одной показанной.

    Группы строятся ТОЙ ЖЕ моделью, что и разложение на экране (`load_groups`):
    позиции, псевдогруппа допработ и псевдогруппа непривязанных строк. Первая
    редакция читала только `position_items`, поэтому опубликованные числа
    объяснителей считались по другому множеству, чем показывает макет: допработы
    статей 3.2, 4.1.3 и 8.2.1 стоят на КОНЦАХ трассы и меняют и движение статьи, и
    отбор строк. Замечание внешнего ревью 30.08.2026; замер обязан воспроизводить
    алгоритм экрана, иначе он измеряет не его.
    """
    per_article: dict[str, dict[object, dict[int, dict]]] = defaultdict(lambda: defaultdict(dict))
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
            key = UNMATCHED_KEY if cat_id is None else cat_id
            per_article[code][key][stage] = {
                "amount": Decimal(amount or 0),
                "rows": rows,
                "volumes": sorted(v for v in volumes if v is not None),
            }
        cur.execute(
            """
            select wc.code, aw.title, sum(aw.total_amount), count(*)
            from estimate_additional_works aw
            join work_categories wc on wc.id = aw.work_category_id
            where aw.proposal_id = %s
            group by wc.code, aw.title
            """,
            (proposal,),
        )
        for code, title, amount, rows in cur.fetchall():
            per_article[code][(EXTRA_KEY, title)][stage] = {
                "amount": Decimal(amount or 0), "rows": rows, "volumes": [],
            }

    needed, sizes, moved, partial, multi, articles = [], [], 0, 0, 0, 0
    outside: list[int] = []
    whole = born = gone = mid = holed = groups_total = 0
    first_stage, last_stage = STAGES[0][0], STAGES[-1][0]
    for groups in per_article.values():
        wrapped = [{"id": cid, "stages": cells} for cid, cells in groups.items()]
        for group in wrapped:
            groups_total += 1
            present = sorted(group["stages"])
            if present != list(range(present[0], present[-1] + 1)):
                holed += 1
            elif len(present) == len(STAGES):
                whole += 1
            elif present[0] != first_stage and present[-1] != last_stage:
                mid += 1
            elif present[0] != first_stage:
                born += 1
            else:
                gone += 1
        total = sum((contribution(g) for g in wrapped), Decimal(0))
        if abs(total) < MOVEMENT_FLOOR:
            continue
        articles += 1
        sizes.append(len(wrapped))
        top, tail = explainers(wrapped)
        needed.append(len(top))
        outside.append(sum(1 for g in tail if len(g["stages"]) < len(STAGES)))
        for group in top:
            if volume_moved(group):
                moved += 1
            if len(group["stages"]) < len(STAGES):
                partial += 1
            if any(cell["rows"] > 1 for cell in group["stages"].values()):
                multi += 1

    # Нулевая сумма и длина наименования — тоже замеры, на которых стоят решения
    # (§2.5 и §2.10 спеки), поэтому считаются здесь, а не разовым скриптом: спека
    # цитирует ТОЛЬКО эту таблицу.
    cur.execute(
        "select count(*) filter (where total_cost_total = 0), count(*) "
        "from position_items where not is_chapter"
    )
    rows_zero, rows_all = cur.fetchone()
    zero_groups = zero_after_priced = 0
    for groups in per_article.values():
        for cells in groups.values():
            values = [cells[s]["amount"] for s in sorted(cells)]
            if any(v == 0 for v in values):
                zero_groups += 1
            priced = False
            for value in values:
                if value != 0:
                    priced = True
                elif priced:
                    zero_after_priced += 1
                    break
    cur.execute(
        """
        select length(cp.standard_job_title),
               position(chr(10) in cp.standard_job_title) > 0
        from catalog_positions cp
        where cp.id in (
            select distinct pi.catalog_position_id from position_items pi
            where pi.proposal_id = any(%s) and not pi.is_chapter
              and pi.catalog_position_id is not null)
        """,
        ([proposal for _, proposal in STAGES],),
    )
    rows_titles = cur.fetchall()
    titles = sorted(r[0] for r in rows_titles)
    titles_multiline = sum(1 for r in rows_titles if r[1])
    cur.execute(
        """
        select length(wc.title)
        from work_categories wc
        where wc.id in (
            select distinct ch.work_category_id
            from position_items pi
            join position_items ch
              on ch.id = pi.chapter_item_id and ch.proposal_id = pi.proposal_id
            where pi.proposal_id = any(%s) and not pi.is_chapter
              and ch.work_category_id is not null)
        """,
        ([proposal for _, proposal in STAGES],),
    )
    article_titles = sorted(r[0] for r in cur.fetchall())

    # Узлы с ПРЯМЫМИ строками и «смешанные» узлы (есть и дети-статьи, и свои
    # работы) решают, где вообще появляется шеврон работ и к какой сумме считать
    # сходимость (§2.1, §2.13) — значит тоже замер, а не наблюдение.
    cur.execute("select id, code, parent_id from work_categories")
    parents = {row[0]: row[2] for row in cur.fetchall()}
    codes = {}
    cur.execute("select id, code from work_categories")
    for cid, code in cur.fetchall():
        codes[cid] = code
    cur.execute(
        """
        select distinct ch.work_category_id
        from position_items pi
        join position_items ch
          on ch.id = pi.chapter_item_id and ch.proposal_id = pi.proposal_id
        where pi.proposal_id = any(%s) and not pi.is_chapter
          and ch.work_category_id is not null
        """,
        ([proposal for _, proposal in STAGES],),
    )
    with_direct = {row[0] for row in cur.fetchall()}
    # Порядок кодов — как в классификаторе («6» раньше «14»), а не лексикографический.
    mixed = [codes[cid] for cid in sorted(
        (cid for cid in with_direct
         if any(parents.get(other) == cid for other in with_direct)),
        key=lambda cid: [int(part) for part in codes[cid].split(".")])]

    # Ветки, недостижимые на стенде: их ноль — тоже факт, и он объясняет, почему
    # DoD требует фикстур, а не зелёного прогона (§5).
    cur.execute(
        """
        select count(*) filter (where pi.catalog_position_id is null),
               count(*) filter (where ch.work_category_id is null),
               count(*) filter (where pi.total_cost_total is null),
               count(*) filter (where pi.total_cost_total in
                                ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
               count(*) filter (where pi.suggested_quantity is null)
        from position_items pi
        left join position_items ch
          on ch.id = pi.chapter_item_id and ch.proposal_id = pi.proposal_id
        where not pi.is_chapter
        """
    )
    no_catalog, no_article, no_amount, not_finite, no_quantity = cur.fetchone()

    ordered = sorted(needed)
    ordered_outside = sorted(outside)

    def percentile(q: float) -> int:
        return ordered[min(len(ordered) - 1, int(len(ordered) * q))]

    def percentile_outside(q: float) -> int:
        return ordered_outside[min(len(ordered_outside) - 1, int(len(ordered_outside) * q))]

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
        "outside_total": sum(outside),
        "outside_median": statistics.median(outside),
        "outside_p90": percentile_outside(0.9),
        "outside_max": max(outside),
        "outside_zero": sum(1 for x in outside if x == 0),
        "outside_over_cap": sum(1 for x in outside if x > PARTIAL_CAP),
        "groups_total": groups_total,
        "whole": whole,
        "born": born,
        "gone": gone,
        "mid": mid,
        "holed": holed,
        "rows_zero": rows_zero,
        "rows_all": rows_all,
        "zero_groups": zero_groups,
        "zero_after_priced": zero_after_priced,
        "titles_n": len(titles),
        "titles_median": statistics.median(titles),
        "titles_p90": titles[min(len(titles) - 1, int(len(titles) * 0.9))],
        "titles_p75": titles[min(len(titles) - 1, int(len(titles) * 0.75))],
        "titles_max": max(titles),
        "titles_multiline": titles_multiline,
        "article_titles_n": len(article_titles),
        "article_titles_median": statistics.median(article_titles),
        "article_titles_p90": article_titles[
            min(len(article_titles) - 1, int(len(article_titles) * 0.9))],
        "article_titles_max": max(article_titles),
        "with_direct": len(with_direct),
        "mixed": mixed,
        "no_catalog": no_catalog,
        "no_article": no_article,
        "no_amount": no_amount,
        "not_finite": not_finite,
        "no_quantity": no_quantity,
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


#: Состояния ячейки — ДОСЛОВНО словарь свода (`services/stage_summary.cell_states`).
#: `absent` (строки нет в файле) и `not_evaluated` (строка есть, сумма ноль, цены
#: не было ни разу) — РАЗНЫЕ факты, и «снято» из них не следует ни то, ни другое:
#: `removed` — это ноль ПОСЛЕ ненулевой суммы. Первая редакция макета печатала
#: «не оценивалась» и «снято» там, где строки в файле просто нет, — то есть
#: утверждала снятие, которого файл не доказывает (замечание внешнего ревью
#: 30.08.2026). Насколько это не мелочь — видно в блоке замеров секции: строк
#: ровно с нулевой суммой и групп с настоящим `removed` там по счётчику.
STATE_ABSENT = "absent"
STATE_NOT_EVALUATED = "not_evaluated"
STATE_REMOVED = "removed"
STATE_AMOUNT = "amount"

STATE_LABEL = {
    STATE_ABSENT: ('<span class="flat">—</span>', "Строки нет в файле этого этапа"),
    STATE_NOT_EVALUATED: ('<span class="pill">не оценивалась</span>',
                          "Строка в файле есть, цены у неё нет"),
    STATE_REMOVED: ('<span class="pill warn">снято</span>',
                    "Строка в файле есть, сумма обнулена после того, как цена была"),
}

#: Виды изменения — та же матрица переходов, что в своде (§2.6 спеки фичи 3).
KIND_LABEL = {
    "appeared": ('<span class="pill">появилась</span>', "Работа появилась в файле"),
    "reappeared": ('<span class="pill">вернулась</span>', "Сумма вернулась после обнуления"),
    "disappeared": ('<span class="pill">нет в файле</span>',
                    "Строка исчезла из файла. Это НЕ доказывает снятия: работу могли "
                    "переименовать, слить с другой строкой или перенести в другую статью"),
    "removed": ('<span class="pill warn">снято</span>', "Сумма обнулена"),
}


def cell_states(gross_by_stage: list[Decimal | None]) -> list[str]:
    """Копия правила свода: `removed` — по ВСЕМ предыдущим этапам, не по предыдущему."""
    states, priced_before = [], False
    for gross in gross_by_stage:
        if gross is None:
            states.append(STATE_ABSENT)
        elif gross != 0:
            states.append(STATE_AMOUNT)
            priced_before = True
        else:
            states.append(STATE_REMOVED if priced_before else STATE_NOT_EVALUATED)
    return states


def change_kind(prev: str, cur: str) -> str | None:
    """Матрица переходов свода, сокращённая до того, что печатается пилюлей."""
    if prev == STATE_AMOUNT and cur == STATE_AMOUNT:
        return None
    if prev == STATE_AMOUNT and cur == STATE_REMOVED:
        return "removed"
    if prev == STATE_AMOUNT and cur == STATE_ABSENT:
        return "disappeared"
    if cur == STATE_AMOUNT and prev == STATE_REMOVED:
        return "reappeared"
    if cur == STATE_AMOUNT:
        return "appeared"
    return None


def pill(markup_and_title: tuple[str, str]) -> str:
    markup, title = markup_and_title
    return markup.replace("<span ", f'<span title="{esc(title)}" ', 1)


def stage_cells(cells: dict[int, dict], *, inline_volume: bool = False,
                group: dict | None = None, bare: bool = False) -> str:
    """Денежные ячейки строки: состояние или сумма, объём, изменение к предыдущему."""
    gross = [None if s not in cells else cells[s]["amount"] for s, _ in STAGES]
    states = cell_states(gross) if not bare else [STATE_AMOUNT] * len(STAGES)
    out = []
    for idx, (stage, _) in enumerate(STAGES):
        state, amount = states[idx], gross[idx]
        kind = change_kind(states[idx - 1], state) if idx else None
        if state != STATE_AMOUNT:
            body = pill(STATE_LABEL[state])
            # «снято» в ячейке уже сказано состоянием — повторять его видом
            # изменения не надо (правило повтора §2.6 спеки фичи 3).
            if kind == "disappeared":
                body = f'{body}<span class="dp">{pill(KIND_LABEL[kind])}</span>'
            out.append(f'<td class="num">{body}</td>')
            continue
        body = f'<span class="mny">{mln(amount)}</span>'
        if inline_volume and group is not None and cells[stage]["volumes"]:
            volumes = "+".join(qty(v) for v in cells[stage]["volumes"])
            tone = " chg" if group["_moved_at"].get(stage) else ""
            body += f'<span class="qty{tone}">{volumes}&nbsp;{esc(cells[stage]["unit"] or "")}</span>'
        if bare or idx == 0:
            pass
        elif kind in KIND_LABEL:
            body += f'<span class="dp">{pill(KIND_LABEL[kind])}</span>'
        elif states[idx - 1] == STATE_AMOUNT:
            text, tone = pct(amount, gross[idx - 1])
            body += f'<span class="dp {tone}">{text}</span>'
        out.append(f'<td class="num">{body}</td>')

    first = gross[0] or Decimal(0)
    last = gross[-1] or Decimal(0)
    delta = last - first
    tone = "flat" if delta == 0 else ("up" if delta > 0 else "down")
    if bare:
        torg = '<span class="flat">—</span>'
    else:
        kind = change_kind(states[0], states[-1])
        if kind in KIND_LABEL:
            torg = pill(KIND_LABEL[kind])
        elif states[0] == STATE_AMOUNT and states[-1] == STATE_AMOUNT:
            text, ptone = pct(last, first)
            torg = f'<span class="{ptone}">{text}</span>'
        else:
            torg = '<span class="flat">—</span>'
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
    if group.get("unmatched"):
        pills += (' <span class="ambig" title="У строк нет каталожной привязки, ключ'
                  ' группы их не берёт, и между этапами они не сопоставляются. На'
                  ' исправных данных этой строки нет вовсе: её появление означает'
                  ' недоработанный матчинг">без каталожной привязки</span>')
    if group.get("extra"):
        pills += (' <span class="ambig" title="Строка «Сведений по дополнительным'
                  ' работам»: каталожной привязки и объёма у неё нет, между этапами'
                  ' такие строки сопоставляются по наименованию">допработы</span>')
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
                    hidden, f"ещё {len(hidden)} {word} появились или исчезли",
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
        ("Групп разложения всего (работы, допработы, непривязанные строки)",
         f"{stats['groups_total']}"),
        ("…есть на всех выбранных этапах", f"{stats['whole']}"),
        ("…появились (нет на первом, есть на последнем)", f"{stats['born']}"),
        ("…исчезли (есть на первом, нет на последнем)", f"{stats['gone']}"),
        ("…есть только на средних этапах (и не на первом, и не на последнем)",
         f"{stats['mid']}"),
        ("…с ДЫРОЙ в середине (пропала и вернулась)", f"{stats['holed']}"),
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
        ("…которые сами есть не на всех этапах", f"{stats['partial']}"),
        ("…у которых на этапе несколько строк сметы", f"{stats['multi']}"),
        ("Появлений и исчезновений ВНЕ объяснителей", f"{stats['outside_total']}"),
        ("…на статью: медиана / девятая дециль / максимум",
         f"{stats['outside_median']:.0f} / {stats['outside_p90']} / {stats['outside_max']}"),
        ("…статей, где их нет вовсе", f"{stats['outside_zero']} из {stats['articles']}"),
        (f"…статей, где их больше {PARTIAL_CAP}", f"{stats['outside_over_cap']}"),
        ("Строк сметы с суммой РОВНО НОЛЬ (вся база)",
         f"{stats['rows_zero']} из {stats['rows_all']}"),
        ("Групп с нулём хотя бы на одном этапе", f"{stats['zero_groups']}"),
        ("…где ноль пришёл ПОСЛЕ ненулевой суммы (состояние «снято»)",
         f"{stats['zero_after_priced']}"),
        ("Длина каталожного наименования: медиана / p75 / p90 / максимум",
         f"{stats['titles_median']:.0f} / {stats['titles_p75']} / {stats['titles_p90']}"
         f" / {stats['titles_max']}"),
        ("…из них с переводом строки внутри",
         f"{stats['titles_multiline']} из {stats['titles_n']}"),
        ("Длина наименования СТАТЬИ: медиана / p90 / максимум",
         f"{stats['article_titles_median']:.0f} / {stats['article_titles_p90']}"
         f" / {stats['article_titles_max']} ({stats['article_titles_n']} статей)"),
        ("Узлов классификатора с ПРЯМЫМИ строками", f"{stats['with_direct']}"),
        ("…из них имеют и детей-статьи, и свои работы",
         ", ".join(stats["mixed"]) or "нет"),
        ("Строк без каталожной привязки / без статьи (вся база)",
         f"{stats['no_catalog']} / {stats['no_article']}"),
        ("Строк без суммы / с неконечной суммой (вся база)",
         f"{stats['no_amount']} / {stats['not_finite']}"),
        ("Строк без suggested_quantity (вся база)", f"{stats['no_quantity']}"),
    ]
    return "".join(f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in measured)


#: Принятый вариант размещения объёма заказчика (решение пользователя 30.08.2026).
ACCEPTED = "v2"


def born_block(born: dict) -> str:
    """Появление и исчезновение работы — на статье, где ровно это и произошло."""
    article = born["article"]
    change = article["cells"][STAGES[-1][0]] - article["cells"][STAGES[0][0]]
    return (
        '<p class="vh">Работа появилась или исчезла из файла: та же грамматика, статья '
        f'{esc(article["code"])}</p>'
        '<p class="secsub">В статье 8.3 все три строки-объяснителя есть на всех '
        'четырёх этапах, поэтому появление и исчезновение там не видно. Вот статья, где '
        'произошло ровно это: на четвёртом этапе подрядчик <b>пересобрал пирог '
        f'подготовки</b>, и статья не подешевела, а ВЫРОСЛА на {mln(change)} млн. '
        'Разложение показывает, чем: одни работы появились, другие исчезли.</p>'
        + fragment(born["root"], born["kids"], article, born, "v2b")
        + '<div class="verdict"><b>Правило: «нет в файле» и «снято» — РАЗНЫЕ факты, и '
        'экран их не смешивает.</b> Строки, которой на этапе нет вовсе, состояние — '
        '<span class="flat">—</span>, а переход к нему помечается пилюлей '
        '<span class="pill">нет в файле</span>: исчезновение строки из файла НЕ '
        'доказывает снятия работы — её могли переименовать, слить с другой строкой или '
        'перенести в другую статью. Пилюля <span class="pill warn">снято</span> '
        'достаётся только строке, которая в файле ЕСТЬ, а сумма у неё обнулена после '
        'того, как цена была; <span class="pill">не оценивалась</span> — строке, которая '
        'есть, но цены у неё не было ни разу. Словарь и матрица переходов взяты у свода '
        'целиком, новых слов не заводится. В колонке «Торг» процента у такой строки не '
        'бывает: делить не на что, там стоит та же пилюля. А вот <b>вклад считается от '
        'нуля</b>: работа, которой на первом этапе не было, а на последнем есть на '
        '22,8 млн, изменила статью ровно на эти 22,8 млн. Иначе появившаяся работа '
        'выпала бы из разложения, а разницу молча унесла бы строка «прочие».</div>'
        '<div class="state info"><b>Второй сюжет той же таблицы.</b> Скачок на '
        'втором этапе, 25,8 → 58,9, сделан не работами, а ветвью ДОПОЛНИТЕЛЬНЫХ '
        'РАБОТ: 34,8 млн, которых нет ни на одном другом этапе. Вклад у этой строки '
        'нулевой — на первом и последнем этапах её нет, — и в объяснители изменения '
        'она не попадает; показана она потому, что правило «появившееся и исчезнувшее '
        'видно всегда» распространяется и на неё.</div>'
        '<div class="state info"><b>Чего экран не утверждает.</b> Здесь видно, что '
        '«Геотекстиль 500 г/м2» исчез из файла, а «Геотекстильное полотно 150 г/м2» '
        'появилось — на том же объёме 8 726,4 м². Человек прочитает это как ЗАМЕНУ '
        'материала, и скорее всего будет прав. Но у нас нет основания это утверждать: '
        'каталожные позиции разные, и «замена» — вывод читателя, а не факт файла. Экран '
        'ставит исчезновение и появление рядом и молчит о связи между ними.</div>'
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
      «Строка-объяснитель» — ГРУППА РАЗЛОЖЕНИЯ, попавшая в число тех, чей накопленный
      вклад объясняет 90 % движения статьи. Групп три вида, и ключ у каждого свой:
      работа — «статья + каталожная позиция», допработа — «статья + наименование»,
      строки без каталожной привязки — одна группа на статью. Критерий отбора —
      близость НАКОПЛЕННОГО вклада к движению статьи, а не доля суммы модулей:
      вклады гасят друг друга, и по модулям набралось бы больше строк, чем нужно.</p>
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
    {plural(len(partials), ("работа", "работы", "работ"))}, которые появились или
    исчезли из файла: их показывают ВСЕГДА, какой бы малой ни была сумма, потому
    что «работы больше нет в файле» — другой факт, чем «подешевела на 3 %».
    Остальные
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
      <li><b>Состояния и виды изменения взяты у свода ТИПАМИ, а не по смыслу.</b>
      Строки нет в файле — <span class="flat">—</span>, переход к этому даёт
      <span class="pill">нет в файле</span>; строка есть и не оценена —
      <span class="pill">не оценивалась</span>; сумма обнулена после ненулевой —
      <span class="pill warn">снято</span>; появление —
      <span class="pill">появилась</span>, возврат суммы —
      <span class="pill">вернулась</span>. Смешивать «нет в файле» со «снято»
      нельзя: исчезновение строки снятия НЕ доказывает. В колонке «Торг» у такой
      строки стоит пилюля, а не процент. А вот ВКЛАД считается от нуля — работа,
      которой на первом этапе не было, изменила статью ровно на свою сумму.</li>
      <li><b>Появившиеся и исчезнувшие работы показываются поимённо всегда</b>, даже
      когда их сумма мала и в объяснители они не попали (решение пользователя
      30.08.2026). Сверх {PARTIAL_CAP} они сворачиваются в строку «ещё N работ
      появились или исчезли». Замер: таких строк вне объяснителей
      {stats['outside_total']} на {stats['articles']} статей — медиана
      {stats['outside_median']:.0f} на статью, девятая дециль {stats['outside_p90']},
      максимум {stats['outside_max']}; у {stats['outside_zero']} статей их нет вовсе,
      больше {PARTIAL_CAP} — у {stats['outside_over_cap']}.
      Из {stats['explainers']} строк-объяснителей {stats['partial']} и сами есть не
      на всех этапах.</li>
      <li><b>Дыра в середине — не гипотеза, а факт стенда.</b> Из
      {stats['groups_total']} групп {stats['whole']} идут сквозь все выбранные этапы,
      {stats['born']} появились, {stats['gone']} исчезли, {stats['mid']} есть только
      на средних, и {stats['holed']} пропадает в середине и возвращается: это
      ДОПРАБОТЫ статьи 3.2 — 29,5 млн на первом этапе, ничего на втором и третьем,
      12,7 млн на четвёртом. Средние этапы получают <span class="flat">—</span>, переход
      к ним — <span class="pill">нет в файле</span>, возврат —
      <span class="pill">появилась</span>, а «Торг» считается процентом по концам.
      Пилюля <span class="pill warn">снято</span> здесь не появляется ни разу, и это
      правильно: файл не говорит, что работу сняли. Случай виден только потому, что
      замер строит группы ТОЙ ЖЕ моделью, что и экран: пока он читал одни
      <code>position_items</code>, дыр «не было ни одной».</li>
      <li><b>Неоднозначная группа помечается пилюлей «несколько строк сметы»</b> —
      решение секции <code>#naming</code>. Таких среди объяснителей
      {stats['multi']}.</li>
      <li><b>Дополнительные работы перечисляются ПОСТРОЧНО, каждая со своим
      наименованием</b> — тот же приём, что в паспорте проекта
      (<code>_extras_by_category</code> ходит мимо VIEW именно потому, что там
      деньги свёрнуты в одну сумму на статью, а экрану нужны строки). Свод считает
      статью по ОБЕИМ ветвям <code>v_category_totals</code>, поэтому без этих строк
      итог не сошёлся бы из показанных — что макет 30.08.2026 сначала и делал, пока
      сходимость не была померена машиной. Ключ между этапами у допработы —
      НАИМЕНОВАНИЕ: это осознанная эвристика, а не вывод матчера, и её промах виден
      читателю глазами. Объёма у допработ нет, поэтому второго этажа в ячейке нет, а
      пилюля «допработы» говорит, что это другая ветвь файла.</li>
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
    partials = sorted((g for g in rest if is_partial(g)), key=sort_key)
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
