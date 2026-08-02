export interface CorridorTypeRule {
  material_type: string;
  is_compensable: boolean | null; // null = not configured
  corridor_pct: number | null;
  has_rule: boolean;
}

export interface CorridorClassResolved {
  material_class_id: number;
  material_class_name: string;
  material_type: string;
  is_compensable: boolean;
  corridor_pct: number | null;
  level: "type" | "class" | "default";
  has_override: boolean;
}

export interface CorridorMatrix {
  types: CorridorTypeRule[];
  classes: CorridorClassResolved[];
}

export interface CorridorUpsertPayload {
  is_compensable: boolean;
  corridor_pct?: number | null;
}
