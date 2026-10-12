import { AlertTriangle } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import type { WorkFamily } from "@/types/domain";

import { rerunResetWarningLabel } from "./labels";

const WARN_TINT = "border-warning-border bg-warning-soft text-warning-text";

/**
 * Предупреждение в окне действия над семьёй, меняющем список семей единицы в теле запроса
 * предложения (правка названия или определения, архивирование, слияние): пока в единице
 * идёт перезапрос, изменение отменит его задания. Действие не блокируется. Только у
 * активной семьи — черновика и архивной в теле запроса нет.
 */
export function RerunResetWarning({
  family,
}: {
  family: Pick<WorkFamily, "status" | "unit_symbol" | "unit_code" | "unit_busy_jobs">;
}) {
  if (family.status !== "active" || family.unit_busy_jobs <= 0) return null;
  const unit = family.unit_symbol ?? family.unit_code ?? "без единицы";
  return (
    <Alert data-testid="rerun-reset-warning" className={`flex items-start gap-2 px-3 py-2 ${WARN_TINT}`}>
      <AlertTriangle className="mt-0.5 size-4 flex-none" />
      <span className="min-w-0 flex-1 text-[13px]">
        {rerunResetWarningLabel(unit, family.unit_busy_jobs)}
      </span>
    </Alert>
  );
}
