"use client";

import {
  Archive02Icon,
  FolderAddIcon,
  FolderOpenIcon,
  MoreHorizontalIcon,
  PencilEdit02Icon,
  Tick02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { EditProjectDialog, useCanEditProjects } from "@/components/edit-project-dialog";
import { Hint } from "@/components/hint";
import { IconButton } from "@/components/icon-button";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { updateProject, type Project, type ProjectStatus, type ProjectUpdate } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";

export const STATUS_ICONS: Record<Exclude<ProjectStatus, "rejected">, IconSvgElement> = {
  proposed: FolderAddIcon,
  active: FolderOpenIcon,
  draft: PencilEdit02Icon,
  closed: Archive02Icon,
};

type Move = NonNullable<ProjectUpdate["status"]>;

/** The owner's moves from each status (docs/architecture/09, States): the one that carries the
 * project forward is a tick, the ones that end it sit in the menu, a tap further away. */
const FORWARD: Partial<Record<ProjectStatus, Move>> = { draft: "active", proposed: "active" };
const ENDING: Partial<Record<ProjectStatus, Move>> = { proposed: "rejected", active: "closed" };

/**
 * A project's moves and its edit, wherever a project is shown: the tick that carries it forward,
 * and a menu with Edit and the move that ends it. `pencil` puts Edit beside them as an icon too,
 * for the project's own panel.
 */
export function ProjectActions({ project, pencil = false }: { project: Project; pencil?: boolean }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const canEdit = useCanEditProjects();
  const [editing, setEditing] = useState(false);
  const move = useMutation({
    mutationFn: (status: Move) => updateProject(project.id, { status }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      void queryClient.invalidateQueries({ queryKey: ["project", project.id] });
    },
  });
  const forward = FORWARD[project.status];
  const ending = ENDING[project.status];
  const labels: Record<Move, string> = {
    active: project.status === "draft" ? t.board.activate : t.board.confirm,
    rejected: t.board.reject,
    closed: t.board.close,
  };
  if (!forward && !ending && !canEdit) return null;

  return (
    <span className="flex shrink-0 items-center gap-0.5">
      {move.isError ? (
        <span className="text-danger-foreground me-1 text-xs" role="alert">
          {t.errors.generic}
        </span>
      ) : null}
      {forward ? (
        <IconButton
          icon={Tick02Icon}
          label={labels[forward]}
          disabled={move.isPending}
          onClick={() => move.mutate(forward)}
        />
      ) : null}
      {pencil && canEdit ? (
        <IconButton
          icon={PencilEdit02Icon}
          label={t.project.editTitle}
          className="text-muted-foreground"
          onClick={() => setEditing(true)}
        />
      ) : null}
      {ending || canEdit ? (
        <DropdownMenu modal={false}>
          <Hint label={t.board.actions}>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                className="text-muted-foreground"
                aria-label={t.board.actions}
                disabled={move.isPending}
              >
                <HugeiconsIcon icon={MoreHorizontalIcon} strokeWidth={1.75} />
              </Button>
            </DropdownMenuTrigger>
          </Hint>
          <DropdownMenuContent align="end">
            {canEdit ? <DropdownMenuItem onSelect={() => setEditing(true)}>{t.project.edit}</DropdownMenuItem> : null}
            {ending ? <DropdownMenuItem onSelect={() => move.mutate(ending)}>{labels[ending]}</DropdownMenuItem> : null}
          </DropdownMenuContent>
        </DropdownMenu>
      ) : null}
      {canEdit ? <EditProjectDialog project={project} open={editing} onOpenChange={setEditing} /> : null}
    </span>
  );
}
