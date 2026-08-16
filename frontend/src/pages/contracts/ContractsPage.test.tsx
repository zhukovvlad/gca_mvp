import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import ContractsPage from "./ContractsPage";
import { renderWithProviders } from "@/test/utils";
import { server } from "@/test/server";

describe("Экран «Договоры» (§7.1)", () => {
  it("показывает договоры с объектом, подрядчиком и классом", async () => {
    renderWithProviders(<ContractsPage />);

    expect(await screen.findByText("ГП-2026-001")).toBeInTheDocument();
    expect(screen.getByText("ЖК Северный")).toBeInTheDocument();
    expect(screen.getByText("ООО СтройПодряд")).toBeInTheDocument();
    expect(screen.getAllByText("Жилые дома").length).toBeGreaterThan(0);
  });

  it("сумму показывает строкой без потери разрядов", async () => {
    renderWithProviders(<ContractsPage />);
    await screen.findByText("ГП-2026-001");

    // 1234567890.12 через double уехало бы в последнем разряде; проверяем именно
    // отформатированную строку (§3, деньги строками).
    expect(screen.getByText(/1\s234\s567\s890,12/)).toBeInTheDocument();
  });

  it("пустая сумма — прочерк, а не ноль", async () => {
    renderWithProviders(<ContractsPage />);
    await screen.findByText("ГП-2026-002");

    const row = screen.getByText("ГП-2026-002").closest("tr");
    expect(row).not.toBeNull();
    expect(row).toHaveTextContent("—");
  });

  it("ищет договор по названию объекта", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContractsPage />);
    await screen.findByText("ГП-2026-001");

    await user.type(screen.getByLabelText("Поиск договоров"), "Южный");

    // Сначала ждём исчезновения непопадающего договора: только это и означает,
    // что отфильтрованный ответ доехал. Обратный порядок ничего не доказывал бы —
    // ГП-2026-002 есть и в неотфильтрованном списке.
    await waitFor(() => {
      expect(screen.queryByText("ГП-2026-001")).not.toBeInTheDocument();
    });
    // findByText, а не getByText: в момент проверки выше список мог быть пуст.
    expect(await screen.findByText("ГП-2026-002")).toBeInTheDocument();
  });

  it("admin видит кнопку заведения договора", async () => {
    renderWithProviders(<ContractsPage />);
    expect(await screen.findByRole("button", { name: /Новый договор/ })).toBeInTheDocument();
  });

  it("member кнопки заведения не видит (§6.2)", async () => {
    renderWithProviders(<ContractsPage />, {
      initialUser: { id: 2, email: "member@example.com", role: "member" },
    });
    await screen.findByText("ГП-2026-001");

    expect(screen.queryByRole("button", { name: /Новый договор/ })).not.toBeInTheDocument();
    // И объяснение пустого состояния для него другое — но список не пуст, так что
    // проверяем именно отсутствие действия, а не текст.
  });

  it("admin видит действие удаления в строке договора", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContractsPage />);
    await screen.findByText("ГП-2026-001");

    await user.click(screen.getAllByRole("button", { name: /Действия с договором/ })[0]);

    expect(await screen.findByRole("menuitem", { name: /Удалить/ })).toBeInTheDocument();
  });

  it("member действия удаления не видит", async () => {
    renderWithProviders(<ContractsPage />, {
      initialUser: { id: 2, email: "member@example.com", role: "member" },
    });
    await screen.findByText("ГП-2026-001");

    expect(screen.queryByRole("button", { name: /Действия с договором/ })).not.toBeInTheDocument();
  });

  it("удаление подтверждается вводом номера договора", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContractsPage />);
    await screen.findByText("ГП-2026-001");

    await user.click(screen.getAllByRole("button", { name: /Действия с договором/ })[0]);
    await user.click(await screen.findByRole("menuitem", { name: /Удалить/ }));

    const confirm = await screen.findByRole("button", { name: "Удалить договор" });
    expect(confirm).toBeDisabled();

    // Опечатка не разблокирует: сверка точная.
    const input = screen.getByLabelText(/Введите номер договора/);
    await user.type(input, "ГП-2026-00");
    expect(confirm).toBeDisabled();

    await user.type(input, "1");
    await waitFor(() => expect(confirm).toBeEnabled());

    // И пробел по краям — это уже НЕ тот номер: сверка без `trim()`.
    await user.type(input, " ");
    expect(confirm).toBeDisabled();
  });

  it("диалог называет ЧИСЛА удаляемого — смет и заданий", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContractsPage />);
    await screen.findByText("ГП-2026-001");

    await user.click(screen.getAllByRole("button", { name: /Действия с договором/ })[0]);
    await user.click(await screen.findByRole("menuitem", { name: /Удалить/ }));

    // Числа фикстур: у ГП-2026-001 `estimates_count: 1` (fixtures.ts:127), а
    // обработчик истории отдаёт ЧЕТЫРЕ задания —
    // `[...sampleImportJobs, sampleFailedJob, sampleRunningJob]`
    // (handlers.ts:477-479), то есть 2 + 1 + 1. Утверждение на слова «заданий
    // импорта» прошло бы при любом неверном числе, поэтому проверяются числа.
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent(/сметы \(1\)/i);
    expect(dialog).toHaveTextContent(/задания импорта \(4\)/i);
  });

  it("пока история загрузок не пришла, удаление недоступно", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/v1/contracts/:id/import-jobs", async () => {
        await delay("infinite");
        return HttpResponse.json([]);
      })
    );
    renderWithProviders(<ContractsPage />);
    await screen.findByText("ГП-2026-001");

    await user.click(screen.getAllByRole("button", { name: /Действия с договором/ })[0]);
    await user.click(await screen.findByRole("menuitem", { name: /Удалить/ }));
    await user.type(screen.getByLabelText(/Введите номер договора/), "ГП-2026-001");

    // Номер введён верно, но состав удаляемого ещё не назван — подтверждать
    // нечего (спека §2.6).
    expect(screen.getByRole("button", { name: "Удалить договор" })).toBeDisabled();
  });
});
