import { Fragment, useId, useMemo, useState } from "react";
import { ChevronRight } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { MoneyCell } from "@/components/ui-domain/MoneyCell";
import { formatSharePercent } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  Decimal,
  ProjectPassport,
  ProjectPassportCategory,
  ProjectPassportSection,
  ProjectPassportUnallocated,
  RateNote,
  RateState,
} from "@/types/domain";

import { RATE_NOTE_LABEL, RATE_STATE_LABEL } from "./rateLabels";
import { UnallocatedPanel } from "./UnallocatedPanel";

/**
 * Таблица по статьям классификатора (Ф6 фазы 7, спека §2.9 пп. 5-9, 12, 15;
 * план, задача 8).
 *
 * Референс раскладки — `docs/superpowers/specs/2026-08-09-project-passport-mockup.html`
 * (`renderTable`/`renderNode`), одобренный на гейте 1. Файл там — форма, а не код
 * для копирования: здесь примитивы shadcn/ui и токены темы, а не литеральные цвета.
 *
 * **Сервер отдаёт `categories` ПЛОСКИМ списком** с `parent_id`, порядок внутри —
 * глубина обхода (правило 2 спеки §2.6). Компонент восстанавливает иерархию, но
 * порядок братьев внутри уровня НЕ пересортировывает — только сохраняет порядок
 * исходного списка при разборе по родителям.
 */

interface CategoryNode extends ProjectPassportCategory {
  children: CategoryNode[];
}

/** Плоский список → дерево. Порядок братьев — порядок появления в исходном
 *  списке (уже глубина обхода, сервер его не пересматривает — план §2.6). */
function buildCategoryTree(categories: ProjectPassportCategory[]): CategoryNode[] {
  const byId = new Map<number, CategoryNode>();
  for (const category of categories) {
    byId.set(category.id, { ...category, children: [] });
  }
  const roots: CategoryNode[] = [];
  for (const category of categories) {
    const node = byId.get(category.id);
    if (!node) continue;
    if (category.parent_id === null) {
      roots.push(node);
      continue;
    }
    const parent = byId.get(category.parent_id);
    parent?.children.push(node);
  }
  return roots;
}

/** Ноль ли десятичная строка — теми же знаками, что `PassportHeader.isZeroDecimal`:
 *  без `Number()`, деньги — `Decimal`-строки (AGENTS.md §3). */
function isZeroDecimal(value: string): boolean {
  return /^-?0+(\.0+)?$/.test(value.trim());
}

/**
 * Доля в колонке таблицы. Формат — общий `formatSharePercent` (спека Ф6a §2.3):
 * РОВНО два знака, целочисленной арифметикой, без `Number()`.
 *
 * Здесь остаётся только обработка `null` — она у двух экранов **разная и должна
 * такой остаться**: колонке нужен прочерк, легенде — опустить процент вовсе
 * (правило 5 §2.10 спеки Ф6). Общим стало ровно то, что у них общее.
 */
function formatSharePct(value: string | null): string {
  return value === null ? "—" : formatSharePercent(value);
}

/**
 * Подпись пилюли по состоянию узла — снимает развилку `volume_inconsistent`.
 * Сами карты (`RATE_STATE_LABEL`, `RATE_NOTE_LABEL`) — в `./rateLabels`, не
 * здесь: файл, экспортирующий React-компонент, не может экспортировать что-то
 * ещё без потери fast refresh (`react-refresh/only-export-components`,
 * `just lint-frontend`).
 */
function rateStateCaption(rateState: RateState, rateNote: RateNote | null): string | null {
  if (rateState === "volume_inconsistent") {
    return rateNote === null ? null : RATE_NOTE_LABEL[rateNote];
  }
  return RATE_STATE_LABEL[rateState];
}

/**
 * Состояния, где данные ДОЗАПОЛНИМЫ либо испорчены — макет различает `pill` и
 * `pill warn`, и разница выражена токенами темы (та же пара, что уже несёт
 * строка «Нераспределённое»), а не литеральным цветом. Состояния, где ставки
 * не будет никогда по природе данных (`additional_works`, `unit_not_scalable`),
 * остаются нейтральными.
 */
const RATE_WARN_STATES: ReadonlySet<RateState> = new Set([
  "amount_missing",
  "unit_missing",
  "unit_conflict",
  "volume_missing",
  "volume_nonpositive",
  "volume_inconsistent",
]);

/**
 * Три ячейки «Ед.» / «Объём» / «Ставка, ₽/ед.» для строки статьи (спека
 * §2.9, §2.10; задача 6 плана). Общая для CategoryRow — четыре другие места
 * раскладки (допработы, служебная строка, «Нераспределённое», «Итого по
 * договору») несут прочерки и этот компонент не используют (мокап, строка
 * итога): там взять реальные `unit`/`volume`/`rate_state` неоткуда — эти
 * колонки посчитаны только по строке-носителю кода статьи.
 */
function RateColumns({
  code,
  unit,
  volume,
  unitRate,
  rateState,
  rateNote,
}: {
  code: string;
  unit: string | null;
  volume: Decimal | null;
  unitRate: Decimal | null;
  rateState: RateState;
  rateNote: RateNote | null;
}) {
  const caption = rateStateCaption(rateState, rateNote);
  const warn = RATE_WARN_STATES.has(rateState);
  return (
    <>
      <TableCell data-testid={`unit-cat-${code}`} className="text-right text-xs text-fg-secondary">
        {unit ?? "—"}
      </TableCell>
      {/*
        Объём — ИЗ ФАЙЛА, не вычислен: `maxFractionDigits` НЕ передаётся
        (докстрока `MoneyCell` разграничивает это явно), в отличие от ₽/м²
        ниже, которая делит `Decimal` на `Decimal` и округляется намеренно.
      */}
      <TableCell data-testid={`volume-cat-${code}`} className="text-right">
        <MoneyCell value={volume} currency="" />
      </TableCell>
      <TableCell data-testid={`rate-cat-${code}`} className="text-right">
        {rateState === "rate" ? (
          <MoneyCell value={unitRate} maxFractionDigits={2} currency="₽/ед." />
        ) : (
          caption && (
            <Badge
              variant="outline"
              className={warn ? "border-warning-border bg-warning-soft text-warning-text" : undefined}
            >
              {caption}
            </Badge>
          )
        )}
      </TableCell>
    </>
  );
}

/** Три ячейки-прочерка тех же колонок — там, где показывать нечего (мокап, строка итога). */
function RateColumnsDash() {
  return (
    <>
      <TableCell className="text-right text-fg-tertiary">—</TableCell>
      <TableCell className="text-right text-fg-tertiary">—</TableCell>
      <TableCell className="text-right text-fg-tertiary">—</TableCell>
    </>
  );
}

/** Одна подпись раздела: «6.5 «Прочее»». Заголовок, уже несущий кавычки
 *  (справочник Ф6 такие заводит), вторично не оборачивается. */
function sectionLabel(section: ProjectPassportSection): string {
  const title = section.title.trim().startsWith("«") ? section.title : `«${section.title}»`;
  return section.number ? `${section.number} ${title}` : title;
}

/**
 * `whitespace-normal break-words` — не косметика, а печатное обязательство
 * (`AGENTS.md` §10: «без обрезки по правому краю»). Ячейка таблицы shadcn несёт
 * `whitespace-nowrap`, а `white-space` НАСЛЕДУЕТСЯ, поэтому подпись без явного
 * переопределения растёт в одну строку: замер в браузере на смете 329-ТУ стенда
 * дал 2437 px содержимого в колонке 221 px и текст, уходящий за лист А4 на
 * 1852 px. Подпись, у которой разделы есть, но все ФАЙЛОВЫЕ, уходила за лист на
 * 77 px — то есть дефект принадлежит самой подписи, а не бейджам ручного
 * разноса. Свёрнутое дерево этого не показывает: там служебных строк нет вовсе,
 * и все прежние замеры печати мерили только его.
 *
 * Класс общий на обе ветки подписи намеренно (ревью PR #19): пока он был
 * продублирован, пустую ветку не стерёг ни один тест, а отступ вложенности
 * сужает колонку с глубиной — фиксированная строка тоже может не поместиться.
 */
const OWN_CAPTION_CLASS = "whitespace-normal break-words text-2xs text-fg-tertiary";

/**
 * Подпись служебной строки собственных денег статьи (правило 6 §2.9), теперь
 * с бейджем «вручную» у разделов, чья статья назначена решением, а не файлом
 * (задача 9, спека §2.10: паспорт идёт в банк и не должен выдавать решение
 * аналитика за содержимое файла — бейдж ПЕЧАТАЕТСЯ, `data-print="hide"` на нём
 * нет).
 *
 * Строится ИЗ `own_sections`, а не из кода статьи и не из номера раздела файла:
 * нумерация файла и коды классификатора — разные оси (§1.5 спеки, находка
 * «строка 6.4 несёт статью 6.6»). Пустой список — узел без известных разделов,
 * печатается нейтральный текст мокапа, а не догадка по номеру.
 *
 * **Почему бейдж оборачивает только `source === "manual"`, а не каждый
 * раздел.** Раньше список был ОДНОЙ строкой — бейдж к строке не приклеить,
 * поэтому раздел нужно стало рендерить элементом. Но если оборачивать элементом
 * КАЖДЫЙ раздел (включая файловые), текст «расползается» по нескольким узлам
 * DOM, и три уже существующих теста (проверяющие ровно эту подпись как ОДНУ
 * строку через `findByText`/`queryByText` с regex) находят «text broken up by
 * multiple elements» и падают — `getByText` у testing-library склеивает только
 * ПРЯМЫЕ текстовые узлы элемента, вложенные элементы в это склеивание не
 * попадают. React прозрачно разворачивает `<Fragment>` в родителя, поэтому
 * файловый раздел, оставленный обычной строкой внутри фрагмента, остаётся
 * прямым текстовым узлом `<p>` — старые тесты не видят разницы. Ручной раздел,
 * обёрнутый `<span>` ради бейджа и тестового id, из этого склеивания выходит —
 * но старые тесты его текст и не проверяют (единственная фикстурная запись с
 * `source: "manual"` — у статьи "10", которую они не трогают).
 */
function OwnSectionsCaption({
  code,
  sections,
}: {
  code: string;
  sections: ProjectPassportSection[];
}) {
  if (sections.length === 0) {
    return (
      <p data-testid={`own-caption-${code}`} className={OWN_CAPTION_CLASS}>
        позиции, привязанные прямо к этой статье
      </p>
    );
  }
  const word = sections.length === 1 ? "раздел сметы" : "разделы сметы";
  return (
    <p data-testid={`own-caption-${code}`} className={OWN_CAPTION_CLASS}>
      {word}{" "}
      {sections.map((section, index) => (
        <Fragment key={section.id}>
          {index > 0 && ", "}
          {section.source === "manual" ? (
            // Testid называет то, что элемент РЕАЛЬНО помечает (ревью 1,
            // finding 6): он существует только для `source: "manual"` —
            // файловые разделы того же списка остаются простой строкой
            // (см. докстроку `OwnSectionsCaption`), и `own-section-{id}` без
            // суффикса обещал бы универсальный per-section-узел, которого нет.
            <span data-testid={`own-section-manual-${section.id}`}>
              {sectionLabel(section)}{" "}
              <Badge variant="outline">вручную</Badge>
            </span>
          ) : (
            sectionLabel(section)
          )}
        </Fragment>
      ))}
    </p>
  );
}

/**
 * Подпись неполноты узла (правило 2 §2.3, спека): стоит, когда `rowsPriced <
 * rows`, и называет ОБЕ причины отдельно — пустую цену и негодное значение.
 * Текст, называющий только одну причину, был бы уже собственного условия
 * (урок Ф4a).
 */
function incompletenessCaption(
  rows: number,
  rowsPriced: number,
  rowsNotFinite: number
): string | null {
  if (rowsPriced >= rows) return null;
  const missingPrice = rows - rowsPriced - rowsNotFinite;
  const parts: string[] = [];
  if (missingPrice > 0) parts.push(`без цены: ${missingPrice}`);
  if (rowsNotFinite > 0) parts.push(`с ошибкой: ${rowsNotFinite}`);
  return parts.length > 0 ? parts.join(", ") : null;
}

/**
 * Подпись «Нераспределённого» (правило 8 §2.9, расширено задачей 9 третьей
 * причиной — граница §5.5). Три причины одного следствия, не взаимозаменимые
 * (§2.2 спеки: «поправить привязку раздела» ≠ «строка сиротлива» ≠ «допработа
 * без разрешимой статьи»), названы отдельно.
 *
 * База строится из ПЕРВОЙ непустой причины, а НЕ всегда из `chapters`: при
 * разнесённых разделах (`chapters === 0`) старая безусловная база сказала бы
 * «0 разделов сметы без статьи классификатора» — назвала бы причину, которой
 * уже нет, вместо действующей (внестроечных позиций или допработ). Каждая
 * следующая непустая причина дописывается через «отдельно — …».
 *
 * Третья причина (допработы, `unallocated.extras`) закрывает случай, невидимый
 * ни в дереве `sections` (которое даёт `chapters`), ни в `rows_outside_structure`:
 * без неё экран объявил бы «всё разнесено» при непустом «Нераспределённое» в
 * той же строке.
 *
 * **`unallocated.extras` ШИРЕ границы §5.5, и текст третьей причины обязан
 * оставаться верным для всех, кто в неё попал** (ревью 1, finding 1, Ruling 3).
 * Третий случай границы §5.5 — это ГЕНУИННО неразрешимые строки: `resolve_ref`
 * (`backend/services/additional_works.py`) возвращает их с причиной «нет
 * кандидатов» или «статьи различаются». Но `unallocated.extras` — это КАЖДАЯ
 * строка допработы без статьи (`work_category_id IS NULL`), включая ту, чей
 * раздел просто ЕЩЁ не получил статью («кандидат без статьи») — а такая строка
 * перестаёт быть нераспределённой САМА, как только аналитик назначит статью её
 * разделу (`backend/services/category_override.py::_materialize_extras`
 * пересчитывает `extras` при разносе). Ответ сервера не несёт причину отказа
 * (`resolve_ref` её не публикует), и экран поэтому не может отличить один
 * случай от другого — текст называет СОСТОЯНИЕ строки (без статьи, статья
 * приезжает из раздела, на который она ссылается) и её причину назначения
 * (напрямую строке статью не поставить), а НЕ утверждает неустранимость: для
 * большинства реальных допработ она устранима самим экраном, на котором стоит
 * эта подпись.
 *
 * Ноль всех трёх причин — нулевое состояние: печатается фраза цели, а не
 * пустая строка и не «0 …».
 */
function unallocatedCaption(unallocated: ProjectPassportUnallocated): string {
  const causes: string[] = [];

  if (unallocated.chapters > 0) {
    const chapterWord =
      unallocated.chapters === 1
        ? "раздел"
        : unallocated.chapters % 10 >= 2 &&
            unallocated.chapters % 10 <= 4 &&
            (unallocated.chapters % 100 < 10 || unallocated.chapters % 100 >= 20)
          ? "раздела"
          : "разделов";
    causes.push(`${unallocated.chapters} ${chapterWord} сметы без статьи классификатора`);
  }

  if (unallocated.rows_outside_structure > 0) {
    const rowsWord = unallocated.rows_outside_structure === 1 ? "позиция" : "позиций";
    causes.push(
      `${unallocated.rows_outside_structure} ${rowsWord} вне структуры сметы (без ссылки на раздел)`
    );
  }

  if (unallocated.extras.length > 0) {
    // Ревью 1, Ruling 3: старый текст («…разносу недоступны») был ложным для
    // САМОГО частого случая — «кандидат без статьи» (раздел существует, ему
    // просто ещё не назначена статья) устраняется тем же экраном. Новая
    // формулировка называет СОСТОЯНИЕ (нет статьи) и его причину (статья
    // приезжает из раздела-ссылки), не утверждая неустранимость — верно для
    // обеих причин `resolve_ref` сразу, «кандидат без статьи» и генуинно
    // неразрешимых («нет кандидатов», «статьи различаются»), см. докстроку
    // выше. Число — ПОСЛЕ двоеточия внутри пояснения, а не перед словом-счётом
    // (та же причина, что и раньше: не заводить новый помощник согласования —
    // решение задачи 6), и заголовок причины теперь СУЩЕСТВИТЕЛЬНОЕ, как у двух
    // соседних причин (ревью 1, finding 8: раньше это было отдельное
    // предложение со своим тире, что ломало регистр внутри одной подписи).
    causes.push(
      `строки допработ без статьи (статья приезжает из раздела, на который ` +
        `они ссылаются; таких строк: ${unallocated.extras.length})`
    );
  }

  if (causes.length === 0) return "все разделы сметы отнесены к статьям";

  const [base, ...rest] = causes;
  return rest.length === 0
    ? base
    : `${base}; ${rest.map((cause) => `отдельно — ${cause}`).join("; ")}`;
}

function ExpandToggle({
  expandable,
  expanded,
  code,
  onToggle,
  label,
}: {
  expandable: boolean;
  expanded: boolean;
  code?: string;
  onToggle: () => void;
  /**
   * Переопределяет `aria-label` целиком — по умолчанию
   * `Развернуть/Свернуть статью ${code}`. Нужно строке «Нераспределённое»:
   * она не статья классификатора, и стандартная подпись называла бы её
   * неверно. Существующие вызовы (статьи дерева) label не передают и держат
   * прежнюю подпись без изменений.
   */
  label?: string;
}) {
  if (!expandable) {
    return <span className="inline-block size-3.5 shrink-0" aria-hidden="true" />;
  }
  return (
    <button
      type="button"
      // Кнопка раскрытия — управление, а не часть документа: на бумаге шеврон
      // ничего не значит, потому что раскрыть там нечего (спека §2.11
      // «служебные элементы уходят»). CSS скрывает ровно то, что помечено.
      data-print="hide"
      aria-expanded={expanded}
      aria-label={label ?? `${expanded ? "Свернуть" : "Развернуть"} статью ${code}`}
      onClick={onToggle}
      className="shrink-0 text-fg-tertiary hover:text-fg"
    >
      <ChevronRight
        aria-hidden="true"
        className={cn("size-3.5 transition-transform", expanded && "rotate-90")}
      />
    </button>
  );
}

function CategoryRow({
  node,
  depth,
  expandedIds,
  onToggle,
  showZero,
}: {
  node: CategoryNode;
  depth: number;
  expandedIds: Set<number>;
  onToggle: (id: number) => void;
  showZero: boolean;
}) {
  const isOpen = expandedIds.has(node.id);
  const hasChildren = node.children.length > 0;
  const hasExtras = node.extras.length > 0;
  // Ревью 1, Ruling 2: черновик задачи 9 расширял это условие третьим
  // слагаемым (`own_sections.length > 0`), чтобы дать разворот листовой
  // статье "10" — единственной в фикстуре с `source: "manual"`. Отменено:
  // `_own_sections_select` (backend/crud/project_passport.py) заполняет
  // `own_sections` для КАЖДОЙ статьи, владеющей разделом со своими позициями,
  // а не только для статей с ручным решением — на реальных данных это дало бы
  // шеврон каждой листовой статье с деньгами и служебную строку, повторяющую
  // уже показанный итог. Это смена поведения фазы-7 экрана, которую никто не
  // согласовывал, и задача 9 в ней не нуждается: пометка «вручную» теперь
  // стоит на строке самой статьи (см. `hasManualOwnSection` ниже), которая
  // рендерится всегда — разворачивать узел, чтобы её увидеть, больше не нужно.
  const expandable = hasChildren || hasExtras;
  const ownRowVisible = expandable && node.own_rows > 0;
  // Пометка «вручную» на строке статьи (задача 9, Ruling 1 ревью 1). Стоит
  // здесь, а не только в служебной строке `own_sections` ниже: та рендерится
  // ТОЛЬКО при развороте (`isOpen`), а в `index.css` нет печатного правила,
  // которое разворачивало бы дерево — печатный слой лишь СКРЫВАЕТ элементы с
  // `data-print="hide"`, ничего не раскрывает. Строка статьи рендерится всегда,
  // поэтому только пометка на НЕЙ гарантированно попадает на бумагу. Условие —
  // «есть ХОТЯ БЫ ОДИН раздел с source: manual», а не «весь own_sections —
  // ручной»: категория может держать смесь файловых и ручных разделов
  // (фикстура «04» несёт два файловых, но реальные данные могут смешивать), и
  // пометка обязана появиться, если решение затронуло хотя бы один из них.
  const hasManualOwnSection = node.own_sections.some((section) => section.source === "manual");
  const incompleteness = incompletenessCaption(node.rows, node.rows_priced, node.rows_not_finite);
  // Те же три правила действуют и для СОБСТВЕННЫХ денег статьи — по own_rows,
  // own_rows_priced и own_rows_not_finite (спека §2.3, последний абзац). Без
  // этого служебная строка показывала бы частичную сумму числом и без единого
  // признака неполноты — ровно то, что §2.3 объявляет недопустимым для узла.
  const ownIncompleteness = incompletenessCaption(
    node.own_rows,
    node.own_rows_priced,
    node.own_rows_not_finite
  );
  const indent = 8 + depth * 20;

  return (
    <>
      <TableRow
        data-testid={`row-cat-${node.code}`}
        data-print="row"
        className={depth === 0 ? "border-t border-border-subtle" : undefined}
      >
        <TableCell className="font-mono text-xs text-fg-secondary">{node.code}</TableCell>
        <TableCell>
          <div className="flex min-w-0 items-start gap-2" style={{ paddingLeft: indent }}>
            <ExpandToggle
              expandable={expandable}
              expanded={isOpen}
              code={node.code}
              onToggle={() => onToggle(node.id)}
            />
            <div className="min-w-0">
              <div className="flex flex-wrap items-baseline gap-1.5">
                <span
                  data-print="clamp"
                  className={cn("line-clamp-2 min-w-0", depth === 0 && "font-semibold text-fg")}
                >
                  {node.title}
                </span>
                {node.is_bucket && <Badge variant="outline">корзина</Badge>}
                {/*
                  Пометка на строке — та же причина, что у бейджа в служебной
                  строке ниже (§2.10: паспорт идёт в банк и не должен выдавать
                  наше решение за содержимое файла), но здесь она ПЕЧАТАЕТСЯ
                  без разворота (см. `hasManualOwnSection` выше). Не заменяет
                  собой бейдж служебной строки — тот называет, КАКОЙ раздел
                  решён, этот — что решение затронуло статью вообще; узел
                  может держать смесь файловых и ручных разделов, и только по
                  этой пометке не различить, какие именно.
                */}
                {hasManualOwnSection && <Badge variant="outline">вручную</Badge>}
              </div>
              {incompleteness && (
                <p data-testid={`incompleteness-cat-${node.code}`} className="mt-0.5 text-2xs text-fg-tertiary">
                  {incompleteness}
                </p>
              )}
            </div>
          </div>
        </TableCell>
        <TableCell data-testid={`amount-cat-${node.code}`} className="text-right">
          <MoneyCell value={node.total} className={depth === 0 ? "font-semibold" : undefined} />
        </TableCell>
        <TableCell
          data-testid={`share-cat-${node.code}`}
          className="text-right text-xs text-fg-secondary"
        >
          {formatSharePct(node.share_pct)}
        </TableCell>
        {/*
          ₽/м² — ВЫЧИСЛЕННАЯ величина: деление `Decimal` на `Decimal` даёт 28
          значащих цифр, и лишние знаки — артефакт деления, а не данные. Ровно
          тот случай, для которого `maxFractionDigits` у `MoneyCell` и заведён
          (его докстрока: «только для вычисленных величин»); точное значение
          уходит в `title`. Округление стоит на слое показа, а не в CRUD — то же
          правило, что §4 AGENTS.md держит для `deviation_pct`.
        */}
        <TableCell data-testid={`per-sqm-cat-${node.code}`} className="text-right">
          <MoneyCell value={node.per_sqm} maxFractionDigits={2} />
        </TableCell>
        <RateColumns
          code={node.code}
          unit={node.unit}
          volume={node.volume}
          unitRate={node.unit_rate}
          rateState={node.rate_state}
          rateNote={node.rate_note}
        />
      </TableRow>

      {isOpen && (
        <>
          {node.children.map((child) => {
            const hiddenAsZero =
              !showZero && child.total !== null && isZeroDecimal(child.total);
            if (hiddenAsZero) return null;
            return (
              <CategoryRow
                key={child.id}
                node={child}
                depth={depth + 1}
                expandedIds={expandedIds}
                onToggle={onToggle}
                showZero={showZero}
              />
            );
          })}

          {node.extras.map((extra) => (
            <TableRow key={extra.id} data-testid={`row-extra-${node.code}-${extra.id}`} data-print="row">
              <TableCell className="font-mono text-xs text-fg-tertiary">·</TableCell>
              <TableCell>
                <div className="flex items-baseline gap-2" style={{ paddingLeft: indent + 20 }}>
                  <span className="inline-block size-3.5 shrink-0" aria-hidden="true" />
                  <span className="italic text-fg-secondary">{extra.title}</span>
                  <Badge variant="outline" className="border-accent-border bg-accent-soft text-accent-text">
                    доп. работы
                  </Badge>
                </div>
              </TableCell>
              <TableCell className="text-right">
                <MoneyCell value={extra.amount} />
              </TableCell>
              <TableCell className="text-right text-fg-tertiary">—</TableCell>
              <TableCell className="text-right text-fg-tertiary">—</TableCell>
              <RateColumnsDash />
            </TableRow>
          ))}

          {ownRowVisible && (
            <TableRow data-testid={`row-own-${node.code}`} data-print="row">
              <TableCell className="font-mono text-xs text-fg-tertiary">·</TableCell>
              <TableCell>
                <div className="flex items-baseline gap-2" style={{ paddingLeft: indent + 20 }}>
                  <span className="inline-block size-3.5 shrink-0" aria-hidden="true" />
                  <div className="min-w-0">
                    <span className="italic text-fg-secondary">
                      {hasChildren ? "Без подстатьи" : "Позиции сметы"}
                    </span>
                    <OwnSectionsCaption code={node.code} sections={node.own_sections} />
                    {ownIncompleteness && (
                      <p
                        data-testid={`own-incompleteness-${node.code}`}
                        className="text-2xs text-fg-tertiary"
                      >
                        {ownIncompleteness}
                      </p>
                    )}
                  </div>
                </div>
              </TableCell>
              <TableCell className="text-right">
                <MoneyCell value={node.own} />
              </TableCell>
              <TableCell className="text-right text-fg-tertiary">—</TableCell>
              <TableCell className="text-right text-fg-tertiary">—</TableCell>
              <RateColumnsDash />
            </TableRow>
          )}
        </>
      )}
    </>
  );
}

export function CategoryTable({
  passport,
  contractId,
}: {
  passport: ProjectPassport;
  /**
   * Из маршрута (`ProjectPassportPage`), НЕ из `passport.contract.id` — запрос
   * паспорта ключуется id маршрута, и мутации разноса инвалидируют тот же
   * ключ (`qk.passport.project`). Два источника одного значения — ровно то,
   * из-за чего инвалидация тихо перестаёт совпадать (task-8-controller-notes).
   */
  contractId: number;
}) {
  const { categories, unallocated, totals, manual_assignments } = passport;
  const [expandedIds, setExpandedIds] = useState<Set<number>>(() => new Set());
  // Правило 12 §2.9: переключатель по умолчанию ВЫКЛЮЧЕН — нулевые подстатьи
  // скрыты, пока пользователь их не запросит явно.
  const [showZero, setShowZero] = useState(false);
  const zeroToggleId = useId();
  // Разворот панели-верстака разноса (задача 8) — своё состояние, не часть
  // `expandedIds`: у «Нераспределённого» нет id статьи классификатора.
  const [unallocatedOpen, setUnallocatedOpen] = useState(false);

  const roots = useMemo(() => buildCategoryTree(categories), [categories]);
  // `ProjectPassportPage` рендерит эту таблицу только после проверки
  // `passport.estimate !== null` — но тип `ProjectPassportEstimate | null`
  // об этом не знает здесь. Без сметы панель разноса не открыть: разносить
  // нечем (нет `estimateId` для мутаций).
  const estimateId = passport.estimate?.id;
  /**
   * Нулевое состояние строки «Нераспределённое» (задача 9, спека §5.5) — ВСЕ
   * ТРИ слагаемых сразу, а не только дерево разноса: спека §5.5 называет три
   * вещи, которые эта фича не умеет разнести — позиции вне структуры файла,
   * предложение с выключенной нумерацией разделов и допработы, чья статья
   * приезжает из раздела, на который они ссылаются (то есть у самой строки
   * органа разноса нет и не может быть — статьёй распоряжается раздел, не
   * строка; ревью 1, Ruling 3: это НЕ то же самое, что «неразрешимая ссылка»,
   * `unallocated.extras` шире границы §5.5, см. докстроку `unallocatedCaption`).
   * Третье слагаемое (`extras.length`) закрывает случай, невидимый в первых
   * двух: такая строка живёт в `unallocated.extras`, но её не видно ни в
   * `sections` (откуда берётся `chapters`), ни в `rows_outside_structure`. Без
   * него экран объявил бы «всё разнесено» при непустом «Нераспределённое» в
   * той же строке.
   */
  const allocated =
    unallocated.sections.length === 0 &&
    unallocated.rows_outside_structure === 0 &&
    unallocated.extras.length === 0;
  /*
    Ревью 1, finding 5 (не смена логики — только имя инварианта, который
    сегодня держит правильность обоих значений). Этот предикат читает
    `unallocated.sections.length` (буквальный предикат брифа); подпись
    `unallocatedCaption` читает `unallocated.chapters` для той же самой
    первой причины. Сегодня это ОДНО И ТО ЖЕ ФАКТИЧЕСКИ: backend держит
    `sections.length === 0 ⟺ chapters === 0` (`chapters` считает узлы без own
    строк — `rows > 0` — а `_unallocated_sections`
    (`backend/crud/project_passport.py`) оставляет в дереве только узлы с
    непустым унаследованным сводом, тем же признаком). Если backend когда-нибудь
    станет держать в `sections` узлы с `rows === 0` (например, чтобы показывать
    пустые главы в дереве разноса), это равенство сломается: строка красной
    рамкой предупреждения покажет предупреждение, а подпись рядом напишет «все
    разделы сметы отнесены к статьям» — та же противоречивая печатная страница,
    которую нулевое состояние обязано исключать. Единое поле для обоих или
    явный тест на равенство `sections.length === 0` и `chapters === 0` закрыл
    бы разрыв — сейчас его закрывает только этот комментарий.
  */

  function toggle(id: number) {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    // data-print="sheet" НЕ здесь: он на корне документа (ProjectPassportPage,
    // задача 10) — таблица лишь часть листа, а не лист целиком.
    <section className="border border-border-subtle bg-surface">
      {/*
        Метка стоит на ВСЕЙ полосе, а не на одном переключателе: пометив только
        сам `Switch`, мы бы убрали с бумаги орган управления и оставили висеть
        его осиротевшую подпись «показывать нулевые подстатьи» вместе с
        разделительной чертой — то есть напечатали бы половину служебного
        элемента.
      */}
      <div
        data-print="hide"
        className="flex items-center justify-end gap-2 border-b border-border-subtle px-6 py-2.5"
      >
        <Label htmlFor={zeroToggleId} className="text-xs font-normal text-fg-secondary">
          показывать нулевые подстатьи
        </Label>
        <Switch id={zeroToggleId} checked={showZero} onCheckedChange={setShowZero} />
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-16 pl-6">Код</TableHead>
            <TableHead>Статья классификатора</TableHead>
            <TableHead className="text-right">Итого, ₽</TableHead>
            <TableHead className="text-right">Доля</TableHead>
            <TableHead className="text-right">₽ / м²</TableHead>
            <TableHead className="text-right">Ед.</TableHead>
            <TableHead className="text-right">Объём</TableHead>
            <TableHead className="text-right pr-6">Ставка, ₽/ед.</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {roots.map((root) => (
            <CategoryRow
              key={root.id}
              node={root}
              depth={0}
              expandedIds={expandedIds}
              onToggle={toggle}
              showZero={showZero}
            />
          ))}

          <TableRow
            data-testid="row-unallocated"
            data-print="row"
            className={cn(
              "border-y",
              allocated ? "border-border-subtle" : "border-warning-border bg-warning-soft"
            )}
          >
            {/*
              Глиф ячейки — часть утверждения «есть проблема» ДЛЯ ЗРЯЧЕГО
              читателя, не декорация: ⚠ рядом с «все разделы сметы отнесены к
              статьям» противоречил бы собственному тексту строки. В нулевом
              состоянии он меняется на ✓, а не просто гаснет — молчание тоже
              было бы не сообщением, а недомолвкой о том, что цель достигнута.

              Для скринридера — наоборот, `aria-hidden` (ревью 1, finding 10):
              и заголовок «Нераспределённое», и подпись `unallocated-caption`
              рядом уже произносят СЛОВАМИ то же самое различие («все разделы
              сметы отнесены к статьям» либо названную причину остатка) —
              озвучивать вслепую «предупреждающий знак»/«галочка» без слов было
              бы вторым, более скудным сообщением о том же факте, а не новой
              информацией.
            */}
            <TableCell
              data-testid="unallocated-status"
              aria-hidden="true"
              className={allocated ? "text-fg-secondary" : "text-warning-text"}
            >
              {allocated ? "✓" : "⚠"}
            </TableCell>
            <TableCell>
              <div className="flex min-w-0 items-start gap-2">
                <ExpandToggle
                  expandable
                  expanded={unallocatedOpen}
                  onToggle={() => setUnallocatedOpen((prev) => !prev)}
                  label={unallocatedOpen ? "Свернуть нераспределённое" : "Развернуть нераспределённое"}
                />
                <div className="min-w-0">
                  <p className={cn("font-semibold", allocated ? "text-fg" : "text-warning-text")}>
                    Нераспределённое
                  </p>
                  <p
                    data-testid="unallocated-caption"
                    className={cn("text-2xs", allocated ? "text-fg-secondary" : "text-warning-text")}
                  >
                    {unallocatedCaption(unallocated)}
                  </p>
                </div>
              </div>
            </TableCell>
            <TableCell data-testid="amount-unallocated" className="text-right">
              <MoneyCell
                value={unallocated.amount}
                className={allocated ? undefined : "text-warning-text"}
              />
            </TableCell>
            <TableCell
              data-testid="share-unallocated"
              className={cn("text-right text-xs", allocated ? "text-fg-secondary" : "text-warning-text")}
            >
              {formatSharePct(unallocated.share_pct)}
            </TableCell>
            <TableCell data-testid="per-sqm-unallocated" className="text-right">
              <MoneyCell
                value={unallocated.per_sqm}
                maxFractionDigits={2}
                className={allocated ? undefined : "text-warning-text"}
              />
            </TableCell>
            <RateColumnsDash />
          </TableRow>

          {/*
            Панель-верстак разноса (задача 8) — своя строка на всю ширину, а
            не пятая колонка «Нераспределённого»: в раскладке макета селектор
            статьи влезает только на место «Доля» и «₽/м²», а автору, дате и
            кнопке «снять» места нет вовсе.
          */}
          {unallocatedOpen && estimateId !== undefined && (
            <TableRow data-testid="row-unallocated-panel" data-print="hide">
              <TableCell colSpan={8} className="p-0">
                <UnallocatedPanel passport={passport} contractId={contractId} estimateId={estimateId} />
              </TableCell>
            </TableRow>
          )}

          <TableRow data-testid="row-grand-total" data-print="row" className="border-t-2 border-fg">
            <TableCell />
            <TableCell className="font-serif text-base font-semibold text-fg">
              Итого по договору
            </TableCell>
            <TableCell className="text-right">
              <MoneyCell value={totals.amount} className="font-semibold" />
            </TableCell>
            {/*
              Доля итога — тавтологические 100 %, но ТОЛЬКО когда итог вообще
              пригоден как знаменатель. Литерал «100,00 %» здесь означал бы
              «сто процентов от неизвестной суммы» при пустом итоге и «сто
              процентов от нуля» при нулевом — ровно то, что правило 5 §2.6
              запрещает всем остальным строкам, чей `share_pct` в этих случаях
              приходит `null`. Прочерк ставится по тому же признаку, что и у
              них: сумма непригодна как знаменатель.
            */}
            <TableCell className="text-right text-sm font-semibold text-fg">
              {totals.amount === null || isZeroDecimal(totals.amount) ? "—" : "100,00 %"}
            </TableCell>
            <TableCell data-testid="per-sqm-grand-total" className="text-right">
              <MoneyCell
                value={totals.per_sqm}
                maxFractionDigits={2}
                className="font-semibold"
              />
            </TableCell>
            <RateColumnsDash />
          </TableRow>
        </TableBody>
      </Table>

      {/*
        Печатная сноска (задача 9, спека §2.10) — называет ЧИСЛО действующих
        решений и намеренно НЕ называет сумму: решения вложены друг в друга, и
        `subtree_amount` внешнего решения уже включает деньги внутреннего —
        сложение задвоило бы их. Подсчёт по строкам с эффективной статьёй
        `manual` эту задвойку не задваивал бы, но потерял бы допработы: у них
        `category_source` нет вовсе (их статья приезжает из раздела, на который
        они ссылаются, а не из собственного решения). Любое единственное число
        было бы либо раздутым, либо неполным — поэтому сноска несёт только
        счётчик. Печатается (без `data-print="hide"`) по той же причине, что и
        бейдж «вручную»: паспорт уходит в банк и не должен скрывать, что часть
        статей — наше решение, а не содержимое файла. Стоит только при
        непустом списке — печатать «0 решений» было бы утверждением о решении,
        которого не было.
      */}
      {manual_assignments.length > 0 && (
        <p
          data-testid="manual-footnote"
          className="border-t border-border-subtle px-6 py-2 text-2xs text-fg-tertiary"
        >
          Действующих ручных решений о статье раздела: {manual_assignments.length}
        </p>
      )}
    </section>
  );
}
