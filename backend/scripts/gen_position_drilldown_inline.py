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

# Скрипт запускается из `backend/`, но `python scripts/...` кладёт в путь
# каталог скрипта, а не корень пакета — поэтому корень добавляется явно.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from money.vat import gross_to_net  # noqa: E402

#: Трассы стенда, на которых стоит макет. Обе — ООО «АНТТЕК», четыре этапа.
#: Второй тендер добавлен 30.08.2026 и изменил дизайн: у него РАСХОДЯТСЯ ставки
#: НДС (20, 20, 22, 22), то есть свод показывает нетто, и в нём происходит
#: уточнение классификации между этапами — работы съезжают в дочерние статьи.
TRACE_ONE = [(1, 27), (2, 29), (3, 33), (4, 38)]
TRACE_TWO = [(1, 47), (2, 52), (3, 57), (4, 62)]

#: Текущая трасса; переключается в `main` на каждый случай.
STAGES: list[tuple[int, int]] = TRACE_ONE
ARTICLE = "8.3"
ROOT = "8"
#: Вторая статья: на четвёртом этапе подрядчик пересобрал пирог подготовки —
#: одни работы исчезли из файла, другие появились. Разложение обязано это
#: показывать, и «исчезла» здесь не синоним «снята» (см. STATE_LABEL).
#: Ось показа и ставки этапов; заполняется в `main` на каждый случай.
TAX: tuple[str, dict] = ("gross", {})

#: Ключи псевдогрупп: у допработ и у строк без каталожной привязки нет
#: каталожной позиции, поэтому ключ группы (§2.2) их не берёт, а показать их
#: обязаны — иначе их деньги выпадут из сходимости.
#:
#: Допработы перечисляются ПОСТРОЧНО, как в паспорте (`_extras_by_category`),
#: и ключ у них — `chapter_ref_raw`, ссылка «3.2.2» из «Сведений». Первое
#: основание ключа — уникальность — второй тендер ОПРОВЕРГ: на строках со
#: статьёй ссылка даёт группы с несколькими строками (блок замеров), причём
#: наименования внутри таких групп СОВПАДАЮТ, а суммы разные — файл дробит
#: одну работу на строки. Действующее основание другое: ссылка ГРУППИРУЕТ
#: одну работу, а группа из нескольких строк помечается пилюлей «несколько
#: строк сметы», как у позиций. Наименование ключом быть не может по-прежнему:
#: оно склеивает РАЗНЫЕ работы (от пары 4.2.4 предложений 34 и 39 до «Стен»
#: с четырьмя разными ссылками под одним словом), и такая склейка невидима.
#: Ссылка не повторяется в разных статьях одного предложения (0 случаев),
#: поэтому переход на поддерево (§2.1) новых склеек не добавил.
#: Внутри статьи ссылка есть ВСЕГДА: `ck_..._unresolved_ref` разрешает NULL
#: только вместе с NULL-статьёй, и таких строк со статьёй в базе ноль.
#: Подпись строки — наименование с последнего этапа, где работа есть.
EXTRA_KEY = "additional_works"
UNMATCHED_KEY = "unmatched"
#: Порядок видов строк при равном по модулю вкладе: сначала работы, затем
#: допработы, затем непривязанные строки. Ничья по модулю вклада возможна
#: (нули, симметричные суммы), и без полного ключа порядок зависел бы от
#: порядка выдачи БД. Сравнивать id СТРОКОЙ нельзя: "10" встанет раньше "2".
KIND_RANK = {EXTRA_KEY: 1, UNMATCHED_KEY: 2}
ARTICLE_BORN = "3.1"
ROOT_BORN = "3"
#: Третий случай: статья 10.6 второго тендера. На ней разом видно ось нетто,
#: родителя с большим поддеревом и уточнение классификации без ложных «нет в
#: файле»: собственные суммы 10.6 идут 83,7 → 81,2 → 0 → 0, а поддерево
#: 83,7 → 91,0, потому что работы съехали в 10.6.1 и 10.6.3.
ARTICLE_MOVE = "10.6"
ROOT_MOVE = "10"
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
#: но на хвосте их бывает много, и там остаток сворачивается.
#:
#: Потолок 10 — пересчёт 30.08.2026 ПОСЛЕ перехода на поддерево и на втором
#: тендере: девятая дециль стала 6 и 9 против прежних 3, потому что раскрытие
#: узла показывает появления всего поддерева, а не только собственных строк.
#: Прежние 5 выведены из замера, которого больше нет.
PARTIAL_CAP = 10


def tax_basis(cur) -> tuple[str, dict[int, Decimal]]:
    """Ось показа и ставки этапов — правило свода (`pick_tax_basis`, `to_shown`).

    Тендер «Cityzen Tr. 1 UB8b» дал первый на стенде случай РАСХОДЯЩИХСЯ ставок
    (20, 20, 22, 22): при нескольких известных ставках свод показывает нетто, и
    разложение обязано считать в тех же величинах — иначе оно не сойдётся к
    строке, под которой стоит.
    """
    rates: dict[int, Decimal] = {}
    for stage, proposal in STAGES:
        cur.execute(
            """
            select coalesce(e.vat_rate_base_override, p.vat_rate)
            from proposals p
            join lots l on l.id = p.lot_id
            join estimates e on e.id = l.estimate_id
            where p.id = %s
            """,
            (proposal,),
        )
        rates[stage] = cur.fetchone()[0]
    known = {r for r in rates.values() if r is not None}
    return ("gross" if len(known) <= 1 else "net"), rates


def to_shown(gross: Decimal, stage: int) -> Decimal:
    """Валовая сумма в величину показа: при расходящихся ставках — нетто."""
    basis, rates = TAX
    rate = rates.get(stage)
    if basis == "gross" or rate is None:
        return gross
    return gross_to_net(gross, rate)


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
            direct[cid][stage] += to_shown(Decimal(amount or 0), stage)
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
            direct[cid][stage] += to_shown(Decimal(amount or 0), stage)

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


def subtree_ids(cur, code: str) -> list[int]:
    """Идентификаторы статьи `code` и ВСЕХ её потомков.

    Разложение раскрывает поддерево, а не собственные строки узла: строка свода
    несёт итог поддерева, и объяснять она обязана именно его. Ревизия §2.1 от
    30.08.2026 по второму тендеру стенда, где у статьи 10.6 собственные суммы
    83,7 → 81,2 → 0 → 0, а поддерево 83,7 → 91,0: разложение по собственным
    строкам написало бы «нет в файле» на каждой работе под строкой «+8,7 %».
    Причина не в переезде денег, а в уточнении классификации на поздних этапах:
    158 каталожных позиций съехали в ДОЧЕРНЮЮ статью, вверх ноль, вбок 51.
    """
    cur.execute("select id, code, parent_id from work_categories")
    rows = cur.fetchall()
    children = defaultdict(list)
    by_code = {}
    for cid, node_code, parent in rows:
        children[parent].append(cid)
        by_code[node_code] = cid

    def walk(cid: int) -> list[int]:
        out = [cid]
        for kid in children[cid]:
            out += walk(kid)
        return out

    return walk(by_code[code])


def load_groups(cur, code: str) -> list[dict]:
    """Группы разложения ПОДДЕРЕВА статьи `code` с суммами и объёмами по этапам.

    Статья в ключ не входит: работа, уточнившая статью внутри поддерева (родитель
    → потомок), обязана остаться ОДНОЙ строкой, иначе экран объявит исчезновение
    там, где ничего не изменилось. См. `subtree_ids`.
    """
    ids = subtree_ids(cur, code)
    groups: dict[object, dict] = {}
    for stage, proposal in STAGES:
        cur.execute(
            """
            select pi.catalog_position_id, min(cp.standard_job_title),
                   sum(pi.total_cost_total), count(*),
                   array_agg(distinct pi.suggested_quantity), min(u.symbol)
            from position_items pi
            join position_items ch
              on ch.id = pi.chapter_item_id and ch.proposal_id = pi.proposal_id
            left join catalog_positions cp on cp.id = pi.catalog_position_id
            left join units_of_measure u on u.id = pi.unit_id
            where pi.proposal_id = %s and not pi.is_chapter
              and ch.work_category_id = any(%s)
            group by pi.catalog_position_id
            """,
            (proposal, ids),
        )
        for cat_id, title, amount, rows, volumes, unit in cur.fetchall():
            key = UNMATCHED_KEY if cat_id is None else cat_id
            group = groups.setdefault(
                key,
                {"id": key, "title": title or "Строки без каталожной привязки",
                 "unmatched": cat_id is None, "stages": {}},
            )
            group["stages"][stage] = {
                "amount": to_shown(Decimal(amount or 0), stage),
                "rows": rows,
                "volumes": sorted(v for v in volumes if v is not None),
                "unit": unit,
            }
        cur.execute(
            """
            -- Подпись группы — наименование строки с наименьшим `ordinal`, то
            -- есть ПЕРВОЙ по файлу: `min(title)` брал бы алфавитно, а это
            -- произвол. `ordinal` — порядок «Сведений», уникальный в пределах
            -- предложения; тем же ключом упорядочивает допработы паспорт.
            select aw.chapter_ref_raw, (array_agg(aw.title order by aw.ordinal))[1],
                   sum(aw.total_amount), count(*)
            from estimate_additional_works aw
            where aw.proposal_id = %s and aw.work_category_id = any(%s)
            group by aw.chapter_ref_raw
            """,
            (proposal, ids),
        )
        for ref, title, amount, rows in cur.fetchall():
            key = (EXTRA_KEY, ref)
            group = groups.setdefault(
                key, {"id": key, "title": title, "extra": True, "ref": ref, "stages": {}}
            )
            # Подпись — с ПОСЛЕДНЕГО этапа, где работа есть: этапы читаются по
            # возрастанию, поэтому присваивание перетирает прежнее. Внутри этапа
            # берётся первая по файлу строка группы (см. SQL выше).
            group["title"] = title
            group["stages"][stage] = {
                "amount": to_shown(Decimal(amount or 0), stage),
                "rows": rows, "volumes": [], "unit": None,
            }
    return list(groups.values())


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
    ref = key[1] if isinstance(key, tuple) else ""
    return (-abs(contribution(group)), rank, catalog_id, ref)


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
    """Замер по ВСЕМ статьям участника — той же моделью, что и экран.

    С 30.08.2026 модель — ПОДДЕРЕВО (§2.1): единица замера — узел классификатора,
    группы собираются по всем его потомкам, ключ работы — только каталожная
    позиция. До ревизии замер считал собственные строки узла и потому мерил не
    тот экран: половина «появлений» и «исчезновений» второго тендера стенда
    оказалась уточнением классификации внутри поддерева.
    """
    cur.execute("select id, code, parent_id from work_categories")
    rows = cur.fetchall()
    children = defaultdict(list)
    codes = {}
    for cid, code, parent in rows:
        children[parent].append(cid)
        codes[cid] = code

    # (категория, ключ группы) -> этап -> ячейка
    direct: dict[int, dict[object, dict[int, dict]]] = defaultdict(lambda: defaultdict(dict))
    for stage, proposal in STAGES:
        cur.execute(
            """
            select ch.work_category_id, pi.catalog_position_id,
                   sum(pi.total_cost_total), count(*), array_agg(distinct pi.suggested_quantity)
            from position_items pi
            join position_items ch
              on ch.id = pi.chapter_item_id and ch.proposal_id = pi.proposal_id
            where pi.proposal_id = %s and not pi.is_chapter
              and ch.work_category_id is not null
            group by ch.work_category_id, pi.catalog_position_id
            """,
            (proposal,),
        )
        for cid, cat_id, amount, count, volumes in cur.fetchall():
            key = UNMATCHED_KEY if cat_id is None else cat_id
            direct[cid][key][stage] = {
                "amount": to_shown(Decimal(amount or 0), stage),
                "rows": count,
                "volumes": sorted(v for v in volumes if v is not None),
            }
        cur.execute(
            """
            select aw.work_category_id, aw.chapter_ref_raw, sum(aw.total_amount), count(*)
            from estimate_additional_works aw
            where aw.proposal_id = %s and aw.work_category_id is not null
            group by aw.work_category_id, aw.chapter_ref_raw
            """,
            (proposal,),
        )
        for cid, ref, amount, count in cur.fetchall():
            direct[cid][(EXTRA_KEY, ref)][stage] = {
                "amount": to_shown(Decimal(amount or 0), stage),
                "rows": count, "volumes": [],
            }

    def walk(cid: int) -> list[int]:
        out = [cid]
        for kid in children[cid]:
            out += walk(kid)
        return out

    def subtree_groups(cid: int) -> dict[object, dict[int, dict]]:
        """Группы поддерева: одна каталожная позиция — одна группа, статья в ключ
        не входит, поэтому уточнение статьи внутри поддерева группу не рвёт."""
        merged: dict[object, dict[int, dict]] = defaultdict(dict)
        for node in walk(cid):
            for key, cells in direct[node].items():
                for stage, cell in cells.items():
                    prev = merged[key].get(stage)
                    if prev is None:
                        merged[key][stage] = dict(cell)
                    else:
                        prev["amount"] += cell["amount"]
                        prev["rows"] += cell["rows"]
                        prev["volumes"] = sorted(set(prev["volumes"]) | set(cell["volumes"]))
        return merged

    needed, sizes, moved, partial, multi, articles = [], [], 0, 0, 0, 0
    outside: list[int] = []
    with_drilldown = wider = no_own = 0
    first_stage, last_stage = STAGES[0][0], STAGES[-1][0]

    # Классы присутствия меряются ПО ВСЕЙ СМЕТЕ, а не по узлам: под моделью
    # поддерева одна и та же группа живёт в каждом предке, и счёт по узлам
    # умножал бы её на глубину. Глобальный счёт отвечает на вопрос «сколько работ
    # действительно появилось и исчезло», очищенный и от глубины дерева, и от
    # уточнения классификации — статья в ключ не входит.
    whole = born = gone = mid = holed = zero_some = zero_after = 0
    global_groups: dict[object, dict[int, dict]] = defaultdict(dict)
    for node_groups in direct.values():
        for key, cells in node_groups.items():
            for stage, cell in cells.items():
                prev = global_groups[key].get(stage)
                if prev is None:
                    global_groups[key][stage] = dict(cell)
                else:
                    prev["amount"] += cell["amount"]
                    prev["rows"] += cell["rows"]
    groups_total = len(global_groups)
    for cells in global_groups.values():
        present = sorted(cells)
        # Ноль — состояние строки («снято», §2.5), а не её отсутствие; счёт
        # идёт ТОЙ ЖЕ моделью групп, что и экран, — прежние 507/294 считались
        # ключом «статья + позиция» и с ревизией §2.1 перестали быть замером.
        zeros = [s for s in present if cells[s]["amount"] == 0]
        if zeros:
            zero_some += 1
            nonzero = [s for s in present if cells[s]["amount"] != 0]
            if nonzero and max(zeros) > min(nonzero):
                zero_after += 1
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

    for cid in codes:
        groups = subtree_groups(cid)
        if not groups:
            continue
        with_drilldown += 1
        if len(groups) > len(direct[cid]):
            wider += 1
        # Живой случай для has_drilldown_rows (§2.1): кнопка обязана появиться
        # у узла, все строки которого лежат у потомков.
        if not direct[cid]:
            no_own += 1
        wrapped = [{"id": key, "stages": cells} for key, cells in groups.items()]
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

    cur.execute(
        "select count(*) filter (where total_cost_total = 0), count(*) "
        "from position_items where not is_chapter"
    )
    rows_zero, rows_all = cur.fetchone()
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

    # Допработы: основание ключа §2.7 обязано пересниматься с базы, потому что
    # первый его вариант («ссылка уникальна») второй тендер опроверг. Все числа
    # — вся база; область группировки — та же, что у экрана: строки со статьёй,
    # ключ `chapter_ref_raw`.
    cur.execute(
        """
        select count(*), count(distinct proposal_id),
               count(*) filter (where work_category_id is not null),
               count(*) filter (where work_category_id is not null
                                and chapter_ref_raw is null)
        from estimate_additional_works
        """
    )
    extras_rows, extras_proposals, extras_with_article, extras_article_no_ref = cur.fetchone()
    cur.execute(
        """
        select count(*), count(*) filter (where n > 1),
               count(*) filter (where n > 1 and titles > 1),
               coalesce(string_agg(n::text, ', ' order by n desc)
                        filter (where n > 1), '')
        from (select count(*) as n, count(distinct title) as titles
              from estimate_additional_works
              where work_category_id is not null
              group by proposal_id, work_category_id, chapter_ref_raw) g
        """
    )
    (extras_groups_ref, extras_collisions,
     extras_collisions_diff_title, extras_collision_sizes) = cur.fetchone()
    cur.execute(
        """
        select count(*), count(*) filter (where refs > 1)
        from (select count(distinct chapter_ref_raw) as refs
              from estimate_additional_works
              where work_category_id is not null
              group by proposal_id, work_category_id, title) g
        """
    )
    extras_groups_title, extras_title_merges = cur.fetchone()
    cur.execute(
        """
        select count(*) from (
          select 1 from estimate_additional_works
          where work_category_id is not null
          group by proposal_id, chapter_ref_raw
          having count(distinct work_category_id) > 1) g
        """
    )
    extras_cross_article = cur.fetchone()[0]
    cur.execute(
        """
        with recursive tree as (
          select id, parent_id, id as root from work_categories
          union all
          select wc.id, wc.parent_id, t.root
          from work_categories wc join tree t on wc.parent_id = t.id
        ),
        direct_rows as (
          select pi.proposal_id as prop, ch.work_category_id as cid,
                 count(*) as np, 0 as ne
          from position_items pi
          join position_items ch
            on ch.id = pi.chapter_item_id and ch.proposal_id = pi.proposal_id
          where not pi.is_chapter and ch.work_category_id is not null
          group by 1, 2
          union all
          select proposal_id, work_category_id, 0, count(*)
          from estimate_additional_works
          where work_category_id is not null
          group by 1, 2
        ),
        per_node as (
          select d.prop, t.root, sum(d.np) as np, sum(d.ne) as ne
          from direct_rows d join tree t on t.id = d.cid
          group by 1, 2
        )
        select count(*) filter (where ne > 0),
               count(*) filter (where ne > 0 and np = 0)
        from per_node
        """
    )
    extras_subtree_pairs, extras_only_subtrees = cur.fetchone()

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
        "explainers": sum(needed),
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
        "titles_n": len(titles),
        "titles_median": statistics.median(titles),
        "titles_p75": titles[min(len(titles) - 1, int(len(titles) * 0.75))],
        "titles_p90": titles[min(len(titles) - 1, int(len(titles) * 0.9))],
        "titles_max": max(titles),
        "titles_multiline": titles_multiline,
        "with_drilldown": with_drilldown,
        "wider": wider,
        "no_own": no_own,
        "zero_some": zero_some,
        "zero_after": zero_after,
        "extras_rows": extras_rows,
        "extras_proposals": extras_proposals,
        "extras_with_article": extras_with_article,
        "extras_article_no_ref": extras_article_no_ref,
        "extras_groups_ref": extras_groups_ref,
        "extras_collisions": extras_collisions,
        "extras_collisions_diff_title": extras_collisions_diff_title,
        "extras_collision_sizes": extras_collision_sizes,
        "extras_groups_title": extras_groups_title,
        "extras_title_merges": extras_title_merges,
        "extras_cross_article": extras_cross_article,
        "extras_subtree_pairs": extras_subtree_pairs,
        "extras_only_subtrees": extras_only_subtrees,
        "no_catalog": no_catalog,
        "no_article": no_article,
        "no_amount": no_amount,
        "not_finite": not_finite,
        "no_quantity": no_quantity,
        "basis": TAX[0],
        "rates": sorted({str(r) for r in TAX[1].values()}),
    }


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


def article_row(node: dict, level: int, *, key: str | None = None,
                works: int | None = None, works_key: str = "") -> str:
    """Строка статьи. `key` — шеврон классификатора, `works` — действие «Работы».

    Действие подписано словом, а не вторым шевроном: под строкой статьи живут ДВА
    разных разложения одного и того же числа — по подстатьям и по работам, — и
    одинаковые треугольники читались бы как один список (§2.1).
    """
    cells = {s: {"amount": a} for s, a in node["cells"].items()}
    chevron = (f'<button class="tw" aria-expanded="true" data-k="{key}">▾</button>'
               if key else '<span class="tw" aria-hidden="true"> </span>')
    action = ""
    if works is not None:
        action = (f' <button class="works" type="button" aria-expanded="true"'
                  f' data-k="{works_key}"><span class="chev">▾</span> Работы'
                  f'<span class="cnt"> · {works}</span></button>')
    label = (f'<td class="t">{chevron}<span class="code">{esc(node["code"])}</span>'
             f'{esc(node["title"])}{action}</td>')
    return (f'<tr class="lvl{level} art">{label}{stage_cells(cells)}</tr>')


def works_head(article: dict, variant: str) -> str:
    """Заголовок блока работ — разделитель между двумя разложениями.

    Без него строки работ читаются как продолжение списка подстатей, то есть как
    слагаемые к уже видимым слагаемым, и сумма на глаз удваивается.
    """
    span = len(STAGES) + 3
    return (f'<tr class="workshead kid k-{variant}r k-{variant}w"><td colspan="{span}">'
            f'Почему изменился итог {esc(article["code"])} — работы статьи и подстатей'
            f'<span class="whsub">объясняют ТУ ЖЕ сумму, что и строки подстатей выше, '
            f'другим разрезом; итог сходится из показанного</span></td></tr>')


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
        ref = esc(str(group.get("ref") or "—"))
        pills += (f' <span class="ambig" title="Строка «Сведений по дополнительным'
                  f' работам», ссылка {ref}: каталожной привязки и объёма у неё нет,'
                  f' между этапами такие строки сопоставляются ПО ССЫЛКЕ, а подпись'
                  f' берётся с последнего этапа">допработы · {ref}</span>')
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
    return f'<tr class="pos3 kid k-{variant}r k-{variant}w"><td class="t">{name}{pills}</td>{cells}</tr>'


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
    return (f'<tr class="pos3 {css} kid k-{variant}r k-{variant}w">{body}'
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
    return (f'<tr class="pos3 dimrow kid k-{variant}r k-{variant}w">{label}'
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
            own_kids = case.get("article_kids") or []
            rows.append(article_row(
                kid, 2, key=f"{variant}k" if own_kids else None,
                # N на кнопке — число ГРУПП разложения поддерева, включая
                # свёрнутые в «ещё…» и «прочие»: кнопка обещает объём блока,
                # и число не зависит от того, что сейчас раскрыто (§2.1).
                works=len(case["top"]) + len(case["partials"])
                + len(case["partials_hidden"]) + len(case["rest"]),
                works_key=f"{variant}w"))
            for own in own_kids:
                rows.append(article_row(own, 3).replace(
                    'class="lvl3 art"',
                    f'class="lvl3 art kid k-{variant}r k-{variant}k"'))
            rows.append(works_head(article, variant))
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
/* Действие «Работы» — подписанное, а не второй шеврон: разложений под строкой
   статьи два (по подстатьям и по работам), и различать их обязано слово. */
.works { border:1px solid var(--bd); background:var(--surface); color:var(--fg2);
  border-radius:6px; font:inherit; font-size:11px; padding:0 6px; margin-left:8px;
  cursor:pointer; white-space:nowrap; vertical-align:1px; }
.works:hover { background:var(--hover); color:var(--fg); }
.works .chev { color:var(--fg3); }
/* Класс подписи назван `whsub`, а не `sub`: `.sub` на этой странице уже занят
   подзаголовком страницы и несёт `max-width:70ch`, из-за чего текст заголовка
   вёрстся в 486 px посреди строки шириной 1238 (замер 30.08.2026). */
tr.workshead > td { background:var(--sechead); border-bottom:1px solid var(--bd);
  font-size:11px; letter-spacing:.04em; text-transform:uppercase; color:var(--fg3);
  font-weight:600; padding:7px 11px 6px 52px; }
tr.workshead .whsub { display:block; margin-top:2px; font-size:11px; letter-spacing:0;
  text-transform:none; font-weight:400; color:var(--fg4); }
tr.lvl3 > td { background:var(--sunken); font-weight:400; }
tr.lvl3 .t { padding-left:52px; color:var(--fg2); }
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


#: Метрики блока замеров: подпись и как достать значение из `stats`.
#: Колонок в таблице столько, сколько замеренных трасс — числа одного тендера
#: слишком легко принять за свойство задачи, и второй тендер стенда это показал:
#: сквозных групп 78 % против 27 %, объём двигался у 51 % строк против 13 %.
MEASURES: list[tuple[str, object]] = [
    ("Ось показа и ставки этапов",
     lambda d: f"{d['basis']} ({', '.join(d['rates'])})"),
    ("Групп работ во ВСЕЙ смете (ключ без статьи)",
     lambda d: f"{d['groups_total']}"),
    ("…есть на всех выбранных этапах", lambda d: f"{d['whole']}"),
    ("…появились (нет на первом, есть на последнем)", lambda d: f"{d['born']}"),
    ("…исчезли (есть на первом, нет на последнем)", lambda d: f"{d['gone']}"),
    ("…есть только на средних этапах", lambda d: f"{d['mid']}"),
    ("…с ДЫРОЙ в середине (пропала и вернулась)", lambda d: f"{d['holed']}"),
    ("Статей с движением больше миллиона", lambda d: f"{d['articles']}"),
    ("Строк, объясняющих 90 % движения: медиана", lambda d: f"{d['median_needed']:.0f}"),
    ("…три четверти статей", lambda d: f"{d['p75_needed']} и меньше"),
    ("…девятая дециль", lambda d: f"{d['p90_needed']}"),
    ("…худшая статья", lambda d: f"{d['max_needed']}"),
    ("Статей, где хватает пяти строк", lambda d: f"{d['under5']} из {d['articles']}"),
    ("Статей, где нужно больше пятнадцати", lambda d: f"{d['over15']}"),
    ("Групп в поддереве статьи: медиана / максимум",
     lambda d: f"{d['median_size']:.0f} / {d['max_size']}"),
    ("Строк-объяснителей всего", lambda d: f"{d['explainers']}"),
    ("…у которых ДВИГАЛСЯ объём заказчика",
     lambda d: f"{d['moved']} ({d['moved'] * 100 // max(d['explainers'], 1)} %)"),
    ("…которые сами есть не на всех этапах", lambda d: f"{d['partial']}"),
    ("…у которых на этапе несколько строк сметы", lambda d: f"{d['multi']}"),
    ("Появлений и исчезновений ВНЕ объяснителей", lambda d: f"{d['outside_total']}"),
    ("…на статью: медиана / девятая дециль / максимум",
     lambda d: f"{d['outside_median']:.0f} / {d['outside_p90']} / {d['outside_max']}"),
    ("…статей, где их нет вовсе", lambda d: f"{d['outside_zero']} из {d['articles']}"),
    (f"…статей, где их больше {PARTIAL_CAP}", lambda d: f"{d['outside_over_cap']}"),
    ("Узлов, у которых в поддереве есть строки", lambda d: f"{d['with_drilldown']}"),
    ("…где поддерево ШИРЕ собственных строк узла", lambda d: f"{d['wider']}"),
    ("…где СОБСТВЕННЫХ строк нет вовсе", lambda d: f"{d['no_own']}"),
    ("Групп с нулевой суммой хотя бы на одном этапе",
     lambda d: f"{d['zero_some']}"),
    ("…где ноль пришёл ПОСЛЕ ненулевой суммы («снято»)",
     lambda d: f"{d['zero_after']}"),
    ("Допработ: строк / предложений / строк со статьёй (вся база)",
     lambda d: f"{d['extras_rows']} / {d['extras_proposals']}"
               f" / {d['extras_with_article']}"),
    ("…групп по ссылке / по наименованию (строки со статьёй)",
     lambda d: f"{d['extras_groups_ref']} / {d['extras_groups_title']}"),
    ("…ссылок с несколькими строками (размеры групп)",
     lambda d: f"{d['extras_collisions']} ({d['extras_collision_sizes']})"),
    ("…из них с РАЗНЫМИ наименованиями внутри",
     lambda d: f"{d['extras_collisions_diff_title']}"),
    ("…наименований, склеивающих РАЗНЫЕ ссылки",
     lambda d: f"{d['extras_title_merges']}"),
    ("…ссылка в двух статьях предложения / со статьёй без ссылки",
     lambda d: f"{d['extras_cross_article']} / {d['extras_article_no_ref']}"),
    ("Поддеревьев с допработами / из них БЕЗ строк сметы (вся база)",
     lambda d: f"{d['extras_subtree_pairs']} / {d['extras_only_subtrees']}"),
    ("Длина каталожного наименования: медиана / p75 / p90 / максимум",
     lambda d: f"{d['titles_median']:.0f} / {d['titles_p75']} / {d['titles_p90']}"
               f" / {d['titles_max']}"),
    ("…из них с переводом строки внутри",
     lambda d: f"{d['titles_multiline']} из {d['titles_n']}"),
    ("Строк сметы с суммой РОВНО НОЛЬ (вся база)",
     lambda d: f"{d['rows_zero']} из {d['rows_all']}"),
    ("Строк без каталожной привязки / без статьи (вся база)",
     lambda d: f"{d['no_catalog']} / {d['no_article']}"),
    ("Строк без суммы / с неконечной суммой (вся база)",
     lambda d: f"{d['no_amount']} / {d['not_finite']}"),
    ("Строк без suggested_quantity (вся база)", lambda d: f"{d['no_quantity']}"),
]


def measure_rows(traces: list[tuple[str, dict]]) -> str:
    head = "".join(f"<th>{esc(name)}</th>" for name, _ in traces)
    out = [f"<tr><th></th>{head}</tr>"]
    for label, read in MEASURES:
        cells = "".join(f"<td>{esc(read(stats))}</td>" for _, stats in traces)
        out.append(f"<tr><td>{esc(label)}</td>{cells}</tr>")
    return "".join(out)


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


def move_block(move: dict) -> str:
    """Родитель, у которого работы съехали в дочерние статьи, на оси нетто."""
    article, own = move["article"], move["own"]
    stages = [s for s, _ in move["stages"]]
    subtree_row = " → ".join(mln(article["cells"].get(s, Decimal(0))) for s in stages)
    own_row = " → ".join(mln(own.get(s, Decimal(0))) for s in stages)
    return (
        '<p class="vh">Родитель, у которого работы уехали в подстатьи: статья '
        f'{esc(article["code"])} второго тендера</p>'
        '<p class="secsub">Здесь разом видны три вещи, которых нет на первом тендере: '
        '<b>ставки этапов расходятся</b> (20, 20, 22, 22), поэтому свод и разложение '
        'считают НЕТТО; <b>у статьи большое поддерево</b>; и между этапами произошло '
        '<b>уточнение классификации</b> — работы съехали в дочерние статьи. Собственные '
        f'суммы статьи идут {own_row} млн, а поддерево — {subtree_row} млн.</p>'
        '<div class="verdict bad"><b>Так выглядела бы прежняя редакция.</b> Разложение '
        'по СОБСТВЕННЫМ строкам узла написало бы «нет в файле» на каждой работе — под '
        f'строкой свода, которая показывает {subtree_row} млн. Ни одна работа никуда не '
        'девалась: она просто получила более точную статью, и деньги остались внутри того '
        'же поддерева. Экран сообщал бы об исчезновении там, где не изменилось ничего.</div>'
        + fragment(move["root"], move["kids"], article, move, "v2c")
        + '<div class="verdict"><b>Разложение раскрывает ПОДДЕРЕВО, и потому объясняет '
        'ровно ту сумму, под которой стоит.</b> Ключ работы — каталожная позиция БЕЗ '
        'статьи, поэтому переезд родитель → потомок группу не рвёт. Уточнение '
        'классификации при этом не пропадает из системы: у строки самой подстатьи в своде '
        'появление видно и объяснимо — просто родитель больше не врёт, будто работа ушла '
        'из его итога.</div>'
    )


def section(case: dict, born: dict, move: dict, traces: list) -> str:
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
    stats = traces[0][1]
    joined = "".join(blocks) + born_block(born) + move_block(move)
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
      <table>{measure_rows(traces)}</table>
      <p class="hint">Замер 30.08.2026, тот же участник и те же четыре этапа.
      «Строка-объяснитель» — ГРУППА РАЗЛОЖЕНИЯ, попавшая в число тех, чей накопленный
      вклад объясняет 90 % движения статьи. Групп три вида, и ключ у каждого свой
      (§2.2): работа — каталожная позиция БЕЗ статьи в пределах поддерева, допработа —
      ссылка «Сведений» (<code>chapter_ref_raw</code>), строки без каталожной
      привязки — одна группа на поддерево. Критерий отбора —
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
      сходимость не была померена машиной. Ключ между этапами у допработы — ССЫЛКА
      «Сведений» (<code>chapter_ref_raw</code>), она же в пилюле. Ссылка ГРУППИРУЕТ
      одну работу, а не удостоверяет строку: группы из нескольких строк на базе
      есть (блок замеров), наименования внутри них совпадают, и такая группа несёт
      пилюлю «несколько строк сметы». Наименование ключом быть не может: оно
      склеивает РАЗНЫЕ работы — от пары статьи 4.2.4 до «Стен» с четырьмя разными
      ссылками — и склейка эта невидима. Подпись берётся с последнего этапа, поэтому
      переименование при сохранённой ссылке экран не покажет — это названная цена.
      Объёма у допработ нет, поэтому второго этажа в ячейке нет.</li>
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


def own_cells(cur, code: str) -> dict[int, Decimal]:
    """Собственные суммы узла по этапам — без потомков.

    Нужны ровно для одного: показать в макете, ЧЕМ разложение по поддереву
    отличается от разложения по собственным строкам. В самом разложении эти
    величины не участвуют.
    """
    out: dict[int, Decimal] = {}
    for stage, proposal in STAGES:
        cur.execute(
            """
            select coalesce(sum(pi.total_cost_total), 0)
            from position_items pi
            join position_items ch
              on ch.id = pi.chapter_item_id and ch.proposal_id = pi.proposal_id
            join work_categories wc on wc.id = ch.work_category_id
            where pi.proposal_id = %s and not pi.is_chapter and wc.code = %s
            """,
            (proposal, code),
        )
        out[stage] = to_shown(Decimal(cur.fetchone()[0] or 0), stage)
    return out


def load_case(cur, root_code: str, article_code: str) -> dict:
    root, kids = load_tree(cur, root_code)
    article = next(k for k in kids if k["code"] == article_code)
    groups = [mark_volume_steps(g) for g in load_groups(cur, article_code)]
    top, rest = explainers(groups)
    partials = sorted((g for g in rest if is_partial(g)), key=sort_key)
    _, article_kids = load_tree(cur, article_code)
    return {
        "stages": list(STAGES), "basis": TAX[0],
        "root": root, "kids": kids, "article": article,
        "article_kids": article_kids, "top": top,
        "partials": partials[:PARTIAL_CAP],
        "partials_hidden": partials[PARTIAL_CAP:],
        "rest": [g for g in rest if not is_partial(g)],
    }


def main() -> None:
    global STAGES, TAX
    with psycopg.connect(dsn()) as con, con.cursor() as cur:
        STAGES = TRACE_ONE
        TAX = tax_basis(cur)
        main_case = load_case(cur, ROOT, ARTICLE)
        born_case = load_case(cur, ROOT_BORN, ARTICLE_BORN)
        stats_one = corpus_stats(cur)

        STAGES = TRACE_TWO
        TAX = tax_basis(cur)
        move_case = load_case(cur, ROOT_MOVE, ARTICLE_MOVE)
        move_case["own"] = own_cells(cur, ARTICLE_MOVE)
        stats_two = corpus_stats(cur)

        STAGES = TRACE_ONE
        TAX = tax_basis(cur)
    body = section(main_case, born_case, move_case,
                   [("Тендер «Генподряд»", stats_one),
                    ("Тендер «Cityzen»", stats_two)])
    if "--write" in sys.argv[1:]:
        write_into_mockup(body)
    else:
        print(body)


if __name__ == "__main__":
    main()
