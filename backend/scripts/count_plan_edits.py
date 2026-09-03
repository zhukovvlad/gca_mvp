"""Содержательные правки планов ПОСЛЕ их создания — механически, не по темам.

Правило счёта: для каждого файла плана берётся коммит, который его создал, и
все последующие, его менявшие. Правка засчитывается СОДЕРЖАТЕЛЬНОЙ, если в её
дифе есть хоть одна изменённая непустая строка, не являющаяся простановкой
чекбокса (`- [ ]` <-> `- [x]`).

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


def git(*args: str) -> str:
    """Git из КОРНЯ репозитория: пути в `ls-files` иначе зависят от cwd, и
    запуск из `backend/` молча возвращал бы ноль планов."""
    root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()
    return subprocess.run(
        ["git", "-C", root, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout


def substantive_edits(path: str) -> tuple[int, int, int]:
    """(всего коммитов, содержательных правок после создания, дописано строк)."""
    commits = [c for c in git("log", "main", "--format=%h", "--reverse", "--", path).split("\n") if c]
    edits = added = 0
    for commit in commits[1:]:
        diff = git("show", "--format=", "--unified=0", commit, "--", path)
        changed = [
            line
            for line in diff.split("\n")
            if line[:1] in "+-" and not line.startswith(("+++", "---")) and line[1:].strip()
        ]
        if any(not CHECKBOX.match(line) for line in changed):
            edits += 1
            added += sum(1 for line in changed if line.startswith("+") and not CHECKBOX.match(line))
    return len(commits), edits, added


if __name__ == "__main__":
    plans = [
        p
        for p in git("ls-files", "docs/superpowers/plans/").split("\n")
        if p.endswith(".md") and not p.endswith("TEMPLATE.md")
    ]
    print(f"{'план':<46}{'коммитов':>9}{'правок':>8}{'+строк':>8}")
    total = 0
    for path in sorted(plans):
        commits, edits, added = substantive_edits(path)
        total += edits
        print(f"{path.split('/')[-1][:-3]:<46}{commits:>9}{edits:>8}{added:>8}")
    print(f"\nпланов: {len(plans)}, содержательных правок после создания: {total}")
