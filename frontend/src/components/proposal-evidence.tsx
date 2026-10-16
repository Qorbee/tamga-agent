"use client";

import { useQuery } from "@tanstack/react-query";

import { listAllPlacements, type Placement, type Project } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

type Evidence = NonNullable<Placement["evidence"]>;

const DOT: Record<Evidence["band"], string> = {
  strong: "bg-success",
  partial: "bg-warning",
  thin: "bg-danger",
};

/**
 * What backs a project Tamga proposes, weighed by code (docs/architecture/09): a band, what the
 * conversation does not tell of the job, and how often the owner kept proposals weighed the same.
 * Shown while the project waits for its answer; nothing when no proposal of it carries evidence.
 * It reads every conversation of the project, as a card may list only those that still wait.
 */
export function ProposalEvidence({ project, className }: { project: Project; className?: string }) {
  const { t } = useI18n();
  const words = t.inbox.evidence;
  const placements = useQuery({
    queryKey: ["placements", { projects: [project.id] }],
    queryFn: () => listAllPlacements({ project_id: [project.id] }),
    enabled: project.status === "proposed",
  });
  const evidence = (placements.data ?? []).find(
    (placement) => placement.outcome === "proposed" && placement.project_id === project.id,
  )?.evidence;
  if (project.status !== "proposed" || !evidence) return null;
  return (
    <div className={cn("text-muted-foreground grid gap-0.5 text-sm", className)}>
      <p className="text-foreground flex items-center gap-2">
        <span className={cn("size-2 shrink-0 rounded-full", DOT[evidence.band])} aria-hidden />
        {words.band[evidence.band]}
      </p>
      {evidence.missing.length > 0 ? (
        <p>{words.missing(evidence.missing.map((gap) => words.gaps[gap]).join(" · "))}</p>
      ) : null}
      <p className="text-xs">
        {evidence.measured ? words.measured(evidence.measured.kept, evidence.measured.answered) : words.unmeasured}
      </p>
    </div>
  );
}
