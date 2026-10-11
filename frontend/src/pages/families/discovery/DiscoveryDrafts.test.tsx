import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { draftFixture, draftsViewFixture, handlerState } from "@/test/handlers";
import { renderWithProviders, waitForDialogFocus } from "@/test/utils";
import type { DiscoveryDraftsView, NotWorkRow } from "@/types/domain";

import { DiscoveryDrafts } from "./DiscoveryDrafts";

/**
 * Экран черновиков единицы M3 (id 3, «м³»; экран 3 макета). Фикстура открытия 500:
 * 301 «Вентиляция» (66 строк, отмечен), 302 «Кондиционирование» (43, отмечен),
 * 303 «Кабельные линии» (21, похоже на активную семью — не отмечен), 304 без категории
 * (не отмечен); «Не работа» — три наименования, у первого два контекста.
 */

const VENT = draftFixture({ id: 301, ordinal: 1, title: "Вентиляция общеобменная", rows: 66 });
const COND = draftFixture({ id: 302, ordinal: 2, title: "Кондиционирование", rows: 43 });
const CABLE = draftFixture({
  id: 303,
  ordinal: 3,
  title: "Кабельные линии",
  rows: 21,
  similar_family_id: 43,
  similar_family_title: "Кровельные работы",
  similar_family_status: "active",
});
const NO_CATEGORY = draftFixture({
  id: 304,
  ordinal: 4,
  title: "Гарантии",
  rows: 4,
  family_category_id: null,
  family_category_title: null,
});

const NOT_WORK_NAMES: NotWorkRow[] = [
  { title: "Примечание к смете", contexts: 2, context_ids: [901, 902] },
  { title: "Оговорка о материалах", contexts: 1, context_ids: [903] },
  { title: "Итого по разделу", contexts: 1, context_ids: [904] },
];

function seed(overrides: Partial<DiscoveryDraftsView> = {}) {
  handlerState.discovery.units = [
    {
      unit_id: 3,
      unit_code: "M3",
      systems: 0,
      new_family: 5,
      bare: 0,
      names: 5,
      uncategorized_families: 0,
      active_families: 1,
      reserve_usd: "0.1",
      expected_cached_usd: "0.05",
      last_discovery: { job_id: 500, status: "done", at: "2026-10-09T10:00:00", open_drafts: 4, actionable: true },
    },
  ];
  handlerState.discovery.drafts["3"] = draftsViewFixture({
    drafts: [VENT, COND, CABLE, NO_CATEGORY].map((d) => ({ ...d })),
    not_work: { id: 9, names: NOT_WORK_NAMES.map((n) => ({ ...n })) },
    ...overrides,
  });
}

async function renderDrafts(onActivated = vi.fn()) {
  renderWithProviders(<DiscoveryDrafts unitId={3} unitLabel="м³" onActivated={onActivated} onRelaunch={vi.fn()} />);
  await screen.findAllByTestId("draft-card");
  return onActivated;
}

function cardOf(title: string) {
  const card = screen
    .getAllByTestId("draft-card")
    .find((c) => within(c).queryByText(title) !== null);
  if (!card) throw new Error(`нет черновика ${title}`);
  return card;
}

const counter = () => screen.getByTestId("discovery-counter");
const activateButton = () =>
  screen.getByRole("button", { name: "Активировать отмеченные и перезапросить «м³»…" });

describe("DiscoveryDrafts — показ", () => {
  it("черновики в порядке сервера, шапка с числом и датой, запрос единицы 3", async () => {
    seed();
    await renderDrafts();

    expect(screen.getByText("Черновики семей · м³")).toBeInTheDocument();
    expect(screen.getByText(/4 черновика · открыто 09\.10\.2026/)).toBeInTheDocument();
    const titles = screen
      .getAllByTestId("draft-card")
      .map((card) => card.querySelector("span.font-semibold")?.textContent);
    expect(titles).toEqual([
      "Вентиляция общеобменная",
      "Кондиционирование",
      "Кабельные линии",
      "Гарантии",
    ]);
    expect(handlerState.discovery.draftsRequests).toContain("3");
  });

  it("единица без выполненных открытий: «Открытий не было»", async () => {
    handlerState.discovery.drafts = {};
    renderWithProviders(<DiscoveryDrafts unitId={3} unitLabel="м³" onActivated={vi.fn()} onRelaunch={vi.fn()} />);

    expect(await screen.findByText("Открытий не было")).toBeInTheDocument();
  });

  it("отмечены по умолчанию черновики с категорией без «похоже на»; счётчик считает их строки", async () => {
    seed();
    await renderDrafts();

    const box = (title: string) =>
      within(cardOf(title)).getByRole("checkbox", { name: `Отметить черновик «${title}»` });
    expect(box("Вентиляция общеобменная")).toBeChecked();
    expect(box("Кондиционирование")).toBeChecked();
    expect(box("Кабельные линии")).not.toBeChecked();
    expect(box("Гарантии")).not.toBeChecked();
    expect(counter()).toHaveTextContent("Отмечено 2 черновика · 109 строк");
  });

  it("отметка черновика меняет счётчик; «Не работа» и пары считаются отдельно", async () => {
    const user = userEvent.setup();
    seed({
      category_proposals: [
        { family_id: 43, family_title: "Кровельные работы", family_definition: "Определение: Кровельные работы.", family_category_id: 1, family_category_title: "Работа" },
      ],
    });
    await renderDrafts();

    await user.click(
      within(cardOf("Кабельные линии")).getByRole("checkbox", { name: "Отметить черновик «Кабельные линии»" })
    );

    expect(counter()).toHaveTextContent("Отмечено 3 черновика · 130 строк");
    expect(counter()).toHaveTextContent("«Не работа»: 4 строки");
    expect(counter()).toHaveTextContent("категорий семьям: 1");
  });

  it("«Не работа» построчно, «в активные семьи» и остаток — свёрнутыми строками с числами", async () => {
    const user = userEvent.setup();
    seed({
      existing: [{ id: 11, family_id: 43, family_title: "Кровельные работы", family_status: "active", rows: 7 }],
      rest: 5,
    });
    await renderDrafts();

    expect(screen.getAllByTestId("not-work-row")).toHaveLength(3);
    expect(screen.getByTestId("discovery-rest")).toHaveTextContent("Без группы: 5 строк");
    const trigger = screen.getByText(/В активные семьи: 7 строк/);
    expect(screen.queryByTestId("existing-group")).not.toBeInTheDocument();
    await user.click(trigger);
    expect(await screen.findByTestId("existing-group")).toHaveTextContent(
      "7 строк модель отнесла к «Кровельные работы» — придут предложениями при перезапросе"
    );
  });

  it("«В активные семьи» в свёрнутой строке — сумма строк всех групп", async () => {
    // Ревью задачи 6: с одной группой сумма неотличима от числа первой.
    seed({
      existing: [
        { id: 11, family_id: 43, family_title: "Кровельные работы", family_status: "active", rows: 7 },
        { id: 12, family_id: 44, family_title: "Демонтаж", family_status: "archived", rows: 5 },
      ],
    });
    await renderDrafts();

    expect(screen.getByText(/В активные семьи: 12 строк/)).toBeInTheDocument();
  });

  it("нет группы «Не работа» и остатка — их блоков нет", async () => {
    seed({ not_work: null, rest: 0 });
    await renderDrafts();

    expect(screen.queryByTestId("not-work-group")).not.toBeInTheDocument();
    expect(screen.queryByTestId("discovery-rest")).not.toBeInTheDocument();
    expect(screen.queryByTestId("folded-draft")).not.toBeInTheDocument();
    expect(screen.queryByTestId("category-proposals")).not.toBeInTheDocument();
  });
});

describe("DiscoveryDrafts — активация", () => {
  it("шлёт отмеченные черновики, ВСЕ контексты отмеченных наименований «Не работа» и отмеченные пары категорий", async () => {
    const user = userEvent.setup();
    seed({
      category_proposals: [
        { family_id: 43, family_title: "Кровельные работы", family_definition: "Определение: Кровельные работы.", family_category_id: 1, family_category_title: "Работа" },
        { family_id: 44, family_title: "Банковская гарантия", family_definition: "Определение: Банковская гарантия.", family_category_id: 3, family_category_title: "Затраты и услуги" },
      ],
    });
    const onActivated = await renderDrafts();

    // Снимаем одно наименование «Не работа» и одну пару; у второй пары меняем категорию.
    await user.click(
      within(screen.getAllByTestId("not-work-row")[1]).getByRole("checkbox", { name: "Отметить строку" })
    );
    await user.click(
      screen.getByRole("checkbox", { name: "Применить категорию семье «Банковская гарантия»" })
    );
    await user.click(within(screen.getAllByTestId("category-proposal")[0]).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: "Затраты и услуги" }));
    await user.click(activateButton());

    await waitFor(() => expect(handlerState.discovery.activateRequests).toHaveLength(1));
    expect(handlerState.discovery.activateRequests[0]).toEqual({
      jobId: 500,
      body: {
        draft_ids: [301, 302],
        not_work_context_ids: [901, 902, 904],
        family_categories: [{ family_id: 43, family_category_id: 3 }],
      },
    });
    await waitFor(() => expect(onActivated).toHaveBeenCalledTimes(1));
    expect(onActivated.mock.calls[0][0]).toMatchObject({
      created_family_ids: [1201, 1202],
      reask_unit_id: 3,
    });
  });

  it("после успеха экран перечитан: активированные черновики ушли из списка", async () => {
    const user = userEvent.setup();
    seed();
    await renderDrafts();

    await user.click(activateButton());

    await waitFor(() => expect(screen.getAllByTestId("draft-card")).toHaveLength(2));
    expect(screen.queryByText("Вентиляция общеобменная", { selector: "span" })).not.toBeInTheDocument();
    expect(screen.getByText(/Уже активировано: «Вентиляция общеобменная», «Кондиционирование»/)).toBeInTheDocument();
  });

  it("пропущенные строки и категории названы уведомлением с числом и именами", async () => {
    const user = userEvent.setup();
    seed({
      category_proposals: [
        { family_id: 43, family_title: "Кровельные работы", family_definition: "Определение: Кровельные работы.", family_category_id: 1, family_category_title: "Работа" },
      ],
    });
    handlerState.discovery.activationOutcome = {
      created_family_ids: [1201, 1202],
      categories_applied: [],
      categories_skipped: [43],
      not_work_applied: [901, 902, 904],
      not_work_skipped: [903],
      reask_unit_id: 3,
    };
    await renderDrafts();

    await user.click(activateButton());

    const notice = await screen.findByText(/строк «Не работа» пропущено: 1/);
    expect(notice).toHaveTextContent("(«Оговорка о материалах»)");
    expect(notice).toHaveTextContent("категорий пропущено: 1 («Кровельные работы»)");
  });

  it("ничего не отмечено — кнопка недоступна, запроса нет", async () => {
    const user = userEvent.setup();
    seed({ not_work: null });
    await renderDrafts();

    await user.click(
      within(cardOf("Вентиляция общеобменная")).getByRole("checkbox", { name: /Отметить черновик/ })
    );
    await user.click(
      within(cardOf("Кондиционирование")).getByRole("checkbox", { name: /Отметить черновик/ })
    );

    expect(counter()).toHaveTextContent("Отмечено 0 черновиков · 0 строк");
    expect(activateButton()).toBeDisabled();
    expect(handlerState.discovery.activateRequests).toEqual([]);
  });

  it("только «Не работа»: активация возможна без черновиков", async () => {
    const user = userEvent.setup();
    seed();
    await renderDrafts();
    await user.click(
      within(cardOf("Вентиляция общеобменная")).getByRole("checkbox", { name: /Отметить черновик/ })
    );
    await user.click(
      within(cardOf("Кондиционирование")).getByRole("checkbox", { name: /Отметить черновик/ })
    );

    await user.click(activateButton());

    await waitFor(() => expect(handlerState.discovery.activateRequests).toHaveLength(1));
    expect(handlerState.discovery.activateRequests[0].body.draft_ids).toEqual([]);
    expect(handlerState.discovery.activateRequests[0].body.not_work_context_ids).toEqual([
      901, 902, 903, 904,
    ]);
  });

  it("отказ «без категории»: подпись у черновика, отметки те же, окно перезапроса не открывается", async () => {
    const user = userEvent.setup();
    seed();
    handlerState.discovery.refusal = {
      action: "activate",
      code: "draft_without_category",
      status: 422,
      context: { draft_id: 302 },
    };
    const onActivated = await renderDrafts();
    await user.click(
      within(cardOf("Кабельные линии")).getByRole("checkbox", { name: /Отметить черновик/ })
    );
    await user.click(
      within(screen.getAllByTestId("not-work-row")[0]).getByRole("checkbox", { name: "Отметить строку" })
    );
    const before = counter().textContent;

    await user.click(activateButton());

    const refusal = await within(cardOf("Кондиционирование")).findByTestId("draft-refusal");
    expect(refusal).toHaveTextContent("У черновика «Кондиционирование» не выбрана категория.");
    expect(onActivated).not.toHaveBeenCalled();
    // Отметки не сброшены: тот же счётчик, те же галочки, чужие карточки без подписи.
    expect(counter().textContent).toBe(before);
    for (const title of ["Вентиляция общеобменная", "Кондиционирование", "Кабельные линии"]) {
      expect(
        within(cardOf(title)).getByRole("checkbox", { name: `Отметить черновик «${title}»` })
      ).toBeChecked();
    }
    expect(
      within(screen.getAllByTestId("not-work-row")[0]).getByRole("checkbox", { name: "Отметить строку" })
    ).not.toBeChecked();
    expect(screen.getAllByTestId("draft-refusal")).toHaveLength(1);
    // Ревью задачи 6: отказ с черновиком печатается ТОЛЬКО подписью — общего тоста рядом нет.
    expect(document.querySelectorAll("[data-sonner-toast]")).toHaveLength(0);
  });

  it("отказ «дубль активной семьи»: подпись называет черновик и единицу; повторное нажатие снимает подпись", async () => {
    const user = userEvent.setup();
    seed();
    handlerState.discovery.refusal = {
      action: "activate",
      code: "duplicate_active_family",
      status: 409,
      context: { draft_id: 301, ordinal: 1 },
    };
    await renderDrafts();

    await user.click(activateButton());
    expect(await screen.findByTestId("draft-refusal")).toHaveTextContent(
      "Активная семья «Вентиляция общеобменная» в единице «м³» уже есть — переименуйте черновик или слейте его с ней."
    );

    // Ревью задачи 6: второй отказ — без черновика, поэтому карточка «Вентиляции» остаётся на экране;
    // подпись обязана сняться самим повторным нажатием, а не исчезновением карточки после успеха.
    handlerState.discovery.refusal = {
      action: "activate",
      code: "discovery_run_superseded",
      status: 409,
    };
    await user.click(activateButton());
    expect(
      await screen.findByText("Эти черновики устарели: единица открыта заново. Обновите экран.")
    ).toBeInTheDocument();
    expect(cardOf("Вентиляция общеобменная")).toBeInTheDocument();
    expect(screen.queryByTestId("draft-refusal")).not.toBeInTheDocument();
    expect(handlerState.discovery.activateRequests).toHaveLength(2);
  });

  it("иной отказ (открытие вытеснено) — тост, без подписи у черновика", async () => {
    const user = userEvent.setup();
    seed();
    handlerState.discovery.refusal = {
      action: "activate",
      code: "discovery_run_superseded",
      status: 409,
    };
    await renderDrafts();

    await user.click(activateButton());

    expect(
      await screen.findByText("Эти черновики устарели: единица открыта заново. Обновите экран.")
    ).toBeInTheDocument();
    expect(screen.queryByTestId("draft-refusal")).not.toBeInTheDocument();
  });
});

describe("DiscoveryDrafts — действия над черновиками", () => {
  it("«Отбросить» уводит черновик в свёрнутые, «Вернуть» возвращает; отметки соседей остаются", async () => {
    const user = userEvent.setup();
    seed();
    await renderDrafts();
    // Человек снял отметку у «Вентиляции» — перечитывание после действия над соседом её не сбрасывает.
    await user.click(
      within(cardOf("Вентиляция общеобменная")).getByRole("checkbox", { name: /Отметить черновик/ })
    );

    await user.click(within(cardOf("Кондиционирование")).getByRole("button", { name: "Отбросить" }));

    await waitFor(() => expect(screen.getAllByTestId("draft-card")).toHaveLength(3));
    expect(
      within(cardOf("Вентиляция общеобменная")).getByRole("checkbox", { name: /Отметить черновик/ })
    ).not.toBeChecked();
    await user.click(screen.getByText("Слитые и отброшенные (1)"));
    const folded = await screen.findByTestId("folded-draft");
    expect(folded).toHaveTextContent("«Кондиционирование» — отброшен");

    await user.click(within(folded).getByRole("button", { name: "Вернуть" }));

    await waitFor(() => expect(screen.getAllByTestId("draft-card")).toHaveLength(4));
    expect(handlerState.discovery.draftRequests.map((r) => r.action)).toEqual(["discard", "restore"]);
    expect(screen.queryByText(/Слитые и отброшенные/)).not.toBeInTheDocument();
  });

  it("слитые показывают цель: черновик или активную семью со статусом", async () => {
    const user = userEvent.setup();
    seed({
      drafts: [VENT, COND].map((d) => ({ ...d })),
      folded: [
        draftFixture({ id: 305, ordinal: 5, status: "merged", title: "Сплит-системы", merged_into_draft_id: 302 }),
        draftFixture({
          id: 306,
          ordinal: 6,
          status: "merged",
          title: "Кровля",
          merged_into_family_id: 43,
          merged_into_family_title: "Кровельные работы",
          merged_into_family_status: "archived",
        }),
      ],
    });
    await renderDrafts();

    await user.click(screen.getByText("Слитые и отброшенные (2)"));
    const rows = await screen.findAllByTestId("folded-draft");
    expect(rows[0]).toHaveTextContent("«Сплит-системы» — слит с черновиком «Кондиционирование»");
    expect(rows[1]).toHaveTextContent("«Кровля» — слит с семьёй «Кровельные работы» (в архиве)");
  });

  it("«Слить с…»: целями предложены другие открытые черновики, но не сам источник", async () => {
    // Ревью задачи 6: список целей собирает экран (`otherDrafts`), тест карточки его не видит.
    const user = userEvent.setup();
    seed();
    await renderDrafts();

    await user.click(within(cardOf("Вентиляция общеобменная")).getByRole("button", { name: "Слить с…" }));
    await screen.findByRole("dialog");
    await waitForDialogFocus();
    await user.click(screen.getByRole("combobox", { name: "Цель слияния" }));

    expect(await screen.findByRole("option", { name: "Черновик «Кондиционирование»" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Черновик «Гарантии»" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Черновик «Вентиляция общеобменная»" })).not.toBeInTheDocument();
  });

  it("«Слить с…» активной семьёй уводит черновик в слитые", async () => {
    const user = userEvent.setup();
    seed();
    await renderDrafts();

    await user.click(within(cardOf("Кабельные линии")).getByRole("button", { name: "Слить с «Кровельные работы»" }));

    await waitFor(() => expect(screen.getAllByTestId("draft-card")).toHaveLength(3));
    await user.click(screen.getByText("Слитые и отброшенные (1)"));
    expect(await screen.findByTestId("folded-draft")).toHaveTextContent(
      "«Кабельные линии» — слит с семьёй «Кровельные работы»"
    );
  });
});

describe("DiscoveryDrafts — «Не работа» и категории активных семей", () => {
  it("группа: первые 20 наименований, остальные под «Показать все»; галочка группы снимает всё", async () => {
    const user = userEvent.setup();
    const many = Array.from({ length: 25 }, (_, i) => ({
      title: `Оговорка ${i + 1}`,
      contexts: 1,
      context_ids: [2000 + i],
    }));
    seed({ not_work: { id: 9, names: many } });
    await renderDrafts();

    expect(screen.getAllByTestId("not-work-row")).toHaveLength(20);
    await user.click(screen.getByRole("button", { name: "Показать все" }));
    expect(screen.getAllByTestId("not-work-row")).toHaveLength(25);

    await user.click(screen.getByRole("checkbox", { name: "Отметить все строки «Не работа»" }));
    expect(screen.getByText(/из 25 отмечены/).textContent).toMatch(/^0 из 25/);
    expect(counter()).not.toHaveTextContent("«Не работа»");

    await user.click(screen.getByRole("checkbox", { name: "Отметить все строки «Не работа»" }));
    expect(counter()).toHaveTextContent("«Не работа»: 25 строк");
  });

  it("предложения категорий: блок с выбором категории", async () => {
    seed({
      category_proposals: [
        { family_id: 43, family_title: "Кровельные работы", family_definition: "Определение: Кровельные работы.", family_category_id: 1, family_category_title: "Работа" },
      ],
    });
    await renderDrafts();

    expect(screen.getByText("Категории активных семей")).toBeInTheDocument();
    expect(within(screen.getByTestId("category-proposal")).getByRole("combobox")).toHaveTextContent("Работа");
  });
});
