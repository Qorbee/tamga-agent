"use client";

import { HugeiconsIcon } from "@hugeicons/react";
import { useQuery } from "@tanstack/react-query";

import { JobFacts, PlacementRows } from "@/components/placement-rows";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, getProject, listAllPlacements } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";

import { ProjectActions, STATUS_ICONS } from "./project-actions";

function useProject(id: string) {
  return useQuery({ queryKey: ["project", id], queryFn: () => getProject(id), retry: false });
}

/** The project's number and name, for the canvas's title bar. */
export function ProjectTitle({ id }: { id: string }) {
  const project = useProject(id);
  if (!project.data) return <Skeleton className="h-4 w-48" />;
  return (
    <>
      <span className="text-muted-foreground shrink-0 font-mono text-xs">{project.data.number}</span>
      <span className="truncate">{project.data.name}</span>
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
    queryKey: ["placements", { projects: [id] }],
    queryFn: () => listAllPlacements({ project_id: [id] }),
  });

  if (project.isError)
    return (
      <p className="text-muted-foreground p-5 text-sm" role="alert">
        {project.error instanceof ApiError && project.error.status === 404 ? t.project.notFound : t.errors.load}
      </p>
    );
  if (!project.data) return <Skeleton className="m-5 h-32 rounded-2xl" />;

  const { name, status, aliases } = project.data;
  const held = (placements.data ?? []).filter((placement) => placement.conversation);
  const waitingFirst = [...held].sort(
    (a, b) => Number(b.suggested_links > 0) - Number(a.suggested_links > 0),
  );
  const reading = held.find((placement) => placement.reading)?.reading ?? null;

  return (
    <div className="grid gap-6 p-4 sm:p-5">
      <header className="grid gap-2">
        {aliases.length > 0 ? (
          <p className="text-muted-foreground font-mono text-xs">{t.board.aliases(aliases.join(", "))}</p>
        ) : null}
        <h3 className="text-xl font-medium tracking-tight text-pretty break-words">{name}</h3>
        <div className="flex items-center gap-2">
          {status !== "rejected" ? (
            <span className="bg-secondary text-secondary-foreground inline-flex items-center gap-1.5 rounded-full py-1 ps-2 pe-2.5 text-xs font-medium">
              <HugeiconsIcon icon={STATUS_ICONS[status]} strokeWidth={1.75} className="size-3.5" aria-hidden />
              {t.board.columns[status]}
            </span>
          ) : null}
          <ProjectActions project={project.data} />
        </div>
        <JobFacts reading={reading} className="mt-1" />
      </header>

      <section className="grid gap-2">
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
