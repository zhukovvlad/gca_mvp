import { MoreHorizontal } from "lucide-react";
import { useState } from "react";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useClearMark, useSetMark } from "@/services/queries";
import type { LineIds, MarkKind, RefRow } from "@/types/domain";

import { MarkDialog } from "./MarkDialog";
import {
  MARK_ACTION,
  MARK_CANCEL,
  MARK_CLEAR,
  MARK_CLEAR_CONFIRM_OK,
  MARK_CLEAR_CONFIRM_TITLE,
  MARK_DIALOG_TITLE,
  MARK_MENU_LABEL,
  MARK_MENU_TRIGGER,
  markClearConfirmText,
  markScopeText,
} from "./ourPriceCopy";

export interface RowMarkMenuProps {
  row: RefRow;
  /** Строка КП панели: от неё тендерная отметка берёт ключ формы. */
  ids: LineIds;
  /** Строка формы словами для окна тендерной отметки: «наименование, единица». */
  formLabel: string;
  testId: string;
}

const KINDS: MarkKind[] = ["tender", "price_error"];

/**
 * Меню отметок строки выдачи поиска (спека «Не аналог» §8 п. 5), только `admin`: те же пункты и окна,
 * что у аналога панели; у отмеченной строки вместо пункта вида — «Снять». Строка договора берётся по
 * `analog_id` (у свёрнутых строк — представитель).
 */
export function RowMarkMenu({ row, ids, formLabel, testId }: RowMarkMenuProps) {
  const setMark = useSetMark();
  const clear = useClearMark();
  const [pending, setPending] = useState<MarkKind | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const has = (kind: MarkKind) => row.marks.some((m) => m.kind === kind);
  const sub = { title: row.title, objectTitle: row.contract.object_title };
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button
              type="button"
              size="icon-xs"
              variant="ghost"
              data-testid={`${testId}-mark-menu`}
              aria-label={MARK_MENU_TRIGGER}
              disabled={setMark.isPending || clear.isPending}
            />
          }
        >
          <MoreHorizontal aria-hidden="true" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-auto min-w-56">
          <DropdownMenuGroup>
            <DropdownMenuLabel>{MARK_MENU_LABEL}</DropdownMenuLabel>
            {KINDS.map((kind) =>
              has(kind) ? (
                <DropdownMenuItem
                  key={kind}
                  data-testid={`${testId}-clear-${kind === "tender" ? "tender" : "price-error"}`}
                  onClick={() => {
                    if (kind === "price_error") setConfirmClear(true);
                    else clear.mutate({ ...ids, kind: "tender", positionItemId: row.analog_id });
                  }}
                >
                  {MARK_CLEAR[kind]}
                </DropdownMenuItem>
              ) : (
                <DropdownMenuItem
                  key={kind}
                  data-testid={`${testId}-mark-${kind === "tender" ? "tender" : "price-error"}`}
                  onClick={() => setPending(kind)}
                >
                  {MARK_ACTION[kind]}
                </DropdownMenuItem>
              )
            )}
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
      <MarkDialog
        kind={pending ?? "tender"}
        title={MARK_DIALOG_TITLE[pending ?? "tender"]}
        scopeText={pending === null ? "" : markScopeText(pending, sub, formLabel, ids.tenderId)}
        open={pending !== null}
        onOpenChange={(next) => {
          if (!next) setPending(null);
        }}
        busy={setMark.isPending}
        onSubmit={(reason) => {
          if (pending === null) return;
          setMark.mutate(
            { ...ids, kind: pending, positionItemId: row.analog_id, reason },
            { onSuccess: () => setPending(null) }
          );
        }}
      />
      <AlertDialog open={confirmClear} onOpenChange={setConfirmClear}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{MARK_CLEAR_CONFIRM_TITLE}</AlertDialogTitle>
            <AlertDialogDescription data-testid="excluded-clear-confirm">
              {markClearConfirmText(sub, null, has("tender") ? ids.tenderId : null)}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel render={<Button variant="outline">{MARK_CANCEL}</Button>} />
            <Button
              type="button"
              onClick={() => {
                clear.mutate({ ...ids, kind: "price_error", positionItemId: row.analog_id });
                setConfirmClear(false);
              }}
            >
              {MARK_CLEAR_CONFIRM_OK}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
