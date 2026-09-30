import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { handlerState } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";
import type { NewRow } from "@/types/domain";

import { NewQueue } from "./NewQueue";

/**
 * Очередь «Новая» (спека semantic-suggestions §2.12): имя от ИИ, «ИИ считает строку
 * системой», строки единицы без активных семей. Строки — фикстура `handlers.ts`:
 * 11 «Гидрошпонка…» (имя «Гидрошпонки», 0,87), 12 «Шпатлёвка…» (0,78), 13 «СИСТЕМА»
 * (0,93), 0 — единица без активных семей.
 */

const UNIT_SYMBOL: Record<string, string> = { M2: "м²", COMPL: "компл", MON: "мес" };
const unitLabel = (code: string | null) => (code ? (UNIT_SYMBOL[code] ?? code) : "без единицы");

function fixtureRows(): NewRow[] {
  return structuredClone(handlerState.newRows);
}

function rowOf(title: RegExp) {
  const row = screen.getAllByTestId("new-row").find((r) => title.test(r.textContent ?? ""));
  if (!row) throw new Error(`нет строки ${title}`);
  return row;
}

afterEach(() => {
  localStorage.clear();
});

describe("NewQueue", () => {
  it("строка с ответом: единица символом, имя от ИИ, наименование, уверенность с двумя знаками", () => {
    renderWithProviders(<NewQueue rows={fixtureRows()} unitLabel={unitLabel} />);

    const row = rowOf(/Гидрошпонка/);
    expect(within(row).getByText("м²")).toBeInTheDocument();
    expect(within(row).getByText("Гидрошпонки")).toBeInTheDocument();
    expect(within(row).getByText("Гидрошпонка ТЕХНОНИКОЛЬ Фундамент ТПС-В-140-1")).toBeInTheDocument();
    expect(within(row).getByText("0,87")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Завести семью…" })).toBeInTheDocument();
  });

  it("ответ «СИСТЕМА» подписан «ИИ считает строку системой» и не показывает имя семьи", () => {
    renderWithProviders(<NewQueue rows={fixtureRows()} unitLabel={unitLabel} />);

    const row = rowOf(/дымоудаления/);
    expect(within(row).getByText("ИИ считает строку системой")).toBeInTheDocument();
    expect(within(row).getByText("компл")).toBeInTheDocument();
    expect(within(row).queryByText("СИСТЕМА")).not.toBeInTheDocument();
  });

  it("ответ с именем не подписан как система", () => {
    renderWithProviders(<NewQueue rows={fixtureRows()} unitLabel={unitLabel} />);
    expect(within(rowOf(/Шпатлёвка/)).queryByText("ИИ считает строку системой")).not.toBeInTheDocument();
  });

  it("единица без активных семей: «ИИ не спрашивали», прочерк вместо уверенности, «Завести семью…» нет", () => {
    renderWithProviders(<NewQueue rows={fixtureRows()} unitLabel={unitLabel} />);

    const row = rowOf(/Аренда бытового городка/);
    expect(
      within(row).getByText("в единице нет активных семей — ИИ не спрашивали")
    ).toBeInTheDocument();
    expect(within(row).getByText("—")).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Завести семью…" })).not.toBeInTheDocument();
    expect(
      screen.getByText(/Для единицы без активных семей семью заводят на вкладке «Семьи»/)
    ).toBeInTheDocument();
  });

  it("«Завести семью…» открывает окно с именем этой строки", async () => {
    const user = userEvent.setup();
    renderWithProviders(<NewQueue rows={fixtureRows()} unitLabel={unitLabel} />);

    await user.click(within(rowOf(/Шпатлёвка/)).getByRole("button", { name: "Завести семью…" }));

    expect((screen.getByLabelText("Имя") as HTMLInputElement).value).toBe("Шпатлёвка стен");
  });

  it("пустая очередь — сообщение, таблицы нет", () => {
    renderWithProviders(<NewQueue rows={[]} unitLabel={unitLabel} />);
    expect(screen.getByText("В этой очереди при текущих фильтрах пусто.")).toBeInTheDocument();
    expect(screen.queryByTestId("new-queue")).not.toBeInTheDocument();
  });

  it("страницы режутся по размеру: при 10 на странице одиннадцатая строка на второй", async () => {
    const user = userEvent.setup();
    const many: NewRow[] = Array.from({ length: 11 }, (_, i) => ({
      ...fixtureRows()[0],
      suggestion_id: 100 + i,
      context_id: 7000 + i,
      title: `Строка ${String(i + 1).padStart(2, "0")}`,
    }));
    renderWithProviders(<NewQueue rows={many} unitLabel={unitLabel} />);

    expect(screen.getAllByTestId("new-row")).toHaveLength(10);
    expect(screen.queryByText("Строка 11")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    expect(screen.getAllByTestId("new-row")).toHaveLength(1);
    expect(screen.getByText("Строка 11")).toBeInTheDocument();
  });
});
