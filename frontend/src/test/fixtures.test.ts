/**
 * @vitest-environment node
 *
 * Тесты чистых функций: DOM здесь не наблюдается ни разу, а jsdom стоит около
 * секунды на файл. Выигрыш — на ТОЧЕЧНОМ прогоне (эти девять файлов: 9,1 с →
 * 3,8 с), а НЕ на полном наборе: там окружения поднимаются параллельно, и
 * 42,3 с накопленного `environment` уходят в тень тяжёлых компонентных файлов
 * (замер 2026-09-02: 98,6 с до, 100,2 с после — в пределах разброса). Признак
 * «нужен ли jsdom» объявляется ФАЙЛОМ, а не глобом в конфиге: глоб пришлось бы
 * держать в синхронизации с деревом, и молчаливое возвращение файла в jsdom
 * заметить было бы нечем.
 */
import { describe, expect, it } from "vitest";

import {
  sampleStagePositions,
  sampleStageSummary,
  stageSummaryAllUnknown,
  stageSummaryNet,
  stageSummaryWithUnknownSecondColumn,
} from "./fixtures";
import type {
  StagePositions,
  StageSummary,
  StageSummaryCell,
  StageSummaryRow,
  StageSummaryTotalCell,
  Direction,
  StageSummaryChange,
} from "@/types/domain";

// Valid reason codes per backend contract (services/stage_summary.py)

// Change reasons: REASON_FIRST_COLUMN, REASON_UNKNOWN_VAT_BASE, REASON_NO_AMOUNTS
type ChangeReason = "first_column" | "unknown_vat_base" | "no_amounts" | null;
const VALID_CHANGE_REASONS: Set<ChangeReason> = new Set([
  "first_column",
  "unknown_vat_base",
  "no_amounts",
  null,
]);

// Contribution reasons: REASON_UNKNOWN_VAT_BASE, REASON_ABSENT_ENDPOINT
type ContributionReason = "unknown_vat_base" | "absent_endpoint" | null;
const VALID_CONTRIBUTION_REASONS: Set<ContributionReason> = new Set([
  "unknown_vat_base",
  "absent_endpoint",
  null,
]);

// Bargain reasons: REASON_UNKNOWN_VAT_BASE (endpoint unavailable), REASON_UNALLOCATED (unallocated row)
type BargainReason = "unknown_vat_base" | "unallocated" | null;
const VALID_BARGAIN_REASONS: Set<BargainReason> = new Set([
  "unknown_vat_base",
  "unallocated",
  null,
]);

// Track reasons: TRACK_NON_POSITIVE, TRACK_NO_COMPARABLE
type TrackReason = "non_positive_total" | "no_comparable_totals" | null;
const VALID_TRACK_REASONS: Set<TrackReason> = new Set([
  "non_positive_total",
  "no_comparable_totals",
  null,
]);

// Convergence reasons: CONV_FILE_TOTAL_UNAVAILABLE (file_total unavailability marker)
type ConvergenceReason = "file_total_unavailable" | null;
const VALID_CONVERGENCE_REASONS: Set<ConvergenceReason> = new Set([
  "file_total_unavailable",
  null,
]);


/**
 * Проверить инварианты фикстуры свода по этапам (спека 2026-08-27-stage-summary-design.md §2.16).
 * Каждый вариант фикстуры должен пройти ВСЕ проверки — это гарантирует, что на Task 7
 * компонент будет тестироваться против данных, которые сервер действительно может создать.
 */

function checkStageSummaryInvariants(fixture: StageSummary, label: string): void {
  describe(`Инварианты ${label}`, () => {
    /**
     * Проверить, что количество cells везде одинаково и совпадает с количеством колонок.
     */
    it("cells везде одинаковой длины, совпадают с columns", () => {
      const columnCount = fixture.columns.length;
      expect(columnCount).toBeGreaterThan(0);

      // Все строки
      for (const row of fixture.rows) {
        checkRowCellsLength(row, columnCount);
      }
      // Нераспределённое
      expect(fixture.unallocated.cells).toHaveLength(columnCount);
      // Итого
      expect(fixture.total.cells).toHaveLength(columnCount);
    });

    /**
     * amount = null ⇔ state !== "amount" ИЛИ есть unavailable_reason —
     * инвариант ячеек СТАТЕЙ (`StageSummaryCell` несёт `state`).
     */
    it("amount = null точно совпадает с условиями", () => {
      for (const row of fixture.rows) {
        checkAmountNullInvariant(row);
      }
      checkAmountNullInvariant(fixture.unallocated);
    });

    /**
     * У «Итого» своё, более узкое правило (§2.16): `StageSummaryTotalCell` не
     * несёт `state` вовсе, поэтому единственное условие для `amount = null` —
     * недоступная база НДС. При известной оси сумма — ВСЕГДА число, включая
     * ноль (ровно дефект PR #34, который эта ревизия правит).
     */
    it("totals: amount = null ⇔ amount_unavailable_reason ≠ null", () => {
      fixture.total.cells.forEach((cell) => {
        checkTotalCellAmountInvariant(cell);
      });
    });

    /**
     * change.kind: "none" → value=null, direction=null, но reason может быть;
     *             "percent" → value и direction оба не null.
     */
    it("change валидны структурно", () => {
      for (const row of fixture.rows) {
        checkChangesInvariant(row);
      }
      checkChangesInvariant(fixture.unallocated);
      fixture.total.cells.forEach((cell) => {
        checkChangeInvariant(cell.change);
      });
    });

    /**
     * contribution.value = null ⇔ есть reason.
     */
    it("contribution.value = null ⇔ есть reason", () => {
      for (const row of fixture.rows) {
        checkContributionInvariant(row.contribution);
        for (const child of row.children) {
          checkContributionInvariant(child.contribution);
        }
      }
      checkContributionInvariant(fixture.unallocated.contribution);
    });

    /**
     * Сумма по колоне (§2.16): при ИЗВЕСТНОЙ базе `total.cells[i].amount`
     * равна сумме известных `rows[*].cells[i].amount` плюс
     * `unallocated.cells[i].amount` — ВСЕГДА число, включая ноль (у
     * `StageSummaryTotalCell` нет `state`, гасить сумму нечем, кроме
     * неизвестной базы). Null во внутренней сумме игнорируется как
     * неизвестное слагаемое, но итог при известной оси null не бывает.
     */
    it("totals по колонам верны", () => {
      for (let i = 0; i < fixture.columns.length; i++) {
        const totalCell = fixture.total.cells[i];
        if (totalCell.amount_unavailable_reason !== null) {
          continue; // неизвестная база — своя проверка ниже
        }

        // Сумма от рядов
        let sum: string | null = null;
        for (const row of fixture.rows) {
          const rowAmount = row.cells[i].amount;
          if (rowAmount !== null) {
            const parsed = parseFloat(rowAmount);
            sum = sum === null ? rowAmount : String(parseFloat(sum) + parsed);
          }
        }

        // Плюс нераспределённое
        const unallocAmount = fixture.unallocated.cells[i].amount;
        if (unallocAmount !== null) {
          const parsed = parseFloat(unallocAmount);
          sum = sum === null ? unallocAmount : String(parseFloat(sum) + parsed);
        }

        // Известная ось — сумма ВСЕГДА число (§2.16): отсутствие известных
        // слагаемых читается как ноль, а не как null.
        expect(totalCell.amount).not.toBeNull();
        expect(parseFloat(totalCell.amount!)).toBeCloseTo(sum === null ? 0 : parseFloat(sum));
      }
    });

    /**
     * `columns[i].total` и `total.cells[i].amount` — ОДНО И ТО ЖЕ число по
     * контракту (§2.16: `columns[i].total = total.cells[i].amount`,
     * `backend/services/stage_summary.py::compute_summary`, `ColumnOut` строится
     * из `total_cells[idx]`) — точное сравнение строк, включая совпадение
     * null с null, а не приведение обоих к «0» перед сравнением (последнее
     * пропустило бы расхождение "0.00" против null).
     */
    it("columns[i].total совпадает с total.cells[i].amount для каждой колонки", () => {
      for (let i = 0; i < fixture.columns.length; i++) {
        expect(fixture.columns[i].total).toBe(fixture.total.cells[i].amount);
      }
    });

    /**
     * `total.cells[i].amount = null` ⇔ база НДС этой колонки неизвестна
     * (§2.16) — то же самое условие, что `vat_state`/`amount_unavailable_reason`
     * колонки, названное явно, а не выведенное из побочных эффектов расчёта.
     */
    it("totals: amount отсутствует ровно когда база НДС неизвестна", () => {
      for (let i = 0; i < fixture.columns.length; i++) {
        const isUnknown = fixture.columns[i].vat_state === "unknown_vat_base";
        expect(fixture.total.cells[i].amount === null).toBe(isUnknown);
        expect(fixture.total.cells[i].amount_unavailable_reason !== null).toBe(isUnknown);
      }
    });

    /**
     * bar_height_pct ≠ null ⇔ track.available = true AND column.total !== null.
     * When track is unavailable (e.g., non-positive totals exist), all bar heights become null globally
     * even if individual columns have positive totals. That case is not exercised today (see
     * MSW handler stage-summary GET endpoint outcome 'non_positive_track' for when it first matters).
     * Если bar_height_pct есть, то макс из них = 100.0 (numerically).
      * backend/services/stage_summary.py, compute_summary (участок bar_height_pct и track availability).
     */
    it("bar_height_pct корректны", () => {
      let maxHeight: number | null = null;
      for (const col of fixture.columns) {
        if (!fixture.track.available || col.total === null) {
          expect(col.bar_height_pct).toBeNull();
        } else {
          expect(col.bar_height_pct).not.toBeNull();
          const height = parseFloat(col.bar_height_pct!);
          if (maxHeight === null || height > maxHeight) {
            maxHeight = height;
          }
        }
      }
      if (maxHeight !== null) {
        expect(maxHeight).toBeCloseTo(100);
      }
    });

    /**
     * bar_height_pct = (column.total / max_total) * 100, rounded to one decimal place.
     * This guards against inherited values that happen to be correct by coincidence (e.g., when
     * dividing all totals by the same factor preserves ratios but a partial transformation breaks it).
     * Comparison done as strings with one decimal precision to match server rendering.
      * backend/services/stage_summary.py, compute_summary: bar_height_pct = total / max_total * 100
     */
    it("bar_height_pct ratio equals total / max_total", () => {
      if (fixture.track.available) {
        // Find max total among columns with available totals
        let maxTotal: number | null = null;
        for (const col of fixture.columns) {
          if (col.total !== null) {
            const total = parseFloat(col.total);
            if (maxTotal === null || total > maxTotal) {
              maxTotal = total;
            }
          }
        }

        // Check each column's bar height equals its ratio
        if (maxTotal !== null && maxTotal > 0) {
          for (const col of fixture.columns) {
            if (col.bar_height_pct !== null) {
              expect(col.total).not.toBeNull();
              const colTotal = parseFloat(col.total!);
              const expectedRatio = (colTotal / maxTotal) * 100;
              // Round to one decimal place the same way the server does
              const expectedHeightStr = expectedRatio.toFixed(1);
              expect(col.bar_height_pct).toBe(expectedHeightStr);
            }
          }
        }
      }
    });

    /**
     * display.tax_basis = "none" ⇔ ALL columns have vat_state = "unknown_vat_base".
     */
    it("tax_basis = none ⇔ все колонки unknown", () => {
      const allUnknown = fixture.columns.every((c) => c.vat_state === "unknown_vat_base");
      if (fixture.display.tax_basis === "none") {
        expect(allUnknown).toBe(true);
      } else {
        expect(allUnknown).toBe(false);
      }
    });

    /**
     * participant.stages должны быть упорядочены по stage_no; count(selected) == columns.length.
     * offer_id каждого selected совпадают с columns[*].offer_id (в любом порядке).
     */
    it("participant.stages согласованы", () => {
      const stages = fixture.participant.stages;
      for (let i = 1; i < stages.length; i++) {
        expect(stages[i].stage_no).toBeGreaterThan(stages[i - 1].stage_no);
      }

      const selectedStages = stages.filter((s) => s.selected);
      expect(selectedStages).toHaveLength(fixture.columns.length);

      const selectedOfferIds = new Set(selectedStages.map((s) => s.offer_id));
      const columnOfferIds = new Set(fixture.columns.map((c) => c.offer_id));
      expect(selectedOfferIds).toEqual(columnOfferIds);
    });

    /**
     * Все reason коды из контракта сервера — не выдуманные.
     */
    it("все reason коды в контракте", () => {
      for (const row of fixture.rows) {
        checkReasonCodesInRow(row);
      }
      checkReasonCodesInRow(fixture.unallocated);
      fixture.total.cells.forEach((cell) => {
        expect(VALID_CHANGE_REASONS.has(cell.change.reason as ChangeReason)).toBe(true);
      });
      for (const col of fixture.columns) {
        expect(VALID_CHANGE_REASONS.has(col.total_change.reason as ChangeReason)).toBe(true);
        expect(VALID_CONVERGENCE_REASONS.has(col.convergence.reason as ConvergenceReason)).toBe(true);
      }
      expect(VALID_TRACK_REASONS.has(fixture.track.reason as TrackReason)).toBe(true);
    });

    /**
     * Индекс 0: все изменения — kind="none" с reason="first_column".
     */
    it("индекс 0: changes = none/first_column", () => {
      for (const row of fixture.rows) {
        checkFirstColumnChanges(row);
      }
      checkFirstColumnChanges(fixture.unallocated);
      expect(fixture.total.cells[0].change).toEqual({
        kind: "none",
        value: null,
        direction: null,
        reason: "first_column",
      });
      expect(fixture.columns[0].total_change).toEqual({
        kind: "none",
        value: null,
        direction: null,
        reason: "first_column",
      });
    });

    /**
     * Contribution value/direction совпадают с last_amount - first_amount.
     * Absent на конце → value=null/reason="absent_endpoint".
     * Absent на начале → value=null/reason="absent_endpoint".
     */
    it("contribution = last - first (с учётом состояний)", () => {
      for (const row of fixture.rows) {
        checkContributionValues(row);
      }
      checkUnallocatedContribution(fixture.unallocated);
    });

    /**
     * vat_state unknown ⇔ vat_rate_base = null.
     */
    it("unknown_vat_base ⇔ vat_rate_base = null", () => {
      for (const col of fixture.columns) {
        const isUnknown = col.vat_state === "unknown_vat_base";
        const rateBaseIsNull = col.vat_rate_base === null;
        expect(isUnknown).toBe(rateBaseIsNull);
      }
    });

    /**
     * Если rates_by_column заполнен, его элементы совпадают с vat_rate_base колонок.
     */
    it("rates_by_column совпадает с column rate_bases", () => {
      if (fixture.display.rates_by_column !== null) {
        expect(fixture.display.rates_by_column).toHaveLength(fixture.columns.length);
        for (let i = 0; i < fixture.columns.length; i++) {
          expect(fixture.display.rates_by_column[i]).toBe(fixture.columns[i].vat_rate_base);
        }
      }
    });

    /**
     * Unallocated: bargain.kind="none"/reason="unallocated", contribution — число когда оба конца известны.
     */
    it("unallocated структура корректна", () => {
      expect(fixture.unallocated.bargain.kind).toBe("none");
      expect(fixture.unallocated.bargain.reason).toBe("unallocated");
      // Contribution: value — число (не null) когда оба конца имеют gross (state=amount) БЕЗ unavailable_reason
      // We read state, not displayed amount: when tax base is unknown, amount is null while state="amount".
      const firstCell = fixture.unallocated.cells[0];
      const lastCell = fixture.unallocated.cells[fixture.columns.length - 1];
      const bothKnown =
        firstCell.state === "amount" &&
        firstCell.amount_unavailable_reason === null &&
        lastCell.state === "amount" &&
        lastCell.amount_unavailable_reason === null;
      if (bothKnown) {
        expect(fixture.unallocated.contribution.value).not.toBeNull();
      }
    });

    /**
     * Когда kind ≠ none, reason обязан быть null.
     * Сервер прикрепляет reason только к подавленным (kind=none) изменениям.
     */
    it("kind != none ⇒ reason = null", () => {
      for (const row of fixture.rows) {
        checkKindReasonContract(row);
      }
      checkKindReasonContract(fixture.unallocated);
      fixture.total.cells.forEach((cell) => {
        if (cell.change.kind !== "none") {
          expect(cell.change.reason).toBeNull();
        }
      });
      for (const col of fixture.columns) {
        if (col.total_change.kind !== "none") {
          expect(col.total_change.reason).toBeNull();
        }
      }
    });

    /**
     * Эквивалентность: kind IS percent/abs_only ⟺ (both states = "amount" AND no unavailability).
     * Else kind IS structural/suppressed (removed/disappeared/appeared/reappeared/none).
      * backend/services/stage_summary.py, change_between: only percent/abs_only for state_amount pair.
     *
     * У «Итого» условие ДРУГОЕ (§2.16, `_numeric_change`): агрегат не несёт
     * `state`, а числовое правило смотрит только на доступность суммы —
     * `amount !== null` на обоих концах, не на матрицу состояний §2.6.
     */
    it("kind ⟺ both=amount+available equivalence", () => {
      for (const row of fixture.rows) {
        checkChangeKindEquivalence(row);
      }
      checkChangeKindEquivalence(fixture.unallocated);
      for (let i = 1; i < fixture.total.cells.length; i++) {
        const prev = fixture.total.cells[i - 1];
        const cur = fixture.total.cells[i];
        const isBothAmountAvailable = prev.amount !== null && cur.amount !== null;
        if (isBothAmountAvailable) {
          expect(["percent", "abs_only"]).toContain(cur.change.kind);
        } else {
          expect(["percent", "abs_only"]).not.toContain(cur.change.kind);
        }
      }
      for (let i = 1; i < fixture.columns.length; i++) {
        const prev = fixture.columns[i - 1];
        const cur = fixture.columns[i];
        const isBothAmountAvailable = prev.total !== null && cur.total !== null;
        if (isBothAmountAvailable) {
          expect(["percent", "abs_only"]).toContain(cur.total_change.kind);
        } else {
          expect(["percent", "abs_only"]).not.toContain(cur.total_change.kind);
        }
      }
    });

    /**
     * state="amount" требует rows_with_amount > 0: ненулевая сумма не может быть от нулевых строк.
      * backend/services/stage_summary.py, _node_inputs: rows_with_amount считается для узла,
     * state="amount" означает сумма ненулевая (спека §2.5).
     * Инвариант — про `state` ячеек СТАТЕЙ; у «Итого» (`StageSummaryTotalCell`)
     * `state` нет вовсе (§2.16, ревизия 28.08.2026 по внешнему ревью PR #34) —
     * условие к нему структурно неприменимо, а не смягчено: раньше тест
     * подгонял агрегат под правило ячейки статьи через фиктивное `state`,
     * которого контракт больше не несёт.
     */
    it("state=amount => rows_with_amount > 0", () => {
      for (const row of fixture.rows) {
        checkAmountStateInvariant(row);
      }
      checkAmountStateInvariant(fixture.unallocated);
    });

    /**
     * Если первая или последняя ячейка ряда имеет unavailable_reason, то bargain и contribution
     * обязаны быть супрессированы с ЭТОЙ ЖЕ причиной. Обе читают один пайр концов и не могут расходиться.
      * backend/services/stage_summary.py, _endpoints: обе вычисляются с одной и той же reason.
      * Исключение: unallocated имеет фиксированное bargain/reason независимо от концов (compute_summary).
     */
    it("недоступный конец => bargain и contribution синхронны", () => {
      for (const row of fixture.rows) {
        checkEndpointUnavailabilitySync(row);
      }
    });

    /**
     * Если ячейка-предшественник в массиве имеет unavailable_reason, то change текущей ячейки —
     * kind=none с ЭТОЙ ЖЕ причиной, независимо от собственной базы ячейки.
      * backend/services/stage_summary.py, _change_step: unavailable_reason=reason or prev_reason
     * Пропагация: unknown_vat_base на col1 => col2 change тоже has reason="unknown_vat_base".
     */
    it("недоступность пропагируется forward => change = none/reason", () => {
      for (const row of fixture.rows) {
        checkPropagatedUnavailability(row);
      }
      checkPropagatedUnavailability(fixture.unallocated);
      for (let i = 1; i < fixture.total.cells.length; i++) {
        const prev = fixture.total.cells[i - 1];
        if (prev.amount_unavailable_reason !== null) {
          expect(fixture.total.cells[i].change.kind).toBe("none");
          expect(fixture.total.cells[i].change.reason).toBe(prev.amount_unavailable_reason);
        }
      }
      for (let i = 1; i < fixture.columns.length; i++) {
        const prev = fixture.columns[i - 1];
        if (prev.total === null) {
          expect(fixture.columns[i].total_change.kind).toBe("none");
          expect(fixture.columns[i].total_change.reason).not.toBeNull();
        }
      }
    });

    /**
     * rates_by_column = null точно когда tax_basis != "net".
      * backend/services/stage_summary.py, pick_tax_basis возвращает rates только для TAX_NET.
     */
    it("rates_by_column is list iff tax_basis = net", () => {
      if (fixture.display.tax_basis === "net") {
        expect(fixture.display.rates_by_column).not.toBeNull();
      } else {
        expect(fixture.display.rates_by_column).toBeNull();
      }
    });

    /**
     * state="amount" без unavailable_reason обязан иметь non-null и ненулевой amount.
      * backend/services/stage_summary.py, cell_states: state="amount" <=> gross !== 0.
     * Инвариант — про `state` ячеек СТАТЕЙ, а не про «Итого»: агрегату ноль —
     * законное число, а не признак другого состояния (§2.16). Утверждать про
     * «Итого» «amount ≠ "0.00"» было бы прямым нарушением контракта — ровно
     * тот дефект, который правит эта ревизия (нулевой итог с живыми строками
     * публиковал состояние «снято» вместе с суммой "0.00").
     */
    it("state=amount + no unavail => amount non-null и non-zero", () => {
      for (const row of fixture.rows) {
        checkAmountStateNonZero(row);
      }
      checkAmountStateNonZero(fixture.unallocated);
    });

    /*
     * ИНВАРИАНТА ПОРЯДКА СТРОК ЗДЕСЬ НЕТ, И ЭТО ГРАНИЦА НАБЛЮДАЕМОСТИ, А НЕ
     * ПРОПУСК (§2.13, ревизия 28.08.2026; `docs/insights/unobservable-in-the-runner.md`).
     * Порядок строк задаёт `sort_order` классификатора, а его контракт ответа
     * НЕ несёт вовсе — по телу свода классификаторный порядок неотличим от
     * любого другого, и проверка, написанная здесь, утверждала бы не правило, а
     * совпадение кодов в конкретной фикстуре. Правило стережёт сервер
     * (`backend/tests/unit/test_stage_summary.py`,
     * `test_rows_sorted_by_classifier_sort_order_within_level` — на фикстуре, где
     * прежнее правило дало бы другой порядок на каждом уровне). Обязательство
     * КЛИЕНТА тут другое и проверяется своим тестом: не пересортировывать
     * пришедшее (`StageSummaryTable.test.tsx`, «порядок строк — как пришёл с
     * сервера»; вход там намеренно переставлен, иначе проверка была бы зелёной
     * при четырёх разных правилах сразу). Прежняя редакция держала здесь
     * проверку «по убыванию |contribution|, nulls в конце» — правила с таким
     * смыслом больше нет.
     *
     * Вторая половина той же мысли, без которой правило ловит половину
     * дефектов (`docs/insights/state-the-rule-as-an-equivalence.md`):
     * наблюдаемое СЛЕДСТВИЕ у правила всё-таки есть — в сиде классификатора
     * `sort_order` назначается по порядку кодов, поэтому на живых данных коды
     * внутри уровня возрастают. Проверять его здесь НЕЛЬЗЯ: такая проверка
     * прибила бы фикстуры к содержимому сида, а не к правилу, и покраснела бы
     * от справочника, где `sort_order` разошёлся с кодом. Именно поэтому
     * инвариант снят, а не переписан «по кодам».
     */

    /**
     * categories_with_amount = count top-level rows whose LAST cell has non-zero sum.
     * categories_total = count top-level rows.
      * backend/services/stage_summary.py, compute_summary (построение kpi).
     *
     * NOTE: We count by STATE, not by displayed amount. When tax base is unknown,
     * the backend withholds the sum from display (amount=null) but preserves the state
     * as "amount" (marking that an underlying gross was computed). This is exactly the
     * contract, so the count reads the state alone.
     */
    it("KPI counts match row contributions", () => {
      const withAmount = fixture.rows.filter((row) => {
        const lastCell = row.cells[row.cells.length - 1];
        return lastCell.state === "amount";
      }).length;
      expect(fixture.kpi.categories_with_amount).toBe(withAmount);
      expect(fixture.kpi.categories_total).toBe(fixture.rows.length);
    });

    /**
     * Convergence: когда file_total известен, delta = categories_sum - file_total и converged = (delta == 0).
      * backend/services/stage_summary.py, compute_summary (построение convergence).
     */
    it("convergence: delta and converged consistent with totals", () => {
      for (const col of fixture.columns) {
        if (col.convergence.file_total !== null) {
          expect(col.convergence.delta).not.toBeNull();
          const delta = parseFloat(col.convergence.delta!);
          const catSum = parseFloat(col.convergence.categories_sum);
          const fileTotal = parseFloat(col.convergence.file_total);
          expect(delta).toBeCloseTo(catSum - fileTotal);
          expect(col.convergence.converged).toBe(delta === 0);
        } else {
          expect(col.convergence.delta).toBeNull();
          expect(col.convergence.converged).toBeNull();
        }
      }
    });
  });
}

/**
 * Проверить, что все reason коды в ряду корректны.
 */
function checkReasonCodesInRow(row: StageSummaryRow): void {
  row.cells.forEach((cell, idx) => {
    if (!VALID_CHANGE_REASONS.has(cell.change.reason as ChangeReason)) {
      throw new Error(`Row ${row.code} cell ${idx}: invalid change reason "${cell.change.reason}". Valid: ${Array.from(VALID_CHANGE_REASONS).join(", ")}`);
    }
  });
  if (!VALID_BARGAIN_REASONS.has(row.bargain.reason as BargainReason)) {
    throw new Error(`Row ${row.code}: invalid bargain reason "${row.bargain.reason}". Valid: ${Array.from(VALID_BARGAIN_REASONS).join(", ")}`);
  }
  if (!VALID_CONTRIBUTION_REASONS.has(row.contribution.reason as ContributionReason)) {
    throw new Error(`Row ${row.code}: invalid contribution reason "${row.contribution.reason}". Valid: ${Array.from(VALID_CONTRIBUTION_REASONS).join(", ")}`);
  }
  for (const child of row.children) {
    checkReasonCodesInRow(child);
  }
}

/**
 * Проверить, что первая ячейка каждого ряда — none/first_column.
 */
function checkFirstColumnChanges(row: StageSummaryRow): void {
  expect(row.cells[0].change).toEqual({
    kind: "none",
    value: null,
    direction: null,
    reason: "first_column",
  });
  for (const child of row.children) {
    checkFirstColumnChanges(child);
  }
}

/**
 * Проверить, что contribution = last_amount - first_amount.
 * Unknown VAT на концах → null с причиной unknown_vat_base.
 * Absent на концах → null/absent_endpoint.
 */
function checkContributionValues(row: StageSummaryRow): void {
  const cells = row.cells;
  if (cells.length > 0) {
    // Contribution is based on first and last cells only
    const firstCell = cells[0];
    const lastCell = cells[cells.length - 1];

    // If first or last cell has unavailable_reason, contribution is null
    const firstUnavailable = firstCell.amount_unavailable_reason !== null;
    const lastUnavailable = lastCell.amount_unavailable_reason !== null;
    if (firstUnavailable || lastUnavailable) {
      expect(row.contribution.value).toBeNull();
      expect(row.contribution.reason).not.toBeNull();
      return;
    }

    const firstAmount = firstCell.state === "absent" ? null : (firstCell.amount ? parseFloat(firstCell.amount) : 0);
    const lastAmount = lastCell.state === "absent" ? null : (lastCell.amount ? parseFloat(lastCell.amount) : 0);

    if (firstAmount === null || lastAmount === null) {
      expect(row.contribution.value).toBeNull();
      expect(row.contribution.reason).toBe("absent_endpoint");
    } else {
      const delta = lastAmount - firstAmount;
      const direction: Direction | null =
        delta > 0 ? "up" : delta < 0 ? "down" : "flat";
      expect(row.contribution.value).not.toBeNull();
      expect(parseFloat(row.contribution.value!)).toBeCloseTo(delta);
      expect(row.contribution.direction).toBe(direction);
      expect(row.contribution.reason).toBeNull();
    }
  }

  for (const child of row.children) {
    checkContributionValues(child);
  }
}

/**
 * Проверить структуру unallocated.contribution.
 */
function checkUnallocatedContribution(unalloc: StageSummaryRow): void {
  // Unallocated всегда is_unallocated=true
  expect(unalloc.is_unallocated).toBe(true);
  // Contribution должна быть числом для 0→0 пути ЕСЛИ нет неизвестных оснований
  const hasUnavailable = unalloc.cells.some((c) => c.amount_unavailable_reason !== null);
  if (!hasUnavailable && unalloc.cells[0].amount === "0.00" && unalloc.cells[unalloc.cells.length - 1].amount === "0.00") {
    expect(unalloc.contribution.value).toBe("0.00");
    expect(unalloc.contribution.direction).toBe("flat");
    expect(unalloc.contribution.reason).toBeNull();
  }
}

/**
 * Вспомогательная функция для рекурсивной проверки длины cells в рядах.
 */
function checkRowCellsLength(row: StageSummaryRow, expected: number): void {
  expect(row.cells).toHaveLength(expected);
  for (const child of row.children) {
    checkRowCellsLength(child, expected);
  }
}

/**
 * Проверить amount в рядах рекурсивно.
 */
function checkAmountNullInvariant(row: StageSummaryRow): void {
  row.cells.forEach((cell) => {
    checkCellAmountInvariant(cell);
  });
  for (const child of row.children) {
    checkAmountNullInvariant(child);
  }
}

function checkCellAmountInvariant(cell: StageSummaryCell): void {
  const amountIsNull = cell.amount === null;
  const shouldBeNull = cell.state !== "amount" || cell.amount_unavailable_reason !== null;
  expect(amountIsNull).toBe(shouldBeNull);
}

/**
 * Тот же инвариант для «Итого» — БЕЗ условия про `state`, которого у
 * `StageSummaryTotalCell` нет (спека §2.16): `amount = null` ровно когда база
 * НДС неизвестна, и никогда — из-за нулевой или отсутствующей суммы.
 */
function checkTotalCellAmountInvariant(cell: StageSummaryTotalCell): void {
  const amountIsNull = cell.amount === null;
  const shouldBeNull = cell.amount_unavailable_reason !== null;
  expect(amountIsNull).toBe(shouldBeNull);
}

/**
 * Проверить change структурно во всех ячейках ряда.
 */
function checkChangesInvariant(row: StageSummaryRow): void {
  row.cells.forEach((cell) => {
    checkChangeInvariant(cell.change);
  });
  checkChangeInvariant(row.bargain);
  for (const child of row.children) {
    checkChangesInvariant(child);
  }
}

function checkChangeInvariant(change: StageSummaryChange): void {
  if (change.kind === "none") {
    expect(change.value).toBeNull();
    expect(change.direction).toBeNull();
    // reason может быть или не быть
  } else if (change.kind === "percent") {
    expect(change.value).not.toBeNull();
    expect(change.direction).not.toBeNull();
  }
}

/**
 * Проверить contribution: value null ⇔ reason есть.
 */
function checkContributionInvariant(contribution: { value: string | null; direction: Direction | null; reason: string | null }): void {
  const valueIsNull = contribution.value === null;
  const hasReason = contribution.reason !== null;
  expect(valueIsNull).toBe(hasReason);
}

/**
 * Проверить контракт: kind != none ⇒ reason = null.
  * backend/services/stage_summary.py: change_between возвращает reason только для kind=none.
 */
function checkKindReasonContract(row: StageSummaryRow): void {
  row.cells.forEach((cell) => {
    if (cell.change.kind !== "none") {
      expect(cell.change.reason).toBeNull();
    }
  });
  if (row.bargain.kind !== "none") {
    expect(row.bargain.reason).toBeNull();
  }
  for (const child of row.children) {
    checkKindReasonContract(child);
  }
}

/**
 * Проверить контракт: шаг после первого с обоими доступными концами — не none.
  * backend/services/stage_summary.py: change_between возвращает kind != none только когда оба конца STATE_AMOUNT
  * и ни один не имеет unavailable_reason.
 */
function checkChangeKindEquivalence(row: StageSummaryRow): void {
  const cells = row.cells;
  for (let i = 1; i < cells.length; i++) {
    const prev = cells[i - 1];
    const cur = cells[i];
    const isBothAmountAvailable =
      prev.state === "amount" &&
      prev.amount_unavailable_reason === null &&
      cur.state === "amount" &&
      cur.amount_unavailable_reason === null;

    if (isBothAmountAvailable) {
      // Both endpoints available => kind must be percent or abs_only
      expect(["percent", "abs_only"]).toContain(cur.change.kind);
    } else {
      // Any other pairing => kind must NOT be percent or abs_only
      expect(["percent", "abs_only"]).not.toContain(cur.change.kind);
    }
  }
  for (const child of row.children) {
    checkChangeKindEquivalence(child);
  }
}

/**
 * Проверить: state="amount" требует rows_with_amount > 0.
  * backend/services/stage_summary.py, _node_inputs: ненулевая сумма не может быть от нулевых строк.
 */
function checkAmountStateInvariant(row: StageSummaryRow): void {
  row.cells.forEach((cell) => {
    if (cell.state === "amount") {
      expect(cell.rows.rows_with_amount).toBeGreaterThan(0);
    }
  });
  for (const child of row.children) {
    checkAmountStateInvariant(child);
  }
}

/**
 * Проверить синхронизацию bargain и contribution при недоступном концовом: обе должны быть подавлены с одной причиной.
  * backend/services/stage_summary.py, _endpoints: обе функции читают пайр концов с одной reason.
 */
function checkEndpointUnavailabilitySync(row: StageSummaryRow): void {
  const cells = row.cells;
  if (cells.length > 0) {
    const firstCell = cells[0];
    const lastCell = cells[cells.length - 1];
    const firstReason = firstCell.amount_unavailable_reason;
    const lastReason = lastCell.amount_unavailable_reason;

    // Если любой конец недоступен, bargain и contribution должны отражать эту причину
    if (firstReason !== null || lastReason !== null) {
      const expectedReason = firstReason || lastReason;
      expect(row.bargain.kind).toBe("none");
      expect(row.bargain.reason).toBe(expectedReason);
      expect(row.contribution.value).toBeNull();
      expect(row.contribution.reason).toBe(expectedReason);
    }
  }
  for (const child of row.children) {
    checkEndpointUnavailabilitySync(child);
  }
}

/**
 * Проверить: если ячейка-предшественник имеет unavailable_reason, то change текущей = kind none с той же причиной.
  * backend/services/stage_summary.py, _change_step: unavailable_reason=reason or prev_reason
 */
function checkPropagatedUnavailability(row: StageSummaryRow): void {
  const cells = row.cells;
  for (let i = 1; i < cells.length; i++) {
    const prev = cells[i - 1];
    if (prev.amount_unavailable_reason !== null) {
      expect(cells[i].change.kind).toBe("none");
      expect(cells[i].change.reason).toBe(prev.amount_unavailable_reason);
    }
  }
  for (const child of row.children) {
    checkPropagatedUnavailability(child);
  }
}

/**
 * Проверить: state="amount" без unavailable_reason => amount non-null и non-zero.
  * backend/services/stage_summary.py, cell_states: non-zero => state="amount", zero => state="removed"/"not_evaluated".
 */
function checkAmountStateNonZero(row: StageSummaryRow): void {
  row.cells.forEach((cell) => {
    if (cell.state === "amount" && cell.amount_unavailable_reason === null) {
      expect(cell.amount).not.toBeNull();
      expect(cell.amount).not.toBe("0.00");
    }
  });
  for (const child of row.children) {
    checkAmountStateNonZero(child);
  }
}

// ============================================================================
// Запустить проверки для каждой фикстуры
// ============================================================================

checkStageSummaryInvariants(sampleStageSummary, "sampleStageSummary");
checkStageSummaryInvariants(stageSummaryNet(), "stageSummaryNet");
checkStageSummaryInvariants(stageSummaryAllUnknown(), "stageSummaryAllUnknown");
checkStageSummaryInvariants(stageSummaryWithUnknownSecondColumn(), "stageSummaryWithUnknownSecondColumn");

// ============================================================================
// Разложение статьи свода (спека 2026-08-30-position-drilldown-design.md
// §2.11, §2.13; Task 10 плана) — три инварианта фикстуры `sampleStagePositions`,
// по образцу проверок sampleStageSummary выше.
// ============================================================================

/**
 * Инварианты `sampleStagePositions` (задача 10 плана):
 *
 * 1. Сходимость (§2.13) — сумма ячеек строк в каждой колонке равна
 *    `article_amount` этой колонки: строка «прочие» несёт остаток, и это
 *    обещание проверяется тестом, а не глазами (§2.13 находило пропажу
 *    допработ, которую четыре пары глаз до того не увидели).
 * 2. `estimate_rows == 0 ⟺ state == "absent"` (§2.11: `estimate_rows: 0`
 *    возможен ТОЛЬКО при `state: absent` — эквивалентность, а не одностороннее
 *    следствие).
 * 3. `row_key` уникальны в наборе — ровно тот инвариант, ради которого поле
 *    заведено (§2.11): устойчивая идентичность строки для React-ключа, а не
 *    сборка из `kind` + `chapter_ref_raw`, которая схлопнула бы две допработы
 *    разных лотов с одной ссылкой.
 */
function checkStagePositionsInvariants(fixture: StagePositions, label: string): void {
  describe(`Инварианты ${label}`, () => {
    it("сумма ячеек строк по колонке равна article_amount (§2.13)", () => {
      fixture.convergence.forEach((column, index) => {
        if (column.article_amount === null) return; // недоступная база НДС — своя ветка (§2.8), здесь не встречается
        const sum = fixture.rows.reduce((acc, row) => {
          const amount = row.cells[index].amount;
          return amount === null ? acc : acc + parseFloat(amount);
        }, 0);
        expect(sum).toBeCloseTo(parseFloat(column.article_amount));
        expect(column.shown_sum).not.toBeNull();
        expect(parseFloat(column.shown_sum!)).toBeCloseTo(sum);
        expect(column.converged).toBe(true);
      });
    });

    it('estimate_rows == 0 ⟺ state == "absent" (§2.11)', () => {
      for (const row of fixture.rows) {
        for (const cell of row.cells) {
          expect(cell.estimate_rows === 0).toBe(cell.state === "absent");
        }
      }
    });

    it("row_key уникальны в наборе (§2.11)", () => {
      const keys = fixture.rows.map((row) => row.row_key);
      expect(new Set(keys).size).toBe(keys.length);
    });
  });
}

checkStagePositionsInvariants(sampleStagePositions, "sampleStagePositions");
