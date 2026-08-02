# 2026-07-20 — ProjectPage load perf, PR-2: переиспользование расчёта + честный лоадер

**Ветка:** `perf/projectpage-load-pr2-calc-reuse` (от `main` @ `652ddc9`)
**Метод:** subagent-driven-development (Opus-оркестратор + ревьюер; Haiku на тривиальных задачах 1–4, Sonnet на содержательных 5–6; python-reviewer на бэкенд-задачах, typescript-reviewer на фронт-задачах; Fable — финальный whole-branch; правки — диффом)
**Спека:** `docs/superpowers/specs/2026-07-20-projectpage-load-perf-design.md` §1, §2, §4. PR-2 из двух (комбинированный бэк+фронт); PR-1 (индексы/дрейф) — уже в `main` (PR #38).

## Задача

`/projects/:id` грузится 1+ сек. Замер PR-1 показал: путь **round-trip-bound**, и `compute_calculations` на холодной загрузке гоняется **дважды** — сначала в `/summary`, затем повторно в `/calculations` (фронт дёргал отдельный эндпоинт). PR-2 убирает второй прогон: `/summary` начинает отдавать уже посчитанные calc-строки, фронт переиспользует их через `initialData` react-query — на первой отрисовке дефолтного вида запрос `/calculations` не уходит вообще. Заодно молчаливая деградация при ошибке summary заменена честной лестницей состояний (цельный skeleton при загрузке; состояние ошибки с «Повторить»; legacy-табы — только для настоящего пустого проекта).

## Что сделано

1. **Общий сериализатор `_serialize_calc_row`** (`backend/routers/dashboard.py`): тело dict-строки вынесено из `/calculations` в хелпер, чтобы `/summary` отдавал байт-в-байт ту же форму (19 ключей, `date → isoformat`, Decimal — как есть, сериализует FastAPI). `/calculations` теперь `return [_serialize_calc_row(r) for r in rows]` — чистая экстракция, поведенчески-нейтрально.
2. **`/summary` возвращает `calculations`** (`get_project_summary`): в return-dict добавлен ключ `"calculations": [_serialize_calc_row(r) for r in calc_rows]` — переиспользует **уже посчитанный** локальный `calc_rows`, второго вызова `compute_calculations` нет. Пустой проект → `[]`.
3. **Тест-страж инварианта периода:** summary считает границы по **нефильтрованным** датам, `/calculations` — с учётом исключённых поставщиков. Страж (`test_summary_calculations_equals_endpoint_with_excluded_edge_supplier`) фиксирует, что при исключённом поставщике на самом раннем краю диапазона границы различаются, но сериализованный выход идентичен (пустые месяцы пропускаются через `continue`).
4. **Опциональный тип + фикстуры** (фронт): `DashboardSummary.calculations?: DashboardCalculation[]` (опционально — старый бэк поля не отдаёт → фолбэк на сеть); фикстуры `sampleSummary{Multi,Mono}WithCalcs` для хук-тестов.
5. **`initialData` в `useDashboardCalculations`** (`frontend/src/services/queries.ts`): хук сеется из кэша `/summary`. `initialData` → `undefined` (фолбэк на сеть) при `projectId === null`, при заданном периоде, или при старом бэке без поля (`calculations === undefined`); присутствующий пустой `[]` сеется как `[]`. Клиентский фильтр по `direction` эквивалентен бэкенд-фильтру (аллокация delivery/additive не зависит от direction). `initialDataUpdatedAt` читает реальный `dataUpdatedAt` summary → посев уважает `staleTime`. Рецепт `just test-frontend-file <path>` для точечного фронт-прогона через `just`.
6. **Цельный skeleton + лестница состояний** (`ProjectPage.tsx`): `ProjectPageSkeleton` (единый блок: breadcrumbs + заголовок + KPI + контент) и ранние возвраты в строгом порядке §4: `projectsQ.isLoading → projectsQ.isError → not-found → summaryQ.isLoading → summaryQ.isError → обычная страница`. Ошибки списка/сводки — `EmptyState` с кнопкой «Повторить» (`loading={q.isFetching}`, ui-domain `Button`). `isLegacy` развязан с ошибкой summary — теперь `directions.length === 0` только для настоящего пустого проекта; удалена маскирующая переменная `summaryFailed`.

## Верификация

- **Ревью:** каждая задача — профильный спец-ревьюер (python-reviewer ×3 бэкенд, typescript-reviewer ×3 фронт), все Approve, 0 находок. Финал (Fable, whole-branch) — 5 кросс-задачных контрактов проверены по неизменному коду; найдена **1 Medium** (см. ниже), устранена в этом же PR + re-review чист.
- `just lint` чист; `just test` — backend **584 passed / 6 skipped**, frontend **218 passed** (+4 новых: `useDashboardCalculations` 5, `corridorInvalidation` 4, минус пересчёт). Cold-lifecycle хук-тест (`expect(seen).toHaveLength(0)`) — детерминированный страж «0 вызовов /calculations».
- **Миграций нет** — PR-2 не трогает `backend/alembic/` (аддитивное поле в JSON-ответе, не колонка). dev-Neon уже на head `6e3b8dc47ba9` (PR-1), сверено `alembic current` + наличием индексов в `pg_indexes`.

## Решения и нюансы

- **Находка финального ревью (Medium, кросс-задачная — пофайловые ревью её увидеть не могли):** 4 corridor-мутации (`useSetTypeCorridor` и др.) инвалидировали `calculations`/`calculationsAll`, но **не** `summary`. С сидингом PR-2 это из «устаревший KPI» превращалось в «устаревшие compensation-строки, посеянные как свежие» (при переключении direction на ещё-не-загруженный ключ `initialData` брал бы старые числа, а `initialDataUpdatedAt` + `staleTime: 60s` считали бы их свежими → рефетча нет). **Фикс** (`d3db0b5`): +`invalidateQueries(qk.dashboard.summary(projectId))` во все 4 мутации + regression-тест `corridorInvalidation.test.tsx`. Побочно закрыл и прежний баг устаревшего KPI сводки.
- **`retry: 2` из спеки §4.3 сознательно НЕ добавлен:** глобальный `retry: 1` (App.tsx) уже покрывает транзиентные 5xx; per-query `retry: 2` переопределил бы `retry:false` тест-клиента и с экспоненциальным `retryDelay` замедлил/зафлачил бы error-тесты — выигрыш маргинальный, кнопка «Повторить» покрывает остальное. Осознанное отклонение, Fable согласен.
- **Тесты — на уровне хука (`renderHook`), не страницы:** контракт `initialData` (фильтр, guard периода, backward-compat, счётчик сети) проверяется напрямую, без хрупкой привязки к разметке DeviationChart. Cold-lifecycle-тест — ценный страж поведения TanStack Query v5: `initialData` переоценивается в `Query.setOptions` на каждом ре-рендере при `data === undefined`, поэтому disabled-observer, созданный ДО прихода summary, подхватывает посев при его резолве (ревьюер подтвердил эмпирически, прогнав инструментированный тест с логами MSW-хендлера).
- **Direction-фильтр «компьют всё + отфильтровать» эквивалентен бэкенду:** в `compute_calculations` фильтр `direction_type_id` применяется в per-class цикле вывода, ПОСЛЕ аллокации delivery/additive (она идёт по всем классам единообразно) — значит клиентский `r.direction === direction` даёт тот же набор строк.

## Замер (точка 3, после PR-2)

Тот же read-only harness на dev-Neon, project id=1, N=25 (`docs/superpowers/notes/2026-07-20-projectpage-load-perf-baseline.md`):

- **`/calculations` при холодной загрузке: 0** (ждали 0) — детерминированный автотест cold-lifecycle. Заголовочный результат PR-2.
- **time-to-ready calc-таблицы:** экономия ≈ один полный прогон ядра (**~p50 2.7 с / p95 3.0 с** при текущем RTT) — данные посеяны из `/summary`, готовы в момент его резолва вместо второй последовательной цепочки `/summary → /calculations`.
- **`compute_calculations`-ядро:** p50=2731/p95=2951 мс — статистически без изменений vs baseline (2888/3108) и точки 2 (2934/3158). Ожидаемо: PR-2 не трогает движок, устраняет его повторный запуск. EXPLAIN всё ещё Seq Scan (exec 0.07–0.12 мс) — планировщик игнорирует и `ix_documents_project_id`, и `uq`-префикс: открытый вопрос точки 2 закрыт, сносить индекс по малым данным оснований нет.

## Гейт варианта B

Порог спеки: `/summary` p95 > ~400–500 мс **на реалистичных данных** → открывать B. `/summary`-ядро p95 ≈2.95 с номинально выше, **но** данные не реалистичны (1 проект, 105 инвойсов), а замер round-trip-bound (~80 SQL × ~35 мс RTT до удалённого Neon), не query-bound. **Вердикт: вариант B — кандидат, но НЕ открывать вслепую** — гейтить за замером на нагрузочном наборе (там видно, доминирует round-trip → батчинг/set-based rewrite, или исполнение → индексы начнут выбираться). PR-2 уже снял крупнейший конкретный выигрыш на текущих данных.

## Деплой

Только код — **миграция не нужна** (схема не меняется). Порядок деплоя из PR-1 (`db-migrate` + no-overlap stop-then-start) здесь не задействован. Выкатить бэкенд + фронт.

## Что осталось

- **Merge PR-2** (решение пользователя).
- **Вариант B** (set-based rewrite `compute_calculations`) — отдельным планом, только после замера на нагрузочном наборе.
- Мелкий долг из PR-1 (import-порядок в `alembic/script.py.mako`, TECH_DEBT) — не трогали.
