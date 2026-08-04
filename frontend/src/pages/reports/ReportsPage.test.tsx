import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import ReportsPage from "./ReportsPage";
import { sampleContracts } from "@/test/fixtures";
import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";

/**
 * Экран отчётов (§7.6).
 *
 * Содержимое файла проверяет backend (`test_reports_api.py` читает его через
 * `load_workbook`). Здесь под контролем то, за что отвечает экран: что запрос уходит
 * с нужными параметрами, что до отказа сервера доходит человек, и что два отчёта
 * остаются двумя (решение §6.6) — общей кнопки «скачать всё» тут быть не должно.
 *
 * Скачивание blob как таковое не проверяется: `URL.createObjectURL` в jsdom нет, и
 * `saveBlob` это учитывает. Проверяется факт и состав запроса.
 */

function renderReports() {
  return renderWithProviders(<ReportsPage />, { initialRoute: "/reports" });
}

async function pickFirstContract(user: ReturnType<typeof userEvent.setup>) {
  const trigger = screen.getByLabelText("Договор");
  await user.click(trigger);
  const option = await screen.findByRole("option", {
    name: new RegExp(sampleContracts[0].contract_number),
  });
  await user.click(option);
}

describe("Экран отчётов", () => {
  it("предлагает два отчёта, а не один общий", async () => {
    renderReports();

    // §6.6: два файла с разным охватом. Один «скачать всё» пришлось бы спрашивать,
    // какой договор, у отчёта, у которого договора нет.
    expect(await screen.findByText("Свод расценок по договору")).toBeInTheDocument();
    expect(screen.getByText("Сравнение с нормативами (для банка)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Скачать свод/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Скачать отчёт/ })).toBeInTheDocument();
  });

  it("свод нельзя скачать, пока договор не выбран", async () => {
    renderReports();
    await screen.findByText("Свод расценок по договору");

    // Иначе запрос ушёл бы без contract_id и вернул ошибку валидации — вместо
    // очевидного «сначала выберите договор».
    expect(screen.getByRole("button", { name: /Скачать свод/ })).toBeDisabled();
  });

  it("свод уходит с выбранным договором", async () => {
    const user = userEvent.setup();
    renderReports();
    await screen.findByText("Свод расценок по договору");

    await pickFirstContract(user);
    await user.click(screen.getByRole("button", { name: /Скачать свод/ }));

    await waitFor(() => {
      expect(handlerState.lastReportRequest?.report).toBe("contract-summary");
    });
    expect(handlerState.lastReportRequest?.params.contract_id).toBe(
      String(sampleContracts[0].id)
    );
  });

  it("отчёт «для банка» доступен без выбора договора", async () => {
    const user = userEvent.setup();
    renderReports();
    await screen.findByText("Сравнение с нормативами (для банка)");

    // У него нет договора вовсе — это выборка. Требование выбрать договор здесь
    // означало бы, что два отчёта склеили в один.
    await user.click(screen.getByRole("button", { name: /Скачать отчёт/ }));

    await waitFor(() => {
      expect(handlerState.lastReportRequest?.report).toBe("bank-comparison");
    });
    // Без фильтров — пустая выборка параметров, а не «все» строкой.
    expect(handlerState.lastReportRequest?.params).toEqual({});
  });

  it("фильтры периода и класса доезжают до сервера", async () => {
    const user = userEvent.setup();
    renderReports();
    await screen.findByText("Сравнение с нормативами (для банка)");

    await user.type(screen.getByLabelText("Смета с"), "2026-01-01");
    await user.type(screen.getByLabelText("Смета по"), "2026-12-31");
    await user.click(screen.getByRole("button", { name: /Скачать отчёт/ }));

    await waitFor(() => {
      expect(handlerState.lastReportRequest?.params).toMatchObject({
        date_from: "2026-01-01",
        date_to: "2026-12-31",
      });
    });
  });

  it("причина отказа доходит до человека ТЕКСТОМ СЕРВЕРА, а не по-английски", async () => {
    /*
      **Найдено при написании этого теста.** `responseType: "blob"` меняет форму тела:
      при отказе `axios` отдаёт JSON сервера тоже блобом, и `apiErrorDetail` из него
      ничего не достаёт. Человек видел «Request failed with status code 500» — тогда
      как сервер объяснил причину. Теперь блоб разбирается (`reportErrorMessage`).
    */
    server.use(
      http.get("/api/v1/reports/bank-comparison", () =>
        HttpResponse.json({ detail: "Не удалось построить отчёт: класс объектов удалён." }, { status: 500 })
      )
    );
    const user = userEvent.setup();
    renderReports();
    await screen.findByText("Сравнение с нормативами (для банка)");

    await user.click(screen.getByRole("button", { name: /Скачать отчёт/ }));

    // Тост — единственный канал сообщений об отказах (§7 брифинга).
    expect(
      await screen.findByText(/Не удалось построить отчёт: класс объектов удалён/)
    ).toBeInTheDocument();
    // И это НЕ англоязычная заглушка axios.
    expect(screen.queryByText(/Request failed with status code/)).not.toBeInTheDocument();
  });

  it("отказ без разбираемого тела всё равно объясняется по-русски", async () => {
    // Тело не JSON (например прокси вернул HTML) — общее сообщение лучше молчания:
    // молчание после нажатия «Скачать» неотличимо от «файл вот-вот появится».
    server.use(
      http.get("/api/v1/reports/bank-comparison", () =>
        new HttpResponse("<html>502</html>", { status: 502 })
      )
    );
    const user = userEvent.setup();
    renderReports();
    await screen.findByText("Сравнение с нормативами (для банка)");

    await user.click(screen.getByRole("button", { name: /Скачать отчёт/ }));

    expect(await screen.findByText(/Не удалось построить файл отчёта/)).toBeInTheDocument();
  });

  it("объясняет, по какой дате отбирается период", async () => {
    renderReports();
    // Та же подпись, что в матрице: без неё человек ждал бы отбора по дате договора.
    expect(
      await screen.findByText(/Период — по дате сметы \(при её отсутствии по дате договора\)/)
    ).toBeInTheDocument();
  });

  it("говорит, что позиции без норматива не входят в отклонение", async () => {
    renderReports();
    // Это прямое требование согласованного макета §6.1, и человек должен знать о нём
    // до того, как отправит файл в банк.
    expect(
      await screen.findByText(/Позиции без норматива в отклонение не входят/)
    ).toBeInTheDocument();
  });
});
