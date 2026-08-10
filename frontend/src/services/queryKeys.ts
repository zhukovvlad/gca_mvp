import type { ContractListParams } from "./api/domain";
import type { MatrixParams, RateStandardParams, ReviewQueueParams } from "@/types/domain";

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
    one: (id: number) => ["objects", "one", id] as const,
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

  // Аналитика фазы 6 (§6, §7.4–§7.5)
  settings: {
    all: ["settings"] as const,
  },

  passport: {
    all: ["passport"] as const,
    /**
     * Паспорт проекта по статьям классификатора (Ф6 фазы 7, задача 6).
     *
     * Корень НЕ заводится заново — `["passport"]` уже является префиксом ключа
     * `project`, поэтому инвалидация `qk.passport.all`, которая уже стоит в
     * `useUpdateObject` (пять корней, F5) и `useUpdateAppSettings`, накрывает
     * паспорт проекта ПО ПОСТРОЕНИЮ — без правки списка инвалидации. Так
     * обязательство 1, унаследованное из F5 («правка объекта обязана
     * перерисовать всё, где лежит его название»), закрывается для Ф6
     * бесплатно: свой отдельный корень (например `qk.projectPassport`)
     * выглядел бы так же удобно, но молча выпал бы из этой инвалидации, и ни
     * один существующий тест F5 этого не заметил бы — см. `queries.test.tsx`,
     * тест «корень паспорта переиспользован новым хуком».
     */
    project: (contractId: number) => ["passport", "project", contractId] as const,
  },

  matrix: {
    all: ["matrix"] as const,
    list: (params?: MatrixParams) => ["matrix", "list", params ?? {}] as const,
    cell: (contractId: number, catalogPositionId: number) =>
      ["matrix", "cell", contractId, catalogPositionId] as const,
  },
};
