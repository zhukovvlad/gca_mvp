import { useState } from "react";

import { EntitySelect } from "@/components/ui-domain/EntitySelect";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { apiErrorCode, useMergeValues } from "@/services/queries";
import type { FamilySchema, FamilySchemaValue } from "@/types/domain";

import { schemaRefusalLabel } from "./labels";

/** Значения, которые ещё не слиты: источником и целью слияния бывают только они. */
function liveValues(values: FamilySchemaValue[]): FamilySchemaValue[] {
  return values.filter((value) => value.merged_into_id === null);
}

interface MergeValuesDialogProps {
  schema: FamilySchema;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * «Слить значения…»: источник и цель — значения ОДНОГО параметра (спека
 * `2026-10-02-catalog-variants-design.md` §2.8). Варианты источника переезжают к цели,
 * само значение остаётся синонимом цели.
 */
export function MergeValuesDialog({ schema, open, onOpenChange }: MergeValuesDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <MergeValuesForm schema={schema} onDone={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

function MergeValuesForm({ schema, onDone }: { schema: FamilySchema; onDone: () => void }) {
  const mergeable = schema.parameters.filter((parameter) => liveValues(parameter.values).length >= 2);
  const [parameterId, setParameterId] = useState<number | null>(null);
  const [sourceId, setSourceId] = useState<number | null>(null);
  const [targetId, setTargetId] = useState<number | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const merge = useMergeValues();

  const parameter = mergeable.find((candidate) => candidate.id === parameterId) ?? null;
  const values = parameter ? liveValues(parameter.values) : [];

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (parameterId === null || sourceId === null || targetId === null) return;
    setRefusal(null);
    merge.mutate(
      {
        familyId: schema.family_id,
        input: { parameter_id: parameterId, source_value_id: sourceId, target_value_id: targetId },
      },
      {
        onSuccess: onDone,
        onError: (error) => setRefusal(schemaRefusalLabel(apiErrorCode(error))),
      }
    );
  }

  return (
    <form onSubmit={handleSubmit}>
      <DialogHeader>
        <DialogTitle>Слить значения</DialogTitle>
        <DialogDescription>
          Варианты с исходным значением перейдут к целевому; исходное остаётся его синонимом.
        </DialogDescription>
      </DialogHeader>

      <div className="grid gap-3 py-4">
        <div className="grid gap-2">
          <Label htmlFor="merge-values-parameter">Параметр</Label>
          <EntitySelect
            id="merge-values-parameter"
            items={mergeable}
            value={parameterId}
            onChange={(v) => {
              setParameterId(v as number | null);
            }}
            getLabel={(p) => p.name}
            placeholder="Выбрать параметр"
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="merge-values-source">Источник</Label>
          <EntitySelect
            id="merge-values-source"
            items={values}
            value={sourceId}
            onChange={(v) => {
              setSourceId(v as number | null);
              if (v === targetId) setTargetId(null);
            }}
            getLabel={(v) => v.value}
            placeholder="Выбрать значение"
            disabled={parameter === null}
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="merge-values-target">Цель</Label>
          <EntitySelect
            id="merge-values-target"
            items={values.filter((v) => v.id !== sourceId)}
            value={targetId}
            onChange={(v) => setTargetId(v as number | null)}
            getLabel={(v) => v.value}
            placeholder="Выбрать значение"
            disabled={parameter === null}
          />
        </div>
        {refusal && (
          <p role="alert" className="text-sm text-danger-text">
            {refusal}
          </p>
        )}
      </div>

      <DialogFooter>
        <Button
          type="submit"
          disabled={sourceId === null || targetId === null || merge.isPending}
        >
          Слить
        </Button>
      </DialogFooter>
    </form>
  );
}
