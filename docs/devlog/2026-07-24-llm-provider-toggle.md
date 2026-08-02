# 2026-07-24 — Переключаемый LLM-провайдер (фаза 1: абстракция OpenRouter)

**Ветка:** `feature/llm-provider-gateway` (от `main`)
**PR:** [#44](https://github.com/zhukovvlad/udp-tenders/pull/44)
**Метод:** subagent-driven-development (Opus — оркестратор + пофайловый ревьюер; Haiku на механике — Task 1/2/8; Sonnet на живом коде — Task 3–7; Fable — финальный whole-branch; правки — диффом)
**Спека:** `docs/superpowers/specs/2026-07-23-llm-provider-toggle-design.md`
**План:** `docs/superpowers/plans/2026-07-23-llm-provider-toggle.md`

## Задача

Ввести deploy-time переключатель `LLM_PROVIDER` с абстракцией провайдера так, что режим `openrouter` (дефолт) работает **1:1 как раньше** (закреплено контрактными тестами), а всё, что не зависит от gateway-спайка, готово к появлению `GatewayProvider`. Контекст — корп-требование: в контуре МР приложение ходит к внешним LLM только через внутренний OpenAI-совместимый gateway с Keycloak-авторизацией; текущий код завязан на OpenRouter-проприетарные механизмы (`plugins`, `usage.cost`, `type:file`).

## Что сделано (8 задач, коммиты `13a3320..d6dd257`)

1. **Конфиг (§1)** (`9a89f3c`): enum `LLM_PROVIDER: openrouter|gateway` (дефолт `openrouter`), namespaced-переменные `OPENROUTER_MODEL/OPENROUTER_PDF_ENGINE/OPENROUTER_MAX_TOKENS`, `GATEWAY_BASE_URL/GATEWAY_MODEL`. Резолверы `resolved_openrouter_*` с алиас-цепочками к legacy (`AI_MODEL`→, `PDF_ENGINE`→, `AI_MAX_TOKENS`→) и warn-once по устаревшим. `validate_llm_settings` (fail-fast на старте; ключ OpenRouter НЕ обязателен — сохранён сценарий «запустить без ключа, задать через Settings API»). Нейтральный `resolved_llm_parse_max_tokens` (домен не знает провайдера).
2. **`llm.py` — интерфейс + локатор (§2.1, §2.3)** (`a347fd1`): типы `PdfAttachment`/`ImagesAttachment`/`LLMResponse`/`LLMProviderError` (несёт биллинг: `cost_usd`/`paid_calls`/`code`/`correlation_id`) + `LLMProvider` Protocol (`vision_completion`); module-level service locator `init/get/reset_provider`. `llm.py` **не импортирует** `pdf_orientation` (иначе цикл).
3. **`OpenRouterProvider` + контрактные тесты (AC-1)** (`f0bc35d`): транспорт/envelope вынесены из `parse_pdf`/`detect_rotations` 1:1. Форма payload (порядок частей `content`, `plugins`, `usage:{include:true}`, `max_tokens`, auth) закреплена двумя контрактными тестами. Осознанный фикс: `usage: null` больше не роняет разбор (`data.get("usage") or {}` → успех с `cost=0`) — закрыт пункт TECH_DEBT.
4. **Рефактор `parse_pdf` + lifespan (§2.2, §1)** (`9c5a426`): парсер зовёт `llm.get_provider().vision_completion(...)`, доменный разбор (fence→JSON→`doc_type`→сверка→`ParsedInvoice`) остаётся на месте под прежним guard'ом. `LLMProviderError → Transient/Permanent` с сохранением биллинга. `lifespan` инициализирует провайдер ДО startup-sweep (fail-fast раньше мутаций БД), `reset` в `finally`.
5. **Рефактор `detect_rotations` + §2.5** (`c0db8c3`): детект через провайдер; **смена семантики битого envelope**: при HTTP 200 без `choices` → `PermanentError` с cost/paid (раньше — тихие нули); целый envelope с непарсящимся содержимым → по-прежнему нули. Удалены мёртвые `OPENROUTER_URL`/`OPENROUTER_BASE_URL` из `pdf_parser`.
6. **Settings API (§5)** (`3106fe2`): GET отдаёт capabilities (`provider`, `can_edit_model`, `cost_available`, `api_key_set`, `model`); PUT в gateway-режиме запрещает `api_key`/`model` (403); в openrouter — пишет namespaced `OPENROUTER_MODEL` (+ зачистка legacy `AI_MODEL`) и **атомарно пересобирает провайдер** (чинит латентный баг «ключ/модель не действуют до рестарта»).
7. **Frontend (§5, AC-5/6)** (`ba69e7b`): узкий DTO `SettingsUpdate` (частичный PUT только изменённых разрешённых полей — enforced на уровне типов); скрытие поля модели по `can_edit_model`; «стоимость недоступна» по `cost_available`.
8. **Гигиена (§8)** (`5bca49c`): `.gitignore` для gateway-токенов; namespaced-переменные в `.env.example`.

**Вне scope (фаза 2):** `GatewayProvider`, подпакет `backend/gateway_client/`, golden eval (§6). В этой фазе `LLM_PROVIDER=gateway` валиден в конфиге, но фабрика даёт понятный `RuntimeError` «после спайка».

## Верификация

- **Ревью:** 8 задач приняты пофайлово (Opus: дифф против текста задачи + спеки, прогон тестов задачи, чек инвариантов — §2.3 биллинг, тексты ошибок 1:1, домен не в провайдере, отсутствие цикла импортов). Финал (Fable, whole-branch) — **Ready to merge: Yes**, 0 Critical, 1 Important (test-only утечка process-global state в PUT-тестах → исправлена, `51019b5`) + Minor.
- **Приёмка локально:** `just lint` PASS; `just test` PASS — backend **614 passed / 6 skipped**, frontend **222 passed** (29 файлов).
- **CI на PR #44:** `backend-tests` PASS (39с), `frontend-tests` PASS (49с), CodeRabbit PASS.

## Решения и нюансы

- **Шов инжекции — service locator, не DI-проброс.** Тесты кодовой базы стоят на module-level monkeypatch, BackgroundTasks не требуют протаскивания `llm_provider=` через 4 сигнатуры. Экземпляр провайдера неизменяем; ссылка локатора атомарно заменяема (для PUT). Работает при single-process инварианте S1 (`workers=1, replicas=1`).
- **Тексты пользовательских ошибок — 1:1.** Единственное осознанное отступление — `usage: null` (см. выше).
- **§2.3 (биллинг платного 200) держится на исключении:** любая ошибка после HTTP 200 несёт `cost_usd`/`paid_calls=1`; транспорт/не-200 → `paid_calls=0`. Доменный код конвертирует `LLMProviderError` в `Transient/PermanentError`, сохраняя учёт.
- **PUT пишет namespaced-ключ, а не legacy.** Иначе заданный в env `OPENROUTER_MODEL` перекрыл бы записанное по алиас-цепочке §1, и PUT молча не действовал бы (регресс закрыт тестом).

## Follow-up по ревью CodeRabbit (PR #44, `d6dd257`)

Каждое замечание проверено против кода (не слепая правка):
- **Приняты (5):** докстринги `_fetch` (snapshot) и `_allow_respx` (тест); `Review.tsx` — cost показывается только при `cost_available === true` (fail-closed: gateway/загрузка/сбой запроса не показывают вводящую в заблуждение сумму); пункт TECH_DEBT `usage:null` переписан в прошедшем времени; языки code-fence в плане (markdownlint).
- **Отклонены с обоснованием:** (a) «краш при пустой модели» (Major) — **ложно**: `resolved_openrouter_model` фолбэчит на дефолт, `RuntimeError` не летит; (b) restore `cfg.CONFIDENCE_THRESHOLD` (Major) — **ложная посылка**: `update_settings` этот синглтон не мутирует; (c) JSDoc на TS — не конвенция репо (соседний TS без JSDoc); (d) enum-валидация PDF-движка на старте — **решение владельца «не добавлять»** (движок всегда резолвится в валидный дефолт; хардкод набора связал бы config с каталогом плагинов OpenRouter).

## Осталось

- Merge PR #44 — решение владельца.
- **Фаза 2** (отдельные планы, ждут gateway-спайка §7): `GatewayProvider` + `gateway_client/` (Keycloak token lifecycle, подача PDF, `GATEWAY_MAX_TOKENS`), golden eval + baseline (§6), IT-вопросы §10.
