import { Trash2, Package, Truck, MoreHorizontal } from "lucide-react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui-domain/Button";
import { MoneyCell } from "@/components/ui-domain/MoneyCell";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { useMaterialClasses } from "@/services/queries";
import type { InvoiceItem } from "@/types/invoice";
import { cn } from "@/lib/utils";

interface ReviewItemsTableProps {
  items: InvoiceItem[];
  onChange?: (items: InvoiceItem[]) => void;
  vatRate?: number;
}

const TYPE_META: Record<
  InvoiceItem["item_type"],
  { label: string; icon: typeof Package; tone: string }
> = {
  material: {
    label: "Материал",
    icon: Package,
    tone: "bg-accent-soft text-accent-text",
  },
  delivery: {
    label: "Доставка",
    icon: Truck,
    tone: "bg-info-soft text-info-text",
  },
  other: {
    label: "Прочее",
    icon: MoreHorizontal,
    tone: "bg-neutral-soft text-neutral-text",
  },
};

export function ReviewItemsTable({ items, onChange, vatRate }: ReviewItemsTableProps) {
  const classes = useMaterialClasses();

  const update = (idx: number, patch: Partial<InvoiceItem>) => {
    if (!onChange) return;
    onChange(
      items.map((it, i) => {
        if (i !== idx) return it;
        const next = { ...it, ...patch };
        // Пересчёт суммы при изменении количества или цены
        if ("quantity" in patch || "unit_price" in patch) {
          next.amount = Math.round(next.quantity * next.unit_price * 100) / 100;
          if (vatRate != null) {
            next.vat_amount = Math.round(next.amount * (vatRate / 100) * 100) / 100;
          }
        }
        return next;
      })
    );
  };

  const withVat = (it: InvoiceItem) => it.amount + (it.vat_amount ?? 0);
  const remove = (idx: number) => {
    if (!onChange) return;
    onChange(items.filter((_, i) => i !== idx));
  };

  if (items.length === 0) {
    return (
      <EmptyState
        title="В этой счёт-фактуре нет позиций"
        description="Возможно, ИИ не смог распознать табличную часть документа. Попробуйте «Переразобрать» или добавьте позиции вручную."
      />
    );
  }

  return (
    <fieldset disabled={!onChange} className="contents">
    <div className="overflow-hidden rounded-lg border border-border-subtle bg-surface">
      {/* Header */}
      <div className="grid grid-cols-[2.5rem_minmax(0,1fr)_9rem_5rem_3.5rem_6rem_7rem_7rem_2.25rem] gap-2 border-b border-border-subtle bg-surface-sunken px-3 py-2 text-2xs uppercase tracking-wider text-fg-tertiary">
        <div className="text-center">№</div>
        <div>Наименование</div>
        <div>Класс материала</div>
        <div className="text-right">Кол-во</div>
        <div>Ед.</div>
        <div className="text-right">
          <div className="inline-block text-center">
            <div>Цена / ед.</div>
            <div className="text-[10px] normal-case tracking-normal text-fg-tertiary/70">без НДС</div>
          </div>
        </div>
        <div className="text-right">
          <div className="inline-block text-center">
            <div>Сумма</div>
            <div className="text-[10px] normal-case tracking-normal text-fg-tertiary/70">без НДС</div>
          </div>
        </div>
        <div className="text-right">
          <div className="inline-block text-center">
            <div>Сумма</div>
            <div className="text-[10px] normal-case tracking-normal text-fg-tertiary/70">с НДС</div>
          </div>
        </div>
        <div></div>
      </div>

      {/* Rows */}
      <div className="divide-y divide-border-subtle">
        {items.map((it, i) => {
          const type = TYPE_META[it.item_type] ?? TYPE_META.other;
          const TypeIcon = type.icon;
          const requireClass = it.item_type === "material";
          const missingClass =
            requireClass && !it.material_class_id && !it.material_class;

          return (
            <div
              key={it.id ?? `new-${i}`}
              className="grid grid-cols-[2.5rem_minmax(0,1fr)_9rem_5rem_3.5rem_6rem_7rem_7rem_2.25rem] items-start gap-2 px-3 py-2.5 hover:bg-surface-hover"
            >
              {/* № */}
              <div className="flex items-center justify-center pt-2 text-xs text-fg-tertiary tabular-nums">
                {i + 1}
              </div>

              {/* Наименование + тип-чип */}
              <div className="min-w-0 space-y-1">
                <Input
                  value={it.raw_name}
                  onChange={(e) => update(i, { raw_name: e.target.value })}
                  placeholder="Наименование"
                  className="w-full"
                />
                <div className="flex items-center gap-1.5">
                  <span
                    className={cn(
                      "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-2xs font-medium",
                      type.tone
                    )}
                  >
                    <TypeIcon size={10} />
                    {type.label}
                  </span>
                  <Select
                    value={it.item_type}
                    onValueChange={(v: string | null) =>
                      update(i, {
                        item_type:
                          (v as InvoiceItem["item_type"]) ?? "other",
                      })
                    }
                  >
                    <SelectTrigger className="h-6 border-none bg-transparent px-1 text-2xs text-fg-tertiary hover:bg-surface-hover">
                      <span>изменить</span>
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="material">Материал</SelectItem>
                      <SelectItem value="delivery">Доставка</SelectItem>
                      <SelectItem value="other">Прочее</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>

              {/* Класс материала */}
              <div className="pt-0.5">
                <Select
                  value={
                    it.material_class_id
                      ? String(it.material_class_id)
                      : it.material_class
                      ? String(it.material_class.id)
                      : ""
                  }
                  onValueChange={(v: string | null) =>
                    update(i, { material_class_id: v ? Number(v) : null })
                  }
                >
                  <SelectTrigger
                    className={cn(
                      "w-full",
                      missingClass &&
                        "border-warning-border bg-warning-soft/40"
                    )}
                  >
                    <SelectValue placeholder={requireClass ? "не задан" : "—"}>
                      {(value) => {
                        const id = value ? Number(value) : null;
                        const found = (classes.data ?? []).find(
                          (c) => c.id === id
                        );
                        if (found) return found.name;
                        if (it.material_class) return it.material_class.name;
                        return requireClass ? "не задан" : "—";
                      }}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {(classes.data ?? []).map((c) => (
                      <SelectItem key={c.id} value={String(c.id)}>
                        {c.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Кол-во */}
              <div className="pt-0.5">
                <Input
                  type="number"
                  step="0.01"
                  value={it.quantity}
                  onChange={(e) =>
                    update(i, { quantity: Number(e.target.value) || 0 })
                  }
                  className="text-right tabular-nums"
                />
              </div>

              {/* Ед. */}
              <div className="pt-0.5">
                <Input
                  value={it.raw_unit}
                  onChange={(e) => update(i, { raw_unit: e.target.value })}
                  placeholder="м³"
                />
              </div>

              {/* Цена за ед. */}
              <div className="pt-0.5">
                <Input
                  type="number"
                  step="0.01"
                  value={it.unit_price}
                  onChange={(e) =>
                    update(i, { unit_price: Number(e.target.value) || 0 })
                  }
                  className="text-right tabular-nums"
                />
              </div>

              {/* Сумма без НДС (readonly, авто) */}
              <div className="flex items-center justify-end whitespace-nowrap pt-2.5 text-sm font-medium">
                <MoneyCell value={it.amount} />
              </div>

              {/* Сумма с НДС (readonly, авто) */}
              <div className="flex items-center justify-end whitespace-nowrap pt-2.5 text-sm font-medium">
                <MoneyCell value={withVat(it)} />
              </div>

              {/* Удалить */}
              <div className="flex items-start justify-end pt-1">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => remove(i)}
                  aria-label="Удалить позицию"
                  className="h-8 w-8 px-0"
                >
                  <Trash2 size={14} />
                </Button>
              </div>
            </div>
          );
        })}
      </div>

      {/* Footer: итог */}
      <div className="grid grid-cols-[2.5rem_minmax(0,1fr)_9rem_5rem_3.5rem_6rem_7rem_7rem_2.25rem] gap-2 border-t border-border-default bg-surface-sunken px-3 py-2.5 text-sm">
        <div></div>
        <div className="text-fg-secondary">Итого по {items.length} позициям</div>
        <div></div>
        <div></div>
        <div></div>
        <div></div>
        <div className="text-right font-medium">
          <MoneyCell value={items.reduce((s, it) => s + (it.amount || 0), 0)} />
        </div>
        <div className="text-right font-medium">
          <MoneyCell value={items.reduce((s, it) => s + withVat(it), 0)} />
        </div>
        <div></div>
      </div>
    </div>
    </fieldset>
  );
}
