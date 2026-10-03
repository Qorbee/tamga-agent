"use client";

import { ArrowDown01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useQuery } from "@tanstack/react-query";

import { Hint } from "@/components/hint";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { getAssistant } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";
import { useAsks } from "@/lib/use-role";

import { ASSISTANT_KEY, chosen, modelName, saveChoice, useRunChoice } from "./run-choice";

/** How long what the server offers is taken as current. */
const OFFER_FRESH = 5 * 60_000;

/**
 * Which model answers the next question, and how long it reasons first: beside Send, for the
 * roles that may ask. A small model at low effort answers an easy question in a fraction of the
 * time and the cost; the choice is the viewer's, kept in their browser, and holds until they
 * change it.
 */
export function ModelPicker() {
  const { t } = useI18n();
  const asks = useAsks();
  // Read again now and then: what is offered changes with the server's settings.
  const offered = useQuery({ queryKey: ASSISTANT_KEY, queryFn: getAssistant, staleTime: OFFER_FRESH }).data;
  const choice = useRunChoice();
  if (!offered || !asks) return null;
  const { model, effort } = chosen(offered, choice);
  const words = t.assistant.choice;

  return (
    <DropdownMenu modal={false}>
      <Hint label={words.label}>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            data-slot="model-picker"
            aria-label={`${words.label}: ${modelName(model)}, ${words.efforts[effort]}`}
            className="text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-ring flex h-7 min-w-0 items-center gap-1 rounded-full ps-2.5 pe-1.5 text-xs outline-none focus-visible:ring-2"
          >
            <span className="max-w-24 truncate sm:max-w-40">{modelName(model)}</span>
            <span className="hidden sm:inline">· {words.efforts[effort]}</span>
            <HugeiconsIcon icon={ArrowDown01Icon} strokeWidth={1.75} className="size-3.5 shrink-0" aria-hidden />
          </button>
        </DropdownMenuTrigger>
      </Hint>
      <DropdownMenuContent align="end" side="top" className="w-64">
        {offered.models.length > 1 ? (
          <>
            <DropdownMenuLabel>{words.model}</DropdownMenuLabel>
            <DropdownMenuRadioGroup value={model} onValueChange={(value) => saveChoice({ ...choice, model: value })}>
              {offered.models.map((name) => (
                <DropdownMenuRadioItem key={name} value={name}>
                  {modelName(name)}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
            <DropdownMenuSeparator />
          </>
        ) : null}
        <DropdownMenuLabel>{words.effort}</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={effort} onValueChange={(value) => saveChoice({ ...choice, effort: value })}>
          {offered.efforts.map((level) => (
            <DropdownMenuRadioItem key={level} value={level}>
              {words.efforts[level]}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
        <p className="text-muted-foreground px-3 pt-1 pb-2 text-xs text-pretty">{words.what}</p>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
