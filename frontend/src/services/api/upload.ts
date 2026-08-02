import api from "@/lib/api";
import type { UploadResponse } from "@/types/invoice";
import type { ID } from "@/types/common";

export const uploadApi = {
  async uploadInvoice(
    projectId: ID,
    file: File,
    onProgress?: (pct: number) => void
  ): Promise<UploadResponse> {
    const form = new FormData();
    form.append("file", file);
    form.append("project_id", String(projectId));
    const { data } = await api.post<UploadResponse>("/invoices/upload", form, {
      headers: { "Content-Type": "multipart/form-data" },
      onUploadProgress: (e) => {
        if (onProgress && e.total) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      },
    });
    return data;
  },
};
