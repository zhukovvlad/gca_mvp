# UDP — task runner. Запуск: just <команда> или just (=help)
# Используем bash везде (на Windows — git bash), чтобы команды (&&, find, rm -rf)
# работали одинаково на dev-машине и в CI.

set shell := ["bash", "-cu"]
set windows-shell := ["bash", "-cu"]

# Default — показать список команд
default:
    @just --list

# === Цель БД ===
# Дев-цикл живёт на локальном Postgres, Neon в нём больше не участвует: за
# корпоративной TLS-инспекцией коннект к Neon рвётся на SCRAM channel binding,
# да и разрабатывать на прод-базе было плохой идеей до аварии. Подробности —
# docs/testing.md, раздел «Локальная dev-БД».
#
# local (дефолт) — локальный DSN подставляется инлайн, .env не участвует.
# env            — DATABASE_URL берётся из backend/.env, каким бы он ни был.
#
# Значение называется `env`, а не `neon`, потому что описывает источник строки,
# а не её содержимое: у контрибьютора в .env может стоять свой Postgres, и ему
# нужен путь мимо жёстко прошитого local-кластера. Разрешение мутировать цель
# из этого НЕ следует — оно отдельное, через APP_ENV/DB_EXTRA_TARGETS (db_guard).
# Иначе `db_target=env` тихо снимал бы защиту тому, чья цель guard'ом не разрешена.
#
# Переопределение: just db_target=env <рецепт> либо UDP_DB_TARGET=env в окружении.
# Имя переменной с префиксом: DB_TARGET слишком общее для машины, где живёт
# несколько проектов, а мусорное значение ломает разбор ВСЕХ рецептов.
db_target := env_var_or_default("UDP_DB_TARGET", "local")

db_env := if db_target == "local" { 'DATABASE_URL="' + dev_db_local + '"' } \
          else if db_target == "env" { "" } \
          else { error("db_target должен быть local или env, получено: " + db_target) }

# === Setup ===

# Установить все зависимости (backend + frontend, e2e добавим позже)
install: install-backend install-frontend
    @echo "==> Установка завершена"

install-backend:
    cd backend && uv sync

install-frontend:
    cd frontend && npm ci

# === Dev ===

# ИНВАРИАНТ S1 (async processing): один процесс — workers=1, replicas=1,
# деплой строго stop-then-start (no-overlap; rolling запрещён до Ступени 2).
# Startup-sweep на старте переводит pending/processing в error — при overlap
# новый процесс пометил бы живые таски старого. См. docs/agent/pdf-parsing.md.
# Backend на :8259 (БД — по db_target, дефолт local)
dev-backend: pg-ensure
    @echo "==> БД: {{db_target}}"
    cd backend && {{db_env}} uv run uvicorn main:app --reload --port 8259

dev-frontend:
    cd frontend && npm run dev

# === Tests ===

# Все backend-тесты. Локальный кластер обязателен: fallback на Neon свёрнут
# ревизией §12 спеки (Neon test-ветка исключена).
test-backend: test-backend-local

# Только unit (быстро)
test-backend-unit:
    cd backend && uv run pytest tests/unit -v

# Только integration (нужен TEST_DATABASE_URL)
test-backend-integration:
    cd backend && uv run pytest tests/integration -v

# Точечный прогон integration по -k паттерну
test-int-k pattern:
    cd backend && uv run pytest tests/integration -v -k "{{pattern}}"

# --- Локальный Postgres (быстрые integration + dev-БД) ---
# Портативный PostgreSQL 16 + pgvector (conda-forge, micromamba) в профиле
# пользователя — без админ-прав и Docker; тот же стек, что CI-образ
# pgvector/pgvector:pg16. Запросы ~0.2 мс вместо ~43 мс RTT до Neon —
# интеграционный слой ~6.5x быстрее. Auth trust (только localhost).
# Один кластер держит несколько баз: udp_test (тесты, дропается) и udp_dev.
# Установка с нуля: docs/testing.md, раздел «Локальный тестовый Postgres».

pg_local := "$LOCALAPPDATA/Programs/udp-pgtest"
# Порт 5459, а не 5433: машина общая, инсталляторы Postgres берут 5432 и далее
# инкрементом (5433, 5434...). 5459 из этой последовательности выпадает и лежит
# ниже эфемерного диапазона Windows (49152+), так что ОС его тоже не займёт.
pg_port := "5459"
test_db_local := "postgresql+psycopg://postgres@localhost:" + pg_port + "/udp_test"
dev_db_local := "postgresql+psycopg://postgres@localhost:" + pg_port + "/udp_dev"

# Запустить локальный Postgres (no-op, если уже работает)
pg-test-start:
    @test -d "{{pg_local}}/data" || { echo "Локальный Postgres не установлен — см. docs/testing.md, раздел «Локальный тестовый Postgres»"; exit 1; }
    @if "{{pg_local}}/Library/bin/pg_ctl" -D "{{pg_local}}/data" status >/dev/null 2>&1; then exit 0; fi; \
     if (exec 3<>/dev/tcp/127.0.0.1/{{pg_port}}) 2>/dev/null; then \
       echo "Порт {{pg_port}} занят посторонним процессом (наш кластер не запущен)."; \
       echo "Машина общая — вероятно, порт забрал чужой сервис. Освободите его либо смените pg_port в justfile и port в {{pg_local}}/data/postgresql.conf."; \
       exit 1; \
     fi; \
     "{{pg_local}}/Library/bin/pg_ctl" -D "{{pg_local}}/data" -l "{{pg_local}}/data/log.txt" start

pg-test-stop:
    "{{pg_local}}/Library/bin/pg_ctl" -D "{{pg_local}}/data" stop

# Зависимость для рецептов, уважающих db_target: при цели env поднимать
# локальный кластер незачем, а падать из-за его отсутствия — тем более.
# Поднять локальный кластер, если db_target=local (при db_target=env — no-op)
pg-ensure:
    @if [ "{{db_target}}" = "local" ]; then just pg-test-start; fi

# Integration против локального Postgres
test-int-local: pg-test-start
    cd backend && TEST_DATABASE_URL="{{test_db_local}}" uv run pytest tests/integration -v

# Точечный локальный прогон по -k паттерну
test-int-local-k pattern: pg-test-start
    cd backend && TEST_DATABASE_URL="{{test_db_local}}" uv run pytest tests/integration -v -k "{{pattern}}"

# Все backend-тесты против локального Postgres
test-backend-local: pg-test-start
    cd backend && TEST_DATABASE_URL="{{test_db_local}}" uv run pytest

# Точечный прогон unit по -k паттерну
test-unit-k pattern:
    cd backend && uv run pytest tests/unit -v -k "{{pattern}}"

# Frontend
test-frontend:
    cd frontend && npm test

# Точечный фронт-прогон одного файла
test-frontend-file file:
    cd frontend && npx vitest run {{file}}

test-frontend-watch:
    cd frontend && npm run test:watch

test-frontend-ui:
    cd frontend && npm run test:ui

# Combined: backend + frontend (без E2E — он отдельно)
test:
    just test-backend
    just test-frontend

# === Coverage ===

coverage-backend:
    cd backend && uv run pytest --cov=. --cov-report=html --cov-report=term

coverage-frontend:
    cd frontend && npm run test:coverage

# === Lint ===

lint-backend:
    cd backend && uv run ruff check .

lint-frontend:
    cd frontend && npm run lint

typecheck-frontend:
    cd frontend && npx tsc -b --noEmit

# Combined lint
lint:
    just lint-backend
    just lint-frontend

format-backend:
    cd backend && uv run ruff format .

# === DB ===

# Создать НОВУЮ ревизию Alembic (без autogenerate — тело заполняется вручную).
# Это создание нового файла в versions/, НЕ правка исторических миграций.
db-revision message:
    cd backend && uv run alembic revision -m "{{message}}"

db-migrate: pg-ensure
    @echo "==> БД: {{db_target}}"
    cd backend && {{db_env}} uv run alembic upgrade head

# Накатить миграции на локальную тестовую БД (udp_test)
db-test-migrate: pg-test-start
    cd backend && DATABASE_URL="{{test_db_local}}" uv run alembic upgrade head

# Проверка дрейфа ORM/БД: локальная тест-БД до head + alembic check.
# Нулевой код = моделей и схемы совпадают (нет pending upgrade ops).
db-test-check: pg-test-start
    cd backend && DATABASE_URL="{{test_db_local}}" uv run alembic upgrade head
    cd backend && DATABASE_URL="{{test_db_local}}" uv run alembic check

# Идемпотентно — можно гонять повторно. Дальше: create-superuser, create-org,
# create-user и запуск через just dev-backend. db_target здесь не при чём:
# createdb — операция над локальным кластером по определению.
# Создать локальную dev-БД udp_dev и накатить миграции
db-dev-init: pg-test-start
    @"{{pg_local}}/Library/bin/psql" -h 127.0.0.1 -p {{pg_port}} -U postgres -Atc \
      "select 1 from pg_database where datname='udp_dev'" | grep -q 1 \
      || "{{pg_local}}/Library/bin/createdb" -h 127.0.0.1 -p {{pg_port}} -U postgres udp_dev
    cd backend && DATABASE_URL="{{dev_db_local}}" uv run alembic upgrade head


# --sessions — базу (udp_dev / udp_test / базы других проектов) выбираем в
# браузере, поэтому один процесс обслуживает весь кластер.
# Neon-базам pgweb не нужен: в консоли Neon есть свои Tables и SQL Editor.
# Бинарник вне профиля (C:\dev-cache) — профиль на 20-ГБ User Disk.
pgweb := "/c/dev-cache/pgweb/pgweb.exe"

# Веб-просмотр таблиц локального кластера на http://localhost:8081
db-web: pg-test-start
    @test -f "{{pgweb}}" || { echo "pgweb не установлен — см. docs/testing.md, раздел «Веб-просмотр таблиц»"; exit 1; }
    "{{pgweb}}" --sessions --bind localhost --listen 8081

# === Misc ===

# Создать суперюзера системы (интерактивный ввод пароля)
create-superuser email: pg-ensure
    cd backend && {{db_env}} uv run python -m cli create-superuser --email {{email}}

# Создать организацию
create-org name: pg-ensure
    cd backend && {{db_env}} uv run python -m cli create-org --name "{{name}}"

# Создать пользователя организации (роль: superadmin | admin | member)
create-user email org_id role="member": pg-ensure
    cd backend && {{db_env}} uv run python -m cli create-user --email {{email}} --org-id {{org_id}} --role {{role}}

# Записать snapshot AI-ответа от реального PDF (пути — относительно backend/; см. docs/testing.md)
snapshot-ai pdf scenario:
    cd backend && uv run python scripts/snapshot_ai_responses.py "{{pdf}}" "{{scenario}}"

clean:
    rm -rf backend/.pytest_cache backend/htmlcov backend/.coverage backend/coverage.xml
    find backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# === Storage ===

# Локальный MinIO (S3): API :9259, консоль :9260, данные — C:\minio\data
# ВАЖНО: данные и temp держим на Windows-томе C:\ (135 ГБ), а НЕ в профиле.
# Профиль C:\Users\<user>\ смонтирован на отдельный маленький 20-ГБ "User Disk";
# при его переполнении MinIO меряет свободное место по data-каталогу и падает
# с XMinioStorageFull, хотя сам бакет крошечный. Путь вне профиля это лечит.
minio:
    mkdir -p /c/minio/data /c/minio/tmp
    TMP="C:/minio/tmp" TEMP="C:/minio/tmp" TMPDIR="C:/minio/tmp" minio server C:/minio/data --address ":9259" --console-address ":9260"
