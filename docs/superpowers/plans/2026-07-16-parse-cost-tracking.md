# Parse Cost Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Показывать реальную стоимость ИИ-разбора каждого документа в $, накапливая её по всем разборам (upload + reparse + deskew-reparse).

**Architecture:** OpenRouter возвращает реальную стоимость вызова (`usage.cost` при `usage: {include: true}`). `parse_invoice_pdf` захватывает её сразу после ответа API и отдаёт на всех платных ветках через хелпер `_with_cost`. Роутеры (`upload_pdf`, `_reparse_from_s3`) накапливают её в две новые колонки `documents` (`parse_cost_usd`, `parse_count`). Сериализация отдаёт поля на фронт; Review-страница показывает их в шапке документа.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy (sync), Alembic, pydantic-settings, httpx, respx (тесты) · React 19 + TS + Vitest.

**Спека:** [docs/superpowers/specs/2026-07-16-parse-cost-tracking-design.md](../specs/2026-07-16-parse-cost-tracking-design.md)

## Global Constraints

- Деньги — только `Decimal`, конверсия из float/JSON через `Decimal(str(x))` (в проекте это хелпер `crud.documents._dec`).
- Колонки стоимости: `parse_cost_usd` — `Numeric(10, 6)`, `parse_count` — `Integer`; обе `NOT NULL`, `server_default="0"`.
- Миграции: **только** `alembic revision --autogenerate` → применение через `just db-migrate`. Файлы в `backend/alembic/versions/` руками не править.
- Докстринг на каждой новой/изменённой функции/методе, включая тесты и приватные хелперы (правило AGENTS.md, порог покрытия ≥80%).
- Команды — через `just`, не `cd backend && ...`. Shell на Windows: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just <cmd> 2>&1"`.
- Валюта — USD, без конвертации в ₽.
- Перед завершением — `just lint` и `just test` зелёные.

---

### Task 1: Схема БД — колонки стоимости на `documents`

**Files:**
- Modify: `backend/models.py` (класс `Document`, ~строки 251-271)
- Create: `backend/alembic/versions/<hash>_add_parse_cost_tracking.py` (генерируется автоматически, руками не править)
- Test: `backend/tests/integration/test_invoices.py`

**Interfaces:**
- Produces: `Document.parse_cost_usd: Decimal` (default `Decimal("0")`), `Document.parse_count: int` (default `0`) — используются в Task 3.

- [ ] **Step 1: Написать падающий тест дефолтов**

В `backend/tests/integration/test_invoices.py` добавить:

```python
def test_new_document_defaults_parse_cost_zero(db_session, factories):
    """Свежесозданный документ имеет нулевую стоимость и нулевой счётчик разборов."""
    from decimal import Decimal

    from crud.documents import create_document

    project = factories.ProjectFactory.create()
    doc = create_document(db_session, project.id, "x.pdf", "2026/07/x.pdf")

    assert doc.parse_cost_usd == Decimal("0")
    assert doc.parse_count == 0
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `just test-backend-integration` (или `cd backend && pytest tests/integration/test_invoices.py::test_new_document_defaults_parse_cost_zero -v`)
Expected: FAIL — `AttributeError: 'Document' object has no attribute 'parse_cost_usd'`.

- [ ] **Step 3: Добавить колонки в модель**

В `backend/models.py`, класс `Document`, после строки `uploaded_at = Column(...)` (перед `__table_args__`):

```python
    # Стоимость ИИ-разбора (OpenRouter usage.cost), накопительно по всем разборам
    # документа — upload + reparse + deskew-reparse. USD. Numeric(10,6): доли цента.
    parse_cost_usd = Column(Numeric(10, 6), nullable=False, server_default="0")
    # Число платных вызовов OpenRouter по этому документу (честность накопления).
    parse_count = Column(Integer, nullable=False, server_default="0")
```

`Numeric` и `Integer` уже импортированы в `models.py` (проверить строку `from sqlalchemy import ...`; если `Numeric` отсутствует — добавить в импорт).

- [ ] **Step 4: Сгенерировать и применить миграцию**

Run:
```
cd backend && alembic revision --autogenerate -m "add parse cost tracking to documents"
```
Затем **прочитать** сгенерированный файл в `backend/alembic/versions/`: убедиться, что `upgrade()` делает `op.add_column("documents", sa.Column("parse_cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"))` и аналогично `parse_count`, а `downgrade()` их дропает. Если автоген выдал лишние изменения (не связанные с этими колонками) — **не** править файл руками: удалить миграцию, поправить модель, перегенерировать.

Применить:
```
just db-migrate
```
Expected: `Running upgrade ... add parse cost tracking to documents`.

- [ ] **Step 5: Запустить тест — убедиться, что проходит**

Run: `just test-backend-integration` (тот же тест)
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/models.py backend/alembic/versions backend/tests/integration/test_invoices.py
git commit -m "feat(be): колонки parse_cost_usd/parse_count на documents"
```

---

### Task 2: Захват стоимости в `pdf_parser`

**Files:**
- Modify: `backend/pdf_parser.py`
- Test: `backend/tests/unit/test_pdf_parser_helpers.py`

**Interfaces:**
- Produces: `_with_cost(result: dict, cost: Decimal | None) -> dict` — кладёт `result["parse_cost_usd"] = cost`, только если `cost is not None`. `parse_invoice_pdf` возвращает `parse_cost_usd: Decimal` во всех результатах после HTTP 200 (успех, unknown, обрезка, битый JSON, провал сверки, падение после ответа). Потребляется Task 3.

- [ ] **Step 1: Написать падающий тест хелпера `_with_cost`**

В `backend/tests/unit/test_pdf_parser_helpers.py` добавить:

```python
def test_with_cost_adds_key_when_cost_known():
    """cost известен → ключ parse_cost_usd появляется в результате."""
    from decimal import Decimal

    from pdf_parser import _with_cost

    result = _with_cost({"error": "boom"}, Decimal("0.0021"))
    assert result["parse_cost_usd"] == Decimal("0.0021")


def test_with_cost_skips_key_when_cost_none():
    """cost is None (вызов не состоялся) → ключа нет."""
    from pdf_parser import _with_cost

    result = _with_cost({"error": "timeout"}, None)
    assert "parse_cost_usd" not in result
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `just test-backend-unit` (или `cd backend && pytest tests/unit/test_pdf_parser_helpers.py -v`)
Expected: FAIL — `ImportError: cannot import name '_with_cost'`.

- [ ] **Step 3: Добавить импорт `Decimal` и хелпер**

В `backend/pdf_parser.py` в начало (рядом с прочими импортами) добавить:

```python
from decimal import Decimal
```

После блока констант (`OPENROUTER_URL = ...`), перед `async def parse_invoice_pdf`:

```python
def _with_cost(result: dict, cost: Decimal | None) -> dict:
    """Кладёт стоимость вызова в результат, только если вызов OpenRouter состоялся.

    Структурный инвариант «был HTTP 200 → в ответе есть parse_cost_usd» без
    перечисления веток: каждый return после response.json() оборачивается этим
    хелпером, а до платного ответа cost остаётся None и ключ не добавляется.
    """
    if cost is not None:
        result["parse_cost_usd"] = cost
    return result
```

- [ ] **Step 4: Запустить тест хелпера — убедиться, что проходит**

Run: `just test-backend-unit`
Expected: оба новых теста PASS.

- [ ] **Step 5: Запросить стоимость и захватить её на всех платных ветках**

В `backend/pdf_parser.py`, в `parse_invoice_pdf`:

**(a)** Первой строкой тела функции (до `try`) объявить `cost`:

```python
async def parse_invoice_pdf(file_data: bytes, db: Session, document_id: int) -> dict:
    """..."""  # существующий докстринг
    cost: Decimal | None = None  # стоимость вызова; заполняется после HTTP 200
    logger.info(...)  # существующая строка
```

**(b)** В `payload` добавить запрос стоимости (рядом с `"model": model,`):

```python
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "usage": {"include": True},  # OpenRouter вернёт реальный usage.cost ($)
            "plugins": [ ... ],          # существующее
            "messages": [ ... ],         # существующее
        }
```

**(c)** Сразу после `data = response.json()` (сейчас строка ~194), до чтения `usage`/`finish_reason`:

```python
        data = response.json()
        cost = Decimal(str(data.get("usage", {}).get("cost") or 0))
        logger.info(f"[doc={document_id}] Стоимость вызова OpenRouter: ${cost}")
```

> **Амендменты (пост-ревью Codex, коммит `248144d`)** — реализация отошла от плана в двух местах:
> 1. `cost = Decimal(0)` присваивается **до** `response.json()` (сразу после `status_code == 200`),
>    чтобы 200 с непарсящимся телом тоже биллился. Захват `usage.cost` уточняет значение после парсинга.
> 2. Накопление в роутере (Task 3) — атомарный SQL-инкремент `doc.parse_cost_usd = Document.parse_cost_usd + ...`
>    вместо `+=` (защита от гонки параллельных reparse). Итоговое состояние см. в
>    [devlog](../../devlog/2026-07-16-parse-cost-tracking.md).

**(d)** Обернуть **каждый** `return` после этой точки в `_with_cost(..., cost)`:

- ветка `finish_reason == "length"`:
  ```python
          return _with_cost({"error": "Ответ модели обрезан по лимиту токенов — часть позиций счёта потеряна. Попробуйте повторить разбор."}, cost)
  ```
- ветка `except json.JSONDecodeError`:
  ```python
          return _with_cost({"error": "Не удалось разобрать ответ модели (невалидный JSON)"}, cost)
  ```
- ветка `doc_type != "invoice"`:
  ```python
          return _with_cost({"doc_type": "unknown", "error": "Документ не является счётом-фактурой"}, cost)
  ```
- ветка провала `_reconcile_totals`:
  ```python
              return _with_cost({"error": f"Разбор счёта №{inv_number} неполный: {reconcile_detail}"}, cost)
  ```
- финальный success:
  ```python
          return _with_cost({"doc_type": "invoice", "invoices_created": invoices_created}, cost)
  ```
- `except Exception`:
  ```python
      except Exception as e:
          logger.exception(f"[doc={document_id}] Неожиданная ошибка парсинга")
          return _with_cost({"error": f"Ошибка парсинга: {str(e)}"}, cost)
  ```

**Не** оборачивать: ветку `response.status_code != 200` и `except httpx.TimeoutException` — там `cost is None` (платного ответа не было). Оставить их `return`-ы как есть.

- [ ] **Step 6: Запустить весь unit-слой — убедиться, что ничего не сломано**

Run: `just test-backend-unit`
Expected: PASS (существующие тесты helpers + два новых).

- [ ] **Step 7: Commit**

```bash
git add backend/pdf_parser.py backend/tests/unit/test_pdf_parser_helpers.py
git commit -m "feat(be): захват usage.cost в pdf_parser на всех платных ветках"
```

---

### Task 3: Накопление и сериализация в роутере

**Files:**
- Modify: `backend/routers/invoices.py` (`_serialize_document` ~67, `list_documents` ~121-140, `_reparse_from_s3` ~178-193, `upload_pdf` ~301-317)

**Interfaces:**
- Consumes: `result["parse_cost_usd"]` из Task 2; `Document.parse_cost_usd`/`parse_count` из Task 1.
- Produces: сериализованный документ содержит `parse_cost_usd: float`, `parse_count: int` — потребляется фронтом (Task 5).

- [ ] **Step 1: Добавить поля в сериализацию `_serialize_document`**

В `backend/routers/invoices.py`, в dict внутри `_serialize_document`, рядом с `"ai_confidence": _avg_confidence(doc),`:

```python
        "parse_cost_usd": float(doc.parse_cost_usd),
        "parse_count": doc.parse_count,
```

- [ ] **Step 2: Добавить поля в `list_documents`**

В том же файле, в dict внутри `list_documents` (рядом с `"ai_confidence": ...`):

```python
            "parse_cost_usd": float(doc.parse_cost_usd),
            "parse_count": doc.parse_count,
```

- [ ] **Step 3: Накопление в `_reparse_from_s3`**

Сразу после `result = await parse_invoice_pdf(pdf_bytes, db, doc.id)` и **до** `if result.get("error"):`:

```python
    result = await parse_invoice_pdf(pdf_bytes, db, doc.id)

    if "parse_cost_usd" in result:          # был платный HTTP 200
        doc.parse_cost_usd += result["parse_cost_usd"]
        doc.parse_count += 1

    if result.get("error"):
        ...  # существующая ветка (db.commit() ниже персистит накопление)
```

`deskew_reparse_document` идёт через `_reparse_from_s3` — покрывается автоматически, отдельной правки не требует.

- [ ] **Step 4: Накопление в `upload_pdf`**

Сразу после `result = await parse_invoice_pdf(file_bytes, db, doc.id)` и **до** `if result.get("error"):`:

```python
    result = await parse_invoice_pdf(file_bytes, db, doc.id)

    if "parse_cost_usd" in result:          # был платный HTTP 200
        doc.parse_cost_usd += result["parse_cost_usd"]
        doc.parse_count += 1

    if result.get("error"):
        ...  # существующая ветка
```

Отдельный `db.commit()` не нужен — обе ветки (`error` и `parsed`) уже коммитят там, где ставят `doc.status`.

- [ ] **Step 5: Запустить backend-тесты — регрессия**

Run: `just test-backend`
Expected: PASS (интеграционные тесты стоимости добавим в Task 4; здесь проверяем, что существующие не сломаны).

- [ ] **Step 6: Commit**

```bash
git add backend/routers/invoices.py
git commit -m "feat(be): накопление parse_cost_usd в роутере + сериализация"
```

---

### Task 4: Фикстуры и интеграционные тесты стоимости

**Files:**
- Modify: `backend/tests/fixtures/openrouter/happy_path.json`, `incomplete_totals.json`, `invalid_json.json`, `truncated_length.json`, `unparseable.json`
- Create: `backend/tests/fixtures/openrouter/happy_path_no_cost.json`
- Test: `backend/tests/integration/test_invoices.py`

**Interfaces:**
- Consumes: всё из Task 1-3; сценарии мока через `mock_openrouter.use_scenario(...)`.

- [ ] **Step 1: Добавить `usage.cost` во все существующие фикстуры**

В каждом из пяти файлов найти объект `"usage": {...}` и добавить в него `"cost"`. Для `happy_path.json`:

```json
  "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300, "cost": 0.0021}
```

Для остальных четырёх — добавить `"cost": 0.0021` в существующий `usage`-объект каждого файла (блок `usage` присутствует во всех пяти фикстурах; значение одинаковое, важно лишь `> 0`).

- [ ] **Step 2: Создать фикстуру без cost**

Создать `backend/tests/fixtures/openrouter/happy_path_no_cost.json` — точная копия `happy_path.json`, но в `usage` **без** ключа `cost`:

```json
{
  "id": "gen-test-nocost",
  "model": "anthropic/claude-sonnet-5",
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "{\"doc_type\": \"invoice\", \"invoices\": [{\"number\": \"СФ-101\", \"date\": \"2026-04-15\", \"supplier_name\": \"ООО Поставщик\", \"supplier_inn\": \"0000000000\", \"vat_rate\": 20, \"doc_total_without_vat\": 56000.0, \"doc_total_with_vat\": 67200.0, \"confidence\": 0.95, \"confidence_reason\": \"все поля читаются чётко\", \"items\": [{\"raw_name\": \"Бетонная смесь БСТ В25\", \"item_type\": \"material\", \"material_class\": \"В25\", \"material_type\": \"concrete\", \"quantity\": 7.0, \"unit\": \"м3\", \"unit_price\": 8000.0, \"amount\": 56000.0, \"vat_amount\": 9333.33, \"confidence\": 0.95}]}]}"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300}
}
```

- [ ] **Step 3: Написать падающие интеграционные тесты**

В `backend/tests/integration/test_invoices.py` добавить. Файл/проект передаём так же, как соседние upload-тесты: multipart-файл из фикстуры `sample_pdf_bytes` (conftest) + `project_id` в `data`. Содержимое PDF не важно — `mock_openrouter` перехватывает httpx.

```python
def test_upload_records_parse_cost(client, mock_openrouter, factories, sample_pdf_bytes):
    """Успешный разбор записывает стоимость и счётчик разборов на документ."""
    project = factories.ProjectFactory.create()
    resp = client.post(
        "/api/invoices/upload",
        data={"project_id": project.id},
        files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["parse_cost_usd"] > 0
    assert body["parse_count"] == 1


def test_reparse_accumulates_parse_cost(client, mock_openrouter, factories, sample_pdf_bytes):
    """Повторный разбор суммирует стоимость, а не перезаписывает."""
    project = factories.ProjectFactory.create()
    up = client.post(
        "/api/invoices/upload",
        data={"project_id": project.id},
        files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
    )
    doc_id = up.json()["id"]
    first_cost = up.json()["parse_cost_usd"]

    re = client.post(f"/api/invoices/documents/{doc_id}/reparse")
    assert re.status_code == 200
    assert re.json()["parse_count"] == 2
    assert re.json()["parse_cost_usd"] > first_cost


def test_failed_parse_is_still_billed(client, mock_openrouter, factories, sample_pdf_bytes):
    """КЛЮЧЕВОЙ ИНВАРИАНТ: провал сверки итогов — платный, стоимость учтена."""
    mock_openrouter.use_scenario("incomplete_totals")
    project = factories.ProjectFactory.create()
    resp = client.post(
        "/api/invoices/upload",
        data={"project_id": project.id},
        files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert body["parse_cost_usd"] > 0
    assert body["parse_count"] == 1


def test_missing_cost_defaults_zero_but_counts(client, mock_openrouter, factories, sample_pdf_bytes):
    """usage.cost отсутствует → стоимость 0, но вызов был — parse_count растёт."""
    mock_openrouter.use_scenario("happy_path_no_cost")
    project = factories.ProjectFactory.create()
    resp = client.post(
        "/api/invoices/upload",
        data={"project_id": project.id},
        files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["parse_cost_usd"] == 0
    assert body["parse_count"] == 1
```

- [ ] **Step 4: Запустить — убедиться, что падают, затем что проходят**

Run: `just test-backend-integration`
Expected сначала: FAIL (если запускать до Task 1-3 — но они уже сделаны, поэтому тесты должны сразу пройти на корректной реализации). Если какой-то падает — чинить реализацию, не тест.
Expected после: PASS все четыре.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/fixtures/openrouter backend/tests/integration/test_invoices.py
git commit -m "test(be): usage.cost в фикстурах + интеграция биллинга (вкл. провал)"
```

---

### Task 5: Фронтенд — тип, формат-хелпер, отображение

**Files:**
- Modify: `frontend/src/types/invoice.ts` (`DocumentSummary` ~70-80)
- Modify: `frontend/src/lib/format.ts`
- Modify: `frontend/src/pages/Review.tsx` (шапка документа ~181-182, импорт из `@/lib/format`)
- Test: `frontend/src/lib/format.test.ts` (создать, если нет)
- Возможно Modify: `frontend/src/test/fixtures.ts` (если там конструируются `DocumentSummary`/`DocumentDetail` — strict TS потребует новые поля)

**Interfaces:**
- Consumes: `parse_cost_usd`, `parse_count` из сериализации (Task 3).
- Produces: `formatUsd(value: number | null | undefined): string`.

- [ ] **Step 1: Написать падающий тест `formatUsd`**

Создать (или дополнить) `frontend/src/lib/format.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { formatUsd } from "./format";

describe("formatUsd", () => {
  it("нулевую стоимость показывает как $0.00", () => {
    expect(formatUsd(0)).toBe("$0.00");
  });
  it("суммы < 1¢ — до 4 знаков без хвостовых нулей", () => {
    expect(formatUsd(0.002)).toBe("$0.002");
    expect(formatUsd(0.0021)).toBe("$0.0021");
  });
  it("суммы < $0.0001 — герметичный guard", () => {
    expect(formatUsd(0.00001)).toBe("<$0.0001");
  });
  it("суммы >= 1¢ — два знака", () => {
    expect(formatUsd(0.06)).toBe("$0.06");
    expect(formatUsd(1.5)).toBe("$1.50");
  });
  it("null/undefined/NaN → тире", () => {
    expect(formatUsd(null)).toBe("—");
    expect(formatUsd(undefined)).toBe("—");
    expect(formatUsd(NaN)).toBe("—");
  });
});
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `just test-frontend` (или `cd frontend && npx vitest run src/lib/format.test.ts`)
Expected: FAIL — `formatUsd is not exported`.

- [ ] **Step 3: Реализовать `formatUsd`**

В `frontend/src/lib/format.ts` добавить:

```ts
/** Инфра-затраты OpenRouter в USD. Мелкие суммы (< 1¢) показываем до 4 знаков
 *  без хвостовых нулей, чтобы дешёвые разборы (~$0.002) не сливались в «$0.00». */
export function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (value === 0) return "$0.00";
  // Guard: суммы < $0.0001 округлились бы toFixed(4) до "0.0000" → трим дал бы "$0".
  // На практике OpenRouter такого не возвращает, но держим формат герметичным.
  if (value < 0.0001) return "<$0.0001";
  if (value < 0.01) {
    const trimmed = value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
    return `$${trimmed}`;
  }
  return `$${value.toFixed(2)}`;
}
```

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `just test-frontend`
Expected: PASS.

- [ ] **Step 5: Добавить поля в тип `DocumentSummary`**

В `frontend/src/types/invoice.ts`, в интерфейс `DocumentSummary`, после `ai_confidence: number | null;`:

```ts
  /** Накопленная стоимость ИИ-разбора, USD (OpenRouter usage.cost). */
  parse_cost_usd: number;
  /** Число платных вызовов OpenRouter по документу. */
  parse_count: number;
```

`DocumentDetail extends DocumentSummary` — поля наследуются автоматически.

- [ ] **Step 6: Показать стоимость в шапке документа (Review)**

В `frontend/src/pages/Review.tsx`:

**(a)** В импорт из `@/lib/format` добавить `formatUsd` (там уже импортируется `formatDate`).

**(b)** В блоке шапки (`<div className="flex items-center gap-3 text-xs text-fg-tertiary">`, рядом с `<span>{doc.filename}</span>`) добавить сразу после имени файла:

```tsx
            <span>{doc.filename}</span>
            {doc.parse_cost_usd > 0 && (
              <span title={doc.parse_count > 1 ? `${doc.parse_count} разбора` : "ИИ-разбор"}>
                ИИ-разбор: {formatUsd(doc.parse_cost_usd)}
                {doc.parse_count > 1 ? ` · ${doc.parse_count}×` : ""}
              </span>
            )}
```

- [ ] **Step 7: Починить типы в тест-фикстурах (если ломаются)**

Run: `just typecheck-frontend`
Если TS ругается на отсутствие `parse_cost_usd`/`parse_count` в объектах `DocumentSummary`/`DocumentDetail` внутри `frontend/src/test/fixtures.ts` (или в тестах страниц) — добавить в эти объекты `parse_cost_usd: 0, parse_count: 1` (или осмысленные значения по контексту теста).
Expected после правок: `tsc` без ошибок.

- [ ] **Step 8: Запустить фронт-тесты и типчек**

Run: `just test-frontend` и `just typecheck-frontend`
Expected: PASS, без TS-ошибок.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/types/invoice.ts frontend/src/lib/format.ts frontend/src/lib/format.test.ts frontend/src/pages/Review.tsx frontend/src/test/fixtures.ts
git commit -m "feat(fe): стоимость ИИ-разбора в шапке документа + formatUsd"
```

---

### Task 6: Финальная проверка и верификация реальной стоимости

**Files:** нет правок (верификация)

- [ ] **Step 1: Полный прогон линтера и тестов**

Run: `just lint` и `just test`
Expected: всё зелёное.

- [ ] **Step 2: Верифицировать, что `usage.cost` включает OCR-плагин (ручная, один раз)**

Запустить бэкенд (`just dev-backend`, предварительно полностью перезапустив — `.env` подхватывается только при старте) и загрузить один реальный УПД. В `backend/logs/app.log` найти строку `Стоимость вызова OpenRouter: $X` и сверить значение с дашбордом OpenRouter (generation cost этого вызова). Убедиться, что цифра включает стоимость `mistral-ocr`/`native`-плагина, а не только токены LLM. Если расходится — зафиксировать в спеке и решить, нужен ли отдельный учёт плагина (вне рамок этого плана).

- [ ] **Step 3: Commit (если были правки после верификации)**

```bash
git add -A && git commit -m "chore: верификация трекинга стоимости разбора"
```

---

## Self-Review

**Spec coverage:**
- Схема (`parse_cost_usd`, `parse_count`, Numeric(10,6), NOT NULL, server_default) → Task 1. ✓
- `usage:{include:true}` + захват cost + структурный `_with_cost` на всех платных ветках → Task 2. ✓
- Накопление до ветвления `if error` в обоих роутерах + deskew через `_reparse_from_s3` → Task 3. ✓
- Сериализация в `_serialize_document` и `list_documents` → Task 3. ✓
- `usage.cost` во все фикстуры + `happy_path_no_cost` + тест биллинга провала → Task 4. ✓
- Формат `formatUsd` (0 → $0.00, <$0.0001 → «<$0.0001» guard, <1¢ → 4 знака без нулей, >=1¢ → 2 знака), тип, отображение → Task 5. ✓
- Верификация OCR-стоимости против дашборда → Task 6 Step 2. ✓
- Небиллимый случай (200 с нераспарсиваемым телом) — сознательно не покрываем, зафиксировано в спеке. ✓

**Placeholder scan:** нет TBD/«add error handling»/«similar to Task N» — код полный в каждом шаге. Единственная условная инструкция (Task 5 Step 7 — «добавить поля в тест-фикстуры при TS-ошибке») оправдана: набор объектов, которые strict-TS потребует дополнить, зависит от кода, который реализатор видит перед собой.

**Type consistency:** `_with_cost(result, cost)` — сигнатура одна в Task 2 и потреблении Task 3. `parse_cost_usd`/`parse_count` — имена идентичны в модели (Task 1), сериализации (Task 3), TS-типе (Task 5). `formatUsd` — одно имя в Task 5. ✓
