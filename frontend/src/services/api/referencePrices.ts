import api from "@/lib/api";
import type {
  ReferencePrice,
  ReferencePriceCreateInput,
  ReferencePriceUpdateInput,
} from "@/types/referencePrice";
import type { ID } from "@/types/common";

export const referencePricesApi = {
  async list(projectId?: ID, materialClassId?: ID, direction?: string): Promise<ReferencePrice[]> {
    const params: Record<string, unknown> = {};
    if (projectId) params.project_id = projectId;
    if (materialClassId) params.material_class_id = materialClassId;
    if (direction) params.direction = direction;
    const { data } = await api.get<ReferencePrice[]>("/reference-prices", {
      params: Object.keys(params).length ? params : undefined,
    });
    return data;
  },
  async create(input: ReferencePriceCreateInput): Promise<ReferencePrice> {
    const { data } = await api.post<ReferencePrice>("/reference-prices", input);
    return data;
  },
  async update(id: ID, input: ReferencePriceUpdateInput): Promise<ReferencePrice> {
    const { data } = await api.patch<ReferencePrice>(`/reference-prices/${id}`, input);
    return data;
  },
  async remove(id: ID): Promise<void> {
    await api.delete(`/reference-prices/${id}`);
  },
};
