"use client";

import { ArrowDown01Icon, ArrowLeft01Icon, ArrowRight01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useState } from "react";

import { IconButton } from "@/components/icon-button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { formatDayLong, formatDays, formatMonth, monthNames, todayIso, weekdayNames } from "@/lib/format";
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
 * zone. Its cells are a finger wide on a phone and a pointer's on a desktop. The month's name
 * opens the year's months, and the year a dozen years, so a far day is two taps away.
 */
export function DayCalendar(
  props:
    | { span?: false; value: string; onChange: (day: string) => void; min?: string }
    | { span: true; value: DaySpan; onChange: (span: DaySpan) => void },
) {
  const { t, locale } = useI18n();
  const first = props.span ? props.value.start || props.value.end : props.value;
  const [shown, setShown] = useState(() => monthOf(first));
  // What the calendar shows: a month's days, the year's months, or a dozen years to jump to.
  const [level, setLevel] = useState<"days" | "months" | "years">("days");
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

  const thisYear = Number(today.slice(0, 4));
  const thisMonth = Number(today.slice(5, 7)) - 1;
  // The dozen years shown together, the shown one among them.
  const decade = shown.year - (((shown.year % 12) + 12) % 12);
  const head =
    level === "days"
      ? { label: formatMonth(shown.year, shown.month, locale), up: t.calendar.pickMonth, back: t.calendar.previous, forward: t.calendar.next, by: 1 }
      : level === "months"
        ? { label: String(shown.year), up: t.calendar.pickYear, back: t.calendar.previousYear, forward: t.calendar.nextYear, by: 12 }
        : { label: `${decade} – ${decade + 11}`, up: "", back: t.calendar.previousYears, forward: t.calendar.nextYears, by: 144 };
  const choice =
    "hover:bg-accent flex h-11 items-center justify-center rounded-xl text-sm tabular-nums outline-none focus-visible:ring-2 focus-visible:ring-ring/50";
  const chosen = "bg-foreground text-background hover:bg-foreground font-medium";

  return (
    <div className="grid w-full max-w-80 gap-2 select-none">
      <div className="flex items-center justify-between gap-1">
        {/* Buttons, never the form's submit: a calendar sits in the form that saves the day. */}
        <IconButton type="button" icon={ArrowLeft01Icon} label={head.back} onClick={() => step(-head.by)} />
        {level === "years" ? (
          <span className="text-sm font-medium tabular-nums" aria-live="polite">
            {head.label}
          </span>
        ) : (
          <button
            type="button"
            aria-label={head.up}
            aria-live="polite"
            onClick={() => setLevel(level === "days" ? "months" : "years")}
            className="hover:bg-accent focus-visible:ring-ring/50 inline-flex h-8 items-center gap-1 rounded-lg px-2 text-sm font-medium capitalize tabular-nums outline-none focus-visible:ring-2"
          >
            {head.label}
            <HugeiconsIcon icon={ArrowDown01Icon} strokeWidth={1.75} className="text-muted-foreground size-3.5" aria-hidden />
          </button>
        )}
        <IconButton type="button" icon={ArrowRight01Icon} label={head.forward} onClick={() => step(head.by)} />
      </div>
      {level === "months" ? (
        <div className="grid grid-cols-3 gap-1" role="group" aria-label={String(shown.year)}>
          {monthNames(locale).map((name, month) => (
            <button
              key={name}
              type="button"
              aria-pressed={month === shown.month}
              onClick={() => {
                setShown({ year: shown.year, month });
                setLevel("days");
              }}
              className={cn(
                choice,
                "capitalize",
                shown.year === thisYear && month === thisMonth && "ring-border ring-1",
                month === shown.month && chosen,
              )}
            >
              {name}
            </button>
          ))}
        </div>
      ) : null}
      {level === "years" ? (
        <div className="grid grid-cols-3 gap-1" role="group" aria-label={head.label}>
          {Array.from({ length: 12 }, (_, at) => decade + at).map((year) => (
            <button
              key={year}
              type="button"
              aria-pressed={year === shown.year}
              onClick={() => {
                setShown({ year, month: shown.month });
                setLevel("months");
              }}
              className={cn(choice, year === thisYear && "ring-border ring-1", year === shown.year && chosen)}
            >
              {year}
            </button>
          ))}
        </div>
      ) : null}
      <div
        className={cn("grid grid-cols-7 text-center", level !== "days" && "hidden")}
        role="group"
        aria-label={formatMonth(shown.year, shown.month, locale)}
      >
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
      {level === "days" && (shown.year !== thisYear || shown.month !== thisMonth) ? (
        <button
          type="button"
          onClick={() => setShown({ year: thisYear, month: thisMonth })}
          className="text-muted-foreground hover:text-foreground justify-self-center rounded-lg px-2 py-1 text-xs"
        >
          {t.calendar.today}
        </button>
      ) : null}
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
