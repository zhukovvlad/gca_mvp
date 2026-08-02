import { Building2, Truck } from "lucide-react";
import type { OrgKind, OrgRole } from "@/types/auth";

/** Бейдж роли организации: заказчик / подрядчик. */
export function OrgKindBadge({ kind }: { kind: OrgKind | null | undefined }) {
  if (kind === "contractor") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-surface-sunken px-2 py-0.5 text-xs font-medium text-fg-secondary">
        <Truck size={12} />
        Подрядчик
      </span>
    );
  }
  if (kind === "customer") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-accent/15 px-2 py-0.5 text-xs font-medium text-accent">
        <Building2 size={12} />
        Заказчик
      </span>
    );
  }
  return <span className="text-fg-tertiary">—</span>;
}

const ROLE_STYLE: Record<OrgRole, string> = {
  // Семантические токены (без raw hex): superadmin — accent, admin — info, member — нейтральный
  superadmin: "bg-accent/15 text-accent",
  admin: "bg-info-soft text-info-text",
  member: "bg-surface-sunken text-fg-secondary",
};

/** Бейдж роли пользователя внутри организации. */
export function OrgRoleBadge({ role }: { role: OrgRole | null | undefined }) {
  if (!role) return <span className="text-fg-tertiary">—</span>;
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${ROLE_STYLE[role]}`}>
      {role}
    </span>
  );
}
