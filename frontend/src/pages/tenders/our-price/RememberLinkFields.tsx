import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { EntitySelect } from "@/components/ui-domain/EntitySelect";
import { LINK_KIND_LABEL } from "@/pages/families/labels";
import type { LinkKind } from "@/types/domain";

import { REF_REMEMBER } from "./ourPriceCopy";
import type { useRememberLink } from "./rememberLink";

export interface RememberLinkFieldsProps {
  state: ReturnType<typeof useRememberLink>;
  kinds: LinkKind[];
  disabled: boolean;
}

/**
 * Флажок «Запомнить для семей» и, когда он включён, вид связи и параметры сторон (вид с параметром). Постоянный
 * множитель вводится в полях диалога: у него те же N и основание, что у ручного пересчёта.
 */
export function RememberLinkFields({ state, kinds, disabled }: RememberLinkFieldsProps) {
  const kindItems = kinds.map((id) => ({ id, label: LINK_KIND_LABEL[id] }));
  return (
    <div data-testid="reference-dialog-remember" className="space-y-2">
      <div className="flex items-center gap-2">
        <Checkbox
          id="reference-remember"
          checked={state.on}
          disabled={disabled}
          onCheckedChange={(checked) => state.setOn(checked === true)}
        />
        <Label htmlFor="reference-remember">{REF_REMEMBER.label}</Label>
      </div>
      {state.on && (
        <div className="space-y-2">
          {kinds.length > 1 && (
            <div className="grid gap-1.5">
              <Label htmlFor="reference-link-kind">{REF_REMEMBER.kind}</Label>
              <EntitySelect
                id="reference-link-kind"
                items={kindItems}
                value={state.kind}
                onChange={(id) => id !== null && state.setKind(id)}
                getLabel={(item) => item.label}
              />
            </div>
          )}
          {!state.isConstant && (
            <>
              <div className="grid gap-1.5">
                <Label htmlFor="reference-param-a">{REF_REMEMBER.paramKp}</Label>
                <EntitySelect
                  id="reference-param-a"
                  items={state.optionsA}
                  value={state.paramA}
                  onChange={(id) => state.setParamA(id)}
                  getLabel={(p) => p.name}
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="reference-param-b">{REF_REMEMBER.paramRow}</Label>
                <EntitySelect
                  id="reference-param-b"
                  items={state.optionsB}
                  value={state.paramB}
                  onChange={(id) => state.setParamB(id)}
                  getLabel={(p) => p.name}
                />
              </div>
              <p className="text-xs text-fg-secondary">{REF_REMEMBER.paramsNote}</p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
