# GCA (База расценок генподряда) — task runner. Запуск: just <команда> или just (=help)
# Используем bash везде (на Windows — git bash), чтобы команды (&&, find, rm -rf)
# работали одинаково на dev-машине и в CI.

set shell := ["bash", "-cu"]
set windows-shell := ["bash", "-cu"]

# Default — показать список команд
default:
    @just --list

# === Цель БД ===
# local (дефолт) — локальный DSN подставляется инлайн, .env не участвует.
# env            — DATABASE_URL берётся из backend/.env, каким бы он ни был.
#
# Переопределение: just db_target=env <рецепт> либо GCA_DB_TARGET=env в окружении.
db_target := env_var_or_default("GCA_DB_TARGET", "local")

db_env := if db_target == "local" { 'DATABASE_URL="' + dev_db_local + '"' } \
          else if db_target == "env" { "" } \
          else { error("db_target должен быть local или env, получено: " + db_target) }

# === Setup ===

# Установить все зависимости (backend + frontend)
install: install-backend install-frontend
    @echo "==> Установка завершена"

install-backend:
    cd backend && uv sync

install-frontend:
    cd frontend && npm ci

# === Dev ===

# ИНВАРИАНТ (AGENTS.md §3): строго ОДИН worker-процесс uvicorn — это условие
# корректности startup-recovery import_jobs. Никаких --workers N.
# Backend на :8259 (БД — по db_target, дефолт local)
dev-backend: pg-ensure
    @echo "==> БД: {{db_target}}"
    cd backend && {{db_env}} uv run uvicorn main:app --reload --port 8259

dev-frontend:
    cd frontend && npm run dev

# === Tests ===

# Все backend-тесты (нужен локальный кластер)
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
# пользователя — без админ-прав и Docker. Кластер общий для проектов на машине
# (установлен как udp-pgtest); GCA живёт в нём отдельными базами gca_dev/gca_test.
# Auth trust (только localhost). В тестах та же мажорная версия PG, что и в
# проде (PG16, UNIQUE NULLS NOT DISTINCT — AGENTS.md §11).

pg_local := "$LOCALAPPDATA/Programs/udp-pgtest"
# Порт 5459 — вне последовательности инсталляторов (5432+) и ниже эфемерного диапазона
pg_port := "5459"
test_db_local := "postgresql+psycopg://postgres@localhost:" + pg_port + "/gca_test"
dev_db_local := "postgresql+psycopg://postgres@localhost:" + pg_port + "/gca_dev"

# Запустить локальный Postgres (no-op, если уже работает)
pg-test-start:
    @test -d "{{pg_local}}/data" || { echo "Локальный Postgres не установлен — см. README"; exit 1; }
    @if "{{pg_local}}/Library/bin/pg_ctl" -D "{{pg_local}}/data" status >/dev/null 2>&1; then exit 0; fi; \
     if (exec 3<>/dev/tcp/127.0.0.1/{{pg_port}}) 2>/dev/null; then \
       echo "Порт {{pg_port}} занят посторонним процессом (наш кластер не запущен)."; \
       exit 1; \
     fi; \
     "{{pg_local}}/Library/bin/pg_ctl" -D "{{pg_local}}/data" -l "{{pg_local}}/data/log.txt" start

pg-test-stop:
    "{{pg_local}}/Library/bin/pg_ctl" -D "{{pg_local}}/data" stop

# Поднять локальный кластер, если db_target=local (при db_target=env — no-op)
pg-ensure:
    @if [ "{{db_target}}" = "local" ]; then just pg-test-start; fi

# Создать локальную тестовую БД gca_test (идемпотентно)
db-test-init: pg-test-start
    @"{{pg_local}}/Library/bin/psql" -h 127.0.0.1 -p {{pg_port}} -U postgres -Atc \
      "select 1 from pg_database where datname='gca_test'" | grep -q 1 \
      || "{{pg_local}}/Library/bin/createdb" -h 127.0.0.1 -p {{pg_port}} -U postgres gca_test

# Integration против локального Postgres
test-int-local: pg-test-start
    cd backend && TEST_DATABASE_URL="{{test_db_local}}" uv run pytest tests/integration -v

# Точечный локальный прогон по -k паттерну
test-int-local-k pattern: pg-test-start
    cd backend && TEST_DATABASE_URL="{{test_db_local}}" uv run pytest tests/integration -v -k "{{pattern}}"

# Все backend-тесты против локального Postgres
test-backend-local: pg-test-start
    cd backend && TEST_DATABASE_URL="{{test_db_local}}" uv run pytest

# Параллельный прогон: pytest-xdist, у каждого воркёра своя база gca_gw<N>_test
# (создаётся фикстурой db_engine сама, предсоздание не нужно — спека §1.3a).
# Замер на 16 ядрах (devlog 2026-08-11, 1497 тестов): серийно 291,6 с,
# 4 воркёра — 194 с, 8 — 173 с, 12 — 151/141 с; дальше кривую держит серийный
# пол (module-scoped разбор оферт и БД-контенция), не число воркёров.
#
# ПЕРЕЗАМЕР 2026-09-02 (2401 тест): n=8 идёт 403–414 с. Тестов стало на 60 %
# больше, а время выросло в 2,6 раза, и рост непропорционален: профиль
# `--durations=30` показывает ~411 с установки фикстур против ~91 с тел тестов.
# Тест на 403-й код платит 10,6 с, потому что висит на цепочке, проводящей
# настоящий импорт сметы. Разбор предложен в
# docs/proposals/2026-09-02-backend-suite-fixture-cost.md.
#
# **Default опущен с 12 до 8** (2026-08-12, фича разноса): на n=12 прогон
# разваливается на `DROP SCHEMA public CASCADE` в фикстуре `db_engine` с
# `psycopg.errors.OutOfMemory: out of shared memory / HINT: increase
# max_locks_per_transaction` — 3 падения из 4 прогонов, 269 и 552 ошибки
# установки. n=8 зелёный дважды подряд (1497 passed, 154-157 с), то есть
# разница со «здоровым» n=12 в пределах нескольких секунд. Это симптом, а не
# причина: причина не найдена (арифметика её не объясняет — 12 × 224 объекта
# схемы = 2688 при таблице блокировок на 6400), см. docs/TECH_DEBT.md.
test-backend-parallel n="8": pg-test-start
    cd backend && TEST_DATABASE_URL="{{test_db_local}}" uv run pytest -n {{n}}

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

# Combined: backend + frontend
test:
    just test-backend
    just test-frontend

# Лок-файл backend соответствует pyproject (в CI это делает флаг --locked)
ci-lock-backend:
    cd backend && uv lock --check

# Цепочка проверок бэкенда. Порядок внутри цепочки — условие пользы, а не вкус:
# db-test-check стоит ДО тестов, потому что дрейф схемы ловится за секунды, а
# набор идёт почти семь минут; он же готовит gca_test, которая тестам всё равно
# нужна.
# ГРАНИЦА: `alembic check` сторожит состав колонок, типы и индексы, но НЕ сравнивает
# CHECK- и Computed-выражения — замерено на Ф1: при подмене обоих autogenerate
# возвращает пустой diff и лишь UserWarning. За выражения отвечают parity-тесты
# (test_schema_constraints.py), а не этот шаг.
# Backend-тесты в ci идут параллельно (test-backend-parallel, база на воркёра);
# серийный test-backend-local остаётся для отладки и воспроизводимого порядка.
#
# Цепочка проверок бэкенда: ruff → alembic check → pytest.
ci-backend: lint-backend db-test-check test-backend-parallel

# Цепочка проверок фронтенда: линт → типизация → тесты.
ci-frontend: lint-frontend typecheck-frontend test-frontend

# Полный прогон в форме CI. Обязателен перед пушем (§9.3).
#
# Две цепочки идут ПАРАЛЛЕЛЬНО: они независимы — бэкенд не знает о фронте, фронт
# не трогает Postgres. Замер 2026-09-02 (16 ядер): параллельно — 407 / 414 /
# 427 с (три прогона целиком); серийно — около 575 с СУММОЙ замеренных шагов
# (набор бэкенда 403–414 с, фронт-цепочка ~150 с, ruff + alembic check + лок
# ~18 с), одним прогоном серийная сумма не мерилась.
#
# ЦЕНА ЭТОГО РЕШЕНИЯ, замеренная: параллельность повышает частоту отказа
# `out of shared memory / max_locks_per_transaction` в фикстуре `db_engine` —
# один красный прогон из пяти против зелёного одиночного набора на том же
# коммите. Класс отказа известен и НЕ объяснён (docs/TECH_DEBT.md запись 1,
# наблюдение 03.09.2026). Если `ci` упал сотнями ошибок на этапе setup — это
# он, а не ваш код: перезапустите. GitHub CI не затронут, там цепочки — в
# отдельных job'ах.
#
# Отсюда следует, чего НЕ надо оптимизировать: фронт-цепочка целиком уходит в
# тень бэкенд-набора, поэтому ускорение eslint, tsc и vitest не сокращает `ci`
# ни на секунду. Под конкуренцией с pytest фронт-цепочка идёт даже медленнее
# (118 с против 92 с сольно) — и это ничего не меняет, пока она короче
# бэкенда.
#
# Шаги остались ОТДЕЛЬНЫМИ рецептами, и это условие корректности, а не стиль:
# составная команда в фазе 5 уже скрыла падение типизации (AGENTS.md §11).
# Здесь то же требование выполнено на новом месте — каждая цепочка запущена в
# подоболочке с `pipefail`, поэтому код возврата берётся у `just`, а НЕ у `sed`,
# который помечает строки префиксом. Без `pipefail` падение цепочки утонуло бы
# в нулевом коде `sed` — ровно тот класс ошибки, от которого стоит §11.
#
# ci-lock-backend идёт ДО развилки: он секундный, и ловить расхождение лока
# после семи минут тестов незачем.
#
# Все проверки: бэкенд- и фронт-цепочки параллельно. Обязателен перед пушем.
ci: ci-lock-backend
    @( set -o pipefail; just ci-backend 2>&1 | sed -u 's/^/[be] /' ) & pid_be=$!;\
     ( set -o pipefail; just ci-frontend 2>&1 | sed -u 's/^/[fe] /' ) & pid_fe=$!;\
     rc_be=0; rc_fe=0;\
     wait $pid_be || rc_be=$?;\
     wait $pid_fe || rc_fe=$?;\
     echo;\
     if [ $rc_be -eq 0 ]; then echo "backend:  OK"; else echo "backend:  ПАДЕНИЕ (код $rc_be)"; fi;\
     if [ $rc_fe -eq 0 ]; then echo "frontend: OK"; else echo "frontend: ПАДЕНИЕ (код $rc_fe)"; fi;\
     if [ $rc_be -ne 0 ] || [ $rc_fe -ne 0 ]; then exit 1; fi;\
     echo "OK: все проверки прошли"

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
db-revision message:
    cd backend && uv run alembic revision -m "{{message}}"

db-migrate: pg-ensure
    @echo "==> БД: {{db_target}}"
    cd backend && {{db_env}} uv run alembic upgrade head

# Накатить миграции на локальную тестовую БД (gca_test)
db-test-migrate: pg-test-start db-test-init
    cd backend && DATABASE_URL="{{test_db_local}}" uv run alembic upgrade head

# Проверка дрейфа ORM/БД: локальная тест-БД до head + alembic check.
db-test-check: pg-test-start db-test-init
    cd backend && DATABASE_URL="{{test_db_local}}" uv run alembic upgrade head
    cd backend && DATABASE_URL="{{test_db_local}}" uv run alembic check

# Создать локальную dev-БД gca_dev и накатить миграции (идемпотентно)
db-dev-init: pg-test-start
    @"{{pg_local}}/Library/bin/psql" -h 127.0.0.1 -p {{pg_port}} -U postgres -Atc \
      "select 1 from pg_database where datname='gca_dev'" | grep -q 1 \
      || "{{pg_local}}/Library/bin/createdb" -h 127.0.0.1 -p {{pg_port}} -U postgres gca_dev
    cd backend && DATABASE_URL="{{dev_db_local}}" uv run alembic upgrade head

pgweb := "/c/dev-cache/pgweb/pgweb.exe"

# Веб-просмотр таблиц локального кластера на http://localhost:8081
db-web: pg-test-start
    @test -f "{{pgweb}}" || { echo "pgweb не установлен"; exit 1; }
    "{{pgweb}}" --sessions --bind localhost --listen 8081

# === Misc ===

# Создать пользователя (роль: admin | member; интерактивный ввод пароля)
create-user email role="member": pg-ensure
    cd backend && {{db_env}} uv run python -m cli create-user --email {{email}} --role {{role}}

clean:
    rm -rf backend/.pytest_cache backend/htmlcov backend/.coverage backend/coverage.xml
    find backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
