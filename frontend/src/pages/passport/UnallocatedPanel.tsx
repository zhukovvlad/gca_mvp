import {
  UnallocatedWorkbench,
  type WorkbenchCopy,
  type WorkbenchManualRow,
  type WorkbenchSection,
} from "@/components/unallocated/UnallocatedWorkbench";
import { MoneyCell } from "@/components/ui-domain/MoneyCell";
import { useClearCategoryOverride, useSetCategoryOverride } from "@/services/queries";
import type {
  ProjectPassport,
  ProjectPassportCategoryOption,
  ProjectPassportManualAssignment,
  ProjectPassportUnallocatedSection,
} from "@/types/domain";

/**
 * Панель-верстак разноса — разворачивается под строкой «Нераспределённое»
 * таблицы по статьям (спека разноса §2.6; план, задача 8).
 *
 * Монтирует и разворачивает панель `CategoryTable` (её тесты — в
 * `ProjectPassportPage.test.tsx`, там же и разводка `contractId`/`estimateId`);
 * этот компонент — только содержимое панели, всегда «развёрнутое», как только
 * смонтировано.
 *
 * Тонкая обёртка над презентационным ядром `UnallocatedWorkbench`
 * (`components/unallocated/UnallocatedWorkbench.tsx`, план — задача 10):
 * ядро несёт разметку дерева, popover выбора статьи и список ручных решений,
 * здесь — только маппинг `ProjectPassport` в его пропсы, две денежные колонки
 * и мутации по-сметного маршрута. Имя, пропсы, testid, тексты и сортировка —
 * ПОСИМВОЛЬНО прежние (§3 п.5 спеки этапного разноса): `UnallocatedPanel.test.tsx`
 * не правился при выделении ядра.
 *
 * Два блока с ОДНОЙ и ТОЙ же по имени, но РАЗНОЙ по смыслу колонкой денег
 * (замечание ревью задачи 4, актуальное и здесь):
 *
 * - «Разделы без статьи» — `unallocated.sections[]`. `subtree_amount` там
 *   сворачивает только НЕРАСПРЕДЕЛЁННУЮ часть поддерева: резолвер приоритета
 *   LOCAL, и потомок со своей файловой статьёй ничего не наследует от
 *   вручную решённого предка.
 * - «Разнесено вручную» — `manual_assignments[]`. `subtree_amount` там —
 *   ПОЛНАЯ файловая свёртка раздела, без обрезки по внутренним решениям.
 *
 * Числа из разных блоков НЕ складываются и не сравнимы «по вершине» — оба
 * блока подписывают свою колонку явно, чтобы это не подразумевалось само собой.
 */
export function UnallocatedPanel({
  passport,
  contractId,
  estimateId,
}: {
  passport: ProjectPassport;
  contractId: number;
  estimateId: number;
}) {
  const setOverride = useSetCategoryOverride();
  const clearOverride = useClearCategoryOverride();
  const disabled = setOverride.isPending || clearOverride.isPending;

  const sections = passport.unallocated.sections.map(toPassportSection);
  const manual = passport.manual_assignments.map(toPassportManualRow);

  function pickCategory(section: PassportSection, option: ProjectPassportCategoryOption) {
    setOverride.mutate({
      contractId,
      estimateId,
      positionItemId: section.positionItemId,
      workCategoryId: option.id,
    });
  }

  function clearAssignment(row: PassportManualRow) {
    clearOverride.mutate({ contractId, estimateId, positionItemId: row.positionItemId });
  }

  return (
    // Служебная зона разноса, не часть документа — на бумаге разносить нечем
    // (те же причины, что у ExpandToggle/переключателя нулевых в CategoryTable).
    // Корень СТАЁТ здесь, а не в ядре: тест «панель помечена data-testid и не
    // попадает в печатный поток» проверяет именно этот элемент.
    <section data-testid="unallocated-panel" data-print="hide" className="bg-surface">
      <UnallocatedWorkbench
        sections={sections}
        manual={manual}
        categoryOptions={passport.category_options}
        copy={PASSPORT_COPY}
        testId={(section) => section.key}
        compareSiblings={compareBySubtreeDesc}
        renderAside={renderSectionAside}
        renderManualAside={renderManualAside}
        // По-сметный маршрут заметку с экрана не принимает — то же поведение,
        // что и до выделения ядра (спека этапного разноса §3 п.5).
        noteField={false}
        onPick={(section, option) => pickCategory(section, option)}
        onClear={clearAssignment}
        disabled={disabled}
      />
    </section>
  );
}

const PASSPORT_COPY: WorkbenchCopy = {
  sectionsHeading: "Разделы без статьи",
  sectionsHint:
    "Сумма — только нераспределённая часть поддерева; строки со своей файловой " +
    "статьёй в неё не входят.",
  sectionsEmpty: "Разделов без статьи не осталось.",
  manualHeading: "Разнесено вручную",
  manualHint: "Сумма — полная файловая свёртка раздела, без разбора вложенных решений.",
  manualEmpty: "Ручных решений пока нет.",
};

// ---------------------------------------------------------------------------
//  Маппинг ProjectPassport → пропсы UnallocatedWorkbench
// ---------------------------------------------------------------------------

interface PassportSection extends WorkbenchSection {
  positionItemId: number;
  amount: string | null;
  subtreeAmount: string | null;
}

function toPassportSection(section: ProjectPassportUnallocatedSection): PassportSection {
  return {
    key: String(section.position_item_id),
    parentKey:
      section.parent_position_item_id === null ? null : String(section.parent_position_item_id),
    number: section.number,
    title: section.title,
    smr_article_raw: section.smr_article_raw,
    positionItemId: section.position_item_id,
    amount: section.amount,
    subtreeAmount: section.subtree_amount,
  };
}

interface PassportManualRow extends WorkbenchManualRow {
  positionItemId: number;
  subtreeAmount: string | null;
}

function toPassportManualRow(assignment: ProjectPassportManualAssignment): PassportManualRow {
  return {
    key: String(assignment.position_item_id),
    number: assignment.number,
    title: assignment.title,
    category_code: assignment.category_code,
    category_title: assignment.category_title,
    assigned_by_email: assignment.assigned_by_email,
    assigned_at: assignment.assigned_at,
    note: assignment.note,
    positionItemId: assignment.position_item_id,
    subtreeAmount: assignment.subtree_amount,
  };
}

// ---------------------------------------------------------------------------
//  Сортировка сиблингов — по убыванию subtree_amount (задача 8, ревью 1)
// ---------------------------------------------------------------------------

/** Десятичное число в виде строки: `-?цифры[.цифры]` — тот же разбор, что у
 *  `src/lib/decimal.ts`; отдельная копия, а не импорт: там нет функции
 *  сравнения, а заводить её ради одного места использования — лишнее. */
const DECIMAL_RE = /^(-?)(\d+)(?:\.(\d+))?$/;

/**
 * Сравнивает две decimal-строки точно, целыми числами (`BigInt`), без
 * перевода в `number` — тот же приём, что у `addDecimalStrings`
 * (AGENTS.md §3: деньги — `Decimal`-строки, `Number()` теряет разряды).
 *
 * Неразбираемый вход считается равным — вызывающий (`compareBySubtreeDesc`)
 * получает данные с сервера, где `subtree_amount` либо `Decimal`, либо `null`,
 * и `null` эта функция никогда не видит: его отсекает вызывающий раньше.
 */
function compareDecimalStrings(a: string, b: string): number {
  const pa = DECIMAL_RE.exec(a.trim());
  const pb = DECIMAL_RE.exec(b.trim());
  if (!pa || !pb) return 0;

  const scale = Math.max(pa[3]?.length ?? 0, pb[3]?.length ?? 0);
  const toBigInt = (m: RegExpExecArray) => {
    const digits = BigInt(`${m[2]}${(m[3] ?? "").padEnd(scale, "0")}`);
    return m[1] === "-" ? -digits : digits;
  };

  const diff = toBigInt(pa) - toBigInt(pb);
  return diff < 0n ? -1 : diff > 0n ? 1 : 0;
}

/** По убыванию `subtree_amount`; `null` — в конец; ничья — по возрастанию
 *  `position_item_id`, чтобы порядок был детерминирован (controller-notes). */
function compareBySubtreeDesc(a: PassportSection, b: PassportSection): number {
  if (a.subtreeAmount === null && b.subtreeAmount === null) {
    return a.positionItemId - b.positionItemId;
  }
  if (a.subtreeAmount === null) return 1;
  if (b.subtreeAmount === null) return -1;

  const cmp = compareDecimalStrings(a.subtreeAmount, b.subtreeAmount);
  return cmp !== 0 ? -cmp : a.positionItemId - b.positionItemId;
}

// ---------------------------------------------------------------------------
//  Правая колонка — деньги (задача 9: manual-amount несёт subtree_amount)
// ---------------------------------------------------------------------------

/*
  ДВЕ суммы, не выбор одной из двух (правка ревью 1, finding C-1): спека §1.5
  — «у узла нужны две суммы — своя и по поддереву», та же пара, что у статей
  классификатора различает `own`/`total`. Свёртка поддерева — ПЕРВИЧНАЯ и
  всегда на месте (её и защищает testid `subtree-amount-{id}`, независимо от
  того, известно значение или нет — тот же принцип, что у `amount-cat-*` в
  CategoryTable: имя testid называет ПОЛЕ, а не факт его наличия, поэтому `—`
  под этим testid не значит подмену смысла, finding M-2). Своя сумма — ВТОРАЯ
  строка, и появляется только когда известна: без неё узел со своими
  деньгами и большим нераспределённым поддеревом ранжировался бы по одному
  числу («цена решения»), а показывал другое.
*/
function renderSectionAside(section: PassportSection) {
  return (
    <div className="text-right">
      <span data-testid={`subtree-amount-${section.key}`}>
        <MoneyCell value={section.subtreeAmount} />
      </span>
      {section.amount !== null && (
        <p className="text-2xs text-fg-tertiary">
          своя:{" "}
          <span data-testid={`own-amount-${section.key}`}>
            <MoneyCell value={section.amount} />
          </span>
        </p>
      )}
    </div>
  );
}

/** Сумма записи о ручном решении — `subtree_amount`, а не `amount` (задача 9):
 *  файловая свёртка ВСЕГО поддерева раздела, цена решения. */
function renderManualAside(row: PassportManualRow) {
  return (
    <div className="text-right">
      <span data-testid={`manual-amount-${row.key}`}>
        <MoneyCell value={row.subtreeAmount} />
      </span>
    </div>
  );
}
