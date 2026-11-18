# План: два медленных запроса экранов 4а/4б

**Спека:** `docs/superpowers/specs/2026-11-18-our-price-slow-queries-design.md` (редакция 1; гейт 2 — Codex на `3249d43` замечаний не имел, 18.11.2026)
**Дизайн:** `docs/superpowers/design/2026-11-18-our-price-slow-queries.md`, замер прототипа — папка `.../2026-11-18-our-price-slow-queries/`
**Ветка:** `perf/our-price-slow-queries`, черновой PR #90
**Статус:** редакция 1, 18.11.2026, ждёт гейта 3.

> Исполнителю: задачи идут по порядку. Адреса `§N` без уточнения — разделы
> спеки; «решение N» — пронумерованные решения этого плана. **Защит,
> дублирующих предикат, не класть.** Числа проверок — нижние границы. ДО сняты
> 18.11.2026 на `3249d43`: `-k slow_queries` — 0 тестов, `-k TestChapterPaths` —
> 5, `-k path_conflict_of_the_main_parameter_is_not_a_match` — 1. **Все новые
> тесты — в `backend/tests/integration/test_slow_queries.py`, шаблон `-k
> slow_queries`.**

## Режим реализации (как у А11, с 28.10.2026)

- **Исполнители** — Sonnet; оркестратор (Opus) принимает задачу, коммитит,
  ведёт `progress.md` (`.superpowers/sdd/2026-11-18-our-price-slow-queries/`).
- **Проверки по риску.** Отдельное ревью (Opus, узкий бриф) — только у Task 1
  (пометка **«ревью»**): её фильтр решает, какая строка истории годится в
  аналоги, то есть какая цена станет «нашей». Tasks 2–3 принимает
  оркестратор: тесты задачи, `ruff`, итог замера.
- **Снятия защит — только К1 ниже, делает оркестратор лично** после приёмки
  Task 1, в копии дерева (стенд отдаёт живое дерево) по протоколу
  `BASELINE_GREEN / MUTATION_APPLIED / EXPECTED_TEST_FAILURE / RESTORED`, с
  жёстким таймаутом прогона; итог — в `progress.md`.
- **Это отступление от `docs/process/implementation.md`** («Распределение
  ответственности»: снятия всех `behavioral`-элементов — ревьюером задачи) — по
  решению пользователя 28.10.2026, как в планах лестницы, упрощения каталога,
  справочных аналогов и А11; называется в devlog. Процессный документ этим
  планом не меняется.
- **Прогоны.** В итерациях — только `-k slow_queries` и точечные файлы; наборы
  соседей исполнитель гоняет **один раз в конце задачи**. Полный бэкенд и
  `just ci` локально не гоняются — проверяет GitHub Actions (`gh pr checks
  90`), красное чинится следующим коммитом.
- **Без остановок** между задачами; стоп — пуш, внешнее ревью и вопросы, где
  допущение небезопасно.
- **Финальное ревью ветки** — Codex (комментарий в PR #90).

## Global Constraints

- **Контракты не меняются** (§2.1): подписи, типы результата, порядок строк,
  пустой вход без запроса, отказ `RoutingError` на цикле у
  `_conflicted_parameters` и `_chapter_chain_rows`; вызывающие не правятся.
- **Ответы экранов не меняются ни в одном байте** — разбор, панель, файл
  подрядчику (дизайн, «Цели»; проверка 5 спеки).
- **Текст запроса цепочки начинается с `WITH RECURSIVE chain`** (§2.3) — на нём
  держится проверка подготовленного плана в `TestChapterPaths`.
- Миграций, индексов, `ANALYZE`, правок фронта, API, справочника и
  `AGENTS.md` нет (§5).
- **Стенд `gca_dev`** — только чтение (замер — транзакции `READ ONLY` с
  откатом); учётки стенда не трогать; кириллицу в терминал не печатать.
- **В тестах — условные имена** семей, параметров и разделов.

## Критические свойства (снятия оркестратора)

| № | Свойство | Задача | Снятие → ожидаемо красный тест |
|---|---|---|---|
| К1 | В конфликт попадает только главный не архивный параметр со значением `path_conflict` контекста из входа (§3 п. 1) | 1 | четыре независимых снятия в новом запросе: условие `role` → тест «второстепенный — нет»; `archived_at` → «архивный — нет»; `source` → «источник не `path_conflict` — нет»; условие на вход → «контекст не из входа — нет» |

Цепочка разделов денежного свойства не несёт — без снятий (§3).

## Review Focus

1. **Контекст с конфликтами под двумя схемами** (значения прежней и
   действующей схемы семьи) — оба ключа схемы, каждый со своими параметрами;
   `_conflicts_of` вызывающего выбирает нужный (Task 1).
2. **Повторы во входе** — тот же результат, что без повторов, в обеих
   функциях (Tasks 1, 2).
3. **Ключ на входе цепочки, которого нет в `position_items`** — его нет в
   результате, остальные пути целы (Task 2).
4. **Номер раздела пустой** (`item_number_in_proposal = NULL`) — шаг с `None`,
   а не пропуск шага (Task 2).
5. **Замер `app` до правки равен `old`** — смысловая проверка самого скрипта:
   на `main` варианты `old` и `app` обязаны дать одинаковые ответы и близкие
   времена, иначе `old` перенесён не дословно (Task 3).

## Структура файлов

```
backend/crud/our_price_load.py                 Edit  _conflicted_parameters — запрос (Task 1)
backend/services/context_routing.py            Edit  _chapter_chain_rows — шаг рекурсии (Task 2)
backend/tests/integration/test_slow_queries.py Create тесты фильтров конфликтов и шагов цепочки (Tasks 1, 2)
docs/superpowers/design/2026-11-18-our-price-slow-queries/measure.py  Edit варианты old/prototype/app (Task 3)
docs/superpowers/design/2026-11-18-our-price-slow-queries/results-app.json, plans-app/  Create итоговый замер (Task 3)
docs/pitfalls/db.md                            Edit  два пункта §2.5 (Task 3)
docs/devlog/2026-11-18-our-price-slow-queries.md  Create (Task 3)
docs/product-roadmap.md                        Edit  пункт 3 карты (Task 3)
```

## Решения плана, которых нет в спеке

1. **Один файл новых тестов** `test_slow_queries.py` на обе функции, классы
   `TestConflictedParameters` и `TestChapterSteps`; фикстуры — по образцу
   `test_variant_params_load.py` (`world.param(..., role=..., archived=...)`) и
   `test_semantic_api.py` (разделы и позиции предложения).
2. **`_conflicted_parameters` тестируется напрямую**, хоть и закрытая: дефект
   живёт в её фильтрах, а через загрузку КП отрицательный случай неотличим от
   «конфликта нет вовсе».
3. **Новые тесты сначала зелёные на старом запросе** (§3: фиксируют поведение).
   Красного прогона TDD у них нет и не будет: исполнитель показывает зелёный
   прогон ДО правки запроса и зелёный ПОСЛЕ; что тесты фильтров не
   ложно-зелёные, доказывает К1 снятиями.
4. **Порядок задач:** конфликты (с ревью) → цепочка → замер и документы. Замер
   идёт последним: вариант `app` имеет смысл только на коде с обеими правками.
5. **Итоговый замер пишется рядом с замером прототипа:** `results-app.json` и
   `plans-app/`; `results.json` и `plans/` не трогаются (§2.4).
6. **Прежний текст запросов для варианта `old`** берётся из `main` (`git show
   main:backend/...`) дословно, с подстановкой только имён параметров
   связывания, нужных `sa.text`.

## Задачи

### Task 1: запрос параметров с `path_conflict` — **ревью**

**Files**
- Edit: `backend/crud/our_price_load.py` (`_conflicted_parameters`)
- Create: `backend/tests/integration/test_slow_queries.py` (класс `TestConflictedParameters`)

**Interfaces**
- Потребляет: `ContextParameterValue`, `FamilyParameter`, `ValueSource`,
  `ParameterRole` (существуют).
- Производит: ничего нового — подпись прежняя:

```python
def _conflicted_parameters(db: Session, context_ids: Sequence[int]) -> dict[int, dict[int, frozenset[int]]]
```

**Утверждения** (все — зелёные и на запросе `main`, и на новом)
- в одном вызове: главный параметр с `path_conflict` — в результате;
  второстепенный с `path_conflict` — нет; главный в архиве с `path_conflict` —
  нет; главный с источником `name` — нет; контекст с `path_conflict`, не
  переданный во входе, — нет (К1);
- контекст с двумя главными параметрами в конфликте — один ключ схемы, в нём
  оба параметра;
- контекст с конфликтами главных параметров двух схем — два ключа схемы,
  каждый со своими параметрами (Review Focus 1);
- вход с повторами — тот же результат, что без повторов;
- пустой вход — `{}` и ни одного SQL-запроса (счётчик запросов сеанса, как в
  `TestChapterPaths::test_empty_input_gives_empty_dict_without_querying`);
- запрос: сначала отбор `context_parameter_values` по входу и источнику в CTE
  с префиксом `MATERIALIZED`, затем соединение с `family_parameters` и условия
  роли и архива (§2.2) — форма проверяется ревью, не тестом.

**Имена**
- Заводятся: `test_slow_queries.py`, `TestConflictedParameters`.
- Существуют, проверено `grep`-ом: `_conflicted_parameters`, `_conflicts_of`,
  `ContextParameterValue`, `FamilyParameter`, `ValueSource.path_conflict`,
  `ValueSource.name`, `ParameterRole.main`, `ParameterRole.secondary`.

**Проверка**
- `just test-int-local-k slow_queries` — ДО 0, ПОСЛЕ ≥ 6; зелёный ДО правки
  запроса и ПОСЛЕ (решение 3).
- Один раз в конце: `just test-int-local-k "path_conflict or variant_params or analog_scene"` — зелёный без правок ожиданий.
- `uv run ruff check` по тронутым файлам — чисто.

### Task 2: шаг рекурсии цепочки разделов

**Files**
- Edit: `backend/services/context_routing.py` (`_chapter_chain_rows`)
- Edit: `backend/tests/integration/test_slow_queries.py` (класс `TestChapterSteps`)

**Interfaces**
- Потребляет: `chapter_steps`, `ChapterStep`, `RoutingError` (существуют).
- Производит: ничего нового — `_chapter_chain_rows(db, chapter_item_ids)`,
  `chapter_steps`, `chapter_paths` с прежними подписями.

**Утверждения** (все — зелёные и на запросе `main`, и на новом)
- цепочка из трёх уровней и две позиции на разных ветках под общим корнем: у
  каждой — шаги сверху вниз, корень первым, сам раздел последним; у каждого
  шага — его ключ, название и номер в файле;
- шаг с пустым номером раздела — `number is None`, шаг не пропущен (Review
  Focus 4);
- раздел без родителя — один шаг;
- ключ, которого нет в `position_items`, — его нет в результате, остальные
  пути целы (Review Focus 3);
- вход с повторами — тот же результат, что без повторов;
- `TestChapterPaths` (5 тестов) — зелёный без правок, включая проверку
  подготовленного плана по тексту `WITH RECURSIVE chain`;
- запрос: шаг рекурсии — `LATERAL`-подзапрос к `position_items` по первичному
  ключу с `OFFSET 0`, рядом комментарий, зачем барьер (§2.3) — форма
  проверяется приёмкой, не тестом.

**Имена**
- Заводятся: `TestChapterSteps`.
- Существуют, проверено `grep`-ом: `_chapter_chain_rows`, `chapter_steps`,
  `chapter_paths`, `ChapterStep`, `RoutingError`, `TestChapterPaths`.

**Проверка**
- `just test-int-local-k slow_queries` — ДО ≥ 6, ПОСЛЕ ≥ 11; зелёный ДО правки
  запроса и ПОСЛЕ.
- `just test-int-local-k TestChapterPaths` — 5, зелёный.
- Один раз в конце: `just test-int-local-k "semantic_api or ref_analogs or our_price_offer or auto_split"` — зелёный без правок ожиданий.
- `uv run ruff check` по тронутым файлам — чисто.

### Task 3: итоговый замер, грабли, devlog, карта

**Files**
- Edit: `docs/superpowers/design/2026-11-18-our-price-slow-queries/measure.py`
- Create: `.../results-app.json`, `.../plans-app/*.txt`
- Edit: `docs/pitfalls/db.md`
- Create: `docs/devlog/2026-11-18-our-price-slow-queries.md`
- Edit: `docs/product-roadmap.md`

**Interfaces**
- Потребляет: код Tasks 1–2.
- Производит: аргумент скрипта `--variants` (список через запятую из `old`,
  `prototype`, `app`; по умолчанию `old,app`).

**Утверждения**
- варианты скрипта (§2.4): `old` — прежний текст обоих запросов из `main`
  (решение 6), `prototype` — нынешние прототипы, `app` — функции приложения
  без подмен; в каждом круге каждый вызов снимается всеми выбранными
  вариантами попеременно; равенство ответов сверяется между всеми выбранными;
  планы снимаются для каждого варианта;
- смысловая проверка скрипта (Review Focus 5): скрипт ветки, запущенный на
  коде `main`, с `--variants old,app --rounds 1` даёт равные ответы и времена
  `old` и `app` в пределах разброса кругов. Код `main` — копия `git archive
  main` во временной папке scratchpad с `backend/.env` из рабочего дерева
  (worktree не заводить; игнорируемые файлы в копию сами не попадают);
- итоговый замер на коде ветки, `gca_dev`: `--variants old,app --rounds 3` —
  ответы `old` и `app` совпадают во всех трёх вызовах; в каждом вызове
  медиана `app` по каждому из двух запросов ≤ 0,3 с на ответ, медиана всего
  ответа `app` ≤ половины `old` (§3 п. 5); результат — `results-app.json`,
  планы — `plans-app/`;
- `docs/pitfalls/db.md` — два пункта §2.5, в стиле файла (одна строка-абзац,
  жирное начало, чем выстрадано — ссылка на devlog);
- devlog: что сделано, таблица замера `old → app` с условиями, отступление от
  `implementation.md` (режим реализации), итог К1, тронутые области и
  прочитанные файлы граблей (`db.md`, `runs.md`) — AGENTS §10;
- карта, пункт 3 «Скорость 4а и 4б»: первая часть сделана (PR #90, ссылка на
  devlog, цифры `old → app`), остаток — кэш этапа по замеру.

**Имена**
- Заводятся: `--variants`, `results-app.json`, `plans-app/`.
- Существуют: `measure.py`, `use`, `CHAIN_AFTER`, `CONFLICT_AFTER`.

**Проверка**
- `uv run ruff check ../docs/superpowers/design/2026-11-18-our-price-slow-queries/measure.py` (из `backend/`) — чисто.
- Страж: `python scripts/check_agents_index.py` (из `backend/`) — 18 из 18.
- Итоговый замер укладывается в пороги выше; иначе — стоп и вопрос
  пользователю, а не правка порогов.

## Команды проверки

- По задаче: указаны в самой задаче.
- По фиче целиком: GitHub Actions на PR #90 (`gh pr checks 90`); локальный
  `just ci` не гоняется (режим реализации).
