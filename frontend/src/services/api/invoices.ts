import api from "@/lib/api";
import type {
  DocumentDetail,
  DocumentSummary,
  InvoiceUpdateInput,
  InvoiceUpdateResult,
} from "@/types/invoice";
import type { ID } from "@/types/common";

export const invoicesApi = {
  async listDocuments(projectId?: ID): Promise<DocumentSummary[]> {
    const { data } = await api.get<DocumentSummary[]>("/invoices/documents", {
      params: projectId ? { project_id: projectId } : undefined,
    });
    return data;
  },
  async getDocument(docId: ID): Promise<DocumentDetail> {
    const { data } = await api.get<DocumentDetail>(`/invoices/documents/${docId}`);
    return data;
  },
  documentPdfUrl(docId: ID): string {
    return `/api/invoices/documents/${docId}/pdf`;
  },
  async reparseDocument(docId: ID): Promise<DocumentDetail> {
    const { data } = await api.post<DocumentDetail>(
      `/invoices/documents/${docId}/reparse`
    );
    return data;
  },
  async deskewReparseDocument(docId: ID): Promise<DocumentDetail> {
    const { data } = await api.post<DocumentDetail>(
      `/invoices/documents/${docId}/deskew-reparse`
    );
    return data;
  },
  async update(invoiceId: ID, input: InvoiceUpdateInput): Promise<InvoiceUpdateResult> {
    const { data } = await api.put<InvoiceUpdateResult>(`/invoices/${invoiceId}`, input);
    return data;
  },
  async removeInvoice(invoiceId: ID): Promise<void> {
    await api.delete(`/invoices/${invoiceId}`);
  },
  async bulkDeleteInvoices(ids: ID[]): Promise<{ deleted: number; skipped: ID[] }> {
    const { data } = await api.delete<{ deleted: number; skipped: ID[] }>("/invoices/bulk", { data: { ids } });
    return data;
  },
  async removeDocument(docId: ID): Promise<void> {
    await api.delete(`/invoices/documents/${docId}`);
  },
  async verifyInvoice(invoiceId: ID): Promise<void> {
    await api.post(`/invoices/${invoiceId}/verify`);
  },
  async unverifyInvoice(invoiceId: ID): Promise<void> {
    await api.post(`/invoices/${invoiceId}/unverify`);
  },
};
