import type { ID, ISODate } from "./common";

export interface ReferencePrice {
  id: ID;
  project_id: ID;
  project_name?: string;
  material_class_id: ID;
  material_class_name?: string;
  unit_id: ID;
  unit_symbol?: string | null;
  price: number;
  period_start: ISODate;
  period_end: ISODate;
  source: string | null;
}

export interface ReferencePriceCreateInput {
  project_id: ID;
  material_class_id: ID;
  unit_id: ID;
  price: number;
  period_start: ISODate;
  period_end: ISODate;
  source?: string | null;
}

export interface ReferencePriceUpdateInput {
  price?: number;
  period_start?: ISODate;
  period_end?: ISODate;
  source?: string | null;
}
