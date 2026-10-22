import { useEffect, useRef, useState } from "react";

import { semanticApi } from "@/services/api/domain";
import { apiErrorCode } from "@/services/queries";
import type { SchemaEditDraft, SchemaEditSummary } from "@/types/domain";

import { schemaRefusalLabel } from "../labels";
import { wireDrafts } from "./drafts";

/** Пауза после последнего изменения тела: пока человек печатает, предпросмотр не запрашивается. */
export const SCHEMA_EDIT_PREVIEW_DELAY_MS = 400;

interface PreviewResult {
  key: string;
  summary: SchemaEditSummary | null;
  refusal: string | null;
}

/**
 * Сводка правки схемы по телу (спека 3в §2.5, §2.11): запрос уходит через 400 мс после
 * последнего изменения тела, а ответ на устаревшее тело отбрасывается по счётчику запроса —
 * иначе поздний ответ на прежний ввод перетёр бы сводку нынешнего.
 *
 * `summary` относится ТОЛЬКО к нынешнему телу: пока запрос не вернулся, он `null`, а `pending`
 * истинно — сохранение по сводке прежнего тела подтвердило бы чужие следствия. Отказ проверки
 * (`schema_bad_range`, `schema_duplicate_parameter`…) приходит подписью в `refusal`, сводки при
 * нём нет. `refreshKey` перезапрашивает предпросмотр при неизменном теле (`preview_changed`,
 * правка схемы из окна: слияние «не из списка»).
 */
export function useSchemaEditPreview(
  familyId: number,
  drafts: SchemaEditDraft[],
  refreshKey: string | number = 0
): { summary: SchemaEditSummary | null; pending: boolean; refusal: string | null } {
  const key = JSON.stringify([familyId, wireDrafts(drafts), refreshKey]);
  const [result, setResult] = useState<PreviewResult | null>(null);
  const counter = useRef(0);

  useEffect(() => {
    counter.current += 1;
    const id = counter.current;
    const [, body] = JSON.parse(key) as [number, SchemaEditDraft[], unknown];
    const timer = setTimeout(() => {
      semanticApi.schemaEditPreview(familyId, body).then(
        (summary) => {
          if (counter.current === id) setResult({ key, summary, refusal: null });
        },
        (error: unknown) => {
          if (counter.current === id) {
            setResult({ key, summary: null, refusal: schemaRefusalLabel(apiErrorCode(error)) });
          }
        }
      );
    }, SCHEMA_EDIT_PREVIEW_DELAY_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [familyId, key]);

  const current = result !== null && result.key === key ? result : null;
  return {
    summary: current?.summary ?? null,
    pending: current === null,
    refusal: current?.refusal ?? null,
  };
}
