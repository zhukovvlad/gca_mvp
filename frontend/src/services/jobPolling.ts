import { TERMINAL_JOB_STATUSES, type ImportJob } from "@/types/domain";

/** Как часто опрашивать задание импорта, пока оно не завершилось. */
export const JOB_POLL_INTERVAL_MS = 1500;

/**
 * Интервал поллинга задания импорта — **останавливается** на терминальном статусе.
 *
 * Без остановки экран опрашивал бы сервер вечно (грабли §7 брифинга фазы 5):
 * задание в `done` больше не меняется, а запросы продолжали бы идти, пока
 * открыта вкладка. `false` — то, чем TanStack Query выключает интервал.
 *
 * Данных ещё нет (первый рендер после `202`) — опрашиваем: задание в этот момент
 * как раз в `pending`.
 */
export function jobRefetchInterval(job: ImportJob | undefined): number | false {
  if (!job) return JOB_POLL_INTERVAL_MS;
  return TERMINAL_JOB_STATUSES.includes(job.status) ? false : JOB_POLL_INTERVAL_MS;
}

/** Завершилось ли задание: `done` либо `error`. */
export function isTerminal(job: ImportJob | undefined | null): boolean {
  return !!job && TERMINAL_JOB_STATUSES.includes(job.status);
}
