"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { Field } from "@/components/field";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ApiError, getMe, updateProject, type Project, type ProjectUpdate } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";

/** Whether the signed-in user may change a project: the owner and ops; a viewer only reads. */
export function useCanEditProjects(): boolean {
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  return me.data?.role === "owner" || me.data?.role === "ops";
}

type Shown = { name: string; number: string; starts_on: string; ends_on: string };

const shownOf = (project: Project): Shown => ({
  name: project.name,
  number: project.number,
  starts_on: project.starts_on ?? "",
  ends_on: project.ends_on ?? "",
});

/**
 * The owner's own name, number and days for a project. What the owner sets stays: later mail
 * proposes a change instead of making it. The number it had stays its alias, so mail that quotes
 * the old one still finds it.
 */
export function EditProjectDialog({
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
        {/* Mounted when it opens, so the form starts from the project as it is then. */}
        {open ? <EditForm project={project} onDone={() => onOpenChange(false)} /> : null}
      </DialogContent>
    </Dialog>
  );
}

function EditForm({ project, onDone }: { project: Project; onDone: () => void }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  // What the form showed when it opened: a field is sent only if the user changed it from that,
  // so a project refetched meanwhile (changed in another session) is not written back over.
  const [shown] = useState(() => shownOf(project));
  const [form, setForm] = useState(shown);
  const save = useMutation({
    mutationFn: (body: ProjectUpdate) => updateProject(project.id, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      void queryClient.invalidateQueries({ queryKey: ["project", project.id] });
      void queryClient.invalidateQueries({ queryKey: ["field-proposals"] });
      onDone();
    },
  });
  const error = save.error instanceof ApiError ? save.error : null;
  const fieldError = (field: string) => (error?.field === field ? error.message : undefined);
  const set = (field: keyof Shown) => (event: { target: { value: string } }) =>
    setForm((current) => ({ ...current, [field]: event.target.value }));

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const body: ProjectUpdate = {};
    if (form.name.trim() !== shown.name) body.name = form.name.trim();
    if (form.number.trim() !== shown.number) body.number = form.number.trim();
    // An emptied day is cleared: sent as null.
    if (form.starts_on !== shown.starts_on) body.starts_on = form.starts_on || null;
    if (form.ends_on !== shown.ends_on) body.ends_on = form.ends_on || null;
    if (Object.keys(body).length === 0) onDone();
    else save.mutate(body);
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>{t.project.editTitle}</DialogTitle>
      </DialogHeader>
      <form onSubmit={onSubmit} className="grid gap-4">
        <Field label={t.board.name} htmlFor="edit-project-name" error={fieldError("name")}>
          <Input id="edit-project-name" required maxLength={300} value={form.name} onChange={set("name")} />
        </Field>
        <Field label={t.board.number} htmlFor="edit-project-number" hint={t.project.numberKept} error={fieldError("number")}>
          <Input
            id="edit-project-number"
            required
            maxLength={60}
            className="font-mono"
            value={form.number}
            aria-invalid={error?.field === "number" || undefined}
            onChange={set("number")}
          />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t.project.startsOn} htmlFor="edit-project-starts">
            <Input id="edit-project-starts" type="date" value={form.starts_on} onChange={set("starts_on")} />
          </Field>
          <Field label={t.project.endsOn} htmlFor="edit-project-ends" error={fieldError("ends_on")}>
            <Input
              id="edit-project-ends"
              type="date"
              min={form.starts_on || undefined}
              value={form.ends_on}
              aria-invalid={error?.field === "ends_on" || undefined}
              onChange={set("ends_on")}
            />
          </Field>
        </div>
        {save.isError && !["name", "number", "ends_on"].includes(error?.field ?? "") ? (
          <p className="text-danger-foreground text-sm" role="alert">
            {t.errors.generic}
          </p>
        ) : null}
        <DialogFooter>
          <Button type="submit" disabled={save.isPending}>
            {t.project.save}
          </Button>
        </DialogFooter>
      </form>
    </>
  );
}
