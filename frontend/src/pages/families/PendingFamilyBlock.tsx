import { Surface } from "@/components/ui-domain/Surface";
import { Button } from "@/components/ui/button";
import { formatConfidence, formatDate } from "@/lib/format";
import { useCancelPendingFamily } from "@/services/queries";
import type { PendingFamily } from "@/types/domain";

interface PendingFamilyBlockProps {
  contextId: number;
  pending: PendingFamily;
}

/** Кто поставил ожидание: автоматическое принятие помечено словом и порогом, остальное — человек. */
function whoLabel(pending: PendingFamily): string {
  return pending.source === "auto_suggestion" ? "поставлено автоматически" : "поставил оператор";
}

/**
 * Блок «Ожидает семьи» карточки контекста (спека `2026-10-02-catalog-variants-design.md` §2.5,
 * §2.12): контекст с вариантом сменит семью только после значений по схеме новой семьи. Показывает
 * семью, кто и когда поставил ожидание, порог (только у автоматического принятия) и «Отменить».
 */
export function PendingFamilyBlock({ contextId, pending }: PendingFamilyBlockProps) {
  const cancel = useCancelPendingFamily();
  const parts = [whoLabel(pending), formatDate(pending.at)];
  if (pending.threshold !== null) parts.push(`порог ${formatConfidence(pending.threshold)}`);

  return (
    <Surface className="grid min-w-0 gap-2 border-warning/40 bg-warning/5">
      <p className="text-sm font-medium text-fg">Ожидает семьи: «{pending.family_title}»</p>
      <p className="text-sm text-fg-secondary">{parts.join(" · ")}</p>
      <p className="text-sm text-fg-tertiary">ожидает значений по схеме цели</p>
      <div>
        <Button
          size="xs"
          variant="outline"
          aria-label="Отменить ожидание семьи"
          disabled={cancel.isPending}
          onClick={() => cancel.mutate(contextId)}
        >
          Отменить
        </Button>
      </div>
    </Surface>
  );
}
