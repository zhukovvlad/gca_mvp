import { Fragment, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ChevronRight } from "lucide-react";

import { EmptyState } from "@/components/ui-domain/EmptyState";
import { MoneyCell } from "@/components/ui-domain/MoneyCell";
import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Surface } from "@/components/ui-domain/Surface";
import {
  CONTROLS_ROW_CLASS,
  CONTROL_CELL_CLASS,
  CONTROL_LABEL_CLASS,
  SEGMENTED_GROUP_CLASS,
  SEGMENTED_ITEM_ACTIVE_CLASS,
  SEGMENTED_ITEM_CLASS,
} from "@/components/ui-domain/controlStyles";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatDate, formatSharePercent } from "@/lib/format";
import { cn } from "@/lib/utils";
import { InflationControls } from "@/components/inflation/InflationControls";
import { InflationLevelsBar } from "@/components/inflation/InflationLevelsBar";
import {
  AMENDMENT_DATE_CODE,
  InflationRefusalBanner,
  MISSING_YEARS_CODE,
} from "@/components/inflation/InflationRefusalBanner";
import {
  InflationSeriesDialog,
  type InflationSeriesTarget,
} from "@/components/inflation/InflationSeriesDialog";
import { useCurrentUser } from "@/hooks/useAuth";
import { coefficientLevel } from "@/lib/inflation";
import { ContractCostChart } from "./ContractCostChart";
import { BUCKET_LABELS, REASON_LABELS, VAT_MODE_LABELS } from "./labels";
import {
  apiErrorCode,
  apiErrorContext,
  apiErrorDetail,
  useComparison,
  useComparisonReport,
  useInflationSeries,
} from "@/services/queries";
import { type DeviationTone, deviationTone } from "./deviationTone";
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
  /*
    `maxFractionDigits={2}` ОБЯЗАТЕЛЕН, и это не косметика. Агрегат намеренно НЕ
    квантует деньги: округли он три корзины по отдельности, и `ДГП + ДС = Итого`
    (DoD 5) разошлось бы на копейку, потому что `Σ round(x) ≠ round(Σ x)`
    (`money/vat.py`). Значит суммы приходят точными: нетто — частное от
    `gross_to_net`, а оно почти никогда не представимо конечной дробью. Без
    округления ЗДЕСЬ экран печатает то, что показал прогон на стенде:
    «14 011 951 126,949999999999999982 ₽» — восемнадцать знаков, изображающих
    точность, которой нет.

    Условие `MoneyCell` соблюдено: параметр разрешён «только для ВЫЧИСЛЕННЫХ
    величин», и это ровно такая — сумма частных, а не хранимая ставка. Точное
    значение уходит в `title` и остаётся доступным.

    Ловилось только замером: в фикстурах стояли круглые суммы, и весь набор
    vitest молчал. Тот же класс, что уже оплачен средневзвешенной ставкой
    матрицы (докстрока `MoneyCell`).
  */
  return <MoneyCell value={cell.shown} maxFractionDigits={2} />;
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
  /*
    Бейдж отклонения НЕ рисуется на визуально пустой ячейке (§2.5 правило 3:
    «пустые ячейки не входят ни в медиану, ни в подсветку»). Случай достижим
    ровно один: режим «своя ставка» и `display_rate_undefined` — агрегат
    осознанно гасит только показ, оставляя `net_per_sqm` и `deviation_pct`
    живыми, потому что медиана считается по нетто в любом режиме (DoD 10).
    Оставь бейдж — и рядом с прочерком стоял бы цветной «+33 %»: цвет без
    числа, то есть та самая «видимость анализа без анализа», которой спека
    боится в правиле 5.

    Подавление — ЧИСТО показ: ни медиана, ни отклонения соседних колонок не
    меняются, поэтому резолюция противоречия (devlog §5.6) остаётся в силе.
    Найдено финальным ревью ветки; закреплено фронтенд-тестом с подменой ответа.
  */
  const deviation = cell.shown_per_sqm === null ? null : deviationTone(cell.deviation_pct);
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
      <InflationChip column={column} />
    </div>
  );
}

/**
 * Чип приведения в шапке колонки (спека §2.12, DoD 33).
 *
 * Коэффициент показан УРОВНЕМ (`+17,4 %`), множитель — в подсказке: человек думает
 * в процентах инфляции, а множитель нужен, чтобы арифметику можно было проверить,
 * но читается он хуже.
 *
 * `inflation_coefficient === null` означает «сметы договора приведены РАЗНЫМИ
 * множителями»; тогда в подсказке лежит их перечень из `inflation_factors`. Чип
 * без разбивки отправлял бы читателя смотреть корзины, а корзины множителей не
 * показывают — арифметика колонки перестала бы быть проверяемой.
 *
 * Ключей нет вовсе, если приведение не считалось ЛИБО у колонки нет смет: у пустого
 * договора множителя не существует, и «разные» о нём было бы неправдой.
 */
function InflationChip({ column }: { column: ComparisonColumn }) {
  if (!("inflation_coefficient" in column)) return null;

  const coefficient = column.inflation_coefficient ?? null;
  if (coefficient !== null) {
    const level = coefficientLevel(coefficient);
    return (
      <div
        data-testid={`inflation-chip-${column.contract_id}`}
        title={`множитель × ${coefficient}`}
        className="mt-1 inline-block rounded bg-accent-soft px-1.5 text-2xs text-accent-text"
      >
        {level.level}
      </div>
    );
  }

  const factors = column.inflation_factors ?? [];
  return (
    <div
      data-testid={`inflation-chip-${column.contract_id}`}
      title={factors.map((factor) => `${factor.label} × ${factor.coefficient}`).join(" · ")}
      className="mt-1 inline-block rounded bg-surface-sunken px-1.5 text-2xs text-fg-secondary"
    >
      разные
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
        {/*
          Заметка об отсутствии медианы нужна и здесь: итоговая строка получает
          медиану наравне с прочими (она отвечает на вопрос «который объект дороже
          в целом»), а значит и правило §2.5 п.5 к ней применимо. Прежде заметки
          не было только у неё — несогласованность показа, замеченная финальным
          ревью: строка молча оставалась без подсветки.
        */}
        {comparison.totals_medians[bucket].comparable_count < 3 && (
          <span
            data-testid="comparison-nomedian-totals"
            title="Сопоставимых значений меньше трёх — медиана не считается, подсветки в строке нет (спека §2.5, правило 5)."
            className="ml-1.5 text-2xs font-normal text-fg-tertiary"
          >
            меньше трёх сопоставимых
          </span>
        )}
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

  // Приведение живёт в URL: ссылка воспроизводит ПАРАМЕТРЫ расчёта (§2.10).
  const seriesIdParam = searchParams.get("inflation_series_id") ?? undefined;
  const targetMonthParam = searchParams.get("target_month") ?? undefined;

  const [bucket, setBucket] = useState<ComparisonBucket>("total");
  const [expandedCodes, setExpandedCodes] = useState<Set<string>>(() => new Set());

  /*
    Выбранный ряд — состояние СТРАНИЦЫ, а не URL, пока приведение не включено:
    §2.12 требует, чтобы выбор ряда сам числа не менял и в адрес не попадал.
    Начальное значение берётся из URL — прямое открытие ссылки обязано показать
    выбранный ряд в селекторе, даже если приведение по нему отказало.
  */
  const [selectedSeriesId, setSelectedSeriesId] = useState<number | null>(
    seriesIdParam ? Number(seriesIdParam) : null
  );
  const [editingSeries, setEditingSeries] = useState<number | null>(null);
  const [dialogMissingYears, setDialogMissingYears] = useState<number[] | undefined>(undefined);

  const { data: currentUser } = useCurrentUser();
  const canEditSeries = currentUser?.role === "admin";
  /*
    Список запрашивается С АРХИВНЫМИ, а «не предлагаются для нового выбора» (§2.10)
    делает уже сам селектор, отбрасывая неактивные из ОПЦИЙ.

    Причина не в экономии запроса. Архивный ряд по прямой ссылке — поддержанный
    путь (DoD 20): приведение по нему считается, полоса уровней рисуется. Пока
    список шёл без архивных, `find` по нему возвращал `undefined`, а `undefined ??
    null` в контракте окна означает РЕЖИМ СОЗДАНИЯ: клик admin'а по «Изменить ряд»
    открывал «Новый ряд индексов» с пустыми полями, и человек, думая что правит,
    заводил дубликат либо упирался в 409 по занятому имени. Найдено финальным ревью
    ветки.

    С архивными в списке окно получает настоящий объект с `is_active: false` и
    показывает своё же состояние «сначала верните в активные» с выключенным
    сохранением.
  */
  const seriesListQ = useInflationSeries(true);

  const hasSelection = Boolean(idsParam) || allParam === "1";

  /*
    Выбранные классы объекта читаются из адреса (спека диаграммы стоимости
    §2.6, §2.7): параметра `rate_class_id` НЕТ — выбраны ВСЕ, и это кодируется
    значением `null`, а не пустым множеством — «параметра нет» и «выбрано
    пусто» РАЗНЫЕ состояния, различие несёт форма кода, а не дисциплина
    (docs/insights/one-value-two-states.md). Пустой `rate_class_id=` сервер
    отвергает 400-м, и клиент его не пишет никогда (см. `toggleRateClass`).
  */
  const selectedRateClassIds = useMemo<Set<number> | null>(() => {
    if (rateClassIdParam === undefined) return null;
    return new Set(rateClassIdParam.split(",").map(Number));
  }, [rateClassIdParam]);

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
      inflation_series_id: seriesIdParam,
      target_month: targetMonthParam,
    }),
    [
      idsParam, allParam, qParam, objectIdParam, contractorIdParam, rateClassIdParam,
      vatMode, singleRateParam, seriesIdParam, targetMonthParam,
    ]
  );

  const comparisonQ = useComparison(params, hasSelection);

  /*
    ОТКАЗ ПРИВЕДЕНИЯ: экран показывает НОМИНАЛЬНЫЙ вариант с баннером, а параметры
    URL сохраняет (§2.9) — пользователь обязан видеть, какой ряд и какая цель не
    сработали, чтобы заполнить недостающие годы.

    Номинальный запрос идёт ВТОРЫМ и только при отказе: делать его всегда значило бы
    удваивать нагрузку ради случая, который на исправном ряде не наступает.
  */
  const refusalCode = apiErrorCode(comparisonQ.error);
  const refused =
    refusalCode === MISSING_YEARS_CODE || refusalCode === AMENDMENT_DATE_CODE;
  const nominalParams = useMemo<ComparisonParams>(
    () => ({ ...params, inflation_series_id: undefined, target_month: undefined }),
    [params]
  );
  const nominalQ = useComparison(nominalParams, hasSelection && refused);

  const comparison = refused ? nominalQ.data : comparisonQ.data;
  /*
    Номинальный запрос — тоже запрос, и он тоже умеет падать. Кодированного отказа
    он вернуть не может (инфляционных параметров в нём нет), поэтому его ошибка
    всегда «неизвестный сбой», и состояние ей нужно ОТДЕЛЬНОЕ от общего EmptyState:
    причина отказа приведения при этом известна и названа баннером.
  */
  const nominalFailed = refused && nominalQ.isError;

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

  /*
    ДЕЙСТВУЮЩЕЕ множество выбранных классов — одно на весь экран: по нему и
    рисуются чипы, и решает `toggleRateClass`. Один предикат вместо двух: два
    независимых «выбран ли класс» однажды разошлись бы, а расхождение выглядело
    бы как нажатый чип, который не снимается, либо ненажатый, который снимается.

    Адрес СЕЧЁТСЯ с фасетом, а не берётся как есть. Фасет считается по выборке
    (спека диаграммы стоимости §2.7), поэтому присланная ссылка от ДРУГОЙ
    выборки может называть класс, которого в этом фасете нет. Лишний id рядом с
    настоящим на результат не влияет — сужать им нечего, — но в СЧЁТЕ выбранных
    он участвовал бы, и защита DoD 30 обходилась бы: `rate_class_id=1,9`
    считался бы двумя выбранными, снятие единственного видимого чипа проходило
    бы, и в адресе оставалось бы `9`, то есть выборка из нуля договоров.
    Замерено. Сечение заодно делает адрес каноническим: фантомный id уходит при
    первой же записи, а не живёт в ссылке дальше.
  */
  const effectiveRateClassIds = useMemo<Set<number>>(() => {
    const allIds = comparison?.available_rate_classes.map((rateClass) => rateClass.id) ?? [];
    if (selectedRateClassIds === null) return new Set(allIds);
    return new Set(allIds.filter((classId) => selectedRateClassIds.has(classId)));
  }, [comparison?.available_rate_classes, selectedRateClassIds]);

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

  /**
   * Переключает чип класса объекта (спека диаграммы стоимости §2.6, §2.7).
   * Фильтр — перезапрос, а не скрытие столбцов: медиана и отклонения обязаны
   * пересчитаться сервером по суженной выборке, значит клик всегда правит
   * адрес, а не локальное состояние страницы.
   *
   * Снятие ПОСЛЕДНЕГО выбранного класса не срабатывает (DoD 30): выборка из
   * нуля договоров — не состояние экрана, а отсутствие запроса, контрол не
   * срабатывает.
   *
   * Запись — тем же приёмом, что `updateVatMode`/`updateSingleRate`: id по
   * возрастанию (одна выборка — один адрес, независимо от порядка чипов), а
   * при выборе ВСЕХ доступных классов параметр из адреса ИСЧЕЗАЕТ (DoD 31) —
   * ссылка становится посимвольно той же, что до фичи.
   */
  function toggleRateClass(id: number) {
    if (!comparison) return;
    const allIds = comparison.available_rate_classes.map((rateClass) => rateClass.id);
    const current = effectiveRateClassIds;
    if (current.has(id) && current.size === 1) return; // DoD 30

    const next = new Set(current);
    if (next.has(id)) next.delete(id);
    else next.add(id);

    const nextParams = new URLSearchParams(searchParams);
    const isFullSet = allIds.length === next.size && allIds.every((classId) => next.has(classId));
    if (isFullSet) {
      nextParams.delete("rate_class_id");
    } else {
      nextParams.set(
        "rate_class_id",
        Array.from(next)
          .sort((a, b) => a - b)
          .join(",")
      );
    }
    setSearchParams(nextParams, { replace: true });
  }

  /**
   * Включение и выключение приведения — тем же приёмом, что `updateVatMode`.
   *
   * Месяц в URL НЕ пишется при включении: его разрешает сервер в названной
   * таймзоне и возвращает в ответе (§2.7), после чего эффект ниже кладёт его в
   * адрес. `Date.now()` на клиенте не используется — это часы читателя, и два
   * человека получили бы два ответа.
   */
  function toggleInflation(enabled: boolean) {
    const next = new URLSearchParams(searchParams);
    if (enabled && selectedSeriesId !== null) {
      next.set("inflation_series_id", String(selectedSeriesId));
    } else {
      // Возврат к номиналу ЧИСТИТ адрес: оставленные параметры означали бы, что
      // приведение всё ещё выбрано, при номинальных числах на экране.
      next.delete("inflation_series_id");
      next.delete("target_month");
    }
    setSearchParams(next, { replace: true });
  }

  function selectSeries(id: number | null) {
    setSelectedSeriesId(id);
    const next = new URLSearchParams(searchParams);
    if (id === null) {
      // Сброс ряда в placeholder выключает приведение: приводить стало нечем.
      next.delete("inflation_series_id");
      next.delete("target_month");
      setSearchParams(next, { replace: true });
      return;
    }
    /*
      Если приведение УЖЕ включено, смена ряда применяется немедленно — так решает
      согласованный макет (§7 спеки, замер «смена ряда меняет числа, а не только
      подпись»). Прежняя редакция оставляла адрес нетронутым, и жило состояние «в
      селекторе один ряд, на всей остальной поверхности другой»: применить новый
      можно было только повторным кликом по уже нажатой «Привести», а нажатая
      кнопка к клику не приглашает. Таблица состояний §2.12 этот переход не
      описывает — решает макет. Найдено финальным ревью ветки.

      Целевой месяц СОХРАНЯЕТСЯ: его выбрал человек либо разрешил сервер, и
      сбрасывать его при смене ряда значило бы терять его решение. Если у нового
      ряда нужных годов нет — придёт штатный отказ с перечнем.
    */
    if (seriesIdParam) {
      next.set("inflation_series_id", String(id));
      setSearchParams(next, { replace: true });
    }
  }

  function updateTargetMonth(month: string) {
    const next = new URLSearchParams(searchParams);
    if (month) next.set("target_month", month);
    else next.delete("target_month");
    if (selectedSeriesId !== null) next.set("inflation_series_id", String(selectedSeriesId));
    setSearchParams(next, { replace: true });
  }

  /*
    Разрешённый сервером месяц клиент немедленно записывает в URL (§2.12): без этого
    ссылка воспроизводила бы «текущий месяц», то есть меняла бы числа со временем.
    Условие сравнивает с тем, что уже в адресе, — иначе эффект переписывал бы адрес
    на каждом рендере.
  */
  const resolvedMonth = comparison?.inflation?.target_month;
  useEffect(() => {
    if (!resolvedMonth || targetMonthParam === resolvedMonth) return;
    const next = new URLSearchParams(searchParams);
    next.set("target_month", resolvedMonth);
    setSearchParams(next, { replace: true });
  }, [resolvedMonth, targetMonthParam, searchParams, setSearchParams]);

  /*
    Действующая ставка показа пишется в адрес ТЕМ ЖЕ приёмом, что месяц
    приведения выше: в режиме «Единая» без явной ставки её предвыбирает
    сервер по составу выборки (`rate_preselected`, спека сравнения §2.3), а
    сужение выборки меняет состав — значит может изменить и предвыбор, и
    числа поехали бы от нажатия на чип класса (спека диаграммы стоимости
    §2.6, подраздел «Ставка показа не имеет права меняться от фильтра»).
    Читается `comparison.single_rate` — ставка, которой числа показаны
    ФАКТИЧЕСКИ, а не то, что ушло в запросе. Условие сравнивает с тем, что
    уже в адресе, — иначе эффект переписывал бы адрес на каждом рендере.

    Отдельного условия на `vatMode` здесь НЕТ, и причина живёт в этом же
    файле, а не на сервере: `params` выше отдаёт `single_rate` ТОЛЬКО в
    режиме «Единая», поэтому в остальных режимах сервер получает `None`,
    подстановку предвыбора не делает и возвращает `null` — эффект молчит по
    условию `!resolvedSingleRate`. Сам сервер ставку не фильтрует: он
    отражает полученную при любом `vat_mode` (`build_comparison`,
    `effective_single_rate`), так что несёт здесь КЛИЕНТСКАЯ проводка, и
    второе условие было бы вторым сторожем той же двери.

    Проводка поэтому закреплена тестом «вне «Единой» клиент ставку серверу не
    отправляет», а не чтением: общий мок сам отдаёт `null` вне «Единой», то
    есть стоит рядом второй защитой и снятие проводки на нём замаскировал бы
    (`docs/insights/verifying-guards.md`, слой 8). Тест поэтому подменяет
    обработчик на отражающий полученную ставку при любом режиме.

    **Предпосылка, от которой зависит этот эффект и эффект месяца выше:
    `comparison` описывает ТЕКУЩИЙ адрес.** Сегодня это так, потому что запрос
    сравнения не отдаёт данных прошлого ключа: при смене ключа `data` пуста, и
    эффект молчит по `!resolvedSingleRate`. Предпосылка не выражена типом и
    держится на настройке запроса — если у `useComparison` появится
    `placeholderData`, эффект начнёт читать кадр ПРОШЛОГО адреса и дописывать в
    новый адрес устаревшее значение, откатить которое ему уже нечем. Замерено:
    такая правка постоянно ломает три утверждения об адресе (DoD 18, выход из
    «Единой», сброс приведения к номиналу) — они и есть сторожа этой
    предпосылки. Подробности и условие безопасного включения —
    `docs/TECH_DEBT.md`, запись 16.
  */
  const resolvedSingleRate = comparison?.single_rate;
  useEffect(() => {
    if (!resolvedSingleRate || singleRateParam === resolvedSingleRate) return;
    const next = new URLSearchParams(searchParams);
    next.set("single_rate", resolvedSingleRate);
    setSearchParams(next, { replace: true });
  }, [resolvedSingleRate, singleRateParam, searchParams, setSearchParams]);

  /*
    Баннер отказа НЕ ЗАВИСИТ ОТ ЧИСЕЛ, и поэтому он — элемент, а не кусок разметки
    внутри блока `comparison &&`. Причина отказа лежит в ответе на ПЕРВЫЙ запрос и
    известна даже тогда, когда номинальный запрос за числами тоже упал. Пока баннер
    жил только вместе с таблицей, эта пара давала страницу с одним заголовком:
    `comparison` пуст, общий EmptyState подавлен условием `!refused`, а скелета нет,
    потому что `nominalQ` не в `isPending`, а в `isError`. Молчание здесь хуже любого
    текста — человек видел заголовок и ничего больше. Найдено внешним ревью.

    Один элемент, ДВЕ точки монтирования, условия взаимоисключающие (`comparison`
    либо есть, либо нет), поэтому в DOM баннер ровно один — это утверждается тестом.
    Место рядом с полосой уровней сохранено намеренно: когда числа показаны, баннер
    объясняет выключенный переключатель, а он стоит там.
  */
  const refusalBanner =
    refused && refusalCode ? (
      <InflationRefusalBanner
        code={refusalCode}
        message={apiErrorDetail(comparisonQ.error) ?? "Причина не названа."}
        seriesName={seriesListQ.data?.find((row) => row.id === selectedSeriesId)?.name}
        missingYears={
          apiErrorContext<{ missing_years?: number[] }>(comparisonQ.error)?.missing_years
        }
        canEdit={Boolean(canEditSeries)}
        onFillMissingYears={(years) => {
          // ТОТ ЖЕ компонент окна, что у полосы и у экрана нормативов, и тот же его
          // экземпляр на этой странице (DoD 34).
          setDialogMissingYears(years);
          setEditingSeries(selectedSeriesId);
        }}
      />
    ) : null;

  /*
    Объект правки и ПРИЧИНА его отсутствия — одно вычисление на одну точку вызова.
    «Не нашли» здесь законно двумя разными способами, и окно обязано различать их:
    запрос списка ещё идёт (`pending`) — либо он упал, либо завершился без этого
    ряда (`failed`), и тогда ждать нечего. Прежде оба уводились в один `null`, и
    упавший список оставлял окно на «Загружаем…» навсегда.
  */
  const editedSeriesRow = seriesListQ.data?.find((row) => row.id === editingSeries) ?? null;

  function buildDialogTarget(): InflationSeriesTarget {
    if (editedSeriesRow) return { mode: "edit", series: editedSeriesRow };
    /*
      Одного `isPending` достаточно и на ПОВТОР после отказа: запрос, ни разу не
      отдавший данных, при новом `fetch` сам возвращается в `pending` с погашенной
      ошибкой (`fetchState()` в `@tanstack/query-core` при `data === undefined`).
      Добавленное сюда `|| isFetching` было мёртвым — снятие не роняло ни одного
      теста; вторая проверка того же факта читалась бы как защита, не будучи ею.
    */
    if (seriesListQ.isPending) return { mode: "edit", series: null, reason: "pending" };
    /*
      Отказ объявляется ВМЕСТЕ со способом его снять. Список рядов принадлежит этой
      странице: окно не может перезапросить его ничем, и закрытие с повторным
      открытием тоже — запрос смонтирован здесь, `refetchOnWindowFocus` выключен, а
      `staleTime` минута. Пока способа не было, окно звало «попробовать снова», не
      имея чем. Найдено третьим кругом ревью.
    */
    return {
      mode: "edit",
      series: null,
      reason: "failed",
      onRetry: () => void seriesListQ.refetch(),
    };
  }

  const dialogTarget = buildDialogTarget();

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

      {(comparisonQ.isPending || (refused && nominalQ.isPending)) && (
        <Skeleton className="mt-6 h-64 w-full" />
      )}

      {/*
        Общий «не загрузилось» — ТОЛЬКО на неизвестной ошибке. Штатный доменный отказ
        приведения ошибкой загрузки не является: сравнение показано, номинальное, и
        причину называет баннер. Прежняя редакция рисовала оба разом, и поверхность
        сама себе противоречила — «не удалось загрузить» над загруженной таблицей.
        Найдено внешним ревью.
      */}
      {comparisonQ.isError && !refused && (
        <EmptyState
          className="mt-6"
          title="Не удалось загрузить сравнение"
          description="Обновите страницу или проверьте выборку в адресе."
        />
      )}

      {/*
        ВТОРАЯ точка монтирования баннера — на случай, когда чисел нет вовсе. Причина
        отказа приведения известна из первого ответа и обязана быть названа, даже
        если номинальный запрос за числами тоже упал. Условие взаимоисключающее с
        точкой внутри блока чисел, поэтому баннер в DOM ровно один.
      */}
      {!comparison && refusalBanner}

      {/*
        Отказ приведения И падение номинального запроса — ДВА разных факта, и второй
        не отменяет первого. Общий EmptyState здесь не годится: он сказал бы «не
        удалось загрузить сравнение», умолчав о том, что приведение отказано штатно и
        по названной причине, — то есть повторил бы дефект второго круга ревью с
        обратным знаком. Кнопка «Заполнить недостающие годы» в баннере выше при этом
        живая: правка ряда перезапросит и сравнение (DoD 36).
      */}
      {nominalFailed && (
        <EmptyState
          className="mt-6"
          title="Номинальные числа получить не удалось"
          description="Причина отказа приведения названа выше, а сами суммы не загрузились. Обновите страницу: выбранные ряд и месяц остались в адресе."
        />
      )}

      {/*
        ОДИН экземпляр окна на всю страницу: и полоса уровней, и баннер отказа
        управляют им, а не заводят каждый свой. Два экземпляра разошлись бы
        состоянием — открытие из баннера обязано давать то же окно, что открытие из
        полосы (DoD 34).
      */}
      <InflationSeriesDialog
        open={editingSeries !== null}
        /*
          Режим ЗДЕСЬ всегда «правка»: и полоса уровней, и баннер отказа открывают
          окно по УЖЕ ВЫБРАННОМУ ряду, создания с этой страницы нет вовсе.

          Объект ряда и причина его отсутствия считаются выше: список рядов идёт
          своим запросом и может ещё не разрешиться либо упасть, когда сравнение уже
          пришло. Прежняя редакция сворачивала это в `?? null`, и такой промах молча
          становился режимом создания: admin, думая что правит ряд, открывал пустую
          форму «Новый ряд индексов». Первое исправление закрыло только случай
          архивного ряда, второе — порядок завершения запросов, а упавший список
          по-прежнему оставлял окно в бесконечной загрузке. Найдено внешним ревью
          трижды, и каждый раз причина была одна: одно значение на два состояния.
        */
        target={dialogTarget}
        missingYears={dialogMissingYears}
        onOpenChange={(open) => {
          if (!open) {
            setEditingSeries(null);
            setDialogMissingYears(undefined);
          }
        }}
      />

      {comparison && (
        <>
          {/*
            Панель управления — ОДНА карточка (макет, `.card.card-pad`). До этого
            ряд корзин, чипы класса и группа поправки лежали прямо на фоне
            страницы тремя отдельными блоками, и ничто не говорило, что это один
            орган управления одной таблицей.

            Порядок внутри — макетный, и он не косметический: сначала ЧТО
            показываем (корзина и налоговый состав), затем НА ЧЁМ (сужение
            выборки классом), затем В КАКИХ ЦЕНАХ (поправка). Поправка стояла
            первой и читалась главным переключателем экрана, хотя выборки она не
            меняет вовсе.
          */}
          <Surface padding="sm" className="mt-4">
            <div className={CONTROLS_ROW_CLASS}>
              <div className={CONTROL_CELL_CLASS}>
                <span className={CONTROL_LABEL_CLASS}>Показатель</span>
                <div role="group" aria-label="Показатель" className={SEGMENTED_GROUP_CLASS}>
                  {(Object.keys(BUCKET_LABELS) as ComparisonBucket[]).map((value) => (
                    <Button
                      key={value}
                      type="button"
                      variant="ghost"
                      size="sm"
                      aria-pressed={bucket === value}
                      className={cn(
                        SEGMENTED_ITEM_CLASS,
                        bucket === value && SEGMENTED_ITEM_ACTIVE_CLASS
                      )}
                      onClick={() => setBucket(value)}
                    >
                      {BUCKET_LABELS[value]}
                    </Button>
                  ))}
                </div>
              </div>

              <div className={CONTROL_CELL_CLASS}>
                <span className={CONTROL_LABEL_CLASS}>НДС</span>
                <div role="group" aria-label="Режим НДС" className={SEGMENTED_GROUP_CLASS}>
                  {(Object.keys(VAT_MODE_LABELS) as ComparisonVatMode[]).map((mode) => (
                    <Button
                      key={mode}
                      type="button"
                      variant="ghost"
                      size="sm"
                      aria-pressed={vatMode === mode}
                      className={cn(
                        SEGMENTED_ITEM_CLASS,
                        vatMode === mode && SEGMENTED_ITEM_ACTIVE_CLASS
                      )}
                      onClick={() => updateVatMode(mode)}
                    >
                      {VAT_MODE_LABELS[mode]}
                    </Button>
                  ))}
                </div>
              </div>

              {/*
                Своя ячейка с ВИДИМОЙ подписью, а не селектор, приставленный к
                группе «НДС». Приставленный, он читался как четвёртая кнопка
                режима: имя у него было только в `aria-label`, то есть экран
                называл его слепым, а глазам не называл никак. Связка —
                `Label htmlFor` (идиома «Ряда индексов» в `InflationControls`), а
                не `aria-label` рядом с подписью: два источника имени на один
                узел однажды разойдутся.
              */}
              <div className={CONTROL_CELL_CLASS}>
                <Label htmlFor="compare-single-rate" className={CONTROL_LABEL_CLASS}>
                  Единая ставка
                </Label>
                <Select
                  value={vatMode === "single" ? (singleRateParam ?? comparison.single_rate ?? "") : ""}
                  onValueChange={(value) => {
                    if (value) updateSingleRate(value);
                  }}
                >
                  <SelectTrigger
                    id="compare-single-rate"
                    disabled={vatMode !== "single"}
                    className="w-28"
                  >
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
              Чипы классов объекта — новый фильтр (спека диаграммы стоимости
              §2.6, §2.7; DoD 30, 31). Источник — `available_rate_classes`, в
              ПОРЯДКЕ ОТВЕТА: сервер уже упорядочил по `title`, и чипы не имеют
              права переставляться, когда меняется набор договоров (§2.7).
              Роль группы даёт сам `fieldset`/`legend` (как в макете) — заводить
              рядом ещё один `role="group"` с тем же именем означало бы два
              узла accessibility-дерева на одну группу; `aria-pressed` на каждой
              кнопке — тот же приём, что у групп «Показатель»/«НДС» выше, чтобы
              экран не выглядел собранным из двух разных наборов. Чип —
              оформление поверх того же `Button`, а не новый примитив.
            */}
            <fieldset className="mt-4 rounded-lg border border-border-subtle bg-surface-sunken px-4 py-3">
              <legend className={cn(CONTROL_LABEL_CLASS, "px-1.5")}>Класс объекта</legend>
              <div className="flex flex-wrap gap-2">
                {comparison.available_rate_classes.map((rateClass) => {
                  const isSelected = effectiveRateClassIds.has(rateClass.id);
                  /*
                    Единственный выбранный чип не снимается (DoD 30), и молчание
                    объяснено: `aria-disabled` с подсказкой, а НЕ `disabled` —
                    выключенная кнопка спрятала бы защиту за DOM, и снятие защиты
                    перестало бы что-либо ронять. Условие ТО ЖЕ, что в
                    `toggleRateClass`, и оба читают одно множество.
                  */
                  const isLastSelected = isSelected && effectiveRateClassIds.size === 1;
                  return (
                    <Button
                      key={rateClass.id}
                      type="button"
                      variant="outline"
                      size="sm"
                      aria-pressed={isSelected}
                      aria-disabled={isLastSelected || undefined}
                      aria-label={`${rateClass.title}, договоров: ${rateClass.count}`}
                      title={
                        isLastSelected
                          ? "Последний класс не снимается: сравнивать было бы нечего"
                          : undefined
                      }
                      className={cn(
                        "rounded-full",
                        isSelected && "border-accent-text/30 bg-accent-soft text-accent-text dark:bg-accent-soft"
                      )}
                      onClick={() => toggleRateClass(rateClass.id)}
                    >
                      {rateClass.title}
                      {/*
                        Счётчик выбранного чипа — акцентным цветом, а не приглушённым:
                        на зелёной заливке `fg-tertiary` уходил в подложку, и число
                        договоров у выбранного класса читалось хуже, чем у невыбранного.
                      */}
                      <span
                        className={cn(
                          "tabular-nums",
                          isSelected ? "text-accent-text" : "text-fg-tertiary"
                        )}
                      >
                        {rateClass.count}
                      </span>
                    </Button>
                  );
                })}
              </div>
            </fieldset>

            <InflationControls
              series={seriesListQ.data ?? []}
              selectedSeriesId={selectedSeriesId}
              // Запасное название — из ОТВЕТА СРАВНЕНИЯ, не собранное клиентом.
              selectedSeriesName={comparison.inflation?.series_name}
              listFailed={seriesListQ.isError}
              enabled={Boolean(seriesIdParam) && !refused}
              targetMonth={targetMonthParam ?? ""}
              onSelectSeries={selectSeries}
              onToggle={toggleInflation}
              onChangeMonth={updateTargetMonth}
            >
              {/*
                Полоса уровней не отрисовывается, пока приведение не сосчитано, — а
                не скрывается атрибутом `hidden`: в макете `display:flex` перебивал
                браузерное `[hidden] { display:none }`, и полоса продолжала занимать
                место. Отсутствующий узел этой ловушки не имеет вовсе.

                Стоит ВНУТРИ группы поправки (макет, `#levels` внутри
                `fieldset.group`): полоса объясняет именно её коэффициенты, и
                соседним блоком снаружи она объясняла бы их через границу.
              */}
              {comparison.inflation && (
                <InflationLevelsBar
                  inflation={comparison.inflation}
                  canEdit={Boolean(canEditSeries)}
                  onEdit={() => {
                    setDialogMissingYears(undefined);
                    setEditingSeries(comparison.inflation!.series_id);
                  }}
                />
              )}
            </InflationControls>

            {refusalBanner}

            {/*
              Подпись налогового состава денег (AGENTS.md §10 v6.8) —
              печатается на поверхности, а не только в подсказке: тултип рядом
              с ячейками объясняет расчёт, но не заменяет объявление состава.

              Место — ПОСЛЕДНЯЯ строка панели (макет, `.axisnote` в карточке): она
              объявляет состав тех чисел, которые собраны переключателями выше, и
              прочитанная до них объявляла бы состав ещё не сделанного выбора.
            */}
            <p data-testid="comparison-caption" className="mt-4 text-xs text-fg-secondary">
              {comparison.caption}
            </p>
          </Surface>

          {/*
            Диаграмма стоимости (план, задача 11; спека диаграммы стоимости
            §2.2) — НАД таблицей статей, ПОД панелью управления и чипами:
            следует тем же переключателям (корзина — пропом, режим НДС и
            приведение — читая готовый `comparison`), а единицу диаграммы
            держит своим локальным состоянием (спека диаграммы стоимости §2.10
            не заводит её в контракте адреса). Подписи причин неполноты берутся
            из общего модуля `labels.ts` — того же, что у таблицы: спека
            диаграммы стоимости §2.9 требует ОДИН словарь на экран.
          */}
          <ContractCostChart comparison={comparison} bucket={bucket} />

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
