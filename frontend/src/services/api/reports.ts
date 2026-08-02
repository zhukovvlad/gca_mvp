import api from "@/lib/api";
import type { ID, ISODate } from "@/types/common";

export interface ExcelExportInput {
  project_id: ID;
  period_start?: ISODate;
  period_end?: ISODate;
  direction?: string;
}

export const reportsApi = {
  async excelBlob(input: ExcelExportInput): Promise<Blob> {
    const params: Record<string, string | number> = { project_id: input.project_id };
    if (input.period_start) params.period_start = input.period_start;
    if (input.period_end) params.period_end = input.period_end;
    if (input.direction) params.direction = input.direction;
    const { data } = await api.get<Blob>("/export/excel", {
      params,
      responseType: "blob",
    });
    return data;
  },
};
