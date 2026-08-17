import { useState } from "react";
import { Link } from "react-router-dom";
import { FilePlus2, MoreHorizontal, Search, Trash2 } from "lucide-react";

import { Pager } from "@/components/domain/Pager";
import { ContractDeleteDialog } from "@/components/contracts/ContractDeleteDialog";
import { ContractFormDialog } from "@/components/contracts/ContractFormDialog";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { MoneyCell } from "@/components/ui-domain/MoneyCell";
import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Surface } from "@/components/ui-domain/Surface";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
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
import { useCurrentUser } from "@/hooks/useAuth";
import type { ContractRow } from "@/types/domain";
import { formatDate } from "@/lib/format";
import { useDebounce } from "@/lib/useDebounce";
import { useContracts, useRateClasses } from "@/services/queries";

const PAGE_SIZE = 20;
const ALL_CLASSES = "all";

/**
 * Экран «Договоры/Объекты», список (AGENTS.md §7.1).
 *
 * Поиск идёт по номеру, названию договора, объекту и подрядчику — человек ищет по
 * тому, что помнит, а помнит он обычно объект.
 *
 * Заведение договора — право `admin` (решение §6.2), поэтому у `member` кнопки нет.
 * Это не единственная защита: сервер отвечает 403 независимо от того, что
 * нарисовано на экране.
 *
 * Выбор договоров для сравнения (спека сравнения §2.6, §2.9) — ЧТЕНИЕ, поэтому
 * галочки и обе кнопки видны `admin` и `member` одинаково; это не то же
 * правило, что у «Новый договор» выше. Маршрут `/compare` заводит задача 8 —
 * здесь только собирается адрес.
 */
export default function ContractsPage() {
  const { data: user } = useCurrentUser();
  const isAdmin = user?.role === "admin";

  const [searchInput, setSearchInput] = useState("");
  const [rateClassId, setRateClassId] = useState<string>(ALL_CLASSES);
  const [page, setPage] = useState(1);
  const [formOpen, setFormOpen] = useState(false);
  const [toDelete, setToDelete] = useState<ContractRow | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  const search = useDebounce(searchInput, 300);
  const classesQ = useRateClasses();
  const contractsQ = useContracts({
    q: search || undefined,
    rate_class_id: rateClassId === ALL_CLASSES ? undefined : Number(rateClassId),
    page,
    page_size: PAGE_SIZE,
  });

  const data = contractsQ.data;

  function toggleSelected(id: number) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const compareSelectedHref = `/compare?ids=${[...selectedIds].join(",")}`;

  // Текущие фильтры экрана, а не полный контракт эндпоинта (спека §2.6): здесь
  // нет полей object_id/contractor_id, а `page`/`page_size` намеренно не
  // переносятся — сравнение берёт всю выборку, а не страницу списка.
  //
  // Значения берутся из состояния ВВОДА, а не из дебаунсенного `search`: адрес
  // несёт то, что человек НАБРАЛ, а не то, что список успел применить. Внутри
  // окна debounce (300 мс) это расходится: список ещё показывает прежнюю выборку,
  // а ссылка уже несёт новый `q`. Выбрано намеренно — клик сразу после ввода
  // должен сравнивать по набранному фильтру, а не по устаревшему. (Прежний
  // комментарий обосновывал этот же выбор доводом «адрес отражает видимое»,
  // который поддерживает ПРОТИВОПОЛОЖНОЕ, — замечание финального ревью.)
  //
  // Выбор галочками живёт по id и потому переживает смену фильтра и страницы:
  // «Сравнить выбранные (N)» может включать договоры, не видимые на экране.
  // Это тоже намеренно — выбор явный, и молча терять его при правке фильтра было
  // бы хуже; цена в том, что число на кнопке шире того, что видно.
  const compareAllParams = new URLSearchParams();
  if (searchInput.trim()) compareAllParams.set("q", searchInput.trim());
  if (rateClassId !== ALL_CLASSES) compareAllParams.set("rate_class_id", rateClassId);
  compareAllParams.set("all", "1");
  const compareAllHref = `/compare?${compareAllParams.toString()}`;

  return (
    <div className="container-page py-8">
      <PageHeader
        serif
        title="Договоры"
        subtitle={
          data ? `Всего: ${data.total}` : "Договоры генподряда, объекты и загруженные сметы"
        }
        actions={
          isAdmin && (
            <Button onClick={() => setFormOpen(true)}>
              <FilePlus2 className="size-4" /> Новый договор
            </Button>
          )
        }
      />

      <div className="mt-6 flex flex-wrap items-center gap-3">
        <InputGroup className="max-w-sm flex-1">
          <InputGroupInput
            aria-label="Поиск договоров"
            placeholder="Номер, объект или подрядчик"
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
                  : (classesQ.data?.find((c) => String(c.id) === raw)?.title ?? "Все классы")
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
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        {selectedIds.size > 0 ? (
          <Button variant="outline" render={<Link to={compareSelectedHref} />}>
            Сравнить выбранные ({selectedIds.size})
          </Button>
        ) : (
          <Button variant="outline" disabled>
            Сравнить выбранные (0)
          </Button>
        )}
        <Button variant="outline" render={<Link to={compareAllHref} />}>
          Сравнить всё по фильтру
        </Button>
      </div>

      <div className="mt-4">
        {contractsQ.isPending && (
          <Surface padding="none">
            <div className="space-y-3 p-4">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          </Surface>
        )}

        {contractsQ.isError && (
          <EmptyState title="Ошибка загрузки" description="Не удалось получить список договоров." />
        )}

        {data && data.items.length === 0 && (
          <EmptyState
            title="Договоров нет"
            description={
              isAdmin
                ? "Заведите договор — после этого к нему можно будет загрузить смету."
                : "Договоры заводит администратор."
            }
          />
        )}

        {data && data.items.length > 0 && (
          <>
            <Surface padding="none" className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-10" />
                    <TableHead>Номер</TableHead>
                    <TableHead>Объект</TableHead>
                    <TableHead>Подрядчик</TableHead>
                    <TableHead>Класс</TableHead>
                    <TableHead>Подписан</TableHead>
                    <TableHead className="text-right">Сумма</TableHead>
                    <TableHead className="text-right">Сметы</TableHead>
                    <TableHead className="w-12" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.items.map((contract) => (
                    <TableRow key={contract.id}>
                      <TableCell>
                        <Checkbox
                          aria-label={`Выбрать «${contract.contract_number}»`}
                          checked={selectedIds.has(contract.id)}
                          onCheckedChange={() => toggleSelected(contract.id)}
                        />
                      </TableCell>
                      <TableCell className="font-medium">
                        <Link
                          to={`/contracts/${contract.id}`}
                          className="text-accent-text hover:underline"
                        >
                          {contract.contract_number}
                        </Link>
                      </TableCell>
                      <TableCell>{contract.object_title}</TableCell>
                      <TableCell>{contract.contractor_title}</TableCell>
                      <TableCell>
                        <Badge variant="secondary">{contract.rate_class_title}</Badge>
                      </TableCell>
                      <TableCell className="tabular-nums">
                        {formatDate(contract.signed_date)}
                      </TableCell>
                      <TableCell className="text-right">
                        <MoneyCell value={contract.total_amount} />
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {contract.estimates_count}
                      </TableCell>
                      <TableCell className="text-right">
                        {isAdmin && (
                          <DropdownMenu>
                            {/*
                              Триггер принимает СВОИ пропсы и children (base-ui
                              `MenuPrimitive.Trigger`), а не `render`/`asChild` —
                              образец живого использования в проекте:
                              `components/layout/TopNav.tsx:79`.
                            */}
                            <DropdownMenuTrigger
                              type="button"
                              aria-label="Действия с договором"
                              className="inline-flex size-8 items-center justify-center rounded-md hover:bg-surface-hover"
                            >
                              <MoreHorizontal className="size-4" />
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              <DropdownMenuItem
                                variant="destructive"
                                onClick={() => setToDelete(contract)}
                              >
                                <Trash2 className="size-4" /> Удалить
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
                        )}
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

      <ContractFormDialog open={formOpen} onOpenChange={setFormOpen} />
      <ContractDeleteDialog contract={toDelete} onOpenChange={() => setToDelete(null)} />
    </div>
  );
}
