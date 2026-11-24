import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useSuspiciousLines } from "@/services/queries";
import type { SuspiciousLinesResponse } from "@/types/domain";

import { ResolvedItem } from "./ResolvedItem";
import { KindBadge, SuspectItem } from "./SuspectItem";
import {
  EMPTY_UNRESOLVED,
  LOAD_ERROR,
  RESOLVED_HINT,
  resolvedTitle,
  SUMMARY_RESOLVED,
  SUMMARY_UNRESOLVED,
  SUSPICION_CHANGED_NOTICE,
  SUSPICIOUS_FILTER_ALL,
  SUSPICIOUS_FILTER_LABEL,
  unresolvedTitle,
} from "./suspiciousCopy";

const ALL = "all";

function positiveInt(raw: string | null): number | null {
  if (raw === null || !/^\d+$/.test(raw)) return null;
  const value = Number(raw);
  return value > 0 ? value : null;
}

function Summary({ data }: { data: SuspiciousLinesResponse }) {
  const outliers = data.unresolved.filter((item) => item.facts.some((f) => f.kind === "outlier")).length;
  const twins = data.unresolved.filter((item) => item.facts.some((f) => f.kind === "twin")).length;
  const cell = "inline-flex items-center gap-2 rounded-[10px] border border-border-subtle bg-surface-sunken px-3 py-1.5 text-[12.5px] text-fg-secondary";
  const num = "text-xl leading-none font-semibold text-fg tabular-nums";
  return (
    <div data-testid="suspicious-summary" className="flex flex-wrap gap-2.5">
      <div className={cell}>
        <span className={num} data-testid="sum-unresolved">{data.unresolved.length}</span>
        <span>{SUMMARY_UNRESOLVED}</span>
      </div>
      <div className={cell}>
        <span className={num} data-testid="sum-outlier">{outliers}</span>
        <KindBadge kind="outlier" />
      </div>
      <div className={cell}>
        <span className={num} data-testid="sum-twin">{twins}</span>
        <KindBadge kind="twin" />
      </div>
      <div className={cell}>
        <span className={num} data-testid="sum-resolved">{data.resolved.length}</span>
        <span>{SUMMARY_RESOLVED}</span>
      </div>
    </div>
  );
}

/** Фильтр по договору: стоит в одной строке с вкладками (макет, раздел 1); состояние — в адресе. */
export function SuspiciousFilter() {
  const [params, setParams] = useSearchParams();
  const contractId = positiveInt(params.get("contract"));
  const data = useSuspiciousLines(contractId, true).data;

  function selectContract(value: string | null) {
    const next = new URLSearchParams(params);
    next.delete("line");
    if (value === null || value === ALL) next.delete("contract");
    else next.set("contract", value);
    setParams(next, { replace: true });
  }

  return (
    <Select value={contractId === null ? ALL : String(contractId)} onValueChange={selectContract}>
      <SelectTrigger className="w-72 max-w-full" aria-label={SUSPICIOUS_FILTER_LABEL}>
        <SelectValue>
          {(raw) => {
            if (!raw || raw === ALL) return SUSPICIOUS_FILTER_ALL;
            const option = data?.contracts.find((c) => String(c.contract_id) === raw);
            return option === undefined ? `Договор ${raw}` : `${option.number} · ${option.object_title}`;
          }}
        </SelectValue>
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL}>{SUSPICIOUS_FILTER_ALL}</SelectItem>
        {(data?.contracts ?? []).map((c) => (
          <SelectItem key={c.contract_id} value={String(c.contract_id)}>
            {c.number} · {c.object_title} ({c.unresolved})
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/**
 * Вкладка «Подозрительные строки» на `/contracts` (спека 2026-11-24-suspicious-lines-design.md,
 * раздел 5 п. 1). Состояние — в адресе: `contract` (фильтр) и `line` (пункт прокручен в вид, блоки раскрыты).
 */
export function SuspiciousTab() {
  const [params] = useSearchParams();
  const contractId = positiveInt(params.get("contract"));
  const lineId = positiveInt(params.get("line"));
  const [changed, setChanged] = useState(false);
  const listQ = useSuspiciousLines(contractId, true);
  const data = listQ.data;
  const lineIsResolved = data?.resolved.some((item) => item.position_item_id === lineId) ?? false;
  const [resolvedOpen, setResolvedOpen] = useState<boolean | null>(null);
  const resolvedShown = resolvedOpen ?? lineIsResolved;

  return (
    <div className="mt-4 space-y-4">
      {changed && (
        <Alert data-testid="suspicion-changed">
          <AlertDescription>{SUSPICION_CHANGED_NOTICE}</AlertDescription>
        </Alert>
      )}

      {listQ.isPending && (
        <div className="space-y-3 rounded-lg border border-border-subtle bg-surface p-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      )}
      {listQ.isError && <EmptyState title="Ошибка загрузки" description={LOAD_ERROR} />}

      {data && (
        <div className="grid min-w-0 gap-3 rounded-lg border border-border-subtle bg-surface p-4">
          <Summary data={data} />

          <h2 className="mt-2 text-[11px] font-semibold tracking-wide text-fg-tertiary uppercase">
            {unresolvedTitle(data.unresolved.length)}
          </h2>
          {data.unresolved.length === 0 && <p className="text-sm text-fg-secondary">{EMPTY_UNRESOLVED}</p>}
          {data.unresolved.map((item) => (
            <SuspectItem
              key={item.position_item_id}
              item={item}
              focused={item.position_item_id === lineId}
              onChanged={() => setChanged(true)}
            />
          ))}

          <Collapsible open={resolvedShown} onOpenChange={setResolvedOpen} data-testid="resolved-block">
            <CollapsibleTrigger className="flex items-center gap-1 text-[11px] font-semibold tracking-wide text-fg-tertiary uppercase">
              {resolvedShown ? <ChevronDown className="size-3.5" aria-hidden /> : <ChevronRight className="size-3.5" aria-hidden />}
              {resolvedTitle(data.resolved.length)}
            </CollapsibleTrigger>
            <CollapsibleContent className="mt-1 grid min-w-0 gap-3">
              <p className="text-xs text-fg-secondary">{RESOLVED_HINT}</p>
              {data.resolved.map((item) => (
                <ResolvedItem key={item.position_item_id} item={item} focused={item.position_item_id === lineId} />
              ))}
            </CollapsibleContent>
          </Collapsible>
        </div>
      )}
    </div>
  );
}
