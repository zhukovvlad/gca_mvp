import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { EstimateUploadPanel } from "./EstimateUploadPanel";
import { qk } from "@/services/queryKeys";
import { handlerState } from "@/test/handlers";
import { sampleContractCard } from "@/test/fixtures";
import { createTestQueryClient, renderWithProviders } from "@/test/utils";
import type { EstimateRow } from "@/types/domain";

/**
 * Форма замены сметы — предупреждение до загрузки (спека §2.9 п. 2, задача 6).
 *
 * Панель не хранит id заменяемой сметы: развилку она узнаёт только из текста
 * 409-го ответа, а число решений — из карточки договора, сопоставляя по
 * `amendment_no`, который пользователь ввёл в поле ДО загрузки.
 *
 * Единственный источник этого числа — рефетч карточки на 409 (спека §2.9
 * п. 2, находки ревью PR #16 finding 2а и 3): панель сама вызывает
 * `useContract` и ждёт ответа `GET /contracts/:id` ПЕРЕД открытием диалога,
 * а прежнего пропа `estimates` (снимка карточки от родителя) у панели больше
 * нет — не осталось ни одного места, которое могло бы им воспользоваться без
 * риска показать непроверенное число. Поэтому тесты этого файла управляют
 * ответом сервера через `handlerState.contractCardEstimatesOverride`
 * (`renderUploadPanel` ниже), а не через проп.
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
 * `serverEstimates` — ответ мокнутого `GET /contracts/:id`, который панель
 * получит рефетчем на 409: единственный источник числа в диалоге.
 */
async function renderUploadPanel({
  serverEstimates,
  replacing,
}: {
  serverEstimates: EstimateRow[];
  replacing: { amendment_no: number | null };
}) {
  handlerState.uploadOutcome = "conflict";
  handlerState.contractCardEstimatesOverride = serverEstimates;
  const user = userEvent.setup();
  renderWithProviders(<EstimateUploadPanel contractId={100} />);

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
      serverEstimates: [
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
      serverEstimates: [
        estimateRow({ amendment_no: null, category_overrides_count: 2 }),
        estimateRow({ amendment_no: 1, category_overrides_count: 0 }),
      ],
      replacing: { amendment_no: 1 },
    });

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("Число в диалоге замены — с сервера на момент конфликта, не из устаревшего кэша (находка ревью PR #16)", () => {
  it("409 обязан обновить число ПЕРЕД тем, как открыть диалог, а не показать устаревший 0 из кэша React Query", async () => {
    // Кэш `qk.contracts.card` предзаполнен устаревшим 0 — БЕЗ сетевого
    // похода, детерминированно (`setQueryData`, тот же приём, что в
    // `queries.test.tsx`): так выглядел бы кэш карточки, если её открыли ДО
    // того, как решение появилось в другой вкладке/у другого пользователя,
    // а мутации разноса до фикса finding 2б инвалидировали запрос ПАСПОРТА,
    // не карточки. Сервер на `GET /contracts/:id` при этом честно отвечает
    // 3. Без фикса finding 2а панель использовала бы то, что уже лежит в
    // кэше `useContract` (`contractQ.data`), а не результат явного рефетча —
    // диалог показал бы устаревший 0 (или не появился бы вовсе, т.к.
    // `lostOnReplace > 0` ложно). С фиксом панель ждёт `refetch()` на 409 и
    // видит именно 3.
    const queryClient = createTestQueryClient();
    const cardKey = qk.contracts.card(100);
    // `refetchOnMount: false` держит подсаженный 0 неприкосновенным до
    // ЯВНОГО вызова `refetch()` в обработчике 409: у тестового клиента
    // `staleTime: 0`, и без этой строки автоматический рефетч ПРИ
    // МОНТИРОВАНИИ обновил бы кэш до конфликта — тест тогда прошёл бы даже
    // без исправления (панель случайно увидела бы 3 не из-за своего
    // `await contractQ.refetch()`, а из-за фонового поведения React Query).
    queryClient.setQueryDefaults(cardKey, { refetchOnMount: false });
    queryClient.setQueryData(cardKey, {
      ...sampleContractCard,
      estimates: [estimateRow({ amendment_no: null, category_overrides_count: 0 })],
    });
    handlerState.contractCardEstimatesOverride = [
      estimateRow({ amendment_no: null, category_overrides_count: 3 }),
    ];
    handlerState.uploadOutcome = "conflict";
    const user = userEvent.setup();
    renderWithProviders(<EstimateUploadPanel contractId={100} />, { queryClient });

    await dropXlsx(user);
    await screen.findByText("Смета уже загружена");

    expect(screen.getByRole("alert")).toHaveTextContent("потеряно: 3");
  });
});

describe("Упавший рефетч карточки не открывает диалог на непроверенном числе (находка ревью, finding 3)", () => {
  it("409 при упавшем GET /contracts/:id — отказ вместо диалога, повтор загрузки остаётся возможным", async () => {
    // `contractCardFails` валит рефетч 500-й ошибкой (см. `handlers.ts`).
    // TanStack Query v5 не бросает из `refetch()` по умолчанию
    // (`throwOnError: false`) — упавший запрос спокойно резолвится с
    // `isError: true`. Диалог не должен открыться НА ЭТОМ прогоне вовсе: без
    // фикса компонент подставил бы в `conflict.estimates` непроверенное
    // значение (устаревшие `fresh.data`, если карточка когда-то грузилась
    // успешно, либо прежний проп до его удаления) и всё равно открыл диалог.
    // `renderUploadPanel` здесь не годится: он ждёт заголовок диалога, а тот
    // не должен появиться.
    handlerState.contractCardFails = true;
    handlerState.uploadOutcome = "conflict";
    const user = userEvent.setup();
    renderWithProviders(<EstimateUploadPanel contractId={100} />);

    await dropXlsx(user);

    /*
      Ждать надо СИГНАЛА, ОБЩЕГО ДЛЯ ОБЕИХ ВЕТОК, и это не педантизм — на этом
      месте тест уже один раз обещал больше, чем делал (замечание ревью P3).

      Утверждение об ОТСУТСТВИИ чего-либо бессмысленно, пока поток не завершён:
      сразу после `dropXlsx` диалога нет просто потому, что запрос ещё летит.
      Значит нужен положительный признак «поток осел». Ждать появления отказа
      нельзя: при снятой защите отказа не будет вовсе, прогон умрёт здесь же —
      на утверждении о ТУПИКЕ, — и до главного, об отсутствии диалога, не
      дойдёт никогда. Ровно этим прошлая редакция и была слабее, чем заявляла.

      Счётчика запросов в MSW тоже мало: обработчик отвечает РАНЬШЕ, чем React
      успевает перерисоваться, и проверка отсутствия прошла бы вхолостую, в
      зазоре между ответом сервера и обновлением состояния.

      Поэтому ждём ЛЮБОЙ из двух возможных развязок — отказ или диалог, — и
      только потом утверждаем, какая из них наступила. При снятой защите
      waitFor разрешится диалогом, и упадёт следующая строка: та самая, что
      называет вред.
    */
    await waitFor(() => {
      const settled =
        screen.queryByTestId("upload-rejection") ??
        screen.queryByRole("alertdialog") ??
        screen.queryByText("Смета уже загружена");
      expect(settled).not.toBeNull();
    });

    expect(screen.queryByText("Смета уже загружена")).not.toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

    const refusal = screen.getByTestId("upload-rejection");
    expect(refusal).toHaveTextContent(/провер.*не удалось/i);
    expect(refusal).toHaveTextContent(/повторите загрузку/i);
  });
});
