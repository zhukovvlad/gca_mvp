"""Во что превращается план по правилу §9.1 «тела запрещены, подписи разрешены».

Классификация блока кода:
  тела тестов     — есть `def test_` / `it(` / `describe(` / `assert`
  команды         — shell
  интерфейс       — только подписи и типы: class/dataclass/def без тела, константы
  тела реализации — всё прочее

Проза (вне блоков) и строки-ограждения считаются отдельно, чтобы сумма
категорий сходилась с `wc -l` — то же требование, что §1 спеки предъявляет к
своей таблице.

Тела тестов пересчитываются в утверждения по замеренному отношению 425 : 67
(живой набор `tests/unit/test_round_unallocated.py` против блока утверждений
в спеке `2026-09-03-plans-as-assertions-design.md` §3.4). Это единственное
допущение, и оно снято с одного модуля.

Запуск: uv run python scripts/measure_plan_shrink.py <файл плана>...
"""

from __future__ import annotations

import pathlib
import re
import sys

RATIO = 425 / 67

SIGNATURE = re.compile(
    r"^\s*(@|class |def [A-Za-z_]+\(.*\)\s*->|def [A-Za-z_]+\(.*\)\s*:?\s*$|"
    r"[A-Z_][A-Z0-9_]*\s*[:=]|[a-z_]+\s*:\s*[A-Za-z\[])"
)
TEST_BODY = re.compile(r"\bdef test_|\bit\(|\bdescribe\(|^\s*assert ", re.M)
SHELL = re.compile(r"^\s*(just|git|uv|npx|npm|cd|psql) ", re.M)


def classify(lang: str, body: list[str]) -> str:
    text = "\n".join(body)
    if lang in ("bash", "sh", "console", "shell") or (lang == "" and SHELL.search(text)):
        return "команды"
    if TEST_BODY.search(text):
        return "тела тестов"
    meaningful = [line for line in body if line.strip() and not line.strip().startswith("#")]
    if not meaningful:
        return "команды"
    if all(SIGNATURE.match(line) or line.strip().endswith((",", "(", ")", "]", '"""')) for line in meaningful):
        return "интерфейс"
    return "тела реализации"


def measure(path: pathlib.Path) -> None:
    lines = path.read_text(encoding="utf-8").split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]  # артефакт split, иначе счёт на строку больше wc -l

    buckets: dict[str, int] = {}
    prose = fences = kept_blocks = 0
    inside = False
    lang = ""
    body: list[str] = []

    for line in lines:
        if line.startswith("```"):
            fences += 1
            if inside:
                kind = classify(lang, body)
                buckets[kind] = buckets.get(kind, 0) + len(body)
                if kind in ("интерфейс", "команды"):
                    kept_blocks += 1
                body = []
            else:
                lang = line[3:].strip()
            inside = not inside
        elif inside:
            body.append(line)
        else:
            prose += 1

    tests = buckets.get("тела тестов", 0)
    impl = buckets.get("тела реализации", 0)
    keep = buckets.get("интерфейс", 0) + buckets.get("команды", 0)
    total = len(lines)
    checksum = prose + fences + keep + tests + impl
    assertions = round(tests / RATIO)
    new = prose + kept_blocks * 2 + keep + assertions

    print(f"\n=== {path.name} ===")
    print(f"  wc -l                      {total:>6}")
    print(f"  проза                      {prose:>6}   остаётся")
    print(f"  строки-ограждения          {fences:>6}   остаются у сохранённых блоков")
    print(f"  интерфейс + команды        {keep:>6}   остаётся")
    print(f"  тела тестов                {tests:>6}   -> {assertions} строк утверждений")
    print(f"  тела реализации            {impl:>6}   -> 0, план их не описывает")
    print(f"  сумма категорий            {checksum:>6}   {'сходится' if checksum == total else 'РАСХОДИТСЯ'}")
    print(f"  СТАНЕТ                     {new:>6}   ({total / new:.1f}x)")


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        measure(pathlib.Path(arg))
