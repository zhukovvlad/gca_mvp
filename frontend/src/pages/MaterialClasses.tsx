import { useState } from "react";
import { Plus, Trash2, Layers } from "lucide-react";

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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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
import { StatusPill } from "@/components/ui-domain/StatusPill";

import {
  useMaterialClasses,
  useCreateMaterialClass,
  useDeleteMaterialClass,
} from "@/services/queries";
import { formatDate } from "@/lib/format";

const TYPE_LABELS: Record<string, string> = {
  concrete: "Бетон",
  rebar: "Арматура",
  other: "Прочее",
};

export default function MaterialClasses() {
  const list = useMaterialClasses();
  const create = useCreateMaterialClass();
  const remove = useDeleteMaterialClass();

  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [type, setType] = useState("concrete");

  const submit = () => {
    if (!name.trim()) return;
    create.mutate(
      { name: name.trim(), material_type: type },
      {
        onSuccess: () => {
          setOpen(false);
          setName("");
          setType("concrete");
        },
      }
    );
  };

  return (
    <div className="container-page py-8">
      <PageHeader
        serif
        title="Классы материалов"
        subtitle="Группы для агрегации цен и расчёта отклонений"
        actions={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger render={<Button leftIcon={<Plus size={14} />}>Добавить класс</Button>} />
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Новый класс материала</DialogTitle>
              </DialogHeader>
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                    Тип материала
                  </Label>
                  <Select value={type} onValueChange={(v: string | null) => setType(v ?? "concrete")}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="concrete">Бетон</SelectItem>
                      <SelectItem value="rebar">Арматура</SelectItem>
                      <SelectItem value="other">Прочее</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                    Название класса *
                  </Label>
                  <Input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="например, В25, А500С"
                  />
                </div>
              </div>
              <DialogFooter>
                <Button variant="ghost" onClick={() => setOpen(false)}>
                  Отмена
                </Button>
                <Button
                  onClick={submit}
                  loading={create.isPending}
                  disabled={!name.trim()}
                >
                  Добавить
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        }
      />

      <div className="mt-6">
        {list.isLoading ? (
          <Surface padding="none">
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
          </Surface>
        ) : (list.data ?? []).length === 0 ? (
          <EmptyState
            icon={<Layers size={20} />}
            title="Нет классов материалов"
            description="Добавьте первый класс — например, бетон В25."
            action={
              <Button leftIcon={<Plus size={14} />} onClick={() => setOpen(true)}>
                Добавить класс
              </Button>
            }
          />
        ) : (
          <Surface padding="none">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Название</TableHead>
                  <TableHead>Тип</TableHead>
                  <TableHead>Создан</TableHead>
                  <TableHead className="w-12"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(list.data ?? []).map((c) => (
                  <TableRow key={c.id}>
                    <TableCell className="font-medium">{c.name}</TableCell>
                    <TableCell>
                      <StatusPill
                        tone="neutral"
                        label={TYPE_LABELS[c.material_type] ?? c.material_type}
                      />
                    </TableCell>
                    <TableCell className="text-fg-secondary">
                      {formatDate(c.created_at)}
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          if (window.confirm(`Удалить «${c.name}»?`)) {
                            remove.mutate(c.id);
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
