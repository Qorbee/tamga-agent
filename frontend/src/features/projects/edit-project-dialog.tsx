"use client";

import { PencilEdit02Icon } from "@hugeicons/core-free-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { Field } from "@/components/field";
import { IconButton } from "@/components/icon-button";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ApiError, getMe, updateProject, type Project, type ProjectUpdate } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";

/**
 * The owner's own name and number for a project. The number it had stays its alias, so mail that
 * quotes the old one still finds it. Shown only to those who may change a project.
 */
export function EditProjectDialog({ project }: { project: Project }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  const [open, setOpen] = useState(false);
  // What the form showed when it opened: a field is sent only if the user changed it from that,
  // so a project refetched meanwhile (changed in another session) is not written back over.
  const [shown, setShown] = useState({ name: project.name, number: project.number });
  const [name, setName] = useState(project.name);
  const [number, setNumber] = useState(project.number);
  const save = useMutation({
    mutationFn: (body: ProjectUpdate) => updateProject(project.id, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      void queryClient.invalidateQueries({ queryKey: ["project", project.id] });
      setOpen(false);
    },
  });
  const error = save.error instanceof ApiError ? save.error : null;

  if (me.data?.role !== "owner" && me.data?.role !== "ops") return null;

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    // Only what changed is sent: a field left as it was keeps who set it.
    const body: ProjectUpdate = {};
    if (name.trim() !== shown.name) body.name = name.trim();
    if (number.trim() !== shown.number) body.number = number.trim();
    if (Object.keys(body).length === 0) setOpen(false);
    else save.mutate(body);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        save.reset();
        if (next) {
          setShown({ name: project.name, number: project.number });
          setName(project.name);
          setNumber(project.number);
        }
      }}
    >
      <DialogTrigger asChild>
        <IconButton icon={PencilEdit02Icon} label={t.project.edit} className="text-muted-foreground" />
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t.project.edit}</DialogTitle>
        </DialogHeader>
        <form onSubmit={onSubmit} className="grid gap-4">
          <Field label={t.board.name} htmlFor="edit-project-name">
            <Input
              id="edit-project-name"
              required
              maxLength={300}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </Field>
          <Field
            label={t.board.number}
            htmlFor="edit-project-number"
            hint={t.project.numberKept}
            error={error?.field === "number" ? error.message : undefined}
          >
            <Input
              id="edit-project-number"
              required
              maxLength={60}
              className="font-mono"
              value={number}
              aria-invalid={error?.field === "number" || undefined}
              onChange={(event) => setNumber(event.target.value)}
            />
          </Field>
          {save.isError && error?.field !== "number" ? (
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
      </DialogContent>
    </Dialog>
  );
}
