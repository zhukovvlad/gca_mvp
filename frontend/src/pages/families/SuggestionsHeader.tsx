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
import type { BudgetPause, PausedInfo, PreviewTarget, QueueStatus, StaleUnitInfo } from "@/types/domain";

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

/** Счётчик шапки: подпись и число; нуль тоже печатается — это факт, а не отсутствие данных. */
function Counter({ testId, label, value }: { testId: string; label: string; value: number }) {
  return (
    <span data-testid={testId}>
      {label}: <b className="font-semibold text-fg tabular-nums">{value.toLocaleString("ru-RU")}</b>
    </span>
  );
}

/** Источник отказа 402 «бюджет на лету»: срок короткий, потому у времени печатаются секунды. */
const IN_FLIGHT_BUDGET_SOURCE = "openrouter_in_flight_budget";

function twoDigits(n: number): string {
  return String(n).padStart(2, "0");
}

/**
 * Срок продолжения из `until` (местное время): «03:00», не сегодня — «16.11 в 03:00»; у «бюджета на
 * лету» с секундами. Своего таймера нет: статус перечитывается тем же опросом, что и всё остальное.
 */
function resumeAt(untilIso: string, withSeconds: boolean): string {
  const until = new Date(untilIso);
  const now = new Date();
  const clock = `${twoDigits(until.getHours())}:${twoDigits(until.getMinutes())}${
    withSeconds ? `:${twoDigits(until.getSeconds())}` : ""
  }`;
  const sameDay =
    until.getFullYear() === now.getFullYear() &&
    until.getMonth() === now.getMonth() &&
    until.getDate() === now.getDate();
  return sameDay ? `в ${clock}` : `${twoDigits(until.getDate())}.${twoDigits(until.getMonth() + 1)} в ${clock}`;
}

function budgetPauseText(pause: BudgetPause): string {
  if (pause.source === IN_FLIGHT_BUDGET_SOURCE) {
    return `OpenRouter просит подождать. Продолжим ${resumeAt(pause.until, true)}.`;
  }
  const sums =
    pause.limit_usd !== null && pause.spend_usd !== null
      ? ` (${formatUsd(pause.spend_usd)} из ${formatUsd(pause.limit_usd)})`
      : "";
  return `Лимит OpenRouter исчерпан${sums}. Продолжим ${resumeAt(pause.until, false)}.`;
}

/** Текст плашки остановки по причине (спека provider-budget-pause §2.5). */
function stopText(paused: PausedInfo): ReactNode {
  switch (paused.reason) {
    case "reserve_exceeded":
      return (
        <>
          <b>Захват остановлен:</b> резерв оказался ниже факта. Новые задания не берутся, пока
          остановка не снята.
        </>
      );
    case "provider_credits":
      return "На счёте OpenRouter не хватает денег на запрос. Пополните счёт и снимите остановку.";
    case "provider_budget_no_reset":
      return "Бюджет OpenRouter без сброса исчерпан. Увеличьте его и снимите остановку.";
    case "provider_refusal_unknown":
      return `OpenRouter отказал по деньгам, причина не распознана (попытка ${paused.attempt_id}). Проверьте ключ и счёт, затем снимите остановку.`;
    default:
      return `Захват остановлен (причина: ${paused.reason}).`;
  }
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

      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-[13px] text-fg-secondary">
        <Counter testId="counter-to-review" label="Строк на разборе" value={status.catalog_to_review} />
        <Counter testId="counter-position" label="Строк-работ" value={status.catalog_position} />
        <Counter testId="counter-with-variant" label="Контекстов с вариантом" value={status.contexts_with_variant} />
        <Counter testId="counter-pending" label="Ожидают семьи" value={status.contexts_pending} />
        <Counter testId="counter-without-schema" label="Семей без схемы" value={status.families_without_schema} />
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
              loading={resume.isPending}
              onClick={() => resume.mutate()}
            >
              Снять остановку
            </Button>
          }
        >
          {stopText(status.claim_paused)}
        </Banner>
      )}

      {status.budget_pause && (
        <Banner
          testId="banner-budget-pause"
          tint={WARN_TINT}
          icon={<PauseCircle className="size-[18px]" aria-hidden />}
          actions={null}
        >
          {budgetPauseText(status.budget_pause)}
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
                loading={discard.isPending && discard.variables === batch.batch_id}
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
