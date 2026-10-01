import { AlertTriangle, PauseCircle } from "lucide-react";
import type { ReactNode } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { formatUsd, ratioPercent } from "@/lib/format";
import {
  useDiscardBatch,
  useResumeWorker,
  useUnits,
} from "@/services/queries";
import type { PreviewTarget, QueueStatus, StaleUnitInfo } from "@/types/domain";

import { BATCH_SOURCE_LABEL, pluralRu } from "./labels";

const HOLD_TINT = "border-info-border bg-info-soft text-info-text";
const WARN_TINT = "border-warning-border bg-warning-soft text-warning-text";

/** Плашка шапки: значок, текст, действия справа (макет 25.09.2026, `.banner`). */
function Banner({
  tint,
  icon,
  actions,
  children,
  testId,
}: {
  tint: string;
  icon: ReactNode;
  actions: ReactNode;
  children: ReactNode;
  testId: string;
}) {
  return (
    <Alert data-testid={testId} className={`flex items-center gap-3 px-3 py-2 ${tint}`}>
      <span className="flex-none">{icon}</span>
      <span className="min-w-0 flex-1 text-[13px]">{children}</span>
      <div className="flex flex-none gap-2">{actions}</div>
    </Alert>
  );
}

interface SuggestionsHeaderProps {
  status: QueueStatus;
  /** Открыть диалог preview для перезапроса или удержанной пачки. */
  onPreview: (target: PreviewTarget, unitLabel?: string) => void;
}

/**
 * Шапка вкладки «Предложения» (спека semantic-suggestions §2.12): расход за
 * 24 часа против суточного бюджета и плашки. Каждая плашка появляется при
 * своём условии и только при нём: остановка захвата — при `claim_paused`,
 * «Удержано» — на каждую удержанную пачку, «Конфигурация изменена» — при
 * `config_stale`, «Список семей единицы изменён» — на каждую устаревшую единицу.
 */
export function SuggestionsHeader({ status, onPreview }: SuggestionsHeaderProps) {
  const unitsQ = useUnits();
  const resume = useResumeWorker();
  const discard = useDiscardBatch();

  function unitLabel(unit: StaleUnitInfo): string {
    if (unit.unit_code === null) return "без единицы";
    return unitsQ.data?.find((u) => u.code === unit.unit_code)?.symbol ?? unit.unit_code;
  }

  const budget = formatUsd(status.daily_budget_usd).replace(/,00$/, "");
  const percent = ratioPercent(status.spent_24h_usd, status.daily_budget_usd);

  return (
    <div className="grid gap-3">
      <div className="flex items-center gap-3 text-[13px] text-fg-secondary">
        <div className="ml-auto flex items-center gap-2">
          <span>Расход за 24 ч</span>
          <Progress
            value={percent}
            aria-label="Расход за 24 часа от суточного бюджета"
            className="w-32 flex-nowrap [&_[data-slot=progress-indicator]]:bg-accent [&_[data-slot=progress-track]]:h-1.5 [&_[data-slot=progress-track]]:bg-border"
          />
          <b className="font-semibold text-fg tabular-nums">{formatUsd(status.spent_24h_usd)}</b>
          <span>из {budget}</span>
        </div>
      </div>

      {status.claim_paused && (
        <Banner
          testId="banner-paused"
          tint={WARN_TINT}
          icon={<PauseCircle className="size-[18px]" aria-hidden />}
          actions={
            <Button
              variant="outline"
              size="sm"
              disabled={resume.isPending}
              onClick={() => resume.mutate()}
            >
              Снять остановку
            </Button>
          }
        >
          <b>Захват остановлен:</b> резерв оказался ниже факта. Новые задания не берутся, пока
          остановка не снята.
        </Banner>
      )}

      {status.held_batches.map((batch) => (
        <Banner
          key={batch.batch_id}
          testId="banner-held"
          tint={HOLD_TINT}
          icon={<PauseCircle className="size-[18px]" aria-hidden />}
          actions={
            <>
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  onPreview({ kind: "batch", batchId: batch.batch_id, source: batch.source })
                }
              >
                Поставить…
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={discard.isPending}
                onClick={() => discard.mutate(batch.batch_id)}
              >
                Отбросить
              </Button>
            </>
          }
        >
          <b>Удержано:</b> {BATCH_SOURCE_LABEL[batch.source]}
          {batch.import_job_id !== null && ` (job ${batch.import_job_id})`} — {batch.contexts_count.toLocaleString("ru-RU")}{" "}
          {pluralRu(batch.contexts_count, "контекст", "контекста", "контекстов")}, резерв{" "}
          {formatUsd(batch.reserve_usd)}, ожидаемо ≈ {formatUsd(batch.expected_cached_usd)}
        </Banner>
      ))}

      {status.config_stale && (
        <Banner
          testId="banner-config"
          tint={WARN_TINT}
          icon={<AlertTriangle className="size-[18px]" aria-hidden />}
          actions={
            <Button variant="outline" size="sm" onClick={() => onPreview({ kind: "config" })}>
              Перезапросить всё…
            </Button>
          }
        >
          <b>Конфигурация изменена</b> (версия промпта {status.config_stale.prompt_version_current}).
          К перезапросу {status.config_stale.stale_count.toLocaleString("ru-RU")}{" "}
          {pluralRu(status.config_stale.stale_count, "контекст", "контекста", "контекстов")}.
        </Banner>
      )}

      {status.stale_units.map((unit) => {
        const label = unitLabel(unit);
        return (
          <Banner
            key={unit.unit_id ?? "none"}
            testId="banner-stale-unit"
            tint={WARN_TINT}
            icon={<AlertTriangle className="size-[18px]" aria-hidden />}
            actions={
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  onPreview({ kind: "unit", unitId: unit.unit_id, unitCode: unit.unit_code }, label)
                }
              >
                Перезапросить {label}…
              </Button>
            }
          >
            <b>{label}:</b> список семей изменён, к перезапросу {unit.stale_count.toLocaleString("ru-RU")}{" "}
            {pluralRu(unit.stale_count, "контекст", "контекста", "контекстов")}
          </Banner>
        );
      })}
    </div>
  );
}
