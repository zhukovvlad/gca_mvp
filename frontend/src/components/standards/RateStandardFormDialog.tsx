import { useState } from "react";
import { Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { normalizeDecimalInput } from "@/lib/decimal";
import { cn } from "@/lib/utils";
import { useDebounce } from "@/lib/useDebounce";
import { useCatalogSearch, useCreateRateStandard, useRateClasses } from "@/services/queries";

interface RateStandardFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Создание норматива (§7.3).
 *
 * Работа выбирается поиском по каталогу (`kind='POSITION'`), класс — из
 * справочника. Ставка — текстовое поле: §3 запрещает float, а
 * `<input type="number">` отдал бы именно его.
 *
 * Дата окончания необязательна: пустая означает открытый период (бесконечная
 * верхняя граница, §4). Пересечение периодов ловит сервер и отвечает 400 с
 * человеческим текстом — экран показывает его тостом, а не изобретает свою
 * проверку, которая разошлась бы с EXCLUDE.
 */
export function RateStandardFormDialog({ open, onOpenChange }: RateStandardFormDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        {/*
          Тело формы монтируется заново на каждое открытие — начальные значения
          задаёт `useState`, а не сбрасывающий эффект.
        */}
        {open && <RateStandardForm onOpenChange={onOpenChange} />}
      </DialogContent>
    </Dialog>
  );
}

function RateStandardForm({ onOpenChange }: { onOpenChange: (open: boolean) => void }) {
  const [positionQuery, setPositionQuery] = useState("");
  const [positionId, setPositionId] = useState<number | null>(null);
  const [positionLabel, setPositionLabel] = useState("");
  const [rateClassId, setRateClassId] = useState<string>("");
  const [rate, setRate] = useState("");
  const [validFrom, setValidFrom] = useState("");
  const [validTo, setValidTo] = useState("");
  const [approvedBy, setApprovedBy] = useState("");
  const [note, setNote] = useState("");

  const query = useDebounce(positionQuery, 300);
  const positionsQ = useCatalogSearch(query);
  const classesQ = useRateClasses();
  const create = useCreateRateStandard();

  const canSubmit =
    positionId !== null && rateClassId !== "" && rate.trim() !== "" && validFrom !== "";

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    try {
      await create.mutateAsync({
        catalog_position_id: positionId as number,
        rate_class_id: Number(rateClassId),
        standard_unit_rate: normalizeDecimalInput(rate),
        valid_from: validFrom,
        valid_to: validTo || null,
        approved_by: approvedBy.trim() || null,
        note: note.trim() || null,
      });
      onOpenChange(false);
    } catch {
      // Отказ уже в тосте — чаще всего пересечение периодов (400).
    }
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>Новый норматив</DialogTitle>
        <DialogDescription>
          Ставка действует для пары «работа × класс объектов» на период. На каждую
          дату у пары может действовать только одна ставка.
        </DialogDescription>
      </DialogHeader>

      <form onSubmit={handleSubmit} className="grid gap-4">
        <div className="grid gap-2">
          <Label htmlFor="standard-position">Работа из каталога</Label>
          <InputGroup>
            <InputGroupInput
              id="standard-position"
              placeholder="Название работы"
              value={positionId !== null ? positionLabel : positionQuery}
              onChange={(e) => {
                setPositionQuery(e.target.value);
                setPositionId(null);
              }}
            />
            <InputGroupAddon align="inline-start">
              <Search size={13} />
            </InputGroupAddon>
          </InputGroup>
          {positionId === null && query.trim() !== "" && (
            <div className="max-h-40 overflow-y-auto rounded-md border border-border-subtle">
              {positionsQ.data?.length === 0 && (
                <p className="p-2 text-xs text-fg-secondary">
                  Работа не найдена. Каталог наполняется загрузкой смет и разбором
                  очереди ручного матчинга.
                </p>
              )}
              <ul>
                {(positionsQ.data ?? []).map((position) => (
                  <li key={position.id}>
                    <button
                      type="button"
                      className={cn(
                        "flex w-full items-center justify-between gap-2 px-2 py-1.5 text-left text-sm hover:bg-surface-hover"
                      )}
                      onClick={() => {
                        setPositionId(position.id);
                        setPositionLabel(position.standard_job_title);
                      }}
                    >
                      <span className="truncate">{position.standard_job_title}</span>
                      <span className="shrink-0 text-xs text-fg-tertiary">
                        {position.unit_code ?? "без единицы"}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        <div className="grid gap-2">
          <Label htmlFor="standard-class">Класс объектов</Label>
          <Select
            value={rateClassId}
            onValueChange={(value: string | null) => setRateClassId(value ?? "")}
          >
            <SelectTrigger id="standard-class">
              <SelectValue placeholder="Выберите класс">
                {(raw) =>
                  raw
                    ? (classesQ.data?.find((c) => String(c.id) === raw)?.title ??
                      "Выберите класс")
                    : "Выберите класс"
                }
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {(classesQ.data ?? []).map((rateClass) => (
                <SelectItem key={rateClass.id} value={String(rateClass.id)}>
                  {rateClass.title}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
          <div className="grid gap-2">
            <Label htmlFor="standard-rate">Ставка за единицу</Label>
            <Input
              id="standard-rate"
              inputMode="decimal"
              placeholder="1000.00"
              value={rate}
              onChange={(e) => setRate(e.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="standard-from">Действует с</Label>
            <Input
              id="standard-from"
              type="date"
              value={validFrom}
              onChange={(e) => setValidFrom(e.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="standard-to">Действует до</Label>
            <Input
              id="standard-to"
              type="date"
              value={validTo}
              onChange={(e) => setValidTo(e.target.value)}
            />
          </div>
        </div>
        <p className="text-xs text-fg-tertiary">
          «Действует до» можно оставить пустым — период будет открытым. Дата окончания
          в период не входит: она же может быть началом следующей ставки.
        </p>

        <div className="grid gap-2">
          <Label htmlFor="standard-approved-by">Утвердил</Label>
          <Input
            id="standard-approved-by"
            value={approvedBy}
            onChange={(e) => setApprovedBy(e.target.value)}
          />
        </div>

        <div className="grid gap-2">
          <Label htmlFor="standard-note">Примечание</Label>
          <Textarea
            id="standard-note"
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Отмена
          </Button>
          <Button type="submit" disabled={!canSubmit || create.isPending}>
            Создать норматив
          </Button>
        </DialogFooter>
      </form>
    </>
  );
}
