"use client";

import {
  Folder01Icon,
  FolderAddIcon,
  HelpCircleIcon,
  MoreHorizontalIcon,
  Tick02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { CanvasLink } from "@/components/canvas-link";
import { IconButton } from "@/components/icon-button";
import { JobFacts, PlacementRows, useInvalidatePlacements } from "@/components/placement-rows";
import { T } from "@/components/translated";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import {
  decidePlacement,
  listAllPlacements,
  listProjects,
  updateProject,
  type Placement,
  type Project,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";
import { conversationName } from "@/lib/mail-text";

/** Projects an item can still be placed in. */
const OPEN = ["draft", "proposed", "active"] as const;
/** Conversations a card lists before "Show all". */
const SHOWN = 4;
/** Project cards shown before "Show more". */
const CARDS = 20;
/** Projects one request for placements names: under the API's limit for a repeated parameter. */
const NAMED = 100;

/**
 * What waits for the owner, a card per project instead of a row per mail: the conversations Tamga
 * placed in it, each with its reason, answered one by one or all at once. Then the conversations
 * that fit more than one project, for the owner to say which. Shown only while something waits.
 */
export function Waiting() {
  const { t } = useI18n();
  const [limit, setLimit] = useState(CARDS);
  const projects = useQuery({
    queryKey: ["projects", { suggested: true, limit }],
    queryFn: () => listProjects({ status: [...OPEN], suggested: true, limit }),
    placeholderData: keepPreviousData,
  });
  const ids = projects.data?.items.map((project) => project.id) ?? [];
  // Every placement of the cards shown, however many: a card's tick answers for all it lists.
  const placements = useQuery({
    queryKey: ["placements", { projects: ids }],
    queryFn: async () => {
      const groups = [];
      for (let start = 0; start < ids.length; start += NAMED)
        groups.push(listAllPlacements({ project_id: ids.slice(start, start + NAMED) }));
      return (await Promise.all(groups)).flat();
    },
    enabled: ids.length > 0,
    placeholderData: keepPreviousData,
  });
  const asked = useQuery({
    queryKey: ["placements", { outcome: "asked" }],
    queryFn: () => listAllPlacements({ outcome: ["asked"] }),
  });

  const failed = projects.isError || placements.isError || asked.isError;
  const cards = (projects.data?.items ?? [])
    .map((project) => ({
      project,
      waiting: (placements.data ?? []).filter(
        (placement) =>
          placement.project_id === project.id && placement.suggested_links > 0 && placement.conversation,
      ),
    }))
    // Only mail is suggested today; a project whose waiting links are of another kind has no row to answer.
    .filter((card) => card.waiting.length > 0);
  const questions = asked.data?.filter((placement) => placement.conversation) ?? [];
  // An empty section is noise above the mail.
  if (!failed && !projects.isPending && cards.length === 0 && questions.length === 0) return null;

  return (
    <section className="grid gap-3">
      <h2 className="text-sm font-medium">{t.inbox.waiting}</h2>
      {failed ? (
        <p className="text-danger-foreground text-sm" role="alert">
          {t.errors.load}
        </p>
      ) : projects.isPending || (ids.length > 0 && placements.isPending) ? (
        <Skeleton className="h-40 rounded-2xl" />
      ) : (
        <>
          {cards.map(({ project, waiting }) => (
            <ProjectCard key={project.id} project={project} waiting={waiting} />
          ))}
          {projects.data.total > projects.data.items.length ? (
            <Button
              variant="ghost"
              size="sm"
              className="justify-self-center"
              onClick={() => setLimit(limit + CARDS)}
            >
              {t.inbox.showMore}
            </Button>
          ) : null}
          {questions.length > 0 ? <Questions placements={questions} /> : null}
        </>
      )}
    </section>
  );
}

function ProjectCard({ project, waiting }: { project: Project; waiting: Placement[] }) {
  const { t } = useI18n();
  const invalidate = useInvalidatePlacements();
  const [all, setAll] = useState(false);
  const proposed = project.status === "proposed";
  const answer = useMutation({
    mutationFn: async (confirm: boolean) => {
      // A proposal is answered as a whole: confirmed, it becomes a project the conversations are
      // then tied to; rejected, nothing is tied to it.
      if (proposed) await updateProject(project.id, { status: confirm ? "active" : "rejected" });
      if (confirm || !proposed)
        await Promise.all(
          waiting.map((placement) =>
            decidePlacement(placement.id, { links: confirm ? "confirmed" : "rejected" }),
          ),
        );
    },
    onSettled: invalidate,
  });
  const shown = all ? waiting : waiting.slice(0, SHOWN);

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
          <IconButton
            icon={Tick02Icon}
            variant="default"
            size="icon"
            label={proposed ? t.inbox.confirmProject : t.inbox.confirmAll}
            disabled={answer.isPending}
            onClick={() => answer.mutate(true)}
          />
          {/* Rejecting a whole card is not undone, so it sits a tap further away than the tick. */}
          <DropdownMenu modal={false}>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="text-muted-foreground"
                aria-label={t.board.actions}
                title={t.board.actions}
                disabled={answer.isPending}
              >
                <HugeiconsIcon icon={MoreHorizontalIcon} strokeWidth={1.75} />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => answer.mutate(false)}>
                {proposed ? t.inbox.rejectProject : t.inbox.rejectAll}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        <div className="col-span-3 min-w-0 sm:col-span-2 sm:col-start-2 sm:-mt-3">
          <CanvasLink
            view={{ kind: "project", id: project.id }}
            className="decoration-muted-foreground/60 text-base font-medium text-pretty break-words underline-offset-4 hover:underline"
          >
            <T>{project.name}</T>
          </CanvasLink>
          <JobFacts placements={waiting} className="mt-1.5" />
        </div>
      </header>
      {answer.isError ? (
        <p className="text-danger-foreground px-4 pb-3 text-sm" role="alert">
          {t.errors.generic}
        </p>
      ) : null}
      <PlacementRows placements={shown} className="border-border border-t" />
      {waiting.length > shown.length ? (
        <div className="border-border border-t p-1">
          <Button variant="ghost" size="sm" className="text-muted-foreground w-full" onClick={() => setAll(true)}>
            {t.inbox.showAll(waiting.length)}
          </Button>
        </div>
      ) : null}
    </article>
  );
}

/** Conversations that fit more than one project: the owner names the one. */
function Questions({ placements }: { placements: Placement[] }) {
  const { t } = useI18n();
  const projects = useQuery({
    queryKey: ["projects", { open: true }],
    queryFn: () => listProjects({ status: [...OPEN] }),
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
      <div className="flex flex-wrap gap-2">
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
