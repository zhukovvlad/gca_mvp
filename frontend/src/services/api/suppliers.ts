import api from "@/lib/api";
import type { SupplierDetail, SupplierInvoiceRow, SupplierListItem, SupplierProjectRow } from "@/types/supplier";
import type { ID } from "@/types/common";

export interface SupplierUpdateInput {
  name: string;
  inn?: string | null;
}

export const suppliersApi = {
  async list(): Promise<SupplierListItem[]> {
    const { data } = await api.get<SupplierListItem[]>("/suppliers");
    return data;
  },

  async get(id: ID): Promise<SupplierDetail> {
    const { data } = await api.get<SupplierDetail>(`/suppliers/${id}`);
    return data;
  },

  async update(id: ID, input: SupplierUpdateInput): Promise<{ id: ID; name: string; inn: string | null }> {
    const { data } = await api.put(`/suppliers/${id}`, input);
    return data;
  },

  async merge(targetId: ID, sourceId: ID): Promise<{ id: ID; name: string; inn: string | null }> {
    const { data } = await api.post(`/suppliers/${targetId}/merge`, { source_id: sourceId });
    return data;
  },

  async getProjects(id: ID): Promise<SupplierProjectRow[]> {
    const { data } = await api.get<SupplierProjectRow[]>(`/suppliers/${id}/projects`);
    return data;
  },

  async getInvoices(id: ID, projectId?: ID): Promise<SupplierInvoiceRow[]> {
    const { data } = await api.get<SupplierInvoiceRow[]>(`/suppliers/${id}/invoices-list`, {
      params: projectId !== undefined ? { project_id: projectId } : undefined,
    });
    return data;
  },
};
