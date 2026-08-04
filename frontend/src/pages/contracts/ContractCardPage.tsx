import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { AlertTriangle, Download, Pencil } from "lucide-react";

import { ContractFormDialog } from "@/components/contracts/ContractFormDialog";
import { EstimateUploadPanel } from "@/components/contracts/EstimateUploadPanel";
import { Breadcrumbs } from "@/components/ui-domain/Breadcrumbs";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { MoneyCell } from "@/components/ui-domain/MoneyCell";
import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { StatusPill } from "@/components/ui-domain/StatusPill";
import { Surface } from "@/components/ui-domain/Surface";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useCurrentUser } from "@/hooks/useAuth";
import { formatDate } from "@/lib/format";
import { useContract, useContractImportJobs, useDownloadJobFile } from "@/services/queries";
import type { ContractImportJob } from "@/types/domain";

/**
 * Карточка договора (AGENTS.md §7.1): реквизиты, класс, текущие сметы, загрузка
 * с поллингом и **история загрузок и замен** со скачиванием исходного XLSX.
 *
 * Заменённые сметы доступны только как файл — структурированная история за
 * скоупом MVP (§7.1). Поэтому история показывает, какое задание держит
 * актуальную смету (`is_current`), а какое было вытеснено заменой.
 */
export default function ContractCardPage() {
  const { contractId } = useParams();
  const id = contractId ? Number(contractId) : undefined;
  const { data: user } = useCurrentUser();
  const isAdmin = user?.role === "admin";
  const [editOpen, setEditOpen] = useState(false);

  const contractQ = useContract(id);
  const jobsQ = useContractImportJobs(id);
  const contract = contractQ.data;

  if (contractQ.isPending) {
    return (
      <div className="container-page py-8">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="mt-4 h-40 w-full" />
      </div>
    );
  }

  if (contractQ.isError || !contract) {
    return (
      <div className="container-page py-8">
        <EmptyState
          title="Договор не найден"
          description="Возможно, он удалён."
          action={
            <Button variant="outline" render={<Link to="/contracts">К списку договоров</Link>} />
          }
        />
      </div>
    );
  }

  return (
    <div className="container-page py-8">
      <Breadcrumbs
        items={[
          { label: "Договоры", to: "/contracts" },
          { label: contract.contract_number },
        ]}
      />

      <div className="mt-4">
        <PageHeader
          serif
          title={contract.contract_number}
          subtitle={`${contract.object_title} · ${contract.contractor_title}`}
          actions={
            isAdmin && (
              <Button variant="outline" onClick={() => setEditOpen(true)}>
                <Pencil className="size-4" /> Правка
              </Button>
            )
          }
        />
      </div>

      <Surface className="mt-6">
        <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Класс объектов">
            <Badge variant="secondary">{contract.rate_class_title}</Badge>
          </Field>
          <Field label="Подписант">{contract.signer ?? "—"}</Field>
          <Field label="Дата подписания">{formatDate(contract.signed_date)}</Field>
          <Field label="Сумма договора">
            <MoneyCell value={contract.total_amount} />
          </Field>
          {contract.title && <Field label="Название">{contract.title}</Field>}
          {contract.notes && <Field label="Примечания">{contract.notes}</Field>}
        </dl>
      </Surface>

      <Tabs defaultValue="estimates" className="mt-6">
        <TabsList>
          <TabsTrigger value="estimates">Сметы ({contract.estimates.length})</TabsTrigger>
          <TabsTrigger value="upload">Загрузка</TabsTrigger>
          <TabsTrigger value="history">История загрузок ({jobsQ.data?.length ?? 0})</TabsTrigger>
        </TabsList>

        <TabsContent value="estimates" className="mt-4">
          {contract.estimates.length === 0 ? (
            <EmptyState
              title="Смет пока нет"
              description="Загрузите XLSX на вкладке «Загрузка» — задание выполнится в фоне."
            />
          ) : (
            <Surface padding="none" className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Смета</TableHead>
                    <TableHead>Дата подготовки</TableHead>
                    <TableHead className="text-right">Позиций</TableHead>
                    <TableHead>Загружена заданием</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {contract.estimates.map((estimate) => (
                    <TableRow key={estimate.id}>
                      <TableCell className="font-medium">
                        {estimate.amendment_no === null
                          ? "Исходная смета"
                          : `Доп. соглашение №${estimate.amendment_no}`}
                      </TableCell>
                      <TableCell className="tabular-nums">
                        {formatDate(estimate.data_prepared_on_date)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {estimate.positions_count}
                      </TableCell>
                      <TableCell className="tabular-nums text-fg-secondary">
                        {estimate.import_job_id ?? "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Surface>
          )}
        </TabsContent>

        <TabsContent value="upload" className="mt-4">
          <EstimateUploadPanel contractId={contract.id} />
        </TabsContent>

        <TabsContent value="history" className="mt-4">
          <ImportHistory jobs={jobsQ.data} loading={jobsQ.isPending} />
        </TabsContent>
      </Tabs>

      <ContractFormDialog open={editOpen} onOpenChange={setEditOpen} contract={contract} />
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-fg-tertiary">{label}</dt>
      <dd className="mt-0.5 text-sm text-fg">{children}</dd>
    </div>
  );
}

/**
 * Что стало со сметой этого задания.
 *
 * «Вытеснена заменой» вправе стоять только у задания, которое смету **создавало**:
 * до исправления так подписывалось любое задание с `is_current=false`, включая
 * незавершённые и упавшие — а они смету не создавали никогда, и подпись про них
 * прямо врала об аудите.
 */
function estimateFate(job: ContractImportJob): string | null {
  if (job.is_current) return null;
  if (job.status === "done") return "вытеснена заменой";
  if (job.status === "error") return "смета не создана";
  return "загрузка не завершена";
}

function ImportHistory({
  jobs,
  loading,
}: {
  jobs: ContractImportJob[] | undefined;
  loading: boolean;
}) {
  const download = useDownloadJobFile();

  if (loading) return <Skeleton className="h-32 w-full" />;
  if (!jobs || jobs.length === 0) {
    return <EmptyState title="Загрузок не было" description="История появится после первой загрузки." />;
  }

  return (
    <Surface padding="none" className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Задание</TableHead>
            <TableHead>Файл</TableHead>
            <TableHead>Смета</TableHead>
            <TableHead>Статус</TableHead>
            <TableHead>Счётчики матчинга</TableHead>
            <TableHead>Создано</TableHead>
            <TableHead>Исходник</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {jobs.map((job) => {
            const fate = estimateFate(job);
            return (
              <TableRow key={job.id}>
                <TableCell className="tabular-nums">{job.id}</TableCell>
                <TableCell>
                  <div className="max-w-xs truncate">{job.filename}</div>
                  {/*
                    Тексты предупреждений, а не только их число: панель загрузки
                    живёт до перезагрузки страницы, и без этого DoD «в карточке
                    видны счётчики и предупреждения» не выполняется — данные
                    существуют в БД, но человеку недоступны.
                  */}
                  {job.warnings.length > 0 && (
                    <details className="mt-1">
                      <summary className="flex cursor-pointer items-center gap-1 text-2xs text-warning-text">
                        <AlertTriangle className="size-3" />
                        предупреждений: {job.warnings.length}
                      </summary>
                      <ul className="mt-1 grid gap-0.5 text-2xs text-fg-secondary">
                        {job.warnings.map((warning, index) => (
                          <li key={index}>{warning}</li>
                        ))}
                      </ul>
                    </details>
                  )}
                  {job.status === "error" && job.error_text && (
                    <p className="mt-1 text-2xs text-danger-text">{job.error_text}</p>
                  )}
                </TableCell>
                <TableCell>
                  {job.amendment_no === null ? "исходная" : `доп. №${job.amendment_no}`}
                  {job.is_current ? (
                    <Badge className="ml-2" variant="secondary">
                      актуальная
                    </Badge>
                  ) : (
                    <span className="ml-2 text-2xs text-fg-tertiary">{fate}</span>
                  )}
                </TableCell>
                <TableCell>
                  <StatusPill
                    tone={
                      job.status === "done" ? "success" : job.status === "error" ? "danger" : "info"
                    }
                    label={job.status}
                  />
                </TableCell>
                <TableCell className="whitespace-nowrap font-mono text-2xs tabular-nums text-fg-secondary">
                  {job.status === "done" ? (
                    <>
                      всего {job.counters.positions_total} · кэш {job.counters.matched_cache} ·
                      точно {job.counters.matched_exact} · не работы{" "}
                      {job.counters.matched_nonposition} · на разбор {job.counters.to_review}
                    </>
                  ) : (
                    "—"
                  )}
                </TableCell>
                <TableCell className="tabular-nums">{formatDate(job.created_at)}</TableCell>
                <TableCell>
                  {/*
                    Кнопка, а не `<a href>`: экран обязан различать 404 («задания
                    нет») и 410 («аудит есть, файл удалён ретенцией», §8) — со
                    ссылкой разбор статуса ушёл бы браузеру и человек увидел бы
                    сырой JSON. Тексты обоих отказов — в `useDownloadJobFile`.
                  */}
                  <Button
                    size="xs"
                    variant="ghost"
                    disabled={download.isPending}
                    onClick={() =>
                      download.mutate({ jobId: job.id, filename: job.filename })
                    }
                  >
                    <Download className="size-3.5" /> скачать
                  </Button>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </Surface>
  );
}
