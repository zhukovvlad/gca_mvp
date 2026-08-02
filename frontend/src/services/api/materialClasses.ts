import api from "@/lib/api";
import type {
  MaterialClass,
  MaterialClassCreateInput,
} from "@/types/materialClass";
import type { ID } from "@/types/common";

export const materialClassesApi = {
  async list(): Promise<MaterialClass[]> {
    const { data } = await api.get<MaterialClass[]>("/material-classes");
    return data;
  },
  async create(input: MaterialClassCreateInput): Promise<MaterialClass> {
    const { data } = await api.post<MaterialClass>("/material-classes", input);
    return data;
  },
  async remove(id: ID): Promise<void> {
    await api.delete(`/material-classes/${id}`);
  },
};
