import { useParams } from "react-router-dom";
import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Surface } from "@/components/ui-domain/Surface";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { MoneyCell } from "@/components/ui-domain/MoneyCell";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Breadcrumbs } from "@/components/ui-domain/Breadcrumbs";
import { StatusPill } from "@/components/ui-domain/StatusPill";

import { useMaterialClasses, useReferencePrices } from "@/services/queries";
import { formatDate } from "@/lib/format";

const TYPE_LABELS: Record<string, string> = { concrete: "Бетон", rebar: "Арматура", other: "Прочее" };

export default function MaterialPage() {
  const { id } = useParams<{ id: string }>();
  const parsed = id ? Number(id) : NaN;
  const materialId = Number.isFinite(parsed) && parsed > 0 ? parsed : null;

  const classesQ = useMaterialClasses();
  const material = classesQ.data?.find((c) => c.id === materialId) ?? null;
  const rpQ = useReferencePrices(undefined, {
    materialClassId: materialId ?? undefined,
    enabled: materialId !== null,
  });

  const materialPrices = rpQ.data ?? [];

  if (classesQ.isLoading) return <div className="container-page py-8"><Skeleton className="h-8 w-1/3" /></div>;
  if (!material) return <div className="container-page py-8"><EmptyState title="Материал не найден" /></div>;

  return (
    <div className="container-page py-8">
      <Breadcrumbs items={[{ label: "Номенклатура", to: "/materials" }, { label: material.name }]} />
      <div className="flex items-center gap-3 mt-2">
        <PageHeader serif title={material.name} />
        <StatusPill tone="neutral" label={TYPE_LABELS[material.material_type] ?? material.material_type} />
      </div>

      <Tabs defaultValue="overview" className="mt-6">
        <TabsList>
          <TabsTrigger value="overview">Обзор</TabsTrigger>
          <TabsTrigger value="suppliers">Поставщики</TabsTrigger>
          <TabsTrigger value="prices">
            Базовые цены{materialPrices.length ? ` · ${materialPrices.length}` : ""}
          </TabsTrigger>
          <TabsTrigger value="projects">Объекты</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="pt-6">
          <EmptyState title="История цен" description="График динамики цен появится после обновления сервиса." />
        </TabsContent>

        <TabsContent value="suppliers" className="pt-6">
          <EmptyState title="Поставщики материала" description="Будет доступно после обновления сервиса." />
        </TabsContent>

        <TabsContent value="prices" className="pt-6">
          {rpQ.isLoading ? <Skeleton className="h-40" /> : materialPrices.length === 0 ? (
            <EmptyState title="Нет базовых цен" description="Базовые цены настраиваются в карточке объекта." />
          ) : (
            <Surface padding="none">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Объект</TableHead>
                    <TableHead>Период</TableHead>
                    <TableHead className="text-right">Базовая цена</TableHead>
                    <TableHead>Источник</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {materialPrices.map((rp) => (
                    <TableRow key={rp.id}>
                      <TableCell className="font-medium">{rp.project_name ?? String(rp.project_id)}</TableCell>
                      <TableCell className="text-fg-secondary">{formatDate(rp.period_start)} — {formatDate(rp.period_end)}</TableCell>
                      <TableCell className="text-right"><MoneyCell value={rp.price} /></TableCell>
                      <TableCell className="text-fg-secondary">{rp.source ?? "—"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Surface>
          )}
        </TabsContent>

        <TabsContent value="projects" className="pt-6">
          <EmptyState title="Объекты с этим материалом" description="Будет доступно после обновления сервиса." />
        </TabsContent>
      </Tabs>
    </div>
  );
}
