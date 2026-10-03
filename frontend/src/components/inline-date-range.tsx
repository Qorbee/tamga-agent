"use client";

import { useId, useState, type FormEvent } from "react";

import { Field } from "@/components/field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { formatDays } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

export type DayRange = { start: string | null; end: string | null };

/**
 * A span of days edited where it is shown: a tap opens the phone's own date pickers for the first
 * and the last day. Only a day that changed from what it was as the picker opened is sent, so a
 * day another session saved meanwhile is not written over, and an emptied one is cleared. Without
 * `onSave` it is only shown.
 */
export function InlineDateRange({
  value,
  labels,
  onSave,
  pending = false,
  error,
  empty,
  className,
}: {
  value: DayRange;
  labels: { start: string; end: string; edit: string };
  onSave?: (changed: Partial<DayRange>) => void;
  pending?: boolean;
  error?: string;
  /** Shown while neither day is known. */
  empty: string;
  className?: string;
}) {
  const { t, locale } = useI18n();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState({ start: value.start ?? "", end: value.end ?? "" });
  const [opened, setOpened] = useState(draft);
  const id = useId();
  const shown = formatDays(value.start, value.end, locale);
  const text = <span className={cn("tabular-nums", !shown && "text-muted-foreground")}>{shown || empty}</span>;

  if (!onSave) return <span className={className}>{text}</span>;

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const changed: Partial<DayRange> = {};
    if (draft.start !== opened.start) changed.start = draft.start || null;
    if (draft.end !== opened.end) changed.end = draft.end || null;
    setOpen(false);
    if (Object.keys(changed).length > 0) onSave(changed);
  };

  return (
    <span className="grid gap-1">
      <Popover
        open={open}
        onOpenChange={(next) => {
          if (next) {
            const days = { start: value.start ?? "", end: value.end ?? "" };
            setDraft(days);
            setOpened(days);
          }
          setOpen(next);
        }}
      >
        <PopoverTrigger asChild>
          <button
            type="button"
            aria-label={labels.edit}
            disabled={pending}
            className={cn(
              "hover:bg-accent/60 focus-visible:ring-ring/50 -mx-1.5 rounded-lg px-1.5 text-start outline-none focus-visible:ring-2 disabled:opacity-60",
              className,
            )}
          >
            {text}
          </button>
        </PopoverTrigger>
        <PopoverContent>
          <form onSubmit={onSubmit} className="grid gap-3">
            <Field label={labels.start} htmlFor={`${id}-start`}>
              <Input
                id={`${id}-start`}
                type="date"
                value={draft.start}
                onChange={(event) => setDraft((current) => ({ ...current, start: event.target.value }))}
              />
            </Field>
            <Field label={labels.end} htmlFor={`${id}-end`}>
              <Input
                id={`${id}-end`}
                type="date"
                min={draft.start || undefined}
                value={draft.end}
                onChange={(event) => setDraft((current) => ({ ...current, end: event.target.value }))}
              />
            </Field>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" size="sm" onClick={() => setDraft({ start: "", end: "" })}>
                {t.project.clear}
              </Button>
              <Button type="submit" size="sm">
                {t.project.save}
              </Button>
            </div>
          </form>
        </PopoverContent>
      </Popover>
      {error ? (
        <span className="text-danger-foreground text-sm" role="alert">
          {error}
        </span>
      ) : null}
    </span>
  );
}
