import type { ContractListParams } from "./api/domain";
import type {
  ComparisonParams,
  MatrixParams,
  RateStandardParams,
  ReviewQueueParams,
} from "@/types/domain";

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

  /**
   * Стартовый дашборд (спека 2026-08-16). Два ключа под одним корнем: главная
   * и её админский таб диагностик — разные эндпоинты и разные права, но одна
   * поверхность, и инвалидируются они вместе.
   */
  dashboard: {
    all: ["dashboard"] as const,
    main: () => ["dashboard", "main"] as const,
    attention: () => ["dashboard", "attention"] as const,
  },

  matrix: {
    all: ["matrix"] as const,
    list: (params?: MatrixParams) => ["matrix", "list", params ?? {}] as const,
    cell: (contractId: number, catalogPositionId: number) =>
      ["matrix", "cell", contractId, catalogPositionId] as const,
  },

  /** Ряды индексов инфляции (спека 2026-08-18 §2.12). */
  inflationSeries: {
    all: ["inflation-series"] as const,
    list: (includeArchived = false) =>
      ["inflation-series", "list", includeArchived] as const,
    values: (id: number) => ["inflation-series", "values", id] as const,
  },

  /** Сравнение договоров по статьям классификатора (спека 2026-08-17, задача 8). */
  comparison: {
    all: ["comparison"] as const,
    get: (params?: ComparisonParams) => ["comparison", "get", params ?? {}] as const,
  },

  /** Тендерный контур (спека 2026-08-26-tenders-contour-design.md §2.13). */
  tenders: {
    all: ["tenders"] as const,
    list: (params?: { q?: string; page?: number; page_size?: number }) =>
      ["tenders", "list", params ?? {}] as const,
    card: (id: number) => ["tenders", "card", id] as const,
    roundJobs: (tenderId: number, roundId: number) =>
      ["tenders", "round-jobs", tenderId, roundId] as const,
    /** Свод по этапам одного участника (спека 2026-08-27-stage-summary-design.md §2.16). */
    stageSummary: (tenderId: number, offerIds: number[]) =>
      ["tenders", "stage-summary", tenderId, [...offerIds].sort((a, b) => a - b)] as const,
    /**
     * Разложение статьи свода (спека 2026-08-30-position-drilldown-design.md
     * §2.12). Ключ канонический: сортировка offerIds — иначе тот же выбор,
     * поданный в другом порядке, создал бы вторую запись кэша вместо
     * попадания в ту же.
     */
    stagePositions: (tenderId: number, workCategoryId: number, offerIds: number[]) =>
      ["tenders", "stage-positions", tenderId, workCategoryId,
       [...offerIds].sort((a, b) => a - b)] as const,
    /**
     * Префикс свода И разложения по этапам одного тендера — инвалидировать
     * ВСЕ комбинации `offerIds` (а у разложения ещё и `workCategoryId`) разом,
     * не зная заранее, какой выбор сейчас открыт на экране (ревью PR #35,
     * finding 2). Раунд после ЗАМЕНЫ переиспользует ТУ ЖЕ строку `Offer`
     * (`services/round_import.py`: `on_conflict_do_nothing` по
     * `(round_id, package_id)`), так что id предложений не меняются, и точечный
     * ключ (`stageSummary`/`stagePositions` с конкретными `offerIds`) навсегда
     * остался бы прежним — прогадать выбор невозможно, только инвалидировать
     * ВЕСЬ префикс тендера. `staleTime: Infinity` у `useStagePositions` (§6.3)
     * делает это ЕДИНСТВЕННЫМ способом освежить кэш разложения — без рефетча
     * по времени и без смены ключа сам хук никогда не перезапросит данные
     * заново.
     *
     * По умолчанию `invalidateQueries` матчит ПО ПРЕФИКСУ (`exact: false`), так
     * что `["tenders", "stage-summary", tenderId]` попадает во ВСЕ записи
     * `qk.tenders.stageSummary(tenderId, любые offerIds)`, а
     * `["tenders", "stage-positions", tenderId]` — во ВСЕ записи
     * `qk.tenders.stagePositions(tenderId, любой workCategoryId, любые
     * offerIds)`. Отдельные функции здесь — а не общий `qk.tenders.all` —
     * чтобы не задевать `tenders.list`/`tenders.card`/`tenders.roundJobs`
     * тем же вызовом: они инвалидируются отдельно, своей логикой.
     */
    stageSummaryForTender: (tenderId: number) => ["tenders", "stage-summary", tenderId] as const,
    stagePositionsForTender: (tenderId: number) => ["tenders", "stage-positions", tenderId] as const,
    /** Нераспределённое раунда (спека этапного разноса §2.3). */
    roundUnallocated: (tenderId: number, roundId: number) => ["tenders", "round-unallocated", tenderId, roundId] as const,
  },
};
