import type { ExcludedAnalog, WorkRung } from "@/types/domain";

import { excludedMovedOn } from "./ourPriceCopy";

type Step = ExcludedAnalog["step"];

export const stepKey = (step: Step) => `${step.rung}-${step.zone_scope}`;

/** Пары следа в порядке первого появления: сервер отдаёт их в порядке обхода подбора. */
export function groupByStep(items: ExcludedAnalog[]): Array<{ step: Step; items: ExcludedAnalog[] }> {
  const groups = new Map<string, { step: Step; items: ExcludedAnalog[] }>();
  for (const item of items) {
    const key = stepKey(item.step);
    const group = groups.get(key);
    if (group === undefined) groups.set(key, { step: item.step, items: [item] });
    else group.items.push(item);
  }
  return [...groups.values()];
}

/**
 * Строка «почему подбор ушёл дальше» (спека «Не аналог» §8 п. 3): пары следа раньше пары `work_rung`
 * с числом исключённых в каждой, через «;». Строка без ступени и без пар раньше — `null`.
 */
export function movedOnText(items: ExcludedAnalog[], workRung: WorkRung | null): string | null {
  if (workRung === null) return null;
  const workKey = stepKey({ rung: workRung.no, zone_scope: workRung.zone_scope });
  const skipped: Array<{ step: Step; count: number }> = [];
  for (const group of groupByStep(items)) {
    if (stepKey(group.step) === workKey) break;
    skipped.push({ step: group.step, count: group.items.length });
  }
  if (skipped.length === 0) return null;
  return excludedMovedOn({ rung: workRung.no, zone_scope: workRung.zone_scope }, skipped);
}
