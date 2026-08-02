import { useMemo } from "react";

import { KpiCard } from "@/components/ui-domain/KpiCard";
import { DEFAULT_CONFIDENCE_THRESHOLD } from "@/lib/constants";
import { pluralRu } from "@/lib/format";
import { useDocuments, useSettings } from "@/services/queries";
import { getStage } from "./invoiceStage";
import type { ID } from "@/types/common";
import type { DashboardInvoiceRow } from "@/types/invoice";

interface InvoiceKpiBarProps {
  invoices: DashboardInvoiceRow[];
  projectId: ID;
}

export function InvoiceKpiBar({ invoices, projectId }: InvoiceKpiBarProps) {
  const settingsQ = useSettings();
  const threshold = settingsQ.data?.confidence_threshold ?? DEFAULT_CONFIDENCE_THRESHOLD;
  const docsQ = useDocuments(projectId);

  const stats = useMemo(() => {
    const confirmed = invoices.filter((inv) => inv.verified).length;
    const pending   = invoices.filter((inv) => getStage(inv, threshold) === "pending").length;
    const errorDocIds = new Set<ID>(
      invoices.filter((inv) => inv.has_issues).map((inv) => inv.document_id),
    );
    (docsQ.data ?? [])
      .filter((doc) => doc.status === "error")
      .forEach((doc) => errorDocIds.add(doc.id));
    return { confirmed, pending, errorDocs: errorDocIds.size };
  }, [invoices, threshold, docsQ.data]);

  const total = invoices.length;
  if (total === 0) return null;

  const { confirmed, pending, errorDocs } = stats;

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <KpiCard
        label="Всего счетов"
        value={String(total)}
      />
      <KpiCard
        label="Подтверждено"
        value={String(confirmed)}
        suffix={`/ ${total}`}
        className="border-accent-border bg-accent-soft"
        valueClassName="text-accent-text"
      />
      <KpiCard
        label="Ожидает подтверждения"
        value={String(pending)}
        suffix={`/ ${total}`}
        className="border-neutral-border bg-neutral-soft"
        valueClassName="text-neutral-text"
      />
      <KpiCard
        label={`Документ${pluralRu(errorDocs)} с ошибками`}
        value={String(errorDocs)}
        className={errorDocs > 0 ? "border-danger-border bg-danger-soft" : ""}
        valueClassName={errorDocs > 0 ? "text-danger" : "text-fg-muted"}
      />
    </div>
  );
}
