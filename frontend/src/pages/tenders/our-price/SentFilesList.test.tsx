import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";

import { handlerState } from "@/test/handlers";
import {
  KP_FILE_FILENAME,
  OUR_PRICE_OFFER_ID,
  OUR_PRICE_ROUND_ID,
  OUR_PRICE_TENDER_ID,
} from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders, spyOnDownload } from "@/test/utils";

import { KP_FILE_SENT_EMPTY, KP_FILE_SENT_TITLE } from "./ourPriceCopy";
import { SentFilesList } from "./SentFilesList";

const SENT_PATH = "/api/v1/tenders/:id/rounds/:roundId/our-price/offers/:offerId/sent-files";

function renderList() {
  return renderWithProviders(
    <SentFilesList tenderId={OUR_PRICE_TENDER_ID} roundId={OUR_PRICE_ROUND_ID} offerId={OUR_PRICE_OFFER_ID} />
  );
}

const downloads = () => handlerState.kpFileRequests.filter((r) => r.kind === "download");

afterEach(() => {
  handlerState.kpFileDownloadOutcome = "ok";
});

describe("SentFilesList", () => {
  it("строки в порядке ответа сервера: дата, кто, состав НДС и уровень", async () => {
    renderList();
    expect(await screen.findByRole("heading", { name: KP_FILE_SENT_TITLE })).toBeInTheDocument();
    const rows = await screen.findAllByTestId(/^sent-file-/);
    expect(rows.map((row) => row.getAttribute("data-testid"))).toEqual(["sent-file-12", "sent-file-11"]);
    expect(rows[0]).toHaveTextContent("15.03.2026");
    expect(rows[0]).toHaveTextContent("admin@example.test");
    expect(rows[0]).toHaveTextContent("с НДС 22 %");
    expect(rows[0]).toHaveTextContent("приведено по ИПЦ: Ряд А, к 03.2026; КП не приводится");
    expect(rows[1]).toHaveTextContent("12.03.2026");
    expect(rows[1]).toHaveTextContent("member@example.test");
    expect(rows[1]).toHaveTextContent("номинальные цены договоров");
  });

  it("порядок не пересортировывается: сервер прислал старые выше новых — так и показано", async () => {
    server.use(
      http.get(SENT_PATH, () =>
        HttpResponse.json([
          { id: 1, filename: "a.xlsx", created_at: "2026-01-01T10:00:00+00:00", created_by: { id: 3, email: "a@example.test" }, vat_rate: "22", level_label: "x", size_bytes: 1 },
          { id: 2, filename: "b.xlsx", created_at: "2026-02-01T10:00:00+00:00", created_by: { id: 3, email: "a@example.test" }, vat_rate: "22", level_label: "x", size_bytes: 1 },
        ])
      )
    );
    renderList();
    const rows = await screen.findAllByTestId(/^sent-file-/);
    expect(rows.map((row) => row.getAttribute("data-testid"))).toEqual(["sent-file-1", "sent-file-2"]);
  });

  it("пустой список — текст «файлов ещё не отправляли»", async () => {
    server.use(http.get(SENT_PATH, () => HttpResponse.json([])));
    renderList();
    expect(await screen.findByText(KP_FILE_SENT_EMPTY)).toBeInTheDocument();
    expect(screen.queryAllByTestId(/^sent-file-/)).toHaveLength(0);
  });

  it("ошибка загрузки списка — текст ошибки, а не «файлов ещё не отправляли»", async () => {
    server.use(http.get(SENT_PATH, () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    renderList();
    expect(await screen.findByTestId("sent-files-error")).toBeInTheDocument();
    expect(screen.queryByText(KP_FILE_SENT_EMPTY)).not.toBeInTheDocument();
  });

  it("«скачать»: запрос нужного файла и имя файла из записи", async () => {
    const download = spyOnDownload();
    try {
      const user = userEvent.setup();
      renderList();
      const row = await screen.findByTestId("sent-file-12");
      await user.click(within(row).getByRole("button", { name: /скачать/i }));
      await waitFor(() => expect(downloads()).toHaveLength(1));
      expect(new URL(downloads()[0].url).pathname).toBe(
        `/api/v1/tenders/${OUR_PRICE_TENDER_ID}/rounds/${OUR_PRICE_ROUND_ID}/our-price/offers/${OUR_PRICE_OFFER_ID}/sent-files/12/file`
      );
      await waitFor(() => expect(download.lastFilename()).toBe(KP_FILE_FILENAME));
    } finally {
      download.restore();
    }
  });

  it("410: файл пропал из хранилища — текст про выгрузку заново, 404 — другой текст", async () => {
    const user = userEvent.setup();
    renderList();
    handlerState.kpFileDownloadOutcome = "gone";
    await user.click(within(await screen.findByTestId("sent-file-12")).getByRole("button", { name: /скачать/i }));
    expect(await screen.findByText(/Выгрузите файл заново/)).toBeInTheDocument();

    handlerState.kpFileDownloadOutcome = "missing";
    await user.click(within(screen.getByTestId("sent-file-11")).getByRole("button", { name: /скачать/i }));
    expect(await screen.findByText("Отправленный файл не найден.")).toBeInTheDocument();
  });
});
