import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "@/test/server";
import { handlerState } from "@/test/handlers";
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
