import { useState } from "react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";

/**
 * «Принять все предложения модели (N)» с подтверждением: общая кнопка очередей лестницы
 * аналогов. Запрос и серый слой — у экрана очереди; окно закрывается по подтверждению.
 */
export function AcceptAllDialog({
  count,
  description,
  disabled,
  onConfirm,
}: {
  count: number;
  description: string;
  disabled: boolean;
  onConfirm: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <div>
        <Button type="button" variant="outline" disabled={disabled} onClick={() => setOpen(true)}>
          {`Принять все предложения модели (${count})`}
        </Button>
      </div>
      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Принять все предложения модели?</AlertDialogTitle>
            <AlertDialogDescription>{description}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel render={<Button variant="outline">Отмена</Button>} />
            <AlertDialogAction
              render={
                <Button
                  onClick={() => {
                    setOpen(false);
                    onConfirm();
                  }}
                >
                  Принять все
                </Button>
              }
            />
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
