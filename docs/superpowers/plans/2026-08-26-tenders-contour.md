# Тендерный контур и импорт сводных таблиц раунда — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Один загруженный файл сводной таблицы раунда становится N offer-сметами
и опциональной baseline-сметой атомарно, в решётке «участники × раунды» тендера,
с полным аудитом разбора и командами удаления, которые не могут потерять данные
молча.

**Architecture:** Четыре новые таблицы (`tenders → tender_rounds`,
`offer_packages → offers`) с составными FK, три владельца у `estimates` и два у
`import_jobs`. Импорт разветвляется в одной точке — `import_estimate` получает
`EstimateOwner` вместо `Contract`; для раунда новый `services/round_import.py`
режет разобранный JSON по каноническому ИНН на проекции и зовёт тот же
`import_estimate` по разу на участника и на baseline, затем один матчинг.
Удаления — команды с единой иерархией локов `tender → rounds → package` и
явным `DELETE offers` там, где `RESTRICT` не пропустит каскад. Экран `/tenders`
— список и карточка с решёткой.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2 / Alembic / PostgreSQL 16 /
pytest; React 19 / TanStack Query 5 / shadcn/ui / vitest + msw.

**Spec:** [`docs/superpowers/specs/2026-08-26-tenders-contour-design.md`](../specs/2026-08-26-tenders-contour-design.md)
(гейт 2 закрыт 26.08.2026 после двух кругов внешнего ревью). План на спеку
**ссылается** и её не пересказывает: при расхождении побеждает спека
(`AGENTS.md` §9.2). Рамка — [`docs/proposals/2026-08-25-tenders-model.md`](../../proposals/2026-08-25-tenders-model.md)
§3–§4: это фича 2 из четырёх.

**Ветка:** `feat/tenders-contour` — **уже существует**, в ней спека и запись 19
`TECH_DEBT.md`. Новой ветки не заводить; PR один, со ссылками на спеку и план.

---

## Global Constraints

Требования спеки, действующие в КАЖДОЙ задаче. Точные значения — из спеки.

1. **Иерархия локов одна для всех: `tender → rounds по id → package`** (§2.6,
   §2.11). Upload и удаление раунда: тендер `FOR KEY SHARE`, раунд `FOR UPDATE`.
   Удаление тендера: тендер `FOR UPDATE`, затем все раунды по `id`. Удаление
   участника: тендер `FOR UPDATE`, раунды по `id`, пакет `FOR UPDATE`. Никакой
   код не берёт эти строки в другом порядке.
2. **`RESTRICT` от пакета требует явного `DELETE offers`** в командах удаления
   участника и тендера — до пакета и до тендера (§2.11). Каскад БД уносит только
   то, что ниже `offers`.
3. **`NOT NULL` на всех колонках составных FK** (§2.1): PostgreSQL проверяет
   составной FK в режиме `MATCH SIMPLE`, и `NULL` в любой колонке отключает
   проверку.
4. **Три факта об одном файле** (§2.3): XLSX в `storage`; точный
   `ParseResult.data` в `import_jobs.parsed_data` + `parser_version`, пишет
   сессия A сразу после парсинга независимо от исхода импорта, у ОБОИХ владельцев;
   `estimate_raw_data` — проекция сметы.
5. **Канон ИНН — только ASCII `[0-9]`**, одна публичная `canonicalize_inn()` в
   create, update, поиске, импорте и сверке шапки; `CHECK (inn ~ '^[0-9]+$')`
   (§2.7). `str.isdigit()` не использовать: он шире.
6. **Замена раунда — целиком, до цикла** (§2.6): удаляются сметы, не `Offer` и
   не `offer_package`.
7. **Разбиение по каноническому ИНН через все лоты** (§2.7): пустой ИНН, повтор
   ИНН в лоте, расхождение наборов ИНН между лотами — отказ, не предупреждение.
8. **Baseline — по лотам** (§2.9): только валидные блоки, лоты без базы в
   warning; контур допработ для baseline выключен целиком.
9. **`deviation_from_baseline_cost`** — у offer-позиций через `.get()`; у
   baseline и у договора принудительно `NULL` (§2.10).
10. **`positions_total`** = позиции offer- и baseline-смет, **допущенные к
    каскаду матчинга**; равенство пяти счётчиков сохраняется (§2.5).
11. **Текущий job раунда** = `status = done` И число смет раунда с
    `import_job_id = job.id` равно `estimates_created` И других смет у раунда
    нет (§2.12).
12. **Договорной импорт**: внешнее и доменное поведение не меняется —
    существующие интеграционные тесты зелёные **без правок утверждений**;
    contract-job начинает писать `parsed_data`, `parser_version`,
    `estimates_created = 1` (новые утверждения).
13. **Паспорт не трогается**; `estimate_total_including_vat` — единогласие
    (§2.13); `_file_total_including_vat` остаётся как есть (`TECH_DEBT.md` 19).
14. **`backend/parser/` не меняется ни одной строкой.** `sheet_builders.py` в
    `tests/unit/parser/` — тестовая инфраструктура, её расширять можно.
15. **Права** (§2.13): чтение и upload — `member`; создание/правка/удаление
    тендера и раунда, удаление участника, `replace` — `admin`.
16. **Конфиденциальность:** реальные имена подрядчиков и объектов, номера
    тендеров и суммы не попадают в код, тесты, доки, коммиты и вывод. Файлы
    `samples/` называются первыми 12 знаками sha256.
17. **`just ci` перед пушем**; интеграционные тесты —
    `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration -k <pattern>`
    (рецепт `just test-int-local-k <pattern>`); юнит — `uv run pytest tests/unit -k <pattern>`;
    фронт — `cd frontend && npx vitest run <file>`; успех читать по коду
    возврата одиночной команды.
18. **Правки в существующих файлах — точечным редактированием**, не
    перезаписью файла целиком: дерево CRLF, полная перезапись флипает в LF, и
    `git status` показывает изменённый файл при пустом `git diff` (`AGENTS.md` §11).

---

## Решения плана (спека молчит — зафиксировано на гейте 3)

| | Решение | Основание |
|---|---|---|
| **Р1** | `canonicalize_inn` живёт в `backend/utils.py` рядом с `utcnow_aware` | нужна и `crud/`, и `services/`; `utils.py` уже импортируют оба слоя, а `crud/` не импортирует `services/` |
| **Р2** | Владельцы сметы — ОДИН dataclass `EstimateOwner` с полем `kind` и три конструктора-функции `contract_estimate_owner`, `offer_estimate_owner`, `baseline_estimate_owner` в `services/import_owners.py` | спека §2.4 называет три типа; тегированный dataclass даёт те же три различимых значения без иерархии классов, а `import_estimate` читает поля, не проверяет `isinstance` |
| **Р3** | `JobOwner` уровня пайплайна не заводится отдельным типом: `JobContext` получает `round_id`, и ветвление идёт по `contract_id is not None` | у пайплайна ровно две ветки и один потребитель; тип ради двух `if` — YAGNI |
| **Р4** | Разбиение JSON раунда и оркестрация — новый `services/round_import.py`; `estimate_import.py` не растёт | `estimate_import.py` уже 1150 строк; рамка §3.3 отвергла второй конвейер, но оркестратор над тем же `import_estimate` — не конвейер |
| **Р5** | Валидность baseline по лоту читается из JSON ПОСЛЕ постобработки: лот валиден, если `lot["baseline_proposal"]["title"] != BASELINE_MISSING_TITLE` | `postprocess` уже решил это на каждый лот (§1.3); второй вызов `_is_baseline_valid` — вторая правда |
| **Р6** | `estimate_total_including_vat` — новый модуль `crud/estimate_totals.py` | функция общая для контура и (позже, запись 19) для паспорта; ни в `tenders.py`, ни в `project_passport.py` ей не место |
| **Р7** | `confirmation_token` = sha256 от JSON `{"offers": sorted ids, "estimates": sorted ids, "positions": n, "overrides": n}` | спека §2.11 требует состав, а не счётчики; JSON с сортировкой детерминирован |
| **Р8** | Общий компонент загрузки — `components/imports/ImportJobPanel.tsx` (dropzone, поллинг, счётчики, предупреждения); `EstimateUploadPanel` и новый `RoundUploadPanel` — оболочки | спека §2.14: не обобщать union-пропом |
| **Р9** | Пункт меню «Тендеры» — иконка `Gavel` из lucide-react, между «Договоры» и «Ручной матчинг» | тендер предшествует договору по смыслу |
| **Р10** | Тесты гонок — по образцу `test_contract_cascade_delete.py` (`after_cursor_execute`-проба внутри `FOR UPDATE`) и `test_category_override_concurrency.py` (два потока, `_wait_until_a_backend_blocks`) | оба приёма в проекте есть и доказаны снятием защиты |

---

## Проверенные символы

Проверено `grep`-ом по дереву 26.08.2026 (`AGENTS.md` §9.1). Существующее —
брать как есть; помеченное «заводится» создаётся названной задачей.

| Символ | Где | Статус |
|---|---|---|
| `Contract`, `Contractor`, `ImportJob`, `Estimate`, `EstimateRawData`, `Lot`, `Proposal`, `ProposalSummaryLine`, `PositionItem`, `EstimateCategoryOverride`, `ImportJobStatus`, `TERMINAL_IMPORT_JOB_STATUSES`, `ACTIVE_IMPORT_JOB_STATUSES`, `_sql_str_list`, `_created_at`, `_updated_at` | `backend/models.py` | существует |
| `utcnow_aware` | `backend/utils.py` | существует |
| `DomainError`, `translating_integrity`, `require_text`, `paginated`, `clamp_page`, `iso`, `rollback_on_domain_error`, `UNSET` | `backend/crud/common.py` | существует |
| `raise_domain_error` | `backend/routers/domain_errors.py` | существует |
| `import_estimate`, `ImportOutcome`, `PositionToMatch`, `EstimateImportError`, `compare_header_with_contract`, `_validate_payload`, `_replace_existing`, `_warn_on_unexpected_baseline`, `_import_additional_info`, `_import_additional_works`, `_import_positions`, `extract_single_proposal`, `extract_positions`, `find_estimate`, `_money`, `_text`, `_digits`, `_loose` | `backend/services/estimate_import.py` | существует; `compare_header_with_contract` → `compare_header`, `_digits` удаляется (задача 1/3) |
| `decide_owner`, `OwnerDecision` | `backend/services/additional_works.py` | существует, не меняется |
| `run_import_job`, `JobContext`, `load_job_context`, `StatusWriter`, `finalize_done`, `_warnings_concat` | `backend/services/import_pipeline.py` | существует, правится задачами 4, 6 |
| `match_positions`, `MatchCounters` | `backend/services/matching.py` | существует, не меняется |
| `recover_interrupted_jobs`, `purge_expired_error_job_files`, `purge_files_best_effort`, `run_startup_maintenance` | `backend/services/maintenance.py` | существует, не меняется |
| `delete_contract`, `list_contract_import_jobs`, `get_contract` | `backend/crud/contracts.py` | существует, образец |
| `create_contractor`, `update_contractor`, `list_contractors`, `delete_contractor`, `_UNIQUE_MESSAGES` | `backend/crud/references.py` | существует, правится задачами 1, 7 |
| `_file_total_including_vat` | `backend/crud/project_passport.py` | существует, **не меняется** |
| `job_response`, `upload_estimate`, `_read_within_limit`, `_validate_xlsx`, `_active_job`, `ACTIVE_PAIR_INDEX`, `CONTRACT_FK_CONSTRAINT` | `backend/routers/estimates.py` | существует; `job_response` правится задачей 8 |
| `download_import_job_file`, `_get_job` | `backend/routers/import_jobs.py` | существует, правится задачей 8 |
| `RAW_SQL_INDEXES` | `backend/alembic/env.py` | существует, дополняется задачей 2 |
| `BASELINE_MISSING_TITLE` | `backend/parser/postprocess.py` | существует |
| `JSON_KEY_LOTS`, `JSON_KEY_PROPOSALS`, `JSON_KEY_BASELINE_PROPOSAL`, `JSON_KEY_LOT_TITLE`, `JSON_KEY_CONTRACTOR_TITLE`, `JSON_KEY_CONTRACTOR_INN`, `JSON_KEY_CONTRACTOR_ADDRESS`, `JSON_KEY_CONTRACTOR_ACCREDITATION`, `JSON_KEY_CONTRACTOR_ITEMS`, `JSON_KEY_CONTRACTOR_ADDITIONAL_INFO`, `JSON_KEY_DEVIATION_FROM_CALCULATED_COST`, `JSON_KEY_TENDER_OBJECT`, `JSON_KEY_TENDER_ADDRESS`, `JSON_KEY_TOTAL_COST_INCLUDING_VAT` | `backend/parser/constants.py` | существует |
| `payload_for`, `estimate_payload`, `proposal`, `position`, `summary_line`, `DEFAULT_INN` | `backend/tests/payloads.py` | существует |
| `gp_sheet`, `add_contractor_block`, `KEYS_8`, `KEYS_GP_11`, `KEYS_TENDER_11`, `KEYS_12` | `backend/tests/unit/parser/sheet_builders.py` | существует; `add_contractor_block` получает `inn`, `address` задачей 6 (по умолчанию `None` — прежние тесты парсера не меняются) |
| `parse_worksheet` | `backend/parser` | существует |
| `ContractorFactory`, `ContractFactory`, `ImportJobFactory`, `EstimateFactory`, `LotFactory`, `ProposalFactory`, `_BaseFactory` | `backend/tests/factories.py` | существует |
| `db_session`, `factories`, `client`, `committing_db`, `committing_factories`, `committing_session_factory`, `committing_client`, `tmp_storage` | `backend/tests/conftest.py` | существует |
| `rejected` | `backend/tests/integration/test_schema_constraints.py` | существует |
| `fake_parse`, `job_env` | `backend/tests/integration/test_import_pipeline.py` | существует |
| `xlsx_bytes`, `stub_parser`, `upload`, `finished_job` | `backend/tests/integration/test_estimates_api.py` | существует, образец |
| `_wait_until_a_backend_blocks` | `backend/tests/integration/test_category_override_concurrency.py` | существует, образец |
| `contractsApi`, `estimatesApi`, `api` | `frontend/src/services/api/domain.ts`, `frontend/src/lib/api.ts` | существует |
| `qk` | `frontend/src/services/queryKeys.ts` | существует, дополняется |
| `useContract`, `useUploadEstimate`, `useImportJob`, `apiErrorDetail`, `apiErrorStatus` | `frontend/src/services/queries.ts` | существует |
| `jobRefetchInterval`, `isTerminal` | `frontend/src/services/jobPolling.ts` | существует |
| `Dropzone`, `StatusPill`, `Surface`, `EmptyState`, `PageHeader`, `Breadcrumbs`, `Skeleton`, `Pager` | `frontend/src/components/ui-domain/`, `components/domain/` | существует |
| `renderWithProviders`, `server`, `handlerState`, `resetHandlerState` | `frontend/src/test/utils.tsx`, `server.ts`, `handlers.ts` | существует |
| `EstimateUploadPanel` | `frontend/src/components/contracts/EstimateUploadPanel.tsx` | существует, правится задачей 11 |
| `canonicalize_inn` | `backend/utils.py` | **заводится задачей 1** |
| `Tender`, `TenderRound`, `OfferPackage`, `Offer` | `backend/models.py` | **заводится задачей 2** |
| `TenderFactory`, `TenderRoundFactory`, `OfferPackageFactory`, `OfferFactory` | `backend/tests/factories.py` | **заводится задачей 2** |
| `EstimateOwner`, `HeaderTruth`, `contract_estimate_owner`, `offer_estimate_owner`, `baseline_estimate_owner`, `compare_header` | `backend/services/import_owners.py`, `estimate_import.py` | **заводится задачей 3** |
| `split_round_payload`, `RoundProjection`, `BaselineProjection`, `replace_round_estimates`, `get_or_create_contractor`, `get_or_create_package`, `get_or_create_offer`, `import_round`, `RoundImportOutcome` | `backend/services/round_import.py` | **заводится задачей 6** |
| `estimate_total_including_vat` | `backend/crud/estimate_totals.py` | **заводится задачей 7** |
| `crud/tenders.py`: `list_tenders`, `get_tender_card`, `create_tender`, `update_tender`, `delete_tender`, `create_round`, `update_round`, `delete_round`, `delete_participant`, `participant_deletion_preview`, `current_round_job`, `list_round_import_jobs` | `backend/crud/tenders.py` | **заводится задачей 7** |
| `routers/tenders.py` | `backend/routers/tenders.py` | **заводится задачей 8** |
| `ImportJobPanel`, `RoundUploadPanel`, `TendersPage`, `TenderCardPage`, `TenderFormDialog`, `ParticipantDeleteDialog` | `frontend/src/components/imports/`, `components/tenders/`, `pages/tenders/` | **заводятся задачами 11, 12** |
| `docs/devlog/2026-08-26-tenders-contour.md` | — | **заводится задачей 13** |

---

## Структура файлов

**Создаётся**

| Файл | Ответственность |
|---|---|
| `backend/alembic/versions/2026_08_26_0015-tenders_contour.py` | Четыре таблицы, правки `estimates`/`proposals`/`import_jobs`/`contractors`, пересечение частичных индексов raw SQL, миграция данных ИНН с отказом, `downgrade` с тремя счётчиками |
| `backend/services/import_owners.py` | `HeaderTruth`, `EstimateOwner` и три конструктора — что сверять с шапкой, куда писать владельца, писать ли подрядчика/отклонение/допработы |
| `backend/services/round_import.py` | Разбиение JSON раунда по каноническому ИНН, проекции участников и baseline, get-or-create цепочка, замена уровнем раунда, цикл импорта, объединённый результат |
| `backend/crud/estimate_totals.py` | `estimate_total_including_vat` — единогласие |
| `backend/crud/tenders.py` | CRUD тендеров и раундов, карточка с решёткой, три команды удаления, текущий job, история round-jobs |
| `backend/routers/tenders.py` | HTTP-контракт §2.13 |
| `backend/tests/unit/test_inn.py` | Канон ИНН |
| `backend/tests/integration/test_tenders_schema.py` | Пробой каждого `CHECK`/индекса, миграция данных, `downgrade` |
| `backend/tests/integration/test_import_owners.py` | `import_estimate` под тремя владельцами |
| `backend/tests/integration/test_round_import.py` | Разбиение, атомарность, замена, baseline по лотам, единый матчинг |
| `backend/tests/integration/test_tenders_crud.py` | Карточка, итог, удаления на полной решётке, `delete_contractor` |
| `backend/tests/integration/test_tenders_api.py` | Маршруты, идемпотентность, token, права |
| `backend/tests/integration/test_tenders_concurrency.py` | Гонки и дедлок, recovery раундового лока |
| `frontend/src/components/imports/ImportJobPanel.tsx` (+test) | Общая часть загрузки |
| `frontend/src/components/tenders/RoundUploadPanel.tsx`, `TenderFormDialog.tsx`, `ParticipantDeleteDialog.tsx`, `RoundDeleteDialog.tsx`, `OfferGrid.tsx` (+tests) | Оболочки экрана тендеров |
| `frontend/src/pages/tenders/TendersPage.tsx`, `TenderCardPage.tsx` (+tests) | Список и карточка |
| `docs/devlog/2026-08-26-tenders-contour.md` | Отступления, замеры, границы |

**Правится**

| Файл | Что именно |
|---|---|
| `backend/utils.py` | `canonicalize_inn` |
| `backend/models.py` | Четыре модели; nullable `contract_id`, `offer_id`, `round_id`, новые `CHECK` у `Estimate`/`ImportJob`/`Proposal`/`Contractor`; `parsed_data`, `parser_version`, `estimates_created` |
| `backend/alembic/env.py` | `RAW_SQL_INDEXES` += `uq_estimates_offer`, `uq_estimates_round`, `uq_import_jobs_active_round` |
| `backend/crud/references.py` | канон ИНН в create/update/search; третий потребитель в `delete_contractor` |
| `backend/services/estimate_import.py` | `owner`+`truth` вместо `contract`; `compare_header`; `_import_positions` читает deviation по флагу владельца; baseline без допработ |
| `backend/services/import_pipeline.py` | `JobContext.round_id`; `parsed_data`/`parser_version` сессией A; ветка раунда; `estimates_created` |
| `backend/routers/estimates.py` | `job_response` — `owner_type`, `estimate_ids[]` у раунда |
| `backend/routers/import_jobs.py` | текст 410 обобщён по владельцу |
| `backend/main.py` | регистрация роутера |
| `backend/tests/factories.py` | четыре фабрики |
| `backend/tests/payloads.py` | `round_payload(...)` — JSON раунда с N предложениями и baseline в форме ПОСЛЕ постобработки |
| `backend/tests/unit/parser/sheet_builders.py` | `add_contractor_block(inn=, address=)` — реквизиты под заголовком блока, чтобы настоящий парсер отдавал ИНН участника |
| Тесты, зовущие `import_estimate(contract=…)` | переход на `owner=contract_estimate_owner(...)` — механическая замена сигнатуры без изменения утверждений |
| `frontend/src/types/domain.ts`, `services/api/domain.ts`, `services/queryKeys.ts`, `services/queries.ts` | типы, API, ключи, хуки |
| `frontend/src/test/fixtures.ts`, `handlers.ts` | фикстуры и хендлеры тендеров |
| `frontend/src/components/contracts/EstimateUploadPanel.tsx` | рендер через `ImportJobPanel`; поведение и тесты прежние |
| `frontend/src/App.tsx`, `components/layout/TopNav.tsx` | маршруты, пункт меню |
| `AGENTS.md` §3, §4, §5, §7, §8 | ревизия §2.15 спеки, одним коммитом |

**Не трогать:** `backend/parser/**` (кроме `tests/unit/parser/sheet_builders.py`),
`backend/crud/project_passport.py`, `analytics.py`, `comparison.py`,
`dashboard.py`, `services/matching.py`, `additional_works.py`,
`category_*.py`, `postprocess.py`, `backend/tests/conftest.py`.

## Task 1: Канон ИНН — одна функция, справочник подрядчиков на ней

Спека §1.5, §2.7, Global Constraint 5. Чистая функция плюс перевод трёх мест
`references.py`. Никакой схемы — `CHECK` и миграция данных приходят задачей 2,
когда уже есть чем канонизировать.

**Files:**
- Modify: `backend/utils.py`
- Modify: `backend/crud/references.py` (`create_contractor`, `update_contractor`, `list_contractors`)
- Modify: `backend/services/estimate_import.py:287` (`_digits` → `canonicalize_inn`)
- Test: `backend/tests/unit/test_inn.py`
- Test: `backend/tests/integration/test_references_api.py` (три новых теста)

**Interfaces:**
- Produces: `utils.canonicalize_inn(value: Any) -> str` — только ASCII `[0-9]`
  из `str(value)`; `None` → `""`.

- [x] **Step 1: падающий юнит-тест**

`backend/tests/unit/test_inn.py`:

```python
"""Канон ИНН — ASCII-цифры и ничего больше (спека контура §1.5, §2.7).

`str.isdigit()` шире: полноширинные, арабско-индийские и надстрочные цифры
проходят его, а `CHECK (inn ~ '^[0-9]+$')` в PostgreSQL их отвергнет — вставка
упала бы с необъяснимым отказом. Канон обязан совпадать с тем, что проверит база.
"""
from __future__ import annotations

import pytest

from utils import canonicalize_inn


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("7700123456", "7700123456"),
        ("77 00 123 456", "7700123456"),
        ("7700-123-456", "7700123456"),
        ("ИНН 7700123456", "7700123456"),
        (7700123456, "7700123456"),
        (None, ""),
        ("", ""),
        ("   ", ""),
    ],
)
def test_keeps_only_ascii_digits(raw, expected):
    assert canonicalize_inn(raw) == expected


def test_non_ascii_digits_are_dropped_not_converted():
    """Полноширинная 7, арабско-индийская 3 и надстрочная 2 — все `isdigit()`,
    ни одна не `[0-9]`. Канон их выбрасывает, а не «переводит»: перевод
    выдумал бы цифру, которой в документе подрядчика нет."""
    assert canonicalize_inn("７７00٣²") == "00"
    assert "７７00٣²".isdigit()  # предпосылка теста: isdigit действительно шире
```

- [x] **Step 2: убедиться, что тест падает**

Run: `cd backend && uv run pytest tests/unit/test_inn.py -v`
Expected: `ImportError: cannot import name 'canonicalize_inn' from 'utils'`.

- [x] **Step 3: реализовать `canonicalize_inn`**

В `backend/utils.py`, после существующих импортов, добавить `import re` и:

```python
_NON_ASCII_DIGIT = re.compile(r"[^0-9]")


def canonicalize_inn(value: object) -> str:
    """Канонический ИНН/БИН: только ASCII-цифры `[0-9]`, без разделителей.

    Единственная форма записи ИНН в проекте — ею сравнивают карточки, ищут
    подрядчика из файла раунда и сверяют шапку с карточкой; та же форма
    закреплена `CHECK (inn ~ '^[0-9]+$')` на `contractors` (миграция 0015).
    `str.isdigit()` здесь непригоден: он принимает цифры других письменностей,
    которые `CHECK` отвергнет. Длина не проверяется — разрядность отличается по
    юрисдикции (10/12 в РФ, 12 в РК), и это прежнее решение справочника.
    """
    if value is None:
        return ""
    return _NON_ASCII_DIGIT.sub("", str(value))
```

- [x] **Step 4: юнит-тест зелёный**

Run: `cd backend && uv run pytest tests/unit/test_inn.py -v`
Expected: PASS, 9 тестов.

- [x] **Step 5: падающие интеграционные тесты справочника**

В `backend/tests/integration/test_references_api.py`, рядом с
`test_delete_unused_contractor_succeeds`:

```python
def test_contractor_inn_is_stored_canonically(client):
    """Разделители не создают новой идентичности (спека контура §2.7)."""
    created = client.post(
        "/api/v1/contractors", json={"title": "ООО Канон", "inn": "77 00-123 456"}
    )
    assert created.status_code == 201
    assert created.json()["inn"] == "7700123456"


def test_contractor_inn_duplicate_detected_across_formatting(client, factories):
    factories.ContractorFactory.create(inn="7700123456")
    response = client.post(
        "/api/v1/contractors", json={"title": "ООО Дубль", "inn": "77 00 123 456"}
    )
    assert response.status_code == 409


def test_contractor_search_by_formatted_inn_finds_canonical_row(client, factories):
    """Хранится «7700123456», человек ищет «77 00 12» — обязан найти."""
    factories.ContractorFactory.create(title="ООО Искомый", inn="7700123456")
    found = client.get("/api/v1/contractors", params={"q": "77 00 12"}).json()
    assert [row["title"] for row in found["items"]] == ["ООО Искомый"]


def test_contractor_inn_of_only_separators_is_422(client):
    response = client.post("/api/v1/contractors", json={"title": "ООО Пусто", "inn": " - - "})
    assert response.status_code == 422
```

- [x] **Step 6: убедиться, что три из четырёх падают**

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_references_api.py -k "canonical or formatting or formatted_inn or separators" -v`
Expected: `test_contractor_inn_is_stored_canonically` FAIL (`"77 00-123 456" != "7700123456"`),
`..._duplicate_detected...` FAIL (201 вместо 409), `..._search_by_formatted...`
FAIL (пусто), `..._only_separators...` PASS уже сейчас — `require_text` его
ловит; он остаётся как контроль, что канонизация не сломает этот отказ.

- [x] **Step 7: перевести `references.py` на канон**

В `create_contractor` заменить

```python
    inn = require_text(inn, "БИН/ИНН")
```

на

```python
    inn = canonicalize_inn(require_text(inn, "БИН/ИНН"))
    if not inn:
        raise DomainError(422, "Поле «БИН/ИНН» не содержит ни одной цифры.")
```

В `update_contractor` заменить

```python
        if inn is not UNSET:
            contractor.inn = require_text(inn, "БИН/ИНН")
```

на

```python
        if inn is not UNSET:
            canonical = canonicalize_inn(require_text(inn, "БИН/ИНН"))
            if not canonical:
                raise DomainError(422, "Поле «БИН/ИНН» не содержит ни одной цифры.")
            contractor.inn = canonical
```

В `list_contractors` заменить условие поиска:

```python
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        conditions = [Contractor.title.ilike(pattern)]
        # ИНН хранится каноном (только цифры), а человек вводит его с
        # разделителями — ищем по канону запроса, иначе «77 00 12» не нашёл
        # бы сохранённое «7700123456» (спека контура §2.7).
        inn_digits = canonicalize_inn(q)
        if inn_digits:
            conditions.append(Contractor.inn.like(f"%{inn_digits}%"))
        stmt = stmt.where(sa.or_(*conditions))
```

Импорт: `from utils import canonicalize_inn` в шапке `references.py`.

- [x] **Step 8: `estimate_import.py` — тот же канон в сверке шапки**

Удалить `_digits` (строки 287–289) и в `compare_header_with_contract` заменить
оба вызова `_digits(...)` на `canonicalize_inn(...)`; добавить
`from utils import canonicalize_inn`. Докстрока `_loose` рядом не меняется.

- [x] **Step 9: интеграционные тесты зелёные, старые не задеты**

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_references_api.py tests/integration/test_estimate_import.py -q`
Expected: PASS все.

- [x] **Step 10: ruff и юнит-набор**

Run: `cd backend && uv run ruff check .` — clean.
Run: `cd backend && uv run pytest tests/unit -q` — PASS.

- [x] **Step 11: Commit**

```bash
git add backend/utils.py backend/crud/references.py backend/services/estimate_import.py \
  backend/tests/unit/test_inn.py backend/tests/integration/test_references_api.py
git commit -m "feat(tenders-contour): канон ИНН — одна функция в utils, справочник и сверка шапки на ней"
```

---

## Task 2: Схема — четыре таблицы, три владельца, миграция 0015

Спека §2.1, §2.2. Модели, миграция с raw SQL для частичных индексов, миграция
данных ИНН с отказом, `downgrade` с тремя счётчиками, фабрики, parity-тесты.
Ничто существующее поведения не меняет: старые строки получают `NULL` в новых
колонках и проходят все новые `CHECK`.

**Files:**
- Modify: `backend/models.py`
- Create: `backend/alembic/versions/2026_08_26_0015-tenders_contour.py`
- Modify: `backend/alembic/env.py:56-62` (`RAW_SQL_INDEXES`)
- Modify: `backend/tests/factories.py`
- Test: `backend/tests/integration/test_tenders_schema.py`

**Interfaces:**
- Produces (модели): `Tender(id, object_id, title, tender_number, rate_class_id, notes, created_at, updated_at)`;
  `TenderRound(id, tender_id, stage_no, label, held_on, created_at, updated_at)`;
  `OfferPackage(id, tender_id, contractor_id, created_at)`;
  `Offer(id, tender_id, round_id, package_id, created_at)`;
  `Estimate.offer_id`, `Estimate.round_id` (nullable), `Estimate.contract_id` nullable;
  `Proposal.contractor_id` nullable;
  `ImportJob.round_id`, `.parsed_data`, `.parser_version`, `.estimates_created` (nullable), `.contract_id` nullable;
  relationships: `Tender.rounds`, `Tender.packages`, `TenderRound.tender`, `TenderRound.offers`,
  `OfferPackage.tender`, `OfferPackage.contractor`, `OfferPackage.offers`, `Offer.round`, `Offer.package`,
  `Estimate.offer`, `Estimate.round`, `ImportJob.round`.
- Produces (фабрики): `TenderFactory`, `TenderRoundFactory(tender=…, stage_no=…)`,
  `OfferPackageFactory(tender=…, contractor=…)`, `OfferFactory(round=…, package=…)` —
  `OfferFactory` ВЫВОДИТ `tender_id` из `round.tender_id`.
- Produces (константы имён): см. шаг 3 — они же в parity-тестах.

- [ ] **Step 1: падающие тесты схемы**

`backend/tests/integration/test_tenders_schema.py`:

```python
"""Схема тендерного контура (спека §2.1): каждый CHECK и частичный индекс —
пробоем, по одному нарушенному ограничению на вход.

Составные FK проверяются в режиме MATCH SIMPLE, и `NOT NULL` на их колонках —
условие, при котором проверка вообще выполняется; тест на `round_id = NULL` —
прямой пробой этого свойства.
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from models import Estimate, ImportJob, ImportJobStatus, Offer, Proposal
from tests.integration.test_schema_constraints import rejected

pytestmark = pytest.mark.integration


class TestTenderTables:
    def test_tender_number_unique(self, db_session, factories):
        factories.TenderFactory.create(tender_number="T-1")
        with rejected(db_session, contains="uq_tenders_tender_number"):
            factories.TenderFactory.create(tender_number="T-1")

    @pytest.mark.parametrize("field", ["title", "tender_number"])
    def test_blank_text_rejected(self, db_session, factories, field):
        with rejected(db_session, contains=f"ck_tenders_{'title' if field == 'title' else 'number'}_not_blank"):
            factories.TenderFactory.create(**{field: "   "})

    def test_stage_no_positive(self, db_session, factories):
        with rejected(db_session, contains="ck_tender_rounds_stage_no"):
            factories.TenderRoundFactory.create(stage_no=0)

    def test_stage_no_unique_within_tender(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create(stage_no=1)
        with rejected(db_session, contains="uq_tender_rounds_tender_stage"):
            factories.TenderRoundFactory.create(tender=rnd.tender, stage_no=1)

    def test_one_package_per_contractor_per_tender(self, db_session, factories):
        pkg = factories.OfferPackageFactory.create()
        with rejected(db_session, contains="uq_offer_packages_tender_contractor"):
            factories.OfferPackageFactory.create(tender=pkg.tender, contractor=pkg.contractor)


class TestOfferGridIntegrity:
    def test_offer_unique_per_round_and_package(self, db_session, factories):
        offer = factories.OfferFactory.create()
        with rejected(db_session, contains="uq_offers_round_package"):
            factories.OfferFactory.create(round=offer.round, package=offer.package)

    def test_round_and_package_must_belong_to_the_same_tender(self, db_session, factories):
        """Скрещивание тендеров: раунд тендера A с участником тендера B."""
        round_a = factories.TenderRoundFactory.create()
        package_b = factories.OfferPackageFactory.create()
        assert round_a.tender_id != package_b.tender_id
        with rejected(db_session):
            db_session.add(Offer(tender_id=round_a.tender_id, round_id=round_a.id, package_id=package_b.id))
            db_session.flush()

    def test_offer_without_round_is_impossible(self, db_session, factories):
        """MATCH SIMPLE: при NULL в round_id составной FK НЕ проверяется — только
        NOT NULL делает ячейку без раунда непредставимой."""
        package = factories.OfferPackageFactory.create()
        with rejected(db_session, contains="round_id"):
            db_session.add(Offer(tender_id=package.tender_id, round_id=None, package_id=package.id))
            db_session.flush()

    def test_offer_without_package_is_impossible(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        with rejected(db_session, contains="package_id"):
            db_session.add(Offer(tender_id=rnd.tender_id, round_id=rnd.id, package_id=None))
            db_session.flush()


class TestEstimateOwners:
    def test_exactly_one_owner_required(self, db_session, factories):
        offer = factories.OfferFactory.create()
        contract = factories.ContractFactory.create()
        with rejected(db_session, contains="ck_estimates_owner"):
            db_session.add(Estimate(contract_id=contract.id, offer_id=offer.id))
            db_session.flush()
        with rejected(db_session, contains="ck_estimates_owner"):
            db_session.add(Estimate())
            db_session.flush()

    def test_offer_estimate_accepted(self, db_session, factories):
        offer = factories.OfferFactory.create()
        db_session.add(Estimate(offer_id=offer.id))
        db_session.flush()

    def test_baseline_estimate_accepted_once_per_round(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.add(Estimate(round_id=rnd.id))
        db_session.flush()
        with rejected(db_session, contains="uq_estimates_round"):
            db_session.add(Estimate(round_id=rnd.id))
            db_session.flush()

    def test_one_estimate_per_offer(self, db_session, factories):
        offer = factories.OfferFactory.create()
        db_session.add(Estimate(offer_id=offer.id))
        db_session.flush()
        with rejected(db_session, contains="uq_estimates_offer"):
            db_session.add(Estimate(offer_id=offer.id))
            db_session.flush()

    def test_amendment_no_only_with_contract(self, db_session, factories):
        offer = factories.OfferFactory.create()
        with rejected(db_session, contains="ck_estimates_amendment_owner"):
            db_session.add(Estimate(offer_id=offer.id, amendment_no=1))
            db_session.flush()

    def test_contract_pair_uniqueness_survives_partial_index(self, db_session, factories):
        """Частичный индекс держит прежний инвариант договоров (§2.1)."""
        contract = factories.ContractFactory.create()
        factories.EstimateFactory.create(contract=contract, amendment_no=None)
        with rejected(db_session, contains="uq_estimates_contract_amendment"):
            factories.EstimateFactory.create(contract=contract, amendment_no=None)


class TestProposalBaseline:
    def test_baseline_proposal_has_no_contractor(self, db_session, factories):
        lot = factories.LotFactory.create()
        db_session.add(Proposal(lot_id=lot.id, contractor_id=None, is_baseline=True))
        db_session.flush()

    def test_baseline_with_contractor_rejected(self, db_session, factories):
        lot = factories.LotFactory.create()
        contractor = factories.ContractorFactory.create()
        with rejected(db_session, contains="ck_proposals_baseline_contractor"):
            db_session.add(Proposal(lot_id=lot.id, contractor_id=contractor.id, is_baseline=True))
            db_session.flush()

    def test_participant_proposal_without_contractor_rejected(self, db_session, factories):
        lot = factories.LotFactory.create()
        with rejected(db_session, contains="ck_proposals_baseline_contractor"):
            db_session.add(Proposal(lot_id=lot.id, contractor_id=None, is_baseline=False))
            db_session.flush()


class TestImportJobOwners:
    def _job(self, **kw):
        return ImportJob(
            filename="f.xlsx", file_key=kw.pop("file_key", "k-1"), file_sha256="0" * 64,
            status=ImportJobStatus.pending.value, **kw,
        )

    def test_exactly_one_owner(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        contract = factories.ContractFactory.create()
        with rejected(db_session, contains="ck_import_jobs_owner"):
            db_session.add(self._job(contract_id=contract.id, round_id=rnd.id))
            db_session.flush()

    def test_amendment_no_only_with_contract(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        with rejected(db_session, contains="ck_import_jobs_amendment_owner"):
            db_session.add(self._job(round_id=rnd.id, amendment_no=1))
            db_session.flush()

    def test_active_round_lock(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.add(self._job(round_id=rnd.id, file_key="k-a"))
        db_session.flush()
        with rejected(db_session, contains="uq_import_jobs_active_round"):
            db_session.add(self._job(round_id=rnd.id, file_key="k-b"))
            db_session.flush()

    @pytest.mark.parametrize("terminal", [ImportJobStatus.done, ImportJobStatus.error])
    def test_terminal_round_job_releases_lock(self, db_session, factories, terminal):
        rnd = factories.TenderRoundFactory.create()
        db_session.add(self._job(round_id=rnd.id, file_key="k-a", status=terminal.value))
        db_session.add(self._job(round_id=rnd.id, file_key="k-b"))
        db_session.flush()

    def test_parsed_data_and_version_come_together(self, db_session, factories):
        contract = factories.ContractFactory.create()
        with rejected(db_session, contains="ck_import_jobs_parsed_pair"):
            db_session.add(self._job(contract_id=contract.id, parsed_data={"lots": {}}))
            db_session.flush()
        with rejected(db_session, contains="ck_import_jobs_parsed_pair"):
            db_session.add(self._job(contract_id=contract.id, parser_version="4.0.0"))
            db_session.flush()

    def test_estimates_created_positive_or_null(self, db_session, factories):
        contract = factories.ContractFactory.create()
        with rejected(db_session, contains="ck_import_jobs_estimates_created"):
            db_session.add(self._job(contract_id=contract.id, estimates_created=0))
            db_session.flush()
        db_session.add(self._job(contract_id=contract.id, estimates_created=None, file_key="k-null"))
        db_session.flush()


class TestContractorInn:
    def test_non_canonical_inn_rejected_by_schema(self, db_session, factories):
        """Правка мимо приложения — тоже под каноном (спека §2.7)."""
        with rejected(db_session, contains="ck_contractors_inn_canonical"):
            factories.ContractorFactory.create(inn="77 00 123")

    def test_empty_inn_rejected_by_schema(self, db_session, factories):
        with rejected(db_session, contains="ck_contractors_inn_canonical"):
            factories.ContractorFactory.create(inn="")
```

- [ ] **Step 2: убедиться, что тесты падают**

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_tenders_schema.py -q`
Expected: сбор падает на `ImportError: cannot import name 'Offer' from 'models'`.

- [ ] **Step 3: модели**

В `backend/models.py`. Сразу после класса `Contractor` (перед комментарием
«Договор и сметы») добавить `CHECK` канона к нему — заменить

```python
    __table_args__ = (UniqueConstraint("inn", name="uq_contractors_inn"),)
```

на

```python
    __table_args__ = (
        UniqueConstraint("inn", name="uq_contractors_inn"),
        # Канон ИНН — только ASCII-цифры (миграция 0015, спека контура §2.7):
        # `utils.canonicalize_inn` пишет ровно эту форму, CHECK стережёт правку
        # мимо приложения. Длина не проверяется — юрисдикции разные.
        CheckConstraint(CONTRACTOR_INN_CANONICAL, name="ck_contractors_inn_canonical"),
    )
```

и над классом `Contractor` объявить константу (дублируется в миграции
намеренно, parity-тест сравнит):

```python
#: Дублирует миграцию 0015; расхождение ловит test_tenders_schema.py.
CONTRACTOR_INN_CANONICAL = "inn ~ '^[0-9]+$'"
```

Новые модели — новый раздел ПОСЛЕ класса `Contract` и ПЕРЕД `ImportJob`:

```python
# ---------------------------------------------------------------------------
#  Тендерный контур (спека 2026-08-26-tenders-contour-design.md §2.1)
# ---------------------------------------------------------------------------

#: Выражения CHECK продублированы в миграции 0015 намеренно (та же дисциплина,
#: что у 0004–0014); расхождение ловит test_tenders_schema.py.
TENDER_TITLE_NOT_BLANK = "btrim(title) <> ''"
TENDER_NUMBER_NOT_BLANK = "btrim(tender_number) <> ''"
TENDER_ROUND_STAGE_NO_POSITIVE = "stage_no > 0"
ESTIMATE_OWNER_EXACTLY_ONE = "num_nonnulls(contract_id, offer_id, round_id) = 1"
ESTIMATE_AMENDMENT_ONLY_WITH_CONTRACT = "contract_id IS NOT NULL OR amendment_no IS NULL"
PROPOSAL_BASELINE_CONTRACTOR = (
    "(is_baseline = true AND contractor_id IS NULL) "
    "OR (is_baseline = false AND contractor_id IS NOT NULL)"
)
IMPORT_JOB_OWNER_EXACTLY_ONE = "num_nonnulls(contract_id, round_id) = 1"
IMPORT_JOB_AMENDMENT_ONLY_WITH_CONTRACT = "contract_id IS NOT NULL OR amendment_no IS NULL"
IMPORT_JOB_PARSED_PAIR = "(parsed_data IS NULL) = (parser_version IS NULL)"
IMPORT_JOB_ESTIMATES_CREATED_POSITIVE = "estimates_created IS NULL OR estimates_created > 0"


class Tender(Base):
    """Тендер: объект, предмет торга, номер. `rate_class_id` — СНИМОК класса на
    момент торга, по тому же правилу, что `contracts.rate_class_id` (§4):
    переклассификация объекта не меняет прошлое."""
    __tablename__ = "tenders"

    id = Column(BigInteger, primary_key=True)
    object_id = Column(BigInteger, ForeignKey("objects.id"), nullable=False)
    title = Column(Text, nullable=False)
    tender_number = Column(Text, nullable=False)
    rate_class_id = Column(BigInteger, ForeignKey("rate_classes.id"), nullable=False)
    notes = Column(Text, nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    object = relationship("ObjectModel")
    rate_class = relationship("RateClass")
    rounds = relationship(
        "TenderRound", back_populates="tender", cascade="all, delete-orphan",
        order_by="TenderRound.stage_no",
    )
    packages = relationship("OfferPackage", back_populates="tender", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("tender_number", name="uq_tenders_tender_number"),
        CheckConstraint(TENDER_TITLE_NOT_BLANK, name="ck_tenders_title_not_blank"),
        CheckConstraint(TENDER_NUMBER_NOT_BLANK, name="ck_tenders_number_not_blank"),
        Index("ix_tenders_object_id", "object_id"),
        Index("ix_tenders_rate_class_id", "rate_class_id"),
    )


class TenderRound(Base):
    """Раунд (этап) торга. `UNIQUE (id, tender_id)` — цель составного FK из
    `offers`: так раунд и участник ячейки обязаны принадлежать одному тендеру
    (спека §2.1)."""
    __tablename__ = "tender_rounds"

    id = Column(BigInteger, primary_key=True)
    tender_id = Column(BigInteger, ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False)
    stage_no = Column(Integer, nullable=False)
    label = Column(Text, nullable=True)
    held_on = Column(Date, nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    tender = relationship("Tender", back_populates="rounds")
    offers = relationship("Offer", back_populates="round", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("tender_id", "stage_no", name="uq_tender_rounds_tender_stage"),
        UniqueConstraint("id", "tender_id", name="uq_tender_rounds_id_tender"),
        CheckConstraint(TENDER_ROUND_STAGE_NO_POSITIVE, name="ck_tender_rounds_stage_no"),
    )


class OfferPackage(Base):
    """Участник тендера — «пакет» его предложений по всем раундам. Удаляется
    ТОЛЬКО командой (спека §2.11): `offers.package_id` объявлен RESTRICT, чтобы
    история участника не исчезала от случайного DELETE."""
    __tablename__ = "offer_packages"

    id = Column(BigInteger, primary_key=True)
    tender_id = Column(BigInteger, ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False)
    contractor_id = Column(BigInteger, ForeignKey("contractors.id", ondelete="RESTRICT"), nullable=False)
    created_at = _created_at()

    tender = relationship("Tender", back_populates="packages")
    contractor = relationship("Contractor")
    offers = relationship("Offer", back_populates="package")

    __table_args__ = (
        UniqueConstraint("tender_id", "contractor_id", name="uq_offer_packages_tender_contractor"),
        UniqueConstraint("id", "tender_id", name="uq_offer_packages_id_tender"),
        Index("ix_offer_packages_contractor_id", "contractor_id"),
    )


class Offer(Base):
    """Ячейка решётки «раунд × участник». `tender_id` продублирован намеренно:
    два составных FK держат раунд и пакет в одном тендере структурно, а не
    триггером. Все три колонки NOT NULL — иначе MATCH SIMPLE отключил бы
    проверку (спека §2.1)."""
    __tablename__ = "offers"

    id = Column(BigInteger, primary_key=True)
    tender_id = Column(BigInteger, nullable=False)
    round_id = Column(BigInteger, nullable=False)
    package_id = Column(BigInteger, nullable=False)
    created_at = _created_at()

    round = relationship("TenderRound", back_populates="offers", foreign_keys=[round_id, tender_id],
                         overlaps="package,offers")
    package = relationship("OfferPackage", back_populates="offers", foreign_keys=[package_id, tender_id],
                           overlaps="round,offers")

    __table_args__ = (
        ForeignKeyConstraint(
            ["round_id", "tender_id"], ["tender_rounds.id", "tender_rounds.tender_id"],
            ondelete="CASCADE", name="fk_offers_round",
        ),
        ForeignKeyConstraint(
            ["package_id", "tender_id"], ["offer_packages.id", "offer_packages.tender_id"],
            ondelete="RESTRICT", name="fk_offers_package",
        ),
        UniqueConstraint("round_id", "package_id", name="uq_offers_round_package"),
        Index("ix_offers_package_id", "package_id"),
    )
```

`ImportJob` — заменить `contract_id = Column(BigInteger, ForeignKey("contracts.id"), nullable=False)` на

```python
    # Два владельца — договор либо раунд, ровно один (спека контура §2.1).
    contract_id = Column(BigInteger, ForeignKey("contracts.id"), nullable=True)
    round_id = Column(BigInteger, ForeignKey("tender_rounds.id", ondelete="CASCADE"), nullable=True)
```

после `to_review` добавить

```python
    # Три факта об одном файле (спека контура §2.3): XLSX в storage,
    # ТОЧНЫЙ ParseResult.data этого разбора — здесь, проекция под смету — в
    # estimate_raw_data. Пишется сессией A сразу после парсинга независимо от
    # исхода импорта; у jobs до 0015 законно NULL.
    parsed_data = Column(JSONB, nullable=True)
    parser_version = Column(Text, nullable=True)
    # Сколько смет создал успешный job: 1 у договора, N(+1) у раунда. Нужен
    # правилу «текущий job раунда» (спека §2.12); у старых jobs NULL.
    estimates_created = Column(Integer, nullable=True)
```

в relationships добавить `round = relationship("TenderRound")`, а в
`__table_args__` — после `ck_import_jobs_amendment_no`:

```python
        CheckConstraint(IMPORT_JOB_OWNER_EXACTLY_ONE, name="ck_import_jobs_owner"),
        CheckConstraint(IMPORT_JOB_AMENDMENT_ONLY_WITH_CONTRACT, name="ck_import_jobs_amendment_owner"),
        CheckConstraint(IMPORT_JOB_PARSED_PAIR, name="ck_import_jobs_parsed_pair"),
        CheckConstraint(IMPORT_JOB_ESTIMATES_CREATED_POSITIVE, name="ck_import_jobs_estimates_created"),
        Index("ix_import_jobs_round_id", "round_id"),
        # uq_import_jobs_active_round — UNIQUE (round_id) WHERE round_id IS NOT NULL
        # AND status NOT IN (terminal); raw SQL в миграции 0015, как active_pair.
```

и дописать к комментарию про `uq_import_jobs_active_pair`: «с 0015 —
частичный, `WHERE contract_id IS NOT NULL`».

`Estimate` — заменить `contract_id = Column(BigInteger, ForeignKey("contracts.id"), nullable=False)` на

```python
    # Три владельца — договор, предложение раунда, раунд (baseline); ровно один
    # (спека контура §2.1). round_id на смете и ЕСТЬ признак baseline.
    contract_id = Column(BigInteger, ForeignKey("contracts.id"), nullable=True)
    offer_id = Column(BigInteger, ForeignKey("offers.id", ondelete="CASCADE"), nullable=True)
    round_id = Column(BigInteger, ForeignKey("tender_rounds.id", ondelete="CASCADE"), nullable=True)
```

relationships: `offer = relationship("Offer")`, `round = relationship("TenderRound")`;
`__table_args__` — после `ck_estimates_amendment_no`:

```python
        CheckConstraint(ESTIMATE_OWNER_EXACTLY_ONE, name="ck_estimates_owner"),
        CheckConstraint(ESTIMATE_AMENDMENT_ONLY_WITH_CONTRACT, name="ck_estimates_amendment_owner"),
        # uq_estimates_offer / uq_estimates_round — частичные UNIQUE, raw SQL 0015;
        # uq_estimates_contract_amendment с 0015 — WHERE contract_id IS NOT NULL.
```

`Proposal` — `contractor_id` → `nullable=True`, докстроку дополнить «у baseline
подрядчика нет (спека контура §1.2)», в `__table_args__` добавить
`CheckConstraint(PROPOSAL_BASELINE_CONTRACTOR, name="ck_proposals_baseline_contractor")`.

Докстрока `EstimateRawData`: заменить «Полный JSON парсера — источник истины по
содержимому файла (§4)» на «Проекция разобранного JSON под ЭТУ смету — вход
материализации и резолвера статей (спека контура §2.3). Полный результат
разбора файла — `import_jobs.parsed_data`».

- [ ] **Step 4: миграция 0015**

`backend/alembic/versions/2026_08_26_0015-tenders_contour.py`:

```python
"""Тендерный контур: тендеры, раунды, участники, предложения; три владельца сметы,
два владельца задания, аудит разбора, канон ИНН (спека 2026-08-26-tenders-contour-design.md §2.1–§2.2).

**Составные FK `offers → tender_rounds (id, tender_id)` и `→ offer_packages
(id, tender_id)`** держат ячейку в одном тендере структурно. Все три колонки
`offers` NOT NULL — PostgreSQL проверяет составной FK как MATCH SIMPLE, и NULL в
любой колонке отключил бы проверку.

**`RESTRICT` от пакета, `CASCADE` от раунда — осознанная асимметрия.** Пакет —
участник со всей историей; его удаление проходит только командой, которая
явно удаляет offers (спека §2.11).

**Пересечение частичных индексов — условие работоспособности.**
`uq_estimates_contract_amendment` объявлен NULLS NOT DISTINCT; при
`contract_id IS NULL` все сметы предложений схлопнулись бы в одну. Оба прежних
индекса пересоздаются с `WHERE contract_id IS NOT NULL`.

**Миграция данных ИНН — с отказом.** Канон (только ASCII-цифры) вычисляется
для каждого подрядчика; пустой канон либо два подрядчика с одним каноном —
отказ с обоими id, потому что схлопнуть их молча значит слить две компании.
CHECK добавляется ПОСЛЕ обновления.

**`downgrade` отказывает** по трём счётчикам отдельно: tenders, round-owned
import_jobs, estimates без contract_id. Каждый назван — откат обязан сказать,
что именно его держит (то же правило, что у 0011).

Выражения CHECK продублированы константами в models.py; parity —
test_tenders_schema.py.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-26
"""
import re

import sqlalchemy as sa

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

IMPORT_JOB_TERMINAL = "'done', 'error'"

CK_TENDERS_TITLE = "btrim(title) <> ''"
CK_TENDERS_NUMBER = "btrim(tender_number) <> ''"
CK_ROUNDS_STAGE_NO = "stage_no > 0"
CK_ESTIMATES_OWNER = "num_nonnulls(contract_id, offer_id, round_id) = 1"
CK_ESTIMATES_AMENDMENT_OWNER = "contract_id IS NOT NULL OR amendment_no IS NULL"
CK_PROPOSALS_BASELINE = (
    "(is_baseline = true AND contractor_id IS NULL) "
    "OR (is_baseline = false AND contractor_id IS NOT NULL)"
)
CK_JOBS_OWNER = "num_nonnulls(contract_id, round_id) = 1"
CK_JOBS_AMENDMENT_OWNER = "contract_id IS NOT NULL OR amendment_no IS NULL"
CK_JOBS_PARSED_PAIR = "(parsed_data IS NULL) = (parser_version IS NULL)"
CK_JOBS_ESTIMATES_CREATED = "estimates_created IS NULL OR estimates_created > 0"
CK_CONTRACTORS_INN = "inn ~ '^[0-9]+$'"

_NON_DIGIT = re.compile(r"[^0-9]")


def _canonicalize_existing_inns(bind) -> None:
    rows = bind.execute(sa.text("SELECT id, inn FROM contractors ORDER BY id")).all()
    seen: dict[str, int] = {}
    updates: list[tuple[int, str]] = []
    for contractor_id, inn in rows:
        canonical = _NON_DIGIT.sub("", inn or "")
        if not canonical:
            raise RuntimeError(
                f"Миграция 0015 невозможна: у подрядчика id={contractor_id} ИНН не содержит "
                "ни одной цифры. Исправьте карточку и повторите."
            )
        if canonical in seen:
            raise RuntimeError(
                f"Миграция 0015 невозможна: подрядчики id={seen[canonical]} и id={contractor_id} "
                f"после канонизации ИНН совпадают ({canonical}). Слить их — решение человека, "
                "не миграции."
            )
        seen[canonical] = contractor_id
        if canonical != inn:
            updates.append((contractor_id, canonical))
    for contractor_id, canonical in updates:
        bind.execute(
            sa.text("UPDATE contractors SET inn = :inn WHERE id = :id"),
            {"inn": canonical, "id": contractor_id},
        )


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "tenders",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("object_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("tender_number", sa.Text(), nullable=False),
        sa.Column("rate_class_id", sa.BigInteger(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_tenders"),
        sa.ForeignKeyConstraint(["object_id"], ["objects.id"], name="fk_tenders_object_id"),
        sa.ForeignKeyConstraint(["rate_class_id"], ["rate_classes.id"], name="fk_tenders_rate_class_id"),
        sa.UniqueConstraint("tender_number", name="uq_tenders_tender_number"),
        sa.CheckConstraint(CK_TENDERS_TITLE, name="ck_tenders_title_not_blank"),
        sa.CheckConstraint(CK_TENDERS_NUMBER, name="ck_tenders_number_not_blank"),
    )
    op.create_index("ix_tenders_object_id", "tenders", ["object_id"])
    op.create_index("ix_tenders_rate_class_id", "tenders", ["rate_class_id"])

    op.create_table(
        "tender_rounds",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tender_id", sa.BigInteger(), nullable=False),
        sa.Column("stage_no", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("held_on", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_tender_rounds"),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE", name="fk_tender_rounds_tender_id"),
        sa.UniqueConstraint("tender_id", "stage_no", name="uq_tender_rounds_tender_stage"),
        sa.UniqueConstraint("id", "tender_id", name="uq_tender_rounds_id_tender"),
        sa.CheckConstraint(CK_ROUNDS_STAGE_NO, name="ck_tender_rounds_stage_no"),
    )

    op.create_table(
        "offer_packages",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tender_id", sa.BigInteger(), nullable=False),
        sa.Column("contractor_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_offer_packages"),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE", name="fk_offer_packages_tender_id"),
        sa.ForeignKeyConstraint(["contractor_id"], ["contractors.id"], ondelete="RESTRICT", name="fk_offer_packages_contractor_id"),
        sa.UniqueConstraint("tender_id", "contractor_id", name="uq_offer_packages_tender_contractor"),
        sa.UniqueConstraint("id", "tender_id", name="uq_offer_packages_id_tender"),
    )
    op.create_index("ix_offer_packages_contractor_id", "offer_packages", ["contractor_id"])

    op.create_table(
        "offers",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tender_id", sa.BigInteger(), nullable=False),
        sa.Column("round_id", sa.BigInteger(), nullable=False),
        sa.Column("package_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_offers"),
        sa.ForeignKeyConstraint(
            ["round_id", "tender_id"], ["tender_rounds.id", "tender_rounds.tender_id"],
            ondelete="CASCADE", name="fk_offers_round",
        ),
        sa.ForeignKeyConstraint(
            ["package_id", "tender_id"], ["offer_packages.id", "offer_packages.tender_id"],
            ondelete="RESTRICT", name="fk_offers_package",
        ),
        sa.UniqueConstraint("round_id", "package_id", name="uq_offers_round_package"),
    )
    op.create_index("ix_offers_package_id", "offers", ["package_id"])

    # --- estimates: три владельца ---
    op.alter_column("estimates", "contract_id", existing_type=sa.BigInteger(), nullable=True)
    op.add_column("estimates", sa.Column("offer_id", sa.BigInteger(), nullable=True))
    op.add_column("estimates", sa.Column("round_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_estimates_offer_id", "estimates", "offers", ["offer_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("fk_estimates_round_id", "estimates", "tender_rounds", ["round_id"], ["id"], ondelete="CASCADE")
    op.create_check_constraint("ck_estimates_owner", "estimates", CK_ESTIMATES_OWNER)
    op.create_check_constraint("ck_estimates_amendment_owner", "estimates", CK_ESTIMATES_AMENDMENT_OWNER)
    op.execute("DROP INDEX uq_estimates_contract_amendment")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_estimates_contract_amendment
        ON estimates (contract_id, amendment_no) NULLS NOT DISTINCT
        WHERE contract_id IS NOT NULL
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_estimates_offer ON estimates (offer_id) WHERE offer_id IS NOT NULL")
    op.execute("CREATE UNIQUE INDEX uq_estimates_round ON estimates (round_id) WHERE round_id IS NOT NULL")

    # --- proposals: baseline без подрядчика ---
    op.alter_column("proposals", "contractor_id", existing_type=sa.BigInteger(), nullable=True)
    op.create_check_constraint("ck_proposals_baseline_contractor", "proposals", CK_PROPOSALS_BASELINE)

    # --- import_jobs: два владельца, аудит разбора ---
    op.alter_column("import_jobs", "contract_id", existing_type=sa.BigInteger(), nullable=True)
    op.add_column("import_jobs", sa.Column("round_id", sa.BigInteger(), nullable=True))
    op.add_column("import_jobs", sa.Column("parsed_data", sa.dialects.postgresql.JSONB(), nullable=True))
    op.add_column("import_jobs", sa.Column("parser_version", sa.Text(), nullable=True))
    op.add_column("import_jobs", sa.Column("estimates_created", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_import_jobs_round_id", "import_jobs", "tender_rounds", ["round_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_import_jobs_round_id", "import_jobs", ["round_id"])
    op.create_check_constraint("ck_import_jobs_owner", "import_jobs", CK_JOBS_OWNER)
    op.create_check_constraint("ck_import_jobs_amendment_owner", "import_jobs", CK_JOBS_AMENDMENT_OWNER)
    op.create_check_constraint("ck_import_jobs_parsed_pair", "import_jobs", CK_JOBS_PARSED_PAIR)
    op.create_check_constraint("ck_import_jobs_estimates_created", "import_jobs", CK_JOBS_ESTIMATES_CREATED)
    op.execute("DROP INDEX uq_import_jobs_active_pair")
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_import_jobs_active_pair
        ON import_jobs (contract_id, COALESCE(amendment_no, -1))
        WHERE contract_id IS NOT NULL AND status NOT IN ({IMPORT_JOB_TERMINAL})
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_import_jobs_active_round
        ON import_jobs (round_id)
        WHERE round_id IS NOT NULL AND status NOT IN ({IMPORT_JOB_TERMINAL})
        """
    )

    # --- contractors: канон ИНН — сначала данные, потом CHECK ---
    _canonicalize_existing_inns(bind)
    op.create_check_constraint("ck_contractors_inn_canonical", "contractors", CK_CONTRACTORS_INN)


def _downgrade_blockers(bind) -> dict[str, int]:
    """Три счётчика, каждый из которых сам по себе держит откат. Вынесены в
    функцию, чтобы тест проверил каждую ветвь отдельно — в валидной схеме
    дочерние строки без тендера не существуют, и одним живым downgrade все
    три диагностики не увидеть."""
    return {
        "tenders": bind.execute(sa.text("SELECT count(*) FROM tenders")).scalar_one(),
        "round_jobs": bind.execute(
            sa.text("SELECT count(*) FROM import_jobs WHERE round_id IS NOT NULL")
        ).scalar_one(),
        "ownerless_estimates": bind.execute(
            sa.text("SELECT count(*) FROM estimates WHERE contract_id IS NULL")
        ).scalar_one(),
    }


def _downgrade_refusal(blockers: dict[str, int]) -> str | None:
    if not any(blockers.values()):
        return None
    return (
        f"Откат 0015 невозможен: тендеров — {blockers['tenders']}, заданий импорта раундов — "
        f"{blockers['round_jobs']}, смет предложений и baseline — {blockers['ownerless_estimates']}. "
        "Это загруженные файлы и результаты импорта; удалить их — решение человека, а не "
        "миграции. Удалите тендеры через приложение и повторите откат."
    )


def downgrade() -> None:
    bind = op.get_bind()
    refusal = _downgrade_refusal(_downgrade_blockers(bind))
    if refusal is not None:
        raise RuntimeError(refusal)

    op.drop_constraint("ck_contractors_inn_canonical", "contractors", type_="check")

    op.execute("DROP INDEX uq_import_jobs_active_round")
    op.execute("DROP INDEX uq_import_jobs_active_pair")
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_import_jobs_active_pair
        ON import_jobs (contract_id, COALESCE(amendment_no, -1))
        WHERE status NOT IN ({IMPORT_JOB_TERMINAL})
        """
    )
    for name in ("ck_import_jobs_estimates_created", "ck_import_jobs_parsed_pair",
                 "ck_import_jobs_amendment_owner", "ck_import_jobs_owner"):
        op.drop_constraint(name, "import_jobs", type_="check")
    op.drop_index("ix_import_jobs_round_id", table_name="import_jobs")
    op.drop_constraint("fk_import_jobs_round_id", "import_jobs", type_="foreignkey")
    op.drop_column("import_jobs", "estimates_created")
    op.drop_column("import_jobs", "parser_version")
    op.drop_column("import_jobs", "parsed_data")
    op.drop_column("import_jobs", "round_id")
    op.alter_column("import_jobs", "contract_id", existing_type=sa.BigInteger(), nullable=False)

    op.drop_constraint("ck_proposals_baseline_contractor", "proposals", type_="check")
    op.alter_column("proposals", "contractor_id", existing_type=sa.BigInteger(), nullable=False)

    op.execute("DROP INDEX uq_estimates_round")
    op.execute("DROP INDEX uq_estimates_offer")
    op.execute("DROP INDEX uq_estimates_contract_amendment")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_estimates_contract_amendment
        ON estimates (contract_id, amendment_no) NULLS NOT DISTINCT
        """
    )
    op.drop_constraint("ck_estimates_amendment_owner", "estimates", type_="check")
    op.drop_constraint("ck_estimates_owner", "estimates", type_="check")
    op.drop_constraint("fk_estimates_round_id", "estimates", type_="foreignkey")
    op.drop_constraint("fk_estimates_offer_id", "estimates", type_="foreignkey")
    op.drop_column("estimates", "round_id")
    op.drop_column("estimates", "offer_id")
    op.alter_column("estimates", "contract_id", existing_type=sa.BigInteger(), nullable=False)

    op.drop_table("offers")
    op.drop_table("offer_packages")
    op.drop_table("tender_rounds")
    op.drop_table("tenders")
```

`sa.dialects.postgresql.JSONB()` — заменить на `from sqlalchemy.dialects.postgresql import JSONB`
в шапке и `JSONB()` в колонке, если `sa.dialects` не резолвится (в 0002 JSONB
импортирован именно так — повторить его форму).

- [ ] **Step 5: `env.py` — новые raw-SQL индексы вне сравнения `alembic check`**

В `backend/alembic/env.py` дополнить `RAW_SQL_INDEXES`:

```python
    "uq_estimates_offer",               # UNIQUE (offer_id) WHERE offer_id IS NOT NULL — 0015
    "uq_estimates_round",               # UNIQUE (round_id) WHERE round_id IS NOT NULL — 0015
    "uq_import_jobs_active_round",      # UNIQUE (round_id) WHERE ... NOT IN terminal — 0015
```

- [ ] **Step 6: фабрики**

В `backend/tests/factories.py`, после `ContractFactory`:

```python
class TenderFactory(_BaseFactory):
    class Meta:
        model = Tender

    object = factory.SubFactory(ObjectFactory)
    rate_class = factory.SubFactory(RateClassFactory)
    title = "Генподряд на строительство"
    tender_number = factory.Sequence(lambda n: f"Т-{n:04d}")


class TenderRoundFactory(_BaseFactory):
    class Meta:
        model = TenderRound

    tender = factory.SubFactory(TenderFactory)
    stage_no = factory.Sequence(lambda n: n + 1)
    label = None


class OfferPackageFactory(_BaseFactory):
    class Meta:
        model = OfferPackage

    tender = factory.SubFactory(TenderFactory)
    contractor = factory.SubFactory(ContractorFactory)


class OfferFactory(_BaseFactory):
    """Ячейка решётки. `tender_id` выводится из раунда — фабрика не даёт
    собрать ячейку из раунда и пакета разных тендеров случайно; тест на
    скрещивание строит Offer руками."""
    class Meta:
        model = Offer

    round = factory.SubFactory(TenderRoundFactory)
    package = factory.LazyAttribute(
        lambda o: OfferPackageFactory.create(tender=o.round.tender)
    )
    tender_id = factory.LazyAttribute(lambda o: o.round.tender_id)
```

Импорт моделей в шапке `factories.py` дополнить `Offer, OfferPackage, Tender, TenderRound`.

- [ ] **Step 7: применить миграцию к тестовой БД и прогнать `alembic check`**

Run: `cd backend && DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run alembic upgrade head`
Expected: `Running upgrade 0014 -> 0015`.
Run: `cd backend && DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run alembic check`
Expected: `No new upgrade operations detected.` Любой дрейф — расхождение
модели и миграции, чинить до продолжения.

- [ ] **Step 8: тесты схемы зелёные**

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_tenders_schema.py -v`
Expected: PASS все.

- [ ] **Step 9: parity-тест и тесты downgrade/миграции данных**

Дописать в `test_tenders_schema.py`:

```python
import importlib.util
from pathlib import Path

from models import (
    CONTRACTOR_INN_CANONICAL,
    ESTIMATE_AMENDMENT_ONLY_WITH_CONTRACT,
    ESTIMATE_OWNER_EXACTLY_ONE,
    IMPORT_JOB_AMENDMENT_ONLY_WITH_CONTRACT,
    IMPORT_JOB_ESTIMATES_CREATED_POSITIVE,
    IMPORT_JOB_OWNER_EXACTLY_ONE,
    IMPORT_JOB_PARSED_PAIR,
    PROPOSAL_BASELINE_CONTRACTOR,
    TENDER_NUMBER_NOT_BLANK,
    TENDER_ROUND_STAGE_NO_POSITIVE,
    TENDER_TITLE_NOT_BLANK,
)


def _migration_0015():
    path = next(Path(__file__).resolve().parents[2].glob("alembic/versions/*0015-tenders_contour.py"))
    spec = importlib.util.spec_from_file_location("_migration_0015", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestParityWithMigration:
    """`alembic check` выражений CHECK не сравнивает — сравниваем сами."""

    @pytest.mark.parametrize(
        ("model_expr", "migration_name"),
        [
            (TENDER_TITLE_NOT_BLANK, "CK_TENDERS_TITLE"),
            (TENDER_NUMBER_NOT_BLANK, "CK_TENDERS_NUMBER"),
            (TENDER_ROUND_STAGE_NO_POSITIVE, "CK_ROUNDS_STAGE_NO"),
            (ESTIMATE_OWNER_EXACTLY_ONE, "CK_ESTIMATES_OWNER"),
            (ESTIMATE_AMENDMENT_ONLY_WITH_CONTRACT, "CK_ESTIMATES_AMENDMENT_OWNER"),
            (PROPOSAL_BASELINE_CONTRACTOR, "CK_PROPOSALS_BASELINE"),
            (IMPORT_JOB_OWNER_EXACTLY_ONE, "CK_JOBS_OWNER"),
            (IMPORT_JOB_AMENDMENT_ONLY_WITH_CONTRACT, "CK_JOBS_AMENDMENT_OWNER"),
            (IMPORT_JOB_PARSED_PAIR, "CK_JOBS_PARSED_PAIR"),
            (IMPORT_JOB_ESTIMATES_CREATED_POSITIVE, "CK_JOBS_ESTIMATES_CREATED"),
            (CONTRACTOR_INN_CANONICAL, "CK_CONTRACTORS_INN"),
        ],
    )
    def test_check_expressions_match(self, model_expr, migration_name):
        assert model_expr == getattr(_migration_0015(), migration_name)


class TestInnDataMigration:
    """`_canonicalize_existing_inns` — на живом соединении со savepoint."""

    def test_two_contractors_collapsing_to_one_canon_refuse(self, db_session, factories):
        a = factories.ContractorFactory.create(inn="7700000001")
        db_session.flush()
        # Второго с неканоническим ИНН схема после 0015 не пустит — обходим
        # CHECK внутри теста, чтобы воспроизвести дофичевое состояние данных.
        db_session.execute(sa.text("ALTER TABLE contractors DROP CONSTRAINT ck_contractors_inn_canonical"))
        b = factories.ContractorFactory.create(inn="77 0000 0001")
        db_session.flush()
        with pytest.raises(RuntimeError, match=f"id={a.id} и id={b.id}"):
            _migration_0015()._canonicalize_existing_inns(db_session.connection())

    def test_formatted_inn_is_rewritten(self, db_session, factories):
        db_session.execute(sa.text("ALTER TABLE contractors DROP CONSTRAINT ck_contractors_inn_canonical"))
        c = factories.ContractorFactory.create(inn="77-00-000-002")
        db_session.flush()
        _migration_0015()._canonicalize_existing_inns(db_session.connection())
        db_session.expire_all()
        assert db_session.get(type(c), c.id).inn == "7700000002"


class TestDowngradeBlockers:
    """Каждая из трёх диагностик отката — отдельно, на живом соединении."""

    def test_clean_schema_does_not_block(self, db_session):
        m = _migration_0015()
        assert m._downgrade_refusal(m._downgrade_blockers(db_session.connection())) is None

    def test_tender_alone_blocks_and_is_named(self, db_session, factories):
        factories.TenderFactory.create()
        db_session.flush()
        m = _migration_0015()
        blockers = m._downgrade_blockers(db_session.connection())
        assert blockers == {"tenders": 1, "round_jobs": 0, "ownerless_estimates": 0}
        assert "тендеров — 1" in m._downgrade_refusal(blockers)

    def test_round_job_is_counted_separately(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        db_session.add(ImportJob(round_id=rnd.id, filename="f.xlsx", file_key="k-dg", file_sha256="0" * 64,
                                 status=ImportJobStatus.error.value))
        db_session.flush()
        m = _migration_0015()
        blockers = m._downgrade_blockers(db_session.connection())
        assert blockers["round_jobs"] == 1
        assert "заданий импорта раундов — 1" in m._downgrade_refusal(blockers)

    def test_ownerless_estimate_is_counted_separately(self, db_session, factories):
        offer = factories.OfferFactory.create()
        db_session.add(Estimate(offer_id=offer.id))
        db_session.flush()
        m = _migration_0015()
        blockers = m._downgrade_blockers(db_session.connection())
        assert blockers["ownerless_estimates"] == 1
        assert "смет предложений и baseline — 1" in m._downgrade_refusal(blockers)
```

Тесты `TestInnDataMigration` работают внутри транзакции `db_session`, которая
откатывается фикстурой — `DROP CONSTRAINT` в ней не переживает тест.

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_tenders_schema.py -v`
Expected: PASS все.

- [ ] **Step 10: downgrade-защита — прогон вручную на тестовой БД**

Run (из `backend/`, три команды по отдельности):

```
DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run alembic downgrade 0014
```
Expected: успех — таблицы пусты.

```
DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run alembic upgrade head
```
Expected: `0014 -> 0015`.

Три ветви отказа по отдельности доказаны `TestDowngradeBlockers` (шаг 9). Здесь
— **связка целиком** на живой БД: `downgrade()` → `_downgrade_blockers` →
`_downgrade_refusal` → `RuntimeError` ДО первого DDL. Наполнить схему одним
тендером (три команды по отдельности, из `backend/`):

```
DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run python -c "
import os, sqlalchemy as sa
e = sa.create_engine(os.environ['DATABASE_URL'])
with e.begin() as c:
    rc = c.execute(sa.text(\"INSERT INTO rate_classes (title) VALUES ('dg-класс') RETURNING id\")).scalar_one()
    ob = c.execute(sa.text(\"INSERT INTO objects (title, address) VALUES ('dg-объект', '-') RETURNING id\")).scalar_one()
    c.execute(sa.text(\"INSERT INTO tenders (object_id, title, tender_number, rate_class_id) VALUES (:o, 'dg', 'DG-1', :r)\"), {'o': ob, 'r': rc})
print('seeded')
"
```

```
DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run alembic downgrade 0014
```
Expected: `RuntimeError: Откат 0015 невозможен: тендеров — 1, заданий импорта
раундов — 0, смет предложений и baseline — 0. …`; схема осталась на 0015
(`alembic current` → `0015`).

```
DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run python -c "
import os, sqlalchemy as sa
e = sa.create_engine(os.environ['DATABASE_URL'])
with e.begin() as c:
    c.execute(sa.text(\"DELETE FROM tenders WHERE tender_number = 'DG-1'\"))
    c.execute(sa.text(\"DELETE FROM objects WHERE title = 'dg-объект'\"))
    c.execute(sa.text(\"DELETE FROM rate_classes WHERE title = 'dg-класс'\"))
print('cleaned')
"
```

Затем `downgrade 0014` — успех, `upgrade head` — снова `0015`. Если у `objects`
или `rate_classes` есть обязательные колонки, не названные здесь, — дополнить
`INSERT` по их определению в `models.py`, не убирая проверку. Вывод всех
команд — в отчёт исполнителя.

- [ ] **Step 11: весь набор — старое не задето**

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest -n 8 -q`
Expected: PASS. `test_schema_constraints.py::TestImportJobsActiveLock` зелёный
без правок — частичный индекс держит прежний инвариант.

- [ ] **Step 12: Commit**

```bash
git add backend/models.py backend/alembic/versions/2026_08_26_0015-tenders_contour.py \
  backend/alembic/env.py backend/tests/factories.py backend/tests/integration/test_tenders_schema.py
git commit -m "feat(tenders-contour): схема — тендеры, раунды, участники, предложения; три владельца сметы; миграция 0015"
```

---

## Task 3: Владельцы сметы — `import_estimate` получает `EstimateOwner`, а не `Contract`

Спека §2.4. Рефакторинг, **сохраняющий поведение**: договорной путь через
`contract_estimate_owner` даёт тот же результат, и все существующие тесты
остаются зелёными **без правок утверждений** — меняется только форма вызова в
шести местах. Offer- и baseline-владельцы заводятся здесь как типы, но их
поведение внутри `import_estimate` (deviation, допработы, `contractor_id`) —
задача 5.

**Files:**
- Create: `backend/services/import_owners.py`
- Modify: `backend/services/estimate_import.py` (`import_estimate`, `compare_header_with_contract` → `compare_header`, `_replace_existing` — вызов по владельцу)
- Modify: `backend/services/import_pipeline.py:238-258` (сборка владельца)
- Modify (форма вызова): `backend/tests/integration/conftest.py`, `test_estimate_import.py`, `test_category_override_concurrency.py`, `test_review_concurrency.py`, `test_matching.py`
- Test: `backend/tests/integration/test_import_owners.py` (новый, только договорный владелец на этом шаге)

**Interfaces:**
- Consumes: `Tender`, `TenderRound`, `Offer`, `OfferPackage`, `Contractor`, `Contract` (модели).
- Produces:
  - `HeaderTruth(object_title, object_address, contractor_title, contractor_inn, contractor_address, contractor_accreditation)` — все `str | None`;
  - `EstimateOwner(kind: Literal["contract","offer","baseline"], contract_id: int | None, amendment_no: int | None, offer_id: int | None, round_id: int | None, proposal_contractor_id: int | None, is_baseline: bool, truth: HeaderTruth)` — frozen, все поля неизменяемы; метод `estimate_columns() -> dict[str, Any]` отдаёт колонки владельца для `Estimate(...)`; свойство `replace_scope -> tuple[int, int | None] | None` — `(contract_id, amendment_no)` у договора, иначе `None`;
    свойства: `imports_additional_works -> bool` (`kind != "baseline"`), `reads_deviation -> bool` (`kind == "offer"`), `warns_on_unexpected_baseline -> bool` (`kind == "contract"`);
  - `contract_estimate_owner(contract: Contract, amendment_no: int | None) -> EstimateOwner`;
  - `offer_estimate_owner(offer: Offer, tender: Tender, contractor: Contractor) -> EstimateOwner`;
  - `baseline_estimate_owner(tender_round: TenderRound, tender: Tender) -> EstimateOwner`;
  - `import_estimate(db, *, owner: EstimateOwner, data, parser_version, import_job_id, replace, unit_resolver, category_resolver) -> ImportOutcome`;
  - `compare_header(data, truth: HeaderTruth, proposal_data) -> list[str]`.

- [ ] **Step 1: падающий тест — договорный владелец эквивалентен прежнему вызову**

`backend/tests/integration/test_import_owners.py`:

```python
"""`import_estimate` под владельцем (спека контура §2.4).

Договорный владелец обязан дать ТО ЖЕ, что прежний вызов с `contract=`:
проверяется не «тест зелёный», а конкретные факты — владелец сметы, подрядчик
предложения, сверка шапки, замена пары.
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from models import Estimate, Lot, Proposal
from services.category_resolution import CategoryResolver
from services.estimate_import import compare_header, import_estimate
from services.import_owners import HeaderTruth, contract_estimate_owner
from services.unit_resolution import UnitResolver
from tests.payloads import payload_for, position

pytestmark = pytest.mark.integration


def _import(db, owner, data, *, replace=False):
    return import_estimate(
        db,
        owner=owner,
        data=data,
        parser_version="4.0.0",
        import_job_id=None,
        replace=replace,
        unit_resolver=UnitResolver(db),
        category_resolver=CategoryResolver.from_db(db),
    )


class TestContractOwner:
    def test_estimate_belongs_to_the_contract_and_proposal_to_its_contractor(self, db_session, factories):
        contract = factories.ContractFactory.create()
        db_session.flush()
        owner = contract_estimate_owner(contract, None)

        outcome = _import(db_session, owner, payload_for(contract))

        estimate = db_session.get(Estimate, outcome.estimate_id)
        assert (estimate.contract_id, estimate.offer_id, estimate.round_id) == (contract.id, None, None)
        assert estimate.amendment_no is None
        proposal = db_session.execute(
            sa.select(Proposal).join(Lot).where(Lot.estimate_id == estimate.id)
        ).scalar_one()
        assert proposal.contractor_id == contract.contractor_id
        assert proposal.is_baseline is False

    def test_truth_is_the_contract_card(self, factories, db_session):
        contract = factories.ContractFactory.create()
        db_session.flush()
        truth = contract_estimate_owner(contract, None).truth
        assert truth == HeaderTruth(
            object_title=contract.object.title,
            object_address=contract.object.address,
            contractor_title=contract.contractor.title,
            contractor_inn=contract.contractor.inn,
            # Договорная сверка адрес и аккредитацию не проверяет — как сегодня
            # (спека §2.4, ревизия гейта 3).
            contractor_address=None,
            contractor_accreditation=None,
        )

    def test_header_mismatch_names_the_card(self, factories, db_session):
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(contract, tender_object="Другой объект")
        warnings = compare_header(data, contract_estimate_owner(contract, None).truth,
                                  data["lots"]["lot_1"]["proposals"]["contractor_1"])
        assert any("Объект" in w and "Другой объект" in w for w in warnings)

    def test_replace_replaces_the_same_pair(self, db_session, factories):
        contract = factories.ContractFactory.create()
        db_session.flush()
        owner = contract_estimate_owner(contract, None)
        first = _import(db_session, owner, payload_for(contract))
        second = _import(db_session, owner, payload_for(contract, [position(job_title="Другая", unit="м2", unit_cost_total="5")]), replace=True)
        assert second.replaced_estimate_id == first.estimate_id
        assert db_session.get(Estimate, first.estimate_id) is None
```

- [ ] **Step 2: убедиться, что падает**

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_import_owners.py -q`
Expected: сбор падает — `No module named 'services.import_owners'`.

- [ ] **Step 3: модуль владельцев**

`backend/services/import_owners.py`:

```python
"""Владелец сметы при импорте — кто владеет строкой `estimates`, с чем сверять
шапку файла, что писать в предложение (спека контура §2.4).

ОДИН dataclass с тегом `kind` и три конструктора вместо иерархии классов
(план, Р2): `import_estimate` читает поля владельца, а не ветвится по типу.
Спека называет три типа — `ContractEstimateOwner`, `OfferEstimateOwner`,
`BaselineEstimateOwner`; здесь они — три функции, возвращающие `EstimateOwner`
с соответствующим `kind`.

Истина шапки (`HeaderTruth`) — то, с чем сверяется файл, и из файла ничего не
апсертится (§3): у договора — карточка договора; у предложения — карточка
ТЕНДЕРА для объекта и карточка ПОДРЯДЧИКА из пакета; у baseline подрядчика нет.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from models import Contract, Contractor, Offer, Tender, TenderRound

OwnerKind = Literal["contract", "offer", "baseline"]


@dataclass(frozen=True)
class HeaderTruth:
    """Реквизиты, с которыми сверяется шапка XLSX. `None` — сверять нечем."""

    object_title: str | None
    object_address: str | None
    contractor_title: str | None
    contractor_inn: str | None
    contractor_address: str | None
    contractor_accreditation: str | None


@dataclass(frozen=True)
class EstimateOwner:
    kind: OwnerKind
    #: Владелец сметы — ровно одно из трёх не None (CHECK схемы). Хранятся
    #: конкретные id, а не словарь: frozen=True не защитил бы содержимое dict.
    contract_id: int | None
    amendment_no: int | None
    offer_id: int | None
    round_id: int | None
    #: Подрядчик предложения: из карточки договора / из пакета; у baseline None.
    proposal_contractor_id: int | None
    is_baseline: bool
    truth: HeaderTruth

    def estimate_columns(self) -> dict[str, Any]:
        """Колонки владельца для `Estimate(...)`."""
        if self.kind == "contract":
            return {"contract_id": self.contract_id, "amendment_no": self.amendment_no}
        if self.kind == "offer":
            return {"offer_id": self.offer_id}
        return {"round_id": self.round_id}

    @property
    def replace_scope(self) -> tuple[int, int | None] | None:
        """Пара для `_replace_existing`; у раундовых владельцев None — замену
        раунда делает `round_import` ДО цикла (§2.6)."""
        if self.kind == "contract" and self.contract_id is not None:
            return (self.contract_id, self.amendment_no)
        return None

    @property
    def imports_additional_works(self) -> bool:
        """Контур допработ (`decide_owner`, `_import_additional_works`) — не для
        baseline: у базы нет «Сведений», а `additional_info` вырезан
        постобработкой (спека §1.4, §2.9)."""
        return self.kind != "baseline"

    @property
    def reads_deviation(self) -> bool:
        """`deviation_from_baseline_cost` читается только у offer-позиций (§2.10)."""
        return self.kind == "offer"

    @property
    def warns_on_unexpected_baseline(self) -> bool:
        """«В сметах ГП baseline нет» — предупреждение договорного пути; у
        раунда baseline ожидаем."""
        return self.kind == "contract"


def contract_estimate_owner(contract: Contract, amendment_no: int | None) -> EstimateOwner:
    """Договор: истина — карточка договора, но у подрядчика ТОЛЬКО название и
    ИНН (спека §2.4, ревизия гейта 3): договорная сверка адрес и аккредитацию не
    проверяла и не начинает — иначе изменилось бы поведение договорного импорта."""
    return EstimateOwner(
        kind="contract",
        contract_id=contract.id, amendment_no=amendment_no, offer_id=None, round_id=None,
        proposal_contractor_id=contract.contractor_id,
        is_baseline=False,
        truth=HeaderTruth(
            object_title=contract.object.title,
            object_address=contract.object.address,
            contractor_title=contract.contractor.title,
            contractor_inn=contract.contractor.inn,
            contractor_address=None,
            contractor_accreditation=None,
        ),
    )


def offer_estimate_owner(offer: Offer, tender: Tender, contractor: Contractor) -> EstimateOwner:
    """Предложение: объект из карточки тендера, подрядчик — ВСЕ четыре поля из
    пакета (спека §2.4)."""
    return EstimateOwner(
        kind="offer",
        contract_id=None, amendment_no=None, offer_id=offer.id, round_id=None,
        proposal_contractor_id=contractor.id,
        is_baseline=False,
        truth=HeaderTruth(
            object_title=tender.object.title,
            object_address=tender.object.address,
            contractor_title=contractor.title,
            contractor_inn=contractor.inn,
            contractor_address=contractor.address,
            contractor_accreditation=contractor.accreditation,
        ),
    )


def baseline_estimate_owner(tender_round: TenderRound, tender: Tender) -> EstimateOwner:
    return EstimateOwner(
        kind="baseline",
        contract_id=None, amendment_no=None, offer_id=None, round_id=tender_round.id,
        proposal_contractor_id=None,
        is_baseline=True,
        truth=HeaderTruth(
            object_title=tender.object.title,
            object_address=tender.object.address,
            contractor_title=None,
            contractor_inn=None,
            contractor_address=None,
            contractor_accreditation=None,
        ),
    )
```

- [ ] **Step 4: `estimate_import.py` — сигнатура и сверка шапки**

Заменить `compare_header_with_contract(data, contract, proposal_data)` на:

```python
def compare_header(
    data: dict[str, Any], truth: HeaderTruth, proposal_data: dict[str, Any] | None
) -> list[str]:
    """Сверяет реквизиты из шапки XLSX с истиной владельца (спека контура §2.4).

    Источник истины — карточка (§3): из файла ничего не апсертится. Расхождения
    не блокируют импорт, они уходят в `import_jobs.warnings`. Поле истины
    `None` означает «сверять нечем» — так у baseline нет подрядчика.
    """
    warnings: list[str] = []

    def mismatch(label: str, in_file: Any, in_card: Any) -> None:
        warnings.append(
            f"{label} в файле («{in_file}») не совпадает с карточкой («{in_card}»). "
            "Импортировано по карточке — она источник истины."
        )

    file_object = _text(data.get(JSON_KEY_TENDER_OBJECT))
    if file_object and truth.object_title is not None and _loose(file_object) != _loose(truth.object_title):
        mismatch("Объект", file_object, truth.object_title)

    file_address = _text(data.get(JSON_KEY_TENDER_ADDRESS))
    if file_address and truth.object_address is not None and _loose(file_address) != _loose(truth.object_address):
        mismatch("Адрес объекта", file_address, truth.object_address)

    if proposal_data and truth.contractor_title is not None:
        file_contractor = _text(proposal_data.get(JSON_KEY_CONTRACTOR_TITLE))
        if file_contractor and _loose(file_contractor) != _loose(truth.contractor_title):
            mismatch("Подрядчик", file_contractor, truth.contractor_title)

        file_inn = canonicalize_inn(proposal_data.get(JSON_KEY_CONTRACTOR_INN))
        card_inn = canonicalize_inn(truth.contractor_inn)
        if file_inn and card_inn and file_inn != card_inn:
            mismatch("ИНН подрядчика", file_inn, truth.contractor_inn)

        # Адрес и аккредитация — только там, где истина их несёт: у предложения
        # раунда (все четыре поля из пакета), у договора они None и не сверяются.
        file_address = _text(proposal_data.get(JSON_KEY_CONTRACTOR_ADDRESS))
        if file_address and truth.contractor_address is not None and _loose(file_address) != _loose(truth.contractor_address):
            mismatch("Адрес подрядчика", file_address, truth.contractor_address)

        file_accreditation = _text(proposal_data.get(JSON_KEY_CONTRACTOR_ACCREDITATION))
        if file_accreditation and truth.contractor_accreditation is not None and _loose(file_accreditation) != _loose(truth.contractor_accreditation):
            mismatch("Аккредитация подрядчика", file_accreditation, truth.contractor_accreditation)

    return warnings
```

Импорты `JSON_KEY_CONTRACTOR_ADDRESS`, `JSON_KEY_CONTRACTOR_ACCREDITATION` из
`parser.constants`.

Текст предупреждения меняется с «карточкой договора» на «карточкой»: у раунда
карточка — тендер и участник. Существующий тест `test_estimate_import.py:551`,
если проверяет фрагмент «карточкой договора», правится **на «карточкой»** — это
и есть единственное разрешённое изменение утверждения в этой задаче, и оно не
ослабляет проверку.

`import_estimate` — сигнатура и тело:

```python
def import_estimate(
    db: Session,
    *,
    owner: EstimateOwner,
    data: dict[str, Any],
    parser_version: str,
    import_job_id: int | None,
    replace: bool,
    unit_resolver: UnitResolver,
    category_resolver: CategoryResolver,
) -> ImportOutcome:
```

в докстроке — `owner: владелец сметы (спека контура §2.4): куда пишется
estimates, с чем сверяется шапка, чей подрядчик у предложения`; параметр
`contract` из Args убрать. В теле:

```python
    if replace and owner.replace_scope is None:
        raise ValueError("replace допустим только для договорного владельца; замена раунда делается до цикла")
    ...
    replaced_id = (
        _replace_existing(db, owner.replace_scope[0], owner.replace_scope[1], replace, warnings)
        if owner.replace_scope is not None
        else None
    )

    estimate = Estimate(
        **owner.estimate_columns(),
        title=_text(data.get(JSON_KEY_TENDER_TITLE)),
        data_prepared_on_date=_prepared_date(data, warnings),
        import_job_id=import_job_id,
    )
```

в цикле по лотам:

```python
        if owner.warns_on_unexpected_baseline:
            _warn_on_unexpected_baseline(lot_content, lot_key, warnings)

        proposal_data = extract_single_proposal(lot_content)
        warnings.extend(compare_header(data, owner.truth, proposal_data))
        ...
        proposal = Proposal(
            lot_id=lot.id,
            # Подрядчик — из карточки владельца, не из файла (§3); у baseline None.
            contractor_id=owner.proposal_contractor_id,
            is_baseline=owner.is_baseline,
            ...
```

**Коллизия имени, которую надо снять здесь же:** в теле `import_estimate` уже
есть `owner = decide_owner(data)` (строка ~429) и
`is_owner=lot_key == owner.owner_lot_key` в вызове `_import_additional_works`.
Новый параметр `owner: EstimateOwner` затенил бы его. Переименовать локальную
переменную в `works_owner`:

```python
    works_owner = decide_owner(data)
    warnings.extend(works_owner.warnings)
    ...
            is_owner=lot_key == works_owner.owner_lot_key,
```

Остальное тело (позиции, допработы, deviation) в этой задаче **не меняется** —
это задача 5. Импорт `from services.import_owners import EstimateOwner, HeaderTruth`.

- [ ] **Step 5: пайплайн собирает владельца**

В `import_pipeline.run_import_job`, вместо

```python
            outcome = import_estimate(
                db,
                contract=contract,
                amendment_no=context.amendment_no,
```

писать

```python
            outcome = import_estimate(
                db,
                owner=contract_estimate_owner(contract, context.amendment_no),
```

Импорт `from services.import_owners import contract_estimate_owner`.

- [ ] **Step 6: шесть мест вызова — механическая замена формы**

В каждом из `tests/integration/conftest.py` (2 вызова),
`test_estimate_import.py` (`run_import`), `test_category_override_concurrency.py`
(4), `test_review_concurrency.py` (1), `test_matching.py` (1) заменить

```python
        contract=<x>,
        amendment_no=<y>,
```

на

```python
        owner=contract_estimate_owner(<x>, <y>),
```

с импортом `from services.import_owners import contract_estimate_owner`. Ни
одно утверждение не трогать. В `test_estimate_import.py` импорт
`compare_header_with_contract` → `compare_header`, вызов на строке ~551 получает
`contract_estimate_owner(contract, None).truth` вторым аргументом.

- [ ] **Step 7: новый тест зелёный, старые — без правок утверждений**

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_import_owners.py tests/integration/test_estimate_import.py tests/integration/test_import_pipeline.py tests/integration/test_matching.py tests/integration/test_review_concurrency.py tests/integration/test_category_override_concurrency.py tests/integration/test_import_fixture_e2e.py -q`
Expected: PASS все. `git diff` по тестовым файлам показывает ТОЛЬКО правки
формы вызова и импорта (плюс одно слово «договора» в одном фрагменте).

- [ ] **Step 8: ruff, юнит**

Run: `cd backend && uv run ruff check .` — clean. `uv run pytest tests/unit -q` — PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/services/import_owners.py backend/services/estimate_import.py \
  backend/services/import_pipeline.py backend/tests
git commit -m "refactor(tenders-contour): import_estimate принимает владельца сметы; договорный путь — без изменения поведения"
```

---

## Task 4: Пайплайн — `parsed_data` сессией A, `round_id` в контексте, `estimates_created`

Спека §2.3, §2.12, Global Constraint 4 и 12. Договорной job начинает писать
три новых поля; ветка раунда заводится как заглушка, которую задача 6 наполнит.

**Files:**
- Modify: `backend/services/import_pipeline.py` (`JobContext`, `load_job_context`, `StatusWriter`, `run_import_job`, `finalize_done`)
- Test: `backend/tests/integration/test_import_pipeline.py` (новый класс)

**Interfaces:**
- Produces: `JobContext(job_id, contract_id: int | None, amendment_no, round_id: int | None, file_key, filename)`;
  `StatusWriter.record_parse(data: dict, parser_version: str) -> None` — пишет
  `parsed_data`, `parser_version` отдельной короткой транзакцией сессии A;
  `finalize_done(db, job_id, *, counters, warnings, now, estimates_created: int)`.

- [ ] **Step 1: падающие тесты**

В `test_import_pipeline.py` новый класс:

```python
class TestParseAudit:
    """Три факта об одном файле (спека контура §2.3): точный ParseResult
    остаётся у job, что бы ни случилось с импортом."""

    def test_done_job_keeps_parsed_data_and_version(self, job_env):
        payload = payload_for(job_env.contract)
        job = job_env.run(payload, parse=fake_parse(payload, version="4.0.0"))
        assert job.status == ImportJobStatus.done.value
        assert job.parsed_data == payload
        assert job.parser_version == "4.0.0"
        assert job.estimates_created == 1

    def test_failed_import_still_keeps_parsed_data(self, job_env):
        """Инъекция отказа ПОСЛЕ парсинга: домен откатился, аудит разбора — нет."""
        payload = payload_for(job_env.contract)
        # Два предложения в лоте — договорный путь отвергает такой файл в
        # _validate_payload, то есть после успешного парсинга.
        payload["lots"]["lot_1"]["proposals"]["contractor_2"] = dict(
            payload["lots"]["lot_1"]["proposals"]["contractor_1"]
        )
        job = job_env.run(payload, parse=fake_parse(payload, version="4.0.0"))
        assert job.status == ImportJobStatus.error.value
        assert job.parsed_data == payload
        assert job.parser_version == "4.0.0"
        assert job.estimates_created is None
        assert job_env.estimates() == []

    def test_parse_failure_leaves_both_null(self, job_env):
        def broken(_handle):
            raise EstimateParseError("не смета")

        job = job_env.run(None, parse=broken)
        assert job.status == ImportJobStatus.error.value
        assert job.parsed_data is None and job.parser_version is None
```

- [ ] **Step 2: убедиться, что падают**

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_import_pipeline.py::TestParseAudit -v`
Expected: первые два FAIL на `parsed_data is None`; третий PASS.

- [ ] **Step 3: `JobContext` и `load_job_context`**

```python
@dataclass(frozen=True)
class JobContext:
    """Минимум данных о задании, нужный пайплайну (читается сессией A один раз).

    Владелец — договор ЛИБО раунд (спека контура §2.1); ровно одно из
    `contract_id`/`round_id` не None, это стережёт CHECK схемы.
    """

    job_id: int
    contract_id: int | None
    amendment_no: int | None
    round_id: int | None
    file_key: str
    filename: str
```

В `load_job_context` добавить `ImportJob.round_id` в `select` между
`amendment_no` и `file_key`.

- [ ] **Step 4: `StatusWriter.record_parse`**

```python
    def record_parse(self, data: dict, parser_version: str) -> None:
        """Точный ParseResult этого разбора — сессией A, сразу после парсинга,
        независимо от исхода импорта (спека контура §2.3): failed-job тоже
        показывает, какой разбор привёл к отказу."""
        self._run({"parsed_data": data, "parser_version": parser_version})
```

- [ ] **Step 5: `finalize_done` получает `estimates_created`**

```python
def finalize_done(
    db: Session, job_id: int, *, counters: MatchCounters, warnings: list[str],
    now: datetime, estimates_created: int,
) -> None:
    ...
    values: dict = {
        "status": ImportJobStatus.done.value,
        "error_text": None,
        "finished_at": now,
        "estimates_created": estimates_created,
        **counters.as_dict(),
    }
```

Докстроку дополнить: «`estimates_created` — сколько смет создал этот job:
1 у договора, N(+1) у раунда; нужен правилу текущего job раунда (§2.12)».

- [ ] **Step 6: `run_import_job` — запись аудита и ветка по владельцу**

После `parse_result = parse(handle)` и `status.add_warnings(...)`:

```python
        status.record_parse(parse_result.data, parse_result.parser_version)
```

Блок сессии B:

```python
        with session_factory() as db, db.begin():
            if context.contract_id is not None:
                contract = db.get(Contract, context.contract_id)
                if contract is None:
                    raise EstimateImportError(
                        f"Договор {context.contract_id} не найден — импортировать смету не к чему."
                    )
                owner = contract_estimate_owner(contract, context.amendment_no)
                resolver = UnitResolver(db)
                category_resolver = CategoryResolver.from_db(db)
                outcome = import_estimate(
                    db, owner=owner, data=parse_result.data,
                    parser_version=parse_result.parser_version,
                    import_job_id=job_id, replace=replace,
                    unit_resolver=resolver, category_resolver=category_resolver,
                )
                positions_to_match = outcome.positions_to_match
                domain_warnings = outcome.warnings
                estimates_created = 1
                log_estimate_ids = [outcome.estimate_id]
            else:
                # Раунд — задача 6 плана; до неё round-job создать негде.
                raise EstimateImportError("Импорт раунда ещё не подключён.")
            deadline.check("импорт")

            status.set_status(ImportJobStatus.matching)
            match = match_positions(db, positions_to_match)
            deadline.check("матчинг")

            finalize_done(
                db, job_id, counters=match.counters,
                warnings=domain_warnings + match.warnings,
                now=utcnow_aware(), estimates_created=estimates_created,
            )

        log.info(
            "Импорт задания %d завершён: estimate_ids=%s, счётчики=%s",
            job_id, log_estimate_ids, match.counters.as_dict(),
        )
```

- [ ] **Step 7: тесты зелёные**

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_import_pipeline.py tests/integration/test_estimates_api.py tests/integration/test_maintenance.py -q`
Expected: PASS все, старые без правок утверждений.

- [ ] **Step 8: ruff, Commit**

Run: `cd backend && uv run ruff check .` — clean.

```bash
git add backend/services/import_pipeline.py backend/tests/integration/test_import_pipeline.py
git commit -m "feat(tenders-contour): пайплайн хранит точный ParseResult у job и число созданных смет"
```

---

## Task 5: `import_estimate` под offer- и baseline-владельцами

Спека §2.9, §2.10, §1.4. Три поведения, которых у договорного пути нет:
`deviation_from_baseline_cost` читается у offer-позиций; baseline не запускает
контур допработ; предложение baseline — `is_baseline=True`, `contractor_id=NULL`.
Проверяется прямым вызовом `import_estimate` с проекцией одного предложения —
разбиение файла на проекции придёт задачей 6.

**Files:**
- Modify: `backend/services/estimate_import.py` (`import_estimate` — гейт допработ; `_import_positions` — параметр `reads_deviation`)
- Modify: `backend/tests/payloads.py` (`baseline_proposal_block`, `round_payload`)
- Test: `backend/tests/integration/test_import_owners.py` (два новых класса)

**Interfaces:**
- Consumes: `offer_estimate_owner`, `baseline_estimate_owner` (задача 3); `OfferFactory`, `TenderRoundFactory` (задача 2).
- Produces:
  - `_import_positions(..., reads_deviation: bool)` — при `True` пишет
    `deviation_from_baseline_cost=_money(raw_position.get(JSON_KEY_DEVIATION_FROM_CALCULATED_COST), ...)`, иначе `None`;
  - `payloads.baseline_proposal_block(positions, *, total: str = "1200.00") -> dict` — блок «Расчетная стоимость» в форме, которую даёт `postprocess` для ВАЛИДНОЙ базы: `title="Расчетная стоимость"`, БЕЗ ключа `additional_info`, summary с ненулевым итогом;
  - `payloads.round_payload(participants: list[dict], *, baseline: dict | None = None, lots: int = 1, **header) -> dict` — JSON раунда: `participants` — список `proposal(...)`-словарей, каждый попадает в каждый лот под ключом `contractor_N`; `baseline` — блок из `baseline_proposal_block` либо `None` → заглушка `BASELINE_MISSING_TITLE`; `lots` — число лотов `lot_1..lot_N`.

- [ ] **Step 1: строители payload**

В `backend/tests/payloads.py`, после `proposal(...)`:

```python
TABLE_PARSE_BASELINE_TITLE = "Расчетная стоимость"


def baseline_proposal_block(
    positions: list[dict[str, Any]], *, total: str = "1200.00"
) -> dict[str, Any]:
    """Блок «Расчетная стоимость» ПОСЛЕ постобработки ВАЛИДНОЙ базы (спека контура
    §1.3, §2.9): заголовок точный, `additional_info` вырезан `postprocess`-ом,
    итог ненулевой — иначе `_is_baseline_valid` заменил бы блок заглушкой."""
    block = proposal(positions, title=TABLE_PARSE_BASELINE_TITLE, inn=None,
                     summary={
                         JSON_KEY_TOTAL_COST_INCLUDING_VAT: summary_line("ИТОГО, руб. с учетом НДС", total),
                     })
    block.pop(JSON_KEY_CONTRACTOR_ADDITIONAL_INFO)
    return block


def round_payload(
    participants: list[dict[str, Any]],
    *,
    baseline: dict[str, Any] | None = None,
    lots: int = 1,
    **header: Any,
) -> dict[str, Any]:
    """JSON сводной таблицы раунда в форме `ParseResult.data` после
    `normalize_lots_json_structure`: N предложений под `contractor_N` в каждом
    лоте, baseline — валидный блок либо заглушка."""
    lots_dict = {}
    for n in range(1, lots + 1):
        lots_dict[f"lot_{n}"] = {
            JSON_KEY_LOT_TITLE: f"Лот №{n} - Тестовый",
            JSON_KEY_PROPOSALS: {
                f"contractor_{i}": copy.deepcopy(p) for i, p in enumerate(participants, start=1)
            },
            JSON_KEY_BASELINE_PROPOSAL: copy.deepcopy(baseline)
            if baseline is not None
            else {JSON_KEY_CONTRACTOR_TITLE: BASELINE_MISSING_TITLE},
        }
    header.setdefault("tender_title", "Сводная таблица раунда")
    return estimate_payload(lots=lots_dict, **header)
```

Импорт `copy` в шапке `payloads.py`.

- [ ] **Step 2: падающие тесты**

В `test_import_owners.py`:

```python
from models import PositionItem
from parser.constants import (
    JSON_KEY_CONTRACTOR_ACCREDITATION,
    JSON_KEY_CONTRACTOR_ADDRESS,
    JSON_KEY_DEVIATION_FROM_CALCULATED_COST,
)
from services.import_owners import baseline_estimate_owner, offer_estimate_owner
from tests.payloads import baseline_proposal_block, estimate_payload, position, proposal


def _positions_of(db, estimate_id):
    return db.execute(
        sa.select(PositionItem).join(Proposal).join(Lot).where(Lot.estimate_id == estimate_id)
    ).scalars().all()


class TestOfferOwner:
    def test_estimate_belongs_to_offer_and_proposal_to_package_contractor(self, db_session, factories):
        offer = factories.OfferFactory.create()
        db_session.flush()
        owner = offer_estimate_owner(offer, offer.round.tender, offer.package.contractor)
        data = estimate_payload(
            [position(job_title="Работа", unit="м2", unit_cost_total="10", total_cost_total="10")],
            title=offer.package.contractor.title, inn=offer.package.contractor.inn,
            tender_object=offer.round.tender.object.title,
        )

        outcome = _import(db_session, owner, data)

        estimate = db_session.get(Estimate, outcome.estimate_id)
        assert (estimate.contract_id, estimate.offer_id, estimate.round_id) == (None, offer.id, None)
        proposal_row = db_session.execute(sa.select(Proposal).join(Lot).where(Lot.estimate_id == estimate.id)).scalar_one()
        assert proposal_row.contractor_id == offer.package.contractor_id
        assert proposal_row.is_baseline is False

    def test_deviation_is_read_from_offer_positions(self, db_session, factories):
        offer = factories.OfferFactory.create()
        db_session.flush()
        owner = offer_estimate_owner(offer, offer.round.tender, offer.package.contractor)
        pos = position(job_title="Работа", unit="м2", unit_cost_total="10", total_cost_total="10")
        pos[JSON_KEY_DEVIATION_FROM_CALCULATED_COST] = "-0.05"

        outcome = _import(db_session, owner, estimate_payload([pos]))

        [item] = _positions_of(db_session, outcome.estimate_id)
        assert item.deviation_from_baseline_cost == Decimal("-0.05")

    def test_missing_deviation_key_stays_null(self, db_session, factories):
        offer = factories.OfferFactory.create()
        db_session.flush()
        owner = offer_estimate_owner(offer, offer.round.tender, offer.package.contractor)
        outcome = _import(db_session, owner, estimate_payload(
            [position(job_title="Работа", unit="м2", unit_cost_total="10", total_cost_total="10")]
        ))
        [item] = _positions_of(db_session, outcome.estimate_id)
        assert item.deviation_from_baseline_cost is None

    def test_replace_is_refused_for_round_owners(self, db_session, factories):
        offer = factories.OfferFactory.create()
        db_session.flush()
        owner = offer_estimate_owner(offer, offer.round.tender, offer.package.contractor)
        with pytest.raises(ValueError, match="замена раунда"):
            _import(db_session, owner, estimate_payload(), replace=True)

    @pytest.mark.parametrize(
        ("key", "label"),
        [
            (JSON_KEY_CONTRACTOR_ADDRESS, "Адрес подрядчика"),
            (JSON_KEY_CONTRACTOR_ACCREDITATION, "Аккредитация подрядчика"),
        ],
    )
    def test_offer_truth_checks_address_and_accreditation(self, db_session, factories, key, label):
        """Все четыре поля подрядчика (спека §2.4). У договора эти два поля в
        истину не входят — см. test_truth_is_the_contract_card."""
        offer = factories.OfferFactory.create()
        db_session.flush()
        owner = offer_estimate_owner(offer, offer.round.tender, offer.package.contractor)
        data = estimate_payload(title=offer.package.contractor.title, inn=offer.package.contractor.inn)
        data["lots"]["lot_1"]["proposals"]["contractor_1"][key] = "СОВСЕМ ДРУГОЕ"

        outcome = _import(db_session, owner, data)

        assert any(label in w and "СОВСЕМ ДРУГОЕ" in w for w in outcome.warnings)

    def test_contract_truth_ignores_address_and_accreditation(self, db_session, factories):
        """Контроль: тот же вход у договора предупреждения НЕ даёт — иначе
        DoD 13 нарушен новыми warnings у смет договора."""
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(contract)
        data["lots"]["lot_1"]["proposals"]["contractor_1"][JSON_KEY_CONTRACTOR_ADDRESS] = "СОВСЕМ ДРУГОЕ"

        outcome = _import(db_session, contract_estimate_owner(contract, None), data)

        assert not any("Адрес подрядчика" in w for w in outcome.warnings)


class TestBaselineOwner:
    def _baseline_data(self):
        pos = position(job_title="Работа", unit="м2", unit_cost_total="10", total_cost_total="10")
        pos[JSON_KEY_DEVIATION_FROM_CALCULATED_COST] = "0.10"  # странная раскладка — у базы это NULL
        block = baseline_proposal_block([pos])
        return estimate_payload(lots={
            "lot_1": {
                "lot_title": "Лот №1",
                "proposals": {"contractor_1": block},
                "baseline_proposal": {"title": "Расчетная стоимость отсутствует"},
            }
        })

    def test_estimate_belongs_to_round_and_proposal_has_no_contractor(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        outcome = _import(db_session, baseline_estimate_owner(rnd, rnd.tender), self._baseline_data())

        estimate = db_session.get(Estimate, outcome.estimate_id)
        assert (estimate.contract_id, estimate.offer_id, estimate.round_id) == (None, None, rnd.id)
        proposal_row = db_session.execute(sa.select(Proposal).join(Lot).where(Lot.estimate_id == estimate.id)).scalar_one()
        assert proposal_row.contractor_id is None
        assert proposal_row.is_baseline is True

    def test_baseline_never_writes_deviation(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        outcome = _import(db_session, baseline_estimate_owner(rnd, rnd.tender), self._baseline_data())
        [item] = _positions_of(db_session, outcome.estimate_id)
        assert item.deviation_from_baseline_cost is None

    def test_baseline_skips_the_additional_works_contour(self, db_session, factories, monkeypatch):
        """Контур допработ выключен целиком (спека §2.9): шпион на обоих звеньях.
        Без гейта `decide_owner` упал бы KeyError на вырезанном additional_info."""
        import services.estimate_import as module

        calls: list[str] = []
        monkeypatch.setattr(module, "decide_owner", lambda data: calls.append("decide_owner") or None)
        real_works = module._import_additional_works
        monkeypatch.setattr(module, "_import_additional_works",
                            lambda *a, **kw: calls.append("_import_additional_works") or real_works(*a, **kw))
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()

        _import(db_session, baseline_estimate_owner(rnd, rnd.tender), self._baseline_data())

        assert calls == []

    def test_contract_owner_still_runs_the_contour(self, db_session, factories, monkeypatch):
        """Контроль наблюдаемости: тот же шпион у договора ЛОВИТ вызовы — значит
        пустой список выше означает «не вызвано», а не «нечем смотреть»."""
        import services.estimate_import as module

        calls: list[str] = []
        real_decide = module.decide_owner
        monkeypatch.setattr(module, "decide_owner", lambda data: calls.append("decide_owner") or real_decide(data))
        contract = factories.ContractFactory.create()
        db_session.flush()

        _import(db_session, contract_estimate_owner(contract, None), payload_for(contract))

        assert calls == ["decide_owner"]
```

Импорт `from decimal import Decimal` в шапке файла.

- [ ] **Step 3: убедиться, что падают по нужной причине**

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_import_owners.py -k "Offer or Baseline" -v`
Expected: `test_deviation_is_read...` FAIL (`None != Decimal('-0.05')`);
`test_baseline_skips...` FAIL (`KeyError` из `decide_owner` либо `calls != []`);
`test_replace_is_refused` — PASS уже (задача 3); остальные — PASS или FAIL, но
не ошибкой сбора.

- [ ] **Step 4: `_import_positions` читает deviation по флагу**

Добавить параметр `reads_deviation: bool` (keyword-only, после `lot_key`) и в
конструкторе `PositionItem` заменить

```python
            # В сметах ГП baseline нет, поле остаётся NULL (§4).
            deviation_from_baseline_cost=None,
```

на

```python
            # Только у offer-позиций (спека контура §2.10): postprocess оставил
            # ключ там, где база лота валидна, и вычистил там, где нет. У договора
            # и у baseline — принудительно NULL, даже если ключ пришёл.
            deviation_from_baseline_cost=_money(
                raw_position.get(JSON_KEY_DEVIATION_FROM_CALCULATED_COST), value_problems, where
            )
            if reads_deviation
            else None,
```

Импорт `JSON_KEY_DEVIATION_FROM_CALCULATED_COST` из `parser.constants`.

- [ ] **Step 5: гейт контура допработ в `import_estimate`**

Предпасс владельца:

```python
    works_owner = decide_owner(data) if owner.imports_additional_works else None
    if works_owner is not None:
        warnings.extend(works_owner.warnings)
```

Вызовы в цикле:

```python
        lot_positions, lot_to_match, lot_priced = _import_positions(
            db, proposal_id=proposal.id, positions=positions, resolution=resolution,
            unit_resolver=unit_resolver, value_problems=value_problems, warnings=warnings,
            long_titles=long_titles, lot_key=str(lot_key),
            reads_deviation=owner.reads_deviation,
        )
        ...
        if works_owner is not None:
            _import_additional_works(
                db, proposal_id=proposal.id, proposal_data=proposal_data, positions=positions,
                resolution=resolution, is_owner=lot_key == works_owner.owner_lot_key,
                lot_key=str(lot_key), warnings=warnings,
            )
```

`_reject_stale_1_1_0_shape(positions, resolution, proposal_data)` остаётся для
всех владельцев без изменений; если на baseline-проекции он поднимет `KeyError`
на отсутствующем ключе — заменить в нём индексирование этого ключа на `.get()`
и записать в отчёт, какой ключ.

- [ ] **Step 6: зелёное; договорной путь не изменился**

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_import_owners.py tests/integration/test_estimate_import.py tests/integration/test_import_fixture_e2e.py tests/unit/test_additional_works.py -q`
Expected: PASS все.

- [ ] **Step 7: ruff, Commit**

```bash
git add backend/services/estimate_import.py backend/tests/payloads.py backend/tests/integration/test_import_owners.py
git commit -m "feat(tenders-contour): import_estimate под offer- и baseline-владельцами — deviation, без контура допработ, предложение без подрядчика"
```

---

## Task 6: `round_import` — разбиение по ИНН, get-or-create, замена раунда, цикл, один матчинг

Спека §2.5–§2.9. Оркестратор над `import_estimate`. Самая большая задача
плана; каждый шаг — одна функция с тестом.

**Files:**
- Create: `backend/services/round_import.py`
- Modify: `backend/services/import_pipeline.py` (ветка раунда вместо заглушки)
- Modify: `backend/tests/unit/parser/sheet_builders.py` (`add_contractor_block` — параметры `inn`, `address`)
- Test: `backend/tests/integration/test_round_import.py`
- Test: `backend/tests/integration/test_import_pipeline.py` (класс `TestRoundJob`)

**Interfaces:**
- Consumes: `EstimateOwner`-конструкторы (3), `import_estimate` (5), `round_payload`/`baseline_proposal_block` (5), модели и фабрики (2), `canonicalize_inn` (1).
- Produces:
  - `RoundProjection(inn: str, title: str | None, address: str | None, accreditation: str | None, data: dict)`;
  - `BaselineProjection(data: dict, lots_with_baseline: tuple[str, ...], lots_without: tuple[str, ...])`;
  - `split_round_payload(data: dict) -> tuple[list[RoundProjection], BaselineProjection | None]` — raises `EstimateImportError`;
  - `replace_round_estimates(db, round_id: int, replace: bool, warnings: list[str]) -> list[int]` — под уже взятыми локами; raises `EstimateImportError` при сметах и `replace=False`;
  - `get_or_create_contractor(db, *, inn: str, title, address, accreditation, warnings) -> Contractor`;
  - `get_or_create_package(db, *, tender_id: int, contractor_id: int) -> OfferPackage`;
  - `get_or_create_offer(db, *, tender_id: int, round_id: int, package_id: int) -> Offer`;
  - `RoundImportOutcome(estimate_ids: list[int], positions_to_match: list[PositionToMatch], warnings: list[str], estimates_created: int)`;
  - `import_round(db, *, tender_round: TenderRound, data, parser_version, import_job_id, replace, unit_resolver, category_resolver) -> RoundImportOutcome`.

- [ ] **Step 1: падающие тесты разбиения (чистая функция)**

`backend/tests/integration/test_round_import.py`:

```python
"""Импорт сводной таблицы раунда (спека контура §2.5–§2.9): разбиение по
каноническому ИНН, атомарность, замена уровнем раунда, baseline по лотам,
один матчинг.

Payload — `round_payload`: форма ParseResult.data ПОСЛЕ постобработки, та же,
что отдаёт парсер на реальной сводной таблице.
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from models import Contractor, Estimate, Lot, Offer, OfferPackage, PositionItem, Proposal
from parser import parse_worksheet
from services.category_resolution import CategoryResolver
from services.estimate_import import EstimateImportError
from services.round_import import (
    import_round,
    split_round_payload,
)
from services.unit_resolution import UnitResolver
from tests.payloads import baseline_proposal_block, position, proposal, round_payload
from tests.unit.parser.sheet_builders import KEYS_GP_11, KEYS_TENDER_11, add_contractor_block, gp_sheet

pytestmark = pytest.mark.integration

P1 = lambda: proposal([position(job_title="Работа", unit="м2", unit_cost_total="10", total_cost_total="10")],
                      title="ООО Первый", inn="7700000001")
P2 = lambda: proposal([position(job_title="Работа", unit="м2", unit_cost_total="12", total_cost_total="12")],
                      title="ООО Второй", inn="77 0000 0002")


class TestSplitRoundPayload:
    def test_one_projection_per_canonical_inn_across_lots(self):
        data = round_payload([P1(), P2()], lots=2)
        projections, baseline = split_round_payload(data)
        assert [p.inn for p in projections] == ["7700000001", "7700000002"]
        assert baseline is None
        for p in projections:
            assert set(p.data["lots"]) == {"lot_1", "lot_2"}
            for lot in p.data["lots"].values():
                assert list(lot["proposals"]) == ["contractor_1"]

    def test_projection_keeps_its_own_block_only(self):
        data = round_payload([P1(), P2()])
        [first, second], _ = split_round_payload(data)
        assert first.data["lots"]["lot_1"]["proposals"]["contractor_1"]["title"] == "ООО Первый"
        assert second.data["lots"]["lot_1"]["proposals"]["contractor_1"]["title"] == "ООО Второй"

    def test_empty_inn_is_a_refusal(self):
        data = round_payload([proposal([position()], title="Без ИНН", inn="")])
        with pytest.raises(EstimateImportError, match="ИНН"):
            split_round_payload(data)

    def test_duplicate_inn_within_a_lot_is_a_refusal(self):
        data = round_payload([P1(), proposal([position()], title="Дубль", inn="7700000001")])
        with pytest.raises(EstimateImportError, match="дважды"):
            split_round_payload(data)

    def test_mismatched_inn_sets_across_lots_is_a_refusal(self):
        data = round_payload([P1(), P2()], lots=2)
        del data["lots"]["lot_2"]["proposals"]["contractor_2"]
        with pytest.raises(EstimateImportError, match="наборы участников"):
            split_round_payload(data)

    def test_valid_baseline_in_all_lots(self):
        block = baseline_proposal_block([position(job_title="База", unit="м2", unit_cost_total="9", total_cost_total="9")])
        _, baseline = split_round_payload(round_payload([P1()], baseline=block, lots=2))
        assert baseline is not None
        assert baseline.lots_with_baseline == ("lot_1", "lot_2")
        assert baseline.lots_without == ()
        for lot in baseline.data["lots"].values():
            assert lot["proposals"]["contractor_1"]["title"] == "Расчетная стоимость"

    def test_baseline_valid_in_one_lot_of_two(self):
        block = baseline_proposal_block([position(job_title="База", unit="м2", unit_cost_total="9", total_cost_total="9")])
        data = round_payload([P1()], baseline=block, lots=2)
        data["lots"]["lot_2"]["baseline_proposal"] = {"title": "Расчетная стоимость отсутствует"}
        _, baseline = split_round_payload(data)
        assert baseline.lots_with_baseline == ("lot_1",)
        assert baseline.lots_without == ("lot_2",)
        assert set(baseline.data["lots"]) == {"lot_1"}

    def test_real_parser_output_without_inn_is_refused(self):
        """Реальный парсер на синтетическом листе без строки ИНН — отказ, не тихий
        участник с пустым ключом (Global Constraint 7)."""
        ws = gp_sheet(KEYS_TENDER_11)
        add_contractor_block(ws, col_start=22, columns=KEYS_GP_11, title='ООО "Второй"')
        data = parse_worksheet(ws).data
        with pytest.raises(EstimateImportError, match="ИНН"):
            split_round_payload(data)


def synthetic_round_sheet(*, baseline_total: float | None = 90.0):
    """Сводная таблица раунда как ЛИСТ (спека §6): два участника с ИНН в
    строке под заголовком, блок «Расчетная стоимость» KEYS_8, одна позиция и
    блок итогов. Геометрия — та же, что у `_tender_sheet_with_baseline` в
    test_estimate.py: участник 1 — J..T (10..20), участник 2 — V..AF (22..32),
    база — AH..AO (34..41); итог базы — total_cost.total, индекс 7 → колонка 41.
    Настоящий `parse_worksheet` даёт форму ParseResult.data, которую режет
    `split_round_payload`: расхождение формы поймает CI, а не стенд."""
    ws = gp_sheet(KEYS_TENDER_11, inn="7700000001", address="г. Тест, ул. Первая, 1")
    add_contractor_block(ws, col_start=22, columns=KEYS_TENDER_11, title='ООО "Второй"',
                         inn="77 0000 0002", address="г. Тест, ул. Вторая, 2")
    add_contractor_block(ws, col_start=34, columns=KEYS_8, title="Расчетная стоимость", vat_suffix=None)
    ws.cell(row=12, column=1, value=2)
    ws.cell(row=12, column=2, value="1")
    ws.cell(row=12, column=4, value="Работа")
    for col_start in (10, 22):
        ws.cell(row=12, column=col_start + 8, value=100.0)   # total_cost.total участника
        ws.cell(row=12, column=col_start + 10, value=-0.05)  # % от р/с
    ws.cell(row=12, column=34 + 7, value=90.0)               # total_cost.total базы
    ws.merge_cells(start_row=14, start_column=1, end_row=14, end_column=5)
    ws.cell(row=14, column=1, value="ИТОГО, руб. с учетом НДС")
    for col_start in (10, 22):
        ws.cell(row=14, column=col_start + 8, value=100.0)
    if baseline_total is not None:
        ws.cell(row=14, column=34 + 7, value=baseline_total)
    return ws


class TestRealParserPath:
    """Положительный путь через НАСТОЯЩИЙ парсер (спека §6): синтетический XLSX
    → parse_worksheet → split → import_round → 3 сметы."""

    def test_two_participants_and_baseline_from_a_real_sheet(self, db_session, factories):
        data = parse_worksheet(synthetic_round_sheet()).data

        projections, baseline = split_round_payload(data)
        assert [p.inn for p in projections] == ["7700000001", "7700000002"]
        assert baseline is not None and baseline.lots_with_baseline == ("lot_1",)

        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        outcome = _run_round(db_session, rnd, data)
        assert outcome.estimates_created == 3
        rows = db_session.execute(
            sa.select(Proposal.is_baseline, PositionItem.deviation_from_baseline_cost)
            .join(PositionItem, PositionItem.proposal_id == Proposal.id)
        ).all()
        assert sorted(str(d) for b, d in rows if not b) == ["-0.05", "-0.05"]
        assert all(d is None for b, d in rows if b)

    def test_empty_baseline_on_a_real_sheet_gives_two_estimates(self, db_session, factories):
        data = parse_worksheet(synthetic_round_sheet(baseline_total=None)).data
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        outcome = _run_round(db_session, rnd, data)
        assert outcome.estimates_created == 2
        assert any("не заполнена" in w for w in outcome.warnings)
```

`_run_round` объявлен ниже (шаг 7) — Python разрешает имена при вызове, порядок
объявлений в файле значения не имеет. Импорт `KEYS_8` в шапке уже есть.

**Строитель листов учится писать ИНН.** В `backend/tests/unit/parser/sheet_builders.py`
`add_contractor_block` получает два параметра после `vat_suffix`:

```python
    inn: str | None = None,
    address: str | None = None,
```

и после записи заголовка подрядчика (`ws.cell(row=contractor_row, column=col_start, value=title)`):

```python
    # Реквизиты блока — строки под заголовком, как в реальных файлах и как их
    # читает get_proposals (row_start+1 — ИНН, +2 — адрес). Аккредитация
    # (+3) намеренно не пишется: при contractor_row=6 и header_row=9 она легла
    # бы в строку шапки.
    if inn is not None:
        ws.cell(row=contractor_row + 1, column=col_start, value=inn)
    if address is not None:
        ws.cell(row=contractor_row + 2, column=col_start, value=address)
```

`gp_sheet` пробрасывает их через `**block_kwargs` без правок. Существующие
тесты парсера от этого не меняются — параметры по умолчанию `None`.

- [ ] **Step 2: убедиться, что падают**

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_round_import.py -q`
Expected: сбор падает — `No module named 'services.round_import'`.

- [ ] **Step 3: `round_import.py` — разбиение**

```python
"""Импорт сводной таблицы раунда: N offer-смет и опциональная baseline из одного
файла, атомарно (спека контура §2.5).

Оркестратор над `import_estimate`, а не второй конвейер (рамка §3.3, план Р4):
файл разбирается один раз, JSON режется на проекции — по одному предложению на
лот у каждого участника и baseline из валидных лотов, — и каждая проекция идёт
тем же путём, что смета договора. Ниже сметы ничего не меняется.

Идентичность участника — КАНОНИЧЕСКИЙ ИНН через все лоты (§2.7): `contractor_N`
в JSON — локальный индекс постобработки, не участник. Любое нарушение состава
— отказ, не предупреждение: частичное участие как правило никто не принимал.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from models import (
    Contractor,
    Estimate,
    EstimateCategoryOverride,
    Lot,
    Offer,
    OfferPackage,
    PositionItem,
    Proposal,
    Tender,
    TenderRound,
)
from parser.constants import (
    JSON_KEY_BASELINE_PROPOSAL,
    JSON_KEY_CONTRACTOR_ACCREDITATION,
    JSON_KEY_CONTRACTOR_ADDRESS,
    JSON_KEY_CONTRACTOR_INN,
    JSON_KEY_CONTRACTOR_TITLE,
    JSON_KEY_LOTS,
    JSON_KEY_PROPOSALS,
)
from parser.postprocess import BASELINE_MISSING_TITLE
from services.category_resolution import CategoryResolver
from services.estimate_import import (
    EstimateImportError,
    PositionToMatch,
    _text,
    import_estimate,
)
from services.import_owners import baseline_estimate_owner, offer_estimate_owner
from services.unit_resolution import UnitResolver
from utils import canonicalize_inn


@dataclass(frozen=True)
class RoundProjection:
    """JSON раунда, сведённый к ОДНОМУ участнику: в каждом лоте ровно его блок."""

    inn: str
    title: str | None
    address: str | None
    accreditation: str | None
    data: dict[str, Any]


@dataclass(frozen=True)
class BaselineProjection:
    """JSON раунда, сведённый к «Расчетной стоимости»: только лоты с валидной базой."""

    data: dict[str, Any]
    lots_with_baseline: tuple[str, ...]
    lots_without: tuple[str, ...]


@dataclass
class RoundImportOutcome:
    estimate_ids: list[int] = field(default_factory=list)
    positions_to_match: list[PositionToMatch] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    estimates_created: int = 0


def _baseline_is_valid(lot: dict[str, Any]) -> bool:
    """Валидность решил postprocess, по лоту (спека §1.3; план Р5): заглушка —
    невалидна, полный блок — валиден. Второй раз `_is_baseline_valid` не зовём."""
    block = lot.get(JSON_KEY_BASELINE_PROPOSAL) or {}
    return _text(block.get(JSON_KEY_CONTRACTOR_TITLE)) != BASELINE_MISSING_TITLE


def _lot_shell(lot: dict[str, Any], proposal_block: dict[str, Any]) -> dict[str, Any]:
    shell = {k: v for k, v in lot.items() if k not in (JSON_KEY_PROPOSALS, JSON_KEY_BASELINE_PROPOSAL)}
    shell[JSON_KEY_PROPOSALS] = {"contractor_1": copy.deepcopy(proposal_block)}
    shell[JSON_KEY_BASELINE_PROPOSAL] = {JSON_KEY_CONTRACTOR_TITLE: BASELINE_MISSING_TITLE}
    return shell


def split_round_payload(data: dict[str, Any]) -> tuple[list[RoundProjection], BaselineProjection | None]:
    """Режет JSON раунда на проекции участников и baseline (спека §2.7, §2.9).

    Raises:
        EstimateImportError: пустой ИНН у блока; один ИНН дважды в лоте;
            наборы ИНН в лотах не совпадают.
    """
    lots: dict[str, dict[str, Any]] = data.get(JSON_KEY_LOTS) or {}
    if not lots:
        raise EstimateImportError("В разобранном файле нет ни одного лота — импортировать нечего.")

    per_lot: dict[str, dict[str, dict[str, Any]]] = {}  # lot_key -> inn -> block
    for lot_key, lot in lots.items():
        seen: dict[str, dict[str, Any]] = {}
        for block in (lot.get(JSON_KEY_PROPOSALS) or {}).values():
            inn = canonicalize_inn(block.get(JSON_KEY_CONTRACTOR_INN))
            title = _text(block.get(JSON_KEY_CONTRACTOR_TITLE))
            if not inn:
                raise EstimateImportError(
                    f"В лоте «{lot_key}» у блока подрядчика «{title}» нет ИНН — участника "
                    "раунда не по чему опознать. Файл раунда обязан нести ИНН в каждом блоке."
                )
            if inn in seen:
                raise EstimateImportError(
                    f"В лоте «{lot_key}» ИНН {inn} встречается дважды («{_text(seen[inn].get(JSON_KEY_CONTRACTOR_TITLE))}» "
                    f"и «{title}»). Один участник — один блок."
                )
            seen[inn] = block
        per_lot[lot_key] = seen

    lot_keys = list(per_lot)
    reference = set(per_lot[lot_keys[0]])
    for lot_key in lot_keys[1:]:
        if set(per_lot[lot_key]) != reference:
            raise EstimateImportError(
                f"Наборы участников в лотах различаются: «{lot_keys[0]}» — {sorted(reference)}, "
                f"«{lot_key}» — {sorted(per_lot[lot_key])}. Частичное участие в раунде не "
                "поддерживается — файл раунда должен нести одних и тех же участников во всех лотах."
            )

    projections: list[RoundProjection] = []
    for inn in sorted(reference, key=lambda i: list(per_lot[lot_keys[0]]).index(i)):
        first_block = per_lot[lot_keys[0]][inn]
        projected = copy.deepcopy(data)
        projected[JSON_KEY_LOTS] = {
            lot_key: _lot_shell(lots[lot_key], per_lot[lot_key][inn]) for lot_key in lot_keys
        }
        projections.append(RoundProjection(
            inn=inn,
            title=_text(first_block.get(JSON_KEY_CONTRACTOR_TITLE)),
            address=_text(first_block.get(JSON_KEY_CONTRACTOR_ADDRESS)),
            accreditation=_text(first_block.get(JSON_KEY_CONTRACTOR_ACCREDITATION)),
            data=projected,
        ))

    with_baseline = tuple(k for k in lot_keys if _baseline_is_valid(lots[k]))
    without = tuple(k for k in lot_keys if k not in with_baseline)
    baseline: BaselineProjection | None = None
    if with_baseline:
        projected = copy.deepcopy(data)
        projected[JSON_KEY_LOTS] = {
            k: _lot_shell(lots[k], lots[k][JSON_KEY_BASELINE_PROPOSAL]) for k in with_baseline
        }
        baseline = BaselineProjection(data=projected, lots_with_baseline=with_baseline, lots_without=without)
    return projections, baseline
```

- [ ] **Step 4: тесты разбиения зелёные**

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_round_import.py::TestSplitRoundPayload -v`
Expected: PASS 8.

- [ ] **Step 5: падающие тесты get-or-create и замены**

Дописать в `test_round_import.py`:

```python
from services.round_import import (
    get_or_create_contractor,
    get_or_create_offer,
    get_or_create_package,
    replace_round_estimates,
)


class TestGetOrCreate:
    def test_new_inn_creates_contractor_and_warns(self, db_session, factories):
        warnings: list[str] = []
        c = get_or_create_contractor(db_session, inn="7700000009", title="ООО Новый",
                                     address=None, accreditation=None, warnings=warnings)
        assert db_session.get(Contractor, c.id).inn == "7700000009"
        assert any("заведён" in w and "7700000009" in w for w in warnings)

    def test_known_inn_is_reused_and_card_untouched(self, db_session, factories):
        existing = factories.ContractorFactory.create(inn="7700000001", title="ООО Карточка")
        db_session.flush()
        warnings: list[str] = []
        c = get_or_create_contractor(db_session, inn="7700000001", title="ООО Из файла",
                                     address=None, accreditation=None, warnings=warnings)
        assert c.id == existing.id
        assert db_session.get(Contractor, c.id).title == "ООО Карточка"
        # Расхождение имени — забота compare_header, не get-or-create: здесь тихо.
        assert warnings == []

    def test_name_mismatch_is_reported_exactly_once_per_lot(self, db_session, factories):
        """Одно расхождение — одно предупреждение (из compare_header), а не два."""
        factories.ContractorFactory.create(inn="7700000001", title="ООО Карточка")
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        outcome = _run_round(db_session, rnd, round_payload([P1()]))  # в файле — «ООО Первый»
        mismatches = [w for w in outcome.warnings if "Подрядчик" in w and "ООО Первый" in w and "ООО Карточка" in w]
        assert len(mismatches) == 1

    def test_package_and_offer_are_idempotent(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        contractor = factories.ContractorFactory.create()
        db_session.flush()
        p1 = get_or_create_package(db_session, tender_id=rnd.tender_id, contractor_id=contractor.id)
        p2 = get_or_create_package(db_session, tender_id=rnd.tender_id, contractor_id=contractor.id)
        assert p1.id == p2.id
        o1 = get_or_create_offer(db_session, tender_id=rnd.tender_id, round_id=rnd.id, package_id=p1.id)
        o2 = get_or_create_offer(db_session, tender_id=rnd.tender_id, round_id=rnd.id, package_id=p1.id)
        assert o1.id == o2.id
        assert db_session.execute(sa.select(sa.func.count()).select_from(Offer)).scalar_one() == 1


class TestReplaceRoundEstimates:
    def test_no_estimates_is_a_noop(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        assert replace_round_estimates(db_session, rnd.id, replace=False, warnings=[]) == []

    def test_existing_estimates_without_replace_refuse(self, db_session, factories):
        offer = factories.OfferFactory.create()
        db_session.add(Estimate(offer_id=offer.id))
        db_session.flush()
        with pytest.raises(EstimateImportError, match="replace"):
            replace_round_estimates(db_session, offer.round_id, replace=False, warnings=[])

    def test_replace_removes_all_round_estimates_but_keeps_offers(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        offer_a = factories.OfferFactory.create(round=rnd)
        offer_b = factories.OfferFactory.create(round=rnd)
        db_session.add_all([Estimate(offer_id=offer_a.id), Estimate(offer_id=offer_b.id), Estimate(round_id=rnd.id)])
        db_session.flush()

        removed = replace_round_estimates(db_session, rnd.id, replace=True, warnings=[])

        assert len(removed) == 3
        assert db_session.execute(sa.select(sa.func.count()).select_from(Estimate)).scalar_one() == 0
        assert db_session.execute(sa.select(sa.func.count()).select_from(Offer)).scalar_one() == 2
```

- [ ] **Step 6: реализация get-or-create и замены**

```python
def get_or_create_contractor(
    db: Session, *, inn: str, title: str | None, address: str | None,
    accreditation: str | None, warnings: list[str],
) -> Contractor:
    """Участник из файла: новый ИНН заводит карточку, известный — берётся как
    есть (спека §2.8). Карточка из XLSX НЕ обновляется (§3): расхождение имени —
    предупреждение. INSERT … ON CONFLICT + SELECT — без IntegrityError в
    транзакции: два раунда могут заводить одного подрядчика одновременно."""
    inserted = db.execute(
        pg_insert(Contractor)
        .values(inn=inn, title=title or f"Подрядчик ИНН {inn}", address=address or "", accreditation=accreditation or "")
        .on_conflict_do_nothing(index_elements=["inn"])
        .returning(Contractor.id)
    ).scalar_one_or_none()
    contractor = db.execute(sa.select(Contractor).where(Contractor.inn == inn)).scalar_one()
    if inserted is not None:
        warnings.append(
            f"Участник «{contractor.title}» (ИНН {inn}) заведён в справочник подрядчиков из файла раунда."
        )
    # Расхождения СУЩЕСТВУЮЩЕЙ карточки с файлом (имя, адрес, аккредитация)
    # здесь не проверяются: это делает `compare_header` по истине владельца —
    # один раз и в одном месте, иначе одно расхождение давало бы два предупреждения.
    return contractor


def get_or_create_package(db: Session, *, tender_id: int, contractor_id: int) -> OfferPackage:
    db.execute(
        pg_insert(OfferPackage)
        .values(tender_id=tender_id, contractor_id=contractor_id)
        .on_conflict_do_nothing(index_elements=["tender_id", "contractor_id"])
    )
    return db.execute(
        sa.select(OfferPackage).where(OfferPackage.tender_id == tender_id, OfferPackage.contractor_id == contractor_id)
    ).scalar_one()


def get_or_create_offer(db: Session, *, tender_id: int, round_id: int, package_id: int) -> Offer:
    db.execute(
        pg_insert(Offer)
        .values(tender_id=tender_id, round_id=round_id, package_id=package_id)
        .on_conflict_do_nothing(index_elements=["round_id", "package_id"])
    )
    return db.execute(
        sa.select(Offer).where(Offer.round_id == round_id, Offer.package_id == package_id)
    ).scalar_one()


def _round_estimate_ids(db: Session, round_id: int) -> list[int]:
    offer_ids = sa.select(Offer.id).where(Offer.round_id == round_id)
    return list(db.execute(
        sa.select(Estimate.id).where(sa.or_(Estimate.round_id == round_id, Estimate.offer_id.in_(offer_ids)))
        .order_by(Estimate.id)
    ).scalars())


def replace_round_estimates(db: Session, round_id: int, replace: bool, warnings: list[str]) -> list[int]:
    """Замена уровнем раунда, ДО цикла по участникам (спека §2.6).

    Вызывается под локами `tender FOR KEY SHARE`, `round FOR UPDATE`, взятыми
    вызывающим. Удаляются СМЕТЫ — offer- и round-owned; `Offer` и пакеты остаются:
    участник без сметы текущей загрузки — законное состояние решётки.
    Предупреждение о снесённых ручных решениях — одно на весь набор.
    """
    ids = _round_estimate_ids(db, round_id)
    if not ids:
        return []
    if not replace:
        raise EstimateImportError(
            f"Раунд уже загружен (смет: {len(ids)}); для замены повторите запрос с replace=true. "
            "Замена раунда — целиком, всех участников и расчётной стоимости разом."
        )
    lost = db.execute(
        sa.select(sa.func.count())
        .select_from(EstimateCategoryOverride)
        .join(PositionItem, PositionItem.id == EstimateCategoryOverride.position_item_id)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id.in_(ids))
    ).scalar_one()
    if lost:
        warnings.append(f"Заменой раунда снесён ручной разнос статей; решений потеряно: {lost}.")
    db.execute(sa.delete(Estimate).where(Estimate.id.in_(ids)))
    warnings.append(f"Заменён раунд: удалено смет предыдущей загрузки — {len(ids)}.")
    return ids
```

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_round_import.py -q`
Expected: PASS все.

- [ ] **Step 7: падающие тесты `import_round`**

```python
def _run_round(db, rnd, data, *, replace=False):
    return import_round(
        db, tender_round=rnd, data=data, parser_version="4.0.0", import_job_id=None,
        replace=replace, unit_resolver=UnitResolver(db), category_resolver=CategoryResolver.from_db(db),
    )


def _estimates_of_round(db, rnd):
    offer_ids = sa.select(Offer.id).where(Offer.round_id == rnd.id)
    return db.execute(
        sa.select(Estimate).where(sa.or_(Estimate.round_id == rnd.id, Estimate.offer_id.in_(offer_ids)))
    ).scalars().all()


class TestImportRound:
    def test_two_participants_and_baseline_give_three_estimates(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        block = baseline_proposal_block([position(job_title="База", unit="м2", unit_cost_total="9", total_cost_total="9")])

        outcome = _run_round(db_session, rnd, round_payload([P1(), P2()], baseline=block))

        assert outcome.estimates_created == 3
        estimates = _estimates_of_round(db_session, rnd)
        assert len(estimates) == 3
        assert sum(1 for e in estimates if e.round_id == rnd.id) == 1
        assert db_session.execute(sa.select(sa.func.count()).select_from(OfferPackage)).scalar_one() == 2
        inns = set(db_session.execute(sa.select(Contractor.inn)).scalars())
        assert {"7700000001", "7700000002"} <= inns

    def test_without_baseline_two_estimates_and_a_warning(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        outcome = _run_round(db_session, rnd, round_payload([P1(), P2()]))
        assert outcome.estimates_created == 2
        assert any("не заполнена" in w for w in outcome.warnings)

    def test_partial_baseline_names_the_lot_without_it(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        block = baseline_proposal_block([position(job_title="База", unit="м2", unit_cost_total="9", total_cost_total="9")])
        data = round_payload([P1()], baseline=block, lots=2)
        data["lots"]["lot_2"]["baseline_proposal"] = {"title": "Расчетная стоимость отсутствует"}

        outcome = _run_round(db_session, rnd, data)

        baseline = next(e for e in _estimates_of_round(db_session, rnd) if e.round_id == rnd.id)
        lot_keys = set(db_session.execute(sa.select(Lot.lot_key).where(Lot.estimate_id == baseline.id)).scalars())
        assert lot_keys == {"lot_1"}
        assert any("lot_2" in w for w in outcome.warnings)

    def test_positions_to_match_are_pooled_across_all_estimates(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        block = baseline_proposal_block([position(job_title="База", unit="м2", unit_cost_total="9", total_cost_total="9")])
        outcome = _run_round(db_session, rnd, round_payload([P1(), P2()], baseline=block))
        # по одной расценённой позиции у двух участников и у базы
        assert len(outcome.positions_to_match) == 3

    def test_second_upload_without_replace_refuses_before_writing(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        _run_round(db_session, rnd, round_payload([P1()]))
        before = {e.id for e in _estimates_of_round(db_session, rnd)}
        with pytest.raises(EstimateImportError, match="replace"):
            _run_round(db_session, rnd, round_payload([P1(), P2()]))
        assert {e.id for e in _estimates_of_round(db_session, rnd)} == before

    def test_replace_drops_a_participant_missing_from_the_new_file_but_keeps_his_offer(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        _run_round(db_session, rnd, round_payload([P1(), P2()]))

        outcome = _run_round(db_session, rnd, round_payload([P1()]), replace=True)

        assert outcome.estimates_created == 1
        offers = db_session.execute(sa.select(Offer).where(Offer.round_id == rnd.id)).scalars().all()
        assert len(offers) == 2  # ячейка второго осталась, сметы у неё нет
        estimates = _estimates_of_round(db_session, rnd)
        assert len(estimates) == 1
        assert any("удалено смет предыдущей загрузки — 2" in w for w in outcome.warnings)

    def test_deviation_lands_on_participants_when_baseline_is_valid(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        p = P1()
        p["contractor_items"]["positions"]["1"]["deviation_from_baseline_cost"] = "-0.05"
        block = baseline_proposal_block([position(job_title="База", unit="м2", unit_cost_total="9", total_cost_total="9")])

        _run_round(db_session, rnd, round_payload([p], baseline=block))

        rows = db_session.execute(
            sa.select(Proposal.is_baseline, PositionItem.deviation_from_baseline_cost)
            .join(PositionItem, PositionItem.proposal_id == Proposal.id)
        ).all()
        by_kind = {is_baseline: dev for is_baseline, dev in rows}
        assert str(by_kind[False]) == "-0.05"
        assert by_kind[True] is None

    def test_failure_on_second_participant_rolls_back_the_first(self, db_session, factories, monkeypatch):
        """Атомарность раунда: инъекция отказа на втором вызове import_estimate."""
        import services.round_import as module

        real = module.import_estimate
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise EstimateImportError("инъекция отказа")
            return real(*args, **kwargs)

        monkeypatch.setattr(module, "import_estimate", flaky)
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()

        with pytest.raises(EstimateImportError, match="инъекция"):
            with db_session.begin_nested():
                _run_round(db_session, rnd, round_payload([P1(), P2()]))

        assert _estimates_of_round(db_session, rnd) == []
```

- [ ] **Step 8: реализация `import_round`**

```python
def import_round(
    db: Session,
    *,
    tender_round: TenderRound,
    data: dict[str, Any],
    parser_version: str,
    import_job_id: int | None,
    replace: bool,
    unit_resolver: UnitResolver,
    category_resolver: CategoryResolver,
) -> RoundImportOutcome:
    """Один файл раунда → N offer-смет + опциональная baseline, в транзакции вызывающего.

    Порядок (спека §2.5): локи по иерархии tender → round (§2.11) → замена
    уровнем раунда ДО цикла (§2.6) → разбиение (§2.7) → на каждую проекцию
    get-or-create contractor → package → offer → import_estimate → baseline
    (§2.9). Матчинг здесь НЕ вызывается: позиции всех смет возвращаются
    объединённым списком, и пайплайн матчит их один раз (§2.5 п.5).
    """
    outcome = RoundImportOutcome()

    tender = db.execute(
        sa.select(Tender).where(Tender.id == tender_round.tender_id).with_for_update(key_share=True)
    ).scalar_one()
    db.execute(
        sa.select(TenderRound.id).where(TenderRound.id == tender_round.id).with_for_update()
    ).scalar_one()

    replace_round_estimates(db, tender_round.id, replace, outcome.warnings)

    projections, baseline = split_round_payload(data)

    for projection in projections:
        contractor = get_or_create_contractor(
            db, inn=projection.inn, title=projection.title, address=projection.address,
            accreditation=projection.accreditation, warnings=outcome.warnings,
        )
        package = get_or_create_package(db, tender_id=tender.id, contractor_id=contractor.id)
        offer = get_or_create_offer(db, tender_id=tender.id, round_id=tender_round.id, package_id=package.id)
        part = import_estimate(
            db, owner=offer_estimate_owner(offer, tender, contractor), data=projection.data,
            parser_version=parser_version, import_job_id=import_job_id, replace=False,
            unit_resolver=unit_resolver, category_resolver=category_resolver,
        )
        outcome.estimate_ids.append(part.estimate_id)
        outcome.positions_to_match.extend(part.positions_to_match)
        outcome.warnings.extend(part.warnings)

    if baseline is None:
        outcome.warnings.append(
            "Расчётная стоимость в файле раунда не заполнена ни в одном лоте — baseline-смета не создана."
        )
    else:
        if baseline.lots_without:
            outcome.warnings.append(
                "Расчётная стоимость заполнена не во всех лотах; без базы: "
                f"{', '.join(baseline.lots_without)}. Baseline-смета создана только по лотам с базой."
            )
        part = import_estimate(
            db, owner=baseline_estimate_owner(tender_round, tender), data=baseline.data,
            parser_version=parser_version, import_job_id=import_job_id, replace=False,
            unit_resolver=unit_resolver, category_resolver=category_resolver,
        )
        outcome.estimate_ids.append(part.estimate_id)
        outcome.positions_to_match.extend(part.positions_to_match)
        outcome.warnings.extend(part.warnings)

    outcome.estimates_created = len(outcome.estimate_ids)
    return outcome
```

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_round_import.py -q`
Expected: PASS все.

- [ ] **Step 9: пайплайн — ветка раунда**

В `run_import_job` заменить заглушку `else: raise EstimateImportError("Импорт раунда ещё не подключён.")`:

```python
            else:
                tender_round = db.get(TenderRound, context.round_id)
                if tender_round is None:
                    raise EstimateImportError(
                        f"Раунд {context.round_id} не найден — импортировать сводную таблицу не к чему."
                    )
                resolver = UnitResolver(db)
                category_resolver = CategoryResolver.from_db(db)
                round_outcome = import_round(
                    db, tender_round=tender_round, data=parse_result.data,
                    parser_version=parse_result.parser_version, import_job_id=job_id,
                    replace=replace, unit_resolver=resolver, category_resolver=category_resolver,
                )
                positions_to_match = round_outcome.positions_to_match
                domain_warnings = round_outcome.warnings
                estimates_created = round_outcome.estimates_created
                log_estimate_ids = round_outcome.estimate_ids
```

Импорты `TenderRound`, `import_round`.

Тест в `test_import_pipeline.py`:

```python
class TestRoundJob:
    def test_round_job_creates_all_estimates_and_matches_once(self, job_env, monkeypatch):
        from services import import_pipeline as pipeline_module
        from tests.payloads import baseline_proposal_block, proposal, round_payload

        rnd = job_env.factories.TenderRoundFactory.create()
        job_env.db.flush()
        job = job_env.factories.ImportJobFactory.create(
            contract=None, round_id=rnd.id, file_key=job_env.storage.save(b"PK\x03\x04x"),
            status=ImportJobStatus.pending.value,
        )
        job_env.db.commit()

        calls = {"n": 0}
        real_match = pipeline_module.match_positions
        def counting_match(db, items):
            calls["n"] += 1
            return real_match(db, items)
        monkeypatch.setattr(pipeline_module, "match_positions", counting_match)

        payload = round_payload(
            [proposal([position(job_title="Работа", unit="м2", unit_cost_total="10", total_cost_total="10")], title="ООО А", inn="7700000001"),
             proposal([position(job_title="Работа", unit="м2", unit_cost_total="11", total_cost_total="11")], title="ООО Б", inn="7700000002")],
            baseline=baseline_proposal_block([position(job_title="Работа", unit="м2", unit_cost_total="9", total_cost_total="9")]),
        )
        done = job_env.run(payload, job=job, parse=fake_parse(payload, version="4.0.0"))

        assert done.status == ImportJobStatus.done.value
        assert done.estimates_created == 3
        assert calls["n"] == 1
        assert done.positions_total == 3
        assert (done.matched_cache + done.matched_exact + done.matched_nonposition + done.to_review) == done.positions_total
        assert done.parsed_data == payload
```

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_import_pipeline.py -q`
Expected: PASS.

- [ ] **Step 10: полный интеграционный набор, ruff**

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest -n 8 -q` — PASS.
Run: `cd backend && uv run ruff check .` — clean.

- [ ] **Step 11: Commit**

```bash
git add backend/services/round_import.py backend/services/import_pipeline.py \
  backend/tests/integration/test_round_import.py backend/tests/integration/test_import_pipeline.py
git commit -m "feat(tenders-contour): импорт раунда — проекции по ИНН, get-or-create, замена целиком, один матчинг"
```

**Точка ревью** (обязательная): сверить порядок локов с §2.11, отсутствие
`replace=True` внутри цикла, отсутствие второго `match_positions`.

---

## Task 7: CRUD тендеров — карточка с решёткой, «Итого с НДС», три команды удаления, текущий job

Спека §2.11, §2.12, §2.13. Домен без HTTP. Каждая функция — свой тест;
удаления проверяются на полной решётке.

**Files:**
- Create: `backend/crud/estimate_totals.py`
- Create: `backend/crud/tenders.py`
- Modify: `backend/crud/references.py` (`delete_contractor`, `_UNIQUE_MESSAGES`)
- Test: `backend/tests/integration/test_tenders_crud.py`
- Test: `backend/tests/integration/test_references_api.py` (один тест)

**Interfaces:**
- Consumes: модели и фабрики (2), `round_payload`/`import_round` (5, 6) для наполнения решётки.
- Produces (`crud/estimate_totals.py`): `estimate_total_including_vat(db, estimate_id: int) -> Decimal | None`.
- Produces (`crud/tenders.py`):
  - `list_tenders(db, *, q: str | None, page: int, page_size: int) -> dict` — `{"items": [...], "total", "page", "page_size"}`; item: `id, tender_number, title, object_id, object_title, rate_class_id, rate_class_title, rounds_count, participants_count, created_at`;
  - `get_tender(db, tender_id) -> Tender` (404 `DomainError`);
  - `get_tender_card(db, tender_id) -> dict` — форма §2.13: `id, tender_number, title, notes, object_id, object_title, object_address, rate_class_id, rate_class_title, created_at, rounds[], participants[], cells[]`;
  - `create_tender(db, *, object_id, title, tender_number, rate_class_id: int | None, notes) -> dict` — `rate_class_id` не передан → класс объекта (как у договора; нет ни там ни там → 422);
  - `update_tender(db, tender_id, *, title=UNSET, notes=UNSET) -> dict`;
  - `delete_tender(db, tender_id) -> list[str]` — `file_keys`;
  - `create_round(db, tender_id, *, stage_no: int, label, held_on) -> dict`;
  - `update_round(db, tender_id, round_id, *, label=UNSET, held_on=UNSET) -> dict`;
  - `get_round(db, tender_id, round_id) -> TenderRound` — ПАРОЙ, 404 на чужой тендер;
  - `delete_round(db, tender_id, round_id) -> list[str]`;
  - `participant_deletion_preview(db, tender_id, package_id) -> dict` — `{rounds_count, estimates_count, positions_count, overrides_count, confirmation_token}`;
  - `delete_participant(db, tender_id, package_id, *, confirmation_token: str | None) -> None` — без token или с устаревшим → `DomainError(409, ..., code="confirmation_required", context=preview)`;
  - `current_round_job(db, round_id) -> ImportJob | None` — правило §2.12;
  - `list_round_import_jobs(db, tender_id, round_id) -> list[dict]` — как `list_contract_import_jobs`, но `estimate_ids: list[int]`, `is_current: bool`;
  - `active_round_job(db, round_id) -> ImportJob | None`.
  - Коды `DomainError`: `"confirmation_required"`, `"active_import"`.

- [ ] **Step 1: падающие тесты «Итого с НДС»**

`backend/tests/integration/test_tenders_crud.py`:

```python
"""CRUD тендерного контура (спека §2.11–§2.13): итог единогласием, карточка с
решёткой, три команды удаления на ПОЛНОЙ решётке, третий потребитель
подрядчика.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud import tenders as crud_tenders
from crud.common import DomainError
from crud.estimate_totals import estimate_total_including_vat
from models import Estimate, ImportJob, ImportJobStatus, Offer, OfferPackage, ProposalSummaryLine
from services.category_resolution import CategoryResolver
from services.round_import import import_round
from services.unit_resolution import UnitResolver
from tests.payloads import baseline_proposal_block, position, proposal, round_payload

pytestmark = pytest.mark.integration


def _summary_line(db, proposal_id, total):
    db.add(ProposalSummaryLine(proposal_id=proposal_id, summary_key="total_cost_including_vat",
                               job_title="ИТОГО", total_cost=total))
    db.flush()


class TestEstimateTotalIncludingVat:
    def _estimate_with_lots(self, db, factories, totals):
        estimate = factories.EstimateFactory.create()
        for total in totals:
            lot = factories.LotFactory.create(estimate=estimate)
            prop = factories.ProposalFactory.create(lot=lot)
            db.flush()
            if total is not ...:
                _summary_line(db, prop.id, total)
        return estimate

    def test_unanimous_value_is_returned(self, db_session, factories):
        e = self._estimate_with_lots(db_session, factories, [Decimal("1200.00"), Decimal("1200.00")])
        assert estimate_total_including_vat(db_session, e.id) == Decimal("1200.00")

    def test_disagreement_is_null(self, db_session, factories):
        e = self._estimate_with_lots(db_session, factories, [Decimal("1200.00"), Decimal("1300.00")])
        assert estimate_total_including_vat(db_session, e.id) is None

    def test_missing_line_on_one_proposal_is_null(self, db_session, factories):
        e = self._estimate_with_lots(db_session, factories, [Decimal("1200.00"), ...])
        assert estimate_total_including_vat(db_session, e.id) is None

    def test_nan_on_one_proposal_is_null(self, db_session, factories):
        e = self._estimate_with_lots(db_session, factories, [Decimal("1200.00"), Decimal("NaN")])
        assert estimate_total_including_vat(db_session, e.id) is None

    def test_no_proposals_is_null(self, db_session, factories):
        e = factories.EstimateFactory.create()
        db_session.flush()
        assert estimate_total_including_vat(db_session, e.id) is None
```

- [ ] **Step 2: убедиться, что падают; реализовать**

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_tenders_crud.py -q`
Expected: сбор падает — `No module named 'crud.estimate_totals'`.

`backend/crud/estimate_totals.py`:

```python
"""«Итого с НДС» сметы — правило единогласия (спека контура §2.13).

Блок итогов общий для листа и повторяется у предложения каждого лота, поэтому
сумма по предложениям дала бы N-кратный итог на многолотовом файле. Значение
есть ТОЛЬКО если у каждого предложения ровно одно конечное значение
`total_cost_including_vat.total_cost_total` и все они равны; пропуск или
`NaN`/`Infinity` у одного — `None`, «единогласия остальных» не бывает.

Паспорт считает то же число СУММОЙ (`project_passport._file_total_including_vat`)
по записанному правилу его спеки §2.5 — здесь оно не переписывается
(`TECH_DEBT.md` запись 19).
"""
from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from models import Lot, Proposal, ProposalSummaryLine
from parser.constants import JSON_KEY_TOTAL_COST_INCLUDING_VAT


def estimate_total_including_vat(db: Session, estimate_id: int) -> Decimal | None:
    proposal_ids = list(db.execute(
        sa.select(Proposal.id).join(Lot, Lot.id == Proposal.lot_id).where(Lot.estimate_id == estimate_id)
    ).scalars())
    if not proposal_ids:
        return None
    totals = list(db.execute(
        sa.select(ProposalSummaryLine.total_cost).where(
            ProposalSummaryLine.proposal_id.in_(proposal_ids),
            ProposalSummaryLine.summary_key == JSON_KEY_TOTAL_COST_INCLUDING_VAT,
        )
    ).scalars())
    if len(totals) != len(proposal_ids):
        return None
    if any(value is None or not value.is_finite() for value in totals):
        return None
    if len(set(totals)) != 1:
        return None
    return totals[0]
```

Run: тот же — `TestEstimateTotalIncludingVat` PASS 5.

- [ ] **Step 3: падающие тесты карточки, текущего job и удалений**

Дописать в `test_tenders_crud.py`:

```python
P_A = lambda: proposal([position(job_title="Работа", unit="м2", unit_cost_total="10", total_cost_total="10")],
                       title="ООО А", inn="7700000001")
P_B = lambda: proposal([position(job_title="Работа", unit="м2", unit_cost_total="12", total_cost_total="12")],
                       title="ООО Б", inn="7700000002")


def _grid(db_session, factories, *, round_participants: dict[int, list], baseline_in_round_1: bool):
    """Тендер с двумя раундами. Jobs проводятся так, как их оставил бы пайплайн:
    done, estimates_created, parsed_data и parser_version заполнены — иначе
    правило текущего job (§2.12) и проверка «аудит сохранён» тестам не видны."""
    tender = factories.TenderFactory.create()
    r1 = factories.TenderRoundFactory.create(tender=tender, stage_no=1)
    r2 = factories.TenderRoundFactory.create(tender=tender, stage_no=2)
    db_session.flush()
    base = baseline_proposal_block([position(job_title="База", unit="м2", unit_cost_total="9", total_cost_total="9")])
    jobs = {}
    for rnd, key in ((r1, "k-r1"), (r2, "k-r2")):
        participants = round_participants[rnd.stage_no]
        data = round_payload(participants, baseline=base if (rnd.stage_no == 1 and baseline_in_round_1) else None)
        job = factories.ImportJobFactory.create(contract=None, round_id=rnd.id, status=ImportJobStatus.pending.value,
                                                file_key=key, file_sha256=f"{rnd.stage_no:064x}")
        db_session.flush()
        outcome = import_round(db_session, tender_round=rnd, data=data, parser_version="4.0.0", import_job_id=job.id,
                               replace=False, unit_resolver=UnitResolver(db_session),
                               category_resolver=CategoryResolver.from_db(db_session))
        job.status = ImportJobStatus.done.value
        job.estimates_created = outcome.estimates_created
        job.parsed_data = data
        job.parser_version = "4.0.0"
        jobs[rnd.stage_no] = job
    db_session.flush()

    class Grid:
        pass

    g = Grid()
    g.tender, g.r1, g.r2, g.j1, g.j2 = tender, r1, r2, jobs[1], jobs[2]

    def package_of(inn):
        return db_session.execute(
            sa.select(OfferPackage).join(OfferPackage.contractor)
            .where(OfferPackage.tender_id == tender.id, OfferPackage.contractor.has(inn=inn))
        ).scalar_one()

    g.package_a = package_of("7700000001")
    g.package_b = package_of("7700000002")
    return g


@pytest.fixture
def rectangular_grid(db_session, factories):
    """Участник Б появляется ТОЛЬКО во втором раунде: у решётки есть ячейка без
    Offer. Корпус для формы карточки (§2.13), не для удалений."""
    return _grid(db_session, factories, round_participants={1: [P_A()], 2: [P_A(), P_B()]}, baseline_in_round_1=True)


@pytest.fixture
def full_grid(db_session, factories):
    """2 раунда × 2 участника, смета в КАЖДОЙ ячейке плюс baseline в первом
    раунде — корпус, которого спека требует для удалений (§2.11, §6)."""
    return _grid(db_session, factories, round_participants={1: [P_A(), P_B()], 2: [P_A(), P_B()]}, baseline_in_round_1=True)


class TestTenderCard:
    def test_cells_are_the_full_rectangle_with_three_states(self, db_session, rectangular_grid):
        g = rectangular_grid
        card = crud_tenders.get_tender_card(db_session, g.tender.id)
        assert [r["stage_no"] for r in card["rounds"]] == [1, 2]
        assert {p["inn"] for p in card["participants"]} == {"7700000001", "7700000002"}
        cells = {(c["round_id"], c["package_id"]): c for c in card["cells"]}
        assert len(cells) == 4
        # участник Б в раунде 1 не участвовал: ни Offer, ни сметы
        none_cell = cells[(g.r1.id, g.package_b.id)]
        assert none_cell["offer_id"] is None and none_cell["estimate_id"] is None
        # участник А в раунде 1: и Offer, и смета, и «Итого с НДС»
        full_cell = cells[(g.r1.id, g.package_a.id)]
        assert full_cell["offer_id"] is not None and full_cell["estimate_id"] is not None
        assert full_cell["total_including_vat"] == "1200.00"

    def test_round_carries_baseline_and_current_job(self, db_session, rectangular_grid):
        g = rectangular_grid
        card = crud_tenders.get_tender_card(db_session, g.tender.id)
        r1 = next(r for r in card["rounds"] if r["stage_no"] == 1)
        assert r1["baseline_estimate_id"] is not None
        assert r1["baseline_total_including_vat"] == "1200.00"
        assert r1["current_job_id"] == g.j1.id
        r2 = next(r for r in card["rounds"] if r["stage_no"] == 2)
        assert r2["baseline_estimate_id"] is None


class TestCurrentRoundJob:
    def test_done_job_with_full_set_is_current(self, db_session, full_grid):
        assert crud_tenders.current_round_job(db_session, full_grid.r2.id).id == full_grid.j2.id

    def test_job_is_not_current_after_a_participant_is_removed(self, db_session, full_grid):
        crud_tenders.delete_participant(
            db_session, full_grid.tender.id, full_grid.package_b.id,
            confirmation_token=crud_tenders.participant_deletion_preview(
                db_session, full_grid.tender.id, full_grid.package_b.id)["confirmation_token"],
        )
        assert crud_tenders.current_round_job(db_session, full_grid.r2.id) is None

    def test_non_done_job_is_never_current(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        factories.ImportJobFactory.create(contract=None, round_id=rnd.id, status=ImportJobStatus.error.value,
                                          estimates_created=None, file_key="k-e")
        db_session.flush()
        assert crud_tenders.current_round_job(db_session, rnd.id) is None


class TestDeleteParticipant:
    def test_without_token_returns_preview_and_deletes_nothing(self, db_session, full_grid):
        with pytest.raises(DomainError) as err:
            crud_tenders.delete_participant(db_session, full_grid.tender.id, full_grid.package_b.id, confirmation_token=None)
        assert err.value.code == "confirmation_required"
        assert err.value.context["rounds_count"] == 2
        assert err.value.context["estimates_count"] == 2
        assert err.value.context["confirmation_token"]
        assert db_session.get(OfferPackage, full_grid.package_b.id) is not None

    def test_stale_token_returns_fresh_preview_and_deletes_nothing(self, db_session, full_grid):
        with pytest.raises(DomainError) as err:
            crud_tenders.delete_participant(db_session, full_grid.tender.id, full_grid.package_b.id,
                                            confirmation_token="stale")
        assert err.value.code == "confirmation_required"
        assert db_session.get(OfferPackage, full_grid.package_b.id) is not None

    def test_valid_token_deletes_offers_estimates_and_package_but_not_jobs(self, db_session, full_grid):
        preview = crud_tenders.participant_deletion_preview(db_session, full_grid.tender.id, full_grid.package_b.id)
        before_jobs = db_session.execute(sa.select(sa.func.count()).select_from(ImportJob)).scalar_one()

        crud_tenders.delete_participant(db_session, full_grid.tender.id, full_grid.package_b.id,
                                        confirmation_token=preview["confirmation_token"])

        assert db_session.get(OfferPackage, full_grid.package_b.id) is None
        assert db_session.execute(sa.select(sa.func.count()).select_from(Offer).where(Offer.package_id == full_grid.package_b.id)).scalar_one() == 0
        # полная решётка: 4 offer-сметы + baseline = 5; ушли две сметы Б, остались
        # две сметы А и baseline
        assert db_session.execute(sa.select(sa.func.count()).select_from(Estimate)).scalar_one() == 3
        assert db_session.execute(sa.select(sa.func.count()).select_from(ImportJob)).scalar_one() == before_jobs
        assert db_session.get(ImportJob, full_grid.j2.id).parsed_data is not None

    def test_active_import_refuses_with_its_own_code(self, db_session, full_grid, factories):
        factories.ImportJobFactory.create(contract=None, round_id=full_grid.r2.id,
                                          status=ImportJobStatus.parsing.value, file_key="k-active")
        db_session.flush()
        with pytest.raises(DomainError) as err:
            crud_tenders.delete_participant(db_session, full_grid.tender.id, full_grid.package_b.id, confirmation_token="x")
        assert err.value.code == "active_import"


class TestDeleteRoundAndTender:
    def test_delete_round_returns_all_history_files(self, db_session, full_grid, factories):
        factories.ImportJobFactory.create(contract=None, round_id=full_grid.r1.id,
                                          status=ImportJobStatus.error.value, file_key="k-r1-old")
        db_session.flush()
        keys = crud_tenders.delete_round(db_session, full_grid.tender.id, full_grid.r1.id)
        assert sorted(keys) == ["k-r1", "k-r1-old"]
        assert db_session.execute(sa.select(sa.func.count()).select_from(Estimate).where(
            sa.or_(Estimate.round_id == full_grid.r1.id))).scalar_one() == 0
        # раунд 2 не тронут
        assert crud_tenders.current_round_job(db_session, full_grid.r2.id) is not None

    def test_delete_round_of_another_tender_is_404(self, db_session, full_grid, factories):
        other = factories.TenderFactory.create()
        db_session.flush()
        with pytest.raises(DomainError) as err:
            crud_tenders.delete_round(db_session, other.id, full_grid.r1.id)
        assert err.value.status_code == 404

    def test_delete_tender_on_full_grid_removes_everything(self, db_session, full_grid):
        keys = crud_tenders.delete_tender(db_session, full_grid.tender.id)
        assert sorted(keys) == ["k-r1", "k-r2"]
        for model in (Estimate, Offer, OfferPackage, ImportJob):
            assert db_session.execute(sa.select(sa.func.count()).select_from(model)).scalar_one() == 0

    def test_delete_tender_refuses_during_active_import(self, db_session, full_grid, factories):
        factories.ImportJobFactory.create(contract=None, round_id=full_grid.r1.id,
                                          status=ImportJobStatus.importing.value, file_key="k-act")
        db_session.flush()
        with pytest.raises(DomainError) as err:
            crud_tenders.delete_tender(db_session, full_grid.tender.id)
        assert err.value.code == "active_import"
```

И в `test_references_api.py`:

```python
def test_delete_contractor_refused_while_it_is_a_tender_participant(client, factories):
    """Третий потребитель подрядчика (спека контура §2.13): пакет есть,
    материализованных предложений нет — раньше это был сырой IntegrityError."""
    package = factories.OfferPackageFactory.create()
    response = client.delete(f"/api/v1/contractors/{package.contractor_id}")
    assert response.status_code == 409
    assert "тендерах (1)" in response.json()["detail"]
```

- [ ] **Step 4: реализация `crud/tenders.py`**

```python
"""Тендерный контур: тендеры, раунды, участники, решётка, удаления (спека
2026-08-26-tenders-contour-design.md §2.11–§2.13).

Иерархия локов — ОДНА для импорта и всех удалений: tender → rounds по id →
package (§2.11). Удаление раунда и upload берут тендер FOR KEY SHARE; удаление
тендера и участника — FOR UPDATE. `RESTRICT` от пакета требует явного
`DELETE offers` до пакета и до тендера.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, joinedload

from crud.common import UNSET, DomainError, clamp_page, iso, paginated, require_text
from crud.estimate_totals import estimate_total_including_vat
from models import (
    TERMINAL_IMPORT_JOB_STATUSES,
    Contractor,
    Estimate,
    EstimateCategoryOverride,
    ImportJob,
    ImportJobStatus,
    Lot,
    ObjectModel,
    Offer,
    OfferPackage,
    PositionItem,
    Proposal,
    RateClass,
    Tender,
    TenderRound,
)

log = logging.getLogger(__name__)

_TERMINAL = [s.value for s in TERMINAL_IMPORT_JOB_STATUSES]


def _dec(value) -> str | None:
    return None if value is None else str(value)


# ---------------------------------------------------------------------------
#  Чтение
# ---------------------------------------------------------------------------

def get_tender(db: Session, tender_id: int) -> Tender:
    tender = db.get(Tender, tender_id)
    if tender is None:
        raise DomainError(404, f"Тендер {tender_id} не найден.")
    return tender


def get_round(db: Session, tender_id: int, round_id: int) -> TenderRound:
    """Раунд ищется ПАРОЙ (§2.13): чужой тендер в URL — 404, не чужие данные."""
    rnd = db.execute(
        sa.select(TenderRound).where(TenderRound.id == round_id, TenderRound.tender_id == tender_id)
    ).scalar_one_or_none()
    if rnd is None:
        raise DomainError(404, f"Раунд {round_id} тендера {tender_id} не найден.")
    return rnd


def list_tenders(db: Session, *, q: str | None = None, page: int = 1, page_size: int = 20) -> dict:
    page, page_size = clamp_page(page, page_size)
    rounds_count = sa.select(sa.func.count()).where(TenderRound.tender_id == Tender.id).scalar_subquery()
    participants_count = sa.select(sa.func.count()).where(OfferPackage.tender_id == Tender.id).scalar_subquery()
    stmt = (
        sa.select(Tender, ObjectModel.title, RateClass.title, rounds_count, participants_count)
        .join(ObjectModel, ObjectModel.id == Tender.object_id)
        .join(RateClass, RateClass.id == Tender.rate_class_id)
    )
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(sa.or_(Tender.tender_number.ilike(pattern), Tender.title.ilike(pattern),
                                 ObjectModel.title.ilike(pattern)))
    rows, total = paginated(db, stmt, order_by=(Tender.created_at.desc(), Tender.id.desc()),
                            page=page, page_size=page_size)
    return {
        "items": [
            {
                "id": t.id, "tender_number": t.tender_number, "title": t.title,
                "object_id": t.object_id, "object_title": object_title,
                "rate_class_id": t.rate_class_id, "rate_class_title": rate_class_title,
                "rounds_count": rounds, "participants_count": participants,
                "created_at": iso(t.created_at),
            }
            for t, object_title, rate_class_title, rounds, participants in rows
        ],
        "total": total, "page": page, "page_size": page_size,
    }


def round_estimate_ids(db: Session, round_id: int) -> list[int]:
    """Все сметы раунда — offer-owned и baseline. Публичная: роутер загрузки
    решает по ней «есть ли что заменять» (задача 8)."""
    offer_ids = sa.select(Offer.id).where(Offer.round_id == round_id)
    return list(db.execute(
        sa.select(Estimate.id).where(sa.or_(Estimate.round_id == round_id, Estimate.offer_id.in_(offer_ids)))
    ).scalars())


def current_round_job(db: Session, round_id: int) -> ImportJob | None:
    """Текущий job раунда (§2.12): done, число смет раунда с этим job равно
    estimates_created, других смет у раунда нет. После удаления участника
    набор неполон — job перестаёт быть текущим, и тот же файл требует replace."""
    estimate_ids = round_estimate_ids(db, round_id)
    if not estimate_ids:
        return None
    rows = db.execute(
        sa.select(Estimate.import_job_id, sa.func.count()).where(Estimate.id.in_(estimate_ids))
        .group_by(Estimate.import_job_id)
    ).all()
    if len(rows) != 1:
        return None
    job_id, count = rows[0]
    if job_id is None:
        return None
    job = db.get(ImportJob, job_id)
    if job is None or job.status != ImportJobStatus.done.value or job.estimates_created != count:
        return None
    return job


def active_round_job(db: Session, round_id: int) -> ImportJob | None:
    return db.execute(
        sa.select(ImportJob).where(ImportJob.round_id == round_id, ImportJob.status.notin_(_TERMINAL))
        .order_by(ImportJob.id).limit(1)
    ).scalar_one_or_none()


def get_tender_card(db: Session, tender_id: int) -> dict:
    tender = db.execute(
        sa.select(Tender).options(joinedload(Tender.object), joinedload(Tender.rate_class))
        .where(Tender.id == tender_id)
    ).scalar_one_or_none()
    if tender is None:
        raise DomainError(404, f"Тендер {tender_id} не найден.")

    rounds = db.execute(sa.select(TenderRound).where(TenderRound.tender_id == tender_id)
                        .order_by(TenderRound.stage_no)).scalars().all()
    packages = db.execute(
        sa.select(OfferPackage, Contractor).join(Contractor, Contractor.id == OfferPackage.contractor_id)
        .where(OfferPackage.tender_id == tender_id).order_by(Contractor.title)
    ).all()
    offers = db.execute(
        sa.select(Offer, Estimate.id).outerjoin(Estimate, Estimate.offer_id == Offer.id)
        .where(Offer.tender_id == tender_id)
    ).all()
    offer_by_cell = {(o.round_id, o.package_id): (o.id, estimate_id) for o, estimate_id in offers}

    rounds_out = []
    for rnd in rounds:
        latest = db.execute(sa.select(ImportJob).where(ImportJob.round_id == rnd.id)
                            .order_by(ImportJob.id.desc()).limit(1)).scalar_one_or_none()
        current = current_round_job(db, rnd.id)
        baseline_id = db.execute(sa.select(Estimate.id).where(Estimate.round_id == rnd.id)).scalar_one_or_none()
        rounds_out.append({
            "id": rnd.id, "stage_no": rnd.stage_no, "label": rnd.label, "held_on": iso(rnd.held_on),
            "latest_job": _job_brief(latest) if latest else None,
            "current_job_id": current.id if current else None,
            "baseline_estimate_id": baseline_id,
            "baseline_total_including_vat": _dec(estimate_total_including_vat(db, baseline_id)) if baseline_id else None,
        })

    cells = []
    for rnd in rounds:
        for package, _ in packages:
            offer_id, estimate_id = offer_by_cell.get((rnd.id, package.id), (None, None))
            cells.append({
                "round_id": rnd.id, "package_id": package.id,
                "offer_id": offer_id, "estimate_id": estimate_id,
                "total_including_vat": _dec(estimate_total_including_vat(db, estimate_id)) if estimate_id else None,
            })

    return {
        "id": tender.id, "tender_number": tender.tender_number, "title": tender.title, "notes": tender.notes,
        "object_id": tender.object_id, "object_title": tender.object.title, "object_address": tender.object.address,
        "rate_class_id": tender.rate_class_id, "rate_class_title": tender.rate_class.title,
        "created_at": iso(tender.created_at),
        "rounds": rounds_out,
        "participants": [
            {"package_id": p.id, "contractor_id": c.id, "title": c.title, "inn": c.inn} for p, c in packages
        ],
        "cells": cells,
    }


def _job_brief(job: ImportJob) -> dict:
    return {"id": job.id, "status": job.status, "filename": job.filename,
            "finished_at": iso(job.finished_at), "created_at": iso(job.created_at)}


def list_round_import_jobs(db: Session, tender_id: int, round_id: int) -> list[dict]:
    get_round(db, tender_id, round_id)
    current = current_round_job(db, round_id)
    jobs = db.execute(sa.select(ImportJob).where(ImportJob.round_id == round_id)
                      .order_by(ImportJob.id.desc())).scalars().all()
    out = []
    for job in jobs:
        estimate_ids = list(db.execute(sa.select(Estimate.id).where(Estimate.import_job_id == job.id)
                                       .order_by(Estimate.id)).scalars())
        out.append({
            "id": job.id, "owner_type": "round", "tender_id": tender_id, "round_id": round_id,
            "filename": job.filename, "file_sha256": job.file_sha256, "status": job.status,
            "error_text": job.error_text, "warnings": job.warnings,
            "counters": {
                "positions_total": job.positions_total, "matched_cache": job.matched_cache,
                "matched_exact": job.matched_exact, "matched_nonposition": job.matched_nonposition,
                "to_review": job.to_review,
            },
            "estimate_ids": estimate_ids, "estimates_created": job.estimates_created,
            "is_current": current is not None and current.id == job.id,
            "created_at": iso(job.created_at), "started_at": iso(job.started_at), "finished_at": iso(job.finished_at),
        })
    return out


# ---------------------------------------------------------------------------
#  Создание и правка
# ---------------------------------------------------------------------------

def create_tender(db: Session, *, object_id: int, title: str, tender_number: str,
                  rate_class_id: int | None, notes: str | None) -> dict:
    obj = db.get(ObjectModel, object_id)
    if obj is None:
        raise DomainError(404, f"Объект {object_id} не найден.")
    snapshot_class = rate_class_id if rate_class_id is not None else obj.rate_class_id
    if snapshot_class is None:
        raise DomainError(422, "Класс объектов не задан ни у тендера, ни у объекта.")
    if db.get(RateClass, snapshot_class) is None:
        raise DomainError(404, f"Класс объектов {snapshot_class} не найден.")
    tender = Tender(
        object_id=object_id, title=require_text(title, "Предмет торга"),
        tender_number=require_text(tender_number, "Номер тендера"),
        rate_class_id=snapshot_class, notes=(notes or None),
    )
    db.add(tender)
    from crud.common import translating_integrity
    with translating_integrity(db, {"uq_tenders_tender_number": "Тендер с таким номером уже есть."}):
        db.commit()
    log.info("tender_created id=%s", tender.id)
    return get_tender_card(db, tender.id)


def update_tender(db: Session, tender_id: int, *, title=UNSET, notes=UNSET) -> dict:
    tender = get_tender(db, tender_id)
    if title is not UNSET:
        tender.title = require_text(title, "Предмет торга")
    if notes is not UNSET:
        tender.notes = notes or None
    db.commit()
    return get_tender_card(db, tender_id)


def create_round(db: Session, tender_id: int, *, stage_no: int, label: str | None, held_on) -> dict:
    get_tender(db, tender_id)
    if stage_no <= 0:
        raise DomainError(422, "Номер этапа нумеруется с 1.")
    rnd = TenderRound(tender_id=tender_id, stage_no=stage_no, label=label or None, held_on=held_on)
    db.add(rnd)
    from crud.common import translating_integrity
    with translating_integrity(db, {"uq_tender_rounds_tender_stage": "Этап с таким номером в этом тендере уже есть."}):
        db.commit()
    return get_tender_card(db, tender_id)


def update_round(db: Session, tender_id: int, round_id: int, *, label=UNSET, held_on=UNSET) -> dict:
    rnd = get_round(db, tender_id, round_id)
    if label is not UNSET:
        rnd.label = label or None
    if held_on is not UNSET:
        rnd.held_on = held_on
    db.commit()
    return get_tender_card(db, tender_id)


# ---------------------------------------------------------------------------
#  Удаления — команды с локами по иерархии tender → rounds → package
# ---------------------------------------------------------------------------

def _lock_tender(db: Session, tender_id: int, *, exclusive: bool) -> Tender:
    stmt = sa.select(Tender).where(Tender.id == tender_id).execution_options(populate_existing=True)
    stmt = stmt.with_for_update() if exclusive else stmt.with_for_update(key_share=True)
    tender = db.execute(stmt).scalar_one_or_none()
    if tender is None:
        raise DomainError(404, f"Тендер {tender_id} не найден.")
    return tender


def _lock_rounds(db: Session, tender_id: int) -> list[int]:
    return list(db.execute(
        sa.select(TenderRound.id).where(TenderRound.tender_id == tender_id).order_by(TenderRound.id).with_for_update()
    ).scalars())


def _refuse_if_active(db: Session, round_ids: list[int]) -> None:
    if not round_ids:
        return
    active = db.execute(
        sa.select(ImportJob.id, ImportJob.status).where(ImportJob.round_id.in_(round_ids), ImportJob.status.notin_(_TERMINAL))
        .order_by(ImportJob.id).limit(1)
    ).first()
    if active is not None:
        raise DomainError(
            409, f"Импорт раунда выполняется (задание {active.id}, статус «{active.status}»). "
            "Дождитесь завершения и повторите.",
            code="active_import", context={"job_id": active.id},
        )


def delete_round(db: Session, tender_id: int, round_id: int) -> list[str]:
    """tender FOR KEY SHARE → round FOR UPDATE; каскад уносит сметы и jobs раунда."""
    _lock_tender(db, tender_id, exclusive=False)
    rnd = db.execute(
        sa.select(TenderRound).where(TenderRound.id == round_id, TenderRound.tender_id == tender_id).with_for_update()
    ).scalar_one_or_none()
    if rnd is None:
        raise DomainError(404, f"Раунд {round_id} тендера {tender_id} не найден.")
    _refuse_if_active(db, [round_id])
    file_keys = list(db.execute(sa.select(ImportJob.file_key).where(ImportJob.round_id == round_id).order_by(ImportJob.id)).scalars())
    db.execute(sa.delete(TenderRound).where(TenderRound.id == round_id))
    db.commit()
    log.info("tender_round_deleted tender=%s round=%s jobs=%d", tender_id, round_id, len(file_keys))
    return file_keys


def delete_tender(db: Session, tender_id: int) -> list[str]:
    """tender FOR UPDATE → все раунды по id → явный DELETE offers → тендер (каскад)."""
    _lock_tender(db, tender_id, exclusive=True)
    round_ids = _lock_rounds(db, tender_id)
    _refuse_if_active(db, round_ids)
    file_keys = list(db.execute(sa.select(ImportJob.file_key).where(ImportJob.round_id.in_(round_ids)).order_by(ImportJob.id)).scalars()) if round_ids else []
    # RESTRICT от пакета: ветка каскада packages встретила бы живые offers.
    db.execute(sa.delete(Offer).where(Offer.tender_id == tender_id))
    db.execute(sa.delete(Tender).where(Tender.id == tender_id))
    db.commit()
    log.info("tender_deleted id=%s jobs=%d", tender_id, len(file_keys))
    return file_keys


def _participant_composition(db: Session, package_id: int) -> dict:
    offer_ids = list(db.execute(sa.select(Offer.id).where(Offer.package_id == package_id).order_by(Offer.id)).scalars())
    estimate_ids = list(db.execute(sa.select(Estimate.id).where(Estimate.offer_id.in_(offer_ids)).order_by(Estimate.id)).scalars()) if offer_ids else []
    positions = db.execute(
        sa.select(sa.func.count()).select_from(PositionItem).join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id).where(Lot.estimate_id.in_(estimate_ids))
    ).scalar_one() if estimate_ids else 0
    overrides = db.execute(
        sa.select(sa.func.count()).select_from(EstimateCategoryOverride)
        .join(PositionItem, PositionItem.id == EstimateCategoryOverride.position_item_id)
        .join(Proposal, Proposal.id == PositionItem.proposal_id).join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id.in_(estimate_ids))
    ).scalar_one() if estimate_ids else 0
    token = hashlib.sha256(json.dumps(
        {"offers": offer_ids, "estimates": estimate_ids, "positions": positions, "overrides": overrides},
        sort_keys=True,
    ).encode()).hexdigest()
    return {
        "rounds_count": len(offer_ids), "estimates_count": len(estimate_ids),
        "positions_count": positions, "overrides_count": overrides, "confirmation_token": token,
    }


def participant_deletion_preview(db: Session, tender_id: int, package_id: int) -> dict:
    get_tender(db, tender_id)
    package = db.execute(sa.select(OfferPackage).where(OfferPackage.id == package_id, OfferPackage.tender_id == tender_id)).scalar_one_or_none()
    if package is None:
        raise DomainError(404, f"Участник {package_id} тендера {tender_id} не найден.")
    return _participant_composition(db, package_id)


def delete_participant(db: Session, tender_id: int, package_id: int, *, confirmation_token: str | None) -> None:
    """«Удалить участника и его материализованные предложения; исходные файлы
    и результаты разбора сохраняются» (§2.11).

    tender FOR UPDATE → раунды по id → package FOR UPDATE → пересчёт состава →
    token совпал → явный DELETE offers → DELETE package. Несовпадение — 409 с
    ОБНОВЛЁННЫМ preview, ничего не удалено.
    """
    _lock_tender(db, tender_id, exclusive=True)
    round_ids = _lock_rounds(db, tender_id)
    package = db.execute(
        sa.select(OfferPackage).where(OfferPackage.id == package_id, OfferPackage.tender_id == tender_id).with_for_update()
    ).scalar_one_or_none()
    if package is None:
        raise DomainError(404, f"Участник {package_id} тендера {tender_id} не найден.")
    _refuse_if_active(db, round_ids)
    composition = _participant_composition(db, package_id)
    if confirmation_token != composition["confirmation_token"]:
        db.rollback()
        raise DomainError(
            409, "Удаление участника требует подтверждения состава: будут удалены предложения "
            f"в раундах — {composition['rounds_count']}, смет — {composition['estimates_count']}, "
            f"позиций — {composition['positions_count']}, ручных решений — {composition['overrides_count']}. "
            "Исходные файлы и результаты разбора сохраняются.",
            code="confirmation_required", context=composition,
        )
    db.execute(sa.delete(Offer).where(Offer.package_id == package_id))
    db.execute(sa.delete(OfferPackage).where(OfferPackage.id == package_id))
    db.commit()
    log.info("tender_participant_deleted tender=%s package=%s estimates=%d",
             tender_id, package_id, composition["estimates_count"])
```

`from crud.common import translating_integrity` вынести в шапку модуля (в
эскизе он внутри функций — это ошибка эскиза, импорт один и наверху).

- [ ] **Step 5: `delete_contractor` — третий потребитель**

В `references.py` заменить тело проверки:

```python
    contracts = db.query(Contract).filter(Contract.contractor_id == contractor.id).count()
    # proposals.contractor_id — тоже FK без каскада: подрядчик остаётся в уже
    # импортированных сметах даже если его договор удалён.
    proposals = db.query(Proposal).filter(Proposal.contractor_id == contractor.id).count()
    # offer_packages.contractor_id RESTRICT (спека контура §2.13): участник
    # тендера без материализованных предложений — третий потребитель, и без
    # этого счётчика удаление упало бы сырым IntegrityError.
    tenders = db.query(OfferPackage).filter(OfferPackage.contractor_id == contractor.id).count()
    if contracts or proposals or tenders:
        raise DomainError(
            409,
            f"Подрядчика «{contractor.title}» удалить нельзя: договоров ({contracts}), "
            f"предложений в сметах ({proposals}), участий в тендерах ({tenders}).",
        )
```

Импорт `OfferPackage` из `models`.

- [ ] **Step 6: все тесты задачи зелёные**

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_tenders_crud.py tests/integration/test_references_api.py -q`
Expected: PASS все.

- [ ] **Step 7: снятие защиты — явный `DELETE offers` (контроллер)**

Закомментировать `db.execute(sa.delete(Offer).where(Offer.tender_id == tender_id))`
в `delete_tender`. Run `test_delete_tender_on_full_grid_removes_everything`.
Expected: FAIL с `IntegrityError` по `fk_offers_package`. Вернуть строку,
прогнать — PASS. То же для `delete_participant` и
`test_valid_token_deletes_offers_estimates_and_package_but_not_jobs`.

- [ ] **Step 8: ruff, Commit**

```bash
git add backend/crud/estimate_totals.py backend/crud/tenders.py backend/crud/references.py \
  backend/tests/integration/test_tenders_crud.py backend/tests/integration/test_references_api.py
git commit -m "feat(tenders-contour): CRUD тендеров — решётка, «Итого с НДС» единогласием, три команды удаления с token"
```

---

## Task 8: API — роутер тендеров, загрузка раунда, `owner_type` у job, обобщённое скачивание

Спека §2.12, §2.13. HTTP-контракт поверх CRUD и пайплайна.

**Files:**
- Create: `backend/routers/tenders.py`
- Modify: `backend/routers/estimates.py` (`job_response`)
- Modify: `backend/routers/import_jobs.py:81-100` (текст 410)
- Modify: `backend/main.py` (регистрация)
- Test: `backend/tests/integration/test_tenders_api.py`

**Interfaces:**
- Consumes: `crud.tenders.*` (7), `run_import_job` (6), `_read_within_limit`, `_validate_xlsx`, `job_response` (`routers/estimates.py`), `purge_files_best_effort`, `raise_domain_error`.
- Produces: маршруты §2.13 под `APIRouter(prefix="/api/v1/tenders", tags=["tenders"])`;
  `job_response` → `{..., "owner_type": "contract"|"round", "contract_id", "amendment_no", "estimate_id" (у договора), "tender_id", "round_id", "estimate_ids" (у раунда), "estimates_created"}`.

- [ ] **Step 1: падающие тесты API**

`backend/tests/integration/test_tenders_api.py`:

```python
"""HTTP-контракт тендерного контура (спека §2.12, §2.13)."""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from models import Estimate, ImportJob, ImportJobStatus, UserRole
from tests.integration.test_estimates_api import finished_job, xlsx_bytes
from tests.payloads import position, proposal, round_payload

pytestmark = pytest.mark.integration

TENDERS = "/api/v1/tenders"


@pytest.fixture
def tender(committing_client, committing_factories, committing_db):
    obj = committing_factories.ObjectFactory.create()
    rc = committing_factories.RateClassFactory.create()
    committing_db.commit()
    created = committing_client.post(TENDERS, json={
        "object_id": obj.id, "title": "Генподряд", "tender_number": "Т-0001", "rate_class_id": rc.id,
    })
    assert created.status_code == 201, created.text
    card = created.json()
    rnd = committing_client.post(f"{TENDERS}/{card['id']}/rounds", json={"stage_no": 1})
    assert rnd.status_code == 201
    return {"id": card["id"], "round_id": rnd.json()["rounds"][0]["id"]}


@pytest.fixture
def round_stub(monkeypatch):
    from services import import_pipeline
    from parser import ParseResult

    holder = {"payload": None}

    def fake(_handle):
        return ParseResult(data=holder["payload"], parser_version="4.0.0", warnings=[])

    monkeypatch.setattr(import_pipeline, "parse_estimate", fake)

    def configure(payload):
        holder["payload"] = payload
    return configure


def upload_round(client, tender, *, content, replace=None, filename="сводная.xlsx"):
    data = {} if replace is None else {"replace": str(replace).lower()}
    return client.post(f"{TENDERS}/{tender['id']}/rounds/{tender['round_id']}/upload",
                       files={"file": (filename, content, "application/octet-stream")}, data=data)


P_A = lambda: proposal([position(job_title="Работа", unit="м2", unit_cost_total="10", total_cost_total="10")], title="ООО А", inn="7700000001")
P_B = lambda: proposal([position(job_title="Работа", unit="м2", unit_cost_total="12", total_cost_total="12")], title="ООО Б", inn="7700000002")


class TestRoundUpload:
    def test_upload_creates_round_job_and_all_estimates(self, committing_client, committing_db, tender, round_stub):
        round_stub(round_payload([P_A(), P_B()]))
        response = upload_round(committing_client, tender, content=xlsx_bytes())
        assert response.status_code == 202
        body = response.json()
        assert body["owner_type"] == "round" and body["round_id"] == tender["round_id"]

        job = finished_job(committing_client, response)
        assert job["status"] == ImportJobStatus.done.value
        assert job["estimates_created"] == 2
        assert len(job["estimate_ids"]) == 2
        assert "estimate_id" not in job

    def test_same_file_twice_is_idempotent_200(self, committing_client, tender, round_stub):
        round_stub(round_payload([P_A()]))
        first = upload_round(committing_client, tender, content=xlsx_bytes("same"))
        finished_job(committing_client, first)
        second = upload_round(committing_client, tender, content=xlsx_bytes("same"))
        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]

    def test_different_file_without_replace_is_409(self, committing_client, tender, round_stub):
        round_stub(round_payload([P_A()]))
        finished_job(committing_client, upload_round(committing_client, tender, content=xlsx_bytes("a")))
        assert upload_round(committing_client, tender, content=xlsx_bytes("b")).status_code == 409

    def test_replace_requires_admin(self, committing_client, tender, round_stub):
        committing_client.auth_state["role"] = UserRole.member
        response = upload_round(committing_client, tender, content=xlsx_bytes(), replace=True)
        assert response.status_code == 403

    def test_member_can_upload(self, committing_client, tender, round_stub):
        round_stub(round_payload([P_A()]))
        committing_client.auth_state["role"] = UserRole.member
        assert upload_round(committing_client, tender, content=xlsx_bytes()).status_code == 202

    def test_round_of_another_tender_in_url_is_404(self, committing_client, committing_factories, committing_db, tender, round_stub):
        other = committing_factories.TenderFactory.create()
        committing_db.commit()
        response = committing_client.post(
            f"{TENDERS}/{other.id}/rounds/{tender['round_id']}/upload",
            files={"file": ("x.xlsx", xlsx_bytes(), "application/octet-stream")},
        )
        assert response.status_code == 404

    def test_same_sha_after_participant_deleted_is_409_not_200(self, committing_client, committing_db, tender, round_stub):
        round_stub(round_payload([P_A(), P_B()]))
        first = upload_round(committing_client, tender, content=xlsx_bytes("same"))
        finished_job(committing_client, first)
        card = committing_client.get(f"{TENDERS}/{tender['id']}").json()
        package_b = next(p for p in card["participants"] if p["inn"] == "7700000002")["package_id"]
        preview = committing_client.delete(f"{TENDERS}/{tender['id']}/participants/{package_b}")
        assert preview.status_code == 409 and preview.json()["detail"]["code"] == "confirmation_required"
        token = preview.json()["detail"]["confirmation_token"]
        assert committing_client.delete(f"{TENDERS}/{tender['id']}/participants/{package_b}",
                                        params={"confirmation_token": token}).status_code == 204

        second = upload_round(committing_client, tender, content=xlsx_bytes("same"))
        assert second.status_code == 409


class TestParticipantDeletionProtocol:
    def test_stale_token_returns_fresh_preview_and_deletes_nothing(self, committing_client, committing_db, tender, round_stub):
        round_stub(round_payload([P_A()]))
        finished_job(committing_client, upload_round(committing_client, tender, content=xlsx_bytes()))
        card = committing_client.get(f"{TENDERS}/{tender['id']}").json()
        package = card["participants"][0]["package_id"]
        response = committing_client.delete(f"{TENDERS}/{tender['id']}/participants/{package}",
                                            params={"confirmation_token": "stale"})
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "confirmation_required"
        assert response.json()["detail"]["estimates_count"] == 1
        committing_db.expire_all()
        assert committing_db.execute(sa.select(sa.func.count()).select_from(Estimate)).scalar_one() == 1

    def test_member_cannot_delete_participant(self, committing_client, tender):
        committing_client.auth_state["role"] = UserRole.member
        assert committing_client.delete(f"{TENDERS}/{tender['id']}/participants/1").status_code == 403


class TestDeletionPurgesFiles:
    """Файлы удаляются ПОСЛЕ коммита, best-effort, по одному ключу (спека §2.11,
    DoD 9). Проверяется физически — через `tmp_storage.exists`, а не по списку
    возвращённых ключей."""

    def _two_jobs(self, committing_client, tender, round_stub):
        round_stub(round_payload([P_A()]))
        first = upload_round(committing_client, tender, content=xlsx_bytes("a"))
        finished_job(committing_client, first)
        second = upload_round(committing_client, tender, content=xlsx_bytes("b"), replace=True)
        finished_job(committing_client, second)
        return first.json()["id"], second.json()["id"]

    def _file_keys(self, committing_db, job_ids):
        committing_db.expire_all()
        return [committing_db.get(ImportJob, j).file_key for j in job_ids]

    def test_delete_round_purges_all_history_files(self, committing_client, committing_db, tmp_storage, tender, round_stub):
        job_ids = self._two_jobs(committing_client, tender, round_stub)
        keys = self._file_keys(committing_db, job_ids)
        assert all(tmp_storage.exists(k) for k in keys)

        response = committing_client.delete(f"{TENDERS}/{tender['id']}/rounds/{tender['round_id']}")

        assert response.status_code == 204
        assert not any(tmp_storage.exists(k) for k in keys)

    def test_delete_tender_purges_files_of_all_rounds(self, committing_client, committing_db, tmp_storage, tender, round_stub):
        job_ids = self._two_jobs(committing_client, tender, round_stub)
        r2 = committing_client.post(f"{TENDERS}/{tender['id']}/rounds", json={"stage_no": 2}).json()
        r2_id = next(r["id"] for r in r2["rounds"] if r["stage_no"] == 2)
        third = committing_client.post(f"{TENDERS}/{tender['id']}/rounds/{r2_id}/upload",
                                       files={"file": ("c.xlsx", xlsx_bytes("c"), "application/octet-stream")})
        finished_job(committing_client, third)
        keys = self._file_keys(committing_db, [*job_ids, third.json()["id"]])

        assert committing_client.delete(f"{TENDERS}/{tender['id']}").status_code == 204
        assert not any(tmp_storage.exists(k) for k in keys)

    def test_one_broken_key_does_not_stop_the_rest(self, committing_client, committing_db, tmp_storage, tender, round_stub):
        """Испорченный file_key поднимает StorageKeyError; purge изолирует
        ошибку по ключу — остальные удалены, ответ 204."""
        first_id, second_id = self._two_jobs(committing_client, tender, round_stub)
        good_key = self._file_keys(committing_db, [second_id])[0]
        committing_db.execute(sa.update(ImportJob).where(ImportJob.id == first_id).values(file_key="not-a-valid-key"))
        committing_db.commit()

        assert committing_client.delete(f"{TENDERS}/{tender['id']}/rounds/{tender['round_id']}").status_code == 204
        assert not tmp_storage.exists(good_key)

    def test_domain_refusal_deletes_no_files(self, committing_client, committing_db, committing_factories, tmp_storage, tender, round_stub):
        job_ids = self._two_jobs(committing_client, tender, round_stub)
        keys = self._file_keys(committing_db, job_ids)
        committing_factories.ImportJobFactory.create(contract=None, round_id=tender["round_id"],
                                                     status=ImportJobStatus.parsing.value, file_key="k-active")
        committing_db.commit()

        response = committing_client.delete(f"{TENDERS}/{tender['id']}/rounds/{tender['round_id']}")

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "active_import"
        assert all(tmp_storage.exists(k) for k in keys)


class TestRights:
    @pytest.mark.parametrize(("method", "path", "payload"), [
        ("post", TENDERS, {"object_id": 1, "title": "x", "tender_number": "y"}),
        ("patch", f"{TENDERS}/1", {"title": "x"}),
        ("delete", f"{TENDERS}/1", None),
        ("post", f"{TENDERS}/1/rounds", {"stage_no": 1}),
        ("patch", f"{TENDERS}/1/rounds/1", {"label": "x"}),
        ("delete", f"{TENDERS}/1/rounds/1", None),
    ])
    def test_member_cannot_change(self, committing_client, method, path, payload):
        committing_client.auth_state["role"] = UserRole.member
        response = getattr(committing_client, method)(path, json=payload) if payload is not None else getattr(committing_client, method)(path)
        assert response.status_code == 403

    def test_member_can_read(self, committing_client, tender):
        committing_client.auth_state["role"] = UserRole.member
        assert committing_client.get(TENDERS).status_code == 200
        assert committing_client.get(f"{TENDERS}/{tender['id']}").status_code == 200


class TestCardShape:
    def test_cells_are_the_rectangle_even_for_a_late_participant(self, committing_client, tender, round_stub):
        """Участник Б появляется только во втором раунде — ячейка (раунд 1, Б)
        обязана быть с offer_id = null (§2.13)."""
        round_stub(round_payload([P_A()]))
        finished_job(committing_client, upload_round(committing_client, tender, content=xlsx_bytes("r1")))
        r2 = committing_client.post(f"{TENDERS}/{tender['id']}/rounds", json={"stage_no": 2}).json()
        r2_id = next(r["id"] for r in r2["rounds"] if r["stage_no"] == 2)
        round_stub(round_payload([P_A(), P_B()]))
        finished_job(committing_client, committing_client.post(
            f"{TENDERS}/{tender['id']}/rounds/{r2_id}/upload",
            files={"file": ("r2.xlsx", xlsx_bytes("r2"), "application/octet-stream")}))

        card = committing_client.get(f"{TENDERS}/{tender['id']}").json()
        assert len(card["cells"]) == 4
        b = next(p for p in card["participants"] if p["inn"] == "7700000002")["package_id"]
        cell = next(c for c in card["cells"] if c["round_id"] == tender["round_id"] and c["package_id"] == b)
        assert cell["offer_id"] is None and cell["estimate_id"] is None


class TestContractJobResponseUnchanged:
    def test_contract_job_keeps_estimate_id_and_gains_owner_type(
        self, committing_client, committing_db, committing_factories, monkeypatch
    ):
        from parser import ParseResult
        from services import import_pipeline
        from tests.integration.test_estimates_api import upload
        from tests.payloads import payload_for

        contract = committing_factories.ContractFactory.create()
        committing_db.commit()
        # monkeypatch, не присваивание модулю: подмена обязана откатиться после
        # теста, иначе утекла бы в соседние файлы прогона.
        monkeypatch.setattr(
            import_pipeline, "parse_estimate",
            lambda _h: ParseResult(data=payload_for(contract), parser_version="4.0.0", warnings=[]),
        )
        response = upload(committing_client, content=xlsx_bytes(), contract_id=contract.id)
        job = finished_job(committing_client, response)
        assert job["owner_type"] == "contract"
        assert job["estimate_id"] is not None
        assert job["estimates_created"] == 1
```

- [ ] **Step 2: убедиться, что падают**

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_tenders_api.py -q`
Expected: фикстура `tender` падает с 404 — маршрута нет.

- [ ] **Step 3: `job_response` — дискриминированный ответ**

В `routers/estimates.py`:

```python
def job_response(db: Session, job: ImportJob) -> dict:
    """Единый формат задания импорта — общий с роутером `import_jobs`.

    Дискриминирован по владельцу (спека контура §2.13): договорной job
    сохраняет `estimate_id` (смета ТЕКУЩЕЙ пары), раундовый несёт
    `estimate_ids` — все сметы, созданные ИМ.
    """
    base = {
        "id": job.id,
        "filename": job.filename,
        "file_sha256": job.file_sha256,
        "status": job.status,
        "error_text": job.error_text,
        "warnings": job.warnings,
        "counters": {
            "positions_total": job.positions_total,
            "matched_cache": job.matched_cache,
            "matched_exact": job.matched_exact,
            "matched_nonposition": job.matched_nonposition,
            "to_review": job.to_review,
        },
        "estimates_created": job.estimates_created,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }
    if job.contract_id is not None:
        estimate = find_estimate(db, job.contract_id, job.amendment_no)
        return {
            **base, "owner_type": "contract",
            "contract_id": job.contract_id, "amendment_no": job.amendment_no,
            # Смета текущей пары, а не «смета этого job» (прежнее правило).
            "estimate_id": estimate.id if estimate else None,
        }
    rnd = db.get(TenderRound, job.round_id)
    estimate_ids = list(db.execute(
        select(Estimate.id).where(Estimate.import_job_id == job.id).order_by(Estimate.id)
    ).scalars())
    return {
        **base, "owner_type": "round",
        "tender_id": rnd.tender_id if rnd else None, "round_id": job.round_id,
        "estimate_ids": estimate_ids,
    }
```

Импорты `Estimate`, `TenderRound`, `select`.

- [ ] **Step 4: роутер тендеров**

`backend/routers/tenders.py`:

```python
"""Тендерный контур: тендеры, раунды, участники, загрузка сводной таблицы
раунда (спека 2026-08-26-tenders-contour-design.md §2.13).

Права: чтение и upload — member; создание/правка/удаление тендера и раунда,
удаление участника, replace — admin. Раунд в URL всегда ищется парой
(round_id, tender_id) — чужой тендер даёт 404.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import get_current_user, require_admin
from config import settings
from crud import tenders as crud_tenders
from crud.common import DomainError
from database import get_db, get_session_factory
from models import ImportJob, ImportJobStatus, User, UserRole
from responses import decimal_json
from routers.domain_errors import raise_domain_error
from routers.estimates import _read_within_limit, _validate_xlsx, job_response
from services.import_pipeline import run_import_job
from services.maintenance import purge_files_best_effort
from storage import Storage, get_storage

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/tenders", tags=["tenders"])

ACTIVE_ROUND_INDEX = "uq_import_jobs_active_round"


class TenderCreate(BaseModel):
    object_id: int
    title: str
    tender_number: str
    rate_class_id: int | None = None
    notes: str | None = None


class TenderUpdate(BaseModel):
    title: str | None = None
    notes: str | None = None


class RoundCreate(BaseModel):
    stage_no: int
    label: str | None = None
    held_on: dt.date | None = None


class RoundUpdate(BaseModel):
    label: str | None = None
    held_on: dt.date | None = None


@router.get("")
def list_tenders(q: str | None = Query(default=None), page: int = Query(default=1, ge=1),
                 page_size: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)):
    return crud_tenders.list_tenders(db, q=q, page=page, page_size=page_size)


@router.get("/{tender_id}")
def get_tender(tender_id: int, db: Session = Depends(get_db)):
    try:
        return decimal_json(crud_tenders.get_tender_card(db, tender_id))
    except DomainError as e:
        raise_domain_error(e)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_tender(body: TenderCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    try:
        card = crud_tenders.create_tender(db, object_id=body.object_id, title=body.title,
                                          tender_number=body.tender_number, rate_class_id=body.rate_class_id,
                                          notes=body.notes)
    except DomainError as e:
        raise_domain_error(e)
    return decimal_json(card, status.HTTP_201_CREATED)


@router.patch("/{tender_id}")
def update_tender(tender_id: int, body: TenderUpdate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    fields = body.model_dump(exclude_unset=True)
    try:
        return decimal_json(crud_tenders.update_tender(db, tender_id, **fields))
    except DomainError as e:
        raise_domain_error(e)


@router.delete("/{tender_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tender(tender_id: int, db: Session = Depends(get_db), storage: Storage = Depends(get_storage),
                  _: User = Depends(require_admin)):
    try:
        file_keys = crud_tenders.delete_tender(db, tender_id)
    except DomainError as e:
        raise_domain_error(e)
    purge_files_best_effort(storage, file_keys, context=f"Удаление тендера {tender_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{tender_id}/rounds", status_code=status.HTTP_201_CREATED)
def create_round(tender_id: int, body: RoundCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    try:
        card = crud_tenders.create_round(db, tender_id, stage_no=body.stage_no, label=body.label, held_on=body.held_on)
    except DomainError as e:
        raise_domain_error(e)
    return decimal_json(card, status.HTTP_201_CREATED)


@router.patch("/{tender_id}/rounds/{round_id}")
def update_round(tender_id: int, round_id: int, body: RoundUpdate, db: Session = Depends(get_db),
                 _: User = Depends(require_admin)):
    fields = body.model_dump(exclude_unset=True)
    try:
        return decimal_json(crud_tenders.update_round(db, tender_id, round_id, **fields))
    except DomainError as e:
        raise_domain_error(e)


@router.delete("/{tender_id}/rounds/{round_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_round(tender_id: int, round_id: int, db: Session = Depends(get_db),
                 storage: Storage = Depends(get_storage), _: User = Depends(require_admin)):
    try:
        file_keys = crud_tenders.delete_round(db, tender_id, round_id)
    except DomainError as e:
        raise_domain_error(e)
    purge_files_best_effort(storage, file_keys, context=f"Удаление раунда {round_id} тендера {tender_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{tender_id}/rounds/{round_id}/import-jobs")
def list_round_import_jobs(tender_id: int, round_id: int, db: Session = Depends(get_db)):
    try:
        return crud_tenders.list_round_import_jobs(db, tender_id, round_id)
    except DomainError as e:
        raise_domain_error(e)


@router.delete("/{tender_id}/participants/{package_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_participant(tender_id: int, package_id: int, confirmation_token: str | None = Query(default=None),
                       db: Session = Depends(get_db), _: User = Depends(require_admin)):
    try:
        crud_tenders.delete_participant(db, tender_id, package_id, confirmation_token=confirmation_token)
    except DomainError as e:
        raise_domain_error(e)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{tender_id}/rounds/{round_id}/upload", status_code=status.HTTP_202_ACCEPTED)
def upload_round(
    tender_id: int, round_id: int, background: BackgroundTasks, response: Response,
    file: UploadFile = File(...), replace: bool = Form(default=False),
    db: Session = Depends(get_db), storage: Storage = Depends(get_storage),
    session_factory=Depends(get_session_factory), current_user: User = Depends(get_current_user),
):
    """Сводная таблица раунда → один job → N смет + baseline (спека §2.5).

    Правила повторной загрузки — те же три, что у договора (§5), с раундом
    вместо пары: 200 — текущий job (§2.12) с тем же sha256; 409 — сметы есть,
    файл другой, replace не передан; replace — admin.
    """
    if replace and current_user.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Замена загруженного раунда доступна только администратору.")
    try:
        rnd = crud_tenders.get_round(db, tender_id, round_id)
    except DomainError as e:
        raise_domain_error(e)

    payload = _read_within_limit(file, settings.UPLOAD_MAX_SIZE_MB * 1024 * 1024)
    if not payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Файл пустой.")
    _validate_xlsx(file, payload)
    file_sha256 = hashlib.sha256(payload).hexdigest()

    running = crud_tenders.active_round_job(db, rnd.id)
    if running is not None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Импорт этого раунда уже выполняется (задание {running.id}, статус «{running.status}»).")

    current = crud_tenders.current_round_job(db, rnd.id)
    if current is not None and current.file_sha256 == file_sha256:
        response.status_code = status.HTTP_200_OK
        return job_response(db, current)
    if crud_tenders.round_estimate_ids(db, rnd.id) and not replace:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Раунд уже загружен; для замены всех его смет повторите запрос с replace=true.")

    file_key = storage.save(payload)
    try:
        job = ImportJob(round_id=rnd.id, filename=file.filename or "round.xlsx", file_key=file_key,
                        file_sha256=file_sha256, status=ImportJobStatus.pending.value)
        db.add(job)
        db.commit()
        db.refresh(job)
    except IntegrityError as exc:
        db.rollback()
        storage.delete(file_key)
        if ACTIVE_ROUND_INDEX in str(exc.orig):
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "Импорт этого раунда уже запущен параллельным запросом.") from exc
        raise
    except Exception:
        db.rollback()
        storage.delete(file_key)
        raise

    background.add_task(run_import_job, job.id, session_factory=session_factory, storage=storage, replace=replace)
    return job_response(db, job)
```

- [ ] **Step 5: регистрация и текст скачивания**

`main.py`, после `import_jobs_router`:

```python
# Тендерный контур (спека 2026-08-26): чтение и upload — member, изменение — admin.
app.include_router(tenders_router.router, dependencies=_auth_dep)
```

`routers/import_jobs.py` — в `download_import_job_file` текст 410:

```python
            "Исходный файл задания больше не хранится: он удалён по ретенции. "
            "Сама запись задания остаётся, пока жив её владелец — договор или раунд тендера.",
```

и в докстроке «пока жив договор» → «пока жив владелец — договор либо раунд».

- [ ] **Step 6: зелёное**

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_tenders_api.py tests/integration/test_estimates_api.py tests/integration/test_contracts_api.py -q`
Expected: PASS все; `test_estimates_api.py` — без правок утверждений.

- [ ] **Step 7: ruff, полный набор, Commit**

Run: `cd backend && uv run ruff check .`; `TEST_DATABASE_URL=... uv run pytest -n 8 -q`.

```bash
git add backend/routers/tenders.py backend/routers/estimates.py backend/routers/import_jobs.py \
  backend/main.py backend/crud/tenders.py backend/tests/integration/test_tenders_api.py
git commit -m "feat(tenders-contour): API — тендеры, раунды, загрузка сводной таблицы, token удаления участника, owner_type у job"
```

---

## Task 9: Гонки, дедлок, recovery раундового лока

Спека §2.11 (иерархия), §6 (гонки), DoD 10. Двухсессионные тесты с
реальным опережением; снятия защиты — контроллер.

**Files:**
- Test: `backend/tests/integration/test_tenders_concurrency.py`
- Test: `backend/tests/integration/test_maintenance.py` (один тест)

**Interfaces:**
- Consumes: `crud.tenders.delete_tender/delete_round/delete_participant/create_round`, `import_round`, `recover_interrupted_jobs`, `committing_session_factory`, `tmp_storage`.

- [ ] **Step 1: тесты**

`backend/tests/integration/test_tenders_concurrency.py`:

```python
"""Гонки тендерного контура (спека §2.11, §6): единая иерархия локов
tender → rounds → package у импорта и всех удалений.

Приём — из test_contract_cascade_delete.py и test_category_override_concurrency.py:
два потока, настоящий замок, ожидание блокировки по pg_stat_activity, а не
пауза «на глазок». Каждый тест называет снятие, которое его красит.
"""
from __future__ import annotations

import threading
import time

import pytest
import sqlalchemy as sa

from crud import tenders as crud_tenders
from crud.common import DomainError
from models import ImportJob, ImportJobStatus, Offer, TenderRound
from services.category_resolution import CategoryResolver
from services.round_import import import_round
from services.unit_resolution import UnitResolver
from tests.payloads import position, proposal, round_payload

pytestmark = pytest.mark.integration


def _wait_until_a_backend_blocks(session_factory, *, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    with session_factory() as probe:
        while time.monotonic() < deadline:
            blocked = probe.execute(sa.text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )).scalar_one()
            probe.rollback()
            if blocked:
                return True
            time.sleep(0.05)
    return False


P_A = lambda: proposal([position(job_title="Работа", unit="м2", unit_cost_total="10", total_cost_total="10")], title="ООО А", inn="7700000001")


@pytest.fixture
def grid(committing_db, committing_factories, committing_session_factory):
    tender = committing_factories.TenderFactory.create()
    rnd = committing_factories.TenderRoundFactory.create(tender=tender, stage_no=1)
    committing_db.flush()
    import_round(committing_db, tender_round=rnd, data=round_payload([P_A()]), parser_version="4.0.0",
                 import_job_id=None, replace=False, unit_resolver=UnitResolver(committing_db),
                 category_resolver=CategoryResolver.from_db(committing_db))
    # Второй раунд — ПУСТОЙ: в нём у участника ещё нет Offer, поэтому импорт в
    # тесте дедлока делает настоящий INSERT (FK берёт KEY SHARE на пакете), а не
    # ON CONFLICT DO NOTHING, который на занятой паре не взял бы ничего.
    empty_round = committing_factories.TenderRoundFactory.create(tender=tender, stage_no=2)
    committing_db.commit()
    package_id = committing_db.execute(sa.select(Offer.package_id).where(Offer.round_id == rnd.id)).scalar_one()

    class G:
        tender_id = tender.id
        round_id = rnd.id
        empty_round_id = empty_round.id
        package = package_id
        session_factory = committing_session_factory

    return G()


def _hold_upload(session_factory, round_id, inserted, may_commit, committed):
    """Поток загрузки: вставил round-job (FOR KEY SHARE на раунде по FK), держит транзакцию."""
    session = session_factory()
    try:
        session.add(ImportJob(round_id=round_id, filename="race.xlsx", file_key="race-" + str(round_id),
                              file_sha256="1" * 64, status=ImportJobStatus.pending.value))
        session.flush()
        inserted.set()
        may_commit.wait(timeout=10)
        session.commit()
        committed.set()
    finally:
        session.close()


@pytest.mark.parametrize("command", ["round", "tender", "participant"])
def test_upload_wins_the_race_and_deletion_answers_active_import(grid, command):
    """Загрузка успела первой (job вставлен, не закоммичен) — удаление ждёт и
    отказывает `active_import`, а не сносит данные из-под живого импорта.
    Снятие любого лока раундов у команды красит тест: без него проверка
    активного job не увидит незакоммиченную вставку."""
    inserted, may_commit, committed = threading.Event(), threading.Event(), threading.Event()
    outcome: dict = {}
    uploader = threading.Thread(target=_hold_upload, args=(grid.session_factory, grid.round_id, inserted, may_commit, committed), daemon=True)
    uploader.start()
    assert inserted.wait(timeout=10)

    def deleter():
        session = grid.session_factory()
        try:
            if command == "round":
                crud_tenders.delete_round(session, grid.tender_id, grid.round_id)
            elif command == "tender":
                crud_tenders.delete_tender(session, grid.tender_id)
            else:
                crud_tenders.delete_participant(session, grid.tender_id, grid.package, confirmation_token="x")
            outcome["result"] = "deleted"
        except DomainError as e:
            outcome["result"] = e.code or str(e.status_code)
        finally:
            session.close()

    deleting = threading.Thread(target=deleter, daemon=True)
    deleting.start()
    assert _wait_until_a_backend_blocks(grid.session_factory), "удаление не встало на замок — сериализации нет"
    assert deleting.is_alive(), "удаление вернулось до коммита загрузки — замка нет"
    may_commit.set()
    assert committed.wait(timeout=10)
    deleting.join(timeout=15)
    assert outcome["result"] == "active_import"


def test_participant_deletion_blocks_a_concurrent_round_creation(grid):
    """FOR UPDATE тендера у удаления участника задерживает вставку раунда до конца
    команды (§2.11). Снятие лока тендера — тест красный: раунд вставится сразу."""
    entered, may_finish = threading.Event(), threading.Event()
    order: list[str] = []

    def deleter():
        session = grid.session_factory()
        try:
            @sa.event.listens_for(session.connection(), "after_cursor_execute")
            def pause_after_tender_lock(conn, cursor, statement, parameters, context, executemany):
                if "FOR UPDATE" in statement.upper() and "tenders" in statement and not entered.is_set():
                    entered.set()
                    may_finish.wait(timeout=10)
            preview = crud_tenders.participant_deletion_preview(session, grid.tender_id, grid.package)
            crud_tenders.delete_participant(session, grid.tender_id, grid.package, confirmation_token=preview["confirmation_token"])
            order.append("deleted")
        finally:
            session.close()

    t = threading.Thread(target=deleter, daemon=True)
    t.start()
    assert entered.wait(timeout=10)

    def creator():
        session = grid.session_factory()
        try:
            # stage_no=3: этапы 1 и 2 у фикстуры grid уже заняты (второй — пустой
            # раунд для теста дедлока); занятый номер дал бы 409 вместо гонки.
            crud_tenders.create_round(session, grid.tender_id, stage_no=3, label=None, held_on=None)
            order.append("created")
        finally:
            session.close()

    c = threading.Thread(target=creator, daemon=True)
    c.start()
    assert _wait_until_a_backend_blocks(grid.session_factory), "создание раунда не ждёт удаление участника"
    may_finish.set()
    t.join(timeout=15)
    c.join(timeout=15)
    assert order == ["deleted", "created"]


def test_import_and_participant_deletion_do_not_deadlock(grid):
    """Обе команды берут tender → rounds → package в одном порядке (§2.11), и
    доказательство ДЕТЕРМИНИРОВАННОЕ — событиями, не «повезло за три прогона».

    Импорт воспроизводится его ТОЧНОЙ последовательностью локов сырым SQL
    (tender FOR KEY SHARE → round FOR UPDATE → INSERT offers, который берёт
    FOR KEY SHARE на пакете по FK); удаление участника — настоящим
    `delete_participant`. Импорт идёт в ПУСТОЙ второй раунд: там у участника
    Offer ещё нет, и INSERT настоящий — на занятой паре `ON CONFLICT DO NOTHING`
    не вставил бы строку, FK не проверялась бы, и ключевой захват пакета не
    состоялся бы вовсе (находка ревью гейта 3). Сценарий:

    1. импорт держит tender KEY SHARE и empty_round FOR UPDATE;
    2. удаление стартует и — по иерархии — упирается в tender FOR UPDATE;
    3. импорт вставляет offer (KEY SHARE на пакете) и коммитит;
    4. удаление просыпается, локирует раунды и пакет, доходит до конца.

    Снятие защиты — переставить в `delete_participant` лок пакета ПЕРЕД
    `_lock_tender`/`_lock_rounds`: тогда на шаге 2 удаление возьмёт пакет сразу
    (событие `package_locked`), импорт на шаге 3 встанет на пакете, удаление
    на раундах — и PostgreSQL убьёт одного `DeadlockDetected`. Тест обязан
    покраснеть по `errors`, а не по таймауту.
    """
    errors: list[str] = []
    import_locked = threading.Event()
    package_locked = threading.Event()
    import_done = threading.Event()
    delete_done = threading.Event()

    def importer():
        session = grid.session_factory()
        try:
            with session.begin():
                session.execute(sa.text("SELECT id FROM tenders WHERE id = :t FOR KEY SHARE"), {"t": grid.tender_id})
                session.execute(sa.text("SELECT id FROM tender_rounds WHERE id = :r FOR UPDATE"), {"r": grid.empty_round_id})
                import_locked.set()
                # Ждём, пока удаление либо встало на замок тендера (правильная
                # иерархия), либо успело взять пакет (снятая защита).
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and not package_locked.is_set():
                    if _wait_until_a_backend_blocks(grid.session_factory, timeout=0.2):
                        break
                # Настоящий INSERT, без ON CONFLICT: занятая пара обязана уронить
                # тест, а не тихо отменить захват пакета.
                inserted = session.execute(sa.text(
                    "INSERT INTO offers (tender_id, round_id, package_id) VALUES (:t, :r, :p) RETURNING id"
                ), {"t": grid.tender_id, "r": grid.empty_round_id, "p": grid.package}).scalar_one()
                assert inserted is not None
        except Exception as e:  # noqa: BLE001
            errors.append(f"import: {type(e).__name__}: {e}")
        finally:
            session.close()
            import_done.set()

    def deleter():
        session = grid.session_factory()
        try:
            @sa.event.listens_for(session.connection(), "after_cursor_execute")
            def note_package_lock(conn, cursor, statement, parameters, context, executemany):
                if "FOR UPDATE" in statement.upper() and "offer_packages" in statement:
                    package_locked.set()

            assert import_locked.wait(timeout=10)
            preview = crud_tenders.participant_deletion_preview(session, grid.tender_id, grid.package)
            try:
                crud_tenders.delete_participant(session, grid.tender_id, grid.package,
                                                confirmation_token=preview["confirmation_token"])
            except DomainError:
                pass  # устаревший token / active_import — законные исходы; ошибка БД — нет
        except Exception as e:  # noqa: BLE001
            errors.append(f"delete: {type(e).__name__}: {e}")
        finally:
            session.close()
            delete_done.set()

    threads = [threading.Thread(target=importer, daemon=True), threading.Thread(target=deleter, daemon=True)]
    for th in threads:
        th.start()
    assert import_done.wait(timeout=30), "импорт не завершился — потоки зависли"
    assert delete_done.wait(timeout=30), "удаление не завершилось — потоки зависли"
    assert errors == []
    # Контроль корпуса: захват пакета состоялся — offer во втором раунде есть.
    with grid.session_factory() as check:
        assert check.execute(
            sa.select(sa.func.count()).select_from(Offer).where(Offer.round_id == grid.empty_round_id)
        ).scalar_one() == 1
```

В `test_maintenance.py`:

```python
    def test_recovery_frees_the_active_round_lock(self, db_session, factories):
        """Новый частичный индекс — вторая точка, где неполное обобщение даёт
        вечный 409 (спека §5 контура)."""
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        factories.ImportJobFactory.create(contract=None, round_id=rnd.id, status=ImportJobStatus.parsing.value, file_key="k-hang")
        db_session.flush()

        assert recover_interrupted_jobs(db_session) == 1

        factories.ImportJobFactory.create(contract=None, round_id=rnd.id, status=ImportJobStatus.pending.value, file_key="k-again")
        db_session.flush()  # без recovery здесь был бы IntegrityError по uq_import_jobs_active_round
```

- [ ] **Step 2: прогон**

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_tenders_concurrency.py tests/integration/test_maintenance.py -v`
Expected: PASS все.

- [ ] **Step 3: снятия защиты (контроллер)**

1. В `delete_participant` убрать `_lock_rounds(...)` — ожидается: тест
   `test_upload_wins_the_race...[participant]` красный (удаление не ждёт,
   исход `deleted`). Вернуть.
2. В `delete_participant` заменить `exclusive=True` на `exclusive=False` у
   `_lock_tender` — ожидается: `test_participant_deletion_blocks_a_concurrent_round_creation`
   красный (`order == ["created", "deleted"]` либо таймаут ожидания замка). Вернуть.
3. В `delete_participant` переставить лок пакета ПЕРЕД `_lock_tender` и
   `_lock_rounds` — ожидается: `test_import_and_participant_deletion_do_not_deadlock`
   красный **детерминированно**, по `errors` с `DeadlockDetected` у одного из
   потоков (сценарий тестом навязан событиями, а не гонкой). Красный по
   таймауту — не пройдено: значит, тест не воспроизводит перекрёстный захват.
   Вернуть.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/integration/test_tenders_concurrency.py backend/tests/integration/test_maintenance.py
git commit -m "test(tenders-contour): гонки upload ↔ delete, удаление участника ↔ создание раунда, дедлок, recovery раундового лока"
```

---

## Task 10: Фронтенд — типы, API, ключи, хуки, msw

Спека §2.13, §2.14. Слой данных без экранов: всё, что экраны задач 11–12
импортируют, появляется здесь и покрывается фикстурами msw.

**Files:**
- Modify: `frontend/src/types/domain.ts`
- Modify: `frontend/src/services/api/domain.ts`
- Modify: `frontend/src/services/queryKeys.ts`
- Modify: `frontend/src/services/queries.ts`
- Modify: `frontend/src/test/fixtures.ts`, `frontend/src/test/handlers.ts`
- Test: `frontend/src/services/queries.tenders.test.tsx`

**Interfaces:**
- Produces (типы): `TenderRow`, `TenderCard`, `TenderRoundRow`, `TenderParticipant`, `TenderCell`, `TenderInput`, `RoundInput`, `RoundImportJob`, `ParticipantDeletionPreview`; `ImportJob` получает `owner_type: "contract" | "round"`, `estimates_created: number | null`, опциональные `tender_id`, `round_id`, `estimate_ids`; `UploadRoundInput`.
- Produces (API): `tendersApi.{list, get, create, update, remove, createRound, updateRound, removeRound, roundImportJobs, uploadRound, removeParticipant}`.
- Produces (ключи): `qk.tenders.{all, list(params), card(id), roundJobs(tenderId, roundId)}`.
- Produces (хуки): `useTenders`, `useTender`, `useCreateTender`, `useUpdateTender`, `useDeleteTender`, `useCreateRound`, `useUpdateRound`, `useDeleteRound`, `useRoundImportJobs`, `useUploadRound`, `useDeleteParticipant`; `useImportJob(jobId, ownerRef?)` — инвалидирует `qk.tenders.card(tenderId)` при `done` раундового job.

- [ ] **Step 1: типы**

В `frontend/src/types/domain.ts`, раздел «Задания импорта»: в `ImportJob`
заменить `contract_id: number; amendment_no: number | null;` на

```ts
  /** Владелец задания (спека контура §2.13): договор либо раунд тендера. */
  owner_type: "contract" | "round";
  contract_id?: number;
  amendment_no?: number | null;
  tender_id?: number;
  round_id?: number;
  /** Сметы, созданные ЭТИМ заданием — только у раунда. */
  estimate_ids?: number[];
  /** 1 у договора, N(+1) у раунда; null у заданий до 0015. */
  estimates_created: number | null;
```

и оставить `estimate_id?: number | null;` (опциональным). Новый раздел:

```ts
// ---------------------------------------------------------------------------
//  Тендерный контур (спека 2026-08-26-tenders-contour-design.md §2.13)
// ---------------------------------------------------------------------------

export interface TenderRow {
  id: number;
  tender_number: string;
  title: string;
  object_id: number;
  object_title: string;
  /** Снимок класса на момент торга (§4) — не класс объекта сейчас. */
  rate_class_id: number;
  rate_class_title: string;
  rounds_count: number;
  participants_count: number;
  created_at: string | null;
}

export interface TenderRoundRow {
  id: number;
  stage_no: number;
  label: string | null;
  held_on: string | null;
  latest_job: { id: number; status: ImportJobStatus; filename: string; finished_at: string | null; created_at: string | null } | null;
  /** Текущий done-job с полным набором смет (§2.12); null — файла нет или состав изменён. */
  current_job_id: number | null;
  baseline_estimate_id: number | null;
  baseline_total_including_vat: Decimal | null;
}

export interface TenderParticipant {
  package_id: number;
  contractor_id: number;
  title: string;
  inn: string;
}

/** Ячейка решётки. Три состояния данными, не выводом клиента (§2.13):
 *  offer_id null — не участвовал; offer_id есть, estimate_id null — предложение
 *  было, сметы сейчас нет; оба есть — смета загружена. */
export interface TenderCell {
  round_id: number;
  package_id: number;
  offer_id: number | null;
  estimate_id: number | null;
  total_including_vat: Decimal | null;
}

export interface TenderCard extends Omit<TenderRow, "rounds_count" | "participants_count"> {
  notes: string | null;
  object_address: string | null;
  rounds: TenderRoundRow[];
  participants: TenderParticipant[];
  cells: TenderCell[];
}

export interface TenderInput {
  object_id: number;
  title: string;
  tender_number: string;
  rate_class_id?: number | null;
  notes?: string | null;
}

export interface RoundInput {
  stage_no: number;
  label?: string | null;
  held_on?: string | null;
}

export interface RoundImportJob extends ImportJob {
  is_current: boolean;
}

export interface UploadRoundInput {
  file: File;
  tender_id: number;
  round_id: number;
  replace?: boolean;
}

export interface ParticipantDeletionPreview {
  code: "confirmation_required";
  message: string;
  rounds_count: number;
  estimates_count: number;
  positions_count: number;
  overrides_count: number;
  confirmation_token: string;
}
```

Все места, где TypeScript теперь жалуется на `job.contract_id` как возможно
`undefined` (`useUploadEstimate.onSuccess`, `EstimateUploadPanel`), — сузить
проверкой `if (job.owner_type === "contract")`.

- [ ] **Step 2: API**

В `services/api/domain.ts`:

```ts
export const tendersApi = {
  list: (params?: { q?: string; page?: number; page_size?: number }): Promise<Paginated<TenderRow>> =>
    api.get<Paginated<TenderRow>>("/v1/tenders", { params }).then((r) => r.data),
  get: (id: number): Promise<TenderCard> =>
    api.get<TenderCard>(`/v1/tenders/${id}`).then((r) => r.data),
  create: (input: TenderInput): Promise<TenderCard> =>
    api.post<TenderCard>("/v1/tenders", input).then((r) => r.data),
  update: (id: number, input: Partial<Pick<TenderInput, "title" | "notes">>): Promise<TenderCard> =>
    api.patch<TenderCard>(`/v1/tenders/${id}`, input).then((r) => r.data),
  remove: (id: number): Promise<void> => api.delete(`/v1/tenders/${id}`).then(() => undefined),
  createRound: (tenderId: number, input: RoundInput): Promise<TenderCard> =>
    api.post<TenderCard>(`/v1/tenders/${tenderId}/rounds`, input).then((r) => r.data),
  updateRound: (tenderId: number, roundId: number, input: Partial<Omit<RoundInput, "stage_no">>): Promise<TenderCard> =>
    api.patch<TenderCard>(`/v1/tenders/${tenderId}/rounds/${roundId}`, input).then((r) => r.data),
  removeRound: (tenderId: number, roundId: number): Promise<void> =>
    api.delete(`/v1/tenders/${tenderId}/rounds/${roundId}`).then(() => undefined),
  roundImportJobs: (tenderId: number, roundId: number): Promise<RoundImportJob[]> =>
    api.get<RoundImportJob[]>(`/v1/tenders/${tenderId}/rounds/${roundId}/import-jobs`).then((r) => r.data),
  uploadRound: ({ file, tender_id, round_id, replace }: UploadRoundInput): Promise<ImportJob> => {
    const form = new FormData();
    form.append("file", file);
    if (replace) form.append("replace", "true");
    return api.post<ImportJob>(`/v1/tenders/${tender_id}/rounds/${round_id}/upload`, form).then((r) => r.data);
  },
  /** Без token — сервер отвечает 409 `confirmation_required` с preview; с token — 204. */
  removeParticipant: (tenderId: number, packageId: number, confirmationToken?: string): Promise<void> =>
    api.delete(`/v1/tenders/${tenderId}/participants/${packageId}`, {
      params: confirmationToken ? { confirmation_token: confirmationToken } : undefined,
    }).then(() => undefined),
};
```

- [ ] **Step 3: ключи и хуки**

`queryKeys.ts`:

```ts
  tenders: {
    all: ["tenders"] as const,
    list: (params?: { q?: string; page?: number; page_size?: number }) => ["tenders", "list", params ?? {}] as const,
    card: (id: number) => ["tenders", "card", id] as const,
    roundJobs: (tenderId: number, roundId: number) => ["tenders", "round-jobs", tenderId, roundId] as const,
  },
```

`queries.ts` — новый раздел «Тендеры (§7.8)»:

```ts
export function useTenders(params?: { q?: string; page?: number; page_size?: number }) {
  return useQuery({ queryKey: qk.tenders.list(params), queryFn: () => tendersApi.list(params) });
}

export function useTender(id: number | undefined) {
  return useQuery({ queryKey: qk.tenders.card(id ?? 0), queryFn: () => tendersApi.get(id as number), enabled: id !== undefined });
}

export function useRoundImportJobs(tenderId: number | undefined, roundId: number | undefined) {
  return useQuery({
    queryKey: qk.tenders.roundJobs(tenderId ?? 0, roundId ?? 0),
    queryFn: () => tendersApi.roundImportJobs(tenderId as number, roundId as number),
    enabled: tenderId !== undefined && roundId !== undefined,
  });
}

function invalidateTender(qc: ReturnType<typeof useQueryClient>, tenderId: number) {
  qc.invalidateQueries({ queryKey: qk.tenders.card(tenderId) });
  qc.invalidateQueries({ queryKey: qk.tenders.all });
}

export function useCreateTender() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: tendersApi.create,
    onSuccess: () => { qc.invalidateQueries({ queryKey: qk.tenders.all }); toast.success("Тендер создан"); },
    onError: toastApiError,
  });
}

export function useUpdateTender() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: number; input: Partial<Pick<TenderInput, "title" | "notes">> }) => tendersApi.update(id, input),
    onSuccess: (card) => invalidateTender(qc, card.id),
    onError: toastApiError,
  });
}

export function useDeleteTender() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => tendersApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.tenders.all });
      qc.invalidateQueries({ queryKey: qk.importJobs.all });
      qc.invalidateQueries({ queryKey: qk.review.all });
      qc.invalidateQueries({ queryKey: qk.contractors.all });
      toast.success("Тендер удалён");
    },
    onError: toastApiError,
  });
}

export function useCreateRound() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ tenderId, input }: { tenderId: number; input: RoundInput }) => tendersApi.createRound(tenderId, input),
    onSuccess: (card) => invalidateTender(qc, card.id),
    onError: toastApiError,
  });
}

export function useUpdateRound() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ tenderId, roundId, input }: { tenderId: number; roundId: number; input: Partial<Omit<RoundInput, "stage_no">> }) =>
      tendersApi.updateRound(tenderId, roundId, input),
    onSuccess: (card) => invalidateTender(qc, card.id),
    onError: toastApiError,
  });
}

export function useDeleteRound() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ tenderId, roundId }: { tenderId: number; roundId: number }) => tendersApi.removeRound(tenderId, roundId),
    onSuccess: (_, { tenderId }) => {
      invalidateTender(qc, tenderId);
      qc.invalidateQueries({ queryKey: qk.importJobs.all });
      qc.invalidateQueries({ queryKey: qk.review.all });
      toast.success("Раунд удалён");
    },
    onError: toastApiError,
  });
}

/** Без общего тоста: 409 — развилка «нужна замена раунда», её ведёт компонент. */
export function useUploadRound() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: tendersApi.uploadRound,
    onSuccess: (job) => { if (job.tender_id !== undefined) invalidateTender(qc, job.tender_id); },
  });
}

/**
 * Удаление участника — протокол token (спека §2.11). Без тоста на ошибку:
 * 409 `confirmation_required` — не сбой, а preview, его показывает диалог.
 */
export function useDeleteParticipant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ tenderId, packageId, confirmationToken }: { tenderId: number; packageId: number; confirmationToken?: string }) =>
      tendersApi.removeParticipant(tenderId, packageId, confirmationToken),
    onSuccess: (_, { tenderId }) => {
      invalidateTender(qc, tenderId);
      qc.invalidateQueries({ queryKey: qk.review.all });
      toast.success("Участник удалён; исходные файлы и результаты разбора сохранены");
    },
  });
}
```

`useImportJob(jobId, contractId?)` → `useImportJob(jobId, ownerRef?: { contractId?: number; tenderId?: number })`:
при `done` и `ownerRef.contractId` — прежние инвалидации; при `ownerRef.tenderId` —
`qc.invalidateQueries({ queryKey: qk.tenders.card(ownerRef.tenderId) })` плюс
`qk.review.all`. Единственный существующий вызов в `EstimateUploadPanel`
переписать на `useImportJob(jobId, { contractId })`.

- [ ] **Step 4: фикстуры и хендлеры msw**

`fixtures.ts`:

```ts
export const sampleTenders: TenderRow[] = [
  { id: 300, tender_number: "Т-2026-001", title: "Генподряд на строительство", object_id: 10, object_title: "ЖК Северный",
    rate_class_id: 1, rate_class_title: "Жилые дома", rounds_count: 2, participants_count: 2, created_at: "2026-08-01T08:00:00Z" },
];

export const sampleTenderCard: TenderCard = {
  ...sampleTenders[0],
  notes: null,
  object_address: "ул. Северная, 1",
  rounds: [
    { id: 3001, stage_no: 1, label: "Первичные предложения", held_on: "2026-06-01",
      latest_job: { id: 9101, status: "done", filename: "r1.xlsx", finished_at: "2026-06-02T10:00:00Z", created_at: "2026-06-02T09:00:00Z" },
      current_job_id: 9101, baseline_estimate_id: null, baseline_total_including_vat: null },
    { id: 3002, stage_no: 2, label: null, held_on: null,
      latest_job: null, current_job_id: null, baseline_estimate_id: null, baseline_total_including_vat: null },
  ],
  participants: [
    { package_id: 501, contractor_id: 20, title: "ООО Альфа", inn: "7700000001" },
    { package_id: 502, contractor_id: 21, title: "ООО Бета", inn: "7700000002" },
  ],
  cells: [
    { round_id: 3001, package_id: 501, offer_id: 7001, estimate_id: 8001, total_including_vat: "1200.00" },
    { round_id: 3001, package_id: 502, offer_id: null, estimate_id: null, total_including_vat: null },
    { round_id: 3002, package_id: 501, offer_id: null, estimate_id: null, total_including_vat: null },
    { round_id: 3002, package_id: 502, offer_id: 7002, estimate_id: null, total_including_vat: null },
  ],
};
```

`handlers.ts` — в `HandlerState` добавить
`tenderRoundState: "loaded" | "loaded-no-baseline" | "empty" | "changed"` (по
умолчанию `"loaded"`), `participantDeleteOutcome: "preview" | "stale" | "deleted" | "active"`
(по умолчанию `"preview"`), `lastRoundUploadReplace: boolean`; сбросить в
`resetHandlerState`. Хендлеры:

```ts
  http.get("/api/v1/tenders", ({ request }) => {
    const q = (new URL(request.url).searchParams.get("q") ?? "").toLowerCase();
    const items = q ? sampleTenders.filter((t) => `${t.tender_number} ${t.title} ${t.object_title}`.toLowerCase().includes(q)) : sampleTenders;
    return HttpResponse.json(page(items));
  }),
  http.get("/api/v1/tenders/:id", ({ params }) => {
    if (Number(params.id) !== sampleTenderCard.id) return HttpResponse.json({ detail: "Тендер не найден." }, { status: 404 });
    return HttpResponse.json(tenderCardFor(handlerState.tenderRoundState));
  }),
  http.post("/api/v1/tenders", async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ ...sampleTenderCard, id: 301, tender_number: body.tender_number }, { status: 201 });
  }),
  http.post("/api/v1/tenders/:id/rounds", () => HttpResponse.json(sampleTenderCard, { status: 201 })),
  http.delete("/api/v1/tenders/:id/rounds/:rid", () => new HttpResponse(null, { status: 204 })),
  http.post("/api/v1/tenders/:id/rounds/:rid/upload", async ({ request, params }) => {
    const body = await request.text();
    handlerState.lastRoundUploadReplace = /name="replace"[\s\S]*?\btrue\b/.test(body);
    if (handlerState.uploadOutcome === "conflict" && !handlerState.lastRoundUploadReplace) {
      return HttpResponse.json({ detail: "Раунд уже загружен; для замены всех его смет повторите запрос с replace=true." }, { status: 409 });
    }
    const job = { ...jobPayload(handlerState.jobStatuses[0] ?? "pending"), owner_type: "round", tender_id: Number(params.id),
                  round_id: Number(params.rid), estimate_ids: [8001, 8002], estimates_created: 2 };
    delete (job as { estimate_id?: unknown }).estimate_id;
    return HttpResponse.json(job, { status: handlerState.uploadOutcome === "idempotent" ? 200 : 202 });
  }),
  http.delete("/api/v1/tenders/:id/participants/:pid", ({ request }) => {
    const token = new URL(request.url).searchParams.get("confirmation_token");
    if (handlerState.participantDeleteOutcome === "active") {
      return HttpResponse.json({ detail: { code: "active_import", message: "Импорт раунда выполняется.", job_id: 9102 } }, { status: 409 });
    }
    if (!token || handlerState.participantDeleteOutcome === "stale") {
      return HttpResponse.json({ detail: { code: "confirmation_required", message: "Удаление участника требует подтверждения состава.",
        rounds_count: 2, estimates_count: 2, positions_count: 1830, overrides_count: 3, confirmation_token: token ? "fresh-token" : "token-1" } }, { status: 409 });
    }
    return new HttpResponse(null, { status: 204 });
  }),
```

`tenderCardFor(state)` — функция рядом с `jobPayload`: `"loaded"` — фикстура как
есть; `"loaded-no-baseline"` — то же, `rounds[0].baseline_estimate_id = null`;
`"empty"` — `rounds[0]` с `latest_job: null, current_job_id: null` и все ячейки
раунда 3001 с `offer_id: null`; `"changed"` — `current_job_id: null`, но ячейка
`(3001, 501)` со сметой. Для `"loaded"` baseline задать
`baseline_estimate_id: 8100, baseline_total_including_vat: "1150.00"` у раунда 3001.

- [ ] **Step 5: тест хуков**

`frontend/src/services/queries.tenders.test.tsx` — по образцу `queries.test.tsx`:
`useTender(300)` отдаёт карточку с четырьмя ячейками; `useDeleteParticipant` без
token отдаёт ошибку со статусом 409 и `apiErrorDetail`-объектом с
`code === "confirmation_required"`; `useUploadRound` с `replace: true` ставит
`handlerState.lastRoundUploadReplace`.

Run: `cd frontend && npx vitest run src/services/queries.tenders.test.tsx` — PASS.
Run: `cd frontend && npx tsc -b --noEmit` — clean (сужение `owner_type` в двух местах).
Run: `cd frontend && npm test` — PASS все прежние.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types/domain.ts frontend/src/services frontend/src/test frontend/src/components/contracts/EstimateUploadPanel.tsx
git commit -m "feat(tenders-contour): фронт — типы, API, ключи и хуки тендеров; owner_type у задания импорта"
```

---

## Task 11: `ImportJobPanel` — общая часть загрузки; `RoundUploadPanel`

Спека §2.14, план Р8. Общее — dropzone, состояние передачи, статус job с
поллингом, счётчики, предупреждения, отказ. Договорная оболочка сохраняет
`amendment_no` и диалог замены со счётом решений; раундовая — диалог замены
уровня раунда. Тесты `EstimateUploadPanel.test.tsx` — зелёные без правок.

**Files:**
- Create: `frontend/src/components/imports/ImportJobPanel.tsx`, `ImportJobPanel.test.tsx`
- Modify: `frontend/src/components/contracts/EstimateUploadPanel.tsx`
- Create: `frontend/src/components/tenders/RoundUploadPanel.tsx`, `RoundUploadPanel.test.tsx`

**Interfaces:**
- Produces: `ImportJobPanel` props — `{ job: ImportJob | undefined; uploading: boolean; idempotent: boolean; rejection: string | null; disabled: boolean; hint: string; onDrop: (files: File[]) => void; children?: ReactNode }` — рендерит dropzone, «Файл передаётся…», отказ (`data-testid="upload-rejection"`, `role="alert"`), блок job (имя файла, `StatusPill`, ожидание, идемпотентная подпись, `error_text`, счётчики при `done`, предупреждения). `children` — слот НАД dropzone (у договора — поле допсоглашения).
- Produces: `RoundUploadPanel` props — `{ tenderId: number; roundId: number; round: TenderRoundRow }`.
- Экспортирует из `ImportJobPanel.tsx`: `STATUS_LABEL`, `statusTone`, `isRunning`, `XLSX_ACCEPT`, `CounterCell` (перенесены из `EstimateUploadPanel`).

- [ ] **Step 1: вынести общую часть**

Создать `ImportJobPanel.tsx`, перенеся из `EstimateUploadPanel.tsx`
`XLSX_ACCEPT`, `STATUS_LABEL`, `statusTone`, `isRunning`, `CounterCell` и весь
JSX от `<Dropzone …>` до конца блока `{job && (...)}` включительно, в компонент
с пропсами выше. `EstimateUploadPanel` импортирует их и рендерит

```tsx
    <Surface className="grid gap-4">
      <ImportJobPanel
        job={job} uploading={upload.isPending} idempotent={idempotent} rejection={rejection}
        disabled={upload.isPending || (job !== undefined && isRunning(job.status))}
        hint="XLSX или XLSM, до 25 МБ" onDrop={handleDrop}
      >
        <div className="grid gap-2 sm:max-w-xs">
          <Label htmlFor="amendment-no">Номер допсоглашения</Label>
          <Input id="amendment-no" ... />
        </div>
      </ImportJobPanel>
      <AlertDialog ...>  {/* диалог замены — без изменений */}
    </Surface>
```

Run: `cd frontend && npx vitest run src/components/contracts/EstimateUploadPanel.test.tsx src/pages/contracts/ContractCardPage.test.tsx` — PASS **без правок тестов**.

- [ ] **Step 2: тест `ImportJobPanel`**

`ImportJobPanel.test.tsx`: рендер с `job` в статусе `done` показывает пять
счётчиков; `job.status === "error"` показывает `error_text` с `role="alert"`;
`rejection` показывает `data-testid="upload-rejection"`; `idempotent` показывает
подпись «уже был загружен»; `children` рендерится над dropzone.

Run: `cd frontend && npx vitest run src/components/imports/ImportJobPanel.test.tsx` — PASS.

- [ ] **Step 3: падающий тест `RoundUploadPanel`**

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { RoundUploadPanel } from "./RoundUploadPanel";
import { handlerState } from "@/test/handlers";
import { sampleTenderCard } from "@/test/fixtures";
import { renderWithProviders } from "@/test/utils";

async function dropXlsx(user: ReturnType<typeof userEvent.setup>) {
  const file = new File(["PKfake"], "сводная.xlsx", { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
  await user.upload(document.querySelector('input[type="file"]') as HTMLInputElement, file);
}

describe("RoundUploadPanel (спека §2.14)", () => {
  it("загружает раунд и показывает счётчики после done", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RoundUploadPanel tenderId={300} roundId={3001} round={sampleTenderCard.rounds[0]} />);
    await dropXlsx(user);
    expect(await screen.findByText("Готово")).toBeInTheDocument();
    expect(screen.getByText("Позиций")).toBeInTheDocument();
  });

  it("409 — развилка «заменить раунд целиком», только у admin", async () => {
    handlerState.uploadOutcome = "conflict";
    const user = userEvent.setup();
    renderWithProviders(<RoundUploadPanel tenderId={300} roundId={3001} round={sampleTenderCard.rounds[0]} />);
    await dropXlsx(user);
    expect(await screen.findByRole("alertdialog")).toHaveTextContent(/всех участников/);
    await user.click(screen.getByRole("button", { name: "Заменить раунд" }));
    await waitFor(() => expect(handlerState.lastRoundUploadReplace).toBe(true));
  });

  it("member на 409 видит отказ, а не диалог", async () => {
    handlerState.uploadOutcome = "conflict";
    const user = userEvent.setup();
    renderWithProviders(<RoundUploadPanel tenderId={300} roundId={3001} round={sampleTenderCard.rounds[0]} />,
      { initialUser: { id: 2, email: "m@example.com", role: "member" } });
    await dropXlsx(user);
    expect(await screen.findByTestId("upload-rejection")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("после перезагрузки показывает идущий импорт по latest_job, а не теряет его", async () => {
    handlerState.jobStatuses = ["parsing", "done"];
    const round = { ...sampleTenderCard.rounds[0], current_job_id: null,
      latest_job: { id: 9102, status: "parsing" as const, filename: "r1.xlsx", finished_at: null, created_at: "2026-06-02T09:00:00Z" } };
    renderWithProviders(<RoundUploadPanel tenderId={300} roundId={3001} round={round} />);
    expect(await screen.findByText("Разбор файла")).toBeInTheDocument();
  });

  it("после перезагрузки показывает ошибку последнего job", async () => {
    handlerState.jobStatuses = ["error"];
    const round = { ...sampleTenderCard.rounds[0], current_job_id: null,
      latest_job: { id: 9103, status: "error" as const, filename: "bad.xlsx", finished_at: "2026-06-02T10:00:00Z", created_at: "2026-06-02T09:00:00Z" } };
    renderWithProviders(<RoundUploadPanel tenderId={300} roundId={3001} round={round} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/Не удалось разобрать файл/);
  });

  it("смена раунда через key пересоздаёт панель и переключает поллинг на job НОВОГО раунда", async () => {
    // Раунд 1 — pending-job 9102, раунд 2 — error-job 9103. Без key панель
    // продолжала бы опрашивать 9102 (useState читает проп один раз).
    // Ответ — ПО ID задания, а не по счётчику опросов: иначе тест не отличил бы
    // «переключился на 9103» от «получил третий статус из очереди».
    const polled: number[] = [];
    server.use(
      http.get("/api/v1/import-jobs/:id", ({ params }) => {
        const id = Number(params.id);
        polled.push(id);
        if (id === 9102) return HttpResponse.json({ ...jobPayload("parsing"), id, owner_type: "round", filename: "r1.xlsx" });
        if (id === 9103) return HttpResponse.json({ ...jobPayload("error"), id, owner_type: "round", filename: "bad.xlsx" });
        return HttpResponse.json({ detail: "нет" }, { status: 404 });
      })
    );
    const round1 = { ...sampleTenderCard.rounds[0], current_job_id: null,
      latest_job: { id: 9102, status: "parsing" as const, filename: "r1.xlsx", finished_at: null, created_at: null } };
    const round2 = { ...sampleTenderCard.rounds[1], current_job_id: null,
      latest_job: { id: 9103, status: "error" as const, filename: "bad.xlsx", finished_at: null, created_at: null } };
    const { rerender } = renderWithProviders(
      <RoundUploadPanel key={round1.id} tenderId={300} roundId={round1.id} round={round1} />
    );
    expect(await screen.findByText("r1.xlsx")).toBeInTheDocument();
    expect(polled).toContain(9102);

    rerender(<RoundUploadPanel key={round2.id} tenderId={300} roundId={round2.id} round={round2} />);

    expect(await screen.findByText("bad.xlsx")).toBeInTheDocument();
    expect(screen.queryByText("r1.xlsx")).toBeNull();
    const switchedAt = polled.indexOf(9103);
    expect(switchedAt).toBeGreaterThan(-1);
    // после переключения 9102 больше не опрашивается
    expect(polled.slice(switchedAt)).not.toContain(9102);
  });
});
```

`rerender` возвращается из `renderWithProviders` (обёртка над `render` Testing
Library). `server` и `http`/`HttpResponse` — из `@/test/server` и `msw`;
`jobPayload` экспортировать из `handlers.ts` (сейчас он модульно-приватный —
добавить `export`).

- [ ] **Step 4: реализация `RoundUploadPanel`**

```tsx
import { useState } from "react";
import { Surface } from "@/components/ui-domain/Surface";
import { ImportJobPanel, isRunning } from "@/components/imports/ImportJobPanel";
import { Button } from "@/components/ui/button";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { useCurrentUser } from "@/hooks/useAuth";
import { apiErrorDetail, apiErrorStatus, useImportJob, useUploadRound } from "@/services/queries";
import type { TenderRoundRow } from "@/types/domain";

/**
 * Загрузка сводной таблицы раунда (спека §2.5, §2.14). Замена — ЦЕЛИКОМ,
 * всех участников и расчётной стоимости разом (§2.6), поэтому диалог говорит
 * именно это; замена — право admin.
 */
export function RoundUploadPanel({ tenderId, roundId, round }: { tenderId: number; roundId: number; round: TenderRoundRow }) {
  const { data: user } = useCurrentUser();
  const isAdmin = user?.role === "admin";
  // Поллинг стартует с ПОСЛЕДНЕГО job раунда, а не с текущего: current_job_id
  // есть только у полного done-набора (§2.12), и после перезагрузки страницы
  // идущий импорт или его ошибка исчезли бы с панели. latest_job — то, что
  // человек видел бы, не перезагружая.
  //
  // useState читает проп ОДИН раз, при монтировании. Смена раунда обязана
  // пересоздать панель целиком — родитель ставит `key={round.id}` (см.
  // TenderCardPage); иначе после `?round=` панель продолжала бы опрашивать job
  // ПРЕЖНЕГО раунда. Эффект с setState здесь не годится: правило
  // `react-hooks/set-state-in-effect` в этом проекте его запрещает (см.
  // комментарий в ContractDeleteDialog).
  const [jobId, setJobId] = useState<number | undefined>(round.latest_job?.id);
  const [idempotent, setIdempotent] = useState(false);
  const [conflict, setConflict] = useState<{ file: File; detail: string } | null>(null);
  const [rejection, setRejection] = useState<string | null>(null);
  const upload = useUploadRound();
  const jobQ = useImportJob(jobId, { tenderId });
  const job = jobQ.data;

  async function send(file: File, replace: boolean) {
    setRejection(null);
    setIdempotent(false);
    try {
      const created = await upload.mutateAsync({ file, tender_id: tenderId, round_id: roundId, replace });
      setJobId(created.id);
      setIdempotent(created.status === "done");
      setConflict(null);
    } catch (error) {
      const status = apiErrorStatus(error);
      const detail = apiErrorDetail(error) ?? "Не удалось загрузить файл.";
      if (status === 409 && isAdmin && !replace) { setConflict({ file, detail }); return; }
      setRejection(detail);
    }
  }

  return (
    <Surface className="grid gap-4">
      <ImportJobPanel job={job} uploading={upload.isPending} idempotent={idempotent} rejection={rejection}
        disabled={upload.isPending || (job !== undefined && isRunning(job.status))}
        hint="Сводная таблица раунда: XLSX или XLSM, до 25 МБ"
        onDrop={(files) => { const f = files[0]; if (f) void send(f, false); }} />
      <AlertDialog open={conflict !== null} onOpenChange={(open) => !open && setConflict(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Раунд уже загружен</AlertDialogTitle>
            <AlertDialogDescription>
              {conflict?.detail} Замена удалит сметы всех участников и расчётной стоимости этого раунда и загрузит
              новый файл. Участники в решётке останутся; прежние задания импорта и их файлы — тоже, это аудит.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel render={<Button variant="outline">Отмена</Button>} />
            <AlertDialogAction render={<Button onClick={() => { const p = conflict; setConflict(null); if (p) void send(p.file, true); }}>Заменить раунд</Button>} />
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Surface>
  );
}
```

Run: `cd frontend && npx vitest run src/components/tenders/RoundUploadPanel.test.tsx` — PASS.

- [ ] **Step 5: lint, typecheck, весь фронт, Commit**

Run: `cd frontend && npm run lint`; `npx tsc -b --noEmit`; `npm test` — всё зелёное.

```bash
git add frontend/src/components/imports frontend/src/components/tenders/RoundUploadPanel.tsx \
  frontend/src/components/tenders/RoundUploadPanel.test.tsx frontend/src/components/contracts/EstimateUploadPanel.tsx
git commit -m "feat(tenders-contour): общий ImportJobPanel; RoundUploadPanel с заменой раунда целиком"
```

---

## Task 12: Экраны — список тендеров и карточка с решёткой

Спека §2.14. Маршруты `/tenders`, `/tenders/:tenderId`, пункт меню «Тендеры»,
`?round=`, четыре состояния baseline, диалоги с подтверждением.

**Files:**
- Create: `frontend/src/pages/tenders/TendersPage.tsx`, `TendersPage.test.tsx`
- Create: `frontend/src/pages/tenders/TenderCardPage.tsx`, `TenderCardPage.test.tsx`
- Create: `frontend/src/components/tenders/TenderFormDialog.tsx`, `OfferGrid.tsx`, `RoundDeleteDialog.tsx`, `ParticipantDeleteDialog.tsx`, `ParticipantDeleteDialog.test.tsx`, `BaselineStatus.tsx`, `BaselineStatus.test.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/components/layout/TopNav.tsx`

**Interfaces:**
- Consumes: всё из задач 10–11.
- Produces: `BaselineStatus({ round }: { round: TenderRoundRow; hasEstimates: boolean })` — одно из четырёх состояний §2.14 текстом; `OfferGrid({ card, selectedRoundId, onSelectRound })`; `ParticipantDeleteDialog({ tenderId, participant, onOpenChange })` — протокол token.

- [ ] **Step 1: падающие тесты состояний baseline**

`BaselineStatus.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BaselineStatus } from "./BaselineStatus";
import { sampleTenderCard } from "@/test/fixtures";

const base = sampleTenderCard.rounds[0];

describe("BaselineStatus — четыре состояния (спека §2.14)", () => {
  it("текущий done-job и baseline есть → «загружена» с итогом", () => {
    render(<BaselineStatus round={{ ...base, current_job_id: 9101, baseline_estimate_id: 8100, baseline_total_including_vat: "1150.00" }} hasEstimates />);
    expect(screen.getByText(/Расчётная стоимость загружена/)).toBeInTheDocument();
    expect(screen.getByText(/Итого с НДС/)).toHaveTextContent(/1\s150,00/);
  });
  it("текущий done-job, baseline нет → «в файле не заполнена»", () => {
    render(<BaselineStatus round={{ ...base, current_job_id: 9101, baseline_estimate_id: null }} hasEstimates />);
    expect(screen.getByText(/в файле не заполнена/)).toBeInTheDocument();
  });
  it("текущего job нет, смет нет → «Файл раунда не загружен»", () => {
    render(<BaselineStatus round={{ ...base, current_job_id: null, baseline_estimate_id: null }} hasEstimates={false} />);
    expect(screen.getByText(/Файл раунда не загружен/)).toBeInTheDocument();
    expect(screen.queryByText(/не заполнена/)).toBeNull();
  });
  it("текущего job нет, сметы остались → «состав изменён — требуется полная замена»", () => {
    render(<BaselineStatus round={{ ...base, current_job_id: null, baseline_estimate_id: null }} hasEstimates />);
    expect(screen.getByText(/Состав раунда изменён после импорта/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: `BaselineStatus`**

```tsx
import { StatusPill } from "@/components/ui-domain/StatusPill";
import { formatDecimalMoney } from "@/lib/format";
import type { TenderRoundRow } from "@/types/domain";

/**
 * Состояние расчётной стоимости раунда — по ИСТОЧНИКУ факта, а не по одному
 * булеву (спека §2.14): до первой загрузки экран не вправе утверждать, что
 * база пуста, — файла он ещё не видел (AGENTS.md §11, «пустое поле — отсутствие
 * факта»).
 */
export function BaselineStatus({ round, hasEstimates }: { round: TenderRoundRow; hasEstimates: boolean }) {
  if (round.current_job_id === null) {
    return hasEstimates ? (
      <StatusPill tone="warning" label="Состав раунда изменён после импорта — требуется полная замена" />
    ) : (
      <StatusPill tone="neutral" label="Файл раунда не загружен" />
    );
  }
  if (round.baseline_estimate_id === null) {
    return <StatusPill tone="neutral" label="Расчётная стоимость в файле не заполнена" />;
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <StatusPill tone="success" label="Расчётная стоимость загружена" />
      <span className="text-sm text-fg-secondary">
        Итого с НДС: {formatDecimalMoney(round.baseline_total_including_vat)}
      </span>
    </div>
  );
}
```

Тона `"success" | "warning" | "neutral"` существуют в `StatusTone`
(`StatusPill.tsx:3-9`); `formatDecimalMoney(value: string | number | null | undefined)`
принимает `Decimal | null` как есть (`Decimal` — алиас `string`).

Run: `cd frontend && npx vitest run src/components/tenders/BaselineStatus.test.tsx` — PASS.

- [ ] **Step 3: `ParticipantDeleteDialog` — тест и реализация**

Тест: открытие диалога вызывает `removeParticipant` без token → 409 →
показывает preview («раундах: 2», «смет: 2», «позиций: 1 830», «ручных решений: 3»)
и кнопку «Удалить участника»; нажатие шлёт token → 204 → `onOpenChange(false)`;
при `handlerState.participantDeleteOutcome = "stale"` после нажатия показывает
обновлённый preview и НЕ закрывается; при `"active"` показывает «Импорт раунда
выполняется — дождитесь» без кнопки удаления. Также: `member` не видит кнопку
удаления участника в карточке (тест карточки ниже).

Реализация: `useDeleteParticipant`; при монтировании с `participant` — вызов
без token; ответ 409 разбирать через `apiErrorDetail`-объект: `detail.code`;
`confirmation_required` → сохранить `detail` как preview; `active_import` →
состояние ожидания; успех → `onOpenChange(false)`.

- [ ] **Step 4: `OfferGrid`, `TenderFormDialog`, `RoundDeleteDialog`**

`OfferGrid` — `<Table>`: первая колонка — участник (название, ИНН), далее по
колонке на раунд (`Этап {stage_no}` + `label`), заголовок колонки — кнопка выбора
раунда (подсвечен `selectedRoundId`). Ячейка: `offer_id === null` → «—» с
`title="Не участвовал"`; `estimate_id === null` → «нет сметы»
(`tone="warning"`); иначе — «Итого с НДС» через `formatDecimalMoney` или «—»
если `null`. Под таблицей — подпись «Итого с НДС».

`TenderFormDialog` — по образцу `ContractFormDialog`: `EntityCombobox` для
объекта (`useObjects` с `q`) и класса (`useRateClasses`), поля `tender_number`,
`title`, `notes`; `rate_class_id` не задан → подсказка «класс объекта».

`RoundDeleteDialog` — `AlertDialog`: «Удалить этап {stage_no}? Уйдут сметы всех
участников этого раунда и история его загрузок с файлами»; `useDeleteRound`.

- [ ] **Step 5: страницы**

`TendersPage` — по образцу `ContractsPage`: `PageHeader` «Тендеры», поиск
(`useDebounce`), таблица (номер → ссылка на карточку, предмет, объект, класс,
раундов, участников), `Pager`, кнопка «Новый тендер» только `isAdmin`,
`EmptyState` «Тендеров пока нет».

`TenderCardPage`:

```tsx
export default function TenderCardPage() {
  const { tenderId } = useParams();
  const id = tenderId ? Number(tenderId) : undefined;
  const [params, setParams] = useSearchParams();
  const cardQ = useTender(id);
  const card = cardQ.data;
  const { data: user } = useCurrentUser();
  const isAdmin = user?.role === "admin";

  // Выбранный раунд — ?round=<id>; по умолчанию последний по stage_no (§2.14).
  const rounds = card?.rounds ?? [];
  const fromUrl = params.get("round") ? Number(params.get("round")) : undefined;
  const selected = rounds.find((r) => r.id === fromUrl) ?? rounds.at(-1);
  const hasEstimates = selected ? card!.cells.some((c) => c.round_id === selected.id && c.estimate_id !== null) : false;
  ...
```

Состав: `Breadcrumbs` (Тендеры → номер), `PageHeader` с кнопками admin (правка,
новый этап, удалить тендер), реквизиты (`Surface`), `OfferGrid`, панель
выбранного раунда: заголовок этапа, `BaselineStatus`,
**`<RoundUploadPanel key={selected.id} … />`** — `key` обязателен: смена
`?round=` пересоздаёт панель, иначе она опрашивала бы job прежнего раунда
(комментарий в самой панели), история загрузок раунда (`useRoundImportJobs`,
`is_current` пилюлей), кнопка удаления этапа (admin). У каждого участника в решётке — кнопка удаления (admin)
→ `ParticipantDeleteDialog`.

`App.tsx`: `import TendersPage`, `TenderCardPage`; маршруты
`<Route path="/tenders" element={<TendersPage />} />`,
`<Route path="/tenders/:tenderId" element={<TenderCardPage />} />` рядом с
договорами (чтение — `member`). `TopNav.tsx`:
`{ to: "/tenders", icon: Gavel, label: "Тендеры" }` после «Договоры»; импорт
`Gavel` из `lucide-react`.

- [ ] **Step 6: тесты страниц**

`TendersPage.test.tsx`: показывает номер, предмет, объект, счётчики;
`member` не видит «Новый тендер». `TenderCardPage.test.tsx` (рендер через
`<Routes><Route path="/tenders/:tenderId" .../></Routes>`, `initialRoute: "/tenders/300"`):
решётка 2×2, ячейка «—» у (этап 1, Бета) и «нет сметы» у (этап 2, Бета);
без `?round` выбран этап 2; `?round=3001` выбирает этап 1 и показывает
«Расчётная стоимость загружена» при `tenderRoundState = "loaded"`;
`"changed"` → «состав изменён»; `member` не видит кнопок удаления.

Run: `cd frontend && npx vitest run src/pages/tenders src/components/tenders` — PASS.

- [ ] **Step 7: lint, typecheck, весь фронт, Commit**

Run: `cd frontend && npm run lint && npx tsc -b --noEmit` (две команды) и `npm test`.

```bash
git add frontend/src/pages/tenders frontend/src/components/tenders frontend/src/App.tsx frontend/src/components/layout/TopNav.tsx
git commit -m "feat(tenders-contour): экраны — список тендеров и карточка с решёткой, ?round=, четыре состояния baseline, диалоги с token"
```

---

## Task 13: Финал — ревизия `AGENTS.md`, devlog, `just ci`, стенд, PR

Спека §2.15, DoD 15–16.

**Files:**
- Modify: `AGENTS.md` §3, §4, §5, §7, §8
- Create: `docs/devlog/2026-08-26-tenders-contour.md`

- [ ] **Step 1: `AGENTS.md` — одним коммитом, пять параграфов**

§3 (абзац «Single-tenant», строка ~196): к правам `member` добавить «загрузка
файлов раундов тендера — и она **заводит подрядчиков** по ИНН из файла (спека
контура §2.8)»; к `admin` — «тендеры, раунды, удаление участника тендера,
замена раунда».

§4: под блоком `estimates` дописать блок:

```
tenders / tender_rounds / offer_packages / offers    # тендерный контур (спека 2026-08-26)
  # offers — ячейка решётки «раунд × участник»; составные FK с продублированным
  # tender_id держат раунд и участника в одном тендере структурно.
estimates: contract_id NULL | offer_id NULL | round_id NULL — РОВНО ОДИН (CHECK);
  # round_id на смете = baseline раунда. uq_estimates_contract_amendment — частичный.
proposals.contractor_id NULL у baseline (CHECK против is_baseline).
position_items.deviation_from_baseline_cost: у смет договора NULL; у offer-смет — из файла (§2.10).
estimate_raw_data — ПРОЕКЦИЯ разобранного JSON под смету; точный результат разбора файла —
  import_jobs.parsed_data + parser_version (три факта об одном файле, спека §2.3).
contractors.inn — канон, только ASCII-цифры (CHECK); одна canonicalize_inn().
```

и в комментарии `position_items` заменить «deviation_from_baseline_cost остаётся
NULL (baseline в сметах нет)» на «…NULL у смет договора; у offer-смет — из файла».

§5: после трёх правил повторной загрузки добавить абзац «**Для раунда тендера**
(`POST /api/v1/tenders/{id}/rounds/{rid}/upload`): владелец один — раунд;
правило 1 работает через **текущий job** — `done`, число смет раунда с этим
`import_job_id` равно `estimates_created`, других смет у раунда нет; замена —
раунда целиком, до цикла, под `FOR KEY SHARE` тендера и `FOR UPDATE` раунда;
один файл даёт N offer-смет плюс baseline в ОДНОЙ транзакции сессии B и ОДИН
матчинг; `parsed_data` пишет сессия A сразу после парсинга у обоих владельцев».

§7: пункт 8 после «Сравнение договоров»: «**Тендеры** — `/tenders` и
`/tenders/:id`: список, карточка с решёткой «участники × раунды», панель
раунда (`?round=`) с четырьмя состояниями расчётной стоимости, загрузка
сводной таблицы, удаление участника с подтверждением состава (token). Сравнение
участников между собой — фича 3 ([спека](docs/superpowers/specs/2026-08-26-tenders-contour-design.md) §2.14)».

§8: заменить «файлы `done`-jobs хранятся бессрочно (аудит), пока жив договор;
удаление договора удаляет их после коммита доменной транзакции, best-effort
(v6.7)» на «файлы `done`-jobs хранятся бессрочно (аудит), пока жив владелец —
договор либо раунд тендера; удаление договора, раунда или тендера удаляет их
после коммита доменной транзакции, best-effort; удаление участника тендера
файлы НЕ трогает — job и результат разбора остаются аудитом (спека контура
§2.11)».

```bash
git add AGENTS.md
git commit -m "docs(agents): ревизия §3, §4, §5, §7, §8 — тендерный контур (спека 2026-08-26 §2.15)"
```

- [ ] **Step 2: devlog**

`docs/devlog/2026-08-26-tenders-contour.md` по форме devlog фичи 1: ветка,
спека, план; что сделано по задачам; **отступления от плана** (что исполнители
поправили в эскизах — из отчётов); **найденные грабли**; **границы** (§4
спеки); замеры стенда (шаг 4). Без имён файлов `samples/`, подрядчиков и сумм.

- [ ] **Step 3: `just ci`**

Run: `just ci` (из корня). Expected: `OK: все проверки прошли`, код возврата
одиночной команды 0 (в bash — `${PIPESTATUS[0]}`, если хвост обрезается `tail`).

- [ ] **Step 4: стенд**

Поднять `just dev-backend` и `just dev-frontend` (или `npx vite --port 5199 --strictPort`,
если 5173 занят чужим vite). Завести тендер и раунд, загрузить **реальную
сводную таблицу** — файл класса 3 фичи 1 (дайджест `74e53bdfc43b`). Ожидается:
4 участника заведены, baseline — «Расчётная стоимость в файле не заполнена»,
все четыре ячейки раунда со сметой и «Итого с НДС». Снимки экрана **в обеих
темах** для каждого состояния взаимодействия: пустой раунд; идущий импорт;
ошибка (загрузить не-смету); replace-confirmation (второй файл); preview
удаления участника; «состав изменён после импорта» (после удаления участника).
Счётчики и наблюдения — в devlog; ни одного имени подрядчика.

```bash
git add docs/devlog/2026-08-26-tenders-contour.md
git commit -m "docs(tenders-contour): devlog — отступления, грабли, границы, стенд"
```

- [ ] **Step 5: push и PR**

```bash
git push -u origin feat/tenders-contour
```

PR: заголовок «Тендерный контур и импорт сводных таблиц раунда», в описании —
ссылки на спеку, план и devlog; что даёт (N смет из одного файла атомарно,
решётка, аудит разбора); что НЕ входит (команда «создать договор из раунда»,
свод по этапам); счётчики стенда без имён; пометка «`backend/parser/` не
изменён; паспорт не тронут, запись 19 `TECH_DEBT.md`».

---

## Соответствие DoD спеки задачам

| DoD | Где закрыт | Чем доказано |
|---|---|---|
| 1. N+1 смет атомарно | 5, 6 | `TestImportRound` (3 сметы, откат при инъекции отказа), `TestRoundJob` |
| 2. `parsed_data` у обоих владельцев, `raw_data` — проекция | 2, 4 | `TestParseAudit` (done, failed, parse-error), докстроки, §4 |
| 3. Замена целиком; `Offer` остаётся | 6 | `TestReplaceRoundEstimates`, `test_replace_drops_a_participant_missing…` |
| 4. Текущий job, идемпотентность, 409 после удаления участника | 7, 8 | `TestCurrentRoundJob`, `test_same_sha_after_participant_deleted_is_409_not_200` |
| 5. Канон ИНН на уровне данных | 1, 2 | `test_inn.py`, `TestContractorInn`, `TestInnDataMigration`, тесты справочника |
| 6. Составные FK, `NOT NULL`, пробой каждого `CHECK` | 2 | `TestOfferGridIntegrity` (включая `round_id = NULL`), `TestEstimateOwners`, `TestImportJobOwners`, parity |
| 7. Baseline по лотам, без допработ, deviation | 5, 6 | `TestBaselineOwner` (шпион на обоих звеньях + контроль), `test_partial_baseline_names_the_lot_without_it`, `test_deviation_lands_on_participants…` |
| 8. Удаление участника не трогает аудит; token | 7, 8 | `TestDeleteParticipant`, `TestParticipantDeletionProtocol` |
| 9. Удаление раунда/тендера чистит файлы | 7, 8 | `TestDeleteRoundAndTender`, `purge_files_best_effort` |
| 10. Иерархия локов, гонки, дедлок | 9 | `test_tenders_concurrency.py` + три снятия защиты |
| 11. Экран, решётка, четыре состояния, `?round=` | 10–12 | `BaselineStatus.test`, `TenderCardPage.test`, `OfferGrid` |
| 12. Права, §3 | 8, 13 | `TestRights`, `test_member_cannot_delete_participant` |
| 13. Договорной импорт без изменений + три новых поля | 3, 4 | существующие тесты без правок утверждений; `TestParseAudit`, `TestContractJobResponseUnchanged` |
| 14. `estimate_total_including_vat` единогласие; паспорт не тронут | 7 | `TestEstimateTotalIncludingVat`; `_file_total_including_vat` вне диффа |
| 15. `AGENTS.md` пять параграфов одним коммитом | 13 | коммит `docs(agents)` |
| 16. `just ci`, стенд, снимки состояний | 13 | devlog |

---

## Порядок и точки ревью

Строго по номерам: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12 → 13.
Параллелить нечего: каждая стоит на предыдущей; 10–12 стоят на 8 (контракт API).

Ревью после КАЖДОЙ задачи обязательно. Отдельное внимание: после 3 — что ни
одно утверждение старых тестов не изменилось; после 6 — порядок локов и
единственный `match_positions`; после 7 — явный `DELETE offers` и снятие;
после 9 — три снятия защиты выполнены контроллером и записаны.

Снятия защиты (задачи 7 и 9) — контроллер, посреди задачи, с возвратом точной
обратной правкой и контрольным прогоном; снятие, не покрасившее целевой тест,
— не пройдено.
