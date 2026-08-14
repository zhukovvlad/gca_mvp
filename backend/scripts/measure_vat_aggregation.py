"""Замер Б нулевой задачи: группировка по базе против join на VALUES.

Запуск (из backend/, стенд поднят):
    PYTHONIOENCODING=utf-8 uv run python -m scripts.measure_vat_aggregation

`PYTHONIOENCODING` обязателен: скрипт печатает кириллицу, а консоль Windows по
умолчанию отдаёт её в cp1251 и роняет прогон `UnicodeEncodeError` на первой же
строке — замер не начался бы вовсе.

Замер идёт ДО миграции 0012, поэтому `estimates.vat_rate_base_override` ещё не
существует; эффективная база моделируется как `proposals.vat_rate`. На момент
замера ручных поправок нет ни у одной сметы, так что это тождество, а не
упрощение.

НЕРАБОЧ после 0012: миграция переименовала `v_position_deviations` в
`v_position_deviation_inputs` (и сняла её `deviation_pct`) — прежде чем снова
запускать этот скрипт, поправь имя VIEW во всех трёх местах ниже, иначе прогон
упадёт на `relation "v_position_deviations" does not exist`.

ВРЕМЕННОЕ ОТСТУПЛЕНИЕ (назначено оркестратором для этого прогона задачи 0,
снять при первом использовании после разбора очереди Review, см.
docs/insights/false-test-premises.md): на этом стенде ВСЕ `catalog_positions`
имеют `kind = 'TO_REVIEW'`, поэтому `v_position_deviations` (фильтрует
`kind = 'POSITION'`) пуст, и оба кандидата вернули бы 0 строк — сверка
"совпадают" была бы ложно-зелёной. Чтобы замер видел реальный объём корпуса,
весь прогон (промоушен каталога, ANALYZE, обе выборки, оба EXPLAIN) обёрнут в
транзакцию, которая ГАРАНТИРОВАННО откатывается — см.
`run_measurement_in_rolled_back_transaction()`. Стенд после прогона не
меняется: `ROLLBACK` возвращает `catalog_positions.kind` к исходным значениям,
и это дополнительно проверяется отдельным запросом после отката.
"""
from __future__ import annotations

from decimal import Context, Decimal, localcontext

import sqlalchemy as sa

from database import SessionLocal

HUNDRED = Decimal(100)
PRECISION = 100

CANDIDATE_1 = """
SELECT d.catalog_position_id, d.contract_id, p.vat_rate AS grouper,
       SUM(d.unit_cost_total * d.weight) AS weighted_cost,
       SUM(d.weight)                     AS weight_total
FROM v_position_deviations d
JOIN proposals p ON p.id = d.proposal_id
WHERE d.weight > 0 AND p.vat_rate IS NOT NULL
GROUP BY d.catalog_position_id, d.contract_id, p.vat_rate
ORDER BY 1, 2, 3
"""

CANDIDATE_2_TEMPLATE = """
SELECT d.catalog_position_id, d.contract_id, f.factor AS grouper,
       SUM(d.unit_cost_total * d.weight) AS weighted_cost,
       SUM(d.weight)                     AS weight_total
FROM v_position_deviations d
JOIN (VALUES {items}) AS f(proposal_id, factor) ON f.proposal_id = d.proposal_id
WHERE d.weight > 0
GROUP BY d.catalog_position_id, d.contract_id, f.factor
ORDER BY 1, 2, 3
"""


def build_values(db) -> str:
    """VALUES для ВСЕХ предложений с известной ставкой, полной точностью.

    Обрезанный литерал вроде `0.8333333333` дал бы кандидату 2 фору по скорости
    и проигрыш по точности — то есть сравнивались бы разные вычисления.
    """
    rows = db.execute(
        sa.text("SELECT id, vat_rate FROM proposals WHERE vat_rate IS NOT NULL ORDER BY id")
    ).all()
    with localcontext(Context(prec=PRECISION)):
        items = ", ".join(
            f"({row.id}::bigint, {HUNDRED / (HUNDRED + row.vat_rate)}::numeric)" for row in rows
        )
    if not items:
        raise SystemExit("На стенде нет ни одного предложения с заявленной ставкой НДС")
    return items


def volumes(db) -> None:
    for label, query in (
        ("позиций (не разделы)", "SELECT count(*) FROM position_items WHERE is_chapter = false"),
        ("строк VIEW", "SELECT count(*) FROM v_position_deviations"),
        ("предложений со ставкой", "SELECT count(*) FROM proposals WHERE vat_rate IS NOT NULL"),
    ):
        print(f"{label}: {db.execute(sa.text(query)).scalar_one()}")


def to_factor(rate: Decimal) -> Decimal:
    """Ставка → множитель, той же арифметикой, что строит VALUES.

    Нужна, чтобы группирующие величины кандидатов стали сравнимыми: у первого
    это ставка, у второго — множитель, и без приведения строки не сопоставить.
    """
    with localcontext(Context(prec=PRECISION)):
        return HUNDRED / (HUNDRED + rate)


def compare(db, sql_1: str, sql_2: str) -> None:
    """Сверка ДО сравнения планов: кандидаты обязаны делать одну и ту же работу.

    Сравнение идёт ПОСТРОЧНО, по полному ключу — свод по (работа, договор)
    скрыл бы разное число групп внутри ячейки, то есть ровно то расхождение,
    ради которого сверка и заводится. `weight_total` сравнивается наравне с
    `weighted_cost`: разойтись они могут независимо.
    """
    def rows_of(sql: str, normalize) -> dict:
        result = {}
        for row in db.execute(sa.text(sql)):
            key = (row.catalog_position_id, row.contract_id, normalize(row.grouper))
            if key in result:
                raise SystemExit(f"Дубль ключа {key} — группировка запроса не та, что заявлена")
            result[key] = (row.weighted_cost, row.weight_total)
        return result

    first = rows_of(sql_1, to_factor)          # ставка → множитель
    second = rows_of(sql_2, lambda value: value)  # уже множитель

    print(f"строк: кандидат 1 — {len(first)}, кандидат 2 — {len(second)}")
    if len(first) != len(second):
        raise SystemExit(
            "Разное число строк — кандидаты делают разную работу, сравнивать планы "
            "бессмысленно"
        )

    differing = [key for key in first.keys() | second.keys() if first.get(key) != second.get(key)]
    if differing:
        for key in differing[:5]:
            print(f"  {key}: кандидат 1 — {first.get(key)}, кандидат 2 — {second.get(key)}")
        raise SystemExit(f"Кандидаты расходятся на {len(differing)} строках")
    print("результаты совпадают построчно — планы сопоставимы")


def explain(db, sql: str, label: str) -> None:
    print(f"\n=== EXPLAIN {label} ===")
    for line in db.execute(sa.text(f"EXPLAIN (ANALYZE, BUFFERS) {sql}")):
        print(line[0])


def run_measurement(db) -> None:
    """Тело замера: объёмы, построчная сверка, оба плана.

    Общее для обычного прогона (после того как очередь Review на стенде будет
    разобрана и `v_position_deviations` перестанет быть пустым сам по себе) и
    для временного прогона внутри промоушен-транзакции ниже.
    """
    volumes(db)
    candidate_2 = CANDIDATE_2_TEMPLATE.format(items=build_values(db))
    compare(db, CANDIDATE_1, candidate_2)
    explain(db, CANDIDATE_1, "кандидат 1 — группировка по базе")
    explain(db, candidate_2, "кандидат 2 — join на VALUES")


def run_measurement_in_rolled_back_transaction() -> None:
    """ВРЕМЕННАЯ ОБВЯЗКА замера Б для ЭТОГО стенда (Review-очередь не разобрана,
    все `catalog_positions.kind = 'TO_REVIEW'`).

    Промоутит все каталожные строки в 'POSITION' внутри одной транзакции с
    ANALYZE, обеими выборками и обоими EXPLAIN, затем ЭТУ ЖЕ транзакцию
    откатывает в `finally` — стенд обязан остаться нетронутым. ANALYZE внутри
    транзакции обязателен: без него планировщик работает по статистике, где
    `kind = 'POSITION'` не встречается ни разу, и выбирает план под
    несуществующее на диске распределение.
    """
    with SessionLocal() as db:
        try:
            db.execute(sa.text("UPDATE catalog_positions SET kind = 'POSITION'"))
            db.execute(sa.text("ANALYZE catalog_positions"))
            db.execute(sa.text("ANALYZE position_items"))
            run_measurement(db)
        finally:
            db.rollback()

    # Проверка ПОСЛЕ отката (отдельная сессия/транзакция): стенд не изменился.
    with SessionLocal() as check:
        remaining = check.execute(
            sa.text("SELECT count(*) FROM catalog_positions WHERE kind <> 'TO_REVIEW'")
        ).scalar_one()
        print(f"после ROLLBACK: catalog_positions с kind <> 'TO_REVIEW' = {remaining}")
        if remaining != 0:
            raise SystemExit(
                "ОТКАТ НЕ СРАБОТАЛ — стенд изменён, замер недействителен, нужно вмешательство"
            )


def main() -> None:
    run_measurement_in_rolled_back_transaction()


if __name__ == "__main__":
    main()
