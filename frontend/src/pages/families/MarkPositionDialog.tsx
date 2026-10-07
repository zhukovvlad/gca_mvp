import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { apiErrorCode, apiErrorContext, useMarkPositionKind } from "@/services/queries";
import type { PositionMarkKind, PositionStandard } from "@/types/domain";

import { contextRefusalLabel } from "./labels";

interface MarkPositionDialogProps {
  positionId: number;
  /** Написание строки каталога — показывается, чтобы было видно, что именно помечается. */
  title: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** `2026-03-01` → `01.03.2026`: дата без часового пояса, поэтому не через `Date`. */
function formatIsoDate(iso: string): string {
  const [year, month, day] = iso.split("-");
  return `${day}.${month}.${year}`;
}

function standardPeriod(standard: PositionStandard): string {
  const from = `с ${formatIsoDate(standard.valid_from)}`;
  return standard.valid_to === null ? `${from}, бессрочно` : `${from} по ${formatIsoDate(standard.valid_to)}`;
}

interface Refusal {
  label: string;
  standards: PositionStandard[];
}

/**
 * «Пометить написание целиком…» (спека `2026-10-02-catalog-variants-design.md` §2.11, §2.12):
 * глобальная пометка строки каталога заголовком или мусором. Пометка относится ко ВСЕМ будущим
 * вхождениям этого написания и снимает семьи, варианты и значения у всех его контекстов. При
 * `409 position_has_standards` диалог остаётся открытым и перечисляет нормативы.
 */
export function MarkPositionDialog({ positionId, title, open, onOpenChange }: MarkPositionDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <MarkPositionForm positionId={positionId} title={title} onDone={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

function MarkPositionForm({
  positionId,
  title,
  onDone,
}: {
  positionId: number;
  title: string;
  onDone: () => void;
}) {
  const mark = useMarkPositionKind();
  const [refusal, setRefusal] = useState<Refusal | null>(null);

  function submit(kind: PositionMarkKind) {
    setRefusal(null);
    mark.mutate(
      { positionId, kind },
      {
        onSuccess: onDone,
        onError: (error) => {
          const code = apiErrorCode(error);
          const standards =
            code === "position_has_standards"
              ? (apiErrorContext<{ standards?: PositionStandard[] }>(error)?.standards ?? [])
              : [];
          setRefusal({ label: contextRefusalLabel(code), standards });
        },
      }
    );
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>Пометить написание целиком</DialogTitle>
        <DialogDescription>
          Пометка относится ко всем будущим вхождениям этого написания в сметах и снимает семьи,
          варианты и значения у всех его контекстов.
        </DialogDescription>
      </DialogHeader>

      <div className="grid gap-3 py-2">
        <p className="break-words text-sm font-medium text-fg">{title}</p>
        {refusal && (
          <div role="alert" className="grid gap-1 text-sm text-danger-text">
            <p>{refusal.label}</p>
            {refusal.standards.length > 0 && (
              <ul className="list-disc pl-5">
                {refusal.standards.map((standard) => (
                  <li key={standard.id}>
                    {standard.rate_class_title} · {standardPeriod(standard)}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      <DialogFooter>
        <Button variant="outline" disabled={mark.isPending} onClick={() => submit("HEADER")}>
          Пометить заголовком
        </Button>
        <Button variant="destructive" disabled={mark.isPending} onClick={() => submit("TRASH")}>
          Пометить мусором
        </Button>
      </DialogFooter>
    </>
  );
}
