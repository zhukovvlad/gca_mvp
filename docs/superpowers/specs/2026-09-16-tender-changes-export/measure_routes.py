"""Замер колонки «Что двигалось»: длина текста и доля строк с сообщением о статьях.

Сомнение, которое проверяется: сообщение о переезде между статьями считается
для РАБОТЫ, а строка листа — это «статья × работа». Значит одно и то же
сообщение печатается на двух строках сразу, и ни одна из них не говорит,
что произошло ИМЕННО С НЕЙ.
"""
from __future__ import annotations

import sys
from collections import Counter

from gen_mockup import build, load, route

# Скрипт печатает кириллицу и обязан работать от буквальной команды,
# без внешнего PYTHONIOENCODING.
sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    _header, stages, positions, additional = load()
    cells, meta, articles_by_stage = build(stages, positions, additional)
    routes = {key: route(key, cells, meta, articles_by_stage, stages) for key in cells}

    lengths = []
    steps_count = Counter()
    with_article = 0
    duplicated = Counter()

    for key, steps in routes.items():
        text = "; ".join(steps)
        lengths.append(len(text))
        steps_count[len(steps)] += 1
        if any(w in s for w in ("ушла", "пришла", "появилась в КП", "исчезла из КП") for s in steps):
            with_article += 1
            duplicated[key[2]] += 1

    lengths.sort()
    total = len(lengths)
    print(f"строк листа: {total}")
    print(f"длина текста «Что двигалось»: медиана {lengths[total // 2]}, "
          f"девятая дециль {lengths[int(total * 0.9)]}, максимум {lengths[-1]}")
    print(f"шагов в маршруте: {dict(sorted(steps_count.items()))}")
    print(f"строк с сообщением о статьях: {with_article}")

    multi = {work: n for work, n in duplicated.items() if n > 1}
    print(f"работ, чьё сообщение о статьях стоит больше чем на одной строке: "
          f"{len(multi)}; всего таких строк: {sum(multi.values())}")

    # Отрицательная проверка правки: сообщение обязано быть РАЗНЫМ на разных
    # строках одной работы. Совпавший текст значит, что строка говорит о работе
    # вообще, а не о себе, — дефект, ради которого правило и переписано.
    by_work = {}
    for key, steps in routes.items():
        by_work.setdefault(key[2], []).append((key[1][0], steps))
    same_text = 0
    for work, rows in by_work.items():
        if len(rows) < 2:
            continue
        seen = {}
        for _code, steps in rows:
            for step in steps:
                if "ушла" in step or "пришла" in step:
                    seen.setdefault(step, 0)
                    seen[step] += 1
        same_text += sum(1 for count in seen.values() if count > 1)
    print(f"совпавших ДОСЛОВНО сообщений на разных строках одной работы: {same_text}")

    worst = max(routes.items(), key=lambda kv: len("; ".join(kv[1])))
    print("\nсамый длинный маршрут:")
    print(f"  статья {worst[0][1][0]} · {meta[worst[0]]['work'][:60]}")
    for step in worst[1]:
        print(f"    {step}")

    sample = next((k for k, v in routes.items()
                   if any("ушла в" in s or "пришла из" in s for s in v) and duplicated[k[2]] > 1), None)
    if sample:
        print("\nпример продублированного сообщения — обе строки одной работы:")
        for key in [k for k in routes if k[2] == sample[2]]:
            print(f"  строка под статьёй {key[1][0]}: {'; '.join(routes[key])}")


if __name__ == "__main__":
    main()
