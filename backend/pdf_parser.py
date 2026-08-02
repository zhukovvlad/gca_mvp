import json
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import httpx

import llm
from config import resolved_llm_parse_max_tokens, settings
from processing import PermanentError, ProcessingError, TransientError

logger = logging.getLogger(__name__)


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

SYSTEM_PROMPT = """Ты — парсер счетов-фактур и УПД (универсальных передаточных документов) для строительных материалов.

ШАГ 1: Определи тип документа.
- Если это счёт-фактура (СФ) или УПД — doc_type = "invoice"
- Если это что-то другое (акт, накладная, письмо, мусор) — doc_type = "unknown"

ШАГ 2: Если doc_type = "invoice", извлеки данные. В одном PDF может быть НЕСКОЛЬКО счетов-фактур.

Верни ТОЛЬКО валидный JSON (без markdown-обёртки):

{
  "doc_type": "invoice",
  "invoices": [
    {
      "number": "номер СФ",
      "date": "YYYY-MM-DD",
      "supplier_name": "название поставщика",
      "supplier_inn": "ИНН или null",
      "vat_rate": 20,
      "doc_total_without_vat": 2472124.99,
      "doc_total_with_vat": 2966550.00,
      "confidence": 0.92,
      "confidence_reason": "почему именно такая уверенность (1-2 коротких предложения)",
      "items": [
        {
          "raw_name": "полное наименование из документа как есть",
          "item_type": "material",
          "calc_role": "base",
          "material_class": "В40",
          "material_type": "concrete",
          "quantity": 7.0,
          "unit": "м3",
          "unit_price": 8500.00,
          "amount": 59500.00,
          "vat_amount": 9916.67,
          "confidence": 0.95
        }
      ]
    }
  ]
}

Если doc_type = "unknown":
{
  "doc_type": "unknown"
}

Правила:
- Дату в формат YYYY-MM-DD
- item_type: "material" — все материальные позиции: бетон, арматура, присадки, цементное молоко, проволока и др.; "delivery" — доставка, перевозка, автобетоносмеситель; "other" — только скидки, возвраты и корректировки
- calc_role: роль позиции в расчёте стоимости (только для item_type="material"):
  - "base" — первичный отслеживаемый материал: бетон (В15..В50), арматура (А500С, А240, А300), а также любой другой самостоятельный строительный материал (раствор, кирпич, металлопрокат и т.п.) с material_type="other"
  - "additive" — добавка, пропорционально входящая в стоимость основного материала (не самостоятельный товар, а улучшитель смеси): пластификатор, гидрофобизатор, противоморозная добавка, ускоритель твердения; на практике встречается только в бетонных СФ
  - "exclude" — сопутствующие позиции и услуги, которые не формируют стоимость материала и не распределяются пропорционально: цементное молоко, простой миксера, мойка миксера, проволока вязальная, разовые услуги и любые позиции, не являющиеся частью самого материала
  - null — для item_type="delivery" и item_type="other"
- material_class: для бетона и арматуры (calc_role="base") — ТОЛЬКО короткое обозначение класса: "В40", "В30", "А500С" — НЕ полная спецификация "В40 П4 F200 W12"; для добавок (calc_role="additive") — короткое торговое название: "Пластификатор", "Гидрофобизатор", "Противоморозная добавка"; для exclude — используй СТРОГО эти канонические имена: бетонная тематика→ "Цементное молоко", "Простой миксера", "Мойка миксера"; арматурная тематика→ "Проволока вязальная"; для доставки и прочего — null
- material_type: "concrete" для бетонной тематики (включая присадки и цементное молоко), "rebar" для арматурной тематики (включая проволоку), "other" для прочих материалов; null для доставки
- quantity, unit_price, amount — числа с плавающей точкой
- doc_total_without_vat / doc_total_with_vat — итоговые суммы из строки «Всего к оплате (9)» документа: графа «без налога — всего» и графа «с налогом — всего» соответственно. Бери их КАК НАПЕЧАТАНО в строке итога, не пересчитывай. Если строки «Всего к оплате» в документе нет — верни null. Эти числа используются для проверки, что все строки таблицы распознаны, поэтому извлекай их обязательно, если они есть.

КРИТИЧЕСКИ ВАЖНО про цены и суммы — БЕРИ ИХ ИЗ ДОКУМЕНТА КАК ЕСТЬ, НИЧЕГО НЕ ПЕРЕСЧИТЫВАЙ:
В стандартной форме счёта-фактуры РФ есть фиксированные графы. Сопоставление:
- quantity      ← графа 3 «Количество (объём)»
- unit          ← графа 2а «Условное обозначение (национальное)»
- unit_price    ← графа 4 «Цена (тариф) за единицу измерения» (БЕЗ НДС)
- amount        ← графа 5 «Стоимость товаров (работ, услуг) без налога — всего» (БЕЗ НДС)
- vat_amount    ← графа 8 «Сумма налога, предъявляемая покупателю»

ЗАПРЕЩЕНО:
- складывать amount + vat_amount и класть в amount — оставь amount без налога
- умножать unit_price на (1 + vat_rate/100) — отдай как напечатано
- использовать графу 9 «Стоимость с налогом — всего» вместо графы 5

Если документ нестандартный (УПД свободной формы, без явной графы «без налога») и колонки разделить не удаётся — бери ту цену, что напечатана в строке позиции, как есть, и в confidence_reason укажи, что цены могут быть с НДС.

САМОПРОВЕРКА (для повышения качества): после извлечения проверь, что amount ≈ quantity * unit_price (допуск ±2% на округления). Если сильно расходится — снижай confidence этой позиции и опиши причину в confidence_reason родителя.

КРИТИЧЕСКИ ВАЖНО про позиции (items):
- КАЖДАЯ СТРОКА из табличной части документа = ОТДЕЛЬНАЯ позиция в массиве items
- НИКОГДА не сливай две или более строк в одну позицию, даже если у них одинаковое наименование, цена или единица измерения
- НИКОГДА не суммируй количество, сумму или НДС из нескольких строк
- Если в документе 5 строк "Бетонная смесь БСТ В40" — в JSON должно быть РОВНО 5 объектов в items
- Если строки выглядят одинаково, но напечатаны отдельными строками таблицы — они остаются отдельными позициями
- Сохраняй порядок позиций тот же, что и в документе

ОБЯЗАТЕЛЬНАЯ САМОПРОВЕРКА ПОЛНОТЫ (выполни перед закрытием JSON):
1. Найди строку «Всего к оплате (9)» в документе и прочитай значение графы «без налога — всего» — это doc_total_without_vat.
2. Посчитай SUM(amount) по всем позициям в items.
3. Если SUM(amount) ≠ doc_total_without_vat (расхождение больше нескольких рублей на округление) — ты пропустил строки. Вернись к табличной части документа и добавь недостающие позиции.
4. Повторяй шаги 2-3 до полного совпадения сумм.
Эта проверка обязательна. Не закрывай JSON, пока суммы не сошлись.

ПРО CONFIDENCE (уверенность):
- confidence на уровне СФ — твоя честная оценка от 0.0 до 1.0, насколько ты уверен в правильности всех извлечённых полей этой СФ. Учитывай: качество скана, читаемость табличной части, соответствие сумм, наличие всех ключевых полей, потенциальные ошибки OCR
- confidence на уровне item — уверенность именно в этой позиции (особенно в цифрах: цена, количество, сумма)
- 0.95–1.0 — текст идеально читаем, все поля извлечены, итоговые суммы сходятся
- 0.80–0.94 — мелкие сомнения (нечёткий символ в номере, спорный класс материала, частичное соответствие сумм)
- 0.60–0.79 — заметные пробелы (часть полей пришлось угадывать, плохой скан, неоднозначные значения)
- ниже 0.60 — серьёзные сомнения, данные могут быть неверны
- НЕ завышай confidence ради красивого числа. Если значение угадано или восстановлено по контексту — снижай уверенность
- confidence_reason — кратко и по делу укажи, что именно вызывает неуверенность, либо "все поля читаются чётко"
"""


async def parse_pdf(file_data: bytes, *, document_id: int) -> ParseOutcome:
    """Чистая фаза A: вызвать OpenRouter, разобрать ответ, вернуть ParseOutcome.

    Без обращения к БД. При ошибке бросает доменное исключение с накопленным
    учётом стоимости: TransientError (транзиентные сбои: сеть/таймаут/5xx/429/408)
    или PermanentError (ошибки контента). Материалы не резолвятся в id — это делает фаза B.
    """
    cost = Decimal(0)
    paid_calls = 0
    logger.info(f"[doc={document_id}] Фаза A: старт парсинга, {len(file_data)} байт")

    max_tokens = resolved_llm_parse_max_tokens(settings)
    try:
        resp = await llm.get_provider().vision_completion(
            system=SYSTEM_PROMPT,
            user_text=("Определи тип документа и извлеки данные. ВАЖНО: каждая строка "
                       "из табличной части — это отдельная позиция в items. "
                       "Не объединяй и не суммируй строки, даже если они выглядят одинаково."),
            attachment=llm.PdfAttachment(data=file_data),
            max_tokens=max_tokens,
            timeout=httpx.Timeout(180),
        )
    except llm.LLMProviderError as exc:
        # Маппинг ошибки провайдера в доменную с сохранением биллинга (§2.3).
        cls = TransientError if exc.retryable else PermanentError
        raise cls(str(exc), cost_usd=exc.cost_usd, paid_calls=exc.paid_calls) from exc

    cost = resp.cost_usd
    paid_calls = resp.paid_calls
    logger.info(f"[doc={document_id}] Фаза A: cost=${cost}, finish_reason={resp.finish_reason}")

    if resp.finish_reason == "length":
        raise PermanentError(
            "Ответ модели обрезан по лимиту токенов — часть позиций счёта потеряна. "
            "Попробуйте повторить разбор.",
            cost_usd=cost, paid_calls=paid_calls,
        )
    if resp.completion_tokens and resp.completion_tokens >= max_tokens:
        logger.error(f"[doc={document_id}] completion_tokens={resp.completion_tokens} == max — ответ обрезан")

    response_text = resp.content
    try:
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
        for _inv_idx, inv_data in enumerate(parsed.get("invoices", [])):
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
                logger.error(f"[doc={document_id}] СФ №{inv_number}: некорректная дата: {e}")
                # Всё-или-ничего (parse-then-swap, §2.3): молчаливый `continue` здесь раньше
                # ронял ЭТУ СФ из набора, но если другая СФ в том же документе валидна,
                # phase A вернула бы НЕПОЛНЫЙ набор, а phase B заменила бы им старые данные —
                # тихая потеря СФ при многодокументном PDF. Одна плохая дата → весь reparse
                # проваливается, старый набор СФ остаётся нетронутым.
                raise PermanentError(
                    f"Разбор счёта №{inv_number} неполный: некорректная дата ({e})",
                    cost_usd=cost, paid_calls=paid_calls,
                ) from e

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
            # doc_type=invoice, но ни одной СФ не разобрано (пустой массив invoices —
            # любая некорректная дата теперь роняет весь разбор через PermanentError
            # выше, а не continue). Не создаём документ «parsed с 0 СФ» — это тот
            # артефакт, ради устранения которого вводилась статусная модель (Q2, класс 2).
            raise PermanentError("Ни одной СФ не удалось разобрать из документа",
                                 cost_usd=cost, paid_calls=paid_calls)
    except ProcessingError:
        raise
    except Exception as exc:  # noqa: BLE001 — недоверенное тело/контент LLM после платного 200:
        # неклассифицированная форма (например top-level JSON-массив вместо объекта →
        # AttributeError на .get, либо ValueError/TypeError на кривых числах, либо
        # битое тело ответа не-JSON) не должна улететь наверх неучтённой — платный
        # вызов уже состоялся (инвариант §2.3). Сообщение пользователю — стабильный
        # текст без сырого exc (FIX 6); подробности — только в логе.
        logger.warning(f"[doc={document_id}] Фаза A: не удалось разобрать ответ модели: {exc!r}")
        raise PermanentError("Не удалось разобрать ответ модели",
                             cost_usd=cost, paid_calls=paid_calls) from exc

    logger.info(f"[doc={document_id}] Фаза A: разобрано СФ {len(invoices)}, cost=${cost}")
    return ParseOutcome(doc_type="invoice", invoices=invoices, cost_usd=cost, paid_calls=paid_calls)


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


def _calculate_completeness(inv_data: dict) -> float:
    """Доля заполненности ключевых полей (0.0-1.0). Сигнализирует о пропусках,
    которые модель сама могла не заметить."""
    score = 0
    total = 0

    total += 1
    if inv_data.get("number"):
        score += 1

    total += 1
    if inv_data.get("date"):
        try:
            date.fromisoformat(inv_data["date"])
            score += 1
        except (ValueError, TypeError):
            pass

    total += 1
    if inv_data.get("supplier_name"):
        score += 1

    items = inv_data.get("items", [])
    total += 1
    if items:
        score += 1

    for item in items:
        total += 4
        if item.get("raw_name"):
            score += 1
        if item.get("quantity") and item["quantity"] > 0:
            score += 1
        if item.get("unit_price") and item["unit_price"] > 0:
            score += 1
        if item.get("amount") and item["amount"] > 0:
            score += 1

    return round(score / total, 2) if total > 0 else 0


def _final_confidence(model_conf, completeness: float) -> float:
    """Итоговая уверенность = минимум из (оценка модели) и (полнота данных).
    Если модель не вернула confidence — используем только completeness."""
    try:
        mc = float(model_conf) if model_conf is not None else None
    except (TypeError, ValueError):
        mc = None

    if mc is None:
        return round(completeness, 2)

    # Зажимаем в диапазон [0, 1] на случай, если модель вернёт 95 вместо 0.95 или > 1
    if mc > 1:
        mc = mc / 100 if mc <= 100 else 1.0
    mc = max(0.0, min(1.0, mc))

    # Берём минимум: модель не может быть увереннее, чем заполнено полей
    return round(min(mc, completeness), 2)
