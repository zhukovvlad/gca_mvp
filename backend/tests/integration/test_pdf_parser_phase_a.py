"""Тесты чистой фазы A парсинга (S0-2). Без БД — только LLM + структуры."""
from decimal import Decimal

import pytest

from pdf_parser import ParseOutcome, parse_pdf
from processing import PermanentError, TransientError


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_parse_pdf_unparseable_raises_permanent_with_cost(sample_pdf_bytes, mock_openrouter):
    """doc_type != invoice → PermanentError, но платный вызов учтён в ошибке."""
    mock_openrouter.use_scenario("unparseable")
    with pytest.raises(PermanentError) as exc:
        await parse_pdf(sample_pdf_bytes, document_id=1)
    assert exc.value.paid_calls == 1
    assert exc.value.cost_usd >= 0


@pytest.mark.asyncio
async def test_parse_pdf_incomplete_totals_raises_permanent(sample_pdf_bytes, mock_openrouter):
    """Провал сверки итогов → PermanentError с учётом стоимости."""
    mock_openrouter.use_scenario("incomplete_totals")
    with pytest.raises(PermanentError) as exc:
        await parse_pdf(sample_pdf_bytes, document_id=1)
    assert exc.value.paid_calls == 1


@pytest.mark.asyncio
async def test_parse_pdf_5xx_raises_transient_no_cost(sample_pdf_bytes, mock_openrouter):
    """OpenRouter 5xx → TransientError без стоимости (нет платного 200)."""
    mock_openrouter.use_http_status(503)
    with pytest.raises(TransientError) as exc:
        await parse_pdf(sample_pdf_bytes, document_id=1)
    assert exc.value.paid_calls == 0
    assert exc.value.cost_usd == Decimal(0)


@pytest.mark.asyncio
async def test_parse_pdf_429_raises_transient(sample_pdf_bytes, mock_openrouter):
    """OpenRouter 429 (rate limit) → TransientError, не Permanent (F12, ретраебельно на S2)."""
    mock_openrouter.use_http_status(429)
    with pytest.raises(TransientError):
        await parse_pdf(sample_pdf_bytes, document_id=1)


@pytest.mark.asyncio
async def test_parse_pdf_wrong_shape_content_raises_permanent_with_cost(sample_pdf_bytes, mock_openrouter):
    """HTTP 200 + валидный JSON, но НЕВЕРНОЙ формы (top-level массив вместо объекта) —
    `json.loads` проходит, но `parsed.get(...)` бросил бы AttributeError. Такое
    неклассифицированное исключение обязано стать PermanentError с учтённой
    стоимостью оплаченного вызова (post-200 catch-all), а не улететь наверх
    неклассифицированным и обнулить cost/paid_calls в process_document (регрессия
    инварианта «HTTP 200 → стоимость учтена»)."""
    mock_openrouter.use_scenario("wrong_shape")
    with pytest.raises(PermanentError) as exc:
        await parse_pdf(sample_pdf_bytes, document_id=1)
    assert exc.value.paid_calls == 1
    assert exc.value.cost_usd >= 0


@pytest.mark.asyncio
async def test_parse_pdf_second_invoice_bad_date_raises_permanent_all_or_nothing(
    sample_pdf_bytes, mock_openrouter,
):
    """FIX 1: две СФ в одном документе, у первой дата валидна, у второй — нет (null).

    Раньше некорректная дата второй СФ молча `continue`-ила — phase A вернула бы
    НЕПОЛНЫЙ набор (только первая СФ), а phase B заменила бы им старые данные
    (нарушение all-or-nothing parse-then-swap). Теперь любая СФ с плохой датой
    роняет ВЕСЬ разбор документа — PermanentError с учтённым платным вызовом."""
    mock_openrouter.use_scenario("two_invoices_second_bad_date")
    with pytest.raises(PermanentError) as exc:
        await parse_pdf(sample_pdf_bytes, document_id=1)
    assert exc.value.paid_calls == 1
    assert exc.value.cost_usd == Decimal("0.0025")
    assert "СФ-102" in exc.value.message


@pytest.mark.asyncio
async def test_parse_pdf_top_level_array_envelope_raises_permanent_with_cost(
    sample_pdf_bytes, mock_openrouter,
):
    """FIX 3: HTTP 200, но ТЕЛО ОТВЕТА (envelope) — top-level JSON-массив `[]` вместо
    объекта, а не контент модели. `response.json()` возвращает список → `data.get(...)`
    бросил бы AttributeError. Обязан стать PermanentError с учтённым платным вызовом
    (post-200 unified guard), а не голым исключением."""
    mock_openrouter.use_raw_body(b"[]")
    with pytest.raises(PermanentError) as exc:
        await parse_pdf(sample_pdf_bytes, document_id=1)
    assert exc.value.paid_calls == 1


@pytest.mark.asyncio
async def test_parse_pdf_corrupted_usage_array_raises_permanent_with_cost(
    sample_pdf_bytes, mock_openrouter,
):
    """FIX 3: HTTP 200, envelope — валидный объект, но `usage` — список вместо словаря
    (`{"usage": []}`). `(data.get("usage") or {}).get("cost")` бросил бы AttributeError
    на списке. Обязан стать PermanentError с учтённым платным вызовом (post-200 unified
    guard), а не голым исключением."""
    mock_openrouter.use_raw_body(
        b'{"choices": [{"message": {"content": "{}"}}], "usage": []}'
    )
    with pytest.raises(PermanentError) as exc:
        await parse_pdf(sample_pdf_bytes, document_id=1)
    assert exc.value.paid_calls == 1


@pytest.mark.asyncio
async def test_parse_pdf_non_numeric_usage_cost_raises_permanent_with_cost(
    sample_pdf_bytes, mock_openrouter,
):
    """FIX 3: `usage.cost` — нечисловая строка. `Decimal(str(...))` бросил бы
    `decimal.InvalidOperation`. Обязан стать PermanentError с учтённым платным вызовом
    (post-200 unified guard), а не голым исключением."""
    mock_openrouter.use_raw_body(
        b'{"choices": [{"message": {"content": "{}"}}], "usage": {"cost": "not-a-number"}}'
    )
    with pytest.raises(PermanentError) as exc:
        await parse_pdf(sample_pdf_bytes, document_id=1)
    assert exc.value.paid_calls == 1


@pytest.mark.asyncio
async def test_parse_pdf_nan_usage_cost_clamped_to_zero(sample_pdf_bytes, mock_openrouter):
    """FIX B: `usage.cost = "NaN"` парсится Decimal-ом без ошибки (в отличие от нечисловой
    строки), но неклэмпленный NaN испортил бы накопленный parse_cost_usd. Разбор в остальном
    успешен, cost_usd клэмпится в 0."""
    mock_openrouter.use_scenario("happy_path_nan_cost")
    outcome = await parse_pdf(sample_pdf_bytes, document_id=1)
    assert outcome.doc_type == "invoice"
    assert len(outcome.invoices) == 1
    assert outcome.cost_usd == Decimal(0)
    assert outcome.paid_calls == 1
