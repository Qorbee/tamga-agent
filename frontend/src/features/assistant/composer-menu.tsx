"use client";

import { ComposerPrimitive } from "@assistant-ui/react";
import { Attachment01Icon, PlusSignIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useQuery } from "@tanstack/react-query";

import { Hint } from "@/components/hint";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
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
 * The composer's menu, behind its "+": add a file, and for the roles that may ask, which model
 * answers the next question and how long it reasons first. Kept out of the row beside Send, which
 * holds only what needs a glance. The choice is the viewer's, kept in their browser, and holds
 * until they change it.
 */
export function ComposerMenu() {
  const { t } = useI18n();

  return (
    <DropdownMenu modal={false}>
      <Hint label={t.assistant.choice.menu}>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            data-slot="composer-menu"
            aria-label={t.assistant.choice.menu}
            className="text-muted-foreground hover:text-foreground hover:bg-muted-foreground/15 focus-visible:ring-ring flex size-7 items-center justify-center rounded-full outline-none focus-visible:ring-2 active:scale-[0.96] motion-reduce:transition-none"
          >
            <HugeiconsIcon icon={PlusSignIcon} strokeWidth={1.75} className="size-4" aria-hidden />
          </button>
        </DropdownMenuTrigger>
      </Hint>
      <DropdownMenuContent align="start" side="top" className="w-64">
        <ComposerPrimitive.AddAttachment asChild>
          <DropdownMenuItem>
            <HugeiconsIcon icon={Attachment01Icon} strokeWidth={1.75} className="size-4" aria-hidden />
            {t.chat.addAttachment}
          </DropdownMenuItem>
        </ComposerPrimitive.AddAttachment>
        <RunChoice />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function RunChoice() {
  const { t } = useI18n();
  const asks = useAsks();
  // Read again now and then: what is offered changes with the server's settings.
  const offered = useQuery({ queryKey: ASSISTANT_KEY, queryFn: getAssistant, staleTime: OFFER_FRESH }).data;
  const choice = useRunChoice();
  if (!offered || !asks) return null;
  const { model, effort } = chosen(offered, choice);
  const words = t.assistant.choice;

  return (
    <>
      <DropdownMenuSeparator />
      {offered.models.length > 1 ? (
        <>
          <DropdownMenuLabel>{words.model}</DropdownMenuLabel>
          <DropdownMenuRadioGroup value={model} onValueChange={(value) => saveChoice({ ...choice, model: value })}>
            {offered.models.map((name) => (
              <DropdownMenuRadioItem key={name} value={name} onSelect={(event) => event.preventDefault()}>
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
          <DropdownMenuRadioItem key={level} value={level} onSelect={(event) => event.preventDefault()}>
            {words.efforts[level]}
          </DropdownMenuRadioItem>
        ))}
      </DropdownMenuRadioGroup>
      <p className="text-muted-foreground px-3 pt-1 pb-2 text-xs text-pretty">{words.what}</p>
    </>
  );
}
