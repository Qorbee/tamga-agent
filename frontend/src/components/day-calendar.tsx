"use client";

import { ArrowLeft01Icon, ArrowRight01Icon } from "@hugeicons/core-free-icons";
import { useState } from "react";

import { IconButton } from "@/components/icon-button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { formatDayLong, formatDays, formatMonth, todayIso, weekdayNames } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

export type DaySpan = { start: string; end: string };

const pad = (value: number) => String(value).padStart(2, "0");
const iso = (year: number, month: number, day: number) => `${year}-${pad(month + 1)}-${pad(day)}`;

/** The month a calendar opens on: its chosen day's, else today's. */
function monthOf(day: string): { year: number; month: number } {
  const [year = 1970, month = 1] = (day || todayIso()).split("-").map(Number);
  return { year, month: month - 1 };
}

/**
 * A month of days to tap, as a phone's calendar is: Monday first, today ringed, the chosen day
 * filled. With `span`, a first tap starts it and a second ends it (one before the start starts it
 * again), and the days between are shaded. Days are the API's `2026-10-16`, never shifted by a time
 * zone. Its cells are a finger wide on a phone and a pointer's on a desktop.
 */
export function DayCalendar(
  props:
    | { span?: false; value: string; onChange: (day: string) => void; min?: string }
    | { span: true; value: DaySpan; onChange: (span: DaySpan) => void },
) {
  const { t, locale } = useI18n();
  const first = props.span ? props.value.start || props.value.end : props.value;
  const [shown, setShown] = useState(() => monthOf(first));
  const today = todayIso();
  const lead = (new Date(shown.year, shown.month, 1).getDay() + 6) % 7;
  const days = new Date(shown.year, shown.month + 1, 0).getDate();
  const cells = [...Array<null>(lead).fill(null), ...Array.from({ length: days }, (_, at) => at + 1)];
  const step = (by: number) =>
    setShown(({ year, month }) => {
      const next = new Date(year, month + by, 1);
      return { year: next.getFullYear(), month: next.getMonth() };
    });

  const pick = (day: string) => {
    if (!props.span) return props.onChange(day);
    const { start, end } = props.value;
    // A span with both ends, or none, starts again; one with a start takes its end, unless the
    // day is before it.
    if (!start || end || day < start) return props.onChange({ start: day, end: "" });
    props.onChange({ start, end: day });
  };

  return (
    <div className="grid w-full max-w-80 gap-2 select-none">
      <div className="flex items-center justify-between">
        <IconButton icon={ArrowLeft01Icon} label={t.calendar.previous} onClick={() => step(-1)} />
        <span className="text-sm font-medium capitalize" aria-live="polite">
          {formatMonth(shown.year, shown.month, locale)}
        </span>
        <IconButton icon={ArrowRight01Icon} label={t.calendar.next} onClick={() => step(1)} />
      </div>
      <div className="grid grid-cols-7 text-center" role="group" aria-label={formatMonth(shown.year, shown.month, locale)}>
        {weekdayNames(locale).map((name) => (
          <span key={name} className="text-muted-foreground pb-1 text-xs" aria-hidden>
            {name}
          </span>
        ))}
        {cells.map((date, at) => {
          if (date === null) return <span key={`lead-${at}`} />;
          const day = iso(shown.year, shown.month, date);
          const value = props.value;
          const start = props.span ? (value as DaySpan).start : (value as string);
          const end = props.span ? (value as DaySpan).end : "";
          const ends = day === start || day === end;
          const between = Boolean(start && end && day > start && day < end);
          const blocked = !props.span && props.min ? day < props.min : false;
          return (
            <span
              key={day}
              className={cn(
                "flex justify-center py-0.5",
                between && "bg-accent",
                props.span && start && end && day === start && start !== end && "bg-accent rounded-s-full",
                props.span && start && end && day === end && start !== end && "bg-accent rounded-e-full",
              )}
            >
              <button
                type="button"
                disabled={blocked}
                aria-pressed={ends}
                aria-label={formatDayLong(day, locale)}
                onClick={() => pick(day)}
                className={cn(
                  "hover:bg-accent flex size-10 items-center justify-center rounded-full text-sm tabular-nums outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:opacity-40 md:size-9",
                  day === today && !ends && "ring-border ring-1",
                  ends && "bg-foreground text-background hover:bg-foreground font-medium",
                )}
              >
                {date}
              </button>
            </span>
          );
        })}
      </div>
    </div>
  );
}

/** A day to choose, shown as it reads and set in a calendar a tap away. */
export function DayInput({
  value,
  onChange,
  label,
  className,
}: {
  value: string;
  onChange: (day: string) => void;
  label: string;
  className?: string;
}) {
  const { locale } = useI18n();
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={label}
          className={cn(
            "border-input bg-background hover:bg-accent/40 h-9 rounded-xl border px-3 text-start text-sm tabular-nums outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
            !value && "text-muted-foreground",
            className,
          )}
        >
          {value ? formatDays(value, null, locale) : label}
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-auto">
        <DayCalendar
          value={value}
          onChange={(day) => {
            onChange(day);
            setOpen(false);
          }}
        />
      </PopoverContent>
    </Popover>
  );
}
