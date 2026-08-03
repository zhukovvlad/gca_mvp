import type { ContractListParams } from "./api/domain";
import type { RateStandardParams, ReviewQueueParams } from "@/types/domain";

/**
 * Ключи TanStack Query — централизованно, чтобы инвалидация не гадала.
 *
 * У каждой сущности есть корневой префикс (`*.all`): им инвалидируются сразу все
 * варианты фильтров после мутации. Точечный ключ со всеми параметрами нужен
 * кэшу, а инвалидация по префиксу — экрану: иначе после создания договора
 * обновлялся бы только тот список, чьи фильтры совпали с текущими.
 */
export const qk = {
  admin: {
    users: (q?: string, page?: number, pageSize?: number) =>
      ["admin", "users", q ?? "", page ?? 1, pageSize ?? 20] as const,
  },

  rateClasses: {
    all: ["rate-classes"] as const,
    list: () => ["rate-classes", "list"] as const,
  },

  objects: {
    all: ["objects"] as const,
    list: (params?: { q?: string; page?: number; page_size?: number }) =>
      ["objects", "list", params ?? {}] as const,
  },

  contractors: {
    all: ["contractors"] as const,
    list: (params?: { q?: string; page?: number; page_size?: number }) =>
      ["contractors", "list", params ?? {}] as const,
  },

  contracts: {
    all: ["contracts"] as const,
    list: (params?: ContractListParams) => ["contracts", "list", params ?? {}] as const,
    card: (id: number) => ["contracts", "card", id] as const,
    importJobs: (id: number) => ["contracts", "import-jobs", id] as const,
  },

  importJobs: {
    all: ["import-jobs"] as const,
    one: (id: number) => ["import-jobs", id] as const,
  },

  review: {
    all: ["review"] as const,
    queue: (params?: ReviewQueueParams) => ["review", "queue", params ?? {}] as const,
    targets: (q: string, unitId?: number) => ["review", "targets", q, unitId ?? null] as const,
  },

  catalog: {
    all: ["catalog"] as const,
    search: (q: string, unitId?: number) => ["catalog", "search", q, unitId ?? null] as const,
  },

  rateStandards: {
    all: ["rate-standards"] as const,
    list: (params?: RateStandardParams) => ["rate-standards", "list", params ?? {}] as const,
  },

  units: {
    all: ["units"] as const,
  },
};
