"use client";

import {
  Brain02Icon,
  BubbleChatIcon,
  File01Icon,
  Folder01Icon,
  Invoice01Icon,
  Mail01Icon,
  UserGroupIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";
import { useState } from "react";

import type { Dictionary } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/client";

/**
 * A tool step as the owner reads it, written by code from the call and its result: no model is
 * asked to describe what another model did. The results are the text the chat's tools give the
 * model (`assistant/looking.py`): fenced as outside data, a search's hits under each source's
 * heading, each line led by the handle the run named it by. The handles mean nothing to the owner
 * and are left out; the raw call and result stay one tap away, under "Show details".
 */

type Source = keyof Dictionary["chat"]["steps"]["sources"];

// The headings `looking.search` gives each source, in its own words, and how each is shown.
const SOURCES: Record<string, { source: Source; icon: IconSvgElement }> = {
  "Mail conversations": { source: "mail", icon: Mail01Icon },
  Projects: { source: "projects", icon: Folder01Icon },
  "Invoices in the accounting records": { source: "invoices", icon: Invoice01Icon },
  Files: { source: "files", icon: File01Icon },
  "Firms and people": { source: "firms", icon: UserGroupIcon },
  "The company's memory": { source: "memory", icon: Brain02Icon },
  "Past chats": { source: "chats", icon: BubbleChatIcon },
};

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

/** What the step found, readable; its raw call and result under "Show details". */
export function StepResult({ toolName, argsText, result }: { toolName: string; argsText?: string; result?: unknown }) {
  const { t } = useI18n();
  const [details, setDetails] = useState(false);
  const text = unfenced(result);
  return (
    <div className="grid gap-2">
      {text === null ? null : toolName === "search" ? (
        <SearchHits text={text} />
      ) : toolName === "open" || toolName === "waiting" ? (
        <RecordLines text={text} />
      ) : (
        <p className="text-muted-foreground text-sm text-pretty whitespace-pre-line">{text}</p>
      )}
      <button
        type="button"
        onClick={() => setDetails((open) => !open)}
        aria-expanded={details}
        className="text-muted-foreground hover:text-foreground w-fit text-xs"
      >
        {details ? t.chat.steps.hideDetails : t.chat.steps.showDetails}
      </button>
      {details ? (
        <div className="grid gap-2">
          {argsText ? <Raw>{argsText}</Raw> : null}
          {result !== undefined ? <Raw>{typeof result === "string" ? result : JSON.stringify(result, null, 2)}</Raw> : null}
        </div>
      ) : null}
    </div>
  );
}

function Raw({ children }: { children: string }) {
  return <pre className="bg-muted/50 text-foreground/90 overflow-x-auto rounded-md p-2.5 text-xs whitespace-pre-wrap">{children}</pre>;
}

/** A search's hits, under each source it looked in, with the handles left out. */
function SearchHits({ text }: { text: string }) {
  const { t } = useI18n();
  const sections = text
    .split(/\n{2,}/)
    .map((block) => {
      const [heading = "", ...lines] = block.split("\n");
      return { heading: heading.replace(/:$/, ""), lines: lines.filter((line) => line.trim() !== "") };
    })
    .filter((section) => section.heading);
  return (
    <ul className="grid gap-2.5">
      {sections.map((section) => {
        const known = SOURCES[section.heading];
        // A source that failed is not one that found nothing: the owner is told which.
        const failed = section.lines.length === 1 && section.lines[0] === "could not be searched just now";
        const empty = section.lines.length === 0 || failed || (section.lines.length === 1 && section.lines[0] === "nothing matches");
        return (
          <li key={section.heading} className="grid gap-1">
            <span className="text-muted-foreground flex items-center gap-1.5 text-xs font-medium">
              {known ? <HugeiconsIcon icon={known.icon} strokeWidth={1.75} className="size-3.5" aria-hidden /> : null}
              <span className="inline-block first-letter:uppercase">{known ? t.chat.steps.sources[known.source] : section.heading}</span>
              {empty ? <span className="font-normal">· {failed ? t.chat.steps.unavailable : t.chat.steps.nothing}</span> : null}
            </span>
            {empty ? null : (
              <ul className="border-border grid gap-0.5 border-s ps-3">
                {section.lines.map((line, index) => (
                  <li key={index} className="truncate text-sm" title={line.replace(HANDLE, "")}>
                    {line.replace(HANDLE, "")}
                  </li>
                ))}
              </ul>
            )}
          </li>
        );
      })}
    </ul>
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
