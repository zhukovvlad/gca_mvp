"""Синтетический прогон ветвей, недостижимых на стенде.

Каталог стенда целиком в `TO_REVIEW`, непривязанных строк ноль, конечная сумма
есть у каждой строки — поэтому диагностические группы и «суммы нет» на живых
данных не появляются НИКОГДА. Макет их показать не может, и это не повод им не
работать: первая редакция контракта «только сумма» падала на `HEADER` с
`KeyError: 'qty'`, и заметить это на стенде было нечем.

«Состав неполон» — случай другой, и прежняя редакция этой докстроки называла его
недостижимым по ложному замеру: построчная полнота (первое условие) на стенде
действительно не нарушена ни разу, но ВТОРОЕ условие — тождество на печатаемых
Δ — гасит от 5,2 % до 31,0 % строк листа на 449-ТУ (спека §2.3). Синтетическим
входом здесь предъявляются его границы, а не сам факт достижимости.

Вход здесь синтетический и назван таковым: он не доказывает ничего о данных,
только о коде, который эти данные разбирает. Числа в нём — обвязка, их можно
заменить любыми другими той же природы.

**Что этот прогон НЕ доказывает.** Построчный счётчик полноты состава считает
SQL, и здесь он подаётся готовым — значит классификация строки «три конечные
составляющие есть» этим прогоном не проверена, её обязан предъявить
интеграционный тест (DoD 15). Проверено здесь другое, и оно не менее важно:
разбор по ветвям, гашение величин у денежных ветвей, маршрут — и РАВЕНСТВО
состава итогу, которое считается на агрегате группы уже в Python.

Запуск:  python check_diagnostic_branches.py
Код возврата 1, если хоть одна проверка не прошла.
"""
from __future__ import annotations

import sys
from decimal import Decimal

from gen_mockup import (MONEY_ONLY_KINDS, amount_of, build, delta_identity_holds,
                        mix_complete, money_round, render_rows, route)

sys.stdout.reconfigure(encoding="utf-8")

STAGES = [(1, "первый", None), (2, "второй", None)]
ARTICLE = (7, "7", "Отделка")


def position(stage, work_id, kind, *, amount="100", works="40", materials="50",
             indirect="10", qty="2", rows=1, article=ARTICLE):
    """Строка в форме `GROUPS_SQL` — двадцать три поля в том же порядке."""
    dec = lambda v: Decimal(v) if v is not None else None  # noqa: E731
    # Построчное условие SQL — ТОЛЬКО полнота данных, без равенства: равенство
    # утверждается про итог группы и считается на нём.
    full_mix = all(v is not None for v in (works, materials, indirect))
    return (
        stage, article[0], article[1], article[2], work_id,
        f"Работа {work_id}" if work_id else None, kind, "м2",
        rows,                                   # rows_all
        rows if amount is not None else 0,      # rows_with_amount
        dec(amount),
        dec(works), dec(materials), dec(indirect),
        rows if (full_mix and amount is not None) else 0,   # rows_with_mix
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
        # ГРАНИЦА: ровно копейка расхождения на одной строке
        position(1, 33, "POSITION", amount="100.00", works="40.00",
                 materials="50.00", indirect="10.01"),
        position(2, 33, "POSITION"),
        # НАКОПЛЕНИЕ: две строки группы по полкопейки в одну сторону дают копейку
        position(1, 34, "POSITION", amount="100.00", works="40.00",
                 materials="50.00", indirect="10.005"),
        position(1, 34, "POSITION", amount="100.00", works="40.00",
                 materials="50.00", indirect="10.005"),
        position(2, 34, "POSITION"),
        # НЕРАСПРЕДЕЛИМОСТЬ ОКРУГЛЕНИЯ: каждая Δ состава печатается 0,00,
        # а Δ суммы — 0,01. Свёрнутая проверка это пропускала.
        position(2, 35, "POSITION", amount="0.012", works="0.004",
                 materials="0.004", indirect="0.004", qty="1"),
        # конечной суммы нет вовсе
        position(1, 32, "POSITION", amount=None),
        position(2, 32, "POSITION"),
        # Н1 внешнего ревью: цены нет на первом этапе (нулевой вес — знаменатель
        # матрицы гасит цену), появилась на втором. Это переход ДОСТУПНОСТИ, а
        # не изменение цены: печатать «цена»/«состав» здесь нельзя (спека §2.7:
        # числовые основания требуют ОБЕИХ ячеек).
        position(1, 40, "POSITION", qty="0"),
        position(2, 40, "POSITION"),
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

    # Пропущенный компонент ловится ПОСТРОЧНОЙ полнотой — это первое условие.
    missing_part = next(key for key in cells if key[2] == 30)
    check("состав с пропущенным компонентом признан неполным",
          not mix_complete(cells[missing_part][0]), failures)
    check("он же не проходит тождество на печатаемых Δ",
          not delta_identity_holds(cells[missing_part][0], cells[missing_part][-1]),
          failures)

    # Остальные три ловятся ВТОРЫМ условием: построчно они полны, а печатаемые
    # Δ не сходятся. Проверять их построчной полнотой нельзя — она зелёная.
    for work_id, name in ((31, "состав, не складывающийся в итог"),
                          (33, "расхождение ровно в копейку"),
                          (34, "накопленные две полкопейки")):
        key = next(k for k in cells if k[2] == work_id)
        cell = cells[key][0]
        check(f"{name}: построчно полон",
              cell["rows_with_mix"] == cell["rows_with_amount"], failures)
        check(f"{name}: тождество на печатаемых Δ не держится",
              not delta_identity_holds(cells[key][0], cells[key][-1]), failures)

    exact = next(key for key in cells if key[2] == 11)
    check("сошедшийся состав признан полным", mix_complete(cells[exact][0]), failures)
    check("тождество на печатаемых Δ держится у сошедшейся строки",
          delta_identity_holds(cells[exact][0], cells[exact][-1]), failures)

    # Округление не распределяется: три по 0,004 печатаются нулями, итог — копейкой.
    indivisible = next(key for key in cells if key[2] == 35)
    check("нераспределимость округления поймана",
          not delta_identity_holds(cells[indivisible][0], cells[indivisible][-1]),
          failures)

    # Н1 внешнего ревью: асимметрия доступности цены/состава не равна их
    # изменению. «Объём» — числовое основание, которому асимметрия ЦЕНЫ не
    # мешает: он печатается как обычно.
    price_gap = next(key for key in cells if key[2] == 40)
    price_gap_steps = routes[price_gap]
    check("переход «цены нет → цена появилась» не печатает «цена»",
          not any("цена" in step for step in price_gap_steps), failures)
    check("тот же переход не печатает «состав»",
          not any("состав" in step for step in price_gap_steps), failures)
    check("несвязанное числовое основание («объём») печатается как обычно",
          any("объём" in step for step in price_gap_steps), failures)

    # Граница HALF_UP против HALF_EVEN: банковское округление дало бы 0,00.
    check("0,005 округляется ВВЕРХ (ROUND_HALF_UP)",
          money_round(Decimal("0.005")) == Decimal("0.01"), failures)
    check("0,015 округляется вверх тем же правилом",
          money_round(Decimal("0.015")) == Decimal("0.02"), failures)

    print()
    if failures:
        print(f"НЕ ПРОШЛО: {len(failures)}")
        for name in failures:
            print(f"  {name}")
        sys.exit(1)
    print("Все ветви, недостижимые на стенде, работают на синтетическом входе.")


if __name__ == "__main__":
    main()
