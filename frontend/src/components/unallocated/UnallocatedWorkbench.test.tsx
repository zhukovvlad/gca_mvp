import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  UnallocatedWorkbench,
  type UnallocatedWorkbenchProps,
  type WorkbenchCopy,
  type WorkbenchManualRow,
  type WorkbenchSection,
} from "./UnallocatedWorkbench";
import { renderWithProviders } from "@/test/utils";
import type { ProjectPassportCategoryOption } from "@/types/domain";

/**
 * Контракт презентационного ядра `UnallocatedWorkbench` (план этапного
 * разноса, задача 10) — пин веток, которые панель паспорта (`UnallocatedPanel`)
 * не упражняет вовсе, потому что всегда передаёт `compareSiblings`,
 * `noteField: false` и не пользуется ни `renderMark`, ни `children`. Именно
 * эти ветки понадобятся тендерному `UnallocatedSheet` (задача 11: файловый
 * порядок = без `compareSiblings`, пометки partial/conflict = `renderMark`,
 * блок диагностики = `children`).
 *
 * НЕ вторая копия сюиты паспорта: testid-контракт дерева и `CategoryPicker`
 * пином не дублируются — они уже закрыты `UnallocatedPanel.test.tsx` и
 * `CategoryPicker.test.tsx` соответственно.
 */

const OPTIONS: ProjectPassportCategoryOption[] = [
  { id: 1, code: "01", title: "Опция", is_bucket: false },
];

const COPY: WorkbenchCopy = {
  sectionsHeading: "Заголовок разделов",
  sectionsHint: "Подсказка разделов",
  sectionsEmpty: "Разделов нет",
  manualHeading: "Заголовок ручных решений",
  manualHint: "Подсказка ручных решений",
  manualEmpty: "Ручных решений нет",
};

function section(key: string, parentKey: string | null = null): WorkbenchSection {
  return { key, parentKey, number: null, title: `Раздел ${key}`, smr_article_raw: null };
}

function manualRow(key: string): WorkbenchManualRow {
  return {
    key,
    number: null,
    title: `Ручное ${key}`,
    category_code: "01",
    category_title: "Опция",
    assigned_by_email: "a@example.com",
    assigned_at: "2026-01-01T00:00:00Z",
    note: null,
  };
}

function renderWorkbench(
  overrides: Partial<UnallocatedWorkbenchProps<WorkbenchSection, WorkbenchManualRow>> = {}
) {
  const onPick = vi.fn();
  const onClear = vi.fn();
  const props: UnallocatedWorkbenchProps<WorkbenchSection, WorkbenchManualRow> = {
    sections: [],
    manual: [],
    categoryOptions: OPTIONS,
    copy: COPY,
    testId: (s) => s.key,
    renderAside: () => null,
    renderManualAside: () => null,
    noteField: false,
    onPick,
    onClear,
    disabled: false,
    ...overrides,
  };
  renderWithProviders(<UnallocatedWorkbench {...props} />);
  return { onPick, onClear };
}

/** Обёртки разделов — регекс исключает `-row-`/`-title-` (содержат дефис
 *  после базового имени), но ключи фикстур обязаны быть без дефисов, иначе
 *  сами попадут под исключение. */
function sectionWrapperTestIds(): (string | null)[] {
  return screen
    .getAllByTestId(/^unallocated-section-[A-Za-z0-9]+$/)
    .map((el) => el.getAttribute("data-testid"));
}

describe("UnallocatedWorkbench: сортировка сиблингов", () => {
  it("compareSiblings применяется РЕКУРСИВНО — не только к корням, но и к их детям", () => {
    /*
      Ревью задачи 10: удаление рекурсии в `sortRec` (сортировать только
      массив `roots`, не заходя в `node.children`) оставляло сюиту паспорта
      зелёной целиком — там либо один уровень вложенности с уже упорядоченным
      входом, либо сортировка по `subtree_amount`, где вход фикстуры совпадает
      с ожидаемым порядком. Здесь у ОДНОГО родителя два ребёнка, порядок
      прихода которых (childB раньше childA) ПРОТИВОПОЛОЖЕН компаратору (по
      возрастанию key) — без рекурсии в дочернем массиве останется входной
      порядок, с ней — переставленный.
    */
    const sections = [section("root"), section("childB", "root"), section("childA", "root")];
    renderWorkbench({
      sections,
      compareSiblings: (a, b) => a.key.localeCompare(b.key),
    });

    expect(sectionWrapperTestIds()).toEqual([
      "unallocated-section-root",
      "unallocated-section-childA",
      "unallocated-section-childB",
    ]);
  });

  it("без compareSiblings — порядок прихода (тендер: файловый порядок ведомости)", () => {
    const sections = [section("z"), section("a"), section("m")];
    renderWorkbench({ sections });

    expect(sectionWrapperTestIds()).toEqual([
      "unallocated-section-z",
      "unallocated-section-a",
      "unallocated-section-m",
    ]);
  });
});

describe("UnallocatedWorkbench: слоты renderMark и children", () => {
  it("renderMark рендерится внутри строки СВОЕГО раздела", () => {
    const sections = [section("s1"), section("s2")];
    renderWorkbench({
      sections,
      renderMark: (s) => <span data-testid={`mark-${s.key}`}>пометка {s.key}</span>,
    });

    const row1 = screen.getByTestId("unallocated-section-s1");
    const row2 = screen.getByTestId("unallocated-section-s2");
    expect(within(row1).getByTestId("mark-s1")).toHaveTextContent("пометка s1");
    expect(within(row1).queryByTestId("mark-s2")).not.toBeInTheDocument();
    expect(within(row2).getByTestId("mark-s2")).toHaveTextContent("пометка s2");
  });

  it("children рендерится ПОСЛЕ блока «Разнесено вручную» (диагностика тендера)", () => {
    renderWorkbench({
      manual: [manualRow("m1")],
      children: <div data-testid="diagnostics-block">блок диагностики</div>,
    });

    const manualHeading = screen.getByText(COPY.manualHeading);
    const diagnostics = screen.getByTestId("diagnostics-block");
    // DOCUMENT_POSITION_FOLLOWING на результате compareDocumentPosition —
    // diagnostics идёт ПОСЛЕ manualHeading в порядке документа.
    expect(
      manualHeading.compareDocumentPosition(diagnostics) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
  });
});

describe("UnallocatedWorkbench: noteField.existingNotes получает СВОЙ раздел", () => {
  it("два раздела с разными существующими заметками не путаются местами", async () => {
    /*
      `existingNotes` вызывается на КАЖДУЮ строку (`SectionRow` замыкает
      `section` — узел ИМЕННО этой строки). Функция, игнорирующая аргумент и
      всегда возвращающая заметки одного раздела (например, первого),
      удовлетворила бы остальные тесты файла — только прямая проверка на
      ДВУХ разделах с РАЗНЫМИ заметками ловит подмену аргумента.
    */
    const user = userEvent.setup();
    const notesBySection: Record<string, (string | null)[]> = {
      s1: ["заметка первого раздела"],
      s2: ["заметка второго раздела"],
    };
    renderWorkbench({
      sections: [section("s1"), section("s2")],
      noteField: { existingNotes: (s) => notesBySection[s.key] },
    });

    await user.click(screen.getByTestId("pick-category-s1"));
    expect(screen.getByLabelText("Заметка")).toHaveValue("заметка первого раздела");

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByLabelText("Заметка")).not.toBeInTheDocument());

    await user.click(screen.getByTestId("pick-category-s2"));
    expect(screen.getByLabelText("Заметка")).toHaveValue("заметка второго раздела");
  });
});

describe("UnallocatedWorkbench: тексты copy доходят до DOM", () => {
  it("заголовки, подсказки и пустые состояния — из copy, а не зашиты в разметку ядра", () => {
    renderWorkbench({ sections: [], manual: [] });

    expect(screen.getByText(COPY.sectionsHeading)).toBeInTheDocument();
    expect(screen.getByText(COPY.sectionsHint)).toBeInTheDocument();
    expect(screen.getByText(COPY.sectionsEmpty)).toBeInTheDocument();
    expect(screen.getByText(COPY.manualHeading)).toBeInTheDocument();
    expect(screen.getByText(COPY.manualHint)).toBeInTheDocument();
    expect(screen.getByText(COPY.manualEmpty)).toBeInTheDocument();
  });
});
