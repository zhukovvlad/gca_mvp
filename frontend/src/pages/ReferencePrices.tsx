import { useMemo, useState } from "react";
import { Plus, Trash2, Target } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import { Button } from "@/components/ui-domain/Button";
import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Surface } from "@/components/ui-domain/Surface";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { MoneyCell } from "@/components/ui-domain/MoneyCell";
import { EntitySelect } from "@/components/ui-domain/EntitySelect";

import {
  useProjects,
  useMaterialClasses,
  useReferencePrices,
  useCreateReferencePrice,
  useDeleteReferencePrice,
  useUnits,
} from "@/services/queries";
import { useDefaultUnitId } from "@/lib/useDefaultUnitId";
import { formatDate } from "@/lib/format";
import type { ID } from "@/types/common";

export default function ReferencePrices() {
  const projectsQ = useProjects();
  const classesQ = useMaterialClasses();

  const [filterProject, setFilterProject] = useState<ID | null>(null);
  const list = useReferencePrices(filterProject ?? undefined);
  const create = useCreateReferencePrice();
  const remove = useDeleteReferencePrice();

  const unitsQ = useUnits();
  const baseUnits = useMemo(
    () => (unitsQ.data ?? []).filter((u) => u.base_unit_id === null),
    [unitsQ.data],
  );
  const getDefaultUnitId = useDefaultUnitId();

  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    project_id: "",
    material_class_id: "",
    unit_id: "",
    price: "",
    period_start: "",
    period_end: "",
    source: "",
  });

  const reset = () =>
    setForm({
      project_id: "",
      material_class_id: "",
      unit_id: "",
      price: "",
      period_start: "",
      period_end: "",
      source: "",
    });

  const canSubmit =
    form.project_id &&
    form.material_class_id &&
    form.unit_id &&
    form.price &&
    form.period_start &&
    form.period_end;

  const submit = () => {
    if (!canSubmit) return;
    create.mutate(
      {
        project_id: Number(form.project_id),
        material_class_id: Number(form.material_class_id),
        unit_id: Number(form.unit_id),
        price: Number(form.price),
        period_start: form.period_start,
        period_end: form.period_end,
        source: form.source.trim() || null,
      },
      {
        onSuccess: () => {
          setOpen(false);
          reset();
        },
      }
    );
  };

  const classNameById = useMemo(() => {
    const m = new Map<number, string>();
    (classesQ.data ?? []).forEach((c) => m.set(c.id, c.name));
    return m;
  }, [classesQ.data]);

  return (
    <div className="container-page py-8">
      <PageHeader
        serif
        title="Эталонные цены"
        subtitle="Базовые цены, относительно которых считаются отклонения"
        actions={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger render={<Button leftIcon={<Plus size={14} />}>Добавить эталон</Button>} />
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Новый эталон</DialogTitle>
              </DialogHeader>
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                    Объект *
                  </Label>
                  <EntitySelect
                    items={projectsQ.data}
                    value={form.project_id ? Number(form.project_id) : null}
                    onChange={(v) =>
                      setForm({ ...form, project_id: v ? String(v) : "" })
                    }
                    getLabel={(p) => p.name}
                    placeholder="Выберите объект"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                    Класс материала *
                  </Label>
                  <EntitySelect
                    items={classesQ.data}
                    value={
                      form.material_class_id
                        ? Number(form.material_class_id)
                        : null
                    }
                    onChange={(v) => {
                      const newClassId = v ? String(v) : "";
                      setForm((f) => ({
                        ...f,
                        material_class_id: newClassId,
                        unit_id: getDefaultUnitId(newClassId),
                      }));
                    }}
                    getLabel={(c) => c.name}
                    placeholder="Выберите класс"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                    Единица измерения *
                  </Label>
                  <EntitySelect
                    items={baseUnits}
                    value={form.unit_id ? Number(form.unit_id) : null}
                    onChange={(v) => setForm({ ...form, unit_id: v ? String(v) : "" })}
                    getLabel={(u) => `${u.name} (${u.symbol})`}
                    placeholder="Выберите единицу"
                    disabled={unitsQ.isLoading}
                  />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                      Цена ₽ *
                    </Label>
                    <Input
                      type="number"
                      step="0.01"
                      value={form.price}
                      onChange={(e) =>
                        setForm({ ...form, price: e.target.value })
                      }
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                      Источник
                    </Label>
                    <Input
                      value={form.source}
                      onChange={(e) =>
                        setForm({ ...form, source: e.target.value })
                      }
                      placeholder="договор / прайс / ..."
                    />
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                      Действует с *
                    </Label>
                    <Input
                      type="date"
                      value={form.period_start}
                      onChange={(e) =>
                        setForm({ ...form, period_start: e.target.value })
                      }
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                      Действует по *
                    </Label>
                    <Input
                      type="date"
                      value={form.period_end}
                      onChange={(e) =>
                        setForm({ ...form, period_end: e.target.value })
                      }
                    />
                  </div>
                </div>
              </div>
              <DialogFooter>
                <Button variant="ghost" onClick={() => setOpen(false)}>
                  Отмена
                </Button>
                <Button
                  onClick={submit}
                  loading={create.isPending}
                  disabled={!canSubmit}
                >
                  Сохранить
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        }
      />

      {/* Фильтр по объекту */}
      <div className="mt-6 flex items-center gap-3">
        <Label className="text-xs text-fg-tertiary">Объект</Label>
        <EntitySelect
          items={projectsQ.data}
          value={filterProject}
          onChange={(v) => setFilterProject(v as ID | null)}
          getLabel={(p) => p.name}
          placeholder="Все объекты"
          className="w-[280px]"
        />
        {filterProject && (
          <Button variant="ghost" size="sm" onClick={() => setFilterProject(null)}>
            Сбросить
          </Button>
        )}
      </div>

      <div className="mt-6">
        {list.isLoading ? (
          <Surface padding="none">
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
          </Surface>
        ) : (list.data ?? []).length === 0 ? (
          <EmptyState
            icon={<Target size={20} />}
            title="Нет эталонных цен"
            description="Добавьте первый эталон, чтобы система могла считать отклонения."
            action={
              <Button leftIcon={<Plus size={14} />} onClick={() => setOpen(true)}>
                Добавить эталон
              </Button>
            }
          />
        ) : (
          <Surface padding="none">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Класс</TableHead>
                  <TableHead>Период</TableHead>
                  <TableHead className="text-right">Цена</TableHead>
                  <TableHead>Ед.</TableHead>
                  <TableHead>Источник</TableHead>
                  <TableHead className="w-12"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(list.data ?? []).map((rp) => (
                  <TableRow key={rp.id}>
                    <TableCell className="font-medium">
                      {rp.material_class_name ??
                        classNameById.get(rp.material_class_id) ??
                        "—"}
                    </TableCell>
                    <TableCell className="text-fg-secondary">
                      {formatDate(rp.period_start)} — {formatDate(rp.period_end)}
                    </TableCell>
                    <TableCell className="text-right">
                      <MoneyCell value={rp.price} />
                    </TableCell>
                    <TableCell className="text-fg-secondary">{rp.unit_symbol ?? "—"}</TableCell>
                    <TableCell className="text-fg-secondary">
                      {rp.source ?? "—"}
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          if (window.confirm("Удалить эталон?")) {
                            remove.mutate(rp.id);
                          }
                        }}
                        aria-label="Удалить"
                      >
                        <Trash2 size={14} />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Surface>
        )}
      </div>
    </div>
  );
}
