import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Search, ChevronLeft, ChevronRight } from "lucide-react";

import { PageHeader } from "@/components/ui-domain/PageHeader";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Surface } from "@/components/ui-domain/Surface";
import { Button } from "@/components/ui-domain/Button";
import { OrgRoleBadge } from "@/components/admin/RoleBadges";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { useAdminUsers } from "@/services/queries";
import { useDebounce } from "@/lib/useDebounce";

const PAGE_SIZE = 20;

export default function AdminUsers() {
  const navigate = useNavigate();
  const [searchInput, setSearchInput] = useState("");
  const [page, setPage] = useState(1);
  const search = useDebounce(searchInput, 300);

  // При смене поиска возвращаемся на первую страницу
  const usersQ = useAdminUsers({ q: search || undefined, page, page_size: PAGE_SIZE });

  const data = usersQ.data;
  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  function handleSearchChange(value: string) {
    setSearchInput(value);
    setPage(1);
  }

  return (
    <div className="container-page py-8">
      <PageHeader
        serif
        title="Пользователи"
        subtitle={data ? `Всего: ${data.total}` : "Все пользователи платформы"}
      />

      <div className="mt-6">
        <InputGroup className="flex-1 max-w-xs">
          <InputGroupInput
            placeholder="Поиск по email или организации"
            value={searchInput}
            onChange={(e) => handleSearchChange(e.target.value)}
          />
          <InputGroupAddon align="inline-start">
            <Search size={13} />
          </InputGroupAddon>
        </InputGroup>
      </div>

      <div className="mt-4">
        {usersQ.isPending && (
          <Surface padding="none">
            <div className="space-y-3 p-4">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          </Surface>
        )}
        {usersQ.isError && <EmptyState title="Ошибка загрузки" description="Не удалось получить пользователей." />}
        {data && data.items.length === 0 && (
          <EmptyState title="Ничего не найдено" description="Уточните параметры поиска." />
        )}
        {data && data.items.length > 0 && (
          <>
            <Surface padding="none" className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Email</TableHead>
                    <TableHead>Организация</TableHead>
                    <TableHead>Роль</TableHead>
                    <TableHead>Статус</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.items.map((u) => (
                    <TableRow
                      key={u.id}
                      role={u.org_id ? "button" : undefined}
                      tabIndex={u.org_id ? 0 : undefined}
                      className={u.org_id ? "cursor-pointer hover:bg-surface-hover" : undefined}
                      onClick={() => u.org_id && navigate(`/admin/organizations/${u.org_id}`)}
                      onKeyDown={(e) => {
                        if (u.org_id && (e.key === "Enter" || e.key === " ")) {
                          e.preventDefault();
                          navigate(`/admin/organizations/${u.org_id}`);
                        }
                      }}
                    >
                      <TableCell className="font-medium text-fg">
                        {u.email}
                        {u.is_superuser && (
                          <span className="ml-2 rounded-full bg-accent/15 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-accent">
                            суперюзер
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="text-fg-secondary">{u.org_name ?? "—"}</TableCell>
                      <TableCell>
                        <OrgRoleBadge role={u.org_role} />
                      </TableCell>
                      <TableCell>
                        {u.is_active ? (
                          <span className="text-xs text-info-text">Активен</span>
                        ) : (
                          <span className="text-xs text-fg-tertiary">Деактивирован</span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Surface>

            {totalPages > 1 && (
              <div className="mt-4 flex items-center justify-center gap-3">
                <Button
                  variant="secondary"
                  size="sm"
                  leftIcon={<ChevronLeft size={14} />}
                  disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                >
                  Назад
                </Button>
                <span className="text-sm text-fg-secondary tabular-nums">
                  {page} / {totalPages}
                </span>
                <Button
                  variant="secondary"
                  size="sm"
                  rightIcon={<ChevronRight size={14} />}
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                >
                  Вперёд
                </Button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
