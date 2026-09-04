"""Указатель §12, ревизии, архив и маршрутизатор §11 → `docs/pitfalls/` — 16 проверок.

Скрипт read-only: он ничего не пишет и ничего не правит. Его предмет — не текст
документов, а СТРУКТУРА трёх списков, которые легко разъезжаются с реальностью:
указателя инсайтов в §12, перечня ревизий в преамбуле `AGENTS.md` и
маршрутизатора граблей в §11.

Почему скриптом, а не конвейером из `grep`. Прежние две команды DoD были
переписаны трижды и трижды допускали ложный зелёный: сравнивали МОЩНОСТИ вместо
множеств, считали объявлением ревизии любое упоминание версии, проверяли только
уцелевшие ссылки. В конвейере отсутствие вывода читается как успех, а здесь
каждая проверка обязана назвать, что именно сравнивалось.

Общее правило формулировок: где сопоставляются коллекции — сравниваются
МНОЖЕСТВА, а не их мощности. Так работают проверки 1, 4, 5 и 8. Два стража
кратности сделаны исключениями намеренно и названы: проверка 2 сравнивает
мощности, проверка 6 считает объявления по списку — множество как раз и
скрывает дубликат, который они ищут. Остальные (3, 7, 9, 10, 11) — построчные
условия и коллекций не сопоставляют вовсе.

Что стерегут шестнадцать проверок:

указатель §12
 1. строки указателя ↔ файлы `docs/insights/` — равенство множеств в обе
    стороны: ни один инсайт не пропал из указателя и ни одна строка не ведёт в
    несуществующий файл;
 2. дубликатов строк нет — уникальных путей столько же, сколько строк. Без этой
    проверки строку инсайта A можно заменить копией строки B: обе ссылки
    рабочие, а инсайт A невидим;
 3. числа и идентификаторы из строки указателя есть в тексте связанного
    инсайта — строка не вправе утверждать то, чего в файле нет. Сверяются
    ТОКЕНЫ с ТОКЕНАМИ: подстрочное сравнение зеленело и на `prepare` внутри
    `prepare_threshold`, и на `34` внутри `134`;

преамбула и архив
 4. у каждой архивной записи есть строка в преамбуле — это и есть локальность
    номера: ревизию цитируют 66 раз, и читают её там, где цитируют;
 5. у каждой исторической строки преамбулы есть запись в архиве. Вместе с 4 это
    равенство множеств, но проверки разнесены намеренно: лишняя запись в архиве
    валит 4 и не трогает 5, пропавшая валит 5 и не трогает 4;
 6. действующая ревизия ровно одна: версия из заголовка документа объявлена в
    преамбуле РОВНО ОДИН раз и ОТСУТСТВУЕТ в архиве. Кратность считается по
    списку объявлений — множество схлопывало два объявления в одно;
 7. каждая историческая строка несёт якорь `AGENTS-revisions.md#vNN` СВОЕЙ
    версии, а не соседней;
 8. каждое упоминание версии разрешается там, где его читают: множество
    упоминаний вложено в множество объявленных в преамбуле;
 9. если архивная запись несёт ссылку на спеку или devlog, строка преамбулы
    несёт хотя бы одну из них;
10. каждая историческая строка несёт хотя бы один `§N`;
11. каждая локальная ссылка архива разрешается в существующий файл;

маршрутизатор §11 и корпус `docs/pitfalls/`
12. цели строк маршрутизации ↔ файлы `docs/pitfalls/` — равенство множеств в
    обе стороны. Файл без строки в §11 и строка, ведущая в никуда, — это одно
    свойство, а не два: вход, ломающий только одно из них, не существует;
13. кратность строк маршрутизации: строк ровно столько, сколько уникальных
    целей. Множества этого не ловят — вторая строка на `frontend.md` оставляет
    их равными, а обещанных семи строк становится восемь. Строкой маршрутизации
    считается ЛЮБОЙ пункт верхнего уровня §11 с локальной ссылкой, а не только
    каноническая форма: иначе восьмой маршрут обходит и 12, и 13 сменой
    оформления;
14. ни один пункт не сдублирован по всему корпусу. Сравнивается СВЁРНУТЫЙ
    ПОЛНЫЙ текст пункта (пробелы свёрнуты, регистр сохранён), а не первая
    строка: сравнение по началу запретило бы два разных пункта с одинаковым
    зачином и проверяло бы лишь часть текста. Пункт кончается следующим пунктом
    верхнего уровня либо концом файла — пустая строка внутри пункта законна, и
    по ней границу проводить нельзя;
15. каждый файл области несёт шапку «когда читать» — ровно одну;
16. каждый файл области несёт хотя бы один распознанный пункт. Заведено
    отдельно от 15: файл, из которого удалили все грабли, оставив шапку, прошёл
    бы проверку «непуст», а читать в объявленной области было бы нечего.

Проверки 15 и 16 держатся на том, что пункт распознаётся НЕЗАВИСИМО от шапки:
`pitfall_items` не ищет шапку и не пропускает строки до неё. Стоило бы искать
пункты после распознанной шапки — и снятие шапки красило бы сразу обе, то есть
изолированного входа для 16 не существовало бы.

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
* **исключительность ссылок и якорей.** Проверки 7, 9 и 10 требуют НАЛИЧИЯ, а
  не единственности: строка с верным якорем своей версии пройдёт и тогда, когда
  рядом стоит якорь чужой. Сказано прямо, чтобы «якорь своей версии» не читалось
  как «только своей»;
* **полноту перечня разделов.** Проверка 10 требует хотя бы один `§N`, а не
  весь перечень тронутых разделов: строка `v6.16` с одним `§12`, без §9.1 и
  §9.2, её пройдёт. Вывести ожидаемый перечень из прозы врезки автоматически
  нельзя — прозаический текст упоминает `§N` и по другим поводам;
* **полноту переезда граблей.** «Сумма пунктов равна 46» стражем стоять не
  может: это свойство ПЕРЕЕЗДА, а не установившегося состояния, и первая же
  фича, заводящая граблю, красила бы его законно — страж, который учат глушить,
  перестаёт стеречь вообще. Полнота сверена один раз разовым скриптом
  посимвольно (`docs/devlog/2026-09-04-pitfalls-routing.md`);
* **что нужный файл граблей прочитан.** Ни один скрипт не отличит «прочитал» от
  «написал, что прочитал». Дисциплина чтения — пункт DoD §10, проверяемый
  чтением на ревью, и названа она слабее механизма намеренно.

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
PITFALLS_REL = "docs/pitfalls"

TITLE_VERSION = re.compile(r"^#\s.*[—-]\s*(v6\.\d+)\s*$")
PREAMBLE_DECL = re.compile(r"^> \*\*(v6\.\d+) \((\d{4}-\d{2}-\d{2})\)")
ARCHIVE_DECL = re.compile(r"^## (v6\.\d+)\s*$")
VERSION = re.compile(r"v6\.\d+")
HEADING = re.compile(r"^## ")
INDEX_ROW = re.compile(r"^- \*\*\[")
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
SECTION_REF = re.compile(r"§\d")
SPEC_OR_DEVLOG = ("docs/superpowers/specs/", "docs/devlog/")

# Шапка файла области — ровно одна строка, и её литерал закреплён здесь, а не
# оставлен вкусу: иначе проверка 15 стерегла бы формулировку исполнителя.
PITFALL_HEADER = re.compile(r"^\*\*Когда читать:\*\*")
# Пункт — строка, начинающаяся с `- `: тот же формат, в котором пункты стояли в
# §11. Предикат НЕ зависит от шапки, и это условие изолированности входов 15 и 16.
PITFALL_ITEM = re.compile(r"^- ")
# Строка маршрутизации §11 — любой пункт верхнего уровня, а не только
# каноническая форма `- **[`: почему так, сказано в `router_lines`.
ROUTER_ROW = re.compile(r"^- ")

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


def rows_under(lines: list[str], heading: str, row: re.Pattern[str]) -> list[str]:
    """Строки раздела `heading`, подходящие под предикат `row`, в порядке появления.

    Предикат — параметр, а не константа: у §12 он узкий (каноническая форма
    строки указателя), у §11 широкий. Разница объяснена в `router_lines`.
    """
    start = None
    for i, line in enumerate(lines):
        if line.startswith(heading):
            start = i
            break
    if start is None:
        return []
    rows = []
    for line in lines[start + 1 :]:
        if HEADING.match(line):
            break
        if row.match(line):
            rows.append(line)
    return rows


def index_rows_of(lines: list[str]) -> list[str]:
    """Строки указателя §12."""
    return rows_under(lines, "## 12.", INDEX_ROW)


def local_targets(row: str) -> list[str]:
    """Локальные цели ссылок строки, разрешённые от корня, в порядке появления."""
    resolved = [canonical(".", target) for target in LINK.findall(row)]
    return [target for target in resolved if target is not None]


def router_lines(lines: list[str]) -> list[str]:
    """Строки маршрутизации §11: ЛЮБОЙ пункт верхнего уровня с локальной ссылкой.

    Предикат намеренно шире канонической формы `- **[имя](путь)** — …`. Узкий
    предикат делал восьмой маршрут, написанный иначе, НЕВИДИМЫМ сразу для 12 и
    13: замерено на ревью PR #43 — строка `- [Повтор](docs/pitfalls/frontend.md)`
    проходила обе проверки, хотя маршрутов в разделе становилось восемь. Обход
    оформлением — не то, от чего должна защищать проверка кратности.

    Требование локальной ссылки оставлено: пункт §11 без ссылки маршрутом не
    является и целью для сравнения множеств быть не может.
    """
    return [row for row in rows_under(lines, "## 11.", ROUTER_ROW) if local_targets(row)]


def router_targets(lines: list[str]) -> list[str]:
    """Цели строк маршрутизации — СПИСКОМ, с кратностью.

    Кратность несущая: проверка 13 ищет ровно тот дубликат, который множество
    скрывает. Цель строки — ПЕРВАЯ её локальная ссылка.
    """
    return [local_targets(row)[0] for row in router_lines(lines)]


def pitfall_items(path: Path) -> list[str]:
    """СВЁРНУТЫЕ полные тексты пунктов файла области, в порядке появления.

    Свёртка (пробелы схлопнуты, регистр сохранён) уместна именно здесь: два
    пункта, различающиеся только пробелами, — это дубль. Посимвольная сверка
    жила в разовом скрипте переезда, а не тут.

    ГРАНИЦА ПУНКТА — следующий пункт верхнего уровня либо конец файла, и это НЕ
    пустая строка. Пункт из двух абзацев — законный Markdown, а прежняя граница
    теряла весь текст после первой пустой строки: два РАЗНЫХ пункта с общим
    первым абзацем становились дублем, и проверка 14 краснела на верном файле.
    Найдено внешним ревью PR #43, воспроизведено на двухабзацном входе. Граница
    та же, что у разового сверщика переезда, — иначе «полный текст» в имени
    проверки означал бы у них разное.

    Шапку функция НЕ ищет и строки до неё НЕ пропускает: от этого зависит
    изолированность входов проверок 15 и 16.
    """
    lines = read_lines(path)
    starts = [i for i, line in enumerate(lines) if PITFALL_ITEM.match(line)]
    bounds = [*starts, len(lines)]
    return [" ".join("\n".join(lines[bounds[n] : bounds[n + 1]]).split()) for n in range(len(starts))]


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


def text_tokens(text: str) -> set[str]:
    """Идентификаторы и числа текста — МНОЖЕСТВОМ, а не для поиска подстрокой.

    Проверка 3 обязана сверять токены с токенами. Подстрочное `token in text`
    давало ложный зелёный в обе стороны: `prepare` считался найденным внутри
    `prepare_threshold`, а `34` — внутри `134`. Поймано внешним ревью PR #41.
    """
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+", text))


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
        present = text_tokens(path.read_text(encoding="utf-8"))
        absent = sorted(row_tokens(row) - present)
        details += [f"{target}: в инсайте нет «{token}» из строки указателя" for token in absent]
    return not details, details


def check_4(archive_declared: set[str], preamble_declared: set[str]) -> tuple[bool, list[str]]:
    orphans = sorted(archive_declared - preamble_declared)
    return not orphans, [f"запись архива без строки в преамбуле: {v}" for v in orphans]


def check_5(preamble_declared: set[str], current: str | None, archive_declared: set[str]) -> tuple[bool, list[str]]:
    historical = preamble_declared - {current}
    absent = sorted(historical - archive_declared)
    return not absent, [f"историческая строка без записи в архиве: {v}" for v in absent]


def check_6(current: str | None, preamble_versions: list[str], archive_declared: set[str]) -> tuple[bool, list[str]]:
    """Действующая ревизия ровно одна: одно объявление в преамбуле и ни одного в архиве.

    Кратность считается по СПИСКУ объявлений, а не по множеству: множество
    схлопывает два объявления `current` в одно, и проверка зеленела на
    преамбуле, объявляющей действующую ревизию дважды (внешнее ревью PR #41).
    Граница: кратность стережётся только у действующей. Дубль ИСТОРИЧЕСКОЙ
    строки ничего не скрывает — номер на месте и его запись в архиве тоже, —
    тогда как две действующие делают документ противоречивым о том, что в силе.
    """
    if current is None:
        return False, ["в заголовке `AGENTS.md` не объявлена версия"]
    details = []
    times = preamble_versions.count(current)
    if times == 0:
        details.append(f"версия заголовка {current} не объявлена в преамбуле")
    elif times > 1:
        details.append(f"действующая ревизия {current} объявлена в преамбуле {times} раза, а не один")
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


def check_12(lines: list[str], files: list[Path]) -> tuple[bool, list[str]]:
    routed = set(router_targets(lines))
    present = {path.relative_to(ROOT).as_posix() for path in files}
    details = [f"файл области без строки маршрутизации в §11: {p}" for p in sorted(present - routed)]
    details += [f"строка §11 ведёт в несуществующий файл области: {p}" for p in sorted(routed - present)]
    return not details, details


def check_13(lines: list[str]) -> tuple[bool, list[str]]:
    """Кратность строк маршрутизации: множества дубликат как раз и скрывают."""
    targets = router_targets(lines)
    rows = router_lines(lines)
    unique = set(targets)
    if len(rows) == len(unique):
        return True, []
    duplicates = sorted(path for path, times in Counter(targets).items() if times > 1)
    return False, [
        f"строк маршрутизации {len(rows)}, уникальных целей {len(unique)}",
        *(f"дубликат: {p}" for p in duplicates),
    ]


def check_14(files: list[Path]) -> tuple[bool, list[str]]:
    seen: dict[str, str] = {}
    details = []
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        for item in pitfall_items(path):
            if item in seen:
                details.append(f"пункт сдублирован: {seen[item]} и {rel} — «{item[:60]}…»")
            else:
                seen[item] = rel
    return not details, details


def check_15(files: list[Path]) -> tuple[bool, list[str]]:
    details = []
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        times = sum(1 for line in read_lines(path) if PITFALL_HEADER.match(line))
        if times != 1:
            details.append(f"{rel}: строк шапки «Когда читать:» — {times}, а не одна")
    return not details, details


def check_16(files: list[Path]) -> tuple[bool, list[str]]:
    details = [
        f"{path.relative_to(ROOT).as_posix()}: ни одного распознанного пункта"
        for path in files
        if not pitfall_items(path)
    ]
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
    preamble_versions = [version for version, _ in entries]
    preamble_declared = set(preamble_versions)
    archive = dict(archive_entries(archive_lines))
    archive_declared = set(archive)
    pitfalls_dir = ROOT / PITFALLS_REL
    pitfall_paths = sorted(pitfalls_dir.glob("*.md")) if pitfalls_dir.is_dir() else []

    results = [
        ("строки указателя §12 ↔ файлы docs/insights/ (множества в обе стороны)", check_1(rows, insight_files)),
        ("дубликатов строк указателя нет", check_2(rows)),
        ("числа и идентификаторы строки есть в тексте инсайта", check_3(rows)),
        ("у каждой записи архива есть строка в преамбуле", check_4(archive_declared, preamble_declared)),
        ("у каждой исторической строки есть запись в архиве", check_5(preamble_declared, current, archive_declared)),
        ("действующая ревизия ровно одна", check_6(current, preamble_versions, archive_declared)),
        ("историческая строка несёт якорь своей версии", check_7(entries, current)),
        ("каждое упоминание версии объявлено в преамбуле", check_8(preamble_declared)),
        ("ссылки на спеку/devlog из архива есть в строке преамбулы", check_9(entries, archive, current)),
        ("историческая строка называет хотя бы один раздел", check_10(entries, current)),
        ("локальные ссылки архива разрешаются в существующие файлы", check_11()),
        ("строки §11 ↔ файлы docs/pitfalls/ (множества в обе стороны)", check_12(agents, pitfall_paths)),
        ("дубликатов строк маршрутизации §11 нет", check_13(agents)),
        ("ни один пункт граблей не сдублирован (свёрнутый полный текст)", check_14(pitfall_paths)),
        ("каждый файл области несёт шапку «Когда читать:»", check_15(pitfall_paths)),
        ("каждый файл области несёт хотя бы один пункт", check_16(pitfall_paths)),
    ]

    print(f"AGENTS.md {current or '?'}: указатель §12 — {len(rows)} строк, инсайтов {len(insight_files)}; "
          f"ревизий в преамбуле {len(preamble_declared)}, в архиве {len(archive_declared)}; "
          f"маршрутизатор §11 — {len(router_lines(agents))} строк, файлов граблей {len(pitfall_paths)}")
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
