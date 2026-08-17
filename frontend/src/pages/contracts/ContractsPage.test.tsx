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

  // --- Выбор договоров для сравнения (спека сравнения §2.6, §2.9, DoD 14) ---
  //
  // Маршрут /compare ещё не существует (задача 8) — кнопки собирают АДРЕС и
  // рендерятся ссылками, но никуда не переходят. Здесь проверяется только
  // сборка href.

  it("у каждой строки списка есть чекбокс выбора", async () => {
    renderWithProviders(<ContractsPage />);
    await screen.findByText("ГП-2026-001");

    // sampleContracts — два договора без фильтра, значит и чекбоксов два.
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
  });

  it("«Сравнить выбранные» неактивна, пока не выбран ни один договор", async () => {
    renderWithProviders(<ContractsPage />);
    await screen.findByText("ГП-2026-001");

    expect(screen.getByRole("button", { name: "Сравнить выбранные (0)" })).toBeDisabled();
  });

  it("выбор строк называет их число и собирает /compare?ids=… из выбранных id", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContractsPage />);
    await screen.findByText("ГП-2026-001");

    const checkboxes = screen.getAllByRole("checkbox");
    await user.click(checkboxes[0]); // ГП-2026-001, id 100

    expect(
      screen.getByRole("link", { name: "Сравнить выбранные (1)" })
    ).toHaveAttribute("href", "/compare?ids=100");

    await user.click(checkboxes[1]); // ГП-2026-002, id 101

    expect(
      screen.getByRole("link", { name: "Сравнить выбранные (2)" })
    ).toHaveAttribute("href", "/compare?ids=100,101");
  });

  it("«Сравнить всё по фильтру» несёт текущий поиск и класс плюс all=1, без page", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContractsPage />);
    await screen.findByText("ГП-2026-001");

    await user.type(screen.getByLabelText("Поиск договоров"), "Северный");
    await user.click(screen.getByRole("combobox", { name: /Класс объектов/ }));
    await user.click(await screen.findByRole("option", { name: "Жилые дома" }));

    // Кириллица в query неизбежно percent-encoded (это делает URLSearchParams
    // корректно) — сравниваем декодированную строку, а не сырой href.
    await waitFor(() => {
      const href = screen.getByRole("link", { name: "Сравнить всё по фильтру" }).getAttribute("href");
      expect(decodeURIComponent(href ?? "")).toBe("/compare?q=Северный&rate_class_id=1&all=1");
    });
    expect(
      screen.getByRole("link", { name: "Сравнить всё по фильтру" }).getAttribute("href")
    ).not.toContain("page");
  });

  it("пустой поиск и «Все классы» не оставляют в адресе пустых параметров", async () => {
    renderWithProviders(<ContractsPage />);
    await screen.findByText("ГП-2026-001");

    expect(
      screen.getByRole("link", { name: "Сравнить всё по фильтру" })
    ).toHaveAttribute("href", "/compare?all=1");
  });

  it("member видит и чекбоксы, и обе кнопки сравнения (§2.9, DoD 14)", async () => {
    renderWithProviders(<ContractsPage />, {
      initialUser: { id: 2, email: "member@example.com", role: "member" },
    });
    await screen.findByText("ГП-2026-001");

    // ПРЕДПОСЫЛКА: роль действительно переключилась. Без этой проверки тест
    // прошёл бы и при молча не применившемся `initialUser` — чекбоксы и кнопки
    // видны admin'у тоже, то есть утверждение «их видит member» стерегло бы
    // только само их существование.
    expect(screen.queryByRole("button", { name: /Новый договор/ })).not.toBeInTheDocument();

    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Сравнить выбранные (0)" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Сравнить всё по фильтру" })).toBeInTheDocument();
  });
});
