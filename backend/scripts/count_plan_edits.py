"""Правки планов — механически, не по темам коммитов, и с разделением по фазе.

Правило счёта. Для каждого файла плана берётся коммит, который его создал, и
все последующие, его менявшие. Правка засчитывается СОДЕРЖАТЕЛЬНОЙ, если в её
дифе есть хоть одна изменённая непустая строка, не являющаяся простановкой
чекбокса (`- [ ]` <-> `- [x]`).

Содержательные правки разделяются по ФАЗЕ, и это разделение несущее:

* **гейт 3** — правки до первого кодового коммита фичи. Это и есть цена
  доводки плана, на которую смотрит §9.1;
* **по ходу** — правки после того, как реализация началась: заметки в план,
  найденные при работе, и пометки протухания. Правило «плану запрещены тела»
  их не касается, и наказывать ими следующую фичу нельзя.

Граница «первый кодовый коммит» ищется по линейному порядку `main`: первый
после создания плана коммит, тронувший ПРОДУКТОВЫЙ исходник — `backend/**/*.py`
кроме `backend/scripts/`, либо `frontend/src/**/*.{ts,tsx}`.

Оба исключения не косметические, их поймало внешнее ревью. Третий круг ревью
плана `position-drilldown` (`ba9780d`) заодно поправил две строки в html-макете
и 26 строк в `backend/scripts/gen_position_drilldown_inline.py`; правило «любой
файл в `backend/` или `frontend/`» приняло этот круг за начало реализации, и
два круга из четырёх уехали в графу «по ходу» (2/2 вместо 4/0).

Правило «границей не может быть коммит, правящий план» рассматривалось и
отвергнуто замером: коммиты реализации ставят в плане чекбоксы и правят его по
ходу, поэтому граница уезжала за конец фичи и давала 75/9 вместо 54/30.

Фичи в этом проекте идут последовательно, поэтому эвристика совпадает с началом
реализации той же фичи; при параллельных ветках её пришлось бы уточнять.

Зачем механически. Счёт по темам коммитов зависит от того, какие слова выбрал
автор, и потому не воспроизводим: по подстроке «круг ревью плана» у плана #37
правок ноль, а файл правился после гейта 3 трижды
(`docs/insights/count-answers-the-command-not-the-question.md`).

Запуск: uv run python scripts/count_plan_edits.py
"""

from __future__ import annotations

import re
import subprocess

CHECKBOX = re.compile(r"^[+-]\s*- \[[ x]\]")
SOURCE = re.compile(r"^(backend/(?!scripts/).*\.py|frontend/src/.*\.tsx?)$")


def git(*args: str) -> str:
    """Git из КОРНЯ репозитория: пути в `ls-files` иначе зависят от cwd, и
    запуск из `backend/` молча возвращал бы ноль планов."""
    root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    return subprocess.run(
        ["git", "-C", root, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout


def source_touching_commits() -> set[str]:
    """Коммиты, тронувшие продуктовый исходник — ОДНИМ проходом по истории.

    `git show` на каждый коммит стоил бы сотен процессов и почти трёх минут;
    здесь один `git log --name-only`, отдающий хэш и его файлы подряд.
    """
    found: set[str] = set()
    current = ""
    for line in git("log", "main", "--format=@%h", "--name-only").split("\n"):
        if line.startswith("@"):
            current = line[1:]
        elif line.strip() and SOURCE.match(line.strip()):
            found.add(current)
    return found


def changed_lines(commit: str, path: str) -> list[str]:
    diff = git("show", "--format=", "--unified=0", commit, "--", path)
    return [
        line
        for line in diff.split("\n")
        if line[:1] in "+-" and not line.startswith(("+++", "---")) and line[1:].strip()
    ]


def main() -> None:
    order = [c for c in git("log", "main", "--reverse", "--format=%h").split("\n") if c]
    position = {commit: i for i, commit in enumerate(order)}
    # Границей считается ПРОДУКТОВЫЙ исходник, а не любой файл в дереве кода:
    # `backend/scripts/` — инструменты, html-макет — не реализация.
    with_source = source_touching_commits()

    plans = [
        p
        for p in git("ls-files", "docs/superpowers/plans/").split("\n")
        if p.endswith(".md") and not p.endswith("TEMPLATE.md")
    ]

    print(f"{'план':<44}{'гейт 3':>8}{'+строк':>8}{'по ходу':>9}{'всего':>7}")
    totals = [0, 0]
    for path in sorted(plans):
        commits = [c for c in git("log", "main", "--format=%h", "--reverse", "--", path).split("\n") if c]
        if len(commits) < 2:
            print(f"{path.split('/')[-1][:-3]:<44}{0:>8}{0:>8}{0:>9}{0:>7}")
            continue
        start = position.get(commits[0], 0)
        first_code = next((i for i in range(start + 1, len(order)) if order[i] in with_source), len(order))
        gate, during, added = 0, 0, 0
        for commit in commits[1:]:
            body = changed_lines(commit, path)
            if not any(not CHECKBOX.match(line) for line in body):
                continue
            if position.get(commit, len(order)) < first_code:
                gate += 1
                added += sum(1 for line in body if line.startswith("+") and not CHECKBOX.match(line))
            else:
                during += 1
        totals[0] += gate
        totals[1] += during
        print(f"{path.split('/')[-1][:-3]:<44}{gate:>8}{added:>8}{during:>9}{gate + during:>7}")

    print(f"\nпланов: {len(plans)}")
    print(f"правок на гейте 3: {totals[0]}, по ходу реализации и после: {totals[1]}")


if __name__ == "__main__":
    main()
