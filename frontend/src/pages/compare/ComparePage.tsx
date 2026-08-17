import { Fragment, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ChevronRight } from "lucide-react";

import { EmptyState } from "@/components/ui-domain/EmptyState";
import { MoneyCell } from "@/components/ui-domain/MoneyCell";
import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatDate, formatSharePercent, roundDecimalPercent } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useComparison, useComparisonReport } from "@/services/queries";
import type {
  Comparison,
  ComparisonBucket,
  ComparisonBucketCell,
  ComparisonColumn,
  ComparisonIncompleteReason,
  ComparisonParams,
  ComparisonRow,
  ComparisonVatMode,
} from "@/types/domain";

/**
 * Сравнение договоров по статьям классификатора
 * (`docs/superpowers/specs/2026-08-17-contract-comparison-design.md`, план —
 * задача 8).
 *
 * Вход — только из списка договоров (`ContractsPage`, задача 7): выборка
 * (`ids` либо `all=1` + фильтры `q`/`object_id`/`contractor_id`/
 * `rate_class_id`) целиком живёт в адресе и эта страница её лишь ПЕРЕДАЁТ
 * серверу, не редактирует. Пункта в главном меню нет (спека §2.9) —
 * сравнение без выборки открывать не с чем.
 *
 * Считает всё сервер, ОДНИМ агрегатом на экран и Excel-лист (спека §2.7):
 * здесь только раскладка, восстановление дерева из плоского списка и показ.
 *
 * **Дерево строк.** `rows` приходит ПЛОСКИМ списком уже в порядке чтения
 * (статья → все её потомки → её «Без подстатьи» последней, спека §2.1.1);
 * клиент восстанавливает иерархию по `code`/`parent_code` — тем же
 * приёмом, что `CategoryTable.tsx` паспорта восстанавливает дерево по
 * `id`/`parent_id`, только по коду вместо числового идентификатора: у
 * синтетических строк («Без подстатьи», «Нераспределённое») нет узла
 * классификатора, а код есть всегда.
 *
 * **Режим НДС и выбранная ставка живут в URL** (спека §2.3, DoD 18) — ссылка
 * обязана воспроизводить увиденное. Переключатель корзины («Итого/ДГП/ДС»,
 * DoD 15) в URL не заведён: спека требует URL только для режима НДС и
 * ставки (DoD 18), про корзину — нет, и заводить лишнее состояние в адресе
 * незачем.
 */

const BUCKET_LABELS: Record<ComparisonBucket, string> = {
  total: "Итого",
  base: "ДГП",
  amendments: "ДС",
};

const VAT_MODE_LABELS: Record<ComparisonVatMode, string> = {
  own: "Своя ставка",
  single: "Единая",
  net: "Без НДС",
};

/** Словарь причин неполноты (спека §2.1.3) — тот же смысл, что паспортная подпись, но список СОВМЕЩАЕТ все причины разом, а не выбирает старшую. */
const REASON_LABELS: Record<ComparisonIncompleteReason, string> = {
  unpriced_rows: "без цены",
  not_finite_rows: "с ошибкой",
  vat_base_unknown: "неизвестна база НДС",
  display_rate_undefined: "ставка показа не определена",
};

// ---------------------------------------------------------------------------
//  Дерево строк из плоского списка (спека §2.1.1)
// ---------------------------------------------------------------------------

interface ComparisonTreeNode {
  row: ComparisonRow;
  children: ComparisonTreeNode[];
}

function buildComparisonTree(rows: ComparisonRow[]): ComparisonTreeNode[] {
  const byCode = new Map<string, ComparisonTreeNode>();
  for (const row of rows) byCode.set(row.code, { row, children: [] });

  const roots: ComparisonTreeNode[] = [];
  for (const row of rows) {
    const node = byCode.get(row.code);
    if (!node) continue;
    if (row.parent_code === null) {
      roots.push(node);
      continue;
    }
    byCode.get(row.parent_code)?.children.push(node);
  }
  return roots;
}

// ---------------------------------------------------------------------------
//  Ячейка: прочерк / ноль / погашенное число (спека §2.1.2, §2.1.3)
// ---------------------------------------------------------------------------

function incompleteReasonsText(reasons: ComparisonIncompleteReason[]): string {
  return reasons.map((reason) => REASON_LABELS[reason]).join(", ");
}

/**
 * Три состояния ячейки нельзя путать (спека §2.1.2, §2.1.3):
 * - `state === "absent"` — статьи нет ни в одной смете договора: голый прочерк;
 * - `shown === null` при `state === "value"` — статья ЕСТЬ, число погашено
 *   причинами (§2.1.3): прочерк СО СЛОВАМИ, а не молчание;
 * - иначе — число, включая настоящий ноль (`state === "zero"`), который
 *   `MoneyCell` печатает как «0,00 ₽», а не как прочерк.
 */
function ComparisonAmountCell({ cell }: { cell: ComparisonBucketCell }) {
  if (cell.state === "absent") {
    return (
      <span className="text-fg-tertiary" title="Статьи нет в смете этого договора">
        —
      </span>
    );
  }
  if (cell.shown === null) {
    const reasons = incompleteReasonsText(cell.incomplete_reasons);
    return (
      <span className="text-fg-tertiary" title={reasons}>
        — ({reasons})
      </span>
    );
  }
  return <MoneyCell value={cell.shown} />;
}

/**
 * Полоса подсветки (спека §2.5, правило 6): ±10&nbsp;% нейтральны — эта
 * граница дана спекой дословно. Вторая граница спекой ЧИСЛОМ не задана —
 * макет иллюстрирует две ступени статичной вёрсткой, а не формулой (его
 * `<script>` пересчитывает только цифры, не CSS-классы), поэтому 30&nbsp;%
 * ниже — решение задачи 8, а не значение из спеки. При появлении явного
 * числа в спеке константу нужно заменить, а не подгонять эту реализацию
 * под неё задним числом.
 *
 * **Ступень выбирается по ПОКАЗАННОМУ числу, а не по сырому значению**, и это
 * не экономия: ячейка, на которой написано «+10&nbsp;%», никогда не окрашена, а
 * «+11&nbsp;%» окрашена всегда — цвет не расходится с цифрой, которую читает
 * человек. Отсюда и разбор величины из текста `roundDecimalPercent`: он строит
 * его из `BigInt` без разделителей разрядов, поэтому цифры вынимаются
 * однозначно; появись там группировка, эту связь придётся пересмотреть, а не
 * латать регулярку. Несовпадение с `DECIMAL_RE` даёт `sign === 0` и уходит в
 * нейтральную ветку до разбора — мусорная строка не окрасит ячейку.
 */
const NEUTRAL_BAND_PCT = 10;
const HIGH_BAND_PCT = 30;

type DeviationTone = "flat" | "up-lo" | "up-hi" | "dn-lo" | "dn-hi";

function deviationTone(value: string | null): { text: string; tone: DeviationTone } | null {
  const rounded = roundDecimalPercent(value, 0);
  if (rounded === null) return null;
  const magnitude = Number.parseInt(rounded.text.replace(/[^0-9]/g, ""), 10);
  if (rounded.sign === 0 || magnitude <= NEUTRAL_BAND_PCT) {
    return { text: rounded.text, tone: "flat" };
  }
  const hi = magnitude > HIGH_BAND_PCT;
  return {
    text: rounded.text,
    tone: rounded.sign > 0 ? (hi ? "up-hi" : "up-lo") : hi ? "dn-hi" : "dn-lo",
  };
}

/**
 * Тон говорит «выше/ниже медианы», НЕ «плохо/хорошо» (спека §2.5, правило 7)
 * — само по себе сочетание цветов этого не скажет, поэтому `title` рядом
 * произносит это словами (см. `ComparisonPerSqmCell`), а не только легенда
 * под таблицей.
 */
const DEVIATION_TONE_CLASS: Record<DeviationTone, string> = {
  flat: "text-fg-tertiary",
  "up-lo": "bg-warning-soft text-warning-text",
  "up-hi": "bg-warning-soft text-warning-text font-semibold",
  "dn-lo": "bg-accent-soft text-accent-text",
  "dn-hi": "bg-accent-soft text-accent-text font-semibold",
};

function ComparisonPerSqmCell({
  rowCode,
  contractId,
  cell,
}: {
  rowCode: string;
  contractId: number;
  cell: ComparisonBucketCell;
}) {
  const deviation = deviationTone(cell.deviation_pct);
  return (
    <div className="flex items-baseline justify-end gap-1.5">
      {cell.shown_per_sqm === null ? (
        <span className="text-fg-tertiary">—</span>
      ) : (
        <MoneyCell value={cell.shown_per_sqm} maxFractionDigits={2} />
      )}
      {deviation && (
        <span
          data-testid={`comparison-deviation-${rowCode}-${contractId}`}
          title="Отклонение от медианы «выше/ниже», а НЕ «плохо/хорошо»: дешевле может быть недобором объёма (спека §2.5)."
          className={cn("rounded px-1 text-2xs tabular-nums", DEVIATION_TONE_CLASS[deviation.tone])}
        >
          {deviation.text}
        </span>
      )}
    </div>
  );
}

/**
 * Строка называет отсутствие подсветки словами (спека §2.5, правило 5),
 * а не молчит: меньше трёх сопоставимых значений — медиана не считается.
 * «Нераспределённое» в медиану не входит вовсе (§2.1.4) — заметка о ней
 * означала бы то, чего у остатка нет и быть не может.
 */
function NoMedianNote({ row, bucket }: { row: ComparisonRow; bucket: ComparisonBucket }) {
  if (row.kind === "unallocated") return null;
  if (row.medians[bucket].comparable_count >= 3) return null;
  return (
    <span
      data-testid={`comparison-nomedian-${row.code}`}
      title="Сопоставимых значений меньше трёх — медиана не считается, подсветки в строке нет (спека §2.5, правило 5)."
      className="ml-1.5 text-2xs text-fg-tertiary"
    >
      меньше трёх сопоставимых
    </span>
  );
}

// ---------------------------------------------------------------------------
//  Шапка колонки (спека §2.1 «Шапка колонки»)
// ---------------------------------------------------------------------------

/** Отсутствующие условия называются словами, а не прочерком в пустоте (спека §2.1). */
function commercialTermsText(column: ComparisonColumn): string {
  const parts: string[] = [];
  if (column.advance_pct !== null) parts.push(`аванс ${formatSharePercent(column.advance_pct)}`);
  if (column.bank_guarantee_pct !== null) {
    parts.push(`БГ ${formatSharePercent(column.bank_guarantee_pct)}`);
  }
  if (column.retention_pct !== null) parts.push(`удерж. ${formatSharePercent(column.retention_pct)}`);
  return parts.length > 0 ? parts.join(" · ") : "условия не заведены";
}

function ComparisonColumnHeader({
  column,
  vatMode,
}: {
  column: ComparisonColumn;
  vatMode: ComparisonVatMode;
}) {
  return (
    <div className="min-w-0">
      <div className="text-sm font-semibold text-fg">{column.contract_number}</div>
      <div className="text-2xs text-fg-secondary">{column.object_title}</div>
      <div className="text-2xs text-fg-tertiary">подписан {formatDate(column.signed_date)}</div>
      <div className="mt-1 text-2xs text-fg-secondary">{column.rate_class_title}</div>
      <div className="text-2xs text-fg-secondary">
        {column.area_total_sp === null ? (
          <span className="text-warning-text">ТЭП не заведены</span>
        ) : (
          <>
            <MoneyCell value={column.area_total_sp} currency="" /> м²
          </>
        )}
      </div>
      <div className="text-2xs text-fg-tertiary">{commercialTermsText(column)}</div>
      {/*
        Состав печатается ТОЛЬКО в режиме «своя ставка» — вне его у выборки
        одна общая ось показа (единая ставка либо нетто), и подпись состава
        колонки не отвечает ни на один вопрос читателя в этом режиме
        (AGENTS.md §10 v6.8, спека §2.3.2).
      */}
      {vatMode === "own" && (
        <div
          data-testid={`comparison-composition-${column.contract_id}`}
          className="text-2xs text-fg-tertiary"
        >
          {column.composition_caption}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
//  Строка дерева (рекурсивно)
// ---------------------------------------------------------------------------

function ComparisonRowGroup({
  node,
  depth,
  expandedCodes,
  onToggle,
  bucket,
  columns,
}: {
  node: ComparisonTreeNode;
  depth: number;
  expandedCodes: Set<string>;
  onToggle: (code: string) => void;
  bucket: ComparisonBucket;
  columns: ComparisonColumn[];
}) {
  const { row, children } = node;
  const expandable = children.length > 0;
  const isOpen = expandedCodes.has(row.code);
  const indent = 8 + depth * 20;
  const byContract = useMemo(
    () => new Map(row.cells.map((cell) => [cell.contract_id, cell] as const)),
    [row.cells]
  );

  return (
    <>
      <tr
        data-testid={`comparison-row-${row.code}`}
        className={cn(
          depth === 0 && "border-t border-border-subtle",
          row.kind === "unallocated" && "bg-surface-sunken"
        )}
      >
        <th
          scope="row"
          className="sticky left-0 z-10 min-w-[22rem] max-w-[22rem] bg-surface px-3 py-2 text-left align-top font-normal"
        >
          <div className="flex min-w-0 items-start gap-1.5" style={{ paddingLeft: indent }}>
            {expandable ? (
              <button
                type="button"
                aria-expanded={isOpen}
                aria-label={`${isOpen ? "Свернуть" : "Развернуть"} статью ${row.code}`}
                onClick={() => onToggle(row.code)}
                className="mt-0.5 shrink-0 text-fg-tertiary hover:text-fg"
              >
                <ChevronRight
                  aria-hidden="true"
                  className={cn("size-3.5 transition-transform", isOpen && "rotate-90")}
                />
              </button>
            ) : (
              <span className="mt-0.5 inline-block size-3.5 shrink-0" aria-hidden="true" />
            )}
            <div className="min-w-0">
              <span
                className={cn(
                  "break-words",
                  depth === 0 && row.kind === "category" && "font-semibold text-fg",
                  row.kind === "own" && "italic text-fg-secondary"
                )}
              >
                {row.kind === "category" && (
                  <span className="mr-1.5 font-mono text-2xs text-fg-tertiary">{row.code}</span>
                )}
                {row.title}
              </span>
              <NoMedianNote row={row} bucket={bucket} />
            </div>
          </div>
        </th>
        {columns.map((column) => {
          const cell = byContract.get(column.contract_id);
          const bucketCell = cell?.[bucket];
          return (
            <Fragment key={column.contract_id}>
              <td
                data-testid={`comparison-cell-${row.code}-${column.contract_id}`}
                className="border-l border-border-subtle px-3 py-2 text-right tabular-nums"
              >
                {bucketCell ? (
                  <ComparisonAmountCell cell={bucketCell} />
                ) : (
                  <span className="text-fg-tertiary">—</span>
                )}
              </td>
              <td
                data-testid={`comparison-persqm-${row.code}-${column.contract_id}`}
                className="px-3 py-2 text-right tabular-nums"
              >
                {bucketCell ? (
                  <ComparisonPerSqmCell rowCode={row.code} contractId={column.contract_id} cell={bucketCell} />
                ) : (
                  <span className="text-fg-tertiary">—</span>
                )}
              </td>
            </Fragment>
          );
        })}
      </tr>
      {isOpen &&
        children.map((child) => (
          <ComparisonRowGroup
            key={child.row.code}
            node={child}
            depth={depth + 1}
            expandedCodes={expandedCodes}
            onToggle={onToggle}
            bucket={bucket}
            columns={columns}
          />
        ))}
    </>
  );
}

/** «Итого по договору» — та же форма ячейки, что у обычной строки, отдельная строка данных (спека §2.1.4: остаток входит сюда). */
function ComparisonTotalsRow({
  comparison,
  bucket,
}: {
  comparison: Comparison;
  bucket: ComparisonBucket;
}) {
  const byContract = useMemo(
    () => new Map(comparison.totals.map((cell) => [cell.contract_id, cell] as const)),
    [comparison.totals]
  );
  return (
    <tr data-testid="comparison-row-totals" className="border-t-2 border-fg bg-surface-sunken font-semibold">
      <th scope="row" className="sticky left-0 z-10 bg-surface-sunken px-3 py-2 text-left align-top">
        Итого по договору
      </th>
      {comparison.columns.map((column) => {
        const cell = byContract.get(column.contract_id);
        const bucketCell = cell?.[bucket];
        return (
          <Fragment key={column.contract_id}>
            <td
              data-testid={`comparison-cell-totals-${column.contract_id}`}
              className="border-l border-border-subtle px-3 py-2 text-right tabular-nums"
            >
              {bucketCell ? <ComparisonAmountCell cell={bucketCell} /> : <span className="text-fg-tertiary">—</span>}
            </td>
            <td
              data-testid={`comparison-persqm-totals-${column.contract_id}`}
              className="px-3 py-2 text-right tabular-nums"
            >
              {bucketCell ? (
                <ComparisonPerSqmCell rowCode="totals" contractId={column.contract_id} cell={bucketCell} />
              ) : (
                <span className="text-fg-tertiary">—</span>
              )}
            </td>
          </Fragment>
        );
      })}
    </tr>
  );
}

// ---------------------------------------------------------------------------
//  Страница
// ---------------------------------------------------------------------------

export default function ComparePage() {
  const [searchParams, setSearchParams] = useSearchParams();

  // Параметры выборки — читаются из URL и лишь ПЕРЕДАЮТСЯ серверу (спека
  // §2.6): эта страница их не редактирует, их собрал `ContractsPage`.
  const idsParam = searchParams.get("ids") ?? undefined;
  const allParam = searchParams.get("all") ?? undefined;
  const qParam = searchParams.get("q") ?? undefined;
  const objectIdParam = searchParams.get("object_id") ?? undefined;
  const contractorIdParam = searchParams.get("contractor_id") ?? undefined;
  const rateClassIdParam = searchParams.get("rate_class_id") ?? undefined;

  const vatMode = (searchParams.get("vat_mode") as ComparisonVatMode | null) ?? "own";
  const singleRateParam = searchParams.get("single_rate") ?? undefined;

  const [bucket, setBucket] = useState<ComparisonBucket>("total");
  const [expandedCodes, setExpandedCodes] = useState<Set<string>>(() => new Set());

  const hasSelection = Boolean(idsParam) || allParam === "1";

  const params = useMemo<ComparisonParams>(
    () => ({
      ids: idsParam,
      all: allParam,
      q: qParam,
      object_id: objectIdParam,
      contractor_id: contractorIdParam,
      rate_class_id: rateClassIdParam,
      vat_mode: vatMode,
      single_rate: vatMode === "single" ? singleRateParam : undefined,
    }),
    [idsParam, allParam, qParam, objectIdParam, contractorIdParam, rateClassIdParam, vatMode, singleRateParam]
  );

  const comparisonQ = useComparison(params, hasSelection);
  const comparison = comparisonQ.data;

  /*
    Выгрузка листа — ТРЕТИЙ отчёт §7.6, и кнопка ему нужна именно здесь.
    Экран отчётов дать выборку не может: там период и класс, а сравнению нужен
    НАБОР договоров (§2.6). План не назначил эту кнопку ни одной задаче —
    эндпоинт сделала задача 6, страницу задача 8, — и без неё третий отчёт
    остался бы недостижим из интерфейса. Параметры передаются ТЕ ЖЕ, что у
    экрана, включая режим НДС: лист обязан отвечать на тот же вопрос, что
    открытая страница (§2.7, «один агрегат — два представления»).
  */
  const exportM = useComparisonReport();

  const tree = useMemo(() => buildComparisonTree(comparison?.rows ?? []), [comparison?.rows]);

  function toggle(code: string) {
    setExpandedCodes((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  }

  /** Правит `vat_mode`/`single_rate` в адресе, сохраняя выборку (спека §2.3, DoD 18). */
  function updateVatMode(nextMode: ComparisonVatMode) {
    const next = new URLSearchParams(searchParams);
    next.set("vat_mode", nextMode);
    if (nextMode !== "single") next.delete("single_rate");
    setSearchParams(next, { replace: true });
  }

  function updateSingleRate(rate: string) {
    const next = new URLSearchParams(searchParams);
    next.set("vat_mode", "single");
    next.set("single_rate", rate);
    setSearchParams(next, { replace: true });
  }

  if (!hasSelection) {
    return (
      <div className="container-page py-8">
        <PageHeader serif title="Сравнение договоров" />
        <EmptyState
          className="mt-6"
          title="Выборка не задана"
          description="Откройте сравнение из списка договоров: отметьте галочками нужные либо нажмите «Сравнить всё по фильтру»."
        />
      </div>
    );
  }

  return (
    <div className="container-page py-8">
      <PageHeader
        serif
        title="Сравнение договоров"
        subtitle="Строки — статьи классификатора, колонки — договоры выборки. Тот же свод, что в паспорте объекта, только на несколько договоров сразу."
        actions={
          <Button
            variant="outline"
            disabled={!comparison || exportM.isPending}
            onClick={() => exportM.mutate(params)}
          >
            {exportM.isPending ? "Готовим файл…" : "Выгрузить в Excel"}
          </Button>
        }
      />

      {comparisonQ.isPending && <Skeleton className="mt-6 h-64 w-full" />}

      {comparisonQ.isError && (
        <EmptyState
          className="mt-6"
          title="Не удалось загрузить сравнение"
          description="Обновите страницу или проверьте выборку в адресе."
        />
      )}

      {comparison && (
        <>
          {/*
            Подпись налогового состава денег (AGENTS.md §10 v6.8) —
            печатается на поверхности, а не только в подсказке: тултип рядом
            с ячейками объясняет расчёт, но не заменяет объявление состава.
          */}
          <p data-testid="comparison-caption" className="mt-4 text-sm text-fg-secondary">
            {comparison.caption}
          </p>

          <div className="mt-4 flex flex-wrap items-center gap-4">
            <div className="flex items-center gap-2">
              <span className="text-2xs font-semibold tracking-wide text-fg-tertiary uppercase">
                Показатель
              </span>
              <div
                role="group"
                aria-label="Показатель"
                className="inline-flex overflow-hidden rounded-lg border border-border"
              >
                {(Object.keys(BUCKET_LABELS) as ComparisonBucket[]).map((value) => (
                  <Button
                    key={value}
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-pressed={bucket === value}
                    className={cn("rounded-none", bucket === value && "bg-accent-soft text-accent-text")}
                    onClick={() => setBucket(value)}
                  >
                    {BUCKET_LABELS[value]}
                  </Button>
                ))}
              </div>
            </div>

            <div className="flex items-center gap-2">
              <span className="text-2xs font-semibold tracking-wide text-fg-tertiary uppercase">
                НДС
              </span>
              <div
                role="group"
                aria-label="Режим НДС"
                className="inline-flex overflow-hidden rounded-lg border border-border"
              >
                {(Object.keys(VAT_MODE_LABELS) as ComparisonVatMode[]).map((mode) => (
                  <Button
                    key={mode}
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-pressed={vatMode === mode}
                    className={cn("rounded-none", vatMode === mode && "bg-accent-soft text-accent-text")}
                    onClick={() => updateVatMode(mode)}
                  >
                    {VAT_MODE_LABELS[mode]}
                  </Button>
                ))}
              </div>

              <Select
                value={vatMode === "single" ? (singleRateParam ?? comparison.single_rate ?? "") : ""}
                onValueChange={(value) => {
                  if (value) updateSingleRate(value);
                }}
              >
                <SelectTrigger aria-label="Единая ставка" disabled={vatMode !== "single"} className="w-28">
                  <SelectValue>{(raw) => (raw ? formatSharePercent(raw) : "ставка")}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {comparison.rate_options.map((rate) => (
                    <SelectItem key={rate} value={rate}>
                      {formatSharePercent(rate)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {/*
            Скролл в обе стороны сразу, первая колонка закреплена (DoD 17) —
            тот же приём, что у `MatrixPage` (§6.4). `position: sticky` не
            вычисляется в jsdom (см. докстроку теста) — здесь только класс.
          */}
          <div className="mt-4 overflow-x-auto rounded-lg border border-border-subtle bg-surface">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr>
                  {/*
                    ВНИМАНИЕ (грабли мокапа): `display:flex` НЕ ставится на
                    сам `th` — этот `th` несёт `rowSpan`, и flex выбил бы его
                    из табличного контекста, молча сломав rowSpan/colSpan.
                  */}
                  <th
                    rowSpan={2}
                    scope="col"
                    className="sticky left-0 z-20 min-w-[22rem] max-w-[22rem] border-b border-border-subtle bg-surface-sunken px-3 py-2 text-left align-bottom text-xs font-medium text-fg-secondary"
                  >
                    Статья классификатора
                  </th>
                  {comparison.columns.map((column) => (
                    <th
                      key={column.contract_id}
                      colSpan={2}
                      scope="col"
                      className="border-b border-l border-border-subtle bg-surface-sunken px-3 py-2 text-left align-top"
                    >
                      <ComparisonColumnHeader column={column} vatMode={vatMode} />
                    </th>
                  ))}
                </tr>
                <tr>
                  {comparison.columns.map((column) => (
                    <Fragment key={column.contract_id}>
                      <th
                        scope="col"
                        className="border-l border-border-subtle bg-surface-sunken px-3 py-1.5 text-right text-2xs font-medium tracking-wide text-fg-tertiary uppercase"
                      >
                        Сумма
                      </th>
                      <th
                        scope="col"
                        className="bg-surface-sunken px-3 py-1.5 text-right text-2xs font-medium tracking-wide text-fg-tertiary uppercase"
                      >
                        за м²
                      </th>
                    </Fragment>
                  ))}
                </tr>
              </thead>
              <tbody>
                {tree.map((node) => (
                  <ComparisonRowGroup
                    key={node.row.code}
                    node={node}
                    depth={0}
                    expandedCodes={expandedCodes}
                    onToggle={toggle}
                    bucket={bucket}
                    columns={comparison.columns}
                  />
                ))}
                <ComparisonTotalsRow comparison={comparison} bucket={bucket} />
              </tbody>
            </table>
          </div>

          <div className="mt-4 rounded-lg border border-border-subtle bg-surface p-4 text-xs text-fg-secondary">
            <p className="font-medium text-fg">Как читать подсветку</p>
            <ul className="mt-1.5 list-disc space-y-1 pl-4">
              <li>Отклонение считается по ₽/м² без НДС, в любом режиме показа (спека §2.5, правило 2).</li>
              <li>Полоса ±10&nbsp;% нейтральна, дальше — две ступени интенсивности.</li>
              <li>
                Цвет означает «выше/ниже медианы», а не «плохо/хорошо»: дешевле может быть недобором
                объёма.
              </li>
              <li>
                Пустые и нулевые ячейки в медиану не входят; строки с числом сопоставимых меньше трёх
                подсветки не получают — это названо рядом со строкой, а не скрыто молча.
              </li>
            </ul>
          </div>
        </>
      )}
    </div>
  );
}
