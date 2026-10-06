import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it } from "vitest";

import { ContextsTab } from "@/pages/families/ContextsTab";
import { server } from "@/test/server";
import {
  BIG_GROUP_CHAPTER_ITEM_ID,
  BIG_GROUP_CONTEXT_ID,
  BIG_GROUP_SIZE,
  handlerState,
  MIXED_GROUPS_CONTEXT_ID,
} from "@/test/handlers";
import { createTestQueryClient, renderWithProviders } from "@/test/utils";
import type { ContextRow } from "@/types/domain";

// Контекст фикстуры, задействованный тестами карточки ниже (см. `STALE` в
// `ContextCard.test.tsx` — тот же id, своя константа здесь: карточка теперь
// открывается щелчком по строке списка, а не пропом напрямую).
const STALE_CONTEXT_ID = 602;

function contextFixture(id: number) {
  return handlerState.semanticContexts.find((c) => c.id === id)!;
}

/**
 * Очередь контекстов — вкладка «Контексты» (спека
 * `2026-09-25-families-screen-design.md` §2.1, §2.4, §2.7). Шесть фикстур
 * (`initialSemanticContexts`, `src/test/handlers.ts`) покрывают состояния из
 * плана фичи 1 (задача 13): обычный, устаревшее членство, конфликт решений,
 * `insufficient_description` без семьи, пустой контекст, архивный.
 *
 * Карточка выбранного контекста теперь панель СПРАВА ОТ СПИСКА, внутри этого
 * же компонента (спека §2.1) — компонент больше не принимает `selectedContextId`/
 * `onSelect` от `FamiliesPage`, выбор — собственное состояние.
 */

async function renderTab() {
  renderWithProviders(<ContextsTab />);
  await waitFor(() =>
    expect(screen.getByText("Штукатурка стен цементно-песчаным раствором")).toBeInTheDocument()
  );
}

function dataRows() {
  const rowgroups = screen.getAllByRole("rowgroup");
  return within(rowgroups[1]).getAllByRole("row");
}

/** Синтетическая страница контекстов для проверок пагинации/выбора — шести
 * фикстур `handlers.ts` (по одной каждого состояния) мало для нескольких
 * страниц по 20/50. Ровно поля `ContextRow` (спека §2.4, §2.8 п. 1). */
function manyContextRows(n: number): ContextRow[] {
  return Array.from({ length: n }, (_, i) => {
    const id = 9000 + i + 1;
    return {
      id,
      bucket_id: id,
      is_default: true,
      semantic_kind: "WORK",
      semantic_kind_source: "rule",
      name_role: "WORK",
      name_role_source: "rule",
      semantic_state: "SUGGESTED",
      comparability_reason: null,
      work_family_id: null,
      family_title: null,
      work_category_id: 77,
      work_category_code: "05.02.03",
      work_category_title: "Оштукатуривание цементно-песчаным раствором",
      catalog_position_id: 90000 + i,
      standard_job_title: `Синтетическая работа №${i + 1}`,
      unit_code: "м2",
      unit_symbol: "м²",
      archived_at: null,
      member_count: 1,
      has_stale_members: false,
      has_conflicting_members: false,
      work_category_path: [{ code: "05", title: "Отделочные работы" }],
    };
  });
}

/**
 * Подменяет `GET /contexts` синтетической выдачей и возвращает журнал
 * параметров КАЖДОГО запроса. Утверждения о `limit`/`offset` читаются из
 * этого журнала, а не из строк на экране: умолчание `limit` обработчика
 * совпадает с умолчанием экрана (20), и проверка по строкам осталась бы
 * зелёной у экрана, не передающего `limit` вовсе.
 */
function useManyContextRows(rows: ContextRow[]): URLSearchParams[] {
  const requests: URLSearchParams[] = [];
  server.use(
    http.get("/api/v1/semantic/contexts", ({ request }) => {
      const url = new URL(request.url);
      requests.push(url.searchParams);
      const limit = Number(url.searchParams.get("limit") ?? 20);
      const offset = Number(url.searchParams.get("offset") ?? 0);
      const page = rows.slice(offset, offset + limit);
      return HttpResponse.json({ items: page, total: rows.length, limit, offset });
    })
  );
  return requests;
}

function lastRequest(requests: URLSearchParams[]): URLSearchParams {
  expect(requests.length).toBeGreaterThan(0);
  return requests[requests.length - 1];
}

// После ввода в поле с задержкой (поиск и статья — 300 мс) идут запрос и
// отрисовка: под нагрузкой машины (`just ci` гонит бэкенд `-n 8` рядом с
// фронтендом) они не укладываются в секунду ожидания по умолчанию — тесты
// падали в полном наборе, проходя в одиночку.
const AFTER_DEBOUNCE = { timeout: 8000 };

describe("ContextsTab", () => {
  // Размер страницы — `usePersistedPageSize` (`gca.families.contexts.pageSize`);
  // без сброса выбор одного теста пережил бы следующий (`pageSizeShared.test.tsx`
  // делает то же для той же причины).
  afterEach(() => {
    localStorage.clear();
  });

  it("пустая очередь контекстов — фильтр не находит ни одного", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.type(screen.getByLabelText("Поиск по написанию каталога"), "нет-такого-текста-в-каталоге");
    expect(await screen.findByText("Контекстов нет", {}, AFTER_DEBOUNCE)).toBeInTheDocument();
  });

  it("подписи причины сравнимости различаются ТЕКСТОМ, а не наличием узла", async () => {
    await renderTab();

    // «Светильники» (id 604) — insufficient_description, без семьи: подпись
    // называет причину словами, а не молчит пустой ячейкой.
    expect(
      screen.getByText("семья не назначена, потому что состав не описан")
    ).toBeInTheDocument();

    // «Устройство покрытий полов из линолеума» (id 602) — семьи нет, но
    // comparability_reason не выставлен: ДРУГАЯ подпись, не подпись
    // insufficient_description — прочерк (спека §2.4: «семья (или «—»)»).
    // Той же подписью помечены ещё два контекста фикстуры (605, 606) —
    // проверяем строку id 602 точечно.
    const staleRow = screen
      .getByText("Устройство покрытий полов из линолеума")
      .closest("tr")!;
    expect(within(staleRow).getByText("—")).toBeInTheDocument();
  });

  // Спека §2.4: «единица; семья (или «—»)» — простой прочерк, не текст «нет
  // семьи» (живой прогон на стенде показал расхождение с §2.4).
  it("строка контекста без семьи печатает «—», не «нет семьи» (спека §2.4)", async () => {
    await renderTab();

    const row = screen
      .getByText("Устройство покрытий полов из линолеума")
      .closest("tr")!;
    expect(within(row).getByText("—")).toBeInTheDocument();
    expect(within(row).queryByText("нет семьи")).not.toBeInTheDocument();
  });

  it("три фильтра-признака — три независимых контрола, каждый сужает очередь по своей оси", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("checkbox", { name: "устаревшие" }));
    await waitFor(() => {
      expect(
        screen.getByText("Устройство покрытий полов из линолеума")
      ).toBeInTheDocument();
      // Не 603: у него И устаревшие, И конфликтные членства разом (MINOR-2,
      // ревью Fable 27.09.2026) — фильтр «устаревшие» его не исключает.
      // Отсутствие проверяет контекст без обеих осей.
      expect(
        screen.queryByText("Штукатурка стен цементно-песчаным раствором")
      ).not.toBeInTheDocument();
    });

    // Снять «устаревшие», включить «конфликтные» — другая проекция, другой контекст.
    await user.click(screen.getByRole("checkbox", { name: "устаревшие" }));
    await user.click(screen.getByRole("checkbox", { name: "конфликт" }));
    await waitFor(() => {
      expect(
        screen.getByText("Отделка потолков водоэмульсионным составом")
      ).toBeInTheDocument();
      expect(
        screen.queryByText("Устройство покрытий полов из линолеума")
      ).not.toBeInTheDocument();
    });

    await user.click(screen.getByRole("checkbox", { name: "конфликт" }));
    await user.click(screen.getByRole("checkbox", { name: "пустые" }));
    await waitFor(() => {
      expect(screen.getByText("Разборка временных перегородок")).toBeInTheDocument();
      expect(
        screen.queryByText("Отделка потолков водоэмульсионным составом")
      ).not.toBeInTheDocument();
    });
  });

  it("архивный контекст в очереди несёт пометку", async () => {
    await renderTab();
    const row = screen.getByText("Гидроизоляция фундамента (снят)").closest("tr")!;
    expect(within(row).getByText("архивный")).toBeInTheDocument();
  });

  it("фильтр по статье сужает очередь (спека §2.10)", async () => {
    const user = userEvent.setup();
    await renderTab();

    // Контекст 604 («Светильники») несёт статью 88, остальные пять — 77.
    await user.type(screen.getByLabelText("Статья (id)"), "88");

    await waitFor(() => {
      expect(screen.getByText("Светильники")).toBeInTheDocument();
      expect(
        screen.queryByText("Штукатурка стен цементно-песчаным раствором")
      ).not.toBeInTheDocument();
    }, AFTER_DEBOUNCE);
  });

  // ---------------------------------------------------------------------
  //  Строка списка (спека §2.4) — наименование, плашка источника, единица,
  //  семья, число позиций, точка внимания; «Вид»/«Наименование называет»/
  //  «Состояние» ушли в карточку (спека §2.5), их ФИЛЬТРЫ остались.
  // ---------------------------------------------------------------------

  it("строка несёт плашку «статья СМР», код и название статьи, единицу, семью и число позиций", async () => {
    await renderTab();

    const row = screen.getByText("Штукатурка стен цементно-песчаным раствором").closest("tr")!;
    expect(within(row).getByText("статья СМР")).toBeInTheDocument();
    expect(within(row).getByText(/05\.02\.03/)).toBeInTheDocument();
    expect(within(row).getByText(/Оштукатуривание цементно-песчаным раствором/)).toBeInTheDocument();
    // Символ единицы (спека §2.8, уточнение 27.09.2026), не код: код фикстуры
    // — "м2" (умышленно нереалистичный, докстрока `initialSemanticContexts`),
    // экран печатает `unit_symbol`.
    expect(within(row).getByText("м²")).toBeInTheDocument();
    expect(within(row).getByText("Семья работ №1")).toBeInTheDocument();
    expect(within(row).getByText("3")).toBeInTheDocument();
  });

  it("код единицы не печатается на экране нигде — только символ (сверка с макетом 27.09.2026)", async () => {
    await renderTab();
    expect(screen.queryByText("м2", { exact: true })).not.toBeInTheDocument();
    expect(screen.queryByText("M2")).not.toBeInTheDocument();
    expect(screen.getAllByText("м²").length).toBeGreaterThan(0);
  });

  it("длинное наименование зажато двумя строками с переносом и полным текстом в title; прочие ячейки не переносятся", async () => {
    const rows = manyContextRows(1);
    const longTitle = "Устройство монолитных конструкций ".repeat(20).trim();
    rows[0].standard_job_title = longTitle;
    useManyContextRows(rows);
    renderWithProviders(<ContextsTab />);

    const name = await screen.findByTitle(longTitle);
    // Раскладку jsdom не считает (замер — живой прогон оркестратора, J1);
    // здесь регрессионная сеть классами в ОБЕ стороны
    // (`docs/pitfalls/frontend.md`: зажим без `whitespace-normal` у ячейки
    // `TableCell` не переносит, а выпускает текст за ячейку).
    expect(name).toHaveTextContent(longTitle);
    expect(name).toHaveClass("line-clamp-2");
    const cells = within(name.closest("tr")!).getAllByRole("cell");
    expect(cells[0]).toHaveClass("whitespace-normal");
    expect(cells[0]).not.toHaveClass("whitespace-nowrap");
    for (const cell of cells.slice(1)) expect(cell).toHaveClass("whitespace-nowrap");
  });

  it("подсказка над строкой кода/названия статьи несёт полный путь классификатора — от корня до самой статьи", async () => {
    await renderTab();

    const row = screen.getByText("Штукатурка стен цементно-песчаным раствором").closest("tr")!;
    // Путь фикстуры (`handlers.ts`, `base.work_category_path`): "05" → "05.02",
    // сама статья строки — "05.02.03". Подсказка обязана перечислить ВСЕ три,
    // не только путь без листа и не только лист без пути.
    expect(
      within(row).getByTitle(
        "05 Отделочные работы › 05.02 Штукатурные работы › 05.02.03 Оштукатуривание цементно-песчаным раствором"
      )
    ).toBeInTheDocument();
  });

  it("«Светильники» (глубина пути 1) несёт подсказку с одним предком, не с двумя", async () => {
    await renderTab();
    const row = screen.getByText("Светильники").closest("tr")!;
    expect(
      within(row).getByTitle("07 Инженерные сети › 07.01 Электромонтажные работы")
    ).toBeInTheDocument();
  });

  it('плашка «система» стоит рядом с наименованием у semantic_kind=SYSTEM и отсутствует у прочих', async () => {
    await renderTab();

    // «Разборка временных перегородок» (605) — единственный SYSTEM среди шести фикстур.
    const systemRow = screen.getByText("Разборка временных перегородок").closest("tr")!;
    expect(within(systemRow).getByText("система")).toBeInTheDocument();

    const workRow = screen.getByText("Штукатурка стен цементно-песчаным раствором").closest("tr")!;
    expect(within(workRow).queryByText("система")).not.toBeInTheDocument();
  });

  it("точка внимания стоит у устаревшего, конфликтного и пустого контекста и её нет у обычного", async () => {
    await renderTab();

    const ordinaryRow = screen.getByText("Штукатурка стен цементно-песчаным раствором").closest("tr")!;
    const staleRow = screen.getByText("Устройство покрытий полов из линолеума").closest("tr")!;
    const conflictedRow = screen.getByText("Отделка потолков водоэмульсионным составом").closest("tr")!;
    const emptyRow = screen.getByText("Разборка временных перегородок").closest("tr")!;

    expect(within(ordinaryRow).queryByTestId("attention-dot")).not.toBeInTheDocument();
    expect(within(staleRow).getByTestId("attention-dot")).toBeInTheDocument();
    expect(within(conflictedRow).getByTestId("attention-dot")).toBeInTheDocument();
    expect(within(emptyRow).getByTestId("attention-dot")).toBeInTheDocument();
  });

  it("подсказка точки называет ИМЕННО те причины, что есть у строки — устаревшие отдельно, обе разом у 603, и «пустой»", async () => {
    await renderTab();

    const staleRow = screen.getByText("Устройство покрытий полов из линолеума").closest("tr")!;
    expect(within(staleRow).getByTitle("есть устаревшие позиции")).toBeInTheDocument();

    // Конфликтная фикстура (603) несёт настоящее `STALE`-членство разом с
    // конфликтным (`STALE_AND_CONFLICT_POSITION_ITEM_ID`) — `has_stale_members`
    // (агрегатный признак фильтра, докстринг `SemanticContextFixture` в
    // `handlers.ts`) обязан быть `true` тоже (MINOR-2, ревью Fable
    // 27.09.2026), и строка называет ОБЕ причины разом, не одну конфликтную.
    const conflictedRow = screen.getByText("Отделка потолков водоэмульсионным составом").closest("tr")!;
    expect(
      within(conflictedRow).getByTitle("есть устаревшие позиции; есть конфликтные позиции")
    ).toBeInTheDocument();

    const emptyRow = screen.getByText("Разборка временных перегородок").closest("tr")!;
    expect(within(emptyRow).getByTitle("пустой — позиций нет")).toBeInTheDocument();
  });

  it("подсказка точки называет ОБЕ причины разом у контекста с устаревшими И конфликтными членствами", async () => {
    const rows = manyContextRows(1);
    rows[0].has_stale_members = true;
    rows[0].has_conflicting_members = true;
    useManyContextRows(rows);
    renderWithProviders(<ContextsTab />);

    const row = await screen.findByText("Синтетическая работа №1").then((el) => el.closest("tr")!);
    expect(
      within(row).getByTitle("есть устаревшие позиции; есть конфликтные позиции")
    ).toBeInTheDocument();
  });

  it('колонки «Вид», «Наименование называет», «Состояние» из строки списка ушли; фильтры по ним остались с человеческими подписями', async () => {
    const user = userEvent.setup();
    await renderTab();

    // Строка контекста 604 несёт `semantic_kind=WORK`, `name_role=GENERIC_WORK`,
    // `semantic_state=SUGGESTED` — ни один код не печатается рядом с ней.
    const row = screen.getByText("Светильники").closest("tr")!;
    expect(within(row).queryByText("GENERIC_WORK")).not.toBeInTheDocument();
    expect(within(row).queryByText("SUGGESTED")).not.toBeInTheDocument();
    expect(within(row).queryByText("WORK")).not.toBeInTheDocument();

    // Фильтры остались и показывают ПОДПИСИ, а не коды. Запрос — ВНУТРИ
    // открытого списка (`role=listbox`): без этого «система» и «работа»
    // неоднозначны — те же слова уже стоят у строк списка/чипов легенды.
    await user.click(screen.getByRole("combobox", { name: "Вид" }));
    const kindListbox = await screen.findByRole("listbox");
    expect(within(kindListbox).getByText("работа")).toBeInTheDocument();
    expect(within(kindListbox).getByText("система")).toBeInTheDocument();
    expect(within(kindListbox).getByText("не определён")).toBeInTheDocument();
    expect(within(kindListbox).queryByText("WORK")).not.toBeInTheDocument();
    expect(within(kindListbox).queryByText("SYSTEM")).not.toBeInTheDocument();
    expect(within(kindListbox).queryByText("UNKNOWN")).not.toBeInTheDocument();
    await user.keyboard("{Escape}");

    await user.click(screen.getByRole("combobox", { name: "Наименование называет" }));
    const roleListbox = await screen.findByRole("listbox");
    expect(within(roleListbox).getByText("работу")).toBeInTheDocument();
    expect(within(roleListbox).getByText("место")).toBeInTheDocument();
    expect(within(roleListbox).getByText("род изделия без состава")).toBeInTheDocument();
    expect(within(roleListbox).queryByText("LOCATION_ONLY")).not.toBeInTheDocument();
    expect(within(roleListbox).queryByText("GENERIC_WORK")).not.toBeInTheDocument();
    await user.keyboard("{Escape}");

    await user.click(screen.getByRole("combobox", { name: "Состояние" }));
    const stateListbox = await screen.findByRole("listbox");
    expect(within(stateListbox).getByText("предложен правилом")).toBeInTheDocument();
    expect(within(stateListbox).getByText("подтверждён")).toBeInTheDocument();
    expect(within(stateListbox).getByText("не применяется")).toBeInTheDocument();
    expect(within(stateListbox).queryByText("SUGGESTED")).not.toBeInTheDocument();
    expect(within(stateListbox).queryByText("CONFIRMED")).not.toBeInTheDocument();
    expect(within(stateListbox).queryByText("NOT_APPLICABLE")).not.toBeInTheDocument();
  });

  it("применение фильтра «Вид» по-прежнему сужает очередь (подпись — только вид отображения)", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("combobox", { name: "Вид" }));
    const listbox = await screen.findByRole("listbox");
    await user.click(within(listbox).getByText("система"));

    await waitFor(() => {
      expect(screen.getByText("Разборка временных перегородок")).toBeInTheDocument();
      expect(
        screen.queryByText("Штукатурка стен цементно-песчаным раствором")
      ).not.toBeInTheDocument();
    });
  });

  // ---------------------------------------------------------------------
  //  Карточка контекста — панель справа от списка (спека §2.1)
  // ---------------------------------------------------------------------

  it("клик по строке контекста открывает его карточку рядом со списком; список остаётся виден", async () => {
    const user = userEvent.setup();
    await renderTab();

    expect(screen.getByText("Контекст не выбран")).toBeInTheDocument();

    await user.click(screen.getByText("Штукатурка стен цементно-песчаным раствором"));

    expect(await screen.findByRole("tab", { name: "Членства 3" })).toBeInTheDocument();
    // Список рядом — не подменён карточкой.
    expect(screen.getByLabelText("Поиск по написанию каталога")).toBeInTheDocument();
    expect(
      screen.getByText("Устройство покрытий полов из линолеума")
    ).toBeInTheDocument();
  });

  // Строки контекстов несут тот же пробел доступности, что и строки семей —
  // без этого теста щелчок мышью остался бы единственным путём открытия
  // строки, без фокуса и клавиш.
  it("строка контекста открывается Пробелом, и Пробел не прокручивает страницу (preventDefault)", async () => {
    await renderTab();

    const row = screen.getByText("Штукатурка стен цементно-песчаным раствором").closest("tr")!;
    row.focus();
    // `fireEvent` возвращает false, если обработчик вызвал preventDefault —
    // единственный наблюдатель «страница не уехала» в jsdom (саму прокрутку
    // jsdom не делает).
    expect(fireEvent.keyDown(row, { key: " " })).toBe(false);
    expect(await screen.findByRole("tab", { name: "Членства 3" })).toBeInTheDocument();
  });

  it("прочие клавиши на строке контекста (Tab, буква) не выбирают её и не гасят действие по умолчанию", async () => {
    await renderTab();

    const row = screen.getByText("Штукатурка стен цементно-песчаным раствором").closest("tr")!;
    row.focus();
    expect(fireEvent.keyDown(row, { key: "Tab" })).toBe(true);
    expect(fireEvent.keyDown(row, { key: "a" })).toBe(true);
    expect(screen.getByText("Контекст не выбран")).toBeInTheDocument();
    expect(row).toHaveAttribute("aria-selected", "false");
  });

  it("строка контекста открывается с клавиатуры — фокус и Enter", async () => {
    const user = userEvent.setup();
    await renderTab();

    const row = screen.getByText("Штукатурка стен цементно-песчаным раствором").closest("tr")!;
    row.focus();
    await user.keyboard("{Enter}");

    expect(await screen.findByRole("tab", { name: "Членства 3" })).toBeInTheDocument();
  });

  it("смена фильтра снимает выбор, если выбранный контекст выпал из выдачи", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByText("Штукатурка стен цементно-песчаным раствором"));
    expect(screen.queryByText("Контекст не выбран")).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("Поиск по написанию каталога"), "линолеум");
    await waitFor(
      () =>
        expect(
          screen.getByText("Устройство покрытий полов из линолеума")
        ).toBeInTheDocument(),
      AFTER_DEBOUNCE
    );
    // Снятие выбора — правка состояния во время рендера новой выдачи;
    // ждём и её, а не проверяем синхронно вслед за предыдущим `waitFor`.
    await waitFor(() => expect(screen.getByText("Контекст не выбран")).toBeInTheDocument(), AFTER_DEBOUNCE);
  });

  it("смена фильтра НЕ снимает выбор, если выбранный контекст остаётся в выдаче", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByText("Штукатурка стен цементно-песчаным раствором"));
    expect(screen.queryByText("Контекст не выбран")).not.toBeInTheDocument();

    // Сужаем поиском текстом, который «Штукатурку…» не исключает. Ждём
    // ПРИМЕНЁННУЮ новую выдачу (диапазон «1–1 из 1»), а не исчезновение
    // соседней строки: без `placeholderData` список на время загрузки
    // заменён скелетоном, соседняя строка пропадает ДО прихода данных, и
    // проверка выбора прошла бы в состоянии загрузки, где сверки ещё не
    // было — тест не увидел бы снятия выбора на любой новой выдаче.
    await user.type(screen.getByLabelText("Поиск по написанию каталога"), "штукатурка");
    await waitFor(() => expect(screen.getByText("1–1 из 1")).toBeInTheDocument(), AFTER_DEBOUNCE);
    expect(
      screen.queryByText("Устройство покрытий полов из линолеума")
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Контекст не выбран")).not.toBeInTheDocument();
  });

  it("смена страницы снимает выбор, если контекст остался на прежней странице", async () => {
    useManyContextRows(manyContextRows(25));
    const user = userEvent.setup();
    renderWithProviders(<ContextsTab />);
    await waitFor(() => expect(screen.getByText("Синтетическая работа №1")).toBeInTheDocument());

    await user.click(screen.getByText("Синтетическая работа №5"));
    expect(screen.queryByText("Контекст не выбран")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await waitFor(() => expect(screen.getByText("Синтетическая работа №21")).toBeInTheDocument());
    expect(screen.getByText("Контекст не выбран")).toBeInTheDocument();
  });

  it("смена страницы НЕ снимает выбор, если выбранный контекст есть на новой странице", async () => {
    // Выдача сдвинулась между запросами (новые контексты появились выше по
    // порядку): выбранный на первой странице №5 теперь стоит на второй.
    const rows = manyContextRows(25);
    server.use(
      http.get("/api/v1/semantic/contexts", ({ request }) => {
        const url = new URL(request.url);
        const limit = Number(url.searchParams.get("limit") ?? 20);
        const offset = Number(url.searchParams.get("offset") ?? 0);
        const page = offset === 0 ? rows.slice(0, limit) : [rows[4], ...rows.slice(offset, offset + limit - 1)];
        return HttpResponse.json({ items: page, total: rows.length, limit, offset });
      })
    );
    const user = userEvent.setup();
    renderWithProviders(<ContextsTab />);
    await waitFor(() => expect(screen.getByText("Синтетическая работа №1")).toBeInTheDocument());

    await user.click(screen.getByText("Синтетическая работа №5"));
    expect(screen.queryByText("Контекст не выбран")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await waitFor(() => expect(screen.getByText("Синтетическая работа №21")).toBeInTheDocument());
    expect(screen.queryByText("Контекст не выбран")).not.toBeInTheDocument();
    expect(screen.getByText("Синтетическая работа №5").closest("tr")).toHaveAttribute("aria-selected", "true");
  });

  it("смена размера страницы НЕ снимает выбор, если контекст остаётся в новой выдаче", async () => {
    useManyContextRows(manyContextRows(25));
    const user = userEvent.setup();
    renderWithProviders(<ContextsTab />);
    await waitFor(() => expect(screen.getByText("Синтетическая работа №1")).toBeInTheDocument());

    await user.click(screen.getByText("Синтетическая работа №15"));
    expect(screen.queryByText("Контекст не выбран")).not.toBeInTheDocument();

    await user.click(screen.getByRole("combobox", { name: "На странице:" }));
    await user.click(await screen.findByRole("option", { name: "50" }));

    await waitFor(() => expect(screen.getByText("Синтетическая работа №21")).toBeInTheDocument());
    expect(screen.queryByText("Контекст не выбран")).not.toBeInTheDocument();
  });

  // Спека §2.1: карточка — один и тот же экземпляр `<ContextCard>`
  // панелью справа (спека §2.1), меняется только `contextId` пропом, без
  // `key`. Без сброса выбор членств ОДНОГО контекста пережил бы щелчок по
  // строке ДРУГОГО — «Разделить выбранные» ушло бы для нового контекста с
  // id старого. Воспроизведение — фикстуры 608 (520 членств одной группой) и
  // 609 (стяжка пола, «Устройство стяжки пола») из `handlers.ts`.
  it("переключение на другой контекст в списке сбрасывает выбор членств карточки, не только видимую страницу (спека §2.1, §2.5)", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByText("Устройство вентиляционных каналов"));
    expect(
      await screen.findByRole("tab", { name: `Членства ${BIG_GROUP_SIZE}` })
    ).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: `Членства ${BIG_GROUP_SIZE}` }));
    await user.click(screen.getByLabelText(/Выбрать группу/));
    await waitFor(() =>
      expect(screen.getByText(`Выбрано членств: ${BIG_GROUP_SIZE}`)).toBeInTheDocument()
    );

    await user.click(screen.getByText("Устройство стяжки пола"));
    await waitFor(() => expect(screen.getByRole("tab", { name: "Членства 3" })).toBeInTheDocument());
    // Счётчик прежнего контекста (520) нигде не виден — ни на новой вкладке
    // по умолчанию («Решения»), ни если открыть «Членства» заново.
    expect(screen.queryByText(`Выбрано членств: ${BIG_GROUP_SIZE}`)).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Членства 3" }));
    expect(screen.getByText("Выбрано членств: 0")).toBeInTheDocument();

    // «Разделить выбранные» после переключения обязано уйти с ТЕКУЩИМ
    // contextId (609) и id ТЕКУЩЕГО выбора — не с 520 id контекста 608.
    await user.click(screen.getByRole("button", { name: /Раскрыть группу «8 Отделочные работы/ }));
    await user.click(await screen.findByLabelText("Выбрать позицию 78001"));
    await user.click(screen.getByRole("button", { name: "Разделить выбранные" }));

    await waitFor(() =>
      expect(handlerState.lastSplitContextRequest).toEqual({
        contextId: MIXED_GROUPS_CONTEXT_ID,
        body: { position_item_ids: [78001], rule: null },
      })
    );
  });

  // Остальные поля того же сброса (карточка — `key` на `<ContextCard>` в
  // `ContextsTab.tsx`, спека §2.5, §2.8): каждое поле — свой вход. Цель
  // переноса и цель слияния прежнего контекста не должны оставлять кнопки
  // нового включёнными (иначе «Перенести выбранные»/«Слить контексты» ушли
  // бы в соседа ЧУЖОЙ корзины). Перенесено из `ContextCard.test.tsx`
  // (проверялось там искусственным `rerender` ТОГО ЖЕ элемента с новым
  // `contextId` — продакшен-код так карточку больше не меняет).
  it("переключение контекста сбрасывает выбранные цели переноса и слияния — кнопки нового контекста без цели выключены", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByText("Штукатурка стен цементно-песчаным раствором"));
    await waitFor(() => expect(screen.getByRole("tab", { name: "Членства 3" })).toBeInTheDocument());
    await user.click(screen.getByRole("tab", { name: "Членства 3" }));
    await user.click(screen.getByRole("combobox", { name: "Целевой контекст для переноса выбранных" }));
    await user.click(await screen.findByRole("option", { name: "контекст #750" }));
    await user.click(screen.getByRole("combobox", { name: "Слить контекст в целевой" }));
    await user.click(await screen.findByRole("option", { name: "контекст #750" }));
    expect(screen.getByRole("button", { name: "Слить контексты" })).toBeEnabled();

    // У 609 живых соседей нет вовсе — выбрать цель там нечего. Ждём Skeleton
    // прежней карточки (601) полностью снятым — новая карточка (609)
    // монтируется свежей, поэтому «Членства 3» ниже — уже ЕЁ вкладка, не
    // переживший переключение узел прежней.
    await user.click(screen.getByText("Устройство стяжки пола"));
    await waitFor(() => expect(screen.getByRole("tab", { name: "Членства 3" })).toBeInTheDocument());
    await user.click(screen.getByRole("tab", { name: "Членства 3" }));
    await user.click(screen.getByRole("button", { name: /Раскрыть группу «8 Отделочные работы/ }));
    await user.click(await screen.findByLabelText("Выбрать позицию 78001"));
    expect(screen.getByText("Выбрано членств: 1")).toBeInTheDocument();

    expect(screen.getByRole("button", { name: "Перенести выбранные" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Слить контексты" })).toBeDisabled();
  });

  // Перенесено из `ContextCard.test.tsx` — та же причина, что у теста выше.
  it("переключение контекста сбрасывает результат пакетного переноса — «перенесено N из M» прежнего контекста не встаёт под строку внимания нового с тем же разделом", async () => {
    const user = userEvent.setup();
    // 609 получает строку внимания ТОГО ЖЕ раздела, что у 602 (ключ группы
    // совпадает) — иначе сброс неотличим от «результат лежит под другим ключом».
    contextFixture(MIXED_GROUPS_CONTEXT_ID).stale_groups = contextFixture(STALE_CONTEXT_ID).stale_groups.map(
      (sg) => ({ ...sg })
    );
    handlerState.staleGroupTransferOverride = {
      results: [
        { position_item_id: 1, outcome: "moved", target_context_id: 9999, error_code: null, message: null },
        { position_item_id: 2, outcome: "moved", target_context_id: 9999, error_code: null, message: null },
        { position_item_id: 3, outcome: "moved", target_context_id: 9999, error_code: null, message: null },
      ],
      moved: 3,
      refused: 0,
    };
    await renderTab();

    await user.click(screen.getByText("Устройство покрытий полов из линолеума"));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^Перенести их/ })).toBeInTheDocument()
    );
    await user.click(screen.getByRole("button", { name: /^Перенести их/ }));
    expect(await screen.findByText("перенесено 3 из 3")).toBeInTheDocument();

    await user.click(screen.getByText("Устройство стяжки пола"));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^Перенести их/ })).toBeInTheDocument()
    );
    // Строка внимания нового контекста на месте — результата прежнего под ней нет.
    expect(screen.queryByText("перенесено 3 из 3")).not.toBeInTheDocument();
  });

  // Перенесено из `ContextCard.test.tsx` — та же причина, что у теста выше.
  it("переключение контекста очищает поле нового контекста по умолчанию для архивирования", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByText("Штукатурка стен цементно-песчаным раствором"));
    await waitFor(() => expect(screen.getByRole("tab", { name: "Членства 3" })).toBeInTheDocument());
    await user.click(screen.getByRole("tab", { name: "Членства 3" }));
    await user.type(screen.getByLabelText(/Архивировать контекст — новый контекст по умолчанию/), "750");
    expect(screen.getByLabelText(/Архивировать контекст — новый контекст по умолчанию/)).toHaveValue("750");

    await user.click(screen.getByText("Устройство стяжки пола"));
    await waitFor(() => expect(screen.getByRole("tab", { name: "Членства 3" })).toBeInTheDocument());
    await user.click(screen.getByRole("tab", { name: "Членства 3" }));
    expect(screen.getByLabelText(/Архивировать контекст — новый контекст по умолчанию/)).toHaveValue("");
  });

  // Возврат к контексту — то же, что первое его открытие: набор id группы,
  // снятый `groupMemberIds` в ПРЕЖНИЙ визит, не отмечает галочку группы.
  // Перенесено из `ContextCard.test.tsx` — та же причина, что у тестов выше.
  it("возврат к контексту не отмечает галочку группы по набору id прежнего визита — как при первом открытии", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByText("Устройство стяжки пола"));
    await waitFor(() => expect(screen.getByRole("tab", { name: "Членства 3" })).toBeInTheDocument());
    await user.click(screen.getByRole("tab", { name: "Членства 3" }));
    await user.click(
      screen.getByLabelText(
        "Выбрать группу «8 Отделочные работы (паркинг, надземная часть МОП) › 8.3 Полы по грунту»"
      )
    );
    await waitFor(() => expect(screen.getByText("Выбрано членств: 2")).toBeInTheDocument());

    await user.click(screen.getByText("Устройство вентиляционных каналов"));
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: `Членства ${BIG_GROUP_SIZE}` })).toBeInTheDocument()
    );

    await user.click(screen.getByText("Устройство стяжки пола"));
    await waitFor(() => expect(screen.getByRole("tab", { name: "Членства 3" })).toBeInTheDocument());
    await user.click(screen.getByRole("tab", { name: "Членства 3" }));
    expect(screen.getByText("Выбрано членств: 0")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Раскрыть группу «8 Отделочные работы/ }));
    await screen.findByText("Стяжка пола, ось А-Б");
    await user.click(screen.getByLabelText("Выбрать позицию 78001"));
    await user.click(screen.getByLabelText("Выбрать позицию 78002"));
    expect(screen.getByText("Выбрано членств: 2")).toBeInTheDocument();
    expect(
      screen.getByLabelText(
        "Выбрать группу «8 Отделочные работы (паркинг, надземная часть МОП) › 8.3 Полы по грунту»"
      )
    ).not.toBeChecked();
  });

  // ---------------------------------------------------------------------
  //  Поздний ответ сети и состояние секции группы прежнего контекста не
  //  должны долетать до нового (спека §2.5, §2.8) — оба закрыты ОДНИМ
  //  механизмом (`key` на `<ContextCard>`, комментарий у него в
  //  `ContextsTab.tsx`), не перечислением сбрасываемых полей.
  // ---------------------------------------------------------------------

  it("поздний ответ member-ids прежнего контекста не добавляет его id в выбор нового", async () => {
    server.use(
      http.get("/api/v1/semantic/contexts/:id/member-ids", async ({ params }) => {
        if (Number(params.id) !== BIG_GROUP_CONTEXT_ID) {
          return HttpResponse.json({ position_item_ids: [], total: 0 });
        }
        // Задержка нарочно больше времени, за которое тест успевает
        // переключиться на другой контекст, — воспроизводит сеть, ответившую
        // ПОСЛЕ смены выбора в списке.
        await delay(400);
        const ids = Array.from({ length: BIG_GROUP_SIZE }, (_, i) => 80001 + i);
        return HttpResponse.json({ position_item_ids: ids, total: ids.length });
      })
    );
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByText("Устройство вентиляционных каналов"));
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: `Членства ${BIG_GROUP_SIZE}` })).toBeInTheDocument()
    );
    await user.click(screen.getByRole("tab", { name: `Членства ${BIG_GROUP_SIZE}` }));
    await user.click(screen.getByRole("button", { name: /9 Инженерные системы/ }));
    await screen.findByText("Вентканал, узел 1");
    // Галочка группы шлёт member-ids с задержкой 400ms — переключаемся на
    // другой контекст ДО того, как он успевает ответить.
    await user.click(screen.getByLabelText(/Выбрать группу/));

    await user.click(screen.getByText("Устройство стяжки пола"));
    await waitFor(() => expect(screen.getByRole("tab", { name: "Членства 3" })).toBeInTheDocument());
    await user.click(screen.getByRole("tab", { name: "Членства 3" }));
    expect(screen.getByText("Выбрано членств: 0")).toBeInTheDocument();

    // Ждём дольше задержки ответа — он приходит уже ПОСЛЕ переключения.
    await new Promise((resolve) => setTimeout(resolve, 500));
    expect(screen.getByText("Выбрано членств: 0")).toBeInTheDocument();
    expect(screen.queryByText(`Выбрано членств: ${BIG_GROUP_SIZE}`)).not.toBeInTheDocument();
    // Ничем не выбранным — «Разделить»/«Перенести» недостижимы, значит и
    // 520 id контекста 608 не могли уйти ни в один запрос нового контекста.
    expect(screen.getByRole("button", { name: "Разделить выбранные" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Перенести выбранные" })).toBeDisabled();
    expect(handlerState.lastSplitContextRequest).toBeNull();
    expect(handlerState.lastMoveMembersRequest).toBeNull();
  });

  // Секция группы вкладки «Членства» держит своё «раскрыта/страница» в
  // собственном `useState`, ключованном ТОЛЬКО номером раздела
  // (`chapter_item_id`) — если раздел контекста А совпадает по этому номеру
  // с разделом ДРУГОГО контекста Б (совпадение возможно: раздел сметы —
  // числовой id, не привязан к контексту), без `key` на карточке React
  // сохранил бы состояние секции между ними. Фикстура 609 (`handlers.ts`)
  // получает раздел с ТЕМ ЖЕ номером, что раздел контекста 608, — иначе
  // совпадение неотличимо от «просто другой раздел».
  //
  // Контекст 609 ОТКРЫВАЕТСЯ ДВАЖДЫ — первый раз до 608, второй раз после:
  // при первом ЛЮБОМ открытии контекста карточка на миг показывает `Skeleton`
  // (`cardQ.isPending` — запрос ещё не закэширован), и это САМО снимает
  // дерево вкладок независимо от `key` карточки — упавший без `key` тест на
  // паре «А → Б» с Б, открываемым впервые, остался бы зелёным ПО ЭТОЙ
  // причине, а не потому что утечки нет. Клиент — как в приложении
  // (`App.tsx`: `staleTime` минута), не тестовый с `staleTime: 0`: только
  // так повторное открытие 609 берёт карточку из кэша БЕЗ `Skeleton`
  // (`gcTime` тоже поднят — иначе тестовый клиент выбросил бы неиспользуемый
  // кэш 609 сам, пока карточка открыта на 608), и утечка состояния секции
  // группы (если `key` карточки убрать) становится видна.
  it("состояние секции группы (раскрыта, страница) не переживает возврат к контексту через другой — даже если номер раздела совпал с чужим", async () => {
    contextFixture(MIXED_GROUPS_CONTEXT_ID).member_paths = contextFixture(
      MIXED_GROUPS_CONTEXT_ID
    ).member_paths.map((group, i) =>
      i === 0 ? { ...group, chapter_item_ids: [BIG_GROUP_CHAPTER_ITEM_ID] } : group
    );
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 60_000, gcTime: 300_000 } },
    });
    const user = userEvent.setup();
    renderWithProviders(<ContextsTab />, { queryClient });
    await waitFor(() =>
      expect(screen.getByText("Штукатурка стен цементно-песчаным раствором")).toBeInTheDocument()
    );

    // Первое открытие 609 — только чтобы закэшировать карточку, без действий.
    await user.click(screen.getByText("Устройство стяжки пола"));
    await waitFor(() => expect(screen.getByRole("tab", { name: "Членства 3" })).toBeInTheDocument());

    await user.click(screen.getByText("Устройство вентиляционных каналов"));
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: `Членства ${BIG_GROUP_SIZE}` })).toBeInTheDocument()
    );
    await user.click(screen.getByRole("tab", { name: `Членства ${BIG_GROUP_SIZE}` }));
    await user.click(screen.getByRole("button", { name: /9 Инженерные системы/ }));
    await screen.findByText("Вентканал, узел 1");
    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await screen.findByText("Вентканал, узел 21");

    // Возврат к 609 — карточка берётся из кэша (`staleTime` минута), без
    // `Skeleton`.
    await user.click(screen.getByText("Устройство стяжки пола"));
    await waitFor(() => expect(screen.getByRole("tab", { name: "Членства 3" })).toBeInTheDocument());
    await user.click(screen.getByRole("tab", { name: "Членства 3" }));

    // Секция того же номера раздела свёрнута (страница 1 по умолчанию), а
    // не унаследовала «раскрыта, страница 2» контекста 608: унаследуй она
    // их, запрос ушёл бы за пределы реального числа позиций 609 по этому
    // разделу — таблица без строк и без Pager, без всякой видимой причины
    // для оператора.
    const trigger = screen.getByRole("button", { name: /Раскрыть группу «8 Отделочные работы/ });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    // Таблиц на экране несколько (список контекстов слева — тоже `<table>`,
    // 9 строк фикстур) — область поиска сужена до вкладки «Членства».
    const membershipPanel = screen.getByRole("tabpanel", { name: "Членства 3" });
    expect(within(membershipPanel).queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText("Вентканал, узел 21")).not.toBeInTheDocument();
  });

  // ---------------------------------------------------------------------
  //  Пагинация (спека §2.7): размер 10/20/50/100 передаёт `limit`, смена
  //  размера и фильтра ставят `offset=0`; диапазон и итог — из `total` ответа.
  // ---------------------------------------------------------------------

  it("выбор размера страницы передаёт limit в запрос и сбрасывает offset на 0", async () => {
    const requests = useManyContextRows(manyContextRows(45));
    const user = userEvent.setup();
    renderWithProviders(<ContextsTab />);
    await waitFor(() => expect(screen.getByText("Синтетическая работа №1")).toBeInTheDocument());

    // Уходим на страницу 2 размером 20 (offset станет 20) — без этого шага
    // offset=0 после смены размера был бы исходным, а не сброшенным.
    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await waitFor(() => expect(screen.getByText("Синтетическая работа №21")).toBeInTheDocument());
    expect(lastRequest(requests).get("offset")).toBe("20");

    // Меняем размер на 10 — offset обязан вернуться на 0 (страница 1 нового размера).
    await user.click(screen.getByRole("combobox", { name: "На странице:" }));
    await user.click(await screen.findByRole("option", { name: "10" }));

    await waitFor(() => {
      expect(screen.getByText("Синтетическая работа №1")).toBeInTheDocument();
      expect(screen.queryByText("Синтетическая работа №11")).not.toBeInTheDocument();
    });
    expect(lastRequest(requests).get("limit")).toBe("10");
    expect(lastRequest(requests).get("offset")).toBe("0");
  });

  // Каждый фильтр сбрасывает страницу СВОИМ обработчиком — восемь независимых
  // мест, и забытый сброс у одного не виден по семи прочим.
  // Синтетический обработчик фильтры не применяет: выдача остаётся из 45
  // строк, и утверждение читает только параметры запроса.
  const FILTER_CASES: Array<{
    name: string;
    param: string;
    value: string;
    apply: (user: ReturnType<typeof userEvent.setup>) => Promise<void>;
  }> = [
    {
      name: "поиск по написанию",
      param: "catalog_query",
      value: "x",
      apply: (user) => user.type(screen.getByLabelText("Поиск по написанию каталога"), "x"),
    },
    {
      name: "статья (id)",
      param: "work_category_id",
      value: "88",
      apply: (user) => user.type(screen.getByLabelText("Статья (id)"), "88"),
    },
    {
      name: "вид",
      param: "semantic_kind",
      value: "SYSTEM",
      apply: async (user) => {
        await user.click(screen.getByRole("combobox", { name: "Вид" }));
        await user.click(within(await screen.findByRole("listbox")).getByText("система"));
      },
    },
    {
      name: "роль имени",
      param: "name_role",
      value: "LOCATION_ONLY",
      apply: async (user) => {
        await user.click(screen.getByRole("combobox", { name: "Наименование называет" }));
        await user.click(within(await screen.findByRole("listbox")).getByText("место"));
      },
    },
    {
      name: "состояние",
      param: "semantic_state",
      value: "CONFIRMED",
      apply: async (user) => {
        await user.click(screen.getByRole("combobox", { name: "Состояние" }));
        await user.click(within(await screen.findByRole("listbox")).getByText("подтверждён"));
      },
    },
    {
      name: "есть устаревшие",
      param: "has_stale_members",
      value: "true",
      apply: (user) => user.click(screen.getByRole("checkbox", { name: "устаревшие" })),
    },
    {
      name: "есть конфликтные",
      param: "has_conflicting_members",
      value: "true",
      apply: (user) => user.click(screen.getByRole("checkbox", { name: "конфликт" })),
    },
    {
      name: "нет членств",
      param: "has_no_members",
      value: "true",
      apply: (user) => user.click(screen.getByRole("checkbox", { name: "пустые" })),
    },
  ];

  it.each(FILTER_CASES)("смена фильтра «$name» ставит offset=0 в запросе", async ({ param, value, apply }) => {
    const requests = useManyContextRows(manyContextRows(45));
    const user = userEvent.setup();
    renderWithProviders(<ContextsTab />);
    await waitFor(() => expect(screen.getByText("Синтетическая работа №1")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await waitFor(() => expect(lastRequest(requests).get("offset")).toBe("20"));

    await apply(user);

    // Запрос С НОВЫМ фильтром обязан уйти с offset=0 — не промежуточный
    // запрос без фильтра (поиск и статья дебаунсятся).
    await waitFor(() => {
      const last = lastRequest(requests);
      expect(last.get(param)).toBe(value);
      expect(last.get("offset")).toBe("0");
    });
  });

  it("под списком виден диапазон и итог из total ответа: «1–20 из 128»", async () => {
    useManyContextRows(manyContextRows(128));
    renderWithProviders(<ContextsTab />);

    await waitFor(() => expect(screen.getByText("Синтетическая работа №1")).toBeInTheDocument());
    expect(screen.getByText("1–20 из 128")).toBeInTheDocument();
  });

  it("на последней неполной странице диапазон кончается итогом, а не границей размера: «121–128 из 128»", async () => {
    useManyContextRows(manyContextRows(128));
    const user = userEvent.setup();
    renderWithProviders(<ContextsTab />);
    await waitFor(() => expect(screen.getByText("Синтетическая работа №1")).toBeInTheDocument());

    await user.click(within(screen.getByRole("navigation", { name: "pagination" })).getByText("7"));

    await waitFor(() => expect(screen.getByText("121–128 из 128")).toBeInTheDocument());
    expect(dataRows()).toHaveLength(8);
  });

  it("размер страницы по умолчанию — 20, и он уходит в запрос как limit", async () => {
    const requests = useManyContextRows(manyContextRows(45));
    renderWithProviders(<ContextsTab />);

    await waitFor(() => expect(dataRows()).toHaveLength(20));
    expect(lastRequest(requests).get("limit")).toBe("20");
    expect(lastRequest(requests).get("offset")).toBe("0");
  });

  it("размер страницы читается и пишется ключом gca.families.contexts.pageSize", async () => {
    localStorage.setItem("gca.families.contexts.pageSize", "50");
    const requests = useManyContextRows(manyContextRows(60));
    const user = userEvent.setup();
    renderWithProviders(<ContextsTab />);

    await waitFor(() => expect(dataRows()).toHaveLength(50));
    expect(lastRequest(requests).get("limit")).toBe("50");

    await user.click(screen.getByRole("combobox", { name: "На странице:" }));
    await user.click(await screen.findByRole("option", { name: "10" }));
    await waitFor(() => expect(dataRows()).toHaveLength(10));
    expect(localStorage.getItem("gca.families.contexts.pageSize")).toBe("10");
  });

  it("выдача сузилась под прежним offset — следующий запрос уходит с последней валидной страницы, а не повторяет старый offset", async () => {
    // `total` меняется НЕЗАВИСИМО от параметров запроса — так же, как
    // сокращение выдачи действием в карточке (спека §2.5, §2.8) или
    // соседа-фильтра меняет ответ БЕЗ смены `page`/`offset` на этом экране.
    let total = 45;
    const rows = manyContextRows(45);
    const requests: URLSearchParams[] = [];
    const queryClient = createTestQueryClient();
    server.use(
      http.get("/api/v1/semantic/contexts", ({ request }) => {
        const url = new URL(request.url);
        requests.push(url.searchParams);
        const limit = Number(url.searchParams.get("limit") ?? 20);
        const offset = Number(url.searchParams.get("offset") ?? 0);
        return HttpResponse.json({ items: rows.slice(offset, offset + limit), total, limit, offset });
      })
    );
    const user = userEvent.setup();
    renderWithProviders(<ContextsTab />, { queryClient });
    await waitFor(() => expect(screen.getByText("Синтетическая работа №1")).toBeInTheDocument());

    // Страница 3 (offset 40) — валидна при total 45.
    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await waitFor(() => expect(lastRequest(requests).get("offset")).toBe("40"));

    // Выдача сократилась БЕЗ смены страницы — offset=40 при limit=20 больше
    // не покрывается 20 позициями.
    total = 20;
    await queryClient.invalidateQueries();

    // Следующий запрос обязан уйти с ИСПРАВЛЕННЫМ offset (последняя валидная
    // страница при total=20, limit=20 — offset=0), не повторить старый 40.
    await waitFor(() => expect(lastRequest(requests).get("offset")).toBe("0"));
    await waitFor(() => expect(screen.getByText("1–20 из 20")).toBeInTheDocument());
  });

  // ---------------------------------------------------------------------
  //  Коды полей на экран не выводятся (спека §2.2, Global Constraints
  //  ветки). Фикстуры `handlers.ts` не несут LOCATION_ONLY,
  //  NOT_APPLICABLE и UNKNOWN — выдача синтетическая и содержит ВСЕ коды.
  //  Карточка (`ContextCard`) здесь не открыта: её коды проверяются
  //  отдельно, в `ContextCard.test.tsx`.
  // ---------------------------------------------------------------------

  it("ни один код полей не виден в списке и в фильтрах — с выдачей, которая несёт их все", async () => {
    const rows = manyContextRows(4);
    Object.assign(rows[0], { name_role: "LOCATION_ONLY", semantic_state: "CONFIRMED" });
    Object.assign(rows[1], { name_role: "GENERIC_WORK", semantic_state: "NOT_APPLICABLE" });
    Object.assign(rows[2], { semantic_kind: "UNKNOWN", semantic_state: "SUGGESTED" });
    Object.assign(rows[3], { semantic_kind: "SYSTEM" });
    useManyContextRows(rows);
    const user = userEvent.setup();
    renderWithProviders(<ContextsTab />);
    await waitFor(() => expect(screen.getByText("Синтетическая работа №4")).toBeInTheDocument());

    const CODES = /LOCATION_ONLY|GENERIC_WORK|SUGGESTED|CONFIRMED|NOT_APPLICABLE|UNKNOWN|SYSTEM|\bWORK\b/;
    expect(document.body.textContent).not.toMatch(CODES);

    // «система» — только у SYSTEM, не у UNKNOWN (вытесненное состояние
    // предиката: «не WORK» ≠ «SYSTEM»).
    const unknownRow = screen.getByText("Синтетическая работа №3").closest("tr")!;
    expect(within(unknownRow).queryByText("система")).not.toBeInTheDocument();
    const systemRow = screen.getByText("Синтетическая работа №4").closest("tr")!;
    expect(within(systemRow).getByText("система")).toBeInTheDocument();

    // Выбранное значение фильтра показывается ПОДПИСЬЮ в самом контроле.
    await user.click(screen.getByRole("combobox", { name: "Вид" }));
    await user.click(within(await screen.findByRole("listbox")).getByText("не определён"));
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Вид" })).toHaveTextContent("не определён"));

    await user.click(screen.getByRole("combobox", { name: "Наименование называет" }));
    await user.click(within(await screen.findByRole("listbox")).getByText("место"));
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Наименование называет" })).toHaveTextContent("место"));

    await user.click(screen.getByRole("combobox", { name: "Состояние" }));
    await user.click(within(await screen.findByRole("listbox")).getByText("не применяется"));
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "Состояние" })).toHaveTextContent("не применяется")
    );

    expect(document.body.textContent).not.toMatch(CODES);
  });
});
