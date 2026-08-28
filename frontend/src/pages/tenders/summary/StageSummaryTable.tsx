import { useState } from "react";
import { ChevronRight } from "lucide-react";

import { StatusPill } from "@/components/ui-domain/StatusPill";
import { Surface } from "@/components/ui-domain/Surface";
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatDate, formatDecimalMoney } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { StageSummary, StageSummaryColumn, StageSummaryRow } from "@/types/domain";

import { REASON_LABEL } from "./cellCopy";
import { cellAlignClass, isNumericChange } from "./cellLayout";
import { ChangeBadge, SummaryCell, SummaryTotalCell } from "./SummaryCell";

/**
 * Таблица свода по статьям (спека 2026-08-27-stage-summary-design.md
 * §2.5–§2.9, §2.13; задача 8 плана).
 *
 * Референс раскладки — `docs/superpowers/specs/2026-08-27-stage-summary-mockup.html`
 * (`table.pass`), одобренный на гейте 1: файл там форма, а не код для
 * копирования — здесь примитивы shadcn/ui и токены темы, а не литеральные
 * цвета. Порядок строк — КАК ПРИШЁЛ С СЕРВЕРА, таблица его не
 * пересортировывает; сам порядок задаёт `sort_order` классификатора
 * (спека §2.13, ревизия 28.08.2026 — прежде было по убыванию модуля «Вклада в
 * итог»), и по телу ответа он не наблюдаем: `sort_order` контракт не несёт.
 *
 * Горизонтальная прокрутка, закреплённая первая колонка и ширины колонок —
 * ЛОЖАТСЯ здесь классами (`sticky left-0`, `overflow-x-auto`), но их РАБОТА
 * не проверяется тестами этого файла: тестовый прогон не считает раскладку
 * (`docs/insights/unobservable-in-the-runner.md`). Замер в браузере — задача
 * следующего этапа (layout), не эта.
 */

/**
 * Пояснение пилюли «обязательная строка» — то же, что на макете (`table.pass`,
 * `title` пилюли «Нераспределённого»): «Разделы без статьи классификатора.
 * Строка показывается всегда, даже пустая». Живёт здесь, не в `cellCopy.ts`:
 * это НЕ экспортируемая константа (см. правило файла-компонента про
 * `react-refresh/only-export-components` — оно касается ЭКСПОРТОВ, не
 * приватных модульных переменных).
 */
const UNALLOCATED_EXPLANATION_ID = "unallocated-pill-explanation";
const UNALLOCATED_EXPLANATION =
  "Разделы без статьи классификатора. Строка показывается всегда, даже пустая.";

/** Число → русское слово в родительном падеже, три формы (1 / 2-4 / 5+, с
 *  исключением 11-14). Тот же приём, что `articlesWord`/`unallocatedCaption`
 *  паспорта проекта (`frontend/src/pages/passport/CategoryTable.tsx`). */
function pluralDecision(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
  return many;
}

/** Подпись разноса под шапкой колонки (контракт §8 спеки макета). */
function overridesCaption(overrides: StageSummaryColumn["manual_overrides"]): string {
  if (overrides.count === 0) return "без ручного разноса";
  const word = pluralDecision(overrides.count, "решение", "решения", "решений");
  return `разнос: ${overrides.count} ${word} · ${formatDate(overrides.last_at)}`;
}

/**
 * Подпись сходимости колонки в строке «Итого» — три состояния (converged
 * true/false/null), по данным `StageSummaryColumn.convergence`, а не по
 * сравнению сумм клиентом. Fix round 2, п.4: расхождение — дефект данных,
 * который спека требует ПОКАЗАТЬ, а не притушить общим нейтральным тоном —
 * три состояния несут три РАЗНЫХ тона: сходится — тем же тихим тоном, что и
 * остальные вычисленные подписи; не сходится — тревожным (`text-warning-text`,
 * тон, которым уже красит несходящееся строка «Нераспределённое» на макете
 * `.conv.bad`); сверка невозможна — СВОИМ нейтральным (`text-neutral-text`,
 * тон пилюль `StatusPill tone="neutral"`), потому что это отсутствие данных
 * (файловый итог недоступен), а не дефект — путать его с «не сходится» значило
 * бы утверждать несуществующее расхождение.
 */
function convergenceView(convergence: StageSummaryColumn["convergence"]): { text: string; toneClass: string } {
  if (convergence.converged === true) {
    return { text: "сходится", toneClass: "text-fg-tertiary" };
  }
  if (convergence.converged === false) {
    return {
      text: `не сходится: Δ ${formatDecimalMoney(convergence.delta)}`,
      toneClass: "text-warning-text",
    };
  }
  return {
    text: `сверка невозможна: ${convergence.reason ? REASON_LABEL[convergence.reason] : "—"}`,
    toneClass: "text-neutral-text",
  };
}

/** Ячейка «Вклад в итог»/«Торг»-контрибуция строки: число с тоном по
 *  направлению либо прочерк с причиной — рисуется ПО ПОЛЮ, не по знаку суммы. */
function ContributionValue({
  contribution,
}: {
  contribution: StageSummaryRow["contribution"];
}) {
  if (contribution.value === null) {
    return (
      <span
        data-testid="contribution-value"
        className="text-fg-tertiary"
        title={contribution.reason ? REASON_LABEL[contribution.reason] : undefined}
      >
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
    <span data-testid="contribution-value" className={toneClass}>
      {formatDecimalMoney(contribution.value)}
    </span>
  );
}

/**
 * Колонка классификатора зажата по ширине — `min-width: 250px`,
 * `max-width: 420px`, ровно как на макете гейта 1 (`table.pass`,
 * `th.art, td.t`). Реализация это ограничение ПОТЕРЯЛА, и таблица с авторазметкой
 * отдавала колонке столько, сколько просило самое длинное наименование: замер на
 * стенде 28.08.2026 — при свёрнутых статьях 459,8 px (34 % контейнера) и всё
 * помещалось, при раскрытых 752,81 px (55,8 %), таблица становилась шире
 * контейнера на 253 px, и «Торг» с «Вкладом в итог» уезжали за правый край.
 *
 * Зажим ставится на ВСЕ ячейки первой колонки — шапку, строки статей и обе
 * строки подвала: ширину колонки в авторазметке диктует самая широкая ячейка, и
 * пропущенная строка вернула бы прежнее поведение целиком.
 *
 * `whitespace-normal` здесь — не украшение, а условие работы зажима: примитив
 * `TableCell`/`TableHead` из shadcn несёт `whitespace-nowrap`, и с ним
 * наименование не переносится вовсе. Он ломает строку ПО ПРОБЕЛАМ; за
 * одиночный длинный токен (слипшееся слово, код без пробелов) отвечают
 * `break-words` и `min-w-0` на самом наименовании ниже — первый разрешает
 * разрыв внутри слова, второй снимает `min-width: auto` у флекс-элемента, без
 * которого он не сузится. Три класса — три разные половины одной работы, и
 * снятие любого возвращает часть дефекта. Замер на стенде показал, чем это кончается:
 * колонка честно вставала в 446 px, а текст на 100 символов вылезал из своей
 * ячейки на 130 px и ЛОЖИЛСЯ ПОВЕРХ первой денежной колонки — 25 таких ячеек при
 * раскрытых статьях. То есть зажим без переноса делает хуже, чем его отсутствие.
 *
 * Вместе с зажимом строка содержимого ячейки выравнивается ПО ВЕРХУ
 * (`items-start` вместо `items-center`): наименование теперь бывает в две-три
 * строки, и при выравнивании по центру треугольник раскрытия и код статьи
 * уезжали бы к середине абзаца вместо его первой строки. То же у
 * «Нераспределённого» — наименование там короткое, но правило одно на колонку,
 * а не на строку.
 *
 * Про число 420: это ПОТОЛОК ПРЕДПОЧТИТЕЛЬНОЙ ширины, а не итоговой. При
 * `table-layout: auto` и `w-full` браузер сперва считает предпочтительные
 * ширины (тут `max-width` и связывает), а СВОБОДНОЕ место потом раздаёт поверх
 * — поэтому на широком окне замер показывает 446 px, и это не полработы.
 * Заработанный инвариант формулируется не «≤ 420 px», а «колонка не растёт от
 * содержимого»: замеры со свёрнутыми и раскрытыми статьями совпадают до сотых.
 */
const FIRST_COL_CLASS = "sticky left-0 min-w-[250px] max-w-[420px] whitespace-normal align-top";

function CategoryRowGroup({
  row,
  depth,
  expandedIds,
  onToggle,
}: {
  row: StageSummaryRow;
  depth: number;
  expandedIds: Set<number>;
  onToggle: (id: number) => void;
}) {
  const hasChildren = row.children.length > 0;
  const isOpen = row.work_category_id !== null && expandedIds.has(row.work_category_id);

  return (
    <>
      <TableRow data-testid={`row-${row.work_category_id ?? row.code ?? "row"}`}>
        <TableCell
          className={cn(FIRST_COL_CLASS, "bg-surface", depth > 0 && "bg-surface-sunken pl-8")}
        >
          <div className="flex items-start gap-2">
            {hasChildren ? (
              <button
                type="button"
                aria-expanded={isOpen}
                aria-label={`${isOpen ? "Свернуть" : "Раскрыть"} ${row.title}`}
                onClick={() => row.work_category_id !== null && onToggle(row.work_category_id)}
                className="shrink-0 text-fg-tertiary hover:text-fg"
              >
                <ChevronRight
                  aria-hidden="true"
                  className={cn("size-3.5 transition-transform", isOpen && "rotate-90")}
                />
              </button>
            ) : (
              <span className="inline-block size-3.5 shrink-0" aria-hidden="true" />
            )}
            {/*
              Код статьи классификатора перед названием — как на макете
              (`table.pass`, `<span class="code">`): расхождение с одобренным
              макетом без причины запрещено правилом проекта «фикс или
              письменный долг», а тут причины нет — ответ несёт `row.code`
              (fix round 2, п.2).
            */}
            {row.code && (
              <span data-testid="row-code" className="shrink-0 font-mono text-2xs text-fg-tertiary">
                {row.code}
              </span>
            )}
            <span className={cn("min-w-0 break-words", depth === 0 ? "font-medium text-fg" : "text-fg-secondary")}>{row.title}</span>
          </div>
        </TableCell>
        {row.cells.map((cell, index) => (
          <SummaryCell key={index} cell={cell} />
        ))}
        {/*
          `dashOnNone` — по макету гейта 1: в колонке «Торг» у статьи, которой
          нет ни на одном конце пути, стоит прочерк (`table.pass`, статьи «15» и
          «99» — `td.num.sep` с литералом «—»), а не пустота. Реализация это
          потеряла вместе с зажимом ширины. Довод тот же, что у трассы и KPI и
          записанный в спеке §2.14: рядом с «Торгом» состояния НЕТ, объяснить
          пустой слот нечем, и читатель принимает его за несчитанное значение.
        */}
        <TableCell
          data-testid="bargain-cell"
          className={cn("border-l text-right", cellAlignClass(isNumericChange(row.bargain), false))}
        >
          <ChangeBadge change={row.bargain} dashOnNone />
        </TableCell>
        <TableCell
          data-testid="contribution-cell"
          className={cn("text-right", cellAlignClass(row.contribution.value !== null, false))}
        >
          <ContributionValue contribution={row.contribution} />
        </TableCell>
      </TableRow>
      {isOpen &&
        row.children.map((child) => (
          <CategoryRowGroup
            key={child.work_category_id ?? child.code ?? "child"}
            row={child}
            depth={depth + 1}
            expandedIds={expandedIds}
            onToggle={onToggle}
          />
        ))}
    </>
  );
}

export function StageSummaryTable({ summary }: { summary: StageSummary }) {
  // Раскрытие статьи — по её id, а не по коду: коды статей ручного разноса
  // не гарантированно уникальны глобально, а id классификатора — да.
  const [expandedIds, setExpandedIds] = useState<Set<number>>(() => new Set());

  function toggle(id: number) {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const { columns, rows, unallocated, total, display } = summary;

  return (
    <div className="space-y-2">
      <Surface padding="none" className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className={cn(FIRST_COL_CLASS, "z-10 bg-section-header")}>Статья классификатора</TableHead>
              {columns.map((column) => (
                <TableHead key={column.offer_id} className="text-right normal-case">
                  <span className="text-2xs uppercase tracking-wider text-fg-tertiary">
                    Этап {column.stage_no}
                    {column.label ? ` · ${column.label}` : ""}
                  </span>
                  <span className="mt-0.5 block text-2xs font-normal text-fg-tertiary">
                    {overridesCaption(column.manual_overrides)}
                  </span>
                </TableHead>
              ))}
              <TableHead className="border-l text-right normal-case">
                <span className="text-2xs uppercase tracking-wider text-fg-tertiary">
                  Торг: первый → последний
                </span>
              </TableHead>
              <TableHead className="text-right normal-case">
                <span className="text-2xs uppercase tracking-wider text-fg-tertiary">Вклад в итог</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <CategoryRowGroup
                key={row.work_category_id ?? row.code ?? "row"}
                row={row}
                depth={0}
                expandedIds={expandedIds}
                onToggle={toggle}
              />
            ))}
          </TableBody>
          <TableFooter>
            <TableRow data-testid="row-unallocated">
              <TableCell className={cn(FIRST_COL_CLASS, "bg-surface-sunken")}>
                <div className="flex items-start gap-2">
                  <span className="inline-block size-3.5 shrink-0" aria-hidden="true" />
                  {/* Код-прочерк — как на макете: у «Нераспределённого» нет
                      кода классификатора, но ячейка кода на строке всё равно есть. */}
                  <span data-testid="row-code" className="shrink-0 font-mono text-2xs text-fg-tertiary">
                    —
                  </span>
                  <span className="text-fg-secondary">{unallocated.title}</span>
                  {/*
                    Пилюль общего компонента `StatusPill` не принимает и не
                    прокидывает `title` — правка не в неё, а в обёртку вокруг
                    (fix round 2, п.3). Причина реализована ТЕМ ЖЕ приёмом, что
                    недоступная плитка решётки тендера (`OfferGrid.tsx`,
                    «foreign»-плитка): `aria-describedby` на фокусируемый узел
                    плюс `sr-only` текст — озвучивается скринридером НЕЗАВИСИМО
                    от курсора, а не только по наведению (как было бы с одним
                    `title`).
                  */}
                  <span tabIndex={0} aria-describedby={UNALLOCATED_EXPLANATION_ID} className="cursor-help">
                    <StatusPill tone="neutral" label="обязательная строка" className="text-2xs" />
                  </span>
                  <span id={UNALLOCATED_EXPLANATION_ID} className="sr-only">
                    {UNALLOCATED_EXPLANATION}
                  </span>
                </div>
              </TableCell>
              {unallocated.cells.map((cell, index) => (
                <SummaryCell key={index} cell={cell} />
              ))}
              {/*
                «Торг» «Нераспределённого» — не процент (контракт §7 макета):
                дельта этой строки не читается как уступка, поэтому здесь
                литеральное «без %» с причиной, а не ChangeBadge с kind=none
                (тот в ячейке ничего не рисует — REASON только в title).
              */}
              {/* «без %» — не число, строка одна: по центру, как пилюли. */}
              <TableCell
                className="border-l text-right align-middle text-fg-tertiary"
                title={REASON_LABEL.unallocated}
              >
                без %
              </TableCell>
              <TableCell
                className={cn("text-right", cellAlignClass(unallocated.contribution.value !== null, false))}
              >
                <ContributionValue contribution={unallocated.contribution} />
              </TableCell>
            </TableRow>
            <TableRow className="border-t-2 border-fg" data-testid="row-total">
              <TableCell className={cn(FIRST_COL_CLASS, "bg-surface-sunken font-semibold text-fg")}>
                Итого по предложению
              </TableCell>
              {total.cells.map((cell, index) => {
                const convergence = convergenceView(columns[index].convergence);
                return (
                  <SummaryTotalCell
                    key={index}
                    cell={cell}
                    extra={
                      <span data-testid="convergence" className={convergence.toneClass}>
                        {convergence.text}
                      </span>
                    }
                  />
                );
              })}
              <TableCell className="border-l" />
              <TableCell />
            </TableRow>
          </TableFooter>
        </Table>
      </Surface>
      {display.tax_basis === "net" && (
        <p className="text-2xs text-fg-tertiary">Δ сходимости измерена в исходных деньгах файла</p>
      )}
    </div>
  );
}
