import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useCancelSchemaBuild, useFamilySchema } from "@/services/queries";
import type {
  FamilySchema,
  FamilySchemaParameter,
  ParameterRole,
  PreviewTarget,
  WorkFamily,
} from "@/types/domain";

import { MergeValuesDialog } from "./MergeValuesDialog";
import { ListSkeleton } from "./pending";
import { PreviewDialog } from "./PreviewDialog";
import { SchemaEditDialog } from "./SchemaEditDialog";
import {
  PARAMETER_KIND_LABEL,
  PARAMETER_ORIGIN_LABEL,
  PARAMETER_ROLE_GROUP_LABEL,
  SCHEMA_VALUE_ORIGIN_LABEL,
  valuesRecountLabel,
} from "./labels";
import { VariantsTable } from "./VariantsTable";

const MARK_TINT = "border-warning-border bg-warning-soft text-warning-text";
const ROLES: ParameterRole[] = ["main", "secondary"];

function ParameterValues({ parameter }: { parameter: FamilySchemaParameter }) {
  const byId = new Map(parameter.values.map((value) => [value.id, value]));
  return (
    <div className="grid gap-1">
      <h5 className="text-sm font-medium text-fg">{parameter.name}</h5>
      <p className="text-xs text-fg-tertiary">
        {PARAMETER_KIND_LABEL[parameter.value_kind].toLowerCase()}
        {" · "}
        {PARAMETER_ORIGIN_LABEL[parameter.origin]}
        {parameter.value_kind === "exact" && parameter.closed && " · только из списка"}
      </p>
      {parameter.values.length === 0 ? (
        <p className="text-xs text-fg-tertiary">Значений нет.</p>
      ) : (
        <ul className="grid gap-0.5 text-sm text-fg-secondary">
          {parameter.values.map((value) => {
            const target = value.merged_into_id === null ? undefined : byId.get(value.merged_into_id);
            return (
              <li key={value.id}>
                {value.value}
                <span className="text-xs text-fg-tertiary">
                  {" · "}
                  {SCHEMA_VALUE_ORIGIN_LABEL[value.origin]}
                  {value.merged_into_id !== null &&
                    ` · синоним${target ? ` «${target.value}»` : ""}`}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/** Можно ли слить значения: у точного параметра не меньше двух несинонимичных (диапазоны строит система). */
function hasMergeableValues(schema: FamilySchema): boolean {
  return schema.parameters.some(
    (parameter) =>
      parameter.value_kind === "exact" &&
      parameter.values.filter((value) => value.merged_into_id === null).length >= 2
  );
}

/**
 * Блок «Схема и варианты» карточки семьи (спека
 * `2026-10-02-catalog-variants-design.md` §2.12): текущая версия схемы с происхождением
 * значений, пометки состояния, действия над схемой и таблица вариантов.
 */
export function SchemaBlock({ family }: { family: WorkFamily }) {
  const schemaQ = useFamilySchema(family.id);
  const cancel = useCancelSchemaBuild();
  const [rebuilding, setRebuilding] = useState(false);
  const [editing, setEditing] = useState(false);
  const [merging, setMerging] = useState(false);

  const schema = schemaQ.data;
  const active = family.status === "active";
  // Стабильная ссылка: новая цель на каждый рендер перезапускала бы предпросмотр и сбрасывала подтверждение в пути.
  const rebuildTarget = useMemo<PreviewTarget | null>(
    () => (rebuilding ? { kind: "schema", familyId: family.id } : null),
    [rebuilding, family.id]
  );

  return (
    <section aria-labelledby={`family-schema-heading-${family.id}`} className="grid gap-3 border-t border-border-subtle pt-3">
      <h4 id={`family-schema-heading-${family.id}`} className="text-base font-medium text-fg">
        Схема и варианты
      </h4>

      {schemaQ.isPending && <ListSkeleton rows={2} />}
      {schemaQ.isError && <p className="text-sm text-fg-tertiary">Не удалось получить схему семьи.</p>}

      {schema && (
        <>
          <div className="flex flex-wrap items-center gap-2 text-sm text-fg-secondary">
            {schema.version === null ? (
              <span>Схемы пока нет.</span>
            ) : (
              <span className="tabular-nums">Версия схемы {schema.version}</span>
            )}
            {schema.building && (
              <Badge variant="outline" className={MARK_TINT}>
                схема строится
              </Badge>
            )}
            {schema.values_jobs_live > 0 && (
              <Badge variant="outline" className={MARK_TINT}>
                {valuesRecountLabel(schema.values_jobs_live)}
              </Badge>
            )}
            {!schema.ready_to_build && (
              <Badge variant="outline" className={MARK_TINT}>
                схема ждёт перезапроса единицы
              </Badge>
            )}
          </div>

          {schema.parameters.length > 0 && (
            <div className="grid gap-3">
              {ROLES.map((role) => {
                const group = schema.parameters.filter((parameter) => parameter.role === role);
                if (group.length === 0) return null;
                return (
                  <section key={role} aria-label={PARAMETER_ROLE_GROUP_LABEL[role]} className="grid gap-2">
                    <h5 className="text-xs font-normal tracking-wide text-fg-tertiary uppercase">
                      {PARAMETER_ROLE_GROUP_LABEL[role]}
                    </h5>
                    {group.map((parameter) => (
                      <ParameterValues key={parameter.id} parameter={parameter} />
                    ))}
                  </section>
                );
              })}
            </div>
          )}

          <div className="flex flex-wrap gap-2">
            <Button variant="outline" disabled={!active} onClick={() => setRebuilding(true)}>
              Пересобрать…
            </Button>
            {schema.building && (
              <Button variant="outline" loading={cancel.isPending} onClick={() => cancel.mutate(family.id)}>
                Отменить пересборку
              </Button>
            )}
            <Button
              variant="outline"
              disabled={!active || schema.version === null || schema.building}
              onClick={() => setEditing(true)}
            >
              Править схему…
            </Button>
            <Button variant="outline" disabled={!hasMergeableValues(schema)} onClick={() => setMerging(true)}>
              Слить значения…
            </Button>
          </div>
          {!active && (
            <p className="text-xs text-fg-tertiary">Схему можно менять только у активной семьи.</p>
          )}
          {active && schema.building && (
            <p className="text-xs text-fg-tertiary">
              Пока схема строится, её нельзя править: дождитесь конца или отмените пересборку.
            </p>
          )}

          <SchemaEditDialog schema={schema} open={editing} onOpenChange={setEditing} />
          <MergeValuesDialog schema={schema} open={merging} onOpenChange={setMerging} />
        </>
      )}

      <PreviewDialog
        target={rebuildTarget}
        onClose={() => setRebuilding(false)}
      />

      <VariantsTable familyId={family.id} />
    </section>
  );
}
