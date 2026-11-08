import { QueryClient } from "@tanstack/react-query";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { PROPOSAL_STALE_TOAST } from "@/services/queries";
import { ladderQueues } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";

import { ReleasableQueue } from "./ReleasableQueue";

/**
 * Очередь «Можно отпустить» (лестница аналогов §2.6, §2.7, §2.20): плоский ответ сервера
 * экран раскладывает по семьям; решение уходит с `request_hash` предложения. Фикстура:
 * семья 1 «Гидроизоляция фундаментов» — «Толщина» (можно, 0,71) и «Тип материала» (обязателен,
 * 0,88), семья 2 «Устройство жб конструкций» — «Тип конструкции» (обязателен, 0,93).
 */

const LATER = { timeout: 8000 };

function paramRow(name: string) {
  const row = screen.getAllByTestId("releasable-row").find((r) => within(r).queryByText(name) !== null);
  if (!row) throw new Error(`нет параметра ${name}`);
  return row;
}

describe("ReleasableQueue", () => {
  it("параметры сгруппированы по семьям: заголовок семьи один на группу", async () => {
    renderWithProviders(<ReleasableQueue />);

    await screen.findAllByTestId("releasable-row");
    const groups = screen.getAllByTestId("releasable-family");
    expect(groups).toHaveLength(2);
    expect(within(groups[0]).getByText("Гидроизоляция фундаментов")).toBeInTheDocument();
    expect(within(groups[0]).getAllByTestId("releasable-row")).toHaveLength(2);
    expect(within(groups[1]).getAllByTestId("releasable-row")).toHaveLength(1);
  });

  it("параметр: значения, ответ модели с уверенностью и причиной", async () => {
    renderWithProviders(<ReleasableQueue />);
    await screen.findAllByTestId("releasable-row");

    const row = paramRow("Толщина");
    expect(within(row).getByText("2 мм")).toBeInTheDocument();
    expect(within(row).getByText("3 мм")).toBeInTheDocument();
    expect(within(row).getByText(/можно отпустить/)).toBeInTheDocument();
    expect(within(row).getByText(/0,71/)).toBeInTheDocument();
    expect(within(row).getByText(/цена за единицу близка/)).toBeInTheDocument();
    expect(within(paramRow("Тип материала")).getByText(/Модель: обязателен/)).toBeInTheDocument();
  });

  it("«Можно отпустить»: PUT с releasable true и request_hash предложения, строка уходит", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ReleasableQueue />);
    await screen.findAllByTestId("releasable-row");

    await user.click(within(paramRow("Толщина")).getByRole("button", { name: "Можно отпустить" }));

    await waitFor(() => expect(ladderQueues.releasableDecisions).toHaveLength(1), LATER);
    expect(ladderQueues.releasableDecisions[0]).toEqual({
      familyId: 1,
      parameterId: 11,
      body: { releasable: true, request_hash: "hash-11" },
    });
    await waitFor(() => expect(screen.getAllByTestId("releasable-row")).toHaveLength(2), LATER);
  });

  it("«Обязателен»: PUT с releasable false", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ReleasableQueue />);
    await screen.findAllByTestId("releasable-row");

    await user.click(within(paramRow("Тип конструкции")).getByRole("button", { name: "Обязателен" }));

    await waitFor(() => expect(ladderQueues.releasableDecisions).toHaveLength(1), LATER);
    expect(ladderQueues.releasableDecisions[0].body).toEqual({ releasable: false, request_hash: "hash-21" });
    expect(ladderQueues.releasableDecisions[0].parameterId).toBe(21);
  });

  it("409 proposal_stale: тост и перезапрос очереди — недействующая строка исчезает", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ReleasableQueue />);
    await screen.findAllByTestId("releasable-row");
    // Семью правили после вопроса: сервер ждёт другой отпечаток.
    ladderQueues.currentHash[11] = "hash-changed";

    await user.click(within(paramRow("Толщина")).getByRole("button", { name: "Можно отпустить" }));

    expect(await screen.findByText(PROPOSAL_STALE_TOAST)).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByTestId("releasable-row")).toHaveLength(2), LATER);
    expect(screen.queryByText("Толщина")).not.toBeInTheDocument();
  });

  it("решение признака перечитывает очереди, сводку, схемы семей и весь разбор «Против нашей цены»", async () => {
    const user = userEvent.setup();
    // gcTime бесконечен: без наблюдателей записи кэша иначе тут же собираются.
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity, staleTime: 0 }, mutations: { retry: false } },
    });
    const keys = {
      round: ["tenders", "our-price", "round", 1, 1, null],
      status: ["semantic-queue", "status"],
      schema: ["work-families", "schema", 1],
      zones: ["semantic-queue", "zones", { filter: "disputed", q: "", page: 1 }],
    } as const;
    for (const key of Object.values(keys)) qc.setQueryData(key, { stub: true });
    renderWithProviders(<ReleasableQueue />, { queryClient: qc });
    await screen.findAllByTestId("releasable-row");

    await user.click(within(paramRow("Толщина")).getByRole("button", { name: "Можно отпустить" }));

    await waitFor(() => {
      for (const key of Object.values(keys)) {
        expect(qc.getQueryState(key)?.isInvalidated, key.join("/")).toBe(true);
      }
    }, LATER);
  });

  it("пустая очередь — «Предложений нет»", async () => {
    ladderQueues.proposals = [];
    renderWithProviders(<ReleasableQueue />);

    expect(await screen.findByText("Предложений нет")).toBeInTheDocument();
  });
});
