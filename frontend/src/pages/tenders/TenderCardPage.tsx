import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Download, Pencil, Plus, Trash2 } from "lucide-react";

import { RoundDeleteDialog } from "@/components/tenders/RoundDeleteDialog";
import { RoundUploadPanel } from "@/components/tenders/RoundUploadPanel";
import { BaselineStatus } from "@/components/tenders/BaselineStatus";
import { OfferGrid } from "@/components/tenders/OfferGrid";
import { TenderFormDialog } from "@/components/tenders/TenderFormDialog";
import { UnallocatedSheet } from "@/components/tenders/UnallocatedSheet";
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
import {
  useCreateRound,
  useDeleteTender,
  useRoundImportJobs,
  useTender,
  useTenderChangesExport,
} from "@/services/queries";
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
  // Выбор предложений для свода по этапам (спека свода §2.1, задача 7):
  // сметы РОВНО одного участника, отсюда и `Set` — порядок выбора неважен.
  const [selectedOfferIds, setSelectedOfferIds] = useState<ReadonlySet<number>>(new Set());

  const cardQ = useTender(id);
  const card = cardQ.data;

  // Сверка выбора с картой при каждом её обновлении (находка финального
  // ревью, fix round 3): выбор — это id предложений в состоянии СТРАНИЦЫ, и
  // ничто раньше не сверяло их с перезагруженной картой. Перезалив файла
  // раунда меняет offer_id/estimate_id его ячеек; id из выбора, который
  // больше не называет ячейку с обоими полями разом, отовсюду исчез — и
  // участник по нему больше не находится (`selectedParticipant` ниже вернул
  // бы `undefined`), из-за чего КАЖДАЯ плитка на решётke вычисляла бы себя
  // «чужой» и решётка блокировалась целиком, а кнопка свода вела бы на адрес,
  // который сервер отказал бы кодом «у предложения нет сметы».
  //
  // Снимаются ТОЛЬКО устаревшие id, не весь выбор — так второй, ещё живой
  // выбор того же участника не пропадает зря. Приём — синхронная
  // корректировка состояния в теле рендера (тот же, что у `TenderDeleteDialog`
  // ниже, где он подробно обоснован через `react-hooks/set-state-in-effect`),
  // а не `useEffect`: `card` — новый объект при каждом успешном рефетче
  // (react-query не сохраняет ссылку, если данные изменились), поэтому
  // сравнение с «последней увиденной» картой ловит именно момент обновления.
  const [reconciledCard, setReconciledCard] = useState(card);
  if (card !== reconciledCard) {
    setReconciledCard(card);
    const liveOfferIds = new Set(
      (card?.cells ?? [])
        .filter((c) => c.offer_id !== null && c.estimate_id !== null)
        .map((c) => c.offer_id as number)
    );
    setSelectedOfferIds((prev) => {
      const next = new Set([...prev].filter((id) => liveOfferIds.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }

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

  // Sheet этапного разноса «Нераспределённого» (спека этапного разноса §2.7):
  // параметр читается И ВАЛИДИРУЕТСЯ — принимается только раунд ТЕКУЩЕЙ
  // карточки, у которого счётчик НЕ `null`. Ноль — годное значение и раунд с
  // нулём принимается: снять ошибочное решение с полностью разнесённого этапа
  // больше неоткуда (§2.7). Чужой или устаревший id
  // не запускает запрос вовсе (`round` в GET-хук уходит `undefined`,
  // `enabled` гасит его). Открытие/закрытие правят ТОЛЬКО `?unallocated=` —
  // остальные параметры (`?round=` и любые будущие) копируются как есть.
  const unallocatedParam = params.get("unallocated");
  const unallocatedRound =
    unallocatedParam === null
      ? undefined
      : rounds.find((r) => r.id === Number(unallocatedParam) && r.unallocated_pending_sections !== null);

  function openUnallocated(roundId: number) {
    const next = new URLSearchParams(params);
    next.set("unallocated", String(roundId));
    setParams(next);
  }

  function closeUnallocated() {
    const next = new URLSearchParams(params);
    next.delete("unallocated");
    setParams(next);
  }

  // Находка ревью задачи 12 (D): фоновый рефетч карточки может обнулить
  // счётчик УЖЕ открытого раунда — его offer-сметы пропали (например, этап
  // остался без единого участника). Тогда `unallocatedRound` выше становится
  // `undefined`, и проп `open` у `UnallocatedSheet` падает в `false` КАК ПРОП
  // — у контролируемого диалога это не пользовательское закрытие, и колбэк
  // `onOpenChange` НЕ зовётся (симметричный путь — 404 «раунд/сметы больше
  // недоступны» — Sheet замыкает сам, см. `handleRoundGone` в
  // `UnallocatedSheet.tsx`; здесь это же самое, только причина обнаружена
  // снаружи, самим Sheet'ом не видна). Без этого эффекта мёртвый
  // `?unallocated=` остался бы в адресе навсегда.
  //
  // Эффект, а не правка состояния в теле рендера (тем приёмом, что
  // `reconciledCard`/`renderedTenderId` в этом файле): та правка годится для
  // СОБСТВЕННОГО состояния компонента, а `setParams` — это навигация роутера,
  // внешняя по отношению к текущему рендеру. По этому же образцу уже правит
  // адрес `ComparePage.tsx` (`target_month`/`single_rate`) — `useEffect` с
  // `{ replace: true }`, чтобы автокоррекция не плодила лишние записи истории.
  //
  // Не бороться с пользователем во время загрузки: `cardQ.isPending` гасит
  // эффект целиком — пока карточка не пришла, `rounds` пуст по построению
  // (`card?.rounds ?? []`), и `unallocatedRound` был бы `undefined` НЕ ПОТОМУ,
  // что раунд действительно недоступен, а потому, что карта ещё не загрузилась;
  // выйти раньше здесь обязательно, иначе первый же рендер с валидным
  // `?unallocated=` стёр бы параметр раньше, чем карточка успела бы его
  // подтвердить.
  useEffect(() => {
    if (cardQ.isPending) return;
    if (unallocatedParam !== null && unallocatedRound === undefined) {
      const next = new URLSearchParams(params);
      next.delete("unallocated");
      setParams(next, { replace: true });
    }
  }, [cardQ.isPending, unallocatedParam, unallocatedRound, params, setParams]);

  // Участник, чьи сметы сейчас выбраны — вычисляется из ячеек, а не хранится
  // отдельным полем: набор offer_id и есть источник истины, второй копии
  // состояния, с которой он мог бы разойтись, нет.
  const selectedParticipant = card?.participants.find((p) =>
    card.cells.some(
      (c) => c.offer_id !== null && selectedOfferIds.has(c.offer_id) && c.package_id === p.package_id
    )
  );

  function toggleOffer(offerId: number) {
    setSelectedOfferIds((prev) => {
      const next = new Set(prev);
      if (next.has(offerId)) next.delete(offerId);
      else next.add(offerId);
      return next;
    });
  }

  function selectParticipant(packageId: number) {
    const own = (card?.cells ?? [])
      .filter((c) => c.package_id === packageId && c.offer_id !== null && c.estimate_id !== null)
      .map((c) => c.offer_id as number);
    // Повторный клик по участнику, у которого уже выбраны все сметы, снимает
    // выбор целиком — тот же toggle, что у отдельной плитки, но пакетом.
    setSelectedOfferIds((prev) => (own.length > 0 && own.every((id) => prev.has(id)) ? new Set() : new Set(own)));
  }

  const summaryParams = new URLSearchParams();
  [...selectedOfferIds].sort((a, b) => a - b).forEach((offerId) => summaryParams.append("offers", String(offerId)));
  const summaryHref = `/tenders/${card?.id}/summary?${summaryParams.toString()}`;

  // Книга «Изменения КП» (спека 2026-09-16-tender-changes-export-design.md
  // §2.1, §2.11): собирается по ВСЕМ участникам тендера с двумя и более
  // сметами, поэтому кнопка НЕ зависит от `selectedOfferIds` — в отличие от
  // «Свода по этапам» выше, ей нечего выбирать на решётке.
  const changesExport = useTenderChangesExport();

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

      <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-fg-secondary">
          {selectedOfferIds.size > 0 && selectedParticipant
            ? `выбрано ${selectedOfferIds.size} ${estimateWordFor(selectedOfferIds.size)} · ${selectedParticipant.title}`
            : "Выберите этапы одного участника, чтобы собрать свод"}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          {selectedOfferIds.size >= 2 ? (
            <Button variant="outline" render={<Link to={summaryHref} />}>
              Свод по этапам ({selectedOfferIds.size})
            </Button>
          ) : (
            <Button variant="outline" disabled title="выберите хотя бы два этапа">
              Свод по этапам ({selectedOfferIds.size})
            </Button>
          )}
          <Button
            variant="outline"
            disabled={changesExport.isPending}
            onClick={() => changesExport.mutate(card.id)}
          >
            <Download className="size-4" />
            {changesExport.isPending ? "Готовлю файл…" : "Изменения КП"}
          </Button>
        </div>
      </div>

      <div className="mt-3">
        <OfferGrid
          card={card}
          selectedRoundId={selected?.id}
          onSelectRound={selectRound}
          selectedOfferIds={selectedOfferIds}
          onToggleOffer={toggleOffer}
          onSelectParticipant={selectParticipant}
          onOpenUnallocated={openUnallocated}
        />
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
      <UnallocatedSheet
        tenderId={card.id}
        round={unallocatedRound}
        open={unallocatedRound !== undefined}
        onOpenChange={(open) => {
          if (!open) closeUnallocated();
        }}
      />
      <TenderDeleteDialog
        tender={deleteOpen ? card : null}
        onOpenChange={() => setDeleteOpen(false)}
        onDeleted={() => navigate("/tenders")}
      />
    </div>
  );
}

/**
 * Русское склонение слова «смета» по числу выбранных предложений (подпись
 * над решёткой). `lib/format.ts` уже несёт `pluralRu` — но тот возвращает
 * ОКОНЧАНИЕ для слов мужского рода вида «объект/объекта/объектов», а «смета»
 * женского рода и меняет не только окончание («смета» → «сметы» → «смет»):
 * тот хелпер сюда не годится, нужна отдельная функция, а не переиспользование
 * не подходящей по роду.
 */
function estimateWordFor(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return "смета";
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return "сметы";
  return "смет";
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
