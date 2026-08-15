import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { ObjectFormDialog } from "./ObjectFormDialog";
import { sampleObjects } from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";

/**
 * `sampleObjects[0]` (id={@link OBJECT_WITH_AREAS}) несёт заполненную пару
 * площадей — на ней стоят тесты живой суммы и правки. `sampleObjects[1]`
 * (id={@link OBJECT_WITHOUT_AREAS}) несёт законное `NULL`/`NULL` («ТЭП не
 * заведены») — на ней стоит тест пустого состояния (спека §2.3).
 */
const OBJECT_WITH_AREAS = sampleObjects[0].id;
const OBJECT_WITHOUT_AREAS = sampleObjects[1].id;

describe("Диалог правки объекта: площади и живая общая (спека §2.9, §2.10)", () => {
  it("показывает вычисленную общую площадь под полями", async () => {
    renderWithProviders(
      <ObjectFormDialog open objectId={OBJECT_WITH_AREAS} onOpenChange={() => {}} />
    );
    await userEvent.clear(await screen.findByLabelText(/наземная/i));
    await userEvent.type(screen.getByLabelText(/наземная/i), "62399.70");
    await userEvent.clear(screen.getByLabelText(/подземная/i));
    await userEvent.type(screen.getByLabelText(/подземная/i), "13341.30");
    expect(await screen.findByText("75741")).toBeInTheDocument();
  });

  it("общая не появляется, пока заполнено только одно поле", async () => {
    renderWithProviders(
      <ObjectFormDialog open objectId={OBJECT_WITHOUT_AREAS} onOpenChange={() => {}} />
    );
    await userEvent.type(await screen.findByLabelText(/наземная/i), "100");
    expect(screen.queryByTestId("area-total-preview")).not.toBeInTheDocument();
  });

  it("отправляет площади строками, а не числами", async () => {
    let body: unknown;
    server.use(
      http.patch("/api/v1/objects/:id", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ id: OBJECT_WITH_AREAS });
      })
    );
    renderWithProviders(
      <ObjectFormDialog open objectId={OBJECT_WITH_AREAS} onOpenChange={() => {}} />
    );
    await userEvent.clear(await screen.findByLabelText(/наземная/i));
    await userEvent.type(screen.getByLabelText(/наземная/i), "62399,70");
    await userEvent.click(screen.getByRole("button", { name: /сохранить/i }));
    await waitFor(() => expect(body).toBeDefined());
    // Запятая приведена к точке, значение — строка: <input type="number"> отдал бы float.
    expect((body as Record<string, unknown>).area_aboveground_sp).toBe("62399.70");
  });

  it("правит одну площадь, не трогая вторую", async () => {
    let body: Record<string, unknown> | undefined;
    server.use(
      http.patch("/api/v1/objects/:id", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: OBJECT_WITH_AREAS });
      })
    );
    renderWithProviders(
      <ObjectFormDialog open objectId={OBJECT_WITH_AREAS} onOpenChange={() => {}} />
    );
    await userEvent.clear(await screen.findByLabelText(/подземная/i));
    await userEvent.type(screen.getByLabelText(/подземная/i), "500");
    await userEvent.click(screen.getByRole("button", { name: /сохранить/i }));
    await waitFor(() => expect(body).toBeDefined());
    expect(body!.area_underground_sp).toBe("500");
  });

  it("сбрасывает обе площади, когда оба поля очищены", async () => {
    let body: Record<string, unknown> | undefined;
    server.use(
      http.patch("/api/v1/objects/:id", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: OBJECT_WITH_AREAS });
      })
    );
    renderWithProviders(
      <ObjectFormDialog open objectId={OBJECT_WITH_AREAS} onOpenChange={() => {}} />
    );
    await userEvent.clear(await screen.findByLabelText(/наземная/i));
    await userEvent.clear(screen.getByLabelText(/подземная/i));
    await userEvent.click(screen.getByRole("button", { name: /сохранить/i }));
    await waitFor(() => expect(body).toBeDefined());
    expect(body!.area_aboveground_sp).toBeNull();
    expect(body!.area_underground_sp).toBeNull();
  });

  it("показывает отказ сервера человеку", async () => {
    server.use(
      http.patch("/api/v1/objects/:id", () =>
        HttpResponse.json({ detail: "Площади задаются парой" }, { status: 422 })
      )
    );
    renderWithProviders(
      <ObjectFormDialog open objectId={OBJECT_WITH_AREAS} onOpenChange={() => {}} />
    );
    await userEvent.clear(await screen.findByLabelText(/подземная/i));
    await userEvent.click(screen.getByRole("button", { name: /сохранить/i }));
    expect(await screen.findByText(/площади задаются парой/i)).toBeInTheDocument();
  });
});

// --- Полезная площадь: третье поле ввода (спека 2026-08-15 §2.7) ---

describe("Диалог правки объекта: полезная площадь (спека 2026-08-15 §2.7)", () => {
  it("подставляет существующее значение при открытии", async () => {
    renderWithProviders(
      <ObjectFormDialog open objectId={OBJECT_WITH_AREAS} onOpenChange={() => {}} />
    );
    expect(await screen.findByLabelText(/полезная/i)).toHaveValue(
      sampleObjects[0].area_useful_sp
    );
  });

  it("отправляет полезную площадь строкой", async () => {
    let body: Record<string, unknown> | undefined;
    server.use(
      http.patch("/api/v1/objects/:id", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: OBJECT_WITH_AREAS });
      })
    );
    renderWithProviders(
      <ObjectFormDialog open objectId={OBJECT_WITH_AREAS} onOpenChange={() => {}} />
    );
    await userEvent.clear(await screen.findByLabelText(/полезная/i));
    await userEvent.type(screen.getByLabelText(/полезная/i), "45000,25");
    await userEvent.click(screen.getByRole("button", { name: /сохранить/i }));
    await waitFor(() => expect(body).toBeDefined());
    // Запятая приведена к точке, значение — строка: та же механика, что у пары.
    expect(body!.area_useful_sp).toBe("45000.25");
  });

  it("пустое поле уходит как null", async () => {
    let body: Record<string, unknown> | undefined;
    server.use(
      http.patch("/api/v1/objects/:id", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: OBJECT_WITH_AREAS });
      })
    );
    renderWithProviders(
      <ObjectFormDialog open objectId={OBJECT_WITH_AREAS} onOpenChange={() => {}} />
    );
    await userEvent.clear(await screen.findByLabelText(/полезная/i));
    await userEvent.click(screen.getByRole("button", { name: /сохранить/i }));
    await waitFor(() => expect(body).toBeDefined());
    expect(body!.area_useful_sp).toBeNull();
  });

  it("в общую площадь не входит: живая сумма считается только по паре", async () => {
    /* Полезная — ЧАСТЬ общей, а не третье слагаемое (спека §2.2). Знаменатель
       ₽/м² не меняется, и предпросмотр обязан это показывать. */
    renderWithProviders(
      <ObjectFormDialog open objectId={OBJECT_WITH_AREAS} onOpenChange={() => {}} />
    );
    await userEvent.clear(await screen.findByLabelText(/полезная/i));
    await userEvent.type(screen.getByLabelText(/полезная/i), "1000");
    expect(await screen.findByTestId("area-total-preview")).toHaveTextContent("75741");
  });
});

// --- Остальные поля объекта: диалог правит объект целиком, а не только ТЭП ---

describe("Диалог правки объекта: остальные поля (спека §2.9)", () => {
  it("показывает название, адрес и класс объекта", async () => {
    renderWithProviders(
      <ObjectFormDialog open objectId={OBJECT_WITH_AREAS} onOpenChange={() => {}} />
    );
    expect(await screen.findByLabelText(/название/i)).toHaveValue(sampleObjects[0].title);
    expect(screen.getByLabelText(/адрес/i)).toHaveValue(sampleObjects[0].address);
    expect(screen.getByText(sampleObjects[0].rate_class_title!)).toBeInTheDocument();
  });

  it("правит название, не трогая площади", async () => {
    let body: Record<string, unknown> | undefined;
    server.use(
      http.patch("/api/v1/objects/:id", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: OBJECT_WITH_AREAS });
      })
    );
    renderWithProviders(
      <ObjectFormDialog open objectId={OBJECT_WITH_AREAS} onOpenChange={() => {}} />
    );
    await userEvent.clear(await screen.findByLabelText(/название/i));
    await userEvent.type(screen.getByLabelText(/название/i), "Новое имя");
    await userEvent.click(screen.getByRole("button", { name: /сохранить/i }));
    await waitFor(() => expect(body).toBeDefined());
    expect(body!.title).toBe("Новое имя");
  });

  it("пустое название не даёт сохранить", async () => {
    renderWithProviders(
      <ObjectFormDialog open objectId={OBJECT_WITH_AREAS} onOpenChange={() => {}} />
    );
    await userEvent.clear(await screen.findByLabelText(/название/i));
    expect(screen.getByRole("button", { name: /сохранить/i })).toBeDisabled();
  });
});

// --- Асинхронная загрузка: тело формы не должно монтироваться до данных ---

describe("Диалог правки объекта: асинхронная загрузка", () => {
  it("показывает загрузку, пока объект не пришёл", async () => {
    server.use(
      http.get("/api/v1/objects/:id", async () => {
        await delay(50);
        return HttpResponse.json(sampleObjects[0]);
      })
    );
    renderWithProviders(
      <ObjectFormDialog open objectId={OBJECT_WITH_AREAS} onOpenChange={() => {}} />
    );
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByLabelText(/название/i)).not.toBeInTheDocument();
    expect(await screen.findByLabelText(/название/i)).toBeInTheDocument();
  });

  it("поля заполнены значениями объекта сразу после загрузки", async () => {
    /* Регресс на инициализацию: тело, смонтированное ДО ответа, увидело бы
       undefined в useState и осталось бы пустым навсегда — эффекта, который
       дозаполнил бы поля, в этой конструкции нет. */
    renderWithProviders(
      <ObjectFormDialog open objectId={OBJECT_WITH_AREAS} onOpenChange={() => {}} />
    );
    expect(await screen.findByLabelText(/наземная/i)).toHaveValue(
      sampleObjects[0].area_aboveground_sp
    );
  });

  it("показывает ошибку, если объект не загрузился", async () => {
    server.use(
      http.get("/api/v1/objects/:id", () => new HttpResponse(null, { status: 500 }))
    );
    renderWithProviders(
      <ObjectFormDialog open objectId={OBJECT_WITH_AREAS} onOpenChange={() => {}} />
    );
    expect(await screen.findByText(/не удалось загрузить объект/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /сохранить/i })).not.toBeInTheDocument();
  });
});
