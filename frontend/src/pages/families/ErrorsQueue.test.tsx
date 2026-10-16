import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "@/test/server";
import { handlerState } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";
import type { JobRow, JobsResponse, UnitHoldGroup } from "@/types/domain";

import { ErrorsQueue } from "./ErrorsQueue";

/**
 * Очередь «Ошибки» (спека semantic-suggestions §2.6, §2.10, §2.12). Задания — фикстура
 * `handlers.ts`: в `error` — 21 (http_429, 5 попыток) и 22 (schema_error, один
 * ручной повтор); в `privacy_hold` — 31 («Ромашка» в строке), 32 («ЖК Северный» в строке
 * и в списке семей 501), 33–35 (единица м², совпадение только в строке семьи 501 —
 * их несёт одна строка единицы).
 */

const UNIT_SYMBOL: Record<string, string> = { M2: "м²", PCS: "шт" };
const unitLabel = (code: string | null) => (code ? (UNIT_SYMBOL[code] ?? code) : "без единицы");

function fixtureHold(overrides: Partial<JobsResponse> = {}): JobsResponse {
  return {
    status: "privacy_hold",
    items: structuredClone(handlerState.holdJobs),
    unit_groups: structuredClone(handlerState.unitHoldGroups),
    ...overrides,
  };
}

function renderQueue(
  options: { hold?: JobsResponse; onOpenFamily?: (id: number) => void; errors?: boolean } = {}
) {
  const errors = options.errors === false ? [] : structuredClone(handlerState.errorJobs);
  return renderWithProviders(
    <ErrorsQueue
      errors={errors}
      hold={options.hold ?? fixtureHold()}
      unitLabel={unitLabel}
      onOpenFamily={options.onOpenFamily}
    />
  );
}

function heldJob(title: RegExp) {
  const row = screen.getAllByTestId("held-job").find((r) => title.test(r.textContent ?? ""));
  if (!row) throw new Error(`нет задержанного задания ${title}`);
  return row;
}

function group(overrides: Partial<UnitHoldGroup>): UnitHoldGroup {
  return { ...structuredClone(handlerState.unitHoldGroups[0]), ...overrides };
}

describe("ErrorsQueue — задания в ошибке", () => {
  it("строка: контекст с единицей, класс ошибки, текст ошибки, число попыток", () => {
    renderQueue();

    const rows = screen.getAllByTestId("error-job");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("Облицовка керамогранитом цоколя · м²")).toBeInTheDocument();
    expect(within(rows[0]).getByText("HTTP 429")).toBeInTheDocument();
    expect(within(rows[0]).getByText("HTTP 429: Too Many Requests")).toBeInTheDocument();
    expect(within(rows[0]).getByText("попыток в этом поколении: 5")).toBeInTheDocument();
  });

  it("schema_error печатается словом, ручные повторы считаются", () => {
    renderQueue();

    const row = screen.getAllByTestId("error-job")[1];
    expect(within(row).getByText("ответ не по схеме")).toBeInTheDocument();
    expect(row).not.toHaveTextContent("schema_error");
    expect(
      within(row).getByText("попыток в этом поколении: 1, ручных повторов: 1")
    ).toBeInTheDocument();
  });

  it("класс, которого нет в словаре подписей, печатается как пришёл", () => {
    const errors = structuredClone(handlerState.errorJobs);
    errors[0].last_error_class = "ConnectionResetError";
    renderWithProviders(
      <ErrorsQueue errors={errors} hold={fixtureHold()} unitLabel={unitLabel} />
    );
    expect(within(screen.getAllByTestId("error-job")[0]).getByText("ConnectionResetError")).toBeInTheDocument();
  });

  it("«Повторить» шлёт повтор именно этого задания и сообщает о возврате в очередь", async () => {
    const user = userEvent.setup();
    renderQueue();

    await user.click(within(screen.getAllByTestId("error-job")[1]).getByRole("button", { name: "Повторить" }));

    await waitFor(() => expect(handlerState.retryJobRequests).toEqual([22]));
    expect(
      await screen.findByText("Задание возвращено в очередь, бюджет попыток сброшен.")
    ).toBeInTheDocument();
    expect(screen.getByText("История прежних попыток сохранена.")).toBeInTheDocument();
  });

  it("409 job_changed на повторе: «задание изменилось, обновите экран», запрос не повторяется", async () => {
    const user = userEvent.setup();
    let calls = 0;
    server.use(
      http.post("/api/v1/semantic/jobs/:id/retry", () => {
        calls += 1;
        return HttpResponse.json(
          { detail: { code: "job_changed", message: "job_changed" } },
          { status: 409 }
        );
      })
    );
    renderQueue();

    await user.click(within(screen.getAllByTestId("error-job")[0]).getByRole("button", { name: "Повторить" }));

    expect(await screen.findByText("Задание изменилось, обновите экран.")).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(calls).toBe(1);
  });

  it("ошибок нет — так и написано", () => {
    renderQueue({ errors: false, hold: fixtureHold({ items: [], unit_groups: [] }) });
    expect(screen.getByText("Ошибок нет.")).toBeInTheDocument();
  });
});

describe("ErrorsQueue — «Задержано проверкой»", () => {
  it("блок назван, под ним объяснение", () => {
    renderQueue();
    const block = screen.getByTestId("privacy-hold");
    expect(within(block).getByText("Задержано проверкой")).toBeInTheDocument();
    expect(block).toHaveTextContent("Запрос не отправлен");
  });

  it("совпавшее слово в строке подсвечено, остальной текст — нет", () => {
    renderQueue();

    const marks = Array.from(heldJob(/Монтаж вентиляции/).querySelectorAll("mark"));
    expect(marks.map((m) => m.textContent)).toEqual(["Ромашка"]);
    expect(heldJob(/Монтаж вентиляции/)).toHaveTextContent("Монтаж вентиляции ООО «Ромашка» корпус 2");
  });

  it("подсвечено только найденное в строке: слово, совпавшее лишь в списке семей, в строке не подсвечено", () => {
    const job = structuredClone(handlerState.holdJobs[0]);
    job.title = "Монтаж ООО «Ромашка», ЖК Северный";
    job.matches = [
      { text: "ромашка", kind: "contractor", where: "context" },
      { text: "жк северный", kind: "object", where: "family:501" },
    ];
    renderQueue({ hold: fixtureHold({ items: [job], unit_groups: [] }) });

    const marks = Array.from(heldJob(/Монтаж вентиляции|Монтаж ООО/).querySelectorAll("mark"));
    expect(marks.map((m) => m.textContent)).toEqual(["Ромашка"]);
  });

  it("номер договора в строке подсвечен буквально, как его нашёл сервер", () => {
    const job = structuredClone(handlerState.holdJobs[0]);
    job.title = "Монтаж по договору 12 б, доп. 12-б";
    job.matches = [{ text: "12 б", kind: "contract", where: "context" }];
    renderQueue({ hold: fixtureHold({ items: [job], unit_groups: [] }) });

    const marks = Array.from(heldJob(/Монтаж по договору/).querySelectorAll("mark"));
    expect(marks.map((m) => m.textContent)).toEqual(["12 б"]);
  });

  it("место совпадения подписано у каждого слова: в строке и в списке семей", () => {
    renderQueue();

    expect(within(heldJob(/Монтаж вентиляции/)).getByText("«ромашка» — в строке")).toBeInTheDocument();
    const mixed = within(heldJob(/Облицовка стен/)).getAllByTestId("held-match");
    expect(mixed.map((m) => m.textContent)).toEqual([
      "«жк северный» — в строке",
      "«жк северный» — в списке семей (семья 501)",
    ]);
  });

  it("совпадение в тексте промпта подписано «в тексте промпта»", () => {
    const hold = fixtureHold({
      items: [
        {
          ...structuredClone(handlerState.holdJobs[0]),
          matches: [
            { text: "ромашка", kind: "contractor", where: "context" },
            { text: "ромашка", kind: "contractor", where: "prompt" },
          ],
        },
      ],
      unit_groups: [],
    });
    renderQueue({ hold });

    expect(within(heldJob(/Монтаж вентиляции/)).getByText("«ромашка» — в тексте промпта")).toBeInTheDocument();
  });

  it("«Отправить» шлёт ровно показанный набор совпадений", async () => {
    const user = userEvent.setup();
    renderQueue();

    await user.click(within(heldJob(/Облицовка стен/)).getByRole("button", { name: "Отправить" }));

    await waitFor(() => expect(handlerState.privacyReleaseRequests).toHaveLength(1));
    expect(handlerState.privacyReleaseRequests[0]).toEqual({
      jobId: 32,
      matches: [
        { text: "жк северный", kind: "object", where: "context" },
        { text: "жк северный", kind: "object", where: "family:501" },
      ],
    });
    expect(handlerState.privacyDeclineRequests).toEqual([]);
  });

  it("«Не отправлять» шлёт тот же показанный набор на другой маршрут", async () => {
    const user = userEvent.setup();
    renderQueue();

    await user.click(within(heldJob(/Монтаж вентиляции/)).getByRole("button", { name: "Не отправлять" }));

    await waitFor(() => expect(handlerState.privacyDeclineRequests).toHaveLength(1));
    expect(handlerState.privacyDeclineRequests[0]).toEqual({
      jobId: 31,
      matches: [{ text: "ромашка", kind: "contractor", where: "context" }],
    });
    expect(handlerState.privacyReleaseRequests).toEqual([]);
  });

  it("набор не пересобирается на клиенте: уходит то, что пришло, даже если в строке слова нет", async () => {
    const user = userEvent.setup();
    const job = structuredClone(handlerState.holdJobs[0]);
    job.matches = [{ text: "слово, которого в строке нет", kind: "object", where: "context" }];
    renderQueue({ hold: fixtureHold({ items: [job], unit_groups: [] }) });

    await user.click(screen.getByRole("button", { name: "Отправить" }));

    await waitFor(() => expect(handlerState.privacyReleaseRequests).toHaveLength(1));
    expect(handlerState.privacyReleaseRequests[0].matches).toEqual([
      { text: "слово, которого в строке нет", kind: "object", where: "context" },
    ]);
  });

  it("409 job_changed на «Отправить»: «задание изменилось, обновите экран»", async () => {
    const user = userEvent.setup();
    handlerState.jobsConflict = true;
    renderQueue();

    await user.click(within(heldJob(/Монтаж вентиляции/)).getByRole("button", { name: "Отправить" }));

    expect(await screen.findByText("Задание изменилось, обновите экран.")).toBeInTheDocument();
    expect(handlerState.jobsConflictCalls).toBe(1);
  });

  it("409 job_changed на «Не отправлять» и на «Отправить все K» говорит то же", async () => {
    const user = userEvent.setup();
    handlerState.jobsConflict = true;
    renderQueue();

    await user.click(within(heldJob(/Монтаж вентиляции/)).getByRole("button", { name: "Не отправлять" }));
    expect(await screen.findByText("Задание изменилось, обновите экран.")).toBeInTheDocument();
    expect(handlerState.jobsConflictCalls).toBe(1);

    await user.click(screen.getByRole("button", { name: "Отправить все 3" }));
    await waitFor(() => expect(screen.getAllByText("Задание изменилось, обновите экран.")).toHaveLength(2));
    expect(handlerState.jobsConflictCalls).toBe(2);
  });
});

describe("ErrorsQueue — строки единицы с похожими наборами", () => {
  it("два набора, различимых только местом второго совпадения, — две строки без повтора ключа", () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const inFamily501 = { text: "жк северный", kind: "object", where: "family:501" };
    const inFamily502 = { text: "жк северный", kind: "object", where: "family:502" };
    try {
      renderQueue({
        hold: fixtureHold({
          items: [],
          unit_groups: [
            group({ matches: [inFamily501], jobs_count: 3 }),
            group({ matches: [inFamily501, inFamily502], jobs_count: 2 }),
          ],
        }),
      });

      expect(screen.getAllByTestId("unit-hold-group")).toHaveLength(2);
      const keyWarnings = errorSpy.mock.calls.filter((call) =>
        call.some((arg) => typeof arg === "string" && arg.includes("same key"))
      );
      expect(keyWarnings).toEqual([]);
    } finally {
      errorSpy.mockRestore();
    }
  });
});

describe("ErrorsQueue — одинаковое совпадение в промпте у разных единиц", () => {
  it("совпадение в тексте промпта у двух единиц — две строки без повтора ключа", () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const inPrompt = [{ text: "ромашка", kind: "contractor", where: "prompt" }];
    try {
      renderQueue({
        hold: fixtureHold({
          items: [],
          unit_groups: [
            group({ place: "prompt", family_id: null, family_title: null, matches: inPrompt }),
            group({
              unit_id: null,
              unit_code: null,
              place: "prompt",
              family_id: null,
              family_title: null,
              matches: inPrompt,
              jobs_count: 2,
            }),
          ],
        }),
      });

      expect(screen.getAllByTestId("unit-hold-group")).toHaveLength(2);
      const keyWarnings = errorSpy.mock.calls.filter((call) =>
        call.some((arg) => typeof arg === "string" && arg.includes("same key"))
      );
      expect(keyWarnings).toEqual([]);
    } finally {
      errorSpy.mockRestore();
    }
  });
});

describe("ErrorsQueue — страницы", () => {
  it("страницы ошибок: при 10 на странице одиннадцатое задание на второй", async () => {
    const user = userEvent.setup();
    const many = Array.from({ length: 11 }, (_, i) => ({
      ...structuredClone(handlerState.errorJobs[0]),
      job_id: 100 + i,
      title: `Задание ${String(i + 1).padStart(2, "0")}`,
    }));
    renderWithProviders(
      <ErrorsQueue
        errors={many}
        hold={fixtureHold({ items: [], unit_groups: [] })}
        unitLabel={unitLabel}
      />
    );

    expect(screen.getAllByTestId("error-job")).toHaveLength(10);
    expect(screen.queryByText("Задание 11 · м²")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    expect(screen.getAllByTestId("error-job")).toHaveLength(1);
    expect(screen.getByText("Задание 11 · м²")).toBeInTheDocument();
  });

  it("страницы задержанных: при 10 на странице одиннадцатое задание на второй", async () => {
    const user = userEvent.setup();
    const many = Array.from({ length: 11 }, (_, i) => ({
      ...structuredClone(handlerState.holdJobs[0]),
      job_id: 200 + i,
      title: `Задержано ${String(i + 1).padStart(2, "0")}`,
    }));
    renderQueue({ errors: false, hold: fixtureHold({ items: many, unit_groups: [] }) });

    expect(screen.getAllByTestId("held-job")).toHaveLength(10);
    expect(screen.queryByText("Задержано 11")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    expect(screen.getAllByTestId("held-job")).toHaveLength(1);
    expect(screen.getByText("Задержано 11")).toBeInTheDocument();
  });
});

describe("ErrorsQueue — совпадение в списке семей одной строкой на единицу", () => {
  it("три задания единицы — одна строка с полным предложением, отдельных строк заданий нет", () => {
    renderQueue();

    const lines = screen.getAllByTestId("unit-hold-group");
    expect(lines).toHaveLength(1);
    expect(lines[0]).toHaveTextContent(
      "Список семей единицы м² содержит „жк северный“ (семья 501) — задержано 3 запроса"
    );
    expect(screen.getAllByTestId("held-job")).toHaveLength(2);
    expect(screen.queryByText(/Устройство покрытия, вариант/)).not.toBeInTheDocument();
  });

  it("«Отправить все K» шлёт единицу и показанный набор совпадений", async () => {
    const user = userEvent.setup();
    renderQueue();

    await user.click(screen.getByRole("button", { name: "Отправить все 3" }));

    await waitFor(() => expect(handlerState.unitPrivacyReleaseRequests).toHaveLength(1));
    expect(handlerState.unitPrivacyReleaseRequests[0]).toEqual({
      unitId: 5,
      matches: [{ text: "жк северный", kind: "object", where: "family:501" }],
    });
    expect(await screen.findByText("3 запроса отправлено.")).toBeInTheDocument();
  });

  it("контексты без единицы: unit_id уходит явным null, а не пропадает", async () => {
    const user = userEvent.setup();
    renderQueue({
      hold: fixtureHold({
        items: [],
        unit_groups: [group({ unit_id: null, unit_code: null, jobs_count: 2 })],
      }),
    });

    expect(screen.getByTestId("unit-hold-group")).toHaveTextContent("Список семей без единицы содержит");
    await user.click(screen.getByRole("button", { name: "Отправить все 2" }));

    await waitFor(() => expect(handlerState.unitPrivacyReleaseRequests).toHaveLength(1));
    expect(handlerState.unitPrivacyReleaseRequests[0].unitId).toBeNull();
  });

  it("«Открыть семью» ведёт на семью из группы", async () => {
    const user = userEvent.setup();
    const onOpenFamily = vi.fn();
    renderQueue({ onOpenFamily });

    await user.click(screen.getByRole("button", { name: "Открыть семью" }));

    expect(onOpenFamily).toHaveBeenCalledWith(501);
  });

  it("семьи в группе нет (совпадение в промпте): «Открыть семью» не показывается, номера семьи в тексте нет", () => {
    renderQueue({
      onOpenFamily: vi.fn(),
      hold: fixtureHold({
        items: [],
        unit_groups: [
          group({
            place: "prompt",
            family_id: null,
            family_title: null,
            matches: [{ text: "ромашка", kind: "contractor", where: "prompt" }],
          }),
        ],
      }),
    });

    const line = screen.getByTestId("unit-hold-group");
    expect(line).toHaveTextContent("Текст промпта единицы м² содержит „ромашка“ — задержано 3 запроса");
    expect(line).not.toHaveTextContent("семья");
    expect(screen.queryByRole("button", { name: "Открыть семью" })).not.toBeInTheDocument();
  });

  it("смешанное место: подлежащее называет и список семей, и промпт", () => {
    renderQueue({
      hold: fixtureHold({
        items: [],
        unit_groups: [
          group({
            place: "mixed",
            matches: [
              { text: "жк северный", kind: "object", where: "family:501" },
              { text: "ромашка", kind: "contractor", where: "prompt" },
            ],
          }),
        ],
      }),
    });

    expect(screen.getByTestId("unit-hold-group")).toHaveTextContent(
      "Список семей и текст промпта единицы м² содержат „жк северный“, „ромашка“ (семья 501)"
    );
  });

  it("один задержанный запрос: «задержан 1 запрос»", () => {
    renderQueue({
      hold: fixtureHold({ items: [], unit_groups: [group({ jobs_count: 1 })] }),
    });
    expect(screen.getByTestId("unit-hold-group")).toHaveTextContent("задержан 1 запрос");
    expect(screen.getByRole("button", { name: "Отправить все 1" })).toBeInTheDocument();
  });

  it("нет ни строк, ни групп — блока нет вовсе", () => {
    renderQueue({ hold: fixtureHold({ items: [], unit_groups: [] }) });
    expect(screen.queryByTestId("privacy-hold")).not.toBeInTheDocument();
  });
});

/**
 * Задание открытия семей (спека 3б §2.13): предмет — единица, а не контекст, поэтому у строки нет
 * ни названия работы, ни ссылки на карточку контекста (`context_id = null`): единица, число
 * имён и текст ошибки либо совпадение.
 */
function discoveryJob(id: number, extra: Partial<JobRow> = {}): JobRow {
  return {
    job_id: id,
    kind: "family_discovery",
    context_id: null,
    names_count: 120,
    title: "Открытие семей",
    unit_id: 5,
    unit_code: "M2",
    article: null,
    path: [],
    status: "error",
    last_error_class: "schema_error",
    error_text: "Номер группы встречается дважды",
    retry_generation: 0,
    attempts_in_generation: 2,
    matches: null,
    updated_at: "2026-10-10T10:00:00+00:00",
    ...extra,
  };
}

describe("ErrorsQueue — задание открытия семей", () => {
  it("строка: подпись и единица, число имён, класс и текст ошибки; ссылки на контекст нет", () => {
    renderWithProviders(
      <ErrorsQueue errors={[discoveryJob(41)]} hold={fixtureHold({ items: [], unit_groups: [] })} unitLabel={unitLabel} />
    );

    const row = screen.getByTestId("error-job");
    expect(within(row).getByText("Открытие семей · м²")).toBeInTheDocument();
    expect(within(row).getByText("имён: 120")).toBeInTheDocument();
    expect(within(row).getByText("ответ не по схеме")).toBeInTheDocument();
    expect(within(row).getByText("Номер группы встречается дважды")).toBeInTheDocument();
    expect(within(row).queryByRole("link")).not.toBeInTheDocument();
    // Единственное действие строки — «Повторить».
    expect(within(row).getAllByRole("button")).toHaveLength(1);
  });

  it("единица «без единицы» и неизвестное число имён (охват изменился) — прочерк, а не ноль", () => {
    renderWithProviders(
      <ErrorsQueue
        errors={[discoveryJob(42, { unit_id: null, unit_code: null, names_count: null })]}
        hold={fixtureHold({ items: [], unit_groups: [] })}
        unitLabel={unitLabel}
      />
    );

    const row = screen.getByTestId("error-job");
    expect(within(row).getByText("Открытие семей · без единицы")).toBeInTheDocument();
    expect(within(row).getByText("имён: —")).toBeInTheDocument();
    expect(row).not.toHaveTextContent("имён: 0");
  });

  it("у обычного задания предложения строки «имён» нет", () => {
    renderQueue();
    for (const row of screen.getAllByTestId("error-job")) {
      expect(row).not.toHaveTextContent("имён:");
    }
    // Ревью задачи 5: то же для задержанных — условие по виду задания стоит в обоих местах.
    const held = screen.getAllByTestId("held-job");
    expect(held.length).toBeGreaterThan(0);
    for (const row of held) {
      expect(row).not.toHaveTextContent("имён:");
    }
  });

  it("«Повторить» шлёт повтор именно этого задания открытия", async () => {
    const user = userEvent.setup();
    handlerState.errorJobs = [...handlerState.errorJobs, discoveryJob(41)];
    renderWithProviders(
      <ErrorsQueue
        errors={structuredClone(handlerState.errorJobs)}
        hold={fixtureHold({ items: [], unit_groups: [] })}
        unitLabel={unitLabel}
      />
    );

    const row = screen.getAllByTestId("error-job").find((r) => /Открытие семей/.test(r.textContent ?? ""))!;
    await user.click(within(row).getByRole("button", { name: "Повторить" }));

    await waitFor(() => expect(handlerState.retryJobRequests).toEqual([41]));
  });

  describe("задержанное проверкой", () => {
    const matches = [{ text: "жк северный", kind: "object", where: "context" }];

    function renderHeld(job: JobRow) {
      renderWithProviders(
        <ErrorsQueue
          errors={[]}
          hold={fixtureHold({ items: [job], unit_groups: [] })}
          unitLabel={unitLabel}
        />
      );
      return screen.getByTestId("held-job");
    }

    it("строка: единица, подпись, число имён и совпадение с местом; ссылки на контекст нет", () => {
      const row = renderHeld(discoveryJob(51, { status: "privacy_hold", matches, last_error_class: null, error_text: null }));

      expect(row).toHaveTextContent("м²");
      expect(within(row).getByText("Открытие семей")).toBeInTheDocument();
      expect(within(row).getByText("имён: 120")).toBeInTheDocument();
      expect(within(row).getByText("«жк северный» — в строке")).toBeInTheDocument();
      expect(within(row).queryByRole("link")).not.toBeInTheDocument();
    });

    it("«без единицы» и неизвестное число имён — подписано, не пусто", () => {
      const row = renderHeld(
        discoveryJob(52, { status: "privacy_hold", matches, unit_id: null, unit_code: null, names_count: null })
      );

      expect(row).toHaveTextContent("без единицы");
      expect(within(row).getByText("имён: —")).toBeInTheDocument();
    });

    it("«Отправить» и «Не отправлять» шлют показанный набор по заданию открытия", async () => {
      const user = userEvent.setup();
      const row = renderHeld(discoveryJob(53, { status: "privacy_hold", matches }));

      await user.click(within(row).getByRole("button", { name: "Отправить" }));
      await waitFor(() => expect(handlerState.privacyReleaseRequests).toEqual([{ jobId: 53, matches }]));

      await user.click(within(row).getByRole("button", { name: "Не отправлять" }));
      await waitFor(() => expect(handlerState.privacyDeclineRequests).toEqual([{ jobId: 53, matches }]));
    });
  });
});
