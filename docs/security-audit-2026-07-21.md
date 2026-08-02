# Аудит безопасности UDP — 2026-07-21

Методология: `npm audit` (frontend), `pip-audit` (backend deps), `bandit` (SAST backend), ручное ревью `auth.py`, `security.py`, `routers/auth.py`, `main.py`, `config.py`, `utils.py`, загрузки файлов в `routers/invoices.py`.

T3MP3ST не применялся (клонирование отклонено пользователем); вместо него — штатный стек аудита.

## Сводка

| Источник | High | Medium | Low | Итого |
|---|---|---|---|---|
| npm audit (frontend) | 6 | 2 | 3 | 11 |
| pip-audit (backend) | 22 записи в 4 пакетах | — | — | 22 |
| bandit (SAST) | 0 | 0 | 2 | 2 |
| Ручное ревью | 0 | 3 | 4 | 7 |

---

## 1. Зависимости frontend (npm audit)

✅ **ИСПРАВЛЕНО 2026-07-21**: выполнен `npm audit fix` — found 0 vulnerabilities. Тесты 219/219 зелёные, lint чист. Ниже — исходный срез на момент аудита:

Чинится `npm audit fix` (все — не ломающие обновления в рамках lockfile):

| Пакет | Severity | Проблема |
|---|---|---|
| vite 8.0.0–8.0.15 | high | `server.fs.deny` bypass на Windows (GHSA-fx2h-pf6j-xcff); NTLMv2 hash disclosure через UNC (dev-риск) |
| undici ≤7.27.2 | high | 7 advisory: TLS bypass в SOCKS5, header injection, DoS WebSocket и др. |
| hono ≤4.12.24 | high | 10 advisory: path traversal в serve-static на Windows, CORS reflect-any-origin, JWT middleware принимает любой scheme |
| js-yaml 4.0.0–4.2.0 | high | DoS квадратичной сложности через merge keys |
| brace-expansion 3.0.0–5.0.6 | high | DoS экспоненциального времени |
| form-data 4.0.0–4.0.5 | high | CRLF injection в multipart |
| axios 1.0.0–1.17.0 | moderate | DoS через рекурсию formDataToJSON; prototype pollution auth subfields |
| qs 6.11.1–6.15.1 | moderate | DoS в stringify |
| react-router 7.12.0–7.15.0 | low | Потенциальный CSRF через document requests (PUT/PATCH/DELETE) |
| @babel/core ≤7.29.0 | low | Arbitrary File Read через sourceMappingURL |

Примечание: vite/hono/undici/@babel — преимущественно dev/toolchain-риски; axios — прямая зависимость фронта, стоит обновить в первую очередь вместе с react-router.

## 2. Зависимости backend (pip-audit)

✅ **ИСПРАВЛЕНО 2026-07-21**: все 4 пакета обновлены в `backend/requirements.txt` (python-multipart 0.0.31, python-dotenv 1.2.2, pydantic-settings 2.14.2, Pillow 12.3.0), переустановлены; повторный pip-audit — **No known vulnerabilities found**. Backend-тесты 584 passed / 6 skipped / 0 failed (локальный Postgres). Ниже — исходный срез:

| Пакет | Установлено | Фикс | Комментарий |
|---|---|---|---|
| python-multipart | 0.0.9 | ≥0.0.31 | **7 PYSEC (2026-3036…3040, 1851, 1852)** — DoS при парсинге multipart. Критично: приложение принимает загрузку файлов |
| pillow | 12.2.0 | ≥12.3.0 | 13 записей (PYSEC-2026-2253…2257, 3451–3453, CVE-2026-54058) — обработка изображений в deskew |
| python-dotenv | 1.0.1 | ≥1.2.2 | PYSEC-2026-2270 |
| pydantic-settings | 2.14.1 | ≥2.14.2 | GHSA-4xgf-cpjx-pc3j |

Рекомендация: поднять минимальные версии в `backend/requirements.txt`:
`python-multipart>=0.0.31`, `pillow>=12.3.0`, `python-dotenv>=1.2.2`, `pydantic-settings>=2.14.2`.

> **Обновление (миграция на uv):** источник backend-зависимостей переведён с `requirements*.txt` на `pyproject.toml` + `uv.lock`. Указанные фиксы зафиксированы как `>=` границы в pyproject; точные версии — в `uv.lock`. См. `docs/superpowers/specs/2026-07-21-uv-migration-design.md`.

## 3. SAST (bandit, backend без tests/alembic/scripts)

0 high / 0 medium. Два low (CWE-703), оба осознанные:
- `pdf_orientation.py:44` — try/except/continue при чтении XObject (сознательный пропуск нестандартных объектов, есть noqa-комментарий);
- `routers/invoices.py:539` — try/except/pass при удалении файла из S3 (best-effort cleanup).

## 4. Ручное ревью кода

### Что сделано хорошо ✅
- Argon2id для паролей (OWASP-рекомендация), `SECRET_KEY` обязателен и ≥32 символов (fail-fast при старте).
- JWT: фиксированный алгоритм HS256 (`algorithms=["HS256"]` — нет alg-confusion), проверка `type=="access"`, jti заложен под будущий блэклист.
- Refresh-токены: в БД только sha256-хэш, ротация при каждом refresh, `with_for_update()` против гонок, отзыв при logout.
- CSRF: double-submit cookie — middleware + Depends(require_csrf) как defense-in-depth; login корректно исключён.
- CORS: конкретный whitelist origin'ов (не wildcard) с credentials.
- Изоляция проектов: 404 вместо 403 для чужих проектов (не раскрывается существование).
- `get_client_ip`: X-Forwarded-For доверяется только при TRUSTED_PROXIES>0.
- Логин не раскрывает, что именно неверно (email/пароль).

### Найденные проблемы ⚠️

**M1. Нет rate limiting на `/api/auth/login`** (Medium)
`routers/auth.py:92` — брутфорс пароля ограничен только стоимостью Argon2 (~100мс/попытка), логирование есть, но блокировки/throttling нет. Рекомендация: slowapi/собственный счётчик по IP+email, или lockout после N неудачных.

**M2. Загрузка файлов: валидация только по расширению, нет лимита размера** (Medium)
`routers/invoices.py` upload_pdf: проверка `filename.endswith(".pdf")` — без проверки magic bytes (`%PDF-`); `file_bytes = await file.read()` читает весь файл в память без лимита → memory DoS большими файлами; не-PDF контент уйдёт в pypdfium2/LLM-пайплайн (расход токенов OpenRouter = финансовый DoS). Рекомендация: проверка сигнатуры, лимит (например 20–50 МБ) с потоковым чтением.

**M3. User enumeration по таймингу логина** (Low)
`routers/auth.py:101` — если пользователь не найден, `verify_password` не вызывается; разница во времени ответа (~100мс Argon2 vs ~1мс) позволяет перебирать существующие email. Рекомендация: dummy-verify против захардкоженного хэша.

**L1. `COOKIE_SECURE: bool = False` по умолчанию** (Low, конфигурационный риск)
`config.py:20` — в проде без явного `COOKIE_SECURE=true` куки уйдут по HTTP. Рекомендация: задокументировать в deploy-чеклисте (уже есть `backend/.env.example` — проверить, что там есть предупреждение).

**L2. Swagger-документация открыта в проде** (Low)
`main.py` — `/docs`, `/redoc`, `/openapi.json` доступны без аутентификации (и в CSRF-exempt). Раскрывает схему API. Рекомендация: отключать при `ENV=prod` (`docs_url=None`).

**L3. Дефолтные креды MinIO в конфиге** (Info)
`config.py:34-35` — `minioadmin/minioadmin` как defaults. Для dev ок, в проде — только через env. Аналогично `DATABASE_URL` с `CHANGE_ME`.

**L4. Access-токен без отзыва** (Info, by design)
30-минутный access-токен не отзывается при logout/смене пароля (jti заложен, блэклист не реализован). Приемлемый компромисс с коротким TTL; зафиксировать в TECH_DEBT при необходимости.

## Приоритеты

1. **Обновить зависимости**: backend — 4 пакета зафиксированы `>=` границами в `backend/pyproject.toml` (+ точные версии в `uv.lock`), см. §2 ✅ (источник зависимостей мигрировал с `requirements.txt` на uv — было критично из-за python-multipart, сетевой DoS на загрузке); фронт — `npm audit fix` (в первую очередь axios + react-router).
2. **M2** — лимит размера + magic bytes на upload.
3. **M1** — rate limiting на login.
4. M3, L1, L2 — по возможности.