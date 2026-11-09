import { QueryClient, useQuery } from "@tanstack/react-query";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { qk } from "@/services/queryKeys";
import { chainRow, ladderQueues } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";

import { ZonesQueue } from "./ZonesQueue";

/**
 * Очередь «Зоны» (лестница аналогов §2.17, §2.20): по умолчанию спорные, поиск, страницы,
 * строка с названиями цепочки, примерами, предложением модели и четырьмя зонами. Фикстура
 * `handlers.ts`: цепочки 1–2 спорные (предложение есть, зоны нет), 3 — спорная с ответом
 * модели «не знаю» (`unknown`), 4 — зона от правила, 5 — зона от модели, 6 — без зоны и без
 * ответа (задание с ошибкой).
 */

const LATER = { timeout: 8000 };

function rowOf(text: RegExp) {
  const row = screen.getAllByTestId("zone-row").find((r) => text.test(r.textContent ?? ""));
  if (!row) throw new Error(`нет строки ${text}`);
  return row;
}

describe("ZonesQueue", () => {
  it("по умолчанию — спорные: запрос filter=disputed, три строки вместе с «не знаю», решённые и без ответа не показаны", async () => {
    renderWithProviders(<ZonesQueue />);

    await screen.findAllByTestId("zone-row");
    expect(screen.getAllByTestId("zone-row")).toHaveLength(3);
    expect(ladderQueues.chainRequests[0]).toContain("filter=disputed");
    expect(screen.queryByText(/Фундаменты/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Прочие затраты/)).not.toBeInTheDocument();
  });

  it("строка: цепочка, число строк КП и договоров, примеры, предложение с уверенностью и причиной", async () => {
    renderWithProviders(<ZonesQueue />);
    await screen.findAllByTestId("zone-row");

    const row = rowOf(/Корпус 2/);
    expect(within(row).getByText(/Корпус 2 \/ Раздел 7 \/ Прочие работы/)).toBeInTheDocument();
    expect(within(row).getByText(/КП: 412/)).toBeInTheDocument();
    expect(within(row).getByText(/договоры: 0/)).toBeInTheDocument();
    expect(within(row).getByText(/Монтаж ограждения кровли/)).toBeInTheDocument();
    expect(within(row).getByText(/надземная/)).toBeInTheDocument();
    expect(within(row).getByText(/0,62/)).toBeInTheDocument();
    expect(within(row).getByText(/раздел смешанный/)).toBeInTheDocument();
  });

  it("модель не знает (`unknown`): спорная строка, подпись «не знаю» с уверенностью и причиной", async () => {
    renderWithProviders(<ZonesQueue />);
    await screen.findAllByTestId("zone-row");

    const row = rowOf(/Секция 3/);
    expect(within(row).getByText(/Модель: не знаю · уверенность 0,35 — «этажи под и над землёй»/)).toBeInTheDocument();
  });

  it("без ответа модели: цепочка в фильтре «Без зоны», подпись «ответа нет»", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ZonesQueue />);
    await screen.findAllByTestId("zone-row");
    await user.click(screen.getByRole("combobox", { name: "Показать" }));
    await user.click(await screen.findByRole("option", { name: "Без зоны" }));
    await waitFor(() => expect(screen.getAllByTestId("zone-row")).toHaveLength(1), LATER);

    expect(within(rowOf(/Прочие затраты/)).getByText("Модель: ответа нет")).toBeInTheDocument();
  });

  it("четыре зоны переключателем; ни одна не нажата, пока зоны нет", async () => {
    renderWithProviders(<ZonesQueue />);
    await screen.findAllByTestId("zone-row");

    const row = rowOf(/Корпус 2/);
    for (const name of ["Подземная", "Надземная", "Территория", "Весь объект"]) {
      expect(within(row).getByRole("button", { name })).toHaveAttribute("aria-pressed", "false");
    }
  });

  it("решение: PUT с зоной, строка уходит из спорных", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ZonesQueue />);
    await screen.findAllByTestId("zone-row");

    await user.click(within(rowOf(/Корпус 2/)).getByRole("button", { name: "Весь объект" }));

    await waitFor(() => expect(ladderQueues.zoneDecisions).toEqual([{ chainId: 1, zone: "whole" }]), LATER);
    await waitFor(() => expect(screen.getAllByTestId("zone-row")).toHaveLength(2), LATER);
    expect(screen.queryByText(/Корпус 2 \/ Раздел 7/)).not.toBeInTheDocument();
  });

  it("фильтр «Все»: запрос filter=all, решённые строки с нажатой зоной и источником", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ZonesQueue />);
    await screen.findAllByTestId("zone-row");

    await user.click(screen.getByRole("combobox", { name: "Показать" }));
    await user.click(await screen.findByRole("option", { name: "Все" }));

    await waitFor(() => expect(screen.getAllByTestId("zone-row")).toHaveLength(6), LATER);
    expect(ladderQueues.chainRequests.at(-1)).toContain("filter=all");
    const row = rowOf(/Фундаменты/);
    expect(within(row).getByRole("button", { name: "Подземная" })).toHaveAttribute("aria-pressed", "true");
    expect(within(row).getByText(/правило/)).toBeInTheDocument();
  });

  it("поиск уходит в q и сбрасывает страницу", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ZonesQueue />);
    await screen.findAllByTestId("zone-row");

    await user.type(screen.getByRole("searchbox", { name: "Поиск по разделам" }), "сети");

    await waitFor(() => expect(screen.getAllByTestId("zone-row")).toHaveLength(1), LATER);
    expect(ladderQueues.chainRequests.at(-1)).toContain("q=%D1%81%D0%B5%D1%82%D0%B8");
    expect(ladderQueues.chainRequests.at(-1)).toContain("page=1");
  });

  it("страницы: 55 спорных цепочек, вторая страница запрашивается с page=2", async () => {
    const user = userEvent.setup();
    ladderQueues.chains = Array.from({ length: 55 }, (_, i) => chainRow(i + 1));
    renderWithProviders(<ZonesQueue />);
    await screen.findAllByTestId("zone-row");
    expect(screen.getAllByTestId("zone-row")).toHaveLength(50);

    await user.click(screen.getByText("2", { selector: "a" }));

    await waitFor(() => expect(screen.getAllByTestId("zone-row")).toHaveLength(5), LATER);
    expect(ladderQueues.chainRequests.at(-1)).toContain("page=2");
  });

  it("подсветка по ?chain=: строка выбранной цепочки помечена", async () => {
    renderWithProviders(<ZonesQueue highlightChainId={2} />);
    await screen.findAllByTestId("zone-row");

    expect(rowOf(/Временные сети/)).toHaveAttribute("data-highlighted", "true");
    expect(rowOf(/Корпус 2/)).not.toHaveAttribute("data-highlighted");
  });

  it("цепочки из ссылки нет на странице: заметка вместо подсветки", async () => {
    renderWithProviders(<ZonesQueue highlightChainId={999} />);
    await screen.findAllByTestId("zone-row");

    expect(screen.getByTestId("zone-not-found")).toBeInTheDocument();
  });

  it("решение зоны → перезапрос разбора: открытый экран «Против нашей цены» запрашивает данные заново", async () => {
    const user = userEvent.setup();
    let fetches = 0;
    function OurPriceProbe() {
      useQuery({
        queryKey: qk.tenders.ourPriceRound(1, 1, null),
        queryFn: async () => {
          fetches += 1;
          return { fetches };
        },
      });
      return null;
    }
    renderWithProviders(
      <>
        <OurPriceProbe />
        <ZonesQueue />
      </>
    );
    await screen.findAllByTestId("zone-row");
    await waitFor(() => expect(fetches).toBe(1));

    await user.click(within(rowOf(/Корпус 2/)).getByRole("button", { name: "Надземная" }));

    await waitFor(() => expect(fetches).toBe(2), LATER);
  });

  it("решение зоны перечитывает очереди, сводку, схемы семей и весь разбор «Против нашей цены»", async () => {
    const user = userEvent.setup();
    // gcTime бесконечен: без наблюдателей записи кэша иначе тут же собираются.
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity, staleTime: 0 }, mutations: { retry: false } },
    });
    const keys = {
      round: ["tenders", "our-price", "round", 1, 1, null],
      line: ["tenders", "our-price", "line", 1, 1, 7, 99, null],
      status: ["semantic-queue", "status"],
      schema: ["work-families", "schema", 43],
      releasable: ["semantic-queue", "releasable", 1],
    } as const;
    for (const key of Object.values(keys)) qc.setQueryData(key, { stub: true });
    renderWithProviders(<ZonesQueue />, { queryClient: qc });
    await screen.findAllByTestId("zone-row");

    await user.click(within(rowOf(/Корпус 2/)).getByRole("button", { name: "Надземная" }));

    await waitFor(() => {
      for (const key of Object.values(keys)) {
        expect(qc.getQueryState(key)?.isInvalidated, key.join("/")).toBe(true);
      }
    }, LATER);
  });

  it("отказ сервера — тост с его текстом, строка остаётся", async () => {
    // Экран шлёт только четыре известные зоны (`invalid_zone` ему недостижим): отказ — цепочка,
    // исчезнувшая до решения, сервер отвечает 404.
    const user = userEvent.setup();
    renderWithProviders(<ZonesQueue />);
    await screen.findAllByTestId("zone-row");
    ladderQueues.chains = ladderQueues.chains.filter((c) => c.id !== 1);

    await user.click(within(rowOf(/Корпус 2/)).getByRole("button", { name: "Подземная" }));

    expect(await screen.findByText("нет цепочки")).toBeInTheDocument();
  });
});
