# 2026-07-21 — Миграция backend pip → uv (project mode)

**Ветка:** `build/uv-migration` (от `main`)
**PR:** [#42](https://github.com/zhukovvlad/udp-tenders/pull/42)
**Метод:** subagent-driven-development (Opus-оркестратор + ревьюер; Sonnet на содержательных задачах 1–3, Haiku на механике 4–5; python-reviewer на Task 1, ручная сверка пина на Task 3, Fable — финальный whole-branch; правки — диффом)
**Спека:** `docs/superpowers/specs/2026-07-21-uv-migration-design.md`
**План:** `docs/superpowers/plans/2026-07-21-uv-migration.md`

## Задача

Перевести backend с pip + плоских `requirements*.txt` на **uv (project mode)**: зависимости в `pyproject.toml`, воспроизводимость через `uv.lock`, изолированный `backend/.venv`, Python 3.12 для dev и CI. Прикладной код не трогаем — только tooling и доки.

## Что сделано (6 коммитов)

1. **pyproject + двухпроходный lock** (`690e6fc`): секции `[project]` / `[dependency-groups].dev` / `[tool.uv] package = false`, `backend/.python-version = 3.12`, `requires-python = "==3.12.*"`. Lock снят двухпроходно (`==` → `uv lock` → `>=` → `uv lock`), чтобы прямые версии не поехали: все 28 прямых зависимостей в `uv.lock` = исходному freeze-листу, включая security-фиксы (`python-multipart>=0.0.31`, `pillow>=12.3.0`, `python-dotenv>=1.2.2`, `pydantic-settings>=2.14.2`). Extras сохранены (`uvicorn[standard]`, `psycopg[binary]`, `pwdlib[argon2]`, `pydantic[email]`). `rapidfuzz` **не перенесён** — мёртвая зависимость (0 импортов); если понадобится под RP-2, добавить явно.
2. **justfile → uv** (`b11fd8b`): все backend-рецепты через `uv run` / `uv sync`; `install-backend` → `uv sync`; удалён сломанный `test-backend-watch` (ptw не в зависимостях). Env-vars и `{{…}}`-интерполяции сохранены байт-в-байт; комментарий-инвариант S1 над `dev-backend` не тронут.
3. **CI на uv** (`790b4e3`, `.github/workflows/backend-tests.yml`): `astral-sh/setup-uv` с SHA-пином `11f9893…` `# v8.3.2` (immutable-релиз), `version: "0.11.29"`, `enable-cache: true`; `uv sync --locked` + `uv run --locked ruff/pytest`; все uv-шаги с `working-directory: backend`; `actions/setup-python` убран (Python приезжает из `.python-version`). Блок `env:` и `services.postgres` (pgvector:pg16) не тронуты.
4. **Удаление requirements + игнор venv** (`f7726f9`): `git rm backend/requirements.txt backend/requirements-test.txt`; `/backend/.venv/` в `.gitignore`.
5. **Доки** (`da0c791`): README/AGENTS/testing синхронизированы (Python 3.12, uv, pytest 9); append-заметка в датированном security-аудите (историю не переписывали).
6. **Доку-фикс из финального ревью** (`490631d`): `pytest --co` → `uv run pytest --co` в живом доке.

## Верификация

- **Ревью:** 5 задач приняты пофайлово (python-reviewer на Task 1 — 0 находок, версии не поехали; ручная сверка пина/`working-directory` на Task 3). Финал (Fable, whole-branch) — кросс-задачные швы (pyproject ↔ justfile ↔ CI ↔ docs) консистентны, **Ready to merge: Yes**, 0 Critical/Important, 2 Minor.
- **Приёмка локально:** `just install` PASS, `just lint` PASS (ruff + eslint), `just test` PASS — backend **584 passed / 6 skipped** (28.55с, локальный PG :5433), frontend **219 passed** (28 файлов).
- **CI на PR:** `backend-tests` **PASS (43с)** — валидирует весь uv-workflow в раннере; `frontend-tests` PASS; CodeRabbit PASS.

## Инцидент: «зависший» `just test` (разобран — НЕ баг миграции)

Первый прогон `just test 2>&1 | tail -40` завис на 600с с **0 байт** вывода. Root cause (подтверждён процессными уликами, systematic-debugging): локальный PG-каталог существует → `test-backend` уходит в ветку `test-backend-local` → `pg-test-start` выполняет `pg_ctl … start`, поднимая **постоянный демон** `postgres.exe` (:5433). На Windows демон наследует пишущий конец пайпа `| tail`; pytest отработал и `just` вышел, но демон держит пайп открытым → `tail` никогда не получает EOF → вечное ожидание. Убийство `tail` мгновенно схлопнуло пайплайн (`exit 0`) — прямое доказательство. Единственная миграционная правка в цепочке (`pytest` → `uv run pytest`) на взаимодействие «демон ↔ пайп» не влияет — воспроизвелось бы и до миграции.

**Вывод на будущее:** на этой машине не гнать `just test` через `| tail`/`| tee` — либо `just pg-test-start` заранее (идемпотентно) + `just test` напрямую, либо редирект в файл (`just test > run.log 2>&1`, файловый редирект не ждёт EOF). Зафиксировано в памяти агента.

## Follow-up по ревью CodeRabbit (PR #42)

- **Major (security-audit):** приоритет-инструкция «bump 4 пакетов в requirements.txt» вела к удалённому файлу — переписана на «backend зафиксирован в `pyproject.toml`/`uv.lock` ✅ + фронт `npm audit fix`». Датированные findings §2 (что и когда исправлено в requirements.txt на 2026-07-21) оставлены как исторический срез — append-заметка о миграции их уже примиряет.
- **Minor (testing.md):** метрики в TL;DR были устаревшими и путали collection с executed. Обновлено на реальные числа: backend 48 файлов / 590 собрано (584 passed / 6 skipped), frontend 28 / 219 passed.
- **Nitpick (testing.md):** ручная snapshot-команда `cd backend && uv run python scripts/…` нарушала правило «только через just» — добавлен рецепт `just snapshot-ai <pdf> <scenario>`, док переведён на него.
- **Доп. (не от ревьюера):** README говорил «React 18» при фактическом `react@^19.2.5` — исправлено на «React 19» (живая инструкция; исторические планы/спеки с «React 18» — датированные снимки, не трогаем).

## Замечания

- Локальный `uv` на машине = `0.11.22`; для генерации lock ок, CI-пин `0.11.29` — отдельно (совместимый формат lock).
- Изоляция worktree не использовалась (работали в текущей ветке — по указанию).
- **Осталось:** merge PR #42 — решение пользователя.
