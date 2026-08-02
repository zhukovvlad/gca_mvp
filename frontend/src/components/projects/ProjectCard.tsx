import { useState } from "react";
import { Link } from "react-router-dom";
import { Archive, Building2, MoreHorizontal, Pencil, Trash2 } from "lucide-react";

import { Button } from "@/components/ui-domain/Button";
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
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";

import { formatDate } from "@/lib/format";
import { useDeleteProject, useUpdateProject } from "@/services/queries";
import type { Project } from "@/types/project";

interface ProjectCardProps {
  project: Project;
}

export function ProjectCard({ project }: ProjectCardProps) {
  const [editOpen, setEditOpen] = useState(false);
  const [editName, setEditName] = useState(project.name);
  const [editContract, setEditContract] = useState(project.contract_number ?? "");

  const [deleteOpen, setDeleteOpen] = useState(false);

  const updateProject = useUpdateProject();
  const deleteProject = useDeleteProject();

  function handleOpenEdit() {
    setEditName(project.name);
    setEditContract(project.contract_number ?? "");
    setEditOpen(true);
  }

  function handleSaveEdit() {
    if (!editName.trim() || updateProject.isPending) return;
    updateProject.mutate(
      {
        id: project.id,
        input: {
          name: editName.trim(),
          contract_number: editContract.trim() || null,
        },
      },
      { onSuccess: () => setEditOpen(false) }
    );
  }

  function handleConfirmDelete() {
    deleteProject.mutate(project.id, {
      onSuccess: () => setDeleteOpen(false),
    });
  }

  return (
    <div className="relative">
      <Link
        to={`/projects/${project.id}`}
        className="group flex flex-col rounded-lg border border-border-subtle bg-surface px-5 py-4 transition-colors duration-150 hover:border-border-default hover:bg-surface-hover"
      >
        <div className="flex items-start gap-3">
          <div
            className="grid h-10 w-10 shrink-0 place-items-center rounded-md bg-accent-soft text-accent-text"
            aria-hidden
          >
            <Building2 size={18} />
          </div>
          <div className="min-w-0 flex-1 pr-8">
            <div className="truncate text-md font-medium text-fg">
              {project.name}
            </div>
            <div className="mt-0.5 truncate text-xs text-fg-secondary">
              {project.contract_number
                ? `Договор № ${project.contract_number}`
                : "Договор не указан"}
            </div>
          </div>
        </div>
        <div className="mt-4 border-t border-border-subtle pt-3 text-xs text-fg-tertiary">
          Создан {formatDate(project.created_at)}
        </div>
      </Link>

      {/* Actions menu — sibling of <Link>, not a child, чтобы клик не вёл на страницу проекта */}
      <div className="absolute right-3 top-3">
        <DropdownMenu>
          <DropdownMenuTrigger
            type="button"
            aria-label="Действия с объектом"
            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-fg-tertiary hover:bg-surface-hover hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30"
          >
            <MoreHorizontal size={16} />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" sideOffset={6} className="min-w-56 p-1.5">
            <DropdownMenuItem
              className="flex items-center gap-2.5 whitespace-nowrap px-2.5 py-2"
              onClick={handleOpenEdit}
            >
              <Pencil size={14} />
              Редактировать
            </DropdownMenuItem>
            <DropdownMenuItem
              className="flex items-center gap-2.5 whitespace-nowrap px-2.5 py-2"
              disabled
            >
              <Archive size={14} />
              В архив
              <span className="ml-auto pl-3 text-xs text-fg-tertiary">скоро</span>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="flex items-center gap-2.5 whitespace-nowrap px-2.5 py-2"
              variant="destructive"
              onClick={() => setDeleteOpen(true)}
            >
              <Trash2 size={14} />
              Удалить объект
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* Edit dialog */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Редактировать объект</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div className="flex flex-col gap-1">
              <label className="text-xs text-fg-secondary">Название *</label>
              <Input
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                placeholder="ЖК «Северный», корпус 1"
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleSaveEdit();
                }}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-fg-secondary">Номер договора</label>
              <Input
                value={editContract}
                onChange={(e) => setEditContract(e.target.value)}
                placeholder="Опционально"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setEditOpen(false)}>
              Отмена
            </Button>
            <Button
              onClick={handleSaveEdit}
              loading={updateProject.isPending}
              disabled={!editName.trim()}
            >
              Сохранить
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirmation */}
      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Удалить объект «{project.name}»?</AlertDialogTitle>
            <AlertDialogDescription>
              Будут удалены все загруженные счета, инвойсы и базовые цены. Это действие нельзя отменить.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Отмена</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={handleConfirmDelete}
              disabled={deleteProject.isPending}
            >
              {deleteProject.isPending ? "Удаление…" : "Удалить"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
