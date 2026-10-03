"use client";

import {
  Cancel01Icon,
  Folder01Icon,
  FolderAddIcon,
  HelpCircleIcon,
  MoreHorizontalIcon,
  Tick02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { CanvasLink } from "@/components/canvas-link";
import { EditProjectDialog, useCanEditProjects, useIsOwner } from "@/components/edit-project-dialog";
import { Hint } from "@/components/hint";
import { IconButton } from "@/components/icon-button";
import { MergeProjectDialog } from "@/components/merge-project-dialog";
import { JobFacts, PlacementRows } from "@/components/placement-rows";
import { T } from "@/components/translated";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  decidePlacement,
  getProject,
  listProjects,
  updateProject,
  type Placement,
  type Project,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";
import { conversationName } from "@/lib/mail-text";
import { useInvalidatePlacements } from "@/lib/use-invalidate-placements";
import { cn } from "@/lib/utils";

/** Projects an item can still be placed in. */
export const OPEN_PROJECTS = ["draft", "proposed", "active"] as const;
/** Conversations a card lists before "Show all". */
const SHOWN = 4;

/**
 * What waits for the owner about one project, as the Inbox lists it and as Tamga puts it under
 * what it writes in a chat: the project, the conversations placed in it, each with its reason,
 * and the owner's answer for all of them. Once answered it says so and offers nothing.
 */
export function ProjectCard({ project, waiting }: { project: Project; waiting: Placement[] }) {
  const { t } = useI18n();
  const invalidate = useInvalidatePlacements();
  const [all, setAll] = useState(false);
  const [editing, setEditing] = useState(false);
  const [merging, setMerging] = useState(false);
  const canEdit = useCanEditProjects();
  const isOwner = useIsOwner();
  const proposed = project.status === "proposed";
  const rejected = project.status === "rejected";
  // A card in a chat stays after its answer, so it may list conversations that no longer wait.
  const unanswered = waiting.filter((placement) => placement.suggested_links > 0);
  // A rejected project keeps the links that were suggested to it, and none of them is asked for.
  const waits = !rejected && (proposed || unanswered.length > 0);
  const answer = useMutation({
    mutationFn: async (confirm: boolean) => {
      // A proposal is answered as a whole: confirmed, it becomes a project the conversations are
      // then tied to; rejected, nothing is tied to it.
      if (proposed) await updateProject(project.id, { status: confirm ? "active" : "rejected" });
      if (confirm || !proposed)
        await Promise.all(
          unanswered.map((placement) =>
            decidePlacement(placement.id, { links: confirm ? "confirmed" : "rejected" }),
          ),
        );
    },
    onSettled: invalidate,
  });
  // What says a card was confirmed is a conversation still tied to its project: when every one
  // was rejected none is left, and the card then claims no answer.
  const listed = rejected ? [] : waiting;
  const shown = all ? listed : listed.slice(0, SHOWN);

  return (
    <article className="border-border bg-card overflow-hidden rounded-2xl border">
      {/* On a phone the name takes the card's whole width, under the icon and the answer. */}
      <header className="grid grid-cols-[auto_1fr_auto] items-center gap-x-3 gap-y-2 p-4">
        <span className="bg-secondary text-muted-foreground grid size-9 place-items-center rounded-xl">
          <HugeiconsIcon icon={proposed ? FolderAddIcon : Folder01Icon} strokeWidth={1.75} className="size-5" aria-hidden />
        </span>
        <p className="text-muted-foreground flex min-w-0 items-baseline gap-2 text-xs">
          <span className="font-mono">{project.number}</span>
          {proposed ? <span className="truncate">{t.inbox.newProject}</span> : null}
        </p>
        <div className="flex items-center gap-1">
          {waits ? (
            <IconButton
              icon={Tick02Icon}
              variant="default"
              size="icon"
              label={proposed ? t.inbox.confirmProject : t.inbox.confirmAll}
              disabled={answer.isPending}
              onClick={() => answer.mutate(true)}
            />
          ) : rejected || listed.length > 0 ? (
            <Answered rejected={rejected} />
          ) : null}
          {/* Rejecting a whole card is not undone, so it sits a tap further away than the tick. */}
          {!rejected && (waits || canEdit || isOwner) ? (
            <DropdownMenu modal={false}>
              <Hint label={t.board.actions}>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="text-muted-foreground"
                    aria-label={t.board.actions}
                    disabled={answer.isPending}
                  >
                    <HugeiconsIcon icon={MoreHorizontalIcon} strokeWidth={1.75} />
                  </Button>
                </DropdownMenuTrigger>
              </Hint>
              <DropdownMenuContent align="end">
                {canEdit ? <DropdownMenuItem onSelect={() => setEditing(true)}>{t.project.edit}</DropdownMenuItem> : null}
                {isOwner ? <DropdownMenuItem onSelect={() => setMerging(true)}>{t.project.merge}</DropdownMenuItem> : null}
                {waits ? (
                  <DropdownMenuItem onSelect={() => answer.mutate(false)}>
                    {proposed ? t.inbox.rejectProject : t.inbox.rejectAll}
                  </DropdownMenuItem>
                ) : null}
              </DropdownMenuContent>
            </DropdownMenu>
          ) : null}
          {canEdit ? <EditProjectDialog project={project} open={editing} onOpenChange={setEditing} /> : null}
          {isOwner ? <MergeProjectDialog project={project} open={merging} onOpenChange={setMerging} /> : null}
        </div>
        <div className="col-span-3 min-w-0 sm:col-span-2 sm:col-start-2 sm:-mt-3">
          <CanvasLink
            view={{ kind: "project", id: project.id }}
            className="decoration-muted-foreground/60 text-base font-medium text-pretty break-words underline-offset-4 hover:underline"
          >
            <T>{project.name}</T>
          </CanvasLink>
          <JobFacts project={project} className="mt-1.5" />
        </div>
      </header>
      {answer.isError ? (
        <p className="text-danger-foreground px-4 pb-3 text-sm" role="alert">
          {t.errors.generic}
        </p>
      ) : null}
      {shown.length > 0 ? <PlacementRows placements={shown} className="border-border border-t" /> : null}
      {listed.length > shown.length ? (
        <div className="border-border border-t p-1">
          <Button variant="ghost" size="sm" className="text-muted-foreground w-full" onClick={() => setAll(true)}>
            {t.inbox.showAll(listed.length)}
          </Button>
        </div>
      ) : null}
    </article>
  );
}

/** The answer a card was given, in place of the buttons that gave it. */
function Answered({ rejected }: { rejected: boolean }) {
  const { t } = useI18n();
  const label = rejected ? t.inbox.rejected : t.inbox.confirmed;
  return (
    <Hint label={label}>
      <span className={cn("grid size-9 place-items-center", rejected ? "text-muted-foreground" : "text-success-foreground")}>
        <HugeiconsIcon icon={rejected ? Cancel01Icon : Tick02Icon} strokeWidth={1.75} className="size-5" aria-label={label} />
      </span>
    </Hint>
  );
}

/** Conversations that fit more than one project: the owner names the one. */
export function Questions({ placements }: { placements: Placement[] }) {
  const { t } = useI18n();
  const projects = useQuery({
    queryKey: ["projects", { open: true }],
    queryFn: () => listProjects({ status: [...OPEN_PROJECTS] }),
  });
  const byId = new Map(projects.data?.items.map((project) => [project.id, project]));

  return (
    <article className="border-border bg-card overflow-hidden rounded-2xl border">
      <header className="flex items-center gap-3 p-4">
        <span className="bg-secondary text-muted-foreground grid size-9 shrink-0 place-items-center rounded-xl">
          <HugeiconsIcon icon={HelpCircleIcon} strokeWidth={1.75} className="size-5" aria-hidden />
        </span>
        <h3 className="text-base font-medium">{t.inbox.which}</h3>
      </header>
      {projects.isError ? (
        // Without the projects' names the answers are blank buttons: say so instead.
        <p className="text-danger-foreground border-border border-t px-4 py-3 text-sm" role="alert">
          {t.errors.load}
        </p>
      ) : (
        <ul className="divide-border border-border divide-y border-t">
          {placements.map((placement) => (
            <Question key={placement.id} placement={placement} projects={byId} />
          ))}
        </ul>
      )}
    </article>
  );
}

function Question({ placement, projects }: { placement: Placement; projects: Map<string, Project> }) {
  const { t } = useI18n();
  const invalidate = useInvalidatePlacements();
  const choose = useMutation({
    mutationFn: (projectId: string) => decidePlacement(placement.id, { project_id: projectId }),
    onSuccess: invalidate,
  });
  const conversation = placement.conversation!;
  // A card in a chat stays after its answer, and then says where the conversation went.
  const asked = placement.outcome === "asked";
  const among = placement.project_id ? projects.get(placement.project_id) : undefined;
  // The project it went to may since have been closed, and is then not among the open ones.
  const elsewhere = useQuery({
    queryKey: ["project", placement.project_id],
    queryFn: () => getProject(placement.project_id!),
    enabled: !asked && placement.project_id != null && !among,
    retry: false,
  });
  const placed = among ?? elsewhere.data;

  return (
    <li className="grid gap-2 px-4 py-3">
      <CanvasLink
        view={{ kind: "mail", id: conversation.latest.id }}
        className="decoration-muted-foreground/60 min-w-0 underline-offset-4 hover:underline"
      >
        <span className="block truncate text-sm font-medium">
          {conversation.subject ? conversationName(conversation.subject) : t.inbox.noSubject}
        </span>
        {placement.reason ? (
          <span className="text-muted-foreground line-clamp-2 block text-sm text-pretty">
            <T>{placement.reason}</T>
          </span>
        ) : null}
      </CanvasLink>
      {asked ? null : (
        <p className="text-muted-foreground flex min-w-0 items-center gap-2 text-sm">
          <HugeiconsIcon
            icon={placement.project_id ? Tick02Icon : Cancel01Icon}
            strokeWidth={1.75}
            className={cn("size-4 shrink-0", placement.project_id && "text-success-foreground")}
            aria-hidden
          />
          {placed ? (
            <CanvasLink
              view={{ kind: "project", id: placed.id }}
              className="decoration-muted-foreground/60 flex min-w-0 items-baseline gap-2 underline-offset-4 hover:underline"
            >
              <span className="shrink-0 font-mono text-xs">{placed.number}</span>
              <span className="text-foreground min-w-0 break-words">
                <T>{placed.name}</T>
              </span>
            </CanvasLink>
          ) : placement.project_id ? (
            t.inbox.confirmed
          ) : (
            t.inbox.unplaced
          )}
        </p>
      )}
      <div className={cn("flex flex-wrap gap-2", !asked && "hidden")}>
        {placement.alternatives.map((id) => {
          const project = projects.get(id);
          return (
            <Button
              key={id}
              variant="outline"
              size="sm"
              className="h-auto max-w-full min-w-0 justify-start py-1.5 whitespace-normal"
              disabled={choose.isPending || !project}
              onClick={() => choose.mutate(id)}
            >
              <span className="text-muted-foreground shrink-0 font-mono text-xs">{project?.number ?? "…"}</span>
              <span className="min-w-0 text-start break-words">{project ? <T>{project.name}</T> : null}</span>
            </Button>
          );
        })}
      </div>
      {choose.isError ? (
        <p className="text-danger-foreground text-xs" role="alert">
          {t.errors.generic}
        </p>
      ) : null}
    </li>
  );
}
