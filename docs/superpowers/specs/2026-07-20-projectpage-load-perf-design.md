# ProjectPage: ускорение первой загрузки + честный лоадер

**Статус:** дизайн (2026-07-20), два раунда внешнего ревью учтены. Раунд 2 (Codex): `initialData` вместо `setQueryData` (тайминг-дыра моей гипотезы опровергнута живым probe — см. §2), полное закрытие дрейфа индексов, backward-compat поля, лестница состояний страницы, переформулировка замеров.
**План:** вариант A из брейншторма. Варианты B (set-based rewrite `compute_calculations`) и C (материализованная кэш-таблица) отложены за замером — память `project-projectpage-load-perf`.

## Цель

`/projects/:id` грузится 1+ сек. Корень — тяжёлый `compute_calculations` (помесячный цикл, ~5 SQL на месяц) выполняется **дважды последовательно** за загрузку: внутри `/summary` (считает `calc_rows` за полный период и выкидывает, оставляя агрегаты) → затем фронт из-за гейта `queriesEnabled` запускает `/calculations`, который считает то же заново. Плюс горячие колонки без индексов. Плюс сетевая ошибка summary молча маскируется под пустой проект.

**Важно про природу выигрыша:** вариант A **не ускоряет сам `/summary`** — тяжёлый расчёт остаётся внутри него. A убирает **второй последовательный расчёт + HTTP round-trip** на первой отрисовке, сокращая время до готового экрана. Объём переданных calc-rows при этом ~тот же (переезжает из отдельного ответа в summary), поэтому «сеть уменьшается» — некорректно; корректно «устраняется один round-trip и повторное вычисление».

**Поставка — два PR:**
- **PR-1 (Секция 3):** закрытие дрейфа ORM/БД + 2 новых индекса. Бэкенд-only, без изменения поведения, вливается **первым**.
- **PR-2 (Секции 1+2+4):** бэкенд отдаёт `calc_rows` + фронт переиспользует через `initialData` + единый лоадер/ошибка. Комбинированный (бэк+фронт).

---

## Секция 1 — Backend: `/summary` отдаёт готовые `calc_rows`

`/summary` уже вызывает `compute_calculations` за полный период ([dashboard.py:225](../../../backend/routers/dashboard.py)) и оставляет только агрегаты. Добавляем в JSON-ответ поле `"calculations": [...]` с той же сериализацией строки, что и `/calculations` ([dashboard.py:360-382](../../../backend/routers/dashboard.py)).

- **Общий сериализатор.** Вынести тело dict-строки в хелпер `_serialize_calc_row(r) -> dict` (в `routers/dashboard.py`) и звать в обоих эндпоинтах — форма гарантированно одна.
- **Порядок вызовов не ломается:** `full_compensation_from_rows` / `_direction_summaries` едят сырые `calc_rows` (dict до сериализации); сериализуем в конце.
- **Пустой проект:** нет счетов → `calc_rows = []` → `"calculations": []`.

### Тип и backward-compat

`DashboardSummary` ([dashboard.ts:21](../../../frontend/src/types/dashboard.ts)) получает поле **опциональным**: `calculations?: DashboardCalculation[]`. Опциональность — не косметика: она даёт backward-compat (старый бэк без поля → `initialData` вернёт `undefined` → `/calculations` штатно уйдёт на сервер, см. §2) и избавляет от правки **всех** summary-фикстур — трогаем только те, что участвуют в тестах посева ([fixtures.ts:63](../../../frontend/src/test/fixtures.ts) и мульти-вариант). `DashboardCalculation` уже содержит `direction` ([dashboard.ts:67](../../../frontend/src/types/dashboard.ts)) — он нужен для клиентского фильтра в §2.

### Инвариант периода (тест-страж)

`/summary` считает период из **нефильтрованных** `date_bounds` ([dashboard.py:210-217](../../../backend/routers/dashboard.py)); `/calculations` без периода выводит границы **с учётом** исключений ([calculations.py:146-162](../../../backend/crud/calculations.py)). Границы могут различаться, если исключённый поставщик держит min/max дату. **Выход идентичен**: месяцы без не-исключённых инвойсов пропускаются через `continue` ([calculations.py:193-195](../../../backend/crud/calculations.py)). Равенство «по построению», не по контракту → **тест-страж**: на фикстуре с исключённым поставщиком на краю диапазона `summary["calculations"] == GET /calculations` (тот же проект, без period/direction).

**Обе стороны сортировать в тесте** по `(period_start, material_class_id)`: `compute_calculations` не имеет финального `ORDER BY` (порядок — из dict `class_contrib` + план запроса), после добавления индексов план может измениться. Порядок API не контракт (фронт сортирует при отображении), поэтому сортировка в тесте — достаточное решение, фиксировать сортировку в сериализаторе не нужно.

---

## Секция 2 — Frontend: `initialData` из `summary.calculations`

Дата-дорога одна: компонент читает `useDashboardCalculations`. Первый вызов на дефолтном виде делаем cache-hit через `initialData`, читающий уже загруженный summary.

### 2.1 Почему `initialData`, а не `setQueryData`

Первая гипотеза (посев `setQueryData` в queryFn summary) исходила из того, что `initialData` не сработает на стабильном ключе мульти-объекта: observer calc-запроса создаётся на render 1 (гейт `enabled` откладывает только fetch), `initialData` якобы вычисляется однократно и до резолва summary даёт `undefined`.

**Проверено живым probe** (react-query 5.100.9, три стратегии + контроль):
- Контроль (ни посева, ни `initialData`): calc уходит на сервер (1 вызов) — гейт валиден.
- `initialData`, читающий summary из кэша: `serverCalls=0`, данные подхвачены — **fetch погашен даже при стабильном ключе**. Гипотеза опровергнута: `initialData` переоценивается на ре-рендерах, пока у query нет данных, и подхватывает summary когда тот резолвится.
- `setQueryData`: тоже `serverCalls=0`.

`initialData` выбран: он **не клоббит** (применяется только при пустом кэше — снимает замечание Codex про перезапись более свежего calc-кэша) и **бесплатно покрывает** deep-link/переключение направления на дефолтном периоде (клиентский фильтр по `r.direction` эквивалентен бэкенд-фильтру `direction_type_id`, который применяется в выходном цикле после аллокации — [calculations.py:309-312](../../../backend/crud/calculations.py)).

### 2.2 Форма

`useDashboardCalculations` получает `initialData` + `initialDataUpdatedAt` из кэша summary:

```ts
// useDashboardCalculations — псевдокод
const qc = useQueryClient();
return useQuery({
  // projectId: ID | null — сохраняем текущую форму хука: условный ключ + `as ID`
  // в queryFn (guard в initialData сами эти строки не типизирует, strict проверит их отдельно).
  queryKey: projectId
    ? qk.dashboard.calculations(projectId, periodStart, periodEnd, direction)
    : ["dashboard", "calculations", "none"],
  queryFn: () => dashboardApi.calculations(projectId as ID, periodStart, periodEnd, direction),
  enabled: projectId !== null && (options?.enabled ?? true),
  initialData: () => {
    // projectId === null → query disabled (не «→ сервер»); изменённый период / старый
    // бэк → сервер. Guard обязателен: без него getQueryData/summary-ключ не типизируются.
    if (projectId === null || periodStart || periodEnd) return undefined;
    const s = qc.getQueryData<DashboardSummary>(qk.dashboard.summary(projectId));
    if (s?.calculations === undefined) return undefined;      // старый бэк → сервер
    return direction ? s.calculations.filter((r) => r.direction === direction) : s.calculations;
  },
  initialDataUpdatedAt: () =>
    projectId === null ? undefined : qc.getQueryState(qk.dashboard.summary(projectId))?.dataUpdatedAt,
});
```

- Покрывает: мульти-дефолт `(…, undefined)`, моно-дефолт `(…, code)` (фильтр отсекает чужие `other`-строки — `calc_role` дефолт `'base'` у всех классов, вкл. тип other), deep-link/переключение на мульти при дефолтном периоде.
- `initialDataUpdatedAt` = момент фетча summary; при глобальном `staleTime: 60_000` ([App.tsx:31](../../../frontend/src/App.tsx)) посеянное свежее 60с → на снятии гейта fetch не уходит.
- **Изменённый период** → `initialData` вернёт `undefined` → `/calculations` уйдёт on-demand (правильно).
- **«0 вызовов» — только холодная первая загрузка** (пустой кэш → summary → calc). Возврат на страницу с **протухшим** summary (>60с, и после gcTime calc-query — дефолт 5 мин) тоже применит `initialData`, но `initialDataUpdatedAt` старый → посеянное сразу stale → строки рисуются мгновенно из посева, а фоновый рефетч уходит. Это корректный SWR, не баг; но сетевой regression-test обязан фиксировать именно холодный сценарий, иначе флак.

### 2.3 Оговорка (out of scope)

`initialData` оптимизирует **первую отрисовку**. После мутации (инвалидация префикса `["dashboard"]`) calc уже имеет данные → помечается stale → рефетчится с сервера, и summary тоже пересчитывает. То есть двойной расчёт **возвращается на пост-мутационном пути** — это не регресс (пользователь только что сделал мутацию и ждёт обновления) и вне объёма задачи «первая загрузка». Убирается вариантами B/C.

---

## Секция 3 — Backend: закрытие дрейфа индексов + новые (PR-1)

Закрываем **полностью** ORM/БД-дрейф из [TECH_DEBT.md](../../../docs/TECH_DEBT.md) (снять чекбокс после мержа) и добавляем 2 новых индекса. Все существующие индексы уже в БД — их объявление в моделях **метаданные-only, без миграционных операций**; реальный `op.create_index` — только для двух новых.

### 3.1 Закрытие дрейфа (метаданные, без миграции)

Определения взяты буквально из [suppliers-миграции](../../../backend/alembic/versions/2026_05_15_1200-b3c7e9f12a45_add_suppliers_table.py) и [calc_role-миграции](../../../backend/alembic/versions/2026_05_21_1200-add_calc_role_to_material_classes.py) — имена обязаны совпадать:

- **`InvoiceItem`** — `__table_args__ += Index("ix_invoice_items_invoice_id_item_type", "invoice_id", "item_type")`.
- **`Invoice`** — `supplier_id = Column(..., index=True)` (дефолтное имя SQLAlchemy `ix_invoices_supplier_id` совпадает с БД).
- **`Supplier`** — `__table_args__`:
  - `Index("ix_suppliers_name_trgm", "name", postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"})`
  - `Index("uq_suppliers_name_no_inn", "name", unique=True, postgresql_where=text("inn IS NULL"))`
- **`Supplier.id`** — **убрать** `index=True` ([models.py:291](../../../backend/models.py)): PK уже проиндексирован, а `ix_suppliers_id` в БД нет → это закрывает единственный `create_index`-дифф автогена.

### 3.2 Новые индексы (реальная миграция + объявление в модели)

- **`Document.project_id`** — `index=True` (`ix_documents_project_id`). Первичный фильтр всех запросов.
- **`Invoice` композит `(document_id, date)`** — `Index("ix_invoices_document_id_date", "document_id", "date")`. Путь доступа всегда `project → documents → invoices(document_id) + date range`; композит покрывает джойн и диапазон разом, дешевле двух отдельных.

**Не добавляем:**
- `invoice_items.invoice_id` отдельно — уже покрыт левым префиксом композита `(invoice_id, item_type)`.
- `invoice_items.item_type` отдельно — низкая селективность (3 значения), в горячих запросах всегда в паре с `invoice_id.in_(...)`; уже полезен как вторая колонка существующего композита.
- `invoice_items.material_class_id` — не на hot path: джойн `MaterialClass` идёт по его PK после сужения по `invoice_id`. Добавлять только если подтвердит `EXPLAIN`.

### 3.3 Миграция и риски

- Ревизия через `just db-revision "add hot-path indexes"`, тело вручную: **только** `op.create_index`/`op.drop_index` для двух новых (§3.2). Дрейф-объявления (§3.1) миграции не требуют.
- Применить `just db-migrate` (dev) / `just db-test-migrate` (тест).
- **Тест-схема — риск снят (проверено):** тесты строят схему через `alembic command.upgrade(cfg, "head")` ([conftest.py:164](../../../backend/tests/conftest.py)), **не** `create_all`. `pg_trgm` и trgm-индекс уже создаются suppliers-миграцией; объявления в моделях (§3.1) — чисто метаданные для автогена, create-пути через модель нет → безопасно.
- **`CREATE INDEX` не безусловно безопасен** на больших таблицах (блокирует запись). Здесь фиксируем: прод не развёрнут, таблицы малы → обычная транзакционная миграция допустима. `CREATE INDEX CONCURRENTLY` (autocommit-блок Alembic) — путь при росте, отметить в теле ревизии комментарием.
- Импорт `from sqlalchemy import text` для `postgresql_where`.

### 3.4 Приёмка PR-1

`alembic revision --autogenerate` (в отбрасываемую ревизию) выдаёт **пустой** upgrade/downgrade — дрейф закрыт. `just test` зелёный (в т.ч. suppliers trgm-поиск). Снять чекбокс пункта дрейфа в [TECH_DEBT.md](../../../docs/TECH_DEBT.md) в этом же PR; попутно поправить в нём устаревшую ссылку `models.py:282` → фактический `Supplier.id` на :291.

**Честная оговорка:** на текущих малых данных новые индексы прироста могут не дать (доминируют round-trip'ы). Это ставка на масштаб.

---

## Секция 4 — Frontend: единый лоадер + честная ошибка

### 4.1 Явная лестница состояний

Сейчас и `projectsQ.isError`, и `summaryQ.isError` маскируются: список падает → «Объект не найден» ([ProjectPage.tsx:620](../../../frontend/src/pages/ProjectPage.tsx)), сводка падает → legacy-табы ([465-476](../../../frontend/src/pages/ProjectPage.tsx)). Зафиксировать явный порядок:

1. `projectsQ` грузится → skeleton.
2. `projectsQ` упал → ошибка списка объектов + retry.
3. Объект отсутствует (список успешен, id не найден) → «Объект не найден».
4. `summaryQ` грузится → цельный skeleton (шапка + KPI-ряд + контент).
5. `summaryQ` упал → ошибка сводки + retry (`summaryQ.refetch()`).
6. Успешный ответ с `directions: []` → legacy-режим (ADR #11).
7. Иначе — обычная страница.

`isLegacy` развязывается с `summaryFailed`: остаётся строго для п.6.

### 4.2 Состояние ошибки

На базе `EmptyState` + `Button` (shadcn-примитивы; спиннер — `Loader2`, уже в проекте). Кнопка «Повторить»: `disabled` на `isFetching` + спиннер в кнопке. Одинаково для **обоих** состояний ошибки: п.2 списка — `projectsQ.refetch()` / `projectsQ.isFetching`; п.5 сводки — `summaryQ.refetch()` / `summaryQ.isFetching`. Без escape-hatch «продолжить без сводки» (при 500 табы тянули бы те же ошибки, только тише).

### 4.3 Ретраи — честно

`retry: 1` + экспоненциальный `retryDelay` уже глобально ([App.tsx:31](../../../frontend/src/App.tsx)). «Настроить retry+backoff явно» — no-op. Для критичного summary — точечно `retry: 2` на `useDashboardSummary` (минорный robustness-бонус на транзиентных 5xx). Больше нигде.

### 4.4 Тест :1059 — инвертировать

`ProjectPage.test.tsx:1059` `"summary error: degrades to legacy tabs..."` противоречит новому поведению. Переписать: summary 500 → состояние ошибки + «Повторить», **не** legacy. Отдельный кейс: `directions: []` при успешном ответе → legacy (п.6).

---

## Порядок реализации

1. **PR-1** — Секция 3 (закрытие дрейфа + 2 индекса). Приёмка §3.4. Мержится первым.
2. **PR-2** — Секция 1 (бэк: `_serialize_calc_row` + `calculations` + тип + тест-страж) → Секция 2 (фронт: `initialData`) → Секция 4 (лоадер/ошибка + инверсия теста). Один PR.

## Замер (гейт на B/C)

Три точки, чтобы разделить вклад индексов и устранения повторного расчёта:
1. **Baseline — до PR-1** (текущий main).
2. **После PR-1** (индексы) — виден эффект индексов на `/summary` и `EXPLAIN`.
3. **После PR-2** (устранение дубля) — виден эффект по time-to-KPI и числу вызовов `/calculations`.

Метрики в каждой точке: p50/p95 `/summary`; **time-to-ready-screen**; число вызовов `/calculations` при **холодной** дефолтной загрузке (после PR-2 ожидаем 0); число SQL внутри `compute_calculations`; размер summary-ответа; `EXPLAIN (ANALYZE, BUFFERS)` до/после индексов; форма набора (месяцы/счета/позиции). Если `/summary` p95 всё ещё >~400-500 мс на реалистичных данных — открывать вариант B отдельным планом (B целит именно во внутренний расчёт `/summary`).

**Метод time-to-ready-screen (воспроизводимый во всех трёх точках):** мерим до готовности **графика/таблицы отклонений** (они зависят от calc-rows), а не только KPI — KPI приходят из summary и вариантом A не ускоряются, ускоряется именно устранение второго расчёта на пути calc. Способ: `performance.mark("nav")` при входе на маршрут + `performance.mark("calc-ready")` в эффекте, когда calc-данные впервые доступны (не в loading), `performance.measure` между ними; p50/p95 по N перезаходов с холодным кэшем (`queryClient.clear()` между прогонами). Один и тот же mark-код во всех трёх точках.

## Тест-план (кратко)

- **Бэк:** `_serialize_calc_row` даёт идентичную форму в обоих эндпоинтах; `summary["calculations"]` непуст на проекте с данными, `[]` на пустом; **инвариант-страж** (исключённый поставщик на краю → summary == /calculations).
- **Фронт:** мульти-дефолт — **сетевой regression-test** на **холодном** сценарии (пустой кэш → summary → calc): счётчик запросов `/calculations` `=== 0` (решение опирается на проверенное поведение TanStack Query, регрессия ловится сетью, а не мока́ми; на протухшем кэше SWR-рефетч штатен — тест обязан стартовать с чистого `queryClient`); моно — вид направления без чужих `other`-строк, **при этом моно-фикстура summary обязана содержать ≥1 строку `calculations` с `direction: "other"`** (иначе фильтру нечего отсекать и тест вакуумно зелёный), ассерт — её отсутствие в таблице направления; изменённый период → `/calculations` уходит; старый бэк без `calculations` → `/calculations` уходит (backward-compat); summary 500 → ошибка+Повторить (инверсия :1059); `directions:[]` → legacy; `projectsQ` 500 → ошибка списка, не «не найден».
- **PR-1:** `alembic --autogenerate` пустой; suppliers trgm-поиск зелёный.
- `just lint` + `just test` перед завершением каждого PR.
