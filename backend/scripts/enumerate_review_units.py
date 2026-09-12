"""Перечислитель единиц ревью плана: подкоманда `assertions` (задача 1).

Скрипт read-only: он только читает файл плана и печатает строки, ничего не
пишет на диск и не трогает git — ни дерево, ни индекс. Предмет задачи —
утверждения задачи плана: раздел «Утверждения» под заголовком `### Task N:`
превращается в список пар «идентификатор пункта, текст пункта», по одной
строке на пункт, поля разделены табуляцией.

Идентификатор пункта — `A<задача>.<номер>` для пункта верхнего уровня
(нумерация с единицы) и `A<задача>.<номер><буква>` для вложенного пункта
(буква — `a`, `b`, … — считается заново внутри КАЖДОГО верхнего пункта, а не
сквозно по задаче). Пункт, продолженный на следующей строке без своего `- `,
даёт ОДНУ строку вывода: текст продолжения дописывается пробелом, а не заводит
новый пункт.

Следующая задача этой же фичи добавляет в этот файл вторую подкоманду
(`hunks`, перечислитель git-hunk'ов) — поэтому `argparse` заведён сразу с
подпарсерами, хотя подкоманда сегодня одна, и `emit` печатает строки ЛЮБОЙ
ширины: два поля у `assertions`, четыре будут у `hunks`.

Запуск (путь `--plan` — от корня репозитория, независимо от того, откуда
запущена сама команда):

    cd backend && uv run python scripts/enumerate_review_units.py \
        assertions --plan docs/superpowers/plans/ДАТА-тема.md --task 4
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
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


def main() -> int:
    parser = argparse.ArgumentParser(prog="enumerate_review_units")
    # Подпарсеры заведены сразу, хотя подкоманда сегодня одна: следующая задача
    # добавляет `hunks` в этот же файл, и плоский парсер пришлось бы переписывать.
    subparsers = parser.add_subparsers(dest="command", required=True)

    assertions_parser = subparsers.add_parser("assertions", help="утверждения задачи плана")
    assertions_parser.add_argument("--plan", required=True, help="путь к плану от корня репозитория")
    assertions_parser.add_argument("--task", required=True, type=int, help="номер задачи в плане")

    args = parser.parse_args()

    if args.command == "assertions":
        return _run_assertions(args.plan, args.task)
    return 1


if __name__ == "__main__":
    sys.exit(main())
