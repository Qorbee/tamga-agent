"use client";

import { ArrowDown01Icon, Tick02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useQuery } from "@tanstack/react-query";

import { Hint } from "@/components/hint";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { getAssistant } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";
import { useAsks } from "@/lib/use-role";
import { cn } from "@/lib/utils";

import { ASSISTANT_KEY, chosen, modelName, saveChoice, useRunChoice } from "./run-choice";

/** How long what the server offers is taken as current. */
const OFFER_FRESH = 5 * 60_000;

/**
 * Which model answers the next question, and how long it reasons first: a chip beside Send, for
 * the roles that may ask, with the model's short name and the effort as rising bars. Its panel
 * lists the models offered and sets the effort in one row of steps. The choice is the viewer's,
 * kept in their browser, and holds until they change it.
 */
export function RunChoiceChip() {
  const { t } = useI18n();
  const asks = useAsks();
  // Read again now and then: what is offered changes with the server's settings.
  const offered = useQuery({ queryKey: ASSISTANT_KEY, queryFn: getAssistant, staleTime: OFFER_FRESH }).data;
  const choice = useRunChoice();
  if (!offered || !asks) return null;
  const { model, effort } = chosen(offered, choice);
  const words = t.assistant.choice;
  const level = offered.efforts.indexOf(effort) + 1;

  return (
    <Popover>
      <Hint label={words.label}>
        <PopoverTrigger asChild>
          <button
            type="button"
            data-slot="run-choice"
            aria-label={`${words.label}: ${modelName(model)}, ${words.efforts[effort]}`}
            className="text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-ring data-[state=open]:bg-accent data-[state=open]:text-foreground flex h-7 min-w-0 items-center gap-1.5 rounded-full ps-2.5 pe-1.5 text-xs outline-none focus-visible:ring-2"
          >
            <span className="max-w-20 truncate font-medium sm:max-w-32">{shortName(model)}</span>
            <Bars of={offered.efforts.length} lit={level} />
            <HugeiconsIcon icon={ArrowDown01Icon} strokeWidth={1.75} className="size-3.5 shrink-0" aria-hidden />
          </button>
        </PopoverTrigger>
      </Hint>
      <PopoverContent side="top" align="end" className="w-72 p-2">
        {offered.models.length > 1 ? (
          <div role="radiogroup" aria-label={words.model} className="pb-2">
            <p className="text-muted-foreground px-2 pt-1 pb-1.5 text-xs">{words.model}</p>
            {offered.models.map((name) => (
              <button
                key={name}
                type="button"
                role="radio"
                aria-checked={name === model}
                onClick={() => saveChoice({ ...choice, model: name })}
                className="hover:bg-accent focus-visible:ring-ring flex w-full items-center justify-between rounded-lg px-2 py-1.5 text-start text-sm outline-none focus-visible:ring-2"
              >
                <span className="truncate">{modelName(name)}</span>
                {name === model ? (
                  <HugeiconsIcon icon={Tick02Icon} strokeWidth={2} className="size-4 shrink-0" aria-hidden />
                ) : null}
              </button>
            ))}
          </div>
        ) : null}
        <p className="text-muted-foreground px-2 pb-1.5 text-xs">{words.effort}</p>
        <ToggleGroup
          type="single"
          value={effort}
          onValueChange={(value) => value && saveChoice({ ...choice, effort: value })}
          spacing={0}
          variant="outline"
          aria-label={words.effort}
          className="w-full"
        >
          {offered.efforts.map((step, index) => (
            <ToggleGroupItem
              key={step}
              value={step}
              aria-label={words.efforts[step]}
              className="h-auto flex-1 flex-col gap-1 py-2 text-xs"
            >
              <Bars of={offered.efforts.length} lit={index + 1} />
              {words.efforts[step]}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <p className="text-muted-foreground px-2 pt-2 pb-1 text-xs text-pretty">{words.what}</p>
      </PopoverContent>
    </Popover>
  );
}

/** A model's last word, as people call it ("gpt-6.1-sol" is Sol); its whole name when it has none. */
function shortName(model: string): string {
  const name = modelName(model);
  const last = name.slice(name.lastIndexOf("-") + 1);
  return /^[a-z]+$/i.test(last) ? last.charAt(0).toUpperCase() + last.slice(1) : name;
}

/** Rising bars, ``lit`` of ``of`` filled: how long a model reasons, at a glance. */
function Bars({ of, lit }: { of: number; lit: number }) {
  return (
    <span className="flex h-3 items-end gap-0.5" aria-hidden>
      {Array.from({ length: of }, (_, index) => (
        <span
          key={index}
          className={cn("w-[3px] rounded-full", index < lit ? "bg-current" : "bg-current opacity-25")}
          style={{ height: `${((index + 1) / of) * 100}%` }}
        />
      ))}
    </span>
  );
}
