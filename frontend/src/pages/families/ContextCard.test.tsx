import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { ContextCard } from "@/pages/families/ContextCard";
import {
  BIG_GROUP_CONTEXT_ID,
  BIG_GROUP_SIZE,
  CONFLICT_POSITION_ITEM_IDS,
  LOCATION_ONLY_CONTEXT_ID,
  MIXED_GROUPS_CHAPTER_ITEM_ID,
  MIXED_GROUPS_CONTEXT_ID,
  LONG_LABEL_CONTEXT_ID,
  SAME_PATH_CHAPTER_A,
  SAME_PATH_CHAPTER_B,
  SAME_PATH_CONTEXT_ID,
  SAME_PATH_TARGET_CATEGORY_ID,
  STALE_AND_CONFLICT_POSITION_ITEM_ID,
  STALE_CHAPTER_ITEM_ID,
  STALE_POSITION_ITEM_ID,
  SUFFIX_LABELS_CONTEXT_ID,
  handlerState,
} from "@/test/handlers";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";

/**
 * Карточка контекста и операции над ним (спека
 * `2026-09-25-families-screen-design.md` §2.5, §2.6, §2.8) — панель СПРАВА
 * ОТ СПИСКА, вкладки внутри карточки («Решения», «Членства N», «Журнал»),
 * строки внимания между шапкой и вкладками, членства группами постраничным
 * запросом группы (`members`/`members_truncated` удалены, §2.8 п. 5). Каждый
 * тест открывает СВОЙ контекст фикстуры
 * (`src/test/handlers.ts::initialSemanticContexts`) — состояние, которое он
 * проверяет, названо в id.
 */

const ORDINARY_CONTEXT_ID = 601;
const STALE_CONTEXT_ID = 602;
const CONFLICT_CONTEXT_ID = 603;
const INSUFFICIENT_DESCRIPTION_CONTEXT_ID = 604;
const SYSTEM_CONTEXT_ID = 605; // тот же контекст несёт member_count = 0 (пустой)
const ARCHIVED_CONTEXT_ID = 606;
/** Независимый эталон набора id большой группы — 520 подряд от 80001 (фикстура `bigGroup`). */
const BIG_GROUP_EXPECTED_IDS = Array.from({ length: 520 }, (_, i) => 80001 + i);

function contextFixture(id: number) {
  return handlerState.semanticContexts.find((c) => c.id === id)!;
}

/**
 * Коды полей и событий, которых на экране быть не должно (Global Constraints
 * ветки; спека §2.2) — ищутся по ВИДИМОМУ тексту (`textContent` не включает
 * атрибуты `title`, где коды законно живут у журнала).
 */
const SCREEN_CODE_RE =
  /\b(LOCATION_ONLY|GENERIC_WORK|SUGGESTED|CONFIRMED|NOT_APPLICABLE|WORK|SYSTEM|UNKNOWN|CURRENT|STALE|rule|manual|file|suggestion|default|insufficient_description|nearest_chapter_equals|chapter_chain_contains|chapter_level_equals|context_created|context_split|context_merged|members_moved|members_marked_stale|kind_set|name_role_set|context_family_assigned|context_archived|routing_rules_dropped)\b/;

/**
 * Видимый текст экрана ПО УЗЛАМ, через перевод строки. Склеенный
 * `document.body.textContent` сращивает соседние узлы («смета #5001» + «STALE»
 * = «#5001STALE»), и `\b` между цифрой и латиницей не срабатывает — код в
 * соседнем с числом узле проходил бы проверку молча.
 */
function screenText(): string {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const parts: string[] = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) parts.push(node.textContent ?? "");
  return parts.join("\n");
}

/** Раскрывает ЕДИНСТВЕННУЮ группу вкладки «Членства», чей путь матчит `namePattern`. */
async function openMembershipTab(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("tab", { name: /^Членства/ }));
}

async function expandGroup(user: ReturnType<typeof userEvent.setup>, namePattern: RegExp) {
  await user.click(screen.getByRole("button", { name: namePattern }));
}

describe("ContextCard", () => {
  it("без выбранного контекста показывает заглушку", () => {
    renderWithProviders(<ContextCard contextId={null} />);
    expect(screen.getByText("Контекст не выбран")).toBeInTheDocument();
  });

  it("шапка: наименование, единица, статья СМР, внутри и ручной разнос", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={CONFLICT_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Отделка потолков водоэмульсионным составом")).toBeInTheDocument()
    );
    // Символ единицы (спека §2.8, уточнение 27.09.2026), не код "м2".
    expect(screen.getByText("м²")).toBeInTheDocument();
    expect(screen.getAllByText("статья СМР").length).toBeGreaterThan(0);
    expect(screen.getByText("05.02.03 Оштукатуривание цементно-песчаным раствором")).toBeInTheDocument();
    expect(
      screen.getByText("внутри: 05 Отделочные работы › 05.02 Штукатурные работы")
    ).toBeInTheDocument();
    // Контекст 603 несёт `work_category_source: "manual"` — единственный
    // среди фикстур; отсутствие этой подписи у остальных проверяет
    // следующий тест.
    expect(screen.getByText("ручной разнос")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Журнал" }));
    // Ничто на экране не печатает голый код поля вне подсказки (Global
    // Constraints ветки) — по крайней мере эти пять кодов таблицы §2.2.
    for (const code of ["SUGGESTED", "CONFIRMED", "NOT_APPLICABLE", "LOCATION_ONLY", "GENERIC_WORK"]) {
      expect(screen.queryByText(code)).not.toBeInTheDocument();
    }
    // Код единицы (спека §2.8, уточнение 27.09.2026) — тот же принцип: на
    // экране только символ `unit_symbol`.
    expect(screen.queryByText("м2", { exact: true })).not.toBeInTheDocument();
    expect(screen.queryByText("M2")).not.toBeInTheDocument();
  });

  it("источник статьи `file` не печатает «ручной разнос»", async () => {
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Штукатурка стен цементно-песчаным раствором")).toBeInTheDocument()
    );
    expect(screen.queryByText("ручной разнос")).not.toBeInTheDocument();
  });

  it("строки внимания: устаревшая группа, конфликтные, пустой контекст и их отсутствие", async () => {
    renderWithProviders(<ContextCard contextId={STALE_CONTEXT_ID} />);
    await waitFor(() =>
      expect(
        screen.getByText("Устройство покрытий полов из линолеума")
      ).toBeInTheDocument()
    );
    expect(screen.getByText(/1 позиция из раздела/)).toBeInTheDocument();
    expect(screen.getByText(/07\.01 «Электромонтажные работы»/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Перенести их/ })).toBeInTheDocument();
    expect(screen.queryByText(/пришли слиянием в Review/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Позиций нет/)).not.toBeInTheDocument();
  });

  // Спека §2.6: строка внимания устаревших обязана
  // называть СВОЮ статью («а лежит здесь, в статье …»), не молчать после
  // «а лежит здесь.» — а кнопка обязана называть контекст назначения
  // («наименование × статья»), не голое «Перенести их». Фикстура несёт
  // ОДНУ устаревшую позицию — согласование единственного числа («лежит»,
  // не «лежат») проверяет именно этот вход.
  it("строка внимания устаревших называет СВОЮ статью, а кнопка — контекст назначения (спека §2.6)", async () => {
    renderWithProviders(<ContextCard contextId={STALE_CONTEXT_ID} />);
    await waitFor(() =>
      expect(
        screen.getByText("Устройство покрытий полов из линолеума")
      ).toBeInTheDocument()
    );
    // Своя статья контекста 602 — «05.02.03 Оштукатуривание…» (фикстура `base`).
    const staleLine = screen.getByText(/1 позиция из раздела/).closest("p")!;
    expect(staleLine).toHaveTextContent(
      /, а лежит здесь, в статье 05\.02\.03 «Оштукатуривание цементно-песчаным раствором»\.?/
    );

    const transferButton = screen.getByRole("button", {
      name: "Перенести их в контекст «Устройство покрытий полов из линолеума × 07.01» — раздел 8 Отделочные работы (паркинг, надземная часть МОП) › 8.2 Отделка полов",
    });
    // Видимый текст — начало доступного имени (WCAG 2.5.3 «метка в имени»):
    // aria-label перекрывает имя, и без этой проверки видимая подпись могла бы
    // разойтись с ним молча.
    expect(transferButton).toHaveTextContent(/^Перенести их$/);
  });

  it("строка внимания конфликтных — по сумме conflict_count всех групп", async () => {
    renderWithProviders(<ContextCard contextId={CONFLICT_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText(/3 позиции пришли слиянием в Review/)).toBeInTheDocument()
    );
    expect(
      screen.getByRole("button", { name: "Принять решение цели (все конфликтные)" })
    ).toBeInTheDocument();
    expect(screen.queryByText(/позици[яий] из раздела/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Позиций нет/)).not.toBeInTheDocument();
  });

  it("строка внимания пустого контекста — точный текст спеки", async () => {
    renderWithProviders(<ContextCard contextId={SYSTEM_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Разборка временных перегородок")).toBeInTheDocument()
    );
    expect(
      screen.getByText("Позиций нет: смета заменена. Архивирует оператор.")
    ).toBeInTheDocument();
  });

  it("контекст без устаревших/конфликтных/пустых состояний не несёт ни одной строки внимания", async () => {
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() =>
      expect(
        screen.getByText("Штукатурка стен цементно-песчаным раствором")
      ).toBeInTheDocument()
    );
    expect(screen.queryByText(/позици[яий] из раздела/)).not.toBeInTheDocument();
    expect(screen.queryByText(/пришли слиянием в Review/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Позиций нет/)).not.toBeInTheDocument();
  });

  it("«Перенести их» вызывает пакетный перенос один раз с chapter_item_id и expected_category_id своей записи", async () => {
    const user = userEvent.setup();
    handlerState.staleGroupTransferOverride = {
      results: [
        { position_item_id: 1, outcome: "moved", target_context_id: 9999, error_code: null, message: null },
        { position_item_id: 2, outcome: "moved", target_context_id: 9999, error_code: null, message: null },
        {
          position_item_id: 3,
          outcome: "refused",
          target_context_id: null,
          error_code: "category_changed",
          message: "статья раздела изменилась после показа строки внимания",
        },
      ],
      moved: 2,
      refused: 1,
    };
    renderWithProviders(<ContextCard contextId={STALE_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Перенести их/ })).toBeInTheDocument()
    );

    await user.click(screen.getByRole("button", { name: /Перенести их/ }));

    await waitFor(() =>
      expect(handlerState.lastTransferStaleGroupRequest).toEqual({
        contextId: STALE_CONTEXT_ID,
        body: { chapter_item_ids: [STALE_CHAPTER_ITEM_ID], expected_category_id: 88 },
      })
    );
    expect(await screen.findByText("перенесено 2 из 3")).toBeInTheDocument();
    expect(
      screen.getByText(/статья раздела изменилась после показа строки внимания/)
    ).toBeInTheDocument();
    // Пакет — ОДИН вызов на строку внимания (спека §2.6), не цикл по позициям.
    expect(handlerState.transferStaleGroupCalls).toBe(1);
    // Код доменного отказа на экран не выходит — только его текст.
    expect(screen.queryByText(/category_changed/)).not.toBeInTheDocument();
  });

  it("«Принять решение цели» строки внимания берёт id ВСЕХ конфликтных членств запросом группы", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={CONFLICT_CONTEXT_ID} />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Принять решение цели (все конфликтные)" })
      ).toBeInTheDocument()
    );

    await user.click(screen.getByRole("button", { name: "Принять решение цели (все конфликтные)" }));

    await waitFor(() =>
      expect(handlerState.lastAcceptTargetDecisionRequest).toEqual([
        CONFLICT_POSITION_ITEM_IDS[0],
        CONFLICT_POSITION_ITEM_IDS[1],
        STALE_AND_CONFLICT_POSITION_ITEM_ID,
      ])
    );
  });

  it("вкладки карточки — «Решения», «Членства N», «Журнал»", async () => {
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() => expect(screen.getByRole("tab", { name: "Решения" })).toBeInTheDocument());
    expect(screen.getByRole("tab", { name: "Членства 3" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Журнал" })).toBeInTheDocument();
  });

  it("роль LOCATION_ONLY печатает работу по разделу представительной позиции", async () => {
    renderWithProviders(<ContextCard contextId={LOCATION_ONLY_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Устройство перегородок (по месту)")).toBeInTheDocument()
    );
    expect(screen.getAllByText("место").length).toBeGreaterThan(0);
    expect(
      screen.getByText(/работа по разделу представительной позиции/)
    ).toBeInTheDocument();
    expect(screen.getByText("Устройство перегородок из ГКЛ")).toBeInTheDocument();
  });

  it("другая роль не несёт строки про работу по разделу представительной позиции", async () => {
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() =>
      expect(
        screen.getByText("Штукатурка стен цементно-песчаным раствором")
      ).toBeInTheDocument()
    );
    expect(
      screen.queryByText(/работа по разделу представительной позиции/)
    ).not.toBeInTheDocument();
  });

  it("insufficient_description: подпись сравнимости и подпись семьи называют причину словами", async () => {
    renderWithProviders(<ContextCard contextId={INSUFFICIENT_DESCRIPTION_CONTEXT_ID} />);
    await waitFor(() => expect(screen.getByText("Светильники")).toBeInTheDocument());

    expect(
      screen.getByText("нет — сравнение ставок не производится")
    ).toBeInTheDocument();
    expect(
      screen.getByText("семья не назначена, потому что состав не описан")
    ).toBeInTheDocument();
    // Другая подпись семьи здесь появиться не должна — это ДРУГОЙ факт.
    expect(screen.queryByText("нет семьи")).not.toBeInTheDocument();
  });

  it("строка группы печатает «устаревшее: N · конфликт: M» рядом с числом позиций (ревью задачи 9)", async () => {
    const user = userEvent.setup();
    const mixed = contextFixture(MIXED_GROUPS_CONTEXT_ID);
    mixed.member_paths = [
      { chapter_item_ids: [MIXED_GROUPS_CHAPTER_ITEM_ID], path: ["8 Отделочные работы"], member_count: 7, stale_count: 2, conflict_count: 1 },
      { chapter_item_ids: [], path: [], member_count: 3, stale_count: 0, conflict_count: 3 },
    ];
    renderWithProviders(<ContextCard contextId={MIXED_GROUPS_CONTEXT_ID} />);
    await waitFor(() => expect(screen.getByText("Устройство стяжки пола")).toBeInTheDocument());
    await openMembershipTab(user);
    const chapterRow = screen.getByRole("button", { name: /Раскрыть группу «8 Отделочные работы/ });
    expect(chapterRow).toHaveTextContent("устаревшее: 2 · конфликт: 1");
    const noChapterRow = screen.getByRole("button", { name: "Раскрыть группу «без раздела»" });
    expect(noChapterRow).toHaveTextContent("конфликт: 3");
    expect(noChapterRow).not.toHaveTextContent("устаревшее");
  });

  it("вкладка «Членства» открывается строкой «Позиции лежат в N разных разделах смет» (сверка с макетом 27.09.2026)", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={MIXED_GROUPS_CONTEXT_ID} />);
    await waitFor(() => expect(screen.getByText("Устройство стяжки пола")).toBeInTheDocument());
    await openMembershipTab(user);

    // Фикстура `MIXED_GROUPS_CONTEXT_ID` несёт ДВЕ группы — множественное число.
    expect(screen.getByText("Позиции лежат в 2 разных разделах смет")).toBeInTheDocument();
  });

  it("группы «Членства» — в порядке ответа, группа без раздела — последней даже будучи меньше", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={MIXED_GROUPS_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Устройство стяжки пола")).toBeInTheDocument()
    );
    await openMembershipTab(user);

    const groupTriggers = screen.getAllByRole("button", { name: /Раскрыть группу/ });
    expect(groupTriggers).toHaveLength(2);
    expect(groupTriggers[0]).toHaveAccessibleName(/8 Отделочные работы/);
    expect(groupTriggers[1]).toHaveAccessibleName(/Раскрыть группу «без раздела»/);
    expect(within(groupTriggers[0]).getByText("2")).toBeInTheDocument();
    expect(within(groupTriggers[1]).getByText("1")).toBeInTheDocument();
  });

  it("раскрытие группы грузит её страницу запросом группы", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() =>
      expect(
        screen.getByText("Штукатурка стен цементно-песчаным раствором")
      ).toBeInTheDocument()
    );
    await openMembershipTab(user);
    expect(screen.queryByText("Штукатурка стен, ось А-Б")).not.toBeInTheDocument();

    await expandGroup(user, /Раскрыть группу «8 Отделочные работы/);

    expect(await screen.findByText("Штукатурка стен, ось А-Б")).toBeInTheDocument();
    expect(screen.getByText("Штукатурка стен, ось Б-В")).toBeInTheDocument();
    expect(screen.getByText("Штукатурка стен, ось В-Г")).toBeInTheDocument();
  });

  it("устаревшая строка несёт ТОЛЬКО действие переноса, без действий конфликта", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={STALE_CONTEXT_ID} />);
    await waitFor(() =>
      expect(
        screen.getByText("Устройство покрытий полов из линолеума")
      ).toBeInTheDocument()
    );
    await openMembershipTab(user);
    await expandGroup(user, /Раскрыть группу «8 Отделочные работы/);

    const staleRow = (await screen.findByText("Устройство покрытий полов, ось 1")).closest("tr")!;
    expect(
      within(staleRow).getByRole("button", { name: /Принять предложение переноса/ })
    ).toBeInTheDocument();
    expect(
      within(staleRow).queryByRole("button", { name: /Принять решение цели/ })
    ).not.toBeInTheDocument();
    expect(
      within(staleRow).queryByRole("button", { name: /Перенести в другой контекст/ })
    ).not.toBeInTheDocument();
  });

  it("конфликтная строка несёт ДВА действия конфликта, без действия переноса устаревшего", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={CONFLICT_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Отделка потолков водоэмульсионным составом")).toBeInTheDocument()
    );
    await openMembershipTab(user);
    await expandGroup(user, /Раскрыть группу «8 Отделочные работы/);

    const conflictRow = (await screen.findByText("Отделка потолков, ось 1")).closest("tr")!;
    expect(
      within(conflictRow).getByRole("button", { name: /Принять решение цели/ })
    ).toBeInTheDocument();
    expect(
      within(conflictRow).getByRole("button", { name: /Перенести в другой контекст/ })
    ).toBeInTheDocument();
    expect(
      within(conflictRow).queryByRole("button", { name: /Принять предложение переноса/ })
    ).not.toBeInTheDocument();
    // Конфликт называет, ЧЕЙ контекст разошёлся, а не только факт расхождения.
    expect(within(conflictRow).getByText(/с контекстом 601/)).toBeInTheDocument();
  });

  it("строка разом устаревшая И конфликтная несёт ДВА действия конфликта, без действия переноса", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={CONFLICT_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Отделка потолков водоэмульсионным составом")).toBeInTheDocument()
    );
    await openMembershipTab(user);
    await expandGroup(user, /Раскрыть группу «8 Отделочные работы/);

    const row = (await screen.findByText("Отделка потолков, ось 3")).closest("tr")!;
    expect(
      within(row).getByRole("button", {
        name: `Принять решение цели для позиции ${STALE_AND_CONFLICT_POSITION_ITEM_ID}`,
      })
    ).toBeInTheDocument();
    expect(
      within(row).getByRole("button", {
        name: `Перенести в другой контекст позицию ${STALE_AND_CONFLICT_POSITION_ITEM_ID}`,
      })
    ).toBeInTheDocument();
    expect(
      within(row).queryByRole("button", { name: /Принять предложение переноса/ })
    ).not.toBeInTheDocument();
  });

  it("устаревшее членство: перенос доходит до сервера с expected_category_id из предложения", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={STALE_CONTEXT_ID} />);
    await waitFor(() =>
      expect(
        screen.getByText("Устройство покрытий полов из линолеума")
      ).toBeInTheDocument()
    );
    await openMembershipTab(user);
    await expandGroup(user, /Раскрыть группу «8 Отделочные работы/);
    await screen.findByText("Устройство покрытий полов, ось 1");

    await user.click(
      screen.getByRole("button", {
        name: `Принять предложение переноса для позиции ${STALE_POSITION_ITEM_ID}`,
      })
    );

    await waitFor(() =>
      expect(handlerState.lastAcceptTransferRequest).toEqual({
        positionItemId: STALE_POSITION_ITEM_ID,
        body: { expected_category_id: 88 },
      })
    );
  });

  // MINOR-3 (ревью Fable 27.09.2026): «Принять предложение переноса»
  // (действие ПО ОДНОЙ строке) не снимало выбор с той же позиции — счётчик
  // «Выбрано членств» продолжал считать позицию, которая уже покинула
  // контекст.
  it("выбранная позиция снимается с выбора после «Принять предложение переноса» (MINOR-3)", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={STALE_CONTEXT_ID} />);
    await waitFor(() =>
      expect(
        screen.getByText("Устройство покрытий полов из линолеума")
      ).toBeInTheDocument()
    );
    await openMembershipTab(user);
    await expandGroup(user, /Раскрыть группу «8 Отделочные работы/);
    await screen.findByText("Устройство покрытий полов, ось 1");

    await user.click(screen.getByLabelText(`Выбрать позицию ${STALE_POSITION_ITEM_ID}`));
    expect(screen.getByText("Выбрано членств: 1")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", {
        name: `Принять предложение переноса для позиции ${STALE_POSITION_ITEM_ID}`,
      })
    );

    await waitFor(() => expect(screen.getByText("Выбрано членств: 0")).toBeInTheDocument());
  });

  it("конфликт: «принять решение цели» доходит до сервера с id ровно ЭТОЙ строки", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={CONFLICT_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Отделка потолков водоэмульсионным составом")).toBeInTheDocument()
    );
    await openMembershipTab(user);
    await expandGroup(user, /Раскрыть группу «8 Отделочные работы/);
    await screen.findByText("Отделка потолков, ось 1");

    await user.click(
      screen.getByRole("button", {
        name: `Принять решение цели для позиции ${CONFLICT_POSITION_ITEM_IDS[0]}`,
      })
    );

    await waitFor(() =>
      expect(handlerState.lastAcceptTargetDecisionRequest).toEqual([
        CONFLICT_POSITION_ITEM_IDS[0],
      ])
    );
  });

  it("конфликт: «перенести в другой контекст» открывает форму и доходит до сервера с id ровно ЭТОЙ строки", async () => {
    const user = userEvent.setup();
    // Первый живой сосед — 759, выбирается 760: цель — выбранная, а не первая.
    const conflicted = contextFixture(CONFLICT_CONTEXT_ID);
    conflicted.bucket_contexts = [
      { id: 759, is_default: false, archived_at: null, member_count: 0 },
      ...conflicted.bucket_contexts,
    ];
    renderWithProviders(<ContextCard contextId={CONFLICT_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Отделка потолков водоэмульсионным составом")).toBeInTheDocument()
    );
    await openMembershipTab(user);
    await expandGroup(user, /Раскрыть группу «8 Отделочные работы/);
    await screen.findByText("Отделка потолков, ось 2");

    await user.click(
      screen.getByRole("button", {
        name: `Перенести в другой контекст позицию ${CONFLICT_POSITION_ITEM_IDS[1]}`,
      })
    );
    // Целевой контекст — выбор из живых соседей по корзине (П6), не ввод id.
    await user.click(await screen.findByRole("combobox", { name: "Целевой контекст" }));
    await user.click(await screen.findByRole("option", { name: "контекст #760" }));
    await user.click(screen.getByRole("button", { name: "Перенести" }));

    await waitFor(() =>
      expect(handlerState.lastMoveMembersRequest).toEqual({
        position_item_ids: [CONFLICT_POSITION_ITEM_IDS[1]],
        target_context_id: 760,
        reason: "manual",
      })
    );
  });

  it("разделить/перенести выбранные — по чекбоксам строк, а не по вводу id", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() =>
      expect(
        screen.getByText("Штукатурка стен цементно-песчаным раствором")
      ).toBeInTheDocument()
    );
    await openMembershipTab(user);
    await expandGroup(user, /Раскрыть группу «8 Отделочные работы/);
    await screen.findByText("Штукатурка стен, ось А-Б");

    await user.click(screen.getByLabelText("Выбрать позицию 71001"));
    await user.click(screen.getByLabelText("Выбрать позицию 71003"));
    expect(screen.getByText("Выбрано членств: 2")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Разделить выбранные" }));
    await waitFor(() =>
      expect(handlerState.lastSplitContextRequest).toEqual({
        contextId: ORDINARY_CONTEXT_ID,
        body: { position_item_ids: [71001, 71003], rule: null },
      })
    );
    // Отметки чекбоксов не переживают успешное разделение (ревью задачи 13, П5):
    // «Выбрано членств» возвращается к нулю, а не ссылается на ушедшие членства.
    await waitFor(() => expect(screen.getByText("Выбрано членств: 0")).toBeInTheDocument());
  });

  it("восстановления архивного контекста на экране нет, набор кнопок — по вкладкам", async () => {
    renderWithProviders(<ContextCard contextId={ARCHIVED_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Гидроизоляция фундамента (снят)")).toBeInTheDocument()
    );
    expect(screen.queryByText(/восстанов/i)).not.toBeInTheDocument();

    // Вкладка «Решения» активна по умолчанию — три кнопки, каждая открывает
    // диалог с прежней формой (сверка с макетом 27.09.2026); формы самих
    // диалогов не в DOM, пока диалог не открыт.
    const decisionButtons = screen
      .getAllByRole("button")
      .map((button) => button.getAttribute("aria-label") ?? button.textContent?.trim() ?? "");
    expect(decisionButtons.sort()).toEqual(
      ["Подтвердить вид", "Назначить семью…", "Изменить «что называет»…"].sort()
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Членства 0" }));
    expect(screen.getByText("Членств нет.")).toBeInTheDocument();
    const membershipButton = screen.getByRole("button", { name: "Архивировать" });
    expect(membershipButton).toBeDisabled();
    const membershipButtons = screen
      .getAllByRole("button")
      .map((button) => button.getAttribute("aria-label") ?? button.textContent?.trim() ?? "");
    expect(membershipButtons.sort()).toEqual(
      ["Разделить выбранные", "Перенести выбранные", "Слить контексты", "Архивировать"].sort()
    );
  });

  it("подтверждение вида уходит с выбранным видом — путь переопределения исключений правила вида", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() =>
      expect(
        screen.getByText("Штукатурка стен цементно-песчаным раствором")
      ).toBeInTheDocument()
    );

    // Формы вида/роли/семьи живут в диалоге (сверка с макетом 27.09.2026,
    // `mock-card-decisions.png`) — открываем его СВОЕЙ кнопкой, взаимодействие
    // дальше — внутри диалога (вне него тот же текст носит кнопка-триггер).
    await user.click(screen.getByRole("button", { name: "Подтвердить вид" }));
    const kindDialog = await screen.findByRole("dialog");
    await user.click(within(kindDialog).getByRole("combobox", { name: "Вид работы" }));
    // Подпись — человеческое слово (спека §2.2), не код `SYSTEM`.
    await user.click(await screen.findByRole("option", { name: "система" }));
    await user.click(within(kindDialog).getByRole("button", { name: "Подтвердить вид" }));

    await waitFor(() =>
      expect(handlerState.lastConfirmKindRequest).toEqual({
        contextId: ORDINARY_CONTEXT_ID,
        body: { kind: "SYSTEM" },
      })
    );
  });

  it("подтверждение вида БЕЗ выбора уходит с ТЕКУЩИМ видом карточки, не с WORK по умолчанию", async () => {
    const user = userEvent.setup();
    // Контекст 605 несёт SYSTEM — если бы селект открывался на захардкоженном
    // "WORK", это действие молча переписало бы системный контекст (ревью
    // задачи 13, П1).
    renderWithProviders(<ContextCard contextId={SYSTEM_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Разборка временных перегородок")).toBeInTheDocument()
    );

    await user.click(screen.getByRole("button", { name: "Подтвердить вид" }));
    const kindDialog = await screen.findByRole("dialog");
    await user.click(within(kindDialog).getByRole("button", { name: "Подтвердить вид" }));

    await waitFor(() =>
      expect(handlerState.lastConfirmKindRequest).toEqual({
        contextId: SYSTEM_CONTEXT_ID,
        body: { kind: "SYSTEM" },
      })
    );
  });

  it("подтверждённый вид несёт кнопку снятия подтверждения, отправляющую unconfirm", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={SYSTEM_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Разборка временных перегородок")).toBeInTheDocument()
    );

    await user.click(screen.getByRole("button", { name: "Подтвердить вид" }));
    const kindDialog = await screen.findByRole("dialog");
    await user.click(within(kindDialog).getByRole("button", { name: "Снять подтверждение" }));

    await waitFor(() =>
      expect(handlerState.lastConfirmKindRequest).toEqual({
        contextId: SYSTEM_CONTEXT_ID,
        body: { unconfirm: true },
      })
    );
  });

  // Ревью задачи 9: диалог «Назначить семью…» обязан слать ТО ЖЕ тело, что
  // прежняя инлайн-форма, — ни одного теста на запрос семьи до сих пор не было.
  it("«Назначить семью…» — диалог шлёт family_id выбранной активной семьи", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Штукатурка стен цементно-песчаным раствором")).toBeInTheDocument()
    );
    await user.click(screen.getByRole("button", { name: "Назначить семью…" }));
    const familyDialog = await screen.findByRole("dialog");
    await user.click(within(familyDialog).getByRole("combobox", { name: "Семья" }));
    // id 43 — единственная активная семья фикстуры.
    await user.click(await screen.findByRole("option", { name: "Кровельные работы" }));
    await user.click(within(familyDialog).getByRole("button", { name: "Назначить семью" }));
    await waitFor(() =>
      expect(handlerState.lastAssignFamilyRequest).toEqual({
        contextId: ORDINARY_CONTEXT_ID,
        body: { family_id: 43 },
      })
    );
  });

  it("«Снять семью» в диалоге семьи шлёт family_id: null явно", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Штукатурка стен цементно-песчаным раствором")).toBeInTheDocument()
    );
    await user.click(screen.getByRole("button", { name: "Назначить семью…" }));
    const familyDialog = await screen.findByRole("dialog");
    // Семья выбрана в селекте — «Снять» обязан слать null, а не выбранную
    // (на пустом селекте подмена тела неотличима).
    await user.click(within(familyDialog).getByRole("combobox", { name: "Семья" }));
    await user.click(await screen.findByRole("option", { name: "Кровельные работы" }));
    await user.click(within(familyDialog).getByRole("button", { name: "Снять семью" }));
    await waitFor(() =>
      expect(handlerState.lastAssignFamilyRequest).toEqual({
        contextId: ORDINARY_CONTEXT_ID,
        body: { family_id: null },
      })
    );
    expect(handlerState.lastAssignFamilyRequest!.body).toHaveProperty("family_id", null);
  });

  it("неподтверждённый вид не несёт кнопки снятия подтверждения", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() =>
      expect(
        screen.getByText("Штукатурка стен цементно-песчаным раствором")
      ).toBeInTheDocument()
    );

    await user.click(screen.getByRole("button", { name: "Подтвердить вид" }));
    const kindDialog = await screen.findByRole("dialog");
    expect(
      within(kindDialog).queryByRole("button", { name: "Снять подтверждение" })
    ).not.toBeInTheDocument();
  });

  it("переопределение роли БЕЗ выбора уходит с ТЕКУЩЕЙ ролью карточки, не с WORK по умолчанию", async () => {
    const user = userEvent.setup();
    // Контекст 604 несёт GENERIC_WORK.
    renderWithProviders(<ContextCard contextId={INSUFFICIENT_DESCRIPTION_CONTEXT_ID} />);
    await waitFor(() => expect(screen.getByText("Светильники")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Изменить «что называет»…" }));
    const roleDialog = await screen.findByRole("dialog");
    await user.click(within(roleDialog).getByRole("button", { name: "Переопределить роль" }));

    await waitFor(() =>
      expect(handlerState.lastSetNameRoleRequest).toEqual({
        contextId: INSUFFICIENT_DESCRIPTION_CONTEXT_ID,
        body: { role: "GENERIC_WORK" },
      })
    );
  });

  it("карточка показывает источник назначения семьи рядом с самой семьёй", async () => {
    // Контекст 601 несёт family_source: "manual" → подпись FAMILY_SOURCE_LABEL «оператор».
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() => expect(screen.getByText("Семья работ №1")).toBeInTheDocument());
    const familyLine = screen.getByText("Семья работ №1").closest("div")!;
    expect(within(familyLine).getByText(/оператор/)).toBeInTheDocument();
  });

  it("разделить выбранные с правилом отправляет rule, а без правила — rule: null (явный выбор, а не подразумеваемый)", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() =>
      expect(
        screen.getByText("Штукатурка стен цементно-песчаным раствором")
      ).toBeInTheDocument()
    );
    await openMembershipTab(user);
    await expandGroup(user, /Раскрыть группу «8 Отделочные работы/);
    await screen.findByText("Штукатурка стен, ось А-Б");

    await user.click(screen.getByLabelText("Выбрать позицию 71001"));

    await user.click(screen.getByRole("combobox", { name: "Разделить с правилом или без" }));
    await user.click(await screen.findByText("С правилом"));
    await user.type(screen.getByLabelText("Значение правила"), "Стены");
    await user.click(screen.getByRole("button", { name: "Разделить выбранные" }));

    await waitFor(() =>
      expect(handlerState.lastSplitContextRequest).toEqual({
        contextId: ORDINARY_CONTEXT_ID,
        body: {
          position_item_ids: [71001],
          rule: { kind: "nearest_chapter_equals", value: "Стены" },
        },
      })
    );
  });

  it("вид правила разноса показан человеческим словом, а не кодом", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() =>
      expect(
        screen.getByText("Штукатурка стен цементно-песчаным раствором")
      ).toBeInTheDocument()
    );
    await openMembershipTab(user);
    await user.click(screen.getByRole("combobox", { name: "Разделить с правилом или без" }));
    await user.click(await screen.findByText("С правилом"));

    expect(screen.getByText("ближайший раздел равен")).toBeInTheDocument();
    await user.click(screen.getByRole("combobox", { name: "Вид правила" }));
    expect(
      await screen.findByRole("option", { name: "раздел встречается в цепочке" })
    ).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "раздел на уровне равен" })).toBeInTheDocument();
    expect(screen.queryByText("nearest_chapter_equals")).not.toBeInTheDocument();
    expect(screen.queryByText("chapter_chain_contains")).not.toBeInTheDocument();
    expect(screen.queryByText("chapter_level_equals")).not.toBeInTheDocument();
  });

  it("перенести выбранные — целевой контекст выбирается из живых соседей по корзине, а не вводится id", async () => {
    const user = userEvent.setup();
    // Живой сосед 749 стоит ПЕРВЫМ, а выбирается 750: тело обязано нести
    // выбранную цель, а не первую из списка.
    const ordinary = contextFixture(ORDINARY_CONTEXT_ID);
    ordinary.bucket_contexts = [
      { id: 749, is_default: false, archived_at: null, member_count: 0 },
      ...ordinary.bucket_contexts,
    ];
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() =>
      expect(
        screen.getByText("Штукатурка стен цементно-песчаным раствором")
      ).toBeInTheDocument()
    );
    await openMembershipTab(user);
    await expandGroup(user, /Раскрыть группу «8 Отделочные работы/);
    await screen.findByText("Штукатурка стен, ось Б-В");

    await user.click(screen.getByLabelText("Выбрать позицию 71002"));
    await user.click(screen.getByRole("combobox", { name: "Целевой контекст для переноса выбранных" }));
    await user.click(await screen.findByRole("option", { name: "контекст #750" }));
    await user.click(screen.getByRole("button", { name: "Перенести выбранные" }));

    await waitFor(() =>
      expect(handlerState.lastMoveMembersRequest).toEqual({
        position_item_ids: [71002],
        target_context_id: 750,
        reason: "manual",
      })
    );
  });

  it("слить контексты — цель выбирается из живых соседей по корзине, а не вводится id", async () => {
    const user = userEvent.setup();
    // Первый живой сосед — 749, выбирается 750 (см. тест переноса выше).
    const ordinary = contextFixture(ORDINARY_CONTEXT_ID);
    ordinary.bucket_contexts = [
      { id: 749, is_default: false, archived_at: null, member_count: 0 },
      ...ordinary.bucket_contexts,
    ];
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() =>
      expect(
        screen.getByText("Штукатурка стен цементно-песчаным раствором")
      ).toBeInTheDocument()
    );
    await openMembershipTab(user);

    await user.click(screen.getByRole("combobox", { name: "Слить контекст в целевой" }));
    await user.click(await screen.findByRole("option", { name: "контекст #750" }));
    await user.click(screen.getByRole("button", { name: "Слить контексты" }));

    await waitFor(() =>
      expect(handlerState.lastMergeContextRequest).toEqual({
        contextId: ORDINARY_CONTEXT_ID,
        targetContextId: 750,
      })
    );
  });

  it("«Журнал» печатает события словами, время и автора; код — только в подсказке", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() =>
      expect(
        screen.getByText("Штукатурка стен цементно-песчаным раствором")
      ).toBeInTheDocument()
    );

    await user.click(screen.getByRole("tab", { name: "Журнал" }));

    expect(screen.getByText("контекст создан")).toBeInTheDocument();
    expect(screen.getByText(/24\.09\.2026/)).toBeInTheDocument();
    expect(screen.getByText(/оператор 1/)).toBeInTheDocument();
    // Код — атрибут подсказки, не отдельный видимый узел текста.
    expect(screen.getByTitle("context_created")).toBeInTheDocument();
    expect(screen.queryByText("context_created")).not.toBeInTheDocument();
  });

  it("событие без автора печатает «система»", async () => {
    // Фикстура 602 несёт события с actor_id: 1 — переопределяем на `null`
    // ДО рендера (карточка запрашивается один раз и не перечитывает фикстуру
    // задним числом; сброс — `resetHandlerState` между тестами,
    // `src/test/setup.ts`), ТОЛЬКО для этого прогона.
    const context = handlerState.semanticContexts.find((c) => c.id === STALE_CONTEXT_ID)!;
    context.events = context.events.map((e) => ({ ...e, actor_id: null }));

    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={STALE_CONTEXT_ID} />);
    await waitFor(() =>
      expect(
        screen.getByText("Устройство покрытий полов из линолеума")
      ).toBeInTheDocument()
    );

    await user.click(screen.getByRole("tab", { name: "Журнал" }));

    expect(screen.getAllByText(/система/).length).toBeGreaterThan(0);
  });

  it("галочка группы из 520 позиций при загруженных 20 выбирает 520 id через member-ids, и «Разделить»/«Перенести» отправляют их", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={BIG_GROUP_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Устройство вентиляционных каналов")).toBeInTheDocument()
    );
    await openMembershipTab(user);
    await expandGroup(user, /9 Инженерные системы/);
    // Первая страница — 20 из 520.
    await screen.findByText("Вентканал, узел 1");
    expect(screen.getByText("Вентканал, узел 20")).toBeInTheDocument();
    expect(screen.queryByText("Вентканал, узел 21")).not.toBeInTheDocument();

    await user.click(screen.getByLabelText(/Выбрать группу/));

    await waitFor(() =>
      expect(screen.getByText(`Выбрано членств: ${BIG_GROUP_SIZE}`)).toBeInTheDocument()
    );

    await user.click(screen.getByRole("button", { name: "Разделить выбранные" }));
    await waitFor(() =>
      expect(
        (handlerState.lastSplitContextRequest?.body as { position_item_ids: number[] })
          .position_item_ids
      ).toHaveLength(BIG_GROUP_SIZE)
    );
    // Не только длина — ровно набор id группы (независимый эталон: 80001…80520
    // фикстуры), без повторов и без чужих.
    expect(
      [...(handlerState.lastSplitContextRequest?.body as { position_item_ids: number[] }).position_item_ids].sort(
        (a, b) => a - b
      )
    ).toEqual(BIG_GROUP_EXPECTED_IDS);

    // Снятие галочки группы убирает ровно её id — счётчик возвращается к нулю
    // (после `onSuccess` разделения он уже был обнулён, поэтому проверяем на
    // отдельном контексте: повторно отмечаем и снимаем).
    await user.click(screen.getByLabelText(/Выбрать группу/));
    await waitFor(() =>
      expect(screen.getByText(`Выбрано членств: ${BIG_GROUP_SIZE}`)).toBeInTheDocument()
    );
    await user.click(screen.getByLabelText(/Выбрать группу/));
    await waitFor(() => expect(screen.getByText("Выбрано членств: 0")).toBeInTheDocument());
  });

  it("галочка строки выбирает ОДНУ позицию — счётчик считает набор id, а не отмеченные строки", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={BIG_GROUP_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Устройство вентиляционных каналов")).toBeInTheDocument()
    );
    await openMembershipTab(user);
    await expandGroup(user, /9 Инженерные системы/);
    await screen.findByText("Вентканал, узел 1");

    await user.click(screen.getByLabelText("Выбрать позицию 80001"));
    expect(screen.getByText("Выбрано членств: 1")).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  //  Входы, различающие утверждения, которые тесты выше проверяли на
  //  совпадающих данных.
  // -------------------------------------------------------------------------

  it("строки внимания: две записи stale_groups — две строки; «Перенести их» группы без раздела шлёт chapter_item_ids: null", async () => {
    const user = userEvent.setup();
    const stale = contextFixture(STALE_CONTEXT_ID);
    stale.stale_groups = [
      ...stale.stale_groups,
      {
        chapter_item_ids: [],
        path: [],
        count: 2,
        target_category_id: 89,
        target_category_code: "07.02",
        target_category_title: "Слаботочные системы",
      },
    ];
    renderWithProviders(<ContextCard contextId={STALE_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: /Перенести их/ })).toHaveLength(2)
    );
    const staleLines = screen.getAllByText(/позици[яий] из раздела/);
    expect(staleLines).toHaveLength(2);
    expect(screen.getByText(/07\.02 «Слаботочные системы»/)).toBeInTheDocument();

    // Согласование глагола числом (спека §2.6): «1 позиция» — единственное
    // число («относится», «лежит»), «2 позиции» рядом — множественное
    // («относятся», «лежат»), не одна и та же форма у обеих строк.
    const line1 = staleLines.find((el) => el.closest("p")!.textContent!.startsWith("1 позиция"))!.closest(
      "p"
    )!;
    const line2 = staleLines.find((el) => el.closest("p")!.textContent!.startsWith("2 позиции"))!.closest(
      "p"
    )!;
    expect(line1).toHaveTextContent(/1 позиция из раздела.*относится к статье/);
    expect(line1).toHaveTextContent(/а лежит здесь/);
    expect(line2).toHaveTextContent(/2 позиции из раздела.*относятся к статье/);
    expect(line2).toHaveTextContent(/а лежат здесь/);

    await user.click(
      screen.getByRole("button", {
        name: "Перенести их в контекст «Устройство покрытий полов из линолеума × 07.02» — раздел без раздела",
      })
    );

    await waitFor(() =>
      expect(handlerState.lastTransferStaleGroupRequest).toEqual({
        contextId: STALE_CONTEXT_ID,
        body: { chapter_item_ids: null, expected_category_id: 89 },
      })
    );
    // `toEqual` не отличает отсутствующий ключ от `undefined` — `null`
    // обязан прийти ЯВНО (спека §2.8 п. 4: `null` значит «без раздела»).
    expect(handlerState.lastTransferStaleGroupRequest!.body).toHaveProperty("chapter_item_ids", null);
    expect(handlerState.transferStaleGroupCalls).toBe(1);
    // Итог переноса группы «без раздела» (ревью задачи 9): подпись «без
    // раздела» БЕЗ плашки «в смете» — у группы нет раздела сметы.
    const resultBlock = (await screen.findByText(/^перенесено \d+ из \d+$/)).parentElement!;
    expect(within(resultBlock).getByText("без раздела")).toBeInTheDocument();
    expect(within(resultBlock).queryByText("в смете")).not.toBeInTheDocument();
  });

  it("строка внимания конфликтных — сумма conflict_count по ВСЕМ группам, не по первой", async () => {
    const conflicted = contextFixture(CONFLICT_CONTEXT_ID);
    conflicted.member_paths = [
      { chapter_item_ids: [8803], path: ["8 Отделочные работы"], member_count: 2, stale_count: 0, conflict_count: 2 },
      { chapter_item_ids: [], path: [], member_count: 1, stale_count: 0, conflict_count: 1 },
    ];
    renderWithProviders(<ContextCard contextId={CONFLICT_CONTEXT_ID} />);
    expect(await screen.findByText(/^3 позиции пришли слиянием в Review/)).toBeInTheDocument();
  });

  // Согласование глагола числом (спека §2.6) — единственное число («1
  // позиция … пришла»), не та же форма множественного, что у трёх и более.
  it("строка внимания конфликтных согласует глагол с единственным числом: «1 позиция … пришла»", async () => {
    const conflicted = contextFixture(CONFLICT_CONTEXT_ID);
    conflicted.member_paths = [
      { chapter_item_ids: [8803], path: ["8 Отделочные работы"], member_count: 1, stale_count: 0, conflict_count: 1 },
    ];
    renderWithProviders(<ContextCard contextId={CONFLICT_CONTEXT_ID} />);
    expect(await screen.findByText(/^1 позиция пришла слиянием в Review/)).toBeInTheDocument();
    expect(screen.queryByText(/пришли/)).not.toBeInTheDocument();
  });

  it("«Принять решение цели» строки внимания шлёт ТОЛЬКО конфликтные id ВСЕГО контекста (разные группы, есть неконфликтное)", async () => {
    const user = userEvent.setup();
    const conflicted = contextFixture(CONFLICT_CONTEXT_ID);
    conflicted.members = [
      ...conflicted.members,
      // Неконфликтное членство той же группы — фильтр состояния обязан его отсечь.
      {
        position_item_id: 9204, job_title: "Отделка потолков, ось 4", estimate_id: 5001,
        membership_state: "CURRENT", conflict_at: null, conflict_from_context_id: null,
        routed_by: "default", chapterItemId: 8803,
      },
      // Конфликтное членство ДРУГОЙ группы — селектор «весь контекст» обязан его взять.
      {
        position_item_id: 9205, job_title: "Отделка потолков, вне структуры", estimate_id: 5001,
        membership_state: "CURRENT", conflict_at: "2026-09-24T10:00:00Z", conflict_from_context_id: 601,
        routed_by: "manual", chapterItemId: null,
      },
    ];
    renderWithProviders(<ContextCard contextId={CONFLICT_CONTEXT_ID} />);
    await user.click(
      await screen.findByRole("button", { name: "Принять решение цели (все конфликтные)" })
    );
    await waitFor(() =>
      expect(handlerState.lastAcceptTargetDecisionRequest).toEqual([
        CONFLICT_POSITION_ITEM_IDS[0],
        CONFLICT_POSITION_ITEM_IDS[1],
        STALE_AND_CONFLICT_POSITION_ITEM_ID,
        9205,
      ])
    );
  });

  it("группы «Членства» идут в порядке ответа, а не пересортированы: группа без раздела последней, даже будучи БОЛЬШЕ", async () => {
    const user = userEvent.setup();
    const mixed = contextFixture(MIXED_GROUPS_CONTEXT_ID);
    mixed.member_paths = [
      { chapter_item_ids: [MIXED_GROUPS_CHAPTER_ITEM_ID], path: ["8 Отделочные работы"], member_count: 2, stale_count: 0, conflict_count: 0 },
      { chapter_item_ids: [], path: [], member_count: 5, stale_count: 0, conflict_count: 0 },
    ];
    renderWithProviders(<ContextCard contextId={MIXED_GROUPS_CONTEXT_ID} />);
    await waitFor(() => expect(screen.getByText("Устройство стяжки пола")).toBeInTheDocument());
    await openMembershipTab(user);
    const triggers = screen.getAllByRole("button", { name: /Раскрыть группу/ });
    expect(triggers[0]).toHaveAccessibleName(/8 Отделочные работы/);
    expect(triggers[1]).toHaveAccessibleName("Раскрыть группу «без раздела»");
  });

  it("раскрытие группы «без раздела» грузит ТОЛЬКО её позиции, не весь контекст", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={MIXED_GROUPS_CONTEXT_ID} />);
    await waitFor(() => expect(screen.getByText("Устройство стяжки пола")).toBeInTheDocument());
    await openMembershipTab(user);
    await expandGroup(user, /Раскрыть группу «без раздела»/);
    expect(await screen.findByText("Стяжка пола, вне структуры")).toBeInTheDocument();
    expect(screen.queryByText("Стяжка пола, ось А-Б")).not.toBeInTheDocument();
    expect(screen.queryByText("Стяжка пола, ось Б-В")).not.toBeInTheDocument();
  });

  it("группа больше страницы листается запросом группы: вторая страница — позиции 21…40", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={BIG_GROUP_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Устройство вентиляционных каналов")).toBeInTheDocument()
    );
    await openMembershipTab(user);
    await expandGroup(user, /9 Инженерные системы/);
    await screen.findByText("Вентканал, узел 1");
    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    expect(await screen.findByText("Вентканал, узел 21")).toBeInTheDocument();
    expect(screen.getByText("Вентканал, узел 40")).toBeInTheDocument();
    expect(screen.queryByText("Вентканал, узел 1")).not.toBeInTheDocument();
  });

  it("выбор — объединение множеств: галочка группы добавляет к выбранному, снятие убирает ТОЛЬКО её id, пересечение не считается дважды", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={MIXED_GROUPS_CONTEXT_ID} />);
    await waitFor(() => expect(screen.getByText("Устройство стяжки пола")).toBeInTheDocument());
    await openMembershipTab(user);
    await expandGroup(user, /Раскрыть группу «8 Отделочные работы/);
    await screen.findByText("Стяжка пола, ось А-Б");

    await user.click(screen.getByLabelText("Выбрать позицию 78001"));
    expect(screen.getByText("Выбрано членств: 1")).toBeInTheDocument();

    // Галочка ДРУГОЙ группы добавляет, а не заменяет выбор.
    await user.click(screen.getByLabelText("Выбрать группу «без раздела»"));
    await waitFor(() => expect(screen.getByText("Выбрано членств: 2")).toBeInTheDocument());
    // Ключ группы различает группы (ревью задачи 9): кэш id одной группы не
    // ставит галочку другой.
    expect(screen.getByLabelText(/Выбрать группу «8 Отделочные работы/)).not.toBeChecked();

    // Снятие галочки группы убирает ровно её id — строка 78001 остаётся.
    await user.click(screen.getByLabelText("Выбрать группу «без раздела»"));
    await waitFor(() => expect(screen.getByText("Выбрано членств: 1")).toBeInTheDocument());
    expect(screen.getByLabelText("Выбрать позицию 78001")).toBeChecked();

    // Галочка группы, куда входит уже выбранная 78001: набор {78001, 78002} — 2, не 3.
    await user.click(screen.getByLabelText(/Выбрать группу «8 Отделочные работы/));
    await waitFor(() => expect(screen.getByText("Выбрано членств: 2")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Разделить выбранные" }));
    await waitFor(() => expect(handlerState.lastSplitContextRequest).not.toBeNull());
    const sent = (handlerState.lastSplitContextRequest!.body as { position_item_ids: number[] })
      .position_item_ids;
    expect([...sent].sort((a, b) => a - b)).toEqual([78001, 78002]);
  });

  it("галочка группы из 520: «Перенести выбранные» отправляет ровно 520 id группы", async () => {
    const user = userEvent.setup();
    const big = contextFixture(BIG_GROUP_CONTEXT_ID);
    big.bucket_contexts = [
      ...big.bucket_contexts,
      { id: 761, is_default: false, archived_at: null, member_count: 0 },
    ];
    renderWithProviders(<ContextCard contextId={BIG_GROUP_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Устройство вентиляционных каналов")).toBeInTheDocument()
    );
    await openMembershipTab(user);
    await user.click(screen.getByLabelText(/Выбрать группу/));
    await waitFor(() =>
      expect(screen.getByText(`Выбрано членств: ${BIG_GROUP_SIZE}`)).toBeInTheDocument()
    );
    await user.click(screen.getByRole("combobox", { name: "Целевой контекст для переноса выбранных" }));
    await user.click(await screen.findByRole("option", { name: "контекст #761" }));
    await user.click(screen.getByRole("button", { name: "Перенести выбранные" }));

    await waitFor(() => expect(handlerState.lastMoveMembersRequest).not.toBeNull());
    const body = handlerState.lastMoveMembersRequest as {
      position_item_ids: number[];
      target_context_id: number;
      reason: string;
    };
    expect([...body.position_item_ids].sort((a, b) => a - b)).toEqual(BIG_GROUP_EXPECTED_IDS);
    expect(body.target_context_id).toBe(761);
  });

  // MAJOR-2 (ревью Fable 27.09.2026): секция группы «Членства» не зажимает
  // `page` — после того как группа сужается ниже текущего offset (действие
  // в карточке разделило/перенесло часть группы), таблица остаётся на
  // старой странице, запрос уходит с offset вне выдачи, строки пустые и
  // пейджера больше нет (он рисуется только при `total > MEMBER_PAGE_SIZE`).
  // Обработчик мутации МЕНЯЕТ фикстуру, как это сделал бы сервер (грабля
  // `docs/pitfalls/frontend.md`, последний пункт) — иначе тест зелёный без
  // всякого зажима.
  it("группа зажимает страницу после сужения ниже текущего offset (MAJOR-2)", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("/api/v1/semantic/contexts/:id/split", async ({ params, request }) => {
        const contextId = Number(params.id);
        const body = (await request.json()) as { position_item_ids: number[] };
        handlerState.lastSplitContextRequest = { contextId, body };
        // Группа сужается до РОВНО MEMBER_PAGE_SIZE (20) — меньше того, что
        // требует текущий offset=20 (страница 2). Мутируем ТУ ЖЕ фикстуру,
        // что читают GET /contexts/:id и GET .../members, а не только ответ
        // мутации.
        const context = contextFixture(BIG_GROUP_CONTEXT_ID);
        const kept = context.members.slice(0, 20);
        const movedCount = context.members.length - kept.length;
        context.members = kept;
        context.member_count = kept.length;
        // Фикстура 608 несёт РОВНО одну группу членств — правим её счётчик
        // напрямую, без выбора по `chapter_item_id`.
        context.member_paths = context.member_paths.map((mp) => ({ ...mp, member_count: kept.length }));
        context.bucket_contexts = context.bucket_contexts.map((bc) =>
          bc.id === BIG_GROUP_CONTEXT_ID ? { ...bc, member_count: kept.length } : bc
        );
        return HttpResponse.json({
          new_context_id: 9999,
          moved_members: movedCount,
          rule_id: null,
          default_replaced: true,
        });
      })
    );

    renderWithProviders(<ContextCard contextId={BIG_GROUP_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Устройство вентиляционных каналов")).toBeInTheDocument()
    );
    await openMembershipTab(user);
    await expandGroup(user, /9 Инженерные системы/);
    await screen.findByText("Вентканал, узел 1");
    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await screen.findByText("Вентканал, узел 21");

    await user.click(screen.getByLabelText(/Выбрать группу/));
    await waitFor(() =>
      expect(screen.getByText(`Выбрано членств: ${BIG_GROUP_SIZE}`)).toBeInTheDocument()
    );
    await user.click(screen.getByRole("button", { name: "Разделить выбранные" }));
    await waitFor(() => expect(handlerState.lastSplitContextRequest).not.toBeNull());

    // Перечитанная группа: страница зажата на 1, показаны её строки, а не
    // пустая таблица без пейджера. Само присутствие «узел 1» после мутации,
    // которая переносит с offset=20, доказывает, что запрос ушёл заново с
    // offset=0 — не то же самое кэшированное значение со старой страницы.
    await waitFor(() => expect(screen.queryByText("Вентканал, узел 21")).not.toBeInTheDocument());
    expect(await screen.findByText("Вентканал, узел 1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Следующая страница" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Предыдущая страница" })).not.toBeInTheDocument();
    const lastGroupRequest = handlerState.groupMembersRequests.at(-1)!;
    expect(new URLSearchParams(lastGroupRequest).get("offset")).toBe("0");
  });

  it("плашки: «статья СМР» у статьи шапки и у целевой статьи строки внимания, «в смете» у пути раздела строки внимания", async () => {
    renderWithProviders(<ContextCard contextId={STALE_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Устройство покрытий полов из линолеума")).toBeInTheDocument()
    );
    const headerLine = screen
      .getByText("05.02.03 Оштукатуривание цементно-песчаным раствором")
      .closest("div")!;
    expect(within(headerLine).getByText("статья СМР")).toBeInTheDocument();

    const staleLine = screen.getByText(/позици[яий] из раздела/);
    expect(within(staleLine).getByText("в смете")).toBeInTheDocument();
    expect(
      within(staleLine).getByText("8 Отделочные работы (паркинг, надземная часть МОП) › 8.2 Отделка полов")
    ).toBeInTheDocument();
    expect(within(staleLine).getByText("статья СМР")).toBeInTheDocument();
  });

  it("плашка «в смете» стоит у КАЖДОГО пути группы во вкладке «Членства»", async () => {
    const user = userEvent.setup();
    const ordinary = contextFixture(ORDINARY_CONTEXT_ID);
    ordinary.member_paths = [
      { chapter_item_ids: [8801], path: ["8 Отделочные работы", "8.2 Стены"], member_count: 2, stale_count: 0, conflict_count: 0 },
      { chapter_item_ids: [8811], path: ["8 Отделочные работы", "8.4 Колонны"], member_count: 1, stale_count: 0, conflict_count: 0 },
    ];
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Штукатурка стен цементно-песчаным раствором")).toBeInTheDocument()
    );
    await openMembershipTab(user);
    const triggers = screen.getAllByRole("button", { name: /Раскрыть группу/ });
    expect(triggers).toHaveLength(2);
    for (const trigger of triggers) {
      expect(within(trigger).getByText("в смете")).toBeInTheDocument();
    }
  });

  // -------------------------------------------------------------------------
  //  Редакция 3 (сверка с макетом 27.09.2026): группа членств — ТЕКСТ пути,
  //  а не один раздел — одинаковый путь у разделов РАЗНЫХ смет сливается в
  //  ОДНУ группу экрана.
  // -------------------------------------------------------------------------

  it("два раздела разных смет с одинаковым путём-текстом дают ОДНУ группу членств с суммой count и id обоих (редакция 3)", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={SAME_PATH_CONTEXT_ID} />);
    await waitFor(() => expect(screen.getByText("Облицовка плиткой")).toBeInTheDocument());
    await openMembershipTab(user);

    // ОДНА группа (не две с одинаковым текстом) — единственное число «разделе».
    expect(screen.getByText("Позиции лежат в 1 разделе смет")).toBeInTheDocument();
    const trigger = screen.getByRole("button", { name: /Раскрыть группу/ });
    expect(within(trigger).getByText("5")).toBeInTheDocument();

    // Раскрытие запрашивает ОБА раздела разом — URL несёт два параметра chapter_item_id.
    await user.click(trigger);
    await screen.findByText("Плитка, корпус 1");
    const params = new URLSearchParams(handlerState.groupMembersRequests.at(-1));
    expect(
      params.getAll("chapter_item_id").map(Number).sort((a, b) => a - b)
    ).toEqual([SAME_PATH_CHAPTER_A, SAME_PATH_CHAPTER_B].sort((a, b) => a - b));

    // Галочка группы выбирает id ОБОИХ разделов целиком.
    await user.click(screen.getByLabelText(/Выбрать группу/));
    await waitFor(() => expect(screen.getByText("Выбрано членств: 5")).toBeInTheDocument());
    // Ревью задачи 9: «5» совпало бы и с запросом ВСЕГО контекста (у фикстуры
    // нет членств вне группы) — поэтому проверяется сам запрос `member-ids`:
    // оба раздела, повторённым ключом без скобок (`chapter_item_id[]` сервер
    // не читает — FastAPI ждёт ровно `chapter_item_id`).
    const idsParams = new URLSearchParams(handlerState.groupMemberIdsRequests.at(-1));
    expect(
      idsParams.getAll("chapter_item_id").map(Number).sort((a, b) => a - b)
    ).toEqual([SAME_PATH_CHAPTER_A, SAME_PATH_CHAPTER_B].sort((a, b) => a - b));
    expect(idsParams.has("no_chapter")).toBe(false);
  });

  it("«Перенести их» слитой группы (два раздела, общий путь) шлёт chapter_item_ids ОБОИХ разом (редакция 3)", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={SAME_PATH_CONTEXT_ID} />);

    await user.click(await screen.findByRole("button", { name: /Перенести их/ }));

    await waitFor(() =>
      expect(handlerState.lastTransferStaleGroupRequest).toEqual({
        contextId: SAME_PATH_CONTEXT_ID,
        body: {
          chapter_item_ids: [SAME_PATH_CHAPTER_A, SAME_PATH_CHAPTER_B].sort((a, b) => a - b),
          expected_category_id: SAME_PATH_TARGET_CATEGORY_ID,
        },
      })
    );
    expect(await screen.findByText("перенесено 2 из 2")).toBeInTheDocument();
  });

  // Замер на стенде 27.09.2026, четвёртый круг: подписи путей в ОДНУ
  // строку не держат и общий хвост, и различающее звено разом при узкой
  // колонке (215px) — различающее звено обязано жить на СВОЕЙ, второй
  // строке, а не делить одну строку с хвостом (та отдельно резалась то
  // фиксированным числом звеньев, то `line-clamp-2`, то одной строкой
  // целиком — три предыдущих круга).
  it("группы с общим суффиксом путей показывают общий хвост в строке 1 и различающее звено в строке 2; группа с уникальным суффиксом строки 2 не несёт", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={SUFFIX_LABELS_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Облицовка плиткой (проверка подписи пути)")).toBeInTheDocument()
    );
    await openMembershipTab(user);

    expect(screen.getAllByRole("button", { name: /Раскрыть группу/ })).toHaveLength(4);

    // Три группы делят последние два звена («Лифтовой холл МОП жилья /
    // Потолок») — это СТРОКА 1, общая у всех трёх (не различает их сама по
    // себе). Различающее звено («Корпус N») — СТРОКА 2, своя у каждой.
    const triggerA = screen.getByRole("button", { name: /Корпус 1 ›/ });
    const triggerB = screen.getByRole("button", { name: /Корпус 2 ›/ });
    const triggerC = screen.getByRole("button", { name: /Корпус 3 ›/ });
    for (const trigger of [triggerA, triggerB, triggerC]) {
      expect(within(trigger).getByText("Лифтовой холл МОП жилья / Потолок")).toBeInTheDocument();
    }
    expect(within(triggerA).getByText("Корпус 1")).toBeInTheDocument();
    expect(within(triggerB).getByText("Корпус 2")).toBeInTheDocument();
    expect(within(triggerC).getByText("Корпус 3")).toBeInTheDocument();
    // Строка 2 — РОВНО различающее звено, не хвост вперемешку с ним.
    expect(within(triggerA).queryByText(/Корпус 1 \//)).not.toBeInTheDocument();

    // Четвёртая группа не коллизирует ни с кем — подпись остаётся ДВУМЯ
    // звеньями строки 1, без строки 2 вовсе.
    const triggerD = screen.getByRole("button", { name: /Кухня ›/ });
    expect(within(triggerD).getByText("Кухня / Пол")).toBeInTheDocument();
    expect(within(triggerD).queryByText(/^Пол$/)).not.toBeInTheDocument();
  });

  // Замер на стенде 27.09.2026, четыре круга: CSS `truncate` (обрезка
  // СПРАВА) резал различающее звено; JS-обрезка по числу символов и
  // `line-clamp-2` не сходились ни с какой пиксельной шириной колонки;
  // одна строка не держала хвост И различающее звено разом. Решение —
  // ДВЕ строки (хвост / различающее звено), КАЖДАЯ — браузерное усечение
  // СЛЕВА через `dir="rtl"` (эллипсис в НАЧАЛЕ, хвост строки всегда виден),
  // `<bdi dir="ltr">` восстанавливает порядок символов (кириллица/`/`)
  // внутри развёрнутого контекста. Полный путь — в `title` каждой строки.
  it("строка 2 (различающее звено) — тоже dir=rtl с усечением слева, полный текст внутри bdi dir=ltr, title — полный путь", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={LONG_LABEL_CONTEXT_ID} />);
    await waitFor(() =>
      expect(
        screen.getByText("Облицовка плиткой (проверка обрезки длинной подписи)")
      ).toBeInTheDocument()
    );
    await openMembershipTab(user);

    const triggerA = screen.getByRole("button", { name: /корпус 1 ›/ });
    const fullPath =
      "Секция общественных пространств входной группы жилого дома, корпус 1 › Лифтовой холл МОП жилья › Потолок";

    // Строка 1 — общий хвост («Лифтовой холл МОП жилья / Потолок»), короткий,
    // умещается без вопросов.
    expect(within(triggerA).getByText("Лифтовой холл МОП жилья / Потолок")).toBeInTheDocument();

    // Строка 2 — само различающее звено (длинное само по себе). Разметка
    // несёт ПОЛНЫЙ текст без обрезки — усечение здесь визуальное свойство
    // CSS (`truncate`+`dir`), а не JS-функция, которая обрезала бы строку
    // заранее и потеряла бы то, что не влезло.
    const bdi = within(triggerA).getByText(
      "Секция общественных пространств входной группы жилого дома, корпус 1"
    );
    expect(bdi.tagName).toBe("BDI");
    expect(bdi).toHaveAttribute("dir", "ltr");

    const labelSpan = bdi.parentElement!;
    expect(labelSpan.tagName).toBe("SPAN");
    expect(labelSpan).toHaveAttribute("dir", "rtl");
    expect(labelSpan).toHaveClass("truncate");
    expect(labelSpan).toHaveAttribute("title", fullPath);

    // Строка 1 несёт ТОТ ЖЕ приём разметки и ТОТ ЖЕ полный путь в title.
    const line1Bdi = within(triggerA).getByText("Лифтовой холл МОП жилья / Потолок");
    expect(line1Bdi.tagName).toBe("BDI");
    expect(line1Bdi.parentElement).toHaveAttribute("dir", "rtl");
    expect(line1Bdi.parentElement).toHaveAttribute("title", fullPath);

    // Короткая уникальная подпись («без коллизии») — строки 2 нет вовсе:
    // ровно ОДИН элемент `dir="rtl"` внутри строки, не два.
    const triggerShort = screen.getByRole("button", { name: /Кухня ›/ });
    const bdiShort = within(triggerShort).getByText("Кухня / Пол");
    expect(bdiShort.tagName).toBe("BDI");
    expect(bdiShort.parentElement).toHaveAttribute("dir", "rtl");
    expect(triggerShort.querySelectorAll('[dir="rtl"]')).toHaveLength(1);
  });

  it("плашка «в смете» стоит у работы по разделу представительной позиции", async () => {
    renderWithProviders(<ContextCard contextId={LOCATION_ONLY_CONTEXT_ID} />);
    const title = await screen.findByText("Устройство перегородок из ГКЛ");
    const line = title.closest("div")!;
    expect(line).toHaveTextContent(/работа по разделу представительной позиции/);
    expect(within(line).getByText("в смете")).toBeInTheDocument();
  });

  // Спека §2.2: подпись называет причину, а не молчит «—» (screens.md,
  // Global Constraints) — все три причины `null` (нет членств / представитель
  // без раздела / цепочка из одних мест) честно сведены к «рабочего раздела
  // нет», не пустому прочерку.
  it("LOCATION_ONLY без рабочего раздела: «рабочего раздела нет» без плашки «в смете», «Состав описан» называет причину", async () => {
    const locationOnly = contextFixture(LOCATION_ONLY_CONTEXT_ID);
    locationOnly.representative_work_title = null;
    locationOnly.comparability_reason = "insufficient_description";
    renderWithProviders(<ContextCard contextId={LOCATION_ONLY_CONTEXT_ID} />);
    const line = (await screen.findByText(/работа по разделу представительной позиции/)).closest("div")!;
    expect(within(line).getByText("рабочего раздела нет")).toBeInTheDocument();
    expect(within(line).queryByText("—")).not.toBeInTheDocument();
    expect(within(line).queryByText("в смете")).not.toBeInTheDocument();
    expect(
      screen.getByText("нет — сравнение ставок не производится")
    ).toBeInTheDocument();
  });

  it("LOCATION_ONLY С рабочим разделом: плашка «в смете» и наименование, не «рабочего раздела нет»", async () => {
    renderWithProviders(<ContextCard contextId={LOCATION_ONLY_CONTEXT_ID} />);
    const title = await screen.findByText("Устройство перегородок из ГКЛ");
    const line = title.closest("div")!;
    expect(within(line).getByText("в смете")).toBeInTheDocument();
    expect(within(line).queryByText("рабочего раздела нет")).not.toBeInTheDocument();
  });

  it("событие без автора печатает «система» В СВОЕЙ строке журнала, а не где-то на экране", async () => {
    const user = userEvent.setup();
    const context = contextFixture(STALE_CONTEXT_ID);
    context.events = context.events.map((e) => ({ ...e, actor_id: null }));
    renderWithProviders(<ContextCard contextId={STALE_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Устройство покрытий полов из линолеума")).toBeInTheDocument()
    );
    await user.click(screen.getByRole("tab", { name: "Журнал" }));
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    for (const item of items) {
      expect(item).toHaveTextContent(/ · система$/);
    }
  });

  it("«Перенести их» перечитывает карточку после ответа — строка внимания отражает новое состояние сервера", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={STALE_CONTEXT_ID} />);
    const button = await screen.findByRole("button", { name: /Перенести их/ });
    const cardRequestsBefore = handlerState.contextCardRequests;
    expect(cardRequestsBefore).toBe(1);
    await user.click(button);
    await waitFor(() => expect(handlerState.transferStaleGroupCalls).toBe(1));
    await waitFor(() =>
      expect(handlerState.contextCardRequests).toBeGreaterThan(cardRequestsBefore)
    );
  });

  // Дефект живого прогона на стенде (спека §2.6, DoD §5 п. 6: «экран
  // сообщает «перенесено N из M» и перечисляет отказы»): при ПОЛНОМ успехе
  // перечитанная карточка (тест выше) больше не несёт свою устаревшую
  // группу — строка внимания пропадает. Если результат печатался ВНУТРИ
  // этой строки, «перенесено 1 из 1» пропадало вместе с ней, и оператор его
  // не видел ни разу.
  it("«перенесено N из M» остаётся видимым после исчезновения строки внимания (полный успех, перечитанная карточка)", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("/api/v1/semantic/contexts/:id/stale-groups/transfer", async ({ params, request }) => {
        const contextId = Number(params.id);
        const body = (await request.json()) as {
          chapter_item_ids: number[] | null;
          expected_category_id: number | null;
        };
        handlerState.lastTransferStaleGroupRequest = { contextId, body };
        handlerState.transferStaleGroupCalls += 1;
        // Сервер перенёс группу целиком — перечитанная карточка отвечает уже
        // без `stale_groups` (мутируем ТУ ЖЕ фикстуру, что читает GET).
        contextFixture(STALE_CONTEXT_ID).stale_groups = [];
        return HttpResponse.json({
          results: [
            {
              position_item_id: STALE_POSITION_ITEM_ID,
              outcome: "moved" as const,
              target_context_id: 9999,
              error_code: null,
              message: null,
            },
          ],
          moved: 1,
          refused: 0,
        });
      })
    );
    renderWithProviders(<ContextCard contextId={STALE_CONTEXT_ID} />);
    await user.click(await screen.findByRole("button", { name: /Перенести их/ }));

    // Строка внимания устаревшей группы пропадает у перечитанной карточки...
    await waitFor(() =>
      expect(screen.queryByText(/позици[яий] из раздела/)).not.toBeInTheDocument()
    );
    // ...а результат переноса остаётся виден оператору — он не был привязан
    // к строке, которой больше нет.
    expect(screen.getByText("перенесено 1 из 1")).toBeInTheDocument();
  });

  it("«Скрыть» убирает результат переноса с экрана", async () => {
    const user = userEvent.setup();
    handlerState.staleGroupTransferOverride = {
      results: [
        {
          position_item_id: STALE_POSITION_ITEM_ID,
          outcome: "moved",
          target_context_id: 9999,
          error_code: null,
          message: null,
        },
      ],
      moved: 1,
      refused: 0,
    };
    renderWithProviders(<ContextCard contextId={STALE_CONTEXT_ID} />);
    await user.click(await screen.findByRole("button", { name: /Перенести их/ }));
    expect(await screen.findByText("перенесено 1 из 1")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Скрыть" }));

    expect(screen.queryByText("перенесено 1 из 1")).not.toBeInTheDocument();
  });

  // MINOR-2 (ревью Fable 27.09.2026): обработчик MSW по умолчанию (БЕЗ
  // `staleGroupTransferOverride`) обязан исключать конфликтные STALE-членства
  // из пакета — тот же фильтр, что берёт бэкенд
  // (`transfer_stale_group`: `membership_state=STALE AND conflict_at IS
  // NULL`, MAJOR-1). Добавляем В ТУ ЖЕ группу ещё одно членство, устаревшее
  // И конфликтное разом — если бы обработчик его тоже перенёс, итог был бы
  // «перенесено 2 из 2», а не «1 из 1».
  it("«Перенести их» не берёт устаревшее И конфликтное членство той же группы", async () => {
    const user = userEvent.setup();
    const stale = contextFixture(STALE_CONTEXT_ID);
    stale.members = [
      ...stale.members,
      {
        position_item_id: 9301,
        job_title: "Устройство покрытий полов, ось 3",
        estimate_id: 5001,
        membership_state: "STALE",
        conflict_at: "2026-09-24T10:00:00Z",
        conflict_from_context_id: 601,
        routed_by: "manual",
        chapterItemId: STALE_CHAPTER_ITEM_ID,
      },
    ];
    renderWithProviders(<ContextCard contextId={STALE_CONTEXT_ID} />);
    await user.click(await screen.findByRole("button", { name: /Перенести их/ }));
    expect(await screen.findByText("перенесено 1 из 1")).toBeInTheDocument();
  });

  it("свёрнутая группа своих позиций не запрашивает; раскрытие запрашивает ровно свою группу", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={MIXED_GROUPS_CONTEXT_ID} />);
    await waitFor(() => expect(screen.getByText("Устройство стяжки пола")).toBeInTheDocument());
    await openMembershipTab(user);
    expect(screen.getAllByRole("button", { name: /Раскрыть группу/ })).toHaveLength(2);
    expect(handlerState.groupMembersRequests).toEqual([]);

    await expandGroup(user, /Раскрыть группу «без раздела»/);
    await screen.findByText("Стяжка пола, вне структуры");
    expect(handlerState.groupMembersRequests).toHaveLength(1);
    const params = new URLSearchParams(handlerState.groupMembersRequests[0]);
    expect(params.get("no_chapter")).toBe("true");
    expect(params.get("chapter_item_id")).toBeNull();
  });

  it("архивный сосед по корзине не предлагается целью переноса выбранных и слияния", async () => {
    const user = userEvent.setup();
    const ordinary = contextFixture(ORDINARY_CONTEXT_ID);
    ordinary.bucket_contexts = [
      ...ordinary.bucket_contexts,
      { id: 751, is_default: false, archived_at: "2026-09-20T10:00:00Z", member_count: 0 },
    ];
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Штукатурка стен цементно-песчаным раствором")).toBeInTheDocument()
    );
    await openMembershipTab(user);
    for (const name of ["Целевой контекст для переноса выбранных", "Слить контекст в целевой"]) {
      await user.click(screen.getByRole("combobox", { name }));
      expect(await screen.findByRole("option", { name: "контекст #750" })).toBeInTheDocument();
      expect(screen.queryByRole("option", { name: "контекст #751" })).not.toBeInTheDocument();
      await user.keyboard("{Escape}");
    }
  });

  it.each([601, 602, 603, 604, 605, 606, 607, 608, 609, 610, 611, 612])(
    "контекст %i: ни на одной вкладке карточки и в выборе правила разделения нет кодов полей и событий",
    async (contextId) => {
      const user = userEvent.setup();
      renderWithProviders(<ContextCard contextId={contextId} />);
      await waitFor(() => expect(screen.getByRole("tab", { name: "Решения" })).toBeInTheDocument());
      expect(screenText()).not.toMatch(SCREEN_CODE_RE);

      await openMembershipTab(user);
      for (const trigger of screen.queryAllByRole("button", { name: /Раскрыть группу/ })) {
        await user.click(trigger);
      }
      const groupCount = screen.queryAllByRole("button", { name: /Раскрыть группу/ }).length;
      if (groupCount === 0) {
        expect(screen.getByText("Членств нет.")).toBeInTheDocument();
      } else {
        await waitFor(() => expect(screen.getAllByRole("table")).toHaveLength(groupCount));
      }
      await user.click(screen.getByRole("combobox", { name: "Разделить с правилом или без" }));
      await user.click(await screen.findByText("С правилом"));
      await user.click(screen.getByRole("combobox", { name: "Вид правила" }));
      await screen.findByRole("option", { name: "раздел на уровне равен" });
      expect(screenText()).not.toMatch(SCREEN_CODE_RE);
      await user.keyboard("{Escape}");

      await user.click(screen.getByRole("tab", { name: "Журнал" }));
      expect(screenText()).not.toMatch(SCREEN_CODE_RE);
    }
  );

  // -------------------------------------------------------------------------
  //  Слово на экране — код в запросе; видимые подписи (спека §2.2).
  // -------------------------------------------------------------------------

  it("переопределение роли: пункт выбран словом, на сервер уходит КОД роли", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={ORDINARY_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Штукатурка стен цементно-песчаным раствором")).toBeInTheDocument()
    );
    await user.click(screen.getByRole("button", { name: "Изменить «что называет»…" }));
    const roleDialog = await screen.findByRole("dialog");
    await user.click(within(roleDialog).getByRole("combobox", { name: "Наименование называет" }));
    await user.click(await screen.findByRole("option", { name: "место" }));
    await user.click(within(roleDialog).getByRole("button", { name: "Переопределить роль" }));
    await waitFor(() =>
      expect(handlerState.lastSetNameRoleRequest).toEqual({
        contextId: ORDINARY_CONTEXT_ID,
        body: { role: "LOCATION_ONLY" },
      })
    );
  });

  it("строка внимания группы без раздела ВИДИМО подписана «без раздела», без плашки «в смете»", async () => {
    const stale = contextFixture(STALE_CONTEXT_ID);
    stale.stale_groups = [
      ...stale.stale_groups,
      {
        chapter_item_ids: [],
        path: [],
        count: 2,
        target_category_id: 89,
        target_category_code: "07.02",
        target_category_title: "Слаботочные системы",
      },
    ];
    renderWithProviders(<ContextCard contextId={STALE_CONTEXT_ID} />);
    await waitFor(() => expect(screen.getAllByText(/позици[яий] из раздела/)).toHaveLength(2));
    // Подпись видна глазом, а не только в aria-label кнопки.
    const nullStaleLine = screen.getAllByText(/позици[яий] из раздела/)[1];
    expect(within(nullStaleLine).getByText("без раздела")).toBeInTheDocument();
    expect(within(nullStaleLine).queryByText("в смете")).not.toBeInTheDocument();
  });

  it("строка группы без раздела во вкладке «Членства» ВИДИМО подписана «без раздела», без плашки «в смете»", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={MIXED_GROUPS_CONTEXT_ID} />);
    await waitFor(() => expect(screen.getByText("Устройство стяжки пола")).toBeInTheDocument());
    await openMembershipTab(user);
    // Подпись видна глазом, а не только в aria-label триггера.
    const nullTrigger = screen.getByRole("button", { name: "Раскрыть группу «без раздела»" });
    expect(within(nullTrigger).getByText("без раздела")).toBeInTheDocument();
    expect(within(nullTrigger).queryByText("в смете")).not.toBeInTheDocument();
  });

  it("раскрытие группы С разделом грузит ТОЛЬКО её позиции — позиция без раздела в неё не попадает", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={MIXED_GROUPS_CONTEXT_ID} />);
    await waitFor(() => expect(screen.getByText("Устройство стяжки пола")).toBeInTheDocument());
    await openMembershipTab(user);
    await expandGroup(user, /Раскрыть группу «8 Отделочные работы/);
    expect(await screen.findByText("Стяжка пола, ось А-Б")).toBeInTheDocument();
    expect(screen.getByText("Стяжка пола, ось Б-В")).toBeInTheDocument();
    expect(screen.queryByText("Стяжка пола, вне структуры")).not.toBeInTheDocument();
    const params = new URLSearchParams(handlerState.groupMembersRequests.at(-1));
    expect(params.get("chapter_item_id")).toBe(String(MIXED_GROUPS_CHAPTER_ITEM_ID));
    expect(params.get("no_chapter")).toBeNull();
  });

  // -------------------------------------------------------------------------
  //  Спека §2.5: неуспех `groupMemberIds` в обработчиках клика (не мутация
  //  react-query) обязан быть пойман и показан тостом — выбор при этом не
  //  меняется.
  //
  //  Сброс состояния карточки при смене контекста (выбор членств, кэш id
  //  группы, цели переноса/слияния, результат пакетного переноса, поле
  //  архивирования, состояние секций группы) проверяется на уровне
  //  `ContextsTab.test.tsx` — там смена контекста происходит так же, как в
  //  продакшене (щелчок по строке списка, `key` на `<ContextCard>` в
  //  `ContextsTab.tsx`), а не искусственным `rerender` того же элемента с
  //  новым `contextId`, которого продакшен-код больше не делает.
  // -------------------------------------------------------------------------

  it("галочка группы: неуспех groupMemberIds показывает тост причиной, выбор не меняется", async () => {
    server.use(
      http.get("/api/v1/semantic/contexts/:id/member-ids", () =>
        HttpResponse.json(
          { detail: "Не удалось получить список позиций группы." },
          { status: 500 }
        )
      )
    );
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={BIG_GROUP_CONTEXT_ID} />);
    await waitFor(() =>
      expect(screen.getByText("Устройство вентиляционных каналов")).toBeInTheDocument()
    );
    await openMembershipTab(user);
    await expandGroup(user, /9 Инженерные системы/);
    await screen.findByText("Вентканал, узел 1");
    // Выбор до щелчка НЕ пуст: иначе «не меняется» неотличимо от «сброшен в
    // пустой» при неуспехе.
    await user.click(screen.getByLabelText("Выбрать позицию 80001"));
    expect(screen.getByText("Выбрано членств: 1")).toBeInTheDocument();

    await user.click(screen.getByLabelText(/Выбрать группу/));

    expect(
      await screen.findByText(/Не удалось получить список позиций группы/)
    ).toBeInTheDocument();
    expect(screen.getByText("Выбрано членств: 1")).toBeInTheDocument();
    expect(screen.getByLabelText("Выбрать позицию 80001")).toBeChecked();
    expect(screen.getByLabelText(/Выбрать группу/)).not.toBeChecked();
  });

  it("«Принять решение цели» (все конфликтные): неуспех groupMemberIds показывает тост, мутация не отправляется", async () => {
    server.use(
      http.get("/api/v1/semantic/contexts/:id/member-ids", () =>
        HttpResponse.json(
          { detail: "Не удалось получить список конфликтных позиций." },
          { status: 500 }
        )
      )
    );
    const user = userEvent.setup();
    renderWithProviders(<ContextCard contextId={CONFLICT_CONTEXT_ID} />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Принять решение цели (все конфликтные)" })
      ).toBeInTheDocument()
    );

    await user.click(
      screen.getByRole("button", { name: "Принять решение цели (все конфликтные)" })
    );

    expect(
      await screen.findByText(/Не удалось получить список конфликтных позиций/)
    ).toBeInTheDocument();
    expect(handlerState.lastAcceptTargetDecisionRequest).toBeNull();
  });
});
