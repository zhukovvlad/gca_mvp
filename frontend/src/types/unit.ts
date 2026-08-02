import type { ID } from "@/types/common";

export type UnitDimension = "mass" | "volume" | "length" | "count";

export interface Unit {
  id: ID;
  code: string;       // TON, KG, M3, L, M, PCS
  name: string;
  symbol: string;     // т, кг, м³, …
  dimension: UnitDimension;
  base_unit_id: ID | null;  // null → base unit of its dimension
}

export interface MaterialType {
  id: ID;
  code: string;       // concrete, rebar, other
  name: string;
  default_unit: { id: ID; code: string; symbol: string } | null;
}
