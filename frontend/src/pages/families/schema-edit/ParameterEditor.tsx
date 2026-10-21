import { useState } from "react";
import { MoreHorizontal, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type {
  FamilySchema,
  FamilySchemaParameter,
  ParameterRole,
  ParameterValueKind,
  SchemaEditDraft,
  SchemaEditValueMove,
} from "@/types/domain";

import { MergeValuesDialog } from "../MergeValuesDialog";
import {
  PARAMETER_KIND_LABEL,
  PARAMETER_NEW_LABEL,
  PARAMETER_ORIGIN_EDITED_LABEL,
  PARAMETER_ORIGIN_LABEL,
  PARAMETER_ROLE_HINT,
  PARAMETER_ROLE_LABEL,
} from "../labels";
import { OffListBlock } from "./OffListBlock";
import { RangeFields } from "./RangeFields";
import { ValueChip } from "./ValueChip";
import { liveValueTexts, normText } from "./drafts";

const ROLES: ParameterRole[] = ["main", "secondary"];
const KINDS: ParameterValueKind[] = ["exact", "range"];

interface ParameterEditorProps {
  schema: FamilySchema;
  /** Параметр в теле правки. */
  draft: SchemaEditDraft;
  /** Сохранённый параметр с тем же номером; у нового его нет. */
  saved: FamilySchemaParameter | undefined;
  /** Параметр модели, которого касается несохранённая правка: пометка «правлен вами». */
  edited: boolean;
  /** Переносы прежних значений из сводки предпросмотра. */
  moves: SchemaEditValueMove[];
  onChange: (patch: Partial<SchemaEditDraft>) => void;
  onRemove: () => void;
}

/**
 * Правая колонка окна правки (спека 3в §2.11): имя, роль, вид значений, значения — плашки с числом
 * строк, «только из списка» и значения не из списка у точных, границы и шаг у диапазонов — и
 * «Убрать параметр». Убрать можно любой параметр: сохранённый архивируется, а не удаляется.
 */
export function ParameterEditor({ schema, draft, saved, edited, moves, onChange, onRemove }: ParameterEditorProps) {
  const [merge, setMerge] = useState<{ parameterId: number; sourceId: number } | null>(null);
  const [newValue, setNewValue] = useState("");

  const savedLive = saved !== undefined && saved.value_kind === "exact" ? saved.values.filter((v) => v.merged_into_id === null) : [];
  const canMerge = savedLive.length >= 2;
  const hasExtension = draft.value_kind === "exact" && savedLive.some((v) => v.origin === "extension" && draft.values.includes(v.value));

  function changeKind(kind: ParameterValueKind) {
    if (kind === draft.value_kind) return;
    if (kind === "range") {
      onChange({
        value_kind: "range",
        closed: true,
        range: saved?.range ?? { start: "", stop: "", step: "" },
      });
      return;
    }
    // Возврат в точные: сохранённые точные значения возвращаются как были, у диапазонов их нет.
    onChange({
      value_kind: "exact",
      closed: saved !== undefined && saved.value_kind === "exact" ? saved.closed : false,
      values: saved !== undefined && saved.value_kind === "exact" ? liveValueTexts(saved) : [],
      range: null,
    });
  }

  function addValue(text: string) {
    const trimmed = text.trim();
    if (trimmed === "") return;
    if (draft.values.some((existing) => normText(existing) === normText(trimmed))) return;
    onChange({ values: [...draft.values, trimmed] });
  }

  const origin =
    draft.ordinal === null
      ? PARAMETER_NEW_LABEL
      : saved?.origin === "human"
        ? PARAMETER_ORIGIN_LABEL.human
        : edited
          ? PARAMETER_ORIGIN_EDITED_LABEL
          : PARAMETER_ORIGIN_LABEL.model;

  return (
    <div className="grid min-h-0 min-w-0 content-start gap-4 overflow-y-auto p-4" aria-label="Редактор параметра" role="group">
      <div className="grid gap-1.5">
        <Label htmlFor="schema-edit-name">Имя параметра</Label>
        <Input id="schema-edit-name" value={draft.name} onChange={(e) => onChange({ name: e.target.value })} />
      </div>

      <div className="grid gap-1.5">
        <span className="text-sm font-medium">Роль</span>
        <ToggleGroup
          variant="outline"
          size="sm"
          spacing={0}
          aria-label="Роль"
          value={[draft.role]}
          onValueChange={(value) => {
            const next = value[0];
            if (next !== undefined) onChange({ role: next as ParameterRole });
          }}
        >
          {ROLES.map((role) => (
            <ToggleGroupItem key={role} value={role}>
              {PARAMETER_ROLE_LABEL[role]}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <p className="text-xs text-fg-tertiary">{PARAMETER_ROLE_HINT}</p>
      </div>

      <div className="grid gap-2">
        <span className="text-sm font-medium">Значения</span>
        <ToggleGroup
          variant="outline"
          size="sm"
          spacing={0}
          aria-label="Вид значений"
          value={[draft.value_kind]}
          onValueChange={(value) => {
            const next = value[0];
            if (next !== undefined) changeKind(next as ParameterValueKind);
          }}
        >
          {KINDS.map((kind) => (
            <ToggleGroupItem key={kind} value={kind}>
              {PARAMETER_KIND_LABEL[kind]}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>

        {draft.value_kind === "exact" ? (
          <>
            <div className="flex flex-wrap items-center gap-1.5">
              {draft.values.map((text) => {
                const value = savedLive.find((candidate) => candidate.value === text);
                return (
                  <ValueChip key={text} text={text} rows={value === undefined ? null : value.rows} extension={value?.origin === "extension"}>
                    {value !== undefined && canMerge && saved !== undefined ? (
                      <DropdownMenu>
                        <DropdownMenuTrigger
                          type="button"
                          aria-label={`Действия со значением «${text}»`}
                          className={buttonVariants({ variant: "ghost", size: "icon-xs" })}
                        >
                          <MoreHorizontal />
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="start">
                          <DropdownMenuItem onClick={() => setMerge({ parameterId: saved.id, sourceId: value.id })}>
                            Слить с другим значением…
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    ) : value === undefined ? (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-xs"
                        aria-label={`Убрать значение «${text}»`}
                        onClick={() => onChange({ values: draft.values.filter((existing) => existing !== text) })}
                      >
                        <X />
                      </Button>
                    ) : null}
                  </ValueChip>
                );
              })}
              <Input
                aria-label="Новое значение"
                placeholder="+ добавить значение"
                className="h-6.5 w-40 text-xs"
                value={newValue}
                onChange={(e) => setNewValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key !== "Enter") return;
                  e.preventDefault();
                  addValue(newValue);
                  setNewValue("");
                }}
              />
            </div>
            <p className="text-xs text-fg-tertiary">
              Число строк — на плашке, пунктир — значение без строк. Удалить сохранённое значение нельзя, только
              слить (⋯).
            </p>
            {hasExtension && (
              <p className="text-xs text-fg-tertiary">
                Оранжевая плашка — значение, дописанное моделью; его можно слить с существующим.
              </p>
            )}
            <div className="flex items-start gap-2 text-sm">
              <Checkbox
                id="schema-edit-closed"
                checked={draft.closed}
                onCheckedChange={(checked) => onChange({ closed: checked === true })}
              />
              <Label htmlFor="schema-edit-closed" className="font-normal">
                Только из списка — модель не заводит новых значений, список меняет только человек
              </Label>
            </div>
            {saved !== undefined && saved.value_kind === "exact" && (
              <OffListBlock
                familyId={schema.family_id}
                parameter={saved}
                draftValues={draft.values}
                onAddToList={(text) => addValue(text)}
              />
            )}
          </>
        ) : (
          <RangeFields
            range={draft.range ?? { start: "", stop: "", step: "" }}
            onChange={(range) => onChange({ range })}
            saved={saved}
            moves={moves}
          />
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <Badge variant="outline">{origin}</Badge>
        <Button type="button" variant="ghost" size="sm" className="text-danger-text" onClick={onRemove}>
          Убрать параметр
        </Button>
      </div>

      <MergeValuesDialog
        schema={schema}
        open={merge !== null}
        onOpenChange={(open) => {
          if (!open) setMerge(null);
        }}
        initial={merge ?? undefined}
      />
    </div>
  );
}
