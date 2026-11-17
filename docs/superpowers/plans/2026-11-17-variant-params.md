# План: параметры варианта с названиями (А11)

**Спека:** `docs/superpowers/specs/2026-11-17-variant-params-design.md` (редакция 2; гейт 2 — Codex на `4df65cc` замечаний не имел, 17.11.2026)
**Дизайн:** `docs/superpowers/design/2026-11-17-variant-params.md` (редакция 2), макет — `.../2026-11-17-variant-params/mockup.html`
**Стоит на:** лестница аналогов, упрощение каталога, справочные аналоги — все в `main`
**Ветка:** `feat/variant-params`, черновой PR #89
**Статус:** редакция 1, 17.11.2026, ждёт гейта 3.

> Исполнителю: задачи идут по порядку; каждая — цикл TDD
> (`superpowers:test-driven-development`: тесты первыми, красный прогон
> показать). Адреса `§N` без уточнения — разделы спеки; «решение N» —
> пронумерованные решения этого плана. **Защит, дублирующих предикат, не
> класть.** Числа проверок — нижние границы. ДО сняты 17.11.2026 на `4df65cc`:
> `variant_params` — 0 совпадений в `backend/` и `frontend/src`. **Все новые
> бэкенд-тесты — в файлах `test_variant_params_*.py`, шаблон `-k
> variant_params`.** Тесты 4а, 4б, лестницы, упрощения каталога и справочных
> аналогов остаются зелёными; их ожидания правятся **только** там, где задача
> называет изменение.

## Режим реализации (как у справочных аналогов, с 28.10.2026)

- **Исполнители** — Sonnet; оркестратор (Opus) принимает задачу, коммитит,
  ведёт `progress.md`.
- **Проверки по риску.** Отдельное ревью (Opus, узкий бриф: свойства задачи
  и «что сделало бы этот тест ложно-зелёным») — только у задач с пометкой
  **«ревью»**: 1, 2, 3, 4. Остальные принимает оркестратор: тесты задачи,
  `ruff`, `tsc`, `eslint`, у фронта — снимок стенда рядом с макетом.
- **Снятия защит — только на критических свойствах (К1–К5 ниже = все строки
  §6 спеки), и делает их оркестратор лично**, после приёмки
  задачи-владельца, в копии дерева (стенд отдаёт живое дерево) по протоколу
  `BASELINE_GREEN / MUTATION_APPLIED / EXPECTED_TEST_FAILURE / RESTORED`,
  итог — в `progress.md`. Исполнитель предъявляет только TDD.
- **Это отступление от `docs/process/implementation.md`** («Распределение
  ответственности»: снятия всех `behavioral`-элементов — ревьюером задачи) —
  по решению пользователя 28.10.2026, как в планах лестницы, упрощения
  каталога и справочных аналогов; называется в devlog фичи. Процессный
  документ этим планом не меняется.
- **Прогоны.** В итерациях — только новые тесты и точечные файлы; наборы
  соседних фич (`-k "our_price or analog or ref_analogs or simplify"`)
  исполнитель гоняет **один раз в конце задачи**, ревьюер их не повторяет.
  Полный бэкенд и `just ci` локально не гоняются — проверяет GitHub Actions
  (`gh pr checks 89`), красное чинится следующим коммитом.
- **Без остановок** между задачами; стоп — пуш, внешнее ревью и вопросы, где
  допущение небезопасно.
- **Финальное ревью ветки** — Codex (комментарий в PR #89).

## Global Constraints

- **Цифры не меняются ни в одном сценарии** (§2.1): состояния строк, вилки,
  цели, плитки, итоги, файл подрядчику, комментарий; `variant_label` и
  `family` остаются в ответе. Лестница работ и ступени систем не правятся —
  новый код их только читает.
- **Одно правило «при подборе»** — функция в `services/analog_ladder.py` на
  тех же `_required_ids`, `_releasable_ids`, `_form`, что `_values_key`
  (§2.3); фронт правила не считает.
- **Одна функция показа значения** на фронте — для таблицы, панели и ячеек
  аналогов (§2.2, §2.7, §2.8).
- **Число запросов не зависит от числа строк:** `load_kp_lines` — плюс один
  запрос на ответ, панель — плюс один (§2.10).
- **Чтение не пишет:** `GET` разбора и панели.
- **Тексты** — точно как в спеке: «должен совпасть», «не учитывается», «можно
  не учитывать», «нужен такой же набор», «ещё не разобрано», «расходится по
  разделам», «не из списка: «…»», «не уточнено», «— прежнее», «—
  уточняется», «без учёта: …», «подробнее · ещё N», «свернуть»; подсказки
  колонки и строка под таблицей — дословно §2.3 и §2.8.
- **Фронтенд** — только shadcn/ui и `ui-domain`, уже стоящие в проекте; новых
  примитивов фича не ставит.
- **Фикстуры фронта — по спеке, не по типу:** `params` — всегда массив,
  `params_basis` — одно из трёх, `analog_columns` и `released_values` —
  всегда массивы.
- **В тестах — условные имена** объектов и подрядчиков; цены стенда в тесты и
  devlog не переносить.
- **Стенд `gca_dev`** — только чтение; учётки стенда не трогать; кириллицу в
  терминал не печатать.
- **`AGENTS.md` не меняется** (§2.9).

## Критические свойства (снятия оркестратора)

| № | Свойство (§6 спеки) | Задача | Снятие → ожидаемо красный тест |
|---|---|---|---|
| К1 | «При подборе» говорит то же, что делает лестница | 1, 3 | вернуть `ignored` для отпускаемого составного → Task 1: тест «ступень 2, отпускаемое составное → `same_set`»; Task 3: сквозной тест «каждый аналог ступени совпадает со строкой КП по всем `required` значением и по всем `same_set` формой» |
| К2 | Ненастоящий источник не выдаётся за настоящий | 2 | четыре независимых снятия: не читать `refresh_requested_at` → тест `refreshing`; отсутствие записи как `none` → тест `missing`; сопоставлять записи с вариантом прежней схемы → тест `old_schema`; не сравнивать `value_id` варианта и записи → тест «один главный разрешён вручную, другой `refreshing`, версия та же» (`stale` у первого, `manual` не отдан) |
| К3 | Пометка видна в таблице КП при пустом значении | 4, 5 | ставить пометку только к непустому основному тексту → Task 4: тест `paramText` «не уточнено — уточняется»; Task 5: тесты таблицы `refreshing + null` и `refreshing + off_list` |
| К4 | Основание правила | 1, 5 | отдавать `rung` вместо `catalog` у работы без ступени → Task 1: тест основания; Task 5: тест «у `catalog` приписки «не учитывается при подборе» нет» |
| К5 | Число запросов | 2, 3 | читать записи значений по строке → тест «`load_kp_lines` — одно число запросов на 2 и 20 строк» (Task 2); читать тексты значений аналогов по аналогу → тест «панель — одно число запросов на 1 и 5 аналогов» (Task 3) |

## Review Focus

1. **Система на варианте прежней схемы** — главные `old_schema`; уровень `v` →
   `required`, `f` → `ignored`; уровня `p` у неё не бывает (первого главного
   нет), правило на него не опирается (Task 1, 2).
2. **Отпускаемый параметр со сборным значением и конфликтом разделов** на
   ступени 2: форма читается как простая → `ignored`, а не `same_set`; в
   панели аналога — «расходится по разделам», `differs = true` (Task 1, 3).
3. **Ровно три параметра** — кнопки «подробнее» нет; четыре — «подробнее ·
   ещё 1»; ноль (строка без варианта, но с семьёй) — семья видна, параметров
   и кнопки нет (Task 5).
4. **Пустое значение у отпущенного параметра и у строки КП, и у аналога** —
   `differs = false`, ячейка «не уточнено» без подсветки (Task 3, 6).
5. **Имя параметра, начинающееся с аббревиатуры** — «МАФ» в «без учёта: …»
   остаётся заглавным, «Марка W» → «марка W»; однобуквенное имя не ломает
   правило (Task 4).

## Структура файлов

```
backend/
  services/analog_ladder.py         правка: MatchRule, ParamsBasis, ParamsRule, params_rule (Task 1);
                                    WorkHistory.lines_of (Task 3)
  crud/our_price_load.py            правка: LineParam, ParamSource, _VariantInfo.mains, _context_values,
                                    _current_secondaries, _line_params, catalog_text, load_value_texts;
                                    KpLineRow.secondary → params; _secondary_values удаляется (Task 2, 3)
  crud/our_price.py                 правка: _line_view — params, params_basis, без secondary (Task 3);
                                    build_line_view — analog_columns, released_values (Task 3)
  tests/unit/test_variant_params_rule.py              создаётся (Task 1)
  tests/integration/test_variant_params_load.py       создаётся (Task 2)
  tests/integration/test_variant_params_api.py        создаётся (Task 3)
  tests/integration/test_our_price_load.py, test_our_price_offer_api.py   правка ожиданий secondary (Task 2, 3)
frontend/src/
  types/domain.ts, test/fixtures.ts                   правка (Task 4)
  pages/tenders/our-price/
    paramsCopy.ts, paramsCopy.test.ts                 создаются (Task 4)
    ourPriceCopy.ts, ourPriceCopy.test.ts             правка: workRungLabel, −refReleased, −SHEET_FIELD.secondary (Task 4)
    LineParamsCell.tsx, LineParamsCell.test.tsx       создаются (Task 5)
    OfferLinesTable.tsx, OfferLinesTable.test.tsx     правка (Task 5)
    WorkParamsSection.tsx, WorkParamsSection.test.tsx создаются (Task 6)
    LineSheet.tsx, LineSheet.work.test.tsx            правка (Task 6)
    AnalogsTable.tsx, AnalogsTable.test.tsx           правка (Task 6)
docs/reference/screens.md                             правка (Task 7)
docs/devlog/2026-11-17-variant-params.md              создаётся (Task 7)
```

## Решения плана, которых нет в спеке

1. **Порядок задач:** правило (1) → параметры строки в загрузке (2) → ответы
   API (3) → фронт: типы и тексты (4) → таблица (5) → панель (6) →
   справочник, стенд, замеры, devlog (7). Правило первым: оно чистое и
   несёт К1.
2. **Правило — одна функция на работу и систему** `params_rule(...)`; на вход
   — идентификаторы главных **строки** (из `params`, по `ordinal` схемы
   варианта), а не схемы семьи, поэтому вариант прежней схемы проходит тем же
   путём. Уровень системы передаётся строкой `"v" | "p" | "f"`:
   `analog_ladder` не импортирует `services/our_price` (обратный импорт уже
   есть).
3. **`LineParam` несёт `parameter_id`** — внутреннее поле для правила; в
   ответ API не уходит (форма §2.4 без него).
4. **Главные — из `_variant_infos`:** его второй запрос (главные схем)
   получает `FamilyParameter.name`, третий уже несёт текст значения;
   `_VariantInfo` получает `mains` — без нового запроса. Записи значений и
   второстепенные действующих схем — два запроса вместо `_secondary_values`
   (§2.10).
5. **Текст `catalog_value`** собирает сервер функцией `catalog_text` по тем
   же словам, что фронт: значение, «не из списка: «…»», «расходится по
   разделам», «не уточнено».
6. **Значения аналогов в панели** — из `WorkHistoryLine` сцены (новый
   `WorkHistory.lines_of(family_id)`, без запроса), тексты — одним
   `load_value_texts(db, value_ids)`.
7. **Фронт:** тексты параметров — в новом `paramsCopy.ts` (`ourPriceCopy.ts`
   уже 750+ строк); ячейка таблицы — `LineParamsCell`, раздел панели —
   `WorkParamsSection`. `workRungLabel` остаётся в `ourPriceCopy.ts` и зовёт
   `lowerFirst` из `paramsCopy.ts`.
8. **`variantCaption`** остаётся (подзаголовок панели переходит на
   `line.family`, таблица — на семью отдельной строкой); если после Task 6 у
   неё не останется вызовов — удаляется вместе с тестами в той же задаче.

## Задачи

### Task 1: правило «при подборе» — **ревью**

**Files**
- Edit: `backend/services/analog_ladder.py`
- Test: `backend/tests/unit/test_variant_params_rule.py`

**Interfaces**
- Потребляет: `FamilyLadderInfo`, `Form`, `RungNo`, `_required_ids`, `_releasable_ids`, `_form` (существуют).
- Производит:

```python
MatchRule = Literal["required", "ignored", "same_set"]
ParamsBasis = Literal["rung", "catalog"]

@dataclass(frozen=True)
class ParamsRule:
    basis: ParamsBasis | None
    #: parameter_id главного -> правило; пусто при basis = None
    main: Mapping[int, MatchRule]
    #: правило второстепенных: "ignored" при basis ≠ None, иначе None
    secondary: MatchRule | None

def params_rule(
    *,
    main_ids: Sequence[int],              # главные строки по ordinal схемы варианта
    has_variant: bool,
    is_system: bool,
    schema_id: int | None,                # схема варианта строки
    forms: Mapping[int, Form],
    conflicted: frozenset[int],
    info: FamilyLadderInfo | None,        # семья на действующей схеме
    work_rung: RungNo | None,
    system_level: Literal["v", "p", "f"] | None,
) -> ParamsRule
```

**Утверждения** (оракул — таблица §2.3, а не функции лестницы)
- работа, ступень 1 и 3 → все главные `required`, основание `rung`;
- работа, ступень 2 и 4 → неотпускаемый `required`; отпускаемый с составной
  формой `same_set` (К1); отпускаемый простой `ignored`; отпускаемый
  составной **с конфликтом** — `ignored`;
- работа без ступени, схема строки = действующая и есть отпускаемый →
  основание `catalog`, правила как у ступени 2 (К4: основание не `rung`);
- работа без ступени на прежней схеме → `catalog`, все `required`; то же у
  семьи без отпускаемых (`releasable` у всех `NULL` или `FALSE`);
- система `v` → все `required`; `p` → первый главный `required`, остальные
  `ignored`; `f` → все `ignored`; основание `rung`;
- система без ступени и строка без варианта → основание `None`, `main` пуст,
  `secondary` `None`;
- второстепенные → `ignored` при любом основании ≠ `None`;
- `params_rule` и `_values_key` согласны: на ступени 2 для любого набора из
  трёх главных (каждый — неотпускаемый, отпускаемый простой, отпускаемый
  составной, с конфликтом и без) два набора значений с равным `_values_key`
  совпадают по всем `required` значением и по всем `same_set` формой — перебор
  всех сочетаний ролей.

**Имена**
- Заводятся: `MatchRule`, `ParamsBasis`, `ParamsRule`, `params_rule`.
- Существуют, проверено `grep`-ом: `_required_ids`, `_releasable_ids`, `_form`, `_values_key`, `FamilyLadderInfo`, `RungNo`, `Form`.

**Проверка**
- `just test-unit-k variant_params` — ДО 0, ПОСЛЕ ≥ 16.
- `just test-unit-k "analog or ladder"` — зелёный без правок ожиданий.

### Task 2: параметры строки в загрузке КП — **ревью**

**Files**
- Edit: `backend/crud/our_price_load.py`
- Edit (ожидания `secondary` → `params`): `backend/tests/integration/test_our_price_load.py` (строки с `row.secondary`, тест числа запросов `test_load_kp_lines_runs_the_same_statements_for_2_and_20_lines`)
- Test: `backend/tests/integration/test_variant_params_load.py`

**Interfaces**
- Потребляет: `ContextParameterValue` (`source`, `off_list_text`, `refresh_requested_at`, `value_id`), `FamilyParameter`, `FamilyParameterSchema`, `SchemaStatus`, `_variant_infos`, `_UNKNOWN_LABEL`, `count_statements` (существуют).
- Производит:

```python
ParamSource = Literal["name", "path", "manual", "path_conflict", "none", "off_list",
                      "missing", "old_schema", "stale"]

@dataclass(frozen=True)
class LineParam:
    parameter_id: int
    name: str
    role: Literal["main", "secondary"]
    value: str | None
    off_list: str | None
    composite: bool
    source: ParamSource
    refreshing: bool
    catalog_value: str | None

@dataclass(frozen=True)
class VariantMain:
    parameter_id: int
    name: str
    value_id: int | None
    text: str | None

# _VariantInfo: + mains: tuple[VariantMain, ...]  — активные главные схемы варианта по ordinal
# KpLineRow: secondary удаляется; + params: tuple[LineParam, ...] = ()

@dataclass(frozen=True)
class _ContextValue:
    value_id: int | None
    text: str | None
    off_list: str | None
    source: str
    refreshing: bool

def _context_values(db: Session, context_ids: Sequence[int]) -> dict[int, dict[int, _ContextValue]]
def _current_secondaries(db: Session, family_ids: Sequence[int]) -> dict[int, tuple[tuple[int, str], ...]]
def catalog_text(value: str | None, source: str, off_list: str | None) -> str
def _line_params(info: _VariantInfo, values: Mapping[int, _ContextValue],
                 secondaries: Sequence[tuple[int, str]]) -> tuple[LineParam, ...]
```

**Утверждения**
- состав и порядок: главные схемы варианта по `ordinal`, затем второстепенные
  действующей схемы по `ordinal`; архивные не входят; строка без варианта —
  `params == ()`; второстепенный без записи **входит** (`missing`);
- `value` главного — текст значения варианта, составного — его текст;
  `composite` — только у варианта на действующей схеме с составной формой;
- шесть источников записи проходят как есть (`name`, `path`, `manual`,
  `path_conflict`, `none`, `off_list`); `off_list` — текст записи;
- `missing`: записи нет → `missing`, `refreshing = false` (К2: не `none`);
- `old_schema`: вариант на прежней схеме → все его главные `old_schema`,
  `refreshing = false`, `off_list = None`, даже при записи у параметра с тем
  же `ordinal` действующей схемы (К2); второстепенные такой строки — по
  записям действующей схемы;
- `stale`: сценарий «правка схемы разрешила один главный (`manual`, отметка
  снята), другой главный `refreshing`, версия та же» → у первого `source =
  stale`, `value` — текст варианта, `off_list = None`, `catalog_value` —
  новое значение записи, источник `manual` не отдан (К2); у второго — свой
  источник и `refreshing = true`;
- `stale` не возникает от пустых значений: `NULL` записи `none`, `off_list`,
  `path_conflict` против `NULL` варианта — исходный источник;
- `catalog_text`: значение → оно; `off_list` → «не из списка: «текст»»;
  `path_conflict` → «расходится по разделам»; пусто → «не уточнено»;
  `catalog_value` у всех источников, кроме `stale`, — `None`;
- `refreshing` — по `refresh_requested_at` записи, источник при этом прежний
  (`none` / `off_list` с текстом), у главного и второстепенного (К2);
- число запросов `load_kp_lines` одно на 2 и 20 строк, у строк есть и главные,
  и второстепенные (К5). Замер исполнителя, не тест: на фикстуре этого теста
  до правки и после — ровно плюс один запрос, число — в отчёт задачи.

**Имена**
- Заводятся: `ParamSource`, `LineParam`, `VariantMain`, `_VariantInfo.mains`, `KpLineRow.params`, `_ContextValue`, `_context_values`, `_current_secondaries`, `catalog_text`, `_line_params`.
- Удаляются: `KpLineRow.secondary`, `_secondary_values`.
- Существуют, проверено `grep`-ом: `load_kp_lines`, `_variant_infos`, `_UNKNOWN_LABEL`, `ContextParameterValue`, `count_statements`.

**Проверка**
- `just test-int-local-k variant_params` — ДО 0, ПОСЛЕ ≥ 14.
- `just test-int-local-k our_price_load` — зелёный; ожидания `secondary` переписаны на `params`, остальные не тронуты.

### Task 3: ответы разбора и панели — **ревью**

**Files**
- Edit: `backend/crud/our_price.py`, `backend/crud/our_price_load.py` (`load_value_texts`), `backend/services/analog_ladder.py` (`WorkHistory.lines_of`)
- Edit (ожидания): `backend/tests/integration/test_our_price_offer_api.py` (`LINE_KEYS`: −`secondary`, +`params`, +`params_basis`; строки 452, 578)
- Test: `backend/tests/integration/test_variant_params_api.py`

**Interfaces**
- Потребляет: `params_rule`, `ParamsRule` (Task 1); `LineParam`, `KpLineRow.params` (Task 2); `_line_view`, `build_line_view`, `_analogs_view`, `_releasable_ids`, `WorkHistoryLine` (существуют).
- Производит:

```python
def WorkHistory.lines_of(self, family_id: int) -> Sequence[WorkHistoryLine]
def load_value_texts(db: Session, value_ids: Collection[int]) -> dict[int, str]
def _params_view(row: KpLineRow, result: LineResult,
                 families: Mapping[int, FamilyLadderInfo]) -> tuple[str | None, list[dict]]   # (params_basis, params)
def _released_view(db: Session, scene: _Scene, row: KpLineRow, result: LineResult
                   ) -> tuple[list[dict], dict[int, list[dict]]]   # (analog_columns, analog_id -> released_values)
```

Ответ строки (`_line_view`): + `params_basis`, + `params` (поля §2.4, без
`parameter_id`), − `secondary`. Ответ панели: + `analog_columns`; каждый
элемент `analogs` + `released_values`.

**Утверждения**
- разбор оффера и панель отдают у каждой строки `params_basis` и `params` по
  форме §2.4, `secondary` нет; `params` строки разбора и `line` панели равны;
- `params_basis`: работа со ступенью и система со ступенью → `rung`; работа с
  вариантом без ступени → `catalog` (К4); система без ступени и строка без
  варианта → `null`, у всех параметров `match = null`;
- сквозной К1: ступень 2 с отпускаемым составным у строки КП → каждый аналог
  в `analogs` совпадает со строкой КП по всем `required` значением и по всем
  `same_set` формой (сверка по `WorkHistoryLine` аналогов сцены);
- `analog_columns` — имена отпускаемых семьи по `ordinal` только при ступени 2
  или 4 (равны `work_rung.released`); при 1/3, у системы, без ступени — `[]`,
  и `released_values` у аналогов — `[]`;
- `released_values`: текст значения аналога (составное — текстом),
  `conflict` по `path_conflict` строки договора, `differs` — `value_id`
  разный либо конфликт у любой из двух; пустое у обеих — `differs = false`;
- панель: одно число запросов на 1 и 5 аналогов ступени 2 (К5). Замер
  исполнителя, не тест: до правки и после — ровно плюс один запрос;
- цифры не меняются: наборы 4а, 4б, лестницы, упрощения и справочных аналогов
  зелёные без правок ожиданий, кроме названных в Files этой задачи.

**Имена**
- Заводятся: `WorkHistory.lines_of`, `load_value_texts`, `_params_view`, `_released_view`, поля ответа `params`, `params_basis`, `analog_columns`, `released_values`.
- Существуют, проверено `grep`-ом: `_line_view`, `build_line_view`, `_analogs_view`, `LINE_KEYS`, `WorkHistory`, `count_statements`.

**Проверка**
- `just test-int-local-k variant_params` — ДО ≥ 14 (Task 2), ПОСЛЕ ≥ 26.
- Один раз в конце: `just test-int-local-k "our_price or analog or ref_analogs or simplify"` — зелёный; правки ожиданий — только названные в Files.

### Task 4: фронт — типы, фикстуры, тексты параметров — **ревью**

**Files**
- Create: `frontend/src/pages/tenders/our-price/paramsCopy.ts`, `paramsCopy.test.ts`
- Edit: `frontend/src/types/domain.ts`, `frontend/src/test/fixtures.ts`, `frontend/src/pages/tenders/our-price/ourPriceCopy.ts`, `ourPriceCopy.test.ts`

**Interfaces**
- Потребляет: форма ответа Task 3; `WorkRung`, `OurPriceLine`, `OurPriceAnalog`, `OurPriceLineView`, `ourPriceLineFixture`, `ourPriceAnalogFixture`, `workLineFixture`, `ourPriceLineViewFixture` (существуют).
- Производит:

```ts
// domain.ts
export type ParamSource = "name" | "path" | "manual" | "path_conflict" | "none" | "off_list"
  | "missing" | "old_schema" | "stale";
export type ParamMatch = "required" | "ignored" | "same_set";
export type ParamsBasis = "rung" | "catalog";
export interface LineParam { name: string; role: "main" | "secondary"; value: string | null;
  off_list: string | null; composite: boolean; source: ParamSource; refreshing: boolean;
  catalog_value: string | null; match: ParamMatch | null; }
// OurPriceLine: + params_basis: ParamsBasis | null; + params: LineParam[]; − secondary
// OurPriceAnalog: + released_values: Array<{ value: string | null; conflict: boolean; differs: boolean }>
// OurPriceLineView: + analog_columns: Array<{ name: string }>

// paramsCopy.ts
export function paramText(p: Pick<LineParam, "source" | "value" | "off_list" | "refreshing">): string
export function paramWarn(p: Pick<LineParam, "source" | "value" | "refreshing">): boolean
export function paramSourceText(p: Pick<LineParam, "source" | "refreshing" | "catalog_value">): string
export function matchText(match: ParamMatch | null, basis: ParamsBasis | null): string
export function tableNote(p: Pick<LineParam, "role" | "match" | "source">, basis: ParamsBasis | null): string | null
export function releasedValueText(v: { value: string | null; conflict: boolean }): string
export function lowerFirst(name: string): string
export const PARAMS_COPY: { more: (n: number) => string; collapse: string; secondaryRole: string;
  sectionTitle: string; columns: { param: string; value: string; source: string; match: string };
  catalogHint: string; matchHint: string; pathNote: string }   // тексты — дословно §2.3, §2.7, §2.8
```

**Утверждения**
- `paramText` по таблице §2.2: `missing` → «ещё не разобрано»;
  `path_conflict` → «расходится по разделам»; `off_list` → «не из списка:
  «текст»»; `value = null` → «не уточнено»; иначе значение; затем «—
  прежнее» у `stale` и «— уточняется» у `refreshing` **и при пустом
  основном тексте** (К3): «не уточнено — уточняется», «не из списка:
  «бетон М350» — уточняется», «не уточнено — прежнее»;
- `paramWarn` истинна у «не уточнено», «ещё не разобрано», «расходится по
  разделам», `stale`, `refreshing`; ложна у обычного значения;
- `paramSourceText`: шесть источников записи и `missing`, `old_schema` —
  тексты колонки «Откуда» §2.2; `stale` — «прежнее значение, ждёт пересчёта
  варианта; в каталоге уже: «…»»; `refreshing` дописывает «, уточняется»;
- `matchText` — таблица подписей §2.3 по основанию; `null` → «—»;
- `tableNote`: «— не учитывается при подборе» у главного `ignored` и «— при
  подборе нужен такой же набор» у `same_set` только при `rung` (К4: при
  `catalog` — `null`); у второстепенного — `null`; «(из разделов)» у `path`;
- `lowerFirst`: «Марка по водонепроницаемости» → «марка по
  водонепроницаемости»; «МАФ» → «МАФ»; «Марка W» → «марка W»; одна буква →
  строчная;
- `workRungLabel`: 1/3 — «тот же вариант»; 2/4 — «без учёта: марка W,
  толщина»; «· другой класс» как сейчас; тесты `ourPriceCopy.test.ts` на
  «без Марка W» переписаны;
- `refReleased` и `SHEET_FIELD.secondary` удалены, `grep` по `frontend/src`
  — 0;
- фикстуры: `ourPriceLineFixture` несёт `params_basis` и `params` (три
  параметра, без `secondary`), `ourPriceAnalogFixture` — `released_values:
  []`, `ourPriceLineViewFixture` — `analog_columns: []`; `tsc` зелёный по
  всему фронту.

**Имена**
- Заводятся: всё в блоке «Производит».
- Удаляются: `refReleased`, `SHEET_FIELD.secondary`, `OurPriceLine.secondary`.
- Существуют, проверено `grep`-ом: `workRungLabel`, `workRungCaption`, `variantCaption`, `SHEET_FIELD`, фикстуры выше.

**Проверка**
- `just test-frontend-file src/pages/tenders/our-price/paramsCopy.test.ts` — ДО нет файла, ПОСЛЕ ≥ 30.
- `just test-frontend-file src/pages/tenders/our-price/ourPriceCopy.test.ts` — зелёный; `cd frontend && npx tsc --noEmit` — без ошибок.

### Task 5: таблица «Против нашей цены»

**Files**
- Create: `frontend/src/pages/tenders/our-price/LineParamsCell.tsx`, `LineParamsCell.test.tsx`
- Edit: `frontend/src/pages/tenders/our-price/OfferLinesTable.tsx`, `OfferLinesTable.test.tsx`

**Interfaces**
- Потребляет: `paramText`, `paramWarn`, `tableNote`, `PARAMS_COPY` (Task 4); `OurPriceLine` (Task 4).
- Производит:

```tsx
export function LineParamsCell(props: { family: string | null; params: LineParam[];
  basis: ParamsBasis | null }): JSX.Element | null
```

**Утверждения**
- ячейка «Работа»: наименование, путь (как сейчас), семья отдельной строкой,
  под ней параметры «название: значение» столбиком в порядке `params`;
- видны первые три; при четырёх и больше — «подробнее · ещё N»; по ней видны
  все, кнопка «свернуть»; ровно три — кнопки нет; ноль — только семья;
- нажатие «подробнее» и «свернуть» **не открывает панель** (обработчик
  клика строки не вызван);
- состояние раскрытия у каждой строки своё: раскрытие одной не раскрывает
  соседнюю;
- `refreshing + null` → «не уточнено — уточняется», `refreshing + off_list` →
  «не из списка: «…» — уточняется» — **в таблице** (К3); предупреждающий
  цвет у значений, где `paramWarn` истинна;
- приписки «— не учитывается при подборе» / «— при подборе нужен такой же
  набор» только при `params_basis = rung` (К4: строка `catalog` с `ignored`
  приписки не несёт); «(из разделов)» у `path`;
- значка ⓘ со второстепенными нет; прежние тесты ⓘ в
  `OfferLinesTable.test.tsx` переписаны на параметры.

**Имена**
- Заводятся: `LineParamsCell`.
- Существуют, проверено `grep`-ом: `OfferLinesTable`, `variantCaption`, кнопки shadcn в `components/ui`.

**Проверка**
- `just test-frontend-file src/pages/tenders/our-price/LineParamsCell.test.tsx` — ДО нет файла, ПОСЛЕ ≥ 10.
- `just test-frontend-file src/pages/tenders/our-price/OfferLinesTable.test.tsx` — зелёный; `tsc`, `eslint` — без ошибок.
- Снимок стенда: оффер 76, строки «Плиты пандусов…» и «Корпус 1,2,3,4,5,7,9…» рядом с макетом; наезда колонок нет (замер ширин в браузере).

### Task 6: панель строки

**Files**
- Create: `frontend/src/pages/tenders/our-price/WorkParamsSection.tsx`, `WorkParamsSection.test.tsx`
- Edit: `frontend/src/pages/tenders/our-price/LineSheet.tsx`, `LineSheet.work.test.tsx`, `AnalogsTable.tsx`, `AnalogsTable.test.tsx`

**Interfaces**
- Потребляет: `paramText`, `paramWarn`, `paramSourceText`, `matchText`, `releasedValueText`, `PARAMS_COPY` (Task 4); `OurPriceLineView.analog_columns`, `OurPriceAnalog.released_values` (Task 4).
- Производит:

```tsx
export function WorkParamsSection(props: { params: LineParam[]; basis: ParamsBasis | null;
  kind: "work" | "system" }): JSX.Element | null
// AnalogsTable: + проп columns: Array<{ name: string }>
```

**Утверждения**
- подзаголовок панели — семья (`line.family`), без склейки значений; без
  семьи — как сейчас;
- строки «Второстепенные» в «Ценах» нет;
- раздел «Работа» стоит сразу после «Цен» и только при непустом `params`:
  таблица «Параметр | В строке КП | Откуда | При подборе»; у второстепенного
  — «второстепенный» под названием; «Откуда» — `paramSourceText`, «При
  подборе» — `matchText`; у `catalog` в заголовке колонки «(аналогов нет —
  настройка каталога)»;
- подсказка заголовка колонки «При подборе» — дословно §2.3, только у работы;
- строка про «из разделов смет» под таблицей — только если есть `source =
  path`;
- таблица аналогов: колонка на каждый элемент `analog_columns`, ячейка —
  `releasedValueText`, подсветка у `differs`; при пустом `analog_columns`
  колонок нет;
- бейдж ступени и справочные блоки — «без учёта: …» (общая
  `workRungLabel`).

**Имена**
- Заводятся: `WorkParamsSection`, проп `AnalogsTable.columns`.
- Существуют, проверено `grep`-ом: `LineSheet`, `AnalogsTable`, `ReferenceBlocks`, `SHEET_SECTION`, `workRungLabel`.

**Проверка**
- `just test-frontend-file src/pages/tenders/our-price/WorkParamsSection.test.tsx` — ДО нет файла, ПОСЛЕ ≥ 9.
- `LineSheet.work.test.tsx`, `AnalogsTable.test.tsx` — зелёные; `tsc`, `eslint` — без ошибок.
- Снимок стенда: панель «Плиты пандусов…» (ступень 2, три аналога с W8, W12, W12 — колонка марки, подсветка) и «Корпус…» (сборная, `catalog`) рядом с макетом.

### Task 7: справочник, стенд, замеры, devlog

**Files**
- Edit: `docs/reference/screens.md`
- Create: `docs/devlog/2026-11-17-variant-params.md`

**Утверждения**
- `screens.md`: ячейка «Работа» таблицы разбора, раздел «Работа» панели,
  колонки аналогов, подпись «без учёта: …»; места с ⓘ, «Второстепенные», «без
  …» и «отпущен» найдены `grep`-ом и переписаны;
- стенд (DoD §5 п. 2): оффер 76 — «Плиты пандусов…», «Корпус 1,2,3,4,5,7,9…»,
  фасад с «не уточнено»; снимки таблицы и панели рядом с макетом — в devlog
  ссылкой на файлы вне гита;
- замеры §2.10 на оффере 76: размер ответа ≤ +10 % к 6 049 094 байт; время —
  медиана трёх прогонов ≤ +5 % к медиане трёх прогонов `main` в том же сеансе
  стенда; числа — в devlog;
- devlog: что сделано, отступление от `implementation.md` (режим по риску),
  итоги снятий К1–К5, замеры, расхождения с планом;
- страж `just check-agents-index` зелёный.

**Проверка**
- `just check-agents-index` — зелёный; `gh pr checks 89` — зелёный после пуша.

## Команды проверки

- По задаче: указаны в самой задаче.
- По фиче целиком: GitHub Actions на PR #89 (локальный `just ci` не гоняется —
  решение пользователя 11.10.2026), плюс стенд по Task 7.
