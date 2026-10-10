import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/server";
import { handlerState } from "@/test/handlers";
import { createTestQueryClient, renderWithProviders } from "@/test/utils";
import type { SuggestionGroup } from "@/types/domain";

import { SuggestionsTab } from "./SuggestionsTab";

/**
 * Вкладка «Предложения» целиком: шапка, фильтры очереди «Семья из списка»,
 * решения и их последствия для очереди (спека semantic-suggestions §2.12).
 */

async function renderTab() {
  const queryClient = createTestQueryClient();
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");
  renderWithProviders(<SuggestionsTab />, { queryClient });
  await screen.findAllByTestId("suggestion-group");
  return { invalidate };
}

function invalidatedKeys(invalidate: { mock: { calls: unknown[][] } }): string[] {
  return invalidate.mock.calls.map((call) => JSON.stringify((call[0] as { queryKey: unknown }).queryKey));
}

function lastQuery(): URLSearchParams {
  const listRequests = handlerState.suggestionsRequests.filter((q) => q.includes("queue=list"));
  return new URLSearchParams(listRequests.at(-1));
}

function groupTitles(): string[] {
  return screen
    .getAllByTestId("suggestion-group")
    .map((g) => within(g).getByText(/^(Геотекстиль|Кровельные работы)$/).textContent ?? "");
}

function manyGroups(n: number): SuggestionGroup[] {
  return Array.from({ length: n }, (_, i) => ({
    family_id: 800 + i,
    family_title: `Семья ${String(i + 1).padStart(2, "0")}`,
    unit_code: "M2",
    band: "high" as const,
    total: 1,
    rows: [
      {
        suggestion_id: 500 + i,
        context_id: 5000 + i,
        title: `Строка ${i + 1}`,
        unit_code: "M2",
        article: null,
        path: [],
        confidence: "0.95",
        reason: "Причина",
        multi_owner: false,
        previously_rejected: null,
        semantic_kind: "WORK" as const,
      },
    ],
  }));
}

afterEach(() => {
  localStorage.clear();
});

describe("SuggestionsTab — очередь и шапка", () => {
  it("показывает шапку с расходом и группы очереди; счётчик — число строк", async () => {
    await renderTab();

    expect(await screen.findByText("$4,20")).toBeInTheDocument();
    expect(groupTitles()).toEqual(["Геотекстиль", "Геотекстиль", "Кровельные работы"]);
    expect(screen.getByRole("tab", { name: /Семья из списка/ })).toHaveTextContent("6");
  });

  it("первый запрос — очередь «list» без фильтров", async () => {
    await renderTab();

    const query = lastQuery();
    expect(query.get("queue")).toBe("list");
    expect(query.has("unit")).toBe(false);
    expect(query.has("band")).toBe(false);
    expect(query.has("multi_owner")).toBe(false);
  });

  it("плашки шапки приходят из сводки: удержанная пачка видна во вкладке", async () => {
    handlerState.queueStatus.held_batches = [
      {
        batch_id: 7,
        source: "import",
        import_job_id: 21,
        unit_id: null,
        contexts_count: 4210,
        reserve_usd: "17.6",
        expected_cached_usd: "6.05",
        created_at: "2026-09-28T10:00:00+00:00",
      },
    ];
    await renderTab();

    expect(await screen.findByTestId("banner-held")).toHaveTextContent("Удержано: Импорт сметы (job 21)");
  });

  it("очередь пуста — сообщение о пустой очереди", async () => {
    handlerState.suggestionGroups = [];
    renderWithProviders(<SuggestionsTab />);

    expect(await screen.findByText("В этой очереди при текущих фильтрах пусто.")).toBeInTheDocument();
  });

  it("ошибка загрузки очереди — сообщение, а не пустая очередь", async () => {
    server.use(http.get("/api/v1/semantic/suggestions", () => new HttpResponse(null, { status: 500 })));
    renderWithProviders(<SuggestionsTab />);

    expect(await screen.findByText("Не удалось получить очередь предложений.")).toBeInTheDocument();
    expect(screen.queryByText("В этой очереди при текущих фильтрах пусто.")).not.toBeInTheDocument();
  });
});

describe("SuggestionsTab — фильтры", () => {
  it("фильтр единицы шлёт id единицы и оставляет группы этой единицы", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("combobox", { name: "Единица" }));
    await user.click(await screen.findByRole("option", { name: "м³" }));

    await waitFor(() => expect(lastQuery().get("unit")).toBe("3"));
    await waitFor(() => expect(groupTitles()).toEqual(["Кровельные работы"]));
  });

  it("«без единицы» шлёт unit=none, а не пропускает параметр", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("combobox", { name: "Единица" }));
    await user.click(await screen.findByRole("option", { name: "без единицы" }));

    await waitFor(() => expect(lastQuery().get("unit")).toBe("none"));
    expect(await screen.findByText("В этой очереди при текущих фильтрах пусто.")).toBeInTheDocument();
  });

  it("фильтр полосы шлёт band и оставляет группы полосы", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("combobox", { name: "Уверенность" }));
    await user.click(await screen.findByRole("option", { name: "< 0,7" }));

    await waitFor(() => expect(lastQuery().get("band")).toBe("low"));
    await waitFor(() => expect(groupTitles()).toEqual(["Кровельные работы"]));
  });

  it("полоса «≥ 0,9» выбирает группу полосы high, а не соседнюю mid", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("combobox", { name: "Уверенность" }));
    await user.click(await screen.findByRole("option", { name: "≥ 0,9" }));

    await waitFor(() => expect(lastQuery().get("band")).toBe("high"));
    await waitFor(() => expect(screen.getAllByTestId("suggestion-group")).toHaveLength(1));
    expect(within(screen.getByTestId("suggestion-group")).getByText("≥ 0,9")).toBeInTheDocument();
  });

  it("«только многовладельческие» шлёт multi_owner=true; без отметки параметра нет", async () => {
    const user = userEvent.setup();
    await renderTab();
    expect(lastQuery().has("multi_owner")).toBe(false);

    await user.click(screen.getByRole("checkbox", { name: "только многовладельческие" }));

    await waitFor(() => expect(lastQuery().get("multi_owner")).toBe("true"));
    // Из фикстуры многовладельческие — строки 1 и 5: остаются по одной в двух группах.
    await waitFor(() => expect(screen.getByRole("tab", { name: /Семья из списка/ })).toHaveTextContent("2"));

    await user.click(screen.getByRole("checkbox", { name: "только многовладельческие" }));
    await waitFor(() => expect(lastQuery().has("multi_owner")).toBe(false));
  });
});

describe("SuggestionsTab — решения обновляют очередь", () => {
  it("подтверждение: группа уходит из очереди, инвалидируются очередь, сводка и контексты", async () => {
    const user = userEvent.setup();
    const { invalidate } = await renderTab();

    const first = screen.getAllByTestId("suggestion-group")[0];
    await user.click(within(first).getByRole("button", { name: "Подтвердить отмеченные 3" }));

    await waitFor(() => expect(groupTitles()).toEqual(["Геотекстиль", "Кровельные работы"]));
    expect(screen.getByRole("tab", { name: /Семья из списка/ })).toHaveTextContent("3");

    const keys = invalidate.mock.calls.map((call) => JSON.stringify((call[0] as { queryKey: unknown }).queryKey));
    expect(keys).toContain(JSON.stringify(["semantic-queue","suggestions"]));
    expect(keys).toContain(JSON.stringify(["semantic-queue","jobs"]));
    expect(keys).not.toContain(JSON.stringify(["semantic-queue","status"]));
    expect(keys).not.toContain(JSON.stringify(["semantic-queue"]));
    expect(keys).toContain(JSON.stringify(["semantic-contexts"]));
    expect(keys).toContain(JSON.stringify(["work-families"]));
  });

  it("«Другая семья…»: назначение инвалидирует очередь, контексты и семьи", async () => {
    const user = userEvent.setup();
    const base = handlerState.workFamilies.find((f) => f.id === 43)!;
    handlerState.workFamilies.push({
      ...base,
      id: 900,
      title: "Устройство покрытий",
      unit_id: 5,
      unit_code: "M2",
      unit_symbol: "м²",
    });
    const { invalidate } = await renderTab();

    const row = within(screen.getAllByTestId("suggestion-group")[0]).getAllByTestId("suggestion-row")[0];
    await user.click(within(row).getByRole("button", { name: "Другая семья…" }));
    await user.click(await screen.findByRole("combobox", { name: /Активные семьи м²/ }));
    await user.click(await screen.findByRole("option", { name: "Устройство покрытий" }));
    await user.click(screen.getByRole("button", { name: "Назначить" }));

    await waitFor(() => expect(handlerState.otherFamilyRequests).toEqual([{ suggestionId: 1, familyId: 900 }]));
    await waitFor(() => {
      const keys = invalidatedKeys(invalidate);
      expect(keys).toContain(JSON.stringify(["semantic-queue","suggestions"]));
      expect(keys).not.toContain(JSON.stringify(["semantic-queue","status"]));
      expect(keys).toContain(JSON.stringify(["semantic-contexts"]));
      expect(keys).toContain(JSON.stringify(["work-families"]));
    });
  });

  it("отказ решения (409): причина тостом, очередь перечитывается", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("/api/v1/semantic/suggestions/confirm", () =>
        HttpResponse.json(
          { detail: { code: "suggestion_changed", message: "Предложение изменилось, обновите экран." } },
          { status: 409 }
        )
      )
    );
    await renderTab();
    const requestsBefore = handlerState.suggestionsRequests.length;

    await user.click(within(screen.getAllByTestId("suggestion-group")[0]).getByRole("button", { name: "Подтвердить отмеченные 3" }));

    expect(await screen.findByText("Предложение изменилось, обновите экран.")).toBeInTheDocument();
    await waitFor(() => expect(handlerState.suggestionsRequests.length).toBeGreaterThan(requestsBefore));
  });

  it("«Снять остановку»: плашка остановки исчезает после перечитывания сводки", async () => {
    const user = userEvent.setup();
    handlerState.queueStatus.claim_paused = {
      reason: "reserve_below_actual",
      attempt_id: 5,
      paused_at: "2026-09-28T09:00:00+00:00",
    };
    await renderTab();

    await user.click(await screen.findByRole("button", { name: "Снять остановку" }));

    await waitFor(() => expect(screen.queryByTestId("banner-paused")).not.toBeInTheDocument());
    expect(handlerState.resumeWorkerCalls).toBe(1);
  });

  it("группа той же семьи с другой полосой не наследует состояние ушедшей группы", async () => {
    const user = userEvent.setup();
    await renderTab();

    // «Геотекстиль ≥ 0,9» раскрыта (первая), «Геотекстиль 0,7–0,9» свёрнута.
    const [high] = screen.getAllByTestId("suggestion-group");
    await user.click(within(high).getByRole("button", { name: "Подтвердить отмеченные 3" }));

    await waitFor(() => expect(screen.getAllByTestId("suggestion-group")).toHaveLength(2));
    const first = screen.getAllByTestId("suggestion-group")[0];
    expect(within(first).getByText("0,7–0,9")).toBeInTheDocument();
    // Своё состояние у каждой пары «семья + полоса»: раскрытость ушедшей группы не переходит.
    expect(within(first).queryAllByTestId("suggestion-row")).toHaveLength(0);
    expect(within(first).getByRole("button", { name: "Раскрыть группу" })).toHaveAttribute("aria-expanded", "false");
  });

  it("отклонение строки: строка уходит, число в кнопке группы уменьшается; контексты не инвалидируются", async () => {
    const user = userEvent.setup();
    const { invalidate } = await renderTab();

    const first = screen.getAllByTestId("suggestion-group")[0];
    await user.click(within(within(first).getAllByTestId("suggestion-row")[0]).getByRole("button", { name: "Отклонить" }));

    await waitFor(() =>
      expect(within(screen.getAllByTestId("suggestion-group")[0]).getByRole("button", { name: "Подтвердить отмеченные 2" })).toBeInTheDocument()
    );
    const keys = invalidate.mock.calls.map((call) => JSON.stringify((call[0] as { queryKey: unknown }).queryKey));
    expect(keys).toContain(JSON.stringify(["semantic-queue","suggestions"]));
    expect(keys).toContain(JSON.stringify(["semantic-queue","jobs"]));
    expect(keys).not.toContain(JSON.stringify(["semantic-queue","status"]));
    expect(keys).not.toContain(JSON.stringify(["semantic-contexts"]));
  });

  it("«Отбросить» пачку: плашка исчезает после перечитывания сводки", async () => {
    const user = userEvent.setup();
    handlerState.queueStatus.held_batches = [
      {
        batch_id: 7,
        source: "mass",
        import_job_id: null,
        unit_id: null,
        contexts_count: 10,
        reserve_usd: "1",
        expected_cached_usd: "0.5",
        created_at: "2026-09-28T10:00:00+00:00",
      },
    ];
    await renderTab();

    await user.click(await screen.findByRole("button", { name: "Отбросить" }));

    await waitFor(() => expect(screen.queryByTestId("banner-held")).not.toBeInTheDocument());
    expect(handlerState.discardBatchRequests).toEqual([7]);
  });

  it("«Перезапросить всё…» открывает общий диалог; подтверждение шлёт hash показанного preview", async () => {
    const user = userEvent.setup();
    handlerState.queueStatus.config_stale = { stale_count: 4380, prompt_version_current: 4 };
    const { invalidate } = await renderTab();

    await user.click(await screen.findByRole("button", { name: "Перезапросить всё…" }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("214");
    expect(invalidatedKeys(invalidate)).not.toContain(JSON.stringify(["semantic-queue","status"]));
    await user.click(within(dialog).getByRole("button", { name: "Поставить в очередь" }));
    // Постановка заданий меняет сводку шапки (расход, плашки): очередь и сводка перечитываются.
    await waitFor(() => expect(invalidatedKeys(invalidate)).toContain(JSON.stringify(["semantic-queue","status"])));
    expect(invalidatedKeys(invalidate)).toEqual(expect.arrayContaining([JSON.stringify(["semantic-queue","suggestions"]), JSON.stringify(["semantic-queue","jobs"])]));

    await waitFor(() => expect(handlerState.reaskConfirmRequests).toHaveLength(1));
    expect(handlerState.reaskConfirmRequests[0]).toEqual({
      path: "/reask-all",
      body: { preview_hash: "preview-hash-1" },
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});

describe("SuggestionsTab — страницы групп", () => {
  it("10 групп на странице, остальные — на второй", async () => {
    const user = userEvent.setup();
    handlerState.suggestionGroups = manyGroups(12);
    await renderTab();

    expect(screen.getAllByTestId("suggestion-group")).toHaveLength(10);
    await user.click(screen.getByRole("button", { name: "Следующая страница" }));

    await waitFor(() => expect(screen.getAllByTestId("suggestion-group")).toHaveLength(2));
    expect(screen.getByText("Семья 11")).toBeInTheDocument();
    expect(screen.queryByText("Семья 01")).not.toBeInTheDocument();
  });

  it("смена фильтра возвращает на первую страницу, даже если вторая по-прежнему есть", async () => {
    const user = userEvent.setup();
    handlerState.suggestionGroups = manyGroups(12);
    await renderTab();

    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await waitFor(() => expect(screen.getAllByTestId("suggestion-group")).toHaveLength(2));
    // Все двенадцать групп — полосы high: после фильтра страниц по-прежнему две.
    await user.click(screen.getByRole("combobox", { name: "Уверенность" }));
    await user.click(await screen.findByRole("option", { name: "≥ 0,9" }));

    await waitFor(() => expect(lastQuery().get("band")).toBe("high"));
    await waitFor(() => expect(screen.getAllByTestId("suggestion-group")).toHaveLength(10));
    expect(screen.getByText("Семья 01")).toBeInTheDocument();
  });

  it("решение опустошило последнюю страницу — экран возвращается на предыдущую", async () => {
    const user = userEvent.setup();
    handlerState.suggestionGroups = manyGroups(11);
    await renderTab();

    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await waitFor(() => expect(screen.getAllByTestId("suggestion-group")).toHaveLength(1));
    await user.click(screen.getByRole("button", { name: "Подтвердить отмеченные 1" }));

    await waitFor(() => expect(screen.getAllByTestId("suggestion-group")).toHaveLength(10));
  });
});

describe("SuggestionsTab — счётчики вкладок", () => {
  const TABS = [/Семья из списка/, /Смена семьи/, /Новая/, /Ошибки/];

  function counterOf(name: RegExp): string {
    return screen.getByRole("tab", { name }).querySelector("span")?.textContent ?? "";
  }

  it("пока очередь грузится, счётчик — заполнитель, а не 0; после ответа — число", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get("/api/v1/semantic/suggestions", async ({ request }) => {
        await gate;
        const queue = new URL(request.url).searchParams.get("queue");
        if (queue === "new") return HttpResponse.json({ queue, groups: [], items: [{}, {}] });
        if (queue === "change") return HttpResponse.json({ queue, groups: manyGroups(3), items: [] });
        return HttpResponse.json({ queue: "list", groups: manyGroups(5), items: [] });
      }),
      http.get("/api/v1/semantic/jobs", async ({ request }) => {
        await gate;
        const status = new URL(request.url).searchParams.get("status");
        return HttpResponse.json({ items: status === "error" ? [{}] : [] });
      })
    );
    renderWithProviders(<SuggestionsTab />);

    for (const name of TABS) {
      expect(await screen.findByRole("tab", { name })).toBeInTheDocument();
      expect(counterOf(name)).toBe("…");
      expect(screen.getByRole("tab", { name })).not.toHaveTextContent(/\b0\b/);
    }

    release();
    await waitFor(() => expect(counterOf(TABS[0])).toBe("5"), { timeout: 8000 });
    await waitFor(() => expect(counterOf(TABS[1])).toBe("3"), { timeout: 8000 });
    await waitFor(() => expect(counterOf(TABS[2])).toBe("2"), { timeout: 8000 });
    await waitFor(() => expect(counterOf(TABS[3])).toBe("1"), { timeout: 8000 });
  });

  it("загруженная пустая очередь показывает 0", async () => {
    server.use(
      http.get("/api/v1/semantic/suggestions", ({ request }) => {
        const queue = new URL(request.url).searchParams.get("queue") ?? "list";
        return HttpResponse.json({ queue, groups: [], items: [] });
      }),
      http.get("/api/v1/semantic/jobs", () => HttpResponse.json({ items: [] }))
    );
    renderWithProviders(<SuggestionsTab />);

    for (const name of TABS) {
      await waitFor(() => expect(counterOf(name)).toBe("0"), { timeout: 8000 });
    }
  });
});
