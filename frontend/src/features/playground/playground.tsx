"use client";

import { Cancel01Icon, Forward02Icon, Globe02Icon, PlusSignIcon, SentIcon, Attachment01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, use, useState, type ReactNode } from "react";

import { IconButton } from "@/components/icon-button";
import { ScenarioPicker } from "@/components/scenario-picker";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getMe, getPolicy, getWorld, listMessages, sayAs, skipDays, type Persona } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

/**
 * The playground around a judge's sandbox (ADR-0004): a bar with the scenario, the sandbox's
 * clock and its skips, and the World panel, where the scenario's people write on simulated
 * WhatsApp and the judge can type as them. A pilot's app shows neither.
 */

type WorldState = { open: boolean; setOpen: (open: boolean) => void };
const WorldOpen = createContext<WorldState>({ open: false, setOpen: () => undefined });

export function PlaygroundProvider({ children }: { children: ReactNode }) {
  // Open beside the page where there is room; on a phone it would cover the app, so it waits.
  const [open, setOpen] = useState(() => typeof window !== "undefined" && window.matchMedia("(min-width: 64rem)").matches);
  return <WorldOpen value={{ open, setOpen }}>{children}</WorldOpen>;
}

function useSandbox(): boolean {
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  return me.data?.company.kind === "sandbox";
}

function useWorld(enabled: boolean) {
  // Read again each minute: the sandbox's clock runs on between skips.
  return useQuery({ queryKey: ["world"], queryFn: getWorld, enabled, refetchInterval: 60_000 });
}

/** Days from the company's day to its next pay day (Monday 0), a week when it is today. */
function daysTo(weekday: number, today: string): number {
  const [year = 1970, month = 1, day = 1] = today.split("-").map(Number);
  const now = (new Date(year, month - 1, day).getDay() + 6) % 7;
  return (weekday - now + 7) % 7 || 7;
}

export function PlaygroundBar() {
  const { t, locale } = useI18n();
  const sandbox = useSandbox();
  const world = useWorld(sandbox);
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false, enabled: sandbox });
  const policy = useQuery({ queryKey: ["policy"], queryFn: getPolicy, enabled: sandbox });
  const { open, setOpen } = use(WorldOpen);
  const [picking, setPicking] = useState(false);
  const queryClient = useQueryClient();
  const skip = useMutation({
    mutationFn: skipDays,
    onSuccess: (updated) => {
      queryClient.setQueryData(["world"], updated);
      // Every page counts from the company's day, so all of it is read again.
      void queryClient.invalidateQueries();
    },
  });
  if (!sandbox) return null;

  const payday = policy.data?.payment_weekday;
  const toPayday = payday != null && me.data ? daysTo(payday, me.data.today) : null;
  const weekdays = t.settings.howYouPay.weekdays;

  return (
    <div
      data-slot="playground-bar"
      className="border-border bg-background flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 border-b px-4 py-2 backdrop-blur"
    >
      <span className="bg-primary/10 text-primary rounded-full px-2.5 py-0.5 text-xs font-medium">{t.playground.badge}</span>
      <span className="min-w-0 truncate text-sm font-medium max-sm:hidden">{world.data?.scenario?.title}</span>
      {world.data ? (
        <span className="text-muted-foreground font-mono text-xs tabular-nums" aria-label={t.playground.clock}>
          {formatDate(new Date(world.data.now), locale, "dateTime")}
        </span>
      ) : null}
      <span className="ms-auto flex items-center gap-1.5">
        <Button size="sm" variant="outline" disabled={skip.isPending || !world.data?.ready} onClick={() => skip.mutate(1)}>
          <HugeiconsIcon icon={Forward02Icon} strokeWidth={1.75} />
          {skip.isPending && skip.variables === 1 ? t.playground.skipping : t.playground.skipDay}
        </Button>
        {payday != null && toPayday != null && toPayday > 1 ? (
          <Button
            size="sm"
            variant="outline"
            className="max-sm:hidden"
            disabled={skip.isPending || !world.data?.ready}
            onClick={() => skip.mutate(toPayday)}
          >
            {t.playground.skipTo(weekdays[payday] ?? "")}
          </Button>
        ) : null}
        <IconButton
          icon={Globe02Icon}
          label={t.playground.world}
          variant={open ? "secondary" : "ghost"}
          aria-pressed={open}
          onClick={() => setOpen(!open)}
        />
        <IconButton icon={PlusSignIcon} label={t.playground.newSandbox} onClick={() => setPicking(true)} />
      </span>
      <ScenarioPicker open={picking} onOpenChange={setPicking} />
    </div>
  );
}

/**
 * The world beside the page: each persona's thread as a phone shows it, oldest first, and a box
 * to type as them. What is typed arrives as their message, through the same intake as theirs.
 */
export function WorldPanel() {
  const { t } = useI18n();
  const sandbox = useSandbox();
  const world = useWorld(sandbox);
  const { open, setOpen } = use(WorldOpen);
  const personas = world.data?.personas ?? [];
  const [chosen, setChosen] = useState<string | null>(null);
  if (!sandbox || !open || personas.length === 0) return null;
  const persona = personas.find((item) => item.key === chosen) ?? personas[0];
  if (!persona) return null;

  return (
    <aside
      data-slot="world-panel"
      aria-label={t.playground.world}
      className={cn(
        "bg-card flex min-h-0 flex-col",
        "max-lg:fixed max-lg:inset-0 max-lg:z-40",
        "lg:border-border lg:m-2 lg:ms-0 lg:w-80 lg:shrink-0 lg:rounded-2xl lg:border",
      )}
    >
      <header className="border-border flex items-start gap-2 border-b px-4 py-3">
        <span className="min-w-0 flex-1">
          <span className="text-muted-foreground block text-xs font-medium tracking-[0.08em] uppercase">
            {t.playground.world}
          </span>
          <span className="text-muted-foreground block text-xs">{t.playground.worldHint}</span>
        </span>
        <IconButton icon={Cancel01Icon} label={t.playground.closeWorld} onClick={() => setOpen(false)} />
      </header>
      {personas.length > 1 ? (
        <div className="flex gap-1 overflow-x-auto px-3 pt-2">
          {personas.map((item) => (
            <Button
              key={item.key}
              size="sm"
              variant={item.key === persona.key ? "secondary" : "ghost"}
              aria-pressed={item.key === persona.key}
              onClick={() => setChosen(item.key)}
            >
              {item.name}
            </Button>
          ))}
        </div>
      ) : null}
      <PersonaThread key={persona.key} persona={persona} />
    </aside>
  );
}

function PersonaThread({ persona }: { persona: Persona }) {
  const { t, locale } = useI18n();
  const messages = useQuery({
    queryKey: ["world-messages", persona.thread_key],
    queryFn: () => listMessages({ channel: "simulated", thread_key: persona.thread_key, limit: 100 }),
  });
  const [text, setText] = useState("");
  const queryClient = useQueryClient();
  const send = useMutation({
    mutationFn: sayAs,
    onSuccess: () => {
      setText("");
      void queryClient.invalidateQueries({ queryKey: ["world-messages", persona.thread_key] });
    },
  });
  const said = [...(messages.data?.items ?? [])].reverse();

  return (
    <>
      <div className="flex items-center gap-3 px-4 py-3">
        <span className="bg-muted text-muted-foreground flex size-9 shrink-0 items-center justify-center rounded-full text-sm font-medium">
          {persona.name.slice(0, 1)}
        </span>
        <span className="min-w-0">
          <span className="block truncate text-sm font-medium">{persona.name}</span>
          <span className="text-muted-foreground block truncate text-xs">{persona.role}</span>
        </span>
      </div>
      <ol className="bg-muted/40 flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto px-3 py-3" aria-live="polite">
        {said.length === 0 && !messages.isPending ? (
          <li className="text-muted-foreground m-auto max-w-[24ch] text-center text-xs text-pretty">{t.playground.noMessages}</li>
        ) : null}
        {said.map((message) => (
          <li key={message.id} className="bg-card border-border max-w-[85%] self-start rounded-2xl rounded-ss-sm border px-3 py-2 shadow-xs">
            <p className="text-sm text-pretty whitespace-pre-line">{message.snippet}</p>
            <p className="text-muted-foreground mt-1 flex items-center justify-end gap-1.5 text-[11px] tabular-nums">
              {message.attachment_count > 0 ? (
                <HugeiconsIcon icon={Attachment01Icon} className="size-3" aria-hidden />
              ) : null}
              {formatDate(new Date(message.sent_at), locale, "dateTime")}
            </p>
          </li>
        ))}
      </ol>
      <form
        className="border-border flex items-center gap-2 border-t p-3"
        onSubmit={(event) => {
          event.preventDefault();
          const typed = text.trim();
          if (typed) send.mutate({ persona: persona.key, text: typed });
        }}
      >
        <Input
          value={text}
          maxLength={200}
          onChange={(event) => setText(event.target.value)}
          placeholder={t.playground.typeAs(persona.name.split(" ")[0] ?? persona.name)}
          aria-describedby="world-typed"
          className="rounded-full"
        />
        <IconButton icon={SentIcon} label={t.playground.send} type="submit" disabled={!text.trim() || send.isPending} />
        <span id="world-typed" className="sr-only">
          {t.playground.sent}
        </span>
      </form>
    </>
  );
}
