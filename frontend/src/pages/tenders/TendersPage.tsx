import { useState } from "react";
import { Link } from "react-router-dom";
import { Gavel, Search } from "lucide-react";

import { Pager } from "@/components/domain/Pager";
import { TenderFormDialog } from "@/components/tenders/TenderFormDialog";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Surface } from "@/components/ui-domain/Surface";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useCurrentUser } from "@/hooks/useAuth";
import { useDebounce } from "@/lib/useDebounce";
import { useTenders } from "@/services/queries";

const PAGE_SIZE = 20;

/**
 * Экран «Тендеры», список (спека §2.13) — по образцу `ContractsPage`.
 *
 * Заведение тендера — право `admin` (то же решение §6.2, что у договоров):
 * сервер отвечает 403 независимо от того, что нарисовано на экране, поэтому у
 * `member` кнопки нет вовсе, а не задизейблена.
 */
export default function TendersPage() {
  const { data: user } = useCurrentUser();
  const isAdmin = user?.role === "admin";

  const [searchInput, setSearchInput] = useState("");
  const [page, setPage] = useState(1);
  const [formOpen, setFormOpen] = useState(false);

  const search = useDebounce(searchInput, 300);
  const tendersQ = useTenders({ q: search || undefined, page, page_size: PAGE_SIZE });
  const data = tendersQ.data;

  return (
    <div className="container-page py-8">
      <PageHeader
        serif
        title="Тендеры"
        subtitle={data ? `Всего: ${data.total}` : "Тендеры, раунды и участники"}
        actions={
          isAdmin && (
            <Button onClick={() => setFormOpen(true)}>
              <Gavel className="size-4" /> Новый тендер
            </Button>
          )
        }
      />

      <div className="mt-6 flex flex-wrap items-center gap-3">
        <InputGroup className="max-w-sm flex-1">
          <InputGroupInput
            aria-label="Поиск тендеров"
            placeholder="Номер, предмет или объект"
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
      </div>

      <div className="mt-4">
        {tendersQ.isPending && (
          <Surface padding="none">
            <div className="space-y-3 p-4">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          </Surface>
        )}

        {tendersQ.isError && (
          <EmptyState title="Ошибка загрузки" description="Не удалось получить список тендеров." />
        )}

        {data && data.items.length === 0 && (
          <EmptyState
            title="Тендеров пока нет"
            description={
              isAdmin
                ? "Заведите тендер — после этого можно будет добавить раунды и участников."
                : "Тендеры заводит администратор."
            }
          />
        )}

        {data && data.items.length > 0 && (
          <>
            <Surface padding="none" className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Номер</TableHead>
                    <TableHead>Предмет</TableHead>
                    <TableHead>Объект</TableHead>
                    <TableHead>Класс</TableHead>
                    <TableHead className="text-right">Раундов</TableHead>
                    <TableHead className="text-right">Участников</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.items.map((tender) => (
                    <TableRow key={tender.id}>
                      <TableCell className="font-medium">
                        <Link
                          to={`/tenders/${tender.id}`}
                          className="text-accent-text hover:underline"
                        >
                          {tender.tender_number}
                        </Link>
                      </TableCell>
                      <TableCell>{tender.title}</TableCell>
                      <TableCell>{tender.object_title}</TableCell>
                      <TableCell>
                        <Badge variant="secondary">{tender.rate_class_title}</Badge>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{tender.rounds_count}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {tender.participants_count}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Surface>

            <Pager page={page} total={data.total} pageSize={PAGE_SIZE} onPageChange={setPage} />
          </>
        )}
      </div>

      <TenderFormDialog open={formOpen} onOpenChange={setFormOpen} />
    </div>
  );
}
