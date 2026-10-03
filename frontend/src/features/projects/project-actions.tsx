"use client";

import {
  Archive02Icon,
  ArrowDown01Icon,
  FolderAddIcon,
  FolderOpenIcon,
  MoreHorizontalIcon,
  PencilEdit02Icon,
  Tick02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Hint } from "@/components/hint";
import { IconButton } from "@/components/icon-button";
import { MergeProjectDialog } from "@/components/merge-project-dialog";
import { useTranslate } from "@/components/translated";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { updateProject, type Project, type ProjectStatus, type ProjectUpdate } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";
import { useCanEditProjects, useIsOwner } from "@/lib/use-role";
import { cn } from "@/lib/utils";

export const STATUS_ICONS: Record<Exclude<ProjectStatus, "rejected">, IconSvgElement> = {
  proposed: FolderAddIcon,
  active: FolderOpenIcon,
  draft: PencilEdit02Icon,
  closed: Archive02Icon,
};

type Move = NonNullable<ProjectUpdate["status"]>;

/** The owner's moves from each status (docs/architecture/09, States): the one that carries the
 * project forward is a tick, the ones that end it sit in a menu, a tap further away. */
const FORWARD: Partial<Record<ProjectStatus, Move>> = { draft: "active", proposed: "active" };
const ENDING: Partial<Record<ProjectStatus, Move>> = { proposed: "rejected", active: "closed" };

/** Moving a project to another status, and the words for each move from where it is. */
function useMove(project: Project) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const move = useMutation({
    mutationFn: (status: Move) => updateProject(project.id, { status }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      void queryClient.invalidateQueries({ queryKey: ["project", project.id] });
    },
  });
  const labels: Record<Move, string> = {
    active: project.status === "draft" ? t.board.activate : t.board.confirm,
    rejected: t.board.reject,
    closed: t.board.close,
  };
  return { move, labels, forward: FORWARD[project.status], ending: ENDING[project.status] };
}

/**
 * A project's moves in a list: the tick that carries it forward, and a menu with Merge and the
 * move that ends it. Its name, number and days are edited in its own panel.
 */
export function ProjectActions({ project }: { project: Project }) {
  const { t } = useI18n();
  const canEdit = useCanEditProjects();
  const isOwner = useIsOwner();
  const [merging, setMerging] = useState(false);
  const { move, labels, forward, ending } = useMove(project);
  // A rejected project is merged into nothing.
  const canMerge = isOwner && project.status !== "rejected";
  if (!canEdit || (!forward && !ending && !canMerge)) return null;

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
      {ending || canMerge ? (
        <DropdownMenu modal={false}>
          <MenuTrigger label={t.board.actions} disabled={move.isPending} />
          <DropdownMenuContent align="end">
            {canMerge ? <DropdownMenuItem onSelect={() => setMerging(true)}>{t.project.merge}</DropdownMenuItem> : null}
            {ending ? <DropdownMenuItem onSelect={() => move.mutate(ending)}>{labels[ending]}</DropdownMenuItem> : null}
          </DropdownMenuContent>
        </DropdownMenu>
      ) : null}
      {canMerge ? <MergeProjectDialog project={project} open={merging} onOpenChange={setMerging} /> : null}
    </span>
  );
}

/**
 * The project's one menu in its panel's title bar: Merge, and Tamga's notes in the reader's
 * language. Its status moves sit on the status itself.
 */
export function ProjectMenu({ project }: { project: Project }) {
  const { t } = useI18n();
  const isOwner = useIsOwner();
  const translate = useTranslate();
  const [merging, setMerging] = useState(false);
  const canMerge = isOwner && project.status !== "rejected";

  return (
    <>
      <DropdownMenu modal={false}>
        <MenuTrigger label={t.board.actions} />
        <DropdownMenuContent align="end" className="w-64">
          {canMerge ? <DropdownMenuItem onSelect={() => setMerging(true)}>{t.project.merge}</DropdownMenuItem> : null}
          <DropdownMenuCheckboxItem checked={translate.on} onCheckedChange={translate.toggle}>
            {t.translate.show}
          </DropdownMenuCheckboxItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {canMerge ? <MergeProjectDialog project={project} open={merging} onOpenChange={setMerging} /> : null}
    </>
  );
}

const CHIP = "bg-secondary text-secondary-foreground inline-flex h-7 items-center gap-1.5 rounded-full ps-2 text-xs font-medium";

/**
 * Where the project stands, as a chip; for whoever may change it, the chip opens the moves from
 * there: confirm or reject a proposal, start a draft, close an active project.
 */
export function StatusMenu({ project }: { project: Project }) {
  const { t } = useI18n();
  const canEdit = useCanEditProjects();
  const { move, labels, forward, ending } = useMove(project);
  const moves = [forward, ending].filter((each): each is Move => each !== undefined);
  const status = project.status;
  const chip = (
    <>
      {status !== "rejected" ? (
        <HugeiconsIcon icon={STATUS_ICONS[status]} strokeWidth={1.75} className="size-3.5" aria-hidden />
      ) : null}
      {status === "rejected" ? t.board.rejected : t.board.columns[status]}
    </>
  );

  if (!canEdit || moves.length === 0) return <span className={cn(CHIP, "pe-2.5")}>{chip}</span>;
  return (
    <span className="flex flex-wrap items-center gap-2">
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className={cn(CHIP, "hover:bg-accent focus-visible:ring-ring/50 pe-1.5 outline-none focus-visible:ring-2")}
            disabled={move.isPending}
            aria-label={`${t.board.changeStatus}: ${status === "rejected" ? t.board.rejected : t.board.columns[status]}`}
          >
            {chip}
            <HugeiconsIcon icon={ArrowDown01Icon} strokeWidth={1.75} className="text-muted-foreground size-3.5" aria-hidden />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          {moves.map((each) => (
            <DropdownMenuItem key={each} onSelect={() => move.mutate(each)}>
              {labels[each]}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      {move.isError ? (
        <span className="text-danger-foreground text-xs" role="alert">
          {t.errors.generic}
        </span>
      ) : null}
    </span>
  );
}

function MenuTrigger({ label, disabled = false }: { label: string; disabled?: boolean }) {
  return (
    <Hint label={label}>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" className="text-muted-foreground" aria-label={label} disabled={disabled}>
          <HugeiconsIcon icon={MoreHorizontalIcon} strokeWidth={1.75} />
        </Button>
      </DropdownMenuTrigger>
    </Hint>
  );
}
