"""Все числа, на которые ссылается дизайн, одной командой и СВОИМ SQL.

Скрипт намеренно НЕ импортирует `gen_mockup`: сверщик, делящий предикат с
генератором, доказывает согласованность, а не правильность. Здесь каждое
утверждение считается независимо, прямо из базы.

Запуск:  python evidence.py
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from decimal import Decimal

import psycopg

# Скрипт печатает кириллицу и обязан работать от буквальной команды
# `python evidence.py`, без внешнего PYTHONIOENCODING.
sys.stdout.reconfigure(encoding="utf-8")

DSN = "postgresql://postgres@localhost:5459/gca_dev"
TRACES = [(2, 'ООО "АНТТЕК"'), (3, 'ООО "АНТТЕК"'), (3, 'ООО "ЕНИГЮН КОНСТРАКШН"')]
MAIN = (3, 'ООО "АНТТЕК"')

#: Предикат цены — дословно из миграции 0016. Проверено на стенде:
#: `'NaN'::numeric = 'NaN'::numeric` ИСТИНА, поэтому самосверка `x = x` не годится.
PRICE_OK = """pi.unit_cost_total > 0
          AND pi.unit_cost_total <> 'NaN'::numeric
          AND pi.unit_cost_total <> 'Infinity'::numeric
          AND pi.unit_cost_total <> '-Infinity'::numeric"""

#: Предикат ВЕСА. Одного `> 0` мало по той же причине, что и у цены:
#: `'NaN'::numeric > 0` и `'Infinity'::numeric > 0` в PostgreSQL истинны —
#: проверено на стенде.
WEIGHT_OK = """pi.suggested_quantity > 0
          AND pi.suggested_quantity <> 'NaN'::numeric
          AND pi.suggested_quantity <> 'Infinity'::numeric
          AND pi.suggested_quantity <> '-Infinity'::numeric"""

TRACE_JOIN = """
  offer_packages pk
  JOIN contractors c ON c.id = pk.contractor_id
  JOIN offers o ON o.package_id = pk.id
  JOIN tender_rounds r ON r.id = o.round_id
  JOIN estimates e ON e.offer_id = o.id
  JOIN lots l ON l.estimate_id = e.id
  JOIN proposals p ON p.lot_id = l.id
"""

CELLS_SQL = f"""
SELECT r.stage_no, ch.work_category_id AS art, pi.catalog_position_id AS work_id,
       cp.standard_job_title AS title,
       count(*) AS rows_all,
       count(*) FILTER (WHERE {PRICE_OK} AND {WEIGHT_OK}) AS rows_priced,
       sum(pi.total_cost_total) AS amount,
       sum(pi.suggested_quantity) AS qty,
       sum(pi.unit_cost_total * pi.suggested_quantity)
         FILTER (WHERE {PRICE_OK} AND {WEIGHT_OK}) AS price_num,
       sum(pi.suggested_quantity)
         FILTER (WHERE {PRICE_OK} AND {WEIGHT_OK}) AS price_den,
       sum(pi.total_cost_works) AS c_works,
       sum(pi.total_cost_materials) AS c_materials,
       sum(pi.total_cost_indirect_costs) AS c_indirect,
       string_agg(pi.item_number_in_proposal, ',' ORDER BY pi.item_number_in_proposal) AS numbers,
       sum(pi.unit_cost_works * pi.suggested_quantity)
         FILTER (WHERE {PRICE_OK} AND {WEIGHT_OK}) AS unit_works_num,
       sum(pi.unit_cost_materials * pi.suggested_quantity)
         FILTER (WHERE {PRICE_OK} AND {WEIGHT_OK}) AS unit_materials_num,
       sum(pi.unit_cost_indirect_costs * pi.suggested_quantity)
         FILTER (WHERE {PRICE_OK} AND {WEIGHT_OK}) AS unit_indirect_num
FROM {TRACE_JOIN}
  JOIN position_items pi ON pi.proposal_id = p.id AND pi.is_chapter = false
  JOIN position_items ch ON ch.id = pi.chapter_item_id
  JOIN catalog_positions cp ON cp.id = pi.catalog_position_id
WHERE pk.tender_id = %s AND c.title = %s
GROUP BY 1, 2, 3, 4
"""

EDGE_SQL = f"""
SELECT count(*) AS rows_all,
       count(*) FILTER (WHERE pi.suggested_quantity IS NULL) AS qty_null,
       count(*) FILTER (WHERE pi.total_cost_total IS NULL) AS amount_null,
       count(*) FILTER (WHERE pi.unit_cost_total = 0) AS price_zero,
       count(*) FILTER (WHERE pi.unit_cost_total < 0) AS price_negative,
       count(*) FILTER (WHERE pi.unit_cost_total = 'NaN'::numeric) AS price_nan,
       count(*) FILTER (WHERE pi.catalog_position_id IS NULL) AS no_catalog,
       count(*) FILTER (WHERE pi.chapter_item_id IS NULL) AS no_chapter,
       count(*) FILTER (WHERE NOT ({PRICE_OK})) AS price_unusable
FROM {TRACE_JOIN}
  JOIN position_items pi ON pi.proposal_id = p.id AND pi.is_chapter = false
WHERE pk.tender_id IN (2, 3)
"""

AW_SQL = """
SELECT r.stage_no, l.lot_key, aw.chapter_ref_raw, l.id AS lot_id,
       aw.work_category_id, sum(aw.total_amount) AS amount
FROM {join}
  JOIN estimate_additional_works aw ON aw.proposal_id = p.id
WHERE pk.tender_id = %s AND c.title = %s
GROUP BY 1, 2, 3, 4, 5
"""

CATALOG_SQL = "SELECT kind, count(*) FROM catalog_positions GROUP BY 1"

#: Состав цены по всей базе: тождество «работы + материалы + косвенные = итог»
#: спека цитирует как основание условного обещания. БЕЗ ДОПУСКА — спека сама
#: запрещает допуск у этого условия (построчный «не больше копейки» копится),
#: поэтому замер обязан считать точное равенство, а не `abs(...) <= 0.01`.
#:
#: Полнота состава считается КОНЕЧНОСТЬЮ, а не `IS NOT NULL`: реализация
#: (`crud.changes_export._finite`) отбрасывает ещё `NaN` и `±Infinity`, и более
#: слабый предикат замера подтверждал бы утверждение спеки «три КОНЕЧНЫЕ
#: составляющие» строками, которых код в состав не берёт.
#:
#: «Не больше копейки» — тоже замер, а не подпись: счётчик расхождений ограничен
#: копейкой, поэтому строка, вышедшая из этой полосы, в него не попадёт, число
#: спеки разойдётся с базой и `check_spec_numbers.py` покраснеет.
MIX_SQL = """
SELECT count(*) AS rows_all,
       count(*) FILTER (
           WHERE total_cost_works IS NOT NULL
             AND total_cost_works NOT IN ('NaN', 'Infinity', '-Infinity')
             AND total_cost_materials IS NOT NULL
             AND total_cost_materials NOT IN ('NaN', 'Infinity', '-Infinity')
             AND total_cost_indirect_costs IS NOT NULL
             AND total_cost_indirect_costs NOT IN ('NaN', 'Infinity', '-Infinity')
             AND total_cost_total IS NOT NULL
             AND total_cost_total NOT IN ('NaN', 'Infinity', '-Infinity')) AS rows_with_mix,
       count(*) FILTER (
           WHERE total_cost_works + total_cost_materials + total_cost_indirect_costs
                 = total_cost_total
           ) AS rows_mix_exact,
       count(*) FILTER (
           WHERE total_cost_works + total_cost_materials + total_cost_indirect_costs
                 <> total_cost_total
             AND abs(total_cost_works + total_cost_materials
                     + total_cost_indirect_costs - total_cost_total) <= 0.01
           ) AS rows_mix_diff
FROM position_items WHERE is_chapter = false
"""

SPLIT_MONEY_SQL = """
WITH g AS (
  SELECT r.stage_no, pi.catalog_position_id AS work_id, ch.work_category_id AS art,
         sum(pi.total_cost_total) AS amount
  FROM {join}
    JOIN position_items pi ON pi.proposal_id = p.id AND pi.is_chapter = false
    JOIN position_items ch ON ch.id = pi.chapter_item_id
  WHERE pk.tender_id = %s AND c.title = %s
  GROUP BY 1, 2, 3
), w AS (
  SELECT stage_no, work_id, count(DISTINCT art) AS arts, sum(amount) AS amount
  FROM g GROUP BY 1, 2
)
SELECT count(*), count(*) FILTER (WHERE arts > 1),
       sum(amount), sum(amount) FILTER (WHERE arts > 1)
FROM w WHERE stage_no = (SELECT max(stage_no) FROM w)
"""


def fetch(conn, sql, params=None):
    return conn.execute(sql, params).fetchall()


def series_of(rows, *, by_article: bool):
    """Свёртка строк в «ключ → этап → величины». Ключ — с статьёй или без."""
    stages = sorted({row[0] for row in rows})
    index = {stage: i for i, stage in enumerate(stages)}
    out = defaultdict(lambda: [None] * len(stages))
    for row in rows:
        stage, art, work_id = row[0], row[1], row[2]
        key = (art, work_id) if by_article else work_id
        slot = out[key][index[stage]] or {"rows_all": 0, "rows_priced": 0,
                                          "amount": Decimal(0), "qty": Decimal(0),
                                          "num": Decimal(0), "den": Decimal(0),
                                          "works": Decimal(0), "materials": Decimal(0),
                                          "indirect": Decimal(0),
                                          "unit_works": Decimal(0),
                                          "unit_materials": Decimal(0),
                                          "unit_indirect": Decimal(0)}
        slot["rows_all"] += row[4]
        slot["rows_priced"] += row[5]
        slot["amount"] += Decimal(row[6] or 0)
        slot["qty"] += Decimal(row[7] or 0)
        slot["num"] += Decimal(row[8] or 0)
        slot["den"] += Decimal(row[9] or 0)
        slot["works"] += Decimal(row[10] or 0)
        slot["materials"] += Decimal(row[11] or 0)
        slot["indirect"] += Decimal(row[12] or 0)
        slot["unit_works"] += Decimal(row[14] or 0)
        slot["unit_materials"] += Decimal(row[15] or 0)
        slot["unit_indirect"] += Decimal(row[16] or 0)
        out[key][index[stage]] = slot
    return out, stages


def gone_count(series_map):
    """Сколько ключей пропадают хотя бы раз после того, как встретились."""
    gone = 0
    for series in series_map.values():
        seen = False
        for cell in series:
            if cell is not None:
                seen = True
            elif seen:
                gone += 1
                break
    return gone


EDGE_NAMES = ("строк", "объём NULL", "сумма NULL", "цена = 0", "цена < 0",
              "цена NaN", "без каталожной строки", "без раздела", "цена непригодна")

TRIPLE = ("works", "materials", "indirect")


def trace_measurements(conn, tender: int, participant: str) -> dict:
    """Все замеры одной трассы. Соседними считаются этапы, СОСЕДНИЕ ПО ШКАЛЕ."""
    rows = fetch(conn, CELLS_SQL, (tender, participant))
    by_art, stages = series_of(rows, by_article=True)
    by_work, _ = series_of(rows, by_article=False)

    abs_move = unit_move = pairs = 0
    for series in by_work.values():
        for prev, cur in zip(series, series[1:]):
            if prev is None or cur is None:
                continue
            pairs += 1
            abs_move += tuple(prev[n] for n in TRIPLE) != tuple(cur[n] for n in TRIPLE)
            # СОСТАВ на единицу — тройка компонентов, а не цена: деление
            # price_num/price_den мерило бы движение цены и выдавало его за
            # движение состава.
            unit_prev = (tuple(prev[f"unit_{n}"] / prev["den"] for n in TRIPLE)
                         if prev["den"] else None)
            unit_cur = (tuple(cur[f"unit_{n}"] / cur["den"] for n in TRIPLE)
                        if cur["den"] else None)
            unit_move += unit_prev != unit_cur

    worst = None
    partial = 0
    for series in by_art.values():
        for cell in series:
            if cell is None or not (0 < cell["rows_priced"] < cell["rows_all"]):
                continue
            partial += 1
            naive = cell["amount"] / cell["qty"] if cell["qty"] else None
            correct = cell["num"] / cell["den"] if cell["den"] else None
            if naive and correct and (worst is None or abs(correct - naive) > worst[0]):
                worst = (abs(correct - naive), naive, correct)

    arts = defaultdict(lambda: defaultdict(set))
    numbers = defaultdict(dict)
    for row in rows:
        arts[row[2]][row[0]].add(row[1])
        numbers[row[2]].setdefault(row[0], []).append(row[13] or "")

    # Переклассификация — оба множества непусты и различаются на соседних по
    # шкале этапах. Появление после пустого этапа переездом не является.
    moves = defaultdict(set)
    for work_id, by_stage in arts.items():
        for a, b in zip(stages, stages[1:]):
            set_a, set_b = by_stage.get(a, set()), by_stage.get(b, set())
            if set_a and set_b and set_a != set_b:
                moves[(a, b)].add(work_id)

    kept = number_pairs = 0
    for by_stage in numbers.values():
        for a, b in zip(stages, stages[1:]):
            if a in by_stage and b in by_stage:
                number_pairs += 1
                kept += sorted(by_stage[a]) == sorted(by_stage[b])

    # Сколько работ хоть раз двинулись: основание «правило отсекает мало».
    # Состав в счёт НЕ идёт — он подпись, а не критерий (см. mix_only ниже).
    changed = mix_only = 0
    for series in by_work.values():
        moved = mix_moved = False
        for prev, cur in zip(series, series[1:]):
            if prev is None and cur is None:
                continue
            if prev is None or cur is None:
                moved = True
                continue
            if (prev["amount"] != cur["amount"] or prev["qty"] != cur["qty"]
                    or (prev["num"] / prev["den"] if prev["den"] else None)
                    != (cur["num"] / cur["den"] if cur["den"] else None)):
                moved = True
            if (tuple(prev[f"unit_{n}"] / prev["den"] for n in TRIPLE) if prev["den"] else None)                     != (tuple(cur[f"unit_{n}"] / cur["den"] for n in TRIPLE) if cur["den"] else None):
                mix_moved = True
        changed += moved
        mix_only += mix_moved and not moved

    # Самая тяжёлая группа и доля пустых косвенных — спека цитирует обе.
    max_rows = max((cell["rows_all"] for series in by_art.values()
                    for cell in series if cell is not None), default=0)
    indirect_zero = sum(1 for series in by_art.values() for cell in series
                        if cell is not None and cell["indirect"] == 0)
    cells_total = sum(1 for series in by_art.values() for cell in series
                      if cell is not None)

    aw = fetch(conn, AW_SQL.format(join=TRACE_JOIN), (tender, participant))
    aw_per_stage = defaultdict(Decimal)
    for row in aw:
        aw_per_stage[row[0]] += Decimal(row[5] or 0)

    return {
        "tender": tender, "participant": participant, "stages": stages,
        "works": len(by_work), "rows": len(by_art),
        "gone_article": gone_count(by_art), "gone_work": gone_count(by_work),
        "pairs": pairs, "mix_abs": abs_move, "mix_unit": unit_move,
        "late": sum(1 for s in by_art.values() if s[0] is None and any(c for c in s)),
        "early": sum(1 for s in by_art.values() if s[-1] is None and any(c for c in s)),
        "partial_cells": partial, "worst_price": worst,
        "moves": [((a, b), len(w), len(arts)) for (a, b), w in sorted(moves.items())],
        "numbers_kept": kept, "numbers_pairs": number_pairs,
        "changed_works": changed, "mix_only_works": mix_only,
        "max_rows_in_group": max_rows,
        "cells_total": cells_total, "indirect_zero_cells": indirect_zero,
        "transitions": sum(len({(tuple(sorted(by_stage.get(a, set()))),
                                 tuple(sorted(by_stage.get(b, set()))))
                                for a, b in zip(stages, stages[1:])
                                if by_stage.get(a) and by_stage.get(b)
                                and by_stage[a] != by_stage[b]})
                           for by_stage in [arts[w] for w in arts]) and len({
            (tuple(sorted(arts[w].get(a, set()))), tuple(sorted(arts[w].get(b, set()))))
            for w in arts for a, b in zip(stages, stages[1:])
            if arts[w].get(a) and arts[w].get(b) and arts[w][a] != arts[w][b]}),
        "aw_rows": len(aw),
        "aw_by_lot_id": len({(row[3], row[2]) for row in aw}),
        "aw_by_lot_key": len({(row[1], row[2]) for row in aw}),
        "aw_per_stage": dict(sorted(aw_per_stage.items())),
    }


def collect() -> dict:
    """Единственный источник чисел дизайна. Спека цитирует ЭТО, а не пересказ."""
    with psycopg.connect(DSN) as conn:
        tender, participant = MAIN
        split = fetch(conn, SPLIT_MONEY_SQL.format(join=TRACE_JOIN),
                      (tender, participant))[0]
        return {
            "catalog": fetch(conn, CATALOG_SQL),
            "mix": dict(zip(("строк базы", "с тремя конечными составляющими",
                             "сходится точно", "расходится в пределах копейки"),
                            fetch(conn, MIX_SQL)[0])),
            "edge": dict(zip(EDGE_NAMES, fetch(conn, EDGE_SQL)[0])),
            "traces": [trace_measurements(conn, t, p) for t, p in TRACES],
            "split": {"works": split[0], "split_works": split[1],
                      "money_all": split[2], "money_split": split[3]},
        }


def spaced(value) -> str:
    return f"{value:,.0f}".replace(",", " ")


def main() -> None:
    data = collect()

    print("=== КАТАЛОГ СТЕНДА ===")
    for kind, count in data["catalog"]:
        print(f"  {kind}: {count}")
    print("  фильтр kind='POSITION' оставил бы книгу пустой")
    print()

    print("=== СОСТАВ ЦЕНЫ ПО ВСЕЙ БАЗЕ ===")
    for name, value in data["mix"].items():
        print(f"  {name}: {value}")
    print()

    print("=== КРАЕВЫЕ ЗНАЧЕНИЯ, обе трассы ===")
    for name, value in data["edge"].items():
        print(f"  {name}: {value}")
    print()

    for t in data["traces"]:
        print(f"=== ТЕНДЕР {t['tender']} · {t['participant']} · этапов {len(t['stages'])} ===")
        print(f"  работ: {t['works']} · строк листа (статья × работа): {t['rows']}")
        print(f"  исчезновений по ключу со статьёй: {t['gone_article']}; "
              f"по работе: {t['gone_work']}; ЛОЖНЫХ: {t['gone_article'] - t['gone_work']}")
        print(f"  пар «работа × соседние этапы»: {t['pairs']}; состав двинулся "
              f"по абсолюту {t['mix_abs']}, по составу на единицу {t['mix_unit']}")
        print(f"  строк, начинающихся позже Э{t['stages'][0]}: {t['late']}; "
              f"кончающихся раньше Э{t['stages'][-1]}: {t['early']}")
        print(f"  ячеек с частичной ценой: {t['partial_cells']}")
        if t["worst_price"]:
            _gap, naive, correct = t["worst_price"]
            print(f"    худшее расхождение цены: наивно {spaced(naive)} "
                  f"против верного {spaced(correct)}")
        for (a, b), count, total in t["moves"]:
            print(f"  переезд Э{a}→Э{b}: {count} работ из {total} "
                  f"({round(100 * count / total)}%)")
        print(f"  номер строки совпал у соседних этапов: "
              f"{t['numbers_kept']} из {t['numbers_pairs']}")
        print(f"  работ, двинувшихся хоть раз: {t['changed_works']} из {t['works']}; "
              f"только состав: {t['mix_only_works']}")
        print(f"  строк сметы в самой тяжёлой группе: {t['max_rows_in_group']}; "
              f"различных переходов между статьями: {t['transitions']}")
        print(f"  ячеек с нулевыми косвенными: {t['indirect_zero_cells']} "
              f"из {t['cells_total']} "
              f"({round(100 * t['indirect_zero_cells'] / t['cells_total'])}%)")
        print(f"  допработы: строк {t['aw_rows']}; ключей по lots.id "
              f"{t['aw_by_lot_id']}, по lot_key {t['aw_by_lot_key']}")
        print("    суммы по этапам: "
              + " · ".join(f"Э{s}: {spaced(v)}" for s, v in t["aw_per_stage"].items()))
        print()

    split = data["split"]
    print("=== РАБОТЫ В НЕСКОЛЬКИХ СТАТЬЯХ (последний этап) ===")
    print(f"  работ {split['works']}, из них в двух и более статьях {split['split_works']}")
    print(f"  денег всего {spaced(split['money_all'])}")
    print(f"  из них в «раздвоенных» работах {spaced(split['money_split'])} "
          f"({round(100 * split['money_split'] / split['money_all'])}%)")


if __name__ == "__main__":
    main()
