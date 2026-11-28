# План: «должен совпасть / не учитывается» в панели строки (А12)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Спека:** [`docs/superpowers/specs/2026-11-28-line-param-toggle-design.md`](../specs/2026-11-28-line-param-toggle-design.md)
**Дизайн:** [`docs/superpowers/design/2026-11-28-line-param-toggle.md`](../design/2026-11-28-line-param-toggle.md), макет — [`mockup.html`](../design/2026-11-28-line-param-toggle/mockup.html)
**Ветка:** `feat/line-param-toggle`, черновой PR #114

**Goal:** в панели строки КП у главного параметра переключатель «должен совпасть / не учитывать»;
панель отвечает аналогами по правилу пользователя, ничего не записывая.

**Architecture:** лестница получает признак `values_only` в сведениях семьи — только ступени 2 и 4,
ступень 2 и при пустом наборе отпускаемых. Маршрут панели принимает `ignore`; при правиле, отличном
от каталога, `build_line_view` строит сцену просмотра — своя `WorkHistory` семьи строки с
подменёнными сведениями, без сохранённых решений, оценка одной строки — и отдаёт ответ той же
формы плюс поле `rule`. Фронт держит правило в состоянии панели, шлёт его в запросе и в
просмотре прячет блоки решений.

**Tech Stack:** FastAPI, SQLAlchemy 2.0.35, pytest; React, TanStack Query, shadcn/ui
(`toggle-group`), Vitest. Миграций нет.

## Global Constraints

- Без `ignore` и при `ignore`, равном правилу каталога, ответ панели побайтно прежний, кроме
  новых `line.params[*].parameter_id` и `rule` (спека §1, §2, критерий 1).
- Просмотр ничего не пишет и не сбрасывает кэш этапа; общая `WorkHistory` и `families` сцены не
  мутируются (спека §1, критерии 8, 9).
- Код отказа дословно: `rule_not_applicable` (422); тексты отказов 1–4 — дословно из спеки §2.
- Тексты экрана дословно из спеки §5: «должен совпасть», «не учитывать», «Просмотр: ваше правило
  подбора», «Цифры строки в таблице, итоги, цель и файл подрядчику не меняются.», «Как считает
  система», «сейчас совпал: найдены точные аналоги», «по-прежнему нужен такой же набор: {value}»,
  «изменено вами · в каталоге: …», «По вашему правилу аналогов тоже нет.», фраза сборного
  значения §5 п. 7. Тексты — в `paramsCopy.ts` / `ourPriceCopy.ts`.
- Компоненты — только shadcn/ui (`ToggleGroup` уже есть в `components/ui/toggle-group.tsx`).
- Режим проверок — по риску (решение пользователя 28.10.2026). Задачи 1–3 проходят узкое ревью
  (Opus, `task-reviewer`); защиты снимает оркестратор лично в копии дерева (раздел «Снятия»).
  Итоговое ревью — Codex.
- Исполнитель гоняет только наборы из «Проверки» своей задачи и сверяет число тестов до и после.
  Полный бэкенд и `just ci` локально не запускаются — их гоняет GitHub Actions.
- Тестовая база: `TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test"`.
  Стенд `gca_dev` не трогается; замер финала — только чтение.

## Review Focus

1. Параметр с конфликтом разделов (`path_conflict`) в строке КП, пользователь пометил его «должен
   совпасть»: ступень заблокирована — панель «По вашему правилу аналогов тоже нет.», а не 500 —
   задача 2.
2. Пока панель открыта, схему семьи перевыпустили: запрос с прежними id даёт 422
   `rule_not_applicable`; панель показывает текст отказа и кнопку «Как считает система», а не
   вечную загрузку — задача 3.
3. Обычный пользователь (не администратор) переключает и получает просмотр — задача 2
   (`member_client`), задача 3 (переключатели видны без `canEdit`).
4. У строки сохранена цель-аналог: в просмотре цели нет и `line.choice` как без решений; после
   «Как считает система» цель снова на месте — задачи 2 и 3.
5. Сняты все главные параметры: пул — вся семья любого значения в своей зоне и классе, подпись
   «без учёта: …» со всеми именами по порядку схемы — задача 1 (лестница), задача 2 (ответ).

## Структура файлов

- Edit: `backend/services/analog_ladder.py` — `values_only` (задача 1).
- Edit: `backend/crud/our_price.py` — разбор `ignore`, сцена просмотра, поле `rule`,
  `parameter_id` (задача 2); `backend/routers/tenders.py` — параметр запроса (задача 2).
- Edit (фронт, задача 3): `types/domain.ts`, `services/api/domain.ts`, `services/queries.ts`,
  `services/queryKeys.ts`, `pages/tenders/our-price/LineSheet.tsx`, `WorkParamsSection.tsx`,
  `paramsCopy.ts`, `ourPriceCopy.ts`, `test/fixtures.ts`.
- Тесты: `backend/tests/unit/test_line_rule_ladder.py`, `backend/tests/integration/test_line_rule_api.py`,
  `frontend/src/pages/tenders/our-price/LineSheet.rule.test.tsx` (новые); дополнения в
  `paramsCopy.test.ts`, `WorkParamsSection.test.tsx`.

## Решения плана, которых нет в спеке

- Признак `values_only` — поле `FamilyLadderInfo`, не аргумент `ladder_for` (спека §3 п. 2 поправлена
  той же веткой, ред. 2).
- `releasable` в `_params_view` берётся из сведений **каталога**: `build_line_view` передаёт в
  `_params_view` исходные `scene.families` для флага, подменённые — для правила (`params_rule`).
- Оценка строки просмотра — `evaluate_offer` по списку из одной строки (`[row.line]`) со сценой
  просмотра и `saved={}`. Оракул в тестах задачи 2: результат той же строки при оценке всего
  оффера со сценой просмотра совпадает.

## Задачи

### Task 1: лестница «только значения»

**Files**
- Edit: `backend/services/analog_ladder.py`
- Test: `backend/tests/unit/test_line_rule_ladder.py` (новый; помощники — как в
  `test_nearest_ladder.py`: `info_of`, `history_of`, `dataclasses.replace(info, …)`)

**Interfaces**
- Производит:

```python
@dataclass(frozen=True)
class FamilyLadderInfo:
    ...
    #: Правило пользователя панели (спека А12 §3 п. 2): только ступени 2 и 4; ступень 2 и при пустом наборе.
    values_only: bool = False
```

- Поведение при `values_only=True`: `_Search.pairs()` и `_dropped_on_first_rung` обходят
  `(scope, rung)` только с `rung in (2, 4)`; `releasable_ladder = info is not None and
  kp.schema_id == info.schema_id` (без условия «есть отпускаемый»); `excluded_trace` — те же
  пары. При `False` — побайтно прежнее поведение.

**Утверждения** (спека §3, критерии 2–6)
- Строка с аналогами на ступени 1 (тот же вариант в классе тендера): при `values_only` и
  `releasable_ids={p2}` — `rung_no == 2`, в пул входит строка другого варианта с теми же
  значениями неотпускаемых и другим `p2`.
- `values_only`, `releasable_ids=∅`: `rung_no == 2`, `released == ()`; в пул входит вариант с теми
  же главными значениями и другой второстепенной частью; строка с другим значением любого
  главного — нет.
- Ужесточение: каталог отпускает `p2`, правило `releasable_ids=∅` — строка с другим `p2` из пула
  уходит.
- Снятая шкала (`scales` у `p2`, КП `B35`, история `B40`, `B60`): `nearest` называет `p2`, в
  наборе только `B40`; снятый не-шкала — все значения; сборное значение снятого — ключ формы,
  строка договора простого значения не берётся.
- Порядок: своя зона раньше чужой; ступень 4 только при пустой 2; `zone_required` — только своя;
  строка из `kp.excluded` не в пуле.
- Сняты все главные: `released` — все имена по порядку схемы (Review Focus 5).
- `values_only=False` — существующие наборы `test_analog_ladder.py`, `test_nearest_ladder.py`,
  `test_variant_params_rule.py` зелёные без правок ожиданий.

**Проверка**
- `cd backend && uv run pytest tests/unit/test_line_rule_ladder.py tests/unit/test_analog_ladder.py tests/unit/test_nearest_ladder.py tests/unit/test_variant_params_rule.py`.
  ДО — 0 + 153 + 86 + 99, ПОСЛЕ — то же плюс новые. `uv run ruff check .`

- [ ] Тесты (красные) → реализация → зелёные → коммит.

### Task 2: маршрут и ответ просмотра

**Files**
- Edit: `backend/crud/our_price.py`, `backend/routers/tenders.py`
- Test: `backend/tests/integration/test_line_rule_api.py` (новый; сцены — по образцу
  `test_nearest_api.py`: `World`, `Stage`, `_panel`, `member_client`)

**Interfaces**
- Потребляет: `FamilyLadderInfo.values_only` (задача 1).
- Производит:

```python
# routers/tenders.py — our_price_line: новый параметр
ignore: str | None = Query(default=None)

# crud/our_price.py
CODE_RULE_NOT_APPLICABLE = "rule_not_applicable"
def build_line_view(db, tender_id, round_id, offer_id, position_item_id, *,
                    level: str | None, scope: str | None, inflation_series_id: int | None,
                    ignore: str | None = None) -> dict: ...
def _parse_ignore(raw: str, info: FamilyLadderInfo) -> frozenset[int]: ...  # отказ 3 спеки §2
```

- Ответ: `line.params[*].parameter_id: int`; верхнее `rule: None | {"catalog_ignored": list[int],
  "preview": None | {"ignored": [{"parameter_id", "name", "by"}], "required_by_user":
  [{"parameter_id", "name"}]}}` — дословно спека §4.

**Утверждения** (спека §2, §4, критерии 1–4, 7–10)
- Критерий 1: `ignore` из отпускаемых каталога в обратном порядке с повтором — текст ответа равен
  ответу без `ignore`; `rule.preview is None`.
- Критерий 2: строка со ступенью 1, `ignore` = каталог + ещё параметр — `line.work_rung.no == 2`,
  `rule.preview.ignored` с `by` «catalog»/«user»; `analog_columns` — колонки снятых.
- Критерий 3: ужесточение — пропадает аналог с другим значением; `required_by_user` его называет.
- Критерий 4: `ignore=` у семьи с отпускаемыми — ступень 2, `released` пуст; у семьи без
  отпускаемых — ответ равен ответу без `ignore`.
- Критерий 7: четыре отказа — `422`, `code == "rule_not_applicable"`, свой текст каждый (системная
  строка, комплект, строка без варианта; прежняя схема; чужой id и «abc»; `ignore` с `level`).
- Критерий 8: серия вызовов с разными `ignore` — `stage_scene_cache.generation()` тот же, число
  строк `kp_line_targets`, `kp_line_agreements`, `kp_line_comments`, `kp_family_rungs`,
  `kp_sent_files`, `family_parameters` и `releasable` у параметров не изменились; ответы разбора
  участника и экрана этапа побайтно прежние. Тест — на `committing_session_factory` с включённым
  кэшем этапа (как `test_stage_cache_api.py`), иначе поколение не проверяется.
- Критерий 9: обычная панель строки после просмотра побайтно равна обычной до него.
- Критерий 10: `rule is None` у системы, комплекта, строки без варианта, без лестницы (без цены),
  на прежней схеме; иначе `catalog_ignored` — отпускаемые каталога по порядку схемы.
- `line.params[*].releasable` в просмотре — по каталогу (решение плана).
- Сохранённая цель-аналог: в просмотре `line.choice` как без решений; обычный ответ — с целью
  (Review Focus 4).
- Конфликт разделов у параметра «должен совпасть» — 200, `state == "no_history"` (Review Focus 1).
- Обычный пользователь (`member_client`) получает просмотр (Review Focus 3).
- Оракул: строка просмотра равна той же строке оценки всего оффера со сценой просмотра.
- Пустые в просмотре: `group_lines`, `reference_zones`, `reference_links`, `parts_reference`.

**Проверка**
- `cd backend && uv run pytest tests/integration/test_line_rule_api.py tests/integration/test_our_price_offer_api.py tests/integration/test_nearest_api.py tests/integration/test_variant_params_api.py tests/integration/test_stage_cache_api.py`.
  ДО — 0 + 149 + 13 + 26 + 24. Ожидания существующих наборов меняются только на новые поля
  `parameter_id` и `rule` (если сравнивают ответ целиком). `uv run ruff check .`

- [ ] Тесты (красные) → реализация → зелёные → коммит.

### Task 3: экран

**Files**
- Edit: `frontend/src/types/domain.ts`, `services/api/domain.ts`, `services/queries.ts`,
  `services/queryKeys.ts`, `pages/tenders/our-price/LineSheet.tsx`, `WorkParamsSection.tsx`,
  `paramsCopy.ts`, `ourPriceCopy.ts`, `src/test/fixtures.ts`
- Test: `pages/tenders/our-price/LineSheet.rule.test.tsx` (новый), `paramsCopy.test.ts`,
  `WorkParamsSection.test.tsx`

**Interfaces**
- Потребляет: ответ задачи 2.
- Производит:

```ts
// types/domain.ts
export interface LineParam { ...; parameter_id: number; }
export interface LineRulePreview {
  ignored: Array<{ parameter_id: number; name: string; by: "user" | "catalog" }>;
  required_by_user: Array<{ parameter_id: number; name: string }>;
}
export interface LineRule { catalog_ignored: number[]; preview: LineRulePreview | null; }
// OurPriceLineView: rule: LineRule | null

// services/queries.ts
useOurPriceLine(tenderId, roundId, offerId, positionItemId, rung, seriesId, ignore: number[] | null)
// queryKeys.ts: ourPriceLine(..., ignore) — сегмент: отсортированный список или null

// paramsCopy.ts
export function applyNote(param: LineParam, position: "required" | "ignored"): string | null
```

**Утверждения** (спека §5, критерий 11)
- Переключатели только у главных и только при `rule !== null`; положение — `catalog_ignored`.
- Переключение шлёт запрос с `ignore` (строка через запятую); набор, равный `catalog_ignored`, —
  `ignore = null`, ключ равен обычному.
- `applyNote`: четыре случая спеки §5 п. 2 дословно; при совпадении положения и применения — `null`.
- Плашка при `rule.preview !== null`: заголовок, перечень с «(вы)»/«(каталог)», «Должен совпасть: …»,
  фраза о неизменных итогах, кнопка «Как считает система» возвращает обычный запрос.
- Пометка «изменено вами · в каталоге: …» только у отличающихся от каталога.
- В просмотре скрыты блоки спеки §5 п. 6; нет кнопок выбора цели, «комплект», «не аналог»; у
  администратора нет кнопок изменения зоны в `LineZoneBlock`, в обычном ответе — есть.
- Фразы «нет аналогов» §5 п. 7 (обычная и сборная).
- Смена строки сбрасывает правило; повторное открытие той же строки — положение каталога.
- Отказ 422 в просмотре: текст отказа и кнопка «Как считает система» (Review Focus 2).
- Обычный пользователь видит переключатели (Review Focus 3); цель возвращается после сброса
  (Review Focus 4).

**Проверка**
- `cd frontend && npx vitest run src/pages/tenders/our-price/` (запускать из пути с заглавной
  буквой диска `C:\…`: при `c:\…` vitest не находит тесты), `npx tsc -b`, `npx eslint src/pages/tenders/our-price src/services src/types`.
  Число тестов каталога до и после — в отчёте.

- [ ] Тесты (красные) → реализация → зелёные → коммит.

## Снятия (оркестратор, копия дерева)

Каждое снятие обязано уронить названный тест:

| Снятие | Падает |
|---|---|
| `values_only` не пропускает ступени 1 и 3 | задача 1, строка со ступенью 1; задача 2, критерий 2 |
| `releasable_ladder` требует отпускаемый и при `values_only` | задача 1, пустой набор; задача 2, критерий 4 |
| нормализация «правило = каталог» | задача 2, критерий 1 и вторая половина критерия 4 |
| сцена просмотра мутирует общие `work_history` / `families` вместо копии | задача 2, критерий 9 |
| в оценку просмотра идут сохранённые решения (`saved`) | задача 2, цель в просмотре |
| `releasable` в просмотре из подменённых сведений | задача 2, `releasable` по каталогу |
| отказ при `ignore` вместе с `level`/`scope` | задача 2, критерий 7 |
| `canEdit` зоны в просмотре не снят | задача 3, кнопки зоны |
| `ignore = null` при наборе, равном каталогу | задача 3, ключ равен обычному |

## Финал (оркестратор)

- Замер §6 спеки скриптом в `docs/superpowers/design/2026-11-28-line-param-toggle/` на `gca_dev`
  только чтением: тёплая панель с `ignore` на оффере 76 (тендер 7, этап 23, ряд 1), медиана из трёх
  — не дольше 1,5 с; строки — витраж 223872 и жб 223663.
- Снимок стенда (Playwright через системный Chrome) панели витража в обычном виде и в просмотре —
  вид чисел и плашки тестам не виден.
- Devlog `docs/devlog/2026-11-28-line-param-toggle.md`; карта — пункт А12 и п. 9 «Если начинать
  завтра». `AGENTS.md` не меняется.

## Команды проверки

- По задаче: в самой задаче.
- По фиче целиком: `just ci` в GitHub Actions; замер и снимок — в финале.
