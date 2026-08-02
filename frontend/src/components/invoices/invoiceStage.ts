import type { DashboardInvoiceRow } from "@/types/invoice";

export type Stage = "confirmed" | "review" | "pending";

export function getStage(inv: DashboardInvoiceRow, threshold: number): Stage {
  if (inv.verified) return "confirmed";
  if (
    inv.has_issues ||
    !inv.supplier_name?.trim() ||
    !inv.number?.trim() ||
    (inv.ai_confidence ?? 0) < threshold
  )
    return "review";
  return "pending";
}
