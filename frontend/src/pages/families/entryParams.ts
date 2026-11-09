import { parseId } from "@/lib/urlParams";
import type { SuggestionUnitFilter } from "@/types/domain";

/**
 * Вход на страницу «Семьи» по адресу: ссылка с подписи «ждёт решения
 * администратора» экрана «Против нашей цены».
 *
 * - `/families?context=<id>` — вкладка «Контексты» с карточкой контекста;
 * - `/families?tab=suggestions&queue=<list|new>&unit=<id|none>&context=<id>` —
 *   вкладка «Предложения»: очередь (по умолчанию `list`), фильтр единицы и
 *   предложение этого контекста.
 *
 * - `/families?tab=suggestions&queue=zones&chain=<id>` — очередь «Зоны» с цепочкой
 *   разделов `<id>` (ссылка с подписи «ждёт решения администратора» строки-работы;
 *   `queue` можно опустить).
 *
 * Вход действует, только когда названное корректно (целые положительные id,
 * единица — ещё и `none`, очередь — `list` или `new`); иначе страница
 * открывается как обычно.
 */
export type Entry =
  | { tab: "contexts"; contextId: number }
  | {
      tab: "suggestions";
      queue: "list" | "new";
      contextId: number;
      unit: SuggestionUnitFilter | undefined;
    }
  | { tab: "suggestions"; queue: "zones"; chainId: number };

/** Параметры адреса, которыми страница принимает вход. */
export const ENTRY_PARAMS = ["tab", "queue", "unit", "context", "chain"] as const;

/** Единица фильтра: нет параметра — `ok` без значения; `none` или id — значение; иное — не `ok`. */
function unitOf(raw: string | null): { ok: true; value: SuggestionUnitFilter | undefined } | { ok: false } {
  if (raw === null) return { ok: true, value: undefined };
  if (raw === "none") return { ok: true, value: "none" };
  const id = parseId(raw);
  return id === null ? { ok: false } : { ok: true, value: id };
}

export function parseEntry(params: URLSearchParams): Entry | null {
  if (params.get("tab") === "suggestions" && params.has("chain")) {
    const chainId = parseId(params.get("chain"));
    const queue = params.get("queue") ?? "zones";
    if (chainId === null || queue !== "zones") return null;
    return { tab: "suggestions", queue: "zones", chainId };
  }
  const contextId = parseId(params.get("context"));
  if (contextId === null) return null;
  if (params.get("tab") !== "suggestions") return { tab: "contexts", contextId };
  const unit = unitOf(params.get("unit"));
  const queue = params.get("queue") ?? "list";
  if (!unit.ok || (queue !== "list" && queue !== "new")) return null;
  return { tab: "suggestions", queue, contextId, unit: unit.value };
}
