/**
 * Транспорт аналитики фазы 6 (AGENTS.md §6, §7.4–§7.6).
 *
 * `baseURL` клиента — `"/api"`, поэтому пути начинаются с `/v1` и `/api` НЕ
 * дублируется (те же грабли, что у `domain.ts`).
 *
 * Все денежные поля приходят **строками** (§3) и типизированы как `Decimal`
 * (= `string`). Приводить их к `number` нельзя нигде — форматирование через
 * `MoneyCell`/`formatDecimalMoney`, отклонения через `DeviationCell`.
 */
import api from "@/lib/api";
import type {
  AppSettings,
  Matrix,
  MatrixCellDetail,
  MatrixParams,
  Passport,
} from "@/types/domain";

export const settingsApi = {
  get: (): Promise<AppSettings> => api.get<AppSettings>("/v1/settings").then((r) => r.data),

  update: (passport_top_n: number): Promise<AppSettings> =>
    api.patch<AppSettings>("/v1/settings", { passport_top_n }).then((r) => r.data),
};

export const analyticsApi = {
  passport: (contractId: number): Promise<Passport> =>
    api.get<Passport>(`/v1/analytics/passport/${contractId}`).then((r) => r.data),

  matrix: (params?: MatrixParams): Promise<Matrix> =>
    api.get<Matrix>("/v1/analytics/matrix", { params }).then((r) => r.data),

  matrixCell: (contract_id: number, catalog_position_id: number): Promise<MatrixCellDetail> =>
    api
      .get<MatrixCellDetail>("/v1/analytics/matrix/cell", {
        params: { contract_id, catalog_position_id },
      })
      .then((r) => r.data),
};
