"""Перечислитель единиц ревью плана: подкоманды `assertions` (задача 1) и `hunks` (задача 2).

Скрипт read-only: он только читает план/git и печатает строки, ничего не
пишет на диск и не трогает git — ни дерево, ни индекс (даже `git add -N`).
`assertions` превращает раздел «Утверждения» задачи плана в список пар
«идентификатор пункта, текст пункта»; `hunks` перечисляет git-hunk'и рабочего
дерева против `HEAD` (staged и unstaged вместе) плюс неотслеживаемые
неигнорируемые файлы — по одной строке на hunk. Обе печатают поля через
табуляцию и не несут отметок времени, чтобы два прогона сравнивались `diff`-ом.

Идентификатор пункта «Утверждения» — `A<задача>.<номер>` для пункта верхнего
уровня (нумерация с единицы) и `A<задача>.<номер><буква>` для вложенного
пункта (буква — `a`, `b`, … — считается заново внутри КАЖДОГО верхнего
пункта, а не сквозно по задаче). Пункт, продолженный на следующей строке без
своего `- `, даёт ОДНУ строку вывода: текст продолжения дописывается
пробелом, а не заводит новый пункт.

Идентификатор hunk'а — `H:<8 hex>`, схема и нормализация тела описаны у
`hunk_id`. `argparse` заведён сразу с подпарсерами (задача 1 уже это учла),
и `emit` печатает строки ЛЮБОЙ ширины: два поля у `assertions`, четыре у
`hunks`.

Запуск (путь `--plan` — от корня репозитория, независимо от того, откуда
запущена сама команда):

    cd backend && uv run python scripts/enumerate_review_units.py \
        assertions --plan docs/superpowers/plans/ДАТА-тема.md --task 4
    cd backend && uv run python scripts/enumerate_review_units.py hunks
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

# Windows-консоль по умолчанию cp1252, и первая же кириллическая строка вывода
# роняет скрипт `UnicodeEncodeError`. Документированная команда обязана
# работать без внешнего `PYTHONIOENCODING`, поэтому поток настраивается здесь —
# тот же приём, что в `count_plan_edits.py` и `check_agents_index.py`.
#
# ОБА потока, не только `stdout`: отказы `assertions` (задача не найдена, нет
# раздела «Утверждения») печатаются в `stderr`, и без его настройки cp1252
# заменяет кириллицу на `backslashreplace`-escape'ы — падения нет, но
# сообщение нечитаемо, а «ёлочки» `«»` (cp1252 их кодирует байтом `0xab`)
# вперемешку с ASCII-escape'ами дают поток, который не декодируется как UTF-8
# вовсе (найдено ревью круга 2: `stderr` был настроен только наполовину —
# `count_plan_edits.py`/`check_agents_index.py` несут тот же изъян, но чинить
# их не в этой задаче).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Заголовок задачи и граница блока: следующий заголовок ЛЮБОГО уровня `### ` —
# или разделитель `---` на отдельной строке. `\d+` в TASK_HEADER не совпадает
# по префиксу: группа сравнивается с номером задачи целиком, поэтому «Task 1»
# не путается с «Task 10».
TASK_HEADER = re.compile(r"^### Task (\d+):")
NEXT_HEADER = re.compile(r"^### ")
SEPARATOR = "---"

# Раздел «Утверждения»: список после этой строки и до следующего жирного
# заголовка раздела («Имена», «Проверка» и т.п. — форма `**Слово**` целиком на
# своей строке, БЕЗ отступа: строка-продолжение пункта, целиком состоящая из
# жирного текста (`  **термин**`), заголовком не считается — маркер сверяется
# с НЕотступленной строкой, `.strip()` перед сравнением не делается). Пункт
# верхнего уровня — строка `- …` БЕЗ отступа; вложенный — та же форма, но С
# отступом; продолжение — отступ без `- `.
ASSERTIONS_HEADER = "**Утверждения**"
BOLD_HEADER = re.compile(r"^\*\*[^*]+\*\*\s*$")
TOP_ITEM = re.compile(r"^- ")
NESTED_ITEM = re.compile(r"^\s+- ")

# Ограждённый блок кода (``` ... ```). Внутри него ни один структурный маркер
# не действует — это решение ревью round 1 (I5): маркер внутри ограждения не
# структурный маркер, а иллюстрация в тексте задачи, и разбор обязан её
# игнорировать целиком, включая сами строки-ограничители.
FENCE_MARKER = re.compile(r"^```")


@dataclass(frozen=True)
class Hunk:
    """Один git-hunk знаменателя `hunks`: путь, контекст `@@`, тело, счётчик.

    `body` — уже НОРМАЛИЗОВАННОЕ тело (см. `hunk_id`): только строки `+`/`-`,
    заголовок `@@` отброшен, хвостовые пробелы сняты, склеены `\n` без
    хвостового переноса. `added`/`removed` — число строк `+`/`-` в теле; это
    независимое от `hunk_id` наблюдение, напечатанное как `+N/-M`, а не
    производная от строки `body`, которую вызывающему коду пришлось бы
    пересчитывать самому.
    """

    path: str
    context: str
    body: str
    added: int
    removed: int


# `diff --git a/<путь> b/<путь>` — заголовок секции файла. Скрипт форсирует
# `--src-prefix=a/ --dst-prefix=b/` при вызове `git diff`, поэтому путь у
# НЕпереименованного файла одинаков в обеих группах, и группа 2 (новая
# сторона) — источник пути для ПЕРЕИМЕНОВАНИЯ и для секций без единого `@@`
# (см. `_metadata_only_hunk`), где строк `---`/`+++` нет вовсе.
#
# У самой этой строки есть неоднозначность (найдено ревью круга 1, I4): путь,
# содержащий литеральную подстроку ` b/`, совпадает с разделителем между
# двумя половинами строки — путь `x b/y.txt` разобрался бы как `y.txt`.
# Секция с содержательным `@@`-hunk'ом эту неоднозначность не несёт: там путь
# берётся из строк `+++`/`---` (см. `_section_path`), у которых разделитель —
# ФИКСИРОВАННЫЙ 6-символьный префикс, а не поиск подстроки. Для двоичных и
# metadata-секций (эта строка — единственный источник пути) неоднозначность
# на путях с ` b/`/` and b/` внутри остаётся непочиненной — вне охвата этого
# раунда ревью, найденный дефект был предъявлен именно на текстовом hunk'е.
DIFF_GIT_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")

# `Binary files X and Y differ` — единственный маркер, который у git ЕСТЬ у
# двоичной секции ВМЕСТО `@@`-hunk'ов. X/Y — `a/<путь>`, `b/<путь>` или
# буквально `/dev/null` (сторона отсутствует: файл добавлен или удалён).
BINARY_DIFFER = re.compile(r"^Binary files (.+) and (.+) differ$")

# Заголовок текстового hunk'а: `@@ -l,s +l,s @@`, часто с хвостовым текстом
# контекста (`@@ ... @@ def foo():`). `.*@@` жадный — это не проблема: для
# `re.match` важно лишь НАЙТИ вторую пару `@@` где-то на строке, и жадность
# её не теряет.
HUNK_HEADER = re.compile(r"^@@ .*@@")

# `?? <путь>` — неотслеживаемый файл в `git status --porcelain`. Игнорируемые
# файлы под этот префикс не попадают вовсе (порцелан без `--ignored` их не
# показывает) — значит, «игнорируемый файл не даёт hunk'ов» выполняется самим
# выбором команды, без отдельной проверки `.gitignore` в коде.
UNTRACKED_STATUS = re.compile(r"^\?\? (.+)$")


def _fenced_mask(lines: Sequence[str]) -> list[bool]:
    """Для каждой строки — стоит ли она внутри ```-ограждения (сами границы — тоже внутри).

    Ограждение переключается по каждой встреченной строке-ограничителю; сама
    строка-ограничитель помечается «внутри» — она не структурный маркер, а
    часть иллюстрации, и не обязана участвовать в поиске границ блока,
    разделов или пунктов. Незакрытое до конца списка ограждение остаётся
    открытым — это осознанно: недописанный пример не должен «раскрыть»
    остаток документа обратно в структурный текст.
    """
    mask: list[bool] = []
    inside = False
    for line in lines:
        if FENCE_MARKER.match(line.strip()):
            inside = not inside
            mask.append(True)
        else:
            mask.append(inside)
    return mask


def _assertions_header_index(block: Sequence[str], mask: Sequence[bool]) -> int | None:
    """Индекс строки `**Утверждения**` в `block`, единственный источник правды.

    Единственное место, которое РЕШАЕТ, есть ли у задачи раздел «Утверждения»:
    и `assertion_items` (разбор пунктов), и `_run_assertions` (код возврата)
    обязаны спрашивать именно эту функцию, а не заводить свой поиск этой же
    строки. До круга 1 ревью оба места искали `**Утверждения**` одинаково
    наивно (без учёта ```-ограждений) и потому СОГЛАСОВАННО ошибались; когда
    круг 1 сделал `assertion_items` fence-aware, а `_run_assertions` — нет,
    признак раздвоился: маркер `**Утверждения**` ТОЛЬКО внутри примера кода
    заставлял наивную половину (`_run_assertions`) считать раздел существующим,
    а fence-aware половину (`assertion_items`) — честно возвращать `[]`, и
    задача без утверждений давала код 0 с пустым stdout (найдено ревью round
    2, Critical). Один источник правды делает такое расхождение невозможным
    структурно, а не только сегодня.

    `mask` — параметр, а не пересчёт внутри: единственный вызывающий, у
    которого уже есть маска для СВОИХ прочих нужд (`assertion_items`), не
    обязан считать ```-ограждения дважды; `_run_assertions`, которому маска
    больше ни для чего не нужна, считает её один раз специально для этого
    вызова.
    """
    for i, line in enumerate(block):
        if mask[i]:
            continue
        if line.strip() == ASSERTIONS_HEADER:
            return i
    return None


def repo_root() -> Path:
    """Корень репозитория — по ТЕКУЩЕМУ каталогу процесса, а не по `__file__`.

    `git rev-parse --show-toplevel` вызывается без `-C`: результат зависит от
    cwd, в котором запущен процесс. Так устроен `git()` в `count_plan_edits.py`,
    и от этого же зависит контракт задачи — путь `--plan` задаётся от корня
    репозитория, а запускать команду предполагается из `backend/`; прогон из
    корня обязан разрешать тот же путь.
    """
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return Path(completed.stdout.strip())


def task_block(lines: list[str], task: int) -> list[str]:
    """Строки задачи `task` плана — от `### Task N:` до ближайшей границы.

    Границей блока служит ближайший следующий заголовок `### ` (заголовок
    ЛЮБОЙ задачи, а значит и её собственных подразделов не бывает — заголовки
    задач в плане плоские) либо строка-разделитель `---`. Если у последней
    задачи плана нет ни следующего заголовка, ни разделителя, блок идёт до
    конца списка строк.

    Задачи с таким номером нет в плане — возвращается пустой список. Это
    сигнал, отличный от «задача есть, а раздела „Утверждения“ в ней нет»:
    вызывающий код (`main`) обязан различать два отказа и печатать разные
    сообщения, поэтому здесь не бросается исключение и не возвращается
    какая-либо реплика текста — только пустота, которую вызывающий код
    проверяет сам.

    Строки внутри ```-ограждения (`_fenced_mask`) в поиске границы не
    участвуют: `### Task N:`, `### ` и голый `---`, встреченные там, — это
    иллюстрация в тексте задачи (например, пример markdown в разделе
    «Interfaces»), а не настоящая соседняя задача и не настоящий разделитель.
    Без этого пропуска пример кода мог бы оборвать блок раньше времени или
    заставить поиск заголовка найти чужой номер задачи внутри чужого примера.
    """
    mask = _fenced_mask(lines)

    start = None
    for i, line in enumerate(lines):
        if mask[i]:
            continue
        match = TASK_HEADER.match(line)
        if match and match.group(1) == str(task):
            start = i
            break
    if start is None:
        return []

    end = len(lines)
    for i in range(start + 1, len(lines)):
        if mask[i]:
            continue
        if NEXT_HEADER.match(lines[i]) or lines[i].strip() == SEPARATOR:
            end = i
            break
    return lines[start:end]


def assertion_items(block: list[str]) -> list[tuple[str, str]]:
    """Пары «идентификатор пункта, текст пункта» раздела «Утверждения» блока.

    Номер задачи читается из первой строки блока (`### Task N:` — так его и
    отдаёт `task_block`); если блок пуст или не начинается с такого заголовка,
    возвращается пустой список. Раздел ищется по строке `**Утверждения**` и
    обрезается по первому следующему жирному заголовку (`**Имена**»,
    «**Проверка**» и т.п.); если раздела нет вовсе, тоже возвращается пустой
    список — оба случая для вызывающего кода неотличимы ПО ЭТОЙ функции и
    должны проверяться им отдельно (см. `task_block` и `main`).

    Текст пункта передаётся как есть, включая хвостовую пунктуацию исходника
    (`;`, `.`) — функция перечисляет пункты, а не редактирует их прозу.
    Пункт из нескольких строк (продолжение — отступ без `- `) даёт ОДНУ пару:
    строки склеиваются пробелом.

    Ограждённые ```-блоки (`_fenced_mask`) не участвуют ни в поиске самого
    раздела, ни в поиске его конца, ни в разборе пунктов: строка внутри
    ограждения никогда не заголовок, никогда не начало пункта — она
    дописывается к тексту ТЕКУЩЕГО пункта как есть (включая саму строку
    ограничителя), пока ограждение не закрылось. Так пример markdown внутри
    пункта («вот как выглядит ```» и т.п.) не обрывает раздел раньше времени
    и не превращает строку внутри примера в фантомный пункт.
    """
    if not block:
        return []
    header = TASK_HEADER.match(block[0])
    if header is None:
        return []
    task = header.group(1)

    mask = _fenced_mask(block)

    start = _assertions_header_index(block, mask)
    if start is None:
        return []

    body: list[str] = []
    body_fenced: list[bool] = []
    for i in range(start + 1, len(block)):
        # Заголовком-терминатором раздела считается только НЕотступленная
        # строка целиком из жирного (I4): `.strip()` перед сравнением не
        # делается, иначе строка-продолжение пункта вида `  **термин**`
        # молча обрывает раздел, а всё, что после неё, теряется.
        if not mask[i] and BOLD_HEADER.match(block[i]):
            break
        body.append(block[i])
        body_fenced.append(mask[i])

    items: list[tuple[str, str]] = []
    top_number = 0
    nested_count = 0
    current_id: str | None = None
    current_text: list[str] = []

    def flush() -> None:
        if current_id is not None:
            items.append((current_id, " ".join(current_text).strip()))

    for line, fenced in zip(body, body_fenced, strict=True):
        if not line.strip():
            continue
        if fenced:
            if current_id is not None:
                current_text.append(line.strip())
            continue
        top_match = TOP_ITEM.match(line)
        nested_match = None if top_match else NESTED_ITEM.match(line)
        if top_match:
            flush()
            top_number += 1
            nested_count = 0
            current_id = f"A{task}.{top_number}"
            current_text = [line[top_match.end() :].rstrip()]
        elif nested_match:
            flush()
            if top_number == 0:
                # Вложенный пункт без родителя (сирота) — буквы внутри
                # несуществующего верхнего пункта не бывает. Задача этого
                # инструмента — не терять единицу знаменателя, поэтому пункт
                # становится верхним, а не отбрасывается и не превращается в
                # мусорный `A<задача>.0a` (Minor F11 круга 1).
                top_number += 1
                nested_count = 0
                current_id = f"A{task}.{top_number}"
            else:
                nested_count += 1
                letter = chr(ord("a") + nested_count - 1)
                current_id = f"A{task}.{top_number}{letter}"
            current_text = [line[nested_match.end() :].rstrip()]
        elif current_id is not None:
            current_text.append(line.strip())
    flush()
    return items


def emit(rows: Iterable[Sequence[str]]) -> None:
    """Печатает `rows`: поля строки через табуляцию, без шапки и без меток времени.

    Ширина строки — свойство вызывающей подкоманды, а не этой функции: у
    `assertions` строки из двух полей, у будущей `hunks` — из четырёх. `emit`
    их не считает и не проверяет, только соединяет табуляцией и печатает.
    """
    for row in rows:
        print("\t".join(row))


def _run_assertions(plan: str, task: int) -> int:
    """Реализация подкоманды `assertions`: два разных отказа, разные коды и сообщения.

    Отсутствующая задача N и отсутствующий в ней раздел «Утверждения» —
    РАЗНЫЕ отказы: первый значит «в плане нет такой задачи», второй — «задача
    есть, но выродилась и/или ещё не описана утверждениями». Оба должны
    завершаться ненулевым кодом с непустым сообщением в stderr и НЕ печатать
    пустой список в stdout с кодом 0 — тишина с нулевым кодом читалась бы как
    «у задачи нет утверждений», что для первого случая неверно, а для второго
    неотличимо от настоящей пустой задачи.

    Наличие раздела проверяется ЧЕРЕЗ `_assertions_header_index` — ТУ ЖЕ
    функцию, которой пользуется `assertion_items`, а не собственным поиском
    строки `**Утверждения**`. Так и был устроен этот отказ до круга 2: свой
    наивный (не знающий про ```-ограждения) поиск здесь СОГЛАСОВАННО, но
    неверно совпадал со старой наивной версией `assertion_items` — а когда
    круг 1 сделал `assertion_items` fence-aware, эта копия осталась наивной и
    стала давать код 0 с пустым stdout на разделе, целиком спрятанном внутри
    примера кода (найдено ревью round 2, Critical).
    """
    root = repo_root()
    plan_path = root / plan
    lines = plan_path.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")

    block = task_block(lines, task)
    if not block:
        print(f"задача {task} не найдена в плане {plan}", file=sys.stderr)
        return 2
    if _assertions_header_index(block, _fenced_mask(block)) is None:
        print(f"в задаче {task} плана {plan} нет раздела «Утверждения»", file=sys.stderr)
        return 3

    emit(assertion_items(block))
    return 0


def _git_text(args: list[str], *, check: bool = True) -> str:
    """Текстовый stdout `git <args>`, декодированный как UTF-8 с `errors="replace"`.

    `check=True` по умолчанию: для команд этого модуля (`diff HEAD`,
    `status --porcelain`) ненулевой код возврата — всегда настоящая ошибка
    git, а не штатный исход. Единственное исключение в этом файле не сюда:
    `git diff --no-index` не используется вовсе (см. `untracked_hunks`) —
    иначе пришлось бы отдельно допускать его код 1 («стороны различаются»,
    не ошибка).

    `errors="replace"`, а не строгий отказ по умолчанию: git считает файл
    ТЕКСТОМ, если в нём нет NUL-байта, даже если байты не образуют валидный
    UTF-8 (например, cp1251-содержимое), и печатает их в `git diff HEAD` как
    есть. Без `errors="replace"` поток чтения `subprocess` падал
    `AttributeError` на `None`-`stdout` (найдено ревью круга 1, K4) —
    прецедент решения уже в репозитории: `count_plan_edits.py` передаёт
    `errors="replace"` ровно по этой причине.
    """
    completed = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )
    return completed.stdout


def _git_bytes(args: list[str]) -> bytes:
    """Сырые байты stdout `git <args>` — для блоба (`show HEAD:<путь>`).

    Байты, не текст: содержимое может быть двоичным, и `text=True` уронил бы
    вызов `UnicodeDecodeError`-ом на первом непечатном байте (та же причина,
    по которой assertions-тесты сравнивают CLI-вывод побайтно, а не текстом).
    """
    completed = subprocess.run(["git", *args], capture_output=True, check=True)
    return completed.stdout


def _diff_sections(diff_text: str) -> list[list[str]]:
    """Разбивает вывод `git diff` на секции по одному файлу — списки строк.

    Границей секции служит строка `diff --git …`: она у git ЕСТЬ всегда,
    для любого изменённого файла, независимо от того, текстовый он или
    двоичный, добавлен, удалён или изменён. Пустой `diff_text` (чистое дерево
    против `HEAD`) даёт пустой список секций — это законный вход, не отказ.

    Разбито по `"\\n"`, а не `.splitlines()`: у `str.splitlines()` границей
    считается ещё десяток юникодных разделителей (`\\x0b`, `\\x0c`, `\\x1c`-
    `\\x1e`, `\\x85`, U+2028, U+2029) — символ вроде `\\x0c` внутри РЕАЛЬНОЙ
    строки содержимого рвёт её на две строки таблицы, и вторая половина
    теряет свой префикс `+`/`-` и выпадает из тела молча. Хуже того, два
    РАЗНЫХ hunk'а, чьи добавленные строки различаются только текстом ПОСЛЕ
    такого разделителя, после такого разрыва дают ОДИНАКОВОЕ тело и
    сталкивающийся `hunk_id` (найдено ревью круга 1, I1). `text=True` уже
    привёл `\\r\\n` к `\\n` на этапе чтения — `"\\n"` здесь единственный
    оставшийся настоящий разделитель строк.
    """
    sections: list[list[str]] = []
    current: list[str] | None = None
    for line in diff_text.split("\n"):
        if line.startswith("diff --git "):
            if current is not None:
                sections.append(current)
            current = [line]
        elif current is not None:
            current.append(line)
    if current is not None:
        sections.append(current)
    return sections


def _normalized_hunk_lines(lines: Iterable[str]) -> tuple[str, int, int]:
    """Тело hunk'а из строк `+`/`-` секции: первый символ сохранён, хвост срезан.

    Строки контекста (пробел в начале) и `\\ No newline at end of file`
    (обратный слэш) сюда не попадают — это ЕДИНСТВЕННОЕ место, где решается,
    что считается телом hunk'а, и `hunk_id` пользуется ровно этим телом, а не
    пересчитывает правило само. `added`/`removed` — счёт строк `+` и `-` СРЕДИ
    ЭТИХ ЖЕ строк, до `rstrip`: хвостовый пробел меняет тело (после нормализации
    — не меняет, снят), но не меняет то, `+` это строка или `-`.
    """
    body_lines: list[str] = []
    added = 0
    removed = 0
    for line in lines:
        if line.startswith("+"):
            added += 1
            body_lines.append(line.rstrip())
        elif line.startswith("-"):
            removed += 1
            body_lines.append(line.rstrip())
    return "\n".join(body_lines), added, removed


def _text_hunks_in_section(path: str, section: Sequence[str]) -> list[Hunk]:
    """Текстовые hunk'и одной секции `diff --git` — по заголовкам `@@`.

    Секция без единого `@@` (например, чистая смена режима файла без правки
    содержимого) даёт пустой список — это не ошибка и не двоичный файл, а
    третий, вырожденный случай: у файла в этом прогоне попросту нет ни одного
    hunk'а знаменателя.
    """
    starts = [i for i, line in enumerate(section) if HUNK_HEADER.match(line)]
    hunks: list[Hunk] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(section)
        body, added, removed = _normalized_hunk_lines(section[start + 1 : end])
        hunks.append(Hunk(path=path, context=section[start], body=body, added=added, removed=removed))
    return hunks


def _binary_hunk(root: Path, path: str, old_side: str, new_side: str) -> Hunk:
    """Синтетический hunk двоичной секции — по симметричной таблице задачи.

    `git diff` не умеет строить `+`/`-` строки по двоичному содержимому,
    поэтому тело здесь не от git, а от sha256 каждой стороны, которая
    РЕАЛЬНО существует: старая — из `git show HEAD:<путь>` (блоб коммита),
    новая — из рабочего дерева. Форма СИММЕТРИЧНА текстовому hunk'у: правка
    `A → B` и правка `C → B` дают РАЗНЫЕ тела (старый хэш входит в тело), а
    односторонняя форма `+binary <sha256 текущего>` их бы отождествила —
    и оставила удалённый файл вовсе без тела, потому что текущего содержимого
    у него нет. Контекст фиксирован — `@@ binary @@` — одинаков для всех трёх
    видов правки; вид определяют сами вызывающие по `/dev/null` в `old_side`
    (правки нет — файл добавлен) и `new_side` (файл удалён).
    """
    context = "@@ binary @@"
    if old_side == "/dev/null":
        new_bytes = (root / path).read_bytes()
        return Hunk(
            path=path,
            context=context,
            body=f"+binary {hashlib.sha256(new_bytes).hexdigest()}",
            added=1,
            removed=0,
        )
    if new_side == "/dev/null":
        old_bytes = _git_bytes(["show", f"HEAD:{path}"])
        return Hunk(
            path=path,
            context=context,
            body=f"-binary {hashlib.sha256(old_bytes).hexdigest()}",
            added=0,
            removed=1,
        )
    old_bytes = _git_bytes(["show", f"HEAD:{path}"])
    new_bytes = (root / path).read_bytes()
    body = f"-binary {hashlib.sha256(old_bytes).hexdigest()}\n+binary {hashlib.sha256(new_bytes).hexdigest()}"
    return Hunk(path=path, context=context, body=body, added=1, removed=1)


def _section_path(section: Sequence[str]) -> str | None:
    """Путь секции из строк `+++ b/…`/`--- a/…` — однозначно, в отличие от `diff --git`.

    Строка `+++ b/<путь>` (или `--- a/<путь>`, если сторона `+++` —
    `/dev/null`, то есть файл удалён) режется по ФИКСИРОВАННОМУ 6-символьному
    префиксу (`"+++ b/"`/`"--- a/"`), а не по поиску подстроки-разделителя —
    поэтому путь, содержащий `" b/"` внутри себя (`x b/y.txt`), режется верно
    (см. `DIFF_GIT_HEADER`, I4 круга 1). Такие строки есть у ЛЮБОЙ секции с
    содержательным `@@`-hunk'ом; у двоичных и metadata-секций их нет вовсе —
    там `None`, и вызывающий код обязан сам упасть на `diff --git`.

    Хвостовой `rstrip("\\t")` обязателен: у пути с пробелом git сам дописывает
    ОДИН хвостовой таб после имени в строках `---`/`+++` (гасит собственную
    неоднозначность формата unified diff, где после таба исторически могла
    идти метка времени) — без снятия этот таб становился частью пути, и
    `x b/y.txt` превращался бы в `"x b/y.txt\\t"`. Замечено на этом же входе,
    что чинит I4, при подготовке доказательства, а не найдено ревью отдельно.
    """
    for line in section:
        if line.startswith("+++ ") and line[4:] != "/dev/null":
            return line[6:].rstrip("\t")
    for line in section:
        if line.startswith("--- ") and line[4:] != "/dev/null":
            return line[6:].rstrip("\t")
    return None


def _metadata_only_hunk(path: str, section: Sequence[str]) -> Hunk:
    """Синтетический hunk секции БЕЗ единого `@@` и без `Binary files … differ`.

    Три известных вида такой секции — чистое переименование (`similarity
    index`/`rename from`/`rename to`), смена режима файла (`old mode`/
    `new mode`) и добавление ПУСТОГО файла (`new file mode`/`index
    0000000..e69de29`, вечная константа git для пустого блоба). Раньше такая
    секция не давала ни одного hunk'а — она молча выпадала из знаменателя
    (найдено ревью круга 1, K5/K6/K7): утверждение A2.11 про двоичные файлы —
    «молчаливый пропуск запрещён» — это ПРИНЦИП, а не правило только для
    двоичных, и план уже показал технику: там, где `git diff` не выражает
    правку строками `+`/`-`, строится детерминированный синтетический hunk, а
    не пропуск.

    Тело — сами описательные строки git ЭТОЙ секции (без первой, `diff --git
    …`: путь уже извлечён из неё вызывающим кодом), непустые, со снятым
    хвостовым пробелом, склеенные `\n` — не выдумка, а то, что git и так
    напечатал; разные виды правки различаются между собой этим же телом (у
    переименования — `rename from`/`rename to`, у смены режима — `old
    mode`/`new mode`, у пустого добавления — `new file mode`).

    Строка `index <старый>..<новый> [режим]` ИСКЛЮЧЕНА из тела нарочно
    (найдено финальным сквозным ревью, S4). У пустого добавленного файла эта
    строка — единственный источник хэшей блоба (`index 0000000..e69de29`), и
    ширина каждого хэша в ней следует НЕ содержанию правки, а git-настройке
    `core.abbrev`: `-c core.abbrev=4` печатает `index 0000..e69d`,
    `core.abbrev=40` — полные 40 hex того же блоба. Без исключения один и тот
    же сценарий на двух машинах с разным `core.abbrev` (сегодня этот вызов
    git его не форсирует ничем) давал бы РАЗНЫЙ `hunk_id` — знаменатель
    переставал бы быть воспроизводимым, хотя дерево одно и то же. Строка
    `index …` — блобовая бухгалтерия git, а не содержание правки: она не
    входит в тело и у ТЕКСТОВОГО hunk'а (`_normalized_hunk_lines` берёт
    только строки `@@`-hunk'а, а строка `index` стоит ДО первого `@@` и туда
    не попадает) — здесь то же решение применено к секции, где `@@` нет
    вовсе. Переименование и смена режима строку `index ` не несут (проверено
    тестом на каждый вид отдельно), так что исключение меняет только случай
    добавления пустого файла. Альтернатива — форсировать `-c
    core.abbrev=40` на вызове `git diff` — отвергнута: она привязывала бы
    стабильность ко ВСЕМ вызовам git этого модуля разом ради одного частного
    случая, тогда как исключение строки решает вопрос ровно там, где он
    возникает. Контекст фиксирован — `@@ metadata @@` — так же, как у
    двоичных hunk'ов вид различает не контекст, а тело. `added`/`removed` —
    оба 0: секция не несёт ни одной строки содержимого.
    """
    descriptive = [
        line.rstrip() for line in section[1:] if line.strip() and not line.startswith("index ")
    ]
    body = "\n".join(descriptive)
    return Hunk(path=path, context="@@ metadata @@", body=body, added=0, removed=0)


def diff_hunks() -> list[Hunk]:
    """Hunk'и рабочего дерева против `HEAD` — staged и unstaged вместе.

    `git diff HEAD` (без `--cached`, без голого `git diff`) — единственная
    команда, которая одновременно видит и то, что уже в индексе, и то, что
    ещё нет: файл, часть которого застейджена, а часть нет, даёт hunk'и обоих
    видов сам по себе, без отдельной ветки кода на этот случай. Индекс не
    трогается — читаются только `diff HEAD` и, для двоичных файлов, блоб
    `HEAD` и рабочее дерево; `git add -N` нигде не вызывается.

    Флаги `--src-prefix=a/ --dst-prefix=b/` форсируют разбор `diff --git a/…
    b/…` независимо от `diff.noprefix`/`diff.mnemonicPrefix` в чужом
    `gitconfig`; `--no-color --no-ext-diff --no-textconv` убирают источники
    недетерминизма (ANSI-коды, внешний diff-драйвер, textconv-фильтр),
    которые исказили бы разбор, не будучи ошибкой git.
    """
    root = repo_root()
    raw = _git_text(
        [
            "-c",
            "core.quotepath=false",
            "diff",
            "HEAD",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "--src-prefix=a/",
            "--dst-prefix=b/",
        ]
    )
    hunks: list[Hunk] = []
    for section in _diff_sections(raw):
        header = DIFF_GIT_HEADER.match(section[0])
        if header is None:
            continue
        path = _section_path(section) or header.group(2)
        binary_line = next((line for line in section if BINARY_DIFFER.match(line)), None)
        if binary_line is not None:
            old_side, new_side = BINARY_DIFFER.match(binary_line).groups()
            hunks.append(_binary_hunk(root, path, old_side, new_side))
            continue
        text_hunks = _text_hunks_in_section(path, section)
        if text_hunks:
            hunks.extend(text_hunks)
        else:
            # Ни одного `@@`, и не двоичная секция: переименование, смена
            # режима, добавление пустого файла и т.п. — знаменатель не
            # теряет элемент (K5/K6/K7 круга 1), а получает синтетический
            # hunk по той же технике, что и двоичные.
            hunks.append(_metadata_only_hunk(path, section))
    return hunks


_SIMPLE_ESCAPES = {"\\\\": "\\", '\\"': '"', "\\t": "\t", "\\n": "\n", "\\r": "\r"}


def _unquote_git_status_path(token: str) -> str:
    """Снимает C-style кавычки `git status --porcelain` — сам путь ими не занят.

    git заворачивает путь в кавычки при пробеле и других «необычных» символах
    НЕЗАВИСИМО от `core.quotepath` — этот флаг влияет только на не-ASCII
    байты ВНУТРИ кавычек, не на само решение кавычить (найдено ревью круга 1,
    K2). Без снятия кавычек путь `read_bytes()` получал бы буквальные символы
    кавычки в имени и падал `OSError`.

    Внутри кавычек здесь встречаются только простые escape-последовательности
    (экранированная обратная косая черта, экранированная кавычка, управляющие
    символы) — октальных нет и не будет: этот модуль форсирует
    `-c core.quotepath=false` на КАЖДОМ вызове `git status`
    (`untracked_hunks`), и под этой настройкой git печатает не-ASCII байты
    сырыми, без кавычек и без октальных escape-последовательностей вовсе.
    Ветвь их разбора существовала здесь раньше и была мертва с рождения —
    сверщик показал её недостижимость (найдено финальным сквозным ревью, S7),
    и по правилу «мёртвое решение удаляется вместе с объяснявшим его
    комментарием» (`docs/process/implementation.md`, «Мёртвое либо
    эквивалентное решение») она снята, а не оставлена документацией
    несуществующего пути.
    """
    if len(token) < 2 or token[0] != '"' or token[-1] != '"':
        return token
    inner = token[1:-1]
    result: list[str] = []
    i = 0
    while i < len(inner):
        if inner[i] == "\\":
            two = inner[i : i + 2]
            if two in _SIMPLE_ESCAPES:
                result.append(_SIMPLE_ESCAPES[two])
                i += 2
                continue
        result.append(inner[i])
        i += 1
    return "".join(result)


def untracked_hunks() -> list[Hunk]:
    """По одному hunk'у «файл добавлен целиком» на неотслеживаемый неигнорируемый файл.

    Источник списка файлов — `git status --porcelain --untracked-files=all`,
    префикс `??`; игнорируемые файлы под него не попадают вовсе (без
    `--ignored` git их не показывает), поэтому отдельного обращения к
    `.gitignore` в этой функции нет и не нужно. `--untracked-files=all`
    обязателен: без него git сворачивает НОВЫЙ КАТАЛОГ в одну запись
    `?? newdir/`, и файлы внутри нигде не появляются в знаменателе — молчаливая
    потеря целой поддиректории (найдено ревью круга 1, K1). Путь из статус-строки
    снимается через `_unquote_git_status_path` (K2: git кавычит путь с
    пробелом независимо от `quotepath`).

    Двоичность определяется САМА, без обращения к git: наличие нулевого
    байта в первых 8000 байт файла — тот же эвристический признак, которым
    пользуется сам git при построении из `Binary files … differ`. Двоичный
    untracked-файл идёт по форме «добавление» таблицы двоичных hunk'ов
    (`+binary <sha256>`, `+1/-0`, `@@ binary @@`) — решение оркестратора:
    таблица разбита по ВИДУ правки, а untracked-файл — это всегда добавление;
    текстовое тело над двоичными байтами не определено. Та же форма — и
    фолбэк для текстового untracked-файла, который НЕ проходит эвристику
    нулевого байта, но не декодируется как UTF-8 (например, cp1251 без NUL,
    найдено ревью круга 1, K3): тело как текст здесь не определено ничуть не
    меньше, чем у настоящего двоичного файла, а падать `UnicodeDecodeError`
    и терять элемент знаменателя нельзя.

    Текстовый (валидный UTF-8) файл даёт ОДИН hunk, где каждая строка файла
    становится строкой `+` (хвостовые пробелы сняты той же нормализацией,
    что и у обычного hunk'а), `removed` — всегда 0, а число `added` равно
    числу строк файла. Разбито по `"\\n"`, не `.splitlines()` — та же причина,
    что и у `_diff_sections` (I1 круга 1): лишний юникодный разделитель внутри
    строки файла рвёт её и меняет тело/id непредсказуемо.
    """
    root = repo_root()
    status = _git_text(["-c", "core.quotepath=false", "status", "--porcelain", "--untracked-files=all"])
    hunks: list[Hunk] = []
    for line in status.split("\n"):
        match = UNTRACKED_STATUS.match(line)
        if match is None:
            continue
        path = _unquote_git_status_path(match.group(1))
        raw_bytes = (root / path).read_bytes()
        if b"\x00" in raw_bytes[:8000]:
            body = f"+binary {hashlib.sha256(raw_bytes).hexdigest()}"
            hunks.append(Hunk(path=path, context="@@ binary @@", body=body, added=1, removed=0))
            continue
        try:
            decoded = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            body = f"+binary {hashlib.sha256(raw_bytes).hexdigest()}"
            hunks.append(Hunk(path=path, context="@@ binary @@", body=body, added=1, removed=0))
            continue
        lines = decoded.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        body = "\n".join(f"+{content.rstrip()}" for content in lines)
        context = f"@@ -0,0 +1,{len(lines)} @@"
        hunks.append(Hunk(path=path, context=context, body=body, added=len(lines), removed=0))
    return hunks


def hunk_id(path: str, body: str) -> str:
    """`H:<8 hex>` — первые 8 hex от `sha256(path + "\\n" + body)`, UTF-8.

    `body` приходит УЖЕ нормализованным (см. `Hunk.body`): это значит, что
    сдвиг номеров строк в заголовке `@@` (в саму хэшируемую строку не входит)
    и хвостовой пробел на изменённой строке (снят до вызова этой функции) не
    меняют идентификатор — а тот же текст в другом файле меняет, потому что
    `path` — часть хэшируемой строки. Известный вход с известным ответом
    (оракул задачи, посчитан независимо от этого модуля): путь
    `backend/money/price.py` и тело из строк `+    if not value.is_finite():`,
    `+        return False`, `-    return value > 0` (склеены `\\n`) дают ровно
    `H:c81dd3dd`; тот же текст при пути `backend/money/other.py` — ровно
    `H:2292efa1`.
    """
    digest = hashlib.sha256(f"{path}\n{body}".encode()).hexdigest()
    return f"H:{digest[:8]}"


def _run_hunks() -> int:
    """Реализация подкоманды `hunks`: слияние, сортировка, позиционные суффиксы дублей.

    Порядок вывода — по пути файла, внутри файла по положению в `diff`:
    `diff_hunks()` и `untracked_hunks()` каждая уже возвращает hunk'и своего
    источника в порядке появления, но лишь явная сортировка ПО ПУТИ гарантирует
    порядок между двумя источниками — полагаться на то, что git и так обходит
    файлы отсортированно, было бы допущением, не проверенным этим кодом.
    Сортировка Python стабильна, поэтому порядок ВНУТРИ одного пути (а значит,
    и внутри одного файла — источник для данного пути всегда только один: файл
    не может быть одновременно отслеживаемым изменением и untracked) не
    нарушается.

    Суффиксы `.2`, `.3` у совпавших хэшей считаются заново на КАЖДОМ прогоне,
    по текущему порядку: раздельного состояния между прогонами нет и не
    нужно — это и есть то самое поведение, из-за которого более ранний по
    порядку дубликат сдвигает суффиксы соседей (спека §3.10): для этого не
    нужна отдельная ветка кода, только пересчёт "с нуля" при каждом вызове.

    Репозиторий без единого коммита — `git diff HEAD` отказывает кодом 128
    (`HEAD` не существует): падать здесь можно (молчаливая пустота была бы
    неотличима от честного «нет правок»), но ПОНЯТНЫМ сообщением, а не
    трассировкой `CalledProcessError` (найдено ревью круга 1, I2) — `git`
    уже написал причину в свой `stderr`, этот код только не даёт ей потеряться
    за трассировкой интерпретатора.
    """
    try:
        hunks = sorted(diff_hunks() + untracked_hunks(), key=lambda hunk: hunk.path)
    except subprocess.CalledProcessError as error:
        message = (error.stderr or str(error)).strip()
        print(f"git отказал: {message}", file=sys.stderr)
        return 2
    seen: dict[str, int] = {}
    rows: list[tuple[str, str, str, str]] = []
    for hunk in hunks:
        raw_id = hunk_id(hunk.path, hunk.body)
        count = seen.get(raw_id, 0) + 1
        seen[raw_id] = count
        display_id = raw_id if count == 1 else f"{raw_id}.{count}"
        rows.append((display_id, hunk.path, hunk.context, f"+{hunk.added}/-{hunk.removed}"))
    emit(rows)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="enumerate_review_units")
    # Подпарсеры заведены сразу, хотя подкоманда сегодня одна: следующая задача
    # добавляет `hunks` в этот же файл, и плоский парсер пришлось бы переписывать.
    subparsers = parser.add_subparsers(dest="command", required=True)

    assertions_parser = subparsers.add_parser("assertions", help="утверждения задачи плана")
    assertions_parser.add_argument("--plan", required=True, help="путь к плану от корня репозитория")
    assertions_parser.add_argument("--task", required=True, type=int, help="номер задачи в плане")

    subparsers.add_parser("hunks", help="git-hunk'и рабочего дерева против HEAD (staged и unstaged)")

    args = parser.parse_args()

    if args.command == "assertions":
        return _run_assertions(args.plan, args.task)
    if args.command == "hunks":
        return _run_hunks()
    return 1


if __name__ == "__main__":
    sys.exit(main())
