import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useSetFamilyZoneRequired } from "@/services/queries";
import type { WorkFamily } from "@/types/domain";

import {
  ZONE_REQUIRED_OFF,
  ZONE_REQUIRED_ON,
  ZONE_REQUIRED_SWITCH,
  ZONE_REQUIRED_TITLE,
  zoneRequiredAuthor,
} from "./labels";

/**
 * Блок «Зона при подборе» карточки семьи (спека зоны как предпочтения §5 п. 7): флажок «зона
 * обязательна» уходит сразу, без «Сохранить»; `Switch` занят на время запроса и недоступен у
 * архивной семьи (сервер ответил бы `409`).
 */
export function ZoneRequiredBlock({ family }: { family: WorkFamily }) {
  const setZoneRequired = useSetFamilyZoneRequired();
  const headingId = `family-zone-heading-${family.id}`;
  const switchId = `family-zone-switch-${family.id}`;
  const author = zoneRequiredAuthor(family);

  return (
    <section aria-labelledby={headingId} className="grid gap-2 border-t border-border-subtle pt-3">
      <h4 id={headingId} className="text-base font-medium text-fg">
        {ZONE_REQUIRED_TITLE}
      </h4>
      <div className="flex items-center gap-2">
        <Switch
          id={switchId}
          checked={family.zone_required}
          disabled={family.status === "archived" || setZoneRequired.isPending}
          onCheckedChange={(value) => setZoneRequired.mutate({ id: family.id, value })}
        />
        <Label htmlFor={switchId}>{ZONE_REQUIRED_SWITCH}</Label>
      </div>
      <p className="text-xs text-fg-tertiary">{family.zone_required ? ZONE_REQUIRED_ON : ZONE_REQUIRED_OFF}</p>
      {author && <p className="text-xs text-fg-tertiary">{author}</p>}
    </section>
  );
}
