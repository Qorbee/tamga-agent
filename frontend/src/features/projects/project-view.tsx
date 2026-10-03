"use client";

import { HugeiconsIcon } from "@hugeicons/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { JobFacts, PlacementRows } from "@/components/placement-rows";
import { T, TranslateToggle } from "@/components/translated";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, getProject, listAllPlacements } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";

import { ProjectActions, STATUS_ICONS } from "./project-actions";

// How long a project read ahead of its opening is fresh enough not to be read again for it.
const READ_AHEAD = 30_000;

const projectKey = (id: string) => ["project", id] as const;
const placementsKey = (id: string) => ["placements", { projects: [id] }] as const;

function useProject(id: string) {
  return useQuery({ queryKey: projectKey(id), queryFn: () => getProject(id), retry: false });
}

/**
 * Reads a project and its conversations before it is opened, while the pointer or focus is on its
 * row, so the canvas opens on them instead of on a placeholder that leaves and takes the
 * scrollbar with it.
 */
export function useReadProjectAhead() {
  const queryClient = useQueryClient();
  return (id: string) => {
    void queryClient.prefetchQuery({
      queryKey: projectKey(id),
      queryFn: () => getProject(id),
      staleTime: READ_AHEAD,
    });
    void queryClient.prefetchQuery({
      queryKey: placementsKey(id),
      queryFn: () => listAllPlacements({ project_id: [id] }),
      staleTime: READ_AHEAD,
    });
  };
}

/** The project's number and name, for the canvas's title bar. */
export function ProjectTitle({ id }: { id: string }) {
  const project = useProject(id);
  if (!project.data) return <Skeleton className="h-4 w-48" />;
  return (
    <>
      <span className="text-muted-foreground shrink-0 font-mono text-xs">{project.data.number}</span>
      <span className="truncate">
        <T>{project.data.name}</T>
      </span>
    </>
  );
}

/**
 * A project in the canvas: what it is, where it stands, what was read of the job, and the
 * conversations tied to it, those that still wait for the owner first, each with why it is here.
 */
export function ProjectView({ id }: { id: string }) {
  const { t } = useI18n();
  const project = useProject(id);
  const placements = useQuery({
    queryKey: placementsKey(id),
    queryFn: () => listAllPlacements({ project_id: [id] }),
  });

  if (project.isError)
    return (
      <p className="text-muted-foreground p-5 text-sm" role="alert">
        {project.error instanceof ApiError && project.error.status === 404 ? t.project.notFound : t.errors.load}
      </p>
    );
  if (!project.data) return <Skeleton className="m-5 h-32 rounded-2xl" />;

  const { number, name, status, aliases } = project.data;
  const held = (placements.data ?? []).filter((placement) => placement.conversation);
  const waitingFirst = [...held].sort(
    (a, b) => Number(b.suggested_links > 0) - Number(a.suggested_links > 0),
  );

  return (
    // One column no wider than the canvas: a long word wraps instead of widening the grid.
    <div className="grid grid-cols-1 gap-6 p-4 sm:p-5">
      <header className="grid grid-cols-1 gap-2">
        <p className="text-muted-foreground flex flex-wrap items-baseline gap-x-2 font-mono text-xs">
          <span className="text-foreground">{number}</span>
          {aliases.length > 0 ? <span>{t.board.aliases(aliases.join(", "))}</span> : null}
        </p>
        <h3 className="text-xl font-medium tracking-tight text-pretty [overflow-wrap:anywhere]">
          <T>{name}</T>
        </h3>
        <div className="flex items-center gap-2">
          {status !== "rejected" ? (
            <span className="bg-secondary text-secondary-foreground inline-flex items-center gap-1.5 rounded-full py-1 ps-2 pe-2.5 text-xs font-medium">
              <HugeiconsIcon icon={STATUS_ICONS[status]} strokeWidth={1.75} className="size-3.5" aria-hidden />
              {t.board.columns[status]}
            </span>
          ) : null}
          <ProjectActions project={project.data} pencil />
          <TranslateToggle className="text-muted-foreground" />
        </div>
        <JobFacts project={project.data} className="mt-1" />
      </header>

      <section className="grid grid-cols-1 gap-2">
        <h4 className="flex items-baseline gap-2 text-sm font-medium">
          {t.project.conversations}
          <span className="text-muted-foreground tabular-nums">{placements.data ? held.length : ""}</span>
        </h4>
        {placements.isError ? (
          <p className="text-danger-foreground text-sm" role="alert">
            {t.errors.load}
          </p>
        ) : placements.isPending ? (
          <Skeleton className="h-24 rounded-2xl" />
        ) : held.length === 0 ? (
          <p className="text-muted-foreground text-sm">{t.project.noConversations}</p>
        ) : (
          <PlacementRows placements={waitingFirst} className="border-border overflow-hidden rounded-2xl border" />
        )}
      </section>
    </div>
  );
}
