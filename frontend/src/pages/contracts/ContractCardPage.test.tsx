import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import ContractCardPage from "./ContractCardPage";
import { JOB_POLL_INTERVAL_MS } from "@/services/jobPolling";
import { sampleContractCard, sampleObjects } from "@/test/fixtures";
import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import type { ContractCard } from "@/types/domain";

/**
 * `renderCard` — единственный хелпер файла, настраивающий MSW-ответ карточки,
 * объекта и текущего пользователя (спека §2.9, задача 8). Расширен тремя
 * необязательными полями поверх исходных опций `renderWithProviders`:
 *
 * - `objectAreas` — подменяет площади объекта карточки (id объекта — 10, тот же,
 *   что у ГП-2026-001): тест пустого состояния «ТЭП не заведены» и тест трёх
 *   величин не могут делить один и тот же ответ сервера.
 * - `terms` — подменяет любую из шести коммерческих условий поверх
 *   `sampleContractCard`.
 * - `role` — короткая запись для `initialUser`, не ломающая существующие
 *   вызовы, которые передают `initialUser` напрямую.
 */
function renderCard(
  options?: Parameters<typeof renderWithProviders>[1] & {
    objectAreas?: { above: string | null; under: string | null; total: string | null };
    terms?: Partial<
      Pick<
        ContractCard,
        | "advance_pct"
        | "advance_note"
        | "bank_guarantee_pct"
        | "bank_guarantee_note"
        | "retention_pct"
        | "retention_note"
      >
    >;
    role?: "admin" | "member";
  }
) {
  const { objectAreas, terms, role, ...renderOptions } = options ?? {};

  if (objectAreas) {
    server.use(
      http.get("/api/v1/objects/:id", () =>
        HttpResponse.json({
          ...sampleObjects[0],
          area_aboveground_sp: objectAreas.above,
          area_underground_sp: objectAreas.under,
          area_total_sp: objectAreas.total,
        })
      )
    );
  }

  if (terms) {
    server.use(
      http.get("/api/v1/contracts/:id", () =>
        HttpResponse.json({ ...sampleContractCard, ...terms })
      )
    );
  }

  return renderWithProviders(
    <Routes>
      <Route path="/contracts/:contractId" element={<ContractCardPage />} />
    </Routes>,
    {
      initialRoute: "/contracts/100",
      ...(role
        ? {
            initialUser: {
              id: role === "admin" ? 1 : 2,
              email: role === "admin" ? "admin@example.com" : "member@example.com",
              role,
            },
          }
        : {}),
      ...renderOptions,
    }
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
    /*
     * Замер: ~6,2 с. Время почти целиком — настоящие ожидания: три опроса с
     * интервалом 1,5 с плюс окно 3 с, доказывающее остановку. Ускорить, не потеряв
     * смысл, нельзя: окно обязано быть больше интервала опроса.
     *
     * 60 с — ~10× замера. Фиксированные ожидания от скорости раннера не зависят, но
     * рендер и обработка зависят, а GitHub Actions медленнее; прежние 20 с давали
     * лишь 3× запаса.
     */
    60_000
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

/**
 * ТЭП объекта и коммерческие условия — оба блока на чтение (спека §2.9, §2.5).
 *
 * ТЭП берутся `useObject(contract.object_id)` — ОТДЕЛЬНЫМ запросом, не из
 * карточки договора: `rate_class_id` в карточке — снимок договора, и класс
 * объекта рядом с ним дал бы два поля с одним именем и разным смыслом. Без
 * пустого состояния «ТЭП не заведены» Ф5 давала бы формы для данных, которых
 * нигде не видно до Ф6 (AGENTS.md §9.1).
 */
describe("Карточка договора: ТЭП объекта и коммерческие условия (§2.9, §2.5)", () => {
  it("показывает ТЭП объекта тремя величинами", async () => {
    renderCard({ objectAreas: { above: "62399.70", under: "13341.30", total: "75741.00" } });
    expect(await screen.findByText("62 399,70")).toBeInTheDocument();
    expect(screen.getByText("13 341,30")).toBeInTheDocument();
    expect(screen.getByText("75 741,00")).toBeInTheDocument();
  });

  it("показывает «ТЭП не заведены», когда площадей нет", async () => {
    renderCard({ objectAreas: { above: null, under: null, total: null } });
    expect(await screen.findByText(/тэп не заведены/i)).toBeInTheDocument();
  });

  it("показывает отказ, когда объект не загрузился", async () => {
    /* Регресс на молчаливое исчезновение блока: прежняя редакция рисовала ТЭП
       только при `objectQ.data`, поэтому на отказе запроса объекта карточка
       теряла обязательный блок целиком — без площадей, без пустого состояния и
       без причины. Пустое состояние здесь читалось бы как «ТЭП не заведены»,
       то есть как факт о данных, которого мы не знаем. */
    server.use(
      http.get("/api/v1/objects/:id", () => new HttpResponse(null, { status: 500 }))
    );
    renderCard();
    expect(await screen.findByText(/не удалось загрузить тэп объекта/i)).toBeInTheDocument();
    expect(screen.queryByText(/тэп не заведены/i)).not.toBeInTheDocument();
  });

  it("показывает загрузку, пока ТЭП объекта не пришли", async () => {
    server.use(
      http.get("/api/v1/objects/:id", async () => {
        await delay(50);
        return HttpResponse.json(sampleObjects[0]);
      })
    );
    renderCard();
    expect(await screen.findByText("Загрузка…")).toBeInTheDocument();
    expect(await screen.findByText("75 741,00")).toBeInTheDocument();
  });

  it("не запрашивает объект по подставному id, пока договор не загружен", async () => {
    /* Хук ТЭП вызывается до ранних `return`, то есть при первом рендере
       идентификатора объекта ещё нет. Пока он подставлялся нулём, каждое
       открытие карточки давало лишний `GET /objects/0` со штатным 404. */
    const requested: string[] = [];
    server.use(
      http.get("/api/v1/objects/:id", ({ params }) => {
        requested.push(String(params.id));
        return HttpResponse.json(sampleObjects[0]);
      })
    );
    renderCard();
    await waitFor(() => expect(requested).toContain(String(sampleObjects[0].id)));
    /* Множество, а не «нет нуля»: подставным значением может стать и `0`, и
       `undefined` — смотря где снята защита, в вызове или в самом хуке.
       Утверждение «запрошен ровно этот идентификатор и никакой другой» ловит
       обе мутации, а «нет нуля» пропустило бы вторую. Дубли терпим: их дало бы
       безобидное повторное чтение того же объекта. */
    expect(new Set(requested)).toEqual(new Set([String(sampleObjects[0].id)]));
  });

  it("показывает процент условия вместе с его оговоркой", async () => {
    renderCard({ terms: { advance_pct: "30", advance_note: "двумя траншами" } });
    const advance = await screen.findByTestId("term-advance");
    expect(advance).toHaveTextContent("30");
    expect(advance).toHaveTextContent("двумя траншами");
  });

  it("показывает оговорку и тогда, когда процента нет", async () => {
    renderCard({ terms: { advance_pct: null, advance_note: "аванс не предусмотрен" } });
    expect(await screen.findByTestId("term-advance")).toHaveTextContent("аванс не предусмотрен");
  });

  it("кнопка правки ТЭП открывает диалог объекта", async () => {
    renderCard({ role: "admin" });
    await userEvent.click(await screen.findByRole("button", { name: /тэп объекта/i }));
    expect(await screen.findByLabelText(/наземная/i)).toBeInTheDocument();
  });

  it("кнопки правки ТЭП нет у member", async () => {
    renderCard({ role: "member" });
    // Дожидаемся загрузки карточки тем же способом, что и остальные тесты файла
    // (`findByText(/договор/i)` из плана неоднозначен: карточка уже несёт и
    // хлебную крошку «Договоры», и подпись «Сумма договора» — `findByText`
    // требует единственного совпадения и падает на самой этой строке).
    await screen.findByRole("heading", { name: "ГП-2026-001" });
    expect(screen.queryByRole("button", { name: /тэп объекта/i })).not.toBeInTheDocument();
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
