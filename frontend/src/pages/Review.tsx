import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { AlertTriangle, ArrowLeft, CheckCircle2, XCircle } from "lucide-react";

import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Surface } from "@/components/ui-domain/Surface";
import { Tabs } from "@/components/ui-domain/Tabs";
import { Breadcrumbs } from "@/components/ui-domain/Breadcrumbs";
import { Button } from "@/components/ui-domain/Button";
import { StatusPill } from "@/components/ui-domain/StatusPill";
import { ConfidenceBadge } from "@/components/ui-domain/ConfidenceBadge";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
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

import { ReviewHeader } from "@/components/review/ReviewHeader";
import { ReviewItemsTable } from "@/components/review/ReviewItemsTable";
import { ReviewIssues } from "@/components/review/ReviewIssues";

import {
  useDocument,
  useUpdateInvoice,
  useReparseDocument,
  useDeskewReparseDocument,
  useDeleteDocument,
  useVerifyInvoice,
  useUnverifyInvoice,
  useSettings,
} from "@/services/queries";
import { invoicesApi } from "@/services/api/invoices";
import { isDocBusy } from "@/services/processingRefetchInterval";
import { formatDate, formatUsd, pluralRu } from "@/lib/format";
import { DEFAULT_CONFIDENCE_THRESHOLD } from "@/lib/constants";
import type { DocumentSummary, InvoiceRow, InvoiceUpdateWarning } from "@/types/invoice";

type TabKey = "header" | "items" | "issues";

/**
 * Пилл статуса документа: busy/error приоритетнее остальной логики (данные СФ
 * могут быть устаревшими во время фонового reparse/deskew — parse-then-swap
 * ещё не случился). Возвращает null, если статус документа — терминальный
 * "parsed" без ошибки: в этом случае решение о пилле принимает вызывающий код
 * (например, hasProblems/verified в основном рендере СФ).
 * Общий хелпер для основного рендера Review и слим-вида документа без СФ —
 * вынесен, чтобы не дублировать логику приоритета (смоук PR #37).
 */
function docStatusPill(doc: Pick<DocumentSummary, "status" | "last_error">) {
  if (isDocBusy(doc.status)) {
    return <StatusPill tone="info" label="Обрабатывается" dot />;
  }
  if (doc.status === "error") {
    // StatusPill не принимает title — оборачиваем в span, чтобы причина
    // ошибки (last_error) была доступна по наведению.
    return (
      <span title={doc.last_error ?? undefined}>
        <StatusPill tone="danger" label="ошибка обработки" dot />
      </span>
    );
  }
  return null;
}

export default function Review() {
  const { id } = useParams<{ id: string }>();
  const docId = id ? Number(id) : null;
  const navigate = useNavigate();

  const docQ = useDocument(docId);
  const update = useUpdateInvoice();
  const reparse = useReparseDocument();
  const deskew = useDeskewReparseDocument();
  const remove = useDeleteDocument();
  const verify = useVerifyInvoice();
  const unverify = useUnverifyInvoice();
  const settingsQ = useSettings();

  const [tab, setTab] = useState<TabKey>("items");
  // Local edits keyed by invoice id — auto-discarded when invoice changes
  const [overrides, setOverrides] = useState<{ invId: number; data: InvoiceRow } | null>(null);
  const [unitWarnings, setUnitWarnings] = useState<InvoiceUpdateWarning[]>([]);
  // Общий AlertDialog удаления документа — триггерится и слим-видом (документ
  // без СФ), и основным рендером; один и тот же docId, поэтому достаточно
  // булева флага (не window.confirm — не паттерн проекта, образец: InvoiceTable).
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  const serverInv = docQ.data?.invoices[0] ?? null;
  const draft = serverInv && overrides?.invId === serverInv.id ? overrides.data : serverInv;

  const dirty =
    serverInv !== null &&
    overrides !== null &&
    overrides.invId === serverInv.id &&
    JSON.stringify(overrides.data) !== JSON.stringify(serverInv);

  if (docId === null) {
    return (
      <div className="container-page py-8">
        <EmptyState title="Документ не найден" />
      </div>
    );
  }

  if (docQ.isLoading) {
    return (
      <div className="container-page py-8 space-y-4">
        <Skeleton className="h-8 w-1/3" />
        <Skeleton className="h-[400px]" />
      </div>
    );
  }

  if (!docQ.data) {
    return (
      <div className="container-page py-8">
        <EmptyState title="Документ не найден" />
      </div>
    );
  }

  if (!draft) {
    // Документ существует, но СФ нет (пользователь удалил все СФ документа —
    // DELETE /api/invoices/{id} удаляет СФ, не документ; либо первичный
    // парсинг упал раньше создания СФ). Раньше это рендерило «Документ не
    // найден», делая документ недостижимым для reparse/deskew/удаления
    // (смоук-баг, root-caused PR #37).
    const slimDoc = docQ.data;
    return (
      <div className="container-page py-6">
        <Breadcrumbs
          items={[
            { label: "Дашборд", to: "/" },
            { label: slimDoc.filename },
          ]}
        />

        <PageHeader
          title={slimDoc.filename}
          subtitle="Документ без счетов-фактур"
          actions={docStatusPill(slimDoc)}
        />

        <div className="mt-6">
          <EmptyState
            title="В документе нет счетов-фактур"
            description="Запустите повторный разбор или удалите документ."
            action={
              <div className="flex items-center justify-center gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={reparse.isPending || isDocBusy(slimDoc.status)}
                  title={isDocBusy(slimDoc.status) ? "Документ обрабатывается — дождитесь завершения" : undefined}
                  onClick={() => reparse.mutate(docId)}
                >
                  Переразобрать
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={deskew.isPending || reparse.isPending || isDocBusy(slimDoc.status)}
                  title={isDocBusy(slimDoc.status) ? "Документ обрабатывается — дождитесь завершения" : undefined}
                  onClick={() => deskew.mutate(docId)}
                >
                  Выпрямить и переразобрать
                </Button>
                <Button
                  variant="danger"
                  size="sm"
                  disabled={remove.isPending || isDocBusy(slimDoc.status)}
                  title={isDocBusy(slimDoc.status) ? "Документ обрабатывается — дождитесь завершения" : undefined}
                  onClick={() => setDeleteDialogOpen(true)}
                >
                  Удалить
                </Button>
              </div>
            }
          />
        </div>

        <section className="mt-8">
          <Surface padding="none" className="overflow-hidden">
            <iframe
              title="Документ"
              src={invoicesApi.documentPdfUrl(docId)}
              className="h-[90vh] w-full border-0 bg-surface-sunken"
            />
          </Surface>
        </section>

        <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Удалить документ?</AlertDialogTitle>
              <AlertDialogDescription>
                Документ «{slimDoc.filename}» и все его счета-фактуры будут удалены без
                возможности восстановления.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Отмена</AlertDialogCancel>
              <AlertDialogAction
                variant="destructive"
                disabled={remove.isPending}
                onClick={() => {
                  remove.mutate(docId, {
                    onSuccess: () => navigate("/"),
                  });
                }}
              >
                {remove.isPending ? "Удаление…" : "Удалить"}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    );
  }

  const doc = docQ.data;
  const inv = draft;
  const threshold = settingsQ.data?.confidence_threshold ?? DEFAULT_CONFIDENCE_THRESHOLD;
  const hasProblems =
    !inv.verified &&
    (inv.has_issues ||
      (inv.ai_confidence ?? 0) < threshold ||
      !inv.supplier_name?.trim() ||
      !inv.number?.trim() ||
      inv.items.length === 0 ||
      inv.items.some((it) => !it.raw_name?.trim() || it.quantity <= 0));
  // Busy-документ (processing/pending) блокируется наравне с verified: поля
  // формы, отредактированные во время фоновой обработки, исчезнут при
  // parse-then-swap — до фикса реально сохранялась только кнопка «Сохранить»,
  // сами поля принимали ввод (Codex P2, fix 3).
  const locked = inv.verified || isDocBusy(doc.status);
  const documentLocked = doc.invoices.some((invoice) => invoice.verified);

  const tabs: Array<{ value: TabKey; label: string }> = [
    { value: "header", label: "Шапка" },
    { value: "items", label: `Позиции · ${inv.items.length}` },
    { value: "issues", label: "Проблемы" },
  ];

  return (
    <div className="container-page py-6">
      <Breadcrumbs
        items={[
          { label: "Дашборд", to: "/" },
          { label: doc.filename },
          { label: `СФ № ${inv.number || "—"}` },
        ]}
      />

      <PageHeader
        title={`СФ № ${inv.number || "—"} от ${formatDate(inv.date)}`}
        subtitle={inv.supplier_name ?? "Поставщик не указан"}
        actions={
          <>
            <ConfidenceBadge value={inv.ai_confidence} />
            {docStatusPill(doc) ?? (
              <>
                {serverInv?.verified && (
                  <StatusPill tone="success" label="Проверено" dot />
                )}
                <StatusPill
                  tone={hasProblems ? "warning" : "success"}
                  label={hasProblems ? "требует проверки" : "готово"}
                  dot
                />
              </>
            )}
          </>
        }
      />

      {unitWarnings.length > 0 && (
        <Surface tone="sunken" padding="sm" className="mt-4 text-sm">
          <p className="font-medium text-fg">Предупреждения</p>
          {unitWarnings.map((w, i) => (
            <p key={i} className="flex items-center gap-1 text-fg-secondary">
              <AlertTriangle size={14} />{w.message}
            </p>
          ))}
        </Surface>
      )}

      {/* Сверху — редактирование на всю ширину */}
      <div className="mt-6">
        <Tabs<TabKey> value={tab} onValueChange={setTab} tabs={tabs}>
          {tab === "header" && (
            <Surface>
              <ReviewHeader
                invoice={inv}
                onChange={locked ? undefined : (patch) => setOverrides({ invId: inv.id, data: { ...inv, ...patch } })}
              />
            </Surface>
          )}
          {tab === "items" && (
            <ReviewItemsTable
              items={inv.items}
              onChange={locked ? undefined : (items) => setOverrides({ invId: inv.id, data: { ...inv, items } })}
              vatRate={inv.vat_rate}
            />
          )}
          {tab === "issues" && (
            <Surface>
              <ReviewIssues invoice={inv} />
            </Surface>
          )}
        </Tabs>
      </div>

      {/* Снизу — превью оригинала документа */}
      <section className="mt-8">
        <div className="mb-3 flex items-end justify-between gap-3">
          <h2 className="font-serif text-xl font-medium text-fg">
            Оригинал документа
          </h2>
          <div className="flex items-center gap-3 text-xs text-fg-tertiary">
            <span>{doc.filename}</span>
            {doc.parse_count > 0 && (
              <span title={`${doc.parse_count} разбор${pluralRu(doc.parse_count)}`}>
                {settingsQ.data?.cost_available === true
                  ? `ИИ-разбор: ${formatUsd(doc.parse_cost_usd)}`
                  : "ИИ-разбор: стоимость недоступна"}
                {doc.parse_count > 1 ? ` · ${doc.parse_count}×` : ""}
              </span>
            )}
            <button
              type="button"
              onClick={() => { setUnitWarnings([]); reparse.mutate(docId); }}
              disabled={reparse.isPending || verify.isPending || unverify.isPending || documentLocked || isDocBusy(doc.status)}
              title={isDocBusy(doc.status) ? "Документ обрабатывается — дождитесь завершения" : documentLocked || verify.isPending || unverify.isPending ? "Сначала завершите или снимите подтверждение" : undefined}
              className="text-fg-secondary underline-offset-2 hover:text-fg hover:underline disabled:opacity-50"
            >
              Переразобрать
            </button>
            <button
              type="button"
              onClick={() => { setUnitWarnings([]); deskew.mutate(docId); }}
              disabled={deskew.isPending || reparse.isPending || verify.isPending || unverify.isPending || documentLocked || isDocBusy(doc.status)}
              title={isDocBusy(doc.status) ? "Документ обрабатывается — дождитесь завершения" : documentLocked || verify.isPending || unverify.isPending ? "Сначала завершите или снимите подтверждение" : undefined}
              className="text-fg-secondary underline-offset-2 hover:text-fg hover:underline disabled:opacity-50"
            >
              Выпрямить и переразобрать
            </button>
          </div>
        </div>
        <Surface padding="none" className="overflow-hidden">
          <iframe
            title="Документ"
            src={invoicesApi.documentPdfUrl(docId)}
            className="h-[90vh] w-full border-0 bg-surface-sunken"
          />
        </Surface>
      </section>

      {/* Sticky-bar внизу */}
      <div className="sticky bottom-0 -mx-6 mt-8 border-t border-border-subtle bg-surface/95 px-6 py-3 backdrop-blur">
        <div className="container-page flex items-center justify-between">
          <Button
            variant="ghost"
            leftIcon={<ArrowLeft size={14} />}
            onClick={() => navigate(-1)}
          >
            Назад
          </Button>
          <div className="flex items-center gap-2">
            <Button
              variant="danger"
              size="sm"
              disabled={verify.isPending || unverify.isPending || documentLocked || isDocBusy(doc.status)}
              title={isDocBusy(doc.status) ? "Документ обрабатывается — дождитесь завершения" : documentLocked || verify.isPending || unverify.isPending ? "Сначала завершите или снимите подтверждение" : undefined}
              onClick={() => setDeleteDialogOpen(true)}
            >
              Удалить
            </Button>
            {serverInv && (
              serverInv.verified ? (
                <Button
                  variant="ghost"
                  size="sm"
                  leftIcon={<XCircle size={14} />}
                  disabled={unverify.isPending || dirty || isDocBusy(doc.status)}
                  loading={unverify.isPending}
                  onClick={() => unverify.mutate(serverInv.id, { onSuccess: () => setOverrides(null) })}
                  title={isDocBusy(doc.status) ? "Документ обрабатывается — дождитесь завершения" : dirty ? "Сначала сохраните изменения" : undefined}
                >
                  Снять подтверждение
                </Button>
              ) : (
                <Button
                  variant="secondary"
                  size="sm"
                  leftIcon={<CheckCircle2 size={14} />}
                  disabled={verify.isPending || dirty || isDocBusy(doc.status)}
                  loading={verify.isPending}
                  onClick={() => verify.mutate(serverInv.id, { onSuccess: () => setOverrides(null) })}
                  title={isDocBusy(doc.status) ? "Документ обрабатывается — дождитесь завершения" : dirty ? "Сначала сохраните изменения" : undefined}
                >
                  Подтвердить
                </Button>
              )
            )}
            <Button
              variant="secondary"
              disabled={!dirty || update.isPending || isDocBusy(doc.status)}
              title={isDocBusy(doc.status) ? "Документ обрабатывается — дождитесь завершения" : undefined}
              loading={update.isPending}
              onClick={() => {
                setUnitWarnings([]);
                update.mutate(
                  {
                    id: inv.id,
                    input: {
                      number: inv.number,
                      date: inv.date,
                      supplier_name: inv.supplier_name,
                      supplier_inn: inv.supplier_inn,
                      vat_rate: inv.vat_rate,
                      items: inv.items,
                    },
                  },
                  {
                    onSuccess: (data) => {
                      setOverrides(null);
                      setUnitWarnings(data.warnings);
                      if (data.warnings.length > 0) {
                        toast.warning("Сохранено с предупреждениями", {
                          description: data.warnings[0].message,
                        });
                      }
                    },
                  },
                );
              }}
            >
              Сохранить
            </Button>
          </div>
        </div>
      </div>

      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Удалить документ?</AlertDialogTitle>
            <AlertDialogDescription>
              Документ «{doc.filename}» и все его счета-фактуры будут удалены без
              возможности восстановления.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Отмена</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={remove.isPending}
              onClick={() => {
                remove.mutate(docId, {
                  onSuccess: () => navigate("/"),
                });
              }}
            >
              {remove.isPending ? "Удаление…" : "Удалить"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
