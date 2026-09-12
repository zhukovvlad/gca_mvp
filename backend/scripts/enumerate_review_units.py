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
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

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
# сторона) — безопасный источник пути даже для удалённого файла: git всё
# равно печатает оба имени в этой строке. Переименования (`R100 …`) вне
# охвата задачи — с ними group(1) != group(2), и код молча берёт группу 2.
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
    """Текстовый stdout `git <args>`, декодированный как UTF-8.

    `check=True` по умолчанию: для команд этого модуля (`diff HEAD`,
    `status --porcelain`) ненулевой код возврата — всегда настоящая ошибка
    git, а не штатный исход. Единственное исключение в этом файле не сюда:
    `git diff --no-index` не используется вовсе (см. `untracked_hunks`) —
    иначе пришлось бы отдельно допускать его код 1 («стороны различаются»,
    не ошибка).
    """
    completed = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
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
    """
    sections: list[list[str]] = []
    current: list[str] | None = None
    for line in diff_text.splitlines():
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
        path = header.group(2)
        binary_line = next((line for line in section if BINARY_DIFFER.match(line)), None)
        if binary_line is not None:
            old_side, new_side = BINARY_DIFFER.match(binary_line).groups()
            hunks.append(_binary_hunk(root, path, old_side, new_side))
        else:
            hunks.extend(_text_hunks_in_section(path, section))
    return hunks


def untracked_hunks() -> list[Hunk]:
    """По одному hunk'у «файл добавлен целиком» на неотслеживаемый неигнорируемый файл.

    Источник списка файлов — `git status --porcelain`, префикс `??`;
    игнорируемые файлы под него не попадают вовсе (без `--ignored` git их не
    показывает), поэтому отдельного обращения к `.gitignore` в этой функции
    нет и не нужно.

    Двоичность определяется САМА, без обращения к git: наличие нулевого
    байта в первых 8000 байт файла — тот же эвристический признак, которым
    пользуется сам git при построении из `Binary files … differ`. Двоичный
    untracked-файл идёт по форме «добавление» таблицы двоичных hunk'ов
    (`+binary <sha256>`, `+1/-0`, `@@ binary @@`) — решение оркестратора:
    таблица разбита по ВИДУ правки, а untracked-файл — это всегда добавление;
    текстовое тело над двоичными байтами не определено. Текстовый файл даёт
    ОДИН hunk, где каждая строка файла становится строкой `+` (хвостовые
    пробелы сняты той же нормализацией, что и у обычного hunk'а), `removed`
    — всегда 0, а число `added` равно числу строк файла.
    """
    root = repo_root()
    status = _git_text(["-c", "core.quotepath=false", "status", "--porcelain"])
    hunks: list[Hunk] = []
    for line in status.splitlines():
        match = UNTRACKED_STATUS.match(line)
        if match is None:
            continue
        path = match.group(1)
        raw_bytes = (root / path).read_bytes()
        if b"\x00" in raw_bytes[:8000]:
            body = f"+binary {hashlib.sha256(raw_bytes).hexdigest()}"
            hunks.append(Hunk(path=path, context="@@ binary @@", body=body, added=1, removed=0))
            continue
        lines = raw_bytes.decode("utf-8").splitlines()
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
    """
    hunks = sorted(diff_hunks() + untracked_hunks(), key=lambda hunk: hunk.path)
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
