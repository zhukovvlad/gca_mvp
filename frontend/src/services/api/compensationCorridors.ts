import api from "@/lib/api";
import type { ID } from "@/types/common";
import type { CorridorMatrix, CorridorUpsertPayload } from "@/types/compensationCorridor";

export const corridorsApi = {
  async getMatrix(projectId: ID): Promise<CorridorMatrix> {
    const { data } = await api.get<CorridorMatrix>(`/projects/${projectId}/corridors`);
    return data;
  },

  async setType(projectId: ID, materialType: string, payload: CorridorUpsertPayload): Promise<void> {
    await api.put(`/projects/${projectId}/corridors/type/${materialType}`, payload);
  },

  async deleteType(projectId: ID, materialType: string): Promise<void> {
    await api.delete(`/projects/${projectId}/corridors/type/${materialType}`);
  },

  async setClass(projectId: ID, materialClassId: ID, payload: CorridorUpsertPayload): Promise<void> {
    await api.put(`/projects/${projectId}/corridors/class/${materialClassId}`, payload);
  },

  async deleteClass(projectId: ID, materialClassId: ID): Promise<void> {
    await api.delete(`/projects/${projectId}/corridors/class/${materialClassId}`);
  },
};
