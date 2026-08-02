import { useState, useCallback } from "react";
import { toast } from "sonner";

import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Dropzone } from "@/components/ui-domain/Dropzone";
import { UploadJobRow, type JobState } from "@/components/upload/UploadJobRow";

import { useUploadInvoice } from "@/services/queries";
import type { ID } from "@/types/common";

interface Props {
  projectId: ID;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Шит загрузки счетов-фактур/УПД, привязанный к объекту (ProjectPage).
 * Джоб-модель (pending|uploading|ready|error) и сеяние 202-ответа в
 * query-кэш — из донора `pages/Upload.tsx` (мёртвая страница, удалена вместе
 * с переносом логики сюда, смоук PR #37). Шит НЕ закрывается автоматически
 * после загрузки — пользователь видит статус обработки построчно
 * (`<UploadJobRow>`, polling через `useDocument`), dropzone остаётся активной
 * для докидывания новых файлов.
 */
export function UploadSheet({ projectId, open, onOpenChange }: Props) {
  const upload = useUploadInvoice();
  const [jobs, setJobs] = useState<JobState[]>([]);

  const handleDrop = useCallback(
    async (files: File[]) => {
      const newJobs: JobState[] = files.map((f, i) => ({
        id: `${Date.now()}-${i}-${f.name}`,
        file: f,
        status: "pending",
        progress: 0,
      }));
      setJobs((prev) => [...newJobs, ...prev]);

      for (const job of newJobs) {
        setJobs((prev) =>
          prev.map((j) => (j.id === job.id ? { ...j, status: "uploading" } : j))
        );
        try {
          const result = await upload.mutateAsync({
            projectId,
            file: job.file,
            onProgress: (pct) =>
              setJobs((prev) =>
                prev.map((j) => (j.id === job.id ? { ...j, progress: pct } : j))
              ),
          });
          setJobs((prev) =>
            prev.map((j) =>
              j.id === job.id ? { ...j, status: "ready", result, progress: 100 } : j
            )
          );
          if (result.duplicate) {
            toast.info(`«${job.file.name}» — файл уже был загружен`);
          } else {
            toast.success(`«${job.file.name}» принят в обработку`);
          }
        } catch (err) {
          setJobs((prev) =>
            prev.map((j) =>
              j.id === job.id
                ? {
                    ...j,
                    status: "error",
                    error: err instanceof Error ? err.message : "Ошибка загрузки",
                  }
                : j
            )
          );
          toast.error(`«${job.file.name}» — ошибка загрузки`);
        }
      }
    },
    [projectId, upload]
  );

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full max-w-[480px]">
        <SheetHeader>
          <SheetTitle>Добавить счёт</SheetTitle>
        </SheetHeader>
        <div className="mt-6 shrink-0">
          <Dropzone onDrop={handleDrop} />
        </div>
        {jobs.length > 0 && (
          <div className="mt-5 flex-1 min-h-0 space-y-2 overflow-y-auto pr-1">
            {jobs.map((j) => (
              <UploadJobRow key={j.id} job={j} />
            ))}
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
