import { useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { LineParam, ParamsBasis } from "@/types/domain";

import { PARAMS_COPY, paramText, paramWarn, tableNote } from "./paramsCopy";

/** Сколько параметров видно до «подробнее» (спека параметров варианта §2.7). */
const VISIBLE_PARAMS = 3;

export interface LineParamsCellProps {
  family: string | null;
  params: LineParam[];
  basis: ParamsBasis | null;
}

/**
 * Семья отдельной строкой и под ней параметры строки КП «название: значение» столбиком
 * в порядке схемы (спека параметров варианта §2.7). Первые три видны сразу, остальные - под
 * «подробнее»; раскрытие у каждой строки своё.
 */
export function LineParamsCell({ family, params, basis }: LineParamsCellProps) {
  const [expanded, setExpanded] = useState(false);
  if (family === null && params.length === 0) return null;

  const hidden = Math.max(0, params.length - VISIBLE_PARAMS);
  const shown = expanded ? params : params.slice(0, VISIBLE_PARAMS);

  return (
    <div className="text-[0.6875rem] leading-snug text-fg-secondary">
      {family !== null && (
        <span data-testid="params-family" className="block">
          {family}
        </span>
      )}
      {shown.map((param) => {
        const note = tableNote(param, basis);
        return (
          <div key={param.name} data-testid="params-row">
            <span>{param.name}:</span>{" "}
            <span data-testid="params-value" className={cn("text-fg", paramWarn(param) && "text-warning")}>
              {paramText(param)}
            </span>
            {note !== null && <span className="text-fg-muted"> {note}</span>}
          </div>
        );
      })}
      {hidden > 0 && (
        <Button
          type="button"
          variant="link"
          size="xs"
          className="h-auto px-0 text-[0.6875rem]"
          onClick={(event) => {
            event.stopPropagation();
            setExpanded((value) => !value);
          }}
        >
          {expanded ? PARAMS_COPY.collapse : PARAMS_COPY.more(hidden)}
        </Button>
      )}
    </div>
  );
}
