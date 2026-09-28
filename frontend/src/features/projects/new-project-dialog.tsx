"use client";

import { Add01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { Field } from "@/components/field";
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
import { ApiError, createProject } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";

/** A draft project, numbered in the company's format unless the owner types a number. */
export function NewProjectDialog() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [number, setNumber] = useState("");
  const create = useMutation({
    mutationFn: createProject,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      setOpen(false);
      setName("");
      setNumber("");
    },
  });
  const error = create.error instanceof ApiError ? create.error : null;

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    create.mutate({ name, number: number.trim() || null });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) create.reset();
      }}
    >
      <DialogTrigger asChild>
        <Button size="sm">
          <HugeiconsIcon icon={Add01Icon} strokeWidth={1.75} data-icon="inline-start" />
          {t.board.newProject}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t.board.newProject}</DialogTitle>
        </DialogHeader>
        <form onSubmit={onSubmit} className="grid gap-4">
          <Field label={t.board.name} htmlFor="project-name">
            <Input
              id="project-name"
              required
              maxLength={300}
              placeholder={t.board.namePlaceholder}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </Field>
          <Field
            label={t.board.number}
            htmlFor="project-number"
            hint={t.board.numberHint}
            error={error?.field === "number" ? error.message : undefined}
          >
            <Input
              id="project-number"
              maxLength={60}
              className="font-mono"
              value={number}
              aria-invalid={error?.field === "number" || undefined}
              onChange={(event) => setNumber(event.target.value)}
            />
          </Field>
          {create.isError && error?.field !== "number" ? (
            <p className="text-danger-foreground text-sm" role="alert">
              {t.errors.generic}
            </p>
          ) : null}
          <DialogFooter>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? t.board.creating : t.board.create}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
