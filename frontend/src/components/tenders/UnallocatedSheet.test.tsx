import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { UnallocatedSheet } from "./UnallocatedSheet";
import { diagnosticsHeading, positionsLabel } from "@/components/unallocated/roundUnallocatedCopy";
import { handlerState } from "@/test/handlers";
import { sampleRoundUnallocated, sampleTenderCard } from "@/test/fixtures";
import { server } from "@/test/server";
import { createTestQueryClient, renderWithProviders } from "@/test/utils";
import { qk } from "@/services/queryKeys";
import type { RoundUnallocatedSection } from "@/types/domain";

/**
 * Sheet-верстак этапного разноса «Нераспределённого» (спека
 * 2026-09-01-round-unallocated-design.md §2.3, §2.4, §2.5, §2.7; макет
 * 2026-09-01-offer-unallocated-mockup.html; план, задача 11).
 *
 * Все текстовые ожидания — буквально из макета (а не импорт констант
 * `roundUnallocatedCopy.ts`: сравнение константы с собой зелено при любой
 * опечатке в ней) — КРОМЕ словоформ (`describe` в конце файла, находка J
 * ревью): там сравнение с экспортом ядра проверки, а не с самим собой,
 * потому что предмет проверки — ветвление внутри функции, а не текст экрана.
 *
 * Фикстура `sampleRoundUnallocated` (`src/test/fixtures.ts`) — источник ВСЕХ
 * чисел по умолчанию: `offers_count: 1` (не 2 из черновика плана — у раунда
 * 3001 в `sampleTenderCard.cells` только ОДНА offer-смета, у package 502 её
 * нет), категории конфликта/ручной записи — реальные тройки id/code/title
 * классификатора `sampleProjectPassport.category_options` (08/07/09), а не
 * изобретённые в плане 10/11/18. Там, где фикстуры не хватает для различения
 * мутанта (находки D/E/H2/H3 ревью), тесты переопределяют хендлер локально —
 * общая фикстура не трогается.
 */

const ROUND = sampleTenderCard.rounds[0]; // id 3001, stage_no 1, 5 разделов в sections

function renderSheet(props: Partial<Parameters<typeof UnallocatedSheet>[0]> = {}) {
  return renderWithProviders(
    <UnallocatedSheet tenderId={300} round={ROUND} open onOpenChange={() => {}} {...props} />
  );
}

/** Код и название категории рендерятся соседними `<span>` без пробела между
 *  ними — их текстовые узлы склеиваются в ОДНО accessible-имя пункта (тот же
 *  приём, что в `CategoryPicker.test.tsx`). */
function optionName(option: { code: string; title: string }): string {
  return `${option.code}${option.title}`;
}

function mockUnallocated(overrides: Partial<typeof sampleRoundUnallocated>) {
  server.use(
    http.get("/api/v1/tenders/:id/rounds/:rid/unallocated", () =>
      HttpResponse.json({ ...sampleRoundUnallocated, ...overrides })
    )
  );
}

describe("UnallocatedSheet — Sheet-верстак этапного разноса раунда (§2.7)", () => {
  it("ленивый GET: закрытый Sheet не шлёт запрос, открытый — шлёт один", async () => {
    let hits = 0;
    server.use(
      http.get("/api/v1/tenders/:id/rounds/:rid/unallocated", () => {
        hits += 1;
        return HttpResponse.json(sampleRoundUnallocated);
      })
    );

    const { rerender } = renderSheet({ open: false });
    await new Promise((r) => setTimeout(r, 50));
    expect(hits).toBe(0);

    rerender(<UnallocatedSheet tenderId={300} round={ROUND} open onOpenChange={() => {}} />);
    await screen.findByRole("heading", { name: "Разнос статей — Этап 1" });
    expect(hits).toBe(1);
  });

  it("шапка: подзаголовок с числом участников; блок «Требуют решения — 5»; счётчик позиций без денег", async () => {
    renderSheet();

    expect(
      await screen.findByText(
        "Ведомость одна на этап: решение по разделу применяется ко всем сметам раунда (1 участник)."
      )
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Требуют решения — 5" })).toBeInTheDocument();
    // lot_1:3 — «14 SHELL & CORE», rows: 3.
    expect(screen.getByTestId("rows-lot_1:3")).toHaveTextContent("3 позиции");
    // lot_1:4 — «14.1 Маячковый ряд», rows: 2.
    expect(screen.getByTestId("rows-lot_1:4")).toHaveTextContent("2 позиции");
    expect(screen.queryByText(/₽/)).toBeNull();
  });

  /*
   * Ревью задачи 11, находка B: план нёс сокращённые формы подсказок, а
   * ЗАТВЕРЖДЁННЫЙ макет — четыре предложения у «Требуют решения» и другой
   * состав у «Разнесено вручную» («ЕДИНЫМ», «offer-сметах», полный вектор в
   * скобках). Находка A: у диагностики подсказки не было вовсе — в макете она
   * есть и называет три кода. Текст ниже набран заново по макету, а не
   * скопирован из `roundUnallocatedCopy.ts` — иначе тест сравнивал бы
   * константу с собой и был бы зелёным при любой опечатке в ней.
   */
  it("подсказки блоков — полный текст макета, а не сокращённая форма плана", async () => {
    renderSheet();
    await screen.findByRole("heading", { name: "Требуют решения — 5" });

    expect(
      screen.getByText(
        "Файловый порядок ведомости; счётчик «N позиций» — полный размер файлового " +
          "поддерева (стабилен и не зависит от вложенных решений: конфликт возможен " +
          "и при нуле нераспределённых строк). Здесь три из четырёх состояний " +
          "логического раздела: без решения · частичное («разнесено не во всех " +
          "сметах») · конфликт («решения в сметах различаются» — по полному вектору: " +
          "статьи, заметки или аудит). Четвёртое, единое решение, — в блоке ниже."
      )
    ).toBeInTheDocument();

    expect(
      screen.getByText(
        "Только разделы с ЕДИНЫМ решением во всех offer-сметах раунда (единая " +
          "статья, заметка и аудит); частичные и конфликтные — выше, в «Требуют " +
          "решения». «Снять» убирает решение во всех сметах раунда."
      )
    ).toBeInTheDocument();

    expect(
      screen.getByText(
        "Три причины с кодами (границы §5.5 спеки разноса): позиция вне структуры " +
          "файла · structure_disabled · допработа с неразрешимой ссылкой. Показаны " +
          "присутствующие в данных; это диагностика, не действие. Ведро " +
          "unallocated.extras паспорта сюда не годится — оно шире границы и " +
          "содержит допработы, которые разнос раздела закроет."
      )
    ).toBeInTheDocument();
  });

  it("пометки partial и conflict — текстами макета", async () => {
    renderSheet();
    await screen.findByRole("heading", { name: "Требуют решения — 5" });

    // lot_1:20 — «12 Лифтовое оборудование», partial: assigned 1 из total 2.
    expect(screen.getByText("разнесено не во всех сметах: 1 из 2")).toBeInTheDocument();
    // lot_1:30 — «11 Слаботочные системы», conflict с ДВУМЯ статьями.
    expect(
      screen.getByText(
        "решения в сметах различаются: статьи «08 · Слаботочные системы» против «07 · Электромонтажные работы» — нераспределённых строк нет, но этап несогласован"
      )
    ).toBeInTheDocument();
    // lot_1:40 — «16 Пусконаладочные работы», conflict с ОДНОЙ статьёй и audit_differs.
    expect(
      screen.getByText(
        "решения в сметах различаются: статья едина, различается аудит (авторы/даты) — повторное решение выравнивает"
      )
    ).toBeInTheDocument();
    // lot_1:3 — unassigned, пометки нет вовсе.
    expect(
      within(screen.getByTestId("unallocated-section-row-lot_1:3")).queryByText(/разнесено|различаются/)
    ).toBeNull();
  });

  // Находка H2 ревью: ничто не читало порядок сиблингов, поэтому добавленный
  // компаратор (например, по номеру) остался бы незамеченным. Общая фикстура
  // уже не по возрастанию номера в файловом порядке (14, 12, 11, 16 —
  // корневые разделы) — этого достаточно, чтобы отличить файловый порядок от
  // любой сортировки по номеру, включая обратную.
  it("дерево остаётся в файловом порядке ведомости, без сортировки по номеру", async () => {
    renderSheet();
    await screen.findByRole("heading", { name: "Требуют решения — 5" });

    // `SheetContent` рендерится в портал (`document.body`), не в `container`
    // рендера — запрос идёт по всему документу тем же способом, что `screen`.
    const rows = Array.from(
      document.querySelectorAll('[data-testid^="unallocated-section-row-"]')
    ).map((el) => el.getAttribute("data-testid")!.replace("unallocated-section-row-", ""));

    expect(rows).toEqual(["lot_1:3", "lot_1:4", "lot_1:20", "lot_1:30", "lot_1:40"]);
  });

  it("«Разнесено вручную»: запись со статьёй, автором, датой, заметкой; «Снять» шлёт DELETE с ключом раздела; счётчика позиций у ручной строки нет", async () => {
    const user = userEvent.setup();
    renderSheet();
    await screen.findByRole("heading", { name: "Требуют решения — 5" });

    const row = screen.getByTestId("manual-assignment-lot_1:50");
    expect(within(row).getByText("13")).toBeInTheDocument();
    expect(within(row).getByText("Благоустройство территории")).toBeInTheDocument();
    expect(within(row).getByText("→ 09 «Благоустройство»")).toBeInTheDocument();
    expect(within(row).getByText("analyst@mr-group.kz · 29.08.2026")).toBeInTheDocument();
    expect(within(row).getByText("код в файле нечитаем")).toBeInTheDocument();
    // Находка H4 ревью — макет (`.mrow`, 2 колонки) не несёт счётчика позиций
    // у ручных решений, в отличие от дерева выше (`.srow`, 3 колонки); ruling
    // ревью закрепляет `renderManualAside={() => null}`.
    expect(within(row).queryByTestId(/^rows-/)).toBeNull();

    await user.click(within(row).getByTestId("manual-remove-lot_1:50"));

    await waitFor(() =>
      expect(handlerState.roundOverrideRequests).toContainEqual({
        method: "DELETE",
        body: { lot_key: "lot_1", position_key_in_proposal: "50" },
      })
    );
  });

  it("выбор статьи у частичного раздела предзаполняет единую заметку и шлёт её в PUT", async () => {
    const user = userEvent.setup();
    renderSheet();
    await screen.findByRole("heading", { name: "Требуют решения — 5" });

    await user.click(screen.getByTestId("pick-category-lot_1:20"));
    const field = await screen.findByLabelText("Заметка");
    expect(field).toHaveValue("код в файле нечитаем");

    const option = sampleRoundUnallocated.category_options[0];
    await user.click(screen.getByRole("option", { name: optionName(option) }));

    await waitFor(() =>
      expect(handlerState.roundOverrideRequests).toContainEqual({
        method: "PUT",
        body: {
          lot_key: "lot_1",
          position_key_in_proposal: "20",
          work_category_id: option.id,
          note: "код в файле нечитаем",
        },
      })
    );
  });

  // Находка H3 ревью: раньше проверялась только ветка `partial`; unassigned и
  // conflict могли молча читать не тот блок (или всегда отдавать `[]`) без
  // единого красного теста.
  it("поле «Заметка»: у unassigned раздела список заметок пуст", async () => {
    const user = userEvent.setup();
    renderSheet();
    await screen.findByRole("heading", { name: "Требуют решения — 5" });

    // lot_1:3 — unassigned, заметок не бывает ни у кого.
    await user.click(screen.getByTestId("pick-category-lot_1:3"));
    const field = await screen.findByLabelText("Заметка");
    expect(field).toHaveValue("");
  });

  it("поле «Заметка»: у conflict раздела предзаполняется заметкой ИЗ conflict-блока, а не из partial", async () => {
    const user = userEvent.setup();
    // lot_1:30 (conflict) в общей фикстуре несёт `notes: [null]` — неотличимо
    // от «взяли пустой список». Подменяем ТОЛЬКО notes этого раздела, не
    // трогая фикстуру целиком.
    mockUnallocated({
      sections: sampleRoundUnallocated.sections.map((s): RoundUnallocatedSection =>
        s.lot_key === "lot_1" && s.position_key_in_proposal === "30" && s.state === "conflict"
          ? { ...s, conflict: { ...s.conflict, notes: ["заметка из конфликта"] } }
          : s
      ),
    });
    renderSheet();
    await screen.findByRole("heading", { name: "Требуют решения — 5" });

    await user.click(screen.getByTestId("pick-category-lot_1:30"));
    const field = await screen.findByLabelText("Заметка");
    expect(field).toHaveValue("заметка из конфликта");
  });

  it("диагностика: заголовок «Не закрывается разносом — 2 строки», участник поимённо, причина, без кнопок", async () => {
    renderSheet();
    const heading = await screen.findByRole("heading", { name: "Не закрывается разносом — 2 строки" });

    const block = heading.parentElement as HTMLElement;
    expect(within(block).getByText("ООО «АНТТЕК»")).toBeInTheDocument();
    expect(within(block).getByText(/«c \+6,650м до \+16,500м»/)).toBeInTheDocument();
    expect(within(block).getByText("ООО «ЕНИГЮН КОНСТРАКШН»")).toBeInTheDocument();
    expect(
      within(block).getAllByText("допработа с неразрешимой ссылкой — статью не от кого наследовать")
    ).toHaveLength(2);
    expect(within(block).queryAllByRole("button")).toHaveLength(0);
  });

  // Находка D ревью: оба диагностических записи общей фикстуры несут rows: 1
  // — мутант, считающий ЗАПИСИ вместо суммы rows, был неотличим. Один rows: 5.
  it("диагностика: заголовок суммирует rows по всем записям, а не считает записи", async () => {
    mockUnallocated({
      diagnostics: [
        { ...sampleRoundUnallocated.diagnostics[0], rows: 5 },
        { ...sampleRoundUnallocated.diagnostics[1], rows: 1 },
      ],
    });
    renderSheet();

    expect(
      await screen.findByRole("heading", { name: "Не закрывается разносом — 6 строк" })
    ).toBeInTheDocument();
  });

  // Находка E ревью: общая фикстура несёт только `unresolved_chapter_ref` —
  // подмена карты причин на двух других кодах осталась бы незамеченной.
  it("диагностика: три кода — три различные причины, каждая на своей строке", async () => {
    mockUnallocated({
      diagnostics: [
        { code: "outside_structure", contractor_title: "Участник А", title: "Строка вне структуры", rows: 1 },
        { code: "structure_disabled", contractor_title: "Участник Б", title: "Погашенная привязка", rows: 1 },
        { code: "unresolved_chapter_ref", contractor_title: "Участник В", title: "Неразрешимая ссылка", rows: 1 },
      ],
    });
    renderSheet();

    const rows = await screen.findAllByTestId("diagnostic-row");
    expect(rows).toHaveLength(3);
    expect(
      within(rows[0]).getByText(
        "позиции вне структуры файла — раздела, которому можно назначить статью, у них нет"
      )
    ).toBeInTheDocument();
    expect(
      within(rows[1]).getByText(
        "привязка по структуре погашена — статьи не привязаны ни к одной строке предложения"
      )
    ).toBeInTheDocument();
    expect(
      within(rows[2]).getByText("допработа с неразрешимой ссылкой — статью не от кого наследовать")
    ).toBeInTheDocument();
  });

  // Находка H1 ревью: пустой список диагностики не проверялся вовсе —
  // рендер пустого блока (заголовок «— 0 строк») остался бы незамеченным.
  it("диагностика: пустой список — блока нет вовсе", async () => {
    mockUnallocated({ diagnostics: [] });
    renderSheet();

    await screen.findByRole("heading", { name: "Требуют решения — 5" });
    expect(screen.queryByRole("heading", { name: /Не закрывается разносом/ })).toBeNull();
    expect(screen.queryByTestId("diagnostic-row")).toBeNull();
  });

  it("404 — «Раунд или его сметы больше недоступны.» в тёплом боксе, «Обновить карточку» закрывает Sheet", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    renderSheet({ round: sampleTenderCard.rounds[1], onOpenChange });

    expect(await screen.findByText("Раунд или его сметы больше недоступны.")).toBeInTheDocument();
    // Находка C ревью: отказ на Sheet — не абзац бегущего текста, а бокс той
    // же формы, что у `InflationRefusalBanner`/`PassportHeader` (`role="alert"`).
    expect(screen.getByRole("alert")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Обновить карточку" }));

    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  // Находка F ревью: ветка 404 обязана выбираться по HTTP-статусу, а не по
  // телу `detail.code` — сервер отдаёт РАЗНЫЕ 404-коды (§2.3: tender_not_found,
  // round_not_found, round_has_no_offer_estimates), и все три обязаны вести на
  // один и тот же путь «раунд недоступен».
  it("404 с другим кодом тела (round_not_found) тоже уходит по статусу, а не по code", async () => {
    server.use(
      http.get("/api/v1/tenders/:id/rounds/:rid/unallocated", () =>
        HttpResponse.json(
          { detail: { code: "round_not_found", message: "Раунд не найден." } },
          { status: 404 }
        )
      )
    );
    renderSheet();

    expect(await screen.findByText("Раунд или его сметы больше недоступны.")).toBeInTheDocument();
    expect(screen.queryByText("Раунд не найден.")).toBeNull();
  });

  // Находка G ревью: удаление инвалидации карточки оставалось бы зелёным —
  // закрытие Sheet проверялось, а обновление счётчика на карточке нет.
  it("«Обновить карточку» инвалидирует карточку тендера в кэше, а не только закрывает Sheet", async () => {
    const user = userEvent.setup();
    const queryClient = createTestQueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    renderWithProviders(
      <UnallocatedSheet tenderId={300} round={sampleTenderCard.rounds[1]} open onOpenChange={() => {}} />,
      { queryClient }
    );

    await screen.findByText("Раунд или его сметы больше недоступны.");
    await user.click(screen.getByRole("button", { name: "Обновить карточку" }));

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: qk.tenders.card(300) });
  });

  it("ошибка сервера — «Не удалось загрузить нераспределённое.» в тёплом боксе, «Повторить» перезапрашивает", async () => {
    const user = userEvent.setup();
    let hits = 0;
    server.use(
      http.get("/api/v1/tenders/:id/rounds/:rid/unallocated", () => {
        hits += 1;
        if (hits === 1) return new HttpResponse(null, { status: 500 });
        return HttpResponse.json(sampleRoundUnallocated);
      })
    );

    renderSheet();
    expect(await screen.findByText("Не удалось загрузить нераспределённое.")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Повторить" }));

    await screen.findByRole("heading", { name: "Требуют решения — 5" });
    expect(screen.queryByText("Не удалось загрузить нераспределённое.")).toBeNull();
    expect(hits).toBe(2);
  });

  // Находка I ревью: `isPending` никогда не наблюдался — скелетон мог не
  // рендериться вовсе, тест остался бы зелёным. `delay` — тот же приём, что в
  // `ProjectPassportPage.test.tsx` (загрузка паспорта).
  it("загрузка — три скелетона в пропорциях макета (§2.7, состояние 2а)", async () => {
    server.use(
      http.get("/api/v1/tenders/:id/rounds/:rid/unallocated", async () => {
        await delay(50);
        return HttpResponse.json(sampleRoundUnallocated);
      })
    );

    renderSheet();
    const skeleton = await screen.findByTestId("unallocated-sheet-skeleton");
    expect(skeleton.children).toHaveLength(3);
  });

  // Находка K ревью: без раунда заголовку неоткуда взять номер этапа — Sheet
  // без содержимого, открытый диалог примитива без `Title`, логирующий
  // предупреждение доступности. Путь недостижим из вызова задачи 12, но
  // ничто не мешает вызвать компонент так напрямую.
  it("открытый Sheet без раунда не рендерит диалог и не падает в console.error", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      renderWithProviders(
        <UnallocatedSheet tenderId={300} round={undefined} open onOpenChange={() => {}} />
      );
      await new Promise((r) => setTimeout(r, 20));

      expect(screen.queryByRole("dialog")).toBeNull();
      expect(errorSpy).not.toHaveBeenCalled();
    } finally {
      errorSpy.mockRestore();
    }
  });
});

// Находка J ревью: оба словоформных хелпера (`positionsWord`, `rowsWord`)
// проверялись только в форме «2-4» (через фикстуру, где `rows` всегда 2-6) —
// формы «1» и «5+» были мертвы. Макет сам показывает все три для позиций
// (1 позиция · 2 позиции · 37 позиций, раздел 2 макета); для строк формы
// проверяются напрямую через экспорт ядра — они не «текст экрана» сами по
// себе, а ветвление внутри функции.
describe("roundUnallocatedCopy — словоформы (§2.7)", () => {
  it("positionsLabel: единственное / два-четыре / много — как в макете (1, 2, 37)", () => {
    expect(positionsLabel(1)).toBe("1 позиция");
    expect(positionsLabel(2)).toBe("2 позиции");
    expect(positionsLabel(37)).toBe("37 позиций");
  });

  it("diagnosticsHeading: единственное / два-четыре / много — три формы слова «строка»", () => {
    expect(diagnosticsHeading(1)).toBe("Не закрывается разносом — 1 строка");
    expect(diagnosticsHeading(2)).toBe("Не закрывается разносом — 2 строки");
    expect(diagnosticsHeading(5)).toBe("Не закрывается разносом — 5 строк");
  });
});
