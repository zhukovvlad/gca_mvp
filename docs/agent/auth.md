# Аутентификация и роли

- **Transport**: httpOnly cookies — `access_token` (Path=/), `refresh_token` (Path=/api/auth), `csrf_token` (читается JS, Path=/).
- **CSRF**: double-submit cookie — CSRF middleware в `main.py` + зависимость `require_csrf`. Exempt: login, `/docs`, `/openapi.json`.
- **JWT**: HS256, 30 мин. Payload: `sub` (user id), `org_id`, `is_superuser`, `org_role`, `exp`, `iat`, `jti`.
- **Refresh-токены**: в БД (`refresh_tokens`), хешированы (SHA-256), 14 дней, отзываемые. Ротация на каждый `/api/auth/refresh`.
- **Роли**: `OrgRole` (superadmin / admin / member) на организацию; `ProjectRole` (customer / contractor) на связь с проектом. Первый юзер новой org автоматически `superadmin` (в `crud.admin.create_user_in_org`).

## Матрица ролей (в guard'ах/CRUD, не только в UI)

Внутри организации: `superadmin` управляет admin+member и назначает admin/member; `admin` управляет только member и назначает только member; `member` — ничего. Роль `superadmin` назначается **только** через `/api/admin` (платформенным `is_superuser`).

Хелперы `can_set_role` / `can_manage_target` в `crud.admin`; guard `require_org_superadmin` в `auth.py`. **Последний активный superadmin организации защищён**: нельзя деактивировать/понизить (4xx) — проверка в `crud.admin.set_user_role_and_active`, **атомарная** через `SELECT ... FOR UPDATE` на строках superadmin'ов (`_count_other_active_superadmins_locked`), чтобы два параллельных запроса не сняли последнего. Платформенный `is_superuser` может всё во всех org через `/api/admin/*` и не подчиняется матрице.

## Эндпоинты

- **Auth**: `POST /api/auth/login`, `/refresh`, `/logout`; `GET /api/auth/me` (отдаёт `organization.kind`).
- **Superuser-консоль** (`routers/admin.py`, под `require_superuser`): `GET/POST /api/admin/organizations`, `GET/PATCH /api/admin/organizations/{id}` (вкл. `kind`), `POST /api/admin/organizations/{id}/users` (первый = superadmin), `GET /api/admin/users` (пагинация `q`/`page`/`page_size` → `{items, total, page, page_size}` + `org_name` — **не голый массив**), `PATCH /api/admin/users/{id}` (role/active + защита последнего superadmin → 409), `POST /api/admin/users/{id}/reset-password` (сервер генерит пароль через `secrets`, plaintext один раз), `POST /api/admin/organizations/{id}/projects` (project_role default = `kind`), `DELETE .../projects/{project_id}`.
- **Org self-service** (`routers/orgs.py`, под `require_org_admin_with_org`): `GET/POST /api/orgs/users`, `PATCH /api/orgs/users/{id}` — по матрице.
- **Frontend**: маршруты `/admin/*` под guard `RequireSuperuser` (экспорт из `App.tsx`); пункт «Админ» в `TopNav` виден только суперюзеру.

## Прочее

- **Все бизнес-роутеры** требуют `get_current_user`. Org-level изоляция данных для запросов проектов/инвойсов **ещё не включена** (см. `docs/TECH_DEBT.md`) — auth лишь не пускает неаутентифицированных.
- **CLI**: `just create-superuser` / `just create-org` — бутстрап первых юзеров.
- **Auth coverage guardian**: `tests/test_auth_coverage.py` бьёт каждый маршрут без токена, ждёт 401/403. Входит в `just test-backend` (лежит вне `tests/unit`, поэтому `test-backend-unit` его НЕ запускает).
