# 2026-07-20 — ProjectPage load perf, PR-1: индексы горячих колонок + закрытие ORM/БД-дрейфа

**Ветка:** `perf/projectpage-load-pr1-indexes` → PR #38 (в `main`)
**Метод:** subagent-driven-development (Opus-оркестратор + ревьюер; Sonnet-исполнители; database-reviewer + python-reviewer на каждую задачу; Fable — финальный whole-branch; правки — диффом)
**Спека:** `docs/superpowers/specs/2026-07-20-projectpage-load-perf-design.md` §3. PR-1 из двух (бэкенд-only, поведенчески-нейтральный); PR-2 (переиспользование `calc_rows` + лоадер) — отдельно.

## Задача

`/projects/:id` грузится 1+ сек. PR-1 закрывает две вещи из спеки §3: (1) весь ORM/БД-дрейф по индексам (в БД есть индексы, не объявленные в моделях → `alembic --autogenerate` вечно шумит), (2) две горячие колонки без индексов (`documents.project_id`, путь `invoices(document_id) + date`). Реальное ускорение — задача PR-2; PR-1 — гигиена схемы + страховка на масштаб.

## Что сделано

1. **Рецепт `just db-test-check`** (justfile): локальная тест-БД :5433 → `alembic upgrade head` → `alembic check`, двумя отдельными строками (код возврата не маскируется). Приёмка дрейфа = **нулевой код** (`No new upgrade operations detected`), не «benign diff».
2. **Закрытие дрейфа (метаданные, без миграции):** в моделях объявлены 4 уже существующих в БД индекса — `ix_invoice_items_invoice_id_item_type` (композит), `ix_invoices_supplier_id`, `ix_suppliers_name_trgm` (GIN `gin_trgm_ops`), `uq_suppliers_name_no_inn` (partial unique `WHERE inn IS NULL`). Убран избыточный `index=True` с PK `Supplier.id` (закрывает единственный `create_index`-дифф автогена: `ix_suppliers_id` в БД никогда не было).
3. **Два новых индекса (модель + реальная миграция `6e3b8dc47ba9`):** `ix_documents_project_id` и композит `ix_invoices_document_id_date (document_id, date)`. Миграция обратима, линейная цепочка от `d184fbac0a71`, транзакционная (прод не развёрнут, таблицы малы; `CREATE INDEX CONCURRENTLY` отмечен в теле как путь при росте).
4. **TECH_DEBT:** снят чекбокс дрейфа (`- [x]`), поправлена устаревшая ссылка `models.py:282 → :291`, добавлена запись о ruff-`I001` в шаблоне `alembic/script.py.mako` (отдельный мелкий долг, всплыл здесь).
5. **Baseline-замер (Task 0)** на dev-Neon — `docs/superpowers/notes/2026-07-20-projectpage-load-perf-baseline.md` (точки 1 и 2 из трёх).

## Верификация

- `just db-test-check` → exit 0 (дрейф закрыт; новые индексы и в модели, и в БД) — перепроверено оркестратором после каждой задачи.
- `just lint` чист; `just test` — backend **579 passed / 6 skipped**, frontend **206 passed** (на финальном HEAD).
- Миграция применена на **dev-Neon** (`d184fbac0a71 → 6e3b8dc47ba9`), оба индекса подтверждены в `pg_indexes`.
- Ревью: каждая задача — database-reviewer + python-reviewer (оба Approved, 0 находок; дефиниции сверены с миграциями `b3c7e9f12a45`/`c7d8e9f0a1b2` byte-for-byte). Финал — Fable: Ready to merge = Yes, 0 Critical/Important.

## Решения и нюансы

- **Главное (честно): индексы PR-1 на текущих данных выигрыша не дают и не должны.** Замер на dev-Neon (project id=1: 105 инвойсов, 410 items, 16 мес): `compute_calculations` p50≈2.9 с, но EXPLAIN exec **0.07–0.13 мс** — путь **round-trip-bound** (~80 последовательных SQL × ~37 мс RTT до Neon), не query-bound. Точка 2 (после применения индексов): p50/p95 без изменений, EXPLAIN **всё ещё Seq Scan** — планировщик корректно игнорирует индексы на малых данных (seq scan дешевле). Это подтверждённая ставка на масштаб; реальный выигрыш даст PR-2 (устранение второго прогона `compute_calculations`).
- **Замер сделан прямым вызовом `compute_calculations`, не HTTP `/summary`** — auth-дэнс не нужен, а ядро (доминирующая работа) воспроизводимо во всех трёх точках. Harness read-only.
- **`ix_documents_project_id` частично перекрывается** левым префиксом уникального `uq_documents_project_file_hash (project_id, file_hash)` (замечание Codex P2 / Fable Minor). Решение (пользователь): **оставить + задокументировать** — спека §3.2, узкий одноколоночный B-tree плотнее композита для главного фильтра `project → documents`, а `documents` — low-write (накладные малы). Помечено в теле миграции и baseline-заметке: проверить пользу vs uq-префикс на нагрузочном наборе; если планировщик стабильно предпочтёт uq — кандидат на снос.
- **Метаданные-only безопасны для тест-схемы:** тесты строят схему через `alembic upgrade head` (не `create_all`), `pg_trgm` и trgm-индекс уже создаёт suppliers-миграция — объявление GIN/partial-unique в модели create-пути не порождает.
- **ruff `I001` на шаблоне миграций:** `alembic/script.py.mako` даёт import-порядок, который триггерит `I001` на каждом `just db-revision` (`pyproject.toml` игнорирует для `alembic/versions/*` E402/F401/UP007/UP035, но не I001). Правку шаблона в PR-1 не складывали (размывает чистый diff) — залогировано в TECH_DEBT.

## Что осталось

- **Merge PR #38** (решение пользователя), затем **PR-2** отдельным планом (`docs/superpowers/plans/2026-07-20-projectpage-load-perf-pr2-calc-reuse-loader.md`) — секции 1→2→4 спеки.
- **Точка 3 замера** — после PR-2 (тот же harness, проект id=1): ждём ~0 повторных вызовов `/calculations` при холодной загрузке + сокращение time-to-ready. Там же снять `EXPLAIN` по скану `documents` за `project_id` (вопрос перекрытия `ix_documents_project_id`).
- **Эффект индексов на масштабе** измерим только на нагрузочном наборе — это гейт вариантов B (set-based rewrite `compute_calculations`) / C.
- Мелкий долг: поправить import-порядок в `alembic/script.py.mako` (TECH_DEBT).
