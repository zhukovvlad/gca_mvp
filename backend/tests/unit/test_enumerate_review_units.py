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


def test_untracked_hunks_non_utf8_text_falls_back_to_binary_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cp1251-текст без NUL-байта (эвристика двоичности его не ловит) — не крашит, а форма «добавление».

    Текстовое тело над байтами, которые не декодируются как UTF-8, не
    определено ничуть не меньше, чем у настоящего двоичного файла — падать
    `UnicodeDecodeError`-ом и терять элемент знаменателя нельзя (найдено
    ревью круга 1, K3).
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
    assert hunk.context == "@@ binary @@"
    assert hunk.body == f"+binary {hashlib.sha256(cp1251_bytes).hexdigest()}"
    assert (hunk.added, hunk.removed) == (1, 0)


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
    assert hunk.context == "@@ metadata @@"
    assert hunk.body == "new file mode 100644\nindex 0000000..e69de29"
    assert (hunk.added, hunk.removed) == (0, 0)


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


# --- I3: `--src-prefix`/`--dst-prefix` не стерегутся ничем -------------------


def test_diff_hunks_survives_diff_noprefix_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`diff.noprefix=true` в чужом конфиге не должен ронять разбор — форсированные префиксы главнее.

    При `diff.noprefix=true` `git diff` без явных `--src-prefix`/`--dst-prefix`
    печатает `diff --git путь путь` и `+++ путь` вовсе БЕЗ `a/`/`b/` —
    `DIFF_GIT_HEADER` и `_section_path` на такую строку не матчатся, секция
    пропускается целиком, и КАЖДЫЙ отслеживаемый hunk исчезает молча (найдено
    ревью круга 1, I3). Удаление обоих флагов оставляет 20 из 20 старых
    тестов `hunks` зелёными — этот вход специально ставит конфиг, который их
    не оставляет зелёными.
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
    восьмеричными escape-последовательностями (`"a/\\321\\204…"`) — ни
    `DIFF_GIT_HEADER`, ни `_section_path` на такую строку не матчатся, и
    hunk пропадает молча.
    """
    repo = _init_hunks_repo(tmp_path)
    _write(repo, "файл.txt", "old\n")
    _commit_all(repo)
    _write(repo, "файл.txt", "new\n")

    monkeypatch.chdir(repo)
    hunks = [h for h in enumerate_review_units.diff_hunks() if h.path == "файл.txt"]

    assert len(hunks) == 1
    assert hunks[0].body == "-old\n+new"


# --- I4: жадность разбора заголовка `diff --git` -----------------------------


def test_diff_hunks_path_containing_the_diff_header_separator_substring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Путь, содержащий литеральную подстроку ` b/` (`x b/y.txt`), не должен резаться неверно.

    Строка `diff --git a/x b/y.txt b/x b/y.txt` неоднозначна для наивного
    `diff --git a/(.+) b/(.+)` — путь разобрался бы как `y.txt` вместо
    `x b/y.txt` (найдено ревью круга 1, I4). Путь берётся из строк
    `+++`/`---`, где разделитель — фиксированный префикс, а не поиск
    подстроки.
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
