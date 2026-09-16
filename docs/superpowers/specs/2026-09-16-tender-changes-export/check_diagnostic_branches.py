"""Синтетический прогон ветвей, недостижимых на стенде.

Каталог стенда целиком в `TO_REVIEW`, непривязанных строк ноль, состав полон
везде — поэтому диагностические группы, «состав неполон» и «суммы нет» на живых
данных не появляются НИКОГДА. Макет их показать не может, и это не повод им не
работать: первая редакция контракта «только сумма» падала на `HEADER` с
`KeyError: 'qty'`, и заметить это на стенде было нечем.

Вход здесь синтетический и назван таковым: он не доказывает ничего о данных,
только о коде, который эти данные разбирает. Числа в нём — обвязка, их можно
заменить любыми другими той же природы.

Запуск:  python check_diagnostic_branches.py
Код возврата 1, если хоть одна проверка не прошла.
"""
from __future__ import annotations

import sys
from decimal import Decimal

from gen_mockup import MONEY_ONLY_KINDS, amount_of, build, mix_complete, render_rows, route

sys.stdout.reconfigure(encoding="utf-8")

STAGES = [(1, "первый", None), (2, "второй", None)]
ARTICLE = (7, "7", "Отделка")


def position(stage, work_id, kind, *, amount="100", works="40", materials="50",
             indirect="10", qty="2", rows=1, article=ARTICLE):
    """Строка в форме `GROUPS_SQL` — двадцать три поля в том же порядке."""
    dec = lambda v: Decimal(v) if v is not None else None  # noqa: E731
    full_mix = all(v is not None for v in (works, materials, indirect))
    sums_up = full_mix and amount is not None and abs(
        dec(works) + dec(materials) + dec(indirect) - dec(amount)) <= Decimal("0.01")
    return (
        stage, article[0], article[1], article[2], work_id,
        f"Работа {work_id}" if work_id else None, kind, "м2",
        rows,                                   # rows_all
        rows if amount is not None else 0,      # rows_with_amount
        dec(amount),
        dec(works), dec(materials), dec(indirect),
        rows if sums_up else 0,                 # rows_with_mix — с равенством
        dec(qty),
        rows if amount is not None else 0,      # rows_priced
        dec(amount), dec(qty),                  # price_num, price_den
        dec(works), dec(materials), dec(indirect),
        "1",
    )


def additional(stage, ref, amount="30"):
    return (stage, ARTICLE[0], ARTICLE[1], ARTICLE[2], "ЛОТ-1", ref,
            f"Допработа {ref}", Decimal(amount), 1, 1)


def check(name: str, condition: bool, failures: list) -> None:
    print(f"  {'OK ' if condition else 'НЕТ'} {name}")
    if not condition:
        failures.append(name)


def main() -> None:
    failures: list[str] = []

    positions = [
        position(1, 10, "TO_REVIEW"), position(2, 10, "TO_REVIEW", amount="90"),
        position(1, 11, "POSITION"), position(2, 11, "POSITION"),
        position(1, 20, "HEADER"), position(2, 20, "HEADER"),
        position(1, 21, "LOT_HEADER"),
        position(2, 22, "TRASH"),
        position(1, None, None), position(2, None, None),
        # состав неполон: компонента нет
        position(1, 30, "POSITION", materials=None),
        position(2, 30, "POSITION"),
        # состав конечен, но не складывается в итог
        position(1, 31, "POSITION", amount="100", works="10",
                 materials="10", indirect="10"),
        position(2, 31, "POSITION"),
        # конечной суммы нет вовсе
        position(1, 32, "POSITION", amount=None),
        position(2, 32, "POSITION"),
    ]
    extras = [additional(1, "3.2.2"), additional(2, "3.2.2", amount="20")]

    cells, meta, arts = build(STAGES, positions, extras)
    kinds = {meta[key]["kind"] for key in cells}
    check("построение не падает на диагностических ветвях", True, failures)
    check("все четыре диагностические ветви заведены",
          {"nonwork", "unmatched", "additional"} <= kinds, failures)

    nonwork_titles = {meta[key]["work"] for key in cells if meta[key]["kind"] == "nonwork"}
    check("HEADER, LOT_HEADER и TRASH разведены по своим группам",
          len(nonwork_titles) == 3, failures)

    routes = {key: route(key, cells, meta, arts, STAGES) for key in cells}
    html = render_rows(list(cells), cells, meta, routes, STAGES)
    check("разметка строится", bool(html), failures)

    money_only = [key for key in cells if meta[key]["kind"] in MONEY_ONLY_KINDS]
    check("денежные ветви есть", bool(money_only), failures)
    for key in money_only:
        row = render_rows([key], cells, meta, routes, STAGES)
        check(f"{meta[key]['work'][:34]}: объём и цена не печатаются",
              row.count('class="dash"') >= 2, failures)
        check(f"{meta[key]['work'][:34]}: состав не печатается",
              row.count('class="num mix">—') == 3, failures)

    # «появилась» на ПЕРВОМ шаге: TRASH есть только на втором этапе
    trash = next(key for key in cells if "мусор" in meta[key]["work"])
    check("первое появление попадает в маршрут",
          any("появилась" in step for step in routes[trash]), failures)

    absent_sum = next(key for key in cells if key[2] == 32)
    check("«конечной суммы нет» — состояние, а не ноль",
          amount_of(cells[absent_sum][0]) is None, failures)

    missing_part = next(key for key in cells if key[2] == 30)
    check("состав с пропущенным компонентом признан неполным",
          not mix_complete(cells[missing_part][0]), failures)

    not_summing = next(key for key in cells if key[2] == 31)
    check("состав, не складывающийся в итог, признан неполным",
          not mix_complete(cells[not_summing][0]), failures)

    print()
    if failures:
        print(f"НЕ ПРОШЛО: {len(failures)}")
        for name in failures:
            print(f"  {name}")
        sys.exit(1)
    print("Все ветви, недостижимые на стенде, работают на синтетическом входе.")


if __name__ == "__main__":
    main()
