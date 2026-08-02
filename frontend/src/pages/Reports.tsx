import { useState } from "react";
import { FileSpreadsheet, Download } from "lucide-react";
import { toast } from "sonner";

import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Surface } from "@/components/ui-domain/Surface";
import { Button } from "@/components/ui-domain/Button";
import { EntitySelect } from "@/components/ui-domain/EntitySelect";

import { useProjects } from "@/services/queries";
import { reportsApi } from "@/services/api/reports";
import type { ID } from "@/types/common";

export default function Reports() {
  const projectsQ = useProjects();
  const [open, setOpen] = useState(false);
  const [projectId, setProjectId] = useState<ID | null>(null);
  const [periodStart, setPeriodStart] = useState("");
  const [periodEnd, setPeriodEnd] = useState("");
  const [downloading, setDownloading] = useState(false);

  const download = async () => {
    if (!projectId) return;
    setDownloading(true);
    try {
      const blob = await reportsApi.excelBlob({
        project_id: projectId,
        period_start: periodStart || undefined,
        period_end: periodEnd || undefined,
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `report-${projectId}-${Date.now()}.xlsx`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      toast.success("Отчёт сформирован");
      setOpen(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Не удалось сформировать отчёт");
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="container-page py-8">
      <PageHeader serif title="Отчёты" subtitle="Экспорт аналитики в Excel" />

      <div className="mt-6 grid grid-cols-1 gap-3 md:grid-cols-2">
        <Surface>
          <div className="flex items-start gap-4">
            <div className="grid h-10 w-10 shrink-0 place-items-center rounded-md bg-accent-soft text-accent-text">
              <FileSpreadsheet size={18} />
            </div>
            <div className="min-w-0 flex-1">
              <h3 className="text-md font-medium text-fg">Сводный Excel</h3>
              <p className="mt-1 text-sm text-fg-secondary">
                Все счета-фактуры, позиции и расчёты отклонений по выбранному
                объекту и периоду.
              </p>
              <Button
                className="mt-4"
                leftIcon={<Download size={14} />}
                onClick={() => setOpen(true)}
              >
                Сформировать
              </Button>
            </div>
          </div>
        </Surface>
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Сформировать сводный Excel</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                Объект *
              </Label>
              <EntitySelect
                items={projectsQ.data}
                value={projectId}
                onChange={(v) => setProjectId(v as ID | null)}
                getLabel={(p) => p.name}
                placeholder="Выберите объект"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                  Период с
                </Label>
                <Input
                  type="date"
                  value={periodStart}
                  onChange={(e) => setPeriodStart(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-2xs uppercase tracking-wider text-fg-tertiary">
                  По
                </Label>
                <Input
                  type="date"
                  value={periodEnd}
                  onChange={(e) => setPeriodEnd(e.target.value)}
                />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Отмена
            </Button>
            <Button onClick={download} loading={downloading} disabled={!projectId}>
              Скачать
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
