import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import ComparePage from "./ComparePage";
import { sampleComparison } from "@/test/fixtures";
import { handlerState } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";

/**
 * Страница сравнения договоров (спека 2026-08-17, план — задача 8).
 *
 * Агрегат считает сервер (крестики над этим уже стоят в `test_comparison_*`
 * бэкенда) — здесь проверяется то, за что отвечает ЭКРАН: дерево строк по
 * умолчанию свёрнуто до корней, переключатель корзины и режим НДС меняют
 * показ, различимость прочерка/нуля/погашенного числа, подпись состава и
 * подсветка только при трёх и более сопоставимых значениях.
 *
 * **Предпосылка, требующая замера, а не веры на слово** (см. инструкцию
 * задачи 8): `position: sticky` не вычисляется в jsdom — тест ниже проверяет
 * ТОЛЬКО класс, которым запросено закрепление первой колонки; фактическое
 * поведение при горизонтальной прокрутке проверяется прогоном на стенде
 * (задача 9), а не этим файлом.
 */

const SELECTION = "ids=204,203,202,201";

function renderCompare(query = SELECTION, initialUser?: Parameters<typeof renderWithProviders>[1]) {
  return renderWithProviders(<ComparePage />, {
    initialRoute: `/compare?${query}`,
    ...initialUser,
  });
}

describe("Сравнение договоров — предпосылки фикстуры", () => {
  it("строка «1» действительно имеет три сопоставимых значения (испытание подсветки)", () => {
    const row1 = sampleComparison.rows.find((r) => r.code === "1");
    expect(row1?.medians.total.comparable_count).toBe(3);
  });

  it("строка «1» действительно имеет потомка (испытание раскрытия)", () => {
    const child = sampleComparison.rows.find((r) => r.parent_code === "1" && r.kind === "category");
    expect(child?.code).toBe("1.1");
  });

  it("строка «2» действительно имеет меньше трёх сопоставимых (испытание отсутствия подсветки)", () => {
    const row2 = sampleComparison.rows.find((r) => r.code === "2");
    expect(row2?.medians.total.comparable_count).toBeLessThan(3);
  });
});

describe("Сравнение договоров — дерево строк", () => {
  it("по умолчанию видны только корни", async () => {
    renderCompare();
    await screen.findByTestId("comparison-caption");

    expect(screen.getByText("Земляные работы")).toBeInTheDocument();
    expect(screen.getByText("Благоустройство, дороги")).toBeInTheDocument();
    expect(screen.getByText("Нераспределённое")).toBeInTheDocument();

    // Второй уровень и служебная строка «Без подстатьи» скрыты до раскрытия.
    expect(screen.queryByText("Разработка грунта")).not.toBeInTheDocument();
    expect(screen.queryByText("Без подстатьи")).not.toBeInTheDocument();
  });

  it("раскрытие ветки показывает второй уровень (DoD 16)", async () => {
    renderCompare();
    await screen.findByTestId("comparison-caption");

    const chevron = screen.getByRole("button", { name: "Развернуть статью 1" });
    await userEvent.click(chevron);

    expect(await screen.findByText("Разработка грунта")).toBeInTheDocument();
    expect(screen.getByText("Без подстатьи")).toBeInTheDocument();
  });
});

describe("Сравнение договоров — переключатель корзины (DoD 15)", () => {
  it("переключение на «ДС» меняет ячейку статьи на ноль", async () => {
    renderCompare();
    await screen.findByTestId("comparison-caption");

    const cell = screen.getByTestId("comparison-cell-1-204");
    expect(within(cell).getByText(/1\s440\s000,00/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "ДС" }));

    expect(within(screen.getByTestId("comparison-cell-1-204")).getByText("0,00 ₽")).toBeInTheDocument();
  });
});

describe("Сравнение договоров — НДС и ставка в URL (DoD 18)", () => {
  it("режим и ставка восстанавливаются из адреса при заходе", async () => {
    renderCompare(`${SELECTION}&vat_mode=single&single_rate=22.00`);
    await screen.findByTestId("comparison-caption");

    expect(screen.getByRole("button", { name: "Единая" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Своя ставка" })).toHaveAttribute("aria-pressed", "false");
    expect(await screen.findByText("22,00 %")).toBeInTheDocument();
  });

  it("по умолчанию (без vat_mode в адресе) активна «Своя ставка»", async () => {
    renderCompare();
    await screen.findByTestId("comparison-caption");

    expect(screen.getByRole("button", { name: "Своя ставка" })).toHaveAttribute("aria-pressed", "true");
  });
});

describe("Сравнение договоров — подпись налогового состава (AGENTS.md §10)", () => {
  it("подпись режима печатается на поверхности", async () => {
    renderCompare();
    const caption = await screen.findByTestId("comparison-caption");
    expect(caption.textContent).toBe(sampleComparison.caption);
  });

  it("в режиме «своя ставка» состав виден у каждой колонки", async () => {
    renderCompare();
    await screen.findByTestId("comparison-caption");

    expect(screen.getByTestId("comparison-composition-204").textContent).toBe("20 %");
    expect(screen.getByTestId("comparison-composition-203").textContent).toBe("20 %");
    expect(screen.getByTestId("comparison-composition-202").textContent).toBe("22 %");
    expect(screen.getByTestId("comparison-composition-201").textContent).toBe("16 %");
  });

  it("вне режима «своя ставка» подписи состава не печатаются", async () => {
    renderCompare(`${SELECTION}&vat_mode=net`);
    await screen.findByTestId("comparison-caption");

    expect(screen.queryByTestId("comparison-composition-204")).not.toBeInTheDocument();
  });
});

describe("Сравнение договоров — подсветка отклонений (спека §2.5)", () => {
  it("подсветка есть только при трёх и более сопоставимых", async () => {
    renderCompare();
    await screen.findByTestId("comparison-caption");

    // Строка «1» — три сопоставимых (203, 202, 201): отклонение показано.
    expect(screen.getByTestId("comparison-deviation-1-203")).toBeInTheDocument();
    expect(screen.getByTestId("comparison-deviation-1-202")).toBeInTheDocument();
    expect(screen.getByTestId("comparison-deviation-1-201")).toBeInTheDocument();
    // У 204 нет ТЭП — он не мог войти в медиану, отклонения нет вовсе.
    expect(screen.queryByTestId("comparison-deviation-1-204")).not.toBeInTheDocument();

    // Строка «2» — сопоставимых 0: подсветки нет, но экран называет причину.
    expect(screen.queryByTestId("comparison-deviation-2-204")).not.toBeInTheDocument();
    expect(screen.getByTestId("comparison-nomedian-2")).toBeInTheDocument();
    expect(screen.queryByTestId("comparison-nomedian-1")).not.toBeInTheDocument();
  });
});

describe("Сравнение договоров — прочерк, ноль и погашенное число (спека §2.1.2, §2.1.3)", () => {
  it("три состояния различимы в одной строке", async () => {
    renderCompare();
    await screen.findByTestId("comparison-caption");

    // 204 — число.
    expect(within(screen.getByTestId("comparison-cell-2-204")).getByText(/600\s000,00/)).toBeInTheDocument();
    // 203 — статья есть, расценена в ноль: настоящий ноль, а не прочерк.
    // Пробел перед знаком валюты — неразрывный (`formatDecimalMoney`), не обычный.
    expect(screen.getByTestId("comparison-cell-2-203").textContent).toBe("0,00 ₽");
    // 202 — статьи нет вовсе: голый прочерк.
    expect(screen.getByTestId("comparison-cell-2-202").textContent).toBe("—");
    // 201 — статья есть, но погашена ДВУМЯ причинами сразу: прочерк со словами, не голый.
    const blanked = screen.getByTestId("comparison-cell-2-201").textContent ?? "";
    expect(blanked).toContain("—");
    expect(blanked).toContain("с ошибкой");
    expect(blanked).toContain("без цены");
    expect(blanked).not.toBe("—");
  });
});

describe("Сравнение договоров — ₽/м² (спека §2.4)", () => {
  it("у договора без ТЭП ₽/м² — прочерк, а не ноль", async () => {
    renderCompare();
    await screen.findByTestId("comparison-caption");

    expect(screen.getByTestId("comparison-persqm-1-204").textContent).toBe("—");
  });
});

describe("Сравнение договоров — первая колонка закреплена (DoD 17)", () => {
  it("шапка и строки несут класс закрепления", async () => {
    renderCompare();
    await screen.findByTestId("comparison-caption");

    const headCell = screen.getByRole("columnheader", { name: "Статья классификатора" });
    expect(headCell.className).toContain("sticky");

    const bodyCell = screen.getByTestId("comparison-row-1").querySelector("th");
    expect(bodyCell?.className).toContain("sticky");
  });
});

describe("Сравнение договоров — выгрузка листа (§7.6, третий отчёт)", () => {
  it("кнопка выгрузки есть на странице и зовёт эндпоинт с ТЕМ ЖЕ режимом НДС", async () => {
    // Кнопка нужна именно здесь: выборку договоров даёт только эта страница,
    // экран отчётов её дать не может (§2.6). План не назначил кнопку ни одной
    // задаче, и без неё третий отчёт §7.6 недостижим из интерфейса.
    renderCompare(`${SELECTION}&vat_mode=net`);
    await screen.findByTestId("comparison-caption");

    const button = screen.getByRole("button", { name: "Выгрузить в Excel" });
    expect(button).toBeEnabled();

    await userEvent.click(button);

    // Обработчик MSW пишет параметры запроса в `handlerState` — тем же
    // способом, что у двух других выгрузок. Лист обязан спрашивать ТОТ ЖЕ
    // режим, что открыт на экране, иначе числа файла и экрана разойдутся.
    await waitFor(() => {
      expect(handlerState.lastReportRequest?.report).toBe("comparison");
    });
    expect(handlerState.lastReportRequest?.params).toMatchObject({
      vat_mode: "net",
      ids: "204,203,202,201",
    });
  });
});

describe("Сравнение договоров — права (спека §2.9, DoD 14)", () => {
  it("member видит страницу без ограничений", async () => {
    renderCompare(SELECTION, {
      initialUser: { id: 9, email: "member@example.com", role: "member" },
    });

    expect(await screen.findByTestId("comparison-caption")).toBeInTheDocument();
    expect(screen.getByText("Земляные работы")).toBeInTheDocument();
  });
});
