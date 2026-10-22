import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { ContextValue, ContextVariantData } from "@/types/domain";

import {
  VALUE_REFRESHING_LABEL,
  VARIANT_VALUE_SOURCE_LABEL,
  VARIANT_VALUE_UNSPECIFIED,
  valueOffListLabel,
} from "./labels";

interface ContextVariantProps {
  variant: ContextVariantData;
  /** Перейти к членствам контекста: по ним видно, чем различаются разделы. */
  onOpenMemberships: () => void;
}

/**
 * Хвост строки значения: источник словами; у `none` он совпал бы с самим «не уточнено», а у
 * `off_list` — с самим «не из списка», поэтому у обоих молчит.
 */
function sourceTail(value: ContextValue): string | null {
  return value.source === "none" || value.source === "off_list" ? null : VARIANT_VALUE_SOURCE_LABEL[value.source];
}

/** Что стоит на месте значения: значение списка, текст не из списка или «не уточнено». */
function valueText(value: ContextValue): string {
  if (value.off_list_text !== null) return valueOffListLabel(value.off_list_text);
  return value.value ?? VARIANT_VALUE_UNSPECIFIED;
}

function ValueLines({ values }: { values: ContextValue[] }) {
  return (
    <ul className="grid gap-1">
      {values.map((value) => {
        const tail = sourceTail(value);
        return (
          <li key={value.parameter_id}>
            <span className="text-fg-tertiary">{value.name}</span>:{" "}
            <span className="font-medium">{valueText(value)}</span>
            {tail !== null && <span className="text-fg-tertiary"> · {tail}</span>}
            {value.refreshing && (
              <>
                {" "}
                <Badge variant="outline" className="border-warning-border bg-warning-soft text-warning-text">
                  {VALUE_REFRESHING_LABEL}
                </Badge>
              </>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/**
 * Строка «Вариант» карточки контекста (спека `2026-10-02-catalog-variants-design.md` §2.12,
 * спека 3в §2.11): главные параметры — это вариант, второстепенные — отдельной строкой ниже. У
 * каждого значения источник (наименование, разделы, вручную), «не уточнено» там, где значения
 * нет, «уточняется» у значения, которое ждёт перезапроса, и «не из списка: «…»» у текста вне
 * закрытого списка; пометка «к делению» с переходом к членствам и пометка «вариант
 * пересчитывается» (задание значений в очереди или выполняется).
 */
export function ContextVariant({ variant, onOpenMemberships }: ContextVariantProps) {
  const hasVariant = variant.variant_id !== null;
  // «Пересчитывается» — только когда задание значений идёт или ждёт очереди; удержанное и
  // упавшее задание видно в очередях «Ошибки», и экран не утверждает того, чего не знает.
  const recalculating = variant.values_job_status === "pending" || variant.values_job_status === "running";
  if (!hasVariant && !recalculating) return null;
  const main = variant.values.filter((value) => value.role !== "secondary");
  const secondary = variant.values.filter((value) => value.role === "secondary");

  return (
    <div className="grid gap-2 text-sm">
      {hasVariant && (
        <div className="grid grid-cols-[170px_minmax(0,1fr)] gap-x-3 gap-y-1">
          <div className="text-fg-tertiary">Вариант</div>
          {main.length === 0 ? <div>{VARIANT_VALUE_UNSPECIFIED}</div> : <ValueLines values={main} />}
          {secondary.length > 0 && (
            <>
              <div className="text-fg-tertiary">Второстепенные</div>
              <ValueLines values={secondary} />
            </>
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
