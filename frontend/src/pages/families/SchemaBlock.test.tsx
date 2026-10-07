import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { useReaskConfirm } from "@/services/queries";
import { qk } from "@/services/queryKeys";
import { SCHEMA_FAMILY_ID, handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { createTestQueryClient, renderWithProviders, waitForDialogFocus } from "@/test/utils";

import { SchemaBlock } from "./SchemaBlock";

/**
 * Блок «Схема и варианты» карточки семьи (спека
 * `2026-10-02-catalog-variants-design.md` §2.12): параметры и значения с
 * происхождением, пометки состояния, «Пересобрать…» через предпросмотр,
 * «Отменить пересборку», «Править схему…», «Слить значения…», таблица вариантов.
 * Обработчики MSW меняют фикстуру, как менял бы сервер, поэтому экран после
 * действия проверяется перечитанным.
 */

const LATER = { timeout: 8000 };

function family(id: number) {
  const found = handlerState.workFamilies.find((f) => f.id === id);
  if (!found) throw new Error(`no family ${id}`);
  return found;
}

function renderBlock(id: number = SCHEMA_FAMILY_ID) {
  return renderWithProviders(<SchemaBlock family={family(id)} />);
}

/** Активная семья без схемы: черновик фикстуры, переведённый в активные. */
function activeFamilyWithoutSchema() {
  family(1).status = "active";
  return 1;
}

async function shown() {
  await screen.findByText("Версия схемы 2", {}, LATER);
}

describe("SchemaBlock: чтение", () => {
  it("печатает версию схемы, параметры и значения с происхождением словами, а не кодами", async () => {
    renderBlock();
    await shown();

    expect(screen.getByRole("heading", { name: "Схема и варианты" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Материал" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Толщина" })).toBeInTheDocument();
    expect(screen.getByText("металло-черепица").closest("li")).toHaveTextContent("добавлено при разборе");
    expect(screen.getByText("0,7 мм").closest("li")).toHaveTextContent("вручную");
    expect(screen.getByText("профнастил").closest("li")).toHaveTextContent("из схемы");
    const listText = screen.getAllByRole("listitem").map((li) => li.textContent).join(" ");
    expect(listText).not.toMatch(/extension|manual|schema/);
  });

  it("слитое значение читается синонимом своей цели", async () => {
    handlerState.familySchemas[SCHEMA_FAMILY_ID].parameters[0].values[2].merged_into_id = 1002;
    renderBlock();
    await shown();

    expect(screen.getByText("металло-черепица").closest("li")).toHaveTextContent("синоним «металлочерепица»");
    expect(screen.getByText("металлочерепица").closest("li")).not.toHaveTextContent("синоним");
  });

  it("у семьи без схемы: «Схемы пока нет», править и сливать нечего", async () => {
    renderBlock(activeFamilyWithoutSchema());

    expect(await screen.findByText("Схемы пока нет.", {}, LATER)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Пересобрать…" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Править схему…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Слить значения…" })).toBeDisabled();
    expect(screen.getByText("Вариантов пока нет.")).toBeInTheDocument();
  });

  it("слить значения можно, только когда у какого-то параметра их не меньше двух живых", async () => {
    const parameters = handlerState.familySchemas[SCHEMA_FAMILY_ID].parameters;
    parameters[0].values = parameters[0].values.slice(0, 1);
    parameters[1].values[1].merged_into_id = 1004;
    renderBlock();
    await shown();

    expect(screen.getByRole("button", { name: "Слить значения…" })).toBeDisabled();
  });

  it("пометка «схема строится» и кнопка «Отменить пересборку» видны только при пересборке", async () => {
    renderBlock();
    await shown();
    expect(screen.queryByText("схема строится")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Отменить пересборку" })).not.toBeInTheDocument();
  });

  it("при пересборке: пометка «схема строится», показанная версия прежняя, есть «Отменить пересборку»", async () => {
    handlerState.familySchemas[SCHEMA_FAMILY_ID].building = true;
    renderBlock();
    await shown();

    expect(screen.getByText("схема строится")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Отменить пересборку" })).toBeEnabled();
    expect(screen.getByText("профнастил")).toBeInTheDocument();
  });

  it("пока схема строится, блок перечитывает её сам, и пометка снимается без действий человека", async () => {
    handlerState.familySchemas[SCHEMA_FAMILY_ID].building = true;
    // Интервал опроса подменяется ДО монтирования (заводится при подписке); прочие таймеры
    // и ожидания RTL остаются настоящими — тест не ждёт 5 с реального времени.
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      renderBlock();
      await shown();
      expect(screen.getByText("схема строится")).toBeInTheDocument();

      handlerState.familySchemas[SCHEMA_FAMILY_ID] = {
        ...handlerState.familySchemas[SCHEMA_FAMILY_ID],
        building: false,
        version: 3,
      };
      await vi.advanceTimersByTimeAsync(4_000);
      expect(screen.getByText("Версия схемы 2")).toBeInTheDocument();
      await vi.advanceTimersByTimeAsync(1_500);

      expect(await screen.findByText("Версия схемы 3", {}, LATER)).toBeInTheDocument();
      expect(screen.queryByText("схема строится")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("схему, которая не строится, блок не опрашивает", async () => {
    let reads = 0;
    server.use(
      http.get("/api/v1/semantic/families/:id/schema", () => {
        reads += 1;
        return HttpResponse.json(handlerState.familySchemas[SCHEMA_FAMILY_ID]);
      })
    );
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      renderBlock();
      await shown();
      expect(reads).toBe(1);

      await vi.advanceTimersByTimeAsync(16_000);
      expect(reads).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("после фоновой пересборки таблица вариантов показывает новые количества контекстов, а не прежние", async () => {
    handlerState.familySchemas[SCHEMA_FAMILY_ID].building = true;
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      renderBlock();
      await shown();
      const before = await screen.findByRole("table", { name: "Варианты семьи" }, LATER);
      expect(within(before).getByText("3")).toBeInTheDocument();

      // Исполнитель закончил: версия 3, у контекстов другие варианты и счётчики.
      handlerState.familySchemas[SCHEMA_FAMILY_ID] = {
        ...handlerState.familySchemas[SCHEMA_FAMILY_ID],
        building: false,
        version: 3,
      };
      handlerState.familyVariants[SCHEMA_FAMILY_ID] = [
        { id: 4, values: ["профнастил", "0,5 мм"], contexts: 5, status: "active" },
        { id: 2, values: ["металлочерепица", null], contexts: 1, status: "active" },
      ];
      await vi.advanceTimersByTimeAsync(5_500);

      expect(await screen.findByText("Версия схемы 3", {}, LATER)).toBeInTheDocument();
      const table = screen.getByRole("table", { name: "Варианты семьи" });
      await waitFor(() => expect(within(table).getByText("5")).toBeInTheDocument(), LATER);
      expect(within(table).queryByText("3")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("пока идут задания значений, блок опрашивает и схему, и варианты и показывает пометку со счётчиком", async () => {
    handlerState.familySchemas[SCHEMA_FAMILY_ID].values_jobs_live = 2;
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      renderBlock();
      await shown();
      expect(screen.getByText("значения пересчитываются: 2")).toBeInTheDocument();
      const before = await screen.findByRole("table", { name: "Варианты семьи" }, LATER);
      expect(within(before).getByText("3")).toBeInTheDocument();

      handlerState.familySchemas[SCHEMA_FAMILY_ID] = {
        ...handlerState.familySchemas[SCHEMA_FAMILY_ID],
        values_jobs_live: 1,
      };
      handlerState.familyVariants[SCHEMA_FAMILY_ID][0].contexts = 4;
      await vi.advanceTimersByTimeAsync(5_500);

      expect(await screen.findByText("значения пересчитываются: 1", {}, LATER)).toBeInTheDocument();
      await waitFor(
        () => expect(within(screen.getByRole("table", { name: "Варианты семьи" })).getByText("4")).toBeInTheDocument(),
        LATER
      );

      handlerState.familySchemas[SCHEMA_FAMILY_ID] = {
        ...handlerState.familySchemas[SCHEMA_FAMILY_ID],
        values_jobs_live: 0,
      };
      handlerState.familyVariants[SCHEMA_FAMILY_ID][0].contexts = 6;
      await vi.advanceTimersByTimeAsync(5_500);

      await waitFor(() => expect(screen.queryByText(/значения пересчитываются/)).not.toBeInTheDocument(), LATER);
      await waitFor(
        () => expect(within(screen.getByRole("table", { name: "Варианты семьи" })).getByText("6")).toBeInTheDocument(),
        LATER
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it("когда счётчик заданий значений падает до нуля, варианты перечитываются ещё раз", async () => {
    const queryClient = createTestQueryClient();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const variantsKey = JSON.stringify(qk.workFamilies.variants(SCHEMA_FAMILY_ID));
    const variantsInvalidations = () =>
      invalidate.mock.calls.filter(([filters]) => JSON.stringify(filters?.queryKey) === variantsKey).length;
    handlerState.familySchemas[SCHEMA_FAMILY_ID].values_jobs_live = 2;
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      renderWithProviders(<SchemaBlock family={family(SCHEMA_FAMILY_ID)} />, { queryClient });
      await shown();
      expect(variantsInvalidations()).toBe(0);

      handlerState.familySchemas[SCHEMA_FAMILY_ID] = {
        ...handlerState.familySchemas[SCHEMA_FAMILY_ID],
        values_jobs_live: 0,
      };
      await vi.advanceTimersByTimeAsync(5_500);

      await waitFor(() => expect(variantsInvalidations()).toBe(1), LATER);
    } finally {
      vi.useRealTimers();
    }
  });

  it("схема в покое: ни схема, ни варианты не опрашиваются, пометки пересчёта нет", async () => {
    let schemaReads = 0;
    let variantsReads = 0;
    server.use(
      http.get("/api/v1/semantic/families/:id/schema", () => {
        schemaReads += 1;
        return HttpResponse.json(handlerState.familySchemas[SCHEMA_FAMILY_ID]);
      }),
      http.get("/api/v1/semantic/families/:id/variants", () => {
        variantsReads += 1;
        return HttpResponse.json(handlerState.familyVariants[SCHEMA_FAMILY_ID]);
      })
    );
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      renderBlock();
      await shown();
      await screen.findByRole("table", { name: "Варианты семьи" }, LATER);
      expect([schemaReads, variantsReads]).toEqual([1, 1]);

      await vi.advanceTimersByTimeAsync(16_000);

      expect([schemaReads, variantsReads]).toEqual([1, 1]);
      expect(screen.queryByText(/значения пересчитываются/)).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("при пересборке «Править схему…» отключена с пояснением (сервер отказал бы), «Слить значения…» доступна", async () => {
    handlerState.familySchemas[SCHEMA_FAMILY_ID].building = true;
    renderBlock();
    await shown();

    expect(screen.getByRole("button", { name: "Править схему…" })).toBeDisabled();
    expect(screen.getByText(/Пока схема строится, её нельзя править/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Слить значения…" })).toBeEnabled();
  });

  it("у неактивной семьи при пересборке названа одна причина — неактивность, пояснения про пересборку нет", async () => {
    family(SCHEMA_FAMILY_ID).status = "archived";
    handlerState.familySchemas[SCHEMA_FAMILY_ID].building = true;
    renderBlock();
    await shown();

    expect(screen.getByRole("button", { name: "Править схему…" })).toBeDisabled();
    expect(screen.getByText("Схему можно менять только у активной семьи.")).toBeInTheDocument();
    expect(screen.queryByText(/Пока схема строится, её нельзя править/)).not.toBeInTheDocument();
  });

  it("без пересборки «Править схему…» доступна и пояснения нет", async () => {
    renderBlock();
    await shown();

    expect(screen.getByRole("button", { name: "Править схему…" })).toBeEnabled();
    expect(screen.queryByText(/Пока схема строится, её нельзя править/)).not.toBeInTheDocument();
  });

  it("пометка «схема ждёт перезапроса единицы» только при ready_to_build = false", async () => {
    const { unmount } = renderBlock();
    await shown();
    expect(screen.queryByText("схема ждёт перезапроса единицы")).not.toBeInTheDocument();
    unmount();

    handlerState.familySchemas[SCHEMA_FAMILY_ID].ready_to_build = false;
    renderBlock();
    expect(await screen.findByText("схема ждёт перезапроса единицы", {}, LATER)).toBeInTheDocument();
  });

  it("у неактивной семьи схему не пересобирают и не правят, причина названа", async () => {
    renderBlock(2);

    expect(await screen.findByText("Схемы пока нет.", {}, LATER)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Пересобрать…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Править схему…" })).toBeDisabled();
    expect(screen.getByText("Схему можно менять только у активной семьи.")).toBeInTheDocument();
  });

  it("у неактивной семьи СО схемой правка тоже недоступна: отключает активность, а не отсутствие версии", async () => {
    family(SCHEMA_FAMILY_ID).status = "archived";
    renderBlock();
    await shown();

    expect(screen.getByRole("button", { name: "Править схему…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Пересобрать…" })).toBeDisabled();
    expect(screen.getByText("Схему можно менять только у активной семьи.")).toBeInTheDocument();
  });

  it("отказ чтения схемы — подпись, а не пустой блок", async () => {
    server.use(
      http.get("/api/v1/semantic/families/:id/schema", () =>
        HttpResponse.json({ detail: { code: "family_not_found", message: "x" } }, { status: 404 })
      )
    );
    renderBlock();

    expect(await screen.findByText("Не удалось получить схему семьи.", {}, LATER)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("family_not_found");
  });

  it("таблица вариантов в блоке: набор, число контекстов, статус", async () => {
    renderBlock();
    await shown();

    const table = await screen.findByRole("table", { name: "Варианты семьи" }, LATER);
    expect(within(table).getByText("профнастил · 0,5 мм")).toBeInTheDocument();
  });
});

describe("SchemaBlock: пересборка", () => {
  it("«Пересобрать…» показывает предпросмотр, подтверждение шлёт его preview_hash, схема после перечитывания строится", async () => {
    const user = userEvent.setup();
    renderBlock();
    await shown();

    await user.click(screen.getByRole("button", { name: "Пересобрать…" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Пересобрать схему?", {}, LATER)).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        "Модель заново составит параметры и значения схемы семьи. Текущая схема остаётся в силе, пока новая не будет готова."
      )
    ).toBeInTheDocument();
    expect(await within(dialog).findByText("$1,90", {}, LATER)).toBeInTheDocument();
    expect(within(dialog).queryByText(/не включает задания значений/)).not.toBeInTheDocument();
    expect(handlerState.previewRequests).toEqual([`schema:${SCHEMA_FAMILY_ID}`]);

    await user.click(within(dialog).getByRole("button", { name: "Пересобрать" }));

    await waitFor(() => expect(handlerState.schemaRequests).toHaveLength(1), LATER);
    expect(handlerState.schemaRequests[0]).toEqual({
      action: "rebuild",
      familyId: SCHEMA_FAMILY_ID,
      body: { preview_hash: "preview-hash-1" },
    });
    expect(await screen.findByText("Пересборка схемы поставлена в очередь.", {}, LATER)).toBeInTheDocument();
    expect(await screen.findByText("схема строится", {}, LATER)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument(), LATER);
  });

  it("у семьи без текущей схемы предпросмотр говорит, что оценка не включает задания значений", async () => {
    const user = userEvent.setup();
    renderBlock(activeFamilyWithoutSchema());
    await screen.findByText("Схемы пока нет.", {}, LATER);

    await user.click(screen.getByRole("button", { name: "Пересобрать…" }));
    const dialog = await screen.findByRole("dialog");

    expect(await within(dialog).findByText(/Оценка не включает задания значений/, {}, LATER)).toBeInTheDocument();
    expect(within(dialog).getByText(/Показанная сумма неполная/)).toBeInTheDocument();
  });

  it("409 preview_changed: подпись «откройте предпросмотр заново», код не виден, старый хэш повторно не шлётся", async () => {
    const user = userEvent.setup();
    renderBlock();
    await shown();
    handlerState.schemaRefusal = { action: "rebuild", code: "preview_changed", status: 409 };

    await user.click(screen.getByRole("button", { name: "Пересобрать…" }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("$1,90", {}, LATER);
    await user.click(within(dialog).getByRole("button", { name: "Пересобрать" }));

    expect(
      await within(dialog).findByText("Состояние изменилось, откройте предпросмотр заново.", {}, LATER)
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("preview_changed");
    // Подпись одна — в диалоге; тоста у этого отказа нет.
    expect(document.querySelectorAll("[data-sonner-toast]")).toHaveLength(0);
    expect(within(dialog).getByRole("button", { name: "Пересобрать" })).toBeDisabled();
    expect(handlerState.schemaRequests).toHaveLength(1);
  });

  it("отказ подтверждения пересборки (семья стала неактивной) — подпись по коду в тосте, не код и не текст сервера", async () => {
    // Идущая пересборка отказом не бывает (`rebuild_schema` идемпотентен); из отказов,
    // которые маршрут даёт кроме `preview_changed`, — `family_not_active` и `family_not_found`.
    const user = userEvent.setup();
    renderBlock();
    await shown();
    handlerState.schemaRefusal = { action: "rebuild", code: "family_not_active", status: 409 };

    await user.click(screen.getByRole("button", { name: "Пересобрать…" }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("$1,90", {}, LATER);
    await user.click(within(dialog).getByRole("button", { name: "Пересобрать" }));

    expect(
      await screen.findByText("Семья не активна: схему можно менять только у активной семьи.", {}, LATER)
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("family_not_active");
    expect(document.body.textContent).not.toContain("server text must not reach the screen");
  });

  it("оценка ушла от показанной (другой предпросмотр выдал новый хэш) — сервер отвечает preview_changed, экран называет подпись", async () => {
    const user = userEvent.setup();
    renderBlock();
    await shown();

    await user.click(screen.getByRole("button", { name: "Пересобрать…" }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("$1,90", {}, LATER);
    handlerState.schemaPreviewHashes[SCHEMA_FAMILY_ID] = "preview-hash-elsewhere";
    await user.click(within(dialog).getByRole("button", { name: "Пересобрать" }));

    expect(
      await within(dialog).findByText("Состояние изменилось, откройте предпросмотр заново.", {}, LATER)
    ).toBeInTheDocument();
    expect(handlerState.schemaRequests[0].body).toEqual({ preview_hash: "preview-hash-1" });
    expect(handlerState.familySchemas[SCHEMA_FAMILY_ID].building).toBe(false);
    expect(document.body.textContent).not.toContain("preview_changed");
  });

  it("пока подтверждение пересборки в пути, «Пересобрать» недоступна — второй запрос не уходит", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let calls = 0;
    server.use(
      http.post("/api/v1/semantic/families/:id/schema/rebuild", async () => {
        calls += 1;
        await gate;
        return HttpResponse.json({ family_id: SCHEMA_FAMILY_ID, schema_id: 77, version: 3, status: "building" });
      })
    );
    const user = userEvent.setup();
    renderBlock();
    await shown();

    await user.click(screen.getByRole("button", { name: "Пересобрать…" }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("$1,90", {}, LATER);
    await user.click(within(dialog).getByRole("button", { name: "Пересобрать" }));

    await waitFor(() => expect(calls).toBe(1), LATER);
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Пересобрать" })).toBeDisabled(), LATER);
    release();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument(), LATER);
    expect(calls).toBe(1);
  });

  describe("блок перерисовался при открытом окне (схема перечитана)", () => {
    /** Схема на сервере изменилась и перечитана — блок рендерится заново, пока окно открыто. */
    async function rereadSchema(queryClient: ReturnType<typeof createTestQueryClient>) {
      handlerState.familySchemas[SCHEMA_FAMILY_ID] = {
        ...handlerState.familySchemas[SCHEMA_FAMILY_ID],
        version: 3,
      };
      await act(async () => {
        await queryClient.invalidateQueries({ queryKey: qk.workFamilies.schema(SCHEMA_FAMILY_ID) });
      });
      await screen.findByText("Версия схемы 3", {}, LATER);
    }

    function gatedRebuild() {
      let release: () => void = () => {};
      const gate = new Promise<void>((resolve) => {
        release = resolve;
      });
      let calls = 0;
      server.use(
        http.post("/api/v1/semantic/families/:id/schema/rebuild", async () => {
          calls += 1;
          await gate;
          return HttpResponse.json({ family_id: SCHEMA_FAMILY_ID, schema_id: 77, version: 4, status: "building" });
        })
      );
      return { release: () => release(), calls: () => calls };
    }

    async function openAndConfirm(queryClient: ReturnType<typeof createTestQueryClient>) {
      const user = userEvent.setup();
      renderWithProviders(<SchemaBlock family={family(SCHEMA_FAMILY_ID)} />, { queryClient });
      await shown();
      await user.click(screen.getByRole("button", { name: "Пересобрать…" }));
      const dialog = await screen.findByRole("dialog");
      await within(dialog).findByText("$1,90", {}, LATER);
      return { user, dialog };
    }

    it("предпросмотр не запрашивается второй раз, показанные числа не меняются молча", async () => {
      const queryClient = createTestQueryClient();
      const { dialog } = await openAndConfirm(queryClient);

      await rereadSchema(queryClient);
      // Повторный запрос оценки ушёл бы из эффекта уже ПОСЛЕ кадра «Версия схемы 3» и долетел
      // бы до обработчика позже — без паузы проверка могла бы успеть раньше него.
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 300));
      });

      expect(handlerState.previewRequests).toEqual([`schema:${SCHEMA_FAMILY_ID}`]);
      expect(within(dialog).getByText("$1,90")).toBeInTheDocument();
    });

    it("панель отдала ту же семью НОВЫМ объектом (перечитанный список) — предпросмотр не перезапускается", async () => {
      const user = userEvent.setup();
      const view = renderWithProviders(<SchemaBlock family={family(SCHEMA_FAMILY_ID)} />);
      await shown();
      await user.click(screen.getByRole("button", { name: "Пересобрать…" }));
      const dialog = await screen.findByRole("dialog");
      await within(dialog).findByText("$1,90", {}, LATER);

      view.rerender(<SchemaBlock family={{ ...family(SCHEMA_FAMILY_ID) }} />);
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 300));
      });

      expect(handlerState.previewRequests).toEqual([`schema:${SCHEMA_FAMILY_ID}`]);
      expect(within(dialog).getByText("$1,90")).toBeInTheDocument();
    });

    it("пока подтверждение в пути, перерисовка не включает «Пересобрать» снова", async () => {
      const rebuild = gatedRebuild();
      const queryClient = createTestQueryClient();
      const { user, dialog } = await openAndConfirm(queryClient);
      await user.click(within(dialog).getByRole("button", { name: "Пересобрать" }));
      await waitFor(() => expect(rebuild.calls()).toBe(1), LATER);

      await rereadSchema(queryClient);

      expect(within(dialog).getByRole("button", { name: "Пересобрать" })).toBeDisabled();
      rebuild.release();
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument(), LATER);
      expect(rebuild.calls()).toBe(1);
    });

    it("после успеха окно закрывается, хотя блок перерисовался в пути", async () => {
      const rebuild = gatedRebuild();
      const queryClient = createTestQueryClient();
      const { user, dialog } = await openAndConfirm(queryClient);
      await user.click(within(dialog).getByRole("button", { name: "Пересобрать" }));
      await waitFor(() => expect(rebuild.calls()).toBe(1), LATER);
      await rereadSchema(queryClient);

      rebuild.release();

      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument(), LATER);
    });
  });

  it("окно закрыли, пока подтверждение было в пути, и открыли заново: поздний ответ старого подтверждения новое окно не закрывает", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post("/api/v1/semantic/families/:id/schema/rebuild", async () => {
        await gate;
        return HttpResponse.json({ family_id: SCHEMA_FAMILY_ID, schema_id: 77, version: 3, status: "building" });
      })
    );
    const user = userEvent.setup();
    renderBlock();
    await shown();

    await user.click(screen.getByRole("button", { name: "Пересобрать…" }));
    let dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("$1,90", {}, LATER);
    await user.click(within(dialog).getByRole("button", { name: "Пересобрать" }));
    await user.click(within(dialog).getByRole("button", { name: "Отмена" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument(), LATER);

    await user.click(screen.getByRole("button", { name: "Пересобрать…" }));
    dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("$2,90", {}, LATER);
    release();
    await screen.findByText("Пересборка схемы поставлена в очередь.", {}, LATER);
    // Колбэк mutate старого подтверждения (onClose) пришёл бы сразу за тостом — дать ему кадр.
    await new Promise((resolve) => setTimeout(resolve, 200));

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await waitFor(
      () => expect(within(screen.getByRole("dialog")).getByRole("button", { name: "Пересобрать" })).toBeEnabled(),
      LATER
    );
  });

  it("отказ подтверждения перечитывает схему: экран не остаётся на устаревшем состоянии", async () => {
    const user = userEvent.setup();
    renderBlock();
    await shown();
    handlerState.schemaRefusal = { action: "rebuild", code: "family_not_active", status: 409 };

    await user.click(screen.getByRole("button", { name: "Пересобрать…" }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("$1,90", {}, LATER);
    handlerState.familySchemas[SCHEMA_FAMILY_ID] = { ...handlerState.familySchemas[SCHEMA_FAMILY_ID], version: 3 };
    await user.click(within(dialog).getByRole("button", { name: "Пересобрать" }));

    expect(await screen.findByText("Версия схемы 3", {}, LATER)).toBeInTheDocument();
  });

  it("цель «схема» не подтверждается общим хуком перезапроса: он отказывает, запрос не уходит", async () => {
    const queryClient = createTestQueryClient();
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useReaskConfirm(), { wrapper });

    await expect(
      result.current.mutateAsync({ target: { kind: "schema", familyId: SCHEMA_FAMILY_ID }, previewHash: "preview-hash-1" })
    ).rejects.toThrow();
    expect(handlerState.schemaRequests).toEqual([]);
  });
});

describe("SchemaBlock: отмена пересборки", () => {
  it("«Отменить пересборку» шлёт отмену, пометка и кнопка пропадают после перечитывания", async () => {
    const user = userEvent.setup();
    handlerState.familySchemas[SCHEMA_FAMILY_ID].building = true;
    renderBlock();
    await shown();

    await user.click(screen.getByRole("button", { name: "Отменить пересборку" }));

    await waitFor(() => expect(handlerState.schemaRequests).toHaveLength(1), LATER);
    expect(handlerState.schemaRequests[0]).toEqual({ action: "cancel", familyId: SCHEMA_FAMILY_ID, body: null });
    expect(await screen.findByText("Пересборка схемы отменена.", {}, LATER)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("схема строится")).not.toBeInTheDocument(), LATER);
    expect(screen.queryByRole("button", { name: "Отменить пересборку" })).not.toBeInTheDocument();
  });

  it("отказ отмены (пересборки уже нет) — подпись по коду, код не виден", async () => {
    const user = userEvent.setup();
    handlerState.familySchemas[SCHEMA_FAMILY_ID].building = true;
    renderBlock();
    await shown();
    handlerState.schemaRefusal = { action: "cancel", code: "schema_no_building", status: 409 };

    await user.click(screen.getByRole("button", { name: "Отменить пересборку" }));

    expect(await screen.findByText("Пересборка уже не идёт: отменять нечего.", {}, LATER)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("schema_no_building");
  });

  it("пересборку уже отменили в другом окне: отказ отмены перечитывает схему, пометка и кнопка пропадают", async () => {
    // Опрос при `building` перечитал бы схему и сам — интервал подменён, чтобы пометку снимало
    // именно перечитывание после отказа, а не очередной тик опроса.
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const user = userEvent.setup();
      handlerState.familySchemas[SCHEMA_FAMILY_ID].building = true;
      renderBlock();
      await shown();
      handlerState.familySchemas[SCHEMA_FAMILY_ID] = { ...handlerState.familySchemas[SCHEMA_FAMILY_ID], building: false };

      await user.click(screen.getByRole("button", { name: "Отменить пересборку" }));

      expect(await screen.findByText("Пересборка уже не идёт: отменять нечего.", {}, LATER)).toBeInTheDocument();
      await waitFor(() => expect(screen.queryByText("схема строится")).not.toBeInTheDocument(), LATER);
      expect(screen.queryByRole("button", { name: "Отменить пересборку" })).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("SchemaBlock: правка и слияние обновляют блок", () => {
  it("«Править схему…»: добавленное значение появляется в блоке со словом «вручную»", async () => {
    const user = userEvent.setup();
    renderBlock();
    await shown();

    await user.click(screen.getByRole("button", { name: "Править схему…" }));
    const dialog = await screen.findByRole("dialog");
    await waitForDialogFocus();
    await user.type(within(dialog).getByLabelText("Значения параметра 2, по одному в строке"), "{Enter}1 мм");
    await user.click(within(dialog).getByRole("button", { name: "Сохранить схему" }));

    expect(await screen.findByText("1 мм", {}, LATER)).toBeInTheDocument();
    expect(screen.getByText("1 мм").closest("li")).toHaveTextContent("вручную");
    expect(screen.getByText("Версия схемы 3")).toBeInTheDocument();
    expect(await screen.findByText("Схема сохранена.", {}, LATER)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument(), LATER);
  });

  it("«Слить значения…»: таблица вариантов перечитана — набор с источником читается целью", async () => {
    const user = userEvent.setup();
    renderBlock();
    await shown();
    const table = await screen.findByRole("table", { name: "Варианты семьи" }, LATER);
    expect(within(table).getByText("профнастил · 0,5 мм")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Слить значения…" }));
    const dialog = await screen.findByRole("dialog");
    await waitForDialogFocus();
    await user.click(within(dialog).getByRole("combobox", { name: "Параметр" }));
    await user.click(await screen.findByRole("option", { name: "Материал" }));
    await user.click(within(dialog).getByRole("combobox", { name: "Источник" }));
    await user.click(await screen.findByRole("option", { name: "профнастил" }));
    await user.click(within(dialog).getByRole("combobox", { name: "Цель" }));
    await user.click(await screen.findByRole("option", { name: "металлочерепица" }));
    await user.click(within(dialog).getByRole("button", { name: "Слить" }));

    expect(await screen.findByText("Значения слиты.", {}, LATER)).toBeInTheDocument();
    await waitFor(() => {
      const rows = within(screen.getByRole("table", { name: "Варианты семьи" })).getAllByRole("row");
      expect(rows[1]).toHaveTextContent("металлочерепица · 0,5 мм");
    }, LATER);
  });

  it("действие над схемой перечитывает всё зависимое: схему, варианты, контексты, шапку очереди", async () => {
    const user = userEvent.setup();
    const queryClient = createTestQueryClient();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    handlerState.familySchemas[SCHEMA_FAMILY_ID].building = true;
    renderWithProviders(<SchemaBlock family={family(SCHEMA_FAMILY_ID)} />, { queryClient });
    await shown();

    await user.click(screen.getByRole("button", { name: "Отменить пересборку" }));
    await screen.findByText("Пересборка схемы отменена.", {}, LATER);

    const keys = invalidate.mock.calls.map(([filters]) => JSON.stringify(filters?.queryKey));
    expect(keys).toEqual(
      expect.arrayContaining([
        JSON.stringify(qk.workFamilies.schema(SCHEMA_FAMILY_ID)),
        JSON.stringify(qk.workFamilies.variants(SCHEMA_FAMILY_ID)),
        JSON.stringify(qk.semanticContexts.all),
        JSON.stringify(qk.semanticQueue.status),
      ])
    );
  });

  it("«Слить значения…»: слитое значение читается синонимом цели", async () => {
    const user = userEvent.setup();
    renderBlock();
    await shown();

    await user.click(screen.getByRole("button", { name: "Слить значения…" }));
    const dialog = await screen.findByRole("dialog");
    await waitForDialogFocus();
    await user.click(within(dialog).getByRole("combobox", { name: "Параметр" }));
    await user.click(await screen.findByRole("option", { name: "Материал" }));
    await user.click(within(dialog).getByRole("combobox", { name: "Источник" }));
    await user.click(await screen.findByRole("option", { name: "металло-черепица" }));
    await user.click(within(dialog).getByRole("combobox", { name: "Цель" }));
    await user.click(await screen.findByRole("option", { name: "металлочерепица" }));
    await user.click(within(dialog).getByRole("button", { name: "Слить" }));

    await waitFor(() => {
      expect(screen.getByText("металло-черепица").closest("li")).toHaveTextContent("синоним «металлочерепица»");
    }, LATER);
  });
});
