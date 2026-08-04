import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import ContractCardPage from "./ContractCardPage";
import { JOB_POLL_INTERVAL_MS } from "@/services/jobPolling";
import { handlerState } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";

function renderCard(options?: Parameters<typeof renderWithProviders>[1]) {
  return renderWithProviders(
    <Routes>
      <Route path="/contracts/:contractId" element={<ContractCardPage />} />
    </Routes>,
    { initialRoute: "/contracts/100", ...options }
  );
}

/** Кладёт файл в dropzone: сам input скрыт, поэтому ищем его по типу. */
async function dropXlsx(user: ReturnType<typeof userEvent.setup>) {
  const file = new File(["PKfake"], "смета.xlsx", {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  await user.upload(input, file);
}

describe("Карточка договора (§7.1)", () => {
  it("показывает реквизиты, класс и сумму строкой", async () => {
    renderCard();

    expect(await screen.findByRole("heading", { name: "ГП-2026-001" })).toBeInTheDocument();
    expect(screen.getByText("Иванов И.И.")).toBeInTheDocument();
    expect(screen.getByText(/1\s234\s567\s890,12/)).toBeInTheDocument();
  });

  it("показывает загруженную смету", async () => {
    renderCard();
    expect(await screen.findByText("Исходная смета")).toBeInTheDocument();
    expect(screen.getByText("1830")).toBeInTheDocument();
  });

  it("в истории отличает актуальную смету от вытесненной заменой", async () => {
    const user = userEvent.setup();
    renderCard();
    await screen.findByRole("heading", { name: "ГП-2026-001" });

    await user.click(screen.getByRole("tab", { name: /История загрузок/ }));

    const current = (await screen.findByText("смета-актуальная.xlsx")).closest("tr");
    const replaced = screen.getByText("смета-вытесненная.xlsx").closest("tr");
    expect(within(current as HTMLElement).getByText("актуальная")).toBeInTheDocument();
    expect(within(replaced as HTMLElement).getByText("вытеснена заменой")).toBeInTheDocument();
  });

  it("даёт скачать исходник каждого задания", async () => {
    const user = userEvent.setup();
    renderCard();
    await screen.findByRole("heading", { name: "ГП-2026-001" });
    await user.click(screen.getByRole("tab", { name: /История загрузок/ }));

    // Кнопка, а не ссылка: разбор статуса (404 против 410) обязан делать экран,
    // а не браузер — см. describe «Скачивание исходника» ниже. Прежний тест здесь
    // проверял `href` и тем закреплял как раз то поведение, которое ревью и
    // признало недостаточным.
    const buttons = await screen.findAllByRole("button", { name: /скачать/ });
    expect(buttons).toHaveLength(4);
  });

  it("показывает предупреждения задания", async () => {
    const user = userEvent.setup();
    renderCard();
    await screen.findByRole("heading", { name: "ГП-2026-001" });
    await user.click(screen.getByRole("tab", { name: /История загрузок/ }));

    expect(await screen.findByText(/предупреждений: 1/)).toBeInTheDocument();
  });
});

describe("Загрузка сметы: три исхода §5", () => {
  it("202 — задание принято, но смета появится позже", async () => {
    handlerState.jobStatuses = ["pending", "parsing", "done"];
    const user = userEvent.setup();
    renderCard();
    await screen.findByRole("heading", { name: "ГП-2026-001" });
    await user.click(screen.getByRole("tab", { name: "Загрузка" }));

    await dropXlsx(user);

    // Экран обязан показать, что работа ещё идёт, а не что «загружено».
    expect(
      await screen.findByText(/Смета появится в карточке после завершения/)
    ).toBeInTheDocument();
  });

  it(
    "поллинг доводит задание до done, показывает счётчики и останавливается",
    async () => {
      handlerState.jobStatuses = ["parsing", "matching", "done"];
      const user = userEvent.setup();
      renderCard();
      await screen.findByRole("heading", { name: "ГП-2026-001" });
      await user.click(screen.getByRole("tab", { name: "Загрузка" }));

      await dropXlsx(user);

      expect(await screen.findByText("Готово", {}, { timeout: 8000 })).toBeInTheDocument();
      expect(screen.getByText("На разбор")).toBeInTheDocument();

      // Поллинг прекратился: окно ожидания заведомо больше интервала опроса,
      // поэтому «счётчик не вырос» означает именно остановку, а не совпадение.
      const pollsAtDone = handlerState.jobPolls;
      await new Promise((resolve) => setTimeout(resolve, JOB_POLL_INTERVAL_MS * 2));
      expect(handlerState.jobPolls).toBe(pollsAtDone);
    },
    20_000
  );

  it("200 — тот же файл: объясняет, что ничего не изменилось", async () => {
    handlerState.uploadOutcome = "idempotent";
    const user = userEvent.setup();
    renderCard();
    await screen.findByRole("heading", { name: "ГП-2026-001" });
    await user.click(screen.getByRole("tab", { name: "Загрузка" }));

    await dropXlsx(user);

    expect(await screen.findByText(/уже был загружен для этой сметы/)).toBeInTheDocument();
  });

  it("409 — не ошибка, а предложение заменить (admin)", async () => {
    handlerState.uploadOutcome = "conflict";
    const user = userEvent.setup();
    renderCard();
    await screen.findByRole("heading", { name: "ГП-2026-001" });
    await user.click(screen.getByRole("tab", { name: "Загрузка" }));

    await dropXlsx(user);

    expect(await screen.findByText("Смета уже загружена")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Заменить смету" }));

    await waitFor(() => expect(handlerState.lastUploadReplace).toBe(true));
  });

  it("409 у member — замену не предлагает, показывает отказ текстом (§5)", async () => {
    handlerState.uploadOutcome = "conflict";
    const user = userEvent.setup();
    renderCard({ initialUser: { id: 2, email: "member@example.com", role: "member" } });
    await screen.findByRole("heading", { name: "ГП-2026-001" });
    await user.click(screen.getByRole("tab", { name: "Загрузка" }));

    await dropXlsx(user);

    expect(await screen.findByRole("alert")).toHaveTextContent(/replace=true/);
    expect(screen.queryByRole("button", { name: "Заменить смету" })).not.toBeInTheDocument();
    expect(handlerState.lastUploadReplace).toBe(false);
  });
});

describe("Права на карточке (§6.2)", () => {
  it("admin видит правку карточки", async () => {
    renderCard();
    expect(await screen.findByRole("button", { name: /Правка/ })).toBeInTheDocument();
  });

  it("member правку не видит, но карточку и загрузку — да", async () => {
    renderCard({ initialUser: { id: 2, email: "member@example.com", role: "member" } });
    await screen.findByRole("heading", { name: "ГП-2026-001" });

    expect(screen.queryByRole("button", { name: /Правка/ })).not.toBeInTheDocument();
    // Загрузка смет — право member по §3, вкладка остаётся.
    expect(screen.getByRole("tab", { name: "Загрузка" })).toBeInTheDocument();
  });
});

describe("История загрузок: аудит не должен врать (разбор внешнего ревью)", () => {
  async function openHistory(user: ReturnType<typeof userEvent.setup>) {
    renderCard();
    await screen.findByRole("heading", { name: "ГП-2026-001" });
    await user.click(screen.getByRole("tab", { name: /История загрузок/ }));
    await screen.findByText("смета-актуальная.xlsx");
  }

  it("«вытеснена заменой» стоит только у задания, которое смету создавало", async () => {
    const user = userEvent.setup();
    await openHistory(user);

    const replaced = screen.getByText("смета-вытесненная.xlsx").closest("tr") as HTMLElement;
    const failed = screen.getByText("смета-битая.xlsx").closest("tr") as HTMLElement;
    const running = screen.getByText("смета-в-работе.xlsx").closest("tr") as HTMLElement;

    expect(within(replaced).getByText("вытеснена заменой")).toBeInTheDocument();
    // Упавшее и незавершённое задания смету не создавали никогда — подпись про
    // замену была бы прямой ложью об аудите.
    expect(within(failed).queryByText("вытеснена заменой")).toBeNull();
    expect(within(failed).getByText("смета не создана")).toBeInTheDocument();
    expect(within(running).queryByText("вытеснена заменой")).toBeNull();
    expect(within(running).getByText("загрузка не завершена")).toBeInTheDocument();
  });

  it("показывает ТЕКСТЫ предупреждений, а не только их число (DoD)", async () => {
    const user = userEvent.setup();
    await openHistory(user);

    // Данные лежат в БД, и после перезагрузки страницы панель загрузки пуста —
    // без текстов в истории DoD «в карточке видны предупреждения» не выполняется.
    expect(
      screen.getByText("Единица измерения «пог.м» не найдена (позиций: 3).")
    ).toBeInTheDocument();
  });

  it("показывает счётчики матчинга завершённого задания (DoD)", async () => {
    const user = userEvent.setup();
    await openHistory(user);

    const current = screen.getByText("смета-актуальная.xlsx").closest("tr") as HTMLElement;
    expect(current).toHaveTextContent("всего 1830");
    expect(current).toHaveTextContent("на разбор 1000");
  });

  it("показывает текст ошибки упавшего задания", async () => {
    const user = userEvent.setup();
    await openHistory(user);
    expect(
      screen.getByText("Не удалось разобрать файл: не найдена шапка сметы.")
    ).toBeInTheDocument();
  });
});

describe("Скачивание исходника: 404 и 410 различимы (§5)", () => {
  async function clickDownload(user: ReturnType<typeof userEvent.setup>) {
    renderCard();
    await screen.findByRole("heading", { name: "ГП-2026-001" });
    await user.click(screen.getByRole("tab", { name: /История загрузок/ }));
    const buttons = await screen.findAllByRole("button", { name: /скачать/ });
    await user.click(buttons[0]);
  }

  it("410 — объясняет, что файл удалён ретенцией, а запись аудита жива", async () => {
    handlerState.fileOutcome = "purged";
    const user = userEvent.setup();
    await clickDownload(user);

    expect(await screen.findByText(/удалён при очистке хранилища/)).toBeInTheDocument();
    // И это не путается с «задания нет».
    expect(screen.queryByText("Задание импорта не найдено.")).not.toBeInTheDocument();
  });

  it("404 — говорит именно про отсутствующее задание", async () => {
    handlerState.fileOutcome = "missing";
    const user = userEvent.setup();
    await clickDownload(user);

    expect(await screen.findByText("Задание импорта не найдено.")).toBeInTheDocument();
    expect(screen.queryByText(/удалён при очистке хранилища/)).not.toBeInTheDocument();
  });

  it("успех не показывает никакого отказа", async () => {
    const user = userEvent.setup();
    await clickDownload(user);

    await waitFor(() => {
      expect(screen.queryByText(/удалён при очистке/)).not.toBeInTheDocument();
    });
    expect(screen.queryByText("Задание импорта не найдено.")).not.toBeInTheDocument();
  });
});
