import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Pencil, Plus, Trash2 } from "lucide-react";

import { RoundDeleteDialog } from "@/components/tenders/RoundDeleteDialog";
import { RoundUploadPanel } from "@/components/tenders/RoundUploadPanel";
import { BaselineStatus } from "@/components/tenders/BaselineStatus";
import { OfferGrid } from "@/components/tenders/OfferGrid";
import { TenderFormDialog } from "@/components/tenders/TenderFormDialog";
import { Breadcrumbs } from "@/components/ui-domain/Breadcrumbs";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { StatusPill } from "@/components/ui-domain/StatusPill";
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useCurrentUser } from "@/hooks/useAuth";
import { formatDate } from "@/lib/format";
import { useCreateRound, useDeleteTender, useRoundImportJobs, useTender } from "@/services/queries";
import type { RoundImportJob, TenderCard, TenderRoundRow } from "@/types/domain";

/**
 * Карточка тендера (спека §2.13, §2.14): реквизиты, решётка «участник × раунд»
 * и панель выбранного этапа — загрузка, статус расчётной стоимости и история
 * импорта.
 *
 * Выбранный раунд живёт в `?round=` (спека §2.14): по умолчанию — последний по
 * `stage_no`, потому что это этап, который обычно интересует сразу после
 * открытия карточки. Смена этапа переписывает адрес — ссылку на конкретный
 * этап можно передать коллеге.
 */
export default function TenderCardPage() {
  const { tenderId } = useParams();
  const id = tenderId ? Number(tenderId) : undefined;
  const [params, setParams] = useSearchParams();
  const { data: user } = useCurrentUser();
  const isAdmin = user?.role === "admin";
  const navigate = useNavigate();

  const [editOpen, setEditOpen] = useState(false);
  const [roundFormOpen, setRoundFormOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [roundToDelete, setRoundToDelete] = useState<TenderRoundRow | null>(null);

  const cardQ = useTender(id);
  const card = cardQ.data;

  const rounds = card?.rounds ?? [];
  // `rounds.length + 1` коллизирует, если удалён средний раунд (этапы [1, 3] →
  // формула снова предложит 3, сервер откажет 409, и диалог зациклится: ручного
  // ввода номера этапа у него нет). Берём максимум по факту, а не по счётчику.
  const nextStageNo = rounds.length === 0 ? 1 : Math.max(...rounds.map((r) => r.stage_no)) + 1;
  const fromUrl = params.get("round") ? Number(params.get("round")) : undefined;
  const selected = rounds.find((r) => r.id === fromUrl) ?? rounds.at(-1);
  const hasEstimates = selected
    ? (card?.cells.some((c) => c.round_id === selected.id && c.estimate_id !== null) ?? false)
    : false;

  const jobsQ = useRoundImportJobs(card?.id, selected?.id);

  function selectRound(roundId: number) {
    const next = new URLSearchParams(params);
    next.set("round", String(roundId));
    setParams(next);
  }

  if (cardQ.isPending) {
    return (
      <div className="container-page py-8">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="mt-4 h-40 w-full" />
      </div>
    );
  }

  if (cardQ.isError || !card) {
    return (
      <div className="container-page py-8">
        <EmptyState
          title="Тендер не найден"
          description="Возможно, он удалён."
          action={<Button variant="outline" render={<Link to="/tenders">К списку тендеров</Link>} />}
        />
      </div>
    );
  }

  return (
    <div className="container-page py-8">
      <Breadcrumbs items={[{ label: "Тендеры", to: "/tenders" }, { label: card.tender_number }]} />

      <div className="mt-4">
        <PageHeader
          serif
          title={card.tender_number}
          subtitle={`${card.title} · ${card.object_title}`}
          actions={
            isAdmin && (
              <>
                <Button variant="outline" onClick={() => setEditOpen(true)}>
                  <Pencil className="size-4" /> Правка
                </Button>
                <Button variant="outline" onClick={() => setRoundFormOpen(true)}>
                  <Plus className="size-4" /> Новый этап
                </Button>
                <Button variant="outline" onClick={() => setDeleteOpen(true)}>
                  <Trash2 className="size-4" /> Удалить
                </Button>
              </>
            )
          }
        />
      </div>

      <Surface className="mt-6">
        <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Класс объектов">
            <Badge variant="secondary">{card.rate_class_title}</Badge>
          </Field>
          <Field label="Объект">{card.object_title}</Field>
          {card.object_address && <Field label="Адрес объекта">{card.object_address}</Field>}
          {card.notes && <Field label="Примечания">{card.notes}</Field>}
        </dl>
      </Surface>

      <div className="mt-6">
        <OfferGrid card={card} selectedRoundId={selected?.id} onSelectRound={selectRound} />
      </div>

      {selected && (
        <Surface className="mt-6 grid gap-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-medium text-fg-secondary">
              Этап {selected.stage_no}
              {selected.label ? ` · ${selected.label}` : ""}
            </h2>
            {isAdmin && (
              <Button variant="outline" size="sm" onClick={() => setRoundToDelete(selected)}>
                <Trash2 className="size-3.5" /> Удалить этап
              </Button>
            )}
          </div>

          <BaselineStatus round={selected} hasEstimates={hasEstimates} />

          {/*
            `key={selected.id}` обязателен: смена `?round=` пересоздаёт панель
            целиком, иначе она продолжала бы опрашивать job ПРЕЖНЕГО раунда —
            комментарий с подробностями живёт в самой `RoundUploadPanel`.
          */}
          <RoundUploadPanel key={selected.id} tenderId={card.id} roundId={selected.id} round={selected} />

          <RoundImportHistory jobs={jobsQ.data} loading={jobsQ.isPending} />
        </Surface>
      )}

      <TenderFormDialog open={editOpen} onOpenChange={setEditOpen} tender={card} />
      {roundFormOpen && (
        <NewRoundDialog tenderId={card.id} nextStageNo={nextStageNo} onOpenChange={setRoundFormOpen} />
      )}
      <RoundDeleteDialog tenderId={card.id} round={roundToDelete} onOpenChange={() => setRoundToDelete(null)} />
      <TenderDeleteDialog
        tender={deleteOpen ? card : null}
        onOpenChange={() => setDeleteOpen(false)}
        onDeleted={() => navigate("/tenders")}
      />
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
 * История загрузок раунда (`useRoundImportJobs` — узкий `RoundImportJob[]`,
 * без приведения типов: владелец здесь известен запросу заранее, в отличие от
 * поллинга одного job'а в `RoundUploadPanel`, где владелец приходится
 * узнавать по контексту вызова).
 */
function RoundImportHistory({ jobs, loading }: { jobs: RoundImportJob[] | undefined; loading: boolean }) {
  if (loading) return <Skeleton className="h-24 w-full" />;
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
            <TableHead>Статус</TableHead>
            <TableHead>Создано</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {jobs.map((job) => (
            <TableRow key={job.id}>
              <TableCell className="tabular-nums">{job.id}</TableCell>
              <TableCell>
                <div className="max-w-xs truncate">{job.filename}</div>
              </TableCell>
              <TableCell>
                <StatusPill
                  tone={job.status === "done" ? "success" : job.status === "error" ? "danger" : "info"}
                  label={job.status}
                />
                {job.is_current && (
                  <Badge className="ml-2" variant="secondary">
                    актуальный
                  </Badge>
                )}
              </TableCell>
              <TableCell className="tabular-nums">{formatDate(job.created_at)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Surface>
  );
}

/**
 * Новый этап: номер (`stage_no`) назначается автоматически — следующий по
 * порядку, без ручного ввода (иначе можно было бы завести дубликат или дыру
 * в нумерации, а сервер такой номер всё равно бы отверг).
 */
function NewRoundDialog({
  tenderId,
  nextStageNo,
  onOpenChange,
}: {
  tenderId: number;
  nextStageNo: number;
  onOpenChange: (open: boolean) => void;
}) {
  const [label, setLabel] = useState("");
  const [heldOn, setHeldOn] = useState("");
  const create = useCreateRound();

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    try {
      await create.mutateAsync({
        tenderId,
        input: { stage_no: nextStageNo, label: label.trim() || null, held_on: heldOn || null },
      });
      onOpenChange(false);
    } catch {
      // Причина уже в тосте (`toastApiError`); диалог оставляем открытым.
    }
  }

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Новый этап</DialogTitle>
          <DialogDescription>
            Этап {nextStageNo} — следующий по порядку; номер назначается автоматически.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="round-label">Название этапа</Label>
            <Input id="round-label" value={label} onChange={(e) => setLabel(e.target.value)} />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="round-held-on">Дата проведения</Label>
            <Input
              id="round-held-on"
              type="date"
              value={heldOn}
              onChange={(e) => setHeldOn(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Отмена
            </Button>
            <Button type="submit" disabled={create.isPending}>
              Создать этап
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Удаление тендера целиком — тем же приёмом, что `ContractDeleteDialog`:
 * кнопка разблокируется только точным вводом номера тендера, операция
 * необратима.
 */
function TenderDeleteDialog({
  tender,
  onOpenChange,
  onDeleted,
}: {
  tender: TenderCard | null;
  onOpenChange: (open: boolean) => void;
  onDeleted?: () => void;
}) {
  const [typed, setTyped] = useState("");
  const remove = useDeleteTender();

  // Сброс поля при смене тендера — БЕЗ `useEffect`, тем же приёмом, что
  // `ContractDeleteDialog` (react-hooks/set-state-in-effect запрещает именно
  // подгонку состояния под проп в эффекте; этот путь — синхронная корректировка
  // в теле рендера, официально документированный паттерн).
  const [renderedTenderId, setRenderedTenderId] = useState(tender?.id);
  if (renderedTenderId !== tender?.id) {
    setRenderedTenderId(tender?.id);
    setTyped("");
  }

  const confirmed = tender !== null && typed === tender.tender_number;

  return (
    <AlertDialog open={tender !== null} onOpenChange={(open) => !open && onOpenChange(false)}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Удалить тендер «{tender?.tender_number}»?</AlertDialogTitle>
          <AlertDialogDescription>
            Вместе с тендером будут удалены все этапы, сметы участников, история загрузок и
            файлы. Действие необратимо: восстановить их из приложения будет нельзя.
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="space-y-2">
          <Label htmlFor="confirm-tender-number">Введите номер тендера, чтобы подтвердить</Label>
          <Input
            id="confirm-tender-number"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            autoComplete="off"
          />
        </div>

        <AlertDialogFooter>
          <AlertDialogCancel render={<Button variant="outline">Отмена</Button>} />
          <AlertDialogAction
            render={
              <Button
                variant="destructive"
                disabled={!confirmed || remove.isPending}
                onClick={() => {
                  if (!tender || !confirmed) return;
                  remove.mutate(tender.id, {
                    onSuccess: () => {
                      onOpenChange(false);
                      onDeleted?.();
                    },
                  });
                }}
              >
                Удалить тендер
              </Button>
            }
          />
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
