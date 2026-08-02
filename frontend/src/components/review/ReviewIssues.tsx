import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { useSettings } from "@/services/queries";
import { DEFAULT_CONFIDENCE_THRESHOLD } from "@/lib/constants";
import type { InvoiceRow } from "@/types/invoice";

interface ReviewIssuesProps {
  invoice: InvoiceRow;
}

export function ReviewIssues({ invoice }: ReviewIssuesProps) {
  const settingsQ = useSettings();
  const threshold = settingsQ.data?.confidence_threshold ?? DEFAULT_CONFIDENCE_THRESHOLD;
  const issues: string[] = [];
  if ((invoice.ai_confidence ?? 0) < threshold) {
    issues.push("Низкая уверенность ИИ — проверьте все поля вручную.");
  }
  if (!invoice.supplier_name?.trim()) issues.push("Не указан поставщик.");
  if (!invoice.number?.trim()) issues.push("Не указан номер СФ.");
  if (invoice.items.length === 0) issues.push("Нет ни одной позиции.");
  invoice.items.forEach((it, i) => {
    if (!it.raw_name?.trim()) issues.push(`Позиция ${i + 1}: пустое наименование.`);
    if ((it.quantity ?? 0) <= 0) issues.push(`Позиция ${i + 1}: некорректное количество.`);
  });

  if (issues.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-md bg-accent-soft p-3 text-accent-text">
        <CheckCircle2 size={16} />
        <span className="text-sm">Замечаний не найдено.</span>
      </div>
    );
  }

  return (
    <ul className="space-y-1.5">
      {issues.map((msg, i) => (
        <li
          key={i}
          className="flex items-start gap-2 rounded-md border border-warning-border bg-warning-soft p-3 text-sm text-warning-text"
        >
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span>{msg}</span>
        </li>
      ))}
    </ul>
  );
}
