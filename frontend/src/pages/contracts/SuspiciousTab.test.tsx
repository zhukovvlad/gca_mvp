import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { qk } from "@/services/queryKeys";
import {
  neighborRowFixture,
  notErrorDecisionOutlier,
  resolvedFixture,
  suspectOutlierFixture,
  suspectTwinFixture,
  suspiciousListFixture,
} from "@/test/fixtures";
import { server } from "@/test/server";
import { createTestQueryClient, renderWithProviders, waitForDialogFocus } from "@/test/utils";
import type { ResolvedView, SuspiciousLinesResponse } from "@/types/domain";

import ContractsPage from "./ContractsPage";

const LIST = "/api/v1/suspicious-lines";
const NOT_ERROR = "/api/v1/position-items/:id/not-error";
const PRICE_ERROR = "/api/v1/position-items/:id/price-error";

interface Calls {
  lists: string[];
  counts: number;
  puts: Array<{ url: string; body: unknown }>;
  deletes: string[];
}

/**
 * Сервер списка с памятью: мутации меняют ответ так, как изменил бы его настоящий сервер (пункт уходит
 * из «Не разобраны» в «Разобраны» и обратно) — иначе тесты не заметили бы, что экран не перечитывает.
 */
function installServer(options: { putNotErrorStatus?: 409; putDelayMs?: number } = {}) {
  const state: { list: SuspiciousLinesResponse } = { list: structuredClone(suspiciousListFixture) };
  const calls: Calls = { lists: [], counts: 0, puts: [], deletes: [] };
  server.use(
    http.get(`${LIST}/counts`, () => {
      calls.counts += 1;
      return HttpResponse.json({ total: state.list.unresolved.length, by_contract: {} });
    }),
    http.get(LIST, ({ request }) => {
      calls.lists.push(new URL(request.url).search);
      return HttpResponse.json(state.list);
    }),
    http.put(NOT_ERROR, async ({ request, params }) => {
      const body = (await request.json()) as { reason: string; bases: unknown[] };
      calls.puts.push({ url: new URL(request.url).pathname, body });
      if (options.putDelayMs) await delay(options.putDelayMs);
      if (options.putNotErrorStatus === 409) {
        return HttpResponse.json(
          { detail: { code: "suspicion_changed", message: "Подозрение по строке изменилось — проверьте заново." } },
          { status: 409 }
        );
      }
      const id = Number(params.id);
      const item = state.list.unresolved.find((i) => i.position_item_id === id);
      state.list.unresolved = state.list.unresolved.filter((i) => i.position_item_id !== id);
      if (item) {
        state.list.resolved.unshift(
          resolvedFixture({
            position_item_id: id,
            title: item.title,
            price_error: null,
            not_error: [{ ...notErrorDecisionOutlier, reason: body.reason }],
            facts: item.facts,
          })
        );
      }
      return HttpResponse.json([{ ...notErrorDecisionOutlier, reason: body.reason }]);
    }),
    http.delete(NOT_ERROR, ({ request, params }) => {
      calls.deletes.push(new URL(request.url).pathname);
      state.list.resolved = state.list.resolved.filter((i) => i.position_item_id !== Number(params.id));
      return new HttpResponse(null, { status: 204 });
    }),
    http.put(PRICE_ERROR, async ({ request, params }) => {
      const body = (await request.json()) as { reason: string };
      calls.puts.push({ url: new URL(request.url).pathname, body });
      const id = Number(params.id);
      const item = state.list.unresolved.find((i) => i.position_item_id === id);
      state.list.unresolved = state.list.unresolved.filter((i) => i.position_item_id !== id);
      if (item) {
        state.list.resolved.unshift(resolvedFixture({ position_item_id: id, title: item.title }));
      }
      return new HttpResponse(null, { status: 204 });
    }),
    http.delete(PRICE_ERROR, ({ request, params }) => {
      calls.deletes.push(new URL(request.url).pathname);
      state.list.resolved = state.list.resolved.filter((i) => i.position_item_id !== Number(params.id));
      return new HttpResponse(null, { status: 204 });
    })
  );
  return { state, calls };
}

function renderTab(route = "/contracts?tab=suspicious", options: Parameters<typeof renderWithProviders>[1] = {}) {
  return renderWithProviders(<ContractsPage />, { initialRoute: route, ...options });
}

async function openBlock(name: string, scope: HTMLElement) {
  await userEvent.click(within(scope).getByRole("button", { name }));
}

describe("вкладки «Договоры | Подозрительные строки» (спека, раздел 5 п. 1)", () => {
  it("admin: вкладка несёт N из счётчиков; переключение показывает список", async () => {
    installServer();
    server.use(http.get(`${LIST}/counts`, () => HttpResponse.json({ total: 4, by_contract: { "14": 4 } })));
    renderTab("/contracts");

    const tab = await screen.findByRole("tab", { name: "Подозрительные строки (4)" });
    expect(screen.getByRole("tab", { name: "Договоры" })).toBeInTheDocument();
    expect(await screen.findByText("ГП-2026-001")).toBeInTheDocument();

    await userEvent.click(tab);
    expect(await screen.findByTestId("suspect-5001")).toBeInTheDocument();
    expect(screen.queryByText("ГП-2026-001")).not.toBeInTheDocument();
  });

  it("member: вкладок нет, адрес tab=suspicious открывает «Договоры», запросов списка и счётчиков нет", async () => {
    const asked: string[] = [];
    server.use(
      http.get(`${LIST}/counts`, ({ request }) => {
        asked.push(request.url);
        return HttpResponse.json({ total: 0, by_contract: {} });
      }),
      http.get(LIST, ({ request }) => {
        asked.push(request.url);
        return HttpResponse.json({ unresolved: [], resolved: [], contracts: [] });
      })
    );
    renderTab("/contracts?tab=suspicious", { initialUser: { id: 2, email: "member@example.com", role: "member" } });

    expect(await screen.findByText("ГП-2026-001")).toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    expect(screen.queryByTestId("suspicious-summary")).not.toBeInTheDocument();
    // дать возможному запросу уйти: счётчика у member нет вовсе
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(asked).toEqual([]);
  });

  it("admin с адресом tab=suspicious открывает список сразу", async () => {
    installServer();
    renderTab();
    expect(await screen.findByTestId("suspect-5001")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Подозрительные строки/, selected: true })).toBeInTheDocument();
  });
});

describe("пункты «Не разобраны»", () => {
  it("выброс: метка, множитель, полоса цен и цена «₽ за ед. без НДС»; блоки свёрнуты", async () => {
    installServer();
    renderTab();
    const item = await screen.findByTestId("suspect-5001");

    expect(within(item).getByTestId("kind-outlier")).toHaveTextContent("↕ цена выбивается");
    expect(within(item).getByTestId("factor")).toHaveTextContent("×14,5");
    expect(within(item).getByTestId("price-strip")).toBeInTheDocument();
    expect(within(item).getByTestId("line-price")).toHaveTextContent(/239\s133,50/);
    expect(item).toHaveTextContent("₽ за шт без НДС");
    expect(within(item).getByTestId("fact-sentence").textContent).not.toMatch(/раза дороже/);
    expect(within(item).getByTestId("fact-sentence")).toHaveTextContent(
      /^Обычная цена «Велопарковки» — 16\s500,00\s₽ за шт: середина цен 7 договоров/
    );
    expect(within(item).queryByRole("table")).not.toBeInTheDocument();
  });

  it("двойник: метка, карточки через «=» с выделенными единицами, полосы цен нет; блок цен только при группе", async () => {
    installServer();
    renderTab();
    const item = await screen.findByTestId("suspect-6001");

    expect(within(item).getByTestId("kind-twin")).toHaveTextContent("= та же цена, другая единица");
    const pair = within(item).getByTestId("twin-pair");
    const units = within(pair).getAllByTestId("twin-unit").map((node) => node.textContent);
    expect(units).toEqual(["м²", "м"]);
    expect(within(item).queryByTestId("price-strip")).not.toBeInTheDocument();
    expect(within(item).queryByTestId("block-prices-6001")).not.toBeInTheDocument();
    expect(within(item).getByTestId("block-neighbors-6001")).toBeInTheDocument();
  });

  it("два Collapsible раскрываются щелчком: соседи с выделенной строкой и цены договоров с «своим»", async () => {
    installServer();
    renderTab();
    const item = await screen.findByTestId("suspect-5001");

    await openBlock("Строки рядом в смете", item);
    const neighbors = within(item).getByTestId("block-neighbors-5001");
    const selfRow = within(neighbors).getByTestId("neighbor-5001");
    expect(selfRow).toHaveAttribute("data-self", "true");
    expect(within(neighbors).getByTestId("neighbor-9001")).not.toHaveAttribute("data-self");
    expect(neighbors).toHaveTextContent("По 3 строки сметы до и после");

    await openBlock("Цены этой работы в договорах", item);
    const prices = within(item).getByTestId("block-prices-5001");
    expect(within(prices).getByTestId("group-contract-14")).toHaveAttribute("data-own", "true");
    expect(within(prices).getByTestId("group-contract-12")).not.toHaveAttribute("data-own");
    expect(within(prices).getByTestId("group-note")).toHaveTextContent(/обычная цена 16\s500,00\s₽ — середина цен договоров/);
  });

  it("узкий экран: таблицы блоков не уже 760 px и прокручиваются внутри блока, деньги не переносятся", async () => {
    installServer();
    renderTab("/contracts?tab=suspicious&line=5001");
    const item = await screen.findByTestId("suspect-5001");

    const tables = within(item).getAllByRole("table");
    expect(tables).toHaveLength(2);
    for (const table of tables) {
      expect(table).toHaveClass("min-w-[760px]");
      expect(table.closest("[data-slot='table-container']")).toHaveClass("overflow-x-auto");
    }
    const money = within(item).getByTestId("neighbor-5001").querySelectorAll("td.font-mono");
    expect(money.length).toBeGreaterThanOrEqual(5);
    for (const cell of money) expect(cell).toHaveClass("whitespace-nowrap");
    expect(within(item).getByTestId("line-price").parentElement).toHaveClass("whitespace-nowrap");
  });

  it("решения на другие основания показаны в «previous», с основанием, причиной, автором и датой", async () => {
    installServer();
    server.use(
      http.get(LIST, () =>
        HttpResponse.json({
          ...suspiciousListFixture,
          unresolved: [suspectOutlierFixture({ previous: [notErrorDecisionOutlier] })],
        })
      )
    );
    renderTab();
    const previous = await screen.findByTestId("previous");
    expect(previous).toHaveTextContent("Решения на другие основания");
    expect(previous).toHaveTextContent("выброс в «Двери»");
    expect(previous).toHaveTextContent("автоматические раздвижные двери — другое изделие, цена верная");
    expect(previous).toHaveTextContent("admin@example.test");
  });
});

describe("адрес: tab, contract, line", () => {
  it("contract уходит в запрос списка и выбран в фильтре", async () => {
    const { calls } = installServer();
    renderTab("/contracts?tab=suspicious&contract=14");
    await screen.findByTestId("suspect-5001");

    expect(calls.lists).toEqual(["?contract_id=14"]);
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Договор" })).toHaveTextContent("ВЕР1-ГП"));
  });

  it("line: пункт прокручен в вид и оба блока раскрыты; прочие свёрнуты", async () => {
    installServer();
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    renderTab("/contracts?tab=suspicious&line=5001");
    const item = await screen.findByTestId("suspect-5001");

    expect(item).toHaveAttribute("data-focused", "true");
    expect(within(item).getAllByRole("table")).toHaveLength(2);
    expect(scroll.mock.contexts.some((node) => node === item)).toBe(true);
    expect(within(screen.getByTestId("suspect-6001")).queryByRole("table")).not.toBeInTheDocument();
    scroll.mockRestore();
  });

  it("line из «Разобраны»: блок раскрыт и пункт прокручен в вид", async () => {
    installServer();
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    renderTab("/contracts?tab=suspicious&line=7002");
    const item = await screen.findByTestId("resolved-7002");

    expect(scroll.mock.contexts.some((node) => node === item)).toBe(true);
    scroll.mockRestore();
  });

  it("строка с выбросом и двойниками считается в каждом из счётчиков по одному разу", async () => {
    const outlier = suspectOutlierFixture();
    const twin = suspectTwinFixture();
    const both = suspectOutlierFixture({ facts: [...outlier.facts, ...twin.facts, ...twin.facts] });
    server.use(
      http.get(LIST, () => HttpResponse.json({ ...structuredClone(suspiciousListFixture), unresolved: [both] })),
      http.get(`${LIST}/counts`, () => HttpResponse.json({ total: 1, by_contract: {} }))
    );
    renderTab("/contracts?tab=suspicious");
    await screen.findByTestId("suspect-5001");

    expect(screen.getByTestId("sum-unresolved")).toHaveTextContent("1");
    expect(screen.getByTestId("sum-outlier")).toHaveTextContent("1");
    expect(screen.getByTestId("sum-twin")).toHaveTextContent("1");
  });

  it("выбор договора в фильтре перечитывает список с contract_id", async () => {
    const { calls } = installServer();
    renderTab();
    await screen.findByTestId("suspect-5001");

    await userEvent.click(screen.getByRole("combobox", { name: "Договор" }));
    await userEvent.click(await screen.findByRole("option", { name: /UB2-CIW/ }));

    await waitFor(() => expect(calls.lists).toContain("?contract_id=15"));
  });
});

describe("«Разобраны»", () => {
  it("свёрнут; раскрывается; у «не ошибки» — основание, причина, автор, дата и «Вернуть»; у «ошибки в цене» — «Снять во всех тендерах…»", async () => {
    installServer();
    renderTab();
    const block = await screen.findByTestId("resolved-block");
    expect(within(block).queryByTestId("resolved-7001")).not.toBeInTheDocument();

    await userEvent.click(within(block).getByRole("button", { name: "Разобраны (2)" }));

    const priceError = within(await screen.findByTestId("resolved-7001")).getByTestId("resolved-price-error");
    expect(priceError).toHaveTextContent("ошибка в цене · во всех тендерах");
    expect(priceError).toHaveTextContent("4 438 ₽ за тонну — строка-сумма с единицей «т»");
    expect(priceError).toHaveTextContent("admin@example.test");
    expect(within(priceError).getByRole("button", { name: "Снять во всех тендерах…" })).toBeInTheDocument();

    const notError = within(screen.getByTestId("resolved-7002")).getByTestId("resolved-not-error");
    expect(notError).toHaveTextContent("не ошибка");
    expect(notError).toHaveTextContent("Проверено: выброс в «Двери»");
    expect(notError).toHaveTextContent("автоматические раздвижные двери — другое изделие, цена верная");
    expect(notError).toHaveTextContent("admin@example.test");
    expect(notError).toHaveTextContent("24.11.2026");
    expect(within(notError).getByRole("button", { name: "Вернуть" })).toBeInTheDocument();
    expect(screen.getByTestId("resolved-7002")).toHaveTextContent(/Правило сейчас: в 19,6 раза дороже обычного/);
  });

  it("пустые facts — «правило сейчас её не ловит»", async () => {
    installServer();
    renderTab("/contracts?tab=suspicious&line=7001");
    const item = await screen.findByTestId("resolved-7001");
    expect(within(item).getByTestId("rule-now")).toHaveTextContent("правило сейчас её не ловит");
  });

  it("price_note: четыре разные подписи; latest=false — «строка прежней сметы»", async () => {
    const variants: Array<[string, Partial<ResolvedView>, string]> = [
      ["no_price", { net: null, price_note: "no_price" }, "цены нет"],
      ["not_finite", { net: null, price_note: "not_finite", raw: "NaN" }, "в смете: NaN — не число"],
      ["negative", { net: null, price_note: "negative", raw: "-100" }, "в смете: -100"],
      ["vat_unknown", { net: null, price_note: "vat_unknown", gross: "1200.00" }, "НДС сметы не известен — цена как в смете"],
    ];
    installServer();
    server.use(
      http.get(LIST, () =>
        HttpResponse.json({
          unresolved: [],
          contracts: [],
          resolved: variants.map(([, overrides], index) =>
            resolvedFixture({ position_item_id: 8000 + index, ...overrides, ...(index === 0 ? { latest: false } : {}) })
          ),
        })
      )
    );
    renderTab("/contracts?tab=suspicious&line=8000");
    const texts: string[] = [];
    for (const [index, [, , caption]] of variants.entries()) {
      const note = within(await screen.findByTestId(`resolved-${8000 + index}`)).getByTestId("line-price-note");
      expect(note).toHaveTextContent(caption);
      texts.push(note.textContent ?? "");
    }
    expect(new Set(texts).size).toBe(4);
    expect(screen.getByTestId("resolved-8000")).toHaveTextContent("строка прежней сметы");
    expect(screen.getByTestId("resolved-8001")).not.toHaveTextContent("строка прежней сметы");
    expect(screen.getByTestId("resolved-8003")).toHaveTextContent(/1\s200,00/);
  });
});

describe("окно «Не ошибка»", () => {
  async function openNotError(itemId = 5001) {
    const item = await screen.findByTestId(`suspect-${itemId}`);
    await userEvent.click(within(item).getByTestId("act-not-error"));
    const dialog = await screen.findByRole("alertdialog");
    await waitForDialogFocus("alertdialog");
    return dialog;
  }

  it("текст основания, «Сохранить» вместо «Исключить», отправляет reason и basis показанных фактов", async () => {
    const { calls } = installServer();
    renderTab();
    const dialog = await openNotError();

    expect(within(dialog).getByRole("heading", { name: "Не ошибка" })).toBeInTheDocument();
    expect(within(dialog).getByTestId("mark-scope")).toHaveTextContent(
      "выброс в «Велопарковки», ×14,5 дороже обычного"
    );
    expect(within(dialog).queryByRole("button", { name: "Исключить" })).not.toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Причина"), "дизайнерская велопарковка");
    await userEvent.click(within(dialog).getByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(calls.puts).toHaveLength(1));
    expect(calls.puts[0]).toEqual({
      url: "/api/v1/position-items/5001/not-error",
      body: {
        reason: "дизайнерская велопарковка",
        bases: [{ kind: "outlier", family_id: 71, unit_id: 5, direction: "up" }],
      },
    });
  });

  it("«Сохранить» занято до ответа; после успеха список, счётчики и «ourPriceAll» перечитаны, пункт ушёл в «Разобраны»", async () => {
    const { calls } = installServer({ putDelayMs: 150 });
    const qc = createTestQueryClient();
    const invalidate = vi.spyOn(qc, "invalidateQueries");
    renderTab("/contracts?tab=suspicious", { queryClient: qc });
    const dialog = await openNotError();
    await userEvent.type(within(dialog).getByLabelText("Причина"), "верная цена");
    const listsBefore = calls.lists.length;
    const countsBefore = calls.counts;
    const save = within(dialog).getByRole("button", { name: "Сохранить" });
    await userEvent.click(save);

    await waitFor(() => expect(save).toBeDisabled());
    await waitFor(() => expect(screen.queryByTestId("suspect-5001")).not.toBeInTheDocument());
    expect(calls.lists.length).toBeGreaterThan(listsBefore);
    await waitFor(() => expect(calls.counts).toBeGreaterThan(countsBefore));
    const keys = invalidate.mock.calls.map(([filters]) => (filters as { queryKey: unknown }).queryKey);
    expect(keys).toContainEqual(qk.suspicious.all);
    expect(keys).toContainEqual(qk.tenders.ourPriceAll);
    expect(keys).toContainEqual(qk.tenders.kpFilePreviewsAll);
    expect(screen.getByTestId("sum-resolved")).toHaveTextContent("3");
  });

  it("409 suspicion_changed: окно закрыто, список перечитан, сверху сообщение; тоста ошибки нет", async () => {
    const { calls } = installServer({ putNotErrorStatus: 409 });
    renderTab();
    const dialog = await openNotError();
    await userEvent.type(within(dialog).getByLabelText("Причина"), "верная цена");
    const listsBefore = calls.lists.length;
    await userEvent.click(within(dialog).getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByTestId("suspicion-changed")).toHaveTextContent(
      "Подозрение по строке изменилось — проверьте заново"
    );
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    await waitFor(() => expect(calls.lists.length).toBeGreaterThan(listsBefore));
    expect(screen.queryByText("Подозрение по строке изменилось — проверьте заново.")).not.toBeInTheDocument();
  });

  it("«Вернуть» шлёт DELETE /not-error и перечитывает список", async () => {
    const { calls } = installServer();
    renderTab("/contracts?tab=suspicious&line=7002");
    const item = await screen.findByTestId("resolved-7002");
    const listsBefore = calls.lists.length;
    await userEvent.click(within(item).getByRole("button", { name: "Вернуть" }));

    await waitFor(() => expect(calls.deletes).toEqual(["/api/v1/position-items/7002/not-error"]));
    await waitFor(() => expect(calls.lists.length).toBeGreaterThan(listsBefore));
    await waitFor(() => expect(screen.queryByTestId("resolved-7002")).not.toBeInTheDocument());
  });
});

describe("«Ошибка в цене…» и «Снять во всех тендерах…» из списка", () => {
  it("«Ошибка в цене…» — PUT /price-error без контекста КП, перечитывает список, счётчики и «ourPriceAll»", async () => {
    const { calls } = installServer();
    const qc = createTestQueryClient();
    const invalidate = vi.spyOn(qc, "invalidateQueries");
    renderTab("/contracts?tab=suspicious", { queryClient: qc });
    const item = await screen.findByTestId("suspect-5001");
    await userEvent.click(within(item).getByTestId("act-price-error"));
    const dialog = await screen.findByRole("alertdialog");
    await waitForDialogFocus("alertdialog");
    await userEvent.type(within(dialog).getByLabelText("Причина"), "цена за комплект");
    await userEvent.click(within(dialog).getByRole("button", { name: "Исключить" }));

    await waitFor(() => expect(calls.puts).toHaveLength(1));
    expect(calls.puts[0]).toEqual({
      url: "/api/v1/position-items/5001/price-error",
      body: { reason: "цена за комплект" },
    });
    await waitFor(() => expect(screen.queryByTestId("suspect-5001")).not.toBeInTheDocument());
    const keys = invalidate.mock.calls.map(([filters]) => (filters as { queryKey: unknown }).queryKey);
    expect(keys).toContainEqual(qk.suspicious.all);
    expect(keys).toContainEqual(qk.tenders.ourPriceAll);
  });

  it("«Снять…»: подтверждение с заголовком; «Отмена» — запроса нет; «Снять везде» — DELETE и перечитывание", async () => {
    const { calls } = installServer();
    const qc = createTestQueryClient();
    const invalidate = vi.spyOn(qc, "invalidateQueries");
    renderTab("/contracts?tab=suspicious&line=7001", { queryClient: qc });
    const item = await screen.findByTestId("resolved-7001");

    await userEvent.click(within(item).getByRole("button", { name: "Снять во всех тендерах…" }));
    const dialog = await screen.findByRole("alertdialog");
    await waitForDialogFocus("alertdialog");
    expect(within(dialog).getByRole("heading", { name: "Снять «ошибку в цене»?" })).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Отмена" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(calls.deletes).toEqual([]);

    await userEvent.click(within(item).getByRole("button", { name: "Снять во всех тендерах…" }));
    const again = await screen.findByRole("alertdialog");
    await waitForDialogFocus("alertdialog");
    const listsBefore = calls.lists.length;
    const countsBefore = calls.counts;
    await userEvent.click(within(again).getByRole("button", { name: "Снять везде" }));

    await waitFor(() => expect(calls.deletes).toEqual(["/api/v1/position-items/7001/price-error"]));
    await waitFor(() => expect(calls.lists.length).toBeGreaterThan(listsBefore));
    await waitFor(() => expect(calls.counts).toBeGreaterThan(countsBefore));
    const keys = invalidate.mock.calls.map(([filters]) => (filters as { queryKey: unknown }).queryKey);
    expect(keys).toContainEqual(qk.tenders.ourPriceAll);
    await waitFor(() => expect(screen.queryByTestId("resolved-7001")).not.toBeInTheDocument());
  });
});

describe("пример без цен группы и с чужой базой НДС", () => {
  it("у выброса без prices полосы нет, у соседей с неизвестной базой НДС — подпись «как в смете»", async () => {
    installServer();
    server.use(
      http.get(LIST, () =>
        HttpResponse.json({
          ...suspiciousListFixture,
          unresolved: [
            suspectOutlierFixture({
              prices: null,
              neighbors: [neighborRowFixture({ gross: true }), neighborRowFixture({ position_item_id: 5001, is_self: true })],
            }),
            suspectTwinFixture(),
          ],
        })
      )
    );
    renderTab("/contracts?tab=suspicious&line=5001");
    const item = await screen.findByTestId("suspect-5001");
    expect(within(item).queryByTestId("price-strip")).not.toBeInTheDocument();
    expect(within(item).queryByTestId("block-prices-5001")).not.toBeInTheDocument();
    expect(within(item).getByTestId("block-neighbors-5001")).toHaveTextContent("НДС сметы не известен");
  });
});
