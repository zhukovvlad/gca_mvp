import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { InflationSeriesTab } from "@/components/standards/InflationSeriesTab";
import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import { sampleInflationSeries } from "@/test/fixtures";

/*
 * Вкладка рядов индексов на экране нормативов (§7.3 в редакции v6.10).
 *
 * Экран целиком под `RequireAdmin` (`App.tsx`), поэтому «кнопок правки нет у
 * `member`» проверяется НЕ здесь, а на `/compare` — там `member` бывает.
 */

async function renderTab() {
  renderWithProviders(<InflationSeriesTab />);
  await waitFor(() => expect(screen.getByText(/Росстат/)).toBeInTheDocument());
}

describe("InflationSeriesTab", () => {
  beforeEach(() => {
    // Состояние хендлеров живое: тесты проверяют ПЕРЕХОДЫ архивации, и без сброса
    // второй тест видел бы результат первого.
    handlerState.inflationSeries = sampleInflationSeries;
    handlerState.lastInflationBody = null;
    handlerState.inflationPatches = 0;
  });

  it("показывает охват годов, примечание и состояние", async () => {
    await renderTab();

    const row = screen.getByText("Росстат, ИПЦ, декабрь к декабрю").closest("tr")!;
    expect(within(row).getByText("2024–2026")).toBeInTheDocument();
    expect(within(row).getByText("официальная публикация, по РФ")).toBeInTheDocument();
    expect(within(row).getByText("активный")).toBeInTheDocument();

    const archived = screen.getByText("Ряд 2024 года, выведен из обращения").closest("tr")!;
    // Охват из одного года печатается одним числом, а не «2024–2024».
    expect(within(archived).getByText("2024")).toBeInTheDocument();
    expect(within(archived).getByText("в архиве")).toBeInTheDocument();
  });

  it("пустой справочник доводит до создания ряда, и ряд появляется в списке", async () => {
    /*
     * Это состояние СРАЗУ ПОСЛЕ МИГРАЦИИ (§2.6): справочник создаётся пустым, и
     * первый ряд заводит человек. Если путь отсюда не проходим, фича не заводится
     * вовсе — поэтому тест идёт до конца, до строки в таблице.
     */
    handlerState.inflationSeries = [];
    renderWithProviders(<InflationSeriesTab />);

    await waitFor(() => expect(screen.getByText(/Рядов индексов пока нет/)).toBeInTheDocument());
    await userEvent.click(
      within(screen.getByText(/Рядов индексов пока нет/).closest("div")!).getByRole("button", {
        name: "Создать ряд",
      })
    );

    await userEvent.type(screen.getByLabelText("Название"), "Росстат, ИПЦ, декабрь к декабрю");
    await userEvent.type(screen.getByLabelText("Примечание"), "официальная публикация");
    await userEvent.click(screen.getByRole("button", { name: "Добавить год" }));
    await userEvent.type(screen.getByLabelText("Коэффициент за 2024"), "1.075");
    await userEvent.type(screen.getByLabelText("Источник за 2024"), "бюллетень 01.2025");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(screen.getByText("Росстат, ИПЦ, декабрь к декабрю")).toBeInTheDocument()
    );
    expect(screen.queryByText(/Рядов индексов пока нет/)).not.toBeInTheDocument();
  });

  it("упавший запрос списка — НЕ пустой справочник и НЕ приглашение создать ряд", async () => {
    /*
     * Третий круг внешнего ревью, находка сверх двух названных. `series.data ?? []`
     * уводил отказ в ту же ветку, что пустоту, и экран УТВЕРЖДАЛ «Рядов индексов
     * пока нет» при живом справочнике — то есть говорил неправду и звал завести
     * первый ряд. Кончилось бы это дубликатом либо `409` по занятому названию.
     *
     * Парный к тесту пустого справочника выше: он проверяет, что настоящая пустота
     * по-прежнему доводит до создания, — иначе эта правка спрятала бы состояние
     * сразу после миграции (§2.6).
     */
    server.use(
      http.get("/api/v1/inflation-series", () => new HttpResponse(null, { status: 500 }))
    );
    renderWithProviders(<InflationSeriesTab />);

    await waitFor(() =>
      expect(screen.getByText(/Не удалось загрузить ряды индексов/)).toBeInTheDocument()
    );
    expect(screen.queryByText(/Рядов индексов пока нет/)).not.toBeInTheDocument();
    // Кнопки создания нет ни одной: создание — единственное опасное действие здесь.
    expect(screen.queryByRole("button", { name: "Создать ряд" })).not.toBeInTheDocument();
  });

  it("«Изменить» открывает ТОТ ЖЕ InflationSeriesDialog", async () => {
    await renderTab();

    const row = screen.getByText("Росстат, ИПЦ, декабрь к декабрю").closest("tr")!;
    await userEvent.click(within(row).getByRole("button", { name: "Изменить" }));

    // Признак того самого компонента, а не похожей формы: заголовок, подпись про
    // общий ряд и живая расшифровка коэффициента — всё из него.
    await waitFor(() => expect(screen.getByText("Изменить ряд индексов")).toBeInTheDocument());
    expect(screen.getByText(/Подкручивать индекс под свою выборку нельзя/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("level-2024")).toHaveTextContent("Рост 7,5 %"));
    expect(document.querySelectorAll("[role=dialog]")).toHaveLength(1);
  });

  it("архивация и возврат — ДВЕ разные кнопки, по ОДНОМУ запросу каждая", async () => {
    /*
     * Совмещать возврат с правкой нельзя: сервер ответит `409` (§2.10), и правило
     * проверяемо только двумя шагами. Поэтому тест смотрит и на ТЕЛО запроса: в
     * нём обязано быть ТОЛЬКО `is_active`.
     */
    await renderTab();

    const active = screen.getByText("Внутренняя оценка ПЭО").closest("tr")!;
    await userEvent.click(within(active).getByRole("button", { name: "В архив" }));

    await waitFor(() => expect(handlerState.lastInflationBody).toEqual({ is_active: false }));
    await waitFor(() =>
      expect(
        within(screen.getByText("Внутренняя оценка ПЭО").closest("tr")!).getByText("в архиве")
      ).toBeInTheDocument()
    );

    const archived = screen.getByText("Внутренняя оценка ПЭО").closest("tr")!;
    await userEvent.click(within(archived).getByRole("button", { name: "Вернуть в активные" }));

    await waitFor(() => expect(handlerState.lastInflationBody).toEqual({ is_active: true }));
    await waitFor(() =>
      expect(
        within(screen.getByText("Внутренняя оценка ПЭО").closest("tr")!).getByText("активный")
      ).toBeInTheDocument()
    );
    // Два шага — два запроса, а не один совмещённый.
    expect(handlerState.inflationPatches).toBe(2);
  });

  it("у архивного ряда «Изменить» выключено", async () => {
    await renderTab();

    const archived = screen.getByText("Ряд 2024 года, выведен из обращения").closest("tr")!;
    expect(within(archived).getByRole("button", { name: "Изменить" })).toBeDisabled();
    expect(
      within(archived).getByRole("button", { name: "Вернуть в активные" })
    ).toBeEnabled();
  });

  it("архивный ряд виден в списке — иначе архивация была бы необратимой", async () => {
    /*
     * Вкладка запрашивает список С архивными намеренно. В селектор выбора на
     * `/compare` они не попадают: там запрос идёт без `include_archived` (§2.10).
     */
    let askedIncludeArchived: string | null = null;
    server.use(
      http.get("/api/v1/inflation-series", ({ request }) => {
        askedIncludeArchived = new URL(request.url).searchParams.get("include_archived");
        return HttpResponse.json(handlerState.inflationSeries);
      })
    );

    await renderTab();

    expect(askedIncludeArchived).toBe("1");
    expect(screen.getByText("Ряд 2024 года, выведен из обращения")).toBeInTheDocument();
  });
});
