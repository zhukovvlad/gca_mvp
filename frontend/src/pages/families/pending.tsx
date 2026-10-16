import { useEffect, useState } from "react";

import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Progress } from "@/components/ui/progress";

/**
 * Сколько перечитывание должно длиться, прежде чем покажется полоса: фоновый опрос с быстрым
 * ответом полосу не показывает.
 */
export const FETCHING_BAR_DELAY_MS = 400;

/**
 * Полоса перечитывания раздела «Семьи»: данные уже на экране, новые едут. Место под полосу
 * занято всегда, поэтому содержимое не прыгает; сама полоса — неопределённый `Progress`.
 */
export function FetchingBar({ active }: { active: boolean }) {
  const [elapsed, setElapsed] = useState(false);
  useEffect(() => {
    if (!active) return;
    const timer = setTimeout(() => setElapsed(true), FETCHING_BAR_DELAY_MS);
    return () => {
      clearTimeout(timer);
      setElapsed(false);
    };
  }, [active]);
  return (
    <div className="h-1 w-full" data-testid="fetching-bar-slot">
      {active && elapsed && (
        <Progress
          value={null}
          aria-label="Обновление данных"
          className="gap-0 [&_[data-slot=progress-indicator]]:w-full [&_[data-slot=progress-indicator]]:animate-pulse"
        />
      )}
    </div>
  );
}

/** Заглушка списка при первой загрузке: несколько строк по форме содержимого. */
export function ListSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div aria-busy="true" data-testid="list-skeleton" className="grid gap-2">
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} className="h-12 w-full" />
      ))}
    </div>
  );
}
