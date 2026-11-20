# План: зона как предпочтение подбора, а не фильтр

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Спека:** [`docs/superpowers/specs/2026-11-20-zone-as-preference-design.md`](../specs/2026-11-20-zone-as-preference-design.md) (редакция 2)
**Дизайн, макет, замер:** [`docs/superpowers/design/2026-11-20-zone-as-preference.md`](../design/2026-11-20-zone-as-preference.md) (редакция 3)
**Ветка:** `feat/zone-as-preference`, черновой PR #93
**Редакция 2** — по ревью Codex к `6e4a849` (P2): своя зона со ступенью,
где все кандидаты — выбросы, не уходит в чужие зоны (Review Focus 2а,
задача 2 утверждение 7а, снятие в финале).

**Goal:** аналоги строке КП ищутся сначала в своей зоне, затем в других
(и в строках договоров без зоны); строка без зоны сравнивается со всеми
зонами; семьи с флажком «зона обязательна» остаются на прежнем правиле.

**Architecture:** подбор (`services/analog_ladder.py`) получает область
кандидатов `own → other` или `any`; загрузка перестаёт отсекать строки
договоров без зоны и читает флажки семей; состояние `no_zone` остаётся только
у семей с флажком. Флажок — колонка `work_families` (миграция 0028 со
стартовым списком), свой маршрут и правило слияния. Фронт показывает
область меткой и пояснением, флажок — блоком в карточке семьи.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, pytest; React, TanStack Query
v5, shadcn/ui (`switch`, `badge` — уже стоят), vitest.

## Global Constraints

- Своя зона на любой ступени побеждает чужую на любой (спека §1 п. 2);
  внутри области правила ступени, класса, выбросов, остатка и цифр — без
  изменений.
- Строка договора без зоны — кандидат только в `other` и `any` и только у
  семьи без флажка (спека §1 п. 3).
- `no_zone` — только `zone is None and zone_required` (спека §2 п. 2);
  правило `in_progress` не меняется (§2 п. 3).
- Миграция 0028: колонки, `CHECK`, стартовый список — спека §4 п. 1
  дословно; названия — `двери`, `облицовка керамогранитом`, `покраска стен`,
  `вывоз и утилизация грунта`, `светильники`, `монтаж воздуховодов
  вентиляции`, `изоляция воздуховодов`.
- Маршрут флажка, отказы и слияние — спека §4 п. 2–4.
- Тексты экрана — спека §5 дословно; тексты на фронте — в `ourPriceCopy.ts`
  (экран КП) и `pages/families/labels.ts` (страница «Семьи»).
- Фронт — только компоненты shadcn/ui, уже стоящие в проекте.
- Режим проверок — по риску (решение пользователя 28.10.2026, отступление
  от `docs/process/implementation.md` по его слову 10.11.2026): критические
  свойства спеки §7 п. 1–6 снимает оркестратор лично в копии дерева (стенд
  отдаёт живое дерево); отдельное узкое ревью (Opus) — у задач 2 и 3, они
  двигают денежные цифры; остальные — приёмка оркестратором (тесты, `tsc`,
  `eslint`, снимок стенда против макета). Большие соседние наборы
  исполнитель гоняет один раз в конце задачи. Итоговое ревью — Codex.

## Review Focus

1. У строки КП в `own` кандидат только на ступени 4 (другой класс), в
   `other` — на ступени 1: побеждает `own`, ступень 4 — задача 2.
2. Все кандидаты `other` — выбросы: строка уходит в «Разные единицы», а не
   ищет дальше и не возвращается в `own` — задача 2.
2а. Обратный случай (ред. 2, Codex): в `own` ступень найдена, но все её
   кандидаты — выбросы, а в `other` есть нормальный аналог — строка остаётся
   `own` и «Разные единицы», в `other` не уходит: переход решает «ступень
   найдена», а не «цена посчитана» — задача 2, снятие в финале.
3. Строка КП без зоны у семьи с флажком при остановленном захвате —
   `no_zone` и «ждёт решения», не подбор — задача 3.
4. Слияние источника с флажком в цель без флажка, у которой есть собственные
   строки КП, — их сравнение после слияния идёт только в своей зоне — задача 1
   (флажок) и задача 3 (подбор читает флажок на момент сцены).
5. Семья в стартовом списке есть в двух единицах («Светильники» на стенде) —
   флажок у обеих — задача 1.

## Структура файлов

- Create: `backend/alembic/versions/2026_11_21_0028-family_zone_required.py`
- Edit: `backend/models.py` (`WorkFamily`, `CK_FAMILY_ZONE_REQUIRED_AUTHOR`)
- Edit: `backend/services/work_families.py` (`set_zone_required`, правило в `merge_families`)
- Edit: `backend/crud/semantic.py` (строка семьи), `backend/routers/semantic.py` (маршрут)
- Edit: `backend/services/analog_ladder.py`, `backend/services/our_price.py`,
  `backend/crud/our_price_load.py`, `backend/crud/our_price.py`
- Create: `backend/tests/integration/test_zone_required_schema.py`
- Edit tests: `tests/integration/test_work_families.py`,
  `tests/integration/test_work_variants_family_merge.py`,
  `tests/unit/test_analog_ladder.py`, `tests/unit/test_our_price_ladder.py`,
  `tests/unit/test_simplify_ladder.py`, `tests/integration/test_our_price_load.py`,
  `tests/integration/test_our_price_signal.py`,
  `tests/integration/test_our_price_offer_api.py`,
  `tests/integration/test_references_api.py`
- Edit: `frontend/src/types/domain.ts`, `frontend/src/services/api/domain.ts`
  (`semanticApi`), `frontend/src/services/queries.ts`
- Edit: `frontend/src/pages/tenders/our-price/{ourPriceCopy.ts,OfferLinesTable.tsx,LineSheet.tsx,AnalogsTable.tsx,PartsReference.tsx}` и их тесты
- Edit: `frontend/src/pages/families/{FamiliesTab.tsx,labels.ts}`;
  Create: `FamiliesTab.zoneRequired.test.tsx`
- Create: `docs/superpowers/design/2026-11-20-zone-as-preference/snapshot_lines.py`
- Docs: `docs/reference/{schema.md,money-axes.md,screens.md}`, `AGENTS.md`,
  `docs/devlog/2026-11-20-zone-as-preference.md`

## Решения плана, которых нет в спеке

1. Пять задач и подготовка: флажок (1), чистый подбор (2), загрузка,
   состояние и ответ (3), экран КП (4), страница «Семьи» (5); прогон
   «до/после», снимки стенда и devlog — финал оркестратора. Подбор и загрузка
   разделены: подбор проверяется юнит-тестами без базы, а ревьюер может
   принять один и вернуть другой.
2. Область — внутренний параметр `ladder_for`: пул семьи хранит кандидатов
   по ключу `(zone, …)` как сегодня, `zone` бывает `None`; область `other` —
   объединение срезов по всем ключам, кроме зоны строки, `any` — по всем.
   Порядок кандидатов внутри объединения не важен: справка сортируется
   сегодняшним ключом.
3. Флажок в подбор идёт как `frozenset[int]` семей
   (`WorkHistory.zone_required`) и как `KpWork/KpLine.zone_required`: пул
   строгой семьи не берёт строк без зоны, строгая строка не идёт дальше
   `own`.
4. `PUT …/zone-required` — отдельный маршрут, а не поле `PATCH /families`:
   своя запись автора и проверка «то же значение».
5. Снимок «до» для прогона спеки §7 п. 13 снимает оркестратор кодом ветки
   **до задачи 2** (подбор ещё прежний; задача 1 его не трогает) —
   отдельным скриптом рядом с дизайном, вывод — в `tasks/zone-pref-run/`
   (не под гитом).

## Подготовка (оркестратор, до задачи 2)

- [ ] Скрипт `snapshot_lines.py` (рядом с `measure_zone.py`, тот же
  принцип: READ ONLY, импорт приложения, без подмен): по всем КП базы —
  `build_offer_view`, строки-работы, на строку проекция спеки §7 п. 13
  (`state`, `work_rung`, `range`, `lowest`, `freshest`, `contractor`,
  `analogs_count`, `to_lowest`, `no_history_reason`) в JSON; режим
  `compare BEFORE AFTER` — нормализация `work_rung` по спеке (прежние поля +
  отдельная проверка `zone_scope == "own"`), счётчики переходов состояний.
- [ ] Снимок «до» на `gca_dev`: `tasks/zone-pref-run/before.json`.

## Задачи

### Task 1: флажок семьи «зона обязательна»

**Files:** миграция 0028; `models.py`; `services/work_families.py`;
`crud/semantic.py`; `routers/semantic.py`; тесты
`test_zone_required_schema.py` (новый), `test_work_families.py`,
`test_work_variants_family_merge.py`; `docs/reference/schema.md`;
`AGENTS.md` (роли `admin`: «флажок семьи «зона обязательна»» со ссылкой на
спеку §4; новая ревизия в шапке).

**Interfaces**
- Потребляет (существуют): `WorkFamily`, `merge_families`,
  `REFUSE_FAMILY_NOT_FOUND`, `REFUSE_UPDATE_ARCHIVED`, `WorkFamilyError`,
  `require_admin`, `_mutating`, `_serialize_family`, `get_family_row`.
- Производит:

```python
# models.py
CK_FAMILY_ZONE_REQUIRED_AUTHOR = "zone_required_changed_by IS NULL OR zone_required_changed_at IS NOT NULL"
# WorkFamily: zone_required (Boolean, NOT NULL, server_default false),
#             zone_required_changed_by (Integer FK users.id RESTRICT, NULL),
#             zone_required_changed_at (DateTime(timezone=True), NULL)
# CheckConstraint(..., name="ck_work_families_zone_required_author")
# миграция: ZONE_REQUIRED_SEED: tuple[str, ...] — семь названий спеки §4 п. 1 в нижнем регистре

# services/work_families.py
def set_zone_required(db: Session, *, family_id: int, value: bool, actor_id: int) -> WorkFamily

# routers/semantic.py
class ZoneRequiredRequest(BaseModel):
    zone_required: bool
# PUT /api/v1/semantic/families/{family_id}/zone-required -> строка семьи

# строка семьи (get_family_row / list_families): + "zone_required": bool,
#   "zone_required_changed_at": str | None, "zone_required_changed_by": {"id": int, "email": str} | None
```

**Утверждения**
1. Миграция: семья с названием из списка (любой регистр и пробелы по краям)
   в статусе `draft`/`active` — `zone_required = true`, `changed_at` не
   пусто, `changed_by` пусто; та же в архиве — `false`; две семьи одного
   названия в разных единицах — обе `true`; прочие — `false`; база без
   таких семей проходит; ненайденные названия печатаются в вывод; `downgrade`
   снимает три колонки и ограничение (scratch-база, как
   `test_ref_analogs_schema.py`).
2. `CHECK`: `changed_by` без `changed_at` — `IntegrityError` с именем
   ограничения; `changed_at` без автора — допустимо; выражение модели равно
   выражению миграции (паритет, как `TestParityWithMigration`).
3. `set_zone_required`: смена значения пишет значение, автора и время;
   то же значение — без смены автора и времени; семьи нет —
   `WorkFamilyError(REFUSE_FAMILY_NOT_FOUND)`; архивная —
   `REFUSE_UPDATE_ARCHIVED`; строка семьи блокируется `FOR UPDATE`.
4. Маршрут: `admin` — 200 и строка семьи с тремя полями; `member` — 403; 404
   и 422 с кодами п. 3; `GET /families` отдаёт три поля у каждой строки.
5. Слияние: флажок у источника, у цели нет — у цели `true`, автор —
   сливающий, время — момент слияния; у цели есть — цель не меняется; ни у
   кого — `false`; перенос контекста в другую семью флажков не меняет.

**Проверка:** `pytest tests/integration/test_zone_required_schema.py
tests/integration/test_work_families.py
tests/integration/test_work_variants_family_merge.py`; `ruff check .`;
страж документации `uv run python scripts/check_agents_index.py`.

- [ ] Тесты по утверждениям (красные) → реализация → зелёные → документация → коммит.

### Task 2: подбор по областям (`services/analog_ladder.py`)

**Files:** `services/analog_ladder.py`; тесты `tests/unit/test_analog_ladder.py`,
`tests/unit/test_simplify_ladder.py` (справка «по частям»).

**Interfaces**
- Потребляет (существуют): `ladder_for`, `evaluate_work_ladder`,
  `WorkHistory`, `_FamilyPool`, `_no_history_reason`, `parts_reference`,
  `PartReference`, `Zone`.
- Производит:

```python
ZoneScope = Literal["own", "other", "any"]
# KpWork: zone_required: bool = False
# WorkHistoryLine.zone: Zone | None — None теперь допустим у кандидата
# Analog: zone: Zone | None
# WorkLadder: zone_scope: ZoneScope | None = None   # None <=> rung_no is None
# PartReference: zone_scope: ZoneScope | None
class WorkHistory:
    def __init__(self, lines, families, contracts, *, factors, zone_required: frozenset[int] = frozenset()) -> None
```

**Утверждения**
1. Строка с зоной, семья без флажка: кандидат в `own` только на ступени 4, в
   `other` — на ступени 1 → ступень 4, `zone_scope = "own"`, аналоги только
   своей зоны (Review Focus 1).
2. В `own` ничего ни на одной ступени → ступени 1–4 по `other`: кандидаты
   двух чужих зон и строка без зоны — одна выборка (минимум, медиана,
   выбросы по ней), `zone_scope = "other"`, `Analog.zone` у каждого — его
   зона.
3. Строка договора без зоны не попадает в `own` никогда (строка КП с зоной
   `underground`, единственный кандидат — без зоны → `other`, а не `own`).
4. Строка КП без зоны (`zone = None`, без флажка) → ступени по `any`, включая
   строки без зоны; `zone_scope = "any"`.
5. Семья с флажком (`zone_required` в `WorkHistory` и у `KpWork`): только
   `own`; кандидат без зоны не берётся нигде; при пустой своей зоне —
   `rung_no is None`, `no_history_reason == "no_zone_match"` как сегодня.
6. Семья без флажка: `no_zone_match` не выдаётся ни при каком входе; пустые
   области дают `no_family`/`path_conflict`/`composite`/`no_params`.
7. Все кандидаты `other` — выбросы: `all_outliers`, область `other`,
   возврата в `own` нет (Review Focus 2).
7а. В `own` ступень найдена и все её кандидаты — выбросы, в `other` на той же
   и на первой ступени есть кандидат без выброса → `zone_scope = "own"`,
   `all_outliers`, аналоги только своей зоны, `lowest is None` (Review
   Focus 2а).
8. `vat_dropped`: со ступенью — в той же области; без ступени — первая пара
   «область, ступень» в порядке подбора (`own` 1–4, затем `other` 1–4).
9. Справка «по частям»: часть с кандидатами в `own` — `own`; без них —
   `other`; строка без зоны — `any`; семья с флажком — только `own`, часть
   без кандидатов — `zone_scope is None`.
10. Существующие тесты лестницы зелёные без правки ожиданий (входы с одной
    зоной у всех кандидатов дают прежний ответ и `zone_scope = "own"`).

**Проверка:** `pytest tests/unit/test_analog_ladder.py
tests/unit/test_simplify_ladder.py tests/unit/test_our_price_ladder.py`;
`ruff check .`.

- [ ] Тесты по утверждениям (красные) → реализация → зелёные → коммит.

### Task 3: загрузка, состояние строки и ответ API

**Files:** `crud/our_price_load.py`, `services/our_price.py`,
`crud/our_price.py`; тесты `test_our_price_load.py`,
`test_our_price_signal.py`, `test_our_price_offer_api.py`,
`test_references_api.py`, `tests/unit/test_our_price_ladder.py`;
`docs/reference/money-axes.md` («Аналоги работы» и «Справочная строка»:
область, строки без зоны, флажок).

**Interfaces**
- Потребляет: задачу 2 (`ZoneScope`, `WorkHistory(zone_required=…)`,
  `KpWork.zone_required`, `Analog.zone`, `WorkLadder.zone_scope`,
  `PartReference.zone_scope`); задачу 1 (`WorkFamily.zone_required`).
- Производит:

```python
# crud/our_price_load.py
def _zone_required_families(db: Session) -> frozenset[int]
# services/our_price.py — KpLine: zone_required: bool = False
# ответ: line.work_rung.zone_scope; analogs[].zone; parts_reference[].parts[].zone_scope
```

**Утверждения**
1. `load_work_history` отдаёт строку договора с цепочкой без зоны
   (`zone is None`); строка без цепочки не приходит.
2. `KpLine.zone_required` и `WorkHistory.zone_required` — по флажку семьи
   варианта; смена флажка видна следующей сцене (кэша нет).
3. Семья без флажка: строка без зоны и без живого задания →
   подбор `any`, состояние по ступени (`comparable` в фикстуре);
   `waiting_admin` ложно, счётчик плашки её не считает.
4. Приоритет строки без зоны, по фикстуре на состояние, для семьи без флажка
   и с флажком: остановка захвата при живом задании — `any` / `no_zone` +
   «ждёт решения»; живое задание — `in_progress`; живое в паузу провайдера
   (`budget_paused_until` в будущем) — `in_progress`; предложение ниже
   порога, «не знаю», `error`, `privacy_hold`, ответа нет — `any` / `no_zone`
   (Review Focus 3).
5. Ответ разбора, этапа и панели: `work_rung.zone_scope` у сравнимой строки;
   `analogs[].zone` в панели; `parts[].zone_scope`; у семьи без флажка
   `no_history_reason` никогда не `no_zone_match` и нет `other_zones`; у
   семьи с флажком — справка А и `other_zones`, как сегодня.
6. Строка, сравнимая по своей зоне, — ответ тот же, что до фичи, кроме
   `zone_scope = "own"` (одна фикстура, проверка полей проекции спеки §7
   п. 13).
7. Строка со справочной целью, ставшая сравнимой: цель действует,
   подсказка `own_analogs` истинна.
8. Флажок слитой семьи: после слияния источника с флажком строка цели без
   своей зоны не берёт чужие зоны (Review Focus 4).

**Проверка:** `pytest tests/integration/test_our_price_load.py
tests/integration/test_our_price_signal.py
tests/integration/test_our_price_offer_api.py
tests/integration/test_references_api.py tests/unit`; один раз в конце —
соседние наборы `test_our_price_round_api.py`,
`test_our_price_inflation.py`, `test_analog_chains.py`; `ruff check .`.

- [ ] Тесты по утверждениям (красные) → реализация → зелёные → документация → коммит.

### Task 4: экран «Против нашей цены»

**Files:** `types/domain.ts`; `ourPriceCopy.ts`; `OfferLinesTable.tsx`;
`LineSheet.tsx` (`WorkComparison`, тексты строгой семьи); `AnalogsTable.tsx`;
`PartsReference.tsx`; фикстуры (`zone_scope: "own"`, `zone: null` по
умолчанию); тесты `OfferLinesTable.test.tsx`, `LineSheet.work.test.tsx`,
`AnalogsTable.test.tsx`, `PartsReference.test.tsx`, `LineZoneBlock.test.tsx`
(тексты подсказки и охвата); `docs/reference/screens.md` (абзацы аналогов и
справочных аналогов).

**Interfaces**
- Потребляет: поля ответа задачи 3.
- Производит:

```ts
export type ZoneScope = "own" | "other" | "any";
// WorkRung: zone_scope: ZoneScope; OurPriceAnalog: zone: SectionZone | null;
// часть справки «по частям»: zone_scope: ZoneScope | null
// ourPriceCopy.ts
export function zoneScopeMark(scope: ZoneScope): string | null     // null | "другая зона" | "зона не определена"
export function otherZonesWhy(lineZone: SectionZone, analogs: Pick<OurPriceAnalog, "zone">[]): string
export const ANY_ZONE_WHY: string
export function analogZoneBadge(zone: SectionZone | null): string  // «зона: Надземная» | «зона: не определена»
export function zoneRequiredNote(family: string): string
export function zoneRequiredNoZonePrefix(family: string): string
```

**Утверждения**
1. Подпись под вилкой: `own` — как сегодня; `other` — «… · другая зона»;
   `any` — «… · зона не определена».
2. «Сравнение»: при `other`/`any` — вторая метка и строка-пояснение спеки §5
   п. 2 дословно; список зон — в порядке `SECTION_ZONES`, одна зона —
   «зоны «…» (N)», несколько — «зон «…» (N) и «…» (M)», строки без зоны —
   последним «строк без зоны (N)»; при `own` — ни метки, ни строки.
3. Список аналогов: при `other`/`any` — метка зоны у каждого, у аналога без
   зоны — «зона: не определена»; при `own` — меток нет.
4. Строгая семья: `no_zone_match` — строка `zoneRequiredNote` над справкой;
   `no_zone` — префикс к тексту «Сравнения»; у прочих строк этих текстов нет.
5. Справка «по частям»: подписи частей `other`/`any` по спеке §5 п. 5.
6. `ZONE_HINT` и `zoneScopeContracts` — новые тексты спеки §5 п. 6 дословно.
7. Числа и тексты не переносятся и не наезжают на узкой панели (снимок
   стенда — финал).

**Проверка:** из `frontend/` (диск `C:\`): `npx vitest run` по файлам
задачи; `npx tsc -b`; `npx eslint .`.

- [ ] Тесты по утверждениям (красные) → реализация → зелёные → документация → коммит.

### Task 5: страница «Семьи» — флажок и слияние

**Files:** `types/domain.ts` (`WorkFamily`); `services/api/domain.ts`
(`semanticApi.setFamilyZoneRequired`); `services/queries.ts`
(`useSetFamilyZoneRequired`, инвалидация в `useMergeWorkFamilies`);
`pages/families/labels.ts`; `FamiliesTab.tsx` (блок, окно слияния); тесты
`FamiliesTab.zoneRequired.test.tsx` (новый), `FamiliesTab.test.tsx` (окно
слияния); `docs/reference/screens.md` (раздел «Семьи»).

**Interfaces**
- Потребляет: маршрут и поля задачи 1.
- Производит:

```ts
// WorkFamily: zone_required: boolean; zone_required_changed_at: string | null;
//             zone_required_changed_by: { id: number; email: string } | null
semanticApi.setFamilyZoneRequired(id: number, value: boolean): Promise<WorkFamily>
export function useSetFamilyZoneRequired(): UseMutationResult<WorkFamily, unknown, { id: number; value: boolean }>
// labels.ts
export const ZONE_REQUIRED_TITLE = "Зона при подборе";
export const ZONE_REQUIRED_SWITCH = "Зона обязательна";
export const ZONE_REQUIRED_ON: string;  // текст макета, состояние 5, флажок есть
export const ZONE_REQUIRED_OFF: string; // текст макета, флажка нет
export function zoneRequiredAuthor(family: WorkFamily): string | null
export function mergeZoneRequiredWarning(source: WorkFamily, target: WorkFamily): string | null
```

**Утверждения**
1. Блок «Зона при подборе» стоит между «Схемой и вариантами» и «Связями»;
   `Switch` отражает `zone_required`, текст пояснения — по значению.
2. Переключение уходит сразу (`PUT …/zone-required` с новым значением),
   `Switch` занят на время запроса; после ответа сброшены
   `qk.workFamilies.all` и `qk.tenders.ourPriceAll`.
3. Подпись автора: «Поставил {email}, {дата}» / «Снял {email}, {дата}» /
   «Поставлен при обновлении, {дата}»; без записи — подписи нет.
4. Архивная семья — `Switch` недоступен.
5. Окно слияния: флажок у источника / у цели — строка спеки §5 п. 8
   дословно; одинаковые флажки — строки нет. Слияние сбрасывает и
   `qk.tenders.ourPriceAll`.

**Проверка:** из `frontend/`: `npx vitest run` по файлам задачи и
`src/pages/families`; `npx tsc -b`; `npx eslint .`.

- [ ] Тесты по утверждениям (красные) → реализация → зелёные → документация → коммит.

## Финал (оркестратор)

- [ ] Снятия критических свойств спеки §7 п. 1–6 в копии дерева, каждое
  восстановлено побайтно; результат — в devlog. К п. 1 — отдельное снятие:
  переход в `other` разрешён при `own` со ступенью, но `all_outliers` —
  обязан покраснеть тест утверждения 7а задачи 2.
- [ ] `gca_dev` до 0028 (стенд остановлен на время миграции); снимок
  «после» и `snapshot_lines.py compare`: у строк, сравнимых «до», проекция
  совпала с нормализацией; прибыль по источникам — в devlog.
- [ ] Снимки стенда по спеке §7 п. 14 в обеих темах, сверка с макетом.
- [ ] `docs/devlog/2026-11-20-zone-as-preference.md`; пуш; черновик → готов
  к ревью по слову пользователя.
