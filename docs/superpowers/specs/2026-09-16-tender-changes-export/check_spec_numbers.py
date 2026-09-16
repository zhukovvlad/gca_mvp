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


def claims(data: dict) -> list[tuple[str, str]]:
    """Пары «о чём утверждение → как оно набрано в спеке»."""
    by_trace = {(t["tender"], t["participant"]): t for t in data["traces"]}
    main = by_trace[MAIN]
    second = by_trace[(2, 'ООО "АНТТЕК"')]
    third = by_trace[(3, 'ООО "ЕНИГЮН КОНСТРАКШН"')]
    catalog = dict(data["catalog"])
    edge = data["edge"]
    mix = data["mix"]
    split = data["split"]

    out = [
        ("каталог стенда в TO_REVIEW", spaced(catalog["TO_REVIEW"])),
        ("строк обеих трасс", spaced(edge["строк"])),
        ("строк с нулевой ценой", spaced(edge["цена = 0"])),
        ("строк базы с составом", spaced(mix["строк базы"])),
        ("работ основной трассы", spaced(main["works"])),
        ("строк листа основной трассы", spaced(main["rows"])),
        ("исчезновений по ключу со статьёй", spaced(main["gone_article"])),
        ("исчезновений по работе", spaced(main["gone_work"])),
        ("ложных исчезновений", spaced(main["gone_article"] - main["gone_work"])),
        ("пар соседних этапов", spaced(main["pairs"])),
        # Спека цитирует РАЗНИЦУ между двумя способами счёта состава, а не
        # сами счётчики: на ней стоит довод «считать надо на единицу».
        ("разница способов счёта состава, основная трасса",
         spaced(main["mix_abs"] - main["mix_unit"])),
        ("строк с поздним началом", spaced(main["late"])),
        ("строк с ранним концом", spaced(main["early"])),
        ("ячеек с частичной ценой", spaced(main["partial_cells"])),
        ("номер строки устоял, основная трасса", spaced(main["numbers_kept"])),
        ("работ двинулось хоть раз", spaced(main["changed_works"])),
        ("допработ, ключей по lots.id", spaced(main["aw_by_lot_id"])),
        ("допработ, ключей по lot_key", spaced(main["aw_by_lot_key"])),
        ("номер строки устоял, вторая трасса", spaced(second["numbers_kept"])),
        ("пар соседних этапов, вторая трасса", spaced(second["numbers_pairs"])),
        ("разница способов счёта состава, вторая трасса",
         spaced(second["mix_abs"] - second["mix_unit"])),
        ("разница способов счёта состава, третья трасса",
         spaced(third["mix_abs"] - third["mix_unit"])),
        ("работ в нескольких статьях", spaced(split["split_works"])),
        ("работ на последнем этапе", spaced(split["works"])),
        ("денег в раздвоенных работах", spaced(split["money_split"])),
        ("денег всего на последнем этапе", spaced(split["money_all"])),
    ]

    if main["worst_price"]:
        _gap, naive, correct = main["worst_price"]
        out.append(("худшая наивная цена", spaced(naive)))
        out.append(("худшая верная цена", spaced(correct)))

    for (a, b), count, total in main["moves"]:
        if count >= 100:  # массовая переклассификация, её спека называет числом
            out.append((f"переклассификация Э{a}→Э{b}", spaced(count)))

    for stage, amount in main["aw_per_stage"].items():
        if amount:
            out.append((f"допработы, этап {stage}", spaced(amount)))

    return out


def main() -> None:
    with open(SPEC, encoding="utf-8") as handle:
        text = handle.read().replace(NBSP, " ")

    data = collect()
    failures = []
    for what, value in claims(data):
        if value not in text:
            failures.append((what, value))
        print(f"  {'OK ' if value in text else 'НЕТ'} {what}: {value}")

    print()
    if failures:
        print(f"НЕ НАЙДЕНО В СПЕКЕ: {len(failures)}")
        for what, value in failures:
            print(f"  {what}: ожидалось «{value}»")
        sys.exit(1)
    print(f"Все {len(claims(data))} утверждений спеки сходятся с базой.")


if __name__ == "__main__":
    main()
