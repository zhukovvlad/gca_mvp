import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { handlerState } from "@/test/handlers";
import { renderWithProviders } from "@/test/utils";
import type { SuggestionGroup, SuggestionRow } from "@/types/domain";

import { SuggestionGroups } from "./SuggestionGroups";

/**
 * Группы очереди «Семья из списка» (спека semantic-suggestions §2.12): отметки
 * строк, «Подтвердить отмеченные N», «Другая семья…», «Отклонить», пометка
 * «ранее отклонено». Группы берутся из фикстуры `handlers.ts`: «Геотекстиль»
 * `high` (строки 1–3), «Геотекстиль» `mid` (строка 4), «Кровельные работы» `low`
 * (строки 5–6).
 */

const UNIT_SYMBOL: Record<string, string> = { M2: "м²", M3: "м³" };
const unitLabel = (code: string | null) => (code ? (UNIT_SYMBOL[code] ?? code) : "без единицы");

function fixtureGroups(): SuggestionGroup[] {
  return structuredClone(handlerState.suggestionGroups);
}

function renderGroups(groups: SuggestionGroup[] = fixtureGroups()) {
  return renderWithProviders(<SuggestionGroups groups={groups} unitLabel={unitLabel} />);
}

function bigGroup(rowCount: number): SuggestionGroup {
  const rows: SuggestionRow[] = Array.from({ length: rowCount }, (_, i) => ({
    suggestion_id: 100 + i,
    context_id: 4000 + i,
    title: `Строка ${i + 1}`,
    unit_code: "M2",
    article: null,
    path: [],
    confidence: "0.95",
    reason: `Причина ${i + 1}`,
    multi_owner: false,
    previously_rejected: null,
  }));
  return { family_id: 900, family_title: "Большая семья", unit_code: "M2", band: "high", rows, total: rowCount };
}

function firstGroup() {
  return screen.getAllByTestId("suggestion-group")[0];
}

describe("SuggestionGroups — группы", () => {
  it("группа — пара «семья + полоса»: у одной семьи две группы, у каждой своя полоса", () => {
    renderGroups();

    const groups = screen.getAllByTestId("suggestion-group");
    expect(groups).toHaveLength(3);
    expect(within(groups[0]).getByText("Геотекстиль")).toBeInTheDocument();
    expect(within(groups[0]).getByText("≥ 0,9")).toBeInTheDocument();
    expect(within(groups[1]).getByText("Геотекстиль")).toBeInTheDocument();
    expect(within(groups[1]).getByText("0,7–0,9")).toBeInTheDocument();
    expect(within(groups[2]).getByText("Кровельные работы")).toBeInTheDocument();
    expect(within(groups[2]).getByText("< 0,7")).toBeInTheDocument();
  });

  it("шапка группы: единица символом и число строк", () => {
    renderGroups();
    expect(within(firstGroup()).getByText("м² · 3")).toBeInTheDocument();
    expect(within(screen.getAllByTestId("suggestion-group")[2]).getByText("м³ · 2")).toBeInTheDocument();
  });

  it("раскрыта только первая группа; остальные раскрываются шевроном", async () => {
    const user = userEvent.setup();
    renderGroups();

    const groups = screen.getAllByTestId("suggestion-group");
    expect(within(groups[0]).getAllByTestId("suggestion-row")).toHaveLength(3);
    expect(within(groups[1]).queryAllByTestId("suggestion-row")).toHaveLength(0);

    await user.click(within(groups[1]).getByRole("button", { name: "Раскрыть группу" }));
    expect(within(groups[1]).getAllByTestId("suggestion-row")).toHaveLength(1);
  });
});

describe("SuggestionGroups — строка", () => {
  it("у каждой строки «Пояснение ИИ: …», «Другая семья…» и «Отклонить»", () => {
    renderGroups();

    const rows = within(firstGroup()).getAllByTestId("suggestion-row");
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getByText("Пояснение ИИ: Геотекстиль с плотностью в м² — ровно семья «Геотекстиль».")).toBeInTheDocument();
    for (const row of rows) {
      expect(within(row).getByRole("button", { name: "Другая семья…" })).toBeInTheDocument();
      expect(within(row).getByRole("button", { name: "Отклонить" })).toBeInTheDocument();
    }
  });

  it("уверенность — строка сервера с запятой, без Number", () => {
    renderGroups();
    const rows = within(firstGroup()).getAllByTestId("suggestion-row");
    expect(within(rows[0]).getByText("0,98")).toBeInTheDocument();
    expect(within(rows[2]).getByText("0,91")).toBeInTheDocument();
  });

  it("пометка «ранее отклонено» — только у строки с previously_rejected", () => {
    renderGroups();

    const rows = within(firstGroup()).getAllByTestId("suggestion-row");
    expect(within(rows[1]).getByText(/ранее отклонено admin: Геотекстиль, 20\.09\.2026/)).toBeInTheDocument();
    expect(within(rows[0]).queryByText(/ранее отклонено/)).not.toBeInTheDocument();
    expect(within(rows[2]).queryByText(/ранее отклонено/)).not.toBeInTheDocument();
  });

  it("путь и статья строки подписаны источниками", () => {
    renderGroups();
    const row = within(firstGroup()).getAllByTestId("suggestion-row")[0];
    expect(within(row).getByText("статья СМР")).toBeInTheDocument();
    expect(within(row).getByText("в смете")).toBeInTheDocument();
    expect(within(row).getByText("Благоустройство / Земляные работы / Мульчирование")).toBeInTheDocument();
  });

  it("«Отклонить» шлёт отклонение именно этой строки", async () => {
    const user = userEvent.setup();
    renderGroups();

    const rows = within(firstGroup()).getAllByTestId("suggestion-row");
    await user.click(within(rows[1]).getByRole("button", { name: "Отклонить" }));

    await waitFor(() => expect(handlerState.rejectSuggestionRequests).toEqual([2]));
  });
});

describe("SuggestionGroups — отметки и «Подтвердить отмеченные N»", () => {
  it("по умолчанию все строки отмечены и число в кнопке равно числу строк", () => {
    renderGroups();

    const checkboxes = within(firstGroup()).getAllByRole("checkbox", { name: "Отметить строку" });
    expect(checkboxes).toHaveLength(3);
    for (const box of checkboxes) expect(box).toBeChecked();
    expect(within(firstGroup()).getByRole("button", { name: "Подтвердить отмеченные 3" })).toBeInTheDocument();
  });

  it("снятие отметки уменьшает число, повторная отметка возвращает", async () => {
    const user = userEvent.setup();
    renderGroups();

    const [first] = within(firstGroup()).getAllByRole("checkbox", { name: "Отметить строку" });
    await user.click(first);
    expect(within(firstGroup()).getByRole("button", { name: "Подтвердить отмеченные 2" })).toBeInTheDocument();
    await user.click(first);
    expect(within(firstGroup()).getByRole("button", { name: "Подтвердить отмеченные 3" })).toBeInTheDocument();
  });

  it("отметка одной группы не затрагивает другую группу той же семьи", async () => {
    const user = userEvent.setup();
    renderGroups();

    await user.click(within(screen.getAllByTestId("suggestion-group")[1]).getByRole("button", { name: "Раскрыть группу" }));
    await user.click(within(firstGroup()).getAllByRole("checkbox", { name: "Отметить строку" })[0]);

    expect(within(screen.getAllByTestId("suggestion-group")[1]).getByRole("button", { name: "Подтвердить отмеченные 1" })).toBeInTheDocument();
  });

  it("подтверждение шлёт ровно отмеченные id", async () => {
    const user = userEvent.setup();
    renderGroups();

    await user.click(within(firstGroup()).getAllByRole("checkbox", { name: "Отметить строку" })[1]);
    await user.click(within(firstGroup()).getByRole("button", { name: "Подтвердить отмеченные 2" }));

    await waitFor(() => expect(handlerState.confirmSuggestionsRequests).toEqual([[1, 3]]));
  });

  it("со всех строк снята отметка — подтверждать нечего, кнопка недоступна", async () => {
    const user = userEvent.setup();
    renderGroups();

    for (const box of within(firstGroup()).getAllByRole("checkbox", { name: "Отметить строку" })) {
      await user.click(box);
    }
    expect(within(firstGroup()).getByRole("button", { name: "Подтвердить отмеченные 0" })).toBeDisabled();
  });

  it("пропущенные сервером показаны сообщением с числом", async () => {
    const user = userEvent.setup();
    handlerState.confirmSkippedIds = [2, 3];
    renderGroups();

    await user.click(within(firstGroup()).getByRole("button", { name: "Подтвердить отмеченные 3" }));

    expect(await screen.findByText(/Пропущено предложений: 2/)).toBeInTheDocument();
    expect(screen.getByText(/1 контекст получил семью «Геотекстиль»\./)).toBeInTheDocument();
  });

  it("снятые отметки остаются в группе, тост говорит об этом числом", async () => {
    const user = userEvent.setup();
    renderGroups();

    await user.click(within(firstGroup()).getAllByRole("checkbox", { name: "Отметить строку" })[0]);
    await user.click(within(firstGroup()).getByRole("button", { name: "Подтвердить отмеченные 2" }));

    // Второе предложение — отдельным элементом описания тоста, а не хвостом строки.
    const note = await screen.findByText(/Снятые \(1\) остались в очереди/);
    expect(note).toHaveAttribute("data-description");
    expect(screen.getByText(/2 контекста получили семью «Геотекстиль»\./)).toBeInTheDocument();
  });

  it.each([
    [1, "1 контекст получил семью"],
    [2, "2 контекста получили семью"],
    [5, "5 контекстов получили семью"],
  ])("согласование числа в тосте: %i", async (count, text) => {
    const user = userEvent.setup();
    const [group] = fixtureGroups();
    group.rows = Array.from({ length: count }, (_, i) => ({ ...group.rows[0], suggestion_id: 700 + i, context_id: 7000 + i }));
    group.total = count;
    renderGroups([group]);

    await user.click(screen.getByRole("button", { name: `Подтвердить отмеченные ${count}` }));

    expect(await screen.findByText(new RegExp(`^${text} «Геотекстиль»\\.$`))).toBeInTheDocument();
  });

  it("без пропущенных сообщения о пропуске нет", async () => {
    const user = userEvent.setup();
    renderGroups();

    await user.click(within(firstGroup()).getByRole("button", { name: "Подтвердить отмеченные 3" }));

    expect(await screen.findByText(/3 контекста получили семью «Геотекстиль»\./)).toBeInTheDocument();
    expect(screen.queryByText(/Пропущено предложений/)).not.toBeInTheDocument();
    // Все строки отмечены — снятых нет, и о них тост не говорит.
    expect(screen.queryByText(/Снятые/)).not.toBeInTheDocument();
  });
});

describe("SuggestionGroups — длинная группа", () => {
  it("печатает первые 20 строк, остальные — «… и ещё K, отмечены»; подтверждение шлёт все отмеченные", async () => {
    const user = userEvent.setup();
    renderGroups([bigGroup(25)]);

    expect(screen.getAllByTestId("suggestion-row")).toHaveLength(20);
    expect(screen.getByText("… и ещё 5, отмечены")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Подтвердить отмеченные 25" }));

    await waitFor(() => expect(handlerState.confirmSuggestionsRequests).toHaveLength(1));
    expect(handlerState.confirmSuggestionsRequests[0]).toHaveLength(25);
    expect(handlerState.confirmSuggestionsRequests[0]).toContain(124);
  });

  it("«Показать все» раскрывает остаток, и его строки можно отметить поштучно", async () => {
    const user = userEvent.setup();
    renderGroups([bigGroup(22)]);

    await user.click(screen.getByRole("button", { name: "Показать все" }));
    expect(screen.getAllByTestId("suggestion-row")).toHaveLength(22);
    expect(screen.queryByText(/и ещё/)).not.toBeInTheDocument();

    await user.click(screen.getAllByRole("checkbox", { name: "Отметить строку" })[21]);
    expect(screen.getByRole("button", { name: "Подтвердить отмеченные 21" })).toBeInTheDocument();
  });

  it("ровно 20 строк — никакого «… и ещё»", () => {
    renderGroups([bigGroup(20)]);
    expect(screen.getAllByTestId("suggestion-row")).toHaveLength(20);
    expect(screen.queryByText(/и ещё/)).not.toBeInTheDocument();
  });
});

describe("SuggestionGroups — «Другая семья…»", () => {
  function activeFamily(id: number, title: string, unitCode: string) {
    const base = handlerState.workFamilies.find((f) => f.id === 43)!;
    handlerState.workFamilies.push({
      ...base,
      id,
      title,
      unit_code: unitCode,
      unit_symbol: UNIT_SYMBOL[unitCode] ?? unitCode,
    });
  }

  it("предлагает только активные семьи той же единицы, кроме предложенной", async () => {
    const user = userEvent.setup();
    activeFamily(900, "Устройство покрытий", "M2");
    activeFamily(501, "Геотекстиль", "M2");
    activeFamily(902, "Кубатурная семья", "M3");
    renderGroups();

    const row = within(firstGroup()).getAllByTestId("suggestion-row")[0];
    await user.click(within(row).getByRole("button", { name: "Другая семья…" }));
    await user.click(await screen.findByRole("combobox", { name: /Активные семьи м²/ }));

    expect(await screen.findByRole("option", { name: "Устройство покрытий" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Кубатурная семья" })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Геотекстиль" })).not.toBeInTheDocument();
  });

  it("назначение шлёт id строки и выбранную семью, диалог закрывается", async () => {
    const user = userEvent.setup();
    activeFamily(900, "Устройство покрытий", "M2");
    renderGroups();

    const row = within(firstGroup()).getAllByTestId("suggestion-row")[1];
    await user.click(within(row).getByRole("button", { name: "Другая семья…" }));
    await user.click(await screen.findByRole("combobox", { name: /Активные семьи м²/ }));
    await user.click(await screen.findByRole("option", { name: "Устройство покрытий" }));
    await user.click(screen.getByRole("button", { name: "Назначить" }));

    await waitFor(() => expect(handlerState.otherFamilyRequests).toEqual([{ suggestionId: 2, familyId: 900 }]));
    expect(await screen.findByText("Назначена семья «Устройство покрытий».")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("«Назначить» недоступна, пока семья не выбрана", async () => {
    const user = userEvent.setup();
    activeFamily(900, "Устройство покрытий", "M2");
    renderGroups();

    await user.click(within(within(firstGroup()).getAllByTestId("suggestion-row")[0]).getByRole("button", { name: "Другая семья…" }));

    expect(await screen.findByRole("button", { name: "Назначить" })).toBeDisabled();
  });

  it("других активных семей единицы нет — сообщение и нет кнопки назначения", async () => {
    const user = userEvent.setup();
    renderGroups();

    await user.click(within(within(firstGroup()).getAllByTestId("suggestion-row")[0]).getByRole("button", { name: "Другая семья…" }));

    expect(await screen.findByText("Других активных семей м² нет.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Назначить" })).not.toBeInTheDocument();
  });
});
