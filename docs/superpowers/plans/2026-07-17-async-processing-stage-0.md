# Async Processing — Ступень 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Устранить потерю данных, ложные статусы, блокировку event loop и незащищённое редактирование СФ во время парсинга — без смены способа постановки задачи (обработка по-прежнему инлайн `await` в хэндлере).

**Architecture:** Парсинг PDF разрезается на чистую фазу A (LLM-вызов, без БД, возвращает `ParseOutcome`, бросает доменные ошибки) и транзакционную фазу B (единственный commit: удалить старые СФ → вставить новые → статус/стоимость). Обе фазы живут в новом модуле `processing.py` за функцией `run_processing_attempt`; эндпоинты становятся тонкими (валидация → atomic guard-переход в `processing` → инлайн-обработка). Старые СФ переживают неудачный reparse.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy (sync), Alembic, PostgreSQL (Neon), httpx, pytest + respx, `anyio.to_thread` (транзитивно через Starlette — не новая зависимость).

## Global Constraints

- **Команды — только через `just`.** Никогда `cd backend && ...` напрямую (в т.ч. `alembic`, `pytest`, `uvicorn`). Shell на Windows: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just <cmd> 2>&1"`. Для точечного прогона тестов используем добавляемые в Task 1 рецепты `just test-int-k '<pattern>'` / `just test-unit-k '<pattern>'` (это `pytest -k`, корректный код возврата). **Пайп `... | grep` в Run-шагах запрещён для вердикта pass/fail** — bash в justfile без `pipefail`, пайп маскирует код возврата pytest; фильтровать вывод только через `-k`-рецепты.
- **Миграции:** создавать НОВУЮ ревизию через добавляемый в Task 1 рецепт `just db-revision message="..."` (это `alembic revision` без `--autogenerate` — создание нового файла, НЕ правка исторических файлов в `versions/`; ручное заполнение тела `upgrade`/`downgrade` новой ревизии — норма). Это соответствует уточнённой формулировке правила миграций в `AGENTS.md` (правка внесена в этой же ревизии: «исторические не редактировать; новые ревизии — через `just db-revision`, тело заполнять вручную допустимо»). Применять: `just db-migrate` (dev-БД) / `just db-test-migrate` (тестовая БД, уже есть в justfile). Head на старте плана — `1859523e53de`.
- Терминальные статусы `parsed` / `error` НЕ переименовывать — существующие тесты (`tests/integration/test_invoices.py`) и фронт-фильтры (`ErrorDocsTab.tsx`, `InvoiceKpiBar.tsx`) на них завязаны.
- **Докстринг у КАЖДОЙ функции/метода — включая тесты, приватные `_helpers` И вложенные тестовые фейки (`boom`, `fake_deskew`, `factory`, `handler`), а также `upgrade`/`downgrade` в миграциях.** Однострочник по сути — норма. Порог покрытия PR ≥80%, цель 100% в изменённых файлах. Все примеры кода в этом плане уже несут докстринги — воспроизводить их дословно.
- Перед завершением каждой задачи прогонять `just lint` и релевантные тесты; перед завершением всего плана — `just lint` и `just test`.
- Русский — для UI-строк и `last_error`-сообщений; docstrings/комментарии — по стилю окружающего кода.
- READ COMMITTED (isolation_level в `database.py` не задан → дефолт Postgres) — на этом основана семантика условного UPDATE (§2.3 спеки) и `SELECT ... FOR UPDATE` (S0-8).

**Спека:** `docs/superpowers/specs/2026-07-16-async-processing-design.md` (round 3.4, финал). Ссылки вида S0-N / AC-S0-N — на её разделы §3.

**Ревизия плана (round 2, после ревью):** исправлены три блокирующих расхождения со спекой — (1) error-путь фазы B теперь несёт стоимость и делает явный `rollback` (AC-S0-11); (2) deskew сохраняет HTTP-контракт 413/502 через `http_status` на доменной ошибке + `reraise` в эндпоинте (AC-S0-8), тесты 502/413 остаются с прежними ассертами; (3) deskew восстанавливает перезапись S3 + бэкап `.orig` (инвариант Q6). Плюс: `write_processing_error` не трогает `doc_type` (§2.3); фаза A бросает `PermanentError` при нуле разобранных СФ (не плодит «parsed + 0 СФ»); миграция ставит `NOT NULL`; bulk-delete лочит в порядке `id` (анти-дедлок).

**Ревизия плана (round 3, после второго ревью):** (1) `_run_deskew` в ветке «нули + есть бэкап» парсит текущий `s3_key` (исправленный), а не повёрнутый `.orig` — фикс flaky-нулевого detect при повторном deskew (иначе хороший набор СФ перезатёрся бы парсом кривого файла); (2) отклонение №4 зафиксировано осознанно — 413/502 deskew оставляют документ в `error` (санкционировано Q1); редакционные правки комментария `doc_type` и опечатки.

**Ревизия плана (round 4, после третьего ревью — исполнимость):** блокирующие фиксы и перестановка задач:
- **Инъектируемая фабрика сессий (F1).** `process_document` больше НЕ связывает `SessionLocal` в default-аргументе (связывание на этапе def плохо патчится и в тестах открывало сессию на реальном dev-`DATABASE_URL` — `conftest` не переопределяет `database.SessionLocal`). Теперь `session_factory=None` → поздний резолв `SessionLocal`; эндпоинты получают фабрику как FastAPI-зависимость `get_processing_session_factory`, а `client`-фикстура её переопределяет на тест-сессию. Так и прямые тесты `process_document`, и вызовы через `TestClient` видят тест-данные.
- **Перестановка задач (F2, F9).** Порядок: 1-4 без изменений → **Task 5** async-обёртки S3 (были Task 9) → **Task 6** ядро `process_document` + DI (были Task 5) → **Task 7** deskew-внутренности + учёт detect (были Task 8) → **Task 8** свап эндпоинтов (были Task 6, БЕЗ временного `xfail`) → **Task 9** FOR UPDATE защита мутаций (были Task 7). Устраняет: (а) использование `*_async`-обёрток до их создания; (б) красный deskew-контракт между коммитами.
- **Учёт стоимости detect при сбое ПОСЛЕ detect (F3).** `deskew_pdf` оборачивает сбой `apply_rotations` в `TransientError(cost_usd=detect_cost, paid_calls=1)`; `_run_deskew` оборачивает последующие S3-сбои так же. Плюс тест «detect ок → apply/upload падает → cost учтён».
- **Re-fetch под блокировкой (F4).** Мутирующие эндпоинты сначала берут `document_id`, лочат `Document` (`FOR UPDATE`), затем ПЕРЕЗАПрашивают СФ под блокировкой (404, если фаза B её удалила) — иначе устаревший ORM-объект → `StaleDataError`/500. В bulk — повторный запрос набора СФ по id после блокировки всех документов.
- **Надёжность/классификация ошибок:** `write_processing_error` ретраит только connection-related сбои, детерминированные — пробрасывает (F8); фаза A ловит `httpx.RequestError` (не только timeout) и классифицирует 429/408 как транзиентные (F12).
- **Покрытие:** AC-S0-11 проверяется end-to-end через `process_document` при сбое фазы B; AC-S0-13 — конкретный шаг Task 6 (два реальных соединения); 409-защита параметризована по всем шести мутациям + bulk (F5, F7).
- **Тестовые патчи (F6):** отмена патчит `pdf_parser.parse_pdf` (локальный импорт в `run_processing_attempt`), а не `processing.parse_pdf`; `processing` берёт S3 через `s3.download_file_async`/`s3.upload_file_async` (позднее связывание) — `in_memory_s3` покрывает их через sync-обёртки внутри `s3.py`.
- **Compliance (F10, F11):** команды и создание ревизий — через `just`-рецепты (добавляются в Task 1); докстринги обязательны у всех функций, включая вложенные фейки и `upgrade`/`downgrade`.

---

### Task 1: Статусная модель + миграция + just-рецепты (S0-1)

**Files:**
- Modify: `justfile` (добавить `db-revision`, `test-int-k`, `test-unit-k`)
- Modify: `backend/models.py:251-277` (класс `Document`)
- Create: `backend/alembic/versions/<generated>_async_processing_status_model.py`
- Test: `backend/tests/integration/test_invoices.py` (добавить тест)

**Interfaces:**
- Produces: `Document.status` (server_default `'pending'`, ORM default `"pending"`), `Document.processing_started_at: datetime | None`, `Document.last_error: str | None`, `Document.processing_run_id: str | None` (зарезервировано под S2, на S0 не используется).

- [ ] **Step 0: Добавить just-рецепты (compliance F10)**

В `justfile` в секцию `# === DB ===` добавить:

```make
# Создать НОВУЮ ревизию Alembic (без autogenerate — тело заполняется вручную).
# Это создание нового файла в versions/, НЕ правка исторических миграций.
db-revision message:
    cd backend && alembic revision -m "{{message}}"
```

В секцию `# === Tests ===` добавить точечные -k рецепты (корректный код возврата, без grep-пайпа):

```make
# Точечный прогон integration по -k паттерну
test-int-k pattern:
    cd backend && pytest tests/integration -v -k "{{pattern}}"

# Точечный прогон unit по -k паттерну
test-unit-k pattern:
    cd backend && pytest tests/unit -v -k "{{pattern}}"
```

Проверить: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just --list 2>&1"` — новые рецепты видны.

- [ ] **Step 1: Написать падающий тест на новый дефолт статуса**

В `backend/tests/integration/test_invoices.py` добавить:

```python
def test_new_document_defaults_to_pending(db_session, factories):
    """create_document создаёт документ в статусе pending, не parsed (S0-1, AC-S0-5)."""
    from crud.documents import create_document

    project = factories.ProjectFactory.create()
    doc = create_document(db_session, project.id, "x.pdf", "2026/07/x.pdf")

    assert doc.status == "pending"
    assert doc.processing_started_at is None
    assert doc.last_error is None
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-k 'test_new_document_defaults_to_pending' 2>&1"`
Expected: FAIL — `assert 'parsed' == 'pending'` (текущий дефолт `parsed`) либо `AttributeError` на `processing_started_at`.

- [ ] **Step 3: Обновить модель `Document`**

В `backend/models.py`, класс `Document`, заменить строку `status = Column(String, default="parsed")` и добавить поля после `parse_count`:

```python
    doc_type = Column(String, default="unknown")
    # Статусная модель обработки: pending → processing → parsed | error.
    # server_default='pending' для новых строк; исторические строки не трогаем (бэкфилл — Q2).
    status = Column(String, nullable=False, server_default="pending", default="pending")
```

И добавить (рядом с `uploaded_at` / `parse_count`, до `__table_args__`):

```python
    # Момент захвата обработки (guard S0-5) — для детекции зависших задач.
    processing_started_at = Column(DateTime, nullable=True)
    # Человекочитаемая причина последней ошибки (раньше жила только в логах).
    last_error = Column(String, nullable=True)
    # Ownership-токен запуска. Зарезервировано под ступень 2 (поздний retry);
    # на ступени 0 всегда NULL, но колонка заводится сразу, чтобы не делать вторую миграцию.
    processing_run_id = Column(String, nullable=True)
```

- [ ] **Step 4: Создать ревизию миграции (через just)**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just db-revision message='async processing status model' 2>&1"`
Expected: создан новый файл `backend/alembic/versions/<ts>-<rev>_async_processing_status_model.py` с `down_revision = '1859523e53de'`.

- [ ] **Step 5: Заполнить тело миграции**

В сгенерированном файле (докстринги у `upgrade`/`downgrade` — обязательны, F11):

```python
def upgrade() -> None:
    """Ставит NOT NULL + server_default='pending' на status, добавляет поля статусной модели."""
    # NOT NULL безопасен: приложение всегда проставляло status (ORM default 'parsed'),
    # NULL-строк в documents.status нет. Совмещаем с установкой server_default,
    # чтобы модель (nullable=False) и БД не расходились.
    op.alter_column("documents", "status", server_default="pending", nullable=False)
    op.add_column("documents", sa.Column("processing_started_at", sa.DateTime(), nullable=True))
    op.add_column("documents", sa.Column("last_error", sa.String(), nullable=True))
    op.add_column("documents", sa.Column("processing_run_id", sa.String(), nullable=True))


def downgrade() -> None:
    """Откатывает поля статусной модели и server_default/NOT NULL на status."""
    op.drop_column("documents", "processing_run_id")
    op.drop_column("documents", "last_error")
    op.drop_column("documents", "processing_started_at")
    op.alter_column("documents", "status", server_default=None, nullable=True)
```

Убедиться, что вверху файла есть `import sqlalchemy as sa` и `from alembic import op`.

- [ ] **Step 6: Применить миграцию к тестовой БД и прогнать тест**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just db-test-migrate 2>&1"`
Then: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-k 'test_new_document_defaults_to_pending' 2>&1"`
Expected: PASS. Также прогнать существующие upload-тесты (`just test-int-k 'upload'`) — они используют `DocumentFactory` (явный `status="parsed"`) и upload-флоу (ставит статус явно), поэтому должны остаться зелёными.

- [ ] **Step 7: Commit**

```bash
git add justfile backend/models.py backend/alembic/versions/ backend/tests/integration/test_invoices.py
git commit -m "feat(processing): статусная модель pending→processing→parsed|error + just-рецепты (S0-1)"
```

---

### Task 2: Доменные ошибки обработки (S0-4)

**Files:**
- Create: `backend/processing.py`
- Test: `backend/tests/unit/test_processing_errors.py`

**Interfaces:**
- Produces: `ProcessingError(message, *, cost_usd: Decimal = 0, paid_calls: int = 0, http_status: int | None = None)`, подклассы `TransientError`, `PermanentError`. Атрибуты `.message: str`, `.cost_usd: Decimal`, `.paid_calls: int`, `.http_status: int | None`.

- [ ] **Step 1: Написать падающий тест**

Create `backend/tests/unit/test_processing_errors.py`:

```python
"""Unit-тесты доменных ошибок обработки (S0-4)."""
from decimal import Decimal

import pytest

from processing import PermanentError, ProcessingError, TransientError


def test_transient_is_processing_error():
    """TransientError — подкласс ProcessingError (общий except ловит обе)."""
    assert issubclass(TransientError, ProcessingError)
    assert issubclass(PermanentError, ProcessingError)


def test_error_carries_accounting():
    """Ошибка несёт накопленный учёт платных вызовов для error-пути (S0-9, §2.5)."""
    err = TransientError("timeout", cost_usd=Decimal("0.0015"), paid_calls=1)
    assert err.cost_usd == Decimal("0.0015")
    assert err.paid_calls == 1
    assert err.message == "timeout"


def test_error_accounting_defaults_zero():
    """Ошибка без платного вызова несёт нулевой учёт и не задаёт http_status."""
    err = PermanentError("no api key")
    assert err.cost_usd == Decimal(0)
    assert err.paid_calls == 0
    assert err.http_status is None


def test_error_http_status_hint():
    """Доменная ошибка может нести подсказку HTTP-статуса для эндпоинта (AC-S0-8)."""
    err = PermanentError("too many pages", http_status=413)
    assert err.http_status == 413


def test_message_is_str_of_exception():
    """str(err) возвращает сообщение — для логов и last_error."""
    assert str(PermanentError("boom")) == "boom"
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-unit-k 'test_processing_errors or error_carries or error_http_status or accounting_defaults or message_is_str' 2>&1"`
Expected: FAIL — `ModuleNotFoundError: No module named 'processing'`.

- [ ] **Step 3: Создать модуль с доменными ошибками**

Create `backend/processing.py`:

```python
"""Ядро обработки документов: парсинг (фаза A) + персистенция (фаза B).

См. docs/superpowers/specs/2026-07-16-async-processing-design.md.
На ступени 0 process_document вызывается инлайн (await в хэндлере).
"""
from decimal import Decimal


class ProcessingError(Exception):
    """Базовая доменная ошибка попытки обработки.

    Несёт накопленный учёт платных вызовов OpenRouter (cost_usd, paid_calls),
    чтобы error-путь мог начислить стоимость даже при провале (инвариант
    parse-cost-tracking: HTTP 200 → деньги потрачены → стоимость учтена).
    """

    def __init__(self, message: str, *, cost_usd: Decimal = Decimal(0), paid_calls: int = 0,
                 http_status: int | None = None):
        """Сохраняет сообщение, накопленный учёт стоимости и подсказку HTTP-статуса.

        http_status задают только доменные ошибки, которые на ступени 0 должны
        дойти до клиента прежним HTTP-кодом (deskew: 413 слишком много страниц,
        502 сервис распознавания недоступен) — см. AC-S0-8. Ошибки парсинга
        http_status не задают → гасятся в status='error' + 200.
        """
        super().__init__(message)
        self.message = message
        self.cost_usd = cost_usd
        self.paid_calls = paid_calls
        self.http_status = http_status


class TransientError(ProcessingError):
    """Транзиентная ошибка (S3 недоступен, httpx timeout/сетевой сбой, 5xx/429/408 OpenRouter, сбой detect).

    На ступени 2 получит retry-политику; на ступени 0/1 ведёт к терминальному error.
    """


class PermanentError(ProcessingError):
    """Перманентная ошибка контента (невалидный JSON, провал сверки итогов,

    finish_reason=length, doc_type != invoice, слишком много страниц для deskew).
    Не ретраится никогда.
    """
```

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-unit-k 'transient_is_processing or error_carries or error_http_status or accounting_defaults or message_is_str' 2>&1"`
Expected: PASS (5 тестов).

- [ ] **Step 5: Commit**

```bash
git add backend/processing.py backend/tests/unit/test_processing_errors.py
git commit -m "feat(processing): доменные ошибки Transient/Permanent с учётом стоимости (S0-4)"
```

---

### Task 3: Фаза A — чистый парсинг `parse_pdf` (S0-2, фаза A)

**Files:**
- Modify: `backend/pdf_parser.py` (добавить `ParseOutcome`, `ParsedInvoice`, `ParsedItem`, `parse_pdf`; НЕ удалять старый `parse_invoice_pdf` — удалится в Task 8)
- Test: `backend/tests/integration/test_pdf_parser_phase_a.py`

**Interfaces:**
- Consumes: `TransientError`, `PermanentError` из `processing.py` (Task 2). Существующие helpers `_reconcile_totals`, `_calculate_completeness`, `_final_confidence`, константы `SYSTEM_PROMPT`, `OPENROUTER_URL`, построение `payload` — переиспользуются без изменений.
- Produces:
  - `@dataclass ParsedItem`: `raw_name: str`, `item_type: str`, `material_class: str | None`, `material_type: str | None`, `calc_role: str | None`, `quantity: float`, `unit: str | None`, `unit_price: float`, `amount: float`, `vat_amount: float | None`.
  - `@dataclass ParsedInvoice`: `number: str`, `date: datetime.date`, `supplier_name: str | None`, `supplier_inn: str | None`, `vat_rate: float`, `confidence: float`, `items: list[ParsedItem]`.
  - `@dataclass ParseOutcome`: `doc_type: str`, `invoices: list[ParsedInvoice]`, `cost_usd: Decimal`, `paid_calls: int`.
  - `async def parse_pdf(file_data: bytes, *, document_id: int) -> ParseOutcome` — чистая (без `db`); при ошибке бросает `TransientError`/`PermanentError` с накопленным `cost_usd`/`paid_calls`. Материалы НЕ резолвятся в id — `material_class`/`material_type`/`calc_role` возвращаются сырыми строками для фазы B.

- [ ] **Step 1: Написать падающие тесты фазы A**

Create `backend/tests/integration/test_pdf_parser_phase_a.py`:

```python
"""Тесты чистой фазы A парсинга (S0-2). Без БД — только LLM + структуры."""
from decimal import Decimal

import pytest

from pdf_parser import ParseOutcome, parse_pdf
from processing import PermanentError, TransientError


@pytest.mark.anyio
async def test_parse_pdf_happy_path_returns_outcome(sample_pdf_bytes, mock_openrouter):
    """Успешный разбор возвращает ParseOutcome со стоимостью и без обращения к БД."""
    outcome = await parse_pdf(sample_pdf_bytes, document_id=1)
    assert isinstance(outcome, ParseOutcome)
    assert outcome.doc_type == "invoice"
    assert len(outcome.invoices) == 1
    assert outcome.invoices[0].number == "СФ-101"
    assert outcome.invoices[0].items[0].material_class is not None or outcome.invoices[0].items[0].item_type
    assert outcome.cost_usd > 0
    assert outcome.paid_calls == 1


@pytest.mark.anyio
async def test_parse_pdf_unparseable_raises_permanent_with_cost(sample_pdf_bytes, mock_openrouter):
    """doc_type != invoice → PermanentError, но платный вызов учтён в ошибке."""
    mock_openrouter.use_scenario("unparseable")
    with pytest.raises(PermanentError) as exc:
        await parse_pdf(sample_pdf_bytes, document_id=1)
    assert exc.value.paid_calls == 1
    assert exc.value.cost_usd >= 0


@pytest.mark.anyio
async def test_parse_pdf_incomplete_totals_raises_permanent(sample_pdf_bytes, mock_openrouter):
    """Провал сверки итогов → PermanentError с учётом стоимости."""
    mock_openrouter.use_scenario("incomplete_totals")
    with pytest.raises(PermanentError) as exc:
        await parse_pdf(sample_pdf_bytes, document_id=1)
    assert exc.value.paid_calls == 1


@pytest.mark.anyio
async def test_parse_pdf_5xx_raises_transient_no_cost(sample_pdf_bytes, mock_openrouter):
    """OpenRouter 5xx → TransientError без стоимости (нет платного 200)."""
    mock_openrouter.use_http_status(503)
    with pytest.raises(TransientError) as exc:
        await parse_pdf(sample_pdf_bytes, document_id=1)
    assert exc.value.paid_calls == 0
    assert exc.value.cost_usd == Decimal(0)


@pytest.mark.anyio
async def test_parse_pdf_429_raises_transient(sample_pdf_bytes, mock_openrouter):
    """OpenRouter 429 (rate limit) → TransientError, не Permanent (F12, ретраебельно на S2)."""
    mock_openrouter.use_http_status(429)
    with pytest.raises(TransientError):
        await parse_pdf(sample_pdf_bytes, document_id=1)
```

Добавить фикстуру `anyio_backend` (если её ещё нет в conftest) — см. Step 2.

- [ ] **Step 2: Добавить anyio-backend фикстуру для async-тестов**

Проверить `backend/tests/conftest.py` на наличие `anyio_backend`. Если нет — добавить:

```python
@pytest.fixture
def anyio_backend() -> str:
    """Гоняем async-тесты только на asyncio (не trio) — единственный backend проекта."""
    return "asyncio"
```

Убедиться, что `anyio` доступен (транзитивно через Starlette): `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -c 'import anyio; print(anyio.__version__)' 2>&1"`. Если `pytest` не понимает `@pytest.mark.anyio` — проверить, что `anyio` установлен с pytest-плагином (`python -c "import anyio.pytest_plugin"`); при отсутствии — заменить маркер на `@pytest.mark.asyncio` (если проект уже использует pytest-asyncio) либо согласовать с пользователем (новая dev-зависимость — по явному запросу).

- [ ] **Step 3: Запустить тесты — убедиться, что падают**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-k 'parse_pdf' 2>&1"`
Expected: FAIL — `ImportError: cannot import name 'parse_pdf'`.

- [ ] **Step 4: Добавить структуры и `parse_pdf` в `pdf_parser.py`**

В начало `backend/pdf_parser.py` добавить импорты и dataclass'ы (после существующих импортов):

```python
from dataclasses import dataclass, field

from processing import PermanentError, TransientError


@dataclass
class ParsedItem:
    """Позиция СФ из ответа модели — сырой material_class/type/role (резолв в id — фаза B)."""
    raw_name: str
    item_type: str
    material_class: str | None
    material_type: str | None
    calc_role: str | None
    quantity: float
    unit: str | None
    unit_price: float
    amount: float
    vat_amount: float | None


@dataclass
class ParsedInvoice:
    """Одна СФ из ответа модели с посчитанной итоговой confidence."""
    number: str
    date: date
    supplier_name: str | None
    supplier_inn: str | None
    vat_rate: float
    confidence: float
    items: list[ParsedItem] = field(default_factory=list)


@dataclass
class ParseOutcome:
    """Результат чистой фазы A: тип документа, разобранные СФ и учёт стоимости вызова."""
    doc_type: str
    invoices: list[ParsedInvoice]
    cost_usd: Decimal
    paid_calls: int
```

Затем добавить функцию `parse_pdf` (переиспользует существующие `SYSTEM_PROMPT`, `OPENROUTER_URL`, построение `payload`, `_reconcile_totals`, `_calculate_completeness`, `_final_confidence` — они уже есть в файле и не меняются):

```python
async def parse_pdf(file_data: bytes, *, document_id: int) -> ParseOutcome:
    """Чистая фаза A: вызвать OpenRouter, разобрать ответ, вернуть ParseOutcome.

    Без обращения к БД. При ошибке бросает доменное исключение с накопленным
    учётом стоимости: TransientError (транзиентные сбои: сеть/таймаут/5xx/429/408)
    или PermanentError (ошибки контента). Материалы не резолвятся в id — это делает фаза B.
    """
    cost = Decimal(0)
    paid_calls = 0
    logger.info(f"[doc={document_id}] Фаза A: старт парсинга, {len(file_data)} байт")

    api_key = settings.OPENROUTER_API_KEY
    if not api_key:
        raise PermanentError("API-ключ OpenRouter не настроен")

    pdf_base64 = base64.b64encode(file_data).decode("utf-8")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    max_tokens = settings.AI_MAX_TOKENS
    payload = {
        "model": settings.AI_MODEL,
        "max_tokens": max_tokens,
        "usage": {"include": True},
        "plugins": [{"id": "file-parser", "pdf": {"engine": settings.PDF_ENGINE}}],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "file", "file": {"filename": "document.pdf",
                     "file_data": f"data:application/pdf;base64,{pdf_base64}"}},
                    {"type": "text", "text": (
                        "Определи тип документа и извлеки данные. ВАЖНО: каждая строка "
                        "из табличной части — это отдельная позиция в items. "
                        "Не объединяй и не суммируй строки, даже если они выглядят одинаково."
                    )},
                ],
            },
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(OPENROUTER_URL, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise TransientError("Таймаут запроса к OpenRouter (180с)") from exc
    except httpx.RequestError as exc:
        # ConnectError / ReadError / RemoteProtocolError / DNS / TLS — транспортный сбой
        # без ответа сервера → платного вызова не было (F12).
        raise TransientError(f"Сетевая ошибка запроса к OpenRouter: {exc}") from exc

    if response.status_code != 200:
        msg = f"OpenRouter API ошибка: {response.status_code}"
        # 5xx (сервер), 429 (rate limit), 408 (request timeout) — транзиентно, ретраебельно на S2.
        if response.status_code >= 500 or response.status_code in (408, 429):
            raise TransientError(msg)
        raise PermanentError(msg)

    # HTTP 200 ⇒ платный вызов состоялся. Фиксируем факт биллинга ДО чтения тела.
    paid_calls = 1
    try:
        data = response.json()
    except Exception as exc:  # noqa: BLE001 — битое тело от прокси остаётся платным вызовом
        raise PermanentError("Не удалось разобрать ответ модели (тело не JSON)",
                             cost_usd=cost, paid_calls=paid_calls) from exc
    cost = Decimal(str((data.get("usage") or {}).get("cost") or 0))

    usage = data.get("usage", {})
    completion_tokens = usage.get("completion_tokens", 0)
    finish_reason = (data.get("choices") or [{}])[0].get("finish_reason")
    logger.info(f"[doc={document_id}] Фаза A: cost=${cost}, finish_reason={finish_reason}")

    if finish_reason == "length":
        raise PermanentError(
            "Ответ модели обрезан по лимиту токенов — часть позиций счёта потеряна. "
            "Попробуйте повторить разбор.",
            cost_usd=cost, paid_calls=paid_calls,
        )
    if completion_tokens and completion_tokens >= max_tokens:
        logger.error(f"[doc={document_id}] completion_tokens={completion_tokens} == max — ответ обрезан")

    try:
        response_text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise PermanentError("Ответ модели без содержимого",
                             cost_usd=cost, paid_calls=paid_calls) from exc

    if "```json" in response_text:
        response_text = response_text.split("```json")[1].split("```")[0]
    elif "```" in response_text:
        response_text = response_text.split("```")[1].split("```")[0]

    try:
        parsed = json.loads(response_text.strip())
    except json.JSONDecodeError as exc:
        raise PermanentError("Не удалось разобрать ответ модели (невалидный JSON)",
                             cost_usd=cost, paid_calls=paid_calls) from exc

    if parsed.get("doc_type") != "invoice":
        raise PermanentError("Документ не является счётом-фактурой",
                             cost_usd=cost, paid_calls=paid_calls)

    invoices: list[ParsedInvoice] = []
    for inv_idx, inv_data in enumerate(parsed.get("invoices", [])):
        confidence = _final_confidence(inv_data.get("confidence"), _calculate_completeness(inv_data))
        items: list[ParsedItem] = []
        for item in inv_data.get("items", []):
            items.append(ParsedItem(
                raw_name=item.get("raw_name") or "",
                item_type=item.get("item_type") or "other",
                material_class=item.get("material_class"),
                material_type=item.get("material_type"),
                calc_role=item.get("calc_role"),
                quantity=float(item.get("quantity") or 0),
                unit=item.get("unit"),
                unit_price=float(item.get("unit_price") or 0),
                amount=float(item.get("amount") or 0),
                vat_amount=item.get("vat_amount"),
            ))

        inv_number = inv_data.get("number", "?")
        try:
            invoice_date_str = inv_data.get("date")
            if not invoice_date_str:
                raise ValueError("Дата СФ отсутствует в ответе модели")
            invoice_date = date.fromisoformat(invoice_date_str)
        except (ValueError, TypeError) as e:
            logger.error(f"[doc={document_id}] СФ №{inv_number}: некорректная дата: {e} — пропуск СФ")
            continue

        doc_total = inv_data.get("doc_total_without_vat")
        try:
            doc_total = float(doc_total) if doc_total is not None else None
        except (TypeError, ValueError):
            doc_total = None
        reconciled, detail = _reconcile_totals(
            doc_total, [{"amount": it.amount} for it in items]
        )
        if not reconciled:
            raise PermanentError(f"Разбор счёта №{inv_number} неполный: {detail}",
                                 cost_usd=cost, paid_calls=paid_calls)

        invoices.append(ParsedInvoice(
            number=inv_data.get("number", ""),
            date=invoice_date,
            supplier_name=inv_data.get("supplier_name"),
            supplier_inn=inv_data.get("supplier_inn"),
            vat_rate=inv_data.get("vat_rate", 20),
            confidence=confidence,
            items=items,
        ))

    if not invoices:
        # doc_type=invoice, но ни одной СФ не разобрано (пустой invoices или все даты кривые
        # → continue выше). Не создаём документ «parsed с 0 СФ» — это тот артефакт, ради
        # устранения которого вводилась статусная модель (Q2, класс 2).
        raise PermanentError("Ни одной СФ не удалось разобрать из документа",
                             cost_usd=cost, paid_calls=paid_calls)

    logger.info(f"[doc={document_id}] Фаза A: разобрано СФ {len(invoices)}, cost=${cost}")
    return ParseOutcome(doc_type="invoice", invoices=invoices, cost_usd=cost, paid_calls=paid_calls)
```

> Примечание для реализатора: `_reconcile_totals` принимает список dict-ов с ключом `amount` — поэтому позиции оборачиваются в `{"amount": it.amount}`. Валидация `calc_role`/`material_type` (сейчас в теле старого `parse_invoice_pdf`, строки ~290-320) переезжает в фазу B вместе с резолвом класса — здесь материалы остаются сырыми строками.

- [ ] **Step 5: Запустить тесты — убедиться, что проходят**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-k 'parse_pdf' 2>&1"`
Expected: PASS (5 тестов). Старый `parse_invoice_pdf` и его тесты (`test_invoices.py`) — без изменений, зелёные.

- [ ] **Step 6: Lint + commit**

```bash
git add backend/pdf_parser.py backend/tests/integration/test_pdf_parser_phase_a.py backend/tests/conftest.py
git commit -m "feat(processing): чистая фаза A parse_pdf → ParseOutcome (S0-2)"
```

---

### Task 4: Фаза B — персистенция `persist_parse_result` + no-commit резолв класса (S0-2, Q7)

**Files:**
- Modify: `backend/crud/materials.py:41-67` (`get_or_create_material_class` — параметр `commit`)
- Modify: `backend/processing.py` (добавить `persist_parse_result`)
- Test: `backend/tests/integration/test_processing_persist.py`

**Interfaces:**
- Consumes: `ParseOutcome`/`ParsedInvoice`/`ParsedItem` (Task 3), `get_or_create_supplier` (уже flush-only, `crud/suppliers.py:76`), `get_or_create_material_class(..., commit=False)` (эта задача), `load_alias_map`/`normalize_item` (`crud/units.py`), `VALID_CALC_ROLES`/`UnknownMaterialType` (`crud/materials.py`).
- Produces: `def persist_parse_result(db: Session, doc_id: int, outcome: ParseOutcome) -> None` — в одной транзакции удаляет старые СФ, резолвит поставщиков/классы (flush), вставляет новые СФ, ставит `status='parsed'`, `doc_type`, инкремент `parse_cost_usd`/`parse_count`, единственный `commit`. Резолв verified-СФ (S0-8) добавится в Task 9.

> **Покрытие AC-S0-11 (F5):** тест этой задачи (`test_persist_phase_b_error_rolls_back_and_carries_cost`) проверяет rollback + перенос стоимости в исключение на уровне `persist_parse_result`. Полный end-to-end AC-S0-11 (сбой фазы B ПОСЛЕ оплаченной фазы A через `process_document` → `status='error'` + `last_error` + стоимость в Document + отсутствие двойного начисления) проверяется в Task 6 (`test_process_document_phase_b_failure_writes_error_with_cost`).

- [ ] **Step 1: Написать падающий тест на no-commit резолв класса**

В `backend/tests/integration/` создать `backend/tests/integration/test_processing_persist.py`:

```python
"""Тесты фазы B: персистенция результата парсинга (S0-2)."""
from decimal import Decimal

import pytest

from crud.documents import create_document
from crud.materials import get_or_create_material_class
from models import Invoice
from pdf_parser import ParsedInvoice, ParsedItem, ParseOutcome
from processing import persist_parse_result


def test_get_or_create_material_class_no_commit_flushes_only(db_session, factories):
    """commit=False оставляет транзакцию открытой (откат вызывающего убирает класс)."""
    from models import MaterialClass

    mc = get_or_create_material_class(db_session, name="В40", material_type="concrete", commit=False)
    assert mc.id is not None  # flush присвоил id
    db_session.rollback()
    # После отката класс не должен сохраниться
    assert db_session.query(MaterialClass).filter(MaterialClass.name == "В40").first() is None
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-k 'no_commit_flushes' 2>&1"`
Expected: FAIL — `TypeError: get_or_create_material_class() got an unexpected keyword argument 'commit'`.

- [ ] **Step 3: Добавить параметр `commit` в `get_or_create_material_class`**

В `backend/crud/materials.py` заменить сигнатуру и тело вставки:

```python
def get_or_create_material_class(
    db: Session, name: str, material_type: str, calc_role: str = "base", *, commit: bool = True
) -> MaterialClass:
    """Найти или создать класс материала.

    commit=True (по умолчанию) — самостоятельная транзакция (ручные CRUD-пути).
    commit=False — только flush, чтобы остаться в транзакции вызывающего (фаза B).
    """
    if calc_role not in VALID_CALC_ROLES:
        raise ValueError(f"Unknown calc_role {calc_role!r}; allowed: {sorted(VALID_CALC_ROLES)}")
    material_type_id = _material_type_id_by_code(db, material_type)
    mc = db.query(MaterialClass).filter(
        MaterialClass.name == name, MaterialClass.material_type_id == material_type_id
    ).first()
    if not mc:
        mc = MaterialClass(name=name, material_type_id=material_type_id, calc_role=calc_role)
        db.add(mc)
        if commit:
            db.commit()
            db.refresh(mc)
        else:
            db.flush()
    elif mc.calc_role != calc_role:
        # Preserved intentionally: the DB record represents a human-reviewed classification;
        # auto-update would allow LLM hallucinations to corrupt it.
        logger.warning(
            "get_or_create_material_class: class %r/%r found with calc_role=%r, "
            "but caller expects %r — stored value preserved",
            name, material_type, mc.calc_role, calc_role,
        )
    return mc
```

- [ ] **Step 4: Запустить no-commit тест — PASS**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-k 'no_commit_flushes' 2>&1"`
Expected: PASS.

- [ ] **Step 5: Написать падающие тесты `persist_parse_result`**

Добавить в `backend/tests/integration/test_processing_persist.py`:

```python
def _outcome(cost="0.002"):
    """ParseOutcome с одной СФ и одной материальной позицией — helper для тестов фазы B."""
    from datetime import date
    return ParseOutcome(
        doc_type="invoice",
        cost_usd=Decimal(cost),
        paid_calls=1,
        invoices=[ParsedInvoice(
            number="СФ-500", date=date(2026, 5, 1),
            supplier_name="ООО Тест", supplier_inn="1111111111",
            vat_rate=20, confidence=0.9,
            items=[ParsedItem(
                raw_name="Бетон В40", item_type="material", material_class="В40",
                material_type="concrete", calc_role="base", quantity=5.0, unit="м3",
                unit_price=9000.0, amount=45000.0, vat_amount=7500.0,
            )],
        )],
    )


def test_persist_creates_invoices_and_sets_parsed(db_session, factories):
    """Фаза B создаёт СФ, ставит parsed, накапливает стоимость и счётчик."""
    project = factories.ProjectFactory.create()
    doc = create_document(db_session, project.id, "x.pdf", "k/x.pdf")
    doc.status = "processing"
    db_session.commit()

    persist_parse_result(db_session, doc.id, _outcome())

    db_session.expire_all()
    from models import Document
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "parsed"
    assert saved.doc_type == "invoice"
    assert saved.parse_cost_usd == Decimal("0.002")
    assert saved.parse_count == 1
    assert db_session.query(Invoice).filter(Invoice.document_id == doc.id).count() == 1


def test_persist_replaces_old_invoices(db_session, factories):
    """Фаза B удаляет старые СФ и вставляет новые (parse-then-swap) в одной транзакции."""
    doc = factories.DocumentFactory.create(status="processing")
    factories.InvoiceFactory.create(document=doc, number="СФ-OLD")

    persist_parse_result(db_session, doc.id, _outcome())

    db_session.expire_all()
    numbers = [i.number for i in db_session.query(Invoice).filter(Invoice.document_id == doc.id).all()]
    assert numbers == ["СФ-500"]


def test_persist_phase_b_error_rolls_back_and_carries_cost(db_session, factories, monkeypatch):
    """Сбой фазы B → rollback (старые СФ целы) + TransientError с учётом стоимости (AC-S0-11)."""
    import processing

    doc = factories.DocumentFactory.create(status="processing")
    factories.InvoiceFactory.create(document=doc, number="СФ-OLD")
    db_session.commit()

    def boom(*a, **k):
        """Ломает вставку позиции внутри фазы B — эмулирует детерминированный сбой БД."""
        raise RuntimeError("db exploded mid-phase-B")
    monkeypatch.setattr(processing, "normalize_item", boom)

    with pytest.raises(processing.TransientError) as exc:
        persist_parse_result(db_session, doc.id, _outcome(cost="0.003"))
    assert exc.value.cost_usd == Decimal("0.003")   # стоимость фазы A сохранена в ошибке
    assert exc.value.paid_calls == 1

    db_session.expire_all()
    # rollback внутри persist откатил удаление старой СФ — данные не потеряны (AC-S0-1/11).
    numbers = [i.number for i in db_session.query(Invoice).filter(Invoice.document_id == doc.id).all()]
    assert numbers == ["СФ-OLD"]
```

- [ ] **Step 6: Запустить — убедиться, что падают**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-k 'persist' 2>&1"`
Expected: FAIL — `ImportError: cannot import name 'persist_parse_result'`.

- [ ] **Step 7: Реализовать `persist_parse_result`**

В `backend/processing.py` добавить импорты и функцию:

```python
from sqlalchemy.orm import Session

from crud.materials import UnknownMaterialType, VALID_CALC_ROLES, get_or_create_material_class
from crud.suppliers import get_or_create_supplier
from crud.units import load_alias_map, normalize_item
from models import Document, Invoice, InvoiceItem


def _dec(value):
    """LLM/JSON float → Decimal через str() (отсекает бинарную погрешность). None-safe."""
    return None if value is None else Decimal(str(value))


def _resolve_material_class_id(db: Session, item, *, document_id: int) -> int | None:
    """Резолвит material_class позиции в id (flush, без commit) — перенос из старого parse.

    Только для item_type='material' с непустым material_class. Неизвестный calc_role → 'base';
    неизвестный material_type → 'other'.
    """
    if item.item_type != "material" or not item.material_class:
        return None
    raw_role = str(item.calc_role or "base").strip().lower()
    if raw_role not in VALID_CALC_ROLES:
        logger.warning("[doc=%d] неизвестный calc_role=%r → 'base'", document_id, raw_role)
        raw_role = "base"
    try:
        mc = get_or_create_material_class(
            db, name=item.material_class, material_type=item.material_type or "other",
            calc_role=raw_role, commit=False,
        )
    except UnknownMaterialType:
        mc = get_or_create_material_class(
            db, name=item.material_class, material_type="other", calc_role=raw_role, commit=False,
        )
    return mc.id


def persist_parse_result(db: Session, doc_id: int, outcome: ParseOutcome) -> None:
    """Фаза B: в одной транзакции заменить СФ документа результатом парсинга.

    Удаляет старые СФ, резолвит поставщиков/классы (flush), вставляет новые СФ,
    ставит status='parsed', инкремент стоимости, единственный commit. Никаких
    промежуточных commit — инвариант транзакционности (§2.3/§2.4).

    Все выходы-ошибки НЕСУТ учёт стоимости outcome (фаза A уже оплачена → error-путь
    обязан начислить, инвариант §2.3). Детерминированный сбой (flush/insert) → явный
    rollback (сессия в failed state, без него последующая error-запись через ту же
    сессию упадёт PendingRollbackError) + TransientError с учётом. Сбой из commit
    (ambiguous) → тоже TransientError; условная error-запись (§2.3) разрулит, лёг ли swap.
    """
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if doc is None:
        raise PermanentError(f"Документ id={doc_id} не найден на фазе B",
                             cost_usd=outcome.cost_usd, paid_calls=outcome.paid_calls)

    try:
        for inv in list(doc.invoices):
            db.delete(inv)

        aliases = load_alias_map(db)
        for pinv in outcome.invoices:
            _inn = (pinv.supplier_inn.strip() or None) if pinv.supplier_inn else None
            _name = (pinv.supplier_name.strip() or None) if pinv.supplier_name else None
            if not _name:
                _inn = None
            supplier_id = None
            if _name:
                supplier = get_or_create_supplier(db, name=_name, inn=_inn)  # уже flush-only
                supplier_id, _name, _inn = supplier.id, supplier.name, supplier.inn

            invoice = Invoice(
                document_id=doc_id, supplier_id=supplier_id, number=pinv.number, date=pinv.date,
                supplier_name=_name, supplier_inn=_inn, vat_rate=_dec(pinv.vat_rate),
                ai_confidence=pinv.confidence,
            )
            db.add(invoice)
            db.flush()

            for item in pinv.items:
                mc_id = _resolve_material_class_id(db, item, document_id=doc_id)
                quantity, unit_price = _dec(item.quantity), _dec(item.unit_price)
                norm = normalize_item(item.unit, quantity, unit_price, aliases)
                db.add(InvoiceItem(
                    invoice_id=invoice.id, raw_name=item.raw_name, item_type=item.item_type,
                    material_class_id=mc_id, quantity=quantity, raw_unit=item.unit,
                    normalized_unit_id=norm.normalized_unit_id if norm else None,
                    normalized_quantity=norm.normalized_quantity if norm else None,
                    normalized_unit_price=norm.normalized_unit_price if norm else None,
                    unit_price=unit_price, amount=_dec(item.amount), vat_amount=_dec(item.vat_amount),
                ))

        doc.status = "parsed"
        doc.last_error = None
        doc.doc_type = "invoice"  # успешный разбор → документ точно СФ
        # (на error-пути doc_type НЕ трогается — см. write_processing_error: документ
        #  хранит живые старые СФ, флип invoice→unknown был бы противоречив.)
        # Атомарный SQL-инкремент (x = x + v) — защита от гонки параллельных разборов.
        doc.parse_cost_usd = Document.parse_cost_usd + outcome.cost_usd
        doc.parse_count = Document.parse_count + outcome.paid_calls
        db.commit()
    except ProcessingError:
        # verified-abort (Task 9) и doc-not-found уже несут cost — не оборачиваем.
        # Мутаций либо не было (raise до тела), либо откатятся close()/rollback вызывающего.
        raise
    except Exception as exc:  # noqa: BLE001 — детерминированный сбой ИЛИ ambiguous commit
        db.rollback()
        raise TransientError(f"Ошибка сохранения (фаза B): {exc}",
                             cost_usd=outcome.cost_usd, paid_calls=outcome.paid_calls) from exc
    logger.info(f"[doc={doc_id}] Фаза B: сохранено СФ {len(outcome.invoices)}, статус parsed")
```

> Примечание: `create_invoice` из `crud/documents.py` НЕ используется в фазе B (он коммитит на каждую СФ — P5). Логика вставки СФ воспроизведена здесь без промежуточных commit. Старый `create_invoice` остаётся для других вызывающих. `logger` определяется в Task 6 (module-level `logging.getLogger(__name__)`) — на этой задаче фаза B ещё не логирует ошибок вне уже импортированного `logger`; если Task 6 ещё не выполнен, временно добавить `import logging; logger = logging.getLogger(__name__)` в шапку `processing.py` (Task 6 его переиспользует, дубля не создавать).

- [ ] **Step 8: Запустить — убедиться, что проходят**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-k 'persist' 2>&1"`
Expected: PASS (4 теста: no_commit + 3 persist).

- [ ] **Step 9: Lint + commit**

```bash
git add backend/crud/materials.py backend/processing.py backend/tests/integration/test_processing_persist.py
git commit -m "feat(processing): фаза B persist_parse_result + no-commit резолв класса (S0-2, Q7)"
```

---

### Task 5: Async-обёртки S3 (S0-6) — до deskew/ядра

**Files:**
- Modify: `backend/s3.py` (async-обёртки на `anyio.to_thread`)
- Test: `backend/tests/unit/test_s3_async.py`

**Interfaces:**
- Produces: `async def upload_file_async(file_bytes, object_name) -> str`, `async def download_file_async(object_name) -> bytes`, `async def delete_file_async(object_name) -> None` — обёртки над sync-boto3 через `anyio.to_thread.run_sync`.

> **Почему первой (F2):** обёртки создаются ДО их первого использования (Task 6 ядро → download; Task 7 deskew → upload/download). Так исключён коммит с импортом ещё не существующих символов. `in_memory_s3`-фикстура патчит sync `s3.download_file`/`s3.upload_file`; async-обёртки вызывают эти sync-функции ВНУТРИ `s3.py` (module-global lookup), поэтому автоматически видят патч — отдельного патча async-обёрток не требуется (F6).

- [ ] **Step 1: Написать падающий тест**

Create `backend/tests/unit/test_s3_async.py`:

```python
"""Unit-тесты async-обёрток S3 (S0-6): sync-boto3 уходит в поток, не блокирует loop."""
import pytest


@pytest.mark.anyio
async def test_download_file_async_delegates(monkeypatch):
    """download_file_async возвращает то же, что sync download_file."""
    import s3

    monkeypatch.setattr(s3, "download_file", lambda name: b"bytes-for-" + name.encode())
    result = await s3.download_file_async("k/x.pdf")
    assert result == b"bytes-for-k/x.pdf"


@pytest.mark.anyio
async def test_upload_file_async_delegates(monkeypatch):
    """upload_file_async возвращает ключ, как sync upload_file."""
    import s3

    monkeypatch.setattr(s3, "upload_file", lambda b, name: name)
    assert await s3.upload_file_async(b"data", "k/y.pdf") == "k/y.pdf"
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-unit-k 's3_async or file_async_delegates' 2>&1"`
Expected: FAIL — `AttributeError: module 's3' has no attribute 'download_file_async'`.

- [ ] **Step 3: Добавить async-обёртки в `s3.py`**

```python
import anyio


async def upload_file_async(file_bytes: bytes, object_name: str) -> str:
    """Async-обёртка upload_file: sync-boto3 уходит в поток, event loop свободен (S0-6)."""
    return await anyio.to_thread.run_sync(upload_file, file_bytes, object_name)


async def download_file_async(object_name: str) -> bytes:
    """Async-обёртка download_file через поток."""
    return await anyio.to_thread.run_sync(download_file, object_name)


async def delete_file_async(object_name: str) -> None:
    """Async-обёртка delete_file через поток."""
    await anyio.to_thread.run_sync(delete_file, object_name)
```

- [ ] **Step 4: Запустить тесты — PASS**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-unit-k 'file_async_delegates' 2>&1"`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
git add backend/s3.py backend/tests/unit/test_s3_async.py
git commit -m "feat(processing): async-обёртки S3 через anyio.to_thread (S0-6)"
```

---

### Task 6: Ядро `run_processing_attempt` + `process_document` + инъекция фабрики + условная error-запись (S0-3, S0-4, §2.3)

**Files:**
- Modify: `backend/processing.py` (добавить `write_processing_error`, `run_processing_attempt`, `process_document`, `get_processing_session_factory`)
- Modify: `backend/tests/conftest.py` (фикстура `session_factory_test`)
- Test: `backend/tests/integration/test_process_document.py`, `backend/tests/integration/test_conditional_error_write_concurrency.py`

**Interfaces:**
- Consumes: `parse_pdf` (Task 3, локальный импорт из `pdf_parser`), `persist_parse_result` (Task 4), доменные ошибки (Task 2), `s3.download_file_async` (Task 5), `SessionLocal` (`database.py`, поздний резолв).
- Produces:
  - `def get_processing_session_factory()` — FastAPI-dependency, возвращает `SessionLocal` (в тестах переопределяется на тест-фабрику — F1).
  - `def write_processing_error(session_factory, doc_id, message, *, cost_usd, paid_calls, retries=3) -> None` — идемпотентная условная error-запись (`WHERE status='processing'`); ретрай ТОЛЬКО при connection-related сбое, детерминированные ошибки пробрасываются (F8).
  - `async def run_processing_attempt(session_factory, doc_id, *, mode, pdf_bytes=None) -> None` — одна попытка: скачать байты (reparse), фаза A → фаза B; доменные ошибки НЕ гасит. deskew-ветка добавится в Task 7.
  - `async def process_document(doc_id, *, mode, pdf_bytes=None, session_factory=None, reraise=False) -> None` — обёртка ступени 0/1; `session_factory=None` → поздний резолв `SessionLocal` (F1); гасит доменные ошибки и `CancelledError` в терминальный `error`.

- [ ] **Step 1: Написать падающие тесты ядра**

Create `backend/tests/integration/test_process_document.py`:

```python
"""Тесты ядра обработки: process_document, error-пути, CancelledError (S0-3, S0-4, §2.3)."""
import asyncio
from decimal import Decimal

import pytest

from models import Document, Invoice
from processing import process_document, write_processing_error


def _proc_doc(factories, db_session, s3, s3_key="k/p.pdf"):
    """Документ в статусе processing с байтами в in-memory S3 — типовая заготовка."""
    doc = factories.DocumentFactory.create(s3_key=s3_key, status="processing")
    s3[s3_key] = b"%PDF"
    db_session.commit()
    return doc


@pytest.mark.anyio
async def test_process_document_success_sets_parsed(
    factories, db_session, in_memory_s3, mock_openrouter, session_factory_test,
):
    """Успешный reparse ставит parsed и создаёт СФ."""
    doc = _proc_doc(factories, db_session, in_memory_s3)
    await process_document(doc.id, mode="parse", session_factory=session_factory_test)
    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "parsed"


@pytest.mark.anyio
async def test_process_document_permanent_error_keeps_old_invoices(
    factories, db_session, in_memory_s3, mock_openrouter, session_factory_test,
):
    """Провал сверки итогов → error, старые СФ невредимы, last_error заполнен (AC-S0-1)."""
    mock_openrouter.use_scenario("incomplete_totals")
    doc = _proc_doc(factories, db_session, in_memory_s3)
    factories.InvoiceFactory.create(document=doc, number="СФ-OLD")
    db_session.commit()

    await process_document(doc.id, mode="parse", session_factory=session_factory_test)

    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "error"
    assert saved.last_error
    assert saved.parse_count == 1  # платный вызов учтён
    assert [i.number for i in db_session.query(Invoice).filter(Invoice.document_id == doc.id)] == ["СФ-OLD"]


@pytest.mark.anyio
async def test_process_document_phase_b_failure_writes_error_with_cost(
    factories, db_session, in_memory_s3, mock_openrouter, monkeypatch, session_factory_test,
):
    """Сбой фазы B ПОСЛЕ оплаченной фазы A (через process_document) → status=error,
    last_error заполнен, стоимость фазы A начислена ровно один раз, старые СФ целы (AC-S0-11 e2e, F5)."""
    import processing

    doc = _proc_doc(factories, db_session, in_memory_s3)
    factories.InvoiceFactory.create(document=doc, number="СФ-OLD")
    db_session.commit()

    def boom(*a, **k):
        """Ломает вставку позиции внутри фазы B (детерминированный сбой БД)."""
        raise RuntimeError("db exploded mid-phase-B")
    monkeypatch.setattr(processing, "normalize_item", boom)

    await process_document(doc.id, mode="parse", session_factory=session_factory_test)

    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "error"
    assert saved.last_error
    assert saved.parse_count == 1                       # detect не было; фаза A оплачена ровно раз
    assert saved.parse_cost_usd > Decimal(0)            # стоимость фазы A начислена
    numbers = [i.number for i in db_session.query(Invoice).filter(Invoice.document_id == doc.id)]
    assert numbers == ["СФ-OLD"]                         # swap откатан, старые СФ целы


@pytest.mark.anyio
async def test_process_document_cancelled_sets_error(
    factories, db_session, in_memory_s3, monkeypatch, session_factory_test,
):
    """CancelledError посреди парсинга → error + last_error='Обработка прервана', re-raise (AC-S0-2)."""
    import pdf_parser  # run_processing_attempt берёт parse_pdf локально из pdf_parser (F6)

    doc = _proc_doc(factories, db_session, in_memory_s3)

    async def boom(*a, **k):
        """Эмулирует отмену таски внутри фазы A."""
        raise asyncio.CancelledError()
    monkeypatch.setattr(pdf_parser, "parse_pdf", boom)

    with pytest.raises(asyncio.CancelledError):
        await process_document(doc.id, mode="parse", session_factory=session_factory_test)

    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "error"
    assert saved.last_error == "Обработка прервана"


def test_write_error_conditional_skips_when_already_parsed(
    factories, db_session, session_factory_test,
):
    """Условная error-запись при уже parsed → rowcount 0, статус не затёрт (AC-S0-12)."""
    doc = factories.DocumentFactory.create(status="parsed")
    db_session.commit()

    write_processing_error(session_factory_test, doc.id, "боль", cost_usd=Decimal("0.001"), paid_calls=1)

    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "parsed"           # не затёрт
    assert saved.parse_count == 0             # стоимость не начислена повторно


def test_write_error_reraises_non_connection_operational_error():
    """Не-connection OperationalError (deadlock) → одна попытка, проброс, без глотания (F8)."""
    from contextlib import contextmanager
    from unittest.mock import MagicMock

    from sqlalchemy.exc import OperationalError

    attempts = {"n": 0}

    @contextmanager
    def factory():
        """Фейковая фабрика: execute всегда бросает детерминированный OperationalError."""
        db = MagicMock()

        def _execute(*a, **k):
            """Эмулирует deadlock — OperationalError без connection_invalidated/SQLSTATE 08."""
            attempts["n"] += 1
            raise OperationalError("UPDATE documents ...", {}, Exception("deadlock detected"))

        db.execute.side_effect = _execute
        yield db

    with pytest.raises(OperationalError):
        write_processing_error(factory, 1, "x", cost_usd=Decimal("0.001"), paid_calls=1)
    assert attempts["n"] == 1  # детерминированная ошибка — без ретраев


def test_write_error_retries_on_connection_loss():
    """connection_invalidated=True → ретраит до retries, затем critical-лог без проброса (F8)."""
    from contextlib import contextmanager
    from unittest.mock import MagicMock

    from sqlalchemy.exc import OperationalError

    attempts = {"n": 0}

    @contextmanager
    def factory():
        """Фейковая фабрика: execute бросает connection-invalidated ошибку."""
        db = MagicMock()

        def _execute(*a, **k):
            """Эмулирует обрыв соединения из commit — ретраебельно."""
            attempts["n"] += 1
            err = OperationalError("UPDATE documents ...", {}, Exception("server closed connection"))
            err.connection_invalidated = True
            raise err

        db.execute.side_effect = _execute
        yield db

    # Все попытки — потеря соединения → исчерпание ретраев → critical-лог, исключение НЕ пробрасывается.
    write_processing_error(factory, 1, "x", cost_usd=Decimal("0.001"), paid_calls=1, retries=3)
    assert attempts["n"] == 3
```

- [ ] **Step 2: Добавить фикстуру `session_factory_test` (инъекция сессии, S1-3)**

В `backend/tests/conftest.py` добавить — фабрика, привязанная к тестовой connection (иначе фоновая логика не увидит незакоммиченные данные теста):

```python
@pytest.fixture
def session_factory_test(db_session):
    """Фабрика сессий, отдающая ту же транзакционную тест-сессию.

    process_document по контракту открывает сессию сам через session_factory;
    в тестах инжектим фабрику, возвращающую db_session, чтобы обработка видела
    данные теста и откатывалась вместе с ним. Контекст-менеджер (__enter__/__exit__)
    имитирован, но close/commit проксируются на общую сессию без реального закрытия.
    """
    from contextlib import contextmanager

    @contextmanager
    def factory():
        """Контекст-менеджер, отдающий общую тест-сессию без реального закрытия."""
        yield db_session  # не закрываем — управляет фикстура db_session

    return factory
```

> Примечание: `process_document`/`write_processing_error` используют `session_factory` как контекст-менеджер (`with session_factory() as db:`). `SessionLocal` в проде поддерживает `with` начиная с SQLAlchemy 1.4+. Фикстура повторяет этот интерфейс.

- [ ] **Step 3: Написать конкурентный тест AC-S0-13 (два реальных соединения, F5)**

Create `backend/tests/integration/test_conditional_error_write_concurrency.py` — этот тест НЕ может использовать транзакционную `db_session` (savepoint не виден другому соединению); ему нужны два независимых реальных соединения. Берём их из существующей фикстуры `db_engine` (гарантирует накат миграций — свой `create_engine` мог бы попасть в неподготовленную БД) через отдельный `sessionmaker`:

```python
"""AC-S0-13: КОНКУРЕНТНАЯ условная error-запись на двух реальных соединениях.

Проверяет реальную гонку, а не пост-фактум (это и отличает тест от AC-S0-12):
T1 держит строку документа под SELECT ... FOR UPDATE и НЕ коммитит swap в parsed;
T2 (в отдельном потоке) шлёт условный UPDATE ... WHERE status='processing' и
БЛОКИРУЕТСЯ на строке. После commit T1 (swap лёг) Postgres перечитывает предикат
(EvalPlanQual) на новой версии строки → он ложен → rowcount 0. Итог: parsed не
затёрт, parse_count/parse_cost_usd не задвоены (§2.3).
"""
import threading
import time
from contextlib import contextmanager
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from processing import write_processing_error


def test_conditional_write_waits_then_skips_when_swap_lands(db_engine):
    """T2-бэкенд РЕАЛЬНО ждёт row lock T1 (наблюдаем через pg_stat_activity), после коммита
    parsed видит предикат ложным → rowcount 0 (AC-S0-13, детерминированно, без тайм-эвристики)."""
    Factory = sessionmaker(bind=db_engine)

    # --- setup: документ в processing (уникальный id вне фабрик, явный cleanup) ---
    setup = Factory()
    try:
        setup.execute(text("INSERT INTO projects (id, name) VALUES (999001, 'ac-s0-13') "
                           "ON CONFLICT (id) DO NOTHING"))
        doc_id = setup.execute(text(
            "INSERT INTO documents (project_id, filename, s3_key, status, doc_type, "
            "parse_count, parse_cost_usd) "
            "VALUES (999001, 'ac13.pdf', 'k/ac13.pdf', 'processing', 'invoice', 1, 0.005) "
            "RETURNING id"
        )).scalar_one()
        setup.commit()
    finally:
        setup.close()

    t2_ready = threading.Event()      # PID backend'а T2 захвачен и опубликован
    t2_done = threading.Event()
    t2_pid: dict = {}
    t2_result: dict = {}

    def t2_worker():
        """T2: фиксирует backend PID на СВОЁМ соединении, затем шлёт условный UPDATE.

        write_processing_error гоняется через фабрику, отдающую ЭТО ЖЕ соединение, чтобы
        заблокированный на row lock backend имел известный PID (видимый в pg_stat_activity).
        """
        s = Factory()
        try:
            t2_pid["pid"] = s.execute(text("SELECT pg_backend_pid()")).scalar_one()
            t2_ready.set()

            @contextmanager
            def pinned_factory():
                """Отдаёт то же соединение s — условный UPDATE идёт под известным PID."""
                yield s

            write_processing_error(pinned_factory, doc_id, "поздняя ошибка",
                                   cost_usd=Decimal("0.003"), paid_calls=1)
            t2_result["ok"] = True
        except Exception as exc:  # noqa: BLE001
            t2_result["error"] = exc
        finally:
            s.close()
            t2_done.set()

    worker = threading.Thread(target=t2_worker)
    worker_started = False
    try:
        # --- T1: берём row lock и делаем swap в parsed, НЕ коммитим ---
        t1 = Factory()
        try:
            t1.execute(text("SELECT id FROM documents WHERE id=:id FOR UPDATE"), {"id": doc_id}).one()
            t1.execute(text("UPDATE documents SET status='parsed' WHERE id=:id"), {"id": doc_id})

            worker.start()
            worker_started = True
            assert t2_ready.wait(timeout=5), "T2 не захватил backend PID"

            # Детерминированно ждём, пока backend T2 РЕАЛЬНО встанет в ожидание блокировки
            # PostgreSQL (wait_event_type='Lock') — это доказывает, что условный UPDATE
            # отправлен и упёрся в row lock T1, а не «поток просто стартовал».
            observer = Factory()
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    row = observer.execute(text(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"
                    ), {"pid": t2_pid["pid"]}).first()
                    observer.rollback()  # не держим снапшот между опросами
                    if row and row.wait_event_type == "Lock":
                        break
                    time.sleep(0.05)
                else:
                    raise AssertionError("T2-бэкенд не вышел в ожидание блокировки (Lock) за 10с")
            finally:
                observer.close()

            t1.commit()  # swap лёг → T2 разблокируется, EvalPlanQual перечитает предикат
        finally:
            t1.close()

        assert t2_done.wait(timeout=5)
        worker.join(timeout=5)
        assert not worker.is_alive(), "T2-поток не завершился"
        assert t2_result.get("ok"), f"T2 упал вместо rowcount 0: {t2_result.get('error')!r}"

        check = Factory()
        try:
            r = check.execute(text("SELECT status, parse_count, parse_cost_usd "
                                   "FROM documents WHERE id=:id"), {"id": doc_id}).one()
            assert r.status == "parsed"            # error-запись не затёрла swap
            assert r.parse_count == 1              # стоимость не задвоена (rowcount 0)
            assert r.parse_cost_usd == Decimal("0.005")
        finally:
            check.close()
    finally:
        # join только если поток реально стартовал — иначе join() бросит RuntimeError
        # и замаскирует исходное исключение (сбой до worker.start()).
        if worker_started:
            worker.join(timeout=5)
        cleanup = Factory()
        try:
            cleanup.execute(text("DELETE FROM documents WHERE id=:id"), {"id": doc_id})
            cleanup.execute(text("DELETE FROM projects WHERE id=999001"))
            cleanup.commit()
        finally:
            cleanup.close()
```

> Реализатору: (1) сверить имена колонок/таблиц (`projects.name`, `documents.filename` и т.д.) с реальной схемой перед запуском; при расхождении — поправить INSERT'ы. (2) Тест намеренно НЕ использует `db_session` (savepoint дал бы ложное покрытие и не выразил бы двух-соединённую гонку), но зависит от `db_engine`, чтобы миграции точно были накатаны. (3) Синхронизация — БЕЗ фиксированных задержек: `write_processing_error` открывает своё соединение через фабрику, поэтому блокируется backend с ЭТИМ PID (не тем, что можно снять заранее) — T2 закрепляет соединение (`pinned_factory`) и публикует его PID, а основной поток опрашивает `pg_stat_activity` до `wait_event_type='Lock'` для этого PID. Это доказывает уже СОСТОЯВШЕЕСЯ ожидание блокировки (в отличие от `before_cursor_execute`, где остаётся микро-окно между callback и фактическим `cursor.execute`). `worker.join(timeout=5)` перед cleanup гарантирует, что поток завершён.

- [ ] **Step 4: Запустить — убедиться, что падают**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-k 'process_document or conditional_write or write_error' 2>&1"`
Expected: FAIL — `ImportError: cannot import name 'write_processing_error'` / `process_document`.

- [ ] **Step 5: Реализовать error-запись, попытку, обёртку и фабрику-зависимость**

В `backend/processing.py` добавить импорты и функции:

```python
import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

logger = logging.getLogger(__name__)  # если ещё не объявлен в Task 4 — единственное объявление

_LAST_ERROR_MAXLEN = 500


def get_processing_session_factory():
    """FastAPI-dependency: фабрика сессий для инлайн-обработки (F1).

    Возвращает SessionLocal (поздний импорт — не связываем на этапе модуля, чтобы
    тестовый override и патч database.SessionLocal работали). В тестах переопределяется
    через app.dependency_overrides на тест-фабрику, чтобы обработка видела тест-данные.
    """
    from database import SessionLocal
    return SessionLocal


def _is_connection_error(exc: DBAPIError) -> bool:
    """Потеря соединения по SQLAlchemy-флагу ИЛИ SQLSTATE класса 08 (connection exception).

    Только такие ошибки из commit ретраебельны (запись идемпотентна). Прочие
    OperationalError (deadlock 40P01, lock_timeout, statement cancellation 57014, …)
    и любой другой DBAPIError детерминированы → пробрасываются вызывающим немедленно (F8).
    """
    sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    return bool(exc.connection_invalidated or (sqlstate and str(sqlstate).startswith("08")))


def write_processing_error(session_factory, doc_id: int, message: str, *,
                           cost_usd: Decimal, paid_calls: int, retries: int = 3) -> None:
    """Идемпотентная условная error-запись (§2.3).

    UPDATE ... WHERE status='processing' — при уже закоммитившемся swap (ambiguous
    commit) предикат ложен после ожидания блокировки (EvalPlanQual), rowcount 0.
    Ретраим ТОЛЬКО потерю соединения из самого commit (`connection_invalidated` ИЛИ
    SQLSTATE класса 08 — см. `_is_connection_error`) — запись идемпотентна. Прочие ошибки,
    ВКЛЮЧАЯ не-connection `OperationalError` (deadlock 40P01, lock_timeout, statement
    cancellation 57014) и любой другой `DBAPIError`/Exception, детерминированы → НЕ глотаем,
    пробрасываем, чтобы баг падал в тестах, а не оставлял документ processing молча (F8).
    Исчерпание connection-ретраев → лог critical, документ остаётся processing (доберёт
    startup-sweep S1-4); стоимость этой попытки теряется (at-most-once).
    """
    # doc_type НЕ трогаем: при parse-then-swap error-документ хранит живые старые СФ,
    # флип doc_type invoice→unknown у документа с СФ противоречив (§2.3 SQL его не содержит).
    sql = text(
        "UPDATE documents SET status='error', last_error=:msg, "
        "parse_cost_usd = parse_cost_usd + :cost, parse_count = parse_count + :calls "
        "WHERE id=:id AND status='processing'"
    )
    params = {"msg": message[:_LAST_ERROR_MAXLEN], "cost": cost_usd, "calls": paid_calls, "id": doc_id}
    for attempt in range(1, retries + 1):
        try:
            with session_factory() as db:
                result = db.execute(sql, params)
                db.commit()
            if result.rowcount == 0:
                # rowcount 0: swap уже лёг (parsed), ИЛИ документ удалён/уже error —
                # различить постфактум нельзя; во всех случаях повторно писать нечего.
                logger.warning(f"[doc={doc_id}] error-запись пропущена (rowcount 0): "
                               f"документ не в статусе processing (swap лёг / удалён / уже error)")
            return
        except DBAPIError as exc:
            # Только потеря соединения ретраебельна; прочий DBAPIError (в т.ч. deadlock/
            # lock_timeout/cancel OperationalError, ProgrammingError) детерминирован — проброс.
            if not _is_connection_error(exc):
                raise
            logger.warning(f"[doc={doc_id}] error-запись, попытка {attempt}/{retries} "
                           f"не удалась (потеря соединения): {exc}")
    logger.critical(f"[doc={doc_id}] error-запись НЕ выполнена: БД недоступна. "
                    f"Документ остаётся processing до рестарта/ручного восстановления; "
                    f"стоимость ${cost_usd} не учтена.")


async def run_processing_attempt(session_factory, doc_id: int, *, mode: str,
                                 pdf_bytes: bytes | None = None) -> None:
    """Одна попытка обработки: (скачать байты) → фаза A → фаза B.

    Доменные ошибки (Transient/Permanent) НЕ гасит — пробрасывает наверх с учётом
    стоимости. Это ядро, неизменное между ступенями (обёртки завершения — разные).
    deskew-режим будет расширен в Task 7 (детект + коррекция до фазы A).
    """
    from pdf_parser import parse_pdf  # локальный импорт против кругового; патчится через pdf_parser (F6)
    from s3 import download_file_async

    if pdf_bytes is None:
        with session_factory() as db:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if doc is None or not doc.s3_key:
                raise PermanentError(f"Документ id={doc_id} без s3_key")
            s3_key = doc.s3_key
        try:
            pdf_bytes = await download_file_async(s3_key)
        except Exception as exc:  # noqa: BLE001
            raise TransientError(f"Не удалось скачать PDF из S3: {exc}") from exc

    outcome = await parse_pdf(pdf_bytes, document_id=doc_id)
    with session_factory() as db:
        persist_parse_result(db, doc_id, outcome)


async def process_document(doc_id: int, *, mode: str, pdf_bytes: bytes | None = None,
                           session_factory=None, reraise: bool = False) -> None:
    """Обёртка ступени 0/1: выполнить попытку, любую доменную ошибку → терминальный error.

    session_factory=None → поздний резолв SessionLocal (F1: не связываем дефолт на этапе
    def — это открывало сессию на реальном dev-DATABASE_URL в тестах и плохо патчилось).

    Всегда пишет status='error' + last_error через условную запись. Если reraise=True
    И ошибка несёт http_status (только ориентация deskew: 413/502) — после записи
    пробрасывает её, чтобы эндпоинт смапил на прежний HTTP-код (AC-S0-8, поведение API
    на S0 не меняется). Ошибки парсинга (http_status=None) не пробрасываются → 200 + error.
    На S1 reraise=False (фоновой таске отвечать некому — контракт §2.2 не ломается).

    CancelledError (обрыв клиента / отмена таски) → error + 'Обработка прервана' + re-raise
    (детерминированный исход, AC-S0-2). Успех фиксируется внутри фазы B.
    """
    if session_factory is None:
        session_factory = get_processing_session_factory()
    try:
        await run_processing_attempt(session_factory, doc_id, mode=mode, pdf_bytes=pdf_bytes)
    except ProcessingError as exc:
        logger.warning(f"[doc={doc_id}] обработка завершилась ошибкой: {exc.message}")
        write_processing_error(session_factory, doc_id, exc.message,
                               cost_usd=exc.cost_usd, paid_calls=exc.paid_calls)
        if reraise and exc.http_status is not None:
            raise
    except asyncio.CancelledError:
        logger.warning(f"[doc={doc_id}] обработка прервана (CancelledError)")
        write_processing_error(session_factory, doc_id, "Обработка прервана",
                               cost_usd=Decimal(0), paid_calls=0)
        raise
    except Exception as exc:  # noqa: BLE001 — подлинно непредвиденное (не ProcessingError)
        logger.exception(f"[doc={doc_id}] непредвиденная ошибка обработки")
        write_processing_error(session_factory, doc_id, f"Ошибка обработки: {exc}",
                               cost_usd=Decimal(0), paid_calls=0)
```

> Примечание: детерминированный сбой и ambiguous commit фазы B `persist_parse_result` уже оборачивает в `TransientError` с учётом стоимости (Task 4), поэтому они приходят в ветку `except ProcessingError` (стоимость фазы A начисляется — это и проверяет `test_process_document_phase_b_failure_writes_error_with_cost`). Ветка `except Exception` — страховка от подлинно непредвиденного (баг вне фаз A/B); учёт нулевой, т.к. платного вызова в этой ветке не было.

- [ ] **Step 6: Запустить — убедиться, что проходят**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-k 'process_document or conditional_write or write_error' 2>&1"`
Expected: PASS (6 тестов process_document + 1 concurrency).

- [ ] **Step 7: Lint + commit**

```bash
git add backend/processing.py backend/tests/integration/test_process_document.py backend/tests/integration/test_conditional_error_write_concurrency.py backend/tests/conftest.py
git commit -m "feat(processing): ядро process_document + инъекция фабрики + условная error-запись (S0-3, S0-4, §2.3)"
```

---

### Task 7: deskew — доменные ошибки + учёт стоимости detect + разблокировка pdfium (S0-4 pdf_orientation, S0-9, S0-6)

**Files:**
- Modify: `backend/pdf_orientation.py` (`detect_rotations` возвращает `(rotations, cost)`, `usage` в payload; `deskew_pdf` возвращает `(bytes, rotations, cost)`; `HTTPException` → доменные ошибки; сбой `apply_rotations` несёт detect-cost; `anyio.to_thread` для рендера)
- Modify: `backend/processing.py` (deskew-ветка в `run_processing_attempt` + `_run_deskew` + `_is_not_found`)
- Test: `backend/tests/integration/test_process_document.py`

**Interfaces:**
- Consumes: `TransientError`/`PermanentError` (Task 2), `s3.download_file_async`/`upload_file_async` (Task 5).
- Produces: `async def detect_rotations(images) -> tuple[list[int], Decimal]`; `async def deskew_pdf(pdf_bytes) -> tuple[bytes, list[int], Decimal]`; в `processing.py` — `_run_deskew`, deskew-ветка `run_processing_attempt`.

> **Порядок (F2/F9):** deskew-внутренности готовы ДО свапа эндпоинтов (Task 8) — значит контракт 413/502 и S3-бэкап существуют к моменту переключения эндпоинтов, никаких временных `xfail`. Использует async-обёртки S3 из Task 5 (уже созданы).

- [ ] **Step 1: Написать падающие тесты (суммарная стоимость + сохранение detect-cost при сбое после detect)**

В `test_process_document.py`:

```python
@pytest.mark.anyio
async def test_deskew_sums_detect_and_parse_cost(
    factories, db_session, in_memory_s3, mock_openrouter, monkeypatch, session_factory_test,
):
    """deskew: parse_cost = detect + parse, parse_count += 2 (AC-S0-10, S0-9)."""
    import pdf_orientation

    doc = _proc_doc(factories, db_session, in_memory_s3)

    async def fake_deskew(pdf_bytes):
        """Возвращает (bytes, rotations, detect_cost) без реального vision-вызова."""
        return pdf_bytes, [0], Decimal("0.001")
    monkeypatch.setattr(pdf_orientation, "deskew_pdf", fake_deskew)

    await process_document(doc.id, mode="deskew", session_factory=session_factory_test)

    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "parsed"
    assert saved.parse_count == 2                       # detect + parse
    assert saved.parse_cost_usd > Decimal("0.001")      # detect + parse cost


@pytest.mark.anyio
async def test_deskew_carries_detect_cost_when_s3_write_fails(
    factories, db_session, in_memory_s3, mock_openrouter, monkeypatch, session_factory_test,
):
    """detect оплачен, но перезапись S3 после detect падает → error, detect cost учтён,
    parse_count += 1 (только detect, фаза A не достигнута) (F3)."""
    import pdf_orientation
    import s3

    doc = _proc_doc(factories, db_session, in_memory_s3)

    async def fake_deskew(pdf_bytes):
        """detect «нашёл» поворот → потребуется перезапись S3."""
        return b"%PDF-corrected", [270], Decimal("0.002")
    monkeypatch.setattr(pdf_orientation, "deskew_pdf", fake_deskew)

    async def boom_upload(file_bytes, object_name):
        """Эмулирует сбой S3-записи ПОСЛЕ оплаченного detect."""
        raise RuntimeError("S3 write failed")
    monkeypatch.setattr(s3, "upload_file_async", boom_upload)

    await process_document(doc.id, mode="deskew", session_factory=session_factory_test)

    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "error"
    assert saved.parse_count == 1                       # оплаченный detect учтён
    assert saved.parse_cost_usd == Decimal("0.002")     # detect cost не потерян
```

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-k 'deskew_sums or carries_detect_cost' 2>&1"`
Expected: FAIL — `deskew_pdf` возвращает 2-кортеж / cost не суммируется / detect-cost теряется при сбое S3.

- [ ] **Step 3: Обновить `pdf_orientation.py` — usage.cost + доменные ошибки + сохранение cost при сбое apply**

В `detect_rotations`: добавить `"usage": {"include": True}` в `payload`; при HTTP-сбое бросать `TransientError` (вместо `HTTPException(502)`); при `n > MAX_DESKEW_PAGES` — `PermanentError` (вместо `HTTPException(413)`); читать и возвращать cost:

```python
from decimal import Decimal

from processing import PermanentError, TransientError


async def detect_rotations(images: list[bytes]) -> tuple[list[int], Decimal]:
    """Один vision-запрос: per-page поворот 0/90/180/270 и стоимость вызова.

    Транспортный сбой/таймаут/не-2xx → TransientError (detect не оплачен, cost не читается);
    слишком много страниц → PermanentError. Непарсящееся СОДЕРЖИМОЕ при 200 → нули
    (безопасная деградация), но cost из usage возвращается (вызов был платным).
    """
    n = len(images)
    if n > MAX_DESKEW_PAGES:
        # http_status=413 — прежний код эндпоинта; на S0 доходит до клиента (AC-S0-8).
        raise PermanentError(f"Слишком много страниц для коррекции (> {MAX_DESKEW_PAGES})",
                             http_status=413)
    content = [{"type": "text", "text": _DETECT_PROMPT}]
    for img in images:
        b64 = base64.b64encode(img).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    payload = {
        "model": settings.AI_MODEL,
        "max_tokens": 200,
        "usage": {"include": True},  # S0-9: detect — платный вызов, стоимость учитывается
        "messages": [{"role": "user", "content": content}],
    }
    headers = {"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    timeout = httpx.Timeout(DETECT_TIMEOUT, connect=5.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning(f"detect_rotations: vision-запрос упал: {e}")
        # http_status=502 — прежний код эндпоинта; на S0 доходит до клиента (AC-S0-8).
        raise TransientError("Сервис распознавания ориентации недоступен", http_status=502) from e

    cost = Decimal(0)
    text = ""
    try:
        data = resp.json()
        cost = Decimal(str((data.get("usage") or {}).get("cost") or 0))
        text = data["choices"][0]["message"]["content"]
        m = re.search(r"\[[\d,\s]*\]", text)
        nums = json.loads(m.group(0)) if m else []
        allowed = {0, 90, 180, 270}
        rots = [v % 360 if (v % 360) in allowed else 0 for v in nums[:n]]
    except Exception:  # noqa: BLE001 — кривое содержимое не должно ронять
        rots = []
    rots += [0] * (n - len(rots))
    logger.info(f"detect_rotations: n={n}, rotations={rots}, cost=${cost}")
    return rots, cost
```

Обновить `deskew_pdf` (сбой `apply_rotations` после оплаченного detect несёт cost — F3):

```python
async def deskew_pdf(pdf_bytes: bytes) -> tuple[bytes, list[int], Decimal]:
    """render-for-detect → detect → селективный raster. Возвращает (bytes, rotations, detect_cost).

    detect уже оплачен к моменту apply_rotations; сбой растеризации оборачиваем в
    TransientError с detect-cost, чтобы стоимость не потерялась в generic-ветке (F3, §2.3).
    """
    images = await anyio.to_thread.run_sync(render_pages_for_detect, pdf_bytes)
    rotations, cost = await detect_rotations(images)
    if not any(r % 360 for r in rotations):
        return pdf_bytes, rotations, cost
    try:
        corrected = await anyio.to_thread.run_sync(apply_rotations, pdf_bytes, rotations)
    except Exception as exc:  # noqa: BLE001 — detect оплачен, cost не теряем
        raise TransientError(f"Не удалось применить коррекцию ориентации: {exc}",
                             cost_usd=cost, paid_calls=1) from exc
    return corrected, rotations, cost
```

Добавить `import anyio` и убрать `from fastapi import HTTPException` из шапки `pdf_orientation.py`.

> Примечание про круговой импорт: `pdf_orientation` теперь импортирует из `processing`, а `processing` в deskew-ветке импортирует `pdf_orientation` локально (внутри функции) — цикла на уровне модулей нет.

- [ ] **Step 4: Реализовать deskew-ветку в `run_processing_attempt` — S3-бэкап + учёт cost + сохранение cost при сбоях после detect**

В `backend/processing.py` добавить хелперы `_is_not_found`, `_run_deskew` и переписать `run_processing_attempt`:

```python
def _is_not_found(exc: Exception) -> bool:
    """S3 «нет объекта» vs транзиентный сбой (порт из routers/invoices старой версии)."""
    from botocore.exceptions import ClientError
    if isinstance(exc, FileNotFoundError):            # in-memory-фикстура тестов
        return True
    if isinstance(exc, ClientError):                  # MinIO/S3 в проде
        return exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NoSuchBucket")
    return False


async def _run_deskew(s3_key: str) -> tuple[bytes, Decimal, int]:
    """Коррекция ориентации от оригинала. Возвращает (pdf_для_парсинга, detect_cost, detect_calls).

    Источник — всегда {s3_key}.orig (идемпотентность повторных deskew). Различаем «нет
    бэкапа» (fallback на s3_key) и транзиентный сбой S3 (→ TransientError 502, чтобы не
    затереть настоящий оригинал). При ненулевых поворотах: одноразовый бэкап оригинала +
    перезапись s3_key исправленными байтами. file_hash не пересчитываем (Q6).

    ПОСЛЕ оплаченного detect любой S3-сбой оборачивается в TransientError с detect-cost —
    стоимость не теряется в generic-ветке process_document (F3, §2.3).
    """
    import pdf_orientation
    from s3 import download_file_async, upload_file_async

    orig_key = f"{s3_key}.orig"
    try:
        source_bytes = await download_file_async(orig_key)
        has_backup = True
    except Exception as e:  # noqa: BLE001 — до detect: сбой S3 не оплачен
        if not _is_not_found(e):
            raise TransientError("Хранилище временно недоступно", http_status=502) from e
        source_bytes = await download_file_async(s3_key)
        has_backup = False

    # deskew_pdf бросает TransientError ДО чтения cost при транспортном сбое detect
    # (тогда detect не оплачен); при сбое apply_rotations — уже С detect-cost (см. Step 3).
    corrected, rotations, detect_cost = await pdf_orientation.deskew_pdf(source_bytes)
    detect_calls = 1

    # Всё, что после успешного detect, оплачено — S3-сбой не должен обнулять учёт (F3).
    try:
        if any(r % 360 for r in rotations):
            if not has_backup:
                await upload_file_async(source_bytes, orig_key)   # одноразовый бэкап оригинала
            await upload_file_async(corrected, s3_key)            # перезапись основным ключом
            return corrected, detect_cost, detect_calls

        # Нули: коррекция не нужна ЭТИМ прогоном. Но если .orig уже существует (has_backup),
        # значит прошлый deskew исправил s3_key — а detect сейчас флейкнул в нули. Парсить
        # повёрнутый .orig нельзя (перезатрёт хороший набор СФ парсом кривого файла) — берём
        # текущий s3_key (исправленную версию). Без бэкапа .orig == s3_key, source и есть текущий.
        if has_backup:
            current = await download_file_async(s3_key)
            return current, detect_cost, detect_calls
        return source_bytes, detect_cost, detect_calls
    except ProcessingError:
        raise
    except Exception as exc:  # noqa: BLE001 — S3-сбой ПОСЛЕ оплаченного detect
        raise TransientError(f"Ошибка S3 после коррекции ориентации: {exc}",
                             cost_usd=detect_cost, paid_calls=detect_calls) from exc


async def run_processing_attempt(session_factory, doc_id: int, *, mode: str,
                                 pdf_bytes: bytes | None = None) -> None:
    """Одна попытка: (скачать / deskew) → фаза A → фаза B. Доменные ошибки пробрасывает."""
    from pdf_parser import parse_pdf
    from s3 import download_file_async

    detect_cost = Decimal(0)
    detect_calls = 0

    if pdf_bytes is None:
        with session_factory() as db:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if doc is None or not doc.s3_key:
                raise PermanentError(f"Документ id={doc_id} без s3_key")
            s3_key = doc.s3_key
        if mode == "deskew":
            pdf_bytes, detect_cost, detect_calls = await _run_deskew(s3_key)
        else:
            try:
                pdf_bytes = await download_file_async(s3_key)
            except Exception as exc:  # noqa: BLE001
                raise TransientError(f"Не удалось скачать PDF из S3: {exc}") from exc

    try:
        outcome = await parse_pdf(pdf_bytes, document_id=doc_id)
    except ProcessingError as exc:
        # Составная попытка (§2.5): прибавляем оплаченный detect к ошибке парсинга.
        exc.cost_usd = exc.cost_usd + detect_cost
        exc.paid_calls = exc.paid_calls + detect_calls
        raise

    outcome.cost_usd = outcome.cost_usd + detect_cost
    outcome.paid_calls = outcome.paid_calls + detect_calls
    with session_factory() as db:
        persist_parse_result(db, doc_id, outcome)
```

> Примечания: (1) upload всегда `mode="parse"` с `pdf_bytes` — S3-скачивание/deskew только при `pdf_bytes is None` (reparse/deskew). (2) Порядок S3-записи — ДО фазы B (как в старом эндпоинте): провал парсинга на исправленном файле не делает коррекцию вредной, а `.orig` гарантирует идемпотентность. (3) `_is_not_found` дублирует функцию из `routers/invoices.py`; она осиротеет после Task 8 — удалить из роутера там же.

- [ ] **Step 5: Запустить целевые тесты — PASS**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-k 'deskew_sums or carries_detect_cost' 2>&1"`
Expected: PASS.

- [ ] **Step 6: Lint + commit**

```bash
git add backend/pdf_orientation.py backend/processing.py backend/tests/integration/test_process_document.py
git commit -m "feat(processing): deskew на доменных ошибках + учёт стоимости detect + разблокировка pdfium (S0-4, S0-9, S0-6)"
```

---

### Task 8: Guard-переход + переключение эндпоинтов на `process_document` (S0-5, свап)

**Files:**
- Modify: `backend/crud/documents.py` (добавить `try_acquire_processing`)
- Modify: `backend/routers/invoices.py:165-335` (`_reparse_from_s3` удаляется; `upload_pdf`, `reparse_document`, `deskew_reparse_document` переписываются на guard + `process_document` через DI-фабрику; `_is_not_found` удаляется — переехал в `processing`)
- Modify: `backend/pdf_parser.py` (удалить старый `parse_invoice_pdf` — заменён фазами A/B)
- Modify: `backend/tests/conftest.py` (`client`-фикстура переопределяет `get_processing_session_factory` на тест-фабрику — F1)
- Test: `backend/tests/integration/test_invoices.py` (существующие + 409 + адаптированные deskew-тесты, ЗЕЛЁНЫЕ без xfail)

**Interfaces:**
- Consumes: `process_document`, `get_processing_session_factory` (Task 6), async-обёртки S3 (Task 5).
- Produces: `def try_acquire_processing(db, doc_id, run_id=None) -> bool` — атомарный `UPDATE ... WHERE status != 'processing'` + commit; True если захватили.

> **Порядок (F9):** deskew-внутренности уже готовы (Task 7), поэтому эндпоинты переключаются сразу на финальный контракт — БЕЗ временного `xfail`. Deskew-тесты адаптируются под новые внутренности и остаются зелёными в этой же задаче.

- [ ] **Step 1: Переопределить `get_processing_session_factory` в `client`-фикстуре (F1)**

В `backend/tests/conftest.py`, в фикстуре `client`, добавить зависимость `session_factory_test` и переопределение (иначе эндпоинт-тесты, доходящие до `process_document`, откроют сессию на реальном dev-`DATABASE_URL`):

```python
@pytest.fixture
def client(db_session, in_memory_s3, session_factory_test) -> Iterator:
    ...
    from processing import get_processing_session_factory
    ...
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_processing_session_factory] = lambda: session_factory_test
    ...
```

(остальное тело `client` без изменений; `app.dependency_overrides.clear()` в конце уже снимает всё.)

- [ ] **Step 2: Написать падающий тест на 409 при параллельной обработке**

В `backend/tests/integration/test_invoices.py` добавить:

```python
def test_reparse_returns_409_when_already_processing(client, factories, in_memory_s3):
    """Reparse документа, уже находящегося в processing → 409 (AC-S0-4)."""
    doc = factories.DocumentFactory.create(s3_key="k/busy.pdf", status="processing")
    in_memory_s3["k/busy.pdf"] = b"%PDF"
    resp = client.post(f"/api/invoices/documents/{doc.id}/reparse")
    assert resp.status_code == 409
```

- [ ] **Step 3: Запустить — убедиться, что падает**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-k 'already_processing' 2>&1"`
Expected: FAIL — сейчас reparse не проверяет `processing`, вернёт 200/500.

- [ ] **Step 4: Добавить `try_acquire_processing` в `crud/documents.py`**

```python
from sqlalchemy import text


def try_acquire_processing(db, doc_id: int, run_id: str | None = None) -> bool:
    """Атомарно перевести документ в processing, если он ещё не там (guard S0-5).

    Коммитит немедленно — иначе переход не виден другим сессиям (409 не сработает,
    а фоновая таска на S1 не увидит processing). Возвращает True, если захватили.
    """
    result = db.execute(
        text("UPDATE documents SET status='processing', processing_started_at=now(), "
             "processing_run_id=:rid, last_error=NULL "
             "WHERE id=:id AND status != 'processing'"),
        {"id": doc_id, "rid": run_id},
    )
    db.commit()
    return result.rowcount == 1
```

- [ ] **Step 5: Переписать эндпоинты в `routers/invoices.py`**

Удалить `_reparse_from_s3` (строки ~165-204) и `_is_not_found` (переехал в `processing`) целиком. Переписать три эндпоинта. `upload_pdf` (S0-6 для upload: убираем per-request `ensure_bucket` — bucket гарантирован lifespan'ом; запись через `upload_file_async` — F2):

```python
@router.post("/upload")
async def upload_pdf(
    project_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    session_factory=Depends(get_processing_session_factory),
):
    """Загрузить PDF: сохранить в S3, создать документ (pending), обработать инлайн."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Только PDF-файлы")

    file_bytes = await file.read()
    now = datetime.now(UTC)
    object_name = f"{now.year}/{now.month:02d}/{uuid.uuid4().hex}_{file.filename}"
    try:
        # ensure_bucket() не в запросе — bucket создаётся в lifespan (S0-6, не блокируем loop).
        await upload_file_async(file_bytes, object_name)
    except Exception:
        logger.exception("Upload: ошибка загрузки в S3")
        raise HTTPException(status_code=500, detail="Не удалось сохранить файл в хранилище")

    doc = create_document(db, project_id, file.filename, object_name)
    if not try_acquire_processing(db, doc.id):
        raise HTTPException(status_code=409, detail="Документ уже обрабатывается")

    from processing import process_document
    await process_document(doc.id, mode="parse", pdf_bytes=file_bytes, session_factory=session_factory)

    db.expire_all()
    doc = get_document(db, doc.id)
    return _serialize_document(doc)
```

`reparse_document`:

```python
@router.post("/documents/{doc_id}/reparse")
async def reparse_document(
    doc_id: int,
    db: Session = Depends(get_db),
    session_factory=Depends(get_processing_session_factory),
):
    """Повторить парсинг документа из S3 (parse-then-swap, старые СФ переживают ошибку)."""
    doc = get_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    if not doc.s3_key:
        raise HTTPException(status_code=400, detail="PDF недоступен в хранилище")
    if any(inv.verified for inv in doc.invoices):
        raise HTTPException(status_code=409, detail="Документ содержит подтверждённые СФ — снимите подтверждение перед повторным разбором")
    if not try_acquire_processing(db, doc_id):
        raise HTTPException(status_code=409, detail="Документ уже обрабатывается")

    from processing import process_document
    await process_document(doc_id, mode="parse", session_factory=session_factory)

    db.expire_all()
    return _serialize_document(get_document(db, doc_id))
```

`deskew_reparse_document` — `reraise=True` + маппинг `http_status` сохраняют прежние 413/502 (AC-S0-8):

```python
@router.post("/documents/{doc_id}/deskew-reparse")
async def deskew_reparse_document(
    doc_id: int,
    db: Session = Depends(get_db),
    session_factory=Depends(get_processing_session_factory),
):
    """Коррекция ориентации страниц + переразбор. Ошибки ориентации (413/502) доходят
    прежним HTTP-кодом; ошибки парсинга → документ в error + 200 (AC-S0-8)."""
    doc = get_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    if not doc.s3_key:
        raise HTTPException(status_code=400, detail="PDF недоступен в хранилище")
    if any(inv.verified for inv in doc.invoices):
        raise HTTPException(status_code=409, detail="Документ содержит подтверждённые СФ — снимите подтверждение перед коррекцией")
    if not try_acquire_processing(db, doc_id):
        raise HTTPException(status_code=409, detail="Документ уже обрабатывается")

    from processing import ProcessingError, process_document
    try:
        await process_document(doc_id, mode="deskew", reraise=True, session_factory=session_factory)
    except ProcessingError as exc:
        # process_document пробрасывает ТОЛЬКО ошибки с http_status (ориентация deskew).
        # Статус документа уже записан в error внутри process_document.
        raise HTTPException(status_code=exc.http_status, detail=exc.message)

    db.expire_all()
    return _serialize_document(get_document(db, doc_id))
```

Обновить импорты в шапке `routers/invoices.py`: добавить `from crud.documents import ..., try_acquire_processing`, `from processing import get_processing_session_factory`, `from s3 import ..., upload_file_async`. Убрать `import pdf_orientation` (переехал в processing) и `_is_not_found`.

- [ ] **Step 6: Удалить старый `parse_invoice_pdf` из `pdf_parser.py`**

Удалить функцию `parse_invoice_pdf` (строки ~138-387 старой версии) — её заменили `parse_pdf` (фаза A) + `persist_parse_result` (фаза B). Оставить helpers `_reconcile_totals`, `_calculate_completeness`, `_final_confidence`, `_with_cost` (если ещё используется — проверить; если нет, удалить), `SYSTEM_PROMPT`, `OPENROUTER_URL`.

- [ ] **Step 7: Адаптировать deskew-тесты под новые внутренности (ЗЕЛЁНЫЕ, без xfail — F9)**

В `test_invoices.py` переписать ТЕЛА трёх deskew-тестов (мокали удалённый `_reparse_from_s3` → теперь мокаем `pdf_orientation.deskew_pdf`, возвращающий 3-кортеж, + `mock_openrouter` для парсинга). **Контрактные ассерты сохраняются**: 502 остаётся 502, бэкап `.orig` и перезапись `s3_key` проверяются как раньше.

```python
def test_deskew_reparse_rotates_and_backs_up(client, factories, in_memory_s3, mock_openrouter, monkeypatch):
    """Повороты ≠ 0: создаётся {key}.orig, основной ключ перезаписан, документ распарсен."""
    import pdf_orientation as po
    from decimal import Decimal

    doc = factories.DocumentFactory.create(s3_key="k/sample.pdf", status="parsed")
    in_memory_s3["k/sample.pdf"] = b"%PDF-original"

    async def fake_deskew(pdf_bytes):
        """Возвращает исправленные байты + поворот + detect-cost."""
        return b"%PDF-corrected", [270], Decimal("0.001")
    monkeypatch.setattr(po, "deskew_pdf", fake_deskew)

    resp = client.post(f"/api/invoices/documents/{doc.id}/deskew-reparse")
    assert resp.status_code == 200
    assert in_memory_s3["k/sample.pdf.orig"] == b"%PDF-original"   # бэкап оригинала
    assert in_memory_s3["k/sample.pdf"] == b"%PDF-corrected"        # перезапись


def test_deskew_reparse_no_rotation_keeps_s3(client, factories, in_memory_s3, mock_openrouter, monkeypatch):
    """Все нули: S3 не трогаем, бэкап не создаём, документ всё равно распарсен."""
    import pdf_orientation as po
    from decimal import Decimal

    doc = factories.DocumentFactory.create(s3_key="k/up.pdf", status="parsed")
    in_memory_s3["k/up.pdf"] = b"%PDF-up"

    async def fake_deskew(pdf_bytes):
        """Возвращает исходные байты без поворотов + detect-cost."""
        return pdf_bytes, [0], Decimal("0.001")
    monkeypatch.setattr(po, "deskew_pdf", fake_deskew)

    resp = client.post(f"/api/invoices/documents/{doc.id}/deskew-reparse")
    assert resp.status_code == 200
    assert "k/up.pdf.orig" not in in_memory_s3   # бэкап не создан


def test_deskew_reparse_vision_failure_502(client, factories, in_memory_s3, monkeypatch):
    """Сбой vision (TransientError с http_status=502) → 502, S3 не тронут (AC-S0-8 сохранён)."""
    import pdf_orientation as po
    from processing import TransientError

    doc = factories.DocumentFactory.create(s3_key="k/x.pdf", status="parsed")
    in_memory_s3["k/x.pdf"] = b"%PDF-x"

    async def boom(pdf_bytes):
        """Эмулирует недоступность vision-сервиса на detect."""
        raise TransientError("vision down", http_status=502)
    monkeypatch.setattr(po, "deskew_pdf", boom)

    resp = client.post(f"/api/invoices/documents/{doc.id}/deskew-reparse")
    assert resp.status_code == 502                      # контракт сохранён
    assert "k/x.pdf.orig" not in in_memory_s3
    assert in_memory_s3["k/x.pdf"] == b"%PDF-x"          # оригинал не тронут
```

> Примечание: тесты гоняют документ из `parsed` (guard разрешает переход `parsed → processing`). После успешного deskew документ проходит фазу B и станет `parsed` (mock_openrouter happy_path) — контрактные ассерты про S3/HTTP от статуса не зависят. Благодаря DI-override `get_processing_session_factory` (Step 1) обработка внутри эндпоинта видит тест-документ (F1).

- [ ] **Step 8: Прогнать весь integration-набор**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-integration 2>&1"`
Expected: PASS, включая `test_upload_*`, `test_reparse_*`, `test_*_parse_cost*`, `test_reparse_returns_409_when_already_processing` и три адаптированных deskew-теста (все зелёные — никакого xfail).

- [ ] **Step 9: Lint + commit**

```bash
git add backend/crud/documents.py backend/routers/invoices.py backend/pdf_parser.py backend/processing.py backend/tests/conftest.py backend/tests/integration/test_invoices.py
git commit -m "feat(processing): guard-переход + эндпоинты на process_document (DI-фабрика), удалён _reparse_from_s3 (S0-5)"
```

---

### Task 9: Защита СФ во время обработки — FOR UPDATE + 409 + re-fetch под блокировкой (S0-8)

**Files:**
- Modify: `backend/routers/invoices.py` (мутирующие: `update_invoice`, `verify_invoice`, `unverify_invoice`, `delete_invoice`, `bulk_delete_invoices`, `delete_document_route`)
- Modify: `backend/processing.py` (`persist_parse_result` — FOR UPDATE + re-check verified)
- Test: `backend/tests/integration/test_invoices.py`, `backend/tests/integration/test_process_document.py`

**Interfaces:**
- Consumes: `try_acquire_processing`, `persist_parse_result`.
- Produces: хелперы `_load_document_locked(db, doc_id)` (`SELECT ... FOR UPDATE`) и `_reject_if_processing(doc)` в `routers/invoices.py`; фаза B резолвит verified под блокировкой строки документа.

> **Ключевой инвариант (F4):** первичный lookup СФ даёт только `document_id`. Затем лочим `Document` (`FOR UPDATE`), затем ПЕРЕЗАПрашиваем СФ под блокировкой — если фаза B успела её удалить (parse-then-swap), возвращаем 404, а не мутируем устаревший ORM-объект (иначе `StaleDataError`/500). В bulk после блокировки всех документов набор СФ запрашивается заново по входным id.

- [ ] **Step 1: Написать падающие тесты (параметризованные 409 по всем мутациям + verified-abort + bulk)**

В `test_invoices.py`:

```python
import pytest


@pytest.mark.parametrize("method,path_tmpl,body", [
    ("put", "/api/invoices/{invoice_id}", {"number": "X", "date": "2026-05-01", "vat_rate": 20, "items": []}),
    ("post", "/api/invoices/{invoice_id}/verify", None),
    ("post", "/api/invoices/{invoice_id}/unverify", None),
    ("delete", "/api/invoices/{invoice_id}", None),
])
def test_invoice_mutations_return_409_while_processing(client, factories, method, path_tmpl, body):
    """Любая мутация СФ документа в processing → 409 (S0-8)."""
    doc = factories.DocumentFactory.create(status="processing")
    inv = factories.InvoiceFactory.create(document=doc)
    url = path_tmpl.format(invoice_id=inv.id)
    resp = getattr(client, method)(url, json=body) if body is not None else getattr(client, method)(url)
    assert resp.status_code == 409


def test_bulk_delete_returns_409_when_any_document_processing(client, factories, db_session):
    """bulk-delete, если хоть один документ набора в processing → 409, НИЧЕГО не удалено (S0-8)."""
    from models import Invoice

    doc_busy = factories.DocumentFactory.create(status="processing")
    doc_free = factories.DocumentFactory.create(status="parsed")
    inv_busy = factories.InvoiceFactory.create(document=doc_busy)
    inv_free = factories.InvoiceFactory.create(document=doc_free)
    resp = client.request("DELETE", "/api/invoices/bulk", json={"ids": [inv_free.id, inv_busy.id]})
    assert resp.status_code == 409
    # атомарность bulk (часть контракта): 409 ⇒ не удалили НИ ОДНОЙ СФ, включая свободную.
    db_session.expire_all()
    remaining = {i.id for i in db_session.query(Invoice).all()}
    assert inv_free.id in remaining and inv_busy.id in remaining


def test_bulk_delete_locks_documents_in_id_order(client, factories):
    """bulk-delete с несколькими документами (разный порядок id) не дедлокает и работает (S0-8, анти-дедлок)."""
    docs = [factories.DocumentFactory.create(status="parsed") for _ in range(3)]
    invs = [factories.InvoiceFactory.create(document=d) for d in docs]
    # Порядок id во входе обратный — блокировка всё равно по возрастанию id.
    resp = client.request("DELETE", "/api/invoices/bulk", json={"ids": [i.id for i in reversed(invs)]})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 3


def test_delete_document_returns_409_while_processing(client, factories):
    """delete документа в processing → 409 (S0-8)."""
    doc = factories.DocumentFactory.create(status="processing")
    resp = client.delete(f"/api/invoices/documents/{doc.id}")
    assert resp.status_code == 409
```

В `test_process_document.py`:

```python
@pytest.mark.anyio
async def test_phase_b_aborts_when_verified_appeared(
    factories, db_session, in_memory_s3, mock_openrouter, session_factory_test,
):
    """Появившаяся verified-СФ к моменту фазы B → error, старый набор не тронут (AC-S0-9)."""
    doc = _proc_doc(factories, db_session, in_memory_s3)
    factories.InvoiceFactory.create(document=doc, number="СФ-VER", verified=True)
    db_session.commit()

    await process_document(doc.id, mode="parse", session_factory=session_factory_test)

    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "error"
    numbers = [i.number for i in db_session.query(Invoice).filter(Invoice.document_id == doc.id)]
    assert "СФ-VER" in numbers  # verified-СФ не удалена
```

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-k 'while_processing or verified_appeared or bulk_delete_returns_409 or locks_documents_in_id_order or delete_document_returns_409' 2>&1"`
Expected: FAIL (409 не возвращается; фаза B удаляет verified).

- [ ] **Step 3: Добавить хелперы блокировки + re-fetch под блокировкой в мутирующие эндпоинты**

В `routers/invoices.py` добавить хелперы:

```python
def _load_document_locked(db: Session, doc_id: int):
    """SELECT ... FOR UPDATE строки документа — сериализует мутации с фазой B (S0-8)."""
    return db.query(Document).filter(Document.id == doc_id).with_for_update().first()


def _reject_if_processing(doc) -> None:
    """409, если документ в обработке (мутации СФ запрещены до терминального статуса)."""
    if doc is not None and doc.status == "processing":
        raise HTTPException(status_code=409, detail="Документ обрабатывается — дождитесь завершения")
```

**Паттерн re-fetch под блокировкой (F4)** — применить в `update_invoice`, `verify_invoice`, `unverify_invoice`, `delete_invoice`. Пример для `verify_invoice`:

```python
@router.post("/{invoice_id}/verify")
def verify_invoice(invoice_id: int, db: Session = Depends(get_db)):
    """Отметить СФ как проверенную человеком (запрещено во время обработки документа)."""
    # 1) первичный lookup — только чтобы узнать document_id.
    row = db.query(Invoice.document_id).filter(Invoice.id == invoice_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="СФ не найдена")
    # 2) блокируем документ; 3) отклоняем, если обрабатывается.
    doc = _load_document_locked(db, row.document_id)
    _reject_if_processing(doc)
    # 4) перезапрашиваем СФ ПОД блокировкой — фаза B могла её удалить (parse-then-swap).
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="СФ не найдена")
    # 5) мутируем.
    invoice.verified = True
    invoice.verified_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()
    return {"message": "Проверено", "invoice_id": invoice.id, "verified_at": invoice.verified_at.isoformat()}
```

Тот же порядок (id → lock doc → reject-if-processing → re-fetch invoice → 404-if-gone → mutate) применить к `update_invoice` (после re-fetch — вся существующая логика supplier/items), `unverify_invoice`, `delete_invoice`.

Для `delete_document_route` — блокировать строку до проверки verified и удаления:

```python
@router.delete("/documents/{doc_id}")
def delete_document_route(doc_id: int, db: Session = Depends(get_db)):
    """Удалить документ вместе с СФ (запрещено во время обработки)."""
    doc = _load_document_locked(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    _reject_if_processing(doc)
    if any(inv.verified for inv in doc.invoices):
        raise HTTPException(status_code=409, detail="Документ содержит подтверждённые СФ — снимите подтверждение перед удалением")
    if doc.s3_key:
        try:
            delete_file(doc.s3_key)
        except Exception:
            pass
    delete_document(db, doc_id)
    return {"message": "Удалено"}
```

Для `bulk_delete_invoices` — блокировать все затронутые документы в детерминированном порядке `id`, затем ПЕРЕЗАПросить набор СФ под блокировкой (F4 + анти-дедлок):

```python
@router.delete("/bulk", status_code=200)
def bulk_delete_invoices(body: BulkDeleteRequest, db: Session = Depends(get_db)):
    """Удалить несколько СФ. Документы в processing → 409; подтверждённые пропускаются."""
    if not body.ids:
        return {"deleted": 0, "skipped": []}

    # 1) узнаём document_id по входным СФ (без блокировки), 2) лочим документы по возрастанию id.
    doc_ids = sorted({
        r.document_id for r in
        db.query(Invoice.document_id).filter(Invoice.id.in_(body.ids)).all()
    })
    for did in doc_ids:  # порядок id — общий для всех транзакций, дедлок невозможен
        _reject_if_processing(_load_document_locked(db, did))

    # 3) перезапрашиваем СФ ПОД блокировкой документов (фаза B могла часть удалить).
    invoices = db.query(Invoice).filter(Invoice.id.in_(body.ids)).all()
    deleted = 0
    skipped: list[int] = []
    for inv in invoices:
        if inv.verified:
            skipped.append(inv.id)
        else:
            db.delete(inv)
            deleted += 1
    db.commit()
    return {"deleted": deleted, "skipped": skipped}
```

- [ ] **Step 4: Добавить FOR UPDATE + re-check verified в `persist_parse_result`**

В `backend/processing.py`, в начале `persist_parse_result`, заменить загрузку документа на блокирующую и добавить проверку verified перед удалением:

```python
    doc = db.query(Document).filter(Document.id == doc_id).with_for_update().first()
    if doc is None:
        raise PermanentError(f"Документ id={doc_id} не найден на фазе B",
                             cost_usd=outcome.cost_usd, paid_calls=outcome.paid_calls)
    # Повторная проверка под блокировкой строки: verified-СФ могла появиться после
    # guard-перехода, пока шёл 180-секундный LLM-вызов (S0-8). Проверка в эндпоинте это
    # уже не гарантирует. Ошибка несёт cost — фаза A оплачена (инвариант §2.3).
    if any(inv.verified for inv in doc.invoices):
        raise PermanentError("Документ содержит подтверждённые СФ — переразбор отменён",
                             cost_usd=outcome.cost_usd, paid_calls=outcome.paid_calls)
```

Эти две проверки заменяют строку `doc = db.query(Document)...first()` в начале `persist_parse_result` (Task 4) и стоят ДО `try:` (мутаций ещё не было — rollback не нужен). Остальное тело без изменений.

- [ ] **Step 5: Запустить целевые тесты — PASS**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-k 'while_processing or verified_appeared or bulk_delete_returns_409 or locks_documents_in_id_order or delete_document_returns_409' 2>&1"`
Expected: PASS.

- [ ] **Step 6: Прогнать весь набор, убедиться в отсутствии регрессий**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-integration 2>&1"`
Expected: PASS (весь набор зелёный).

- [ ] **Step 7: Lint + commit**

```bash
git add backend/routers/invoices.py backend/processing.py backend/tests/integration/
git commit -m "feat(processing): FOR UPDATE + 409 + re-fetch под блокировкой для всех мутаций СФ (S0-8)"
```

---

### Task 10: `last_error` в API/сериализаторе + фронт (S0-7)

**Files:**
- Modify: `backend/routers/invoices.py` (`_serialize_document`, `list_documents`)
- Modify: `frontend/src/types/invoice.ts` (поле `last_error`)
- Modify: `frontend/src/components/projects/ErrorDocsTab.tsx` (показ причины)
- Test: `backend/tests/integration/test_invoices.py`, `frontend/src/components/projects/ErrorDocsTab.test.tsx`

**Interfaces:**
- Produces: поля ответа `status`, `last_error` в document-сериализации; фронт-тип `DocumentSummary.last_error?: string | null`.

- [ ] **Step 1: Написать падающий backend-тест**

В `test_invoices.py`:

```python
def test_serialized_document_exposes_last_error(client, factories):
    """API отдаёт last_error для документов в статусе error (S0-7)."""
    doc = factories.DocumentFactory.create(status="error")
    d = client.get(f"/api/invoices/documents/{doc.id}").json()
    assert "last_error" in d
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-k 'exposes_last_error' 2>&1"`
Expected: FAIL — ключа `last_error` нет в ответе.

- [ ] **Step 3: Добавить `last_error` в сериализацию**

В `_serialize_document` и в dict внутри `list_documents` (`routers/invoices.py`) добавить после `"status": doc.status,`:

```python
        "last_error": doc.last_error,
```

- [ ] **Step 4: Backend-тест PASS, затем фронт**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-k 'exposes_last_error' 2>&1"`
Expected: PASS.

- [ ] **Step 5: Добавить поле в тип и показ в ErrorDocsTab**

В `frontend/src/types/invoice.ts` в `DocumentSummary`/`DocumentDetail` добавить:

```typescript
  last_error?: string | null;
```

В `ErrorDocsTab.tsx` в ячейке статуса заменить голый лейбл на причину, когда она есть:

```tsx
const statusLabel = doc.status === "error"
  ? (doc.last_error || "Ошибка парсинга")
  : "Проблемы в СФ";
```

(если `last_error` длинный — обернуть в `<Tooltip>`, показывая краткое «Ошибка парсинга» с полным текстом в тултипе; компонент `Tooltip` уже импортирован в файле).

- [ ] **Step 6: Написать/обновить фронт-тест**

В `frontend/src/components/projects/ErrorDocsTab.test.tsx` добавить проверку, что при `status: "error", last_error: "Разбор счёта №5 неполный"` в DOM виден текст причины (или тултип содержит его). Запустить:

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-frontend 2>&1"`
Expected: PASS.

- [ ] **Step 7: Lint (backend + frontend) + typecheck + commit**

```bash
git add backend/routers/invoices.py frontend/src/types/invoice.ts frontend/src/components/projects/ErrorDocsTab.tsx frontend/src/components/projects/ErrorDocsTab.test.tsx backend/tests/integration/test_invoices.py
git commit -m "feat(processing): last_error в API и ErrorDocsTab (S0-7)"
```

---

### Task 11: Data-миграция бэкфилла исторических артефактов (Q2) — GATED

> **Гейт:** выполнять ТОЛЬКО после SELECT-валидации по реальной БД. Сначала посчитать кандидатов; если их нет — задача закрывается без миграции.

**Files:**
- Create: `backend/alembic/versions/<generated>_backfill_stuck_parsed_docs.py` (только если гейт пройден)

- [ ] **Step 1: SELECT-валидация по реальной БД (гейт)**

Прогнать против прод-подобной БД (или дампа) диагностический запрос:

```sql
-- Артефакт дефолта P3: parsed + unknown + 0 СФ
SELECT count(*) AS stuck_unknown
FROM documents d
WHERE d.status = 'parsed' AND d.doc_type = 'unknown'
  AND NOT EXISTS (SELECT 1 FROM invoices i WHERE i.document_id = d.id);

-- Второй класс: parsed + invoice + 0 СФ (пустой invoices/кривые даты)
SELECT count(*) AS stuck_invoice_empty
FROM documents d
WHERE d.status = 'parsed' AND d.doc_type = 'invoice'
  AND NOT EXISTS (SELECT 1 FROM invoices i WHERE i.document_id = d.id);
```

Если оба счётчика 0 — **закрыть задачу без миграции**, отметить в devlog. Если > 0 — согласовать с пользователем, трогать ли второй класс, и перейти к Step 2.

- [ ] **Step 2: Создать ревизию (если гейт пройден) — через just**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just db-revision message='backfill stuck parsed docs to error' 2>&1"`

- [ ] **Step 3: Заполнить тело data-миграции** (докстринги у `upgrade`/`downgrade` — обязательны, F11)

```python
def upgrade() -> None:
    """Переводит зависшие parsed+unknown+0СФ документы в error (исторический артефакт P3)."""
    op.execute(
        "UPDATE documents SET status='error', "
        "last_error='Разбор не был завершён (исторические данные)' "
        "WHERE status='parsed' AND doc_type='unknown' "
        "AND NOT EXISTS (SELECT 1 FROM invoices i WHERE i.document_id = documents.id)"
    )
    # Второй класс включать ТОЛЬКО по согласованию (см. Step 1).


def downgrade() -> None:
    """No-op: бэкфилл семантически one-way — исходный status (был parsed) не восстановить."""
    pass
```

- [ ] **Step 4: Применить и проверить**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just db-migrate 2>&1"`
Затем повторить SELECT из Step 1 — `stuck_unknown` должен стать 0.

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/
git commit -m "chore(processing): бэкфилл зависших parsed-документов в error (Q2)"
```

---

## Финальная проверка плана

- [ ] **Прогнать полный набор + линт**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just lint 2>&1"`
Then: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test 2>&1"`
Expected: всё зелёное. Проверить покрытие изменённых файлов (`processing.py`, `pdf_parser.py`, `crud/materials.py`, `crud/documents.py`, `routers/invoices.py`, `pdf_orientation.py`, `s3.py`) ≥ порога.

- [ ] **Обновить документацию агента**

Отразить новую архитектуру обработки в `docs/agent/pdf-parsing.md` (фазы A/B, статусная модель, guard, доменные ошибки, DI-фабрика сессий) и `docs/agent/database.md` (новые поля `Document`). Завести devlog-запись `docs/devlog/2026-07-17-async-processing-stage-0.md`.

---

## Покрытие спеки (self-review)

| Спека | Задача | AC |
|---|---|---|
| S0-1 статусная модель | Task 1 | AC-S0-5 |
| S0-2 фазы A/B | Task 3 (A), Task 4 (B) | AC-S0-3 (Task 3 reconcile→raise), AC-S0-11 (Task 4 rollback+cost, **Task 6 e2e** `test_process_document_phase_b_failure_writes_error_with_cost`) |
| S0-3 process_document + DI-фабрика | Task 6 | — |
| S0-4 доменные ошибки + Cancelled | Task 2, Task 6, Task 7 | AC-S0-2, AC-S0-8 |
| S0-5 guard + commit-граница | Task 8 | AC-S0-4 |
| S0-6 разблокировка loop | Task 5 (S3), Task 7 (pdfium), Task 8 (upload → upload_file_async) | AC-S0-7 (ручной смоук) |
| S0-7 last_error в API/UI | Task 10 | — |
| S0-8 защита СФ (FOR UPDATE + 409 + re-fetch) | Task 9 | AC-S0-9 |
| S0-9 стоимость detect (+ сохранение при сбое после detect) | Task 7 | AC-S0-10 |
| S0-8 HTTP-контракт deskew | Task 2 (http_status), Task 8 (reraise+маппинг), Task 7 (deskew-внутренности + адаптированные тесты) | AC-S0-8 |
| §2.3 условная error-запись | Task 4 (rollback+cost), Task 6 (write, narrowed retry), **Task 6 concurrency** | AC-S0-11, AC-S0-12, **AC-S0-13** (`test_conditional_error_write_concurrency`) |
| Q2 бэкфилл | Task 11 (gated) | — |
| Q7 no-commit класс | Task 4 | — |

**Явные отклонения от прежнего поведения (обоснованные):** (1) фаза A бросает `PermanentError` при нуле разобранных СФ — раньше был бы `parsed` с 0 СФ (Task 3, ради устранения артефакта Q2); (2) reparse документа с отсутствующим в S3 файлом → `error` + 200 вместо прежнего 404 (нет теста на этот путь, `last_error` информативнее — принято); (3) `doc_type` на error-пути больше не флипается в `unknown` (Task 4/6) — parse-then-swap хранит живые СФ, флип был бы противоречив; на свежем upload `doc_type` и так `unknown` по дефолту, поэтому `test_upload_unparseable_marks_doc_type_unknown` остаётся зелёным; (4) **413/502 deskew теперь оставляют документ в `error`** (HTTP-код прежний — буква AC-S0-8 соблюдена, но раньше документ не трогался). Побочный эффект: документ с живыми СФ уедет в ErrorDocsTab, например при слишком многостраничном файле. Принято осознанно: комбинация «error + живые СФ» санкционирована спекой (Q1), причина видна в `last_error` (S0-7), reparse (не-deskew, без 20-страничного лимита) чинит. Альтернатива — пропускать error-запись для http_status-ошибок и откатывать guard к прежнему статусу — требует запоминать предыдущий статус в guard (CTE с `RETURNING prev.status`); отложено как дороже пользы. Если UX-жалобы появятся — guard апгрейдится точечно.

**Закрытые ревью-замечания (round 4):** AC-S0-11 покрыт end-to-end (Task 6); AC-S0-13 — конкретный шаг с двумя реальными соединениями (Task 6, `test_conditional_error_write_concurrency.py`), не «пробел при исполнении». AC-S0-1 и AC-S0-6 (старые тесты терминальных статусов проходят без изменений) проверяются полным прогоном в «Финальной проверке».
