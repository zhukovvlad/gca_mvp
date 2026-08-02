# Parse Completeness Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the PDF parser from silently saving incomplete invoices when the LLM response is truncated or drops table rows, by reconciling extracted line-item totals against the document's printed "Всего к оплате" and by honoring the API `finish_reason`.

**Architecture:** Three layers. (1) Capacity: raise `AI_MAX_TOKENS` so long invoices fit in one completion. (2) Truncation detection: read `finish_reason` from the OpenRouter response — `"length"` means the answer was cut off. (3) Totals reconciliation: ask the model to extract the printed document total (графа 9 «Всего к оплате»), then compare it to `SUM(item.amount)`; a mismatch beyond rounding tolerance means rows were lost. Any of (2) or (3) firing turns the parse into an explicit error result (same contract as the existing invalid-JSON path) instead of a silent success.

**Tech Stack:** Python 3.12, FastAPI, pytest 8 + respx (parser API mocked via `mock_openrouter` fixture loading `tests/fixtures/openrouter/*.json`), pydantic-settings.

---

## Background facts (verified against codebase)

- `parse_invoice_pdf(file_data, db, document_id)` lives in [pdf_parser.py:115](../../../backend/pdf_parser.py#L115). It posts to OpenRouter, strips a markdown fence, `json.loads` the content, then loops invoices → items → `create_invoice(...)`.
- The **only** current truncation guard is [pdf_parser.py:195](../../../backend/pdf_parser.py#L195): `if completion_tokens and completion_tokens >= max_tokens`. It does **not** fire when the model closes valid JSON early (the real bug: 60 of 66 rows saved under `confidence=0.96`).
- `item["amount"]` is графа 5 «Стоимость без налога» per line. The document's «Всего к оплате» без НДС is the plain sum of графа 5 across **all** rows (material + delivery + exclude). So the reconciliation is a plain sum vs. a plain printed total — no VAT/delivery proration involved (that logic is only for `avg_price`, not for completeness).
- Pure parser helpers are unit-tested in [tests/unit/test_pdf_parser_helpers.py](../../../backend/tests/unit/test_pdf_parser_helpers.py) (no DB, no HTTP). New pure helpers get tested there.
- End-to-end parser behavior is tested via the `mock_openrouter` fixture ([tests/conftest.py:238](../../../backend/tests/conftest.py#L238)): `mock_openrouter.use_scenario("<name>")` loads `tests/fixtures/openrouter/<name>.json` and returns it as the API response (HTTP 200). Fixture shape: top-level `choices[0].message.content` is a **JSON string**; `usage` holds token counts. `finish_reason` is currently absent from fixtures.
- `AI_MAX_TOKENS` default is `32768` in [config.py:41](../../../backend/config.py#L41). The comment says claude-sonnet-4.6 supports up to 64K output.
- Existing fixtures: `happy_path.json`, `invalid_json.json`, `unparseable.json` in `tests/fixtures/openrouter/`.

---

## File Structure

- **Modify** `backend/config.py` — raise `AI_MAX_TOKENS` default.
- **Modify** `backend/.env.example` — keep the documented value in sync (if it lists `AI_MAX_TOKENS`).
- **Modify** `backend/pdf_parser.py` — add `_reconcile_totals()` pure helper; add `finish_reason` read; add totals fields to `SYSTEM_PROMPT`; wire both checks into `parse_invoice_pdf`.
- **Modify** `backend/tests/unit/test_pdf_parser_helpers.py` — unit tests for `_reconcile_totals`.
- **Modify** `backend/tests/fixtures/openrouter/happy_path.json` — add the new total fields so it still reconciles.
- **Create** `backend/tests/fixtures/openrouter/truncated_length.json` — response with `finish_reason: "length"`.
- **Create** `backend/tests/fixtures/openrouter/incomplete_totals.json` — valid JSON whose item sum ≠ declared document total (the 60/66 scenario in miniature).
- **Modify** `backend/tests/integration/test_invoices.py` — end-to-end tests that truncated / incomplete parses produce an error (no invoices saved).

---

## Task 1: Raise the token ceiling (#4)

**Files:**
- Modify: `backend/config.py:41`
- Modify: `backend/.env.example` (only if it contains an `AI_MAX_TOKENS` line)

- [ ] **Step 1: Read `.env.example` to see if it pins the value**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && grep -n AI_MAX_TOKENS backend/.env.example 2>&1 || echo NONE"`
Expected: either a line like `AI_MAX_TOKENS=32768` (note the line number) or `NONE`.

- [ ] **Step 2: Raise the default in config.py**

In `backend/config.py`, replace line 41:

```python
    AI_MAX_TOKENS: int = 32768  # claude-sonnet-4.6 поддерживает до 64K; 8192 режет большие СФ
```

with:

```python
    AI_MAX_TOKENS: int = 64000  # верхний предел вывода claude-sonnet-4.6 (~64K); prompt от mistral-ocr на 8-страничных СФ съедает ~24K, оставляя на ответ всё что есть
```

- [ ] **Step 3: Sync `.env.example` if needed**

If Step 1 found an `AI_MAX_TOKENS=...` line, edit it to `AI_MAX_TOKENS=64000`. If Step 1 printed `NONE`, skip this step.

- [ ] **Step 4: Verify config still loads**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -c 'from config import settings; print(settings.AI_MAX_TOKENS)' 2>&1"`
Expected: `64000`

- [ ] **Step 5: Commit**

```bash
git add backend/config.py backend/.env.example
git commit -m "fix(parser): raise AI_MAX_TOKENS to 64000 so long invoices fit in one completion"
```

---

## Task 2: `_reconcile_totals` pure helper (#1, logic only)

**Files:**
- Modify: `backend/pdf_parser.py` (add helper near `_calculate_completeness`, ~line 322)
- Test: `backend/tests/unit/test_pdf_parser_helpers.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_pdf_parser_helpers.py`:

```python
from pdf_parser import _reconcile_totals


class TestReconcileTotals:
    def test_matching_totals_ok(self):
        items = [{"amount": 56000.0}, {"amount": 7500.0}]
        ok, detail = _reconcile_totals(63500.0, items)
        assert ok is True
        assert detail == ""

    def test_rounding_noise_within_tolerance_ok(self):
        # 66 строк с покопеечным округлением: сумма расходится с печатным итогом на рубли
        items = [{"amount": 73300.0} for _ in range(33)] + [{"amount": 7500.0} for _ in range(33)]
        # фактическая сумма = 2 666 400; печатный итог на 50 ₽ больше (накопленное округление)
        ok, detail = _reconcile_totals(2_666_450.0, items)
        assert ok is True

    def test_missing_rows_flags_incomplete(self):
        # 60 из 66 строк: сумма сильно меньше печатного итога
        items = [{"amount": 73300.0} for _ in range(30)] + [{"amount": 7500.0} for _ in range(30)]
        # сумма = 2 424 000; печатный итог 2 472 124.99 → расхождение ~48k
        ok, detail = _reconcile_totals(2_472_124.99, items)
        assert ok is False
        assert "Всего к оплате" in detail

    def test_absent_doc_total_flags_incomplete(self):
        ok, detail = _reconcile_totals(None, [{"amount": 100.0}])
        assert ok is False
        assert detail

    def test_zero_doc_total_flags_incomplete(self):
        ok, detail = _reconcile_totals(0.0, [{"amount": 100.0}])
        assert ok is False

    def test_empty_items_with_positive_total_flags(self):
        ok, detail = _reconcile_totals(1000.0, [])
        assert ok is False

    def test_item_missing_amount_treated_as_zero(self):
        # позиция без amount не должна ломать суммирование
        ok, detail = _reconcile_totals(100.0, [{"amount": 100.0}, {"raw_name": "x"}])
        assert ok is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-unit 2>&1"`
Expected: FAIL — `ImportError: cannot import name '_reconcile_totals'`.

- [ ] **Step 3: Implement the helper**

In `backend/pdf_parser.py`, add directly above `def _calculate_completeness` (currently line 323):

```python
def _reconcile_totals(
    doc_total_without_vat: float | None,
    items: list[dict],
    rel_tol: float = 0.001,
    abs_tol: float = 1.0,
) -> tuple[bool, str]:
    """Сверяет сумму графы-5 (amount) извлечённых позиций с печатным итогом
    «Всего к оплате» (без НДС) из документа.

    Возвращает (True, "") если суммы сходятся в пределах допуска (накопленное
    покопеечное округление по строкам). Возвращает (False, причина) если итог
    не извлечён (модель не дошла до конца таблицы) или расходится сильнее допуска
    (потеряны строки). Это детектор НЕПОЛНОГО разбора, не проверка арифметики НДС.
    """
    if not doc_total_without_vat or doc_total_without_vat <= 0:
        return False, "В документе не извлечён итог «Всего к оплате» — разбор, вероятно, неполный (не дошёл до конца таблицы)"

    items_sum = sum(float(item.get("amount") or 0) for item in items)
    diff = abs(items_sum - doc_total_without_vat)
    tolerance = max(abs_tol, doc_total_without_vat * rel_tol)

    if diff > tolerance:
        return False, (
            f"Сумма позиций ({items_sum:.2f}) не сходится с «Всего к оплате» без НДС "
            f"({doc_total_without_vat:.2f}), расхождение {diff:.2f} ₽ > допуска {tolerance:.2f} ₽ — "
            f"часть строк таблицы, вероятно, не распознана"
        )
    return True, ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-unit 2>&1"`
Expected: PASS, including all `TestReconcileTotals` cases.

- [ ] **Step 5: Commit**

```bash
git add backend/pdf_parser.py backend/tests/unit/test_pdf_parser_helpers.py
git commit -m "feat(parser): add _reconcile_totals helper to detect dropped invoice rows"
```

---

## Task 3: Ask the model for the document total (#1, prompt)

**Files:**
- Modify: `backend/pdf_parser.py` (SYSTEM_PROMPT, lines 24–106)
- Modify: `backend/tests/fixtures/openrouter/happy_path.json`

- [ ] **Step 1: Add the total fields to the JSON schema in SYSTEM_PROMPT**

In `backend/pdf_parser.py`, inside the schema block, add two fields to the invoice object. Change the lines (currently 32–35):

```python
        "supplier_inn": "ИНН или null",
        "vat_rate": 20,
        "confidence": 0.92,
        "confidence_reason": "почему именно такая уверенность (1-2 коротких предложения)",
```

to:

```python
        "supplier_inn": "ИНН или null",
        "vat_rate": 20,
        "doc_total_without_vat": 2472124.99,
        "doc_total_with_vat": 2966550.00,
        "confidence": 0.92,
        "confidence_reason": "почему именно такая уверенность (1-2 коротких предложения)",
```

- [ ] **Step 2: Add an extraction rule for the printed total**

In `backend/pdf_parser.py`, in the `Правила:` section, add a bullet immediately after the `quantity, unit_price, amount` rule (currently line 70):

```python
- doc_total_without_vat / doc_total_with_vat — итоговые суммы из строки «Всего к оплате (9)» документа: графа «без налога — всего» и графа «с налогом — всего» соответственно. Бери их КАК НАПЕЧАТАНО в строке итога, не пересчитывай. Если строки «Всего к оплате» в документе нет — верни null. Эти числа используются для проверки, что все строки таблицы распознаны, поэтому извлекай их обязательно, если они есть.
```

- [ ] **Step 3: Update happy_path fixture so it reconciles**

In `backend/tests/fixtures/openrouter/happy_path.json`, the `content` string holds one invoice with a single item `amount: 56000.0`. Add the matching total. Replace the `\"vat_rate\": 20, \"confidence\": 0.95,` substring inside `content` with:

```
\"vat_rate\": 20, \"doc_total_without_vat\": 56000.0, \"doc_total_with_vat\": 67200.0, \"confidence\": 0.95,
```

The full `content` value becomes (single line, escaped):

```
"{\"doc_type\": \"invoice\", \"invoices\": [{\"number\": \"СФ-101\", \"date\": \"2026-04-15\", \"supplier_name\": \"ООО Поставщик\", \"supplier_inn\": \"0000000000\", \"vat_rate\": 20, \"doc_total_without_vat\": 56000.0, \"doc_total_with_vat\": 67200.0, \"confidence\": 0.95, \"confidence_reason\": \"все поля читаются чётко\", \"items\": [{\"raw_name\": \"Бетонная смесь БСТ В25\", \"item_type\": \"material\", \"material_class\": \"В25\", \"material_type\": \"concrete\", \"quantity\": 7.0, \"unit\": \"м3\", \"unit_price\": 8000.0, \"amount\": 56000.0, \"vat_amount\": 9333.33, \"confidence\": 0.95}]}]}"
```

- [ ] **Step 4: Verify the fixture is still valid JSON**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && python -c 'import json; json.load(open(\"backend/tests/fixtures/openrouter/happy_path.json\", encoding=\"utf-8\"))' && echo OK 2>&1"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add backend/pdf_parser.py backend/tests/fixtures/openrouter/happy_path.json
git commit -m "feat(parser): extract printed «Всего к оплате» totals for completeness checks"
```

---

## Task 4: Wire reconciliation + finish_reason into parse_invoice_pdf (#1 + #2)

**Files:**
- Modify: `backend/pdf_parser.py:184-237` (response handling + per-invoice loop)

- [ ] **Step 1: Read the current response-handling block**

Read `backend/pdf_parser.py` lines 184–238 to confirm exact surrounding text before editing (the `data = response.json()` block and the start of the `for inv_idx, inv_data in enumerate(...)` loop).

- [ ] **Step 2: Capture finish_reason right after parsing the response JSON**

In `backend/pdf_parser.py`, find (currently lines 184–192):

```python
        data = response.json()
        usage = data.get("usage", {})
        completion_tokens = usage.get("completion_tokens", 0)
        logger.info(
            f"[doc={document_id}] OpenRouter ответ получен. "
            f"Токены: prompt={usage.get('prompt_tokens', '?')}, "
            f"completion={completion_tokens}, "
            f"total={usage.get('total_tokens', '?')}"
        )
```

Replace with:

```python
        data = response.json()
        usage = data.get("usage", {})
        completion_tokens = usage.get("completion_tokens", 0)
        finish_reason = (data.get("choices") or [{}])[0].get("finish_reason")
        logger.info(
            f"[doc={document_id}] OpenRouter ответ получен. "
            f"Токены: prompt={usage.get('prompt_tokens', '?')}, "
            f"completion={completion_tokens}, "
            f"total={usage.get('total_tokens', '?')}, "
            f"finish_reason={finish_reason}"
        )

        # finish_reason="length" → модель упёрлась в лимит токенов, ответ обрезан.
        # Это надёжнее, чем сравнение completion_tokens >= max_tokens ниже.
        if finish_reason == "length":
            logger.error(
                f"[doc={document_id}] Ответ ОБРЕЗАН по лимиту токенов (finish_reason=length). "
                f"Часть позиций потеряна. Увеличьте AI_MAX_TOKENS."
            )
            return {"error": "Ответ модели обрезан по лимиту токенов — часть позиций счёта потеряна. Попробуйте повторить разбор."}
```

- [ ] **Step 3: Add the totals reconciliation per invoice, after items are built**

In `backend/pdf_parser.py`, find the block where `items` is fully built and the date is parsed, immediately before `invoice = create_invoice(...)` (currently lines 289–298):

```python
            try:
                invoice_date_str = inv_data.get("date")
                if not invoice_date_str:
                    raise ValueError("Дата СФ отсутствует в ответе модели")
                invoice_date = date.fromisoformat(invoice_date_str)
            except (ValueError, TypeError) as e:
                logger.error(f"[doc={document_id}] СФ №{inv_number}: некорректная дата '{inv_data.get('date')}': {e}")
                continue

            invoice = create_invoice(
```

Insert the reconciliation check between the date `try/except` and `invoice = create_invoice(`:

```python
            try:
                invoice_date_str = inv_data.get("date")
                if not invoice_date_str:
                    raise ValueError("Дата СФ отсутствует в ответе модели")
                invoice_date = date.fromisoformat(invoice_date_str)
            except (ValueError, TypeError) as e:
                logger.error(f"[doc={document_id}] СФ №{inv_number}: некорректная дата '{inv_data.get('date')}': {e}")
                continue

            doc_total = inv_data.get("doc_total_without_vat")
            try:
                doc_total = float(doc_total) if doc_total is not None else None
            except (TypeError, ValueError):
                doc_total = None
            reconciled, reconcile_detail = _reconcile_totals(doc_total, items)
            if not reconciled:
                logger.error(
                    f"[doc={document_id}] СФ №{inv_number}: разбор НЕПОЛНЫЙ — {reconcile_detail}"
                )
                return {"error": f"Разбор счёта №{inv_number} неполный: {reconcile_detail}"}

            invoice = create_invoice(
```

- [ ] **Step 4: Run the full unit suite (no regressions in helpers)**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-unit 2>&1"`
Expected: PASS.

- [ ] **Step 5: Lint**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && cd backend && ruff check pdf_parser.py 2>&1"`
Expected: `All checks passed!` (or no errors). Fix any reported issues (e.g. line length 120).

- [ ] **Step 6: Commit**

```bash
git add backend/pdf_parser.py
git commit -m "feat(parser): reject truncated (finish_reason=length) and unreconciled parses instead of saving partial data"
```

---

## Task 5: Integration fixtures + tests for the rejection paths

**Files:**
- Create: `backend/tests/fixtures/openrouter/truncated_length.json`
- Create: `backend/tests/fixtures/openrouter/incomplete_totals.json`
- Modify: `backend/tests/integration/test_invoices.py`

- [ ] **Step 1: Read the existing reparse/upload integration test for shape**

Read `backend/tests/integration/test_invoices.py` around line 260 (the `happy_path` usage) and locate one test that uses `mock_openrouter` end-to-end (upload or reparse) plus how it asserts invoices were created. Match that test's setup (fixtures used, endpoint called, how it queries `Invoice` count) in the new tests below. Note the exact endpoint path and any project/document factory fixtures it relies on.

- [ ] **Step 2: Create the truncated fixture**

Create `backend/tests/fixtures/openrouter/truncated_length.json`:

```json
{
  "id": "gen-test-truncated",
  "model": "anthropic/claude-sonnet-4.6",
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "{\"doc_type\": \"invoice\", \"invoices\": [{\"number\": \"СФ-TRUNC\", \"date\": \"2026-04-15\", \"supplier_name\": \"ООО Поставщик\", \"supplier_inn\": \"0000000000\", \"vat_rate\": 20, \"doc_total_without_vat\": 56000.0, \"doc_total_with_vat\": 67200.0, \"confidence\": 0.95, \"confidence_reason\": \"обрыв\", \"items\": [{\"raw_name\": \"Бетон В25\", \"item_type\": \"material\", \"material_class\": \"В25\", \"material_type\": \"concrete\", \"quantity\": 7.0, \"unit\": \"м3\", \"unit_price\": 8000.0, \"amount\": 56000.0, \"vat_amount\": 9333.33, \"confidence\": 0.95}]}]}",
        "finish_reason": "length"
      },
      "finish_reason": "length"
    }
  ],
  "usage": {"prompt_tokens": 100, "completion_tokens": 64000, "total_tokens": 64100}
}
```

Note: `finish_reason` is placed on the choice object (where `parse_invoice_pdf` reads `choices[0].get("finish_reason")`). The content is intentionally still valid JSON — this proves the `finish_reason` check fires **before** and independently of JSON validity.

- [ ] **Step 3: Create the incomplete-totals fixture**

Create `backend/tests/fixtures/openrouter/incomplete_totals.json`. Valid JSON, no truncation, but the single item's `amount` (56000) does not match the declared `doc_total_without_vat` (242400 — as if 3 more lines were dropped):

```json
{
  "id": "gen-test-incomplete",
  "model": "anthropic/claude-sonnet-4.6",
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "{\"doc_type\": \"invoice\", \"invoices\": [{\"number\": \"СФ-INCOMPLETE\", \"date\": \"2026-04-15\", \"supplier_name\": \"ООО Поставщик\", \"supplier_inn\": \"0000000000\", \"vat_rate\": 20, \"doc_total_without_vat\": 242400.0, \"doc_total_with_vat\": 290880.0, \"confidence\": 0.96, \"confidence_reason\": \"всё хорошо\", \"items\": [{\"raw_name\": \"Бетон В25\", \"item_type\": \"material\", \"material_class\": \"В25\", \"material_type\": \"concrete\", \"quantity\": 7.0, \"unit\": \"м3\", \"unit_price\": 8000.0, \"amount\": 56000.0, \"vat_amount\": 9333.33, \"confidence\": 0.95}]}]}",
        "finish_reason": "stop"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300}
}
```

- [ ] **Step 4: Verify both fixtures are valid JSON**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && python -c 'import json,glob; [json.load(open(f,encoding=\"utf-8\")) for f in glob.glob(\"backend/tests/fixtures/openrouter/*.json\")]; print(\"OK\")' 2>&1"`
Expected: `OK`

- [ ] **Step 5: Write the failing integration tests**

Append to `backend/tests/integration/test_invoices.py`. **Adapt the setup** (factory fixtures, endpoint path, invoice-count query) to match the existing end-to-end parser test you read in Step 1 — the skeleton below shows intent; fill the project/document creation exactly as the neighboring tests do:

```python
def test_truncated_response_saves_no_invoices(client, db_session, mock_openrouter):
    """finish_reason=length → parse returns error, no Invoice rows created."""
    mock_openrouter.use_scenario("truncated_length")
    # ARRANGE: create a project + document with an s3_key, mock S3 download
    #          (copy the arrange block from the existing reparse/upload happy-path test).
    # ACT: call the same parse-triggering endpoint (upload or reparse) used there.
    # ASSERT:
    from models import Invoice
    assert db_session.query(Invoice).count() == 0


def test_incomplete_totals_saves_no_invoices(client, db_session, mock_openrouter):
    """Item sum ≠ printed «Всего к оплате» → parse returns error, no Invoice rows created."""
    mock_openrouter.use_scenario("incomplete_totals")
    # ARRANGE / ACT: same as above.
    # ASSERT:
    from models import Invoice
    assert db_session.query(Invoice).count() == 0


def test_happy_path_still_saves_invoice(client, db_session, mock_openrouter):
    """Reconciling response (happy_path) still creates exactly one invoice."""
    # default scenario is happy_path; do not call use_scenario.
    # ARRANGE / ACT: same trigger as above.
    from models import Invoice
    assert db_session.query(Invoice).count() == 1
```

If an equivalent happy-path "invoice created" assertion already exists in the file, do **not** duplicate it — keep only the two new rejection tests and rely on the existing happy-path test for the positive case.

- [ ] **Step 6: Run integration tests to verify rejection tests behave**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-integration 2>&1"`
Expected: the two new rejection tests PASS (0 invoices); happy-path test PASS (1 invoice). If integration DB is unavailable (`TEST_DATABASE_URL` unset), note it and run the full backend suite instead in Step 7.

- [ ] **Step 7: Run the whole backend suite**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend 2>&1"`
Expected: all PASS. In particular, no pre-existing parser test broke because of the new required `doc_total_without_vat` field (the `happy_path` fixture was updated in Task 3; any other fixture used by a "creates invoice" test must also carry a reconciling `doc_total_without_vat` — if such a test fails, add the field to that fixture the same way).

- [ ] **Step 8: Commit**

```bash
git add backend/tests/fixtures/openrouter/truncated_length.json backend/tests/fixtures/openrouter/incomplete_totals.json backend/tests/integration/test_invoices.py
git commit -m "test(parser): cover truncated and unreconciled parse rejection end-to-end"
```

---

## Task 6: Update documentation

**Files:**
- Modify: `CLAUDE.md` (the `pdf_parser.py` / parsing methodology area)
- Modify: `docs/TECH_DEBT.md` (note remaining layers not done)

- [ ] **Step 1: Document the completeness guard in CLAUDE.md**

Add a short paragraph to `CLAUDE.md` near the avg_price methodology section describing the new guard:

```markdown
**Parse completeness guard:** `pdf_parser.parse_invoice_pdf` rejects a parse (returns `{"error": ...}`, no rows saved) in two cases: (1) the API response `finish_reason == "length"` — the model hit the token ceiling and the answer is truncated; (2) `_reconcile_totals` finds `SUM(item.amount)` diverging from the model-extracted «Всего к оплате» без НДС (`doc_total_without_vat`) beyond `max(1 ₽, 0.1%)`. This prevents silently storing a partial invoice (e.g. 60 of 66 rows) under high confidence. `AI_MAX_TOKENS=64000` to fit long invoices in one completion. NOT yet done: per-page chunking for invoices that exceed even that, and prompt-size reduction — see TECH_DEBT.md.
```

- [ ] **Step 2: Note the deferred layers in TECH_DEBT.md**

Add to `docs/TECH_DEBT.md`:

```markdown
- **Parser chunking for very long invoices**: `_reconcile_totals` now *detects* dropped rows, but recovery for invoices too long for a single completion (100+ line items) requires per-page chunking + merge. Not implemented. Also: mistral-ocr prompt is ~24K tokens on an 8-page form (repeated page headers/footers) — reducing it would leave more room for the answer.
- **Reparse deletes before validating**: `routers/invoices.reparse_document` deletes existing invoices *before* the new parse runs. If the new parse is rejected by the completeness guard, the document ends with zero invoices (old good data already gone). Consider parse-then-swap.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/TECH_DEBT.md
git commit -m "docs: document parse completeness guard and remaining parser tech debt"
```

---

## Self-Review notes

- **Spec coverage:** #4 → Task 1. #1 (logic) → Task 2; #1 (prompt + golden number) → Task 3; #1 (wiring) → Task 4 Step 3. #2 (finish_reason) → Task 4 Step 2. Tests → Task 5. Docs → Task 6. All three "системный минимум" items covered.
- **Type/name consistency:** helper named `_reconcile_totals` throughout (distinct from existing `_calculate_completeness`); returns `tuple[bool, str]`; field `doc_total_without_vat` used identically in prompt schema (Task 3), fixtures (Tasks 3, 5), and the wiring read (Task 4 Step 3).
- **Known residual:** reparse-deletes-before-validate is intentionally left as documented tech debt, not fixed here (out of "minimum" scope, flagged to the user already).
