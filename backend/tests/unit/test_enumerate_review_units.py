"""Тесты подкоманды `assertions` перечислителя единиц ревью (задача 1).

Скрипт read-only, и это касается и тестов: ни один из них не имеет права
изменить рабочее дерево или индекс этого репозитория. Тестам, которым нужно
состояние git (независимость `repo_root()` от текущего каталога, неизменность
`git status --porcelain`), заводится СВОЙ временный репозиторий во временном
каталоге pytest (`tmp_path` + `git init`) — рабочий репозиторий они не трогают
вовсе.

Тесты чистых функций (`task_block`, `assertion_items`, `emit`) обходятся без
git и без файлов: план передаётся списком строк, как в реальном контракте.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

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


# --- assertion_items ---------------------------------------------------------


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
    # Пункт 4 идёт после вложенных пунктов третьего — его номер верхнего
    # уровня 4, и никакого «переноса» буквы из пункта 3 у него нет: он вообще
    # не вложенный.
    assert "A1.4" in items
    assert "A1.4a" not in items


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
    assert items == [
        ("A1.1", "утверждение первой задачи, пункт один;"),
        (
            "A1.2",
            "утверждение первой задачи, пункт два, дописанный второй строкой без тире;",
        ),
        ("A1.3", "**третий** пункт первой задачи с вложенными случаями:"),
        ("A1.3a", "вложенный пункт «a» третьего пункта;"),
        ("A1.3b", "вложенный пункт «b» третьего пункта;"),
        ("A1.4", "пункт четыре, идущий после вложенных — своя буква с единицы не наследуется."),
    ]


def test_assertion_items_excludes_files_section() -> None:
    block = enumerate_review_units.task_block(plan_lines(), 1)
    items = enumerate_review_units.assertion_items(block)
    assert not any("Create: `a.py`" in text for _, text in items)


def test_assertion_items_excludes_interfaces_section() -> None:
    block = enumerate_review_units.task_block(plan_lines(), 1)
    items = enumerate_review_units.assertion_items(block)
    assert not any("Производит" in text or "f() -> None" in text for _, text in items)


def test_assertion_items_excludes_names_section() -> None:
    block = enumerate_review_units.task_block(plan_lines(), 1)
    items = enumerate_review_units.assertion_items(block)
    assert not any("Заводятся этой задачей" in text for _, text in items)


def test_assertion_items_excludes_verification_section() -> None:
    block = enumerate_review_units.task_block(plan_lines(), 1)
    items = enumerate_review_units.assertion_items(block)
    assert not any("не утверждение" in text for _, text in items)


def test_assertion_items_missing_section_returns_empty() -> None:
    block = enumerate_review_units.task_block(plan_lines(), 3)
    assert enumerate_review_units.assertion_items(block) == []


def test_assertion_items_two_runs_are_byte_identical() -> None:
    block = enumerate_review_units.task_block(plan_lines(), 1)
    first = enumerate_review_units.assertion_items(block)
    second = enumerate_review_units.assertion_items(block)
    assert first == second


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
    repo = _init_repo(tmp_path)
    plan_rel = "docs/superpowers/plans/test-plan.md"

    first = _run_assertions(repo, plan_rel, "1")
    second = _run_assertions(repo, plan_rel, "1")

    assert first.returncode == 0
    assert first.stdout == second.stdout


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
    repo = _init_repo(tmp_path)
    missing_task = _run_assertions(repo, "docs/superpowers/plans/test-plan.md", "99")
    missing_section = _run_assertions(repo, "docs/superpowers/plans/test-plan.md", "3")

    assert missing_task.returncode != 0
    assert missing_section.returncode != 0
    assert missing_task.stderr != missing_section.stderr


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
