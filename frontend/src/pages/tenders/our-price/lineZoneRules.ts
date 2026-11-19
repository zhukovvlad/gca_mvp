import { formatConfidence, formatDate } from "@/lib/format";
import type { SectionZone, ZoneChainInfo } from "@/types/domain";

import {
  WAITING_ADMIN_LABEL,
  ZONE_MODEL_STATE_TEXT,
  ZONE_SOURCE_RULE,
  ZONE_SOURCE_RUNNING,
  zoneModelProposed,
  zoneModelUnknown,
  zoneModelWas,
  zoneSourceHuman,
  zoneSourceModel,
} from "./ourPriceCopy";

/**
 * Нужно ли окно подтверждения при выборе зоны `zone` (спека зоны в панели строки §2 п. 3): зона
 * меняется и либо уже стоит другая, либо это первая установка, а у цепочки есть строки договоров
 * (первая установка делает их аналогами для других КП). Нажатие стоящей зоны и первая установка без
 * строк договоров — сразу.
 */
export function zoneNeedsConfirm(chain: Pick<ZoneChainInfo, "zone" | "rows_contracts">, zone: SectionZone): boolean {
  return zone !== chain.zone && (chain.zone !== null || chain.rows_contracts > 0);
}

/** Строка про модель (спека §3 п. 5–6); `null` — строки нет. */
export function zoneModelText(chain: ZoneChainInfo): string | null {
  const answer = chain.model_answer;
  if (chain.zone !== null) {
    return answer === null ? null : zoneModelWas(answer.zone, formatConfidence(answer.confidence), answer.reason);
  }
  switch (chain.model_state) {
    case "proposed":
      if (answer === null) return null;
      if (answer.zone === "unknown") return zoneModelUnknown(answer.reason);
      return zoneModelProposed(
        answer.zone,
        formatConfidence(answer.confidence),
        answer.reason,
        formatConfidence(chain.threshold)
      );
    case "unknown":
      return answer === null ? null : zoneModelUnknown(answer.reason);
    case "running":
    case "paused":
    case "stopped":
    case "failed":
    case "none":
      return ZONE_MODEL_STATE_TEXT[chain.model_state];
    default:
      return null;
  }
}

/** Подпись источника (спека §3 п. 2): у цепочки без зоны — чего ждём; `null` — подписи нет. */
export function zoneSourceText(chain: ZoneChainInfo): string | null {
  if (chain.zone !== null) {
    if (chain.zone_source === "rule") return ZONE_SOURCE_RULE;
    if (chain.zone_source === "model") {
      return zoneSourceModel(chain.model_answer === null ? null : formatConfidence(chain.model_answer.confidence));
    }
    if (chain.zone_source === "human") return zoneSourceHuman(formatDate(chain.decided_at));
    return null;
  }
  if (chain.model_state === "running" || chain.model_state === "paused") return ZONE_SOURCE_RUNNING;
  if (chain.model_state === "none" || chain.model_state === null) return null;
  return WAITING_ADMIN_LABEL;
}
