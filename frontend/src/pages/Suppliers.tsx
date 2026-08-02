import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { PageHeader } from "@/components/ui-domain/PageHeader";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Surface } from "@/components/ui-domain/Surface";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "@/components/ui/input-group";
import { Search } from "lucide-react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useSuppliers } from "@/services/queries";
import { formatMoney, formatNumber, pluralRu } from "@/lib/format";
import type { SupplierListItem } from "@/types/supplier";

// Бейдж «Новый» — если первый счёт появился менее 30 дней назад
const NEW_SUPPLIER_DAYS = 30;

function isNew(firstInvoiceDate: string | null): boolean {
  if (!firstInvoiceDate) return false;
  const d = new Date(firstInvoiceDate);
  if (Number.isNaN(d.getTime())) return false;
  const diffMs = Date.now() - d.getTime();
  return diffMs >= 0 && diffMs < NEW_SUPPLIER_DAYS * 86_400_000;
}

type SortKey = "turnover" | "invoice_count" | "name";

function sortSuppliers(items: SupplierListItem[], key: SortKey): SupplierListItem[] {
  return [...items].sort((a, b) => {
    if (key === "name") return a.name.localeCompare(b.name, "ru");
    if (key === "invoice_count") return b.invoice_count - a.invoice_count;
    return b.turnover - a.turnover;
  });
}

export default function Suppliers() {
  const navigate = useNavigate();
  const suppliersQ = useSuppliers();

  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState<string>("__all__");
  const [sort, setSort] = useState<SortKey>("turnover");

  const suppliers = useMemo(() => suppliersQ.data ?? [], [suppliersQ.data]);

  // Все уникальные категории для фильтра
  const allCategories = useMemo(() => {
    const set = new Set<string>();
    for (const s of suppliers) for (const c of s.categories) set.add(c);
    return Array.from(set).sort((a, b) => a.localeCompare(b, "ru"));
  }, [suppliers]);

  const filtered = useMemo(() => {
    let result = suppliers;
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      result = result.filter(
        (s) =>
          s.name.toLowerCase().includes(q) ||
          (s.inn ?? "").includes(q),
      );
    }
    if (categoryFilter !== "__all__") {
      result = result.filter((s) => s.categories.includes(categoryFilter));
    }
    return sortSuppliers(result, sort);
  }, [suppliers, search, categoryFilter, sort]);

  const totalTurnover = filtered.reduce((s, r) => s + r.turnover, 0);
  const totalInvoices = filtered.reduce((s, r) => s + r.invoice_count, 0);

  return (
    <div className="container-page py-8">
      <PageHeader
        serif
        title="Поставщики"
        subtitle={
          suppliersQ.isSuccess && suppliers.length > 0
            ? `${suppliers.length} поставщик${pluralRu(suppliers.length)} · общий оборот ${formatMoney(suppliers.reduce((s, r) => s + r.turnover, 0))}`
            : "Компании, с которыми работает портфель"
        }
      />

      <div className="mt-6 flex gap-2">
        <InputGroup className="flex-1">
          <InputGroupInput
            placeholder="Поиск по названию или ИНН"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <InputGroupAddon align="inline-start">
            <Search size={13} />
          </InputGroupAddon>
        </InputGroup>
        <Select value={categoryFilter} onValueChange={(v) => setCategoryFilter(v ?? "__all__")}>
          <SelectTrigger className="w-44">
            <SelectValue>
              {categoryFilter === "__all__" ? "Все категории" : categoryFilter}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">Все категории</SelectItem>
            {allCategories.map((c) => (
              <SelectItem key={c} value={c}>
                {c}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={sort} onValueChange={(v) => { if (v) setSort(v as SortKey); }}>
          <SelectTrigger className="w-44">
            <SelectValue>
              {sort === "turnover" ? "По обороту ↓" : sort === "invoice_count" ? "По числу счетов ↓" : "По названию А–Я"}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="turnover">По обороту ↓</SelectItem>
            <SelectItem value="invoice_count">По числу счетов ↓</SelectItem>
            <SelectItem value="name">По названию А–Я</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div className="mt-4">
        {suppliersQ.isPending && <SupplierTableSkeleton />}
        {suppliersQ.isError && (
          <EmptyState title="Ошибка загрузки" description="Не удалось получить список поставщиков. Попробуйте обновить страницу." />
        )}
        {suppliersQ.isSuccess && suppliers.length === 0 && (
          <EmptyState
            title="Поставщики не найдены"
            description="Загрузите первые счёт-фактуры, чтобы данные по поставщикам появились в системе."
          />
        )}
        {suppliersQ.isSuccess && suppliers.length > 0 && filtered.length === 0 && (
          <EmptyState title="Ничего не найдено" description="Уточните параметры поиска или фильтра." />
        )}
        {suppliersQ.isSuccess && filtered.length > 0 && (
          <Surface padding="none" className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Поставщик</TableHead>
                  <TableHead className="text-right">Оборот</TableHead>
                  <TableHead className="text-right">Объектов</TableHead>
                  <TableHead className="text-right">Счетов</TableHead>
                  <TableHead className="w-8" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((s) => (
                  <TableRow
                    key={s.id}
                    role="button"
                    tabIndex={0}
                    className="cursor-pointer hover:bg-surface-hover"
                    onClick={() => navigate(`/suppliers/${s.id}`)}
                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); navigate(`/suppliers/${s.id}`); } }}
                  >
                    <TableCell>
                      <div className="flex items-baseline gap-2">
                        <span className="font-medium text-fg">{s.name}</span>
                        {isNew(s.first_invoice_date) && (
                          <span className="rounded-full bg-accent/15 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-accent">
                            Новый
                          </span>
                        )}
                      </div>
                      <div className="mt-0.5 text-xs text-fg-tertiary">
                        {[s.categories.join(" · "), s.inn ? `ИНН ${s.inn}` : null]
                          .filter(Boolean)
                          .join(" · ")}
                      </div>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatMoney(s.turnover)}
                    </TableCell>
                    <TableCell className="text-right text-fg-secondary tabular-nums">
                      {formatNumber(s.project_count)}
                    </TableCell>
                    <TableCell className="text-right text-fg-secondary tabular-nums">
                      {formatNumber(s.invoice_count)}
                    </TableCell>
                    <TableCell className="text-fg-tertiary">
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="m9 18 6-6-6-6" />
                      </svg>
                    </TableCell>
                  </TableRow>
                ))}

                {/* Итого */}
                <TableRow className="border-t border-border-default bg-surface-sunken font-medium">
                  <TableCell className="text-xs uppercase tracking-wide text-fg-secondary">
                    Итого
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-fg">
                    {formatMoney(totalTurnover)}
                  </TableCell>
                  <TableCell className="text-right text-fg-tertiary">—</TableCell>
                  <TableCell className="text-right tabular-nums text-fg">
                    {formatNumber(totalInvoices)}
                  </TableCell>
                  <TableCell />
                </TableRow>
              </TableBody>
            </Table>
          </Surface>
        )}
      </div>
    </div>
  );
}

function SupplierTableSkeleton() {
  return (
    <Surface padding="none" className="overflow-x-auto">
      <div className="p-4 space-y-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    </Surface>
  );
}

