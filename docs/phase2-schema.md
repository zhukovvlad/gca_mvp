# Фаза 2 — схема БД: что сделано и где отступления

Дополнение к `AGENTS.md` §4 (источник истины по схеме). Здесь — только то, чего
в брифе нет: карта миграции, отступления от исходников с обоснованием и способ
проверки. Читать вместе с `backend/alembic/versions/2026_08_02_0002-domain_schema.py`.

## 1. Что создано

Ревизия **0002** поверх `0001` (расширения `vector`/`btree_gist`, auth, единицы).

| Контур | Таблицы |
|---|---|
| Справочники | `rate_classes`, `objects`, `contractors` |
| Договор | `contracts` |
| Сметы | `estimates`, `estimate_raw_data`, `lots`, `proposals`, `proposal_additional_info`, `proposal_summary_lines`, `position_items` |
| Каталог | `catalog_positions` (+ `normalized_job_title`, `fts_vector`), `matching_cache` |
| Нормативы | `rate_standards` |
| Служебное | `import_jobs` |
| Представление | `v_position_deviations` |

Из `tenders-go@121718bf45df` **не переносились**: рубрикатор тендеров
(`tender_types`/`tender_chapters`/`tender_categories`), `executors`, `persons`,
`winners`, RAG-инфраструктура (`lots_md_documents`, `lots_chunks`),
`suggested_merges` и миграции `000003`–`000008` поверх неё, `users`/`user_sessions`
(auth свой, из `udp-tenders`).

## 2. Объекты, созданные raw SQL (`AGENTS.md` §11)

| Объект | Почему не декларативно |
|---|---|
| `uq_catalog_positions_norm_unit` | индекс по выражению `COALESCE(unit_id,-1)`; он же арбитр `ON CONFLICT` в get-or-create матчинга (§5, шаг 4.3) |
| `uq_estimates_contract_amendment` | `UNIQUE NULLS NOT DISTINCT` — синтаксис PG16 |
| `uq_import_jobs_active_pair` | частичный уникальный индекс с `COALESCE` и `WHERE status NOT IN (...)` |
| `ex_rate_standards_no_overlap` | `EXCLUDE USING gist (... daterange(...) WITH &&)` |
| `v_position_deviations` | VIEW |

Первые три невидимы для `Base.metadata`, поэтому `alembic check` на каждом прогоне
предлагал бы их удалить. Они внесены в `RAW_SQL_INDEXES` в `backend/alembic/env.py`
(фильтр `include_object`). **Плата:** их пропажа из БД детектором дрейфа не
ловится — поэтому и наличие, и поведение каждого закреплены интеграционными
тестами (`tests/integration/test_schema_constraints.py`), включая проверку, что
`ON CONFLICT (normalized_job_title, COALESCE(unit_id,-1))` действительно
находит свой индекс.

`downgrade` снимает все пять объектов явно, до `DROP TABLE`; круговой рейс
`downgrade base` → `upgrade head` проверен.

## 3. Отступления от исходников и от буквы брифа

1. **`units_of_measure`, а не `units_of_measurement`** (§4 называет таблицу
   вторым именем). Слияние `UnitOfMeasure` + `UnitAlias` из `udp-tenders`
   выполнено в фазе 1 под именем `units_of_measure` и принято ревью; переименование
   ради буквы §4 стоило бы миграции и правок кода без выигрыша. FK
   `catalog_positions.unit_id` и `position_items.unit_id` — `integer`, как PK этой
   таблицы (остальные новые PK — `bigint`, как в tenders-go).
2. **`comment_organizer` вместо `comment_organazier`.** В tenders-go опечатка;
   парсер отдаёт ключ `comment_organizer`
   (`parser_tender_xlsx/app/constants.py:90`). Переименование прямо разрешено
   §2 («эквивалентный перенос допускает переименование»).
3. **Миграции 000007/000008 tenders-go не переносились** — вопреки списку в
   `docs/phase2-start.md` §3, но по §4 `AGENTS.md`: перечень колонок
   `catalog_positions` и список значений `kind` там даны явно и не содержат
   `parent_id`, `parameters`, `GROUP_TITLE`. Эти колонки обслуживают воркфлоу
   группировки вариантов и `suggested_merges`, который из скоупа исключён (§2).
   Если группировка вариантов понадобится — это отдельная ревизия.
4. **`lots.estimate_id` — `ON DELETE CASCADE`**, хотя в tenders-go каскада
   намеренно не было. Этого требует replace-флоу (§5, правило 3): смета удаляется
   целиком вместе с лотами, предложением, позициями и raw_data.
5. **`uq_proposals_lot_id`** — уникальный индекс по `lot_id`, закрепляющий
   «ровно одно предложение на лот» (§4). Слой `proposals` сохранён как задел на
   возврат тендеров; при их возврате индекс снимается одной строкой миграции.
6. **Добавленные CHECK-и, которых не было в источниках** (ослаблений нет, §2):
   `contracts.total_amount >= 0`; `amendment_no > 0` на `estimates` и
   `import_jobs` (`-1` занят сентинелом в `COALESCE`);
   `inflation_index > 0`; `matching_cache`: `source='manual' ⇒ expires_at IS NULL`
   — правило TTL из §4 теперь невозможно нарушить в обход кода.
7. **Nullability новых колонок** — по букве §4: `NOT NULL` только там, где бриф
   это написал (FK договора, `signed_date`), остальное (`title`, `signer`,
   `total_amount`, `notes`) — nullable. Поля, пришедшие из tenders-go, сохранили
   исходную обязательность (`objects.address`, `contractors.address`,
   `contractors.accreditation` — `NOT NULL`); если в фазе 5 это окажется
   неудобным для ручного заведения карточек, ослабление — отдельная ревизия.
8. **Временные метки новых таблиц — `timestamptz`** (как в tenders-go и как
   требует §4 для `matching_cache.expires_at`), тогда как таблицы фазы 1
   используют наивный `timestamp`. Единообразить фазой 2 не стали: это правка
   принятой миграции 0001.

## 4. Проверка

```bash
just db-migrate        # накат на gca_dev
just db-test-check     # gca_test + alembic check (дрейф ORM/БД)
just test-backend      # 203 passed
just lint-backend
# круговой рейс:
cd backend && DATABASE_URL=... uv run alembic downgrade base && uv run alembic upgrade head
```

Тесты схемы: `tests/integration/test_schema_constraints.py` (42 теста вместе с
VIEW) и `tests/integration/test_deviations_view.py`. Доменные фабрики для фаз 4–6
— в `tests/factories.py`.

## 5. Что осталось на следующие фазы

- `normalized_job_title` заполняется в Python при insert/update — самой функции
  нормализации (`sanitize_text.normalize_job_title_with_lemmatization`) в проекте
  ещё нет, она приезжает с парсером в фазе 3. В фабриках тестов сейчас стоит
  детерминированная заглушка.
- `catalog_positions.embedding` и HNSW-индекс созданы, но не используются:
  векторные подсказки — вне MVP (§5).
- Значение `norm_version` (константа в cache_key) вводится вместе с матчингом в
  фазе 4; колонка `matching_cache.norm_version` уже есть.
