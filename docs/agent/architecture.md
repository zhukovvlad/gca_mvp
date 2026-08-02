# Архитектура и структура

Полный дизайн маршрутов/навигации — `docs/ui/routes-architecture.md`. Здесь — карта кода и ключевые принципы.

## Структура проекта

```
UDP/
├── backend/
│   ├── main.py           — FastAPI app, CORS, routers, middleware, CSRF middleware
│   ├── config.py         — pydantic-settings Settings (единый источник env-переменных)
│   ├── models.py         — ORM-модели (Project, Document, Invoice, InvoiceItem, MaterialClass,
│   │                       MaterialType, UnitOfMeasure, UnitAlias, ReferencePrice,
│   │                       CompensationCorridor, Supplier, Organization, User,
│   │                       ProjectOrganization, RefreshToken, ProjectSupplierExclusion)
│   ├── crud/             — операции с БД:
│   │   ├── projects.py   — Project + ReferencePrice CRUD
│   │   ├── materials.py  — MaterialClass CRUD + VALID_CALC_ROLES + код↔id типов материалов
│   │   ├── units.py      — нормализация единиц (normalize_unit_key, normalize_item,
│   │   │                   load_alias_map, item_has_issues) + seed-данные справочников
│   │   ├── documents.py  — Document + Invoice CRUD (нормализация единиц при записи)
│   │   ├── calculations.py — avg_price, deviation, dimension guard, export-строки
│   │   ├── compensation_corridors.py — коридоры компенсации (резолвер по material_type_id)
│   │   ├── suppliers.py  — Supplier CRUD + аналитические агрегаты
│   │   ├── supplier_exclusions.py — get_excluded_supplier_ids, set_supplier_excluded
│   │   └── admin.py      — админ-консоль суперюзера: orgs, users, project links,
│   │                       last-superadmin guard, матрица ролей
│   ├── security.py       — чистая крипта: hash_password, JWT encode/decode, refresh, CSRF
│   ├── auth.py           — FastAPI auth-зависимости (get_current_user, require_csrf,
│   │                       require_superuser, require_org_admin*, ProjectAccess)
│   ├── cli.py            — Click CLI: create-superuser, create-org, create-user
│   ├── llm.py            — абстракция LLM-провайдера: типы интерфейса + service locator
│   │                       (get/init/reset_provider); переключатель LLM_PROVIDER
│   ├── llm_openrouter.py — OpenRouterProvider: транспорт/envelope OpenRouter за интерфейсом llm.py
│   ├── pdf_parser.py     — парсинг УПД (фаза A через llm.get_provider(); доменный разбор JSON)
│   ├── s3.py             — MinIO-хелперы
│   ├── utils.py          — get_client_ip, utcnow
│   ├── routers/          — projects, invoices, dashboard, export, material_classes,
│   │                       reference_prices, units (+ /api/material-types), settings,
│   │                       suppliers, auth, admin, orgs;
│   │                       common.py — shared-хелперы роутеров (resolve_direction_type → 422)
│   ├── alembic/          — миграции
│   └── tests/            — unit/ + integration/ + fixtures/ + test_auth_coverage.py
├── frontend/src/
│   ├── pages/            — Dashboard, Projects, ProjectPage, Suppliers, SupplierPage,
│   │                       Materials, Reports, Review, Settings, admin/, handbook/
│   ├── components/       — ui/, ui-domain/, layout/, dashboard/, projects/, invoices/,
│   │                       review/, admin/, handbook/
│   ├── lib/             — format, constants, utils, useDebounce, password
│   ├── services/         — api/ (axios), queries.ts (TanStack Query), queryKeys.ts
│   └── types/            — TS-типы по доменам
├── docs/
│   ├── TECH_DEBT.md      — трекинг долга (свериться перед правкой связанного кода)
│   ├── testing.md        — архитектура тестов, покрытие, как добавлять
│   ├── agent/            — справочники для агентов (этот набор)
│   └── ui/routes-architecture.md  — полный дизайн маршрутов
├── .github/workflows/backend-tests.yml — CI: ruff + полный pytest на каждый push/PR
│                       (Postgres c pgvector как service-container; ~1 мин)
└── justfile
```

## Принципы навигации (из дизайна маршрутов)

- **Entity-oriented navigation**: в навигации сначала бизнес-сущности — Объекты, Поставщики, Номенклатура, Отчёты, Справочник. Технических маршрутов в основном меню нет.
- **Три оси данных**: любая точка данных достижима через Проект, Поставщика или Материал.
- **Загрузка — slide-over панель** (`Sheet` из shadcn) внутри страницы проекта, не отдельный маршрут. `/upload` редиректит на `/projects`.
- **Нет списка `/documents` в навигации** — инвойсы доступны через родительскую сущность или Отчёты. Проблемные документы — через таб «Ошибки» в `ProjectPage` (`ErrorDocsTab.tsx`): фильтрует `useDocuments(projectId)` по `status === "error" || has_issues`, действия по строке (открыть PDF, разбор `/documents/:id`, переразбор, удаление).
- **Базовые цены** живут внутри карточки проекта (таб) и карточки материала — без отдельного маршрута.
- **Progressive disclosure** — проценты AI-confidence спрятаны в тултипах; технический слой полностью раскрывается в карточке документа.

## Чего ещё нет в кодовой базе (планы)

- E2E-тесты (Playwright) — спека написана, не реализована.
- Org-level изоляция данных — auth есть, но запросы проектов/инвойсов ещё не фильтруют по org (см. `docs/TECH_DEBT.md`).
- Сброс пароля / верификация email — нет.
- Прод-деплой — планируется.
- Поставщики: таб «Сравнение» (бенчмарк рынка) — бэклог, нужно ≥3 поставщика на класс.
- Поставщики: экспорт Excel/PDF — кнопка-заглушка.
