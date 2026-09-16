"""Сходимость листа со СВОДОМ: сумма строк макета против суммы двух ветвей.

Обещание дизайна — «лист сохраняет каждый рубль свода». Здесь оно проверяется,
а не объявляется.

Сторона БД повторяет правило `v_category_totals` (миграция 0012) ДОСЛОВНО:
`WHERE pi.is_chapter = false` плюс ветвь `estimate_additional_works`, с тем же
правилом конечности — и БЕЗ фильтра по виду каталожной строки, которого у
свода нет. Прежняя редакция добавляла сюда `JOIN catalog_positions` и отсев
`HEADER`/`TRASH`, то есть сравнивала генератор с отфильтрованной копией его
собственного правила, а заодно выбрасывала непривязанные строки, которые книга
обязана включать. На стенде расхождения не возникало только потому, что каталог
целиком в `TO_REVIEW`; после промоушена каталога (фича А1) проверка молча
разошлась бы с картой тендера.
"""
from __future__ import annotations

import sys
from decimal import Decimal

import psycopg

from gen_mockup import DSN, PARTICIPANT, TENDER, amount_of, build, load

# Скрипт печатает кириллицу и обязан работать от буквальной команды,
# без внешнего PYTHONIOENCODING.
sys.stdout.reconfigure(encoding="utf-8")

DB_SQL = """
SELECT r.stage_no,
       (SELECT coalesce(sum(pi.total_cost_total) FILTER (
                  WHERE pi.total_cost_total IS NOT NULL
                    AND pi.total_cost_total <> 'NaN'::numeric
                    AND pi.total_cost_total <> 'Infinity'::numeric
                    AND pi.total_cost_total <> '-Infinity'::numeric), 0)
          FROM lots l
          JOIN proposals p ON p.lot_id = l.id
          JOIN position_items pi ON pi.proposal_id = p.id AND pi.is_chapter = false
         WHERE l.estimate_id = e.id) AS positions_sum,
       (SELECT coalesce(sum(aw.total_amount) FILTER (
                  WHERE aw.total_amount IS NOT NULL
                    AND aw.total_amount <> 'NaN'::numeric
                    AND aw.total_amount <> 'Infinity'::numeric
                    AND aw.total_amount <> '-Infinity'::numeric), 0)
          FROM lots l
          JOIN proposals p ON p.lot_id = l.id
          JOIN estimate_additional_works aw ON aw.proposal_id = p.id
         WHERE l.estimate_id = e.id) AS additional_sum
FROM offer_packages pk
JOIN contractors c ON c.id = pk.contractor_id
JOIN offers o ON o.package_id = pk.id
JOIN tender_rounds r ON r.id = o.round_id
JOIN estimates e ON e.offer_id = o.id
WHERE pk.tender_id = %s AND c.title = %s
ORDER BY r.stage_no
"""


def main() -> None:
    _header, stages, positions, additional = load()
    cells, _meta, _articles = build(stages, positions, additional)

    with psycopg.connect(DSN) as conn:
        db_rows = conn.execute(DB_SQL, (TENDER, PARTICIPANT)).fetchall()

    sheet = [Decimal(0)] * len(stages)
    for series in cells.values():
        for index, cell in enumerate(series):
            value = amount_of(cell)
            if value is not None:
                sheet[index] += value

    ok = True
    for index, (stage_no, positions_sum, additional_sum) in enumerate(db_rows):
        expected = Decimal(positions_sum) + Decimal(additional_sum)
        got = sheet[index]
        delta = got - expected
        mark = "СХОДИТСЯ" if delta == 0 else f"РАСХОЖДЕНИЕ {delta}"
        ok = ok and delta == 0
        print(f"этап {stage_no}: лист {got:>18,} · БД {expected:>18,} · {mark}"
              .replace(",", " "))
    print("итог:", "все этапы сходятся" if ok else "есть расхождения")


if __name__ == "__main__":
    main()
