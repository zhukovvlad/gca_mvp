import { useState } from "react";
import { Trash2 } from "lucide-react";

import { ParticipantDeleteDialog } from "@/components/tenders/ParticipantDeleteDialog";
import { StatusPill } from "@/components/ui-domain/StatusPill";
import { Surface } from "@/components/ui-domain/Surface";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useCurrentUser } from "@/hooks/useAuth";
import { formatDecimalMoney } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { TenderCard, TenderParticipant } from "@/types/domain";

interface OfferGridProps {
  card: TenderCard;
  selectedRoundId: number | undefined;
  onSelectRound: (roundId: number) => void;
}

/**
 * Решётка «участник × раунд» (спека §2.13, §2.14).
 *
 * ПРЯМОУГОЛЬНИК — обязательное свойство, а не следствие обычно полных данных:
 * у КАЖДОГО участника есть ячейка в КАЖДОМ раунде, включая раунд, где он
 * впервые появился (`offer_id` пуст в более ранних раундах). Это верно и
 * тогда, когда сервер вовсе не прислал запись `cells` для пары (раунд,
 * участник) — `!cell` трактуется тем же образом, что и явное `offer_id: null`:
 * «не участвовал», а не пропуск клетки. Ряд Беты не должен быть короче ряда
 * Альфы ни при каких данных.
 *
 * Три состояния ячейки — данными, не выводом клиента (см. `TenderCell` в
 * `types/domain.ts`): нет предложения → «—»; предложение есть, сметы нет →
 * «нет сметы»; обе есть → сумма.
 */
export function OfferGrid({ card, selectedRoundId, onSelectRound }: OfferGridProps) {
  const { data: user } = useCurrentUser();
  const isAdmin = user?.role === "admin";
  const [toDelete, setToDelete] = useState<TenderParticipant | null>(null);

  return (
    <>
      <Surface padding="none" className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Участник</TableHead>
              {card.rounds.map((round) => (
                <TableHead key={round.id}>
                  <button
                    type="button"
                    onClick={() => onSelectRound(round.id)}
                    className={cn(
                      "rounded-md px-2 py-1 text-left text-xs font-medium transition-colors",
                      round.id === selectedRoundId
                        ? "bg-accent-soft text-accent-text"
                        : "text-fg-secondary hover:bg-surface-hover"
                    )}
                  >
                    Этап {round.stage_no}
                    {round.label ? ` · ${round.label}` : ""}
                  </button>
                </TableHead>
              ))}
              {isAdmin && <TableHead className="w-10" />}
            </TableRow>
          </TableHeader>
          <TableBody>
            {card.participants.map((participant) => (
              <TableRow key={participant.package_id}>
                <TableCell>
                  <div className="font-medium text-fg">{participant.title}</div>
                  <div className="text-xs text-fg-tertiary">{participant.inn}</div>
                </TableCell>
                {card.rounds.map((round) => {
                  const cell = card.cells.find(
                    (c) => c.round_id === round.id && c.package_id === participant.package_id
                  );
                  return (
                    <TableCell key={round.id}>
                      {!cell || cell.offer_id === null ? (
                        <span title="Не участвовал" className="text-fg-tertiary">
                          —
                        </span>
                      ) : cell.estimate_id === null ? (
                        <StatusPill tone="warning" label="нет сметы" />
                      ) : (
                        formatDecimalMoney(cell.total_including_vat)
                      )}
                    </TableCell>
                  );
                })}
                {isAdmin && (
                  <TableCell className="text-right">
                    <Button
                      size="xs"
                      variant="ghost"
                      aria-label={`Удалить участника «${participant.title}»`}
                      onClick={() => setToDelete(participant)}
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Surface>
      <p className="mt-2 text-xs text-fg-tertiary">Суммы в ячейках — итого с НДС</p>

      <ParticipantDeleteDialog tenderId={card.id} participant={toDelete} onOpenChange={() => setToDelete(null)} />
    </>
  );
}
