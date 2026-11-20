import { useState } from "react";
import { useIsMutating, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { ZoneToggle } from "@/pages/families/ZonesQueue";
import { useDecideChainZone, zoneDecisionMutationKey } from "@/services/queries";
import { qk } from "@/services/queryKeys";
import type { SectionZone, ZoneChainInfo } from "@/types/domain";

import {
  SAVING_LABEL,
  ZONE_BLOCK_TITLE,
  ZONE_DIALOG_CANCEL,
  ZONE_DIALOG_CONFIRM,
  ZONE_DIALOG_TITLE,
  ZONE_HINT,
  ZONE_HINT_LABEL,
  ZONE_NO_CHAIN,
  ZONE_QUEUE_LINK,
  ZONE_REFRESHING_LABEL,
  ZONE_READONLY,
  ZONE_STALE_REFRESH,
  ZONE_STALE_TEXT,
  ZONE_UNDEFINED,
  ZONE_UNDEFINED_TITLE,
  zoneDialogChange,
  zoneQueueHref,
  zoneScopeContracts,
  zoneScopeRows,
  zoneTitle,
} from "./ourPriceCopy";
import { zoneModelText, zoneNeedsConfirm, zoneSourceText } from "./lineZoneRules";
import { RecalcOverlay } from "./RecalcOverlay";

/** Состояние запроса, чьи данные показывает панель (спека §2 п. 4а): его передаёт `LineSheet`. */
export interface PanelAnswer {
  failed: boolean;
  fetching: boolean;
}

/**
 * Блок «Зона места» панели строки КП (спека зоны в панели строки §2–3): зона цепочки разделов,
 * кто и когда её поставил, охват решения и ответ модели. `canEdit` (администратор) получает
 * кнопки зон, окно подтверждения и ссылку на очередь «Зоны»; остальные видят то же, но без правки.
 * Строка без цепочки разделов — одной строкой: назначать зону некуда.
 */
export function LineZoneBlock({
  chain,
  canEdit,
  answer,
}: {
  chain: ZoneChainInfo | null;
  canEdit: boolean;
  answer: PanelAnswer;
}) {
  if (chain === null) {
    return (
      <p
        data-testid="line-zone-block"
        className="rounded-[10px] border border-warning-border bg-warning-soft px-3 py-2.5 text-sm text-warning-text"
      >
        {ZONE_NO_CHAIN}
      </p>
    );
  }
  return <ChainZoneBlock chain={chain} canEdit={canEdit} answer={answer} />;
}

function ChainZoneBlock({ chain, canEdit, answer }: { chain: ZoneChainInfo; canEdit: boolean; answer: PanelAnswer }) {
  // Решение из панели держит мутацию до свежего ответа панели: пока она идёт, кнопки заняты.
  const qc = useQueryClient();
  const decide = useDecideChainZone({ awaitOurPrice: true });
  const deciding = useIsMutating({ mutationKey: zoneDecisionMutationKey }) > 0;
  // Ожидание перечитывания не отличает успех от ошибки, а при ошибке панель держит прежний ответ — и
  // в кэше, после закрытия и повторного открытия (спека §2 п. 4а): кнопки открыты, только когда
  // запрос панели подтверждён, и это не зависит от времени жизни блока.
  const confirmed = !answer.failed && !answer.fetching;
  const busy = deciding || !confirmed;
  // Окно держит выбранную зону и после закрытия: текст не мигает, пока оно гаснет.
  const [confirm, setConfirm] = useState<{ zone: SectionZone; open: boolean } | null>(null);

  const undecided = chain.zone === null;
  const source = zoneSourceText(chain);
  const model = zoneModelText(chain);
  const decideNow = (zone: SectionZone) => decide.mutate({ chainId: chain.id, zone });
  const refresh = () => {
    void qc
      .refetchQueries({ queryKey: qk.tenders.ourPriceAll, type: "active" }, { throwOnError: true })
      .catch(() => undefined);
  };
  const refreshing = canEdit && answer.fetching;

  return (
    <RecalcOverlay
      active={deciding || refreshing}
      label={deciding ? SAVING_LABEL : ZONE_REFRESHING_LABEL}
      placement="center"
    >
      <div
        data-testid="line-zone-block"
        className={cn(
          "grid min-h-14 gap-1.5 rounded-[10px] border px-3 py-2.5",
          undecided ? "border-warning-border bg-warning-soft" : "border-border bg-surface-sunken"
        )}
      >
        <div className="flex flex-wrap items-baseline justify-between gap-x-2.5 gap-y-1">
          <span className="flex items-baseline gap-1 text-sm">
            <span className="text-[0.6875rem] font-medium uppercase tracking-wide text-fg-secondary">
              {ZONE_BLOCK_TITLE}
            </span>
            <Tooltip>
              <TooltipTrigger
                render={
                  <button
                    type="button"
                    aria-label={ZONE_HINT_LABEL}
                    className="inline-flex size-[15px] cursor-help items-center justify-center rounded-full border border-fg-secondary text-[10px] font-semibold text-fg-secondary outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    ?
                  </button>
                }
              />
              <TooltipContent className="max-w-80 text-left">{ZONE_HINT}</TooltipContent>
            </Tooltip>
            <span className="text-fg-secondary">·</span>
            <span
              data-testid="zone-name"
              className={cn("font-semibold", undecided && "text-warning-text")}
            >
              {chain.zone === null ? ZONE_UNDEFINED : zoneTitle(chain.zone)}
            </span>
          </span>
          {source !== null && (
            <Badge data-testid="zone-source" variant="secondary" className="font-normal">
              {source}
            </Badge>
          )}
        </div>
        <p data-testid="zone-scope" className="text-[0.8125rem]">
          {zoneScopeRows(chain.rows_kp, chain.rows_contracts)}
          {chain.rows_contracts > 0 && (
            <span className="mt-0.5 block text-fg-secondary">{zoneScopeContracts(chain.rows_contracts)}</span>
          )}
        </p>
        {model !== null && (
          <p data-testid="zone-model" className="text-[0.8125rem] text-fg-secondary">
            {model}
          </p>
        )}
        {canEdit && answer.failed && (
          <div className="flex flex-wrap items-center justify-between gap-2.5">
            <p data-testid="zone-stale" className="text-[0.8125rem] text-warning-text">
              {ZONE_STALE_TEXT}
            </p>
            <Button variant="outline" size="sm" onClick={refresh} disabled={answer.fetching}>
              {ZONE_STALE_REFRESH}
            </Button>
          </div>
        )}
        {canEdit ? (
          <div className="flex flex-wrap items-center justify-between gap-2.5">
            <ZoneToggle
              chain={chain}
              busy={busy}
              onDecide={(zone) => {
                if (zoneNeedsConfirm(chain, zone)) setConfirm({ zone, open: true });
                else decideNow(zone);
              }}
            />
            <Link to={zoneQueueHref(chain.id)} className="text-[0.8125rem] text-info-text underline underline-offset-2">
              {ZONE_QUEUE_LINK}
            </Link>
          </div>
        ) : (
          <p data-testid="zone-readonly" className="text-xs text-fg-tertiary">
            {ZONE_READONLY}
          </p>
        )}
      </div>
      {canEdit && confirm !== null && (
        <AlertDialog
          open={confirm.open}
          onOpenChange={(open) => {
            if (!open) setConfirm({ ...confirm, open: false });
          }}
        >
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{ZONE_DIALOG_TITLE}</AlertDialogTitle>
              <AlertDialogDescription className="space-y-2">
                <span className="block">
                  {zoneDialogChange(
                    chain.titles,
                    chain.zone === null ? ZONE_UNDEFINED_TITLE : zoneTitle(chain.zone),
                    zoneTitle(confirm.zone)
                  )}
                </span>
                <span className="block">{zoneScopeRows(chain.rows_kp, chain.rows_contracts)}</span>
                {chain.rows_contracts > 0 && (
                  <span className="block">{zoneScopeContracts(chain.rows_contracts)}</span>
                )}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel render={<Button variant="outline">{ZONE_DIALOG_CANCEL}</Button>} />
              <Button
                onClick={() => {
                  setConfirm({ ...confirm, open: false });
                  decideNow(confirm.zone);
                }}
              >
                {ZONE_DIALOG_CONFIRM}
              </Button>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      )}
    </RecalcOverlay>
  );
}
