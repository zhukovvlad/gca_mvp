import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { TenderFormDialog } from "./TenderFormDialog";
import { sampleTenderCard } from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";

/**
 * `TenderFormDialog` (спека §2.13, задача 12) — по образцу
 * `ContractFormDialog.test.tsx`: своя выделенная проверка формы, а не только
 * упоминание кнопки открытия со страницы (finding ревью задачи 12).
 *
 * Режим создания и режим правки — РАЗНЫЕ формы (объект/номер/класс
 * фиксируются снимком и в правке не редактируются вовсе — не задизейблены, а
 * отсутствуют), поэтому у каждого свой блок тестов.
 */
describe("Форма тендера: создание", () => {
  it("создать тендер нельзя без объекта и предмета", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TenderFormDialog open onOpenChange={() => {}} />);

    const submit = await screen.findByRole("button", { name: "Создать тендер" });
    expect(submit).toBeDisabled();

    await user.click(screen.getByRole("combobox", { name: /Объект/ }));
    await user.click(await screen.findByText("ЖК Северный"));
    expect(submit).toBeDisabled();

    await user.type(screen.getByLabelText("Номер тендера"), "Т-2026-777");
    expect(submit).toBeDisabled();

    await user.type(screen.getByLabelText("Предмет тендера"), "Генподряд");
    await waitFor(() => expect(submit).toBeEnabled());
  });

  it("отправляет ИМЕННО выбранные object_id/rate_class_id и введённые номер/предмет — не любые ненулевые", async () => {
    let body: Record<string, unknown> | undefined;
    server.use(
      http.post("/api/v1/tenders", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...sampleTenderCard, id: 777 }, { status: 201 });
      })
    );
    const user = userEvent.setup();
    renderWithProviders(<TenderFormDialog open onOpenChange={() => {}} />);

    await user.click(screen.getByRole("combobox", { name: /Объект/ }));
    await user.click(await screen.findByText("ЖК Северный")); // id 10
    await user.click(screen.getByRole("combobox", { name: /Класс объектов/ }));
    // Регекс, а не точная строка: у опции есть подсказка-описание рядом с
    // названием («Многоквартирные жилые дома»), и доступное имя опции несёт
    // ОБА текста — тот же приём, что уже использует `ContractFormDialog.test.tsx`.
    await user.click(await screen.findByRole("option", { name: /Жилые дома/ })); // id 1
    await user.type(screen.getByLabelText("Номер тендера"), "Т-2026-777");
    await user.type(screen.getByLabelText("Предмет тендера"), "Генподряд на реконструкцию");

    await user.click(screen.getByRole("button", { name: "Создать тендер" }));

    await waitFor(() => expect(body).toBeDefined());
    expect(body!.object_id).toBe(10);
    expect(body!.rate_class_id).toBe(1);
    expect(body!.tender_number).toBe("Т-2026-777");
    expect(body!.title).toBe("Генподряд на реконструкцию");
  });

  it("класс не выбран — подсказка «класс объекта», а не блокировка отправки", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TenderFormDialog open onOpenChange={() => {}} />);

    expect(
      await screen.findByText(/возьмётся класс объекта/)
    ).toBeInTheDocument();

    await user.click(screen.getByRole("combobox", { name: /Объект/ }));
    await user.click(await screen.findByText("ЖК Северный"));
    await user.type(screen.getByLabelText("Номер тендера"), "Т-2026-778");
    await user.type(screen.getByLabelText("Предмет тендера"), "Генподряд");

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Создать тендер" })).toBeEnabled()
    );
  });
});

describe("Форма тендера: правка", () => {
  it("объект, номер и класс НЕ редактируются — полей нет вовсе, а не задизейблены", async () => {
    renderWithProviders(
      <TenderFormDialog open onOpenChange={() => {}} tender={sampleTenderCard} />
    );

    await screen.findByRole("heading", { name: "Правка тендера" });
    expect(screen.queryByRole("combobox", { name: /Объект/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Номер тендера")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: /Класс объектов/ })).not.toBeInTheDocument();
  });

  it("отправляет ТОЛЬКО предмет и примечания — ничего больше", async () => {
    let body: unknown;
    server.use(
      http.patch("/api/v1/tenders/:id", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ...sampleTenderCard, ...(body as Record<string, unknown>) });
      })
    );
    const user = userEvent.setup();
    renderWithProviders(
      <TenderFormDialog open onOpenChange={() => {}} tender={sampleTenderCard} />
    );

    const titleInput = await screen.findByLabelText("Предмет тендера");
    await user.clear(titleInput);
    await user.type(titleInput, "Новый предмет тендера");
    await user.type(screen.getByLabelText("Примечания"), "уточнение по итогам");

    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(body).toBeDefined());
    // Строгое равенство, а не «содержит поля»: если бы правка вдруг послала
    // ещё и object_id/tender_number/rate_class_id (например по недосмотру
    // после рефакторинга формы), это утверждение бы это поймало — «содержит»
    // пропустило бы лишние поля молча.
    expect(body).toEqual({ title: "Новый предмет тендера", notes: "уточнение по итогам" });
  });
});
