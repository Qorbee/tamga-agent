"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";

import { InlineText } from "@/components/inline-text";
import { PlacementRows } from "@/components/placement-rows";
import { T } from "@/components/translated";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, getProject, listAllPlacements, listProjectParties } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";
import { useCanEditProjects } from "@/lib/use-role";

import { ProjectMenu } from "./project-actions";
import { partiesKey, ProjectProperties, useProjectUpdate } from "./project-properties";

// How long a project read ahead of its opening is fresh enough not to be read again for it.
const READ_AHEAD = 30_000;

const projectKey = (id: string) => ["project", id] as const;
const placementsKey = (id: string) => ["placements", { projects: [id] }] as const;

function useProject(id: string) {
  return useQuery({ queryKey: projectKey(id), queryFn: () => getProject(id), retry: false });
}

/**
 * Reads a project, its parties and its conversations before it is opened, while the pointer or
 * focus is on its row, so the canvas opens on them instead of on a placeholder that leaves and
 * takes the scrollbar with it.
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
      queryKey: partiesKey(id),
      queryFn: () => listProjectParties(id),
      staleTime: READ_AHEAD,
    });
    void queryClient.prefetchQuery({
      queryKey: placementsKey(id),
      queryFn: () => listAllPlacements({ project_id: [id] }),
      staleTime: READ_AHEAD,
    });
  };
}

/** The project's number, for the canvas's title bar: its name heads the panel itself. */
export function ProjectTitle({ id }: { id: string }) {
  const project = useProject(id);
  if (!project.data) return <Skeleton className="h-4 w-20" />;
  return <span className="truncate font-mono">{project.data.number}</span>;
}

/** The project's one menu, for the canvas's title bar. */
export function ProjectTitleMenu({ id }: { id: string }) {
  const project = useProject(id);
  return project.data ? <ProjectMenu project={project.data} /> : null;
}

/**
 * A project in the canvas: its name and its properties, each edited where it is shown, and the
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

  const held = (placements.data ?? []).filter((placement) => placement.conversation);
  const waitingFirst = [...held].sort(
    (a, b) => Number(b.suggested_links > 0) - Number(a.suggested_links > 0),
  );

  return (
    // One column no wider than the canvas: a long word wraps instead of widening the grid.
    <div className="grid grid-cols-1 gap-6 p-4 sm:p-5">
      <header className="grid grid-cols-1 gap-4">
        <ProjectName project={project.data} />
        <ProjectProperties project={project.data} />
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

/** The project's name, large, renamed where it is: what the owner names it, later mail never
 * renames. */
function ProjectName({ project }: { project: NonNullable<ReturnType<typeof useProject>["data"]> }) {
  const { t } = useI18n();
  const canEdit = useCanEditProjects();
  const { save, fieldError } = useProjectUpdate(project);
  return (
    <div className="grid gap-1">
      <h3 className="text-xl font-medium tracking-tight text-pretty">
        <InlineText
          value={project.name}
          label={t.project.rename}
          maxLength={300}
          pending={save.isPending}
          error={fieldError("name")}
          onSave={canEdit ? (name) => save.mutate({ name }) : undefined}
        >
          <T>{project.name}</T>
        </InlineText>
      </h3>
      {project.field_sources.name === "owner" ? (
        <span className="text-muted-foreground text-xs">{t.board.ownerEdited}</span>
      ) : null}
    </div>
  );
}
