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

import os
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
