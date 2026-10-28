import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { handlerState } from "@/test/handlers";
import { holdRequests } from "@/test/hold";
import {
  KP_FILE_FILENAME,
  kpFilePreviewFixture,
  OUR_PRICE_LINE_ID,
  OUR_PRICE_OFFER_ID,
  OUR_PRICE_ROUND_ID,
  OUR_PRICE_TENDER_ID,
} from "@/test/fixtures";
import { server } from "@/test/server";
import { qk } from "@/services/queryKeys";
import { createTestQueryClient, renderWithProviders, spyOnDownload } from "@/test/utils";

import KpFilePage from "./KpFilePage";
import { KP_FILE_DOWNLOAD_LABEL, KP_FILE_SENT_TITLE, KP_FILE_TITLE } from "./ourPriceCopy";

const BASE = "/api/v1/tenders/:id/rounds/:roundId/our-price/offers/:offerId";
const PREVIEW_PATH = `${BASE}/export-preview`;
const COMMENT_PATH = `${BASE}/lines/:positionItemId/comment`;
const ROUTE = `/tenders/${OUR_PRICE_TENDER_ID}/rounds/${OUR_PRICE_ROUND_ID}/our-price/offers/${OUR_PRICE_OFFER_ID}/file`;

function renderPage(search = "", role: "admin" | "member" = "admin", queryClient = createTestQueryClient()) {
  return renderWithProviders(
    <Routes>
      <Route
        path="/tenders/:tenderId/rounds/:roundId/our-price/offers/:offerId/file"
        element={<KpFilePage />}
      />
    </Routes>,
    { initialRoute: ROUTE + search, initialUser: { id: 1, email: "u@example.com", role }, queryClient }
  );
}

const ofKind = (kind: string) => handlerState.kpFileRequests.filter((r) => r.kind === kind);
const seriesOf = (url: string) => new URL(url).searchParams.get("inflation_series_id");
const downloadButton = () => screen.getByRole("button", { name: KP_FILE_DOWNLOAD_LABEL });

async function loaded() {
  await screen.findByTestId("kp-file-table");
}

function refuseSeries() {
  server.use(
    http.get(PREVIEW_PATH, ({ request }) => {
      handlerState.kpFileRequests.push({ kind: "preview", method: "GET", url: request.url, body: null });
      if (new URL(request.url).searchParams.has("inflation_series_id")) {
        return HttpResponse.json(
          { detail: { code: "missing_inflation_years", message: "В ряду нет значений за 2024 год.", missing_years: [2024] } },
          { status: 422 }
        );
      }
      return HttpResponse.json(kpFilePreviewFixture({ withInflation: false }));
    })
  );
}

afterEach(() => {
  handlerState.kpFileExportOutcome = "ok";
});

describe("KpFilePage: загрузка и вид", () => {
  it("корень страницы несёт container-page; заголовок, таблица и список отправленных", async () => {
    const { container } = renderPage();
    await loaded();
    expect(container.querySelector(".container-page")).not.toBeNull();
    expect(screen.getByRole("heading", { level: 1, name: KP_FILE_TITLE })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: KP_FILE_SENT_TITLE })).toBeInTheDocument();
    expect(await screen.findByTestId("sent-file-12")).toBeInTheDocument();
  });

  it("без ряда в адресе: запрос без inflation_series_id, столбца ИПЦ нет", async () => {
    renderPage();
    await loaded();
    expect(ofKind("preview")).toHaveLength(1);
    expect(seriesOf(ofKind("preview")[0].url)).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Приведение по ИПЦ" })).not.toBeInTheDocument();
  });

  it("?inflation=5: запрос несёт ряд, столбец ИПЦ есть", async () => {
    renderPage("?inflation=5");
    await loaded();
    expect(seriesOf(ofKind("preview")[0].url)).toBe("5");
    expect(screen.getByRole("columnheader", { name: "Приведение по ИПЦ" })).toBeInTheDocument();
  });

  it("плашка «каталог ещё разбирает N строк» при in_progress_lines > 0 и без неё при нуле", async () => {
    const first = renderPage();
    await loaded();
    expect(screen.getByTestId("kp-file-in-progress")).toHaveTextContent(
      "Каталог ещё разбирает 1 строку — в файле у них не будет наших цен"
    );
    first.unmount();

    const withInProgress = (n: number) =>
      server.use(
        http.get(PREVIEW_PATH, () => {
          const preview = kpFilePreviewFixture({ withInflation: false });
          preview.header.in_progress_lines = n;
          return HttpResponse.json(preview);
        })
      );
    withInProgress(5);
    const second = renderPage();
    await loaded();
    expect(screen.getByTestId("kp-file-in-progress")).toHaveTextContent("разбирает 5 строк —");
    second.unmount();

    withInProgress(0);
    renderPage();
    await loaded();
    expect(screen.queryByTestId("kp-file-in-progress")).not.toBeInTheDocument();
  });

  it("404 предпросмотра — состояние ошибки со ссылкой назад, а не пустая таблица", async () => {
    server.use(http.get(PREVIEW_PATH, () => HttpResponse.json({ detail: "нет" }, { status: 404 })));
    renderPage();
    expect(await screen.findByTestId("kp-file-error")).toBeInTheDocument();
    expect(screen.queryByTestId("kp-file-table")).not.toBeInTheDocument();
  });
});

describe("KpFilePage: отказ ИПЦ", () => {
  it("второй запрос без ряда, баннер своей ветки, таблица на номинальных цифрах", async () => {
    refuseSeries();
    renderPage("?inflation=5");
    await loaded();
    expect(await screen.findByTestId("inflation-refusal")).toHaveTextContent("В ряду нет значений за 2024 год.");
    expect(ofKind("preview").map((r) => seriesOf(r.url))).toEqual(["5", null]);
    expect(screen.queryByRole("columnheader", { name: "Приведение по ИПЦ" })).not.toBeInTheDocument();
  });

  it("выгрузка при отказе уходит без ряда: так, как показан предпросмотр", async () => {
    refuseSeries();
    const download = spyOnDownload();
    try {
      const user = userEvent.setup();
      renderPage("?inflation=5");
      await loaded();
      await waitFor(() => expect(downloadButton()).toBeEnabled());
      await user.click(downloadButton());
      await waitFor(() => expect(ofKind("export")).toHaveLength(1));
      expect(seriesOf(ofKind("export")[0].url)).toBeNull();
    } finally {
      download.restore();
    }
  });
});

describe("KpFilePage: выгрузка", () => {
  it("«Скачать xlsx» уходит с рядом показанного предпросмотра, имя файла — из шапки предпросмотра", async () => {
    const download = spyOnDownload();
    try {
      const user = userEvent.setup();
      renderPage("?inflation=5");
      await loaded();
      await waitFor(() => expect(downloadButton()).toBeEnabled());
      await user.click(downloadButton());
      await waitFor(() => expect(ofKind("export")).toHaveLength(1));
      expect(seriesOf(ofKind("export")[0].url)).toBe("5");
      await waitFor(() => expect(download.lastFilename()).toBe(KP_FILE_FILENAME));
    } finally {
      download.restore();
    }
  });

  it("без ряда выгрузка идёт без inflation_series_id; после успеха список отправленных перезапрошен", async () => {
    const download = spyOnDownload();
    try {
      const user = userEvent.setup();
      renderPage();
      await loaded();
      await screen.findByTestId("sent-file-12");
      const before = ofKind("sent").length;
      await waitFor(() => expect(downloadButton()).toBeEnabled());
      await user.click(downloadButton());
      await waitFor(() => expect(ofKind("export")).toHaveLength(1));
      expect(seriesOf(ofKind("export")[0].url)).toBeNull();
      await waitFor(() => expect(ofKind("sent").length).toBe(before + 1));
    } finally {
      download.restore();
    }
  });

  it("отказ выгрузки (неизвестная база НДС) — текст сервера тостом", async () => {
    handlerState.kpFileExportOutcome = "unknown_vat_base";
    const user = userEvent.setup();
    renderPage();
    await loaded();
    await waitFor(() => expect(downloadButton()).toBeEnabled());
    await user.click(downloadButton());
    expect(await screen.findByText(/У КП неизвестна база НДС/)).toBeInTheDocument();
  });
});

describe("KpFilePage: права", () => {
  it("member: таблица, выгрузка и скачивание есть; ни одного textbox и кнопки правки", async () => {
    const download = spyOnDownload();
    try {
      const user = userEvent.setup();
      renderPage("", "member");
      await loaded();
      expect(screen.queryAllByRole("textbox")).toHaveLength(0);
      expect(screen.queryAllByRole("button", { name: /править комментарий/i })).toHaveLength(0);
      await waitFor(() => expect(downloadButton()).toBeEnabled());
      await user.click(downloadButton());
      await waitFor(() => expect(ofKind("export")).toHaveLength(1));
      await user.click(within(await screen.findByTestId("sent-file-12")).getByRole("button", { name: /скачать/i }));
      await waitFor(() => expect(ofKind("download")).toHaveLength(1));
    } finally {
      download.restore();
    }
  });

  it("admin: у строк есть кнопка правки комментария", async () => {
    renderPage();
    await loaded();
    expect(screen.getAllByRole("button", { name: /править комментарий/i })).toHaveLength(2);
  });
});

describe("KpFilePage: правка комментария и «Скачать xlsx»", () => {
  it("перезапрос предпросмотра без мутации (isFetching) тоже гасит кнопку", async () => {
    const queryClient = createTestQueryClient();
    renderPage("", "admin", queryClient);
    await loaded();
    await waitFor(() => expect(downloadButton()).toBeEnabled());
    const hold = holdRequests("get", PREVIEW_PATH);

    void queryClient.invalidateQueries({
      queryKey: qk.tenders.kpFilePreviewOfOffer(OUR_PRICE_TENDER_ID, OUR_PRICE_ROUND_ID, OUR_PRICE_OFFER_ID),
    });
    await waitFor(() => expect(downloadButton()).toBeDisabled());

    hold.release();
    await waitFor(() => expect(downloadButton()).toBeEnabled());
  });

  it("пока идёт мутация, кнопка неактивна", async () => {
    const user = userEvent.setup();
    renderPage();
    await loaded();
    await waitFor(() => expect(downloadButton()).toBeEnabled());
    const hold = holdRequests("put", COMMENT_PATH);

    await user.click(screen.getByTestId(`comment-edit-${OUR_PRICE_LINE_ID}`));
    await user.type(screen.getByRole("textbox"), "!");
    await user.click(screen.getByTestId(`comment-save-${OUR_PRICE_LINE_ID}`));
    await waitFor(() => expect(hold.seen.count).toBe(1));
    expect(downloadButton()).toBeDisabled();

    hold.release();
    await waitFor(() => expect(downloadButton()).toBeEnabled());
  });

  it("правка → кнопка неактивна, пока предпросмотр перезапрашивается → новый текст в ячейке и кнопка активна", async () => {
    const user = userEvent.setup();
    renderPage();
    await loaded();
    await waitFor(() => expect(downloadButton()).toBeEnabled());
    expect(screen.getByTestId(`cell-${OUR_PRICE_LINE_ID}-comment`)).toHaveTextContent("Свой комментарий");

    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get(PREVIEW_PATH, async () => {
        await gate;
        const preview = kpFilePreviewFixture({ withInflation: false });
        const row = preview.rows.find((r) => r.kind === "line" && r.position_item_id === OUR_PRICE_LINE_ID);
        if (row?.kind === "line") row.comment = "Новый текст";
        return HttpResponse.json(preview);
      })
    );

    await user.click(screen.getByTestId(`comment-edit-${OUR_PRICE_LINE_ID}`));
    const field = screen.getByRole("textbox");
    await user.clear(field);
    await user.type(field, "Новый текст");
    await user.click(screen.getByTestId(`comment-save-${OUR_PRICE_LINE_ID}`));

    // PUT прошёл, перезапрос предпросмотра висит: кнопка неактивна, а поле ещё держит набранное.
    await waitFor(() => expect(ofKind("decision")).toHaveLength(1));
    await waitFor(() => expect(downloadButton()).toBeDisabled());
    expect(screen.getByRole("textbox")).toHaveValue("Новый текст");

    release();
    await waitFor(() => expect(screen.queryByRole("textbox")).not.toBeInTheDocument());
    expect(screen.getByTestId(`cell-${OUR_PRICE_LINE_ID}-comment`)).toHaveTextContent("Новый текст");
    await waitFor(() => expect(downloadButton()).toBeEnabled());
  });
});
