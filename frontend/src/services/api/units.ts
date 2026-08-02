import api from "@/lib/api";
import type { MaterialType, Unit } from "@/types/unit";

export const unitsApi = {
  async list(): Promise<Unit[]> {
    const { data } = await api.get<Unit[]>("/units");
    return data;
  },
};

export const materialTypesApi = {
  async list(): Promise<MaterialType[]> {
    const { data } = await api.get<MaterialType[]>("/material-types");
    return data;
  },
};
