import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { sampleStageSummary } from "@/test/fixtures";
import { formatDecimalMoney, roundDecimalPercent } from "@/lib/format";
import type { StageSummaryCell, StageSummaryChange } from "@/types/domain";
import { KIND_LABEL, REASON_LABEL } from "./cellCopy";
import { StageSummaryTable } from "./StageSummaryTable";
import { ChangeBadge, SummaryCell } from "./SummaryCell";

/**
 * Таблица свода — состояния рисуются ПО ДАННЫМ, а не выводятся клиентом из
 * других полей (спека 2026-08-27-stage-summary-design.md §2.5–§2.7, §2.9,
 * §2.13; Global 13). Числа и порядок строк — из `sampleStageSummary`
 * (`frontend/src/test/fixtures.ts`), а не из брифа задачи: фикстура прошла
 * инвариантный набор `fixtures.test.ts` и она источник истины.
 */
describe("Таблица свода — состояния по данным (спека §2.5–§2.7, §2.9, §2.13)", () => {
  it("порядок строк — как пришёл с сервера; «Нераспределённое» и «Итого» в tfoot", () => {
    render(<StageSummaryTable summary={sampleStageSummary} />);
    // sampleStageSummary.rows[0] — «Котлован» (|contribution| = 60), rows[1] —
    // «Фасадные работы» (|contribution| = 30): убывание модуля вклада уже
    // проверено инвариантом фикстуры, здесь — что таблица не пересортировывает.
    const bodyRows = within(screen.getAllByRole("rowgroup")[1]).getAllByRole("row");
    expect(bodyRows[0]).toHaveTextContent("Котлован");
    expect(bodyRows[1]).toHaveTextContent("Фасадные работы");
    const foot = within(screen.getAllByRole("rowgroup")[2]).getAllByRole("row");
    expect(foot[0]).toHaveTextContent("Нераспределённое");
    expect(foot[1]).toHaveTextContent("Итого по предложению");
  });

  it.each([
    ["removed", "снято"],
    ["not_evaluated", "не оценивалась"],
    ["absent", "—"],
  ])("state=%s рисует «%s» — по полю, а не по сумме", (state, label) => {
    // amount=999.00 намеренно противоречит state: если бы рендер выводил
    // состояние из суммы, тест поймал бы это здесь.
    const cell = { ...sampleStageSummary.rows[0].cells[0], state, amount: "999.00" } as StageSummaryCell;
    render(
      <table>
        <tbody>
          <tr>
            <SummaryCell cell={cell} />
          </tr>
        </tbody>
      </table>
    );
    expect(screen.getByRole("cell")).toHaveTextContent(label);
    expect(screen.getByRole("cell")).not.toHaveTextContent("999");
  });

  /**
   * Fix round 3, п.2: макет рисует «отсутствует» плоским прочерком, а
   * «снято»/«не оценивалась» — пилюлями (то и другое остаётся пилюлями); эта
   * ветка проверяет ФАКТ РАЗМЕТКИ — какой элемент отрисован, что наблюдаемо в
   * прогоне — а не саму визуальную приглушённость прочерка: та проверяется
   * замером в браузере, не здесь (`docs/insights/unobservable-in-the-runner.md`).
   */
  it("state=absent рисует плоский прочерк (не пилюлю); removed/not_evaluated остаются пилюлями", () => {
    const dashCell = { ...sampleStageSummary.rows[0].cells[0], state: "absent", amount: null } as StageSummaryCell;
    const { unmount: unmountDash } = render(
      <table>
        <tbody>
          <tr>
            <SummaryCell cell={dashCell} />
          </tr>
        </tbody>
      </table>
    );
    const dash = screen.getByTestId("cell-dash");
    expect(dash).toHaveTextContent("—");
    // Пилюля (`StatusPill`) несёт `rounded-full`/`border` безусловно — у
    // плоского прочерка их нет: разные элементы, а не один перекрашенный.
    expect(dash).not.toHaveClass("rounded-full");
    unmountDash();

    for (const state of ["removed", "not_evaluated"] as const) {
      const pillCell = { ...sampleStageSummary.rows[0].cells[0], state, amount: null } as StageSummaryCell;
      const { unmount } = render(
        <table>
          <tbody>
            <tr>
              <SummaryCell cell={pillCell} />
            </tr>
          </tbody>
        </table>
      );
      expect(screen.queryByTestId("cell-dash")).not.toBeInTheDocument();
      expect(screen.getByRole("cell").querySelector(".rounded-full")).not.toBeNull();
      unmount();
    }
  });

  /**
   * Дефект внешнего ревью PR: `amount_unavailable_reason` тестировался ДО
   * `state`, и в колонке с неизвестной базой НДС «снято»/«не оценивалась»/
   * «отсутствует» подменялись подписью «нет базы НДС» — состояние ячейки
   * пропадало. Спека §2.5, §2.8 и AGENTS.md §10 сходятся: неизвестная база
   * гасит ТОЛЬКО показанную сумму, состояние ячейки не зависит от ставки НДС
   * и рисуется как обычно. У `removed`/`not_evaluated`/`absent` числа и так не
   * было — подписи «нет базы НДС» рядом с их пилюлей/прочерком макет не
   * показывает ни для одного состояния, поэтому она не выводится вовсе (у
   * `<td>` остаётся `title` с причиной — тот же механизм, что уже был).
   */
  it.each([
    ["removed", "снято"],
    ["not_evaluated", "не оценивалась"],
    ["absent", "—"],
  ])(
    "amount_unavailable_reason не гасит state=%s — остаётся «%s», а не «нет базы НДС»",
    (state, label) => {
      const cell = {
        ...sampleStageSummary.rows[0].cells[0],
        state,
        amount: null,
        amount_unavailable_reason: "unknown_vat_base",
      } as StageSummaryCell;
      render(
        <table>
          <tbody>
            <tr>
              <SummaryCell cell={cell} />
            </tr>
          </tbody>
        </table>
      );
      expect(screen.getByRole("cell")).toHaveTextContent(label);
      expect(screen.getByRole("cell")).not.toHaveTextContent("нет базы НДС");
    }
  );

  it("amount_unavailable_reason гасит сумму ТОЛЬКО у state=amount — «нет базы НДС» вместо числа", () => {
    const cell = {
      ...sampleStageSummary.rows[0].cells[0],
      state: "amount",
      amount: "999.00",
      amount_unavailable_reason: "unknown_vat_base",
    } as StageSummaryCell;
    render(
      <table>
        <tbody>
          <tr>
            <SummaryCell cell={cell} />
          </tr>
        </tbody>
      </table>
    );
    expect(screen.getByRole("cell")).toHaveTextContent("нет базы НДС");
    expect(screen.getByRole("cell")).not.toHaveTextContent("999");
  });

  it.each([
    ["appeared", "появилась"],
    ["reappeared", "вернулась"],
    ["disappeared", "нет в файле"],
    ["removed", "снято"],
  ])("change.kind=%s подписан «%s»", (kind, label) => {
    const cell = {
      ...sampleStageSummary.rows[0].cells[1],
      state: "amount",
      amount: "5.00",
      change: { kind, value: null, direction: null, reason: null },
    } as StageSummaryCell;
    render(
      <table>
        <tbody>
          <tr>
            <SummaryCell cell={cell} />
          </tr>
        </tbody>
      </table>
    );
    expect(screen.getByRole("cell")).toHaveTextContent(label);
  });

  it.each([
    [{ kind: "percent", value: "-50.0", direction: "down", reason: null }, /-50,0%/],
    [{ kind: "abs_only", value: "-20.00", direction: "down", reason: null }, /20,00.*Δ, без %/],
    [{ kind: "disappeared", value: null, direction: null, reason: null }, /нет в файле/],
    [{ kind: "none", value: null, direction: null, reason: "unknown_vat_base" }, /^—$/],
  ])("ChangeBadge исчерпывающе рисует %o (тот же компонент в KPI «Последний к первому»)", (change, expected) => {
    render(<ChangeBadge change={change as StageSummaryChange} inKpi />);
    expect(screen.getByTestId("change")).toHaveTextContent(expected);
  });

  it("direction трёх состояний: up/down/flat — знак и тон, flat не окрашен как рост", () => {
    // text-accent-text/text-danger-text/text-fg-tertiary — токены, которые
    // РЕАЛЬНО существуют в теме (frontend/src/index.css, alias --color-*
    // из @theme inline). "text-accent-primary-text" из брифа — несуществующая
    // утилита: Tailwind не находит `--color-accent-primary-text` и не
        // генерирует CSS вовсе, поэтому дефект был бы невидим и типам, и линту,
      // и этому тесту, если бы имя осталось таким.
    for (const [direction, value, cls] of [
      ["up", "+5.0", "text-accent-text"],
      ["down", "-5.0", "text-danger-text"],
      ["flat", "0.0", "text-fg-tertiary"],
    ] as const) {
      const cell = {
        ...sampleStageSummary.rows[0].cells[1],
        change: { kind: "percent", value, direction, reason: null },
      } as StageSummaryCell;
      const { unmount } = render(
        <table>
          <tbody>
            <tr>
              <SummaryCell cell={cell} />
            </tr>
          </tbody>
        </table>
      );
      expect(screen.getByTestId("change")).toHaveClass(cls);
      unmount();
    }
  });

  it("неполнота: значок и подсказка «учтено X из N строк», отдельно неконечные", () => {
    const cell = {
      ...sampleStageSummary.rows[0].cells[0],
      rows: { row_count: 40, rows_with_amount: 37, rows_not_finite: 1 },
    };
    render(
      <table>
        <tbody>
          <tr>
            <SummaryCell cell={cell} />
          </tr>
        </tbody>
      </table>
    );
    expect(screen.getByLabelText(/учтено 37 из 40 строк.*неконечных значений: 1/)).toBeInTheDocument();
  });

  it("сходимость трёх состояний в «Итого» — свой текст И свой тон у каждого (fix round 2, п.4)", () => {
    const s = structuredClone(sampleStageSummary);
    s.columns[1].convergence = { ...s.columns[1].convergence, converged: false, delta: "-10.00" };
    s.columns[2].convergence = {
      categories_sum: "90.00",
      file_total: null,
      converged: null,
      delta: null,
      reason: "file_total_unavailable",
    };
    render(<StageSummaryTable summary={s} />);
    const total = screen.getByText("Итого по предложению").closest("tr") as HTMLElement;
    const convergenceSpans = within(total).getAllByTestId("convergence");
    expect(convergenceSpans).toHaveLength(3);

    expect(convergenceSpans[0]).toHaveTextContent("сходится");
    expect(convergenceSpans[0]).toHaveClass("text-fg-tertiary");

    // Δ печатается настоящим форматом formatDecimalMoney (дефис ASCII, а не
    // типографский минус) — иначе тест зафиксировал бы символ, которого
    // функция форматирования не производит. Расхождение — дефект данных
    // (спека), поэтому тревожный тон, а не тот же тихий, что у «сходится».
    expect(convergenceSpans[1]).toHaveTextContent("не сходится: Δ -10,00");
    expect(convergenceSpans[1]).toHaveClass("text-warning-text");

    // Невозможность сверки — отсутствие файлового итога, а не расхождение:
    // СВОЙ нейтральный тон, отличный от обоих предыдущих.
    expect(convergenceSpans[2]).toHaveTextContent("сверка невозможна: итог файла не единогласен");
    expect(convergenceSpans[2]).toHaveClass("text-neutral-text");

    // Три тона должны быть попарно различны — иначе «различимость» была бы
    // утверждением на глаз, а не проверенным фактом.
    const classes = convergenceSpans.map((el) => el.className);
    expect(new Set(classes).size).toBe(3);
  });

  it("раскрытие статьи показывает детей и помечается aria-expanded", async () => {
    const user = userEvent.setup();
    render(<StageSummaryTable summary={sampleStageSummary} />);
    const toggle = screen.getByRole("button", { name: /Раскрыть Фасадные работы/ });
    expect(screen.queryByText("Прочее (фасады)")).not.toBeInTheDocument();
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Прочее (фасады)")).toBeInTheDocument();
  });

  it("подпись разноса под шапкой колонки", () => {
    render(<StageSummaryTable summary={sampleStageSummary} />);
    expect(screen.getByText(/разнос: 1 решение · 26\.08\.2026/)).toBeInTheDocument();
    // Колонки 1 и 4 (индексы 0 и 2) без ручного разноса; колонка 2 (индекс 1) —
    // единственная с решением (sampleStageSummary.columns[1].manual_overrides).
    expect(screen.getAllByText(/без ручного разноса/)).toHaveLength(2);
  });

  it("«Нераспределённое»: бейдж и «без %» вместо процента — не читается как уступка", () => {
    render(<StageSummaryTable summary={sampleStageSummary} />);
    const unallocated = screen.getByText("Нераспределённое").closest("tr") as HTMLElement;
    expect(unallocated).toHaveTextContent("обязательная строка");
    expect(unallocated).toHaveTextContent("без %");
  });

  /**
   * Fix round 2, п.3: пилюля «обязательная строка» несёт на макете пояснение
   * («Разделы без статьи классификатора…») в `title` — общий `StatusPill` не
   * принимает и не прокидывает `title`, поэтому пояснение стоит на обёртке
   * вокруг пилюли (`aria-describedby` → `sr-only`-текст), тем же приёмом, что
   * недоступная плитка решётки тендера (`OfferGrid.tsx`). Проверяется, что
   * пояснение ДОСТИЖИМО программно (через id, а не только через наведение
   * курсора, которое в jsdom не наблюдаемо вовсе).
   */
  it("пилюля «обязательная строка» несёт пояснение, достижимое скринридером, не только курсором", () => {
    render(<StageSummaryTable summary={sampleStageSummary} />);
    const pill = screen.getByText("обязательная строка");
    const describedHost = pill.closest("[aria-describedby]") as HTMLElement;
    expect(describedHost).not.toBeNull();
    const describedId = describedHost.getAttribute("aria-describedby") as string;
    const explanation = document.getElementById(describedId);
    expect(explanation).not.toBeNull();
    expect(explanation).toHaveTextContent(/Разделы без статьи классификатора/);
  });

  /**
   * Fix round 2, п.2: макет печатает код статьи классификатора ПЕРЕД
   * названием на каждой строке (`table.pass`, `<span class="code">`); ответ
   * несёт `row.code`, но страница его не рисовала — расхождение с
   * согласованным макетом без письменного долга запрещено правилом проекта.
   * Проверяются коды на трёх уровнях: корень («Котлован»/«Фасадные работы»),
   * ребёнок («Прочее (фасады)») и «Нераспределённое» (код-прочерк на макете).
   */
  it("код статьи классификатора печатается перед названием, как на макете", async () => {
    const user = userEvent.setup();
    render(<StageSummaryTable summary={sampleStageSummary} />);

    const kotlovan = sampleStageSummary.rows[0];
    const facades = sampleStageSummary.rows[1];
    expect(kotlovan.code).not.toBeNull();
    expect(facades.code).not.toBeNull();

    const kotlovanRow = screen.getByText(kotlovan.title).closest("tr") as HTMLElement;
    expect(within(kotlovanRow).getByTestId("row-code")).toHaveTextContent(kotlovan.code as string);
    const facadesRow = screen.getByText(facades.title).closest("tr") as HTMLElement;
    expect(within(facadesRow).getByTestId("row-code")).toHaveTextContent(facades.code as string);

    await user.click(screen.getByRole("button", { name: new RegExp(`Раскрыть ${facades.title}`) }));
    const child = facades.children[0];
    expect(child.code).not.toBeNull();
    const childRow = screen.getByText(child.title).closest("tr") as HTMLElement;
    expect(within(childRow).getByTestId("row-code")).toHaveTextContent(child.code as string);

    // «Нераспределённое» не несёт кода классификатора (`code: null`), но
    // ячейка кода на его строке всё равно есть — как на макете («—»).
    expect(sampleStageSummary.unallocated.code).toBeNull();
    const unallocatedRow = screen.getByText("Нераспределённое").closest("tr") as HTMLElement;
    expect(within(unallocatedRow).getByTestId("row-code")).toHaveTextContent("—");
  });

  /**
   * Обычная строка: «Торг» и «Вклад» — две правые колонки, ради которых
   * существует страница. Управляется `sampleStageSummary.rows[1]` («Фасадные
   * работы»): её `bargain` — процент со знаком и направлением `down`, её
   * `contribution` — сумма с тем же направлением. Ожидания читаются из самой
   * ячейки фикстуры (`roundDecimalPercent`/`formatDecimalMoney` — те же
   * форматтеры, что используют `ChangeBadge`/`ContributionValue`), а не
   * переписаны литералом — тест не отстанет, если проценты в фикстуре
   * поменяются.
   */
  it("Торг и Вклад обычной строки — знак, число и тон по направлению (rows[1] «Фасадные работы»)", () => {
    render(<StageSummaryTable summary={sampleStageSummary} />);
    const row = sampleStageSummary.rows[1];
    expect(row.bargain.kind).toBe("percent");
    expect(row.bargain.direction).toBe("down");
    expect(row.contribution.direction).toBe("down");

    const tr = screen.getByText(row.title).closest("tr") as HTMLElement;
    const bargainCell = within(tr).getByTestId("bargain-cell");
    const contributionCell = within(tr).getByTestId("contribution-cell");

    const expectedBargainText = roundDecimalPercent(row.bargain.value)!.text;
    expect(bargainCell).toHaveTextContent(expectedBargainText);
    // down → тот же тон, что «направление трёх состояний» уже закрепил для
    // ChangeBadge в ячейках — здесь проверяется, что «Торг» вызывает тот же
    // компонент, а не рисует цвет своей веткой.
    expect(within(bargainCell).getByTestId("change")).toHaveClass("text-danger-text");

    // toHaveTextContent normalizes the ELEMENT's text (NBSP → obычный пробел),
    // но не нормализует переданную строку: formatDecimalMoney вставляет НЕразрывный
    // пробел перед «₽», и байт-в-байт сравнение с ним провалилось бы даже при
    // видимо одинаковом тексте — нормализуем ожидание тем же правилом (`\s+` → " ").
    const expectedContributionText = formatDecimalMoney(row.contribution.value).replace(/\s+/g, " ");
    expect(contributionCell).toHaveTextContent(expectedContributionText);
    expect(within(contributionCell).getByTestId("contribution-value")).toHaveClass("text-danger-text");
  });

  /**
   * Ребёнок со статьёй, исчезнувшей из файла на последнем этапе: управляется
   * `sampleStageSummary.rows[1].children[0]` («Прочее (фасады)»). `bargain.kind
   * === "disappeared"` — структурная подпись (`KIND_LABEL`), не число; у
   * `contribution` `value === null` с `reason === "absent_endpoint"` — причина,
   * не ноль. Родителя нужно раскрыть, иначе строки ребёнка нет в DOM.
   */
  it("Торг и Вклад ребёнка со снятой статьёй — структурная подпись и причина, не число/ноль (rows[1].children[0] «Прочее (фасады)»)", async () => {
    const user = userEvent.setup();
    render(<StageSummaryTable summary={sampleStageSummary} />);
    const parent = sampleStageSummary.rows[1];
    const child = parent.children[0];
    expect(child.bargain.kind).toBe("disappeared");
    expect(child.contribution.value).toBeNull();
    expect(child.contribution.reason).not.toBeNull();

    await user.click(screen.getByRole("button", { name: new RegExp(`Раскрыть ${parent.title}`) }));

    const tr = screen.getByText(child.title).closest("tr") as HTMLElement;
    const bargainCell = within(tr).getByTestId("bargain-cell");
    const contributionCell = within(tr).getByTestId("contribution-cell");

    expect(bargainCell).toHaveTextContent(KIND_LABEL[child.bargain.kind as keyof typeof KIND_LABEL]);
    expect(bargainCell).not.toHaveTextContent(/\d/);

    // Вклад — прочерк (не «0»/«0,00»), причина названа атрибутом title:
    // тот же приём, что «Нераспределённое».bargain несёт REASON в title рядом
    // с коротким видимым «без %».
    expect(contributionCell).not.toHaveTextContent(/\d/);
    const contributionValue = within(contributionCell).getByTestId("contribution-value");
    expect(contributionValue.textContent?.trim()).toBe("—");
    expect(contributionValue).toHaveAttribute("title", REASON_LABEL[child.contribution.reason!]);
  });
});
