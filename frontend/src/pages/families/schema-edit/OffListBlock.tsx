import { useState } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { apiErrorCode, useResolveOffList } from "@/services/queries";
import type { FamilySchemaParameter } from "@/types/domain";

import { pluralRu, schemaRefusalLabel } from "../labels";
import { ValueChip } from "./ValueChip";
import { normText } from "./drafts";

interface OffListBlockProps {
  familyId: number;
  parameter: FamilySchemaParameter;
  /** Тексты значений в списке правки: «Добавить в список» у них уже сделано. */
  draftValues: string[];
  /** «Добавить в список» — обычная правка, добавляющая значение; сохраняется вместе с остальной правкой. */
  onAddToList: (text: string) => void;
}

/**
 * «Встретились значения не из списка» (спека 3в §2.7, §2.11): у строк, где модель встретила
 * значение, которого нет в закрытом списке, лежит его текст. «Добавить в список» вносит значение
 * в правку; «Слить с…» сразу отдельным вызовом относит строки с этим текстом к значению списка
 * (список не меняется) — схема после него перечитывается.
 */
export function OffListBlock({ familyId, parameter, draftValues, onAddToList }: OffListBlockProps) {
  const resolve = useResolveOffList();
  const [refusal, setRefusal] = useState<string | null>(null);
  const targets = parameter.values.filter((value) => value.merged_into_id === null);
  const inDraft = new Set(draftValues.map(normText));

  if (parameter.off_list.length === 0) return null;

  function mergeInto(textNorm: string, targetValueId: number) {
    setRefusal(null);
    resolve.mutate(
      { familyId, parameter_id: parameter.id, text_norm: textNorm, target_value_id: targetValueId },
      { onError: (error) => setRefusal(schemaRefusalLabel(apiErrorCode(error))) }
    );
  }

  return (
    <section
      aria-label="Значения не из списка"
      className="grid gap-2 rounded-lg border border-info-border bg-info-soft p-3 text-sm text-info-text"
    >
      <h4 className="font-semibold">Встретились значения не из списка</h4>
      <ul className="grid gap-2">
        {parameter.off_list.map((entry) => (
          <li key={entry.text_norm} className="flex flex-wrap items-center gap-2">
            <ValueChip text={entry.text} rows={entry.rows} />
            <span className="text-xs">
              {entry.contexts} {pluralRu(entry.contexts, "контекст", "контекста", "контекстов")}
            </span>
            {inDraft.has(normText(entry.text)) ? (
              <span className="text-xs">будет добавлено в список при сохранении</span>
            ) : (
              <Button type="button" size="sm" variant="outline" onClick={() => onAddToList(entry.text)}>
                Добавить в список
              </Button>
            )}
            <DropdownMenu>
              <DropdownMenuTrigger
                type="button"
                aria-label={`Слить «${entry.text}» с…`}
                disabled={resolve.isPending}
                className={buttonVariants({ variant: "ghost", size: "sm" })}
              >
                Слить с…
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                {targets.map((target) => (
                  <DropdownMenuItem key={target.id} onClick={() => mergeInto(entry.text_norm, target.id)}>
                    {target.value}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          </li>
        ))}
      </ul>
      {refusal && (
        <p role="alert" className="text-danger-text">
          {refusal}
        </p>
      )}
    </section>
  );
}
