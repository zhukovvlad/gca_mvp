import { StatusPill } from "@/components/ui-domain/StatusPill";
import { formatDecimalMoney } from "@/lib/format";
import type { TenderRoundRow } from "@/types/domain";

/**
 * Состояние расчётной стоимости раунда — по ИСТОЧНИКУ факта, а не по одному
 * булеву (спека §2.14): до первой загрузки экран не вправе утверждать, что
 * база пуста, — файла он ещё не видел (AGENTS.md §11, «пустое поле — отсутствие
 * факта»).
 */
export function BaselineStatus({ round, hasEstimates }: { round: TenderRoundRow; hasEstimates: boolean }) {
  if (round.current_job_id === null) {
    return hasEstimates ? (
      <StatusPill tone="warning" label="Состав раунда изменён после импорта — требуется полная замена" />
    ) : (
      <StatusPill tone="neutral" label="Файл раунда не загружен" />
    );
  }
  if (round.baseline_estimate_id === null) {
    return <StatusPill tone="neutral" label="Расчётная стоимость в файле не заполнена" />;
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <StatusPill tone="success" label="Расчётная стоимость загружена" />
      <span className="text-sm text-fg-secondary">
        Итого с НДС: {formatDecimalMoney(round.baseline_total_including_vat)}
      </span>
    </div>
  );
}
