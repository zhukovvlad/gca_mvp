import { useState } from "react";
import { Plus, RefreshCw, Search, Trash2 } from "lucide-react";

import { Pager } from "@/components/domain/Pager";
import { RateClassesTab } from "@/components/standards/RateClassesTab";
import { RateStandardFormDialog } from "@/components/standards/RateStandardFormDialog";
import { ReapproveDialog } from "@/components/standards/ReapproveDialog";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { MoneyCell } from "@/components/ui-domain/MoneyCell";
import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Surface } from "@/components/ui-domain/Surface";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatDate } from "@/lib/format";
import { useDebounce } from "@/lib/useDebounce";
import { useDeleteRateStandard, useRateClasses, useRateStandards } from "@/services/queries";
import type { RateStandard } from "@/types/domain";

const PAGE_SIZE = 20;
const ALL_CLASSES = "all";

/** Локальная дата в формате `YYYY-MM-DD` — то, что ждёт фильтр `on_date`. */
function today(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

/**
 * Экран «Нормативы» (AGENTS.md §7.3), право `admin` — маршрут обёрнут `RequireAdmin`.
 *
 * Две вкладки: ставки с периодами и классы объектов. Классы живут здесь по
 * решению §6.1 — §7.3 описывает этот экран как «классы, ставки, периоды».
 *
 * Фильтр «действующие на дату» использует тот же полуинтервал `[valid_from,
 * valid_to)`, что EXCLUDE-констрейнт и VIEW отклонений: иначе экран показывал бы
 * действующим не то, по чему считается отклонение.
 */
export default function StandardsPage() {
  const [searchInput, setSearchInput] = useState("");
  const [rateClassId, setRateClassId] = useState<string>(ALL_CLASSES);
  const [activeOnly, setActiveOnly] = useState(false);
  const [page, setPage] = useState(1);
  const [formOpen, setFormOpen] = useState(false);
  const [reapproving, setReapproving] = useState<RateStandard | null>(null);
  /**
   * Удаление норматива необратимо и стирает утверждённую ставку вместе с её
   * периодом, поэтому спрашивается подтверждение — как у классов объектов. Без
   * него один промах мышью убирал ставку, по которой считаются отклонения.
   */
  const [toDelete, setToDelete] = useState<RateStandard | null>(null);

  const search = useDebounce(searchInput, 300);
  const classesQ = useRateClasses();
  const standardsQ = useRateStandards({
    q: search || undefined,
    rate_class_id: rateClassId === ALL_CLASSES ? undefined : Number(rateClassId),
    on_date: activeOnly ? today() : undefined,
    page,
    page_size: PAGE_SIZE,
  });
  const remove = useDeleteRateStandard();

  const data = standardsQ.data;

  return (
    <div className="container-page py-8">
      <PageHeader
        serif
        title="Нормативы расценок"
        subtitle="Ставки по классам объектов с историей периодов"
        actions={
          <Button onClick={() => setFormOpen(true)}>
            <Plus className="size-4" /> Новый норматив
          </Button>
        }
      />

      <Tabs defaultValue="rates" className="mt-6">
        <TabsList>
          <TabsTrigger value="rates">Ставки</TabsTrigger>
          <TabsTrigger value="classes">Классы объектов</TabsTrigger>
        </TabsList>

        <TabsContent value="rates" className="mt-4">
          <div className="flex flex-wrap items-center gap-3">
            <InputGroup className="max-w-sm flex-1">
              <InputGroupInput
                aria-label="Поиск по названию работы"
                placeholder="Название работы"
                value={searchInput}
                onChange={(e) => {
                  setSearchInput(e.target.value);
                  setPage(1);
                }}
              />
              <InputGroupAddon align="inline-start">
                <Search size={13} />
              </InputGroupAddon>
            </InputGroup>

            <Select
              value={rateClassId}
              onValueChange={(value: string | null) => {
                setRateClassId(value ?? ALL_CLASSES);
                setPage(1);
              }}
            >
              <SelectTrigger className="w-56" aria-label="Класс объектов">
                <SelectValue>
                  {(raw) =>
                    !raw || raw === ALL_CLASSES
                      ? "Все классы"
                      : (classesQ.data?.find((c) => String(c.id) === raw)?.title ??
                        "Все классы")
                  }
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL_CLASSES}>Все классы</SelectItem>
                {(classesQ.data ?? []).map((rateClass) => (
                  <SelectItem key={rateClass.id} value={String(rateClass.id)}>
                    {rateClass.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <div className="flex items-center gap-2">
              <Checkbox
                id="active-only"
                checked={activeOnly}
                onCheckedChange={(checked) => {
                  setActiveOnly(checked === true);
                  setPage(1);
                }}
              />
              <Label htmlFor="active-only" className="text-sm font-normal">
                Только действующие сегодня
              </Label>
            </div>
          </div>

          <div className="mt-4">
            {standardsQ.isPending && <Skeleton className="h-40 w-full" />}

            {standardsQ.isError && (
              <EmptyState title="Ошибка загрузки" description="Не удалось получить нормативы." />
            )}

            {data && data.items.length === 0 && (
              <EmptyState
                title="Нормативов нет"
                description="Норматив назначается работе каталога и классу объектов на период."
              />
            )}

            {data && data.items.length > 0 && (
              <>
                <Surface padding="none" className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Работа</TableHead>
                        <TableHead>Класс</TableHead>
                        <TableHead className="text-right">Ставка</TableHead>
                        <TableHead>Период</TableHead>
                        <TableHead>Индекс</TableHead>
                        <TableHead>Утвердил</TableHead>
                        <TableHead />
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {data.items.map((standard) => (
                        <TableRow key={standard.id}>
                          <TableCell>
                            <div className="font-medium text-fg">
                              {standard.catalog_position_title}
                            </div>
                            {standard.unit_code && (
                              <span className="text-xs text-fg-tertiary">
                                {standard.unit_code}
                              </span>
                            )}
                          </TableCell>
                          <TableCell>
                            <Badge variant="secondary">{standard.rate_class_title}</Badge>
                          </TableCell>
                          <TableCell className="text-right">
                            <MoneyCell value={standard.standard_unit_rate} />
                          </TableCell>
                          <TableCell className="whitespace-nowrap tabular-nums">
                            {formatDate(standard.valid_from)} —{" "}
                            {standard.valid_to ? (
                              formatDate(standard.valid_to)
                            ) : (
                              <span className="text-accent-text">открыт</span>
                            )}
                          </TableCell>
                          <TableCell className="tabular-nums">
                            {standard.inflation_index ?? "—"}
                          </TableCell>
                          <TableCell className="text-fg-secondary">
                            {standard.approved_by ?? "—"}
                          </TableCell>
                          <TableCell>
                            <div className="flex justify-end gap-1">
                              {/*
                                Переутверждение предлагается только для открытого
                                периода: закрытый переутверждать нечего, сервер
                                на это отвечает 422.
                              */}
                              {standard.valid_to === null && (
                                <Button
                                  size="xs"
                                  variant="outline"
                                  onClick={() => setReapproving(standard)}
                                >
                                  <RefreshCw className="size-3.5" /> Переутвердить
                                </Button>
                              )}
                              <Button
                                size="icon-sm"
                                variant="ghost"
                                aria-label={`Удалить норматив ${standard.catalog_position_title}`}
                                disabled={remove.isPending}
                                onClick={() => setToDelete(standard)}
                              >
                                <Trash2 className="size-4" />
                              </Button>
                            </div>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </Surface>

                <Pager
                  page={page}
                  total={data.total}
                  pageSize={PAGE_SIZE}
                  onPageChange={setPage}
                />
              </>
            )}
          </div>
        </TabsContent>

        <TabsContent value="classes" className="mt-4">
          <RateClassesTab />
        </TabsContent>
      </Tabs>

      <AlertDialog open={toDelete !== null} onOpenChange={(open) => !open && setToDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Удалить норматив?</AlertDialogTitle>
            <AlertDialogDescription>
              {toDelete && (
                <>
                  «{toDelete.catalog_position_title}» · {toDelete.rate_class_title} ·{" "}
                  {toDelete.standard_unit_rate} с {toDelete.valid_from}. Удаление
                  необратимо: отклонения смет за этот период перестанут считаться вовсе —
                  «нет норматива» и «ноль процентов» это разные вещи (§10). Если ставка
                  просто изменилась, нужно переутверждение, а не удаление.
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel render={<Button variant="outline">Отмена</Button>} />
            <AlertDialogAction
              render={
                <Button
                  variant="destructive"
                  onClick={() => {
                    if (toDelete) remove.mutate(toDelete.id);
                    setToDelete(null);
                  }}
                >
                  Удалить
                </Button>
              }
            />
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <RateStandardFormDialog open={formOpen} onOpenChange={setFormOpen} />
      <ReapproveDialog
        standard={reapproving}
        onOpenChange={(open) => !open && setReapproving(null)}
      />
    </div>
  );
}
