import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

import { SOURCE_EXPLANATION, type SourceKind } from "./labels";

const SOURCE_TEXT: Record<SourceKind, string> = {
  classifier: "статья СМР",
  estimate: "в смете",
};

/**
 * Заливка чипа по источнику (сверка с макетом 27.09.2026, `.src-c`/`.src-e`
 * макета) — «статья СМР» фиолетовый/индиго, «в смете» жёлтый/амбер; «система»
 * несёт СВОЙ, третий тон (`bg-info-soft`, спека §2.3 — синий), рядом в
 * `ContextsTab.tsx`/`ContextCard.tsx`. Три плашки экрана обязаны различаться
 * КЛАССОМ, не только текстом — jsdom вычисленный цвет не видит
 * (`docs/pitfalls/frontend.md`), поэтому регрессию держит `SourceChip.test.tsx`
 * сравнением `className`, а не глазом.
 */
const SOURCE_TINT: Record<SourceKind, string> = {
  classifier: "border-source-classifier-border bg-source-classifier-soft text-source-classifier-text",
  estimate: "border-warning-border bg-warning-soft text-warning-text",
};

/**
 * Плашка источника подписи (спека §2.3) — «статья СМР» (пункт
 * классификатора `work_categories`, общий для всех смет) или «в смете»
 * (раздел `position_items.job_title_in_proposal` конкретной сметы).
 * Ставится перед текстом везде, где он показан: строка списка, шапка
 * карточки, пути членств, работа имени-места (спека §2.3).
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
          <Badge variant="outline" className={cn("cursor-help", SOURCE_TINT[kind])}>
            {text}
          </Badge>
        }
      />
      <TooltipContent>{explanation}</TooltipContent>
    </Tooltip>
  );
}
