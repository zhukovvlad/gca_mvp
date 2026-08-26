import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { RoundUploadPanel } from "./RoundUploadPanel";
import { handlerState, jobPayload } from "@/test/handlers";
import { sampleTenderCard } from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";

/**
 * `RoundUploadPanel` (спека §2.14, задача 11): та же загрузка, что у сметы
 * договора, но владелец — раунд тендера, а замена — раунда ЦЕЛИКОМ (§2.6).
 */

async function dropXlsx(user: ReturnType<typeof userEvent.setup>) {
  const file = new File(["PKfake"], "сводная.xlsx", {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
  await user.upload(document.querySelector('input[type="file"]') as HTMLInputElement, file);
}

describe("RoundUploadPanel (спека §2.14)", () => {
  it("загружает раунд и показывает счётчики после done", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <RoundUploadPanel tenderId={300} roundId={3001} round={sampleTenderCard.rounds[0]} />
    );
    await dropXlsx(user);
    expect(await screen.findByText("Готово")).toBeInTheDocument();
    expect(screen.getByText("Позиций")).toBeInTheDocument();
  });

  it("409 — развилка «заменить раунд целиком», только у admin", async () => {
    handlerState.uploadOutcome = "conflict";
    const user = userEvent.setup();
    renderWithProviders(
      <RoundUploadPanel tenderId={300} roundId={3001} round={sampleTenderCard.rounds[0]} />
    );
    await dropXlsx(user);
    expect(await screen.findByRole("alertdialog")).toHaveTextContent(/всех участников/);
    await user.click(screen.getByRole("button", { name: "Заменить раунд" }));
    await waitFor(() => expect(handlerState.lastRoundUploadReplace).toBe(true));
  });

  it("member на 409 видит отказ, а не диалог", async () => {
    handlerState.uploadOutcome = "conflict";
    const user = userEvent.setup();
    renderWithProviders(
      <RoundUploadPanel tenderId={300} roundId={3001} round={sampleTenderCard.rounds[0]} />,
      { initialUser: { id: 2, email: "m@example.com", role: "member" } }
    );
    await dropXlsx(user);
    expect(await screen.findByTestId("upload-rejection")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("после перезагрузки показывает идущий импорт по latest_job, а не теряет его", async () => {
    handlerState.jobStatuses = ["parsing", "done"];
    const round = {
      ...sampleTenderCard.rounds[0],
      current_job_id: null,
      latest_job: {
        id: 9102,
        status: "parsing" as const,
        filename: "r1.xlsx",
        finished_at: null,
        created_at: "2026-06-02T09:00:00Z",
      },
    };
    renderWithProviders(<RoundUploadPanel tenderId={300} roundId={3001} round={round} />);
    expect(await screen.findByText("Разбор файла")).toBeInTheDocument();
  });

  it("после перезагрузки показывает ошибку последнего job", async () => {
    handlerState.jobStatuses = ["error"];
    const round = {
      ...sampleTenderCard.rounds[0],
      current_job_id: null,
      latest_job: {
        id: 9103,
        status: "error" as const,
        filename: "bad.xlsx",
        finished_at: "2026-06-02T10:00:00Z",
        created_at: "2026-06-02T09:00:00Z",
      },
    };
    renderWithProviders(<RoundUploadPanel tenderId={300} roundId={3001} round={round} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/Не удалось разобрать файл/);
  });

  /**
   * Смена раунда (например через `?round=` на `TenderCardPage`) обязана
   * пересоздать панель по `key={round.id}` — без него `useState(round.
   * latest_job?.id)` читает проп ОДИН раз, при монтировании, и панель
   * продолжала бы опрашивать job ПРЕЖНЕГО раунда даже после того, как
   * родитель отрисовал другой.
   *
   * Проверка ведётся ПО ID ЗАДАНИЯ, а не по счётчику опросов: `server.use`
   * подменяет обработчик `GET /api/v1/import-jobs/:id` так, чтобы каждый id
   * отвечал СВОИМ фиксированным содержимым (9102 — вечный `parsing`, 9103 —
   * `error`), и каждый опрошенный id пишется в `polled`. Так тест отличает
   * «панель переключилась на 9103» от «панель получила третий статус из
   * очереди по счётчику», а по хвосту `polled` после точки переключения видно,
   * что 9102 больше не опрашивается — не только то, что 9103 стал опрашиваться.
   */
  it("смена раунда через key пересоздаёт панель и переключает поллинг на job НОВОГО раунда", async () => {
    const polled: number[] = [];
    server.use(
      http.get("/api/v1/import-jobs/:id", ({ params }) => {
        const id = Number(params.id);
        polled.push(id);
        if (id === 9102) {
          return HttpResponse.json({
            ...jobPayload("parsing"),
            id,
            owner_type: "round",
            filename: "r1.xlsx",
          });
        }
        if (id === 9103) {
          return HttpResponse.json({
            ...jobPayload("error"),
            id,
            owner_type: "round",
            filename: "bad.xlsx",
          });
        }
        return HttpResponse.json({ detail: "нет" }, { status: 404 });
      })
    );
    const round1 = {
      ...sampleTenderCard.rounds[0],
      current_job_id: null,
      latest_job: { id: 9102, status: "parsing" as const, filename: "r1.xlsx", finished_at: null, created_at: null },
    };
    const round2 = {
      ...sampleTenderCard.rounds[1],
      current_job_id: null,
      latest_job: { id: 9103, status: "error" as const, filename: "bad.xlsx", finished_at: null, created_at: null },
    };
    const { rerender } = renderWithProviders(
      <RoundUploadPanel key={round1.id} tenderId={300} roundId={round1.id} round={round1} />
    );
    expect(await screen.findByText("r1.xlsx")).toBeInTheDocument();
    expect(polled).toContain(9102);

    rerender(<RoundUploadPanel key={round2.id} tenderId={300} roundId={round2.id} round={round2} />);

    expect(await screen.findByText("bad.xlsx")).toBeInTheDocument();
    expect(screen.queryByText("r1.xlsx")).toBeNull();
    const switchedAt = polled.indexOf(9103);
    expect(switchedAt).toBeGreaterThan(-1);
    // после переключения 9102 больше не опрашивается
    expect(polled.slice(switchedAt)).not.toContain(9102);
  });
});
