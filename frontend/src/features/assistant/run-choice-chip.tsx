"use client";

import { ArrowDown01Icon, Tick02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Hint } from "@/components/hint";
import { Segmented } from "@/components/segmented";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Sheet, SheetContent, SheetGrip, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { useIsMobile } from "@/hooks/use-mobile";
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
  const mobile = useIsMobile();
  const [sheetOpen, setSheetOpen] = useState(false);
  const closeSheet = () => setSheetOpen(false);
  if (!offered || !asks) return null;
  const { model, effort } = chosen(offered, choice);
  const words = t.assistant.choice;
  const level = offered.efforts.indexOf(effort) + 1;

  // Its choices, in a panel over the composer on a desk, in a sheet from the bottom on a phone.
  const panel = (
    <>
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
              className="hover:bg-accent focus-visible:ring-ring flex w-full items-center justify-between rounded-lg px-2 py-1.5 text-start text-sm outline-none focus-visible:ring-2 max-md:py-3 max-md:text-base"
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
      <Segmented
        fill
        label={words.effort}
        value={effort}
        onChange={(value) => saveChoice({ ...choice, effort: value })}
        options={offered.efforts.map((step, index) => ({
          value: step,
          className: "flex-col gap-1 py-2 text-xs",
          label: (
            <>
              <Bars of={offered.efforts.length} lit={index + 1} />
              {words.efforts[step]}
            </>
          ),
        }))}
      />
      <p className="text-muted-foreground px-2 pt-2 pb-1 text-xs text-pretty">{words.what}</p>
    </>
  );
  const chip = (
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
  );

  // A phone's own pickers come up from the bottom, clear of the keyboard and the composer.
  if (mobile) {
    return (
      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetTrigger asChild>{chip}</SheetTrigger>
        <SheetContent side="bottom" className="max-h-[85dvh] gap-0 overflow-y-auto rounded-t-3xl px-3 pt-2 pb-[max(1rem,env(safe-area-inset-bottom))]">
          <SheetGrip onDismiss={closeSheet}>
            <SheetHeader className="px-2 pt-1 pb-2">
              <SheetTitle className="text-base">{words.label}</SheetTitle>
            </SheetHeader>
          </SheetGrip>
          {panel}
        </SheetContent>
      </Sheet>
    );
  }
  return (
    <Popover>
      <Hint label={words.label}>
        <PopoverTrigger asChild>{chip}</PopoverTrigger>
      </Hint>
      <PopoverContent side="top" align="end" collisionPadding={16} className="w-72 p-2">
        {panel}
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
