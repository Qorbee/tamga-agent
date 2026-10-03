"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import { cn } from "@/lib/utils";

/**
 * A value edited where it is shown: a click turns it into a field, Enter or a click elsewhere
 * saves, Escape puts it back. Nothing is sent when the text did not change from what it was as
 * editing began, so a value another session saved meanwhile is not written over; an empty one is
 * refused rather than saved. Without `onSave` it is only shown.
 */
export function InlineText({
  value,
  label,
  onSave,
  pending = false,
  error,
  maxLength,
  className,
  children,
}: {
  value: string;
  /** What the field is, for a screen reader and the button that opens it. */
  label: string;
  onSave?: (value: string) => void;
  pending?: boolean;
  error?: string;
  maxLength?: number;
  className?: string;
  /** How the value is shown when not being edited; the value itself when left out. */
  children?: React.ReactNode;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [start, setStart] = useState(value);
  const field = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) field.current?.select();
  }, [editing]);

  if (!onSave)
    return <span className={cn("[overflow-wrap:anywhere]", className)}>{children ?? value}</span>;

  const save = () => {
    setEditing(false);
    const text = draft.trim();
    if (text && text !== start) onSave(text);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      save();
    } else if (event.key === "Escape") {
      event.preventDefault();
      setEditing(false);
    }
  };

  return (
    <span className="grid gap-1">
      {editing ? (
        <input
          ref={field}
          aria-label={label}
          value={draft}
          maxLength={maxLength}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={save}
          onKeyDown={onKeyDown}
          className={cn(
            "bg-background focus-visible:ring-ring/50 -mx-1.5 w-[calc(100%+0.75rem)] min-w-0 rounded-lg px-1.5 outline-none focus-visible:ring-2",
            className,
          )}
        />
      ) : (
        <button
          type="button"
          aria-label={label}
          disabled={pending}
          onClick={() => {
            setDraft(value);
            setStart(value);
            setEditing(true);
          }}
          className={cn(
            "hover:bg-accent/60 focus-visible:ring-ring/50 -mx-1.5 min-w-0 cursor-text rounded-lg px-1.5 text-start [overflow-wrap:anywhere] outline-none focus-visible:ring-2 disabled:opacity-60",
            className,
          )}
        >
          {children ?? value}
        </button>
      )}
      {error ? (
        <span className="text-danger-foreground text-sm" role="alert">
          {error}
        </span>
      ) : null}
    </span>
  );
}
