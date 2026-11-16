import { useState } from "react";

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
import { useCurrentUser } from "@/hooks/useAuth";
import { formatDate } from "@/lib/format";
import { useDeleteFamilyLink, useFamilyLinks } from "@/services/queries";
import type { FamilyLinkView, WorkFamily } from "@/types/domain";

import { FamilyLinkDialog } from "./FamilyLinkDialog";
import { LINK_INACTIVE_LABEL, LINK_KIND_LABEL, LINK_STALE_LABEL } from "./labels";
import { ListSkeleton } from "./pending";

const WARN_TINT = "border-warning-border bg-warning-soft text-warning-text";

/** Равенство постоянной связи для показа: «1 т = 0,667 м³»; число — `shown` сервера, не пересчитывается. */
function constantText(family: WorkFamily, link: FamilyLinkView): string | null {
  if (link.shown === null) return null;
  const fromThis = link.shown.from_family_id === family.id;
  const fromUnit = fromThis ? family.unit_symbol : link.other_family.unit;
  const toUnit = fromThis ? link.other_family.unit : family.unit_symbol;
  return `1 ${fromUnit ?? "ед."} = ${link.shown.factor.replace(".", ",")} ${toUnit ?? "ед."}`;
}

function paramsText(link: FamilyLinkView): string | null {
  if (link.kind === "constant") return null;
  const parts = [
    link.this_param === null ? null : `эта семья: «${link.this_param.name}»`,
    link.other_param === null ? null : `другая: «${link.other_param.name}»`,
  ].filter((part): part is string => part !== null);
  return parts.length === 0 ? null : `параметры — ${parts.join(", ")}`;
}

function LinkItem({
  family,
  link,
  canEdit,
  onEdit,
  onDelete,
}: {
  family: WorkFamily;
  link: FamilyLinkView;
  canEdit: boolean;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const inactive = link.state === "inactive";
  const equality = constantText(family, link);
  const params = paramsText(link);
  // «Параметр устарел» уже названо причиной, когда не осталось ни одной живой стороны.
  const staleSides = inactive && link.inactive_reason === "no_live_side" ? [] : link.stale_sides;
  return (
    <li data-testid={`family-link-${link.id}`} className="grid gap-1 rounded-md border border-border-subtle px-3 py-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium text-fg">
          {link.other_family.title}
          {link.other_family.unit !== null && (
            <span className="font-normal text-fg-secondary">{` · ${link.other_family.unit}`}</span>
          )}
        </span>
        <Badge variant="outline">{LINK_KIND_LABEL[link.kind]}</Badge>
      </div>
      {equality !== null && (
        <p data-testid={`family-link-${link.id}-shown`} className="font-mono text-sm tabular-nums">
          {equality}
          {link.basis !== null && <span className="font-sans text-xs text-fg-secondary">{` — ${link.basis}`}</span>}
        </p>
      )}
      {params !== null && <p className="text-xs text-fg-secondary">{params}</p>}
      {inactive && link.inactive_reason !== null && (
        <p data-testid={`family-link-${link.id}-inactive`} className="text-xs text-warning-text">
          {`не действует: ${LINK_INACTIVE_LABEL[link.inactive_reason]}`}
        </p>
      )}
      {staleSides.map((side) => (
        <Badge key={side} variant="outline" className={`w-fit ${WARN_TINT}`} data-testid={`family-link-${link.id}-stale`}>
          {`${side === "this" ? "эта семья" : "другая семья"}: ${LINK_STALE_LABEL}`}
        </Badge>
      ))}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-fg-tertiary">
          {link.updated_by !== null && link.updated_at !== null
            ? `Правил ${link.updated_by.email ?? "—"}, ${formatDate(link.updated_at)}`
            : `Завёл ${link.created_by.email ?? "—"}, ${formatDate(link.created_at)}`}
        </span>
        {canEdit && (
          <span className="flex gap-1">
            <Button type="button" size="sm" variant="outline" onClick={onEdit}>
              Править
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={onDelete}>
              Удалить
            </Button>
          </span>
        )}
      </div>
    </li>
  );
}

/**
 * Раздел «Связи» карточки семьи (спека справочных аналогов §2.6, §2.18): связи, развёрнутые от этой
 * семьи, признак «не действует» с причиной и «параметр устарел». Создание, правка и удаление — только `admin`.
 */
export function FamilyLinksBlock({ family }: { family: WorkFamily }) {
  const linksQ = useFamilyLinks(family.id);
  const { data: user } = useCurrentUser();
  const canEdit = user?.role === "admin";
  const remove = useDeleteFamilyLink();
  const [dialog, setDialog] = useState<{ link: FamilyLinkView | null } | null>(null);
  const [deleting, setDeleting] = useState<FamilyLinkView | null>(null);
  const active = family.status === "active";

  return (
    <section aria-labelledby={`family-links-heading-${family.id}`} className="grid gap-3 border-t border-border-subtle pt-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 id={`family-links-heading-${family.id}`} className="text-base font-medium text-fg">
          Связи
        </h4>
        {canEdit && (
          <Button type="button" variant="outline" disabled={!active} onClick={() => setDialog({ link: null })}>
            Добавить связь…
          </Button>
        )}
      </div>
      <p className="text-xs text-fg-tertiary">
        Связь говорит, что две семьи в разных единицах — одна работа: по ней строки договоров пересчитываются
        в единицу строки КП.
      </p>

      {linksQ.isPending && <ListSkeleton rows={2} />}
      {linksQ.isError && <p className="text-sm text-fg-tertiary">Не удалось получить связи семьи.</p>}
      {linksQ.data && linksQ.data.length === 0 && <p className="text-sm text-fg-tertiary">Связей нет.</p>}
      {linksQ.data && linksQ.data.length > 0 && (
        <ul className="grid gap-2">
          {linksQ.data.map((link) => (
            <LinkItem
              key={link.id}
              family={family}
              link={link}
              canEdit={canEdit}
              onEdit={() => setDialog({ link })}
              onDelete={() => setDeleting(link)}
            />
          ))}
        </ul>
      )}

      <FamilyLinkDialog
        // Ключ по связи: форма инициализируется хранимыми значениями каждой открытой связи заново.
        key={dialog?.link?.id ?? "new"}
        family={family}
        link={dialog?.link ?? null}
        open={dialog !== null}
        onOpenChange={(next) => !next && setDialog(null)}
      />

      <AlertDialog open={deleting !== null} onOpenChange={(next) => !next && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Удалить связь с «{deleting?.other_family.title}»?</AlertDialogTitle>
            <AlertDialogDescription>
              Цели, выбранные по этой связи, сохранят цифру по снимку, а карточка цели скажет, что связи больше нет.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel render={<Button variant="outline">Отмена</Button>} />
            <AlertDialogAction
              render={
                <Button
                  variant="destructive"
                  onClick={() => {
                    if (deleting) remove.mutate(deleting.id);
                    setDeleting(null);
                  }}
                >
                  Удалить связь
                </Button>
              }
            />
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
