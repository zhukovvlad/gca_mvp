# Видимость счетов без направления в «Все» — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** В режиме «Все направления» показать список всех счетов объекта + клиентский фильтр «только прочие», чтобы распознанные СФ без направления (бетон/арматура) перестали быть невидимыми.

**Architecture:** Бэкенд отдаёт на каждый счёт `directions: string[]` (коды направлений; `[]` = прочий) тем же резолвером, что и summary (единый источник истины классификации). Фронт рендерит готовый `InvoiceTable` в сводке «Все» и фильтрует уже загруженный массив по `inv.directions.length === 0` — без новых запросов.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, pytest. React 19, TS strict, Vite, TanStack Query, MSW v2, Vitest. Команды — через `just`.

Спека: [docs/superpowers/specs/2026-06-16-invoices-without-direction-visibility-design.md](../specs/2026-06-16-invoices-without-direction-visibility-design.md)

**Shell:** инструмент Bash — это git-bash напрямую (НЕ через PowerShell-обёртку). Команды пишутся как обычный bash. Рабочая директория между вызовами сохраняется, но после `git`-команд из корня репозитория делай `cd backend` заново перед точечным pytest. Точечный pytest: `cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest <путь>::<тест> -v`.

---

## File Structure

- **Modify:** `backend/routers/dashboard.py` — извлечь общий резолвер `_directions_by_invoice`; переключить на него `_direction_summaries`; добавить `directions` (счёт) в `/dashboard/invoices`.
- **Modify:** `backend/tests/integration/test_dashboard_directions.py` — тесты резолвера + payload.
- **Modify:** `frontend/src/types/invoice.ts` — `directions` в `DashboardInvoiceRow`.
- **Modify:** `frontend/src/test/fixtures.ts` — `directions` в моках + счёт-сирота.
- **Modify:** `frontend/src/components/ui-domain/KpiCard.tsx` — опциональный `onClick` на строке breakdown.
- **Modify:** `frontend/src/components/invoices/InvoiceTable.tsx` — бейдж «прочее» по `inv.directions`.
- **Modify:** `frontend/src/pages/ProjectPage.tsx` — секция «Счета» в `AllDirectionsSummaryView`, фильтр `showOnlyOther`, кликабельный «Прочие · N».
- **Modify:** `frontend/src/pages/ProjectPage.test.tsx` — тесты видимости и фильтра.

---

### Task 0: Ветка от main

**Files:** —

- [ ] **Step 1: Создать ветку**

```bash
cd /c/Users/zhukov_v/Projects/UDP && git checkout main && git checkout -b feat/invoices-without-direction-visibility
```

Expected: `Switched to a new branch 'feat/invoices-without-direction-visibility'`.

---

### Task 1: Backend — общий резолвер `_directions_by_invoice`

**Files:**
- Modify: `backend/routers/dashboard.py` (функция `_direction_summaries`, ~строки 79–98, 112)

- [ ] **Step 1: Зелёный baseline — прогнать dashboard-тесты направлений**

Run: `cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_dashboard_directions.py tests/integration/test_dashboard.py -q 2>&1 | tail -15`
Expected: PASS (фиксируем зелёное ДО рефактора).

- [ ] **Step 2: Добавить функцию `_directions_by_invoice` над `_direction_summaries`**

В `backend/routers/dashboard.py` непосредственно ПЕРЕД `def _direction_summaries(` вставить:

```python
def _directions_by_invoice(db: Session, project_id: int, excl_filter=lambda q: q) -> dict[int, set[str]]:
    """Коды направлений (material_types, кроме other — ADR #9), которых касается каждый счёт.

    Единый резолвер: match по material_class.material_type_id, item_type='material', distinct.
    Используется и _direction_summaries (§5.5), и /dashboard/invoices — ОДНА реализация
    классификации «какие направления у счёта», без второй ветки.
    excl_filter — опционально (summary передаёт фильтр исключённых поставщиков; список счетов — нет).
    """
    direction_types = [t for t in db.query(MaterialType).all() if t.code != "other"]
    id_to_code = {t.id: t.code for t in direction_types}
    if not id_to_code:
        return {}
    rows = excl_filter(
        db.query(
            Invoice.id.label("inv_id"),
            MaterialClass.material_type_id.label("type_id"),
        )
        .join(Document, Invoice.document_id == Document.id)
        .join(InvoiceItem, InvoiceItem.invoice_id == Invoice.id)
        .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
        .filter(
            Document.project_id == project_id,
            InvoiceItem.item_type == "material",
            MaterialClass.material_type_id.in_(id_to_code.keys()),
        )
        .distinct()
    ).all()
    result: dict[int, set[str]] = {}
    for r in rows:
        result.setdefault(r.inv_id, set()).add(id_to_code[r.type_id])
    return result
```

- [ ] **Step 3: Переключить `_direction_summaries` на резолвер**

В `_direction_summaries` заменить блок «3) Счета по типам + смешанность» (от `direction_type_ids = [t.id for t in direction_types]` до строки `mixed_invoice_ids = {inv for inv, s in types_by_invoice.items() if len(s) >= 2}` включительно) на:

```python
    # 3) Счета по направлениям + смешанность (§5.5) — общий резолвер (коды направлений на счёт).
    types_by_invoice = _directions_by_invoice(db, project_id, excl_filter)
    mixed_invoice_ids = {inv for inv, s in types_by_invoice.items() if len(s) >= 2}
```

- [ ] **Step 4: Поправить membership id→code в построении directions**

В том же `_direction_summaries`, в цикле `for t in direction_types:`, строку
`invoice_ids = {inv for inv, s in types_by_invoice.items() if t.id in s}`
заменить на (теперь множества содержат КОДЫ, не id):

```python
        invoice_ids = {inv for inv, s in types_by_invoice.items() if t.code in s}
```

(Строка `directed_invoice_ids: set(types_by_invoice.keys())` в return остаётся — это id счетов, не затронуто.)

- [ ] **Step 5: Прогнать те же тесты — поведение не изменилось**

Run: `cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_dashboard_directions.py tests/integration/test_dashboard.py -q 2>&1 | tail -15`
Expected: PASS (как в Step 1 — рефактор поведенчески нейтрален).

- [ ] **Step 6: Commit**

```bash
cd /c/Users/zhukov_v/Projects/UDP && git add backend/routers/dashboard.py && git commit -m "refactor(dashboard): извлечь общий резолвер _directions_by_invoice

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Backend — `directions` в `/dashboard/invoices`

**Files:**
- Modify: `backend/routers/dashboard.py` (функция `list_project_invoices`, ~строки 234–295)
- Test: `backend/tests/integration/test_dashboard_directions.py`

- [ ] **Step 1: Написать падающие тесты**

Добавить в конец `backend/tests/integration/test_dashboard_directions.py`:

```python
def test_dashboard_invoices_directions_field(client, factories):
    """Каждый счёт несёт directions: коды направлений; смешанный — оба, прочий — []."""
    project = factories.ProjectFactory.create()
    concrete = factories.MaterialClassFactory.create(name="В25")          # concrete
    rebar = _rebar_class(factories)                                        # rebar
    doc = factories.DocumentFactory.create(project=project)

    # смешанный счёт: бетон + арматура
    mixed = factories.InvoiceFactory.create(document=doc, number="MIX", date=date(2026, 3, 10))
    factories.InvoiceItemFactory.create(
        invoice=mixed, material_class=concrete, item_type="material",
        quantity=10, unit_price=8000, amount=80000)
    _rebar_item(factories, mixed, rebar, qty=2, unit_price=10000)

    # счёт-сирота: материал без класса (material_class=None)
    orphan = factories.InvoiceFactory.create(document=doc, number="ORPHAN", date=date(2026, 3, 11))
    factories.InvoiceItemFactory.create(
        invoice=orphan, material_class=None, item_type="material",
        quantity=1, unit_price=5000, amount=5000)

    rows = client.get(f"/api/dashboard/invoices?project_id={project.id}").json()
    by_number = {r["number"]: r for r in rows}
    assert sorted(by_number["MIX"]["directions"]) == ["concrete", "rebar"]
    assert by_number["ORPHAN"]["directions"] == []
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_dashboard_directions.py -k directions_field -v 2>&1 | tail -20`
Expected: FAIL — `KeyError: 'directions'`.

- [ ] **Step 3: Добавить поле в сериализатор**

В `backend/routers/dashboard.py`, в `list_project_invoices`, сразу после строки `invoices = q.order_by(Invoice.date.desc()).all()` добавить:

```python
    dir_map = _directions_by_invoice(db, project_id)   # без excl-фильтра: сырой список всех счетов
```

В возвращаемом dict счёта добавить ключ (рядом с `"has_issues"`):

```python
            "directions": sorted(dir_map.get(inv.id, set())),   # [] = прочий
```

(Позицию НЕ трогаем — `material_type` на позиции не нужен: сигнал «прочий» несёт `directions` на уровне счёта, см. спеку §«Метка сирот».)

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_dashboard_directions.py tests/integration/test_dashboard.py -q 2>&1 | tail -15`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /c/Users/zhukov_v/Projects/UDP && git add backend/routers/dashboard.py backend/tests/integration/test_dashboard_directions.py && git commit -m "feat(dashboard): directions на счёт в /invoices

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Frontend — типы + моки

**Files:**
- Modify: `frontend/src/types/invoice.ts`
- Modify: `frontend/src/test/fixtures.ts`

- [ ] **Step 1: Добавить поле в тип**

В `frontend/src/types/invoice.ts`, в `DashboardInvoiceRow` добавить поле (рядом с `has_issues`):

```ts
  /** Коды направлений, которых касается счёт (ADR #9: other не направление). [] = прочий. */
  directions: string[];
```

(`DashboardInvoiceItem` НЕ трогаем — `material_type` на позиции не заводим, см. спеку §«Метка сирот».)

- [ ] **Step 2: Обновить моки + добавить счёт-сирота**

В `frontend/src/test/fixtures.ts` в каждом счёте массива `sampleDashboardInvoices` добавить `directions: ["concrete"]` (все текущие — бетонные, `_baseItem` = В25). Затем добавить В КОНЕЦ массива счёт-сироту:

```ts
  // Прочий (без направления): материал без класса
  {
    id: 209,
    document_id: 18,
    number: "СФ-OTHER",
    date: "2026-03-20",
    supplier_name: "Поставщик Прочее",
    supplier_inn: null,
    vat_rate: 20,
    ai_confidence: 0.9,
    has_issues: false,
    verified: false,
    verified_at: null,
    directions: [],
    items: [
      {
        raw_name: "Прокат чёрных металлов",
        item_type: "material" as const,
        material_class: null,
        quantity: 1,
        raw_unit: "т",
        unit_price: 5000.0,
        amount: 5000.0,
      },
    ],
  },
```

- [ ] **Step 3: Typecheck**

Run: `cd /c/Users/zhukov_v/Projects/UDP && just typecheck-frontend 2>&1 | tail -5`
Expected: без ошибок (если existing-фикстуры counts где-то ассертятся по длине — поправить в Task 5).

- [ ] **Step 4: Commit**

```bash
cd /c/Users/zhukov_v/Projects/UDP && git add frontend/src/types/invoice.ts frontend/src/test/fixtures.ts && git commit -m "feat(fe): directions в типе DashboardInvoiceRow и моках + счёт-сирота

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Frontend — бейдж «прочее» в InvoiceTable

**Files:**
- Modify: `frontend/src/components/invoices/InvoiceTable.tsx`

- [ ] **Step 1: Добавить бейдж «прочее» к строке-сироте**

В `frontend/src/components/invoices/InvoiceTable.tsx`, в ячейке колонки `number` (блок `{!hiddenCols.has("number") && (...)}`), внутри `<span className="block whitespace-normal break-all" ...>` ПОСЛЕ `{inv.number || "—"}` добавить:

```tsx
                        {inv.directions.length === 0 && (
                          <span
                            className="ml-1.5 rounded bg-surface-sunken px-1 py-0.5 text-[10px] font-normal text-fg-tertiary align-middle"
                            title="Без направления (не бетон/арматура)"
                          >
                            прочее
                          </span>
                        )}
```

(Бейдж читает `inv.directions` напрямую — поле уже на строке; доп. проп не нужен. Семантику не трогаем; ортогонален danger-border «Разобрать» — они могут со-возникать, это ожидаемо.)

- [ ] **Step 2: Typecheck**

Run: `cd /c/Users/zhukov_v/Projects/UDP && just typecheck-frontend 2>&1 | tail -5`
Expected: без ошибок.

- [ ] **Step 3: Commit**

```bash
cd /c/Users/zhukov_v/Projects/UDP && git add frontend/src/components/invoices/InvoiceTable.tsx && git commit -m "feat(fe): бейдж «прочее» на строках счетов без направления

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Frontend — секция «Счета» + фильтр в «Все направления»

**Files:**
- Modify: `frontend/src/components/ui-domain/KpiCard.tsx`
- Modify: `frontend/src/pages/ProjectPage.tsx`
- Test: `frontend/src/pages/ProjectPage.test.tsx`

- [ ] **Step 1: Расширить KpiCard — опциональный onClick на строке breakdown**

В `frontend/src/components/ui-domain/KpiCard.tsx` изменить тип `breakdown`:

```ts
  breakdown?: { label: string; value: string; onClick?: () => void }[];
```

И рендер строки breakdown (блок `{breakdown && breakdown.length > 0 && (...)}`) заменить на:

```tsx
      {breakdown && breakdown.length > 0 && (
        <div className="mt-2 space-y-0.5">
          {breakdown.map((b) =>
            b.onClick ? (
              <button
                key={b.label}
                type="button"
                onClick={b.onClick}
                className="flex w-full items-center justify-between gap-2 text-xs text-accent-text hover:underline"
              >
                <span>{b.label}</span>
                <span className="font-mono">{b.value}</span>
              </button>
            ) : (
              <div key={b.label} className="flex items-center justify-between gap-2 text-xs text-fg-tertiary">
                <span>{b.label}</span>
                <span className="font-mono">{b.value}</span>
              </div>
            ),
          )}
        </div>
      )}
```

- [ ] **Step 2: Написать падающие тесты ProjectPage**

В `frontend/src/pages/ProjectPage.test.tsx` добавить (внутри `describe("ProjectPage", ...)`), используя multi-режим (он даёт вид «Все направления»):

```tsx
  it("shows all invoices (including orphan) in «Все направления» summary", async () => {
    mockSummary(sampleDashboardSummaryMulti);
    renderProject("1", "?direction=all");
    // СФ-OTHER (directions=[]) виден в общем списке сводки
    expect(await screen.findByText("СФ-OTHER")).toBeInTheDocument();
    // и несёт бейдж «прочее»
    expect(screen.getByText("прочее")).toBeInTheDocument();
  });

  it("«Прочие · N» KPI filters the invoice list to orphans only", async () => {
    mockSummary(sampleDashboardSummaryMulti);
    const user = userEvent.setup();
    renderProject("1", "?direction=all");
    // дождаться полного списка (есть бетонный СФ-CONFIRMED)
    expect(await screen.findByText("СФ-CONFIRMED")).toBeInTheDocument();
    // клик по кликабельной строке «Прочие» (label содержит число прочих = 1)
    await user.click(screen.getByRole("button", { name: /Прочие/i }));
    await waitFor(() => {
      // после фильтра остаются только сироты
      expect(screen.getByText("СФ-OTHER")).toBeInTheDocument();
      expect(screen.queryByText("СФ-CONFIRMED")).not.toBeInTheDocument();
    });
  });
```

- [ ] **Step 3: Запустить — убедиться, что падает**

Run: `cd /c/Users/zhukov_v/Projects/UDP && just test-frontend 2>&1 | grep -A3 "ProjectPage" | tail -20`
Expected: FAIL — нет таблицы счетов / кнопки «Прочие» в сводке «Все».

- [ ] **Step 4: Прокинуть `invoices` в `AllDirectionsSummaryView` и отрисовать секцию**

В `frontend/src/pages/ProjectPage.tsx`:

(a) В импорты добавить `useRef`:

```tsx
import { useMemo, useRef, useState } from "react";
```

(b) В props-тип `AllDirectionsSummaryView` добавить `invoices: DashboardInvoiceRow[];` (рядом с `calculations`), и в импорт типов добавить `DashboardInvoiceRow`:

```tsx
import type { DocumentSummary, DashboardInvoiceRow } from "@/types/invoice";
```

(c) В сигнатуру деструктуризации `AllDirectionsSummaryView({...})` добавить `invoices`.

(d) Внутри `AllDirectionsSummaryView` в начале тела (до `return`) добавить состояние и производные:

```tsx
  const [showOnlyOther, setShowOnlyOther] = useState(false);
  const invoicesSectionRef = useRef<HTMLDivElement>(null);
  const otherCount = useMemo(
    () => invoices.filter((i) => i.directions.length === 0).length,
    [invoices],
  );
  const shownInvoices = showOnlyOther
    ? invoices.filter((i) => i.directions.length === 0)
    : invoices;
```

(e) В карточке KPI «Счетов» (`label="Счетов"`) заменить строку breakdown «Прочие» на кликабельную из клиентского счётчика. Найти в массиве `breakdown`:

```tsx
              ...(summaryData.other_invoice_count > 0 ? [{ label: "Прочие", value: formatNumber(summaryData.other_invoice_count) }] : []),
```

и заменить на (счётчик — из клиентского массива, не из summary; см. спеку):

```tsx
              ...(otherCount > 0 ? [{
                label: "Прочие",
                value: formatNumber(otherCount),
                onClick: () => {
                  setShowOnlyOther(true);
                  invoicesSectionRef.current?.scrollIntoView?.({ behavior: "smooth" });
                },
              }] : []),
```

(f) После блока `<DeviationChart ... />` (перед закрывающим `</div>` основного контейнера секций) добавить секцию таблицы:

```tsx
        {/* Все счета объекта (включая прочие/без направления) */}
        <div ref={invoicesSectionRef} className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="font-serif text-lg">
              Счета{invoices.length > 0 ? ` · ${invoices.length}` : ""}
            </h2>
            {showOnlyOther && (
              <button
                type="button"
                className="text-sm text-accent-text hover:underline"
                onClick={() => setShowOnlyOther(false)}
              >
                × все счета
              </button>
            )}
          </div>
          {invoices.length === 0 ? (
            <p className="text-sm text-fg-tertiary">Нет счетов.</p>
          ) : (
            <div className="overflow-hidden rounded-lg border border-border-subtle bg-surface">
              <InvoiceTable invoices={shownInvoices} />
            </div>
          )}
        </div>
```

(g) В импорты `ProjectPage.tsx` добавить (если ещё нет) `InvoiceTable` — он уже импортирован для scoped-вкладки (`import { InvoiceTable } from "@/components/invoices/InvoiceTable";`). Проверить наличие; если нет — добавить.

(h) В месте рендера `<AllDirectionsSummaryView ... />` (в основном `return`) прокинуть `invoices={invoices}` рядом с `calculations={calculations}`.

- [ ] **Step 5: Запустить тесты — убедиться, что проходят**

Run: `cd /c/Users/zhukov_v/Projects/UDP && just test-frontend 2>&1 | tail -8`
Expected: PASS.

- [ ] **Step 6: Typecheck**

Run: `cd /c/Users/zhukov_v/Projects/UDP && just typecheck-frontend 2>&1 | tail -5`
Expected: без ошибок.

- [ ] **Step 7: Commit**

```bash
cd /c/Users/zhukov_v/Projects/UDP && git add frontend/src/components/ui-domain/KpiCard.tsx frontend/src/pages/ProjectPage.tsx frontend/src/pages/ProjectPage.test.tsx && git commit -m "feat(fe): список всех счетов + фильтр «Прочие» в сводке «Все направления»

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Финальная проверка

- [ ] **Step 1: Линт**

Run: `cd /c/Users/zhukov_v/Projects/UDP && just lint 2>&1 | tail -10`
Expected: без ошибок (ruff + eslint).

- [ ] **Step 2: Полный бэкенд + фронт**

Run: `cd /c/Users/zhukov_v/Projects/UDP && just test-backend-integration 2>&1 | tail -5`
Expected: PASS.
Run: `cd /c/Users/zhukov_v/Projects/UDP && just test-frontend 2>&1 | tail -5`
Expected: PASS.

- [ ] **Step 3: Документы уже в main**

Спека и план (`2026-06-16-invoices-without-direction-visibility-{design,}.md`) закоммичены в `main` ДО старта, поэтому присутствуют в ветке `feat/...` автоматически (ветка от main). Отдельного коммита не требуется — пропустить шаг.

---

## Self-Review

**Spec coverage:**
- 2b: бэкенд отдаёт `directions` на счёт тем же резолвером, что summary → Task 1 (резолвер) + Task 2 (поле) ✓
- Колонку «Позиции» НЕ чиним, `material_type` не заводим (YAGNI; сигнал «прочий» — бейдж на счёте) → не делаем, согласовано спекой §«Метка сирот» ✓
- Тип `DashboardInvoiceRow.directions` → Task 3 ✓
- Список всех счетов в «Все направления» (секция, не мини-табы) → Task 5 (f) ✓
- Клиентский фильтр `inv.directions.length === 0` → Task 5 (d) ✓
- Кликабельный «Прочие · N», счётчик из клиентского массива (не summary) → Task 5 (e) ✓; гарантия бейдж==строки (один массив) — тест Task 5 Step 2 ✓
- Сброс «× все счета» → Task 5 (f) ✓
- Бейдж «прочее» на сиротах + ортогональность has_issues → Task 4 ✓
- Регрессия summary (рефактор поведенчески нейтрален) → Task 1 Step 1/5 ✓
- YAGNI: классификация/новые направления/сегмент в свитчере — не делаем ✓

**Placeholder scan:** заглушек нет; весь код приведён буквально (резолвер, сериализатор, типы, фикстуры, KpiCard, секция, тесты).

**Type consistency:** `_directions_by_invoice(db, project_id, excl_filter=...) -> dict[int, set[str]]` (коды) — определена Task 1, используется Task 1 (`_direction_summaries`, membership `t.code in s`) и Task 2 (`dir_map.get(inv.id, set())`). `directions: string[]` — единственное новое поле; едино между бэкенд-payload (Task 2), типом (Task 3), моками (Task 3), InvoiceTable-бейджем (Task 4), фильтром ProjectPage (Task 5). `material_type` на позицию НЕ вводится нигде (YAGNI). `breakdown[].onClick?` — добавлен в KpiCard (Task 5 Step 1) и используется там же (Step 4e). `showOnlyOther`/`shownInvoices`/`otherCount`/`invoicesSectionRef` — определены и используются в одном компоненте `AllDirectionsSummaryView` (Task 5).
