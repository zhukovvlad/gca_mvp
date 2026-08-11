import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { EstimateUploadPanel } from "./EstimateUploadPanel";
import { handlerState } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";
import type { EstimateRow } from "@/types/domain";

/**
 * Форма замены сметы — предупреждение до загрузки (спека §2.9 п. 2, задача 6).
 *
 * Панель не хранит id заменяемой сметы: развилку она узнаёт только из текста
 * 409-го ответа, а число решений — из `estimates[]` карточки, сопоставляя по
 * `amendment_no`, который пользователь ввёл в поле ДО загрузки. Отсюда и
 * форма теста: он не подставляет счётчик пропом напрямую, а доводит панель до
 * настоящего конфликта (drop → 409 → диалог) и проверяет, что предупреждение
 * внутри диалога взяло число ИМЕННО той пары, которую заменяют.
 */

/** Кладёт файл в dropzone: сам input скрыт, поэтому ищем его по типу (тот же
 * приём, что в `ContractCardPage.test.tsx` — хелперы тестов не делятся между
 * модулями по правилу проекта, поэтому здесь свой). */
async function dropXlsx(user: ReturnType<typeof userEvent.setup>) {
  const file = new File(["PKfake"], "смета.xlsx", {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  await user.upload(input, file);
}

/** `EstimateRow` — с дефолтами на полях, которых сам тест не касается: важны
 * только `amendment_no` и `category_overrides_count`, остальное — форма. */
function estimateRow(
  overrides: Pick<EstimateRow, "amendment_no" | "category_overrides_count">
): EstimateRow {
  return {
    id: overrides.amendment_no ?? 0,
    title: null,
    data_prepared_on_date: null,
    import_job_id: null,
    positions_count: 0,
    created_at: null,
    ...overrides,
  };
}

/**
 * Доводит панель до диалога замены для конкретной заменяемой пары.
 *
 * `replacing.amendment_no` — то, что пользователь вводит в поле ДО сброса
 * файла: панель узнаёт номер допсоглашения именно оттуда (`parsedAmendment`),
 * а не из пропа — пропа с id заменяемой сметы у панели нет и не может быть.
 */
async function renderUploadPanel({
  estimates,
  replacing,
}: {
  estimates: EstimateRow[];
  replacing: { amendment_no: number | null };
}) {
  handlerState.uploadOutcome = "conflict";
  const user = userEvent.setup();
  renderWithProviders(<EstimateUploadPanel contractId={100} estimates={estimates} />);

  if (replacing.amendment_no !== null) {
    await user.type(screen.getByLabelText("Номер допсоглашения"), String(replacing.amendment_no));
  }
  await dropXlsx(user);
  await screen.findByText("Смета уже загружена");
}

describe("Форма замены предупреждает об утрате ручного разноса (спека §2.9 п. 2)", () => {
  it("форма замены предупреждает об утрате разноса заменяемой сметы", async () => {
    // Счётчик берётся по amendment_no ТОЙ пары, которую заменяют.
    await renderUploadPanel({
      estimates: [
        estimateRow({ amendment_no: null, category_overrides_count: 2 }),
        estimateRow({ amendment_no: 1, category_overrides_count: 0 }),
      ],
      replacing: { amendment_no: null },
    });

    expect(screen.getByRole("alert")).toHaveTextContent(/ручной разнос/i);
    // Число закреплено рядом со словом «потеряно», а не голой цифрой: так
    // тест не мог бы зазеленеть на случайном «2» из другого места текста.
    expect(screen.getByRole("alert")).toHaveTextContent("потеряно: 2");
  });

  it("замена допсоглашения без решений молчит, даже если у исходной сметы они есть", async () => {
    // Ровно тот случай, который сломал бы источник «из паспорта»: у исходной
    // сметы решения есть, но заменяют не её.
    await renderUploadPanel({
      estimates: [
        estimateRow({ amendment_no: null, category_overrides_count: 2 }),
        estimateRow({ amendment_no: 1, category_overrides_count: 0 }),
      ],
      replacing: { amendment_no: 1 },
    });

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
