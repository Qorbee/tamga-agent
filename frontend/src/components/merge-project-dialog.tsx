"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { useCanvasView, useShowInCanvas } from "@/components/canvas-link";
import { ProjectPicker } from "@/components/project-picker";
import { T } from "@/components/translated";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ApiError, mergeProject, type Project } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";

/**
 * The owner saying this project and another are one job. The other stays and takes everything
 * this one holds; this one's number keeps finding it. A merge is not undone, so the project
 * picked is shown once more before anything happens.
 */
export function MergeProjectDialog({
  project,
  open,
  onOpenChange,
}: {
  project: Project;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        {/* Mounted when it opens, so it starts from the search each time. */}
        {open ? <Merge project={project} onDone={() => onOpenChange(false)} /> : null}
      </DialogContent>
    </Dialog>
  );
}

// What a merge changes: the lists both projects were in, everything read by project, and a
// chat's card that showed the project that went, which the server points at the one that stays.
const CHANGED = ["projects", "project", "placements", "project-links", "field-proposals", "chat-messages"];

/** A project's number with hyphens that do not break: it reads as one word at a line's end. */
const unbroken = (number: string) => number.replaceAll("-", "\u2011");

function Merge({ project, onDone }: { project: Project; onDone: () => void }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const shown = useCanvasView();
  const show = useShowInCanvas();
  const [into, setInto] = useState<Project | null>(null);
  const merge = useMutation({
    mutationFn: (target: Project) => mergeProject(project.id, { into: target.id }),
    onSuccess: (kept) => {
      // The panel of a project that is gone shows the one it went into; until the address has
      // changed, what it holds is that project, and it is not asked for again.
      queryClient.setQueryData(["project", project.id], kept);
      if (shown?.kind === "project" && shown.id === project.id) show({ kind: "project", id: kept.id });
      void queryClient.invalidateQueries({
        predicate: ({ queryKey }) =>
          CHANGED.includes(String(queryKey[0])) && !(queryKey[0] === "project" && queryKey[1] === project.id),
      });
      onDone();
    },
  });
  // The one refusal with a way out: the project has trucks, and the merge goes the other way.
  const refused = merge.error instanceof ApiError && merge.error.status === 409;

  return (
    <>
      <DialogHeader>
        <DialogTitle>{t.project.mergeTitle}</DialogTitle>
        <DialogDescription>
          <span className="font-mono">{project.number}</span> <T>{project.name}</T>
        </DialogDescription>
      </DialogHeader>
      {into === null ? (
        <ProjectPicker exclude={[project.id]} onPick={setInto} />
      ) : (
        <div className="grid gap-4">
          <div className="border-border grid gap-0.5 rounded-xl border px-3 py-2.5">
            <span className="text-muted-foreground font-mono text-xs">{into.number}</span>
            <span className="text-sm text-pretty [overflow-wrap:anywhere]">
              <T>{into.name}</T>
            </span>
          </div>
          <p className="text-muted-foreground text-sm text-pretty">{t.project.mergeWarning(unbroken(project.number), unbroken(into.number))}</p>
          {merge.isError ? (
            <p className="text-danger-foreground text-sm" role="alert">
              {refused ? t.project.mergeHasTrucks : t.errors.generic}
            </p>
          ) : null}
          <DialogFooter>
            <Button
              variant="ghost"
              disabled={merge.isPending}
              onClick={() => {
                merge.reset();
                setInto(null);
              }}
            >
              {t.project.back}
            </Button>
            <Button disabled={merge.isPending} onClick={() => merge.mutate(into)}>
              {t.project.mergeConfirm}
            </Button>
          </DialogFooter>
        </div>
      )}
    </>
  );
}
