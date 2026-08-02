import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

import { Surface } from "@/components/ui-domain/Surface";
import { Button } from "@/components/ui-domain/Button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { PasswordField } from "@/components/admin/PasswordField";
import { RoleBadge } from "@/components/admin/RoleBadges";
import { useCreateAdminUser } from "@/services/queries";
import { generatePassword } from "@/lib/password";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import type { UserRole } from "@/types/auth";

const ROLES: UserRole[] = ["admin", "member"];

export default function AdminUserCreate() {
  const navigate = useNavigate();
  const createUser = useCreateAdminUser();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState(() => generatePassword());
  const [role, setRole] = useState<UserRole>("member");
  const [isActive, setIsActive] = useState(true);

  const canSubmit = !!email.trim() && !!password;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    try {
      await createUser.mutateAsync({ email: email.trim(), password, role, is_active: isActive });
      toast.success("Пользователь создан");
      navigate("/admin/users");
    } catch {
      // тосты в onError
    }
  }

  return (
    <div className="container-page py-8">
      <div className="mx-auto max-w-xl">
        <button
          type="button"
          onClick={() => navigate("/admin/users")}
          className="mb-4 flex items-center gap-2 text-sm text-fg-secondary hover:text-fg"
        >
          <ArrowLeft size={18} />
          Новый пользователь
        </button>

        <form onSubmit={handleSubmit}>
          <Surface padding="lg" className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="user-email">Email</Label>
              <Input
                id="user-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="i.orlova@example.com"
                required
                autoComplete="off"
              />
            </div>

            <PasswordField value={password} onChange={setPassword} />

            <div className="flex flex-col gap-1.5">
              <Label>Роль</Label>
              <div className="grid grid-cols-2 gap-2">
                {ROLES.map((r) => {
                  const active = role === r;
                  return (
                    <button
                      key={r}
                      type="button"
                      aria-pressed={active}
                      onClick={() => setRole(r)}
                      className={cn(
                        "flex items-center justify-center rounded-md border p-2 transition-colors",
                        active ? "border-accent bg-accent/10" : "border-border-default hover:bg-surface-hover",
                      )}
                    >
                      <RoleBadge role={r} />
                    </button>
                  );
                })}
              </div>
              <p className="text-xs text-fg-tertiary">
                admin — управление пользователями, классами, нормативами, замена смет;
                member — чтение, загрузка смет, ручной матчинг.
              </p>
            </div>

            <div className="flex items-center justify-between border-t border-border-subtle pt-4">
              <div>
                <p className="text-sm text-fg">Активен</p>
                <p className="text-xs text-fg-secondary">Может входить в систему сразу после создания</p>
              </div>
              <Switch checked={isActive} onCheckedChange={setIsActive} />
            </div>
          </Surface>

          <div className="mt-4 flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => navigate("/admin/users")}>
              Отмена
            </Button>
            <Button type="submit" loading={createUser.isPending} disabled={!canSubmit}>
              Создать пользователя
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
