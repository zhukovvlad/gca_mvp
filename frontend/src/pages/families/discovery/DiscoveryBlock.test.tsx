import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  discoveryUnitFixture,
  draftFixture,
  draftsViewFixture,
  handlerState,
} from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";
import type { DiscoveryRunInfo } from "@/types/domain";

import { DiscoveryBlock } from "./DiscoveryBlock";

/**
 * Блок «Открыть семьи» над таблицей очереди «Новая» (экраны 1 и 3 макета): строка на единицу,
 * «открывается…» пока задание живо, экран черновиков вместо строки, окно перезапроса после
 * активации. Единицы — M3 (id 3, «м³»), M2 (id 5, «м²») и «без единицы» (`null`).
 */

const SYMBOL: Record<string, string> = { M3: "м³", M2: "м²" };
const unitLabel = (code: string | null) => (code === null ? "без единицы" : (SYMBOL[code] ?? code));

const RUN = (overrides: Partial<DiscoveryRunInfo> = {}): DiscoveryRunInfo => ({
  job_id: 500,
  status: "done",
  at: "2026-10-09T10:00:00",
  open_drafts: 0,
  actionable: false,
  ...overrides,
});

afterEach(() => {
  vi.useRealTimers();
});

function renderBlock(props: Partial<Parameters<typeof DiscoveryBlock>[0]> = {}) {
  renderWithProviders(<DiscoveryBlock unitLabel={unitLabel} {...props} />);
}

function rowOf(label: string) {
  const row = screen
    .getAllByTestId("discovery-unit-row")
    .find((r) => within(r).queryByText(label, { selector: "b" }) !== null);
  if (!row) throw new Error(`нет строки единицы ${label}`);
  return row;
}

describe("DiscoveryBlock — строки единиц", () => {
  it("строка на единицу: числа охвата, оценка и кнопка «Открыть семьи…»", async () => {
    handlerState.discovery.units = [
      discoveryUnitFixture({ systems: 1382, expected_cached_usd: "0.6" }),
      discoveryUnitFixture({
        unit_id: 5,
        unit_code: "M2",
        systems: 0,
        new_family: 278,
        bare: 4,
        uncategorized_families: 2,
        expected_cached_usd: "0.15",
      }),
    ];
    renderBlock();

    await screen.findAllByTestId("discovery-unit-row");
    expect(screen.getByText("Открыть семьи")).toBeInTheDocument();
    const m3 = rowOf("м³");
    expect(m3).toHaveTextContent(/1\s382 системы без семьи/);
    expect(m3).toHaveTextContent("≈ $0,60");
    expect(within(m3).getByRole("button", { name: "Открыть семьи…" })).toBeInTheDocument();
    const m2 = rowOf("м²");
    expect(m2).toHaveTextContent("278 строк с «новой семьёй»");
    expect(m2).toHaveTextContent("4 строки в единице без активных семей");
    expect(m2).toHaveTextContent("2 семьи без категории");
    expect(m2).not.toHaveTextContent("систем");
    expect(handlerState.discovery.unitsRequests).toBeGreaterThanOrEqual(1);
  });

  it("системы рядом с обычными строками — «и N систем» при «новой семье» и при «голых» строках", async () => {
    // Ревью задачи 6: без этого входа ветка «… и 1 система» (макет, экран 1, строка «шт») не стерегилась.
    handlerState.discovery.units = [
      discoveryUnitFixture({ systems: 1, new_family: 305, bare: 0 }),
      discoveryUnitFixture({ unit_id: 5, unit_code: "M2", systems: 2, new_family: 0, bare: 4 }),
    ];
    renderBlock();

    await screen.findAllByTestId("discovery-unit-row");
    expect(rowOf("м³")).toHaveTextContent("305 строк с «новой семьёй» и 1 система");
    expect(rowOf("м³")).not.toHaveTextContent("без семьи");
    expect(rowOf("м²")).toHaveTextContent("4 строки в единице без активных семей и 2 системы");
  });

  it("единиц нет — блока нет", async () => {
    renderBlock();

    await waitFor(() => expect(handlerState.discovery.unitsRequests).toBe(1));
    expect(screen.queryByTestId("discovery-block")).not.toBeInTheDocument();
  });

  it("фильтр единицы очереди сужает строки; «без единицы» — только строка с unit_id = null", async () => {
    handlerState.discovery.units = [
      discoveryUnitFixture(),
      discoveryUnitFixture({ unit_id: 5, unit_code: "M2" }),
      discoveryUnitFixture({ unit_id: null, unit_code: null }),
    ];
    const { unmount } = renderWithProviders(<DiscoveryBlock unitLabel={unitLabel} unitFilter={5} />);
    await screen.findAllByTestId("discovery-unit-row");
    expect(screen.getAllByTestId("discovery-unit-row")).toHaveLength(1);
    expect(rowOf("м²")).toBeInTheDocument();
    unmount();

    renderWithProviders(<DiscoveryBlock unitLabel={unitLabel} unitFilter="none" />);
    await screen.findAllByTestId("discovery-unit-row");
    expect(screen.getAllByTestId("discovery-unit-row")).toHaveLength(1);
    expect(rowOf("без единицы")).toBeInTheDocument();
  });
});

describe("DiscoveryBlock — запуск", () => {
  it("кнопка строки открывает окно запуска этой единицы; после запуска строка «открывается…»", async () => {
    const user = userEvent.setup();
    handlerState.discovery.units = [discoveryUnitFixture(), discoveryUnitFixture({ unit_id: 5, unit_code: "M2" })];
    renderBlock();
    await screen.findAllByTestId("discovery-unit-row");

    await user.click(within(rowOf("м²")).getByRole("button", { name: "Открыть семьи…" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Открыть семьи · м²" })).toBeInTheDocument();
    await within(dialog).findByText("$0,90");
    expect(handlerState.discovery.previewRequests).toEqual([5]);
    await user.click(within(dialog).getByRole("button", { name: "Открыть семьи" }));

    await waitFor(() => expect(handlerState.discovery.launchRequests).toHaveLength(1));
    // Блок перечитан: у запущенной единицы нет кнопки, у соседней она есть.
    await waitFor(() => expect(within(rowOf("м²")).getByRole("status")).toHaveTextContent("открывается…"));
    expect(within(rowOf("м²")).queryByRole("button", { name: "Открыть семьи…" })).not.toBeInTheDocument();
    expect(within(rowOf("м³")).getByRole("button", { name: "Открыть семьи…" })).toBeInTheDocument();
  });

  it("единица «без единицы» запускается с unit_id = null", async () => {
    const user = userEvent.setup();
    handlerState.discovery.units = [discoveryUnitFixture({ unit_id: null, unit_code: null })];
    renderBlock();
    await screen.findAllByTestId("discovery-unit-row");

    await user.click(within(rowOf("без единицы")).getByRole("button", { name: "Открыть семьи…" }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("$0,90");
    await user.click(within(dialog).getByRole("button", { name: "Открыть семьи" }));

    await waitFor(() =>
      expect(handlerState.discovery.launchRequests).toEqual([
        { unit_id: null, preview_hash: "discovery-hash-1" },
      ])
    );
    expect(handlerState.discovery.previewRequests).toEqual([null]);
  });
});

describe("DiscoveryBlock — живое открытие", () => {
  // Интервал опроса подменяется ДО монтирования; ответ меняется между опросами — тест не ждёт
  // реального времени.
  it("пока задание живо — «открывается…» без кнопки; опрос принёс черновики — строку заменил экран черновиков", async () => {
    handlerState.discovery.units = [
      discoveryUnitFixture({ last_discovery: RUN({ status: "running" }) }),
    ];
    handlerState.discovery.drafts["3"] = draftsViewFixture({
      drafts: [draftFixture()],
    });
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    renderBlock();

    expect(await screen.findByRole("status")).toHaveTextContent("открывается…");
    expect(screen.queryByRole("button", { name: "Открыть семьи…" })).not.toBeInTheDocument();
    expect(screen.queryByTestId("discovery-drafts")).not.toBeInTheDocument();

    handlerState.discovery.units = [
      discoveryUnitFixture({ last_discovery: RUN({ status: "done", open_drafts: 1, actionable: true }) }),
    ];
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_500);
    });

    expect(await screen.findByTestId("discovery-drafts")).toBeInTheDocument();
    expect(screen.queryByTestId("discovery-unit-row")).not.toBeInTheDocument();
  });

  it("живое задание перечитывается, завершённое — нет", async () => {
    handlerState.discovery.units = [
      discoveryUnitFixture({ last_discovery: RUN({ status: "pending" }) }),
    ];
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    renderBlock();
    await screen.findByRole("status");

    const first = handlerState.discovery.unitsRequests;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_500);
    });
    expect(handlerState.discovery.unitsRequests).toBeGreaterThan(first);

    handlerState.discovery.units = [discoveryUnitFixture({ last_discovery: RUN({ status: "done" }) })];
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_500);
    });
    await screen.findByRole("button", { name: "Открыть семьи…" });
    // Опрос принёс «задание кончилось»; дальше тишина.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    const settled = handlerState.discovery.unitsRequests;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(16_000);
    });
    expect(handlerState.discovery.unitsRequests).toBe(settled);
  });

  it("задержано проверкой приватности — подпись без кнопки; ошибка — подпись и кнопка остаётся", async () => {
    handlerState.discovery.units = [
      discoveryUnitFixture({ last_discovery: RUN({ status: "privacy_hold" }) }),
      discoveryUnitFixture({ unit_id: 5, unit_code: "M2", last_discovery: RUN({ status: "error" }) }),
    ];
    renderBlock();
    await screen.findAllByTestId("discovery-unit-row");

    const held = rowOf("м³");
    expect(held).toHaveTextContent("задержано проверкой приватности");
    expect(within(held).queryByRole("button")).not.toBeInTheDocument();
    const failed = rowOf("м²");
    expect(failed).toHaveTextContent("последнее открытие закончилось ошибкой");
    expect(within(failed).getByRole("button", { name: "Открыть семьи…" })).toBeInTheDocument();
  });
});

describe("DiscoveryBlock — черновики и перезапрос", () => {
  it("у единицы с открытыми черновиками строку заменяет экран черновиков, у соседней строка остаётся", async () => {
    handlerState.discovery.units = [
      discoveryUnitFixture({ last_discovery: RUN({ open_drafts: 1, actionable: true }) }),
      discoveryUnitFixture({ unit_id: 5, unit_code: "M2" }),
    ];
    handlerState.discovery.drafts["3"] = draftsViewFixture({ drafts: [draftFixture()] });
    renderBlock();

    expect(await screen.findByTestId("discovery-drafts")).toBeInTheDocument();
    expect(screen.getAllByTestId("discovery-unit-row")).toHaveLength(1);
    expect(rowOf("м²")).toBeInTheDocument();
    expect(await screen.findByText("Черновики семей · м³")).toBeInTheDocument();
  });

  it("черновики двух единиц — у каждой свои (кэш черновиков по единице)", async () => {
    // Ревью задачи 6: ключ `qk.discovery.drafts(unitId)` без единицы показывал бы обеим одно и то же.
    handlerState.discovery.units = [
      discoveryUnitFixture({ last_discovery: RUN({ open_drafts: 1, actionable: true }) }),
      discoveryUnitFixture({ unit_id: 5, unit_code: "M2", last_discovery: RUN({ job_id: 501, open_drafts: 1, actionable: true }) }),
    ];
    handlerState.discovery.drafts["3"] = draftsViewFixture({ drafts: [draftFixture()] });
    handlerState.discovery.drafts["5"] = draftsViewFixture({
      job_id: 501,
      unit_id: 5,
      drafts: [draftFixture({ id: 401, title: "Штукатурка стен" })],
    });
    renderBlock();

    await waitFor(() => expect(screen.getAllByTestId("discovery-drafts")).toHaveLength(2));
    const [m3, m2] = screen.getAllByTestId("discovery-drafts");
    expect(await within(m3).findByText("Черновики семей · м³")).toBeInTheDocument();
    expect(within(m3).getByText("Вентиляция общеобменная")).toBeInTheDocument();
    expect(within(m3).queryByText("Штукатурка стен")).not.toBeInTheDocument();
    expect(await within(m2).findByText("Черновики семей · м²")).toBeInTheDocument();
    expect(within(m2).getByText("Штукатурка стен")).toBeInTheDocument();
    expect(within(m2).queryByText("Вентиляция общеобменная")).not.toBeInTheDocument();
  });

  it("черновики единицы «без единицы» читаются без unit_id в запросе", async () => {
    // Review Focus 1: `unit_id IS NULL` — обычная единица и на экране черновиков.
    handlerState.discovery.units = [
      discoveryUnitFixture({ unit_id: null, unit_code: null, last_discovery: RUN({ open_drafts: 1, actionable: true }) }),
    ];
    handlerState.discovery.drafts["null"] = draftsViewFixture({ unit_id: null, drafts: [draftFixture()] });
    renderBlock();

    expect(await screen.findByText("Черновики семей · без единицы")).toBeInTheDocument();
    expect(await screen.findByText("Вентиляция общеобменная")).toBeInTheDocument();
    expect(handlerState.discovery.draftsRequests).toEqual(["none"]);
  });

  it("выполненное открытие, где решать нечего (actionable = false), — обычная строка с кнопкой", async () => {
    handlerState.discovery.units = [
      discoveryUnitFixture({ last_discovery: RUN({ open_drafts: 0, actionable: false }) }),
    ];
    renderBlock();

    await screen.findAllByTestId("discovery-unit-row");
    expect(screen.queryByTestId("discovery-drafts")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Открыть семьи…" })).toBeInTheDocument();
    expect(handlerState.discovery.draftsRequests).toEqual([]);
  });

  it("открытие «только категории»: новых черновиков нет, экран показан ради «Категорий активных семей»", async () => {
    handlerState.discovery.units = [
      discoveryUnitFixture({ last_discovery: RUN({ open_drafts: 0, actionable: true }) }),
    ];
    handlerState.discovery.drafts["3"] = draftsViewFixture({
      category_proposals: [
        {
          family_id: 43,
          family_title: "Кровельные работы",
          family_definition: "Устройство кровельного покрытия.",
          family_category_id: 1,
          family_category_title: "Работа",
        },
      ],
    });
    renderBlock();

    expect(await screen.findByTestId("discovery-drafts")).toBeInTheDocument();
    expect(screen.queryByTestId("draft-card")).not.toBeInTheDocument();
    expect(screen.getByTestId("category-proposals")).toBeInTheDocument();
    expect(screen.queryByTestId("discovery-unit-row")).not.toBeInTheDocument();
  });

  it("отброшен последний открытый черновик: экран остаётся ради «Вернуть»", async () => {
    const user = userEvent.setup();
    handlerState.discovery.units = [
      discoveryUnitFixture({ last_discovery: RUN({ open_drafts: 1, actionable: true }) }),
    ];
    handlerState.discovery.drafts["3"] = draftsViewFixture({ drafts: [draftFixture()] });
    renderBlock();
    await screen.findAllByTestId("draft-card");

    await user.click(screen.getByRole("button", { name: "Отбросить" }));

    await waitFor(() => expect(screen.queryByTestId("draft-card")).not.toBeInTheDocument());
    expect(screen.getByTestId("discovery-drafts")).toBeInTheDocument();
    await user.click(screen.getByText("Слитые и отброшенные (1)"));
    expect(await screen.findByRole("button", { name: "Вернуть" })).toBeInTheDocument();
  });

  it.each([
    ["error", /Последнее открытие закончилось ошибкой/],
    ["pending", /открывается…/],
  ])("новейшее задание в статусе %s не прячет черновики последнего выполненного открытия", async (status, note) => {
    handlerState.discovery.units = [
      discoveryUnitFixture({ last_discovery: RUN({ status, open_drafts: 1, actionable: true }) }),
    ];
    handlerState.discovery.drafts["3"] = draftsViewFixture({ drafts: [draftFixture()] });
    renderBlock();

    expect(await screen.findByTestId("discovery-drafts")).toBeInTheDocument();
    expect(screen.getAllByTestId("draft-card")).toHaveLength(1);
    // Статус новейшего задания строка называет отдельно, как и раньше.
    expect(screen.getByTestId("discovery-latest-job")).toHaveTextContent(note);
  });

  it("на экране черновиков «Открыть семьи заново…» открывает окно запуска этой единицы; запуск шлёт её unit_id и preview_hash", async () => {
    const user = userEvent.setup();
    handlerState.discovery.units = [
      discoveryUnitFixture({ last_discovery: RUN({ open_drafts: 1, actionable: true }) }),
    ];
    handlerState.discovery.drafts["3"] = draftsViewFixture({ drafts: [draftFixture()] });
    renderBlock();
    await screen.findAllByTestId("draft-card");

    await user.click(screen.getByRole("button", { name: "Открыть семьи заново…" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Открыть семьи · м³" })).toBeInTheDocument();
    await within(dialog).findByText("$0,90");
    expect(handlerState.discovery.previewRequests).toEqual([3]);
    await user.click(within(dialog).getByRole("button", { name: "Открыть семьи" }));

    await waitFor(() =>
      expect(handlerState.discovery.launchRequests).toEqual([
        { unit_id: 3, preview_hash: "discovery-hash-1" },
      ])
    );
  });

  it("повторный запуск с тем же входом: отказ discovery_input_unchanged показан подписью, окно остаётся", async () => {
    const user = userEvent.setup();
    handlerState.discovery.units = [
      discoveryUnitFixture({ last_discovery: RUN({ open_drafts: 1, actionable: true }) }),
    ];
    handlerState.discovery.drafts["3"] = draftsViewFixture({ drafts: [draftFixture()] });
    handlerState.discovery.refusal = {
      action: "launch",
      code: "discovery_input_unchanged",
      status: 409,
    };
    renderBlock();
    await screen.findAllByTestId("draft-card");

    await user.click(screen.getByRole("button", { name: "Открыть семьи заново…" }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("$0,90");
    await user.click(within(dialog).getByRole("button", { name: "Открыть семьи" }));

    expect(
      await screen.findByText(
        "С прошлого открытия единицы «м³» ничего не изменилось — его черновики и есть ответ. Правьте, сливайте или отбрасывайте их."
      )
    ).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("активация открывает окно перезапроса единицы reask_unit_id; подтверждение шлёт её id", async () => {
    const user = userEvent.setup();
    handlerState.discovery.units = [discoveryUnitFixture({ last_discovery: RUN({ open_drafts: 1, actionable: true }) })];
    handlerState.discovery.drafts["3"] = draftsViewFixture({ drafts: [draftFixture()] });
    renderBlock();
    await screen.findAllByTestId("draft-card");

    await user.click(
      screen.getByRole("button", { name: "Активировать отмеченные и перезапросить «м³»…" })
    );

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Перезапросить м³?" })).toBeInTheDocument();
    await within(dialog).findByText(/Ожидаемая цена при попадании в кэш/);
    expect(handlerState.previewRequests).toEqual(["unit:3"]);
    // Единица взята из ответа активации, а не из показанной строки.
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Поставить в очередь" })).toBeEnabled());
    await user.click(within(dialog).getByRole("button", { name: "Поставить в очередь" }));
    await waitFor(() =>
      expect(handlerState.reaskConfirmRequests).toEqual([
        { path: "/unit-reask", body: { unit_id: 3, preview_hash: "preview-hash-1" } },
      ])
    );
    // Окно закрыто; все черновики активированы — экран вернулся к строке единицы.
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByRole("button", { name: "Открыть семьи…" })).toBeInTheDocument();
  });

  it("окно перезапроса берёт единицу из ответа сервера", async () => {
    const user = userEvent.setup();
    handlerState.discovery.units = [discoveryUnitFixture({ last_discovery: RUN({ open_drafts: 1, actionable: true }) })];
    handlerState.discovery.drafts["3"] = draftsViewFixture({ drafts: [draftFixture()] });
    handlerState.discovery.activationOutcome = {
      created_family_ids: [1201],
      categories_applied: [],
      categories_skipped: [],
      not_work_applied: [],
      not_work_skipped: [],
      reask_unit_id: 5,
    };
    renderBlock();
    await screen.findAllByTestId("draft-card");

    await user.click(
      screen.getByRole("button", { name: "Активировать отмеченные и перезапросить «м³»…" })
    );

    await screen.findByRole("dialog");
    await waitFor(() => expect(handlerState.previewRequests).toEqual(["unit:5"]));
  });
});
