# План: ближайшее значение на шкале

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Спека:** [`docs/superpowers/specs/2026-11-26-nearest-value-design.md`](../specs/2026-11-26-nearest-value-design.md) (редакция 3)
**Дизайн и макет:** [`docs/superpowers/design/2026-11-25-nearest-value.md`](../design/2026-11-25-nearest-value.md)
**Ветка:** `feat/nearest-value`, черновой PR #107
**Редакция 2** — по ревью Codex к `926b933`: снятие «пустой набор» ловит отдельное утверждение
«при найденной ступени аналоги непусты», а не неизменность ступени (задача 2); в ответе API
`nearest` проверяется у обеих шкал плиты (задача 3); проверка типов — `npx tsc -b --noEmit`, как
в CI (задачи 4, 5; `docs/pitfalls/runs.md`).

**Goal:** отпущенный главный параметр-шкала на ступенях 2 и 4 берёт ближайшие значения кругами до
трёх договоров, а экран называет взятые значения, направление и причину расширения.

**Architecture:** чистая проверка шкалы `services/value_scale.py`; шкалы семьи едут в
`FamilyLadderInfo.scales` из `_family_ladder_infos`. Сужение — чистая функция модуля лестницы,
вызывается в `ladder_for` после `first_pair`; итог — `WorkLadder.nearest` и `matched`. Ответ
разбора и тексты экрана строятся из них; каталог получает `ParameterOut.scale`.

**Tech Stack:** FastAPI, SQLAlchemy, pytest; React, shadcn/ui, vitest. Миграций нет.

## Global Constraints

- Правило шкалы, круги, `MIN_CONTRACTS = 3`, расстояние `max(v, t) / min(v, t)` в `Decimal`
  (`price_basis._CONTEXT`), шаг и итог пометки, `released`, `matched` — спека §1–§2 дословно.
- Ступень, `no_history_reason`, `base_reason`, `excluded_trace`, `parts_reference` от сужения не
  меняются (спека §2 п. 6, уточнения дизайна). Ступени 1 и 3, системы, файл подрядчику — без
  изменений.
- При пустом `FamilyLadderInfo.scales` поведение лестницы и ответа — побайтно прежнее (кроме
  новых полей со значениями по умолчанию): существующие наборы тестов зелёные без правок
  ожиданий, кроме мест, где фикстура обязана получить новое поле.
- Ответ API — поля спеки §3 дословно: `work_rung.nearest`, `contracts_count`,
  `match ∈ {required, ignored, same_set, nearest, matched}`, `nearest` у параметра, `side`
  ячейки, `nearest` у группы зоны, `ParameterOut.scale`. Новых маршрутов нет.
- Тексты экрана — спека §4 дословно; файлы текстов — `ourPriceCopy.ts`, `paramsCopy.ts`; фронт —
  только shadcn/ui.
- Скорость этапа и панели на `gca_dev` — не хуже +10 % к `main` (спека §5).
- Режим проверок — по риску (решение пользователя 28.10.2026, отступление от
  `docs/process/implementation.md`): критические свойства — задача 2 (ступень не меняется;
  при ступени набор непуст; сужение не трогает строки без отпущенной шкалы) — снимает оркестратор лично в копии дерева;
  узкое ревью (Opus) — у задачи 2; остальные — приёмка оркестратором (тесты, tsc, eslint, снимок
  стенда против макета). Большие соседние наборы исполнитель гоняет один раз в конце задачи.
  Итоговое ревью — Codex.

## Review Focus

1. Две шкалы, поздняя убирает значения ранней (КП 60 и W12; 40 с W8 в одном договоре, 90 с W12
   в трёх) — пометка первой «90 ↑», не «40–90 ↕»; вариант, где у первой не осталось чисел, —
   она в `released`, марка в `matched` — задача 2.
2. Значение КП есть только в договорах другого класса, своя ступень берёт B40 — абзац
   «почему» говорит «на этой ступени», а не «в истории» — задачи 2 и 4.
3. Отпущенная шкала у строки КП с составным значением («B25 или B30») или конфликтом — правило
   `same_set` и набор прежние, параметр в `released` — задача 2.
4. Значения «B30» и «В30» (латиница и кириллица) у одного параметра — одна форма, оба — круг
   расстояния 1; параметр `matched`, а не «ближайшее B30–В30» — задачи 1 и 2.
5. Выбранная целью строка-аналог с далёким значением после сужения — выбор сбрасывается
   причиной `analog_unavailable`, а не ломает ответ — задача 3.

## Структура файлов

- Create: `backend/services/value_scale.py`; Edit: `backend/crud/our_price_load.py`
  (`_family_ladder_infos`), `backend/crud/work_variants.py` (`ParameterOut`, чтение схемы).
- Edit: `backend/services/analog_ladder.py` (сужение, `NearestParam`, `WorkLadder`,
  `params_rule`), `backend/services/reference_blocks.py` (`ZoneGroup.nearest`).
- Edit: `backend/crud/our_price.py` (`_line_view`, `_params_view`, `_released_view`,
  `_reference_zones_view`).
- Create tests: `backend/tests/unit/test_value_scale.py`, `backend/tests/unit/test_nearest_ladder.py`,
  `backend/tests/integration/test_nearest_api.py`; Edit:
  `backend/tests/integration/test_our_price_load.py`,
  `backend/tests/integration/test_work_variants_schema.py`,
  `backend/tests/unit/test_variant_params_rule.py`.
- Frontend: `types/domain.ts`, `test/fixtures.ts`, `pages/tenders/our-price/{ourPriceCopy.ts,
  paramsCopy.ts,LineSheet.tsx,AnalogsTable.tsx,ReferenceBlocks.tsx,WorkParamsSection.tsx}`,
  `pages/families/schema-edit/ParameterEditor.tsx`; тесты рядом.
- Docs: `docs/reference/screens.md`, `docs/devlog/2026-11-26-nearest-value.md`
  (`schema.md` описывает таблицы, а не ответы — без правки: новых полей в базе нет).

## Решения плана, которых нет в спеке

1. Пять задач: шкала (1), сужение в лестнице (2), ответ API (3), типы и тексты фронта (4),
   компоненты фронта и каталог (5). Документы справочника, прогон на стенде против макета,
   скорость, снимки в обеих темах и devlog — финал оркестратора.
2. `Scale` живёт в `services/value_scale.py` рядом с `scale_numbers`; модуль лестницы его
   импортирует. Шкалы считаются для всех главных точных параметров, не только отпущенных:
   `side` ячейки (спека §3 п. 4) нужен и у совпавших колонок, а отметку администратор может
   поставить в любой момент.
3. Сужение — чистая функция `_narrow_scales` модуля лестницы, а не метод `_Search`: `_Search`
   читают `first_pair` и `excluded_trace`, которым сужение не нужно (спека, уточнения дизайна).
   Фильтр выпавших без НДС — `accepts` из того же результата, передаётся в `dropped_on`
   необязательным аргументом; без него `dropped_on` прежний.
4. Совпадение значений, разные тексты одного числа (B30/В30): в `values` — тексты всех взятых
   значений, порядок по числу, затем по id; «совпал» решает число, а не текст.
5. `params_rule` получает `nearest: Mapping[int, NearestParam]` и `matched: frozenset[int]`
   именованными аргументами со значениями по умолчанию — прочие вызовы и тесты не меняются.
6. `matchText` получает третий необязательный аргумент `nearest` — единственный вызов в
   `WorkParamsSection.tsx`; `tableNote` читает `p.nearest` из `Pick`.
7. «Справочные аналоги» (`crud/reference_search.py`) группы зон не сериализуют — только
   считают отметки; сужение действует там через `zone_groups` без правки файла.
8. Справочник: `docs/reference/screens.md` — подпись ступени, абзац «почему», колонка «При
   подборе», строка «Шкала…»; AGENTS.md не меняется (инвариантов фича не трогает).

## Задачи

### Task 1: шкала

**Files**
- Create: `backend/services/value_scale.py`
- Edit: `backend/crud/our_price_load.py` (`_family_ladder_infos`), `backend/services/analog_ladder.py`
  (поле `FamilyLadderInfo.scales`), `backend/crud/work_variants.py` (`ParameterOut.scale`)
- Test: `backend/tests/unit/test_value_scale.py` (новый),
  `backend/tests/integration/test_our_price_load.py`,
  `backend/tests/integration/test_work_variants_schema.py`

**Interfaces**
- Потребляет (существуют): `FamilyLadderInfo`, `FamilyParameter`, `FamilyParameterValue`,
  `ParameterOut`, `_family_ladder_infos`.
- Производит:

```python
# services/value_scale.py
@dataclass(frozen=True)
class Scale:
    numbers: Mapping[int, Decimal]  # value_id -> число
    texts: Mapping[int, str]        # value_id -> текст значения

def scale_numbers(values: Sequence[tuple[int, str]]) -> Scale | None   # (value_id, текст); None — не шкала

# services/analog_ladder.py — FamilyLadderInfo
scales: Mapping[int, Scale] = field(default_factory=dict)   # parameter_id -> шкала; только шкалы

# crud/work_variants.py — ParameterOut
scale: bool
```

**Утверждения** (спека §1, §6 п. 1)
- Шкала: «B25, B30»; «50, 60, 80»; «200 мм, 250 мм»; «EI60, EI90, EI120»; «2,5, 3» (число 2,5 —
  `Decimal("2.5")`); «B30, В40» (латиница и кириллица) — `Scale` с числами каждого значения.
- Не шкала (`None`): «600×600, 800×800»; «EI60 или EI90, EI120»; «до 100, 200»; «от 50, 100»;
  «50 мм, 5 см»; «B30, М300»; «0, 50, 100»; «КМ0, КМ1»; «B25 W6, B30 W6»; одно значение «B30».
- Загрузка: у главного точного параметра с B25 и B30 и составным «B25 или B30» — шкала из двух
  чисел (составное не участвует); снятое (`retired_at`) и слитое (`merged_into_id`) значение не
  участвуют; параметр-диапазон, второстепенный и архивный — не в `scales`; параметр на
  незамороженной версии — не в `scales`.
- Ответ схемы: `scale = true` у главного точного параметра-шкалы, `false` у нешкального, у
  второстепенного с числами и у диапазона.

**Имена**
- Заводятся: `services/value_scale.py`, `Scale`, `scale_numbers`, `FamilyLadderInfo.scales`,
  `ParameterOut.scale`.
- Существуют, проверено `grep`-ом: `_family_ladder_infos` (`crud/our_price_load.py:607`),
  `ParameterOut` (`crud/work_variants.py:119`), `FamilyLadderInfo` (`services/analog_ladder.py:100`).

**Проверка**
- `cd backend && uv run pytest tests/unit/test_value_scale.py` — ДО 0, ПОСЛЕ — утверждения
  задачи.
- `cd backend && TEST_DATABASE_URL=… uv run pytest tests/integration/test_our_price_load.py
  tests/integration/test_work_variants_schema.py` — ДО 145 + 289, ПОСЛЕ — больше на новые
  тесты; `uv run ruff check .`.

- [ ] Тесты (красные) → реализация → зелёные → коммит.

### Task 2: сужение в лестнице

**Files**
- Edit: `backend/services/analog_ladder.py`, `backend/services/reference_blocks.py`
- Test: `backend/tests/unit/test_nearest_ladder.py` (новый)

**Interfaces**
- Потребляет: `Scale`, `FamilyLadderInfo.scales` (Task 1); существуют `ladder_for`, `_Search`,
  `_Candidate`, `_releasable_ids`, `_form`, `price_basis._CONTEXT`, `ZoneGroup`, `zone_groups`.
- Производит:

```python
MIN_CONTRACTS = 3
Direction = Literal["up", "down", "both"]

@dataclass(frozen=True)
class NearestParam:
    parameter_id: int
    name: str
    kp_value: str
    values: tuple[str, ...]
    kp_contracts: int
    direction: Direction | None
    without_value: bool
    after: tuple[str, ...]

# WorkLadder
nearest: tuple[NearestParam, ...] = ()
matched: frozenset[int] = frozenset()

@dataclass(frozen=True)
class _Narrowed:
    picked: list[_Candidate]
    nearest: tuple[NearestParam, ...]
    matched: frozenset[int]
    #: parameter_id не пропущенной шкалы -> (value_id взятых кругов, был ли последний круг).
    accepts: Mapping[int, tuple[frozenset[int], bool]]
    #: Пропущенные шкалы и шкалы без чисел в окончательном наборе.
    released_scales: frozenset[int]

def _narrow_scales(kp: KpWork, info: FamilyLadderInfo | None, picked: Sequence[_Candidate]) -> _Narrowed
# _Search.dropped_on(scope, rung_no, picked_ids, accepts: Mapping[int, tuple[frozenset[int], bool]] | None = None)

# ZoneGroup
nearest: tuple[NearestParam, ...] = ()
```

**Утверждения** (спека §2, §6 п. 2–7; критические — п. 3 и 4 §6)
- Круги: КП B25 (шкала B25, B30, B35, B40), B25 в трёх договорах — взят только B25, параметр в
  `matched`, не в `nearest` и не в `released`; B25 в двух договорах и B30 в одном — взяты оба,
  `kp_contracts = 2`, `values = ("B25", "B30")`, `direction = up`; КП 60, кандидаты 40 и 90 в
  разных договорах (по одному) — один круг, оба взяты, `direction = both`; круги кончились при
  двух договорах — добавлены кандидаты без числа (не уточнено, конфликт, слитое значение),
  `without_value = True`; у кандидатов нет чисел — параметр в `released`, набор прежний.
- Пропуск: у строки КП параметр не уточнён, в `kp.conflicted`, составной или значение вне
  шкалы — параметр в `released`, набор равен набору при пустом `scales`.
- Две шкалы — плита B35 W12 (B40 W12 в двух договорах, B40 W8 в одном, B60 W12 в трёх): класс
  → B40, марка → W12 и W8, `nearest` в порядке параметров, у марки `after = ("<имя класса>",)`,
  `kp_contracts = 2`; с переставленными `main_parameter_ids` итог другой.
- Поздняя шкала убирает значение ранней: КП 60 W12; 40 W8 в одном договоре, 90 W12 в трёх —
  у первого параметра `values = ("90",)`, `direction = up`, марка в `matched`; вариант с
  кандидатами без числа первой шкалы вместо 90 — первый параметр в `released`, марка в
  `matched`.
- «B30» и «В30» у одного параметра, КП B30, по договору каждого и третий договор с B30 —
  параметр в `matched`.
- **Ступень не меняется** (критическое): на тех же входах `rung_no`, `zone_scope`,
  `no_history_reason`, `base_reason` и `excluded_trace` равны значениям при пустом `scales`.
  Ступень выбирается до сужения, поэтому снятие «пустой набор» эту проверку не роняет — его
  ловит следующее утверждение.
- **Набор непуст** (критическое): при найденной ступени (`rung_no` не `None`) `analogs` непусты
  на каждом входе задачи, включая две шкалы и последний круг; снятие — `_narrow_scales`,
  отдающая пустой набор, роняет этот тест.
- **Набор прежний** (критическое): строка, где шкала не отпущена, и отпущенный нешкальный
  параметр — `analogs` равны аналогам при пустом `scales`; ступени 1 и 3 — тоже.
- `released`: имена отпускаемых без `nearest` и `matched`, порядок `ordinal`; при пустом
  `scales` — прежний список.
- `vat_dropped`: договор без НДС с B60 при взятом круге B40 — не в `vat_dropped`, с B40 — в нём.
- `zone_groups`: у группы зоны `nearest` равен `ladder_for` строки КП с этой зоной.
- Цифры (`lowest`, `freshest`, `contractor`, `kp_ratio`, выбросы) считаются по суженному набору:
  самая низкая B60 при взятом B40 — не самая низкая.

**Имена**
- Заводятся: `MIN_CONTRACTS` (в `analog_ladder.py`), `Direction`, `NearestParam`, `_Narrowed`,
  `_narrow_scales`, `WorkLadder.nearest`, `WorkLadder.matched`, `ZoneGroup.nearest`, аргумент
  `accepts` у `_Search.dropped_on`.
- Существуют, проверено `grep`-ом: `ladder_for` (`analog_ladder.py:534`), `_Search.dropped_on`
  (`:722`), `_Search.first_pair` (`:736`), `excluded_trace` (`:745`), `_released_names` (`:937`),
  `ZoneGroup` (`reference_blocks.py:79`), `zone_groups` (`reference_blocks.py:108`).

**Проверка**
- `cd backend && uv run pytest tests/unit/test_nearest_ladder.py` — ДО 0, ПОСЛЕ — утверждения
  задачи.
- `cd backend && uv run pytest tests/unit/test_analog_ladder.py tests/unit/test_not_analog_ladder.py
  tests/unit/test_simplify_ladder.py tests/unit/test_ref_analogs_blocks.py` — ДО 308, ПОСЛЕ 308
  (без правок ожиданий); `uv run ruff check .`.

- [ ] Тесты (красные) → реализация → зелёные → коммит. Узкое ревью (Opus) и снятия
  оркестратора — после коммита.

### Task 3: ответ API

**Files**
- Edit: `backend/crud/our_price.py` (`_line_view`, `_params_view`, `_released_view`,
  `_reference_zones_view`), `backend/services/analog_ladder.py` (`MatchRule`, `params_rule`)
- Test: `backend/tests/unit/test_variant_params_rule.py`,
  `backend/tests/integration/test_nearest_api.py` (новый)

**Interfaces**
- Потребляет: `WorkLadder.nearest`, `WorkLadder.matched`, `ZoneGroup.nearest`, `NearestParam`,
  `FamilyLadderInfo.scales` (задачи 1–2).
- Производит:

```python
MatchRule = Literal["required", "ignored", "same_set", "nearest", "matched"]
def params_rule(..., nearest: Mapping[int, NearestParam] = {}, matched: frozenset[int] = frozenset()) -> ParamsRule

class NearestView(TypedDict):
    name: str; kp_value: str; values: list[str]; kp_contracts: int
    direction: Literal["up", "down", "both"] | None; without_value: bool; after: list[str]
# строка: work_rung.nearest: list[NearestView]; contracts_count: int
# параметр строки: nearest: {"values": list[str], "without_value": bool} | None
# ячейка released_values: side: Literal["up", "down"] | None
# группа reference.zones[]: nearest: list[NearestView]
```

**Утверждения** (спека §3, §6 п. 8)
- `params_rule` при ступени 2 или 4: параметр из `nearest` — `nearest`, из `matched` —
  `matched`, прочие — как сейчас; при основании `catalog` и ступенях 1, 3 новые значения не
  появляются; без новых аргументов — прежний результат.
- Ответ строки с плитой B35 W12 (сцена задачи 2): `work_rung.nearest` — два элемента в порядке
  параметров со всеми полями; `released` без них; `contracts_count` — число различных договоров
  аналогов; у параметра класса `match = nearest` и `nearest = {"values": ["B40"],
  "without_value": false}`; у параметра марки `match = nearest` и `nearest = {"values": ["W8",
  "W12"], "without_value": false}`; `nearest = null` — только у параметров вне
  `ladder.nearest`.
- Строка без ступени — `contracts_count = 0`, `work_rung = null`; ступень 1 — `nearest = []`.
- `side`: колонка шкалы, аналог B40 при КП B35 — `up`, W8 при W12 — `down`, совпавшее значение
  и нешкальная колонка — `null`, аналог без числа — `null`.
- Группа справочного блока по зонам несёт `nearest` той же формы.
- Выбор цели «аналог» на строке, которая после сужения не в наборе, — выбор не действует с
  причиной `analog_unavailable`.

**Имена**
- Заводятся: `NearestView`, значения `nearest` и `matched` у `MatchRule`, поля ответа выше.
- Существуют, проверено `grep`-ом: `_line_view` (`crud/our_price.py:925`), `_params_view`
  (`:856`), `_released_view` (`:1711`), `_reference_zones_view` (`:2113`), `params_rule`
  (`analog_ladder.py:290`), `analog_unavailable` (`services/our_price.py:250`).

**Проверка**
- `cd backend && uv run pytest tests/unit/test_variant_params_rule.py` — ДО 87, ПОСЛЕ больше.
- `cd backend && TEST_DATABASE_URL=… uv run pytest tests/integration/test_nearest_api.py` — ДО 0,
  ПОСЛЕ — утверждения задачи.
- Один раз в конце: `tests/integration/test_variant_params_api.py
  tests/integration/test_ref_analogs_panel.py tests/integration/test_our_price_offer_api.py` —
  ДО 191, ПОСЛЕ 191; `uv run ruff check .`.

- [ ] Тесты (красные) → реализация → зелёные → коммит.

### Task 4: типы и тексты фронта

**Files**
- Edit: `frontend/src/types/domain.ts`, `frontend/src/test/fixtures.ts`,
  `frontend/src/pages/tenders/our-price/ourPriceCopy.ts`,
  `frontend/src/pages/tenders/our-price/paramsCopy.ts`
- Test: `ourPriceCopy.test.ts`, `paramsCopy.test.ts` (рядом)

**Interfaces**
- Потребляет: поля ответа задачи 3, `ParameterOut.scale` (задача 1).
- Производит:

```ts
export interface NearestParam {
  name: string; kp_value: string; values: string[]; kp_contracts: number;
  direction: "up" | "down" | "both" | null; without_value: boolean; after: string[];
}
// WorkRung.nearest: NearestParam[]; ReferenceZoneGroup.nearest: NearestParam[]
// OurPriceLine.contracts_count: number
// ParamMatch = "required" | "ignored" | "same_set" | "nearest" | "matched"
// LineParam.nearest: { values: string[]; without_value: boolean } | null
// released_values[i].side: "up" | "down" | null; параметр схемы: scale: boolean
export function workRungLabel(rung: Pick<WorkRung, "no" | "released" | "other_class" | "nearest">): string
export function workRungCaption(line: Pick<OurPriceLine, "work_rung" | "analogs_count" | "contracts_count">): string | null
export function nearestWhy(nearest: NearestParam[]): string | null
export function matchText(match: ParamMatch | null, basis: ParamsBasis | null, nearest?: LineParam["nearest"]): string
export function sideText(side: "up" | "down"): string   // «выше, чем в КП» / «ниже, чем в КП»
```

**Утверждения** (спека §4 п. 1–5, §6 п. 9 — тексты дословно)
- `workRungLabel`: ступень 2 с плитой — «ближайшее: класс бетона по прочности B40 ↑, марка по
  водонепроницаемости W8–W12 ↓ · другой класс» (ступень 4); «и без значения» перед стрелкой;
  без `direction` — без стрелки; `nearest` и `released` вместе — «ближайшее: … · без учёта: …»;
  оба пусты — «те же значения параметров»; ступени 1 и 3 — «тот же вариант».
- `workRungCaption`: при непустом `nearest` — «… · 10 аналогов в 3 договорах»; при пустом —
  прежняя «… · N аналогов».
- `nearestWhy`: `kp_contracts = 0` — «Класс бетона по прочности B35 на этой ступени нет —
  взято B40, выше, чем в КП.»; `kp_contracts = 2` с `after` — «Марка по водонепроницаемости W12
  на этой ступени после отбора по классу бетона по прочности — только в 2 договорах, добавлено
  W8, ниже, чем в КП.»; `kp_contracts = 1` — «в 1 договоре»; `without_value` — «добавлено W8 и
  строки без значения», без других значений — «добавлено строки без значения»; `both` —
  «выше и ниже, чем в КП»; последняя фраза «Цены аналогов не пересчитаны.»; пустой список —
  `null`. Текст не содержит «в истории».
- `matchText`: `nearest` с одним значением — «ближайшее: B40», с двумя — «ближайшие: W8, W12»,
  с `without_value` — «… и без значения»; `matched` — «совпал»; прежние значения — прежние
  тексты.
- `tableNote`: `nearest` при основании `rung` — «— при подборе ближайшее: B40»; `matched` — без
  приписки.
- Фикстуры: новые поля со значениями по умолчанию (`nearest: []`, `contracts_count`, `side: null`,
  `nearest: null`, `scale: false`); `fixtures.test.ts` зелёный.

**Имена**
- Заводятся: `NearestParam` (тип), `nearestWhy`, `sideText`, поля типов выше.
- Существуют, проверено `grep`-ом: `workRungLabel` (`ourPriceCopy.ts:565`), `workRungCaption`
  (`:572`), `matchText`, `tableNote` (`paramsCopy.ts`), `lowerFirst`, `WorkRung`,
  `ReferenceZoneGroup`, `ParamMatch` (`domain.ts:3667`).

**Проверка**
- `cd frontend && npx vitest run src/pages/tenders/our-price/ourPriceCopy.test.ts
  src/pages/tenders/our-price/paramsCopy.test.ts src/test/fixtures.test.ts` (из `C:\`) — ДО
  85 + 64 + 111, ПОСЛЕ больше; `npx tsc -b --noEmit` (как в CI; без `-b` проверяет ноль файлов), `npx eslint` по правленым файлам.

- [ ] Тесты (красные) → реализация → зелёные → коммит.

### Task 5: компоненты фронта и каталог

**Files**
- Edit: `frontend/src/pages/tenders/our-price/{LineSheet.tsx,AnalogsTable.tsx,ReferenceBlocks.tsx,WorkParamsSection.tsx}`,
  `frontend/src/pages/families/schema-edit/ParameterEditor.tsx`
- Test: `LineSheet.work.test.tsx`, `AnalogsTable.test.tsx`, `ReferenceBlocks.test.tsx`,
  `WorkParamsSection.test.tsx`, `OfferLinesTable.test.tsx`, `ParameterEditor.releasable.test.tsx`

**Interfaces**
- Потребляет: `nearestWhy`, `sideText`, `workRungLabel`, `workRungCaption`, `matchText` (задача 4).
- Производит: абзац `data-testid="nearest-why"` в `WorkComparison`; строку каталога
  `data-testid="schema-edit-scale"`.

**Утверждения** (спека §4 п. 1–6)
- Панель строки с `nearest`: плашка — подпись ступени с «ближайшее: …»; абзац `nearest-why` с
  текстом `nearestWhy` рядом с абзацем зоны; без `nearest` абзаца нет.
- Таблица строк: подпись под вилкой — «… в 3 договорах» при `nearest`.
- Колонка «При подборе» панели — «ближайшее: B40» / «совпал».
- Ячейка отпущенного параметра с `side = up` — «B40» и «↑», скрытый текст «выше, чем в КП»
  (`sr-only`), подсветка `differs` прежняя; `side = null` — без стрелки.
- Справочный блок по зоне с `nearest` — подпись группы с «ближайшее: …».
- Окно правки схемы: у сохранённого главного параметра с `scale = true` под подсказкой флажка
  — «Шкала: отпущенному параметру сначала берутся ближайшие значения.»; у `scale = false`, у
  нового параметра и при роли «второстепенный» в черновике — строки нет.

**Имена**
- Заводятся: `data-testid` `nearest-why`, `schema-edit-scale`.
- Существуют, проверено `grep`-ом: `WorkComparison` (`LineSheet.tsx:530`), `zone-scope-why`,
  `schema-edit-releasable`, `releasedValueText`.

**Проверка**
- `cd frontend && npx vitest run` по шести файлам (из `C:\`) — ДО 77 + 45 + 27 + 14 + 70 + 7,
  ПОСЛЕ больше; `npx tsc -b --noEmit` (как в CI; без `-b` проверяет ноль файлов), `npx eslint` по правленым файлам.

- [ ] Тесты (красные) → реализация → зелёные → коммит.

### Финал (оркестратор)

- Снятия критических свойств задачи 2 в копии дерева (ступень не меняется, при ступени набор
  непуст, набор прежний без отпущенной шкалы).
- Стенд `gca_dev`: тротуарная плитка 60 мм — на нынешних отметках, как есть; плита B35 W12 и
  марш B25 — только после отпускания шкал, а это решение пользователя (дизайн §4.5): до него —
  на копии базы стенда, `gca_dev` не трогается. Значения и подписи — против макета; снимки в
  обеих темах.
- Скорость этапа и панели против `main` — не хуже +10 %.
- `docs/reference/screens.md`, devlog `docs/devlog/2026-11-26-nearest-value.md`; затем пуш и
  ревью Codex.

## Команды проверки

- По задаче: указаны в самой задаче.
- По фиче целиком: GitHub Actions (`just ci`), прогон на стенде и снимки по финалу.
