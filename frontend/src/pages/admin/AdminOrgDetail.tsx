import { useMemo, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Plus, KeyRound, Copy, Trash2, Pencil, Building2, Truck } from "lucide-react";
import { toast } from "sonner";

import { PageHeader } from "@/components/ui-domain/PageHeader";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Surface } from "@/components/ui-domain/Surface";
import { Button } from "@/components/ui-domain/Button";
import { EntitySelect } from "@/components/ui-domain/EntitySelect";
import { OrgKindBadge, OrgRoleBadge } from "@/components/admin/RoleBadges";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  useAdminOrganization,
  useProjects,
  useUpdateAdminUser,
  useUpdateOrganization,
  useResetUserPassword,
  useLinkProject,
  useUnlinkProject,
} from "@/services/queries";
import { copyToClipboard } from "@/lib/password";
import { cn } from "@/lib/utils";
import type { ID } from "@/types/common";
import type { OrgKind, OrgRole } from "@/types/auth";
import type { AdminOrgDetail as AdminOrgDetailType, AdminUser, OrgProjectLink } from "@/types/admin";

type TabKey = "users" | "projects";

export default function AdminOrgDetail() {
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  const parsed = Number(id);
  // Невалидный id в URL (/admin/organizations/abc) → null: запрос не уходит на /NaN
  const orgId = Number.isInteger(parsed) && parsed > 0 ? parsed : null;
  const orgQ = useAdminOrganization(orgId);
  const [tab, setTab] = useState<TabKey>("users");
  const [editOpen, setEditOpen] = useState(false);

  const org = orgQ.data;

  if (orgId === null) {
    return (
      <div className="container-page py-8">
        <EmptyState title="Организация не найдена" description="Некорректный адрес страницы." />
      </div>
    );
  }

  return (
    <div className="container-page py-8">
      <button
        type="button"
        onClick={() => navigate("/admin")}
        className="mb-4 flex items-center gap-2 text-sm text-fg-secondary hover:text-fg"
      >
        <ArrowLeft size={18} />
        К списку организаций
      </button>

      {orgQ.isPending && <Skeleton className="h-24 w-full" />}
      {orgQ.isError && <EmptyState title="Ошибка" description="Не удалось загрузить организацию." />}

      {org && (
        <>
          <PageHeader
            serif
            title={org.name}
            subtitle={org.inn ? `ИНН ${org.inn}` : undefined}
            actions={
              <>
                <OrgKindBadge kind={org.kind} />
                <Button variant="secondary" leftIcon={<Pencil size={14} />} onClick={() => setEditOpen(true)}>
                  Редактировать
                </Button>
              </>
            }
          />

          <EditOrgDialog org={org} open={editOpen} onClose={() => setEditOpen(false)} />

          <Tabs className="mt-6" value={tab} onValueChange={(v) => setTab(v as TabKey)}>
            <TabsList variant="line">
              <TabsTrigger value="users">Пользователи · {org.users.length}</TabsTrigger>
              <TabsTrigger value="projects">Доступ к проектам · {org.projects.length}</TabsTrigger>
            </TabsList>

            <TabsContent value="users" className="mt-6">
              <UsersTab orgId={orgId} users={org.users} />
            </TabsContent>
            <TabsContent value="projects" className="mt-6">
              <ProjectsTab key={org.kind} orgId={orgId} kind={org.kind} links={org.projects} />
            </TabsContent>
          </Tabs>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
//  Диалог редактирования организации
// ---------------------------------------------------------------------------

const KIND_OPTIONS: { value: OrgKind; label: string; hint: string; icon: typeof Building2 }[] = [
  { value: "customer", label: "Заказчик", hint: "Видит все данные проекта", icon: Building2 },
  { value: "contractor", label: "Подрядчик", hint: "Видит только свои загрузки", icon: Truck },
];

function EditOrgDialog({
  org,
  open,
  onClose,
}: {
  org: AdminOrgDetailType;
  open: boolean;
  onClose: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Редактирование организации</DialogTitle>
          <DialogDescription>Измените название, ИНН или роль организации.</DialogDescription>
        </DialogHeader>
        {/* key пересоздаёт форму при каждом открытии → состояние инициализируется
            из актуального org через useState, без setState-in-effect/render. */}
        {open && <EditOrgForm key={org.id} org={org} onClose={onClose} />}
      </DialogContent>
    </Dialog>
  );
}

function EditOrgForm({ org, onClose }: { org: AdminOrgDetailType; onClose: () => void }) {
  const updateOrg = useUpdateOrganization();
  const [name, setName] = useState(org.name);
  const [inn, setInn] = useState(org.inn ?? "");
  const [kind, setKind] = useState<OrgKind>(org.kind);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    updateOrg.mutate(
      { id: org.id, input: { name: name.trim(), inn: inn.trim() || null, kind } },
      { onSuccess: () => onClose() },
    );
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="edit-org-name">Название</Label>
        <Input
          id="edit-org-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="edit-org-inn">
          ИНН <span className="text-fg-tertiary">(необязательно)</span>
        </Label>
        <Input id="edit-org-inn" value={inn} onChange={(e) => setInn(e.target.value)} />
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
                  active ? "border-accent bg-accent/10" : "border-border-default hover:bg-surface-hover",
                )}
              >
                <div className="flex items-center gap-2">
                  <Icon size={16} className={active ? "text-accent" : "text-fg-secondary"} />
                  <span className={cn("text-sm font-medium", active ? "text-accent" : "text-fg")}>{label}</span>
                </div>
                <p className={cn("mt-1 text-xs", active ? "text-accent" : "text-fg-secondary")}>{hint}</p>
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onClose}>
          Отмена
        </Button>
        <Button type="submit" loading={updateOrg.isPending} disabled={!name.trim()}>
          Сохранить
        </Button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
//  Вкладка «Пользователи»
// ---------------------------------------------------------------------------

function UsersTab({ orgId, users }: { orgId: ID; users: AdminUser[] }) {
  const navigate = useNavigate();
  const updateUser = useUpdateAdminUser();
  const resetPassword = useResetUserPassword();

  const [deactivateTarget, setDeactivateTarget] = useState<AdminUser | null>(null);
  const [resetResult, setResetResult] = useState<{ email: string; password: string } | null>(null);

  function handleRoleChange(user: AdminUser, role: OrgRole) {
    if (role === user.org_role) return;
    updateUser.mutate({ userId: user.id, input: { org_role: role } });
  }

  function handleToggleActive(user: AdminUser) {
    if (user.is_active) {
      setDeactivateTarget(user);
    } else {
      updateUser.mutate({ userId: user.id, input: { is_active: true } });
    }
  }

  function confirmDeactivate() {
    if (!deactivateTarget) return;
    updateUser.mutate({ userId: deactivateTarget.id, input: { is_active: false } });
    setDeactivateTarget(null);
  }

  async function handleReset(user: AdminUser) {
    try {
      const result = await resetPassword.mutateAsync(user.id);
      setResetResult({ email: result.email, password: result.password });
    } catch {
      // Ошибка уже показана глобальным mutations.onError; ловим, чтобы не было
      // unhandled promise rejection (onClick не await'ит этот промис).
    }
  }

  return (
    <>
      <div className="mb-3 flex justify-end">
        <Button
          leftIcon={<Plus size={14} />}
          onClick={() => navigate(`/admin/organizations/${orgId}/users/new`)}
        >
          Добавить пользователя
        </Button>
      </div>

      {users.length === 0 ? (
        <EmptyState title="Пользователей нет" description="Добавьте первого пользователя организации." />
      ) : (
        <Surface padding="none" className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Email</TableHead>
                <TableHead>Роль</TableHead>
                <TableHead>Статус</TableHead>
                <TableHead className="text-right">Действия</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {users.map((u) => (
                <TableRow key={u.id}>
                  <TableCell className="font-medium text-fg">{u.email}</TableCell>
                  <TableCell>
                    <Select
                      value={u.org_role ?? ""}
                      onValueChange={(v) => v && handleRoleChange(u, v as OrgRole)}
                    >
                      <SelectTrigger className="h-7 w-32" aria-label={`Роль ${u.email}`}>
                        <SelectValue>
                          {(raw) => (raw ? <OrgRoleBadge role={raw as OrgRole} /> : "—")}
                        </SelectValue>
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="superadmin">superadmin</SelectItem>
                        <SelectItem value="admin">admin</SelectItem>
                        <SelectItem value="member">member</SelectItem>
                      </SelectContent>
                    </Select>
                  </TableCell>
                  <TableCell>
                    {u.is_active ? (
                      <span className="text-xs text-info-text">Активен</span>
                    ) : (
                      <span className="text-xs text-fg-tertiary">Деактивирован</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-1.5">
                      <Button
                        variant="ghost"
                        size="sm"
                        leftIcon={<KeyRound size={13} />}
                        onClick={() => handleReset(u)}
                        loading={resetPassword.isPending && resetPassword.variables === u.id}
                      >
                        Сбросить пароль
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => handleToggleActive(u)}>
                        {u.is_active ? "Деактивировать" : "Активировать"}
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Surface>
      )}

      {/* Подтверждение деактивации */}
      <AlertDialog open={deactivateTarget !== null} onOpenChange={(o) => !o && setDeactivateTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Деактивировать пользователя?</AlertDialogTitle>
            <AlertDialogDescription>
              {deactivateTarget?.email} больше не сможет входить в систему. Действие обратимо.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Отмена</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={confirmDeactivate}>
              Деактивировать
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Показ нового пароля один раз */}
      <ResetPasswordDialog result={resetResult} onClose={() => setResetResult(null)} />
    </>
  );
}

function ResetPasswordDialog({
  result,
  onClose,
}: {
  result: { email: string; password: string } | null;
  onClose: () => void;
}) {
  async function handleCopy() {
    if (!result) return;
    const ok = await copyToClipboard(result.password);
    if (ok) toast.success("Пароль скопирован — передайте его безопасным способом");
    else toast.error("Не удалось скопировать пароль");
  }

  return (
    <Dialog open={result !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Новый пароль</DialogTitle>
          <DialogDescription>
            Пароль для {result?.email} сгенерирован. Он показывается один раз — передайте его
            пользователю безопасным способом.
          </DialogDescription>
        </DialogHeader>
        <div className="flex items-center gap-2">
          <Input readOnly value={result?.password ?? ""} className="flex-1 font-mono tracking-wide" />
          <Button type="button" variant="secondary" aria-label="Скопировать пароль" onClick={handleCopy}>
            <Copy size={15} />
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
//  Вкладка «Доступ к проектам»
// ---------------------------------------------------------------------------

function ProjectsTab({
  orgId,
  kind,
  links,
}: {
  orgId: ID;
  kind: OrgKind | null;
  links: OrgProjectLink[];
}) {
  const projectsQ = useProjects();
  const linkProject = useLinkProject();
  const unlinkProject = useUnlinkProject();

  const [selectedProject, setSelectedProject] = useState<ID | null>(null);
  const [projectRole, setProjectRole] = useState<OrgKind>(kind ?? "customer");
  const [unlinkTarget, setUnlinkTarget] = useState<OrgProjectLink | null>(null);

  // Проекты, ещё не привязанные к организации
  const availableProjects = useMemo(() => {
    const linkedIds = new Set(links.map((l) => l.project_id));
    return (projectsQ.data ?? []).filter((p) => !linkedIds.has(p.id));
  }, [projectsQ.data, links]);

  function handleLink() {
    if (selectedProject === null) return;
    linkProject.mutate(
      { orgId, input: { project_id: selectedProject, project_role: projectRole } },
      {
        onSuccess: () => {
          setSelectedProject(null);
          setProjectRole(kind ?? "customer");
        },
      },
    );
  }

  function confirmUnlink() {
    if (!unlinkTarget) return;
    unlinkProject.mutate({ orgId, projectId: unlinkTarget.project_id });
    setUnlinkTarget(null);
  }

  return (
    <>
      {/* Форма «Дать доступ к проекту» */}
      <Surface padding="md" className="mb-4">
        <p className="mb-3 text-sm font-medium text-fg-tertiary">Дать доступ к проекту</p>
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-56 flex-1">
            <EntitySelect
              items={availableProjects}
              value={selectedProject}
              onChange={setSelectedProject}
              getLabel={(p) => p.name}
              placeholder="Выберите проект"
              disabled={projectsQ.isPending || projectsQ.isError}
            />
          </div>
          <Select value={projectRole} onValueChange={(v) => v && setProjectRole(v as OrgKind)}>
            <SelectTrigger className="w-40" aria-label="Роль на проекте">
              <SelectValue>{projectRole === "customer" ? "Заказчик" : "Подрядчик"}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="customer">Заказчик</SelectItem>
              <SelectItem value="contractor">Подрядчик</SelectItem>
            </SelectContent>
          </Select>
          <Button
            leftIcon={<Plus size={14} />}
            onClick={handleLink}
            disabled={selectedProject === null}
            loading={linkProject.isPending}
          >
            Дать доступ
          </Button>
        </div>
      </Surface>

      {links.length === 0 ? (
        <EmptyState title="Доступа к проектам нет" description="Выдайте организации доступ к проекту выше." />
      ) : (
        <Surface padding="none" className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Проект</TableHead>
                <TableHead>Роль на проекте</TableHead>
                <TableHead className="text-right">Действия</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {links.map((l) => (
                <TableRow key={l.project_id}>
                  <TableCell className="font-medium text-fg">{l.project_name}</TableCell>
                  <TableCell>
                    <OrgKindBadge kind={l.project_role} />
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      leftIcon={<Trash2 size={13} />}
                      onClick={() => setUnlinkTarget(l)}
                    >
                      Снять доступ
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Surface>
      )}

      <AlertDialog open={unlinkTarget !== null} onOpenChange={(o) => !o && setUnlinkTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Снять доступ к проекту?</AlertDialogTitle>
            <AlertDialogDescription>
              Организация потеряет доступ к проекту «{unlinkTarget?.project_name}».
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Отмена</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={confirmUnlink}>
              Снять доступ
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
