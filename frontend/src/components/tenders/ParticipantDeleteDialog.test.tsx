import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ParticipantDeleteDialog } from "./ParticipantDeleteDialog";
import { handlerState } from "@/test/handlers";
import { sampleTenderCard } from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";

// Мок целиком, а не спай поверх реального модуля: `toast.error` нужен как
// `vi.fn()`, который можно проверить на «не звали вовсе» — реальный sonner
// кладёт уведомление в DOM асинхронно и через портал, и «не нашли текст на
// экране» не отличило бы «не звали» от «ещё не отрисовалось».
vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
  Toaster: () => null,
}));

import { toast } from "sonner";

/**
 * Протокол `confirmation_token` (спека §2.11, задача 12): первый запрос — БЕЗ
 * токена — сервер ВСЕГДА отвечает 409 с preview состава; кнопка отправляет
 * ВТОРОЙ запрос уже с токеном preview. Три развилки второго ответа:
 * успех (204), "stale" (409 с НОВЫМ токеном — состав успел измениться) и
 * активный импорт раунда (409 `active_import`, кнопки удаления нет вовсе).
 */
const participant = sampleTenderCard.participants[0];

/**
 * Тот же глобальный `mutations.onError`, что в `App.tsx`: `toast.error(error
 * instanceof Error ? error.message : "Произошла ошибка")`. Без него тест «нет
 * тоста» проходил бы и без исправления — `createTestQueryClient()` из
 * `@/test/utils` глобального обработчика не несёт, а `onError` на самой
 * мутации молчит независимо от него. Это единственный способ здесь и правда
 * воспроизвести баг: без страховки внутри `useDeleteParticipant` именно ЭТОТ
 * обработчик и кладёт в тост сырую строку axios.
 */
function queryClientWithGlobalErrorToast(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: {
        retry: false,
        onError: (error: unknown) => {
          toast.error(error instanceof Error ? error.message : "Произошла ошибка");
        },
      },
    },
  });
}

describe("ParticipantDeleteDialog — протокол confirmation_token (§2.11)", () => {
  afterEach(() => vi.clearAllMocks());

  it("открытие запрашивает preview и показывает состав удаляемого", async () => {
    renderWithProviders(
      <ParticipantDeleteDialog tenderId={300} participant={participant} onOpenChange={() => {}} />
    );

    expect(await screen.findByText(/Раундов:\s*2/)).toBeInTheDocument();
    expect(screen.getByText(/Смет:\s*2/)).toBeInTheDocument();
    expect(screen.getByText(/Позиций:\s*1\s*830/)).toBeInTheDocument();
    expect(screen.getByText(/вручную:\s*3/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Удалить участника" })).toBeEnabled();
  });

  it("нажатие шлёт токен; 204 закрывает диалог", async () => {
    const user = userEvent.setup();
    let closed = false;
    renderWithProviders(
      <ParticipantDeleteDialog
        tenderId={300}
        participant={participant}
        onOpenChange={(open) => {
          if (!open) closed = true;
        }}
      />
    );

    await user.click(await screen.findByRole("button", { name: "Удалить участника" }));
    await waitFor(() => expect(closed).toBe(true));
  });

  it('"stale" — после нажатия показывает ИЗМЕНИВШИЙСЯ состав, а не старый, и НЕ закрывается', async () => {
    // Общий хендлер `handlerState.participantDeleteOutcome = "stale"` отдаёт
    // ОДИНАКОВЫЙ preview на оба запроса (меняется только токен) — этого
    // достаточно, чтобы проверить «не закрылось», но не «состав обновился»: тот
    // же экран прошёл бы этот тест, даже если бы вообще не перечитывал ответ
    // сервера, а просто держал первый preview на экране. Поэтому здесь —
    // собственный хендлер: второй ответ (уже с токеном) несёт ДРУГИЕ числа, и
    // тест требует, чтобы именно они появились на экране, а старые — исчезли.
    let calls = 0;
    server.use(
      http.delete("/api/v1/tenders/:id/participants/:pid", ({ request }) => {
        calls += 1;
        const token = new URL(request.url).searchParams.get("confirmation_token");
        if (!token) {
          return HttpResponse.json(
            {
              detail: {
                code: "confirmation_required",
                message: "Удаление участника требует подтверждения состава.",
                rounds_count: 2,
                estimates_count: 2,
                positions_count: 1830,
                overrides_count: 3,
                confirmation_token: "token-1",
              },
            },
            { status: 409 }
          );
        }
        // Токен из первого ответа пришёл, но состав успел измениться (кто-то
        // загрузил новый раунд между preview и нажатием) — сервер отвечает
        // 409 ЗАНОВО, с другими числами и новым токеном, а не 204.
        return HttpResponse.json(
          {
            detail: {
              code: "confirmation_required",
              message: "Состав снова изменился — подтвердите заново.",
              rounds_count: 3,
              estimates_count: 4,
              positions_count: 2100,
              overrides_count: 6,
              confirmation_token: "token-2",
            },
          },
          { status: 409 }
        );
      })
    );

    const user = userEvent.setup();
    let closed = false;
    renderWithProviders(
      <ParticipantDeleteDialog
        tenderId={300}
        participant={participant}
        onOpenChange={(open) => {
          if (!open) closed = true;
        }}
      />
    );

    expect(await screen.findByText(/Раундов:\s*2/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Удалить участника" }));

    // Новые числа — на экране; старые — нет. Проверяем оба направления: одного
    // «новые числа появились» было бы мало, если бы компонент их просто
    // ДОБАВИЛ рядом со старыми, ничего не убрав.
    expect(await screen.findByText(/Раундов:\s*3/)).toBeInTheDocument();
    expect(screen.getByText(/Смет:\s*4/)).toBeInTheDocument();
    expect(screen.getByText(/Позиций:\s*2\s*100/)).toBeInTheDocument();
    expect(screen.getByText(/вручную:\s*6/)).toBeInTheDocument();
    expect(screen.queryByText(/Раундов:\s*2\b/)).toBeNull();
    expect(screen.queryByText(/Позиций:\s*1\s*830/)).toBeNull();

    expect(closed).toBe(false);
    await waitFor(() => expect(calls).toBe(2));
  });

  it('"active" — «Импорт раунда выполняется» без кнопки удаления', async () => {
    handlerState.participantDeleteOutcome = "active";
    renderWithProviders(
      <ParticipantDeleteDialog tenderId={300} participant={participant} onOpenChange={() => {}} />
    );

    expect(await screen.findByText(/Импорт раунда выполняется — дождитесь/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Удалить участника" })).toBeNull();
  });

  // Краснеет от: возврата `useDeleteParticipant` к отсутствию собственного
  // `onError` (или от него, но без проверки `confirmation_required`) — запрос
  // падал бы в глобальный обработчик `App.tsx`, а тот кладёт в тост сырую
  // строку axios «Request failed with status code 409» для ОЖИДАЕМОГО первого
  // шага протокола, которого preview на экране уже и так объясняет по-русски.
  it("ожидаемый 409 confirmation_required не показывает тост-ошибку", async () => {
    renderWithProviders(
      <ParticipantDeleteDialog tenderId={300} participant={participant} onOpenChange={() => {}} />,
      { queryClient: queryClientWithGlobalErrorToast() }
    );

    expect(await screen.findByText(/Раундов:\s*2/)).toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
  });

  // Краснеет от: onError, который молчит на ЛЮБОЙ отказ (а не только
  // `confirmation_required`) — настоящий сбой протокола (409 `active_import`,
  // 404, 500) обязан дойти до человека, а не потеряться вместе с ожидаемым.
  it('"active" — настоящий отказ показывает тост с текстом сервера', async () => {
    handlerState.participantDeleteOutcome = "active";
    renderWithProviders(
      <ParticipantDeleteDialog tenderId={300} participant={participant} onOpenChange={() => {}} />
    );

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Импорт раунда выполняется."));
  });
});
