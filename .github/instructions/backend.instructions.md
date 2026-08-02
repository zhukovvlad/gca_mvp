---
applyTo: "backend/**"
---
# Backend

- Только `just`-команды, не `cd backend && ...`.
- Финансы — `Decimal` end-to-end, не `float`. Колонки БД — `Numeric`. Округление — `money_round` из `finance.py` (ROUND_HALF_UP). Вход LLM→DB нормализуй через `Decimal(str(value))`.
- `Invoice.vat_rate` не NOT NULL → всегда `COALESCE(vat_rate, literal(Decimal("20.0")))` в SQL-выражениях.
- `DateTime` хранится как naive UTC. При ручной сериализации в dict-ответах добавляй `"Z"`: `dt.isoformat() + "Z"`. Pydantic-схемы делают это сами.
- CRUD разнесён по `crud/`: `projects`, `materials`, `units`, `documents`, `calculations`, `compensation_corridors`, `suppliers`, `supplier_exclusions`, `admin`. Логику БД — туда, не в роутеры.
- Единицы измерения: позиции счетов нормализуются при записи (`crud/units.py`); агрегация — по `normalized_quantity`. `material_type` в HTTP API — строковый код (`concrete`/`rebar`/`other`), в БД — FK `material_type_id`.
- Направления: параметр `direction` в HTTP API = код `material_types`; резолв и 422 — только через `routers/common.resolve_direction_type`. Фильтр направления в расчётах — строго на выходе (знаменатели разноски по полному счёту), см. `docs/agent/calculations.md`.
- Pydantic-модели запросов/ответов — в файлах роутеров. Логирование — `logging.getLogger(__name__)`.
- ruff line-length 120, target py3.12, правила E/F/I/B/UP/SIM. `Depends()` в аргументах — ок (B008 игнорится).
- Все эндпоинты требуют `get_current_user`. Org-level изоляция данных ещё не включена — см. `docs/TECH_DEBT.md`.
- **LLM-вызовы — только через абстракцию `llm.py`**, не прямой httpx к OpenRouter: `await llm.get_provider().vision_completion(system=..., user_text=..., attachment=llm.PdfAttachment|ImagesAttachment, max_tokens=..., timeout=...)`. Транспорт/envelope — в `llm_openrouter.py` (`OpenRouterProvider`), закреплены контрактными тестами; доменный парсинг туда НЕ переезжает. `llm.py` не импортирует `pdf_orientation` (цикл).
- Конфиг LLM: enum `LLM_PROVIDER` + namespaced `OPENROUTER_*`/`GATEWAY_*`; читать модель/движок/лимиты только через `resolved_*` из `config.py` (алиас-цепочки к legacy `AI_MODEL`/`PDF_ENGINE`/`AI_MAX_TOKENS`), не `os.getenv`/`settings.AI_MODEL` напрямую. Провайдер инициализируется в `main.lifespan` (`init_provider`), PUT `/api/settings` пересобирает его атомарно.
- Ошибки провайдера — `LLMProviderError(retryable, cost_usd, paid_calls, ...)`; домен маппит в `TransientError`/`PermanentError`, сохраняя биллинг. **Инвариант §2.3:** любая ошибка ПОСЛЕ HTTP 200 несёт `cost_usd`+`paid_calls=1`; транспорт/не-200 → `paid_calls=0`. Тексты пользовательских ошибок — стабильные строки, не менять.
- Роль окружения — `APP_ENV` (`dev`/`prod`, deploy-time, дефолт `dev`): единственный потребитель — `backend/db_guard.py::ensure_mutation_allowed`, вызывается на трёх call-site'ах (`alembic/env.py`, `cli.py::_guard`, `main.py::_sweep_stuck_documents`). В `dev` мутировать разрешено loopback-целям и целям из `DB_EXTRA_TARGETS`; в `prod` цели не проверяются. Детали и инварианты — `docs/testing.md`, раздел «Guard от мутации незапланированной БД».

Глубже: методология расчётов — `docs/agent/calculations.md`; модели БД — `docs/agent/database.md`; auth — `docs/agent/auth.md`; парсинг УПД и коррекция ориентации (deskew: detect отдаёт поворот страницы, apply отменяет через `(360−R)`) — `docs/agent/pdf-parsing.md`.
