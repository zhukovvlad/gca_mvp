import api from "@/lib/api";

export interface AppSettings {
  provider: "openrouter" | "gateway";
  can_edit_model: boolean;
  cost_available: boolean;
  api_key_set: boolean;
  model: string;
  confidence_threshold: number;
  // расширяется по мере добавления полей в backend
  [key: string]: unknown;
}

/** Частичный PUT: только редактируемые поля — response-only capabilities сюда не входят. */
export interface SettingsUpdate {
  api_key?: string;
  model?: string;
  confidence_threshold?: number;
}

export const settingsApi = {
  async get(): Promise<AppSettings> {
    const { data } = await api.get<AppSettings>("/settings");
    return data;
  },
  async update(input: SettingsUpdate): Promise<{ message: string }> {
    const { data } = await api.put<{ message: string }>("/settings", input);
    return data;
  },
};
