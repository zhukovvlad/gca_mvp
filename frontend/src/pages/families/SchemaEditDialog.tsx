import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { apiErrorCode, useUpdateSchema } from "@/services/queries";
import type { FamilySchema, FamilySchemaParameter, SchemaEditDraft } from "@/types/domain";

import { SCHEMA_EDIT_PREVIEW_CHANGED_NOTICE, schemaRefusalLabel } from "./labels";
import { EditSummaryPanel } from "./schema-edit/EditSummaryPanel";
import { ParameterEditor } from "./schema-edit/ParameterEditor";
import { ParameterList } from "./schema-edit/ParameterList";
import { type EditorRow, draftOf, rowsOf, wireDrafts } from "./schema-edit/drafts";
import { useSchemaEditPreview } from "./schema-edit/useSchemaEditPreview";

interface SchemaEditDialogProps {
  schema: FamilySchema;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * «Править схему…» — окно «Список и редактор» (спека 3в §2.5, §2.11, макет
 * `2026-10-19-catalog-parameters/schema-dialog.html`): слева параметры по ролям, справа
 * редактор выбранного, внизу сводка предпросмотра и «Сохранить схему». Окно не выше 90 % высоты
 * экрана: шапка и низ закреплены, список и редактор прокручиваются каждый сам.
 */
export function SchemaEditDialog({ schema, open, onOpenChange }: SchemaEditDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* Класс потребителя `sm:max-w-4xl` и базовый `sm:max-w-sm` — один ключ `tailwind-merge`: побеждает потребитель. */}
      <DialogContent className="max-h-[90vh] grid-rows-[minmax(0,1fr)] gap-0 overflow-hidden p-0 sm:max-w-4xl">
        <SchemaEditForm schema={schema} onDone={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

function savedOf(schema: FamilySchema, draft: SchemaEditDraft): FamilySchemaParameter | undefined {
  return draft.ordinal === null ? undefined : schema.parameters.find((p) => p.ordinal === draft.ordinal);
}

/** Строки без значений, которые в схеме стали синонимами; та же ссылка, если убирать нечего. */
function dropMergedValues(rows: EditorRow[], schema: FamilySchema): EditorRow[] {
  let changed = false;
  const next = rows.map((row) => {
    const saved = savedOf(schema, row.draft);
    if (saved === undefined || saved.value_kind !== "exact" || row.draft.value_kind !== "exact") return row;
    const merged = new Set(saved.values.filter((v) => v.merged_into_id !== null).map((v) => v.value));
    const values = row.draft.values.filter((text) => !merged.has(text));
    if (values.length === row.draft.values.length) return row;
    changed = true;
    return { ...row, draft: { ...row.draft, values } };
  });
  return changed ? next : rows;
}

function SchemaEditForm({ schema, onDone }: { schema: FamilySchema; onDone: () => void }) {
  const [rows, setRows] = useState<EditorRow[]>(() => rowsOf(schema));
  const [baseline, setBaseline] = useState(() => JSON.stringify(rows.map((row) => row.draft)));
  const [selectedKey, setSelectedKey] = useState<string | null>(
    () => (rows.find((row) => row.draft.role === "main") ?? rows[0])?.key ?? null
  );
  const [refusal, setRefusal] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const newKeys = useRef(0);
  const update = useUpdateSchema();

  const drafts = rows.map((row) => row.draft);
  const preview = useSchemaEditPreview(schema.family_id, drafts, `${schema.revision}:${refresh}`);

  const dirty = JSON.stringify(drafts) !== baseline;

  // Схема на сервере меняется из окна (слияние) и извне (другой администратор). Исходное
  // состояние пересобирается из новой схемы; без несохранённых правок черновики берутся из неё
  // (сохранять нечего), с правками они остаются, а значение, ставшее синонимом, из них уходит.
  // Подстройка по смене `schema` идёт во время рендера, не эффектом.
  const [syncedSchema, setSyncedSchema] = useState(schema);
  if (syncedSchema !== schema) {
    setSyncedSchema(schema);
    const fresh = rowsOf(schema);
    setBaseline(JSON.stringify(fresh.map((row) => row.draft)));
    if (!dirty) {
      // Своих правок нет: черновики берутся из перечитанной схемы целиком, вместе с чужой правкой,
      // иначе сохранение откатило бы её к показанному при открытии.
      setRows(fresh);
    } else {
      const withoutMerged = dropMergedValues(rows, schema);
      if (withoutMerged !== rows) setRows(withoutMerged);
    }
  }

  const selected = rows.find((row) => row.key === selectedKey) ?? rows[0] ?? null;
  // Сводка у хука бывает только для нынешнего тела и только без отказа проверки.
  const canSave = dirty && preview.summary !== null;

  function patchRow(key: string, patch: Partial<SchemaEditDraft>) {
    setRows((current) => current.map((row) => (row.key === key ? { ...row, draft: { ...row.draft, ...patch } } : row)));
  }

  /** Убранный сохранённый параметр просто не уходит в тело — сервер архивирует его; новый — исчезает. */
  function removeRow(key: string) {
    const index = rows.findIndex((row) => row.key === key);
    const rest = rows.filter((row) => row.key !== key);
    setRows(rest);
    setSelectedKey((rest[Math.min(index, rest.length - 1)] ?? null)?.key ?? null);
  }

  function addParameter() {
    newKeys.current += 1;
    const key = `n${newKeys.current}`;
    const draft: SchemaEditDraft = {
      ordinal: null,
      name: "",
      role: "main",
      value_kind: "exact",
      closed: false,
      values: [],
      range: null,
    };
    setRows((current) => [...current, { key, draft }]);
    setSelectedKey(key);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    // Только сужение типа: отправка идёт лишь доступной кнопкой «Сохранить»,
    // а она доступна только при готовой сводке.
    if (preview.summary === null) return;
    setRefusal(null);
    setNotice(null);
    update.mutate(
      { familyId: schema.family_id, parameters: wireDrafts(drafts), previewHash: preview.summary.preview_hash },
      {
        onSuccess: onDone,
        onError: (error) => {
          const code = apiErrorCode(error);
          if (code === "preview_changed") {
            // Схема ушла от показанной сводки: сводка пересчитывается, правка в окне остаётся.
            setNotice(SCHEMA_EDIT_PREVIEW_CHANGED_NOTICE);
            setRefresh((n) => n + 1);
            return;
          }
          setRefusal(schemaRefusalLabel(code));
        },
      }
    );
  }

  const selectedSaved = selected === null ? undefined : savedOf(schema, selected.draft);
  const edited = (row: EditorRow) => {
    const saved = savedOf(schema, row.draft);
    return saved !== undefined && JSON.stringify(draftOf(saved)) !== JSON.stringify(row.draft);
  };
  const moves =
    selected === null
      ? []
      : (preview.summary?.value_moves ?? []).filter(
          (move) => move.parameter === selected.draft.name || move.parameter === selectedSaved?.name
        );

  return (
    <form onSubmit={handleSubmit} className="grid min-h-0 grid-rows-[auto_minmax(0,1fr)_auto]">
      <DialogHeader className="gap-1 border-b border-border-subtle px-5 pt-4 pb-3">
        <DialogTitle>Править схему</DialogTitle>
        <DialogDescription>
          Параметры семьи, их роли и значения. Правка пишется в действующую версию схемы; удалить
          значение нельзя — его сливают с другим.
          {preview.summary !== null && !preview.pending && ` Вариантов сейчас: ${preview.summary.variants_before}.`}
        </DialogDescription>
      </DialogHeader>

      <div className="grid min-h-0 overflow-y-auto sm:grid-cols-[16rem_minmax(0,1fr)] sm:grid-rows-[minmax(0,1fr)] sm:overflow-hidden">
        <ParameterList
          rows={rows}
          savedOf={(row) => savedOf(schema, row.draft)}
          isEdited={edited}
          selectedKey={selected?.key ?? null}
          onSelect={setSelectedKey}
          onAdd={addParameter}
        />
        {selected === null ? (
          <p className="p-4 text-sm text-fg-tertiary">
            Параметров нет. Схема без параметров не делит семью на варианты; добавьте параметр слева.
          </p>
        ) : (
          <ParameterEditor
            key={selected.key}
            schema={schema}
            draft={selected.draft}
            saved={selectedSaved}
            edited={edited(selected)}
            moves={moves}
            onChange={(patch) => patchRow(selected.key, patch)}
            onRemove={() => removeRow(selected.key)}
          />
        )}
      </div>

      <div className="grid gap-2 rounded-b-xl border-t border-border-subtle bg-surface-sunken px-5 pt-3 pb-4">
        <EditSummaryPanel summary={preview.summary} pending={preview.pending} refusal={preview.refusal} />
        {notice && (
          <p role="status" className="rounded-md border border-warning-border bg-warning-soft px-3 py-2 text-sm text-warning-text">
            {notice}
          </p>
        )}
        {refusal && (
          <p role="alert" className="text-sm text-danger-text">
            {refusal}
          </p>
        )}
        <DialogFooter className="m-0 border-0 bg-transparent p-0">
          <Button type="button" variant="ghost" onClick={onDone}>
            Отмена
          </Button>
          <Button type="submit" disabled={!canSave} loading={update.isPending}>
            Сохранить схему
          </Button>
        </DialogFooter>
      </div>
    </form>
  );
}
