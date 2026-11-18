/**
 * Тексты параметров строки с названиями (спека параметров варианта §2.2, §2.3, §2.6-§2.8):
 * значение, источник, учёт при подборе. Чистые функции - таблица и панель берут тексты отсюда.
 */
import type { LineParam, ParamMatch, ParamsBasis } from "@/types/domain";

const NOT_PARSED = "ещё не разобрано";
const CONFLICT = "расходится по разделам";
const NOT_CLARIFIED = "не уточнено";
const NO_MATCH = "—";

const SOURCE_TEXT: Record<Exclude<LineParam["source"], "stale">, string> = {
  name: "из наименования",
  path: "из разделов смет",
  manual: "задано вручную",
  path_conflict: "разделы расходятся",
  none: "нет ни в наименовании, ни в разделах",
  off_list: "не из списка",
  missing: NOT_PARSED,
  old_schema: "по прежней версии схемы",
};

function mainText(p: Pick<LineParam, "source" | "value" | "off_list">): string {
  if (p.source === "missing") return NOT_PARSED;
  if (p.source === "path_conflict") return CONFLICT;
  if (p.source === "off_list") return `не из списка: «${p.off_list ?? ""}»`;
  if (p.value === null) return NOT_CLARIFIED;
  return p.value;
}

/** Показ значения параметра: основной текст и пометки «— прежнее», «— уточняется» (спека §2.2). */
export function paramText(p: Pick<LineParam, "source" | "value" | "off_list" | "refreshing">): string {
  const marks = [p.source === "stale" ? "— прежнее" : null, p.refreshing ? "— уточняется" : null];
  return [mainText(p), ...marks].filter((part) => part !== null).join(" ");
}

/** Значение показывается предупреждающим цветом. */
export function paramWarn(p: Pick<LineParam, "source" | "value" | "refreshing">): boolean {
  if (p.source === "stale" || p.refreshing) return true;
  if (p.source === "missing" || p.source === "path_conflict") return true;
  return p.source !== "off_list" && p.value === null;
}

/** Колонка «Откуда» панели (спека §2.2). */
export function paramSourceText(p: Pick<LineParam, "source" | "refreshing" | "catalog_value">): string {
  const base =
    p.source === "stale"
      ? `прежнее значение, ждёт пересчёта варианта; в каталоге уже: «${p.catalog_value ?? ""}»`
      : SOURCE_TEXT[p.source];
  return p.refreshing ? `${base}, уточняется` : base;
}

/** Колонка «При подборе»: подпись правила по основанию строки (спека §2.3). */
export function matchText(match: ParamMatch | null, basis: ParamsBasis | null): string {
  if (match === null) return NO_MATCH;
  if (match === "required") return "должен совпасть";
  if (match === "same_set") return "нужен такой же набор";
  return basis === "catalog" ? "можно не учитывать" : "не учитывается";
}

/**
 * Приписка к значению в таблице (спека §2.7): правило подбора у главного параметра - только при
 * основании `rung`; «(из разделов)» у значения из разделов смет. Обе - одной строкой, источник первым.
 */
export function tableNote(p: Pick<LineParam, "role" | "match" | "source">, basis: ParamsBasis | null): string | null {
  const parts: string[] = [];
  if (p.source === "path") parts.push("(из разделов)");
  if (basis === "rung" && p.role === "main") {
    if (p.match === "ignored") parts.push("— не учитывается при подборе");
    if (p.match === "same_set") parts.push("— при подборе нужен такой же набор");
  }
  return parts.length === 0 ? null : parts.join(" ");
}

/** Ячейка колонки отпущенного параметра в таблице аналогов (спека §2.5, §2.8). */
export function releasedValueText(v: { value: string | null; conflict: boolean }): string {
  return mainText({ source: v.conflict ? "path_conflict" : "name", value: v.value, off_list: null });
}

/** Имя параметра в середине фразы: первая буква строчная, если вторая не заглавная (спека §2.6). */
export function lowerFirst(name: string): string {
  if (name === "") return name;
  const second = name.charAt(1);
  const secondIsUpper = second !== "" && second === second.toUpperCase() && second !== second.toLowerCase();
  return secondIsUpper ? name : name.charAt(0).toLowerCase() + name.slice(1);
}

export const PARAMS_COPY = {
  more: (n: number): string => `подробнее · ещё ${n}`,
  collapse: "свернуть",
  secondaryRole: "второстепенный",
  sectionTitle: "Работа",
  columns: { param: "Параметр", value: "В строке КП", source: "Откуда", match: "При подборе" },
  catalogHint: "(аналогов нет — настройка каталога)",
  matchHint:
    "Не учитывается — значение не сравнивается; сборные строки договоров при этом не берутся. Нужен такой же набор — строка договора должна быть сборной из тех же частей.",
  pathNote:
    "Значение «из разделов смет» общее для строк каталога с этим наименованием; они бывают из разных разделов и смет, какой раздел дал значение — не хранится.",
} as const;
