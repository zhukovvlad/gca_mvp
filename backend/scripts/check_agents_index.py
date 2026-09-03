"""Указатель §12, преамбула ревизий и архив `docs/AGENTS-revisions.md` — 11 проверок.

Скрипт read-only: он ничего не пишет и ничего не правит. Его предмет — не текст
документов, а СТРУКТУРА двух списков, которые легко разъезжаются с реальностью:
указателя инсайтов в §12 и перечня ревизий в преамбуле `AGENTS.md`.

Почему скриптом, а не конвейером из `grep`. Прежние две команды DoD были
переписаны трижды и трижды допускали ложный зелёный: сравнивали МОЩНОСТИ вместо
множеств, считали объявлением ревизии любое упоминание версии, проверяли только
уцелевшие ссылки. В конвейере отсутствие вывода читается как успех, а здесь
каждая проверка обязана назвать, что именно сравнивалось.

Общее правило формулировок: где сопоставляются коллекции — сравниваются
МНОЖЕСТВА, а не их мощности. Единственное исключение сделано намеренно и
названо: проверка 2 как раз сравнивает мощности, потому что она страж
дубликатов.

Что стерегут одиннадцать проверок:

указатель §12
 1. строки указателя ↔ файлы `docs/insights/` — равенство множеств в обе
    стороны: ни один инсайт не пропал из указателя и ни одна строка не ведёт в
    несуществующий файл;
 2. дубликатов строк нет — уникальных путей столько же, сколько строк. Без этой
    проверки строку инсайта A можно заменить копией строки B: обе ссылки
    рабочие, а инсайт A невидим;
 3. числа и идентификаторы из строки указателя есть в тексте связанного
    инсайта — строка не вправе утверждать то, чего в файле нет;

преамбула и архив
 4. у каждой архивной записи есть строка в преамбуле — это и есть локальность
    номера: ревизию цитируют 66 раз, и читают её там, где цитируют;
 5. у каждой исторической строки преамбулы есть запись в архиве. Вместе с 4 это
    равенство множеств, но проверки разнесены намеренно: лишняя запись в архиве
    валит 4 и не трогает 5, пропавшая валит 5 и не трогает 4;
 6. действующая ревизия ровно одна: версия из заголовка документа объявлена в
    преамбуле и ОТСУТСТВУЕТ в архиве;
 7. каждая историческая строка несёт якорь `AGENTS-revisions.md#vNN` СВОЕЙ
    версии, а не соседней;
 8. каждое упоминание версии разрешается там, где его читают: множество
    упоминаний вложено в множество объявленных в преамбуле;
 9. если архивная запись несёт ссылку на спеку или devlog, строка преамбулы
    несёт хотя бы одну из них;
10. каждая историческая строка несёт хотя бы один `§N`;
11. каждая локальная ссылка архива разрешается в существующий файл.

Объявлением ревизии считается только СТРУКТУРНАЯ форма: строка преамбулы
`> **v6.N (дата)` и заголовок `## v6.N` в архиве. Упоминание версии внутри
текста другой ревизии объявлением не считается — `v6.13` цитирует «инвариант
v6.12», и считать это объявлением значило бы объявить ревизию текстом о ней.

Область проверки 8 — все вхождения `v6.N` в `AGENTS.md` целиком и во всех
`docs/**/*.md`, включая сам архив, ЗА ВЫЧЕТОМ структурных токенов объявления.
Вычет несущий: без него лишний заголовок в архиве валил бы сразу 4 и 8, и
входа, ломающего ровно 4, не существовало бы — а DoD требует по одному входу на
свойство. Текст при этом не исключается нигде: ошибочная ссылка внутри
действующей или архивной записи ловится.

Чего скрипт НЕ проверяет, и это надо знать перед тем как поверить зелёному:

* **факт обычной прозой.** Проверка 3 сверяет числа и идентификаторы, а не
  смысл. Полноту переноса прозы держит чтение на ревью;
* **полноту перечня разделов.** Проверка 10 требует хотя бы один `§N`, а не
  весь перечень тронутых разделов: строка `v6.16` с одним `§12`, без §9.1 и
  §9.2, её пройдёт. Вывести ожидаемый перечень из прозы врезки автоматически
  нельзя — прозаический текст упоминает `§N` и по другим поводам.

Список файлов берётся с диска, а не из `git ls-files`: под `docs/` нет
игнорируемых `.md`, зато новый файл проверяется раньше, чем попадёт в индекс.

Запуск (из `backend/`): uv run python scripts/check_agents_index.py
"""

from __future__ import annotations

import posixpath
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "AGENTS.md"
ARCHIVE_REL = "docs/AGENTS-revisions.md"
ARCHIVE = ROOT / ARCHIVE_REL
INSIGHTS_REL = "docs/insights"

TITLE_VERSION = re.compile(r"^#\s.*[—-]\s*(v6\.\d+)\s*$")
PREAMBLE_DECL = re.compile(r"^> \*\*(v6\.\d+) \((\d{4}-\d{2}-\d{2})\)")
ARCHIVE_DECL = re.compile(r"^## (v6\.\d+)\s*$")
VERSION = re.compile(r"v6\.\d+")
HEADING = re.compile(r"^## ")
INDEX_ROW = re.compile(r"^- \*\*\[")
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
SECTION_REF = re.compile(r"§\d")
SPEC_OR_DEVLOG = ("docs/superpowers/specs/", "docs/devlog/")

# Windows-консоль по умолчанию cp1252, и первая же кириллическая строка вывода
# роняет скрипт `UnicodeEncodeError`. Документированная команда обязана
# работать без внешнего `PYTHONIOENCODING`, поэтому поток настраивается здесь.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def read_lines(path: Path) -> list[str]:
    """Строки файла с переводами строк, приведёнными к LF."""
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")


def canonical(base_dir: str, target: str) -> str | None:
    """Путь ссылки, разрешённый от каталога СВОЕГО файла, в виде пути от корня.

    Возвращает `None` для того, что локальной ссылкой не является: внешние
    схемы и ссылки-якори внутри страницы. Без разрешения от своего каталога
    сравнивать ссылки преамбулы и архива нельзя: базовые каталоги у них разные,
    и сырое сравнение расходилось бы на каждой строке.
    """
    head = target.split("#", 1)[0].strip()
    if not head or "://" in head or head.startswith("mailto:"):
        return None
    return posixpath.normpath(posixpath.join(base_dir, head))


def preamble_of(lines: list[str]) -> list[str]:
    """Преамбула — всё до первого заголовка раздела."""
    for i, line in enumerate(lines):
        if HEADING.match(line):
            return lines[:i]
    return lines


def index_rows_of(lines: list[str]) -> list[str]:
    """Строки указателя §12."""
    start = None
    for i, line in enumerate(lines):
        if line.startswith("## 12."):
            start = i
            break
    if start is None:
        return []
    rows = []
    for line in lines[start + 1 :]:
        if HEADING.match(line):
            break
        if INDEX_ROW.match(line):
            rows.append(line)
    return rows


def preamble_entries(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Записи преамбулы: версия и её строки — от объявления до следующего."""
    pre = preamble_of(lines)
    starts = [i for i, line in enumerate(pre) if PREAMBLE_DECL.match(line)]
    entries = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(pre)
        block = pre[start:end]
        while block and block[-1].strip() in {">", ""}:
            block.pop()
        entries.append((PREAMBLE_DECL.match(pre[start]).group(1), block))
    return entries


def archive_entries(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Записи архива: версия из заголовка `## v6.N` и её текст."""
    starts = [i for i, line in enumerate(lines) if ARCHIVE_DECL.match(line)]
    entries = []
    for start in starts:
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if HEADING.match(lines[j]):
                end = j
                break
        entries.append((ARCHIVE_DECL.match(lines[start]).group(1), lines[start + 1 : end]))
    return entries


def row_link(row: str) -> str | None:
    """Цель ПЕРВОЙ ссылки строки указателя — той, что стоит в названии."""
    match = LINK.search(row)
    return match.group(1) if match else None


def row_tokens(row: str) -> set[str]:
    """Числа и идентификаторы строки указателя — без целей ссылок.

    Цели ссылок вырезаются: имя файла инсайта не является утверждением о его
    содержимом. Из бэктиков берутся идентификаторы и числа, из остальной прозы
    — только числа.
    """
    prose = LINK.sub(lambda m: m.group(0).split("](")[0] + "]", row)
    tokens: set[str] = set()
    for span in re.findall(r"`([^`]+)`", prose):
        tokens |= set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+", span))
    tokens |= set(re.findall(r"\d+", re.sub(r"`[^`]+`", " ", prose)))
    return tokens


def docs_markdown() -> list[Path]:
    """`AGENTS.md` и все `docs/**/*.md` — область проверки 8."""
    return [AGENTS, *sorted((ROOT / "docs").rglob("*.md"))]


def collect_references() -> list[tuple[str, str]]:
    """Упоминания версий: (версия, «файл:строка») за вычетом объявлений."""
    found: list[tuple[str, str]] = []
    for path in docs_markdown():
        rel = path.relative_to(ROOT).as_posix()
        for number, line in enumerate(read_lines(path), start=1):
            scanned = line
            if rel == ARCHIVE_REL and ARCHIVE_DECL.match(line):
                continue
            if rel == "AGENTS.md":
                decl = PREAMBLE_DECL.match(line)
                if decl:
                    scanned = line[: decl.start(1)] + line[decl.end(1) :]
            found.extend((version, f"{rel}:{number}") for version in VERSION.findall(scanned))
    return found


def check_1(rows: list[str], insight_files: set[str]) -> tuple[bool, list[str]]:
    indexed = {row_link(row) for row in rows} - {None}
    missing = sorted(insight_files - indexed)
    dangling = sorted(indexed - insight_files)
    details = [f"инсайт без строки указателя: {p}" for p in missing]
    details += [f"строка ведёт в несуществующий инсайт: {p}" for p in dangling]
    return not details, details


def check_2(rows: list[str]) -> tuple[bool, list[str]]:
    indexed = [row_link(row) for row in rows]
    unique = set(indexed)
    if len(unique) == len(rows):
        return True, []
    duplicates = sorted(str(path) for path, times in Counter(indexed).items() if times > 1)
    return False, [f"строк {len(rows)}, уникальных путей {len(unique)}", *(f"дубликат: {p}" for p in duplicates)]


def check_3(rows: list[str]) -> tuple[bool, list[str]]:
    details = []
    for row in rows:
        target = row_link(row)
        if target is None:
            continue
        path = ROOT / target
        if not path.is_file():
            continue  # о несуществующем файле докладывает проверка 1
        text = path.read_text(encoding="utf-8")
        absent = sorted(token for token in row_tokens(row) if token not in text)
        details += [f"{target}: в инсайте нет «{token}» из строки указателя" for token in absent]
    return not details, details


def check_4(archive_declared: set[str], preamble_declared: set[str]) -> tuple[bool, list[str]]:
    orphans = sorted(archive_declared - preamble_declared)
    return not orphans, [f"запись архива без строки в преамбуле: {v}" for v in orphans]


def check_5(preamble_declared: set[str], current: str | None, archive_declared: set[str]) -> tuple[bool, list[str]]:
    historical = preamble_declared - {current}
    absent = sorted(historical - archive_declared)
    return not absent, [f"историческая строка без записи в архиве: {v}" for v in absent]


def check_6(current: str | None, preamble_declared: set[str], archive_declared: set[str]) -> tuple[bool, list[str]]:
    if current is None:
        return False, ["в заголовке `AGENTS.md` не объявлена версия"]
    details = []
    if current not in preamble_declared:
        details.append(f"версия заголовка {current} не объявлена в преамбуле")
    if current in archive_declared:
        details.append(f"действующая ревизия {current} лежит и в архиве")
    return not details, details


def check_7(entries: list[tuple[str, list[str]]], current: str | None) -> tuple[bool, list[str]]:
    details = []
    for version, block in entries:
        if version == current:
            continue
        anchor = "AGENTS-revisions.md#" + version.replace(".", "")
        if anchor not in "\n".join(block):
            details.append(f"{version}: нет якоря своей версии ({anchor})")
    return not details, details


def check_8(preamble_declared: set[str]) -> tuple[bool, list[str]]:
    unresolved = sorted({(v, where) for v, where in collect_references() if v not in preamble_declared})
    return not unresolved, [f"{where}: упомянута {v}, не объявленная в преамбуле" for v, where in unresolved]


def check_9(
    entries: list[tuple[str, list[str]]],
    archive: dict[str, list[str]],
    current: str | None,
) -> tuple[bool, list[str]]:
    details = []
    for version, block in entries:
        if version == current:
            continue
        in_archive = {canonical("docs", target) for target in LINK.findall("\n".join(archive.get(version, [])))}
        expected = {path for path in in_archive if path and path.startswith(SPEC_OR_DEVLOG)}
        if not expected:
            continue
        offered = {canonical(".", target) for target in LINK.findall("\n".join(block))}
        if not expected & offered:
            details.append(f"{version}: в архиве есть {sorted(expected)}, а в строке преамбулы — ни одной из них")
    return not details, details


def check_10(entries: list[tuple[str, list[str]]], current: str | None) -> tuple[bool, list[str]]:
    details = [
        f"{version}: строка не называет ни одного раздела (§N)"
        for version, block in entries
        if version != current and not SECTION_REF.search("\n".join(block))
    ]
    return not details, details


def check_11() -> tuple[bool, list[str]]:
    details = []
    for number, line in enumerate(read_lines(ARCHIVE), start=1):
        for target in LINK.findall(line):
            resolved = canonical("docs", target)
            if resolved is None:
                continue
            path = ROOT / resolved
            if target.split("#", 1)[0].endswith("/"):
                if not path.is_dir():
                    details.append(f"{ARCHIVE_REL}:{number}: ссылка на несуществующий каталог {resolved}")
            elif not path.is_file():
                details.append(f"{ARCHIVE_REL}:{number}: ссылка на несуществующий файл {resolved}")
    return not details, details


def main() -> int:
    if not ARCHIVE.is_file():
        print(f"нет файла архива {ARCHIVE_REL}")
        return 1

    agents = read_lines(AGENTS)
    archive_lines = read_lines(ARCHIVE)

    title = next((TITLE_VERSION.match(line) for line in agents if TITLE_VERSION.match(line)), None)
    current = title.group(1) if title else None

    rows = index_rows_of(agents)
    insight_files = {p.relative_to(ROOT).as_posix() for p in (ROOT / INSIGHTS_REL).glob("*.md")}
    entries = preamble_entries(agents)
    preamble_declared = {version for version, _ in entries}
    archive = dict(archive_entries(archive_lines))
    archive_declared = set(archive)

    results = [
        ("строки указателя §12 ↔ файлы docs/insights/ (множества в обе стороны)", check_1(rows, insight_files)),
        ("дубликатов строк указателя нет", check_2(rows)),
        ("числа и идентификаторы строки есть в тексте инсайта", check_3(rows)),
        ("у каждой записи архива есть строка в преамбуле", check_4(archive_declared, preamble_declared)),
        ("у каждой исторической строки есть запись в архиве", check_5(preamble_declared, current, archive_declared)),
        ("действующая ревизия ровно одна", check_6(current, preamble_declared, archive_declared)),
        ("историческая строка несёт якорь своей версии", check_7(entries, current)),
        ("каждое упоминание версии объявлено в преамбуле", check_8(preamble_declared)),
        ("ссылки на спеку/devlog из архива есть в строке преамбулы", check_9(entries, archive, current)),
        ("историческая строка называет хотя бы один раздел", check_10(entries, current)),
        ("локальные ссылки архива разрешаются в существующие файлы", check_11()),
    ]

    print(f"AGENTS.md {current or '?'}: указатель §12 — {len(rows)} строк, инсайтов {len(insight_files)}; "
          f"ревизий в преамбуле {len(preamble_declared)}, в архиве {len(archive_declared)}")
    print()
    failed = 0
    for number, (name, (ok, details)) in enumerate(results, start=1):
        print(f"{number:2d}. [{'OK ' if ok else 'FAIL'}] {name}")
        for line in details:
            print(f"          {line}")
        if not ok:
            failed += 1
    print()
    print(f"Итог: {len(results) - failed} из {len(results)}.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
