import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

import { SOURCE_EXPLANATION, type SourceKind } from "./labels";

const SOURCE_TEXT: Record<SourceKind, string> = {
  classifier: "статья СМР",
  estimate: "в смете",
};

/**
 * Плашка источника подписи (спека §2.3) — «статья СМР» (пункт
 * классификатора `work_categories`, общий для всех смет) или «в смете»
 * (раздел `position_items.job_title_in_proposal` конкретной сметы).
 * Ставится перед текстом везде, где он показан: строка списка, шапка
 * карточки, пути членств, работа имени-места (Task 7–8).
 *
 * Пояснение несёт и `title` (тестируемо, доступно без наведения — атрибут
 * читает скринридер и показывает нативный тултип), и `TooltipContent`
 * (визуальная подсказка shadcn/base-ui при наведении в браузере).
 */
export function SourceChip({ kind }: { kind: SourceKind }) {
  const text = SOURCE_TEXT[kind];
  const explanation = SOURCE_EXPLANATION[kind];

  return (
    <Tooltip>
      <TooltipTrigger
        title={explanation}
        render={
          <Badge variant="outline" className="cursor-help">
            {text}
          </Badge>
        }
      />
      <TooltipContent>{explanation}</TooltipContent>
    </Tooltip>
  );
}
