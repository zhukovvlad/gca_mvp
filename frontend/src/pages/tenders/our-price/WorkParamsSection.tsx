import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { LineParam, ParamsBasis } from "@/types/domain";

import { matchText, PARAMS_COPY, paramSourceText, paramText, paramWarn } from "./paramsCopy";

export interface WorkParamsSectionProps {
  params: LineParam[];
  basis: ParamsBasis | null;
  kind: "work" | "system";
}

/**
 * Раздел «Работа» панели строки (спека параметров варианта §2.8): каждый параметр строкой -
 * значение в КП, откуда оно взято и как учитывается при подборе. Подсказка колонки «При подборе»
 * есть только у работы: уровень `p` системы форм не сравнивает (§2.3).
 */
export function WorkParamsSection({ params, basis, kind }: WorkParamsSectionProps) {
  if (params.length === 0) return null;
  const hasPath = params.some((p) => p.source === "path");
  return (
    <section data-testid="sheet-work-params" className="space-y-2">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-fg-secondary">{PARAMS_COPY.sectionTitle}</h4>
      <Table className="text-sm">
        <TableHeader>
          <TableRow>
            <TableHead>{PARAMS_COPY.columns.param}</TableHead>
            <TableHead>{PARAMS_COPY.columns.value}</TableHead>
            <TableHead>{PARAMS_COPY.columns.source}</TableHead>
            <TableHead>
              <MatchHeader kind={kind} basis={basis} />
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {params.map((param) => (
            <TableRow key={param.name} data-testid="work-param-row">
              <TableCell className="whitespace-normal align-top">
                {param.name}
                {param.role === "secondary" && (
                  <span className="block text-xs text-fg-secondary">{PARAMS_COPY.secondaryRole}</span>
                )}
              </TableCell>
              <TableCell
                data-testid="work-param-value"
                className={cn("whitespace-normal align-top", paramWarn(param) && "text-warning-text")}
              >
                {paramText(param)}
              </TableCell>
              <TableCell className="whitespace-normal align-top text-fg-secondary">{paramSourceText(param)}</TableCell>
              <TableCell className="whitespace-normal align-top">{matchText(param.match, basis)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {hasPath && <p className="text-xs text-fg-secondary">{PARAMS_COPY.pathNote}</p>}
    </section>
  );
}

function MatchHeader({ kind, basis }: { kind: "work" | "system"; basis: ParamsBasis | null }) {
  const label = PARAMS_COPY.columns.match;
  return (
    <>
      {kind === "work" ? (
        <Tooltip>
          <TooltipTrigger
            title={PARAMS_COPY.matchHint}
            render={<span className="cursor-help underline decoration-dotted underline-offset-2">{label}</span>}
          />
          <TooltipContent>{PARAMS_COPY.matchHint}</TooltipContent>
        </Tooltip>
      ) : (
        label
      )}
      {basis === "catalog" && <span className="ml-1 font-normal text-fg-secondary">{PARAMS_COPY.catalogHint}</span>}
    </>
  );
}
