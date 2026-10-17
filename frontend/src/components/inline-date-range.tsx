"use client";

import { useState, type FormEvent } from "react";

import { DayCalendar } from "@/components/day-calendar";
import { PopSheet } from "@/components/pop-sheet";
import { Button } from "@/components/ui/button";
import { formatDays } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

export type DayRange = { start: string | null; end: string | null };

/**
 * A span of days edited where it is shown: a tap opens a calendar, a tap on the first day and one
 * on the last. Only a day that changed from what it was as the picker opened is sent, so a
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
      <PopSheet
        open={open}
        onOpenChange={(next) => {
          if (next) {
            const days = { start: value.start ?? "", end: value.end ?? "" };
            setDraft(days);
            setOpened(days);
          }
          setOpen(next);
        }}
        title={labels.edit}
        className="w-auto"
        trigger={
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
        }
      >
          <form onSubmit={onSubmit} className="grid gap-3">
            <DayCalendar span value={draft} onChange={setDraft} />
            <p className="text-muted-foreground grid grid-cols-2 gap-2 text-xs">
              <span>
                {labels.start}
                <span className="text-foreground block text-sm tabular-nums">{formatDays(draft.start || null, null, locale) || "—"}</span>
              </span>
              <span>
                {labels.end}
                <span className="text-foreground block text-sm tabular-nums">{formatDays(draft.end || null, null, locale) || "—"}</span>
              </span>
            </p>
            {/* A thumb's width on a phone, where the sheet runs edge to edge. */}
            <div className="flex justify-end gap-2 max-md:grid max-md:grid-cols-2">
              <Button type="button" variant="ghost" size="sm" className="max-md:h-11" onClick={() => setDraft({ start: "", end: "" })}>
                {t.project.clear}
              </Button>
              <Button type="submit" size="sm" className="max-md:h-11">
                {t.project.save}
              </Button>
            </div>
          </form>
      </PopSheet>
      {error ? (
        <span className="text-danger-foreground text-sm" role="alert">
          {error}
        </span>
      ) : null}
    </span>
  );
}
