import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, Search, Building2 } from "lucide-react";

import { PageHeader } from "@/components/ui-domain/PageHeader";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Surface } from "@/components/ui-domain/Surface";
import { Button } from "@/components/ui-domain/Button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { OrgKindBadge } from "@/components/admin/RoleBadges";
import { useAdminOrganizations } from "@/services/queries";
import { formatNumber, formatDate } from "@/lib/format";

export default function AdminOrganizations() {
  const navigate = useNavigate();
  const orgsQ = useAdminOrganizations();
  const [search, setSearch] = useState("");

  const orgs = useMemo(() => orgsQ.data ?? [], [orgsQ.data]);

  const filtered = useMemo(() => {
    if (!search.trim()) return orgs;
    const q = search.trim().toLowerCase();
    return orgs.filter(
      (o) => o.name.toLowerCase().includes(q) || (o.inn ?? "").includes(q),
    );
  }, [orgs, search]);

  return (
    <div className="container-page py-8">
      <PageHeader
        serif
        title="Организации"
        subtitle={
          orgsQ.isSuccess && orgs.length > 0
            ? `Всего: ${orgs.length}`
            : "Управление организациями платформы"
        }
        actions={
          <Button leftIcon={<Plus size={14} />} onClick={() => navigate("/admin/organizations/new")}>
            Создать организацию
          </Button>
        }
      />

      <div className="mt-6">
        <InputGroup className="flex-1 max-w-xs">
          <InputGroupInput
            placeholder="Поиск по названию или ИНН"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <InputGroupAddon align="inline-start">
            <Search size={13} />
          </InputGroupAddon>
        </InputGroup>
      </div>

      <div className="mt-4">
        {orgsQ.isPending && <TableSkeleton />}
        {orgsQ.isError && (
          <EmptyState title="Ошибка загрузки" description="Не удалось получить список организаций." />
        )}
        {orgsQ.isSuccess && orgs.length === 0 && (
          <EmptyState
            icon={<Building2 size={18} />}
            title="Организаций пока нет"
            description="Создайте первую организацию и её администратора."
            action={
              <Button leftIcon={<Plus size={14} />} onClick={() => navigate("/admin/organizations/new")}>
                Создать организацию
              </Button>
            }
          />
        )}
        {orgsQ.isSuccess && orgs.length > 0 && filtered.length === 0 && (
          <EmptyState title="Ничего не найдено" description="Уточните параметры поиска." />
        )}
        {orgsQ.isSuccess && filtered.length > 0 && (
          <Surface padding="none" className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Организация</TableHead>
                  <TableHead>Роль</TableHead>
                  <TableHead className="text-right">Пользователей</TableHead>
                  <TableHead className="text-right">Проектов</TableHead>
                  <TableHead>Создана</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((o) => (
                  <TableRow
                    key={o.id}
                    role="button"
                    tabIndex={0}
                    className="cursor-pointer hover:bg-surface-hover"
                    onClick={() => navigate(`/admin/organizations/${o.id}`)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        navigate(`/admin/organizations/${o.id}`);
                      }
                    }}
                  >
                    <TableCell>
                      <span className="font-medium text-fg">{o.name}</span>
                      {o.inn && <div className="mt-0.5 text-xs text-fg-tertiary">ИНН {o.inn}</div>}
                    </TableCell>
                    <TableCell>
                      <OrgKindBadge kind={o.kind} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-fg-secondary">
                      {formatNumber(o.user_count)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-fg-secondary">
                      {formatNumber(o.project_count)}
                    </TableCell>
                    <TableCell className="text-fg-tertiary">{formatDate(o.created_at)}</TableCell>
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

function TableSkeleton() {
  return (
    <Surface padding="none">
      <div className="space-y-3 p-4">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    </Surface>
  );
}
