# Async Processing — Ступень 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Эндпоинты обработки отвечают немедленно (`202` + документ в `processing`), результат доезжает через polling; плюс идемпотентный upload по `file_hash` (Q6) и startup-sweep зависших документов.

**Architecture:** Ядро S0 (`process_document`, guard, DI-фабрика) не меняется — меняются только обёртки: эндпоинты ставят таску в `BackgroundTasks` вместо `await`; фронт получает polling (`refetchInterval`) + один глобальный детектор терминального перехода на QueryCache; sweep в lifespan закрывает crash-окно.

**Tech Stack:** FastAPI BackgroundTasks, SQLAlchemy sync, PostgreSQL; React 19 + TS strict, TanStack Query v5 (`^5.100.9`), MSW, vitest.

**Спека:** `docs/superpowers/specs/2026-07-19-async-processing-stage-1-design.md` (одобрена, 3 раунда правок) — при расхождении план сверять со спекой; базовая спека `2026-07-16-async-processing-design.md` §4.

## Global Constraints

- Команды ТОЛЬКО через `just`: `just test-int-k '<pat>'`, `just test-unit-k '<pat>'`, `just test-backend`, `just test-frontend`, `just lint-backend`, `just lint-frontend`, `just typecheck-frontend`. Windows-обёртка: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just <cmd> 2>&1"`. Никогда `| grep` поверх pytest.
- Докстринги на КАЖДОЙ функции/методе, включая тесты, вложенные фейки и хелперы (AGENTS.md). Однострочник — норма.
- Async-тесты backend: `@pytest.mark.asyncio` (pytest-asyncio AUTO mode; НЕ `@pytest.mark.anyio`).
- Терминальные статусы `parsed`/`error` и нетерминальные `pending`/`processing` не переименовывать.
- Новые зависимости запрещены (в т.ч. фронтовые) — всё строится на существующем стеке.
- Миграции НЕ нужны: `documents.file_hash` (String(64), index) и `uq_documents_project_file_hash` уже существуют (ревизия d1e2f3a4b5c6).
- Ключи квери фронта: list = `["documents", projectId?]`, detail = `["document", docId]` — префиксы РАЗНЫЕ ("documents" vs "document"), детектор и инвалидации учитывают оба.
- Тексты UI/сообщений — русские, точные формулировки из спеки: «Обработка прервана перезапуском сервера», «Обработка запущена», «Файл уже был загружен», «Обрабатывается».
- Каждая задача завершается зелёным состоянием: lint + релевантные тесты. Никаких временных xfail.
- Ветка: `feat/async-processing-stage-1` (создать от main перед Task 1; в main не коммитить).

---

### Task 1: Guard мутаций расширяется на `pending` (`_reject_if_busy`)

**Files:**
- Modify: `backend/routers/invoices.py:40-43` (хелпер) + все call-sites (`:291, :400, :422, :454, :480` и `delete_document_route`)
- Test: `backend/tests/integration/test_invoices.py` (параметризованные 409-тесты S0-8)

**Interfaces:**
- Produces: `def _reject_if_busy(doc: Document | None) -> None` — 409 при `doc.status in ("pending", "processing")`. Имя `_reject_if_processing` исчезает.

- [ ] **Step 1: Написать падающий тест**

В `test_invoices.py` найти параметризованный тест `test_invoice_mutations_return_409_while_processing` (создаёт документ `status="processing"`). Добавить параметр статуса — тест становится параметризованным по декартову произведению (мутация × статус):

```python
@pytest.mark.parametrize("busy_status", ["pending", "processing"])
@pytest.mark.parametrize("method,path_tmpl,body", [
    ("put", "/api/invoices/{invoice_id}", {"number": "X", "date": "2026-05-01", "vat_rate": 20, "items": []}),
    ("post", "/api/invoices/{invoice_id}/verify", None),
    ("post", "/api/invoices/{invoice_id}/unverify", None),
    ("delete", "/api/invoices/{invoice_id}", None),
])
def test_invoice_mutations_return_409_while_busy(client, factories, method, path_tmpl, body, busy_status):
    """Любая мутация СФ документа в pending|processing → 409 (S1: guard закрывает и pending-окно)."""
    doc = factories.DocumentFactory.create(status=busy_status)
    inv = factories.InvoiceFactory.create(document=doc)
    url = path_tmpl.format(invoice_id=inv.id)
    resp = getattr(client, method)(url, json=body) if body is not None else getattr(client, method)(url)
    assert resp.status_code == 409
```

Аналогично добавить `pending`-вариант для delete-document и bulk-delete 409-тестов (переименовать/параметризовать существующие `test_delete_document_returns_409_while_processing` и `test_bulk_delete_returns_409_when_any_document_processing` — сохранить их ассерты, включая атомарность bulk).

- [ ] **Step 2: Запустить — убедиться, что pending-варианты падают**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-k 'while_busy or delete_document_returns_409 or bulk_delete_returns_409' 2>&1"`
Expected: FAIL для `busy_status=pending` (сейчас 409 только на processing), PASS для processing.

- [ ] **Step 3: Переименовать и расширить хелпер**

В `backend/routers/invoices.py` заменить `_reject_if_processing` (строки 40-43):

```python
def _reject_if_busy(doc: Document | None) -> None:
    """409, если документ в нетерминальном статусе (pending|processing) —
    мутации СФ/документа запрещены до терминального.

    pending включён (S1): в окне между commit create_document (pending) и
    guard-commit (processing) документ иначе можно было бы удалить,
    оставив S3-сироту. Легитимных мутаций pending-документа не существует.
    """
    if doc is not None and doc.status in ("pending", "processing"):
        raise HTTPException(status_code=409, detail="Документ обрабатывается — дождитесь завершения")
```

Заменить ВСЕ вызовы `_reject_if_processing(` → `_reject_if_busy(` (grep по файлу; ожидается 6 call-sites: update_invoice, verify, unverify, bulk_delete, delete_invoice, delete_document_route).

- [ ] **Step 4: Запустить — PASS**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-k 'while_busy or delete_document_returns_409 or bulk_delete_returns_409' 2>&1"`
Expected: PASS все варианты. Затем полный integration-набор: `just test-backend-integration` — без регрессий.

- [ ] **Step 5: Lint + commit**

```bash
git add backend/routers/invoices.py backend/tests/integration/test_invoices.py
git commit -m "feat(processing): guard мутаций расширен на pending — _reject_if_busy (S1)"
```

---

### Task 2: Startup-sweep зависших документов (S1-4, AC-S1-3, AC-S1-3b)

**Files:**
- Modify: `backend/main.py` (функция `_sweep_stuck_documents` + вызов в lifespan, строки ~27-38)
- Modify: `backend/tests/conftest.py` (`client`-фикстура, строки 190-236 — подмена sweep на тест-фабрику)
- Modify: `justfile` (комментарий-инвариант у `dev-backend`)
- Modify: `docs/agent/pdf-parsing.md` (раздел про deployment-инвариант)
- Test: `backend/tests/integration/test_startup_sweep.py` (create)

**Interfaces:**
- Consumes: `database.SessionLocal` (поздний резолв — паттерн F1 из S0).
- Produces: `def _sweep_stuck_documents(session_factory=None) -> int` в `main.py` — возвращает число переведённых документов; lifespan вызывает её до `yield`.

- [ ] **Step 1: Написать падающие тесты**

Create `backend/tests/integration/test_startup_sweep.py`:

```python
"""Startup-sweep S1-4: pending|processing → error на старте процесса (AC-S1-3)."""
import pytest

from models import Document


def test_sweep_marks_pending_and_processing_as_error(factories, db_session, session_factory_test):
    """Оба нетерминальных статуса переводятся в error с текстом про перезапуск."""
    from main import _sweep_stuck_documents

    doc_pending = factories.DocumentFactory.create(status="pending")
    doc_processing = factories.DocumentFactory.create(status="processing")
    doc_parsed = factories.DocumentFactory.create(status="parsed")
    doc_error = factories.DocumentFactory.create(status="error", last_error="старая причина")
    db_session.commit()

    swept = _sweep_stuck_documents(session_factory=session_factory_test)

    db_session.expire_all()
    assert swept == 2
    for doc_id in (doc_pending.id, doc_processing.id):
        saved = db_session.query(Document).filter(Document.id == doc_id).first()
        assert saved.status == "error"
        assert saved.last_error == "Обработка прервана перезапуском сервера"
    assert db_session.query(Document).filter(Document.id == doc_parsed.id).first().status == "parsed"
    err = db_session.query(Document).filter(Document.id == doc_error.id).first()
    assert err.last_error == "старая причина"  # терминальные не тронуты


def test_lifespan_invokes_sweep(monkeypatch):
    """lifespan вызывает sweep на старте (интеграция функции в жизненный цикл)."""
    from fastapi.testclient import TestClient

    import main

    calls = {"n": 0}

    def spy(session_factory=None):
        """Считает вызовы sweep вместо реального обращения к БД."""
        calls["n"] += 1
        return 0
    monkeypatch.setattr(main, "_sweep_stuck_documents", spy)

    with TestClient(main.app):
        pass
    assert calls["n"] == 1


def test_lifespan_aborts_startup_when_sweep_fails(monkeypatch):
    """Fail-fast: ошибка sweep (БД недоступна) прерывает startup, приложение не поднимается."""
    from fastapi.testclient import TestClient

    import main

    def boom(session_factory=None):
        """Эмулирует недоступность БД на старте."""
        raise RuntimeError("db down")
    monkeypatch.setattr(main, "_sweep_stuck_documents", boom)

    with pytest.raises(RuntimeError, match="db down"):
        with TestClient(main.app):
            pass
```

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-k 'sweep' 2>&1"`
Expected: FAIL — `ImportError: cannot import name '_sweep_stuck_documents'`.

- [ ] **Step 3: Реализовать sweep + вызов в lifespan**

В `backend/main.py` добавить (рядом с lifespan; `text` импортировать из sqlalchemy):

```python
def _sweep_stuck_documents(session_factory=None) -> int:
    """Startup-sweep (S1-4): все pending|processing → error одним UPDATE.

    На старте процесса легитимных нетерминальных документов не существует
    (однопроцессная модель, no-overlap deployment): pending — crash в окне
    create_document→guard, processing — crash посреди фоновой таски. Оба
    нетерминальны для polling'а — зомби заставил бы фронт поллиться вечно.
    session_factory=None → поздний резолв SessionLocal (паттерн F1 S0);
    в тестах инжектится тест-фабрика.
    """
    from sqlalchemy import text

    if session_factory is None:
        from database import SessionLocal
        session_factory = SessionLocal
    with session_factory() as db:
        result = db.execute(text(
            "UPDATE documents SET status='error', "
            "last_error='Обработка прервана перезапуском сервера' "
            "WHERE status IN ('pending', 'processing')"
        ))
        db.commit()
    if result.rowcount:
        logger.warning(f"Startup-sweep: {result.rowcount} документ(ов) переведено в error")
    return result.rowcount
```

В `lifespan`, после блока `ensure_bucket`, до `yield` — **fail-fast, БЕЗ try/except**:

```python
    # Fail-fast (ревью плана, P1): sweep обязан выполниться до приёма трафика.
    # Проглотить ошибку нельзя — если БД оживёт позже, зомби-processing останутся
    # навсегда и polling не завершится. БД недоступна → приложение не стартует
    # (оно всё равно неработоспособно), рестарт повторит sweep.
    swept = _sweep_stuck_documents()
    logger.info(f"Startup-sweep выполнен: {swept} документ(ов)")
```

(Контраст с `ensure_bucket`, который остаётся fail-open: без S3 ломается только upload-путь, без БД — всё приложение.)

- [ ] **Step 3b: Подменить sweep в `client`-фикстуре (КРИТИЧНО — без этого падает весь integration-набор)**

`client`-фикстура (conftest.py:191) входит в `with TestClient(app)` (строка :233) — lifespan (и sweep) выполняется на КАЖДОМ тесте с этой фикстурой. Sweep по умолчанию резолвит реальный `database.SessionLocal` — dependency override `get_db` на него НЕ действует: локально он бы ударил по dev-БД, в CI (`DATABASE_URL` → немигрированная БД без таблицы `documents`) startup упал бы и уронил почти весь набор.

В `backend/tests/conftest.py`, фикстура `client`: добавить параметр `monkeypatch` и ДО создания TestClient:

```python
@pytest.fixture
def client(db_session, in_memory_s3, session_factory_test, monkeypatch) -> Iterator:
    """... (существующий докстринг; дополнить строкой:)
    - startup-sweep (lifespan) перенаправляется на session_factory_test —
      реальный SessionLocal не трогается (dependency override на него не действует).
    """
    ...
    import main

    real_sweep = main._sweep_stuck_documents

    def _sweep_via_test_factory(session_factory=None):
        """Sweep в lifespan через тестовую фабрику — dev/CI БД не затрагивается."""
        return real_sweep(session_factory=session_factory_test)

    monkeypatch.setattr(main, "_sweep_stuck_documents", _sweep_via_test_factory)
    # ... существующие dependency_overrides и `with TestClient(app) ...` без изменений
```

Специальные lifespan-тесты (Step 1) свою подмену задают сами (spy/boom) — с фикстурой не конфликтуют (они не используют `client`).

Страховка (единственное место вне conftest, создающее TestClient): `test_auth_coverage.py:28` держит module-scoped `TestClient(app)` БЕЗ контекст-менеджера — по семантике Starlette lifespan там НЕ запускается (докстринг файла утверждает обратное — он неточен). После Step 3 прогнать `just test-int-k 'auth'` вместе с `tests/test_auth_coverage.py`: если auth-coverage-тесты вдруг падают об sweep (lifespan всё же выполнился) — добавить noop-подмену sweep и туда, а заодно поправить докстринг файла.

- [ ] **Step 4: Запустить — PASS**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-k 'sweep' 2>&1"`
Expected: PASS (2 теста).

- [ ] **Step 5: Зафиксировать deployment-инвариант (AC-S1-3b)**

В `justfile`, над рецептом `dev-backend`, добавить комментарий:

```text
# ИНВАРИАНТ S1 (async processing): один процесс — workers=1, replicas=1,
# деплой строго stop-then-start (no-overlap; rolling запрещён до Ступени 2).
# Startup-sweep на старте переводит pending/processing в error — при overlap
# новый процесс пометил бы живые таски старого. См. docs/agent/pdf-parsing.md.
```

В `docs/agent/pdf-parsing.md` добавить в раздел про асинхронную обработку абзац: «Deployment-инвариант S1: `workers=1, replicas=1`, no-overlap deployment (stop-then-start). Startup-sweep в lifespan переводит все `pending|processing` в `error` на старте. Потребность в `workers>1`/rolling — триггер Ступени 2 (advisory-lock)».

Плюс РЕАЛЬНАЯ проверка (не только документация): убедиться grep'ом, что `justfile` не содержит `--workers` ни в одном uvicorn-рецепте (`grep -n "workers" justfile` → пусто или только комментарий-инвариант). Прод-деплой-конфига пока не существует (прод не развёрнут) — конфигурировать нечего; при первом развёртывании конфиг создаётся сразу stop-then-start, о чём говорит doc-абзац выше.

> **Advisory lock — осознанно отложен на S2 (решение уровня спеки, не плана):** зафиксировано в спеке S1 §3 и подтверждено на её ревью («верная YAGNI-калибровка»). Технически на Neon advisory lock требует долгоживущего соединения на весь lifetime процесса — ровно та неизвестность (scale-to-zero, обрыв соединений, ср. pool_recycle=300), ради которой существует обязательный спайк S2-0; молча потерянный lock дал бы ЛОЖНУЮ уверенность в single-instance. Не реализовывать в S1.

- [ ] **Step 6: Lint + commit**

```bash
git add backend/main.py backend/tests/conftest.py backend/tests/integration/test_startup_sweep.py justfile docs/agent/pdf-parsing.md
git commit -m "feat(processing): startup-sweep pending|processing → error (fail-fast) + deployment-инвариант (S1-4)"
```

---

### Task 3: Дедуп upload по file_hash (Q6)

**Files:**
- Modify: `backend/crud/documents.py:32-38` (`create_document` + параметр `file_hash`)
- Modify: `backend/routers/invoices.py:244-274` (`upload_pdf`: hash, fast-path, гонка)
- Test: `backend/tests/integration/test_invoices.py` (дедуп-тесты), `backend/tests/integration/test_upload_dedup_concurrency.py` (create), `backend/tests/unit/test_upload_race_branches.py` (create)

**Interfaces:**
- Consumes: `delete_file_async` (`s3.py:62`), `_serialize_document`, `try_acquire_processing`.
- Produces: `create_document(db, project_id, filename, s3_key, file_hash: str | None = None) -> Document`; upload-ответ получает ключ `"duplicate": bool` (True — 200-дубликат; НЕ-дубликатная ветка добавит `"duplicate": False` в Task 4 вместе с 202).

> Важно: в этой задаче upload остаётся СИНХРОННЫМ (await process_document) — 202 придёт в Task 4. Дедуп-ветки (200 duplicate:true) от этого не зависят и переживут Task 4 без изменений.

- [ ] **Step 1: Написать падающие integration-тесты**

В `test_invoices.py`:

```python
def test_upload_duplicate_returns_200_with_flag(client, factories, in_memory_s3, mock_openrouter, sample_pdf_bytes):
    """Повторная загрузка того же файла → 200 duplicate:true, S3 не растёт, документ не создан (Q6)."""
    import io

    files = {"file": ("a.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")}
    project = factories.ProjectFactory.create()
    r1 = client.post("/api/invoices/upload", data={"project_id": project.id}, files=files)
    assert r1.status_code in (200, 202)
    s3_size_after_first = len(in_memory_s3)

    files2 = {"file": ("copy.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")}
    r2 = client.post("/api/invoices/upload", data={"project_id": project.id}, files=files2)
    assert r2.status_code == 200
    d = r2.json()
    assert d["duplicate"] is True
    assert d["id"] == r1.json()["id"]           # тот же документ
    assert len(in_memory_s3) == s3_size_after_first  # S3 не вырос


def test_upload_duplicate_while_original_processing(client, factories, in_memory_s3, sample_pdf_bytes, db_session):
    """Дубль, пока оригинал в processing → 200 duplicate:true со статусом processing (спека §2)."""
    import hashlib
    import io

    project = factories.ProjectFactory.create()
    file_hash = hashlib.sha256(sample_pdf_bytes).hexdigest()
    factories.DocumentFactory.create(project=project, status="processing", file_hash=file_hash)
    db_session.commit()

    files = {"file": ("b.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")}
    r = client.post("/api/invoices/upload", data={"project_id": project.id}, files=files)
    assert r.status_code == 200
    assert r.json()["duplicate"] is True
    assert r.json()["status"] == "processing"
```

(Сверить kwargs `DocumentFactory` с фабрикой: поддерживает ли `project=`/`file_hash=` — при расхождении адаптировать под факт.)

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-k 'upload_duplicate' 2>&1"`
Expected: FAIL — ключа `duplicate` нет, создаётся второй документ.

- [ ] **Step 3: Расширить `create_document`**

В `backend/crud/documents.py:32`:

```python
def create_document(db: Session, project_id: int, filename: str, s3_key: str,
                    file_hash: str | None = None) -> Document:
    """Создать документ (pending). file_hash — sha256 исходных байтов (Q6, дедуп)."""
    doc = Document(project_id=project_id, filename=filename, s3_key=s3_key, file_hash=file_hash)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc
```

- [ ] **Step 4: Реализовать fast-path и гонку в `upload_pdf`**

В `backend/routers/invoices.py` (импорты: `hashlib`, `from sqlalchemy.exc import IntegrityError`, `from s3 import delete_file_async, upload_file_async`). Тело до S3-записи:

```python
    file_bytes = await file.read()
    file_hash = hashlib.sha256(file_bytes).hexdigest()  # хеш ОРИГИНАЛА; после deskew не пересчитывается (Q6)

    # Fast-path дедупа: файл уже загружали в этот проект → отдаём существующий документ.
    existing = (db.query(Document)
                .filter(Document.project_id == project_id, Document.file_hash == file_hash)
                .first())
    if existing:
        return {**_serialize_document(existing), "duplicate": True}
```

Создание документа с обработкой гонки (вместо `doc = create_document(...)`):

```python
    try:
        doc = create_document(db, project_id, file.filename, object_name, file_hash=file_hash)
    except IntegrityError:
        # Гонка двух параллельных загрузок одного файла ИЛИ иная integrity-ошибка (FK).
        db.rollback()  # обязателен: сессия в failed state (PendingRollbackError без него)
        winner = (db.query(Document)
                  .filter(Document.project_id == project_id, Document.file_hash == file_hash)
                  .first())
        try:
            await delete_file_async(object_name)  # наш S3-объект осиротел — best-effort очистка
        except Exception:
            logger.warning(f"Дедуп-гонка: не удалось удалить S3-сироту {object_name}")
        if winner is None:
            raise  # IntegrityError был НЕ про uq_documents_project_file_hash — не маскируем под дубликат
        return {**_serialize_document(winner), "duplicate": True}
```

- [ ] **Step 5: Юнит-тест обеих веток гонки**

Create `backend/tests/unit/test_upload_race_branches.py` — прямой вызов эндпоинт-функции с мок-сессией нецелесообразен (FastAPI DI); вместо этого integration-тест ветки winner через monkeypatch `create_document`:

```python
"""Ветки IntegrityError-гонки upload: winner найден / winner is None (Q6, спека §2 шаг 4)."""
import io

import pytest
from sqlalchemy.exc import IntegrityError


def _files(sample_pdf_bytes, name="race.pdf"):
    """multipart-пейлоад для upload."""
    return {"file": (name, io.BytesIO(sample_pdf_bytes), "application/pdf")}


def test_race_winner_found_returns_duplicate(client, factories, db_session, in_memory_s3,
                                             sample_pdf_bytes, monkeypatch):
    """IntegrityError + существующий победитель → rollback, S3-сирота удалена, 200 duplicate:true."""
    import hashlib

    import routers.invoices as inv_router

    project = factories.ProjectFactory.create()
    file_hash = hashlib.sha256(sample_pdf_bytes).hexdigest()
    winner = factories.DocumentFactory.create(project=project, status="parsed", file_hash=None)
    db_session.commit()

    def boom_create(db, project_id, filename, s3_key, file_hash=None):
        """Эмулирует проигрыш гонки: победитель успел закоммититься, наш INSERT падает."""
        winner.file_hash = file_hash
        db.commit()
        raise IntegrityError("INSERT INTO documents ...", {}, Exception("uq_documents_project_file_hash"))
    monkeypatch.setattr(inv_router, "create_document", boom_create)

    s3_before = set(in_memory_s3)
    r = client.post("/api/invoices/upload", data={"project_id": project.id}, files=_files(sample_pdf_bytes))
    assert r.status_code == 200
    assert r.json()["duplicate"] is True
    assert r.json()["id"] == winner.id
    assert set(in_memory_s3) == s3_before  # наш объект удалён (сирот нет)


def test_race_winner_none_reraises(client, factories, in_memory_s3, sample_pdf_bytes, monkeypatch):
    """IntegrityError БЕЗ победителя (например, FK) → сирота удалена, исходная ошибка переброшена.

    Фикстура client создаёт TestClient с raise_server_exceptions=True (дефолт) —
    перевыброшенное эндпоинтом исключение долетает до теста КАК ЕСТЬ, что проверяет
    «не замаскирован под дубликат» даже строже, чем ассерт кода 5xx. Общую фикстуру
    НЕ менять (глобальный raise_server_exceptions=False изменил бы весь набор).
    """
    import routers.invoices as inv_router

    project = factories.ProjectFactory.create()

    def boom_create(db, project_id, filename, s3_key, file_hash=None):
        """Эмулирует чужой IntegrityError — победителя по (project, hash) не существует."""
        raise IntegrityError("INSERT INTO documents ...", {}, Exception("fk violation"))
    monkeypatch.setattr(inv_router, "create_document", boom_create)

    s3_before = set(in_memory_s3)
    with pytest.raises(IntegrityError):
        client.post("/api/invoices/upload", data={"project_id": project.id}, files=_files(sample_pdf_bytes))
    assert set(in_memory_s3) == s3_before  # сирота убрана ДО проброса
```

> Реализатору: (1) файл лежит в `tests/integration/` по факту (нужны client/factories) — если положить в unit не выйдет из-за фикстур, размести в `tests/integration/test_upload_race_branches.py`, суть тестов не меняется. (2) `create_document` должен вызываться в роутере ПО ИМЕНИ МОДУЛЯ (`create_document(...)` из импорта `from crud.documents import create_document`) — monkeypatch `inv_router.create_document` перехватит его; сверить с фактическим импортом.

- [ ] **Step 6: Конкурентный тест — две реальные сессии**

Create `backend/tests/integration/test_upload_dedup_concurrency.py` по паттерну `test_conditional_error_write_concurrency.py` (sessionmaker(bind=db_engine), уникальный проект через `INSERT ... RETURNING id`, явный cleanup):

```python
"""Q6: конкурентная вставка одного (project_id, file_hash) двумя реальными сессиями.

Проверяет саму СУБД-гарантию, на которой стоит гонка upload: уникальный
констрейнт uq_documents_project_file_hash допускает ровно один INSERT;
второй получает IntegrityError ПОСЛЕ ожидания блокировки (проигравший
ждёт исхода транзакции победителя). Итог: ровно один документ.
"""
import threading

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker


def test_concurrent_insert_same_hash_yields_single_document(db_engine):
    """Две параллельные вставки одного (project, hash): один победитель, один IntegrityError."""
    Factory = sessionmaker(bind=db_engine)
    setup = Factory()
    try:
        project_id = setup.execute(text(
            "INSERT INTO projects (name) VALUES ('q6-race') RETURNING id")).scalar_one()
        setup.commit()
    finally:
        setup.close()

    file_hash = "a" * 64
    results: dict[str, str] = {}
    barrier = threading.Barrier(2)

    def inserter(tag: str) -> None:
        """Вставляет документ с общим (project, hash); фиксирует исход в results."""
        s = Factory()
        try:
            barrier.wait(timeout=5)
            s.execute(text(
                "INSERT INTO documents (project_id, filename, s3_key, status, doc_type, "
                "parse_count, parse_cost_usd, file_hash) "
                "VALUES (:p, :f, :k, 'pending', 'unknown', 0, 0, :h)"),
                {"p": project_id, "f": f"{tag}.pdf", "k": f"q6/{tag}.pdf", "h": file_hash})
            s.commit()
            results[tag] = "ok"
        except IntegrityError:
            s.rollback()
            results[tag] = "integrity_error"
        finally:
            s.close()

    threads = [threading.Thread(target=inserter, args=(t,)) for t in ("t1", "t2")]
    try:
        for t in threads:
            t.start()
    finally:
        for t in threads:
            t.join(timeout=10)

    check = Factory()
    try:
        count = check.execute(text(
            "SELECT count(*) FROM documents WHERE project_id=:p AND file_hash=:h"),
            {"p": project_id, "h": file_hash}).scalar_one()
        assert count == 1
        assert sorted(results.values()) == ["integrity_error", "ok"]
    finally:
        check.execute(text("DELETE FROM documents WHERE project_id=:p"), {"p": project_id})
        check.execute(text("DELETE FROM projects WHERE id=:p"), {"p": project_id})
        check.commit()
        check.close()
```

(Сверить NOT NULL-колонки `documents` с моделью перед запуском; INSERT-набор — по образцу AC-S0-13-теста.)

- [ ] **Step 7: Запустить всё — PASS**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-k 'upload_duplicate or race or concurrent_insert' 2>&1"`
Expected: PASS. Затем `just test-backend-integration` — прежние upload-тесты зелёные (не-дубликатный путь не изменился, добавился только hash).

- [ ] **Step 8: Lint + commit**

```bash
git add backend/crud/documents.py backend/routers/invoices.py backend/tests/
git commit -m "feat(processing): дедуп upload по file_hash — fast-path + гонка с winner-ветками (Q6)"
```

---

### Task 4: 202 + BackgroundTasks на трёх эндпоинтах (S1-1, S1-2)

**Files:**
- Modify: `backend/routers/invoices.py` (`upload_pdf:244`, `reparse_document:190`, `deskew_reparse_document:214`)
- Test: `backend/tests/integration/test_invoices.py` (адаптация существующих + структурный enqueue-тест)
- Test: `backend/tests/integration/test_upload_race_e2e.py` (create — Step 4b)

**Interfaces:**
- Consumes: `process_document` (S0), guard, дедуп из Task 3.
- Produces: все три эндпоинта → `202` + `_serialize_document` со `status="processing"`; upload дополнительно `"duplicate": False` на 202-пути. Контракт для фронта (Task 5+): `UploadResponse = DocumentDetail & { duplicate: boolean }`, `invoices: []` на свежем 202.

> TestClient-семантика (критично для адаптации тестов): Starlette выполняет BackgroundTasks ПОСЛЕ отправки ответа, но ДО возврата из `client.post(...)`. Поэтому: тело ответа — `processing` + `invoices: []`, а БД сразу после вызова — уже терминальная. Существующие тесты, читавшие СФ из тела ответа, переключаются на `GET /documents/{id}` после POST.

- [ ] **Step 1: Написать падающий структурный enqueue-тест (AC-S1-1)**

Главный контракт S1 — обработка ОТДЕЛЕНА от HTTP-запроса. Шпионить нужно за `BackgroundTasks.add_task` (НЕ за `process_document`: тот спай прошёл бы и при ошибочном инлайн-`await process_document(...)`). Спай не исполняет таску → дополнительно доказываем, что ответ вернулся ДО обработки (документ в БД остался processing).

В `test_invoices.py`:

```python
def test_upload_enqueues_via_background_tasks(client, factories, in_memory_s3,
                                              sample_pdf_bytes, monkeypatch):
    """Upload → 202/processing/invoices=[]/duplicate=false; process_document поставлен
    именно в BackgroundTasks и НЕ исполнен к моменту ответа (AC-S1-1)."""
    import io

    from fastapi import BackgroundTasks

    import processing

    calls: list[dict] = []

    def spy_add_task(self, func, *args, **kwargs):
        """Фиксирует постановку в фон, НЕ исполняя таску (документ останется processing)."""
        calls.append({"func": func, "args": args, "kwargs": kwargs})
    monkeypatch.setattr(BackgroundTasks, "add_task", spy_add_task)

    project = factories.ProjectFactory.create()
    files = {"file": ("a.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")}
    r = client.post("/api/invoices/upload", data={"project_id": project.id}, files=files)

    assert r.status_code == 202
    d = r.json()
    assert d["status"] == "processing"
    assert d["invoices"] == []
    assert d["duplicate"] is False
    # Поставлена ИМЕННО фоновая таска с правильной функцией и аргументами:
    assert len(calls) == 1
    assert calls[0]["func"] is processing.process_document
    assert calls[0]["args"][0] == d["id"]
    assert calls[0]["kwargs"]["mode"] == "parse"
    assert calls[0]["kwargs"]["pdf_bytes"] is not None
    # Таска не исполнялась → документ в БД остался processing (ответ не ждал обработку):
    g = client.get(f"/api/invoices/documents/{d['id']}")
    assert g.json()["status"] == "processing"


def test_reparse_enqueues_via_background_tasks(client, factories, in_memory_s3, monkeypatch):
    """Reparse → 202 + processing; таска в BackgroundTasks, не исполнена (AC-S1-1)."""
    from fastapi import BackgroundTasks

    import processing

    calls: list[dict] = []

    def spy_add_task(self, func, *args, **kwargs):
        """Спай постановки в фон без исполнения."""
        calls.append({"func": func, "args": args, "kwargs": kwargs})
    monkeypatch.setattr(BackgroundTasks, "add_task", spy_add_task)

    doc = factories.DocumentFactory.create(s3_key="k/r.pdf", status="parsed")
    in_memory_s3["k/r.pdf"] = b"%PDF"
    r = client.post(f"/api/invoices/documents/{doc.id}/reparse")
    assert r.status_code == 202
    assert r.json()["status"] == "processing"
    assert len(calls) == 1
    assert calls[0]["func"] is processing.process_document
    assert calls[0]["args"][0] == doc.id
    assert calls[0]["kwargs"]["mode"] == "parse"
```

> (1) Идентичность `calls[0]["func"] is processing.process_document` работает: роутер импортирует `process_document` локально (`from processing import ...`) — это тот же атрибут модуля. (2) Патчится МЕТОД КЛАССА `BackgroundTasks.add_task` — действует на инстанс, который FastAPI инжектит в эндпоинт. (3) e2e-путь (таска реально исполняется через TestClient) покрывают адаптированные тесты Step 4 — они дополняют структурные, не заменяются ими.

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-k '202' 2>&1"`
Expected: FAIL — сейчас 200 и терминальный статус в теле.

- [ ] **Step 3: Переписать три эндпоинта**

`upload_pdf` (decorator + сигнатура + хвост; дедуп-ветки Task 3 не трогать, только добавить `response.status_code = 200` в них):

```python
@router.post("/upload", status_code=202)
async def upload_pdf(
    response: Response,
    background_tasks: BackgroundTasks,
    project_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    session_factory=Depends(get_processing_session_factory),
):
    """Загрузить PDF: S3 + документ + guard, обработка в фоне; 202 немедленно (S1-1).

    Дубликат по file_hash → 200 duplicate:true с существующим документом (Q6).
    """
    ...  # валидация, hash, fast-path (Task 3) — в fast-path и winner-ветке добавить:
         #   response.status_code = 200
         #   return {**_serialize_document(existing_или_winner), "duplicate": True}
    ...  # S3-запись, create_document, гонка (Task 3)

    if not try_acquire_processing(db, doc.id):
        raise HTTPException(status_code=409, detail="Документ уже обрабатывается")

    from processing import process_document
    background_tasks.add_task(process_document, doc.id, mode="parse",
                              pdf_bytes=file_bytes, session_factory=session_factory)

    db.expire_all()
    return {**_serialize_document(get_document(db, doc.id)), "duplicate": False}
```

(`Response`, `BackgroundTasks` — импорт из fastapi.) `reparse_document`:

```python
@router.post("/documents/{doc_id}/reparse", status_code=202)
async def reparse_document(
    doc_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    session_factory=Depends(get_processing_session_factory),
):
    """Повторный парсинг в фоне: синхронные проверки + guard → 202 (S1-1)."""
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
    background_tasks.add_task(process_document, doc_id, mode="parse", session_factory=session_factory)

    db.expire_all()
    return _serialize_document(get_document(db, doc_id))
```

`deskew_reparse_document` — то же с `mode="deskew"` и БЕЗ `reraise`/`try-except ProcessingError` (фоновой таске отвечать некому; 413/502 доезжают как error+last_error — спека §1). Докстринг: «Коррекция ориентации + переразбор в фоне; ошибки ориентации доезжают статусом error + last_error через polling (S1)».

- [ ] **Step 4: Адаптировать существующие тесты**

В `test_invoices.py` (искать по grep, полный список даст прогон):
- Upload-тесты, читавшие СФ/статус из тела POST → после POST (теперь 202) делать `client.get(f"/api/invoices/documents/{doc_id}")` и ассертить терминальный результат там (TestClient уже выполнил фон). Ассерты про S3/стоимость не меняются.
- deskew-тесты 413/502 (`test_deskew_vision_failure_becomes_error_via_polling` и родственные): POST → 202; затем GET → `status == "error"`, `last_error == "Сервис распознавания ориентации недоступен"` (текст из pdf_orientation, сверить). Ассерты «S3 не тронут» сохраняются.
- 404/400/409-тесты эндпоинтов — без изменений (синхронная часть).
- Проверка S1-2: `grep -rn "rotations_applied" backend/ frontend/src/` → пусто (поле исчезло ещё в S0-свапе; если где-то всплыло — удалить).

- [ ] **Step 4b: Конкурентный e2e-тест гонки upload (полный инвариант эндпоинта)**

SQL-тест Task 3 Step 6 проверяет СУБД-гарантию; этот — КОМПОЗИЦИЮ веток эндпоинта под реальной гонкой: два параллельных upload одного файла → ровно один документ, один enqueue, один живой S3-объект, проигравший получает `200 duplicate:true`. Требует реальных сессий (транзакционная `db_session` не потокобезопасна) — отдельный модуль со своим TestClient и переопределением `get_db`.

> **Примечание (as-built):** сработал фолбэк из примечания (2) после теста ниже — anyio-портал ОДНОГО `TestClient` сериализует параллельные запросы на общем event loop, поэтому гонки на буквальной реализации фикстуры не возникает. В `backend/tests/integration/test_upload_race_e2e.py` фикстура вместо одного `client` отдаёт фабрику `make_client()`, вызываемую дважды — два независимых `TestClient`, каждый со своим порталом/event loop, что и воспроизводит реальную гонку. Код ниже — исходный вариант плана (pre-execution); как реализовано фактически — см. тест и devlog.

Create `backend/tests/integration/test_upload_race_e2e.py`:

```python
"""E2E-гонка upload: два параллельных запроса одного файла (Q6, ревью плана P2).

НЕ использует общую client-фикстуру: её get_db-override отдаёт одну
транзакционную сессию на все запросы — два потока сломали бы её. Здесь
каждый запрос получает СВОЮ реальную сессию (sessionmaker(bind=db_engine)),
данные реально коммитятся, cleanup явный. Барьер внутри обёртки
create_document выравнивает оба запроса ПОСЛЕ fast-path (оба увидели
«дубликата нет») и ПЕРЕД INSERT — гонка детерминированна.
"""
import io
import threading

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def race_client(db_engine, in_memory_s3, monkeypatch):
    """TestClient с реальными пер-запросными сессиями и спаем enqueue — зеркало
    основной client-фикстуры (conftest.py:190-236: auth-мок, CSRF double-submit,
    session-factory), но с РЕАЛЬНЫМИ сессиями вместо транзакционной.

    Возвращает (client, enqueued) — enqueued копит вызовы add_task под локом.
    """
    from unittest.mock import MagicMock

    import main
    from auth import get_current_user
    from database import get_db
    from main import app
    from processing import get_processing_session_factory

    Factory = sessionmaker(bind=db_engine)

    def real_get_db():
        """Каждому запросу — собственная реальная сессия (потокобезопасность гонки)."""
        db = Factory()
        try:
            yield db
        finally:
            db.close()

    def override_get_current_user():
        """Мок суперюзера — как в основной client-фикстуре."""
        user = MagicMock()
        user.id = 1
        user.is_superuser = True
        user.org_id = None
        user.org_role = None
        user.is_active = True
        return user

    def _sweep_noop(session_factory=None):
        """Sweep в lifespan гасится: он бил бы по реальному SessionLocal (чужая БД),
        а pending-документы этого теста должны жить (sweep не предмет теста)."""
        return 0
    monkeypatch.setattr(main, "_sweep_stuck_documents", _sweep_noop)

    enqueued: list[dict] = []
    lock = threading.Lock()

    def spy_add_task(self, func, *args, **kwargs):
        """Потокобезопасно фиксирует enqueue, не исполняя таску."""
        with lock:
            enqueued.append({"func": func, "args": args})
    monkeypatch.setattr(BackgroundTasks, "add_task", spy_add_task)

    app.dependency_overrides[get_db] = real_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    # sessionmaker поддерживает контекст-менеджер (SQLAlchemy 1.4+) — валидная фабрика
    # для process_document; спай add_task всё равно не даст таске исполниться.
    app.dependency_overrides[get_processing_session_factory] = lambda: Factory

    _csrf_token = "test-csrf-token"  # CSRF double-submit — как в основной фикстуре
    try:
        with TestClient(app, headers={"X-CSRF-Token": _csrf_token}) as c:
            c.cookies.set("csrf_token", _csrf_token)
            yield c, enqueued
    finally:
        app.dependency_overrides.clear()


def test_two_parallel_uploads_same_file(race_client, db_engine, in_memory_s3, sample_pdf_bytes, monkeypatch):
    """Гонка: 1 документ, 1 enqueue, 1 живой S3-объект, у проигравшего 200 duplicate:true."""
    client, enqueued = race_client
    Factory = sessionmaker(bind=db_engine)

    setup = Factory()
    try:
        project_id = setup.execute(text(
            "INSERT INTO projects (name) VALUES ('q6-race-e2e') RETURNING id")).scalar_one()
        setup.commit()
    finally:
        setup.close()

    import routers.invoices as inv_router
    real_create = inv_router.create_document
    barrier = threading.Barrier(2, timeout=10)

    def synced_create(db, project_id, filename, s3_key, file_hash=None):
        """Выравнивает оба запроса после fast-path и перед INSERT — гонка гарантирована."""
        barrier.wait()
        return real_create(db, project_id, filename, s3_key, file_hash=file_hash)
    monkeypatch.setattr(inv_router, "create_document", synced_create)

    results: dict[str, dict] = {}

    def do_upload(tag: str) -> None:
        """Один участник гонки: POST /upload и фиксация исхода."""
        files = {"file": (f"{tag}.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")}
        r = client.post("/api/invoices/upload", data={"project_id": project_id}, files=files)
        results[tag] = {"code": r.status_code, "body": r.json()}

    threads = [threading.Thread(target=do_upload, args=(t,)) for t in ("t1", "t2")]
    try:
        for t in threads:
            t.start()
    finally:
        for t in threads:
            t.join(timeout=30)

    check = Factory()
    try:
        codes = sorted(res["code"] for res in results.values())
        assert codes == [200, 202]                                   # победитель 202, проигравший 200
        loser = next(res for res in results.values() if res["code"] == 200)
        winner = next(res for res in results.values() if res["code"] == 202)
        assert loser["body"]["duplicate"] is True
        assert winner["body"]["duplicate"] is False
        assert loser["body"]["id"] == winner["body"]["id"]           # один и тот же документ
        count = check.execute(text(
            "SELECT count(*) FROM documents WHERE project_id=:p"), {"p": project_id}).scalar_one()
        assert count == 1                                            # ровно один документ
        assert len(enqueued) == 1                                    # ровно один enqueue
        s3_keys = [k for k in in_memory_s3 if not k.endswith(".orig")]
        assert len(s3_keys) == 1                                     # сирота проигравшего удалена
    finally:
        check.execute(text("DELETE FROM documents WHERE project_id=:p"), {"p": project_id})
        check.execute(text("DELETE FROM projects WHERE id=:p"), {"p": project_id})
        check.commit()
        check.close()
```

> Реализатору: (1) фикстура выше — зеркало conftest.client по состоянию на написание плана; перед запуском сверить, что overrides/CSRF в conftest не изменились (Task 2 добавил туда подмену sweep — здесь она своя, noop); (2) TestClient потокобезопасен для параллельных запросов (портал anyio per-request) — если наткнёшься на обратное, разнеси запросы на два TestClient над одним app; (3) `in_memory_s3` — обычный dict, GIL достаточен; ассерт по числу ключей учитывает только этот тест — если фикстура шарится, считать разницу до/после.

- [ ] **Step 5: Запустить всё — PASS**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-integration 2>&1"`
Expected: PASS весь набор, включая e2e-гонку (детерминированную по барьеру).

- [ ] **Step 6: Lint + commit**

```bash
git add backend/routers/invoices.py backend/tests/integration/test_invoices.py backend/tests/integration/test_upload_race_e2e.py
git commit -m "feat(processing): 202 + BackgroundTasks на upload/reparse/deskew, duplicate:false в контракте (S1-1)"
```

> AC-S1-2 (обрыв клиента) в CI непроверяем — ручной смоук через реальный uvicorn; фиксируется в devlog (Task 10). НЕ добавлять ложноположительный тест с закрытием response.

---

### Task 5: Фронт — тип `UploadResponse` и API-клиент

**Files:**
- Modify: `frontend/src/types/invoice.ts` (после `DocumentDetail`, ~строка 90)
- Modify: `frontend/src/services/api/upload.ts`

**Interfaces:**
- Produces: `export type UploadResponse = DocumentDetail & { duplicate: boolean }`; `uploadApi.uploadInvoice(...): Promise<UploadResponse>`. Task 8 (UploadJobRow) потребляет `result.duplicate`.

- [ ] **Step 1: Добавить тип**

В `frontend/src/types/invoice.ts` после `DocumentDetail`:

```typescript
/** Ответ POST /upload: 202 (создан, duplicate=false) или 200 (дубликат по file_hash, Q6). */
export type UploadResponse = DocumentDetail & { duplicate: boolean };
```

- [ ] **Step 2: Обновить клиент**

`frontend/src/services/api/upload.ts` — заменить `DocumentDetail` на `UploadResponse` в сигнатуре и дженерике `api.post<UploadResponse>`; импорт типа из `@/types/invoice`.

- [ ] **Step 3: Typecheck + commit**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just typecheck-frontend 2>&1"`
Expected: PASS (Upload.tsx использует `result` только как DocumentDetail-подмножество — совместимо).

```bash
git add frontend/src/types/invoice.ts frontend/src/services/api/upload.ts
git commit -m "feat(frontend): тип UploadResponse с duplicate-флагом (S1, Q6)"
```

---

### Task 6: Фронт — polling `processingRefetchInterval` (S1-5, AC-S1-4)

**Files:**
- Create: `frontend/src/services/processingRefetchInterval.ts`
- Modify: `frontend/src/services/queries.ts` (`useDocument:174-180`, `useDocuments:373-378`)
- Test: `frontend/src/services/processingRefetchInterval.test.ts` (create)

**Interfaces:**
- Produces: `export function processingRefetchInterval(query: { state: { data?: unknown } }): number | false` — 2500 при наличии нетерминального документа, иначе false. `export const NON_TERMINAL_STATUSES: ReadonlySet<string>` (реиспользуют Task 7 и Task 9).

- [ ] **Step 1: Написать падающий тест**

Create `frontend/src/services/processingRefetchInterval.test.ts`:

```typescript
import { describe, expect, it } from "vitest";

import { processingRefetchInterval } from "./processingRefetchInterval";

/** Обёртка: собирает минимальный query-объект вокруг data. */
function q(data: unknown) {
  return { state: { data } };
}

describe("processingRefetchInterval (AC-S1-4)", () => {
  it("массив с processing-документом → 2500", () => {
    expect(processingRefetchInterval(q([{ id: 1, status: "parsed" }, { id: 2, status: "processing" }]))).toBe(2500);
  });
  it("массив с pending-документом → 2500 (pending нетерминален)", () => {
    expect(processingRefetchInterval(q([{ id: 1, status: "pending" }]))).toBe(2500);
  });
  it("все терминальные → false (polling останавливается)", () => {
    expect(processingRefetchInterval(q([{ id: 1, status: "parsed" }, { id: 2, status: "error" }]))).toBe(false);
  });
  it("одиночный документ (detail-квери) → по его статусу", () => {
    expect(processingRefetchInterval(q({ id: 1, status: "processing" }))).toBe(2500);
    expect(processingRefetchInterval(q({ id: 1, status: "error" }))).toBe(false);
  });
  it("нет данных → false", () => {
    expect(processingRefetchInterval(q(undefined))).toBe(false);
  });
});
```

- [ ] **Step 2: Запустить — FAIL (модуля нет)**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-frontend 2>&1"`
Expected: FAIL — cannot resolve `./processingRefetchInterval`.

- [ ] **Step 3: Реализовать**

Create `frontend/src/services/processingRefetchInterval.ts`:

```typescript
/** Нетерминальные статусы документа: обработка ещё идёт, данные изменятся. */
export const NON_TERMINAL_STATUSES: ReadonlySet<string> = new Set(["pending", "processing"]);

const POLL_MS = 2500;

type DocLike = { status?: string };

/**
 * Колбэк для refetchInterval (react-query v5): 2500 мс, пока в данных квери
 * есть документ в нетерминальном статусе, иначе false — polling останавливается (S1-5).
 * Данные нормализуются: list-квери отдаёт массив, detail — одиночный объект.
 */
export function processingRefetchInterval(query: { state: { data?: unknown } }): number | false {
  const data = query.state.data;
  const docs: DocLike[] = Array.isArray(data) ? data : data ? [data as DocLike] : [];
  return docs.some((d) => NON_TERMINAL_STATUSES.has(d?.status ?? "")) ? POLL_MS : false;
}
```

- [ ] **Step 4: Подключить к хукам**

В `queries.ts` — `useDocument` и `useDocuments` получают `refetchInterval: processingRefetchInterval` (+ импорт):

```typescript
export function useDocument(docId: ID | null | undefined) {
  return useQuery({
    queryKey: qk.documents.detail(docId ?? -1),
    queryFn: () => invoicesApi.getDocument(docId as ID),
    enabled: docId !== null && docId !== undefined,
    refetchInterval: processingRefetchInterval,
  });
}
```

(аналогично `useDocuments`).

- [ ] **Step 5: Тесты + lint + commit**

Run: `just test-frontend` → PASS; `just lint-frontend` + `just typecheck-frontend` → PASS.

```bash
git add frontend/src/services/processingRefetchInterval.ts frontend/src/services/processingRefetchInterval.test.ts frontend/src/services/queries.ts
git commit -m "feat(frontend): polling processingRefetchInterval на useDocuments/useDocument (S1-5)"
```

---

### Task 7: Фронт — детектор терминального перехода + замена тостов (S1-7)

**Files:**
- Create: `frontend/src/services/terminalTransition.ts`
- Modify: `frontend/src/App.tsx:28-37` (подписка рядом с QueryClient)
- Modify: `frontend/src/services/queries.ts:186-206` (тосты reparse/deskew)
- Test: `frontend/src/services/terminalTransition.test.ts` (create)

**Interfaces:**
- Consumes: `NON_TERMINAL_STATUSES` (Task 6); ключи `["documents", ...]` / `["document", id]` (queryKeys.ts).
- Produces: `export function createTerminalTransitionListener(queryClient: QueryClient): (event: QueryCacheNotifyEvent) => void` (чистая, тестируемая); `export function subscribeTerminalTransitions(queryClient: QueryClient): () => void` (единственная подписка + HMR-guard).

- [ ] **Step 1: Написать падающие тесты (3 случая из спеки §7)**

Create `frontend/src/services/terminalTransition.test.ts`:

```typescript
import { QueryClient } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createTerminalTransitionListener } from "./terminalTransition";

/** Собирает updated-событие QueryCache с заданными queryKey и data. */
function updatedEvent(queryKey: readonly unknown[], data: unknown) {
  return { type: "updated", query: { queryKey, state: { data } } } as never;
}

describe("terminal transition detector (S1-7, спека §5)", () => {
  let qc: QueryClient;
  let invalidate: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    qc = new QueryClient();
    invalidate = vi.fn();
    qc.invalidateQueries = invalidate as never;
  });

  it("переход processing→parsed в одной квери → инвалидация ровно один раз", () => {
    const listen = createTerminalTransitionListener(qc);
    listen(updatedEvent(["documents", 1], [{ id: 7, project_id: 1, status: "processing" }]));
    expect(invalidate).not.toHaveBeenCalled();
    listen(updatedEvent(["documents", 1], [{ id: 7, project_id: 1, status: "parsed" }]));
    // 3 вызова = documents + document + dashboard за ОДИН переход
    expect(invalidate).toHaveBeenCalledTimes(3);
  });

  it("тот же переход из list- И detail-квери → всё равно один набор инвалидаций (общая Map)", () => {
    const listen = createTerminalTransitionListener(qc);
    listen(updatedEvent(["documents", 1], [{ id: 7, project_id: 1, status: "processing" }]));
    listen(updatedEvent(["document", 7], { id: 7, project_id: 1, status: "processing" }));
    listen(updatedEvent(["documents", 1], [{ id: 7, project_id: 1, status: "parsed" }]));
    listen(updatedEvent(["document", 7], { id: 7, project_id: 1, status: "parsed" }));
    expect(invalidate).toHaveBeenCalledTimes(3); // не 6: второй репорт видит обновлённую Map
  });

  it("первое наблюдение терминального документа → ноль инвалидаций", () => {
    const listen = createTerminalTransitionListener(qc);
    listen(updatedEvent(["documents", 1], [{ id: 7, project_id: 1, status: "parsed" }]));
    expect(invalidate).not.toHaveBeenCalled();
  });

  it("чужие квери игнорируются", () => {
    const listen = createTerminalTransitionListener(qc);
    listen(updatedEvent(["dashboard", "summary", 1], [{ id: 7, status: "processing" }]));
    listen(updatedEvent(["dashboard", "summary", 1], [{ id: 7, status: "parsed" }]));
    expect(invalidate).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Запустить — FAIL (модуля нет)**

Run: `just test-frontend` → FAIL cannot resolve `./terminalTransition`.

- [ ] **Step 3: Реализовать детектор**

Create `frontend/src/services/terminalTransition.ts`:

```typescript
import type { QueryCacheNotifyEvent, QueryClient } from "@tanstack/react-query";

import { NON_TERMINAL_STATUSES } from "./processingRefetchInterval";

const TERMINAL_STATUSES: ReadonlySet<string> = new Set(["parsed", "error"]);

type DocLike = { id?: number | string; status?: string };

/**
 * Детектор терминального перехода (S1-7, спека §5): одна общая Map<docId, status>
 * на приложение. Обрабатывает только updated-события квери documents (list) /
 * document (detail); data нормализуется к массиву; Map обновляется ДО
 * invalidateQueries; первое наблюдение документа переходом не считается.
 *
 * Семантика — AT-LEAST-ONCE (спека §5 п.5): запоздалый out-of-order ответ
 * (in-flight detail-запрос со старым processing, донесённый после перехода)
 * откатывает Map, и следующий свежий ответ даёт ПОВТОРНУЮ инвалидацию.
 * Это осознанно допустимо: инвалидация идемпотентна, цена — лишний refetch
 * в редкой гонке. Блокировать откат нельзя — легитимный даунгрейд
 * (новый reparse: parsed → processing) обязан записываться, иначе следующий
 * терминальный переход не сработает; отличить их на этом уровне нечем.
 */
export function createTerminalTransitionListener(queryClient: QueryClient) {
  const lastStatus = new Map<number | string, string>();

  return (event: QueryCacheNotifyEvent): void => {
    if (event.type !== "updated") return;
    const key0 = event.query.queryKey[0];
    if (key0 !== "documents" && key0 !== "document") return;

    const data: unknown = event.query.state.data;
    const docs: DocLike[] = Array.isArray(data) ? data : data ? [data as DocLike] : [];
    for (const doc of docs) {
      if (doc?.id === undefined || !doc.status) continue;
      const prev = lastStatus.get(doc.id);
      lastStatus.set(doc.id, doc.status); // до invalidate — иначе синхронный ре-репорт задвоит
      if (prev !== undefined && NON_TERMINAL_STATUSES.has(prev) && TERMINAL_STATUSES.has(doc.status)) {
        // Терминальный переход: свежие данные нужны спискам, карточке и dashboard.
        // Операционного тоста НЕТ — детектор не знает, какая операция шла (спека §5).
        // ["dashboard"] префиксом целиком, а не по-проектно — ОСОЗНАННОЕ упрощение
        // относительно спеки §5 п.5: projectId лежит в трёх семействах dashboard-ключей
        // на разных позициях, точечная инвалидация потребовала бы predicate по трём
        // формам; цена префикса — лишний refetch дашборда другого проекта, если тот
        // вдруг смонтирован. Существующие мутации инвалидируют так же.
        queryClient.invalidateQueries({ queryKey: ["documents"] });
        queryClient.invalidateQueries({ queryKey: ["document", doc.id] });
        queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      }
    }
  };
}

let subscribed = false;

/**
 * Единственная подписка детектора на QueryCache. Повторный вызов (HMR) — no-op;
 * возвращённый cleanup снимает подписку и разрешает новую.
 */
export function subscribeTerminalTransitions(queryClient: QueryClient): () => void {
  if (subscribed) return () => {};
  subscribed = true;
  const unsubscribe = queryClient
    .getQueryCache()
    .subscribe(createTerminalTransitionListener(queryClient));
  return () => {
    subscribed = false;
    unsubscribe();
  };
}
```

- [ ] **Step 4: Подписать в App.tsx + заменить тосты**

В `App.tsx` после создания `queryClient` (строка ~37):

```typescript
import { subscribeTerminalTransitions } from "@/services/terminalTransition";
// ...
subscribeTerminalTransitions(queryClient);
```

В `queries.ts`: `useReparseDocument` — `toast.success("Документ переразобран")` → `toast.success("Обработка запущена")`; `useDeskewReparseDocument` — `toast.success("Документ выпрямлен и переразобран")` → `toast.success("Обработка запущена")`. Существующие инвалидации в их onSuccess ОСТАВИТЬ (свежий processing-статус нужен немедленно — polling подхватит).

- [ ] **Step 5: Тесты + lint + commit**

Run: `just test-frontend` → PASS (включая существующие — если какой-то тест ассертил старый текст тоста, обновить на «Обработка запущена»); `just lint-frontend`; `just typecheck-frontend`.

```bash
git add frontend/src/services/terminalTransition.ts frontend/src/services/terminalTransition.test.ts frontend/src/App.tsx frontend/src/services/queries.ts
git commit -m "feat(frontend): детектор терминального перехода на QueryCache + тосты «Обработка запущена» (S1-7)"
```

---

### Task 8: Фронт — `<UploadJobRow>` + Upload-страница (S1-6, AC-S1-5)

**Files:**
- Create: `frontend/src/components/upload/UploadJobRow.tsx`
- Modify: `frontend/src/pages/Upload.tsx` (JobState.result: UploadResponse; рендер строк через UploadJobRow; убрать `j.result.invoices` со строки 170)
- Test: `frontend/src/components/upload/UploadJobRow.test.tsx` (create)

**Interfaces:**
- Consumes: `UploadResponse` (Task 5), `useDocument` с polling (Task 6).
- Produces: `<UploadJobRow job={JobState} />` — экспорт `JobState` переезжает в UploadJobRow.tsx (Upload.tsx импортирует).

- [ ] **Step 1: Написать падающий компонентный тест**

Create `frontend/src/components/upload/UploadJobRow.test.tsx` (паттерн — существующий `ErrorDocsTab.test.tsx`: QueryClientProvider + MSW из `src/test/handlers.ts`; сверить реальные хелперы renderWithProviders, при расхождении адаптировать):

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";

import { server } from "@/test/server";
import { UploadJobRow, type JobState } from "./UploadJobRow";

/** Рендер строки job'а с провайдерами react-query и роутера. */
function renderRow(job: JobState) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <UploadJobRow job={job} />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const baseDoc = {
  id: 7, project_id: 1, filename: "a.pdf", doc_type: "invoice", status: "processing",
  last_error: null, uploaded_at: "2026-07-19T10:00:00", invoice_count: 0, has_issues: false,
  ai_confidence: null, parse_cost_usd: 0, parse_count: 0, invoices: [],
};

describe("UploadJobRow (S1-6, AC-S1-5)", () => {
  it("после 202 показывает «обрабатывается» из статуса документа", async () => {
    server.use(http.get("*/api/invoices/documents/7", () => HttpResponse.json(baseDoc)));
    renderRow({ id: "j1", file: new File([], "a.pdf"), status: "ready", progress: 100,
                result: { ...baseDoc, duplicate: false } });
    expect(await screen.findByText(/обрабатывается/i)).toBeInTheDocument();
  });

  it("после завершения показывает СФ из данных polling'а (query-кэш, не снапшот ответа)", async () => {
    server.use(http.get("*/api/invoices/documents/7", () => HttpResponse.json({
      ...baseDoc, status: "parsed", invoice_count: 1,
      invoices: [{ id: 11, document_id: 7, number: "СФ-1", date: "2026-07-01",
                   supplier_name: null, supplier_inn: null, vat_rate: 20, ai_confidence: 0.9,
                   verified: false, verified_at: null, has_issues: false, items: [] }],
    })));
    renderRow({ id: "j1", file: new File([], "a.pdf"), status: "ready", progress: 100,
                result: { ...baseDoc, duplicate: false } });
    expect(await screen.findByText(/СФ № СФ-1/)).toBeInTheDocument();
  });

  it("дубликат: бейдж «Файл уже был загружен» + ссылка на документ, не error-стиль", () => {
    renderRow({ id: "j1", file: new File([], "a.pdf"), status: "ready", progress: 100,
                result: { ...baseDoc, status: "parsed", duplicate: true } });
    expect(screen.getByText("Файл уже был загружен")).toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveAttribute("href", "/documents/7");
  });
});
```

- [ ] **Step 2: Запустить — FAIL (компонента нет)**

Run: `just test-frontend` → FAIL cannot resolve UploadJobRow.

- [ ] **Step 3: Реализовать `UploadJobRow`**

Create `frontend/src/components/upload/UploadJobRow.tsx` — переносит из Upload.tsx рендер одной строки (иконки/пиллы локального этапа БЕЗ изменений) и добавляет серверный этап:

```tsx
import { Link } from "react-router-dom";
import { AlertTriangle, CheckCircle2, FileText, Loader2 } from "lucide-react";

import { Surface } from "@/components/ui-domain/Surface";
import { StatusPill } from "@/components/ui-domain/StatusPill";
import { ConfidenceBadge } from "@/components/ui-domain/ConfidenceBadge";
import { Button } from "@/components/ui-domain/Button";
import { useDocument } from "@/services/queries";
import type { UploadResponse } from "@/types/invoice";

export interface JobState {
  id: string;
  file: File;
  status: "pending" | "uploading" | "ready" | "error";
  progress: number;
  result?: UploadResponse;
  error?: string;
}

/**
 * Строка задания загрузки: локальный этап (pending|uploading|error загрузки)
 * + серверный этап после 202 — статус документа из query-кэша (polling S1-5).
 * Терминальное состояние строки привязано к статусу документа, СФ рендерятся
 * из данных квери, не из снапшота ответа (S1-6). Дубликат — нейтральный бейдж.
 */
export function UploadJobRow({ job }: { job: JobState }) {
  // enabled: хук не дёргает сеть, пока 202 не принят (нет result.id).
  const docQ = useDocument(job.result?.id ?? null);
  const doc = docQ.data ?? job.result;
  const isDuplicate = job.result?.duplicate === true;
  const serverBusy = doc != null && (doc.status === "pending" || doc.status === "processing");
  const serverError = doc?.status === "error";

  return (
    <Surface padding="sm">
      <div className="flex items-start gap-4">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-md bg-surface-sunken">
          {(job.status === "uploading" || serverBusy) && (
            <Loader2 size={16} className="animate-spin text-accent" />
          )}
          {job.status === "ready" && !serverBusy && !serverError && (
            <CheckCircle2 size={16} className="text-accent" />
          )}
          {(job.status === "error" || serverError) && (
            <AlertTriangle size={16} className="text-danger" />
          )}
          {job.status === "pending" && <FileText size={16} className="text-fg-tertiary" />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium text-fg">{job.file.name}</span>
            {job.status === "uploading" && <StatusPill tone="info" label={`${job.progress}%`} />}
            {isDuplicate && <StatusPill tone="info" label="Файл уже был загружен" />}
            {job.status === "ready" && serverBusy && (
              <StatusPill tone="info" label="обрабатывается" dot />
            )}
            {job.status === "ready" && doc?.status === "parsed" && (
              <StatusPill tone="success" label="готово" dot />
            )}
            {serverError && <StatusPill tone="danger" label={doc?.last_error || "ошибка"} dot />}
            {job.status === "error" && <StatusPill tone="danger" label="ошибка" dot />}
          </div>
          {doc != null && "invoices" in doc && doc.invoices.length > 0 && (
            <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-fg-secondary">
              {doc.invoices.map((inv) => (
                <span key={inv.id} className="flex items-center gap-1.5">
                  СФ № {inv.number} · {inv.items.length} позиций
                  <ConfidenceBadge value={inv.ai_confidence} />
                </span>
              ))}
            </div>
          )}
          {job.error && <div className="mt-1 text-xs text-danger-text">{job.error}</div>}
        </div>
        {job.result && (
          <Link to={`/documents/${job.result.id}`}>
            <Button variant="secondary" size="sm">Проверить</Button>
          </Link>
        )}
      </div>
    </Surface>
  );
}
```

(`useDocument(null)` отключён через существующий `enabled: docId !== null && docId !== undefined` — сверить; данные квери типизированы DocumentDetail, `"invoices" in doc` отделяет detail от возможного summary.)

- [ ] **Step 4: Переписать Upload.tsx**

- `JobState` импортируется из UploadJobRow (локальное определение удалить), `result?: UploadResponse`.
- `jobs.map(...)` → `{jobs.map((j) => <UploadJobRow key={j.id} job={j} />)}`; JSX строки (Surface с иконками, строки 137-190) удалить из Upload.tsx.
- Тост в handleDrop: `toast.success(\`«${job.file.name}» загружен\`)` → для дубликата различить: `result.duplicate ? toast.info(\`«${job.file.name}» — файл уже был загружен\`) : toast.success(\`«${job.file.name}» принят в обработку\`)`.

- [ ] **Step 5: Тесты + lint + commit**

Run: `just test-frontend` → PASS (3 новых + прежние); `just lint-frontend`; `just typecheck-frontend`.

```bash
git add frontend/src/components/upload/ frontend/src/pages/Upload.tsx
git commit -m "feat(frontend): UploadJobRow — серверный этап job'а из polling, UX дубликата (S1-6)"
```

---

### Task 9: Фронт — дизейбл мутаций и бейдж «Обрабатывается» (S1-6)

**Files:**
- Modify: `frontend/src/services/processingRefetchInterval.ts` (хелпер `isDocBusy`)
- Modify: `frontend/src/pages/Review.tsx` (кнопки reparse/deskew, verify/edit/delete СФ, delete документа)
- Modify: `frontend/src/pages/ProjectPage.tsx` (бейдж статуса в списке документов)
- Test: `frontend/src/pages/Review.test.tsx` (существует — дополнить)

**Interfaces:**
- Consumes: `NON_TERMINAL_STATUSES` (Task 6) — единственный источник истины для «busy».
- Produces: `isDocBusy(status)` в processingRefetchInterval.ts.

> **ErrorDocsTab НЕ трогаем** (уточнение ревью плана): его строки терминальны по построению — фильтр `error || has_issues`, где `has_issues` вычисляется только для `parsed`, а busy-документ не попадает в выборку. После клика reparse документ уходит в processing и исчезает из вкладки на ближайшем refetch; окно до refetch закрывает существующий mutation-state (`isPending`) кнопки. Тест busy-состояния в ErrorDocsTab не увидел бы строку вовсе — основное покрытие идёт в `Review.test.tsx`.

- [ ] **Step 1: Хелпер и падающий тест**

В `frontend/src/services/processingRefetchInterval.ts` добавить:

```typescript
/** Документ в обработке: мутации запрещены (совпадает с 409-контрактом бэка S1). */
export function isDocBusy(status: string | undefined): boolean {
  return NON_TERMINAL_STATUSES.has(status ?? "");
}
```

В `frontend/src/pages/Review.test.tsx` (существует — паттерны рендера/MSW взять из него же) добавить тест: документ со `status: "processing"` в detail-ответе → кнопки reparse/deskew/verify/delete задизейблены (`toBeDisabled()`; точные селекторы кнопок — по фактическим текстам/testid в Review.tsx).

- [ ] **Step 2: Запустить — FAIL**

Run: `just test-frontend` → новый тест FAIL (кнопки активны).

- [ ] **Step 3: Применить дизейбл + бейдж**

- `Review.tsx`: у кнопок reparse/deskew, verify/unverify, редактирования и удаления СФ, удаления документа **ДОБАВИТЬ busy-условие К СУЩЕСТВУЮЩЕМУ, не заменяя его**:

  ```tsx
  disabled={existingCondition || isDocBusy(doc.status)}
  ```

  где `existingCondition` — то, что уже стоит в атрибуте (mutation `isPending`, verified-условия и т.п.). Если у кнопки `disabled` не было — просто `disabled={isDocBusy(doc.status)}`. Точные места — grep по `useReparseDocument|useDeskewReparseDocument|useVerifyInvoice|useDeleteInvoice|useDeleteDocument|disabled` в Review.tsx; существующую логику обработчиков и условий НЕ менять.
- `ProjectPage.tsx`: в рендере строки документа (таблица документов) добавить `{isDocBusy(doc.status) && <StatusPill tone="info" label="Обрабатывается" dot />}` — рядом с существующим статус-рендером (сверить структуру ячейки по факту).

- [ ] **Step 4: Тесты + lint + commit**

Run: `just test-frontend` → PASS; `just lint-frontend`; `just typecheck-frontend`.

```bash
git add frontend/src/services/processingRefetchInterval.ts frontend/src/pages/
git commit -m "feat(frontend): дизейбл мутаций для pending|processing + бейдж «Обрабатывается» (S1-6)"
```

---

### Task 10: Финальная проверка + документация

**Files:**
- Modify: `docs/agent/pdf-parsing.md` (202-контракт, polling, sweep — если не покрыто Task 2)
- Create: `docs/devlog/2026-07-19-async-processing-stage-1.md`

- [ ] **Step 1: Полный прогон**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just lint 2>&1"`
Then: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test 2>&1"`
Expected: всё зелёное, 0 warnings backend.

- [ ] **Step 2: Ручной смоук (зафиксировать результат в devlog)**

Поднять `just dev-backend` + `just dev-frontend`; загрузить PDF: (а) ответ мгновенный, строка «обрабатывается», СФ появляются по завершении; (б) AC-S1-2: повторить upload и закрыть вкладку до завершения → документ в БД доходит до `parsed`; (в) повторная загрузка того же файла → «Файл уже был загружен»; (г) рестарт бэка посреди обработки → документ в `error` «Обработка прервана перезапуском сервера».

- [ ] **Step 3: Devlog + доки**

`docs/devlog/2026-07-19-async-processing-stage-1.md` по образцу stage-0-devlog: что отгружено (S1-1…7, Q6), ключевые решения (QueryCache-детектор, UploadJobRow, no-overlap инвариант), результаты смоука, AC-таблица. В `docs/agent/pdf-parsing.md` — актуализировать раздел обработки (202-контракт, polling 2500мс, sweep, дедуп).

- [ ] **Step 4: Commit**

```bash
git add docs/
git commit -m "docs(processing): архитектура S1, devlog, смоук-результаты (S1)"
```

---

## Покрытие спеки (self-review)

| Спека §/AC | Задача |
|---|---|
| §1 202-контракт, deskew без 413/502, S1-2 | Task 4 |
| §1 UploadResponse-контракт | Task 4 (бэк) + Task 5 (фронт) |
| §2 дедуп: fast-path, гонка (winner/None), create_document(file_hash), дубль-в-processing, дубль-error-документа | Task 3 (+UX Task 8) |
| §3 sweep pending+processing, no-overlap инвариант, AC-S1-3b | Task 2 |
| §4 polling обоих хуков | Task 6 |
| §5 детектор (алгоритм 1-6), без тоста на переходе | Task 7 |
| §6 тосты-замена, UploadJobRow+enabled, дубликат-UX, бейдж, дизейбл, guard pending (сервер) | Task 7, 8, 9, 1 |
| AC-S1-1 структурный enqueue | Task 4 |
| AC-S1-2 смоук | Task 10 (не CI — спека §7) |
| AC-S1-3 / 3b | Task 2 |
| AC-S1-4 | Task 6 |
| AC-S1-5 | Task 8 |
| AC-S1-6 E2E | вне S1 (спека) |
| §7 конкурентный dedup-тест | Task 3 Step 6 |

**Примечания к порядку:** Task 1-4 backend (1, 2, 3 независимы; 4 зависит от 3), Task 5-9 frontend (6 до 7 и 9 — экспортирует NON_TERMINAL_STATUSES; 5 до 8). Строго последовательное исполнение 1→10 корректно.
