import { useState } from "react";

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
import { EntitySelect } from "@/components/ui-domain/EntitySelect";
import {
  apiErrorCode,
  linkInvalidReason,
  useCreateFamilyLink,
  useFamilySchema,
  useUpdateFamilyLink,
  useWorkFamilies,
} from "@/services/queries";
import type { FamilyLinkBody, FamilyLinkView, FamilySchema, LinkKind, WorkFamily } from "@/types/domain";

import { LINK_KIND_LABEL, linkRefusalText } from "./labels";

const NO_PARAM = 0;
const KINDS = (Object.keys(LINK_KIND_LABEL) as LinkKind[]).map((id) => ({ id, label: LINK_KIND_LABEL[id] }));

/** Параметры, из которых берётся число связи: точные значения действующей схемы (спека §2.6). */
function exactParams(schema: FamilySchema | undefined): Array<{ id: number; name: string }> {
  const list = (schema?.parameters ?? [])
    .filter((p) => p.value_kind === "exact")
    .map((p) => ({ id: p.id, name: p.name }));
  return [{ id: NO_PARAM, name: "Без параметра" }, ...list];
}

export interface FamilyLinkDialogProps {
  /** Карточка, с которой открыт диалог: «эта» семья. */
  family: WorkFamily;
  /** `null` — новая связь; иначе правка этой связи (пара не меняется). */
  link: FamilyLinkView | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function FamilyLinkDialog({ family, link, open, onOpenChange }: FamilyLinkDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <LinkForm family={family} link={link} onDone={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

/**
 * Форма связи. Правка заполняется ХРАНИМЫМИ `factor` и `factor_from_family_id` (не `shown`), поэтому
 * сохранение без изменений с карточки любой из двух семей шлёт те же значения (спека §2.16).
 */
function LinkForm({ family, link, onDone }: { family: WorkFamily; link: FamilyLinkView | null; onDone: () => void }) {
  const editing = link !== null;
  const create = useCreateFamilyLink();
  const update = useUpdateFamilyLink();
  const familiesQ = useWorkFamilies("active");

  const [otherId, setOtherId] = useState<number | null>(link?.other_family.id ?? null);
  const [kind, setKind] = useState<LinkKind>(link?.kind ?? "thickness");
  // Устаревший параметр не предзаполняется: человек укажет его заново.
  const [thisParam, setThisParam] = useState<number>(
    link?.this_param != null && !link.stale_sides.includes("this") ? link.this_param.id : NO_PARAM
  );
  const [otherParam, setOtherParam] = useState<number>(
    link?.other_param != null && !link.stale_sides.includes("other") ? link.other_param.id : NO_PARAM
  );
  const [fromId, setFromId] = useState<number>(link?.factor_from_family_id ?? family.id);
  const [factor, setFactor] = useState(link?.factor ?? "");
  const [basis, setBasis] = useState(link?.basis ?? "");
  const [refusal, setRefusal] = useState<string | null>(null);

  const thisSchemaQ = useFamilySchema(family.id);
  const otherSchemaQ = useFamilySchema(otherId ?? 0, { enabled: otherId !== null });

  const others = (familiesQ.data ?? []).filter((f) => f.id !== family.id);
  const otherTitle = link?.other_family.title ?? others.find((f) => f.id === otherId)?.title ?? "";
  const otherUnit = link?.other_family.unit ?? others.find((f) => f.id === otherId)?.unit_symbol ?? null;
  const thisUnit = family.unit_symbol;
  const directionItems =
    otherId === null
      ? []
      : [
          { id: family.id, label: `${family.title}${thisUnit ? ` · ${thisUnit}` : ""}` },
          { id: otherId, label: `${otherTitle}${otherUnit ? ` · ${otherUnit}` : ""}` },
        ];

  const isConstant = kind === "constant";
  const factorText = factor.trim().replace(",", ".");
  const ready =
    otherId !== null &&
    (isConstant
      ? factorText !== "" && basis.trim() !== ""
      : thisParam !== NO_PARAM || otherParam !== NO_PARAM);
  const pending = create.isPending || update.isPending;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (otherId === null || !ready || pending) return;
    // Пустое значение у вида с параметром и параметры у `constant` — `null`, а не пустая строка.
    const body: FamilyLinkBody = {
      family_a_id: family.id,
      family_b_id: otherId,
      kind,
      param_a_id: isConstant || thisParam === NO_PARAM ? null : thisParam,
      param_b_id: isConstant || otherParam === NO_PARAM ? null : otherParam,
      factor_from_family_id: isConstant ? fromId : null,
      factor: isConstant ? factorText : null,
      basis: isConstant ? basis.trim() : null,
    };
    setRefusal(null);
    try {
      if (link === null) await create.mutateAsync(body);
      else await update.mutateAsync({ id: link.id, body });
      onDone();
    } catch (error) {
      const text = linkRefusalText(apiErrorCode(error), linkInvalidReason(error));
      // Прочие отказы уже показал тост хука.
      if (text !== null) setRefusal(text);
    }
  }

  return (
    <form onSubmit={submit}>
      <DialogHeader>
        <DialogTitle>{editing ? "Правка связи" : "Новая связь"}</DialogTitle>
        <DialogDescription>
          {family.title}
          {thisUnit ? ` · ${thisUnit}` : ""}
          {" ↔ "}
          {otherTitle || "другая семья"}
          {otherUnit ? ` · ${otherUnit}` : ""}
        </DialogDescription>
      </DialogHeader>
      <div className="grid gap-3 py-4">
        {!editing && (
          <div className="grid gap-2">
            <Label htmlFor="link-other-family">Другая семья</Label>
            <EntitySelect
              id="link-other-family"
              items={others}
              value={otherId}
              onChange={(id) => {
                setOtherId(id);
                setOtherParam(NO_PARAM);
              }}
              getLabel={(f) => `${f.title}${f.unit_symbol ? ` · ${f.unit_symbol}` : ""}`}
              placeholder="Выберите семью"
            />
          </div>
        )}
        <div className="grid gap-2">
          <Label htmlFor="link-kind">Вид связи</Label>
          <EntitySelect
            id="link-kind"
            items={KINDS}
            value={kind}
            onChange={(id) => id !== null && setKind(id)}
            getLabel={(k) => k.label}
          />
        </div>
        {isConstant ? (
          <>
            <div className="grid gap-2">
              <Label htmlFor="link-from">Левая часть равенства</Label>
              <EntitySelect
                id="link-from"
                items={directionItems}
                value={otherId === null ? null : fromId}
                onChange={(id) => id !== null && setFromId(id)}
                getLabel={(item) => item.label}
                disabled={otherId === null}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="link-factor">Множитель</Label>
              <Input
                id="link-factor"
                inputMode="decimal"
                autoComplete="off"
                value={factor}
                onChange={(event) => setFactor(event.target.value)}
              />
              <p className="text-xs text-fg-tertiary">
                1 единица левой части = множитель единиц другой семьи. Записывается как введён.
              </p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="link-basis">Основание</Label>
              <Input
                id="link-basis"
                autoComplete="off"
                maxLength={300}
                value={basis}
                onChange={(event) => setBasis(event.target.value)}
              />
            </div>
          </>
        ) : (
          <>
            <div className="grid gap-2">
              <Label htmlFor="link-this-param">Параметр этой семьи</Label>
              <EntitySelect
                id="link-this-param"
                items={exactParams(thisSchemaQ.data)}
                value={thisParam}
                onChange={(id) => setThisParam(id ?? NO_PARAM)}
                getLabel={(p) => p.name}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="link-other-param">Параметр другой семьи</Label>
              <EntitySelect
                id="link-other-param"
                items={exactParams(otherSchemaQ.data)}
                value={otherParam}
                onChange={(id) => setOtherParam(id ?? NO_PARAM)}
                getLabel={(p) => p.name}
                disabled={otherId === null}
              />
            </div>
            <p className="text-xs text-fg-tertiary">Нужен параметр хотя бы одной стороны.</p>
          </>
        )}
        {refusal !== null && (
          <p role="alert" data-testid="link-refusal" className="text-sm text-danger-text">
            {refusal}
          </p>
        )}
      </div>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone}>
          Отмена
        </Button>
        <Button type="submit" disabled={!ready} loading={pending}>
          Сохранить
        </Button>
      </DialogFooter>
    </form>
  );
}
