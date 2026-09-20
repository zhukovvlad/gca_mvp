import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";

import TenderCardPage from "./TenderCardPage";
import { qk } from "@/services/queryKeys";
import { handlerState } from "@/test/handlers";
import { sampleRoundUnallocated, sampleTenderCard } from "@/test/fixtures";
import { server } from "@/test/server";
import { createTestQueryClient, renderWithProviders, spyOnDownload } from "@/test/utils";

/**
 * Карточка тендера (спека §2.13, §2.14, задача 12): решётка участник×раунд —
 * ПРЯМОУГОЛЬНИК (у каждого участника есть ячейка в каждом раунде, включая тот,
 * где он впервые появился позже первого этапа), `?round=` выбирает раунд,
 * `BaselineStatus` даёт одно из четырёх состояний §2.14, действия правки и
 * удаления — только `admin`.
 */
/** Зонд текущего URL (задача 12, §2.7): `useLocation` работает у любого узла
 *  внутри `<MemoryRouter>`, ему не нужен собственный совпавший `<Route>` —
 *  поэтому он просто сосед `<Routes>`, а не второй маршрут на тот же путь. */
function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.search}</div>;
}

function currentSearch() {
  return screen.getByTestId("location").textContent;
}

function renderCard(options?: Parameters<typeof renderWithProviders>[1] & { initialRoute?: string }) {
  const { initialRoute = "/tenders/300", ...rest } = options ?? {};
  return renderWithProviders(
    <>
      <Routes>
        <Route path="/tenders/:tenderId" element={<TenderCardPage />} />
      </Routes>
      <LocationProbe />
    </>,
    { initialRoute, ...rest }
  );
}

describe("Карточка тендера (§2.13, §2.14)", () => {
  it("решётка 2×2: «—» у (этап 1, Бета), «нет сметы» у (этап 2, Бета)", async () => {
    renderCard();

    expect(await screen.findByText("ООО Альфа")).toBeInTheDocument();
    const betaRow = screen.getByText("ООО Бета").closest("tr") as HTMLElement;
    const betaCells = within(betaRow).getAllByRole("cell");
    // Участник · этап 1 (3001) · этап 2 (3002) · [удаление]
    expect(betaCells[1]).toHaveTextContent("—");
    expect(betaCells[2]).toHaveTextContent("нет сметы");
  });

  it("без ?round выбран последний по stage_no этап", async () => {
    renderCard();
    // Заголовок панели этапа — `<h2>`, отдельно от кнопки выбора раунда в
    // решётке (у неё та же подпись «Этап N»): проверяем именно панель.
    expect(await screen.findByRole("heading", { name: /Этап 2/, level: 2 })).toBeInTheDocument();
  });

  it("?round=3001 выбирает этап 1 и показывает «Расчётная стоимость загружена» при tenderRoundState=loaded", async () => {
    renderCard({ initialRoute: "/tenders/300?round=3001" });
    expect(await screen.findByRole("heading", { name: /Этап 1/, level: 2 })).toBeInTheDocument();
    expect(screen.getByText(/Расчётная стоимость загружена/)).toBeInTheDocument();
  });

  it('tenderRoundState="changed" → «состав изменён после импорта»', async () => {
    handlerState.tenderRoundState = "changed";
    renderCard({ initialRoute: "/tenders/300?round=3001" });
    expect(await screen.findByText(/Состав раунда изменён после импорта/)).toBeInTheDocument();
  });

  it("member не видит кнопок правки и удаления", async () => {
    renderCard({ initialUser: { id: 2, email: "member@example.com", role: "member" } });
    await screen.findByText("ООО Альфа");

    expect(screen.queryByRole("button", { name: /Правка/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Новый этап/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Удалить этап/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Удалить участника/ })).not.toBeInTheDocument();
  });

  /**
   * Прямоугольник решётки — требование, а не следствие удачных фикстур.
   * Здесь ячейка (этап 1, Бета) убрана из ответа сервера ЦЕЛИКОМ (не просто
   * `offer_id: null` в существующей записи, как в стандартной фикстуре), и
   * грид обязан показать «—», а не молча пропустить клетку — иначе строка
   * Беты имела бы на одну ячейку меньше строки Альфы.
   */
  it("решётка остаётся прямоугольником, даже если сервер не прислал ячейку вовсе", async () => {
    server.use(
      http.get("/api/v1/tenders/:id", () =>
        HttpResponse.json({
          ...sampleTenderCard,
          cells: sampleTenderCard.cells.filter((c) => !(c.round_id === 3001 && c.package_id === 502)),
        })
      )
    );
    renderCard();

    await screen.findByText("ООО Альфа");
    const betaRow = screen.getByText("ООО Бета").closest("tr") as HTMLElement;
    const betaCells = within(betaRow).getAllByRole("cell");
    expect(betaCells[1]).toHaveTextContent("—");
  });
});

/**
 * Интерактивное покрытие четырёх admin-диалогов карточки (finding ревью
 * задачи 12): раньше тесты проверяли только ОТСУТСТВИЕ кнопок у `member`, ни
 * разу не открывая их как `admin` и не проверяя эффект. По образцу
 * `ContractCardPage.test.tsx` ("admin удаляет договор с карточки и уходит со
 * страницы") — тот же приём, на который уже ссылается комментарий
 * `TenderDeleteDialog` в самом компоненте.
 */
describe("Admin-диалоги карточки тендера — открытие и эффект", () => {
  it('"Новый этап" предлагает СЛЕДУЮЩИЙ по порядку номер и шлёт именно его', async () => {
    // sampleTenderCard несёт ДВА раунда (stage_no 1 и 2) — предложенный номер
    // обязан быть 3. Off-by-one (например nextStageNo = rounds.length без +1,
    // или взятый из последнего round.stage_no без +1) дал бы 2 — тест ловит
    // это и по подписи в диалоге, и по фактически отправленному телу.
    let body: Record<string, unknown> | undefined;
    server.use(
      http.post("/api/v1/tenders/:id/rounds", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(sampleTenderCard, { status: 201 });
      })
    );
    const user = userEvent.setup();
    renderCard();
    await screen.findByText("ООО Альфа");

    await user.click(screen.getByRole("button", { name: "Новый этап" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Этап 3");

    await user.type(within(dialog).getByLabelText("Название этапа"), "Третий этап");
    await user.click(within(dialog).getByRole("button", { name: "Создать этап" }));

    await waitFor(() => expect(body).toBeDefined());
    expect(body!.stage_no).toBe(3);
    expect(body!.label).toBe("Третий этап");
  });

  /**
   * Удаление СРЕДНЕГО раунда оставляет дыру в нумерации (этапы [1, 3]) —
   * `rounds.length + 1` предложил бы 3 и столкнулся бы с уже существующим
   * этапом (сервер отвечает 409, диалог без ручного поля номера зацикливается
   * на отказе). Фикстура здесь — НЕ contiguous [1, 2] из предыдущего теста:
   * именно на contiguous паре `rounds.length + 1` и `max(stage_no) + 1` дают
   * одно и то же число и тест не отличил бы формулы.
   */
  it('"Новый этап" после удаления среднего раунда предлагает max(stage_no)+1, а не rounds.length+1', async () => {
    server.use(
      http.get("/api/v1/tenders/:id", () =>
        HttpResponse.json({
          ...sampleTenderCard,
          rounds: [
            sampleTenderCard.rounds[0],
            { ...sampleTenderCard.rounds[1], id: 3003, stage_no: 3 },
          ],
          cells: sampleTenderCard.cells.map((c) => (c.round_id === 3002 ? { ...c, round_id: 3003 } : c)),
        })
      )
    );
    let body: Record<string, unknown> | undefined;
    server.use(
      http.post("/api/v1/tenders/:id/rounds", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(sampleTenderCard, { status: 201 });
      })
    );
    const user = userEvent.setup();
    renderCard();
    await screen.findByText("ООО Альфа");

    await user.click(screen.getByRole("button", { name: "Новый этап" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Этап 4");

    await user.click(within(dialog).getByRole("button", { name: "Создать этап" }));

    await waitFor(() => expect(body).toBeDefined());
    expect(body!.stage_no).toBe(4);
  });

  it('"Удалить этап" шлёт id ИМЕННО выбранного раунда (?round=3001), а не первого/последнего', async () => {
    let deletedTenderId: string | undefined;
    let deletedRoundId: string | undefined;
    server.use(
      http.delete("/api/v1/tenders/:id/rounds/:rid", ({ params }) => {
        deletedTenderId = String(params.id);
        deletedRoundId = String(params.rid);
        return new HttpResponse(null, { status: 204 });
      })
    );
    const user = userEvent.setup();
    // Этап 1 (id 3001) выбран явно через `?round=` — если бы кнопка панели
    // слала id какого-то другого раунда (первого в массиве, последнего,
    // «текущего по умолчанию»), этот тест поймал бы несовпадение, а тест по
    // умолчанию (последний раунд) — нет.
    renderCard({ initialRoute: "/tenders/300?round=3001" });
    await screen.findByRole("heading", { name: /Этап 1/, level: 2 });

    await user.click(screen.getByRole("button", { name: "Удалить этап" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Удалить этап 1?");
    await user.click(within(dialog).getByRole("button", { name: "Удалить этап" }));

    await waitFor(() => expect(deletedRoundId).toBe("3001"));
    expect(deletedTenderId).toBe("300");
  });

  it('"Удалить" тендер: гейт по ТОЧНОМУ номеру (обе стороны) и правильный id уходит в запрос', async () => {
    let deletedId: string | undefined;
    server.use(
      http.delete("/api/v1/tenders/:id", ({ params }) => {
        deletedId = String(params.id);
        return new HttpResponse(null, { status: 204 });
      })
    );
    const user = userEvent.setup();
    renderCard();
    await screen.findByText("ООО Альфа");

    await user.click(screen.getByRole("button", { name: "Удалить" }));
    const dialog = await screen.findByRole("alertdialog");
    const confirmBtn = within(dialog).getByRole("button", { name: "Удалить тендер" });
    expect(confirmBtn).toBeDisabled();

    const input = within(dialog).getByLabelText(/Введите номер тендера/);
    // Неверная строка НЕ разблокирует кнопку — иначе опечатка удалила бы не
    // тот тендер.
    await user.type(input, "Т-2026-999");
    expect(confirmBtn).toBeDisabled();

    // Верная строка — разблокирует.
    await user.clear(input);
    await user.type(input, sampleTenderCard.tender_number);
    await waitFor(() => expect(confirmBtn).toBeEnabled());

    await user.click(confirmBtn);

    await waitFor(() => expect(deletedId).toBe(String(sampleTenderCard.id)));
    // Успешное удаление уводит на `/tenders` — в тесте зарегистрирован только
    // маршрут карточки (см. `renderCard`), поэтому вся разметка карточки
    // пропадает; тот же приём, что в `ContractCardPage.test.tsx`.
    await waitFor(() => {
      expect(screen.queryByRole("heading", { name: sampleTenderCard.tender_number })).not.toBeInTheDocument();
    });
  });
});

/**
 * Выбор предложений для свода по этапам (спека 2026-08-27-stage-summary-design.md
 * §2.16, задача 7): плитка есть только у ячейки с offer_id и estimate_id разом;
 * чужие плитки недоступны, пока выбрана хотя бы одна; клик по имени участника
 * выбирает все его сметы; кнопка ведёт на /summary с offer_id по возрастанию.
 *
 * Кнопка следует образцу `ContractsPage` ("Сравнить выбранные (N)"): ниже двух
 * выбранных — обычная disabled-кнопка (роль `button`), от двух — рендерится
 * ссылкой (роль `link`) с адресом. Disabled-ссылка осталась бы кликабельной,
 * поэтому у неактивной кнопки другая роль, а не одна и та же с атрибутом.
 */
describe("Выбор предложений для свода (спека свода §2.1)", () => {
  it("плитки есть только у ячеек со сметой; у «—» и «нет сметы» плиток нет", async () => {
    handlerState.tenderRoundState = "both-loaded";
    renderCard();
    await screen.findByText("ООО Альфа");
    // Обе сметы Альфы (этап 1 — 7001, этап 2 — 7003) — плитки; у Беты плиток
    // нет ни в одном раунде («—» в первом, «нет сметы» во втором).
    const tiles = screen
      .getAllByRole("button", { pressed: false })
      .filter((t) => t.getAttribute("aria-pressed") !== null);
    expect(tiles).toHaveLength(2);
  });

  it("нажатие плитки выбирает; кнопка активна от двух; чужие плитки недоступны", async () => {
    handlerState.tenderRoundState = "both-loaded-with-beta";
    const user = userEvent.setup();
    renderCard();
    await screen.findByText("ООО Альфа");
    expect(screen.getByRole("button", { name: /Свод по этапам/ })).toBeDisabled();

    const alfaRow = screen.getByText("ООО Альфа").closest("tr") as HTMLElement;
    const alfaTiles = within(alfaRow).getAllByRole("button", { pressed: false });
    await user.click(alfaTiles[0]);
    expect(alfaTiles[0]).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /Свод по этапам/ })).toBeDisabled();
    expect(screen.getByText(/выбрано 1 смета · ООО Альфа/)).toBeInTheDocument();

    const betaRow = screen.getByText("ООО Бета").closest("tr") as HTMLElement;
    const betaTile = within(betaRow).getByRole("button", { pressed: false });
    // Недоступность — через ARIA, не через нативный `disabled` (fix round 2):
    // плитка остаётся фокусируемой (не выпадает из Tab), а причину несёт
    // accessible description, а не только hover-title.
    expect(betaTile).toBeEnabled();
    expect(betaTile).toHaveAttribute("aria-disabled", "true");
    expect(betaTile).toHaveAccessibleDescription("Свод строится по одному участнику");

    // Раз плитка не задизейблена нативно, нажатие на неё должно игнорироваться
    // кодом обработчика — иначе то, что раньше давал `disabled` бесплатно,
    // молча перестало бы работать.
    await user.click(betaTile);
    expect(betaTile).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText(/выбрано 1 смета · ООО Альфа/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Свод по этапам (1)" })).toBeDisabled();

    await user.click(alfaTiles[1]);
    // От двух выбранных кнопка становится ссылкой (роль меняется с button на
    // link) — старую ссылку на `button` из начала теста здесь уже не читаем,
    // после ре-рендера это другой DOM-узел.
    const link = screen.getByRole("link", { name: "Свод по этапам (2)" });
    expect(link).toHaveAttribute("href", "/tenders/300/summary?offers=7001&offers=7003");
  });

  it("клик по имени участника выбирает все его сметы; повторный снимает", async () => {
    handlerState.tenderRoundState = "both-loaded";
    const user = userEvent.setup();
    renderCard();
    const name = await screen.findByRole("button", { name: "ООО Альфа" });
    await user.click(name);
    expect(screen.getAllByRole("button", { pressed: true })).toHaveLength(2);
    await user.click(name);
    expect(screen.queryAllByRole("button", { pressed: true })).toHaveLength(0);
  });

  /**
   * Находка ревью (fix round 1): клик по имени участника обязан брать сметы
   * ТОЛЬКО там, где есть и offer_id, и estimate_id. У Альфы во втором раунде
   * offer_id есть, а estimate_id — нет («нет сметы»); без фильтра по обоим id
   * это предложение молча попало бы в выбор без единой плитки на экране, а
   * сервер свода отказал бы кодом «у предложения нет сметы». Проверяем
   * именно ЧИСЛО — а не только факт единственной нажатой плитки — иначе тест
   * не отличил бы «взяли одну» от «взяли обе, но нарисовали плитку только
   * одной».
   */
  it("клик по имени берёт только сметы с обоими id; предложение без сметы не входит в выбор", async () => {
    handlerState.tenderRoundState = "second-round-no-estimate";
    const user = userEvent.setup();
    renderCard();
    const name = await screen.findByRole("button", { name: "ООО Альфа" });
    await user.click(name);

    expect(screen.getAllByRole("button", { pressed: true })).toHaveLength(1);
    expect(screen.getByText(/выбрано 1 смета · ООО Альфа/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Свод по этапам (1)" })).toBeDisabled();
  });

  it("без выбора карточка сохраняет факты решётки: «—», «нет сметы», суммы", async () => {
    renderCard();
    await screen.findByText("ООО Альфа");
    const betaRow = screen.getByText("ООО Бета").closest("tr") as HTMLElement;
    expect(within(betaRow).getAllByRole("cell")[1]).toHaveTextContent("—");
    expect(within(betaRow).getAllByRole("cell")[2]).toHaveTextContent("нет сметы");
    expect(screen.getByText(/1\s200,00/)).toBeInTheDocument();
  });

  /**
   * Находка финального ревью (fix round 3): выбор — id предложений в
   * состоянии страницы, и ничто не сверяло его с перезагруженной картой.
   * Перезалив файла раунда (тот же путь, что в реальности приводит сюда) даёт
   * этому раунду НОВЫЙ offer_id — старый id из выбора отовсюду исчезает,
   * участник по нему больше не находится, и КАЖДАЯ плитка вычисляла бы себя
   * «чужой» — решётка блокировалась бы целиком, а кнопка вела бы на адрес,
   * который сервер отказал бы.
   *
   * Вторую карту доставляем через MSW-хендлер (`server.use`) и инвалидацию
   * запроса — НЕ трогая состояние компонента напрямую: `queryClient` передан
   * в `renderCard`, и именно его инвалидация запускает настоящий рефетч.
   */
  it("перезалив раунда с выбранными плитками снимает только устаревший id, а не весь выбор", async () => {
    handlerState.tenderRoundState = "both-loaded";
    const user = userEvent.setup();
    const queryClient = createTestQueryClient();
    renderCard({ queryClient });
    await screen.findByText("ООО Альфа");

    const alfaRow = screen.getByText("ООО Альфа").closest("tr") as HTMLElement;
    const alfaTiles = within(alfaRow).getAllByRole("button", { pressed: false });
    await user.click(alfaTiles[0]); // этап 1 — offer_id 7001
    await user.click(alfaTiles[1]); // этап 2 — offer_id 7003
    expect(screen.getAllByRole("button", { pressed: true })).toHaveLength(2);

    // Раунд 2 перезалит: у offer_id 7003 больше нет ячейки — на его месте
    // новое предложение 9003 с новой сметой. offer_id 7001 (этап 1) не тронут.
    server.use(
      http.get("/api/v1/tenders/:id", () =>
        HttpResponse.json({
          ...sampleTenderCard,
          cells: sampleTenderCard.cells.map((c) =>
            c.round_id === 3002 && c.package_id === 501
              ? { ...c, offer_id: 9003, estimate_id: 9008, total_including_vat: "1400.00" }
              : c
          ),
        })
      )
    );
    await queryClient.invalidateQueries({ queryKey: qk.tenders.card(sampleTenderCard.id) });

    // 1) Ни одна плитка не показывает пропавшее предложение нажатым — а
    //    нажатых плиток теперь ровно одна (выжившая: offer_id 7001).
    await waitFor(() => {
      expect(screen.getAllByRole("button", { pressed: true })).toHaveLength(1);
    });
    expect(screen.getByText(/1\s?200,00/)).toBeInTheDocument();

    // 2) Счётчик над решёткой и на кнопке совпадает с фактическим числом
    //    нажатых плиток — не отстаёт и не забегает вперёд.
    expect(screen.getByText(/выбрано 1 смета · ООО Альфа/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Свод по этапам (1)" })).toBeDisabled();

    // 3) Решётка не заблокирована целиком: ни одна плитка (Альфы — своя же,
    //    Беты — она вообще без единой сметы в этом состоянии) не отмечена
    //    недоступной. Раньше здесь ломалось именно так: участник по
    //    пропавшему id не находился, и КАЖДАЯ плитка считала себя «чужой».
    const unavailableTiles = screen
      .getAllByRole("button")
      .filter((b) => b.getAttribute("aria-disabled") === "true");
    expect(unavailableTiles).toHaveLength(0);
  });
});

/**
 * Кнопка «Изменения КП» (спека 2026-09-16-tender-changes-export-design.md
 * §2.1, §2.11, план фичи, Task 5) — книга собирается по ВСЕМ участникам
 * тендера с двумя и более сметами, поэтому кнопка стоит рядом со «Сводом по
 * этапам», но, в отличие от него, НЕ зависит от выбора плиток на решётке.
 */
describe("Кнопка «Изменения КП» (спека 2026-09-16-tender-changes-export-design.md §2.1, §2.11)", () => {
  it("активна без единого выбранного этапа и запрашивает книгу по id тендера карточки", async () => {
    const user = userEvent.setup();
    renderCard();
    await screen.findByText("ООО Альфа");

    // Ничего не выбрано на решётке — «Свод по этапам» неактивен, а «Изменения
    // КП» ему не пара в этом смысле: ей нечего выбирать (спека §2.1 — книга
    // на всех участников и все их этапы).
    expect(screen.getByRole("button", { name: /Свод по этапам/ })).toBeDisabled();
    const button = screen.getByRole("button", { name: /Изменения КП/ });
    expect(button).toBeEnabled();

    await user.click(button);

    await waitFor(() =>
      expect(handlerState.lastChangesExportTenderId).toBe(sampleTenderCard.id)
    );
  });

  /**
   * Внешнее ревью H3: `useTenderChangesExport` принимает номер тендера от
   * вызывающего (тем же приёмом, что `useContractSummaryReport` принимает
   * `filename`) — карточка обязана его передать, а не звать хук с одним
   * `tenderId`, иначе имя файла на диске осталось бы зашитой строкой,
   * неразличимой между тендерами.
   */
  it("имя скачанного файла несёт номер тендера карточки, а не зашитую строку", async () => {
    const user = userEvent.setup();
    renderCard();
    await screen.findByText("ООО Альфа");

    const download = spyOnDownload();
    try {
      await user.click(screen.getByRole("button", { name: /Изменения КП/ }));
      await waitFor(() => expect(download.lastFilename()).toBeDefined());
      expect(download.lastFilename()).toContain(sampleTenderCard.tender_number);
      expect(download.lastFilename()).not.toBe("Изменения КП.xlsx");
    } finally {
      download.restore();
    }
  });

  it("422 no_comparable_participants доходит до человека ТЕКСТОМ СЕРВЕРА, а не «Request failed with status code 422»", async () => {
    handlerState.changesExportOutcome = "no_comparable";
    const user = userEvent.setup();
    renderCard();
    await screen.findByText("ООО Альфа");

    await user.click(screen.getByRole("button", { name: /Изменения КП/ }));

    // Блоб-ответ разбирается тем же путём, что у трёх выгрузок §7.6
    // (`toastReportError`/`reportErrorMessage`) — без него человек увидел бы
    // англоязычную заглушку axios вместо причины отказа.
    expect(
      await screen.findByText(/В тендере нет участников с двумя и более сметами/)
    ).toBeInTheDocument();
    expect(screen.queryByText(/Request failed with status code/)).not.toBeInTheDocument();
  });
});

/**
 * Триггер разноса в заголовке этапа решётки и URL-контракт `?unallocated=`
 * (спека этапного разноса §2.7, план — задача 12): три состояния триггера
 * по счётчику `unallocated_pending_sections` раунда, открытие пишет параметр
 * СОХРАНЯЯ прочие, закрытие удаляет только его, а чужой или устаревший
 * `round_id` не запускает GET вовсе — проверка на уровне запроса, а не по
 * видимому отсутствию Sheet (оно ничего не доказывает: и валидный, и
 * невалидный id одинаково не показывают заголовок, пока запрос не ответил).
 */
describe("Триггер разноса и ?unallocated= (§2.7)", () => {
  it("три состояния триггера: тёплый бейдж при 24, тихое «разнести» при 0, ничего при null", async () => {
    // both-loaded трогает только счётчик раунда 3002 (0); 3001 остаётся
    // дефолтным значением фикстуры — 24 (frontend/src/test/fixtures.ts).
    handlerState.tenderRoundState = "both-loaded";
    renderCard();
    const badge = await screen.findByTestId("unallocated-trigger-3001");
    expect(badge).toHaveTextContent("⚠ 24 раздела требуют решения — разнести");
    // Находка B ревью: тёплый и тихий триггер обязаны различаться КЛАССОМ, не
    // только текстом — иначе безусловный тёплый бокс красил бы и полностью
    // разнесённый этап тревожным цветом, который спека резервирует за
    // «больше нуля» (§2.7).
    expect(badge).toHaveClass("border-warning-border", "bg-warning-soft");

    const quiet = screen.getByTestId("unallocated-trigger-3002");
    expect(quiet).toHaveTextContent(/^разнести$/);
    expect(quiet).toHaveClass("text-fg-tertiary");
    expect(quiet).not.toHaveClass("border-warning-border");
  });

  it("тихий триггер при нуле тоже кликабелен: открывает Sheet и пишет параметр — снять ошибочное решение можно и на полностью разнесённом этапе (A)", async () => {
    // Находка A ревью: обёртка обработчика в guard "pending > 0" проходила бы
    // все прежние тесты незамеченной — единственная причина, по которой §2.7
    // держит триггер живым при нуле, это как раз возможность СНЯТЬ решение на
    // уже разнесённом этапе.
    handlerState.tenderRoundState = "both-loaded"; // 3002 -> 0, тихий триггер
    const user = userEvent.setup();
    renderCard();

    await user.click(await screen.findByTestId("unallocated-trigger-3002"));
    expect(await screen.findByRole("heading", { name: "Разнос статей — Этап 2" })).toBeInTheDocument();
    expect(currentSearch()).toBe("?unallocated=3002");
  });

  it("при null триггера нет", async () => {
    // Состояние по умолчанию ("loaded"): у раунда 3002 unallocated_pending_sections
    // — null (нет offer-смет) — кнопки в заголовке этапа не должно быть вовсе.
    renderCard();
    await screen.findByText("ООО Альфа");
    expect(screen.queryByTestId("unallocated-trigger-3002")).toBeNull();
  });

  it("клик пишет ?unallocated=<round_id>, сохраняя ?round=; закрытие удаляет только unallocated", async () => {
    const user = userEvent.setup();
    renderCard({ initialRoute: "/tenders/300?round=3002" });

    await user.click(await screen.findByTestId("unallocated-trigger-3001"));
    expect(await screen.findByRole("heading", { name: "Разнос статей — Этап 1" })).toBeInTheDocument();
    expect(currentSearch()).toBe("?round=3002&unallocated=3001");

    // Имя кнопки закрытия Sheet — русифицированный sr-only текст примитива
    // shadcn (frontend/src/components/ui/sheet.tsx: `<span class="sr-only">Закрыть</span>`).
    await user.click(screen.getByRole("button", { name: /Close|Закрыть/ }));
    await waitFor(() => expect(currentSearch()).toBe("?round=3002"));
  });

  it("чужой ?unallocated=999 и раунд без offer-смет (3002 при null) не запускают запрос", async () => {
    let hits = 0;
    server.use(
      http.get("/api/v1/tenders/:id/rounds/:rid/unallocated", () => {
        hits += 1;
        return HttpResponse.json(sampleRoundUnallocated);
      })
    );

    renderCard({ initialRoute: "/tenders/300?unallocated=999" });
    await screen.findByText("ООО Альфа");
    expect(screen.queryByRole("heading", { name: /Разнос статей/ })).toBeNull();

    cleanup();
    renderCard({ initialRoute: "/tenders/300?unallocated=3002" });
    await screen.findByText("ООО Альфа");
    expect(hits).toBe(0);
  });

  /**
   * Находка D ревью — настоящий пробел поведения, не пробел теста: фоновый
   * рефетч карточки может обнулить счётчик УЖЕ открытого раунда (его
   * offer-сметы пропали). `unallocatedRound` тогда становится `undefined`, и
   * проп `open` у `UnallocatedSheet` падает в `false` КАК ПРОП — колбэк
   * `onOpenChange` у контролируемого диалога на смену пропа не зовётся (это
   * не пользовательское закрытие), и мёртвый `?unallocated=` остался бы в
   * адресе навсегда, если бы страница не сняла его сама.
   *
   * Открываем Sheet СРАЗУ по URL (без клика) — заодно доказывает прямую
   * загрузку по ссылке; затем чиним карточку сервера так, будто раунд лишился
   * offer-смет, и инвалидируем её тем же приёмом, что и тест «перезалив
   * раунда» выше (реальный рефетч через `queryClient`, а не подмена состояния
   * компонента напрямую).
   */
  it("рефетч карточки обнуляет счётчик открытого раунда — параметр снимается сам, Sheet закрывается (D)", async () => {
    const queryClient = createTestQueryClient();
    renderCard({ initialRoute: "/tenders/300?unallocated=3001", queryClient });

    expect(await screen.findByRole("heading", { name: "Разнос статей — Этап 1" })).toBeInTheDocument();
    expect(currentSearch()).toBe("?unallocated=3001");

    server.use(
      http.get("/api/v1/tenders/:id", () =>
        HttpResponse.json({
          ...sampleTenderCard,
          rounds: sampleTenderCard.rounds.map((r) =>
            r.id === 3001 ? { ...r, unallocated_pending_sections: null } : r
          ),
        })
      )
    );
    await queryClient.invalidateQueries({ queryKey: qk.tenders.card(sampleTenderCard.id) });

    await waitFor(() => expect(currentSearch()).toBe(""));
    expect(screen.queryByRole("heading", { name: /Разнос статей/ })).toBeNull();
  });
});
