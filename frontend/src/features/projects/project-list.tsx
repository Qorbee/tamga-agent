"use client";

import { Link01Icon, Search01Icon, TruckIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useDeferredValue, useState } from "react";

import { CanvasLink } from "@/components/canvas-link";
import { Count } from "@/components/placement-rows";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { T } from "@/components/translated";
import { listProjects, listShipments, type Project, type ProjectStatus } from "@/lib/api";
import { formatListDate } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";

import { NewProjectDialog } from "./new-project-dialog";
import { ProjectActions, STATUS_ICONS } from "./project-actions";

/** What needs the owner first. Rejected proposals are not shown: the list is the work in hand. */
const STATUSES = ["proposed", "active", "draft", "closed"] as const satisfies readonly ProjectStatus[];
type Shown = (typeof STATUSES)[number];

/**
 * The projects, one status at a time: a full-width row each, so a long name reads in one piece on
 * a phone too. A row opens the project in the canvas.
 */
export function ProjectList() {
  const { t } = useI18n();
  const [search, setSearch] = useState("");
  const q = useDeferredValue(search.trim());
  const [chosen, setChosen] = useState<Shown | null>(null);
  const projects = useQuery({
    queryKey: ["projects", { q }],
    queryFn: () => listProjects({ status: [...STATUSES], q }),
    placeholderData: keepPreviousData,
  });
  const all = projects.data?.items ?? [];
  const count = (status: Shown) => all.filter((project) => project.status === status).length;
  // Until the owner picks one: the proposals when any wait, else the work that runs.
  const status = chosen ?? (count("proposed") > 0 ? "proposed" : "active");
  const items = all.filter((project) => project.status === status);
  const ids = items.map((project) => project.id);
  const shipments = useQuery({
    queryKey: ["shipments", ids],
    queryFn: () => listShipments(ids),
    enabled: ids.length > 0,
  });
  const trucks = new Map<string, number>();
  for (const shipment of shipments.data ?? [])
    trucks.set(shipment.project_id, (trucks.get(shipment.project_id) ?? 0) + 1);

  return (
    <div className="grid gap-4">
      <div className="flex items-center gap-2">
        <div className="relative min-w-0 flex-1">
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
        <NewProjectDialog />
      </div>

      <ToggleGroup
        type="single"
        value={status}
        aria-label={t.board.status}
        className="w-full"
        spacing={1}
        onValueChange={(value) => {
          if (value) setChosen(value as Shown);
        }}
      >
        {STATUSES.map((value) => (
          <ToggleGroupItem
            key={value}
            value={value}
            aria-label={t.board.columns[value]}
            title={t.board.columns[value]}
            className="group/status text-muted-foreground data-[state=on]:bg-secondary data-[state=on]:text-foreground min-w-0 gap-1.5 px-2 max-sm:data-[state=on]:flex-1 sm:flex-1"
          >
            <HugeiconsIcon icon={STATUS_ICONS[value]} strokeWidth={1.75} className="shrink-0" />
            {/* On a phone only the chosen status is spelled out; the others are an icon and a count. */}
            <span className="truncate max-sm:group-data-[state=off]/status:sr-only">{t.board.columns[value]}</span>
            <span className="tabular-nums">{projects.data ? count(value) : ""}</span>
          </ToggleGroupItem>
        ))}
      </ToggleGroup>

      {projects.isError ? (
        <p className="text-danger-foreground text-sm" role="alert">
          {t.errors.load}
        </p>
      ) : projects.isPending ? (
        <Skeleton className="h-40 rounded-2xl" />
      ) : items.length === 0 ? (
        <p className="text-muted-foreground py-10 text-center text-sm">{t.board.empty}</p>
      ) : (
        <ul className="divide-border border-border bg-card divide-y overflow-hidden rounded-2xl border">
          {items.map((project) => (
            <ProjectRow key={project.id} project={project} trucks={trucks.get(project.id) ?? 0} />
          ))}
        </ul>
      )}
    </div>
  );
}

function ProjectRow({ project, trucks }: { project: Project; trucks: number }) {
  const { t, locale } = useI18n();
  // Whatever is tied to it or suggested for it: mails, and files or invoices tied by hand.
  const items = project.confirmed_links + project.suggested_links;

  return (
    <li className="flex items-center gap-1 py-1 ps-2 pe-3">
      <CanvasLink
        view={{ kind: "project", id: project.id }}
        className="hover:bg-accent/40 aria-[current=true]:bg-secondary min-w-0 flex-1 rounded-xl px-2 py-2.5 transition-colors"
      >
        <span className="text-muted-foreground flex items-baseline gap-2 font-mono text-xs">
          {project.number}
          {project.aliases.length > 0 ? <span>{t.board.aliases(project.aliases.join(", "))}</span> : null}
          {project.field_sources.number === "owner" ? <span className="sr-only">{t.board.ownerEdited}</span> : null}
        </span>
        <span className="block text-base font-medium text-pretty break-words">
          <T>{project.name}</T>
        </span>
        <span className="text-muted-foreground mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs">
          {items > 0 ? <Count icon={Link01Icon} count={items} label={t.board.items(items)} /> : null}
          {trucks > 0 ? <Count icon={TruckIcon} count={trucks} label={t.board.trucks(trucks)} /> : null}
          {project.suggested_links > 0 ? (
            <span className="text-warning-foreground inline-flex items-center gap-1.5">
              <span className="bg-warning size-1.5 rounded-full" aria-hidden />
              {t.board.waiting}
            </span>
          ) : null}
          <time dateTime={project.updated_at} className="tabular-nums">
            {formatListDate(new Date(project.updated_at), locale)}
          </time>
        </span>
      </CanvasLink>
      <ProjectActions project={project} />
    </li>
  );
}
