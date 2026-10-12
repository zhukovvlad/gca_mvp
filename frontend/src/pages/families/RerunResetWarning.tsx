import { AlertTriangle } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import type { WorkFamily } from "@/types/domain";

import { rerunResetWarningBothLabel, rerunResetWarningLabel } from "./labels";

const WARN_TINT = "border-warning-border bg-warning-soft text-warning-text";

/**
 * Предупреждение в окне действия над семьёй, меняющем список семей единицы в теле запроса
 * предложения (правка названия или определения, архивирование, слияние): пока в единице
 * идёт перезапрос, изменение отменит его задания. Действие не блокируется. Только у
 * активной семьи — черновика и архивной в теле запроса нет.
 *
 * При смене единицы затронуты две единицы: прежняя (семья из неё уходит) и новая (семья
 * попадает в её список кандидатов). `target` — новая единица и её живые задания;
 * `fromApplies=false` — прежнюю единицу не считать (смены нет или она не наступила).
 */
export function RerunResetWarning({
  family,
  target = null,
  fromApplies = true,
}: {
  family: Pick<WorkFamily, "status" | "unit_symbol" | "unit_code" | "unit_busy_jobs">;
  target?: { unit: string; count: number } | null;
  fromApplies?: boolean;
}) {
  if (family.status !== "active") return null;
  const from =
    fromApplies && family.unit_busy_jobs > 0
      ? { unit: family.unit_symbol ?? family.unit_code ?? "без единицы", count: family.unit_busy_jobs }
      : null;
  const to = target !== null && target.count > 0 ? target : null;
  const only = from ?? to;
  if (only === null) return null;
  const text =
    from !== null && to !== null
      ? rerunResetWarningBothLabel(from, to)
      : rerunResetWarningLabel(only.unit, only.count);
  return (
    <Alert data-testid="rerun-reset-warning" className={`flex items-start gap-2 px-3 py-2 ${WARN_TINT}`}>
      <AlertTriangle className="mt-0.5 size-4 flex-none" />
      <span className="min-w-0 flex-1 text-[13px]">
        {text}
      </span>
    </Alert>
  );
}
