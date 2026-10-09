import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ContractFormDialog } from "@/components/contracts/ContractFormDialog";
import { LinkContractDialog } from "@/components/tenders/LinkContractDialog";
import { NotConcludedDialog } from "@/components/tenders/NotConcludedDialog";
import { RemoveAwardDialog } from "@/components/tenders/RemoveAwardDialog";
import { Button } from "@/components/ui/button";
import { useCurrentUser } from "@/hooks/useAuth";
import { formatDate, formatMillionsVat } from "@/lib/format";
import type { TenderAward, TenderAwardEvent, TenderCard } from "@/types/domain";

type OpenDialog = "create" | "link" | "not-concluded" | "remove" | null;

/**
 * Плашка победителя над решёткой (спека Б2 §2.8, макет экраны 1, 2, 4, 6).
 *
 * Состояния: «не отмечен» — строка подсказки; отмечен без договора — четыре
 * действия (только `admin`: команды серверу доступны ему же); отмечен с
 * договором — «Открыть договор», снять отметку и записать «не заключён» уже
 * нельзя. История — только если в ней есть «не заключён».
 */
export function WinnerBanner({ card }: { card: TenderCard }) {
  const { data: user } = useCurrentUser();
  const isAdmin = user?.role === "admin";
  const navigate = useNavigate();
  const [open, setOpen] = useState<OpenDialog>(null);
  const award = card.award;
  const showHistory = card.award_history.some((event) => event.kind === "not_concluded");

  return (
    <>
      {award === null ? (
        <div className="rounded-xl border border-dashed border-border px-4 py-3 text-sm text-fg-secondary">
          Победитель не отмечен. Отметка ставится в финальном этапе, когда решение принято.
          {showHistory && <AwardHistory events={card.award_history} />}
        </div>
      ) : (
        <div className="rounded-xl border border-accent-border bg-accent-soft px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-base font-semibold text-accent-text">Победитель — {award.contractor_title}</div>
              <div className="mt-0.5 text-sm text-fg-secondary">{details(award)}</div>
            </div>
            {award.contract !== null ? (
              <Button variant="outline" render={<Link to={`/contracts/${award.contract.id}`} />}>
                Открыть договор
              </Button>
            ) : (
              isAdmin && (
                <div className="flex flex-wrap items-center gap-2">
                  <Button onClick={() => setOpen("create")}>Создать договор</Button>
                  <Button variant="outline" onClick={() => setOpen("link")}>
                    Привязать существующий договор
                  </Button>
                  <Button
                    variant="outline"
                    className="border-warning-border text-warning-text dark:border-warning-border"
                    onClick={() => setOpen("not-concluded")}
                  >
                    Договор не заключён
                  </Button>
                  <Button variant="ghost" className="text-fg-secondary" onClick={() => setOpen("remove")}>
                    Снять отметку
                  </Button>
                </div>
              )
            )}
          </div>
          {showHistory && <AwardHistory events={card.award_history} />}
        </div>
      )}

      {award !== null && open === "create" && (
        <ContractFormDialog
          open
          onOpenChange={(next) => !next && setOpen(null)}
          fromAward={{
            tenderId: card.id,
            tenderNumber: card.tender_number,
            award,
            objectId: card.object_id,
            objectTitle: card.object_title,
          }}
          onCreated={(contract) => navigate(`/contracts/${contract.id}`)}
        />
      )}
      {award !== null && open === "link" && (
        <LinkContractDialog tenderId={card.id} award={award} onOpenChange={(next) => !next && setOpen(null)} />
      )}
      {award !== null && open === "not-concluded" && (
        <NotConcludedDialog tenderId={card.id} award={award} onOpenChange={(next) => !next && setOpen(null)} />
      )}
      {award !== null && open === "remove" && (
        <RemoveAwardDialog tenderId={card.id} award={award} onOpenChange={(next) => !next && setOpen(null)} />
      )}
    </>
  );
}

function details(award: TenderAward): string {
  const contract = award.contract
    ? `договор № ${award.contract.contract_number} от ${formatDate(award.contract.signed_date)}`
    : "договора пока нет";
  return [
    `КП этапа ${award.stage_no}`,
    formatMillionsVat(award.total_including_vat),
    `отмечен ${formatDate(award.awarded_at)}`,
    contract,
  ].join(" · ");
}

/** История отметок: порядок — как прислал сервер (по id отметки). */
function AwardHistory({ events }: { events: TenderAwardEvent[] }) {
  return (
    <div className="mt-3 border-t border-accent-border pt-2 text-sm">
      {events.map((event, index) => (
        <div
          key={`${event.award_id}-${event.kind}-${index}`}
          data-testid="award-history-row"
          className="flex items-baseline gap-3 py-0.5 text-fg-secondary"
        >
          <span className="min-w-24 tabular-nums text-fg-tertiary">
            {formatDate(event.kind === "awarded" ? event.awarded_at : event.not_concluded_on)}
          </span>
          <span>
            {event.kind === "awarded" ? (
              <>
                отмечен <b className="font-semibold text-fg">{event.contractor_title}</b>
                {event.is_active && " · действующий"}
              </>
            ) : (
              <>
                договор с <b className="font-semibold text-fg">{event.contractor_title}</b> не заключён
                {event.note !== null && ` — «${event.note}»`}
              </>
            )}
          </span>
        </div>
      ))}
    </div>
  );
}
