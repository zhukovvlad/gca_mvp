import { Button } from "@/components/ui/button";
import type { ContextValue, ContextVariantData } from "@/types/domain";

import { VARIANT_VALUE_SOURCE_LABEL, VARIANT_VALUE_UNSPECIFIED } from "./labels";

interface ContextVariantProps {
  variant: ContextVariantData;
  /** Перейти к членствам контекста: по ним видно, чем различаются разделы. */
  onOpenMemberships: () => void;
}

/** Хвост строки значения: источник словами; у `none` он совпал бы с самим «не уточнено», поэтому молчит. */
function sourceTail(value: ContextValue): string | null {
  return value.source === "none" ? null : VARIANT_VALUE_SOURCE_LABEL[value.source];
}

/**
 * Строка «Вариант» карточки контекста (спека `2026-10-02-catalog-variants-design.md` §2.12):
 * значение каждого параметра схемы семьи с источником (наименование, разделы, вручную),
 * «не уточнено» там, где значения нет, пометка «к делению» с переходом к членствам и пометка
 * «вариант пересчитывается» (задание значений в очереди или выполняется).
 */
export function ContextVariant({ variant, onOpenMemberships }: ContextVariantProps) {
  const hasVariant = variant.variant_id !== null;
  // «Пересчитывается» — только когда задание значений идёт или ждёт очереди; удержанное и
  // упавшее задание видно в очередях «Ошибки», и экран не утверждает того, чего не знает.
  const recalculating = variant.values_job_status === "pending" || variant.values_job_status === "running";
  if (!hasVariant && !recalculating) return null;

  return (
    <div className="grid gap-2 text-sm">
      {hasVariant && (
        <div className="grid grid-cols-[170px_minmax(0,1fr)] gap-x-3 gap-y-1">
          <div className="text-fg-tertiary">Вариант</div>
          {variant.values.length === 0 ? (
            <div>{VARIANT_VALUE_UNSPECIFIED}</div>
          ) : (
            <ul className="grid gap-1">
              {variant.values.map((value) => {
                const tail = sourceTail(value);
                return (
                  <li key={value.parameter_id}>
                    <span className="text-fg-tertiary">{value.name}</span>:{" "}
                    <span className="font-medium">{value.value ?? VARIANT_VALUE_UNSPECIFIED}</span>
                    {tail !== null && <span className="text-fg-tertiary"> · {tail}</span>}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}

      {recalculating && <p className="text-fg-tertiary">вариант пересчитывается</p>}

      {hasVariant && variant.split_hint && (
        <div className="flex flex-wrap items-center gap-2 text-warning-text">
          <span>к делению: разделы расходятся</span>
          <Button size="xs" variant="link" className="h-auto p-0" onClick={onOpenMemberships}>
            Показать членства
          </Button>
        </div>
      )}
    </div>
  );
}
