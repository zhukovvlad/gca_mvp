import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "@/App";
import { handlerState } from "@/test/handlers";
import { holdRequests } from "@/test/hold";
import { OUR_PRICE_ROUND_ID, OUR_PRICE_TENDER_ID, ourPriceRoundFixture, sampleInflationSeries } from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import { OUR_PRICE_POLL_MS } from "@/services/queries";
import type { InflationSeries, OurPriceRound } from "@/types/domain";

import OurPriceRoundPage from "./OurPriceRoundPage";
import {
  INFLATION_SWITCH_LABEL,
  KP_NOT_ADJUSTED_NOTE,
  NO_INFLATION_CAPTION,
  PRICES_CAPTION_NET,
  PRICES_CAPTION_SHOWN_MIXED,
  RECALC_LABEL,
  VAT_SWITCH_LABEL,
  errorLabel,
} from "./ourPriceCopy";

const ROUND_PATH = "/api/v1/tenders/:id/rounds/:roundId/our-price";
const ROUTE = `/tenders/${OUR_PRICE_TENDER_ID}/rounds/${OUR_PRICE_ROUND_ID}/our-price`;
const ORIGINAL_SERIES = sampleInflationSeries;

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="loc">{location.pathname + location.search}</div>;
}

function renderPage(search = "", options: { role?: "admin" | "member" } = {}) {
  const user = { id: 1, email: "u@example.com", role: options.role ?? "admin" } as const;
  return renderWithProviders(
    <>
      <Routes>
        <Route path="/tenders/:tenderId/rounds/:roundId/our-price" element={<OurPriceRoundPage />} />
      </Routes>
      <LocationProbe />
    </>,
    { initialRoute: ROUTE + search, initialUser: user }
  );
}

const roundRequests = () => handlerState.ourPriceRequests.filter((r) => r.kind === "round");
const seriesParam = (index: number) =>
  new URL(roundRequests()[index].url).searchParams.get("inflation_series_id");

async function loaded() {
  await screen.findByTestId("cell-11-7001");
}

afterEach(() => {
  vi.useRealTimers();
  handlerState.inflationSeries = ORIGINAL_SERIES;
});

describe("состояния экрана", () => {
  it("первая загрузка — скелет без чужих цифр, затем экран; корень — container-page", async () => {
    const hold = holdRequests("get", ROUND_PATH);
    const { container } = renderPage();
    expect(container.querySelector(".container-page .animate-pulse")).not.toBeNull();
    expect(screen.queryByTestId("cell-11-7001")).not.toBeInTheDocument();
    hold.release();
    await loaded();
    expect(container.querySelector(".animate-pulse")).toBeNull();
    expect(container.querySelector(".container-page [data-testid='round-header']")).not.toBeNull();
  });

  it.each([
    [404, errorLabel(404)],
    [500, errorLabel(500)],
  ])("ответ %i: подпись причины, без пустого молчания и без скелета; корень — container-page", async (status, label) => {
    server.use(http.get(ROUND_PATH, () => HttpResponse.json({ detail: "x" }, { status })));
    const { container } = renderPage();
    expect(await screen.findByText(label)).toBeInTheDocument();
    expect(container.querySelector(".animate-pulse")).toBeNull();
    expect(screen.getByText(label).closest(".container-page")).not.toBeNull();
    expect(errorLabel(404)).not.toBe(errorLabel(500));
  });

  it("шапка: этап, число участников, дата, объект, площадь, класс и подпись цен", async () => {
    renderPage();
    await loaded();
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent("Этап 3");
    expect(heading).toHaveTextContent("3 участника");
    const header = screen.getByTestId("round-header");
    expect(header).toHaveTextContent("10.03.2026");
    expect(header).toHaveTextContent("Объект Т");
    expect(header).toHaveTextContent("5 000,00 м²");
    expect(header).toHaveTextContent("Класс 2");
    expect(header).toHaveTextContent(PRICES_CAPTION_NET);
  });

  it("карточки участников и таблица статей на экране", async () => {
    renderPage();
    await loaded();
    expect(screen.getByTestId("card-7001")).toBeInTheDocument();
    expect(screen.getByTestId("card-7002")).toHaveTextContent("КП не загружено");
    expect(screen.getByTestId("card-7003")).toHaveAttribute("data-reason", "unknown_vat_base");
    expect(screen.getByTestId("row-total")).toBeInTheDocument();
  });

  it("кнопка разбора ведёт на разбор участника и несёт переключатели адреса; у «КП не загружено» её нет", async () => {
    renderPage("?vat=shown&inflation=1");
    await loaded();
    expect(within(screen.getByTestId("card-7001")).getByRole("link")).toHaveAttribute(
      "href",
      `${ROUTE}/offers/7001?vat=shown&inflation=1`
    );
    expect(within(screen.getByTestId("card-7002")).queryByRole("link")).not.toBeInTheDocument();
    expect(within(screen.getByTestId("cell-11-7001")).getByRole("link")).toHaveAttribute(
      "href",
      `${ROUTE}/offers/7001?vat=shown&inflation=1&article=11`
    );
  });

  it("«дешевле всех на м²» — у минимального ₽/м² по числу, а не по строке", async () => {
    const base = ourPriceRoundFixture();
    const [a, b, c] = base.participants;
    const round: OurPriceRound = {
      ...base,
      participants: [
        { ...a, per_sqm: { net: "9", shown: "10" } },
        { ...b, state: "ok", estimate_id: 9002, per_sqm: { net: "10", shown: "11" }, totals: a.totals },
        c,
      ],
    };
    server.use(http.get(ROUND_PATH, () => HttpResponse.json(round)));
    renderPage();
    await loaded();
    expect(screen.getByTestId("card-7001")).toHaveTextContent("дешевле всех на м²");
    expect(screen.getByTestId("card-7002")).not.toHaveTextContent("дешевле всех на м²");
    expect(screen.getByTestId("card-7003")).not.toHaveTextContent("дешевле всех на м²");
  });

  it("«дешевле всех на м²» — по полной строке сервера: на экране у обоих 100,00, отмечен один", async () => {
    const base = ourPriceRoundFixture();
    const [a, b, c] = base.participants;
    const round: OurPriceRound = {
      ...base,
      participants: [
        { ...a, per_sqm: { net: "100.004", shown: "122.01" } },
        { ...b, state: "ok", estimate_id: 9002, per_sqm: { net: "100.001", shown: "122.00" }, totals: a.totals },
        c,
      ],
    };
    server.use(http.get(ROUND_PATH, () => HttpResponse.json(round)));
    renderPage();
    await loaded();
    expect(screen.getByTestId("card-7001")).toHaveTextContent("100,00");
    expect(screen.getByTestId("card-7002")).toHaveTextContent("100,00");
    expect(screen.getByTestId("card-7002")).toHaveTextContent("дешевле всех на м²");
    expect(screen.getByTestId("card-7001")).not.toHaveTextContent("дешевле всех на м²");
  });
});

describe("НДС", () => {
  it("?vat=shown переключает показ без нового запроса и без серого слоя", async () => {
    renderPage();
    await loaded();
    expect(roundRequests()).toHaveLength(1);
    const cell = screen.getByTestId("cell-11-7001");
    expect(cell).toHaveTextContent("110 000,00");

    fireEvent.click(screen.getByRole("switch", { name: VAT_SWITCH_LABEL }));
    await waitFor(() => expect(screen.getByTestId("cell-11-7001")).toHaveTextContent("134 200,00"));
    expect(screen.getByTestId("loc")).toHaveTextContent("vat=shown");
    expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument();
    expect(roundRequests()).toHaveLength(1);

    fireEvent.click(screen.getByRole("switch", { name: VAT_SWITCH_LABEL }));
    await waitFor(() => expect(screen.getByTestId("cell-11-7001")).toHaveTextContent("110 000,00"));
    expect(screen.getByTestId("loc")).not.toHaveTextContent("vat=");
    expect(roundRequests()).toHaveLength(1);
  });

  it("заход с ?vat=shown: подписи ставок — у каждого своя, у общей колонки — нетто с причиной", async () => {
    renderPage("?vat=shown");
    await loaded();
    expect(screen.getByRole("switch", { name: VAT_SWITCH_LABEL })).toBeChecked();
    expect(screen.getByTestId("round-header")).toHaveTextContent(PRICES_CAPTION_SHOWN_MIXED);
    expect(screen.getByTestId("card-7001")).toHaveTextContent("с НДС 22 %");
    expect(screen.getByTestId("col-7001")).toHaveTextContent("с НДС 22 %");
    expect(screen.getByTestId("fork-head")).toHaveTextContent("без НДС — ставки участников разные");
    expect(screen.getByTestId("fork-11")).toHaveTextContent("90 000,00");
  });

  it("одна ставка у всех участников: шапка пишет «цены с НДС 22 %» (ставка «22.00» — без хвоста), общая колонка — в shown", async () => {
    const base = ourPriceRoundFixture();
    const round: OurPriceRound = { ...base, history: { ...base.history, shown_rate: "22.00" } };
    server.use(http.get(ROUND_PATH, () => HttpResponse.json(round)));
    renderPage("?vat=shown");
    await loaded();
    const header = screen.getByTestId("round-header");
    expect(header).toHaveTextContent("цены с НДС 22 %");
    expect(header).not.toHaveTextContent("22.00");
    expect(header).not.toHaveTextContent(PRICES_CAPTION_SHOWN_MIXED);
    expect(screen.getByTestId("fork-head")).toHaveTextContent("с НДС 22 %");
    expect(screen.getByTestId("fork-11")).toHaveTextContent("109 800,00");
  });

  it("неизвестное значение ?vat= — это нетто", async () => {
    renderPage("?vat=gross");
    await loaded();
    expect(screen.getByRole("switch", { name: VAT_SWITCH_LABEL })).not.toBeChecked();
    expect(screen.getByTestId("cell-11-7001")).toHaveTextContent("110 000,00");
  });

  it("переключатель НДС выключен, когда ни у одного участника нет известной базы", async () => {
    const base = ourPriceRoundFixture();
    const round: OurPriceRound = {
      ...base,
      participants: base.participants.map((p) => ({ ...p, vat_rate: null })),
    };
    server.use(http.get(ROUND_PATH, () => HttpResponse.json(round)));
    renderPage();
    await loaded();
    expect(screen.getByRole("switch", { name: VAT_SWITCH_LABEL })).toHaveAttribute("aria-disabled", "true");
  });
});

describe("ИПЦ", () => {
  it("галочка выключена по умолчанию, запрос без ряда", async () => {
    renderPage();
    await loaded();
    expect(screen.getByRole("switch", { name: INFLATION_SWITCH_LABEL })).not.toBeChecked();
    expect(seriesParam(0)).toBeNull();
    expect(screen.getByTestId("round-header")).not.toHaveTextContent(NO_INFLATION_CAPTION);
  });

  it("при включённом ряде подпись шапки говорит, что КП не приводится", async () => {
    renderPage("?inflation=3");
    await loaded();
    const header = screen.getByTestId("round-header");
    await waitFor(() => expect(header).toHaveTextContent(KP_NOT_ADJUSTED_NOTE));
    expect(header).toHaveTextContent("Цены приведены к марту 2026 по ряду «Ряд А»; ");
  });

  it("без ряда пояснения про КП нет", async () => {
    renderPage();
    await loaded();
    expect(screen.getByTestId("round-header")).not.toHaveTextContent(KP_NOT_ADJUSTED_NOTE);
  });

  it("включение ставит в адрес id первого активного ряда по id, а не по порядку списка", async () => {
    const row = (id: number, active: boolean): InflationSeries => ({
      ...sampleInflationSeries[0],
      id,
      name: `Ряд ${id}`,
      is_active: active,
    });
    handlerState.inflationSeries = [row(5, true), row(2, true), row(1, false)];
    renderPage();
    await loaded();
    const toggle = screen.getByRole("switch", { name: INFLATION_SWITCH_LABEL });
    await waitFor(() => expect(toggle).not.toHaveAttribute("aria-disabled", "true"));
    fireEvent.click(toggle);
    await waitFor(() => expect(roundRequests()).toHaveLength(2));
    expect(seriesParam(1)).toBe("2");
    expect(screen.getByTestId("loc")).toHaveTextContent("inflation=2");
    expect(await screen.findByTestId("round-header")).toHaveTextContent("Цены приведены к марту 2026");
  });

  it.each(["abc", "0", "1.5"])("?inflation=%s — не ряд: запрос без ряда, галочка выключена", async (raw) => {
    renderPage(`?inflation=${raw}`);
    await loaded();
    expect(seriesParam(0)).toBeNull();
    expect(screen.getByRole("switch", { name: INFLATION_SWITCH_LABEL })).not.toBeChecked();
  });

  it("нет ни одного активного ряда: галочка недоступна; ряд в адресе — доступна, чтобы её снять", async () => {
    const row = (id: number, active: boolean): InflationSeries => ({
      ...sampleInflationSeries[0],
      id,
      name: `Ряд ${id}`,
      is_active: active,
    });
    handlerState.inflationSeries = [row(3, false)];
    const first = renderPage();
    await loaded();
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: INFLATION_SWITCH_LABEL })).toHaveAttribute("aria-disabled", "true")
    );
    first.unmount();

    renderPage("?inflation=3");
    await loaded();
    const toggle = screen.getByRole("switch", { name: INFLATION_SWITCH_LABEL });
    expect(toggle).toBeChecked();
    expect(toggle).not.toHaveAttribute("aria-disabled", "true");
  });

  it("архивный ряд из адреса остаётся в выборе ряда под своим именем", async () => {
    const archived: InflationSeries = {
      ...sampleInflationSeries[0],
      id: 3,
      name: "Архивный ряд Х",
      is_active: false,
    };
    handlerState.inflationSeries = [{ ...sampleInflationSeries[0], id: 1, name: "Ряд 1", is_active: true }, archived];
    renderPage("?inflation=3");
    await loaded();
    await waitFor(() => expect(screen.getByRole("combobox")).toHaveTextContent("Архивный ряд Х"));
  });

  it("выключение убирает ряд из адреса и возвращает номинал", async () => {
    renderPage("?inflation=1");
    await loaded();
    const toggle = screen.getByRole("switch", { name: INFLATION_SWITCH_LABEL });
    expect(toggle).toBeChecked();
    fireEvent.click(toggle);
    await waitFor(() => expect(screen.getByTestId("loc")).not.toHaveTextContent("inflation="));
    await waitFor(() => expect(screen.getByRole("switch", { name: INFLATION_SWITCH_LABEL })).not.toBeChecked());
  });

  it("смена ряда: серый слой с подписью поверх прежних цифр, пока идёт запрос; затем слой уходит", async () => {
    renderPage();
    await loaded();
    const toggle = screen.getByRole("switch", { name: INFLATION_SWITCH_LABEL });
    await waitFor(() => expect(toggle).not.toHaveAttribute("aria-disabled", "true"));
    const hold = holdRequests("get", ROUND_PATH);
    fireEvent.click(toggle);
    const overlay = await screen.findByTestId("recalc-overlay");
    expect(overlay).toHaveTextContent(RECALC_LABEL);
    expect(screen.getByTestId("cell-11-7001")).toHaveTextContent("110 000,00");
    hold.release();
    await waitFor(() => expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument());
    expect(screen.getByTestId("round-header")).toHaveTextContent("Цены приведены к марту 2026");
  });
});

describe("отказ приведения ИПЦ", () => {
  const MESSAGES = {
    missing_inflation_years: "В ряду нет значений за 2024 год.",
    amendment_date_missing: "У допсоглашения договора 1 нет даты подготовки сметы.",
    inflation_target_missing: "У этапа нет даты проведения",
  } as const;

  it.each(Object.entries(MESSAGES))(
    "%s: номинальные цифры вторым запросом, баннер своей ветки, «цены без приведения», ряд в адресе",
    async (code, message) => {
      handlerState.ourPriceRefusal = code as keyof typeof MESSAGES;
      renderPage("?inflation=3");
      await loaded();
      expect(roundRequests()).toHaveLength(2);
      expect(seriesParam(0)).toBe("3");
      expect(seriesParam(1)).toBeNull();
      expect(screen.getByTestId("cell-11-7001")).toHaveTextContent("110 000,00");
      expect(screen.getByTestId("inflation-refusal")).toHaveTextContent(message);
      expect(screen.getByTestId("inflation-refusal")).not.toHaveTextContent("остались в адресе");
      expect(screen.getByTestId("round-header")).toHaveTextContent(NO_INFLATION_CAPTION);
      expect(screen.getByTestId("loc")).toHaveTextContent("inflation=3");
      expect(screen.getAllByTestId("inflation-refusal")).toHaveLength(1);
    }
  );

  it("кнопка «Заполнить недостающие годы» — только missing_inflation_years и только admin", async () => {
    handlerState.ourPriceRefusal = "missing_inflation_years";
    const admin = renderPage("?inflation=3", { role: "admin" });
    await loaded();
    expect(within(screen.getByTestId("inflation-refusal")).getByRole("button")).toBeInTheDocument();
    admin.unmount();

    handlerState.ourPriceRequests = [];
    renderPage("?inflation=3", { role: "member" });
    await loaded();
    expect(within(screen.getByTestId("inflation-refusal")).queryByRole("button")).not.toBeInTheDocument();
  });

  it.each(["amendment_date_missing", "inflation_target_missing"] as const)(
    "%s: кнопки нет даже у admin",
    async (code) => {
      handlerState.ourPriceRefusal = code;
      renderPage("?inflation=3", { role: "admin" });
      await loaded();
      expect(within(screen.getByTestId("inflation-refusal")).queryByRole("button")).not.toBeInTheDocument();
    }
  );

  it("закрытие диалога ряда перезапрашивает этап с тем же рядом", async () => {
    handlerState.ourPriceRefusal = "missing_inflation_years";
    renderPage("?inflation=3", { role: "admin" });
    await loaded();
    const withSeries = () =>
      roundRequests().filter((r) => new URL(r.url).searchParams.get("inflation_series_id") === "3").length;
    const before = withSeries();
    await userEvent.click(within(screen.getByTestId("inflation-refusal")).getByRole("button"));
    await screen.findByRole("dialog");
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(withSeries()).toBeGreaterThan(before));
  });

  it("номинальный запрос тоже упал — состояние ошибки экрана, а не пустое молчание", async () => {
    handlerState.ourPriceRefusal = "missing_inflation_years";
    server.use(
      http.get(ROUND_PATH, ({ request }) => {
        if (new URL(request.url).searchParams.has("inflation_series_id")) {
          return HttpResponse.json(
            { detail: { code: "missing_inflation_years", message: "В ряду нет значений.", missing_years: [2024] } },
            { status: 422 }
          );
        }
        return HttpResponse.json({ detail: "x" }, { status: 500 });
      })
    );
    renderPage("?inflation=3");
    expect(await screen.findByText(errorLabel(500))).toBeInTheDocument();
    expect(screen.queryByTestId("cell-11-7001")).not.toBeInTheDocument();
  });
});

describe("плашка каталога и опрос", () => {
  it("in_progress 3 → 1 → 0: плашка растёт и гаснет без перезагрузки", async () => {
    handlerState.ourPriceRoundInProgress = [3, 1, 0];
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    renderPage();
    await vi.waitFor(() => expect(screen.getByTestId("catalog-progress")).toHaveTextContent("7 из 10"));
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "70");

    await act(() => vi.advanceTimersByTimeAsync(OUR_PRICE_POLL_MS));
    await vi.waitFor(() => expect(screen.getByTestId("catalog-progress")).toHaveTextContent("9 из 10"));
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "90");

    await act(() => vi.advanceTimersByTimeAsync(OUR_PRICE_POLL_MS));
    await vi.waitFor(() => expect(screen.queryByTestId("catalog-progress")).not.toBeInTheDocument());
    expect(roundRequests()).toHaveLength(3);
    expect(screen.getByTestId("cell-11-7001")).toBeInTheDocument();
  });

  it("запрос опроса идёт без серого слоя: слой — только смена ряда, опрос экран не закрывает", async () => {
    handlerState.ourPriceRoundInProgress = [3, 3];
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    renderPage();
    await vi.waitFor(() => expect(screen.getByTestId("catalog-progress")).toBeInTheDocument());
    const hold = holdRequests("get", ROUND_PATH);
    await act(() => vi.advanceTimersByTimeAsync(OUR_PRICE_POLL_MS));
    await vi.waitFor(() => expect(hold.seen.count).toBe(1));
    expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument();
    expect(screen.getByTestId("cell-11-7001")).toBeInTheDocument();
    hold.release();
    await vi.waitFor(() => expect(roundRequests()).toHaveLength(2));
  });

  it("in_progress = 0: плашки нет", async () => {
    renderPage();
    await loaded();
    expect(screen.queryByTestId("catalog-progress")).not.toBeInTheDocument();
  });
});

describe("маршрут", () => {
  it("вне RequireAdmin: member открывает экран по адресу", async () => {
    server.use(http.get("/api/auth/me", () => HttpResponse.json({ id: 2, email: "m@example.com", role: "member" })));
    window.history.pushState({}, "", ROUTE);
    render(<App />);
    expect(await screen.findByTestId("cell-11-7001", undefined, { timeout: 5000 })).toBeInTheDocument();
    window.history.pushState({}, "", "/");
  });
});

describe("правки по ревью", () => {
  it("после отказа ИПЦ смена ряда — серый слой над номинальными цифрами, а не скелет", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get(ROUND_PATH, async ({ request }) => {
        const series = new URL(request.url).searchParams.get("inflation_series_id");
        if (series === "3") {
          return HttpResponse.json(
            { detail: { code: "missing_inflation_years", message: "m", missing_years: [2024] } },
            { status: 422 }
          );
        }
        if (series === "2") await gate;
        return HttpResponse.json(ourPriceRoundFixture({ withInflation: series !== null }));
      })
    );
    const { container } = renderPage("?inflation=3");
    await screen.findByTestId("inflation-refusal");
    await loaded();
    const trigger = screen.getByRole("combobox");
    await userEvent.click(trigger);
    await userEvent.click(await screen.findByRole("option", { name: /Внутренняя оценка/ }));
    await waitFor(() => expect(screen.queryByTestId("inflation-refusal")).not.toBeInTheDocument());
    expect(screen.getByTestId("recalc-overlay")).toBeInTheDocument();
    expect(screen.getByTestId("cell-11-7001")).toBeInTheDocument();
    expect(container.querySelector(".animate-pulse")).toBeNull();
    release();
    await waitFor(() => expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument());
  });

  it("ряд из адреса не найден (404): подпись называет сообщение сервера, а не этап", async () => {
    server.use(
      http.get(ROUND_PATH, ({ request }) =>
        new URL(request.url).searchParams.has("inflation_series_id")
          ? HttpResponse.json({ detail: "Ряд индексов 999 не найден." }, { status: 404 })
          : HttpResponse.json(ourPriceRoundFixture())
      )
    );
    renderPage("?inflation=999");
    expect(await screen.findByText("Ряд индексов 999 не найден.")).toBeInTheDocument();
    expect(screen.queryByText(errorLabel(404))).not.toBeInTheDocument();
  });

  it("?vat=shown при недоступном переключателе: показ нетто, переключатель снят", async () => {
    const base = ourPriceRoundFixture();
    const round: OurPriceRound = {
      ...base,
      participants: base.participants.map((p) => ({ ...p, vat_rate: null })),
    };
    server.use(http.get(ROUND_PATH, () => HttpResponse.json(round)));
    renderPage("?vat=shown");
    await loaded();
    expect(screen.getByRole("switch", { name: VAT_SWITCH_LABEL })).not.toBeChecked();
    expect(screen.getByTestId("cell-11-7001")).toHaveTextContent("110 000,00");
    expect(screen.getByTestId("round-header")).toHaveTextContent(PRICES_CAPTION_NET);
  });
});

describe("прежние цифры под слоем — только своего этапа (решение 11)", () => {
  function GoTo({ to }: { to: string }) {
    const navigate = useNavigate();
    return (
      <button type="button" data-testid="go" onClick={() => navigate(to)}>
        go
      </button>
    );
  }

  it.each([
    ["другой этап", `/tenders/${OUR_PRICE_TENDER_ID}/rounds/${OUR_PRICE_ROUND_ID + 1}/our-price`],
    ["другой тендер", `/tenders/${OUR_PRICE_TENDER_ID + 1}/rounds/${OUR_PRICE_ROUND_ID}/our-price`],
  ])("переход на %s: скелет, чужих цифр под слоем нет", async (_label, to) => {
    const { container } = renderWithProviders(
      <>
        <Routes>
          <Route path="/tenders/:tenderId/rounds/:roundId/our-price" element={<OurPriceRoundPage />} />
        </Routes>
        <GoTo to={to} />
      </>,
      { initialRoute: ROUTE }
    );
    await loaded();
    const hold = holdRequests("get", ROUND_PATH);
    fireEvent.click(screen.getByTestId("go"));
    await waitFor(() => expect(hold.seen.count).toBe(1));
    expect(screen.queryByTestId("cell-11-7001")).not.toBeInTheDocument();
    expect(screen.queryByTestId("recalc-overlay")).not.toBeInTheDocument();
    expect(container.querySelector(".container-page .animate-pulse")).not.toBeNull();
    hold.release();
  });
});
