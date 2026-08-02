import type { UserRole } from "@/types/auth";

const ROLE_STYLE: Record<UserRole, string> = {
  // Семантические токены (без raw hex): admin — info, member — нейтральный
  admin: "bg-info-soft text-info-text",
  member: "bg-surface-sunken text-fg-secondary",
};

/** Бейдж роли пользователя. */
export function RoleBadge({ role }: { role: UserRole | null | undefined }) {
  if (!role) return <span className="text-fg-tertiary">—</span>;
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${ROLE_STYLE[role]}`}>
      {role}
    </span>
  );
}
