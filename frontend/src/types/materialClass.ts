import type { ID, ISODateTime } from "./common";

export interface MaterialClass {
  id: ID;
  material_type: string; // "concrete" | "rebar" | "other"
  name: string;          // например "В40"
  created_at: ISODateTime;
}

export interface MaterialClassCreateInput {
  material_type: string;
  name: string;
}
