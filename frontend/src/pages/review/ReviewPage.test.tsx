import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import ReviewPage from "./ReviewPage";
import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { MAX_REVIEW_BATCH } from "@/types/domain";
import { renderWithProviders } from "@/test/utils";

describe("Экран «Ручной матчинг» (§7.2)", () => {
  it("показывает очередь с весом работы и примерами наименований", async () => {
    renderWithProviders(<ReviewPage />);

    expect(await screen.findByText("Стяжка неведомая")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("в смете: Стяжка пола 50мм")).toBeInTheDocument();
  });

  it("сначала самые тяжёлые работы (§6.5)", async () => {
    renderWithProviders(<ReviewPage />);
    await screen.findByText("Стяжка неведомая");

    const rows = screen.getAllByRole("row").slice(1); // без шапки
    expect(within(rows[0]).getByText("Стяжка неведомая")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Кладка непонятная")).toBeInTheDocument();
  });

  it("сортировка по названию меняет порядок", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ReviewPage />);
    await screen.findByText("Стяжка неведомая");

    await user.click(screen.getByRole("button", { name: "По названию" }));

    await waitFor(() => {
      const rows = screen.getAllByRole("row").slice(1);
      expect(within(rows[0]).getByText("Кладка непонятная")).toBeInTheDocument();
    });
  });

  it("фильтр «без единицы» оставляет только строки без единицы", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ReviewPage />);
    await screen.findByText("Стяжка неведомая");

    await user.click(screen.getByLabelText("Единица измерения"));
    await user.click(await screen.findByRole("option", { name: "Без единицы" }));

    await waitFor(() => {
      expect(screen.queryByText("Стяжка неведомая")).not.toBeInTheDocument();
    });
    expect(await screen.findByText("Кладка непонятная")).toBeInTheDocument();
  });

  it("построчная разметка отправляет kind", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ReviewPage />);
    await screen.findByText("Стяжка неведомая");

    const row = screen.getByText("Стяжка неведомая").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Мусор" }));

    // Успех виден по инвалидации: очередь перезапрошена, экран не упал.
    await waitFor(() => expect(screen.getByText("Стяжка неведомая")).toBeInTheDocument());
  });
});

describe("Пакетная разметка (решение §6.5)", () => {
  it("отправляет выбранные строки одним запросом", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ReviewPage />);
    await screen.findByText("Стяжка неведомая");

    await user.click(screen.getByLabelText("Выбрать «Стяжка неведомая»"));
    await user.click(screen.getByLabelText("Выбрать «Кладка непонятная»"));
    expect(screen.getByText("Выбрано строк: 2")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "В мусор" }));

    await waitFor(() => {
      expect(handlerState.lastBatch).toEqual({ ids: [700, 701], kind: "TRASH" });
    });
  });

  it("«выбрать все» отмечает всю страницу", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ReviewPage />);
    await screen.findByText("Стяжка неведомая");

    await user.click(screen.getByLabelText("Выбрать все строки на странице"));
    expect(screen.getByText("Выбрано строк: 2")).toBeInTheDocument();
  });

  it("после пакета выделение снимается", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ReviewPage />);
    await screen.findByText("Стяжка неведомая");

    await user.click(screen.getByLabelText("Выбрать все строки на странице"));
    await user.click(screen.getByRole("button", { name: "Пометить заголовками" }));

    // Разобранные строки уходят из очереди: оставить выделение значило бы
    // применить следующее действие к строкам, которых на экране уже нет.
    await waitFor(() => {
      expect(screen.queryByText(/Выбрано строк/)).not.toBeInTheDocument();
    });
  });

  it("пропущенные строки не роняют пакет и объясняются отдельно", async () => {
    handlerState.batchSkipsFirst = true;
    const user = userEvent.setup();
    renderWithProviders(<ReviewPage />);
    await screen.findByText("Стяжка неведомая");

    await user.click(screen.getByLabelText("Выбрать все строки на странице"));
    await user.click(screen.getByRole("button", { name: "В мусор" }));

    expect(await screen.findByText("Размечено строк: 1")).toBeInTheDocument();
    expect(await screen.findByText(/Пропущено: 1/)).toBeInTheDocument();
  });
});

describe("Слияние с каталожной работой (§5)", () => {
  it("предзаполняет поиск и сообщает, к скольким позициям применится решение", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ReviewPage />);
    await screen.findByText("Стяжка неведомая");

    const row = screen.getByText("Стяжка неведомая").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Слить" }));

    expect(await screen.findByText(/позиций: 42/)).toBeInTheDocument();
    expect(screen.getByLabelText("Поиск каталожной работы")).toHaveValue("Стяжка неведомая");
  });

  it("сливает с выбранной целью и закрывает диалог", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ReviewPage />);
    await screen.findByText("Стяжка неведомая");

    const row = screen.getByText("Стяжка неведомая").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Слить" }));

    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /Стяжка цементная/ }));
    // Кнопка «Слить» есть и в строке таблицы — берём именно диалоговую.
    await user.click(within(dialog).getByRole("button", { name: "Слить" }));

    await waitFor(() => {
      expect(screen.queryByText(/позиций: 42/)).not.toBeInTheDocument();
    });
  });
});

describe("Права (§3): очередь принадлежит и member", () => {
  it("member видит очередь и действия", async () => {
    renderWithProviders(<ReviewPage />, {
      initialUser: { id: 2, email: "member@example.com", role: "member" },
    });

    expect(await screen.findByText("Стяжка неведомая")).toBeInTheDocument();
    const row = screen.getByText("Стяжка неведомая").closest("tr") as HTMLElement;
    expect(within(row).getByRole("button", { name: "Слить" })).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Это работа" })).toBeInTheDocument();
  });
});

describe("Найдено собственным ревью: потолок пакета", () => {
  /** Очередь из N строк на одной странице — чтобы «выбрать все» дало >200. */
  function queueOf(count: number) {
    const items = Array.from({ length: count }, (_, index) => ({
      id: 1000 + index,
      standard_job_title: `Работа очереди ${index}`,
      normalized_job_title: `работа очередь ${index}`,
      unit_id: null,
      unit_code: null,
      unit_name: null,
      position_count: 1,
      sample_titles: [],
      created_at: null,
    }));
    server.use(
      http.get("/api/v1/review/queue", () =>
        HttpResponse.json({ items, total: items.length, page: 1, page_size: items.length })
      )
    );
  }

  it(
    "выделение больше потолка объясняется и блокирует пакет",
    async () => {
      queueOf(MAX_REVIEW_BATCH + 1);
      const user = userEvent.setup();
      renderWithProviders(<ReviewPage />);
      await screen.findByText("Работа очереди 0");

      await user.click(screen.getByLabelText("Выбрать все строки на странице"));
      expect(screen.getByText(`Выбрано строк: ${MAX_REVIEW_BATCH + 1}`)).toBeInTheDocument();

      // Иначе сервер отвечает 422 от Pydantic текстом «List should have at most
      // 200 items» — по-английски и без подсказки, что делать.
      expect(screen.getByRole("alert")).toHaveTextContent(
        `не больше ${MAX_REVIEW_BATCH} строк`
      );
      expect(screen.getByRole("button", { name: "В мусор" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Утвердить как работы" })).toBeDisabled();
      expect(handlerState.lastBatch).toBeNull();
    },
    // Рендер 200+ строк в jsdom дорог; дефолтных 5 с не хватает.
    30_000
  );

  // Отдельного теста «ровно потолок разрешён» нет намеренно: он стоил бы ещё
  // одного рендера 200 строк (~6 с), а границу и так держат две вещи — строгое
  // сравнение `>` в экране и `max_length=200` на сервере, который проверен
  // тестом бэкенда `test_batch_kind_rejects_empty_and_oversized_batches`.
});

describe("Отказ пакета: выделение сохраняется, отклонение не улетает мимо", () => {
  it("после ошибки сервера выделение остаётся, чтобы повторить пакет", async () => {
    /*
     * Уточнение от внешнего ревью: в отчёте было сказано, что при 422 выделение
     * «уже потеряно» — это неверно. `setSelected` стоит ПОСЛЕ `await
     * mutateAsync`, поэтому отказ его не трогает, и повторить пакет можно.
     *
     * Тест закрепляет именно это. Он же ловит необработанное отклонение промиса:
     * вызов идёт как `void applyBatch(...)`, и без `try/catch` vitest сообщает об
     * unhandled rejection («might cause false positive tests»).
     */
    server.use(
      http.post("/api/v1/review/batch-kind", () =>
        HttpResponse.json({ detail: "Пакет не применён: сбой сервера." }, { status: 500 })
      )
    );

    const user = userEvent.setup();
    renderWithProviders(<ReviewPage />);
    await screen.findByText("Стяжка неведомая");

    await user.click(screen.getByLabelText("Выбрать все строки на странице"));
    expect(screen.getByText("Выбрано строк: 2")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "В мусор" }));

    expect(await screen.findByText("Пакет не применён: сбой сервера.")).toBeInTheDocument();
    expect(screen.getByText("Выбрано строк: 2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "В мусор" })).toBeEnabled();
  });
});
