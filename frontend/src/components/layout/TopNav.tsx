import { NavLink } from "react-router-dom";
import {
  Home, Users, LogOut, Search, Bell, FileText, ListChecks, Ruler, type LucideIcon,
} from "lucide-react";
import { Logo } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";
import { cn } from "@/lib/utils";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useCurrentUser, useLogout } from "@/hooks/useAuth";

// Паспорт объекта, сквозная матрица и отчёты — фаза 6 (AGENTS.md §7.4–§7.6, §9.6)
const NAV: { to: string; icon: LucideIcon; label: string; end?: boolean; adminOnly?: boolean }[] = [
  { to: "/",            icon: Home,       label: "Главная", end: true },
  { to: "/contracts",   icon: FileText,   label: "Договоры" },
  { to: "/review",      icon: ListChecks, label: "Ручной матчинг" },
  { to: "/standards",   icon: Ruler,      label: "Нормативы", adminOnly: true },
  { to: "/admin/users", icon: Users,      label: "Пользователи", adminOnly: true },
];

function getInitials(email: string): string {
  const local = (email.split("@")[0] ?? "").trim();
  const parts = local.split(/[._-]/).filter(Boolean);
  const letters =
    parts.length >= 2
      ? `${parts[0].charAt(0)}${parts[1].charAt(0)}`
      : local.replace(/[._-]/g, "").slice(0, 2);
  return (letters || "??").toUpperCase();
}

export function TopNav() {
  const { data: user } = useCurrentUser();
  const logout = useLogout();

  const initials = user ? getInitials(user.email) : "…";
  const navItems = NAV.filter((item) => !item.adminOnly || user?.role === "admin");

  return (
    <header className="sticky top-0 z-40 h-14 border-b border-border-subtle bg-surface/95 backdrop-blur">
      <div className="container-page flex h-full items-center gap-6">
        <Logo />
        <nav className="flex flex-1 flex-wrap items-center gap-0.5">
          {navItems.map(({ to, icon: Icon, label, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors duration-150",
                  isActive
                    ? "bg-surface-hover text-fg"
                    : "text-fg-secondary hover:bg-surface-hover hover:text-fg"
                )
              }
            >
              <Icon size={14} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="flex items-center gap-2">
          <button type="button" aria-label="Поиск" disabled className="flex h-8 w-8 items-center justify-center rounded-md text-fg-tertiary opacity-40">
            <Search size={16} />
          </button>
          <button type="button" aria-label="Уведомления" disabled className="flex h-8 w-8 items-center justify-center rounded-md text-fg-tertiary opacity-40">
            <Bell size={16} />
          </button>
          <ThemeToggle />
          <DropdownMenu>
            <DropdownMenuTrigger
              type="button"
              aria-label="Открыть меню пользователя"
              className="flex items-center gap-2 rounded-md px-2 py-1 text-sm hover:bg-surface-hover"
            >
              <span className="flex h-7 w-7 items-center justify-center rounded-full bg-accent/20 text-xs font-semibold text-accent">
                {initials}
              </span>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-48">
              {user && (
                <>
                  <div className="px-2 py-1.5">
                    <p className="text-sm font-medium text-fg truncate">{user.email}</p>
                    <p className="text-xs text-fg-secondary truncate">{user.role}</p>
                  </div>
                  <DropdownMenuSeparator />
                </>
              )}
              <DropdownMenuItem
                className="flex items-center gap-2 text-fg-secondary"
                onClick={() => logout.mutate()}
                disabled={logout.isPending}
              >
                <LogOut size={14} /> Выйти
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    </header>
  );
}
