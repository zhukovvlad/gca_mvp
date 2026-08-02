# UDP Frontend Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перенести дизайн-систему и UX-паттерны из проекта `kpi-tenders-react` во фронтенд UDP — токены, темы light/dark, hairline-бордеры 0.5px, serif-заголовки, слой `ui-domain/`, AppShell с TopNav, react-query, sonner, react-dropzone, переписать все страницы на новый язык.

**Architecture:** Vite + React 19 + TypeScript + Tailwind v4. Слой shadcn-примитивов (`components/ui/*`) уже есть и остаётся (на `@base-ui/react`). Поверх него — новый слой `components/ui-domain/*` с продуктовыми компонентами. Layout через `AppShell` + `<Outlet/>`. Данные через `@tanstack/react-query` поверх существующего axios-инстанса. Тосты через `sonner`. Темы через `next-themes` с атрибутом `data-theme`.

**Tech Stack:** React 19, Vite 8, TypeScript, Tailwind 4, shadcn (`@base-ui/react`), `@tanstack/react-query` 5, `next-themes`, `sonner`, `react-dropzone`, `recharts` (есть, не используем сейчас), `lucide-react`, Cormorant Garamond через `@fontsource/cormorant-garamond`.

**Спек:** [docs/superpowers/specs/2026-05-09-udp-frontend-redesign-design.md](../specs/2026-05-09-udp-frontend-redesign-design.md)

---

## File Structure

### Создаются

```
frontend/src/
├── components/
│   ├── layout/
│   │   ├── AppShell.tsx              # каркас: TopNav + <Outlet/>
│   │   ├── TopNav.tsx                # горизонтальная навигация
│   │   ├── Logo.tsx                  # лого + текст
│   │   └── ThemeToggle.tsx           # Sun/Moon переключатель
│   ├── ui-domain/
│   │   ├── Button.tsx                # обёртка над shadcn Button с CTA-вариантом
│   │   ├── PageHeader.tsx            # h1 + subtitle + actions
│   │   ├── Surface.tsx               # bg-surface + hairline + rounded-lg
│   │   ├── KpiCard.tsx               # метрика label+value+delta
│   │   ├── StatusPill.tsx            # цветная пилюля
│   │   ├── FilterPill.tsx            # кнопка-фильтр с count
│   │   ├── EmptyState.tsx            # icon+title+description+action
│   │   ├── Skeleton.tsx              # animate-pulse блок
│   │   ├── Tabs.tsx                  # hairline-табы
│   │   ├── Breadcrumbs.tsx           # хлебные крошки
│   │   ├── MoneyCell.tsx             # 1 234 ₽ моно
│   │   ├── DeviationCell.tsx         # +12.4% с tone
│   │   ├── ConfidenceBadge.tsx       # AI-confidence
│   │   └── Dropzone.tsx              # обёртка react-dropzone
│   ├── invoices/
│   │   └── InvoiceTable.tsx          # таблица СФ для Dashboard и Project
│   ├── projects/
│   │   └── ProjectCard.tsx           # карточка для грида объектов
│   └── review/
│       ├── ReviewHeader.tsx          # шапка СФ в Review
│       ├── ReviewItemsTable.tsx      # таблица позиций
│       └── ReviewIssues.tsx          # список проблем
├── lib/
│   └── format.ts                     # formatMoney, formatPercent, formatRelative
├── services/api/
│   ├── projects.ts
│   ├── invoices.ts
│   ├── materialClasses.ts
│   ├── referencePrices.ts
│   ├── dashboard.ts
│   ├── reports.ts
│   ├── settings.ts
│   └── upload.ts
├── types/
│   ├── project.ts
│   ├── invoice.ts
│   ├── materialClass.ts
│   ├── referencePrice.ts
│   ├── dashboard.ts
│   └── common.ts
└── pages/
    └── ProjectPage.tsx                # новая, для /projects/:id
```

### Модифицируются

```
frontend/
├── package.json                       # добавить зависимости, скрипты
├── src/
│   ├── App.tsx                        # провайдеры + AppShell + роуты
│   ├── index.css                      # ПОЛНАЯ замена: токены, темы, hairline
│   ├── lib/api.ts                     # без изменений (использует services/api)
│   └── pages/
│       ├── Dashboard.tsx              # переписать целиком
│       ├── Upload.tsx                 # Dropzone
│       ├── Review.tsx                 # двухколоночный layout
│       ├── Projects.tsx               # грид карточек
│       ├── MaterialClasses.tsx        # таблица + Dialog
│       ├── ReferencePrices.tsx        # таблица + Dialog
│       ├── Reports.tsx                # плитка + Dialog
│       └── Settings.tsx               # боковое меню + контент
```

---

## Phase 1 — Foundation: зависимости и токены

### Task 1.1: Установить новые зависимости

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: Установить runtime-зависимости**

Run в `frontend/`:
```bash
npm install @tanstack/react-query@^5.99.0 next-themes@^0.4.6 sonner@^2.0.7 react-dropzone@^15.0.0 @fontsource/cormorant-garamond@^5.2.8
```

Expected: `package.json` обновлён, `node_modules` содержит новые пакеты, нет ошибок установки.

- [ ] **Step 2: Убедиться, что `@base-ui/react` остался в зависимостях**

Open `frontend/package.json`, проверить наличие `"@base-ui/react": "^1.4.1"` в `dependencies`. **НЕ удалять** — он используется в `components/ui/*`.

- [ ] **Step 3: Запустить dev-сервер для baseline**

Run:
```bash
cd frontend && npm run dev
```
Expected: Vite запускается на `http://localhost:5173`, приложение открывается, ошибок в консоли нет (приложение пока в старом виде — это нормально).

Остановить dev-сервер (`Ctrl+C`).

- [ ] **Step 4: Commit**

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "chore(frontend): add react-query, next-themes, sonner, react-dropzone, cormorant-garamond"
```

---

### Task 1.2: Заменить `index.css` на дизайн-токены

**Files:**
- Modify: `frontend/src/index.css` (полная замена содержимого)

- [ ] **Step 1: Полностью переписать `frontend/src/index.css`**

Заменить всё содержимое файла на:

```css
@import "tailwindcss";
@import "tw-animate-css";
@import "@fontsource/cormorant-garamond/400.css";
@import "@fontsource/cormorant-garamond/500.css";
@import "@fontsource-variable/geist";

/* ============================================================================
   UDP — Design Tokens
   Источник: kpi-tenders-react. Спокойный, плотный язык продукта.
   Иерархия — через бордеры и подложки, не через тени. Цвет несёт смысл.
============================================================================ */

@custom-variant dark (&:is([data-theme="dark"] *));

:root,
[data-theme="light"] {
  --bg-page:           #F4F2EC;
  --bg-surface:        #FFFFFF;
  --bg-surface-sunken: #F7F6F2;
  --bg-surface-hover:  #FAFAF7;
  --bg-section-header: #FAFAF7;

  --text-primary:      #1F2128;
  --text-secondary:    #5A5D66;
  --text-tertiary:     #8E8B82;
  --text-muted:        #B5B2A8;

  --border-subtle:     rgba(0, 0, 0, 0.08);
  --border-default:    rgba(0, 0, 0, 0.14);
  --border-strong:     rgba(0, 0, 0, 0.22);

  --accent-primary:        #5F8568;
  --accent-primary-hover:  #4F7256;
  --accent-primary-soft:   #E8F0EA;
  --accent-primary-border: #C9D9CD;
  --accent-primary-text:   #3D5443;

  --action-primary:        #2D3A30;
  --action-primary-hover:  #1F2A23;
  --action-primary-text:   #F7F6F2;

  --warning:               #B5642E;
  --warning-strong:        #A33D1F;
  --warning-soft:          #FAF1E1;
  --warning-border:        #E8C8A8;
  --warning-text:          #6B3915;

  --neutral-soft:          #EFEEE6;
  --neutral-border:        #D8D4C8;
  --neutral-text:          #5F5E5A;
  --neutral-dot:           #888780;

  --danger:                #C44545;
  --danger-soft:           #F9E7E7;
  --danger-border:         #E8C8C8;
  --danger-text:           #7A2424;

  --info:                  #4A7290;
  --info-soft:             #E5EEF4;
  --info-border:           #C5D7E2;
  --info-text:             #2C4A60;
}

[data-theme="dark"] {
  --bg-page:           #1A1D24;
  --bg-surface:        #232730;
  --bg-surface-sunken: #1F232B;
  --bg-surface-hover:  #262B35;
  --bg-section-header: #1C1F26;

  --text-primary:      #EDEAE0;
  --text-secondary:    #B5B2A8;
  --text-tertiary:     #8E8B82;
  --text-muted:        #6E6B65;

  --border-subtle:     #2D323D;
  --border-default:    #3A4148;
  --border-strong:     #4A525C;

  --accent-primary:        #8FAB91;
  --accent-primary-hover:  #A0BCA2;
  --accent-primary-soft:   #2A352D;
  --accent-primary-border: #3A4A3D;
  --accent-primary-text:   #B8C4BB;

  --action-primary:        #B8C4BB;
  --action-primary-hover:  #C9D4CC;
  --action-primary-text:   #1A1D24;

  --warning:               #D49A6E;
  --warning-strong:        #E8B080;
  --warning-soft:          #353027;
  --warning-border:        #4A3D2E;
  --warning-text:          #D49A6E;

  --neutral-soft:          #2A2D33;
  --neutral-border:        #353944;
  --neutral-text:          #B5B2A8;
  --neutral-dot:           #8E8B82;

  --danger:                #E07878;
  --danger-soft:           #3A2929;
  --danger-border:         #4A3434;
  --danger-text:           #E8A0A0;

  --info:                  #7AA0BE;
  --info-soft:             #2A323D;
  --info-border:           #3A4854;
  --info-text:             #A5C0D6;
}

:root {
  --radius-sm:   4px;
  --radius-md:   8px;
  --radius-lg:   12px;
  --radius-xl:   16px;
  --radius-full: 9999px;

  --font-sans:  "Inter", "Manrope", "Geist Variable", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --font-serif: "Cormorant Garamond", "Source Serif Pro", Georgia, serif;
  --font-mono:  "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;

  --shadow-popover: 0 4px 12px rgba(0, 0, 0, 0.08);
  --shadow-modal:   0 12px 32px rgba(0, 0, 0, 0.16);
  --shadow-focus:   0 0 0 3px rgba(95, 133, 104, 0.25);
}

@theme inline {
  --color-page:            var(--bg-page);
  --color-surface:         var(--bg-surface);
  --color-surface-sunken:  var(--bg-surface-sunken);
  --color-surface-hover:   var(--bg-surface-hover);
  --color-section-header:  var(--bg-section-header);

  --color-fg:              var(--text-primary);
  --color-fg-secondary:    var(--text-secondary);
  --color-fg-tertiary:     var(--text-tertiary);
  --color-fg-muted:        var(--text-muted);

  --color-border-subtle:   var(--border-subtle);
  --color-border-default:  var(--border-default);
  --color-border-strong:   var(--border-strong);

  --color-accent:          var(--accent-primary);
  --color-accent-hover:    var(--accent-primary-hover);
  --color-accent-soft:     var(--accent-primary-soft);
  --color-accent-border:   var(--accent-primary-border);
  --color-accent-text:     var(--accent-primary-text);

  --color-action:          var(--action-primary);
  --color-action-hover:    var(--action-primary-hover);
  --color-action-text:     var(--action-primary-text);

  --color-warning:         var(--warning);
  --color-warning-strong:  var(--warning-strong);
  --color-warning-soft:    var(--warning-soft);
  --color-warning-border:  var(--warning-border);
  --color-warning-text:    var(--warning-text);

  --color-neutral-soft:    var(--neutral-soft);
  --color-neutral-border:  var(--neutral-border);
  --color-neutral-text:    var(--neutral-text);
  --color-neutral-dot:     var(--neutral-dot);

  --color-danger:          var(--danger);
  --color-danger-soft:     var(--danger-soft);
  --color-danger-border:   var(--danger-border);
  --color-danger-text:     var(--danger-text);

  --color-info:            var(--info);
  --color-info-soft:       var(--info-soft);
  --color-info-border:     var(--info-border);
  --color-info-text:       var(--info-text);

  /* shadcn-совместимые алиасы — поддержать готовые примитивы из ui/ */
  --color-background:           var(--bg-page);
  --color-foreground:           var(--text-primary);
  --color-card:                 var(--bg-surface);
  --color-card-foreground:      var(--text-primary);
  --color-popover:              var(--bg-surface);
  --color-popover-foreground:   var(--text-primary);
  --color-primary:              var(--action-primary);
  --color-primary-foreground:   var(--action-primary-text);
  --color-secondary:            var(--bg-surface-sunken);
  --color-secondary-foreground: var(--text-secondary);
  --color-muted:                var(--bg-surface-sunken);
  --color-muted-foreground:     var(--text-tertiary);
  --color-accent-foreground:    var(--accent-primary-text);
  --color-destructive:          var(--danger);
  --color-destructive-foreground: var(--danger-text);
  --color-border:               var(--border-default);
  --color-input:                var(--border-default);
  --color-ring:                 var(--accent-primary);
  --color-sidebar:              var(--bg-surface);
  --color-sidebar-foreground:   var(--text-primary);
  --color-sidebar-primary:      var(--action-primary);
  --color-sidebar-primary-foreground: var(--action-primary-text);
  --color-sidebar-accent:       var(--bg-surface-hover);
  --color-sidebar-accent-foreground: var(--text-primary);
  --color-sidebar-border:       var(--border-subtle);
  --color-sidebar-ring:         var(--accent-primary);

  --font-sans:    var(--font-sans);
  --font-serif:   var(--font-serif);
  --font-mono:    var(--font-mono);
  --font-heading: var(--font-serif);

  --text-2xs: 11px;
  --text-2xs--line-height: 1.4;
  --text-xs:  12px;
  --text-xs--line-height: 1.4;
  --text-sm:  13px;
  --text-sm--line-height: 1.5;
  --text-base: 14px;
  --text-base--line-height: 1.6;
  --text-md:  15px;
  --text-md--line-height: 1.5;
  --text-lg:  18px;
  --text-lg--line-height: 1.4;
  --text-xl:  22px;
  --text-xl--line-height: 1.3;
  --text-2xl: 26px;
  --text-2xl--line-height: 1.2;
  --text-3xl: 28px;
  --text-3xl--line-height: 1.2;

  --radius-sm: 4px;
  --radius-md: 8px;
  --radius-lg: 12px;
  --radius-xl: 16px;
  --radius-2xl: 20px;
  --radius-3xl: 24px;
  --radius-full: 9999px;

  --shadow-popover: var(--shadow-popover);
  --shadow-modal:   var(--shadow-modal);
  --shadow-focus:   var(--shadow-focus);
}

@layer base {
  *, ::before, ::after {
    border-color: var(--color-border-subtle);
  }

  html {
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    color-scheme: light dark;
  }

  body {
    background-color: var(--color-page);
    color: var(--color-fg);
    font-family: var(--font-sans);
    font-size: 14px;
    line-height: 1.6;
    font-weight: 400;
  }

  h1, h2, h3, h4, h5, h6 {
    font-weight: 500;
    line-height: 1.2;
    color: var(--color-fg);
  }

  ::selection {
    background-color: var(--color-accent-soft);
    color: var(--color-accent-text);
  }

  /* Hairline 0.5px по умолчанию */
  .border, .border-t, .border-r, .border-b, .border-l, .border-x, .border-y {
    border-style: solid;
    border-width: 0.5px;
  }
  .border-t { border-top-width: 0.5px; }
  .border-r { border-right-width: 0.5px; }
  .border-b { border-bottom-width: 0.5px; }
  .border-l { border-left-width: 0.5px; }

  .border-2 { border-width: 2px !important; }
  .border-0 { border-width: 0 !important; }

  input, textarea, select, button {
    font-family: inherit;
  }
}

@utility container-page {
  margin-inline: auto;
  max-width: 1200px;
  padding-inline: 24px;
}

@utility hairline {
  border-width: 0.5px;
  border-style: solid;
  border-color: var(--color-border-subtle);
}

@utility hairline-strong {
  border-width: 0.5px;
  border-style: solid;
  border-color: var(--color-border-default);
}

@utility hairline-dashed {
  border-width: 0.5px;
  border-style: dashed;
  border-color: var(--color-border-default);
}

@utility focus-ring {
  outline: 2px solid transparent;
  outline-offset: 2px;
  box-shadow: var(--shadow-focus);
}
```

- [ ] **Step 2: Запустить dev-сервер**

Run:
```bash
cd frontend && npm run dev
```

Открыть `http://localhost:5173`. **Что должно произойти:**
- Фон страницы изменился на тёплый бежевый `#F4F2EC`
- Кнопки и карточки получили антрацитовый CTA-цвет вместо чёрно-серого
- Бордеры стали тоньше (0.5px)
- В консоли нет ошибок про неразрешённые токены/импорты

Если что-то выглядит сломанно (например, белые пятна там, где должен быть фон) — это нормально, поправим в фазах 5+ при переписывании страниц.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/index.css
git commit -m "feat(frontend): replace design tokens with kpi-tenders palette

- light/dark themes via data-theme attribute
- accent (sage), action (anthracite), bg (warm beige) palettes
- 4-level text, 3-level borders
- 0.5px hairline borders by default
- shadcn token aliases for compatibility
- container-page, hairline-* utilities"
```

---

## Phase 2 — Providers, AppShell, навигация

### Task 2.1: Logo

**Files:**
- Create: `frontend/src/components/layout/Logo.tsx`

- [ ] **Step 1: Создать `frontend/src/components/layout/Logo.tsx`**

```tsx
import { Link } from "react-router-dom";
import { FileSpreadsheet } from "lucide-react";

export function Logo() {
  return (
    <Link
      to="/"
      className="flex items-center gap-2 text-fg hover:text-fg"
      aria-label="УПД Трекер — на главную"
    >
      <FileSpreadsheet size={18} className="text-accent" />
      <span className="font-serif text-base leading-none font-medium tracking-tight">
        УПД&nbsp;Трекер
      </span>
    </Link>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/layout/Logo.tsx
git commit -m "feat(frontend): add Logo component"
```

---

### Task 2.2: ThemeToggle

**Files:**
- Create: `frontend/src/components/layout/ThemeToggle.tsx`

- [ ] **Step 1: Создать `frontend/src/components/layout/ThemeToggle.tsx`**

```tsx
import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const isDark = theme === "dark";

  return (
    <button
      type="button"
      onClick={() => setTheme(isDark ? "light" : "dark")}
      className="grid h-8 w-8 place-items-center rounded-md text-fg-secondary hover:bg-surface-hover hover:text-fg focus-ring"
      aria-label={isDark ? "Светлая тема" : "Тёмная тема"}
      title={isDark ? "Светлая тема" : "Тёмная тема"}
    >
      {isDark ? <Sun size={16} /> : <Moon size={16} />}
    </button>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/layout/ThemeToggle.tsx
git commit -m "feat(frontend): add ThemeToggle component"
```

---

### Task 2.3: TopNav

**Files:**
- Create: `frontend/src/components/layout/TopNav.tsx`

- [ ] **Step 1: Создать `frontend/src/components/layout/TopNav.tsx`**

```tsx
import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  Upload,
  Building2,
  Layers,
  Target,
  FileSpreadsheet,
  Settings,
  type LucideIcon,
} from "lucide-react";
import { Logo } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";
import { cn } from "@/lib/utils";

interface NavItem {
  to: string;
  icon: LucideIcon;
  label: string;
  end?: boolean;
}

const NAV: NavItem[] = [
  { to: "/", icon: LayoutDashboard, label: "Дашборд", end: true },
  { to: "/upload", icon: Upload, label: "Загрузка" },
  { to: "/projects", icon: Building2, label: "Объекты" },
  { to: "/material-classes", icon: Layers, label: "Классы материалов" },
  { to: "/reference-prices", icon: Target, label: "Эталоны" },
  { to: "/reports", icon: FileSpreadsheet, label: "Отчёты" },
  { to: "/settings", icon: Settings, label: "Настройки" },
];

export function TopNav() {
  return (
    <header className="sticky top-0 z-40 h-14 border-b border-border-subtle bg-surface/95 backdrop-blur">
      <div className="container-page flex h-full items-center gap-6">
        <Logo />
        <nav className="flex flex-1 flex-wrap items-center gap-0.5">
          {NAV.map(({ to, icon: Icon, label, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors duration-150",
                  isActive
                    ? "bg-surface-hover text-fg"
                    : "text-fg-secondary hover:bg-surface-hover hover:text-fg"
                )
              }
            >
              <Icon size={14} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <ThemeToggle />
      </div>
    </header>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/layout/TopNav.tsx
git commit -m "feat(frontend): add TopNav with logo, nav links, theme toggle"
```

---

### Task 2.4: AppShell

**Files:**
- Create: `frontend/src/components/layout/AppShell.tsx`

- [ ] **Step 1: Создать `frontend/src/components/layout/AppShell.tsx`**

```tsx
import { Outlet } from "react-router-dom";
import { TopNav } from "./TopNav";

export function AppShell() {
  return (
    <div className="min-h-screen bg-page text-fg">
      <TopNav />
      <main>
        <Outlet />
      </main>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/layout/AppShell.tsx
git commit -m "feat(frontend): add AppShell layout"
```

---

### Task 2.5: Переписать `App.tsx` на провайдеры + AppShell

**Files:**
- Modify: `frontend/src/App.tsx` (полная замена)

- [ ] **Step 1: Заменить содержимое `frontend/src/App.tsx`**

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { Toaster, toast } from "sonner";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";

import { AppShell } from "@/components/layout/AppShell";
import Dashboard from "@/pages/Dashboard";
import UploadPage from "@/pages/Upload";
import Review from "@/pages/Review";
import Projects from "@/pages/Projects";
import MaterialClasses from "@/pages/MaterialClasses";
import ReferencePrices from "@/pages/ReferencePrices";
import Reports from "@/pages/Reports";
import SettingsPage from "@/pages/Settings";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 60_000,
      refetchOnWindowFocus: false,
    },
    mutations: {
      onError: (error: unknown) => {
        const message =
          error instanceof Error ? error.message : "Произошла ошибка";
        toast.error(message);
      },
    },
  },
});

export default function App() {
  return (
    <ThemeProvider
      attribute="data-theme"
      defaultTheme="light"
      enableSystem={false}
      disableTransitionOnChange
    >
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Routes>
            <Route element={<AppShell />}>
              <Route path="/" element={<Dashboard />} />
              <Route path="/upload" element={<UploadPage />} />
              <Route path="/projects" element={<Projects />} />
              <Route path="/material-classes" element={<MaterialClasses />} />
              <Route path="/reference-prices" element={<ReferencePrices />} />
              <Route path="/reports" element={<Reports />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/documents/:id" element={<Review />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
        <Toaster richColors position="top-right" />
      </QueryClientProvider>
    </ThemeProvider>
  );
}
```

- [ ] **Step 2: Запустить dev-сервер и проверить smoke-test**

Run:
```bash
cd frontend && npm run dev
```

Открыть `http://localhost:5173`. Проверить:
- В шапке появилось лого «УПД Трекер» (serif), 7 пунктов меню с иконками, кнопка темы справа
- Клик по кнопке темы переключает на тёмную (фон становится `#1A1D24`), повторный клик — обратно
- Клик по любому пункту меню переходит на соответствующую страницу, активный пункт подсвечен `bg-surface-hover`
- Старые страницы (Dashboard и т.д.) открываются как раньше — внутри ещё старая разметка, это норма
- Консоль чистая: нет ошибок React, нет предупреждений про роуты

Остановить dev-сервер.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(frontend): wire up providers, AppShell, sonner

- ThemeProvider (next-themes, data-theme attribute)
- QueryClientProvider with global mutation onError -> toast
- AppShell with TopNav as Outlet wrapper
- Toaster top-right"
```

---

## Phase 3 — `ui-domain/` слой

### Task 3.1: Утилиты форматирования

**Files:**
- Create: `frontend/src/lib/format.ts`

- [ ] **Step 1: Создать `frontend/src/lib/format.ts`**

```ts
export function formatMoney(value: number | null | undefined, currency = "₽"): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ${currency}`;
}

export function formatPercent(value: number | null | undefined, withSign = false): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = withSign && value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("ru-RU");
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
}

export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";

  const now = Date.now();
  const diffMs = now - d.getTime();
  const diffMin = Math.round(diffMs / 60_000);
  const diffHr = Math.round(diffMs / 3_600_000);
  const diffDay = Math.round(diffMs / 86_400_000);

  if (diffMin < 1) return "только что";
  if (diffMin < 60) return `${diffMin} мин назад`;
  if (diffHr < 24) return `${diffHr} ч назад`;
  if (diffDay < 7) return `${diffDay} дн назад`;
  return formatDate(iso);
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/format.ts
git commit -m "feat(frontend): add format utilities (money, percent, date, relative)"
```

---

### Task 3.2: Button (`ui-domain`)

**Files:**
- Create: `frontend/src/components/ui-domain/Button.tsx`

- [ ] **Step 1: Создать `frontend/src/components/ui-domain/Button.tsx`**

Это **новый продуктовый Button** на нативном `<button>`. Старый `components/ui/button.tsx` остаётся для обратной совместимости shadcn-компонентов, но в страницах используем именно этот.

```tsx
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  leftIcon?: ReactNode;
  rightIcon?: ReactNode;
  loading?: boolean;
}

const VARIANT: Record<Variant, string> = {
  primary:
    "bg-action text-action-text hover:bg-action-hover disabled:opacity-50",
  secondary:
    "border border-border-default bg-surface text-fg hover:bg-surface-hover disabled:opacity-50",
  ghost:
    "text-fg-secondary hover:bg-surface-hover hover:text-fg disabled:opacity-50",
  danger:
    "bg-danger-soft text-danger-text border border-danger-border hover:bg-danger/10 disabled:opacity-50",
};

const SIZE: Record<Size, string> = {
  sm: "h-7 px-2.5 text-xs gap-1",
  md: "h-8 px-3 text-sm gap-1.5",
  lg: "h-10 px-4 text-sm gap-2",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      variant = "primary",
      size = "md",
      leftIcon,
      rightIcon,
      loading,
      disabled,
      className,
      children,
      ...rest
    },
    ref
  ) => {
    return (
      <button
        ref={ref}
        disabled={disabled || loading}
        className={cn(
          "inline-flex items-center justify-center rounded-md font-medium transition-colors duration-150 focus-ring disabled:cursor-not-allowed",
          VARIANT[variant],
          SIZE[size],
          className
        )}
        {...rest}
      >
        {loading ? (
          <Loader2 size={14} className="animate-spin" />
        ) : (
          leftIcon
        )}
        {children}
        {rightIcon}
      </button>
    );
  }
);
Button.displayName = "Button";
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/ui-domain/Button.tsx
git commit -m "feat(ui-domain): Button with primary/secondary/ghost/danger variants"
```

---

### Task 3.3: Surface

**Files:**
- Create: `frontend/src/components/ui-domain/Surface.tsx`

- [ ] **Step 1: Создать `frontend/src/components/ui-domain/Surface.tsx`**

```tsx
import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

interface SurfaceProps extends HTMLAttributes<HTMLDivElement> {
  tone?: "default" | "sunken";
  padding?: "none" | "sm" | "md" | "lg";
}

const TONE = {
  default: "bg-surface",
  sunken: "bg-surface-sunken",
};

const PADDING = {
  none: "",
  sm: "p-4",
  md: "p-6",
  lg: "p-8",
};

export function Surface({
  tone = "default",
  padding = "md",
  className,
  ...rest
}: SurfaceProps) {
  return (
    <div
      className={cn(
        "rounded-lg border border-border-subtle",
        TONE[tone],
        PADDING[padding],
        className
      )}
      {...rest}
    />
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/ui-domain/Surface.tsx
git commit -m "feat(ui-domain): Surface block with tone and padding"
```

---

### Task 3.4: PageHeader

**Files:**
- Create: `frontend/src/components/ui-domain/PageHeader.tsx`

- [ ] **Step 1: Создать `frontend/src/components/ui-domain/PageHeader.tsx`**

```tsx
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface PageHeaderProps {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  serif?: boolean;
}

export function PageHeader({ title, subtitle, actions, serif }: PageHeaderProps) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4 border-b border-border-subtle pb-4">
      <div className="min-w-0">
        <h1
          className={cn(
            "text-3xl text-fg",
            serif ? "font-serif font-medium tracking-tight" : "font-medium"
          )}
        >
          {title}
        </h1>
        {subtitle && (
          <p className="mt-1 text-sm text-fg-secondary">{subtitle}</p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/ui-domain/PageHeader.tsx
git commit -m "feat(ui-domain): PageHeader with serif title flag and actions slot"
```

---

### Task 3.5: StatusPill

**Files:**
- Create: `frontend/src/components/ui-domain/StatusPill.tsx`

- [ ] **Step 1: Создать `frontend/src/components/ui-domain/StatusPill.tsx`**

```tsx
import { cn } from "@/lib/utils";

export type StatusTone =
  | "success"
  | "warning"
  | "danger"
  | "neutral"
  | "info"
  | "accent";

interface StatusPillProps {
  tone: StatusTone;
  label: string;
  dot?: boolean;
  className?: string;
}

const TONE: Record<StatusTone, { bg: string; border: string; text: string; dot: string }> = {
  success: {
    bg: "bg-accent-soft",
    border: "border-accent-border",
    text: "text-accent-text",
    dot: "bg-accent",
  },
  warning: {
    bg: "bg-warning-soft",
    border: "border-warning-border",
    text: "text-warning-text",
    dot: "bg-warning",
  },
  danger: {
    bg: "bg-danger-soft",
    border: "border-danger-border",
    text: "text-danger-text",
    dot: "bg-danger",
  },
  neutral: {
    bg: "bg-neutral-soft",
    border: "border-neutral-border",
    text: "text-neutral-text",
    dot: "bg-neutral-dot",
  },
  info: {
    bg: "bg-info-soft",
    border: "border-info-border",
    text: "text-info-text",
    dot: "bg-info",
  },
  accent: {
    bg: "bg-accent-soft",
    border: "border-accent-border",
    text: "text-accent-text",
    dot: "bg-accent",
  },
};

export function StatusPill({ tone, label, dot, className }: StatusPillProps) {
  const c = TONE[tone];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-2xs font-medium",
        c.bg,
        c.border,
        c.text,
        className
      )}
    >
      {dot && <span className={cn("h-1.5 w-1.5 rounded-full", c.dot)} />}
      {label}
    </span>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/ui-domain/StatusPill.tsx
git commit -m "feat(ui-domain): StatusPill with 6 tones and optional dot"
```

---

### Task 3.6: FilterPill

**Files:**
- Create: `frontend/src/components/ui-domain/FilterPill.tsx`

- [ ] **Step 1: Создать `frontend/src/components/ui-domain/FilterPill.tsx`**

```tsx
import { cn } from "@/lib/utils";

interface FilterPillProps {
  active: boolean;
  label: string;
  count?: number;
  onClick: () => void;
  tone?: "default" | "warning";
}

export function FilterPill({
  active,
  label,
  count,
  onClick,
  tone = "default",
}: FilterPillProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-md border px-3 py-1.5 text-xs font-medium transition-colors duration-150 focus-ring",
        active
          ? tone === "warning"
            ? "border-warning-border bg-warning-soft text-warning-text"
            : "border-accent-border bg-accent-soft text-accent-text"
          : "border-border-subtle bg-transparent text-fg-secondary hover:bg-surface-hover hover:text-fg"
      )}
    >
      {label}
      {count !== undefined && (
        <span className="ml-1.5 text-fg-tertiary">· {count}</span>
      )}
    </button>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/ui-domain/FilterPill.tsx
git commit -m "feat(ui-domain): FilterPill with active state and count"
```

---

### Task 3.7: EmptyState + Skeleton

**Files:**
- Create: `frontend/src/components/ui-domain/EmptyState.tsx`
- Create: `frontend/src/components/ui-domain/Skeleton.tsx`

- [ ] **Step 1: Создать `frontend/src/components/ui-domain/EmptyState.tsx`**

```tsx
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-lg border border-border-subtle bg-surface px-6 py-16 text-center",
        className
      )}
    >
      {icon && (
        <div className="mb-3 grid h-10 w-10 place-items-center rounded-md bg-surface-sunken text-fg-tertiary">
          {icon}
        </div>
      )}
      <h3 className="text-md font-medium text-fg">{title}</h3>
      {description && (
        <p className="mt-1 max-w-md text-sm text-fg-secondary">{description}</p>
      )}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
```

- [ ] **Step 2: Создать `frontend/src/components/ui-domain/Skeleton.tsx`**

```tsx
import { cn } from "@/lib/utils";

interface SkeletonProps {
  className?: string;
}

export function Skeleton({ className }: SkeletonProps) {
  return (
    <div
      className={cn(
        "animate-pulse rounded-md border border-border-subtle bg-surface",
        className
      )}
    />
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ui-domain/EmptyState.tsx frontend/src/components/ui-domain/Skeleton.tsx
git commit -m "feat(ui-domain): EmptyState and Skeleton"
```

---

### Task 3.8: KpiCard

**Files:**
- Create: `frontend/src/components/ui-domain/KpiCard.tsx`

- [ ] **Step 1: Создать `frontend/src/components/ui-domain/KpiCard.tsx`**

```tsx
import { ArrowDown, ArrowUp } from "lucide-react";
import { cn } from "@/lib/utils";

interface KpiCardProps {
  label: string;
  value: string;
  delta?: { value: string; tone: "up" | "down" | "neutral" };
  className?: string;
}

export function KpiCard({ label, value, delta, className }: KpiCardProps) {
  return (
    <div
      className={cn(
        "rounded-lg border border-border-subtle bg-surface p-5",
        className
      )}
    >
      <div className="text-2xs uppercase tracking-wider text-fg-tertiary">
        {label}
      </div>
      <div className="mt-2 font-mono text-2xl text-fg">{value}</div>
      {delta && (
        <div
          className={cn(
            "mt-1 inline-flex items-center gap-1 text-xs",
            delta.tone === "up" && "text-warning",
            delta.tone === "down" && "text-accent",
            delta.tone === "neutral" && "text-fg-tertiary"
          )}
        >
          {delta.tone === "up" && <ArrowUp size={12} />}
          {delta.tone === "down" && <ArrowDown size={12} />}
          {delta.value}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/ui-domain/KpiCard.tsx
git commit -m "feat(ui-domain): KpiCard with label/value/delta"
```

---

### Task 3.9: Tabs (hairline)

**Files:**
- Create: `frontend/src/components/ui-domain/Tabs.tsx`

- [ ] **Step 1: Создать `frontend/src/components/ui-domain/Tabs.tsx`**

```tsx
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface TabItem<T extends string> {
  value: T;
  label: string;
  count?: number;
}

interface TabsProps<T extends string> {
  value: T;
  onValueChange: (value: T) => void;
  tabs: TabItem<T>[];
  children?: ReactNode;
  className?: string;
}

export function Tabs<T extends string>({
  value,
  onValueChange,
  tabs,
  children,
  className,
}: TabsProps<T>) {
  return (
    <div className={className}>
      <div className="flex gap-1 border-b border-border-subtle">
        {tabs.map((tab) => {
          const active = tab.value === value;
          return (
            <button
              key={tab.value}
              type="button"
              onClick={() => onValueChange(tab.value)}
              className={cn(
                "-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors duration-150",
                active
                  ? "border-accent text-fg"
                  : "border-transparent text-fg-secondary hover:text-fg"
              )}
            >
              {tab.label}
              {tab.count !== undefined && (
                <span className="ml-1.5 text-fg-tertiary">· {tab.count}</span>
              )}
            </button>
          );
        })}
      </div>
      {children && <div className="mt-4">{children}</div>}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/ui-domain/Tabs.tsx
git commit -m "feat(ui-domain): hairline Tabs with active border-b accent"
```

---

### Task 3.10: Breadcrumbs

**Files:**
- Create: `frontend/src/components/ui-domain/Breadcrumbs.tsx`

- [ ] **Step 1: Создать `frontend/src/components/ui-domain/Breadcrumbs.tsx`**

```tsx
import { Fragment } from "react";
import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";

export interface BreadcrumbItem {
  label: string;
  to?: string;
}

interface BreadcrumbsProps {
  items: BreadcrumbItem[];
}

export function Breadcrumbs({ items }: BreadcrumbsProps) {
  return (
    <nav aria-label="Хлебные крошки" className="flex items-center gap-1 text-xs">
      {items.map((item, idx) => {
        const isLast = idx === items.length - 1;
        return (
          <Fragment key={`${item.label}-${idx}`}>
            {idx > 0 && (
              <ChevronRight size={12} className="text-fg-tertiary" />
            )}
            {item.to && !isLast ? (
              <Link
                to={item.to}
                className="text-fg-secondary hover:text-fg"
              >
                {item.label}
              </Link>
            ) : (
              <span className="text-fg">{item.label}</span>
            )}
          </Fragment>
        );
      })}
    </nav>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/ui-domain/Breadcrumbs.tsx
git commit -m "feat(ui-domain): Breadcrumbs with chevron separator"
```

---

### Task 3.11: MoneyCell, DeviationCell, ConfidenceBadge

**Files:**
- Create: `frontend/src/components/ui-domain/MoneyCell.tsx`
- Create: `frontend/src/components/ui-domain/DeviationCell.tsx`
- Create: `frontend/src/components/ui-domain/ConfidenceBadge.tsx`

- [ ] **Step 1: `MoneyCell.tsx`**

```tsx
import { formatMoney } from "@/lib/format";
import { cn } from "@/lib/utils";

interface MoneyCellProps {
  value: number | null | undefined;
  currency?: string;
  className?: string;
}

export function MoneyCell({ value, currency, className }: MoneyCellProps) {
  return (
    <span className={cn("font-mono tabular-nums", className)}>
      {formatMoney(value, currency)}
    </span>
  );
}
```

- [ ] **Step 2: `DeviationCell.tsx`**

```tsx
import { formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

interface DeviationCellProps {
  value: number | null | undefined;
  className?: string;
}

export function DeviationCell({ value, className }: DeviationCellProps) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className={cn("text-fg-tertiary", className)}>—</span>;
  }
  const tone =
    value > 0 ? "text-warning-text" : value < 0 ? "text-accent-text" : "text-fg-tertiary";
  return (
    <span className={cn("font-mono tabular-nums font-medium", tone, className)}>
      {formatPercent(value, true)}
    </span>
  );
}
```

- [ ] **Step 3: `ConfidenceBadge.tsx`**

```tsx
import { StatusPill, type StatusTone } from "./StatusPill";

interface ConfidenceBadgeProps {
  value: number | null | undefined;
}

export function ConfidenceBadge({ value }: ConfidenceBadgeProps) {
  if (value === null || value === undefined) {
    return <span className="text-fg-tertiary">—</span>;
  }
  const pct = Math.round(value * 100);
  const tone: StatusTone =
    value >= 0.85 ? "success" : value >= 0.7 ? "warning" : "danger";
  return <StatusPill tone={tone} label={`${pct}%`} />;
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/ui-domain/MoneyCell.tsx frontend/src/components/ui-domain/DeviationCell.tsx frontend/src/components/ui-domain/ConfidenceBadge.tsx
git commit -m "feat(ui-domain): MoneyCell, DeviationCell, ConfidenceBadge"
```

---

### Task 3.12: Dropzone

**Files:**
- Create: `frontend/src/components/ui-domain/Dropzone.tsx`

- [ ] **Step 1: Создать `frontend/src/components/ui-domain/Dropzone.tsx`**

```tsx
import { useDropzone } from "react-dropzone";
import { UploadCloud } from "lucide-react";
import { cn } from "@/lib/utils";

interface DropzoneProps {
  onDrop: (files: File[]) => void;
  accept?: Record<string, string[]>;
  multiple?: boolean;
  disabled?: boolean;
  hint?: string;
}

export function Dropzone({
  onDrop,
  accept,
  multiple = true,
  disabled,
  hint = "PDF, JPG, PNG до 20 МБ",
}: DropzoneProps) {
  const { getRootProps, getInputProps, isDragActive, isDragReject } =
    useDropzone({ onDrop, accept, multiple, disabled });

  return (
    <div
      {...getRootProps()}
      className={cn(
        "flex cursor-pointer flex-col items-center justify-center rounded-lg px-6 py-12 text-center transition-colors duration-150 hairline-dashed",
        isDragActive && !isDragReject && "border-accent bg-accent-soft",
        isDragReject && "border-danger bg-danger-soft",
        !isDragActive && "bg-surface hover:bg-surface-hover",
        disabled && "cursor-not-allowed opacity-60"
      )}
    >
      <input {...getInputProps()} />
      <UploadCloud
        size={32}
        className={cn(
          "mb-3",
          isDragActive ? "text-accent" : "text-fg-tertiary"
        )}
      />
      <p className="text-sm font-medium text-fg">
        {isDragActive
          ? isDragReject
            ? "Этот формат не поддерживается"
            : "Отпустите, чтобы загрузить"
          : "Перетащите файлы сюда или нажмите для выбора"}
      </p>
      <p className="mt-1 text-xs text-fg-tertiary">{hint}</p>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/ui-domain/Dropzone.tsx
git commit -m "feat(ui-domain): Dropzone wrapping react-dropzone"
```

---

### Task 3.13: Smoke-test ui-domain через временную страницу-витрину

**Files:**
- Modify: `frontend/src/pages/Dashboard.tsx` (временно — вставим витрину поверх старого содержимого, **в конце фазы 5 это будет переписано**)

- [ ] **Step 1: Открыть `frontend/src/pages/Dashboard.tsx` и в самом верху функции `Dashboard()` (перед существующим return) добавить временный возврат витрины**

Вставить **сразу после** `useEffect` блока, ПЕРЕД старым `return (`:

```tsx
  // TODO: убрать после Phase 5 (Dashboard переписан)
  if (new URLSearchParams(window.location.search).get("showcase") === "1") {
    const { Button } = require("@/components/ui-domain/Button");
    const { PageHeader } = require("@/components/ui-domain/PageHeader");
    const { Surface } = require("@/components/ui-domain/Surface");
    const { StatusPill } = require("@/components/ui-domain/StatusPill");
    const { FilterPill } = require("@/components/ui-domain/FilterPill");
    const { EmptyState } = require("@/components/ui-domain/EmptyState");
    const { KpiCard } = require("@/components/ui-domain/KpiCard");
    const { Tabs } = require("@/components/ui-domain/Tabs");
    const { Breadcrumbs } = require("@/components/ui-domain/Breadcrumbs");
    const { MoneyCell } = require("@/components/ui-domain/MoneyCell");
    const { DeviationCell } = require("@/components/ui-domain/DeviationCell");
    const { ConfidenceBadge } = require("@/components/ui-domain/ConfidenceBadge");
    return (
      <div className="container-page py-8 space-y-6">
        <Breadcrumbs items={[{ label: "Дом", to: "/" }, { label: "Showcase" }]} />
        <PageHeader serif title="Витрина ui-domain" subtitle="Smoke-test перед фазой 4" actions={<Button>Action</Button>} />
        <div className="grid grid-cols-4 gap-3">
          <KpiCard label="Документов" value="42" />
          <KpiCard label="Объём" value="1 250" delta={{ value: "+5%", tone: "up" }} />
          <KpiCard label="Сумма" value="3.5М ₽" delta={{ value: "−2%", tone: "down" }} />
          <KpiCard label="СФ" value="120" delta={{ value: "0%", tone: "neutral" }} />
        </div>
        <Surface><div className="space-x-2">
          <StatusPill tone="success" label="готово" dot />
          <StatusPill tone="warning" label="внимание" dot />
          <StatusPill tone="danger" label="ошибка" dot />
          <StatusPill tone="neutral" label="нейтрально" dot />
          <StatusPill tone="info" label="инфо" dot />
        </div></Surface>
        <Surface><div className="space-x-2">
          <FilterPill active label="Все" count={42} onClick={() => {}} />
          <FilterPill active={false} label="Готовы" count={20} onClick={() => {}} />
          <FilterPill active={false} label="Требуют внимания" count={5} tone="warning" onClick={() => {}} />
        </div></Surface>
        <Surface><div className="flex gap-3">
          <Button variant="primary">Primary</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="danger">Danger</Button>
          <Button loading>Loading</Button>
        </div></Surface>
        <Surface><div className="space-x-4">
          <MoneyCell value={1234567.89} />
          <DeviationCell value={12.4} />
          <DeviationCell value={-5.6} />
          <ConfidenceBadge value={0.92} />
          <ConfidenceBadge value={0.75} />
          <ConfidenceBadge value={0.5} />
        </div></Surface>
        <EmptyState title="Ничего не найдено" description="Попробуйте изменить фильтр." action={<Button>Сбросить</Button>} />
      </div>
    );
  }
```

- [ ] **Step 2: Запустить dev-сервер и открыть витрину**

Run:
```bash
cd frontend && npm run dev
```

Открыть `http://localhost:5173/?showcase=1`. Проверить:
- Все компоненты отрисовались, ничего не сломано
- Переключение темы (`Moon`/`Sun` в TopNav) меняет цвета во всех компонентах
- Кнопки кликабельны, hover работает, FilterPill активный — accent-soft
- DeviationCell `+12.4%` оранжевый (warning-text), `−5.6%` зелёный (accent-text)
- ConfidenceBadge: 92% — success, 75% — warning, 50% — danger

Если что-то поплыло — поправить компонент, перезапустить, проверить ещё раз.

Остановить dev-сервер.

- [ ] **Step 3: Убрать витрину (вернуть Dashboard как был)**

В `Dashboard.tsx` удалить вставленный блок. Файл вернётся к исходному состоянию (старый Dashboard, без витрины).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Dashboard.tsx
git commit -m "chore(frontend): smoke-test ui-domain via temporary showcase (reverted)"
```

---

## Phase 4 — TypeScript-типы и `services/api/*` поверх react-query

Цель: компоненты не дёргают axios напрямую. Вместо этого — типизированные функции-сервисы и react-query хуки. Тосты на ошибки мутаций.

### Task 4.1: Доменные типы

**Files:**
- Create: `frontend/src/types/common.ts`
- Create: `frontend/src/types/project.ts`
- Create: `frontend/src/types/materialClass.ts`
- Create: `frontend/src/types/referencePrice.ts`
- Create: `frontend/src/types/invoice.ts`
- Create: `frontend/src/types/dashboard.ts`

- [ ] **Step 1: `common.ts`**

```ts
export type ID = number;
export type ISODate = string;     // "2025-01-08"
export type ISODateTime = string; // "2025-01-08T12:34:56Z"
```

- [ ] **Step 2: `project.ts`**

```ts
import type { ID, ISODateTime } from "./common";

export interface Project {
  id: ID;
  name: string;
  contract_number: string | null;
  created_at: ISODateTime;
}

export interface ProjectCreateInput {
  name: string;
  contract_number?: string | null;
}

export type ProjectUpdateInput = ProjectCreateInput;
```

- [ ] **Step 3: `materialClass.ts`**

```ts
import type { ID, ISODateTime } from "./common";

export interface MaterialClass {
  id: ID;
  material_type: string; // "concrete" | "rebar" | "other"
  name: string;          // например "В40"
  created_at: ISODateTime;
}

export interface MaterialClassCreateInput {
  material_type: string;
  name: string;
}
```

- [ ] **Step 4: `referencePrice.ts`**

```ts
import type { ID, ISODate } from "./common";

export interface ReferencePrice {
  id: ID;
  project_id: ID;
  material_class_id: ID;
  material_class_name?: string;
  price: number;
  period_start: ISODate;
  period_end: ISODate;
  source: string | null;
}

export interface ReferencePriceCreateInput {
  project_id: ID;
  material_class_id: ID;
  price: number;
  period_start: ISODate;
  period_end: ISODate;
  source?: string | null;
}
```

- [ ] **Step 5: `invoice.ts`**

```ts
import type { ID, ISODate, ISODateTime } from "./common";

export interface InvoiceItem {
  id?: ID;
  raw_name: string;
  item_type: "material" | "delivery" | "other";
  material_class: string | null;
  material_class_id?: ID | null;
  quantity: number;
  unit: string;
  unit_price: number;
  amount: number;
  vat_amount?: number | null;
}

export interface InvoiceRow {
  id: ID;
  document_id: ID;
  number: string;
  date: ISODate;
  supplier_name: string | null;
  supplier_inn?: string | null;
  vat_rate: number;
  ai_confidence: number | null;
  has_issues: boolean;
  items: InvoiceItem[];
}

export interface DocumentSummary {
  id: ID;
  project_id: ID;
  filename: string;
  doc_type: string;
  status: string;
  uploaded_at: ISODateTime;
  invoice_count: number;
}

export interface DocumentDetail extends DocumentSummary {
  invoices: InvoiceRow[];
}

export interface InvoiceUpdateInput {
  number?: string;
  date?: ISODate;
  supplier_name?: string | null;
  supplier_inn?: string | null;
  vat_rate?: number;
  items?: InvoiceItem[];
}
```

- [ ] **Step 6: `dashboard.ts`**

```ts
import type { ID, ISODate } from "./common";
import type { InvoiceRow } from "./invoice";

export interface DashboardSummary {
  doc_count: number;
  invoice_count: number;
  total_amount: number;
  total_qty: number;
}

export interface DashboardCalculation {
  material_class_name: string;
  period_start: ISODate;
  period_end: ISODate;
  avg_price: number;
  reference_price: number | null;
  deviation_pct: number | null;
  deviation_amount: number | null;
  total_qty: number;
  invoice_count: number;
}

export interface AutoCalculateResponse {
  period_start: ISODate | null;
  period_end: ISODate | null;
}

export interface CalculateInput {
  project_id: ID;
  material_class_id?: ID;
  period_start: ISODate;
  period_end: ISODate;
}

export type DashboardInvoices = InvoiceRow[];
```

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types
git commit -m "feat(frontend): domain TypeScript types"
```

---

### Task 4.2: services/api — типизированные функции

**Files:**
- Create: `frontend/src/services/api/projects.ts`
- Create: `frontend/src/services/api/materialClasses.ts`
- Create: `frontend/src/services/api/referencePrices.ts`
- Create: `frontend/src/services/api/invoices.ts`
- Create: `frontend/src/services/api/dashboard.ts`
- Create: `frontend/src/services/api/upload.ts`
- Create: `frontend/src/services/api/settings.ts`
- Create: `frontend/src/services/api/reports.ts`

- [ ] **Step 1: `projects.ts`**

```ts
import api from "@/lib/api";
import type {
  Project,
  ProjectCreateInput,
  ProjectUpdateInput,
} from "@/types/project";
import type { ID } from "@/types/common";

export const projectsApi = {
  async list(): Promise<Project[]> {
    const { data } = await api.get<Project[]>("/projects");
    return data;
  },
  async create(input: ProjectCreateInput): Promise<Project> {
    const { data } = await api.post<Project>("/projects", input);
    return data;
  },
  async update(id: ID, input: ProjectUpdateInput): Promise<Project> {
    const { data } = await api.put<Project>(`/projects/${id}`, input);
    return data;
  },
  async remove(id: ID): Promise<void> {
    await api.delete(`/projects/${id}`);
  },
};
```

- [ ] **Step 2: `materialClasses.ts`**

```ts
import api from "@/lib/api";
import type {
  MaterialClass,
  MaterialClassCreateInput,
} from "@/types/materialClass";
import type { ID } from "@/types/common";

export const materialClassesApi = {
  async list(): Promise<MaterialClass[]> {
    const { data } = await api.get<MaterialClass[]>("/material-classes");
    return data;
  },
  async create(input: MaterialClassCreateInput): Promise<MaterialClass> {
    const { data } = await api.post<MaterialClass>("/material-classes", input);
    return data;
  },
  async remove(id: ID): Promise<void> {
    await api.delete(`/material-classes/${id}`);
  },
};
```

- [ ] **Step 3: `referencePrices.ts`**

```ts
import api from "@/lib/api";
import type {
  ReferencePrice,
  ReferencePriceCreateInput,
} from "@/types/referencePrice";
import type { ID } from "@/types/common";

export const referencePricesApi = {
  async list(projectId?: ID): Promise<ReferencePrice[]> {
    const { data } = await api.get<ReferencePrice[]>("/reference-prices", {
      params: projectId ? { project_id: projectId } : undefined,
    });
    return data;
  },
  async create(input: ReferencePriceCreateInput): Promise<ReferencePrice> {
    const { data } = await api.post<ReferencePrice>("/reference-prices", input);
    return data;
  },
  async remove(id: ID): Promise<void> {
    await api.delete(`/reference-prices/${id}`);
  },
};
```

- [ ] **Step 4: `invoices.ts`**

```ts
import api from "@/lib/api";
import type {
  DocumentDetail,
  DocumentSummary,
  InvoiceRow,
  InvoiceUpdateInput,
} from "@/types/invoice";
import type { ID } from "@/types/common";

export const invoicesApi = {
  async listDocuments(projectId?: ID): Promise<DocumentSummary[]> {
    const { data } = await api.get<DocumentSummary[]>("/invoices/documents", {
      params: projectId ? { project_id: projectId } : undefined,
    });
    return data;
  },
  async getDocument(docId: ID): Promise<DocumentDetail> {
    const { data } = await api.get<DocumentDetail>(`/invoices/documents/${docId}`);
    return data;
  },
  documentPdfUrl(docId: ID): string {
    // axios baseURL уже = /api, но <img>/<iframe> не идут через axios:
    return `/api/invoices/documents/${docId}/pdf`;
  },
  async reparseDocument(docId: ID): Promise<DocumentDetail> {
    const { data } = await api.post<DocumentDetail>(
      `/invoices/documents/${docId}/reparse`
    );
    return data;
  },
  async update(invoiceId: ID, input: InvoiceUpdateInput): Promise<InvoiceRow> {
    const { data } = await api.put<InvoiceRow>(`/invoices/${invoiceId}`, input);
    return data;
  },
  async removeInvoice(invoiceId: ID): Promise<void> {
    await api.delete(`/invoices/${invoiceId}`);
  },
  async removeDocument(docId: ID): Promise<void> {
    await api.delete(`/invoices/documents/${docId}`);
  },
};
```

- [ ] **Step 5: `dashboard.ts`**

```ts
import api from "@/lib/api";
import type {
  AutoCalculateResponse,
  CalculateInput,
  DashboardCalculation,
  DashboardInvoices,
  DashboardSummary,
} from "@/types/dashboard";
import type { ID } from "@/types/common";

export const dashboardApi = {
  async summary(projectId: ID): Promise<DashboardSummary> {
    const { data } = await api.get<DashboardSummary>("/dashboard/summary", {
      params: { project_id: projectId },
    });
    return data;
  },
  async invoices(projectId: ID): Promise<DashboardInvoices> {
    const { data } = await api.get<DashboardInvoices>("/dashboard/invoices", {
      params: { project_id: projectId },
    });
    return data;
  },
  async calculations(projectId: ID): Promise<DashboardCalculation[]> {
    const { data } = await api.get<DashboardCalculation[]>(
      "/dashboard/calculations",
      { params: { project_id: projectId } }
    );
    return data;
  },
  async autoCalculate(projectId: ID): Promise<AutoCalculateResponse> {
    const { data } = await api.post<AutoCalculateResponse>(
      "/dashboard/auto-calculate",
      null,
      { params: { project_id: projectId } }
    );
    return data;
  },
  async calculate(input: CalculateInput): Promise<void> {
    const params: Record<string, string | number> = {
      project_id: input.project_id,
      period_start: input.period_start,
      period_end: input.period_end,
    };
    if (input.material_class_id) {
      params.material_class_id = input.material_class_id;
    }
    await api.post("/dashboard/calculate", null, { params });
  },
};
```

- [ ] **Step 6: `upload.ts`**

```ts
import api from "@/lib/api";
import type { DocumentDetail } from "@/types/invoice";
import type { ID } from "@/types/common";

export const uploadApi = {
  async uploadInvoice(
    projectId: ID,
    file: File,
    onProgress?: (pct: number) => void
  ): Promise<DocumentDetail> {
    const form = new FormData();
    form.append("file", file);
    form.append("project_id", String(projectId));
    const { data } = await api.post<DocumentDetail>("/invoices/upload", form, {
      headers: { "Content-Type": "multipart/form-data" },
      onUploadProgress: (e) => {
        if (onProgress && e.total) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      },
    });
    return data;
  },
};
```

- [ ] **Step 7: `settings.ts`**

```ts
import api from "@/lib/api";

export interface AppSettings {
  ai_provider: "openrouter" | "anthropic" | "off";
  ai_model: string;
  parse_threshold: number;
  // расширяется по мере добавления полей в backend
  [key: string]: unknown;
}

export const settingsApi = {
  async get(): Promise<AppSettings> {
    const { data } = await api.get<AppSettings>("/settings");
    return data;
  },
  async update(input: Partial<AppSettings>): Promise<AppSettings> {
    const { data } = await api.put<AppSettings>("/settings", input);
    return data;
  },
};
```

- [ ] **Step 8: `reports.ts`**

```ts
import api from "@/lib/api";
import type { ID, ISODate } from "@/types/common";

export interface ExcelExportInput {
  project_id: ID;
  period_start?: ISODate;
  period_end?: ISODate;
}

export const reportsApi = {
  async excelBlob(input: ExcelExportInput): Promise<Blob> {
    const { data } = await api.get<Blob>("/export/excel", {
      params: input,
      responseType: "blob",
    });
    return data;
  },
};
```

- [ ] **Step 9: Commit**

```bash
git add frontend/src/services
git commit -m "feat(frontend): typed services/api wrappers around axios"
```

---

### Task 4.3: react-query хуки и query keys

**Files:**
- Create: `frontend/src/services/queryKeys.ts`
- Create: `frontend/src/services/queries.ts`

- [ ] **Step 1: `queryKeys.ts`**

```ts
import type { ID } from "@/types/common";

export const qk = {
  projects: { all: ["projects"] as const },
  materialClasses: { all: ["material-classes"] as const },
  referencePrices: {
    all: (projectId?: ID) =>
      projectId ? (["reference-prices", projectId] as const) : (["reference-prices"] as const),
  },
  documents: {
    list: (projectId?: ID) =>
      projectId ? (["documents", projectId] as const) : (["documents"] as const),
    detail: (docId: ID) => ["document", docId] as const,
  },
  dashboard: {
    summary: (projectId: ID) => ["dashboard", "summary", projectId] as const,
    invoices: (projectId: ID) => ["dashboard", "invoices", projectId] as const,
    calculations: (projectId: ID) =>
      ["dashboard", "calculations", projectId] as const,
  },
  settings: { current: ["settings"] as const },
};
```

- [ ] **Step 2: `queries.ts` — фасад с готовыми хуками**

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { projectsApi } from "./api/projects";
import { materialClassesApi } from "./api/materialClasses";
import { referencePricesApi } from "./api/referencePrices";
import { invoicesApi } from "./api/invoices";
import { dashboardApi } from "./api/dashboard";
import { uploadApi } from "./api/upload";
import { settingsApi } from "./api/settings";
import { qk } from "./queryKeys";

import type { ID } from "@/types/common";
import type { ProjectCreateInput, ProjectUpdateInput } from "@/types/project";
import type { MaterialClassCreateInput } from "@/types/materialClass";
import type { ReferencePriceCreateInput } from "@/types/referencePrice";
import type { InvoiceUpdateInput } from "@/types/invoice";
import type { CalculateInput } from "@/types/dashboard";
import type { AppSettings } from "./api/settings";

// ========== Projects ==========
export function useProjects() {
  return useQuery({ queryKey: qk.projects.all, queryFn: projectsApi.list });
}

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: ProjectCreateInput) => projectsApi.create(input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.projects.all });
      toast.success("Объект создан");
    },
  });
}

export function useUpdateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: ID; input: ProjectUpdateInput }) =>
      projectsApi.update(id, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.projects.all });
      toast.success("Объект обновлён");
    },
  });
}

export function useDeleteProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: ID) => projectsApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.projects.all });
      toast.success("Объект удалён");
    },
  });
}

// ========== Material classes ==========
export function useMaterialClasses() {
  return useQuery({
    queryKey: qk.materialClasses.all,
    queryFn: materialClassesApi.list,
  });
}

export function useCreateMaterialClass() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: MaterialClassCreateInput) =>
      materialClassesApi.create(input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.materialClasses.all });
      toast.success("Класс материала добавлен");
    },
  });
}

export function useDeleteMaterialClass() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: ID) => materialClassesApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.materialClasses.all });
      toast.success("Класс материала удалён");
    },
  });
}

// ========== Reference prices ==========
export function useReferencePrices(projectId?: ID) {
  return useQuery({
    queryKey: qk.referencePrices.all(projectId),
    queryFn: () => referencePricesApi.list(projectId),
  });
}

export function useCreateReferencePrice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: ReferencePriceCreateInput) =>
      referencePricesApi.create(input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["reference-prices"] });
      toast.success("Эталон сохранён");
    },
  });
}

export function useDeleteReferencePrice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: ID) => referencePricesApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["reference-prices"] });
      toast.success("Эталон удалён");
    },
  });
}

// ========== Documents / Invoices ==========
export function useDocument(docId: ID | null | undefined) {
  return useQuery({
    queryKey: qk.documents.detail(docId ?? -1),
    queryFn: () => invoicesApi.getDocument(docId as ID),
    enabled: docId !== null && docId !== undefined,
  });
}

export function useReparseDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (docId: ID) => invoicesApi.reparseDocument(docId),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: qk.documents.detail(data.id) });
      toast.success("Документ переразобран");
    },
  });
}

export function useUpdateInvoice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: ID; input: InvoiceUpdateInput }) =>
      invoicesApi.update(id, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["document"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      toast.success("СФ сохранена");
    },
  });
}

export function useDeleteDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (docId: ID) => invoicesApi.removeDocument(docId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      toast.success("Документ удалён");
    },
  });
}

// ========== Dashboard ==========
export function useDashboardSummary(projectId: ID | null) {
  return useQuery({
    queryKey: projectId ? qk.dashboard.summary(projectId) : ["dashboard", "summary", "none"],
    queryFn: () => dashboardApi.summary(projectId as ID),
    enabled: projectId !== null,
  });
}

export function useDashboardInvoices(projectId: ID | null) {
  return useQuery({
    queryKey: projectId ? qk.dashboard.invoices(projectId) : ["dashboard", "invoices", "none"],
    queryFn: () => dashboardApi.invoices(projectId as ID),
    enabled: projectId !== null,
  });
}

export function useDashboardCalculations(projectId: ID | null) {
  return useQuery({
    queryKey: projectId
      ? qk.dashboard.calculations(projectId)
      : ["dashboard", "calculations", "none"],
    queryFn: () => dashboardApi.calculations(projectId as ID),
    enabled: projectId !== null,
  });
}

export function useAutoCalculate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (projectId: ID) => dashboardApi.autoCalculate(projectId),
    onSuccess: (_data, projectId) => {
      qc.invalidateQueries({ queryKey: qk.dashboard.calculations(projectId) });
    },
  });
}

export function useCalculate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CalculateInput) => dashboardApi.calculate(input),
    onSuccess: (_d, input) => {
      qc.invalidateQueries({ queryKey: qk.dashboard.calculations(input.project_id) });
      toast.success("Расчёт выполнен");
    },
  });
}

// ========== Upload ==========
export function useUploadInvoice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      projectId,
      file,
      onProgress,
    }: {
      projectId: ID;
      file: File;
      onProgress?: (pct: number) => void;
    }) => uploadApi.uploadInvoice(projectId, file, onProgress),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}

// ========== Settings ==========
export function useSettings() {
  return useQuery({ queryKey: qk.settings.current, queryFn: settingsApi.get });
}

export function useUpdateSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: Partial<AppSettings>) => settingsApi.update(input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.settings.current });
      toast.success("Настройки сохранены");
    },
  });
}
```

- [ ] **Step 3: Smoke-test**

Run:
```bash
cd frontend && npm run build
```

Expected: `tsc -b` проходит без ошибок типов, `vite build` собирает без проблем. Если есть ошибки — поправить.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/services
git commit -m "feat(frontend): react-query hooks + queryKeys facade

- Все мутации через useMutation с toast.success
- Глобальный onError из QueryClient ловит ошибки
- queryKeys фасад для консистентной инвалидации"
```

---

## Phase 5 — Dashboard

Это эталон-страница на новом языке. Калибруем плотность, отступы, скелетоны.

### Task 5.1: InvoiceTable (компонент таблицы СФ)

**Files:**
- Create: `frontend/src/components/invoices/InvoiceTable.tsx`

- [ ] **Step 1: Создать `frontend/src/components/invoices/InvoiceTable.tsx`**

```tsx
import { Link } from "react-router-dom";
import { AlertTriangle, FileEdit } from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui-domain/Button";
import { ConfidenceBadge } from "@/components/ui-domain/ConfidenceBadge";
import { MoneyCell } from "@/components/ui-domain/MoneyCell";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { InvoiceRow } from "@/types/invoice";

interface InvoiceTableProps {
  invoices: InvoiceRow[];
}

export function InvoiceTable({ invoices }: InvoiceTableProps) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Номер</TableHead>
          <TableHead>Дата</TableHead>
          <TableHead>Поставщик</TableHead>
          <TableHead>Позиции</TableHead>
          <TableHead className="text-right">Сумма</TableHead>
          <TableHead>ИИ</TableHead>
          <TableHead className="w-12"></TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {invoices.map((inv) => {
          const total = inv.items.reduce((s, it) => s + it.amount, 0);
          return (
            <TableRow
              key={inv.id}
              className={cn(
                "hover:bg-surface-hover",
                inv.has_issues && "bg-warning-soft"
              )}
            >
              <TableCell className="font-medium">
                <div className="flex items-center gap-1.5">
                  {inv.has_issues && (
                    <AlertTriangle
                      size={14}
                      className="text-warning"
                      aria-label="Требует проверки"
                    />
                  )}
                  {inv.number}
                </div>
              </TableCell>
              <TableCell className="text-fg-secondary">{formatDate(inv.date)}</TableCell>
              <TableCell>{inv.supplier_name || "—"}</TableCell>
              <TableCell className="max-w-md">
                <div className="space-y-0.5">
                  {inv.items.slice(0, 3).map((it, i) => (
                    <div key={i} className="truncate text-xs text-fg-secondary">
                      {it.material_class || it.item_type} ·{" "}
                      <span className="text-fg-tertiary">
                        {it.raw_name?.slice(0, 50)}
                        {(it.raw_name?.length ?? 0) > 50 ? "…" : ""}
                      </span>
                    </div>
                  ))}
                  {inv.items.length > 3 && (
                    <div className="text-xs text-fg-tertiary">
                      и ещё {inv.items.length - 3}
                    </div>
                  )}
                </div>
              </TableCell>
              <TableCell className="text-right">
                <MoneyCell value={total} />
              </TableCell>
              <TableCell>
                <ConfidenceBadge value={inv.ai_confidence} />
              </TableCell>
              <TableCell>
                <Link to={`/documents/${inv.document_id}`}>
                  <Button variant="ghost" size="sm" aria-label="Редактировать">
                    <FileEdit size={14} />
                  </Button>
                </Link>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/invoices/InvoiceTable.tsx
git commit -m "feat(invoices): InvoiceTable component"
```

---

### Task 5.2: Переписать `Dashboard.tsx`

**Files:**
- Modify: `frontend/src/pages/Dashboard.tsx` (полная замена)

- [ ] **Step 1: Полностью заменить содержимое `frontend/src/pages/Dashboard.tsx`**

```tsx
import { useMemo, useState } from "react";
import { Search, FolderOpen, Sigma } from "lucide-react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import { Button } from "@/components/ui-domain/Button";
import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Surface } from "@/components/ui-domain/Surface";
import { KpiCard } from "@/components/ui-domain/KpiCard";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { MoneyCell } from "@/components/ui-domain/MoneyCell";
import { DeviationCell } from "@/components/ui-domain/DeviationCell";
import { InvoiceTable } from "@/components/invoices/InvoiceTable";

import {
  useProjects,
  useMaterialClasses,
  useDashboardSummary,
  useDashboardInvoices,
  useDashboardCalculations,
  useAutoCalculate,
  useCalculate,
} from "@/services/queries";
import { formatNumber, formatMoney, formatDate } from "@/lib/format";
import type { ID } from "@/types/common";

export default function Dashboard() {
  const projectsQ = useProjects();
  const classesQ = useMaterialClasses();

  const [projectId, setProjectId] = useState<ID | null>(null);
  const [classId, setClassId] = useState<ID | null>(null);
  const [periodStart, setPeriodStart] = useState("");
  const [periodEnd, setPeriodEnd] = useState("");
  const [search, setSearch] = useState("");

  const summaryQ = useDashboardSummary(projectId);
  const invoicesQ = useDashboardInvoices(projectId);
  const calcsQ = useDashboardCalculations(projectId);
  const auto = useAutoCalculate();
  const calc = useCalculate();

  const handleProjectChange = async (val: string) => {
    const id = val ? Number(val) : null;
    setProjectId(id);
    if (id !== null) {
      try {
        const r = await auto.mutateAsync(id);
        if (r.period_start) setPeriodStart(r.period_start);
        if (r.period_end) setPeriodEnd(r.period_end);
      } catch {
        // ошибки уже обрабатываются глобальным onError мутаций
      }
    }
  };

  const filteredInvoices = useMemo(() => {
    const list = invoicesQ.data ?? [];
    if (!search.trim()) return list;
    const q = search.trim().toLowerCase();
    return list.filter(
      (inv) =>
        inv.number.toLowerCase().includes(q) ||
        (inv.supplier_name ?? "").toLowerCase().includes(q)
    );
  }, [invoicesQ.data, search]);

  return (
    <div className="container-page py-8">
      <PageHeader
        serif
        title="Аналитика"
        subtitle="Отклонения цен по объектам и периодам"
      />

      {/* Контекст: объект + класс материала */}
      <Surface className="mt-6">
        <div className="flex flex-wrap items-end gap-4">
          <div className="space-y-1.5">
            <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
              Объект *
            </Label>
            <Select
              value={projectId ? String(projectId) : ""}
              onValueChange={handleProjectChange}
            >
              <SelectTrigger className="w-[280px]">
                <SelectValue placeholder="Выберите объект" />
              </SelectTrigger>
              <SelectContent>
                {(projectsQ.data ?? []).map((p) => (
                  <SelectItem key={p.id} value={String(p.id)}>
                    {p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
              Класс материала
            </Label>
            <Select
              value={classId ? String(classId) : ""}
              onValueChange={(v) => setClassId(v ? Number(v) : null)}
            >
              <SelectTrigger className="w-[200px]">
                <SelectValue placeholder="Все классы" />
              </SelectTrigger>
              <SelectContent>
                {(classesQ.data ?? []).map((c) => (
                  <SelectItem key={c.id} value={String(c.id)}>
                    {c.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
              Период с
            </Label>
            <Input
              type="date"
              value={periodStart}
              onChange={(e) => setPeriodStart(e.target.value)}
              className="w-[160px]"
            />
          </div>

          <div className="space-y-1.5">
            <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
              По
            </Label>
            <Input
              type="date"
              value={periodEnd}
              onChange={(e) => setPeriodEnd(e.target.value)}
              className="w-[160px]"
            />
          </div>

          <Button
            onClick={() =>
              projectId &&
              periodStart &&
              periodEnd &&
              calc.mutate({
                project_id: projectId,
                material_class_id: classId ?? undefined,
                period_start: periodStart,
                period_end: periodEnd,
              })
            }
            disabled={
              !projectId || !periodStart || !periodEnd || calc.isPending
            }
            loading={calc.isPending}
          >
            Рассчитать
          </Button>
        </div>
      </Surface>

      {/* KPI */}
      {projectId && (
        <div className="mt-6 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
          {summaryQ.isLoading ? (
            <>
              <Skeleton className="h-[112px]" />
              <Skeleton className="h-[112px]" />
              <Skeleton className="h-[112px]" />
              <Skeleton className="h-[112px]" />
            </>
          ) : summaryQ.data ? (
            <>
              <KpiCard
                label="Документов"
                value={formatNumber(summaryQ.data.doc_count)}
              />
              <KpiCard
                label="Счетов-фактур"
                value={formatNumber(summaryQ.data.invoice_count)}
              />
              <KpiCard
                label="Объём, м³"
                value={formatNumber(summaryQ.data.total_qty)}
              />
              <KpiCard
                label="Сумма"
                value={formatMoney(summaryQ.data.total_amount)}
              />
            </>
          ) : null}
        </div>
      )}

      {/* Расчёты отклонений */}
      {projectId && (calcsQ.data ?? []).length > 0 && (
        <section className="mt-8">
          <h2 className="mb-3 font-serif text-xl font-medium text-fg">
            Отклонения от эталона
          </h2>
          <Surface padding="none">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Класс</TableHead>
                  <TableHead>Период</TableHead>
                  <TableHead className="text-right">Ср. цена</TableHead>
                  <TableHead className="text-right">Эталон</TableHead>
                  <TableHead className="text-right">Откл. %</TableHead>
                  <TableHead className="text-right">Откл. ₽</TableHead>
                  <TableHead className="text-right">Объём</TableHead>
                  <TableHead className="text-right">СФ</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(calcsQ.data ?? []).map((row, i) => (
                  <TableRow key={i}>
                    <TableCell className="font-medium">
                      {row.material_class_name}
                    </TableCell>
                    <TableCell className="text-fg-secondary">
                      {formatDate(row.period_start)} — {formatDate(row.period_end)}
                    </TableCell>
                    <TableCell className="text-right">
                      <MoneyCell value={row.avg_price} />
                    </TableCell>
                    <TableCell className="text-right">
                      <MoneyCell value={row.reference_price} />
                    </TableCell>
                    <TableCell className="text-right">
                      <DeviationCell value={row.deviation_pct} />
                    </TableCell>
                    <TableCell className="text-right">
                      <MoneyCell value={row.deviation_amount} />
                    </TableCell>
                    <TableCell className="text-right text-fg-secondary tabular-nums">
                      {formatNumber(row.total_qty)}
                    </TableCell>
                    <TableCell className="text-right text-fg-secondary tabular-nums">
                      {row.invoice_count}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Surface>
        </section>
      )}

      {/* Список СФ */}
      {projectId && (
        <section className="mt-8">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <h2 className="font-serif text-xl font-medium text-fg">
              Счета-фактуры
              {invoicesQ.data && (
                <span className="ml-2 text-sm font-normal text-fg-tertiary">
                  · {invoicesQ.data.length}
                </span>
              )}
            </h2>
            <div className="relative w-[280px]">
              <Search
                size={14}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-tertiary"
              />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Поиск по номеру или поставщику"
                className="w-full rounded-md border border-border-subtle bg-surface py-1.5 pl-9 pr-3 text-sm text-fg placeholder:text-fg-tertiary focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30"
              />
            </div>
          </div>

          {invoicesQ.isLoading ? (
            <Surface padding="none">
              <div className="space-y-1 p-2">
                <Skeleton className="h-10" />
                <Skeleton className="h-10" />
                <Skeleton className="h-10" />
              </div>
            </Surface>
          ) : (filteredInvoices.length === 0) ? (
            <EmptyState
              title={search ? "Ничего не найдено" : "Нет загруженных документов"}
              description={
                search
                  ? "Попробуйте изменить запрос."
                  : "Начните с загрузки счетов-фактур."
              }
              action={
                !search ? (
                  <a href="/upload">
                    <Button>Загрузить документ</Button>
                  </a>
                ) : undefined
              }
            />
          ) : (
            <Surface padding="none">
              <InvoiceTable invoices={filteredInvoices} />
            </Surface>
          )}
        </section>
      )}

      {/* Empty state: объект не выбран */}
      {!projectId && !projectsQ.isLoading && (
        <div className="mt-8">
          <EmptyState
            icon={<FolderOpen size={20} />}
            title="Выберите объект"
            description="Аналитика отображается по выбранному объекту. Выберите проект из списка выше или создайте новый."
            action={
              <a href="/projects">
                <Button variant="secondary">К списку объектов</Button>
              </a>
            }
          />
        </div>
      )}

      {/* Empty state: нет данных по проекту */}
      {projectId &&
        summaryQ.data &&
        summaryQ.data.invoice_count === 0 &&
        (invoicesQ.data ?? []).length === 0 && (
          <div className="mt-8">
            <EmptyState
              icon={<Sigma size={20} />}
              title="Нет данных по этому объекту"
              description="Загрузите счета-фактуры, чтобы увидеть аналитику."
              action={
                <a href="/upload">
                  <Button>Загрузить документ</Button>
                </a>
              }
            />
          </div>
        )}
    </div>
  );
}
```

- [ ] **Step 2: Запустить dev-сервер и проверить**

Run: `cd frontend && npm run dev`. Открыть `/`. Проверить:
- Page header «Аналитика» рисуется в serif.
- Селект объектов работает; при выборе объекта KPI-карточки показывают цифры (или скелетоны при загрузке).
- Селекты периода/класса работают, кнопка «Рассчитать» доступна когда заполнены поля.
- При успешном расчёте появляется тост «Расчёт выполнен», таблица отклонений обновляется.
- Поиск по СФ фильтрует таблицу клиент-сайд.
- Без выбранного объекта — `EmptyState` с CTA «К списку объектов».
- Светлая/тёмная тема — обе работают, KPI-цифры читаются на обеих.

Остановить dev-сервер.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Dashboard.tsx
git commit -m "feat(dashboard): rewrite Dashboard on new design system

- PageHeader (serif), Surface для контекст-блока
- KPI: 4 KpiCard со скелетонами
- Таблица отклонений: Surface + DeviationCell + MoneyCell
- Таблица СФ: InvoiceTable с поиском, ConfidenceBadge
- EmptyState для не-выбранного и пустого объекта
- react-query для всех данных, тосты на расчёт"
```

---

## Phase 6 — Upload

### Task 6.1: Переписать `Upload.tsx` на Dropzone + прогресс

**Files:**
- Modify: `frontend/src/pages/Upload.tsx` (полная замена)

- [ ] **Step 1: Полностью заменить `frontend/src/pages/Upload.tsx`**

```tsx
import { useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, AlertTriangle, Loader2, FileText } from "lucide-react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Label } from "@/components/ui/label";

import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Surface } from "@/components/ui-domain/Surface";
import { Dropzone } from "@/components/ui-domain/Dropzone";
import { StatusPill } from "@/components/ui-domain/StatusPill";
import { ConfidenceBadge } from "@/components/ui-domain/ConfidenceBadge";
import { Button } from "@/components/ui-domain/Button";
import { EmptyState } from "@/components/ui-domain/EmptyState";

import { useProjects, useUploadInvoice } from "@/services/queries";
import type { ID } from "@/types/common";
import type { DocumentDetail } from "@/types/invoice";

interface JobState {
  id: string;
  file: File;
  status: "pending" | "uploading" | "ready" | "error";
  progress: number;
  result?: DocumentDetail;
  error?: string;
}

export default function UploadPage() {
  const projectsQ = useProjects();
  const upload = useUploadInvoice();

  const [projectId, setProjectId] = useState<ID | null>(null);
  const [jobs, setJobs] = useState<JobState[]>([]);

  const handleDrop = async (files: File[]) => {
    if (!projectId) return;
    const newJobs: JobState[] = files.map((f, i) => ({
      id: `${Date.now()}-${i}-${f.name}`,
      file: f,
      status: "pending",
      progress: 0,
    }));
    setJobs((prev) => [...newJobs, ...prev]);

    for (const job of newJobs) {
      setJobs((prev) =>
        prev.map((j) => (j.id === job.id ? { ...j, status: "uploading" } : j))
      );
      try {
        const result = await upload.mutateAsync({
          projectId,
          file: job.file,
          onProgress: (pct) =>
            setJobs((prev) =>
              prev.map((j) => (j.id === job.id ? { ...j, progress: pct } : j))
            ),
        });
        setJobs((prev) =>
          prev.map((j) =>
            j.id === job.id ? { ...j, status: "ready", result, progress: 100 } : j
          )
        );
      } catch (err) {
        setJobs((prev) =>
          prev.map((j) =>
            j.id === job.id
              ? {
                  ...j,
                  status: "error",
                  error: err instanceof Error ? err.message : "Ошибка загрузки",
                }
              : j
          )
        );
      }
    }
  };

  return (
    <div className="container-page py-8">
      <PageHeader
        serif
        title="Загрузка документов"
        subtitle="Перетащите счета-фактуры или УПД — система распарсит позиции автоматически"
      />

      {/* Контекст: объект */}
      <Surface className="mt-6">
        <div className="flex items-end gap-4">
          <div className="space-y-1.5">
            <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
              Объект *
            </Label>
            <Select
              value={projectId ? String(projectId) : ""}
              onValueChange={(v) => setProjectId(v ? Number(v) : null)}
            >
              <SelectTrigger className="w-[320px]">
                <SelectValue placeholder="Выберите объект" />
              </SelectTrigger>
              <SelectContent>
                {(projectsQ.data ?? []).map((p) => (
                  <SelectItem key={p.id} value={String(p.id)}>
                    {p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      </Surface>

      {/* Dropzone */}
      <div className="mt-4">
        {projectId ? (
          <Dropzone
            onDrop={handleDrop}
            multiple
            accept={{
              "application/pdf": [".pdf"],
              "image/jpeg": [".jpg", ".jpeg"],
              "image/png": [".png"],
            }}
          />
        ) : (
          <EmptyState
            title="Сначала выберите объект"
            description="К объекту привязываются загружаемые документы."
          />
        )}
      </div>

      {/* Список заданий */}
      {jobs.length > 0 && (
        <section className="mt-8">
          <h2 className="mb-3 font-serif text-xl font-medium text-fg">
            История загрузки
          </h2>
          <div className="space-y-2">
            {jobs.map((j) => (
              <Surface key={j.id} padding="sm">
                <div className="flex items-start gap-4">
                  <div className="grid h-9 w-9 shrink-0 place-items-center rounded-md bg-surface-sunken">
                    {j.status === "uploading" && (
                      <Loader2 size={16} className="animate-spin text-accent" />
                    )}
                    {j.status === "ready" && (
                      <CheckCircle2 size={16} className="text-accent" />
                    )}
                    {j.status === "error" && (
                      <AlertTriangle size={16} className="text-danger" />
                    )}
                    {j.status === "pending" && (
                      <FileText size={16} className="text-fg-tertiary" />
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium text-fg">
                        {j.file.name}
                      </span>
                      {j.status === "uploading" && (
                        <StatusPill tone="info" label={`${j.progress}%`} />
                      )}
                      {j.status === "ready" && (
                        <StatusPill tone="success" label="готово" dot />
                      )}
                      {j.status === "error" && (
                        <StatusPill tone="danger" label="ошибка" dot />
                      )}
                    </div>
                    {j.result && (
                      <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-fg-secondary">
                        {j.result.invoices.map((inv) => (
                          <span key={inv.id} className="flex items-center gap-1.5">
                            СФ № {inv.number} · {inv.items.length} позиций
                            <ConfidenceBadge value={inv.ai_confidence} />
                          </span>
                        ))}
                      </div>
                    )}
                    {j.error && (
                      <div className="mt-1 text-xs text-danger-text">{j.error}</div>
                    )}
                  </div>
                  {j.result && (
                    <Link to={`/documents/${j.result.id}`}>
                      <Button variant="secondary" size="sm">
                        Проверить
                      </Button>
                    </Link>
                  )}
                </div>
              </Surface>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Smoke-test**

Run dev-сервер. На `/upload`:
- Выбрать объект → Dropzone становится активной.
- Drag & drop PDF — должен начать загрузку, прогресс растёт.
- По завершении — карточка с «готово», `ConfidenceBadge`, кнопкой «Проверить».
- Drag нескольких файлов — обрабатываются последовательно.
- При ошибке (например, при отключённом бэкенде) — `StatusPill ошибка` + текст ошибки + тост.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Upload.tsx
git commit -m "feat(upload): rewrite Upload with Dropzone, progress, job history"
```

---

## Phase 7 — Review (двухколоночный экран проверки СФ)

### Task 7.1: Подкомпоненты Review

**Files:**
- Create: `frontend/src/components/review/ReviewHeader.tsx`
- Create: `frontend/src/components/review/ReviewItemsTable.tsx`
- Create: `frontend/src/components/review/ReviewIssues.tsx`

- [ ] **Step 1: `ReviewHeader.tsx` — шапка СФ с полями для редактирования**

```tsx
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { InvoiceRow } from "@/types/invoice";

interface ReviewHeaderProps {
  invoice: InvoiceRow;
  onChange: (patch: Partial<InvoiceRow>) => void;
}

export function ReviewHeader({ invoice, onChange }: ReviewHeaderProps) {
  return (
    <div className="grid grid-cols-2 gap-4">
      <Field label="Номер СФ">
        <Input
          value={invoice.number}
          onChange={(e) => onChange({ number: e.target.value })}
        />
      </Field>
      <Field label="Дата">
        <Input
          type="date"
          value={invoice.date}
          onChange={(e) => onChange({ date: e.target.value })}
        />
      </Field>
      <Field label="Поставщик">
        <Input
          value={invoice.supplier_name ?? ""}
          onChange={(e) => onChange({ supplier_name: e.target.value })}
        />
      </Field>
      <Field label="ИНН">
        <Input
          value={invoice.supplier_inn ?? ""}
          onChange={(e) => onChange({ supplier_inn: e.target.value })}
        />
      </Field>
      <Field label="Ставка НДС, %">
        <Input
          type="number"
          step="0.01"
          value={invoice.vat_rate}
          onChange={(e) =>
            onChange({ vat_rate: Number(e.target.value) || 0 })
          }
        />
      </Field>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
        {label}
      </Label>
      {children}
    </div>
  );
}
```

- [ ] **Step 2: `ReviewItemsTable.tsx`**

```tsx
import { Trash2 } from "lucide-react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui-domain/Button";
import { MoneyCell } from "@/components/ui-domain/MoneyCell";
import { useMaterialClasses } from "@/services/queries";
import type { InvoiceItem } from "@/types/invoice";

interface ReviewItemsTableProps {
  items: InvoiceItem[];
  onChange: (items: InvoiceItem[]) => void;
}

export function ReviewItemsTable({ items, onChange }: ReviewItemsTableProps) {
  const classes = useMaterialClasses();

  const update = (idx: number, patch: Partial<InvoiceItem>) => {
    onChange(items.map((it, i) => (i === idx ? { ...it, ...patch } : it)));
  };
  const remove = (idx: number) => {
    onChange(items.filter((_, i) => i !== idx));
  };

  return (
    <div className="space-y-2">
      {items.map((it, i) => (
        <div
          key={i}
          className="grid grid-cols-12 gap-2 rounded-md border border-border-subtle bg-surface p-2"
        >
          <div className="col-span-4">
            <Input
              value={it.raw_name}
              onChange={(e) => update(i, { raw_name: e.target.value })}
              placeholder="Наименование"
            />
          </div>
          <div className="col-span-2">
            <Select
              value={it.material_class_id ? String(it.material_class_id) : ""}
              onValueChange={(v) =>
                update(i, { material_class_id: v ? Number(v) : null })
              }
            >
              <SelectTrigger>
                <SelectValue placeholder="Класс" />
              </SelectTrigger>
              <SelectContent>
                {(classes.data ?? []).map((c) => (
                  <SelectItem key={c.id} value={String(c.id)}>
                    {c.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="col-span-1">
            <Input
              type="number"
              value={it.quantity}
              onChange={(e) =>
                update(i, { quantity: Number(e.target.value) || 0 })
              }
            />
          </div>
          <div className="col-span-1">
            <Input
              value={it.unit}
              onChange={(e) => update(i, { unit: e.target.value })}
              placeholder="ед."
            />
          </div>
          <div className="col-span-2">
            <Input
              type="number"
              value={it.unit_price}
              onChange={(e) =>
                update(i, { unit_price: Number(e.target.value) || 0 })
              }
              placeholder="цена"
            />
          </div>
          <div className="col-span-1 flex items-center justify-end pr-1 text-sm">
            <MoneyCell value={it.amount} />
          </div>
          <div className="col-span-1 flex items-center justify-end">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => remove(i)}
              aria-label="Удалить позицию"
            >
              <Trash2 size={14} />
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 3: `ReviewIssues.tsx`**

```tsx
import { AlertTriangle, CheckCircle2 } from "lucide-react";
import type { InvoiceRow } from "@/types/invoice";

interface ReviewIssuesProps {
  invoice: InvoiceRow;
}

export function ReviewIssues({ invoice }: ReviewIssuesProps) {
  const issues: string[] = [];
  if ((invoice.ai_confidence ?? 0) < 0.7) {
    issues.push("Низкая уверенность ИИ — проверьте все поля вручную.");
  }
  if (!invoice.supplier_name) issues.push("Не указан поставщик.");
  if (!invoice.number) issues.push("Не указан номер СФ.");
  if (invoice.items.length === 0) issues.push("Нет ни одной позиции.");
  invoice.items.forEach((it, i) => {
    if (!it.raw_name) issues.push(`Позиция ${i + 1}: пустое наименование.`);
    if (it.item_type === "material" && !it.material_class) {
      issues.push(`Позиция ${i + 1}: не определён класс материала.`);
    }
  });

  if (issues.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-md bg-accent-soft p-3 text-accent-text">
        <CheckCircle2 size={16} />
        <span className="text-sm">Замечаний не найдено.</span>
      </div>
    );
  }

  return (
    <ul className="space-y-1.5">
      {issues.map((msg, i) => (
        <li
          key={i}
          className="flex items-start gap-2 rounded-md border border-warning-border bg-warning-soft p-3 text-sm text-warning-text"
        >
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span>{msg}</span>
        </li>
      ))}
    </ul>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/review
git commit -m "feat(review): ReviewHeader, ReviewItemsTable, ReviewIssues components"
```

---

### Task 7.2: Переписать `Review.tsx` на двухколоночный layout

**Files:**
- Modify: `frontend/src/pages/Review.tsx` (полная замена)

- [ ] **Step 1: Полностью заменить `frontend/src/pages/Review.tsx`**

```tsx
import { useState, useMemo, useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Surface } from "@/components/ui-domain/Surface";
import { Tabs } from "@/components/ui-domain/Tabs";
import { Breadcrumbs } from "@/components/ui-domain/Breadcrumbs";
import { Button } from "@/components/ui-domain/Button";
import { StatusPill } from "@/components/ui-domain/StatusPill";
import { ConfidenceBadge } from "@/components/ui-domain/ConfidenceBadge";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";

import { ReviewHeader } from "@/components/review/ReviewHeader";
import { ReviewItemsTable } from "@/components/review/ReviewItemsTable";
import { ReviewIssues } from "@/components/review/ReviewIssues";

import {
  useDocument,
  useUpdateInvoice,
  useReparseDocument,
  useDeleteDocument,
} from "@/services/queries";
import { invoicesApi } from "@/services/api/invoices";
import { formatDate } from "@/lib/format";
import type { InvoiceRow } from "@/types/invoice";

type TabKey = "header" | "items" | "issues";

export default function Review() {
  const { id } = useParams<{ id: string }>();
  const docId = id ? Number(id) : null;
  const navigate = useNavigate();

  const docQ = useDocument(docId);
  const update = useUpdateInvoice();
  const reparse = useReparseDocument();
  const remove = useDeleteDocument();

  const [tab, setTab] = useState<TabKey>("header");
  const [draft, setDraft] = useState<InvoiceRow | null>(null);

  // Загрузить первый СФ документа в draft при первом получении
  useEffect(() => {
    const inv = docQ.data?.invoices[0];
    if (inv && (!draft || draft.id !== inv.id)) {
      setDraft(inv);
    }
  }, [docQ.data, draft]);

  const dirty = useMemo(() => {
    const inv = docQ.data?.invoices[0];
    if (!draft || !inv) return false;
    return JSON.stringify(draft) !== JSON.stringify(inv);
  }, [draft, docQ.data]);

  if (docId === null) {
    return (
      <div className="container-page py-8">
        <EmptyState title="Документ не найден" />
      </div>
    );
  }

  if (docQ.isLoading) {
    return (
      <div className="container-page py-8 space-y-4">
        <Skeleton className="h-8 w-1/3" />
        <Skeleton className="h-[400px]" />
      </div>
    );
  }

  if (!docQ.data || !draft) {
    return (
      <div className="container-page py-8">
        <EmptyState title="Документ не найден" />
      </div>
    );
  }

  const doc = docQ.data;
  const inv = draft;

  const tabs: Array<{ value: TabKey; label: string }> = [
    { value: "header", label: "Шапка" },
    { value: "items", label: `Позиции · ${inv.items.length}` },
    { value: "issues", label: "Проблемы" },
  ];

  return (
    <div className="container-page py-6">
      <Breadcrumbs
        items={[
          { label: "Дашборд", to: "/" },
          { label: doc.filename },
          { label: `СФ № ${inv.number || "—"}` },
        ]}
      />

      <PageHeader
        title={`СФ № ${inv.number || "—"} от ${formatDate(inv.date)}`}
        subtitle={inv.supplier_name ?? "Поставщик не указан"}
        actions={
          <>
            <ConfidenceBadge value={inv.ai_confidence} />
            <StatusPill
              tone={inv.has_issues ? "warning" : "success"}
              label={inv.has_issues ? "требует проверки" : "готово"}
              dot
            />
          </>
        }
      />

      {/* Двухколоночный layout */}
      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Левая колонка — превью документа */}
        <div className="lg:sticky lg:top-20 lg:self-start">
          <Surface padding="none" className="overflow-hidden">
            <iframe
              title="Документ"
              src={invoicesApi.documentPdfUrl(docId)}
              className="h-[80vh] w-full border-0 bg-surface-sunken"
            />
          </Surface>
          <div className="mt-2 flex items-center justify-between text-xs text-fg-tertiary">
            <span>{doc.filename}</span>
            <button
              type="button"
              onClick={() => reparse.mutate(docId)}
              disabled={reparse.isPending}
              className="text-fg-secondary underline-offset-2 hover:text-fg hover:underline disabled:opacity-50"
            >
              Переразобрать
            </button>
          </div>
        </div>

        {/* Правая колонка — редактирование */}
        <div>
          <Tabs<TabKey> value={tab} onValueChange={setTab} tabs={tabs}>
            {tab === "header" && (
              <Surface>
                <ReviewHeader
                  invoice={inv}
                  onChange={(patch) => setDraft({ ...inv, ...patch })}
                />
              </Surface>
            )}
            {tab === "items" && (
              <ReviewItemsTable
                items={inv.items}
                onChange={(items) => setDraft({ ...inv, items })}
              />
            )}
            {tab === "issues" && (
              <Surface>
                <ReviewIssues invoice={inv} />
              </Surface>
            )}
          </Tabs>
        </div>
      </div>

      {/* Sticky-bar внизу */}
      <div className="sticky bottom-0 -mx-6 mt-8 border-t border-border-subtle bg-surface/95 px-6 py-3 backdrop-blur">
        <div className="container-page flex items-center justify-between">
          <Button
            variant="ghost"
            leftIcon={<ArrowLeft size={14} />}
            onClick={() => navigate(-1)}
          >
            Назад
          </Button>
          <div className="flex items-center gap-2">
            <Button
              variant="danger"
              size="sm"
              onClick={() => {
                if (window.confirm("Удалить документ?")) {
                  remove.mutate(docId, {
                    onSuccess: () => navigate("/"),
                  });
                }
              }}
            >
              Удалить
            </Button>
            <Button
              variant="secondary"
              disabled={!dirty || update.isPending}
              loading={update.isPending}
              onClick={() => update.mutate({ id: inv.id, input: inv })}
            >
              Сохранить
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Smoke-test**

Открыть существующий документ через клик из таблицы СФ на дашборде. Проверить:
- Слева отображается PDF/изображение в `<iframe>`.
- Справа табы Шапка/Позиции/Проблемы переключаются.
- Изменение полей делает кнопку «Сохранить» активной.
- При сохранении — тост «СФ сохранена», query инвалидируется.
- «Переразобрать» вызывает реparse, возвращает обновлённые данные.
- «Удалить» с подтверждением удаляет и редиректит на `/`.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Review.tsx
git commit -m "feat(review): two-column layout with PDF preview, tabs, sticky save bar"
```

---

## Phase 8 — Projects: грид карточек

### Task 8.1: ProjectCard

**Files:**
- Create: `frontend/src/components/projects/ProjectCard.tsx`

- [ ] **Step 1: Создать `frontend/src/components/projects/ProjectCard.tsx`**

```tsx
import { Link } from "react-router-dom";
import { Building2 } from "lucide-react";
import { formatDate } from "@/lib/format";
import type { Project } from "@/types/project";

interface ProjectCardProps {
  project: Project;
}

export function ProjectCard({ project }: ProjectCardProps) {
  return (
    <Link
      to={`/projects/${project.id}`}
      className="group flex flex-col rounded-lg border border-border-subtle bg-surface px-5 py-4 transition-colors duration-150 hover:border-border-default hover:bg-surface-hover"
    >
      <div className="flex items-start gap-3">
        <div
          className="grid h-10 w-10 shrink-0 place-items-center rounded-md bg-accent-soft text-accent-text"
          aria-hidden
        >
          <Building2 size={18} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-md font-medium text-fg">
            {project.name}
          </div>
          <div className="mt-0.5 truncate text-xs text-fg-secondary">
            {project.contract_number
              ? `Договор № ${project.contract_number}`
              : "Договор не указан"}
          </div>
        </div>
      </div>
      <div className="mt-4 border-t border-border-subtle pt-3 text-xs text-fg-tertiary">
        Создан {formatDate(project.created_at)}
      </div>
    </Link>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/projects/ProjectCard.tsx
git commit -m "feat(projects): ProjectCard"
```

---

### Task 8.2: Переписать `Projects.tsx` + диалог создания

**Files:**
- Modify: `frontend/src/pages/Projects.tsx` (полная замена)

- [ ] **Step 1: Полностью заменить `frontend/src/pages/Projects.tsx`**

```tsx
import { useMemo, useState } from "react";
import { Plus, Search, Building2 } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { Button } from "@/components/ui-domain/Button";
import { PageHeader } from "@/components/ui-domain/PageHeader";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { ProjectCard } from "@/components/projects/ProjectCard";

import { useProjects, useCreateProject } from "@/services/queries";

export default function Projects() {
  const projectsQ = useProjects();
  const create = useCreateProject();

  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [contract, setContract] = useState("");

  const filtered = useMemo(() => {
    const list = projectsQ.data ?? [];
    if (!search.trim()) return list;
    const q = search.trim().toLowerCase();
    return list.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        (p.contract_number ?? "").toLowerCase().includes(q)
    );
  }, [projectsQ.data, search]);

  const submit = () => {
    if (!name.trim()) return;
    create.mutate(
      { name: name.trim(), contract_number: contract.trim() || null },
      {
        onSuccess: () => {
          setOpen(false);
          setName("");
          setContract("");
        },
      }
    );
  };

  return (
    <div className="container-page py-8">
      <PageHeader
        serif
        title="Объекты"
        subtitle={
          (projectsQ.data ?? []).length > 0
            ? `${(projectsQ.data ?? []).length} объектов в портфеле`
            : "Здесь появится ваш портфель объектов"
        }
        actions={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button leftIcon={<Plus size={14} />}>Новый объект</Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Создать объект</DialogTitle>
              </DialogHeader>
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                    Название *
                  </Label>
                  <Input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="ЖК «Северный», корпус 1"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                    Номер договора
                  </Label>
                  <Input
                    value={contract}
                    onChange={(e) => setContract(e.target.value)}
                    placeholder="Опционально"
                  />
                </div>
              </div>
              <DialogFooter>
                <Button variant="ghost" onClick={() => setOpen(false)}>
                  Отмена
                </Button>
                <Button
                  onClick={submit}
                  loading={create.isPending}
                  disabled={!name.trim()}
                >
                  Создать
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        }
      />

      <div className="mt-6 relative w-full max-w-md">
        <Search
          size={14}
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-tertiary"
        />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Поиск по названию или договору"
          className="w-full rounded-md border border-border-subtle bg-surface py-2 pl-9 pr-3 text-sm text-fg placeholder:text-fg-tertiary focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30"
        />
      </div>

      <div className="mt-6">
        {projectsQ.isLoading ? (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-[120px]" />
            ))}
          </div>
        ) : (projectsQ.data ?? []).length === 0 ? (
          <EmptyState
            icon={<Building2 size={20} />}
            title="Создайте первый объект"
            description="Объект — это контейнер для договоров и счетов-фактур. С него начинается работа в УПД Трекере."
            action={
              <Button leftIcon={<Plus size={14} />} onClick={() => setOpen(true)}>
                Новый объект
              </Button>
            }
          />
        ) : filtered.length === 0 ? (
          <EmptyState
            title="Ничего не найдено"
            description="Попробуйте изменить запрос."
          />
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {filtered.map((p) => (
              <ProjectCard key={p.id} project={p} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/pages/Projects.tsx
git commit -m "feat(projects): grid of ProjectCards with search and create dialog"
```

---

### Task 8.3: ProjectPage (новая страница)

**Files:**
- Create: `frontend/src/pages/ProjectPage.tsx`
- Modify: `frontend/src/App.tsx` (роут уже есть, проверить)

- [ ] **Step 1: Создать `frontend/src/pages/ProjectPage.tsx`**

```tsx
import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

import { Breadcrumbs } from "@/components/ui-domain/Breadcrumbs";
import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Surface } from "@/components/ui-domain/Surface";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Button } from "@/components/ui-domain/Button";
import { InvoiceTable } from "@/components/invoices/InvoiceTable";

import {
  useProjects,
  useDashboardInvoices,
  useDashboardSummary,
} from "@/services/queries";
import { formatDate, formatMoney, formatNumber } from "@/lib/format";
import { KpiCard } from "@/components/ui-domain/KpiCard";

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const projectId = id ? Number(id) : null;

  const projectsQ = useProjects();
  const project = projectsQ.data?.find((p) => p.id === projectId) ?? null;

  const summaryQ = useDashboardSummary(projectId);
  const invoicesQ = useDashboardInvoices(projectId);

  if (projectsQ.isLoading) {
    return (
      <div className="container-page py-8 space-y-4">
        <Skeleton className="h-8 w-1/3" />
        <Skeleton className="h-[120px]" />
      </div>
    );
  }

  if (!project) {
    return (
      <div className="container-page py-8">
        <EmptyState
          title="Объект не найден"
          action={
            <Link to="/projects">
              <Button variant="secondary" leftIcon={<ArrowLeft size={14} />}>
                К списку объектов
              </Button>
            </Link>
          }
        />
      </div>
    );
  }

  return (
    <div className="container-page py-8">
      <Breadcrumbs
        items={[
          { label: "Объекты", to: "/projects" },
          { label: project.name },
        ]}
      />
      <PageHeader
        serif
        title={project.name}
        subtitle={
          project.contract_number
            ? `Договор № ${project.contract_number} · создан ${formatDate(project.created_at)}`
            : `Создан ${formatDate(project.created_at)}`
        }
      />

      {summaryQ.data && (
        <div className="mt-6 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
          <KpiCard label="Документов" value={formatNumber(summaryQ.data.doc_count)} />
          <KpiCard label="СФ" value={formatNumber(summaryQ.data.invoice_count)} />
          <KpiCard label="Объём, м³" value={formatNumber(summaryQ.data.total_qty)} />
          <KpiCard label="Сумма" value={formatMoney(summaryQ.data.total_amount)} />
        </div>
      )}

      <section className="mt-8">
        <h2 className="mb-3 font-serif text-xl font-medium text-fg">
          Счета-фактуры
        </h2>
        {invoicesQ.isLoading ? (
          <Surface padding="none">
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
          </Surface>
        ) : (invoicesQ.data ?? []).length === 0 ? (
          <EmptyState
            title="Нет счетов-фактур"
            description="Загрузите документы, чтобы они появились здесь."
            action={
              <Link to="/upload">
                <Button>Загрузить документ</Button>
              </Link>
            }
          />
        ) : (
          <Surface padding="none">
            <InvoiceTable invoices={invoicesQ.data ?? []} />
          </Surface>
        )}
      </section>
    </div>
  );
}
```

- [ ] **Step 2: Подключить роут в `App.tsx`**

В `frontend/src/App.tsx` после строки `import Projects from "@/pages/Projects";` добавить:

```tsx
import ProjectPage from "@/pages/ProjectPage";
```

И в блоке `<Routes>` после `<Route path="/projects" element={<Projects />} />` добавить:

```tsx
<Route path="/projects/:id" element={<ProjectPage />} />
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/ProjectPage.tsx frontend/src/App.tsx
git commit -m "feat(projects): ProjectPage with KPI and invoices"
```

---

## Phase 9 — MaterialClasses + ReferencePrices

### Task 9.1: Переписать `MaterialClasses.tsx`

**Files:**
- Modify: `frontend/src/pages/MaterialClasses.tsx` (полная замена)

- [ ] **Step 1: Полностью заменить `frontend/src/pages/MaterialClasses.tsx`**

```tsx
import { useState } from "react";
import { Plus, Trash2, Layers } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import { Button } from "@/components/ui-domain/Button";
import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Surface } from "@/components/ui-domain/Surface";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { StatusPill } from "@/components/ui-domain/StatusPill";

import {
  useMaterialClasses,
  useCreateMaterialClass,
  useDeleteMaterialClass,
} from "@/services/queries";
import { formatDate } from "@/lib/format";

const TYPE_LABELS: Record<string, string> = {
  concrete: "Бетон",
  rebar: "Арматура",
  other: "Прочее",
};

export default function MaterialClasses() {
  const list = useMaterialClasses();
  const create = useCreateMaterialClass();
  const remove = useDeleteMaterialClass();

  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [type, setType] = useState("concrete");

  const submit = () => {
    if (!name.trim()) return;
    create.mutate(
      { name: name.trim(), material_type: type },
      {
        onSuccess: () => {
          setOpen(false);
          setName("");
          setType("concrete");
        },
      }
    );
  };

  return (
    <div className="container-page py-8">
      <PageHeader
        serif
        title="Классы материалов"
        subtitle="Группы для агрегации цен и расчёта отклонений"
        actions={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button leftIcon={<Plus size={14} />}>Добавить класс</Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Новый класс материала</DialogTitle>
              </DialogHeader>
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                    Тип материала
                  </Label>
                  <Select value={type} onValueChange={setType}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="concrete">Бетон</SelectItem>
                      <SelectItem value="rebar">Арматура</SelectItem>
                      <SelectItem value="other">Прочее</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                    Название класса *
                  </Label>
                  <Input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="например, В25, А500С"
                  />
                </div>
              </div>
              <DialogFooter>
                <Button variant="ghost" onClick={() => setOpen(false)}>
                  Отмена
                </Button>
                <Button
                  onClick={submit}
                  loading={create.isPending}
                  disabled={!name.trim()}
                >
                  Добавить
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        }
      />

      <div className="mt-6">
        {list.isLoading ? (
          <Surface padding="none">
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
          </Surface>
        ) : (list.data ?? []).length === 0 ? (
          <EmptyState
            icon={<Layers size={20} />}
            title="Нет классов материалов"
            description="Добавьте первый класс — например, бетон В25."
            action={
              <Button leftIcon={<Plus size={14} />} onClick={() => setOpen(true)}>
                Добавить класс
              </Button>
            }
          />
        ) : (
          <Surface padding="none">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Название</TableHead>
                  <TableHead>Тип</TableHead>
                  <TableHead>Создан</TableHead>
                  <TableHead className="w-12"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(list.data ?? []).map((c) => (
                  <TableRow key={c.id}>
                    <TableCell className="font-medium">{c.name}</TableCell>
                    <TableCell>
                      <StatusPill
                        tone="neutral"
                        label={TYPE_LABELS[c.material_type] ?? c.material_type}
                      />
                    </TableCell>
                    <TableCell className="text-fg-secondary">
                      {formatDate(c.created_at)}
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          if (window.confirm(`Удалить «${c.name}»?`)) {
                            remove.mutate(c.id);
                          }
                        }}
                        aria-label="Удалить"
                      >
                        <Trash2 size={14} />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Surface>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/pages/MaterialClasses.tsx
git commit -m "feat(material-classes): rewrite with PageHeader, Surface, dialog"
```

---

### Task 9.2: Переписать `ReferencePrices.tsx`

**Files:**
- Modify: `frontend/src/pages/ReferencePrices.tsx` (полная замена)

- [ ] **Step 1: Полностью заменить `frontend/src/pages/ReferencePrices.tsx`**

```tsx
import { useMemo, useState } from "react";
import { Plus, Trash2, Target } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import { Button } from "@/components/ui-domain/Button";
import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Surface } from "@/components/ui-domain/Surface";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { MoneyCell } from "@/components/ui-domain/MoneyCell";

import {
  useProjects,
  useMaterialClasses,
  useReferencePrices,
  useCreateReferencePrice,
  useDeleteReferencePrice,
} from "@/services/queries";
import { formatDate } from "@/lib/format";
import type { ID } from "@/types/common";

export default function ReferencePrices() {
  const projectsQ = useProjects();
  const classesQ = useMaterialClasses();

  const [filterProject, setFilterProject] = useState<ID | null>(null);
  const list = useReferencePrices(filterProject ?? undefined);
  const create = useCreateReferencePrice();
  const remove = useDeleteReferencePrice();

  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    project_id: "",
    material_class_id: "",
    price: "",
    period_start: "",
    period_end: "",
    source: "",
  });

  const reset = () =>
    setForm({
      project_id: "",
      material_class_id: "",
      price: "",
      period_start: "",
      period_end: "",
      source: "",
    });

  const canSubmit =
    form.project_id &&
    form.material_class_id &&
    form.price &&
    form.period_start &&
    form.period_end;

  const submit = () => {
    if (!canSubmit) return;
    create.mutate(
      {
        project_id: Number(form.project_id),
        material_class_id: Number(form.material_class_id),
        price: Number(form.price),
        period_start: form.period_start,
        period_end: form.period_end,
        source: form.source.trim() || null,
      },
      {
        onSuccess: () => {
          setOpen(false);
          reset();
        },
      }
    );
  };

  const classNameById = useMemo(() => {
    const m = new Map<number, string>();
    (classesQ.data ?? []).forEach((c) => m.set(c.id, c.name));
    return m;
  }, [classesQ.data]);

  return (
    <div className="container-page py-8">
      <PageHeader
        serif
        title="Эталонные цены"
        subtitle="Базовые цены, относительно которых считаются отклонения"
        actions={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button leftIcon={<Plus size={14} />}>Добавить эталон</Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Новый эталон</DialogTitle>
              </DialogHeader>
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                    Объект *
                  </Label>
                  <Select
                    value={form.project_id}
                    onValueChange={(v) => setForm({ ...form, project_id: v })}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Выберите объект" />
                    </SelectTrigger>
                    <SelectContent>
                      {(projectsQ.data ?? []).map((p) => (
                        <SelectItem key={p.id} value={String(p.id)}>
                          {p.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                    Класс материала *
                  </Label>
                  <Select
                    value={form.material_class_id}
                    onValueChange={(v) =>
                      setForm({ ...form, material_class_id: v })
                    }
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Выберите класс" />
                    </SelectTrigger>
                    <SelectContent>
                      {(classesQ.data ?? []).map((c) => (
                        <SelectItem key={c.id} value={String(c.id)}>
                          {c.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                      Цена ₽ *
                    </Label>
                    <Input
                      type="number"
                      step="0.01"
                      value={form.price}
                      onChange={(e) =>
                        setForm({ ...form, price: e.target.value })
                      }
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                      Источник
                    </Label>
                    <Input
                      value={form.source}
                      onChange={(e) =>
                        setForm({ ...form, source: e.target.value })
                      }
                      placeholder="договор / прайс / ..."
                    />
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                      Действует с *
                    </Label>
                    <Input
                      type="date"
                      value={form.period_start}
                      onChange={(e) =>
                        setForm({ ...form, period_start: e.target.value })
                      }
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                      Действует по *
                    </Label>
                    <Input
                      type="date"
                      value={form.period_end}
                      onChange={(e) =>
                        setForm({ ...form, period_end: e.target.value })
                      }
                    />
                  </div>
                </div>
              </div>
              <DialogFooter>
                <Button variant="ghost" onClick={() => setOpen(false)}>
                  Отмена
                </Button>
                <Button
                  onClick={submit}
                  loading={create.isPending}
                  disabled={!canSubmit}
                >
                  Сохранить
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        }
      />

      {/* Фильтр по объекту */}
      <div className="mt-6 flex items-center gap-3">
        <Label className="text-xs text-fg-tertiary">Объект</Label>
        <Select
          value={filterProject ? String(filterProject) : ""}
          onValueChange={(v) => setFilterProject(v ? Number(v) : null)}
        >
          <SelectTrigger className="w-[280px]">
            <SelectValue placeholder="Все объекты" />
          </SelectTrigger>
          <SelectContent>
            {(projectsQ.data ?? []).map((p) => (
              <SelectItem key={p.id} value={String(p.id)}>
                {p.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {filterProject && (
          <Button variant="ghost" size="sm" onClick={() => setFilterProject(null)}>
            Сбросить
          </Button>
        )}
      </div>

      <div className="mt-6">
        {list.isLoading ? (
          <Surface padding="none">
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
          </Surface>
        ) : (list.data ?? []).length === 0 ? (
          <EmptyState
            icon={<Target size={20} />}
            title="Нет эталонных цен"
            description="Добавьте первый эталон, чтобы система могла считать отклонения."
            action={
              <Button leftIcon={<Plus size={14} />} onClick={() => setOpen(true)}>
                Добавить эталон
              </Button>
            }
          />
        ) : (
          <Surface padding="none">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Класс</TableHead>
                  <TableHead>Период</TableHead>
                  <TableHead className="text-right">Цена</TableHead>
                  <TableHead>Источник</TableHead>
                  <TableHead className="w-12"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(list.data ?? []).map((rp) => (
                  <TableRow key={rp.id}>
                    <TableCell className="font-medium">
                      {rp.material_class_name ??
                        classNameById.get(rp.material_class_id) ??
                        "—"}
                    </TableCell>
                    <TableCell className="text-fg-secondary">
                      {formatDate(rp.period_start)} — {formatDate(rp.period_end)}
                    </TableCell>
                    <TableCell className="text-right">
                      <MoneyCell value={rp.price} />
                    </TableCell>
                    <TableCell className="text-fg-secondary">
                      {rp.source ?? "—"}
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          if (window.confirm("Удалить эталон?")) {
                            remove.mutate(rp.id);
                          }
                        }}
                        aria-label="Удалить"
                      >
                        <Trash2 size={14} />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Surface>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/pages/ReferencePrices.tsx
git commit -m "feat(reference-prices): rewrite with PageHeader, dialog, project filter"
```

---

## Phase 10 — Reports + Settings

### Task 10.1: Переписать `Reports.tsx`

**Files:**
- Modify: `frontend/src/pages/Reports.tsx` (полная замена)

- [ ] **Step 1: Полностью заменить `frontend/src/pages/Reports.tsx`**

```tsx
import { useState } from "react";
import { FileSpreadsheet, Download } from "lucide-react";
import { toast } from "sonner";

import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Surface } from "@/components/ui-domain/Surface";
import { Button } from "@/components/ui-domain/Button";

import { useProjects } from "@/services/queries";
import { reportsApi } from "@/services/api/reports";
import type { ID } from "@/types/common";

export default function Reports() {
  const projectsQ = useProjects();
  const [open, setOpen] = useState(false);
  const [projectId, setProjectId] = useState<ID | null>(null);
  const [periodStart, setPeriodStart] = useState("");
  const [periodEnd, setPeriodEnd] = useState("");
  const [downloading, setDownloading] = useState(false);

  const download = async () => {
    if (!projectId) return;
    setDownloading(true);
    try {
      const blob = await reportsApi.excelBlob({
        project_id: projectId,
        period_start: periodStart || undefined,
        period_end: periodEnd || undefined,
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `report-${projectId}-${Date.now()}.xlsx`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success("Отчёт сформирован");
      setOpen(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Не удалось сформировать отчёт");
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="container-page py-8">
      <PageHeader serif title="Отчёты" subtitle="Экспорт аналитики в Excel" />

      <div className="mt-6 grid grid-cols-1 gap-3 md:grid-cols-2">
        <Surface>
          <div className="flex items-start gap-4">
            <div className="grid h-10 w-10 shrink-0 place-items-center rounded-md bg-accent-soft text-accent-text">
              <FileSpreadsheet size={18} />
            </div>
            <div className="min-w-0 flex-1">
              <h3 className="text-md font-medium text-fg">Сводный Excel</h3>
              <p className="mt-1 text-sm text-fg-secondary">
                Все счета-фактуры, позиции и расчёты отклонений по выбранному
                объекту и периоду.
              </p>
              <Button
                className="mt-4"
                leftIcon={<Download size={14} />}
                onClick={() => setOpen(true)}
              >
                Сформировать
              </Button>
            </div>
          </div>
        </Surface>
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Сформировать сводный Excel</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                Объект *
              </Label>
              <Select
                value={projectId ? String(projectId) : ""}
                onValueChange={(v) => setProjectId(v ? Number(v) : null)}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Выберите объект" />
                </SelectTrigger>
                <SelectContent>
                  {(projectsQ.data ?? []).map((p) => (
                    <SelectItem key={p.id} value={String(p.id)}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                  Период с
                </Label>
                <Input
                  type="date"
                  value={periodStart}
                  onChange={(e) => setPeriodStart(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                  По
                </Label>
                <Input
                  type="date"
                  value={periodEnd}
                  onChange={(e) => setPeriodEnd(e.target.value)}
                />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Отмена
            </Button>
            <Button onClick={download} loading={downloading} disabled={!projectId}>
              Скачать
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/pages/Reports.tsx
git commit -m "feat(reports): tile of available reports + Excel export dialog"
```

---

### Task 10.2: Переписать `Settings.tsx`

**Files:**
- Modify: `frontend/src/pages/Settings.tsx` (полная замена)

- [ ] **Step 1: Полностью заменить `frontend/src/pages/Settings.tsx`**

```tsx
import { useEffect, useMemo, useState } from "react";
import { cn } from "@/lib/utils";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Surface } from "@/components/ui-domain/Surface";
import { Button } from "@/components/ui-domain/Button";
import { Skeleton } from "@/components/ui-domain/Skeleton";

import { useSettings, useUpdateSettings } from "@/services/queries";
import type { AppSettings } from "@/services/api/settings";

type SectionKey = "general" | "parsing" | "about";

const SECTIONS: Array<{ key: SectionKey; label: string }> = [
  { key: "general", label: "Общие" },
  { key: "parsing", label: "Парсинг" },
  { key: "about", label: "О приложении" },
];

export default function SettingsPage() {
  const settingsQ = useSettings();
  const update = useUpdateSettings();

  const [active, setActive] = useState<SectionKey>("general");
  const [draft, setDraft] = useState<AppSettings | null>(null);

  useEffect(() => {
    if (settingsQ.data && !draft) setDraft(settingsQ.data);
  }, [settingsQ.data, draft]);

  const dirty = useMemo(() => {
    if (!draft || !settingsQ.data) return false;
    return JSON.stringify(draft) !== JSON.stringify(settingsQ.data);
  }, [draft, settingsQ.data]);

  if (settingsQ.isLoading || !draft) {
    return (
      <div className="container-page py-8 space-y-4">
        <Skeleton className="h-8 w-1/3" />
        <Skeleton className="h-[300px]" />
      </div>
    );
  }

  return (
    <div className="container-page py-8">
      <PageHeader serif title="Настройки" subtitle="Параметры приложения и парсинга" />

      <div className="mt-6 grid grid-cols-12 gap-6">
        {/* Боковое меню */}
        <aside className="col-span-12 md:col-span-3">
          <nav className="md:sticky md:top-20 flex flex-row gap-1 md:flex-col">
            {SECTIONS.map((s) => (
              <button
                key={s.key}
                type="button"
                onClick={() => setActive(s.key)}
                className={cn(
                  "rounded-md px-3 py-1.5 text-left text-sm transition-colors duration-150",
                  active === s.key
                    ? "bg-surface-hover text-fg font-medium"
                    : "text-fg-secondary hover:bg-surface-hover hover:text-fg"
                )}
              >
                {s.label}
              </button>
            ))}
          </nav>
        </aside>

        {/* Контент */}
        <section className="col-span-12 md:col-span-9">
          {active === "general" && (
            <Surface>
              <h3 className="text-md font-medium">Общие</h3>
              <p className="mt-1 text-xs text-fg-tertiary">
                Базовые параметры приложения. Расширяется по мере добавления
                полей в backend.
              </p>
              <div className="mt-4 text-sm text-fg-secondary">
                Дополнительных параметров пока нет.
              </div>
            </Surface>
          )}

          {active === "parsing" && (
            <Surface>
              <h3 className="text-md font-medium">Парсинг ИИ</h3>
              <div className="mt-4 space-y-4">
                <div className="space-y-1.5">
                  <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                    Провайдер ИИ
                  </Label>
                  <Select
                    value={String(draft.ai_provider ?? "off")}
                    onValueChange={(v) =>
                      setDraft({ ...draft, ai_provider: v as AppSettings["ai_provider"] })
                    }
                  >
                    <SelectTrigger className="w-[280px]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="openrouter">OpenRouter</SelectItem>
                      <SelectItem value="anthropic">Anthropic</SelectItem>
                      <SelectItem value="off">Отключено</SelectItem>
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-fg-tertiary">
                    Сервис, который парсит таблицу позиций из СФ.
                  </p>
                </div>

                <div className="space-y-1.5">
                  <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                    Модель
                  </Label>
                  <Input
                    value={String(draft.ai_model ?? "")}
                    onChange={(e) => setDraft({ ...draft, ai_model: e.target.value })}
                    placeholder="например, anthropic/claude-haiku-4-5"
                    className="max-w-md"
                  />
                </div>

                <div className="space-y-1.5">
                  <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                    Порог уверенности (0..1)
                  </Label>
                  <Input
                    type="number"
                    step="0.05"
                    min="0"
                    max="1"
                    value={String(draft.parse_threshold ?? 0.7)}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        parse_threshold: Number(e.target.value) || 0,
                      })
                    }
                    className="w-[160px]"
                  />
                  <p className="text-xs text-fg-tertiary">
                    Документы с уверенностью ниже порога отмечаются «требует
                    проверки».
                  </p>
                </div>
              </div>
            </Surface>
          )}

          {active === "about" && (
            <Surface>
              <h3 className="text-md font-medium">О приложении</h3>
              <div className="mt-4 space-y-1 text-sm text-fg-secondary">
                <div>УПД Трекер цен</div>
                <div>Версия: 2.0.0</div>
              </div>
            </Surface>
          )}
        </section>
      </div>

      {/* Sticky-bar */}
      {dirty && (
        <div className="sticky bottom-0 mt-8 -mx-6 border-t border-border-subtle bg-surface/95 px-6 py-3 backdrop-blur">
          <div className="container-page flex items-center justify-end gap-2">
            <Button variant="ghost" onClick={() => setDraft(settingsQ.data ?? null)}>
              Отменить изменения
            </Button>
            <Button
              loading={update.isPending}
              onClick={() => update.mutate(draft)}
            >
              Сохранить
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Smoke-test (вся фаза 9–10)**

Run: `cd frontend && npm run dev`. Пройтись по всем страницам:
- `/material-classes` — список, диалог создания, удаление с confirm.
- `/reference-prices` — список, фильтр по объекту, диалог с длинной формой, удаление.
- `/reports` — карточка отчёта, диалог Excel, скачивание.
- `/settings` — боковое меню, секции, sticky-bar при изменениях, сохранение.
- Тёмная тема для всех.

- [ ] **Step 3: Финальная проверка сборки**

Run:
```bash
cd frontend && npm run build && npm run lint
```

Expected: `tsc -b` без ошибок, `vite build` собирает без warnings, lint clean.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Settings.tsx
git commit -m "feat(settings): rewrite Settings with sidebar, sections, sticky save bar"
```

---

## Финальный smoke-test всего приложения

- [ ] **Step 1: Прокликать сценарий пользователя**

Запустить `cd frontend && npm run dev`. Сценарий:

1. Открыть `/`. Без выбранного объекта — `EmptyState` «Выберите объект».
2. Перейти на `/projects`. Создать новый объект через диалог. Тост «Объект создан».
3. Кликнуть в карточку объекта — открыть `ProjectPage`. Пусто — `EmptyState` «Нет счетов-фактур».
4. Перейти на `/upload`. Выбрать только что созданный объект. Перетащить тестовый PDF.
5. Дождаться загрузки. Кликнуть «Проверить» — открыть `Review`.
6. В `Review`: переключить табы. Изменить поле в шапке. Кнопка «Сохранить» активна. Сохранить. Тост.
7. Вернуться на `/`. Выбрать объект. Появились KPI и список СФ.
8. Создать `/material-classes` запись. Создать `/reference-prices` запись. На `/` нажать «Рассчитать» — таблица отклонений.
9. На `/reports` — скачать Excel, файл скачивается.
10. На `/settings` — изменить порог уверенности, сохранить.
11. Переключить тёмную тему — проверить все страницы.

Если что-то ломается — починить, повторить от шага 1.

- [ ] **Step 2: Финальный коммит «redesign complete»**

После полного зелёного прокликивания:

```bash
git commit --allow-empty -m "feat(frontend): UDP redesign complete

Все 10 фаз пройдены. Дизайн-система перенесена из kpi-tenders-react,
все страницы переписаны на новый язык. Темы light/dark работают,
react-query управляет данными, sonner показывает тосты, react-dropzone
обрабатывает загрузку."
```

---

## Что осталось за рамками этого плана

- Авторизация (Login/Register, ProtectedRoute, AuthContext, бэкенд users + JWT) — отдельный цикл spec → plan → implementation.
- Аналитические чарты на Dashboard через recharts (пакет уже стоит).
- Мобильная адаптация: коллапс TopNav в Sheet ≤768px.
- Юнит-тесты `ui-domain/` компонентов.







