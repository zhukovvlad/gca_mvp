"""Тесты check_17 и check_18 стража документации (маршруты §9.1 и §9.2).

check_18 (§9.2 ↔ docs/product-roadmap.md) устроена тем же контрактом, что
check_17, плюс ветвь КРАТНОСТИ шапки «Когда читать:» по образцу check_12.
Четыре её ветви предъявляются восемью входами, и у кратности предъявлены ОБЕ
границы — 0 и 2: односторонний предикат пропустил бы вторую шапку по
построению (`docs/insights/enumerate-the-rules-own-properties.md`). Участие в
прогоне `main()` проверяется по СТРОКЕ ОТЧЁТА этой проверки, а не по одному
коду возврата: пока идёт фича-ревизия, `main()` законно возвращает 1 из-за
проверки версий, и утверждение «сняли регистрацию — стало 1» было бы зелено
всегда (`docs/insights/claimed-property-needs-its-own-input.md`).


ТРИ ветви отчёта check_17 доказываются НЕСКОЛЬКИМИ входами — ветвь 2 («не
ссылается») уронить можно КАЧЕСТВЕННО разными способами (см. докстринг самого
`check_17` — ветви и входы там названы раздельно, как у check_12/check_13), и
каждый вход предъявлен красным по СВОЕЙ ветви: ни один из трёх не роняет две
ветви разом (docs/insights/branch-completeness-misses-weak-predicates.md,
docs/insights/testing-races-verify-by-removing-guard.md). Отдельно закрыт
слабый предикат `if not targets:` (ссылка на ЧУЖОЙ, но существующий файл),
половина границы `subsection_of` по `## ` и то, что `check_17` реально
участвует в прогоне `main()`, а не только существует в коде (ревью, круг 1).

Тесты не трогают рабочий репозиторий ни на одном шаге: входы «раздела нет» и
«ссылки нет» — списки строк, собранные самим тестом; вход «файла нет»
подменяет модульную константу `ROOT` (`monkeypatch`), а не путь на диске;
вход «регистрация снята» проверяется вызовом настоящего `guard.main()` на
реальном дереве (только для чтения).
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


def test_check_17_red_when_9_1_links_to_wrong_target():
    """Ветвь 2, вход (в): §9.1 ссылается на ДРУГОЙ локальный файл.

    Отличает «ссылка есть» от «ссылка ведёт на IMPL_REL» — слабый предикат
    `if not targets:` пропустил бы этот вход зелёным, потому что `targets`
    непуст (там лежит чужая цель — проверка сверяет ПРИНАДЛЕЖНОСТЬ цели
    множеству, а не то, существует ли файл на диске); правильный предикат
    `IMPL_REL not in targets` красит его верно, потому что сравнивает ЦЕЛЬ, а
    не непустоту множества. Найдено внешним ревью, круг 1
    (docs/insights/branch-completeness-misses-weak-predicates.md): дефолт
    `_section_with_link(target=guard.IMPL_REL)` до этого теста ни разу не
    вызывался с другим значением, и параметр был мёртв.
    """
    ok, details = guard.check_17(_section_with_link(target="docs/process/review.md"))
    assert ok is False
    assert len(details) == 1
    assert "не ссылается" in details[0]


def test_check_17_stops_subsection_at_top_level_heading():
    """Ветвь 2, вход (г) второй половины: §9.1 — последний подраздел §9, сразу
    за ним `## 10.` со ссылкой на IMPL_REL, а не другой `### `.

    Граница `subsection_of` несёт ОБЕ половины предиката (`### ` И `## `). Во
    всех остальных входах набора сразу после §9.1 стоит `### 9.2.`, и половина
    `## ` ни разу не срабатывает. Здесь — срабатывает: без неё срез дошёл бы
    до конца входа и захватил бы чужую ссылку из `## 10.`, а с ней — ссылка
    §10 в срез §9.1 не попадает, и §9.1 (без своей ссылки) красит ветвь 2.
    """
    lines = [
        "## 9. Процесс разработки",
        "",
        "### 9.1. Цикл фичи (единица работы)",
        "",
        "Здесь §9.1 не ссылается на файл фазы предъявления вовсе.",
        "",
        "## 10. Другой раздел верхнего уровня",
        "",
        f"Ссылка чужого раздела — [`{guard.IMPL_REL}`]({guard.IMPL_REL}).",
    ]
    ok, details = guard.check_17(lines)
    assert ok is False
    assert len(details) == 1
    assert "не ссылается" in details[0]


def test_check_17_participates_in_main_run(capsys):
    """`check_17` реально участвует в прогоне `main()`, а не только существует
    в коде — регистрация в `results` ничем не стережётся сама по себе.

    Вызывается настоящий `main()` на реальном дереве репозитория (только
    чтение, ROOT не подменяется). Без строки регистрации `check_17` в
    `results` страж напечатал бы «17 из 17» с кодом возврата 0 — «пропавшая»
    проверка молча перестаёт исполняться (ревью, круг 1, I3).

    Знаменатель здесь ОБЯЗАН называться числом и обязан расти вместе с
    набором: он и есть то, чем «пропавшая проверка» обнаруживается. Фича
    «дорожная карта под гит» довела его с 17 до 18.
    """
    code = guard.main()
    output = capsys.readouterr().out
    assert code == 0
    assert "Итог: 18 из 18." in output


def test_check_17_break_yields_exit_code_1_via_main(monkeypatch):
    """Поломка check_17 внутри `main()` даёт код возврата 1, не только `ok=False`.

    Утверждение задачи «код возврата стража — 0 на здоровом дереве, 1 при
    любой из трёх поломок» было предъявлено только половиной: что `check_17`
    возвращает `ok=False`, а не что это доходит до кода возврата `main()`.
    IMPL_REL подменяется на несуществующий путь — единственная константа,
    которую видит только `check_17` (проверено `grep`-ом: больше нигде в
    модуле не участвует), поэтому все шестнадцать остальных проверок
    отрабатывают на настоящем дереве без изменений, а `check_17` красит и
    ветвь 2 (реальная ссылка §9.1 больше не совпадает с подменённым IMPL_REL),
    и ветвь 3 (подменённого пути нет на диске) — итог обязан быть 1.
    """
    monkeypatch.setattr(guard, "IMPL_REL", "docs/process/does-not-exist.md")
    assert guard.main() == 1


# --- check_18: §9.2 ↔ docs/product-roadmap.md (маршрут, шапка) ----------------


def _section_9_2_with_link(target: str = guard.ROADMAP_REL) -> list[str]:
    """Минимальный §9.2 с markdown-ссылкой на `target` внутри своего тела.

    Форма — как у настоящего AGENTS.md: `### 9.2.` зажат между `### 9.1.` и
    `### 9.3.`, и `subsection_of` режет срез по ближайшему из них. Клетка
    таблицы с голым бэктиком стоит здесь намеренно: она есть и в реальном
    документе, и зелень обязана держаться НЕ на ней.
    """
    return [
        "## 9. Процесс разработки",
        "",
        "### 9.1. Цикл фичи (единица работы)",
        "",
        "Текст §9.1, не относящийся к §9.2.",
        "",
        "### 9.2. Таксономия документов",
        "",
        f"| Дорожная карта | `{guard.ROADMAP_REL}` | направления развития |",
        "",
        f"Карта — [`{target}`]({target}) — живёт постоянно.",
        "",
        "### 9.3. Ветвление, проверки, PR",
        "",
        "Текст §9.3.",
    ]


def _report_line(output: str, needle: str) -> str:
    """Строка отчёта `main()`, содержащая `needle`.

    Строка ищется по ИМЕНИ РЕГИСТРАЦИИ проверки: имя в `results` — литерал и
    от подменяемых констант не зависит. Поиск по подменённому пути дал бы
    `StopIteration` — искал бы то, чего проверка не печатает.
    """
    return next(line for line in output.splitlines() if needle in line)


ROADMAP_CHECK_NAME = "docs/product-roadmap.md (маршрут"


def _roadmap_root(tmp_path, headers: int):
    """Временный ROOT, где карта несёт ровно `headers` шапок «Когда читать:».

    Пишется в `tmp_path`, а не в рабочий репозиторий: ветвь кратности иначе
    потребовала бы портить отслеживаемый файл.
    """
    path = tmp_path / guard.ROADMAP_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    body = ["**Когда читать:** выбираешь следующую фичу." for _ in range(headers)]
    path.write_text("\n".join(body + ["", "# Дорожная карта", ""]), encoding="utf-8")
    return tmp_path


def test_check_18_green_on_well_formed_section():
    """Базовая зелень: подраздел есть, ссылка есть, файл есть, шапка одна.

    ROOT не подменяется — `docs/product-roadmap.md` в реальном репозитории уже
    существует (заведён задачей 1 этой же фичи) и несёт ровно одну шапку. Вход
    доказывает, что предикат не тождественно красный.
    """
    ok, details = guard.check_18(_section_9_2_with_link())
    assert ok is True
    assert details == []


def test_check_18_red_when_subsection_absent():
    """Ветвь 1: подраздела «### 9.2.» нет вовсе — ранний возврат, ОДНА запись.

    Вход несёт §9 БЕЗ «### 9.2.», причём ссылка на карту в нём ЕСТЬ — в §9.1.
    Одного наличия ссылки где-то в §9 недостаточно, а судить о файле и шапке,
    когда самого подраздела нет, не из чего.
    """
    lines = [
        "## 9. Процесс разработки",
        "",
        "### 9.1. Цикл фичи (единица работы)",
        "",
        f"Ссылка есть, но не в §9.2: [`{guard.ROADMAP_REL}`]({guard.ROADMAP_REL}).",
    ]
    ok, details = guard.check_18(lines)
    assert ok is False
    assert len(details) == 1
    assert "подраздела" in details[0] and "9.2" in details[0]


def test_check_18_red_when_9_2_has_no_local_links_at_all():
    """Ветвь 2, вход (а): в §9.2 нет ни одной локальной ссылки вовсе.

    Самый бедный вход ветви: множество целей ПУСТО. Отчёт обязан это назвать
    («ни одной ссылки»), а не молча сказать «не ссылается» — причина у пустого
    и у чужого множества разная.
    """
    lines = [
        "## 9. Процесс разработки",
        "",
        "### 9.2. Таксономия документов",
        "",
        "Таблица классов без единой ссылки.",
        "",
        "### 9.3. Ветвление, проверки, PR",
    ]
    ok, details = guard.check_18(lines)
    assert ok is False
    assert len(details) == 1
    assert "не ссылается" in details[0]
    assert "ни одной ссылки" in details[0]


def test_check_18_red_when_roadmap_is_a_bare_backtick_path():
    """Ветвь 2, вход (б): путь стоит ГОЛОЙ подстрокой в бэктиках, без ссылки.

    Вход не гипотетический, а неизбежный: клетка «Где» строки «Дорожная карта»
    несёт ровно этот путь голым бэктиком, как все тринадцать её соседок. Здесь
    он единственный носитель пути в §9.2 — и проверка обязана НЕ засчитать его
    за маршрут, иначе она стерегла бы форму таблицы вместо ссылки, и
    абзац-маршрут можно было бы удалить незаметно.
    """
    lines = [
        "## 9. Процесс разработки",
        "",
        "### 9.2. Таксономия документов",
        "",
        f"| Дорожная карта | `{guard.ROADMAP_REL}` | направления развития |",
        "",
        "### 9.3. Ветвление, проверки, PR",
    ]
    # Свидетельство, что дыра реальна: подстрока в срезе ЕСТЬ, а цели ссылки нет.
    section = guard.subsection_of(lines, "### 9.2.")
    assert guard.ROADMAP_REL in "\n".join(section)
    assert guard.ROADMAP_REL not in guard.local_targets("\n".join(section))

    ok, details = guard.check_18(lines)
    assert ok is False
    assert len(details) == 1
    assert "не ссылается" in details[0]


def test_check_18_red_when_9_2_links_to_another_existing_file():
    """Ветвь 2, вход (в): §9.2 ссылается на ДРУГОЙ существующий локальный файл.

    Отличает «ссылка есть» от «ссылка ведёт на карту»: ослабленный предикат
    `if not targets:` держал бы вход зелёным, потому что множество целей
    НЕПУСТО. Цель взята существующая и настоящая — так §9.2 ссылается на спеку
    «история наружу» и без этой фичи, — чтобы вход отличался от (а) ровно
    содержимым множества, а не его наличием.
    """
    other = "docs/superpowers/specs/2026-09-05-history-out-design.md"
    assert (guard.ROOT / other).is_file()

    ok, details = guard.check_18(_section_9_2_with_link(target=other))
    assert ok is False
    assert len(details) == 1
    assert "не ссылается" in details[0]


def test_check_18_ignores_link_that_lives_in_sibling_subsection():
    """Ветвь 2, вход (г): ссылка на карту стоит в СОСЕДНЕМ подразделе.

    Прямая проверка границы: `section_of(lines, "## 9.")` вернула бы весь §9
    вместе с §9.1 и §9.3, и чужая ссылка удовлетворила бы условие «§9.2
    ссылается на карту» ложно. `check_18` берёт `subsection_of`, и в его срез
    ссылки соседей не попадают.
    """
    lines = [
        "## 9. Процесс разработки",
        "",
        "### 9.1. Цикл фичи (единица работы)",
        "",
        f"Ссылка §9.1 — [`{guard.ROADMAP_REL}`]({guard.ROADMAP_REL}).",
        "",
        "### 9.2. Таксономия документов",
        "",
        "Здесь §9.2 на карту не ссылается вовсе.",
        "",
        "### 9.3. Ветвление, проверки, PR",
        "",
        f"И ссылка §9.3 — [`{guard.ROADMAP_REL}`]({guard.ROADMAP_REL}).",
    ]
    whole_section_9 = guard.section_of(lines, "## 9.")
    assert guard.ROADMAP_REL in guard.local_targets("\n".join(whole_section_9))

    ok, details = guard.check_18(lines)
    assert ok is False
    assert len(details) == 1
    assert "не ссылается" in details[0]


def test_check_18_red_when_roadmap_missing_on_disk(monkeypatch, tmp_path):
    """Ветвь 3: подраздел и ссылка на месте, но файла карты нет на диске.

    `ROOT` подменяется на пустой временный каталог, а не правится рабочий
    репозиторий. Ранний возврат после этой ветви несущий: шапку считать не из
    чего, и без возврата одна поломка красила бы заодно ветвь 4.
    """
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    ok, details = guard.check_18(_section_9_2_with_link())
    assert ok is False
    assert len(details) == 1
    assert guard.ROADMAP_REL in details[0]
    assert "нет файла" in details[0]


def test_check_18_red_when_roadmap_has_no_header(monkeypatch, tmp_path):
    """Ветвь 4, граница 0: у карты нет шапки «Когда читать:» вовсе."""
    monkeypatch.setattr(guard, "ROOT", _roadmap_root(tmp_path, headers=0))
    ok, details = guard.check_18(_section_9_2_with_link())
    assert ok is False
    assert len(details) == 1
    assert "шапки" in details[0]
    assert "— 0" in details[0]


def test_check_18_red_when_roadmap_has_two_headers(monkeypatch, tmp_path):
    """Ветвь 4, граница 2: у карты ДВЕ шапки «Когда читать:».

    Вторая граница кратности предъявляется отдельным входом намеренно:
    односторонний предикат («шапка есть») пропустил бы этот вход зелёным, а
    две шапки — ровно то состояние, которым кончилось бы слияние карты с
    другим документом.
    """
    monkeypatch.setattr(guard, "ROOT", _roadmap_root(tmp_path, headers=2))
    ok, details = guard.check_18(_section_9_2_with_link())
    assert ok is False
    assert len(details) == 1
    assert "шапки" in details[0]
    assert "— 2" in details[0]


def test_check_18_participates_in_main_run(capsys):
    """`check_18` реально исполняется внутри `main()`, а не только существует.

    Утверждение предъявляется по СТРОКЕ ОТЧЁТА этой проверки, а не по коду
    возврата `main()`: пока идёт фича-ревизия, код законно равен 1 из-за
    проверки версий, и утверждение «сняли регистрацию — стало 1» было бы
    зелено всегда, ничего не стерегая. Строка же исчезает вместе с
    регистрацией — и знаменатель «из 18» падает до «из 17».
    """
    guard.main()
    output = capsys.readouterr().out
    assert "из 18." in output
    assert _report_line(output, ROADMAP_CHECK_NAME).strip().startswith("18. [OK ]")


def test_check_18_break_shows_fail_in_main_run(monkeypatch, capsys):
    """Поломка check_18 доходит до ОТЧЁТА и до кода возврата `main()`.

    `ROADMAP_REL` подменяется на несуществующий путь — константа, которую
    видит только `check_18`, поэтому остальные семнадцать проверок
    отрабатывают на настоящем дереве без изменений. Красной обязана стать
    именно строка восемнадцатой проверки: код возврата 1 сам по себе сейчас
    ничего не доказывает, потому что страж законно красен проверкой версий.

    Строка отчёта ищется по ИМЕНИ РЕГИСТРАЦИИ, а не по подменённому пути —
    см. `_report_line`.
    """
    monkeypatch.setattr(guard, "ROADMAP_REL", "docs/does-not-exist-roadmap.md")
    code = guard.main()
    output = capsys.readouterr().out

    assert _report_line(output, ROADMAP_CHECK_NAME).strip().startswith("18. [FAIL]")
    assert "does-not-exist-roadmap.md" in output
    assert code == 1
