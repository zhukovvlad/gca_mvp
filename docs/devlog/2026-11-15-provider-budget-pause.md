# Devlog: пауза до сброса лимита провайдера вместо ошибок

**Дизайн:** [`docs/superpowers/design/2026-11-15-provider-budget-pause.md`](../superpowers/design/2026-11-15-provider-budget-pause.md) (редакция 3)
**Спека:** [`docs/superpowers/specs/2026-11-15-provider-budget-pause-design.md`](../superpowers/specs/2026-11-15-provider-budget-pause-design.md) (редакция 2)
**План:** [`docs/superpowers/plans/2026-11-15-provider-budget-pause.md`](../superpowers/plans/2026-11-15-provider-budget-pause.md) (редакция 2)
**Ветка:** `fix/provider-budget-pause`, PR #83. Реализация 15.11.2026, ускоренный
режим: исполнители Sonnet (Task 1–4), ревью Opus только у Task 3, снятия защит —
оркестратор в копии дерева.

## Что сделано

1. **Клиент** (`services/semantic_client.py`, Task 1, `3974279`):
   `BudgetAction`, `ProviderBudgetError(TransientModelError)`; 402 и 403 разбираются
   по таблице §2.1, суммы — `Decimal` из JSON-чисел, `Retry-After` 1..3600, иначе 60.
2. **Миграция 0026 и модель** (Task 2, `516d0d5`): исход `budget_refused`, пять полей
   паузы по времени у `semantic_worker_state`, два CHECK; downgrade отказывает при
   попытке `budget_refused` или стоящей паузе.
3. **Опросчик и статус** (Task 3, `f2c98d9`): ветка отказа в `record_failure`
   (`_record_budget_refusal`), `_stop_claim` — единственная точка остановки
   (`_apply_fuse` идёт через неё), `_set_budget_pause` с полом 60 с, проверка паузы в
   `claim_next`, `QueueStatus.budget_pause` вживую мимо кэша тяжёлых частей.
4. **Причина ожидания схемы и экран** (Task 4, `81cb1bb`): `open_suggestion_holds`,
   `schema_build_wait`, `SchemaOut.build_wait` (одно чтение с `ready_to_build`);
   плашки паузы и остановки, список семей карточки строго по единице, единица из
   справочника в «Новой семье работ», метка схемы с номером пачки.
5. **Документы** (Task 5): справочник схемы и экранов, `.env.example`, долг №63.

## Замеры

- Шаблон `-k "provider_budget or ProviderBudget or build_wait or BuildWait"`: ДО 0,
  ПОСЛЕ 85 (клиент) + 47 (`TestProviderBudget*`) + 13 (`TestBuildWait`) = 145.
- Файл клиента 39 → 123 теста; схема 0026 — 22 теста (17 обычных + 5
  `migration_roundtrip`).
- Затронутые наборы Task 3 (семь файлов плана): ДО 916, ПОСЛЕ 1000 = 916 − 1 + 85;
  997 зелёных, 3 красных — `TestAutoAccept` в `test_work_variants_api.py`, известный
  эффект локального `backend/.env` (долг №60), к фиче не относится. После Task 4 те
  же наборы плюс `test_provider_budget_pause.py` и `test_catalog_parameters_api.py`:
  1122 зелёных, те же 3 красных.
- Фронт: четыре файла плана ДО 250 (16 + 141 + 42 + 51; в плане записано 231 —
  `ContextCard.test.tsx` насчитан 122 вместо 141), ПОСЛЕ 285 (38 + 146 + 45 + 56);
  полный vitest — 178 файлов, 3352 теста, зелёный; `tsc -b`, `eslint .` — чисто.

## Снятия защит

Протокол `BASELINE_GREEN / MUTATION_APPLIED / EXPECTED_TEST_FAILURE / RESTORED`, в
копии дерева, серийно, таймаут 900 с на прогон; у всех семи — базовый прогон
зелёный, правка легла, sha после восстановления совпал.

| Снятие | Проверка | Красных |
|---|---|---|
| задание в `error` при отказе | 4, 14 | 12, в т. ч. `TestProviderBudgetEndToEnd` |
| стоимость выполненных порций → 0 | 5 | 9, в т. ч. `TestProviderBudgetDiscovery::test_two_done_portions_and_a_refused_third_cost_their_sum` |
| проверка паузы в `claim_next` убрана | 9 | 2 — `TestProviderBudgetClaim::test_the_claim_before_the_term_is_none_and_the_job_is_untouched`, EndToEnd |
| предохранитель на пути отказа убран | 8а | 4 — `TestProviderBudgetFuse` (и `lost_claim`), AutoSplit |
| 402 снова постоянная ошибка | 1 | 29 в `TestProviderBudget` клиента |
| отбор по виду отпечатков убран | 19а | 4 — `values_only[import\|mass]`, `schema_only[mass]`, смешанная пачка |
| фильтр единицы в списке семей убран | 17 | 3 — группа «список семей строго по единице контекста» |

`schema_only[import]` под снятием 19а остался зелёным: у отпечатков схемы нет
контекста, единица не находится и без отбора по виду — пробела нет.

## Отступления от плана

1. **Ещё три контрактных ожидания, не названных в Global Constraints**, правлены
   до нового контракта, прежние утверждения остались:
   - `test_semantic_queue_schema.py`, `TestParityWithMigration[SEMANTIC_ATTEMPT_OUTCOMES]`:
     заморожен литерал 0018 (как у `SUGGESTION_DECISIONS` после 0019); модель против
     0026 с независимым литералом — в `test_provider_budget_schema.py`;
   - `test_semantic_queue_api.py`, `TestStatus.test_empty_system_shape`: +
     `"budget_pause": None`;
   - `test_catalog_parameters_api.py`, `TestSchemaForm.test_the_keys_are_the_old_ones_plus_the_new_ones`:
     + `build_wait`.
2. **Фикстуры фронта сверх списка:** контекст 601 в тестах карточки поставлен в м³
   (`beforeEach`; общий `handlers.ts` не тронут — `ContextsTab.test.tsx` ждёт у него
   «м²»), имена опций с «· м³»; причина остановки `reserve_below_actual` в фикстурах
   шапки заменена на `reserve_exceeded` — бэкенд первого значения не знает, а
   неизвестная причина теперь печатает общий текст.
3. **Формулировки, которых спека не дала дословно:** несколько пачек — «схема ждёт
   удержанные пачки №8 (импорт, job 46), №11 (массовая постановка) — поставьте или
   отбросьте их…»; источники пачек строчными (`BATCH_SOURCE_SHORT_LABEL`); при
   `build_wait = null` и `ready_to_build = false` остаётся прежняя «схема ждёт
   перезапроса единицы» (сервер так не ответит, старый тест это закрепляет).
4. **Ревью Task 3**: дефектов продукта нет; ревьюер усилил п. 5 и 7 (резерв строго
   больше стоимости) и добавил тест «`budget_pause` вживую при включённом кэше тяжёлых
   частей». Низкое замечание о ширине типа `_set_budget_pause(source: str | None)`
   против CHECK тройки — вход «пауза без источника» клиент не выдаёт, не правилось.
   Замечание «ручная остановка не снимает паузу» не подтвердилось: ручной остановки
   в коде нет, остановку ставит только `_stop_claim`, и она паузу снимает.

## Грабли

- Остановленный фоновый прогон снятий успел наложить мутацию и не восстановил файл
  копии (`finally` не выполнился). Найдено сверкой `cmp` копии с оригиналом перед
  перезапуском. Первая причина остановки — коллизия баз: снятия шли `-n 1` на
  `gca_gw0_test`, а финальный `-n 4` исполнителя пересоздаёт ту же базу воркёра;
  снятия перезапущены `-n 0` на отдельной `gcamut_test`.
- vite стенда отдаёт живое дерево: новый фронт сразу работает со старым бэкендом
  стенда, а тот не отдаёт `build_wait` — метка схемы при `ready_to_build = false`
  обращается к `undefined`. Лечит перезапуск бэкенда стенда.

Тронутые области и файлы граблей: БД — `docs/pitfalls/db.md` (исполнитель Task 2),
фронтенд — `docs/pitfalls/frontend.md` (исполнитель фронта Task 4).

## Стенд

Миграция 0026 на `gca_dev` применена (0025 → 0026). Перезапуск бэкенда стенда и
снимки окон каталога — за пользователем: остановку процесса стенда агенту запретил
классификатор.
