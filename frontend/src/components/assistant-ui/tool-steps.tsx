"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { CanvasLink } from "@/components/canvas-link";
import { parseView, type CanvasView } from "@/lib/canvas-view";
import { listProjects } from "@/lib/api";
import type { Dictionary } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/client";

/**
 * A tool step as the owner reads it, written by code from the call and its result: no model is
 * asked to describe what another model did. The results are the text the chat's tools give the
 * model (`assistant/looking.py`): fenced as outside data, a search's hits under each source's
 * heading, each line led by the handle the run named it by. The handles mean nothing to the owner
 * and are left out. A project opened by its number is a link that opens it in the canvas; a
 * search is its one line.
 */

type Source = keyof Dictionary["chat"]["steps"]["sources"];

const FENCE = /^BEGIN OUTSIDE DATA (\w+)\n[^\n]*\n([\s\S]*)\nEND OUTSIDE DATA \1\s*$/;
// A handle the run gave a record at a line's start: "i2: " for most, "[c3] " for a conversation,
// and the "- " a memory entry is listed with.
const HANDLE = /^(?:[cfipstl]\d+:|\[[a-z]\d+\]|-)\s*/;
// Handles anywhere in an opened record: "[l1] f1 cmr.pdf", "Its files: f1 a.pdf; f2 b.pdf", "on t1".
const HANDLES = /(?:\s+on\s+t\d+\b|(?<![\w-])(?:\[[a-z]\d+\]|[cfipstl]\d+:?)(?=\s|$)\s?)/g;
const HANDLE_KINDS: Record<string, keyof Dictionary["chat"]["steps"]["records"]> = {
  c: "conversation",
  i: "invoice",
  f: "file",
  p: "firm",
  s: "chat",
};

/** What a result says, without its fence. */
function unfenced(result: unknown): string | null {
  if (typeof result !== "string") return null;
  return FENCE.exec(result)?.[2] ?? result;
}

function argsOf(argsText: string | undefined): Record<string, unknown> {
  try {
    const parsed: unknown = JSON.parse(argsText ?? "{}");
    return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

/** The step in one sentence, from what it was asked; none where the tool's own name says it. */
export function useStepLabel(): (toolName: string, argsText: string | undefined) => string | null {
  const { t } = useI18n();
  const steps = t.chat.steps;
  return (toolName, argsText) => {
    const args = argsOf(argsText);
    if (toolName === "search") {
      const query = typeof args.query === "string" ? args.query.trim() : "";
      const sources = Array.isArray(args.sources)
        ? args.sources.filter((source): source is Source => typeof source === "string" && source in steps.sources)
        : [];
      const where = sources.map((source) => steps.sources[source]).join(", ");
      if (!query) return where ? steps.latestIn(where) : steps.latest;
      return where ? steps.searchedIn(query, where) : steps.searched(query);
    }
    if (toolName === "open" && typeof args.handle === "string") {
      const handle = args.handle.trim();
      const kind = HANDLE_KINDS[handle.charAt(0).toLowerCase()];
      return kind && /^[a-z]\d+$/i.test(handle) ? steps.opened(steps.records[kind]) : steps.openedProject(handle);
    }
    return null;
  };
}

/** The project number a step opened, where it opened one by its number (not by a handle). */
export function openedNumber(toolName: string, argsText: string | undefined): string | null {
  if (toolName !== "open") return null;
  const handle = argsOf(argsText).handle;
  if (typeof handle !== "string" || !handle.trim()) return null;
  return HANDLE_KINDS[handle.trim().charAt(0).toLowerCase()] && /^[a-z]\d+$/i.test(handle.trim()) ? null : handle.trim();
}

// Where the app shows a record `open` opened, on its fence's label: "record c3 · view mail:<id>".
const VIEW_LABEL = /^BEGIN OUTSIDE DATA \w+\nrecord [^\n]* · view (\w+):([\w-]+)\n/;

/** Where the app shows what a step opened, when it told. */
export function openedView(result: unknown): CanvasView | null {
  if (typeof result !== "string") return null;
  const found = VIEW_LABEL.exec(result);
  return found ? parseView(`${found[1]}:${found[2]}`) : null;
}

/** Whether a step is said in its one line, with nothing under it: what was found or opened is
 * Tamga's to say in its answer, and a record opened is a link, never its text. */
export function oneLine(toolName: string): boolean {
  return toolName === "search" || toolName === "open" || toolName === "waiting";
}

/** Whether a step's lookup failed in part: a source that could not be searched, a record with no
 * such number (the words `assistant/looking.py` answers with). Never shown as a lookup done. */
export function fellShort(result: unknown): boolean {
  const text = unfenced(result) ?? "";
  return (
    text.includes("could not be searched just now") ||
    text.startsWith("No such record") ||
    /^That [\w ]+ is gone\.$/.test(text.trim())
  );
}

const bare = (number: string) => number.replace(/\s+/g, "").toUpperCase();

/** "Opened project P26-008", a link that opens the project in the canvas once its number finds
 * it; plain words where none does (merged away, or another company's sandbox). */
export function OpenedProject({ number, className }: { number: string; className?: string }) {
  const { t } = useI18n();
  const found = useQuery({
    queryKey: ["projects", { q: number, limit: 5 }],
    queryFn: () => listProjects({ q: number, limit: 5 }),
    staleTime: 60_000,
  });
  const project = found.data?.items.find((each) =>
    [each.number, ...each.aliases].some((known) => bare(known) === bare(number)),
  );
  const label = t.chat.steps.openedProject(number);
  if (!project) return <span className={className}>{label}</span>;
  return (
    <CanvasLink view={{ kind: "project", id: project.id }} className={className}>
      <span className="underline decoration-dotted underline-offset-4">{label}</span>
    </CanvasLink>
  );
}

/** What the step found, readable. */
export function StepResult({ toolName, result }: { toolName: string; result?: unknown }) {
  const text = unfenced(result);
  if (text === null) return null;
  return toolName === "open" || toolName === "waiting" ? (
    <RecordLines text={text} />
  ) : (
    <p className="text-muted-foreground text-sm text-pretty whitespace-pre-line">{text}</p>
  );
}

const FIRST_LINES = 6;

/** A record as `open` gave it: its first lines, and the rest a tap away. */
function RecordLines({ text }: { text: string }) {
  const { t } = useI18n();
  const [all, setAll] = useState(false);
  const lines = text.split("\n").map((line) => line.replace(HANDLE, "").replace(HANDLES, "").trimEnd());
  const shown = all ? lines : lines.slice(0, FIRST_LINES);
  return (
    <div className="grid gap-1">
      <p className="text-sm text-pretty whitespace-pre-line">{shown.join("\n")}</p>
      {lines.length > FIRST_LINES ? (
        <button type="button" onClick={() => setAll((open) => !open)} className="text-muted-foreground hover:text-foreground w-fit text-xs">
          {all ? t.chat.steps.showLess : t.chat.steps.showAll(lines.length)}
        </button>
      ) : null}
    </div>
  );
}
