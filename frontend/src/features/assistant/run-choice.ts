import { useSyncExternalStore } from "react";

import type { Assistant } from "@/lib/api";

/**
 * The model and reasoning effort this viewer chose for their questions, of what the server
 * offers (`GET /assistant`). It is kept in this browser: a convenience of one viewer, which
 * the server neither stores nor trusts, and checks on every question.
 */
export type RunChoice = { model?: string; effort?: string };
export type Chosen = { model: string; effort: Assistant["effort"] };

export const ASSISTANT_KEY = ["assistant"] as const;

const KEY = "tamga_run_choice";
const NOTHING: RunChoice = {};
const listeners = new Set<() => void>();
// The last value read, by the text it was read from: a store's snapshot must stay the same
// object until what it holds changes.
let held: { text: string | null; choice: RunChoice } = { text: null, choice: NOTHING };

function parsed(text: string | null): RunChoice {
  try {
    const value: unknown = text ? JSON.parse(text) : null;
    if (typeof value !== "object" || value === null) return NOTHING;
    const { model, effort } = value as Record<string, unknown>;
    return {
      ...(typeof model === "string" ? { model } : {}),
      ...(typeof effort === "string" ? { effort } : {}),
    };
  } catch {
    return NOTHING;
  }
}

/** What is chosen now. Storage can be closed to the page (a private window): the choice then lasts as long as the page. */
export function readChoice(): RunChoice {
  let text = held.text;
  try {
    text = window.localStorage.getItem(KEY);
  } catch {
    // Kept in memory only.
  }
  if (text !== held.text) held = { text, choice: parsed(text) };
  return held.choice;
}

export function saveChoice(choice: RunChoice): void {
  const text = JSON.stringify(choice);
  held = { text, choice };
  try {
    window.localStorage.setItem(KEY, text);
  } catch {
    // Kept in memory only.
  }
  for (const listener of listeners) listener();
}

function subscribe(onChange: () => void) {
  listeners.add(onChange);
  // Another tab of the same browser chose.
  window.addEventListener("storage", onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("storage", onChange);
  };
}

export function useRunChoice(): RunChoice {
  return useSyncExternalStore(subscribe, readChoice, () => NOTHING);
}

/**
 * What a question is asked with: the viewer's choice where the server still offers it, else
 * what the assistant is configured with. The picker shows this and the question is sent with
 * it, so what is shown is what answers.
 */
export function chosen(offered: Assistant, choice: RunChoice): Chosen {
  return {
    model: offered.models.find((model) => model === choice.model) ?? offered.model,
    effort: offered.efforts.find((effort) => effort === choice.effort) ?? offered.effort,
  };
}

/** A model as the picker names it: its name without its provider. */
export function modelName(model: string): string {
  return model.slice(model.indexOf(":") + 1);
}
