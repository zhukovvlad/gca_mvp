import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, Building2, Truck, Info } from "lucide-react";
import { toast } from "sonner";

import { Surface } from "@/components/ui-domain/Surface";
import { Button } from "@/components/ui-domain/Button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PasswordField } from "@/components/admin/PasswordField";
import { useCreateOrganization, useCreateAdminUser } from "@/services/queries";
import { generatePassword } from "@/lib/password";
import { cn } from "@/lib/utils";
import type { OrgKind } from "@/types/auth";

const KIND_OPTIONS: { value: OrgKind; label: string; hint: string; icon: typeof Building2 }[] = [
  { value: "customer", label: "Заказчик", hint: "Видит все данные проекта", icon: Building2 },
  { value: "contractor", label: "Подрядчик", hint: "Видит только свои загрузки", icon: Truck },
];

export default function AdminOrgCreate() {
  const navigate = useNavigate();
  const createOrg = useCreateOrganization();
  const createUser = useCreateAdminUser();

  const [name, setName] = useState("");
  const [inn, setInn] = useState("");
  const [kind, setKind] = useState<OrgKind>("customer");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState(() => generatePassword());

  const submitting = createOrg.isPending || createUser.isPending;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    try {
      const trimmedName = name.trim();
      const trimmedEmail = email.trim();
      const org = await createOrg.mutateAsync({ name: trimmedName, inn: inn.trim() || null, kind });
      // Первый пользователь организации автоматически становится superadmin (на бэке)
      await createUser.mutateAsync({
        orgId: org.id,
        input: { email: trimmedEmail, password, org_role: "superadmin" },
      });
      toast.success("Организация и администратор созданы — передайте пароль безопасно");
      navigate(`/admin/organizations/${org.id}`);
    } catch {
      // ошибки показываются тостами в mutation onError
    }
  }

  return (
    <div className="container-page py-8">
      <div className="mx-auto max-w-xl">
        <button
          type="button"
          onClick={() => navigate("/admin")}
          className="mb-4 flex items-center gap-2 text-sm text-fg-secondary hover:text-fg"
        >
          <ArrowLeft size={18} />
          Создание организации
        </button>

        <form onSubmit={handleSubmit}>
          <Surface padding="lg" className="flex flex-col gap-4">
            <p className="text-sm font-medium text-fg-tertiary">Данные организации</p>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="org-name">Название</Label>
              <Input
                id="org-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="ООО «СтройГрад»"
                required
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="org-inn">
                ИНН <span className="text-fg-tertiary">(необязательно)</span>
              </Label>
              <Input
                id="org-inn"
                value={inn}
                onChange={(e) => setInn(e.target.value)}
                placeholder="7705123456"
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label>Роль</Label>
              <div className="grid grid-cols-2 gap-2">
                {KIND_OPTIONS.map(({ value, label, hint, icon: Icon }) => {
                  const active = kind === value;
                  return (
                    <button
                      key={value}
                      type="button"
                      aria-pressed={active}
                      onClick={() => setKind(value)}
                      className={cn(
                        "rounded-md border p-3 text-left transition-colors",
                        active
                          ? "border-accent bg-accent/10"
                          : "border-border-default hover:bg-surface-hover",
                      )}
                    >
                      <div className="flex items-center gap-2">
                        <Icon size={16} className={active ? "text-accent" : "text-fg-secondary"} />
                        <span className={cn("text-sm font-medium", active ? "text-accent" : "text-fg")}>
                          {label}
                        </span>
                      </div>
                      <p className={cn("mt-1 text-xs", active ? "text-accent" : "text-fg-secondary")}>
                        {hint}
                      </p>
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="border-t border-border-subtle pt-4">
              <div className="flex items-baseline justify-between">
                <p className="text-sm font-medium text-fg-tertiary">Первый администратор</p>
                <span className="rounded-md bg-accent/15 px-2 py-0.5 text-xs text-accent">
                  роль superadmin
                </span>
              </div>
              <p className="mb-3 mt-1 flex items-center gap-1 text-xs text-fg-secondary">
                <Info size={13} />
                Первый пользователь организации автоматически получает права superadmin.
              </p>

              <div className="flex flex-col gap-4">
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="admin-email">Email</Label>
                  <Input
                    id="admin-email"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="a.petrov@stroygrad.ru"
                    required
                    autoComplete="off"
                  />
                </div>
                <PasswordField value={password} onChange={setPassword} />
              </div>
            </div>
          </Surface>

          <div className="mt-4 flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => navigate("/admin")}>
              Отмена
            </Button>
            <Button type="submit" loading={submitting} disabled={!name.trim() || !email.trim() || !password}>
              Создать организацию
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
