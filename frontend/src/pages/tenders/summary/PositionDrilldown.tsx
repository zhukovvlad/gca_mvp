import { useEffect, useRef } from "react";

import { StatusPill } from "@/components/ui-domain/StatusPill";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Button } from "@/components/ui/button";
import { TableCell, TableRow } from "@/components/ui/table";
import { formatDecimalMoney } from "@/lib/format";
import { cn } from "@/lib/utils";
import { apiErrorCode, useStagePositions } from "@/services/queries";
import type { StagePositionsRow, StageSummaryErrorCode } from "@/types/domain";

import { ERROR_LABEL, REASON_LABEL } from "./cellCopy";
import { FIRST_COL_CLASS } from "./cellLayout";
import {
  AMBIGUOUS_PILL,
  DRILLDOWN_ERROR_LABEL,
  NO_ROWS_LABEL,
  RETRY_LABEL,
  UNMATCHED_HINT,
  UNMATCHED_PILL_LABEL,
  WORKS_SUBHEADING,
  collapsedTitle,
  extraPill,
  restTitle,
  worksHeading,
} from "./drilldownCopy";
import { drilldownGroupCount, drilldownRowKey, showsLot } from "./drilldownData";
import { PositionCell } from "./PositionCell";
import { ChangeBadge } from "./SummaryCell";

/**
 * Блок попозиционного раскрытия статьи — строки ТОЙ ЖЕ таблицы свода (спека
 * 2026-08-30-position-drilldown-design.md §2.1, §2.3, §2.7, §2.10–§2.12;
 * задача 10 плана).
 *
 * Возвращает фрагмент `<TableRow>` и монтируется внутри `<TableBody>` под
 * строкой статьи (Task 11 владеет кнопкой «Работы · N» и состоянием
 * раскрытия — этот компонент только рисует строки и сообщает счётчик группой
 * вверх через `onCount`). Каждый тест поэтому оборачивает компонент в
 * `<table><tbody>` — фрагмент `<tr>` вне таблицы jsdom отвергает.
 */

/** Короткое имя вида строки для `data-testid` — используется тестами и
 *  приёмкой стенда, полные значения `StagePositionsRowKind` для разметки не
 *  годятся (длиннее и содержат подчёркивания там, где короче нагляднее). */
const ROW_TESTID: Record<StagePositionsRow["kind"], string> = {
  position: "position",
  additional_works: "extra",
  unmatched: "unmatched",
  collapsed_appeared_disappeared: "collapsed",
  rest: "rest",
};

/**
 * Ячейка «Вклад в итог» строки разложения — тот же приём, что
 * `ContributionValue` таблицы свода (`StageSummaryTable.tsx`): число тоном
 * направления либо прочерк. Функция не экспортирована оттуда намеренно (та
 * функция приватна файлу-компоненту задачи 11, и превращать её в общий
 * экспорт значило бы плодить чужую точку связывания ради одного вызова) —
 * приём скопирован, а не переиспользован через импорт.
 */
function DrilldownContribution({ contribution }: { contribution: StagePositionsRow["contribution"] }) {
  if (contribution.value === null) {
    return (
      <span data-testid="drill-contribution" className="text-fg-tertiary">
        —
      </span>
    );
  }
  const toneClass =
    contribution.direction === "up"
      ? "text-accent-text"
      : contribution.direction === "down"
        ? "text-danger-text"
        : "text-fg-tertiary";
  return (
    <span data-testid="drill-contribution" className={toneClass}>
      {formatDecimalMoney(contribution.value)}
    </span>
  );
}

/**
 * Наименование строки — обрезано двумя строками, полный текст в `title`
 * (§2.10). У свёрнутых строк (`collapsed_appeared_disappeared`, `rest`)
 * подпись не файловая, а сгенерированная (`collapsedTitle`/`restTitle` по
 * `group_count`), и приглушённым тоном (§2.3): `title` сервера у них пуст, и
 * показывать его во `title`-атрибуте нечем и незачем.
 */
function DrilldownRowTitle({ row }: { row: StagePositionsRow }) {
  if (row.kind === "collapsed_appeared_disappeared") {
    return <span className="line-clamp-2 text-fg-tertiary">{collapsedTitle(row.group_count ?? 0)}</span>;
  }
  if (row.kind === "rest") {
    return <span className="line-clamp-2 text-fg-tertiary">{restTitle(row.group_count ?? 0)}</span>;
  }
  return (
    <span className="line-clamp-2" title={row.title}>
      {row.title}
    </span>
  );
}

/** Пилюли строки — «несколько строк сметы», допработа (со ссылкой и, при
 *  нескольких лотах, лотом, §2.7) либо диагностическая подсказка (§2.7). */
function DrilldownRowPills({ row, lotShown }: { row: StagePositionsRow; lotShown: boolean }) {
  return (
    <div className="flex flex-wrap gap-1">
      {row.ambiguous && <StatusPill tone="neutral" label={AMBIGUOUS_PILL} />}
      {row.kind === "additional_works" && (
        <StatusPill tone="neutral" label={extraPill(row.chapter_ref_raw ?? "", lotShown ? row.lot_key : null)} />
      )}
      {row.kind === "unmatched" && (
        <span title={UNMATCHED_HINT}>
          <StatusPill tone="neutral" label={UNMATCHED_PILL_LABEL} />
        </span>
      )}
    </div>
  );
}

/**
 * `rowKey` — вычислен ОДИН РАЗ вызывающей стороной (`drilldownRowKey(row)`) и
 * передан сюда как проп, а не пересчитан внутри: тот же локал идёт и в
 * React-`key` карты `.map` (недоступен внутри дочернего компонента — `key` не
 * попадает в `props`), и в `data-row-key` этой строки. Единое выражение —
 * не стиль, а гарантия: правка, поменявшая ключ, не может тихо оставить
 * атрибут со старым значением, потому что атрибуту неоткуда взять другое
 * число (находка ревью 31.08.2026 — компонентный тест на `showsLot`/`extraPill`
 * уже ловил ровно такой разрыв «функция правильна, вызов забыт»; `row_key`
 * был в той же слепой зоне: `key` React не наблюдаем тестом напрямую).
 */
function DrilldownRow({ row, rowKey, lotShown }: { row: StagePositionsRow; rowKey: string; lotShown: boolean }) {
  return (
    <TableRow data-testid={`drill-row-${ROW_TESTID[row.kind]}`} data-row-key={rowKey}>
      {/*
        `pl-[52px]`, не `pl-8` (32px, шаг вложенности подстатей в
        `StageSummaryTable.tsx`): найдено сверкой с макетом (задача 12, DoD
        6) — `tr.pos3 .t { padding-left: 52px }`, ОДНИМ уровнем глубже
        собственного отступа статьи-владельца. С `pl-8` строка работы визуально
        читалась КАК СЕСТРА подстатьи, а не её ребёнок.
      */}
      <TableCell className={cn(FIRST_COL_CLASS, "bg-surface-sunken pl-[52px]")}>
        <div className="flex flex-col gap-1">
          <DrilldownRowTitle row={row} />
          <DrilldownRowPills row={row} lotShown={lotShown} />
        </div>
      </TableCell>
      {row.cells.map((cell, index) => (
        <PositionCell key={index} cell={cell} />
      ))}
      <TableCell className="text-right tabular-nums">
        <ChangeBadge change={row.bargain} dashOnNone />
      </TableCell>
      <TableCell className="text-right tabular-nums">
        <DrilldownContribution contribution={row.contribution} />
      </TableCell>
    </TableRow>
  );
}

export interface PositionDrilldownProps {
  tenderId: number;
  workCategoryId: number;
  articleCode: string;
  offerIds: number[];
  /** Число выбранных колонок этапов — определяет `colSpan = columnsCount + 3`
   *  строк-заглушек (наименование + «Торг» + «Вклад»). */
  columnsCount: number;
  /**
   * Блок свёрнут своей кнопкой — строки не рисуются, но компонент остаётся
   * смонтированным (пока видима сама строка статьи, §2.1). Хук ниже НЕ
   * зависит от `open`: компонент монтируется только после первого раскрытия
   * (Task 11), и повторное сворачивание/раскрытие не должно посылать запрос
   * заново — эту работу делает `staleTime`/`gcTime: Infinity` хука (Task 8).
   */
  open: boolean;
  /** N кнопки «Работы · N» родителя — число ГРУПП разложения, включая
   *  свёрнутые (§2.1), сообщается по приходу данных. */
  onCount?: (n: number) => void;
}

export function PositionDrilldown({
  tenderId,
  workCategoryId,
  articleCode,
  offerIds,
  columnsCount,
  open,
  onCount,
}: PositionDrilldownProps) {
  const { data, isPending, isError, error, refetch } = useStagePositions(tenderId, workCategoryId, offerIds, true);

  // `onCount` живёт в рефе, а не в зависимостях эффекта: репорт идёт «по
  // приходу данных», не «при каждой смене identity колбэка родителя» (Task
  // 11 передаёт инлайновую функцию, которая пересоздаётся на каждый рендер, —
  // с колбэком в зависимостях эффект стрелял бы на каждый ререндер родителя,
  // а не только на приход `data`). Реф всегда несёт САМУЮ СВЕЖУЮ функцию, а не
  // замыкание с монтажа, поэтому честная альтернатива `eslint-disable` не
  // жертвует и правильностью: колбэк вызывается новейшим, просто не входит в
  // список триггеров повторного запуска.
  const onCountRef = useRef(onCount);
  useEffect(() => {
    onCountRef.current = onCount;
  });

  useEffect(() => {
    if (data) {
      onCountRef.current?.(drilldownGroupCount(data.rows));
    }
  }, [data]);

  if (!open) return null;

  const colSpan = columnsCount + 3;

  if (isPending) {
    return (
      <TableRow>
        <TableCell colSpan={colSpan}>
          <Skeleton className="h-16" />
        </TableCell>
      </TableRow>
    );
  }

  if (isError) {
    const code = apiErrorCode(error) as StageSummaryErrorCode | undefined;
    const reasonText = code && code in ERROR_LABEL ? ERROR_LABEL[code] : null;
    return (
      <TableRow>
        <TableCell colSpan={colSpan}>
          <div className="flex items-center justify-between gap-2">
            <span className="text-fg-secondary">
              {DRILLDOWN_ERROR_LABEL}
              {reasonText ? `: ${reasonText}` : ""}
            </span>
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              {RETRY_LABEL}
            </Button>
          </div>
        </TableCell>
      </TableRow>
    );
  }

  if (!data) return null;

  if (data.reason === "no_rows_in_subtree") {
    return (
      <TableRow>
        <TableCell colSpan={colSpan} className="text-fg-tertiary">
          {NO_ROWS_LABEL}
        </TableCell>
      </TableRow>
    );
  }

  if (data.reason === "unknown_vat_base") {
    return (
      <TableRow>
        <TableCell colSpan={colSpan} className="text-fg-tertiary">
          {REASON_LABEL.unknown_vat_base}
        </TableCell>
      </TableRow>
    );
  }

  const lotShown = showsLot(data.rows);

  return (
    <>
      {/*
        Найдено сверкой с макетом (задача 12, DoD 6): заголовок блока стоял
        обычным предложением (13px, вес 500, цвет основного текста), а в
        макете (`tr.workshead > td`) это «эркер» секции — 11px, вес 600,
        КАПС, разрядка, приглушённый тон, подложка `--sechead`, — тот же
        приём, что уже несёт заголовок «Этап N» шапки свода
        (`StageSummaryTable.tsx`, `text-2xs uppercase tracking-wider
        text-fg-tertiary`). Подложка — `bg-section-header`, а не
        `bg-surface-sunken`: замер её `backgroundColor` на макете дал
        rgb(250, 250, 247), а это ровно токен `--bg-section-header`
        (`index.css`), не `--bg-surface-sunken`.
      */}
      <TableRow data-testid="drill-heading" className="bg-section-header">
        <TableCell colSpan={colSpan}>
          <div className="text-2xs font-semibold uppercase tracking-wider text-fg-tertiary">
            {worksHeading(articleCode)}
          </div>
          <div className="text-2xs text-fg-tertiary">{WORKS_SUBHEADING}</div>
        </TableCell>
      </TableRow>
      {data.rows.map((row) => {
        // Единое вычисление — см. докстрок `DrilldownRow`: `rowKey` идёт и в
        // React-`key`, и в `data-row-key`, из ОДНОГО выражения.
        const rowKey = drilldownRowKey(row);
        return <DrilldownRow key={rowKey} rowKey={rowKey} row={row} lotShown={lotShown} />;
      })}
    </>
  );
}
