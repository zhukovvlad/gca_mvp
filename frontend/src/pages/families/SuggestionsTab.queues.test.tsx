import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { createTestQueryClient, renderWithProviders } from "@/test/utils";

import { SuggestionsTab } from "./SuggestionsTab";

/**
 * Очереди «Новая» и «Ошибки» внутри вкладки «Предложения» (спека semantic-suggestions
 * §2.12): переключатель, счётчики, фильтры, решения и их последствия для экрана.
 * Данные — фикстура `handlers.ts`: «Новая» 4 строки (две в м²), «Ошибки» 2 задания и
 * 5 задержанных (три из них несёт одна строка единицы м²).
 */

async function renderTab(onOpenFamily?: (id: number) => void) {
  const queryClient = createTestQueryClient();
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");
  renderWithProviders(<SuggestionsTab onOpenFamily={onOpenFamily} />, { queryClient });
  await screen.findAllByTestId("suggestion-group");
  return { invalidate };
}

function invalidatedKeys(invalidate: { mock: { calls: unknown[][] } }): string[] {
  return invalidate.mock.calls.map((call) => JSON.stringify((call[0] as { queryKey: unknown }).queryKey));
}

async function openQueue(user: ReturnType<typeof userEvent.setup>, name: RegExp) {
  await user.click(screen.getByRole("tab", { name }));
}

afterEach(() => {
  localStorage.clear();
});

describe("SuggestionsTab — переключатель очередей", () => {
  it("три очереди со счётчиками: список, новая, ошибки вместе с задержанными", async () => {
    await renderTab();

    await waitFor(() => expect(screen.getByRole("tab", { name: /Новая/ })).toHaveTextContent("4"));
    expect(screen.getByRole("tab", { name: /Семья из списка/ })).toHaveTextContent("6");
    await waitFor(() => expect(screen.getByRole("tab", { name: /Ошибки/ })).toHaveTextContent("7"));
  });

  it("«Новая»: строки очереди, фильтры полосы и «многовладельческие» пропадают", async () => {
    const user = userEvent.setup();
    await renderTab();

    await openQueue(user, /Новая/);

    expect(await screen.findByTestId("new-queue")).toBeInTheDocument();
    expect(screen.getAllByTestId("new-row")).toHaveLength(4);
    expect(screen.queryAllByTestId("suggestion-group")).toHaveLength(0);
    expect(screen.queryByLabelText("Уверенность")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("только многовладельческие")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Единица")).toBeInTheDocument();
  });

  it("«Новая»: единица фильтрует эту очередь, запрос идёт с queue=new и unit", async () => {
    const user = userEvent.setup();
    await renderTab();
    await openQueue(user, /Новая/);
    await screen.findByTestId("new-queue");

    await user.click(screen.getByLabelText("Единица"));
    await user.click(await screen.findByRole("option", { name: "м²" }));

    await waitFor(() => {
      const newRequests = handlerState.suggestionsRequests.filter((q) => q.includes("queue=new"));
      expect(new URLSearchParams(newRequests.at(-1)).get("unit")).toBe("5");
    });
    await waitFor(() => expect(screen.getAllByTestId("new-row")).toHaveLength(2));
  });

  it("«Ошибки»: задания и задержанные, фильтра единицы нет", async () => {
    const user = userEvent.setup();
    await renderTab();

    await openQueue(user, /Ошибки/);

    expect(await screen.findByTestId("errors-queue")).toBeInTheDocument();
    expect(screen.getAllByTestId("error-job")).toHaveLength(2);
    expect(screen.getByTestId("privacy-hold")).toBeInTheDocument();
    expect(screen.queryByLabelText("Единица")).not.toBeInTheDocument();
  });

  it("возврат в «Семья из списка» возвращает группы и фильтр полосы", async () => {
    const user = userEvent.setup();
    await renderTab();
    await openQueue(user, /Ошибки/);
    await screen.findByTestId("errors-queue");

    await openQueue(user, /Семья из списка/);

    expect(await screen.findAllByTestId("suggestion-group")).toHaveLength(3);
    expect(screen.getByLabelText("Уверенность")).toBeInTheDocument();
  });
});

describe("SuggestionsTab — решения в очередях «Новая» и «Ошибки»", () => {
  it("«Завести семью…»: строка уходит из очереди, счётчик убывает, в шапке — «список семей изменён»", async () => {
    const user = userEvent.setup();
    const { invalidate } = await renderTab();
    await openQueue(user, /Новая/);
    await screen.findByTestId("new-queue");
    expect(screen.queryByTestId("banner-stale-unit")).not.toBeInTheDocument();

    const row = screen
      .getAllByTestId("new-row")
      .find((r) => /Гидрошпонка/.test(r.textContent ?? "")) as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Завести семью…" }));
    await user.type(screen.getByLabelText(/Определение/), "Входит: шпонки. Не входит: мастики.");
    await user.click(screen.getByRole("button", { name: "Сохранить и активировать" }));

    await waitFor(() => expect(screen.getAllByTestId("new-row")).toHaveLength(3));
    expect(screen.queryByText(/Гидрошпонка ТЕХНОНИКОЛЬ/)).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Новая/ })).toHaveTextContent("3");
    expect(await screen.findByTestId("banner-stale-unit")).toHaveTextContent("список семей изменён");
    expect(invalidatedKeys(invalidate)).toEqual(
      expect.arrayContaining([
        JSON.stringify(["semantic-queue","suggestions"]),
        JSON.stringify(["semantic-queue","jobs"]),
        JSON.stringify(["semantic-queue","status"]),
        JSON.stringify(["work-families"]),
        JSON.stringify(["semantic-contexts"]),
      ])
    );
  });

  it("«Завести семью…» → «такая семья уже есть» → «Открыть семью» передаёт номер семьи наружу", async () => {
    const user = userEvent.setup();
    handlerState.createFamilyOutcome = "exists";
    const onOpenFamily = vi.fn();
    await renderTab(onOpenFamily);
    await openQueue(user, /Новая/);
    await screen.findByTestId("new-queue");

    const row = screen
      .getAllByTestId("new-row")
      .find((r) => /Гидрошпонка/.test(r.textContent ?? "")) as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Завести семью…" }));
    await user.type(screen.getByLabelText(/Определение/), "Входит: шпонки.");
    await user.click(screen.getByRole("button", { name: "Сохранить и активировать" }));

    const exists = await screen.findByTestId("family-exists");
    await user.click(within(exists).getByRole("button", { name: "Открыть семью" }));
    expect(onOpenFamily).toHaveBeenCalledWith(43);
  });

  it("«Повторить»: задание уходит из очереди ошибок, счётчик убывает", async () => {
    const user = userEvent.setup();
    await renderTab();
    await openQueue(user, /Ошибки/);
    await screen.findByTestId("errors-queue");

    await user.click(
      within(screen.getAllByTestId("error-job")[0]).getByRole("button", { name: "Повторить" })
    );

    await waitFor(() => expect(screen.getAllByTestId("error-job")).toHaveLength(1));
    expect(screen.getByRole("tab", { name: /Ошибки/ })).toHaveTextContent("6");
  });

  it("«Отправить все K»: строка единицы уходит вместе с тремя заданиями, счётчик ошибок убывает на K", async () => {
    const user = userEvent.setup();
    await renderTab();
    await openQueue(user, /Ошибки/);
    await screen.findByTestId("unit-hold-group");

    await user.click(screen.getByRole("button", { name: "Отправить все 3" }));

    await waitFor(() => expect(screen.queryByTestId("unit-hold-group")).not.toBeInTheDocument());
    expect(screen.getAllByTestId("held-job")).toHaveLength(2);
    expect(screen.getByRole("tab", { name: /Ошибки/ })).toHaveTextContent("4");
  });

  it("«Отправить» по строке: задание уходит из блока, счётчик ошибок убывает", async () => {
    const user = userEvent.setup();
    await renderTab();
    await openQueue(user, /Ошибки/);
    const row = (await screen.findAllByTestId("held-job")).find((r) =>
      /Монтаж вентиляции/.test(r.textContent ?? "")
    ) as HTMLElement;

    await user.click(within(row).getByRole("button", { name: "Отправить" }));

    await waitFor(() => expect(screen.getAllByTestId("held-job")).toHaveLength(1));
    expect(screen.getByRole("tab", { name: /Ошибки/ })).toHaveTextContent("6");
  });

  it("«Не отправлять» по строке: задание уходит из блока, счётчик ошибок убывает", async () => {
    const user = userEvent.setup();
    await renderTab();
    await openQueue(user, /Ошибки/);
    const row = (await screen.findAllByTestId("held-job")).find((r) =>
      /Монтаж вентиляции/.test(r.textContent ?? "")
    ) as HTMLElement;

    await user.click(within(row).getByRole("button", { name: "Не отправлять" }));

    await waitFor(() => expect(screen.getAllByTestId("held-job")).toHaveLength(1));
    expect(screen.getByRole("tab", { name: /Ошибки/ })).toHaveTextContent("6");
  });

  it("409 job_changed перечитывает очередь, запрос не повторяется", async () => {
    const user = userEvent.setup();
    handlerState.jobsConflict = true;
    const { invalidate } = await renderTab();
    await openQueue(user, /Ошибки/);
    const row = (await screen.findAllByTestId("held-job")).find((r) =>
      /Монтаж вентиляции/.test(r.textContent ?? "")
    ) as HTMLElement;
    invalidate.mockClear();

    await user.click(within(row).getByRole("button", { name: "Отправить" }));

    expect(await screen.findByText("Задание изменилось, обновите экран.")).toBeInTheDocument();
    expect(invalidatedKeys(invalidate)).toEqual(expect.arrayContaining([JSON.stringify(["semantic-queue","suggestions"]), JSON.stringify(["semantic-queue","jobs"]), JSON.stringify(["semantic-queue","status"])]));
    expect(handlerState.jobsConflictCalls).toBe(1);
  });

  it("«Новая» не загрузилась — сообщение об ошибке вместо очереди", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/v1/semantic/suggestions", ({ request }) =>
        new URL(request.url).searchParams.get("queue") === "new"
          ? HttpResponse.json({ detail: "boom" }, { status: 500 })
          : undefined
      )
    );
    await renderTab();
    await openQueue(user, /Новая/);

    expect(await screen.findByText("Не удалось получить очередь «Новая».")).toBeInTheDocument();
    expect(screen.queryByTestId("new-queue")).not.toBeInTheDocument();
  });

  it("задания не загрузились — сообщение об ошибке вместо очереди «Ошибки»", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/v1/semantic/jobs", () => HttpResponse.json({ detail: "boom" }, { status: 500 }))
    );
    await renderTab();
    await openQueue(user, /Ошибки/);

    expect(await screen.findByText("Не удалось получить задания очереди.")).toBeInTheDocument();
    expect(screen.queryByTestId("errors-queue")).not.toBeInTheDocument();
  });

  it("«Открыть семью» из блока задержанных передаёт номер семьи наружу", async () => {
    const user = userEvent.setup();
    const onOpenFamily = vi.fn();
    await renderTab(onOpenFamily);
    await openQueue(user, /Ошибки/);

    await user.click(await screen.findByRole("button", { name: "Открыть семью" }));

    expect(onOpenFamily).toHaveBeenCalledWith(501);
  });
});

describe("SuggestionsTab — опрос", () => {
  function countRequests() {
    const counts: Record<string, number> = {};
    const listener = ({ request }: { request: Request }) => {
      const url = new URL(request.url);
      if (!url.pathname.startsWith("/api/v1/semantic/")) return;
      const key = `${url.pathname.split("/").pop()}${url.searchParams.get("queue") ?? url.searchParams.get("status") ?? ""}`;
      counts[key] = (counts[key] ?? 0) + 1;
    };
    server.events.on("request:start", listener);
    return { counts, stop: () => server.events.removeListener("request:start", listener) };
  }

  it("через минуту перечитываются только сводка и видимая очередь; переключение перечитывает скрытую", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const probe = countRequests();
    try {
      renderWithProviders(<SuggestionsTab />, { queryClient: createTestQueryClient() });
      await vi.waitFor(() => expect(screen.getAllByTestId("suggestion-group").length).toBeGreaterThan(0));
      await vi.waitFor(() => expect(probe.counts.status).toBe(1));
      await vi.waitFor(() => expect(probe.counts.jobserror).toBe(1));
      const before = { ...probe.counts };

      await vi.advanceTimersByTimeAsync(61_000);

      await vi.waitFor(() => expect(probe.counts.status).toBe(before.status + 1));
      await vi.waitFor(() => expect(probe.counts.suggestionslist).toBe(before.suggestionslist + 1));
      expect(probe.counts.suggestionsnew).toBe(before.suggestionsnew);
      expect(probe.counts.jobserror).toBe(before.jobserror);
      expect(probe.counts.jobsprivacy_hold).toBe(before.jobsprivacy_hold);

      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      await openQueue(user, /Ошибки/);

      await vi.waitFor(() => expect(probe.counts.jobserror).toBe(before.jobserror + 1));
      await vi.waitFor(() =>
        expect(probe.counts.jobsprivacy_hold).toBe(before.jobsprivacy_hold + 1)
      );

      // Каждая скрытая очередь перечитывается своим переключением.
      await openQueue(user, /Новая/);
      await vi.waitFor(() => expect(probe.counts.suggestionsnew).toBe(before.suggestionsnew + 1));
      const listBefore = probe.counts.suggestionslist;
      await openQueue(user, /Семья из списка/);
      await vi.waitFor(() => expect(probe.counts.suggestionslist).toBe(listBefore + 1));
    } finally {
      probe.stop();
      vi.useRealTimers();
    }
  });
});
