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
 *
 * После находки ревью PR #16 источник этого числа — рефетч карточки на 409
 * (§2.9 п. 2), а не проп `estimates` напрямую: панель сама вызывает
 * `useContract` и ждёт ответа `GET /contracts/:id` ПЕРЕД открытием диалога.
 * `renderUploadPanel` поэтому держит ДВА независимых источника — `estimates`
 * (проп, как его увидел бы компонент от родителя) и `serverEstimates` (что
 * мокнутый сервер отдаёт на `GET /contracts/:id`), по умолчанию равные, чтобы
 * существующие тесты этого файла не знали о разнице. Сценарий находки задаёт
 * их РАЗНЫМИ намеренно — см. `serverEstimates` в третьем блоке ниже.
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
 *
 * `serverEstimates` — ответ мокнутого `GET /contracts/:id` на момент 409;
 * по умолчанию равен `estimates` (проп и сервер согласны), чтобы тесты, не
 * знающие о рефетче на конфликте, продолжали проверять то же самое, что и
 * раньше. Тест на находку ревью передаёт его отдельно.
 */
async function renderUploadPanel({
  estimates,
  replacing,
  serverEstimates = estimates,
}: {
  estimates: EstimateRow[];
  replacing: { amendment_no: number | null };
  serverEstimates?: EstimateRow[];
}) {
  handlerState.uploadOutcome = "conflict";
  handlerState.contractCardEstimatesOverride = serverEstimates;
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

describe("Число в диалоге замены — с сервера на момент конфликта, не из устаревшего кэша (находка ревью PR #16)", () => {
  it("409 обязан обновить число ПЕРЕД тем, как открыть диалог, а не показать устаревший 0 из пропа", async () => {
    // `estimates` (проп) — то же самое, что ЗАМЕНЯЕМАЯ панель получила бы от
    // родителя, у которого карточка договора закэширована со старым 0
    // (решение только что появилось в другой вкладке/у другого пользователя,
    // и мутации разноса инвалидируют запрос ПАСПОРТА, не карточки, до фикса
    // finding 2б). `serverEstimates` — то, что сервер на `GET /contracts/:id`
    // честно отдаёт В МОМЕНТ конфликта: 3, а не 0. Без фикса finding 2а
    // панель ни разу не рефетчит карточку и подставляет в диалог устаревший 0
    // из пропа, из-за чего условие `lostOnReplace > 0` ложно и диалог
    // предупреждения не покажет ВООБЩЕ — то есть `getByRole("alert")` ниже
    // упадёт «не найдено», а не только с неверным числом.
    await renderUploadPanel({
      estimates: [estimateRow({ amendment_no: null, category_overrides_count: 0 })],
      serverEstimates: [estimateRow({ amendment_no: null, category_overrides_count: 3 })],
      replacing: { amendment_no: null },
    });

    expect(screen.getByRole("alert")).toHaveTextContent("потеряно: 3");
  });
});
