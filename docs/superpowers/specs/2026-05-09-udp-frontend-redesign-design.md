# UDP Frontend Redesign — Design Spec

**Дата:** 2026-05-09
**Проект:** UDP (УПД Трекер)
**Источник вдохновения:** [zhukovvlad/kpi-tenders-react](https://github.com/zhukovvlad/kpi-tenders-react)
**Скоуп:** Полный перенос дизайн-системы и UX-паттернов из kpi-tenders-react во фронтенд UDP. Авторизация и интеграция бэкенда — вне скоупа.

---

## 1. Контекст и цель

Текущий фронтенд UDP — голый shadcn в нейтральных серых тонах: header + контейнер, страницы построены из `Card { CardHeader + CardContent }`, всё в одной плотности, без характерного визуального языка. Загрузка/ошибки/пустота не оформлены, нет тостов, нет тем, нет react-query.

В соседнем проекте `kpi-tenders-react` уже отстроена зрелая дизайн-система: семантические токены (шалфей+антрацит+бежевый), темы light/dark, hairline-бордеры 0.5px, serif-заголовки (Cormorant Garamond), слой `ui-domain/` поверх shadcn (`PageHeader`, `KpiCard`, `StatusPill`, `EmptyState`, `Surface`, `Tabs`, `Breadcrumbs`, `Button`), AppShell с TopNav и темопереключателем, `react-query` для данных, `sonner` для тостов.

**Цель:** перенести этот язык 1:1 в UDP, переписать все страницы под новые компоненты, выровнять продукты визуально и по UX-паттернам. После редизайна фронтенд UDP должен ощущаться как родственник kpi-tenders, а не как другой продукт.

**Не цель:** менять backend, добавлять авторизацию, вводить i18n, переходить на Next/App Router, добавлять стейт-менеджер сверх react-query, писать юнит-тесты на компоненты.

---

## 2. Архитектура и стек

### 2.1 Новые зависимости

| Пакет | Назначение |
|---|---|
| `@tanstack/react-query` | Кэширование, фон-ревалидация, скелетоны, ретраи |
| `next-themes` | Переключатель light/dark через `data-theme` атрибут |
| `sonner` | Тосты для успехов/ошибок/предупреждений |
| `react-dropzone` | Drag-n-drop загрузка файлов на странице Upload |
| `@fontsource/cormorant-garamond` | Шрифт для serif-заголовков |

### 2.2 Удаляемые зависимости

Нет. Изначально планировалось удалить `@base-ui/react`, но при ревизии оказалось, что 11 shadcn-примитивов в `components/ui/*` используют его как primitive-слой (button, dialog, dropdown-menu, navigation-menu, select, switch, slider, separator, badge, tabs, input). Удаление сломало бы все эти компоненты. Оставляем `@base-ui/react` как primitive-слой (роль аналогична `radix-ui` в kpi-tenders).

### 2.3 Структура `frontend/src/`

```
src/
├── App.tsx                       # провайдеры + роуты
├── main.tsx
├── index.css                     # дизайн-токены (полная замена)
├── lib/
│   ├── api.ts                    # axios instance (есть)
│   ├── format.ts                 # formatPercent, formatRelative, formatMoney (новое)
│   └── utils.ts                  # cn (есть)
├── services/api/
│   ├── projects.ts
│   ├── invoices.ts
│   ├── materialClasses.ts
│   ├── referencePrices.ts
│   ├── dashboard.ts
│   ├── reports.ts
│   └── settings.ts
├── types/                        # доменные TS-типы
├── components/
│   ├── ui/                       # shadcn-примитивы (есть, не трогаем)
│   ├── ui-domain/                # НОВОЕ: PageHeader, KpiCard, StatusPill,
│   │                             # EmptyState, Surface, Tabs, FilterPill,
│   │                             # Breadcrumbs, Button, MoneyCell,
│   │                             # DeviationCell, ConfidenceBadge, Dropzone,
│   │                             # Skeleton
│   ├── layout/                   # AppShell, TopNav, Logo, ThemeToggle
│   └── <domain>/                 # ProjectCard, InvoiceTable и т.п.
└── pages/                        # переписываются под новый язык
```

### 2.4 Принципы

- Все компоненты говорят на семантических токенах (`bg-surface`, `text-fg-secondary`, `border-border-subtle`), а не на сырых цветах.
- Слой `ui/` (shadcn-примитивы) минимально модифицируем — оверрайды через токены `--color-*`. Своё пишем в `ui-domain/`.
- Каждая страница строится по шаблону `<PageHeader> + фильтры + контент (Surface/таблица/грид) + EmptyState`.
- Никаких axios-вызовов из компонентов — только через `services/api/*` + react-query.

---

## 3. Дизайн-токены и темы

### 3.1 Поверхности

| Токен | Light | Dark |
|---|---|---|
| `--bg-page` | `#F4F2EC` (тёплый бежевый) | `#1A1D24` |
| `--bg-surface` | `#FFFFFF` | `#232730` |
| `--bg-surface-sunken` | `#F7F6F2` | `#1F232B` |
| `--bg-surface-hover` | `#FAFAF7` | `#262B35` |
| `--bg-section-header` | `#FAFAF7` | `#1C1F26` |

### 3.2 Текст

4 уровня: `--text-primary` → `--text-secondary` → `--text-tertiary` → `--text-muted`.
Light: `#1F2128 / #5A5D66 / #8E8B82 / #B5B2A8`.
Dark: `#EDEAE0 / #B5B2A8 / #8E8B82 / #6E6B65`.

### 3.3 Границы

3 уровня: `subtle / default / strong`. Light — alpha от чёрного (8% / 14% / 22%), dark — фиксированные `#2D323D / #3A4148 / #4A525C`.

### 3.4 Семантические цвета

- **Акцент (шалфей):** `--accent-primary #5F8568` + `hover #4F7256`, `soft #E8F0EA`, `border #C9D9CD`, `text #3D5443`.
- **CTA (антрацит):** `--action-primary #2D3A30` + `hover #1F2A23`, `text #F7F6F2`. В dark инвертируется в светлый блок на тёмном фоне.
- **Warning:** `#B5642E` + `soft / border / text` варианты.
- **Danger:** `#C44545` + `soft / border / text`.
- **Info:** `#4A7290` + `soft / border / text`.
- **Neutral:** `soft / border / text / dot` для нейтральных пилюль.

### 3.5 Типографика

- `--font-sans`: `"Inter", "Manrope", "Geist Variable", system-ui, sans-serif`.
- `--font-serif`: `"Cormorant Garamond", "Source Serif Pro", Georgia, serif`. Используется по флагу `serif` в `<PageHeader>` и в `KpiCard.value`.
- `--font-mono`: `"JetBrains Mono", "SF Mono", Menlo, Consolas, monospace`. Используется в денежных ячейках таблиц и больших числах.
- **Веса только 400 и 500.** Заголовки — 500.
- Размерная шкала: `2xs 11 / xs 12 / sm 13 / base 14 / md 15 / lg 18 / xl 22 / 2xl 26 / 3xl 28`.

### 3.6 Радиусы и тени

- Радиусы: `sm 4 / md 8 / lg 12 / xl 16 / 2xl 20 / 3xl 24 / full`.
- Тени **только три**: `--shadow-popover`, `--shadow-modal`, `--shadow-focus`. Иерархия карточек строится бордерами и подложками, а не drop-shadow.

### 3.7 Hairline

В `@layer base` все Tailwind border-utility (`border`, `border-t/r/b/l/x/y`) рендерятся в **0.5px**. Перебивается явным `border-2` или `border-0`.

### 3.8 Shadcn-совместимость

Все семантические токены мапятся в shadcn-имена (`--color-primary`, `--color-card`, `--color-border`, `--color-sidebar`, и т.д.) внутри блока `@theme inline`. Это позволяет shadcn-компонентам автоматически унаследовать палитру без правки их кода.

### 3.9 Утилиты

- `container-page` — `mx-auto max-w-[1200px] px-6`.
- `hairline` / `hairline-strong` / `hairline-dashed` — явные hairline-бордеры.
- `focus-ring` — единый стиль focus.

---

## 4. Layout и навигация

### 4.1 `App.tsx`

```tsx
<ThemeProvider attribute="data-theme" defaultTheme="light" enableSystem={false} disableTransitionOnChange>
  <QueryClientProvider client={queryClient}>
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/"                  element={<DashboardPage />} />
          <Route path="/upload"            element={<UploadPage />} />
          <Route path="/projects"          element={<ProjectsPage />} />
          <Route path="/projects/:id"      element={<ProjectPage />} />
          <Route path="/material-classes"  element={<MaterialClassesPage />} />
          <Route path="/reference-prices"  element={<ReferencePricesPage />} />
          <Route path="/reports"           element={<ReportsPage />} />
          <Route path="/settings"          element={<SettingsPage />} />
          <Route path="/documents/:id"     element={<ReviewPage />} />
          <Route path="*"                  element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
    <Toaster richColors position="top-right" />
  </QueryClientProvider>
</ThemeProvider>
```

`queryClient`: `staleTime: 60_000`, `retry: 1`, `refetchOnWindowFocus: false`. Глобальный `onError` для мутаций → `toast.error`.

### 4.2 `AppShell`

Фиксированный TopNav сверху (`h-14`, hairline-bottom, `bg-surface`), под ним `<Outlet />` на `bg-page min-h-screen`. **Никаких глобальных контейнеров и паддингов** — каждая страница сама решает, использовать `container-page py-8` или растянуться на всю ширину (Review-страница с превью документа).

### 4.3 `TopNav`

- Слева: `<Logo />` (иконка + текст «УПД Трекер», fontFamily-serif, 16px, weight 500, `<Link to="/">`).
- По центру/справа: 7 пунктов через `NavLink`: Дашборд, Загрузка, Объекты, Классы материалов, Эталоны, Отчёты, Настройки. Каждый — иконка lucide 14px + текст `text-sm font-medium`. Активный: `text-fg + bg-surface-hover`. Неактивный: `text-fg-secondary hover:bg-surface-hover hover:text-fg`. Padding `px-3 py-1.5`, радиус `md`.
- Крайний правый блок: `<ThemeToggle />`. Слот для будущего `<UserMenu />`.

### 4.4 `ThemeToggle`

Иконочная кнопка `Sun`/`Moon` (lucide), переключает `next-themes`. Без анимации перехода (`disableTransitionOnChange`).

### 4.5 Респонсив

Десктоп — как описано выше. На ≤768px меню коллапсится в `Sheet` (shadcn) с гамбургер-кнопкой слева. **В первой итерации не реализуем** — UDP внутренний инструмент, мобильный не приоритет, но архитектура должна это допускать.

---

## 5. Слой `ui-domain/`

| Компонент | Назначение | Ключевые пропсы |
|---|---|---|
| `Button` | Обёртка над shadcn `button`. Variants: primary (CTA-антрацит), secondary, ghost, danger. Sizes: sm/md/lg. | `variant`, `size`, `leftIcon`, `rightIcon`, `loading` |
| `PageHeader` | `<h1>` (по флагу `serif` — Cormorant 28px), `subtitle` (text-fg-secondary), слот `actions`. Hairline-bottom. | `title`, `subtitle`, `actions`, `serif` |
| `Surface` | Замена `Card`: `bg-surface + hairline + rounded-lg + p-6`. | `tone` (default/sunken), `padding` |
| `KpiCard` | Метрика: `label` (uppercase, tracking-wider, text-fg-tertiary), `value` (2xl, serif или mono), `delta` (опционально). | `label`, `value`, `delta`, `tone` |
| `StatusPill` | Цветная пилюля: tone (success/warning/danger/neutral/info/accent) → `bg-*-soft + border-*-border + text-*-text`. | `tone`, `label`, `dot` |
| `FilterPill` | Кнопка-фильтр со счётчиком (` · 12`). Активная — accent-soft. | `active`, `label`, `count`, `tone`, `onClick` |
| `EmptyState` | Центрированный блок: иконка, title, description, action-слот. | `icon`, `title`, `description`, `action` |
| `Skeleton` | `animate-pulse + bg-surface + hairline`. | `className` |
| `Tabs` | Hairline-таблы: горизонтальная линия по низу + активный таб с `border-b-2 accent`. | `value`, `onValueChange`, `tabs` |
| `Breadcrumbs` | Хлебные крошки. Все звенья кроме последнего — `<Link>`, последнее — `text-fg`. | `items` |
| `MoneyCell` | `1 234 567 ₽`, моно, right-align. | `value`, `currency` |
| `DeviationCell` | `+12.4%` с tone (положит — warning, отриц — accent/success, ноль — fg-tertiary). | `value`, `withSign` |
| `ConfidenceBadge` | AI-confidence: ≥0.85 success, 0.7–0.85 warning, <0.7 danger. Поверх `StatusPill`. | `value` |
| `Dropzone` | Обёртка над `react-dropzone`: hairline-dashed, иконка `UploadCloud`, состояния idle/dragging/uploading. | `onDrop`, `accept`, `multiple` |

---

## 6. Страницы

### 6.1 Шаблон страницы

```tsx
<div className="container-page py-8">
  <PageHeader serif title="..." subtitle="..." actions={<Button>...</Button>} />
  <div className="mt-6"> {/* фильтры */} </div>
  <div className="mt-6"> {/* контент: грид/таблица/EmptyState/Skeleton */} </div>
</div>
```

### 6.2 Dashboard

- `PageHeader` «Аналитика» + subtitle + actions «Выбрать период».
- **Контекст-блок** сверху: `Select` объект + `Select` класс материала (это контекст, не фильтр).
- **Строка KPI** (4 `KpiCard`): Документов / СФ / Объём м³ / Сумма ₽. Скелетоны при загрузке.
- **Фильтр-строка**: Search + `FilterPill` группа (по статусам обработки) + Sort.
- **Таблица «Расчёты отклонений»** в `Surface`. Колонки `Откл. %` через `DeviationCell`, суммы через `MoneyCell`. При пустоте — `EmptyState` внутри.
- **Таблица «Счета-фактуры»** в `Surface`. `ConfidenceBadge` для AI-confidence. Строки с `has_issues` подсвечиваются `bg-warning-soft`, иконка `AlertTriangle` слева.
- **Empty-states:** объект не выбран → подсказка + список последних объектов; объект пуст → CTA «Загрузить» → `/upload`.

### 6.3 Upload

- Большой `<Dropzone>` (hairline-dashed, иконка облака, текст). Над зоной — `Select` объект.
- При drop: список загружаемых файлов с прогрессом, `StatusPill` `processing`/`ready`/`attention`/`danger`.
- Каждый успешный файл — карточка-результат («СФ № X от Y, поставщик Z, N позиций, AI-confidence M%»). Кнопка «Проверить» → `/documents/:id`.
- Тосты успеха/ошибки на каждый файл.

### 6.4 Review (`/documents/:id`)

- `Breadcrumbs`: Объекты › <название> › СФ № <номер>.
- Маленький `PageHeader`: номер + дата + поставщик. Справа `StatusPill`.
- **Двухколоночный layout:** слева превью документа (изображение/PDF), справа форма редактирования.
- Правая колонка — `Tabs` «Шапка / Позиции / Проблемы»:
  - **Шапка**: поля счёта-фактуры (номер, дата, поставщик, ставка НДС).
  - **Позиции**: редактируемая таблица с inline-`Select` для `material_class`.
  - **Проблемы**: список замечаний парсера/ИИ с возможностью «решено».
- Sticky-bar внизу: «Отклонить» (ghost) / «Сохранить как черновик» / «Подтвердить» (CTA).

### 6.5 Projects (`/projects`)

- `PageHeader` «Объекты» + кнопка «Новый объект» (открывает `Dialog`).
- Поиск по названию + сорт.
- Грид карточек 1/2/3 колонки. Карточка по образу `SiteCard` из эталона: иконка, название, breadcrumb, счётчики, `StatusPill`, низ — пара мелких метрик. Вся карточка — `<Link to="/projects/:id">`.
- Empty-state «Создайте первый объект» с CTA.

### 6.6 ProjectPage (`/projects/:id`)

- `Breadcrumbs` + `PageHeader` с названием объекта.
- Список СФ объекта (та же таблица, что на Dashboard, но без фильтра по объекту).
- Список расчётов отклонений по объекту.

### 6.7 MaterialClasses, ReferencePrices

- `PageHeader` + кнопка «Добавить» (`Dialog`).
- Таблица в `Surface`. Inline-редактирование через `Sheet` справа или `Dialog`.
- `EmptyState` при пустоте.

### 6.8 Reports

- `PageHeader` «Отчёты».
- Плитка карточек 2×N: каждая — `Surface` с описанием отчёта и кнопкой «Сформировать» (`Dialog` с параметрами + скачивание).

### 6.9 Settings

- Двухколоночная страница: sticky боковое меню секций слева + контент справа.
- Секции: Общие / Парсинг / Эталоны / Интеграции / О приложении.
- Поля: `<Label>` + контрол + helper-text. Sticky-bar «Сохранить» внизу.

### 6.10 Что не делаем сейчас

- Отдельные страницы создания (`/projects/new`, `/material-classes/new`) — всё через `Dialog`.
- Аналитические чарты на Dashboard (recharts уже есть, но первая итерация — табличная).
- Profile/Keys страницы — связаны с авторизацией, не в скоупе.
- Мобильная адаптация (`Sheet`-меню) — следующая итерация.

---

## 7. Порядок работ

Каждая фаза — отдельный коммит, проверяется в браузере перед следующей.

1. **Foundation** — `package.json` (новые зависимости), `index.css` (полная замена). Проверка: `npm run dev`, цвета изменились во всех местах за счёт shadcn-маппинга.
2. **Providers + AppShell** — `ThemeProvider`, `QueryClientProvider`, `Toaster`. Новый `App.tsx`, `AppShell`, `TopNav`, `Logo`, `ThemeToggle`. Проверка: переключение темы, навигация, все страницы открываются (пусть в старом виде).
3. **`ui-domain/`** — все компоненты из секции 5 без интеграции в страницы. Опц. dev-only страница `/dev/showcase` для прокликивания.
4. **`services/api/*` + `types/*` + react-query** — переезд axios-вызовов в `useQuery`/`useMutation`. UI-изменений нет. Тосты на ошибки через глобальный `onError`.
5. **Dashboard** — переписать целиком. Эталон-страница, на ней калибруется плотность.
6. **Upload** — `Dropzone`, прогресс, тосты, карточки результатов.
7. **Review** — двухколоночный layout, `Tabs`, sticky-bar.
8. **Projects + ProjectPage** — грид карточек.
9. **MaterialClasses + ReferencePrices** — таблица + `Sheet`/`Dialog`.
10. **Reports + Settings** — последние, низкоприоритетные.

---

## 8. Тестирование

- На каждой фазе: `npm run dev`, проход по приложению, обе темы, состояния loading/empty/error (последние — отключив бэкенд).
- Фаза 4: проверить, что react-query кэш правильно инвалидируется после мутаций (`invalidateQueries`).
- Финал: `npm run build` без warnings, `npm run lint` clean.
- Юнит-тесты компонентов в этой итерации не пишем — продукт пока не покрыт, добавление тестов — отдельный проект.

---

## 9. Риски

- **shadcn-компоненты могут плохо ложиться на новые токены** — пройтись по `components/ui/*.tsx` (особенно `button`, `select`, `dialog`) и подкорректировать классы. План: правки только если поверх токенов выглядит сломано.
- **Cormorant Garamond на кириллице** — проверить начертания. Fallback: `Source Serif Pro` / Charter.
- **Hairline 0.5px на 1× DPI** — может быть слишком тонко. На 2×/Retina — норма. Проверять на стандартном мониторе во время фазы 1.
- **React Query 5 + React 19** — стабильно, проблем не ожидаем.
- **Объём работ велик** — по факту это переписывание всего фронтенда. Если устаём — после фазы 7 (Review) можно остановиться, остальное доделать в следующей итерации, продукт уже будет в новом языке.

---

## 10. Дальнейшие итерации (вне скоупа этого спека)

- **Авторизация**: бэкенд users + JWT, AuthContext, LoginPage/RegisterPage, ProtectedRoute. LoginPage уже будет в готовом языке.
- **Чарты на Dashboard** через recharts.
- **Мобильная адаптация**: коллапс TopNav в Sheet.
- **Юнит-тесты** ключевых компонентов `ui-domain/`.
