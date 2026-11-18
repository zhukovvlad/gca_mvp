import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { useLocation, useNavigate } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { SAVING_LABEL } from "@/pages/tenders/our-price/ourPriceCopy";
import { ladderQueues } from "@/test/handlers";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";

import { ZONE_ENTRY_NOT_FOUND } from "./labels";
import { SuggestionsTab } from "./SuggestionsTab";
import { ZoneEntryCard } from "./ZoneEntryCard";
import { ZonesQueue } from "./ZonesQueue";

/**
 * Карточка входа по ссылке «ждёт решения» (дизайн 2026-11-18-zone-link-entry §3). Фикстура
 * `handlers.ts`: цепочка 1 «Корпус 2 / Раздел 7» спорная, 4 «Фундаменты» — зона от правила (подземная).
 * Вход на цепочку дальше первой страницы очереди проверяет `SuggestionsTab.ladder.test.tsx`.
 */

const LATER = { timeout: 8000 };
const ZONE_NAMES = ["Подземная", "Надземная", "Территория", "Весь объект"];

function NavProbe() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  return (
    <>
      <span data-testid="path">{pathname}</span>
      <button onClick={() => navigate("/zones")}>перейти</button>
    </>
  );
}

function queueRowOf(text: RegExp) {
  const row = screen.getAllByTestId("zone-row").find((r) => text.test(r.textContent ?? ""));
  if (!row) throw new Error(`нет строки ${text}`);
  return row;
}

describe("ZoneEntryCard", () => {
  it("показывает разделы, счётчики, примеры, модель и четыре кнопки зон, ни одна не нажата", async () => {
    renderWithProviders(<ZoneEntryCard chainId={1} />);

    const card = await screen.findByTestId("zone-entry-card");
    expect(card).toHaveTextContent("Строки КП ждут зону этой цепочки разделов");
    expect(card).toHaveTextContent("Выберите зону. Решение действует на все сметы с этой цепочкой");
    expect(card).toHaveTextContent("Корпус 2 / Раздел 7 / Прочие работы");
    expect(card).toHaveTextContent("КП: 412 строк · договоры: 0");
    expect(card).toHaveTextContent("Монтаж ограждения кровли");
    expect(card).toHaveTextContent("Модель: надземная · уверенность 0,62 — «раздел смешанный»");
    for (const name of ZONE_NAMES) {
      expect(within(card).getByRole("button", { name })).toHaveAttribute("aria-pressed", "false");
    }
  });

  it("нажатие зоны шлёт ровно одно решение; после перечитывания кнопка нажата, вместо модели «зона поставлена: человек»", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ZoneEntryCard chainId={1} />);
    const card = await screen.findByTestId("zone-entry-card");

    await user.click(within(card).getByRole("button", { name: "Подземная" }));

    await waitFor(() => expect(ladderQueues.zoneDecisions).toEqual([{ chainId: 1, zone: "underground" }]), LATER);
    await waitFor(
      () => expect(within(card).getByRole("button", { name: "Подземная" })).toHaveAttribute("aria-pressed", "true"),
      LATER
    );
    expect(card).toHaveTextContent("зона поставлена: человек");
    expect(card).not.toHaveTextContent("Модель:");
    expect(screen.getByTestId("zone-entry-card")).toBe(card);
  });

  describe("общая блокировка карточки и очереди", () => {
    function holdZonePut() {
      let release!: () => void;
      const hold = new Promise<void>((resolve) => {
        release = resolve;
      });
      server.use(
        http.put("/api/v1/semantic/section-chains/:id/zone", async ({ params, request }) => {
          const { zone } = (await request.json()) as { zone: string };
          ladderQueues.zoneDecisions.push({ chainId: Number(params.id), zone });
          await hold;
          const chain = ladderQueues.chains.find((c) => c.id === Number(params.id));
          if (chain) {
            chain.zone = zone as typeof chain.zone;
            chain.zone_source = "human";
            chain.proposed_zone = chain.proposed_confidence = chain.proposed_reason = null;
          }
          return new HttpResponse(null, { status: 204 });
        })
      );
      return release;
    }

    async function renderBoth() {
      const user = userEvent.setup();
      renderWithProviders(
        <>
          <ZoneEntryCard chainId={4} />
          <ZonesQueue />
        </>
      );
      const card = await screen.findByTestId("zone-entry-card");
      await screen.findAllByTestId("zone-row");
      // Цепочка 4 (зона от правила) видна в очереди только под фильтром «Все».
      await user.click(screen.getByRole("combobox", { name: "Показать" }));
      await user.click(await screen.findByRole("option", { name: "Все" }));
      await waitFor(() => expect(screen.getAllByTestId("zone-row")).toHaveLength(6), LATER);
      return { user, card };
    }

    function expectBothBlocked(card: HTMLElement) {
      const row = queueRowOf(/Фундаменты/);
      for (const name of ZONE_NAMES) {
        expect(within(card).getByRole("button", { name })).toBeDisabled();
        expect(within(row).getByRole("button", { name })).toBeDisabled();
      }
      expect(screen.getByRole("button", { name: /Принять все предложения модели/ })).toBeDisabled();
      expect(screen.getAllByTestId("recalc-overlay")).toHaveLength(2);
      expect(screen.getAllByTestId("recalc-overlay")[0]).toHaveTextContent(SAVING_LABEL);
    }

    async function expectBothShow(card: HTMLElement, zone: string) {
      await waitFor(() => expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument(), LATER);
      const row = queueRowOf(/Фундаменты/);
      for (const name of ZONE_NAMES) {
        const pressed = name === zone ? "true" : "false";
        expect(within(card).getByRole("button", { name })).toHaveAttribute("aria-pressed", pressed);
        expect(within(row).getByRole("button", { name })).toHaveAttribute("aria-pressed", pressed);
        expect(within(card).getByRole("button", { name })).toBeEnabled();
      }
    }

    it("решение из карточки блокирует кнопки зон и «Принять все» в очереди; второго решения нет; затем обе области согласны", async () => {
      const release = holdZonePut();
      const { user, card } = await renderBoth();

      await user.click(within(card).getByRole("button", { name: "Надземная" }));

      await waitFor(() => expectBothBlocked(card));
      await user.click(within(queueRowOf(/Фундаменты/)).getByRole("button", { name: "Территория" }));
      expect(ladderQueues.zoneDecisions).toEqual([{ chainId: 4, zone: "aboveground" }]);

      release();
      await expectBothShow(card, "Надземная");
      expect(ladderQueues.zoneDecisions).toHaveLength(1);
    });

    it("решение из строки очереди блокирует кнопки карточки; второго решения нет; затем обе области согласны", async () => {
      const release = holdZonePut();
      const { user, card } = await renderBoth();

      await user.click(within(queueRowOf(/Фундаменты/)).getByRole("button", { name: "Территория" }));

      await waitFor(() => expectBothBlocked(card));
      await user.click(within(card).getByRole("button", { name: "Весь объект" }));
      expect(ladderQueues.zoneDecisions).toEqual([{ chainId: 4, zone: "territory" }]);

      release();
      await expectBothShow(card, "Территория");
      expect(ladderQueues.zoneDecisions).toHaveLength(1);
    });

    // Ревью Codex к 011825a: PUT уже ответил, а перечитывание карточки ещё идёт — блокировка
    // держится до его конца, иначе между ответом и перечитыванием можно нажать по старым данным.
    it("PUT ответил, перечитывание карточки задержано: обе области заблокированы до его конца; плашка карточки — по центру", async () => {
      let release!: () => void;
      const hold = new Promise<void>((resolve) => {
        release = resolve;
      });
      let cardReads = 0;
      server.use(
        http.get("/api/v1/semantic/section-chains/:id", async ({ params }) => {
          cardReads += 1;
          if (ladderQueues.zoneDecisions.length > 0) await hold;
          const chain = ladderQueues.chains.find((c) => c.id === Number(params.id));
          return chain ? HttpResponse.json(chain) : new HttpResponse(null, { status: 404 });
        })
      );
      const { user, card } = await renderBoth();
      const readsBefore = cardReads;

      await user.click(within(card).getByRole("button", { name: "Надземная" }));

      await waitFor(() => expect(cardReads).toBeGreaterThan(readsBefore), LATER);
      await waitFor(() => expect(ladderQueues.zoneDecisions).toEqual([{ chainId: 4, zone: "aboveground" }]), LATER);
      expectBothBlocked(card);
      const [cardOverlay, queueOverlay] = screen.getAllByTestId("recalc-overlay");
      expect(card).toContainElement(cardOverlay);
      expect(cardOverlay).toHaveAttribute("data-placement", "center");
      expect(queueOverlay).toHaveAttribute("data-placement", "top");
      await user.click(within(queueRowOf(/Фундаменты/)).getByRole("button", { name: "Территория" }));
      expect(ladderQueues.zoneDecisions).toHaveLength(1);

      release();
      await expectBothShow(card, "Надземная");
    });
  });

  it("404 от маршрута: вместо карточки плашка «не найдена», очередь ниже отрисована", async () => {
    renderWithProviders(<SuggestionsTab chainId={999} />);

    expect(await screen.findByTestId("zone-entry-not-found")).toHaveTextContent(ZONE_ENTRY_NOT_FOUND);
    expect(screen.queryByTestId("zone-entry-card")).not.toBeInTheDocument();
    expect(await screen.findAllByTestId("zone-row")).toHaveLength(3);
  });

  it("ошибка 500 — общая плашка ошибки, а не текст «не найдена»", async () => {
    server.use(
      http.get("/api/v1/semantic/section-chains/:id", () => new HttpResponse(null, { status: 500 }))
    );
    renderWithProviders(<ZoneEntryCard chainId={1} />);

    expect(await screen.findByText("Ошибка загрузки", undefined, LATER)).toBeInTheDocument();
    expect(screen.queryByText(/не найдена/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("zone-entry-not-found")).not.toBeInTheDocument();
  });

  describe("«← вернуться к КП»", () => {
    it("при первом входе (истории нет) ссылки нет", async () => {
      renderWithProviders(<ZoneEntryCard chainId={1} />);
      await screen.findByTestId("zone-entry-card");

      expect(screen.queryByRole("button", { name: "← вернуться к КП" })).not.toBeInTheDocument();
    });

    it("после навигации есть, и нажатие возвращает на предыдущий адрес", async () => {
      const user = userEvent.setup();
      renderWithProviders(
        <>
          <NavProbe />
          <ZoneEntryCard chainId={1} />
        </>,
        { initialRoute: "/kp" }
      );
      await screen.findByTestId("zone-entry-card");
      await user.click(screen.getByRole("button", { name: "перейти" }));
      expect(screen.getByTestId("path")).toHaveTextContent("/zones");

      await user.click(await screen.findByRole("button", { name: "← вернуться к КП" }));

      await waitFor(() => expect(screen.getByTestId("path")).toHaveTextContent("/kp"));
    });
  });
});
