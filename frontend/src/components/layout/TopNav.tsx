import { NavLink, useNavigate } from "react-router-dom";
import {
  LayoutDashboard, BookOpen, Building2, Users, Layers, FileSpreadsheet,
  Settings, LogOut, Search, Bell, ShieldCheck, type LucideIcon,
} from "lucide-react";
import { Logo } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";
import { cn } from "@/lib/utils";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useCurrentUser, useLogout } from "@/hooks/useAuth";

const NAV: { to: string; icon: LucideIcon; label: string; end?: boolean; superuserOnly?: boolean }[] = [
  { to: "/dashboard", icon: LayoutDashboard, label: "Дашборд", end: true },
  { to: "/projects",  icon: Building2,       label: "Объекты" },
  { to: "/suppliers", icon: Users,           label: "Поставщики" },
  { to: "/materials", icon: Layers,          label: "Номенклатура" },
  { to: "/reports",   icon: FileSpreadsheet, label: "Отчёты" },
  { to: "/handbook", icon: BookOpen,         label: "Справочник" },
  { to: "/admin",     icon: ShieldCheck,     label: "Админ", superuserOnly: true },
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
  const navigate = useNavigate();
  const { data: user } = useCurrentUser();
  const logout = useLogout();

  const initials = user ? getInitials(user.email) : "…";
  const navItems = NAV.filter((item) => !item.superuserOnly || user?.is_superuser);

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
              {user?.organization && (
                <span className="hidden max-w-32 truncate text-xs font-medium text-fg-secondary sm:block">
                  {user.organization.name}
                </span>
              )}
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-48">
              {user && (
                <>
                  <div className="px-2 py-1.5">
                    <p className="text-sm font-medium text-fg truncate">{user.email}</p>
                    {user.organization && (
                      <p className="text-xs text-fg-secondary truncate">{user.organization.name}</p>
                    )}
                  </div>
                  <DropdownMenuSeparator />
                </>
              )}
              <DropdownMenuItem
                className="flex items-center gap-2"
                onClick={() => navigate("/settings")}
              >
                <Settings size={14} /> Настройки
              </DropdownMenuItem>
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
