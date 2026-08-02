# LLM-провайдер — как пользоваться

Как настраивать и эксплуатировать переключаемый LLM-провайдер, и как звать LLM
из кода. Дизайн — в спеке `docs/superpowers/specs/2026-07-23-llm-provider-toggle-design.md`.

## TL;DR

- `LLM_PROVIDER=openrouter` (дефолт) — полностью рабочий режим, ведёт себя как раньше.
- `LLM_PROVIDER=gateway` — заложен в конфиг, но **пока не реализован** (фабрика бросает `RuntimeError` «после спайка»). Это фаза 2.
- Ключ и модель можно менять в рантайме через страницу «Настройки» / `PUT /api/settings` — **без рестарта**.
- Переключение самого провайдера — только через `.env` при деплое + рестарт.

## 1. Настройка при деплое (`backend/.env`)

```dotenv
# Провайдер LLM: openrouter (дефолт) | gateway (контур МР; после спайка)
LLM_PROVIDER=openrouter

# --- namespace openrouter ---
OPENROUTER_API_KEY=sk-...                      # можно пусто → задать позже через UI/API
OPENROUTER_MODEL=anthropic/claude-sonnet-5
OPENROUTER_PDF_ENGINE=native                   # рабочее значение
OPENROUTER_MAX_TOKENS=64000
# OPENROUTER_BASE_URL=                          # пусто → https://openrouter.ai/api/v1

# --- namespace gateway (фаза 2, пока не активен) ---
# GATEWAY_BASE_URL=
# GATEWAY_MODEL=
```

`APP_ENV` (`dev`/`prod`) — тоже deploy-time переменная `backend/.env`, но к LLM-провайдеру
отношения не имеет: это ось `backend/db_guard.py` (роль окружения для guard'а от мутации
БД), см. `docs/testing.md`, раздел «Guard от мутации незапланированной БД».

**Движок парсинга PDF** (`OPENROUTER_PDF_ENGINE`):

| Значение | Что делает |
|----------|-----------|
| `native` | модель читает страницы PDF как изображения (рабочее значение) |
| `mistral-ocr` | OCR (~$2/1000 страниц), лучше для сложных табличных бланков |
| `pdf-text` | извлечение текста (бесплатно, плохо с табличными формами) |

**Обратная совместимость (алиасы).** Старые переменные ещё читаются:
`OPENROUTER_MODEL → AI_MODEL → дефолт`, `OPENROUTER_PDF_ENGINE → PDF_ENGINE → native`,
`OPENROUTER_MAX_TOKENS → AI_MAX_TOKENS → 64000`. Namespaced-переменная побеждает; пустая
или пробельная строка = «не задано» (падаем на legacy, затем на дефолт). При использовании
legacy-переменной в лог один раз пишется предупреждение об устаревании.

**Fail-fast на старте.** Невалидный `LLM_PROVIDER`, а для `gateway` — отсутствие
`GATEWAY_BASE_URL`/`GATEWAY_MODEL` — уронят запуск сразу. **Ключ OpenRouter на старте
НЕ обязателен**: приложение поднимется без него, ошибка «API-ключ OpenRouter не настроен»
придёт только при первом разборе — это специально, чтобы можно было запустить и задать ключ
через UI.

## 2. Рантайм-настройка — «Настройки» → «Парсинг»

Меняется без рестарта (UI или напрямую API):

**`GET /api/settings`**
```json
{
  "provider": "openrouter",
  "can_edit_model": true,
  "cost_available": true,
  "api_key_set": true,
  "model": "anthropic/claude-sonnet-5",
  "confidence_threshold": 0.7
}
```

**`PUT /api/settings`** — присылать ТОЛЬКО изменённые поля:
```json
{ "api_key": "sk-...", "model": "anthropic/claude-sonnet-5", "confidence_threshold": 0.8 }
```

- В `openrouter`-режиме смена `api_key`/`model` **сразу атомарно пересобирает провайдер** —
  рестарт не нужен (раньше установленный через API ключ не действовал до перезапуска).
- В `gateway`-режиме `api_key`/`model` запрещены → **403** («задаются при деплое»);
  `confidence_threshold` менять можно.

Фронт по capabilities сам скрывает поле модели (`can_edit_model=false`) и показывает
«стоимость недоступна» вместо суммы (`cost_available=false`). Backend всё равно enforce'ит
запреты независимо от фронта.

## 3. Переключить провайдера

`LLM_PROVIDER` — **deploy-time** переключатель (комплаенс, не рантайм-тумблер): читается
на старте. Смена — правка `.env` + рестарт. Работает при инварианте single-process
(`workers=1, replicas=1`). **Сейчас переключать некуда** — `gateway` включится в фазе 2
(нужен gateway-спайк §7 спеки).

## 4. Вызов LLM из кода (для разработчиков)

Не ходить в OpenRouter напрямую — только через локатор `backend/llm.py`:

```python
import httpx
import llm
from config import resolved_llm_parse_max_tokens, settings

resp = await llm.get_provider().vision_completion(
    system=SYSTEM_PROMPT,
    user_text="...",
    attachment=llm.PdfAttachment(data=file_bytes),   # или llm.ImagesAttachment(images=(...,))
    max_tokens=resolved_llm_parse_max_tokens(settings),
    timeout=httpx.Timeout(180),                       # свойство операции: parse=180, detect=Timeout(30, connect=5.0)
)
# resp.content, resp.finish_reason, resp.cost_usd (Decimal), resp.completion_tokens, resp.paid_calls
```

Правила:

- Транспорт/форма запроса — в `backend/llm_openrouter.py` (`OpenRouterProvider`), закреплены
  контрактными тестами `backend/tests/unit/test_openrouter_contract.py`. Менять payload —
  только вместе с тестами.
- Доменный парсинг (JSON УПД, `doc_type`, сверка сумм, разбор поворотов) в провайдер НЕ
  переносить. `llm.py` не импортирует `pdf_orientation` (иначе цикл импортов).
- Ошибки провайдера — `llm.LLMProviderError(retryable, cost_usd, paid_calls, code, correlation_id)`.
  Домен ловит и маппит в `TransientError` (retryable) / `PermanentError`, **сохраняя биллинг**.
- **Инвариант учёта (§2.3):** любая ошибка ПОСЛЕ HTTP 200 несёт `cost_usd` + `paid_calls=1`;
  транспортный сбой и не-200 → `paid_calls=0`.
- Конфиг читать через `resolved_*` из `config.py`, не `os.getenv`/`settings.AI_MODEL` напрямую.

## 5. Диагностика

| Симптом | Причина / что делать |
|---------|----------------------|
| «API-ключ OpenRouter не настроен» | ключ не задан — `PUT /api/settings` или `.env` |
| «OpenRouter API ошибка: {код}» | не-200 от API; 5xx/408/429 — транзиентно (ретраится), прочее — перманентно |
| «Ответ модели без содержимого» / «Не удалось разобрать ответ модели» | битое тело платного 200 — стоимость всё равно учтена |
| «Сервис распознавания ориентации недоступен/отклонил запрос» | сбой detect на deskew-reparse |
| В Review «стоимость недоступна» | `cost_available=false` (контурный режим) — стоимость от провайдера недоступна |

Тексты пользовательских ошибок стабильные (по ним же гоняются тесты) — не менять.

## См. также

- Спека (дизайн, инварианты, AC): `docs/superpowers/specs/2026-07-23-llm-provider-toggle-design.md`
- Девлог реализации фазы 1: `docs/devlog/2026-07-24-llm-provider-toggle.md`
- Парсинг УПД и коррекция ориентации: `docs/agent/pdf-parsing.md`
- [deployment.md](deployment.md) — вторая deploy-time ось: `APP_ENV`/`DATABASE_URL`,
  профили развёртывания, guard от мутации незапланированной БД.
