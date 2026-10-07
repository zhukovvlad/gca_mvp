import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { apiErrorCode, useUpdateSchema } from "@/services/queries";
import type { FamilySchema, SchemaEditParameter } from "@/types/domain";

import { schemaRefusalLabel } from "./labels";

/** Схема семьи — до трёх параметров (спека `2026-10-02-catalog-variants-design.md` §2.8). */
const MAX_PARAMETERS = 3;

interface EditRow {
  ordinal: number;
  name: string;
  /** Значения — по одному на строку. */
  values: string;
}

function rowsOf(schema: FamilySchema): EditRow[] {
  return schema.parameters.map((parameter) => ({
    ordinal: parameter.ordinal,
    name: parameter.name,
    // Слитые значения — синонимы своей цели и в список правки не входят.
    values: parameter.values
      .filter((value) => value.merged_into_id === null)
      .map((value) => value.value)
      .join("\n"),
  }));
}

function toParameters(rows: EditRow[]): SchemaEditParameter[] {
  return rows.map((row) => ({
    ordinal: row.ordinal,
    name: row.name,
    values: row.values
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line !== ""),
  }));
}

interface SchemaEditDialogProps {
  schema: FamilySchema;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * «Править схему…»: имена параметров и списки значений. Правка написания и добавление
 * значения принимаются; смысловое переименование и удаление значения сервер отказывает
 * (спека §2.8) — подпись по коду ответа, сам код на экран не выходит.
 */
export function SchemaEditDialog({ schema, open, onOpenChange }: SchemaEditDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <SchemaEditForm schema={schema} onDone={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

function SchemaEditForm({ schema, onDone }: { schema: FamilySchema; onDone: () => void }) {
  const [rows, setRows] = useState<EditRow[]>(() => rowsOf(schema));
  const [refusal, setRefusal] = useState<string | null>(null);
  const update = useUpdateSchema();

  function patchRow(ordinal: number, patch: Partial<EditRow>) {
    setRows((current) => current.map((row) => (row.ordinal === ordinal ? { ...row, ...patch } : row)));
  }

  /** Убранный параметр просто не уходит в тело; номера остальных не пересчитываются — номер и есть идентичность параметра. */
  function removeParameter(ordinal: number) {
    setRows((current) => current.filter((row) => row.ordinal !== ordinal));
  }

  /**
   * Новый параметр берёт наименьший номер из 1..3, свободный и в правке, и в ТЕКУЩЕЙ версии:
   * сервер сравнивает правку с версией по номеру, и занятый убранным параметром номер он
   * прочёл бы как смысловое переименование.
   */
  const takenOrdinals = new Set([
    ...rows.map((row) => row.ordinal),
    ...schema.parameters.map((parameter) => parameter.ordinal),
  ]);
  const freeOrdinal = [1, 2, 3].find((ordinal) => !takenOrdinals.has(ordinal));

  function addParameter() {
    if (freeOrdinal === undefined) return;
    setRows((current) => [...current, { ordinal: freeOrdinal, name: "", values: "" }]);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setRefusal(null);
    update.mutate(
      { familyId: schema.family_id, parameters: toParameters(rows) },
      {
        onSuccess: onDone,
        onError: (error) => setRefusal(schemaRefusalLabel(apiErrorCode(error))),
      }
    );
  }

  return (
    <form onSubmit={handleSubmit}>
      <DialogHeader>
        <DialogTitle>Править схему</DialogTitle>
        <DialogDescription>
          Имя параметра можно поправить по написанию, значения — добавить. Удалить значение
          нельзя: его сливают с другим.
        </DialogDescription>
      </DialogHeader>

      <div className="grid gap-4 py-4">
        {rows.map((row) => (
          <div key={row.ordinal} className="grid gap-2">
            <Label htmlFor={`schema-edit-name-${row.ordinal}`}>Параметр {row.ordinal}</Label>
            <Input
              id={`schema-edit-name-${row.ordinal}`}
              value={row.name}
              onChange={(e) => patchRow(row.ordinal, { name: e.target.value })}
            />
            <Label htmlFor={`schema-edit-values-${row.ordinal}`} className="text-xs text-fg-tertiary">
              Значения параметра {row.ordinal}, по одному в строке
            </Label>
            <Textarea
              id={`schema-edit-values-${row.ordinal}`}
              value={row.values}
              onChange={(e) => patchRow(row.ordinal, { values: e.target.value })}
            />
            <div>
              <Button type="button" variant="ghost" onClick={() => removeParameter(row.ordinal)}>
                Убрать параметр {row.ordinal}
              </Button>
            </div>
          </div>
        ))}
        {rows.length < MAX_PARAMETERS && (
          <div className="grid gap-1">
            <div>
              <Button type="button" variant="outline" disabled={freeOrdinal === undefined} onClick={addParameter}>
                Добавить параметр
              </Button>
            </div>
            {freeOrdinal === undefined && (
              <p className="text-xs text-fg-tertiary">
                Номер убранного параметра нельзя занять в этой же правке: сохраните удаление, затем добавьте
                параметр отдельной правкой.
              </p>
            )}
          </div>
        )}
        {refusal && (
          <p role="alert" className="text-sm text-danger-text">
            {refusal}
          </p>
        )}
      </div>

      <DialogFooter>
        <Button type="submit" disabled={update.isPending}>
          Сохранить схему
        </Button>
      </DialogFooter>
    </form>
  );
}
