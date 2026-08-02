import type { ID, ISODateTime } from "./common";

export interface Project {
  id: ID;
  name: string;
  contract_number: string | null;
  created_at: ISODateTime;
}

export interface ProjectCreateInput {
  name: string;
  contract_number?: string | null;
}

export type ProjectUpdateInput = ProjectCreateInput;
