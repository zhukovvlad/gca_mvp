import type { ImportJobStatus } from "@/types/domain";

/**
 * Константы и чистые функции статуса задания импорта, вынесенные из
 * `ImportJobPanel.tsx` в СВОЙ модуль (задача 11, перенос из
 * `EstimateUploadPanel`).
 *
 * `react-refresh/only-export-components` не позволяет файлу с экспортом
 * компонента экспортировать что-то ещё — тот же приём, что у
 * `pages/passport/rateLabels.ts` и `pages/compare/deviationTone.ts`.
 * `ImportJobPanel.tsx` эти имена НЕ реэкспортирует (даже сквозным `export …
 * from` — линт запрещает и его): он только импортирует их для внутреннего
 * использования. Вызывающие (`EstimateUploadPanel`, `RoundUploadPanel`,
 * тесты) обязаны импортировать `statusTone`/`isRunning`/`STATUS_LABEL`/
 * `XLSX_ACCEPT` отсюда напрямую (`@/components/imports/jobStatus`), а не из
 * `ImportJobPanel` — брифинг задачи 11 называл местом экспорта именно
 * `ImportJobPanel.tsx`, но этим расходится с правилом линта проекта.
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
