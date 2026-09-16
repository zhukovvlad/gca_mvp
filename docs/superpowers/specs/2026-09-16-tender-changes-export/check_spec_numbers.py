"""Сверка чисел спеки с базой: две проверки на каждое утверждение.

1. **База по-прежнему даёт это значение.** Стенд живой и растёт: за одну сессию
   каталог вырос с 5 234 до 5 970 строк, и половина чисел первой редакции спеки
   протухла молча.
2. **Значение дословно стоит в тексте спеки.** Иначе опечатка в прозе живёт
   рядом с верным замером и выглядит как замер.

Процедурного правила «цитировать только из макета» недостаточно — именно так
спека попозиционного раскрытия разошлась со своим макетом.

Запуск:  python check_spec_numbers.py
Код возврата 1, если хоть одно утверждение не сошлось.
"""
from __future__ import annotations

import os
import sys

from evidence import MAIN, collect

sys.stdout.reconfigure(encoding="utf-8")

SPEC = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                    "2026-09-16-tender-changes-export-design.md")

#: Неразрывный пробел: разряды в спеке могут быть отбиты им, а сверять надо с
#: обычным. Записан ЭКРАНИРОВАННО — символом его не видно глазами в диффе.
NBSP = "\u00a0"


def spaced(value) -> str:
    """Число так, как оно набрано в спеке: разряды отбиты пробелом.

    ОКРУГЛЕНИЕ, а не усечение: `int()` на Decimal отбрасывает копейки и
    расходится со спекой на единицу — первая редакция этого сверщика так и
    «не нашла» четыре верных числа.
    """
    return f"{round(value):,}".replace(",", " ")


def claims(data: dict) -> list[tuple[str, str, str]]:
    """Тройки «о чём → как набрано в спеке → якорная фраза».

    Якорь обязателен: без него короткое значение вроде «12» подтверждается
    любым случайным вхождением, и страж выглядит строже, чем он есть.
    Проверяется АБЗАЦ, содержащий и значение, и якорь: строка для markdown
    слишком мелкая единица — текст переносится, и число с якорем оказываются
    на соседних строках одного утверждения.
    """
    by_trace = {(t["tender"], t["participant"]): t for t in data["traces"]}
    main = by_trace[MAIN]
    second = by_trace[(2, 'ООО "АНТТЕК"')]
    third = by_trace[(3, 'ООО "ЕНИГЮН КОНСТРАКШН"')]
    catalog = dict(data["catalog"])
    edge = data["edge"]
    split = data["split"]
    mix_gap = [spaced(t["mix_abs"] - t["mix_unit"]) for t in (second, main, third)]

    out = [
        ("каталог стенда", spaced(catalog["TO_REVIEW"]), "TO_REVIEW"),
        ("строк обеих трасс", spaced(edge["строк"]), "0 из"),
        ("строк с нулевой ценой", spaced(edge["цена = 0"]), "unit_cost_total = 0"),
        ("строк базы с составом", spaced(data["mix"]["строк базы"]), "строках базы"),
        ("работ основной трассы", spaced(main["works"]), "из 877"),
        ("строк листа", spaced(main["rows"]), "из 1 365"),
        ("исчезновений со статьёй", spaced(main["gone_article"]), "исчезновений"),
        ("исчезновений по работе", spaced(main["gone_work"]), "вместо"),
        ("ложных исчезновений", spaced(main["gone_article"] - main["gone_work"]), "ложных"),
        ("пар соседних этапов", spaced(main["pairs"]), "пар"),
        ("строк с поздним началом", spaced(main["late"]), "позже первого"),
        ("строк с ранним концом", spaced(main["early"]), "раньше последнего"),
        ("ячеек с частичной ценой", spaced(main["partial_cells"]), "частичной"),
        ("номер устоял, основная", spaced(main["numbers_kept"]), "парах из"),
        ("работ двинулось", spaced(main["changed_works"]), "хотя бы раз"),
        ("ключей по lots.id", spaced(main["aw_by_lot_id"]), "ключей вместо"),
        ("ключей по lot_key", spaced(main["aw_by_lot_key"]), "ключей вместо"),
        ("самая тяжёлая группа", spaced(main["max_rows_in_group"]), "строк сметы"),
        ("различных переходов", spaced(main["transitions"]), "различных переходов"),
        ("доля пустых косвенных",
         f"{round(100 * main['indirect_zero_cells'] / main['cells_total'])} %",
         "косвенные расходы"),
        ("номер устоял, вторая трасса", spaced(second["numbers_kept"]), "из 2 369"),
        ("пар, вторая трасса", spaced(second["numbers_pairs"]), "907"),
        ("разница способов счёта состава", ", ".join(mix_gap[:2]) + " и " + mix_gap[2],
         "пар на трёх трассах"),
        ("работ в нескольких статьях", spaced(split["split_works"]), "работ из 714"),
        ("работ на последнем этапе", spaced(split["works"]), "работ из 714"),
        ("денег в раздвоенных", spaced(split["money_split"]), "из"),
        ("денег всего", spaced(split["money_all"]), "приходится"),
    ]

    if main["worst_price"]:
        _gap, naive, correct = main["worst_price"]
        out.append(("худшая наивная цена", spaced(naive), "против верных"))
        out.append(("худшая верная цена", spaced(correct), "против верных"))

    for (a, b), count, _total in main["moves"]:
        out.append((f"переклассификация Э{a}→Э{b}", spaced(count), "статью сменили"))

    for stage, amount in main["aw_per_stage"].items():
        if amount:
            out.append((f"допработы, этап {stage}", spaced(amount), "этапе"))

    return out


def main() -> None:
    with open(SPEC, encoding="utf-8") as handle:
        text = handle.read().replace(NBSP, " ")
    # Абзац — блок между пустыми строками. Строка таблицы markdown тоже абзац
    # для этой цели: она целиком на одной строке.
    blocks = [block for block in text.split(chr(10) * 2) if block.strip()]

    data = collect()
    checked = claims(data)
    failures = []
    for what, value, anchor in checked:
        hit = any(value in block and anchor in block for block in blocks)
        if not hit:
            failures.append((what, value, anchor))
        print(f"  {'OK ' if hit else 'НЕТ'} {what}: {value}")

    print()
    if failures:
        print(f"НЕ СОШЛОСЬ: {len(failures)} из {len(checked)}")
        for what, value, anchor in failures:
            print(f"  {what}: ждал «{value}» в строке с «{anchor}»")
        sys.exit(1)
    print(f"Все {len(checked)} выбранных замеров стоят в спеке и сходятся с базой.")
    print("Это ВЫБОРКА, а не весь текст: страж проверяет перечисленные здесь")
    print("утверждения, и расширять список — часть правки спеки.")


if __name__ == "__main__":
    main()
