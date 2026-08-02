# Спецификация: Переработка UI под routes-architecture.md

**Дата:** 2026-05-12  
**Ветка:** `feat/ui-redesign`  
**Подход:** Вариант 1 — поэтапно по слоям. Каждый этап даёт рабочий результат.  
**Стиль:** текущая дизайн-система без изменений (CSS-переменные, shadcn/ui, Tailwind)  
**Чарты:** Recharts через shadcn/ui chart (recharts уже установлен, компонент chart нужно добавить)

---

## Контекст

Текущие роуты и навигация построены вокруг технических процессов (загрузка, эталоны, классы материалов). Цель переработки — сущностно-ориентированная навигация: объекты, поставщики, номенклатура. Подробная архитектура описана в `docs/ui/routes-architecture.md`.

Новые страницы для поставщиков и детальных карточек материалов требуют backend-эндпоинтов, которых ещё нет. Фронтенд реализуется полностью согласно архитектуре; где данных нет — пустые состояния и заглушки.

---

## Текущее состояние

**Роуты:** `/`, `/upload`, `/projects`, `/projects/:id`, `/material-classes`, `/reference-prices`, `/reports`, `/settings`, `/documents/:id`

**Навигация (7 пунктов):** Дашборд, Загрузка, Объекты, Классы материалов, Эталоны, Отчёты, Настройки

**Shadcn-компоненты, которых нет:** `chart`, `sheet` — нужно добавить через `npx shadcn add chart sheet`

---

## Целевое состояние

**Роуты:** `/dashboard`, `/projects`, `/projects/:id`, `/suppliers`, `/suppliers/:id`, `/materials`, `/materials/:id`, `/reports`, `/documents/:id`, `/settings`

**Навигация (5 пунктов):** Дашборд, Объекты, Поставщики, Номенклатура, Отчёты  
**Настройки:** через аватар пользователя (DropdownMenu) в правом углу шапки  
**Redirect:** `/` → `/dashboard`

---

## Этап 1 — Инфраструктура (навигация и роуты)

### TopNav

Переработать `frontend/src/components/layout/TopNav.tsx`:

- Убрать пункты: Загрузка (`/upload`), Классы материалов (`/material-classes`), Эталоны (`/reference-prices`)
- Добавить пункты: Поставщики (`/suppliers`), Номенклатура (`/materials`)
- Переименовать `end: true` на `/` → теперь привязан к `/dashboard`
- В правой части шапки: иконка поиска (stub), иконка уведомлений (stub), аватар-кнопка с `DropdownMenu` содержащим «Настройки» → `/settings` и «Выйти» (stub)

Итоговый массив NAV (5 пунктов):
```text
{ to: "/dashboard", icon: LayoutDashboard, label: "Дашборд", end: true }
{ to: "/projects",  icon: Building2,       label: "Объекты" }
{ to: "/suppliers", icon: Users,           label: "Поставщики" }
{ to: "/materials", icon: Layers,          label: "Номенклатура" }
{ to: "/reports",   icon: FileSpreadsheet, label: "Отчёты" }
```

### App.tsx — роуты

```text
/                       → <Navigate to="/dashboard" replace />
/dashboard              → <Dashboard />          (новая страница)
/projects               → <Projects />           (без изменений)
/projects/:id           → <ProjectPage />        (переработать с табами)
/suppliers              → <Suppliers />          (новая страница)
/suppliers/:id          → <SupplierPage />       (новая страница)
/materials              → <Materials />          (переименовать MaterialClasses)
/materials/:id          → <MaterialPage />       (новая страница)
/reports                → <Reports />            (без изменений)
/documents/:id          → <Review />             (без изменений)
/settings               → <SettingsPage />       (без изменений)

/upload                 → <Navigate to="/projects" replace />
/material-classes       → <Navigate to="/materials" replace />
/reference-prices       → <Navigate to="/projects" replace />
```

---

## Этап 2 — Дашборд (`/dashboard`)

**Файл:** `frontend/src/pages/Dashboard.tsx` — полная переработка.

Текущий дашборд (фильтр + расчёт отклонений) заменяется кросс-портфельной витриной. Логика расчёта отклонений переезжает в `ProjectPage` (таб «Обзор»).

### Структура страницы

**Строка-переключатель периода** (правый верхний угол): 7 дн. / 30 дней / Квартал / Год — локальный state, фильтрует данные на фронтенде где возможно.

**KPI-строка (3 карточки):**
- «Переплата к базовым» — сумма `deviation_amount` из `GET /dashboard/calculations` (без project_id фильтра). Если нет данных — «—».
- «Оборот» — сумма `material_total + delivery_total` из тех же расчётов.
- «Требуют внимания» — счётчик документов с `has_issues: true` из `GET /api/invoices/documents` (без фильтра). Клик → `/reports`.

**Динамика цен (LineChart через shadcn chart):**  
Данные: агрегация `avg_price` по `period_start` из `GET /dashboard/calculations` без фильтра, группировка по `material_class_name`. Линия на каждый класс материала. Если расчётов нет — empty state «Рассчитайте отклонения по объектам, чтобы увидеть динамику».

**Топ поставщиков:**  
Заглушка — empty state с текстом «Аналитика по поставщикам появится здесь». Готовая структура компонента для будущего backend.

**Лента «Требуют внимания»:**  
Список документов с `has_issues: true` из `GET /api/invoices/documents`. Каждая строка: иконка предупреждения, имя файла, дата, ссылка на `/documents/:id`.

### API-запросы
- `GET /dashboard/calculations` (без query-параметров) — все расчёты
- `GET /api/invoices/documents` (без query-параметров) — все документы для подсчёта проблемных

---

## Этап 3 — Карточка объекта (`/projects/:id`)

**Файл:** `frontend/src/pages/ProjectPage.tsx` — переработка с добавлением табов и slide-over.

### Шапка страницы
Хлебные крошки, название (serif), номер договора + дата. Кнопки: «Экспорт» (открывает диалог из текущего `Reports.tsx`) и «+ Добавить счёт» (открывает Sheet).

### Табы (shadcn `Tabs` компонент)

**Таб «Обзор»** (по умолчанию):

Баннер-вердикт (если есть расчёты):
- Красный/жёлтый фон в зависимости от суммарного `deviation_amount`
- Текст: «Переплата по объекту: +N ₽ (+X%)» + кнопка «Разобрать →» → скролл к таблице расчётов
- Если экономия — зелёный баннер «Экономия»

KPI-строка (4 карточки): Оборот, Объём м³, Счетов, К проверке — из `GET /dashboard/summary?project_id=`.

Блок расчёта отклонений (переезжает из старого Dashboard):
- Выбор периода (даты) + кнопка «Рассчитать» → `POST /dashboard/calculate`
- Кнопка «Пересчитать авто» → `POST /dashboard/auto-calculate`
- Список отклонений из `GET /dashboard/calculations?project_id=`

BarChart с центральной осью (Recharts):
- Ось X: классы материалов
- Ось Y: `deviation_pct`
- Положительные бары — красный/жёлтый, отрицательные — зелёный
- Референсная линия Y=0 («базовая цена»)
- Класс без базовой цены — отдельная метка «настроить →» ссылкой на таб «Базовые цены»

**Таб «Счета»:**

Поиск по номеру/поставщику + таблица из `InvoiceTable`. Данные: `GET /dashboard/invoices?project_id=`. Существующий код переносится без изменений.

**Таб «Базовые цены»:**

Содержимое текущей страницы `ReferencePrices.tsx`, отфильтрованное по `project_id`. Фильтр по объекту убирается (он известен из контекста). CRUD (добавить/удалить) работает через существующие API.

**Таб «Поставщики»:**

Таблица уникальных поставщиков объекта. Вычисляется на фронтенде из данных `GET /dashboard/invoices?project_id=` — группировка по `supplier_name + supplier_inn`, подсчёт кол-ва счетов. Колонки: Поставщик, ИНН, Счетов, Оборот (сумма amount по позициям). Ссылка в строке → `/suppliers/:slug` (stub).

### Slide-over загрузки (Sheet)

Открывается кнопкой «+ Добавить счёт» и drag & drop на страницу. Реализуется через shadcn `Sheet` (position=right, width=480px).

Содержимое: `Dropzone` компонент (уже существует), список загруженных файлов с индикатором прогресса. Upload через существующий `POST /upload` с `project_id` из контекста страницы. После успеха: Sheet закрывается, инвалидируются queries `dashboard/invoices` и `dashboard/summary`.

---

## Этап 4 — Поставщики (`/suppliers`, `/suppliers/:id`)

### `/suppliers` — Реестр

**Файл:** `frontend/src/pages/Suppliers.tsx` (новый)

Данные: `GET /api/invoices/documents` (без фильтра) → агрегация на фронтенде по `invoices[].supplier_name` / `invoices[].supplier_inn`.

Таблица: Поставщик, ИНН, Документов, Оборот*. Сортировка по умолчанию — по кол-ву документов.

*Оборот — stub (нет API для агрегации по поставщику без project_id). Колонка «Оборот» показывает «—» с тултипом «Данные скоро появятся».

Поиск по названию/ИНН. Клик на строку → `/suppliers/:slug`, где slug = URL-encoded `supplier_name`.

### `/suppliers/:id` — Карточка

**Файл:** `frontend/src/pages/SupplierPage.tsx` (новый)

Шапка: название поставщика (из URL-decode параметра), ИНН если есть.

Табы: «Обзор», «Счета», «Объекты», «Сравнение» — все с empty state «Подробная аналитика по поставщику будет доступна после обновления сервиса». Кости для будущего backend.

---

## Этап 5 — Номенклатура (`/materials`, `/materials/:id`)

### `/materials` — Реестр

**Файл:** `frontend/src/pages/Materials.tsx` (переименование от `MaterialClasses.tsx`)

Функционально идентична текущей `MaterialClasses.tsx`. Изменения:
- Роут `/material-classes` → `/materials`
- Заголовок «Классы материалов» → «Номенклатура»
- Строки таблицы кликабельны → `/materials/:id`

### `/materials/:id` — Карточка

**Файл:** `frontend/src/pages/MaterialPage.tsx` (новый)

Шапка: название класса (из `GET /material-classes`, фильтр по id), тип материала.

Табы:
- «Обзор» — stub, empty state
- «Поставщики» — stub, empty state
- **«Базовые цены»** — работает сразу: таблица из `GET /reference-prices` без фильтра, отфильтрованная по `material_class_id`. Колонки: Объект, Базовая цена, Период. Только просмотр (редактирование — в карточке объекта).
- «Объекты» — stub, empty state

---

## Новые файлы

| Файл | Описание |
|------|----------|
| `src/pages/Dashboard.tsx` | Переработка существующего — кросс-портфельная витрина |
| `src/pages/Suppliers.tsx` | Новый — реестр поставщиков |
| `src/pages/SupplierPage.tsx` | Новый — карточка поставщика (stub) |
| `src/pages/Materials.tsx` | Переименование MaterialClasses |
| `src/pages/MaterialPage.tsx` | Новый — карточка материала |
| `src/components/projects/UploadSheet.tsx` | Новый — slide-over загрузки |
| `src/components/projects/DeviationChart.tsx` | Новый — BarChart отклонений (Recharts) |
| `src/components/dashboard/PriceChart.tsx` | Новый — LineChart динамики цен (Recharts) |
| `src/components/ui/chart.tsx` | Shadcn chart (добавить через CLI) |
| `src/components/ui/sheet.tsx` | Shadcn sheet (добавить через CLI) |

## Изменяемые файлы

| Файл | Изменение |
|------|-----------|
| `src/App.tsx` | Новые роуты, редиректы |
| `src/components/layout/TopNav.tsx` | 5 пунктов + аватар с меню |
| `src/pages/ProjectPage.tsx` | Добавить 4 таба + slide-over |
| `src/pages/ReferencePrices.tsx` | Убрать — логика переезжает в ProjectPage |
| `src/pages/MaterialClasses.tsx` | Убрать — заменяется Materials.tsx |
| `src/pages/Upload.tsx` | Убрать — заменяется UploadSheet.tsx |

---

## Зависимости

- `recharts` — уже установлен (v3.8.0)
- `shadcn add chart` — нужно выполнить
- `shadcn add sheet` — нужно выполнить
- `shadcn add dropdown-menu` — проверить наличие (нужен для аватара)

---

## Что остаётся как stub (backend нужен позже)

| Функция | Где | Что нужно от backend |
|---------|-----|----------------------|
| Полный профиль поставщика | `/suppliers/:id` | Эндпоинт с агрегацией по supplier_name |
| Оборот в реестре поставщиков | `/suppliers` | То же |
| История цен по материалу | `/materials/:id` таб «Обзор» | Агрегация avg_price по material_class_id по датам |
| Список объектов материала | `/materials/:id` таб «Объекты» | JOIN documents + invoice_items |
| Лента событий «Требуют внимания» | `/dashboard` | Структурированные события (сейчас — только documents с has_issues) |
| Глобальный поиск (Cmd+K) | TopNav | Поиск по всем сущностям |
| Уведомления (колокольчик) | TopNav | Эндпоинт событий |
