import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CategoryPicker, type CategoryPickerProps } from "./CategoryPicker";
import { sampleProjectPassport } from "@/test/fixtures";
import { renderWithProviders } from "@/test/utils";

/**
 * Выбор статьи с необязательным полем «Заметка» (спека этапного разноса
 * §2.7, §2.4; план, задача 10) — компонент выделен из `UnallocatedPanel`.
 *
 * `noteField === false` (паспорт) — поля нет вовсе, `onPick(option, null)`.
 * Иначе — `Textarea aria-label="Заметка"`:
 * - 0 или 1 РАЗЛИЧНАЯ заметка (`null` — «без заметки» — тоже значение) —
 *   поле предзаполнено ею (или пусто), пункты доступны сразу;
 * - ≥2 различных — пункты `CommandItem` недоступны, пока не нажата одна из
 *   кнопок-заметок (включая «без заметки»); после — доступны, поле несёт
 *   выбранную заметку.
 */

const OPTIONS = sampleProjectPassport.category_options;

// Код и название рендерятся соседними `<span>` без пробела между ними — их
// текстовые узлы всё равно склеиваются в ОДНО accessible-имя пункта.
function optionName(option: (typeof OPTIONS)[number]): string {
  return `${option.code}${option.title}`;
}

function pick(props: Partial<CategoryPickerProps> = {}) {
  const onPick = vi.fn();
  renderWithProviders(
    <CategoryPicker
      testKey="k"
      label="14 SHELL & CORE"
      options={OPTIONS}
      noteField={false}
      onPick={onPick}
      {...props}
    />
  );
  return onPick;
}

async function openPopover(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByTestId("pick-category-k"));
}

/** Закрывает поповер `Escape` (базовое поведение `useDismiss` в base-ui) и
 *  дожидается ухода портального содержимого — иначе следующее открытие
 *  застаёт старый DOM ещё не размонтированным. */
async function closePopover(user: ReturnType<typeof userEvent.setup>) {
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByLabelText("Заметка")).not.toBeInTheDocument());
}

describe("CategoryPicker: без поля заметки (паспорт)", () => {
  it("выбор статьи шлёт null, поля «Заметка» нет вовсе", async () => {
    const user = userEvent.setup();
    const onPick = pick();

    await openPopover(user);
    expect(screen.queryByLabelText("Заметка")).not.toBeInTheDocument();

    await user.click(screen.getByRole("option", { name: optionName(OPTIONS[0]) }));

    expect(onPick).toHaveBeenCalledWith(OPTIONS[0], null);
  });
});

describe("CategoryPicker: поле заметки — 0 или 1 различная заметка", () => {
  it("единственная существующая заметка предзаполняет поле и уходит в выбор", async () => {
    const user = userEvent.setup();
    const onPick = pick({ noteField: { existingNotes: ["код в файле нечитаем"] } });

    await openPopover(user);
    const field = screen.getByLabelText("Заметка");
    expect(field).toHaveValue("код в файле нечитаем");
    // Пункты доступны сразу — единственная заметка не требует явного выбора.
    expect(screen.getByRole("option", { name: optionName(OPTIONS[0]) })).not.toHaveAttribute(
      "aria-disabled",
      "true"
    );

    await user.click(screen.getByRole("option", { name: optionName(OPTIONS[0]) }));

    expect(onPick).toHaveBeenCalledWith(OPTIONS[0], "код в файле нечитаем");
  });

  it("отсутствие существующих заметок оставляет поле пустым", async () => {
    const user = userEvent.setup();
    const onPick = pick({ noteField: { existingNotes: [] } });

    await openPopover(user);
    expect(screen.getByLabelText("Заметка")).toHaveValue("");

    await user.click(screen.getByRole("option", { name: optionName(OPTIONS[0]) }));

    expect(onPick).toHaveBeenCalledWith(OPTIONS[0], null);
  });

  it("очищенное вручную поле уходит в выбор как null, а не как пустая строка", async () => {
    const user = userEvent.setup();
    const onPick = pick({ noteField: { existingNotes: ["была заметка"] } });

    await openPopover(user);
    const field = screen.getByLabelText("Заметка");
    await user.clear(field);

    await user.click(screen.getByRole("option", { name: optionName(OPTIONS[0]) }));

    expect(onPick).toHaveBeenCalledWith(OPTIONS[0], null);
  });

  it("повторяющиеся одинаковые заметки схлопываются в одну — поле предзаполнено, пункты доступны сразу", async () => {
    /*
      Ревью задачи 10: подмена `distinctNotes` на функцию тождества (без
      схлопывания) оставляла все семь тогдашних тестов зелёными — ни один не
      использовал список с повторами. А это ЧАСТЫЙ случай, не редкий: заметки
      сервера для раздела, разрешённого во всех сметах раунда ОДНОЙ и той же
      заметкой, приходят K копиями одного значения (K — число смет). Без
      дедупликации `distinct.length` считался бы ≥2 и раздел с ЕДИНЫМ
      решением блокировал бы выбор до нажатия одной из двух одинаковых
      кнопок — регресс именно в типовом, а не в краевом сценарии.
    */
    const user = userEvent.setup();
    const onPick = pick({
      noteField: {
        existingNotes: [
          "одна и та же заметка",
          "одна и та же заметка",
          "одна и та же заметка",
        ],
      },
    });

    await openPopover(user);
    expect(screen.getByLabelText("Заметка")).toHaveValue("одна и та же заметка");
    // Различающихся заметок нет — блока выбора между ними тоже нет.
    expect(
      screen.queryByText("Заметки в сметах различаются — выберите, какую оставить:")
    ).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: optionName(OPTIONS[0]) })).not.toHaveAttribute(
      "aria-disabled",
      "true"
    );

    await user.click(screen.getByRole("option", { name: optionName(OPTIONS[0]) }));

    expect(onPick).toHaveBeenCalledWith(OPTIONS[0], "одна и та же заметка");
  });
});

describe("CategoryPicker: поле заметки — заметки различаются", () => {
  it("пункты недоступны, пока не выбрана одна из различающихся заметок", async () => {
    const user = userEvent.setup();
    pick({ noteField: { existingNotes: ["а", null] } });

    await openPopover(user);

    expect(screen.getByRole("option", { name: optionName(OPTIONS[0]) })).toHaveAttribute(
      "aria-disabled",
      "true"
    );
  });

  it("показывает поясняющий заголовок над кнопками-заметками", async () => {
    const user = userEvent.setup();
    pick({ noteField: { existingNotes: ["а", "б"] } });

    await openPopover(user);

    expect(
      screen.getByText("Заметки в сметах различаются — выберите, какую оставить:")
    ).toBeInTheDocument();
  });

  it("кнопки различающихся заметок идут в порядке первого появления", async () => {
    const user = userEvent.setup();
    // "вторая" встречается раньше "первая" во входном списке (с повторами) —
    // порядок кнопок обязан отражать это, а не алфавит и не второе появление.
    pick({
      noteField: { existingNotes: ["вторая", "первая", "вторая", "первая", null] },
    });

    await openPopover(user);

    const heading = screen.getByText(
      "Заметки в сметах различаются — выберите, какую оставить:"
    );
    const buttons = within(heading.parentElement as HTMLElement).getAllByRole("button");
    expect(buttons.map((button) => button.textContent)).toEqual([
      "«вторая»",
      "«первая»",
      "без заметки",
    ]);
  });

  it("«без заметки» делает пункты доступными и уходит в выбор как null", async () => {
    const user = userEvent.setup();
    const onPick = pick({ noteField: { existingNotes: ["а", null] } });

    await openPopover(user);
    await user.click(screen.getByRole("button", { name: "без заметки" }));

    expect(screen.getByRole("option", { name: optionName(OPTIONS[0]) })).not.toHaveAttribute(
      "aria-disabled",
      "true"
    );

    await user.click(screen.getByRole("option", { name: optionName(OPTIONS[0]) }));

    expect(onPick).toHaveBeenCalledWith(OPTIONS[0], null);
  });

  it("выбор одной из различающихся заметок отправляет именно её", async () => {
    const user = userEvent.setup();
    const onPick = pick({ noteField: { existingNotes: ["а", "б"] } });

    await openPopover(user);
    await user.click(screen.getByRole("button", { name: "«а»" }));
    await user.click(screen.getByRole("option", { name: optionName(OPTIONS[0]) }));

    expect(onPick).toHaveBeenCalledWith(OPTIONS[0], "а");
  });
});

describe("CategoryPicker: обрезка поля заметки — только у пробельной строки", () => {
  it("заметка с пробелами по краям уходит В ВЫБОР как есть, без обрезки", async () => {
    // Правило подмены пустоты на null трогает ТОЛЬКО пробельную строку —
    // содержательный текст с ведущими/замыкающими пробелами не обрезается.
    const user = userEvent.setup();
    const onPick = pick({ noteField: { existingNotes: [] } });

    await openPopover(user);
    await user.type(screen.getByLabelText("Заметка"), "  текст с пробелами  ");
    await user.click(screen.getByRole("option", { name: optionName(OPTIONS[0]) }));

    expect(onPick).toHaveBeenCalledWith(OPTIONS[0], "  текст с пробелами  ");
  });

  it("поле из одних пробелов уходит как null, а не как пробельная строка", async () => {
    const user = userEvent.setup();
    const onPick = pick({ noteField: { existingNotes: [] } });

    await openPopover(user);
    await user.type(screen.getByLabelText("Заметка"), "   ");
    await user.click(screen.getByRole("option", { name: optionName(OPTIONS[0]) }));

    expect(onPick).toHaveBeenCalledWith(OPTIONS[0], null);
  });
});

describe("CategoryPicker: повторное открытие — сброс состояния поля", () => {
  it("закрытие без выбора не сохраняет черновик — новое открытие видит исходную заметку", async () => {
    /*
      Ревью задачи 10: удаление сброса состояния при повторном открытии
      (`resetNoteState()` в `handleOpenChange`) оставляло все семь тогдашних
      тестов зелёными — ни один не закрывал и не открывал поповер повторно.
      Без сброса брошенный черновик пережил бы закрытие и подменил бы собой
      предзаполненную существующую заметку при следующем открытии.
    */
    const user = userEvent.setup();
    const onPick = pick({ noteField: { existingNotes: ["исходная заметка"] } });

    await openPopover(user);
    const field = screen.getByLabelText("Заметка");
    expect(field).toHaveValue("исходная заметка");
    await user.clear(field);
    await user.type(field, "черновик, не отправлен");
    expect(field).toHaveValue("черновик, не отправлен");

    await closePopover(user);
    await openPopover(user);

    expect(screen.getByLabelText("Заметка")).toHaveValue("исходная заметка");

    await user.click(screen.getByRole("option", { name: optionName(OPTIONS[0]) }));
    expect(onPick).toHaveBeenCalledWith(OPTIONS[0], "исходная заметка");
  });

  it("раздел, ставший однозначным после первого открытия, снова разрешает выбор при повторном открытии", async () => {
    /*
      Тот же удалённый ревью сброс, острый случай: раздел был неоднозначным
      при ПЕРВОМ открытии (`resolved` инициализирован в false), затем —
      МЕЖДУ закрытием и повторным открытием — сметы раунда выровнялись, и
      `existingNotes` пришёл с ОДНОЙ различной заметкой. Без пересчёта на
      открытии флаг `resolved` остался бы false НАВСЕГДА: кнопки-заметки,
      единственный способ его переключить, при неоднозначности=false уже не
      рендерятся — пункты меню остались бы задизейблены без выхода.
    */
    const user = userEvent.setup();
    const onPick = vi.fn();
    const { rerender } = renderWithProviders(
      <CategoryPicker
        testKey="k"
        label="14 SHELL & CORE"
        options={OPTIONS}
        noteField={{ existingNotes: ["а", "б"] }}
        onPick={onPick}
      />
    );

    await openPopover(user);
    expect(screen.getByRole("option", { name: optionName(OPTIONS[0]) })).toHaveAttribute(
      "aria-disabled",
      "true"
    );

    await closePopover(user);

    rerender(
      <CategoryPicker
        testKey="k"
        label="14 SHELL & CORE"
        options={OPTIONS}
        noteField={{ existingNotes: ["а", "а"] }}
        onPick={onPick}
      />
    );

    await openPopover(user);
    expect(screen.getByLabelText("Заметка")).toHaveValue("а");
    expect(screen.getByRole("option", { name: optionName(OPTIONS[0]) })).not.toHaveAttribute(
      "aria-disabled",
      "true"
    );

    await user.click(screen.getByRole("option", { name: optionName(OPTIONS[0]) }));
    expect(onPick).toHaveBeenCalledWith(OPTIONS[0], "а");
  });
});
