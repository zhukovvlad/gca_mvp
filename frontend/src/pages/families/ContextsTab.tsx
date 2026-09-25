import { useState } from "react";
import { Search } from "lucide-react";

import { Pager } from "@/components/domain/Pager";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Surface } from "@/components/ui-domain/Surface";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
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
import { cn } from "@/lib/utils";
import { useDebounce } from "@/lib/useDebounce";
import { useSemanticContexts } from "@/services/queries";
import type { NameRole, SemanticKind, SemanticState } from "@/types/domain";

const PAGE_SIZE = 20;
const ANY = "any";
const KIND_OPTIONS: SemanticKind[] = ["WORK", "SYSTEM", "UNKNOWN"];
const ROLE_OPTIONS: NameRole[] = ["WORK", "LOCATION_ONLY", "GENERIC_WORK"];
const STATE_OPTIONS: SemanticState[] = ["SUGGESTED", "CONFIRMED", "NOT_APPLICABLE"];

interface ContextsTabProps {
  selectedContextId: number | null;
  onSelect: (contextId: number) => void;
}

/**
 * Очередь контекстов — вкладка «Контексты» (спека §2.10). Три
 * фильтра-признака (`has_stale_members`, `has_conflicting_members`,
 * `has_no_members`) — три отдельных чекбокса, а не один общий: это разные
 * оси (§2.5, §2.8) и вход с обоими сразу обязан проходить оба фильтра.
 */
export function ContextsTab({ selectedContextId, onSelect }: ContextsTabProps) {
  const [searchInput, setSearchInput] = useState("");
  const [categoryInput, setCategoryInput] = useState("");
  const [kind, setKind] = useState<string>(ANY);
  const [role, setRole] = useState<string>(ANY);
  const [state, setState] = useState<string>(ANY);
  const [hasStale, setHasStale] = useState(false);
  const [hasConflicting, setHasConflicting] = useState(false);
  const [hasNoMembers, setHasNoMembers] = useState(false);
  const [page, setPage] = useState(1);

  const search = useDebounce(searchInput, 300);
  const categoryId = useDebounce(categoryInput, 300);

  const contextsQ = useSemanticContexts({
    catalog_query: search || undefined,
    work_category_id: categoryId ? Number(categoryId) : undefined,
    semantic_kind: kind === ANY ? undefined : (kind as SemanticKind),
    name_role: role === ANY ? undefined : (role as NameRole),
    semantic_state: state === ANY ? undefined : (state as SemanticState),
    has_stale_members: hasStale || undefined,
    has_conflicting_members: hasConflicting || undefined,
    has_no_members: hasNoMembers || undefined,
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
  });

  const data = contextsQ.data;

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <InputGroup className="max-w-sm flex-1">
          <InputGroupInput
            aria-label="Поиск по написанию каталога"
            placeholder="Написание в каталоге"
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

        <div className="grid gap-1">
          <Label htmlFor="filter-category" className="text-xs text-fg-tertiary">Статья (id)</Label>
          <Input
            id="filter-category"
            className="w-28"
            inputMode="numeric"
            value={categoryInput}
            onChange={(e) => {
              setCategoryInput(e.target.value);
              setPage(1);
            }}
          />
        </div>

        <div className="grid gap-1">
          <Label htmlFor="filter-kind" className="text-xs text-fg-tertiary">Вид</Label>
          <Select value={kind} onValueChange={(v) => { setKind(v ?? ANY); setPage(1); }}>
            <SelectTrigger id="filter-kind" className="w-40">
              <SelectValue>{(raw) => (!raw || raw === ANY ? "Любой вид" : raw)}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Любой вид</SelectItem>
              {KIND_OPTIONS.map((k) => (
                <SelectItem key={k} value={k}>{k}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="grid gap-1">
          <Label htmlFor="filter-role" className="text-xs text-fg-tertiary">Роль имени</Label>
          <Select value={role} onValueChange={(v) => { setRole(v ?? ANY); setPage(1); }}>
            <SelectTrigger id="filter-role" className="w-44">
              <SelectValue>{(raw) => (!raw || raw === ANY ? "Любая роль" : raw)}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Любая роль</SelectItem>
              {ROLE_OPTIONS.map((r) => (
                <SelectItem key={r} value={r}>{r}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="grid gap-1">
          <Label htmlFor="filter-state" className="text-xs text-fg-tertiary">Состояние</Label>
          <Select value={state} onValueChange={(v) => { setState(v ?? ANY); setPage(1); }}>
            <SelectTrigger id="filter-state" className="w-48">
              <SelectValue>{(raw) => (!raw || raw === ANY ? "Любое состояние" : raw)}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Любое состояние</SelectItem>
              {STATE_OPTIONS.map((s) => (
                <SelectItem key={s} value={s}>{s}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex items-center gap-2">
          <Checkbox
            id="filter-stale"
            checked={hasStale}
            onCheckedChange={(checked) => { setHasStale(checked === true); setPage(1); }}
          />
          <Label htmlFor="filter-stale" className="text-sm font-normal">Есть устаревшие членства</Label>
        </div>

        <div className="flex items-center gap-2">
          <Checkbox
            id="filter-conflicting"
            checked={hasConflicting}
            onCheckedChange={(checked) => { setHasConflicting(checked === true); setPage(1); }}
          />
          <Label htmlFor="filter-conflicting" className="text-sm font-normal">Есть конфликтные членства</Label>
        </div>

        <div className="flex items-center gap-2">
          <Checkbox
            id="filter-no-members"
            checked={hasNoMembers}
            onCheckedChange={(checked) => { setHasNoMembers(checked === true); setPage(1); }}
          />
          <Label htmlFor="filter-no-members" className="text-sm font-normal">Нет членств</Label>
        </div>
      </div>

      {contextsQ.isPending && <Skeleton className="h-40 w-full" />}

      {contextsQ.isError && (
        <EmptyState title="Ошибка загрузки" description="Не удалось получить контексты." />
      )}

      {data && data.items.length === 0 && (
        <EmptyState
          title="Контекстов нет"
          description="Ни один контекст не подходит под текущие фильтры."
        />
      )}

      {data && data.items.length > 0 && (
        <>
          <Surface padding="none" className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Написание</TableHead>
                  <TableHead>Статья</TableHead>
                  <TableHead>Вид</TableHead>
                  <TableHead>Роль имени</TableHead>
                  <TableHead>Состояние</TableHead>
                  <TableHead>Семья</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.items.map((row) => {
                  const familyCaption =
                    row.work_family_id !== null
                      ? row.family_title ?? `Семья #${row.work_family_id}`
                      : row.comparability_reason === "insufficient_description"
                        ? "семья не назначена, потому что состав не описан"
                        : "нет семьи";
                  return (
                    <TableRow
                      key={row.id}
                      role="row"
                      aria-selected={row.id === selectedContextId}
                      onClick={() => onSelect(row.id)}
                      className={cn(
                        "cursor-pointer",
                        row.id === selectedContextId && "bg-surface-hover"
                      )}
                    >
                      <TableCell>
                        <div className="font-medium text-fg">{row.standard_job_title}</div>
                        {row.unit_code && (
                          <span className="text-xs text-fg-tertiary">{row.unit_code}</span>
                        )}
                        {row.archived_at && (
                          <Badge variant="outline" className="ml-2">архивный</Badge>
                        )}
                      </TableCell>
                      <TableCell>{row.work_category_title ?? "—"}</TableCell>
                      <TableCell>
                        <Badge variant="outline">{row.semantic_kind}</Badge>
                      </TableCell>
                      <TableCell>{row.name_role}</TableCell>
                      <TableCell>{row.semantic_state}</TableCell>
                      <TableCell>{familyCaption}</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </Surface>

          <Pager page={page} total={data.total} pageSize={PAGE_SIZE} onPageChange={setPage} />
        </>
      )}
    </div>
  );
}
