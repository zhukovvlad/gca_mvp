import { useState } from "react";
import { Loader2, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { cn } from "@/lib/utils";
import { useDebounce } from "@/lib/useDebounce";
import { useMergeReview, useMergeTargets } from "@/services/queries";
import type { ReviewQueueItem } from "@/types/domain";

interface MergeTargetDialogProps {
  item: ReviewQueueItem | null;
  onOpenChange: (open: boolean) => void;
}

/**
 * Выбор каталожной POSITION-строки, с которой сливается строка очереди (§5).
 *
 * Поиск по человеческому названию — ILIKE по отображаемому названию и по леммам
 * (решение §6.4). Единица строки передаётся как **подсказка**: цели в той же
 * единице идут первыми, но цели в другой не скрываются — слить осознанно оператор
 * вправе, и решать это за него экран не должен.
 *
 * После слияния строка очереди исчезает: её ссылки перенесены, а сама она
 * удалена, чтобы освободить свою нормализованную пару.
 */
export function MergeTargetDialog({ item, onOpenChange }: MergeTargetDialogProps) {
  return (
    <Dialog open={item !== null} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        {/*
          Содержимое монтируется заново на каждое открытие, поэтому поиск
          предзаполняется через `useState`, а не эффектом-сбросом.
        */}
        {item && <MergeTargetForm key={item.id} item={item} onOpenChange={onOpenChange} />}
      </DialogContent>
    </Dialog>
  );
}

function MergeTargetForm({
  item,
  onOpenChange,
}: {
  item: ReviewQueueItem;
  onOpenChange: (open: boolean) => void;
}) {
  // Поиск предзаполнен названием разбираемой строки — чаще всего цель называется
  // почти так же.
  const [queryInput, setQueryInput] = useState(item.standard_job_title);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const query = useDebounce(queryInput, 300);

  const targetsQ = useMergeTargets(query, item.unit_id ?? undefined);
  const merge = useMergeReview();

  async function handleMerge() {
    if (selectedId === null) return;
    try {
      await merge.mutateAsync({ toReviewId: item.id, targetId: selectedId });
      onOpenChange(false);
    } catch {
      // Причина в тосте: как правило, строку уже разобрал другой оператор (409).
    }
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>Слить с каталожной работой</DialogTitle>
        <DialogDescription>
          «{item.standard_job_title}»
          {item.unit_code ? ` · ${item.unit_code}` : " · без единицы"} · позиций:{" "}
          {item.position_count}. Решение применится ко всем этим позициям.
        </DialogDescription>
      </DialogHeader>

      <InputGroup>
        <InputGroupInput
          aria-label="Поиск каталожной работы"
          placeholder="Название работы или его часть"
          value={queryInput}
          onChange={(e) => setQueryInput(e.target.value)}
        />
        <InputGroupAddon align="inline-start">
          <Search size={13} />
        </InputGroupAddon>
      </InputGroup>

      <div className="max-h-72 overflow-y-auto rounded-md border border-border-subtle">
        {targetsQ.isPending && query.trim() && (
          <p className="flex items-center gap-2 p-3 text-sm text-fg-secondary">
            <Loader2 className="size-4 animate-spin" /> Поиск…
          </p>
        )}
        {targetsQ.data?.length === 0 && (
          <p className="p-3 text-sm text-fg-secondary">
            Подходящих работ не найдено. Если это новая работа — закройте диалог и нажмите
            «Утвердить как работу».
          </p>
        )}
        <ul>
          {(targetsQ.data ?? []).map((target) => (
            <li key={target.id}>
              <button
                type="button"
                onClick={() => setSelectedId(target.id)}
                aria-pressed={selectedId === target.id}
                className={cn(
                  "flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm hover:bg-surface-hover",
                  selectedId === target.id && "bg-surface-hover"
                )}
              >
                <span className="truncate">{target.standard_job_title}</span>
                <span className="shrink-0 text-xs text-fg-tertiary">
                  {target.unit_code ?? "без единицы"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>

      <DialogFooter>
        <Button variant="outline" onClick={() => onOpenChange(false)}>
          Отмена
        </Button>
        <Button onClick={handleMerge} disabled={selectedId === null || merge.isPending}>
          Слить
        </Button>
      </DialogFooter>
    </>
  );
}
