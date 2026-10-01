"use client";

import { Search01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useQuery } from "@tanstack/react-query";
import { useDeferredValue, useState } from "react";

import { T } from "@/components/translated";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { listProjects, type Project, type ProjectStatus } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";

// A rejected project is no place to put anything.
const OFFERED: ProjectStatus[] = ["proposed", "draft", "active", "closed"];
const SHOWN = 20;

/**
 * Finds one of the company's projects by its number or a word of its name: the most recently
 * changed first, `exclude` left out. Tapping one picks it.
 */
export function ProjectPicker({
  exclude,
  disabled = false,
  onPick,
}: {
  exclude: readonly string[];
  disabled?: boolean;
  onPick: (project: Project) => void;
}) {
  const { t } = useI18n();
  const [search, setSearch] = useState("");
  const q = useDeferredValue(search.trim());
  const found = useQuery({
    queryKey: ["projects", { pick: q }],
    queryFn: () => listProjects({ status: OFFERED, q: q || undefined, limit: SHOWN + exclude.length }),
  });
  const projects = (found.data?.items ?? []).filter((project) => !exclude.includes(project.id)).slice(0, SHOWN);

  return (
    <div className="grid gap-3">
      <div className="relative">
        <HugeiconsIcon
          icon={Search01Icon}
          strokeWidth={1.75}
          className="text-muted-foreground pointer-events-none absolute start-3 top-1/2 size-4 -translate-y-1/2"
        />
        <Input
          type="search"
          autoFocus
          aria-label={t.board.search}
          placeholder={t.board.search}
          className="ps-9"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>
      {found.isError ? (
        <p className="text-danger-foreground text-sm" role="alert">
          {t.errors.load}
        </p>
      ) : found.isPending ? (
        <Skeleton className="h-24 rounded-xl" />
      ) : projects.length === 0 ? (
        <p className="text-muted-foreground py-4 text-center text-sm">{t.board.empty}</p>
      ) : (
        <ul className="divide-border border-border max-h-[50dvh] divide-y overflow-y-auto rounded-xl border">
          {projects.map((project) => (
            <li key={project.id}>
              <button
                type="button"
                disabled={disabled}
                className="hover:bg-accent/40 focus-visible:bg-accent/40 grid w-full gap-0.5 px-3 py-2.5 text-start outline-none disabled:opacity-60"
                onClick={() => onPick(project)}
              >
                <span className="text-muted-foreground font-mono text-xs">{project.number}</span>
                <span className="text-sm text-pretty [overflow-wrap:anywhere]">
                  <T>{project.name}</T>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
