"""Тесты check_17 стража документации (§9.1 ↔ docs/process/implementation.md).

Три ветви отчёта check_17 доказываются ТРЕМЯ РАЗНЫМИ входами, и каждый вход
предъявлен красным по СВОЕЙ ветви — снятием ровно того, от чего эта ветвь
защищает; ни один из трёх не роняет две ветви разом
(docs/insights/branch-completeness-misses-weak-predicates.md,
docs/insights/testing-races-verify-by-removing-guard.md). Тесты не трогают
рабочий репозиторий ни на одном шаге: входы «раздела нет» и «ссылки нет» —
списки строк, собранные самим тестом; вход «файла нет» подменяет модульную
константу `ROOT` (`monkeypatch`), а не путь на диске.
"""
import scripts.check_agents_index as guard


def _section_with_link(target: str = guard.IMPL_REL) -> list[str]:
    """Минимальный §9.1 с markdown-ссылкой на `target` внутри своего тела.

    Форма — как у настоящего AGENTS.md: `### 9.1.`, тело, затем `### 9.2.`
    границей (`subsection_of` останавливается на ближайшем `### ` либо `## `).
    """
    return [
        "## 9. Процесс разработки",
        "",
        "### 9.1. Цикл фичи (единица работы)",
        "",
        f"Фаза предъявления описана в [`{target}`]({target}).",
        "",
        "### 9.2. Таксономия документов",
        "",
        "Текст §9.2, не относящийся к §9.1.",
    ]


def test_check_17_green_on_well_formed_section():
    """Базовая зелень: раздел есть, ссылка на IMPL_REL есть, файл существует.

    ROOT не подменяется — `docs/process/implementation.md` в реальном
    репозитории уже существует (заведён предыдущей задачей фичи), и этот вход
    доказывает, что предикат не тождественно красный.
    """
    ok, details = guard.check_17(_section_with_link())
    assert ok is True
    assert details == []


def test_check_17_red_when_subsection_absent():
    """Ветвь 1: подраздела «### 9.1.» в AGENTS.md нет вовсе — ранний возврат.

    Вход несёт §9 БЕЗ «### 9.1.» (только §9.2, и в нём — ссылка на IMPL_REL,
    чтобы доказать: одного наличия ссылки где-то в §9 недостаточно). Единственная
    ветвь, которую можно уронить, когда самого §9.1 нет, — это ветвь 1: судить о
    ссылке или о файле уже не из чего, поэтому — ранний возврат, ОДНА запись.
    """
    lines = [
        "## 9. Процесс разработки",
        "",
        "### 9.2. Таксономия документов",
        "",
        f"Здесь тоже есть ссылка [`{guard.IMPL_REL}`]({guard.IMPL_REL}), но не в §9.1.",
    ]
    ok, details = guard.check_17(lines)
    assert ok is False
    assert len(details) == 1
    assert "подраздела" in details[0] and "9.1" in details[0]


def test_check_17_red_when_9_1_does_not_link_impl_rel():
    """Ветвь 2: §9.1 есть, но не ссылается на IMPL_REL.

    IMPL_REL встречается в теле §9.1 ГОЛОЙ ПОДСТРОКОЙ в бэктиках, БЕЗ
    markdown-ссылки, — ровно вход «подстрока есть, ссылки нет» из утверждения
    задачи: маршрут сверяется по ЦЕЛИ ссылки (`local_targets`), а не
    вхождением подстроки, и голая подстрока эту ветвь не гасит. Файл на диске
    существует (ROOT не подменён), поэтому ветвь 3 остаётся зелёной — красна
    только эта одна запись.
    """
    lines = [
        "## 9. Процесс разработки",
        "",
        "### 9.1. Цикл фичи (единица работы)",
        "",
        f"Фаза предъявления описана в файле `{guard.IMPL_REL}` (без ссылки).",
        "",
        "### 9.2. Таксономия документов",
        "",
        "Текст §9.2.",
    ]
    ok, details = guard.check_17(lines)
    assert ok is False
    assert len(details) == 1
    assert "не ссылается" in details[0]


def test_check_17_ignores_link_that_lives_in_sibling_subsection():
    """§9.1 без ссылки, но §9.3 ссылается на IMPL_REL — проверка не зеленеет.

    Прямая проверка решения оркестратора: `section_of(lines, "## 9.")` вернула
    бы весь §9 целиком вместе с §9.3, и чужая ссылка удовлетворила бы условие
    «§9.1 ссылается на IMPL_REL» ложно. `check_17` берёт `subsection_of`,
    ограниченный ближайшим «### » либо «## », и в его срез ссылка §9.3 не
    попадает.
    """
    lines = [
        "## 9. Процесс разработки",
        "",
        "### 9.1. Цикл фичи (единица работы)",
        "",
        "Здесь §9.1 не ссылается на файл фазы предъявления вовсе.",
        "",
        "### 9.2. Таксономия документов",
        "",
        "Текст §9.2.",
        "",
        "### 9.3. Ветвление, проверки, PR",
        "",
        f"Правила предъявления — в [`{guard.IMPL_REL}`]({guard.IMPL_REL}).",
    ]
    # Свидетельство того, что дыра реальна: `section_of("## 9.")` (негодная для
    # §9.1 стратегия, см. её докстринг) находит ссылку §9.3 внутри "## 9." целиком.
    whole_section_9 = guard.section_of(lines, "## 9.")
    assert guard.IMPL_REL in guard.local_targets("\n".join(whole_section_9))

    ok, details = guard.check_17(lines)
    assert ok is False
    assert len(details) == 1
    assert "не ссылается" in details[0]


def test_check_17_red_when_impl_rel_missing_on_disk(monkeypatch, tmp_path):
    """Ветвь 3: §9.1 и ссылка на месте, но файла IMPL_REL нет на диске.

    `ROOT` подменяется на временный каталог через `monkeypatch`, а не правкой
    рабочего репозитория: `check_17` строит путь как `ROOT / IMPL_REL`, и
    пустой временный `ROOT` даёт «файла нет» без единой записи вне `tmp_path`.
    Раздел и ссылка на месте (см. `_section_with_link`), поэтому ветви 1 и 2
    остаются зелёными — красна только эта одна запись.
    """
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    ok, details = guard.check_17(_section_with_link())
    assert ok is False
    assert len(details) == 1
    assert guard.IMPL_REL in details[0]
    assert "нет файла" in details[0]
