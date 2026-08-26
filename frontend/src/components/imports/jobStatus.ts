import type { ImportJobStatus } from "@/types/domain";

/**
 * Константы и чистые функции статуса задания импорта, вынесенные из
 * `ImportJobPanel.tsx` в СВОЙ модуль (задача 11, перенос из
 * `EstimateUploadPanel`).
 *
 * `react-refresh/only-export-components` не позволяет файлу с экспортом
 * компонента экспортировать что-то ещё — тот же приём, что у
 * `pages/passport/rateLabels.ts` и `pages/compare/deviationTone.ts`:
 * `ImportJobPanel.tsx` реэкспортирует эти имена (`export { ... } from
 * "./jobStatus"`), поэтому вызывающие по-прежнему импортируют их из
 * `ImportJobPanel`, как называет брифинг задачи 11, а горячая перезагрузка
 * компонента при этом не ломается.
 */

export const XLSX_ACCEPT = {
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
  "application/vnd.ms-excel.sheet.macroEnabled.12": [".xlsm"],
};

export const STATUS_LABEL: Record<ImportJobStatus, string> = {
  pending: "В очереди",
  parsing: "Разбор файла",
  importing: "Запись сметы",
  matching: "Матчинг работ",
  done: "Готово",
  error: "Ошибка",
};

export function statusTone(status: ImportJobStatus): "success" | "danger" | "info" {
  if (status === "done") return "success";
  if (status === "error") return "danger";
  return "info";
}

/** Идёт ли работа — по этому же признаку останавливается поллинг. */
export function isRunning(status: ImportJobStatus): boolean {
  return status !== "done" && status !== "error";
}
