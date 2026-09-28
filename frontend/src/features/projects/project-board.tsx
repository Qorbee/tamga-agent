"use client";

import { MoreHorizontalIcon, Search01Icon, TruckIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useDeferredValue, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  listProjects,
  listShipments,
  updateProject,
  type Project,
  type ProjectStatus,
  type ProjectUpdate,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

import { NewProjectDialog } from "./new-project-dialog";

/** Rejected proposals are not shown: the board is the work in hand. */
const COLUMNS = ["draft", "proposed", "active", "closed"] as const satisfies readonly ProjectStatus[];

/** The owner's moves from each status (docs/architecture/09, States). */
const MOVES: Partial<Record<ProjectStatus, NonNullable<ProjectUpdate["status"]>[]>> = {
  draft: ["active"],
  proposed: ["active", "rejected"],
  active: ["closed"],
};

export function ProjectBoard() {
  const { t } = useI18n();
  const [search, setSearch] = useState("");
  const q = useDeferredValue(search.trim());
  const projects = useQuery({
    queryKey: ["projects", { q }],
    queryFn: () => listProjects({ status: [...COLUMNS], q }),
  });
  const ids = projects.data?.items.map((project) => project.id) ?? [];
  const shipments = useQuery({
    queryKey: ["shipments", ids],
    queryFn: () => listShipments(ids),
    enabled: ids.length > 0,
  });
  const trucks = new Map<string, number>();
  for (const shipment of shipments.data ?? [])
    trucks.set(shipment.project_id, (trucks.get(shipment.project_id) ?? 0) + 1);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 px-3 pb-6 md:px-6">
      <div className="flex items-center gap-2">
        <div className="relative max-w-xs flex-1">
          <HugeiconsIcon
            icon={Search01Icon}
            strokeWidth={1.75}
            className="text-muted-foreground pointer-events-none absolute start-3 top-1/2 size-4 -translate-y-1/2"
          />
          <Input
            type="search"
            aria-label={t.board.search}
            placeholder={t.board.search}
            className="ps-9"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <div className="ms-auto">
          <NewProjectDialog />
        </div>
      </div>

      {projects.isError ? (
        <p className="text-danger-foreground text-sm" role="alert">
          {t.errors.load}
        </p>
      ) : (
        <div className="-mx-3 flex min-h-0 flex-1 snap-x scroll-px-3 gap-3 overflow-x-auto px-3 md:mx-0 md:grid md:grid-cols-4 md:px-0">
          {COLUMNS.map((status) => {
            const items = projects.data?.items.filter((project) => project.status === status) ?? [];
            return (
              <section
                key={status}
                aria-label={t.board.columns[status]}
                className="bg-secondary/60 flex w-72 shrink-0 snap-start flex-col gap-2 rounded-2xl p-2 md:w-auto"
              >
                <h2 className="text-muted-foreground flex items-center justify-between px-2 pt-1 text-xs font-medium">
                  {t.board.columns[status]}
                  <span className="tabular-nums">{projects.data ? items.length : ""}</span>
                </h2>
                {projects.isPending ? (
                  <Skeleton className="h-20 rounded-xl" />
                ) : items.length === 0 ? (
                  <p className="text-muted-foreground px-2 py-3 text-xs">{t.board.empty}</p>
                ) : (
                  items.map((project) => (
                    <ProjectCard key={project.id} project={project} trucks={trucks.get(project.id) ?? 0} />
                  ))
                )}
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ProjectCard({ project, trucks }: { project: Project; trucks: number }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const move = useMutation({
    mutationFn: (status: NonNullable<ProjectUpdate["status"]>) => updateProject(project.id, { status }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["projects"] }),
  });
  const moves = MOVES[project.status] ?? [];
  const label = { active: t.board.confirm, rejected: t.board.reject, closed: t.board.close };

  return (
    <article className="bg-card ring-foreground/5 grid gap-1.5 rounded-xl p-3 ring-1">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-muted-foreground font-mono text-xs">
            {project.number}
            {project.field_sources.number === "owner" ? (
              <span className="sr-only"> · {t.board.ownerEdited}</span>
            ) : null}
          </p>
          <h3 className="mt-0.5 text-sm font-medium break-words">{project.name}</h3>
        </div>
        {moves.length > 0 ? (
          <DropdownMenu modal={false}>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-xs" aria-label={t.board.actions} disabled={move.isPending}>
                <HugeiconsIcon icon={MoreHorizontalIcon} strokeWidth={1.75} />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {moves.map((status) => (
                <DropdownMenuItem key={status} onSelect={() => move.mutate(status)}>
                  {label[status]}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
      </div>
      <p className={cn("text-muted-foreground flex items-center gap-1.5 text-xs", trucks === 0 && "opacity-70")}>
        <HugeiconsIcon icon={TruckIcon} strokeWidth={1.75} className="size-3.5" />
        {t.board.trucks(trucks)}
      </p>
      {project.aliases.length > 0 ? (
        <p className="text-muted-foreground font-mono text-xs">{t.board.aliases(project.aliases.join(", "))}</p>
      ) : null}
      {move.isError ? (
        <p className="text-danger-foreground text-xs" role="alert">
          {t.errors.generic}
        </p>
      ) : null}
    </article>
  );
}
