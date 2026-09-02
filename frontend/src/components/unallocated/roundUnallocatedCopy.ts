import { pluralRu } from "@/lib/format";
import type { RoundDiagnosticCode, RoundSectionConflict, RoundSectionPartial } from "@/types/domain";

/**
 * Тексты экрана этапного разноса «Нераспределённого» — Sheet-верстак раунда
 * (спека 2026-09-01-round-unallocated-design.md §2.7; макет
 * 2026-09-01-offer-unallocated-mockup.html, раздел 2/2а; план, задача 11).
 *
 * Заголовки, подсказки блоков, пометки и текст диагностики — буквально из
 * макета (ревью задачи 11, находка B: сокращённые формы плана расходились с
 * макетом, а при разногласии со ЗАТВЕРЖДЁННЫМ макетом выигрывает макет — не
 * то, что перечислил план задачи). Ровно ДВЕ строки макет не несёт вовсе —
 * `PENDING_EMPTY` и `MANUAL_EMPTY`: макет показывает только ЗАПОЛНЕННЫЕ блоки,
 * пустые состояния он не рисует, поэтому сверять их не с чем — это
 * собственный текст экрана, а не цитата.
 *
 * Отдельный модуль, а не соседство с `UnallocatedSheet.tsx`: правило
 * `react-refresh/only-export-components` не пропускает экспорт констант рядом
 * с компонентом в одном файле (тот же приём, что у `drilldownCopy.ts`).
 */

export const sheetTitle = (stageNo: number) => `Разнос статей — Этап ${stageNo}`;

export const sheetSubtitle = (offers: number) =>
  `Ведомость одна на этап: решение по разделу применяется ко всем сметам раунда (${offers} участник${pluralRu(offers)}).`;

export const pendingHeading = (n: number) => `Требуют решения — ${n}`;

export const PENDING_HINT =
  "Файловый порядок ведомости; счётчик «N позиций» — полный размер файлового " +
  "поддерева (стабилен и не зависит от вложенных решений: конфликт возможен " +
  "и при нуле нераспределённых строк). Здесь три из четырёх состояний " +
  "логического раздела: без решения · частичное («разнесено не во всех " +
  "сметах») · конфликт («решения в сметах различаются» — по полному вектору: " +
  "статьи, заметки или аудит). Четвёртое, единое решение, — в блоке ниже.";

export const MANUAL_HEADING = "Разнесено вручную";

export const MANUAL_HINT =
  "Только разделы с ЕДИНЫМ решением во всех offer-сметах раунда (единая " +
  "статья, заметка и аудит); частичные и конфликтные — выше, в «Требуют " +
  "решения». «Снять» убирает решение во всех сметах раунда.";

// НЕ из макета (§ докстрока модуля выше) — макет не рисует пустые состояния,
// сверять эти два текста не с чем.
export const PENDING_EMPTY = "Разделов, требующих решения, нет.";
export const MANUAL_EMPTY = "Единых ручных решений пока нет.";

export const DIAGNOSTICS_HEADING_HINT =
  "Три причины с кодами (границы §5.5 спеки разноса): позиция вне структуры " +
  "файла · structure_disabled · допработа с неразрешимой ссылкой. Показаны " +
  "присутствующие в данных; это диагностика, не действие. Ведро " +
  "unallocated.extras паспорта сюда не годится — оно шире границы и " +
  "содержит допработы, которые разнос раздела закроет.";

export const diagnosticsHeading = (rows: number) => `Не закрывается разносом — ${rows} ${rowsWord(rows)}`;

export const positionsLabel = (n: number) => `${n} ${positionsWord(n)}`;

export const partialMark = (p: RoundSectionPartial) =>
  `разнесено не во всех сметах: ${p.assigned} из ${p.total}`;

export function conflictMark(c: RoundSectionConflict): string {
  if (c.categories.length > 1)
    return `решения в сметах различаются: статьи ${c.categories
      .map((x) => `«${x.code} · ${x.title}»`)
      .join(" против ")} — нераспределённых строк нет, но этап несогласован`;
  if (c.notes.length > 1)
    return "решения в сметах различаются: статья едина, различаются заметки — повторное решение выравнивает";
  return "решения в сметах различаются: статья едина, различается аудит (авторы/даты) — повторное решение выравнивает";
}

export const DIAGNOSTIC_REASON: Record<RoundDiagnosticCode, string> = {
  outside_structure: "позиции вне структуры файла — раздела, которому можно назначить статью, у них нет",
  structure_disabled: "привязка по структуре погашена — статьи не привязаны ни к одной строке предложения",
  unresolved_chapter_ref: "допработа с неразрешимой ссылкой — статью не от кого наследовать",
};

export const LOAD_ERROR = "Не удалось загрузить нераспределённое.";
export const ROUND_GONE = "Раунд или его сметы больше недоступны.";

/** Ключ логического раздела для React/testid/дерева — `lot_key` и
 *  `position_key_in_proposal` вместе; на бэкенде это пара §2.3/§2.4. */
export const sectionKeyOf = (lotKey: string, positionKey: string) => `${lotKey}:${positionKey}`;

/** «позиция/позиции/позиций» (ж. р.) — тот же приём, что у `estimateWordFor`
 *  в `TenderCardPage.tsx`: `pluralRu` из `lib/format.ts` отдаёт только
 *  окончание для существительных мужского рода, «позиция» женского и меняет
 *  не только окончание. */
function positionsWord(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return "позиция";
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return "позиции";
  return "позиций";
}

/** «строка/строки/строк» (ж. р.) — тот же приём. */
function rowsWord(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return "строка";
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return "строки";
  return "строк";
}
