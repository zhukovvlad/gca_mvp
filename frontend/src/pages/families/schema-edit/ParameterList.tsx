import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { FamilySchemaParameter, ParameterRole } from "@/types/domain";

import {
  MAX_SCHEMA_PARAMETERS,
  PARAMETER_NEW_LABEL,
  PARAMETER_ORIGIN_EDITED_LABEL,
  PARAMETER_ORIGIN_LABEL,
  PARAMETER_ROLE_GROUP_LABEL,
  pluralRu,
} from "../labels";
import { type EditorRow, rangeInnerCount, rowsTotal, sameRange } from "./drafts";

const ROLES: ParameterRole[] = ["main", "secondary"];

/** Пометки строки списка: вид, число значений и строк, «только из списка». */
function metaOf(row: EditorRow, saved: FamilySchemaParameter | undefined): string {
  const { draft } = row;
  const parts: string[] = [];
  if (draft.value_kind === "range") {
    parts.push("диапазоны");
    let count: number | null = null;
    if (saved !== undefined && saved.value_kind === "range" && sameRange(saved.range, draft.range)) {
      count = saved.values.filter((value) => value.merged_into_id === null).length;
    } else if (draft.range !== null) {
      const inner = rangeInnerCount(draft.range);
      count = inner === null ? null : inner + 2;
    }
    if (count !== null) parts.push(`${count} ${pluralRu(count, "значение", "значения", "значений")}`);
  } else {
    const count = draft.values.length;
    parts.push(`${count} ${pluralRu(count, "значение", "значения", "значений")}`);
  }
  if (saved !== undefined && saved.value_kind === draft.value_kind) {
    const rows = rowsTotal(saved);
    if (rows > 0) parts.push(`${rows.toLocaleString("ru-RU")} ${pluralRu(rows, "строка", "строки", "строк")}`);
  }
  if (draft.value_kind === "exact" && draft.closed) parts.push("только из списка");
  return parts.join(" · ");
}

interface ParameterListProps {
  rows: EditorRow[];
  /** Сохранённый параметр по номеру: числа строк и происхождение берутся из него. */
  savedOf: (row: EditorRow) => FamilySchemaParameter | undefined;
  /** Параметр модели, которого касается несохранённая правка. */
  isEdited: (row: EditorRow) => boolean;
  selectedKey: string | null;
  onSelect: (key: string) => void;
  onAdd: () => void;
}

/**
 * Левая колонка окна правки (спека 3в §2.11): параметры двумя группами — главные и
 * второстепенные, строка компактная: имя и пометки. Под списком «Добавить параметр» со
 * счётчиком «N из 10»; на пределе кнопка недоступна.
 */
export function ParameterList({ rows, savedOf, isEdited, selectedKey, onSelect, onAdd }: ParameterListProps) {
  const full = rows.length >= MAX_SCHEMA_PARAMETERS;
  return (
    <nav
      aria-label="Параметры"
      className="grid min-h-0 content-start gap-1 overflow-y-auto border-b border-border-subtle bg-surface-sunken p-3 sm:border-r sm:border-b-0"
    >
      {ROLES.map((role) => {
        const group = rows.filter((row) => row.draft.role === role);
        return (
          <section key={role} aria-label={PARAMETER_ROLE_GROUP_LABEL[role]} className="grid gap-1">
            <h3 className="px-2.5 pt-2 text-[11px] font-normal tracking-wide text-fg-tertiary uppercase">
              {PARAMETER_ROLE_GROUP_LABEL[role]}
            </h3>
            {group.length === 0 && <p className="px-2.5 text-xs text-fg-tertiary">Нет.</p>}
            {group.map((row) => {
              const saved = savedOf(row);
              const isNew = row.draft.ordinal === null;
              const tag = isNew
                ? PARAMETER_NEW_LABEL
                : saved?.origin === "human"
                  ? PARAMETER_ORIGIN_LABEL.human
                  : isEdited(row)
                    ? PARAMETER_ORIGIN_EDITED_LABEL
                    : PARAMETER_ORIGIN_LABEL.model;
              const selected = row.key === selectedKey;
              return (
                <button
                  key={row.key}
                  type="button"
                  aria-current={selected ? "true" : undefined}
                  onClick={() => onSelect(row.key)}
                  className={cn(
                    "grid gap-0.5 rounded-lg border border-transparent px-2.5 py-1.5 text-left text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                    selected ? "border-border-default bg-surface" : "hover:bg-surface-hover"
                  )}
                >
                  <span className="font-medium break-words">{row.draft.name.trim() || "Без имени"}</span>
                  <span className="text-xs text-fg-tertiary">{metaOf(row, saved)}</span>
                  <span>
                    <Badge variant="outline" className="h-4 px-1.5 text-[10.5px]">
                      {tag}
                    </Badge>
                  </span>
                </button>
              );
            })}
          </section>
        );
      })}
      <div className="grid gap-1 pt-2">
        <div>
          <Button type="button" variant="outline" size="sm" disabled={full} onClick={onAdd}>
            Добавить параметр
          </Button>
        </div>
        <p className="px-0.5 text-xs text-fg-tertiary">
          {rows.length} из {MAX_SCHEMA_PARAMETERS} параметров{full ? " — предел" : ""}
        </p>
      </div>
    </nav>
  );
}
