# 2026-07-17 — Async Processing, Ступень 0

**Ветка:** `feat/async-processing-stage-0` (от `main` после PR #35)
**Спека:** [2026-07-16-async-processing-design.md](../superpowers/specs/2026-07-16-async-processing-design.md) (round 3.4, финал)
**План:** [2026-07-17-async-processing-stage-0.md](../superpowers/plans/2026-07-17-async-processing-stage-0.md)
**Метод:** subagent-driven-development (свежий субагент-исполнитель на задачу, ревью диффа между задачами).

## Задача

До этой ступени обработка документа (парсинг + запись в БД) была одной большой
операцией без промежуточного статуса: провальный разбор мог оставить документ
в `parsed` без единой СФ (артефакт Q2), гонка редактирования СФ во время
фонового переразбора не была защищена, event loop блокировался синхронным
`boto3`, а стоимость detect-вызова коррекции ориентации терялась молча при
сбое. Ступень 0 — не полноценная асинхронная очередь (это ступень 2), а
рефакторинг ядра обработки под статусную модель и явные доменные ошибки, БЕЗ
смены способа постановки задачи: обработка всё ещё выполняется инлайн
(`await` в хэндлере upload/reparse/deskew-reparse).

## Что сделано

Десять задач плана (Task 1–10), каждая — TDD, отдельный коммит:

1. **Статусная модель + миграция** (`3e99be5`, S0-1): `documents.status`
   `pending → processing → parsed | error` (`NOT NULL`, `server_default='pending'`),
   новые поля `processing_started_at`, `last_error`, `processing_run_id`
   (миграция `d184fbac0a71`).
2. **Доменные ошибки** (`86f7a9c`, S0-4): `ProcessingError`/`TransientError`/`PermanentError`
   с накопленным учётом `cost_usd`/`paid_calls` и опциональным `http_status`.
3. **Фаза A** (`9e20f7c`, S0-2): `pdf_parser.parse_pdf` — чистый LLM-вызов без
   БД, возвращает `ParseOutcome` или бросает доменную ошибку.
4. **Фаза B** (`aa0289c`, S0-2, Q7): `processing.persist_parse_result` —
   одна транзакция, parse-then-swap, единственный `commit`, явный `rollback`
   на детерминированном сбое.
5. **Async-обёртки S3** (`7eae02c`, S0-6): `download_file_async`/`upload_file_async`
   через `anyio.to_thread` — синхронный `boto3` больше не блокирует event loop.
6. **Ядро `process_document` + DI-фабрика + условная error-запись**
   (`3aa1235`, S0-3/S0-4, §2.3): три слоя вызова (обёртка → `run_processing_attempt`
   → фазы A/B), `get_processing_session_factory`, `write_processing_error`.
7. **Deskew на доменных ошибках + учёт detect-стоимости** (`fb1de03`, S0-4/S0-9/S0-6):
   `pdf_orientation` бросает `Transient/PermanentError` с `http_status` (413/502);
   detect-cost учитывается даже при последующем сбое; рендер/раст в потоке.
8. **Guard-переход + свап эндпоинтов на `process_document`** (`3f5b02e`, S0-5):
   `try_acquire_processing`, удалён старый `_reparse_from_s3`/`_is_not_found`
   из роутера (переехал в `processing.py`).
9. **FOR UPDATE + 409 + re-fetch под блокировкой** (`6ddeb3e`, S0-8): все шесть
   мутирующих эндпоинтов СФ (update/verify/unverify/delete/bulk-delete/delete-document)
   лочат документ и отклоняют мутацию при `processing`.
10. **`last_error` в API и ErrorDocsTab** (`da9ada5`, S0-7): причина ошибки
    видна пользователю, не только в логах.

Плюс два ревью-фикса между задачами (`6279696`, `043aa0a`) — покрытие
`get_processing_session_factory`/`session_factory=None` и веток `has_backup=True`
в `_run_deskew`.

## Архитектура (фазы A/B)

`backend/processing.py` — три слоя, неизменные между ступенями (меняется
только внешняя обёртка, §2.2 спеки):

- **`process_document`** — обёртка ступени 0/1: любой исход попытки → терминальный
  статус (`error` через `write_processing_error`, либо успех внутри фазы B).
  `reraise=True` пробрасывает только ошибки с `http_status` (deskew 413/502) —
  HTTP-контракт эндпоинтов не меняется.
- **`run_processing_attempt`** — одна попытка: (скачать / `_run_deskew`) →
  фаза A → фаза B. Не гасит доменные ошибки — пробрасывает с накопленной
  стоимостью.
- **`parse_pdf` (фаза A)** — чистый LLM-вызов, без БД.
- **`persist_parse_result` (фаза B)** — одна транзакция: `FOR UPDATE`,
  повторная проверка verified, parse-then-swap, единственный `commit`.

Подробности — `docs/agent/pdf-parsing.md` (раздел «Обработка документа»);
новые поля `Document` — `docs/agent/database.md`.

## Ключевые решения

- **Parse-then-swap, а не delete-then-parse.** Старые СФ живут до самого
  успешного commit'а фазы B. Провальный reparse/deskew-reparse больше не
  теряет данные — документ остаётся с прежним набором СФ, только уходит в
  `error` с `last_error`.
- **Инъектируемая фабрика сессий (F1).** `process_document`/`run_processing_attempt`
  принимают `session_factory` вместо жёсткого `SessionLocal()`. Причина —
  `conftest.py`: тестовая транзакция открыта с `join_transaction_mode="create_savepoint"`,
  голая `SessionLocal()` открыла бы отдельное соединение, не видящее
  незакоммиченные тестовые данные (и наоборот). `get_processing_session_factory`
  — FastAPI-dependency с поздним импортом `SessionLocal`, переопределяется в
  тестах через `app.dependency_overrides`.
- **Условная идемпотентная error-запись (§2.3).** `write_processing_error` —
  `UPDATE ... WHERE status='processing'`. При ambiguous commit (потеря
  соединения ровно во время `commit` фазы B) UPDATE дожидается конкурирующей
  транзакции и перепроверяет предикат на свежей версии строки (EvalPlanQual)
  → `rowcount=0`, если swap успел лечь — без гонки, без двойной записи.
  Ретраится только потеря соединения самой этой записи; всё остальное —
  пробрасывается (не глушим баги).
- **`FOR UPDATE` защищает мутации СФ от гонки с фазой B (S0-8).** Update/verify/
  unverify/delete/bulk-delete СФ и удаление документа лочат строку `Document`
  и отклоняют операцию 409, если он `processing`; после получения блокировки
  перезапрашивают СФ (могла успеть удалиться при parse-then-swap).
- **Учёт стоимости коррекции ориентации (S0-9).** `detect_rotations` всегда
  читает `usage.cost`, даже если содержимое ответа не парсится (безопасная
  деградация в нули на уровне контента ≠ неоплаченный вызов). Стоимость
  detect прибавляется к исходу фазы A composite-попыткой — и при успехе, и
  при ошибке парсинга.
- **Явные доменные ошибки вместо возвращаемых dict.** `TransientError`/`PermanentError`
  с `cost_usd`/`paid_calls`/`http_status` — единый язык ошибок между
  `pdf_parser`, `pdf_orientation` и `processing`, готовый к retry-политике
  ступени 2 (`TransientError` ретраится, `PermanentError` — никогда).

## Task 11 (Q2 backfill) — GATED и закрыта без миграции

Спека предусматривала гейтованный бэкфилл исторических документов, зависших в
`parsed` без единой СФ (артефакт старого поведения P3). Гейт по плану: сначала
SELECT-подсчёт кандидатов по реальной БД, миграция создаётся ТОЛЬКО если
счётчик > 0.

```sql
SELECT count(*) FROM documents d WHERE d.status='parsed' AND d.doc_type='unknown'
  AND NOT EXISTS (SELECT 1 FROM invoices i WHERE i.document_id = d.id);
SELECT count(*) FROM documents d WHERE d.status='parsed' AND d.doc_type='invoice'
  AND NOT EXISTS (SELECT 1 FROM invoices i WHERE i.document_id = d.id);
```

Прогнано на **dev-БД** (прод не развёрнут — dev здесь единственная база с
реальными, не тестовыми, данными). Оба счётчика вернули **0**. Согласно гейту
задача **закрыта без миграции** — ни новой ревизии, ни `op.execute` не
создавалось.

## Верификация

- `just lint` чист.
- Полный `just test` зелёный (backend + frontend), включая новые unit/integration
  тесты фаз A/B, `process_document`, условной error-записи (в т.ч. конкурентный
  тест на двух реальных соединениях, AC-S0-13), FOR UPDATE-защиты мутаций и
  deskew-учёта стоимости.
- Явные отклонения от прежнего поведения зафиксированы и приняты осознанно
  (self-review плана, конец файла плана): фаза A не создаёт `parsed` с 0 СФ;
  reparse отсутствующего в S3 файла → `error`+200 вместо 404; `doc_type` на
  error-пути не флипается; 413/502 deskew теперь помечают документ `error`
  (раньше не трогали) — причина видна в `last_error`, обычный reparse чинит.

## Хвосты

- Ступень 1 (фоновая задача + `startup-sweep` зависших `processing`) и ступень 2
  (persistent-очередь, `processing_run_id`-ownership, retry-политика на
  `TransientError`) — отдельные раунды дизайна, не в скоупе этой ветки.
- Read-timeout фронт-прокси для `deskew-reparse` (синхронный запрос
  worst-case ~6 мин) — см. `docs/agent/pdf-parsing.md` → «Долг».
- Документация: `docs/agent/pdf-parsing.md` (архитектура фаз A/B, доменные
  ошибки, guard, DI-фабрика, условная error-запись, deskew-cost),
  `docs/agent/database.md` (новые поля `Document`), `docs/testing.md`
  (`TEST_DATABASE_URL` и `pytest-dotenv`) обновлены в этом же коммите.
