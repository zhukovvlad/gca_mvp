import { QueryClient } from "@tanstack/react-query";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { qk } from "@/services/queryKeys";
import { server } from "@/test/server";
import { SCHEMA_FAMILY_ID, handlerState, schemaParameter, schemaValue } from "@/test/handlers";
import { createTestQueryClient, renderWithProviders } from "@/test/utils";
import type { PreviewTarget } from "@/types/domain";

import { PreviewDialog } from "./PreviewDialog";

/**
 * Общий диалог трёх действий, ставящих задания (спека semantic-suggestions
 * §2.10): перезапрос единицы, перезапрос по конфигурации, удержанная пачка.
 * Подтверждение шлёт `preview_hash` из ПОКАЗАННОГО preview; `409
 * preview_changed` — сообщение и новый preview, а не молчаливое повторение.
 */

const UNIT: PreviewTarget = { kind: "unit", unitId: 5, unitCode: "M2" };

function renderDialog(target: PreviewTarget, onClose = vi.fn(), unitLabel = "м²") {
  renderWithProviders(<PreviewDialog target={target} unitLabel={unitLabel} onClose={onClose} />);
  return onClose;
}

describe("PreviewDialog — что показывает", () => {
  it("открытие запрашивает preview и показывает число контекстов, резерв и ожидаемую цену", async () => {
    renderDialog(UNIT);

    expect(await screen.findByText("214")).toBeInTheDocument();
    expect(screen.getByText("$1,90")).toBeInTheDocument();
    expect(screen.getByText("≈ $0,34")).toBeInTheDocument();
    expect(screen.getByText("Перезапросить м²?")).toBeInTheDocument();
    expect(handlerState.previewRequests).toEqual(["unit:5"]);
  });

  it("деньги preview выводятся из строк без округления через Number", async () => {
    server.use(
      http.post("/api/v1/semantic/unit-reask/preview", () =>
        HttpResponse.json({
          context_count: 3,
          reserve_usd: "12345678901234567.89",
          expected_cached_usd: "0.1000000000000000055511",
          preview_hash: "h",
        })
      )
    );
    renderDialog(UNIT);

    // Number("12345678901234567.89") = 12345678901234568; getByText сворачивает NBSP в пробел.
    expect(await screen.findByText("$12 345 678 901 234 567,89")).toBeInTheDocument();
    expect(screen.getByText("≈ $0,10")).toBeInTheDocument();
  });

  it("до ответа сервера подтвердить нельзя", async () => {
    server.use(
      http.post("/api/v1/semantic/unit-reask/preview", async () => {
        await delay(150);
        return HttpResponse.json({
          context_count: 1,
          reserve_usd: "1",
          expected_cached_usd: "1",
          preview_hash: "h",
        });
      })
    );
    renderDialog(UNIT);

    expect(await screen.findByRole("button", { name: "Поставить в очередь" })).toBeDisabled();
    await waitFor(() => expect(screen.getByRole("button", { name: "Поставить в очередь" })).toBeEnabled());
  });

  it("закрытый диалог (target = null) ничего не запрашивает", () => {
    renderWithProviders(<PreviewDialog target={null} onClose={vi.fn()} />);
    expect(handlerState.previewRequests).toEqual([]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("PreviewDialog — три цели", () => {
  it("перезапрос единицы: подтверждение шлёт unit_id и preview_hash из показанного preview", async () => {
    const user = userEvent.setup();
    const onClose = renderDialog(UNIT);

    await screen.findByText("214");
    await user.click(screen.getByRole("button", { name: "Поставить в очередь" }));

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(handlerState.reaskConfirmRequests).toEqual([
      { path: "/unit-reask", body: { unit_id: 5, preview_hash: "preview-hash-1" } },
    ]);
    expect(await screen.findByText("Задания поставлены в очередь.")).toBeInTheDocument();
  });

  it("единица «без единицы» (unit_id = null) уходит как null, а не пропадает из тела", async () => {
    const user = userEvent.setup();
    renderDialog({ kind: "unit", unitId: null, unitCode: null }, vi.fn(), "без единицы");

    await screen.findByText("214");
    await user.click(screen.getByRole("button", { name: "Поставить в очередь" }));

    await waitFor(() => expect(handlerState.reaskConfirmRequests).toHaveLength(1));
    expect(handlerState.previewRequests).toEqual(["unit:null"]);
    expect(handlerState.reaskConfirmRequests[0].body).toEqual({ unit_id: null, preview_hash: "preview-hash-1" });
  });

  it("перезапрос по конфигурации: свои маршруты preview и подтверждения", async () => {
    const user = userEvent.setup();
    renderDialog({ kind: "config" });

    expect(await screen.findByText("Перезапросить всё?")).toBeInTheDocument();
    await screen.findByText("214");
    await user.click(screen.getByRole("button", { name: "Поставить в очередь" }));

    await waitFor(() => expect(handlerState.reaskConfirmRequests).toHaveLength(1));
    expect(handlerState.previewRequests).toEqual(["config"]);
    expect(handlerState.reaskConfirmRequests[0]).toEqual({
      path: "/reask-all",
      body: { preview_hash: "preview-hash-1" },
    });
  });

  it("удержанная пачка: preview и approve по id пачки, источник в описании", async () => {
    const user = userEvent.setup();
    renderDialog({ kind: "batch", batchId: 7, source: "import" });

    expect(await screen.findByText("Поставить удержанную пачку?")).toBeInTheDocument();
    expect(screen.getByText("Импорт сметы")).toBeInTheDocument();
    await screen.findByText("214");
    await user.click(screen.getByRole("button", { name: "Поставить" }));

    await waitFor(() => expect(handlerState.reaskConfirmRequests).toHaveLength(1));
    expect(handlerState.previewRequests).toEqual(["batch:7"]);
    expect(handlerState.reaskConfirmRequests[0]).toEqual({
      path: "/batches/7/approve",
      body: { preview_hash: "preview-hash-1" },
    });
    expect(await screen.findByText("Пачка поставлена в очередь.")).toBeInTheDocument();
  });

  it("удержанная пачка: пояснение про отпечатки — под числами, как в макете", async () => {
    renderDialog({ kind: "batch", batchId: 7, source: "import" });

    const reserve = await screen.findByText("$1,90");
    const note = screen.getByText(/Задания ставятся по текущим отпечаткам входа/);
    expect(reserve.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe("PreviewDialog — 409 preview_changed", () => {
  it("сообщает «оценка изменилась», запрашивает preview заново и НЕ повторяет подтверждение молча", async () => {
    const user = userEvent.setup();
    const onClose = renderDialog(UNIT);
    handlerState.reaskConflictsLeft = 1;

    await screen.findByText("$1,90");
    await user.click(screen.getByRole("button", { name: "Поставить в очередь" }));

    // Новые числа второго preview на экране, сообщение показано.
    expect(await screen.findByText("$2,90")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Оценка изменилась");
    expect(handlerState.previewRequests).toEqual(["unit:5", "unit:5"]);
    // Подтверждение ушло ровно один раз (с прежним hash), автоповтора нет, диалог открыт.
    expect(handlerState.reaskConfirmRequests).toEqual([
      { path: "/unit-reask", body: { unit_id: 5, preview_hash: "preview-hash-1" } },
    ]);
    expect(onClose).not.toHaveBeenCalled();
    // Отказ показан сообщением диалога, а не ещё и тостом с текстом сервера.
    expect(screen.queryByText("Оценка изменилась, откройте preview заново.")).not.toBeInTheDocument();
  });

  it("после закрытия и нового открытия прежнего сообщения «оценка изменилась» нет", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const { rerender } = renderWithProviders(
      <PreviewDialog target={UNIT} unitLabel="м²" onClose={onClose} />
    );
    handlerState.reaskConflictsLeft = 1;

    await screen.findByText("$1,90");
    await user.click(screen.getByRole("button", { name: "Поставить в очередь" }));
    await screen.findByText("$2,90");
    expect(screen.getByRole("status")).toHaveTextContent("Оценка изменилась");
    await user.click(screen.getByRole("button", { name: "Отмена" }));
    rerender(<PreviewDialog target={null} onClose={onClose} />);
    rerender(<PreviewDialog target={{ kind: "config" }} onClose={onClose} />);

    expect(await screen.findByText("$3,90")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("новое открытие не показывает числа прежнего preview, пока не пришёл свой", async () => {
    const onClose = vi.fn();
    const { rerender } = renderWithProviders(
      <PreviewDialog target={UNIT} unitLabel="м²" onClose={onClose} />
    );
    await screen.findByText("$1,90");
    server.use(
      http.post("/api/v1/semantic/reask-all/preview", async () => {
        await delay(150);
        return HttpResponse.json({
          context_count: 9,
          reserve_usd: "7.77",
          expected_cached_usd: "1",
          preview_hash: "h-config",
        });
      })
    );

    rerender(<PreviewDialog target={null} onClose={onClose} />);
    rerender(<PreviewDialog target={{ kind: "config" }} onClose={onClose} />);

    // Проверка — сразу после рендера, до ожидания: первый кадр нового открытия
    // уже не несёт чисел и hash прежнего preview (их нельзя подтвердить).
    expect(screen.getByText("Перезапросить всё?")).toBeInTheDocument();
    expect(screen.queryByText("$1,90")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Поставить в очередь" })).toBeDisabled();
    expect(await screen.findByText("$7,77")).toBeInTheDocument();
  });

  it("preview не получен — подтвердить нельзя, сообщение об ошибке", async () => {
    server.use(
      http.post("/api/v1/semantic/unit-reask/preview", () => new HttpResponse(null, { status: 500 }))
    );
    renderDialog(UNIT);

    expect(await screen.findByText(/Не удалось получить оценку/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Поставить в очередь" })).toBeDisabled();
  });

  it("другой отказ перечитывает очередь и сводку: экран мог устареть", async () => {
    const user = userEvent.setup();
    const queryClient = createTestQueryClient();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    renderWithProviders(<PreviewDialog target={UNIT} unitLabel="м²" onClose={vi.fn()} />, { queryClient });
    server.use(
      http.post("/api/v1/semantic/unit-reask", () =>
        HttpResponse.json({ detail: { code: "job_changed", message: "Задание изменилось." } }, { status: 409 })
      )
    );

    await screen.findByText("$1,90");
    await user.click(screen.getByRole("button", { name: "Поставить в очередь" }));

    await screen.findByText("Задание изменилось.");
    const keys = invalidate.mock.calls.map((call) => JSON.stringify((call[0] as { queryKey: unknown }).queryKey));
    expect(keys).toEqual(expect.arrayContaining([JSON.stringify(["semantic-queue","suggestions"]), JSON.stringify(["semantic-queue","jobs"]), JSON.stringify(["semantic-queue","status"])]));
  });

  it("повторное подтверждение после нового preview шлёт НОВЫЙ hash", async () => {
    const user = userEvent.setup();
    const onClose = renderDialog(UNIT);
    handlerState.reaskConflictsLeft = 1;

    await screen.findByText("$1,90");
    await user.click(screen.getByRole("button", { name: "Поставить в очередь" }));
    await screen.findByText("$2,90");
    await user.click(await screen.findByRole("button", { name: "Поставить в очередь" }));

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(handlerState.reaskConfirmRequests.map((r) => r.body.preview_hash)).toEqual([
      "preview-hash-1",
      "preview-hash-2",
    ]);
  });

  it("другой отказ (не preview_changed) не запрашивает preview заново и не закрывает диалог", async () => {
    const user = userEvent.setup();
    const onClose = renderDialog(UNIT);
    server.use(
      http.post("/api/v1/semantic/unit-reask", () =>
        HttpResponse.json({ detail: { code: "job_changed", message: "Задание изменилось." } }, { status: 409 })
      )
    );

    await screen.findByText("$1,90");
    await user.click(screen.getByRole("button", { name: "Поставить в очередь" }));

    expect(await screen.findByText("Задание изменилось.")).toBeInTheDocument();
    expect(handlerState.previewRequests).toEqual(["unit:5"]);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe("PreviewDialog — отмена", () => {
  it("«Отмена» закрывает диалог и ничего не подтверждает", async () => {
    const user = userEvent.setup();
    const onClose = renderDialog(UNIT);

    await screen.findByText("214");
    await user.click(screen.getByRole("button", { name: "Отмена" }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(handlerState.reaskConfirmRequests).toEqual([]);
  });
});

describe("PreviewDialog — пересборка схемы: оценка приближённая (спека 3в §2.8)", () => {
  const SCHEMA: PreviewTarget = { kind: "schema", familyId: SCHEMA_FAMILY_ID };

  it("values_approximate: пометка стоит рядом с суммой, кода на экране нет", async () => {
    renderDialog(SCHEMA);

    expect(await screen.findByText(/Оценка значений приближённая/)).toBeInTheDocument();
    expect(screen.getByText(/считаются нынешние параметры модели/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("values_approximate");
  });

  it("ответ без values_approximate (или ложь) пометки не даёт", async () => {
    server.use(
      http.post("/api/v1/semantic/families/:id/schema/rebuild/preview", () =>
        HttpResponse.json({
          context_count: 3,
          reserve_usd: "1.0000",
          expected_cached_usd: "0.2000",
          preview_hash: "h",
          values_included: true,
          values_approximate: false,
        })
      )
    );
    renderDialog(SCHEMA);

    await screen.findByText("$1,00");
    expect(screen.queryByText(/Оценка значений приближённая/)).not.toBeInTheDocument();
  });

  it("у перезапроса единицы пометки нет", async () => {
    renderDialog(UNIT);

    await screen.findByText("214");
    expect(screen.queryByText(/Оценка значений приближённая/)).not.toBeInTheDocument();
  });
});

describe("PreviewDialog — пересборка схемы: что останется и что заменится (спека 3в §2.8)", () => {
  const SCHEMA: PreviewTarget = { kind: "schema", familyId: SCHEMA_FAMILY_ID };
  const SCHEMA_URL = "/api/v1/semantic/families/:id/schema";

  function setParameters(parameters: ReturnType<typeof schemaParameter>[]) {
    handlerState.familySchemas[SCHEMA_FAMILY_ID] = {
      ...handlerState.familySchemas[SCHEMA_FAMILY_ID],
      parameters,
    };
  }

  function human(id: number, name: string, extra: Partial<Parameters<typeof schemaParameter>[4]> = {}) {
    return schemaParameter(id, id, name, [schemaValue(id * 10, "x")], { origin: "human", ...extra });
  }

  function modelParam(id: number, name: string, values: ReturnType<typeof schemaValue>[]) {
    return schemaParameter(id, id, name, values);
  }

  it("обе группы: ваши — имя, роль, вид, пометка «ваш»; заменятся — имя, число значений без слитых, пометка «модели»", async () => {
    setParameters([
      human(1, "Тип гидроизоляции"),
      human(2, "Толщина, мм", { role: "secondary", value_kind: "range" }),
      modelParam(3, "Количество слоёв", [
        schemaValue(31, "1", "schema", { rows: 40 }),
        schemaValue(32, "2", "schema", { rows: 22 }),
        schemaValue(33, "два", "extension", { merged_into_id: 32 }),
      ]),
    ]);
    renderDialog(SCHEMA);

    const mine = await screen.findByRole("region", { name: "Останутся — ваши" });
    expect(mine).toHaveTextContent("Тип гидроизоляции · главный · точные");
    expect(mine).toHaveTextContent("Толщина, мм · второстепенный · диапазоны");
    expect(mine.textContent?.match(/ваш/g)).toHaveLength(3); // заголовок группы и две пометки
    const replaced = screen.getByRole("region", { name: "Заменятся — параметры модели, которые никто не правил" });
    expect(replaced).toHaveTextContent("Количество слоёв · 2 значения · 62 строки");
    // Пометка — сама плашка: «модели» есть и в заголовке группы, поэтому toHaveTextContent её не отличает.
    expect(within(replaced).getByText("модели")).toBeInTheDocument();
    // Группы не пересекаются: параметр человека не числится среди заменяемых и наоборот.
    expect(replaced).not.toHaveTextContent("Тип гидроизоляции");
    expect(replaced).not.toHaveTextContent("Толщина, мм");
    expect(mine).not.toHaveTextContent("Количество слоёв");
    expect(screen.getByText("Модель предложит до 3 новых параметров: занято 2 из 10.")).toBeInTheDocument();
    expect(screen.getByText(/Ваши параметры она увидит в запросе и не будет повторять/)).toBeInTheDocument();
  });

  it("«Что произойдёт» — рамка с заголовком: строка про параметры, контексты, деньги и пометка приближённости внутри неё", async () => {
    setParameters([human(1, "Тип гидроизоляции")]);
    renderDialog(SCHEMA);

    const box = await screen.findByRole("region", { name: "Что произойдёт" });
    expect(within(box).getByRole("heading", { name: "Что произойдёт" })).toBeInTheDocument();
    expect(box).toHaveTextContent("Модель предложит до 3 новых параметров: занято 1 из 10.");
    expect(await within(box).findByText("$1,90")).toBeInTheDocument();
    expect(box).toHaveTextContent("Контекстов");
    expect(box).toHaveTextContent("Оценка значений приближённая");
    // Строка про параметры и группы — не внутри рамки друг друга.
    expect(within(box).queryByText("Тип гидроизоляции")).not.toBeInTheDocument();
  });

  it("значения и строки у параметров модели склоняются: 1, 5, 21", async () => {
    const values = (base: number, n: number) =>
      Array.from({ length: n }, (_, i) => schemaValue(base + i, `v${i}`, "schema", { rows: 1 }));
    setParameters([
      modelParam(1, "Один", values(100, 1)),
      modelParam(2, "Пять", values(200, 5)),
      modelParam(3, "Двадцать один", values(300, 21)),
    ]);
    renderDialog(SCHEMA);

    const replaced = await screen.findByRole("region", { name: /Заменятся/ });
    // Текст строки целиком без плашки: подстрока «5 строк» прошла бы и при ошибочном «5 строка».
    const lines = within(replaced)
      .getAllByRole("listitem")
      .map((item) => item.textContent?.replace(/модели$/, ""));
    expect(lines).toEqual([
      "Один · 1 значение · 1 строка",
      "Пять · 5 значений · 5 строк",
      "Двадцать один · 21 значение · 21 строка",
    ]);
  });

  it("число строк — с разделителем разрядов, как в окне правки: «1 117 строк»", async () => {
    setParameters([modelParam(1, "Количество слоёв", [schemaValue(100, "1", "schema", { rows: 1117 })])]);
    renderDialog(SCHEMA);

    const replaced = await screen.findByRole("region", { name: /Заменятся/ });
    expect(replaced.textContent).toMatch(/1\u00a0117 строк/);
  });

  it("оценка не получена, схема есть: рамка «Что произойдёт» со строкой про параметры остаётся, чисел оценки нет", async () => {
    server.use(
      http.post("/api/v1/semantic/families/:id/schema/rebuild/preview", () => new HttpResponse(null, { status: 500 }))
    );
    renderDialog(SCHEMA);

    expect(await screen.findByText(/Не удалось получить оценку/)).toBeInTheDocument();
    const box = await screen.findByRole("region", { name: "Что произойдёт" });
    expect(box).toHaveTextContent("Модель предложит до 3 новых параметров: занято 0 из 10.");
    expect(box).not.toHaveTextContent("Контекстов");
    expect(screen.getByRole("button", { name: "Пересобрать" })).toBeDisabled();
  });

  it("у других целей рамки «Что произойдёт» нет", async () => {
    renderDialog(UNIT);

    await screen.findByText("214");
    expect(screen.queryByRole("region", { name: "Что произойдёт" })).not.toBeInTheDocument();
  });

  it("у других целей строки «занято N из 10» нет, даже если в кэше ключа выключенного запроса схемы есть данные", async () => {
    // Выключенный запрос всё равно отдаёт данные из кэша своего ключа: строка обязана
    // зависеть от цели окна, а не от того, пришли ли данные.
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } },
    });
    queryClient.setQueryData(qk.workFamilies.schema(0), {
      ...handlerState.familySchemas[SCHEMA_FAMILY_ID],
      family_id: 0,
      parameters: Array.from({ length: 9 }, (_, i) => human(i + 1, `Параметр ${i + 1}`)),
    });
    renderWithProviders(<PreviewDialog target={UNIT} unitLabel="м²" onClose={vi.fn()} />, { queryClient });

    await screen.findByText("214");
    expect(screen.queryByText(/занято \d+ из 10/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Модель предложит/)).not.toBeInTheDocument();
  });

  it("параметров человека нет: группы «Останутся» нет, про запрос с их параметрами ничего не сказано", async () => {
    renderDialog(SCHEMA);

    await screen.findByRole("region", { name: /Заменятся/ });
    expect(screen.queryByRole("region", { name: "Останутся — ваши" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Ваши параметры она увидит/)).not.toBeInTheDocument();
    expect(screen.getByText("Модель предложит до 3 новых параметров: занято 0 из 10.")).toBeInTheDocument();
  });

  it("параметров модели нет: группы «Заменятся» нет", async () => {
    setParameters([human(1, "Тип гидроизоляции")]);
    renderDialog(SCHEMA);

    await screen.findByRole("region", { name: "Останутся — ваши" });
    expect(screen.queryByRole("region", { name: /Заменятся/ })).not.toBeInTheDocument();
  });

  it("предел ответа модели — min(3, 10 − H): при H = 8 до 2 параметров", async () => {
    setParameters(Array.from({ length: 8 }, (_, i) => human(i + 1, `Параметр ${i + 1}`)));
    renderDialog(SCHEMA);

    expect(await screen.findByText("Модель предложит до 2 новых параметров: занято 8 из 10.")).toBeInTheDocument();
  });

  it("при H = 9 место одно: «не больше одного нового параметра», а не «до 1 новых параметров»", async () => {
    setParameters(Array.from({ length: 9 }, (_, i) => human(i + 1, `Параметр ${i + 1}`)));
    renderDialog(SCHEMA);

    expect(
      await screen.findByText("Модель предложит не больше одного нового параметра: занято 9 из 10.")
    ).toBeInTheDocument();
    expect(screen.queryByText(/до 1 новых/)).not.toBeInTheDocument();
  });

  it("H — только параметры человека: параметры модели место в пределе не занимают (8 ваших и 2 модели — до 2)", async () => {
    setParameters([
      ...Array.from({ length: 8 }, (_, i) => human(i + 1, `Параметр ${i + 1}`)),
      modelParam(9, "Количество слоёв", [schemaValue(91, "1")]),
      modelParam(10, "Тип основания", [schemaValue(101, "бетон")]),
    ]);
    renderDialog(SCHEMA);

    expect(await screen.findByText("Модель предложит до 2 новых параметров: занято 8 из 10.")).toBeInTheDocument();
  });

  it("H = 10: модель параметров не получит, «до N» не обещается", async () => {
    setParameters(Array.from({ length: 10 }, (_, i) => human(i + 1, `Параметр ${i + 1}`)));
    renderDialog(SCHEMA);

    expect(await screen.findByText("Модель новых параметров не предложит: занято 10 из 10.")).toBeInTheDocument();
    expect(screen.getByText(/Схема соберётся заново из ваших параметров; модель новых параметров не получит: все 10 мест заняты/)).toBeInTheDocument();
    // Модель не вызывается: фраза «Модель построит свою часть» была бы ложью.
    expect(document.body.textContent).not.toContain("Модель построит свою часть");
    expect(screen.queryByText(/Модель предложит до/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Ваши параметры она увидит/)).not.toBeInTheDocument();
  });

  it("прежний текст «заново составит параметры и значения схемы» не показывается", async () => {
    setParameters([human(1, "Тип гидроизоляции")]);
    renderDialog(SCHEMA);

    await screen.findByRole("region", { name: "Останутся — ваши" });
    expect(document.body.textContent).not.toContain("заново составит параметры и значения");
    expect(screen.getByText(/Модель построит свою часть схемы заново/)).toBeInTheDocument();
  });

  it("пока схема грузится: групп и строки про «занято» нет, подтвердить по готовому предпросмотру можно", async () => {
    server.use(
      http.get(SCHEMA_URL, async () => {
        await delay(400);
        return HttpResponse.json(handlerState.familySchemas[SCHEMA_FAMILY_ID]);
      })
    );
    renderDialog(SCHEMA);

    await screen.findByText("$1,90");
    expect(screen.queryByRole("region", { name: /Останутся|Заменятся/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/занято \d+ из 10/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Пересобрать" })).toBeEnabled();
    expect(await screen.findByRole("region", { name: /Заменятся/ })).toBeInTheDocument();
  });

  it("схема не получена: подпись состояния, а не пустые группы; «Пересобрать» доступна", async () => {
    server.use(http.get(SCHEMA_URL, () => new HttpResponse(null, { status: 500 })));
    renderDialog(SCHEMA);

    expect(await screen.findByText(/Не удалось получить схему семьи/)).toBeInTheDocument();
    await screen.findByText("$1,90");
    expect(screen.queryByRole("region", { name: /Останутся|Заменятся/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/занято \d+ из 10/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Пересобрать" })).toBeEnabled();
  });

  it("у других целей схема не запрашивается и групп нет", async () => {
    let schemaGets = 0;
    server.use(
      http.get(SCHEMA_URL, () => {
        schemaGets += 1;
        return HttpResponse.json(handlerState.familySchemas[SCHEMA_FAMILY_ID]);
      })
    );
    renderDialog(UNIT);

    await screen.findByText("214");
    expect(schemaGets).toBe(0);
    expect(screen.queryByRole("region", { name: /Останутся|Заменятся/ })).not.toBeInTheDocument();
    // Выключенный запрос схемы вечно «грузится»: блок групп у чужой цели остался бы скелетом навсегда.
    expect(document.querySelector(".animate-pulse")).toBeNull();
  });
});
