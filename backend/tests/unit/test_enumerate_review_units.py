"""Тесты подкоманд `assertions` (задача 1) и `hunks` (задача 2) перечислителя.

Скрипт read-only, и это касается и тестов: ни один из них не имеет права
изменить рабочее дерево или индекс ЭТОГО репозитория. Тестам, которым нужно
состояние git (независимость `repo_root()` от текущего каталога, неизменность
`git status --porcelain`, любой тест `diff_hunks`/`untracked_hunks`/CLI
`hunks`), заводится СВОЙ временный репозиторий во временном каталоге pytest
(`tmp_path` + `git init`) — рабочий репозиторий они не трогают вовсе.

Тесты чистых функций (`task_block`, `assertion_items`, `emit`, `hunk_id`)
обходятся без git и без файлов: вход передаётся строками/списками строк, как
в реальном контракте.

Тесты `hunks`, которым нужно СОДЕРЖИМОЕ временного репозитория (а не только
его наличие), зовут `diff_hunks()`/`untracked_hunks()` НАПРЯМУЮ после
`monkeypatch.chdir(repo)` — так же, как `repo_root()` полагается на cwd
процесса, а не на `-C`. Это быстрее subprocess-прогона всего CLI и не менее
честно: обе функции сами по себе — это подпроцессы `git`, а не мок.
Read-only-гарантия (индекс не меняется) и формат вывода команды `hunks`
проверяются subprocess-ом, как и у `assertions` — это гарантии CLI-обёртки,
а не отдельных функций.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.enumerate_review_units as enumerate_review_units

SCRIPT_PATH = Path(enumerate_review_units.__file__).resolve()

# Мини-план с тремя задачами — тот же скелет секций (Files/Interfaces/
# Утверждения/Имена/Проверка), что в реальных планах `docs/superpowers/plans/`,
# но без зависимости от их текста: реальный план правится другими задачами
# этой же фичи, и тест не должен краснеть от чужой правки прозы.
PLAN_TEXT = """# План: тестовая фича

## Задачи

### Task 1: Первая задача

**Files**
- Create: `a.py`

**Interfaces**
- Производит: `f() -> None`

**Утверждения**
- утверждение первой задачи, пункт один;
- утверждение первой задачи, пункт два,
  дописанный второй строкой без тире;
- **третий** пункт первой задачи с вложенными случаями:
  - вложенный пункт «a» третьего пункта;
  - вложенный пункт «b» третьего пункта;
- пункт четыре, идущий после вложенных — своя буква с единицы не наследуется.

**Имена**
- Заводятся этой задачей: `f`.
- Существуют, проверено `grep`-ом: ничего.

**Проверка**
- пункт раздела «Проверка» первой задачи — не утверждение.

---

### Task 2: Вторая задача

**Files**
- Edit: `a.py`

**Interfaces**
- Потребляет: `f` (задача 1).

**Утверждения**
- утверждение второй задачи — не должно попасть в вывод по задаче 1.

**Проверка**
- зелёная.

---

### Task 3: Третья задача, без раздела «Утверждения»

**Files**
- Create: `b.py`

**Проверка**
- зелёная.
"""


def plan_lines() -> list[str]:
    return PLAN_TEXT.split("\n")


# --- task_block ------------------------------------------------------------


def test_task_block_bounded_by_next_task_header() -> None:
    block = enumerate_review_units.task_block(plan_lines(), 1)
    text = "\n".join(block)
    assert "### Task 1:" in text
    assert "### Task 2:" not in text
    assert "утверждение второй задачи" not in text


def test_task_block_bounded_by_separator_for_last_task() -> None:
    # Задача 3 — последняя, и после неё в файле нет ни `### `, ни `---`: блок
    # обязан дойти до конца списка строк, а не потеряться.
    block = enumerate_review_units.task_block(plan_lines(), 3)
    text = "\n".join(block)
    assert "### Task 3:" in text
    assert "b.py" in text


def test_task_block_missing_task_returns_empty() -> None:
    assert enumerate_review_units.task_block(plan_lines(), 99) == []


def test_task_block_does_not_confuse_task_1_with_task_10() -> None:
    lines = ["### Task 10: чужая задача", "**Утверждения**", "- чужой пункт;"]
    assert enumerate_review_units.task_block(lines, 1) == []


def test_task_block_stops_at_next_header_without_a_preceding_separator() -> None:
    """I1, половина первая: граница по `### `, когда перед ней нет `---`.

    В `PLAN_TEXT` каждый `### Task N+1:` стоит через `---`, поэтому обе
    половины предиката совпадают и не различимы по нему одному (найдено
    ревью round 1, F3). Здесь `---` нет вовсе — если бы вклад `NEXT_HEADER`
    убрали, блок проглотил бы и заголовок, и пункт задачи 2.
    """
    lines = [
        "### Task 1: Задача без --- перед соседом",
        "**Утверждения**",
        "- пункт первой задачи;",
        "### Task 2: Соседняя задача без ---",
        "**Утверждения**",
        "- пункт второй задачи, который не должен попасть в блок первой;",
    ]
    block = enumerate_review_units.task_block(lines, 1)
    text = "\n".join(block)
    assert "пункт первой задачи" in text
    assert "### Task 2" not in text
    assert "пункт второй задачи" not in text


def test_task_block_stops_at_separator_without_a_following_header() -> None:
    """I1, половина вторая: граница по `---`, когда после неё нет `### `.

    Здесь после `---` идёт `## ` (раздел плана целиком, не задача) — если бы
    вклад проверки `SEPARATOR` убрали, блок проглотил бы `---` и весь
    следующий раздел, потому что `### ` до конца списка строк не встретится.
    """
    lines = [
        "### Task 1: Задача с разделителем без заголовка после",
        "**Утверждения**",
        "- пункт первой задачи;",
        "---",
        "## Команды проверки",
        "- пункт финального раздела плана, не задачи;",
    ]
    block = enumerate_review_units.task_block(lines, 1)
    text = "\n".join(block)
    assert "пункт первой задачи" in text
    assert "---" not in text
    assert "Команды проверки" not in text


def test_task_block_ignores_structural_markers_inside_a_fenced_block() -> None:
    """I5 (task_block): `### Task N:` и `---` внутри ```-примера — не границы.

    Раздел «Interfaces» реальных задач часто содержит блок кода, а в нём
    вполне может встретиться текст, ВЫГЛЯДЯЩИЙ как заголовок задачи или
    разделитель (иллюстрация markdown). Без учёта ограждения блок задачи 1
    оборвался бы на этих строках, не дойдя до «Утверждения».
    """
    lines = [
        "### Task 1: Задача с примером кода",
        "**Interfaces**",
        "```python",
        "### Task 9: это не задача, а пример кода",
        "---",
        "```",
        "**Утверждения**",
        "- пункт один;",
    ]
    block = enumerate_review_units.task_block(lines, 1)
    text = "\n".join(block)
    assert "пункт один" in text
    assert "**Утверждения**" in text


# --- assertion_items ---------------------------------------------------------

# Оракул для задачи 1 мини-плана `PLAN_TEXT` — полный ожидаемый список пар,
# известным входом с известным ответом. Используется и как прямой оракул
# (ниже), и как позитивный якорь в четырёх «отрицательных» тестах: до фикса
# round 1 (I2) те тесты проходили и при пустом списке, то есть ничего не
# доказывали. Сравнение с ПОЛНЫМ ожидаемым списком доказывает одновременно и
# что чужого текста нет, и что своего текста не меньше, чем должно быть.
TASK1_ITEMS = [
    ("A1.1", "утверждение первой задачи, пункт один;"),
    ("A1.2", "утверждение первой задачи, пункт два, дописанный второй строкой без тире;"),
    ("A1.3", "**третий** пункт первой задачи с вложенными случаями:"),
    ("A1.3a", "вложенный пункт «a» третьего пункта;"),
    ("A1.3b", "вложенный пункт «b» третьего пункта;"),
    ("A1.4", "пункт четыре, идущий после вложенных — своя буква с единицы не наследуется."),
]


def test_assertion_items_top_level_ids_numbered_from_one() -> None:
    block = enumerate_review_units.task_block(plan_lines(), 1)
    items = enumerate_review_units.assertion_items(block)
    top_level_ids = [item_id for item_id, _ in items if item_id in {"A1.1", "A1.2", "A1.3", "A1.4"}]
    assert top_level_ids == ["A1.1", "A1.2", "A1.3", "A1.4"]


def test_assertion_items_nested_gets_letter_within_its_own_top_item() -> None:
    block = enumerate_review_units.task_block(plan_lines(), 1)
    items = dict(enumerate_review_units.assertion_items(block))
    assert "A1.3a" in items
    assert "A1.3b" in items
    assert items["A1.3a"] == "вложенный пункт «a» третьего пункта;"
    assert items["A1.3b"] == "вложенный пункт «b» третьего пункта;"


def test_assertion_items_nested_letter_resets_per_top_item() -> None:
    """C1: буква вложенного пункта — внутри СВОЕГО верхнего, а не сквозно.

    `PLAN_TEXT` несёт только ОДИН верхний пункт с детьми (A1.3), и «сквозная»
    против «попунктной» нумерации буквы неразличимы на входе с одним таким
    пунктом (найдено ревью round 1, F1 — снятие `nested_count = 0` проходило
    22 из 22). Здесь ДВА верхних пункта, у КАЖДОГО свои вложенные: верная
    реализация даёт `A1.1a, A1.1b, A1.2a, A1.2b`, сквозная дала бы
    `A1.1a, A1.1b, A1.2c, A1.2d`.
    """
    lines = [
        "### Task 1: X",
        "**Утверждения**",
        "- первый верхний пункт с детьми:",
        "  - первый вложенный первого пункта;",
        "  - второй вложенный первого пункта;",
        "- второй верхний пункт с детьми:",
        "  - первый вложенный второго пункта;",
        "  - второй вложенный второго пункта;",
    ]
    block = enumerate_review_units.task_block(lines, 1)
    items = enumerate_review_units.assertion_items(block)
    assert [item_id for item_id, _ in items] == ["A1.1", "A1.1a", "A1.1b", "A1.2", "A1.2a", "A1.2b"]


def test_assertion_items_multiline_item_is_one_output_line() -> None:
    block = enumerate_review_units.task_block(plan_lines(), 1)
    items = dict(enumerate_review_units.assertion_items(block))
    assert items["A1.2"] == (
        "утверждение первой задачи, пункт два, дописанный второй строкой без тире;"
    )


def test_assertion_items_known_input_known_output_exact() -> None:
    """Оракул: полный список пар для задачи 1 этого мини-плана — буквально.

    Не «свойство», а конкретный известный ответ: защищает от случайного сдвига
    нумерации или потери пункта, который частичные проверки выше не ловят.
    """
    block = enumerate_review_units.task_block(plan_lines(), 1)
    items = enumerate_review_units.assertion_items(block)
    assert items == TASK1_ITEMS


def test_assertion_items_excludes_files_section() -> None:
    """I2: отрицательный вход рядом с позитивным ожиданием.

    До фикса round 1 (F4) четыре теста этой группы были вида `not any(...)` —
    и проходили даже при пустом `items`, то есть при полностью сломанном
    `assertion_items`. Сравнение с ПОЛНЫМ ожидаемым `TASK1_ITEMS` доказывает
    одновременно и что текста «Files» нет, и что то, что должно быть, — есть.
    """
    block = enumerate_review_units.task_block(plan_lines(), 1)
    items = enumerate_review_units.assertion_items(block)
    assert not any("Create: `a.py`" in text for _, text in items)
    assert items == TASK1_ITEMS


def test_assertion_items_excludes_interfaces_section() -> None:
    block = enumerate_review_units.task_block(plan_lines(), 1)
    items = enumerate_review_units.assertion_items(block)
    assert not any("Производит" in text or "f() -> None" in text for _, text in items)
    assert items == TASK1_ITEMS


def test_assertion_items_excludes_names_section() -> None:
    block = enumerate_review_units.task_block(plan_lines(), 1)
    items = enumerate_review_units.assertion_items(block)
    assert not any("Заводятся этой задачей" in text for _, text in items)
    assert items == TASK1_ITEMS


def test_assertion_items_excludes_verification_section() -> None:
    block = enumerate_review_units.task_block(plan_lines(), 1)
    items = enumerate_review_units.assertion_items(block)
    assert not any("не утверждение" in text for _, text in items)
    assert items == TASK1_ITEMS


def test_assertion_items_missing_section_returns_empty() -> None:
    block = enumerate_review_units.task_block(plan_lines(), 3)
    assert enumerate_review_units.assertion_items(block) == []


def test_assertion_items_nested_before_any_top_item_is_promoted_not_dropped() -> None:
    """Minor (F11): вложенный пункт-сирота получает верхний id, а не `A1.0a`.

    Без первого верхнего пункта буква внутри него не считается ни при каком
    осмысленном прочтении контракта; правильный ответ — не потерять единицу
    знаменателя молча и не выдать мусорный id, а сделать сироту верхним
    пунктом.
    """
    lines = [
        "### Task 1: X",
        "**Утверждения**",
        "  - вложенный пункт без родителя;",
        "- обычный верхний пункт;",
    ]
    block = enumerate_review_units.task_block(lines, 1)
    items = enumerate_review_units.assertion_items(block)
    ids = [item_id for item_id, _ in items]
    assert "A1.0a" not in ids
    assert ids == ["A1.1", "A1.2"]
    assert items[0][1] == "вложенный пункт без родителя;"


def test_assertion_items_bold_only_continuation_line_is_not_a_section_terminator() -> None:
    """I4: строка-продолжение, целиком из жирного, не обрывает раздел.

    `enumerate_review_units.py` раньше сравнивал `line.strip()` с
    `BOLD_HEADER`, поэтому отступленная строка `  **жирный термин**`
    (продолжение пункта) читалась как заголовок следующего раздела и обрывала
    список — пункты два и три пропадали молча (найдено ревью round 1, F7;
    такой стиль уже в корпусе, `docs/superpowers/plans/2026-08-08-vat-rate.md:128`).
    """
    lines = [
        "### Task 1: X",
        "**Утверждения**",
        "- пункт один, чья вторая строка это",
        "  **жирный термин**",
        "- пункт два;",
        "- пункт три;",
    ]
    block = enumerate_review_units.task_block(lines, 1)
    items = enumerate_review_units.assertion_items(block)
    assert [item_id for item_id, _ in items] == ["A1.1", "A1.2", "A1.3"]
    assert items[0][1] == "пункт один, чья вторая строка это **жирный термин**"


def test_assertion_items_fence_hides_a_separator_from_the_section_scan() -> None:
    """I5: голый `---` внутри ```-ограждения не обрывает раздел «Утверждения».

    До фикса round 1 (F8) такой вход давал ОДИН пункт вместо трёх: маркер
    ограждения читался как настоящий разделитель задачи/раздела.
    """
    lines = [
        "### Task 1: X",
        "**Утверждения**",
        "- пункт один, пример:",
        "",
        "```",
        "---",
        "```",
        "",
        "- пункт два;",
        "- пункт три;",
    ]
    block = enumerate_review_units.task_block(lines, 1)
    items = enumerate_review_units.assertion_items(block)
    assert [item_id for item_id, _ in items] == ["A1.1", "A1.2", "A1.3"]
    assert items[1][1] == "пункт два;"
    assert items[2][1] == "пункт три;"


def test_assertion_items_fence_hides_bullet_lines_from_becoming_items() -> None:
    """I5: строка `- …` внутри ```-ограждения не становится отдельным пунктом.

    До фикса round 1 (F9) `- это не пункт` внутри ```python-примера давало
    ЛИШНИЙ элемент знаменателя (`A1.2`), а настоящий второй пункт съезжал на
    `A1.3` — а в разделе «Interfaces» каждого плана такие ограждения уже есть.
    """
    lines = [
        "### Task 1: X",
        "**Утверждения**",
        "- пункт один, смотри:",
        "",
        "```python",
        "- это не пункт",
        "```",
        "",
        "- пункт два;",
    ]
    block = enumerate_review_units.task_block(lines, 1)
    items = enumerate_review_units.assertion_items(block)
    assert [item_id for item_id, _ in items] == ["A1.1", "A1.2"]
    assert items[1][1] == "пункт два;"


# --- emit --------------------------------------------------------------------


def test_emit_prints_tab_separated_rows(capsys: object) -> None:
    enumerate_review_units.emit([("A1.1", "текст один"), ("A1.2", "текст два")])
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert captured.out == "A1.1\tтекст один\nA1.2\tтекст два\n"


def test_emit_accepts_rows_of_different_width(capsys: object) -> None:
    # `emit` обслуживает и будущую подкоманду `hunks` с четырьмя полями —
    # ширина строки не жёстко зашита.
    enumerate_review_units.emit([("H:aaaaaaaa", "path.py", "@@ ctx @@", "+1/-0")])
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert captured.out == "H:aaaaaaaa\tpath.py\t@@ ctx @@\t+1/-0\n"


# --- repo_root и CLI (subprocess, свой временный git-репозиторий) -----------


def _init_repo(tmp_path: Path) -> Path:
    """Свой временный git-репозиторий с одним планом и одной коммиченной правкой.

    Тестам, проверяющим неизменность `git status --porcelain`, нужен НЕ пустой
    статус: штатный момент запуска перечислителя — грязное дерево незакоммиченной
    задачи. Поэтому после коммита плана в дереве остаётся одна незакоммиченная
    правка отслеживаемого файла и один неотслеживаемый файл.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)

    plan_dir = tmp_path / "docs" / "superpowers" / "plans"
    plan_dir.mkdir(parents=True)
    plan_path = plan_dir / "test-plan.md"
    plan_path.write_text(PLAN_TEXT, encoding="utf-8")

    tracked = tmp_path / "tracked.txt"
    tracked.write_text("исходное содержимое\n", encoding="utf-8")

    (tmp_path / "backend").mkdir()

    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)

    # Грязное дерево: правка отслеживаемого файла и новый неотслеживаемый файл.
    tracked.write_text("исходное содержимое\nнезакоммиченная правка\n", encoding="utf-8")
    (tmp_path / "untracked.txt").write_text("новый файл\n", encoding="utf-8")

    return tmp_path


def _run_assertions(cwd: Path, plan_rel: str, task: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "assertions", "--plan", plan_rel, "--task", task],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _run_assertions_bytes(cwd: Path, plan_rel: str, task: str) -> subprocess.CompletedProcess[bytes]:
    """Тот же прогон, но БЕЗ `text=True`: `stdout`/`stderr` — сырые байты.

    `text=True` включает universal-newline перевод и декодирование, то есть
    маскирует то, что реально пишет процесс. Утверждение A1 («побайтно равный
    вывод») обязано сравнивать именно байты, не нормализованный текст (найдено
    ревью round 1, F5).
    """
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "assertions", "--plan", plan_rel, "--task", task],
        cwd=cwd,
        capture_output=True,
        check=False,
    )


def test_repo_root_resolution_independent_of_cwd(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    plan_rel = "docs/superpowers/plans/test-plan.md"

    from_root = _run_assertions(repo, plan_rel, "1")
    from_backend = _run_assertions(repo / "backend", plan_rel, "1")

    assert from_root.returncode == 0
    assert from_backend.returncode == 0
    assert from_root.stdout == from_backend.stdout
    assert from_root.stdout != ""


def test_cli_output_is_deterministic_across_two_runs(tmp_path: Path) -> None:
    """A1, часть первая: два прогона дают одинаковый вывод — побайтно, не текстом.

    Сравнение раньше шло через `text=True` (universal newlines, декодирование) —
    это сравнение НОРМАЛИЗОВАННОГО текста, а утверждение говорит про байты
    (найдено ревью round 1, F5). `_run_assertions_bytes` не декодирует ничего.
    """
    repo = _init_repo(tmp_path)
    plan_rel = "docs/superpowers/plans/test-plan.md"

    first = _run_assertions_bytes(repo, plan_rel, "1")
    second = _run_assertions_bytes(repo, plan_rel, "1")

    assert first.returncode == 0
    assert first.stdout == second.stdout


def test_cli_stdout_matches_known_bytes_exactly_no_hidden_timestamp(tmp_path: Path) -> None:
    """A1, часть вторая: побайтный оракул — единственное, что реально ловит метку времени.

    Раунд-трип «два прогона подряд равны» СТРУКТУРНО не ловит метку времени
    ГРУБОЙ точности (например, дату) — она не меняется между двумя быстрыми
    прогонами и потому проходит 22 из 22 (найдено ревью round 1, F5). Сравнение
    с ЗАРАНЕЕ известными байтами ловит любую метку и любую иную примесь
    детерминированно, а не по совпадению границы секунды.
    """
    repo = _init_repo(tmp_path)
    result = _run_assertions_bytes(repo, "docs/superpowers/plans/test-plan.md", "1")

    # `os.linesep`, а не голый `"\n"`: `print()` на Windows транслирует перевод
    # строки в `\r\n` даже когда stdout перенаправлен в пайп подпроцесса — это
    # свойство платформы, не скрипта, и оракул обязан сравнивать с тем, что
    # платформа реально пишет, а не с тем, что удобно набрать в исходнике теста.
    expected = "".join(f"{item_id}\t{text}\n" for item_id, text in TASK1_ITEMS).replace("\n", os.linesep)

    assert result.returncode == 0
    assert result.stdout == expected.encode("utf-8")


def test_cli_missing_task_is_nonzero_with_its_own_message(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    result = _run_assertions(repo, "docs/superpowers/plans/test-plan.md", "99")

    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr != ""


def test_cli_missing_assertions_section_is_nonzero_with_a_different_message(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    result = _run_assertions(repo, "docs/superpowers/plans/test-plan.md", "3")

    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr != ""


def test_cli_missing_task_and_missing_section_report_different_messages(tmp_path: Path) -> None:
    """C2: сообщения различаются ПО СУТИ отказа, а не подставленным номером задачи.

    Прежняя версия сравнивала `stderr` двух прогонов с РАЗНЫМИ номерами задачи
    (99 и 3) — строки различались уже из-за подставленного числа, и мутант,
    печатающий В ОБЕИХ ветках один и тот же шаблон `"задача {task} не найдена
    в плане {plan}"`, проходил зелёным (найдено ревью round 1, F2: отдельный
    прогон `-k different_messages` — 1 passed на мутанте). Два разных отказа
    на ОДНОМ номере задачи не построить (задача либо есть, либо нет), поэтому
    здесь сравнивается ПРИСУТСТВИЕ характерной, не зависящей от номера подстроки
    каждого отказа — и её ОТСУТСТВИЕ у чужого: под мутантом «единый шаблон»
    `missing_section.stderr` содержал бы «не найдена» вместо «нет раздела».
    """
    repo = _init_repo(tmp_path)
    missing_task = _run_assertions(repo, "docs/superpowers/plans/test-plan.md", "99")
    missing_section = _run_assertions(repo, "docs/superpowers/plans/test-plan.md", "3")

    assert missing_task.returncode != 0
    assert missing_section.returncode != 0

    assert "не найдена" in missing_task.stderr
    assert "не найдена" not in missing_section.stderr

    assert "нет раздела «Утверждения»" in missing_section.stderr
    assert "нет раздела «Утверждения»" not in missing_task.stderr


def test_cli_stderr_is_valid_utf8_without_external_pythonioencoding(tmp_path: Path) -> None:
    """`stderr` подкоманды `assertions` — валидный UTF-8 с русским текстом БЕЗ внешней `PYTHONIOENCODING`.

    `just ci` эту переменную не выставляет, а `_run_assertions` наследует
    окружение процесса pytest КАК ЕСТЬ — набор был зелёным два круга подряд
    только потому, что переменная стояла в оболочке разработчика/оркестратора
    (найдено ревью круга 2: тот же прогон, каким его выполнит `just ci`, дал
    `1 failed`). Тест обязан явно СТРОИТЬ окружение подпроцесса БЕЗ неё
    (`env.pop("PYTHONIOENCODING", None)`), а не полагаться на то, что её нет у
    текущего процесса — иначе он наследует гигиену разработчика и ничего не
    стережёт, ровно как сегодня.

    До правки настраивался только `sys.stdout`: cp1252 отдавала кириллицу
    `stderr` как `backslashreplace`-escape'ы, а кавычка-«ёлочка» (`«` — валидный
    байт `0xab` в cp1252) вперемешку с этими escape'ами не декодировалась как
    UTF-8 вовсе — сам `.decode("utf-8")` ниже часть утверждения: под дефектом
    он бросал `UnicodeDecodeError`, а не просто давал «неправильный» текст.
    """
    repo = _init_repo(tmp_path)
    env = {**os.environ}
    env.pop("PYTHONIOENCODING", None)

    def run(task: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "assertions", "--plan", "docs/superpowers/plans/test-plan.md", "--task", task],
            cwd=repo,
            capture_output=True,
            env=env,
            check=False,
        )

    missing_task = run("99")
    missing_section = run("3")

    assert missing_task.returncode != 0
    assert missing_section.returncode != 0

    missing_task_stderr = missing_task.stderr.decode("utf-8")
    missing_section_stderr = missing_section.stderr.decode("utf-8")

    assert "задача 99 не найдена" in missing_task_stderr
    assert "нет раздела «Утверждения»" in missing_section_stderr


def test_cli_does_not_modify_the_tree_or_the_index(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    def status() -> str:
        return subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout

    before = status()
    # Дерево обязано быть грязным ДО прогона — иначе тест доказывает не то,
    # что заявлено (пустой статус там был бы исключением, а не нормой).
    assert before != ""

    result = _run_assertions(repo, "docs/superpowers/plans/test-plan.md", "1")
    assert result.returncode == 0

    after = status()
    assert after == before


def test_cli_empty_assertions_section_is_exit_zero_with_empty_output(tmp_path: Path) -> None:
    """Minor (F12): раздел «Утверждения» есть, но пуст — код 0, stdout пуст.

    Закреплено тестом как НАМЕРЕНИЕ, не как случайность: бриф запрещает пустой
    вывод с кодом 0 только для ДВУХ ИМЕНОВАННЫХ отказов — отсутствующей задачи
    и отсутствующего раздела. Существующий, но пустой раздел «Утверждения» —
    третье, законное состояние (например, вырожденная по §3.6 задача до того,
    как в неё вписали хоть один пункт), и отказом не является.
    """
    repo = _init_repo(tmp_path)
    plan_dir = repo / "docs" / "superpowers" / "plans"
    plan_path = plan_dir / "empty-section-plan.md"
    plan_path.write_text(
        "### Task 1: Задача с пустым разделом\n\n**Утверждения**\n\n**Проверка**\n- зелёная.\n",
        encoding="utf-8",
    )

    result = _run_assertions(repo, "docs/superpowers/plans/empty-section-plan.md", "1")

    assert result.returncode == 0
    assert result.stdout == ""


def test_cli_fenced_only_assertions_marker_is_treated_as_missing_section(tmp_path: Path) -> None:
    """Critical (round 2): `**Утверждения**` ТОЛЬКО внутри ```-ограждения — не раздел.

    До этой правки `_run_assertions` искал строку `**Утверждения**` СВОИМ,
    наивным (не знающим про ограждения) предикатом — второй копией того же
    признака, который `assertion_items` уже проверял fence-aware способом
    после круга 1. Единственное вхождение `**Утверждения**` внутри примера
    markdown в разделе «Interfaces» (иллюстрация того, как выглядит заголовок,
    а не сам раздел) наивная предпроверка засчитывала как «раздел есть»: отказ
    3 не срабатывал, `assertion_items` честно возвращал `[]`, и
    `_run_assertions` печатал ПУСТОЙ stdout с кодом 0 — задача без утверждений
    становилась неотличима от задачи с законно пустым разделом (найдено ревью
    round 2, единственное Critical). Правка убрала вторую копию признака:
    теперь и разбор, и предпроверка отказа спрашивают одну и ту же
    `_assertions_header_index`.
    """
    repo = _init_repo(tmp_path)
    plan_dir = repo / "docs" / "superpowers" / "plans"
    plan_path = plan_dir / "fenced-marker-plan.md"
    plan_path.write_text(
        "### Task 1: Задача, где раздел только в примере кода\n"
        "\n"
        "**Interfaces**\n"
        "\n"
        "```\n"
        "**Утверждения**\n"
        "- вот так будет выглядеть заголовок раздела — это пример, не раздел;\n"
        "```\n"
        "\n"
        "**Проверка**\n"
        "- зелёная.\n",
        encoding="utf-8",
    )

    result = _run_assertions(repo, "docs/superpowers/plans/fenced-marker-plan.md", "1")

    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr != ""


# =============================================================================
# `hunks` (задача 2)
# =============================================================================


def _init_hunks_repo(root: Path) -> Path:
    """Пустой временный git-репозиторий с `core.autocrlf=false`.

    `autocrlf` отключён нарочно: без этого git на Windows переписывает LF в
    CRLF на чекауте (предупреждение реально всплывало в этой сессии при
    ручной проверке сценариев) — тогда байтовое содержимое файла, записанное
    тестом через `_write`, разошлось бы с тем, что видит `git diff`, и тесты
    нормализации (сдвиг номеров строк, хвостовой пробел) перестали бы
    что-либо доказывать.
    """
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=root, check=True)
    return root


def _write(repo: Path, rel_path: str, content: str) -> Path:
    """Пишет `content` (UTF-8) по `rel_path` внутри `repo` — БАЙТАМИ, не `write_text`.

    `Path.write_text` пропускает содержимое через текстовый режим платформы —
    на Windows это перевод `\n` в `\r\n`, ровно ловушка, из-за которой скрипт
    этого задания обязан читать/писать байты. Тесты нормализации без этого
    были бы недоказательны: расхождение шло бы от записи теста, а не от кода.
    """
    path = repo / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))
    return path


def _commit_all(repo: Path, message: str = "init") -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


def _cli_hunks_text(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "hunks"],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _cli_hunks_bytes(cwd: Path) -> subprocess.CompletedProcess[bytes]:
    """Тот же прогон, БЕЗ `text=True` — как `_run_assertions_bytes` у задачи 1."""
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "hunks"],
        cwd=cwd,
        capture_output=True,
        check=False,
    )


# --- hunk_id: оракул, посчитан оркестратором независимо от этого модуля -----

PRICE_ORACLE_BODY = "\n".join(
    [
        "+    if not value.is_finite():",
        "+        return False",
        "-    return value > 0",
    ]
)


def test_hunk_id_known_input_known_output_price_py() -> None:
    """Известный вход (путь + тело буквально из брифа) — известный ответ.

    Не пересказ алгоритма и не сверка с самим собой: значение `H:c81dd3dd`
    взято из задания и посчитано оркестратором отдельной программой.
    """
    assert enumerate_review_units.hunk_id("backend/money/price.py", PRICE_ORACLE_BODY) == "H:c81dd3dd"


def test_hunk_id_same_body_different_path_gives_different_id() -> None:
    """Тот же текст тела при ДРУГОМ пути — ДРУГОЙ id: путь входит в хэш.

    Сверяется с литералом `H:2292efa1` из задания, а не с результатом вызова
    `hunk_id` для первого пути — иначе тест доказывал бы только то, что
    функция способна вернуть два разных значения на разных входах, что верно
    даже для сломанной реализации (например, «хэш только пути»).
    """
    assert enumerate_review_units.hunk_id("backend/money/other.py", PRICE_ORACLE_BODY) == "H:2292efa1"


def test_hunk_id_form_is_H_colon_eight_hex() -> None:
    identifier = enumerate_review_units.hunk_id("any/path.py", "+line")
    assert re.fullmatch(r"H:[0-9a-f]{8}", identifier)


# --- diff_hunks: текстовые hunk'и --------------------------------------------


def test_diff_hunks_basic_text_hunk_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Путь, контекст `@@`, тело и счётчик одного простого текстового hunk'а.

    Ожидаемое тело — буквальный вывод `git diff HEAD` на этом сценарии,
    снятый независимо от парсера (прямым прогоном git) ДО того, как этот
    тест был написан, а не пересчитанный тем же кодом, который проверяется.
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "backend/money/price.py", "def f(value):\n    return value > 0\n")
    _commit_all(repo)
    _write(
        repo,
        "backend/money/price.py",
        "def f(value):\n    if not value.is_finite():\n        return False\n",
    )

    monkeypatch.chdir(repo)
    hunks = enumerate_review_units.diff_hunks()

    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk.path == "backend/money/price.py"
    assert hunk.context == "@@ -1,2 +1,3 @@"
    assert hunk.body == "-    return value > 0\n+    if not value.is_finite():\n+        return False"
    assert (hunk.added, hunk.removed) == (2, 1)


def test_diff_hunks_sees_staged_and_unstaged_parts_of_the_same_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Файл, часть которого застейджена, а часть нет, даёт hunk'и ОБОИХ видов.

    `git diff HEAD` (не `--cached`, не голый `git diff`) обязана видеть и то,
    что уже в индексе, и то, что ещё нет, ОДНИМ вызовом. Два region'а этого
    файла разнесены на 30 строк-заполнителей — заведомо больше контекста
    diff'а по умолчанию (3 строки), иначе git мог бы слить их в один hunk и
    тест перестал бы различать «два hunk'а» от «один большой».
    """
    filler = "\n".join(f"filler {i}" for i in range(30))
    base = f"TOP\n{filler}\nold_a\n{filler}\nold_b\nBOTTOM\n"
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "region.txt", base)
    _commit_all(repo)

    staged_only = base.replace("old_a", "new_a")
    _write(repo, "region.txt", staged_only)
    subprocess.run(["git", "add", "region.txt"], cwd=repo, check=True)

    both = staged_only.replace("old_b", "new_b")
    _write(repo, "region.txt", both)

    monkeypatch.chdir(repo)
    hunks = [h for h in enumerate_review_units.diff_hunks() if h.path == "region.txt"]

    assert len(hunks) == 2
    assert hunks[0].body == "-old_a\n+new_a"
    assert hunks[1].body == "-old_b\n+new_b"


def test_diff_hunks_line_shift_gives_the_same_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Тот же hunk со сдвинутыми номерами строк даёт ТОТ ЖЕ id.

    Два репозитория с РАЗНЫМ числом строк-заполнителей перед одинаковой
    правкой: заголовок `@@` у них заведомо разный (номера строк реально
    сдвинуты), а тело — байт в байт одно и то же. Доказывает, что заголовок
    `@@` не участвует в теле/хэше — а не пересказывает это утверждение.
    """

    def make(root: Path, filler_lines: int) -> None:
        pad = "\n".join(f"pad{i}" for i in range(filler_lines))
        _write(root, "backend/money/price.py", f"{pad}\ndef f(value):\n    return value > 0\n")
        _commit_all(root)
        _write(
            root,
            "backend/money/price.py",
            f"{pad}\ndef f(value):\n    if not value.is_finite():\n        return False\n",
        )

    repo_a = _init_hunks_repo(tmp_path / "a")
    make(repo_a, filler_lines=1)
    monkeypatch.chdir(repo_a)
    hunks_a = enumerate_review_units.diff_hunks()

    repo_b = _init_hunks_repo(tmp_path / "b")
    make(repo_b, filler_lines=9)
    monkeypatch.chdir(repo_b)
    hunks_b = enumerate_review_units.diff_hunks()

    assert len(hunks_a) == 1
    assert len(hunks_b) == 1
    assert hunks_a[0].context != hunks_b[0].context
    assert hunks_a[0].body == hunks_b[0].body
    id_a = enumerate_review_units.hunk_id(hunks_a[0].path, hunks_a[0].body)
    id_b = enumerate_review_units.hunk_id(hunks_b[0].path, hunks_b[0].body)
    assert id_a == id_b


def test_diff_hunks_trailing_whitespace_is_normalized_away(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Добавленный хвостовой пробел на изменённой строке — ТОТ ЖЕ id.

    Вход обязан реально нести пробел: версия `trailing` заканчивает
    добавленную строку тремя пробелами, версия `clean` — нет. Оба тела
    сравниваются с независимо написанным ожиданием, а не друг с другом
    напрямую, чтобы падение нормализации было видно и по форме тела.
    """
    repo_clean = _init_hunks_repo(tmp_path / "clean")
    _write(repo_clean, "f.txt", "value = 1\n")
    _commit_all(repo_clean)
    _write(repo_clean, "f.txt", "value = 2\n")

    repo_trailing = _init_hunks_repo(tmp_path / "trailing")
    _write(repo_trailing, "f.txt", "value = 1\n")
    _commit_all(repo_trailing)
    _write(repo_trailing, "f.txt", "value = 2   \n")

    monkeypatch.chdir(repo_clean)
    hunks_clean = enumerate_review_units.diff_hunks()
    monkeypatch.chdir(repo_trailing)
    hunks_trailing = enumerate_review_units.diff_hunks()

    expected_body = "-value = 1\n+value = 2"
    assert hunks_clean[0].body == expected_body
    assert hunks_trailing[0].body == expected_body
    assert enumerate_review_units.hunk_id("f.txt", hunks_clean[0].body) == enumerate_review_units.hunk_id(
        "f.txt", hunks_trailing[0].body
    )


# --- diff_hunks: двоичные hunk'и (три независимых входа) ---------------------

BINARY_OLD = b"OLD-CONTENT\x00\x01\x02binary"
BINARY_NEW = b"NEW-CONTENT\x00\x03\x04binary"
BINARY_ADDED = b"ADDED-CONTENT\x00\x05binary"


def test_diff_hunks_binary_file_added(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "keep.txt", "x\n")
    _commit_all(repo)
    (repo / "new.bin").write_bytes(BINARY_ADDED)
    subprocess.run(["git", "add", "new.bin"], cwd=repo, check=True)

    monkeypatch.chdir(repo)
    hunks = [h for h in enumerate_review_units.diff_hunks() if h.path == "new.bin"]

    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk.context == "@@ binary @@"
    assert hunk.body == f"+binary {hashlib.sha256(BINARY_ADDED).hexdigest()}"
    assert (hunk.added, hunk.removed) == (1, 0)


def test_diff_hunks_binary_file_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_hunks_repo(tmp_path)
    (repo / "old.bin").write_bytes(BINARY_OLD)
    _commit_all(repo)
    (repo / "old.bin").unlink()

    monkeypatch.chdir(repo)
    hunks = [h for h in enumerate_review_units.diff_hunks() if h.path == "old.bin"]

    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk.context == "@@ binary @@"
    assert hunk.body == f"-binary {hashlib.sha256(BINARY_OLD).hexdigest()}"
    assert (hunk.added, hunk.removed) == (0, 1)


def test_diff_hunks_binary_file_modified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_hunks_repo(tmp_path)
    (repo / "x.bin").write_bytes(BINARY_OLD)
    _commit_all(repo)
    (repo / "x.bin").write_bytes(BINARY_NEW)

    monkeypatch.chdir(repo)
    hunks = [h for h in enumerate_review_units.diff_hunks() if h.path == "x.bin"]

    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk.context == "@@ binary @@"
    expected_body = f"-binary {hashlib.sha256(BINARY_OLD).hexdigest()}\n+binary {hashlib.sha256(BINARY_NEW).hexdigest()}"
    assert hunk.body == expected_body
    assert (hunk.added, hunk.removed) == (1, 1)


def test_diff_hunks_binary_add_and_modify_to_same_bytes_give_different_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`A → B` и `C → B` различаются: тело несёт СТАРЫЙ хэш, не только новый.

    Односторонняя форма `+binary <sha256 текущего>` отождествила бы эти две
    разные правки — обе кончаются одним и тем же `B`, и у неё не было бы как
    отличить «откуда» правка пришла.
    """
    repo_from_a = _init_hunks_repo(tmp_path / "from_a")
    (repo_from_a / "x.bin").write_bytes(b"AAAA\x00side")
    _commit_all(repo_from_a)
    (repo_from_a / "x.bin").write_bytes(BINARY_NEW)

    repo_from_c = _init_hunks_repo(tmp_path / "from_c")
    (repo_from_c / "x.bin").write_bytes(b"CCCC\x00side")
    _commit_all(repo_from_c)
    (repo_from_c / "x.bin").write_bytes(BINARY_NEW)

    monkeypatch.chdir(repo_from_a)
    body_from_a = next(h for h in enumerate_review_units.diff_hunks() if h.path == "x.bin").body
    monkeypatch.chdir(repo_from_c)
    body_from_c = next(h for h in enumerate_review_units.diff_hunks() if h.path == "x.bin").body

    assert body_from_a != body_from_c
    id_from_a = enumerate_review_units.hunk_id("x.bin", body_from_a)
    id_from_c = enumerate_review_units.hunk_id("x.bin", body_from_c)
    assert id_from_a != id_from_c


# --- untracked_hunks ----------------------------------------------------------


def test_untracked_hunks_text_file_is_one_hunk_added_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "README.md", "keep\n")
    _commit_all(repo)
    _write(repo, "new_file.txt", "line one\nline two   \nline three\n")

    monkeypatch.chdir(repo)
    hunks = enumerate_review_units.untracked_hunks()

    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk.path == "new_file.txt"
    assert hunk.context == "@@ -0,0 +1,3 @@"
    assert hunk.body == "+line one\n+line two\n+line three"
    assert (hunk.added, hunk.removed) == (3, 0)


def test_untracked_hunks_ignored_file_gives_none_two_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Неотслеживаемый неигнорируемый файл — один hunk; игнорируемый — ноль.

    Два входа рядом, а не рассуждение по одному: `visible.txt` не в
    `.gitignore`, `ignored.txt` — в нём. Список результата обязан содержать
    только первый.
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, ".gitignore", "ignored.txt\n")
    _commit_all(repo)
    _write(repo, "visible.txt", "hello\n")
    _write(repo, "ignored.txt", "should not appear\n")

    monkeypatch.chdir(repo)
    hunks = enumerate_review_units.untracked_hunks()

    assert [h.path for h in hunks] == ["visible.txt"]


def test_untracked_hunks_binary_file_uses_binary_add_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Неотслеживаемый ДВОИЧНЫЙ файл — форма «добавление» таблицы двоичных.

    Решение оркестратора по неоднозначности: не текстовое тело над байтами
    (оно над ними не определено), а та же форма, что у добавленного
    двоичного файла из `diff_hunks`.
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "README.md", "keep\n")
    _commit_all(repo)
    (repo / "blob.bin").write_bytes(BINARY_ADDED)

    monkeypatch.chdir(repo)
    hunks = enumerate_review_units.untracked_hunks()

    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk.path == "blob.bin"
    assert hunk.context == "@@ binary @@"
    assert hunk.body == f"+binary {hashlib.sha256(BINARY_ADDED).hexdigest()}"
    assert (hunk.added, hunk.removed) == (1, 0)


# --- CLI `hunks`: формат, read-only, порядок, суффиксы дублей ----------------


def test_cli_hunks_empty_tree_is_exit_zero_with_empty_output(tmp_path: Path) -> None:
    """Пустое дерево (нет diff'а, нет untracked) — пустой вывод, код 0: не отказ."""
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "a.txt", "content\n")
    _commit_all(repo)

    result = _cli_hunks_text(repo)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_cli_hunks_output_is_tab_separated_four_fields_no_timestamp(tmp_path: Path) -> None:
    """Побайтный оракул строки вывода: id, путь, контекст, счётчик — через TAB, без меток времени.

    Как и у `assertions` (F5 круга 1 той задачи): раунд-трип «два прогона
    равны» не ловит метку времени грубой точности. Здесь сразу сравнение с
    ЗАРАНЕЕ известными байтами.
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "backend/money/price.py", "def f(value):\n    return value > 0\n")
    _commit_all(repo)
    _write(
        repo,
        "backend/money/price.py",
        "def f(value):\n    if not value.is_finite():\n        return False\n",
    )

    result = _cli_hunks_bytes(repo)

    # `H:c2008c37` — литерал, посчитанный отдельным вызовом `hashlib.sha256`
    # ДО этого теста (ревью круга 1, Minor): вызов `hunk_id` здесь сверял бы
    # оракул сам с собой — тавтология, которая осталась бы зелёной даже при
    # сломанном `hunk_id`, лишь бы `_run_hunks` вызывал ту же (сломанную)
    # функцию с теми же аргументами.
    # `os.linesep`: `print()` транслирует `\n` в `\r\n` на Windows даже когда
    # stdout — пайп подпроцесса (тот же приём, что в тестах `assertions`).
    expected_line = "H:c2008c37\tbackend/money/price.py\t@@ -1,2 +1,3 @@\t+2/-1\n".replace("\n", os.linesep)

    assert result.returncode == 0
    assert result.stdout == expected_line.encode("utf-8")


def test_cli_hunks_does_not_modify_the_tree_or_the_index(tmp_path: Path) -> None:
    """Индекс и дерево не меняются — проверено СЛЕДСТВИЕМ, не временем файла.

    Неотслеживаемый файл до прогона числится в `git status --porcelain` как
    `??`; после прогона — по-прежнему `??`, а не `A ` (что означало бы, что
    скрипт сделал `git add`/`git add -N`). Сверка `mtime` `.git/index` для
    этого не годится: его обновляет и сам `git status`, и тест краснел бы по
    причине, к скрипту не относящейся.
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "a.txt", "content\n")
    _commit_all(repo)
    _write(repo, "a.txt", "content\nchanged\n")
    _write(repo, "untracked.txt", "new\n")

    def status() -> str:
        return subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout

    before = status()
    assert before != ""
    assert "?? untracked.txt" in before

    result = _cli_hunks_text(repo)
    assert result.returncode == 0

    after = status()
    assert "?? untracked.txt" in after
    assert after == before


def test_cli_hunks_order_matches_path_sort(tmp_path: Path) -> None:
    """Три правленных файла — вывод в порядке сортировки путей, не создания/правки.

    Три ОТСЛЕЖИВАЕМЫХ файла в одном каталоге git и так обходит по имени —
    этот случай сам по себе не отличил бы явную сортировку от совпадения
    (проверено мутацией: удаление `sorted(...)` в `_run_hunks` эту версию
    теста не красит). Поэтому здесь дополнительно ТРЕТИЙ файл —
    неотслеживаемый, из ДРУГОГО источника (`untracked_hunks`, а не
    `diff_hunks`), с путём, который по алфавиту должен встать МЕЖДУ двумя
    отслеживаемыми. `diff_hunks()` и `untracked_hunks()` — два отдельных
    списка, конкатенация которых без явной сортировки ВСЕГДА кладёт весь
    результат `untracked_hunks()` последним, независимо от алфавита; только
    сортировка по пути в `_run_hunks` создаёт межисточниковое чередование.
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "b_tracked.txt", "old\n")
    _write(repo, "d_tracked.txt", "old\n")
    _commit_all(repo)
    _write(repo, "b_tracked.txt", "new\n")
    _write(repo, "d_tracked.txt", "new\n")
    _write(repo, "c_untracked.txt", "new file\n")

    result = _cli_hunks_text(repo)
    paths = [line.split("\t")[1] for line in result.stdout.splitlines() if line]

    assert paths == ["b_tracked.txt", "c_untracked.txt", "d_tracked.txt"]


GAP = "\n".join(f"g{i}" for i in range(20))


def _dup_content(markers: list[str]) -> str:
    """Строки-маркеры, разнесённые `GAP`-ом (20 строк) — заведомо разные hunk'и.

    20 строк заполнителя — больше 2×3 (контекст diff'а по умолчанию), иначе
    git мог бы слить соседние изменения в один hunk, и тест перестал бы
    различать «N дублей» от «один большой hunk».
    """
    return (f"\n{GAP}\n").join(markers) + "\n"


def test_cli_hunks_duplicate_bodies_get_positional_dot_suffixes(tmp_path: Path) -> None:
    """Три hunk'а с одинаковым нормализованным телом в одном файле — `H:xxxx`, `.2`, `.3`."""
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "dup.txt", _dup_content(["DUP_OLD"] * 3))
    _commit_all(repo)
    _write(repo, "dup.txt", _dup_content(["DUP_NEW"] * 3))

    result = _cli_hunks_text(repo)
    ids = [line.split("\t")[0] for line in result.stdout.splitlines() if line.split("\t")[1] == "dup.txt"]

    raw = ids[0]
    assert re.fullmatch(r"H:[0-9a-f]{8}", raw)
    assert ids == [raw, f"{raw}.2", f"{raw}.3"]


def test_cli_hunks_earlier_duplicate_shifts_neighbor_suffixes(tmp_path: Path) -> None:
    """Появление дубликата РАНЬШЕ по порядку сдвигает суффиксы соседей (спека §3.10).

    Не рассуждение, а отдельный вход: тройка правок (как в тесте выше) и
    отдельно четвёрка — та же тройка плюс ЕЩЁ ОДНА правка того же
    нормализованного тела, стоящая в файле ПЕРВОЙ. В тройке второй элемент
    несёт `.2`; в четвёрке та же по смыслу позиция (теперь третья по счёту)
    несёт уже `.3` — суффикс пересчитан заново, а не унаследован.
    """
    repo3 = _init_hunks_repo(tmp_path / "three")
    _write(repo3, "dup.txt", _dup_content(["DUP_OLD"] * 3))
    _commit_all(repo3)
    _write(repo3, "dup.txt", _dup_content(["DUP_NEW"] * 3))
    result3 = _cli_hunks_text(repo3)
    ids3 = [line.split("\t")[0] for line in result3.stdout.splitlines() if line.split("\t")[1] == "dup.txt"]

    repo4 = _init_hunks_repo(tmp_path / "four")
    _write(repo4, "dup.txt", _dup_content(["DUP_OLD"] * 4))
    _commit_all(repo4)
    _write(repo4, "dup.txt", _dup_content(["DUP_NEW"] * 4))
    result4 = _cli_hunks_text(repo4)
    ids4 = [line.split("\t")[0] for line in result4.stdout.splitlines() if line.split("\t")[1] == "dup.txt"]

    raw = ids3[0]
    assert ids3 == [raw, f"{raw}.2", f"{raw}.3"]
    assert ids4 == [raw, f"{raw}.2", f"{raw}.3", f"{raw}.4"]
    assert ids3[1] == f"{raw}.2"
    assert ids4[2] == f"{raw}.3"


# =============================================================================
# Круг 1 ревью: входы, где инструмент либо падает целиком, либо молча теряет
# элемент знаменателя. Critical K1-K7, Important I1-I4.
# =============================================================================


# --- K1: неотслеживаемый КАТАЛОГ ---------------------------------------------


def test_untracked_hunks_new_directory_recurses_into_its_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Новый каталог не сворачивается в одну запись `?? newdir/` — файлы внутри видны по отдельности.

    По умолчанию `git status --porcelain` печатает НОВЫЙ каталог одной
    записью `?? newdir/`, и файлы внутри (в том числе во вложенном
    подкаталоге) не попадают в знаменатель вовсе — молчаливая потеря целой
    поддиректории (найдено ревью круга 1, K1).
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "keep.txt", "keep\n")
    _commit_all(repo)
    _write(repo, "newdir/one.txt", "a\n")
    _write(repo, "newdir/sub/two.txt", "b\n")

    monkeypatch.chdir(repo)
    hunks = enumerate_review_units.untracked_hunks()

    assert {h.path for h in hunks} == {"newdir/one.txt", "newdir/sub/two.txt"}


# --- K2: неотслеживаемый путь с пробелом -------------------------------------


def test_untracked_hunks_path_with_space_is_unquoted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Путь с пробелом git заворачивает в кавычки — `core.quotepath=false` их не снимает.

    `core.quotepath` влияет только на не-ASCII байты ВНУТРИ кавычек, не на
    само решение кавычить путь с пробелом. Без снятия кавычек путь
    `read_bytes()` получал бы буквальные `"` в имени и падал `OSError`
    (найдено ревью круга 1, K2).
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "keep.txt", "keep\n")
    _commit_all(repo)
    _write(repo, "spaced file.txt", "hello\n")

    monkeypatch.chdir(repo)
    hunks = enumerate_review_units.untracked_hunks()

    assert len(hunks) == 1
    assert hunks[0].path == "spaced file.txt"
    assert hunks[0].body == "+hello"


# --- K3: неотслеживаемый текст не в UTF-8 ------------------------------------


def test_untracked_hunks_non_utf8_text_without_nul_is_a_text_hunk_not_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cp1251-текст без NUL-байта (эвристика двоичности его не ловит) — текстовый hunk, не двоичный.

    Круг 3 внешнего ревью (Important I3, открыт с первой волны): двоичность
    здесь определяется ЕДИНСТВЕННЫМ признаком — нулевым байтом, тем же, что
    и у git. До этой правки НЕ декодируемый как UTF-8, но БЕЗ NUL контент
    получал СВОЙ, ВТОРОЙ признак двоичности (`try: raw_bytes.decode("utf-8")`)
    здесь, которого у git (а значит и у `diff_hunks` для того же,
    ЗАСТЕЙДЖЕННОГО файла) нет вовсе — git считает его текстом всегда, когда
    в нём нет NUL. Тот же файл, untracked и staged, расходился на РАЗНЫХ
    ветвях построения тела и получал РАЗНЫЙ `H:` от одного `git add`
    (найдено внешним ревью: `H:00e486cf` untracked против `H:86cec3f6`
    staged). Padать `UnicodeDecodeError`-ом по-прежнему нельзя — тело просто
    не требует валидного UTF-8 вовсе, оно строится из СЫРЫХ байт строки, а
    для показа декодируется с `errors="replace"` (см. `Hunk`).
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "keep.txt", "keep\n")
    _commit_all(repo)
    cp1251_bytes = "привет".encode("cp1251")
    assert b"\x00" not in cp1251_bytes  # эвристика двоичности его не поймает
    (repo / "cp1251.txt").write_bytes(cp1251_bytes)

    monkeypatch.chdir(repo)
    hunks = enumerate_review_units.untracked_hunks()

    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk.path == "cp1251.txt"
    assert hunk.context == "@@ -0,0 +1,1 @@"
    assert hunk.raw_body == b"+" + cp1251_bytes
    assert (hunk.added, hunk.removed) == (1, 0)


def test_untracked_then_staged_non_utf8_without_nul_file_gives_the_same_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Important I3: не-UTF-8 файл БЕЗ NUL-байта — untracked и staged дают ОДИН и тот же `H:`.

    Прямое предъявление второго входа замечания: `git add` не правка
    содержимого, а до этой правки он менял `H:` файла, у которого нет ни NUL
    (эвристику двоичности не проходит), ни валидного UTF-8 (untracked-ветка
    считала его двоичным СВОИМ признаком, staged — git всегда текстом).
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "keep.txt", "keep\n")
    _commit_all(repo)
    cp1251_bytes = "привет".encode("cp1251")
    assert b"\x00" not in cp1251_bytes
    (repo / "cp1251.txt").write_bytes(cp1251_bytes)

    monkeypatch.chdir(repo)
    untracked_hunk = next(h for h in enumerate_review_units.untracked_hunks() if h.path == "cp1251.txt")
    id_untracked = enumerate_review_units.hunk_id(untracked_hunk.path, untracked_hunk.raw_body)

    subprocess.run(["git", "add", "cp1251.txt"], cwd=repo, check=True)
    staged_hunk = next(h for h in enumerate_review_units.diff_hunks() if h.path == "cp1251.txt")
    id_staged = enumerate_review_units.hunk_id(staged_hunk.path, staged_hunk.raw_body)

    assert untracked_hunk.raw_body == staged_hunk.raw_body
    assert id_untracked == id_staged


# --- K4: невалидные UTF-8 байты в `git diff HEAD` ----------------------------


def test_diff_hunks_non_utf8_tracked_content_does_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отслеживаемый файл с невалидными UTF-8 байтами (git печатает его как текст) не роняет чтение.

    git считает файл текстом, если в нём нет NUL, даже если байты не образуют
    валидный UTF-8, и печатает их как есть в `git diff HEAD`. Без
    `errors="replace"` поток чтения `subprocess` ронял `AttributeError` на
    `None`-`stdout` (найдено ревью круга 1, K4). Воспроизведено дословно как
    в замечании: `bytes(range(50, 250))`, закоммичено и удалено.
    """
    repo = _init_hunks_repo(tmp_path)
    weird_bytes = bytes(range(50, 250))
    assert b"\x00" not in weird_bytes
    (repo / "weird.bin").write_bytes(weird_bytes)
    _commit_all(repo)
    (repo / "weird.bin").unlink()

    monkeypatch.chdir(repo)
    hunks = [h for h in enumerate_review_units.diff_hunks() if h.path == "weird.bin"]

    assert len(hunks) == 1
    assert (hunks[0].added, hunks[0].removed) == (0, 1)


# --- K5/K6/K7: секция без единого `@@` и не двоичная -------------------------


def test_diff_hunks_empty_file_added_gives_one_metadata_hunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пустой файл, добавленный в индекс: секция без `@@` — не пропуск, а один синтетический hunk.

    git печатает такую секцию без единого `@@` (нечего показывать), и файл
    не появлялся в выводе никогда — при этом НЕотслеживаемый пустой файл уже
    появлялся (см. `untracked_hunks`): две ветки расходились между собой на
    одном и том же по смыслу событии (найдено ревью круга 1, K5).

    Форма — КАНОНИЧЕСКАЯ, та же, что у untracked-ветки (`@@ -0,0 +1,0 @@`,
    пустое тело), а не `@@ metadata @@` с `"new file mode 100644"`: внешнее
    ревью (BLOCKER 3) показало, что ОДИН и тот же пустой файл до и после
    `git add` иначе получал бы ДВА разных `H:` от события, не менявшего в
    файле ни байта. См. `test_untracked_then_staged_empty_file_gives_the_same_id`
    ниже — прямое доказательство равенства id.
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "keep.txt", "keep\n")
    _commit_all(repo)
    (repo / "empty.py").write_bytes(b"")
    subprocess.run(["git", "add", "empty.py"], cwd=repo, check=True)

    monkeypatch.chdir(repo)
    hunks = [h for h in enumerate_review_units.diff_hunks() if h.path == "empty.py"]

    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk.context == "@@ -0,0 +1,0 @@"
    assert hunk.body == ""
    assert (hunk.added, hunk.removed) == (0, 0)


def test_diff_hunks_empty_file_added_metadata_id_is_independent_of_core_abbrev(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S4: id metadata-hunk'а пустого добавленного файла не зависит от `core.abbrev`.

    Секция такой правки несёт строку `index 0000000..e69de29`, чья длина хэшей
    СЛЕДУЕТ git-настройке `core.abbrev`, а не содержанию правки:
    `git -c core.abbrev=4 diff` печатает `index 0000..e69d`, `core.abbrev=40` —
    полные 40 hex того же блоба (проверено вручную оркестратором до этого
    теста). Без исключения строки `index ` из тела `_metadata_only_hunk` один и
    тот же сценарий на двух машинах с разным `core.abbrev` давал бы РАЗНЫЙ
    `H:` — знаменатель переставал бы быть воспроизводимым, хотя дерево одно и
    то же (найдено финальным сквозным ревью, Critical S4). Два независимых
    репозитория с явно РАЗНЫМ `core.abbrev` — вход, а не рассуждение: один и
    тот же `git add` пустого файла обязан дать один и тот же `H:` в обоих.

    После BLOCKER 3 (внешнее ревью) секция пустого добавленного файла вообще
    не строит тело из описательных строк git — она уходит по канонической
    форме untracked-ветки (`@@ -0,0 +1,0 @@`, пустое тело) и строку `index `
    не читает вовсе; тест остаётся в силе (id по-прежнему не зависит от
    `core.abbrev`), но по более сильной причине, чем раньше.
    """

    def hunk_id_for(root: Path, abbrev: str) -> str:
        repo = _init_hunks_repo(root)
        subprocess.run(["git", "config", "core.abbrev", abbrev], cwd=repo, check=True)
        _write(repo, "keep.txt", "keep\n")
        _commit_all(repo)
        (repo / "empty.py").write_bytes(b"")
        subprocess.run(["git", "add", "empty.py"], cwd=repo, check=True)

        monkeypatch.chdir(repo)
        hunk = next(h for h in enumerate_review_units.diff_hunks() if h.path == "empty.py")
        return enumerate_review_units.hunk_id(hunk.path, hunk.body)

    id_short_abbrev = hunk_id_for(tmp_path / "short", "4")
    id_long_abbrev = hunk_id_for(tmp_path / "long", "40")

    assert id_short_abbrev == id_long_abbrev


def test_diff_hunks_pure_rename_gives_one_metadata_hunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Чистое переименование (`git mv`, без правки содержимого) — один синтетический hunk, не пропуск.

    Решение оркестратора по скоупу круга 1: рассмотрено как «вне скоупа» в
    отчёте задачи 2, это решение отменено — принцип A2.11 («молчаливый
    пропуск запрещён») уже применён к двоичным файлам, и план показал
    технику; выполнение того же принципа на переименовании — не новое
    проектирование (найдено ревью круга 1, K6).
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "a.txt", "content\n")
    _commit_all(repo)
    subprocess.run(["git", "mv", "a.txt", "b.txt"], cwd=repo, check=True)

    monkeypatch.chdir(repo)
    hunks = [h for h in enumerate_review_units.diff_hunks() if h.path == "b.txt"]

    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk.context == "@@ metadata @@"
    assert hunk.body == "similarity index 100%\nrename from a.txt\nrename to b.txt"
    assert (hunk.added, hunk.removed) == (0, 0)


def test_diff_hunks_mode_only_change_gives_one_metadata_hunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Правка одного режима файла (`--chmod`, без правки содержимого) — один синтетический hunk.

    Докстринг раньше называл это намеренным — намеренным быть не может:
    знаменатель обязан содержать элемент на всякую правку, которую ревьюер
    увидит в `git diff` (найдено ревью круга 1, K7).
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "x.sh", "content\n")
    _commit_all(repo)
    subprocess.run(["git", "update-index", "--chmod=+x", "x.sh"], cwd=repo, check=True)

    monkeypatch.chdir(repo)
    hunks = [h for h in enumerate_review_units.diff_hunks() if h.path == "x.sh"]

    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk.context == "@@ metadata @@"
    assert hunk.body == "old mode 100644\nnew mode 100755"
    assert (hunk.added, hunk.removed) == (0, 0)


# --- I1: `splitlines()` рвёт больше, чем перевод строки ----------------------


def test_diff_hunks_form_feed_inside_a_line_does_not_split_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`\\x0c` (form feed) внутри добавленной строки — часть ОДНОЙ строки тела, не граница.

    `str.splitlines()` режет ещё по `\\x0b \\x0c \\x1c-\\x1e \\x85` и юникодным
    U+2028/U+2029 — не только по `\\n`. Одна физическая строка git-диффа
    `+beta\\x0cGAMMA` резалась бы на `+beta` (в теле) и `GAMMA` (без `+`,
    отброшена молча) — часть добавленной строки терялась НЕЗАМЕТНО.
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "a.txt", "beta\n")
    _commit_all(repo)
    _write(repo, "a.txt", "beta\x0cGAMMA\n")

    monkeypatch.chdir(repo)
    hunks = enumerate_review_units.diff_hunks()

    assert len(hunks) == 1
    assert hunks[0].body == "-beta\n+beta\x0cGAMMA"
    assert (hunks[0].added, hunks[0].removed) == (1, 1)


def test_diff_hunks_form_feed_does_not_collide_two_different_hunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Найденная ревью коллизия буквально: `beta`→`beta\\x0cGAMMA` и `beta`→`beta\\x0cDELTA` — РАЗНЫЕ id.

    До фикса обе правки давали тело `-beta\\n+beta` (часть после `\\x0c`
    рвалась и терялась), и оба файла сходились на ОДНОМ `hunk_id` — второй
    становился фиктивным `.2`, хотя это два разных по смыслу hunk'а (найдено
    ревью круга 1, I1).
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "a.txt", "beta\n")
    _write(repo, "b.txt", "beta\n")
    _commit_all(repo)
    _write(repo, "a.txt", "beta\x0cGAMMA\n")
    _write(repo, "b.txt", "beta\x0cDELTA\n")

    monkeypatch.chdir(repo)
    hunks = {h.path: h for h in enumerate_review_units.diff_hunks()}

    assert hunks["a.txt"].body != hunks["b.txt"].body
    id_a = enumerate_review_units.hunk_id("a.txt", hunks["a.txt"].body)
    id_b = enumerate_review_units.hunk_id("b.txt", hunks["b.txt"].body)
    assert id_a != id_b


def test_untracked_hunks_form_feed_inside_a_line_does_not_split_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Тот же дефект `splitlines()`, вторая точка — построение тела untracked-файла."""
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "keep.txt", "keep\n")
    _commit_all(repo)
    _write(repo, "new.txt", "beta\x0cGAMMA\nsecond\n")

    monkeypatch.chdir(repo)
    hunks = enumerate_review_units.untracked_hunks()

    assert len(hunks) == 1
    assert hunks[0].body == "+beta\x0cGAMMA\n+second"
    assert hunks[0].added == 2


# --- I2: репозиторий без единого коммита -------------------------------------


def test_cli_hunks_no_commits_yet_fails_with_a_clear_message_not_a_traceback(
    tmp_path: Path,
) -> None:
    """`git diff HEAD` в репозитории без коммитов отказывает кодом 128 — падать можно, трассировкой нельзя.

    Молча выдавать пустоту здесь нельзя (неотличимо от честного «нет
    правок»), но и трассировка `CalledProcessError` — не годится: git уже
    написал причину отказа в свой `stderr` (найдено ревью круга 1, I2).
    """
    repo = _init_hunks_repo(tmp_path)  # `git init`, но НИ ОДНОГО коммита

    result = _cli_hunks_text(repo)

    assert result.returncode != 0
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
    assert result.stderr.strip() != ""


# --- I3: настройка ВИДА путей в патче не должна менять знаменатель -----------


def test_diff_hunks_survives_diff_noprefix_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`diff.noprefix=true` в чужом конфиге не должен ронять разбор — путь не из текста патча.

    При `diff.noprefix=true` `git diff` печатает `diff --git путь путь` и
    `+++ путь` вовсе БЕЗ префиксов `a/`/`b/`. Круг 1 разбирал путь из этих
    строк по ФИКСИРОВАННОМУ префиксу, и такой конфиг молча выбрасывал КАЖДЫЙ
    отслеживаемый hunk (найдено ревью круга 1, I3); тогда это стерегли
    форсированные `--src-prefix`/`--dst-prefix`.

    С круга 3 путь не читается из текста патча вовсе (он из
    `git diff --raw -z HEAD`), а строка `diff --git ` служит только маркером
    ГРАНИЦЫ секции — форсирование префиксов стало лишним и снято. Этот вход
    стережёт ИМЕННО ТО, что осталось: ни `--no-prefix`, ни любая другая
    настройка ВИДА путей в патче не должна влиять ни на число элементов, ни
    на привязку тела к пути. Верни сюда разбор пути из `+++`/`diff --git` —
    и этот тест снова покраснеет, уже без всяких флагов.
    """
    repo = _init_hunks_repo(tmp_path)
    subprocess.run(["git", "config", "diff.noprefix", "true"], cwd=repo, check=True)
    _write(repo, "f.txt", "old\n")
    _commit_all(repo)
    _write(repo, "f.txt", "new\n")

    monkeypatch.chdir(repo)
    hunks = [h for h in enumerate_review_units.diff_hunks() if h.path == "f.txt"]

    assert len(hunks) == 1
    assert hunks[0].body == "-old\n+new"


# --- Minor: `core.quotepath=false` не стерегётся -----------------------------


def test_diff_hunks_survives_default_quotepath_with_cyrillic_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Кириллическое имя файла (репозиторий русскоязычный) не должно теряться без `quotepath=false`.

    Без `-c core.quotepath=false` git заворачивает не-ASCII путь в кавычки с
    восьмеричными escape-последовательностями (`"\\321\\204…"`). ГДЕ именно
    это до сих пор важно — ИЗМЕРЕНО снятием флага по одной команде за раз:

    * `git status --porcelain` (неотслеживаемые файлы, `untracked_hunks`) —
      флаг ЛОМАЕТСЯ при снятии: путь приходит манглированным, и чтение файла
      падает `FileNotFoundError: …\\\\321\\\\204\\\\320\\\\260…`. Здесь флаг
      несущий, и именно поэтому вход ниже несёт и НЕОТСЛЕЖИВАЕМЫЙ файл, а не
      только отслеживаемый;
    * `git diff --raw -z HEAD` (`_raw_diff_records`) — формат `-z` НЕ кавычит
      путь ВООБЩЕ, ни при каком значении `core.quotepath` (проверено
      hexdump-ом вывода при `quotepath=true` и `quotepath=false`: байты пути
      одинаковы), поэтому снятие флага здесь ничего не меняет;
    * патч (`_whole_diff_args`) — путь из его текста не читается вовсе с
      круга 3, поэтому и здесь снятие флага ничего не меняет.

    На двух последних командах флаг оставлен как страховка на случай, если
    кто-нибудь снова начнёт читать путь из текста патча, и эта оговорка
    записана здесь честно, а не выдана за несущую гарантию.
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "файл.txt", "old\n")
    _commit_all(repo)
    _write(repo, "файл.txt", "new\n")
    _write(repo, "новый.txt", "untracked\n")

    monkeypatch.chdir(repo)
    hunks = [h for h in enumerate_review_units.diff_hunks() if h.path == "файл.txt"]
    untracked = [h for h in enumerate_review_units.untracked_hunks() if h.path == "новый.txt"]

    assert len(hunks) == 1
    assert hunks[0].body == "-old\n+new"
    assert len(untracked) == 1
    assert untracked[0].body == "+untracked"


# --- I4: жадность разбора заголовка `diff --git` -----------------------------


def test_diff_hunks_path_containing_the_diff_header_separator_substring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Путь, содержащий литеральную подстроку ` b/` (`x b/y.txt`), не должен резаться неверно.

    Строка `diff --git a/x b/y.txt b/x b/y.txt` неоднозначна для наивного
    `diff --git a/(.+) b/(.+)` — путь разобрался бы как `y.txt` вместо
    `x b/y.txt` (найдено ревью круга 1, I4). Путь берётся из
    `git diff --raw -z HEAD` (`_raw_diff_records`), а не из этой строки —
    после BLOCKER 2 внешнего ревью это верно для ЛЮБОЙ секции, не только
    текстовой (см. `test_diff_hunks_binary_modified_at_path_containing_diff_header_separator`
    ниже — тот же вход на двоичной и metadata-секции, где раньше падал ВЕСЬ
    перечислитель).
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "x b/y.txt", "old\n")
    _commit_all(repo)
    _write(repo, "x b/y.txt", "new\n")

    monkeypatch.chdir(repo)
    hunks = enumerate_review_units.diff_hunks()

    assert len(hunks) == 1
    assert hunks[0].path == "x b/y.txt"
    assert hunks[0].body == "-old\n+new"


# =============================================================================
# Внешнее ревью (2026-09-12-review-presentation-phase): BLOCKER 1-3, замечания 4-5.
# =============================================================================


# --- BLOCKER 1: `errors="replace"` схлопывал разные невалидные-UTF-8 правки --


def test_diff_hunks_two_different_non_utf8_edits_give_different_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BLOCKER 1: байт `0xFF` и байт `0xFE` в той же позиции той же строки — РАЗНЫЕ `H:`.

    Тело отслеживаемого файла без NUL-байта, но с невалидным UTF-8-байтом, —
    git печатает его как ТЕКСТ (та же ситуация, что и K4). Раньше `hunk_id`
    хэшировал `Hunk.body` — строку, декодированную из `git diff HEAD` с
    `errors="replace"`: ЛЮБОЙ невалидный байт становится ОДНИМ и тем же
    `U+FFFD` ДО хэширования, и потому байт `0xFF` и байт `0xFF` — разные по
    смыслу правки одного файла — давали ОДИН и тот же `H:` (найдено внешним
    ревью). `hunk_id` теперь получает `Hunk.raw_body` — сырые байты; тело
    ДЛЯ ЧЕЛОВЕКА (`Hunk.body`) при этом у обеих правок совпадает буквально
    (обе несут один и тот же `U+FFFD`) — это и есть доказательство, что
    расхождение идёт именно от перехода на `raw_body`, а не от чего-то ещё.
    """
    repo_ff = _init_hunks_repo(tmp_path / "ff")
    (repo_ff / "bad.txt").write_bytes(b"line\n")
    _commit_all(repo_ff)
    (repo_ff / "bad.txt").write_bytes(b"line\nADD\xffMORE\n")

    repo_fe = _init_hunks_repo(tmp_path / "fe")
    (repo_fe / "bad.txt").write_bytes(b"line\n")
    _commit_all(repo_fe)
    (repo_fe / "bad.txt").write_bytes(b"line\nADD\xfeMORE\n")

    monkeypatch.chdir(repo_ff)
    hunk_ff = next(h for h in enumerate_review_units.diff_hunks() if h.path == "bad.txt")
    monkeypatch.chdir(repo_fe)
    hunk_fe = next(h for h in enumerate_review_units.diff_hunks() if h.path == "bad.txt")

    # Одно и то же декодированное тело для человека — старый дефект был бы
    # НЕВИДИМ на уровне `body`, только на уровне `raw_body`/`hunk_id`.
    assert hunk_ff.body == hunk_fe.body
    assert hunk_ff.raw_body != hunk_fe.raw_body

    id_ff = enumerate_review_units.hunk_id(hunk_ff.path, hunk_ff.raw_body)
    id_fe = enumerate_review_units.hunk_id(hunk_fe.path, hunk_fe.raw_body)
    assert id_ff != id_fe


# --- BLOCKER 2: путь двоичной/metadata-секции разбирался из `diff --git` ----


def test_diff_hunks_binary_modified_at_path_containing_diff_header_separator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BLOCKER 2: двоичный файл по пути `x b/y.bin` не роняет весь перечислитель.

    Заголовок `diff --git a/x b/y.bin b/x b/y.bin` неоднозначен для разбора
    по подстроке ` b/`; у двоичной секции нет строк `+++`/`---`, на которые
    можно было бы упасть, поэтому старый код резолвил путь ИЗ этой строки,
    получал НЕВЕРНЫЙ путь и падал `git отказал: ... does not exist in
    'HEAD'` — весь `hunks` возвращал код 2, знаменатель исчезал целиком
    (воспроизведено внешним ревью буквально на этом входе). Второй, ДРУГОЙ
    изменённый файл рядом (`other.txt`) — свидетель того, что вся команда
    выживает, а не только этот один путь.
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "other.txt", "old\n")
    ambiguous_dir = repo / "x b"
    ambiguous_dir.mkdir(parents=True)
    ambiguous = ambiguous_dir / "y.bin"
    ambiguous.write_bytes(BINARY_OLD)
    _commit_all(repo)
    _write(repo, "other.txt", "new\n")
    ambiguous.write_bytes(BINARY_NEW)

    monkeypatch.chdir(repo)
    hunks = enumerate_review_units.diff_hunks()

    paths = {h.path for h in hunks}
    assert paths == {"other.txt", "x b/y.bin"}
    hunk = next(h for h in hunks if h.path == "x b/y.bin")
    assert hunk.context == "@@ binary @@"
    expected_body = (
        f"-binary {hashlib.sha256(BINARY_OLD).hexdigest()}\n"
        f"+binary {hashlib.sha256(BINARY_NEW).hexdigest()}"
    )
    assert hunk.body == expected_body


def test_diff_hunks_binary_rename_at_ambiguous_path_with_changed_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Переименование ДВОИЧНОГО файла на путь с ` b/` внутри, с изменённым содержимым.

    Второй вход замечания BLOCKER 2. Секция такой правки несёт И
    `rename from`/`rename to` (старый путь ≠ новый путь), И `Binary files …
    differ` в ОДНОЙ секции — обе стороны обязаны читаться по СВОЕМУ пути
    (старая — `git show HEAD:<старый>`, новая — рабочее дерево по <новому>),
    а не по одному общему, который `_binary_hunk` использовал раньше.
    Сходство достаточно высокое (один изменённый байт в большом файле), чтобы
    git всё ещё распознал это как переименование, а не удаление+добавление.
    """
    repo = _init_hunks_repo(tmp_path)
    base = bytes(range(256)) * 20
    d = repo / "x b"
    d.mkdir(parents=True)
    (d / "old.bin").write_bytes(base)
    _commit_all(repo)
    subprocess.run(["git", "mv", "x b/old.bin", "x b/new.bin"], cwd=repo, check=True)
    modified = bytearray(base)
    modified[100] = 0xAB
    (d / "new.bin").write_bytes(bytes(modified))

    monkeypatch.chdir(repo)
    hunks = [h for h in enumerate_review_units.diff_hunks() if h.path == "x b/new.bin"]

    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk.context == "@@ binary @@"
    expected_body = (
        f"-binary {hashlib.sha256(base).hexdigest()}\n"
        f"+binary {hashlib.sha256(bytes(modified)).hexdigest()}"
    )
    assert hunk.body == expected_body


def test_diff_hunks_mode_only_change_at_ambiguous_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Третий вход замечания: metadata-секция (смена режима) по пути с ` b/` внутри.

    Тот же класс неоднозначности на секции БЕЗ единого `@@` и БЕЗ `Binary
    files … differ` — `_metadata_only_hunk` раньше тоже получал путь из
    строки `diff --git`.
    """
    repo = _init_hunks_repo(tmp_path)
    d = repo / "x b"
    d.mkdir(parents=True)
    (d / "mode.sh").write_bytes(b"content\n")
    _commit_all(repo)
    subprocess.run(["git", "update-index", "--chmod=+x", "x b/mode.sh"], cwd=repo, check=True)

    monkeypatch.chdir(repo)
    hunks = [h for h in enumerate_review_units.diff_hunks() if h.path == "x b/mode.sh"]

    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk.context == "@@ metadata @@"
    assert hunk.body == "old mode 100644\nnew mode 100755"


# --- BLOCKER 3: пустой файл меняет `H:` от одного `git add` -----------------


def test_untracked_then_staged_empty_file_gives_the_same_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BLOCKER 3: пустой файл до и после `git add` — ОДИН и тот же `H:`.

    `git add` не правка содержимого файла — он как был пустым, так и остался.
    Документ обещает: нетронутый hunk сохраняет идентификатор. Раньше
    untracked-ветка (`@@ -0,0 +1,0 @@`, пустое тело) и staged-ветка
    (`@@ metadata @@`, тело `"new file mode 100644"`, выдуманное из строки,
    которой у untracked-файла нет) расходились и давали два разных `H:` на
    одном и том же событии (найдено внешним ревью).
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "keep.txt", "keep\n")
    _commit_all(repo)
    (repo / "e.py").write_bytes(b"")

    monkeypatch.chdir(repo)
    untracked_hunk = next(h for h in enumerate_review_units.untracked_hunks() if h.path == "e.py")
    id_untracked = enumerate_review_units.hunk_id(untracked_hunk.path, untracked_hunk.raw_body)

    subprocess.run(["git", "add", "e.py"], cwd=repo, check=True)
    staged_hunk = next(h for h in enumerate_review_units.diff_hunks() if h.path == "e.py")
    id_staged = enumerate_review_units.hunk_id(staged_hunk.path, staged_hunk.raw_body)

    assert untracked_hunk.context == staged_hunk.context == "@@ -0,0 +1,0 @@"
    assert untracked_hunk.body == staged_hunk.body == ""
    assert id_untracked == id_staged


# --- Замечание 4: `diff.renames` пользователя не форсировался ---------------


def test_diff_hunks_pure_rename_is_one_hunk_regardless_of_diff_renames_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Замечание 4: `--find-renames` форсирован — чужой `diff.renames` не влияет.

    Без форсирования один и тот же `git mv` давал ОДИН metadata-hunk на
    репозитории с `diff.renames=true` и ДВА hunk'а (удаление + добавление) на
    репозитории с `diff.renames=false` — тот же класс недетерминизма, что уже
    чинили у `core.abbrev` (найдено внешним ревью).
    """

    def make(root: Path, renames: str) -> list[enumerate_review_units.Hunk]:
        repo = _init_hunks_repo(root)
        subprocess.run(["git", "config", "diff.renames", renames], cwd=repo, check=True)
        _write(repo, "a.txt", "content\n")
        _commit_all(repo)
        subprocess.run(["git", "mv", "a.txt", "b.txt"], cwd=repo, check=True)
        monkeypatch.chdir(repo)
        return [h for h in enumerate_review_units.diff_hunks() if h.path in {"a.txt", "b.txt"}]

    hunks_true = make(tmp_path / "true", "true")
    hunks_false = make(tmp_path / "false", "false")

    assert len(hunks_true) == 1
    assert len(hunks_false) == 1
    assert hunks_true[0].context == "@@ metadata @@"
    assert hunks_false[0].context == "@@ metadata @@"
    assert hunks_true[0].body == hunks_false[0].body
    id_true = enumerate_review_units.hunk_id(hunks_true[0].path, hunks_true[0].raw_body)
    id_false = enumerate_review_units.hunk_id(hunks_false[0].path, hunks_false[0].raw_body)
    assert id_true == id_false


# --- Замечание 5: непунктовая строка дописывалась к пункту без разбора отступа --


def test_assertion_items_unindented_paragraph_ends_item_without_becoming_one() -> None:
    """Замечание 5: пункт, неотступленный абзац, ещё пункт — ДВА пункта, первый БЕЗ абзаца.

    План разрешает продолжением ТОЛЬКО отступленную строку
    (`docs/process/implementation.md`, «Схема идентификаторов»). Реализация
    раньше дописывала к последнему пункту ЛЮБУЮ непунктовую строку — отступ
    был не при чём — и врезка к задаче становилась частью текста последнего
    утверждения (найдено внешним ревью). Отказывать на таком входе тоже
    нельзя: замер по `docs/superpowers/plans/` показал живые планы с такими
    врезками (`2026-09-09-price-predicate.md`, задача 6) — неотступленный
    абзац заканчивает пункт и сам пунктом не становится, знаменатель не
    меняется.
    """
    lines = [
        "### Task 1: X",
        "**Утверждения**",
        "- first",
        "unindented paragraph",
        "- second",
    ]
    block = enumerate_review_units.task_block(lines, 1)
    items = enumerate_review_units.assertion_items(block)
    assert items == [("A1.1", "first"), ("A1.2", "second")]


# =============================================================================
# Внешняя волна, круг 2 (external-findings-round2.md): BLOCKER N1, BLOCKER N2.
# =============================================================================


def _make_type_change_repo(root: Path) -> Path:
    """Репозиторий с ОДНИМ файлом, тип которого сменён: обычный файл → симлинк.

    Реального OS-симлинка на Windows без прав может не быть — вход строится
    так, как называет замечание N1: `git update-index --add --cacheinfo
    120000,<sha>,<путь>` подменяет РЕЖИМ файла в ИНДЕКСЕ на `120000`
    (симлинк), не трогая реальные байты на диске. `git diff HEAD` (не
    `--cached`) при этом читает содержимое из РАБОЧЕГО ДЕРЕВА (оно не
    изменилось) и режим — из индекса (изменился) и печатает это КАК СМЕНУ
    ТИПА: `git diff --raw -z HEAD` даёт РОВНО ОДНУ запись со статусом `T`, а
    `git diff HEAD` — ДВЕ секции патча (удаление старого типа, добавление
    нового) для ОДНОГО и того же пути. Это и есть вход, на котором ложный
    инвариант «секций ровно столько, сколько записей» (BLOCKER N1) рвался.
    """
    repo = _init_hunks_repo(root)
    _write(repo, "f.txt", "hello world content here\n")
    _commit_all(repo)
    target_sha = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo, input="target.txt", capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo", f"120000,{target_sha},f.txt"],
        cwd=repo, check=True,
    )
    return repo


def test_diff_hunks_type_change_gives_two_hunks_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BLOCKER N1: смена ТИПА файла (обычный → симлинк, режим `120000`) — два hunk'а, не отказ.

    До этой правки `diff_hunks` сопоставляла секции `git diff HEAD` и записи
    `git diff --raw -z HEAD` ПОЗИЦИОННО (`zip(bounds, records, strict=True)`)
    и падала на расхождении их числа (`RuntimeError`, не пойманным
    `_run_hunks`, — вся команда роняла трассировку и терялся ВЕСЬ
    знаменатель, воспроизведено оркестратором буквально: `rc=1, строк 0`).
    Здесь секций для `f.txt` — ДВЕ (`git diff HEAD` рендерит смену типа как
    удаление старого + добавление нового), а запись `--raw` для него — ОДНА
    со статусом `T`: старый код увидел бы 1 != N где-то ещё в этом дереве и
    упал бы уже на первом же прогоне с несовпадением. Обе секции здесь —
    ТЕКСТОВЫЕ (есть `+++`/`---`, `core.symlinks=false` заставляет git
    показать содержимое симлинка как текст), и путь для ОБЕИХ — один и тот
    же, путь ЕДИНСТВЕННОЙ записи `T`: правило «`T` владеет двумя смежными
    секциями» живёт в `_sections_per_record`, а раздачу делает
    `_assign_sections`. Сломай это правило (отдай `T` одну секцию) — и этот
    вход краснеет громким отказом, а не тихой потерей элемента.
    """
    repo = _make_type_change_repo(tmp_path)

    monkeypatch.chdir(repo)
    hunks = [h for h in enumerate_review_units.diff_hunks() if h.path == "f.txt"]

    assert len(hunks) == 2
    bodies = {h.body for h in hunks}
    assert bodies == {"-hello world content here", "+hello world content here"}
    counts = {(h.added, h.removed) for h in hunks}
    assert counts == {(0, 1), (1, 0)}


def test_cli_hunks_type_change_does_not_crash_the_whole_run(tmp_path: Path) -> None:
    """BLOCKER N1 на уровне CLI: `hunks` не отказывает кодом 1 с трассировкой на смене типа файла.

    Побочный, но обязательный по условию приёмки эффект: рядом с f.txt в том
    же дереве стоит ДРУГОЙ, не менявшийся файл (`_init_hunks_repo` сам ничего
    не коммитит сверх того, что попросили, поэтому здесь его не нужно
    заводить отдельно) — важно само отсутствие трассировки и кода `1`.
    """
    repo = _make_type_change_repo(tmp_path)

    result = _cli_hunks_text(repo)

    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    assert result.stderr == ""
    rows = [line.split("\t") for line in result.stdout.splitlines() if line]
    f_txt_ids = [row[0] for row in rows if row[1] == "f.txt"]
    assert len(f_txt_ids) == 2
    assert len(set(f_txt_ids)) == 2  # разные тела — разные `H:`, не дубль


def test_run_hunks_generic_exception_is_a_clean_message_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """BLOCKER N1 + Important I2: ЛЮБОЕ исключение перечисления — сообщение с ТИПОМ и код 3, не traceback.

    До круга 2 `_run_hunks` ловил только `subprocess.CalledProcessError` —
    любое другое исключение выходило трассировкой. Круг 3 (Important I2)
    добавил требование к САМОМУ сообщению: голый `str(error)` неотличим от
    законного отказа git — внедрённая программная ошибка выглядела бы как
    обычное сообщение (`'str' object has no attribute 'decode'`), а не как
    сигнал «сломался сам инструмент». Здесь `diff_hunks` подменена так, чтобы
    бросить произвольный `ValueError` (не обязательно тот же путь, которым
    падал реальный N1, — важно само свойство «функция это ловит и называет
    тип», а не воспроизведение сценария целиком, которое уже отдельно
    доказано `test_cli_hunks_type_change_does_not_crash_the_whole_run`).
    """

    def boom() -> list[enumerate_review_units.Hunk]:
        raise ValueError("нарочная поломка перечисления для теста")

    monkeypatch.setattr(enumerate_review_units, "diff_hunks", boom)

    code = enumerate_review_units._run_hunks()
    captured = capsys.readouterr()

    assert code == 3
    assert captured.out == ""
    assert "Traceback" not in captured.err
    assert "ValueError" in captured.err
    assert "нарочная поломка перечисления для теста" in captured.err


def test_untracked_then_staged_trailing_nbsp_file_gives_the_same_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BLOCKER N2: хвостовой NBSP (`U+00A0`) — untracked и staged дают ОДИН и тот же `H:`.

    До правки `body` резался `str.rstrip()` (снимает ЛЮБОЙ юникодный
    пробел — NBSP в том числе), а `raw_body` — `bytes.rstrip()` (только
    ASCII) — на файле БЕЗ единого изменённого байта `git add` менял `H:`
    (тот же класс, что BLOCKER 3, но в общем виде, не только для пустого
    файла). Второй вход из замечания — id, посчитанный ЧЕРЕЗ показанное тело
    (`hunk.body`), обязан совпасть с id через хэшируемое (`hunk.raw_body`):
    до правки они были РАЗНЫМИ представлениями одного события и это
    расхождение пришлось бы проверять отдельно.
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "keep.txt", "keep\n")
    _commit_all(repo)
    content = "alpha \nbeta\n"
    (repo / "n.txt").write_bytes(content.encode("utf-8"))

    monkeypatch.chdir(repo)
    untracked_hunk = next(h for h in enumerate_review_units.untracked_hunks() if h.path == "n.txt")
    id_untracked = enumerate_review_units.hunk_id(untracked_hunk.path, untracked_hunk.raw_body)

    subprocess.run(["git", "add", "n.txt"], cwd=repo, check=True)
    staged_hunk = next(h for h in enumerate_review_units.diff_hunks() if h.path == "n.txt")
    id_staged = enumerate_review_units.hunk_id(staged_hunk.path, staged_hunk.raw_body)

    assert " " in untracked_hunk.body  # NBSP — не ASCII-пробел, остаётся значащим
    assert untracked_hunk.body == staged_hunk.body
    assert untracked_hunk.raw_body == staged_hunk.raw_body
    assert id_untracked == id_staged

    # Второй вход замечания: id по НАПЕЧАТАННОМУ (показанному) телу совпадает
    # с id по хэшируемому — расхождение здесь и было бы BLOCKER N2.
    id_from_displayed_body = enumerate_review_units.hunk_id(staged_hunk.path, staged_hunk.body)
    assert id_from_displayed_body == id_staged


# =============================================================================
# Внешняя волна, круг 3 (external-findings-round3.md): пути НИКОГДА не читаются
# из текста патча — `git diff --raw -z HEAD` их единственный источник. Способ
# ЗАБРАТЬ патч круг 3 выбрал неверно (отдельный вызов `git diff HEAD -- <путь>`
# на файл, то есть путь как pathspec) и круг 4 его заменил одним патчем на всё
# дерево; входы ниже от этого не устарели — они про ПУТИ, а не про способ.
# =============================================================================


def test_diff_hunks_mutually_ambiguous_renames_give_two_distinct_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Замер круга 3: два переименования с ПОБУКВЕННО ОДИНАКОВЫМ заголовком секции.

    `a` → `"c b/d"` и `"a b/c"` → `d` — оба дают у `git diff HEAD` СТРОКУ
    `diff --git a/a b/c b/d`, слово в слово одинаковую: в заголовке физически
    нет информации, которая отличала бы одну правку от другой (M2 —
    построено и проверено оркестратором лично). Никакой разбор ЭТОГО текста
    не может дать верный ответ — не потому что разбор недостаточно умный, а
    потому что ответа в этом тексте нет. До круга 3 резолвер молча брал
    ПЕРВЫЙ подходящий разрез заголовка: один путь исчезал из знаменателя,
    другой дублировался, `rc=0`, `stderr` пуст — тихая порча знаменателя,
    один из самых опасных исходов для этого инструмента (хуже честного
    отказа). После круга 3 путь приходит не из этого текста вовсе, а из
    `git diff --raw -z HEAD` (`_raw_diff_records`) — заголовок текста патча
    для пути больше не читается нигде, и обе секции корректно разрешаются в
    СВОИ, РАЗНЫЕ пути.
    """
    repo = _init_hunks_repo(tmp_path)
    (repo / "a").write_bytes(b"content-a\n")
    ab_dir = repo / "a b"
    ab_dir.mkdir()
    (ab_dir / "c").write_bytes(b"content-abc\n")
    _write(repo, "plain.txt", "plain old\n")
    _commit_all(repo)

    cb_dir = repo / "c b"
    cb_dir.mkdir()
    subprocess.run(["git", "mv", "a", "c b/d"], cwd=repo, check=True)
    subprocess.run(["git", "mv", "a b/c", "d"], cwd=repo, check=True)
    _write(repo, "plain.txt", "plain new\n")

    # Свидетель: заголовки секций ДЕЙСТВИТЕЛЬНО побуквенно совпадают —
    # без этого свидетеля тест мог бы молча перестать проверять то, что
    # заявляет (найденное дерево перестало бы быть враждебным).
    raw_diff = subprocess.run(
        ["git", "-c", "core.quotepath=false", "diff", "HEAD", "--no-color", "--find-renames"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    headers = [line for line in raw_diff.splitlines() if line.startswith("diff --git")]
    assert headers.count("diff --git a/a b/c b/d") == 2

    monkeypatch.chdir(repo)
    hunks = enumerate_review_units.diff_hunks()

    paths = sorted(h.path for h in hunks if h.path in {"c b/d", "d"})
    assert paths == ["c b/d", "d"]
    rename_cb_d = next(h for h in hunks if h.path == "c b/d")
    rename_d = next(h for h in hunks if h.path == "d")
    assert rename_cb_d.body == "similarity index 100%\nrename from a\nrename to c b/d"
    assert rename_d.body == "similarity index 100%\nrename from a b/c\nrename to d"
    assert any(h.path == "plain.txt" for h in hunks)


def _build_quote_path_repo(root: Path, blob_content: bytes, quoted_path: str) -> Path:
    """Репозиторий, где `HEAD` несёт файл с ЛИТЕРАЛЬНОЙ кавычкой в имени — БЕЗ ЕГО checkout'а.

    Windows не позволяет создать файл с `"` в имени НИКАКИМ штатным
    способом (Win32 API отвергает этот символ) — ни через Python, ни через
    `git checkout`/`git add`, которые сами используют тот же API (проверено:
    `git update-index --add --cacheinfo` на таком пути отвечает `fatal: git
    update-index: --cacheinfo cannot add a"b.txt`). Дерево строится ЦЕЛИКОМ
    через git-плампинг, который никогда не трогает рабочую директорию:
    `git hash-object -w --stdin` кладёт блоб в object database без файла на
    диске, `git mktree` строит дерево из произвольных имён (включая
    кавычку) БЕЗ проверки, что путь допустим для текущей ОС, `git
    commit-tree` строит коммит из дерева, а `git update-ref HEAD` наводит на
    него HEAD — ни один из этих шагов не читает и не пишет рабочую
    директорию. Текущий индекс/дерево остаются ПУСТЫМИ для этого пути (в
    этом свежем репозитории ничего, кроме `keep.txt`, никогда не
    добавлялось) — поэтому `git diff HEAD` видит файл как УДАЛЁННЫЙ
    (в `HEAD` есть, в текущем состоянии — нет), что для целей этого теста
    ничем не хуже правки: он проходит ТОЧНО ТУ ЖЕ цепочку («`--raw -z` даёт
    путь → секция из общего патча достаётся этой записи по порядку → тело
    разобрано по виду секции»), что и любая другая правка, и вообще не
    касается частей кода, зависящих от того, добавлен файл или удалён. На CI (`ubuntu-latest`) такое имя —
    обычный файл, и там достаточно было бы обычного `_write`+`_commit_all`;
    здесь используется техника, которая работает на ОБЕИХ платформах
    одинаково, поэтому применена без ветвления по ОС.
    """
    repo = _init_hunks_repo(root)
    quoted_blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"], cwd=repo, input=blob_content, capture_output=True, check=True,
    ).stdout.strip().decode()
    (repo / "keep.txt").write_bytes(b"keep\n")
    keep_blob = subprocess.run(
        ["git", "hash-object", "-w", str(repo / "keep.txt")], cwd=repo, capture_output=True, check=True,
    ).stdout.strip().decode()
    mktree_input = f"100644 blob {quoted_blob}\t{quoted_path}\n100644 blob {keep_blob}\tkeep.txt\n".encode()
    tree = subprocess.run(["git", "mktree"], cwd=repo, input=mktree_input, capture_output=True, check=True).stdout.strip().decode()
    commit = subprocess.run(["git", "commit-tree", tree, "-m", "init"], cwd=repo, capture_output=True, check=True).stdout.strip().decode()
    subprocess.run(["git", "update-ref", "HEAD", commit], cwd=repo, check=True)
    # `keep.txt` РЕАЛЬНО существует на диске и добавляется штатно — он здесь
    # свидетель, что обычные файлы рядом с враждебным путём не страдают.
    subprocess.run(["git", "add", "keep.txt"], cwd=repo, check=True)
    return repo


def test_diff_hunks_quoted_path_text_file_resolves_to_the_exact_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Путь с кавычкой (`a"b.txt`) — ТЕКСТОВАЯ секция, путь верный, не `/a\\"b.txt"`.

    До круга 3 путь такой секции читался из заголовка `diff --git
    "a/a\\"b.txt" "b/a\\"b.txt"` — git ВСЕГДА заворачивает путь с
    embedded-кавычкой в кавычки и экранирует её (`\\"`) НЕЗАВИСИМО от
    `core.quotepath` (эта настройка гасит только octal-escape не-ASCII
    байт, а не необходимость экранировать саму кавычку), и наивная резка по
    фиксированному префиксу `"+++ b/"` возвращала БЫ `/a\\"b.txt"` —
    путь, которого не существует (M1). `git diff --raw -z` эту секцию ВООБЩЕ
    не экранирует — путь приходит верным по построению.
    """
    repo = _build_quote_path_repo(tmp_path, b"line one\nline two\n", 'a"b.txt')

    monkeypatch.chdir(repo)
    hunks = enumerate_review_units.diff_hunks()

    hunk = next(h for h in hunks if h.path == 'a"b.txt')
    assert hunk.body == "-line one\n-line two"
    assert (hunk.added, hunk.removed) == (0, 2)
    assert not any(h.path != 'a"b.txt' and "keep" not in h.path for h in hunks)


def test_diff_hunks_quoted_path_binary_file_resolves_to_the_exact_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """То же имя (`a"c.bin`) для ДВОИЧНОГО файла — путь верный, не отказ кодом 3 с пустым выводом.

    Второй, отдельный вход замечания: у двоичной секции нет `+++`/`---`
    вовсе, и до круга 3 путь для неё разрешался ЕЩЁ более хрупким способом
    (перебор разрезов заголовка по множеству известных пар) — на этом же
    имени старый резолвер бросал `ValueError` («секция не начинается с …»,
    воспроизведено оркестратором до этой правки), что при единственном
    изменённом файле в дереве превращало ВЕСЬ прогон в `rc=3` с пустым
    выводом.
    """
    content = b"\x00\x01BINARYDATA"
    repo = _build_quote_path_repo(tmp_path, content, 'a"c.bin')

    monkeypatch.chdir(repo)
    hunks = enumerate_review_units.diff_hunks()

    hunk = next(h for h in hunks if h.path == 'a"c.bin')
    assert hunk.context == "@@ binary @@"
    assert hunk.body == f"-binary {hashlib.sha256(content).hexdigest()}"
    assert (hunk.added, hunk.removed) == (0, 1)


def test_cli_hunks_quoted_path_does_not_crash_the_whole_run(tmp_path: Path) -> None:
    """Путь с кавычкой на уровне CLI: `rc=0`, никакой трассировки, никакого пустого отказа.

    Прямое предъявление формулировки замечания «не `rc=3` с пустым выводом» —
    в подпроцессе, той же командой, которой пользуется ревьюер.
    """
    repo = _build_quote_path_repo(tmp_path, b"\x00\x01BINARYDATA", 'a"c.bin')

    result = _cli_hunks_text(repo)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    rows = [line.split("\t") for line in result.stdout.splitlines() if line]
    assert any(row[1] == 'a"c.bin' for row in rows)


# =============================================================================
# Внешняя волна, круг 4 (external-findings-round4.md): pathspec — это ШАБЛОН,
# а не имя файла. Круг 3 передавал путь записи `--raw` обратно git'у как
# pathspec, и ТОТ ЖЕ класс дефекта (элемент знаменателя достался не тому файлу
# либо пропал МОЛЧА, с кодом 0) переехал в новый механизм. Круг 4 убирает
# pathspec вовсе: ОДИН патч на всё дерево плюс ОДНА листовка `--raw -z`,
# секции раздаются записям ПО ПОРЯДКУ (`_assign_sections`), `T` владеет двумя.
# =============================================================================


def _build_tree_with_subdirectory(root: Path) -> Path:
    """Дерево из трёх отслеживаемых правок в РАЗНЫХ каталогах плюс неотслеживаемый файл.

    Форма входа BLOCKER P1 буквально: правки лежат и в `backend/`, и в
    `docs/`, и в корне, а документированная команда ревьюера начинается с
    `cd backend` — то есть исполняется ИЗ ПОДКАТАЛОГА. Неотслеживаемый файл
    добавлен нарочно: `git status --porcelain` всегда печатает путь от корня,
    поэтому на круге 3 он ОДИН и уцелевал, маскируя потерю остальных.
    """
    repo = _init_hunks_repo(root)
    _write(repo, "backend/money.py", "a\n")
    _write(repo, "docs/note.md", "b\n")
    _write(repo, "root.txt", "c\n")
    _commit_all(repo)
    _write(repo, "backend/money.py", "aX\n")
    _write(repo, "docs/note.md", "bX\n")
    _write(repo, "root.txt", "cX\n")
    _write(repo, "backend/untracked.py", "u\n")
    return repo


def test_cli_hunks_from_a_subdirectory_gives_the_same_output_as_from_the_root(tmp_path: Path) -> None:
    """BLOCKER P1: документированный `cd backend && … hunks` обязан дать ТОТ ЖЕ знаменатель.

    Пути записей `git diff --raw` — от КОРНЯ репозитория, а pathspec
    разрешается от ТЕКУЩЕГО каталога. Круг 3 передавал первое как второе, и
    из `backend/` три отслеживаемых файла из четырёх исчезали МОЛЧА, с кодом
    возврата 0 и пустым `stderr` (воспроизведено оркестратором: `rows=4` из
    корня против `rows=1` из `backend/`, причём уцелевшая строка — как раз
    неотслеживаемый файл, который идёт мимо `diff`).

    Сравниваются ПОБАЙТНО оба вывода целиком, а не только их длина: сдвиг
    привязки, сохранивший число строк, тоже обязан краснеть.
    """
    repo = _build_tree_with_subdirectory(tmp_path)

    from_root = _cli_hunks_bytes(repo)
    from_subdir = _cli_hunks_bytes(repo / "backend")

    assert from_root.returncode == 0, from_root.stderr
    assert from_subdir.returncode == 0, from_subdir.stderr
    assert from_subdir.stdout == from_root.stdout
    rows = [line.split("\t") for line in from_root.stdout.decode("utf-8").splitlines() if line]
    assert [row[1] for row in rows] == [
        "backend/money.py",
        "backend/untracked.py",
        "docs/note.md",
        "root.txt",
    ]


def test_cli_hunks_from_a_subdirectory_survives_diff_relative_config(tmp_path: Path) -> None:
    """Та же потеря знаменателя другим путём: пользовательский `diff.relative=true`.

    Убрать pathspec — необходимо, но НЕ достаточно: при `diff.relative=true`
    git и в патче, и в `--raw` показывает ТОЛЬКО поддерево текущего каталога,
    и прогон из `backend/` снова терял бы всё остальное. Замерено на дереве с
    девятью записями: из подкаталога `diff.relative=true` оставляет ОДНУ
    запись в `--raw` и одну секцию в патче. Стережёт это `--no-relative`, и
    он обязан стоять на ОБЕИХ командах — эта проверка красна, если его снять
    с любой из них.
    """
    repo = _build_tree_with_subdirectory(tmp_path)
    subprocess.run(["git", "config", "diff.relative", "true"], cwd=repo, check=True)

    from_root = _cli_hunks_bytes(repo)
    from_subdir = _cli_hunks_bytes(repo / "backend")

    assert from_root.returncode == 0, from_root.stderr
    assert from_subdir.returncode == 0, from_subdir.stderr
    assert from_subdir.stdout == from_root.stdout
    rows = [line.split("\t") for line in from_root.stdout.decode("utf-8").splitlines() if line]
    assert [row[1] for row in rows] == [
        "backend/money.py",
        "backend/untracked.py",
        "docs/note.md",
        "root.txt",
    ]


def test_diff_hunks_glob_magic_in_a_plain_filename_does_not_bind_a_foreign_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BLOCKER P2: `a[bc]d.txt` рядом с `abd.txt` — ДВА элемента, и тела не перепутаны.

    Имена обычные — никакой «магии» в них нет, это ровно форма маршрута
    JS/TS `[id].tsx`, и Windows такие файлы создаёт. Но pathspec трактует
    `[bc]` как класс символов, и `a[bc]d.txt` как ШАБЛОН совпадает ещё и с
    `abd.txt` (а также с `acd.txt`). Круг 3 давал на два файла ТРИ строки:
    путь `a[bc]d.txt` дважды, и под одним из них лежало тело ЧУЖОГО файла
    `abd.txt` (воспроизведено оркестратором буквально).

    Проверяется не только счёт, но и СОСТАВ: тело каждого пути сверено со
    своим — счёт сошёлся бы и при перепутанных телах.
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "a[bc]d.txt", "one\n")
    _write(repo, "abd.txt", "two\n")
    _commit_all(repo)
    _write(repo, "a[bc]d.txt", "oneX\n")
    _write(repo, "abd.txt", "twoX\n")

    monkeypatch.chdir(repo)
    hunks = enumerate_review_units.diff_hunks()

    assert sorted(h.path for h in hunks) == ["a[bc]d.txt", "abd.txt"]
    bodies = {h.path: h.body for h in hunks}
    assert bodies["a[bc]d.txt"] == "-one\n+oneX"
    assert bodies["abd.txt"] == "-two\n+twoX"


def test_diff_hunks_directory_replaced_by_a_file_does_not_pull_in_the_subtree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BLOCKER P3: каталог `pkg/` заменён ФАЙЛОМ `pkg` — элементов ровно столько, сколько записей.

    Имена здесь совсем обычные, символов шаблона нет вовсе: дефект даёт САМА
    СЕМАНТИКА pathspec — путь, совпавший с каталогом, забирает ВСЁ его
    поддерево, и `--literal-pathspecs` этого не чинит (рекурсия по каталогу
    не магия шаблона). Круг 3 на ЧЕТЫРЁХ записях `--raw` печатал ШЕСТЬ
    элементов: тела `pkg/a.txt` и `pkg/b.txt` доставались ещё и пути `pkg`
    (воспроизведено оркестратором: `H:5bf1c15a | pkg | '-x'` рядом с
    `H:08b495c4 | pkg/a.txt | '-x'`).

    `git add -A` обязателен: без него новый файл `pkg` остаётся
    неотслеживаемым и в `git diff --raw` не попадает вовсе — тогда записи,
    чей pathspec совпадёт с каталогом, просто не будет, и вход перестанет
    предъявлять дефект.
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "pkg/a.txt", "x\n")
    _write(repo, "pkg/b.txt", "y\n")
    _write(repo, "other.txt", "z\n")
    _commit_all(repo)
    shutil.rmtree(repo / "pkg")
    _write(repo, "pkg", "now a file\n")
    _write(repo, "other.txt", "zX\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)

    monkeypatch.chdir(repo)
    records = enumerate_review_units._raw_diff_records()
    hunks = enumerate_review_units.diff_hunks()

    assert sorted(record[2] for record in records) == ["other.txt", "pkg", "pkg/a.txt", "pkg/b.txt"]
    assert len(hunks) == len(records)
    bodies = {h.path: h.body for h in hunks}
    assert sorted(bodies) == ["other.txt", "pkg", "pkg/a.txt", "pkg/b.txt"]
    assert bodies["pkg"] == "+now a file"
    assert bodies["pkg/a.txt"] == "-x"
    assert bodies["pkg/b.txt"] == "-y"


def test_sections_per_record_gives_two_sections_to_a_type_change_and_one_to_everything_else() -> None:
    """Правило `T` предъявлено ПРЯМО, а не только через свои последствия.

    Смена ТИПА файла — единственный измеренный статус, у которого записей и
    секций РАЗНОЕ число: одна запись `--raw` со статусом `T`, две смежные
    секции патча (unified-diff рендерит смену типа как удаление старого типа
    плюс добавление нового). Измерено на трёх формах: обычный → симлинк,
    симлинк → обычный и смена типа при ПОБАЙТНО одинаковом блобе.

    Остальные статусы меряны на дереве с восемью статусами разом — у каждого
    ровно одна секция; `R` проверяется с баллом сходства (`R100`), потому что
    сравнение идёт по первому символу.
    """
    assert enumerate_review_units._sections_per_record("T") == 2
    for status in ("M", "A", "D", "R100", "C75", ""):
        assert enumerate_review_units._sections_per_record(status) == 1, status


def test_assign_sections_distributes_in_order_and_gives_the_type_change_both_sections() -> None:
    """Раздача ПО ПОРЯДКУ: каждой записи — её секции, `T` — две смежные.

    Порядок записей `--raw` и секций патча совпадает элемент в элемент — это
    ИЗМЕРЕНО (дерево с восемью статусами разом), и здесь проверяется, что код
    раздаёт именно так: запись `T` забирает ДВЕ смежные секции, соседние
    записи получают свои, а не сдвинутые на одну.
    """
    records = [("M", "a.txt", "a.txt"), ("T", "t.txt", "t.txt"), ("D", "z.txt", "z.txt")]
    bounds = [(0, 5), (5, 9), (9, 13), (13, 20)]

    assignments = enumerate_review_units._assign_sections(records, bounds)

    assert assignments == [
        (("M", "a.txt", "a.txt"), [(0, 5)]),
        (("T", "t.txt", "t.txt"), [(5, 9), (9, 13)]),
        (("D", "z.txt", "z.txt"), [(13, 20)]),
    ]


def test_assign_sections_refuses_loudly_when_the_counts_disagree() -> None:
    """Расхождение сумм — ГРОМКИЙ отказ с числами и статусами, а не сдвиг привязки.

    Кардинальный грех этого инструмента — приписать правку не тому файлу или
    потерять её МОЛЧА, с кодом 0. Если у неизмеренного статуса вдруг окажется
    не то число секций, сумма не сойдётся — и тогда единственный допустимый
    исход это отказ, называющий число записей, число секций и ВСЕ статусы,
    чтобы случай можно было воспроизвести и измерить. Догадка запрещена даже
    тогда, когда «очевидно», какая секция чья.
    """
    records = [("M", "a.txt", "a.txt"), ("M", "b.txt", "b.txt")]
    bounds = [(0, 5), (5, 9), (9, 13)]

    with pytest.raises(RuntimeError) as excinfo:
        enumerate_review_units._assign_sections(records, bounds)

    message = str(excinfo.value)
    assert "2" in message and "3" in message
    assert "M, M" in message


def test_run_hunks_turns_the_assignment_refusal_into_a_message_and_code_3(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Громкий отказ доходит до пользователя сообщением и кодом 3, а не трассировкой.

    Отказ `_assign_sections` — это `RuntimeError` из `diff_hunks()`; общая
    сеть `except Exception` в `_run_hunks` обязана превратить его в строку
    `stderr` с ИМЕНЕМ типа и код возврата `3`, при ПУСТОМ `stdout`: частичный
    знаменатель хуже отсутствующего, потому что выглядит полным.
    """
    def boom() -> list[object]:
        raise RuntimeError("перечисление невозможно: записей 2, секций 3")

    monkeypatch.setattr(enumerate_review_units, "diff_hunks", boom)

    code = enumerate_review_units._run_hunks()

    captured = capsys.readouterr()
    assert code == 3
    assert captured.out == ""
    assert "RuntimeError" in captured.err
    assert "перечисление невозможно" in captured.err
    assert "Traceback" not in captured.err


# =============================================================================
# Внешняя волна, круг 5 (external-findings-round5.md): последняя настройка,
# менявшая ЧИСЛО секций на запись. `diff.submodule` рисует подмодуль тремя
# разными способами, и при значении `diff` счёт СХОДИТСЯ, а привязка — нет:
# под путём первого подмодуля печатается тело файла из второго, rc=0.
# Рядом — `diff.ignoreSubmodules`, которая выкидывает подмодуль из ОБОИХ
# выводов сразу: отказа нет, но элемент знаменателя исчезает молча.
# =============================================================================


def _build_two_submodules_repo(root: Path) -> tuple[Path, dict[str, tuple[str, str]]]:
    """Дерево с ДВУМЯ подмодулями: у первого внутренний diff ПУСТ, у второго — два файла.

    Асимметрия здесь и есть вход: при `diff.submodule=diff` git разворачивает
    ВНУТРЕННИЙ diff каждого подмодуля вместо строки `Subproject commit`. У
    первого подмодуля указатель сдвинут ПУСТЫМ коммитом — внутренний diff
    пуст, и секций он не даёт НИ ОДНОЙ; второй меняет два файла и даёт ДВЕ.
    Итого две записи `--raw` и две секции — суммы сходятся, громкий отказ не
    срабатывает, и раздача по порядку отдаёт первую секцию (тело `s2/a.txt`)
    записи `s1`. Симметричное дерево (по одному файлу в каждом подмодуле) это
    НЕ предъявляет: там 2 записи и 2 секции разошлись бы только телами, а
    здесь путь получает содержимое ЧУЖОГО подмодуля.

    Возвращает репозиторий и словарь `путь → (старый sha, новый sha)` — по
    ним тест сверяет, что тело каждого элемента принадлежит СВОЕМУ подмодулю,
    а не только что элементов двое.

    `protocol.file.allow=always` обязателен: git по умолчанию запрещает
    `file://`-транспорт для подмодулей (CVE-2022-39253), и без него
    `submodule add` отказывает.
    """
    inner_specs = {"s1": ["f.txt"], "s2": ["a.txt", "b.txt"]}
    for name, files in inner_specs.items():
        inner = _init_hunks_repo(root / name)
        for index, rel in enumerate(files):
            _write(inner, rel, f"{name}-{index}\n")
        _commit_all(inner)

    repo = _init_hunks_repo(root / "main")
    for name in inner_specs:
        subprocess.run(
            [
                "git", "-c", "protocol.file.allow=always", "submodule", "add", "-q",
                (root / name).as_uri(), name,
            ],
            cwd=repo, check=True,
        )
    _write(repo, "top.txt", "top\n")
    _commit_all(repo)

    shas: dict[str, tuple[str, str]] = {}
    for name in inner_specs:
        work = repo / name
        old = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=work, capture_output=True, text=True, check=True
        ).stdout.strip()
        if name == "s1":
            # Пустой коммит: указатель сдвинулся, внутренний diff ПУСТ.
            subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "move"], cwd=work, check=True)
        else:
            for index, rel in enumerate(inner_specs[name]):
                _write(work, rel, f"{name}-{index}-changed\n")
            subprocess.run(["git", "commit", "-qam", "two files"], cwd=work, check=True)
        new = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=work, capture_output=True, text=True, check=True
        ).stdout.strip()
        shas[name] = (old, new)
    return repo, shas


def _assert_each_submodule_carries_its_own_pointer(
    hunks: list[enumerate_review_units.Hunk], shas: dict[str, tuple[str, str]]
) -> None:
    """Ровно два элемента, и тело каждого — сдвиг указателя СВОЕГО подмодуля.

    Счёт проверять мало: при `diff.submodule=diff` счёт как раз сходится, а
    тело `s1` несёт содержимое `s2/a.txt`. Сверка идёт с ПОЛНЫМИ sha, взятыми
    у самих подмодулей (`git rev-parse`), а не с выводом инструмента — это
    внешний оракул, а не сверщик, делящий предикат с генератором. Полные
    40 hex здесь законны: строка `Subproject commit` печатает sha целиком при
    любом `core.abbrev` (проверено на значениях 4, 12 и 40), в отличие от
    строки `index …`, которую тело metadata-hunk'а поэтому и не включает.
    """
    by_path = {hunk.path: hunk for hunk in hunks if hunk.path in shas}
    assert sorted(by_path) == ["s1", "s2"]
    for name, (old, new) in shas.items():
        assert by_path[name].body == f"-Subproject commit {old}\n+Subproject commit {new}", name


def test_diff_hunks_submodule_diff_rendering_does_not_bind_a_foreign_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BLOCKER S1: `diff.submodule=diff` — счёт сходится, а тело достаётся ЧУЖОМУ пути.

    Самый опасный из трёх видов рендеринга: 2 записи и 2 секции, поэтому
    `_assign_sections` НЕ отказывает, и раздача по порядку молча отдаёт
    секцию `s2/a.txt` записи `s1`. Воспроизведено оркестратором на коде
    круга 4: `s1` получал тело `-a\\n+aX`, `s2` — `-b\\n+bX`, `rc=0`, `stderr`
    пуст. Стережёт это `--submodule=short` на команде патча: он заставляет git
    печатать одну строку `Subproject commit` на подмодуль при ЛЮБОМ значении
    настройки.
    """
    repo, shas = _build_two_submodules_repo(tmp_path)
    subprocess.run(["git", "config", "diff.submodule", "diff"], cwd=repo, check=True)

    monkeypatch.chdir(repo)
    _assert_each_submodule_carries_its_own_pointer(enumerate_review_units.diff_hunks(), shas)


def test_diff_hunks_submodule_log_rendering_still_yields_both_elements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BLOCKER S1, второй вид: `diff.submodule=log` — секций НОЛЬ, весь знаменатель терялся.

    `log` рендерит подмодуль списком коммитов, БЕЗ единой строки `diff --git `
    — 2 записи против 0 секций, суммы не сходятся, и круг 4 отвечал громким
    отказом `rc=3`. Отказ честнее молчания, но знаменатель всё равно
    отсутствует, а причина — чужой конфиг, а не дерево. С `--submodule=short`
    оба элемента на месте и несут свои указатели.
    """
    repo, shas = _build_two_submodules_repo(tmp_path)
    subprocess.run(["git", "config", "diff.submodule", "log"], cwd=repo, check=True)

    monkeypatch.chdir(repo)
    _assert_each_submodule_carries_its_own_pointer(enumerate_review_units.diff_hunks(), shas)


def test_diff_hunks_ignore_submodules_config_does_not_drop_the_pointer_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Вторая половина того же греха: `diff.ignoreSubmodules=all` — элемента НЕТ ВОВСЕ.

    Найдено при замере круга 5 сверх текста замечаний. Эта настройка меняет
    не рендеринг, а СОСТАВ: подмодуль исчезает СРАЗУ из обоих выводов (0
    записей и 0 секций вместо 2 и 2), поэтому суммы сходятся, отказ не
    срабатывает — и сдвиг указателя подмодуля просто не попадает в
    знаменатель ревью, с кодом 0 и пустым `stderr`. Не «правка не тому
    файлу», а «правки нет вовсе» — тот же кардинальный грех другой стороной.
    Стережёт `--ignore-submodules=none`, и он обязан стоять на ОБЕИХ
    командах: на одной он вернул бы расхождение сумм вместо верного ответа.

    `submodule.<имя>.ignore=all` — та же настройка, заданная поимённо, и
    проверяется здесь же: она выбивает ОДИН подмодуль из двух, то есть
    меняет знаменатель тише, чем `diff.ignoreSubmodules`, и потому опаснее.
    """
    repo, shas = _build_two_submodules_repo(tmp_path)
    monkeypatch.chdir(repo)

    subprocess.run(["git", "config", "diff.ignoreSubmodules", "all"], cwd=repo, check=True)
    _assert_each_submodule_carries_its_own_pointer(enumerate_review_units.diff_hunks(), shas)

    subprocess.run(["git", "config", "--unset", "diff.ignoreSubmodules"], cwd=repo, check=True)
    subprocess.run(["git", "config", "submodule.s1.ignore", "all"], cwd=repo, check=True)
    _assert_each_submodule_carries_its_own_pointer(enumerate_review_units.diff_hunks(), shas)


def test_assignment_breakpoint_names_a_path_for_each_kind_of_disagreement() -> None:
    """I3: сообщение отказа обязано назвать ПУТЬ, а не только `M, M, M, …`.

    На дереве из ста записей список статусов не локатор: воспроизводить по
    нему нечего. Проверяются все три ветви — секций не хватило (путь ПЕРВОЙ
    обделённой записи, а не последней сошедшейся), секций больше нужного
    (путь последней записи, после которой остался хвост) и вырожденный случай
    «записей нет вовсе».
    """
    records = [("M", "a.txt", "a.txt"), ("T", "t.txt", "t.txt"), ("D", "z.txt", "z.txt")]

    # Записи требуют 1+2+1=4 секции; их 2 — обделена запись `t.txt`, не `z.txt`.
    short = enumerate_review_units._assignment_breakpoint(records, 2)
    assert "t.txt" in short and "z.txt" not in short and "T" in short

    # Секций 5 при нужных 4 — хвост после последней записи.
    surplus = enumerate_review_units._assignment_breakpoint(records, 5)
    assert "z.txt" in surplus

    assert "3" in enumerate_review_units._assignment_breakpoint([], 3)


def test_assign_sections_refusal_message_carries_the_breaking_path() -> None:
    """Тот же локатор — в самом сообщении `RuntimeError`, а не только в хелпере.

    Проверка на уровне, где дефект живёт для ЧИТАТЕЛЯ: человек видит текст
    исключения, а не возврат вспомогательной функции.
    """
    records = [("M", "a.txt", "a.txt"), ("M", "b.txt", "b.txt")]

    with pytest.raises(RuntimeError) as excinfo:
        enumerate_review_units._assign_sections(records, [(0, 5)])

    # Секция досталась `a.txt`; обделена ВТОРАЯ запись — её и называет локатор.
    assert "b.txt" in str(excinfo.value)


# =============================================================================
# Круг 6 внешнего ревью (external-findings-round6.md): B1-B3.
# =============================================================================


def test_diff_hunks_survives_wide_diff_context_and_inter_hunk_context_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B1: `diff.context`/`diff.interHunkContext`, выставленные широко, не должны сливать элементы.

    Докстринг `_whole_diff_args` называет измеренный вход: файл из 30 строк с
    правками в строках 3 и 21 при умолчании git даёт ДВА элемента (`H:c0d940f2`,
    `H:e4177606`), а при `diff.context=10` — уже ОДИН (`H:f7a52fb9`, все
    идентификаторы другие) — соседние правки молча сливаются в один hunk, и
    таблица предъявления начинает ссылаться на идентификаторы, которых у
    следующего читателя не будет. Инструмент форсирует `-U3
    --inter-hunk-context=0` на команде патча — те же значения, что умолчания
    git, — чтобы чужой конфиг не мог сдвинуть эту границу. Вход здесь ВДВОЕ
    шире измеренного слияния (20, не 10), с запасом: два элемента обязаны
    остаться двумя.

    Убери форсирование этих двух флагов из `_whole_diff_args` — и этот тест
    покраснеет: `len(hunks)` станет 1, а не 2.
    """
    repo = _init_hunks_repo(tmp_path)
    subprocess.run(["git", "config", "diff.context", "20"], cwd=repo, check=True)
    subprocess.run(["git", "config", "diff.interHunkContext", "20"], cwd=repo, check=True)

    lines = [f"line {i}\n" for i in range(1, 31)]
    _write(repo, "f.txt", "".join(lines))
    _commit_all(repo)
    lines[2] = "line 3 CHANGED\n"
    lines[20] = "line 21 CHANGED\n"
    _write(repo, "f.txt", "".join(lines))

    monkeypatch.chdir(repo)
    hunks = [h for h in enumerate_review_units.diff_hunks() if h.path == "f.txt"]

    assert len(hunks) == 2


def test_untracked_then_staged_gitattributes_no_diff_file_gives_the_same_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B3: закоммиченный `.gitattributes` (`*.log -diff`) — untracked и staged дают ОДИН и тот же `H:`.

    Рецидив BLOCKER 3: до круга 6 двоичность неотслеживаемого файла решалась
    ЕДИНСТВЕННЫМ признаком — нулевым байтом (`untracked_hunks`), — а git на
    отслеживаемой стороне спрашивает АТРИБУТ `diff` ПЕРВЫМ и только при его
    отсутствии смотрит на содержимое. Правило `-diff` не несёт нулевого байта
    вовсе, поэтому untracked-ветка по старому признаку шла ТЕКСТОМ, а
    staged-ветка (сам git) — ДВОИЧНОЙ формой: один `git add`, не менявший ни
    байта в файле, менял его `H:`. Инструмент теперь спрашивает то же самое,
    что и git (`_diff_attributes`/`_is_binary_for_git`), на неотслеживаемой
    стороне тоже — обе ветки решают ОДИНАКОВО.

    Верни на неотслеживаемой стороне признак «только нулевой байт» — и этот
    тест покраснеет: `untracked_hunk.context` станет обычным текстовым
    `@@ -0,0 +1,N @@` вместо `@@ binary @@`, и `id_untracked != id_staged`.
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, ".gitattributes", "*.log -diff\n")
    _write(repo, "keep.txt", "keep\n")
    _commit_all(repo)
    _write(repo, "n.log", "first line\nsecond line\n")

    monkeypatch.chdir(repo)
    untracked_hunk = next(h for h in enumerate_review_units.untracked_hunks() if h.path == "n.log")
    id_untracked = enumerate_review_units.hunk_id(untracked_hunk.path, untracked_hunk.raw_body)

    subprocess.run(["git", "add", "n.log"], cwd=repo, check=True)

    # Свидетель: git САМ считает `n.log` двоичным из-за атрибута — не выдумка теста.
    witness = subprocess.run(
        ["git", "diff", "HEAD", "--no-color"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "Binary files" in witness

    staged_hunk = next(h for h in enumerate_review_units.diff_hunks() if h.path == "n.log")
    id_staged = enumerate_review_units.hunk_id(staged_hunk.path, staged_hunk.raw_body)

    assert untracked_hunk.context == "@@ binary @@"
    assert staged_hunk.context == "@@ binary @@"
    assert untracked_hunk.body == staged_hunk.body
    assert id_untracked == id_staged


def _documented_hunks_commands() -> list[list[str]]:
    """Три команды `hunks`, взятые ДОСЛОВНО из фенса `docs/process/implementation.md`.

    Извлекается ИМЕННО фенс с командами (маркер — третья команда, статуса
    `status --untracked-files=all`, она уникальна в файле), а не переписывается
    в тесте по памяти: B2 сверяет документ с кодом ИСПОЛНЕНИЕМ, и списанные
    руками токены были бы тем же самым чтением, которое и подвело документ до
    круга 6 (он называл команд две вместо трёх).

    Строки внутри фенса склеены по обратному слэшу в конце строки (перенос
    команды на следующую строку), а сами команды разделены пустой строкой.
    """
    doc_path = SCRIPT_PATH.parents[2] / "docs" / "process" / "implementation.md"
    text = doc_path.read_text(encoding="utf-8")
    match = re.search(
        r"```\n(git -c core\.quotepath=false diff HEAD.*?"
        r"git -c core\.quotepath=false status --porcelain --untracked-files=all)\n```",
        text,
        re.DOTALL,
    )
    assert match is not None, "фенс с тремя командами не найден в implementation.md"
    joined = re.sub(r"\\\n\s*", " ", match.group(1))
    commands = [line.strip() for line in joined.split("\n") if line.strip()]
    assert len(commands) == 3, f"ожидались три команды, найдено {len(commands)}: {commands!r}"
    return [shlex.split(command) for command in commands]


def test_documented_hunks_commands_match_the_tool_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B2: ВСЕ ТРИ документированные команды дают побайтно то же, что запускает инструмент.

    До круга 6 документ приводил только ДВЕ команды из трёх — третья
    (`git status --porcelain`, неотслеживаемые файлы) упоминалась прозой, и в
    ней вовсе отсутствовал `--untracked-files=all` — а этот флаг НЕСУЩИЙ (круг
    1, K1): без него новый каталог сворачивается в одну запись `?? newdir/`, и
    все файлы внутри пропадают из знаменателя.

    Команды здесь не переписываются по памяти — они парсятся из САМОГО файла
    (`_documented_hunks_commands`). А вызовы `git`, которые реально делает
    инструмент, перехватываются на лету: `subprocess.run` подменён шпионом,
    который всё равно исполняет настоящий git и возвращает настоящий
    результат, — сравниваются РЕАЛЬНЫЕ байты обеих сторон, а не переписанные
    вручную аргументы одной из них.
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "f.txt", "old\n")
    _commit_all(repo)
    _write(repo, "f.txt", "new\n")
    _write(repo, "untracked.txt", "added\n")
    # Каталог, а не только плоский файл: без `--untracked-files=all` `git
    # status --porcelain` свернул бы его в одну запись `?? newdir/`, и вывод
    # третьей команды перестал бы совпадать с тем, что реально видит
    # инструмент, — без этого узла тест B2 не был бы чувствителен к K1.
    _write(repo, "newdir/inside.txt", "added\n")

    documented = _documented_hunks_commands()

    monkeypatch.chdir(repo)
    real_run = subprocess.run
    captured: list[tuple[list[str], bytes]] = []

    def spying_run(args, *pargs, **kwargs):
        completed = real_run(args, *pargs, **kwargs)
        if args and args[0] == "git":
            stdout = completed.stdout
            if isinstance(stdout, str):
                stdout = stdout.encode("utf-8", errors="replace")
            captured.append((list(args), stdout if stdout is not None else b""))
        return completed

    monkeypatch.setattr(subprocess, "run", spying_run)

    enumerate_review_units.diff_hunks()
    enumerate_review_units.untracked_hunks()

    whole_diff_call = next(
        (args, out)
        for args, out in captured
        if args[:5] == ["git", "-c", "core.quotepath=false", "diff", "HEAD"]
    )
    raw_diff_call = next((args, out) for args, out in captured if "--raw" in args)
    status_call = next(
        (args, out) for args, out in captured if args[:4] == ["git", "-c", "core.quotepath=false", "status"]
    )
    tool_calls = [whole_diff_call, raw_diff_call, status_call]

    assert len(documented) == len(tool_calls) == 3
    for doc_tokens, (tool_args, tool_output) in zip(documented, tool_calls, strict=True):
        doc_result = real_run(doc_tokens, cwd=repo, capture_output=True, check=True)
        assert doc_result.stdout == tool_output, f"документ: {doc_tokens}\nинструмент: {tool_args}"
