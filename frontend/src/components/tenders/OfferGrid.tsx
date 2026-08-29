import { useState } from "react";
import { Check, Trash2 } from "lucide-react";

import { ParticipantDeleteDialog } from "@/components/tenders/ParticipantDeleteDialog";
import { StatusPill } from "@/components/ui-domain/StatusPill";
import { Surface } from "@/components/ui-domain/Surface";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Toggle } from "@/components/ui/toggle";
import { useCurrentUser } from "@/hooks/useAuth";
import { formatDecimalMoney } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { TenderCard, TenderParticipant } from "@/types/domain";

interface OfferGridProps {
  card: TenderCard;
  selectedRoundId: number | undefined;
  onSelectRound: (roundId: number) => void;
  /** Выбранные для свода предложения — сметы ровно одного участника (спека свода §2.1). */
  selectedOfferIds: ReadonlySet<number>;
  onToggleOffer: (offerId: number) => void;
  /** Клик по имени участника — переключить разом все его сметы. */
  onSelectParticipant: (packageId: number) => void;
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
 *
 * Плитка выбора для свода по этапам (спека свода §2.1, задача 7) есть ТОЛЬКО
 * у ячейки с обоими id разом — у «—» и «нет сметы» нечего брать в свод.
 * Свод строится по одному участнику: пока выбрана хотя бы одна плитка,
 * плитки остальных участников недоступны (`disabled` с поясняющим `title`).
 */
export function OfferGrid({
  card,
  selectedRoundId,
  onSelectRound,
  selectedOfferIds,
  onToggleOffer,
  onSelectParticipant,
}: OfferGridProps) {
  const { data: user } = useCurrentUser();
  const isAdmin = user?.role === "admin";
  const [toDelete, setToDelete] = useState<TenderParticipant | null>(null);

  // Участник, чьи сметы уже выбраны — по факту принадлежности выбранных
  // offer_id, а не отдельным полем состояния: так выбор не может
  // рассинхронизироваться с тем, что реально отмечено.
  const selectedPackageId = card.cells.find(
    (c) => c.offer_id !== null && selectedOfferIds.has(c.offer_id)
  )?.package_id;

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
                  <button
                    type="button"
                    className="font-medium text-fg hover:underline"
                    onClick={() => onSelectParticipant(participant.package_id)}
                    title="Выбрать все этапы участника"
                  >
                    {participant.title}
                  </button>
                  <div className="text-xs text-fg-tertiary">{participant.inn}</div>
                </TableCell>
                {card.rounds.map((round) => {
                  const cell = card.cells.find(
                    (c) => c.round_id === round.id && c.package_id === participant.package_id
                  );
                  if (!cell || cell.offer_id === null) {
                    return (
                      <TableCell key={round.id}>
                        <span title="Не участвовал" className="text-fg-tertiary">
                          —
                        </span>
                      </TableCell>
                    );
                  }
                  if (cell.estimate_id === null) {
                    return (
                      <TableCell key={round.id}>
                        <StatusPill tone="warning" label="нет сметы" />
                      </TableCell>
                    );
                  }

                  const offerId = cell.offer_id;
                  const pressed = selectedOfferIds.has(offerId);
                  // «Чужая» плитка — выбор уже сделан, но по ДРУГОМУ участнику
                  // (свод строится по одному участнику разом, спека §2.1).
                  const foreign =
                    selectedOfferIds.size > 0 && !pressed && selectedPackageId !== participant.package_id;
                  // Находка ревью (fix round 2): «недоступна» — состояние, которого
                  // до этой плитки в интерфейсе не было, и его причину не вывести из
                  // тишины. Нативный `disabled` убрал бы плитку из порядка Tab и
                  // спрятал бы причину в title, до которого клавиатура и скринридер
                  // не добираются. Поэтому недоступность — только `aria-disabled`
                  // (плитка остаётся фокусируемой), причина — `aria-describedby` на
                  // скрытый для глаза, но озвучиваемый текст, а не только title.
                  // Обработчик игнорирует нажатие сам — то, что раньше давал нативный
                  // атрибут бесплатно, теперь приходится обеспечивать в коде.
                  const foreignReasonId = `offer-${offerId}-foreign-reason`;
                  const foreignReason = "Свод строится по одному участнику";
                  return (
                    <TableCell key={round.id} className="text-right">
                      <Toggle
                        variant="outline"
                        size="sm"
                        pressed={pressed}
                        aria-disabled={foreign || undefined}
                        aria-describedby={foreign ? foreignReasonId : undefined}
                        title={foreign ? foreignReason : pressed ? "В своде" : "Взять в свод"}
                        onPressedChange={() => {
                          if (foreign) return;
                          onToggleOffer(offerId);
                        }}
                        className={cn(
                          "relative tabular-nums",
                          // Тот же визуальный эффект, что раньше давал `disabled:` —
                          // но явными классами, потому что `disabled:` в utility-стилях
                          // тумблера реагирует на НАТИВНЫЙ атрибут, а не на `aria-disabled`.
                          // `pointer-events-none` сюда не входит (находка внешнего
                          // ревью): он снял бы наведение — а с ним и `title` с
                          // объяснением причины — хотя обработчик уже сам отказывает
                          // в переключении (`if (foreign) return;` ниже); выключать
                          // события указателя ради поведения, которое и так не
                          // происходит, только стоило бы подсказки.
                          foreign && "opacity-50",
                          pressed &&
                            "border-accent-border bg-accent-soft text-accent-text font-semibold dark:border-accent-border dark:bg-accent-soft dark:text-accent-text"
                        )}
                      >
                        {formatDecimalMoney(cell.total_including_vat)}
                        {pressed && (
                          <Check
                            aria-hidden
                            className="absolute -right-1.5 -top-1.5 size-3.5 rounded-full bg-accent p-0.5 text-action-text"
                          />
                        )}
                      </Toggle>
                      {foreign && (
                        <span id={foreignReasonId} className="sr-only">
                          {foreignReason}
                        </span>
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
