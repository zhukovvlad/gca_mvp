import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { renderWithProviders, waitForDialogFocus } from "@/test/utils";

import { FamilyCategoriesDialog } from "./FamilyCategoriesDialog";

/**
 * Окно «Категории семей» (спека 3б §2.9, §2.13, макет К2): имя, определение (его видит модель),
 * число семей, «Править», «Добавить категорию»; удаление — только у категории без семей.
 * Фикстура справочника: «Работа» (2 семьи), «Инженерная система», «Затраты и услуги»,
 * «Проектирование» — у трёх последних семей нет.
 */

async function renderDialog(onOpenChange = vi.fn()) {
  renderWithProviders(<FamilyCategoriesDialog open onOpenChange={onOpenChange} />);
  await waitForDialogFocus();
  await screen.findByText("Инженерная система");
  return { onOpenChange };
}

function rowOf(title: string): HTMLElement {
  return screen.getByText(title, { selector: "td" }).closest("tr")!;
}

describe("FamilyCategoriesDialog", () => {
  it("печатает имя, определение и число семей каждой категории", async () => {
    await renderDialog();

    const work = rowOf("Работа");
    expect(
      within(work).getByText("Строительно-монтажная работа или материал с объёмом в своей единице.")
    ).toBeInTheDocument();
    // «Работа» носят две семьи фикстуры (активная и архивная), у «Проектирования» их нет.
    expect(within(work).getByText("2")).toBeInTheDocument();
    expect(within(rowOf("Проектирование")).getByText("0")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Семей" })).toBeInTheDocument();
  });

  it("«Править»: имя и определение уходят в PATCH, строка показывает новые значения", async () => {
    const user = userEvent.setup();
    await renderDialog();

    await user.click(within(rowOf("Проектирование")).getByRole("button", { name: "Править" }));
    const title = screen.getByLabelText("Имя категории");
    expect(title).toHaveValue("Проектирование");
    await user.clear(title);
    await user.type(title, "Проектные работы");
    const definition = screen.getByLabelText("Определение категории");
    await user.clear(definition);
    await user.type(definition, "Проекты и изыскания.");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(handlerState.categoryRequests).toEqual([
        { method: "PATCH", id: 4, body: { title: "Проектные работы", definition: "Проекты и изыскания." } },
      ])
    );
    expect(await screen.findByText("Проектные работы", { selector: "td" })).toBeInTheDocument();
    expect(screen.queryByText("Проектирование", { selector: "td" })).not.toBeInTheDocument();
  });

  it("«Добавить категорию»: пока имя или определение пусты, «Добавить» неактивна; затем POST и новая строка", async () => {
    const user = userEvent.setup();
    await renderDialog();

    await user.click(screen.getByRole("button", { name: "Добавить категорию" }));
    const add = screen.getByRole("button", { name: "Добавить" });
    expect(add).toBeDisabled();
    await user.type(screen.getByLabelText("Имя категории"), "Охрана");
    expect(add).toBeDisabled();
    await user.type(screen.getByLabelText("Определение категории"), "Охрана объекта.");
    expect(add).toBeEnabled();
    await user.click(add);

    await waitFor(() =>
      expect(handlerState.categoryRequests).toEqual([
        { method: "POST", id: null, body: { title: "Охрана", definition: "Охрана объекта." } },
      ])
    );
    expect(await screen.findByText("Охрана", { selector: "td" })).toBeInTheDocument();
  });

  it("имя из одних пробелов не считается именем", async () => {
    const user = userEvent.setup();
    await renderDialog();

    await user.click(screen.getByRole("button", { name: "Добавить категорию" }));
    await user.type(screen.getByLabelText("Имя категории"), "   ");
    await user.type(screen.getByLabelText("Определение категории"), "Охрана объекта.");
    expect(screen.getByRole("button", { name: "Добавить" })).toBeDisabled();
  });

  it("дубль имени: подпись отказа category_duplicate, форма остаётся открытой", async () => {
    const user = userEvent.setup();
    await renderDialog();

    await user.click(screen.getByRole("button", { name: "Добавить категорию" }));
    await user.type(screen.getByLabelText("Имя категории"), "Работа");
    await user.type(screen.getByLabelText("Определение категории"), "Любое.");
    await user.click(screen.getByRole("button", { name: "Добавить" }));

    expect(await screen.findByText("Категория «Работа» уже есть.")).toBeInTheDocument();
    expect(screen.getByLabelText("Имя категории")).toHaveValue("Работа");
  });

  it.each([
    ["добавление", "POST"],
    ["переименование", "PATCH"],
  ])("дубль имени (%s): подпись по коду, а не текст сервера", async (_name, method) => {
    // Ревью задачи 5: фикстура MSW отдаёт текст, совпадающий с подписью, — такой тест не
    // отличил бы подпись по коду от сырого текста сервера. Здесь текст сервера другой.
    const user = userEvent.setup();
    const refusal = () =>
      HttpResponse.json(
        { detail: { code: "category_duplicate", message: "сырой текст сервера", title: "Работа" } },
        { status: 409 }
      );
    server.use(
      method === "POST"
        ? http.post("/api/v1/semantic/family-categories", refusal)
        : http.patch("/api/v1/semantic/family-categories/:id", refusal)
    );
    await renderDialog();

    if (method === "POST") {
      await user.click(screen.getByRole("button", { name: "Добавить категорию" }));
      await user.type(screen.getByLabelText("Имя категории"), "Работа");
      await user.type(screen.getByLabelText("Определение категории"), "Любое.");
      await user.click(screen.getByRole("button", { name: "Добавить" }));
    } else {
      await user.click(within(rowOf("Проектирование")).getByRole("button", { name: "Править" }));
      const title = screen.getByLabelText("Имя категории");
      await user.clear(title);
      await user.type(title, "Работа");
      await user.click(screen.getByRole("button", { name: "Сохранить" }));
    }

    expect(await screen.findByText("Категория «Работа» уже есть.")).toBeInTheDocument();
    expect(screen.queryByText("сырой текст сервера")).not.toBeInTheDocument();
  });

  it("удаление недоступно у категории с семьями и доступно у пустой", async () => {
    await renderDialog();

    expect(within(rowOf("Работа")).getByRole("button", { name: "Удалить" })).toBeDisabled();
    expect(within(rowOf("Проектирование")).getByRole("button", { name: "Удалить" })).toBeEnabled();
    // Ревью задачи 5: недоступная кнопка называет причину, доступная — нет.
    expect(within(rowOf("Работа")).getByRole("button", { name: "Удалить" })).toHaveAttribute(
      "title",
      "Удалить можно только категорию без семей"
    );
    expect(within(rowOf("Проектирование")).getByRole("button", { name: "Удалить" })).not.toHaveAttribute("title");
  });

  it("«Править» другой строки переключает форму на неё, а не оставляет прежние значения", async () => {
    const user = userEvent.setup();
    await renderDialog();

    await user.click(within(rowOf("Работа")).getByRole("button", { name: "Править" }));
    expect(screen.getByLabelText("Имя категории")).toHaveValue("Работа");
    await user.click(within(rowOf("Проектирование")).getByRole("button", { name: "Править" }));

    expect(screen.getByLabelText("Имя категории")).toHaveValue("Проектирование");
    expect(screen.getByLabelText("Определение категории")).toHaveValue("Проектные и изыскательские работы.");
  });

  it("справочник не прочитан — окно говорит об этом, а не показывает пустую таблицу молча", async () => {
    server.use(
      http.get("/api/v1/semantic/family-categories", () => HttpResponse.json({ detail: "x" }, { status: 500 }))
    );
    renderWithProviders(<FamilyCategoriesDialog open onOpenChange={vi.fn()} />);

    expect(await screen.findByText("Не удалось получить справочник категорий.", {}, { timeout: 8000 })).toBeInTheDocument();
  });

  it("удаление пустой категории: подтверждение, DELETE, строка исчезает", async () => {
    const user = userEvent.setup();
    await renderDialog();

    await user.click(within(rowOf("Проектирование")).getByRole("button", { name: "Удалить" }));
    expect(handlerState.categoryRequests).toEqual([]);
    await user.click(await screen.findByRole("button", { name: "Удалить категорию" }));

    await waitFor(() =>
      expect(handlerState.categoryRequests).toEqual([{ method: "DELETE", id: 4, body: null }])
    );
    await waitFor(() =>
      expect(screen.queryByText("Проектирование", { selector: "td" })).not.toBeInTheDocument()
    );
  });

  it("category_in_use от сервера (семья успела сослаться): отказ показан подписью, счётчик перечитан", async () => {
    const user = userEvent.setup();
    await renderDialog();
    expect(within(rowOf("Проектирование")).getByText("0")).toBeInTheDocument();
    // Клиент ещё видит у «Проектирования» ноль семей, а сервер уже знает о семье: она сослалась
    // на категорию после того, как окно прочитало справочник.
    handlerState.workFamilies.find((f) => f.id === 2)!.family_category_id = 4;
    // Форма отказа — как у сервера (`services/family_categories.py::_in_use`): в контексте
    // `category_id` и `family_count`, имени категории нет — подпись берёт текст сервера.
    server.use(
      http.delete("/api/v1/semantic/family-categories/:id", () =>
        HttpResponse.json(
          {
            detail: {
              code: "category_in_use",
              message: "Категорию «Проектирование» носят 1 семей — удалить можно только пустую.",
              category_id: 4,
              family_count: 1,
            },
          },
          { status: 409 }
        )
      )
    );

    await user.click(within(rowOf("Проектирование")).getByRole("button", { name: "Удалить" }));
    await user.click(await screen.findByRole("button", { name: "Удалить категорию" }));

    expect(
      await screen.findByText("Категорию «Проектирование» носят 1 семей — удалить можно только пустую.")
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("category_in_use");
    // Ревью задачи 5: заголовок обещал «счётчик перечитан» — теперь это проверено.
    await waitFor(() => expect(within(rowOf("Проектирование")).getByText("1")).toBeInTheDocument());
    expect(within(rowOf("Проектирование")).getByRole("button", { name: "Удалить" })).toBeDisabled();
  });

  it("после сохранения форма закрывается; «Закрыть» сбрасывает недописанную форму", async () => {
    const user = userEvent.setup();
    await renderDialog();

    await user.click(screen.getByRole("button", { name: "Добавить категорию" }));
    await user.type(screen.getByLabelText("Имя категории"), "Охрана");
    await user.type(screen.getByLabelText("Определение категории"), "Охрана объекта.");
    await user.click(screen.getByRole("button", { name: "Добавить" }));
    await waitFor(() => expect(screen.queryByLabelText("Имя категории")).not.toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Добавить категорию" }));
    await user.type(screen.getByLabelText("Имя категории"), "Недописанная");
    await user.click(screen.getByRole("button", { name: "Закрыть" }));
    expect(screen.queryByLabelText("Имя категории")).not.toBeInTheDocument();
  });

  it("пока запрос в пути, «Добавить» и «Удалить» недоступны — второго запроса нет", async () => {
    const user = userEvent.setup();
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post("/api/v1/semantic/family-categories", async () => {
        await held;
        return HttpResponse.json({ detail: { code: "category_duplicate", message: "x" } }, { status: 409 });
      }),
      http.delete("/api/v1/semantic/family-categories/:id", async () => {
        await held;
        return HttpResponse.json({ detail: { code: "category_in_use", message: "x" } }, { status: 409 });
      })
    );
    await renderDialog();

    await user.click(screen.getByRole("button", { name: "Добавить категорию" }));
    await user.type(screen.getByLabelText("Имя категории"), "Охрана");
    await user.type(screen.getByLabelText("Определение категории"), "Охрана объекта.");
    await user.click(screen.getByRole("button", { name: "Добавить" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Добавить" })).toBeDisabled());

    await user.click(within(rowOf("Проектирование")).getByRole("button", { name: "Удалить" }));
    await user.click(await screen.findByRole("button", { name: "Удалить категорию" }));
    await waitFor(() =>
      expect(within(rowOf("Затраты и услуги")).getByRole("button", { name: "Удалить" })).toBeDisabled()
    );
    release();
  });

  it("«Закрыть» закрывает окно", async () => {
    const user = userEvent.setup();
    const { onOpenChange } = await renderDialog();

    await user.click(screen.getByRole("button", { name: "Закрыть" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
