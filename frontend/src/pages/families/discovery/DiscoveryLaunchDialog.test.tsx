import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { discoveryUnitFixture, handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";

import { DiscoveryLaunchDialog, type DiscoveryTarget } from "./DiscoveryLaunchDialog";

/**
 * Окно запуска открытия семей (экран 2 макета): preview → `preview_hash` → запуск. Единица —
 * M3 (id 3), «м³»; её строка блока — фикстура `discoveryUnitFixture`.
 */

const TARGET: DiscoveryTarget = { unitId: 3, unitLabel: "м³" };

function nextPreviewBody() {
  return {
    unit_id: 3,
    counts: { systems: 5, new_family: 0, bare: 0, names: 4, uncategorized_families: 0 },
    active_families: 2,
    reserve_usd: "0.9",
    expected_cached_usd: "0.6",
    preview_hash: "gated-hash",
  };
}

function renderDialog(onClose = vi.fn(), target: DiscoveryTarget | null = TARGET) {
  renderWithProviders(<DiscoveryLaunchDialog target={target} onClose={onClose} />);
  return onClose;
}

describe("DiscoveryLaunchDialog", () => {
  it("показывает числа охвата, активные семьи, что уходит наружу и оценку", async () => {
    handlerState.discovery.units = [
      discoveryUnitFixture({ systems: 1382, new_family: 40, bare: 0, uncategorized_families: 3 }),
    ];
    renderDialog();

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Открыть семьи · м³" })).toBeInTheDocument();
    await within(dialog).findByText(/1\s382/);
    expect(within(dialog).getByText("Систем без семьи")).toBeInTheDocument();
    expect(within(dialog).getByText("Строк без семьи")).toBeInTheDocument();
    expect(within(dialog).getByText("40")).toBeInTheDocument();
    expect(within(dialog).getByText("Семей без категории")).toBeInTheDocument();
    expect(within(dialog).getByText(/Активных семей единицы/)).toBeInTheDocument();
    expect(within(dialog).getByText(/— модель их видит/)).toBeInTheDocument();
    expect(
      within(dialog).getByText("имена, статьи, пути разделов, активные семьи и категории")
    ).toBeInTheDocument();
    expect(within(dialog).getByText("$0,90")).toBeInTheDocument();
    expect(within(dialog).getByText("≈ $0,60")).toBeInTheDocument();
    expect(handlerState.discovery.previewRequests).toEqual([3]);
    // Запуск без подтверждения не идёт.
    expect(handlerState.discovery.launchRequests).toEqual([]);
  });

  it("строки охвата, которых в единице нет, не печатаются нулями", async () => {
    handlerState.discovery.units = [
      discoveryUnitFixture({ systems: 0, new_family: 12, bare: 0, uncategorized_families: 0 }),
    ];
    renderDialog();

    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("Строк без семьи");
    expect(within(dialog).queryByText("Систем без семьи")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("Семей без категории")).not.toBeInTheDocument();
  });

  it("запуск шлёт preview_hash из показанного preview, закрывает окно и сообщает о запуске", async () => {
    const user = userEvent.setup();
    handlerState.discovery.units = [discoveryUnitFixture()];
    const onClose = renderDialog();

    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("$0,90");
    await user.click(within(dialog).getByRole("button", { name: "Открыть семьи" }));

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(handlerState.discovery.launchRequests).toEqual([
      { unit_id: 3, preview_hash: "discovery-hash-1" },
    ]);
    expect(await screen.findByText("Открытие семей для единицы «м³» запущено.")).toBeInTheDocument();
  });

  it("кнопка запуска недоступна, пока preview не пришёл", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post("/api/v1/semantic/discovery/preview", async () => {
        await gate;
        return HttpResponse.json(nextPreviewBody());
      })
    );
    renderDialog();

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Открыть семьи" })).toBeDisabled();
    release();
    await within(dialog).findByText("$0,90");
    expect(within(dialog).getByRole("button", { name: "Открыть семьи" })).toBeEnabled();
  });

  it("409 preview_changed: подпись, preview читается заново, запуск не повторяется молча", async () => {
    const user = userEvent.setup();
    handlerState.discovery.units = [discoveryUnitFixture()];
    handlerState.discovery.launchConflictsLeft = 1;
    const onClose = renderDialog();

    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("$0,90");
    // Охват между показом и запуском изменился: новое preview покажет другой резерв.
    handlerState.discovery.units[0].reserve_usd = "1.5";
    await user.click(within(dialog).getByRole("button", { name: "Открыть семьи" }));

    expect(await within(dialog).findByRole("status")).toHaveTextContent(
      "Оценка изменилась, пока окно было открыто."
    );
    expect(await within(dialog).findByText("$1,50")).toBeInTheDocument();
    expect(handlerState.discovery.previewRequests).toEqual([3, 3]);
    // Ревью задачи 6: 409 preview_changed разбирает окно — общего тоста ошибки поверх подписи нет.
    expect(document.querySelectorAll("[data-sonner-toast]")).toHaveLength(0);
    // Ровно один запуск: молчаливого повтора со старым или новым хэшем не было.
    expect(handlerState.discovery.launchRequests).toHaveLength(1);
    expect(onClose).not.toHaveBeenCalled();

    // Человек видит новые числа и подтверждает сам: уходит хэш НОВОГО preview.
    await user.click(within(dialog).getByRole("button", { name: "Открыть семьи" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(handlerState.discovery.launchRequests).toHaveLength(2);
    expect(handlerState.discovery.launchRequests[1].preview_hash).toBe("discovery-hash-2");
  });

  it("отказ запуска с кодом печатается подписью из таблицы отказов, окно остаётся открытым", async () => {
    const user = userEvent.setup();
    handlerState.discovery.units = [discoveryUnitFixture()];
    handlerState.discovery.refusal = {
      action: "launch",
      code: "discovery_nothing_to_do",
      status: 409,
    };
    const onClose = renderDialog();

    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("$0,90");
    await user.click(within(dialog).getByRole("button", { name: "Открыть семьи" }));

    expect(
      await screen.findByText(
        "В единице «м³» нет строк без семьи и семей без категории — открывать нечего."
      )
    ).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(handlerState.discovery.previewRequests).toEqual([3]);
  });

  it("«Отмена» закрывает окно без запуска", async () => {
    const user = userEvent.setup();
    handlerState.discovery.units = [discoveryUnitFixture()];
    const onClose = renderDialog();

    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("$0,90");
    await user.click(within(dialog).getByRole("button", { name: "Отмена" }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(handlerState.discovery.launchRequests).toEqual([]);
  });

  it("без цели окно закрыто и preview не запрашивается", () => {
    renderDialog(vi.fn(), null);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(handlerState.discovery.previewRequests).toEqual([]);
  });
});
