import { useState } from "react";
import { Surface } from "@/components/ui-domain/Surface";
import { Button } from "@/components/ui-domain/Button";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  useCorridors,
  useSetTypeCorridor,
  useDeleteTypeCorridor,
  useSetClassCorridor,
  useDeleteClassCorridor,
} from "@/services/queries";
import type { ID } from "@/types/common";
import type { CorridorClassResolved } from "@/types/compensationCorridor";

interface Props {
  projectId: ID;
  /** Скоуп направления (спека §3.3): фильтрация матрицы на фронте, API без изменений (§6.8). */
  direction?: string;
}

const TYPE_LABELS: Record<string, string> = {
  concrete: "Бетон",
  rebar: "Арматура",
  other: "Прочее",
};

export function CorridorsTab({ projectId, direction }: Props) {
  const { data: matrix, isLoading } = useCorridors(projectId);
  const setType = useSetTypeCorridor(projectId);
  const deleteType = useDeleteTypeCorridor(projectId);
  const setClass = useSetClassCorridor(projectId);
  const deleteClass = useDeleteClassCorridor(projectId);

  const [editingClass, setEditingClass] = useState<ID | null>(null);
  const [editPct, setEditPct] = useState("");
  const [editCompensable, setEditCompensable] = useState(true);

  const [editingType, setEditingType] = useState<string | null>(null);
  const [typePct, setTypePct] = useState("");

  if (isLoading || !matrix) {
    return <Skeleton className="h-40" />;
  }

  const typeMap = new Map(matrix.types.map((t) => [t.material_type, t]));
  const grouped = new Map<string, CorridorClassResolved[]>();
  for (const cls of matrix.classes) {
    const list = grouped.get(cls.material_type) ?? [];
    list.push(cls);
    grouped.set(cls.material_type, list);
  }
  // В режиме направления — только его тип и его классы (фильтрация на фронте, §3.3)
  const allTypes = direction
    ? [direction]
    : [...new Set([...typeMap.keys(), ...grouped.keys()])].sort();

  function handleTypeSetup(mt: string) {
    setEditingType(mt);
    setTypePct("5");
  }

  function handleTypeRemove(mt: string) {
    deleteType.mutate(mt);
  }

  function handleTypeSave(mt: string) {
    const pct = parseFloat(typePct.replace(",", "."));
    if (!Number.isFinite(pct) || pct < 0 || pct > 100) return;
    setType.mutate({ materialType: mt, payload: { is_compensable: true, corridor_pct: pct } });
    setEditingType(null);
  }

  function handleTypeDisable(mt: string) {
    setType.mutate({ materialType: mt, payload: { is_compensable: false } });
    setEditingType(null);
  }

  function startClassEdit(cls: CorridorClassResolved) {
    setEditingClass(cls.material_class_id);
    setEditPct(cls.corridor_pct != null ? String(cls.corridor_pct) : "5");
    setEditCompensable(cls.is_compensable);
  }

  function handleClassSave(classId: ID) {
    if (editCompensable) {
      const pct = parseFloat(editPct.replace(",", "."));
      if (!Number.isFinite(pct) || pct < 0 || pct > 100) return;
      setClass.mutate({ materialClassId: classId, payload: { is_compensable: true, corridor_pct: pct } });
    } else {
      setClass.mutate({ materialClassId: classId, payload: { is_compensable: false } });
    }
    setEditingClass(null);
  }

  return (
    <Surface padding="none" className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow className="text-xs text-fg-tertiary hover:bg-transparent">
            <TableHead className="font-medium w-[200px]">Класс</TableHead>
            <TableHead className="font-medium w-[120px]">Статус</TableHead>
            <TableHead className="font-medium w-[100px]">Коридор, %</TableHead>
            <TableHead className="font-medium w-[130px]">Источник</TableHead>
            <TableHead className="font-medium">Действия</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {allTypes.map((mt) => {
            const rule = typeMap.get(mt);
            const classes = grouped.get(mt) ?? [];
            const isEditingThisType = editingType === mt;
            const label = TYPE_LABELS[mt] ?? mt;

            return (
              <>
                {/* Type header row */}
                <TableRow key={`type-${mt}`} className="bg-surface-sunken">
                  <TableCell className="font-semibold text-fg">{label}</TableCell>
                  <TableCell>
                    {rule?.has_rule ? (
                      rule.is_compensable ? (
                        <span className="text-xs text-green-600">Вкл.</span>
                      ) : (
                        <span className="text-xs text-red-500">Выкл.</span>
                      )
                    ) : (
                      <span className="text-xs text-fg-tertiary">Не настроено</span>
                    )}
                  </TableCell>
                  <TableCell>
                    {isEditingThisType ? (
                      <input
                        aria-label="Процент коридора для типа"
                        autoFocus
                        className="w-20 rounded border border-border-subtle bg-surface px-2 py-1 text-right text-sm"
                        value={typePct}
                        onChange={(e) => setTypePct(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") handleTypeSave(mt);
                          if (e.key === "Escape") setEditingType(null);
                        }}
                      />
                    ) : rule?.corridor_pct != null ? (
                      <span className="font-mono text-sm">{rule.corridor_pct}%</span>
                    ) : (
                      <span className="text-fg-tertiary">—</span>
                    )}
                  </TableCell>
                  <TableCell />
                  <TableCell>
                    {isEditingThisType ? (
                      <div className="flex gap-2">
                        <Button size="sm" variant="primary" onClick={() => handleTypeSave(mt)}>
                          Сохранить
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => handleTypeDisable(mt)}>
                          Выкл.
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setEditingType(null)}>
                          Отмена
                        </Button>
                      </div>
                    ) : rule?.has_rule ? (
                      <Button size="sm" variant="ghost" onClick={() => handleTypeRemove(mt)}>
                        Снять
                      </Button>
                    ) : (
                      <Button size="sm" variant="ghost" onClick={() => handleTypeSetup(mt)}>
                        Настроить
                      </Button>
                    )}
                  </TableCell>
                </TableRow>

                {/* Class rows */}
                {classes.map((cls) => {
                  const isClassEditing = editingClass === cls.material_class_id;
                  return (
                    <TableRow key={`class-${cls.material_class_id}`}>
                      <TableCell className="pl-8 text-fg">{cls.material_class_name}</TableCell>
                      <TableCell>
                        {isClassEditing ? (
                          <label className="flex items-center gap-2 cursor-pointer">
                            <input
                              type="checkbox"
                              checked={editCompensable}
                              onChange={(e) => setEditCompensable(e.target.checked)}
                            />
                            <span className="text-xs">Компенсировать</span>
                          </label>
                        ) : cls.level === "default" ? (
                          <span className="text-xs text-fg-tertiary">—</span>
                        ) : cls.is_compensable ? (
                          <span className="text-xs text-green-600">✓</span>
                        ) : (
                          <span className="text-xs text-red-500">✗</span>
                        )}
                      </TableCell>
                      <TableCell>
                        {isClassEditing && editCompensable ? (
                          <input
                            aria-label="Процент коридора для класса"
                            autoFocus
                            className="w-20 rounded border border-border-subtle bg-surface px-2 py-1 text-right text-sm"
                            value={editPct}
                            onChange={(e) => setEditPct(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") handleClassSave(cls.material_class_id);
                              if (e.key === "Escape") setEditingClass(null);
                            }}
                          />
                        ) : cls.corridor_pct != null ? (
                          <span className="font-mono text-sm">{cls.corridor_pct}%</span>
                        ) : (
                          <span className="text-fg-tertiary">—</span>
                        )}
                      </TableCell>
                      <TableCell>
                        {cls.has_override ? (
                          <span className="text-xs font-medium text-accent">[своё]</span>
                        ) : cls.level === "type" ? (
                          <span className="text-xs text-fg-tertiary">(наследовано)</span>
                        ) : null}
                      </TableCell>
                      <TableCell>
                        {isClassEditing ? (
                          <div className="flex gap-2">
                            <Button size="sm" variant="primary" onClick={() => handleClassSave(cls.material_class_id)}>
                              Сохранить
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => setEditingClass(null)}>
                              Отмена
                            </Button>
                          </div>
                        ) : (
                          <div className="flex gap-2">
                            <Button size="sm" variant="ghost" onClick={() => startClassEdit(cls)}>
                              Изменить
                            </Button>
                            {cls.has_override && (
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => deleteClass.mutate(cls.material_class_id)}
                              >
                                ×
                              </Button>
                            )}
                          </div>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </>
            );
          })}
        </TableBody>
      </Table>
    </Surface>
  );
}
