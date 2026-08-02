# Фаза 2 — схема БД: инструкция на старт

Брифинг для агента, начинающего фазу 2 «с холодного старта». Читать **после**
`AGENTS.md` — он остаётся единственным источником истины, здесь только то, чего
в нём нет: состояние репозитория, окружение и грабли, найденные в фазах 0–1.

## 1. Где мы сейчас

Фазы 0 и 1 завершены и приняты внешним ревью (условно принята фаза 0 —
получен один реальный образец вместо 2–3).

Что уже есть в `main`:

- `AGENTS.md` — редакция **v6.1**: относительно исходного брифа v6 в §4 и §6
  закрыта развилка `quantity` vs `suggested_quantity`, в §9 фаза 0 помечена
  выполненной условно. Решения не менялись.
- Boilerplate из `udp-tenders@98fb676`, вычищенный от LLM/PDF/MinIO,
  мультиарендности и домена УПД. Роли — `admin` / `member` (`models.UserRole`).
- **Миграция `0001`** (`backend/alembic/versions/2026_08_02_0001-initial_auth_units.py`):
  расширения `vector` и `btree_gist` (включены первой миграцией, как требует §3),
  таблицы `users`, `refresh_tokens`, `units_of_measure`, `unit_aliases` + сиды
  единиц и алиасов. **Фаза 2 начинается с ревизии `0002`.**
- Тесты: backend `161 passed`, frontend `14 passed`, ruff/eslint/tsc чистые.
- `docs/phase0-input-data.md` — результаты фазы 0 и открытые риски.

Репозиторий **приватный**. История переписывалась (`git-filter-repo`) для удаления
реальных данных — все SHA до 2026-08-02 недействительны; бэкап прежней истории
лежит вне репозитория, в профиле пользователя.

## 2. Окружение

| Что | Как |
|---|---|
| Python | 3.12 через `uv` (окружение в `backend/.venv`) |
| БД | локальный PostgreSQL **16.14 + pgvector** на порту **5459** (кластер общий с другим проектом, установлен как `udp-pgtest`) |
| Базы | `gca_dev` — разработка, `gca_test` — тесты (дропается conftest'ом) |
| Task runner | `just` (см. `justfile`) |

Ключевые команды:

```bash
just db-migrate        # накатить миграции на gca_dev
just db-test-check     # накатить на gca_test + alembic check (детект дрейфа ORM/БД)
just test-backend      # pytest против gca_test
just lint-backend      # ruff
```

`backend/.env` и `.env.test` уже созданы локально (в git не попадают).
Пользователь для UI: `admin@example.com` / `gca-admin-2026` (домен `.local`
не годится — `EmailStr` его отвергает).

**Инвариант:** приложение запускается строго одним worker'ом uvicorn (§3) —
`just dev-backend` уже настроен верно.

## 3. Исходники схемы

Клоны исходных репозиториев на зафиксированных ревизиях лежат **вне рабочего
дерева**, в `C:\Users\zhukov_v\Projects\_gca_sources\`:

| Репозиторий | Ревизия | Что брать в фазе 2 |
|---|---|---|
| `tenders-go` | `121718bf45df` | `cmd/internal/db/migration/`: `000001_init_schema_consolidated.up.sql` (основа), `000002_add_fts_to_catalog` (FTS), `000007_add_position_grouping`, `000008_add_group_kind`. Есть `README_CONSOLIDATED.md` — читать |
| `udp-tenders` | `98fb67667c44` | паттерны Alembic-миграций и моделей (уже перенесены) |

**Не брать:** `users`/`user_sessions` (auth уже свой), RAG-эндпоинты,
`suggested_merges`-воркфлоу, миграции `000003`–`000006`.

## 4. Что делает фаза 2

Дословно из `AGENTS.md` §9:

> **Схема:** Alembic — pgvector + btree_gist extensions; перенос таблиц tenders-go
> (raw SQL через `op.execute()` для COALESCE-уникальных, частичных,
> EXCLUDE-индексов; проверить корректный downgrade); contracts, rate_classes,
> rate_standards, import_jobs, estimate_raw_data, `normalized_job_title`;
> VIEW отклонений; слияние units. SQLAlchemy-модели поверх.

Расширения уже включены в `0001` — этот пункт закрыт.

Полное описание таблиц и ограничений — `AGENTS.md` §4. Сверять построчно:
переименование `tenders` → `estimates`, `contracts` со снимком `rate_class_id`,
`estimate_raw_data`, `lots`/`proposals`/`position_items`, каталожный контур
(`catalog_positions` с новой колонкой `normalized_job_title`, `matching_cache`),
`rate_standards`, `import_jobs`, VIEW `v_position_deviations`.

### Требует raw SQL через `op.execute()` (§11)

Alembic не выражает это декларативно, и `downgrade` обязан их корректно снимать:

- `UNIQUE (normalized_job_title, COALESCE(unit_id, -1))` на `catalog_positions`;
- частичные индексы (`WHERE kind = 'POSITION'` и т. п.);
- `EXCLUDE USING gist (catalog_position_id WITH =, rate_class_id WITH =, daterange(valid_from, valid_to, '[)') WITH &&)` на `rate_standards` (btree_gist уже есть);
- `UNIQUE (contract_id, COALESCE(amendment_no, -1)) WHERE status NOT IN ('done','error')` на `import_jobs`;
- `UNIQUE NULLS NOT DISTINCT (contract_id, amendment_no)` на `estimates` — **синтаксис PG16**, тестовая БД той же мажорной версии (§11).

### Денежные поля

`numeric` в БД ↔ `Decimal` в Python ↔ строки в JSON. Никаких `float`. Пустая
стоимость → `NULL`, не `0` (§3). При переносе из tenders-go **не ослаблять**
типы денежных полей и CHECK-ограничения, не терять частичные индексы (§2).

## 5. Definition of done фазы 2

- `just db-migrate` проходит на чистой `gca_dev`;
- **круговой рейс** `alembic downgrade base` → `alembic upgrade head` проходит
  без ошибок (в фазе 1 это проверялось и работало — не сломать);
- `just db-test-check` — `alembic check` не показывает дрейфа между моделями и схемой;
- `just test-backend` зелёный; `just lint-backend` чистый;
- модели SQLAlchemy покрывают все новые таблицы и согласованы с миграцией.

Полезно добавить тест, фиксирующий сами ограничения (что EXCLUDE реально
запрещает пересечение периодов, что частичный уникальный индекс на `import_jobs`
пропускает параллельные допсоглашения) — в фазе 4 на них завязана логика.

## 6. Процесс

Бриф (§9) требует **останавливаться после каждой фазы** и коммитить её отдельно.
После остановки пользователь прогоняет внешнее ревью (Codex и другая модель) и
приносит замечания. Замечания проверять по фактам, а не принимать на веру:
в фазах 0–1 часть из них подтвердилась, часть потребовала уточнения замером.

Реальные данные (наименования контрагентов и объектов, расценки) **не попадают
ни в код, ни в документацию, ни в сообщения коммитов** — правило §9 шире, чем
«не коммитить XLSX».

## 7. Хвосты, не относящиеся к фазе 2

Держать в поле зрения, но делать не сейчас:

- **Фаза 3:** нужен тест производительности на синтетическом файле с раздутой
  размерностью (`max_column` = 16384); обезличенный fixture этот дефект не
  воспроизводит в обычном режиме открытия. Подробности и замеры —
  `docs/phase0-input-data.md`.
- **Фаза 3:** константы детекции шапки писать по реальному заголовку
  «Предлагаемое количество».
- **До пилотной приёмки парсера:** запросить у пользователя ещё 1–2 реальные
  сметы других объектов и лет.
- Если репозиторий когда-нибудь вернут в public — сначала убедиться, что старые
  SHA собраны сборщиком мусора GitHub.
