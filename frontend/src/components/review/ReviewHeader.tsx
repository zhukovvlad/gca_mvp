import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { InvoiceRow } from "@/types/invoice";

interface ReviewHeaderProps {
  invoice: InvoiceRow;
  onChange?: (patch: Partial<InvoiceRow>) => void;
}

export function ReviewHeader({ invoice, onChange }: ReviewHeaderProps) {
  return (
    <fieldset disabled={!onChange} className="contents">
      <div className="grid grid-cols-2 gap-4">
        <Field label="Номер СФ">
          <Input
            value={invoice.number}
            onChange={(e) => onChange?.({ number: e.target.value })}
          />
        </Field>
        <Field label="Дата">
          <Input
            type="date"
            value={invoice.date}
            onChange={(e) => onChange?.({ date: e.target.value })}
          />
        </Field>
        <Field label="Поставщик">
          <Input
            value={invoice.supplier_name ?? ""}
            onChange={(e) => onChange?.({ supplier_name: e.target.value })}
          />
        </Field>
        <Field label="ИНН">
          <Input
            value={invoice.supplier_inn ?? ""}
            onChange={(e) => onChange?.({ supplier_inn: e.target.value })}
          />
        </Field>
        <Field label="Ставка НДС, %">
          <Input
            type="number"
            step="0.01"
            value={invoice.vat_rate}
            onChange={(e) =>
              onChange?.({ vat_rate: Number(e.target.value) || 0 })
            }
          />
        </Field>
      </div>
    </fieldset>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
        {label}
      </Label>
      {children}
    </div>
  );
}
