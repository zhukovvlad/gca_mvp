import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";

import { FamiliesTab } from "@/pages/families/FamiliesTab";
import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { createTestQueryClient, renderWithProviders } from "@/test/utils";

/**
 * Категория семьи на вкладке «Семьи» (спека 3б §2.9, §2.13, макет К1): колонка после «Статуса»,
 * фильтр рядом с «Единицей», кнопка «Категории…», выбор на карточке семьи, «Активировать» без
 * категории. Фикстура: семья 43 «Кровельные работы» (активна) и 44 архивная — «Работа» (1) и без
 * категории; семья 1 — черновик с категорией «Работа», остальные черновики без категории.
 */

async function renderTab() {
  renderWithProviders(<FamiliesTab />);
  await waitFor(() => expect(screen.getByText("Семья работ №3")).toBeInTheDocument());
}

/** Статус «все» и страница 100: в списке видны все три статуса разом. */
async function showEverything(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("combobox", { name: "Статус" }));
  await user.click(await screen.findByRole("option", { name: "все" }));
  await user.click(screen.getByRole("combobox", { name: "На странице:" }));
  await user.click(await screen.findByRole("option", { name: "100" }));
  await waitFor(() => expect(screen.getByText("Кровельные работы")).toBeInTheDocument());
}

function rowOf(title: string): HTMLElement {
  return screen.getByText(title).closest("tr")!;
}

describe("FamiliesTab: категория семьи", () => {
  afterEach(() => {
    localStorage.clear();
  });

  it("колонка «Категория» стоит сразу после «Статуса» и печатает имя категории строки", async () => {
    const user = userEvent.setup();
    await renderTab();
    await showEverything(user);

    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers.indexOf("Категория")).toBe(headers.indexOf("Статус") + 1);

    const cells = (title: string) => within(rowOf(title)).getAllByRole("cell");
    const categoryIndex = headers.indexOf("Категория");
    // Активная семья 43 размечена «Работа»; черновик без категории — прочерк, а не пустота.
    expect(cells("Кровельные работы")[categoryIndex]).toHaveTextContent("Работа");
    expect(cells("Семья работ №3")[categoryIndex]).toHaveTextContent("—");
  });

  it("фильтр по категории уходит в запрос и сужает список; «без категории» — отдельный запрос", async () => {
    const user = userEvent.setup();
    await renderTab();
    await showEverything(user);

    await user.click(screen.getByRole("combobox", { name: "Категория (фильтр)" }));
    for (const title of ["Любая категория", "Работа", "Инженерная система", "Без категории"]) {
      expect(await screen.findByRole("option", { name: title })).toBeInTheDocument();
    }
    await user.click(screen.getByRole("option", { name: "Работа" }));

    await waitFor(() =>
      expect(handlerState.familiesRequests.some((q) => q.includes("family_category_id=1"))).toBe(true)
    );
    await waitFor(() => {
      expect(screen.getByText("Кровельные работы")).toBeInTheDocument();
      expect(screen.getByText("Семья работ №1")).toBeInTheDocument();
      expect(screen.queryByText("Семья работ №3")).not.toBeInTheDocument();
    });

    await user.click(screen.getByRole("combobox", { name: "Категория (фильтр)" }));
    await user.click(await screen.findByRole("option", { name: "Без категории" }));

    await waitFor(() =>
      expect(handlerState.familiesRequests.some((q) => q.includes("family_category_id=none"))).toBe(true)
    );
    await waitFor(() => {
      expect(screen.getByText("Семья работ №3")).toBeInTheDocument();
      expect(screen.queryByText("Кровельные работы")).not.toBeInTheDocument();
    });
  });

  it("возврат к «Любой категории» снимает фильтр: параметр в запрос не уходит", async () => {
    const user = userEvent.setup();
    await renderTab();
    await showEverything(user);

    await user.click(screen.getByRole("combobox", { name: "Категория (фильтр)" }));
    await user.click(await screen.findByRole("option", { name: "Работа" }));
    await waitFor(() => expect(screen.queryByText("Семья работ №3")).not.toBeInTheDocument());

    handlerState.familiesRequests = [];
    await user.click(screen.getByRole("combobox", { name: "Категория (фильтр)" }));
    await user.click(await screen.findByRole("option", { name: "Любая категория" }));

    await waitFor(() => expect(screen.getByText("Семья работ №3")).toBeInTheDocument());
    expect(handlerState.familiesRequests.some((q) => q.includes("family_category_id"))).toBe(false);
  });

  it("фильтр входит в ключ кэша: у каждого значения свой запрос, а не общий список", async () => {
    const user = userEvent.setup();
    const queryClient = createTestQueryClient();
    renderWithProviders(<FamiliesTab />, { queryClient });
    await waitFor(() => expect(screen.getByText("Семья работ №3")).toBeInTheDocument());

    // Пятый элемент ключа — фильтр категории; неактивные запросы кэш тут же отдаёт сборщику,
    // поэтому ключ читается, пока значение выбрано.
    const categoryKeys = () =>
      queryClient
        .getQueryCache()
        .findAll({ queryKey: ["work-families", "list"] })
        .map((q) => q.queryKey[4]);
    expect(categoryKeys()).not.toContain(1);

    await user.click(screen.getByRole("combobox", { name: "Категория (фильтр)" }));
    await user.click(await screen.findByRole("option", { name: "Работа" }));
    await waitFor(() => expect(screen.queryByText("Семья работ №3")).not.toBeInTheDocument());
    expect(categoryKeys()).toContain(1);

    await user.click(screen.getByRole("combobox", { name: "Категория (фильтр)" }));
    await user.click(await screen.findByRole("option", { name: "Без категории" }));
    await waitFor(() => expect(screen.getByText("Семья работ №3")).toBeInTheDocument());
    expect(categoryKeys()).toContain("none");
  });

  it("«Категории…» открывает справочник с числом семей", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("button", { name: "Категории…" }));

    const dialog = await screen.findByRole("dialog", { name: "Категории семей" });
    expect(await within(dialog).findByText("Инженерная система")).toBeInTheDocument();
    expect(within(dialog).getByRole("columnheader", { name: "Семей" })).toBeInTheDocument();
  });

  it("карточка семьи показывает её категорию; смена шлёт family_category_id, список обновляется", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByText("Семья работ №1"));
    const select = await screen.findByRole("combobox", { name: "Категория" });
    expect(select).toHaveTextContent("Работа");

    await user.click(select);
    await user.click(await screen.findByRole("option", { name: "Инженерная система" }));

    await waitFor(() =>
      expect(handlerState.lastUpdateFamilyRequest).toEqual({ id: 1, body: { family_category_id: 2 } })
    );
    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
    await waitFor(() =>
      expect(within(rowOf("Семья работ №1")).getAllByRole("cell")[headers.indexOf("Категория")]).toHaveTextContent(
        "Инженерная система"
      )
    );
  });

  it("у черновика без категории поле пусто, «Активировать» недоступна и подпись называет причину", async () => {
    const user = userEvent.setup();
    // Семья 1 несёт определение, но категории у неё нет: единственная причина недоступности.
    const family = handlerState.workFamilies.find((f) => f.id === 1)!;
    family.family_category_id = null;
    family.family_category_title = null;
    await renderTab();

    await user.click(screen.getByText("Семья работ №1"));
    expect(await screen.findByRole("combobox", { name: "Категория" })).toHaveTextContent("Выбрать категорию");

    const activate = await screen.findByRole("button", { name: "Активировать семью Семья работ №1" });
    expect(activate).toBeDisabled();
    expect(screen.getByText("Сначала выберите категорию семьи.")).toBeInTheDocument();
  });

  it("после выбора категории «Активировать» доступна, подпись исчезает, активация доходит до сервера", async () => {
    const user = userEvent.setup();
    const family = handlerState.workFamilies.find((f) => f.id === 1)!;
    family.family_category_id = null;
    family.family_category_title = null;
    await renderTab();

    await user.click(screen.getByText("Семья работ №1"));
    const activate = await screen.findByRole("button", { name: "Активировать семью Семья работ №1" });
    expect(activate).toBeDisabled();

    await user.click(screen.getByRole("combobox", { name: "Категория" }));
    await user.click(await screen.findByRole("option", { name: "Работа" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Активировать семью Семья работ №1" })).toBeEnabled()
    );
    expect(screen.queryByText("Сначала выберите категорию семьи.")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Активировать семью Семья работ №1" }));
    await waitFor(() => expect(family.status).toBe("active"));
  });

  // --- Дописано ревью задачи 5: решения, которые не предъявлял ни один тест. ---

  it("переход к семье по ссылке снимает фильтр категории: иначе отфильтрованная семья не открылась бы", async () => {
    const user = userEvent.setup();
    const { rerender } = renderWithProviders(<FamiliesTab focusFamilyId={null} />);
    await waitFor(() => expect(screen.getByText("Семья работ №3")).toBeInTheDocument());

    const filter = () => screen.getByRole("combobox", { name: "Категория (фильтр)" });
    await user.click(filter());
    await user.click(await screen.findByRole("option", { name: "Работа" }));
    await waitFor(() => expect(filter()).toHaveTextContent("Работа"));

    // Семья 43 размечена «Работа» — фильтр «Без категории» её скрывает.
    await user.click(filter());
    await user.click(await screen.findByRole("option", { name: "Без категории" }));
    await waitFor(() => expect(screen.queryByText("Семья работ №1")).not.toBeInTheDocument());
    expect(filter()).toHaveTextContent("Без категории");

    rerender(<FamiliesTab focusFamilyId={43} />);

    expect(await screen.findByDisplayValue("Кровельные работы")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Категория (фильтр)" })).toHaveTextContent("Любая категория");
  });

  it("смена фильтра категории возвращает на первую страницу, даже когда выдача уже в кэше", async () => {
    const user = userEvent.setup();
    // Выдача без кэша на время загрузки пуста, и зажим страницы сам вернул бы на первую —
    // сброс страницы виден только на закэшированной выдаче (как у живого клиента, gcTime 5 мин).
    const queryClient = createTestQueryClient();
    queryClient.setDefaultOptions({
      ...queryClient.getDefaultOptions(),
      queries: { ...queryClient.getDefaultOptions().queries, gcTime: Infinity },
    });
    renderWithProviders(<FamiliesTab />, { queryClient });
    await waitFor(() => expect(screen.getByText("Семья работ №3")).toBeInTheDocument());
    const filter = () => screen.getByRole("combobox", { name: "Категория (фильтр)" });

    // Выдача «Без категории» попадает в кэш: 41 черновик (у семьи 1 категория есть), три страницы.
    await user.click(filter());
    await user.click(await screen.findByRole("option", { name: "Без категории" }));
    await waitFor(() => expect(screen.queryByText("Семья работ №1")).not.toBeInTheDocument());
    await user.click(filter());
    await user.click(await screen.findByRole("option", { name: "Любая категория" }));
    await waitFor(() => expect(screen.getByText("Семья работ №1")).toBeInTheDocument());

    // Черновики: 42 семьи по 20 на странице; третья страница — семьи 41–42.
    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await waitFor(() => expect(screen.getByText("Семья работ №41")).toBeInTheDocument());

    await user.click(filter());
    await user.click(await screen.findByRole("option", { name: "Без категории" }));

    await waitFor(() => expect(screen.getByText("Устройство покрытий полов")).toBeInTheDocument());
    expect(screen.queryByText("Семья работ №42")).not.toBeInTheDocument();
  });

  it("переименование категории в справочнике видно в колонке списка семей", async () => {
    const user = userEvent.setup();
    await renderTab();
    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
    const categoryCell = () => within(rowOf("Семья работ №1")).getAllByRole("cell")[headers.indexOf("Категория")];
    expect(categoryCell()).toHaveTextContent("Работа");

    await user.click(screen.getByRole("button", { name: "Категории…" }));
    const dialog = await screen.findByRole("dialog", { name: "Категории семей" });
    const workRow = (await within(dialog).findByText("Работа", { selector: "td" })).closest("tr")!;
    await user.click(within(workRow).getByRole("button", { name: "Править" }));
    const title = within(dialog).getByLabelText("Имя категории");
    await user.clear(title);
    await user.type(title, "Общестрой");
    await user.click(within(dialog).getByRole("button", { name: "Сохранить" }));
    await within(dialog).findByText("Общестрой", { selector: "td" });
    await user.click(within(dialog).getByRole("button", { name: "Закрыть" }));

    await waitFor(() => expect(categoryCell()).toHaveTextContent("Общестрой"));
  });

  it("смена категории на карточке семьи двигает «Семей» в справочнике", async () => {
    const user = userEvent.setup();
    await renderTab();
    const countOf = (dialog: HTMLElement, title: string) =>
      within(within(dialog).getByText(title, { selector: "td" }).closest("tr")!).getAllByRole("cell")[2];

    await user.click(screen.getByRole("button", { name: "Категории…" }));
    let dialog = await screen.findByRole("dialog", { name: "Категории семей" });
    await within(dialog).findByText("Инженерная система", { selector: "td" });
    // «Работа» — семьи 1 и 43; «Инженерная система» пуста.
    expect(countOf(dialog, "Работа")).toHaveTextContent("2");
    expect(countOf(dialog, "Инженерная система")).toHaveTextContent("0");
    await user.click(within(dialog).getByRole("button", { name: "Закрыть" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Категории семей" })).not.toBeInTheDocument());

    await user.click(screen.getByText("Семья работ №1"));
    await user.click(await screen.findByRole("combobox", { name: "Категория" }));
    await user.click(await screen.findByRole("option", { name: "Инженерная система" }));
    await waitFor(() => expect(handlerState.lastUpdateFamilyRequest).toEqual({ id: 1, body: { family_category_id: 2 } }));

    await user.click(screen.getByRole("button", { name: "Категории…" }));
    dialog = await screen.findByRole("dialog", { name: "Категории семей" });
    await waitFor(() => expect(countOf(dialog, "Инженерная система")).toHaveTextContent("1"));
    expect(countOf(dialog, "Работа")).toHaveTextContent("1");
  });

  it("выбор той же категории на карточке запроса не шлёт", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByText("Семья работ №1"));
    await user.click(await screen.findByRole("combobox", { name: "Категория" }));
    await user.click(await screen.findByRole("option", { name: "Работа" }));
    await waitFor(() => expect(screen.queryByRole("option", { name: "Работа" })).not.toBeInTheDocument());
    await new Promise((resolve) => setTimeout(resolve, 200));

    expect(handlerState.lastUpdateFamilyRequest).toBeNull();
  });

  it("пока смена категории в пути, выбор недоступен", async () => {
    const user = userEvent.setup();
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.patch("/api/v1/semantic/families/:id", async () => {
        await held;
        return HttpResponse.json({ detail: { code: "category_not_found", message: "x" } }, { status: 409 });
      })
    );
    await renderTab();

    await user.click(screen.getByText("Семья работ №1"));
    await user.click(await screen.findByRole("combobox", { name: "Категория" }));
    await user.click(await screen.findByRole("option", { name: "Инженерная система" }));

    await waitFor(() => expect(screen.getByRole("combobox", { name: "Категория" })).toBeDisabled());
    release();
  });

  it("отказ сервера activate_without_category подписан по коду, а не сырым текстом", async () => {
    const user = userEvent.setup();
    await renderTab();
    await user.click(screen.getByText("Семья работ №1"));
    const activate = await screen.findByRole("button", { name: "Активировать семью Семья работ №1" });
    expect(activate).toBeEnabled();

    // Категорию у семьи сняли на сервере, пока экран был открыт: экран ещё считает её размеченной.
    server.use(
      http.post("/api/v1/semantic/families/:id/activate", () =>
        HttpResponse.json(
          { detail: { code: "activate_without_category", message: "сырой текст сервера", family_id: 1 } },
          { status: 422 }
        )
      )
    );
    await user.click(activate);

    expect(await screen.findByText("Сначала выберите категорию семьи.")).toBeInTheDocument();
    expect(screen.queryByText("сырой текст сервера")).not.toBeInTheDocument();
  });

  it("«Новая семья» с категорией: поле необязательно, категория уходит в запрос, «Семей» в справочнике растёт", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("button", { name: /Новая семья/ }));
    await user.type(screen.getByLabelText("Название (обязательно)"), "Проектные работы");
    await user.click(screen.getByRole("combobox", { name: "Категория" }));
    await user.click(await screen.findByRole("option", { name: "Проектирование" }));
    await user.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() =>
      expect(handlerState.lastCreateFamilyRequest).toMatchObject({
        title: "Проектные работы",
        family_category_id: 4,
      })
    );
    // Справочник перечитан: у «Проектирования» была ноль семей, стала одна.
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Категории…" }));
    const dialog = await screen.findByRole("dialog", { name: "Категории семей" });
    const row = (await within(dialog).findByText("Проектирование", { selector: "td" })).closest("tr")!;
    await waitFor(() => expect(within(row).getByText("1")).toBeInTheDocument());
  });

  it("«Новая семья» без категории: поле в запрос не попадает", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("button", { name: /Новая семья/ }));
    await user.type(screen.getByLabelText("Название (обязательно)"), "Без категории");
    await user.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() => expect(handlerState.lastCreateFamilyRequest).not.toBeNull());
    expect(handlerState.lastCreateFamilyRequest).not.toHaveProperty("family_category_id");
  });

  it("«Категории…» стоит сразу за фильтром категории, а не рядом с «Новой семьёй»", async () => {
    await renderTab();

    const filter = screen.getByRole("combobox", { name: "Категория (фильтр)" });
    const button = screen.getByRole("button", { name: "Категории…" });
    const newFamily = screen.getByRole("button", { name: /Новая семья/ });
    // Порядок в документе: фильтр, «Категории…», «Новая семья».
    expect(filter.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(button.compareDocumentPosition(newFamily) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Ближайший общий предок кнопки и фильтра не содержит «Новую семью».
    let ancestor: HTMLElement | null = button.parentElement;
    while (ancestor && !ancestor.contains(filter)) ancestor = ancestor.parentElement;
    expect(ancestor!.contains(newFamily)).toBe(false);
  });
});
