import { Link } from "react-router-dom";
import { AlertTriangle, Clock } from "lucide-react";

import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Surface } from "@/components/ui-domain/Surface";
import { KpiCard } from "@/components/ui-domain/KpiCard";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { PriceChart } from "@/components/dashboard/PriceChart";

import { useProjects, useDocuments, useAllCalculations } from "@/services/queries";
import { formatMoney, formatDate } from "@/lib/format";

export default function Dashboard() {
  const projectsQ = useProjects();
  const docsQ = useDocuments();
  const calcsQ = useAllCalculations();

  const totalOverpay = (calcsQ.data ?? []).reduce((s, c) => s + (c.deviation_amount ?? 0), 0);
  const totalTurnover = (calcsQ.data ?? []).reduce((s, c) => s + (c.material_total ?? 0) + (c.delivery_total ?? 0), 0);
  const issueCount = (docsQ.data ?? []).filter((d) => d.has_issues).length;
  const issueDocs = (docsQ.data ?? []).filter((d) => d.has_issues).slice(0, 5);

  return (
    <div className="container-page py-8">
      <PageHeader serif title="Сводка по портфелю" subtitle="Аналитика закупок по всем объектам" />

      <div className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-3">
        {calcsQ.isLoading || docsQ.isLoading ? (
          <><Skeleton className="h-[88px]" /><Skeleton className="h-[88px]" /><Skeleton className="h-[88px]" /></>
        ) : (
          <>
            <KpiCard label="Оборот" value={calcsQ.data?.length ? formatMoney(totalTurnover) : "—"} />
            <KpiCard label="Переплата к базовым" value={calcsQ.data?.length ? formatMoney(totalOverpay) : "—"} />
            <KpiCard label="Требуют внимания" value={String(issueCount)} />
          </>
        )}
      </div>

      <Surface className="mt-6">
        {calcsQ.isLoading || calcsQ.isFetching ? <Skeleton className="h-48" /> : <PriceChart calculations={calcsQ.data ?? []} />}
      </Surface>

      <Surface className="mt-6">
        <h2 className="mb-4 font-serif text-base font-medium text-fg">
          Объекты
          {projectsQ.data && <span className="ml-2 text-sm font-normal text-fg-tertiary">· {projectsQ.data.length}</span>}
        </h2>
        {projectsQ.isLoading ? (
          <div className="space-y-2"><Skeleton className="h-10" /><Skeleton className="h-10" /></div>
        ) : (projectsQ.data ?? []).length === 0 ? (
          <EmptyState title="Нет объектов" description="Создайте первый объект в разделе Объекты." />
        ) : (
          <div className="divide-y divide-border-subtle">
            {(projectsQ.data ?? []).map((p) => (
              <Link key={p.id} to={`/projects/${p.id}`}
                className="flex items-center justify-between py-3 text-sm hover:text-accent transition-colors">
                <span className="font-medium">{p.name}</span>
              </Link>
            ))}
          </div>
        )}
      </Surface>

      {issueDocs.length > 0 && (
        <Surface className="mt-6">
          <h2 className="mb-4 font-serif text-base font-medium text-fg">Требуют внимания</h2>
          <div className="space-y-2">
            {issueDocs.map((d) => (
              <Link key={d.id} to={`/documents/${d.id}`}
                className="flex items-center gap-3 rounded-md px-3 py-2 text-sm hover:bg-surface-hover transition-colors">
                <AlertTriangle size={14} className="text-warning shrink-0" />
                <span className="flex-1 truncate">{d.filename}</span>
                <span className="text-fg-tertiary flex items-center gap-1">
                  <Clock size={12} /> {d.uploaded_at ? formatDate(d.uploaded_at) : "—"}
                </span>
              </Link>
            ))}
          </div>
        </Surface>
      )}
    </div>
  );
}
