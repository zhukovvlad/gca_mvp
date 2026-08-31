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
import { cellAlignClass, FIRST_COL_CLASS, isNumericChange } from "./cellLayout";
import { WORKS_BUTTON_LABEL } from "./drilldownCopy";
import { PositionDrilldown } from "./PositionDrilldown";
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

// `FIRST_COL_CLASS` (зажим ширины/перенос первой колонки, вся её история и
// числа замеров) переехал в `cellLayout.ts` вместе с `cellAlignClass`,
// `isNumericChange` (задача 12): `PositionDrilldown.tsx` красит строки той же
// колонки той же таблицы, а этот файл уже импортирует `PositionDrilldown` для
// собственной рекурсии — обратный импорт константы отсюда замкнул бы
// модульный цикл. `cellLayout.ts` — нейтральный модуль-нейтральная площадка,
// заведённая ровно для такого разделения (см. докстрок файла).

/**
 * Пропсы блока работ статьи — три независимых ключа раскрытия (§2.1) плюс
 * то, что нужно самому запросу разложения (`tenderId`/`offerIds`) и рисовке
 * (`columnsCount`). Ключи живут в состоянии `StageSummaryTable`, а не в
 * `CategoryRowGroup`: рекурсия строит новое дерево компонентов при каждом
 * раскрытии предка, и локальное состояние узла (в т.ч. счётчик N) умерло бы
 * вместе со свёрнутым узлом — ровно то, чего ради заведён `worksMountedIds` и
 * `Map<number, number>` счётчика на уровне таблицы, а не блока.
 */
interface WorksProps {
  tenderId: number;
  offerIds: number[];
  columnsCount: number;
  worksOpenIds: Set<number>;
  worksMountedIds: Set<number>;
  worksCounts: Map<number, number>;
  onToggleWorks: (id: number) => void;
  onCount: (id: number, n: number) => void;
}

function CategoryRowGroup({
  row,
  depth,
  expandedIds,
  onToggle,
  works,
}: {
  row: StageSummaryRow;
  depth: number;
  expandedIds: Set<number>;
  onToggle: (id: number) => void;
  works: WorksProps;
}) {
  const hasChildren = row.children.length > 0;
  const isOpen = row.work_category_id !== null && expandedIds.has(row.work_category_id);
  // Кнопка «Работы · N» рисуется ПО ПОЛЮ `has_drilldown_rows` (§2.1, §6.3), а
  // не по пустоте `children`: узел без своих строк, но со строками потомков
  // (`1` на обоих трассах стенда), и статья с одними допработами и без детей
  // классификатора вовсе — оба должны получить кнопку, а узел с детьми, но без
  // строк своего поддерева (фикстурная «6.99») — не должен.
  const worksId = row.work_category_id;
  const showWorksButton = worksId !== null && row.has_drilldown_rows;
  const worksOpen = worksId !== null && works.worksOpenIds.has(worksId);
  const worksMounted = worksId !== null && works.worksMountedIds.has(worksId);
  const worksCount = worksId !== null ? works.worksCounts.get(worksId) : undefined;

  return (
    <>
      {/*
        Вес и подложка — на ВСЮ строку, а не на одну ячейку подписи: макет гейта 1
        объявляет `tr.lvl1 > td` полужирным, а `tr.lvl2 > td` — с подложкой
        утопленного уровня (имя переменной макета здесь НЕ ЦИТИРУЕТСЯ: палитра
        макета автономна, а `summaryTokens.test.ts` сканирует исходник текстом и
        не отличает комментарий от кода — и правильно делает, AGENTS.md §11).
        Реализация ставила полужирный только заголовку строки, а
        подложку — только её первой ячейке, поэтому корни не выделялись в числовых
        колонках, а тонировка ребёнка обрывалась на границе подписи (замерено:
        фон числовой ячейки ребёнка — прозрачный против тонированного в макете).
        Сверка макета с реализацией 28.08.2026.
      */}
      <TableRow
        data-testid={`row-${row.work_category_id ?? row.code ?? "row"}`}
        className={cn(depth === 0 ? "font-semibold" : "bg-surface-sunken")}
      >
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
            <span className={cn("min-w-0 break-words", depth === 0 ? "text-fg" : "text-fg-secondary")}>{row.title}</span>
            {/*
              Кнопка «Работы» — слово с `aria-expanded`, а не второй шеврон
              (§2.1): она отдельна от шеврона подстатей выше и сворачивает
              ТОЛЬКО блок работ, оставляя подстатьи на месте. Подпись без
              счётчика до первой загрузки, «Работы · N» после — N читается из
              `worksCounts` таблицы, а не из самого блока, поэтому переживает
              его размонтирование при сворачивании (докстрок `WorksProps`).

              Полировка после мержа: сверка с макетом (задача 12, DoD 6) нашла
              расхождение по внешнему виду и записала его долгом в
              `task-12-report.md` §4/§8 и в девлоге (кнопка осталась НЕ
              исправлена намеренно тогда, до отдельного прохода) — долг не
              заведён отдельным пунктом `docs/TECH_DEBT.md`. Здесь этот долг
              закрывается тремя
              измеренными свойствами — радиус (4 → 6px, `rounded-[6px]`,
              точного именованного токена между `--radius-sm` 4px и
              `--radius-md` 8px в палитре нет), вертикальный отступ (2px →
              0, `py-0.5` → `py-0`), и приведением ФОНА к
              `--bg-surface`/`bg-surface` (rgb(255,255,255) в макете — та же
              строка `bg-surface`, что уже стоит у ячейки статьи глубины 0
              несколькими строками выше). Цвет текста — токеном, не литералом
              макета: `--fg2` макета (rgb(90,93,102)) — это ТОЧНО
              `--text-secondary` приложения (`index.css`), уже несомый классом
              `text-fg-secondary`, которым выше в этом же файле красится
              заголовок статьи (`depth === 0 ? "text-fg" : "text-fg-secondary"`)
              — тот же муted-но-не-третичный токен, точное совпадение в обеих
              темах, а не «ближайший» подбор.

              Второй круг полировки (ветка `feat/drilldown-polish`,
              01.09.2026): продукт-оунер посмотрел на смёрженный экран и
              указал, что кнопка по-прежнему читается как серый текст, а не
              контрол — и был прав. Причина в том, что предыдущая сверка
              (DoD 6, задача 12) сравнивала только радиус/отступ/цвет/фон и
              ни разу не сравнивала `border` и `:hover` — ни для этой кнопки,
              ни для какого-либо другого узла фичи (инсайт
              `docs/insights/enumerate-the-rules-own-properties.md`).
              Добавлены `border border-border-default` (макетная рамка —
              `1px solid`, цвет `rgba(0,0,0,.14)`/`#3A4148` — точное
              совпадение с `--border-default` приложения в обеих темах, тот
              же идиом уже несёт вторичная кнопка,
              `ui-domain/Button.tsx`), и `hover:bg-surface-hover` заменил
              `hover:bg-surface-sunken` (макетный цвет наведения —
              `#FAFAF7`/`#262B35` — это `--bg-surface-hover` приложения, а не
              `--bg-surface-sunken`/`#F7F6F2`, другой токен). Отступ до
              заголовка статьи (`margin-left: 8px` макета) отдельным классом
              не добавлен: `gap-2` родительского `flex`
              (`items-start gap-2` на строке контейнера первой ячейки) уже
              даёт ровно 8px между всеми детьми, включая эту кнопку и
              заголовок перед ней.
            */}
            {showWorksButton && (
              <button
                type="button"
                aria-expanded={worksOpen}
                onClick={() => works.onToggleWorks(worksId as number)}
                className="shrink-0 rounded-[6px] border border-border-default bg-surface px-1.5 py-0 text-2xs font-normal text-fg-secondary hover:bg-surface-hover hover:text-fg"
              >
                {worksCount !== undefined ? `${WORKS_BUTTON_LABEL} · ${worksCount}` : WORKS_BUTTON_LABEL}
              </button>
            )}
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
          className={cn("border-l text-right tabular-nums", cellAlignClass(isNumericChange(row.bargain), false))}
        >
          <ChangeBadge change={row.bargain} dashOnNone />
        </TableCell>
        <TableCell
          data-testid="contribution-cell"
          className={cn("text-right tabular-nums", cellAlignClass(row.contribution.value !== null, false))}
        >
          <ContributionValue contribution={row.contribution} />
        </TableCell>
      </TableRow>
      {/*
        Порядок «сначала дети-статьи, потом работы» — как в спеке (§2.1): у
        узла с обоими (фикстурные «6» и «14» на стенде) подстатьи повторяют
        уже существующий порядок уровней свода, а работы всего поддерева идут
        под ними.
      */}
      {isOpen &&
        row.children.map((child) => (
          <CategoryRowGroup
            key={child.work_category_id ?? child.code ?? "child"}
            row={child}
            depth={depth + 1}
            expandedIds={expandedIds}
            onToggle={onToggle}
            works={works}
          />
        ))}
      {/*
        Блок монтируется после ПЕРВОГО открытия (`worksMountedIds`) и остаётся
        смонтированным при последующем сворачивании кнопкой — так живёт кэш
        запроса (`gcTime`/`staleTime: Infinity`, Task 8) и счётчик N виден
        сразу при повторном раскрытии. Видимость по раскрытым ПРЕДКАМ ничем
        отдельным не гарантируется: свёрнутый предок не рендерит СВОИХ детей
        вовсе (ветка `isOpen &&` выше на уровне предка), поэтому этот блок для
        строки-потомка исчезает вместе с её собственной `<TableRow>` — и
        РАЗМОНТИРУЕТСЯ, теряя локальное состояние; счётчик и факт «когда-либо
        открывали» переживают это ровно потому, что живут в состоянии
        `StageSummaryTable`, на уровень выше рекурсии, а не здесь.
      */}
      {worksMounted && (
        <PositionDrilldown
          tenderId={works.tenderId}
          workCategoryId={worksId as number}
          articleCode={row.code ?? ""}
          offerIds={works.offerIds}
          columnsCount={works.columnsCount}
          open={worksOpen}
          onCount={(n) => works.onCount(worksId as number, n)}
        />
      )}
    </>
  );
}

export function StageSummaryTable({
  summary,
  tenderId,
  offerIds,
}: {
  summary: StageSummary;
  tenderId: number;
  offerIds: number[];
}) {
  // Раскрытие статьи — по её id, а не по коду: коды статей ручного разноса
  // не гарантированно уникальны глобально, а id классификатора — да.
  const [expandedIds, setExpandedIds] = useState<Set<number>>(() => new Set());
  // Три НЕЗАВИСИМЫХ ключа раскрытия блока работ (§2.1): открыт ли блок сейчас
  // (`worksOpenIds`), открывали ли его хоть раз — компонент остаётся
  // смонтированным ради кэша запроса и счётчика (`worksMountedIds`), и сам
  // счётчик N по id статьи (`worksCounts`). Ни один из них не выводится из
  // `expandedIds` подстатей и не хранится внутри `PositionDrilldown` — оба
  // решения обсуждены в докстроке `WorksProps` и в блоке рендера выше.
  const [worksOpenIds, setWorksOpenIds] = useState<Set<number>>(() => new Set());
  const [worksMountedIds, setWorksMountedIds] = useState<Set<number>>(() => new Set());
  // Что из роли `worksMountedIds` реально доказано тестами (задача 11,
  // ревью): её ПРОВЕРЯЕМАЯ работа — не пускать запрос ДО первого раскрытия
  // статьи (§2.1, «раскрытие ленивое»); тест на это есть и краснеет при
  // порче условия монтажа. Заявленная в докстроке WorksProps вторая роль —
  // «пережить закрытие своей же кнопки, не размонтируясь» — тестами НЕ
  // доказана и, похоже, недоказуема: `queryFn` хука не принимает `signal`
  // (нечего отменять), `staleTime`/`gcTime: Infinity` (Task 8) и дедуп
  // запроса по ключу уже гарантируют мгновенный ответ из кэша при повторном
  // монтировании — так что у «остаться смонтированным, а не пересоздаться»
  // нет наблюдаемого следствия ни для сети, ни для экрана. Оставлена как
  // есть, потому что это явное требование интерфейса задачи (три ключа,
  // §2.1), а не потому что для этой конкретной роли нашёлся тест.
  const [worksCounts, setWorksCounts] = useState<Map<number, number>>(() => new Map());

  function toggle(id: number) {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleWorks(id: number) {
    setWorksMountedIds((prev) => (prev.has(id) ? prev : new Set(prev).add(id)));
    setWorksOpenIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function handleWorksCount(id: number, n: number) {
    setWorksCounts((prev) => {
      if (prev.get(id) === n) return prev;
      const next = new Map(prev);
      next.set(id, n);
      return next;
    });
  }

  const { columns, rows, unallocated, total, display } = summary;
  const works: WorksProps = {
    tenderId,
    offerIds,
    columnsCount: columns.length,
    worksOpenIds,
    worksMountedIds,
    worksCounts,
    onToggleWorks: toggleWorks,
    onCount: handleWorksCount,
  };

  return (
    <div className="space-y-2">
      <Surface padding="none" className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className={cn(FIRST_COL_CLASS, "z-10 bg-section-header font-semibold")}>
                Статья классификатора
              </TableHead>
              {columns.map((column) => (
                <TableHead key={column.offer_id} className="whitespace-normal text-right font-semibold normal-case">
                  <span className="text-2xs uppercase tracking-wider text-fg-tertiary">
                    Этап {column.stage_no}
                    {column.label ? ` · ${column.label}` : ""}
                  </span>
                  <span className="mt-0.5 block text-2xs font-normal text-fg-tertiary">
                    {overridesCaption(column.manual_overrides)}
                  </span>
                </TableHead>
              ))}
              <TableHead className="whitespace-normal border-l text-right font-semibold normal-case">
                <span className="text-2xs uppercase tracking-wider text-fg-tertiary">
                  первый → последний
                </span>
              </TableHead>
              <TableHead className="whitespace-normal text-right font-semibold normal-case">
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
                works={works}
              />
            ))}
          </TableBody>
          <TableFooter>
            {/* Обычный вес, а не медиум подвала: макет объявляет `tr.unalloc > td { font-weight: 400 }`. */}
            {/*
              Подложка — по макету: строка «Итого» тонирована ЦЕЛИКОМ, а
              «Нераспределённое» не тонирована вовсе (замер макета: 247,246,242
              против белого). В реализации тонирована была только закреплённая
              ячейка, а остальные получали полутон от подвала примитива — обе
              строки выходили двухцветными, и «Нераспределённое» тонировалось
              вопреки макету. Тот же дефект, что чинился у строк-детей, только
              в подвале.
            */}
            <TableRow data-testid="row-unallocated" className="bg-surface font-normal">
              <TableCell className={cn(FIRST_COL_CLASS, "bg-surface")}>
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
                className={cn("text-right tabular-nums", cellAlignClass(unallocated.contribution.value !== null, false))}
              >
                <ContributionValue contribution={unallocated.contribution} />
              </TableCell>
            </TableRow>
            <TableRow className="border-t-2 border-fg bg-surface-sunken font-semibold" data-testid="row-total">
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
