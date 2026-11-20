import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import {
  OUR_PRICE_LINE_ID,
  OUR_PRICE_OFFER_ID,
  OUR_PRICE_ROUND_ID,
  OUR_PRICE_TENDER_ID,
  ourPriceLineViewFixture,
  zoneChainFixture,
} from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import { useOurPriceLine } from "@/services/queries";
import type { ZoneChainInfo, ZoneModelState } from "@/types/domain";

import { LineZoneBlock } from "./LineZoneBlock";
import { zoneModelText, zoneNeedsConfirm } from "./lineZoneRules";

const LATER = { timeout: 8000 };
const PUT_URL = "/api/v1/semantic/section-chains/:id/zone";
const LINE_PATH = "/api/v1/tenders/:id/rounds/:roundId/our-price/offers/:offerId/lines/:positionItemId";
const ZONE_NAMES = ["Подземная", "Надземная", "Территория", "Весь объект"];

// Эталоны — литералы спеки §3 и дизайна §2, а не константы проверяемого кода.
const HINT =
  "Зона — часть объекта, где выполняется работа: подземная, надземная, территория или весь объект. Её определяют разделы сметы, а не название работы: один и тот же дренаж бывает и над паркингом, и на кровле корпуса. Аналоги строке подбираются только из договоров с той же зоной.";
const SCOPE_ROWS_0 = "Зона назначается строкам раздела во всех сметах: КП — 56, договоры — 0.";
const SCOPE_ROWS_3 = "Зона назначается строкам раздела во всех сметах: КП — 56, договоры — 3.";
const SCOPE_CONTRACTS_3 =
  "Строки договоров этого раздела (3) служат аналогами для строк других КП той же зоны: решение меняет и их сравнение.";

const PROPOSAL = { zone: "underground", confidence: "0.85", reason: "Причина" } as const;

function servePut(opts: { hold?: Promise<void> } = {}) {
  const calls: Array<{ chainId: number; zone: string }> = [];
  server.use(
    http.put(PUT_URL, async ({ params, request }) => {
      const { zone } = (await request.json()) as { zone: string };
      calls.push({ chainId: Number(params.id), zone });
      if (opts.hold) await opts.hold;
      return new HttpResponse(null, { status: 204 });
    })
  );
  return calls;
}

function holdable() {
  let release!: () => void;
  const hold = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { hold, release };
}

function renderBlock(chain: ZoneChainInfo | null, canEdit = true) {
  return renderWithProviders(<LineZoneBlock chain={chain} canEdit={canEdit} />);
}

const block = () => screen.getByTestId("line-zone-block");

describe("zoneNeedsConfirm", () => {
  it.each([
    [{ zone: null, rows_contracts: 0 }, "underground", false],
    [{ zone: null, rows_contracts: 3 }, "underground", true],
    [{ zone: "underground", rows_contracts: 0 }, "underground", false],
    [{ zone: "underground", rows_contracts: 3 }, "underground", false],
    [{ zone: "underground", rows_contracts: 0 }, "whole", true],
    [{ zone: "underground", rows_contracts: 3 }, "whole", true],
  ] as const)("%j → %s: %s", (partial, zone, expected) => {
    expect(zoneNeedsConfirm(zoneChainFixture({ ...partial }), zone)).toBe(expected);
  });
});

describe("zoneModelText", () => {
  const undecided = (model_state: ZoneModelState, over: Partial<ZoneChainInfo> = {}) =>
    zoneChainFixture({ zone: null, model_state, ...over });

  it.each([
    ["running", "Модель определяет зону."],
    ["paused", "Модель ждёт сброса лимита провайдера; можно не ждать и выбрать зону."],
    ["stopped", "Разбор моделью остановлен до решения администратора; зону выбирает администратор."],
    ["failed", "Модель не ответила; решает администратор."],
    ["none", "Ответа модели нет; решает администратор."],
  ] as const)("без зоны, %s", (state, text) => {
    expect(zoneModelText(undecided(state))).toBe(text);
  });

  it("без зоны, предложение ниже порога: зона, уверенность, причина и порог", () => {
    expect(zoneModelText(undecided("proposed", { model_answer: PROPOSAL }))).toBe(
      "Модель предлагает: подземная часть · 0,85 — «Причина»; ниже порога 0,95, решает администратор."
    );
  });

  it("без зоны, «не знаю» с высокой уверенностью: слова о пороге нет", () => {
    const text = zoneModelText(
      undecided("unknown", { model_answer: { zone: "unknown", confidence: "0.97", reason: "Не ясно" } })
    );
    expect(text).toBe("Модель не смогла определить зону — «Не ясно»; решает администратор.");
    expect(text).not.toContain("порог");
  });

  it("с зоной и ответом модели: «предлагала» с зоной, уверенностью и причиной", () => {
    const decided = zoneChainFixture({
      zone: "whole",
      zone_source: "human",
      model_state: null,
      model_answer: PROPOSAL,
    });
    expect(zoneModelText(decided)).toBe("Модель предлагала: подземная часть · 0,85 — «Причина»");
  });

  it("с зоной и ответом «не знаю»: «не знаю» вместо зоны", () => {
    const decided = zoneChainFixture({
      zone: "whole",
      zone_source: "human",
      model_state: null,
      model_answer: { zone: "unknown", confidence: "0.97", reason: "Не ясно" },
    });
    expect(zoneModelText(decided)).toBe("Модель предлагала: не знаю · 0,97 — «Не ясно»");
  });

  it("с зоной без ответа модели строки нет", () => {
    const decided = zoneChainFixture({ zone: "whole", zone_source: "rule", model_state: null, model_answer: null });
    expect(zoneModelText(decided)).toBeNull();
  });
});

describe("LineZoneBlock: зона и источник", () => {
  it("правило: название зоны с заглавной и «по правилу»", () => {
    renderBlock(zoneChainFixture({ zone: "underground", zone_source: "rule", model_state: null }));
    expect(screen.getByTestId("zone-name")).toHaveTextContent("Подземная часть");
    expect(screen.getByTestId("zone-source")).toHaveTextContent("по правилу");
    expect(block()).toHaveTextContent("Зона места");
  });

  it("модель с ответом: «поставила модель · 0,97»", () => {
    renderBlock(
      zoneChainFixture({
        zone: "whole",
        zone_source: "model",
        model_state: null,
        model_answer: { zone: "whole", confidence: "0.97", reason: "Причина" },
      })
    );
    expect(screen.getByTestId("zone-name")).toHaveTextContent("Весь объект");
    expect(screen.getByTestId("zone-source")).toHaveTextContent("поставила модель · 0,97");
  });

  it("модель без ответа в журнале: «поставила модель» без уверенности", () => {
    renderBlock(zoneChainFixture({ zone: "whole", zone_source: "model", model_state: null, model_answer: null }));
    expect(screen.getByTestId("zone-source").textContent).toBe("поставила модель");
  });

  it("человек: «поставил человек · дата», имени нет", () => {
    renderBlock(
      zoneChainFixture({
        zone: "aboveground",
        zone_source: "human",
        decided_at: "2026-11-19T12:00:00+00:00",
        model_state: null,
      })
    );
    expect(screen.getByTestId("zone-source")).toHaveTextContent("поставил человек · 19.11.2026");
  });

  it("без зоны, предложение ждёт решения: «не определена» и «ждёт решения администратора»", () => {
    renderBlock(zoneChainFixture({ model_state: "proposed", model_answer: PROPOSAL }));
    expect(screen.getByTestId("zone-name")).toHaveTextContent("не определена");
    expect(screen.getByTestId("zone-source")).toHaveTextContent("ждёт решения администратора");
  });

  it("без зоны, модель разбирает: «модель определяет»", () => {
    renderBlock(zoneChainFixture({ model_state: "running" }));
    expect(screen.getByTestId("zone-source")).toHaveTextContent("модель определяет");
  });

  it("без зоны и без ответа модели: подписи источника нет", () => {
    renderBlock(zoneChainFixture({ model_state: "none" }));
    expect(screen.getByTestId("zone-name")).toHaveTextContent("не определена");
    expect(screen.queryByTestId("zone-source")).not.toBeInTheDocument();
  });

  it("пауза и остановка — разные тексты; пауза «модель определяет», остановка «ждёт решения»", () => {
    const paused = renderBlock(zoneChainFixture({ model_state: "paused" }));
    const pausedModel = screen.getByTestId("zone-model").textContent;
    expect(screen.getByTestId("zone-name")).toHaveTextContent("не определена");
    expect(screen.getByTestId("zone-source")).toHaveTextContent("модель определяет");
    paused.unmount();

    renderBlock(zoneChainFixture({ model_state: "stopped" }));
    expect(screen.getByTestId("zone-model").textContent).not.toBe(pausedModel);
    expect(screen.getByTestId("zone-source")).toHaveTextContent("ждёт решения администратора");
    expect(screen.getByTestId("zone-source")).not.toHaveTextContent("модель определяет");
  });

  it("охват: строки КП и договоров; вторая строка про договоры — только при договорах > 0", () => {
    const zero = renderBlock(zoneChainFixture({ rows_contracts: 0 }));
    expect(screen.getByTestId("zone-scope")).toHaveTextContent(SCOPE_ROWS_0);
    expect(screen.getByTestId("zone-scope")).not.toHaveTextContent("служат аналогами");
    zero.unmount();

    renderBlock(zoneChainFixture({ rows_contracts: 3 }));
    expect(screen.getByTestId("zone-scope")).toHaveTextContent(SCOPE_ROWS_3);
    expect(screen.getByTestId("zone-scope")).toHaveTextContent(SCOPE_CONTRACTS_3);
  });

  it("строка модели: у цепочки с зоной и ответом — «предлагала»; без ответа — строки нет", () => {
    const withAnswer = renderBlock(
      zoneChainFixture({ zone: "whole", zone_source: "human", model_state: null, model_answer: PROPOSAL })
    );
    expect(screen.getByTestId("zone-model")).toHaveTextContent(
      "Модель предлагала: подземная часть · 0,85 — «Причина»"
    );
    withAnswer.unmount();

    renderBlock(zoneChainFixture({ zone: "whole", zone_source: "rule", model_state: null, model_answer: null }));
    expect(screen.queryByTestId("zone-model")).not.toBeInTheDocument();
  });
});

describe("LineZoneBlock: правка администратором", () => {
  it("первая установка без строк договоров: «Подземная» шлёт одно решение без окна", async () => {
    const user = userEvent.setup();
    const calls = servePut();
    renderBlock(zoneChainFixture({ rows_contracts: 0, model_state: "none" }));

    await user.click(within(block()).getByRole("button", { name: "Подземная" }));

    await waitFor(() => expect(calls).toEqual([{ chainId: 91, zone: "underground" }]), LATER);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("первая установка со строками договоров: окно с обеими строками охвата; «Отмена» — ни одного решения; «Сменить зону» — ровно одно", async () => {
    const user = userEvent.setup();
    const calls = servePut();
    renderBlock(zoneChainFixture({ rows_contracts: 3, model_state: "none" }));

    await user.click(within(block()).getByRole("button", { name: "Подземная" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Сменить зону раздела?");
    expect(dialog).toHaveTextContent("«Раздел А / Подраздел Б»: Не определена → Подземная часть.");
    expect(dialog).toHaveTextContent(SCOPE_ROWS_3);
    expect(dialog).toHaveTextContent(SCOPE_CONTRACTS_3);
    expect(calls).toEqual([]);

    await user.click(within(dialog).getByRole("button", { name: "Отмена" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(), LATER);
    expect(calls).toEqual([]);

    await user.click(within(block()).getByRole("button", { name: "Подземная" }));
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Сменить зону" }));
    await waitFor(() => expect(calls).toEqual([{ chainId: 91, zone: "underground" }]), LATER);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("стоящая зона от правила: нажатие закрепляет без окна; «Весь объект» — через окно со словами «было → стало»", async () => {
    const user = userEvent.setup();
    const calls = servePut();
    renderBlock(zoneChainFixture({ zone: "aboveground", zone_source: "rule", model_state: null }));

    await user.click(within(block()).getByRole("button", { name: "Надземная" }));
    await waitFor(() => expect(calls).toEqual([{ chainId: 91, zone: "aboveground" }]), LATER);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    await waitFor(() => expect(within(block()).getByRole("button", { name: "Надземная" })).toBeEnabled(), LATER);

    await user.click(within(block()).getByRole("button", { name: "Весь объект" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("«Раздел А / Подраздел Б»: Надземная часть → Весь объект.");
    expect(calls).toHaveLength(1);
  });

  it("у администратора есть ссылка на очередь «Зоны» этой цепочки", () => {
    renderBlock(zoneChainFixture({ id: 27 }));
    const link = screen.getByRole("link", { name: "открыть в очереди «Зоны» →" });
    expect(link).toHaveAttribute("href", "/families?tab=suggestions&queue=zones&chain=27");
  });

  it("member: ни кнопок зон, ни ссылки; есть «Зону раздела меняет администратор.»", () => {
    renderBlock(zoneChainFixture({ zone: "underground", zone_source: "human", model_state: null }), false);
    expect(within(block()).queryAllByRole("button", { name: /Подземная|Надземная|Территория|Весь объект/ })).toHaveLength(0);
    expect(within(block()).queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByTestId("zone-readonly")).toHaveTextContent("Зону раздела меняет администратор.");
    expect(screen.getByTestId("zone-name")).toHaveTextContent("Подземная часть");
  });

  it("у администратора фразы «Зону раздела меняет администратор.» нет", () => {
    renderBlock(zoneChainFixture());
    expect(screen.queryByTestId("zone-readonly")).not.toBeInTheDocument();
  });
});

describe("LineZoneBlock: строка без цепочки", () => {
  it("одна строка: без кнопок, ссылки, охвата и «?»", () => {
    renderBlock(null);
    expect(block()).toHaveTextContent(
      "Зона места · не определена — строка стоит вне разделов сметы, зону назначить некуда"
    );
    expect(within(block()).queryAllByRole("button")).toHaveLength(0);
    expect(within(block()).queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByTestId("zone-scope")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Что такое зона")).not.toBeInTheDocument();
  });
});

describe("LineZoneBlock: подсказка «?»", () => {
  it("фокус с клавиатуры открывает подсказку с текстом дизайна", async () => {
    const user = userEvent.setup();
    renderBlock(zoneChainFixture());
    expect(screen.queryByText(HINT)).not.toBeInTheDocument();

    await user.tab();

    expect(screen.getByLabelText("Что такое зона")).toHaveFocus();
    const popup = await screen.findByText(HINT);
    expect(popup.closest('[data-slot="tooltip-content"]')).not.toBeNull();
  });
});

describe("LineZoneBlock: общая блокировка", () => {
  it("пока идёт решение из блока: кнопки заблокированы, виден «Сохраняем…»; после ответа — снова доступны", async () => {
    const user = userEvent.setup();
    const { hold, release } = holdable();
    const calls = servePut({ hold });
    const view = renderBlock(zoneChainFixture({ rows_contracts: 0 }));

    await user.click(within(block()).getByRole("button", { name: "Подземная" }));

    await waitFor(() => expect(calls).toHaveLength(1), LATER);
    expect(await screen.findByTestId("recalc-overlay")).toHaveTextContent("Сохраняем…");
    for (const name of ZONE_NAMES) {
      expect(within(block()).getByRole("button", { name })).toBeDisabled();
    }
    await user.click(within(block()).getByRole("button", { name: "Надземная" }));
    expect(calls).toHaveLength(1);

    release();
    await waitFor(() => expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument(), LATER);
    // Панель ответила сохранённой зоной (в этом тесте её нет — блок получает её свойством).
    view.rerender(<LineZoneBlock chain={zoneChainFixture({ rows_contracts: 0, zone: "underground" })} canEdit />);
    for (const name of ZONE_NAMES) {
      expect(within(block()).getByRole("button", { name })).toBeEnabled();
    }
  });
});

describe("LineZoneBlock: свежая панель после решения", () => {
  /** Панель строки с блоком: ответ панели читает зону из `current`, а после решения его можно придержать. */
  function PanelHarness() {
    const query = useOurPriceLine(
      OUR_PRICE_TENDER_ID,
      OUR_PRICE_ROUND_ID,
      OUR_PRICE_OFFER_ID,
      OUR_PRICE_LINE_ID,
      null,
      null
    );
    if (query.data === undefined) return null;
    return <LineZoneBlock chain={query.data.zone_chain} canEdit />;
  }

  it("кнопки заняты, пока не ответила панель; второй выбор не уходит; после ответа смена зоны требует окна", async () => {
    const user = userEvent.setup();
    const { hold, release } = holdable();
    const decisions: string[] = [];
    let current = zoneChainFixture({ rows_contracts: 0, model_state: "none" });
    let decided = false;
    server.use(
      http.put(PUT_URL, async ({ request }) => {
        const { zone } = (await request.json()) as { zone: string };
        decisions.push(zone);
        current = zoneChainFixture({
          zone: zone as ZoneChainInfo["zone"],
          zone_source: "human",
          decided_at: "2026-11-19T12:00:00+00:00",
          model_state: null,
        });
        decided = true;
        return new HttpResponse(null, { status: 204 });
      }),
      http.get(LINE_PATH, async () => {
        // Первый ответ — до решения; ответ после решения придержан, пока тест не отпустит.
        if (decided) await hold;
        return HttpResponse.json({ ...ourPriceLineViewFixture(), zone_chain: current });
      })
    );
    renderWithProviders(<PanelHarness />);
    await screen.findByTestId("line-zone-block");

    await user.click(within(block()).getByRole("button", { name: "Подземная" }));

    await waitFor(() => expect(decisions).toEqual(["underground"]), LATER);
    // Решение и очереди уже прошли, ответ панели ещё идёт: блок под слоем «Сохраняем…».
    await waitFor(() => expect(screen.getByTestId("recalc-overlay")).toHaveTextContent("Сохраняем…"), LATER);
    await new Promise((resolve) => setTimeout(resolve, 100));
    for (const name of ZONE_NAMES) {
      expect(within(block()).getByRole("button", { name })).toBeDisabled();
    }
    await user.click(within(block()).getByRole("button", { name: "Весь объект" }));
    expect(decisions).toEqual(["underground"]);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

    release();
    await waitFor(() => expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument(), LATER);
    expect(screen.getByTestId("zone-name")).toHaveTextContent("Подземная часть");
    expect(within(block()).getByRole("button", { name: "Подземная" })).toHaveAttribute("aria-pressed", "true");

    await user.click(within(block()).getByRole("button", { name: "Весь объект" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("«Раздел А / Подраздел Б»: Подземная часть → Весь объект.");
    expect(decisions).toEqual(["underground"]);
  });

  it("панель не ответила после сохранения: текст про устаревшую панель, кнопки закрыты; «Обновить» — пока 500, текст остаётся; после ответа текст снят и смена зоны требует окна", async () => {
    const user = userEvent.setup();
    const decisions: string[] = [];
    let current = zoneChainFixture({ rows_contracts: 0, model_state: "none" });
    let decided = false;
    let panelOk = false;
    server.use(
      http.put(PUT_URL, async ({ request }) => {
        const { zone } = (await request.json()) as { zone: string };
        decisions.push(zone);
        current = zoneChainFixture({
          zone: zone as ZoneChainInfo["zone"],
          zone_source: "human",
          decided_at: "2026-11-19T12:00:00+00:00",
          model_state: null,
        });
        decided = true;
        return new HttpResponse(null, { status: 204 });
      }),
      http.get(LINE_PATH, () => {
        if (decided && !panelOk) return new HttpResponse(null, { status: 500 });
        return HttpResponse.json({ ...ourPriceLineViewFixture(), zone_chain: current });
      })
    );
    renderWithProviders(<PanelHarness />);
    await screen.findByTestId("line-zone-block");

    await user.click(within(block()).getByRole("button", { name: "Подземная" }));

    const stale = await screen.findByTestId("zone-stale", undefined, LATER);
    expect(stale).toHaveTextContent(
      "Зона сохранена, но панель не обновилась — обновите её, прежде чем менять зону снова."
    );
    await waitFor(() => expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument(), LATER);
    expect(screen.getByTestId("zone-name")).toHaveTextContent("не определена");
    for (const name of ZONE_NAMES) {
      expect(within(block()).getByRole("button", { name })).toBeDisabled();
    }
    await user.click(within(block()).getByRole("button", { name: "Весь объект" }));
    expect(decisions).toEqual(["underground"]);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

    await user.click(within(block()).getByRole("button", { name: "Обновить" }));
    await waitFor(() => expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument(), LATER);
    expect(screen.getByTestId("zone-stale")).toBeInTheDocument();
    expect(screen.getByTestId("zone-name")).toHaveTextContent("не определена");

    panelOk = true;
    await user.click(within(block()).getByRole("button", { name: "Обновить" }));
    await waitFor(() => expect(screen.queryByTestId("zone-stale")).not.toBeInTheDocument(), LATER);
    await waitFor(() => expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument(), LATER);
    expect(screen.getByTestId("zone-name")).toHaveTextContent("Подземная часть");

    await user.click(within(block()).getByRole("button", { name: "Весь объект" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("«Раздел А / Подраздел Б»: Подземная часть → Весь объект.");
    expect(decisions).toEqual(["underground"]);
  });
});
